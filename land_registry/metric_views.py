"""Refresh the ISTAT-database map metric views (solar potential, MEF income).

``lr_map.v_solar_potential_comuni_map`` and ``lr_map.v_mef_income_comuni_map`` join municipality geometry to
local copies of the solar and MEF/IRPEF municipal metrics (see
``scripts/sql/istat-municipality-metric-map-views.sql``). The copies are materialized views, so they only change
when this job runs: it calls ``lr_map.refresh_metric_views()``, which refreshes them ``CONCURRENTLY`` and never
blocks tile reads. Map requests never trigger it.

    python -m land_registry.metric_views

Schedule it daily; the underlying MEF and solar releases change a few times a year. On Cloud SQL use
``pg_cron`` instead (``scripts/sql/schedule-metric-views-refresh.sql``).
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg2

from land_registry.pg_ssl import plain_loopback_dsn

logger = logging.getLogger(__name__)
DATABASE = "istat"


def istat_dsn() -> str:
    """Explicit ISTAT DSN, or the stats-server DSN (host and credentials only) pointed at ``istat``."""
    raw = os.getenv("ISTAT_POSTGRES_DSN") or os.getenv("AECS4U_SOURCE_ISTAT_DATABASE_URL")
    if not raw:
        raw = os.getenv("STATS_POSTGRES_DSN") or os.getenv("AECS4U_STATS_POSTGRES_DSN")
        if not raw:
            raise ValueError("Set ISTAT_POSTGRES_DSN or STATS_POSTGRES_DSN")
        raw = urlunsplit(urlsplit(raw)._replace(path=f"/{DATABASE}"))
    parts = urlsplit(raw.replace("postgresql+asyncpg://", "postgresql://", 1).replace("postgresql+psycopg2://", "postgresql://", 1))
    if parts.scheme not in {"postgres", "postgresql"} or parts.path != f"/{DATABASE}":
        raise ValueError("The metric views live in the istat PostgreSQL database")
    return urlunsplit(parts._replace(scheme="postgresql"))


def refresh(dsn: str | None = None) -> dict[str, int]:
    """Refresh both metric views and return their row counts."""
    connection = psycopg2.connect(plain_loopback_dsn(dsn or istat_dsn()), connect_timeout=10)
    try:
        # REFRESH ... CONCURRENTLY cannot run inside a transaction block.
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute("SET statement_timeout = '10min'")
            cursor.execute("SELECT lr_map.refresh_metric_views()")
            counts = {}
            for name in ("mv_solar_potential_comuni", "mv_mef_income_comuni"):
                cursor.execute(f"SELECT count(*) FROM lr_map.{name}")
                counts[name] = int(cursor.fetchone()[0])
        return counts
    finally:
        connection.close()


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        counts = refresh()
    except Exception:
        logger.exception("Metric view refresh failed")
        return 1
    logger.info("Refreshed lr_map metric views: %s", counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
