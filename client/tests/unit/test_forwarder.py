import asyncio

import aiohttp
import pytest

from custom_components.energy_hub import ws_client
from custom_components.energy_hub.ws_client import (
    AuthError, Forwarder, ProtocolError, fetch_hello, normalize_url,
)
from fakehub import DEVICES, KEY, FakeHub, wait_for

BATTERY = {"id": "battery-1", "device": "battery", "name": "Battery 1", "sensors": {
    "power": {"source": "sensor.b1_power"}, "state_of_charge": {"source": "sensor.b1_soc"}}}
METER = {"id": "meter", "device": "electricitymeter", "name": "Meter", "sensors": {
    "energy_import": {"source": "sensor.imp"}, "price_import": {"fixed": 0.25}}}
HEATPUMP = {"id": "hp", "device": "heatpump", "name": "Heat pump", "sensors": {
    "supply_temp": {"source": "sensor.hp_t"}}}
INSTANCES = [BATTERY, METER, HEATPUMP]


async def fast_sleep(seconds: float) -> None:
    await asyncio.sleep(min(seconds, 0.05))


@pytest.fixture
async def session():
    async with aiohttp.ClientSession() as s:
        yield s


@pytest.fixture
async def hub():
    h = FakeHub()
    await h.start()
    yield h
    await h.stop()
    assert h.violations == []          # the client never sent anything the schemas forbid


def make(session, hub, **kw):
    kw.setdefault("throttle", 0.05)
    kw.setdefault("sleep", fast_sleep)
    return Forwarder(session, f"http://127.0.0.1:{hub.port}", kw.pop("key", KEY),
                     kw.pop("instances", INSTANCES), **kw)


@pytest.mark.parametrize("raw,expected", [
    ("https://hub.example.com", "wss://hub.example.com/ws/ingest"),
    ("http://10.0.0.5:8000/", "ws://10.0.0.5:8000/ws/ingest"),
    ("hub.example.com", "wss://hub.example.com/ws/ingest"),
    ("wss://hub.example.com/base", "wss://hub.example.com/base/ws/ingest"),
    ("ws://h:1/ws/ingest", "ws://h:1/ws/ingest"),
])
def test_normalize_url(raw, expected):
    assert normalize_url(raw) == expected


def test_normalize_url_rejects_garbage():
    with pytest.raises(ValueError):
        normalize_url("ftp://example.com")


# ---- handshake ----------------------------------------------------------------------------------
async def test_fetch_hello_returns_the_catalogue(session, hub):
    hello = await fetch_hello(session, f"http://127.0.0.1:{hub.port}", KEY)
    assert hello["site"]["id"] == "unit" and hello["version"] == 2
    assert {d["id"] for d in hello["devices"]} == {d["id"] for d in DEVICES}


async def test_fetch_hello_wrong_key(session, hub):
    with pytest.raises(AuthError):
        await fetch_hello(session, f"http://127.0.0.1:{hub.port}", "wrong")


async def test_fetch_hello_refuses_another_protocol_version(session):
    hub = FakeHub(version=3)
    await hub.start()
    try:
        with pytest.raises(ProtocolError, match="unsupported protocol version 3"):
            await fetch_hello(session, f"http://127.0.0.1:{hub.port}", KEY)
    finally:
        await hub.stop()


async def test_the_forwarder_refuses_another_protocol_version_and_sends_nothing(session):
    hub = FakeHub(version=99)
    await hub.start()
    fwd = make(session, hub)
    fwd.start()
    try:
        await wait_for(lambda: fwd.last_error and "protocol version 99" in fwd.last_error)
        assert not fwd.connected and hub.configs == [] and hub.items == []
    finally:
        await fwd.stop()
        await hub.stop()


# ---- config and readings ----------------------------------------------------------------------
async def test_sends_config_with_currency_and_coalesced_readings(session, hub):
    fwd = make(session, hub, currency="EUR")
    fwd.start()
    try:
        await wait_for(lambda: fwd.connected)
        cfg = hub.configs[0]
        assert cfg["currency"] == "EUR"
        assert [i["id"] for i in cfg["instances"]] == ["battery-1", "meter", "hp"]
        assert cfg["instances"][1]["sensors"]["price_import"] == {"fixed": 0.25}
        assert fwd.site["id"] == "unit" and fwd.limits["max_instances"] == 100
        for v in (20.0, 20.5, 21.0):                       # three readings inside one throttle window
            fwd.push("hp.supply_temp", v)
        await wait_for(lambda: hub.items)
        assert [i["value"] for i in hub.items if i["key"] == "hp.supply_temp"] == [21.0]
    finally:
        await fwd.stop()


async def test_a_fixed_only_slot_round_trips_as_a_fixed_value(session, hub):
    battery = {**BATTERY, "sensors": {**BATTERY["sensors"], "capacity": {"fixed": 10.0}}}
    fwd = make(session, hub, instances=[battery])
    fwd.start()
    try:
        await wait_for(lambda: fwd.connected)
        assert hub.configs[0]["instances"][0]["sensors"]["capacity"] == {"fixed": 10.0}
        assert fwd.last_error is None
    finally:
        await fwd.stop()


async def test_a_source_on_a_fixed_only_slot_is_skipped_with_a_warning(session, hub, caplog):
    battery = {**BATTERY, "sensors": {**BATTERY["sensors"], "capacity": {"source": "sensor.x"}}}
    fwd = make(session, hub, instances=[battery])
    fwd.start()
    try:
        await wait_for(lambda: fwd.connected)
        sensors = hub.configs[0]["instances"][0]["sensors"]
        assert "capacity" not in sensors and "power" in sensors
        assert "battery-1.capacity takes only a fixed value; skipping it" in caplog.text
    finally:
        await fwd.stop()


async def test_unit_lookup_comes_from_the_servers_catalogue(session, hub):
    fwd = make(session, hub)
    assert fwd.unit_for("battery", "power") is None        # nothing known before the first hello
    fwd.start()
    try:
        await wait_for(lambda: fwd.connected)
        assert fwd.unit_for("battery", "power") == "W"
        assert fwd.unit_for("heatpump", "supply_temp") == "°C"
        assert fwd.unit_for("watermeter", "consumption") == "m3"
        assert fwd.unit_for("heatpump", "nothing") is None and fwd.unit_for("nope", "x") is None
    finally:
        await fwd.stop()


async def test_dedup_and_keepalive(session, hub):
    now = [1000.0]
    fwd = make(session, hub, clock=lambda: now[0])
    fwd.start()
    try:
        await wait_for(lambda: fwd.connected)
        fwd.push("hp.supply_temp", 20.0)
        await wait_for(lambda: len(hub.items) == 1)
        fwd.push("hp.supply_temp", 20.0)                   # unchanged and recent: skipped
        await asyncio.sleep(0.3)
        assert len(hub.items) == 1
        now[0] += ws_client.KEEPALIVE_SECONDS + 1          # unchanged but stale: resent
        fwd.push("hp.supply_temp", 20.0)
        await wait_for(lambda: len(hub.items) == 2)
    finally:
        await fwd.stop()


async def test_what_the_server_would_not_accept_is_dropped_locally(session, hub):
    instances = [
        {"id": "ok", "device": "battery", "name": "Ok", "sensors": {
            "power": {"source": "sensor.p"},
            "flux": {"source": "sensor.flux"},                       # unknown sensor
            "energy_charged": {"fixed": 3.0}}},                      # fixed on a slot that takes none
        {"id": "ghost", "device": "flux_capacitor", "name": "Ghost", "sensors": {"x": {"source": "sensor.g"}}},
        {"id": "empty", "device": "battery", "name": "Empty", "sensors": {"nothing": {"source": "sensor.n"}}},
    ]
    original = [dict(i) for i in instances]
    fwd = make(session, hub, instances=instances)
    fwd.start()
    try:
        await wait_for(lambda: hub.configs)
        sent = hub.configs[0]["instances"]
        assert [i["id"] for i in sent] == ["ok"] and list(sent[0]["sensors"]) == ["power"]
        assert instances == original                                  # the caller's data is not changed
        fwd.push("ok.flux", 1.0)                                      # filtered locally, never sent
        fwd.push("ok.power", 5.0)
        await wait_for(lambda: hub.items)
        assert [i["key"] for i in hub.items] == ["ok.power"]
    finally:
        await fwd.stop()


async def test_instances_beyond_the_servers_limit_are_cut(session, hub):
    hub.limits = {**hub.limits, "max_instances": 2}
    many = [{"id": f"hp-{i}", "device": "heatpump", "name": f"HP {i}", "sensors": {"cop": {"source": f"sensor.c{i}"}}}
            for i in range(4)]
    fwd = make(session, hub, instances=many)
    fwd.start()
    try:
        await wait_for(lambda: hub.configs)
        assert [i["id"] for i in hub.configs[0]["instances"]] == ["hp-0", "hp-1"]
    finally:
        await fwd.stop()


async def test_update_instances_applies_without_reconnect(session, hub):
    fwd = make(session, hub)
    fwd.start()
    try:
        await wait_for(lambda: fwd.connected)
        fwd.push("battery-1.power", 1.0)
        await fwd.update_instances([HEATPUMP])
        await wait_for(lambda: len(hub.configs) == 2)
        assert [i["id"] for i in hub.configs[1]["instances"]] == ["hp"]
        assert hub.connections == 1
        fwd.push("battery-1.power", 2.0)                   # no longer declared: never sent
        fwd.push("hp.supply_temp", 30.0)
        await wait_for(lambda: any(i["key"] == "hp.supply_temp" for i in hub.items))
        assert not any(i["key"] == "battery-1.power" and i["value"] == 2.0 for i in hub.items)
    finally:
        await fwd.stop()


# ---- connection handling ----------------------------------------------------------------------
async def test_reconnects_after_server_drop_and_resends_config(session, hub):
    fwd = make(session, hub)
    fwd.start()
    try:
        await wait_for(lambda: fwd.connected)
        await hub.drop_connections()
        await wait_for(lambda: hub.connections == 2 and fwd.connected)
        assert len(hub.configs) == 2
        fwd.push("hp.supply_temp", 19.0)
        await wait_for(lambda: hub.items)
    finally:
        await fwd.stop()


async def test_a_replaced_connection_waits_a_minute_before_trying_again(session, hub):
    waits = []

    async def recording_sleep(seconds):
        waits.append(seconds)
        await asyncio.sleep(0.02)

    fwd = make(session, hub, sleep=recording_sleep)
    fwd.start()
    try:
        await wait_for(lambda: fwd.connected)
        await hub.drop_connections(code=4000)              # another client took over this key
        await wait_for(lambda: hub.connections == 2)
        assert ws_client.REPLACED_WAIT_SECONDS in waits
    finally:
        await fwd.stop()


async def test_offline_buffer_is_bounded_and_flushed_in_order(session):
    hub = FakeHub()
    port = await hub.start()
    await hub.stop()                                       # nothing listens on `port` now
    hub.port = port
    fwd = make(session, hub, max_buffer=3)
    fwd.start()
    try:
        for i in range(6):
            fwd.push("hp.supply_temp", float(i))
            await asyncio.sleep(0.12)
        assert fwd.buffered_batches == 3                   # the oldest batches were dropped
        assert not fwd.connected
        await hub.start(port)
        await wait_for(lambda: len(hub.items) >= 3)
        assert [i["value"] for i in hub.items][:3] == [3.0, 4.0, 5.0]
    finally:
        await fwd.stop()
        await hub.stop()
    assert hub.violations == []


async def test_auth_failure_sets_flag_calls_back_once_and_sends_nothing(session, hub, monkeypatch):
    monkeypatch.setattr(ws_client, "AUTH_RETRY_SECONDS", 0.05)
    called = []
    fwd = make(session, hub, key="wrong-key", on_auth_failed=lambda: called.append(1))
    fwd.start()
    try:
        await wait_for(lambda: fwd.auth_failed)
        await asyncio.sleep(0.3)                           # several retries
        fwd.push("hp.supply_temp", 1.0)
        await asyncio.sleep(0.2)
        assert hub.items == [] and hub.connections == 0
        assert called == [1]                               # the reauth flow is started only once
    finally:
        await fwd.stop()


async def test_a_rejected_config_keeps_retrying_without_crashing(session, hub):
    hub.reject_config = True
    fwd = make(session, hub)
    fwd.start()
    try:
        await wait_for(lambda: hub.connections >= 2)
        assert "invalid_config" in (fwd.last_error or "") and "instances" in fwd.last_error
        assert not fwd.connected
    finally:
        await fwd.stop()


async def test_on_connected_runs_after_the_config_ack(session, hub):
    seen = []

    async def cb():
        seen.append(len(hub.configs))

    fwd = make(session, hub, on_connected=cb)
    fwd.start()
    try:
        await wait_for(lambda: seen)
        assert seen == [1]
    finally:
        await fwd.stop()


async def test_unknown_keys_in_an_ack_make_the_client_send_its_config_again(session, hub):
    fwd = make(session, hub)
    fwd.start()
    try:
        await wait_for(lambda: fwd.connected)
        hub.keys.clear()                                   # the server "forgot" the config
        fwd.push("hp.supply_temp", 5.0)
        await wait_for(lambda: len(hub.configs) == 2)      # it sent the config again
    finally:
        await fwd.stop()


async def test_a_large_clock_difference_is_reported(session, hub, caplog):
    hub.server_time_offset = 3600
    fwd = make(session, hub)
    fwd.start()
    try:
        await wait_for(lambda: fwd.connected)
        assert "clock differs" in caplog.text
    finally:
        await fwd.stop()
