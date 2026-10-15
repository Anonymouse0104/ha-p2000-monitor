"""Tests for the Brandbase vehicle lookup."""
from unittest.mock import patch

from homeassistant.core import HomeAssistant

from custom_components.p2000_monitor import brandbase
from custom_components.p2000_monitor.brandbase import BrandbaseVehicles, parse_index, parse_region

# Minimal synthetic pages in the structure Brandbase uses.
INDEX = (
    '<div class="panel-heading">Regio 24</div><div class="panel-body">'
    '<a href="https://brandbase.hetbrandweerforum.nl/voertuigen/regio/24-brandweer-zuid-limburg/">VRZL</a></div>'
)
REGION = (
    "<h3>24-07 Regionaal</h3><ul>"
    '<li><a href="https://brandbase.hetbrandweerforum.nl/voertuigen/24-0733-tankautospuit/">24-0733 Tankautospuit</a></li>'
    '<li><a href="https://brandbase.hetbrandweerforum.nl/voertuigen/24-0752-autoladder/">24-0752 Autoladder</a></li>'
    "</ul><h3>24-32 Echt</h3><ul>"
    '<li><a href="https://brandbase.hetbrandweerforum.nl/voertuigen/24-3231-tankautospuit/">24-3231 Tankautospuit &amp; Redvoertuig</a></li>'
    "</ul>"
)


def test_parsers():
    assert parse_index(INDEX) == {"24": "https://brandbase.hetbrandweerforum.nl/voertuigen/regio/24-brandweer-zuid-limburg/"}
    vehicles = parse_region(REGION)
    assert vehicles["24-0733"]["type"] == "Tankautospuit"
    assert vehicles["24-0733"]["post"] == "24-07" and vehicles["24-0733"]["station"] == "Regionaal"
    assert vehicles["24-3231"] == {
        "type": "Tankautospuit & Redvoertuig", "post": "24-32", "station": "Echt",
        "url": "https://brandbase.hetbrandweerforum.nl/voertuigen/24-3231-tankautospuit/",
    }


async def test_lookup_is_lazy_cached_and_polite(hass: HomeAssistant, aioclient_mock):
    aioclient_mock.get(brandbase.INDEX_URL, text=INDEX)
    aioclient_mock.get("https://brandbase.hetbrandweerforum.nl/voertuigen/regio/24-brandweer-zuid-limburg/", text=REGION)
    db = BrandbaseVehicles(hass)
    await db.async_load()
    message = {"dienstid": "2", "vehicles": [{"id": "24-0733"}]}
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    with patch.object(brandbase, "_sleep", fake_sleep):
        db.enrich(message)
        db.enrich({"dienstid": "2", "vehicles": [{"id": "24-3231"}]})  # same region: no extra request
        await hass.async_block_till_done(wait_background_tasks=True)
    assert aioclient_mock.call_count == 2  # index + one region page
    assert all(s >= 10 for s in sleeps)  # robots.txt crawl delay
    assert all(h.get("User-Agent", "").startswith("P2000-Monitor") for *_, h in aioclient_mock.mock_calls)
    db.enrich(message)
    assert message["vehicles"][0]["type"] == "Tankautospuit"
    # Within a week nothing is fetched again; ambulance alerts are never looked up.
    db.enrich({"dienstid": "3", "vehicles": [{"id": "17-103"}]})
    db.enrich({"dienstid": "2", "vehicles": [{"id": "24-0752"}]})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert aioclient_mock.call_count == 2
    await db.async_stop()
