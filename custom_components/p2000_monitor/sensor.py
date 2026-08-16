"""Sensor platform for P2000 Monitor."""
from __future__ import annotations

from collections import deque
import re
from typing import Any

import voluptuous as vol
from homeassistant.components.sensor import PLATFORM_SCHEMA, SensorEntity
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_TEST_CAPCODES,
    ATTR_TEST_DISCIPLINE,
    ATTR_TEST_MESSAGE,
    ATTR_TEST_REGION,
    ATTR_TEST_REGION_NAME,
    DEFAULT_INCIDENT_WINDOW,
    DOMAIN,
    EVENT_FILTER_MATCH,
    EVENT_INCIDENT_UPDATE,
    EVENT_NEW_INCIDENT,
    EVENT_NEW_MESSAGE,
    MAX_FILTER_INCIDENT_HISTORY,
    MAX_HISTORY,
    SERVICE_INJECT_TEST_INCIDENT,
)
from .coordinator_v2 import P2000DataCoordinator, REGIONS, SERVICES
from .coordinator import extract_priority

DEFAULT_NAME = "P2000 Monitor"
CONF_FILTERS = "filters"
CONF_EXCLUDE_CAPCODES = "exclude_capcodes"
CONF_INCIDENT_WINDOW = "incident_window"
CONF_REGIONS = "regions"
CONF_DISCIPLINES = "disciplines"
CONF_PRIORITIES = "priorities"
CONF_CAPCODES = "capcodes"
CONF_INCLUDE_TEXT = "include_text"
CONF_EXCLUDE_TEXT = "exclude_text"

FILTER_SCHEMA = vol.Schema({
    vol.Required(CONF_NAME): cv.string,
    vol.Optional(CONF_REGIONS, default=[]): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional(CONF_DISCIPLINES, default=[]): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional(CONF_PRIORITIES, default=[]): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional(CONF_CAPCODES, default=[]): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional(CONF_INCLUDE_TEXT, default=[]): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional(CONF_EXCLUDE_TEXT, default=[]): vol.All(cv.ensure_list, [cv.string]),
})

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    vol.Optional(CONF_EXCLUDE_CAPCODES, default=[]): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional(CONF_INCIDENT_WINDOW, default=DEFAULT_INCIDENT_WINDOW): vol.All(vol.Coerce(int), vol.Range(min=60, max=1800)),
    vol.Optional(CONF_FILTERS, default=[]): vol.All(cv.ensure_list, [FILTER_SCHEMA]),
})


def _normalise_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    return [x.strip() for x in str(value or "").replace(";", ",").split(",") if x.strip()]


def _filter_matches(message: dict[str, Any], config: dict[str, Any]) -> bool:
    regions = {str(x) for x in config.get(CONF_REGIONS, [])}
    disciplines = {str(x) for x in config.get(CONF_DISCIPLINES, [])}
    priorities = {str(x).upper().replace(" ", "") for x in config.get(CONF_PRIORITIES, [])}
    capcodes = {str(x) for x in config.get(CONF_CAPCODES, [])}
    include = [str(x).lower() for x in config.get(CONF_INCLUDE_TEXT, []) if str(x).strip()]
    exclude = [str(x).lower() for x in config.get(CONF_EXCLUDE_TEXT, []) if str(x).strip()]

    if regions and str(message.get("regio", "")) not in regions:
        return False
    if disciplines:
        wanted_names = {SERVICES.get(x, x).lower() for x in disciplines}
        if str(message.get("discipline", "")).lower() not in wanted_names:
            return False
    if priorities:
        priority = extract_priority(message.get("message", ""))
        if not priority or priority.upper() not in priorities:
            return False
    if capcodes and str(message.get("capcode", "")) not in capcodes:
        return False

    text = str(message.get("message", ""))
    lower = text.lower()
    if include and not all(term in lower for term in include):
        return False
    if exclude and any(term in lower for term in exclude):
        return False
    return True


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
    entry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one user-configured P2000 sensor."""
    data = dict(entry.data)
    filter_config = {
        CONF_NAME: data.get(CONF_NAME, entry.title),
        CONF_REGIONS: data.get(CONF_REGIONS, []),
        CONF_DISCIPLINES: data.get(CONF_DISCIPLINES, []),
        CONF_PRIORITIES: data.get(CONF_PRIORITIES, []),
        CONF_CAPCODES: data.get(CONF_CAPCODES, []),
        CONF_INCLUDE_TEXT: data.get(CONF_INCLUDE_TEXT, []),
        CONF_EXCLUDE_TEXT: data.get(CONF_EXCLUDE_TEXT, []),
    }
    api_filter = {
        "regios": filter_config[CONF_REGIONS],
        "diensten": filter_config[CONF_DISCIPLINES],
        "capcodes": filter_config[CONF_CAPCODES],
        "melding": filter_config[CONF_INCLUDE_TEXT],
        "priorities": filter_config[CONF_PRIORITIES],
        "include_text": filter_config[CONF_INCLUDE_TEXT],
        "exclude_text": filter_config[CONF_EXCLUDE_TEXT],
    }
    coordinator = await _build_coordinator(
        hass,
        int(data.get(CONF_INCIDENT_WINDOW, DEFAULT_INCIDENT_WINDOW)),
        [],
        api_filter,
    )
    async_add_entities([P2000FilterSensor(coordinator, filter_config, 0)], True)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up legacy YAML configuration."""
    name = config.get(CONF_NAME, DEFAULT_NAME)
    filters = config.get(CONF_FILTERS, [])
    exclude_capcodes = config.get(CONF_EXCLUDE_CAPCODES, [])
    incident_window = config.get(CONF_INCIDENT_WINDOW, DEFAULT_INCIDENT_WINDOW)
    coordinator = await _build_coordinator(hass, incident_window, exclude_capcodes, {})

    if not hass.services.has_service(DOMAIN, SERVICE_INJECT_TEST_INCIDENT):
        async def handle_test_incident(call):
            region = str(call.data.get(ATTR_TEST_REGION, "21"))
            region_name = call.data.get(ATTR_TEST_REGION_NAME, REGIONS.get(region, "Limburg-Zuid"))
            discipline = call.data.get(ATTR_TEST_DISCIPLINE, "Brandweerdiensten")
            message = call.data.get(ATTR_TEST_MESSAGE, "P 1 BR woning Teststraat Maastricht 243231")
            capcodes = call.data.get(ATTR_TEST_CAPCODES, ["1005258", "1005264", "1005998"])
            if isinstance(capcodes, str):
                capcodes = [capcodes]
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            messages = []
            for index, capcode in enumerate(capcodes):
                messages.append({
                    "message": message,
                    "capcode": str(capcode),
                    "regio": region,
                    "regio_name": region_name,
                    "discipline": discipline,
                    "latitude": "50.8514",
                    "longitude": "5.6909",
                    "published": f"TEST-PENDING-{now.isoformat()}-{index}",
                    "test": True,
                })
            await coordinator.async_inject_test_messages(messages)
        hass.services.async_register(DOMAIN, SERVICE_INJECT_TEST_INCIDENT, handle_test_incident)

    entities: list[SensorEntity] = [P2000MonitorSensor(coordinator, name)]
    for index, filter_config in enumerate(filters):
        entities.append(P2000FilterSensor(coordinator, filter_config, index))
    async_add_entities(entities, True)


class P2000MonitorSensor(CoordinatorEntity, SensorEntity):
    """Unfiltered latest P2000 message sensor."""

    def __init__(self, coordinator, name):
        super().__init__(coordinator)
        self._attr_name = name
        self._attr_unique_id = "p2000_monitor_main"

    @property
    def native_value(self):
        latest = (self.coordinator.data or {}).get("latest")
        return latest.get("message", "Geen meldingen") if latest else "Geen meldingen"

    @property
    def icon(self):
        latest = (self.coordinator.data or {}).get("latest") or {}
        return {
            "Brandweerdiensten": "mdi:fire-truck",
            "Ambulancediensten": "mdi:ambulance",
            "Politiediensten": "mdi:car-emergency",
            "Lifeliner": "mdi:helicopter",
        }.get(latest.get("discipline"), "mdi:radio-tower")

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data or {}
        latest = data.get("latest") or {}
        attrs = dict(latest)
        attrs["incident_count"] = len(data.get("incidents", []))
        return attrs

    def _handle_coordinator_update(self):
        data = self.coordinator.data or {}
        for message in data.get("new_messages", []):
            self.hass.bus.async_fire(EVENT_NEW_MESSAGE, dict(message))
        for change in data.get("incident_changes", []):
            event = EVENT_NEW_INCIDENT if change.get("action") == "new" else EVENT_INCIDENT_UPDATE
            self.hass.bus.async_fire(event, dict(change.get("incident", {})))
        super()._handle_coordinator_update()


class P2000FilterSensor(CoordinatorEntity, SensorEntity):
    """Filtered P2000 view with incident history."""

    _attr_icon = "mdi:radio-tower"

    def __init__(self, coordinator, filter_config, index):
        super().__init__(coordinator)
        self.filter_config = filter_config
        self._attr_name = filter_config[CONF_NAME]
        safe = re.sub(r"[^a-z0-9]+", "_", self._attr_name.lower()).strip("_")
        self._attr_unique_id = f"p2000_monitor_filter_{index}_{safe}"
        self._history = deque(maxlen=MAX_HISTORY)
        self._incident_history = deque(maxlen=MAX_FILTER_INCIDENT_HISTORY)
        self._processed = set()
        self._processed_incidents = set()

    def _matching_messages(self) -> list[dict[str, Any]]:
        data = self.coordinator.data or {}
        candidates = []
        latest = data.get("latest")
        if isinstance(latest, dict):
            candidates.append(latest)
        candidates.extend(data.get("new_messages", []))
        unique = {}
        for message in candidates:
            mid = message.get("message_id")
            if mid:
                unique[mid] = message
        return [m for m in unique.values() if _filter_matches(m, self.filter_config)]

    def _matching_incidents(self) -> list[dict[str, Any]]:
        data = self.coordinator.data or {}
        return [i for i in data.get("incidents", []) if _filter_matches(i, self.filter_config)]

    @property
    def native_value(self):
        latest = self._history[0] if self._history else None
        if latest:
            return latest.get("message", "Onbekende melding")
        matches = self._matching_messages()
        return matches[0].get("message", "Geen meldingen") if matches else "Geen meldingen"

    @property
    def icon(self):
        latest = self._history[0] if self._history else None
        if latest:
            return {
                "Brandweerdiensten": "mdi:fire-truck",
                "Ambulancediensten": "mdi:ambulance",
                "Politiediensten": "mdi:car-emergency",
                "Lifeliner": "mdi:helicopter",
            }.get(latest.get("discipline"), "mdi:radio-tower")
        return "mdi:radio-tower"

    @property
    def extra_state_attributes(self):
        latest = self._history[0] if self._history else None
        incidents = list(self._incident_history)
        latest_incident = incidents[0] if incidents else None
        attrs = dict(latest or {})
        attrs["filter"] = self.filter_config
        attrs["incident_count"] = len(incidents)
        attrs["incident"] = latest_incident
        attrs["incident_id"] = latest_incident.get("incident_id") if latest_incident else None
        attrs["incident_tijd"] = latest_incident.get("last_seen") if latest_incident else None
        attrs["incidenten"] = incidents
        attrs["incident_history"] = incidents
        attrs["incidenthistorie"] = incidents
        attrs["meldingen"] = [x.get("message", "") for x in list(self._history)]
        return attrs

    def _handle_coordinator_update(self):
        for message in self.coordinator.data.get("new_messages", []) if self.coordinator.data else []:
            if not _filter_matches(message, self.filter_config):
                continue
            mid = message.get("message_id")
            if mid and mid not in self._processed:
                self._processed.add(mid)
                self._history.appendleft(dict(message))
                self.hass.bus.async_fire(EVENT_FILTER_MATCH, {"filter": self.filter_config, "message": dict(message)})
        for incident in self._matching_incidents():
            iid = incident.get("incident_id")
            if iid and iid not in self._processed_incidents:
                self._processed_incidents.add(iid)
                self._incident_history.appendleft(dict(incident))
        super()._handle_coordinator_update()
