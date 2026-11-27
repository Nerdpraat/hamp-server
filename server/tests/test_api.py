import time

import pytest
from starlette.websockets import WebSocketDisconnect

from conftest import HOME_TOKEN, UI_KEY

DAY = 1_799_971_200  # a day boundary (multiple of 86400)


def inst(iid, device, name, **sensors):
    return {"id": iid, "device": device, "name": name, "sensors": sensors}


def src(name):
    return {"source": f"sensor.secret_{name}"}


def seed(client, site="home"):
    """Write a small site and two hours of 1-minute buckets straight into the database."""
    db = client.app.state.db

    async def go():
        await db.upsert_site(site, site)
        await db.replace_config(site, [
            inst("meter", "electricitymeter", "Electricity Meter",
                 energy_import=src("imp"), energy_export=src("exp"), price_import=src("price"),
                 price_export={"fixed": 0.1}),
            inst("roof", "solar_inverter", "Roof", energy=src("sol")),
            inst("gas", "gasmeter", "Gas", consumption=src("gas"), price={"fixed": 1.0}),
            inst("bat", "battery", "Battery", state_of_charge=src("soc")),
        ])
        await db.set_site_meta(site, {"currency": "EUR"})
        rows = []
        for i in range(120):  # two hours
            t = DAY + i * 60
            rows.append((site, "meter.energy_import", t, 0.1, 1.0, None, 0.0, None, 1))
            rows.append((site, "meter.energy_export", t, 0.02, 1.0, None, 0.0, None, 1))
            rows.append((site, "roof.energy", t, 0.05, 1.0, None, 0.0, None, 1))
            rows.append((site, "gas.consumption", t, 0.01, 1.0, None, 0.0, None, 1))
            rows.append((site, "meter.price_import", t, 0.0, 0.25, 0.25, 0.25, 0.25, 1))
            rows.append((site, "bat.state_of_charge", t, 0.0, 55.0 + i, 55.0 + i, 55.0 + i, 55.0 + i, 1))
        await db.upsert_buckets(rows)
    client.portal.call(go)


def q(extra=""):
    return f"start={DAY}&end={DAY + 7200}{extra}"


def test_requires_auth(client):
    for url in ["/api/sites", "/api/me", "/api/sites/home/devices",
                f"/api/sites/home/series?{q()}", f"/api/sites/home/totals?{q()}",
                f"/api/sites/home/summary?{q()}", "/api/sites/home/live"]:
        assert client.get(url).status_code == 401, url


def test_login_wrong_and_right(client):
    assert client.post("/api/login", json={"key": "wrong"}).status_code == 401
    r = client.post("/api/login", json={"key": UI_KEY})
    assert r.status_code == 200
    set_cookie = r.headers["set-cookie"].lower()
    assert "httponly" in set_cookie and "samesite=lax" in set_cookie
    assert client.get("/api/me").status_code == 200


def test_forged_cookie_rejected(client):
    client.cookies.set("eh_session", "forged.value.here")
    assert client.get("/api/me").status_code == 401


def test_login_rate_limit(client):
    for _ in range(5):
        assert client.post("/api/login", json={"key": "bad"}).status_code == 401
    assert client.post("/api/login", json={"key": UI_KEY}).status_code == 429


def test_logout(authed):
    authed.post("/api/logout")
    assert authed.get("/api/me").status_code == 401


def test_sites_and_unknown_site(authed):
    sites = authed.get("/api/sites").json()
    assert {s["id"] for s in sites} == {"home", "cabin"}
    assert all("token" not in s for s in sites)
    assert authed.get("/api/sites/nope/devices").status_code == 404


def test_the_old_types_route_is_gone(authed):
    assert authed.get("/api/sites/home/types").status_code in (404, 405)


# ---- devices -----------------------------------------------------------------------------------------
def test_devices_lists_instances_with_sensors_and_hides_sources(authed):
    seed(authed)
    r = authed.get("/api/sites/home/devices")
    assert "sensor.secret" not in r.text                      # the client's entity ids stay private
    body = r.json()
    insts = {i["id"]: i for i in body["instances"]}
    assert set(insts) == {"meter", "roof", "gas", "bat"}
    meter = {s["id"]: s for s in insts["meter"]["sensors"]}
    assert (meter["energy_export"]["role"], meter["energy_export"]["sign"]) == ("grid_export", -1)
    assert meter["energy_export"]["key"] == "meter.energy_export"
    assert meter["price_export"]["key"] is None and meter["price_export"]["fixed"] == 0.1
    assert insts["meter"]["device_name"] == "Electricity Meter" and insts["meter"]["category"] == "utilities"
    ents = {e["key"]: e for e in body["entities"]}
    assert "meter.price_export" not in ents                    # a fixed value has no series
    assert ents["roof.energy"]["name"] == "Roof"               # solar energy: just the instance name
    assert ents["gas.consumption"]["name"] == "Gas"            # the sensor name would only repeat it
    assert ents["meter.energy_import"]["name"] == "Electricity Meter · Energy Import"
    assert ents["bat.state_of_charge"]["unit"] == "%" and ents["gas.consumption"]["unit"] == "m3"


def test_generic_instances_are_flagged_for_the_devices_tab(authed):
    db = authed.app.state.db

    async def go():
        await db.replace_config("home", [
            inst("hp", "heatpump", "Heat pump", power=src("p"), supply_temp=src("t"), cop=src("c")),
            inst("water", "watermeter", "Water", consumption=src("w")),
            inst("bat", "battery", "Battery", power=src("bp")),
            inst("plug", "consumer_device", "Plug", energy=src("pe")),
        ])
    authed.portal.call(go)
    insts = {i["id"]: i for i in authed.get("/api/sites/home/devices").json()["instances"]}
    assert insts["hp"]["generic"] is True                       # temperatures and COP have no role
    assert insts["water"]["generic"] is True                    # role water has no dashboard
    assert insts["bat"]["generic"] is False and insts["plug"]["generic"] is False


def test_devices_shows_the_battery_capacity_as_a_fixed_sensor(authed):
    db = authed.app.state.db

    async def go():
        await db.replace_config("home", [
            inst("bat", "battery", "Battery", power=src("bp"), capacity={"fixed": 10000})])
    authed.portal.call(go)
    bat = authed.get("/api/sites/home/devices").json()["instances"][0]
    cap = {s["id"]: s for s in bat["sensors"]}["capacity"]
    assert cap["fixed"] == 10000 and cap["key"] is None and cap["unit"] == "Wh"


def test_instances_removed_from_the_catalogue_are_skipped(authed):
    db = authed.app.state.db

    async def go():
        await db.conn.execute("INSERT INTO devices VALUES('home','old','flux_capacitor','Old')")
        await db.conn.commit()
    authed.portal.call(go)
    assert [i["id"] for i in authed.get("/api/sites/home/devices").json()["instances"]] == []


# ---- series ---------------------------------------------------------------------------------------------
def test_series_counter_and_gauge(authed):
    seed(authed)
    r = authed.get(f"/api/sites/home/series?{q('&resolution=1h')}").json()
    imp = r["series"]["meter.energy_import"]["points"]
    assert len(imp) == 2 and imp[0][0] == DAY * 1000
    assert imp[0][1] == pytest.approx(6.0)
    soc = r["series"]["bat.state_of_charge"]["points"][0]
    assert soc[1] == pytest.approx(55 + 29.5) and soc[2] == 55 and soc[3] == 114


def test_series_keys_filter_and_validation(authed):
    seed(authed)
    r = authed.get(f"/api/sites/home/series?start={DAY}&end={DAY + 600}&resolution=5m&keys=meter.energy_import,zzz")
    assert list(r.json()["series"]) == ["meter.energy_import"]
    assert authed.get(f"/api/sites/home/series?start={DAY}&end={DAY + 600}&resolution=9x").status_code == 422
    assert authed.get(f"/api/sites/home/series?start={DAY + 10}&end={DAY}").status_code == 422
    assert authed.get(f"/api/sites/home/series?start=0&end={90 * 86400}").status_code == 422


# ---- totals and summary ---------------------------------------------------------------------------
def test_totals_with_cost(authed):
    seed(authed)
    r = authed.get(f"/api/sites/home/totals?{q()}").json()
    src_ = {s["key"]: s for s in r["sources"]}
    assert src_["meter.energy_import"]["energy"] == pytest.approx(12.0)
    assert src_["meter.energy_import"]["cost"] == pytest.approx(3.0)          # 12 kWh * 0.25 (price sensor)
    assert src_["meter.energy_export"]["signed_energy"] == pytest.approx(-2.4)
    assert src_["meter.energy_export"]["cost"] == pytest.approx(-0.24)        # fixed 0.1, export is negative
    assert src_["roof.energy"]["cost"] is None                                # no price for solar
    assert src_["gas.consumption"]["cost"] == pytest.approx(1.2)              # fixed 1.0 per m3
    assert r["groups"]["grid"]["energy"] == pytest.approx(9.6)
    assert r["groups"]["grid"]["cost"] == pytest.approx(2.76)
    assert r["total_cost"] == pytest.approx(2.76 + 1.2)
    assert r["currency"] == "EUR"
    assert "bat.state_of_charge" not in src_                                  # gauges are not totals


def test_summary(authed):
    seed(authed)
    r = authed.get(f"/api/sites/home/summary?{q()}").json()
    v = r["values"]
    assert v["grid_import"] == pytest.approx(12.0) and v["solar_energy"] == pytest.approx(6.0)
    assert v["home"] == pytest.approx(12.0 + 6.0 - 2.4)
    assert r["net_grid"] == pytest.approx(9.6)
    assert r["battery_soc"] == 55 + 119
    assert r["has"]["solar_energy"] and r["has"]["gas"] and not r["has"]["battery_charge"]


def test_summary_empty_range(authed):
    seed(authed)
    r = authed.get(f"/api/sites/home/summary?start={DAY - 7200}&end={DAY - 3600}").json()
    assert r["values"]["home"] == 0 and r["battery_soc"] is None
    assert r["total_cost"] is not None        # fixed prices still give a cost of 0


def test_a_price_slot_only_prices_its_own_role(authed):
    """Import and export have separate prices; a site without price slots has no cost."""
    db = authed.app.state.db

    async def go():
        await db.replace_config("home", [inst("meter", "electricitymeter", "M",
                                              energy_import=src("i"), energy_export=src("e"))])
        await db.upsert_buckets([("home", "meter.energy_import", DAY, 5.0, 1.0, None, 0.0, None, 1),
                                 ("home", "meter.energy_export", DAY, 2.0, 1.0, None, 0.0, None, 1)])
    authed.portal.call(go)
    r = authed.get(f"/api/sites/home/totals?{q()}").json()
    assert all(s["cost"] is None for s in r["sources"]) and r["total_cost"] is None


def test_several_instances_are_summed_and_listed_separately(authed):
    db = authed.app.state.db

    async def go():
        await db.replace_config("home", [
            inst("roof-south", "solar_inverter", "Roof south", energy=src("s1")),
            inst("roof-north", "solar_inverter", "Roof north", energy=src("s2")),
            inst("bat-1", "battery", "Battery 1", energy_charged=src("c1"), energy_discharged=src("d1"),
                 state_of_charge=src("l1")),
            inst("bat-2", "battery", "Battery 2", energy_charged=src("c2"), energy_discharged=src("d2"),
                 state_of_charge=src("l2")),
            inst("meter", "electricitymeter", "Meter", energy_import=src("imp")),
        ])
        t = DAY
        await db.upsert_buckets([
            ("home", "roof-south.energy", t, 3.0, 1.0, None, 0.0, None, 1),
            ("home", "roof-north.energy", t, 2.0, 1.0, None, 0.0, None, 1),
            ("home", "bat-1.energy_charged", t, 1.0, 1.0, None, 0.0, None, 1),
            ("home", "bat-2.energy_charged", t, 0.5, 1.0, None, 0.0, None, 1),
            ("home", "bat-1.energy_discharged", t, 0.25, 1.0, None, 0.0, None, 1),
            ("home", "bat-2.energy_discharged", t, 0.25, 1.0, None, 0.0, None, 1),
            ("home", "bat-1.state_of_charge", t, 0.0, 80.0, 80.0, 80.0, 80.0, 1),
            ("home", "bat-2.state_of_charge", t, 0.0, 40.0, 40.0, 40.0, 40.0, 1),
            ("home", "meter.energy_import", t, 4.0, 1.0, None, 0.0, None, 1),
        ])
    authed.portal.call(go)
    v = authed.get(f"/api/sites/home/summary?{q()}").json()
    assert v["values"]["solar_energy"] == pytest.approx(5.0)
    assert v["values"]["battery_charge"] == pytest.approx(1.5)
    assert v["values"]["battery_discharge"] == pytest.approx(0.5)
    assert v["values"]["home"] == pytest.approx(4.0 + 5.0 + 0.5 - 1.5)
    assert v["battery_soc"] == pytest.approx(60.0)
    totals = authed.get(f"/api/sites/home/totals?{q()}").json()
    names = [s["name"] for s in totals["sources"]]
    assert "Roof south" in names and "Roof north" in names                    # one row per instance
    assert totals["groups"]["solar"]["energy"] == pytest.approx(5.0)
    assert totals["groups"]["battery"]["energy"] == pytest.approx(-1.0)


def test_consumer_devices_are_totalled_but_water_is_not(authed):
    db = authed.app.state.db

    async def go():
        await db.replace_config("home", [
            inst("car", "consumer_device", "Car", energy=src("c"), power=src("cp")),
            inst("hp", "heatpump", "Heat pump", energy=src("h")),
            inst("water", "watermeter", "Water", consumption=src("w")),
        ])
        await db.upsert_buckets([("home", "car.energy", DAY, 7.0, 1.0, None, 0.0, None, 1),
                                 ("home", "hp.energy", DAY, 3.0, 1.0, None, 0.0, None, 1),
                                 ("home", "water.consumption", DAY, 0.2, 1.0, None, 0.0, None, 1)])
    authed.portal.call(go)
    r = authed.get(f"/api/sites/home/totals?{q()}").json()
    by_key = {s["key"]: s for s in r["sources"]}
    assert set(by_key) == {"car.energy", "hp.energy"}                          # not water, not power
    assert by_key["car.energy"]["role"] == "device_energy" and by_key["car.energy"]["group"] == "device"


# ---- live ----------------------------------------------------------------------------------------------------
def push(client, *messages, token=HOME_TOKEN):
    """Run messages through /ws/ingest and return the replies."""
    out = []
    with client.websocket_connect("/ws/ingest", headers={"Authorization": f"Bearer {token}"}) as ws:
        ws.receive_json()
        for m in messages:
            ws.send_json(m)
            out.append(ws.receive_json())
    return out


HP = {"type": "config", "instances": [inst("hp", "heatpump", "Heat pump", supply_temp=src("t"))]}


def test_live_snapshot_and_ws(authed):
    now = time.time()
    with authed.websocket_connect("/ws/ingest", headers={"Authorization": f"Bearer {HOME_TOKEN}"}) as ws:
        ws.receive_json()
        ws.send_json(HP)
        ws.receive_json()
        ws.send_json({"type": "states", "items": [{"key": "hp.supply_temp", "ts": now, "value": 21.5}]})
        ws.receive_json()
        snap = authed.get("/api/sites/home/live").json()
        assert snap["values"]["hp.supply_temp"][1] == 21.5
        with authed.websocket_connect("/ws/live?site=home") as live:
            first = live.receive_json()
            assert first["type"] == "values" and first["values"]["hp.supply_temp"][1] == 21.5
            ws.send_json({"type": "states", "items": [{"key": "hp.supply_temp", "ts": now + 1, "value": 22.0}]})
            ws.receive_json()
            nxt = live.receive_json()   # pushed on the next tick, throttled to about 1 per second
            assert nxt["values"]["hp.supply_temp"][1] == 22.0


def test_live_ws_requires_cookie(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/live?site=home"):
            pass


def test_live_ws_throttled(authed):
    now = time.time()
    with authed.websocket_connect("/ws/ingest", headers={"Authorization": f"Bearer {HOME_TOKEN}"}) as ws:
        ws.receive_json()
        ws.send_json(HP)
        ws.receive_json()
        with authed.websocket_connect("/ws/live?site=home") as live:
            live.receive_json()  # initial (empty)
            for i in range(10):
                ws.send_json({"type": "states", "items": [{"key": "hp.supply_temp", "ts": now + i, "value": i}]})
                ws.receive_json()
            msg = live.receive_json()
            assert msg["values"]["hp.supply_temp"][1] == 9   # many updates collapsed into one push


def test_removing_a_sensor_clears_its_live_value(authed):
    now = time.time()
    with authed.websocket_connect("/ws/ingest", headers={"Authorization": f"Bearer {HOME_TOKEN}"}) as ws:
        ws.receive_json()
        ws.send_json(HP)
        ws.receive_json()
        ws.send_json({"type": "states", "items": [{"key": "hp.supply_temp", "ts": now, "value": 1}]})
        ws.receive_json()
        assert "hp.supply_temp" in authed.get("/api/sites/home/live").json()["values"]
        ws.send_json({"type": "config", "instances": [inst("other", "gasmeter", "G", consumption=src("g"))]})
        ws.receive_json()
        assert authed.get("/api/sites/home/live").json()["values"] == {}


def test_live_ws_rejects_foreign_origin(authed):
    with pytest.raises(WebSocketDisconnect):
        with authed.websocket_connect("/ws/live?site=home", headers={"origin": "https://evil.example"}):
            pass
    with authed.websocket_connect("/ws/live?site=home", headers={"origin": "http://testserver"}) as ws:
        assert ws.receive_json()["type"] == "values"


def test_security_headers_and_no_store(authed):
    r = authed.get("/api/sites")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store"
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert authed.get("/healthz").headers["x-frame-options"] == "DENY"


def test_static_files_are_revalidated(client):
    for path in ("/", "/app.js", "/style.css", "/distribution.js"):
        r = client.get(path)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-cache", path
    again = client.get("/app.js", headers={"if-none-match": client.get("/app.js").headers["etag"]})
    assert again.status_code == 304            # revalidation is cheap


# ---- overview ------------------------------------------------------------------------------------------------
def _fake_model(*pairs, capacity=None):
    """A stand-in for SiteModel: overview_for_site reads `entities` (key, role, instance) and `fixed`.

    The instance is the key's part before the dot; `capacity` maps instance -> fixed Wh.
    """
    from types import SimpleNamespace
    ents = [SimpleNamespace(key=k, role=r, instance=k.split(".")[0]) for k, r in pairs]
    fixed = [SimpleNamespace(instance=i, sensor=SimpleNamespace(role="battery_capacity"), value=v)
             for i, v in (capacity or {}).items()]
    return SimpleNamespace(entities=ents, fixed=fixed)


def test_overview_helper_sums_and_averages():
    from app.api import overview_for_site
    now = 1000.0
    model = _fake_model(("m.pi", "grid_power_import"), ("m.pe", "grid_power_export"),
                        ("r1.p", "solar_power"), ("r2.p", "solar_power"),
                        ("b1.p", "battery_power"), ("b1.s", "battery_soc"), ("b2.s", "battery_soc"))
    values = {"m.pi": [now, 500.0], "m.pe": [now, 120.0], "r1.p": [now, 300.0], "r2.p": [now - 5, 200.0],
              "b1.p": [now, -250.0], "b1.s": [now, 40.0], "b2.s": [now, 60.0]}
    out = overview_for_site(model, values, now)
    assert out["grid"] == {"import_w": 500.0, "export_w": 120.0, "net_w": 380.0}
    assert out["solar"] == {"power_w": 500.0}
    assert out["battery"] == {"power_w": -250.0, "soc": 50.0, "soc_n": 2, "stored_kwh": None}


def test_overview_helper_drops_stale_and_missing():
    from app.api import ONLINE_WINDOW, overview_for_site
    now = 10_000.0
    model = _fake_model(("m.pi", "grid_power_import"), ("r.p", "solar_power"), ("b.s", "battery_soc"))
    values = {"m.pi": [now - ONLINE_WINDOW - 1, 500.0], "b.s": [now, 80.0]}   # grid stale, solar unseen
    out = overview_for_site(model, values, now)
    assert out["grid"] is None and out["solar"] is None
    assert out["battery"] == {"power_w": None, "soc": 80.0, "soc_n": 1, "stored_kwh": None}
    assert overview_for_site(_fake_model(), {}, now) == {"grid": None, "solar": None, "battery": None}


def test_overview_stored_energy_from_soc_and_capacity():
    from app.api import overview_for_site
    out = overview_for_site(_fake_model(("b1.s", "battery_soc"), capacity={"b1": 10000.0}), {"b1.s": [5.0, 62.0]}, 5.0)
    assert out["battery"]["stored_kwh"] == pytest.approx(6.2)
    assert out["battery"]["soc_n"] == 1


def test_overview_stored_energy_skips_batteries_without_capacity():
    from app.api import overview_for_site
    model = _fake_model(("b1.s", "battery_soc"), ("b2.s", "battery_soc"), capacity={"b1": 10000.0})
    out = overview_for_site(model, {"b1.s": [5.0, 40.0], "b2.s": [5.0, 80.0]}, 5.0)
    assert out["battery"]["stored_kwh"] == pytest.approx(4.0)
    assert out["battery"]["soc"] == pytest.approx(60.0) and out["battery"]["soc_n"] == 2


def test_overview_stored_energy_ignores_stale_soc_and_none_without_capacity():
    from app.api import ONLINE_WINDOW, overview_for_site
    now = 10_000.0
    model = _fake_model(("b1.s", "battery_soc"), ("b2.s", "battery_soc"), capacity={"b1": 10000.0})
    out = overview_for_site(model, {"b1.s": [now - ONLINE_WINDOW - 1, 40.0], "b2.s": [now, 80.0]}, now)
    assert out["battery"]["stored_kwh"] is None and out["battery"]["soc_n"] == 1
    out = overview_for_site(_fake_model(("b2.s", "battery_soc")), {"b2.s": [now, 80.0]}, now)
    assert out["battery"]["stored_kwh"] is None


def test_overview_grid_with_import_only_has_zero_export():
    from app.api import overview_for_site
    out = overview_for_site(_fake_model(("m.pi", "grid_power_import")), {"m.pi": [5.0, 70.0]}, 5.0)
    assert out["grid"] == {"import_w": 70.0, "export_w": 0, "net_w": 70.0}


def test_overview_requires_auth(client):
    assert client.get("/api/overview").status_code == 401


def test_overview_endpoint_shape(authed):
    now = time.time()
    cfg = {"type": "config", "instances": [
        inst("meter", "electricitymeter", "Meter", power_import=src("pi"), power_export=src("pe")),
        inst("roof", "solar_inverter", "Roof", power=src("sp")),
    ]}
    push(authed, cfg, {"type": "states", "items": [
        {"key": "meter.power_import", "ts": now, "value": 800.0},
        {"key": "meter.power_export", "ts": now, "value": 0.0},
        {"key": "roof.power", "ts": now, "value": 1500.0}]})
    rows = authed.get("/api/overview").json()
    home = next(r for r in rows if r["id"] == "home")
    assert set(home) == {"id", "name", "online", "last_seen", "grid", "solar", "battery"}
    assert home["grid"]["net_w"] == 800.0
    assert home["solar"]["power_w"] == 1500.0
    assert home["battery"] is None


def test_overview_ws_requires_cookie(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/overview"):
            pass


def test_overview_ws_rejects_foreign_origin(authed):
    with pytest.raises(WebSocketDisconnect):
        with authed.websocket_connect("/ws/overview", headers={"origin": "https://evil.example"}):
            pass


def test_overview_ws_pushes_updates(authed):
    now = time.time()
    cfg = {"type": "config", "instances": [inst("roof", "solar_inverter", "Roof", power=src("sp"))]}
    with authed.websocket_connect("/ws/ingest", headers={"Authorization": f"Bearer {HOME_TOKEN}"}) as ing:
        ing.receive_json()
        ing.send_json(cfg)
        ing.receive_json()
        ing.send_json({"type": "states", "items": [{"key": "roof.power", "ts": now, "value": 100.0}]})
        ing.receive_json()
        with authed.websocket_connect("/ws/overview") as ws:
            first = ws.receive_json()
            assert first["type"] == "overview"
            home = next(s for s in first["sites"] if s["id"] == "home")
            assert home["solar"]["power_w"] == 100.0
            ing.send_json({"type": "states", "items": [{"key": "roof.power", "ts": now + 1, "value": 250.0}]})
            ing.receive_json()
            nxt = ws.receive_json()          # pushed on the next tick
            assert next(s for s in nxt["sites"] if s["id"] == "home")["solar"]["power_w"] == 250.0
