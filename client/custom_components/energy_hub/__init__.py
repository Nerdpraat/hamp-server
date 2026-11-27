"""Energy Hub: push the Home Assistant energy dashboard data to a central server."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .manager import EnergyHubManager

type EnergyHubConfigEntry = ConfigEntry[EnergyHubManager]


async def async_setup_entry(hass: HomeAssistant, entry: EnergyHubConfigEntry) -> bool:
    manager = EnergyHubManager(hass, entry)
    await manager.async_start()
    entry.runtime_data = manager
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EnergyHubConfigEntry) -> bool:
    await entry.runtime_data.async_stop()
    return True


async def _async_options_updated(hass: HomeAssistant, entry: EnergyHubConfigEntry) -> None:
    """Apply changed options live: no reload and no HA restart."""
    await entry.runtime_data.async_refresh_instances()
