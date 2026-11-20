#!/usr/bin/env python
"""Report whether the ANNCSU address store can serve the map address search.

Reads only the export schema ``anncsu_v2_ref`` of the ``anncsu`` database. Point it at an
environment with ``ANNCSU_POSTGRES_DSN`` (or ``STATS_POSTGRES_DSN`` for the host):

    python scripts/check_anncsu_address_store.py [--region 19] [--probe "via goethe 1, Palermo:082053"]

Prints, per loaded region: street and address counts, geocoded share and the dataset
release; then runs the optional probe through the same parser and lookup the API uses.
Exit code 1 when no region is loaded or the probe does not find an exact address.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from land_registry import anncsu_lookup  # noqa: E402
from land_registry.address_search import parse_address  # noqa: E402


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
    try:
        regions = anncsu_lookup._loaded_regions(conn)
        if args.region:
            regions = {k: v for k, v in regions.items() if k == args.region}
        if not regions:
            print(f"FAIL no region with addresses in {anncsu_lookup.EXPORT_SCHEMA}.v_region")
            return 1
        with conn.cursor() as cur:
            cur.execute(f"SELECT contract_version FROM {anncsu_lookup.EXPORT_SCHEMA}.v_meta")
            contract = cur.fetchone()[0]
            for code in sorted(regions):
                info = anncsu_lookup._all_regions(conn)[code]
                cur.execute(
                    f"SELECT count(*) FROM {anncsu_lookup.EXPORT_SCHEMA}.v_street s "
                    f"JOIN {anncsu_lookup.EXPORT_SCHEMA}.v_municipality m USING (istat_code) WHERE m.region_code = %s",
                    (code,),
                )
                n_streets = cur.fetchone()[0]
                release = anncsu_lookup._release_for(cur, code) or {}
                share = f"{100 * info['geocoded'] / info['total']:.0f}%" if info["total"] else "n/a"
                print(f"region {code:02d} {info['name']} streets={n_streets} addresses={info['total']} "
                      f"geocoded={share} version={release.get('source_version')} contract={contract}")
    finally:
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
