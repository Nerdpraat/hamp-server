import copy
import sys
from pathlib import Path

import pytest
import yaml

from app.devices import ROLES, Catalogue, StaticRejected, load_catalogue, parse_catalogue, validate_static
from app.errors import ConfigError

EXAMPLE = Path(__file__).resolve().parent.parent / "config.example" / "devices.yaml"
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))


@pytest.fixture(scope="module")
def cat() -> Catalogue:
    return load_catalogue(EXAMPLE)


def simple(**sensor):
    return {"devices": [{"id": "d", "name": "D", "sensors": [{"id": "s", "name": "S", **sensor}]}]}


# ---- the shipped catalogue --------------------------------------------------------------
def test_shipped_catalogue_has_all_device_types(cat):
    assert [d.id for d in cat.devices] == [
        "heatpump", "solar_inverter", "battery", "gasmeter", "watermeter",
        "electricitymeter", "consumer_device", "water_heater"]


def test_shipped_sensors_resolve_as_designed(cat):
    s = cat.sensor("electricitymeter", "energy_export")
    assert (s.role, s.kind, s.unit, s.sign) == ("grid_export", "counter", "kWh", -1)
    s = cat.sensor("battery", "energy_charged")
    assert (s.role, s.sign, s.color) == ("battery_charge", -1, "#f06292")
    s = cat.sensor("battery", "state_of_charge")
    assert (s.kind, s.unit, s.dashboard) == ("gauge", "%", True)
    assert cat.sensor("heatpump", "supply_temp").unit == "°C"        # from device_class
    cop = cat.sensor("heatpump", "cop")
    assert (cop.role, cop.kind, cop.unit, cop.dashboard) == (None, "gauge", "", False)
    flow = cat.sensor("watermeter", "flow_rate")
    assert (flow.kind, flow.unit) == ("gauge", "L/min")
    water = cat.sensor("watermeter", "consumption")
    assert (water.role, water.dashboard, water.unit) == ("water", False, "m3")
    assert cat.sensor("heatpump", "energy").role == "device_energy"


def test_price_slots_are_entity_slots(cat):
    for dev, sid in (("electricitymeter", "price_import"), ("electricitymeter", "price_export"),
                     ("gasmeter", "price")):
        s = cat.sensor(dev, sid)
        assert not s.static and s.kind == "gauge" and s.selector.type == "entity"


def test_battery_capacity_slot_is_a_static_number(cat):
    cap = cat.sensor("battery", "capacity")
    assert (cap.role, cap.kind, cap.unit) == ("battery_capacity", "gauge", "Wh")
    assert cap.static is True and cap.selector.type == "number"
    assert (cap.selector.number.min, cap.selector.number.max) == (0, 100000)
    assert [s.id for s in cat.sensors("battery") if s.static] == ["capacity"]
    assert not any(s.static for d in cat.devices if d.id != "battery" for s in cat.sensors(d.id))


def test_hello_sends_the_native_selector_unchanged(cat):
    hello = {s["id"]: s for d in cat.hello() if d["id"] == "battery" for s in d["sensors"]}
    assert hello["capacity"]["selector"] == {"number": {
        "min": 0, "max": 100000, "step": 0.1, "unit_of_measurement": "Wh", "mode": "box"}}
    assert hello["power"]["selector"] == {"entity": {"domain": "sensor", "device_class": "power"}}
    assert hello["capacity"]["unit"] == "Wh"
    assert all("allow_fixed" not in s and "fixed_only" not in s for s in hello.values())


def test_hello_selector_is_written_as_in_the_file_not_normalised():
    data = simple(selector={"entity": {"domain": ["sensor", "number"], "device_class": "power"}})
    assert parse_catalogue(data).hello()[0]["sensors"][0]["selector"] == {
        "entity": {"domain": ["sensor", "number"], "device_class": "power"}}
    data = simple(role="battery_capacity", selector={"number": {"max": 5}})
    assert parse_catalogue(data).hello()[0]["sensors"][0]["selector"] == {"number": {"max": 5}}


def test_select_and_boolean_slots_are_static():
    data = {"devices": [{"id": "d", "name": "D", "sensors": [
        {"id": "mode", "name": "Mode", "selector": {"select": {"options": ["auto", {"value": "on", "label": "On"}],
                                                                "custom_value": True, "mode": "dropdown"}}},
        {"id": "enabled", "name": "Enabled", "selector": {"boolean": None}}]}]}
    cat = parse_catalogue(data)
    mode, enabled = cat.sensor("d", "mode"), cat.sensor("d", "enabled")
    assert mode.static and mode.selector.options == ("auto", "on") and mode.selector.custom_value
    assert enabled.static and enabled.selector.raw == {"boolean": {}}
    assert cat.hello()[0]["sensors"][1]["selector"] == {"boolean": {}}
    assert (mode.kind, enabled.kind) == ("gauge", "gauge")


def test_number_settings_are_optional_and_partial():
    sensor = parse_catalogue(simple(selector={"number": {"max": 5}})).sensor("d", "s")
    assert sensor.selector.number.max == 5 and sensor.selector.number.mode == "box" and sensor.static


def test_entity_device_class_gives_the_defaults_as_string_or_list():
    for dc in ("energy", ["energy", "power"]):
        s = parse_catalogue(simple(selector={"entity": {"device_class": dc}})).sensor("d", "s")
        assert (s.kind, s.unit) == ("counter", "kWh")


@pytest.mark.parametrize("sensor,fragment", [
    ({"selector": {"domain": "sensor"}}, "old flat form"),
    ({"selector": {"domain": "sensor", "device_class": "power"}}, "old flat form"),
    ({"selector": {"entity": {}, "number": {}}}, "exactly one of"),
    ({"selector": {}}, "exactly one of"),
    ({"selector": "sensor"}, "exactly one of"),
    ({"selector": {"text": {}}}, "unknown selector type 'text'"),
    ({"selector": {"entity": "sensor"}}, "must be a mapping"),
    ({"selector": {"entity": {"multiple": True}}}, "multiple is not supported"),
    ({"selector": {"number": {"min": 10, "max": 5}}}, "larger than number.max"),
    ({"selector": {"number": {"step": 0}}}, "greater than 0"),
    ({"selector": {"number": {"mode": "dial"}}}, "box"),
    ({"selector": {"number": {"colour": "red"}}}, "colour"),
    ({"selector": {"select": {}}}, "non-empty list"),
    ({"selector": {"select": {"options": []}}}, "non-empty list"),
    ({"selector": {"select": {"options": [1, 2]}}}, "string"),
    ({"selector": {"select": {"options": [{"label": "x"}]}}}, "string"),
    ({"selector": {"select": {"options": ["a", "a"]}}}, "duplicate"),
    ({"selector": {"select": {"options": ["a"], "multiple": True}}}, "multiple is not supported"),
    ({"selector": {"boolean": {"x": 1}}}, "takes no settings"),
    ({"role": "battery_capacity", "selector": {"entity": {"domain": "sensor"}}}, "must be a number selector"),
    ({"role": "battery_capacity", "selector": {"boolean": {}}}, "must be a number selector"),
    ({"role": "battery_soc", "selector": {"number": {"max": 5}}}, "needs an entity selector"),
    ({"role": "price_import", "selector": {"boolean": {}}}, "needs an entity selector"),
])
def test_invalid_selectors_and_roles_stop_startup(sensor, fragment):
    with pytest.raises(ConfigError) as err:
        parse_catalogue(simple(**sensor))
    assert fragment in str(err.value)


def test_a_static_slot_without_role_is_fine():
    assert parse_catalogue(simple(selector={"number": {"max": 5}})).sensor("d", "s").role is None


def test_every_role_is_used_by_the_shipped_catalogue(cat):
    used = {s.role for d in cat.devices for s in cat.sensors(d.id)}
    assert set(ROLES) <= used


def test_sensor_names_are_unique_per_device(cat):
    for d in cat.devices:
        names = [s.name for s in cat.sensors(d.id)]
        assert len(names) == len(set(names)), d.id


def test_hello_catalogue_matches_the_protocol_schema(cat):
    import protocol_schemas as ps
    hello = {"type": "hello", "version": 2, "site": {"id": "a", "name": "A"}, "server_time": 1.0,
             "limits": {"max_message_bytes": 1, "max_items_per_batch": 1,
                        "max_messages_per_second": 1, "max_instances": 1},
             "devices": cat.hello()}
    ps.validate_server_message(hello)
    flat = str(hello)
    assert "role" not in flat                                         # roles stay on the server


def test_entity_selector_forms(cat):
    assert cat.sensor("heatpump", "cop").selector.raw == {"entity": {"domain": "sensor"}}
    assert cat.sensor("battery", "power").selector.device_class == ["power"]
    s = parse_catalogue(simple(selector={"entity": {"domain": ["sensor", "number"],
                                                    "device_class": ["power", "energy"]}})).sensor("d", "s")
    assert s.selector.device_class == ["power", "energy"]
    assert s.kind == "gauge"                       # the first device class decides the default
    assert parse_catalogue(simple()).sensor("d", "s").selector.raw == {"entity": {"domain": "sensor"}}


# ---- resolution rules ---------------------------------------------------------------------
def test_defaults_from_device_class_without_role():
    c = parse_catalogue(simple(selector={"entity": {"device_class": "energy"}}))
    s = c.sensor("d", "s")
    assert (s.kind, s.unit, s.role) == ("counter", "kWh", None)


def test_unknown_device_class_gives_a_plain_gauge():
    s = parse_catalogue(simple(selector={"entity": {"device_class": "weirdness"}})).sensor("d", "s")
    assert (s.kind, s.unit) == ("gauge", "")


def test_explicit_fields_override_defaults():
    s = parse_catalogue(simple(role="grid_import", unit="Wh", color="#123456")).sensor("d", "s")
    assert (s.unit, s.color, s.kind) == ("Wh", "#123456", "counter")
    s = parse_catalogue(simple(kind="counter", sign=-1)).sensor("d", "s")
    assert (s.kind, s.sign) == ("counter", -1)


# ---- validation ------------------------------------------------------------------------------
def bad(data, match):
    with pytest.raises(ConfigError, match=match):
        parse_catalogue(data)


def test_unknown_role():
    bad(simple(role="flux"), "unknown role 'flux'")


def test_kind_conflicting_with_role():
    bad(simple(role="grid_import", kind="gauge"), "conflicts with role")


def test_duplicate_device_ids():
    data = {"devices": [simple()["devices"][0], simple()["devices"][0]]}
    bad(data, "duplicate device id d")


def test_duplicate_sensor_ids_and_names():
    d = simple()["devices"][0]
    d["sensors"].append({"id": "s", "name": "Other"})
    bad({"devices": [d]}, "duplicate sensor id s")
    d = simple()["devices"][0]
    d["sensors"].append({"id": "t", "name": "S"})
    bad({"devices": [d]}, "duplicate sensor name S")


def test_device_needs_sensors_and_catalogue_needs_devices():
    bad({"devices": [{"id": "d", "name": "D", "sensors": []}]}, "sensors")
    bad({"devices": []}, "devices")


@pytest.mark.parametrize("bad_id", ["Heat", "1abc", "has space", "a-b", ""])
def test_invalid_ids(bad_id):
    d = simple()
    d["devices"][0]["id"] = bad_id
    bad(d, "invalid device id|String should|devices")
    d = simple()
    d["devices"][0]["sensors"][0]["id"] = bad_id
    bad(d, "invalid sensor id")


def test_error_messages_name_the_field():
    with pytest.raises(ConfigError) as err:
        parse_catalogue(simple(role="flux"))
    assert "devices.0.sensors.0.role" in str(err.value)


def test_load_errors(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_catalogue(tmp_path / "missing.yaml")
    p = tmp_path / "devices.yaml"
    p.write_text("- just\n- a list\n")
    with pytest.raises(ConfigError, match="mapping"):
        load_catalogue(p)
    p.write_text("devices: [unclosed")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_catalogue(p)


def test_the_users_original_file_still_loads_without_roles():
    """A catalogue without any role is valid: those sensors only appear in the Devices tab."""
    data = yaml.safe_load(EXAMPLE.read_text())
    stripped = copy.deepcopy(data)
    for d in stripped["devices"]:
        for s in d["sensors"]:
            s.pop("role", None)
    c = parse_catalogue(stripped)
    assert all(not rs.dashboard for d in c.devices for rs in c.sensors(d.id))


def test_the_yaml_examples_in_the_documentation_are_valid():
    import re
    doc = (Path(__file__).resolve().parents[2] / "docs" / "devices.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```yaml\n(.*?)```", doc, re.S)
    assert len(blocks) >= 2
    full = parse_catalogue(yaml.safe_load(blocks[0]))                         # a complete file
    assert full.sensor("battery", "energy_charged").sign == -1
    extra = yaml.safe_load(blocks[1])                                         # an entry to append
    shipped = yaml.safe_load(EXAMPLE.read_text())
    combined = parse_catalogue({"devices": shipped["devices"] + extra})
    ev = combined.sensor("ev_charger", "session_energy")
    assert (ev.role, ev.kind, ev.unit, ev.dashboard) == (None, "counter", "kWh", False)
    assert combined.hello()[-1]["id"] == "ev_charger"


# ---- static values ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def static_cat() -> Catalogue:
    return parse_catalogue({"devices": [{"id": "d", "name": "D", "sensors": [
        {"id": "cap", "name": "Cap", "selector": {"number": {"min": 0, "max": 100}}},
        {"id": "free", "name": "Free", "selector": {"number": {}}},
        {"id": "mode", "name": "Mode", "selector": {"select": {"options": ["auto", "on", ""]}}},
        {"id": "any", "name": "Any", "selector": {"select": {"options": ["a"], "custom_value": True}}},
        {"id": "flag", "name": "Flag", "selector": {"boolean": {}}},
        {"id": "ent", "name": "Ent", "selector": {"entity": {"domain": "sensor"}}}]}]})


@pytest.mark.parametrize("slot,value,stored", [
    ("cap", 13.5, 13.5), ("cap", 0, 0.0), ("cap", 0.0, 0.0), ("cap", 100, 100.0), ("cap", 7, 7.0),
    ("free", -5, -5.0), ("free", 1e12, 1e12),
    ("mode", "auto", "auto"), ("mode", "", ""),
    ("any", "a", "a"), ("any", "whatever", "whatever"), ("any", "", ""),
    ("flag", False, False), ("flag", True, True),
])
def test_valid_static_values_are_returned_as_stored(static_cat, slot, value, stored):
    out = validate_static(static_cat.sensor("d", slot), value)
    assert out == stored and type(out) is type(stored)


@pytest.mark.parametrize("slot,value,fragment", [
    ("cap", -0.1, "at least 0"), ("cap", 100.5, "at most 100"),
    ("cap", True, "must be a number"), ("cap", False, "must be a number"),
    ("cap", "13.5", "must be a number"), ("cap", None, "must be a number"), ("cap", [1], "must be a number"),
    ("cap", float("nan"), "finite"), ("cap", float("inf"), "finite"), ("cap", float("-inf"), "finite"),
    ("mode", "off", "one of auto, on"), ("mode", "AUTO", "one of"), ("mode", 1, "must be a string"),
    ("mode", True, "must be a string"), ("mode", None, "must be a string"),
    ("any", 5, "must be a string"),
    ("flag", 0, "true or false"), ("flag", 1, "true or false"), ("flag", "true", "true or false"),
    ("flag", None, "true or false"),
    ("ent", 1, "not a static value"),
])
def test_invalid_static_values_are_rejected_with_a_reason(static_cat, slot, value, fragment):
    with pytest.raises(StaticRejected, match=fragment):
        validate_static(static_cat.sensor("d", slot), value)
