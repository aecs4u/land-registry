"""Contracts for the canonical PostGIS map-layer integration."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from land_registry.map_layers import MAP_LAYERS, PostgresMapLayerSource, get_map_layer
from land_registry.map_layers import _AsyncpgConnectionSource, _asyncpg_sql
from land_registry.routers import enrichment as enrichment_module


API_SOURCE = Path(__file__).parents[1] / "land_registry" / "routers" / "api.py"
FRONTEND_SOURCE = Path(__file__).parents[1] / "land_registry" / "static" / "enrichment-layers.js"


def test_catalog_covers_every_canonical_spatial_relation():
    tables = {layer.table for layer in MAP_LAYERS}
    assert {
        "geo.geo_boundary",
        "spatial.cadastral_parcel",
        "spatial.cadastral_sheet",
        "spatial.cadastral_urban_section",
        "spatial.market_zone",
        "spatial.postal_zone",
        "spatial.hazard_area",
        "facts.poi",
        "facts.hazard_measurement",
        "facts.raster_coverage",
        "hazards_mps04.mps04_points",
        "census_sections.sections",
        "serving.municipality_profile",
        "serving.market_zone_snapshot",
        "demanio_marittimo.concessions",
    } == tables


def test_catalog_ids_are_safe_and_have_feature_ids():
    assert len({layer.id for layer in MAP_LAYERS}) == len(MAP_LAYERS)
    for layer in MAP_LAYERS:
        assert layer.id_column in layer.properties
        assert layer.table.replace(".", "").replace("_", "").isalnum()
    assert get_map_layer("cadastral-parcels").coverage == "partial"
    assert "expected 2,847" in get_map_layer("urban-sections").coverage_note
    assert get_map_layer("maritime-concessions").id_column == "row_id"
    assert get_map_layer("geo-boundaries").role == "admin-substitute"
    assert get_map_layer("municipality-profiles").role == "admin-substitute"
    assert get_map_layer("geo-boundaries").public()["role"] == "admin-substitute"


class _Connection:
    """Records the last asyncpg call; rows are dicts like asyncpg Records."""

    def __init__(self, rows=(), row=None, value=b"mvt"):
        self.rows = list(rows)
        self.row = row
        self.value = value
        self.sql = ""
        self.params = ()

    def _record(self, sql, params):
        self.sql = sql
        self.params = params

    async def fetch(self, sql, *params):
        self._record(sql, params)
        return self.rows

    async def fetchrow(self, sql, *params):
        self._record(sql, params)
        return self.row

    async def fetchval(self, sql, *params):
        self._record(sql, params)
        return self.value


class _ConnectionSource:
    def __init__(self, connection):
        self.connection_value = connection

    @asynccontextmanager
    async def connection(self):
        yield self.connection_value

    async def close(self):
        pass



def test_partial_cadastral_coverage_is_published_as_a_viewport_bound():
    parcels = get_map_layer("cadastral-parcels")
    assert parcels.coverage == "partial"
    assert parcels.coverage_note == "Veneto only"
    assert parcels.public()["coverage_bounds"] == [10.62, 44.79, 13.10, 46.68]


def test_boundary_tile_joins_unit_name_and_type():
    connection = _Connection()
    source = PostgresMapLayerSource(_ConnectionSource(connection))

    asyncio.run(source.read_mvt("geo-boundaries", 10, 550, 380))

    assert "geo.geo_unit AS u ON u.id = t.geo_unit_id" in connection.sql
    assert "u.canonical_name AS canonical_name" in connection.sql
    assert "u.unit_type AS unit_type" in connection.sql


def test_exact_reference_search_returns_feature_ids_and_uses_plain_index_columns():
    connection = _Connection(rows=[{"id": 7, "canonical_reference": "L781B016200.14", "national_cadastral_reference": None, "parcel": "14", "sheet": "162", "municipality_id": 11180, "municipality_name": "Verona"}])
    source = PostgresMapLayerSource(_ConnectionSource(connection))

    rows = asyncio.run(source.search_parcels_by_reference("l781b016200.14", 10))

    assert rows[0]["id"] == 7
    # Each reference column is matched in its own UNION branch so the lookup
    # stays on the reference indexes even with stale planner statistics.
    assert "WHERE canonical_reference = $1" in connection.sql
    assert "WHERE national_cadastral_reference = $2" in connection.sql
    assert "UNION" in connection.sql and " OR " not in connection.sql
    assert connection.params == ("L781B016200.14", "L781B016200.14", 10)


def test_geojson_page_uses_offset_and_stable_feature_order():
    connection = _Connection(rows=[])
    source = PostgresMapLayerSource(_ConnectionSource(connection))

    asyncio.run(source.read_geojson("census-sections", (12, 41, 13, 42), 100, offset=200))

    assert "ORDER BY t.sez21_id" in connection.sql
    assert "LIMIT $9 OFFSET $10" in connection.sql
    assert connection.params[-2:] == (100, 200)

def test_dbapi_placeholders_translate_to_asyncpg():
    assert _asyncpg_sql("a = %s AND b ILIKE '%%x%%' AND c = %s") == "a = $1 AND b ILIKE '%x%' AND c = $2"


def test_mvt_query_is_allowlisted_and_attribute_bearing():
    connection = _Connection()
    source = PostgresMapLayerSource(_ConnectionSource(connection))

    assert asyncio.run(source.read_mvt("market-zones", 12, 2100, 1500)) == b"mvt"
    assert "spatial.market_zone" in connection.sql
    assert "omi_zone_key" in connection.sql
    assert "ST_AsMVT" in connection.sql
    assert "%s" not in connection.sql and "$8::text" in connection.sql
    # Clip and simplify before transforming; no exact intersect on huge polygons.
    assert "ST_ClipByBox2D" in connection.sql and "ST_Intersects" not in connection.sql
    assert "market-zones" in connection.params


def test_geojson_query_transforms_projected_census_geometry():
    connection = _Connection(rows=[{
        "sez21_id": 123, "procom": "H501", "cod_reg": "001", "pop21": 100, "fam21": 40,
        "abi21": 30, "edi21": 10, "geometry": '{"type":"Point","coordinates":[12.5,41.9]}',
    }])
    source = PostgresMapLayerSource(_ConnectionSource(connection))

    result = asyncio.run(source.read_geojson("census-sections", (12, 41, 13, 42), 10))

    assert result["features"][0]["id"] == 123
    assert result["features"][0]["properties"]["pop21"] == 100
    assert "32632" in connection.sql
    assert "ST_AsGeoJSON" in connection.sql
    assert all(isinstance(value, float) for value in connection.params[:8])


def test_point_lookup_returns_geojson_feature_from_canonical_source():
    connection = _Connection(row={
        "id": 42,
        "canonical_reference": "C773_002800.29",
        "national_cadastral_reference": "C773_002800.29",
        "parcel": "29",
        "sheet": "28",
        "municipality_id": 7,
        "area_sqm": 123.5,
        "source_release": "2026-01",
        "geometry": '{"type":"Polygon","coordinates":[]}',
    })
    source = PostgresMapLayerSource(_ConnectionSource(connection))

    result = asyncio.run(source.read_feature_at_point("cadastral-parcels", 42.07, 11.85))

    assert result["type"] == "Feature"
    assert result["id"] == 42
    assert result["properties"]["national_cadastral_reference"] == "C773_002800.29"
    assert result["geometry"]["type"] == "Polygon"
    assert "ST_Covers" in connection.sql
    assert connection.params == (11.85, 42.07, 11.85, 42.07)


def test_reference_lookup_returns_geojson_feature_from_canonical_source():
    connection = _Connection(row={
        "id": 42,
        "canonical_reference": "C773_002800.29",
        "national_cadastral_reference": "C773_002800.29",
        "parcel": "29",
        "sheet": "28",
        "municipality_id": 7,
        "area_sqm": 123.5,
        "source_release": "2026-01",
        "geometry": '{"type":"Polygon","coordinates":[]}',
    })
    source = PostgresMapLayerSource(_ConnectionSource(connection))

    result = asyncio.run(source.read_feature_by_reference("cadastral-parcels", "C773_002800.29"))

    assert result["properties"]["canonical_reference"] == "C773_002800.29"
    assert "national_cadastral_reference = $1" in connection.sql
    assert connection.params == ("C773_002800.29", "C773_002800.29")


def test_reference_lookup_uses_primary_key_when_feature_id_is_known():
    connection = _Connection(row={
        "id": 42,
        "canonical_reference": "C773_002800.29",
        "geometry": '{"type":"Polygon","coordinates":[]}',
    })
    source = PostgresMapLayerSource(_ConnectionSource(connection))

    result = asyncio.run(source.read_feature_by_reference("cadastral-parcels", "C773_002800.29", feature_id=42))

    assert result["id"] == 42
    # The PK predicate keeps the lookup off the unindexed reference columns,
    # while the reference predicate rejects a stale or mismatched id.
    assert "t.id = $1" in connection.sql
    assert connection.params == (42, "C773_002800.29", "C773_002800.29")


@pytest.mark.asyncio
async def test_point_enrichment_falls_back_to_canonical_source():
    feature = {"type": "Feature", "properties": {"national_cadastral_reference": "C773_002800.29"}}
    source = MagicMock(available=True)
    source.read_feature_at_point = AsyncMock(return_value=feature)

    with patch.object(enrichment_module.stats_service, "cadastral_store_available", return_value=False), \
         patch.object(enrichment_module, "get_map_layer_source", return_value=source):
        result = await enrichment_module.get_parcel_at_point(42.07, 11.85)

    assert result == feature
    source.read_feature_at_point.assert_awaited_once_with("cadastral-parcels", 42.07, 11.85)


@pytest.mark.asyncio
async def test_reference_enrichment_falls_back_to_canonical_source():
    feature = {"type": "Feature", "properties": {"national_cadastral_reference": "C773_002800.29"}}
    source = MagicMock(available=True)
    source.search_parcels_by_reference = AsyncMock(return_value=[{"id": 7}])
    source.read_feature_by_reference = AsyncMock(return_value=feature)

    with patch.object(enrichment_module.stats_service, "cadastral_store_available", return_value=False), \
         patch.object(enrichment_module, "get_map_layer_source", return_value=source):
        result = await enrichment_module.get_parcel_by_reference("C773_002800.29")

    assert result == feature
    source.read_feature_by_reference.assert_awaited_once_with(
        "cadastral-parcels", "C773_002800.29", feature_id=7
    )



@pytest.mark.asyncio
async def test_reference_enrichment_rejects_ambiguous_duplicate_polygons():
    source = MagicMock(available=True)
    source.search_parcels_by_reference = AsyncMock(return_value=[{"id": 7}, {"id": 8}])
    with patch.object(enrichment_module.stats_service, "cadastral_store_available", return_value=False), \
         patch.object(enrichment_module, "get_map_layer_source", return_value=source):
        with pytest.raises(enrichment_module.HTTPException) as error:
            await enrichment_module.get_parcel_by_reference("L781B016200.STRADA300")
    assert error.value.status_code == 409

def test_adjacent_lookup_returns_neighboring_features_excluding_self():
    connection = _Connection(rows=[{
        "id": 43,
        "canonical_reference": "C773_002800.30",
        "national_cadastral_reference": "C773_002800.30",
        "parcel": "30",
        "sheet": "28",
        "municipality_id": 7,
        "area_sqm": 88.0,
        "source_release": "2026-01",
        "geometry": '{"type":"Polygon","coordinates":[]}',
    }])
    source = PostgresMapLayerSource(_ConnectionSource(connection))

    features = asyncio.run(source.read_adjacent_features("cadastral-parcels", "C773_002800.29"))

    assert len(features) == 1
    assert features[0]["properties"]["national_cadastral_reference"] == "C773_002800.30"
    assert "ST_Intersects" in connection.sql
    # Self-exclusion is by primary key: national_cadastral_reference is NULL
    # for whole regional loads, so excluding by reference kept the target.
    assert "t.id <> target.target_id" in connection.sql
    assert "IS DISTINCT FROM" not in connection.sql
    assert connection.params == ("C773_002800.29", "C773_002800.29", 25)


@pytest.mark.asyncio
async def test_adjacent_parcels_endpoint_returns_feature_collection():
    features = [{"type": "Feature", "properties": {"national_cadastral_reference": "C773_002800.30"}}]
    source = MagicMock(available=True)
    source.read_adjacent_features = AsyncMock(return_value=features)

    with patch.object(enrichment_module, "get_map_layer_source", return_value=source):
        result = await enrichment_module.get_adjacent_parcels("C773_002800.29", limit=25)

    assert result == {"type": "FeatureCollection", "features": features}
    source.read_adjacent_features.assert_awaited_once_with(
        "cadastral-parcels", "C773_002800.29", limit=25, feature_id=None
    )


def test_municipality_search_returns_compact_profiles_with_centroids():
    connection = _Connection(rows=[{
        "id": "1", "canonical_name": "Roma", "istat_code": "058091",
        "source_release": "2025", "latitude": 41.9, "longitude": 12.5,
    }])
    source = PostgresMapLayerSource(_ConnectionSource(connection))

    result = asyncio.run(source.search_municipalities("Roma", 20))

    assert result[0]["canonical_name"] == "Roma"
    assert result[0]["latitude"] == 41.9
    assert "ILIKE" in connection.sql
    assert "ST_Y(ST_Centroid" in connection.sql
    # Substring name match first ($1), ISTAT prefix ($2), then the exact and
    # prefix ranking terms; binding the bare query to $1 made "Rom" find nothing.
    assert "COALESCE(NULLIF(t.canonical_name, ''), u.canonical_name) ILIKE $1" in connection.sql
    assert "LEFT JOIN geo.geo_unit AS u ON u.id = t.geo_unit_id" in connection.sql
    assert connection.params == ("%Roma%", "Roma%", "Roma", "Roma%", 20)


def test_health_contract_checks_geometry_srid():
    connection = _Connection(row=("market-zones", True, 10, True, True, 4326))
    source = PostgresMapLayerSource(_ConnectionSource(connection))

    result = asyncio.run(source.health())

    assert result[0]["available"] is True
    assert "row_estimate" not in result[0]
    assert "table" not in result[0]
    assert "geometry_columns" in connection.sql
    assert "'%USING gist%'" in connection.sql
    assert connection.params[-1] == MAP_LAYERS[-1].source_srid


def test_unconfigured_source_reports_every_layer_unavailable():
    source = PostgresMapLayerSource(None)
    source.connection_source = None

    result = asyncio.run(source.health())

    assert [item["id"] for item in result] == [layer.id for layer in MAP_LAYERS]
    assert not any(item["available"] for item in result)


def test_unknown_layer_is_rejected():
    try:
        get_map_layer("not-a-table")
    except KeyError:
        pass
    else:
        raise AssertionError("unknown layer was accepted")


def test_environment_accepts_sqlalchemy_postgres_dsn(monkeypatch):
    monkeypatch.setenv("AECS4U_STATS_POSTGRES_ENABLE", "1")
    monkeypatch.setenv("AECS4U_STATS_POSTGRES_DSN", "postgresql+asyncpg://user:pass@localhost/db")
    source = _AsyncpgConnectionSource.from_environment()
    assert source is not None
    assert source.dsn.startswith("postgresql://")


def test_api_and_frontend_expose_catalog_health_and_tiles():
    api = API_SOURCE.read_text(encoding="utf-8")
    frontend = FRONTEND_SOURCE.read_text(encoding="utf-8")
    assert '@api_router.get("/map/layers")' in api
    assert '@api_router.get("/map/layers/health")' in api
    assert '@api_router.get("/map/layers/{layer_id}/features/{feature_id}")' in api
    assert '@api_router.get("/tiles/map-layers/{layer_id}/{z}/{x}/{y}.pbf")' in api
    assert "/api/v1/map/layers/health" in frontend
    assert "L.vectorGrid.protobuf(spec.tile_url" in frontend
    assert "setCanonicalMapLayerOpacity" in frontend
    assert "canonical-layer-status" in frontend
    assert "canonicalLayerOpacities" in frontend
    assert "_canonicalLoadFeatureDetails" in frontend
    assert "_canonicalHttpUrl" in frontend
    assert "target=\"_blank\"" in frontend
    assert "document_url" in frontend
    assert "detail_url" in frontend


def test_consumer_map_has_geojson_fallback():
    consumer = (Path(__file__).parents[1] / "land_registry" / "consumer.py").read_text(encoding="utf-8")
    embedded = (Path(__file__).parents[1] / "land_registry" / "static" / "consumer-map.js").read_text(encoding="utf-8")
    assert '@consumer_router.get("/map/layers/{layer_id}/features")' in consumer
    assert '@consumer_router.get("/map/layers/{layer_id}/features/{feature_id}")' in consumer
    assert "/features" in embedded
    assert "refreshCanonicalGeoJson" in embedded


@pytest.mark.asyncio
async def test_poi_endpoint_accepts_comma_separated_categories():
    with patch.object(enrichment_module.stats_service, "aget_pois_near", AsyncMock(return_value={})) as pois:
        await enrichment_module.get_pois(38.09, 13.39, radius_km=2, categories=["schools,supermarkets", "parks"])

    pois.assert_awaited_once_with(38.09, 13.39, radius_km=2, categories=["schools", "supermarkets", "parks"])


def test_adjacent_lookup_resolves_target_by_primary_key_when_known():
    connection = _Connection(rows=[])
    source = PostgresMapLayerSource(_ConnectionSource(connection))

    asyncio.run(source.read_adjacent_features("cadastral-parcels", "C773_002800.29", feature_id=42))

    assert "WHERE id = $1 AND" in connection.sql
    assert connection.params == (42, "C773_002800.29", "C773_002800.29", 25)


def test_only_boundaries_resolve_names_through_the_geo_unit_join():
    # Municipality profiles carry canonical_name on their own row; dropping it
    # made search results and profile tiles show bare ISTAT codes.
    profiles = PostgresMapLayerSource._source_columns(get_map_layer("municipality-profiles"))
    assert "t.canonical_name" in profiles
    assert "u.canonical_name" not in profiles

    boundaries = PostgresMapLayerSource._source_columns(get_map_layer("geo-boundaries"))
    assert "u.canonical_name AS canonical_name" in boundaries
    assert "u.unit_type AS unit_type" in boundaries
    assert "t.canonical_name" not in boundaries


def test_municipality_search_selects_a_resolved_name():
    connection = _Connection(rows=[])
    source = PostgresMapLayerSource(_ConnectionSource(connection))

    asyncio.run(source.search_municipalities("Roma", 5))

    assert "COALESCE(NULLIF(t.canonical_name, ''), u.canonical_name) AS canonical_name" in connection.sql


@pytest.mark.asyncio
async def test_map_search_runs_parcel_lookup_on_reserved_search_pool():
    # On the shared tile pool the lookup waited behind tile rendering and the
    # 2 s timeout returned 503 even though the indexed query takes milliseconds.
    from land_registry.routers import api as api_module

    tile_source = MagicMock(available=True)
    tile_source.search_parcels_by_reference = AsyncMock(return_value=[])
    search_source = MagicMock(available=True)
    search_source.search_parcels_by_reference = AsyncMock(return_value=[{
        "id": 5226219, "canonical_reference": "L781B016200.14", "municipality_name": "Verona", "sheet": "162",
    }])
    with patch.object(api_module, "get_map_layer_source", return_value=tile_source), \
         patch.object(api_module, "get_map_search_source", return_value=search_source):
        result = await api_module.search_map("L781B016200.14", 10)

    search_source.search_parcels_by_reference.assert_awaited_once_with("L781B016200.14", 10)
    tile_source.search_parcels_by_reference.assert_not_awaited()
    assert result["results"][0]["label"] == "L781B016200.14"


@pytest.mark.asyncio
async def test_search_pool_is_warmed_ahead_of_first_query():
    from land_registry import map_layers

    connection_source = MagicMock()
    connection_source._get_pool = AsyncMock()
    with patch.object(map_layers, "get_map_search_source", return_value=MagicMock(connection_source=connection_source)):
        await map_layers.warm_map_search_source()

    connection_source._get_pool.assert_awaited_once()
