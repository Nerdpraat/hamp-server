import copy
import sys
from pathlib import Path

import pytest
import yaml

from app.devices import ROLES, Catalogue, load_catalogue, parse_catalogue
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


def test_price_slots_allow_fixed_numbers(cat):
    for dev, sid in (("electricitymeter", "price_import"), ("electricitymeter", "price_export"),
                     ("gasmeter", "price")):
        s = cat.sensor(dev, sid)
        assert s.allow_fixed and s.kind == "gauge"
    assert not cat.sensor("battery", "power").allow_fixed


def test_battery_capacity_slot_takes_only_a_fixed_number(cat):
    cap = cat.sensor("battery", "capacity")
    assert (cap.role, cap.kind, cap.unit) == ("battery_capacity", "gauge", "kWh")
    assert cap.allow_fixed is True and cap.fixed_only is True
    for s in cat.sensors("battery"):
        if s.id != "capacity":
            assert s.fixed_only is False
    assert cat.sensor("electricitymeter", "price_import").fixed_only is False
    hello = {s["id"]: s for d in cat.hello() if d["id"] == "battery" for s in d["sensors"]}
    assert hello["capacity"]["fixed_only"] is True and hello["capacity"]["allow_fixed"] is True
    assert all(isinstance(s["fixed_only"], bool) for s in hello.values())
    assert hello["power"]["fixed_only"] is False


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


def test_selector_forms_are_normalised(cat):
    assert cat.sensor("heatpump", "cop").selector.domain == ["sensor"]
    assert cat.sensor("battery", "power").selector.device_class == ["power"]
    c = parse_catalogue(simple(selector={"domain": ["sensor", "number"], "device_class": ["power", "energy"]}))
    s = c.sensor("d", "s")
    assert s.selector.domain == ["sensor", "number"]
    assert s.selector.device_class == ["power", "energy"]
    assert s.kind == "gauge"                       # the first device class decides the default


# ---- resolution rules ---------------------------------------------------------------------
def test_defaults_from_device_class_without_role():
    c = parse_catalogue(simple(selector={"device_class": "energy"}))
    s = c.sensor("d", "s")
    assert (s.kind, s.unit, s.role) == ("counter", "kWh", None)


def test_unknown_device_class_gives_a_plain_gauge():
    s = parse_catalogue(simple(selector={"device_class": "weirdness"})).sensor("d", "s")
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
