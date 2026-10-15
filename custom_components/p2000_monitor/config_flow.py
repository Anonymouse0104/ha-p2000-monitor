"""Config flow for P2000 Monitor.

The integration is added once. Each sensor is a subentry ("Sensor toevoegen")
with its own filters, and can be changed later through "Wijzigen".
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_ACTIVE_MINUTES,
    CONF_ALL_SERVICES,
    CONF_BRANDBASE,
    CONF_CAPCODES,
    CONF_DISCIPLINES,
    CONF_DISPLAY,
    CONF_EXCLUDE_TEXT,
    CONF_INCIDENT_WINDOW,
    CONF_INCLUDE_TEXT,
    CONF_PRIORITIES,
    CONF_RADIUS,
    CONF_REGIONS,
    CONF_VEHICLES,
    DEFAULT_ACTIVE_MINUTES,
    DEFAULT_INCIDENT_WINDOW,
    DISPLAY_PAGER,
    DISPLAY_TEXT,
    DOMAIN,
    HUB_TITLE,
    SUBENTRY_SENSOR,
    SUBENTRY_WATCH,
)
from .coordinator import REGIONS
from .enrich import normalize_vehicle

DISCIPLINES = {
    "1": "Politie", "2": "Brandweer", "3": "Ambulance", "4": "KNRM", "5": "Lifeliner", "7": "DARES",
}
# Ambulance A1/A2/B count as urgency 1/2/3, so they match P1/P2/P3.
PRIORITIES = {"P1": "P1 / A1", "P2": "P2 / A2", "P3": "P3 / B", "P4": "P4", "P5": "P5"}
TEXT_LIST_FIELDS = (CONF_CAPCODES, CONF_VEHICLES, CONF_INCLUDE_TEXT, CONF_EXCLUDE_TEXT)

RADIUS_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(min=0, max=300, step=1, unit_of_measurement="km", mode=selector.NumberSelectorMode.BOX)
)
DISPLAY_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(options=[DISPLAY_TEXT, DISPLAY_PAGER], translation_key="display")
)


def _multi_selector(options: list[dict[str, str]]) -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(options=options, multiple=True, mode=selector.SelectSelectorMode.LIST)
    )


REGION_SELECTOR = _multi_selector(
    [{"value": key, "label": value} for key, value in sorted(REGIONS.items(), key=lambda kv: kv[1])]
)
DISCIPLINE_SELECTOR = _multi_selector([{"value": key, "label": value} for key, value in DISCIPLINES.items()])
PRIORITY_SELECTOR = _multi_selector([{"value": key, "label": label} for key, label in PRIORITIES.items()])


def _schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = dict(defaults or {})
    # Stored as lists, edited as comma separated text.
    for key in TEXT_LIST_FIELDS:
        if isinstance(defaults.get(key), list):
            defaults[key] = ", ".join(defaults[key])
    all_services_default = defaults.get(
        CONF_ALL_SERVICES,
        str(defaults.get("name", "")).strip().lower() == HUB_TITLE.lower(),
    )
    return vol.Schema({
        vol.Required("name", default=defaults.get("name", "P2000")): str,
        vol.Optional(CONF_ALL_SERVICES, default=all_services_default): bool,
        vol.Optional(CONF_REGIONS, default=defaults.get(CONF_REGIONS, [])): REGION_SELECTOR,
        vol.Optional(CONF_DISCIPLINES, default=defaults.get(CONF_DISCIPLINES, [])): DISCIPLINE_SELECTOR,
        vol.Optional(CONF_PRIORITIES, default=defaults.get(CONF_PRIORITIES, [])): PRIORITY_SELECTOR,
        vol.Optional(CONF_CAPCODES, default=defaults.get(CONF_CAPCODES, "")): str,
        vol.Optional(CONF_VEHICLES, default=defaults.get(CONF_VEHICLES, "")): str,
        vol.Optional(CONF_INCLUDE_TEXT, default=defaults.get(CONF_INCLUDE_TEXT, "")): str,
        vol.Optional(CONF_EXCLUDE_TEXT, default=defaults.get(CONF_EXCLUDE_TEXT, "")): str,
        vol.Optional(CONF_RADIUS, default=defaults.get(CONF_RADIUS, 0)): RADIUS_SELECTOR,
        vol.Optional(CONF_DISPLAY, default=defaults.get(CONF_DISPLAY, DISPLAY_TEXT)): DISPLAY_SELECTOR,
        vol.Optional(
            CONF_INCIDENT_WINDOW, default=defaults.get(CONF_INCIDENT_WINDOW, DEFAULT_INCIDENT_WINDOW)
        ): vol.All(vol.Coerce(int), vol.Range(min=60, max=1800)),
    })


def _normalise(data: dict[str, Any]) -> dict[str, Any]:
    def split(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(x).strip() for x in value if str(x).strip()]
        return [x.strip() for x in str(value or "").replace(";", ",").split(",") if x.strip()]

    return {
        "name": str(data["name"]).strip() or "P2000",
        CONF_ALL_SERVICES: bool(data.get(CONF_ALL_SERVICES, False)),
        CONF_REGIONS: [str(x) for x in data.get(CONF_REGIONS, [])],
        CONF_DISCIPLINES: [str(x) for x in data.get(CONF_DISCIPLINES, [])],
        CONF_PRIORITIES: [str(x).upper() for x in data.get(CONF_PRIORITIES, [])],
        CONF_CAPCODES: split(data.get(CONF_CAPCODES)),
        CONF_VEHICLES: [normalize_vehicle(x) for x in split(data.get(CONF_VEHICLES))],
        CONF_INCLUDE_TEXT: split(data.get(CONF_INCLUDE_TEXT)),
        CONF_EXCLUDE_TEXT: split(data.get(CONF_EXCLUDE_TEXT)),
        CONF_RADIUS: float(data.get(CONF_RADIUS) or 0),
        CONF_DISPLAY: data.get(CONF_DISPLAY) if data.get(CONF_DISPLAY) in (DISPLAY_TEXT, DISPLAY_PAGER) else DISPLAY_TEXT,
        CONF_INCIDENT_WINDOW: int(data.get(CONF_INCIDENT_WINDOW, DEFAULT_INCIDENT_WINDOW)),
    }


class P2000ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add P2000 Monitor together with its first sensor."""

    VERSION = 2

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            data = _normalise(user_input)
            return self.async_create_entry(
                title=HUB_TITLE,
                data={},
                subentries=[
                    {"subentry_type": SUBENTRY_SENSOR, "title": data["name"], "data": data, "unique_id": None}
                ],
            )
        return self.async_show_form(
            step_id="user", data_schema=_schema({"name": HUB_TITLE, CONF_ALL_SERVICES: True})
        )

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry: ConfigEntry) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_SENSOR: P2000SensorSubentryFlow, SUBENTRY_WATCH: P2000WatchSubentryFlow}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return P2000OptionsFlow()


class P2000OptionsFlow(OptionsFlow):
    """Integration-wide options."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(CONF_BRANDBASE, default=self.config_entry.options.get(CONF_BRANDBASE, True)): bool,
            }),
        )


class _NamedSubentryFlow(ConfigSubentryFlow):
    def _name_taken(self, name: str, own_subentry_id: str | None = None) -> bool:
        return any(
            sub.title.strip().lower() == name.lower()
            for sub_id, sub in self._get_entry().subentries.items()
            if sub_id != own_subentry_id
        )


def _watch_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = dict(defaults or {})
    for key in (CONF_CAPCODES, CONF_VEHICLES):
        if isinstance(defaults.get(key), list):
            defaults[key] = ", ".join(defaults[key])
    return vol.Schema({
        vol.Required("name", default=defaults.get("name", "")): str,
        vol.Optional(CONF_CAPCODES, default=defaults.get(CONF_CAPCODES, "")): str,
        vol.Optional(CONF_VEHICLES, default=defaults.get(CONF_VEHICLES, "")): str,
        vol.Optional(CONF_REGIONS, default=defaults.get(CONF_REGIONS, [])): REGION_SELECTOR,
        vol.Optional(
            CONF_ACTIVE_MINUTES, default=defaults.get(CONF_ACTIVE_MINUTES, DEFAULT_ACTIVE_MINUTES)
        ): vol.All(vol.Coerce(int), vol.Range(min=1, max=240)),
    })


def _normalise_watch(data: dict[str, Any]) -> dict[str, Any]:
    split = lambda value: [x.strip() for x in str(value or "").replace(";", ",").split(",") if x.strip()]
    return {
        "name": str(data["name"]).strip(),
        CONF_CAPCODES: split(data.get(CONF_CAPCODES)),
        CONF_VEHICLES: [normalize_vehicle(x) for x in split(data.get(CONF_VEHICLES))],
        CONF_REGIONS: [str(x) for x in data.get(CONF_REGIONS, [])],
        CONF_ACTIVE_MINUTES: int(data.get(CONF_ACTIVE_MINUTES, DEFAULT_ACTIVE_MINUTES)),
    }


class P2000WatchSubentryFlow(_NamedSubentryFlow):
    """Follow capcodes and/or vehicles: alarm event + active sensor."""

    def _validate(self, data: dict[str, Any], own_subentry_id: str | None = None) -> dict[str, str]:
        if not data["name"]:
            return {"name": "name_required"}
        if self._name_taken(data["name"], own_subentry_id):
            return {"name": "name_exists"}
        if not data[CONF_CAPCODES] and not data[CONF_VEHICLES]:
            return {"base": "no_units"}
        return {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _normalise_watch(user_input)
            if not (errors := self._validate(data)):
                return self.async_create_entry(title=data["name"], data=data)
        return self.async_show_form(step_id="user", data_schema=_watch_schema(user_input), errors=errors)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _normalise_watch(user_input)
            if not (errors := self._validate(data, subentry.subentry_id)):
                return self.async_update_and_abort(
                    self._get_entry(), subentry, title=data["name"], data={**subentry.data, **data}
                )
        defaults = user_input or {"name": subentry.title, **subentry.data}
        return self.async_show_form(step_id="reconfigure", data_schema=_watch_schema(defaults), errors=errors)


class P2000SensorSubentryFlow(_NamedSubentryFlow):
    """Add or change one P2000 sensor."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _normalise(user_input)
            if self._name_taken(data["name"]):
                errors["name"] = "name_exists"
            else:
                return self.async_create_entry(title=data["name"], data=data)
        return self.async_show_form(step_id="user", data_schema=_schema(user_input), errors=errors)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _normalise(user_input)
            if self._name_taken(data["name"], subentry.subentry_id):
                errors["name"] = "name_exists"
            else:
                # Keep migration keys (entity_key, storage_id) next to the new filters.
                return self.async_update_and_abort(
                    self._get_entry(), subentry, title=data["name"], data={**subentry.data, **data}
                )
        defaults = user_input or {"name": subentry.title, **subentry.data}
        return self.async_show_form(step_id="reconfigure", data_schema=_schema(defaults), errors=errors)
