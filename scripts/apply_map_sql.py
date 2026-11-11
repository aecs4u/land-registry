#!/usr/bin/env python
"""Apply the map's idempotent index SQL to the source databases that own the indexed relations.

aecs4u-stats is deprecated, so nothing here targets it any more. Each script names the database it belongs to;
``STATS_POSTGRES_DSN`` supplies only host and credentials, and ``AECS4U_SOURCE_<DATABASE>_DATABASE_URL``
overrides one database. Statements run one at a time in autocommit mode (``CREATE INDEX CONCURRENTLY`` cannot
run inside a transaction or a multi-statement batch).

    python scripts/apply_map_sql.py --dry-run
    python scripts/apply_map_sql.py --database hazards

Every script here is safe to re-run. Scripts that change data or create cross-database objects are deliberately
not in the list; run them by hand:
``istat-municipality-metric-map-views.sql`` (ISTAT database: foreign tables + materialized views; refresh with
``python -m land_registry.metric_views``), ``pvp-enriched-modelview-map-view.sql`` and
``pvp-enriched-modelview-map-points.sql`` (pvp_enriched database), ``parcel-enrichment-read-model.sql`` and
``ispra-coastal-dynamics.sql`` (land_registry database).
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

SQL_DIR = Path(__file__).parent / "sql"

# database -> scripts, in dependency order.
PLAN = {
    "hazards": ("mps04-map-index.sql",),
}
ORDER = tuple(name for names in PLAN.values() for name in names)


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


def database_dsn(database: str, base: str = "") -> str:
    """DSN for one source database: an explicit override, else the base DSN with its database replaced."""
    override = os.getenv(f"AECS4U_SOURCE_{database.upper()}_DATABASE_URL")
    if override:
        return override.replace("postgresql+asyncpg://", "postgresql://", 1)
    base = base or os.getenv("STATS_POSTGRES_DSN", "")
    if not base:
        raise SystemExit("Set STATS_POSTGRES_DSN (host and credentials) or AECS4U_SOURCE_<DATABASE>_DATABASE_URL")
    parts = urlsplit(base.replace("postgresql+asyncpg://", "postgresql://", 1))
    return urlunsplit(parts._replace(path=f"/{database}"))


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
    parser.add_argument("--database", choices=sorted(PLAN), help="apply only this database's scripts (default: all)")
    parser.add_argument("--dsn", default="", help="base DSN (host and credentials); default: $STATS_POSTGRES_DSN")
    args = parser.parse_args()

    plan = dict(load_plan())
    failures = 0
    for database, names in PLAN.items():
        if args.database and database != args.database:
            continue
        scripts = [(name, plan[name]) for name in names]
        if args.dry_run:
            for name, statements in scripts:
                for statement in statements:
                    print(f"{database}/{name}: {' '.join(statement.split())}")
            continue
        failures += asyncio.run(apply(database_dsn(database, args.dsn), scripts))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
