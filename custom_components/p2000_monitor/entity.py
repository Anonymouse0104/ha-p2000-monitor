"""Shared base for the entities of a capcode/vehicle watch."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .coordinator import P2000FilterProcessor


def alarm_summary(message: dict[str, Any]) -> dict[str, Any]:
    """Compact alert fields for event data and attributes."""
    vehicles = message.get("vehicles") or []
    return {
        "message": message.get("message", ""),
        "pager_text": message.get("pager_text", message.get("melding", "")),
        "priority": message.get("priority"),
        "urgency": message.get("urgency"),
        "discipline": message.get("discipline", ""),
        "region": message.get("regio_name", ""),
        "city": message.get("city", ""),
        "street": message.get("street", ""),
        "capcodes": message.get("capcodes", []),
        "vehicles": [v.get("id") if isinstance(v, dict) else v for v in vehicles],
        "vehicle_types": [v.get("type") for v in vehicles if isinstance(v, dict) and v.get("type")],
        "latitude": message.get("latitude"),
        "longitude": message.get("longitude"),
        "distance_km": message.get("distance_km"),
        "published": message.get("published", ""),
        "message_id": message.get("message_id", ""),
    }


class P2000WatchEntity(Entity):
    """Entity of a watch subentry; one device per watch groups its entities."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, processor: P2000FilterProcessor, subentry: ConfigSubentry, key: str) -> None:
        self._processor = processor
        self._attr_unique_id = f"{subentry.subentry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, subentry.subentry_id)},
            name=subentry.title,
            manufacturer="P2000 Monitor",
            model="Capcode/voertuig",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._processor.async_add_listener(self._handle_processor_update))

    @callback
    def _handle_processor_update(self) -> None:
        raise NotImplementedError
