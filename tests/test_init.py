from datetime import timedelta
import re

import aiohttp
from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.p2000_monitor.const import DATA_FEED, DOMAIN
from custom_components.p2000_monitor.coordinator import API_URL, LOCAL_TZ, _published

BASE = {
    "dienstid": "2", "regioid": "21", "dienst": "Brandweer", "regio": "Zuid-Limburg",
    "plaats": "Maastricht", "lat": None, "lon": None,
}
SENSOR_DEFAULTS = {"all_services": False, "regions": [], "disciplines": [], "priorities": [],
                   "capcodes": [], "include_text": [], "exclude_text": [], "incident_window": 900}


def item(id_, text, capcodes, **extra):
    now = dt_util.now(LOCAL_TZ)
    return {
        **BASE, "id": str(id_), "melding": text, "tekstmelding": text,
        "datum": now.strftime("%d-%m"), "tijd": now.strftime("%H:%M"),
        "capcodes": [{"capcode": c, "omschrijving": "x"} for c in capcodes], **extra,
    }


def feed(*items):
    return {"meldingen": list(items), "high_id": "1", "prev_high_id": -1}


def sensor(name, **data):
    return {"subentry_type": "sensor", "title": name, "unique_id": None,
            "data": {"name": name, **SENSOR_DEFAULTS, **data}}


def hub(*sensors, brandbase=False):
    # Brandbase off by default so tests never reach out; test_brandbase covers it.
    return MockConfigEntry(domain=DOMAIN, version=2, title="P2000 Monitor", unique_id=DOMAIN,
                           data={}, options={"brandbase": brandbase}, subentries_data=list(sensors))


def legacy_entry(name, **data):
    return MockConfigEntry(domain=DOMAIN, version=1, title=name, unique_id=f"p2000_monitor_{name.lower()}",
                           data={"name": name, **SENSOR_DEFAULTS, **data})


def processors(entry):
    return {p.subentry_id: p for p in entry.runtime_data.values()}


def by_name(entry, name):
    sub_id = next(s.subentry_id for s in entry.subentries.values() if s.title == name)
    return entry.runtime_data[sub_id]


BACKFILL_URL = re.compile(r"/api2/find/%7B")


async def setup(hass, aioclient_mock, *entries, payload=None, backfill=None):
    # Startup backfill requests (filtered API) get an empty feed unless given.
    aioclient_mock.get(BACKFILL_URL, json=backfill or feed())
    aioclient_mock.get(API_URL, json=payload or feed(item(100, "P 1 BR woning Echt", ["1005258"])))
    for e in entries:
        e.add_to_hass(hass)
    for e in entries:
        if e.state is ConfigEntryState.NOT_LOADED:
            await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()


async def poll(hass, aioclient_mock, payload, exc=None):
    aioclient_mock.clear_requests()
    aioclient_mock.get(BACKFILL_URL, json=feed())
    if exc:
        aioclient_mock.get(API_URL, exc=exc)
    else:
        aioclient_mock.get(API_URL, json=payload)
    await hass.data[DATA_FEED].async_refresh()
    await hass.async_block_till_done()


async def test_one_request_for_all_sensors(hass: HomeAssistant, aioclient_mock):
    e = hub(sensor("A"), sensor("B", regions=["15"]), sensor("C", all_services=True))
    await setup(hass, aioclient_mock, e)
    feed_calls = [c for c in aioclient_mock.mock_calls if "%7B" not in str(c[1])]
    assert len(feed_calls) == 1  # one live poll for all sensors (B also backfills once)
    assert len(e.runtime_data) == 3
    assert {hass.states.get(x) is not None for x in ("sensor.a", "sensor.b", "sensor.c")} == {True}
    ent_reg = er.async_get(hass)
    reg = ent_reg.async_get("sensor.b")
    assert reg.config_subentry_id == by_name(e, "B").subentry_id


async def test_new_message_events_once_including_subitems(hass, aioclient_mock):
    e = hub(sensor("A"), sensor("B"))
    await setup(hass, aioclient_mock, e)
    new = async_capture_events(hass, "p2000_monitor_new_message")
    match = async_capture_events(hass, "p2000_monitor_filter_match")
    payload = feed(
        item(103, "P 2 BR buitenbrand Echt", ["1005264"], subitems=[item(102, "P 2 BR buitenbrand Echt", ["1005998"])]),
        item(101, "A1 Maastricht rit 1", ["1420003"]),
        item(100, "P 1 BR woning Echt", ["1005258"]),
    )
    await poll(hass, aioclient_mock, payload)
    assert sorted(ev.data["message"]["api_id"] for ev in new) == ["101", "102", "103"]
    assert len(match) == 6
    assert {ev.data["subentry_id"] for ev in match} == set(e.subentries)
    await poll(hass, aioclient_mock, payload)
    await poll(hass, aioclient_mock, None, exc=aiohttp.ClientError("down"))
    assert len(new) == 3 and len(match) == 6


async def test_filters(hass, aioclient_mock):
    e = hub(
        sensor("Inc", include_text=["woning", "buitenbrand"]),
        sensor("Cap", capcodes=["001005998"]),
        sensor("Reg", regions=["15"], disciplines=["2"]),
        sensor("All", all_services=True, regions=["15"], exclude_text=["rit"]),
    )
    await setup(hass, aioclient_mock, e, payload=feed(item(1, "x", ["9"], regioid="1")))
    await poll(hass, aioclient_mock, feed(
        item(103, "P 2 BR buitenbrand Echt", ["1005264", "1005998"]),
        item(101, "A1 Maastricht rit 1", ["1420003"], dienstid="3"),
        item(100, "P 1 BR woning Echt", ["1005258"]),
    ))
    ids = lambda name: [m["api_id"] for m in by_name(e, name).data["messages"]]
    assert ids("Inc") == ["103", "100"]
    assert ids("Cap") == ["103"]
    assert ids("Reg") == []
    assert ids("All") == ["103", "100", "1"]
    assert hass.states.get("sensor.inc").state == "P 2 BR buitenbrand Echt"


async def test_phase_a_filters_and_display(hass, aioclient_mock):
    hass.config.latitude, hass.config.longitude = 50.8514, 5.6909  # Maastricht
    e = hub(
        sensor("Pager", display="pager"),
        sensor("Spoed", priorities=["P1"]),
        sensor("Wagen", vehicles=["240733"]),
        sensor("Dichtbij", radius_km=10),
    )
    await setup(hass, aioclient_mock, e, payload=feed(item(1, "x", ["9"], regioid="1")))
    await poll(hass, aioclient_mock, feed(
        item(103, "Brandweer met spoed naar Echt", ["1005264"], melding="P 1 BR woning Echt 240733",
             lat="51.1028", lon="5.8662"),
        item(102, "Ambulance met spoed naar Maastricht", ["1"], melding="A1 AMBU 24101 Maastricht",
             dienstid="3", lat="50.85", lon="5.69"),
        item(101, "P 3 BR container", ["2"]),
    ))
    ids = lambda name: [m["api_id"] for m in by_name(e, name).data["messages"]]
    assert ids("Spoed") == ["103", "102"]  # P1 and ambulance A1
    assert ids("Wagen") == ["103"]
    assert ids("Dichtbij") == ["102"]  # 0.1 km; Echt is ~31 km, 101 has no location
    assert hass.states.get("sensor.pager").state == "P 1 BR woning Echt 240733"  # newest, as pager text
    assert hass.states.get("sensor.spoed").attributes["vehicles"] == [{"id": "24-0733"}]


async def test_brandbase_names_vehicles_of_accepted_alerts(hass, aioclient_mock):
    from unittest.mock import patch

    from custom_components.p2000_monitor import brandbase
    from tests.test_brandbase import INDEX, REGION

    aioclient_mock.get(brandbase.INDEX_URL, text=INDEX)
    aioclient_mock.get("https://brandbase.hetbrandweerforum.nl/voertuigen/regio/24-brandweer-zuid-limburg/", text=REGION)
    e = hub(sensor("Limburg", regions=["21"]), brandbase=True)

    async def no_sleep(_seconds):
        return None

    with patch.object(brandbase, "_sleep", no_sleep):
        await setup(hass, aioclient_mock, e, payload=feed(item(100, "P 1 BR woning Echt 240733", ["1"])))
        await hass.async_block_till_done(wait_background_tasks=True)
    state = hass.states.get("sensor.limburg")
    assert state.attributes["vehicles"][0]["type"] == "Tankautospuit"
    assert state.attributes["vehicles"][0]["station"] == "Regionaal"


async def test_options_switch_brandbase_off(hass, aioclient_mock):
    e = hub(sensor("A"), brandbase=True)
    await setup(hass, aioclient_mock, e, payload=feed(item(1, "A1 x", ["1"], dienstid="3")))
    result = await hass.config_entries.options.async_init(e.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"brandbase": False})
    await hass.async_block_till_done()
    assert e.options["brandbase"] is False
    assert e.state is ConfigEntryState.LOADED
    assert all(p.vehicle_db is None for p in e.runtime_data.values())


def watch(name, **data):
    return {"subentry_type": "watch", "title": name, "unique_id": None,
            "data": {"name": name, "capcodes": [], "vehicles": [], "regions": [], "active_minutes": 15, **data}}


async def test_watch_fires_alarm_event_and_active_sensor(hass, aioclient_mock, freezer):
    e = hub(watch("Kazerne Echt", capcodes=["1005258"], vehicles=["24-3231"]))
    await setup(hass, aioclient_mock, e, payload=feed(item(1, "x", ["9"])))
    assert hass.states.get("binary_sensor.kazerne_echt_active").state == "off"
    event_state = hass.states.get("event.kazerne_echt_alarm")
    assert event_state.state == "unknown"

    await poll(hass, aioclient_mock, feed(
        item(3, "Brandweer naar Echt", ["7777"], melding="P 1 BR woning Echt 243231"),  # vehicle only
        item(2, "Ambulance Rotterdam", ["1"], regioid="10", dienstid="3"),
        item(1, "x", ["9"]),
    ))
    event_state = hass.states.get("event.kazerne_echt_alarm")
    assert event_state.attributes["event_type"] == "alarm"
    assert event_state.attributes["vehicles"] == ["24-3231"]
    assert event_state.attributes["pager_text"] == "P 1 BR woning Echt 243231"
    assert hass.states.get("binary_sensor.kazerne_echt_active").state == "on"
    fired_at = event_state.state

    # Same feed again: no new alarm.
    await poll(hass, aioclient_mock, feed(item(3, "Brandweer naar Echt", ["7777"], melding="P 1 BR woning Echt 243231")))
    assert hass.states.get("event.kazerne_echt_alarm").state == fired_at

    # Active turns off after the window.
    freezer.tick(timedelta(minutes=16))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.kazerne_echt_active").state == "off"


async def test_watch_flow_requires_a_unit(hass, aioclient_mock):
    e = hub(sensor("A"))
    await setup(hass, aioclient_mock, e)
    flow = await hass.config_entries.subentries.async_init((e.entry_id, "watch"), context={"source": SOURCE_USER})
    result = await hass.config_entries.subentries.async_configure(flow["flow_id"], {"name": "Leeg"})
    assert result["errors"] == {"base": "no_units"}
    result = await hass.config_entries.subentries.async_configure(
        flow["flow_id"], {"name": "Echt", "capcodes": "1005258", "vehicles": "243231"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    sub = next(s for s in e.subentries.values() if s.subentry_type == "watch")
    assert sub.data["vehicles"] == ["24-3231"]
    assert hass.states.get("event.echt_alarm") is not None


async def test_recent_alerts_on_the_map(hass, aioclient_mock, freezer):
    e = hub(sensor("Limburg", regions=["21"]), watch("Echt", capcodes=["1"]))
    await setup(hass, aioclient_mock, e, payload=feed(
        item(2, "Brandweer naar Echt", ["1"], lat="51.1028", lon="5.8662"),
        item(1, "Zonder locatie", ["1"]),
    ))
    geo = [s for s in hass.states.async_all("geo_location") if s.attributes.get("source") == "p2000_monitor"]
    assert len(geo) == 1
    assert geo[0].attributes["latitude"] == 51.1028
    assert geo[0].attributes["message"] == "Brandweer naar Echt"
    # Older than an hour: off the map at the next update.
    freezer.tick(timedelta(minutes=61))
    await poll(hass, aioclient_mock, feed(item(3, "Nieuw zonder locatie", ["1"])))
    assert not [s for s in hass.states.async_all("geo_location") if s.attributes.get("source") == "p2000_monitor"]


async def test_startup_backfills_latest_matching_alerts(hass, aioclient_mock):
    """A sensor for a quiet region gets its last alerts at startup, without events."""
    match = async_capture_events(hass, "p2000_monitor_filter_match")
    old = feed(
        item(26204335, "Brandweer met gepaste spoed naar Holzstraat, Kerkrade", ["1005110"]),
        item(26203473, "Brandweer met gepaste spoed naar Bartokring, Landgraaf", ["1005110"]),
        item(26203000, "Ambulance elders", ["1"], dienstid="3"),
    )
    e = hub(sensor("Brandweer ZL", regions=["21"], disciplines=["2"]), sensor("Alles", all_services=True))
    await setup(hass, aioclient_mock, e, payload=feed(item(1, "Amsterdam", ["9"], regioid="1")), backfill=old)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get("sensor.brandweer_zl").state == "Brandweer met gepaste spoed naar Holzstraat, Kerkrade"
    assert [m["api_id"] for m in by_name(e, "Brandweer ZL").data["messages"]] == ["26204335", "26203473"]
    assert not match
    # The API was asked with the sensor's own filter; "all services" needs no backfill.
    backfill_calls = [str(c[1]) for c in aioclient_mock.mock_calls if "%7B" in str(c[1])]
    assert len(backfill_calls) == 1 and "regios" in backfill_calls[0] and "diensten" in backfill_calls[0]


async def test_police_units_cover_all_their_regions(hass, aioclient_mock):
    e = hub(sensor("LN", regions=["15"]), sensor("Utrecht", regions=["18"]))
    await setup(hass, aioclient_mock, e, payload=feed(item(1, "x", ["9"], regioid="1")))
    await poll(hass, aioclient_mock, feed(
        item(2, "Aanrijding letsel N297 Born", ["1033215"], dienstid="1", regioid="21", regio="Eenheid Limburg"),
    ))
    assert [m["api_id"] for m in by_name(e, "LN").data["messages"]] == ["2"]
    assert by_name(e, "Utrecht").data["messages"] == []
    assert hass.states.get("sensor.ln").attributes["regio_name"] == "Eenheid Limburg"


async def test_failure_backoff_and_availability(hass, aioclient_mock):
    e = hub(sensor("A"))
    await setup(hass, aioclient_mock, e)
    feed_coord = hass.data[DATA_FEED]
    assert feed_coord.update_interval == timedelta(seconds=30)  # default
    await poll(hass, aioclient_mock, None, exc=TimeoutError())
    assert feed_coord.update_interval == timedelta(seconds=60)
    assert hass.states.get("sensor.a").state != "unavailable"
    await poll(hass, aioclient_mock, feed(item(100, "P 1 BR woning Echt", ["1005258"])))
    assert feed_coord.update_interval == timedelta(seconds=30)


async def test_poll_interval_option(hass, aioclient_mock):
    e = hub(sensor("A"))
    await setup(hass, aioclient_mock, e)
    result = await hass.config_entries.options.async_init(e.entry_id)
    # Below the minimum is refused.
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(result["flow_id"], {"brandbase": False, "scan_interval": 10})
    await hass.config_entries.options.async_configure(result["flow_id"], {"brandbase": False, "scan_interval": 120})
    await hass.async_block_till_done()
    assert e.options["scan_interval"] == 120
    feed_coord = hass.data[DATA_FEED]
    assert feed_coord.update_interval == timedelta(seconds=120)
    # Backoff during an outage starts from and resets to the configured value.
    await poll(hass, aioclient_mock, None, exc=TimeoutError())
    assert feed_coord.update_interval == timedelta(seconds=240)
    await poll(hass, aioclient_mock, feed(item(100, "P 1 BR woning Echt", ["1005258"])))
    assert feed_coord.update_interval == timedelta(seconds=120)


async def test_attributes_and_long_state(hass, aioclient_mock):
    e = hub(sensor("A"))
    await setup(hass, aioclient_mock, e, payload=feed(item(100, "P 1 " + "x" * 400, ["1"])))
    state = hass.states.get("sensor.a")
    assert len(state.state) == 255
    assert len(state.attributes["message"]) == 404


async def test_history_survives_feed_window_and_reload(hass, aioclient_mock):
    e = hub(sensor("Limburg", regions=["15", "21"]))
    await setup(hass, aioclient_mock, e, payload=feed(item(90, "A1 Rotterdam", ["1"], regioid="10")))
    assert hass.states.get("sensor.limburg").state == "Geen meldingen"
    await poll(hass, aioclient_mock, feed(item(100, "P 1 BR woning Echt", ["1005258"]), item(99, "A1 R", ["1"], regioid="10")))
    await poll(hass, aioclient_mock, feed(item(101, "A2 Amsterdam", ["2"], regioid="1")))
    assert hass.states.get("sensor.limburg").state == "P 1 BR woning Echt"
    await hass.config_entries.async_reload(e.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.limburg").state == "P 1 BR woning Echt"


async def test_initial_flow_creates_hub_with_first_sensor(hass, aioclient_mock):
    aioclient_mock.get(API_URL, json=feed(item(100, "P 1 BR woning Echt", ["1005258"])))
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "P2000 Monitor", "all_services": True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.state is ConfigEntryState.LOADED and len(entry.subentries) == 1
    assert hass.states.get("sensor.p2000_monitor").state == "P 1 BR woning Echt"
    again = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert again["type"] is FlowResultType.ABORT


async def test_add_change_remove_sensor(hass, aioclient_mock):
    e = hub(sensor("A"))
    await setup(hass, aioclient_mock, e)
    # Add
    result = await hass.config_entries.subentries.async_init((e.entry_id, "sensor"), context={"source": SOURCE_USER})
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Limburg", "regions": ["15", "21"], "capcodes": "", "include_text": "", "exclude_text": ""}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert hass.states.get("sensor.limburg").state == "P 1 BR woning Echt"
    # Duplicate name is refused
    dup = await hass.config_entries.subentries.async_init((e.entry_id, "sensor"), context={"source": SOURCE_USER})
    dup = await hass.config_entries.subentries.async_configure(dup["flow_id"], {"name": "limburg"})
    assert dup["errors"] == {"name": "name_exists"}
    # Change
    sub_id = next(s for s, sub in e.subentries.items() if sub.title == "Limburg")
    result = await hass.config_entries.subentries.async_init(
        (e.entry_id, "sensor"), context={"source": SOURCE_RECONFIGURE, "subentry_id": sub_id}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Limburg", "regions": ["1"], "capcodes": "", "include_text": "", "exclude_text": ""}
    )
    assert result["type"] is FlowResultType.ABORT
    await hass.async_block_till_done()
    assert e.subentries[sub_id].data["regions"] == ["1"]
    # Remove
    hass.config_entries.async_remove_subentry(e, sub_id)
    await hass.async_block_till_done()
    assert sub_id not in e.runtime_data
    assert er.async_get(hass).async_get("sensor.limburg") is None


async def test_migrates_separate_entries_into_one(hass, aioclient_mock):
    ent_reg = er.async_get(hass)
    old = [legacy_entry("P2000 Monitor"), legacy_entry("Brandweer Echt", capcodes=["1005258"]),
           legacy_entry("Limburg", regions=["15", "21"])]
    for e in old:
        e.add_to_hass(hass)
        ent_reg.async_get_or_create("sensor", DOMAIN, f"p2000_monitor_{e.unique_id}",
                                    config_entry=e, suggested_object_id=e.title.lower().replace(" ", "_"))
    aioclient_mock.get(API_URL, json=feed(item(100, "P 1 BR woning Echt", ["1005258"])))
    # Setting up the first entry sets up the integration, which sets up all entries.
    await hass.config_entries.async_setup(old[0].entry_id)
    await hass.async_block_till_done()

    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    main = entries[0]
    assert main.version == 2 and main.title == "P2000 Monitor" and main.state is ConfigEntryState.LOADED
    assert sorted(s.title for s in main.subentries.values()) == ["Brandweer Echt", "Limburg", "P2000 Monitor"]
    for object_id in ("p2000_monitor", "brandweer_echt", "limburg"):
        reg = ent_reg.async_get(f"sensor.{object_id}")
        assert reg is not None and reg.config_entry_id == main.entry_id and reg.config_subentry_id
        assert hass.states.get(f"sensor.{object_id}").state == "P 1 BR woning Echt"


async def test_service_registered(hass, aioclient_mock):
    e = hub(sensor("A"))
    await setup(hass, aioclient_mock, e)
    new_incident = async_capture_events(hass, "p2000_monitor_new_incident")
    await hass.services.async_call(DOMAIN, "inject_test_incident", {}, blocking=True)
    await hass.async_block_till_done()
    assert len(new_incident) == 1
    assert hass.states.get("sensor.a").state.startswith("P 1 BR woning Teststraat")


def test_published_parsing():
    assert _published({"datum": "14-10", "tijd": "20:44"}).endswith("+00:00")
    assert _published({"timestamp": "1700000000"}) == "2023-11-14T22:13:20+00:00"
    assert _published({}) == ""
