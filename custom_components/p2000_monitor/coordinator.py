"""AlarmeringDroid API coordinator for P2000 Monitor."""
from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
import hashlib
import logging
import re
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DEFAULT_INCIDENT_WINDOW,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_INCIDENT_HISTORY,
    MAX_SEEN_MESSAGES,
    STORAGE_KEY,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)
API_URL = "https://beta.alarmeringdroid.nl/api2/find/"
API_TIMEOUT = 15
LOCAL_TZ = ZoneInfo("Europe/Amsterdam")
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
SERVICES = {"1": "Politiediensten", "2": "Brandweerdiensten", "3": "Ambulancediensten", "4": "KNRM", "5": "Lifeliner", "7": "DARES"}


def extract_priority(text: str) -> str | None:
    match = re.search(r"\bP\s*([1-5])\b", str(text).upper())
    return f"P{match.group(1)}" if match else None


def normalize_incident_text(text: str) -> str:
    text = str(text or "").lower()
    text = re.sub(r"\bp\s*[1-5]\b", " ", text)
    text = re.sub(r"\b[a-z]{2,5}-\d{1,3}\b", " ", text)
    text = re.sub(r"\b\d{5,8}\b", " ", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def text_similarity(first: str, second: str) -> float:
    a, b = set(first.split()), set(second.split())
    return len(a & b) / len(a | b) if a and b else 0.0


def _as_text(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return str(value).strip() if value is not None else ""


def _capcodes(item: dict[str, Any]) -> list[str]:
    value = item.get("capcodes", item.get("codes", item.get("capcode", item.get("code", []))))
    if not isinstance(value, list):
        value = [value] if value else []
    result = []
    for entry in value:
        if isinstance(entry, dict):
            entry = entry.get("capcode") or entry.get("code") or entry.get("id")
        text = _as_text(entry)
        if text and text not in result:
            result.append(text)
    return result


def _region_id(item: dict[str, Any]) -> str:
    raw = item.get("regioid", item.get("regio_id", item.get("region_id"),))
    if raw is None:
        raw = item.get("regio")
    raw = _as_text(raw)
    if raw.isdigit():
        return raw
    compact = raw.lower().replace(" ", "").replace("-", "")
    for region_id, name in REGIONS.items():
        if compact == name.lower().replace(" ", "").replace("-", ""):
            return region_id
    return ""


def _published(item: dict[str, Any]) -> str:
    for key in ("published", "datetime", "datumtijd", "timestamp", "date"):
        value = item.get(key)
        if not value:
            continue
        try:
            dt = datetime.fromisoformat(_as_text(value).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=LOCAL_TZ)
            return dt.astimezone(timezone.utc).isoformat()
        except ValueError:
            pass
    date_match = re.search(r"(\d{1,2})-(\d{1,2})", _as_text(item.get("datum")))
    time_match = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", _as_text(item.get("tijd")))
    if date_match and time_match:
        day, month = int(date_match.group(1)), int(date_match.group(2))
        hour, minute = int(time_match.group(1)), int(time_match.group(2))
        second = int(time_match.group(3) or 0)
        now = datetime.now(LOCAL_TZ)
        try:
            dt = datetime(now.year, month, day, hour, minute, second, tzinfo=LOCAL_TZ)
            if dt - now > timedelta(days=2):
                dt = dt.replace(year=now.year - 1)
            return dt.astimezone(timezone.utc).isoformat()
        except ValueError:
            pass
    return datetime.now(timezone.utc).isoformat()


def normalize_api_message(item: dict[str, Any]) -> dict[str, Any] | None:
    message = _as_text(item.get("tekstmelding") or item.get("message") or item.get("melding"))
    if not message:
        return None
    service_id = _as_text(item.get("dienstid") or item.get("dienst_id") or item.get("dienst"))
    region = _region_id(item)
    capcodes = _capcodes(item)
    result = {
        "message": message,
        "melding": _as_text(item.get("melding") or message),
        "tekstmelding": message,
        "capcode": capcodes[0] if capcodes else "",
        "capcodes": capcodes,
        "regio": region,
        "regio_name": REGIONS.get(region, _as_text(item.get("regio_name") or item.get("region_name") or item.get("regio"))),
        "discipline": SERVICES.get(service_id, _as_text(item.get("discipline") or item.get("dienst"))),
        "dienstid": service_id,
        "latitude": item.get("latitude", item.get("lat", "")),
        "longitude": item.get("longitude", item.get("lon", "")),
        "published": _published(item),
        "source": "alarmeringdroid",
        "city": _as_text(item.get("plaats") or item.get("city") or item.get("woonplaats")),
    }
    result["message_id"] = P2000DataCoordinator._generate_message_id(result)
    return result


class P2000DataCoordinator(DataUpdateCoordinator):
    """Fetch the complete AlarmeringDroid feed and filter it locally."""

    def __init__(self, hass: HomeAssistant, exclude_capcodes=None, incident_window=DEFAULT_INCIDENT_WINDOW, api_filter=None):
        self.exclude_capcodes = {str(x).strip() for x in (exclude_capcodes or []) if str(x).strip()}
        self.incident_window = incident_window
        self.api_filter = {k: v for k, v in (api_filter or {}).items() if v not in (None, "", [], {})}
        self._seen_ids = set()
        self._seen_order = deque(maxlen=MAX_SEEN_MESSAGES)
        self._initialized = False
        self._incidents = deque(maxlen=MAX_INCIDENT_HISTORY)
        self._storage_loaded = False
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL))

    @staticmethod
    def _generate_message_id(data):
        raw = "|".join(str(data.get(k, "")) for k in ("published", "capcode", "message", "regio", "discipline"))
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    async def async_load_incident_history(self):
        if self._storage_loaded:
            return
        self._storage_loaded = True
        try:
            stored = await self._store.async_load()
        except Exception as err:
            _LOGGER.warning("Kon opgeslagen P2000-historie niet laden: %s", err)
            return
        for raw in (stored or {}).get("incidents", []):
            if not isinstance(raw, dict) or raw.get("test"):
                continue
            incident = dict(raw)
            incident["_normalized_text"] = normalize_incident_text(incident.get("message", ""))
            try:
                last_seen = datetime.fromisoformat(incident.get("last_seen", ""))
                last_seen = last_seen if last_seen.tzinfo else last_seen.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                last_seen = datetime.now(timezone.utc)
            incident["_last_seen_dt"] = last_seen
            incident.setdefault("capcodes", [])
            incident.setdefault("message_ids", [])
            incident.setdefault("alarm_count", len(incident["capcodes"]))
            self._incidents.append(incident)

    async def async_save_incident_history(self):
        try:
            await self._store.async_save({"incidents": [self._public_incident(x) for x in self._incidents if not x.get("test")][:MAX_INCIDENT_HISTORY]})
        except Exception as err:
            _LOGGER.warning("Kon P2000-incidenthistorie niet opslaan: %s", err)

    def _remember_message_id(self, message_id):
        if not message_id:
            return
        if len(self._seen_order) >= MAX_SEEN_MESSAGES:
            self._seen_ids.discard(self._seen_order[0])
        self._seen_order.append(message_id)
        self._seen_ids.add(message_id)

    def _is_excluded(self, message):
        return bool(set(message.get("capcodes", [])) & self.exclude_capcodes)

    def _passes_filters(self, message):
        regions = {str(x) for x in self.api_filter.get("regios", [])}
        services = {str(x) for x in self.api_filter.get("diensten", [])}
        capcodes = {str(x) for x in self.api_filter.get("capcodes", [])}
        priorities = {str(x).upper().replace(" ", "") for x in self.api_filter.get("priorities", [])}
        includes = [str(x).lower() for x in self.api_filter.get("include_text", []) if str(x).strip()]
        excludes = [str(x).lower() for x in self.api_filter.get("exclude_text", []) if str(x).strip()]
        if regions and str(message.get("regio", "")) not in regions:
            return False
        if services and str(message.get("dienstid", "")) not in services:
            return False
        if capcodes and not (set(message.get("capcodes", [])) & capcodes):
            return False
        if priorities:
            priority = extract_priority(message.get("message", ""))
            if not priority or priority.upper() not in priorities:
                return False
        lower = str(message.get("message", "")).lower()
        if includes and not all(x in lower for x in includes):
            return False
        if excludes and any(x in lower for x in excludes):
            return False
        return True

    @staticmethod
    def _priority_rank(priority):
        match = re.search(r"([1-5])", str(priority or ""))
        return int(match.group(1)) if match else 999

    def _find_matching_incident(self, message, now):
        normalized = normalize_incident_text(message.get("message", ""))
        for incident in self._incidents:
            last_seen = incident.get("_last_seen_dt")
            if not last_seen or (now - last_seen).total_seconds() > self.incident_window:
                continue
            if str(incident.get("regio", "")) != str(message.get("regio", "")):
                continue
            if str(incident.get("discipline", "")).lower() != str(message.get("discipline", "")).lower():
                continue
            old = incident.get("_normalized_text", "")
            if old == normalized or text_similarity(old, normalized) >= 0.70:
                return incident
        return None

    def _create_incident(self, message, now, seed=None):
        normalized = normalize_incident_text(message.get("message", ""))
        seed_text = "|".join(str(x) for x in (seed or now.isoformat(), message.get("regio", ""), message.get("discipline", ""), normalized))
        return {
            "incident_id": hashlib.sha256(seed_text.encode()).hexdigest()[:16],
            "message": message.get("message", ""),
            "priority": extract_priority(message.get("message", "")),
            "regio": message.get("regio", ""),
            "regio_name": message.get("regio_name", ""),
            "discipline": message.get("discipline", ""),
            "latitude": message.get("latitude", ""),
            "longitude": message.get("longitude", ""),
            "first_seen": now.isoformat(),
            "last_seen": now.isoformat(),
            "alarm_count": len(message.get("capcodes", [])),
            "capcodes": list(message.get("capcodes", [])),
            "message_ids": [message["message_id"]] if message.get("message_id") else [],
            "test": bool(message.get("test", False)),
            "_normalized_text": normalized,
            "_last_seen_dt": now,
        }

    def _merge_message_into_incident(self, incident, message, now):
        changed = False
        incident["last_seen"] = now.isoformat()
        incident["_last_seen_dt"] = now
        for capcode in message.get("capcodes", []):
            if capcode not in incident["capcodes"]:
                incident["capcodes"].append(capcode)
                changed = True
        incident["alarm_count"] = len(incident["capcodes"])
        message_id = message.get("message_id")
        if message_id and message_id not in incident["message_ids"]:
            incident["message_ids"].append(message_id)
        new_priority = extract_priority(message.get("message", ""))
        if new_priority and self._priority_rank(new_priority) < self._priority_rank(incident.get("priority")):
            incident["priority"] = new_priority
            incident["message"] = message.get("message", incident["message"])
            incident["_normalized_text"] = normalize_incident_text(incident["message"])
            changed = True
        return changed

    @staticmethod
    def _public_incident(incident):
        return {k: v for k, v in incident.items() if not k.startswith("_")}

    def _current_incidents(self):
        return [self._public_incident(x) for x in self._incidents]

    def _messages_match(self, first, second):
        if str(first.get("regio", "")) != str(second.get("regio", "")):
            return False
        if str(first.get("discipline", "")).lower() != str(second.get("discipline", "")).lower():
            return False
        a = normalize_incident_text(first.get("message", ""))
        b = normalize_incident_text(second.get("message", ""))
        return a == b or text_similarity(a, b) >= 0.70

    def _process_live_batch(self, messages):
        groups, changes = [], []
        for message in messages:
            group = next((g for g in groups if self._messages_match(g[0], message)), None)
            if group is None:
                groups.append([message])
            else:
                group.append(message)
        for group in groups:
            now = datetime.now(timezone.utc)
            incident = self._find_matching_incident(group[0], now)
            if incident:
                changed = False
                for message in group:
                    changed = self._merge_message_into_incident(incident, message, now) or changed
                if changed:
                    changes.append({"action": "updated", "message": dict(group[0]), "incident": self._public_incident(incident)})
            else:
                incident = self._create_incident(group[0], now)
                for message in group[1:]:
                    self._merge_message_into_incident(incident, message, now)
                self._incidents.appendleft(incident)
                changes.append({"action": "new", "message": dict(group[0]), "incident": self._public_incident(incident)})
        return changes

    async def async_inject_test_messages(self, messages):
        accepted = []
        batch_id = hashlib.sha256(datetime.now(timezone.utc).isoformat().encode()).hexdigest()[:12]
        for index, original in enumerate(messages):
            message = dict(original)
            message["published"] = f"TEST-{batch_id}-{index}"
            message["test"] = True
            message.setdefault("capcodes", [str(message.get("capcode"))] if message.get("capcode") else [])
            message["message_id"] = self._generate_message_id(message)
            if not self._is_excluded(message) and self._passes_filters(message):
                accepted.append(message)
        changes = self._process_live_batch(accepted)
        current = list((self.data or {}).get("messages") or [])
        ids = {x.get("message_id") for x in accepted}
        combined = accepted + [m for m in current if m.get("message_id") not in ids]
        data = dict(self.data or {})
        data.update({"latest": combined[0] if combined else data.get("latest"), "messages": combined, "new_messages": accepted, "incident_changes": changes, "incidents": self._current_incidents()})
        self.async_set_updated_data(data)

    async def _fetch_api(self):
        _LOGGER.info("P2000 API: fetching complete unfiltered feed; local filters=%s", self.api_filter)
        headers = {"User-Agent": "P2000-Monitor/0.5.7 Home Assistant", "Accept": "application/json, text/plain, */*"}
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(API_URL, timeout=aiohttp.ClientTimeout(total=API_TIMEOUT)) as response:
                response.raise_for_status()
                raw = await response.json(content_type=None)
        items = raw.get("meldingen") if isinstance(raw, dict) else None
        if items is None and isinstance(raw, dict):
            items = raw.get("items")
        if not isinstance(items, list):
            raise ValueError("AlarmeringDroid API returned no 'meldingen'/'items' list")
        messages = []
        for item in items:
            if isinstance(item, dict):
                message = normalize_api_message(item)
                if message:
                    messages.append(message)
        _LOGGER.info("P2000 API returned %s messages; normalized %s", len(items), len(messages))
        return messages

    async def _async_update_data(self):
        await self.async_load_incident_history()
        try:
            messages = await self._fetch_api()
        except Exception as err:
            _LOGGER.exception("AlarmeringDroid API update mislukt")
            raise UpdateFailed(f"AlarmeringDroid API update mislukt: {err}") from err
        original_count = len(messages)
        messages = [m for m in messages if not self._is_excluded(m)]
        messages = [m for m in messages if self._passes_filters(m)]
        messages.sort(key=lambda m: self._parse_published(m.get("published")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        latest = messages[0] if messages else None
        _LOGGER.info("P2000 after local filters: %s messages (from %s)", len(messages), original_count)
        if not self._initialized:
            for message in messages:
                self._remember_message_id(message.get("message_id", ""))
            self._initialized = True
            return {"latest": latest, "messages": messages, "new_messages": [], "incident_changes": [], "incidents": self._current_incidents()}
        now = datetime.now(timezone.utc)
        live_horizon = max(self.incident_window, 900)
        live_new = []
        for message in messages:
            published = self._parse_published(message.get("published"))
            age = (now - published).total_seconds() if published else 0
            if published is not None and (age < -300 or age > live_horizon):
                continue
            message_id = message.get("message_id", "")
            if not message_id or message_id in self._seen_ids:
                continue
            self._remember_message_id(message_id)
            live_new.append(message)
            _LOGGER.info("P2000 new message: %s | %s | %s | regio %s/%s", message.get("published", ""), message.get("discipline", ""), message.get("message", ""), message.get("regio", ""), message.get("regio_name", ""))
        incident_changes = self._process_live_batch(live_new)
        if incident_changes:
            await self.async_save_incident_history()
        return {"latest": latest, "messages": messages, "new_messages": live_new, "incident_changes": incident_changes, "incidents": self._current_incidents()}

    @staticmethod
    def _parse_published(value):
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
