#!/usr/bin/env python3
"""Refresh the persisted SISTER ``has_visura`` flag on cadastral parcels.

Run ``scripts/prepare_cadastral_map.py`` first when parcels are in the
dedicated ``cadastral`` database. The refresh reads SISTER from the stats
database and commits updates in bounded batches.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import land_registry.stats_service as stats_service
from land_registry.cadastral_map_views import REGIONAL_TABLE, identity_sql
from land_registry.map_layers import get_map_layer_source


async def _municipality_names(codes: list[str], cache: dict) -> dict:
    for code in codes:
        if code in cache:
            continue
        municipality = await stats_service.aget_municipality_by_cadastral_code(code)
        if municipality:
            cache[code] = (
                str(municipality.get("name") or municipality.get("official_name") or ""),
                (str(municipality.get("province") or ""), str(municipality.get("province_sigla") or "")),
            )
    return {code: cache[code] for code in codes if code in cache}


async def _persist_batch(connection, *, target_kind: str, table: str, features: list[dict], flags: dict, dry_run: bool) -> int:
    resolved = {
        feature["id"]: flags[feature["id"]]
        for feature in features
        if feature["id"] in flags
    }
    if not resolved or dry_run:
        return len(resolved)

    if target_kind == "canonical":
        refs = {
            str(feature["properties"].get("national_cadastral_reference")
                or feature["properties"].get("canonical_reference")): flags[feature["id"]]
            for feature in features
            if feature["id"] in flags
            and (feature["properties"].get("national_cadastral_reference")
                 or feature["properties"].get("canonical_reference"))
        }
        if not refs:
            return 0
        statement = """
            WITH updated AS (
                UPDATE spatial.cadastral_parcel AS parcel
                SET has_visura = status_rows.has_visura
                FROM unnest($1::text[], $2::boolean[]) AS status_rows(reference, has_visura)
                WHERE coalesce(parcel.national_cadastral_reference, parcel.canonical_reference) = status_rows.reference
                  AND parcel.has_visura IS DISTINCT FROM status_rows.has_visura
                RETURNING 1
            ) SELECT count(*) FROM updated
        """
        return int(await connection.fetchval(statement, list(refs), list(refs.values())))

    refs = {}
    for feature in features:
        feature_id = feature["id"]
        if feature_id in resolved:
            reference = feature["properties"].get("national_cadastral_reference")
            if reference:
                refs[str(reference)] = resolved[feature_id]
    if not refs:
        return 0
    statement = f"""
        WITH updated AS (
            UPDATE public."{table}" AS parcel
            SET has_visura = status_rows.has_visura
            FROM unnest($1::text[], $2::boolean[]) AS status_rows(reference, has_visura)
            WHERE parcel.national_cadastral_reference = status_rows.reference
              AND parcel.has_visura IS DISTINCT FROM status_rows.has_visura
            RETURNING 1
        ) SELECT count(*) FROM updated
    """
    return int(await connection.fetchval(statement, list(refs), list(refs.values())))


async def _refresh_canonical(source, connection, *, batch_size: int, dry_run: bool, names_cache: dict) -> tuple[int, int]:
    if not dry_run:
        await connection.execute("ALTER TABLE spatial.cadastral_parcel ADD COLUMN IF NOT EXISTS has_visura boolean")
    cursor_id, cursor_reference = 0, ""
    visited = updated = 0
    while True:
        rows = await connection.fetch("""
            SELECT id, canonical_reference, national_cadastral_reference, parcel, sheet
            FROM spatial.cadastral_parcel
            WHERE (id, coalesce(national_cadastral_reference, canonical_reference, '')) > ($1, $2)
            ORDER BY id, coalesce(national_cadastral_reference, canonical_reference, '')
            LIMIT $3
        """, cursor_id, cursor_reference, batch_size)
        if not rows:
            break
        features = [{
            "id": str(row["national_cadastral_reference"] or row["canonical_reference"]),
            "properties": dict(row),
        } for row in rows]
        codes = source.visura_candidate_codes(features)
        names = await _municipality_names(codes, names_cache)
        flags = await source.read_visura_flags(features, names) if names else {}
        if flags is None and names:
            raise RuntimeError("SISTER visura data is unavailable; the batch was not changed")
        flags = flags or {}
        visited += len(features)
        updated += await _persist_batch(
            connection, target_kind="canonical", table="", features=features,
            flags=flags, dry_run=dry_run,
        )
        cursor_id = int(rows[-1]["id"])
        cursor_reference = str(rows[-1]["national_cadastral_reference"] or rows[-1]["canonical_reference"] or "")
        print(f"spatial.cadastral_parcel: scanned={visited} updated={updated} last_id={cursor_id}", flush=True)
    return visited, updated


async def _refresh_regional(source, connection, *, batch_size: int, dry_run: bool, names_cache: dict) -> tuple[int, int]:
    tables = await connection.fetch("""
        SELECT c.relname
        FROM pg_class AS c
        JOIN pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
          AND c.relname ~ '^[a-z][a-z_]*__particelle$'
        ORDER BY c.relname
    """)
    table_names = [row["relname"] for row in tables if REGIONAL_TABLE.fullmatch(row["relname"])]
    if not table_names:
        raise RuntimeError("No regional parcel tables found; prepare the cadastral map source first")

    visited = updated = 0
    for table in table_names:
        columns = {row["column_name"] for row in await connection.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = $1",
            table,
        )}
        if "has_visura" not in columns:
            raise RuntimeError(f'public."{table}" has no has_visura column; run scripts/prepare_cadastral_map.py first')
        expression = identity_sql("cadastral-parcels", "national_cadastral_reference")
        cursor_id, cursor_reference = 0, ""
        table_visited = table_updated = 0
        while True:
            rows = await connection.fetch(f"""
                SELECT DISTINCT ON ({expression}, national_cadastral_reference)
                       {expression} AS page_id,
                       national_cadastral_reference,
                       national_cadastral_reference AS canonical_reference,
                       parcel_number AS parcel,
                       sheet_number AS sheet,
                       municipality_code
                FROM public."{table}"
                WHERE national_cadastral_reference IS NOT NULL
                  AND ({expression}, national_cadastral_reference) > ($1, $2)
                ORDER BY {expression}, national_cadastral_reference, inspire_localid
                LIMIT $3
            """, cursor_id, cursor_reference, batch_size)
            if not rows:
                break
            features = [{
                "id": str(row["national_cadastral_reference"]),
                "properties": dict(row),
            } for row in rows]
            codes = source.visura_candidate_codes(features)
            names = await _municipality_names(codes, names_cache)
            flags = await source.read_visura_flags(features, names) if names else {}
            if flags is None and names:
                raise RuntimeError("SISTER visura data is unavailable; the batch was not changed")
            flags = flags or {}
            table_visited += len(features)
            table_updated += await _persist_batch(
                connection, target_kind="regional", table=table, features=features,
                flags=flags, dry_run=dry_run,
            )
            cursor_id = int(rows[-1]["page_id"])
            cursor_reference = str(rows[-1]["national_cadastral_reference"])
            print(f"{table}: scanned={table_visited} updated={table_updated} last_id={cursor_id}", flush=True)
        visited += table_visited
        updated += table_updated
    return visited, updated


async def run(*, batch_size: int, dry_run: bool) -> dict[str, int]:
    import asyncpg

    source = get_map_layer_source()
    if source.connection_source is None:
        raise RuntimeError("Enable STATS_POSTGRES_ENABLE and configure STATS_POSTGRES_DSN")
    async with source.connection_source.connection() as stats_connection:
        sister_available = await stats_connection.fetchval(
            "SELECT to_regclass('sister.v_sister_document_by_cadastral_parcel') IS NOT NULL"
        )
    if not sister_available:
        raise RuntimeError("SISTER document view is unavailable")

    canonical_connection = None
    async with source.connection_source.connection() as stats_connection:
        relation_kind = await stats_connection.fetchval("""
            SELECT c.relkind
            FROM pg_class AS c
            WHERE c.oid = to_regclass('spatial.cadastral_parcel')
        """)
        has_rows = bool(relation_kind and await stats_connection.fetchval(
            "SELECT EXISTS (SELECT 1 FROM spatial.cadastral_parcel LIMIT 1)"
        ))
    if relation_kind in ("r", "p") and has_rows:
        canonical_connection = await asyncpg.connect(source.connection_source.dsn, timeout=10)

    regional_connection = None
    if canonical_connection is None:
        regional_source = source.cadastral_connection_source
        if regional_source is None:
            raise RuntimeError("Configure CADASTRAL_POSTGRES_DSN or the dedicated cadastral database")
        regional_connection = await asyncpg.connect(regional_source.dsn, timeout=10)

    names_cache = {}
    try:
        if canonical_connection is not None:
            visited, updated = await _refresh_canonical(
                source, canonical_connection, batch_size=batch_size,
                dry_run=dry_run, names_cache=names_cache,
            )
        else:
            visited, updated = await _refresh_regional(
                source, regional_connection, batch_size=batch_size,
                dry_run=dry_run, names_cache=names_cache,
            )
    finally:
        if canonical_connection is not None:
            await canonical_connection.close()
        if regional_connection is not None:
            await regional_connection.close()
        await source.close()
    mode = "would update" if dry_run else "updated"
    print(f"Complete: scanned {visited} parcel references; {mode} {updated} rows.", flush=True)
    return {"scanned": visited, "updated": updated}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    asyncio.run(run(batch_size=args.batch_size, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
