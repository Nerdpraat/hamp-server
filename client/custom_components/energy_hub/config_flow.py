"""Config flow (onboarding with auto-discovery) and options flow (add, edit and remove devices)."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector, EntitySelector, EntitySelectorConfig, NumberSelector, NumberSelectorConfig,
    NumberSelectorMode, SelectSelector, SelectSelectorConfig, SelectSelectorMode, TextSelector,
)

from .const import (
    CONF_AUTO_SYNC, CONF_DEVICES, CONF_EXCLUDED, CONF_THROTTLE, CONF_TOKEN, CONF_URL, DEFAULT_THROTTLE, DOMAIN,
    MAX_THROTTLE, MIN_THROTTLE,
)
from .discovery import async_read_energy_prefs
from .mapper import InstanceDef, Slot, discover, manual_id
from .ws_client import AuthError, ProtocolError, fetch_hello, normalize_url

_LOGGER = logging.getLogger(__name__)

_CONNECT_ERRORS = (aiohttp.ClientError, asyncio.TimeoutError, OSError, ProtocolError)
FIXED_SUFFIX = " (fixed value)"


def device_label(device: dict[str, Any]) -> str:
    return f"{device['name']} ({device['category']})"


def _selector_config(sensor: dict[str, Any]) -> EntitySelectorConfig:
    flt: dict[str, Any] = {"domain": list(sensor["selector"].get("domain") or ["sensor"])}
    if sensor["selector"].get("device_class"):
        flt["device_class"] = list(sensor["selector"]["device_class"])
    return EntitySelectorConfig(filter=[flt])


def sensor_schema(device: dict[str, Any]) -> dict[vol.Marker, Any]:
    """One optional entity field per sensor slot. Dynamic fields have no translations, so the
    field *name* is the slot's name; price slots get a second field for a fixed number."""
    fields: dict[vol.Marker, Any] = {}
    for s in device["sensors"]:
        fixed_only = bool(s.get("fixed_only"))
        if not fixed_only:
            fields[vol.Optional(s["name"])] = EntitySelector(_selector_config(s))
        if s["allow_fixed"] or fixed_only:
            number: dict[str, Any] = {"min": 0, "max": 1000, "step": "any", "mode": NumberSelectorMode.BOX}
            if fixed_only and s.get("unit"):
                number["unit_of_measurement"] = s["unit"]
            fields[vol.Optional(s["name"] + FIXED_SUFFIX)] = NumberSelector(NumberSelectorConfig(**number))
    return fields


def slots_markdown(device: dict[str, Any]) -> str:
    return "\n".join(f"- **{s['name']}**: {s['description']}" for s in device["sensors"])


def device_class_ok(hass: HomeAssistant, entity_id: str, sensor: dict[str, Any]) -> bool:
    """Home Assistant checks only the domain of a chosen entity. Check the device class here,
    when the entity has a state to tell it by."""
    wanted = sensor["selector"].get("device_class") or []
    state = hass.states.get(entity_id)
    if not wanted or state is None:
        return True
    return state.attributes.get("device_class") in wanted


def slots_from_input(hass: HomeAssistant, device: dict[str, Any], user_input: dict[str, Any]
                     ) -> tuple[list[Slot], dict[str, str]]:
    """Turn a submitted sensor form into slots, or per-field errors."""
    slots: list[Slot] = []
    errors: dict[str, str] = {}
    for s in device["sensors"]:
        entity = None if s.get("fixed_only") else user_input.get(s["name"])
        fixed = user_input.get(s["name"] + FIXED_SUFFIX)
        if entity and fixed is not None:
            errors[s["name"] + FIXED_SUFFIX] = "entity_and_fixed"
        elif entity:
            if device_class_ok(hass, entity, s):
                slots.append(Slot(s["id"], entity_id=entity))
            else:
                errors[s["name"]] = "wrong_device_class"
        elif fixed is not None:
            slots.append(Slot(s["id"], fixed=float(fixed)))
    if not slots and not errors:
        errors["base"] = "no_sensors"
    return slots, errors


def suggested_values(device: dict[str, Any], instance: InstanceDef) -> dict[str, Any]:
    by_id = {s["id"]: s for s in device["sensors"]}
    out: dict[str, Any] = {}
    for slot in instance.slots:
        sensor = by_id.get(slot.sensor_id)
        if sensor is None:
            continue
        if slot.entity_id is not None:
            if not sensor.get("fixed_only"):
                out[sensor["name"]] = slot.entity_id
        else:
            out[sensor["name"] + FIXED_SUFFIX] = slot.fixed
    return out


class EnergyHubConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ask for the server URL and site key, then confirm what was discovered."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self._hello: dict[str, Any] = {}

    async def _validate(self, url: str, key: str) -> tuple[dict[str, Any] | None, dict[str, str]]:
        errors: dict[str, str] = {}
        try:
            hello = await fetch_hello(async_get_clientsession(self.hass), url, key)
        except ValueError:
            errors[CONF_URL] = "invalid_url"
        except AuthError:
            errors["base"] = "invalid_auth"
        except ProtocolError:
            errors["base"] = "unsupported_server"
        except _CONNECT_ERRORS:
            errors["base"] = "cannot_connect"
        except Exception:  # noqa: BLE001
            _LOGGER.exception("unexpected error while connecting to Energy Hub")
            errors["base"] = "unknown"
        else:
            return hello, errors
        return None, errors

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_URL].strip()
            hello, errors = await self._validate(url, user_input[CONF_TOKEN])
            if hello is not None:
                self._hello = hello
                await self.async_set_unique_id(f"{normalize_url(url)}#{hello['site']['id']}")
                self._abort_if_unique_id_configured()
                self._data = {CONF_URL: url, CONF_TOKEN: user_input[CONF_TOKEN]}
                return await self.async_step_discover()
        schema = vol.Schema({vol.Required(CONF_URL): str, vol.Required(CONF_TOKEN): str})
        return self.async_show_form(
            step_id="user", data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors)

    async def async_step_discover(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        def name_for(entity_id: str) -> str:
            state = self.hass.states.get(entity_id)
            return state.name if state is not None else entity_id

        found = discover(await async_read_energy_prefs(self.hass), name_for)
        title = self._hello["site"]["name"]
        options = {CONF_AUTO_SYNC: True, CONF_EXCLUDED: [], CONF_DEVICES: [], CONF_THROTTLE: DEFAULT_THROTTLE}
        if not found:
            # No energy dashboard yet: finish anyway, sources are picked up once it is configured.
            return self.async_create_entry(title=title, data=self._data, options=options)
        names = {d["id"]: d["name"] for d in self._hello.get("devices", [])}
        choices = {i.id: f"{i.name} ({names.get(i.device, i.device)})" for i in found}
        if user_input is not None:
            options[CONF_EXCLUDED] = [iid for iid in choices if iid not in user_input["devices"]]
            return self.async_create_entry(title=title, data=self._data, options=options)
        schema = vol.Schema({vol.Required("devices", default=list(choices)): cv.multi_select(choices)})
        return self.async_show_form(step_id="discover", data_schema=schema)

    # ---- re-authentication ----------------------------------------------
    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            hello, errors = await self._validate(entry.data[CONF_URL], user_input[CONF_TOKEN])
            if hello is not None:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_TOKEN: user_input[CONF_TOKEN]})
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=vol.Schema({vol.Required(CONF_TOKEN): str}),
            errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return EnergyHubOptionsFlow()


class EnergyHubOptionsFlow(OptionsFlow):
    """Add, edit and remove devices, re-run discovery, tune settings."""

    def __init__(self) -> None:
        self._device: dict[str, Any] | None = None     # catalogue entry being filled in
        self._name = ""
        self._editing: InstanceDef | None = None

    def _save(self, **changes: Any) -> Any:
        return self.async_create_entry(data={**self.config_entry.options, **changes})

    def _manager(self):
        return self.config_entry.runtime_data

    async def _catalogue(self) -> list[dict[str, Any]] | None:
        manager = getattr(self.config_entry, "runtime_data", None)
        if manager is not None and manager.forwarder is not None and manager.forwarder.catalogue:
            return manager.forwarder.catalogue
        try:
            hello = await fetch_hello(
                async_get_clientsession(self.hass),
                self.config_entry.data[CONF_URL], self.config_entry.data[CONF_TOKEN])
        except (AuthError, ValueError, *_CONNECT_ERRORS):
            return None
        return hello["devices"]

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> Any:
        return self.async_show_menu(
            step_id="init",
            menu_options=["add_device", "edit_device", "remove_devices", "rediscover", "settings"])

    # ---- add a device -------------------------------------------------------
    async def async_step_add_device(self, user_input: dict[str, Any] | None = None) -> Any:
        catalogue = await self._catalogue()
        if catalogue is None:
            return self.async_abort(reason="cannot_connect")
        errors: dict[str, str] = {}
        if user_input is not None:
            device = next((d for d in catalogue if d["id"] == user_input["device"]), None)
            name = user_input["name"].strip()
            if device is None:
                errors["device"] = "unknown_device"
            elif not name:
                errors["name"] = "name_required"
            else:
                self._device, self._name, self._editing = device, name, None
                return await self.async_step_sensors()
        choices = [{"value": d["id"], "label": device_label(d)}
                   for d in sorted(catalogue, key=lambda d: (d["category"], d["name"]))]
        schema = vol.Schema({
            vol.Required("device"): SelectSelector(
                SelectSelectorConfig(options=choices, mode=SelectSelectorMode.DROPDOWN)),
            vol.Required("name"): TextSelector(),
        })
        return self.async_show_form(
            step_id="add_device", data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors)

    async def async_step_sensors(self, user_input: dict[str, Any] | None = None) -> Any:
        device = self._device
        assert device is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            slots, errors = slots_from_input(self.hass, device, user_input)
            if not errors:
                if self._editing is not None:
                    inst = InstanceDef(self._editing.id, device["id"], self._editing.name, tuple(slots))
                else:
                    taken = {i.id for i in self._manager().instances}
                    taken |= {i.id for i in await self._manager().async_discovered()}
                    inst = InstanceDef(manual_id(self._name, device["id"], taken), device["id"],
                                       self._name, tuple(slots))
                keep = [d for d in self.config_entry.options.get(CONF_DEVICES, []) if d.get("id") != inst.id]
                return self._save(**{CONF_DEVICES: [*keep, inst.to_option()]})
        schema = vol.Schema(sensor_schema(device))
        if self._editing is not None and user_input is None:
            schema = self.add_suggested_values_to_schema(schema, suggested_values(device, self._editing))
        elif user_input is not None:
            schema = self.add_suggested_values_to_schema(schema, user_input)
        return self.async_show_form(
            step_id="sensors", data_schema=schema, errors=errors,
            description_placeholders={
                "device": device["name"], "name": self._editing.name if self._editing else self._name,
                "about": device["description"], "slots": slots_markdown(device)})

    # ---- edit a device you added ----------------------------------------------
    async def async_step_edit_device(self, user_input: dict[str, Any] | None = None) -> Any:
        mine = {i.id: i for i in self._manager().manual_instances()}
        if not mine:
            return self.async_abort(reason="no_manual_devices")
        catalogue = await self._catalogue()
        if catalogue is None:
            return self.async_abort(reason="cannot_connect")
        if user_input is not None:
            inst = mine.get(user_input["device"])
            device = next((d for d in catalogue if inst and d["id"] == inst.device), None)
            if inst is None or device is None:
                return self.async_abort(reason="unknown_device")
            self._device, self._editing, self._name = device, inst, inst.name
            return await self.async_step_sensors()
        choices = [{"value": i.id, "label": f"{i.name} ({i.device})"} for i in mine.values()]
        schema = vol.Schema({vol.Required("device"): SelectSelector(
            SelectSelectorConfig(options=choices, mode=SelectSelectorMode.DROPDOWN))})
        return self.async_show_form(step_id="edit_device", data_schema=schema)

    # ---- remove devices ---------------------------------------------------------
    async def async_step_remove_devices(self, user_input: dict[str, Any] | None = None) -> Any:
        manager = self._manager()
        choices = {i.id: f"{i.name} ({i.device})" for i in manager.instances}
        if not choices:
            return self.async_abort(reason="no_devices")
        if user_input is not None:
            remove = set(user_input["devices"])
            discovered = {i.id for i in await manager.async_discovered()}
            devices = [d for d in self.config_entry.options.get(CONF_DEVICES, []) if d.get("id") not in remove]
            excluded = sorted({*self.config_entry.options.get(CONF_EXCLUDED, []), *(remove & discovered)})
            return self._save(**{CONF_DEVICES: devices, CONF_EXCLUDED: excluded})
        schema = vol.Schema({vol.Required("devices", default=[]): cv.multi_select(choices)})
        return self.async_show_form(step_id="remove_devices", data_schema=schema)

    async def async_step_rediscover(self, user_input: dict[str, Any] | None = None) -> Any:
        if user_input is not None:
            return self._save(**{CONF_AUTO_SYNC: True, CONF_EXCLUDED: []})
        return self.async_show_form(step_id="rediscover", data_schema=vol.Schema({}))

    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> Any:
        opts = self.config_entry.options
        if user_input is not None:
            return self._save(**{
                CONF_THROTTLE: int(user_input[CONF_THROTTLE]),
                CONF_AUTO_SYNC: user_input[CONF_AUTO_SYNC],
            })
        schema = vol.Schema({
            vol.Required(CONF_THROTTLE, default=opts.get(CONF_THROTTLE, DEFAULT_THROTTLE)): NumberSelector(
                NumberSelectorConfig(min=MIN_THROTTLE, max=MAX_THROTTLE, step=1, unit_of_measurement="s")),
            vol.Required(CONF_AUTO_SYNC, default=opts.get(CONF_AUTO_SYNC, True)): BooleanSelector(),
        })
        return self.async_show_form(step_id="settings", data_schema=schema)
