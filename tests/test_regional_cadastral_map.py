"""Regression tests for maps backed by the dedicated regional database."""

import asyncio
from contextlib import asynccontextmanager

import pytest

from land_registry.cadastral_map_views import regional_health, view_branch
from land_registry.map_layers import PostgresMapLayerSource, _AsyncpgConnectionSource


class Connection:
    def __init__(self, *, relation=None, row=None, rows=()):
        self.relation = relation
        self.row = row
        self.rows = rows
        self.calls = []

    async def fetchval(self, sql, *params):
        self.calls.append((sql, params))
        return self.relation if "to_regclass" in sql else b"regional-tile"

    async def fetchrow(self, sql, *params):
        self.calls.append((sql, params))
        return self.row

    async def fetch(self, sql, *params):
        self.calls.append((sql, params))
        return self.rows


class Source:
    def __init__(self, connection):
        self.conn = connection
        self.closed = False

    @asynccontextmanager
    async def connection(self):
        yield self.conn

    async def close(self):
        self.closed = True


def regional_source(*, row=None, rows=()):
    stats = Source(Connection())
    regional = Source(Connection(relation="spatial.cadastral_parcel", row=row, rows=rows))
    return PostgresMapLayerSource(stats, cadastral_connection_source=regional), stats, regional


def test_legacy_canonical_installations_keep_their_existing_source():
    stats = Source(Connection(relation="spatial.cadastral_parcel"))
    regional = Source(Connection())
    source = PostgresMapLayerSource(stats, cadastral_connection_source=regional)
    assert asyncio.run(source.read_mvt("cadastral-parcels", 16, 35000, 23000)) == b"regional-tile"
    assert not regional.conn.calls


def test_tiles_resolve_missing_canonical_relations_to_regional_views_and_cache_the_route():
    source, stats, regional = regional_source()

    async def run():
        assert await source.read_mvt("cadastral-parcels", 16, 35000, 23000) == b"regional-tile"
        assert await source.read_mvt("cadastral-parcels", 16, 35001, 23000) == b"regional-tile"

    asyncio.run(run())
    assert len(stats.conn.calls) == 1
    assert all("to_regclass" in sql for sql, _ in stats.conn.calls)
    assert "ST_AsMVT" in regional.conn.calls[-1][0]
    assert "t.municipality_name" in regional.conn.calls[-1][0]


@pytest.mark.parametrize("method,args", [
    ("read_feature_at_point", ("cadastral-parcels", 41.5, 14.0)),
    ("read_feature_by_reference", ("cadastral-parcels", "A032_001000.427")),
    ("read_geojson", ("cadastral-parcels", (13.99, 41.49, 14.01, 41.51), 100)),
    ("read_adjacent_features", ("cadastral-parcels", "A032_001000.427")),
    ("read_feature_details", ("cadastral-sheets", 42)),
])
def test_all_cadastral_reads_use_the_resolved_database(method, args):
    row = {"id": 42, "national_cadastral_reference": "A032_001000.427", "municipality_name": "ACQUAFONDATA", "geometry": '{"type":"Point","coordinates":[14,41.5]}'}
    source, stats, regional = regional_source(row=row, rows=[row])
    payload = asyncio.run(getattr(source, method)(*args))
    assert payload
    assert len(stats.conn.calls) == 1
    assert "spatial.cadastral_" in regional.conn.calls[-1][0]
    if isinstance(payload, dict) and "properties" in payload:
        assert payload["properties"]["municipality_name"] == "ACQUAFONDATA"


def test_reference_search_uses_identity_index_and_checks_original_reference():
    source, _, regional = regional_source(rows=[{"id": 42, "municipality_name": "ACQUAFONDATA"}])
    assert asyncio.run(source.search_parcels_by_reference(" a032_001000.427 "))[0]["id"] == 42
    sql, params = regional.conn.calls[-1]
    assert "WHERE id = " in sql and "md5(" in sql
    assert "AND national_cadastral_reference = $2" in sql
    assert "geo.geo_unit" not in sql
    assert params == ("A032_001000.427", "A032_001000.427", 10)


def test_regional_reference_lookup_checks_stale_id_hints():
    source, _, regional = regional_source()
    assert asyncio.run(source.read_feature_by_reference("cadastral-parcels", "A032_001000.427", feature_id=7)) is None
    sql, params = regional.conn.calls[-1]
    assert "AND t.id = $3" in sql
    assert params == ("A032_001000.427", "A032_001000.427", 7)


def test_broad_parcel_viewports_require_zoom_without_scanning_regional_rows():
    source, _, regional = regional_source()
    result = asyncio.run(source.read_geojson("cadastral-parcels", (6.5, 36.3, 18.6, 47.2), 100))
    assert result == {"type": "FeatureCollection", "features": [], "zoom_required": 14}
    assert all("to_regclass" in sql for sql, _ in regional.conn.calls)


def test_regional_reads_survive_an_independent_stats_database_outage():
    source, stats, _ = regional_source()

    async def unavailable(*args):
        raise ConnectionError("stats unavailable")

    stats.conn.fetchval = unavailable
    assert asyncio.run(source.read_mvt("cadastral-parcels", 16, 35000, 23000)) == b"regional-tile"


def test_regional_health_checks_every_backing_spatial_index_and_estimates_extent():
    connection = Connection(rows=[
        {"relname": "lazio__particelle", "row_estimate": 500, "geometry_column_exists": True, "srid": 4326, "has_gist": True, "west": 11, "south": 41, "east": 14, "north": 43},
        {"relname": "veneto__particelle", "row_estimate": 700, "geometry_column_exists": True, "srid": 4326, "has_gist": False, "west": 10, "south": 44, "east": 13, "north": 47},
    ])
    result = asyncio.run(regional_health(connection, "cadastral-parcels", "spatial.cadastral_parcel"))
    assert not result["available"] and not result["gist_index_exists"]
    assert result["row_estimate"] == 1200
    assert result["estimated_bounds"] == [10, 41, 14, 47]
    assert result["coverage"] == "unknown"
    assert "ST_EstimatedExtent" in connection.calls[-1][0]
    connection.rows[1]["has_gist"] = True
    assert asyncio.run(regional_health(connection, "cadastral-parcels", "spatial.cadastral_parcel"))["available"]


def test_regional_database_dsn_preserves_connection_options(monkeypatch):
    monkeypatch.setenv("STATS_POSTGRES_ENABLE", "1")
    monkeypatch.setenv("STATS_POSTGRES_DSN", "postgresql+asyncpg://user:secret@localhost:5432/aecs4u-stats?sslmode=require")
    monkeypatch.delenv("CADASTRAL_POSTGRES_DSN", raising=False)
    assert _AsyncpgConnectionSource.cadastral_from_environment().dsn == "postgresql://user:secret@localhost:5432/cadastral?sslmode=require"
    monkeypatch.setenv("CADASTRAL_POSTGRES_DSN", "postgresql://other:secret@localhost/custom")
    assert _AsyncpgConnectionSource.cadastral_from_environment().dsn.endswith("/custom")
    monkeypatch.setenv("STATS_POSTGRES_ENABLE", "0")
    assert _AsyncpgConnectionSource.cadastral_from_environment() is None


def test_close_releases_both_database_pools():
    source, stats, regional = regional_source()
    asyncio.run(source.close())
    assert stats.closed and regional.closed


def test_view_preparation_rejects_untrusted_relation_names():
    for table in ("veneto__particelle; DROP TABLE users", "other.parcels", "spatial.cadastral_parcel"):
        with pytest.raises(ValueError):
            view_branch(table)
