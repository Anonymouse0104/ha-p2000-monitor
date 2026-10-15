"""Derive priority, vehicles, capcode descriptions and distance from P2000 alerts."""
from __future__ import annotations

from math import asin, cos, radians, sin, sqrt
import re
from typing import Any

# Brandweer pager texts end with the call signs of the alerted units as six
# digits: region (veiligheidsregio 01-28) + unit, e.g. 243231 -> 24-3231.
_FIRE_UNIT = re.compile(r"(?<![\d-])(0[1-9]|1\d|2[0-8])(\d{4})(?![\d-])")
# Ambulance texts name the unit as five digits: region + unit, e.g. 17103 -> 17-103.
_AMBULANCE_UNIT = re.compile(r"(?<![\d-])(0[1-9]|1\d|2[0-8])(\d{3})(?![\d-])")
# Ride and receipt numbers that look like unit numbers.
_NOT_A_UNIT = re.compile(r"\b(?:rit|bon|ritnummer)\s*:?\s*\d+", re.IGNORECASE)

_P_PRIORITY = re.compile(r"\b(?:P|PRIO)\s*([1-5])\b", re.IGNORECASE)
_AMBULANCE_PRIORITY = re.compile(r"^\s*(A\s*[12]|B\s*[12]?)\b", re.IGNORECASE)

SERVICE_POLICE = "1"
SERVICE_FIRE = "2"
SERVICE_AMBULANCE = "3"

# Capcodes that only the police uses but that AlarmeringDroid files under
# another service (and then generates a fire brigade text for). P2Kflex labels
# them by capcode, e.g. 1033216 = "Politie Eenheid Limburg (Persalarm District
# Noord en Midden)". Extend when other regions show the same.
POLICE_CAPCODES = {
    "1033215",  # Politie Eenheid Limburg, persalarm District Parkstad/Zuid-West
    "1033216",  # Politie Eenheid Limburg, persalarm District Noord en Midden
}
_POLICE_DESCRIPTION = re.compile(r"\b(politie|persinfo|persalarm)\b", re.IGNORECASE)


def is_police_alert(capcodes: list[str], details: list[dict[str, str]]) -> bool:
    """True when every capcode of an alert belongs to the police."""
    if not capcodes:
        return False
    descriptions = {d["capcode"]: d.get("description", "") for d in details}
    return all(
        code in POLICE_CAPCODES or _POLICE_DESCRIPTION.search(descriptions.get(code, ""))
        for code in capcodes
    )


def parse_priority(pager_text: str, prio1: Any = None) -> tuple[str | None, int | None]:
    """Return (label, urgency) where urgency 1 is the most urgent.

    Fire and police use P1-P5; ambulances use A1 (urgency 1), A2 (2) and B (3).
    The API's prio1 flag fills in when the text has no priority.
    """
    text = str(pager_text or "")
    if match := _AMBULANCE_PRIORITY.match(text):
        label = match.group(1).upper().replace(" ", "")
        return label, {"A1": 1, "A2": 2}.get(label, 3)
    if match := _P_PRIORITY.search(text):
        return f"P{match.group(1)}", int(match.group(1))
    if str(prio1) == "1":
        return "P1", 1
    return None, None


def normalize_vehicle(value: Any) -> str:
    """Normalize a call sign: '240733', '24-0733' and ' 24 0733 ' -> '24-0733'."""
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) == 6:
        return f"{digits[:2]}-{digits[2:]}"
    if len(digits) == 5:
        return f"{digits[:2]}-{digits[2:]}"
    return str(value or "").strip().upper()


def extract_vehicles(pager_text: str, service_id: str) -> list[str]:
    """Return the call signs of the units named in a pager text."""
    text = _NOT_A_UNIT.sub(" ", str(pager_text or ""))
    if service_id == SERVICE_FIRE:
        pattern = _FIRE_UNIT
    elif service_id == SERVICE_AMBULANCE:
        pattern = _AMBULANCE_UNIT
    else:
        return []
    vehicles: list[str] = []
    for match in pattern.finditer(text):
        call_sign = f"{match.group(1)}-{match.group(2)}"
        if call_sign not in vehicles:
            vehicles.append(call_sign)
    return vehicles


def capcode_details(raw_capcodes: Any, normalize) -> list[dict[str, str]]:
    """Return [{capcode, description}] from the API's capcode objects."""
    details: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in raw_capcodes if isinstance(raw_capcodes, list) else []:
        if not isinstance(entry, dict):
            continue
        code = normalize(entry.get("capcode") or entry.get("code") or "")
        if not code or code in seen:
            continue
        seen.add(code)
        description = str(entry.get("omschrijving") or entry.get("description") or "").strip()
        details.append({"capcode": code, "description": "" if description in ("???", "Onbekend") else description})
    return details


def to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * 6371.0 * asin(sqrt(a))
