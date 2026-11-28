import json
import time

import pytest
from starlette.websockets import WebSocketDisconnect

import protocol_schemas as ps
import yaml
from fastapi.testclient import TestClient

from app import ingest
from app.devices import parse_catalogue
from app.main import create_app
from conftest import CABIN_TOKEN, EXAMPLE_DIR, HOME_TOKEN, make_config


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
    "price_import": {"source": "sensor.tariff"}}}
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
    assert (soc["kind"], soc["unit"]) == ("gauge", "%")
    assert soc["selector"] == {"entity": {"domain": "sensor", "device_class": "battery"}}
    cap = next(s for s in devices["battery"]["sensors"] if s["id"] == "capacity")
    assert cap["selector"] == {"number": {"min": 0, "max": 100000, "step": 0.1,
                                          "unit_of_measurement": "Wh", "mode": "box"}}
    price = next(s for s in devices["electricitymeter"]["sensors"] if s["id"] == "price_import")
    assert price["selector"] == {"entity": {"domain": "sensor"}}
    assert "allow_fixed" not in json.dumps(hello) and "fixed_only" not in json.dumps(hello)
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
                    "electricity-meter.energy_import": "sensor.p1_import",
                    "electricity-meter.price_import": "sensor.tariff"}
    assert db_rows(client, "SELECT * FROM static_values") == []
    sites = {s["id"]: s for s in client.app.state.db.__class__ and
             client.portal.call(client.app.state.db.list_sites)}
    assert sites["home"]["meta"] == {"currency": "EUR"}


CAP = {"source": "static"}


def site_currency(client):
    sites = {x["id"]: x for x in client.portal.call(client.app.state.db.list_sites)}
    return sites["home"]["meta"].get("currency")


def test_a_config_without_currency_keeps_the_stored_one(client):
    with opened(client) as c:
        c.call(config(BATTERY))                                        # currency EUR
        assert site_currency(client) == "EUR"
        no_currency = {"type": "config", "instances": [BATTERY]}
        assert c.call(no_currency)["type"] == "ack"
        assert site_currency(client) == "EUR"
        c.call({**no_currency, "currency": "USD"})                     # an explicit one still changes it
        assert site_currency(client) == "USD"
        c.call({**no_currency, "currency": ""})                        # and so can an empty one
    assert site_currency(client) == ""


def test_a_null_currency_is_invalid(client):
    with opened(client) as c:
        err = c.call({"type": "config", "currency": None, "instances": [BATTERY]})
    assert err["code"] == "invalid_config" and err["path"] == "currency"


def with_capacity(**extra):
    return {**BATTERY, "sensors": {**BATTERY["sensors"], "capacity": CAP, **extra}}


def static_rows(client):
    return db_rows(client, "SELECT instance, sensor_id, value FROM static_values ORDER BY instance, sensor_id")


def test_a_static_slot_is_registered_with_the_static_marker(client):
    with opened(client) as c:
        ack = c.call(config(with_capacity()))
    assert ack["type"] == "ack" and ack["sensors"] == 4
    assert static_rows(client) == [{"instance": "battery-1", "sensor_id": "capacity", "value": None}]
    keys = {r["key"] for r in db_rows(client, "SELECT key FROM entities")}
    assert "battery-1.capacity" not in keys                     # a static slot has no series
    assert "static" not in {r["source"] for r in db_rows(client, "SELECT source FROM entities")}


def test_a_resent_config_keeps_the_stored_static_value(client):
    with opened(client) as c:
        c.call(config(with_capacity()))
        assert c.call({"type": "states", "items": [{"key": "battery-1.capacity", "value": 13500}]})["accepted"] == 1
        c.call(config(with_capacity()))
    assert static_rows(client) == [{"instance": "battery-1", "sensor_id": "capacity", "value": "13500.0"}]


def test_a_static_slot_that_is_taken_out_loses_its_value(client):
    with opened(client) as c:
        c.call(config(with_capacity()))
        c.call({"type": "states", "items": [{"key": "battery-1.capacity", "value": 13500}]})
        ack = c.call(config(BATTERY))
        assert ack["removed"] == {"instances": [], "sensors": ["battery-1.capacity"]}
        assert static_rows(client) == []
        c.call(config(with_capacity()))                         # added again: starts empty
    assert static_rows(client) == [{"instance": "battery-1", "sensor_id": "capacity", "value": None}]


def test_removing_an_instance_removes_its_static_values(client):
    with opened(client) as c:
        c.call(config(with_capacity(), {**with_capacity(), "id": "battery-2", "name": "B2"}))
        c.call({"type": "states", "items": [{"key": "battery-2.capacity", "value": 5000}]})
        ack = c.call(config(with_capacity()))
    assert ack["removed"] == {"instances": ["battery-2"], "sensors": []}
    assert [r["instance"] for r in static_rows(client)] == ["battery-1"]


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
    ({"type": "config", "instances": [{**BATTERY, "sensors": {"power": {"fixed": 1}}}]},
     "instances[0].sensors.power", "needs a 'source'"),
    ({"type": "config", "instances": [{**BATTERY, "sensors": {"power": {"source": ""}}}]},
     "instances[0].sensors.power.source", "1 to 255"),
    ({"type": "config", "instances": [{**BATTERY, "sensors": {"power": {"source": "static"}}}]},
     "instances[0].sensors.power.source", "takes an entity"),
    ({"type": "config", "instances": [{**BATTERY, "sensors": {"capacity": {"source": "sensor.x"}}}]},
     "instances[0].sensors.capacity.source", "static slot: its source must be 'static'"),
    ({"type": "config", "instances": [{**BATTERY, "sensors": {"capacity": {"fixed": 10000}}}]},
     "instances[0].sensors.capacity", "needs a 'source'"),
    ({"type": "config", "instances": [{**METER, "sensors": {"price_import": {"fixed": 0.25}}}]},
     "instances[0].sensors.price_import", "needs a 'source'"),
    ({"type": "config", "instances": [{**METER, "sensors": {"price_import": {"source": "static"}}}]},
     "instances[0].sensors.price_import.source", "takes an entity"),
    ({"type": "config", "instances": [{**BATTERY, "sensors": {"capacity": {"source": 5}}}]},
     "instances[0].sensors.capacity.source", "1 to 255"),
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
        # battery-2 disappears; battery-1 loses a sensor and the meter loses its price sensor
        slim = {**BATTERY, "sensors": {"power": {"source": "sensor.b1_power"}}}
        meter = {**METER, "sensors": {"energy_import": {"source": "sensor.p1_import"}}}
        ack = c.call(config(slim, meter))
    assert ack["removed"] == {
        "instances": ["battery-2"],
        "sensors": ["battery-1.energy_charged", "battery-1.state_of_charge", "electricity-meter.price_import"]}
    assert {r["key"] for r in db_rows(client, "SELECT DISTINCT key FROM buckets")} == {"battery-1.power"}
    assert {r["instance"] for r in db_rows(client, "SELECT * FROM devices")} == {"battery-1", "electricity-meter"}
    assert static_rows(client) == []


def test_changing_the_source_of_a_sensor_keeps_its_history(client):
    now = time.time()
    with opened(client) as c:
        c.call(config(METER))
        c.call({"type": "states", "items": [{"key": "electricity-meter.price_import", "ts": now, "value": 0.3}]})
        flush(client)
        ack = c.call(config(METER | {"sensors": {"energy_import": {"source": "sensor.x"},
                                                 "price_import": {"source": "sensor.other_tariff"}}}))
    assert ack["removed"] == {"instances": [], "sensors": []}
    sources = {r["key"]: r["source"] for r in db_rows(client, "SELECT key, source FROM entities")}
    assert sources["electricity-meter.price_import"] == "sensor.other_tariff"
    assert len(db_rows(client, "SELECT * FROM buckets WHERE key='electricity-meter.price_import'")) == 1


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


# ---- static values in states -------------------------------------------------------------------
THERMOSTAT = {"id": "thermostat", "name": "Thermostat", "description": "Test device with static slots.",
              "sensors": [
                  {"id": "temp", "name": "Temperature", "selector": {"entity": {"domain": "sensor"}}},
                  {"id": "target", "name": "Target", "selector": {"number": {"min": 0, "max": 30}}},
                  {"id": "mode", "name": "Mode", "selector": {"select": {"options": ["auto", "on", "off"]}}},
                  {"id": "enabled", "name": "Enabled", "selector": {"boolean": {}}}]}
STAT = {"id": "t-1", "device": "thermostat", "name": "Hall", "sensors": {
    "temp": {"source": "sensor.hall_temp"}, "target": CAP, "mode": CAP, "enabled": CAP}}


def catalogue_with_thermostat():
    data = yaml.safe_load((EXAMPLE_DIR / "devices.yaml").read_text())
    data["devices"].append(THERMOSTAT)
    return parse_catalogue(data)


@pytest.fixture
def sclient(tmp_path):
    cfg = make_config(str(tmp_path / "s.db"), catalogue=catalogue_with_thermostat())
    with TestClient(create_app(cfg, retention_interval=3600)) as c:
        yield c


def stored(client):
    return {f"{r['instance']}.{r['sensor_id']}": r["value"] for r in static_rows(client)}


def state(key, value, **extra):
    return {"type": "states", "items": [{"key": key, "value": value, **extra}]}


def test_a_static_number_is_stored_and_acknowledged(client):
    with opened(client) as c:
        c.call(config(with_capacity()))
        ack = c.call(state("battery-1.capacity", 13.5, ts=1730000000))     # ts is ignored
    assert ack == {"type": "ack", "for": "states", "accepted": 1, "rejected": 0}
    assert stored(client) == {"battery-1.capacity": "13.5"}


def test_the_handoff_example_message_works(sclient):
    msg = {"type": "states", "items": [
        {"key": "t-1.target", "ts": 1730000000, "value": 13.5},
        {"key": "t-1.enabled", "ts": 1730000000, "value": False},
        {"key": "t-1.mode", "ts": 1730000000, "value": "auto"}]}
    ps.validate_client_message(msg)
    with opened(sclient) as c:
        c.call(config(STAT))
        ack = c.call(msg)
    assert ack == {"type": "ack", "for": "states", "accepted": 3, "rejected": 0}
    assert stored(sclient) == {"t-1.target": "13.5", "t-1.enabled": "false", "t-1.mode": '"auto"'}


def test_zero_false_and_empty_values_are_real_values(sclient):
    with opened(sclient) as c:
        c.call(config(STAT))
        assert c.call(state("t-1.target", 0))["accepted"] == 1
        assert c.call(state("t-1.enabled", False))["accepted"] == 1
    assert stored(sclient) == {"t-1.target": "0.0", "t-1.enabled": "false", "t-1.mode": None}
    model = sclient.portal.call(load_model, sclient)
    values = {v.sensor.id: v.value for v in model.statics}
    assert values["target"] == 0.0 and values["target"] is not None
    assert values["enabled"] is False
    assert values["mode"] is None


async def load_model(client):
    from app.model import load_site_model
    st = client.app.state
    return await load_site_model(st.db, st.config.catalogue, "home")


@pytest.mark.parametrize("key,value", [
    ("t-1.target", -1), ("t-1.target", 30.5), ("t-1.target", "20"), ("t-1.target", True),
    ("t-1.target", None), ("t-1.target", [1]),
    ("t-1.mode", "heat"), ("t-1.mode", 1), ("t-1.mode", False), ("t-1.mode", ""),
    ("t-1.enabled", 0), ("t-1.enabled", 1), ("t-1.enabled", "true"), ("t-1.enabled", None),
])
def test_values_that_do_not_fit_the_selector_are_rejected_and_not_stored(sclient, key, value):
    with opened(sclient) as c:
        c.call(config(STAT))
        ack = c.call(state(key, value))
    assert ack == {"type": "ack", "for": "states", "accepted": 0, "rejected": 1}
    assert stored(sclient)[key] is None


def test_nan_and_infinity_are_rejected_for_a_static_number(sclient):
    with opened(sclient) as c:
        c.call(config(STAT))
        c.send_text('{"type": "states", "items": [{"key": "t-1.target", "value": NaN},'
                    ' {"key": "t-1.target", "value": Infinity}]}')
        ack = c.recv()
    assert ack["accepted"] == 0 and ack["rejected"] == 2


def test_a_rejected_value_leaves_the_previous_one_in_force(sclient):
    with opened(sclient) as c:
        c.call(config(STAT))
        c.call(state("t-1.target", 21))
        assert c.call(state("t-1.target", 99))["rejected"] == 1
    assert stored(sclient)["t-1.target"] == "21.0"


def test_a_repeated_value_is_accepted_each_time(client):
    with opened(client) as c:
        c.call(config(with_capacity()))
        for _ in range(3):
            assert c.call(state("battery-1.capacity", 10000))["accepted"] == 1
    assert stored(client) == {"battery-1.capacity": "10000.0"}


def test_the_latest_value_wins_in_receive_order(sclient):
    with opened(sclient) as c:
        c.call(config(STAT))
        ack = c.call({"type": "states", "items": [
            {"key": "t-1.target", "value": 10}, {"key": "t-1.target", "value": 99},      # 99 is rejected
            {"key": "t-1.target", "value": 20}, {"key": "t-1.mode", "value": "on"},
            {"key": "t-1.mode", "value": "off"}]})
        assert (ack["accepted"], ack["rejected"]) == (4, 1)
        assert stored(sclient)["t-1.target"] == "20.0" and stored(sclient)["t-1.mode"] == '"off"'
        c.call(state("t-1.target", 5))                          # a later message overwrites an earlier one
        c.call(state("t-1.target", 7))
    assert stored(sclient)["t-1.target"] == "7.0"


def test_acks_follow_the_order_of_the_messages(sclient):
    with opened(sclient) as c:
        c.call(config(STAT))
        msgs = [state("t-1.target", 1), state("t-1.target", 99), state("t-1.enabled", True), {"type": "ping"},
                state("t-1.mode", "nope")]
        for m in msgs:
            c.send(m)
        replies = [c.recv() for _ in msgs]
    assert [(r.get("accepted"), r.get("rejected")) for r in replies] == [
        (1, 0), (0, 1), (1, 0), (None, None), (0, 1)]
    assert replies[3] == {"type": "pong"}


def test_a_mixed_batch_of_readings_and_static_values(sclient):
    now = time.time()
    with opened(sclient) as c:
        c.call(config(STAT))
        ack = c.call({"type": "states", "items": [
            {"key": "t-1.temp", "ts": now, "value": 19.5},
            {"key": "t-1.target", "value": 21},
            {"key": "t-1.temp", "ts": now, "value": "warm"},              # readings stay numeric
            {"key": "t-1.enabled", "value": True},
            {"key": "t-1.nothing", "value": 1}]})
    assert ack == {"type": "ack", "for": "states", "accepted": 3, "rejected": 2, "unknown_keys": ["t-1.nothing"]}
    flush(sclient)
    assert [r["key"] for r in db_rows(sclient, "SELECT key FROM buckets")] == ["t-1.temp"]   # no series for static


def test_a_static_value_needs_no_timestamp_and_ignores_a_bad_one(sclient):
    with opened(sclient) as c:
        c.call(config(STAT))
        ack = c.call({"type": "states", "items": [
            {"key": "t-1.target", "value": 5, "ts": "yesterday"}, {"key": "t-1.target", "value": 6, "ts": 1}]})
    assert ack["accepted"] == 2
    assert stored(sclient)["t-1.target"] == "6.0"


def test_an_entity_reading_cannot_be_sent_for_an_unregistered_static_key(sclient):
    with opened(sclient) as c:
        c.call(config({**STAT, "sensors": {"temp": {"source": "sensor.t"}}}))      # statics not declared
        ack = c.call(state("t-1.target", 5))
    assert ack["accepted"] == 0 and ack["unknown_keys"] == ["t-1.target"]


def test_static_values_survive_a_reconnect(sclient):
    with opened(sclient) as c:
        c.call(config(STAT))
        c.call(state("t-1.target", 22))
    with opened(sclient) as c:                                  # no new config
        assert c.call(state("t-1.mode", "auto"))["accepted"] == 1    # the slot is known right away
    assert stored(sclient) == {"t-1.target": "22.0", "t-1.mode": '"auto"', "t-1.enabled": None}


def test_static_values_survive_a_server_restart(tmp_path):
    db = str(tmp_path / "r.db")
    cat = catalogue_with_thermostat()
    with TestClient(create_app(make_config(db, catalogue=cat), retention_interval=3600)) as c1:
        with opened(c1) as c:
            c.call(config(STAT))
            c.call(state("t-1.target", 22.5))
            c.call(state("t-1.enabled", False))
    with TestClient(create_app(make_config(db, catalogue=cat), retention_interval=3600)) as c2:
        assert stored(c2) == {"t-1.target": "22.5", "t-1.enabled": "false", "t-1.mode": None}
        model = c2.portal.call(load_model, c2)
        assert {v.sensor.id: v.value for v in model.statics} == {"target": 22.5, "enabled": False, "mode": None}


def test_a_stored_value_that_no_longer_fits_the_catalogue_is_ignored_with_a_warning(sclient, caplog):
    with opened(sclient) as c:
        c.call(config(STAT))
        c.call(state("t-1.mode", "on"))
    sclient.portal.call(lambda: sclient.app.state.db.conn.execute(
        "UPDATE static_values SET value='\"retired\"' WHERE sensor_id='mode'"))
    with caplog.at_level("WARNING"):
        model = sclient.portal.call(load_model, sclient)
    assert {v.sensor.id: v.value for v in model.statics}["mode"] is None
    assert "stored value of t-1.mode ignored" in caplog.text


def test_a_failed_write_is_rejected_not_acknowledged(sclient, monkeypatch):
    async def boom(*_a, **_k):
        raise RuntimeError("disk full")
    with opened(sclient) as c:
        c.call(config(STAT))
        monkeypatch.setattr(sclient.app.state.db, "set_static_values", boom)
        ack = c.call(state("t-1.target", 5))
    assert ack == {"type": "ack", "for": "states", "accepted": 0, "rejected": 1}


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
                assert reply["accepted"] == len(msg["items"]) and reply["rejected"] == 0
            else:
                assert reply == {"type": "pong"}
