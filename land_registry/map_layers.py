"""Allow-listed PostGIS map-layer catalog and read-only feature source.

The map must not build SQL from table names supplied by a browser.  This
module keeps the database contract in one place and exposes only map-ready
PostGIS spatial relations that are safe to render.  Large layers use MVT; small
viewport requests use GeoJSON for identify/popups and debugging.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Any, Optional

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MapLayerSpec:
    id: str
    title: str
    table: str
    geometry_column: str
    id_column: str = "id"
    source_srid: int = 4326
    min_zoom: int = 5
    max_features: int = 2000
    geojson_max_area: float = 250.0
    coverage: str = "full"
    coverage_note: str = ""
    properties: tuple[str, ...] = ()
    kind: str = "polygon"

    def public(self) -> dict[str, Any]:
        value = asdict(self)
        value["properties"] = list(self.properties)
        value["source"] = "aecs4u-stats PostgreSQL/PostGIS"
        value["tile_url"] = f"/api/v1/tiles/map-layers/{self.id}/{{z}}/{{x}}/{{y}}.pbf"
        value["geojson_url"] = f"/api/v1/map/layers/{self.id}/features"
        return value


# Keep the property lists deliberately small: census and raw-tag columns can
# be very wide.  Raw landing relations are allowed when they have explicitly
# prepared native geometry and map-safe indexes.
MAP_LAYERS: tuple[MapLayerSpec, ...] = (
    MapLayerSpec("geo-boundaries", "Administrative boundaries", "geo.geo_boundary", "geom", properties=("id", "geo_unit_id", "generalization", "source_release")),
    MapLayerSpec("cadastral-sheets", "Cadastral sheets", "spatial.cadastral_sheet", "geom", min_zoom=10, coverage="partial", coverage_note="Canonical publication currently covers only loaded regions", properties=("id", "sheet_reference", "municipality_id", "level", "level_name", "area_sqm", "source_release")),
    MapLayerSpec("cadastral-parcels", "Cadastral parcels", "spatial.cadastral_parcel", "geom", min_zoom=14, max_features=5000, coverage="partial", coverage_note="Canonical publication currently covers only loaded regions", properties=("id", "canonical_reference", "national_cadastral_reference", "parcel", "sheet", "municipality_id", "area_sqm", "source_release")),
    MapLayerSpec("urban-sections", "Cadastral urban sections", "spatial.cadastral_urban_section", "geom", min_zoom=11, coverage="partial", coverage_note="Upstream source currently contains 1,523 of the expected 2,847 sections", properties=("id", "zoning_reference", "section", "municipality_id", "source_release")),
    MapLayerSpec("market-zones", "OMI market zones", "spatial.market_zone", "geom", min_zoom=10, properties=("id", "omi_zone_key", "municipality_id", "valid_from", "valid_to", "source_release")),
    MapLayerSpec("postal-zones", "Postal zones", "spatial.postal_zone", "geom", min_zoom=10, properties=("id", "cap", "municipality_id", "valid_from", "valid_to", "source_release")),
    MapLayerSpec("hazard-areas", "Hazard areas", "spatial.hazard_area", "geom", min_zoom=8, max_features=3000, properties=("id", "hazard_type", "class_code", "source_release")),
    MapLayerSpec("census-sections", "ISTAT census sections", "census_sections.sections", "geom", id_column="sez21_id", source_srid=32632, min_zoom=11, max_features=2000, properties=("sez21_id", "procom", "cod_reg", "pop21", "fam21", "abi21", "edi21")),
    MapLayerSpec("points-of-interest", "Points of interest", "facts.poi", "geom", min_zoom=12, max_features=3000, properties=("id", "osm_natural_key", "category_id", "name"), kind="point"),
    MapLayerSpec("hazard-measurements", "Hazard measurements", "facts.hazard_measurement", "geom", min_zoom=8, max_features=3000, geojson_max_area=4.0, properties=("id", "hazard_type", "metric", "value", "period", "source_release"), kind="point"),
    MapLayerSpec("raster-coverage", "Raster coverage footprints", "facts.raster_coverage", "footprint", min_zoom=5, properties=("id", "raster_asset_id", "resolution_m")),
    MapLayerSpec("mps04-points", "MPS04 seismic points", "hazards_mps04.mps04_points", "geom", id_column="point_id", min_zoom=7, max_features=3000, properties=("point_id", "grid_variant", "lon", "lat"), kind="point"),
    MapLayerSpec("municipality-profiles", "Municipality profiles", "serving.municipality_profile", "geom", properties=("id", "geo_unit_id", "canonical_name", "istat_code", "observation_count", "tax_fact_count", "market_zone_count", "pv_observation_count")),
    MapLayerSpec("market-zone-snapshots", "Market-zone snapshots", "serving.market_zone_snapshot", "geom", min_zoom=10, properties=("id", "market_zone_id", "omi_zone_key", "municipality_name", "quote_count", "latest_period")),
    MapLayerSpec("maritime-concessions", "Maritime-domain concessions", "demanio_marittimo.concessions", "geom", id_column="row_id", min_zoom=7, max_features=5000, geojson_max_area=4.0, coverage_note="MIT/SID snapshot; mixed point and polygon geometry", properties=("row_id", "idconc", "layer_kind", "geometry_type", "crs_original", "snapshot_id", "source_release")),
)

_BY_ID = {layer.id: layer for layer in MAP_LAYERS}


def get_map_layer(layer_id: str) -> MapLayerSpec:
    try:
        return _BY_ID[layer_id]
    except KeyError as exc:
        raise KeyError(f"Unknown map layer: {layer_id}") from exc


def map_layer_catalog() -> list[dict[str, Any]]:
    return [layer.public() for layer in MAP_LAYERS]


def _json_value(value: Any) -> Any:
    """Convert DB-native values into JSON-safe values without losing dates."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


class _PostgresConnectionSource:
    """Small optional-dependency-free connection pool for map reads."""

    def __init__(self, dsn: str, max_connections: int = 4):
        dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
        if "connect_timeout=" not in dsn:
            separator = "&" if "?" in dsn else "?"
            dsn = f"{dsn}{separator}connect_timeout=3"
        self.dsn = dsn
        self.max_connections = max_connections
        self._pool = None
        self._pool_lock = threading.Lock()

    @classmethod
    def from_environment(cls) -> Optional["_PostgresConnectionSource"]:
        if os.getenv("AECS4U_STATS_POSTGRES_ENABLE", "").strip().lower() not in ("1", "true", "yes", "on"):
            return None
        dsn = (
            os.getenv("AECS4U_STATS_POSTGRES_DSN")
            or os.getenv("AECS4U_STATS_DATABASE_URL")
            or os.getenv("AECS4U_STATS_SPATIAL_DATABASE_URL")
        )
        if not dsn or not dsn.startswith(("postgres://", "postgresql://", "postgresql+")):
            return None
        return cls(dsn)

    def _get_pool(self):
        if self._pool is None:
            with self._pool_lock:
                if self._pool is None:
                    import psycopg2.pool

                    self._pool = psycopg2.pool.ThreadedConnectionPool(1, self.max_connections, self.dsn)
        return self._pool

    @contextmanager
    def _connection(self):
        pool = self._get_pool()
        connection = pool.getconn()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET jit = off")
                cursor.execute("SET statement_timeout = 8000")
            yield connection
        except Exception:
            connection.rollback()
            raise
        finally:
            pool.putconn(connection, close=bool(getattr(connection, "closed", 0)))

    def close(self) -> None:
        with self._pool_lock:
            if self._pool is not None:
                self._pool.closeall()
                self._pool = None


class PostgresMapLayerSource:
    """Read-only canonical map source with its own bounded connection pool."""

    def __init__(self, connection_source: Optional[_PostgresConnectionSource] = None):
        if connection_source is None:
            connection_source = _PostgresConnectionSource.from_environment()
        self.connection_source = connection_source

    @property
    def available(self) -> bool:
        return self.connection_source is not None

    def close(self) -> None:
        if self.connection_source is not None:
            self.connection_source.close()

    def health(self) -> list[dict[str, Any]]:
        """Return cheap relation/index readiness checks for every catalog layer."""
        if not self.connection_source:
            return [{"id": layer.id, "available": False} for layer in MAP_LAYERS]
        checks = []
        sql = """
            WITH relation AS (
                SELECT %s::text AS layer_id,
                       to_regclass(%s)::oid AS relation_oid,
                       %s::text AS schema_name,
                       %s::text AS table_name,
                       %s::text AS geometry_column,
                       %s::integer AS expected_srid
            )
            SELECT r.layer_id,
                   r.relation_oid IS NOT NULL AS relation_exists,
                   coalesce(c.reltuples, 0)::bigint AS row_estimate,
                   EXISTS (
                       SELECT 1 FROM pg_attribute a
                       WHERE a.attrelid = r.relation_oid
                         AND a.attname = r.geometry_column
                         AND a.attnum > 0
                         AND NOT a.attisdropped
                   ) AS geometry_column_exists,
                   EXISTS (
                       SELECT 1 FROM pg_indexes i
                       WHERE i.schemaname = r.schema_name
                         AND i.tablename = r.table_name
                         AND i.indexdef ILIKE '%%USING gist%%'
                   ) AS gist_index_exists,
                   COALESCE((
                       SELECT gc.srid
                       FROM public.geometry_columns gc
                       WHERE gc.f_table_schema = r.schema_name
                         AND gc.f_table_name = r.table_name
                         AND gc.f_geometry_column = r.geometry_column
                       LIMIT 1
                   ), 0)::integer AS geometry_srid
            FROM relation r
            LEFT JOIN pg_class c ON c.oid = r.relation_oid
        """
        with self.connection_source._connection() as connection:
            with connection.cursor() as cursor:
                for layer in MAP_LAYERS:
                    schema, table = layer.table.split(".", 1)
                    cursor.execute(sql, (layer.id, layer.table, schema, table, layer.geometry_column, layer.source_srid))
                    row = cursor.fetchone()
                    values = row or (layer.id, False, 0, False, False, 0)
                    geometry_srid = int(values[5] or 0)
                    checks.append({
                        "id": values[0],
                        "table": layer.table,
                        "available": bool(values[1] and values[3] and values[4] and geometry_srid == layer.source_srid),
                        "relation_exists": bool(values[1]),
                        "row_estimate": max(0, int(values[2] or 0)),
                        "geometry_column": layer.geometry_column,
                        "geometry_column_exists": bool(values[3]),
                        "gist_index_exists": bool(values[4]),
                        "expected_srid": layer.source_srid,
                        "geometry_srid": geometry_srid,
                        "srid_matches": geometry_srid == layer.source_srid,
                        "coverage": layer.coverage if values[1] else "unavailable",
                        "coverage_note": layer.coverage_note,
                    })
        return checks

    @staticmethod
    def _source_columns(layer: MapLayerSpec) -> str:
        # Every identifier comes from MAP_LAYERS above, never from a request.
        return ", ".join(f"t.{column}" for column in layer.properties)

    def read_mvt(self, layer_id: str, z: int, x: int, y: int) -> bytes:
        layer = get_map_layer(layer_id)
        if not self.connection_source:
            return b""
        columns = self._source_columns(layer)
        if layer.source_srid == 4326:
            # ST_AsMVTGeom performs tile clipping itself.  Avoiding the
            # explicit ST_Intersection used for projected sources is a large
            # win for native-WGS84 layers with many polygon features.
            mvt_geometry = f"ST_Transform(t.{layer.geometry_column}, 3857)"
        else:
            mvt_geometry = (
                f"ST_Transform(ST_Intersection(ST_Transform(t.{layer.geometry_column}, 4326), "
                "bounds.web), 3857)"
            )
        sql = f"""
            WITH bounds AS (
                SELECT ST_TileEnvelope(%s, %s, %s) AS tile,
                       ST_Transform(ST_TileEnvelope(%s, %s, %s), 4326) AS web,
                       ST_Transform(ST_Transform(ST_TileEnvelope(%s, %s, %s), 4326), {layer.source_srid}) AS source
            ), mvtgeom AS (
                SELECT {columns},
                       ST_AsMVTGeom(
                           {mvt_geometry},
                           bounds.tile, 4096, 64, true
                       ) AS geom
                FROM {layer.table} AS t CROSS JOIN bounds
                WHERE t.{layer.geometry_column} && bounds.source
                  AND ST_Intersects(t.{layer.geometry_column}, bounds.source)
                LIMIT %s
            )
            SELECT COALESCE(ST_AsMVT(mvtgeom, %s, 4096, 'geom'), ''::bytea)
            FROM mvtgeom
            WHERE geom IS NOT NULL
        """
        with self.connection_source._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, (z, x, y, z, x, y, z, x, y, layer.max_features, layer.id))
                return (cursor.fetchone() or (b"",))[0] or b""

    def read_geojson(self, layer_id: str, bbox: tuple[float, float, float, float], limit: int) -> dict[str, Any]:
        layer = get_map_layer(layer_id)
        if not self.connection_source:
            return {"type": "FeatureCollection", "features": []}
        west, south, east, north = bbox
        if (east - west) * (north - south) > layer.geojson_max_area:
            return {"type": "FeatureCollection", "features": [], "zoom_required": layer.min_zoom}
        columns = self._source_columns(layer)
        sql = f"""
            WITH bounds AS (
                SELECT ST_MakeEnvelope(%s, %s, %s, %s, 4326) AS web,
                       ST_Transform(ST_MakeEnvelope(%s, %s, %s, %s, 4326), {layer.source_srid}) AS source
            )
            SELECT {columns},
                   ST_AsGeoJSON(ST_Intersection(ST_Transform(t.{layer.geometry_column}, 4326), bounds.web)) AS geometry
            FROM {layer.table} AS t CROSS JOIN bounds
            WHERE t.{layer.geometry_column} && bounds.source
              AND ST_Intersects(t.{layer.geometry_column}, bounds.source)
            LIMIT %s
        """
        features = []
        with self.connection_source._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, (west, south, east, north, west, south, east, north, min(limit, layer.max_features)))
                names = [column.name for column in cursor.description]
                for row in cursor.fetchall():
                    values = dict(zip(names, row))
                    geometry = values.pop("geometry")
                    feature_id = values.get(layer.id_column)
                    features.append({
                        "type": "Feature",
                        "id": feature_id,
                        "properties": {key: _json_value(values.get(key)) for key in layer.properties},
                        "geometry": json.loads(geometry) if geometry else None,
                    })
        return {"type": "FeatureCollection", "features": features}


_source: Optional[PostgresMapLayerSource] = None
_source_lock = threading.Lock()


def get_map_layer_source() -> PostgresMapLayerSource:
    global _source
    if _source is None:
        with _source_lock:
            if _source is None:
                _source = PostgresMapLayerSource()
    return _source


def close_map_layer_source() -> None:
    global _source
    with _source_lock:
        if _source is not None:
            _source.close()
            _source = None
