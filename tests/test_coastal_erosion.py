from __future__ import annotations

import pytest

from land_registry import stats_service


@pytest.fixture
def parcel_polygon() -> dict:
    return {
        "type": "Polygon",
        "coordinates": [[
            [11.7, 42.0],
            [11.8, 42.0],
            [11.8, 42.1],
            [11.7, 42.1],
            [11.7, 42.0],
        ]],
    }


def _segment(object_id: int, classification: str = "Erosione") -> dict:
    return {
        "object_id": object_id,
        "cod_istat": 12058032,
        "change_class": classification,
        "coast_type": 1,
        "shoreline_length_m": 120.0,
        "mean_variation_m": -7.0 if classification == "Erosione" else 0.0,
        "max_variation_m": -10.0 if classification == "Erosione" else 0.0,
        "severity_index": 2,
        "source_release": "ISPRA_LINEA_COSTA_2020_V2_DYNAMICA_2006_2020",
        "distance_m": float(object_id),
    }


class _CoastalSource:
    _retry_at = 0.0

    def __init__(self, rows: list[dict], status: dict | None = None) -> None:
        self.rows = rows
        self.status = status or {
            "available": True,
            "reason": None,
            "feature_count": 99_229,
            "imported_at": "2026-10-15T09:45:26+00:00",
            "source_version": "ISPRA_LINEA_COSTA_2020_V2_DYNAMICA_2006_2020",
        }
        self.radius_m = None
        self.limit = None

    async def coastal_dynamics_store_status(self) -> dict:
        return self.status

    async def coastal_changes_near_geometry(
        self, geometry: str, *, radius_m: float, limit: int
    ) -> list[dict]:
        self.radius_m = radius_m
        self.limit = limit
        return self.rows


def _use_source(monkeypatch, source: _CoastalSource) -> None:
    async def get_source():
        return source

    monkeypatch.setattr(stats_service, "_get_async_postgres_source", get_source)


@pytest.mark.parametrize("classification", ["Erosione", "Stabilità", "Avanzamento"])
@pytest.mark.asyncio
async def test_coastal_change_classes_keep_source_and_method_metadata(
    monkeypatch, parcel_polygon: dict, classification: str
) -> None:
    source = _CoastalSource([_segment(12, classification)])
    _use_source(monkeypatch, source)

    result = await stats_service.aget_parcel_coastal_erosion(parcel_polygon)

    assert result["available"] is True
    assert result["matched"] is True
    assert result["classification_counts"] == {classification: 1}
    assert result["nearest_segment"]["classification"] == classification
    assert result["data_vintage"] == "2006–2020"
    assert result["license"] == "Creative Commons Attribution 4.0 International (CC BY 4.0)"
    assert result["positional_accuracy_m"] == 5.0
    assert result["classification_threshold_m"] == 5.0
    assert result["match_method"] == "parcel_polygon_distance_to_shoreline_segment"
    assert source.radius_m == 1000.0


@pytest.mark.asyncio
async def test_no_nearby_segments_is_unknown_coverage_not_zero(
    monkeypatch, parcel_polygon: dict
) -> None:
    source = _CoastalSource([])
    _use_source(monkeypatch, source)

    result = await stats_service.aget_parcel_coastal_erosion(parcel_polygon)

    assert result["available"] is True
    assert result["matched"] is False
    assert result["coverage"] == "unknown"
    assert result["coverage_status"] == "no_mapped_segment_within_1km"
    assert result["segments"] == []


@pytest.mark.asyncio
async def test_unavailable_store_has_a_distinct_reason(
    monkeypatch, parcel_polygon: dict
) -> None:
    source = _CoastalSource([], {"available": False, "reason": "not_built"})
    _use_source(monkeypatch, source)

    result = await stats_service.aget_parcel_coastal_erosion(parcel_polygon)

    assert result["available"] is False
    assert result["reason"] == "coastal_store_not_built"
    assert result["data_vintage"] == "2006–2020"


@pytest.mark.asyncio
async def test_candidate_cap_marks_incomplete_coverage(
    monkeypatch, parcel_polygon: dict
) -> None:
    limit = stats_service._COASTAL_CANDIDATE_LIMIT
    source = _CoastalSource([_segment(index) for index in range(1, limit + 2)])
    _use_source(monkeypatch, source)

    result = await stats_service.aget_parcel_coastal_erosion(parcel_polygon)

    assert source.limit == limit
    assert result["available"] is True
    assert result["candidate_limit"] == limit
    assert result["segment_count"] == limit
    assert result["truncated"] is True
    assert result["query_complete"] is False
    assert result["coverage"] == "partial"
    assert result["coverage_status"] == "partial"
