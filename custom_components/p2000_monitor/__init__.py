"""P2000 Monitor integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

PLATFORMS = ["sensor"]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the P2000 Monitor integration."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up P2000 Monitor from a UI config entry."""
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry after its UI configuration changes."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a P2000 Monitor config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
