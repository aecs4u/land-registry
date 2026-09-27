#!/usr/bin/env python3
"""Backfill Veneto parcel fields in small, individually committed batches.

Set AECS4U_STATS_POSTGRES_DSN or AECS4U_STATS_DATABASE_URL before running.
Each batch is one PostgreSQL statement, so it commits independently and emits
progress. The prior one-shot SQL remains available for reference but should
not be started alongside this runner.
"""
from __future__ import annotations

import argparse
import asyncio
import os

BATCH_SQL = """
WITH batch AS MATERIALIZED (
    SELECT id
    FROM spatial.cadastral_parcel
    WHERE id > $1
      AND source_release = 'cadastral.veneto.IT.duckdb'
      AND geom IS NOT NULL
      AND ST_SRID(geom) = 4326
      AND (area_sqm IS NULL OR national_cadastral_reference IS NULL)
    ORDER BY id
    LIMIT $2
), updated AS (
    UPDATE spatial.cadastral_parcel AS parcel
    SET area_sqm = COALESCE(parcel.area_sqm, ST_Area(parcel.geom::geography)),
        national_cadastral_reference = COALESCE(
            parcel.national_cadastral_reference,
            CASE
                WHEN parcel.source_feature_key = 'veneto:particelle:IT.AGE.PLA.' || parcel.canonical_reference
                THEN parcel.canonical_reference
            END
        )
    FROM batch
    WHERE parcel.id = batch.id
    RETURNING parcel.id,
              parcel.area_sqm IS NOT NULL AS has_area,
              parcel.national_cadastral_reference IS NOT NULL AS has_national_reference
)
SELECT COALESCE(MAX(id), $1) AS last_id,
       COUNT(*) AS rows_updated,
       COUNT(*) FILTER (WHERE has_area) AS rows_with_area,
       COUNT(*) FILTER (WHERE has_national_reference) AS rows_with_national_reference,
       COUNT(*) FILTER (WHERE NOT has_national_reference) AS rows_missing_national_reference
FROM updated
"""


async def run(batch_size: int, pause: float, after_id: int) -> None:
    try:
        import asyncpg
    except ImportError as exc:
        raise SystemExit("asyncpg is required; install the project dependencies first") from exc

    dsn = os.getenv("AECS4U_STATS_POSTGRES_DSN") or os.getenv("AECS4U_STATS_DATABASE_URL")
    if not dsn:
        raise SystemExit("Set AECS4U_STATS_POSTGRES_DSN or AECS4U_STATS_DATABASE_URL")
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    connection = await asyncpg.connect(dsn=dsn, command_timeout=None, server_settings={"jit": "off"})
    active = await connection.fetchval("""
        SELECT EXISTS (
            SELECT 1 FROM pg_stat_activity
            WHERE datname = current_database()
              AND pid <> pg_backend_pid()
              AND state = 'active'
              AND query ILIKE '%UPDATE spatial.cadastral_parcel%'
              AND query ILIKE '%cadastral.veneto.IT.duckdb%'
        )
    """)
    if active:
        await connection.close()
        raise SystemExit("A Veneto cadastral parcel backfill is already active; wait for it to finish first.")
    last_id = after_id
    total = 0
    missing_refs = 0
    try:
        while True:
            row = await connection.fetchrow(BATCH_SQL, last_id, batch_size)
            rows = int(row["rows_updated"])
            if rows == 0:
                break
            last_id = int(row["last_id"])
            total += rows
            missing_refs += int(row["rows_missing_national_reference"])
            print(
                f"committed rows={rows} total={total} last_id={last_id} "
                f"area={row['rows_with_area']} national_reference={row['rows_with_national_reference']} "
                f"missing_reference={row['rows_missing_national_reference']}",
                flush=True,
            )
            if pause:
                await asyncio.sleep(pause)
    finally:
        await connection.close()
    print(f"complete rows={total} last_id={last_id} rows_missing_reference={missing_refs}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--pause", type=float, default=0.1, help="seconds between committed batches")
    parser.add_argument("--after-id", type=int, default=0, help="resume after a previously reported id")
    args = parser.parse_args()
    if args.batch_size < 1 or args.pause < 0:
        parser.error("--batch-size must be positive and --pause cannot be negative")
    asyncio.run(run(args.batch_size, args.pause, args.after_id))


if __name__ == "__main__":
    main()
