"""Sensor platform for P2000 Monitor."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import entry_settings
from .const import (
    CONF_ALL_SERVICES,
    CONF_CAPCODES,
    CONF_DISCIPLINES,
    CONF_EXCLUDE_TEXT,
    CONF_INCLUDE_TEXT,
    CONF_PRIORITIES,
    CONF_REGIONS,
    MAX_FILTER_INCIDENT_HISTORY,
    MAX_HISTORY,
)
from .coordinator import (
    SERVICES,
    P2000FilterProcessor,
    extract_priority,
    message_sort_key,
    normalize_incident_text,
    parse_published,
    text_similarity,
)


def _filter_matches(message: dict[str, Any], config: dict[str, Any]) -> bool:
    """Return whether a message matches a sensor's configured filters."""
    if config.get(CONF_ALL_SERVICES):
        return True

    regions = {str(x) for x in config.get(CONF_REGIONS, [])}
    disciplines = {str(x) for x in config.get(CONF_DISCIPLINES, [])}
    priorities = {str(x).upper().replace(" ", "") for x in config.get(CONF_PRIORITIES, [])}
    capcodes = {str(x) for x in config.get(CONF_CAPCODES, [])}
    include = [str(x).lower() for x in config.get(CONF_INCLUDE_TEXT, []) if str(x).strip()]
    exclude = [str(x).lower() for x in config.get(CONF_EXCLUDE_TEXT, []) if str(x).strip()]

    if regions and str(message.get("regio", "")) not in regions:
        return False
    if disciplines:
        wanted = {SERVICES.get(x, x).lower() for x in disciplines}
        if str(message.get("discipline", "")).lower() not in wanted:
            return False
    if priorities:
        priority = extract_priority(message.get("message", ""))
        if not priority or priority.upper() not in priorities:
            return False

    message_capcodes = {
        str(code).strip()
        for code in (message.get("capcodes") or [])
        if str(code).strip()
    }
    if not message_capcodes and message.get("capcode"):
        message_capcodes.add(str(message.get("capcode")).strip())
    if capcodes and not message_capcodes.intersection(capcodes):
        return False

    lower = str(message.get("message", "")).lower()
    if include and not all(x in lower for x in include):
        return False
    if exclude and any(x in lower for x in exclude):
        return False
    return True


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
    processor: P2000FilterProcessor = entry.runtime_data
    entity_key = entry.unique_id or entry.entry_id
    async_add_entities([P2000FilterSensor(processor, entry_settings(entry), entity_key)])


class P2000FilterSensor(SensorEntity):
    _attr_should_poll = False

    def __init__(self, processor: P2000FilterProcessor, config: dict[str, Any], entity_key: str) -> None:
        self._processor = processor
        self._config = config
        self._entity_key = entity_key
        self._attr_name = config.get(CONF_NAME, "P2000")
        self._attr_unique_id = f"p2000_monitor_{entity_key}"

    @property
    def available(self) -> bool:
        return self._processor.feed.last_update_success

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

    def _matching_messages(self):
        return _newest_first(list(self._data().get("messages") or []))

    def _matching_incidents(self):
        messages = self._matching_messages()
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

    @property
    def native_value(self):
        messages = self._matching_messages()
        return messages[0].get("message", "Geen meldingen") if messages else "Geen meldingen"

    @property
    def icon(self):
        messages = self._matching_messages()
        return self._icon_for(messages[0] if messages else {})

    @property
    def extra_state_attributes(self):
        messages = self._matching_messages()
        latest = messages[0] if messages else {}
        data = self._data()
        incidents = self._matching_incidents()
        return {
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
        self.async_on_remove(self._processor.async_add_listener(self._handle_processor_update))

    @callback
    def _handle_processor_update(self):
        self.async_write_ha_state()

