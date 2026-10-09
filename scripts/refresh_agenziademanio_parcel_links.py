#!/usr/bin/env python
"""Calculate the Agenzia Demanio to cadastral-parcel spatial crosswalk.

    .venv/bin/python scripts/refresh_agenziademanio_parcel_links.py
    .venv/bin/python scripts/refresh_agenziademanio_parcel_links.py --apply

The default run is a dry run. Matching happens in the dedicated `cadastral`
database, where the parcel GiST indexes live. `--apply` stages the result in
`aecs4u-stats` and atomically replaces the local crosswalk after computation
finishes successfully.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DDL_PATH = ROOT / "scripts/sql/refresh-agenziademanio-parcel-links.sql"
STAGE_TABLE = "concession_parcel_links_stage"
STAGE_COLUMNS = (
    "snapshot_id",
    "concession_row_id",
    "idconc",
    "admin_label",
    "concession_source_release",
    "geometry_valid_4326",
    "parcel_id",
    "canonical_reference",
    "parcel_source_releases",
    "match_method",
    "intersection_area_sqm",
)

SOURCE_SQL = """
    SELECT row_id AS concession_row_id,
           snapshot_id::text AS snapshot_id,
           NULLIF(BTRIM(idconc::text), '') AS idconc,
           admin_label::text AS admin_label,
           source_release::text AS concession_source_release,
           geometry_valid_4326,
           ST_SRID(geom) AS geom_srid,
           ST_AsBinary(geom) AS geom_wkb
    FROM agenziademanio.concessions
    WHERE snapshot_id IS NOT NULL
      AND geom IS NOT NULL
      AND NOT ST_IsEmpty(geom)
      AND layer_kind IN ('polygon_shp', 'csv_wgs84')
    ORDER BY row_id
"""

POLYGON_MATCH_SQL = f"""
    WITH parcel_hits AS (
        SELECT c.snapshot_id,
               c.concession_row_id,
               c.idconc,
               c.admin_label,
               c.concession_source_release,
               c.geometry_valid_4326,
               p.id AS parcel_id,
               p.canonical_reference,
               p.source_release AS parcel_source_release,
               ST_Intersection(
                   ST_Transform(c.geom, 3035),
                   ST_Transform(parcel_geom.match_geom, 3035)
               ) AS overlap_geom
        FROM _concession_batch AS c
        JOIN spatial.cadastral_parcel AS p
          ON p.geom && c.geom
        CROSS JOIN LATERAL (
            SELECT CASE WHEN ST_IsValid(p.geom) THEN p.geom ELSE ST_MakeValid(p.geom) END AS match_geom
        ) AS parcel_geom
        WHERE c.geometry_dimension = 2
          AND ST_Dimension(p.geom) = 2
          AND p.id IS NOT NULL
          AND p.canonical_reference IS NOT NULL
          AND ST_Intersects(parcel_geom.match_geom, c.geom)
    ), grouped AS (
        SELECT snapshot_id,
               concession_row_id,
               idconc,
               admin_label,
               concession_source_release,
               geometry_valid_4326,
               parcel_id,
               canonical_reference,
               COALESCE(
                   array_agg(DISTINCT parcel_source_release ORDER BY parcel_source_release)
                       FILTER (WHERE parcel_source_release IS NOT NULL),
                   ARRAY[]::text[]
               ) AS parcel_source_releases,
               ST_Area(ST_UnaryUnion(ST_Collect(overlap_geom))) AS intersection_area_sqm
        FROM parcel_hits
        GROUP BY snapshot_id, concession_row_id, idconc, admin_label,
                 concession_source_release, geometry_valid_4326,
                 parcel_id, canonical_reference
    )
    SELECT snapshot_id,
           concession_row_id,
           idconc,
           admin_label,
           concession_source_release,
           geometry_valid_4326,
           parcel_id,
           canonical_reference,
           parcel_source_releases,
           'polygon_overlap'::text AS match_method,
           intersection_area_sqm
    FROM grouped
    WHERE intersection_area_sqm > 0
"""

POINT_MATCH_SQL = f"""
    SELECT c.snapshot_id,
           c.concession_row_id,
           c.idconc,
           c.admin_label,
           c.concession_source_release,
           c.geometry_valid_4326,
           p.id AS parcel_id,
           p.canonical_reference,
           COALESCE(
               array_agg(DISTINCT p.source_release ORDER BY p.source_release)
                   FILTER (WHERE p.source_release IS NOT NULL),
               ARRAY[]::text[]
           ) AS parcel_source_releases,
           'point_covered'::text AS match_method,
           NULL::double precision AS intersection_area_sqm
    FROM _concession_batch AS c
    JOIN spatial.cadastral_parcel AS p
      ON p.geom && c.geom
    CROSS JOIN LATERAL (
        SELECT CASE WHEN ST_IsValid(p.geom) THEN p.geom ELSE ST_MakeValid(p.geom) END AS match_geom
    ) AS parcel_geom
    WHERE c.geometry_dimension = 0
      AND ST_Dimension(p.geom) = 2
      AND p.id IS NOT NULL
      AND p.canonical_reference IS NOT NULL
      AND ST_Covers(parcel_geom.match_geom, c.geom)
    GROUP BY c.snapshot_id, c.concession_row_id, c.idconc, c.admin_label,
             c.concession_source_release, c.geometry_valid_4326,
             p.id, p.canonical_reference
"""

CREATE_BATCH_SQL = """
    CREATE TEMP TABLE _concession_batch (
        concession_row_id bigint NOT NULL,
        snapshot_id text NOT NULL,
        idconc text,
        admin_label text,
        concession_source_release text,
        geometry_valid_4326 boolean,
        geom_wkb bytea NOT NULL,
        geom geometry(Geometry, 4326),
        geometry_dimension smallint
    ) ON COMMIT PRESERVE ROWS
"""

CREATE_STAGE_SQL = f"""
    CREATE TEMP TABLE {STAGE_TABLE} (
        snapshot_id text NOT NULL,
        concession_row_id bigint NOT NULL,
        idconc text,
        admin_label text,
        concession_source_release text,
        geometry_valid_4326 boolean,
        parcel_id bigint NOT NULL,
        canonical_reference text NOT NULL,
        parcel_source_releases text[] NOT NULL,
        match_method text NOT NULL,
        intersection_area_sqm double precision,
        PRIMARY KEY (snapshot_id, concession_row_id, parcel_id, canonical_reference)
    ) ON COMMIT PRESERVE ROWS
"""


def _database_dsns() -> tuple[str, str]:
    from dotenv import dotenv_values

    values = {
        **dotenv_values(ROOT / ".env"),
        **os.environ,
    }

    stats_dsn = next(
        (
            str(values[name]).strip()
            for name in (
                "STATS_POSTGRES_DSN",
                "AECS4U_STATS_POSTGRES_DSN",
                "AECS4U_STATS_DATABASE_URL",
                "AECS4U_STATS_SPATIAL_DATABASE_URL",
            )
            if values.get(name)
        ),
        "",
    )
    if not stats_dsn:
        raise RuntimeError("Set STATS_POSTGRES_DSN or AECS4U_STATS_POSTGRES_DSN")
    stats_dsn = stats_dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    if not stats_dsn.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("The stats database URL must use PostgreSQL")

    cadastral_dsn = str(values.get("CADASTRAL_POSTGRES_DSN") or "").strip()
    if cadastral_dsn:
        cadastral_dsn = cadastral_dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    else:
        parsed = urlsplit(stats_dsn)
        cadastral_dsn = urlunsplit(parsed._replace(path="/cadastral"))
    if not cadastral_dsn.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("The cadastral database URL must use PostgreSQL")
    return stats_dsn, cadastral_dsn


def _stage_record(row) -> tuple:
    return tuple(row[column] for column in STAGE_COLUMNS)


async def _write_stage_rows(connection, rows: list[tuple]) -> None:
    if rows:
        await connection.copy_records_to_table(
            STAGE_TABLE,
            records=rows,
            columns=STAGE_COLUMNS,
        )


async def _process_batch(
    cadastral,
    stats_writer,
    records: list,
    totals: Counter,
) -> None:
    await cadastral.execute("TRUNCATE _concession_batch")
    copy_rows = []
    for row in records:
        if row["geom_srid"] != 4326:
            raise RuntimeError("A concession geometry is not EPSG:4326; no table rows were replaced")
        copy_rows.append(
            (
                row["concession_row_id"],
                row["snapshot_id"],
                row["idconc"],
                row["admin_label"],
                row["concession_source_release"],
                row["geometry_valid_4326"],
                row["geom_wkb"],
            )
        )
    await cadastral.copy_records_to_table(
        "_concession_batch",
        records=copy_rows,
        columns=(
            "concession_row_id",
            "snapshot_id",
            "idconc",
            "admin_label",
            "concession_source_release",
            "geometry_valid_4326",
            "geom_wkb",
        ),
    )
    await cadastral.execute("""
        UPDATE _concession_batch
        SET geom = ST_SetSRID(ST_GeomFromWKB(geom_wkb), 4326);
        UPDATE _concession_batch
        SET geom = ST_MakeValid(geom)
        WHERE NOT ST_IsValid(geom);
        UPDATE _concession_batch
        SET geometry_dimension = ST_Dimension(geom)
    """)
    await cadastral.execute("ANALYZE _concession_batch")

    for match_sql in (POLYGON_MATCH_SQL, POINT_MATCH_SQL):
        staged_rows: list[tuple] = []
        async with cadastral.transaction():
            async for row in cadastral.cursor(match_sql, prefetch=1000):
                method = row["match_method"]
                totals[method] += 1
                if stats_writer is not None:
                    staged_rows.append(_stage_record(row))
                    if len(staged_rows) >= 2000:
                        await _write_stage_rows(stats_writer, staged_rows)
                        staged_rows.clear()
        if staged_rows:
            await _write_stage_rows(stats_writer, staged_rows)

    totals["processed_features"] += len(records)
    if totals["processed_features"] % 10000 < len(records):
        print(
            f"Processed {totals['processed_features']:,} concession features; "
            f"found {totals['polygon_overlap']:,} polygon links and "
            f"{totals['point_covered']:,} point links.",
            flush=True,
        )


async def _validate_databases(stats_source, cadastral) -> None:
    from land_registry.cadastral_map_views import regional_health

    stats_database = await stats_source.fetchval("SELECT current_database()")
    cadastral_database = await cadastral.fetchval("SELECT current_database()")
    if stats_database != "aecs4u-stats":
        raise RuntimeError(f"Expected aecs4u-stats, connected to {stats_database!r}")
    if cadastral_database != "cadastral":
        raise RuntimeError(f"Expected cadastral, connected to {cadastral_database!r}")
    if await stats_source.fetchval("SELECT to_regclass('agenziademanio.concessions')") is None:
        raise RuntimeError("agenziademanio.concessions is missing from aecs4u-stats")
    if await cadastral.fetchval("SELECT to_regclass('spatial.cadastral_parcel')") is None:
        raise RuntimeError("spatial.cadastral_parcel is missing from cadastral; prepare the cadastral map views first")
    health = await regional_health(cadastral, "cadastral-parcels", "spatial.cadastral_parcel")
    if not health["available"]:
        raise RuntimeError(
            "The cadastral parcel view must have EPSG:4326 geometry and valid GiST indexes on its source tables"
        )


async def _apply_stage(stats_writer) -> None:
    ddl = DDL_PATH.read_text(encoding="utf-8")
    async with stats_writer.transaction():
        await stats_writer.execute("SET LOCAL lock_timeout = '5s'")
        for statement in ddl.split(";"):
            if statement.strip():
                await stats_writer.execute(statement)
        await stats_writer.execute("DELETE FROM agenziademanio.concession_parcel_links")
        await stats_writer.execute(f"""
            INSERT INTO agenziademanio.concession_parcel_links (
                snapshot_id,
                concession_row_id,
                idconc,
                admin_label,
                concession_source_release,
                geometry_valid_4326,
                parcel_id,
                canonical_reference,
                parcel_source_releases,
                match_method,
                intersection_area_sqm,
                computed_at
            )
            SELECT snapshot_id,
                   concession_row_id,
                   idconc,
                   admin_label,
                   concession_source_release,
                   geometry_valid_4326,
                   parcel_id,
                   canonical_reference,
                   parcel_source_releases,
                   match_method,
                   intersection_area_sqm,
                   now()
            FROM {STAGE_TABLE}
        """)
        await stats_writer.execute("ANALYZE agenziademanio.concession_parcel_links")


async def refresh(*, apply: bool, batch_size: int) -> None:
    import asyncpg

    stats_dsn, cadastral_dsn = _database_dsns()
    stats_source = stats_writer = cadastral = None
    totals: Counter = Counter()
    try:
        stats_source = await asyncpg.connect(
            stats_dsn, timeout=10, command_timeout=3600,
            server_settings={"application_name": "refresh_agenziademanio_parcel_links_reader"},
        )
        cadastral = await asyncpg.connect(
            cadastral_dsn, timeout=10, command_timeout=3600,
            server_settings={"application_name": "refresh_agenziademanio_parcel_links"},
        )
        if apply:
            stats_writer = await asyncpg.connect(
                stats_dsn, timeout=10, command_timeout=3600,
                server_settings={"application_name": "refresh_agenziademanio_parcel_links_writer"},
            )
        await _validate_databases(stats_source, cadastral)
        await cadastral.execute(CREATE_BATCH_SQL)
        if stats_writer is not None:
            await stats_writer.execute(CREATE_STAGE_SQL)

        mode = "apply" if apply else "dry run"
        print(f"Starting {mode}; parcel matches will be calculated in cadastral.", flush=True)
        batch: list = []
        async with stats_source.transaction():
            async for row in stats_source.cursor(SOURCE_SQL, prefetch=batch_size):
                batch.append(row)
                if len(batch) >= batch_size:
                    await _process_batch(cadastral, stats_writer, batch, totals)
                    batch.clear()
            if batch:
                await _process_batch(cadastral, stats_writer, batch, totals)

        if totals["processed_features"] == 0:
            raise RuntimeError("No concession geometries were read; the existing crosswalk was left unchanged")
        link_count = totals["polygon_overlap"] + totals["point_covered"]
        if link_count == 0:
            raise RuntimeError("No parcel links were found; the existing crosswalk was left unchanged")

        print(
            f"Finished: {totals['processed_features']:,} concession features, "
            f"{totals['polygon_overlap']:,} polygon links, "
            f"{totals['point_covered']:,} point links.",
            flush=True,
        )
        if apply:
            await _apply_stage(stats_writer)
            stored_count = await stats_writer.fetchval(
                "SELECT count(*) FROM agenziademanio.concession_parcel_links"
            )
            print(f"Replaced aecs4u-stats.agenziademanio.concession_parcel_links with {stored_count:,} rows.", flush=True)
        else:
            print("Dry run complete; no persistent stats database changes were made.", flush=True)
    finally:
        connections = [connection for connection in (stats_source, stats_writer, cadastral) if connection]
        if connections:
            await asyncio.gather(*(connection.close() for connection in connections), return_exceptions=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="atomically replace the stored parcel links")
    parser.add_argument("--batch-size", type=int, default=1000, help="concessions per spatial-query batch (default: 1000)")
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 10000:
        parser.error("--batch-size must be between 1 and 10000")
    asyncio.run(refresh(apply=args.apply, batch_size=args.batch_size))


if __name__ == "__main__":
    main()
