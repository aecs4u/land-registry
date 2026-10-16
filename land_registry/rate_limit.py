"""Small in-process rate limiter for the expensive API endpoints.

Per-client token buckets, kept in memory. Cloud Run runs a few instances, so
the effective limit is per instance; this is a guard against a single client
hammering search, uploads or the public API, not a distributed quota system.
Tile and static requests are never limited: a pan legitimately issues dozens
of tile requests at once.
"""

import os
import time

from starlette.responses import JSONResponse

# (path prefix, bucket name, token capacity, refill period in seconds).
# The first matching rule wins.
_RULES = (
    ("/api/v1/map/search", "search", 40, 60),
    # PV economics invokes the external PVGIS service on cache misses; limit
    # individual clients more tightly than the general enrichment API.
    ("/api/v1/enrichment/solar-economics/", "solar-economics", 20, 60),
    ("/api/v1/upload-qpkg", "upload", 12, 60),
    ("/api/v1/generate-map", "upload", 12, 60),
    ("/api/v1/load-cadastral-files", "load", 20, 60),
    # Burst guard only. Authenticated API keys enforce the shared 10K UTC-hour
    # quota in PostgreSQL; this per-process cap must remain above that rate.
    ("/api/v2/", "api-v2", 600, 60),
    ("/api/v1/", "api", 300, 60),
)
_SKIP_PREFIXES = ("/api/v1/tiles/", "/api/v1/map/layers/health")
_MAX_BUCKETS = 10_000
_MAX_BUCKET_IDLE_SECONDS = 120


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

    def _take(
        self,
        key: tuple[str, str],
        capacity: int,
        period_seconds: float,
        now: float,
    ) -> tuple[float, int, float]:
        """Consume one token and return (wait, remaining, full-reset delay)."""
        capacity = float(capacity)
        rate = capacity / period_seconds
        tokens, updated = self._buckets.get(key, [capacity, now])
        tokens = min(capacity, tokens + (now - updated) * rate)
        if tokens >= 1.0:
            remaining_tokens = tokens - 1.0
            self._buckets[key] = [remaining_tokens, now]
            reset_after = (capacity - remaining_tokens) / rate
            return 0.0, int(remaining_tokens), reset_after
        self._buckets[key] = [tokens, now]
        wait = (1.0 - tokens) / rate
        reset_after = (capacity - tokens) / rate
        return wait, 0, reset_after

    def _prune(self, now: float) -> None:
        if len(self._buckets) > _MAX_BUCKETS:
            # Every in-process bucket refills within 60 seconds.
            self._buckets = {
                key: bucket
                for key, bucket in self._buckets.items()
                if now - bucket[1] < _MAX_BUCKET_IDLE_SECONDS
            }

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") == "OPTIONS" or not _enabled():
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        if path.startswith(_SKIP_PREFIXES):
            await self.app(scope, receive, send)
            return
        for prefix, bucket, capacity, period_seconds in _RULES:
            if path.startswith(prefix):
                now = time.monotonic()
                self._prune(now)
                wait, remaining, reset_after = self._take(
                    (_client_key(scope), bucket), capacity, period_seconds, now
                )
                rate_headers = [
                    (b"x-ratelimit-limit", str(capacity).encode("ascii")),
                    (b"x-ratelimit-remaining", str(remaining).encode("ascii")),
                    (
                        b"x-ratelimit-reset",
                        str(int(time.time() + reset_after + 0.999)).encode("ascii"),
                    ),
                ]
                if wait > 0:
                    retry_after = str(max(1, int(wait + 0.999)))
                    response = JSONResponse(
                        {"detail": "Too many requests"},
                        status_code=429,
                        headers={
                            "Retry-After": retry_after,
                            "Cache-Control": "no-store",
                            **{
                                name.decode("ascii"): value.decode("ascii")
                                for name, value in rate_headers
                            },
                        },
                    )
                    await response(scope, receive, send)
                    return

                async def send_with_rate_headers(message):
                    if message["type"] == "http.response.start":
                        existing = {name.lower() for name, _ in message.get("headers", [])}
                        message = {
                            **message,
                            "headers": [
                                *message.get("headers", []),
                                *(
                                    (name, value)
                                    for name, value in rate_headers
                                    if name not in existing
                                ),
                            ],
                        }
                    await send(message)

                await self.app(scope, receive, send_with_rate_headers)
                return
        await self.app(scope, receive, send)
