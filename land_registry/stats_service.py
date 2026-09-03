"""
Adapter over the ``aecs4u-stats`` package: Italian public reference data
(ISTAT municipalities/population, OSM POIs, OMI real-estate quotes, MEF/IRPEF
income, natural hazards) consumed by the parcel-enrichment endpoints.

All lookups degrade gracefully: when the underlying data stores have not been
built on this host (see ``ISTAT_DATA_DIR``, default ``~/.aecs4u_stats/istat``),
functions return ``None``/empty results instead of raising, so the map keeps
working without the enrichment layer.

Data stores are built with the aecs4u-stats pipeline, e.g.:
    python -m aecs4u_stats.istat.scripts.import_data
    python -m aecs4u_stats.osm.scripts.download_pois
    python -m aecs4u_stats.omi.scripts.import_omi --data-dir /path/to/OMI
    python -m aecs4u_stats.mef.scripts.import_irpef --year 2022
    python -m aecs4u_stats.hazards.scripts.import_seismic --file classificazione.xlsx

The IdroGEO (flood/landslide), FIRMS (active fires) and DPC bulletin datasets
are runtime API clients (no local store) — see ``get_environmental_risks``,
``get_active_fires`` and ``get_criticality_bulletin`` below.
"""

import logging
import hashlib
import json
import math
import os
import re
import threading
import time
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from dotenv import load_dotenv

    # ``main.py`` imports the enrichment router before ``land_registry.config``
    # loads .env, so this module must make its own DSN lookup deterministic.
    load_dotenv(override=False)
except ImportError:  # pragma: no cover - dotenv is an application dependency
    pass

from aecs4u_stats.cadastral import (
    cadastral_db_available,
    fogli_for_comune,
    parcel_at_point,
    parcel_by_reference,
    parcels_for_comune,
    parcels_in_bbox,
)
try:
    from aecs4u_stats.census import census_db_available as _census_db_available
    from aecs4u_stats.census import section_at_point as _census_section_at_point
    from aecs4u_stats.census import sections_for_comune as _census_sections_for_comune
except ImportError:
    # Census sections are an optional aecs4u-stats dataset. Keep the adapter
    # importable with older package releases that predate this subpackage.
    def _census_db_available() -> bool:
        return False

    def _census_section_at_point(*args, **kwargs):
        return None

    def _census_sections_for_comune(*args, **kwargs):
        return None

try:
    from aecs4u_stats.census.config import CENSUS_STORE_PATH as _CENSUS_STORE_PATH
except ImportError:
    _CENSUS_STORE_PATH = None
from aecs4u_stats.hazards import (
    active_fires as _active_fires,
    get_comune_hazards,
    latest_criticality_bulletin,
    seismic_db_available,
    seismic_zone,
    summarize_hazards,
)
from aecs4u_stats.istat.config import ISTAT_SQLITE_PATH
from aecs4u_stats.mef import income_by_cadastral_code, mef_db_available
from aecs4u_stats.omi import (
    OMI_DB_PATH,
    OMI_ZONES_DIR,
    omi_db_available,
    quote_history,
    quotes_for_comune,
    zone_boundaries,
    zone_boundaries_available as _zone_boundaries_available,
)
from aecs4u_stats.osm.config import POI_CATEGORIES
from aecs4u_stats.osm.pois import pois_within_radius, resolve_poi_db
from shapely.geometry import Point, shape

logger = logging.getLogger(__name__)


_PARCEL_DETAIL_BLOCKS = (
    "basic", "cadastral", "address", "addresses", "risk", "subsidence", "terrain",
    "population", "buildings", "economics", "demographics", "land_cover",
    "land_use", "valuation", "valuation_history", "coastal_erosion",
    "cultural_heritage", "solar", "poi", "nightlights",
)


def _detail_block(
    data: Any = None,
    *,
    source: Optional[str] = None,
    match_method: Optional[str] = None,
    available: Optional[bool] = None,
) -> Dict[str, Any]:
    """Return the common block envelope described by the parcel reference."""
    is_available = bool(data) if available is None else bool(available)
    return {
        "available": is_available,
        "data": data if is_available else None,
        "source": source,
        "data_vintage": None,
        "updated_at": None,
        "match_method": match_method,
        "match_distance_m": None,
        "coverage_status": "full" if is_available else "not_available",
        "confidence": None,
        "license": None,
    }


def _call_with_hard_timeout(func, timeout, *args, **kwargs):
    """Run ``func`` in a worker thread with a wall-clock timeout that a
    blocking C call can't defeat by ignoring its own timeout parameter.

    Observed on this host: psycopg2 connection setup can hang well past
    ``connect_timeout``/``statement_timeout`` (even a raw ``socket.connect``
    with an explicit Python-level timeout can hang) once ``pydantic_settings``
    has been imported in the process — environment-specific, cause unclear,
    but it made every Postgres-backed enrichment path capable of hanging a
    request indefinitely. Raises ``TimeoutError`` if ``func`` hasn't returned
    in time; the worker thread (daemon) is then abandoned rather than joined,
    since Python cannot forcibly cancel a blocking C call.
    """
    box: list = []

    def _target():
        try:
            box.append(("ok", func(*args, **kwargs)))
        except Exception as exc:  # noqa: BLE001 - re-raised on the caller's thread
            box.append(("error", exc))

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise TimeoutError(f"{getattr(func, '__qualname__', func)} did not return within {timeout}s")
    status, payload = box[0]
    if status == "error":
        raise payload
    return payload


class _PostgresPoiSource:
    """Read-only adapter over the canonical aecs4u-stats PostGIS ``facts.poi``
    table — the migrated replacement for the local OSM POI SQLite store.

    Mirrors ``PostgresCadastralBoundarySource`` in datashader_service.py:
    same DSN env-var chain, same lazy connection pool, same "unavailable is
    not an error" degrade-to-fallback behaviour.
    """

    def __init__(self, dsn: str, max_connections: int = 4):
        dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
        if "connect_timeout=" not in dsn:
            separator = "&" if "?" in dsn else "?"
            dsn = f"{dsn}{separator}connect_timeout=3"
        self.dsn = dsn
        self.max_connections = max_connections
        self._pool = None
        self._pool_lock = threading.Lock()
        self._retry_at = 0.0

    @classmethod
    def from_environment(cls):
        if not _postgres_stats_enabled():
            return None
        dsn = (
            os.getenv("AECS4U_STATS_POSTGRES_DSN")
            or os.getenv("AECS4U_STATS_DATABASE_URL")
            or os.getenv("AECS4U_STATS_SPATIAL_DATABASE_URL")
        )
        if not dsn or not dsn.startswith(("postgres://", "postgresql://", "postgresql+")):
            return None
        try:
            return cls(dsn)
        except Exception as exc:
            logger.warning("Postgres POI source unavailable: %s", exc)
            return None

    def available(self) -> bool:
        return time.monotonic() >= self._retry_at

    def _get_pool(self):
        if self._pool is None:
            with self._pool_lock:
                if self._pool is None:
                    import psycopg2.pool

                    self._pool = _call_with_hard_timeout(
                        psycopg2.pool.ThreadedConnectionPool, 5, 1, self.max_connections, self.dsn
                    )
        return self._pool

    @contextmanager
    def _connection(self):
        pool = self._get_pool()
        connection = pool.getconn()
        try:
            # facts.poi's GiST index triggers a PostGIS JIT bitcode-version
            # crash on this server unless JIT is disabled per-connection.
            with connection.cursor() as cursor:
                cursor.execute("SET jit = off")
            yield connection
        except Exception:
            connection.rollback()
            raise
        finally:
            pool.putconn(connection, close=bool(getattr(connection, "closed", 0)))

    def pois_near(
        self, lat: float, lng: float, radius_km: float = 1.0, categories: Optional[List[str]] = None
    ) -> Dict[str, List[dict]]:
        sql = """
            SELECT c.code, p.name, ST_Y(p.geom), ST_X(p.geom),
                   ST_Distance(
                       p.geom::geography,
                       ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography
                   ) / 1000.0 AS distance_km
            FROM facts.poi p
            JOIN facts.poi_category c ON c.id = p.category_id
            WHERE ST_DWithin(
                p.geom::geography,
                ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
                %s
            )
        """
        params: list = [lng, lat, lng, lat, radius_km * 1000.0]
        if categories:
            placeholders = ",".join(["%s"] * len(categories))
            sql += f" AND c.code IN ({placeholders})"
            params.extend(categories)
        sql += " ORDER BY distance_km"

        grouped: Dict[str, List[dict]] = {}
        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                for code, name, poi_lat, poi_lng, distance_km in cursor.fetchall():
                    grouped.setdefault(code, []).append(
                        {
                            "lat": poi_lat,
                            "lng": poi_lng,
                            "name": name,
                            "distance_km": round(float(distance_km), 3),
                        }
                    )
        return grouped


def _postgres_stats_enabled() -> bool:
    """Return whether the configured aecs4u-stats Postgres source is enabled.

    Deliberately opt-in, DSN-presence alone is NOT enough: a blocking
    psycopg2 call has been reproduced on this host freezing the *entire*
    process — even the unrelated ``/health`` endpoint stops responding while
    it's stuck — and neither connect_timeout/statement_timeout, a
    thread-join timeout, nor ``asyncio.wait_for`` can recover from it
    (consistent with the call never releasing the GIL). Defaulting to
    "DSN configured -> enabled" silently reintroduces that freeze on every
    request the moment ``AECS4U_STATS_POSTGRES_DSN`` is set, e.g. via
    ``.env``. Require an explicit ``AECS4U_STATS_POSTGRES_ENABLE=1`` until
    that's diagnosed/fixed, or Postgres access is moved behind real process
    isolation (a subprocess that can be SIGKILLed).
    """
    return os.getenv("AECS4U_STATS_POSTGRES_ENABLE", "").strip().lower() in ("1", "true", "yes", "on")


def _postgres_stats_dsn() -> Optional[str]:
    """Return the explicitly configured aecs4u-stats PostgreSQL DSN.

    Do not fall back to the application's main ``DATABASE_URL`` here: that
    database contains land-registry application data, not the canonical
    aecs4u-stats datasets.
    """
    if not _postgres_stats_enabled():
        return None
    dsn = os.getenv("AECS4U_STATS_POSTGRES_DSN")
    if not dsn or not dsn.startswith(("postgres://", "postgresql://", "postgresql+")):
        return None
    return dsn


def _postgres_scalar(value: Any) -> Any:
    """Convert psycopg2 values into JSON-safe API values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "item"):
        try:
            return value.item()
        except (AttributeError, ValueError):
            pass
    if hasattr(value, "isoformat"):
        return value.isoformat()
    try:
        from decimal import Decimal

        if isinstance(value, Decimal):
            return float(value)
    except ImportError:  # pragma: no cover
        pass
    return str(value)


def _census_ratios(properties: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """Derive the small, stable indicators used by the parcel panel."""
    def ratio(numerator: Any, denominator: Any) -> Optional[float]:
        try:
            denominator = float(denominator)
            if denominator == 0:
                return None
            return round(float(numerator) / denominator, 4)
        except (TypeError, ValueError):
            return None

    working_age = sum(float(properties.get(name) or 0) for name in (
        "p17", "p18", "p19", "p20", "p21", "p22", "p23", "p24", "p25", "p26",
    ))
    return {
        "education_tertiary_rate": ratio(properties.get("p90"), properties.get("p83")),
        "employment_rate_working_age": ratio(properties.get("p101"), working_age),
        "foreign_resident_share": ratio(properties.get("st1"), properties.get("p1")),
        "vacancy_rate": ratio(properties.get("a3"), properties.get("a8")),
        "avg_household_size": ratio(properties.get("p1"), properties.get("pf1")),
    }


class _PostgresStatsSource(_PostgresPoiSource):
    """Read-only parcel context adapter over the local aecs4u-stats PostGIS DB.

    The current database has the context ingredients but its canonical parcel
    spine is not populated.  This adapter therefore reports both the context
    rows and ``parcel_spine_available`` instead of manufacturing a parcel join.
    """

    @classmethod
    def from_environment(cls):
        dsn = _postgres_stats_dsn()
        if not dsn:
            return None
        try:
            return cls(dsn)
        except Exception as exc:
            logger.warning("Postgres stats source unavailable: %s", exc)
            return None

    def __init__(self, dsn: str, max_connections: int = 4):
        super().__init__(dsn, max_connections=max_connections)
        self._read_model_ready = False
        self._read_model_lock = threading.Lock()

    def _ensure_read_model(self) -> None:
        """Create the PostgreSQL parcel-keyed read model if needed.

        This is a narrow application-facing cache, separate from the source
        facts. It is intentionally keyed by the canonical parcel reference so
        the details panel performs one indexed lookup after the cold build.
        """
        if self._read_model_ready:
            return
        with self._read_model_lock:
            if self._read_model_ready:
                return
            with self._connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        CREATE TABLE IF NOT EXISTS serving.parcel_enrichment_read_model (
                            parcel_key TEXT PRIMARY KEY,
                            payload JSONB NOT NULL,
                            source_fingerprint TEXT,
                            refreshed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                        )
                        """
                    )
                    cursor.execute(
                        """
                        CREATE INDEX IF NOT EXISTS parcel_enrichment_read_model_refreshed_idx
                        ON serving.parcel_enrichment_read_model (refreshed_at)
                        """
                    )
                connection.commit()
            self._read_model_ready = True

    def get_read_model(self, parcel_key: str) -> Optional[Dict[str, Any]]:
        """Return one PostgreSQL materialized read-model payload."""
        self._ensure_read_model()
        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT parcel_key, payload, source_fingerprint, refreshed_at
                    FROM serving.parcel_enrichment_read_model
                    WHERE parcel_key = %s
                    """,
                    (parcel_key,),
                )
                row = cursor.fetchone()
        if not row:
            return None
        payload = row[1]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (TypeError, ValueError, json.JSONDecodeError):
                logger.warning("Invalid PostgreSQL parcel enrichment payload for %s", parcel_key)
                return None
        if not isinstance(payload, dict):
            return None
        payload["read_model"] = {
            "key": row[0],
            "source_fingerprint": row[2],
            "refreshed_at": _postgres_scalar(row[3]),
            "cached": True,
            "database": "aecs4u-stats PostgreSQL",
        }
        return payload

    def upsert_read_model(
        self,
        parcel_key: str,
        payload: Dict[str, Any],
        source_fingerprint: Optional[str],
    ) -> None:
        """Atomically replace one PostgreSQL parcel read-model row."""
        self._ensure_read_model()
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO serving.parcel_enrichment_read_model
                        (parcel_key, payload, source_fingerprint, refreshed_at, updated_at)
                    VALUES (%s, %s::jsonb, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT (parcel_key) DO UPDATE SET
                        payload = EXCLUDED.payload,
                        source_fingerprint = EXCLUDED.source_fingerprint,
                        refreshed_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (parcel_key, serialized, source_fingerprint),
                )
            connection.commit()

    def context_for_parcel(
        self,
        national_reference: str,
        cadastral_code: str,
        point: Optional[Dict[str, float]],
    ) -> Optional[Dict[str, Any]]:
        """Load municipality, census, OMI, postal, and tax context."""
        if not point:
            return None
        try:
            with self._connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SET statement_timeout = '20s'")
                    cursor.execute(
                        """
                        SELECT u.id, u.canonical_name, u.unit_type,
                               gi_istat.code AS istat_code,
                               gi_cad.code AS cadastral_code,
                               mp.observation_count, mp.tax_fact_count,
                               mp.total_imponibile, mp.market_zone_count,
                               mp.pv_n_buildings, mp.pv_pvout_pessimistic_kwh_year_total,
                               mp.pv_pvout_modern_kwh_year_total, mp.pv_pvout_per_capita_kwh,
                               mp.pv_kwp_max_total, mp.pv_high_viability_pct,
                               mp.pv_medium_viability_pct, mp.pv_low_viability_pct,
                               mp.pv_not_eligible_pct, mp.pv_observation_count
                        FROM geo.geo_identifier gi_cad
                        JOIN geo.geo_unit u ON u.id = gi_cad.geo_unit_id
                        LEFT JOIN geo.geo_identifier gi_istat
                          ON gi_istat.geo_unit_id = u.id
                         AND gi_istat.scheme = 'ISTAT_COMUNE'
                        LEFT JOIN serving.municipality_profile mp ON mp.geo_unit_id = u.id
                        WHERE gi_cad.scheme = 'CATASTALE_COMUNE'
                          AND UPPER(gi_cad.code) = UPPER(%s)
                        LIMIT 1
                        """,
                        (cadastral_code,),
                    )
                    municipality_row = cursor.fetchone()
                    if municipality_row is None:
                        return None
                    municipality_columns = [column.name for column in cursor.description]
                    municipality_values = dict(zip(municipality_columns, municipality_row, strict=True))

                    municipality_id = municipality_values["id"]
                    point_sql = "ST_SetSRID(ST_MakePoint(%s, %s), 4326)"

                    cursor.execute(
                        """
                        SELECT id, canonical_reference, source_release, dataset_release_id
                        FROM spatial.cadastral_parcel
                        WHERE canonical_reference = %s
                        LIMIT 1
                        """,
                        (national_reference,),
                    )
                    parcel_row = cursor.fetchone()
                    parcel_columns = [column.name for column in cursor.description]

                    cursor.execute(
                        f"""
                        SELECT mz.id, mz.omi_zone_key, mz.valid_from, mz.valid_to,
                               mz.source_release, mz.dataset_release_id,
                               mzs.quote_count, mzs.latest_period
                        FROM spatial.market_zone mz
                        LEFT JOIN serving.market_zone_snapshot mzs ON mzs.id = mz.id
                        WHERE mz.municipality_id = %s
                          AND ST_Covers(mz.geom, {point_sql})
                        LIMIT 1
                        """,
                        (municipality_id, point["lng"], point["lat"]),
                    )
                    omi_row = cursor.fetchone()
                    omi_columns = [column.name for column in cursor.description]

                    cursor.execute(
                        f"""
                        SELECT pz.cap
                        FROM spatial.postal_zone pz
                        WHERE pz.municipality_id = %s
                          AND ST_Covers(pz.geom, {point_sql})
                        LIMIT 1
                        """,
                        (municipality_id, point["lng"], point["lat"]),
                    )
                    postal_row = cursor.fetchone()

                    cursor.execute(
                        """
                        SELECT year, measure, frequency, amount
                        FROM facts.tax_fact
                        WHERE municipality_id = %s
                        ORDER BY year DESC, measure
                        """,
                        (municipality_id,),
                    )
                    tax_rows = cursor.fetchall()

                    # Census geometry is stored in UTM 32N for this release;
                    # the fixed CRS keeps the GiST candidate filter usable.
                    cursor.execute(
                        f"""
                        SELECT s.*
                        FROM census_sections.sections s
                        WHERE s.procom = %s
                          AND s.geom && ST_Transform({point_sql}, 32632)
                          AND ST_Covers(
                                s.geom,
                                ST_Transform({point_sql}, 32632)
                              )
                        LIMIT 1
                        """,
                        (
                            municipality_values["istat_code"],
                            point["lng"], point["lat"],
                            point["lng"], point["lat"],
                        ),
                    )
                    census_row = cursor.fetchone()
                    census_columns = [column.name for column in cursor.description]

            municipality = {
                key: _postgres_scalar(value)
                for key, value in municipality_values.items()
                if key != "id"
            }
            profile_keys = (
                "observation_count", "tax_fact_count", "total_imponibile",
                "market_zone_count", "pv_n_buildings",
                "pv_pvout_pessimistic_kwh_year_total", "pv_pvout_modern_kwh_year_total",
                "pv_pvout_per_capita_kwh", "pv_kwp_max_total", "pv_high_viability_pct",
                "pv_medium_viability_pct", "pv_low_viability_pct", "pv_not_eligible_pct",
                "pv_observation_count",
            )
            profile = {key: municipality.pop(key, None) for key in profile_keys}
            omi = None
            if omi_row:
                omi = {
                    key: _postgres_scalar(value)
                    for key, value in zip(omi_columns, omi_row, strict=True)
                }
            census = None
            if census_row:
                properties = {
                    key: _postgres_scalar(value)
                    for key, value in zip(census_columns, census_row, strict=True)
                    if key != "geom"
                }
                properties["ratios"] = _census_ratios(properties)
                census = {"type": "Feature", "properties": properties, "geometry": None}
            tax = [
                {
                    key: _postgres_scalar(value)
                    for key, value in zip(("year", "measure", "frequency", "amount"), row, strict=True)
                }
                for row in tax_rows
            ]
            return {
                "parcel_spine_available": parcel_row is not None,
                "parcel_spine": {
                    key: _postgres_scalar(value)
                    for key, value in zip(parcel_columns, parcel_row, strict=True)
                } if parcel_row else None,
                "parcel_spine_reference": national_reference,
                "municipality": municipality,
                "municipality_profile": profile,
                "omi": omi,
                "postal_code": _postgres_scalar(postal_row[0]) if postal_row else None,
                "tax_facts": tax,
                "census": census,
                "source": "aecs4u-stats PostgreSQL",
            }
        except Exception as exc:
            logger.warning("Postgres parcel context lookup failed for %s: %s", cadastral_code, exc)
            return None


_postgres_poi_source: "_PostgresPoiSource | None" = None
_postgres_poi_source_loaded = False
_postgres_stats_source: "_PostgresStatsSource | None" = None
_postgres_stats_source_loaded = False


def _get_postgres_poi_source() -> "_PostgresPoiSource | None":
    global _postgres_poi_source, _postgres_poi_source_loaded
    if not _postgres_poi_source_loaded:
        _postgres_poi_source = _PostgresPoiSource.from_environment()
        _postgres_poi_source_loaded = True
    return _postgres_poi_source


def _get_postgres_stats_source() -> "_PostgresStatsSource | None":
    global _postgres_stats_source, _postgres_stats_source_loaded
    if not _postgres_stats_source_loaded:
        _postgres_stats_source = _PostgresStatsSource.from_environment()
        _postgres_stats_source_loaded = True
    return _postgres_stats_source


def istat_db_available() -> bool:
    """True when the ISTAT reference SQLite store exists on this host."""
    path = _istat_sqlite_path()
    return path.exists() and path.stat().st_size > 0


def _istat_sqlite_path() -> Path:
    """Resolve the installed ISTAT municipality store.

    ``aecs4u-stats`` currently calls this file ``eurostat.IT.sqlite`` while
    the shared provisioned data volume used by the applications still ships
    the same schema as ``istat.sqlite``.  Prefer an explicitly configured
    directory, then the shared volume, and finally the upstream default.
    Keeping this resolution in the consumer avoids requiring a symlink or a
    data-file rename on deployments that mount the legacy store read-only.
    """
    configured_dir = os.getenv("ISTAT_DATA_DIR")
    candidates = []
    if configured_dir:
        base = Path(configured_dir).expanduser()
        candidates.extend((base / "eurostat.IT.sqlite", base / "istat.sqlite"))

    shared_dir = Path("/data/istat")
    candidates.extend((shared_dir / "eurostat.IT.sqlite", shared_dir / "istat.sqlite"))
    candidates.append(Path(ISTAT_SQLITE_PATH))

    for candidate in candidates:
        try:
            if candidate.exists() and candidate.stat().st_size > 0:
                return candidate
        except OSError:
            continue
    return Path(ISTAT_SQLITE_PATH)


def _census_store_path() -> Optional[Path]:
    """Resolve the census store, including the shared volume fallback."""
    configured_dir = os.getenv("ISTAT_DATA_DIR")
    candidates = []
    if _CENSUS_STORE_PATH is not None:
        candidates.append(Path(_CENSUS_STORE_PATH))
    if configured_dir:
        candidates.append(Path(configured_dir).expanduser() / "census_sections.IT.duckdb")
    candidates.append(Path("/data/istat/census_sections.IT.duckdb"))

    for candidate in candidates:
        try:
            if candidate.exists() and candidate.stat().st_size > 0:
                return candidate
        except OSError:
            continue
    return candidates[0] if candidates else None


def poi_db_available() -> bool:
    """True when POIs are servable: the aecs4u-stats PostGIS store when
    configured, otherwise the local OSM POI SQLite store."""
    source = _get_postgres_poi_source()
    if source is not None and source.available():
        return True
    return resolve_poi_db() is not None


def omi_db_available_public() -> bool:
    """True when the OMI store exists on this host."""
    return omi_db_available(_omi_sqlite_path())


def _omi_sqlite_path() -> Path:
    """Resolve OMI data from the configured or shared aecs4u-stats volume."""
    configured_dir = os.getenv("ISTAT_DATA_DIR")
    candidates = []
    if configured_dir:
        candidates.append(Path(configured_dir).expanduser() / "omi.IT.sqlite")
    candidates.append(Path("/data/istat/omi.IT.sqlite"))
    candidates.append(Path(OMI_DB_PATH))
    for candidate in candidates:
        try:
            if candidate.exists() and candidate.stat().st_size > 0:
                return candidate
        except OSError:
            continue
    return Path(OMI_DB_PATH)


def _omi_zones_dir() -> Path:
    """Resolve the optional OMI boundary mirror beside the shared data store."""
    configured_dir = os.getenv("OMI_ZONES_DIR")
    candidates = []
    if configured_dir:
        candidates.append(Path(configured_dir).expanduser())
    candidates.append(Path("/data/istat/omi_zones"))
    candidates.append(Path(OMI_ZONES_DIR))
    for candidate in candidates:
        try:
            if candidate.exists():
                return candidate
        except OSError:
            continue
    return Path(OMI_ZONES_DIR)


def zone_boundaries_available(province: Optional[str] = None) -> bool:
    """Return whether OMI zone boundaries exist, nationally or by province."""
    if province is not None:
        return _zone_boundaries_available(province, zones_dir=_omi_zones_dir())
    zones_dir = _omi_zones_dir()
    return zones_dir.exists() and any(zones_dir.glob("*.geojson"))


def mef_db_available_public() -> bool:
    """True when the MEF/IRPEF store exists on this host."""
    return mef_db_available()


def enrichment_status() -> Dict[str, Any]:
    """Availability report for every aecs4u-stats dataset we consume."""
    status = {
        "istat_municipalities": {
            "available": istat_db_available(),
            "path": str(_istat_sqlite_path()),
        },
        "cadastral_parcels": {
            "available": cadastral_db_available(),
            "note": "per-region DuckDB stores; build with python -m aecs4u_stats.cadastral.scripts.import_cadastral",
        },
        "census_sections": {
            "available": census_db_available() or _get_postgres_stats_source() is not None,
            "note": "2021 ISTAT census sections; build with python -m aecs4u_stats.census.scripts.import_census_sections --all",
        },
        "aecs4u_stats_postgres": {
            "available": _get_postgres_stats_source() is not None,
            "source": "aecs4u-stats PostgreSQL",
            "note": "Canonical context database; cadastral parcel spine is required for full parcel joins",
        },
        "istat_safety": {
            "available": safety_db_available(),
            "note": "reported-crime rates (delitti denunciati), province level",
        },
        "istat_bes": {
            "available": bes_db_available(),
            "note": "BES territorial quality-of-life indicators, province level",
        },
        "istat_demographic": {
            "available": demographic_db_available(),
            "note": "ISTAT demographic indicators, province level",
        },
        "osm_pois": {
            "available": poi_db_available(),
            "categories": sorted(POI_CATEGORIES),
        },
        "omi_quotes": {
            "available": omi_db_available_public(),
        },
        "omi_zone_boundaries": {
            "available": zone_boundaries_available(),
            "path": str(_omi_zones_dir()),
        },
        "mef_irpef": {
            "available": mef_db_available(),
        },
        "hazards_seismic": {
            "available": seismic_db_available(),
        },
        "hazards_idrogeo": {
            "available": True,  # runtime API, no local store
            "note": "ISPRA IdroGEO — live API, requires network access",
        },
        "hazards_firms": {
            "available": True,
            "note": "NASA FIRMS — live API, requires FIRMS_MAP_KEY",
        },
        "hazards_bulletin": {
            "available": True,
            "note": "Protezione Civile — live API, requires network access",
        },
    }

    # Keep the existing dataset-specific keys (path, note, categories) while
    # making the status contract explicit.  Timestamps remain null until the
    # upstream store exposes publication metadata; the adapter must not invent
    # freshness values from a local file mtime.
    for dataset_name, dataset_status in status.items():
        dataset_status.setdefault("source", "aecs4u-stats")
        dataset_status.setdefault("dataset", dataset_name)
        dataset_status.setdefault("source_version", None)
        dataset_status.setdefault("freshness", {})

    return status


@lru_cache(maxsize=1)
def _istat_engine():
    """Lazily created read-only SQLModel engine over the ISTAT store."""
    from sqlmodel import create_engine

    return create_engine(f"sqlite:///{_istat_sqlite_path()}", echo=False)


def _municipality_sqlite_profile(cadastral_code: str) -> Optional[Dict[str, Any]]:
    """Read the normalized municipality profile directly from the active DB.

    This complements SQLModel for legacy/shared SQLite snapshots where the
    ORM query can return the hierarchy but omit newly added nullable columns.
    """
    try:
        import sqlite3

        connection = sqlite3.connect(f"file:{_istat_sqlite_path()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            municipality = connection.execute(
                "SELECT * FROM municipalities WHERE UPPER(cadastral_code) = ? LIMIT 1",
                (cadastral_code.strip().upper(),),
            ).fetchone()
            if municipality is None:
                return None
            province = connection.execute(
                "SELECT * FROM provinces WHERE id = ?", (municipality["province_id"],)
            ).fetchone()
            region = connection.execute(
                "SELECT * FROM regions WHERE id = ?", (municipality["region_id"],)
            ).fetchone()
            population = connection.execute(
                "SELECT year, resident_population FROM municipality_population_stats "
                "WHERE municipality_id = ? ORDER BY year DESC",
                (municipality["id"],),
            ).fetchall()
        finally:
            connection.close()
        return {
            "cadastral_code": municipality["cadastral_code"],
            "istat_code": municipality["alphanumeric_code"],
            "official_name": municipality["official_name"],
            "procom": municipality["id"],
            "name": municipality["italian_name"] or municipality["official_name"],
            "province": province["name"] if province else None,
            "province_sigla": province["vehicle_code"] if province else None,
            "region": region["name"] if region else None,
            "nuts3_2021": province["nuts3_2021"] if province else None,
            "nuts3": province["nuts3_2024"] if province else None,
            "is_provincial_capital": bool(municipality["is_provincial_capital"]),
            "latitude": municipality["latitude"],
            "longitude": municipality["longitude"],
            "postal_code": municipality["postal_code"],
            "tax_code": municipality["tax_code"],
            "email": municipality["email"],
            "pec_email": municipality["pec_email"],
            "website": municipality["website"],
            "wikipedia_url": municipality["wikipedia_url"],
            "wikidata_url": municipality["wikidata_url"],
            "coat_of_arms_url": municipality["coat_of_arms_url"],
            "population": {
                "year": population[0]["year"],
                "resident_population": population[0]["resident_population"],
            } if population else None,
            "population_history": [
                {"year": row["year"], "resident_population": row["resident_population"]}
                for row in reversed(population)
            ],
            "source": "ISTAT via aecs4u-stats",
        }
    except (OSError, KeyError, sqlite3.Error):
        return None


def get_municipality_by_cadastral_code(cadastral_code: str) -> Optional[Dict[str, Any]]:
    """
    Municipality profile for a catasto comune code (e.g. ``C773`` → Civitavecchia).

    The cadastral code is the join key between Agenzia delle Entrate parcel
    attributes (``ADMINISTRATIVEUNIT`` / NATIONALCADASTRALREFERENCE prefix) and
    the ISTAT administrative hierarchy.

    Returns ``None`` when the store is missing or the code is unknown.
    """
    if not istat_db_available():
        return None

    from sqlmodel import Session, select

    from aecs4u_stats.istat.models import (
        IstatMunicipality,
        IstatProvince,
        IstatRegion,
        MunicipalityPopulationStat,
    )

    code = cadastral_code.strip().upper()
    try:
        with Session(_istat_engine()) as session:
            muni = session.exec(
                select(IstatMunicipality).where(IstatMunicipality.cadastral_code == code)
            ).first()
            if muni is None:
                return None

            province = session.get(IstatProvince, muni.province_id)
            region = session.get(IstatRegion, muni.region_id)
            pop_stats = session.exec(
                select(MunicipalityPopulationStat)
                .where(MunicipalityPopulationStat.municipality_id == muni.id)
                .order_by(MunicipalityPopulationStat.year.desc())  # type: ignore[union-attr]
            ).all()

            latest_pop = pop_stats[0] if pop_stats else None
            result = {
                "cadastral_code": muni.cadastral_code,
                "istat_code": muni.alphanumeric_code,
                "official_name": muni.official_name,
                "procom": muni.id,  # numeric national comune code — join key for census sections
                "name": muni.italian_name or muni.official_name,
                "province": province.name if province else None,
                "province_sigla": province.vehicle_code if province else None,
                "region": region.name if region else None,
                "nuts3_2021": province.nuts3_2021 if province else None,
                "nuts3": province.nuts3_2024 if province else None,
                "is_provincial_capital": muni.is_provincial_capital,
                "latitude": muni.latitude,
                "longitude": muni.longitude,
                "postal_code": muni.postal_code,
                "tax_code": muni.tax_code,
                "email": muni.email,
                "pec_email": muni.pec_email,
                "website": muni.website,
                "wikipedia_url": muni.wikipedia_url,
                "wikidata_url": muni.wikidata_url,
                "coat_of_arms_url": muni.coat_of_arms_url,
                "population": {
                    "year": latest_pop.year,
                    "resident_population": latest_pop.resident_population,
                }
                if latest_pop
                else None,
                "population_history": [
                    {"year": s.year, "resident_population": s.resident_population}
                    for s in reversed(pop_stats)
                ],
                "source": "ISTAT via aecs4u-stats",
            }
            direct = _municipality_sqlite_profile(code)
            if direct:
                for key, value in direct.items():
                    if result.get(key) in (None, [], "") and value not in (None, [], ""):
                        result[key] = value
            return result
    except Exception as e:  # pragma: no cover - defensive: store may be partial
        logger.warning("ISTAT municipality lookup failed for %s: %s", code, e)
        return None


def get_pois_near(
    lat: float,
    lng: float,
    radius_km: float = 1.0,
    categories: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    POIs around a point, grouped by category with ``distance_km``, nearest-first.

    Prefers the aecs4u-stats PostGIS ``facts.poi`` table when configured
    (``AECS4U_STATS_POSTGRES_DSN``), falling back to the local OSM POI
    SQLite store, and finally to an empty result when neither is available.
    """
    postgres_source = _get_postgres_poi_source()
    if postgres_source is not None and postgres_source.available():
        try:
            # Bound the *entire* attempt (connect + query), not just pool
            # construction — a crashing/hanging query is just as capable of
            # never returning as a hanging connect() on this host.
            grouped = _call_with_hard_timeout(
                postgres_source.pois_near, 6, lat, lng, radius_km=radius_km, categories=categories
            )
            return {
                "center": {"lat": lat, "lng": lng},
                "radius_km": radius_km,
                "total": sum(len(v) for v in grouped.values()),
                "categories": grouped,
                "source": "OpenStreetMap via aecs4u-stats (PostGIS)",
            }
        except Exception:
            postgres_source._retry_at = time.monotonic() + 60
            logger.warning("Postgres POI query failed; falling back to local SQLite store", exc_info=True)

    grouped = pois_within_radius(lat, lng, radius_km=radius_km, categories=categories)
    return {
        "center": {"lat": lat, "lng": lng},
        "radius_km": radius_km,
        "total": sum(len(v) for v in grouped.values()),
        "categories": grouped,
        "source": "OpenStreetMap via aecs4u-stats",
    }


def _latest_omi_semester(db_path: Path) -> Optional[tuple[int, int]]:
    """Read the latest QI semester from the compact import log.

    The quote table is multi-gigabyte on the shared volume and its historical
    ``latest_semester`` query has to sort the full table. The import log holds
    the same publication marker in a tiny indexed-by-rowid table.
    """
    try:
        import sqlite3

        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            entries = connection.execute(
                "SELECT csv_entry FROM omi_import_log "
                "WHERE target_table = 'quotazioni_valori'"
            ).fetchall()
        finally:
            connection.close()
        semesters = []
        for (entry,) in entries:
            match = re.search(
                r"QI_(?:\d+_\d+_)?(\d{4})([12])_(?:VALORI|ZONE)\.csv$",
                str(entry or ""),
            )
            if match:
                semesters.append((int(match.group(1)), int(match.group(2))))
        return max(semesters) if semesters else None
    except (OSError, sqlite3.Error):
        return None


def get_omi_quotes(comune: str, zona: Optional[str] = None) -> Dict[str, Any]:
    """
    OMI (Osservatorio Mercato Immobiliare) quotes for a comune's latest
    semester: per-zone/typology sale + rent €/m² ranges.

    ``comune`` accepts either the catasto code (e.g. ``C773``) or the ISTAT
    numeric code. Pass ``zona`` (e.g. ``B1``) to filter to one OMI zone.
    """
    db_path = _omi_sqlite_path()
    latest = _latest_omi_semester(db_path)
    kwargs = {"db_path": db_path}
    if latest:
        kwargs.update({"anno": latest[0], "semestre": latest[1]})
    rows = quotes_for_comune(comune, zona=zona, **kwargs)
    return {
        "comune": comune,
        "zona": zona,
        "quotes": rows,
        "source": "Agenzia delle Entrate OMI via aecs4u-stats",
    }


def get_omi_history(comune: str, zona: str, cod_tipologia: Optional[str] = None) -> Dict[str, Any]:
    """Full semester history (oldest-first) of OMI quotes for one comune/zone."""
    rows = quote_history(comune, zona, cod_tipologia=cod_tipologia, db_path=_omi_sqlite_path())
    return {
        "comune": comune,
        "zona": zona,
        "history": rows,
        "source": "Agenzia delle Entrate OMI via aecs4u-stats",
    }


def estimate_omi_value(
    comune: str,
    zona: str,
    cod_tipologia: str,
    area_sqm: float,
    stato_conservazione: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Return a reproducible, explicitly non-appraisal OMI value range."""
    try:
        area = float(area_sqm)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(area) or area <= 0:
        return None
    db_path = _omi_sqlite_path()
    latest = _latest_omi_semester(db_path)
    kwargs = {"db_path": db_path}
    if latest:
        kwargs.update({"anno": latest[0], "semestre": latest[1]})
    rows = quotes_for_comune(comune, zona=zona, **kwargs)
    type_code = str(cod_tipologia).strip().upper()
    state = stato_conservazione.strip().casefold() if stato_conservazione else None
    matches = [
        row
        for row in rows
        if str(row.get("cod_tipologia", "")).strip().upper() == type_code
        and (
            state is None
            or str(row.get("stato_conservazione", "")).strip().casefold() == state
        )
    ]
    if len(matches) != 1:
        return None

    quote = matches[0]
    try:
        min_rate = float(quote["prezzo_min"])
        max_rate = float(quote["prezzo_max"])
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(min_rate) or not math.isfinite(max_rate) or min_rate < 0 or max_rate < min_rate:
        return None

    return {
        "methodology": "omi-area-range-v1",
        "input": {
            "comune": comune,
            "zona": zona.strip().upper(),
            "cod_tipologia": str(quote.get("cod_tipologia", cod_tipologia)),
            "stato_conservazione": quote.get("stato_conservazione"),
            "area_sqm": area,
        },
        "quote": {
            "anno": quote.get("anno"),
            "semestre": quote.get("semestre"),
            "tipologia": quote.get("tipologia"),
            "prezzo_min_eur_sqm": min_rate,
            "prezzo_max_eur_sqm": max_rate,
        },
        "value_range_eur": {
            "min": round(area * min_rate, 2),
            "max": round(area * max_rate, 2),
        },
        "disclaimer": (
            "Stima indicativa ottenuta moltiplicando la superficie dichiarata per "
            "l'intervallo OMI selezionato. Non è una perizia né una valutazione immobiliare."
        ),
        "source": "Agenzia delle Entrate OMI via aecs4u-stats",
    }


@lru_cache(maxsize=128)
def _load_omi_zone_boundaries(province: str) -> Dict[str, Any]:
    """Load one province's boundary collection once per worker process."""
    return zone_boundaries(province, zones_dir=_omi_zones_dir())


def _omi_zone_code(properties: Dict[str, Any]) -> Optional[str]:
    """Extract a quote-compatible zone code from heterogeneous AdE KML fields."""
    normalized = {
        re.sub(r"[^a-z0-9]", "", str(key).lower()): value
        for key, value in properties.items()
    }
    candidates = [
        normalized.get(key)
        for key in ("codzona", "codicezona", "zonaomi", "zonecode", "zona", "name")
    ]
    candidates.extend(properties.values())
    for value in candidates:
        if value is None or isinstance(value, (dict, list)):
            continue
        text = str(value).strip().upper()
        exact = re.fullmatch(r"[A-Z][0-9]{1,3}", text)
        match = exact or re.search(r"(?:^|\b)([A-Z][0-9]{1,3})(?:\b|$)", text)
        if match:
            return match.group(0) if exact else match.group(1)
    return None


def get_omi_zone_at_point(province: str, lat: float, lng: float) -> Dict[str, Any]:
    """Match a WGS84 point to an OMI zone polygon for one province."""
    result: Dict[str, Any] = {
        "province": province,
        "point": {"lat": lat, "lng": lng},
        "matched": False,
        "zone": None,
        "source": "Agenzia delle Entrate OMI via aecs4u-stats",
    }
    if not zone_boundaries_available(province):
        result["reason"] = "boundary_data_unavailable"
        return result

    point = Point(lng, lat)
    for feature in _load_omi_zone_boundaries(province).get("features", []):
        geometry = feature.get("geometry")
        if not geometry:
            continue
        try:
            if not shape(geometry).covers(point):
                continue
        except (TypeError, ValueError):
            logger.warning("Invalid OMI boundary geometry for province %s", province)
            continue
        properties = feature.get("properties") or {}
        zone = _omi_zone_code(properties)
        if not zone:
            result["reason"] = "zone_code_unavailable"
            return result
        result.update({"matched": True, "zone": zone})
        return result

    result["reason"] = "point_outside_zones"
    return result


def get_income_profile(cadastral_code: str, year: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """
    MEF/IRPEF income profile for a comune by catasto code: taxpayer count,
    mean taxable income, and the 8-bracket income distribution.
    """
    return income_by_cadastral_code(cadastral_code, year=year)


def get_environmental_risks(istat_code: int | str) -> Optional[Dict[str, Any]]:
    """
    Environmental risk profile for a comune (ISTAT code): DPC seismic zone
    (1-4, local store) plus ISPRA IdroGEO flood/landslide indicators (live
    API). Either half may be ``None`` if its source is unavailable.
    """
    seismic = seismic_zone(istat_code)
    raw_hazards = get_comune_hazards(istat_code)
    hazards = summarize_hazards(raw_hazards) if raw_hazards else None

    if seismic is None and hazards is None:
        return None

    return {
        "istat_code": istat_code,
        "seismic": seismic,
        "hydrogeological": hazards,
    }


def get_active_fires(radius_km: float = 50.0, lat: Optional[float] = None, lng: Optional[float] = None) -> Dict[str, Any]:
    """
    Active-fire detections (NASA FIRMS) near a point, or across all of Italy
    when no point is given. Requires the ``FIRMS_MAP_KEY`` environment
    variable; returns an empty list otherwise.
    """
    from aecs4u_stats.hazards.config import ITALY_BBOX

    if lat is not None and lng is not None:
        km_per_deg = 111.0
        dlat = radius_km / km_per_deg
        dlng = radius_km / (km_per_deg * max(0.1, abs(math.cos(math.radians(lat)))))
        bbox = (lng - dlng, lat - dlat, lng + dlng, lat + dlat)
    else:
        bbox = ITALY_BBOX

    detections = _active_fires(bbox=bbox)
    return {
        "bbox": bbox,
        "count": len(detections),
        "detections": detections,
        "source": "NASA FIRMS via aecs4u-stats",
    }


def get_criticality_bulletin() -> Optional[Dict[str, Any]]:
    """Latest Protezione Civile hydro-criticality bulletin (allerta meteo), or
    ``None`` if the live API is unreachable."""
    return latest_criticality_bulletin()


def cadastral_store_available() -> bool:
    """True when at least one per-region cadastral parcel store is built."""
    return cadastral_db_available()


def get_parcels(
    comune: str,
    foglio: Optional[str] = None,
    particella: Optional[str] = None,
    limit: int = 1000,
    offset: int = 0,
    include_geometry: bool = True,
) -> Dict[str, Any]:
    """Cadastral parcels of a comune (optionally one foglio/particella) as a
    GeoJSON FeatureCollection, from the aecs4u-stats per-region DuckDB stores.

    Parcel ``sheet_number`` is spatially derived at build time — reliable
    nationally, unlike parsing NATIONALCADASTRALREFERENCE."""
    fc = parcels_for_comune(
        comune, foglio=foglio, particella=particella,
        limit=limit, offset=offset, include_geometry=include_geometry,
    )
    fc["metadata"]["source"] = "Agenzia delle Entrate INSPIRE via aecs4u-stats"
    return fc


def get_fogli(comune: str) -> Dict[str, Any]:
    """Sheet (foglio) list for a comune with per-sheet parcel counts."""
    return {
        "comune": comune,
        "fogli": fogli_for_comune(comune),
        "source": "Agenzia delle Entrate INSPIRE via aecs4u-stats",
    }


def get_parcel_by_reference(national_reference: str) -> Optional[Dict[str, Any]]:
    """One parcel Feature by exact NATIONALCADASTRALREFERENCE, or ``None``."""
    # Avoid entering the DuckDB adapter when its optional per-region store is
    # absent; its connection setup may attempt to load the spatial extension.
    result = parcel_by_reference(national_reference) if cadastral_db_available() else None
    return result if result is not None else _local_parcel_by_reference(national_reference)


def _local_parcel_by_reference(national_reference: str) -> Optional[Dict[str, Any]]:
    """Read a parcel from the provisioned INSPIRE GeoPackage when no DuckDB
    cadastral store has been built yet.

    This is intentionally a cold-path fallback: the read model stores the
    resulting feature, so subsequent panel requests do not reopen the source.
    """
    reference = str(national_reference).strip()
    code = reference.split("_", 1)[0].upper() if "_" in reference else ""
    if not code:
        return None
    roots = []
    configured = os.getenv("CADASTRAL_DATA_DIR")
    if configured:
        configured_root = Path(configured)
        roots.append(configured_root / "ITALIA" if (configured_root / "ITALIA").is_dir() else configured_root)
    roots.extend((Path("/data/catasto/ITALIA"), Path("/data/catasto")))
    paths = []
    seen = set()
    for root in roots:
        if not root.is_dir():
            continue
        # Shared cadastral volume layout is ITALIA/REGIONE/PROVINCIA/COMUNE.
        direct_paths = list(root.glob(f"*/*/*/{code}_*_ple.gpkg"))
        for path in direct_paths:
            if path not in seen:
                seen.add(path)
                paths.append(path)
        if paths:
            break
        for path in root.rglob(f"{code}_*_ple.gpkg"):
            if path not in seen:
                seen.add(path)
                paths.append(path)
        if paths:
            break
    try:
        import sqlite3
        from shapely import wkb
        from shapely.geometry import mapping

        # GeoPackages are SQLite containers. Query the indexed/ordinary
        # attribute row directly instead of asking pyogrio to materialize the
        # entire feature layer; this keeps a parcel-keyed cold build sub-second
        # even for large regional extracts.
        for path in paths:
            try:
                connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
                connection.row_factory = sqlite3.Row
                try:
                    tables = connection.execute(
                        "SELECT table_name FROM gpkg_contents WHERE data_type = 'features'"
                    ).fetchall()
                    for table_row in tables:
                        table = str(table_row[0]).replace('"', '""')
                        row = connection.execute(
                            f'SELECT * FROM "{table}" WHERE NATIONALCADASTRALREFERENCE = ? LIMIT 1',
                            (reference,),
                        ).fetchone()
                        if row is None:
                            continue
                        properties = {key: row[key] for key in row.keys() if key != "geom"}
                        blob = row["geom"]
                        flags = blob[3] if blob and len(blob) >= 8 else 0
                        envelope_type = (flags >> 1) & 0x07
                        envelope_sizes = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}
                        geometry = wkb.loads(bytes(blob)[8 + envelope_sizes.get(envelope_type, 0):])
                        return {
                            "type": "Feature",
                            "properties": properties,
                            "geometry": mapping(geometry),
                        }
                finally:
                    connection.close()
            except (OSError, sqlite3.Error, TypeError, ValueError):
                continue

        import fiona

        for path in paths:
            try:
                with fiona.open(path) as source:
                    for item in source:
                        properties = dict(item.get("properties") or {})
                        candidate = (
                            properties.get("NATIONALCADASTRALREFERENCE")
                            or properties.get("national_cadastral_reference")
                        )
                        if str(candidate or "").strip() != reference:
                            continue
                        return json.loads(json.dumps({
                            "type": "Feature",
                            "properties": properties,
                            "geometry": item.get("geometry"),
                        }))
            except (OSError, ValueError):
                continue
    except ImportError:
        try:
            import pyogrio
            for path in paths:
                try:
                    escaped = reference.replace("'", "''")
                    frame = pyogrio.read_dataframe(
                        path,
                        where=f"NATIONALCADASTRALREFERENCE = '{escaped}'",
                        use_arrow=False,
                    )
                    if frame.empty:
                        continue
                    row = frame.iloc[0]
                    properties = {
                        key: (value.item() if hasattr(value, "item") else value)
                        for key, value in row.to_dict().items()
                        if key != "geometry"
                    }
                    geometry = row.get("geometry")
                    return {
                        "type": "Feature",
                        "properties": properties,
                        "geometry": mapping(geometry) if geometry is not None else None,
                    }
                except (OSError, ValueError):
                    continue
        except ImportError:
            logger.warning("Neither Fiona nor pyogrio is available; local cadastral fallback is disabled")
    return None


def get_parcel_at_point(lat: float, lng: float) -> Optional[Dict[str, Any]]:
    """The parcel Feature containing a WGS84 point, or ``None``."""
    return parcel_at_point(lat, lng) if cadastral_db_available() else None


def _parcel_reference(feature: Optional[Dict[str, Any]]) -> Optional[str]:
    """Read the canonical parcel key from a GeoJSON feature."""
    if not feature:
        return None
    props = feature.get("properties") or {}
    value = (
        props.get("national_cadastral_reference")
        or props.get("NATIONALCADASTRALREFERENCE")
        or props.get("national_reference")
    )
    return str(value).strip() if value else None


def _parcel_centroid(feature: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """Return a WGS84 centroid from the parcel GeoJSON geometry."""
    geometry = feature.get("geometry")
    if not geometry:
        return None
    try:
        centroid = shape(geometry).centroid
        return {"lat": float(centroid.y), "lng": float(centroid.x)}
    except (TypeError, ValueError, AttributeError):
        return None


def _parcel_enrichment_fingerprint() -> str:
    """Fingerprint source snapshots without reading the large data files."""
    paths = (_istat_sqlite_path(), _census_store_path(), _omi_sqlite_path())
    parts = []
    for path in paths:
        if path is None:
            parts.append("-")
            continue
        try:
            stat = Path(path).stat()
            parts.append(f"{path}:{stat.st_size}:{stat.st_mtime_ns}")
        except OSError:
            parts.append(f"{path}:-")
    postgres_dsn = _postgres_stats_dsn()
    postgres_marker = hashlib.sha256(postgres_dsn.encode("utf-8")).hexdigest() if postgres_dsn else "-"
    material = "parcel-read-model-v3|" + "|".join(parts) + "|postgres:" + postgres_marker
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _census_section_for_parcel(
    point: Optional[Dict[str, float]],
    municipality: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Resolve a parcel centroid against its municipality's census sections.

    The provisioned 2021 store contains Sicily geometry in UTM 32N while the
    upstream helper currently assumes that the geometry is WGS84. Restricting
    the read to one ``PROCOM`` also avoids scanning the national store.
    """
    if not point or not municipality or municipality.get("procom") is None:
        return None
    store_path = _census_store_path()
    if store_path is None:
        return None
    try:
        import duckdb
        from pyproj import Transformer
        from shapely import wkb

        connection = duckdb.connect(str(store_path), read_only=True)
        try:
            columns = [row[0] for row in connection.execute("DESCRIBE sections").fetchall()]
            geometry_index = columns.index("geom")
            rows = connection.execute(
                "SELECT * FROM sections WHERE procom = ?",
                [int(municipality["procom"])],
            ).fetchall()
        finally:
            connection.close()

        # Sicily's provisioned census geometry is EPSG:32632. Keep the second
        # candidate for stores generated from the neighbouring UTM zone.
        projected_points = []
        for epsg in (32632, 32633):
            transformer = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
            projected_points.append((epsg, Point(*transformer.transform(point["lng"], point["lat"]))))

        for row in rows:
            geometry = wkb.loads(bytes(row[geometry_index]))
            projected_point = next(
                (candidate for epsg, candidate in projected_points if geometry.covers(candidate)),
                None,
            )
            if projected_point is None:
                continue
            props = {}
            for name, value in zip(columns, row, strict=True):
                if name == "geom":
                    continue
                if hasattr(value, "item"):
                    value = value.item()
                props[name] = value
            props["ratios"] = {
                "education_tertiary_rate": round(props["p90"] / props["p83"], 4)
                if props.get("p90") is not None and props.get("p83") not in (None, 0) else None,
                "employment_rate_working_age": round(
                    props["p101"] / sum(props.get(name) or 0 for name in (
                        "p17", "p18", "p19", "p20", "p21", "p22",
                        "p23", "p24", "p25", "p26",
                    )),
                    4,
                ) if props.get("p101") is not None and sum(
                    props.get(name) or 0 for name in (
                        "p17", "p18", "p19", "p20", "p21", "p22",
                        "p23", "p24", "p25", "p26",
                    )
                ) else None,
                "foreign_resident_share": round(props["st1"] / props["p1"], 4)
                if props.get("st1") is not None and props.get("p1") not in (None, 0) else None,
                "vacancy_rate": round(props["a3"] / props["a8"], 4)
                if props.get("a3") is not None and props.get("a8") not in (None, 0) else None,
                "avg_household_size": round(props["p1"] / props["pf1"], 4)
                if props.get("p1") is not None and props.get("pf1") not in (None, 0) else None,
            }
            return {"type": "Feature", "properties": props, "geometry": None}
    except Exception as exc:  # pragma: no cover - optional store/CRS fallback
        logger.warning("Parcel census lookup failed: %s", exc)
    return None


def _build_parcel_enrichment(national_reference: str) -> Optional[Dict[str, Any]]:
    """Build the stable portion of one parcel's read-optimized profile."""
    parcel = get_parcel_by_reference(national_reference)
    if parcel is None:
        return None

    props = parcel.get("properties") or {}
    cadastral_code = str(
        props.get("municipality_code")
        or props.get("ADMINISTRATIVEUNIT")
        or national_reference.split("_", 1)[0]
    ).strip().upper()
    municipality = get_municipality_by_cadastral_code(cadastral_code)
    point = _parcel_centroid(parcel)

    postgres_source = _get_postgres_stats_source()
    postgres_context = None
    if postgres_source is not None and postgres_source.available():
        postgres_context = postgres_source.context_for_parcel(national_reference, cadastral_code, point)

    omi = get_omi_quotes(cadastral_code)
    omi_zone = None
    if point and municipality and municipality.get("province"):
        omi_zone = get_omi_zone_at_point(
            municipality["province"], point["lat"], point["lng"]
        )

    postgres_omi = (postgres_context or {}).get("omi")
    postgres_census = (postgres_context or {}).get("census")
    if postgres_omi and postgres_omi.get("omi_zone_key"):
        omi_zone = {
            "province": (municipality or {}).get("province_sigla"),
            "point": point,
            "matched": True,
            "zone": postgres_omi["omi_zone_key"],
            "source": "aecs4u-stats PostgreSQL spatial.market_zone",
        }
    census = postgres_census or _census_section_for_parcel(point, municipality)
    tax_facts = (postgres_context or {}).get("tax_facts") or []
    tax_years = [row.get("year") for row in tax_facts if row.get("year") is not None]
    latest_tax_year = max(tax_years) if tax_years else None
    latest_tax = [row for row in tax_facts if row.get("year") == latest_tax_year]
    tax_by_measure = {row.get("measure"): row for row in latest_tax}
    taxpayers = (tax_by_measure.get("imponibile") or {}).get("frequency")
    total_income = (tax_by_measure.get("imponibile") or {}).get("amount")
    economics = None
    if latest_tax:
        economics = {
            "tax_year": latest_tax_year,
            "taxpayers": taxpayers,
            "total_income": total_income,
            "average_income": round(float(total_income) / float(taxpayers), 2)
            if total_income is not None and taxpayers not in (None, 0) else None,
            "net_tax": (tax_by_measure.get("imposta_netta") or {}).get("amount"),
            "income_brackets": {
                row["measure"].removeprefix("bracket_"): row.get("frequency")
                for row in latest_tax
                if str(row.get("measure", "")).startswith("bracket_")
            },
            "source_facts": latest_tax,
        }
    blocks = {
        name: _detail_block()
        for name in _PARCEL_DETAIL_BLOCKS
    }
    blocks.update({
        "basic": _detail_block(
            parcel,
            source="Agenzia delle Entrate INSPIRE cadastral extract",
            match_method="parcel_reference",
        ),
        "cadastral": _detail_block(
            {
                "foglio": props.get("sheet_number") or props.get("foglio"),
                "sezione_urbana": props.get("urban_section") or props.get("sezione_urbana"),
                "comune_code": cadastral_code,
                "postal_code": (municipality or {}).get("postal_code")
                or (postgres_context or {}).get("postal_code"),
                "postgres_parcel_spine_available": bool(
                    (postgres_context or {}).get("parcel_spine_available")
                ),
            },
            source="Agenzia delle Entrate / ISTAT",
            match_method="parcel_reference",
        ),
        "population": _detail_block(
            census,
            source="ISTAT Permanent Census 2021 via aecs4u-stats PostgreSQL"
            if postgres_census else "ISTAT Basi Territoriali 2021",
            match_method="centroid",
        ),
        "demographics": _detail_block(
            census,
            source="ISTAT Permanent Census 2021 via aecs4u-stats PostgreSQL"
            if postgres_census else "ISTAT Basi Territoriali 2021",
            match_method="centroid",
        ),
        "economics": _detail_block(
            economics,
            source="MEF/IRPEF facts via aecs4u-stats PostgreSQL",
            match_method="municipality",
        ),
        "valuation": _detail_block(
            {
                "zone": omi_zone,
                "quotes": (omi or {}).get("quotes", []),
                "postgres_snapshot": postgres_omi,
            },
            source="Agenzia delle Entrate OMI via aecs4u-stats PostgreSQL"
            if postgres_omi else "Agenzia delle Entrate OMI",
            match_method="centroid",
        ),
    })
    return {
        "national_reference": national_reference,
        "parcel": parcel,
        "municipality": municipality,
        "centroid": point,
        "omi": omi,
        "omi_zone": omi_zone,
        "census": census,
        "blocks": blocks,
        "postgres_context": postgres_context,
        "source": "aecs4u-stats PostgreSQL context with cadastral parcel fallback",
    }


def get_parcel_enrichment(
    national_reference: str,
    refresh: bool = False,
) -> Optional[Dict[str, Any]]:
    """Read or refresh one parcel-keyed materialized enrichment profile.

    The read model is stored in the aecs4u-stats PostgreSQL ``serving`` schema.
    A cold key is built once; subsequent panel loads only perform one indexed
    row lookup. The application SQLite database is not used by this path.
    """
    reference = str(national_reference).strip()
    if not reference:
        return None

    postgres_source = _get_postgres_stats_source()
    if postgres_source is None or not postgres_source.available():
        logger.warning("PostgreSQL stats source is required for parcel enrichment")
        return None

    fingerprint = _parcel_enrichment_fingerprint()
    if not refresh:
        cached = postgres_source.get_read_model(reference)
        cached_meta = (cached or {}).get("read_model") or {}
        if cached is not None and cached_meta.get("source_fingerprint") == fingerprint:
            return cached

    payload = _build_parcel_enrichment(reference)
    if payload is None:
        return None
    postgres_source.upsert_read_model(
        reference,
        payload,
        source_fingerprint=fingerprint,
    )
    payload["read_model"] = {
        "key": reference,
        "source_fingerprint": fingerprint,
        "cached": False,
        "database": "aecs4u-stats PostgreSQL",
    }
    return payload


def get_parcels_in_bbox(
    min_lng: float, min_lat: float, max_lng: float, max_lat: float,
    limit: int = 5000, include_geometry: bool = True,
) -> Dict[str, Any]:
    """Parcels intersecting a WGS84 bbox, across all built region stores."""
    fc = parcels_in_bbox(min_lng, min_lat, max_lng, max_lat, limit=limit,
                         include_geometry=include_geometry)
    fc["metadata"]["source"] = "Agenzia delle Entrate INSPIRE via aecs4u-stats"
    return fc


# ---------------------------------------------------------------------------
# ISTAT census sections (2021) — population/education/employment/foreign-
# resident/household/dwelling-occupancy indicators at ~756,000-section
# national granularity, the finest official socioeconomic signal available.
# ---------------------------------------------------------------------------

def census_db_available() -> bool:
    """True when the 2021 census-sections store has been built on this host."""
    path = _census_store_path()
    return bool(path and _census_db_available(path))


def get_census_sections(cadastral_code: str, limit: int = 5000) -> Optional[Dict[str, Any]]:
    """Census sections covering a comune (by catasto code) as a GeoJSON
    FeatureCollection — each Feature's properties carry the 119 raw ISTAT
    indicator counts plus a ``ratios`` sub-dict (education/employment/
    foreign-resident/vacancy rate, avg household size)."""
    if not census_db_available():
        return None
    muni = get_municipality_by_cadastral_code(cadastral_code)
    if muni is None or muni.get("procom") is None:
        return None
    fc = _census_sections_for_comune(muni["procom"], limit=limit, store_path=_census_store_path())
    fc["metadata"]["source"] = "ISTAT Basi Territoriali 2021 via aecs4u-stats"
    return fc


def get_census_section_at_point(lat: float, lng: float) -> Optional[Dict[str, Any]]:
    """The census section containing a WGS84 point, or ``None``."""
    if not census_db_available():
        return None
    parcel = get_parcel_at_point(lat, lng)
    if parcel:
        props = parcel.get("properties") or {}
        code = props.get("municipality_code") or props.get("ADMINISTRATIVEUNIT")
        municipality = get_municipality_by_cadastral_code(str(code)) if code else None
        result = _census_section_for_parcel(
            {"lat": lat, "lng": lng}, municipality
        )
        if result is not None:
            return result
    return _census_section_at_point(lat, lng, store_path=_census_store_path())


# ---------------------------------------------------------------------------
# ISTAT safety/crime, BES quality-of-life, demographic indicators — province
# (NUTS3) level. Resolved from a comune's catasto code via its province's
# nuts3 code (see get_municipality_by_cadastral_code); no comune-level
# granularity is published for these datasets.
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _istat_query_engine():
    """Lazily created DuckDB-backed ISTATQueryEngine (separate from
    ``_istat_engine()`` above, which is the SQLite/SQLModel municipality store)."""
    from aecs4u_stats.istat.queries import ISTATQueryEngine

    return ISTATQueryEngine()


def safety_db_available() -> bool:
    return _istat_query_engine().safety_available()


def bes_db_available() -> bool:
    return _istat_query_engine().bes_available()


def demographic_db_available() -> bool:
    return _istat_query_engine().demographic_available()


def _resolve_nuts3(cadastral_code: str) -> Optional[Dict[str, Any]]:
    muni = get_municipality_by_cadastral_code(cadastral_code)
    if muni is None or not muni.get("nuts3"):
        return None
    return muni


def get_crime_profile(cadastral_code: str, year: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Reported-crime (delitti denunciati) profile for a comune's province —
    ISTAT publishes this at province (NUTS3), not comune, granularity."""
    muni = _resolve_nuts3(cadastral_code)
    if muni is None:
        return None
    engine = _istat_query_engine()
    kpis = engine.get_safety_overview_kpis(ref_area=muni["nuts3"], year=year)
    if not kpis or kpis.get("year") is None:
        return None
    return {
        "cadastral_code": cadastral_code,
        "province": muni["province"],
        "nuts3": muni["nuts3"],
        **kpis,
        "crime_types_available": engine.list_crime_types(ref_area=muni["nuts3"]),
        "source": "ISTAT (delitti denunciati) via aecs4u-stats",
    }


def get_quality_of_life_indicators(cadastral_code: str) -> Optional[Dict[str, Any]]:
    """BES indicator codes available for a comune's province."""
    muni = _resolve_nuts3(cadastral_code)
    if muni is None:
        return None
    indicators = _istat_query_engine().list_bes_indicators(ref_area=muni["nuts3"])
    if not indicators:
        return None
    return {
        "cadastral_code": cadastral_code, "nuts3": muni["nuts3"],
        "indicators": indicators, "source": "ISTAT BES via aecs4u-stats",
    }


def get_quality_of_life_indicator(
    cadastral_code: str, data_type: str, year: Optional[int] = None
) -> Optional[Dict[str, Any]]:
    """Time series for one BES indicator (see ``get_quality_of_life_indicators``)."""
    muni = _resolve_nuts3(cadastral_code)
    if muni is None:
        return None
    series = _istat_query_engine().get_bes_indicator(data_type, ref_area=muni["nuts3"], year=year)
    if not series:
        return None
    return {
        "cadastral_code": cadastral_code, "nuts3": muni["nuts3"], "data_type": data_type,
        "series": series, "source": "ISTAT BES via aecs4u-stats",
    }


def get_demographic_indicators(cadastral_code: str) -> Optional[Dict[str, Any]]:
    """Demographic indicator codes available for a comune's province."""
    muni = _resolve_nuts3(cadastral_code)
    if muni is None:
        return None
    indicators = _istat_query_engine().list_demographic_indicators(ref_area=muni["nuts3"])
    if not indicators:
        return None
    return {
        "cadastral_code": cadastral_code, "nuts3": muni["nuts3"],
        "indicators": indicators, "source": "ISTAT demographic indicators via aecs4u-stats",
    }


def get_demographic_indicator(
    cadastral_code: str, data_type: str, year: Optional[int] = None
) -> Optional[Dict[str, Any]]:
    """Time series for one demographic indicator (see ``get_demographic_indicators``)."""
    muni = _resolve_nuts3(cadastral_code)
    if muni is None:
        return None
    series = _istat_query_engine().get_demographic_indicator(data_type, ref_area=muni["nuts3"], year=year)
    if not series:
        return None
    return {
        "cadastral_code": cadastral_code, "nuts3": muni["nuts3"], "data_type": data_type,
        "series": series, "source": "ISTAT demographic indicators via aecs4u-stats",
    }
