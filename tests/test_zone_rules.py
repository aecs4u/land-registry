import pytest

from land_registry.zone_rules import (
    MICROZONE_WARNING_THRESHOLD_KM2,
    area_sqm_to_km2,
    geometry_metrics_from_geojson,
    is_large_microzone,
)
from land_registry.routers.api import _microzone_row_to_response


def test_area_sqm_to_km2_handles_none_and_invalid() -> None:
    assert area_sqm_to_km2(None) is None
    assert area_sqm_to_km2("not-a-number") is None


def test_area_sqm_to_km2_converts_correctly() -> None:
    assert area_sqm_to_km2(1_000_000) == 1.0
    assert area_sqm_to_km2(250_000) == 0.25


def test_is_large_microzone_threshold_boundary() -> None:
    boundary_sqm = MICROZONE_WARNING_THRESHOLD_KM2 * 1_000_000
    assert is_large_microzone(boundary_sqm) is False
    assert is_large_microzone(boundary_sqm + 1) is True


def test_is_large_microzone_with_missing_value() -> None:
    assert is_large_microzone(None) is False


def test_geometry_metrics_from_geojson_returns_geodesic_area_and_centroid() -> None:
    # ~0.01 x 0.01 degrees near equator (~1.23 km^2)
    feature = {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [0.0, 0.0],
                    [0.0, 0.01],
                    [0.01, 0.01],
                    [0.01, 0.0],
                    [0.0, 0.0],
                ]
            ],
        },
        "properties": {},
    }

    area_sqm, centroid_lat, centroid_lng = geometry_metrics_from_geojson(feature)

    assert area_sqm is not None
    assert 1_000_000 < area_sqm < 1_300_000
    assert centroid_lat == pytest.approx(0.005)
    assert centroid_lng == pytest.approx(0.005)


def test_microzone_response_threshold_boundary_fields() -> None:
    boundary_sqm = MICROZONE_WARNING_THRESHOLD_KM2 * 1_000_000
    base_row = {
        "id": 1,
        "zone_id": 10,
        "name": "M1",
        "description": None,
        "microzone_type": "polygon",
        "color": "#3388ff",
        "centroid_lat": 41.9,
        "centroid_lng": 12.5,
        "is_visible": 1,
        "tags": "[]",
        "created_at": "2026-02-19T00:00:00Z",
        "updated_at": "2026-02-19T00:00:00Z",
    }

    at_boundary = _microzone_row_to_response({**base_row, "area_sqm": boundary_sqm})
    above_boundary = _microzone_row_to_response({**base_row, "area_sqm": boundary_sqm + 1})

    assert at_boundary["area_km2"] == pytest.approx(MICROZONE_WARNING_THRESHOLD_KM2)
    assert at_boundary["is_large_area"] is False
    assert at_boundary["warning_threshold_km2"] == MICROZONE_WARNING_THRESHOLD_KM2

    assert above_boundary["is_large_area"] is True
