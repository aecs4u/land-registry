"""Map contracts for the regional tables in the dedicated cadastral database."""

from __future__ import annotations

import re
from typing import Any

REGIONAL_TABLE = re.compile(r"^[a-z][a-z_]*__(?:fogli|particelle)$")
CADASTRAL_LAYER_IDS = frozenset({"cadastral-sheets", "cadastral-parcels"})
REGIONAL_PROPERTIES = ("region", "province", "municipality_code", "municipality_name", "inspire_localid")


async def regional_health(connection: Any, layer_id: str, relation: str) -> dict[str, Any]:
    """Check the union view's source tables and their actual geometry indexes."""
    rows = await connection.fetch("""
        WITH sources AS (
            SELECT DISTINCT c.oid, n.nspname, c.relname, c.reltuples
            FROM pg_rewrite rw
            JOIN pg_depend d ON d.classid = 'pg_rewrite'::regclass AND d.objid = rw.oid
                AND d.refclassid = 'pg_class'::regclass
            JOIN pg_class c ON c.oid = d.refobjid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE rw.ev_class = to_regclass($1) AND c.relkind IN ('r', 'p')
        )
        SELECT s.relname, greatest(s.reltuples, 0)::bigint AS row_estimate,
               a.attnum IS NOT NULL AS geometry_column_exists,
               coalesce(postgis_typmod_srid(a.atttypmod), 0) AS srid,
               EXISTS (
                   SELECT 1 FROM pg_index i
                   JOIN pg_class idx ON idx.oid = i.indexrelid
                   JOIN pg_am am ON am.oid = idx.relam
                   WHERE i.indrelid = s.oid AND i.indisvalid
                       AND am.amname = 'gist' AND a.attnum = ANY(i.indkey)
               ) AS has_gist,
               ST_XMin(ST_EstimatedExtent(s.nspname, s.relname, 'geom')) AS west,
               ST_YMin(ST_EstimatedExtent(s.nspname, s.relname, 'geom')) AS south,
               ST_XMax(ST_EstimatedExtent(s.nspname, s.relname, 'geom')) AS east,
               ST_YMax(ST_EstimatedExtent(s.nspname, s.relname, 'geom')) AS north
        FROM sources s
        LEFT JOIN pg_attribute a ON a.attrelid = s.oid AND a.attname = 'geom'
            AND a.attnum > 0 AND NOT a.attisdropped
        ORDER BY s.relname
    """, relation)
    geometry_exists = bool(rows) and all(row["geometry_column_exists"] for row in rows)
    srid_matches = bool(rows) and all(row["srid"] == 4326 for row in rows)
    indexed = bool(rows) and all(row["has_gist"] for row in rows)
    result = {
        "id": layer_id, "available": geometry_exists and srid_matches and indexed,
        "relation_exists": bool(rows), "geometry_column_exists": geometry_exists,
        "gist_index_exists": indexed, "requires_gist_index": True,
        "geometry_srid": 4326 if srid_matches else 0, "expected_srid": 4326,
        "srid_matches": srid_matches, "row_estimate": sum(row["row_estimate"] for row in rows),
        "coverage": "unknown", "coverage_note": "", "coverage_bounds": None,
        "source_database": "cadastral", "source_table_count": len(rows),
        "index_scope": "regional source tables",
    }
    # Statistics describe an approximate extent, not complete national coverage.
    extents = [row for row in rows if all(row[k] is not None for k in ("west", "south", "east", "north"))]
    if extents:
        result["estimated_bounds"] = [
            min(float(row["west"]) for row in extents), min(float(row["south"]) for row in extents),
            max(float(row["east"]) for row in extents), max(float(row["north"]) for row in extents),
        ]
        result["extent_source"] = "PostGIS geometry statistics"
    return result


def identity_sql(layer_id: str, reference_sql: str) -> str:
    """Stable, positive feature IDs that fit exactly in a JavaScript number.

    Regional imports have no surrogate primary key. Use the national entity
    reference so reloads and new imports preserve links; separate polygon
    parts of the same cadastral entity intentionally share its identity.
    Reference lookups also check the original reference to guard hash collisions.
    """
    if layer_id not in CADASTRAL_LAYER_IDS:
        raise ValueError("Unknown cadastral layer")
    return f"(('x' || substr(md5('{layer_id}:' || {reference_sql}), 1, 13))::bit(52)::bigint + 1)"


def view_branch(table: str) -> str:
    """Normalize one catalog-validated regional source without copying rows."""
    if not REGIONAL_TABLE.fullmatch(table):
        raise ValueError("Invalid regional cadastral table")
    parcels = table.endswith("__particelle")
    layer_id = "cadastral-parcels" if parcels else "cadastral-sheets"
    reference = "national_cadastral_reference" if parcels else "national_zoning_reference"
    identity = identity_sql(layer_id, reference)
    specific = (
        "national_cadastral_reference AS canonical_reference, national_cadastral_reference, "
        "parcel_number AS parcel, sheet_number AS sheet"
        if parcels else
        "national_zoning_reference AS sheet_reference, level, level_name"
    )
    parcel_flags = ", has_visura, is_auction_sale" if parcels else ""
    return (
        f"SELECT {identity} AS id, {specific}, NULL::bigint AS municipality_id, "
        f"area_sqm, '{table}'::text AS source_release, region, province, "
        f"municipality_code, municipality_name, inspire_localid, geom{parcel_flags} "
        f"FROM public.\"{table}\""
    )
