"""P2000 Monitor integration."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_NAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_TEST_CAPCODES,
    ATTR_TEST_DISCIPLINE,
    ATTR_TEST_MESSAGE,
    ATTR_TEST_REGION,
    ATTR_TEST_REGION_NAME,
    CONF_ALL_SERVICES,
    CONF_CAPCODES,
    CONF_DISCIPLINES,
    CONF_EXCLUDE_TEXT,
    CONF_INCIDENT_WINDOW,
    CONF_INCLUDE_TEXT,
    CONF_PRIORITIES,
    CONF_REGIONS,
    DATA_FEED,
    DEFAULT_INCIDENT_WINDOW,
    DOMAIN,
    EVENT_NEW_MESSAGE,
    SERVICE_INJECT_TEST_INCIDENT,
    STORAGE_VERSION,
)
from .coordinator import REGIONS, P2000FeedCoordinator, P2000FilterProcessor, storage_key

PLATFORMS = [Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

TEST_INCIDENT_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_TEST_REGION, default="21"): cv.string,
        vol.Optional(ATTR_TEST_REGION_NAME): cv.string,
        vol.Optional(ATTR_TEST_DISCIPLINE, default="Brandweerdiensten"): cv.string,
        vol.Optional(ATTR_TEST_MESSAGE, default="P 1 BR woning Teststraat Maastricht 243231"): cv.string,
        vol.Optional(ATTR_TEST_CAPCODES, default=["1005258", "1005264", "1005998"]): vol.All(
            cv.ensure_list, [cv.string]
        ),
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration-wide services."""

    async def handle_test_incident(call: ServiceCall) -> None:
        region = str(call.data[ATTR_TEST_REGION])
        discipline = call.data[ATTR_TEST_DISCIPLINE]
        now = dt_util.utcnow().isoformat()
        messages = [
            {
                "message": call.data[ATTR_TEST_MESSAGE],
                "capcode": str(capcode),
                "capcodes": [str(capcode)],
                "regio": region,
                "regio_name": call.data.get(ATTR_TEST_REGION_NAME, REGIONS.get(region, "")),
                "discipline": discipline,
                "dienstid": "2" if discipline == "Brandweerdiensten" else "",
                "latitude": "50.8514",
                "longitude": "5.6909",
                "published": f"TEST-PENDING-{now}-{index}",
                "test": True,
            }
            for index, capcode in enumerate(call.data[ATTR_TEST_CAPCODES])
        ]
        for message in messages:
            hass.bus.async_fire(EVENT_NEW_MESSAGE, {"message": message})
        for entry in hass.config_entries.async_entries(DOMAIN):
            if entry.state is ConfigEntryState.LOADED:
                await entry.runtime_data.async_inject_test_messages(messages)

    hass.services.async_register(
        DOMAIN, SERVICE_INJECT_TEST_INCIDENT, handle_test_incident, schema=TEST_INCIDENT_SCHEMA
    )
    return True


async def _async_get_feed(hass: HomeAssistant) -> P2000FeedCoordinator:
    """Return the feed coordinator shared by all entries, fetching it once."""
    feed: P2000FeedCoordinator | None = hass.data.get(DATA_FEED)
    if feed is None:
        feed = hass.data[DATA_FEED] = P2000FeedCoordinator(hass)
    async with feed.setup_lock:
        if feed.data is None:
            await feed.async_refresh()
            if not feed.last_update_success:
                raise ConfigEntryNotReady("AlarmeringDroid API is niet bereikbaar")
    return feed


def entry_settings(entry: ConfigEntry) -> dict[str, Any]:
    """Return the effective filter settings of an entry."""
    data = dict(entry.data)
    name = data.get(CONF_NAME, entry.title)
    all_services = bool(data.get(CONF_ALL_SERVICES, False))
    if CONF_ALL_SERVICES not in data and name == "P2000 Monitor":
        all_services = True
    return {
        CONF_NAME: name,
        CONF_ALL_SERVICES: all_services,
        CONF_REGIONS: data.get(CONF_REGIONS, []),
        CONF_DISCIPLINES: data.get(CONF_DISCIPLINES, []),
        CONF_PRIORITIES: data.get(CONF_PRIORITIES, []),
        CONF_CAPCODES: data.get(CONF_CAPCODES, []),
        CONF_INCLUDE_TEXT: data.get(CONF_INCLUDE_TEXT, []),
        CONF_EXCLUDE_TEXT: data.get(CONF_EXCLUDE_TEXT, []),
        CONF_INCIDENT_WINDOW: int(data.get(CONF_INCIDENT_WINDOW, DEFAULT_INCIDENT_WINDOW)),
    }


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up P2000 Monitor from a UI config entry."""
    feed = await _async_get_feed(hass)
    settings = entry_settings(entry)
    api_filter = {} if settings[CONF_ALL_SERVICES] else {
        "regios": settings[CONF_REGIONS],
        "diensten": settings[CONF_DISCIPLINES],
        "capcodes": settings[CONF_CAPCODES],
        "priorities": settings[CONF_PRIORITIES],
        "include_text": settings[CONF_INCLUDE_TEXT],
        "exclude_text": settings[CONF_EXCLUDE_TEXT],
    }
    processor = P2000FilterProcessor(
        hass,
        feed,
        entry.entry_id,
        incident_window=settings[CONF_INCIDENT_WINDOW],
        api_filter=api_filter,
    )
    await processor.async_start()
    entry.runtime_data = processor
    entry.async_on_unload(processor.async_stop)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry after its UI configuration changes."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a P2000 Monitor config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded and not any(
        other.state is ConfigEntryState.LOADED
        for other in hass.config_entries.async_entries(DOMAIN)
        if other.entry_id != entry.entry_id
    ):
        # Last entry gone: drop the shared feed so a later setup starts clean.
        feed: P2000FeedCoordinator | None = hass.data.pop(DATA_FEED, None)
        if feed is not None:
            await feed.async_shutdown()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the stored incident history of a removed entry."""
    await Store(hass, STORAGE_VERSION, storage_key(entry.entry_id)).async_remove()
