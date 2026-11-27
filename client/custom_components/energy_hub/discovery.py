"""Read the Home Assistant energy dashboard configuration."""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


async def async_read_energy_prefs(hass: HomeAssistant) -> dict[str, Any] | None:
    """Return the energy preferences (same data as the `energy/get_prefs` command).

    Returns None when the energy integration is not loaded or not configured.
    """
    if "energy" not in hass.config.components:
        return None
    try:
        from homeassistant.components.energy.data import async_get_manager
    except ImportError:  # pragma: no cover - energy integration unavailable
        return None
    manager = await async_get_manager(hass)
    return manager.data


async def async_listen_energy_updates(
    hass: HomeAssistant, listener: Callable[[], Awaitable[None]]
) -> Callable[[], None]:
    """Call `listener` whenever the energy dashboard configuration changes.

    Returns a function that removes the listener again.
    """
    if "energy" not in hass.config.components:
        return lambda: None
    try:
        from homeassistant.components.energy.data import async_get_manager
    except ImportError:  # pragma: no cover
        return lambda: None
    manager = await async_get_manager(hass)
    manager.async_listen_updates(listener)

    def remove() -> None:
        try:
            manager._update_listeners.remove(listener)  # noqa: SLF001 - no public API to unsubscribe
        except (AttributeError, ValueError):
            pass

    return remove
