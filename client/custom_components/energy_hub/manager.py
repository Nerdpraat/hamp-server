"""Glue between Home Assistant and the websocket forwarder."""
from __future__ import annotations

import logging
import math
from datetime import timedelta
from typing import Any, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval

from .const import (
    CONF_AUTO_SYNC, CONF_DEVICES, CONF_EXCLUDED, CONF_THROTTLE, CONF_TOKEN, CONF_URL, DEFAULT_THROTTLE,
)
from .discovery import async_listen_energy_updates, async_read_energy_prefs
from .mapper import (
    Binding, InstanceDef, apply_transform, bindings, convert, discover, effective_instances,
    instance_from_option,
)
from .ws_client import Forwarder

_LOGGER = logging.getLogger(__name__)

REFRESH_INTERVAL = timedelta(seconds=55)


class EnergyHubManager:
    """Tracks the entities behind the device instances and forwards their values."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.instances: list[InstanceDef] = []
        self.forwarder: Forwarder | None = None
        self._by_entity: dict[str, list[Binding]] = {}
        self._device_of: dict[str, str] = {}              # instance id -> catalogue device id
        self._unsubs: list[Callable[[], None]] = []
        self._unsub_states: Callable[[], None] | None = None
        self._stopped = False

    # ---- lifecycle ----------------------------------------------------
    async def async_start(self) -> None:
        opts = self.entry.options
        self.forwarder = Forwarder(
            async_get_clientsession(self.hass),
            self.entry.data[CONF_URL],
            self.entry.data[CONF_TOKEN],
            [],
            throttle=opts.get(CONF_THROTTLE, DEFAULT_THROTTLE),
            currency=self.hass.config.currency or "",
            on_connected=self._on_connected,
            on_auth_failed=self._on_auth_failed,
        )
        await self.async_refresh_instances()
        self.forwarder.start()
        self._unsubs.append(async_track_time_interval(self.hass, self._periodic_push, REFRESH_INTERVAL))
        self._unsubs.append(await async_listen_energy_updates(self.hass, self._on_energy_changed))

    async def async_stop(self) -> None:
        self._stopped = True
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._unsub_states is not None:
            self._unsub_states()
            self._unsub_states = None
        if self.forwarder is not None:
            await self.forwarder.stop()

    # ---- instance selection -------------------------------------------
    def _name_for(self, entity_id: str) -> str:
        state = self.hass.states.get(entity_id)
        return state.name if state is not None else entity_id

    async def async_discovered(self) -> list[InstanceDef]:
        return discover(await async_read_energy_prefs(self.hass), self._name_for)

    def manual_instances(self) -> list[InstanceDef]:
        """Instances the user added. Stored options in any other shape are ignored."""
        found = (instance_from_option(d) for d in self.entry.options.get(CONF_DEVICES, []))
        return [i for i in found if i is not None]

    async def async_compute_instances(self) -> list[InstanceDef]:
        opts = self.entry.options
        discovered = await self.async_discovered() if opts.get(CONF_AUTO_SYNC, True) else []
        return effective_instances(discovered, self.manual_instances(), opts.get(CONF_EXCLUDED, []))

    async def async_refresh_instances(self) -> None:
        """Recompute the instances, re-subscribe, and tell the server (no reload needed)."""
        assert self.forwarder is not None
        self.instances = await self.async_compute_instances()
        self._device_of = {i.id: i.device for i in self.instances}
        self._by_entity = {}
        for b in bindings(self.instances):
            self._by_entity.setdefault(b.entity_id, []).append(b)
        if self._unsub_states is not None:
            self._unsub_states()
            self._unsub_states = None
        if self._by_entity:
            self._unsub_states = async_track_state_change_event(
                self.hass, list(self._by_entity), self._handle_state_event)
        self.forwarder.throttle = max(0.05, float(self.entry.options.get(CONF_THROTTLE, DEFAULT_THROTTLE)))
        await self.forwarder.update_instances([i.to_message() for i in self.instances])
        self._push_all()

    async def _on_energy_changed(self) -> None:
        if self._stopped or not self.entry.options.get(CONF_AUTO_SYNC, True):
            return
        _LOGGER.debug("energy dashboard changed; refreshing the device list")
        await self.async_refresh_instances()

    # ---- values -------------------------------------------------------
    @callback
    def _handle_state_event(self, event: Event[EventStateChangedData]) -> None:
        new = event.data["new_state"]
        if new is not None:
            self._push_state(new)

    def _push_state(self, state: State) -> None:
        bound = self._by_entity.get(state.entity_id)
        if not bound or self.forwarder is None:
            return
        if state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE, ""):
            return
        try:
            value = float(state.state)
        except ValueError:
            return
        if not math.isfinite(value):
            return
        unit: Any = state.attributes.get("unit_of_measurement")
        for b in bound:
            target = self.forwarder.unit_for(self._device_of.get(b.instance, ""), b.sensor_id)
            self.forwarder.push(b.key, apply_transform(convert(value, unit, target), b.transform))

    @callback
    def _push_all(self) -> None:
        for entity_id in self._by_entity:
            state = self.hass.states.get(entity_id)
            if state is not None:
                self._push_state(state)

    @callback
    def _periodic_push(self, _now: Any) -> None:
        """HA only fires events on change; re-push so unchanged values keep charts gap-free."""
        self._push_all()

    async def _on_connected(self) -> None:
        self._push_all()

    @callback
    def _on_auth_failed(self) -> None:
        self.entry.async_start_reauth(self.hass)
