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
    # Highest zoom at which the browser requests tiles. Above it MapLibre
    # overzooms the tile it already has, so a z18 view reuses z16 tiles instead
    # of requesting 4-16x more of them. At z16 one tile unit is ~0.15 m, finer
    # than any source geometry here.
    tile_max_zoom: int = 16
    # Relations can move between schemas during canonical-database migrations.
    # Keep alternatives explicit and allow-listed just like the primary table.
    fallback_tables: tuple[str, ...] = ()

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
        value.pop("fallback_tables", None)
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
    MapLayerSpec("geo-boundaries", "Administrative boundaries", "istat.v_comuni_map", "geom", properties=("id", "geo_unit_id", "canonical_name", "unit_type", "generalization", "source_release"), role="admin-substitute", group="administrative", color="#526b84", z_order=10, fill_opacity=0.075, line_width=1.1, unit_levels=((0, "region"), (8, "province"), (10, "municipality")), require_gist_index=False, tile_revision="istat-2"),
    # Coverage changes as upstream publications are loaded. Do not hard-code a
    # region or a row count here; health() reports the current estimated extent
    # from PostGIS statistics when ANALYZE data is available.
    MapLayerSpec("cadastral-sheets", "Cadastral sheets", "spatial.cadastral_sheet", "geom", min_zoom=10, geojson_max_area=4.0, coverage="unknown", properties=("id", "sheet_reference", "municipality_id", "level", "level_name", "area_sqm", "source_release"), group="cadastral", color="#1976a8", z_order=70, tile_revision="regional-1"),
    MapLayerSpec("cadastral-parcels", "Cadastral parcels", "spatial.cadastral_parcel", "geom", min_zoom=14, max_features=5000, geojson_max_area=0.04, coverage="unknown", properties=("id", "canonical_reference", "national_cadastral_reference", "parcel", "sheet", "municipality_id", "area_sqm", "source_release", "has_visura", "is_auction_sale"), group="cadastral", color="#d97925", z_order=90, fill_opacity=0.04, line_width=0.8, tile_revision="regional-4"),
    MapLayerSpec("urban-sections", "Cadastral urban sections", "sezioni_urbane.sezioni_urbane", "geom", id_column="OGC_FID", min_zoom=11, coverage="unknown", properties=("OGC_FID", "nationalcadastralzoningreference", "administrativeunit", "sezione_urbana"), group="cadastral", color="#2f9aa8", z_order=80, require_gist_index=False),
    MapLayerSpec("market-zones", "OMI market zones", "zornade.zornade_zone_omi", "geom", id_column="OGC_FID", min_zoom=10, properties=("OGC_FID", "codcom", "codzona", "zona_descr", "comune_descrizione", "descr_tip_prev", "compr_min", "compr_max"), group="market", color="#7b61a8", z_order=30, require_gist_index=False, tile_revision="2"),
    MapLayerSpec("postal-zones", "Postal zones", "cap_subcomunali.cap_subcomunali", "geom", id_column="OGC_FID", min_zoom=10, properties=("OGC_FID", "cap", "comune_cap", "comune", "provincia", "regione", "fonte"), group="administrative", color="#8a7a3d", z_order=40, require_gist_index=False, tile_revision="3"),
    MapLayerSpec("flood-hazard", "Flood hazard areas", "hazards.flood_hazard", "geom", id_column="scenario_code", min_zoom=8, max_features=3000, properties=("scenario_code", "scenario_label", "area_sqm"), group="risk", color="#2b7bb9", z_order=50, fill_opacity=0.2, require_gist_index=False, tile_revision="2"),
    MapLayerSpec("landslide-hazard", "Landslide hazard areas", "hazards.landslide_hazard", "geom", id_column="hazard_code", min_zoom=8, max_features=3000, properties=("hazard_code", "hazard_label", "area_sqm"), group="risk", color="#a0522d", z_order=51, fill_opacity=0.2, require_gist_index=False, tile_revision="2"),
    MapLayerSpec("census-sections", "ISTAT census sections", "census.sections", "geom", id_column="sez21_id", source_srid=32632, min_zoom=11, max_features=2000, properties=("sez21_id", "procom", "cod_reg", "pop21", "fam21", "abi21", "edi21"), group="demographics", color="#3f8f73", z_order=60, fill_opacity=0.3, color_ramp=("pop21", ((0, "#e8f4ef"), (50, "#bfe3d2"), (200, "#7fc1a3"), (500, "#3f8f73"), (1500, "#1f5d49"))), fallback_tables=("census_sections.sections",)),
    MapLayerSpec("mps04-points", "MPS04 seismic points", "hazards.mps04_points", "geom", id_column="point_id", min_zoom=7, max_features=3000, properties=("point_id", "grid_variant", "lon", "lat"), kind="point", group="risk", color="#5b6fb5", z_order=120, require_gist_index=False, tile_revision="1"),
    MapLayerSpec("seismic-classification", "Seismic classification", "hazards.v_seismic_classification_map", "geom", id_column="istat_code", min_zoom=6, max_features=3000, properties=("istat_code", "comune", "regione", "provincia", "zone", "zone_label"), group="risk", color="#7a4cb5", z_order=110, fill_opacity=0.2, color_match=("zone_label", (("zone 1", "#7d1515"), ("zone 2", "#c44444"), ("zone 3", "#e6a700"), ("zone 4", "#5b8f55"))), require_gist_index=False, tile_revision="1"),
    MapLayerSpec("surface-subsidence", "Ground movement (EGMS)", "geo_surface_change.egms_subsidenza", "geom", id_column="risk_index", min_zoom=8, max_features=3000, geojson_max_area=4.0, properties=("risk_index", "mean_velocity", "acceleration", "risk_class", "risk_class_label", "velocity_class_label", "signal_quality"), group="risk", color="#c45c2d", z_order=115, fill_opacity=0.25, require_gist_index=False, tile_revision="2"),
    MapLayerSpec(
        "solar-potential", "Solar potential by municipality",
        "solar.solar_potential_comuni", "geom", id_column="id",
        properties=(
            "id", "geo_unit_id", "canonical_name", "n_buildings",
            "pvout_modern_kwh_year_total", "pvout_pessimistic_kwh_year_total",
            "kwp_max_total", "high_viability_pct", "medium_viability_pct",
            "low_viability_pct", "not_eligible_pct", "solar_data_version", "updated_at",
        ),
        min_zoom=5, max_features=5000, group="energy", color="#e6a700",
        z_order=45, fill_opacity=0.48,
        coverage_note="Municipality geometry joined from ISTAT by municipality code",
        color_ramp=("high_viability_pct", ((0, "#fff2b2"), (25, "#f6c453"), (50, "#e78a24"), (75, "#b84a1b"), (100, "#762a12"))),
        require_gist_index=False, tile_revision="2",
    ),
    MapLayerSpec(
        "maritime-concessions", "Maritime-domain concessions",
        "agenziademanio.concessions", "geom",
        id_column="row_id", min_zoom=6, max_features=5000, geojson_max_area=4.0,
        coverage_note="MIT/SID snapshot; published coverage may be incomplete",
        properties=(
            "row_id", "idconc", "admin_label", "layer_kind", "geometry_type",
            "crs_original", "snapshot_id", "source_release",
            "geometry_valid_4326", "geometry_repaired",
        ),
        kind="mixed", group="territory", color="#1f8ea3", z_order=130,
        polygon_color="#e45724", fill_opacity=0.42, line_width=3.0,
        require_gist_index=False, tile_revision="3",
    ),
)

_BY_ID = {layer.id: layer for layer in MAP_LAYERS}
_HAZARDS_SOURCE_RELATIONS = {
    "flood-hazard": "public.flood_hazard",
    "landslide-hazard": "public.landslide_hazard",
    "mps04-points": "public.mps04_points",
    "seismic-classification": "public.v_seismic_classification_map",
}
_EGMS_SOURCE_RELATIONS = {"surface-subsidence": "public.egms_subsidenza"}


def _istat_boundary_relation() -> str:
    """Normalize canonical ISTAT map views to the boundary tile schema."""
    return """(
        SELECT (1000000000 + cod_reg)::bigint AS id,
               cod_reg::bigint AS geo_unit_id,
               name AS canonical_name,
               'region'::text AS unit_type,
               NULL::text AS generalization,
               'ISTAT'::text AS source_release,
               geom
        FROM istat.v_regioni_map
        UNION ALL
        SELECT (2000000000 + cod_uts)::bigint AS id,
               cod_uts::bigint AS geo_unit_id,
               name AS canonical_name,
               'province'::text AS unit_type,
               NULL::text AS generalization,
               'ISTAT'::text AS source_release,
               geom
        FROM istat.v_province_map
        UNION ALL
        SELECT (3000000000 + pro_com)::bigint AS id,
               pro_com::bigint AS geo_unit_id,
               name AS canonical_name,
               'municipality'::text AS unit_type,
               NULL::text AS generalization,
               'ISTAT'::text AS source_release,
               geom
        FROM istat.v_comuni_map
    )"""


def _solar_potential_relation() -> str:
    """Join canonical solar metrics to current ISTAT municipality geometry."""
    return """(
        SELECT u.pro_com::bigint AS id,
               u.pro_com::bigint AS geo_unit_id,
               u.name AS canonical_name,
               s.n_buildings,
               s.pvout_modern_kwh_year_total,
               s.pvout_pessimistic_kwh_year_total,
               s.kwp_max_total,
               s.high_viability_pct,
               s.medium_viability_pct,
               s.low_viability_pct,
               s.not_eligible_pct,
               s.solar_data_version,
               s.updated_at,
               u.geom
        FROM solar.solar_potential_comuni AS s
        JOIN istat.v_comuni_map AS u
          ON ltrim(s.pro_com_t::text, '0') = u.pro_com::text
    )"""


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


def _sister_cadastral_values(value: Any, *, sheet: bool = False) -> set[str]:
    """Normalize the fixed-width sheet and parcel values used by AdE and SISTER."""
    text = str(value or "").strip()
    if not text:
        return set()
    values = {text}
    try:
        compact = str(int(text))
    except ValueError:
        compact = text.lstrip("0") or "0"
    values.add(compact)
    # Some AdE regional extracts encode a sheet as a six-character number
    # whose last two zeroes are a precision marker (e.g. 001800 -> SISTER 18).
    if sheet and compact.isdigit() and int(compact) >= 100 and int(compact) % 100 == 0:
        values.add(str(int(compact) // 100))
    return values


def _sister_reference_parts(reference: Any) -> tuple[str, str, str, str]:
    """Extract municipality, section, sheet and parcel from a cadastral reference."""
    value = str(reference or "").strip().upper()
    if "_" in value:
        head, suffix = value.split("_", 1)
        match = re.fullmatch(r"([A-Z]\d{3})([A-Z]?)", head)
        sheet, _, parcel = suffix.partition(".")
    else:
        match = re.match(r"^([A-Z]\d{3})([A-Z]?)(\d+)\.(.+)$", value)
        sheet, parcel = (match.group(3), match.group(4)) if match else ("", "")
    if not match:
        return "", "", "", ""
    return match.group(1), match.group(2), sheet, parcel.split("/", 1)[0]


# SISTER document types that are a visura per immobile (the `has_visura` flag).
_VISURA_DOCUMENT_TYPES = ("visura_fabbricati", "visura_terreni", "visura")

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

    # Every pooled connection that reads a foreign table keeps one backend open
    # on each remote server it touched, so the pool size multiplies across the
    # ~10 foreign servers behind the map. Four keeps a tile burst from using
    # up Postgres' connection slots; override with MAP_DB_POOL_MAX.
    DEFAULT_POOL_SIZE = 4
    ACQUIRE_TIMEOUT_SECONDS = 15.0

    def __init__(self, dsn: str, max_connections: Optional[int] = None, connect_timeout: float = 3.0):
        self.dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
        if max_connections is None:
            try:
                max_connections = int(os.getenv("MAP_DB_POOL_MAX", ""))
            except ValueError:
                max_connections = self.DEFAULT_POOL_SIZE
            max_connections = max(1, min(max_connections, 32))
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
    def hazards_from_environment(cls) -> Optional["_AsyncpgConnectionSource"]:
        """Use the hazards database directly so local GiST indexes serve tiles."""
        direct_dsn = os.getenv("HAZARDS_POSTGRES_DSN") or os.getenv("AECS4U_STATS_HAZARDS_DATABASE_URL")
        if direct_dsn:
            if direct_dsn.startswith(("postgres://", "postgresql://", "postgresql+")):
                return cls(direct_dsn)
            return None
        stats = cls.from_environment()
        if stats is None:
            return None
        parsed = urlsplit(stats.dsn)
        return cls(urlunsplit(parsed._replace(path="/hazards")))

    @classmethod
    def egms_from_environment(cls) -> Optional["_AsyncpgConnectionSource"]:
        """Use the EGMS database directly so its local GiST index serves tiles."""
        direct_dsn = os.getenv("EGMS_POSTGRES_DSN") or os.getenv("AECS4U_STATS_EGMS_DATABASE_URL")
        if direct_dsn:
            if direct_dsn.startswith(("postgres://", "postgresql://", "postgresql+")):
                return cls(direct_dsn)
            return None
        stats = cls.from_environment()
        if stats is None:
            return None
        parsed = urlsplit(stats.dsn)
        return cls(urlunsplit(parsed._replace(path="/egms")))


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
                        # Every pooled connection that reads a foreign table
                        # keeps one backend open per remote server. Recycle idle
                        # connections quickly so a tile burst does not leave
                        # dozens of FDW backends holding Postgres connection
                        # slots ("too many clients already").
                        max_inactive_connection_lifetime=20,
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
        async with pool.acquire(timeout=self.ACQUIRE_TIMEOUT_SECONDS) as connection:
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
        hazards_connection_source: Optional[_AsyncpgConnectionSource] = None,
        egms_connection_source: Optional[_AsyncpgConnectionSource] = None,
    ):
        use_environment = connection_source is None
        if connection_source is None:
            connection_source = _AsyncpgConnectionSource.from_environment()
        self.connection_source = connection_source
        self.census_connection_source = census_connection_source
        self.cadastral_connection_source = cadastral_connection_source
        if self.cadastral_connection_source is None and use_environment and self.connection_source is not None:
            self.cadastral_connection_source = _AsyncpgConnectionSource.cadastral_from_environment()
        self.hazards_connection_source = hazards_connection_source
        if self.hazards_connection_source is None and use_environment and self.connection_source is not None:
            self.hazards_connection_source = _AsyncpgConnectionSource.hazards_from_environment()
        self.egms_connection_source = egms_connection_source
        if self.egms_connection_source is None and use_environment and self.connection_source is not None:
            self.egms_connection_source = _AsyncpgConnectionSource.egms_from_environment()
        self._hazards_routes: dict[str, tuple[Any, float]] = {}
        self._egms_routes: dict[str, tuple[Any, float]] = {}
        self._cadastral_routes: dict[str, tuple[Any, float]] = {}
        self._resolved_relations: dict[str, tuple[str, float]] = {}

    def _source_for(self, layer: MapLayerSpec) -> Any:
        if layer.id == "census-sections" and self.census_connection_source is not None:
            return self.census_connection_source
        if layer.id in _HAZARDS_SOURCE_RELATIONS and self.hazards_connection_source is not None:
            return self.hazards_connection_source
        if layer.id in _EGMS_SOURCE_RELATIONS and self.egms_connection_source is not None:
            return self.egms_connection_source
        return self.connection_source

    async def _layer_source_for(self, layer: MapLayerSpec) -> Any:
        if layer.id in _HAZARDS_SOURCE_RELATIONS and self.hazards_connection_source is not None:
            cached = self._hazards_routes.get(layer.id)
            if cached and cached[1] > time.monotonic():
                return cached[0]
            selected = self.connection_source
            try:
                async with self.hazards_connection_source.connection() as connection:
                    if await connection.fetchval(
                        "SELECT to_regclass($1)::text", _HAZARDS_SOURCE_RELATIONS[layer.id]
                    ):
                        selected = self.hazards_connection_source
            except Exception as exc:
                log.debug("Direct hazards map source unavailable for %s: %s", layer.id, exc)
            ttl = 300 if selected is self.hazards_connection_source else 10
            self._hazards_routes[layer.id] = (selected, time.monotonic() + ttl)
            return selected
        if layer.id in _EGMS_SOURCE_RELATIONS and self.egms_connection_source is not None:
            cached = self._egms_routes.get(layer.id)
            if cached and cached[1] > time.monotonic():
                return cached[0]
            selected = self.connection_source
            try:
                async with self.egms_connection_source.connection() as connection:
                    if await connection.fetchval(
                        "SELECT to_regclass($1)::text", _EGMS_SOURCE_RELATIONS[layer.id]
                    ):
                        selected = self.egms_connection_source
            except Exception as exc:
                log.debug("Direct EGMS map source unavailable for %s: %s", layer.id, exc)
            ttl = 300 if selected is self.egms_connection_source else 10
            self._egms_routes[layer.id] = (selected, time.monotonic() + ttl)
            return selected
        if layer.id not in CADASTRAL_LAYER_IDS or self.cadastral_connection_source is None:
            return self._source_for(layer)
        cached = self._cadastral_routes.get(layer.id)
        if cached and cached[1] > time.monotonic():
            return cached[0]
        # Preserve populated canonical installations; some deployments retain
        # an empty compatibility view in stats after moving the regional data
        # to the dedicated cadastral database.
        selected = self.connection_source
        if selected is not None:
            try:
                async with selected.connection() as connection:
                    canonical_exists = await connection.fetchval("SELECT to_regclass($1)::text", layer.table)
                    canonical_has_features = (
                        await connection.fetchval(
                            f"SELECT EXISTS (SELECT 1 FROM {layer.table} LIMIT 1)"
                        )
                        if canonical_exists
                        else False
                    )
                    canonical_has_parcel_flags = (
                        await connection.fetchval("""
                            SELECT count(*) = 2
                            FROM pg_attribute
                            WHERE attrelid = to_regclass($1)
                              AND attname::text IN ('has_visura', 'is_auction_sale')
                              AND attnum > 0 AND NOT attisdropped
                        """, layer.table)
                        if canonical_exists and layer.id == "cadastral-parcels"
                        else True
                    )
            except Exception as exc:
                log.debug("Canonical cadastral source unavailable: %s", exc)
                canonical_exists = False
                canonical_has_features = False
                canonical_has_parcel_flags = False
            if canonical_exists and canonical_has_features and canonical_has_parcel_flags:
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

    async def _resolve_relation_for(self, layer: MapLayerSpec, layer_source: Any) -> str:
        """Resolve a configured schema fallback and cache it briefly."""
        if layer.id == "census-sections" and layer_source is self.census_connection_source:
            return "public.sections"
        if layer_source is self.hazards_connection_source and layer.id in _HAZARDS_SOURCE_RELATIONS:
            return _HAZARDS_SOURCE_RELATIONS[layer.id]
        if layer_source is self.egms_connection_source and layer.id in _EGMS_SOURCE_RELATIONS:
            return _EGMS_SOURCE_RELATIONS[layer.id]
        cached = self._resolved_relations.get(layer.id)
        if cached and cached[1] > time.monotonic():
            return cached[0]
        candidates = (layer.table, *layer.fallback_tables)
        try:
            async with layer_source.connection() as connection:
                for relation in candidates:
                    if await connection.fetchval("SELECT to_regclass($1)::text", relation):
                        self._resolved_relations[layer.id] = (relation, time.monotonic() + 300)
                        return relation
        except Exception as exc:
            log.debug("Could not resolve map relation for %s: %s", layer.id, exc)
        # Preserve the primary relation during outages; the actual request will
        # report its normal transient database error and the cache can retry.
        return layer.table

    @property
    def available(self) -> bool:
        return self.connection_source is not None

    async def close(self) -> None:
        sources = {
            id(source): source
            for source in (
                self.connection_source, self.census_connection_source,
                self.cadastral_connection_source, self.hazards_connection_source,
                self.egms_connection_source,
            )
            if source is not None
        }
        for source in sources.values():
            await source.close()

    async def health(self) -> list[dict[str, Any]]:
        """Return relation/index readiness and estimated extents for map layers."""
        if not self.available:
            return [{
                "id": layer.id, "available": False,
                "schema": layer.table.split(".", 1)[0],
                "schema_exists": None, "database_name": None,
                "source_database": "aecs4u-stats",
            } for layer in MAP_LAYERS]
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
                   ), 0)::integer AS geometry_srid,
                   to_regnamespace(r.schema_name) IS NOT NULL AS schema_exists,
                   current_database() AS database_name
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
            schema = layer.table.split(".", 1)[0]
            source_database = "aecs4u-stats"
            try:
                layer_source = await self._layer_source_for(layer)
                if layer_source is None:
                    checks.append({
                        "id": layer.id, "available": False, "schema": schema,
                        "schema_exists": None, "database_name": None,
                        "source_database": source_database,
                    })
                    continue
                if layer_source is self.cadastral_connection_source:
                    source_database = "cadastral"
                elif layer_source is self.census_connection_source:
                    source_database = "census"
                elif layer_source is self.hazards_connection_source:
                    source_database = "hazards"
                elif layer_source is self.egms_connection_source:
                    source_database = "egms"
                relation = await self._resolve_relation_for(layer, layer_source)
                schema, table = relation.split(".", 1)
                async with layer_source.connection() as connection:
                    if layer.id in CADASTRAL_LAYER_IDS and layer_source is self.cadastral_connection_source:
                        check = await regional_health(connection, layer.id, relation)
                        check.update({
                            "schema": schema,
                            "schema_exists": bool(await connection.fetchval(
                                "SELECT to_regnamespace($1::text) IS NOT NULL", schema
                            )),
                            "database_name": await connection.fetchval("SELECT current_database()"),
                            "source_database": source_database,
                        })
                        checks.append(check)
                        continue
                    row = await connection.fetchrow(
                        sql, layer.id, relation, schema, table, layer.geometry_column, layer.source_srid
                    )
                    values = tuple(row) if row else (layer.id, False, 0, False, False, 0)
                    schema_exists = (
                        None if len(values) <= 6 or values[6] is None else bool(values[6])
                    )
                    database_name = str(values[7]) if len(values) > 7 and values[7] else None
                    geometry_srid = int(values[5] or 0)
                    if layer.id == "solar-potential" and values[1]:
                        # The consolidated solar relation contains attributes
                        # only. The map query joins those rows to ISTAT
                        # municipality geometry, so validate the geometry side
                        # of that virtual spatial source here.
                        geometry_srid = int(await connection.fetchval(
                            "SELECT ST_SRID(geom) FROM istat.v_comuni_map "
                            "WHERE geom IS NOT NULL LIMIT 1"
                        ) or 0)
                        values = (values[0], values[1], values[2], geometry_srid > 0, values[4], geometry_srid)
                    # PostGIS's geometry_columns view cannot always infer the
                    # SRID of a geometry column exposed by a SQL view (for
                    # example, serving.comuni). In that
                    # case, inspect one non-null geometry so a usable view is
                    # not incorrectly marked unavailable in the layer UI.
                    if geometry_srid == 0 and values[1] and values[3]:
                        geometry_srid = int(await connection.fetchval(
                            f"SELECT ST_SRID({layer.geometry_column}) "
                            f"FROM {relation} "
                            f"WHERE {layer.geometry_column} IS NOT NULL LIMIT 1"
                        ) or 0)
                    has_spatial_index = bool(values[4])
                    check = {
                        "id": values[0],
                        "available": bool(
                            values[1] and values[3]
                            and (has_spatial_index or not layer.require_gist_index)
                            and geometry_srid == layer.source_srid
                        ),
                        "relation_exists": bool(values[1]),
                        "schema": schema,
                        "schema_exists": schema_exists,
                        "database_name": database_name,
                        "source_database": source_database,
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
                checks.append({
                    "id": layer.id, "available": False, "schema": schema,
                    "schema_exists": None, "database_name": None,
                    "source_database": source_database,
                })
                continue
            checks.append(check)
        return checks

    def _properties_for(self, layer: MapLayerSpec, layer_source: Any = None) -> tuple[str, ...]:
        if layer.id in CADASTRAL_LAYER_IDS and layer_source is not None and layer_source is self.cadastral_connection_source:
            return (*layer.properties, *REGIONAL_PROPERTIES)
        return layer.properties

    @staticmethod
    def _column_ref(alias: str, column: str) -> str:
        """Quote catalog-defined column names, including OGR's mixed-case IDs."""
        return f'{alias}."{column.replace(chr(34), chr(34) * 2)}"'

    @staticmethod
    def _source_columns(layer: MapLayerSpec, properties: Optional[tuple[str, ...]] = None) -> str:
        # Every identifier comes from MAP_LAYERS above, never from a request.
        return ", ".join(PostgresMapLayerSource._column_ref("t", column) for column in (properties or layer.properties))

    @staticmethod
    def _source_join(layer: MapLayerSpec) -> str:
        return ""

    @staticmethod
    def _query_relation(layer: MapLayerSpec, resolved_relation: str) -> str:
        if layer.id == "geo-boundaries":
            return _istat_boundary_relation()
        if layer.id == "solar-potential":
            return _solar_potential_relation()
        return resolved_relation

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
        geometry_ref = self._column_ref("t", layer.geometry_column)
        geometry = geometry_ref
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
        spatial_filter = f"{geometry_ref} && ST_Expand({source_envelope}, {padding!r})"
        relation = self._query_relation(layer, await self._resolve_relation_for(layer, layer_source))
        level_filter = ""
        if layer.id == "geo-boundaries":
            level = next((unit for minimum, unit in layer.unit_levels if z >= minimum), layer.unit_levels[0][1])
            for minimum, unit in layer.unit_levels:
                if z >= minimum:
                    level = unit
            level_filter = " AND t.unit_type = %s"
        if layer.kind == "mixed":
            # Points and polygons share this foreign table. A single unordered
            # LIMIT can return only the first physical rows (currently mostly
            # CSV points) for a dense low-zoom tile. Fetch bounded candidates
            # from both classes, then spend the tile cap on polygons first so
            # points cannot hide the concession boundaries.
            column_names = ", ".join(layer.properties)
            # Sub-pixel footprints collapse to nothing in ST_AsMVTGeom. Drop
            # them in the remote-pushable filter so they neither cross the FDW
            # nor consume the tile cap. Source units are degrees (or metres
            # for projected sources); the Mercator pixel is never smaller than
            # this, so only polygons that would vanish are skipped.
            min_polygon_area = 0.25 * (tile_width / 4096) ** 2
            point_filter = ""
            if layer.id == "maritime-concessions":
                # Keep marker-only concessions, but do not place a marker over
                # a matching footprint that survives clipping/quantization.
                # Probe a deduplicated key CTE: anti-joining against the
                # geometry-bearing polygon CTE was planned as a nested loop
                # (points x polygons) and took ~4s on a dense z7 tile.
                point_filter = """
                    AND NOT EXISTS (
                        SELECT 1 FROM polygon_keys AS p
                        WHERE p.idconc = t.idconc AND p.snapshot_id = t.snapshot_id
                    )
                """
            feature_ctes = f"""
                polygon_features AS (
                    SELECT * FROM (
                        SELECT {columns},
                               ST_AsMVTGeom(ST_Transform({geometry}, 3857), bounds.tile, 4096, 64, true) AS geom
                        FROM {relation} AS t {self._source_join(layer)} CROSS JOIN bounds
                        WHERE {spatial_filter} AND ST_Dimension({geometry_ref}) = 2
                          AND ST_Area({geometry_ref}) >= {min_polygon_area!r}
                    ) AS clipped
                    WHERE geom IS NOT NULL
                    LIMIT %s
                ), polygon_keys AS (
                    SELECT DISTINCT idconc, snapshot_id FROM polygon_features
                ), point_features AS (
                    SELECT {columns},
                           ST_AsMVTGeom(ST_Transform(t.{layer.geometry_column}, 3857), bounds.tile, 4096, 64, true) AS geom
                    FROM {relation} AS t {self._source_join(layer)} CROSS JOIN bounds
                    WHERE {spatial_filter} AND ST_Dimension({geometry_ref}) = 0
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
                    WHERE {spatial_filter}{level_filter}
                    LIMIT %s
                )
            """
            params = ((level, layer.max_features, layer.id) if level_filter else (layer.max_features, layer.id))

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
        relation = self._query_relation(layer, await self._resolve_relation_for(layer, layer_source))
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
                   ST_AsGeoJSON(ST_Intersection(ST_Transform({self._column_ref('t', layer.geometry_column)}, 4326), bounds.web)) AS geometry
            FROM {relation} AS t {self._source_join(layer)} CROSS JOIN bounds
            WHERE {self._column_ref('t', layer.geometry_column)} && {source_bbox}
              AND ST_Intersects({self._column_ref('t', layer.geometry_column)}, bounds.source)
            ORDER BY {self._column_ref('t', layer.id_column)}
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

    async def read_sister_parcels(
        self, bbox: tuple[float, float, float, float], limit: int = 3000
    ) -> dict[str, Any]:
        """Return visible parcel polygons whose cadastral keys occur in SISTER.

        SISTER and the canonical parcel geometry can live in separate databases.
        Keep the map query spatially bounded, then match only the visible parcel
        keys against the read-only SISTER view in aecs4u-stats.
        """
        layer = get_map_layer("cadastral-parcels")
        stats_source = self.connection_source
        if stats_source is None:
            return {"available": False, "type": "FeatureCollection", "features": []}
        parcel_source = await self._layer_source_for(layer)
        if parcel_source is None:
            return {"available": False, "type": "FeatureCollection", "features": []}

        west, south, east, north = (float(value) for value in bbox)
        if (east - west) * (north - south) > layer.geojson_max_area:
            return {
                "available": True,
                "type": "FeatureCollection",
                "features": [],
                "zoom_required": layer.min_zoom,
            }

        relation = self._query_relation(layer, await self._resolve_relation_for(layer, parcel_source))
        web_bbox = f"ST_MakeEnvelope({west!r}, {south!r}, {east!r}, {north!r}, 4326)"
        source_bbox = f"ST_Transform({web_bbox}, {layer.source_srid})"
        if parcel_source is self.cadastral_connection_source:
            # The dedicated cadastral database publishes one normalized union
            # view with SISTER's municipality/province names already attached.
            context_columns = (
                "t.province AS province, t.municipality_name AS municipality_name, "
                "t.municipality_code AS municipality_code"
            )
            context_joins = ""
        else:
            # The aecs4u-stats canonical table stores only the municipality ID;
            # resolve names through its administrative spine below.
            context_columns = (
                "u.canonical_name AS municipality_name, province.canonical_name AS province, "
                "NULL::text AS municipality_code"
            )
            context_joins = """
                LEFT JOIN geo.geo_unit AS u ON u.id = t.municipality_id
                LEFT JOIN geo.geo_relation AS province_relation
                  ON province_relation.child_id = u.id
                 AND province_relation.relation_type = 'contains'
                LEFT JOIN geo.geo_unit AS province
                  ON province.id = province_relation.parent_id
                 AND province.unit_type = 'province'
            """

        candidate_sql = f"""
            WITH bounds AS (
                SELECT ST_MakeEnvelope(%s, %s, %s, %s, 4326) AS web
            )
            SELECT t.id, t.canonical_reference, t.national_cadastral_reference,
                   t.parcel, t.sheet, t.source_release,
                   {context_columns},
                   ST_AsGeoJSON(ST_Intersection(ST_Transform(t.{layer.geometry_column}, 4326), bounds.web)) AS geometry
            FROM {relation} AS t {context_joins} CROSS JOIN bounds
            WHERE t.{layer.geometry_column} && {source_bbox}
              AND ST_Intersects(t.{layer.geometry_column}, {source_bbox})
            ORDER BY t.{layer.id_column}
            LIMIT %s
        """
        async with parcel_source.connection() as connection:
            candidate_rows = await connection.fetch(
                _asyncpg_sql(candidate_sql),
                west, south, east, north,
                layer.max_features + 1,
            )
        candidates_truncated = len(candidate_rows) > layer.max_features
        candidate_rows = candidate_rows[:layer.max_features]

        candidate_context = []
        missing_codes = set()
        for row in candidate_rows:
            values = dict(row)
            reference = values.get("national_cadastral_reference") or values.get("canonical_reference")
            code, section, ref_sheet, ref_parcel = _sister_reference_parts(reference)
            sheet = values.get("sheet") or ref_sheet
            parcel = values.get("parcel") or ref_parcel
            municipality_name = str(values.get("municipality_name") or "").strip()
            province = str(values.get("province") or "").strip()
            if code and (not municipality_name or not province):
                missing_codes.add(code)
            candidate_context.append({
                "row": values,
                "code": code,
                "section": section,
                "sheet": _sister_cadastral_values(sheet, sheet=True),
                "parcel": _sister_cadastral_values(str(parcel).split("/", 1)[0]),
                "municipality": municipality_name.casefold(),
                "province": province.casefold(),
            })

        async with stats_source.connection() as connection:
            sister_relation = await connection.fetchval(
                "SELECT to_regclass('sister.v_sister_property_by_cadastral_parcel') IS NOT NULL"
            )
            if not sister_relation:
                return {
                    "available": False,
                    "type": "FeatureCollection",
                    "features": [],
                    "count": 0,
                    "source": "aecs4u-stats sister.v_sister_property_by_cadastral_parcel",
                }

            municipality_by_code = {}
            if missing_codes:
                try:
                    municipality_rows = await connection.fetch(_asyncpg_sql("""
                        SELECT gi_cad.code AS cadastral_code,
                               u.canonical_name AS municipality_name,
                               province.canonical_name AS province
                        FROM geo.geo_identifier AS gi_cad
                        JOIN geo.geo_unit AS u ON u.id = gi_cad.geo_unit_id
                        LEFT JOIN geo.geo_relation AS province_relation
                          ON province_relation.child_id = u.id
                         AND province_relation.relation_type = 'contains'
                        LEFT JOIN geo.geo_unit AS province
                          ON province.id = province_relation.parent_id
                         AND province.unit_type = 'province'
                        WHERE gi_cad.scheme = 'CATASTALE_COMUNE'
                          AND upper(gi_cad.code) = ANY(%s)
                    """), sorted(missing_codes))
                    municipality_by_code = {
                        str(row["cadastral_code"]).upper(): (
                            str(row["municipality_name"] or "").strip().casefold(),
                            str(row["province"] or "").strip().casefold(),
                        )
                        for row in municipality_rows
                    }
                except Exception as exc:
                    # Dedicated regional rows already carry this context. If
                    # the canonical geo spine is temporarily absent, those
                    # rows can still be matched while other candidates skip.
                    log.debug("SISTER map municipality context unavailable: %s", exc)

            for candidate in candidate_context:
                if not candidate["municipality"] or not candidate["province"]:
                    names = municipality_by_code.get(candidate["code"], ("", ""))
                    candidate["municipality"] = candidate["municipality"] or names[0]
                    candidate["province"] = candidate["province"] or names[1]

            provinces = sorted({item["province"] for item in candidate_context if item["province"]})
            municipalities = sorted({item["municipality"] for item in candidate_context if item["municipality"]})
            sheets = sorted({value for item in candidate_context for value in item["sheet"]})
            parcels = sorted({value for item in candidate_context for value in item["parcel"]})
            sections = sorted({item["section"].upper() for item in candidate_context})
            sister_rows = []
            if provinces and municipalities and sheets and parcels:
                section_exists = await connection.fetchval("""
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema = 'sister'
                          AND table_name = 'v_sister_property_by_cadastral_parcel'
                          AND column_name = 'section'
                    )
                """)
                section_select = "upper(trim(coalesce(section, '')))" if section_exists else "''::text"
                section_filter = (
                    "AND upper(trim(coalesce(section, ''))) = ANY(%s)"
                    if section_exists else ""
                )
                query = f"""
                    SELECT DISTINCT lower(trim(coalesce(province, ''))) AS province,
                           lower(trim(coalesce(municipality, ''))) AS municipality,
                           trim(coalesce(sheet, '')) AS sheet,
                           trim(split_part(coalesce(parcel, ''), '/', 1)) AS parcel,
                           {section_select} AS section
                    FROM sister.v_sister_property_by_cadastral_parcel
                    WHERE lower(trim(coalesce(province, ''))) = ANY(%s)
                      AND lower(trim(coalesce(municipality, ''))) = ANY(%s)
                      AND (trim(coalesce(sheet, '')) = ANY(%s)
                           OR ltrim(trim(coalesce(sheet, '')), '0') = ANY(%s))
                      AND (trim(split_part(coalesce(parcel, ''), '/', 1)) = ANY(%s)
                           OR ltrim(trim(split_part(coalesce(parcel, ''), '/', 1)), '0') = ANY(%s))
                      {section_filter}
                    LIMIT 50001
                """
                params: tuple[Any, ...] = (
                    provinces, municipalities, sheets, sheets, parcels, parcels,
                )
                if section_exists:
                    params += (sections,)
                sister_rows = await connection.fetch(_asyncpg_sql(query), *params)
                sister_rows_truncated = len(sister_rows) > 50000
                sister_rows = sister_rows[:50000]
            else:
                sister_rows_truncated = False

        sister_keys = set()
        for row in sister_rows:
            values = dict(row)
            for sheet_value in _sister_cadastral_values(values.get("sheet"), sheet=True):
                for parcel_value in _sister_cadastral_values(
                    str(values.get("parcel") or "").split("/", 1)[0]
                ):
                    sister_keys.add((
                        str(values.get("province") or "").casefold(),
                        str(values.get("municipality") or "").casefold(),
                        str(values.get("section") or "").upper(),
                        sheet_value,
                        parcel_value,
                    ))

        matches = []
        for candidate in candidate_context:
            key_prefix = (
                candidate["province"], candidate["municipality"], candidate["section"].upper()
            )
            if not candidate["province"] or not candidate["municipality"]:
                continue
            if any(
                (*key_prefix, sheet_value, parcel_value) in sister_keys
                for sheet_value in candidate["sheet"]
                for parcel_value in candidate["parcel"]
            ):
                row = candidate["row"]
                geometry = row.get("geometry")
                properties = {
                    "id": row.get("id"),
                    "canonical_reference": row.get("canonical_reference"),
                    "national_cadastral_reference": row.get("national_cadastral_reference"),
                    "parcel": row.get("parcel"),
                    "sheet": row.get("sheet"),
                    "municipality_name": row.get("municipality_name") or candidate["municipality"],
                    "province": row.get("province") or candidate["province"],
                    "source_release": row.get("source_release"),
                    "sister_listed": True,
                }
                matches.append({
                    "type": "Feature",
                    "id": row.get("id"),
                    "properties": {key: _json_value(value) for key, value in properties.items()},
                    "geometry": json.loads(geometry) if isinstance(geometry, str) else geometry,
                })

        matches.sort(key=lambda feature: str(feature.get("id", "")))
        feature_limit = min(max(int(limit), 1), layer.max_features)
        truncated = candidates_truncated or sister_rows_truncated or len(matches) > feature_limit
        return {
            "available": True,
            "type": "FeatureCollection",
            "features": matches[:feature_limit],
            "count": min(len(matches), feature_limit),
            "truncated": truncated,
            "source": "aecs4u-stats sister.v_sister_property_by_cadastral_parcel",
        }

    async def read_sister_batch_selection(
        self,
        *,
        parcel_ids: Optional[list[int]] = None,
        geometry: Optional[dict[str, Any]] = None,
        limit: int = 5000,
    ) -> dict[str, Any]:
        """Resolve selected parcels into the cadastral fields SISTER needs."""
        layer = get_map_layer("cadastral-parcels")
        layer_source = await self._layer_source_for(layer)
        if layer_source is None:
            return {"type": "FeatureCollection", "features": [], "truncated": False}
        ids = list(dict.fromkeys(int(value) for value in (parcel_ids or [])))
        if not ids and geometry is None:
            return {"type": "FeatureCollection", "features": [], "truncated": False}

        properties = self._properties_for(layer, layer_source)
        columns = self._source_columns(layer, properties)
        relation = self._query_relation(layer, await self._resolve_relation_for(layer, layer_source))
        geometry_ref = self._column_ref("t", layer.geometry_column)
        if layer_source is self.cadastral_connection_source:
            context_columns = (
                "t.province AS province, t.municipality_name AS municipality_name, "
                "t.municipality_code AS municipality_code"
            )
            context_joins = ""
        else:
            context_columns = (
                "u.canonical_name AS municipality_name, province.canonical_name AS province, "
                "NULL::text AS municipality_code"
            )
            context_joins = """
                LEFT JOIN geo.geo_unit AS u ON u.id = t.municipality_id
                LEFT JOIN geo.geo_relation AS province_relation
                  ON province_relation.child_id = u.id
                 AND province_relation.relation_type = 'contains'
                LEFT JOIN geo.geo_unit AS province
                  ON province.id = province_relation.parent_id
                 AND province.unit_type = 'province'
            """

        if geometry is not None:
            ring = geometry["coordinates"][0]
            longitudes = [float(point[0]) for point in ring]
            latitudes = [float(point[1]) for point in ring]
            west, south, east, north = min(longitudes), min(latitudes), max(longitudes), max(latitudes)
            source_bbox = (
                f"ST_Transform(ST_MakeEnvelope({west!r}, {south!r}, {east!r}, {north!r}, 4326), "
                f"{layer.source_srid})"
            )
            selection_geometry = (
                f"ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326), {layer.source_srid})"
            )
            selection_sql = ""
            predicate = f"{geometry_ref} && {source_bbox} AND ST_Intersects({geometry_ref}, {selection_geometry})"
            from_sql = f"{relation} AS t {context_joins}"
            params: tuple[Any, ...] = (json.dumps(geometry, separators=(",", ":")),)
        else:
            selection_sql = ""
            predicate = f"{self._column_ref('t', layer.id_column)} = ANY(%s)"
            from_sql = f"{relation} AS t {context_joins}"
            params = (ids,)

        sql = f"""
            {selection_sql}
            SELECT {columns}, {context_columns},
                   ST_AsGeoJSON(ST_Transform({geometry_ref}, 4326)) AS geometry
            FROM {from_sql}
            WHERE {predicate}
            ORDER BY {self._column_ref('t', layer.id_column)}
            LIMIT %s
        """
        feature_limit = min(max(int(limit), 1), 5000)
        async with layer_source.connection() as connection:
            rows = await connection.fetch(_asyncpg_sql(sql), *params, feature_limit + 1)
        truncated = len(rows) > feature_limit
        rows = rows[:feature_limit]
        features = []
        for row in rows:
            values = dict(row)
            raw_geometry = values.pop("geometry", None)
            feature_properties = {key: _json_value(values.get(key)) for key in properties}
            for key in ("province", "municipality_name", "municipality_code"):
                if values.get(key) is not None:
                    feature_properties[key] = _json_value(values[key])
            features.append({
                "type": "Feature",
                "id": values.get(layer.id_column),
                "properties": feature_properties,
                "geometry": json.loads(raw_geometry) if raw_geometry else None,
            })
        return {"type": "FeatureCollection", "features": features, "truncated": truncated}

    @staticmethod
    def visura_candidate_codes(features: list[dict[str, Any]]) -> list[str]:
        """Distinct cadastral municipality codes of parcel features (for name resolution)."""
        codes = set()
        for feature in features:
            properties = feature.get("properties") or {}
            reference = properties.get("national_cadastral_reference") or properties.get("canonical_reference")
            code = str(properties.get("municipality_code") or _sister_reference_parts(reference)[0] or "").upper()
            if code:
                codes.add(code)
        return sorted(codes)

    async def read_visura_flags(
        self,
        features: list[dict[str, Any]],
        names_by_code: dict[str, tuple[str, tuple[str, ...]]],
    ) -> Optional[dict[Any, bool]]:
        """Map each cadastral parcel feature id to whether SISTER holds a property visura for it.

        Only visure per immobile count (``visura_fabbricati``, ``visura_terreni`` and the
        untyped ``visura``); subject visure and plans do not describe the parcel itself.
        ``names_by_code`` maps a cadastral municipality code to ``(municipality, province
        spellings)``. The regional parcel tables carry the province as a code (``PA``)
        and the SISTER views are not consistent (documents use the code, properties the
        name), so parcels are matched on the municipality, any accepted province spelling,
        section, sheet and parcel. Parcels whose municipality has no names are left out
        of the result. Returns ``None`` when SISTER cannot be consulted so callers can
        tell "unknown" from "no visura".
        """
        stats_source = self.connection_source
        if stats_source is None or not features:
            return None

        resolved = {
            code.upper(): (
                municipality.strip().casefold(),
                frozenset(value.strip().casefold() for value in provinces if value and value.strip()),
            )
            for code, (municipality, provinces) in names_by_code.items()
            if municipality and any(value and value.strip() for value in provinces)
        }
        candidates = []
        for feature in features:
            properties = feature.get("properties") or {}
            reference = properties.get("national_cadastral_reference") or properties.get("canonical_reference")
            code, section, ref_sheet, ref_parcel = _sister_reference_parts(reference)
            code = str(properties.get("municipality_code") or code or "").upper()
            if code not in resolved:
                continue
            candidates.append({
                "id": feature.get("id", properties.get("id")),
                "municipality": resolved[code][0],
                "provinces": resolved[code][1],
                "section": section.upper(),
                "sheet": _sister_cadastral_values(properties.get("sheet") or ref_sheet, sheet=True),
                "parcel": _sister_cadastral_values(str(properties.get("parcel") or ref_parcel).split("/", 1)[0]),
            })
        if not candidates:
            return None

        provinces = sorted({value for item in candidates for value in item["provinces"]})
        municipalities = sorted({item["municipality"] for item in candidates})
        sheets = sorted({value for item in candidates for value in item["sheet"]})
        parcels = sorted({value for item in candidates for value in item["parcel"]})
        async with stats_source.connection() as connection:
            if not await connection.fetchval(
                "SELECT to_regclass('sister.v_sister_document_by_cadastral_parcel') IS NOT NULL"
            ):
                return None
            document_rows = await connection.fetch(_asyncpg_sql("""
                SELECT DISTINCT lower(trim(coalesce(province, ''))) AS province,
                       lower(trim(coalesce(municipality, ''))) AS municipality,
                       upper(trim(coalesce(section, ''))) AS section,
                       trim(coalesce(sheet, '')) AS sheet,
                       trim(split_part(coalesce(parcel, ''), '/', 1)) AS parcel
                FROM sister.v_sister_document_by_cadastral_parcel
                WHERE document_type = ANY(%s)
                  AND lower(trim(coalesce(province, ''))) = ANY(%s)
                  AND lower(trim(coalesce(municipality, ''))) = ANY(%s)
                  AND (trim(coalesce(sheet, '')) = ANY(%s)
                       OR ltrim(trim(coalesce(sheet, '')), '0') = ANY(%s))
                  AND (trim(split_part(coalesce(parcel, ''), '/', 1)) = ANY(%s)
                       OR ltrim(trim(split_part(coalesce(parcel, ''), '/', 1)), '0') = ANY(%s))
            """), list(_VISURA_DOCUMENT_TYPES), provinces, municipalities, sheets, sheets, parcels, parcels)

        keys = set()
        for row in document_rows:
            for sheet_value in _sister_cadastral_values(row["sheet"], sheet=True):
                for parcel_value in _sister_cadastral_values(row["parcel"]):
                    keys.add((row["province"], row["municipality"], row["section"], sheet_value, parcel_value))

        return {
            item["id"]: any(
                (province, item["municipality"], item["section"], sheet_value, parcel_value) in keys
                for province in item["provinces"]
                for sheet_value in item["sheet"]
                for parcel_value in item["parcel"]
            )
            for item in candidates
        }

    async def read_feature_at_point(self, layer_id: str, lat: float, lng: float) -> Optional[dict[str, Any]]:
        """Return the polygon containing a WGS84 point as a GeoJSON feature."""

        layer = get_map_layer(layer_id)
        layer_source = await self._layer_source_for(layer)
        if layer_source is None:
            return None
        columns = self._source_columns(layer, self._properties_for(layer, layer_source))
        relation = self._query_relation(layer, await self._resolve_relation_for(layer, layer_source))
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
                   ST_AsGeoJSON(ST_Transform({self._column_ref('t', layer.geometry_column)}, 4326)) AS geometry
            FROM {relation} AS t {self._source_join(layer)} CROSS JOIN click
            WHERE {self._column_ref('t', layer.geometry_column)} && {source_point_filter}
              AND ST_Covers({self._column_ref('t', layer.geometry_column)}, click.source)
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
        self, layer_id: str, reference: str, limit: int = 25, feature_id: Optional[int] = None,
        method: str = "intersects",
    ) -> list[dict[str, Any]]:
        """Return parcels adjacent to the parcel identified by ``reference``."""

        layer = get_map_layer(layer_id)
        layer_source = await self._layer_source_for(layer)
        if layer_source is None:
            return []
        if layer.id != "cadastral-parcels":
            raise ValueError("Adjacency lookup is only supported for cadastral parcels")
        predicates = {
            "touches": "ST_Touches",
            "intersects": "ST_Intersects",
            "overlaps": "ST_Overlaps",
        }
        predicate = predicates.get(method)
        if predicate is None:
            raise ValueError("Unsupported adjacency method")
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
              AND {predicate}(t.{layer.geometry_column}, target.geom)
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
        relation = await self._resolve_relation_for(layer, layer_source)
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
        """Search the canonical ISTAT municipality map relation.

        This is intentionally separate from ``read_geojson``: place search
        must remain useful before the map reaches parcel zoom, and it should
        return compact centroids rather than geometry for whole areas.
        """
        if not self.connection_source:
            return []
        normalized = str(query or "").strip()
        if len(normalized) < 2:
            return []
        sql = f"""
            SELECT (3000000000 + t.pro_com)::bigint AS id,
                   t.pro_com::bigint AS geo_unit_id,
                   t.name AS canonical_name,
                   t.pro_com::text AS istat_code,
                   ST_Y(ST_Centroid(t.geom)) AS latitude,
                   ST_X(ST_Centroid(t.geom)) AS longitude,
                   ST_XMin(Box2D(ST_Transform(t.geom, 4326))) AS west,
                   ST_YMin(Box2D(ST_Transform(t.geom, 4326))) AS south,
                   ST_XMax(Box2D(ST_Transform(t.geom, 4326))) AS east,
                   ST_YMax(Box2D(ST_Transform(t.geom, 4326))) AS north
            FROM istat.v_comuni_map AS t
            WHERE t.name ILIKE %s
               OR t.pro_com::text ILIKE %s
            ORDER BY CASE
                WHEN lower(t.name) = lower(%s) THEN 0
                WHEN lower(t.name) LIKE lower(%s) THEN 1
                ELSE 2
            END, t.name ASC
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
