"""Contracts for parcel census and provincial social-data integrations."""

from pathlib import Path

from land_registry.routers.enrichment import enrichment_router


ROOT = Path(__file__).resolve().parents[1]
PANEL_SOURCE = (ROOT / "land_registry" / "static" / "parcel-enrichment.js").read_text(
    encoding="utf-8"
)


def test_census_at_point_route_precedes_dynamic_census_route() -> None:
    paths = [route.path for route in enrichment_router.routes]

    assert paths.index("/census/at-point") < paths.index("/census/{cadastral_code}")


def test_panel_requests_all_social_enrichment_surfaces() -> None:
    assert "/api/v1/enrichment/census/at-point" in PANEL_SOURCE
    assert "/api/v1/enrichment/crime/" in PANEL_SOURCE
    assert "/api/v1/enrichment/demographics/" in PANEL_SOURCE
    assert "/api/v1/enrichment/quality-of-life/" in PANEL_SOURCE


def test_panel_renders_census_ratios_and_provincial_disclaimer() -> None:
    assert "employment_rate_working_age" in PANEL_SOURCE
    assert "education_tertiary_rate" in PANEL_SOURCE
    assert "foreign_resident_share" in PANEL_SOURCE
    assert "vacancy_rate" in PANEL_SOURCE
    assert "Dato aggregato a livello provinciale" in PANEL_SOURCE


def test_panel_limits_indicator_preview_and_ignores_stale_results() -> None:
    assert "INDICATOR_PREVIEW_LIMIT = 4" in PANEL_SOURCE
    assert "renderToken !== activeRenderToken" in PANEL_SOURCE
    assert "Promise.allSettled(tasks)" in PANEL_SOURCE
