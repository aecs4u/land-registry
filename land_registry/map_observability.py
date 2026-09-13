"""Small, privacy-preserving observability primitives for the map stack.

The map has several optional data sources and remote tile providers. These
metrics retain only normalized route buckets and latency histograms; request
URLs, coordinates, and user identity never enter the in-process state.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


_LATENCY_BUCKETS_MS = (100, 250, 500, 1000, 3000, 10000)


def map_route_bucket(path: str) -> str | None:
    """Normalize public map paths into a bounded set of metric buckets."""
    if path == "/map":
        return "map_shell"
    if path.startswith("/api/v1/tiles/map-layers/"):
        return "canonical_vector_tile"
    if path.startswith("/api/v1/tiles/cadastral-boundaries/"):
        return "cadastral_raster_tile"
    if path == "/api/v1/map/search":
        return "search"
    if path == "/api/v1/map/layers":
        return "layer_catalog"
    if path == "/api/v1/map/layers/health":
        return "layer_health"
    if path == "/api/v1/map/metrics":
        return "metrics"
    if path.startswith("/api/v1/map/layers/"):
        return "layer_features"
    return None


class MapMetrics:
    """Thread-safe counters and bounded latency buckets for map requests."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started_at = time.time()
        self._requests = 0
        self._errors = 0
        self._routes: dict[str, dict[str, Any]] = defaultdict(self._new_route)

    @staticmethod
    def _new_route() -> dict[str, Any]:
        return {
            "requests": 0,
            "errors": 0,
            "latency_buckets_ms": {str(bucket): 0 for bucket in _LATENCY_BUCKETS_MS},
            "latency_buckets_ms[inf]": 0,
        }

    def record(self, route: str, status_code: int, duration_ms: float) -> None:
        with self._lock:
            item = self._routes[route]
            item["requests"] += 1
            self._requests += 1
            if status_code >= 400:
                item["errors"] += 1
                self._errors += 1
            bucket = next((limit for limit in _LATENCY_BUCKETS_MS if duration_ms <= limit), None)
            if bucket is None:
                item["latency_buckets_ms[inf]"] += 1
            else:
                item["latency_buckets_ms"][str(bucket)] += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "started_at": self._started_at,
                "uptime_seconds": max(0.0, time.time() - self._started_at),
                "requests": self._requests,
                "errors": self._errors,
                "routes": {
                    route: {
                        "requests": item["requests"],
                        "errors": item["errors"],
                        "latency_buckets_ms": {
                            **item["latency_buckets_ms"],
                            "inf": item["latency_buckets_ms[inf]"],
                        },
                    }
                    for route, item in self._routes.items()
                },
            }


class MapMetricsMiddleware(BaseHTTPMiddleware):
    """Record normalized map request timings without changing responses."""

    def __init__(self, app: Any, metrics: MapMetrics) -> None:
        super().__init__(app)
        self.metrics = metrics

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        route = map_route_bucket(request.url.path)
        if route is None:
            return await call_next(request)

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            self.metrics.record(route, 500, (time.perf_counter() - started) * 1000)
            raise
        self.metrics.record(route, response.status_code, (time.perf_counter() - started) * 1000)
        return response


map_metrics = MapMetrics()
