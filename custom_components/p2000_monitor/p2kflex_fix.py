"""Compatibility fixes for the P2KFlex feed.

The P2KFlex mobile feed only exposes a time-of-day in the rendered HTML.
The previous coordinator included the inferred calendar date in message IDs.
When the same historical row was parsed with a different inferred date, it
looked like a brand-new message.  This module replaces the message-ID
algorithm with a date-independent key based on the actual visible message
identity.
"""

from __future__ import annotations

import hashlib
import re


def stable_message_id(data: dict) -> str:
    """Return a stable ID that does not depend on inferred calendar date."""

    message = re.sub(r"\s+", " ", str(data.get("message", "")).strip()).lower()
    region = str(data.get("regio", "")).strip()
    discipline = str(data.get("discipline", "")).strip().lower()
    capcode = str(data.get("capcode", "")).strip()
    city = str(data.get("city", "")).strip().lower()

    # The feed only supplies a time-of-day. Keep that part of the identity,
    # but deliberately leave the inferred date out of the key.
    published = str(data.get("published", ""))
    time_part = ""
    match = re.search(r"T(\d{2}:\d{2}:\d{2})", published)
    if match:
        time_part = match.group(1)

    raw = "|".join(
        (region, discipline, city, time_part, capcode, message)
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def apply_patch() -> None:
    """Patch the coordinator before Home Assistant creates the platform."""

    from . import coordinator

    coordinator.P2000DataCoordinator._generate_message_id = staticmethod(
        stable_message_id
    )

    # The parser calls the class method above, so newly parsed feed rows use
    # the stable ID automatically. Existing stored incidents remain valid.
