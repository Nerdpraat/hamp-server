import asyncio
import signal
import sqlite3
import time

import aiohttp
import pytest

from conftest import SITES, ServerProcess
from custom_components.energy_hub.ws_client import Forwarder

# uvicorn finishes its graceful shutdown and then re-raises SIGTERM, so a clean stop shows up
# as 0 or as -SIGTERM. Whether the shutdown was really graceful is proven by the final flush.
CLEAN_EXIT = (0, -signal.SIGTERM)

HOME = [
    {"id": "meter", "device": "electricitymeter", "name": "Electricity Meter", "sensors": {
        "energy_import": {"source": "sensor.imp"}, "energy_export": {"source": "sensor.exp"},
        "price_import": {"source": "sensor.price"}, "price_export": {"fixed": 0.1}}},
    {"id": "roof-south", "device": "solar_inverter", "name": "Roof south", "sensors": {
        "energy": {"source": "sensor.pv1"}}},
    {"id": "roof-north", "device": "solar_inverter", "name": "Roof north", "sensors": {
        "energy": {"source": "sensor.pv2"}}},
    {"id": "battery-1", "device": "battery", "name": "Battery 1", "sensors": {
        "state_of_charge": {"source": "sensor.b1_soc"}, "energy_charged": {"source": "sensor.b1_in"}}},
    {"id": "battery-2", "device": "battery", "name": "Battery 2", "sensors": {
        "state_of_charge": {"source": "sensor.b2_soc"}, "energy_charged": {"source": "sensor.b2_in"}}},
    {"id": "gas", "device": "gasmeter", "name": "Gas", "sensors": {
        "consumption": {"source": "sensor.gas"}, "price": {"fixed": 1.5}}},
    {"id": "heat-pump", "device": "heatpump", "name": "Heat pump", "sensors": {
        "supply_temp": {"source": "sensor.hp_t"}}},
]


async def wait_until(cond, timeout=15.0, step=0.05):
    deadline = time.time() + timeout
    while True:
        result = cond()
        if asyncio.iscoroutine(result):
            result = await result
        if result:
            return
        if time.time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(step)


def forwarder(server, session, site="home", instances=HOME, **kw):
    return Forwarder(session, server.base_url, server.keys[site], instances, throttle=0.1, **kw)


def rows(db_path, sql):
    con = sqlite3.connect(db_path)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


# ---- secrets ---------------------------------------------------------------------------------------
def test_secrets_are_generated_once_stored_in_auth_yaml_and_never_logged(tmp_path):
    server = ServerProcess(tmp_path)
    assert server.secrets()["ui_key"] == ""                          # nothing exists yet
    server.start()
    try:
        secrets = server.secrets()
        values = [secrets["ui_key"], secrets["session_secret"], *secrets["keys"].values()]
        assert all(values) and len(set(values)) == 5
        assert all(len(v) >= 16 for v in [secrets["session_secret"], *secrets["keys"].values()])
        log = server.log.read_text()
        assert "generated 5 secret(s)" in log and "key-home" in log
        for value in values:
            assert value not in log                                  # names are logged, never values
        before = (server.config_dir / "auth.yaml").read_text()
        assert server.terminate() in CLEAN_EXIT
        server.log.write_text("")
        server.start()                                               # a second start changes nothing
        assert (server.config_dir / "auth.yaml").read_text() == before
        assert "secret(s)" not in server.log.read_text()           # nothing was generated this time
    finally:
        server.kill()


# ---- client -> server -> API ------------------------------------------------------------------------
async def test_client_to_server_to_api(server, api):
    async with aiohttp.ClientSession() as http:
        fwd = forwarder(server, http, currency="EUR")
        fwd.start()
        try:
            await wait_until(lambda: fwd.connected)
            for i in range(5):
                fwd.push("meter.energy_import", 100 + i * 0.5)
                fwd.push("meter.energy_export", 50 + i * 0.1)
                fwd.push("meter.price_import", 0.30)
                fwd.push("roof-south.energy", 10 + i * 0.1)
                fwd.push("roof-north.energy", 20 + i * 0.2)
                fwd.push("battery-1.energy_charged", 5 + i * 0.05)
                fwd.push("battery-2.energy_charged", 7 + i * 0.05)
                fwd.push("battery-1.state_of_charge", 80)
                fwd.push("battery-2.state_of_charge", 40)
                fwd.push("gas.consumption", 500 + i * 0.01)
                fwd.push("heat-pump.supply_temp", 35 + i)
                await asyncio.sleep(0.25)
            now = int(time.time())
            q = f"start={now - 3600}&end={now + 3600}"

            async def everything_arrived():
                r = await api.get(f"/api/sites/home/summary?{q}")
                v = (await r.json())["values"]
                return v["solar_energy"] > 1.19 and v["grid_import"] > 1.99 and v["gas"] > 0.039
            await wait_until(everything_arrived)

            summary = await (await api.get(f"/api/sites/home/summary?{q}")).json()
            v = summary["values"]
            assert v["grid_import"] == pytest.approx(2.0) and v["grid_export"] == pytest.approx(0.4)
            assert v["solar_energy"] == pytest.approx(0.4 + 0.8)       # two inverters are added
            assert v["battery_charge"] == pytest.approx(0.2 + 0.2)     # two batteries are added
            assert v["home"] == pytest.approx(2.0 + 1.2 - 0.4 - 0.4)
            assert summary["battery_soc"] == pytest.approx(60.0)       # mean of 80 and 40
            totals = await (await api.get(f"/api/sites/home/totals?{q}")).json()
            by_key = {s["key"]: s for s in totals["sources"]}
            assert by_key["meter.energy_import"]["cost"] == pytest.approx(2.0 * 0.30)   # price sensor
            assert by_key["meter.energy_export"]["cost"] == pytest.approx(-0.4 * 0.1)   # fixed price, negative
            assert by_key["gas.consumption"]["cost"] == pytest.approx(0.04 * 1.5)
            assert {"Roof south", "Roof north"} <= {s["name"] for s in totals["sources"]}
            assert totals["currency"] == "EUR"

            devices = await (await api.get("/api/sites/home/devices")).json()
            assert "sensor." not in str(devices)                         # sources are never exposed
            generic = [i["id"] for i in devices["instances"] if i["generic"]]
            assert generic == ["heat-pump"]                              # for the Devices tab
            series = await (await api.get(
                f"/api/sites/home/series?{q}&resolution=5m&keys=heat-pump.supply_temp")).json()
            point = series["series"]["heat-pump.supply_temp"]["points"][0]
            assert (point[2], point[3]) == (35, 39)                      # min and max
            sites = {s["id"]: s for s in await (await api.get("/api/sites")).json()}
            assert sites["home"]["online"] is True and sites["cabin"]["online"] is False
        finally:
            await fwd.stop()


async def test_each_site_only_sees_its_own_data(server, api):
    cabin = [{"id": "cabin-hp", "device": "heatpump", "name": "Cabin heat pump", "sensors": {
        "supply_temp": {"source": "sensor.t"}}}]
    async with aiohttp.ClientSession() as http:
        fwd = forwarder(server, http, site="cabin", instances=cabin)
        fwd.start()
        try:
            await wait_until(lambda: fwd.connected)
            for i in range(3):
                fwd.push("cabin-hp.supply_temp", 20 + i)
                await asyncio.sleep(0.25)
            now = int(time.time())
            q = f"start={now - 3600}&end={now + 3600}&resolution=1h"

            async def cabin_has_data():
                r = await (await api.get(f"/api/sites/cabin/series?{q}")).json()
                return bool(r["series"].get("cabin-hp.supply_temp", {}).get("points"))
            await wait_until(cabin_has_data)
            home = await (await api.get("/api/sites/home/devices")).json()
            assert home == {"instances": [], "entities": []}
        finally:
            await fwd.stop()


async def test_a_key_only_works_for_its_own_site(server):
    async with aiohttp.ClientSession() as http:
        fwd = Forwarder(http, server.base_url, "not-a-key-0123456789", HOME, throttle=0.1)
        fwd.start()
        try:
            await wait_until(lambda: fwd.auth_failed)
            assert not fwd.connected
        finally:
            await fwd.stop()


# ---- restart ----------------------------------------------------------------------------------------------
async def test_restart_keeps_data_keys_and_client_reconnects(tmp_path):
    """SIGTERM flushes buffered buckets; the keys survive; the client buffers and reconnects."""
    server = ServerProcess(tmp_path, flush_interval=60)          # the periodic flush never fires here
    server.start()
    gas = [{"id": "gas", "device": "gasmeter", "name": "Gas", "sensors": {"consumption": {"source": "sensor.g"}}}]
    try:
        keys_before = server.keys
        async with aiohttp.ClientSession() as http:
            fwd = forwarder(server, http, instances=gas)
            fwd.start()
            try:
                await wait_until(lambda: fwd.connected)
                for v in (10.0, 11.0, 12.0):
                    fwd.push("gas.consumption", v)
                    await asyncio.sleep(0.25)
                await asyncio.sleep(0.3)
                assert rows(server.db_path, "SELECT COUNT(*) FROM buckets")[0][0] == 0   # still in memory

                assert server.terminate() in CLEAN_EXIT
                flushed = rows(server.db_path, "SELECT SUM(delta), MAX(last) FROM buckets WHERE key='gas.consumption'")[0]
                assert flushed[0] == pytest.approx(2.0) and flushed[1] == 12.0       # the final flush kept it

                fwd.push("gas.consumption", 13.0)                 # the server is down: buffered client-side
                await asyncio.sleep(0.4)
                assert not fwd.connected and fwd.buffered_batches >= 1

                server.start()                                    # same port, same database, same config dir
                assert server.keys == keys_before                 # the generated keys are still the ones in use
                await wait_until(lambda: fwd.connected, timeout=20)
                fwd.push("gas.consumption", 14.0)
                await asyncio.sleep(0.4)
                assert server.terminate() in CLEAN_EXIT
                total = rows(server.db_path, "SELECT SUM(delta) FROM buckets WHERE key='gas.consumption'")[0][0]
                assert total == pytest.approx(4.0)                # 10 -> 14: nothing lost, nothing doubled
            finally:
                await fwd.stop()
    finally:
        server.kill()


# ---- the protocol -------------------------------------------------------------------------------------------
async def test_the_server_passes_the_protocol_conformance_check(server):
    import protocol_check
    code = await protocol_check.run(server.ingest_url, server.keys["shed"])
    assert code == 0


async def test_the_reference_client_works_against_the_server(server, api):
    import reference_client
    client = reference_client.Client(server.ingest_url, server.keys["home"], reference_client.demo_instances(),
                                     currency="EUR", throttle=0.2)

    async def feed():
        for _ in range(10):
            for key in client.keys():
                client.push(key, 1000.0 + time.time() % 7 if key.endswith(("charged", "discharged", "energy", "import"))
                            else 50.0)
            await asyncio.sleep(0.2)

    feeder = asyncio.create_task(feed())
    try:
        await client.run(duration=3)
    finally:
        feeder.cancel()
    devices = await (await api.get("/api/sites/home/devices")).json()
    names = {i["name"] for i in devices["instances"]}
    assert {"Battery 1", "Battery 2", "Car charger", "Electricity Meter"} <= names
    fixed = next(s for i in devices["instances"] if i["id"] == "meter" for s in i["sensors"] if s["id"] == "price_import")
    assert fixed["fixed"] == 0.25


# ---- load ------------------------------------------------------------------------------------------------------
async def test_load_three_sites_fifteen_sensors(tmp_path):
    """3 sites x 15 sensors. Real life is 1 Hz; run at 10x to prove headroom."""
    from load_test import run_load

    server = ServerProcess(tmp_path, flush_interval=5)
    server.start()
    try:
        keys = list(server.keys.values())
        cpu_before = server.cpu_seconds()
        duration, rate = 12.0, 10.0
        t0 = time.time()
        stats = await run_load(server.port, keys, rate=rate, duration=duration)
        wall = time.time() - t0
        cpu = server.cpu_seconds() - cpu_before
        assert server.terminate() in CLEAN_EXIT
    finally:
        server.kill()

    expected = len(SITES) * 15 * rate * duration
    assert stats["errors"] == 0 and stats["rejected"] == 0
    assert stats["accepted"] == stats["sent"] >= expected * 0.9
    n_rows = rows(server.db_path, "SELECT COUNT(*) FROM buckets")[0][0]
    db_bytes = server.db_path.stat().st_size
    cpu_pct = cpu / wall * 100
    print(f"\nLOAD: {stats['sent']} readings in {wall:.1f}s ({stats['sent'] / wall:.0f}/s, "
          f"{rate:.0f}x real time) -> server CPU {cpu_pct:.1f}% of one core "
          f"(~{cpu_pct / rate:.1f}% at real-time 1 Hz), {n_rows} bucket rows, db {db_bytes / 1024:.0f} KiB")
    assert n_rows <= len(SITES) * 15 * 3                          # 1-minute buckets, not raw readings
    assert cpu_pct < 60                                           # generous: 10x load on one core
