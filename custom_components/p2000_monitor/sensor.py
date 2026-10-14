"""Sensor platform for P2000 Monitor."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import sensor_settings
from .const import AVAILABILITY_GRACE, CONF_ENTITY_KEY, MAX_FILTER_INCIDENT_HISTORY, MAX_HISTORY

# Home Assistant rejects states longer than 255 characters.
MAX_STATE_LENGTH = 255
from .coordinator import (
    P2000FilterProcessor,
    extract_priority,
    message_sort_key,
    normalize_incident_text,
    parse_published,
    text_similarity,
)


def _published_timestamp(message: dict[str, Any]) -> float:
    published = parse_published(message.get("published"))
    return published.timestamp() if published else 0.0


def _newest_first(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(messages, key=message_sort_key, reverse=True)


def _build_incident_history(messages: list[dict[str, Any]], incident_window: int) -> list[dict[str, Any]]:
    """Build a visible incident history from the messages accepted by this sensor.

    This deliberately works from the already filtered message set. That means a
    VRLN sensor can never show an incident that belongs to VRZL, and the Echt
    sensor can only show incidents containing one of its configured capcodes.
    """
    incidents: list[dict[str, Any]] = []
    ordered = _newest_first(messages)

    for message in ordered:
        if message.get("test"):
            continue
        normalized = normalize_incident_text(message.get("message", ""))
        published = _published_timestamp(message)
        matched = None

        for incident in incidents:
            if str(incident.get("regio", "")) != str(message.get("regio", "")):
                continue
            if str(incident.get("discipline", "")).lower() != str(message.get("discipline", "")).lower():
                continue
            if abs(incident.get("last_seen_ts", 0.0) - published) > incident_window:
                continue
            old = str(incident.get("_normalized_text", ""))
            if old == normalized or text_similarity(old, normalized) >= 0.70:
                matched = incident
                break

        if matched is None:
            matched = {
                "incident_id": message.get("message_id", ""),
                "message": message.get("message", ""),
                "priority": extract_priority(message.get("message", "")),
                "regio": message.get("regio", ""),
                "regio_name": message.get("regio_name", ""),
                "discipline": message.get("discipline", ""),
                "dienstid": message.get("dienstid", ""),
                "city": message.get("city", ""),
                "latitude": message.get("latitude", ""),
                "longitude": message.get("longitude", ""),
                "first_seen": message.get("published", ""),
                "last_seen": message.get("published", ""),
                "message_count": 0,
                "capcodes": [],
                "messages": [],
                "last_seen_ts": published,
                "_normalized_text": normalized,
            }
            incidents.append(matched)

        matched["message_count"] += 1
        matched["last_seen"] = max(str(matched.get("last_seen", "")), str(message.get("published", "")))
        matched["last_seen_ts"] = max(matched.get("last_seen_ts", 0.0), published)
        for capcode in message.get("capcodes", []) or []:
            capcode = str(capcode).strip()
            if capcode and capcode not in matched["capcodes"]:
                matched["capcodes"].append(capcode)
        matched["messages"].append({
            "message": message.get("message", ""),
            "published": message.get("published", ""),
            "capcodes": message.get("capcodes", []),
            "message_id": message.get("message_id", ""),
        })

    incidents.sort(key=lambda item: item.get("last_seen_ts", 0.0), reverse=True)
    for incident in incidents:
        incident.pop("last_seen_ts", None)
        incident.pop("_normalized_text", None)
        incident["messages"] = incident["messages"][:MAX_HISTORY]
    return incidents[:MAX_FILTER_INCIDENT_HISTORY]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add one sensor per sensor subentry."""
    for subentry_id, processor in entry.runtime_data.items():
        subentry = entry.subentries[subentry_id]
        # Migrated sensors keep the unique_id they had as a separate entry.
        entity_key = subentry.data.get(CONF_ENTITY_KEY, subentry_id)
        async_add_entities(
            [P2000FilterSensor(processor, sensor_settings(subentry.data, subentry.title), entity_key)],
            config_subentry_id=subentry_id,
        )


class P2000FilterSensor(SensorEntity):
    _attr_should_poll = False
    # These lists easily exceed the recorder's 16 KB attribute limit (which
    # drops all attributes with a warning on every state change). They stay
    # available live in the state machine for dashboards and templates.
    _unrecorded_attributes = frozenset(
        {"capcodes", "incident", "incidenten", "incident_history", "meldingen"}
    )

    def __init__(self, processor: P2000FilterProcessor, config: dict[str, Any], entity_key: str) -> None:
        self._processor = processor
        self._config = config
        self._entity_key = entity_key
        self._attr_name = config.get(CONF_NAME, "P2000")
        self._attr_unique_id = f"p2000_monitor_{entity_key}"

    @property
    def available(self) -> bool:
        feed = self._processor.feed
        if feed.last_update_success:
            return True
        return (
            feed.last_success_time is not None
            and dt_util.utcnow() - feed.last_success_time < AVAILABILITY_GRACE
        )

    def _data(self):
        return self._processor.data or {}

    @staticmethod
    def _icon_for(message):
        return {
            "Brandweerdiensten": "mdi:fire-truck",
            "Ambulancediensten": "mdi:ambulance",
            "Politiediensten": "mdi:car-emergency",
            "Lifeliner": "mdi:helicopter",
        }.get(message.get("discipline"), "mdi:radio-tower")

    def _matching_incidents(self, messages):
        visible_history = _build_incident_history(
            messages,
            int(self._processor.incident_window),
        )
        if visible_history:
            return visible_history

        incidents = list(self._data().get("incidents", []) or [])
        return sorted(
            incidents,
            key=lambda i: _published_timestamp({"published": i.get("last_seen") or i.get("first_seen")}),
            reverse=True,
        )[:MAX_FILTER_INCIDENT_HISTORY]

    @callback
    def _update_from_processor(self) -> None:
        """Compute state, icon and attributes once per update.

        Messages arrive newest first from the processor (injected test messages
        on top), so they are not sorted again here.
        """
        data = self._data()
        messages = list(data.get("messages") or [])
        latest = messages[0] if messages else {}
        incidents = self._matching_incidents(messages)
        state = latest.get("message") or "Geen meldingen"
        self._attr_native_value = state if len(state) <= MAX_STATE_LENGTH else state[: MAX_STATE_LENGTH - 1] + "…"
        self._attr_icon = self._icon_for(latest)
        self._attr_extra_state_attributes = {
            "source": latest.get("source", "alarmeringdroid"),
            "message": latest.get("message", ""),
            "melding": latest.get("melding", ""),
            "tekstmelding": latest.get("tekstmelding", ""),
            "published": latest.get("published", ""),
            "regio": latest.get("regio", ""),
            "regio_name": latest.get("regio_name", ""),
            "discipline": latest.get("discipline", ""),
            "dienstid": latest.get("dienstid", ""),
            "city": latest.get("city", ""),
            "capcode": latest.get("capcode", ""),
            "capcodes": latest.get("capcodes", []),
            "latitude": latest.get("latitude", ""),
            "longitude": latest.get("longitude", ""),
            "incident_count": len(incidents),
            "incident": incidents[0] if incidents else None,
            "incident_id": incidents[0].get("incident_id") if incidents else None,
            "incident_tijd": incidents[0].get("first_seen") if incidents else None,
            "incidenten": incidents,
            "incident_history": incidents,
            "meldingen": messages[:MAX_HISTORY],
            "new_message_count": len(data.get("new_messages") or []),
        }

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self._processor.entity_id = self.entity_id
        self._update_from_processor()
        self.async_on_remove(self._processor.async_add_listener(self._handle_processor_update))

    @callback
    def _handle_processor_update(self):
        self._update_from_processor()
        self.async_write_ha_state()

