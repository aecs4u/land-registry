"""MPS04 PGA is read from the shared stats PostgreSQL schema, with a file-store fallback."""

import pytest

from land_registry import stats_service


class _Source:
    def __init__(self, row):
        self._retry_at = 0.0
        self._row = row
        self.calls = []

    async def mps04_pga_at_point(self, lat, lng):
        self.calls.append((lat, lng))
        if isinstance(self._row, Exception):
            raise self._row
        return self._row


def _use_source(monkeypatch, source):
    async def fake():
        return source

    monkeypatch.setattr(stats_service, "_get_async_postgres_source", fake)


@pytest.mark.asyncio
async def test_postgres_row_is_mapped_to_the_panel_contract(monkeypatch):
    source = _Source({
        "point_id": "44952", "point_lat": 38.18478, "point_lng": 13.30041, "distance_m": 958.3,
        "pga_g": 0.15404, "pga_p16_g": 0.13492, "pga_p84_g": 0.16347,
    })
    _use_source(monkeypatch, source)

    result = await stats_service.aget_mps04_pga_at_point(38.19095, 13.308061)

    assert result["available"] is True and result["matched"] is True
    assert (result["pga_g"], result["pga_p16_g"], result["pga_p84_g"]) == (0.15404, 0.13492, 0.16347)
    assert result["point_id"] == "44952"
    assert result["exceedance_probability_pct"] == 10.0
    assert result["license"] == "CC BY 4.0"
    assert source.calls == [(38.19095, 13.308061)]


@pytest.mark.asyncio
async def test_point_outside_the_match_radius_is_available_but_unmatched(monkeypatch):
    _use_source(monkeypatch, _Source({}))

    result = await stats_service.aget_mps04_pga_at_point(48.85, 2.35)

    assert result["available"] is True
    assert result["matched"] is False
    assert "pga_g" not in result


@pytest.mark.asyncio
async def test_unprovisioned_schema_falls_back_to_the_local_store(monkeypatch):
    _use_source(monkeypatch, _Source(None))
    monkeypatch.setattr(
        stats_service, "get_mps04_pga_at_point",
        lambda lat, lng: {"available": False, "reason": "mps04_not_built", "from": "file-store"},
    )

    result = await stats_service.aget_mps04_pga_at_point(38.19, 13.3)

    assert result == {"available": False, "reason": "mps04_not_built", "from": "file-store"}


@pytest.mark.asyncio
async def test_query_failure_backs_off_and_uses_the_local_store(monkeypatch):
    source = _Source(RuntimeError("connection refused"))
    _use_source(monkeypatch, source)
    monkeypatch.setattr(stats_service, "get_mps04_pga_at_point", lambda lat, lng: {"from": "file-store"})

    result = await stats_service.aget_mps04_pga_at_point(38.19, 13.3)

    assert result == {"from": "file-store"}
    assert source._retry_at > 0
