from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from catalogue import DEVICES


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations):
    yield


def hello() -> dict:
    return {"type": "hello", "version": 2, "site": {"id": "home", "name": "Home"}, "server_time": 0,
            "limits": {"max_message_bytes": 1, "max_items_per_batch": 1, "max_messages_per_second": 1,
                       "max_instances": 100},
            "devices": DEVICES}


class FakeForwarder:
    """Stands in for ws_client.Forwarder; records what the manager asks of it."""

    instances: list["FakeForwarder"] = []

    def __init__(self, session, url, key, instances=None, *, throttle=5.0, currency="", on_connected=None,
                 on_auth_failed=None, **_: Any) -> None:
        self.url, self.key, self.throttle, self.currency = url, key, throttle, currency
        self.config = list(instances or [])
        self.on_connected, self.on_auth_failed = on_connected, on_auth_failed
        self.pushed: list[tuple[str, float]] = []
        self.updates: list[list[dict]] = []
        self.started = self.stopped = False
        self.catalogue = DEVICES
        FakeForwarder.instances.append(self)

    def unit_for(self, device_id: str, sensor_id: str) -> str | None:
        for d in self.catalogue:
            if d["id"] == device_id:
                return next((s["unit"] for s in d["sensors"] if s["id"] == sensor_id), None)
        return None

    def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    def push(self, key: str, value: float, ts: float | None = None) -> None:
        self.pushed.append((key, value))

    async def update_instances(self, instances: list[dict]) -> None:
        self.config = list(instances)
        self.updates.append(list(instances))


@pytest.fixture
def fake_forwarder():
    FakeForwarder.instances.clear()
    with patch("custom_components.energy_hub.manager.Forwarder", FakeForwarder):
        yield FakeForwarder


PREFS = {
    "energy_sources": [
        {"type": "grid", "stat_energy_from": "sensor.p1_import", "stat_energy_to": "sensor.p1_export",
         "stat_cost": None, "entity_energy_price": None, "number_energy_price": 0.25,
         "entity_energy_price_export": None, "number_energy_price_export": None,
         "stat_rate": "sensor.p1_power", "cost_adjustment_day": 0},
        {"type": "solar", "stat_energy_from": "sensor.pv_energy", "stat_rate": "sensor.pv_power"},
        {"type": "battery", "stat_energy_from": "sensor.b1_out", "stat_energy_to": "sensor.b1_in",
         "stat_soc": "sensor.b1_soc", "name": "Battery 1"},
        {"type": "battery", "stat_energy_from": "sensor.b2_out", "stat_energy_to": "sensor.b2_in",
         "stat_soc": "sensor.b2_soc", "name": "Battery 2"},
    ],
    "device_consumption": [{"stat_consumption": "sensor.car", "name": "Autolader"}],
}

# The ids the discovery above produces
METER = "electricitymeter-sensor-p1-import"
SOLAR = "solar-inverter-sensor-pv-energy"
BAT1 = "battery-sensor-b1-out"
BAT2 = "battery-sensor-b2-out"
CAR = "consumer-device-sensor-car"
ALL_DISCOVERED = {METER, SOLAR, BAT1, BAT2, CAR}


@pytest.fixture
def energy_prefs():
    """Make discovery see PREFS in both the config flow and the manager."""
    holder = {"prefs": PREFS}

    async def read(hass):
        return holder["prefs"]

    async def listen(hass, listener):
        holder["listener"] = listener
        return lambda: None

    with patch("custom_components.energy_hub.config_flow.async_read_energy_prefs", read), \
         patch("custom_components.energy_hub.manager.async_read_energy_prefs", read), \
         patch("custom_components.energy_hub.manager.async_listen_energy_updates", listen):
        yield holder
