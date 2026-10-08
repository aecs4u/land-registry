#!/usr/bin/env python
"""Prepare indexed map views over the dedicated database's regional imports.

    .venv/bin/python scripts/prepare_cadastral_map.py --dry-run
    .venv/bin/python scripts/prepare_cadastral_map.py

No source rows are copied or changed. Identity indexes are built concurrently;
the views are published together only after every source has been validated.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from land_registry.cadastral_map_views import REGIONAL_TABLE, identity_sql, view_branch


def _dsn() -> str:
    from dotenv import dotenv_values

    values = {**dotenv_values(Path(__file__).resolve().parents[1] / ".env"), **os.environ}
    direct = values.get("CADASTRAL_POSTGRES_DSN")
    if direct:
        return direct.replace("postgresql+asyncpg://", "postgresql://", 1)
    stats = values.get("STATS_POSTGRES_DSN") or values.get("AECS4U_STATS_POSTGRES_DSN")
    if not stats:
        raise RuntimeError("Set CADASTRAL_POSTGRES_DSN or STATS_POSTGRES_DSN")
    parsed = urlsplit(stats.replace("postgresql+asyncpg://", "postgresql://", 1))
    return urlunsplit(parsed._replace(path="/cadastral"))


async def prepare(dsn: str, *, dry_run: bool = False) -> None:
    import asyncpg

    connection = await asyncpg.connect(dsn, timeout=5, command_timeout=3600)
    try:
        rows = await connection.fetch("""
            SELECT c.relname, postgis_typmod_srid(a.atttypmod) AS srid,
                   EXISTS (
                       SELECT 1 FROM pg_index i
                       JOIN pg_class idx ON idx.oid = i.indexrelid
                       JOIN pg_am am ON am.oid = idx.relam
                       WHERE i.indrelid = c.oid AND i.indisvalid
                         AND am.amname = 'gist' AND a.attnum = ANY(i.indkey)
                   ) AS has_gist
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'geom'
            WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
              AND c.relname ~ '^[a-z][a-z_]*__(fogli|particelle)$'
            ORDER BY c.relname
        """)
        tables = [row["relname"] for row in rows]
        if not tables or not any(t.endswith("__fogli") for t in tables) or not any(t.endswith("__particelle") for t in tables):
            raise RuntimeError("Both regional sheet and parcel tables are required")
        for row in rows:
            table = row["relname"]
            if not REGIONAL_TABLE.fullmatch(table) or row["srid"] != 4326 or not row["has_gist"]:
                raise RuntimeError(f"{table}: requires EPSG:4326 geometry and a valid geometry GiST index")
            parcels = table.endswith("__particelle")
            required = {
                "region", "province", "municipality_code", "municipality_name", "sheet_number",
                "inspire_localid", "area_sqm", "geom",
                *(('parcel_number', 'national_cadastral_reference') if parcels else ('national_zoning_reference', 'level', 'level_name')),
            }
            columns = {r[0] for r in await connection.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = $1", table
            )}
            if required - columns:
                raise RuntimeError(f"{table}: missing columns {sorted(required - columns)}")

        for table in tables:
            parcels = table.endswith("__particelle")
            layer_id = "cadastral-parcels" if parcels else "cadastral-sheets"
            reference = "national_cadastral_reference" if parcels else "national_zoning_reference"
            expression = identity_sql(layer_id, reference)
            index = f"{table}_map_identity_idx"
            # A failed concurrent build leaves an invalid index. Do not publish
            # views that appear indexed merely because its name exists.
            valid = await connection.fetchval(
                "SELECT i.indisvalid FROM pg_index i WHERE i.indexrelid = to_regclass($1)", f"public.{index}"
            )
            if valid is False:
                raise RuntimeError(f"{index}: invalid previous build; repair it before retrying")
            if valid is None:
                statement = f'CREATE INDEX CONCURRENTLY "{index}" ON public."{table}" ({expression})'
                if dry_run:
                    print(statement + ";", flush=True)
                else:
                    print(f"Indexing {table}…", flush=True)
                    await connection.execute(statement)

        statements = ["CREATE SCHEMA IF NOT EXISTS spatial"]
        for suffix, relation in (("__fogli", "cadastral_sheet"), ("__particelle", "cadastral_parcel")):
            branches = [view_branch(table) for table in tables if table.endswith(suffix)]
            statements.append(f"CREATE OR REPLACE VIEW spatial.{relation} AS " + "\nUNION ALL\n".join(branches))
        if dry_run:
            print(";\n".join(statements) + ";")
        else:
            async with connection.transaction():
                for statement in statements:
                    await connection.execute(statement)
            print(f"Published cadastral sheets and parcels from {len(tables)} regional tables.", flush=True)
    finally:
        await connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(prepare(_dsn(), dry_run=args.dry_run))


if __name__ == "__main__":
    main()
