"""Config flow for P2000 Monitor."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

DOMAIN = "p2000_monitor"
CONF_ALL_SERVICES = "all_services"

REGIONS = {
    "1": "Amsterdam-Amstelland", "2": "Groningen", "3": "Noord- en Oost-Gelderland",
    "4": "Zaanstreek-Waterland", "5": "Hollands Midden", "6": "Brabant-Noord",
    "7": "Friesland", "8": "Gelderland-Midden", "9": "Kennemerland",
    "10": "Rotterdam-Rijnmond", "11": "Brabant-Zuidoost", "12": "Drenthe",
    "13": "Gelderland-Zuid", "14": "Zuid-Holland-Zuid", "15": "Limburg-Noord",
    "17": "IJsselland", "18": "Utrecht", "19": "Gooi en Vechtstreek", "20": "Zeeland",
    "21": "Zuid-Limburg", "23": "Twente", "24": "Noord-Holland-Noord", "25": "Haaglanden",
    "26": "Midden- en West-Brabant", "27": "Flevoland",
}

DISCIPLINES = {
    "1": "Politie", "2": "Brandweer", "3": "Ambulance", "4": "KNRM", "5": "Lifeliner", "7": "DARES",
}
PRIORITIES = ["P1", "P2", "P3", "P4", "P5"]


def _multi_selector(options: list[dict[str, str]]) -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(options=options, multiple=True, mode=selector.SelectSelectorMode.LIST)
    )

REGION_SELECTOR = _multi_selector([{"value": key, "label": value} for key, value in REGIONS.items()])
DISCIPLINE_SELECTOR = _multi_selector([{"value": key, "label": value} for key, value in DISCIPLINES.items()])
PRIORITY_SELECTOR = _multi_selector([{"value": value, "label": value} for value in PRIORITIES])


def _schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema({
        vol.Required("name", default=defaults.get("name", "P2000")): str,
        vol.Optional(CONF_ALL_SERVICES, default=defaults.get(CONF_ALL_SERVICES, False)): bool,
        vol.Optional("regions", default=defaults.get("regions", [])): REGION_SELECTOR,
        vol.Optional("disciplines", default=defaults.get("disciplines", [])): DISCIPLINE_SELECTOR,
        vol.Optional("priorities", default=defaults.get("priorities", [])): PRIORITY_SELECTOR,
        vol.Optional("capcodes", default=defaults.get("capcodes", "")): str,
        vol.Optional("include_text", default=defaults.get("include_text", "")): str,
        vol.Optional("exclude_text", default=defaults.get("exclude_text", "")): str,
        vol.Optional("incident_window", default=defaults.get("incident_window", 900)): vol.All(vol.Coerce(int), vol.Range(min=60, max=1800)),
    })


def _normalise(data: dict[str, Any]) -> dict[str, Any]:
    def split(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(x).strip() for x in value if str(x).strip()]
        return [x.strip() for x in str(value or "").replace(";", ",").split(",") if x.strip()]

    return {
        "name": str(data["name"]).strip() or "P2000",
        CONF_ALL_SERVICES: bool(data.get(CONF_ALL_SERVICES, False)),
        "regions": [str(x) for x in data.get("regions", [])],
        "disciplines": [str(x) for x in data.get("disciplines", [])],
        "priorities": [str(x).upper() for x in data.get("priorities", [])],
        "capcodes": split(data.get("capcodes")),
        "include_text": split(data.get("include_text")),
        "exclude_text": split(data.get("exclude_text")),
        "incident_window": int(data.get("incident_window", 900)),
    }


class P2000ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle P2000 Monitor setup."""
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            data = _normalise(user_input)
            await self.async_set_unique_id(f"p2000_monitor_{data['name'].lower()}")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=data["name"], data=data)
        return self.async_show_form(step_id="user", data_schema=_schema())

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return P2000OptionsFlow()


class P2000OptionsFlow(config_entries.OptionsFlow):
    """Allow filters to be changed after installation."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        current = dict(self.config_entry.data)
        if user_input is not None:
            data = _normalise(user_input)
            self.hass.config_entries.async_update_entry(self.config_entry, data=data, title=data["name"])
            return self.async_create_entry(title="", data=data)
        return self.async_show_form(step_id="init", data_schema=_schema(current))
