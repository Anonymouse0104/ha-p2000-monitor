"""Sensor platform for P2000 Monitor."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DEFAULT_INCIDENT_WINDOW,
    EVENT_FILTER_MATCH,
    EVENT_INCIDENT_UPDATE,
    EVENT_NEW_INCIDENT,
    EVENT_NEW_MESSAGE,
    MAX_FILTER_INCIDENT_HISTORY,
    MAX_HISTORY,
)
from .coordinator import (
    P2000DataCoordinator,
    SERVICES,
    extract_priority,
    message_sort_key,
    normalize_incident_text,
    parse_published,
    text_similarity,
)

CONF_INCIDENT_WINDOW = "incident_window"
CONF_REGIONS = "regions"
CONF_DISCIPLINES = "disciplines"
CONF_PRIORITIES = "priorities"
CONF_CAPCODES = "capcodes"
CONF_INCLUDE_TEXT = "include_text"
CONF_EXCLUDE_TEXT = "exclude_text"
CONF_ALL_SERVICES = "all_services"


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


async def _build_coordinator(
    hass: HomeAssistant,
    incident_window: int,
    exclude_capcodes: list[str],
    api_filter: dict[str, Any] | None = None,
) -> P2000DataCoordinator:
    coordinator = P2000DataCoordinator(
        hass=hass,
        exclude_capcodes=exclude_capcodes,
        incident_window=incident_window,
        api_filter=api_filter or {},
    )
    await coordinator.async_config_entry_first_refresh()
    return coordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = dict(entry.data)
    name = data.get(CONF_NAME, entry.title)
    all_services = bool(data.get(CONF_ALL_SERVICES, False))
    if CONF_ALL_SERVICES not in data and name == "P2000 Monitor":
        all_services = True

    config = {
        CONF_NAME: name,
        CONF_ALL_SERVICES: all_services,
        CONF_REGIONS: data.get(CONF_REGIONS, []),
        CONF_DISCIPLINES: data.get(CONF_DISCIPLINES, []),
        CONF_PRIORITIES: data.get(CONF_PRIORITIES, []),
        CONF_CAPCODES: data.get(CONF_CAPCODES, []),
        CONF_INCLUDE_TEXT: data.get(CONF_INCLUDE_TEXT, []),
        CONF_EXCLUDE_TEXT: data.get(CONF_EXCLUDE_TEXT, []),
    }

    api_filter = {} if all_services else {
        "regios": config[CONF_REGIONS],
        "diensten": config[CONF_DISCIPLINES],
        "capcodes": config[CONF_CAPCODES],
        "priorities": config[CONF_PRIORITIES],
        "include_text": config[CONF_INCLUDE_TEXT],
        "exclude_text": config[CONF_EXCLUDE_TEXT],
    }

    coordinator = await _build_coordinator(
        hass,
        int(data.get(CONF_INCIDENT_WINDOW, DEFAULT_INCIDENT_WINDOW)),
        [],
        api_filter,
    )
    entry.runtime_data = coordinator
    entity_key = entry.unique_id or entry.entry_id
    async_add_entities([P2000FilterSensor(coordinator, config, entity_key)], True)


class P2000BaseSensor(CoordinatorEntity, SensorEntity):
    def _data(self):
        return self.coordinator.data or {}

    @staticmethod
    def _icon_for(message):
        return {
            "Brandweerdiensten": "mdi:fire-truck",
            "Ambulancediensten": "mdi:ambulance",
            "Politiediensten": "mdi:car-emergency",
            "Lifeliner": "mdi:helicopter",
        }.get(message.get("discipline"), "mdi:radio-tower")


class P2000FilterSensor(P2000BaseSensor):
    def __init__(self, coordinator, config, entity_key):
        super().__init__(coordinator)
        self._config = config
        self._entity_key = entity_key
        self._attr_name = config.get(CONF_NAME, "P2000")
        self._attr_unique_id = f"p2000_monitor_{entity_key}"

    def _matching_messages(self):
        return _newest_first(list(self._data().get("messages") or []))

    def _matching_incidents(self):
        messages = self._matching_messages()
        visible_history = _build_incident_history(
            messages,
            int(self.coordinator.incident_window),
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
        self.async_on_remove(self.coordinator.async_add_listener(self._coordinator_updated))

    def _coordinator_updated(self):
        data = self._data()
        for message in data.get("new_messages", []):
            self.hass.bus.async_fire(
                EVENT_NEW_MESSAGE,
                {"message": message, "sensor": self.entity_id},
            )
            self.hass.bus.async_fire(
                EVENT_FILTER_MATCH,
                {"message": message, "filter": self._config, "sensor": self.entity_id},
            )
        for change in data.get("incident_changes", []):
            incident = change.get("incident", {})
            event = EVENT_NEW_INCIDENT if change.get("action") == "new" else EVENT_INCIDENT_UPDATE
            self.hass.bus.async_fire(
                event,
                {
                    "incident": incident,
                    "message": change.get("message", {}),
                    "sensor": self.entity_id,
                },
            )
