"""Event entities: one 'alarm' event per alert for a watched capcode or vehicle."""
from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import EVENT_TYPE_ALARM, SUBENTRY_WATCH
from .entity import P2000WatchEntity, alarm_summary


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    for subentry_id, processor in entry.runtime_data.items():
        subentry = entry.subentries[subentry_id]
        if subentry.subentry_type == SUBENTRY_WATCH:
            async_add_entities([P2000AlarmEvent(processor, subentry)], config_subentry_id=subentry_id)


class P2000AlarmEvent(P2000WatchEntity, EventEntity):
    """Fires 'alarm' for every new alert that names the watched units.

    In the automation editor: trigger "Entity → state" on this entity, or use
    the blueprint. Event data holds the alert (text, priority, vehicles, ...).
    """

    _attr_event_types = [EVENT_TYPE_ALARM]
    _attr_icon = "mdi:alarm-light"

    def __init__(self, processor, subentry) -> None:
        super().__init__(processor, subentry, "alarm")
        self._last_data = processor.data

    @callback
    def _handle_processor_update(self) -> None:
        # The processor also notifies without new data (e.g. vehicle names
        # arrived); only a new snapshot can hold new alerts.
        data = self._processor.data
        if data is self._last_data:
            return
        self._last_data = data
        new_messages = data.get("new_messages") or []
        if not new_messages:
            return
        for message in reversed(new_messages):  # oldest first
            self._trigger_event(EVENT_TYPE_ALARM, alarm_summary(message))
        self.async_write_ha_state()
