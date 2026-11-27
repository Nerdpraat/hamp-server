"""A small device catalogue shared by the client tests (the shape of the protocol `hello.devices`)."""


def sensor(sid, name, kind, unit, allow_fixed=False, device_class=None, fixed_only=False, number=None):
    selector = {"domain": ["sensor"]}
    if device_class:
        selector["device_class"] = [device_class]
    if number:
        selector["number"] = number
    return {"id": sid, "name": name, "description": name, "kind": kind, "unit": unit,
            "allow_fixed": allow_fixed or fixed_only, "fixed_only": fixed_only, "selector": selector}


DEVICES = [
    {"id": "battery", "name": "Battery", "description": "Home battery", "category": "energy", "sensors": [
        sensor("state_of_charge", "State of Charge", "gauge", "%", device_class="battery"),
        sensor("power", "Power", "gauge", "W", device_class="power"),
        sensor("energy_charged", "Energy Charged", "counter", "kWh", device_class="energy"),
        sensor("energy_discharged", "Energy Discharged", "counter", "kWh", device_class="energy"),
        sensor("capacity", "Capacity", "gauge", "Wh", fixed_only=True,
               number={"min": 0, "max": 100000, "step": 0.1, "unit_of_measurement": "Wh", "mode": "box"})]},
    {"id": "electricitymeter", "name": "Electricity Meter", "description": "Grid", "category": "utilities",
     "sensors": [
         sensor("energy_import", "Energy Import", "counter", "kWh", device_class="energy"),
         sensor("energy_export", "Energy Export", "counter", "kWh", device_class="energy"),
         sensor("power_import", "Power Import", "gauge", "W", device_class="power"),
         sensor("power_export", "Power Export", "gauge", "W", device_class="power"),
         sensor("price_import", "Import Price", "gauge", "/kWh", allow_fixed=True)]},
    {"id": "heatpump", "name": "Heatpump", "description": "Heating", "category": "climate", "sensors": [
        sensor("power", "Power", "gauge", "W", device_class="power"),
        sensor("supply_temp", "Supply Temperature", "gauge", "°C", device_class="temperature"),
        sensor("cop", "COP", "gauge", "")]},
    {"id": "watermeter", "name": "Water Meter", "description": "Water", "category": "utilities", "sensors": [
        sensor("consumption", "Water Consumption", "counter", "m3", device_class="water")]},
]
