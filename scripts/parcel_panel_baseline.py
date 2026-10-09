#!/usr/bin/env python3
"""Measure parcel detail coverage and latency against a running land-registry.

For each area it walks a grid of points around a centre until it has found the
requested number of unique parcels (``/parcel/at-point``), then reads
``/parcel/details/{reference}`` twice and records latency, payload size,
``read_model.cached`` and which blocks carry data. Cold builds and cache hits
are summarised separately. It issues read-only GET requests and never calls
``/enrichment/status`` (that endpoint probes every store and is slow on hosts
where stores are not built).

Usage::

    python scripts/parcel_panel_baseline.py --base-url http://127.0.0.1:8011 \\
        --per-area 5 --output docs/baselines/parcel_panel_baseline.json

Re-run after each phase and compare the JSON files. See
``docs/PARCEL_PANEL_GAP_ANALYSIS_2026-10.md`` (Phase 0).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Centres of contrasting areas: two large cities, a small comune and open
# countryside (median parcel area is large there). Bolzano/Trento are absent
# from the AdE extract and would only return 404.
DEFAULT_AREAS = {
    "roma-centro": (41.8986, 12.4769),
    "milano-centro": (45.4642, 9.1900),
    "tolfa-comune": (42.1517, 11.9394),
    "monte-romano-campagna": (42.2500, 11.9000),
}
# Candidate offsets (about 150 m steps) walked outward from each centre until
# enough unique parcels are found.
_OFFSETS = sorted(
    ((i * 0.0015, j * 0.002) for i in range(-6, 7) for j in range(-6, 7)),
    key=lambda offset: abs(offset[0]) + abs(offset[1]),
)


def _get_json(url: str, timeout: float) -> tuple[int, Any, float, int]:
    """Return (status, parsed body or None, seconds, payload bytes)."""
    started = time.perf_counter()
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        body, status = exc.read(), exc.code
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, None, time.perf_counter() - started, 0
    elapsed = time.perf_counter() - started
    try:
        return status, json.loads(body), elapsed, len(body)
    except ValueError:
        return status, None, elapsed, len(body)


def _reference(feature: dict[str, Any]) -> str | None:
    props = feature.get("properties") or {}
    for key in ("national_cadastral_reference", "national_reference", "NATIONALCADASTRALREFERENCE"):
        if props.get(key):
            return str(props[key])
    return None


def _details(base_url: str, reference: str, timeout: float) -> dict[str, Any]:
    url = f"{base_url}/api/v1/enrichment/parcel/details/{urllib.parse.quote(reference, safe='')}"
    status, detail, seconds, size = _get_json(url, timeout)
    detail = detail if isinstance(detail, dict) else {}
    blocks = detail.get("blocks") or {}
    return {
        "status": status,
        "seconds": round(seconds, 3),
        "bytes": size,
        "cached": bool((detail.get("read_model") or {}).get("cached")),
        "blocks_available": sorted(name for name, block in blocks.items() if block.get("available")),
        "blocks_declared": len(blocks),
    }


def measure(
    base_url: str,
    areas: dict[str, tuple[float, float]],
    per_area: int,
    timeout: float,
) -> list[dict[str, Any]]:
    """Collect ``per_area`` unique parcels in each area, two detail calls each."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for area, (centre_lat, centre_lng) in areas.items():
        found = 0
        attempts = 0
        for d_lat, d_lng in _OFFSETS:
            if found >= per_area:
                break
            attempts += 1
            lat, lng = round(centre_lat + d_lat, 6), round(centre_lng + d_lng, 6)
            query = urllib.parse.urlencode({"lat": lat, "lng": lng})
            status, feature, lookup_s, _ = _get_json(
                f"{base_url}/api/v1/enrichment/parcel/at-point?{query}", timeout
            )
            reference = _reference(feature) if isinstance(feature, dict) else None
            if not reference or reference in seen:
                if attempts >= 3 and found == 0 and status == 404:
                    break  # no coverage here; do not walk the whole grid
                continue
            seen.add(reference)
            found += 1
            rows.append({
                "area": area, "lat": lat, "lng": lng, "reference": reference,
                "lookup_status": status, "lookup_seconds": round(lookup_s, 3),
                "first": _details(base_url, reference, timeout),
                "second": _details(base_url, reference, timeout),
            })
        if found < per_area:
            print(f"warning: {area}: only {found} unique parcels found", file=sys.stderr)
    return rows


def _stats(values: list[float]) -> dict[str, Any]:
    values = sorted(values)
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "median": round(statistics.median(values), 3),
        "p95": values[min(len(values) - 1, int(len(values) * 0.95))],
        "max": values[-1],
    }


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    calls = [call for row in rows for call in (row["first"], row["second"]) if call["status"] == 200]
    cold = [call["seconds"] for call in calls if not call["cached"]]
    hits = [call["seconds"] for call in calls if call["cached"]]
    first_calls = [row["first"] for row in rows if row["first"]["status"] == 200]
    coverage: dict[str, int] = {}
    for call in first_calls:
        for name in call["blocks_available"]:
            coverage[name] = coverage.get(name, 0) + 1
    per_area: dict[str, int] = {}
    for row in rows:
        per_area[row["area"]] = per_area.get(row["area"], 0) + 1
    return {
        "unique_parcels": len(rows),
        "parcels_per_area": per_area,
        "details_calls_ok": len(calls),
        "details_calls_failed": 2 * len(rows) - len(calls),
        "cold_build_seconds": _stats(cold),
        "cache_hit_seconds": _stats(hits),
        "cache_hits_observed": len(hits),
        "details_bytes_median": int(statistics.median(call["bytes"] for call in first_calls)) if first_calls else None,
        "block_coverage": {name: f"{count}/{len(first_calls)}" for name, count in sorted(coverage.items())},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://127.0.0.1:8011")
    parser.add_argument("--per-area", type=int, default=5, help="unique parcels collected in each area (default 5, 20 in total)")
    parser.add_argument("--timeout", type=float, default=30.0, help="seconds per request")
    parser.add_argument("--output", help="write the full result as JSON to this path")
    args = parser.parse_args(argv)

    base_url = args.base_url.rstrip("/")
    status, _, _, _ = _get_json(f"{base_url}/health", 5.0)
    if status != 200:
        print(f"{base_url}/health did not answer 200 (got {status}); is the server running?", file=sys.stderr)
        return 2

    rows = measure(base_url, DEFAULT_AREAS, max(args.per_area, 1), args.timeout)
    result = {
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "base_url": base_url,
        "summary": summarise(rows),
        "rows": rows,
    }
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
