"""'Active' binary sensors: on for a while after a watched unit is alerted."""
from __future__ import annotations

from datetime import timedelta

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .const import CONF_ACTIVE_MINUTES, DEFAULT_ACTIVE_MINUTES, SUBENTRY_WATCH
from .entity import P2000WatchEntity, alarm_summary


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    for subentry_id, processor in entry.runtime_data.items():
        subentry = entry.subentries[subentry_id]
        if subentry.subentry_type == SUBENTRY_WATCH:
            async_add_entities([P2000ActiveSensor(processor, subentry)], config_subentry_id=subentry_id)


class P2000ActiveSensor(P2000WatchEntity, BinarySensorEntity):
    """On while the last alert for the watched units is recent."""

    _attr_icon = "mdi:fire-alert"

    def __init__(self, processor, subentry) -> None:
        super().__init__(processor, subentry, "active")
        self._window = timedelta(minutes=int(subentry.data.get(CONF_ACTIVE_MINUTES, DEFAULT_ACTIVE_MINUTES)))
        self._cancel_expiry = None

    @property
    def is_on(self) -> bool:
        last = self._processor.last_match_time
        return last is not None and dt_util.utcnow() - last < self._window

    @property
    def extra_state_attributes(self):
        messages = self._processor.data.get("messages") or []
        attributes = alarm_summary(messages[0]) if messages else {}
        attributes["last_alarm"] = self._processor.last_match_time
        return attributes

    @callback
    def _handle_processor_update(self) -> None:
        if self._cancel_expiry:
            self._cancel_expiry()
            self._cancel_expiry = None
        if self.is_on:
            remaining = self._window - (dt_util.utcnow() - self._processor.last_match_time)
            self._cancel_expiry = async_call_later(self.hass, remaining, self._expire)
        self.async_write_ha_state()

    @callback
    def _expire(self, _now) -> None:
        self._cancel_expiry = None
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        if self._cancel_expiry:
            self._cancel_expiry()
