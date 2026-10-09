"""DPC snapshots in hazards PostgreSQL; HTTP is used only by the refresh job.

Run ``python -m land_registry.bulletin_store --initialize`` once, then run
without that flag on a separate schedule. Map requests only query the store.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import httpx
import psycopg2
from psycopg2.extras import Json

logger = logging.getLogger(__name__)
REPO = "pcm-dpc/DPC-Bollettini-Criticita-Idrogeologica-Idraulica"
SOURCE = "Dipartimento della Protezione Civile, CC BY 4.0"
ROME = ZoneInfo("Europe/Rome")
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS public.dpc_criticality_bulletin (
    repo text NOT NULL,
    stamp text NOT NULL CHECK (stamp ~ '^[0-9]{8}_[0-9]{4}$'),
    bulletin_day date NOT NULL,
    fetched_at timestamptz NOT NULL,
    payload jsonb NOT NULL CHECK (
        jsonb_typeof(payload) = 'object'
        AND payload ? 'today_zones'
        AND payload->'today_zones'->>'type' = 'Topology'
    ),
    PRIMARY KEY (repo, stamp)
);
COMMENT ON TABLE public.dpc_criticality_bulletin IS
    'DPC metadata and today/tomorrow TopoJSON; refreshed outside map requests.';
"""


def hazards_dsn() -> str:
    """Use an explicit hazards DSN, or the stats server's hazards database."""
    raw = os.getenv("HAZARDS_POSTGRES_DSN") or os.getenv("AECS4U_STATS_HAZARDS_DATABASE_URL")
    if not raw:
        raw = os.getenv("STATS_POSTGRES_DSN") or os.getenv("AECS4U_STATS_POSTGRES_DSN")
        if not raw:
            raise ValueError("Set HAZARDS_POSTGRES_DSN or STATS_POSTGRES_DSN")
        raw = urlunsplit(urlsplit(raw)._replace(path="/hazards"))
    parts = urlsplit(raw)
    if parts.scheme.split("+", 1)[0] not in {"postgres", "postgresql"} or parts.path != "/hazards":
        raise ValueError("Bulletin storage requires the hazards PostgreSQL database")
    return urlunsplit(parts._replace(scheme="postgresql"))


def _connect(dsn: str):
    return psycopg2.connect(dsn, connect_timeout=5, options="-c statement_timeout=5000")


def _validate_topology(topology: dict) -> None:
    if not isinstance(topology, dict) or topology.get("type") != "Topology":
        raise ValueError("DPC zone data must be TopoJSON")
    objects = topology.get("objects")
    if not isinstance(objects, dict) or not objects or not isinstance(topology.get("arcs"), list):
        raise ValueError("DPC zone topology is incomplete")
    if not any(isinstance(obj, dict) and obj.get("geometries") for obj in objects.values()):
        raise ValueError("DPC zone topology contains no zones")


def download_bulletin() -> dict:
    """Fetch a complete snapshot; any failed zone download aborts the refresh."""
    base = f"https://raw.githubusercontent.com/{REPO}/master/files/"
    api = f"https://api.github.com/repos/{REPO}"
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        def get_json(url, **kwargs):
            response = client.get(url, **kwargs)
            response.raise_for_status()
            return response.json()

        commits = get_json(f"{api}/commits", params={"path": "files", "per_page": 5})
        stamp = None
        for commit in commits:
            detail = get_json(f"{api}/commits/{commit['sha']}")
            stamps = [
                match.group(1)
                for file in detail.get("files", [])
                if file.get("status") != "removed"
                and (match := re.match(r"files/(?:[a-z]+/)?(\d{8}_\d{4})[_.]", file.get("filename", "")))
            ]
            if stamps:
                stamp = max(stamps)
                break
        if stamp is None:
            raise ValueError("No bulletin stamp found in recent DPC commits")
        bulletin = get_json(f"{base}{stamp}.json")
        for day in ("today", "tomorrow"):
            url = (bulletin.get(day) or {}).get("topo_json")
            if not url:
                if day == "today":
                    raise ValueError("DPC bulletin has no today's zones")
                continue
            if url != f"{base}topojson/{stamp}_{day}.json":
                raise ValueError("Unexpected DPC zone URL")
            topology = get_json(url)
            _validate_topology(topology)
            bulletin[f"{day}_zones"] = topology
        bulletin.update(stamp=stamp, source=SOURCE)
        return bulletin


def save_bulletin(bulletin: dict, *, initialize: bool = False) -> None:
    """Validate before opening a transaction; publish metadata and zones together."""
    stamp = bulletin.get("stamp", "")
    if not re.fullmatch(r"\d{8}_\d{4}", stamp):
        raise ValueError("Invalid DPC bulletin stamp")
    day = datetime.strptime(stamp, "%Y%m%d_%H%M").replace(tzinfo=ROME).date()
    _validate_topology(bulletin.get("today_zones"))
    if (bulletin.get("tomorrow") or {}).get("topo_json"):
        _validate_topology(bulletin.get("tomorrow_zones"))
    connection = _connect(hazards_dsn())
    try:
        with connection, connection.cursor() as cursor:
            if initialize:
                cursor.execute(SCHEMA_SQL)
            cursor.execute("""
                    INSERT INTO public.dpc_criticality_bulletin
                        (repo, stamp, bulletin_day, fetched_at, payload)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (repo, stamp) DO UPDATE SET
                        fetched_at = EXCLUDED.fetched_at, payload = EXCLUDED.payload
                """, (REPO, stamp, day, datetime.now(UTC), Json(bulletin)))
    finally:
        connection.close()


def _for_day(payload: dict, bulletin_day: date, fetched_at: datetime, target_day: date) -> dict:
    result = dict(payload)
    result.update(
        fetched_at=fetched_at.isoformat(), storage="hazards PostgreSQL",
        valid_for=target_day.isoformat(), stale=False,
    )
    if target_day == bulletin_day + timedelta(days=1) and result.get("tomorrow_zones"):
        result["today_zones"] = result["tomorrow_zones"]
        result["today"] = result.get("tomorrow")
    elif target_day != bulletin_day:
        # Never interpret expired zones as today's alerts.
        result["today_zones"] = None
        result["stale"] = True
    result.pop("tomorrow_zones", None)
    return result


def get_bulletin(*, target_day: date | None = None) -> dict | None:
    """Read only PostgreSQL; missing configuration/data never triggers HTTP."""
    connection = None
    target_day = target_day or datetime.now(ROME).date()
    try:
        connection = _connect(hazards_dsn())
        connection.set_session(readonly=True, autocommit=True)
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT payload - CASE WHEN bulletin_day = %s THEN 'tomorrow_zones'
                                      ELSE 'today_zones' END AS payload,
                       bulletin_day, fetched_at
                FROM public.dpc_criticality_bulletin
                WHERE repo = %s ORDER BY stamp DESC LIMIT 1
            """, (target_day, REPO))
            row = cursor.fetchone()
        return _for_day(*row, target_day) if row else None
    except (ValueError, psycopg2.Error) as exc:
        logger.warning("Hazards bulletin store unavailable (%s)", type(exc).__name__)
        return None
    finally:
        if connection is not None:
            connection.close()


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--initialize", action="store_true", help="create the table before the first import")
    args = parser.parse_args()
    try:
        hazards_dsn()
        bulletin = download_bulletin()
        save_bulletin(bulletin, initialize=args.initialize)
    except (ValueError, KeyError, IndexError, TypeError, httpx.HTTPError, psycopg2.Error) as exc:
        print(f"DPC refresh failed ({type(exc).__name__}); previous snapshot retained")
        return 1
    print(f"Stored DPC bulletin {bulletin['stamp']} in hazards PostgreSQL")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
