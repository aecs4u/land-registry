"""Browser smoke test: /map must back off, and say so, when its data source is down.

Regression guard for the unbounded tile-retry storm found in the 2026-09-28 and
2026-10-02 audits. With no STATS_POSTGRES_DSN the app really has no map source,
so every tile request is a genuine 503. Skipped when Playwright or a browser
is unavailable.
"""

import socket
import threading
import time

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
uvicorn = pytest.importorskip("uvicorn")


@pytest.fixture(scope="module")
def server():
    from land_registry.main import app

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    instance = uvicorn.Server(config)
    thread = threading.Thread(target=instance.run, daemon=True)
    thread.start()
    deadline = time.time() + 20
    while not instance.started and time.time() < deadline:
        time.sleep(0.1)
    assert instance.started, "test server did not start"
    yield f"http://127.0.0.1:{port}"
    instance.should_exit = True
    thread.join(timeout=10)


@pytest.fixture(scope="module")
def browser():
    with playwright_sync.sync_playwright() as p:
        try:
            instance = p.chromium.launch()
        except Exception as exc:  # browser binaries not installed
            pytest.skip(f"Chromium unavailable: {exc}")
        yield instance
        instance.close()


def test_map_backs_off_and_reports_outage(server, browser):
    page = browser.new_page(viewport={"width": 1280, "height": 800}, locale="en-US")
    tile_requests = []
    page.on("request", lambda r: tile_requests.append(r.url) if "/api/v1/tiles/" in r.url else None)
    # Libraries come from CDNs, so scripts and styles must load. Block only the
    # data traffic (basemap tiles, geocoder) to keep the run quiet and fast.
    page.route(
        "**/*",
        lambda route: route.abort()
        if not route.request.url.startswith(server) and route.request.resource_type in {"fetch", "xhr", "image"}
        else route.continue_(),
    )
    page.goto(f"{server}/map?lat=45.4384&lng=10.9916&zoom=15", wait_until="domcontentloaded")
    # Retries back off (about 1.5 s, then 3 s) and give up; allow them to finish,
    # then require the page to go quiet. An uncapped loop never does.
    page.wait_for_timeout(9000)
    seen_after_settle = len(tile_requests)
    page.wait_for_timeout(5000)
    seen_later = len(tile_requests)
    assert seen_later == seen_after_settle, f"still retrying: {seen_after_settle} -> {seen_later} tile requests"

    if "could not be initialised" in page.inner_text("body").lower():
        pytest.skip("map libraries could not be loaded from their CDNs (offline?)")
    banner = page.locator("#mapSourceBanner")
    assert banner.is_visible(), "the outage must be visible to the user"
    assert "unavailable" in banner.inner_text().lower()
    page.close()
