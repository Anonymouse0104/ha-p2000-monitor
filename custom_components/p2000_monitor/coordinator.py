"""AlarmeringDroid API coordinator for P2000 Monitor."""
from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
from datetime import datetime, timedelta
import hashlib
import json
import logging
import re
from typing import Any
from urllib.parse import quote

import aiohttp
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .enrich import (
    SERVICE_POLICE,
    capcode_details,
    distance_km,
    extract_vehicles,
    is_police_alert,
    normalize_vehicle,
    parse_priority,
    to_float,
)

from .const import (
    DEFAULT_INCIDENT_WINDOW,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    EVENT_FILTER_MATCH,
    EVENT_INCIDENT_UPDATE,
    EVENT_NEW_INCIDENT,
    EVENT_NEW_MESSAGE,
    MAX_BACKOFF,
    MAX_EVENT_AGE,
    MAX_HISTORY,
    MAX_INCIDENT_HISTORY,
    MAX_SEEN_MESSAGES,
    STORAGE_KEY,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)
API_URL = "https://beta.alarmeringdroid.nl/api2/find/"
API_TIMEOUT = 15
LOCAL_TZ = dt_util.get_time_zone("Europe/Amsterdam")


def storage_key(storage_id: str) -> str:
    """Per-sensor storage key so sensors never overwrite each other's history."""
    return f"{STORAGE_KEY}.{storage_id}"


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
    """Priority label (P1-P5, A1, A2, B) of a pager or message text."""
    return parse_priority(text)[0]


def message_urgency(message: dict[str, Any]) -> int | None:
    """Urgency 1-5 of a message (1 = most urgent), also for injected test messages."""
    if message.get("urgency"):
        return message["urgency"]
    return parse_priority(message.get("pager_text") or message.get("message") or "")[1]


def message_vehicles(message: dict[str, Any]) -> set[str]:
    return {v["id"] if isinstance(v, dict) else normalize_vehicle(v) for v in message.get("vehicles") or []}


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


def normalize_capcode(value: Any) -> str:
    """Normalize a capcode so '001005258', ' 1005258' and 1005258 compare equal."""
    text = str(value if value is not None else "").strip()
    return text.lstrip("0") or ("0" if text else "")


def _capcodes(item: dict[str, Any]) -> list[str]:
    value = item.get("capcodes", item.get("codes", item.get("capcode", item.get("code", []))))
    if isinstance(value, str):
        value = re.split(r"[,;\s]+", value)
    elif not isinstance(value, list):
        value = [value] if value else []
    result = []
    for entry in value:
        if isinstance(entry, dict):
            entry = entry.get("capcode") or entry.get("code") or entry.get("id")
        text = normalize_capcode(_as_text(entry))
        if text and text not in result:
            result.append(text)
    return result


def _message_capcodes(message: dict[str, Any]) -> set[str]:
    """All capcodes of a normalized message, including a lone 'capcode' value."""
    codes = {normalize_capcode(x) for x in message.get("capcodes") or []}
    if message.get("capcode"):
        codes.add(normalize_capcode(message["capcode"]))
    codes.discard("")
    return codes


# Police alerts carry a police unit ("Eenheid Limburg") instead of a
# veiligheidsregio, under the id of one of its regions (Limburg = 21). Map each
# unit to all veiligheidsregio's it covers, so region filters work for police.
POLICE_UNITS = {
    "noord-nederland": ["2", "7", "12"],
    "oost-nederland": ["3", "8", "13", "17", "23"],
    "midden-nederland": ["18", "19", "27"],
    "noord-holland": ["4", "9", "24"],
    "amsterdam": ["1"],
    "den haag": ["5", "25"],
    "rotterdam": ["10", "14"],
    "zeeland-west-brabant": ["20", "26"],
    "oost-brabant": ["6", "11"],
    "limburg": ["15", "21"],
}


def police_unit_regions(region_text: str) -> list[str] | None:
    """Veiligheidsregio ids covered by a police unit name, or None if not a unit."""
    text = region_text.strip().lower()
    if not text.startswith("eenheid"):
        return None
    return POLICE_UNITS.get(text.removeprefix("eenheid").strip())


def message_regions(message: dict[str, Any]) -> set[str]:
    """All veiligheidsregio ids an alert belongs to (several for police units)."""
    regions = set(message.get("regions") or [])
    if message.get("regio"):
        regions.add(str(message["regio"]))
    return regions


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
    """Return the publication time as a UTC ISO string, or "" when unknown.

    The feed reports local Dutch time as separate 'datum' (dd-mm) and 'tijd'
    (hh:mm) fields; full timestamps are honoured when present.
    """
    for key in ("published", "datetime", "datumtijd", "timestamp", "date"):
        value = _as_text(item.get(key))
        if not value:
            continue
        if value.replace(".", "", 1).isdigit():
            try:
                return dt_util.utc_from_timestamp(float(value)).isoformat()
            except (OverflowError, OSError, ValueError):
                continue
        dt = dt_util.parse_datetime(value)
        if dt is None:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=LOCAL_TZ)
        return dt_util.as_utc(dt).isoformat()
    date_match = re.search(r"(\d{1,2})-(\d{1,2})(?:-(\d{4}))?", _as_text(item.get("datum")))
    time_match = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", _as_text(item.get("tijd")))
    if date_match and time_match:
        day, month = int(date_match.group(1)), int(date_match.group(2))
        hour, minute = int(time_match.group(1)), int(time_match.group(2))
        second = int(time_match.group(3) or 0)
        now = dt_util.now(LOCAL_TZ)
        year = int(date_match.group(3) or now.year)
        try:
            dt = datetime(year, month, day, hour, minute, second, tzinfo=LOCAL_TZ)
            if not date_match.group(3) and dt - now > timedelta(days=2):
                dt = dt.replace(year=now.year - 1)
            return dt_util.as_utc(dt).isoformat()
        except ValueError:
            pass
    # Never fall back to now(): that would make the message look new on every poll.
    return ""


def parse_published(value: Any) -> datetime | None:
    """Parse a normalized 'published' value into an aware UTC datetime."""
    if not value:
        return None
    dt = dt_util.parse_datetime(str(value))
    if dt is None:
        return None
    return dt_util.as_utc(dt if dt.tzinfo else dt.replace(tzinfo=dt_util.UTC))


def message_sort_key(message: dict[str, Any]) -> tuple[float, int]:
    """Sort key for newest-first ordering: publication time, then API id.

    The feed only has minute resolution, so the monotonically increasing API
    id breaks ties between messages published in the same minute.
    """
    published = parse_published(message.get("published"))
    api_id = str(message.get("api_id") or "")
    return (published.timestamp() if published else 0.0, int(api_id) if api_id.isdigit() else 0)


def _flatten_items(items: list[Any]) -> list[dict[str, Any]]:
    """Return feed items including the alarms nested under 'subitems'."""
    result: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        result.append(item)
        subitems = item.get("subitems")
        if isinstance(subitems, dict):
            subitems = list(subitems.values())
        if isinstance(subitems, list):
            result.extend(_flatten_items(subitems))
    return result


def normalize_api_message(item: dict[str, Any]) -> dict[str, Any] | None:
    message = _as_text(item.get("tekstmelding") or item.get("message") or item.get("melding"))
    if not message:
        return None
    service_id = _as_text(item.get("dienstid") or item.get("dienst_id") or item.get("dienst"))
    region = _region_id(item)
    capcodes = _capcodes(item)
    pager_text = _as_text(item.get("melding")) or message
    priority, urgency = parse_priority(pager_text, item.get("prio1"))
    region_text = _as_text(item.get("regio_name") or item.get("region_name") or item.get("regio"))
    unit_regions = police_unit_regions(region_text)
    details = capcode_details(item.get("capcodes"), normalize_capcode)
    if service_id != SERVICE_POLICE and is_police_alert(capcodes, details):
        # Misfiled police alert (e.g. a Limburg press alarm marked as fire
        # brigade): use the police service and the real pager text instead of
        # the generated "Brandweer met ... spoed naar ..." text.
        service_id = SERVICE_POLICE
        message = pager_text
    result = {
        "api_id": _as_text(item.get("id")),
        "message": message,
        "melding": pager_text,
        "tekstmelding": message,
        "pager_text": pager_text,
        "priority": priority,
        "urgency": urgency,
        "grip": int(_as_text(item.get("grip")) or 0) if _as_text(item.get("grip")).isdigit() else 0,
        "vehicles": [{"id": v} for v in extract_vehicles(pager_text, service_id)],
        "capcode_details": details,
        "street": _as_text(item.get("straat")),
        "postcode": _as_text(item.get("postcode")),
        "capcode": capcodes[0] if capcodes else "",
        "capcodes": capcodes,
        "regio": region,
        # Police units keep their own name ("Eenheid Limburg"), not a region's.
        "regio_name": region_text if unit_regions else REGIONS.get(region, region_text),
        "regions": unit_regions or ([region] if region else []),
        "discipline": SERVICES.get(service_id, _as_text(item.get("discipline") or item.get("dienst"))),
        "dienstid": service_id,
        "latitude": item.get("latitude", item.get("lat", "")),
        "longitude": item.get("longitude", item.get("lon", "")),
        "published": _published(item),
        "source": "alarmeringdroid",
        "city": _as_text(item.get("plaats") or item.get("city") or item.get("woonplaats")),
    }
    result["message_id"] = generate_message_id(result)
    return result


def generate_message_id(data: dict[str, Any]) -> str:
    """Return a stable id for a normalized message."""
    # The API id is stable across polls; only fall back to a content hash without it.
    if data.get("api_id"):
        return f"ad-{data['api_id']}"
    raw = "|".join(str(data.get(k, "")) for k in ("published", "message", "regio", "discipline"))
    raw += "|" + ",".join(data.get("capcodes") or [str(data.get("capcode", ""))])
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


class P2000FeedCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll the complete AlarmeringDroid feed once for all config entries.

    Filtering and incident tracking happen per entry in P2000FilterProcessor,
    so adding sensors never adds API requests.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        self._seen_ids: set[str] = set()
        self._seen_order: deque[str] = deque()
        self._initialized = False
        self.setup_lock = asyncio.Lock()
        self.last_success_time: datetime | None = None
        super().__init__(
            hass,
            _LOGGER,
            config_entry=None,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )

    def _remember_message_id(self, message_id: str) -> None:
        if not message_id or message_id in self._seen_ids:
            return
        if len(self._seen_order) >= MAX_SEEN_MESSAGES:
            self._seen_ids.discard(self._seen_order.popleft())
        self._seen_order.append(message_id)
        self._seen_ids.add(message_id)

    async def async_fetch_recent(self, query: dict[str, list[str]]) -> list[dict[str, Any]]:
        """The latest ~30 alerts matching an API-side filter, however old.

        The API accepts a JSON filter with 'regios', 'diensten' and 'capcodes'
        appended to the URL. Used to fill a sensor's history at startup.
        """
        payload = json.dumps({k: [str(x) for x in v] for k, v in query.items() if v}, separators=(",", ":"))
        return await self._fetch_api(API_URL + quote(payload, safe=""))

    async def _fetch_api(self, url: str = API_URL) -> list[dict[str, Any]]:
        headers = {"User-Agent": "P2000-Monitor Home Assistant", "Accept": "application/json, text/plain, */*"}
        session = async_get_clientsession(self.hass)
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=API_TIMEOUT)) as response:
            response.raise_for_status()
            raw = await response.json(content_type=None)
        if isinstance(raw, list):
            items = raw
        elif isinstance(raw, dict):
            items = raw.get("meldingen")
            if items is None:
                items = raw.get("items")
        else:
            items = None
        if not isinstance(items, list):
            raise ValueError("AlarmeringDroid API returned no 'meldingen'/'items' list")
        messages = []
        for item in _flatten_items(items):
            message = normalize_api_message(item)
            if message:
                self._add_distance(message)
                messages.append(message)
        _LOGGER.debug("P2000 API returned %s items; normalized %s messages", len(items), len(messages))
        return messages

    def _add_distance(self, message: dict[str, Any]) -> None:
        """Distance from the Home Assistant home location, for the radius filter."""
        lat, lon = to_float(message.get("latitude")), to_float(message.get("longitude"))
        home_lat, home_lon = self.hass.config.latitude, self.hass.config.longitude
        if lat is None or lon is None or home_lat is None or home_lon is None:
            message["distance_km"] = None
        else:
            message["distance_km"] = round(distance_km(home_lat, home_lon, lat, lon), 1)

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            messages = await self._fetch_api()
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            # Back off while the API is down; the coordinator itself logs the
            # failure once and the recovery once, so no traceback per poll.
            self.update_interval = min(self.update_interval * 2, MAX_BACKOFF)
            raise UpdateFailed(f"AlarmeringDroid API update mislukt: {err!r}") from err
        self.update_interval = timedelta(seconds=DEFAULT_SCAN_INTERVAL)
        self.last_success_time = dt_util.utcnow()
        messages.sort(key=message_sort_key, reverse=True)
        new_messages = self._detect_new(messages)
        for message in reversed(new_messages):  # oldest first
            self.hass.bus.async_fire(EVENT_NEW_MESSAGE, {"message": message})
        return {"messages": messages, "new_messages": new_messages}

    def _detect_new(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return messages whose id was never seen before, newest first.

        Every id in the feed is remembered, whatever its age, so a message is
        reported exactly once no matter how many arrive within one interval.
        The first poll only primes the cache: the feed's backlog is not new.
        """
        if not self._initialized:
            for message in messages:
                self._remember_message_id(message["message_id"])
            self._initialized = True
            return []
        now = dt_util.utcnow()
        new_messages = []
        for message in messages:
            message_id = message["message_id"]
            if message_id in self._seen_ids:
                continue
            self._remember_message_id(message_id)
            published = parse_published(message.get("published"))
            if published is not None and (now - published).total_seconds() > MAX_EVENT_AGE:
                # Seen for the first time after a long outage: too stale to alarm on.
                _LOGGER.debug("P2000 skipping stale message %s from %s", message_id, published)
                continue
            new_messages.append(message)
            _LOGGER.debug("P2000 new message: %s | %s | %s | regio %s/%s", message.get("published", ""), message.get("discipline", ""), message.get("message", ""), message.get("regio", ""), message.get("regio_name", ""))
        return new_messages


class P2000FilterProcessor:
    """Apply one sensor's filters to the shared feed and track its incidents."""

    def __init__(
        self,
        hass: HomeAssistant,
        feed: P2000FeedCoordinator,
        storage_id: str,
        *,
        entry_id: str | None = None,
        subentry_id: str | None = None,
        exclude_capcodes=None,
        incident_window=DEFAULT_INCIDENT_WINDOW,
        api_filter=None,
        match_any_unit: bool = False,
        vehicle_db=None,
    ) -> None:
        self.match_any_unit = match_any_unit
        # Optional Brandbase lookup that adds names to fire vehicles.
        self.vehicle_db = vehicle_db
        self.hass = hass
        self.feed = feed
        self.storage_id = storage_id
        self.entry_id = entry_id
        self.subentry_id = subentry_id
        # Set by the sensor once it is added, so events can name it.
        self.entity_id: str | None = None
        # When this filter last accepted a new alert (drives "active" sensors).
        self.last_match_time: datetime | None = None
        self.exclude_capcodes = {normalize_capcode(x) for x in (exclude_capcodes or []) if normalize_capcode(x)}
        self.incident_window = incident_window
        self.api_filter = {k: v for k, v in (api_filter or {}).items() if v not in (None, "", [], {})}
        # Pre-normalized once instead of on every message.
        self._filters = {
            "regions": {str(x) for x in self.api_filter.get("regios", [])},
            "services": {str(x) for x in self.api_filter.get("diensten", [])},
            "capcodes": {normalize_capcode(x) for x in self.api_filter.get("capcodes", []) if normalize_capcode(x)},
            # P1-P5 as urgency 1-5, so P1 also matches ambulance A1 (and P3 matches B).
            "priorities": {
                int(m.group(1))
                for x in self.api_filter.get("priorities", [])
                if (m := re.search(r"([1-5])", str(x)))
            },
            "vehicles": {normalize_vehicle(x) for x in self.api_filter.get("vehicles", []) if str(x).strip()},
            "radius_km": float(self.api_filter.get("radius_km") or 0),
            "include": [str(x).strip().lower() for x in self.api_filter.get("include_text", []) if str(x).strip()],
            "exclude": [str(x).strip().lower() for x in self.api_filter.get("exclude_text", []) if str(x).strip()],
        }
        self.data: dict[str, Any] = {}
        self._incidents: deque[dict[str, Any]] = deque(maxlen=MAX_INCIDENT_HISTORY)
        # Accepted messages by id, newest first, kept beyond the short API feed window.
        self._history: dict[str, dict[str, Any]] = {}
        self._store = Store(hass, STORAGE_VERSION, storage_key(storage_id))
        self._listeners: list[Callable[[], None]] = []
        self._unsub_feed: Callable[[], None] | None = None
        self._unsub_vehicles: Callable[[], None] | None = None
        self._last_feed_data: dict[str, Any] | None = None
        self._backfill_task: asyncio.Task | None = None
        self._history_loaded = False

    async def async_start(self) -> None:
        """Load stored incidents, build the initial view and follow the feed."""
        await self.async_load_incident_history()
        if self.vehicle_db:
            # Before the first _apply: that may start a lookup which can finish
            # immediately (tasks start eagerly), and its result must reach us.
            self._unsub_vehicles = self.vehicle_db.async_add_listener(self._handle_vehicle_names)
        self._last_feed_data = self.feed.data
        self._apply(self.feed.data or {}, process_new=False)
        self._unsub_feed = self.feed.async_add_listener(self._handle_feed_update)
        if query := self._backfill_query():
            self._backfill_task = self.hass.async_create_background_task(
                self._async_backfill(query), f"p2000_monitor backfill {self.storage_id}"
            )

    def _backfill_query(self) -> dict[str, list[str]] | None:
        """API-side filter for this sensor's latest alerts, or None if not useful.

        Without region/service/capcode filters the live feed already holds the
        newest alerts. Police units are filed under one of their regions, so
        all regions of a selected unit are asked for; local filters narrow it.
        """
        f = self._filters
        regions = set(f["regions"])
        for unit in POLICE_UNITS.values():
            if regions & set(unit):
                regions |= set(unit)
        query = {"regios": sorted(regions), "diensten": sorted(f["services"])}
        if f["capcodes"] and not (self.match_any_unit and f["vehicles"]):
            # A watch on capcodes OR vehicles cannot be expressed in the API.
            query["capcodes"] = sorted(f["capcodes"])
        return query if any(query.values()) else None

    async def _async_backfill(self, query: dict[str, list[str]]) -> None:
        """Fill the history with the latest matching alerts, however old.

        The live feed only covers the last ~30 national alerts, so a sensor for
        a quiet region would otherwise stay empty after a restart or when it is
        created. Backfilled alerts are not new: they fire no events.
        """
        try:
            messages = await self.feed.async_fetch_recent(query)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            _LOGGER.debug("P2000 backfill for %s failed: %s", self.storage_id, err)
            return
        accepted = [m for m in messages if self._accepts(m)]
        if self.vehicle_db:
            for message in accepted:
                self.vehicle_db.enrich(message)
        if self._merge_history(accepted):
            self._store.async_delay_save(self._data_to_save, 10)
            history = list(self._history.values())
            self.data = {**self.data, "latest": history[0], "messages": history, "new_messages": [], "incident_changes": []}
            self._notify()

    @callback
    def _handle_vehicle_names(self) -> None:
        """Brandbase delivered a region: name the vehicles of stored alerts too."""
        for message in self._history.values():
            self.vehicle_db.enrich(message)
        self._notify()

    async def async_stop(self) -> None:
        """Stop following the feed and write any pending history right away.

        A delayed save is only flushed automatically at HA shutdown, not when an
        entry unloads for a reload, so it would otherwise be lost.
        """
        if self._unsub_feed:
            self._unsub_feed()
            self._unsub_feed = None
        if self._unsub_vehicles:
            self._unsub_vehicles()
            self._unsub_vehicles = None
        if self._backfill_task:
            self._backfill_task.cancel()
            self._backfill_task = None
        # Never overwrite stored history with an empty one after a failed load.
        if self._history_loaded:
            await self._store.async_save(self._data_to_save())

    @callback
    def async_add_listener(self, update_callback: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(update_callback)

        @callback
        def remove_listener() -> None:
            self._listeners.remove(update_callback)

        return remove_listener

    @callback
    def _notify(self) -> None:
        for update_callback in list(self._listeners):
            update_callback()

    @callback
    def _handle_feed_update(self) -> None:
        # Listeners also run after a failed refresh, with the previous data still
        # in place; only process each feed snapshot once so events never repeat.
        if self.feed.data is not None and self.feed.data is not self._last_feed_data:
            self._last_feed_data = self.feed.data
            self._apply(self.feed.data, process_new=True)
        self._notify()

    def _accepts(self, message: dict[str, Any]) -> bool:
        return not self._is_excluded(message) and self._passes_filters(message)

    def _merge_history(self, messages: list[dict[str, Any]]) -> bool:
        """Add accepted messages to this entry's own history; return whether it changed.

        The API feed only holds the latest ~30 national messages (often under
        half an hour), so the sensor keeps its own history instead of showing
        only what is still in the feed.
        """
        added = False
        for message in messages:
            message_id = message.get("message_id")
            if not message_id:
                continue
            if message_id not in self._history:
                added = True
            # Replace known ones too, so later enrichment (vehicle names) shows up.
            self._history[message_id] = message
        if added:
            ordered = sorted(self._history.values(), key=message_sort_key, reverse=True)[:MAX_HISTORY]
            self._history = {m["message_id"]: m for m in ordered}
        return added

    @callback
    def _apply(self, feed_data: dict[str, Any], process_new: bool) -> None:
        accepted = [m for m in feed_data.get("messages") or [] if self._accepts(m)]
        if self.vehicle_db:
            # Only accepted alerts, so Brandbase is asked only for regions you follow.
            for message in accepted:
                self.vehicle_db.enrich(message)
        new_messages = [m for m in feed_data.get("new_messages") or [] if self._accepts(m)] if process_new else []
        _LOGGER.debug("P2000 after local filters: %s messages (from %s)", len(accepted), len(feed_data.get("messages") or []))
        history_changed = self._merge_history(accepted)
        incident_changes = self._process_live_batch(new_messages)
        if history_changed or incident_changes:
            self._store.async_delay_save(self._data_to_save, 10)
        messages = list(self._history.values())
        self.data = {
            "latest": messages[0] if messages else None,
            "messages": messages,
            "new_messages": new_messages,
            "incident_changes": incident_changes,
            "incidents": self._current_incidents(),
        }
        self._fire_events(new_messages, incident_changes)

    @callback
    def _fire_events(self, new_messages: list[dict[str, Any]], incident_changes: list[dict[str, Any]]) -> None:
        """Fire one filter-match event per accepted new message, plus incident events."""
        if new_messages:
            self.last_match_time = dt_util.utcnow()
        base = {"entry_id": self.entry_id, "subentry_id": self.subentry_id, "sensor": self.entity_id}
        for message in reversed(new_messages):  # oldest first
            self.hass.bus.async_fire(
                EVENT_FILTER_MATCH, {**base, "message": message, "filter": self.api_filter}
            )
        for change in incident_changes:
            event = EVENT_NEW_INCIDENT if change.get("action") == "new" else EVENT_INCIDENT_UPDATE
            self.hass.bus.async_fire(
                event,
                {**base, "incident": change.get("incident", {}), "message": change.get("message", {})},
            )

    async def async_load_incident_history(self) -> None:
        try:
            stored = await self._store.async_load()
        except Exception as err:
            _LOGGER.warning("Kon opgeslagen P2000-historie niet laden: %s", err)
            return
        self._history_loaded = True
        for raw in (stored or {}).get("incidents", []):
            if not isinstance(raw, dict) or raw.get("test"):
                continue
            incident = dict(raw)
            incident["_normalized_text"] = normalize_incident_text(incident.get("message", ""))
            incident["_last_seen_dt"] = parse_published(incident.get("last_seen")) or dt_util.utcnow()
            incident.setdefault("capcodes", [])
            incident.setdefault("message_ids", [])
            incident.setdefault("alarm_count", len(incident["capcodes"]))
            self._incidents.append(incident)
        # Re-check stored messages: the filters may have changed in the options flow.
        self._merge_history(
            [
                m
                for m in (stored or {}).get("messages", [])
                if isinstance(m, dict) and m.get("message_id") and not m.get("test") and self._accepts(m)
            ]
        )

    @callback
    def _data_to_save(self) -> dict[str, Any]:
        return {
            "incidents": [self._public_incident(x) for x in self._incidents if not x.get("test")][:MAX_INCIDENT_HISTORY],
            "messages": [m for m in self._history.values() if not m.get("test")],
        }

    def _is_excluded(self, message):
        return bool(_message_capcodes(message) & self.exclude_capcodes)

    def _passes_filters(self, message):
        """OR between the values of one filter, AND between filters.

        In 'any unit' mode (capcode/vehicle watches) capcodes and vehicles
        together form one OR filter: either alerting the unit is enough.
        """
        f = self._filters
        if f["regions"] and not (message_regions(message) & f["regions"]):
            return False
        if f["services"] and str(message.get("dienstid", "")) not in f["services"]:
            return False
        capcode_hit = bool(f["capcodes"] and _message_capcodes(message) & f["capcodes"])
        vehicle_hit = bool(f["vehicles"] and message_vehicles(message) & f["vehicles"])
        if self.match_any_unit:
            if (f["capcodes"] or f["vehicles"]) and not (capcode_hit or vehicle_hit):
                return False
        else:
            if f["capcodes"] and not capcode_hit:
                return False
            if f["vehicles"] and not vehicle_hit:
                return False
        if f["priorities"]:
            urgency = message_urgency(message)
            if urgency is None or urgency not in f["priorities"]:
                return False
        if f["radius_km"]:
            distance = message.get("distance_km")
            if distance is None or distance > f["radius_km"]:
                return False
        if f["include"] or f["exclude"]:
            # Match both the readable text and the pager text ("BR woning").
            lower = f"{message.get('message', '')} {message.get('pager_text', '')}".lower()
            if f["include"] and not any(x in lower for x in f["include"]):
                return False
            if any(x in lower for x in f["exclude"]):
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
            now = dt_util.utcnow()
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
        batch_id = hashlib.sha256(dt_util.utcnow().isoformat().encode()).hexdigest()[:12]
        for index, original in enumerate(messages):
            message = dict(original)
            message["published"] = f"TEST-{batch_id}-{index}"
            message["test"] = True
            message.setdefault("capcodes", [str(message.get("capcode"))] if message.get("capcode") else [])
            message["message_id"] = generate_message_id(message)
            if self._accepts(message):
                accepted.append(message)
        changes = self._process_live_batch(accepted)
        current = list(self.data.get("messages") or [])
        ids = {x.get("message_id") for x in accepted}
        combined = accepted + [m for m in current if m.get("message_id") not in ids]
        self.data = {
            **self.data,
            "latest": combined[0] if combined else self.data.get("latest"),
            "messages": combined,
            "new_messages": accepted,
            "incident_changes": changes,
            "incidents": self._current_incidents(),
        }
        self._fire_events(accepted, changes)
        self._notify()
