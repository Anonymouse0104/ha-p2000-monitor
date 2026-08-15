"""Data coordinator for P2000 Monitor."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
import hashlib
import logging
import re
import asyncio
import uuid
from html import unescape

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

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

P2KFLEX_URL = "https://m.p2kflex.nl/engine.php"
P2KFLEX_INDEX_URL = "https://m.p2kflex.nl/index.php"
P2KFLEX_REGIONS = ("23", "24")
P2KFLEX_TIMEOUT = 20


def _strip_html(value: str) -> str:
    value = unescape(str(value or ""))
    value = re.sub(r"<br\s*/?>", " ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _parse_p2kflex_html(html: str, region: str) -> list[dict]:
    """Parse the visible P2KFlex mobile table."""
    rows = re.findall(r"<tr\b[^>]*>(.*?)</tr>", html, flags=re.I | re.S)
    messages: list[dict] = []

    now_local = datetime.now().astimezone()
    current_date = now_local.date()
    previous_seconds: int | None = None
    first_seconds: int | None = None
    now_seconds = (
        now_local.hour * 3600
        + now_local.minute * 60
        + now_local.second
    )

    for row in rows:
        time_match = re.search(
            r'<td[^>]*class=["\'][^"\']*datetime[^"\']*["\'][^>]*>(.*?)</td>',
            row, flags=re.I | re.S,
        )
        msg_match = re.search(
            r'<td[^>]*class=["\']([^"\']*MSG[^"\']*)["\'][^>]*>(.*?)</td>',
            row, flags=re.I | re.S,
        )
        if not msg_match:
            continue

        message = _strip_html(msg_match.group(2))
        if not message:
            continue

        time_text = _strip_html(time_match.group(1)) if time_match else ""
        hm = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$", time_text)
        if not hm:
            continue

        seconds = (
            int(hm.group(1)) * 3600
            + int(hm.group(2)) * 60
            + int(hm.group(3) or 0)
        )

        # P2KFlex returns newest first. If the first row is later than the
        # current clock, it belongs to yesterday. Every later increase while
        # walking down the table means another midnight was crossed.
        if first_seconds is None:
            first_seconds = seconds
            if seconds > now_seconds + 300:
                current_date -= timedelta(days=1)
        elif previous_seconds is not None and seconds > previous_seconds:
            current_date -= timedelta(days=1)
        previous_seconds = seconds

        published_local = datetime.combine(
            current_date,
            datetime.min.time(),
        ).replace(
            hour=int(hm.group(1)),
            minute=int(hm.group(2)),
            second=int(hm.group(3) or 0),
            tzinfo=now_local.tzinfo,
        )

        cell_class = msg_match.group(1).lower()
        if "msgbrw" in cell_class:
            discipline = "Brandweer"
        elif "msgamb" in cell_class:
            discipline = "Ambulance"
        elif "msgpol" in cell_class:
            discipline = "Politie"
        elif "msginc" in cell_class:
            discipline = "Incident"
        else:
            discipline = ""

        city_match = re.search(
            r'data-cityname=["\']([^"\']+)["\']',
            row, flags=re.I,
        )
        city = unescape(city_match.group(1)).strip() if city_match else ""

        data = {
            "message": message,
            "capcode": "",
            "regio": region,
            "regio_name": (
                "Brandweer Limburg-Noord" if region == "23"
                else "Brandweer Zuid-Limburg"
            ),
            "discipline": discipline,
            "latitude": "",
            "longitude": "",
            "published": published_local.astimezone(timezone.utc).isoformat(),
            "source": "p2kflex",
            "city": city,
        }
        data["message_id"] = P2000DataCoordinator._generate_message_id(data)
        messages.append(data)

    unique: list[dict] = []
    seen: set[str] = set()
    for item in messages:
        if item["message_id"] in seen:
            continue
        seen.add(item["message_id"])
        unique.append(item)
    return unique


def normalize_incident_text(text: str) -> str:
    """Normalize a P2000 message for incident comparison."""

    text = str(text).lower()

    # Prioriteit P1 t/m P5 verwijderen.
    text = re.sub(
        r"\bp\s*[1-5]\b",
        " ",
        text,
    )

    # Codes zoals BLB-03, BNH-01, OMS-01 enz.
    text = re.sub(
        r"\b[a-z]{2,5}-\d{1,3}\b",
        " ",
        text,
    )

    # Lange numerieke rit-/incidentnummers.
    text = re.sub(
        r"\b\d{5,8}\b",
        " ",
        text,
    )

    # Leestekens normaliseren.
    text = re.sub(
        r"[^a-z0-9\s]",
        " ",
        text,
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


def extract_priority(text: str) -> str | None:
    """Extract P1 through P5."""

    match = re.search(
        r"\bP\s*([1-5])\b",
        str(text).upper(),
    )

    if not match:
        return None

    return f"P{match.group(1)}"


def text_similarity(
    first: str,
    second: str,
) -> float:
    """Calculate word-overlap similarity."""

    first_words = set(
        first.split()
    )

    second_words = set(
        second.split()
    )

    if not first_words or not second_words:
        return 0.0

    union = (
        first_words
        | second_words
    )

    if not union:
        return 0.0

    intersection = (
        first_words
        & second_words
    )

    return (
        len(intersection)
        /
        len(union)
    )


class P2000DataCoordinator(
    DataUpdateCoordinator
):
    """Fetch P2000 data and build central incidents."""

    def __init__(
        self,
        hass: HomeAssistant,
        exclude_capcodes: list[str] | None = None,
        incident_window: int = DEFAULT_INCIDENT_WINDOW,
    ) -> None:
        """Initialize coordinator."""

        self.exclude_capcodes = {
            str(capcode).strip()
            for capcode in (
                exclude_capcodes
                or []
            )
            if str(capcode).strip()
        }

        self.incident_window = (
            incident_window
        )

        self._seen_ids = set()

        self._seen_order = deque(
            maxlen=MAX_SEEN_MESSAGES
        )

        self._initialized = False

        self._incidents = deque(
            maxlen=MAX_INCIDENT_HISTORY
        )

        # ---------------------------------------------------------
        # PERSISTENT STORAGE
        # ---------------------------------------------------------

        self._store = Store(
            hass,
            STORAGE_VERSION,
            STORAGE_KEY,
        )

        self._storage_loaded = False

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=DEFAULT_SCAN_INTERVAL
            ),
        )

    # =============================================================
    # STORAGE
    # =============================================================

    async def async_load_incident_history(
        self,
    ) -> None:
        """Load persisted incident history once."""

        if self._storage_loaded:
            return

        self._storage_loaded = True

        try:

            stored = await (
                self._store.async_load()
            )

        except Exception as err:

            _LOGGER.warning(
                "Kon opgeslagen P2000-historie "
                "niet laden: %s",
                err,
            )

            return

        if not stored:

            return

        stored_incidents = (
            stored.get(
                "incidents",
                [],
            )
        )

        restored = []

        for stored_incident in (
            stored_incidents
        ):

            if not isinstance(
                stored_incident,
                dict,
            ):
                continue

            # Testincidenten horen nooit
            # persistent opgeslagen te zijn.
            if stored_incident.get(
                "test",
                False,
            ):
                continue

            incident = dict(
                stored_incident
            )

            incident[
                "_normalized_text"
            ] = normalize_incident_text(
                incident.get(
                    "message",
                    "",
                )
            )

            try:

                last_seen = (
                    datetime.fromisoformat(
                        incident.get(
                            "last_seen",
                            "",
                        )
                    )
                )

                if (
                    last_seen.tzinfo
                    is None
                ):

                    last_seen = (
                        last_seen.replace(
                            tzinfo=timezone.utc
                        )
                    )

            except (
                TypeError,
                ValueError,
            ):

                last_seen = (
                    datetime.now(
                        timezone.utc
                    )
                )

            incident[
                "_last_seen_dt"
            ] = last_seen

            # Zorg dat oudere opgeslagen data
            # ook met nieuwe versies werkt.
            incident.setdefault(
                "capcodes",
                [],
            )

            incident.setdefault(
                "message_ids",
                [],
            )

            incident.setdefault(
                "alarm_count",
                len(
                    incident.get(
                        "capcodes",
                        [],
                    )
                ),
            )

            incident[
                "test"
            ] = False

            restored.append(
                incident
            )

        self._incidents = deque(
            restored[
                :MAX_INCIDENT_HISTORY
            ],
            maxlen=MAX_INCIDENT_HISTORY,
        )

        _LOGGER.info(
            "%s opgeslagen P2000-incidenten geladen",
            len(
                self._incidents
            ),
        )

    async def async_save_incident_history(
        self,
    ) -> None:
        """Persist non-test incident history."""

        incidents = []

        for incident in self._incidents:

            if incident.get(
                "test",
                False,
            ):
                continue

            incidents.append(
                self._public_incident(
                    incident
                )
            )

        try:

            await self._store.async_save(
                {
                    "incidents":
                        incidents[
                            :MAX_INCIDENT_HISTORY
                        ],
                }
            )

        except Exception as err:

            _LOGGER.warning(
                "Kon P2000-incidenthistorie "
                "niet opslaan: %s",
                err,
            )

    # =============================================================
    # MESSAGE HELPERS
    # =============================================================

    @staticmethod
    def _generate_message_id(
        data: dict,
    ) -> str:
        """Generate stable unique message ID."""

        raw = "|".join(
            [
                str(
                    data.get(
                        "published",
                        "",
                    )
                ),
                str(
                    data.get(
                        "capcode",
                        "",
                    )
                ),
                str(
                    data.get(
                        "message",
                        "",
                    )
                ),
                str(
                    data.get(
                        "regio",
                        "",
                    )
                ),
                str(
                    data.get(
                        "discipline",
                        "",
                    )
                ),
            ]
        )

        return hashlib.sha256(
            raw.encode(
                "utf-8"
            )
        ).hexdigest()[:24]

    @staticmethod
    def _normalize_entry(entry) -> dict:
        """Normalize an already parsed P2KFlex message."""
        data = dict(entry)
        data["message"] = str(data.get("message", "")).strip()
        data["message_id"] = P2000DataCoordinator._generate_message_id(data)
        return data

    def _remember_message_id(
        self,
        message_id: str,
    ) -> None:
        """Remember message ID."""

        if not message_id:
            return

        if (
            len(
                self._seen_order
            )
            >= MAX_SEEN_MESSAGES
        ):

            oldest = (
                self._seen_order[
                    0
                ]
            )

            self._seen_ids.discard(
                oldest
            )

        self._seen_order.append(
            message_id
        )

        self._seen_ids.add(
            message_id
        )

    def _is_excluded(
        self,
        message: dict,
    ) -> bool:
        """Check whether message capcode is excluded."""

        capcode = str(
            message.get(
                "capcode",
                "",
            )
        ).strip()

        return (
            bool(capcode)
            and
            capcode
            in self.exclude_capcodes
        )

    # =============================================================
    # INCIDENT HELPERS
    # =============================================================

    @staticmethod
    def _priority_rank(
        priority: str | None,
    ) -> int:
        """Return numeric priority rank."""

        if not priority:
            return 999

        match = re.search(
            r"([1-5])",
            str(priority),
        )

        if not match:
            return 999

        return int(
            match.group(1)
        )

    def _find_matching_incident(
        self,
        message: dict,
        now: datetime,
    ) -> dict | None:
        """Find recent matching incident."""

        normalized = (
            normalize_incident_text(
                message.get(
                    "message",
                    "",
                )
            )
        )

        regio = str(
            message.get(
                "regio",
                "",
            )
        )

        discipline = str(
            message.get(
                "discipline",
                "",
            )
        ).lower()

        for incident in self._incidents:

            # Testincidenten mogen nooit
            # met echte incidenten mengen.
            if incident.get(
                "test",
                False,
            ):
                continue

            last_seen = (
                incident.get(
                    "_last_seen_dt"
                )
            )

            if not last_seen:
                continue

            age = (
                now
                -
                last_seen
            ).total_seconds()

            if (
                age
                >
                self.incident_window
            ):
                continue

            if (
                str(
                    incident.get(
                        "regio",
                        "",
                    )
                )
                !=
                regio
            ):
                continue

            if (
                str(
                    incident.get(
                        "discipline",
                        "",
                    )
                ).lower()
                !=
                discipline
            ):
                continue

            incident_text = (
                incident.get(
                    "_normalized_text",
                    "",
                )
            )

            if (
                incident_text
                ==
                normalized
            ):
                return incident

            if (
                text_similarity(
                    incident_text,
                    normalized,
                )
                >= 0.70
            ):
                return incident

        return None

    def _create_incident(
        self,
        message: dict,
        now: datetime,
        force_id_seed: str | None = None,
    ) -> dict:
        """Create a new central incident."""

        normalized = (
            normalize_incident_text(
                message.get(
                    "message",
                    "",
                )
            )
        )

        if force_id_seed:

            raw_id = "|".join(
                [
                    force_id_seed,
                    now.isoformat(),
                    str(
                        message.get(
                            "regio",
                            "",
                        )
                    ),
                    str(
                        message.get(
                            "discipline",
                            "",
                        )
                    ),
                    normalized,
                ]
            )

        else:

            raw_id = "|".join(
                [
                    now.isoformat(),
                    str(
                        message.get(
                            "regio",
                            "",
                        )
                    ),
                    str(
                        message.get(
                            "discipline",
                            "",
                        )
                    ),
                    normalized,
                ]
            )

        incident_id = (
            hashlib.sha256(
                raw_id.encode(
                    "utf-8"
                )
            )
            .hexdigest()[:16]
        )

        capcode = str(
            message.get(
                "capcode",
                "",
            )
        ).strip()

        capcodes = []

        if capcode:

            capcodes.append(
                capcode
            )

        message_id = (
            message.get(
                "message_id"
            )
        )

        message_ids = []

        if message_id:

            message_ids.append(
                message_id
            )

        incident = {
            "incident_id":
                incident_id,

            "message":
                message.get(
                    "message",
                    "",
                ),

            "priority":
                extract_priority(
                    message.get(
                        "message",
                        "",
                    )
                ),

            "regio":
                message.get(
                    "regio",
                    "",
                ),

            "regio_name":
                message.get(
                    "regio_name",
                    "",
                ),

            "discipline":
                message.get(
                    "discipline",
                    "",
                ),

            "latitude":
                message.get(
                    "latitude",
                    "",
                ),

            "longitude":
                message.get(
                    "longitude",
                    "",
                ),

            "first_seen":
                now.isoformat(),

            "last_seen":
                now.isoformat(),

            "alarm_count":
                len(
                    capcodes
                ),

            "capcodes":
                capcodes,

            "message_ids":
                message_ids,

            "test":
                bool(
                    message.get(
                        "test",
                        False,
                    )
                ),

            "_normalized_text":
                normalized,

            "_last_seen_dt":
                now,
        }

        return incident

    def _merge_message_into_incident(
        self,
        incident: dict,
        message: dict,
        now: datetime,
    ) -> bool:
        """
        Merge message into incident.

        Returns True when the incident meaningfully changed.
        """

        changed = False

        incident[
            "last_seen"
        ] = now.isoformat()

        incident[
            "_last_seen_dt"
        ] = now

        capcode = str(
            message.get(
                "capcode",
                "",
            )
        ).strip()

        if (
            capcode
            and
            capcode
            not in incident[
                "capcodes"
            ]
        ):

            incident[
                "capcodes"
            ].append(
                capcode
            )

            changed = True

        # alarm_count is altijd het aantal
        # unieke geaccepteerde capcodes.
        incident[
            "alarm_count"
        ] = len(
            incident[
                "capcodes"
            ]
        )

        message_id = (
            message.get(
                "message_id"
            )
        )

        if (
            message_id
            and
            message_id
            not in incident[
                "message_ids"
            ]
        ):

            incident[
                "message_ids"
            ].append(
                message_id
            )

        if message.get(
            "test",
            False,
        ):

            incident[
                "test"
            ] = True

        new_priority = (
            extract_priority(
                message.get(
                    "message",
                    "",
                )
            )
        )

        old_priority = (
            incident.get(
                "priority"
            )
        )

        if (
            new_priority
            and
            self._priority_rank(
                new_priority
            )
            <
            self._priority_rank(
                old_priority
            )
        ):

            incident[
                "priority"
            ] = new_priority

            incident[
                "message"
            ] = message.get(
                "message",
                incident[
                    "message"
                ],
            )

            incident[
                "_normalized_text"
            ] = normalize_incident_text(
                incident[
                    "message"
                ]
            )

            changed = True

        if (
            not incident.get(
                "latitude"
            )
            and
            message.get(
                "latitude"
            )
        ):

            incident[
                "latitude"
            ] = message.get(
                "latitude"
            )

            changed = True

        if (
            not incident.get(
                "longitude"
            )
            and
            message.get(
                "longitude"
            )
        ):

            incident[
                "longitude"
            ] = message.get(
                "longitude"
            )

            changed = True

        return changed

    @staticmethod
    def _public_incident(
        incident: dict,
    ) -> dict:
        """Return public incident data."""

        return {
            key: value
            for key, value
            in incident.items()
            if not key.startswith(
                "_"
            )
        }

    def _current_incidents(
        self,
    ) -> list[dict]:
        """Return current public incidents."""

        return [
            self._public_incident(
                incident
            )
            for incident
            in self._incidents
        ]

    # =============================================================
    # BATCH GROUPING
    # =============================================================

    def _messages_match(
        self,
        first: dict,
        second: dict,
    ) -> bool:
        """Check if two messages belong to same batch incident."""

        if (
            str(
                first.get(
                    "regio",
                    "",
                )
            )
            !=
            str(
                second.get(
                    "regio",
                    "",
                )
            )
        ):
            return False

        if (
            str(
                first.get(
                    "discipline",
                    "",
                )
            ).lower()
            !=
            str(
                second.get(
                    "discipline",
                    "",
                )
            ).lower()
        ):
            return False

        first_text = (
            normalize_incident_text(
                first.get(
                    "message",
                    "",
                )
            )
        )

        second_text = (
            normalize_incident_text(
                second.get(
                    "message",
                    "",
                )
            )
        )

        if first_text == second_text:
            return True

        return (
            text_similarity(
                first_text,
                second_text,
            )
            >= 0.70
        )

    def _group_messages(
        self,
        messages: list[dict],
    ) -> list[list[dict]]:
        """Group same-poll messages into incident batches."""

        groups = []

        for message in messages:

            matching_group = None

            for group in groups:

                if self._messages_match(
                    group[0],
                    message,
                ):

                    matching_group = group
                    break

            if matching_group is None:

                groups.append(
                    [
                        message
                    ]
                )

            else:

                matching_group.append(
                    message
                )

        return groups

    # =============================================================
    # LIVE INCIDENT PROCESSING
    # =============================================================

    def _process_live_batch(
        self,
        messages: list[dict],
    ) -> list[dict]:
        """Process live messages as grouped batches."""

        incident_changes = []

        groups = self._group_messages(
            messages
        )

        for group in groups:

            if not group:
                continue

            now = datetime.now(
                timezone.utc
            )

            first_message = group[0]

            existing = (
                self._find_matching_incident(
                    first_message,
                    now,
                )
            )

            if existing:

                meaningful_change = False

                for message in group:

                    if (
                        self._merge_message_into_incident(
                            existing,
                            message,
                            now,
                        )
                    ):

                        meaningful_change = True

                # Alleen incident_update wanneer
                # er werkelijk iets nieuws is.
                if meaningful_change:

                    incident_changes.append(
                        {
                            "action":
                                "updated",

                            "message":
                                dict(
                                    first_message
                                ),

                            "incident":
                                self._public_incident(
                                    existing
                                ),
                        }
                    )

            else:

                incident = (
                    self._create_incident(
                        first_message,
                        now,
                    )
                )

                # Rest van dezelfde batch eerst
                # volledig toevoegen.
                for message in group[1:]:

                    self._merge_message_into_incident(
                        incident,
                        message,
                        now,
                    )

                self._incidents.appendleft(
                    incident
                )

                # Eén new_incident per gegroepeerd incident.
                incident_changes.append(
                    {
                        "action":
                            "new",

                        "message":
                            dict(
                                first_message
                            ),

                        "incident":
                            self._public_incident(
                                incident
                            ),
                    }
                )

        return incident_changes

    # =============================================================
    # TEST INCIDENT PROCESSING
    # =============================================================

    def _process_test_batch(
        self,
        messages: list[dict],
        test_batch_id: str,
    ) -> list[dict]:
        """Process one isolated test injection."""

        if not messages:
            return []

        groups = self._group_messages(
            messages
        )

        incident_changes = []

        for group_index, group in enumerate(
            groups
        ):

            if not group:
                continue

            now = datetime.now(
                timezone.utc
            )

            first_message = group[0]

            incident = (
                self._create_incident(
                    first_message,
                    now,
                    force_id_seed=(
                        f"{test_batch_id}-"
                        f"{group_index}"
                    ),
                )
            )

            for message in group[1:]:

                self._merge_message_into_incident(
                    incident,
                    message,
                    now,
                )

            # Testincident blijft tijdelijk beschikbaar
            # voor sensoren/events, maar wordt niet
            # persistent opgeslagen.
            self._incidents.appendleft(
                incident
            )

            incident_changes.append(
                {
                    "action":
                        "new",

                    "message":
                        dict(
                            first_message
                        ),

                    "incident":
                        self._public_incident(
                            incident
                        ),
                }
            )

        return incident_changes

    async def async_inject_test_messages(
        self,
        messages: list[dict],
    ) -> None:
        """Immediately process one isolated test batch."""

        accepted_messages = []

        test_batch_id = (
            uuid.uuid4().hex
        )

        for index, original in enumerate(
            messages
        ):

            message = dict(
                original
            )

            # Iedere testbatch krijgt unieke
            # published-data zodat message_id's
            # nooit botsen met eerdere tests.
            message[
                "published"
            ] = (
                f"TEST-"
                f"{test_batch_id}-"
                f"{index}"
            )

            message[
                "test"
            ] = True

            message[
                "message_id"
            ] = (
                self._generate_message_id(
                    message
                )
            )

            if self._is_excluded(
                message
            ):
                continue

            accepted_messages.append(
                message
            )

        incident_changes = (
            self._process_test_batch(
                accepted_messages,
                test_batch_id,
            )
        )

        current_data = dict(
            self.data
            or {}
        )

        if accepted_messages:

            current_data[
                "latest"
            ] = dict(
                accepted_messages[
                    0
                ]
            )

        current_data[
            "new_messages"
        ] = [
            dict(message)
            for message
            in accepted_messages
        ]

        current_data[
            "incident_changes"
        ] = incident_changes

        current_data[
            "incidents"
        ] = (
            self._current_incidents()
        )

        # Testdata bewust NIET opslaan.
        self.async_set_updated_data(
            current_data
        )

    # =============================================================
    # FEED UPDATE
    # =============================================================

    async def _fetch_p2kflex_region(
        self,
        session: aiohttp.ClientSession,
        region: str,
    ) -> list[dict]:
        """Fetch one Limburg region directly from P2KFlex."""
        sid_cookie = session.cookie_jar.filter_cookies(P2KFLEX_INDEX_URL).get("fp_sid")
        sid = sid_cookie.value if sid_cookie else uuid.uuid4().hex[:24]
        params = {
            "id": "0",
            "rt": "1",
            "sid": sid,
            "classic": "0",
            "date": datetime.now().astimezone().strftime(
                "%a %b %d %Y %H:%M:%S GMT%z"
            ),
            "regio": f"{region}2;",
            "capcode": "none",
            "city": "0",
            "include": "none",
            "exclude": "none",
        }
        async with session.get(
            P2KFLEX_URL,
            params=params,
            timeout=aiohttp.ClientTimeout(total=P2KFLEX_TIMEOUT),
        ) as response:
            response.raise_for_status()
            html = await response.text(errors="replace")

        messages = _parse_p2kflex_html(html, region)
        _LOGGER.debug(
            "P2KFlex regio %s: %s berichten ontvangen",
            region,
            len(messages),
        )
        return messages

    async def _async_update_data(self):
        """Fetch live Limburg data directly from P2KFlex."""
        try:
            await self.async_load_incident_history()

            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/151 Safari/537.36"
                ),
                "Accept": "*/*",
                "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
                "Referer": P2KFLEX_INDEX_URL,
            }

            async with aiohttp.ClientSession(headers=headers) as session:
                # Start a mobile session first, matching the browser flow.
                try:
                    async with session.get(
                        P2KFLEX_INDEX_URL,
                        timeout=aiohttp.ClientTimeout(total=P2KFLEX_TIMEOUT),
                    ) as index_response:
                        await index_response.read()
                except Exception as err:
                    _LOGGER.debug(
                        "P2KFlex index initialisatie mislukt: %s",
                        err,
                    )

                results = await asyncio.gather(
                    *(
                        self._fetch_p2kflex_region(session, region)
                        for region in P2KFLEX_REGIONS
                    ),
                    return_exceptions=True,
                )

            normalized_messages: list[dict] = []
            errors = []
            for region, result in zip(P2KFLEX_REGIONS, results):
                if isinstance(result, Exception):
                    errors.append(f"regio {region}: {result}")
                    continue
                for message in result:
                    if not self._is_excluded(message):
                        normalized_messages.append(message)

            if errors and not normalized_messages:
                raise UpdateFailed(
                    "P2KFlex gaf geen bruikbare data terug: "
                    + "; ".join(errors)
                )

            # Keep newest first for sensor.p2000_monitor.
            normalized_messages.sort(
                key=lambda item: self._parse_published(item.get("published"))
                or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            latest = normalized_messages[0] if normalized_messages else None

            if not self._initialized:
                for message in normalized_messages:
                    self._remember_message_id(message["message_id"])
                self._initialized = True
                return {
                    "latest": latest,
                    "new_messages": [],
                    "incident_changes": [],
                    "incidents": self._current_incidents(),
                }

            live_new: list[dict] = []
            now = datetime.now(timezone.utc)
            live_horizon = max(self.incident_window, 900)

            for message in normalized_messages:
                published = self._parse_published(message.get("published"))
                if published is not None:
                    age = (now - published).total_seconds()
                    # Historical rows must never be promoted to new incidents.
                    if age < -300 or age > live_horizon:
                        continue

                message_id = message.get("message_id", "")
                if not message_id or message_id in self._seen_ids:
                    continue

                self._remember_message_id(message_id)
                live_new.append(message)

            incident_changes = self._process_live_batch(live_new)

            if incident_changes:
                await self.async_save_incident_history()

            return {
                "latest": latest,
                "new_messages": live_new,
                "incident_changes": incident_changes,
                "incidents": self._current_incidents(),
            }

        except UpdateFailed:
            raise
        except Exception as err:
            _LOGGER.exception("P2KFlex update mislukt")
            raise UpdateFailed(
                f"P2KFlex update mislukt: {err}"
            ) from err

    @staticmethod
    def _parse_published(value) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except (TypeError, ValueError):
            return None

