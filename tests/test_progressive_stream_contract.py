"""Contract checks for the progressive cadastral load stream.

A single large cadastral file produces one `layer` event after tens of seconds
of silence.  Everything that makes that wait legible -- the documented progress
event, and the stream reaching the browser unbuffered -- is pinned here because
each failure mode is invisible in normal use: the load still succeeds, it just
looks frozen.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "land_registry" / "routers" / "api.py"
STATIC = ROOT / "land_registry" / "static"


def _stream_endpoint_source() -> str:
    """The endpoint body, sliced to the next top-level definition."""
    source = API.read_text(encoding="utf-8")
    start = source.index("async def load_cadastral_files_stream")
    end = source.index("\n@api_router.", start)
    return source[start:end]


def test_stream_opts_out_of_gzip_middleware() -> None:
    """The NDJSON stream must not pass through GZipMiddleware.

    Starlette feeds every chunk into one shared zlib stream, and
    GzipFile.write() does not flush, so small start/progress events compress to
    nothing and the client receives the whole stream in one burst at the end.
    Declaring a Content-Encoding is the supported way to opt out: both of
    Starlette's responders pass through any response that already sets it.
    """
    endpoint = _stream_endpoint_source()

    assert '"Content-Encoding": "identity"' in endpoint
    assert '"X-Accel-Buffering": "no"' in endpoint
    assert 'media_type="application/x-ndjson"' in endpoint


def test_stream_emits_the_documented_progress_event() -> None:
    """The protocol documents a progress event; it must actually be sent."""
    endpoint = _stream_endpoint_source()

    assert endpoint.count('"event": "progress"') >= 1
    assert '"status": "loading"' in endpoint


def test_stream_waits_with_a_timeout_so_it_can_heartbeat() -> None:
    """Waiting only on completion leaves the stream silent for the whole load."""
    endpoint = _stream_endpoint_source()

    assert "asyncio.wait(" in endpoint
    assert "FIRST_COMPLETED" in endpoint
    assert "elapsed_seconds" in endpoint


def test_client_ticks_elapsed_time_locally() -> None:
    """Server heartbeats cannot be relied on while CPU-bound work holds the GIL.

    The overlay therefore measures elapsed time in the browser, which keeps
    updating no matter what the server's event loop is doing.
    """
    source = (STATIC / "progressive-loader.js").read_text(encoding="utf-8")

    assert "progressive-load-elapsed" in source
    assert "_tickElapsed" in source
    assert "setInterval" in source
    # The ticker must be stopped, or it keeps running after the panel is gone.
    hide_start = source.index("    hide() {")
    assert "clearInterval" in source[hide_start:hide_start + 200]


def test_client_ignores_heartbeat_events_without_a_file() -> None:
    """A heartbeat carries no file identity and must not blank the status."""
    source = (STATIC / "progressive-loader.js").read_text(encoding="utf-8")

    case_start = source.index("case 'progress':")
    case_body = source[case_start:case_start + 400]
    assert "event.file_path === undefined" in case_body


def test_client_forwards_optional_viewport_bbox() -> None:
    """High-zoom parcel loads must not serialize an entire municipality PLE."""
    source = (STATIC / "progressive-loader.js").read_text(encoding="utf-8")

    assert "requestBody.bbox = options.bbox" in source
    assert "options.bbox.length === 4" in source
