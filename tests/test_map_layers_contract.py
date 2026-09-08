"""Contracts for the canonical PostGIS map-layer integration."""

from pathlib import Path

from land_registry.map_layers import MAP_LAYERS, PostgresMapLayerSource, get_map_layer
from land_registry.map_layers import _PostgresConnectionSource


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


class _Cursor:
    description = []

    def __init__(self, rows=(), result=(b"mvt",)):
        self.rows = list(rows)
        self.result = result
        self.sql = ""
        self.params = ()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=()):
        self.sql = sql
        self.params = params

    def fetchone(self):
        return self.result

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self, cursor):
        self.cursor_value = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self.cursor_value


class _ConnectionSource:
    def __init__(self, cursor):
        self.cursor_value = cursor

    def _connection(self):
        return _Connection(self.cursor_value)

    def close(self):
        pass


def test_mvt_query_is_allowlisted_and_attribute_bearing():
    cursor = _Cursor()
    source = PostgresMapLayerSource(_ConnectionSource(cursor))

    assert source.read_mvt("market-zones", 12, 2100, 1500) == b"mvt"
    assert "spatial.market_zone" in cursor.sql
    assert "omi_zone_key" in cursor.sql
    assert "ST_AsMVT" in cursor.sql
    assert "market-zones" in cursor.params


def test_geojson_query_transforms_projected_census_geometry():
    cursor = _Cursor(
        rows=[(123, "H501", "001", 100, 40, 30, 10, '{"type":"Point","coordinates":[12.5,41.9]}')]
    )
    cursor.description = [type("Column", (), {"name": name})() for name in (
        "sez21_id", "procom", "cod_reg", "pop21", "fam21", "abi21", "edi21", "geometry"
    )]
    source = PostgresMapLayerSource(_ConnectionSource(cursor))

    result = source.read_geojson("census-sections", (12, 41, 13, 42), 10)

    assert result["features"][0]["id"] == 123
    assert result["features"][0]["properties"]["pop21"] == 100
    assert "32632" in cursor.sql
    assert "ST_AsGeoJSON" in cursor.sql


def test_health_contract_checks_geometry_srid():
    cursor = _Cursor(result=("market-zones", True, 10, True, True, 4326))
    source = PostgresMapLayerSource(_ConnectionSource(cursor))

    result = source.health()

    assert result[0]["srid_matches"] is True
    assert result[0]["available"] is True
    assert "geometry_columns" in cursor.sql
    assert cursor.params[-1] == MAP_LAYERS[-1].source_srid


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
    source = _PostgresConnectionSource.from_environment()
    assert source is not None
    assert source.dsn.startswith("postgresql://")


def test_api_and_frontend_expose_catalog_health_and_tiles():
    api = API_SOURCE.read_text(encoding="utf-8")
    frontend = FRONTEND_SOURCE.read_text(encoding="utf-8")
    assert '@api_router.get("/map/layers")' in api
    assert '@api_router.get("/map/layers/health")' in api
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
    assert "/features" in embedded
    assert "refreshCanonicalGeoJson" in embedded
