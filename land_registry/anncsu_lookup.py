"""Forward ANNCSU lookup: street name + civic number -> access point.

The regional stores live in the ``anncsu`` Postgres database as
``rNN__streets`` / ``rNN__addresses``.  Depending on the deployment they are
in schema ``anncsu`` or ``public``; the installed ``aecs4u_stats`` package only
reads ``public``, so this module resolves the schema itself and always
qualifies table names.  Table and schema names are built from a strict
allow-list, never from user input.

Everything here is synchronous (psycopg2); call it with ``asyncio.to_thread``.
"""

from __future__ import annotations

import difflib
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

from land_registry.address_search import ParsedAddress, normalize_street

logger = logging.getLogger(__name__)

ATTRIBUTION = "ANNCSU — Agenzia delle Entrate and ISTAT; CC BY 4.0"
_SCHEMAS = ("anncsu", "public")
_TTL_SECONDS = 3600
_FUZZY_MIN_RATIO = 0.84
_NEARBY_LIMIT = 6

_lock = threading.Lock()
_regions_cache: tuple[float, dict[int, str]] | None = None
_streets_cache: dict[tuple[int, str], tuple[float, list[dict[str, Any]]]] = {}
_region_of_istat: dict[str, int] = {}


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


def _connect():
    from aecs4u_stats._source_postgres import connect_source_database

    return connect_source_database("anncsu")


def _loaded_regions(conn) -> dict[int, str]:
    """``{region_code: schema}`` for every region with a streets and addresses table."""
    global _regions_cache
    now = time.monotonic()
    with _lock:
        if _regions_cache and now - _regions_cache[0] < _TTL_SECONDS:
            return _regions_cache[1]
    found: dict[int, set[str]] = {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_schema = ANY(%s) AND table_name ~ '^r[0-9]{2}__(streets|addresses)$'",
            (list(_SCHEMAS),),
        )
        tables: dict[tuple[str, int], set[str]] = {}
        for schema, name in cur.fetchall():
            tables.setdefault((schema, int(name[1:3])), set()).add(name[5:])
    regions: dict[int, str] = {}
    for schema in _SCHEMAS:  # earlier schema wins
        for (sch, code), kinds in tables.items():
            if sch == schema and {"streets", "addresses"} <= kinds and code not in regions:
                regions[code] = sch
    with _lock:
        _regions_cache = (now, regions)
    return regions


def _qualified(schema: str, region: int, kind: str) -> str:
    if schema not in _SCHEMAS or kind not in {"streets", "addresses"} or not 1 <= int(region) <= 99:
        raise ValueError("invalid ANNCSU table reference")
    return f'"{schema}"."r{int(region):02d}__{kind}"'


def _istat6(code: str) -> str:
    digits = re.sub(r"\D", "", str(code or ""))
    return digits.zfill(6)


def _streets_for_comune(conn, schema: str, region: int, istat: str) -> list[dict[str, Any]]:
    key = (region, istat)
    now = time.monotonic()
    with _lock:
        cached = _streets_cache.get(key)
        if cached and now - cached[0] < _TTL_SECONDS:
            return cached[1]
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT street_id, comune_code, odonimo, locality, total_accesses "
            f"FROM {_qualified(schema, region, 'streets')} WHERE istat_code = %s",
            (istat,),
        )
        rows = [
            {"street_id": r[0], "comune_code": r[1], "odonimo": r[2], "locality": r[3],
             "total_accesses": r[4] or 0}
            for r in cur.fetchall()
        ]
    for row in rows:
        row["type"], row["key"] = normalize_street(row["odonimo"])
    with _lock:
        if len(_streets_cache) > 256:
            _streets_cache.clear()
        _streets_cache[key] = (now, rows)
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


def _geocoded_accesses(conn, schema: str, region: int, comune_code: str, street_id: str) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT civic_number, exponent, longitude, latitude, coordinate_method "
            f"FROM {_qualified(schema, region, 'addresses')} "
            f"WHERE comune_code = %s AND street_id = %s "
            f"AND longitude IS NOT NULL AND latitude IS NOT NULL",
            (comune_code, street_id),
        )
        return [
            {"civic_number": r[0], "exponent": r[1], "longitude": float(r[2]),
             "latitude": float(r[3]), "coordinate_method": r[4]}
            for r in cur.fetchall()
        ]


def _region_for_istat(conn, regions: dict[int, str], istat: str) -> tuple[int, str] | None:
    cached = _region_of_istat.get(istat)
    if cached in regions:
        return cached, regions[cached]
    for code, schema in sorted(regions.items()):
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT 1 FROM {_qualified(schema, code, 'streets')} WHERE istat_code = %s LIMIT 1",
                (istat,),
            )
            if cur.fetchone():
                _region_of_istat[istat] = code
                return code, schema
    return None


def find_addresses(istat_code: str, parsed: ParsedAddress, limit: int = 3) -> list[AddressMatch]:
    """Resolve ``parsed`` inside one municipality; ``[]`` when nothing matches.

    Raises on database errors so the caller can tell "no match" from "source
    down".
    """
    istat = _istat6(istat_code)
    conn = _connect()
    try:
        regions = _loaded_regions(conn)
        located = _region_for_istat(conn, regions, istat)
        if not located:
            return []
        region, schema = located
        streets = _streets_for_comune(conn, schema, region, istat)
        matches: list[AddressMatch] = []
        for level, street in rank_streets(streets, parsed)[: max(limit, 1)]:
            accesses = _geocoded_accesses(conn, schema, region, street["comune_code"], street["street_id"])
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


# --- Parcel-panel reader: schema-aware twin of aecs4u_stats.anncsu.addresses_queries ----------
# The installed package only reads the regional tables from schema ``public``.
# Databases that keep them in ``anncsu`` get the same status/answer shape from
# these two functions; stats_service uses them only when the package reports
# the store as missing.

def region_status(region: Any) -> dict[str, Any] | None:
    """Status of a region's store in either schema; ``None`` when not loaded."""
    from aecs4u_stats.anncsu.config import anncsu_region_name, normalize_region_code

    code = normalize_region_code(region)
    conn = _connect()
    try:
        schema = _loaded_regions(conn).get(code)
        if not schema:
            return None
        provenance = f'"{schema}"."r{code:02d}__anncsu_provenance"'
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass(%s)", (provenance,))
            meta = None
            if cur.fetchone()[0]:
                cur.execute(
                    f"SELECT source_version, imported_at, source_vintage, access_last_modified, "
                    f"access_etag, row_count, geocoded_count FROM {provenance} LIMIT 1"
                )
                meta = cur.fetchone()
        count = max(0, int(meta[5] or 0)) if meta else 0
        imported = meta[1] if meta else None
        return {
            "region_code": code, "region_name": anncsu_region_name(code),
            "path": f"postgresql://anncsu/{schema}.r{code:02d}__addresses",
            "state": "ready" if count else "empty", "ready": bool(count),
            "row_count": count, "geocoded_count": max(0, int(meta[6] or 0)) if meta else 0,
            "source_version": meta[0] if meta else None,
            "updated_at": imported.isoformat() if hasattr(imported, "isoformat") else imported,
            "source_vintage": meta[2] if meta else None,
            "storage": "postgresql", "schema": schema,
        }
    finally:
        conn.close()


def civic_addresses_for_geometry(
    geometry: Any,
    region: Any,
    *,
    cadastral_code: str | None = None,
    radius_m: float = 50.0,
    result_limit: int = 100,
    candidate_limit: int = 10_000,
) -> dict[str, Any] | None:
    """Geocoded access points inside a parcel (or near a point); same keys as the package."""
    import math

    from aecs4u_stats.anncsu import addresses_queries as aq
    from aecs4u_stats.anncsu.config import (
        ANNCSU_LICENSE, ANNCSU_LICENSE_URL, ANNCSU_OPEN_DATA_PAGE, ANNCSU_SOURCE,
        anncsu_region_name, normalize_region_code,
    )
    from psycopg2 import sql
    from psycopg2.extras import RealDictCursor

    code = normalize_region_code(region)
    area = aq._geometry(geometry)
    if area.geom_type == "Point":
        dy = radius_m / 110_574.0
        dx = radius_m / max(1.0, 111_320.0 * abs(math.cos(math.radians(float(area.y)))))
        bounds = (float(area.x) - dx, float(area.y) - dy, float(area.x) + dx, float(area.y) + dy)
    else:
        bounds = tuple(float(v) for v in area.bounds)
    conn = _connect()
    try:
        schema = _loaded_regions(conn).get(code)
        if not schema or schema not in _SCHEMAS:
            return None
        ident = lambda kind: sql.Identifier(schema, f"r{code:02d}__{kind}")  # noqa: E731
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # PostGIS may be installed in the same schema as the tables.
            cur.execute("SET search_path TO public, anncsu")
            cur.execute(sql.SQL("SELECT to_regclass(%s) AS t"), (f'"{schema}"."r{code:02d}__anncsu_provenance"',))
            provenance = None
            if cur.fetchone()["t"]:
                cur.execute(sql.SQL("SELECT * FROM {} LIMIT 1").format(ident("anncsu_provenance")))
                provenance = cur.fetchone()
            cur.execute(
                sql.SQL(
                    "WITH parcel AS (SELECT ST_SetSRID(ST_GeomFromGeoJSON(%s), 4258) AS geom) "
                    "SELECT a.access_id, a.street_id, a.comune_code, a.istat_code, "
                    "s.odonimo, s.locality, a.civic_number, a.exponent, a.specificity, "
                    "a.metric_number, a.snc_progressive, a.longitude, a.latitude, "
                    "a.altitude_m, a.coordinate_method "
                    "FROM {} a CROSS JOIN parcel p LEFT JOIN {} s ON s.street_id = a.street_id "
                    "WHERE a.geom && ST_MakeEnvelope(%s, %s, %s, %s, 4258) "
                    "AND CASE WHEN GeometryType(p.geom) = 'POINT' "
                    "THEN ST_DWithin(a.geom::geography, p.geom::geography, %s) "
                    "ELSE ST_Covers(p.geom, a.geom) END "
                    "ORDER BY a.comune_code, a.street_id, a.civic_number, a.exponent, a.access_id LIMIT %s"
                ).format(ident("addresses"), ident("streets")),
                (aq.json_dumps_geometry(area), *bounds, radius_m, candidate_limit + 1),
            )
            rows = cur.fetchall()
            coverage = {"municipality_total_accesses": None, "municipality_geocoded_accesses": None,
                        "municipality_coordinate_coverage_pct": None}
            if cadastral_code:
                cur.execute(
                    sql.SQL("SELECT total_accesses::bigint AS t, geocoded_accesses::bigint AS g FROM {} "
                            "WHERE upper(comune_code) = upper(%s)").format(ident("municipality_coverage")),
                    (cadastral_code.strip().upper(),),
                )
                row = cur.fetchone()
                total, geocoded = (int(row["t"] or 0), int(row["g"] or 0)) if row else (0, 0)
                coverage = {"municipality_total_accesses": total, "municipality_geocoded_accesses": geocoded,
                            "municipality_coordinate_coverage_pct": round(100.0 * geocoded / total, 2) if total else None}
    except Exception as exc:
        logger.warning("ANNCSU schema-aware geometry query failed for region %s (%s)", code, type(exc).__name__)
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
        "address": aq._address_label(r), "longitude": r["longitude"], "latitude": r["latitude"],
        "altitude_m": r["altitude_m"], "coordinate_method": r["coordinate_method"],
        "coordinate_method_label": aq._METHOD_LABELS.get(str(r["coordinate_method"] or "")),
    } for r in shown]
    imported = provenance["imported_at"] if provenance else None
    pct = coverage["municipality_coordinate_coverage_pct"]
    return {
        "available": True, "matched": bool(addresses),
        "status": "partial" if (cap_reached or truncated) else "matched" if addresses else "no_geocoded_match",
        "addresses": addresses, "address_count": len(candidates), "shown_count": len(addresses),
        "truncated": truncated, "candidate_count": min(len(rows), candidate_limit),
        "candidate_limit": candidate_limit, "candidate_limit_reached": cap_reached, "result_limit": result_limit,
        "match_method": "geodesic_radius" if area.geom_type == "Point" else "point_in_parcel_polygon",
        "radius_m": radius_m if area.geom_type == "Point" else None,
        "region_code": code, "region_name": anncsu_region_name(code),
        "dataset_version": provenance["source_version"] if provenance else None,
        "source_vintage": provenance["source_vintage"] if provenance else None,
        "source_last_modified": provenance["access_last_modified"] if provenance else None,
        "source_etag": provenance["access_etag"] if provenance else None,
        "updated_at": imported.isoformat() if hasattr(imported, "isoformat") else imported,
        "source": ANNCSU_SOURCE, "source_url": ANNCSU_OPEN_DATA_PAGE,
        "license": ANNCSU_LICENSE, "license_url": ANNCSU_LICENSE_URL, "attribution": ATTRIBUTION,
        "spatial_resolution": "municipality-submitted civic access coordinates",
        "coordinate_crs": "EPSG:4258", "coordinate_method_codes": aq._METHOD_LABELS,
        "coverage_status": "unknown",
        "municipality_coordinate_coverage_status": (
            "unknown" if pct is None else "full" if pct >= 99.99 else "partial"),
        **coverage, "storage": "postgresql",
    }
