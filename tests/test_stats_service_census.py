import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import duckdb
from pyproj import Transformer
from shapely.geometry import box

from land_registry import stats_service


def test_local_census_lookup_ranks_centroids_then_checks_exact_geometry(tmp_path, monkeypatch):
    store_path = tmp_path / "census.duckdb"
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:32632", always_xy=True)
    x, y = transformer.transform(10.995509625, 45.440299615)
    connection = duckdb.connect(str(store_path))
    connection.execute(
        """
        CREATE TABLE sections (
            sez21_id BIGINT,
            procom INTEGER,
            centroid_lat DOUBLE,
            centroid_lng DOUBLE,
            geom BLOB
        )
        """
    )
    distractor_geometry = box(x + 1000, y + 1000, x + 1010, y + 1010).wkb
    connection.executemany(
        "INSERT INTO sections VALUES (?, 23091, ?, ?, ?)",
        [
            (section_id, y + 10, x + section_id, distractor_geometry)
            for section_id in range(1, 41)
        ],
    )
    containing_geometry = box(x - 5, y - 5, x + 5, y + 5).wkb
    connection.execute(
        "INSERT INTO sections VALUES (99, 23091, ?, ?, ?)",
        [y, x + 100, containing_geometry],
    )
    connection.close()
    monkeypatch.setattr(stats_service, "_census_store_path", lambda: store_path)

    result = stats_service._census_section_for_parcel(
        {"lat": 45.440299615, "lng": 10.995509625}, {"procom": 23091}
    )

    assert result is not None
    assert result["properties"]["sez21_id"] == 99


def test_async_census_lookup_uses_local_exact_match_before_postgres(tmp_path, monkeypatch):
    store_path = tmp_path / "census.duckdb"
    store_path.touch()
    local_feature = {"type": "Feature", "properties": {"sez21_id": 99}}
    postgres_source = SimpleNamespace(
        _retry_at=0,
        census_section_at_point=AsyncMock(return_value=None),
    )
    monkeypatch.setattr(stats_service, "_census_store_path", lambda: store_path)
    monkeypatch.setattr(
        stats_service,
        "get_municipality_by_cadastral_code",
        lambda _code, *, use_postgres: {"procom": 23091} if not use_postgres else None,
    )
    monkeypatch.setattr(
        stats_service,
        "_census_section_for_parcel",
        lambda _point, _municipality: dict(local_feature),
    )
    source_lookup = AsyncMock(return_value=postgres_source)
    monkeypatch.setattr(stats_service, "_get_async_postgres_source", source_lookup)

    result = asyncio.run(
        stats_service.aget_census_section_at_point(45.440299615, 10.995509625, "L781")
    )

    assert result == {
        "type": "Feature",
        "properties": {"sez21_id": 99},
        "source": "ISTAT Permanent Census 2021 via local DuckDB",
    }
    source_lookup.assert_not_awaited()
    postgres_source.census_section_at_point.assert_not_awaited()
