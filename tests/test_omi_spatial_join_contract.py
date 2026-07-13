"""Contracts for automatic parcel-to-OMI-zone matching."""

from pathlib import Path

import pytest

from land_registry import stats_service
from land_registry.routers import enrichment as enrichment_module
from land_registry.routers.enrichment import enrichment_router


ROOT = Path(__file__).resolve().parents[1]
PANEL_SOURCE = (ROOT / "land_registry" / "static" / "parcel-enrichment.js").read_text(
    encoding="utf-8"
)
STYLE_SOURCE = (ROOT / "land_registry" / "static" / "styles.css").read_text(
    encoding="utf-8"
)


def test_point_inside_boundary_returns_quote_compatible_zone(monkeypatch) -> None:
    boundaries = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"name": "Zona OMI B1"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[12, 41], [13, 41], [13, 42], [12, 42], [12, 41]]],
                },
            }
        ],
    }
    monkeypatch.setattr(stats_service, "zone_boundaries_available", lambda province: True)
    monkeypatch.setattr(stats_service, "_load_omi_zone_boundaries", lambda province: boundaries)

    result = stats_service.get_omi_zone_at_point("Roma", lat=41.5, lng=12.5)

    assert result["matched"] is True
    assert result["zone"] == "B1"


def test_missing_boundaries_degrade_to_an_unmatched_result(monkeypatch) -> None:
    monkeypatch.setattr(stats_service, "zone_boundaries_available", lambda province: False)

    result = stats_service.get_omi_zone_at_point("Roma", lat=41.5, lng=12.5)

    assert result["matched"] is False
    assert result["reason"] == "boundary_data_unavailable"


@pytest.mark.asyncio
async def test_at_point_endpoint_dispatches_spatial_lookup(monkeypatch) -> None:
    monkeypatch.setattr(
        enrichment_module.stats_service,
        "get_omi_zone_at_point",
        lambda province, lat, lng: {"matched": True, "zone": "C2", "province": province},
    )

    result = await enrichment_module.get_omi_zone_at_point("Milano", 45.46, 9.19)

    assert result == {"matched": True, "zone": "C2", "province": "Milano"}
    assert "/omi/at-point" in [route.path for route in enrichment_router.routes]


def test_panel_prefers_detected_zone_but_keeps_manual_selector() -> None:
    assert "/api/v1/enrichment/omi/at-point" in PANEL_SOURCE
    assert "Zona OMI rilevata automaticamente" in PANEL_SOURCE
    assert "rilevata, ma senza quotazioni disponibili" in PANEL_SOURCE
    assert "detectedZoneHasQuotes" in PANEL_SOURCE
    assert "aMatch - bMatch" in PANEL_SOURCE
    assert 'id="omiQuoteSelect"' in PANEL_SOURCE
    assert ".omi-zone-match" in STYLE_SOURCE
    assert ".omi-zone-match.is-warning" in STYLE_SOURCE
    assert "body.dark-mode .omi-zone-match" in STYLE_SOURCE
