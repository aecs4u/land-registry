"""Small in-process rate limiter for the expensive API endpoints.

Per-client token buckets, kept in memory. Cloud Run runs a few instances, so
the effective limit is per instance; this is a guard against a single client
hammering search or uploads, not a quota system. Tile and static requests are
never limited: a pan legitimately issues dozens of tile requests at once.
"""

import os
import time

from starlette.responses import JSONResponse

# (path prefix, bucket name, requests per minute); first match wins.
_RULES = (
    ("/api/v1/map/search", "search", 40),
    ("/api/v1/upload-qpkg", "upload", 12),
    ("/api/v1/generate-map", "upload", 12),
    ("/api/v1/load-cadastral-files", "load", 20),
    ("/api/v1/", "api", 300),
)
_SKIP_PREFIXES = ("/api/v1/tiles/", "/api/v1/map/layers/health")
_MAX_BUCKETS = 10_000


def _enabled() -> bool:
    return os.getenv("RATE_LIMIT_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}


def _client_key(scope) -> str:
    """Client address, honouring the proxy chain.

    On Cloud Run the rightmost X-Forwarded-For entry is the address the
    Google front end saw; anything to its left is client-supplied and
    spoofable. ``RATE_LIMIT_PROXY_HOPS`` raises the offset behind extra proxies.
    """
    try:
        hops = max(1, int(os.getenv("RATE_LIMIT_PROXY_HOPS", "1")))
    except ValueError:
        hops = 1
    for name, value in scope.get("headers", []):
        if name == b"x-forwarded-for":
            parts = [p.strip() for p in value.decode("latin-1").split(",") if p.strip()]
            if parts:
                return parts[-hops] if len(parts) >= hops else parts[0]
    client = scope.get("client")
    return client[0] if client else "unknown"


class RateLimitMiddleware:
    def __init__(self, app):
        self.app = app
        self._buckets: dict[tuple[str, str], list[float]] = {}

    def _take(self, key: tuple[str, str], per_minute: int, now: float) -> float:
        """Consume one token; return 0 if allowed, else seconds to wait."""
        capacity = float(per_minute)
        rate = capacity / 60.0
        tokens, updated = self._buckets.get(key, [capacity, now])
        tokens = min(capacity, tokens + (now - updated) * rate)
        if tokens >= 1.0:
            self._buckets[key] = [tokens - 1.0, now]
            return 0.0
        self._buckets[key] = [tokens, now]
        return (1.0 - tokens) / rate

    def _prune(self, now: float) -> None:
        if len(self._buckets) > _MAX_BUCKETS:
            # Anything idle for two minutes has refilled to capacity anyway.
            self._buckets = {k: v for k, v in self._buckets.items() if now - v[1] < 120}

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") == "OPTIONS" or not _enabled():
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        if path.startswith(_SKIP_PREFIXES):
            await self.app(scope, receive, send)
            return
        for prefix, bucket, per_minute in _RULES:
            if path.startswith(prefix):
                now = time.monotonic()
                self._prune(now)
                wait = self._take((_client_key(scope), bucket), per_minute, now)
                if wait > 0:
                    retry_after = str(max(1, int(wait + 0.999)))
                    response = JSONResponse(
                        {"detail": "Too many requests"},
                        status_code=429,
                        headers={"Retry-After": retry_after, "Cache-Control": "no-store"},
                    )
                    await response(scope, receive, send)
                    return
                break
        await self.app(scope, receive, send)
