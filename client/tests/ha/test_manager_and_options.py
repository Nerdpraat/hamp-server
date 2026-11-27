from unittest.mock import patch

from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.energy_hub.const import (
    CONF_AUTO_SYNC, CONF_DEVICES, CONF_EXCLUDED, CONF_THROTTLE, CONF_TOKEN, CONF_URL, DOMAIN,
)
from custom_components.energy_hub.ws_client import AuthError

from conftest import ALL_DISCOVERED, CAR, METER, SOLAR

DATA = {CONF_URL: "https://hub.example.com", CONF_TOKEN: "key-key-key-key-1"}
OPTIONS = {CONF_AUTO_SYNC: True, CONF_EXCLUDED: [], CONF_DEVICES: [], CONF_THROTTLE: 5}

HEATPUMP = {"id": "heat-pump", "device": "heatpump", "name": "Heat pump",
            "sensors": {"power": {"entity_id": "sensor.hp_w"}, "supply_temp": {"entity_id": "sensor.hp_t"}}}


async def setup_entry(hass, options=None):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, options=options if options is not None else dict(OPTIONS),
                            unique_id="u#home")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def ids(fwd):
    return {i["id"] for i in fwd.config}


def instance(fwd, iid):
    return next(i for i in fwd.config if i["id"] == iid)


# ---- the manager ------------------------------------------------------------------------------
async def test_setup_registers_the_discovered_instances_and_starts(hass, energy_prefs, fake_forwarder):
    hass.states.async_set("sensor.p1_import", "12.5", {"unit_of_measurement": "kWh"})
    entry = await setup_entry(hass)
    fwd = fake_forwarder.instances[0]
    assert fwd.started and fwd.url == DATA[CONF_URL] and fwd.key == DATA[CONF_TOKEN]
    assert fwd.currency == hass.config.currency                     # costs get the HA currency symbol
    assert ids(fwd) == ALL_DISCOVERED

    meter = instance(fwd, METER)["sensors"]
    assert meter["energy_import"] == {"source": "sensor.p1_import"}
    assert meter["price_import"] == {"fixed": 0.25}
    assert meter["power_import"] == meter["power_export"] == {"source": "sensor.p1_power"}
    assert instance(fwd, CAR)["name"] == "Autolader"
    assert ("%s.energy_import" % METER, 12.5) in fwd.pushed         # the current state goes out at once
    assert {i.id for i in entry.runtime_data.instances} == ALL_DISCOVERED


async def test_state_changes_are_pushed_with_unit_conversion_and_filtering(hass, energy_prefs, fake_forwarder):
    await setup_entry(hass)
    fwd = fake_forwarder.instances[0]
    fwd.pushed.clear()
    hass.states.async_set("sensor.p1_import", "1500", {"unit_of_measurement": "Wh"})   # the server wants kWh
    hass.states.async_set("sensor.pv_energy", "unavailable")
    hass.states.async_set("sensor.car", "unknown")
    hass.states.async_set("sensor.b1_soc", "not a number")
    hass.states.async_set("sensor.somebody_else", "5")                                  # not used
    await hass.async_block_till_done()
    assert fwd.pushed == [(f"{METER}.energy_import", 1.5)]


async def test_the_signed_grid_power_sensor_feeds_both_the_import_and_export_slots(hass, energy_prefs, fake_forwarder):
    await setup_entry(hass)
    fwd = fake_forwarder.instances[0]
    for state, unit, expected in (("700", "W", (700, 0)), ("-500", "W", (0, 500)), ("-0.5", "kW", (0, 500)),
                                  ("0", "W", (0, 0))):
        fwd.pushed.clear()
        hass.states.async_set("sensor.p1_power", "999999", {"unit_of_measurement": "W"})   # force a change
        fwd.pushed.clear()
        hass.states.async_set("sensor.p1_power", state, {"unit_of_measurement": unit})
        await hass.async_block_till_done()
        assert dict(fwd.pushed) == {f"{METER}.power_import": expected[0], f"{METER}.power_export": expected[1]}


async def test_unselected_discovered_instances_are_not_tracked(hass, energy_prefs, fake_forwarder):
    await setup_entry(hass, {**OPTIONS, CONF_EXCLUDED: [CAR]})
    fwd = fake_forwarder.instances[0]
    assert CAR not in ids(fwd)
    fwd.pushed.clear()
    hass.states.async_set("sensor.car", "3")
    await hass.async_block_till_done()
    assert fwd.pushed == []


async def test_options_in_an_unknown_shape_are_ignored(hass, energy_prefs, fake_forwarder):
    stale = {CONF_AUTO_SYNC: True, CONF_THROTTLE: 5, CONF_EXCLUDED: ["sensor.car"],
             "manual": [{"entity_id": "sensor.hp", "type": "heatpump_temperature", "name": "HP"}],
             CONF_DEVICES: [{"entity_id": "sensor.x", "type": "old"}, "junk", None]}
    await setup_entry(hass, stale)
    assert ids(fake_forwarder.instances[0]) == ALL_DISCOVERED


async def test_one_entity_can_fill_several_slots(hass, energy_prefs, fake_forwarder):
    energy_prefs["prefs"] = None
    two = {"id": "twin", "device": "electricitymeter", "name": "Twin", "sensors": {
        "energy_import": {"entity_id": "sensor.shared"}, "energy_export": {"entity_id": "sensor.shared"}}}
    await setup_entry(hass, {**OPTIONS, CONF_DEVICES: [two]})
    fwd = fake_forwarder.instances[0]
    fwd.pushed.clear()
    hass.states.async_set("sensor.shared", "42", {"unit_of_measurement": "kWh"})
    await hass.async_block_till_done()
    assert sorted(fwd.pushed) == [("twin.energy_export", 42.0), ("twin.energy_import", 42.0)]


async def test_energy_dashboard_changes_are_followed_automatically(hass, energy_prefs, fake_forwarder):
    await setup_entry(hass)
    fwd = fake_forwarder.instances[0]
    new = "consumer-device-sensor-new-meter"
    assert new not in ids(fwd)
    energy_prefs["prefs"] = {**energy_prefs["prefs"], "device_consumption": [
        {"stat_consumption": "sensor.car"}, {"stat_consumption": "sensor.new_meter"}]}
    await energy_prefs["listener"]()                          # HA calls this when the dashboard is saved
    assert new in ids(fwd)


async def test_auto_sync_off_ignores_dashboard_changes(hass, energy_prefs, fake_forwarder):
    await setup_entry(hass, {**OPTIONS, CONF_AUTO_SYNC: False})
    fwd = fake_forwarder.instances[0]
    assert ids(fwd) == set()
    energy_prefs["prefs"] = {"energy_sources": [], "device_consumption": [{"stat_consumption": "sensor.x"}]}
    await energy_prefs["listener"]()
    assert ids(fwd) == set()


async def test_unload_stops_the_forwarder(hass, energy_prefs, fake_forwarder):
    entry = await setup_entry(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert fake_forwarder.instances[0].stopped


async def test_an_auth_failure_starts_reauthentication(hass, energy_prefs, fake_forwarder):
    await setup_entry(hass)
    fake_forwarder.instances[0].on_auth_failed()
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert any(f["context"]["source"] == "reauth" for f in flows)


# ---- the options flow ----------------------------------------------------------------------------------
async def menu(hass, entry, step):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    return await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": step})


def fields(result):
    return {str(k) for k in result["data_schema"].schema}


async def add_heatpump(hass, entry, name="Heat pump", **entities):
    result = await menu(hass, entry, "add_device")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"device": "heatpump", "name": name})
    assert result["step_id"] == "sensors"
    return await hass.config_entries.options.async_configure(result["flow_id"], entities)


async def test_the_menu_offers_every_action(hass, energy_prefs, fake_forwarder):
    entry = await setup_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["menu_options"] == ["add_device", "edit_device", "remove_devices", "rediscover", "settings"]


async def test_add_device_lists_the_catalogue_by_category(hass, energy_prefs, fake_forwarder):
    entry = await setup_entry(hass)
    result = await menu(hass, entry, "add_device")
    options = result["data_schema"].schema["device"].config["options"]
    assert [(o["value"], o["label"]) for o in options] == [
        ("heatpump", "Heatpump (climate)"), ("battery", "Battery (energy)"),
        ("electricitymeter", "Electricity Meter (utilities)"), ("watermeter", "Water Meter (utilities)")]


async def test_add_device_builds_the_sensor_form_from_the_catalogue(hass, energy_prefs, fake_forwarder):
    entry = await setup_entry(hass)
    result = await menu(hass, entry, "add_device")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"device": "heatpump", "name": "Heat pump"})
    assert result["step_id"] == "sensors"
    assert fields(result) == {"Power", "Supply Temperature", "COP"}             # labelled by sensor name
    schema = {str(k): v for k, v in result["data_schema"].schema.items()}
    assert schema["Supply Temperature"].config["filter"][0]["device_class"] == ["temperature"]
    assert schema["Supply Temperature"].config["filter"][0]["domain"] == ["sensor"]
    assert "device_class" not in schema["COP"].config["filter"][0]               # COP: any sensor
    ph = result["description_placeholders"]
    assert ph["device"] == "Heatpump" and ph["name"] == "Heat pump"
    assert "- **Supply Temperature**" in ph["slots"] and "- **COP**" in ph["slots"]


async def test_add_device_applies_live_without_a_reload(hass, energy_prefs, fake_forwarder):
    entry = await setup_entry(hass)
    fwd = fake_forwarder.instances[0]
    hass.states.async_set("sensor.hp_t", "35.5", {"device_class": "temperature", "unit_of_measurement": "°C"})
    result = await add_heatpump(hass, entry, **{"Power": "sensor.hp_w", "Supply Temperature": "sensor.hp_t"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert len(fake_forwarder.instances) == 1 and not fwd.stopped              # no reload, no restart
    assert entry.options[CONF_DEVICES] == [HEATPUMP]
    assert instance(fwd, "heat-pump")["sensors"] == {
        "power": {"source": "sensor.hp_w"}, "supply_temp": {"source": "sensor.hp_t"}}
    assert ("heat-pump.supply_temp", 35.5) in fwd.pushed                      # its current state is sent at once
    fwd.pushed.clear()
    hass.states.async_set("sensor.hp_t", "36.0", {"unit_of_measurement": "°C"})
    await hass.async_block_till_done()
    assert fwd.pushed == [("heat-pump.supply_temp", 36.0)]


async def test_adding_the_same_name_twice_gives_unique_ids(hass, energy_prefs, fake_forwarder):
    entry = await setup_entry(hass)
    await add_heatpump(hass, entry, **{"Power": "sensor.hp1"})
    await add_heatpump(hass, entry, **{"Power": "sensor.hp2"})
    assert [d["id"] for d in entry.options[CONF_DEVICES]] == ["heat-pump", "heat-pump-2"]
    await hass.async_block_till_done()
    assert {"heat-pump", "heat-pump-2"} <= ids(fake_forwarder.instances[0])


async def test_the_device_class_is_checked_when_the_entity_has_a_state(hass, energy_prefs, fake_forwarder):
    entry = await setup_entry(hass)
    hass.states.async_set("sensor.an_energy_meter", "5", {"device_class": "energy"})
    result = await add_heatpump(hass, entry, **{"Power": "sensor.an_energy_meter"})   # an energy sensor in a power slot
    assert result["type"] is FlowResultType.FORM and result["errors"] == {"Power": "wrong_device_class"}
    hass.states.async_set("sensor.a_power_meter", "5", {"device_class": "power"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"Power": "sensor.a_power_meter"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    # an entity without a state yet cannot be checked, so it is accepted
    result = await add_heatpump(hass, entry, name="Second", **{"Power": "sensor.not_there_yet"})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_a_sensor_form_needs_at_least_one_sensor(hass, energy_prefs, fake_forwarder):
    entry = await setup_entry(hass)
    result = await add_heatpump(hass, entry)
    assert result["type"] is FlowResultType.FORM and result["errors"] == {"base": "no_sensors"}


async def test_the_device_needs_a_name(hass, energy_prefs, fake_forwarder):
    entry = await setup_entry(hass)
    result = await menu(hass, entry, "add_device")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"device": "heatpump", "name": "  "})
    assert result["type"] is FlowResultType.FORM and result["errors"] == {"name": "name_required"}


async def test_price_slots_take_an_entity_or_a_fixed_number_but_not_both(hass, energy_prefs, fake_forwarder):
    entry = await setup_entry(hass)
    result = await menu(hass, entry, "add_device")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"device": "electricitymeter", "name": "Second meter"})
    assert "Import Price" in fields(result) and "Import Price (fixed value)" in fields(result)
    assert "Power Import (fixed value)" not in fields(result)                    # only price slots take a number
    result = await hass.config_entries.options.async_configure(result["flow_id"], {
        "Energy Import": "sensor.imp2", "Import Price": "sensor.tariff", "Import Price (fixed value)": 0.2})
    assert result["errors"] == {"Import Price (fixed value)": "entity_and_fixed"}
    result = await hass.config_entries.options.async_configure(result["flow_id"], {
        "Energy Import": "sensor.imp2", "Import Price (fixed value)": 0.2})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert instance(fake_forwarder.instances[0], "second-meter")["sensors"] == {
        "energy_import": {"source": "sensor.imp2"}, "price_import": {"fixed": 0.2}}


async def test_edit_a_device_you_added(hass, energy_prefs, fake_forwarder):
    entry = await setup_entry(hass, {**OPTIONS, CONF_DEVICES: [HEATPUMP]})
    fwd = fake_forwarder.instances[0]
    result = await menu(hass, entry, "edit_device")
    assert [o["value"] for o in result["data_schema"].schema["device"].config["options"]] == ["heat-pump"]
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"device": "heat-pump"})
    assert result["step_id"] == "sensors"
    suggested = {str(k): k.description["suggested_value"] for k in result["data_schema"].schema if k.description}
    assert suggested == {"Power": "sensor.hp_w", "Supply Temperature": "sensor.hp_t"}   # the current choices
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"Power": "sensor.hp_other", "COP": "sensor.hp_cop"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_DEVICES] == [{**HEATPUMP, "sensors": {
        "power": {"entity_id": "sensor.hp_other"}, "cop": {"entity_id": "sensor.hp_cop"}}}]
    assert instance(fwd, "heat-pump")["sensors"] == {
        "power": {"source": "sensor.hp_other"}, "cop": {"source": "sensor.hp_cop"}}
    assert instance(fwd, "heat-pump")["name"] == "Heat pump"                    # id and name are kept


async def test_editing_needs_a_device_you_added(hass, energy_prefs, fake_forwarder):
    entry = await setup_entry(hass)
    result = await menu(hass, entry, "edit_device")
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "no_manual_devices"


async def test_remove_discovered_and_added_devices(hass, energy_prefs, fake_forwarder):
    entry = await setup_entry(hass, {**OPTIONS, CONF_DEVICES: [HEATPUMP]})
    fwd = fake_forwarder.instances[0]
    assert "heat-pump" in ids(fwd)
    result = await menu(hass, entry, "remove_devices")
    assert set(result["data_schema"].schema["devices"].options) == ALL_DISCOVERED | {"heat-pump"}
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"devices": ["heat-pump", CAR]})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_DEVICES] == []                                   # the added one is deleted
    assert entry.options[CONF_EXCLUDED] == [CAR]                               # the discovered one is excluded
    assert ids(fwd) == ALL_DISCOVERED - {CAR}


async def test_nothing_to_remove(hass, energy_prefs, fake_forwarder):
    energy_prefs["prefs"] = None
    entry = await setup_entry(hass)
    result = await menu(hass, entry, "remove_devices")
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "no_devices"


async def test_rediscover_brings_removed_devices_back(hass, energy_prefs, fake_forwarder):
    entry = await setup_entry(hass, {**OPTIONS, CONF_EXCLUDED: [CAR, SOLAR]})
    fwd = fake_forwarder.instances[0]
    assert CAR not in ids(fwd)
    result = await menu(hass, entry, "rediscover")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert ids(fwd) == ALL_DISCOVERED and entry.options[CONF_EXCLUDED] == []


async def test_settings_change_throttle_and_auto_sync(hass, energy_prefs, fake_forwarder):
    entry = await setup_entry(hass)
    fwd = fake_forwarder.instances[0]
    result = await menu(hass, entry, "settings")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_THROTTLE: 30, CONF_AUTO_SYNC: False})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert fwd.throttle == 30
    assert ids(fwd) == set()                                   # auto sync off and nothing added by hand


async def test_adding_a_device_needs_the_server(hass, energy_prefs, fake_forwarder):
    entry = await setup_entry(hass)
    fake_forwarder.instances[0].catalogue = []                 # never connected, so no catalogue yet
    with patch("custom_components.energy_hub.config_flow.fetch_hello", side_effect=AuthError("no")):
        result = await menu(hass, entry, "add_device")
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "cannot_connect"


# ---- fixed-only slots (battery capacity) -----------------------------------------------------------------
async def open_sensors(hass, entry, device, name):
    result = await menu(hass, entry, "add_device")
    return await hass.config_entries.options.async_configure(result["flow_id"], {"device": device, "name": name})


async def test_a_fixed_only_slot_has_only_a_number_field_with_its_unit(hass, energy_prefs, fake_forwarder):
    entry = await setup_entry(hass)
    result = await open_sensors(hass, entry, "battery", "Home battery")
    assert fields(result) == {"State of Charge", "Power", "Energy Charged", "Energy Discharged",
                              "Capacity (fixed value)"}
    schema = {str(k): v for k, v in result["data_schema"].schema.items()}
    assert schema["Capacity (fixed value)"].config["unit_of_measurement"] == "kWh"
    assert schema["Capacity (fixed value)"].config["min"] == 0


async def test_price_slots_keep_both_fields(hass, energy_prefs, fake_forwarder):
    entry = await setup_entry(hass)
    result = await open_sensors(hass, entry, "electricitymeter", "Meter")
    assert {"Import Price", "Import Price (fixed value)"} <= fields(result)
    schema = {str(k): v for k, v in result["data_schema"].schema.items()}
    assert "unit_of_measurement" not in schema["Import Price (fixed value)"].config


async def test_a_catalogue_without_the_flag_behaves_as_before(hass, energy_prefs, fake_forwarder):
    from custom_components.energy_hub.config_flow import sensor_schema
    from catalogue import DEVICES
    battery = next(d for d in DEVICES if d["id"] == "battery")
    old = {**battery, "sensors": [{k: v for k, v in s.items() if k != "fixed_only"} for s in battery["sensors"]]}
    assert {str(k) for k in sensor_schema(old)} == {
        "State of Charge", "Power", "Energy Charged", "Energy Discharged", "Capacity",
        "Capacity (fixed value)"}


async def test_entering_a_capacity_stores_a_fixed_value(hass, energy_prefs, fake_forwarder):
    entry = await setup_entry(hass)
    fwd = fake_forwarder.instances[0]
    result = await open_sensors(hass, entry, "battery", "Home battery")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"Power": "sensor.bat_w", "Capacity (fixed value)": 10})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    stored = entry.options[CONF_DEVICES][0]["sensors"]
    assert stored["capacity"] == {"fixed": 10.0}
    assert instance(fwd, "home-battery")["sensors"]["capacity"] == {"fixed": 10.0}


async def test_editing_prefills_the_capacity(hass, energy_prefs, fake_forwarder):
    bat = {"id": "home-battery", "device": "battery", "name": "Home battery",
           "sensors": {"power": {"entity_id": "sensor.bat_w"}, "capacity": {"fixed": 10.0}}}
    entry = await setup_entry(hass, {**OPTIONS, CONF_DEVICES: [bat]})
    result = await menu(hass, entry, "edit_device")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"device": "home-battery"})
    assert result["step_id"] == "sensors"
    markers = {str(k): k for k in result["data_schema"].schema}
    assert markers["Capacity (fixed value)"].description == {"suggested_value": 10.0}
    assert markers["Power"].description == {"suggested_value": "sensor.bat_w"}
