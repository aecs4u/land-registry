"""Contracts for the canonical PostGIS map-layer integration."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from land_registry.map_layers import MAP_LAYERS, PostgresMapLayerSource, get_map_layer
from land_registry.map_layers import _AsyncpgConnectionSource, _asyncpg_sql


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
    assert connection.params == ("%Roma%", "%Roma%", 20)


def test_health_contract_checks_geometry_srid():
    connection = _Connection(row=("market-zones", True, 10, True, True, 4326))
    source = PostgresMapLayerSource(_ConnectionSource(connection))

    result = asyncio.run(source.health())

    assert result[0]["srid_matches"] is True
    assert result[0]["available"] is True
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
    assert "detail_url" in frontend


def test_consumer_map_has_geojson_fallback():
    consumer = (Path(__file__).parents[1] / "land_registry" / "consumer.py").read_text(encoding="utf-8")
    embedded = (Path(__file__).parents[1] / "land_registry" / "static" / "consumer-map.js").read_text(encoding="utf-8")
    assert '@consumer_router.get("/map/layers/{layer_id}/features")' in consumer
    assert '@consumer_router.get("/map/layers/{layer_id}/features/{feature_id}")' in consumer
    assert "/features" in embedded
    assert "refreshCanonicalGeoJson" in embedded
