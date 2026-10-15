from datetime import timedelta

import aiohttp
from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
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


def hub(*sensors):
    return MockConfigEntry(domain=DOMAIN, version=2, title="P2000 Monitor", unique_id=DOMAIN,
                           data={}, subentries_data=list(sensors))


def legacy_entry(name, **data):
    return MockConfigEntry(domain=DOMAIN, version=1, title=name, unique_id=f"p2000_monitor_{name.lower()}",
                           data={"name": name, **SENSOR_DEFAULTS, **data})


def processors(entry):
    return {p.subentry_id: p for p in entry.runtime_data.values()}


def by_name(entry, name):
    sub_id = next(s.subentry_id for s in entry.subentries.values() if s.title == name)
    return entry.runtime_data[sub_id]


async def setup(hass, aioclient_mock, *entries, payload=None):
    aioclient_mock.get(API_URL, json=payload or feed(item(100, "P 1 BR woning Echt", ["1005258"])))
    for e in entries:
        e.add_to_hass(hass)
    for e in entries:
        if e.state is ConfigEntryState.NOT_LOADED:
            await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()


async def poll(hass, aioclient_mock, payload, exc=None):
    aioclient_mock.clear_requests()
    if exc:
        aioclient_mock.get(API_URL, exc=exc)
    else:
        aioclient_mock.get(API_URL, json=payload)
    await hass.data[DATA_FEED].async_refresh()
    await hass.async_block_till_done()


async def test_one_request_for_all_sensors(hass: HomeAssistant, aioclient_mock):
    e = hub(sensor("A"), sensor("B", regions=["15"]), sensor("C", all_services=True))
    await setup(hass, aioclient_mock, e)
    assert aioclient_mock.call_count == 1
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


async def test_failure_backoff_and_availability(hass, aioclient_mock):
    e = hub(sensor("A"))
    await setup(hass, aioclient_mock, e)
    feed_coord = hass.data[DATA_FEED]
    await poll(hass, aioclient_mock, None, exc=TimeoutError())
    assert feed_coord.update_interval == timedelta(seconds=20)
    assert hass.states.get("sensor.a").state != "unavailable"
    await poll(hass, aioclient_mock, feed(item(100, "P 1 BR woning Echt", ["1005258"])))
    assert feed_coord.update_interval == timedelta(seconds=10)


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
