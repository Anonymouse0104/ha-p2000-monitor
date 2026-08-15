"""Constants for P2000 Monitor."""

DOMAIN = "p2000_monitor"

DEFAULT_SCAN_INTERVAL = 10

EVENT_NEW_MESSAGE = "p2000_monitor_new_message"
EVENT_FILTER_MATCH = "p2000_monitor_filter_match"
EVENT_NEW_INCIDENT = "p2000_monitor_new_incident"
EVENT_INCIDENT_UPDATE = "p2000_monitor_incident_update"

MAX_SEEN_MESSAGES = 1000
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