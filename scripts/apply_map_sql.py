#!/usr/bin/env python
"""Apply the map's idempotent index/statistics SQL to the stats database.

Rebuilding the database used to mean remembering which scripts in
``scripts/sql`` to run and in what order. This runs them in dependency order,
one statement at a time in autocommit mode (``CREATE INDEX CONCURRENTLY``
cannot run inside a transaction or a multi-statement batch).

    python scripts/apply_map_sql.py --dry-run
    STATS_POSTGRES_DSN=postgresql://... python scripts/apply_map_sql.py

Every script here is safe to re-run. Scripts that change data or touch other
databases are deliberately not in the list; run them by hand:
``map-parcel-field-backfill.sql`` (bulk UPDATE) and
``pvp-enriched-modelview-map-view.sql`` and
``pvp-enriched-modelview-map-points.sql`` and
``solar-map-foreign-tables.sql`` (foreign data wrapper setup).
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

SQL_DIR = Path(__file__).parent / "sql"

# Indexes first, ANALYZE-only script last so it sees the new indexes.
ORDER = (
    "map-reference-indexes.sql",
    "map-concession-tiles-index.sql",
    "mps04-map-index.sql",
    "omi-period-index.sql",
    "map-layer-statistics.sql",
)


def split_statements(sql: str) -> list[str]:
    """Split a script on top-level semicolons, dropping ``--`` comments."""
    statements, current = [], []
    for line in sql.splitlines():
        stripped = line.split("--", 1)[0].rstrip()
        if not stripped:
            continue
        current.append(stripped)
        if stripped.endswith(";"):
            statements.append("\n".join(current))
            current = []
    if current:
        statements.append("\n".join(current))
    return statements


def load_plan(sql_dir: Path = SQL_DIR) -> list[tuple[str, list[str]]]:
    missing = [name for name in ORDER if not (sql_dir / name).is_file()]
    if missing:
        raise SystemExit(f"Missing SQL scripts: {', '.join(missing)}")
    return [(name, split_statements((sql_dir / name).read_text(encoding="utf-8"))) for name in ORDER]


async def apply(dsn: str, plan: list[tuple[str, list[str]]]) -> int:
    import asyncpg

    failures = 0
    connection = await asyncpg.connect(dsn)
    try:
        for name, statements in plan:
            for statement in statements:
                label = " ".join(statement.split())[:90]
                try:
                    await connection.execute(statement)
                    print(f"ok    {name}: {label}")
                except Exception as exc:  # a missing relation must not hide later scripts
                    failures += 1
                    print(f"FAIL  {name}: {label}\n      {exc}", file=sys.stderr)
    finally:
        await connection.close()
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="print the statements without running them")
    parser.add_argument("--dsn", default=os.getenv("STATS_POSTGRES_DSN", ""), help="default: $STATS_POSTGRES_DSN")
    args = parser.parse_args()

    plan = load_plan()
    if args.dry_run:
        for name, statements in plan:
            for statement in statements:
                print(f"{name}: {' '.join(statement.split())}")
        return 0
    if not args.dsn:
        print("Set STATS_POSTGRES_DSN or pass --dsn", file=sys.stderr)
        return 2
    return 1 if asyncio.run(apply(args.dsn, plan)) else 0


if __name__ == "__main__":
    sys.exit(main())
