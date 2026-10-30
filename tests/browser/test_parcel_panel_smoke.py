"""Browser smoke test: opening a parcel on /map fills the side sheet section by section.

All parcel data is mocked at the network layer, so the test needs no database
or ISTAT store. It guards the behaviours that unit tests cannot: sections load
lazily and independently, a failing source never blanks the panel, the tab bar
follows the scroll position, and the OMI estimator reacts to input. Skipped
when Playwright or a browser is unavailable.
"""

import base64
import hashlib
import json
import os
import re
import shutil
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
uvicorn = pytest.importorskip("uvicorn")

REFERENCE = "H501A048600.D"
PARCEL = {
    "type": "Feature",
    "properties": {
        "municipality_code": "H501", "municipality_name": "ROMA", "province": "RM", "region": "LAZIO",
        "sheet": "486", "parcel": "D", "national_cadastral_reference": REFERENCE,
        "area_sqm": 5545.17, "centroid_lat": 41.8986, "centroid_lng": 12.4769,
    },
    "geometry": {"type": "Polygon", "coordinates": [[[12.4765, 41.8983], [12.4773, 41.8983], [12.4773, 41.8989], [12.4765, 41.8989], [12.4765, 41.8983]]]},
    "bbox": [12.4765, 41.8983, 12.4773, 41.8989],
}


def _quote(zone, low, high):
    return {
        "zona": zone, "cod_tipologia": "20", "tipologia": "Abitazioni civili", "stato_conservazione": "NORMALE",
        "prezzo_min": low, "prezzo_max": high, "locazione_min": 19.5, "locazione_max": 25.8, "anno": 2025, "semestre": 2,
    }


HISTORY = [
    {"anno": 2019 + i // 2, "semestre": i % 2 + 1, "prezzo_min": 6500 + i * 120, "prezzo_max": 8600 + i * 150, "stato_conservazione": "NORMALE"}
    for i in range(14)
]
MUNICIPALITY = {"name": "Roma", "province": "Roma", "province_sigla": "RM", "region": "Lazio", "istat_code": "058091", "cadastral_code": "H501"}
# (path fragment, status, body); first match wins.
ROUTES = [
    ("/parcel/by-reference/", 200, PARCEL),
    ("/parcel/details/", 200, {"national_reference": REFERENCE, "blocks": {"basic": {"available": True}, "cadastral": {"available": True}}}),
    ("/omi/quotes", 200, {"quotes": [_quote("B31", 7400, 9700), _quote("C1", 3000, 4200)], "source": "OMI", "dataset_version": "2025/2"}),
    ("/omi/at-point", 200, {"matched": True, "zone": "B31"}),
    ("/omi/history", 200, {"history": HISTORY}),
    ("/omi/estimate", 200, {"value_range_eur": {"min": 592000, "max": 776000}, "model_version": "omi-area-range-v1"}),
    ("/municipality/H501", 200, MUNICIPALITY),
    ("/risks/058091", 200, {"seismic": {"zone": 3}, "hydrogeological": {"flood": {"area_pct": {"P3_high_probability": 6.2}}, "landslide": {"area_pct": {"P4_very_high": 0.4}}}}),
    ("/mps04/pga", 200, {"source": "INGV MPS04", "available": True, "matched": True, "pga_g": 0.123, "pga_p16_g": 0.101, "pga_p84_g": 0.147}),
    # A source that is down: its section must show a retry, not break the panel.
    ("/bulletin", 503, {"detail": "down"}),
    ("/fires", 200, {"count": 0, "source": "FIRMS"}),
    ("/income/H501", 200, {"taxpayers": 1000, "mean_taxable_income_eur": 28650, "income_distribution": [{"bracket": "<10k", "pct": 30}]}),
    ("/census/at-point", 404, {"detail": "none"}),
    ("/crime/H501", 200, {"province": "Roma", "year": 2023, "total_crimes": 10, "crime_types": 3}),
    ("/pois/", 200, {"total": 2, "categories": {"Shops": [1, 2]}}),
    ("/parcel/buildings/", 200, {
        "source": "SISTER SQLite building_classifications",
        "address_source": "SISTER SQLite visura_properties.address",
        "available": True,
        "count": 2,
        "buildings": [{"category": "A/2"}, {"category": "C/6"}],
        "addresses": ["Via Roma 1"],
        "address_count": 1,
        "addresses_truncated": False,
    }),
    ("/parcel/opendata", 200, {"source": "OpenData", "records": []}),
    ("/parcel/pvp", 200, {"source": "PVP", "records": []}),
    ("/parcel/subsidence/", 200, {
        "source": "EGMS", "license": "ODbL", "available": True, "matched": True,
        "query_complete": True, "cell_count": 2, "covered_area_sqm": 120, "covered_area_pct": 100,
        "mean_velocity_mm_per_year": -1.2, "min_velocity_mm_per_year": -1.7,
        "max_velocity_mm_per_year": -0.7, "mean_acceleration_mm_per_year_squared": 0.1,
        "class_distribution": [{"class": "Moderate", "area_pct": 100}],
    }),
    ("/demographics/", 200, {"indicators": [], "source": "ISTAT"}),
    ("/quality-of-life/", 200, {"indicators": [], "source": "ISTAT"}),
]


@pytest.fixture(scope="module")
def server():
    from land_registry.main import app
    from land_registry.routers.auth import get_current_user, get_current_user_optional

    previous_user = app.dependency_overrides.get(get_current_user_optional)
    previous_required_user = app.dependency_overrides.get(get_current_user)
    test_user = SimpleNamespace(
        id="browser-test-user",
        email="browser-test@example.test",
        first_name="Browser",
        last_name="Test",
        full_name="Browser Test",
        image_url=None,
    )
    app.dependency_overrides[get_current_user_optional] = lambda: test_user
    app.dependency_overrides[get_current_user] = lambda: test_user

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    instance = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning", lifespan="off",
    ))
    thread = threading.Thread(target=instance.run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 20
        while not instance.started and time.time() < deadline:
            time.sleep(0.1)
        assert instance.started, "test server did not start"
        yield f"http://127.0.0.1:{port}"
    finally:
        instance.should_exit = True
        thread.join(timeout=10)
        if previous_user is None:
            app.dependency_overrides.pop(get_current_user_optional, None)
        else:
            app.dependency_overrides[get_current_user_optional] = previous_user
        if previous_required_user is None:
            app.dependency_overrides.pop(get_current_user, None)
        else:
            app.dependency_overrides[get_current_user] = previous_required_user


@pytest.fixture(scope="module")
def browser():
    with playwright_sync.sync_playwright() as p:
        try:
            executable = (
                os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
                or shutil.which("chromium")
                or shutil.which("google-chrome")
            )
            instance = (
                p.chromium.launch(executable_path=executable)
                if executable
                else p.chromium.launch()
            )
        except Exception as exc:  # browser binaries not installed
            pytest.skip(f"Chromium unavailable: {exc}")
        yield instance
        instance.close()


def _open_parcel(
    browser,
    server,
    viewport,
    route_overrides=None,
    wait_for_panel=True,
    fragment=None,
    parcel_payload=PARCEL,
    initial_center=(41.8986, 12.4769),
):
    mobile = viewport["width"] <= 700
    # The app's CSP has no 'unsafe-eval', and Playwright's wait_for_function
    # polls with eval inside the page, so the policy is bypassed for these UI
    # smoke tests. CSP itself is covered by the route tests in test_main_endpoints.
    page = browser.new_page(viewport=viewport, locale="en-US", is_mobile=mobile, has_touch=mobile, bypass_csp=True)
    requested = []
    import panel as pn

    panel_bundled = Path(pn.__file__).parent / "dist" / "bundled"
    maplibre_js = panel_bundled / "deckglplot" / "maplibre-gl" / "dist" / "maplibre-gl.js"
    maplibre_css = panel_bundled / "deckglplot" / "maplibre-gl@4.4.1" / "dist" / "maplibre-gl.css"
    if not maplibre_js.is_file() or not maplibre_css.is_file():
        pytest.skip("Panel's bundled MapLibre assets are unavailable for the offline browser smoke test")
    topojson_stub = b"window.topojson = window.topojson || {};"
    maplibre_integrity = "sha384-" + base64.b64encode(hashlib.sha384(maplibre_js.read_bytes()).digest()).decode("ascii")
    maplibre_css_integrity = "sha384-" + base64.b64encode(hashlib.sha384(maplibre_css.read_bytes()).digest()).decode("ascii")
    topojson_integrity = "sha384-" + base64.b64encode(hashlib.sha384(topojson_stub).digest()).decode("ascii")
    # MapLibre waits for visible raster tiles before firing its load event. A
    # failed external tile request can therefore prevent parcel URL restoration
    # from running in this intentionally offline browser test.
    offline_tile = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLttAAAAABJRU5ErkJggg=="
    )

    def handle(route):
        url = route.request.url
        if url.startswith(server) and "/api/v1/user/preferences" in url:
            for override_fragment, status, body in route_overrides or []:
                if override_fragment in url:
                    response = body(url) if callable(body) else body
                    response_status, response_body = response if isinstance(response, tuple) else (status, response)
                    return route.fulfill(status=response_status, content_type="application/json", body=json.dumps(response_body))
            return route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"preferences": {"default_basemap": None, "default_layers": []}}),
            )
        if url.startswith(server) and url.split("?", 1)[0].endswith("/api/v1/map/layers"):
            # Use the real static catalog contract without its database-backed
            # availability query. These UI smoke tests do not exercise PostGIS.
            from land_registry.map_layers import map_layer_catalog

            return route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"source": "browser smoke fixture", "available": True, "layers": map_layer_catalog()}),
            )
        if url.startswith(server) and url.split("?", 1)[0].endswith("/api/v1/map/layers/health"):
            return route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"available": True, "layers": []}),
            )
        if url.startswith(server) and "/api/v1/map/layers/cadastral-parcels/features" in url:
            return route.fulfill(
                status=200,
                content_type="application/geo+json",
                body=json.dumps({"type": "FeatureCollection", "features": []}),
            )
        if url.startswith(server) and "/api/v1/tiles/map-layers/" in url:
            return route.fulfill(status=200, content_type="application/x-protobuf", body=b"")
        if url.startswith(server) and "/api/v1/map/search" in url:
            for override_fragment, status, body in route_overrides or []:
                if override_fragment in url:
                    response = body(url) if callable(body) else body
                    response_status, response_body = response if isinstance(response, tuple) else (status, response)
                    return route.fulfill(status=response_status, content_type="application/json", body=json.dumps(response_body))
            return route.continue_()
        if url.startswith(server) and route.request.resource_type == "document":
            response = route.fetch()
            html = response.text()
            html = html.replace("sha384-SYKAG6cglRMN0RVvhNeBY0r3FYKNOJtznwA0v7B5Vp9tr31xAHsZC0DqkQ/pZDmj", maplibre_integrity)
            html = html.replace("sha384-MinO0mNliZ3vwppuPOUnGa+iq619pfMhLVUXfC4LHwSCvF9H+6P/KO4Q7qBOYV5V", maplibre_css_integrity)
            html = html.replace("sha384-Ukv1p/xTma6P4/2bY5KzWBw+ydSpXmhCMtyciIQVDJ1RmOxtCYNMF1uXT9T63H67", topojson_integrity)
            return route.fulfill(response=response, body=html)
        if "unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js" in url:
            return route.fulfill(path=str(maplibre_js), content_type="application/javascript")
        if "unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css" in url:
            return route.fulfill(path=str(maplibre_css), content_type="text/css")
        if "cdn.jsdelivr.net/npm/topojson-client@3.1.0/dist/topojson-client.min.js" in url:
            return route.fulfill(body=topojson_stub, content_type="application/javascript")
        if ".tile.openstreetmap.org/" in url:
            return route.fulfill(body=offline_tile, content_type="image/png")
        if url.startswith(server) and "/api/v1/sales/nearby-points" in url:
            requested.append(url)
            for fragment, status, body in route_overrides or []:
                if fragment in url:
                    response = body(url) if callable(body) else body
                    response_status, response_body = response if isinstance(response, tuple) else (status, response)
                    return route.fulfill(status=response_status, content_type="application/json", body=json.dumps(response_body))
            return route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({
                    "source": {"relation": "modelview.v_map_sale_points", "database": "pvp_enriched", "loaded_at": "2026-10-10T00:00:00+02:00", "geocoded_sales": 0},
                    "center": {"lat": 41.8986, "lng": 12.4769},
                    "radius_km": 10,
                    "count": 0,
                    "fields": ["id", "lng", "lat", "price", "date", "category", "approximate", "distance_km"],
                    "points": [],
                }),
            )
        if url.startswith(server) and "/api/v1/enrichment/" in url:
            requested.append(url)
            for fragment, status, body in [*(route_overrides or []), *ROUTES]:
                if fragment in url:
                    if fragment == "/parcel/by-reference/" and status == 200:
                        body = parcel_payload
                    return route.fulfill(status=status, content_type="application/json", body=json.dumps(body))
            return route.fulfill(status=404, content_type="application/json", body="{}")
        if not url.startswith(server) and route.request.resource_type in {"fetch", "xhr", "image"}:
            return route.abort()
        return route.continue_()

    page.route("**/*", handle)
    reference = (
        parcel_payload.get("properties", {}).get("canonical_reference")
        or parcel_payload.get("properties", {}).get("national_cadastral_reference")
        or REFERENCE
    )
    initial_lat, initial_lng = initial_center
    url = f"{server}/map?lat={initial_lat}&lng={initial_lng}&zoom=17&parcel={reference}"
    if fragment:
        url += f"#{fragment}"
    page.goto(url, wait_until="domcontentloaded")
    if wait_for_panel:
        page.wait_for_selector('#parcel-section-omi[data-state="ready"]', timeout=45000)
    return page, requested


def _state(page, section):
    return page.evaluate(f"document.querySelector('#parcel-section-{section}').dataset.state")


def _open_section(page, section):
    card = page.locator(f"#parcel-section-{section}")
    if not card.evaluate("element => element.open"):
        card.locator(":scope > summary.parcel-section-header").click()


def _scroll_through(page):
    for y in range(0, 6000, 300):
        page.evaluate(f"document.getElementById('directParcelContent').scrollTop = {y}")
        page.wait_for_timeout(80)


def test_panel_fills_sections_independently_and_survives_a_failing_source(server, browser):
    page, requested = _open_parcel(browser, server, {"width": 1440, "height": 900})
    try:
        # Identity comes from the tile feature; eager sections load at once.
        assert page.inner_text("#directParcelTitle") == "Parcel D"
        assert _state(page, "identity") == "ready"
        page.wait_for_selector('#parcel-section-address[data-state="ready"]')
        assert page.locator(".parcel-sheet-header #parcelShareButton").is_visible()
        assert page.locator(".parcel-sheet-header #parcelReportLink").is_visible()
        _open_section(page, "address")
        assert "Via Roma 1" in page.inner_text("#parcel-section-address")
        assert "not a geocoded address register" in page.inner_text("#parcel-section-address")
        assert any("/parcel/details/" in url and "view=panel" in url for url in requested)
        # Numbers follow the page language: "5,545.2 m²" in English, "5545,2 m²" in Italian.
        assert re.match(r"5[,.]?545", page.inner_text('[data-stat="area"] .parcel-stat-value'))
        assert f"/api/v1/enrichment/parcel/dossier/{REFERENCE}" in page.get_attribute("#parcelReportLink", "href")
        assert page.get_attribute("#parcelReportLink", "aria-disabled") == "false"
        page.wait_for_function("document.querySelector('[data-stat=\"hazard\"] .parcel-stat-value').textContent.startsWith('High')")
        assert page.get_attribute('[data-stat="hazard"]', "data-level") == "high"
        # The strip no longer has a seismic cell; the zone is read in the Risks card.
        assert page.locator('[data-stat="seismic"]').count() == 0
        page.wait_for_selector('#parcel-section-risks[data-state="ready"]')
        _open_section(page, "risks")
        assert "Zone 3" in page.inner_text("#parcel-section-risks")
        assert "PGA (10 percent in 50 years)" in page.inner_text("#parcel-section-risks")
        assert re.search(r"0[,.]123 g", page.inner_text("#parcel-section-risks"))
        assert any("/mps04/pga?" in url for url in requested)
        # Lazy sections wait until they approach the viewport.
        assert not any("/income/" in url for url in requested)

        _scroll_through(page)
        assert page.locator(".parcel-sheet-header #parcelShareButton").is_visible()
        assert page.locator(".parcel-sheet-header #parcelReportLink").is_visible()
        page.wait_for_selector('#parcel-section-income[data-state="ready"]', timeout=15000)

        # The bulletin source returned 503: that card explains and offers a retry; the rest is intact.
        page.wait_for_selector('#parcel-section-bulletin[data-state="error"]', timeout=15000)
        _open_section(page, "bulletin")
        assert page.locator("#parcel-section-bulletin .parcel-retry").count() == 1
        assert "temporarily unavailable" in page.inner_text("#parcel-section-bulletin")
        assert _state(page, "income") == "ready"
        assert _state(page, "census") == "empty"
        unavailable = page.locator(".parcel-unavailable-group")
        unavailable.locator("summary").click()
        unavailable.locator('[data-restore-section="census"]').click()
        page.wait_for_function("""() => {
          const section = document.querySelector('#parcel-section-census');
          return section && !section.hidden && section.open;
        }""")
        assert "No matching data was found" in page.inner_text("#parcel-section-census")
        assert page.locator("#parcel-section-census .parcel-retry").count() == 0
        assert _state(page, "identity") == "ready"
        assert page.evaluate("document.querySelectorAll('.parcel-section').length") >= 15
    finally:
        page.close()


def test_uncovered_cadastral_reference_explains_current_province_coverage(server, browser):
    page, requested = _open_parcel(
        browser,
        server,
        {"width": 1440, "height": 900},
        route_overrides=[("/parcel/by-reference/", 404, {"detail": "not found"})],
        wait_for_panel=False,
    )
    page_errors = []
    console_errors = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    try:
        try:
            page.wait_for_function(
                "document.querySelector('#mapStatus').textContent.includes('Bolzano and Trento')",
                timeout=15000,
            )
        except Exception as error:
            diagnostics = page.evaluate("""() => ({
              url: location.href,
              navigationUrl: performance.getEntriesByType('navigation')[0]?.name,
              status: document.querySelector('#mapStatus')?.textContent,
              statusClass: document.querySelector('#mapStatus')?.className,
              progress: document.querySelector('#mapTaskProgress')?.textContent,
              progressHidden: document.querySelector('#mapTaskProgress')?.hidden,
              readyState: document.readyState,
              maplibre: typeof window.maplibregl,
              mapLoaded: window.landRegistryPurchaseMap?.maps?.().map?.loaded?.(),
              styleLoaded: window.landRegistryPurchaseMap?.maps?.().map?.isStyleLoaded?.(),
              mapRect: (() => { const r = document.querySelector('#directMap')?.getBoundingClientRect(); return r && { x: r.x, y: r.y, width: r.width, height: r.height }; })(),
              canvasRect: (() => { const r = document.querySelector('#directMap canvas')?.getBoundingClientRect(); return r && { x: r.x, y: r.y, width: r.width, height: r.height }; })(),
              mapListeners: Object.keys(window.landRegistryPurchaseMap?.maps?.().map?._listeners || {}),
              loadListenerCount: window.landRegistryPurchaseMap?.maps?.().map?._listeners?.load?.length,
              loadListener: String(window.landRegistryPurchaseMap?.maps?.().map?._listeners?.load?.[0] || '').slice(0, 300),
              selection: window.landRegistryPurchaseMap?.selection?.(),
              panelHidden: document.querySelector('#directParcelPanel')?.hidden,
            })""")
            pytest.fail(
                f"parcel selection did not reach the uncovered-reference message: {error}; "
                f"diagnostics={diagnostics}; requested={requested}; "
                f"page_errors={page_errors}; console_errors={console_errors}"
            )
        assert any("/parcel/by-reference/" in url for url in requested)
        assert "not currently covered" in page.inner_text("#mapStatus")
    finally:
        page.close()


@pytest.mark.parametrize("fragment", ["sezione-income", "parcel-section-income"])
def test_section_deep_links_restore_and_track_the_selected_card(server, browser, fragment):
    census_properties = {
        "sez21_id": "S1",
        "p1": 100,
        "p2": 48,
        "p3": 52,
        "area_sqm": 1_000_000,
    }
    page, requested = _open_parcel(
        browser,
        server,
        {"width": 1440, "height": 900},
        fragment=fragment,
        route_overrides=[(
            "/census/at-point",
            200,
            {"type": "Feature", "properties": census_properties, "geometry": None},
        )],
    )
    try:
        page.wait_for_selector('#parcel-section-income[data-state="ready"]', timeout=15000)
        page.wait_for_function("location.hash === '#sezione-income'", timeout=10000)
        assert page.get_attribute('#parcelSectionNav button[data-tab="context"]', "aria-current") == "true"
        assert any("/income/H501" in url for url in requested)

        _scroll_through(page)
        page.wait_for_selector('#parcel-section-census[data-state="ready"]', timeout=15000)
        census = page.locator("#parcel-section-census")
        assert not census.evaluate("element => element.hidden")
        _open_section(page, "census")
        page.evaluate("""() => {
          const content = document.querySelector('#directParcelContent');
          content.dispatchEvent(new WheelEvent('wheel', { bubbles: true, deltaY: 1 }));
          document.querySelector('#parcel-section-census').scrollIntoView({ block: 'start', behavior: 'auto' });
        }""")
        page.wait_for_function("location.hash === '#sezione-census'", timeout=10000)
        assert page.get_attribute('#parcelSectionNav button[data-tab="context"]', "aria-current") == "true"
    finally:
        page.close()


def test_pvp_cold_snapshot_shows_retry_and_recovers(server, browser):
    calls = 0

    def nearby_response(_url):
        nonlocal calls
        calls += 1
        if calls == 1:
            return 503, {"detail": "Sales data is loading"}
        return 200, {
            "source": {"relation": "modelview.v_map_sale_points", "database": "pvp_enriched", "loaded_at": "2026-10-10T00:00:00+02:00", "geocoded_sales": 0},
            "center": {"lat": 41.8986, "lng": 12.4769},
            "radius_km": 10,
            "count": 0,
            "fields": ["id", "lng", "lat", "price", "date", "category", "approximate", "distance_km"],
            "points": [],
        }

    page, requested = _open_parcel(
        browser,
        server,
        {"width": 1440, "height": 900},
        route_overrides=[("/api/v1/sales/nearby-points", 503, nearby_response)],
        fragment="sezione-pvp",
    )
    try:
        page.wait_for_selector('#parcel-section-pvp[data-state="ready"]', timeout=15000)
        page.locator("#parcelPvpNearbySummary").click()
        page.wait_for_selector("#parcelPvpNearbyList .parcel-retry", timeout=10000)
        assert calls == 1
        with page.expect_response(lambda response: "/api/v1/sales/nearby-points" in response.url) as response_info:
            page.locator("#parcelPvpNearbyList .parcel-retry").click()
        assert response_info.value.status == 200, f"attempts={calls}, requests={requested}"
        page.wait_for_function("document.querySelector('#parcelPvpNearbyList .parcel-retry') === null", timeout=10000)
        assert calls == 2
        assert len([url for url in requested if "/api/v1/sales/nearby-points" in url]) == 2
    finally:
        page.close()


def test_egms_section_summarizes_grid_cells_intersecting_the_selected_parcel(server, browser):
    page, requested = _open_parcel(browser, server, {"width": 1440, "height": 900})
    try:
        _scroll_through(page)
        page.wait_for_selector('#parcel-section-subsidence[data-state="ready"]', timeout=15000)
        _open_section(page, "subsidence")
        section = page.inner_text("#parcel-section-subsidence")
        assert any(f"/parcel/subsidence/{REFERENCE}" in url for url in requested)
        assert "Movement class by covered area" in section
        assert "Intersected EGMS cells" in section
        assert "100%" in section
        assert "Negative vertical velocity indicates lowering" in section
    finally:
        page.close()


def test_section_cards_can_be_collapsed_and_reopened(server, browser):
    page, _ = _open_parcel(browser, server, {"width": 1440, "height": 900})
    try:
        card = page.locator("#parcel-section-identity")
        assert card.evaluate("element => element.tagName") == "DETAILS"
        body = card.locator(".parcel-section-body")
        if card.evaluate("element => element.open"):
            card.locator(".parcel-section-header").click()
        assert card.evaluate("element => element.open") is False
        assert not body.is_visible()

        card.locator(".parcel-section-header").click()
        assert card.evaluate("element => element.open") is True
        assert body.is_visible()

        page.locator('#parcelSectionNav button[data-tab="value"]').click()
        page.wait_for_function("document.querySelector('#parcel-section-omi').open")
        assert page.locator("#parcel-section-omi").is_visible()

        page.locator('#parcelSectionNav button[data-tab="data"]').click()
        coverage = page.locator("#parcel-section-coverage")
        coverage.wait_for(state="visible")
        page.wait_for_function("document.querySelector('#parcel-section-coverage').dataset.state === 'ready'")
        groups = coverage.locator("details.parcel-coverage-group")
        assert groups.count() == 2
        assert "Available" in groups.nth(0).inner_text()
        assert "Not available" in groups.nth(1).inner_text()

        content = page.locator("#directParcelContent")
        for y in range(0, 5400, 240):
            content.evaluate("(element, top) => { element.scrollTop = top; }", y)
            page.wait_for_timeout(50)
        unavailable = page.locator(".parcel-unavailable-group")
        page.wait_for_function("""() => {
          const group = document.querySelector('.parcel-unavailable-group');
          return group && !group.hidden && group.querySelectorAll('[data-restore-section]').length > 0;
        }""", timeout=10000)
        unavailable.locator("summary").click()
        restore = unavailable.locator("[data-restore-section]").first
        section_id = restore.get_attribute("data-restore-section")
        restore.click()
        section = page.locator(f"#parcel-section-{section_id}")
        assert section.is_visible()
        assert section.evaluate("element => element.open")
    finally:
        page.close()


def test_omi_estimator_reacts_to_selection_and_surface(server, browser):
    page, _ = _open_parcel(browser, server, {"width": 1440, "height": 900})
    try:
        assert "B31" in page.inner_text("#parcel-section-omi .parcel-callout")
        assert page.input_value("#parcelOmiQuote") == ""
        assert page.input_value("#parcelOmiArea") == ""
        assert page.locator("#parcelOmiEstimate strong").count() == 0
        assert page.locator('#parcelStatStrip [data-stat="price"] [data-stat-text]').inner_text() == "—"
        page.fill("#parcelOmiArea", "80")
        assert page.locator("#parcelOmiEstimate strong").count() == 0
        page.select_option("#parcelOmiQuote", index=1)
        page.wait_for_selector("#parcelOmiEstimate strong")
        assert page.locator('#parcelStatStrip [data-stat="price"] [data-stat-text]').inner_text() != "—"
        assert "592" in page.inner_text("#parcelOmiEstimate")  # 80 m2 x 7,400 EUR/m2 = 592,000
        page.wait_for_selector("#parcelOmiHistory svg.parcel-history-chart", timeout=10000)
        page.select_option("#parcelOmiQuote", index=2)
        page.fill("#parcelOmiArea", "")
        assert "surface" in page.inner_text("#parcelOmiEstimate").lower()
    finally:
        page.close()


def test_search_deduplicates_polygon_choices_and_map_click_clears_stale_text(server, browser):
    duplicate = {
        "kind": "parcel", "id": 495784906341173, "label": "H501A048600.D",
        "reference": "H501A048600.D", "municipality_name": "Roma", "sheet": "486",
    }
    page, _ = _open_parcel(
        browser,
        server,
        {"width": 1440, "height": 900},
        route_overrides=[("/api/v1/map/search", 200, {"results": [duplicate, {**duplicate}]} )],
    )
    try:
        page.fill("#mapSearchInput", "H501A048600.D")
        page.wait_for_selector("#mapSearchResults:not([hidden]) .map-search-result")
        assert page.locator("#mapSearchResults .map-search-result").count() == 1
        page.locator("#mapSearchInput").press("Escape")
        assert page.locator("#mapSearchResults").is_hidden()

        page.fill("#mapSearchInput", "H501A048600.D")
        page.wait_for_selector("#mapSearchResults:not([hidden]) .map-search-result")
        page.mouse.click(700, 500)
        page.wait_for_function(
            "document.querySelector('#mapSearchInput').value === '' && document.querySelector('#mapSearchResults').hidden",
            timeout=10000,
        )
    finally:
        page.close()


def test_ambiguous_parcel_409_opens_distinct_search_choices(server, browser):
    choices = [
        {"kind": "parcel", "id": 111, "label": "H501A048600.D · polygon 111", "reference": REFERENCE},
        {"kind": "parcel", "id": 222, "label": "H501A048600.D · polygon 222", "reference": REFERENCE},
    ]
    page, _ = _open_parcel(
        browser,
        server,
        {"width": 1440, "height": 900},
        wait_for_panel=False,
        route_overrides=[
            ("/parcel/by-reference/", 409, {"detail": "This reference has multiple parcel polygons. Choose a specific map feature."}),
            ("/api/v1/map/search", 200, {"results": choices}),
        ],
    )
    try:
        page.wait_for_selector("#mapSearchResults:not([hidden]) .map-search-result")
        assert page.locator("#mapSearchResults .map-search-result").count() == 2
        assert page.input_value("#mapSearchInput") == REFERENCE
        assert "Search unavailable" not in page.inner_text("#mapSearchStatus")
    finally:
        page.close()


def test_language_switch_preserves_map_query_and_fragment(server, browser):
    page, _ = _open_parcel(browser, server, {"width": 1440, "height": 900}, fragment="sezione-omi")
    try:
        page.evaluate("""() => {
          const url = new URL(location.href);
          url.searchParams.set('basemap', 'osm');
          url.searchParams.set('layers', 'cadastral-parcels,administrative');
          url.searchParams.set('audit_state', 'keep-me');
          history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`);
          const link = document.createElement('a');
          link.id = 'auditItalianLanguageLink';
          link.href = '/map?lang=it';
          link.textContent = 'Italiano';
          document.body.append(link);
          link.click();
        }""")
        page.wait_for_function("new URLSearchParams(location.search).get('lang') === 'it'", timeout=10000)
        query = page.evaluate("Object.fromEntries(new URLSearchParams(location.search))")
        assert query["audit_state"] == "keep-me"
        assert query["parcel"] == REFERENCE
        assert query["lat"] and query["lng"] and query["zoom"]
        assert query["basemap"] == "osm"
        assert query["layers"] == "cadastral-parcels,administrative"
        assert page.evaluate("location.hash") == "#sezione-omi"
    finally:
        page.close()


def test_theme_switch_uses_existing_dark_basemap_and_respects_manual_choice(server, browser):
    page, _ = _open_parcel(
        browser,
        server,
        {"width": 1440, "height": 900},
        route_overrides=[("/api/v1/user/preferences", 200, {"default_basemap": None})],
    )
    try:
        page.locator("#themeToggle").click()
        page.wait_for_function("document.documentElement.getAttribute('data-bs-theme') === 'dark'")
        page.wait_for_function("document.querySelector('#basemapEngineSelect').value === 'dark'")
        dark_tiles = page.evaluate("window.landRegistryPurchaseMap.maps().map.getStyle().sources.basemap.tiles[0]")
        assert "World_Dark_Gray_Base" in dark_tiles

        page.locator("#basemapToggle").click()
        page.locator("#basemapEngineSelect").select_option("osm")
        page.locator("#themeToggle").click()
        page.wait_for_function("document.documentElement.getAttribute('data-bs-theme') === 'light'")
        assert page.input_value("#basemapEngineSelect") == "osm"
    finally:
        page.close()


def test_layer_filter_hides_nonmatching_groups_and_recovers_when_cleared(server, browser):
    page, _ = _open_parcel(browser, server, {"width": 1440, "height": 900})
    try:
        page.locator("#layersToggle").click()
        page.wait_for_function(
            "document.querySelectorAll('#mapLayerList .map-layer-item').length > 0",
            timeout=15000,
        )
        all_items = page.locator("#mapLayerList .map-layer-item").count()
        page.fill("#mapLayerFilter", "cadastral")
        page.wait_for_function(
            "[...document.querySelectorAll('#mapLayerList .map-layer-item')].some(item => !item.hidden)"
        )
        matching_items = page.locator("#mapLayerList .map-layer-item:not([hidden])")
        assert matching_items.count() > 0
        assert matching_items.count() < all_items
        assert page.locator("#mapLayerFilterEmpty").is_hidden()

        page.fill("#mapLayerFilter", "no-layer-has-this-name")
        page.wait_for_function("document.querySelector('#mapLayerFilterEmpty').hidden === false")
        assert page.locator("#mapLayerList .map-layer-item:not([hidden])").count() == 0

        page.fill("#mapLayerFilter", "")
        page.wait_for_function(
            "[...document.querySelectorAll('#mapLayerList .map-layer-item')].every(item => !item.hidden)"
        )
        assert page.locator("#mapLayerList .map-layer-item:not([hidden])").count() == all_items
    finally:
        page.close()


def test_live_panel_dashboard_keeps_its_websocket_when_available(browser):
    from urllib.request import urlopen

    from land_registry.main import PANEL_BASE_URL, PANEL_DASHBOARD_URL

    try:
        with urlopen(PANEL_BASE_URL, timeout=0.5) as response:
            if response.status >= 400:
                pytest.skip(f"Panel returned HTTP {response.status}")
    except Exception as exc:
        pytest.skip(f"Panel is unavailable for the integration smoke test: {exc}")

    page = browser.new_page()
    sockets = []
    page_errors = []
    console_errors = []
    page.on("websocket", lambda websocket: sockets.append(websocket))
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    try:
        response = page.goto(PANEL_DASHBOARD_URL, wait_until="domcontentloaded", timeout=20000)
        assert response and response.status == 200
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not any(
            websocket.url.endswith("/dashboard/ws") and not websocket.is_closed()
            for websocket in sockets
        ):
            page.wait_for_timeout(250)
        assert any(
            websocket.url.endswith("/dashboard/ws") and not websocket.is_closed()
            for websocket in sockets
        ), [websocket.url for websocket in sockets]
        assert page.title() == "Land Registry - Table View"
        assert page_errors == []
        assert console_errors == []
    finally:
        page.close()


def test_public_road_parcel_has_neutral_municipal_context_and_no_purchase_action(server, browser):
    road = {
        **PARCEL,
        "properties": {
            **PARCEL["properties"],
            "parcel": "STRADA307",
            "parcel_number": "STRADA307",
            "national_cadastral_reference": "H501A048600.STRADA307",
            "canonical_reference": "H501A048600.STRADA307",
        },
    }
    page, _ = _open_parcel(
        browser,
        server,
        {"width": 1440, "height": 900},
        parcel_payload=road,
        wait_for_panel=False,
    )
    try:
        page.wait_for_selector('#parcel-section-risks[data-state="ready"]')
        assert "Public road parcel" in page.inner_text(".parcel-domain-note")
        assert page.locator("#parcelPurchaseButton").is_hidden()
        for section in ("omi", "pvp", "solar"):
            assert page.locator(f"#parcel-section-{section}").count() == 0
        hazard = page.locator('[data-stat="hazard"]')
        assert hazard.get_attribute("data-level") is None
        assert "Municipal" in hazard.inner_text()
        assert "municipality, not this parcel" in hazard.get_attribute("title")
    finally:
        page.close()


def test_census_section_shows_age_pyramid_when_schema_fields_are_available(server, browser):
    properties = {"sez21_id": "S1", "p1": 100, "p2": 48, "p3": 52, "area_sqm": 1_000_000}
    for key in range(14, 30):
        properties[f"p{key}"] = 2
    for key in range(30, 46):
        properties[f"p{key}"] = 1
    for key in range(67, 83):
        properties[f"p{key}"] = 1
    page, _ = _open_parcel(
        browser,
        server,
        {"width": 390, "height": 844},
        route_overrides=[("/census/at-point", 200, {"type": "Feature", "properties": properties, "geometry": None})],
    )
    try:
        _scroll_through(page)
        page.wait_for_selector('#parcel-section-census[data-state="ready"]', timeout=15000)
        _open_section(page, "census")
        chart = page.locator("#parcel-section-census .parcel-census-demographics")
        assert chart.locator("summary").inner_text() == "Age and sex distribution (Census 2021)"
        chart.locator("summary").click()
        assert chart.locator("tbody tr").count() == 16
        assert chart.get_by_text("Male residents", exact=True).count() == 1
        assert chart.get_by_text("not residents of this parcel").count() == 1
        assert chart.is_visible()
    finally:
        page.close()


def test_energy_tab_shows_only_municipal_solar_aggregates(server, browser):
    read_model = {
        "national_reference": REFERENCE,
        "blocks": {
            "solar": {
                "available": True,
                "data": {
                    "pv_n_buildings": 125,
                    "pv_pvout_pessimistic_kwh_year_total": 940000,
                    "pv_pvout_modern_kwh_year_total": 1210000,
                    "pv_high_viability_pct": 18.5,
                    "pv_medium_viability_pct": 32,
                    "pv_low_viability_pct": 21.5,
                    "pv_not_eligible_pct": 28,
                },
                "source": "aecs4u-stats serving.municipality_profile",
                "dataset_version": "solar-fixture-2026",
                "updated_at": "2026-09-30",
                "spatial_resolution": "municipality",
                "match_method": "municipality",
            },
        },
    }
    page, _ = _open_parcel(
        browser,
        server,
        {"width": 390, "height": 844},
        route_overrides=[("/parcel/details/", 200, read_model)],
    )
    try:
        page.click('#parcelSectionNav button[data-tab="energy"]')
        page.wait_for_selector('#parcel-section-solar[data-state="ready"]', timeout=15000)
        page.wait_for_function(
            "document.querySelector('#parcelSectionNav button[data-tab=\\\"energy\\\"]').getAttribute('aria-current') === 'true'",
            timeout=10000,
        )
        assert page.get_attribute('#parcelSectionNav button[data-tab="energy"]', "aria-current") == "true"
        assert page.evaluate("location.hash") == "#energia"
        page.locator("#parcel-section-solar > summary").click()
        assert "940 MWh/year" in page.inner_text("#parcel-section-solar")
        assert re.search(r"1[,.]2 GWh/year", page.inner_text("#parcel-section-solar"))
        assert "not estimates for this parcel, building, or roof" in page.inner_text("#parcel-section-solar")
        assert "solar-fixture-2026" in page.inner_text("#parcel-section-solar")
        assert "2026-09-30" in page.inner_text("#parcel-section-solar")
    finally:
        page.close()


def test_pdf_export_downloads_the_server_built_dossier(server, browser):
    page, _ = _open_parcel(browser, server, {"width": 1440, "height": 900})
    requests = []

    def dossier(route):
        requests.append((route.request.method, route.request.post_data))
        route.fulfill(
            status=200,
            content_type="application/pdf",
            headers={"Content-Disposition": 'attachment; filename="parcel-dossier.pdf"'},
            body=b"%PDF-mock-dossier",
        )

    page.route("**/api/v1/enrichment/parcel/dossier/**", dossier)
    try:
        with page.expect_download(timeout=30000) as download_info:
            page.click("#parcelReportLink")
        assert download_info.value.suggested_filename == "parcel-dossier.pdf"
        # The dossier is built from server data: a plain GET, no panel snapshot is uploaded.
        assert requests == [("GET", None)]
    finally:
        page.close()


def test_tab_buttons_scroll_to_their_section_and_follow_the_scroll(server, browser):
    page, _ = _open_parcel(browser, server, {"width": 1440, "height": 900})
    try:
        assert page.get_attribute('#parcelSectionNav button[data-tab="value"]', "aria-current") == "true"
        page.click('#parcelSectionNav button[data-tab="context"]')
        page.wait_for_function(
            "document.querySelector('#parcelSectionNav button[data-tab=\"context\"]').getAttribute('aria-current') === 'true'"
        )
        assert page.get_attribute('#parcelSectionNav button[data-tab="value"]', "aria-current") is None
    finally:
        page.close()


def test_selecting_another_parcel_discards_the_previous_panel(server, browser):
    page, _ = _open_parcel(browser, server, {"width": 1440, "height": 900})
    try:
        page.click("#parcelClearButton")
        assert page.is_hidden("#directParcelPanel")
        assert page.evaluate("document.getElementById('directParcelContent').children.length") == 0
    finally:
        page.close()


def test_mobile_sheet_keeps_actions_in_one_row(server, browser):
    page, _ = _open_parcel(browser, server, {"width": 390, "height": 800})
    try:
        box = page.locator("#directParcelPanel").bounding_box()
        assert box["width"] >= 385
        assert box["height"] <= 800 * 0.55, box
        assert page.locator(".parcel-sheet-header #parcelShareButton").is_visible()
        assert page.locator(".parcel-sheet-header #parcelReportLink").is_visible()
        tops = page.evaluate("[...document.querySelectorAll('.parcel-detail-actions .secondary-action')].map(e => Math.round(e.getBoundingClientRect().top))")
        assert len(set(tops)) == 1, tops
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        initial_height = box["height"]
        page.locator("#parcelSheetExpandButton").click()
        page.wait_for_function("document.getElementById('directParcelPanel').classList.contains('is-expanded')")
        expanded_height = page.locator("#directParcelPanel").bounding_box()["height"]
        assert expanded_height > initial_height
        assert page.locator(".parcel-section-header").first.bounding_box()["height"] >= 44
    finally:
        page.close()


@pytest.mark.parametrize("width,height", [(1440, 900), (1024, 768), (768, 1024), (390, 844)])
def test_audit_viewports_keep_the_sheet_and_search_controls_in_bounds(server, browser, width, height):
    initial_center = (41.8986, 12.471) if width == 1440 else (41.8986, 12.4769)
    page, _ = _open_parcel(
        browser,
        server,
        {"width": width, "height": height},
        initial_center=initial_center,
    )
    try:
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        sheet = page.locator("#directParcelPanel").bounding_box()
        assert sheet["x"] >= 0 and sheet["y"] >= 0
        assert sheet["x"] + sheet["width"] <= width + 1
        assert sheet["y"] + sheet["height"] <= height + 1
        search = page.locator(".map-search-card").bounding_box()
        if width > 700:
            assert search["x"] + search["width"] <= sheet["x"] + 1, (width, search, sheet)
        else:
            assert sheet["height"] <= height * 0.55
        progress = page.locator("#mapTaskProgress")
        if progress.is_visible():
            assert not page.locator("#mapStatus").is_visible()
            progress_box = progress.bounding_box()
            attribution = page.locator(".maplibregl-ctrl-attrib").bounding_box()
            if attribution:
                no_overlap = (
                    progress_box["x"] + progress_box["width"] <= attribution["x"]
                    or attribution["x"] + attribution["width"] <= progress_box["x"]
                    or progress_box["y"] + progress_box["height"] <= attribution["y"]
                    or attribution["y"] + attribution["height"] <= progress_box["y"]
                )
                assert no_overlap, (progress_box, attribution)
        close = page.locator("#parcelCloseButton")
        assert close.is_visible()
        hit = close.evaluate("element => { const r=element.getBoundingClientRect(); return document.elementFromPoint(r.left+r.width/2,r.top+r.height/2) === element; }")
        assert hit
        if width == 1440:
            placement = page.evaluate("""() => {
              const map = window.landRegistryPurchaseMap?.maps().map;
              const sheet = document.querySelector('#directParcelPanel');
              const rect = sheet?.getBoundingClientRect();
              const mapRect = map?.getContainer().getBoundingClientRect();
              const point = map?.project([12.4769, 41.8986]);
              const center = map?.getCenter();
              return { rect: rect && { left: rect.left, top: rect.top, width: rect.width, height: rect.height },
                mapRect: mapRect && { left: mapRect.left, width: mapRect.width, height: mapRect.height },
                point: point && { x: point.x, y: point.y },
                center: center && { lng: center.lng, lat: center.lat }, zoom: map?.getZoom(), hidden: sheet?.hidden };
            }""")
            assert placement["mapRect"]["left"] + placement["point"]["x"] < placement["rect"]["left"] - 10, placement
    finally:
        page.close()
