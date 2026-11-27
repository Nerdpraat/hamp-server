import json
import time

import pytest
from starlette.websockets import WebSocketDisconnect

import protocol_schemas as ps
from app import ingest
from conftest import CABIN_TOKEN, HOME_TOKEN


class Conn:
    """A websocket test connection that validates every server message against the schemas."""

    def __init__(self, ws):
        self.ws = ws

    def send(self, msg):
        self.ws.send_json(msg)

    def send_text(self, text):
        self.ws.send_text(text)

    def recv(self):
        msg = self.ws.receive_json()
        ps.validate_server_message(msg)
        return msg

    def call(self, msg):
        self.send(msg)
        return self.recv()


def connect(client, token=HOME_TOKEN):
    return client.websocket_connect("/ws/ingest", headers={"Authorization": f"Bearer {token}"})


class opened:
    """`with opened(client) as c:` connects, consumes and checks the hello."""

    def __init__(self, client, token=HOME_TOKEN):
        self.cm = connect(client, token)

    def __enter__(self):
        c = Conn(self.cm.__enter__())
        c.hello = c.recv()
        assert c.hello["type"] == "hello"
        return c

    def __exit__(self, *exc):
        return self.cm.__exit__(*exc)


BATTERY = {"id": "battery-1", "device": "battery", "name": "Battery 1", "sensors": {
    "state_of_charge": {"source": "sensor.b1_soc"},
    "power": {"source": "sensor.b1_power"},
    "energy_charged": {"source": "sensor.b1_in"}}}
METER = {"id": "electricity-meter", "device": "electricitymeter", "name": "Electricity Meter", "sensors": {
    "energy_import": {"source": "sensor.p1_import"},
    "price_import": {"fixed": 0.25}}}
PLUG = {"id": "plug-car", "device": "consumer_device", "name": "Car charger", "sensors": {
    "energy": {"source": "sensor.car_energy"}}}


def config(*instances, **extra):
    return {"type": "config", "currency": "EUR", "instances": list(instances), **extra}


def db_rows(client, sql):
    async def go():
        cur = await client.app.state.db.conn.execute(sql)
        return [dict(r) for r in await cur.fetchall()]
    return client.portal.call(go)


def flush(client):
    client.portal.call(client.app.state.aggregator.flush)


# ---- handshake -----------------------------------------------------------------------------
def test_rejects_missing_or_wrong_token(client):
    for headers in ({}, {"Authorization": "Bearer nope"}, {"Authorization": "Basic abc"}):
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/ingest", headers=headers):
                pass


def test_hello_carries_version_limits_time_and_the_catalogue(client):
    before = time.time()
    with opened(client) as c:
        hello = c.hello
    assert hello["version"] == 2
    assert hello["site"] == {"id": "home", "name": "Home"}
    assert before - 1 <= hello["server_time"] <= time.time() + 1
    assert hello["limits"] == ingest.LIMITS
    devices = {d["id"]: d for d in hello["devices"]}
    assert set(devices) == {"heatpump", "solar_inverter", "battery", "gasmeter", "watermeter",
                            "electricitymeter", "consumer_device", "water_heater"}
    soc = next(s for s in devices["battery"]["sensors"] if s["id"] == "state_of_charge")
    assert (soc["kind"], soc["unit"], soc["allow_fixed"]) == ("gauge", "%", False)
    assert soc["selector"] == {"domain": ["sensor"], "device_class": ["battery"]}
    price = next(s for s in devices["electricitymeter"]["sensors"] if s["id"] == "price_import")
    assert price["allow_fixed"] is True
    assert "role" not in json.dumps(hello)                       # roles are server-internal


# ---- config -------------------------------------------------------------------------------------
def test_config_is_stored_and_acknowledged(client):
    with opened(client) as c:
        ack = c.call(config(BATTERY, METER))
    assert ack == {"type": "ack", "for": "config", "instances": 2, "sensors": 5,
                   "removed": {"instances": [], "sensors": []}}
    assert {r["instance"]: r["device"] for r in db_rows(client, "SELECT * FROM devices")} == {
        "battery-1": "battery", "electricity-meter": "electricitymeter"}
    ents = {r["key"]: r["source"] for r in db_rows(client, "SELECT * FROM entities")}
    assert ents == {"battery-1.state_of_charge": "sensor.b1_soc", "battery-1.power": "sensor.b1_power",
                    "battery-1.energy_charged": "sensor.b1_in",
                    "electricity-meter.energy_import": "sensor.p1_import"}
    fixed = db_rows(client, "SELECT instance, sensor_id, value FROM fixed_values")
    assert fixed == [{"instance": "electricity-meter", "sensor_id": "price_import", "value": 0.25}]
    sites = {s["id"]: s for s in client.app.state.db.__class__ and
             client.portal.call(client.app.state.db.list_sites)}
    assert sites["home"]["meta"] == {"currency": "EUR"}


def test_battery_capacity_accepts_a_fixed_number(client):
    battery = {**BATTERY, "sensors": {**BATTERY["sensors"], "capacity": {"fixed": 10}}}
    with opened(client) as c:
        ack = c.call(config(battery))
    assert ack["type"] == "ack" and ack["sensors"] == 4
    fixed = db_rows(client, "SELECT instance, sensor_id, value FROM fixed_values")
    assert fixed == [{"instance": "battery-1", "sensor_id": "capacity", "value": 10.0}]


@pytest.mark.parametrize("bad,path,fragment", [
    ({"type": "config"}, "instances", "must be a list"),
    ({"type": "config", "instances": "x"}, "instances", "must be a list"),
    ({"type": "config", "instances": [], "currency": "EUROEUROEURO"}, "currency", "at most 8"),
    ({"type": "config", "instances": [5]}, "instances[0]", "must be an object"),
    ({"type": "config", "instances": [{**BATTERY, "id": "Battery-1"}]}, "instances[0].id", "must match"),
    ({"type": "config", "instances": [{**BATTERY, "id": "x" * 41}]}, "instances[0].id", "must match"),
    ({"type": "config", "instances": [BATTERY, BATTERY]}, "instances[1].id", "duplicate instance id 'battery-1'"),
    ({"type": "config", "instances": [{**BATTERY, "device": "flux"}]}, "instances[0].device", "unknown device 'flux'"),
    ({"type": "config", "instances": [{**BATTERY, "name": ""}]}, "instances[0].name", "1 to 80"),
    ({"type": "config", "instances": [{**BATTERY, "name": "n" * 81}]}, "instances[0].name", "1 to 80"),
    ({"type": "config", "instances": [{**BATTERY, "sensors": {}}]}, "instances[0].sensors", "at least one"),
    ({"type": "config", "instances": [{**BATTERY, "sensors": {"flux_capacitor": {"source": "x"}}}]},
     "instances[0].sensors.flux_capacitor", "unknown sensor 'flux_capacitor' for device 'battery'"),
    ({"type": "config", "instances": [{**BATTERY, "sensors": {"power": "sensor.x"}}]},
     "instances[0].sensors.power", "must be an object"),
    ({"type": "config", "instances": [{**BATTERY, "sensors": {"power": {}}}]},
     "instances[0].sensors.power", "needs a 'source'"),
    ({"type": "config", "instances": [{**BATTERY, "sensors": {"power": {"source": "x", "fixed": 1}}}]},
     "instances[0].sensors.power", "not both"),
    ({"type": "config", "instances": [{**BATTERY, "sensors": {"power": {"source": ""}}}]},
     "instances[0].sensors.power.source", "1 to 255"),
    ({"type": "config", "instances": [{**BATTERY, "sensors": {"power": {"fixed": 1}}}]},
     "instances[0].sensors.power.fixed", "does not accept a fixed value"),
    ({"type": "config", "instances": [{**METER, "sensors": {"price_import": {"fixed": -1}}}]},
     "instances[0].sensors.price_import.fixed", "0 or more"),
    ({"type": "config", "instances": [{**METER, "sensors": {"price_import": {"fixed": "1"}}}]},
     "instances[0].sensors.price_import.fixed", "0 or more"),
    ({"type": "config", "instances": [{**METER, "sensors": {"price_import": {"fixed": True}}}]},
     "instances[0].sensors.price_import.fixed", "0 or more"),
    ({"type": "config", "instances": [{**BATTERY, "sensors": {"capacity": {"source": "sensor.x"}}}]},
     "instances[0].sensors.capacity.source", "takes only a fixed value"),
    ({"type": "config", "instances": [{**BATTERY, "sensors": {"capacity": {"fixed": 0}}}]},
     "instances[0].sensors.capacity.fixed", "greater than 0"),
    ({"type": "config", "instances": [{**BATTERY, "sensors": {"capacity": {"fixed": -5}}}]},
     "instances[0].sensors.capacity.fixed", "greater than 0"),
])
def test_invalid_config_is_rejected_with_a_path_and_the_connection_stays_open(client, bad, path, fragment):
    with opened(client) as c:
        err = c.call(bad)
        assert err["type"] == "error" and err["code"] == "invalid_config"
        assert err["path"] == path and fragment in err["message"], err
        assert c.call({"type": "ping"}) == {"type": "pong"}          # still open
    assert db_rows(client, "SELECT * FROM devices") == []            # nothing was stored


def test_too_many_instances_are_refused(client, monkeypatch):
    monkeypatch.setattr(ingest, "MAX_INSTANCES", 2)
    many = [{**PLUG, "id": f"plug-{i}"} for i in range(3)]
    with opened(client) as c:
        err = c.call(config(*many))
        assert err["code"] == "invalid_config" and err["path"] == "instances"


def test_a_rejected_config_leaves_the_previous_one_in_force(client):
    now = time.time()
    with opened(client) as c:
        c.call(config(BATTERY))
        assert c.call(config({**BATTERY, "device": "nope"}))["code"] == "invalid_config"
        ack = c.call({"type": "states", "items": [{"key": "battery-1.power", "ts": now, "value": 5}]})
        assert ack["accepted"] == 1


def test_many_instances_of_one_device_type(client):
    plugs = [{**PLUG, "id": f"plug-{i}", "name": f"Plug {i}"} for i in range(5)]
    with opened(client) as c:
        ack = c.call(config(*plugs, BATTERY, {**BATTERY, "id": "battery-2", "name": "Battery 2"}))
    assert ack["instances"] == 7
    assert len(db_rows(client, "SELECT * FROM devices")) == 7


def test_removing_instances_and_sensors_deletes_their_history(client):
    now = time.time()
    with opened(client) as c:
        c.call(config(BATTERY, {**BATTERY, "id": "battery-2", "name": "Battery 2"}, METER))
        c.call({"type": "states", "items": [
            {"key": "battery-1.power", "ts": now, "value": 1.0},
            {"key": "battery-2.power", "ts": now, "value": 2.0},
            {"key": "battery-2.energy_charged", "ts": now, "value": 3.0}]})
        flush(client)
        # battery-2 disappears; battery-1 loses a sensor and the fixed price goes too
        slim = {**BATTERY, "sensors": {"power": {"source": "sensor.b1_power"}}}
        meter = {**METER, "sensors": {"energy_import": {"source": "sensor.p1_import"}}}
        ack = c.call(config(slim, meter))
    assert ack["removed"] == {
        "instances": ["battery-2"],
        "sensors": ["battery-1.energy_charged", "battery-1.state_of_charge", "electricity-meter.price_import"]}
    assert {r["key"] for r in db_rows(client, "SELECT DISTINCT key FROM buckets")} == {"battery-1.power"}
    assert {r["instance"] for r in db_rows(client, "SELECT * FROM devices")} == {"battery-1", "electricity-meter"}
    assert db_rows(client, "SELECT * FROM fixed_values") == []


def test_switching_a_sensor_from_source_to_fixed_drops_its_data(client):
    now = time.time()
    with opened(client) as c:
        c.call(config(METER | {"sensors": {"energy_import": {"source": "sensor.x"},
                                           "price_import": {"source": "sensor.price"}}}))
        c.call({"type": "states", "items": [{"key": "electricity-meter.price_import", "ts": now, "value": 0.3}]})
        flush(client)
        ack = c.call(config(METER | {"sensors": {"energy_import": {"source": "sensor.x"},
                                                 "price_import": {"fixed": 0.4}}}))
    assert ack["removed"] == {"instances": [], "sensors": []}        # still there, just a different kind
    assert db_rows(client, "SELECT key FROM entities") == [{"key": "electricity-meter.energy_import"}]
    assert db_rows(client, "SELECT * FROM buckets WHERE key='electricity-meter.price_import'") == []
    assert db_rows(client, "SELECT value FROM fixed_values") == [{"value": 0.4}]


def test_renaming_an_instance_keeps_its_history(client):
    now = time.time()
    with opened(client) as c:
        c.call(config(BATTERY))
        c.call({"type": "states", "items": [{"key": "battery-1.power", "ts": now, "value": 1.0}]})
        flush(client)
        c.call(config({**BATTERY, "name": "Garage battery"}))
    assert db_rows(client, "SELECT name FROM devices") == [{"name": "Garage battery"}]
    assert len(db_rows(client, "SELECT * FROM buckets")) == 1


# ---- states -------------------------------------------------------------------------------------
def test_states_are_aggregated_into_minute_buckets(client):
    now = time.time()
    with opened(client) as c:
        c.call(config(BATTERY, METER))
        ack = c.call({"type": "states", "items": [
            {"key": "electricity-meter.energy_import", "ts": now - 20, "value": 100.0},
            {"key": "electricity-meter.energy_import", "ts": now - 10, "value": 100.5},
            {"key": "battery-1.power", "ts": now - 10, "value": -1234.5},
            {"key": "battery-1.power", "ts": now, "value": -1000}]})
    assert ack == {"type": "ack", "for": "states", "accepted": 4, "rejected": 0}
    flush(client)
    by_key = {}
    for r in db_rows(client, "SELECT * FROM buckets"):
        by_key.setdefault(r["key"], []).append(r)
    assert sum(r["delta"] for r in by_key["electricity-meter.energy_import"]) == pytest.approx(0.5)
    assert min(r["min"] for r in by_key["battery-1.power"]) == -1234.5


def test_bad_values_unknown_keys_and_bad_timestamps_are_rejected_not_fatal(client):
    now = time.time()
    with opened(client) as c:
        c.call(config(BATTERY))
        ack = c.call({"type": "states", "items": [
            {"key": "battery-1.power", "ts": now, "value": 10},               # ok
            {"key": "battery-1.power", "ts": now, "value": "10"},              # string
            {"key": "battery-1.power", "ts": now, "value": True},              # bool
            {"key": "battery-1.power", "ts": now, "value": None},
            {"key": "battery-1.power", "ts": "yesterday", "value": 1},         # ts not a number
            {"key": "battery-1.power", "ts": now - 7200, "value": 1},          # too old
            {"key": "battery-1.power", "ts": now + 3600, "value": 1},          # in the future
            {"key": "battery-1.power"},                                        # no value
            {"key": "nope.power", "ts": now, "value": 1},                      # unknown key
            {"key": "battery-1.nothing", "ts": now, "value": 1},               # unknown sensor
            {"key": 5, "value": 1}, "not an object", {}]})
    assert ack["accepted"] == 1 and ack["rejected"] == 12      # 13 items sent
    assert ack["unknown_keys"] == ["nope.power", "battery-1.nothing"]


def test_nan_and_infinity_are_rejected(client):
    with opened(client) as c:
        c.call(config(BATTERY))
        c.send_text('{"type": "states", "items": [{"key": "battery-1.power", "value": NaN},'
                    ' {"key": "battery-1.power", "value": Infinity}]}')
        ack = c.recv()
    assert ack["accepted"] == 0 and ack["rejected"] == 2


def test_a_fixed_value_has_no_key_to_send_to(client):
    with opened(client) as c:
        c.call(config(METER))
        ack = c.call({"type": "states", "items": [{"key": "electricity-meter.price_import", "value": 1}]})
    assert ack["accepted"] == 0 and ack["unknown_keys"] == ["electricity-meter.price_import"]


def test_only_declared_keys_are_accepted_and_unknown_keys_are_capped(client):
    with opened(client) as c:
        c.call(config(BATTERY))
        ack = c.call({"type": "states", "items": [{"key": f"x.k{i}", "value": 1} for i in range(40)]})
    assert ack["accepted"] == 0 and ack["rejected"] == 40 and len(ack["unknown_keys"]) == 20


def test_states_before_any_config_are_unknown_not_fatal(client):
    with opened(client) as c:
        ack = c.call({"type": "states", "items": [{"key": "battery-1.power", "value": 1}]})
    assert ack["accepted"] == 0 and ack["unknown_keys"] == ["battery-1.power"]


def test_the_server_remembers_the_config_after_a_reconnect(client):
    now = time.time()
    with opened(client) as c:
        c.call(config(BATTERY))
    with opened(client) as c:                                           # no new config
        ack = c.call({"type": "states", "items": [{"key": "battery-1.power", "ts": now, "value": 5}]})
    assert ack["accepted"] == 1


# ---- protocol hygiene ---------------------------------------------------------------------------
def test_bad_payloads_do_not_kill_the_connection(client):
    with opened(client) as c:
        c.send_text("not json")
        assert c.recv()["code"] == "bad_json"
        c.send_text("[1,2]")
        assert c.recv()["code"] == "bad_message"
        assert c.call({"type": "wat"})["code"] == "unknown_type"
        assert c.call({"type": "states", "items": "x"})["code"] == "bad_states"
        assert c.call({"type": "states", "items": [{"key": "a.b", "value": 1}] * 1001})["code"] == "bad_states"
        assert c.call({"type": "ping"}) == {"type": "pong"}


def test_second_connection_replaces_first(client):
    with opened(client) as c1:
        with opened(client):
            with pytest.raises(WebSocketDisconnect) as gone:
                c1.recv()
            assert gone.value.code == 4000


def test_sites_are_isolated(client):
    now = time.time()
    with opened(client, CABIN_TOKEN) as c:
        c.call(config(BATTERY))
        c.call({"type": "states", "items": [{"key": "battery-1.power", "ts": now, "value": 5}]})
    flush(client)
    assert {r["site_id"] for r in db_rows(client, "SELECT site_id FROM buckets")} == {"cabin"}
    assert db_rows(client, "SELECT * FROM devices WHERE site_id='home'") == []


def test_oversized_message_closes_with_1009(client):
    with opened(client) as c:
        c.send_text("x" * (300 * 1024))
        with pytest.raises(WebSocketDisconnect) as gone:
            c.recv()
        assert gone.value.code == 1009


def test_flooding_gets_rate_limited_and_closed_with_4429(client, monkeypatch):
    monkeypatch.setattr(ingest, "MAX_MESSAGES_PER_SECOND", 5)
    with opened(client) as c:
        for _ in range(5):
            assert c.call({"type": "ping"}) == {"type": "pong"}
        err = c.call({"type": "ping"})
        assert err["code"] == "rate_limited"
        with pytest.raises(WebSocketDisconnect) as gone:
            c.recv()
        assert gone.value.code == 4429


def test_unknown_fields_are_ignored(client):
    now = time.time()
    with opened(client) as c:
        ack = c.call({**config({**BATTERY, "colour": "red"}), "future": True})
        assert ack["instances"] == 1
        ack = c.call({"type": "states", "future": 1,
                      "items": [{"key": "battery-1.power", "ts": now, "value": 1, "unit": "W"}]})
        assert ack["accepted"] == 1


def test_clients_messages_in_the_documented_examples_work_against_the_server(client):
    """Replay the happy-path example from docs/protocol/examples against a real server."""
    path = ps.SCHEMA_DIR.parent / "examples" / "happy-path.json"
    steps = json.loads(path.read_text())["steps"]
    sent = [s["message"] for s in steps if s["direction"] == "client->server" and "message" in s]
    now = time.time()
    with opened(client) as c:
        for msg in sent:
            if msg["type"] == "states":
                msg = {**msg, "items": [{**i, "ts": now} for i in msg["items"]]}
            reply = c.call(msg)
            if msg["type"] == "config":
                assert reply["for"] == "config" and reply["instances"] == 2
            elif msg["type"] == "states":
                assert reply["accepted"] == 4 and reply["rejected"] == 0
            else:
                assert reply == {"type": "pong"}
