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
import math
import os
import re
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from typing import Any, AsyncIterator, Optional
from urllib.parse import urlsplit, urlunsplit

from land_registry.cadastral_map_views import CADASTRAL_LAYER_IDS, REGIONAL_PROPERTIES, identity_sql, regional_health

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
    coverage_bounds: Optional[tuple[float, float, float, float]] = None
    properties: tuple[str, ...] = ()
    kind: str = "polygon"
    role: str = ""
    # Presentation contract: the browser draws every layer from these fields so
    # a new layer is a single catalog edit (no colour/order tables in the JS).
    group: str = "territory"
    color: str = "#47758f"
    # Optional fill/outline colour when the same vector source contains both
    # polygons and points; point styling continues to use ``color``.
    polygon_color: Optional[str] = None
    # Stack position, bottom to top; layers sharing a value keep catalog order.
    z_order: int = 100
    fill_opacity: float = 0.11
    line_width: float = 1.4
    # Optional ((min_zoom, unit_type), ...) ladder: which ``unit_type`` of a
    # multi-level layer is drawn at each zoom (ascending by min_zoom).
    unit_levels: tuple[tuple[int, str], ...] = ()
    # Optional data-driven fill: ("property", ((value, colour), ...)) is drawn
    # as a linear colour ramp instead of the flat ``color``.
    color_ramp: Optional[tuple[str, tuple[tuple[float, str], ...]]] = None
    # Optional categorical colouring: ("property", ((substring, colour), ...)).
    # The first case-insensitive substring found in the property wins; anything
    # else keeps the flat ``color``, so unexpected source values stay visible.
    color_match: Optional[tuple[str, tuple[tuple[str, str], ...]]] = None
    # Health checks report whether the serving relation needs a GiST index.
    require_gist_index: bool = True
    # Change when tile geometry/selection changes so browsers fetch fresh tiles.
    tile_revision: str = ""

    def public(self) -> dict[str, Any]:
        value = asdict(self)
        value["properties"] = list(self.properties)
        value["unit_levels"] = [{"min_zoom": zoom, "unit_type": unit} for zoom, unit in self.unit_levels]
        value["color_ramp"] = (
            {"property": self.color_ramp[0], "stops": [{"value": at, "color": color} for at, color in self.color_ramp[1]]}
            if self.color_ramp
            else None
        )
        value["color_match"] = (
            {"property": self.color_match[0], "cases": [{"contains": text, "color": color} for text, color in self.color_match[1]]}
            if self.color_match
            else None
        )
        value.pop("require_gist_index", None)
        value.pop("tile_revision", None)
        value["source"] = "aecs4u-stats PostgreSQL/PostGIS"
        value["tile_url"] = f"/api/v1/tiles/map-layers/{self.id}/{{z}}/{{x}}/{{y}}.pbf"
        if self.tile_revision:
            value["tile_url"] += f"?v={self.tile_revision}"
        value["geojson_url"] = f"/api/v1/map/layers/{self.id}/features"
        value["detail_url"] = f"/api/v1/map/layers/{self.id}/features/{{feature_id}}"
        if self.coverage_bounds is not None:
            value["coverage_bounds"] = list(self.coverage_bounds)
        return value


# Keep the property lists deliberately small: census and raw-tag columns can
# be very wide. Map layers should use prepared spatial relations; raw landing
# relations require bounded, source-filtered map queries.
MAP_LAYERS: tuple[MapLayerSpec, ...] = (
    MapLayerSpec("geo-boundaries", "Administrative boundaries", "geo.geo_boundary", "geom", properties=("id", "geo_unit_id", "canonical_name", "unit_type", "generalization", "source_release"), role="admin-substitute", group="administrative", color="#526b84", z_order=10, fill_opacity=0.075, line_width=1.1, unit_levels=((0, "region"), (8, "province"), (10, "municipality"))),
    # Coverage changes as upstream publications are loaded. Do not hard-code a
    # region or a row count here; health() reports the current estimated extent
    # from PostGIS statistics when ANALYZE data is available.
    MapLayerSpec("cadastral-sheets", "Cadastral sheets", "spatial.cadastral_sheet", "geom", min_zoom=10, geojson_max_area=4.0, coverage="unknown", properties=("id", "sheet_reference", "municipality_id", "level", "level_name", "area_sqm", "source_release"), group="cadastral", color="#1976a8", z_order=70, tile_revision="regional-1"),
    MapLayerSpec("cadastral-parcels", "Cadastral parcels", "spatial.cadastral_parcel", "geom", min_zoom=14, max_features=5000, geojson_max_area=0.04, coverage="unknown", properties=("id", "canonical_reference", "national_cadastral_reference", "parcel", "sheet", "municipality_id", "area_sqm", "source_release"), group="cadastral", color="#d97925", z_order=90, fill_opacity=0.04, line_width=0.8, tile_revision="regional-1"),
    MapLayerSpec("urban-sections", "Cadastral urban sections", "spatial.cadastral_urban_section", "geom", min_zoom=11, coverage="unknown", properties=("id", "zoning_reference", "section", "municipality_id", "source_release"), group="cadastral", color="#2f9aa8", z_order=80),
    MapLayerSpec("market-zones", "OMI market zones", "spatial.market_zone", "geom", min_zoom=10, properties=("id", "omi_zone_key", "municipality_id", "valid_from", "valid_to", "source_release"), group="market", color="#7b61a8", z_order=30),
    MapLayerSpec("postal-zones", "Postal zones", "cap_subcomunali.cap_subcomunali", "geom", id_column="fid", min_zoom=10, properties=("fid", "cap", "comune_cap", "comune", "provincia", "regione", "fonte"), group="administrative", color="#8a7a3d", z_order=40, require_gist_index=False, tile_revision="1"),
    MapLayerSpec("hazard-areas", "Hazard areas", "spatial.hazard_area", "geom", min_zoom=8, max_features=3000, properties=("id", "hazard_type", "class_code", "source_release"), group="risk", color="#c44444", z_order=50, fill_opacity=0.2, color_match=("hazard_type", (("flood", "#2b7bb9"), ("alluvi", "#2b7bb9"), ("landslide", "#a0522d"), ("frana", "#a0522d"), ("seism", "#7a4cb5")))),
    MapLayerSpec("census-sections", "ISTAT census sections", "census_sections.sections", "geom", id_column="sez21_id", source_srid=32632, min_zoom=11, max_features=2000, properties=("sez21_id", "procom", "cod_reg", "pop21", "fam21", "abi21", "edi21"), group="demographics", color="#3f8f73", z_order=60, fill_opacity=0.3, color_ramp=("pop21", ((0, "#e8f4ef"), (50, "#bfe3d2"), (200, "#7fc1a3"), (500, "#3f8f73"), (1500, "#1f5d49")))),
    MapLayerSpec("points-of-interest", "Points of interest", "facts.poi", "geom", min_zoom=12, max_features=3000, properties=("id", "osm_natural_key", "category_id", "name"), kind="point", group="territory", color="#b04a9b", z_order=100),
    MapLayerSpec("hazard-measurements", "Hazard measurements", "facts.hazard_measurement", "geom", min_zoom=8, max_features=3000, geojson_max_area=4.0, properties=("id", "hazard_type", "metric", "value", "period", "source_release"), kind="point", group="risk", color="#b5332e", z_order=110),
    MapLayerSpec("raster-coverage", "Raster coverage footprints", "facts.raster_coverage", "footprint", min_zoom=5, properties=("id", "raster_asset_id", "resolution_m"), group="territory", z_order=140),
    MapLayerSpec("mps04-points", "MPS04 seismic points", "hazards_mps04.mps04_points", "geom", id_column="point_id", min_zoom=7, max_features=3000, properties=("point_id", "grid_variant", "lon", "lat"), kind="point", group="risk", color="#5b6fb5", z_order=120),
    MapLayerSpec("municipality-profiles", "Municipality profiles", "serving.municipality_profile", "geom", properties=("id", "geo_unit_id", "canonical_name", "istat_code", "observation_count", "tax_fact_count", "market_zone_count", "pv_observation_count"), role="admin-substitute", group="administrative", color="#4f6f86", z_order=20, fill_opacity=0.075, line_width=1.1),
    MapLayerSpec("market-zone-snapshots", "Market-zone snapshots", "serving.market_zone_snapshot", "geom", min_zoom=10, properties=("id", "market_zone_id", "omi_zone_key", "municipality_name", "quote_count", "latest_period"), group="market", color="#9a6bb3", z_order=35, fill_opacity=0.3, color_ramp=("quote_count", ((0, "#efe6f5"), (10, "#d3bde6"), (50, "#9a6bb3"), (200, "#5e3a85")))),
    MapLayerSpec(
        "maritime-concessions", "Maritime-domain concessions",
        "agenziademanio.concessions", "geom",
        id_column="row_id", min_zoom=6, max_features=5000, geojson_max_area=4.0,
        coverage_note="MIT/SID snapshot; mixed point and polygon geometry",
        properties=("row_id", "idconc", "layer_kind", "geometry_type", "crs_original", "snapshot_id", "source_release"),
        kind="mixed", group="territory", color="#1f8ea3", z_order=130,
        polygon_color="#e45724", fill_opacity=0.42, line_width=3.0,
        require_gist_index=False, tile_revision="2",
    ),
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
        if os.getenv("STATS_POSTGRES_ENABLE", "").strip().lower() not in ("1", "true", "yes", "on"):
            return None
        dsn = (
            os.getenv("STATS_POSTGRES_DSN")
            or os.getenv("AECS4U_STATS_DATABASE_URL")
            or os.getenv("AECS4U_STATS_SPATIAL_DATABASE_URL")
        )
        if not dsn or not dsn.startswith(("postgres://", "postgresql://", "postgresql+")):
            return None
        return cls(dsn)

    @classmethod
    def census_from_environment(cls) -> Optional["_AsyncpgConnectionSource"]:
        """Use the census database directly so its local GiST index serves tiles."""
        if os.getenv("STATS_POSTGRES_ENABLE", "").strip().lower() not in ("1", "true", "yes", "on"):
            return None
        direct_dsn = os.getenv("CENSUS_POSTGRES_DSN") or os.getenv("CENSUS_SECTIONS_POSTGRES_DSN")
        if direct_dsn:
            return cls(direct_dsn)
        stats_dsn = (
            os.getenv("STATS_POSTGRES_DSN")
            or os.getenv("AECS4U_STATS_DATABASE_URL")
            or os.getenv("AECS4U_STATS_SPATIAL_DATABASE_URL")
        )
        if not stats_dsn or not stats_dsn.startswith(("postgres://", "postgresql://", "postgresql+")):
            return None
        parsed = urlsplit(stats_dsn.replace("postgresql+asyncpg://", "postgresql://", 1))
        if not parsed.scheme or not parsed.netloc:
            return None
        census_dsn = urlunsplit(
            (parsed.scheme, parsed.netloc, "/census_sections", parsed.query, parsed.fragment)
        )
        return cls(census_dsn)


    @classmethod
    def cadastral_from_environment(cls) -> Optional["_AsyncpgConnectionSource"]:
        """Use prepared views in the dedicated database when stats has none."""
        stats = cls.from_environment()
        if stats is None:
            return None
        direct_dsn = os.getenv("CADASTRAL_POSTGRES_DSN")
        if direct_dsn:
            return cls(direct_dsn)
        parsed = urlsplit(stats.dsn)
        return cls(urlunsplit(parsed._replace(path="/cadastral")))

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

    def __init__(
        self,
        connection_source: Optional[_AsyncpgConnectionSource] = None,
        census_connection_source: Optional[_AsyncpgConnectionSource] = None,
        cadastral_connection_source: Optional[_AsyncpgConnectionSource] = None,
    ):
        use_environment = connection_source is None
        if connection_source is None:
            connection_source = _AsyncpgConnectionSource.from_environment()
        self.connection_source = connection_source
        self.census_connection_source = census_connection_source
        if self.census_connection_source is None and use_environment and self.connection_source is not None:
            self.census_connection_source = _AsyncpgConnectionSource.census_from_environment()
        self.cadastral_connection_source = cadastral_connection_source
        if self.cadastral_connection_source is None and use_environment and self.connection_source is not None:
            self.cadastral_connection_source = _AsyncpgConnectionSource.cadastral_from_environment()
        self._cadastral_routes: dict[str, tuple[Any, float]] = {}

    def _source_for(self, layer: MapLayerSpec) -> Any:
        if layer.id == "census-sections" and self.census_connection_source is not None:
            return self.census_connection_source
        return self.connection_source

    async def _layer_source_for(self, layer: MapLayerSpec) -> Any:
        if layer.id not in CADASTRAL_LAYER_IDS or self.cadastral_connection_source is None:
            return self._source_for(layer)
        cached = self._cadastral_routes.get(layer.id)
        if cached and cached[1] > time.monotonic():
            return cached[0]
        # Preserve canonical installations; use regional views only when the
        # canonical relation has moved out of the stats database.
        selected = self.connection_source
        if selected is not None:
            try:
                async with selected.connection() as connection:
                    canonical_exists = await connection.fetchval("SELECT to_regclass($1)::text", layer.table)
            except Exception as exc:
                log.debug("Canonical cadastral source unavailable: %s", exc)
                canonical_exists = False
            if canonical_exists:
                self._cadastral_routes[layer.id] = (selected, time.monotonic() + 300)
                return selected
        try:
            async with self.cadastral_connection_source.connection() as connection:
                if await connection.fetchval("SELECT to_regclass($1)::text", layer.table):
                    selected = self.cadastral_connection_source
        except Exception as exc:
            log.debug("Dedicated cadastral source unavailable: %s", exc)
        ttl = 300 if selected is self.cadastral_connection_source else 10
        self._cadastral_routes[layer.id] = (selected, time.monotonic() + ttl)
        return selected

    def _relation_for(self, layer: MapLayerSpec) -> str:
        if layer.id == "census-sections" and self.census_connection_source is not None:
            return "public.sections"
        return layer.table

    @property
    def available(self) -> bool:
        return self.connection_source is not None

    async def close(self) -> None:
        sources = {
            id(source): source
            for source in (self.connection_source, self.census_connection_source, self.cadastral_connection_source)
            if source is not None
        }
        for source in sources.values():
            await source.close()

    async def health(self) -> list[dict[str, Any]]:
        """Return relation/index readiness and estimated extents for map layers."""
        if not self.available:
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
        extent_sql = """
            WITH estimated AS (
                SELECT public.ST_EstimatedExtent($1, $2, $3, TRUE)::geometry AS geom
            ), wgs84 AS (
                SELECT ST_Transform(ST_SetSRID(geom, $4), 4326) AS geom
                FROM estimated
                WHERE geom IS NOT NULL
            )
            SELECT ST_XMin(Box3D(geom)) AS west,
                   ST_YMin(Box3D(geom)) AS south,
                   ST_XMax(Box3D(geom)) AS east,
                   ST_YMax(Box3D(geom)) AS north
            FROM wgs84
        """
        for layer in MAP_LAYERS:
            try:
                layer_source = await self._layer_source_for(layer)
                if layer_source is None:
                    checks.append({"id": layer.id, "available": False})
                    continue
                relation = self._relation_for(layer)
                schema, table = relation.split(".", 1)
                async with layer_source.connection() as connection:
                    if layer.id in CADASTRAL_LAYER_IDS and layer_source is self.cadastral_connection_source:
                        checks.append(await regional_health(connection, layer.id, relation))
                        continue
                    row = await connection.fetchrow(
                        sql, layer.id, relation, schema, table, layer.geometry_column, layer.source_srid
                    )
                    values = tuple(row) if row else (layer.id, False, 0, False, False, 0)
                    geometry_srid = int(values[5] or 0)
                    has_spatial_index = bool(values[4])
                    check = {
                        "id": values[0],
                        "available": bool(
                            values[1] and values[3]
                            and (has_spatial_index or not layer.require_gist_index)
                            and geometry_srid == layer.source_srid
                        ),
                        "relation_exists": bool(values[1]),
                        "geometry_column_exists": bool(values[3]),
                        "gist_index_exists": has_spatial_index,
                        "requires_gist_index": layer.require_gist_index,
                        "geometry_srid": geometry_srid,
                        "expected_srid": layer.source_srid,
                        "srid_matches": geometry_srid == layer.source_srid,
                        "row_estimate": int(values[2] or 0),
                        "coverage": layer.coverage if values[1] else "unavailable",
                        "coverage_note": layer.coverage_note,
                        "coverage_bounds": list(layer.coverage_bounds) if layer.coverage_bounds else None,
                    }
                    if check["available"] and layer.id in {"cadastral-sheets", "cadastral-parcels", "urban-sections"}:
                        try:
                            extent = await connection.fetchrow(
                                extent_sql, schema, table, layer.geometry_column, layer.source_srid
                            )
                            if extent and all(extent[key] is not None for key in ("west", "south", "east", "north")):
                                bounds = [float(extent[key]) for key in ("west", "south", "east", "north")]
                                if bounds[0] < bounds[2] and bounds[1] < bounds[3]:
                                    check["estimated_bounds"] = bounds
                                    check["extent_source"] = "PostGIS geometry statistics"
                        except Exception as exc:
                            # Extents are advisory; a stale/missing ANALYZE
                            # sample must not disable an otherwise healthy layer.
                            log.debug("Could not estimate coverage for %s: %s", layer.id, exc)
            except Exception as exc:
                # Keep one database outage from marking every independent
                # catalog layer unhealthy.
                log.warning("Map layer health check failed for %s: %s", layer.id, exc)
                checks.append({"id": layer.id, "available": False})
                continue
            checks.append(check)
        return checks

    def _properties_for(self, layer: MapLayerSpec, layer_source: Any = None) -> tuple[str, ...]:
        if layer.id in CADASTRAL_LAYER_IDS and layer_source is not None and layer_source is self.cadastral_connection_source:
            return (*layer.properties, *REGIONAL_PROPERTIES)
        return layer.properties

    @staticmethod
    def _source_columns(layer: MapLayerSpec, properties: Optional[tuple[str, ...]] = None) -> str:
        # Every identifier comes from MAP_LAYERS above, never from a request.
        if layer.id != "geo-boundaries":
            return ", ".join(f"t.{column}" for column in (properties or layer.properties))
        # Only boundaries resolve their name and level through the geo.geo_unit
        # join (_source_join); other layers, e.g. municipality profiles, carry
        # canonical_name on their own row and must keep selecting it.
        columns = [f"t.{column}" for column in layer.properties if column not in {"canonical_name", "unit_type"}]
        columns.extend(("u.canonical_name AS canonical_name", "u.unit_type AS unit_type"))
        return ", ".join(columns)

    @staticmethod
    def _source_join(layer: MapLayerSpec) -> str:
        return "LEFT JOIN geo.geo_unit AS u ON u.id = t.geo_unit_id" if layer.id == "geo-boundaries" else ""

    async def read_mvt(self, layer_id: str, z: int, x: int, y: int) -> bytes:
        layer = get_map_layer(layer_id)
        layer_source = await self._layer_source_for(layer)
        if layer_source is None:
            return b""
        columns = self._source_columns(layer, self._properties_for(layer, layer_source))
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
        else:
            padding = 0.0

        # Keep the foreign-table spatial restriction independent of the local
        # one-row bounds CTE. postgres_fdw cannot push a join against that CTE
        # to the remote server, which otherwise transfers the full concession
        # relation before applying the bbox. Inline only validated tile ints so
        # the remote planner sees a constant PostGIS envelope and can use the
        # source GiST index. `bounds` remains for MVT geometry construction.
        tile_envelope = f"ST_TileEnvelope({int(z)}, {int(x)}, {int(y)})"
        source_envelope = f"ST_Transform({tile_envelope}, {layer.source_srid})"
        spatial_filter = f"t.{layer.geometry_column} && ST_Expand({source_envelope}, {padding!r})"
        relation = self._relation_for(layer)
        if layer.kind == "mixed":
            # Points and polygons share this foreign table. A single unordered
            # LIMIT can return only the first physical rows (currently mostly
            # CSV points) for a dense low-zoom tile. Fetch bounded candidates
            # from both classes, then spend the tile cap on polygons first so
            # points cannot hide the concession boundaries.
            column_names = ", ".join(layer.properties)
            point_filter = ""
            if layer.id == "maritime-concessions":
                # Keep marker-only concessions, but do not place a marker over
                # a matching footprint that survives clipping/quantization.
                point_filter = """
                    AND NOT EXISTS (
                        SELECT 1 FROM polygon_features AS p
                        WHERE p.idconc = t.idconc AND p.snapshot_id = t.snapshot_id
                          AND p.geom IS NOT NULL AND ST_Dimension(p.geom) = 2
                    )
                """
            feature_ctes = f"""
                polygon_features AS (
                    SELECT {columns},
                           ST_AsMVTGeom(ST_Transform({geometry}, 3857), bounds.tile, 4096, 64, true) AS geom
                    FROM {relation} AS t {self._source_join(layer)} CROSS JOIN bounds
                    WHERE {spatial_filter} AND ST_Dimension(t.{layer.geometry_column}) = 2
                    LIMIT %s
                ), point_features AS (
                    SELECT {columns},
                           ST_AsMVTGeom(ST_Transform(t.{layer.geometry_column}, 3857), bounds.tile, 4096, 64, true) AS geom
                    FROM {relation} AS t {self._source_join(layer)} CROSS JOIN bounds
                    WHERE {spatial_filter} AND ST_Dimension(t.{layer.geometry_column}) = 0
                    {point_filter}
                    LIMIT %s
                ), prioritized_features AS (
                    SELECT *, 0 AS _priority FROM polygon_features
                    UNION ALL
                    SELECT *, 1 AS _priority FROM point_features
                ), mvtgeom AS (
                    SELECT {column_names}, geom
                    FROM prioritized_features
                    WHERE geom IS NOT NULL
                    ORDER BY _priority
                    LIMIT %s
                )
            """
            params = (layer.max_features, layer.max_features, layer.max_features, layer.id)
        else:
            feature_ctes = f"""
                mvtgeom AS (
                    SELECT {columns},
                           ST_AsMVTGeom(ST_Transform({geometry}, 3857), bounds.tile, 4096, 64, true) AS geom
                    FROM {relation} AS t {self._source_join(layer)} CROSS JOIN bounds
                    WHERE {spatial_filter}
                    LIMIT %s
                )
            """
            params = (layer.max_features, layer.id)

        sql = f"""
            WITH bounds AS (
                SELECT {tile_envelope} AS tile,
                       {source_envelope} AS source
            ), {feature_ctes}
            SELECT COALESCE(ST_AsMVT(mvtgeom, %s::text, 4096, 'geom'), ''::bytea)
            FROM mvtgeom
            WHERE geom IS NOT NULL
        """
        async with layer_source.connection() as connection:
            tile = await connection.fetchval(_asyncpg_sql(sql), *params)
        return tile or b""

    async def read_geojson(self, layer_id: str, bbox: tuple[float, float, float, float], limit: int, offset: int = 0) -> dict[str, Any]:
        layer = get_map_layer(layer_id)
        layer_source = await self._layer_source_for(layer)
        if layer_source is None:
            return {"type": "FeatureCollection", "features": []}
        west, south, east, north = (float(value) for value in bbox)
        if (east - west) * (north - south) > layer.geojson_max_area:
            return {"type": "FeatureCollection", "features": [], "zoom_required": layer.min_zoom}
        columns = self._source_columns(layer, self._properties_for(layer, layer_source))
        relation = self._relation_for(layer)
        # Keep the foreign-table bbox independent of the bounds CTE so
        # postgres_fdw can ship it to the remote PostGIS source.
        source_bbox = (
            f"ST_Transform(ST_MakeEnvelope({west!r}, {south!r}, {east!r}, {north!r}, 4326), "
            f"{layer.source_srid})"
        )
        sql = f"""
            WITH bounds AS (
                SELECT ST_MakeEnvelope(%s, %s, %s, %s, 4326) AS web,
                       ST_Transform(ST_MakeEnvelope(%s, %s, %s, %s, 4326), {layer.source_srid}) AS source
            )
            SELECT {columns},
                   ST_AsGeoJSON(ST_Intersection(ST_Transform(t.{layer.geometry_column}, 4326), bounds.web)) AS geometry
            FROM {relation} AS t {self._source_join(layer)} CROSS JOIN bounds
            WHERE t.{layer.geometry_column} && {source_bbox}
              AND ST_Intersects(t.{layer.geometry_column}, bounds.source)
            ORDER BY t.{layer.id_column}
            LIMIT %s OFFSET %s
        """
        async with layer_source.connection() as connection:
            rows = await connection.fetch(
                _asyncpg_sql(sql),
                west, south, east, north, west, south, east, north,
                min(max(int(limit), 1), layer.max_features), max(int(offset), 0),
            )
        features = []
        for row in rows:
            values = dict(row)
            geometry = values.pop("geometry")
            features.append({
                "type": "Feature",
                "id": values.get(layer.id_column),
                "properties": {key: _json_value(values.get(key)) for key in self._properties_for(layer, layer_source)},
                "geometry": json.loads(geometry) if geometry else None,
            })
        return {"type": "FeatureCollection", "features": features}

    async def read_feature_at_point(self, layer_id: str, lat: float, lng: float) -> Optional[dict[str, Any]]:
        """Return the polygon containing a WGS84 point as a GeoJSON feature."""

        layer = get_map_layer(layer_id)
        layer_source = await self._layer_source_for(layer)
        if layer_source is None:
            return None
        columns = self._source_columns(layer, self._properties_for(layer, layer_source))
        relation = self._relation_for(layer)
        point = f"ST_SetSRID(ST_Point(%s, %s), 4326)"
        source_point = f"ST_Transform({point}, {layer.source_srid})"
        source_point_filter = (
            f"ST_Transform(ST_SetSRID(ST_Point({float(lng)!r}, {float(lat)!r}), 4326), "
            f"{layer.source_srid})"
        )
        sql = f"""
            WITH click AS (
                SELECT {point} AS web, {source_point} AS source
            )
            SELECT {columns},
                   ST_AsGeoJSON(ST_Transform(t.{layer.geometry_column}, 4326)) AS geometry
            FROM {relation} AS t {self._source_join(layer)} CROSS JOIN click
            WHERE t.{layer.geometry_column} && {source_point_filter}
              AND ST_Covers(t.{layer.geometry_column}, click.source)
            LIMIT 1
        """
        async with layer_source.connection() as connection:
            row = await connection.fetchrow(_asyncpg_sql(sql), float(lng), float(lat), float(lng), float(lat))
        if not row:
            return None
        values = dict(row)
        geometry = values.pop("geometry", None)
        return {
            "type": "Feature",
            "id": values.get(layer.id_column),
            "properties": {key: _json_value(values.get(key)) for key in self._properties_for(layer, layer_source)},
            "geometry": json.loads(geometry) if geometry else None,
        }

    async def read_feature_by_reference(
        self, layer_id: str, reference: str, feature_id: Optional[int] = None
    ) -> Optional[dict[str, Any]]:
        """Return one cadastral feature by its canonical parcel reference."""

        layer = get_map_layer(layer_id)
        layer_source = await self._layer_source_for(layer)
        if layer_source is None:
            return None
        if layer.id != "cadastral-parcels":
            raise ValueError("Reference lookup is only supported for cadastral parcels")
        columns = self._source_columns(layer, self._properties_for(layer, layer_source))
        # Panel-facing extras: whole regional loads ship without area_sqm, and
        # the municipality is otherwise only an opaque geo_unit id.
        extras = (
            f"ST_AsGeoJSON(ST_Transform(t.{layer.geometry_column}, 4326)) AS geometry, "
            f"ST_Area(t.{layer.geometry_column}::geography) AS computed_area_sqm, "
            "(SELECT u.canonical_name FROM geo.geo_unit AS u WHERE u.id = t.municipality_id) AS municipality_name"
        )
        normalized = str(reference).strip()
        if layer_source is self.cadastral_connection_source:
            # The dedicated database stores names directly and has no geo_unit
            # spine. Its expression indexes support both IDs and references.
            normalized = normalized.upper()
            extras = (
                f"ST_AsGeoJSON(t.{layer.geometry_column}) AS geometry, "
                f"COALESCE(t.area_sqm, ST_Area(t.{layer.geometry_column}::geography)) AS computed_area_sqm"
            )
            predicate = f"t.id = {identity_sql(layer.id, '%s::text')} AND t.national_cadastral_reference = %s"
            params: tuple[Any, ...] = (normalized, normalized)
            if feature_id is not None:
                predicate += " AND t.id = %s"
                params += (int(feature_id),)
            sql = f"SELECT {columns}, {extras} FROM {layer.table} AS t WHERE {predicate} LIMIT 1"
        elif feature_id is not None:
            # A clicked vector-tile feature already carries its primary key.
            # The reference columns have no index in aecs4u-stats, so an OR
            # lookup over them is a sequential scan of every parcel; the PK
            # lookup is instant, and the reference check guards stale ids.
            sql = f"""
                SELECT {columns}, {extras}
                FROM {layer.table} AS t
                WHERE t.{layer.id_column} = %s
                  AND (t.national_cadastral_reference = %s OR t.canonical_reference = %s)
                LIMIT 1
            """
            params = (int(feature_id), normalized, normalized)
        else:
            sql = f"""
                SELECT {columns}, {extras}
                FROM {layer.table} AS t
                WHERE t.national_cadastral_reference = %s
                   OR t.canonical_reference = %s
                LIMIT 1
            """
            params = (normalized, normalized)
        async with layer_source.connection() as connection:
            row = await connection.fetchrow(_asyncpg_sql(sql), *params)
        if not row:
            return None
        values = dict(row)
        geometry = values.pop("geometry", None)
        properties = {key: _json_value(values.get(key)) for key in self._properties_for(layer, layer_source)}
        for key in ("computed_area_sqm", "municipality_name"):
            if values.get(key) is not None:
                properties[key] = _json_value(values[key])
        return {
            "type": "Feature",
            "id": values.get(layer.id_column),
            "properties": properties,
            "geometry": json.loads(geometry) if geometry else None,
        }

    async def search_parcels_by_reference(self, reference: str, limit: int = 10) -> list[dict[str, Any]]:
        """Return every parcel polygon matching an exact cadastral reference.

        This query is intentionally exact and bounded, and depends on the
        canonical reference indexes installed by scripts/sql/map-reference-indexes.sql.
        """
        layer = get_map_layer("cadastral-parcels")
        layer_source = await self._layer_source_for(layer)
        if layer_source is None:
            return []
        normalized = str(reference or "").strip().upper()
        if layer_source is self.cadastral_connection_source:
            sql = f"""
                SELECT id, canonical_reference, national_cadastral_reference,
                       parcel, sheet, municipality_id, municipality_name
                FROM {layer.table}
                WHERE id = {identity_sql(layer.id, '%s::text')}
                  AND national_cadastral_reference = %s
                LIMIT %s
            """
            async with layer_source.connection() as connection:
                rows = await connection.fetch(_asyncpg_sql(sql), normalized, normalized, min(max(int(limit), 1), 20))
            return [_json_row(row) for row in rows]
        # Resolve matches through the two reference indexes first. A single
        # "a = x OR b = y ORDER BY id LIMIT n" lets the planner walk the
        # primary key instead whenever column statistics are missing or stale
        # (observed on the unanalyzed Veneto load: a full scan past the 2 s
        # search timeout). Each UNION branch is an indexed equality lookup.
        sql = f"""
            WITH matches AS MATERIALIZED (
                SELECT {layer.id_column} AS id FROM {layer.table} WHERE canonical_reference = %s
                UNION
                SELECT {layer.id_column} AS id FROM {layer.table} WHERE national_cadastral_reference = %s
            )
            SELECT t.{layer.id_column} AS id, t.canonical_reference,
                   t.national_cadastral_reference, t.parcel, t.sheet,
                   t.municipality_id, u.canonical_name AS municipality_name
            FROM matches
            JOIN {layer.table} AS t ON t.{layer.id_column} = matches.id
            LEFT JOIN geo.geo_unit AS u ON u.id = t.municipality_id
            ORDER BY t.{layer.id_column}
            LIMIT %s
        """
        async with layer_source.connection() as connection:
            rows = await connection.fetch(
                _asyncpg_sql(sql), normalized, normalized, min(max(int(limit), 1), 20)
            )
        return [_json_row(row) for row in rows]

    async def read_adjacent_features(
        self, layer_id: str, reference: str, limit: int = 25, feature_id: Optional[int] = None
    ) -> list[dict[str, Any]]:
        """Return parcels that touch or overlap the parcel identified by ``reference``.

        Mirrors the legacy "Find Adjacent" analysis (default "intersects"
        method): ``ST_Intersects`` already covers shared-boundary touches, so
        one predicate serves both.
        """

        layer = get_map_layer(layer_id)
        layer_source = await self._layer_source_for(layer)
        if layer_source is None:
            return []
        if layer.id != "cadastral-parcels":
            raise ValueError("Adjacency lookup is only supported for cadastral parcels")
        columns = self._source_columns(layer, self._properties_for(layer, layer_source))
        normalized = str(reference).strip()
        # Same primary-key fast path as read_feature_by_reference: the
        # reference columns are unindexed, so resolving the target by them is
        # a full scan that routinely hits the statement timeout.
        if layer_source is self.cadastral_connection_source:
            normalized = normalized.upper()
            target_predicate = f"id = {identity_sql(layer.id, '%s::text')} AND national_cadastral_reference = %s"
            target_params: tuple[Any, ...] = (normalized, normalized)
            if feature_id is not None:
                target_predicate += " AND id = %s"
                target_params += (int(feature_id),)
        elif feature_id is not None:
            target_predicate = (
                f"{layer.id_column} = %s AND (national_cadastral_reference = %s OR canonical_reference = %s)"
            )
            target_params = (int(feature_id), normalized, normalized)
        else:
            target_predicate = "national_cadastral_reference = %s OR canonical_reference = %s"
            target_params = (normalized, normalized)
        sql = f"""
            WITH target AS (
                SELECT {layer.id_column} AS target_id, {layer.geometry_column} AS geom
                FROM {layer.table}
                WHERE {target_predicate}
                LIMIT 1
            )
            SELECT {columns},
                   ST_AsGeoJSON(ST_Transform(t.{layer.geometry_column}, 4326)) AS geometry
            FROM {layer.table} AS t, target
            WHERE t.{layer.geometry_column} && target.geom
              AND ST_Intersects(t.{layer.geometry_column}, target.geom)
              AND t.{layer.id_column} <> target.target_id
            LIMIT %s
        """
        # Exclude the target by primary key: national_cadastral_reference is
        # NULL for whole regional loads, and NULL IS DISTINCT FROM 'x' is true,
        # so a reference-based exclusion returned the parcel as its own neighbour.
        async with layer_source.connection() as connection:
            rows = await connection.fetch(
                _asyncpg_sql(sql), *target_params, min(max(int(limit), 1), 50),
            )
        features = []
        for row in rows:
            values = dict(row)
            geometry = values.pop("geometry", None)
            features.append({
                "type": "Feature",
                "id": values.get(layer.id_column),
                "properties": {key: _json_value(values.get(key)) for key in self._properties_for(layer, layer_source)},
                "geometry": json.loads(geometry) if geometry else None,
            })
        return features

    async def read_feature_details(self, layer_id: str, feature_id: int) -> Optional[dict[str, Any]]:
        """Return one allow-listed feature and related maritime records."""

        layer = get_map_layer(layer_id)
        layer_source = await self._layer_source_for(layer)
        if layer_source is None:
            return None
        columns = self._source_columns(layer, self._properties_for(layer, layer_source))
        relation = self._relation_for(layer)
        async with layer_source.connection() as connection:
            row = await connection.fetchrow(
                _asyncpg_sql(
                    f"select {columns} from {relation} t "
                    f"where t.{layer.id_column} = %s limit 1"
                ),
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
                try:
                    return [_json_row(item) for item in await connection.fetch(_asyncpg_sql(sql), *params)]
                except Exception as exc:
                    # The concession layer can be published before its
                    # optional document-enrichment relations are provisioned.
                    log.debug("Related concession data unavailable: %s", exc)
                    return []

            snapshot_id = row.get("snapshot_id")
            idconc = row.get("idconc")
            if snapshot_id is None or idconc is None:
                return result
            source_schema = relation.split(".", 1)[0]
            result["related"]["document_gaps"] = await related_rows(
                "select gap_id, source_row_id, idconc, institution_key, document_type, "
                "document_description, status, evidence, checked_at "
                f"from {source_schema}.document_gaps "
                "where source_row_id = %s order by gap_id",
                feature_id,
            )
            matches = await related_rows(
                "select match_id, document_url, idconc, match_type, confidence, evidence, "
                "extraction_status, extracted_text_chars, page_count, checked_at "
                f"from {source_schema}.online_document_matches "
                "where snapshot_id = %s and idconc = %s order by match_id",
                snapshot_id,
                idconc,
            )
            result["related"]["online_document_matches"] = matches
            urls = [item["document_url"] for item in matches if item.get("document_url")]
            result["related"]["online_documents"] = await related_rows(
                "select source_page_url, document_url, title, document_kind, status, "
                "content_type, local_path, size_bytes, sha256, discovered_at, "
                f"downloaded_at, error from {source_schema}.online_documents "
                "where snapshot_id = %s and document_url = any(%s::text[]) "
                "order by document_url",
                snapshot_id,
                urls,
            ) if urls else []
            result["related"]["snapshot"] = await related_rows(
                "select snapshot_id, package_id, reference_date, status, manifest_path, loaded_at_utc "
                f"from {source_schema}.snapshots where snapshot_id = %s",
                snapshot_id,
            )
            result["related"]["resources"] = await related_rows(
                "select resource_id, kind, resource_title, requested_url, final_url, "
                f"size_bytes, sha256, qa_json from {source_schema}.resources "
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
        # Some serving rows have a geo_unit_id but no materialized display
        # name. Resolve the canonical name from the authoritative unit table so
        # search results never fall back to opaque ISTAT codes.
        name_expression = "COALESCE(NULLIF(t.canonical_name, ''), u.canonical_name)"
        columns = self._source_columns(layer).replace(
            "t.canonical_name", f"{name_expression} AS canonical_name", 1
        )
        columns = f"{columns}, ST_Y(ST_Centroid(t.{layer.geometry_column})) AS latitude, " \
                  f"ST_X(ST_Centroid(t.{layer.geometry_column})) AS longitude, " \
                  f"ST_XMin(Box2D(ST_Transform(t.{layer.geometry_column}, 4326))) AS west, " \
                  f"ST_YMin(Box2D(ST_Transform(t.{layer.geometry_column}, 4326))) AS south, " \
                  f"ST_XMax(Box2D(ST_Transform(t.{layer.geometry_column}, 4326))) AS east, " \
                  f"ST_YMax(Box2D(ST_Transform(t.{layer.geometry_column}, 4326))) AS north"
        sql = f"""
            SELECT {columns}
            FROM {layer.table} AS t
            LEFT JOIN geo.geo_unit AS u ON u.id = t.geo_unit_id
            WHERE {name_expression} ILIKE %s
               OR t.istat_code ILIKE %s
            ORDER BY CASE
                WHEN lower({name_expression}) = lower(%s) THEN 0
                WHEN lower({name_expression}) LIKE lower(%s) THEN 1
                ELSE 2
            END, {name_expression} ASC
            LIMIT %s
        """
        pattern = f"%{normalized}%"
        prefix = f"{normalized}%"
        # Bind order follows the placeholders above: substring name match,
        # ISTAT-code prefix, then exact-name and name-prefix ranking.
        async with self.connection_source.connection() as connection:
            rows = await connection.fetch(
                _asyncpg_sql(sql), pattern, prefix, normalized, prefix, min(max(int(limit), 1), 50)
            )
        return [_json_row(row) for row in rows]


_source: Optional[PostgresMapLayerSource] = None
_search_source: Optional[PostgresMapLayerSource] = None
_source_lock = threading.Lock()


def get_map_layer_source() -> PostgresMapLayerSource:
    global _source
    if _source is None:
        with _source_lock:
            if _source is None:
                _source = PostgresMapLayerSource()
    return _source


def get_map_search_source() -> PostgresMapLayerSource:
    """Use a small reserved pool for latency-sensitive interactive search.

    Municipality and parcel-reference search both run here so they never
    queue behind tile rendering on the shared pool.
    """
    global _search_source
    if _search_source is not None:
        return _search_source
    map_source = get_map_layer_source()
    tile_source = map_source.connection_source
    with _source_lock:
        if _search_source is None:
            if tile_source is None:
                _search_source = PostgresMapLayerSource(None)
            else:
                _search_connection_source = _AsyncpgConnectionSource(
                    tile_source.dsn,
                    max_connections=2,
                    connect_timeout=tile_source.connect_timeout,
                )
                cadastral_source = map_source.cadastral_connection_source
                search_cadastral_source = (
                    _AsyncpgConnectionSource(
                        cadastral_source.dsn, max_connections=2,
                        connect_timeout=cadastral_source.connect_timeout,
                    ) if cadastral_source is not None else None
                )
                _search_source = PostgresMapLayerSource(
                    _search_connection_source, cadastral_connection_source=search_cadastral_source
                )
    return _search_source


async def warm_map_search_source() -> None:
    """Open the reserved search pool ahead of the first query.

    Creating the pool lazily put its connection setup (seconds under database
    load) inside the first search's timeout, which then returned 503.
    """
    connection_source = get_map_search_source().connection_source
    if connection_source is None:
        return
    try:
        await connection_source._get_pool()
    except Exception as exc:
        log.warning("Map search pool warm-up failed (search will retry lazily): %s", exc)


async def warm_low_zoom_map_tiles() -> None:
    """Prime a compact set of Italy boundary tiles after startup.

    The single-worker pass warms shared PostGIS pages used by the common
    national view. It is delayed and limited to z5–z6 to avoid a large startup
    query burst; deployments can disable it with MAP_TILE_WARMUP_ENABLED=0.
    """
    if os.getenv("MAP_TILE_WARMUP_ENABLED", "1").strip().lower() in {"0", "false", "no", "off"}:
        return
    try:
        delay = max(0.0, float(os.getenv("MAP_TILE_WARMUP_DELAY_SECONDS", "90")))
    except ValueError:
        delay = 90.0
    if delay:
        await asyncio.sleep(delay)
    source = get_map_layer_source()
    if not source.available or source.connection_source is None:
        return
    async def database_is_busy() -> bool:
        async with source.connection_source.connection() as connection:
            return await connection.fetchval("""
                SELECT EXISTS (
                    SELECT 1 FROM pg_stat_activity
                    WHERE datname = current_database()
                      AND pid <> pg_backend_pid()
                      AND state = 'active'
                      AND (
                          (query ILIKE '%UPDATE spatial.cadastral_parcel%'
                           AND query ILIKE '%cadastral.veneto.IT.duckdb%')
                          OR query ILIKE '%ST_AsMVT%'
                          OR query ILIKE '%ST_AsMVTGeom%'
                      )
                )
            """)

    try:
        if await database_is_busy():
            log.info("Skipping low-zoom tile warm-up while cadastral updates or live tile queries are active")
            return
    except Exception as exc:
        log.info("Skipping low-zoom tile warm-up because database load could not be checked: %s", exc)
        return

    west, south, east, north = 6.5, 36.3, 18.6, 47.2
    for zoom in (5, 6):
        scale = 1 << zoom
        x_min = max(0, int((west + 180.0) / 360.0 * scale))
        x_max = min(scale - 1, int((east + 180.0) / 360.0 * scale))
        y_for_lat = lambda lat: int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * scale)
        y_min = max(0, y_for_lat(north))
        y_max = min(scale - 1, y_for_lat(south))
        for x in range(x_min, x_max + 1):
            for y in range(y_min, y_max + 1):
                try:
                    if await database_is_busy():
                        log.info("Pausing low-zoom tile warm-up because live map queries became active")
                        return
                    await source.read_mvt("geo-boundaries", zoom, x, y)
                except Exception as exc:
                    log.warning("Map tile warm-up failed for geo-boundaries/%s/%s/%s: %s", zoom, x, y, exc)
                    return
                await asyncio.sleep(0.1)
    log.info("Warmed low-zoom administrative boundary tiles for Italy (z5–z6)")


async def close_map_layer_source() -> None:
    global _source, _search_source
    with _source_lock:
        source, _source = _source, None
        search_source, _search_source = _search_source, None
    if search_source is not None:
        await search_source.close()
    if source is not None:
        await source.close()
