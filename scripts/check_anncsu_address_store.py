#!/usr/bin/env python
"""Report whether the ANNCSU address store can serve the map address search.

Run it against each environment (local, staging, production) with that
environment's ``STATS_POSTGRES_DSN`` / ``AECS4U_STATS_POSTGRES_DSN``:

    python scripts/check_anncsu_address_store.py [--region 19] [--probe "via goethe 1, Palermo:082053"]

Prints, per loaded region: schema, street and address counts, geocoded share,
source vintage, and the indexes on the address table; then runs the optional
probe through the same parser and lookup the API uses. Exit code 1 when no
region is loaded or the probe does not find an exact address.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from land_registry.address_search import parse_address  # noqa: E402
from land_registry import anncsu_lookup  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", type=int, help="only this ISTAT region code")
    ap.add_argument("--probe", help='"text:ISTAT", e.g. "via goethe 1, Palermo:082053"')
    args = ap.parse_args()

    try:
        conn = anncsu_lookup._connect()
    except Exception as exc:
        print(f"FAIL cannot connect to the anncsu database: {type(exc).__name__}: {exc}")
        return 1
    regions = anncsu_lookup._loaded_regions(conn)
    if args.region:
        regions = {k: v for k, v in regions.items() if k == args.region}
    if not regions:
        print("FAIL no region with both streets and addresses tables in schema 'anncsu' or 'public'")
        return 1
    for code, schema in sorted(regions.items()):
        addresses = anncsu_lookup._qualified(schema, code, "addresses")
        streets = anncsu_lookup._qualified(schema, code, "streets")
        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*), count(*) FILTER (WHERE longitude IS NOT NULL) FROM {addresses}")
            n_addr, n_geo = cur.fetchone()
            cur.execute(f"SELECT count(*) FROM {streets}")
            n_streets = cur.fetchone()[0]
            cur.execute("SELECT indexname FROM pg_indexes WHERE schemaname=%s AND tablename=%s",
                        (schema, f"r{code:02d}__addresses"))
            indexes = sorted(r[0] for r in cur.fetchall())
            cur.execute(
                "SELECT to_regclass(%s)", (f'"{schema}"."r{code:02d}__anncsu_provenance"',))
            prov = None
            if cur.fetchone()[0]:
                cur.execute(f'SELECT source_version FROM "{schema}"."r{code:02d}__anncsu_provenance" LIMIT 1')
                row = cur.fetchone()
                prov = row[0] if row else None
        if schema != "public":
            print(f"WARN region {code:02d}: tables are in schema '{schema}', not 'public' (the layout the package "
                  f"importer writes). Address search, the 'anncsu-addresses' map layer and the parcel panel's ANNCSU "
                  f"block all handle this through schema-aware readers; other aecs4u_stats consumers read only 'public'. "
                  f"To normalise: ALTER TABLE \"{schema}\".r{code:02d}__addresses SET SCHEMA public (same for "
                  f"__streets, __municipality_coverage, __anncsu_provenance).")
        share = f"{100 * n_geo / n_addr:.0f}%" if n_addr else "n/a"
        print(f"region {code:02d} schema={schema} streets={n_streets} addresses={n_addr} geocoded={share} "
              f"version={prov} indexes={indexes}")
    conn.close()

    if args.probe:
        text, _, istat = args.probe.rpartition(":")
        parsed = parse_address(text)
        if not parsed:
            print(f"FAIL probe does not parse as an address: {text!r}")
            return 1
        started = time.perf_counter()
        matches = anncsu_lookup.find_addresses(istat, parsed[0])
        elapsed = time.perf_counter() - started
        top = matches[0] if matches else None
        print(f"probe {text!r} in {istat}: {top.match_level if top else 'no match'} ({elapsed:.2f}s)"
              + (f" -> {top.latitude:.6f},{top.longitude:.6f}" if top else ""))
        return 0 if top and top.match_level == "address" else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
