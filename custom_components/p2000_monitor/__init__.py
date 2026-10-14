"""P2000 Monitor integration."""
from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_TEST_CAPCODES,
    ATTR_TEST_DISCIPLINE,
    ATTR_TEST_MESSAGE,
    ATTR_TEST_REGION,
    ATTR_TEST_REGION_NAME,
    DOMAIN,
    SERVICE_INJECT_TEST_INCIDENT,
)
from .coordinator import REGIONS

PLATFORMS = [Platform.SENSOR]
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
        for entry in hass.config_entries.async_entries(DOMAIN):
            if entry.state is ConfigEntryState.LOADED:
                await entry.runtime_data.async_inject_test_messages(messages)

    hass.services.async_register(
        DOMAIN, SERVICE_INJECT_TEST_INCIDENT, handle_test_incident, schema=TEST_INCIDENT_SCHEMA
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up P2000 Monitor from a UI config entry."""
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry after its UI configuration changes."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a P2000 Monitor config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
