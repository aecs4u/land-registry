"""PostgresCadastralBoundarySource: indexable bbox filter, bytes WKB parsing and identify without the geo schema."""

from contextlib import contextmanager

import pytest
from shapely.geometry import box

from land_registry.datashader_service import PostgresCadastralBoundarySource


class _Column:
    def __init__(self, name):
        self.name = name


class _FakeCursor:
    """Records executed SQL and replays canned rows, one result set per execute()."""

    def __init__(self, results, description=None):
        self.executed = []
        self._results = list(results)
        self._current = []
        self.description = description

    def execute(self, sql, params=None):
        self.executed.append(sql)
        self._current = list(self._results.pop(0)) if self._results else []

    def fetchall(self):
        return self._current

    def fetchone(self):
        return self._current[0] if self._current else None


@pytest.fixture
def source():
    return PostgresCadastralBoundarySource("postgresql://user@localhost/cadastral")


def _use_cursor(source, cursor):
    @contextmanager
    def connection():
        class Conn:
            @contextmanager
            def cursor(self_inner):
                yield cursor

        yield Conn()

    source._connection = connection


def test_read_geometries_filters_on_the_indexed_column_and_parses_memoryview(source):
    # psycopg2 returns bytea as memoryview; shapely only parses bytes.
    wkb_row = (memoryview(box(0, 0, 1, 1).wkb),)
    cursor = _FakeCursor([[wkb_row]])
    _use_cursor(source, cursor)

    frame = source.read_geometries("ple", (12.47, 41.88, 12.50, 41.91))

    sql = cursor.executed[0]
    assert "t.geom && bounds.geom" in sql
    assert "ST_Transform" not in sql  # would defeat the GiST index on the national views
    assert "spatial.cadastral_parcel" in sql
    assert len(frame) == 1
    assert frame.crs.to_epsg() == 4326


def test_read_geometries_binds_plain_floats_not_numpy_scalars(source):
    import numpy as np

    params = []
    cursor = _FakeCursor([[]])
    original = cursor.execute
    cursor.execute = lambda sql, p=None: (params.append(p), original(sql, p))[1]
    _use_cursor(source, cursor)

    source.read_geometries("map", (np.float64(12.4), np.float64(41.8), np.float64(12.5), np.float64(41.9)))

    assert all(type(value) is float for value in params[0])  # psycopg2 renders np.float64 as "np.float64(..)"


def test_identify_without_geo_schema_uses_view_columns(source):
    columns = ["id", "sheet_reference", "level_name", "municipality_name", "province", "region", "geom"]
    values = (1, "H501A048600", "Mappa Catastale", "ROMA", "RM", "LAZIO", None)
    cursor = _FakeCursor([[(False,)], [values]], description=[_Column(c) for c in columns])
    _use_cursor(source, cursor)

    result = source.identify("map", 41.9, 12.48)

    assert "geo.geo_unit" not in cursor.executed[1]
    assert result["comune"] == "ROMA"
    assert result["provincia"] == "RM"
    assert result["regione"] == "LAZIO"
    assert result["reference"] == "H501A048600"


def test_identify_joins_geo_lookup_when_available_and_checks_only_once(source):
    columns = ["id", "level_name", "canonical_comune_name", "cadastral_code"]
    cursor = _FakeCursor(
        [[(True,)], [(1, "Mappa", "Roma", "H501")], [(1, "Mappa", "Roma", "H501")]],
        description=[_Column(c) for c in columns],
    )
    _use_cursor(source, cursor)

    first = source.identify("map", 41.9, 12.48)
    source.identify("map", 41.9, 12.48)

    assert "geo.geo_unit" in cursor.executed[1]
    assert first["comune"] == "Roma"
    assert sum("to_regclass" in sql for sql in cursor.executed) == 1


def test_from_environment_prefers_the_dedicated_cadastral_dsn(monkeypatch):
    monkeypatch.setenv("STATS_POSTGRES_ENABLE", "true")
    monkeypatch.setenv("STATS_POSTGRES_DSN", "postgresql://u@localhost/aecs4u-stats")
    monkeypatch.setenv("CADASTRAL_POSTGRES_DSN", "postgresql+asyncpg://u@localhost/cadastral")

    source = PostgresCadastralBoundarySource.from_environment()

    assert "/cadastral" in source.dsn
    assert "aecs4u-stats" not in source.dsn


def test_from_environment_stays_opt_in_and_falls_back_to_stats_dsn(monkeypatch):
    monkeypatch.delenv("STATS_POSTGRES_ENABLE", raising=False)
    monkeypatch.setenv("CADASTRAL_POSTGRES_DSN", "postgresql://u@localhost/cadastral")
    assert PostgresCadastralBoundarySource.from_environment() is None

    monkeypatch.setenv("STATS_POSTGRES_ENABLE", "true")
    monkeypatch.delenv("CADASTRAL_POSTGRES_DSN")
    monkeypatch.setenv("STATS_POSTGRES_DSN", "postgresql://u@localhost/aecs4u-stats")
    assert "aecs4u-stats" in PostgresCadastralBoundarySource.from_environment().dsn


def test_empty_tile_is_not_cached_while_the_postgres_source_is_failing(tmp_path, monkeypatch):
    import time
    from unittest.mock import MagicMock

    from land_registry.datashader_service import DatashaderTileService

    monkeypatch.setenv("DATASHADER_TILE_CACHE_DIR", str(tmp_path))
    source = MagicMock()
    source.read_geometries.side_effect = RuntimeError("db down")
    service = DatashaderTileService(cadastral_db=None, boundary_source=source)
    monkeypatch.setattr(service, "_candidate_fgb_files", lambda *a, **k: [])

    service.generate_boundary_tile(35039, 24352, 16, "ple")  # read fails -> blank tile, retry window opens
    assert service._postgres_boundary_retry_at > time.monotonic()
    assert not list(tmp_path.glob("*.png")), "a failed read must not be written to the disk cache"
    assert not service._tile_cache

    # Without a failing source an empty answer is still cached (sparse pans stay cheap).
    service._postgres_boundary_retry_at = 0.0
    service._postgres_boundary_source = None
    service.generate_boundary_tile(35039, 24352, 16, "ple")
    assert service._tile_cache
