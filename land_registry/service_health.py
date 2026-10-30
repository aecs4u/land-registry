"""Recent status of optional data services observed from real requests."""

from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
from time import monotonic
from typing import Any

_STATUS_TTL_SECONDS = 60
_LOCK = Lock()
_LAST_STATUS: dict[str, tuple[float, dict[str, Any]]] = {}


def record_service_response(name: str, status_code: int, *, stale: bool = False) -> None:
    """Remember only successful and server-error responses for a known service."""
    if stale:
        status = "degraded"
    elif 200 <= status_code < 300:
        status = "available"
    elif status_code >= 500:
        status = "degraded"
    else:
        return
    now = monotonic()
    result = {
        "status": status,
        "http_status": status_code,
        "stale": stale,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    with _LOCK:
        _LAST_STATUS[name] = (now, result)


def service_health_snapshot() -> dict[str, dict[str, Any]]:
    """Return fresh observations without making readiness depend on optional services."""
    now = monotonic()
    snapshot: dict[str, dict[str, Any]] = {}
    with _LOCK:
        for name in ("municipality_reference", "map_search", "sales_feed"):
            saved = _LAST_STATUS.get(name)
            if saved is None or now - saved[0] > _STATUS_TTL_SECONDS:
                snapshot[name] = {"status": "unknown", "required": False}
            else:
                snapshot[name] = {**saved[1], "required": False}
    return snapshot


class ServiceHealthMiddleware:
    """Record availability of optional services as users exercise them."""

    _COMPONENTS = (
        ("/api/v1/enrichment/municipality/", "municipality_reference"),
        ("/api/v1/map/search", "map_search"),
        ("/api/v1/sales/nearby-points", "sales_feed"),
        ("/api/v1/sales/map-points", "sales_feed"),
        ("/api/v1/sales/pvp/", "sales_feed"),
    )

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        component = next((name for prefix, name in self._COMPONENTS if path.startswith(prefix)), None)
        if component is None:
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message.get("type") == "http.response.start":
                headers = {key.lower(): value for key, value in message.get("headers", [])}
                stale = headers.get(b"x-data-stale", b"").lower() == b"true"
                record_service_response(component, int(message.get("status", 0)), stale=stale)
            await send(message)

        await self.app(scope, receive, send_wrapper)
