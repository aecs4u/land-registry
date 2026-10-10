#!/usr/bin/env python
"""Populate is_auction_sale from structured PVP cadastral registry records.

    .venv/bin/python scripts/refresh_cadastral_parcel_auction.py --dry-run
    .venv/bin/python scripts/refresh_cadastral_parcel_auction.py --apply

Run scripts/prepare_cadastral_map.py first. PVP ``modelview_registries`` rows
provide sheet and parcel directly and are joined to their asset, sale, and
municipality. Only keys resolving to one canonical parcel identity are
flagged; all polygon parts of that identity receive the flag. Existing flags
not supported by the structured registry rows are cleared. The default run is
read-only.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Load the same .env configuration used by the application before constructing
# the map and PVP data sources.
import land_registry.config  # noqa: F401
from land_registry.cadastral_map_views import REGIONAL_TABLE, identity_sql
from land_registry.map_layers import get_map_layer_source
from land_registry.pvp_sales import get_pvp_sales_store


async def _pvp_source_with_registries(store):
    failures = []
    for source, relation in store._sources():
        try:
            async with source.connection() as connection:
                available = await connection.fetchval("""
                    SELECT to_regclass('modelview.modelview_registries') IS NOT NULL
                       AND to_regclass('modelview.modelview_assets') IS NOT NULL
                       AND to_regclass('modelview.modelview_sales') IS NOT NULL
                """)
            if available:
                return source, relation
        except Exception as exc:
            failures.append(f"{relation}: {exc}")
    detail = "; ".join(failures) if failures else "no configured PVP source exposes modelview registries"
    raise RuntimeError(f"PVP structured cadastral registry data is unavailable ({detail})")


_SIMPLE_PARCEL_LIST = re.compile(
    r"\s*[0-9]+[A-Z]*(?:/[A-Z0-9]+)?"
    r"(?:\s*(?:[-,;]|\s+E\s+)\s*[0-9]+[A-Z]*(?:/[A-Z0-9]+)?)*\s*",
    re.IGNORECASE,
)


def _normalize_cadastral_component(value: str) -> str:
    component = value.strip().upper()
    if component.isdigit():
        return component.lstrip("0") or "0"
    return component


def _parcel_values(value: str) -> list[str]:
    """Extract explicit parcel identifiers without using numbers in notes."""
    parcel = value.strip().upper()
    if not parcel or parcel == "/":
        return []
    if _SIMPLE_PARCEL_LIST.fullmatch(parcel):
        raw_values = re.split(r"\s*(?:[-,;]|\s+E\s+)\s*", parcel)
    else:
        # A few registry rows retain labels/notes such as ``p.lla 207`` or
        # ``612 ex 176/c``. Trust only the first identifier in those values.
        match = re.search(
            r"(?:^|\b)(?:P\.?LLA|PARTICELLA|MAPPALE|MAPP\.?)?\s*"
            r"([0-9]+[A-Z]*(?:/[A-Z0-9]+)?)",
            parcel,
        )
        raw_values = [match.group(1)] if match else []
    return list(dict.fromkeys(_normalize_cadastral_component(item) for item in raw_values if item.strip()))


def _sheet_value(value: str) -> str:
    """Normalize a sheet identifier, tolerating a short descriptive prefix."""
    sheet = value.strip().upper()
    if _SIMPLE_PARCEL_LIST.fullmatch(sheet):
        token = re.split(r"\s*(?:[-,;]|\s+E\s+)\s*", sheet)[0]
    else:
        match = re.search(r"(?:^|\b)([0-9]+[A-Z]*(?:/[A-Z0-9]+)?)", sheet)
        token = match.group(1) if match else ""
    return _normalize_cadastral_component(token) if token else ""


async def _regional_parcel_tables(connection) -> list[str]:
    rows = await connection.fetch("""
        SELECT c.relname
        FROM pg_class AS c
        JOIN pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
          AND c.relname ~ '^[a-z][a-z_]*__particelle$'
        ORDER BY c.relname
    """)
    tables = [row["relname"] for row in rows if REGIONAL_TABLE.fullmatch(row["relname"])]
    if not tables:
        raise RuntimeError("No regional parcel tables found; run scripts/prepare_cadastral_map.py first")
    for table in tables:
        exists = await connection.fetchval("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = $1
                  AND column_name = 'is_auction_sale'
            )
        """, table)
        if not exists:
            raise RuntimeError(
                f'public."{table}" has no is_auction_sale column; '
                "run scripts/prepare_cadastral_map.py first"
            )
    return tables


async def _stage_pvp_keys(pvp_connection, cadastral_connection, *, batch_size: int) -> tuple[int, int]:
    await cadastral_connection.execute("""
        CREATE TEMP TABLE _pvp_auction_registry_keys (
            municipality_name text NOT NULL,
            province_code text NOT NULL,
            sheet text NOT NULL,
            parcel text NOT NULL
        ) ON COMMIT PRESERVE ROWS
    """)
    scanned_registries = 0
    reference_count = 0
    last_registry_id = -9223372036854775808
    while True:
        async with pvp_connection.transaction():
            await pvp_connection.execute("SET LOCAL statement_timeout = '600s'")
            await pvp_connection.execute("SET LOCAL work_mem = '64MB'")
            rows = await pvp_connection.fetch(f"""
                SELECT registry.id AS registry_id,
                       COALESCE(address_municipality.name, municipality.name) AS municipality_name,
                       COALESCE(address_municipality.province_code, municipality.province_code) AS province_code,
                       registry.sheet,
                       registry.parcel
                FROM modelview.modelview_registries AS registry
                JOIN modelview.modelview_assets AS asset ON asset.id = registry.asset_id
                JOIN modelview.modelview_sales AS sale ON sale.id = asset.sale_id
                LEFT JOIN modelview.modelview_addresses AS address ON address.id = asset.address_id
                LEFT JOIN modelview.map_pvp_municipalities AS address_municipality
                  ON address_municipality.code = address.municipality_code
                LEFT JOIN LATERAL (
                    SELECT candidate.name, candidate.province_code
                    FROM modelview.map_pvp_municipalities AS candidate
                    LEFT JOIN modelview.map_pvp_provinces AS province
                      ON province.code = candidate.province_code
                    WHERE address_municipality.code IS NULL
                      AND lower(BTRIM(candidate.name)) = lower(BTRIM(sale.city))
                      AND (NULLIF(BTRIM(sale.province), '') IS NULL
                           OR lower(BTRIM(province.name)) = lower(BTRIM(sale.province))
                           OR upper(BTRIM(province.code)) = upper(BTRIM(sale.province)))
                    ORDER BY candidate.code
                    LIMIT 1
                ) AS municipality ON TRUE
                WHERE registry.id > $1
                  AND NULLIF(BTRIM(registry.sheet), '') IS NOT NULL
                  AND NULLIF(BTRIM(registry.parcel), '') IS NOT NULL
                ORDER BY registry.id
                LIMIT $2
            """, last_registry_id, batch_size)
            if not rows:
                break
        last_registry_id = int(rows[-1]["registry_id"])
        scanned_registries += len(rows)
        records = []
        for row in rows:
            municipality = (row["municipality_name"] or "").strip()
            province = (row["province_code"] or "").strip()
            sheet = _sheet_value(row["sheet"] or "")
            if not municipality or not province or not sheet:
                continue
            for parcel in _parcel_values(row["parcel"] or ""):
                records.append((municipality, province, sheet, parcel))
        if records:
            await cadastral_connection.copy_records_to_table(
                "_pvp_auction_registry_keys",
                records=records,
                columns=("municipality_name", "province_code", "sheet", "parcel"),
            )
            reference_count += len(records)
        print(f"PVP registries: scanned={scanned_registries} parcel_keys={reference_count}", flush=True)
    return scanned_registries, reference_count


async def _stage_matched_parcels(connection) -> int:
    await connection.execute("""
        CREATE TEMP TABLE _pvp_auction_keys ON COMMIT PRESERVE ROWS AS
        SELECT DISTINCT lower(btrim(municipality_name)) AS municipality_key,
                        upper(btrim(province_code)) AS province_key,
                        upper(btrim(sheet)) AS sheet_key,
                        upper(btrim(parcel)) AS parcel_key
        FROM _pvp_auction_registry_keys
        WHERE NULLIF(btrim(municipality_name), '') IS NOT NULL
          AND NULLIF(btrim(province_code), '') IS NOT NULL
          AND NULLIF(btrim(sheet), '') IS NOT NULL
          AND NULLIF(btrim(parcel), '') IS NOT NULL
    """)
    await connection.execute("""
        CREATE INDEX _pvp_auction_keys_lookup_idx
        ON _pvp_auction_keys (municipality_key, province_key, sheet_key, parcel_key)
    """)
    await connection.execute("ANALYZE _pvp_auction_keys")
    reference_sheet = (
        "split_part(split_part(COALESCE(parcel.national_cadastral_reference, "
        "parcel.canonical_reference), '_', 2), '.', 1)"
    )
    sheet_source = (
        "COALESCE(NULLIF(BTRIM(parcel.sheet), ''), "
        f"CASE WHEN {reference_sheet} ~ '^[0-9]{{4}}00$' "
        f"THEN LEFT({reference_sheet}, 4) ELSE {reference_sheet} END)"
    )
    normalized_sheet = f"""CASE
        WHEN BTRIM({sheet_source}) ~ '^[0-9]+$'
        THEN COALESCE(NULLIF(LTRIM(BTRIM({sheet_source}), '0'), ''), '0')
        ELSE UPPER(BTRIM({sheet_source}))
    END"""
    normalized_parcel = """CASE
        WHEN BTRIM(parcel.parcel) ~ '^[0-9]+$'
        THEN COALESCE(NULLIF(LTRIM(BTRIM(parcel.parcel), '0'), ''), '0')
        ELSE UPPER(BTRIM(parcel.parcel))
    END"""
    await connection.execute(f"""
        CREATE TEMP TABLE _pvp_auction_parcels ON COMMIT PRESERVE ROWS AS
        SELECT DISTINCT resolved.parcel_id
        FROM (
            SELECT min(parcel.id) AS parcel_id
            FROM _pvp_auction_keys AS asset
            JOIN spatial.cadastral_parcel AS parcel
              ON lower(btrim(parcel.municipality_name)) = asset.municipality_key
             AND upper(btrim(parcel.province)) = asset.province_key
             AND {normalized_sheet} = asset.sheet_key
             AND {normalized_parcel} = asset.parcel_key
            GROUP BY asset.municipality_key, asset.province_key,
                     asset.sheet_key, asset.parcel_key
            HAVING count(DISTINCT parcel.id) = 1
        ) AS resolved
        WHERE resolved.parcel_id IS NOT NULL
    """)
    await connection.execute("CREATE UNIQUE INDEX _pvp_auction_parcels_id_idx ON _pvp_auction_parcels (parcel_id)")
    await connection.execute("ANALYZE _pvp_auction_parcels")
    return int(await connection.fetchval("SELECT count(*) FROM _pvp_auction_parcels"))


async def run(*, batch_size: int, apply: bool) -> dict[str, int]:
    import asyncpg

    map_source = get_map_layer_source()
    cadastral_source = map_source.cadastral_connection_source
    if cadastral_source is None:
        raise RuntimeError("Configure CADASTRAL_POSTGRES_DSN or STATS_POSTGRES_DSN")
    pvp_store = get_pvp_sales_store()
    pvp_source, pvp_relation = await _pvp_source_with_registries(pvp_store)
    pvp_connection = None
    cadastral_connection = None
    try:
        pvp_connection = await asyncpg.connect(
            pvp_source.dsn,
            timeout=10,
            command_timeout=7200,
            server_settings={"jit": "off", "statement_timeout": "3600000"},
        )
        cadastral_connection = await asyncpg.connect(
            cadastral_source.dsn,
            timeout=10,
            command_timeout=7200,
            server_settings={"jit": "off", "statement_timeout": "3600000"},
        )
        tables = await _regional_parcel_tables(cadastral_connection)
        await cadastral_connection.execute("SET work_mem = '128MB'")
        scanned_registries, reference_count = await _stage_pvp_keys(
            pvp_connection, cadastral_connection, batch_size=batch_size
        )
        matched_parcels = await _stage_matched_parcels(cadastral_connection)
        print(
            f"PVP source={pvp_relation}; registry rows scanned={scanned_registries}; "
            f"structured parcel keys={reference_count}; unique matched parcel identities={matched_parcels}",
            flush=True,
        )

        updated_total = 0
        cleared_total = 0
        for table in tables:
            expression = identity_sql("cadastral-parcels", "parcel.national_cadastral_reference")
            if apply:
                async with cadastral_connection.transaction():
                    await cadastral_connection.execute("SET LOCAL statement_timeout = '3600s'")
                    await cadastral_connection.execute("SET LOCAL work_mem = '128MB'")
                    stale_status = await cadastral_connection.execute(f"""
                        UPDATE public."{table}" AS parcel
                        SET is_auction_sale = FALSE
                        WHERE parcel.is_auction_sale IS TRUE
                          AND NOT EXISTS (
                              SELECT 1 FROM _pvp_auction_parcels AS matched
                              WHERE matched.parcel_id = {expression}
                          )
                    """)
                    status = await cadastral_connection.execute(f"""
                        UPDATE public."{table}" AS parcel
                        SET is_auction_sale = TRUE
                        FROM _pvp_auction_parcels AS matched
                        WHERE {expression} = matched.parcel_id
                          AND parcel.is_auction_sale IS DISTINCT FROM TRUE
                    """)
                changed = int(status.rsplit(" ", 1)[-1])
                cleared = int(stale_status.rsplit(" ", 1)[-1])
                mode = "updated"
                updated_total += changed
                cleared_total += cleared
            else:
                changed = int(await cadastral_connection.fetchval(f"""
                    SELECT count(*)
                    FROM public."{table}" AS parcel
                    JOIN _pvp_auction_parcels AS matched
                      ON {expression} = matched.parcel_id
                    WHERE parcel.is_auction_sale IS DISTINCT FROM TRUE
                """))
                cleared = int(await cadastral_connection.fetchval(f"""
                    SELECT count(*)
                    FROM public."{table}" AS parcel
                    WHERE parcel.is_auction_sale IS TRUE
                      AND NOT EXISTS (
                          SELECT 1 FROM _pvp_auction_parcels AS matched
                          WHERE matched.parcel_id = {expression}
                      )
                """))
                mode = "would update"
            print(f'{table}: {mode} {changed} true row(s); clear {cleared} stale row(s)', flush=True)

        if apply:
            print(
                f"Complete: set true on {updated_total} polygon rows and cleared {cleared_total} stale rows across {len(tables)} tables.",
                flush=True,
            )
        else:
            print("Dry run complete; no parcel flags were changed. Use --apply to populate them.", flush=True)
        return {
            "registry_rows_scanned": scanned_registries,
            "structured_parcel_keys": reference_count,
            "matched_parcel_identities": matched_parcels,
            "set_true": updated_total,
            "cleared": cleared_total,
        }
    finally:
        if cadastral_connection is not None:
            await cadastral_connection.close()
        if pvp_connection is not None:
            await pvp_connection.close()
        await pvp_source.close()
        await map_source.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=5000)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="match and report without changing parcel rows (default)")
    mode.add_argument("--apply", action="store_true", help="refresh is_auction_sale from structured PVP registry rows")
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    asyncio.run(run(batch_size=args.batch_size, apply=args.apply))


if __name__ == "__main__":
    main()
