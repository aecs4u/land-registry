#!/usr/bin/env python
"""Create or refresh aecs4u-stats foreign tables for the agenziademanio database.

Run with ``STATS_POSTGRES_DSN`` set, or pass ``--dsn``. Missing foreign tables
are imported from their remote views; existing relations are kept and mapped
in place.

Prepare the indexed source view with
``scripts/sql/agenziademanio-map-features.sql`` in the agenziademanio database.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
from typing import Any

OLD_SERVER = "demanio_marittimo_source"
SERVER = "agenziademanio_source"
LOCAL_SCHEMA = "agenziademanio"
FOREIGN_TABLE_MAPPINGS = {
    "concessions": ("agenziademanio", "v_concession_map_features"),
    "document_gaps": ("agenziademanio", "v_document_gaps"),
    "institution_request_targets": ("agenziademanio", "v_institution_request_targets"),
    "online_document_matches": ("agenziademanio", "v_online_document_matches"),
    "online_documents": ("agenziademanio", "v_online_documents"),
    "regional_source_audits": ("agenziademanio", "v_regional_source_audits"),
    "resources": ("agenziademanio", "v_resources"),
    "sicily_contracts": ("agenziademanio", "v_sicily_contracts"),
    "snapshots": ("agenziademanio", "v_snapshots"),
}
FOREIGN_TABLES = tuple(FOREIGN_TABLE_MAPPINGS)


def _quote_identifier(value: str) -> str:
    """Quote a catalog identifier used in an IMPORT/ALTER statement."""
    return '"' + value.replace('"', '""') + '"'


def _dsn_from_environment() -> str:
    dsn = os.getenv("STATS_POSTGRES_DSN") or os.getenv("AECS4U_STATS_POSTGRES_DSN")
    if dsn:
        return dsn

    # Match the repo's local .env workflow without printing credential values.
    env_file = Path(__file__).resolve().parents[1] / ".env"
    if env_file.is_file():
        for raw_line in env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() in {"STATS_POSTGRES_DSN", "AECS4U_STATS_POSTGRES_DSN"}:
                value = value.strip().strip("\"'")
                if value:
                    return value
    raise SystemExit("Set STATS_POSTGRES_DSN or pass --dsn")


async def _set_server_option(connection: Any, server_name: str, option: str, value: str) -> None:
    options = await connection.fetchval(
        "SELECT srvoptions FROM pg_foreign_server WHERE srvname = $1", server_name
    )
    if options is None:
        raise RuntimeError(f"Foreign server {server_name!r} does not exist")
    present = any(item.split("=", 1)[0] == option for item in options)
    operation = "SET" if present else "ADD"
    await connection.execute(
        f"ALTER SERVER {server_name} OPTIONS ({operation} {option} '{value}')"
    )


async def _set_foreign_table_option(
    connection: Any, local_table: str, option: str, value: str
) -> None:
    options = await connection.fetchval(
        "SELECT ftoptions FROM pg_foreign_table WHERE ftrelid = to_regclass($1)",
        f"{LOCAL_SCHEMA}.{local_table}",
    )
    if options is None:
        raise RuntimeError(f"{LOCAL_SCHEMA}.{local_table} is not a foreign table")
    present = any(item.split("=", 1)[0] == option for item in options)
    operation = "SET" if present else "ADD"
    await connection.execute(
        f"ALTER FOREIGN TABLE {LOCAL_SCHEMA}.{local_table} "
        f"OPTIONS ({operation} {option} '{value}')"
    )


async def _create_missing_foreign_tables(connection: Any) -> list[str]:
    """Import missing definitions without replacing existing local objects."""
    if not await connection.fetchval(
        "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'postgis')"
    ):
        raise RuntimeError("PostGIS must be installed in aecs4u-stats before importing map foreign tables")

    await connection.execute(f"CREATE SCHEMA IF NOT EXISTS {_quote_identifier(LOCAL_SCHEMA)}")
    existing = await connection.fetch(
        """
        SELECT c.relname, c.relkind, s.srvname
        FROM pg_class AS c
        JOIN pg_namespace AS n ON n.oid = c.relnamespace
        LEFT JOIN pg_foreign_table AS ft ON ft.ftrelid = c.oid
        LEFT JOIN pg_foreign_server AS s ON s.oid = ft.ftserver
        WHERE n.nspname = $1 AND c.relname = ANY($2::text[])
        """,
        LOCAL_SCHEMA,
        list(FOREIGN_TABLES),
    )
    existing_by_name = {row["relname"]: row for row in existing}
    for name, row in existing_by_name.items():
        if row["relkind"] not in ("f", b"f"):
            raise RuntimeError(
                f"{LOCAL_SCHEMA}.{name} exists as relation kind {row['relkind']!r}, not a foreign table"
            )

    missing = [name for name in FOREIGN_TABLES if name not in existing_by_name]
    if not missing:
        return []

    imports_by_schema: dict[str, list[tuple[str, str]]] = {}
    for local_name in missing:
        remote_schema, remote_name = FOREIGN_TABLE_MAPPINGS[local_name]
        imports_by_schema.setdefault(remote_schema, []).append((local_name, remote_name))

    created: list[str] = []
    for remote_schema, mappings in imports_by_schema.items():
        for local_name, remote_name in mappings:
            if local_name != remote_name and await connection.fetchval(
                "SELECT to_regclass($1)::text", f"{LOCAL_SCHEMA}.{remote_name}"
            ):
                raise RuntimeError(
                    f"Cannot import {remote_schema}.{remote_name}: "
                    f"{LOCAL_SCHEMA}.{remote_name} already exists"
                )

        remote_names = ", ".join(_quote_identifier(remote_name) for _, remote_name in mappings)
        await connection.execute(
            f"IMPORT FOREIGN SCHEMA {_quote_identifier(remote_schema)} "
            f"LIMIT TO ({remote_names}) FROM SERVER {_quote_identifier(SERVER)} "
            f"INTO {_quote_identifier(LOCAL_SCHEMA)}"
        )

        for local_name, remote_name in mappings:
            if not await connection.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = $1 AND c.relname = $2 AND c.relkind = 'f')",
                LOCAL_SCHEMA,
                remote_name,
            ):
                raise RuntimeError(
                    f"Remote relation {remote_schema}.{remote_name} was not imported; "
                    "check its existence and the foreign user mapping"
                )
            if local_name != remote_name:
                await connection.execute(
                    f"ALTER FOREIGN TABLE {_quote_identifier(LOCAL_SCHEMA)}."
                    f"{_quote_identifier(remote_name)} RENAME TO {_quote_identifier(local_name)}"
                )
            created.append(local_name)
    return created


async def refresh(dsn: str) -> None:
    import asyncpg

    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    connection = await asyncpg.connect(dsn, timeout=5)
    try:
        database_name = await connection.fetchval("SELECT current_database()")
        if database_name != "aecs4u-stats":
            raise RuntimeError(
                f"Refusing to refresh foreign tables in unexpected database {database_name!r}"
            )

        async with connection.transaction():
            servers = await connection.fetch(
                "SELECT srvname FROM pg_foreign_server WHERE srvname = ANY($1::text[])",
                [OLD_SERVER, SERVER],
            )
            server_names = {row["srvname"] for row in servers}
            if OLD_SERVER in server_names and SERVER in server_names:
                raise RuntimeError(f"Both {OLD_SERVER!r} and {SERVER!r} exist; refusing ambiguous refresh")
            if not server_names:
                raise RuntimeError(f"Neither {OLD_SERVER!r} nor {SERVER!r} exists")
            current_server = SERVER if SERVER in server_names else OLD_SERVER

            await _set_server_option(connection, current_server, "dbname", "agenziademanio")
            await _set_server_option(connection, current_server, "extensions", "postgis")
            if current_server == OLD_SERVER:
                await connection.execute(f"ALTER SERVER {OLD_SERVER} RENAME TO {SERVER}")

            created_tables = await _create_missing_foreign_tables(connection)

            local_relations = await connection.fetch(
                """
                SELECT c.relname, c.relkind, s.srvname
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                LEFT JOIN pg_foreign_table ft ON ft.ftrelid = c.oid
                LEFT JOIN pg_foreign_server s ON s.oid = ft.ftserver
                WHERE n.nspname = $1 AND c.relname = ANY($2::text[])
                """,
                LOCAL_SCHEMA,
                list(FOREIGN_TABLES),
            )
            for relation in local_relations:
                if relation["relkind"] not in ("f", b"f") or relation["srvname"] != SERVER:
                    raise RuntimeError(
                        f"{LOCAL_SCHEMA}.{relation['relname']} is not a foreign table on {SERVER}"
                    )

            if len(local_relations) != len(FOREIGN_TABLES):
                found = sorted(row["relname"] for row in local_relations)
                missing = sorted(set(FOREIGN_TABLES) - set(found))
                raise RuntimeError(
                    "Expected existing foreign tables in "
                    f"{LOCAL_SCHEMA}; missing: {', '.join(missing) or 'none'}"
                )

            # The raw v_concessions view parses GeoJSON and repairs/transforms
            # every geometry while evaluating a tile bbox. Use its indexed
            # map view backed by public.concessions instead; keep details on their existing
            # Agenzia views.
            for table, (remote_schema, remote_table) in FOREIGN_TABLE_MAPPINGS.items():
                await _set_foreign_table_option(
                    connection, table, "schema_name", remote_schema
                )
                await _set_foreign_table_option(
                    connection, table, "table_name", remote_table
                )
            await connection.execute(
                f"COMMENT ON FOREIGN TABLE {LOCAL_SCHEMA}.concessions IS "
                "'Map-ready rows from agenziademanio.agenziademanio.v_concession_map_features (indexed public.concessions geometry)'"
            )

            mapping_rows = await connection.fetch(
                """
                SELECT c.relname, ft.ftoptions, s.srvoptions
                FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                JOIN pg_foreign_table ft ON ft.ftrelid = c.oid
                JOIN pg_foreign_server s ON s.oid = ft.ftserver
                WHERE n.nspname = $1 AND c.relname = ANY($2::text[])
                """,
                LOCAL_SCHEMA,
                list(FOREIGN_TABLES),
            )
            if len(mapping_rows) != len(FOREIGN_TABLES):
                raise RuntimeError("Foreign-table refresh did not find the expected table set")
            expected_mappings = {
                table: {f"schema_name={remote_schema}", f"table_name={remote_table}"}
                for table, (remote_schema, remote_table) in FOREIGN_TABLE_MAPPINGS.items()
            }
            for row in mapping_rows:
                server_options = row["srvoptions"] or []
                if [option.partition("=")[2] for option in server_options if option.startswith("dbname=")] != ["agenziademanio"]:
                    raise RuntimeError("Foreign server is not targeting the agenziademanio database")
                if [option.partition("=")[2] for option in server_options if option.startswith("extensions=")] != ["postgis"]:
                    raise RuntimeError("Foreign server is missing PostGIS pushdown configuration")
                table_options = row["ftoptions"] or []
                actual = set(table_options)
                if not expected_mappings[row["relname"]].issubset(actual) or any(
                    [option.partition("=")[2] for option in table_options if option.startswith(f"{key}=")] != [expected]
                    for key, expected in (
                        ("schema_name", FOREIGN_TABLE_MAPPINGS[row["relname"]][0]),
                        ("table_name", FOREIGN_TABLE_MAPPINGS[row["relname"]][1]),
                    )
                ):
                    raise RuntimeError(
                        f"{LOCAL_SCHEMA}.{row['relname']} has unexpected remote mapping"
                    )

        print(
            f"Created {len(created_tables)} missing and refreshed {len(FOREIGN_TABLES)} "
            f"foreign tables in aecs4u-stats.{LOCAL_SCHEMA}; server now targets "
            "the agenziademanio database."
        )
    finally:
        await connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", default=None, help="stats database DSN; defaults to STATS_POSTGRES_DSN or .env")
    args = parser.parse_args()
    asyncio.run(refresh(args.dsn or _dsn_from_environment()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
