"""Vehicle names from Brandbase (brandbase.hetbrandweerforum.nl).

Fire brigade pager texts name units by call sign (24-0733); Brandbase lists the
vehicle type and post per call sign, one page per veiligheidsregio. This module
fetches those pages politely:

- only regions that actually appear in alerts your sensors accept,
- each region at most once a week (failures retried after a day),
- one request at a time with at least REQUEST_DELAY seconds in between,
- a descriptive User-Agent, results cached in .storage across restarts.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
import html
import logging
import re
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://brandbase.hetbrandweerforum.nl"
INDEX_URL = f"{BASE_URL}/voertuigen/regios/"
USER_AGENT = "P2000-Monitor-HomeAssistant (+https://github.com/Anonymouse0104/ha-p2000-monitor)"
REFRESH_AFTER = timedelta(days=7)
RETRY_AFTER = timedelta(days=1)
# robots.txt asks for a crawl delay of 10 seconds; stay above it.
REQUEST_DELAY = 15
TIMEOUT = aiohttp.ClientTimeout(total=30)
STORAGE_KEY = "p2000_monitor.brandbase"
STORAGE_VERSION = 1

_REGION_LINK = re.compile(r'href="(https://brandbase\.hetbrandweerforum\.nl/voertuigen/regio/(\d{2})-[^"/]+/)"')
_VEHICLE_LINK = re.compile(
    r'<a href="(https://brandbase\.hetbrandweerforum\.nl/voertuigen/[^"]+)">\s*(\d{2}-\d{4})\s+([^<]*)</a>'
)
# Indirection so tests can skip the real delay.
_sleep = asyncio.sleep
_POST_HEADING = re.compile(r"^\s*(\d{2}-\d{2})\s+(.*)$")


def parse_index(page: str) -> dict[str, str]:
    """Region number -> region page URL."""
    return {number: url for url, number in _REGION_LINK.findall(page)}


def parse_region(page: str) -> dict[str, dict[str, str]]:
    """Call sign -> {type, post, station, url} from a region page."""
    vehicles: dict[str, dict[str, str]] = {}
    for section in page.split("<h3>")[1:]:
        heading, _, body = section.partition("</h3>")
        heading = html.unescape(re.sub(r"<[^>]+>", "", heading)).strip()
        post, station = "", heading
        if match := _POST_HEADING.match(heading):
            post, station = match.group(1), match.group(2).strip()
        body = body.split("<h3>", 1)[0]
        for url, call_sign, vehicle_type in _VEHICLE_LINK.findall(body):
            vehicles.setdefault(
                call_sign,
                {"type": html.unescape(vehicle_type).strip(), "post": post, "station": station, "url": url},
            )
    return vehicles


class BrandbaseVehicles:
    """Cached, lazily filled lookup of fire brigade call signs."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._data: dict[str, Any] = {"index": {}, "regions": {}}
        self._pending: set[str] = set()
        self._worker: asyncio.Task | None = None
        self._listeners: list = []

    async def async_load(self) -> None:
        stored = await self._store.async_load()
        if isinstance(stored, dict):
            self._data = {"index": stored.get("index") or {}, "regions": stored.get("regions") or {}}

    @callback
    def async_add_listener(self, update_callback) -> callback:
        """Called when new vehicle data arrived, so views can refresh."""
        self._listeners.append(update_callback)
        return lambda: self._listeners.remove(update_callback)

    async def async_stop(self) -> None:
        if self._worker:
            self._worker.cancel()
            self._worker = None
        self._pending.clear()

    @callback
    def enrich(self, message: dict[str, Any]) -> None:
        """Add type/post/station to the fire vehicles of an alert; queue unknown regions."""
        if message.get("dienstid") != "2":
            return
        for vehicle in message.get("vehicles") or []:
            if not isinstance(vehicle, dict) or "type" in vehicle:
                continue
            call_sign = vehicle.get("id", "")
            region = call_sign[:2]
            # Request first: a lookup may complete right away (tasks start eagerly).
            self._request(region)
            info = (self._data["regions"].get(region) or {}).get("vehicles", {}).get(call_sign)
            if info:
                vehicle.update(info)

    def _region_due(self, region: str) -> bool:
        state = self._data["regions"].get(region)
        if not state:
            return True
        checked = dt_util.parse_datetime(state.get("checked", ""))
        wait = REFRESH_AFTER if state.get("ok") else RETRY_AFTER
        return checked is None or dt_util.utcnow() - checked > wait

    @callback
    def _request(self, region: str) -> None:
        if not region.isdigit() or region in self._pending or not self._region_due(region):
            return
        self._pending.add(region)
        if self._worker is None or self._worker.done():
            self._worker = self.hass.async_create_background_task(self._run(), "p2000_monitor brandbase")

    async def _fetch(self, url: str) -> str:
        session = async_get_clientsession(self.hass)
        async with session.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT) as response:
            response.raise_for_status()
            return await response.text()

    async def _run(self) -> None:
        """Fetch queued regions one by one, REQUEST_DELAY seconds apart."""
        first = True
        while self._pending:
            region = min(self._pending)
            if not first:
                await _sleep(REQUEST_DELAY)
            first = False
            try:
                index = self._data["index"]
                checked = dt_util.parse_datetime(index.get("checked", ""))
                if not index.get("regions") or checked is None or dt_util.utcnow() - checked > REFRESH_AFTER:
                    regions = parse_index(await self._fetch(INDEX_URL))
                    self._data["index"] = {"checked": dt_util.utcnow().isoformat(), "regions": regions}
                    await _sleep(REQUEST_DELAY)
                url = self._data["index"].get("regions", {}).get(region)
                vehicles = parse_region(await self._fetch(url)) if url else {}
                self._data["regions"][region] = {
                    "checked": dt_util.utcnow().isoformat(),
                    "ok": bool(url),
                    "vehicles": vehicles,
                }
                _LOGGER.debug("Brandbase: %s vehicles for region %s", len(vehicles), region)
            except (aiohttp.ClientError, TimeoutError) as err:
                _LOGGER.debug("Brandbase region %s not available: %s", region, err)
                previous = self._data["regions"].get(region) or {}
                self._data["regions"][region] = {**previous, "checked": dt_util.utcnow().isoformat(), "ok": False}
            self._pending.discard(region)
            self._store.async_delay_save(lambda: self._data, 5)
            for update_callback in list(self._listeners):
                update_callback()
