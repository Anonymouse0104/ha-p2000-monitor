"""Constants for P2000 Monitor."""

DOMAIN = "p2000_monitor"

# hass.data key of the feed coordinator shared by all config entries.
DATA_FEED = f"{DOMAIN}_feed"

CONF_ALL_SERVICES = "all_services"
CONF_REGIONS = "regions"
CONF_DISCIPLINES = "disciplines"
CONF_PRIORITIES = "priorities"
CONF_CAPCODES = "capcodes"
CONF_INCLUDE_TEXT = "include_text"
CONF_EXCLUDE_TEXT = "exclude_text"
CONF_INCIDENT_WINDOW = "incident_window"

# The single config entry and its per-sensor subentries.
HUB_TITLE = "P2000 Monitor"
SUBENTRY_SENSOR = "sensor"
# Set on sensors migrated from the old one-entry-per-sensor setup, so they keep
# their entity unique_id and stored history.
CONF_ENTITY_KEY = "entity_key"
CONF_STORAGE_ID = "storage_id"
DATA_MIGRATION_LOCK = f"{DOMAIN}_migration_lock"

from datetime import timedelta

DEFAULT_SCAN_INTERVAL = 10
# Poll interval doubles per failed update up to this while the API is down.
MAX_BACKOFF = timedelta(minutes=5)
# Sensors stay available with their last data for this long after the last
# successful update, so one failed poll does not flap them to unavailable.
AVAILABILITY_GRACE = timedelta(minutes=5)

EVENT_NEW_MESSAGE = "p2000_monitor_new_message"
EVENT_FILTER_MATCH = "p2000_monitor_filter_match"
EVENT_NEW_INCIDENT = "p2000_monitor_new_incident"
EVENT_INCIDENT_UPDATE = "p2000_monitor_incident_update"

MAX_SEEN_MESSAGES = 1000
# New messages older than this (seconds) are deduplicated but fire no event,
# e.g. when the feed comes back after a long outage.
MAX_EVENT_AGE = 3600
MAX_HISTORY = 100

DEFAULT_INCIDENT_WINDOW = 120
MAX_INCIDENT_HISTORY = 50

# Hoeveel incidenten per filtersensor beschikbaar
# worden gemaakt voor gebruik in Lovelace.
MAX_FILTER_INCIDENT_HISTORY = 10

# Persistent storage.
# Hiermee kan de incidenthistorie een herstart
# van Home Assistant overleven.
STORAGE_VERSION = 1
STORAGE_KEY = "p2000_monitor.incident_history"

SERVICE_INJECT_TEST_INCIDENT = "inject_test_incident"

ATTR_TEST_REGION = "region"
ATTR_TEST_REGION_NAME = "region_name"
ATTR_TEST_DISCIPLINE = "discipline"
ATTR_TEST_MESSAGE = "message"
ATTR_TEST_CAPCODES = "capcodes"