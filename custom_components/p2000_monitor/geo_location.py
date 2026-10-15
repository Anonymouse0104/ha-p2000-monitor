"""Map entities for recent alerts of all P2000 sensors.

Use a map card with `geo_location_sources: [p2000_monitor]` to show them.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.components.geo_location import GeolocationEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfLength
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SUBENTRY_SENSOR
from .coordinator import P2000FilterProcessor, parse_published
from .enrich import to_float
from .entity import alarm_summary

MAP_WINDOW = timedelta(minutes=60)
ICONS = {
    "Brandweerdiensten": "mdi:fire-truck",
    "Ambulancediensten": "mdi:ambulance",
    "Politiediensten": "mdi:police-badge",
    "KNRM": "mdi:lifebuoy",
    "Lifeliner": "mdi:helicopter",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    processors = [
        processor
        for subentry_id, processor in entry.runtime_data.items()
        if entry.subentries[subentry_id].subentry_type == SUBENTRY_SENSOR
    ]
    manager = P2000MapManager(hass, processors, async_add_entities)
    manager.async_update()
    for processor in processors:
        entry.async_on_unload(processor.async_add_listener(manager.async_update))
    entry.async_on_unload(manager.async_remove_all)


class P2000MapManager:
    """Keeps one map entity per recent alert with coordinates."""

    def __init__(self, hass: HomeAssistant, processors: list[P2000FilterProcessor], add_entities) -> None:
        self.hass = hass
        self._processors = processors
        self._add_entities = add_entities
        self._entities: dict[str, P2000AlertLocation] = {}

    @callback
    def async_update(self) -> None:
        cutoff = dt_util.utcnow() - MAP_WINDOW
        current: dict[str, dict[str, Any]] = {}
        for processor in self._processors:
            for message in processor.data.get("messages") or []:
                published = parse_published(message.get("published"))
                if published is None or published < cutoff:
                    continue
                if to_float(message.get("latitude")) is None or to_float(message.get("longitude")) is None:
                    continue
                current.setdefault(message["message_id"], message)
        for message_id in list(self._entities):
            if message_id not in current:
                self.hass.async_create_task(self._entities.pop(message_id).async_remove())
        new = [P2000AlertLocation(m) for mid, m in current.items() if mid not in self._entities]
        for entity in new:
            self._entities[entity.message_id] = entity
        if new:
            self._add_entities(new)

    @callback
    def async_remove_all(self) -> None:
        # Unloading the platform removes the entities itself.
        self._entities.clear()


class P2000AlertLocation(GeolocationEvent):
    """One recent alert on the map."""

    _attr_should_poll = False
    _attr_source = DOMAIN
    _attr_unit_of_measurement = UnitOfLength.KILOMETERS

    def __init__(self, message: dict[str, Any]) -> None:
        self.message_id = message["message_id"]
        self._attr_name = (message.get("message") or message.get("pager_text") or "P2000")[:80]
        self._attr_latitude = to_float(message.get("latitude"))
        self._attr_longitude = to_float(message.get("longitude"))
        self._attr_distance = message.get("distance_km")
        self._attr_icon = ICONS.get(message.get("discipline"), "mdi:alarm-light")
        attributes = alarm_summary(message)
        # GeolocationEvent provides numeric latitude/longitude itself.
        attributes.pop("latitude", None)
        attributes.pop("longitude", None)
        self._attr_extra_state_attributes = attributes
