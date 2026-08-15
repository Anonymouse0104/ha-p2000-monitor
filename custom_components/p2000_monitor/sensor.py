"""Sensor platform for P2000 Monitor."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import re

import voluptuous as vol

from homeassistant.components.sensor import (
    PLATFORM_SCHEMA,
    SensorEntity,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity_platform import (
    AddEntitiesCallback,
)
from homeassistant.helpers.typing import (
    ConfigType,
    DiscoveryInfoType,
)
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
)

from .const import (
    ATTR_TEST_CAPCODES,
    ATTR_TEST_DISCIPLINE,
    ATTR_TEST_MESSAGE,
    ATTR_TEST_REGION,
    ATTR_TEST_REGION_NAME,
    DEFAULT_INCIDENT_WINDOW,
    DOMAIN,
    EVENT_FILTER_MATCH,
    EVENT_INCIDENT_UPDATE,
    EVENT_NEW_INCIDENT,
    EVENT_NEW_MESSAGE,
    MAX_FILTER_INCIDENT_HISTORY,
    MAX_HISTORY,
    SERVICE_INJECT_TEST_INCIDENT,
)
from .coordinator import (
    P2000DataCoordinator,
    extract_priority,
)


DEFAULT_NAME = "P2000 Monitor"

CONF_FILTERS = "filters"
CONF_EXCLUDE_CAPCODES = "exclude_capcodes"
CONF_INCIDENT_WINDOW = "incident_window"

CONF_REGIONS = "regions"
CONF_DISCIPLINES = "disciplines"
CONF_PRIORITIES = "priorities"
CONF_CAPCODES = "capcodes"
CONF_INCLUDE_TEXT = "include_text"
CONF_EXCLUDE_TEXT = "exclude_text"


FILTER_SCHEMA = vol.Schema(
    {
        vol.Required(
            CONF_NAME
        ): cv.string,

        vol.Optional(
            CONF_REGIONS,
            default=[],
        ): vol.All(
            cv.ensure_list,
            [
                cv.string
            ],
        ),

        vol.Optional(
            CONF_DISCIPLINES,
            default=[],
        ): vol.All(
            cv.ensure_list,
            [
                cv.string
            ],
        ),

        vol.Optional(
            CONF_PRIORITIES,
            default=[],
        ): vol.All(
            cv.ensure_list,
            [
                cv.string
            ],
        ),

        vol.Optional(
            CONF_CAPCODES,
            default=[],
        ): vol.All(
            cv.ensure_list,
            [
                cv.string
            ],
        ),

        vol.Optional(
            CONF_INCLUDE_TEXT,
            default=[],
        ): vol.All(
            cv.ensure_list,
            [
                cv.string
            ],
        ),

        vol.Optional(
            CONF_EXCLUDE_TEXT,
            default=[],
        ): vol.All(
            cv.ensure_list,
            [
                cv.string
            ],
        ),
    }
)


PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Optional(
            CONF_NAME,
            default=DEFAULT_NAME,
        ): cv.string,

        vol.Optional(
            CONF_EXCLUDE_CAPCODES,
            default=[],
        ): vol.All(
            cv.ensure_list,
            [
                cv.string
            ],
        ),

        vol.Optional(
            CONF_INCIDENT_WINDOW,
            default=DEFAULT_INCIDENT_WINDOW,
        ): vol.All(
            vol.Coerce(int),
            vol.Range(
                min=10,
                max=1800,
            ),
        ),

        vol.Optional(
            CONF_FILTERS,
            default=[],
        ): vol.All(
            cv.ensure_list,
            [
                FILTER_SCHEMA
            ],
        ),
    }
)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up P2000 Monitor."""

    name = config.get(
        CONF_NAME,
        DEFAULT_NAME,
    )

    filters = config.get(
        CONF_FILTERS,
        [],
    )

    exclude_capcodes = (
        config.get(
            CONF_EXCLUDE_CAPCODES,
            [],
        )
    )

    incident_window = (
        config.get(
            CONF_INCIDENT_WINDOW,
            DEFAULT_INCIDENT_WINDOW,
        )
    )

    coordinator = (
        P2000DataCoordinator(
            hass=hass,
            exclude_capcodes=(
                exclude_capcodes
            ),
            incident_window=(
                incident_window
            ),
        )
    )

    await (
        coordinator
        .async_config_entry_first_refresh()
    )

    # ---------------------------------------------------------
    # TESTSERVICE
    # ---------------------------------------------------------

    async def handle_test_incident(
        call
    ):
        """Create one simulated P2000 incident batch."""

        region = str(
            call.data.get(
                ATTR_TEST_REGION,
                "24",
            )
        )

        region_name = call.data.get(
            ATTR_TEST_REGION_NAME,
            "Limburg Zuid",
        )

        discipline = call.data.get(
            ATTR_TEST_DISCIPLINE,
            "Brandweerdiensten",
        )

        message = call.data.get(
            ATTR_TEST_MESSAGE,
            (
                "P 1 BR woning Teststraat "
                "Maastricht 243231"
            ),
        )

        capcodes = call.data.get(
            ATTR_TEST_CAPCODES,
            [
                "1005258",
                "1005264",
                "1005998",
            ],
        )

        if isinstance(
            capcodes,
            str,
        ):
            capcodes = [
                capcodes
            ]

        test_messages = []

        now = datetime.now(
            timezone.utc
        )

        for index, capcode in enumerate(
            capcodes
        ):

            test_messages.append(
                {
                    "message":
                        message,

                    "capcode":
                        str(
                            capcode
                        ),

                    "regio":
                        region,

                    "regio_name":
                        region_name,

                    "discipline":
                        discipline,

                    "latitude":
                        "50.8514",

                    "longitude":
                        "5.6909",

                    "published":
                        (
                            f"TEST-PENDING-"
                            f"{now.isoformat()}-"
                            f"{index}"
                        ),

                    "test":
                        True,
                }
            )

        await (
            coordinator
            .async_inject_test_messages(
                test_messages
            )
        )

    if not hass.services.has_service(
        DOMAIN,
        SERVICE_INJECT_TEST_INCIDENT,
    ):

        hass.services.async_register(
            DOMAIN,
            SERVICE_INJECT_TEST_INCIDENT,
            handle_test_incident,
        )

    # ---------------------------------------------------------
    # ENTITIES
    # ---------------------------------------------------------

    entities = [
        P2000MonitorSensor(
            coordinator,
            name,
        )
    ]

    for index, filter_config in enumerate(
        filters
    ):

        entities.append(
            P2000FilterSensor(
                coordinator=coordinator,
                filter_config=filter_config,
                index=index,
            )
        )

    async_add_entities(
        entities
    )


class P2000MonitorSensor(
    CoordinatorEntity,
    SensorEntity,
):
    """National raw P2000 sensor."""

    _attr_has_entity_name = False

    def __init__(
        self,
        coordinator,
        name,
    ):

        super().__init__(
            coordinator
        )

        self._attr_name = name

        self._attr_unique_id = (
            "p2000_monitor_main"
        )

        self._handled_message_ids = set()

        self._handled_incident_changes = set()

    @property
    def native_value(
        self
    ):

        latest = (
            self.coordinator.data
            or {}
        ).get(
            "latest"
        )

        if not latest:

            return "Geen meldingen"

        return latest.get(
            "message",
            "Onbekende melding",
        )

    @property
    def icon(
        self
    ):

        latest = (
            self.coordinator.data
            or {}
        ).get(
            "latest"
        )

        if not latest:

            return "mdi:radio-tower"

        discipline = str(
            latest.get(
                "discipline",
                "",
            )
        )

        if discipline == "Brandweerdiensten":
            return "mdi:fire-truck"

        if discipline == "Ambulancediensten":
            return "mdi:ambulance"

        if discipline == "Politiediensten":
            return "mdi:car-emergency"

        if discipline == "Lifeliner":
            return "mdi:helicopter"

        return "mdi:radio-tower"

    @property
    def extra_state_attributes(
        self
    ):

        latest = (
            self.coordinator.data
            or {}
        ).get(
            "latest"
        )

        attributes = {}

        if latest:

            attributes.update(
                latest
            )

        incidents = (
            self.coordinator.data
            or {}
        ).get(
            "incidents",
            [],
        )

        attributes[
            "incident_count"
        ] = len(
            incidents
        )

        # De volledige incidentlijst wordt bewust
        # niet gepubliceerd om te voorkomen dat
        # Home Assistant de attribuutlimiet van
        # 16 kB overschrijdt.

        return attributes

    def _handle_coordinator_update(
        self
    ):
        """Handle coordinator update."""

        data = (
            self.coordinator.data
            or {}
        )

        # ---------------------------------------------
        # RAW NEW MESSAGE EVENTS
        # ---------------------------------------------

        for message in data.get(
            "new_messages",
            [],
        ):

            message_id = message.get(
                "message_id"
            )

            if (
                not message_id
                or
                message_id
                in self._handled_message_ids
            ):
                continue

            self._handled_message_ids.add(
                message_id
            )

            self.hass.bus.async_fire(
                EVENT_NEW_MESSAGE,
                dict(
                    message
                ),
            )

        # ---------------------------------------------
        # CENTRAL INCIDENT EVENTS
        # ---------------------------------------------

        for change in data.get(
            "incident_changes",
            [],
        ):

            action = change.get(
                "action"
            )

            incident = change.get(
                "incident",
                {},
            )

            incident_id = (
                incident.get(
                    "incident_id"
                )
            )

            if not incident_id:
                continue

            if action == "new":

                change_key = (
                    f"new|"
                    f"{incident_id}"
                )

            else:

                change_key = (
                    f"updated|"
                    f"{incident_id}|"
                    f"{incident.get('last_seen', '')}|"
                    f"{incident.get('alarm_count', '')}"
                )

            if (
                change_key
                in self._handled_incident_changes
            ):
                continue

            self._handled_incident_changes.add(
                change_key
            )

            event_data = dict(
                incident
            )

            if action == "new":

                self.hass.bus.async_fire(
                    EVENT_NEW_INCIDENT,
                    event_data,
                )

            elif action == "updated":

                self.hass.bus.async_fire(
                    EVENT_INCIDENT_UPDATE,
                    event_data,
                )

        super()._handle_coordinator_update()


class P2000FilterSensor(
    CoordinatorEntity,
    SensorEntity,
):
    """Filtered view of central P2000 data."""

    _attr_icon = "mdi:radio-tower"

    def __init__(
        self,
        coordinator,
        filter_config,
        index,
    ):

        super().__init__(
            coordinator
        )

        self.filter_config = (
            filter_config
        )

        self._attr_name = (
            filter_config[
                CONF_NAME
            ]
        )

        safe_name = re.sub(
            r"[^a-z0-9]+",
            "_",
            self._attr_name.lower(),
        ).strip("_")

        self._attr_unique_id = (
            f"p2000_monitor_filter_"
            f"{index}_{safe_name}"
        )

        self._latest_match = None

        self._latest_incident = None

        self._history = deque(
            maxlen=MAX_HISTORY
        )

        self._incident_ids = deque(
            maxlen=MAX_HISTORY
        )

        self._processed_message_ids = set()

    # =========================================================
    # FILTER MATCHING
    # =========================================================

    def _matches(
        self,
        message: dict,
    ) -> bool:
        """Check whether message matches filter."""

        regions = [
            str(value).lower()
            for value
            in self.filter_config.get(
                CONF_REGIONS,
                [],
            )
        ]

        disciplines = [
            str(value).lower()
            for value
            in self.filter_config.get(
                CONF_DISCIPLINES,
                [],
            )
        ]

        priorities = [
            str(value)
            .upper()
            .replace(
                " ",
                "",
            )
            for value
            in self.filter_config.get(
                CONF_PRIORITIES,
                [],
            )
        ]

        capcodes = [
            str(value)
            for value
            in self.filter_config.get(
                CONF_CAPCODES,
                [],
            )
        ]

        include_text = [
            str(value).lower()
            for value
            in self.filter_config.get(
                CONF_INCLUDE_TEXT,
                [],
            )
        ]

        exclude_text = [
            str(value).lower()
            for value
            in self.filter_config.get(
                CONF_EXCLUDE_TEXT,
                [],
            )
        ]

        message_region = str(
            message.get(
                "regio",
                "",
            )
        ).lower()

        message_discipline = str(
            message.get(
                "discipline",
                "",
            )
        ).lower()

        message_capcode = str(
            message.get(
                "capcode",
                "",
            )
        )

        message_text = str(
            message.get(
                "message",
                "",
            )
        )

        message_text_lower = (
            message_text.lower()
        )

        message_priority = (
            extract_priority(
                message_text
            )
        )

        if message_priority:

            message_priority = (
                message_priority
                .upper()
                .replace(
                    " ",
                    "",
                )
            )

        if (
            regions
            and
            message_region
            not in regions
        ):
            return False

        if (
            disciplines
            and
            message_discipline
            not in disciplines
        ):
            return False

        if (
            priorities
            and
            message_priority
            not in priorities
        ):
            return False

        if (
            capcodes
            and
            message_capcode
            not in capcodes
        ):
            return False

        if (
            include_text
            and
            not any(
                value
                in message_text_lower
                for value
                in include_text
            )
        ):
            return False

        if any(
            value
            in message_text_lower
            for value
            in exclude_text
        ):
            return False

        return True

    def _incident_matches(
        self,
        incident: dict,
    ) -> bool:
        """
        Check whether a central incident matches this filter.

        Central incidents can contain multiple capcodes, so capcode
        filtering is handled separately from normal message matching.
        """

        regions = [
            str(value).lower()
            for value
            in self.filter_config.get(
                CONF_REGIONS,
                [],
            )
        ]

        disciplines = [
            str(value).lower()
            for value
            in self.filter_config.get(
                CONF_DISCIPLINES,
                [],
            )
        ]

        priorities = [
            str(value)
            .upper()
            .replace(
                " ",
                "",
            )
            for value
            in self.filter_config.get(
                CONF_PRIORITIES,
                [],
            )
        ]

        capcodes = [
            str(value)
            for value
            in self.filter_config.get(
                CONF_CAPCODES,
                [],
            )
        ]

        include_text = [
            str(value).lower()
            for value
            in self.filter_config.get(
                CONF_INCLUDE_TEXT,
                [],
            )
        ]

        exclude_text = [
            str(value).lower()
            for value
            in self.filter_config.get(
                CONF_EXCLUDE_TEXT,
                [],
            )
        ]

        incident_region = str(
            incident.get(
                "regio",
                "",
            )
        ).lower()

        incident_discipline = str(
            incident.get(
                "discipline",
                "",
            )
        ).lower()

        incident_priority = str(
            incident.get(
                "priority",
                "",
            )
        ).upper().replace(
            " ",
            "",
        )

        incident_capcodes = [
            str(value)
            for value
            in incident.get(
                "capcodes",
                [],
            )
        ]

        incident_text = str(
            incident.get(
                "message",
                "",
            )
        ).lower()

        if (
            regions
            and
            incident_region
            not in regions
        ):
            return False

        if (
            disciplines
            and
            incident_discipline
            not in disciplines
        ):
            return False

        if (
            priorities
            and
            incident_priority
            not in priorities
        ):
            return False

        if (
            capcodes
            and
            not any(
                capcode
                in incident_capcodes
                for capcode
                in capcodes
            )
        ):
            return False

        if (
            include_text
            and
            not any(
                value
                in incident_text
                for value
                in include_text
            )
        ):
            return False

        if any(
            value
            in incident_text
            for value
            in exclude_text
        ):
            return False

        return True

    # =========================================================
    # INCIDENT HISTORY
    # =========================================================

    def _get_incident_history(
        self
    ) -> list[dict]:
        """Return latest matching non-test incidents."""

        incidents = (
            self.coordinator.data
            or {}
        ).get(
            "incidents",
            [],
        )

        result = []

        seen_ids = set()

        for incident in incidents:

            if not isinstance(
                incident,
                dict,
            ):
                continue

            # Testincidenten niet opnemen
            # in de echte historie.
            if incident.get(
                "test",
                False,
            ):
                continue

            if not self._incident_matches(
                incident
            ):
                continue

            incident_id = (
                incident.get(
                    "incident_id"
                )
            )

            if (
                incident_id
                and
                incident_id
                in seen_ids
            ):
                continue

            if incident_id:

                seen_ids.add(
                    incident_id
                )

            result.append(
                dict(
                    incident
                )
            )

            if (
                len(
                    result
                )
                >=
                MAX_FILTER_INCIDENT_HISTORY
            ):
                break

        return result

    # =========================================================
    # PROCESS UPDATES
    # =========================================================

    def _process_updates(
        self
    ) -> None:
        """Process coordinator updates."""

        data = (
            self.coordinator.data
            or {}
        )

        # ---------------------------------------------
        # RAW FILTER MATCHES
        # ---------------------------------------------

        for message in data.get(
            "new_messages",
            [],
        ):

            message_id = message.get(
                "message_id"
            )

            if (
                not message_id
                or
                message_id
                in self._processed_message_ids
            ):
                continue

            self._processed_message_ids.add(
                message_id
            )

            if not self._matches(
                message
            ):
                continue

            self._latest_match = (
                message
            )

            self._history.appendleft(
                message
            )

            event_data = dict(
                message
            )

            event_data[
                "filter_name"
            ] = self._attr_name

            self.hass.bus.async_fire(
                EVENT_FILTER_MATCH,
                event_data,
            )

        # ---------------------------------------------
        # CENTRAL INCIDENT CHANGES
        # ---------------------------------------------

        for change in data.get(
            "incident_changes",
            [],
        ):

            incident = change.get(
                "incident",
                {},
            )

            if not incident:
                continue

            if not self._incident_matches(
                incident
            ):
                continue

            self._latest_incident = dict(
                incident
            )

            incident_id = (
                incident.get(
                    "incident_id"
                )
            )

            if (
                incident_id
                and
                incident_id
                not in self._incident_ids
            ):

                self._incident_ids.appendleft(
                    incident_id
                )

        # ---------------------------------------------
        # RESTORE LATEST INCIDENT FROM HISTORY
        # ---------------------------------------------

        # Dit is belangrijk na een Home Assistant restart:
        # incident_changes is dan leeg, maar de persistente
        # centrale historie is wel beschikbaar.
        if self._latest_incident is None:

            history = (
                self._get_incident_history()
            )

            if history:

                self._latest_incident = (
                    dict(
                        history[
                            0
                        ]
                    )
                )

    # =========================================================
    # ENTITY STATE
    # =========================================================

    @property
    def native_value(
        self
    ):

        # Nieuwe live/raw melding heeft voorkeur.
        if self._latest_match:

            return self._latest_match.get(
                "message",
                "Onbekende melding",
            )

        # Na restart kan de laatste persistente
        # incidentmelding als status gebruikt worden.
        if self._latest_incident:

            return self._latest_incident.get(
                "message",
                "Onbekende melding",
            )

        history = (
            self._get_incident_history()
        )

        if history:

            return history[
                0
            ].get(
                "message",
                "Onbekende melding",
            )

        return "Wachten op melding"

    @property
    def icon(
        self
    ):

        source = (
            self._latest_match
            or
            self._latest_incident
        )

        if not source:

            history = (
                self._get_incident_history()
            )

            if history:

                source = history[
                    0
                ]

        if not source:

            return "mdi:radio-tower"

        discipline = str(
            source.get(
                "discipline",
                "",
            )
        )

        if discipline == "Brandweerdiensten":
            return "mdi:fire-truck"

        if discipline == "Ambulancediensten":
            return "mdi:ambulance"

        if discipline == "Politiediensten":
            return "mdi:car-emergency"

        if discipline == "Lifeliner":
            return "mdi:helicopter"

        return "mdi:radio-tower"

    @property
    def extra_state_attributes(
        self
    ):

        incident_history = (
            self._get_incident_history()
        )

        attributes = {
            "filter":
                self._attr_name,

            "meldingen_deze_sessie":
                len(
                    self._history
                ),

            "incidenten_deze_sessie":
                len(
                    self._incident_ids
                ),

            "incident_history_count":
                len(
                    incident_history
                ),

            "incident_history":
                incident_history,
        }

        if self._latest_match:

            attributes.update(
                self._latest_match
            )

        latest_incident = (
            self._latest_incident
        )

        if (
            latest_incident
            is None
            and
            incident_history
        ):

            latest_incident = (
                incident_history[
                    0
                ]
            )

        if latest_incident:

            attributes[
                "latest_incident"
            ] = dict(
                latest_incident
            )

        return attributes

    def _handle_coordinator_update(
        self
    ):
        """Handle coordinator update."""

        self._process_updates()

        super()._handle_coordinator_update()