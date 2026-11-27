import pytest

from custom_components.energy_hub.mapper import (
    INSTANCE_ID_RE, NEGATIVE, POSITIVE, InstanceDef, Slot, apply_transform, bindings, convert, discover,
    discovered_id, effective_instances, instance_from_option, manual_id,
)


def name_for(entity_id: str) -> str:
    return entity_id.split(".")[1].replace("_", " ").title()


UNIFIED = {
    "energy_sources": [
        {"type": "grid", "stat_energy_from": "sensor.p1_import", "stat_energy_to": "sensor.p1_export",
         "entity_energy_price": "sensor.tariff", "number_energy_price": None,
         "entity_energy_price_export": None, "number_energy_price_export": 0.08,
         "stat_rate": "sensor.p1_power", "cost_adjustment_day": 0},
        {"type": "solar", "stat_energy_from": "sensor.pv_energy", "stat_rate": "sensor.pv_power",
         "config_entry_solar_forecast": None},
        {"type": "battery", "stat_energy_from": "sensor.bat_out", "stat_energy_to": "sensor.bat_in",
         "stat_rate": "sensor.bat_power", "stat_soc": "sensor.bat_soc"},
        {"type": "gas", "stat_energy_from": "sensor.gas_m3", "stat_cost": None,
         "entity_energy_price": None, "number_energy_price": 1.5},
        {"type": "water", "stat_energy_from": "sensor.water"},
    ],
    "device_consumption": [
        {"stat_consumption": "sensor.car", "name": "Autolader", "stat_rate": "sensor.car_power"},
        {"stat_consumption": "sensor.desk"},
        {"stat_consumption": "external:statistic"},
    ],
}

LEGACY = {
    "energy_sources": [
        {"type": "grid",
         "flow_from": [{"stat_energy_from": "sensor.imp1", "entity_energy_price": None, "number_energy_price": 0.3},
                       {"stat_energy_from": "sensor.imp2", "entity_energy_price": None, "number_energy_price": None}],
         "flow_to": [{"stat_energy_to": "sensor.exp1", "entity_energy_price": "sensor.exp_price",
                      "number_energy_price": None}],
         "power": [{"stat_rate": "sensor.gridp"}], "cost_adjustment_day": 0},
    ],
    "device_consumption": [],
}


def by_device(instances):
    out = {}
    for i in instances:
        out.setdefault(i.device, []).append(i)
    return out


def slots(instance):
    return {s.sensor_id: s for s in instance.slots}


# ---- discovery -------------------------------------------------------------------------------
def test_discover_unified_layout():
    found = by_device(discover(UNIFIED, name_for))
    assert set(found) == {"electricitymeter", "solar_inverter", "battery", "gasmeter", "consumer_device"}

    (meter,) = found["electricitymeter"]
    s = slots(meter)
    assert meter.name == "Electricity Meter" and meter.discovered
    assert s["energy_import"].entity_id == "sensor.p1_import"
    assert s["energy_export"].entity_id == "sensor.p1_export"
    assert s["price_import"].entity_id == "sensor.tariff" and s["price_import"].fixed is None
    assert s["price_export"].fixed == 0.08 and s["price_export"].entity_id is None
    # the one signed power sensor feeds both power slots, split by sign
    assert (s["power_import"].entity_id, s["power_import"].transform) == ("sensor.p1_power", POSITIVE)
    assert (s["power_export"].entity_id, s["power_export"].transform) == ("sensor.p1_power", NEGATIVE)

    (solar,) = found["solar_inverter"]
    assert solar.name == "Solar Inverter"
    assert {k: v.entity_id for k, v in slots(solar).items()} == {"energy": "sensor.pv_energy", "power": "sensor.pv_power"}

    (battery,) = found["battery"]
    assert battery.name == "Battery"
    assert {k: v.entity_id for k, v in slots(battery).items()} == {
        "energy_discharged": "sensor.bat_out", "energy_charged": "sensor.bat_in",
        "power": "sensor.bat_power", "state_of_charge": "sensor.bat_soc"}

    (gas,) = found["gasmeter"]
    assert slots(gas)["consumption"].entity_id == "sensor.gas_m3" and slots(gas)["price"].fixed == 1.5

    cars = {i.name: i for i in found["consumer_device"]}
    assert set(cars) == {"Autolader", "Desk"}                         # named, and from the friendly name
    assert {k: v.entity_id for k, v in slots(cars["Autolader"]).items()} == {
        "energy": "sensor.car", "power": "sensor.car_power"}
    assert set(slots(cars["Desk"])) == {"energy"}                      # no power sensor, no power slot


def test_water_and_external_statistics_are_not_discovered():
    names = {i.name for i in discover(UNIFIED, name_for)}
    assert not any("water" in n.lower() for n in names)
    assert all("external" not in s.entity_id for i in discover(UNIFIED, name_for)
               for s in i.slots if s.entity_id)


def test_discover_legacy_layout_gives_one_meter_per_import_and_export_list_entry():
    meters = discover(LEGACY, name_for)
    assert [m.name for m in meters] == ["Electricity Meter 1", "Electricity Meter 2"]
    first, second = (slots(m) for m in meters)
    assert first["energy_import"].entity_id == "sensor.imp1" and first["price_import"].fixed == 0.3
    assert first["energy_export"].entity_id == "sensor.exp1"
    assert first["price_export"].entity_id == "sensor.exp_price"
    assert first["power_import"].entity_id == "sensor.gridp"           # the power sensor goes to the first
    assert set(second) == {"energy_import"}


def test_several_solar_sources_and_batteries_become_numbered_instances():
    prefs = {"energy_sources": [
        {"type": "solar", "stat_energy_from": "sensor.pv_south", "stat_rate": "sensor.pv_south_w"},
        {"type": "solar", "stat_energy_from": "sensor.pv_north", "stat_rate": "sensor.pv_north_w", "name": "North roof"},
        {"type": "battery", "stat_energy_from": "sensor.b1_out", "stat_energy_to": "sensor.b1_in", "stat_soc": "sensor.b1_soc"},
        {"type": "battery", "stat_energy_from": "sensor.b2_out", "stat_energy_to": "sensor.b2_in", "stat_soc": "sensor.b2_soc"},
    ], "device_consumption": []}
    found = by_device(discover(prefs, name_for))
    assert [i.name for i in found["solar_inverter"]] == ["Solar Inverter 1", "North roof"]
    assert [i.name for i in found["battery"]] == ["Battery 1", "Battery 2"]
    ids = [i.id for v in found.values() for i in v]
    assert len(set(ids)) == len(ids) == 4


def test_discovered_ids_are_valid_and_stable_and_short():
    for inst in discover(UNIFIED, name_for):
        assert INSTANCE_ID_RE.match(inst.id), inst.id
    assert discovered_id("battery", "sensor.bat_out") == "battery-sensor-bat-out"
    assert discovered_id("battery", "sensor.bat_out") == discovered_id("battery", "sensor.bat_out")
    long_entity = "sensor." + "x" * 80
    a, b = discovered_id("solar_inverter", long_entity), discovered_id("solar_inverter", long_entity + "y")
    assert len(a) <= 40 and INSTANCE_ID_RE.match(a) and a != b
    assert a == discovered_id("solar_inverter", long_entity)


def test_discover_empty_and_missing():
    assert discover(None, name_for) == []
    assert discover({}, name_for) == []
    assert discover({"energy_sources": [], "device_consumption": []}, name_for) == []


def test_a_source_without_any_usable_entity_is_skipped():
    prefs = {"energy_sources": [{"type": "solar", "stat_energy_from": "ext:stat"},
                                {"type": "grid", "stat_energy_from": None, "stat_energy_to": None}],
             "device_consumption": []}
    assert discover(prefs, name_for) == []


def test_the_same_entity_twice_gives_one_instance():
    prefs = {"energy_sources": [], "device_consumption": [
        {"stat_consumption": "sensor.a"}, {"stat_consumption": "sensor.a", "name": "again"}]}
    assert len(discover(prefs, name_for)) == 1


# ---- messages and options ------------------------------------------------------------------------
def test_to_message_has_source_or_fixed_and_a_short_name():
    inst = InstanceDef("meter", "electricitymeter", "M" * 100, (
        Slot("energy_import", "sensor.imp"), Slot("price_import", fixed=0.25)))
    assert inst.to_message() == {"id": "meter", "device": "electricitymeter", "name": "M" * 80, "sensors": {
        "energy_import": {"source": "sensor.imp"}, "price_import": {"fixed": 0.25}}}


def test_every_discovered_instance_message_matches_the_protocol_schema():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
    import protocol_schemas as ps
    for prefs in (UNIFIED, LEGACY):
        msg = {"type": "config", "currency": "EUR",
               "instances": [i.to_message() for i in discover(prefs, name_for)]}
        ps.validate_client_message(msg)


def test_options_roundtrip_and_stale_options_are_ignored():
    inst = InstanceDef("hp", "heatpump", "Heat pump", (Slot("power", "sensor.hp_w"), Slot("cop", "sensor.cop")))
    again = instance_from_option(inst.to_option())
    assert again == inst
    price = InstanceDef("g", "gasmeter", "Gas", (Slot("price", fixed=1.5),))
    assert instance_from_option(price.to_option()) == price
    for stale in ({"entity_id": "sensor.x", "type": "heatpump_temperature", "name": "x"},      # earlier layout
                  None, "text", {}, {"id": "Bad Id", "device": "d", "name": "n", "sensors": {"a": {"entity_id": "s.x"}}},
                  {"id": "ok", "device": "d", "name": "", "sensors": {"a": {"entity_id": "s.x"}}},
                  {"id": "ok", "device": "d", "name": "n", "sensors": {}},
                  {"id": "ok", "device": "d", "name": "n", "sensors": {"a": {"nonsense": 1}}},
                  {"id": "ok", "device": "d", "name": "n", "sensors": {"a": "sensor.x"}}):
        assert instance_from_option(stale) is None, stale


def test_manual_ids_are_unique_and_valid():
    assert manual_id("Heat pump", "heatpump", []) == "heat-pump"
    assert manual_id("Heat pump", "heatpump", ["heat-pump"]) == "heat-pump-2"
    assert manual_id("Heat pump", "heatpump", ["heat-pump", "heat-pump-2"]) == "heat-pump-3"
    assert manual_id("!!!", "consumer_device", []) == "consumer-device"
    long = manual_id("x" * 100, "battery", [])
    assert INSTANCE_ID_RE.match(long)
    assert INSTANCE_ID_RE.match(manual_id("Étagère ✓", "battery", []))


def test_effective_instances_exclusion_and_manual_override():
    discovered = discover(UNIFIED, name_for)
    gone = next(i.id for i in discovered if i.device == "gasmeter")
    eff = {i.id: i for i in effective_instances(discovered, [], [gone])}
    assert gone not in eff and len(eff) == len(discovered) - 1
    manual = InstanceDef(gone, "gasmeter", "My gas", (Slot("consumption", "sensor.other"),))
    eff = {i.id: i for i in effective_instances(discovered, [manual], [gone])}
    assert eff[gone].name == "My gas"                                 # manual wins, even over an exclusion
    assert [i.id for i in effective_instances([], [manual], [])] == [gone]


def test_bindings_and_transforms():
    inst = InstanceDef("meter", "electricitymeter", "M", (
        Slot("energy_import", "sensor.imp"), Slot("power_import", "sensor.p", transform=POSITIVE),
        Slot("power_export", "sensor.p", transform=NEGATIVE), Slot("price_import", fixed=0.2)))
    b = bindings([inst])
    assert [x.key for x in b] == ["meter.energy_import", "meter.power_import", "meter.power_export"]   # no fixed
    assert [x.entity_id for x in b if x.entity_id == "sensor.p"] == ["sensor.p", "sensor.p"]
    assert apply_transform(500, POSITIVE) == 500 and apply_transform(-500, POSITIVE) == 0
    assert apply_transform(-500, NEGATIVE) == 500 and apply_transform(500, NEGATIVE) == 0
    assert apply_transform(-3, None) == -3


@pytest.mark.parametrize("value,u_from,u_to,expected", [
    (1500, "Wh", "kWh", 1.5), (2, "MWh", "kWh", 2000), (1.5, "kW", "W", 1500),
    (3, "kWh", "kWh", 3), (5, "L", "m3", 0.005), (1, "m³", "m3", 1),
    (212, "°F", "°C", 100), (10, "°C", "°F", 50), (7, "kWh", "W", 7),     # unrelated: unchanged
    (7, None, "kWh", 7), (7, "kWh", "", 7), (7, "bogus", "kWh", 7),
    (60, "L/h", "L/min", 1), (1, "m³/h", "L/min", 1000 / 60), (2, "L/s", "L/min", 120),
])
def test_convert(value, u_from, u_to, expected):
    assert convert(value, u_from, u_to) == pytest.approx(expected)
