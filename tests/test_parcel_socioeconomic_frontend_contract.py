"""Contracts for parcel-level census and socioeconomic enrichment cards."""

from pathlib import Path

import pytest

from land_registry.routers import enrichment as enrichment_module
from land_registry.routers.enrichment import enrichment_router


STATIC_FILE = (
    Path(__file__).resolve().parents[1]
    / "land_registry"
    / "static"
    / "parcel-enrichment.js"
)
STATS_FILE = (
    Path(__file__).resolve().parents[1]
    / "land_registry"
    / "stats_service.py"
)


def test_census_at_point_route_precedes_dynamic_census_route() -> None:
    paths = [route.path for route in enrichment_router.routes]

    assert paths.index("/census/at-point") < paths.index("/census/{cadastral_code}")


def test_parcel_read_model_route_is_exposed() -> None:
    paths = [route.path for route in enrichment_router.routes]

    assert "/parcel/details/{national_reference}" in paths


@pytest.mark.asyncio
async def test_census_at_point_dispatches_to_spatial_lookup(monkeypatch) -> None:
    feature = {
        "type": "Feature",
        "properties": {"sez21_id": 123, "p1": 42, "ratios": {}},
        "geometry": None,
    }
    monkeypatch.setattr(enrichment_module.stats_service, "census_db_available", lambda: True)
    monkeypatch.setattr(
        enrichment_module.stats_service,
        "get_census_section_at_point",
        lambda lat, lng: feature,
    )
    response = await enrichment_module.get_census_section_at_point(lat=41.9, lng=12.5)

    assert response["properties"]["sez21_id"] == 123


def test_parcel_panel_requests_all_socioeconomic_sources() -> None:
    source = STATIC_FILE.read_text(encoding="utf-8")

    assert "/api/v1/enrichment/census/at-point" in source
    assert "/api/v1/enrichment/crime/" in source
    assert "/api/v1/enrichment/demographics/" in source
    assert "/api/v1/enrichment/quality-of-life/" in source


def test_census_card_surfaces_derived_rates_and_resolution() -> None:
    source = STATIC_FILE.read_text(encoding="utf-8")

    assert "employment_rate_working_age" in source
    assert "education_tertiary_rate" in source
    assert "foreign_resident_share" in source
    assert "vacancy_rate" in source
    assert "ISTAT Basi Territoriali 2021" in source


def test_municipality_card_surfaces_extended_istat_profile() -> None:
    source = STATIC_FILE.read_text(encoding="utf-8")
    stats_source = STATS_FILE.read_text(encoding="utf-8")

    for field in ("population_history", "tax_code", "email", "pec_email", "website", "wikipedia_url"):
        assert field in source
        assert field in stats_source
    assert "_istat_sqlite_path" in stats_source
    assert "census_sections.IT.duckdb" in stats_source


def test_parcel_panel_prefers_the_parcel_read_model() -> None:
    source = STATIC_FILE.read_text(encoding="utf-8")

    assert "/api/v1/enrichment/parcel/details/" in source
    assert "readModel.omi" in source
    assert "readModel.census" in source


def test_read_model_declares_reference_catalog_blocks() -> None:
    stats_source = STATS_FILE.read_text(encoding="utf-8")
    panel_source = STATIC_FILE.read_text(encoding="utf-8")

    for block in (
        "basic", "cadastral", "address", "addresses", "risk", "subsidence", "terrain", "population",
        "buildings", "economics", "demographics", "land_cover", "land_use",
        "valuation", "valuation_history", "coastal_erosion", "cultural_heritage",
        "solar", "poi", "nightlights",
    ):
        assert f'"{block}"' in stats_source
    assert "available" in panel_source
    assert "match_method" in stats_source


def test_province_level_cards_disclose_their_resolution() -> None:
    source = STATIC_FILE.read_text(encoding="utf-8")

    assert "Dato aggregato a livello provinciale" in source
    assert "Indicatori a livello provinciale" in source


def test_stale_async_responses_are_ignored() -> None:
    source = STATIC_FILE.read_text(encoding="utf-8")

    assert "activeRenderToken" in source
    assert "renderToken !== activeRenderToken" in source
