"""A simulated household for fake_site.py, seed_demo.py and the dev stack.

Two solar strings, two batteries, a grid meter, gas, five smart plugs, a heat pump and a water
meter, described as protocol v2 device instances (see docs/protocol.md).
"""
from __future__ import annotations

import json
import math
import random
from datetime import datetime
from typing import Callable

Values = dict[str, float]

# (instance id, device type, name, {sensor id: reader of the simulation values}, {sensor id: fixed number})
SPECS: list[tuple[str, str, str, dict[str, Callable[[Values], float]], dict[str, float]]] = [
    ("meter", "electricitymeter", "Electricity Meter", {
        "energy_import": lambda v: v["grid_import"],
        "energy_export": lambda v: v["grid_export"],
        "power_import": lambda v: max(v["grid_power"], 0.0),
        "power_export": lambda v: max(-v["grid_power"], 0.0),
        "price_import": lambda v: v["price"],
    }, {"price_export": 0.10}),
    ("roof-south", "solar_inverter", "Roof south", {
        "energy": lambda v: v["solar"] * 0.6, "power": lambda v: v["solar_power"] * 0.6}, {}),
    ("roof-north", "solar_inverter", "Roof north", {
        "energy": lambda v: v["solar"] * 0.4, "power": lambda v: v["solar_power"] * 0.4}, {}),
    ("battery-1", "battery", "Battery 1", {
        "state_of_charge": lambda v: v["battery_soc"],
        "power": lambda v: v["battery_power"] * 0.5,
        "energy_charged": lambda v: v["battery_in"] * 0.5,
        "energy_discharged": lambda v: v["battery_out"] * 0.5}, {"capacity": 10.0}),
    ("battery-2", "battery", "Battery 2", {
        "state_of_charge": lambda v: min(100.0, v["battery_soc"] * 0.85),
        "power": lambda v: v["battery_power"] * 0.5,
        "energy_charged": lambda v: v["battery_in"] * 0.5,
        "energy_discharged": lambda v: v["battery_out"] * 0.5}, {"capacity": 5.0}),
    ("gas", "gasmeter", "Gas", {"consumption": lambda v: v["gas"]}, {"price": 1.62}),
    ("plug-car", "consumer_device", "Car charger", {
        "energy": lambda v: v["dev_car"], "power": lambda v: v["p_car"]}, {}),
    ("plug-aircon", "consumer_device", "Aircon attic", {
        "energy": lambda v: v["dev_aircon"], "power": lambda v: v["p_aircon"]}, {}),
    ("plug-desk", "consumer_device", "Desk", {
        "energy": lambda v: v["dev_desk"], "power": lambda v: v["p_desk"]}, {}),
    ("plug-lights", "consumer_device", "Lights", {
        "energy": lambda v: v["dev_lights"], "power": lambda v: v["p_lights"]}, {}),
    ("plug-bike", "consumer_device", "Bike charger", {
        "energy": lambda v: v["dev_bike"], "power": lambda v: v["p_bike"]}, {}),
    ("heatpump", "heatpump", "Heat pump", {
        "power": lambda v: v["hp_power"], "energy": lambda v: v["hp_energy"],
        "supply_temp": lambda v: v["hp_supply"], "room_temp": lambda v: v["hp_room"],
        "cop": lambda v: v["hp_cop"]}, {}),
    ("water", "watermeter", "Water", {
        "consumption": lambda v: v["water"], "flow_rate": lambda v: v["water_flow"]}, {}),
]


def config_message(currency: str = "EUR") -> dict:
    """The protocol `config` message for this household."""
    instances = []
    for iid, device, name, readers, fixed in SPECS:
        sensors = {sid: {"source": f"sim.{iid}.{sid}"} for sid in readers}
        sensors.update({sid: {"fixed": value} for sid, value in fixed.items()})
        instances.append({"id": iid, "device": device, "name": name, "sensors": sensors})
    return {"type": "config", "currency": currency, "instances": instances}


def keys() -> list[str]:
    """Keys (<instance>.<sensor>) of every sensor that carries readings."""
    return [f"{iid}.{sid}" for iid, _d, _n, readers, _f in SPECS for sid in readers]


def readings(values: Values) -> dict[str, float]:
    """Simulation values -> {key: reading}."""
    return {f"{iid}.{sid}": read(values) for iid, _d, _n, readers, _f in SPECS for sid, read in readers.items()}


class Sim:
    CAPACITY_WH = 10_000

    def __init__(self, seed: int = 1) -> None:
        self.rng = random.Random(seed)
        self.counters = {k: 1000.0 for k in (
            "grid_import", "grid_export", "solar", "battery_in", "battery_out",
            "dev_car", "dev_aircon", "dev_desk", "dev_lights", "dev_bike", "hp_energy")}
        self.counters["gas"] = 500.0
        self.counters["water"] = 50.0
        self.soc = 50.0

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"counters": self.counters, "soc": self.soc}, fh)

    def load(self, path: str) -> None:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.counters.update(data["counters"])
        self.soc = data["soc"]

    def step(self, t: float, dt: float) -> Values:
        now = datetime.fromtimestamp(t)
        h = now.hour + now.minute / 60
        solar = max(0.0, math.sin(math.pi * (h - 7) / 10)) * 3000 * (0.7 + 0.3 * self.rng.random()) \
            if 7 <= h <= 17 else 0.0
        car = 7000.0 if 1 <= h < 5 else 0.0
        aircon = 400.0 if 9 <= h < 17 else 15.0
        desk = 80.0 if 8 <= h < 18 else 3.0
        lights = 120.0 if 17 <= h < 23 else 5.0
        bike = 200.0 if 18 <= h < 19 else 0.0
        hp = 900.0 + 600.0 * math.sin(h / 24 * 2 * math.pi) ** 2 if 6 <= h < 22 else 150.0
        untracked = 250 + (200 if 17 <= h < 22 else 0) + self.rng.random() * 80
        home = car + aircon + desk + lights + bike + hp + untracked

        charge = discharge = 0.0
        if solar > home and self.soc < 100:
            charge = min(solar - home, 3000.0)
        elif home > solar and 17 <= h < 23 and self.soc > 5:
            discharge = min(home - solar, 3000.0)
        self.soc = min(100.0, max(0.0, self.soc + (charge - discharge) * dt / 3600 / self.CAPACITY_WH * 100))
        net = home - solar - discharge + charge
        imp, exp = max(net, 0.0), max(-net, 0.0)
        water_flow = 8.0 if 6 <= h < 8 or 18 <= h < 20 else 0.0     # litres per minute

        def kwh(w: float) -> float:
            return w * dt / 3600 / 1000

        c = self.counters
        c["grid_import"] += kwh(imp)
        c["grid_export"] += kwh(exp)
        c["solar"] += kwh(solar)
        c["battery_in"] += kwh(charge)
        c["battery_out"] += kwh(discharge)
        c["dev_car"] += kwh(car)
        c["dev_aircon"] += kwh(aircon)
        c["dev_desk"] += kwh(desk)
        c["dev_lights"] += kwh(lights)
        c["dev_bike"] += kwh(bike)
        c["hp_energy"] += kwh(hp)
        c["water"] += water_flow * dt / 60 / 1000                    # m3
        if 6 <= h < 9 or 17 <= h < 22:
            c["gas"] += 0.8 * dt / 3600
        values = {k: round(v, 4) for k, v in c.items()}
        values.update({
            "grid_power": net, "solar_power": solar, "battery_power": discharge - charge,
            "battery_soc": self.soc, "price": 0.30 if 17 <= h < 21 else 0.22,
            "p_car": car, "p_aircon": aircon, "p_desk": desk, "p_lights": lights, "p_bike": bike,
            "hp_power": hp, "hp_supply": 35 + 5 * math.sin(h / 24 * 2 * math.pi),
            "hp_room": 20.5 + 0.8 * math.sin((h - 6) / 24 * 2 * math.pi),
            "hp_cop": 3.2 + 0.6 * math.sin(h / 12 * math.pi), "water_flow": water_flow,
        })
        return values
