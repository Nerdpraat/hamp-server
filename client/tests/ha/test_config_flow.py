from unittest.mock import patch

import aiohttp
import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.energy_hub.const import (
    CONF_AUTO_SYNC, CONF_DEVICES, CONF_EXCLUDED, CONF_THROTTLE, CONF_TOKEN, CONF_URL, DOMAIN,
)
from custom_components.energy_hub.ws_client import AuthError, ProtocolError

from conftest import ALL_DISCOVERED, BAT1, CAR, METER, SOLAR, hello

USER = {CONF_URL: "https://hub.example.com", CONF_TOKEN: "key-key-key-key-1"}


def patch_hello(**kw):
    kw.setdefault("return_value", hello())
    return patch("custom_components.energy_hub.config_flow.fetch_hello", **kw)


async def start(hass):
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})


async def test_onboarding_discovers_instances_and_lets_the_user_untick(hass, energy_prefs, fake_forwarder):
    result = await start(hass)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    with patch_hello():
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER)
    assert result["step_id"] == "discover"
    offered = result["data_schema"].schema["devices"].options
    assert set(offered) == ALL_DISCOVERED
    assert offered[METER] == "Electricity Meter (Electricity Meter)"
    assert offered[BAT1] == "Battery 1 (Battery)"
    assert offered[CAR] == "Autolader (consumer_device)"          # unknown to the catalogue: shows the id

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"devices": [METER, SOLAR]})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Home" and result["data"] == USER
    options = result["options"]
    assert options[CONF_AUTO_SYNC] is True and options[CONF_DEVICES] == [] and options[CONF_THROTTLE] == 5
    assert set(options[CONF_EXCLUDED]) == ALL_DISCOVERED - {METER, SOLAR}


async def test_onboarding_without_an_energy_dashboard_creates_the_entry(hass, energy_prefs, fake_forwarder):
    energy_prefs["prefs"] = None
    result = await start(hass)
    with patch_hello():
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"][CONF_EXCLUDED] == [] and result["options"][CONF_DEVICES] == []


@pytest.mark.parametrize("exc,field,error", [
    (AuthError("bad"), "base", "invalid_auth"),
    (aiohttp.ClientConnectionError(), "base", "cannot_connect"),
    (TimeoutError(), "base", "cannot_connect"),
    (ProtocolError("unsupported protocol version 3"), "base", "unsupported_server"),
    (ValueError("url"), CONF_URL, "invalid_url"),
    (RuntimeError("boom"), "base", "unknown"),
])
async def test_user_step_errors_and_recovery(hass, energy_prefs, fake_forwarder, exc, field, error):
    result = await start(hass)
    with patch_hello(side_effect=exc):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER)
    assert result["type"] is FlowResultType.FORM and result["errors"] == {field: error}
    with patch_hello():                                          # the same form can be retried
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER)
    assert result["step_id"] == "discover"


async def test_the_same_site_cannot_be_added_twice(hass, energy_prefs, fake_forwarder):
    MockConfigEntry(domain=DOMAIN, data=USER, unique_id="wss://hub.example.com/ws/ingest#home").add_to_hass(hass)
    result = await start(hass)
    with patch_hello():
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER)
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "already_configured"


async def test_reauth_updates_the_key(hass, energy_prefs, fake_forwarder):
    entry = MockConfigEntry(domain=DOMAIN, data=USER, options={}, unique_id="x#home")
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    with patch_hello(side_effect=AuthError("bad")):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_TOKEN: "nope"})
    assert result["errors"] == {"base": "invalid_auth"}
    with patch_hello():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_TOKEN: "fresh-key-0123456789"})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reauth_successful"
    assert entry.data[CONF_TOKEN] == "fresh-key-0123456789"
    assert entry.data[CONF_URL] == USER[CONF_URL]
