"""Port the legacy SpatiaLite cadastral sheet map into PostGIS.

All SQLite source rows are retained in ``cadastral_legacy.map_source`` with
their original attributes and geometry. The canonical map view uses rows
with a national sheet reference only when no regional PostgreSQL record
already supplies that reference. The source SQLite file is opened read-only.

    .venv/bin/python scripts/migrate_cadastral_map_to_postgres.py --dry-run
    PGPASSFILE=/home/emanuele/.pgpass .venv/bin/python \
        scripts/migrate_cadastral_map_to_postgres.py --apply
"""

from __future__ import annotations

import argparse
import csv
import io
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "data" / "cadastral_map.sqlite"
BATCH_SIZE = 1_000

SOURCE_COLUMNS = (
    "id, regione, provincia, comune_code, comune_name, foglio, particella, "
    "layer_type, inspire_id, inspire_namespace, label, national_reference, "
    "begin_lifespan, end_lifespan, level, level_name, original_scale, "
    "geometry_wkt, min_lon, min_lat, max_lon, max_lat, source_file, imported_at"
)

CREATE_SOURCE_TABLE = """
CREATE TABLE IF NOT EXISTS cadastral_legacy.map_source (
    source_id bigint PRIMARY KEY,
    regione text NOT NULL,
    provincia text NOT NULL,
    comune_code text NOT NULL,
    comune_name text,
    foglio integer NOT NULL,
    particella integer,
    layer_type text NOT NULL,
    inspire_id text,
    inspire_namespace text,
    label text,
    national_reference text,
    begin_lifespan text,
    end_lifespan text,
    level text,
    level_name text,
    original_scale integer,
    min_lon double precision,
    min_lat double precision,
    max_lon double precision,
    max_lat double precision,
    source_file text,
    imported_at text,
    area_sqm double precision,
    geom geometry(MultiPolygon, 4326) NOT NULL
)
"""


def source_inventory(path: Path) -> tuple[int, int]:
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    try:
        total, invalid_geometry = connection.execute("""
            SELECT COUNT(*),
                   SUM(geometry_wkt IS NULL OR trim(geometry_wkt) = '')
            FROM cadastral_parcels WHERE layer_type = 'map'
        """).fetchone()
        if invalid_geometry:
            raise RuntimeError(f"Source contains {invalid_geometry} map rows without geometry")
        invalid_layer = connection.execute(
            "SELECT COUNT(*) FROM cadastral_parcels WHERE layer_type <> 'map'"
        ).fetchone()[0]
        if invalid_layer:
            raise RuntimeError(f"Expected a map-only database, found {invalid_layer} other rows")
        duplicates = connection.execute("""
            SELECT COUNT(*) FROM (
                SELECT national_reference FROM cadastral_parcels
                WHERE layer_type='map' AND national_reference IS NOT NULL
                GROUP BY national_reference HAVING COUNT(*) > 1
            )
        """).fetchone()[0]
        if duplicates:
            raise RuntimeError(f"Source contains {duplicates} duplicated non-null sheet references")
        unreferenced = connection.execute("""
            SELECT COUNT(*) FROM cadastral_parcels
            WHERE layer_type='map' AND national_reference IS NULL
        """).fetchone()[0]
        return total, unreferenced
    finally:
        connection.close()


def _copy_batch(cursor, rows: list[tuple]) -> None:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerows(rows)
    buffer.seek(0)
    cursor.copy_expert(
        "COPY cadastral_map_staging FROM STDIN WITH (FORMAT CSV)", buffer
    )


def _ensure_postgres_objects(connection) -> None:
    with connection, connection.cursor() as cursor:
        cursor.execute("CREATE SCHEMA IF NOT EXISTS cadastral_legacy")
        cursor.execute(CREATE_SOURCE_TABLE)
        cursor.execute("""
            CREATE TEMP TABLE IF NOT EXISTS cadastral_map_staging (
                source_id bigint NOT NULL,
                regione text NOT NULL,
                provincia text NOT NULL,
                comune_code text NOT NULL,
                comune_name text,
                foglio integer NOT NULL,
                particella integer,
                layer_type text NOT NULL,
                inspire_id text,
                inspire_namespace text,
                label text,
                national_reference text,
                begin_lifespan text,
                end_lifespan text,
                level text,
                level_name text,
                original_scale integer,
                geometry_wkt text NOT NULL,
                min_lon double precision,
                min_lat double precision,
                max_lon double precision,
                max_lat double precision,
                source_file text,
                imported_at text
            ) ON COMMIT PRESERVE ROWS
        """)


def _publish_sheet_view(connection) -> int:
    from land_registry.cadastral_map_views import legacy_sheet_branch, view_branch

    with connection, connection.cursor() as cursor:
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS map_source_reference_idx
            ON cadastral_legacy.map_source (national_reference)
            WHERE national_reference IS NOT NULL
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS map_source_geom_gix
            ON cadastral_legacy.map_source USING GIST (geom)
        """)
        cursor.execute("ANALYZE cadastral_legacy.map_source")
        cursor.execute("""
            SELECT c.relname
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
              AND c.relname ~ '^[a-z][a-z_]*__fogli$'
            ORDER BY c.relname
        """)
        tables = [row[0] for row in cursor.fetchall()]
        if not tables:
            raise RuntimeError("No canonical PostgreSQL cadastral sheet tables exist")
        for table in tables:
            cursor.execute(f'''
                CREATE INDEX IF NOT EXISTS "{table}_zoning_reference_idx"
                ON public."{table}" (national_zoning_reference)
            ''')
            cursor.execute(f'ANALYZE public."{table}"')
        branches = [view_branch(table) for table in tables]
        branches.append(legacy_sheet_branch(tables))
        cursor.execute("CREATE SCHEMA IF NOT EXISTS spatial")
        cursor.execute(
            "CREATE OR REPLACE VIEW spatial.cadastral_sheet AS "
            + "\nUNION ALL\n".join(branches)
        )
    return len(tables)


def migrate(source: Path, *, dry_run: bool = False) -> None:
    total, unreferenced = source_inventory(source)
    print(f"Source: {source}")
    print(f"Validated {total:,} sheet-map geometries; {unreferenced:,} have no national reference.")
    if dry_run:
        return

    try:
        import psycopg2
    except ImportError as exc:
        raise RuntimeError("Install psycopg2 in the shared environment before applying this migration") from exc

    pg_connection = psycopg2.connect(
        host="localhost",
        port=5432,
        user="postgres",
        dbname="cadastral",
        connect_timeout=10,
        application_name="land-registry-cadastral-map-migration",
    )
    sqlite_connection = sqlite3.connect(f"file:{source.resolve()}?mode=ro", uri=True)
    imported = 0
    try:
        _ensure_postgres_objects(pg_connection)
        source_cursor = sqlite_connection.execute(
            f"SELECT {SOURCE_COLUMNS} FROM cadastral_parcels WHERE layer_type='map' ORDER BY id"
        )
        while rows := source_cursor.fetchmany(BATCH_SIZE):
            with pg_connection, pg_connection.cursor() as cursor:
                _copy_batch(cursor, rows)
                cursor.execute("""
                    INSERT INTO cadastral_legacy.map_source (
                        source_id, regione, provincia, comune_code, comune_name,
                        foglio, particella, layer_type, inspire_id,
                        inspire_namespace, label, national_reference,
                        begin_lifespan, end_lifespan, level, level_name,
                        original_scale, min_lon, min_lat, max_lon, max_lat,
                        source_file, imported_at, area_sqm, geom
                    )
                    SELECT source_id, regione, provincia, comune_code, comune_name,
                           foglio, particella, layer_type, inspire_id,
                           inspire_namespace, label, national_reference,
                           begin_lifespan, end_lifespan, level, level_name,
                           original_scale, min_lon, min_lat, max_lon, max_lat,
                               source_file, imported_at, NULL::double precision,
                               ST_GeomFromText(s.geometry_wkt, 4326)::geometry(MultiPolygon, 4326)
                    FROM cadastral_map_staging s
                    ON CONFLICT (source_id) DO NOTHING
                """)
                inserted = cursor.rowcount
                imported += inserted
                cursor.execute("TRUNCATE cadastral_map_staging")
            if imported and imported % 25_000 < BATCH_SIZE:
                print(f"Imported {imported:,}/{total:,} source geometries", flush=True)

        with pg_connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM cadastral_legacy.map_source")
            stored = cursor.fetchone()[0]
        if stored != total:
            raise RuntimeError(f"PostgreSQL verification failed: stored {stored:,} of {total:,} source rows")

        with pg_connection, pg_connection.cursor() as cursor:
            cursor.execute("UPDATE cadastral_legacy.map_source SET area_sqm = NULL WHERE area_sqm IS NOT NULL")
        table_count = _publish_sheet_view(pg_connection)
        print(f"Imported {imported:,} new rows; {stored:,} source rows are stored in PostGIS.")
        print(f"Published spatial.cadastral_sheet with {table_count} regional tables and legacy map coverage.")
    finally:
        sqlite_connection.close()
        pg_connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--dry-run", action="store_true")
    action.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.source.is_file():
        parser.error(f"SQLite source does not exist: {args.source}")
    migrate(args.source, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
