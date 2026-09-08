"""Deployment gate for the canonical PostGIS map layer catalog."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Callable

from land_registry.map_layers import get_map_layer_source


def evaluate_health(
    health: list[dict[str, Any]],
    *,
    source_available: bool,
    allow_partial: bool = False,
) -> list[str]:
    """Return release-blocking problems found in a layer health response."""
    problems: list[str] = []
    if not source_available:
        problems.append("canonical PostGIS source is disabled or not configured")
        return problems

    for item in health:
        layer_id = item.get("id", "unknown")
        if not item.get("relation_exists"):
            problems.append(f"{layer_id}: relation is missing")
        if not item.get("geometry_column_exists"):
            problems.append(f"{layer_id}: geometry column is missing")
        if not item.get("gist_index_exists"):
            problems.append(f"{layer_id}: GiST index is missing")
        if not item.get("srid_matches"):
            problems.append(
                f"{layer_id}: geometry SRID {item.get('geometry_srid', 0)} "
                f"does not match expected {item.get('expected_srid', 'unknown')}"
            )
        if not allow_partial and item.get("coverage") == "partial":
            problems.append(f"{layer_id}: coverage is partial")

    return problems


def run_preflight(
    *,
    allow_partial: bool = False,
    source_factory: Callable[[], Any] = get_map_layer_source,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Run the live catalog check and return ``(health, problems)``."""
    source = source_factory()
    try:
        health = source.health()
        return health, evaluate_health(
            health,
            source_available=source.available,
            allow_partial=allow_partial,
        )
    finally:
        source.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check that every canonical PostGIS map layer is ready for release."
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="allow catalog layers explicitly marked as partial (development only)",
    )
    parser.add_argument("--json", action="store_true", help="print machine-readable output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        health, problems = run_preflight(allow_partial=args.allow_partial)
    except Exception as exc:  # pragma: no cover - exercised by deployment failures
        problems = [f"database health query failed: {exc}"]
        health = []

    if args.json:
        print(json.dumps({"ready": not problems, "layers": health, "blocking": problems}, default=str))
    else:
        print(f"canonical map layers: {len(health)} checked")
        for item in health:
            status = "ready" if item.get("available") else "not-ready"
            coverage = item.get("coverage", "unknown")
            print(f"{item.get('id', 'unknown')}: {status}; coverage={coverage}; rows~{item.get('row_estimate', 0)}")
        if problems:
            print("blocking:")
            for problem in problems:
                print(f"- {problem}")
        else:
            print("ready: yes")

    return 0 if not problems else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
