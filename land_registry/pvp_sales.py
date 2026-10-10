"""Public-auction sales (Portale delle Vendite Pubbliche) for the direct map.

aecs4u-stats publishes ``pvp.v_map_sale_points`` for bulk map loads and
``pvp.v_map_sales`` for individual sale details. When a PVP modelview DSN is
configured, the map reads those enriched views directly; otherwise it uses
the stats FDW. The points view resolves addresses in batches instead of
repeating the detail view's indexed lookups for every sale. The map reads
geocoded rows once,
keeps a compact in-memory copy (refreshed hourly, the last good copy kept if
a refresh fails) and filters that copy per request.
"""

from __future__ import annotations

import asyncio
import heapq
import logging
import math
import os
import re
import time
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import urlsplit, urlunsplit

from land_registry.map_layers import _asyncpg_sql, _AsyncpgConnectionSource, get_map_layer_source

log = logging.getLogger(__name__)

SALES_RELATION = "pvp.v_map_sales"

# Mainland Italy, Sardinia, Sicily and the minor islands.  Rows outside are
# geocoding placeholders ((1, 1), (683, 684), ...) or foreign addresses.
ITALY_BOUNDS = (6.4, 35.2, 18.8, 47.2)  # west, south, east, north

# This many sales on one exact coordinate means a fallback geocode (a court or
# a municipality centroid), not the property's own address.
APPROXIMATE_LOCATION_SHARE = 20

# (key, label, keywords) checked in order against the lower-cased PVP
# property type; the first match wins.  Keywords match at the start of a word
# ("merci" must not match "commerciali").  Movable goods and special buildings
# come first because their names reuse words such as "commerciali" or "casa".
SALE_CATEGORIES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("movable", "Vehicles, goods & business assets", (
        "autovettur", "automezz", "veicol", "furgon", "motocicl", "imbarcazion", "attrezzatur",
        "mobili", "arredi", "computer", "macchinar", "merci", "titoli", "marchi", "credito",
        "cessione d'azienda", "azienda", "gioiell", "quadri", "beni mobili",
    )),
    ("commercial", "Shops, offices & hospitality", (
        "negozi", "negozio", "bottegh", "uffic", "studi privati", "commercial", "alberg",
        "pension", "casa di cura", "case di cura", "ospedal", "teatri", "cinema", "palestra",
        "sportiv", "convent", "convitto", "istitut", "balnear",
    )),
    ("parking_storage", "Garages, storage & annexes", (
        "posto auto", "garage", "autorimess", "box", "stalle", "rimesse", "magazzin", "deposit",
        "cantin", "tettoi", "lastrico", "soffitt",
    )),
    ("residential", "Homes", (
        "abitazion", "appartament", "vill", "alloggi", "residenz", "attico", "mansard",
    )),
    ("industrial", "Industrial & agricultural buildings", (
        "industrial", "opifici", "opificio", "laborator", "capannon", "artigian", "agricol",
        "rurale",
    )),
    ("land", "Land & building plots", ("terren", "edificabil", "fondo rustico")),
    ("building", "Whole or partial buildings", (
        "fabbricat", "porzione", "compendio", "immobil", "edifici", "edificio", "castell", "palazz",
    )),
)
OTHER_CATEGORY = ("other", "Other")
UNSPECIFIED_CATEGORY = ("unspecified", "Type not stated")
CATEGORY_LABELS: dict[str, str] = {
    **{key: label for key, label, _ in SALE_CATEGORIES},
    OTHER_CATEGORY[0]: OTHER_CATEGORY[1],
    UNSPECIFIED_CATEGORY[0]: UNSPECIFIED_CATEGORY[1],
}
CATEGORY_KEYS: tuple[str, ...] = tuple(CATEGORY_LABELS)
_CATEGORY_PATTERNS = tuple(
    (key, re.compile(r"(?<!\w)(?:" + "|".join(re.escape(keyword) for keyword in keywords) + ")"))
    for key, _label, keywords in SALE_CATEGORIES
)

PERIODS = ("upcoming", "12m", "5y", "all")

# Leading characters of the notice text used when the property type is empty.
DESCRIPTION_HINT_LENGTH = 240

# Compact point layout shared with map-v2.js (see the ``fields`` key).
# City, address and description come from the per-sale detail endpoint.
POINT_FIELDS = ("id", "lng", "lat", "price", "date", "category", "approximate")

# Columns read for the map points; only sale_id and a location are required,
# so the loader keeps working while the view definition evolves.
_POINT_COLUMNS = (
    "sale_id", "latitude", "longitude", "price", "sale_datetime", "property_type",
    "coordinate_is_approximate", "description_hint",
)
_DETAIL_COLUMNS = (
    "sale_id", "detail_id", "source", "property_type", "description", "price", "sale_datetime",
    "city", "province", "postal_code", "address", "street", "house_number", "url",
    "latitude", "longitude", "address_source", "geocoding_source", "coordinate_is_approximate",
)
SPATIAL_CELL_DEGREES = 0.1


def sale_category(property_type: Optional[str], description: Optional[str] = None) -> str:
    """Group the free-text PVP property type into a map category key.

    Most upcoming sales carry no property type; their notice text ("piena
    proprietà di box auto…") is classified with the same keywords instead.
    """
    text = (property_type or "").strip().lower()
    if not text:
        # Notices name the main asset first ("appartamento … con cantina").
        hint = (description or "").strip().lower()[:DESCRIPTION_HINT_LENGTH]
        matches = [(match.start(), key) for key, pattern in _CATEGORY_PATTERNS if (match := pattern.search(hint))]
        return min(matches)[1] if matches else UNSPECIFIED_CATEGORY[0]
    for key, pattern in _CATEGORY_PATTERNS:
        if pattern.search(text):
            return key
    return OTHER_CATEGORY[0]


def _in_italy(lng: float, lat: float) -> bool:
    west, south, east, north = ITALY_BOUNDS
    return west <= lng <= east and south <= lat <= north


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _day(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def _period_start_end(period: str, today: date) -> tuple[Optional[date], Optional[date]]:
    if period == "upcoming":
        return today, None
    if period == "12m":
        return today - timedelta(days=365), None
    if period == "5y":
        return today - timedelta(days=5 * 365), None
    return None, None


class PvpSalesStore:
    """Compact, periodically refreshed copy of the geocoded PVP sales."""

    LOAD_STATEMENT_TIMEOUT = "300s"
    DETAIL_STATEMENT_TIMEOUT = "20s"

    def __init__(self, connection_source: Any = None, ttl: float = 3600.0):
        self._connection_source = connection_source
        self.ttl = ttl
        self._snapshot: Optional[dict[str, Any]] = None
        self._loaded_at = 0.0
        self._task: Optional[asyncio.Task] = None
        self._active_source: Any = connection_source
        self._active_relation = SALES_RELATION
        self._source_candidates: Optional[list[tuple[Any, str]]] = None
        self.last_error: Optional[str] = None

    def _sources(self) -> list[tuple[Any, str]]:
        if self._source_candidates is not None:
            return self._source_candidates
        if self._connection_source is not None:
            self._source_candidates = [(self._connection_source, SALES_RELATION)]
            return self._source_candidates

        # The enriched PVP database owns the complete view. Prefer its direct
        # publication when configured; aecs4u-stats can serve the same view
        # through postgres_fdw as a deployment-compatible fallback.
        names = (
            "AECS4U_PVP_ENRICHED_POSTGRES_DSN",
            "PVP_ENRICHED_MODELVIEW_POSTGRES_DSN",
            "PVP_MODELVIEW_DATABASE_URL",
            "PROD_MODELVIEW_DATABASE_URL_PVP",
        )
        dsn = next(
            (
                os.getenv(name, "").strip()
                for name in names
                if os.getenv(name, "").strip().startswith(("postgres://", "postgresql://", "postgresql+"))
            ),
            None,
        )
        if not dsn:
            # Local source credentials may live in property-scraper's .env.
            # Read only its PVP-specific DSN, never its general DATABASE_URL.
            try:
                from dotenv import dotenv_values

                sibling_env = Path(__file__).resolve().parents[2] / "property-scraper" / ".env"
                dsn = str(dotenv_values(sibling_env).get("PROD_MODELVIEW_DATABASE_URL_PVP") or "").strip()
            except (OSError, TypeError, ValueError):
                dsn = ""
        if not dsn:
            for name in ("AECS4U_PVP_MODELVIEW_POSTGRES_DSN", "PVP_MODELVIEW_POSTGRES_DSN"):
                legacy_dsn = os.getenv(name, "").strip()
                if not legacy_dsn.startswith(("postgres://", "postgresql://", "postgresql+")):
                    continue
                parsed = urlsplit(legacy_dsn.replace("postgresql+asyncpg://", "postgresql://", 1))
                database = parsed.path.rsplit("/", 1)[-1]
                # The long-standing local variable name points at pvp_modelview,
                # while the complete production superset is the pvp_enriched DB.
                dsn = legacy_dsn if "enriched" in database.lower() else urlunsplit(parsed._replace(path="/pvp_enriched"))
                break

        candidates: list[tuple[Any, str]] = []
        if dsn.startswith(("postgres://", "postgresql://", "postgresql+")):
            pvp_source = _AsyncpgConnectionSource(dsn)
            candidates.extend((pvp_source, relation) for relation in ("modelview.v_map_sales", "public.v_map_sales"))
        stats_source = get_map_layer_source().connection_source
        if stats_source is not None:
            candidates.append((stats_source, SALES_RELATION))
        self._source_candidates = candidates
        return candidates

    @property
    def connection_source(self) -> Any:
        if self._active_source is not None:
            return self._active_source
        candidates = self._sources()
        return candidates[0][0] if candidates else None

    @property
    def available(self) -> bool:
        return bool(self._sources())

    async def snapshot(self, wait: bool = True) -> Optional[dict[str, Any]]:
        """The cached sales, refreshed in the background once ``ttl`` expires.

        With ``wait=False`` a caller gets None until the first load finishes
        (the endpoint answers 503 + Retry-After meanwhile); afterwards a stale
        copy is served while the refresh runs.
        """
        fresh = self._snapshot is not None and time.monotonic() - self._loaded_at <= self.ttl
        if fresh:
            return self._snapshot
        if self._task is None or self._task.done():
            self._task = asyncio.ensure_future(self._refresh())
        if self._snapshot is not None:
            return self._snapshot
        if not wait:
            return None
        await asyncio.shield(self._task)
        return self._snapshot

    async def refresh(self) -> Optional[dict[str, Any]]:
        """Force a complete reload after the source view or its data changes."""
        if self._task is None or self._task.done():
            self._loaded_at = 0.0
            self._task = asyncio.ensure_future(self._refresh())
        await asyncio.shield(self._task)
        return self._snapshot

    def _publish(self, snapshot: dict[str, Any]) -> None:
        self._snapshot = snapshot
        self._loaded_at = time.monotonic()
        self.last_error = None

    async def _refresh(self) -> None:
        try:
            snapshot = await self._load()
        except Exception as exc:  # keep serving the previous copy
            self.last_error = str(exc)
            log.warning("PVP sales load from configured views failed: %s", exc)
            if self._snapshot is None:
                raise
            return
        self._snapshot = snapshot
        self._loaded_at = time.monotonic()
        self.last_error = None

    async def _columns(self, connection: Any, relation: str = SALES_RELATION) -> set[str]:
        rows = await connection.fetch(_asyncpg_sql("""
            SELECT attname::text AS name
            FROM pg_attribute
            WHERE attrelid = to_regclass(%s) AND attnum > 0 AND NOT attisdropped
        """), relation)
        return {row["name"] for row in rows}

    @staticmethod
    def _location_sql(columns: set[str], relation: str = SALES_RELATION) -> tuple[str, str]:
        if {"latitude", "longitude"} <= columns:
            return "latitude", "longitude"
        if "geom" in columns:
            return "ST_Y(geom)", "ST_X(geom)"
        raise RuntimeError(f"{relation} exposes no latitude/longitude or geom column")

    async def _load(self) -> dict[str, Any]:
        started = time.perf_counter()
        west, south, east, north = ITALY_BOUNDS
        failures = []
        published: Optional[dict[str, Any]] = None
        candidates = [
            (source, relation, detail_relation)
            for source, detail_relation in self._sources()
            for relation in (detail_relation.replace("v_map_sales", "v_map_sale_points"), detail_relation)
        ]
        for source, relation, detail_relation in candidates:
            try:
                async with source.connection() as connection:
                    columns = await self._columns(connection, relation)
                    if not columns:
                        raise RuntimeError(f"{relation} is not published in this database")
                    if "sale_id" not in columns:
                        raise RuntimeError(f"{relation} has no sale_id column")
                    latitude, longitude = self._location_sql(columns, relation)
                    optional = [name for name in _POINT_COLUMNS[3:] if name in columns]
                    if "description_hint" not in columns and {"property_type", "description"} <= columns:
                        # Built-in functions ship to the remote view, so only a short
                        # prefix crosses the FDW, and only for untyped sales.
                        optional.append(
                            "CASE WHEN NULLIF(btrim(property_type), '') IS NULL"
                            f" THEN left(description, {DESCRIPTION_HINT_LENGTH}) END AS description_hint"
                        )
                    select = ", ".join(["sale_id", f"{latitude} AS latitude", f"{longitude} AS longitude", *optional])
                    # Push the Italy bounds into the selected source so placeholder
                    # and foreign coordinates are discarded before transfer.
                    sql = f"""
                        SELECT {select}
                        FROM {relation}
                        WHERE {latitude} BETWEEN {south!r} AND {north!r}
                          AND {longitude} BETWEEN {west!r} AND {east!r}
                    """
                    async with connection.transaction():
                        await connection.execute(f"SET LOCAL statement_timeout = '{self.LOAD_STATEMENT_TIMEOUT}'")
                        if relation.endswith(".v_map_sale_points"):
                            # Bulk hash joins/sorts need more than PostgreSQL's
                            # default 4 MB. Limit this allowance to the map load.
                            await connection.execute("SET LOCAL work_mem = '64MB'")
                            await connection.execute("SET LOCAL jit = off")
                        rows = await connection.fetch(sql, timeout=330)
                        # The sales are usable as soon as they are read. Build and
                        # publish them before the optional parcel refinement below,
                        # whose two queries can add tens of seconds, so a request
                        # waiting on a cold start is answered from the base copy.
                        # Normalization and grid construction stay off the event
                        # loop so one cold refresh cannot stall unrelated requests.
                        snapshot = await asyncio.to_thread(self._build_snapshot, rows)
                        snapshot["columns"] = sorted(columns)
                        snapshot["relation"] = relation
                        snapshot["database"] = (
                            "pvp_enriched" if detail_relation == "modelview.v_map_sales"
                            else "pvp_enriched_modelview" if detail_relation == "public.v_map_sales"
                            else "aecs4u-stats"
                        )
                        self._active_source, self._active_relation = source, detail_relation
                        published = snapshot
                        self._publish(snapshot)
                        log.info(
                            "PVP sales loaded from %s: %d geocoded sales in %.0f ms",
                            relation, len(snapshot["points"]), (time.perf_counter() - started) * 1000,
                        )
                        asset_references = []
                        if relation in {"modelview.v_map_sale_points", "modelview.v_map_sales"}:
                            asset_references = await self._asset_cadastral_references(
                                connection, [row["sale_id"] for row in rows if row["sale_id"] is not None]
                            )
                cadastral_points = await self._resolve_cadastral_points(asset_references)
                refined = self._with_cadastral_points(snapshot, cadastral_points)
                if refined is not snapshot:
                    self._publish(refined)
                    log.info(
                        "PVP sales refined with %d parcel positions in %.0f ms",
                        len(cadastral_points), (time.perf_counter() - started) * 1000,
                    )
                return refined
            except Exception as exc:
                if published is not None:
                    # The sales are already being served; a failed refinement
                    # must not restart the load against another relation.
                    log.warning("PVP sales parcel refinement failed: %s", exc)
                    return published
                failures.append(f"{relation}: {exc}")
                log.debug("PVP map source %s unavailable: %s", relation, exc)
        raise RuntimeError("No usable PVP map view found (" + "; ".join(failures) + ")")

    @staticmethod
    async def _asset_cadastral_references(
        connection: Any, sale_ids: list[int]
    ) -> list[tuple[int, str, str, str, str]]:
        """Read explicit asset sheet/parcel pairs and their municipality."""
        if not sale_ids:
            return []
        sql = _asyncpg_sql(r"""
            SELECT asset.sale_id,
                   COALESCE(address_municipality.name, municipality.name) AS municipality_name,
                   COALESCE(address_municipality.province_code, municipality.province_code) AS province_code,
                   reference[1] AS sheet,
                   reference[2] AS parcel
              FROM modelview.modelview_assets AS asset
              JOIN modelview.modelview_sales AS sale ON sale.id = asset.sale_id
              LEFT JOIN modelview.modelview_addresses AS address ON address.id = asset.address_id
              LEFT JOIN modelview.map_pvp_municipalities AS address_municipality
                ON address_municipality.code = address.municipality_code
              LEFT JOIN LATERAL (
                   SELECT candidate.name, candidate.province_code
                     FROM modelview.map_pvp_municipalities AS candidate
                     LEFT JOIN modelview.map_pvp_provinces AS province
                       ON province.code = candidate.province_code
                    WHERE address_municipality.code IS NULL
                      AND lower(BTRIM(candidate.name)) = lower(BTRIM(sale.city))
                      AND (NULLIF(BTRIM(sale.province), '') IS NULL
                           OR lower(BTRIM(province.name)) = lower(BTRIM(sale.province))
                           OR upper(BTRIM(province.code)) = upper(BTRIM(sale.province)))
                    ORDER BY candidate.code
                    LIMIT 1
              ) AS municipality ON TRUE
              CROSS JOIN LATERAL regexp_match(
                   lower(asset.description),
                   '(?:foglio|fgl?\.?)[[:space:],:;]*(?:n\.?[[:space:]]*)?([0-9]+[[:alpha:]]*(?:/[[:alnum:]]+)?)'
                   '.{0,160}?(?:particella|part\.?|p\.lla|mappale|mapp\.?)[[:space:],:;]*(?:n\.?[[:space:]]*)?([0-9]+[[:alpha:]]*(?:/[[:alnum:]]+)?)'
              ) AS reference
             WHERE asset.sale_id = ANY(%s::bigint[])
               AND NULLIF(BTRIM(asset.description), '') IS NOT NULL
               AND asset.description ~* '(foglio|fgl?\.?)[[:space:]]*[0-9]+'
               AND asset.description ~* '(particella|part\.?|p\.lla|mappale|mapp\.?)[[:space:]]*[0-9]+'
        """)
        try:
            rows = await connection.fetch(sql, sale_ids, timeout=120)
        except Exception:
            # Older/enriched installations can omit one of the modelview
            # relations. Auction address points remain usable in that case.
            log.info("PVP asset cadastral references are unavailable", exc_info=True)
            return []
        return [
            (
                int(row["sale_id"]), str(row["municipality_name"]), str(row["province_code"]),
                str(row["sheet"]), str(row["parcel"]),
            )
            for row in rows
            if row["sale_id"] is not None and row["municipality_name"] and row["province_code"]
            and row["sheet"] and row["parcel"]
        ]

    async def _resolve_cadastral_points(
        self, references: list[tuple[int, str, str, str, str]]
    ) -> dict[int, tuple[float, float]]:
        """Resolve only unique municipality/sheet/parcel matches."""
        cadastral_source = get_map_layer_source().cadastral_connection_source
        if cadastral_source is None or not references:
            return {}
        # Repeated assets can name the same parcel; retain distinct sale keys
        # and ask Postgres to reject sales with more than one matching parcel.
        sale_ids, municipalities, provinces, sheets, parcels = zip(*set(references))
        sql = _asyncpg_sql("""
            WITH requested AS (
                SELECT DISTINCT *
                  FROM unnest(%s::bigint[], %s::text[], %s::text[], %s::text[], %s::text[])
                       AS key(sale_id, municipality_name, province_code, sheet, parcel)
            ), matches AS MATERIALIZED (
                SELECT requested.sale_id,
                       parcel.id,
                       ST_X(ST_PointOnSurface(parcel.geom)) AS longitude,
                       ST_Y(ST_PointOnSurface(parcel.geom)) AS latitude
                  FROM requested
                  JOIN spatial.cadastral_parcel AS parcel
                    ON lower(BTRIM(parcel.municipality_name)) = lower(requested.municipality_name)
                   AND upper(BTRIM(parcel.province)) = upper(requested.province_code)
                   AND upper(BTRIM(parcel.sheet)) = upper(requested.sheet)
                   AND upper(BTRIM(parcel.parcel)) = upper(requested.parcel)
            )
            SELECT sale_id, min(longitude) AS longitude, min(latitude) AS latitude
              FROM matches
             GROUP BY sale_id
            HAVING count(DISTINCT id) = 1
        """)
        try:
            async with cadastral_source.connection() as connection:
                async with connection.transaction():
                    await connection.execute("SET LOCAL statement_timeout = '120s'")
                    await connection.execute("SET LOCAL work_mem = '128MB'")
                    rows = await connection.fetch(
                        sql, sale_ids, municipalities, provinces, sheets, parcels, timeout=125
                    )
        except Exception:
            # Parcel enrichment is optional; never fail the auction map load.
            log.info("Cadastral parcel coordinate lookup unavailable", exc_info=True)
            return {}
        result = {}
        for row in rows:
            longitude, latitude = _finite(row["longitude"]), _finite(row["latitude"])
            if longitude is not None and latitude is not None and _in_italy(longitude, latitude):
                result[int(row["sale_id"])] = (longitude, latitude)
        return result

    @staticmethod
    def _with_cadastral_points(
        snapshot: dict[str, Any], cadastral_points: dict[int, tuple[float, float]]
    ) -> dict[str, Any]:
        """``snapshot`` with parcel-based positions, or ``snapshot`` itself if none apply.

        The input is never modified: requests may be reading it while the
        refined copy is built, and only the moved points are re-created.
        """
        if not cadastral_points:
            return snapshot
        points = list(snapshot["points"])
        updated = False
        for index, point in enumerate(points):
            parcel_point = cadastral_points.get(point[0])
            if parcel_point is None:
                continue
            longitude, latitude = parcel_point
            # PointOnSurface is a reliable pin within the parcel footprint,
            # while still being an approximation of the property's entrance.
            points[index] = [point[0], round(longitude, 5), round(latitude, 5), *point[3:6], 1, *point[7:]]
            updated = True
        if not updated:
            return snapshot
        return {**snapshot, "points": points, "spatial_index": PvpSalesStore._build_spatial_index(points)}

    @staticmethod
    def _build_spatial_index(points: list[list[Any]]) -> dict[tuple[int, int], list[int]]:
        index: dict[tuple[int, int], list[int]] = {}
        for point_index, point in enumerate(points):
            cell = (
                math.floor(point[2] / SPATIAL_CELL_DEGREES),
                math.floor(point[1] / SPATIAL_CELL_DEGREES),
            )
            index.setdefault(cell, []).append(point_index)
        return index

    @staticmethod
    def _build_snapshot(rows: Iterable[Any]) -> dict[str, Any]:
        points: list[list[Any]] = []
        days: list[Optional[date]] = []
        seen: set[Any] = set()
        for row in rows:
            sale_id = row["sale_id"]
            lat, lng = _finite(row["latitude"]), _finite(row["longitude"])
            if sale_id is None or sale_id in seen or lat is None or lng is None or not _in_italy(lng, lat):
                continue
            seen.add(sale_id)
            getter = row.get  # asyncpg Record and dict rows; absent columns read None
            price = _finite(getter("price"))
            day = _day(getter("sale_datetime"))
            points.append([
                int(sale_id), round(lng, 5), round(lat, 5),
                round(price) if price is not None and price > 0 else None,  # whole euros
                day.isoformat() if day else None,
                sale_category(getter("property_type"), getter("description_hint")),
                1 if getter("coordinate_is_approximate") else 0,
            ])
            days.append(day)
        shared = Counter((point[1], point[2]) for point in points)
        for point in points:
            if shared[(point[1], point[2])] >= APPROXIMATE_LOCATION_SHARE:
                point[6] = 1
        spatial_index = PvpSalesStore._build_spatial_index(points)
        return {
            "points": points,
            "days": days,
            "spatial_index": spatial_index,
            "loaded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }

    @staticmethod
    def _bbox_candidates(snapshot: dict[str, Any], bbox: tuple[float, float, float, float]) -> Iterable[int]:
        """Indexes of the points whose grid cell touches ``bbox`` (west, south, east, north).

        Falls back to every point when the snapshot has no spatial index or the
        box covers more cells than the index holds, so a national view does not
        walk millions of empty cells.
        """
        points = snapshot["points"]
        index = snapshot.get("spatial_index")
        if not index:
            return range(len(points))
        west, south, east, north = bbox
        lng0, lng1 = math.floor(west / SPATIAL_CELL_DEGREES), math.floor(east / SPATIAL_CELL_DEGREES)
        lat0, lat1 = math.floor(south / SPATIAL_CELL_DEGREES), math.floor(north / SPATIAL_CELL_DEGREES)
        if (lng1 - lng0 + 1) * (lat1 - lat0 + 1) > len(index):
            return (i for cell, items in index.items() if lat0 <= cell[0] <= lat1 and lng0 <= cell[1] <= lng1 for i in items)
        return (i for la in range(lat0, lat1 + 1) for lo in range(lng0, lng1 + 1) for i in index.get((la, lo), ()))

    async def map_points(
        self,
        *,
        period: str = "upcoming",
        categories: Optional[set[str]] = None,
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
        today: Optional[date] = None,
        wait: bool = False,
        refresh: bool = False,
        bbox: Optional[tuple[float, float, float, float]] = None,
        limit: Optional[int] = None,
    ) -> Optional[dict[str, Any]]:
        """Sales points, optionally restricted to a viewport.

        With ``bbox`` (west, south, east, north) only the points inside it are
        scanned and returned, capped at ``limit`` (nearest to the box centre
        first) with ``truncated`` set when more matched. Category counts then
        describe the viewport. Without ``bbox`` the national feed is returned
        unchanged for existing consumers.
        """
        snapshot = await (self.refresh() if refresh else self.snapshot(wait=wait))
        if snapshot is None:
            return None
        start, end = _period_start_end(period, today or date.today())
        selected: list[list[Any]] = []
        category_counts: Counter[str] = Counter()
        points_all, days_all = snapshot["points"], snapshot["days"]
        if bbox is not None:
            west, south, east, north = bbox
            iterator = ((points_all[i], days_all[i]) for i in self._bbox_candidates(snapshot, bbox))
        else:
            iterator = zip(points_all, days_all)
        for point, day in iterator:
            if bbox is not None and not (west <= point[1] <= east and south <= point[2] <= north):
                continue
            if start is not None and (day is None or day < start):
                continue
            if end is not None and (day is None or day > end):
                continue
            price = point[3]
            if min_price is not None and (price is None or price < min_price):
                continue
            if max_price is not None and (price is None or price > max_price):
                continue
            # Counts ignore the category filter so the type list stays usable.
            category_counts[point[5]] += 1
            if categories and point[5] not in categories:
                continue
            selected.append(point)
        matched = len(selected)
        truncated = False
        if bbox is not None and limit is not None and matched > limit:
            centre_lng, centre_lat = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
            selected = heapq.nsmallest(
                limit, selected,
                key=lambda point: (point[1] - centre_lng) ** 2 + (point[2] - centre_lat) ** 2,
            )
            truncated = True
        payload = {
            "source": {
                "relation": snapshot.get("relation", SALES_RELATION),
                "database": snapshot.get("database", "aecs4u-stats"),
                "loaded_at": snapshot["loaded_at"],
                "geocoded_sales": len(snapshot["points"]),
            },
            "period": period,
            "fields": list(POINT_FIELDS),
            "categories": [
                {"key": key, "label": CATEGORY_LABELS[key], "count": category_counts.get(key, 0)}
                for key in CATEGORY_KEYS
            ],
            "count": len(selected),
            "points": selected,
        }
        if bbox is not None:
            payload.update({"bbox": list(bbox), "matched": matched, "truncated": truncated})
        return payload

    async def nearby_points(
        self,
        *,
        lat: float,
        lng: float,
        radius_km: float,
        limit: int = 8,
        wait: bool = True,
    ) -> Optional[dict[str, Any]]:
        """Count nearby geocoded sales and return only the nearest ``limit`` points."""
        snapshot = await self.snapshot(wait=wait)
        if snapshot is None:
            return None
        return await asyncio.to_thread(
            self._nearby_points_from_snapshot, snapshot, lat, lng, radius_km, limit,
        )

    @staticmethod
    def _nearby_points_from_snapshot(
        snapshot: dict[str, Any], lat: float, lng: float, radius_km: float, limit: int,
    ) -> dict[str, Any]:
        lat_delta = radius_km / 110.574
        longitude_scale = max(abs(math.cos(math.radians(lat))), 0.01)
        lng_delta = radius_km / (111.320 * longitude_scale)
        min_lat_cell = math.floor((lat - lat_delta) / SPATIAL_CELL_DEGREES)
        max_lat_cell = math.floor((lat + lat_delta) / SPATIAL_CELL_DEGREES)
        min_lng_cell = math.floor((lng - lng_delta) / SPATIAL_CELL_DEGREES)
        max_lng_cell = math.floor((lng + lng_delta) / SPATIAL_CELL_DEGREES)
        spatial_index = snapshot.get("spatial_index") or {}
        points = snapshot["points"]
        nearest: list[tuple[float, int, int]] = []
        match_count = 0
        if spatial_index:
            candidates = (
                point_index
                for lat_cell in range(min_lat_cell, max_lat_cell + 1)
                for lng_cell in range(min_lng_cell, max_lng_cell + 1)
                for point_index in spatial_index.get((lat_cell, lng_cell), ())
            )
        else:
            candidates = range(len(points))
        phi1 = math.radians(lat)
        cosine_phi1 = math.cos(phi1)
        for point_index in candidates:
            point = points[point_index]
            point_lng, point_lat = point[1], point[2]
            phi2 = math.radians(point_lat)
            delta_phi = phi2 - phi1
            delta_lambda = math.radians(point_lng - lng)
            haversine = (
                math.sin(delta_phi / 2) ** 2
                + cosine_phi1 * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
            )
            haversine = min(1.0, max(0.0, haversine))
            distance_km = 6371.0088 * 2 * math.atan2(math.sqrt(haversine), math.sqrt(1 - haversine))
            if distance_km > radius_km:
                continue
            match_count += 1
            heap_item = (-distance_km, -point_index, point_index)
            if len(nearest) < limit:
                heapq.heappush(nearest, heap_item)
            elif distance_km < -nearest[0][0]:
                heapq.heapreplace(nearest, heap_item)

        selected = sorted(
            ((-negative_distance, point_index) for negative_distance, _negative_index, point_index in nearest),
            key=lambda item: item[0],
        )
        return {
            "source": {
                "relation": snapshot.get("relation", SALES_RELATION),
                "database": snapshot.get("database", "aecs4u-stats"),
                "loaded_at": snapshot["loaded_at"],
                "geocoded_sales": len(points),
            },
            "center": {"lat": lat, "lng": lng},
            "radius_km": radius_km,
            "count": match_count,
            "fields": [*POINT_FIELDS, "distance_km"],
            "points": [points[point_index] + [round(distance_km, 1)] for distance_km, point_index in selected],
        }

    async def sale_detail(self, sale_id: int) -> Optional[dict[str, Any]]:
        row = None
        wanted = []
        failures = []
        candidates = [(self._active_source, self._active_relation)] if self._active_source is not None else self._sources()
        for source, relation in candidates:
            try:
                async with source.connection() as connection:
                    columns = await self._columns(connection, relation)
                    wanted = [name for name in _DETAIL_COLUMNS if name in columns]
                    if "sale_id" not in wanted:
                        continue
                    async with connection.transaction():
                        await connection.execute(f"SET LOCAL statement_timeout = '{self.DETAIL_STATEMENT_TIMEOUT}'")
                        row = await connection.fetchrow(
                            f"SELECT {', '.join(wanted)} FROM {relation} WHERE sale_id = $1 LIMIT 1",
                            int(sale_id),
                            timeout=30,
                        )
                self._active_source, self._active_relation = source, relation
                break
            except Exception as exc:
                failures.append(str(exc))
        if row is None and failures and len(failures) == len(candidates):
            raise RuntimeError("PVP sale detail source unavailable: " + "; ".join(failures))
        if row is None:
            return None
        detail: dict[str, Any] = {}
        for name in wanted:
            value = row[name]
            if isinstance(value, (datetime, date)):
                value = value.isoformat()
            elif isinstance(value, float) and not math.isfinite(value):
                value = None
            elif isinstance(value, str):
                value = value.strip() or None
            detail[name] = value
        detail["category"] = sale_category(detail.get("property_type"), detail.get("description"))
        detail["category_label"] = CATEGORY_LABELS[detail["category"]]
        url = detail.get("url")
        # Relative links in the modelview point at unknown hosts; only
        # absolute web links are offered.
        detail["url"] = url if isinstance(url, str) and url.lower().startswith(("https://", "http://")) else None
        return detail


_store: Optional[PvpSalesStore] = None


def get_pvp_sales_store() -> PvpSalesStore:
    global _store
    if _store is None:
        _store = PvpSalesStore()
    return _store


async def warm_pvp_sales() -> None:
    """Load the sales copy after startup so the first toggle is immediate."""
    if os.getenv("MAP_SALES_WARMUP_ENABLED", "1").strip().lower() in {"0", "false", "no", "off"}:
        return
    try:
        await asyncio.sleep(max(0.0, float(os.getenv("MAP_SALES_WARMUP_DELAY_SECONDS", "30"))))
        store = get_pvp_sales_store()
        if store.available:
            await store.snapshot()
    except Exception as exc:
        log.warning("PVP sales warm-up failed: %s", exc)
