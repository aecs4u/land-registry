"""Bounded in-memory cache for rendered vector tiles.

Parcel and sheet tiles take seconds to build in PostGIS but change rarely, so
repeat views of the same tile (and several tabs on the same area) should not
reach the database again. Concurrent requests for one missing tile share a
single fetch.
"""

import asyncio
import os
import time
from collections import OrderedDict
from typing import Awaitable, Callable, Hashable


class TileCache:
    def __init__(self, max_bytes: int = 64 * 1024 * 1024, ttl_seconds: float = 600.0):
        self.max_bytes = max_bytes
        self.ttl_seconds = ttl_seconds
        self._entries: "OrderedDict[Hashable, tuple[float, bytes]]" = OrderedDict()
        self._size = 0
        self._inflight: dict[Hashable, "asyncio.Future[bytes]"] = {}

    @property
    def size_bytes(self) -> int:
        return self._size

    def _get(self, key: Hashable) -> bytes | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        stored_at, data = entry
        if time.monotonic() - stored_at > self.ttl_seconds:
            self._drop(key)
            return None
        self._entries.move_to_end(key)
        return data

    def _drop(self, key: Hashable) -> None:
        entry = self._entries.pop(key, None)
        if entry is not None:
            self._size -= len(entry[1])

    def _put(self, key: Hashable, data: bytes) -> None:
        if len(data) > self.max_bytes // 4:  # one huge tile must not flush the cache
            return
        self._drop(key)
        self._entries[key] = (time.monotonic(), data)
        self._size += len(data)
        while self._size > self.max_bytes and self._entries:
            self._drop(next(iter(self._entries)))

    async def get_or_fetch(self, key: Hashable, fetch: Callable[[], Awaitable[bytes]]) -> bytes:
        cached = self._get(key)
        if cached is not None:
            return cached
        pending = self._inflight.get(key)
        if pending is not None:
            return await asyncio.shield(pending)
        future: "asyncio.Future[bytes]" = asyncio.get_running_loop().create_future()
        self._inflight[key] = future
        try:
            data = await fetch()
        except BaseException as exc:
            # Failures are never cached; waiters see the same error.
            future.set_exception(exc)
            future.exception()  # mark retrieved so an unawaited future does not warn
            raise
        else:
            self._put(key, data)
            future.set_result(data)
            return data
        finally:
            self._inflight.pop(key, None)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, ""))
    except ValueError:
        return default


map_tile_cache = TileCache(
    max_bytes=_env_int("MAP_TILE_CACHE_MB", 64) * 1024 * 1024,
    ttl_seconds=float(_env_int("MAP_TILE_CACHE_TTL_SECONDS", 600)),
)
