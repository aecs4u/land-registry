"""Run the TINITALY importer with two local workarounds (installed package untouched).

1. aecs4u-stats 0.2.1 uses TINITALY_GRID_ORIGIN in terrain/importer.py without
   importing it from terrain.config; the name is injected for this run.
2. The importer has no retry, so one HTTP 5xx from the INGV WCS aborts a
   ~120-chunk region import. Each chunk request is retried with backoff.
"""
import functools, logging, runpy, sys, time
import httpx
import aecs4u_stats.terrain.importer as importer
from aecs4u_stats.terrain.config import TINITALY_GRID_ORIGIN

importer.TINITALY_GRID_ORIGIN = TINITALY_GRID_ORIGIN
_original = importer._download_coverage
log = logging.getLogger("terrain-retry")

@functools.wraps(_original)
def _with_retry(client, left, bottom, right, top):
    delay = 3.0
    for attempt in range(1, 7):
        try:
            return _original(client, left, bottom, right, top)
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if attempt == 6 or (status is not None and status < 500):
                raise
            logging.getLogger().warning("WCS chunk %s failed (%s); retry %d in %.0fs", (left, bottom), status or type(exc).__name__, attempt, delay)
            time.sleep(delay)
            delay = min(delay * 2, 60)

importer._download_coverage = _with_retry
sys.argv = ["import_terrain", *sys.argv[1:]]
runpy.run_module("aecs4u_stats.terrain.scripts.import_terrain", run_name="__main__", alter_sys=True)
