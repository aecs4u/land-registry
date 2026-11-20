"""Forward ANNCSU lookup: street name + civic number -> access point.

Reads the stable export schema ``anncsu_v2_ref`` of the local ``anncsu`` database
(views ``v_street``, ``v_address``, ``v_region``, ``v_municipality``,
``v_dataset_release``). Nothing here reads the internal ``anncsu_v2`` tables or the
deprecated legacy schemas, and nothing depends on the ``aecs4u_stats`` package.
All names are fixed in code, never built from user input.

PostGIS lives in the schema ``postgis`` of that database, so connections put it on the
``search_path`` after the export schema.

Everything here is synchronous (psycopg2); call it with ``asyncio.to_thread``.
"""

from __future__ import annotations

import difflib
import json
import logging
import os
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from land_registry.address_search import ParsedAddress, normalize_street
from land_registry.pg_ssl import plain_loopback_dsn

logger = logging.getLogger(__name__)

ATTRIBUTION = "ANNCSU — Agenzia delle Entrate and ISTAT; CC BY 4.0"
ANNCSU_SOURCE = "ANNCSU — Agenzia delle Entrate and ISTAT"
ANNCSU_LICENSE = "Creative Commons Attribution 4.0 International (CC BY 4.0)"
ANNCSU_LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
ANNCSU_OPEN_DATA_PAGE = "https://www.anncsu.gov.it/it/consultazione-dellarchivio/open-data/"
EXPORT_SCHEMA = "anncsu_v2_ref"
POSTGIS_SCHEMA = "postgis"
_SEARCH_PATH = f"{EXPORT_SCHEMA},{POSTGIS_SCHEMA}"
_DEFAULT_DSN = "postgresql://postgres@127.0.0.1:5432/anncsu"
_METHOD_LABELS = {
    "1": "field survey, estimated accuracy < 5 m",
    "2": "field survey, estimated accuracy ≥ 5 m",
    "3": "indirect mapping, estimated accuracy < 5 m",
    "4": "indirect mapping, estimated accuracy ≥ 5 m",
    "5": "indirect mapping via Portale per i Comuni",
}
_TTL_SECONDS = 3600
_FUZZY_MIN_RATIO = 0.84
_NEARBY_LIMIT = 6

_lock = threading.Lock()
_regions_cache: tuple[float, dict[int, dict[str, Any]]] | None = None
_streets_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


@dataclass
class AddressMatch:
    match_level: str            # address | address_exponent_dropped | street | fuzzy_street
    street: str
    street_id: str
    istat_code: str
    locality: str | None
    civic: str | None
    exponent: str | None
    latitude: float
    longitude: float
    coordinate_method: str | None
    nearby: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "match_level": self.match_level, "street": self.street, "street_id": self.street_id,
            "istat_code": self.istat_code, "locality": self.locality,
            "civic": self.civic, "exponent": self.exponent,
            "latitude": self.latitude, "longitude": self.longitude,
            "coordinate_method": self.coordinate_method, "nearby": self.nearby,
            "attribution": ATTRIBUTION,
        }


def anncsu_dsn() -> str:
    """DSN of the ``anncsu`` database (environment first, then the local default)."""
    for name in ("ANNCSU_POSTGRES_DSN", "AECS4U_SOURCE_ANNCSU_DATABASE_URL", "AECS4U_STATS_ANNCSU_DATABASE_URL"):
        value = os.getenv(name, "").strip()
        if value:
            return value.replace("postgresql+psycopg2://", "postgresql://", 1)
    base = os.getenv("STATS_POSTGRES_DSN", "").strip()
    if base:  # same host and credentials as the shared Stats DSN, database anncsu
        parsed = urlsplit(base.replace("postgresql+psycopg2://", "postgresql://", 1))
        return urlunsplit(parsed._replace(path="/anncsu"))
    return _DEFAULT_DSN


def _connect():
    import psycopg2

    return psycopg2.connect(
        plain_loopback_dsn(anncsu_dsn()), connect_timeout=4, options=f"-c search_path={_SEARCH_PATH}"
    )


def _region_key(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value).casefold())
    return "".join(char for char in decomposed if char.isalnum())


def _all_regions(conn) -> dict[int, dict[str, Any]]:
    """``{region_code: {name, total, geocoded}}`` from the export view (cached)."""
    global _regions_cache
    now = time.monotonic()
    with _lock:
        if _regions_cache and now - _regions_cache[0] < _TTL_SECONDS:
            return _regions_cache[1]
    with conn.cursor() as cur:
        cur.execute(f"SELECT region_code, name, total_accesses, geocoded_accesses FROM {EXPORT_SCHEMA}.v_region")
        regions = {
            int(code): {"name": name, "total": int(total or 0), "geocoded": int(geocoded or 0)}
            for code, name, total, geocoded in cur.fetchall()
        }
    with _lock:
        _regions_cache = (now, regions)
    return regions


def _loaded_regions(conn) -> dict[int, str]:
    """``{region_code: schema}`` for every region that has addresses loaded."""
    return {code: EXPORT_SCHEMA for code, info in _all_regions(conn).items() if info["total"] > 0}


def normalize_region_code(conn, region: Any) -> int:
    """ISTAT region code from a code or a name ("SICILIA", "Valle d'Aosta/Vallée d'Aoste")."""
    if isinstance(region, bool):
        raise ValueError("region must be an ISTAT code or region name")
    regions = _all_regions(conn)
    if isinstance(region, int) or str(region).strip().isdigit():
        code = int(region)
        if code in regions:
            return code
        raise ValueError(f"Unknown ISTAT region code: {region!r}")
    wanted = _region_key(region)
    for code, info in regions.items():
        key = _region_key(info["name"])
        if wanted == key or wanted == _region_key(str(info["name"]).split("/", 1)[0]):
            return code
    raise ValueError(f"Unknown ISTAT region name: {region!r}")


def _istat6(code: str) -> str:
    digits = re.sub(r"\D", "", str(code or ""))
    return digits.zfill(6)


def _streets_for_comune(conn, istat: str) -> list[dict[str, Any]]:
    now = time.monotonic()
    with _lock:
        cached = _streets_cache.get(istat)
        if cached and now - cached[0] < _TTL_SECONDS:
            return cached[1]
    with conn.cursor() as cur:
        # istat_code is the CURRENT ISTAT code; legacy codes are not needed for a lookup
        # by a code taken from today's ISTAT list.
        cur.execute(
            f"SELECT street_id, cadastral_code, name, locality, total_accesses "
            f"FROM {EXPORT_SCHEMA}.v_street WHERE istat_code = %s",
            (istat,),
        )
        rows = [
            {"street_id": str(r[0]), "comune_code": r[1], "odonimo": r[2], "locality": r[3],
             "total_accesses": r[4] or 0}
            for r in cur.fetchall()
        ]
    for row in rows:
        row["type"], row["key"] = normalize_street(row["odonimo"])
    with _lock:
        if len(_streets_cache) > 256:
            _streets_cache.clear()
        _streets_cache[istat] = (now, rows)
    return rows


def rank_streets(streets: list[dict[str, Any]], parsed: ParsedAddress) -> list[tuple[str, dict[str, Any]]]:
    """Registry streets matching ``parsed`` as ``(level, street)``, best first.

    Levels: ``exact`` (same type and name), ``name`` (same name, other or
    missing type), ``tokens`` (all query words present), ``fuzzy``.
    """
    key = parsed.street
    q_tokens = set(key.split())
    exact, name, tokens, fuzzy = [], [], [], []
    for street in streets:
        s_key = street["key"]
        if s_key == key:
            if not parsed.street_type or street["type"] == parsed.street_type:
                exact.append(street)
            else:
                name.append(street)
        elif q_tokens and q_tokens <= set(s_key.split()):
            tokens.append(street)
        elif key and len(key) >= 4:
            ratio = difflib.SequenceMatcher(None, key, s_key).ratio()
            if ratio >= _FUZZY_MIN_RATIO:
                fuzzy.append((ratio, street))
    by_size = lambda s: -s["total_accesses"]  # noqa: E731
    ranked = (
        [("exact", s) for s in sorted(exact, key=by_size)]
        + [("name", s) for s in sorted(name, key=by_size)]
        + [("tokens", s) for s in sorted(tokens, key=by_size)]
        + [("fuzzy", s) for _, s in sorted(fuzzy, key=lambda x: -x[0])]
    )
    return ranked


def _civic_sort_key(number: str | None) -> tuple[int, str]:
    digits = re.match(r"\d+", str(number or ""))
    return (int(digits.group()) if digits else 10**9, str(number or ""))


def pick_civic(
    rows: list[dict[str, Any]], civic: str | None, exponent: str | None
) -> tuple[str, dict[str, Any] | None, list[str]]:
    """Choose the access point for a civic number from a street's geocoded rows.

    Returns ``(level, row, nearby_labels)`` where level is ``address``,
    ``address_exponent_dropped`` or ``street`` (no usable civic match; ``row``
    is then the street's most central geocoded access).
    """
    def label(r: dict[str, Any]) -> str:
        return f"{r['civic_number']}/{r['exponent']}" if r.get("exponent") else str(r["civic_number"])

    if not rows:
        return "street", None, []
    ordered = sorted(rows, key=lambda r: (_civic_sort_key(r["civic_number"]), str(r.get("exponent") or "")))
    if civic:
        same = [r for r in ordered if str(r["civic_number"]).strip().upper() == civic.upper()]
        if same:
            wanted = (exponent or "").upper()
            exact = [r for r in same if str(r.get("exponent") or "").upper() == wanted]
            if exact:
                return "address", exact[0], []
            if not wanted:
                return "address", same[0], []
            return "address_exponent_dropped", same[0], [label(r) for r in same][:_NEARBY_LIMIT]
        target = _civic_sort_key(civic)[0]
        near = sorted(ordered, key=lambda r: abs(_civic_sort_key(r["civic_number"])[0] - target))
        nearby = []
        for r in near:
            if label(r) not in nearby:
                nearby.append(label(r))
            if len(nearby) >= _NEARBY_LIMIT:
                break
        return "street", None, nearby
    return "street", None, []


def _street_centroid(rows: list[dict[str, Any]]) -> tuple[float, float] | None:
    if not rows:
        return None
    lons = sorted(r["longitude"] for r in rows)
    lats = sorted(r["latitude"] for r in rows)
    mid = len(rows) // 2
    return lats[mid], lons[mid]


def _geocoded_accesses(conn, street_id: str) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT civic_number, exponent, longitude, latitude, coordinate_method "
            f"FROM {EXPORT_SCHEMA}.v_address "
            f"WHERE street_id = %s AND longitude IS NOT NULL AND latitude IS NOT NULL",
            (int(street_id),),
        )
        return [
            {"civic_number": r[0], "exponent": r[1], "longitude": float(r[2]),
             "latitude": float(r[3]), "coordinate_method": None if r[4] is None else str(r[4])}
            for r in cur.fetchall()
        ]


def find_addresses(istat_code: str, parsed: ParsedAddress, limit: int = 3) -> list[AddressMatch]:
    """Resolve ``parsed`` inside one municipality; ``[]`` when nothing matches.

    Raises on database errors so the caller can tell "no match" from "source
    down".
    """
    istat = _istat6(istat_code)
    conn = _connect()
    try:
        streets = _streets_for_comune(conn, istat)
        matches: list[AddressMatch] = []
        for level, street in rank_streets(streets, parsed)[: max(limit, 1)]:
            accesses = _geocoded_accesses(conn, street["street_id"])
            if not accesses:
                continue
            picked_level, row, nearby = pick_civic(accesses, parsed.civic, parsed.exponent)
            if row is None:
                centre = _street_centroid(accesses)
                if centre is None:
                    continue
                lat, lon, method = centre[0], centre[1], None
                civic = exponent = None
                match_level = "street" if level in ("exact", "name", "tokens") else "fuzzy_street"
            else:
                lat, lon, method = row["latitude"], row["longitude"], row["coordinate_method"]
                civic, exponent = str(row["civic_number"]), row.get("exponent")
                match_level = picked_level if level in ("exact", "name", "tokens") else "fuzzy_street"
            matches.append(AddressMatch(
                match_level=match_level, street=street["odonimo"], street_id=str(street["street_id"]),
                istat_code=istat, locality=street.get("locality"), civic=civic, exponent=exponent,
                latitude=lat, longitude=lon, coordinate_method=method, nearby=nearby,
            ))
            if match_level == "address":
                break  # an exact access beats the weaker streets that follow
        return matches
    finally:
        conn.close()


# --- Parcel-panel reader -------------------------------------------------------------------
# stats_service uses these two functions for the parcel panel; they answer from the
# export schema and return the status/answer shape the panel already understands.

def _vintage_text(value: Any) -> Any:
    """``YYYYMMDD`` text, the form the panel and the legacy provenance used."""
    return value.strftime("%Y%m%d") if hasattr(value, "strftime") else value


def _release_for(cur, code: int) -> dict[str, Any] | None:
    """Newest dataset release covering ``code`` (the region's own, else a national one)."""
    cur.execute(
        f"SELECT source_version, source_vintage, imported_at, access_last_modified, access_etag, "
        f"row_count, geocoded_count FROM {EXPORT_SCHEMA}.v_dataset_release "
        f"WHERE region_code = %s OR region_code IS NULL "
        f"ORDER BY (region_code IS NULL), imported_at DESC NULLS LAST LIMIT 1",
        (code,),
    )
    row = cur.fetchone()
    if not row:
        return None
    keys = ("source_version", "source_vintage", "imported_at", "access_last_modified", "access_etag",
            "row_count", "geocoded_count")
    return dict(zip(keys, row))


def region_status(region: Any) -> dict[str, Any] | None:
    """Status of a region's address store; ``None`` when not loaded."""
    conn = _connect()
    try:
        code = normalize_region_code(conn, region)
        info = _all_regions(conn)[code]
        if info["total"] <= 0:
            return None
        with conn.cursor() as cur:
            release = _release_for(cur, code) or {}
        count = info["total"]  # counted from the data itself, not from a stored provenance row
        imported = release.get("imported_at")
        return {
            "region_code": code, "region_name": info["name"],
            "path": f"postgresql://anncsu/{EXPORT_SCHEMA}.v_address",
            "state": "ready" if count else "empty", "ready": bool(count),
            "row_count": count, "geocoded_count": info["geocoded"],
            "source_version": release.get("source_version"),
            "updated_at": imported.isoformat() if hasattr(imported, "isoformat") else imported,
            "source_vintage": _vintage_text(release.get("source_vintage")),
            "storage": "postgresql", "schema": EXPORT_SCHEMA,
        }
    finally:
        conn.close()


def _geometry(value: Any):
    """Shapely geometry in EPSG:4258 from an EPSG:4326 GeoJSON Point/Polygon/MultiPolygon."""
    from pyproj import Transformer
    from shapely.geometry import shape
    from shapely.ops import transform

    geometry = value.get("geometry") if isinstance(value, dict) and value.get("type") == "Feature" else value
    if not isinstance(geometry, dict) or geometry.get("type") not in {"Polygon", "MultiPolygon", "Point"}:
        raise ValueError("geometry must be an EPSG:4326 GeoJSON Point, Polygon or MultiPolygon")
    try:
        result = shape(geometry)
    except Exception as exc:
        raise ValueError("geometry is not valid GeoJSON") from exc
    if result.is_empty:
        raise ValueError("geometry must not be empty")
    if not result.is_valid and result.geom_type != "Point":
        result = result.buffer(0)
    if result.is_empty or (result.geom_type != "Point" and not result.is_valid):
        raise ValueError("geometry must be valid")
    return transform(Transformer.from_crs("EPSG:4326", "EPSG:4258", always_xy=True).transform, result)


def _dumps_geometry(geometry: Any) -> str:
    from shapely.geometry import mapping

    return json.dumps(mapping(geometry), ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _address_label(row: Any) -> str:
    number = row["civic_number"] or row["metric_number"] or ""
    if row["exponent"]:
        number = f"{number}/{row['exponent']}" if number != "" else row["exponent"]
    number = str(number)
    if row["specificity"]:
        number = f"{number} {row['specificity']}".strip()
    if row["snc_progressive"]:
        number = f"{number} SNC {row['snc_progressive']}".strip()
    return " ".join(part for part in (row["odonimo"] or "", number, row["locality"] or "") if part)


def civic_addresses_for_geometry(
    geometry: Any,
    region: Any,
    *,
    cadastral_code: str | None = None,
    radius_m: float = 50.0,
    result_limit: int = 100,
    candidate_limit: int = 10_000,
) -> dict[str, Any] | None:
    """Geocoded access points inside a parcel (or near a point), keyed like the panel expects."""
    import math

    from psycopg2.extras import RealDictCursor

    area = _geometry(geometry)
    if area.geom_type == "Point":
        dy = radius_m / 110_574.0
        dx = radius_m / max(1.0, 111_320.0 * abs(math.cos(math.radians(float(area.y)))))
        bounds = (float(area.x) - dx, float(area.y) - dy, float(area.x) + dx, float(area.y) + dy)
    else:
        bounds = tuple(float(v) for v in area.bounds)
    conn = _connect()
    try:
        code = normalize_region_code(conn, region)
        info = _all_regions(conn)[code]
        if info["total"] <= 0:
            return None
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            release = _release_for(cur, code)
            cur.execute(
                f"WITH parcel AS (SELECT ST_SetSRID(ST_GeomFromGeoJSON(%s), 4258) AS geom) "
                f"SELECT a.access_id, a.street_id, a.cadastral_code AS comune_code, a.istat_code, "
                f"a.street AS odonimo, a.locality, a.civic_number, a.exponent, a.specificity, "
                f"a.metric_number, a.snc_progressive, a.longitude, a.latitude, "
                f"a.altitude_m, a.coordinate_method "
                f"FROM {EXPORT_SCHEMA}.v_address a CROSS JOIN parcel p "
                f"WHERE a.geom && ST_MakeEnvelope(%s, %s, %s, %s, 4258) AND a.region_code = %s "
                f"AND CASE WHEN GeometryType(p.geom) = 'POINT' "
                f"THEN ST_DWithin(a.geom::geography, p.geom::geography, %s) "
                f"ELSE ST_Covers(p.geom, a.geom) END "
                f"ORDER BY a.cadastral_code, a.street_id, a.civic_number, a.exponent, a.access_id LIMIT %s",
                (_dumps_geometry(area), *bounds, code, radius_m, candidate_limit + 1),
            )
            rows = cur.fetchall()
            coverage = {"municipality_total_accesses": None, "municipality_geocoded_accesses": None,
                        "municipality_coordinate_coverage_pct": None}
            if cadastral_code:
                cur.execute(
                    f"SELECT total_accesses::bigint AS t, geocoded_accesses::bigint AS g "
                    f"FROM {EXPORT_SCHEMA}.v_municipality WHERE upper(cadastral_code) = upper(%s)",
                    (cadastral_code.strip().upper(),),
                )
                row = cur.fetchone()
                total, geocoded = (int(row["t"] or 0), int(row["g"] or 0)) if row else (0, 0)
                coverage = {"municipality_total_accesses": total, "municipality_geocoded_accesses": geocoded,
                            "municipality_coordinate_coverage_pct": round(100.0 * geocoded / total, 2) if total else None}
    except Exception as exc:
        logger.warning("ANNCSU geometry query failed for region %s (%s)", region, type(exc).__name__)
        return None
    finally:
        conn.close()

    cap_reached = len(rows) > candidate_limit
    candidates = rows[:candidate_limit]
    truncated = len(candidates) > result_limit
    shown = candidates[:result_limit]
    addresses = [{
        "access_id": r["access_id"], "street_id": r["street_id"], "comune_code": r["comune_code"],
        "istat_code": r["istat_code"], "street": r["odonimo"], "locality": r["locality"],
        "civic_number": r["civic_number"], "exponent": r["exponent"], "specificity": r["specificity"],
        "metric_number": r["metric_number"], "snc_progressive": r["snc_progressive"],
        "address": _address_label(r), "longitude": r["longitude"], "latitude": r["latitude"],
        "altitude_m": r["altitude_m"], "coordinate_method": None if r["coordinate_method"] is None else str(r["coordinate_method"]),
        "coordinate_method_label": _METHOD_LABELS.get(str(r["coordinate_method"] or "")),
    } for r in shown]
    release = release or {}
    imported = release.get("imported_at")
    pct = coverage["municipality_coordinate_coverage_pct"]
    return {
        "available": True, "matched": bool(addresses),
        "status": "partial" if (cap_reached or truncated) else "matched" if addresses else "no_geocoded_match",
        "addresses": addresses, "address_count": len(candidates), "shown_count": len(addresses),
        "truncated": truncated, "candidate_count": min(len(rows), candidate_limit),
        "candidate_limit": candidate_limit, "candidate_limit_reached": cap_reached, "result_limit": result_limit,
        "match_method": "geodesic_radius" if area.geom_type == "Point" else "point_in_parcel_polygon",
        "radius_m": radius_m if area.geom_type == "Point" else None,
        "region_code": code, "region_name": info["name"],
        "dataset_version": release.get("source_version"),
        "source_vintage": _vintage_text(release.get("source_vintage")),
        "source_last_modified": release.get("access_last_modified"),
        "source_etag": release.get("access_etag"),
        "updated_at": imported.isoformat() if hasattr(imported, "isoformat") else imported,
        "source": ANNCSU_SOURCE, "source_url": ANNCSU_OPEN_DATA_PAGE,
        "license": ANNCSU_LICENSE, "license_url": ANNCSU_LICENSE_URL, "attribution": ATTRIBUTION,
        "spatial_resolution": "municipality-submitted civic access coordinates",
        "coordinate_crs": "EPSG:4258", "coordinate_method_codes": _METHOD_LABELS,
        "coverage_status": "unknown",
        "municipality_coordinate_coverage_status": (
            "unknown" if pct is None else "full" if pct >= 99.99 else "partial"),
        **coverage, "storage": "postgresql",
    }
