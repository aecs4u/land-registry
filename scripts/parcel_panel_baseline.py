#!/usr/bin/env python3
"""Measure parcel detail coverage and latency against a running land-registry.

For each area it walks a grid of points around a centre until it has found the
requested number of unique canonical parcels (``reference`` plus feature ID,
falling back to reference when the ID is absent), then reads
``/parcel/details/{reference}`` twice, forwarding the canonical feature ID when
the point lookup returns one. ``--refresh-first`` forces the first request to
rebuild and persist the row, making the second request a cache-hit check. It
records latency, payload size,
``read_model.cached``, the cache database and which blocks carry data. Cold
builds and cache hits are summarised separately. It sends GET requests only;
``--view panel`` can populate the application's panel read cache. It never calls
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
from collections import defaultdict
from datetime import UTC, datetime
from math import ceil
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


def _wait_for_health(base_url: str, timeout: float) -> tuple[int, float]:
    """Wait for a freshly started application to become ready."""
    started = time.monotonic()
    deadline = started + max(timeout, 0.0)
    status = 0
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        status, _, _, _ = _get_json(
            f"{base_url}/health", min(5.0, max(0.1, remaining))
        )
        if status == 200:
            return status, time.monotonic() - started
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(0.5, remaining))
    return status, time.monotonic() - started


def _reference(feature: dict[str, Any]) -> str | None:
    props = feature.get("properties") or {}
    for key in ("national_cadastral_reference", "national_reference", "NATIONALCADASTRALREFERENCE"):
        if props.get(key):
            return str(props[key])
    return None


def _feature_id(feature: dict[str, Any]) -> int | None:
    props = feature.get("properties") or {}
    value = feature.get("id", props.get("id"))
    try:
        feature_id = int(value)
    except (TypeError, ValueError):
        return None
    return feature_id if feature_id > 0 else None


def _details(
    base_url: str,
    reference: str,
    timeout: float,
    view: str,
    feature_id: int | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    url = f"{base_url}/api/v1/enrichment/parcel/details/{urllib.parse.quote(reference, safe='')}"
    params = {}
    if view != "full":
        params["view"] = view
    if feature_id is not None:
        params["id"] = str(feature_id)
    if refresh:
        params["refresh"] = "true"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    status, detail, seconds, size = _get_json(url, timeout)
    detail = detail if isinstance(detail, dict) else {}
    blocks = detail.get("blocks") or {}
    read_model = detail.get("read_model") or {}
    return {
        "status": status,
        "seconds": round(seconds, 3),
        "bytes": size,
        "cached": bool(read_model.get("cached")),
        "cache_database": read_model.get("database"),
        "requested_feature_id": feature_id,
        "read_model_feature_id": read_model.get("feature_id"),
        "feature_id_matches": (
            feature_id is None
            or str(read_model.get("feature_id")) == str(feature_id)
        ),
        "blocks_available": sorted(name for name, block in blocks.items() if block.get("available")),
        "blocks_declared": len(blocks),
    }


def measure(
    base_url: str,
    areas: dict[str, tuple[float, float]],
    per_area: int,
    timeout: float,
    view: str = "full",
    refresh_first: bool = False,
) -> list[dict[str, Any]]:
    """Collect unique canonical parcels per area, with two detail calls each."""
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, int | None]] = set()
    for area, (centre_lat, centre_lng) in areas.items():
        found = 0
        attempts = 0
        transport_errors = 0
        for d_lat, d_lng in _OFFSETS:
            if found >= per_area:
                break
            attempts += 1
            lat, lng = round(centre_lat + d_lat, 6), round(centre_lng + d_lng, 6)
            query = urllib.parse.urlencode({"lat": lat, "lng": lng})
            status, feature, lookup_s, _ = _get_json(
                f"{base_url}/api/v1/enrichment/parcel/at-point?{query}", timeout
            )
            if status == 0:
                transport_errors += 1
                if transport_errors >= 3:
                    raise RuntimeError(
                        f"{base_url} failed three consecutive parcel lookups; "
                        "the benchmark was aborted without writing partial results"
                    )
            else:
                transport_errors = 0
            reference = _reference(feature) if isinstance(feature, dict) else None
            if not reference:
                if attempts >= 3 and found == 0 and status == 404:
                    break  # no coverage here; do not walk the whole grid
                continue
            feature_id = _feature_id(feature) if isinstance(feature, dict) else None
            # A cadastral reference can identify more than one canonical map
            # feature. Keep each reference/feature pair so this benchmark can
            # exercise the feature-scoped cache keys; fall back to reference
            # identity only when the point endpoint has no feature ID.
            identity = (reference, feature_id)
            if identity in seen:
                continue
            seen.add(identity)
            found += 1
            rows.append({
                "area": area, "lat": lat, "lng": lng, "reference": reference,
                "feature_id": feature_id,
                "lookup_status": status, "lookup_seconds": round(lookup_s, 3),
                "first": _details(
                    base_url, reference, timeout, view, feature_id,
                    refresh=refresh_first,
                ),
                "second": _details(base_url, reference, timeout, view, feature_id),
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
        # Nearest-rank percentile: rank = ceil(p * n), one-indexed.
        "p95": round(values[max(0, ceil(len(values) * 0.95) - 1)], 3),
        "max": values[-1],
    }


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    all_calls = [call for row in rows for call in (row["first"], row["second"])]
    calls = [call for call in all_calls if call["status"] == 200]
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
    cache_databases: dict[str, dict[str, Any]] = {}
    for call in calls:
        database = call.get("cache_database") or "none"
        bucket = cache_databases.setdefault(
            database,
            {"calls": 0, "cached_calls": 0, "response_seconds": [], "cached_seconds": []},
        )
        bucket["calls"] += 1
        bucket["response_seconds"].append(call["seconds"])
        if call["cached"]:
            bucket["cached_calls"] += 1
            bucket["cached_seconds"].append(call["seconds"])
    for bucket in cache_databases.values():
        bucket["response_seconds"] = _stats(bucket["response_seconds"])
        bucket["cached_seconds"] = _stats(bucket["cached_seconds"])
    feature_ids_by_reference: dict[str, set[int]] = defaultdict(set)
    for row in rows:
        feature_id = row.get("feature_id")
        if feature_id is not None:
            feature_ids_by_reference[str(row["reference"])].add(int(feature_id))
    duplicate_reference_groups = sum(
        len(feature_ids) > 1 for feature_ids in feature_ids_by_reference.values()
    )
    duplicate_references = {
        reference
        for reference, feature_ids in feature_ids_by_reference.items()
        if len(feature_ids) > 1
    }
    duplicate_rows = [row for row in rows if row["reference"] in duplicate_references]
    expected_duplicate_pairs = sum(
        len(feature_ids_by_reference[reference]) for reference in duplicate_references
    )
    duplicate_calls = [
        call for row in duplicate_rows for call in (row["first"], row["second"])
    ]
    duplicate_warm_hits = sum(
        row["second"]["status"] == 200
        and row["second"].get("cached")
        and row["second"].get("feature_id_matches")
        for row in duplicate_rows
    )
    duplicate_cache_isolated = (
        None
        if duplicate_reference_groups == 0
        else (
            len(duplicate_rows) == expected_duplicate_pairs
            and all(
                call["status"] == 200 and call.get("feature_id_matches")
                for call in duplicate_calls
            )
            and duplicate_warm_hits == len(duplicate_rows)
        )
    )
    feature_scoped_attempts = [
        call for call in all_calls if call.get("requested_feature_id") is not None
    ]
    feature_scoped_calls = [call for call in feature_scoped_attempts if call["status"] == 200]
    feature_id_cache_mismatches = sum(
        not call.get("feature_id_matches", False) for call in feature_scoped_calls
    )
    return {
        "percentile_method": "nearest_rank",
        "unique_parcels": len(rows),
        "canonical_feature_ids": sum(row.get("feature_id") is not None for row in rows),
        "duplicate_reference_groups": duplicate_reference_groups,
        "duplicate_reference_feature_pairs": len(duplicate_rows),
        "duplicate_reference_warm_hits": duplicate_warm_hits,
        "duplicate_reference_cache_isolated": duplicate_cache_isolated,
        "feature_id_cache_attempts": len(feature_scoped_attempts),
        "feature_id_cache_checks": len(feature_scoped_calls),
        "feature_id_cache_mismatches": feature_id_cache_mismatches,
        "parcels_per_area": per_area,
        "details_calls_ok": len(calls),
        "details_calls_failed": 2 * len(rows) - len(calls),
        "details_response_seconds": _stats([call["seconds"] for call in calls]),
        "cold_build_seconds": _stats(cold),
        "cache_hit_seconds": _stats(hits),
        "cache_hits_observed": len(hits),
        "cache_databases": cache_databases,
        "details_bytes_median": int(statistics.median(call["bytes"] for call in first_calls)) if first_calls else None,
        "block_coverage": {name: f"{count}/{len(first_calls)}" for name, count in sorted(coverage.items())},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://127.0.0.1:8011")
    parser.add_argument(
        "--per-area",
        type=int,
        default=5,
        help="unique parcels collected in each area (default 5, 20 in total)",
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="seconds per request")
    parser.add_argument(
        "--startup-timeout",
        type=float,
        default=60.0,
        help="seconds to wait for /health before starting the measurement (default: 60)",
    )
    parser.add_argument(
        "--view",
        choices=("full", "panel"),
        default="full",
        help="details response shape (default: full)",
    )
    parser.add_argument(
        "--refresh-first",
        action="store_true",
        help="force each first detail call to rebuild, then measure its repeat cache hit",
    )
    parser.add_argument("--output", help="write the full result as JSON to this path")
    args = parser.parse_args(argv)

    base_url = args.base_url.rstrip("/")
    if args.startup_timeout <= 0:
        parser.error("--startup-timeout must be greater than zero")
    status, startup_seconds = _wait_for_health(base_url, args.startup_timeout)
    if status != 200:
        print(
            f"{base_url}/health did not answer 200 within "
            f"{startup_seconds:.1f}s (last status: {status}); is the server running?",
            file=sys.stderr,
        )
        return 2

    try:
        rows = measure(
            base_url,
            DEFAULT_AREAS,
            max(args.per_area, 1),
            args.timeout,
            args.view,
            refresh_first=args.refresh_first,
        )
    except RuntimeError as exc:
        print(f"benchmark aborted: {exc}", file=sys.stderr)
        return 2
    result = {
        "captured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "base_url": base_url,
        "details_view": args.view,
        "refresh_first": args.refresh_first,
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
