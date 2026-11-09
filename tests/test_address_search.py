"""Address search: parser, ANNCSU street/civic matching, geocoder ranking, UI contract."""

from pathlib import Path

import pytest

from land_registry.address_search import normalize_street, parse_address
from land_registry.anncsu_lookup import pick_civic, rank_streets
from land_registry.routers.api import _address_label, _rank_geocode_hits

ROOT = Path(__file__).resolve().parents[1] / "land_registry"
MAP_SCRIPT = (ROOT / "static" / "map-v2.js").read_text(encoding="utf-8")
MAP_CSS = (ROOT / "static" / "map-v2.css").read_text(encoding="utf-8")


def first(text):
    return parse_address(text)[0]


@pytest.mark.parametrize(
    "text, street, civic, exponent, municipality",
    [
        ("via goethe 1, Palermo", "goethe", "1", None, "palermo"),
        ("Via Goethe 1 Palermo", "goethe", "1", None, "palermo"),
        ("V. Goethe 14/A, Palermo", "goethe", "14", "A", "palermo"),
        ("via Roma 10 bis, Catania, CT", "roma", "10", "BIS", "catania"),
        ("p.zza Politeama 3, Palermo", "politeama", "3", None, "palermo"),
        ("via 4 novembre 12 palermo", "4 novembre", "12", None, "palermo"),
        ("goethe 1, palermo", "goethe", "1", None, "palermo"),
        ("Via Goethe 1, Palermo (PA)", "goethe", "1", None, "palermo"),
        ("via dell'Orsa 1 a Palermo", "dell orsa", "1", "A", "palermo"),
        ("Via Göthe 5, Palermo", "gothe", "5", None, "palermo"),
        ("via antigone 99, 90151 palermo", "antigone", "99", None, "palermo"),
        ("Via Antigone 99, 90151 Palermo (PA)", "antigone", "99", None, "palermo"),
        ("via antigone 99 90151 palermo", "antigone", "99", None, "palermo"),
        ("via roma 10, 00184 roma", "roma", "10", None, "roma"),
    ],
)
def test_parse_address(text, street, civic, exponent, municipality):
    parsed = first(text)
    assert (parsed.street, parsed.civic, parsed.exponent, parsed.municipality_hint) == (
        street, civic, exponent, municipality,
    )


def test_bare_province_is_not_a_municipality():
    assert first("V. Goethe 14/A (PA)").municipality_hint is None


def test_street_without_number_splits_municipality_tail():
    parsed = first("via goethe palermo")
    assert (parsed.street, parsed.civic, parsed.municipality_hint) == ("goethe", None, "palermo")


@pytest.mark.parametrize("text", ["Palermo", "C773 28 42", "C773_000028.0042", "ab", ""])
def test_non_addresses_are_not_parsed(text):
    assert parse_address(text) == []


def test_normalize_street_strips_type_and_accents():
    assert normalize_street("VIA GOETHE") == ("via", "goethe")
    assert normalize_street("Viale Strasburgo") == ("viale", "strasburgo")
    assert normalize_street("Via DELL'ORSA") == ("via", "dell orsa")


def _street(odonimo, accesses=10, street_id="1"):
    stype, key = normalize_street(odonimo)
    return {"street_id": street_id, "odonimo": odonimo, "type": stype, "key": key,
            "total_accesses": accesses, "comune_code": "G273"}


def test_rank_streets_prefers_exact_then_name_then_fuzzy():
    streets = [_street("VIA GOETHE", 86, "a"), _street("PIAZZA GOETHE", 5, "b"),
               _street("VIA GIOVANNI GOETHE", 3, "c"), _street("VIA ROMA", 900, "d")]
    ranked = rank_streets(streets, first("via goethe 1, palermo"))
    assert [(level, s["street_id"]) for level, s in ranked][:3] == [("exact", "a"), ("name", "b"), ("tokens", "c")]
    typo = rank_streets(streets, first("via gothe 1, palermo"))
    assert typo[0][0] == "fuzzy" and typo[0][1]["street_id"] == "a"


ROWS = [
    {"civic_number": "1", "exponent": None, "latitude": 38.12, "longitude": 13.35, "coordinate_method": "3"},
    {"civic_number": "14", "exponent": "A", "latitude": 38.121, "longitude": 13.351, "coordinate_method": "3"},
    {"civic_number": "14", "exponent": None, "latitude": 38.122, "longitude": 13.352, "coordinate_method": "3"},
    {"civic_number": "78", "exponent": None, "latitude": 38.123, "longitude": 13.353, "coordinate_method": "3"},
]


def test_pick_civic_levels():
    assert pick_civic(ROWS, "1", None)[0:2] == ("address", ROWS[0])
    assert pick_civic(ROWS, "14", "A")[1] is ROWS[1]
    assert pick_civic(ROWS, "14", None)[1] is ROWS[2]
    level, row, nearby = pick_civic(ROWS, "14", "Z")
    assert level == "address_exponent_dropped" and row is not None and "14/A" in nearby
    level, row, nearby = pick_civic(ROWS, "75", None)
    assert level == "street" and row is None and nearby[0] == "78"
    assert pick_civic(ROWS, None, None)[0] == "street"


def test_address_label():
    base = {"street": "VIA GOETHE", "civic": "14", "exponent": "A", "match_level": "address"}
    assert _address_label(base, "Palermo") == "Via Goethe 14/A, Palermo"
    missing = {"street": "VIA GOETHE", "civic": None, "match_level": "street", "requested_civic": "999"}
    assert _address_label(missing, "Palermo") == "Via Goethe, Palermo (number 999 not found)"


def test_geocode_ranking_prefers_house_and_drops_other_cities():
    hits = [
        {"lat": "38.1", "lon": "13.3", "display_name": "Via Goethe, Palermo", "category": "highway",
         "addresstype": "road", "address": {"city": "Palermo"}},
        {"lat": "38.2", "lon": "13.4", "display_name": "1, Via Goethe, Palermo", "addresstype": "house",
         "address": {"city": "Palermo", "house_number": "1"}},
        {"lat": "41.0", "lon": "12.0", "display_name": "Via Goethe, Roma", "category": "highway",
         "address": {"city": "Roma"}},
    ]
    results = _rank_geocode_hits(hits, "Palermo")
    assert [r["match_level"] for r in results] == ["address", "street"]
    assert all(r["source"] == "openstreetmap" for r in results)


def test_ui_marker_parcel_and_sheet_contract():
    # Address pick: marker labelled with the address, parcel lookup, parcel window.
    assert "chooseAddressResult" in MAP_SCRIPT
    assert "result.kind === 'address'" in MAP_SCRIPT
    assert "/api/v1/enrichment/parcel/at-point" in MAP_SCRIPT
    assert "await loadParcelByReference(reference, true" in MAP_SCRIPT
    assert "text.textContent = label" in MAP_SCRIPT  # label is text, never HTML
    assert ".search-address-marker-label" in MAP_CSS


def test_browser_no_longer_calls_nominatim_directly():
    assert "nominatim.openstreetmap.org" not in MAP_SCRIPT
    assert "/api/v1/map/geocode" in MAP_SCRIPT


def test_shortlist_follows_search_card_height():
    assert "--map-search-bottom" in MAP_CSS and "--map-search-bottom" in MAP_SCRIPT


def test_address_search_records_outcome_counters_without_query_text(monkeypatch):
    import asyncio

    from land_registry.anncsu_lookup import AddressMatch
    from land_registry.map_observability import map_metrics
    from land_registry.routers import api

    class Source:
        async def search_municipalities(self, name, limit):
            return [{"canonical_name": "Palermo", "istat_code": "82053"}]

    def fake_find(istat, parsed, limit=3):
        return [AddressMatch("address", "VIA GOETHE", "1", "082053", None, "1", None, 38.1, 13.3, "3", [])]

    monkeypatch.setattr("land_registry.anncsu_lookup.find_addresses", fake_find)
    before = map_metrics.snapshot()["events"].get("address_search.address", 0)
    results = asyncio.run(api._search_addresses("via goethe 1, Palermo", Source(), 5))
    assert results[0]["match_level"] == "address"
    snapshot = map_metrics.snapshot()
    assert snapshot["events"]["address_search.address"] == before + 1
    assert "goethe" not in str(snapshot).lower()

    monkeypatch.setattr("land_registry.anncsu_lookup.find_addresses", lambda *a, **k: [])
    before = snapshot["events"].get("address_search.no_match", 0)
    assert asyncio.run(api._search_addresses("via nowhere 1, Palermo", Source(), 5)) == []
    assert map_metrics.snapshot()["events"]["address_search.no_match"] == before + 1


def test_ui_url_and_tolerance_contract():
    assert "addr_lat" in MAP_SCRIPT and "restoreAddressMarkerFromUrl" in MAP_SCRIPT
    assert "parcelNearPoint" in MAP_SCRIPT


def test_address_search_degrades_quietly_when_the_store_is_missing(monkeypatch):
    """No ANNCSU region / unreachable database: no address results, no exception."""
    import asyncio

    from land_registry.routers import api

    class Source:
        async def search_municipalities(self, name, limit):
            return [{"canonical_name": "Palermo", "istat_code": "82053"}]

    def unreachable(*args, **kwargs):
        raise RuntimeError("anncsu database unavailable")

    with monkeypatch.context() as scoped:
        scoped.setattr("land_registry.anncsu_lookup.find_addresses", unreachable)
        assert asyncio.run(api._search_addresses("via goethe 1, Palermo", Source(), 5)) == []

    # No region with both tables: the lookup itself returns [] rather than raising.
    from land_registry import anncsu_lookup

    class Conn:
        def close(self):
            pass

    monkeypatch.setattr(anncsu_lookup, "_connect", lambda: Conn())
    monkeypatch.setattr(anncsu_lookup, "_loaded_regions", lambda conn: {})
    assert anncsu_lookup.find_addresses("082053", first("via goethe 1, Palermo")) == []


PARCEL_NEAR_VIA_GOETHE = {
    "type": "Feature", "properties": {"region": "SICILIA"},
    "geometry": {"type": "Polygon", "coordinates": [[
        [13.3505, 38.1199], [13.3512, 38.1199], [13.3512, 38.1204], [13.3505, 38.1204], [13.3505, 38.1199],
    ]]},
}


def test_parcel_panel_block_falls_back_to_the_schema_aware_reader(monkeypatch):
    """Package says the store is missing (tables not in public); the twin reader serves it."""
    from land_registry import anncsu_lookup, stats_service

    class Package:
        @staticmethod
        def anncsu_region_db_status(region):
            return {"ready": False, "state": "missing"}

        @staticmethod
        def civic_addresses_for_geometry(*args, **kwargs):
            raise AssertionError("the package reader must not be used for a missing store")

    monkeypatch.setattr(stats_service, "_anncsu", Package)
    monkeypatch.setattr(anncsu_lookup, "region_status", lambda region: {"ready": True, "state": "ready"})
    monkeypatch.setattr(anncsu_lookup, "civic_addresses_for_geometry", lambda *a, **k: {
        "available": True, "matched": True, "status": "matched", "addresses": [{"address": "VIA GOETHE 1"}],
        "address_count": 1, "shown_count": 1,
    })
    block = stats_service._anncsu_addresses_detail_block(PARCEL_NEAR_VIA_GOETHE, {"region": "SICILIA"}, "G273")
    assert block["available"] is True and block["status"] == "matched"

    # Not loaded in either schema: still the documented "not built" reason.
    monkeypatch.setattr(anncsu_lookup, "region_status", lambda region: None)
    assert stats_service._anncsu_addresses_detail_block(
        PARCEL_NEAR_VIA_GOETHE, {"region": "SICILIA"}, "G273")["available"] is False


def test_schema_aware_reader_finds_addresses_in_a_parcel():
    from land_registry import anncsu_lookup

    try:
        status = anncsu_lookup.region_status("SICILIA")
    except Exception as exc:
        pytest.skip(f"ANNCSU database unavailable: {type(exc).__name__}")
    if not status or not status["ready"]:
        pytest.skip("Sicily ANNCSU store not loaded")
    data = anncsu_lookup.civic_addresses_for_geometry(PARCEL_NEAR_VIA_GOETHE, "SICILIA", cadastral_code="G273")
    assert data and data["status"] in {"matched", "partial"}
    assert any("GOETHE" in (a["street"] or "") for a in data["addresses"])


def test_busy_municipality_lookup_is_retried_remembered_and_never_reported_as_no_match():
    import asyncio

    from fastapi import HTTPException

    from land_registry.routers import api

    api._address_municipality_cache.clear()

    class Flaky:
        calls = 0

        async def search_municipalities(self, name, limit):
            Flaky.calls += 1
            if Flaky.calls == 1:
                raise asyncio.TimeoutError()
            return [{"canonical_name": "Palermo", "istat_code": "82053"}]

    first = asyncio.run(api._resolve_address_municipalities(Flaky(), "palermo"))
    assert first[0]["canonical_name"] == "Palermo" and Flaky.calls == 2  # one retry
    asyncio.run(api._resolve_address_municipalities(Flaky(), "palermo"))
    assert Flaky.calls == 2  # second call served from the cache

    class Down:
        async def search_municipalities(self, name, limit):
            raise RuntimeError("pool exhausted")

    api._address_municipality_cache.clear()
    with pytest.raises(HTTPException) as caught:
        asyncio.run(api._search_addresses("via antigone 99, 90151 palermo", Down(), 5))
    assert caught.value.status_code == 503 and caught.value.headers["Retry-After"]


def test_postal_code_in_the_query_is_ignored_end_to_end(monkeypatch):
    import asyncio

    from land_registry.anncsu_lookup import AddressMatch
    from land_registry.routers import api

    api._address_municipality_cache.clear()
    seen = {}

    class Source:
        async def search_municipalities(self, name, limit):
            seen["hint"] = name
            return [{"canonical_name": "Palermo", "istat_code": "82053"}]

    monkeypatch.setattr(
        "land_registry.anncsu_lookup.find_addresses",
        lambda istat, parsed, limit=3: [AddressMatch("address", "VIA ANTIGONE", "9", "082053", None, "99", None, 38.19, 13.31, "3", [])],
    )
    results = asyncio.run(api._search_addresses("via antigone 99, 90151 palermo", Source(), 5))
    assert seen["hint"] == "palermo" and results[0]["label"] == "Via Antigone 99, Palermo"
