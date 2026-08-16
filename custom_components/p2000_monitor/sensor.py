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
    # The AlarmeringDroid API is known to support region, service and capcode
    # filters. Priority/text filters are applied locally so an API change or
    # unsupported filter key cannot make an otherwise valid query return zero.
    api_filter = {
        "regios": filter_config[CONF_REGIONS],
        "diensten": filter_config[CONF_DISCIPLINES],
        "capcodes": filter_config[CONF_CAPCODES],
    }
    coordinator = await _build_coordinator(
        hass,
        int(data.get(CONF_INCIDENT_WINDOW, DEFAULT_INCIDENT_WINDOW)),
        [],
        api_filter | {
            "priorities": filter_config[CONF_PRIORITIES],
            "include_text": filter_config[CONF_INCLUDE_TEXT],
            "exclude_text": filter_config[CONF_EXCLUDE_TEXT],
        },
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
            region_name = call.data.get(ATTR_TEST_REGION_NAME, REGIONS.get(region, "Zuid-Limburg"))
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
        history = list(data.get("new_messages") or [])
        return {
            "source": latest.get("source", "alarmeringdroid"),
            "region": latest.get("regio_name", ""),
            "discipline": latest.get("discipline", ""),
            "city": latest.get("city", ""),
            "published": latest.get("published", ""),
            "message_id": latest.get("message_id", ""),
            "incident_count": len(data.get("incidents") or []),
            "new_message_count": len(history),
        }


class P2000FilterSensor(CoordinatorEntity, SensorEntity):
    """Sensor exposing the latest message matching a user-defined filter."""

    def __init__(self, coordinator, config, index):
        super().__init__(coordinator)
        self._config = config
        self._index = index
        self._attr_name = config.get(CONF_NAME, f"P2000 {index + 1}")
        self._attr_unique_id = f"p2000_monitor_filter_{index}"

    @property
    def native_value(self):
        messages = self._matching_messages()
        return messages[0].get("message", "Geen meldingen") if messages else "Geen meldingen"

    @property
    def icon(self):
        messages = self._matching_messages()
        latest = messages[0] if messages else {}
        return {
            "Brandweerdiensten": "mdi:fire-truck",
            "Ambulancediensten": "mdi:ambulance",
            "Politiediensten": "mdi:car-emergency",
            "Lifeliner": "mdi:helicopter",
        }.get(latest.get("discipline"), "mdi:radio-tower")

    def _matching_messages(self):
        data = self.coordinator.data or {}
        messages = list(data.get("new_messages") or [])
        return [m for m in messages if _filter_matches(m, self._config)]
