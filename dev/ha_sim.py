#!/usr/bin/env python3
"""Set up a fresh Home Assistant for Energy Hub testing and feed it simulated sensors.

Runs inside the dev compose stack. Every step is idempotent, so it is safe to restart.

1. complete Home Assistant onboarding (user dev / password dev) or log in
2. create demo sensors for the simulated household (tools/simulation.py)
3. fill the energy dashboard from them (grid, two solar inverters, two batteries, gas, plugs)
4. add the Energy Hub integration, taking everything the energy dashboard offers
5. add a heat pump and a water meter through the integration's options flow
6. keep updating the sensor values once per second
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time

import aiohttp

sys.path.insert(0, os.path.dirname(__file__))
from simulation import SPECS, Sim  # noqa: E402

HA = os.environ.get("HA_URL", "http://homeassistant:8123")
HUB_URL = os.environ.get("HUB_URL", "http://energy-hub:8000")
HUB_KEY = os.environ["HUB_KEY"]
HUB_UI_KEY = os.environ["HUB_UI_KEY"]
HUB_SITE = os.environ.get("HUB_SITE", "home")
USER, PASSWORD = "dev", "dev"
CLIENT = "http://localhost:8123/"
REDIRECT = CLIENT + "?auth_callback=1"

log = logging.getLogger("ha_sim")

ENERGY = {"unit_of_measurement": "kWh", "device_class": "energy", "state_class": "total_increasing"}
POWER = {"unit_of_measurement": "W", "device_class": "power", "state_class": "measurement"}


def entity(instance: str, sensor: str) -> str:
    return f"sensor.{instance.replace('-', '_')}_{sensor}"


def attributes(device: str, sensor: str) -> dict:
    """Home Assistant attributes that make the demo sensor look like the real thing."""
    if sensor.startswith("energy") or (sensor == "energy"):
        return ENERGY
    if sensor.startswith("power") or sensor == "power":
        return POWER
    if sensor == "state_of_charge":
        return {"unit_of_measurement": "%", "device_class": "battery", "state_class": "measurement"}
    if sensor == "consumption":
        klass = "gas" if device == "gasmeter" else "water"
        return {"unit_of_measurement": "m³", "device_class": klass, "state_class": "total_increasing"}
    if sensor == "flow_rate":
        return {"unit_of_measurement": "L/min", "device_class": "volume_flow_rate", "state_class": "measurement"}
    if sensor in ("supply_temp", "room_temp"):
        return {"unit_of_measurement": "°C", "device_class": "temperature", "state_class": "measurement"}
    if sensor.startswith("price"):
        return {"unit_of_measurement": "EUR/kWh", "state_class": "measurement"}
    return {"state_class": "measurement"}                        # cop


def sensor_states(values: dict) -> list[tuple[str, str, dict, float]]:
    """(entity id, friendly name, attributes, value) for every demo sensor."""
    out = []
    for iid, device, name, readers, _fixed in SPECS:
        for sid, read in readers.items():
            if iid == "meter" and sid in ("power_import", "power_export"):
                continue                       # Home Assistant's grid power is one signed sensor
            attrs = {**attributes(device, sid), "friendly_name": f"{name} {sid.replace('_', ' ')}"}
            out.append((entity(iid, sid), attrs["friendly_name"], attrs, round(read(values), 4)))
    out.append(("sensor.grid_power", "Grid power", {**POWER, "friendly_name": "Grid power"},
                round(values["grid_power"], 1)))       # positive when importing, negative when exporting
    return out


def energy_prefs() -> dict:
    e = entity
    sources = [
        {"type": "grid", "stat_energy_from": e("meter", "energy_import"),
         "stat_energy_to": e("meter", "energy_export"), "stat_cost": None,
         "entity_energy_price": e("meter", "price_import"), "number_energy_price": None,
         "stat_compensation": None, "entity_energy_price_export": None, "number_energy_price_export": 0.10,
         "stat_rate": "sensor.grid_power", "cost_adjustment_day": 0},
    ]
    for iid, name in (("roof-south", "Roof south"), ("roof-north", "Roof north")):
        sources.append({"type": "solar", "stat_energy_from": e(iid, "energy"), "stat_rate": e(iid, "power"),
                        "name": name, "config_entry_solar_forecast": None})
    for iid, name in (("battery-1", "Battery 1"), ("battery-2", "Battery 2")):
        sources.append({"type": "battery", "stat_energy_from": e(iid, "energy_discharged"),
                        "stat_energy_to": e(iid, "energy_charged"), "stat_rate": e(iid, "power"),
                        "stat_soc": e(iid, "state_of_charge"), "name": name})
    sources.append({"type": "gas", "stat_energy_from": e("gas", "consumption"), "stat_cost": None,
                    "entity_energy_price": None, "number_energy_price": 1.62})
    devices = [{"stat_consumption": e(iid, "energy"), "stat_rate": e(iid, "power"), "name": name}
               for iid, device, name, _r, _f in SPECS if device == "consumer_device"]
    return {"energy_sources": sources, "device_consumption": devices}


class HomeAssistant:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self.s = session
        self.token = ""
        self.token_at = 0.0

    async def wait_until_up(self) -> None:
        while True:
            try:
                async with self.s.get(f"{HA}/api/onboarding") as r:
                    if r.status == 200:
                        return
            except aiohttp.ClientError:
                pass
            log.info("waiting for Home Assistant ...")
            await asyncio.sleep(3)

    async def _exchange(self, code: str) -> str:
        async with self.s.post(f"{HA}/auth/token", data={
                "grant_type": "authorization_code", "code": code, "client_id": CLIENT}) as r:
            r.raise_for_status()
            return (await r.json())["access_token"]

    async def login(self) -> None:
        """Onboard (first run) or log in, and keep a fresh access token."""
        async with self.s.get(f"{HA}/api/onboarding") as r:
            steps = {x["step"]: x["done"] for x in await r.json()}
        if not steps.get("user", True):
            log.info("creating user %s", USER)
            async with self.s.post(f"{HA}/api/onboarding/users", json={
                    "client_id": CLIENT, "name": "Dev", "username": USER,
                    "password": PASSWORD, "language": "en"}) as r:
                r.raise_for_status()
                self.token = await self._exchange((await r.json())["auth_code"])
        else:
            async with self.s.post(f"{HA}/auth/login_flow", json={
                    "client_id": CLIENT, "handler": ["homeassistant", None], "redirect_uri": REDIRECT}) as r:
                r.raise_for_status()
                flow = (await r.json())["flow_id"]
            async with self.s.post(f"{HA}/auth/login_flow/{flow}", json={
                    "client_id": CLIENT, "username": USER, "password": PASSWORD}) as r:
                r.raise_for_status()
                self.token = await self._exchange((await r.json())["result"])
        self.token_at = time.monotonic()
        for step in ("core_config", "analytics"):
            if not steps.get(step, True):
                await self.post(f"/api/onboarding/{step}", {})
        if not steps.get("integration", True):
            await self.post("/api/onboarding/integration", {"client_id": CLIENT, "redirect_uri": REDIRECT})

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    async def ensure_fresh_token(self) -> None:
        if time.monotonic() - self.token_at > 20 * 60:         # HA access tokens last 30 minutes
            await self.login()

    async def post(self, path: str, body: dict) -> dict:
        async with self.s.post(f"{HA}{path}", json=body, headers=self.headers) as r:
            if r.status >= 400:
                raise RuntimeError(f"POST {path} -> {r.status}: {await r.text()}")
            return await r.json(content_type=None)

    async def get(self, path: str):
        async with self.s.get(f"{HA}{path}", headers=self.headers) as r:
            r.raise_for_status()
            return await r.json()

    async def ws_command(self, payload: dict) -> dict:
        async with self.s.ws_connect(f"{HA}/api/websocket") as ws:
            await ws.receive_json()
            await ws.send_json({"type": "auth", "access_token": self.token})
            assert (await ws.receive_json())["type"] == "auth_ok"
            await ws.send_json({"id": 1, **payload})
            return await ws.receive_json()

    async def set_state(self, entity_id: str, state: str, attrs: dict) -> None:
        await self.post(f"/api/states/{entity_id}", {"state": state, "attributes": attrs})

    # ---- the integration's flows over the REST API ------------------------------------------
    async def run_flow(self, base: str, start: dict, *answers: dict) -> dict:
        """Start a flow and answer each step in turn; returns the last result."""
        result = await self.post(base, start)
        for answer in answers:
            result = await self.post(f"{base}/{result['flow_id']}", answer)
        return result


async def push_states(ha: HomeAssistant, sim: Sim, dt: float) -> None:
    values = sim.step(time.time(), dt)
    await asyncio.gather(*(ha.set_state(eid, str(v), attrs) for eid, _n, attrs, v in sensor_states(values)))


async def ensure_energy_prefs(ha: HomeAssistant) -> None:
    got = await ha.ws_command({"type": "energy/get_prefs"})
    if got.get("success") and got["result"].get("energy_sources"):
        log.info("energy dashboard already configured")
        return
    res = await ha.ws_command({"type": "energy/save_prefs", **energy_prefs()})
    if not res.get("success"):
        raise RuntimeError(f"energy/save_prefs failed: {res}")
    log.info("energy dashboard configured")


async def ensure_integration(ha: HomeAssistant) -> str:
    """Add the Energy Hub integration (everything the energy dashboard offers); returns the entry id."""
    entries = await ha.get("/api/config/config_entries/entry?domain=energy_hub")
    if entries:
        log.info("Energy Hub integration already added")
        return entries[0]["entry_id"]
    base = "/api/config/config_entries/flow"
    flow = await ha.post(base, {"handler": "energy_hub", "show_advanced_options": False})
    flow = await ha.post(f"{base}/{flow['flow_id']}", {"url": HUB_URL, "token": HUB_KEY})
    if flow.get("type") == "form" and flow.get("step_id") == "discover":
        offered = list(flow["data_schema"][0]["options"])
        flow = await ha.post(f"{base}/{flow['flow_id']}", {"devices": offered})
    if flow.get("type") != "create_entry":
        raise RuntimeError(f"could not add Energy Hub integration: {flow}")
    log.info("Energy Hub integration added (%s)", flow.get("title"))
    return flow["result"]["entry_id"]


async def hub_instances(session: aiohttp.ClientSession) -> set[str]:
    """The device instances the hub knows for the site, to see what is already configured."""
    async with session.post(f"{HUB_URL}/api/login", json={"key": HUB_UI_KEY}) as r:
        r.raise_for_status()
    async with session.get(f"{HUB_URL}/api/sites/{HUB_SITE}/devices") as r:
        r.raise_for_status()
        return {i["id"] for i in (await r.json())["instances"]}


async def ensure_extra_devices(ha: HomeAssistant, hub: aiohttp.ClientSession, entry_id: str) -> None:
    """Add a heat pump and a water meter through the options flow, like a user would."""
    for _ in range(60):                         # wait until the integration has told the hub about its devices
        known = await hub_instances(hub)
        if known:
            break
        await asyncio.sleep(2)
    else:
        raise RuntimeError("the hub never received the integration's devices")

    base = "/api/config/config_entries/options/flow"
    extras = [
        ("heat-pump", "heatpump", "Heat pump", {
            "Power": entity("heatpump", "power"), "Energy": entity("heatpump", "energy"),
            "Supply Temperature": entity("heatpump", "supply_temp"),
            "Room Temperature": entity("heatpump", "room_temp"), "COP": entity("heatpump", "cop")}),
        ("water", "watermeter", "Water", {
            "Water Consumption": entity("water", "consumption"), "Flow Rate": entity("water", "flow_rate")}),
    ]
    for iid, device, name, sensors in extras:
        if iid in known:
            log.info("%s already added", name)
            continue
        result = await ha.run_flow(base, {"handler": entry_id, "show_advanced_options": False},
                                   {"next_step_id": "add_device"}, {"device": device, "name": name}, sensors)
        if result.get("type") != "create_entry":
            raise RuntimeError(f"could not add {name}: {result}")
        log.info("added %s through the options flow", name)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    async with aiohttp.ClientSession() as session, \
            aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as hub:
        ha = HomeAssistant(session)
        await ha.wait_until_up()
        await ha.login()
        sim = Sim()
        await push_states(ha, sim, 1.0)          # the entities must exist before the dashboard uses them
        await ensure_energy_prefs(ha)
        entry_id = await ensure_integration(ha)
        await ensure_extra_devices(ha, hub, entry_id)
        log.info("ready: Home Assistant http://localhost:8123 (dev/dev), hub http://localhost:8000 "
                 "(key dev-dashboard-key). Feeding sensors every second.")
        last = time.monotonic()
        while True:
            await asyncio.sleep(1.0)
            now = time.monotonic()
            await ha.ensure_fresh_token()
            await push_states(ha, sim, now - last)
            last = now


if __name__ == "__main__":
    asyncio.run(main())
