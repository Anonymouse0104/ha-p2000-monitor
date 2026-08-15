"""Compatibility fixes for the P2KFlex feed."""

from __future__ import annotations

import hashlib
import re
from html import unescape


def clean_visible_text(value: str) -> str:
    """Extract only visible text and discard HTML event-handler attributes."""
    text = unescape(str(value or ""))
    text = re.sub(r"<script\b[^>]*>.*?</script\s*>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style\s*>", " ", text, flags=re.I | re.S)
    text = re.sub(r"\bon(?:click|mouse(?:over|out|down|up|move|enter|leave))\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)", " ", text, flags=re.I)
    text = re.sub(r"\bhref\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)", " ", text, flags=re.I)
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def stable_message_id(data: dict) -> str:
    """Return a stable ID that does not depend on inferred calendar date."""
    message = clean_visible_text(data.get("message", "")).lower()
    region = str(data.get("regio", "")).strip()
    discipline = clean_visible_text(data.get("discipline", "")).lower()
    capcode = str(data.get("capcode", "")).strip()
    city = clean_visible_text(data.get("city", "")).lower()

    published = str(data.get("published", ""))
    time_part = ""
    match = re.search(r"T(\d{2}:\d{2}:\d{2})", published)
    if match:
        time_part = match.group(1)

    raw = "|".join((region, discipline, city, time_part, capcode, message))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def apply_patch() -> None:
    """Patch coordinator message-ID generation with the stable algorithm."""
    from . import coordinator

    coordinator.P2000DataCoordinator._generate_message_id = staticmethod(
        stable_message_id
    )
