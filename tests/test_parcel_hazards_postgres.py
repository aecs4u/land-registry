"""Parcel hazard and EGMS summaries accept PostgreSQL candidates and fall back cleanly."""

import pytest

from land_registry import stats_service

PARCEL = {
    "type": "Polygon",
    "coordinates": [[[13.0, 38.0], [13.001, 38.0], [13.001, 38.001], [13.0, 38.001], [13.0, 38.0]]],
}


def _square(west, south, east, north):
    return {
        "type": "Polygon",
        "coordinates": [[[west, south], [east, south], [east, north], [west, north], [west, south]]],
    }


def _collection(features):
    return {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {"total_count": len(features)},
    }


def test_prefetched_hazard_polygons_are_intersected_with_the_parcel():
    landslide = _collection([
        {"type": "Feature", "properties": {"hazard_code": "P3"}, "geometry": _square(13.0, 38.0, 13.0005, 38.001)},
    ])
    flood = _collection([
        {"type": "Feature", "properties": {"scenario_code": "P2"}, "geometry": _square(14.0, 40.0, 14.1, 40.1)},
    ])

    result = stats_service.get_parcel_hazard_intersections(PARCEL, collections=(landslide, flood))

    assert result["available"] is True
    assert result["landslide"]["worst_class"] == "P3"
    assert 45 < result["landslide"]["affected_area_pct"] < 55
    assert result["flood"]["worst_class"] is None
    assert result["coverage_complete"] is True


def test_prefetched_egms_cells_are_weighted_by_overlap():
    cells = _collection([
        {"type": "Feature", "properties": {"mean_velocity": -2.0, "risk_class_label": "basso"},
         "geometry": _square(13.0, 38.0, 13.0005, 38.001)},
        {"type": "Feature", "properties": {"mean_velocity": 0.0, "risk_class_label": "trascurabile"},
         "geometry": _square(13.0005, 38.0, 13.001, 38.001)},
    ])

    result = stats_service.get_parcel_subsidence_intersections(PARCEL, collection=cells)

    assert result["available"] is True and result["matched"] is True
    assert result["cell_count"] == 2
    assert result["mean_velocity_mm_per_year"] == pytest.approx(-1.0, abs=0.05)
    assert result["dominant_class"] in {"basso", "trascurabile"}


class _Source:
    def __init__(self, *, hazards=None, egms=None, provisioned=True, error=None):
        self._retry_at = 0.0
        self._hazards, self._egms, self._provisioned, self._error = hazards, egms, provisioned, error

    async def hazard_polygons_in_bbox(self, west, south, east, north, *, limit):
        if self._error:
            raise self._error
        return self._hazards

    async def egms_cells_in_bbox(self, west, south, east, north, *, limit):
        if self._error:
            raise self._error
        return self._egms

    async def _relations_available(self, relations):
        return self._provisioned


def _use_source(monkeypatch, source):
    async def fake():
        return source

    monkeypatch.setattr(stats_service, "_get_async_postgres_source", fake)


@pytest.mark.asyncio
async def test_hazards_use_postgres_candidates_and_label_the_storage(monkeypatch):
    _use_source(monkeypatch, _Source(hazards=(_collection([]), _collection([]))))

    result = await stats_service.aget_parcel_hazard_intersections(PARCEL)

    assert result["available"] is True
    assert result["storage"] == "aecs4u-stats PostgreSQL"
    assert result["landslide"]["affected_area_pct"] == 0.0


@pytest.mark.asyncio
async def test_unprovisioned_hazards_fall_back_to_the_local_store(monkeypatch):
    _use_source(monkeypatch, _Source(hazards=None))
    monkeypatch.setattr(
        stats_service, "get_parcel_hazard_intersections",
        lambda geometry, **kwargs: {"available": False, "reason": "ispra_mosaics_not_built", "kwargs": kwargs},
    )

    result = await stats_service.aget_parcel_hazard_intersections(PARCEL)

    assert result == {"available": False, "reason": "ispra_mosaics_not_built", "kwargs": {}}


@pytest.mark.asyncio
async def test_egms_without_postgis_pushdown_reports_why(monkeypatch):
    _use_source(monkeypatch, _Source(egms=None, provisioned=True))
    monkeypatch.setattr(
        stats_service, "get_parcel_subsidence_intersections",
        lambda geometry, **kwargs: {"available": False, "reason": "egms_not_built"},
    )

    result = await stats_service.aget_parcel_subsidence_intersections(PARCEL)

    assert result["available"] is False
    assert result["reason"] == "egms_postgres_pushdown_not_configured"


@pytest.mark.asyncio
async def test_postgres_failure_backs_off_and_uses_the_local_store(monkeypatch):
    source = _Source(error=RuntimeError("connection refused"))
    _use_source(monkeypatch, source)
    monkeypatch.setattr(
        stats_service, "get_parcel_hazard_intersections",
        lambda geometry, **kwargs: {"available": True, "from": "file-store"},
    )

    result = await stats_service.aget_parcel_hazard_intersections(PARCEL)

    assert result == {"available": True, "from": "file-store"}
    assert source._retry_at > 0
