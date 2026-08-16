"""API-backed coordinator for P2000 Monitor v0.5.2."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
import re
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
from homeassistant.helpers.update_coordinator import UpdateFailed

from .coordinator import P2000DataCoordinator as LegacyCoordinator

_LOGGER = logging.getLogger(__name__)

API_URL = "https://beta.alarmeringdroid.nl/api2/find/"
API_TIMEOUT = 15
LOCAL_TZ = ZoneInfo("Europe/Amsterdam")

REGIONS: dict[str, str] = {
    "1": "Amsterdam-Amstelland", "2": "Groningen", "3": "Noord- en Oost-Gelderland",
    "4": "Zaanstreek-Waterland", "5": "Hollands Midden", "6": "Brabant-Noord",
    "7": "Friesland", "8": "Gelderland-Midden", "9": "Kennemerland",
    "10": "Rotterdam-Rijnmond", "11": "Brabant-Zuidoost", "12": "Drenthe",
    "13": "Gelderland-Zuid", "14": "Zuid-Holland-Zuid", "15": "Limburg-Noord",
    "17": "IJsselland", "18": "Utrecht", "19": "Gooi en Vechtstreek",
    "20": "Zeeland", "21": "Zuid-Limburg", "23": "Twente",
    "24": "Noord-Holland-Noord", "25": "Haaglanden", "26": "Midden- en West-Brabant",
    "27": "Flevoland",
}

SERVICES = {
    "1": "Politiediensten", "2": "Brandweerdiensten", "3": "Ambulancediensten",
    "4": "KNRM", "5": "Lifeliner", "7": "DARES",
}


def _first(value: Any, default: Any = "") -> Any:
    if isinstance(value, (list, tuple)):
        return value[0] if value else default
    return value if value is not None else default


def _as_text(value: Any) -> str:
    value = _first(value, "")
    return str(value).strip() if value is not None else ""


def _capcode_from_item(item: dict[str, Any]) -> str:
    for key in ("capcode", "capcodes", "code", "codes"):
        value = item.get(key)
        if isinstance(value, list):
            return str(value[0]).strip() if value else ""
        if value:
            return str(value).strip()
    return ""


def _region_id(item: dict[str, Any]) -> str:
    raw = item.get("regioid", item.get("regio_id", item.get("region_id")))
    if raw is None:
        raw = item.get("regio")
    raw = _as_text(raw)
    if raw.isdigit():
        return raw
    wanted = raw.lower().replace(" ", "").replace("-", "")
    for region_id, name in REGIONS.items():
        if wanted == name.lower().replace(" ", "").replace("-", ""):
            return region_id
    return ""


def _published(item: dict[str, Any]) -> str:
    """Return a stable ISO timestamp from all supported API date formats."""
    for key in ("published", "datetime", "datumtijd", "timestamp", "date"):
        value = item.get(key)
        if not value:
            continue
        text = _as_text(value)
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=LOCAL_TZ)
            return dt.astimezone(timezone.utc).isoformat()
        except ValueError:
            pass

    # AlarmeringDroid currently returns separate Dutch local-time fields:
    # "datum": "16-08" and "tijd": "13:14".
    date_text = _as_text(item.get("datum"))
    time_text = _as_text(item.get("tijd"))
    if date_text and time_text:
        match = re.search(r"(\d{1,2})-(\d{1,2})", date_text)
        time_match = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", time_text)
        if match and time_match:
            day, month = int(match.group(1)), int(match.group(2))
            hour, minute = int(time_match.group(1)), int(time_match.group(2))
            second = int(time_match.group(3) or 0)
            now_local = datetime.now(LOCAL_TZ)
            year = now_local.year
            try:
                dt_local = datetime(year, month, day, hour, minute, second, tzinfo=LOCAL_TZ)
            except ValueError:
                dt_local = None
            if dt_local is not None:
                if dt_local - now_local > timedelta(days=2):
                    dt_local = dt_local.replace(year=year - 1)
                return dt_local.astimezone(timezone.utc).isoformat()

    _LOGGER.warning("Geen geldige publicatiedatum gevonden voor P2000-melding: %s", item)
    return datetime.now(timezone.utc).isoformat()


def normalize_api_message(item: dict[str, Any]) -> dict[str, Any] | None:
    """Convert one AlarmeringDroid item into our internal message format."""
    message = _as_text(item.get("tekstmelding") or item.get("message") or item.get("melding"))
    if not message:
        return None

    dienst_id = _as_text(item.get("dienstid") or item.get("dienst_id") or item.get("dienst"))
    discipline = SERVICES.get(dienst_id, _as_text(item.get("discipline") or item.get("dienst")))
    region = _region_id(item)
    region_name = REGIONS.get(region, _as_text(item.get("regio_name") or item.get("region_name") or item.get("regio")))
    latitude = item.get("latitude", item.get("lat", ""))
    longitude = item.get("longitude", item.get("lon", ""))
    city = _as_text(item.get("plaats") or item.get("city") or item.get("woonplaats"))
    capcode = _capcode_from_item(item)
    published = _published(item)

    normalized = {
        "message": message,
        "melding": _as_text(item.get("melding") or message),
        "tekstmelding": message,
        "capcode": capcode,
        "regio": region,
        "regio_name": region_name,
        "discipline": discipline,
        "dienstid": dienst_id,
        "latitude": latitude,
        "longitude": longitude,
        "published": published,
        "source": "alarmeringdroid",
        "city": city,
    }
    normalized["message_id"] = LegacyCoordinator._generate_message_id(normalized)
    return normalized


class P2000DataCoordinator(LegacyCoordinator):
    """Drop-in replacement using the structured AlarmeringDroid API."""

    def __init__(self, hass, exclude_capcodes=None, incident_window=900, api_filter=None):
        self.api_filter = {k: v for k, v in (api_filter or {}).items() if v not in (None, "", [], {})}
        super().__init__(hass, exclude_capcodes=exclude_capcodes, incident_window=incident_window)

    async def _fetch_api(self) -> list[dict[str, Any]]:
        # The public API endpoint is proven to support region, service and
        # capcode filtering. Priorities and text filters are deliberately kept
        # out of the request and are applied locally below.
        request_filter = {
            key: self.api_filter[key]
            for key in ("regios", "diensten", "capcodes")
            if self.api_filter.get(key) not in (None, "", [], {})
        }
        payload = json.dumps(request_filter, ensure_ascii=False, separators=(",", ":"))
        headers = {
            "User-Agent": "P2000-Monitor/0.5.2 Home Assistant",
            "Accept": "application/json, text/plain, */*",
        }
        timeout = aiohttp.ClientTimeout(total=API_TIMEOUT)
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(f"{API_URL}{payload}", timeout=timeout) as response:
                response.raise_for_status()
                text = await response.text(errors="replace")
                raw = json.loads(text)

        items = raw.get("meldingen") if isinstance(raw, dict) else None
        if items is None and isinstance(raw, dict):
            items = raw.get("items")
        if not isinstance(items, list):
            raise ValueError("AlarmeringDroid API returned no 'meldingen'/'items' list")

        messages: list[dict[str, Any]] = []
        for item in items:
            if isinstance(item, dict):
                message = normalize_api_message(item)
                if message:
                    messages.append(message)
        return messages

    @staticmethod
    def _local_filter(message: dict[str, Any], api_filter: dict[str, Any]) -> bool:
        from .coordinator import extract_priority

        priorities = api_filter.get("priorities") or []
        if priorities:
            wanted = {str(x).upper().replace(" ", "") for x in priorities}
            if (extract_priority(message.get("message", "")) or "").upper() not in wanted:
                return False

        include_text = [str(x).lower() for x in api_filter.get("include_text", []) if str(x).strip()]
        exclude_text = [str(x).lower() for x in api_filter.get("exclude_text", []) if str(x).strip()]
        text = str(message.get("message", "")).lower()
        if include_text and not all(x in text for x in include_text):
            return False
        if exclude_text and any(x in text for x in exclude_text):
            return False
        return True

    async def _async_update_data(self):
        await self.async_load_incident_history()
        try:
            messages = await self._fetch_api()
        except Exception as err:
            _LOGGER.exception("AlarmeringDroid API update mislukt")
            raise UpdateFailed(f"AlarmeringDroid API update mislukt: {err}") from err

        messages = [m for m in messages if not self._is_excluded(m)]
        messages = [m for m in messages if self._local_filter(m, self.api_filter)]
        messages.sort(
            key=lambda item: self._parse_published(item.get("published"))
            or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        latest = messages[0] if messages else None

        if not self._initialized:
            for message in messages:
                self._remember_message_id(message.get("message_id", ""))
            self._initialized = True
            return {"latest": latest, "new_messages": [], "incident_changes": [], "incidents": self._current_incidents()}

        now = datetime.now(timezone.utc)
        live_horizon = max(self.incident_window, 900)
        live_new: list[dict[str, Any]] = []
        for message in messages:
            published = self._parse_published(message.get("published"))
            if published is not None:
                age = (now - published).total_seconds()
                if age < -300 or age > live_horizon:
                    continue
            message_id = message.get("message_id", "")
            if not message_id or message_id in self._seen_ids:
                continue
            self._remember_message_id(message_id)
            live_new.append(message)
            _LOGGER.info(
                "AlarmeringDroid nieuwe melding: %s | %s | %s | %s | regio %s",
                message.get("published", ""), message.get("discipline", ""),
                message.get("city", ""), message.get("message", ""),
                message.get("regio_name", message.get("regio", "")),
            )

        incident_changes = self._process_live_batch(live_new)
        if incident_changes:
            await self.async_save_incident_history()
        return {
            "latest": latest,
            "new_messages": live_new,
            "incident_changes": incident_changes,
            "incidents": self._current_incidents(),
        }
