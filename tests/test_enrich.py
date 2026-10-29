"""Tests for alert enrichment helpers."""
from custom_components.p2000_monitor.coordinator import normalize_api_message, normalize_capcode
from custom_components.p2000_monitor.enrich import (
    capcode_details,
    distance_km,
    extract_vehicles,
    normalize_vehicle,
    parse_priority,
)


def test_priority():
    assert parse_priority("P 1 BR woning Echt 243231") == ("P1", 1)
    assert parse_priority("PRIO 2 Ass. politie") == ("P2", 2)
    assert parse_priority("A1 AMBU 17103 Peperdonk") == ("A1", 1)
    assert parse_priority("A2 (dia: ja) 10118 Rit 155028") == ("A2", 2)
    assert parse_priority("B2 AMBU 17216 Dr. Molewaterplein") == ("B2", 3)
    assert parse_priority("Graag contact OC", prio1="1") == ("P1", 1)
    assert parse_priority("Graag contact OC") == (None, None)


def test_vehicles():
    assert extract_vehicles("P 1 BR woning Teststraat Maastricht 243231 240733", "2") == ["24-3231", "24-0733"]
    assert extract_vehicles("A1 AMBU 17103 Peperdonk 3206CB Spijkenisse bon 159921", "3") == ["17-103"]
    assert extract_vehicles("A2 Bergen op Zoom rit: 182673", "3") == []
    assert extract_vehicles("A2 (dia: ja) 10118 Rit 155028 Kruiszwin", "3") == ["10-118"]
    assert extract_vehicles("Graag contact 243231", "1") == []
    assert normalize_vehicle("240733") == normalize_vehicle(" 24-0733 ") == "24-0733"


def test_capcode_details_and_distance():
    raw = [{"capcode": "001420003", "omschrijving": "Ambulance 17-103"}, {"capcode": "2029578", "omschrijving": "???"}]
    assert capcode_details(raw, normalize_capcode) == [
        {"capcode": "1420003", "description": "Ambulance 17-103"},
        {"capcode": "2029578", "description": ""},
    ]
    assert round(distance_km(50.8514, 5.6909, 51.1028, 5.8662)) == 31


def test_misfiled_police_press_alarm_becomes_police():
    # Real feed item 26204478: AlarmeringDroid files it as fire brigade with a
    # generated fire text; capcode 1033216 is a Limburg police press alarm.
    message = normalize_api_message({
        "id": "26204478", "melding": "Aanrijding letsel N279 - Schaapsbrug Heibloem",
        "tekstmelding": "Brandweer met gepaste spoed naar Schaapsbrug, Heibloem, voor een hulpverlening",
        "dienstid": "2", "regioid": "15", "regio": "Limburg-Noord", "datum": "15-10", "tijd": "10:32",
        "capcodes": [{"capcode": "1033216", "omschrijving": "Onbekend"}],
    })
    assert message["dienstid"] == "1" and message["discipline"] == "Politiediensten"
    assert message["message"] == "Aanrijding letsel N279 - Schaapsbrug Heibloem"
    # A real fire alert with a mixed capcode list stays fire brigade.
    fire = normalize_api_message({
        "id": "1", "melding": "P 2 BLB-01 Stank Holzstraat Kerkrade 242131", "tekstmelding": "Brandweer naar Kerkrade",
        "dienstid": "2", "regioid": "21", "datum": "15-10", "tijd": "10:03",
        "capcodes": [{"capcode": "1005110", "omschrijving": "Bevelvoerders / Kerkrade"}, {"capcode": "1033215", "omschrijving": "Persinfo"}],
    })
    assert fire["dienstid"] == "2" and fire["vehicles"] == [{"id": "24-2131"}]


def test_misfiled_ambulance_becomes_ambulance():
    # Real feed item 26270066: filed as fire, region Amsterdam, no capcodes,
    # with a generated fire text; P2Kflex shows capcode 1023193 Ambulance 24-113.
    message = normalize_api_message({
        "id": "26270066", "melding": "A1 Prinses Christinastraat Echt 99369",
        "tekstmelding": "Brandweer met spoed naar Prinses Christinastraat, Echt, voor een overigen",
        "dienstid": "2", "regioid": "1", "regio": "Amsterdam-Amstelland", "datum": "28-10", "tijd": "20:12",
        "prio1": "1", "capcodes": [],
    })
    assert message["dienstid"] == "3" and message["discipline"] == "Ambulancediensten"
    assert message["message"] == "A1 Prinses Christinastraat Echt 99369"
    assert message["vehicles"] == []  # 99369 is a ride number, not a unit
    for pager in ("P 1 BR woning Echt 243231", "BR container Echt", "BDH-01 Liftopsluiting"):
        fire = normalize_api_message({"id": "2", "melding": pager, "tekstmelding": "Brandweer", "dienstid": "2",
                                      "regioid": "21", "datum": "28-10", "tijd": "20:12", "capcodes": []})
        assert fire["dienstid"] == "2", pager


def test_normalize_api_message_enriches():
    message = normalize_api_message({
        "id": "26202451", "melding": "A1 AMBU 17103 Peperdonk 3206CB Spijkenisse SPIJKN bon 159921",
        "tekstmelding": "Ambulance met spoed naar Peperdonk, Spijkenisse", "dienstid": "3", "regioid": "10",
        "datum": "14-10", "tijd": "20:44", "grip": "0", "straat": "Peperdonk", "postcode": "3206CB",
        "capcodes": [{"capcode": "1420003", "omschrijving": "Ambulance 17-103 / VRR - AZRR"}],
    })
    assert message["message"] == "Ambulance met spoed naar Peperdonk, Spijkenisse"
    assert message["pager_text"].startswith("A1 AMBU 17103")
    assert (message["priority"], message["urgency"]) == ("A1", 1)
    assert message["vehicles"] == [{"id": "17-103"}]
    assert message["capcode_details"][0]["description"] == "Ambulance 17-103 / VRR - AZRR"
