"""Allow-listed PostGIS map-layer catalog and read-only feature source.

The map must not build SQL from table names supplied by a browser.  This
module keeps the database contract in one place and exposes only map-ready
PostGIS spatial relations that are safe to render.  Large layers use MVT; small
viewport requests use GeoJSON for identify/popups and debugging.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from typing import Any, AsyncIterator, Optional

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
    role: str = ""

    def public(self) -> dict[str, Any]:
        value = asdict(self)
        value["properties"] = list(self.properties)
        value["source"] = "aecs4u-stats PostgreSQL/PostGIS"
        value["tile_url"] = f"/api/v1/tiles/map-layers/{self.id}/{{z}}/{{x}}/{{y}}.pbf"
        value["geojson_url"] = f"/api/v1/map/layers/{self.id}/features"
        value["detail_url"] = f"/api/v1/map/layers/{self.id}/features/{{feature_id}}"
        return value


# Keep the property lists deliberately small: census and raw-tag columns can
# be very wide.  Raw landing relations are allowed when they have explicitly
# prepared native geometry and map-safe indexes.
MAP_LAYERS: tuple[MapLayerSpec, ...] = (
    MapLayerSpec("geo-boundaries", "Administrative boundaries", "geo.geo_boundary", "geom", properties=("id", "geo_unit_id", "generalization", "source_release"), role="admin-substitute"),
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
    MapLayerSpec("municipality-profiles", "Municipality profiles", "serving.municipality_profile", "geom", properties=("id", "geo_unit_id", "canonical_name", "istat_code", "observation_count", "tax_fact_count", "market_zone_count", "pv_observation_count"), role="admin-substitute"),
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


def _json_row(row: Any) -> dict[str, Any]:
    return {name: _json_value(value) for name, value in row.items()}


_PLACEHOLDER = re.compile(r"%(s|%)")


def _asyncpg_sql(sql: str) -> str:
    """Translate DB-API ``%s``/``%%`` markup into asyncpg ``$n`` placeholders."""
    index = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal index
        if match.group(1) == "%":
            return "%"
        index += 1
        return f"${index}"

    return _PLACEHOLDER.sub(replace, sql)


class _AsyncpgConnectionSource:
    """Bounded asyncpg pool for map reads.

    This used to be a psycopg2 pool.  ``psycopg2-binary`` bundles its own
    libpq and OpenSSL; next to the system OpenSSL and the copies bundled by
    the GDAL/rasterio wheels, its TLS connection setup segfaulted the API
    worker (SIGSEGV, not catchable) on ordinary map page loads.  asyncpg uses
    the interpreter's ``ssl`` module, its timeouts are enforced by the event
    loop, and ``acquire`` waits for a free connection instead of raising
    ``PoolError`` when a burst of tile requests exceeds the pool size.
    """

    RETRY_AFTER_SECONDS = 10.0

    def __init__(self, dsn: str, max_connections: int = 8, connect_timeout: float = 3.0):
        self.dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
        self.max_connections = max_connections
        self.connect_timeout = connect_timeout
        self._pool = None
        self._pool_loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock: Optional[asyncio.Lock] = None
        self._retry_at = 0.0

    @classmethod
    def from_environment(cls) -> Optional["_AsyncpgConnectionSource"]:
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

    async def _get_pool(self):
        loop = asyncio.get_running_loop()
        if self._pool_loop is not loop:
            # A pool belongs to the loop that created it.  The server has one
            # loop; test clients and the preflight CLI each bring their own.
            self._pool, self._pool_loop, self._lock = None, loop, asyncio.Lock()
        if self._pool is not None:
            return self._pool
        async with self._lock:
            if self._pool is None:
                # Fail fast while the database is down instead of making every
                # tile in a burst wait for its own connect timeout.
                if time.monotonic() < self._retry_at:
                    raise ConnectionError("canonical map database was unreachable moments ago")
                import asyncpg

                try:
                    self._pool = await asyncpg.create_pool(
                        self.dsn,
                        min_size=1,
                        max_size=self.max_connections,
                        timeout=self.connect_timeout,
                        command_timeout=10,
                        statement_cache_size=0,
                        server_settings={"jit": "off", "statement_timeout": "8000"},
                    )
                except Exception:
                    self._retry_at = time.monotonic() + self.RETRY_AFTER_SECONDS
                    raise
        return self._pool

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[Any]:
        pool = await self._get_pool()
        async with pool.acquire(timeout=5) as connection:
            yield connection

    async def close(self) -> None:
        pool, self._pool = self._pool, None
        if pool is None:
            return
        try:
            same_loop = asyncio.get_running_loop() is self._pool_loop
        except RuntimeError:
            same_loop = False
        if same_loop:
            await pool.close()
        else:
            pool.terminate()


class PostgresMapLayerSource:
    """Read-only canonical map source with its own bounded connection pool."""

    def __init__(self, connection_source: Optional[_AsyncpgConnectionSource] = None):
        if connection_source is None:
            connection_source = _AsyncpgConnectionSource.from_environment()
        self.connection_source = connection_source

    @property
    def available(self) -> bool:
        return self.connection_source is not None

    async def close(self) -> None:
        if self.connection_source is not None:
            await self.connection_source.close()

    async def health(self) -> list[dict[str, Any]]:
        """Return cheap relation/index readiness checks for every catalog layer."""
        if not self.connection_source:
            return [{"id": layer.id, "available": False} for layer in MAP_LAYERS]
        checks = []
        sql = _asyncpg_sql("""
            WITH relation AS (
                SELECT %s::text AS layer_id,
                       to_regclass(%s::text)::oid AS relation_oid,
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
        """)
        async with self.connection_source.connection() as connection:
            for layer in MAP_LAYERS:
                schema, table = layer.table.split(".", 1)
                row = await connection.fetchrow(
                    sql, layer.id, layer.table, schema, table, layer.geometry_column, layer.source_srid
                )
                values = tuple(row) if row else (layer.id, False, 0, False, False, 0)
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

    async def read_mvt(self, layer_id: str, z: int, x: int, y: int) -> bytes:
        layer = get_map_layer(layer_id)
        if not self.connection_source:
            return b""
        columns = self._source_columns(layer)
        # Clip to the tile (plus the MVT buffer) and simplify to ~1/4 pixel in
        # the source SRID before transforming.  One hazard polygon has ~250k
        # vertices; transforming and intersecting whole geometries pushed
        # cold-cache tiles past the statement timeout.  ST_AsMVTGeom still
        # does the exact clip in tile space.
        tile_width = (360.0 if layer.source_srid == 4326 else 40075016.686) / (1 << z)
        geometry = f"t.{layer.geometry_column}"
        if layer.kind != "point":
            padding = tile_width * 64 / 4096
            tolerance = tile_width / 1024
            geometry = f"ST_Simplify(ST_ClipByBox2D({geometry}, ST_Expand(bounds.source, {padding!r})), {tolerance!r}, true)"
        sql = f"""
            WITH bounds AS (
                SELECT ST_TileEnvelope(%s, %s, %s) AS tile,
                       ST_Transform(ST_Transform(ST_TileEnvelope(%s, %s, %s), 4326), {layer.source_srid}) AS source
            ), mvtgeom AS (
                SELECT {columns},
                       ST_AsMVTGeom(ST_Transform({geometry}, 3857), bounds.tile, 4096, 64, true) AS geom
                FROM {layer.table} AS t CROSS JOIN bounds
                WHERE t.{layer.geometry_column} && bounds.source
                LIMIT %s
            )
            SELECT COALESCE(ST_AsMVT(mvtgeom, %s::text, 4096, 'geom'), ''::bytea)
            FROM mvtgeom
            WHERE geom IS NOT NULL
        """
        async with self.connection_source.connection() as connection:
            tile = await connection.fetchval(
                _asyncpg_sql(sql), z, x, y, z, x, y, layer.max_features, layer.id
            )
        return tile or b""

    async def read_geojson(self, layer_id: str, bbox: tuple[float, float, float, float], limit: int) -> dict[str, Any]:
        layer = get_map_layer(layer_id)
        if not self.connection_source:
            return {"type": "FeatureCollection", "features": []}
        west, south, east, north = (float(value) for value in bbox)
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
        async with self.connection_source.connection() as connection:
            rows = await connection.fetch(
                _asyncpg_sql(sql),
                west, south, east, north, west, south, east, north, min(limit, layer.max_features),
            )
        features = []
        for row in rows:
            values = dict(row)
            geometry = values.pop("geometry")
            features.append({
                "type": "Feature",
                "id": values.get(layer.id_column),
                "properties": {key: _json_value(values.get(key)) for key in layer.properties},
                "geometry": json.loads(geometry) if geometry else None,
            })
        return {"type": "FeatureCollection", "features": features}

    async def read_feature_details(self, layer_id: str, feature_id: int) -> Optional[dict[str, Any]]:
        """Return one allow-listed feature and related maritime records."""

        layer = get_map_layer(layer_id)
        if not self.connection_source:
            return None
        columns = self._source_columns(layer)
        async with self.connection_source.connection() as connection:
            row = await connection.fetchrow(
                _asyncpg_sql(f"select {columns} from {layer.table} t where t.{layer.id_column} = %s limit 1"),
                feature_id,
            )
            if not row:
                return None
            result: dict[str, Any] = {
                "layer": layer.id,
                "id": feature_id,
                "properties": _json_row(row),
                "related": {},
            }
            if layer_id != "maritime-concessions":
                return result

            async def related_rows(sql: str, *params: Any) -> list[dict[str, Any]]:
                return [_json_row(item) for item in await connection.fetch(_asyncpg_sql(sql), *params)]

            snapshot_id = row["snapshot_id"]
            idconc = row["idconc"]
            result["related"]["document_gaps"] = await related_rows(
                "select gap_id, source_row_id, idconc, institution_key, document_type, "
                "document_description, status, evidence, checked_at "
                "from demanio_marittimo.document_gaps "
                "where source_row_id = %s order by gap_id",
                feature_id,
            )
            matches = await related_rows(
                "select match_id, document_url, idconc, match_type, confidence, evidence, "
                "extraction_status, extracted_text_chars, page_count, checked_at "
                "from demanio_marittimo.online_document_matches "
                "where snapshot_id = %s and idconc = %s order by match_id",
                snapshot_id,
                idconc,
            )
            result["related"]["online_document_matches"] = matches
            urls = [item["document_url"] for item in matches if item.get("document_url")]
            result["related"]["online_documents"] = await related_rows(
                "select source_page_url, document_url, title, document_kind, status, "
                "content_type, local_path, size_bytes, sha256, discovered_at, "
                "downloaded_at, error from demanio_marittimo.online_documents "
                "where snapshot_id = %s and document_url = any(%s::text[]) "
                "order by document_url",
                snapshot_id,
                urls,
            ) if urls else []
            result["related"]["snapshot"] = await related_rows(
                "select snapshot_id, package_id, reference_date, status, manifest_path, loaded_at_utc "
                "from demanio_marittimo.snapshots where snapshot_id = %s",
                snapshot_id,
            )
            result["related"]["resources"] = await related_rows(
                "select resource_id, kind, resource_title, requested_url, final_url, "
                "size_bytes, sha256, qa_json from demanio_marittimo.resources "
                "where snapshot_id = %s order by resource_id",
                snapshot_id,
            )
            return result

    async def search_municipalities(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Search the allow-listed municipality profile relation.

        This is intentionally separate from ``read_geojson``: place search
        must remain useful before the map reaches parcel zoom, and it should
        return compact centroids rather than geometry for whole areas.
        """
        layer = get_map_layer("municipality-profiles")
        if not self.connection_source:
            return []
        normalized = str(query or "").strip()
        if len(normalized) < 2:
            return []
        columns = self._source_columns(layer)
        columns = f"{columns}, ST_Y(ST_Centroid(t.{layer.geometry_column})) AS latitude, " \
                  f"ST_X(ST_Centroid(t.{layer.geometry_column})) AS longitude"
        sql = f"""
            SELECT {columns}
            FROM {layer.table} AS t
            WHERE t.canonical_name ILIKE %s
               OR t.istat_code ILIKE %s
            ORDER BY t.canonical_name ASC
            LIMIT %s
        """
        pattern = f"%{normalized}%"
        async with self.connection_source.connection() as connection:
            rows = await connection.fetch(_asyncpg_sql(sql), pattern, pattern, min(max(int(limit), 1), 50))
        return [_json_row(row) for row in rows]


_source: Optional[PostgresMapLayerSource] = None
_source_lock = threading.Lock()


def get_map_layer_source() -> PostgresMapLayerSource:
    global _source
    if _source is None:
        with _source_lock:
            if _source is None:
                _source = PostgresMapLayerSource()
    return _source


async def close_map_layer_source() -> None:
    global _source
    with _source_lock:
        source, _source = _source, None
    if source is not None:
        await source.close()
