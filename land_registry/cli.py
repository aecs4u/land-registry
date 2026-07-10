"""Unified command line interface for Land Registry."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Sequence

import uvicorn

from land_registry.cadastral_utils import clear_cache, load_cadastral_structure
from land_registry.config import (
    app_settings,
    cadastral_settings,
    db_settings,
    get_cadastral_data_root,
    get_cadastral_structure_path,
    get_panel_url,
    panel_settings,
    storage_settings,
)
from land_registry.file_availability_db import file_availability_db


@dataclass(frozen=True)
class TargetSpec:
    import_string: str
    module_path: str
    default_port: int
    default_reload: bool


TARGETS: dict[str, TargetSpec] = {
    "main": TargetSpec(
        import_string="land_registry.main:app",
        module_path="land_registry.main",
        default_port=app_settings.port,
        default_reload=app_settings.reload,
    ),
}


def _target_spec(target: str) -> TargetSpec:
    if target not in TARGETS:
        available = ", ".join(sorted(TARGETS))
        raise ValueError(f"Unknown target '{target}'. Available targets: {available}")
    return TARGETS[target]


def _load_fastapi_app(module_path: str) -> Any:
    module = importlib.import_module(module_path)
    app = getattr(module, "app", None)
    if app is None:
        raise RuntimeError(f"Module '{module_path}' does not export an 'app' object")
    return app


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def _print_key_values(payload: dict[str, Any], prefix: str = "") -> None:
    for key, value in payload.items():
        if isinstance(value, dict):
            new_prefix = f"{prefix}{key}."
            _print_key_values(value, new_prefix)
            continue
        print(f"{prefix}{key}: {value}")


def cmd_serve(args: argparse.Namespace) -> int:
    spec = _target_spec(args.target)
    host = args.host or app_settings.host
    port = args.port or spec.default_port
    reload_enabled = spec.default_reload if args.reload is None else args.reload

    uvicorn.run(
        spec.import_string,
        host=host,
        port=port,
        reload=reload_enabled,
        log_level=args.log_level,
    )
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    payload = {
        "app": {
            "name": app_settings.app_name,
            "version": app_settings.app_version,
            "debug": app_settings.debug,
            "environment": os.getenv("ENVIRONMENT", "auto"),
        },
        "server": {
            "host": app_settings.host,
            "port": app_settings.port,
            "reload": app_settings.reload,
        },
        "panel": {
            "host": panel_settings.panel_host,
            "port": panel_settings.panel_port,
            "url": get_panel_url(panel_settings.panel_dashboard_route),
        },
        "cadastral": {
            "use_local_files": cadastral_settings.use_local_files,
            "data_root": get_cadastral_data_root(),
            "structure_file": get_cadastral_structure_path(),
        },
        "storage": {
            "provider": storage_settings.provider,
            "s3_bucket": storage_settings.s3_bucket,
            "gcs_bucket": storage_settings.gcs_bucket,
            "local_path": storage_settings.local_path,
        },
        "database": {
            "sqlite_path": db_settings.sqlite_path,
            "file_availability_db_path": db_settings.file_availability_db_path,
            "use_sqlite": db_settings.use_sqlite,
            "use_neon": db_settings.use_neon,
        },
    }

    if args.json:
        _print_json(payload)
    else:
        _print_key_values(payload)
    return 0


def cmd_routes(args: argparse.Namespace) -> int:
    spec = _target_spec(args.target)
    app = _load_fastapi_app(spec.module_path)

    route_rows: list[tuple[str, str, str]] = []
    for route in app.routes:
        methods_raw = getattr(route, "methods", None) or set()
        methods = sorted(set(methods_raw) - {"HEAD", "OPTIONS"})
        methods_str = ",".join(methods) if methods else "-"
        path = getattr(route, "path", "-")
        name = getattr(route, "name", "-")
        route_rows.append((methods_str, path, name))

    route_rows.sort(key=lambda row: (row[1], row[0]))

    if args.json:
        _print_json(
            {
                "target": args.target,
                "module": spec.module_path,
                "routes": [
                    {"methods": methods, "path": path, "name": name}
                    for methods, path, name in route_rows
                ],
            }
        )
        return 0

    print(f"Target: {args.target} ({spec.module_path})")
    print("METHODS            PATH                                   NAME")
    print("---------------------------------------------------------------")
    for methods, path, name in route_rows:
        print(f"{methods:<18} {path:<38} {name}")
    return 0


def cmd_cadastral_stats(args: argparse.Namespace) -> int:
    cad = load_cadastral_structure(use_cache=not args.fresh)
    if cad is None:
        print("Unable to load cadastral structure.", file=sys.stderr)
        return 1

    payload = {
        "source": cad.source,
        "cache": cad.cache_metadata(),
        "stats": cad.stats,
        "file_availability": cad.get_file_availability_stats(),
    }
    if args.json:
        _print_json(payload)
    else:
        _print_key_values(payload)
    return 0


def cmd_cache_show(args: argparse.Namespace) -> int:
    payload = {
        "file_availability_db_path": db_settings.file_availability_db_path,
        "file_availability": file_availability_db.get_stats(),
    }
    if args.json:
        _print_json(payload)
    else:
        _print_key_values(payload)
    return 0


def cmd_cache_clear(args: argparse.Namespace) -> int:
    clear_cadastral_cache = args.all or args.cadastral or (not args.cadastral and not args.file_db)
    clear_file_db = args.all or args.file_db or (not args.cadastral and not args.file_db)

    actions: list[str] = []
    if clear_cadastral_cache:
        clear_cache()
        actions.append("cadastral cache")
    if clear_file_db:
        file_availability_db.clear_cache()
        actions.append("file availability cache")

    print(f"Cleared: {', '.join(actions)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="land-registry",
        description="Land Registry command line interface.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {app_settings.app_version}",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="Run the web server")
    serve_parser.add_argument("--target", choices=sorted(TARGETS), default="main", help="App target to run")
    serve_parser.add_argument("--host", help="Host to bind (defaults to configured app host)")
    serve_parser.add_argument("--port", type=int, help="Port to bind (defaults by target)")
    serve_parser.add_argument(
        "--reload",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable/disable auto-reload (default depends on target)",
    )
    serve_parser.add_argument(
        "--log-level",
        default="info",
        choices=["critical", "error", "warning", "info", "debug", "trace"],
        help="Uvicorn log level",
    )
    serve_parser.set_defaults(func=cmd_serve)

    info_parser = subparsers.add_parser("info", help="Show effective runtime configuration")
    info_parser.add_argument("--json", action="store_true", help="Output as JSON")
    info_parser.set_defaults(func=cmd_info)

    routes_parser = subparsers.add_parser("routes", help="List registered FastAPI routes")
    routes_parser.add_argument("--target", choices=sorted(TARGETS), default="main", help="App target to inspect")
    routes_parser.add_argument("--json", action="store_true", help="Output as JSON")
    routes_parser.set_defaults(func=cmd_routes)

    cadastral_parser = subparsers.add_parser("cadastral-stats", help="Show cadastral structure and cache stats")
    cadastral_parser.add_argument("--fresh", action="store_true", help="Bypass in-memory cache")
    cadastral_parser.add_argument("--json", action="store_true", help="Output as JSON")
    cadastral_parser.set_defaults(func=cmd_cadastral_stats)

    cache_parser = subparsers.add_parser("cache", help="Inspect or clear caches")
    cache_subparsers = cache_parser.add_subparsers(dest="cache_command", required=True)

    cache_show_parser = cache_subparsers.add_parser("show", help="Show file availability cache stats")
    cache_show_parser.add_argument("--json", action="store_true", help="Output as JSON")
    cache_show_parser.set_defaults(func=cmd_cache_show)

    cache_clear_parser = cache_subparsers.add_parser("clear", help="Clear runtime caches")
    cache_clear_parser.add_argument("--all", action="store_true", help="Clear all caches")
    cache_clear_parser.add_argument("--cadastral", action="store_true", help="Clear in-memory cadastral cache")
    cache_clear_parser.add_argument("--file-db", action="store_true", help="Clear file availability DB cache")
    cache_clear_parser.set_defaults(func=cmd_cache_clear)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # pragma: no cover - defensive top-level error handling
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
