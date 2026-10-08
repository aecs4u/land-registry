"""Regression tests for the asyncpg request-path adapters."""

import sqlite3
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from land_registry import stats_service


def test_asyncpg_placeholder_translation():
    assert stats_service._asyncpg_sql("SELECT %s, %s::jsonb") == "SELECT $1, $2::jsonb"


@pytest.mark.asyncio
async def test_async_municipality_lookup_does_not_use_legacy_adapter(monkeypatch):
    class FakeSource:
        _retry_at = 0.0

        async def municipality_by_cadastral_code(self, code):
            return {"cadastral_code": code, "name": "Async municipality"}

    async def source_factory(*, poi=False):
        assert poi is False
        return FakeSource()

    monkeypatch.setattr(stats_service, "_get_async_postgres_source", source_factory)
    monkeypatch.setattr(
        stats_service,
        "get_municipality_by_cadastral_code",
        lambda *args, **kwargs: pytest.fail("legacy synchronous lookup was used"),
    )

    result = await stats_service.aget_municipality_by_cadastral_code("H501")

    assert result == {"cadastral_code": "H501", "name": "Async municipality"}


@pytest.mark.asyncio
@pytest.mark.parametrize("relation", ["facts.poi", "source_osm_pois.osm_pois"])
async def test_async_poi_lookup_uses_available_relation(monkeypatch, relation):
    source = stats_service._AsyncPostgresSource("postgresql://localhost/stats")
    probe = AsyncMock(return_value={"relation": relation})
    fetch = AsyncMock(return_value=[
        {"code": "schools", "name": "School", "st_y": 41.9, "st_x": 12.5, "distance_km": 0.1234},
    ])
    monkeypatch.setattr(source, "_fetchrow", probe)
    monkeypatch.setattr(source, "_fetch", fetch)
    monkeypatch.setattr(stats_service, "_get_async_postgres_source", AsyncMock(return_value=source))
    local = Mock(side_effect=AssertionError("local fallback was used"))
    monkeypatch.setattr(stats_service, "pois_within_radius", local)

    result = await stats_service.aget_pois_near(41.9, 12.5, radius_km=2, categories=["schools"])
    await stats_service.aget_pois_near(41.9, 12.5, radius_km=2, categories=["schools"])

    assert result["categories"] == {
        "schools": [{"lat": 41.9, "lng": 12.5, "name": "School", "distance_km": 0.123}],
    }
    assert result["total"] == 1
    assert result["source"] == "OpenStreetMap via aecs4u-stats (PostGIS asyncpg)"
    probe.assert_awaited_once()
    sql, params = fetch.call_args.args
    assert f"FROM {relation} p" in sql
    assert "ORDER BY distance_km" in sql
    assert "ST_DWithin" in sql
    assert "schools" in params
    assert 2000 in params
    if relation == "facts.poi":
        assert "JOIN facts.poi_category" in sql
        assert "c.code IN (%s)" in sql
    else:
        assert "facts.poi_category" not in sql
        assert "p.category IN (%s)" in sql
        assert "p.latitude BETWEEN %s AND %s" in sql
        assert "p.longitude BETWEEN %s AND %s" in sql
        assert all(isinstance(value, (int, float)) for value in params[:-1])
    local.assert_not_called()


@pytest.mark.asyncio
async def test_missing_postgres_poi_relations_use_local_store_and_recover(monkeypatch, tmp_path, caplog):
    """A missing optional schema must not trigger queries or prevent later recovery."""
    db_path = tmp_path / "osm_pois.IT.sqlite"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE osm_pois "
            "(category TEXT, name TEXT, latitude REAL, longitude REAL, tags_json TEXT)"
        )
        connection.executemany(
            "INSERT INTO osm_pois VALUES (?, ?, ?, ?, ?)",
            [
                ("schools", "Local school", 41.9, 12.5, "{}"),
                ("schools", "Distant school", 42.9, 12.5, "{}"),
                ("parks", "Local park", 41.9, 12.5, "{}"),
            ],
        )
    monkeypatch.setenv("OSM_POI_DB", str(db_path))
    source = stats_service._AsyncPostgresSource("postgresql://localhost/stats")
    probe = AsyncMock(side_effect=[{"relation": None}, {"relation": "source_osm_pois.osm_pois"}])
    fetch = AsyncMock(return_value=[])
    monkeypatch.setattr(source, "_fetchrow", probe)
    monkeypatch.setattr(source, "_fetch", fetch)
    monkeypatch.setattr(stats_service, "_get_async_postgres_source", AsyncMock(return_value=source))

    for _ in range(2):
        result = await stats_service.aget_pois_near(41.9, 12.5, radius_km=1, categories=["schools"])
        assert result["total"] == 1
        assert result["categories"]["schools"][0]["name"] == "Local school"
        assert result["categories"]["schools"][0]["distance_km"] == 0
        assert result["source"] == "OpenStreetMap via aecs4u-stats"
    probe.assert_awaited_once()
    fetch.assert_not_awaited()
    assert not [record for record in caplog.records if record.exc_info]

    source._poi_relation_cache = (None, 0.0)
    result = await stats_service.aget_pois_near(41.9, 12.5, categories=["schools"])
    assert result["total"] == 0
    assert result["source"] == "OpenStreetMap via aecs4u-stats (PostGIS asyncpg)"
    assert probe.await_count == 2
    fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_postgres_and_local_poi_stores_return_empty(monkeypatch):
    source = stats_service._AsyncPostgresSource("postgresql://localhost/stats")
    monkeypatch.setattr(source, "_fetchrow", AsyncMock(return_value={"relation": None}))
    fetch = AsyncMock(side_effect=AssertionError("missing table was queried"))
    monkeypatch.setattr(source, "_fetch", fetch)
    monkeypatch.setattr(stats_service, "_get_async_postgres_source", AsyncMock(return_value=source))
    monkeypatch.setattr("aecs4u_stats.osm.pois.resolve_poi_db", lambda *args, **kwargs: None)

    result = await stats_service.aget_pois_near(41.9, 12.5, categories=["schools"])

    assert result["total"] == 0
    assert result["categories"] == {"schools": []}
    fetch.assert_not_awaited()


@pytest.mark.parametrize("relation", ["facts.poi", "source_osm_pois.osm_pois", None])
def test_sync_poi_lookup_uses_available_relation_or_local_fallback(monkeypatch, relation):
    source = stats_service._PostgresPoiSource("postgresql://localhost/stats")
    cursor = MagicMock()
    cursor.fetchone.return_value = (relation,)
    cursor.fetchall.return_value = [("schools", "School", 41.9, 12.5, 0.1234)]
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value = cursor

    @contextmanager
    def get_connection():
        yield connection

    monkeypatch.setattr(source, "_connection", get_connection)
    monkeypatch.setattr(stats_service, "_get_postgres_poi_source", lambda: source)
    local = Mock(return_value={"schools": []})
    monkeypatch.setattr(stats_service, "pois_within_radius", local)

    result = stats_service.get_pois_near(41.9, 12.5, categories=["schools"], use_postgres=True)

    if relation is None:
        assert result["total"] == 0
        local.assert_called_once_with(41.9, 12.5, radius_km=1.0, categories=["schools"])
        cursor.fetchall.assert_not_called()
    else:
        assert result["categories"]["schools"][0]["distance_km"] == 0.123
        assert result["source"] == "OpenStreetMap via aecs4u-stats (PostGIS)"
        assert f"FROM {relation} p" in cursor.execute.call_args.args[0]
        local.assert_not_called()
