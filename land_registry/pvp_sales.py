"""Public-auction sales (Portale delle Vendite Pubbliche) for the direct map.

aecs4u-stats publishes the enriched PVP modelview as ``pvp.v_map_sales``: a
postgres_fdw table over ``pvp_enriched_modelview.public.v_map_sales``, one row
per sale with its first geocoded address (scripts/sql/
pvp-enriched-modelview-map-view.sql).  The view has ~800k sales, of which
~120k carry coordinates; nothing reaches the remote side as an index lookup,
so a full read takes ~15 s.  The map therefore reads the geocoded rows once,
keeps a compact in-memory copy (refreshed hourly, the last good copy kept if
a refresh fails) and filters that copy per request.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import time
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Optional

from land_registry.map_layers import _asyncpg_sql, get_map_layer_source

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
_POINT_COLUMNS = ("sale_id", "latitude", "longitude", "price", "sale_datetime", "property_type")
_DETAIL_COLUMNS = (
    "sale_id", "detail_id", "source", "property_type", "description", "price", "sale_datetime",
    "city", "province", "postal_code", "address", "street", "house_number", "url",
    "latitude", "longitude", "address_source", "geocoding_source",
)


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
        self.last_error: Optional[str] = None

    @property
    def connection_source(self) -> Any:
        if self._connection_source is None:
            self._connection_source = get_map_layer_source().connection_source
        return self._connection_source

    @property
    def available(self) -> bool:
        return bool(self.connection_source)

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

    async def _refresh(self) -> None:
        try:
            snapshot = await self._load()
        except Exception as exc:  # keep serving the previous copy
            self.last_error = str(exc)
            log.warning("PVP sales load from %s failed: %s", SALES_RELATION, exc)
            if self._snapshot is None:
                raise
            return
        self._snapshot = snapshot
        self._loaded_at = time.monotonic()
        self.last_error = None

    async def _columns(self, connection: Any) -> set[str]:
        rows = await connection.fetch(_asyncpg_sql("""
            SELECT attname::text AS name
            FROM pg_attribute
            WHERE attrelid = to_regclass(%s) AND attnum > 0 AND NOT attisdropped
        """), SALES_RELATION)
        return {row["name"] for row in rows}

    @staticmethod
    def _location_sql(columns: set[str]) -> tuple[str, str]:
        if {"latitude", "longitude"} <= columns:
            return "latitude", "longitude"
        if "geom" in columns:
            return "ST_Y(geom)", "ST_X(geom)"
        raise RuntimeError(f"{SALES_RELATION} exposes no latitude/longitude or geom column")

    async def _load(self) -> dict[str, Any]:
        started = time.perf_counter()
        west, south, east, north = ITALY_BOUNDS
        async with self.connection_source.connection() as connection:
            columns = await self._columns(connection)
            if not columns:
                raise RuntimeError(f"{SALES_RELATION} is not published in this database")
            if "sale_id" not in columns:
                raise RuntimeError(f"{SALES_RELATION} has no sale_id column")
            latitude, longitude = self._location_sql(columns)
            optional = [name for name in _POINT_COLUMNS[3:] if name in columns]
            if {"property_type", "description"} <= columns:
                # Built-in functions ship to the remote view, so only a short
                # prefix crosses the FDW, and only for untyped sales.
                optional.append(
                    "CASE WHEN NULLIF(btrim(property_type), '') IS NULL"
                    f" THEN left(description, {DESCRIPTION_HINT_LENGTH}) END AS description_hint"
                )
            select = ", ".join(["sale_id", f"{latitude} AS latitude", f"{longitude} AS longitude", *optional])
            # Plain float comparisons ship to the remote view; the Italy box
            # drops placeholder coordinates before they cross the FDW.
            sql = f"""
                SELECT {select}
                FROM {SALES_RELATION}
                WHERE {latitude} BETWEEN {south!r} AND {north!r}
                  AND {longitude} BETWEEN {west!r} AND {east!r}
            """
            async with connection.transaction():
                await connection.execute(f"SET LOCAL statement_timeout = '{self.LOAD_STATEMENT_TIMEOUT}'")
                rows = await connection.fetch(sql, timeout=330)
        snapshot = self._build_snapshot(rows)
        snapshot["columns"] = sorted(columns)
        log.info(
            "PVP sales loaded from %s: %d geocoded sales in %.0f ms",
            SALES_RELATION, len(snapshot["points"]), (time.perf_counter() - started) * 1000,
        )
        return snapshot

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
                0,
            ])
            days.append(day)
        shared = Counter((point[1], point[2]) for point in points)
        for point in points:
            if shared[(point[1], point[2])] >= APPROXIMATE_LOCATION_SHARE:
                point[6] = 1
        return {
            "points": points,
            "days": days,
            "loaded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }

    async def map_points(
        self,
        *,
        period: str = "upcoming",
        categories: Optional[set[str]] = None,
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
        today: Optional[date] = None,
        wait: bool = False,
    ) -> Optional[dict[str, Any]]:
        snapshot = await self.snapshot(wait=wait)
        if snapshot is None:
            return None
        start, end = _period_start_end(period, today or date.today())
        selected: list[list[Any]] = []
        category_counts: Counter[str] = Counter()
        for point, day in zip(snapshot["points"], snapshot["days"]):
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
        return {
            "source": {
                "relation": SALES_RELATION,
                "database": "aecs4u-stats",
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

    async def sale_detail(self, sale_id: int) -> Optional[dict[str, Any]]:
        async with self.connection_source.connection() as connection:
            columns = await self._columns(connection)
            wanted = [name for name in _DETAIL_COLUMNS if name in columns]
            if "sale_id" not in wanted:
                return None
            async with connection.transaction():
                await connection.execute(f"SET LOCAL statement_timeout = '{self.DETAIL_STATEMENT_TIMEOUT}'")
                row = await connection.fetchrow(
                    f"SELECT {', '.join(wanted)} FROM {SALES_RELATION} WHERE sale_id = $1 LIMIT 1",
                    int(sale_id),
                    timeout=30,
                )
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
