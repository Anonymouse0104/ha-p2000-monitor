"""P2000 Monitor integration.

One config entry ("P2000 Monitor") holds any number of sensor subentries, each
with its own region/service/priority/capcode/text filters. All sensors share a
single poll of the AlarmeringDroid feed.
"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
import logging
from pathlib import Path
from types import MappingProxyType
from typing import Any

import voluptuous as vol

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry, ConfigEntryState, ConfigSubentry
from homeassistant.loader import async_get_integration
from homeassistant.const import CONF_NAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er
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
    CONF_BRANDBASE,
    CONF_CAPCODES,
    CONF_DISCIPLINES,
    CONF_DISPLAY,
    CONF_ENTITY_KEY,
    CONF_RADIUS,
    CONF_VEHICLES,
    DISPLAY_TEXT,
    CONF_EXCLUDE_TEXT,
    CONF_INCIDENT_WINDOW,
    CONF_INCLUDE_TEXT,
    CONF_PRIORITIES,
    CONF_REGIONS,
    CONF_STORAGE_ID,
    DATA_FEED,
    DATA_MIGRATION_LOCK,
    DEFAULT_INCIDENT_WINDOW,
    DOMAIN,
    EVENT_NEW_MESSAGE,
    HUB_TITLE,
    SERVICE_INJECT_TEST_INCIDENT,
    STORAGE_VERSION,
    SUBENTRY_SENSOR,
    SUBENTRY_WATCH,
)
from .brandbase import BrandbaseVehicles
from .coordinator import REGIONS, P2000FeedCoordinator, P2000FilterProcessor, storage_key

_LOGGER = logging.getLogger(__name__)

CARD_URL = f"/{DOMAIN}/p2000-live-card.js"
PLATFORMS = [Platform.BINARY_SENSOR, Platform.EVENT, Platform.GEO_LOCATION, Platform.SENSOR]
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

type P2000ConfigEntry = ConfigEntry[dict[str, P2000FilterProcessor]]


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
                for processor in entry.runtime_data.values():
                    await processor.async_inject_test_messages(messages)

    hass.services.async_register(
        DOMAIN, SERVICE_INJECT_TEST_INCIDENT, handle_test_incident, schema=TEST_INCIDENT_SCHEMA
    )
    await _async_register_card(hass)
    return True


async def _async_register_card(hass: HomeAssistant) -> None:
    """Serve the P2000 live card and load it in the frontend, no manual resource needed."""
    if hass.http is None:
        return
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(Path(__file__).parent / "frontend" / "p2000-live-card.js"), True)]
    )
    if "frontend" in hass.config.components:
        version = (await async_get_integration(hass, DOMAIN)).version
        # The version query busts the browser cache after an update.
        add_extra_js_url(hass, f"{CARD_URL}?v={version}")


async def _async_get_feed(hass: HomeAssistant) -> P2000FeedCoordinator:
    """Return the shared feed coordinator, fetching it once."""
    feed: P2000FeedCoordinator | None = hass.data.get(DATA_FEED)
    if feed is None:
        feed = hass.data[DATA_FEED] = P2000FeedCoordinator(hass)
    async with feed.setup_lock:
        if feed.data is None:
            await feed.async_refresh()
            if not feed.last_update_success:
                raise ConfigEntryNotReady("AlarmeringDroid API is niet bereikbaar")
    return feed


def sensor_settings(data: Mapping[str, Any], title: str) -> dict[str, Any]:
    """Return the effective filter settings of a sensor subentry."""
    name = data.get(CONF_NAME, title)
    all_services = bool(data.get(CONF_ALL_SERVICES, False))
    if CONF_ALL_SERVICES not in data and name == HUB_TITLE:
        all_services = True
    return {
        CONF_NAME: name,
        CONF_ALL_SERVICES: all_services,
        CONF_REGIONS: data.get(CONF_REGIONS, []),
        CONF_DISCIPLINES: data.get(CONF_DISCIPLINES, []),
        CONF_PRIORITIES: data.get(CONF_PRIORITIES, []),
        CONF_CAPCODES: data.get(CONF_CAPCODES, []),
        CONF_VEHICLES: data.get(CONF_VEHICLES, []),
        CONF_INCLUDE_TEXT: data.get(CONF_INCLUDE_TEXT, []),
        CONF_EXCLUDE_TEXT: data.get(CONF_EXCLUDE_TEXT, []),
        CONF_RADIUS: float(data.get(CONF_RADIUS) or 0),
        CONF_DISPLAY: data.get(CONF_DISPLAY, DISPLAY_TEXT),
        CONF_INCIDENT_WINDOW: int(data.get(CONF_INCIDENT_WINDOW, DEFAULT_INCIDENT_WINDOW)),
    }


def _api_filter(settings: dict[str, Any]) -> dict[str, Any]:
    # 'Toon alle meldingen' ignores region/service/priority/capcode/vehicle
    # filters, as the UI describes; the text and radius filters keep applying.
    api_filter = {
        "include_text": settings[CONF_INCLUDE_TEXT],
        "exclude_text": settings[CONF_EXCLUDE_TEXT],
        "radius_km": settings[CONF_RADIUS],
    }
    if not settings[CONF_ALL_SERVICES]:
        api_filter |= {
            "regios": settings[CONF_REGIONS],
            "diensten": settings[CONF_DISCIPLINES],
            "capcodes": settings[CONF_CAPCODES],
            "vehicles": settings[CONF_VEHICLES],
            "priorities": settings[CONF_PRIORITIES],
        }
    return api_filter


def subentry_storage_id(subentry: ConfigSubentry) -> str:
    """Storage id of a sensor; migrated sensors keep their old entry's history."""
    return subentry.data.get(CONF_STORAGE_ID, subentry.subentry_id)


async def async_setup_entry(hass: HomeAssistant, entry: P2000ConfigEntry) -> bool:
    """Set up P2000 Monitor and one filter processor per sensor subentry."""
    if entry.version < 2:
        # Merged into the P2000 Monitor entry by async_migrate_entry; drop it.
        hass.async_create_task(hass.config_entries.async_remove(entry.entry_id))
        return True

    feed = await _async_get_feed(hass)
    vehicle_db: BrandbaseVehicles | None = None
    if entry.options.get(CONF_BRANDBASE, True):
        vehicle_db = BrandbaseVehicles(hass)
        await vehicle_db.async_load()
        entry.async_on_unload(vehicle_db.async_stop)
    processors: dict[str, P2000FilterProcessor] = {}
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type == SUBENTRY_SENSOR:
            settings = sensor_settings(subentry.data, subentry.title)
            processor = P2000FilterProcessor(
                hass,
                feed,
                subentry_storage_id(subentry),
                entry_id=entry.entry_id,
                subentry_id=subentry_id,
                incident_window=settings[CONF_INCIDENT_WINDOW],
                api_filter=_api_filter(settings),
                vehicle_db=vehicle_db,
            )
        elif subentry.subentry_type == SUBENTRY_WATCH:
            processor = P2000FilterProcessor(
                hass,
                feed,
                subentry_storage_id(subentry),
                entry_id=entry.entry_id,
                subentry_id=subentry_id,
                api_filter={
                    "capcodes": subentry.data.get(CONF_CAPCODES, []),
                    "vehicles": subentry.data.get(CONF_VEHICLES, []),
                    "regios": subentry.data.get(CONF_REGIONS, []),
                },
                match_any_unit=True,
                vehicle_db=vehicle_db,
            )
        else:
            continue
        await processor.async_start()
        entry.async_on_unload(processor.async_stop)
        processors[subentry_id] = processor
    entry.runtime_data = processors
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: P2000ConfigEntry) -> None:
    """Reload after a sensor was added, changed or removed."""
    removed = {
        processor.storage_id
        for subentry_id, processor in entry.runtime_data.items()
        if subentry_id not in entry.subentries
    }
    await hass.config_entries.async_reload(entry.entry_id)
    for storage_id in removed:
        await Store(hass, STORAGE_VERSION, storage_key(storage_id)).async_remove()


async def async_unload_entry(hass: HomeAssistant, entry: P2000ConfigEntry) -> bool:
    """Unload P2000 Monitor."""
    if entry.version < 2:
        return True
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded and not any(
        other.state is ConfigEntryState.LOADED
        for other in hass.config_entries.async_entries(DOMAIN)
        if other.entry_id != entry.entry_id
    ):
        # Nothing uses the feed any more: drop it so a later setup starts clean.
        feed: P2000FeedCoordinator | None = hass.data.pop(DATA_FEED, None)
        if feed is not None:
            await feed.async_shutdown()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the stored history of every sensor of a removed entry."""
    if entry.version < 2:
        # A merged v1 entry: its history now belongs to a subentry of the hub.
        return
    for subentry in entry.subentries.values():
        await Store(hass, STORAGE_VERSION, storage_key(subentry_storage_id(subentry))).async_remove()


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Merge the old one-sensor-per-entry setup into a single entry with subentries.

    Version 1 entries each held one sensor. The first one to migrate becomes the
    P2000 Monitor entry; every version 1 entry (itself included) becomes a sensor
    subentry of it, keeping its entity (same unique_id, so the same entity_id
    and history) and its stored message history. The other entries are removed
    on setup.
    """
    if entry.version >= 2:
        return True
    lock: asyncio.Lock = hass.data.setdefault(DATA_MIGRATION_LOCK, asyncio.Lock())
    async with lock:
        hub = next(
            (e for e in hass.config_entries.async_entries(DOMAIN) if e.version >= 2),
            None,
        )
        sensor = ConfigSubentry(
            data=MappingProxyType(
                {
                    **dict(entry.data),
                    CONF_ENTITY_KEY: entry.unique_id or entry.entry_id,
                    CONF_STORAGE_ID: entry.entry_id,
                }
            ),
            subentry_type=SUBENTRY_SENSOR,
            title=str(entry.data.get(CONF_NAME, entry.title)),
            unique_id=None,
        )
        if hub is None:
            hub = entry
            hass.config_entries.async_update_entry(
                entry, version=2, title=HUB_TITLE, unique_id=DOMAIN, data={}, options={}
            )
        hass.config_entries.async_add_subentry(hub, sensor)

        ent_reg = er.async_get(hass)
        for reg_entry in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
            ent_reg.async_update_entity(
                reg_entry.entity_id,
                config_entry_id=hub.entry_id,
                config_subentry_id=sensor.subentry_id,
            )
        _LOGGER.info("P2000 sensor '%s' merged into %s", sensor.title, HUB_TITLE)
        # A loaded hub reloads through its update listener; one still being set up
        # may already have read its subentries, so reload it once setup finishes.
        if hub is not entry and hub.state is not ConfigEntryState.LOADED:
            hass.config_entries.async_schedule_reload(hub.entry_id)
    return True
