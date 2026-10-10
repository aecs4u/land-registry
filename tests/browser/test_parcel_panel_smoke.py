"""Browser smoke test: opening a parcel on /map fills the side sheet section by section.

All parcel data is mocked at the network layer, so the test needs no database
or ISTAT store. It guards the behaviours that unit tests cannot: sections load
lazily and independently, a failing source never blanks the panel, the tab bar
follows the scroll position, and the OMI estimator reacts to input. Skipped
when Playwright or a browser is unavailable.
"""

import json
import socket
import threading
import time

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

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    instance = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning", lifespan="off",
    ))
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


def _open_parcel(browser, server, viewport, route_overrides=None, wait_for_panel=True, fragment=None):
    page = browser.new_page(viewport=viewport, locale="en-US")
    requested = []

    def handle(route):
        url = route.request.url
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
                    return route.fulfill(status=status, content_type="application/json", body=json.dumps(body))
            return route.fulfill(status=404, content_type="application/json", body="{}")
        if not url.startswith(server) and route.request.resource_type in {"fetch", "xhr", "image"}:
            return route.abort()
        return route.continue_()

    page.route("**/*", handle)
    url = f"{server}/map?lat=41.8986&lng=12.4769&zoom=17&parcel={REFERENCE}"
    if fragment:
        url += f"#{fragment}"
    page.goto(url, wait_until="domcontentloaded")
    if wait_for_panel:
        page.wait_for_selector('#parcel-section-omi[data-state="ready"]', timeout=45000)
    return page, requested


def _state(page, section):
    return page.evaluate(f"document.querySelector('#parcel-section-{section}').dataset.state")


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
        assert "Via Roma 1" in page.inner_text("#parcel-section-address")
        assert "not a geocoded address register" in page.inner_text("#parcel-section-address")
        assert any("/parcel/details/" in url and "view=panel" in url for url in requested)
        assert page.inner_text('[data-stat="area"] .parcel-stat-value').startswith("5545")
        assert f"/api/v1/enrichment/parcel/report/{REFERENCE}" in page.get_attribute("#parcelReportLink", "href")
        assert page.get_attribute("#parcelReportLink", "aria-disabled") == "false"
        page.wait_for_function("document.querySelector('[data-stat=\"buildings\"] .parcel-stat-value').textContent === '2'")
        page.wait_for_function("document.querySelector('[data-stat=\"seismic\"] .parcel-stat-value').textContent.includes('3')")
        assert "PGA (10 percent in 50 years)" in page.inner_text("#parcel-section-risks")
        assert "0,123 g" in page.inner_text("#parcel-section-risks")
        assert any("/mps04/pga?" in url for url in requested)
        # Lazy sections wait until they approach the viewport.
        assert not any("/income/" in url for url in requested)

        _scroll_through(page)
        assert page.locator(".parcel-sheet-header #parcelShareButton").is_visible()
        assert page.locator(".parcel-sheet-header #parcelReportLink").is_visible()
        page.wait_for_selector('#parcel-section-income[data-state="ready"]', timeout=15000)

        # The bulletin source returned 503: that card explains and offers a retry; the rest is intact.
        page.wait_for_selector('#parcel-section-bulletin[data-state="error"]', timeout=15000)
        assert page.locator("#parcel-section-bulletin .parcel-retry").count() == 1
        assert "temporarily unavailable" in page.inner_text("#parcel-section-bulletin")
        assert _state(page, "income") == "ready"
        assert _state(page, "census") == "empty"
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
    try:
        page.wait_for_function(
            "document.querySelector('#mapStatus').textContent.includes('Bolzano and Trento')",
            timeout=15000,
        )
        assert any("/parcel/by-reference/" in url for url in requested)
        assert "not currently covered" in page.inner_text("#mapStatus")
    finally:
        page.close()


@pytest.mark.parametrize("fragment", ["sezione-income", "parcel-section-income"])
def test_section_deep_links_restore_and_track_the_selected_card(server, browser, fragment):
    page, requested = _open_parcel(
        browser,
        server,
        {"width": 1440, "height": 900},
        fragment=fragment,
    )
    try:
        page.wait_for_selector('#parcel-section-income[data-state="ready"]', timeout=15000)
        page.wait_for_function("location.hash === '#sezione-income'", timeout=10000)
        assert page.get_attribute('#parcelSectionNav button[data-tab="context"]', "aria-current") == "true"
        assert any("/income/H501" in url for url in requested)

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
        assert card.evaluate("element => element.open") is True
        body = card.locator(".parcel-section-body")
        assert body.is_visible()

        card.locator(".parcel-section-header").click()
        assert card.evaluate("element => element.open") is False
        assert not body.is_visible()

        card.locator(".parcel-section-header").click()
        assert card.evaluate("element => element.open") is True
        assert body.is_visible()
    finally:
        page.close()


def test_omi_estimator_reacts_to_selection_and_surface(server, browser):
    page, _ = _open_parcel(browser, server, {"width": 1440, "height": 900})
    try:
        assert "B31" in page.inner_text("#parcel-section-omi .parcel-callout")
        page.fill("#parcelOmiArea", "80")
        page.wait_for_selector("#parcelOmiEstimate strong")
        assert "592" in page.inner_text("#parcelOmiEstimate")  # 80 m2 x 7,400 EUR/m2 = 592,000
        page.wait_for_selector("#parcelOmiHistory svg.parcel-history-chart", timeout=10000)
        page.select_option("#parcelOmiQuote", index=1)
        page.fill("#parcelOmiArea", "")
        assert "surface" in page.inner_text("#parcelOmiEstimate").lower()
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
        assert "940.000 kWh/year" in page.inner_text("#parcel-section-solar")
        assert "not estimates for this parcel, building, or roof" in page.inner_text("#parcel-section-solar")
        assert "solar-fixture-2026" in page.inner_text("#parcel-section-solar")
        assert "2026-09-30" in page.inner_text("#parcel-section-solar")
    finally:
        page.close()


def test_pdf_export_posts_the_complete_panel_snapshot(server, browser):
    page, _ = _open_parcel(browser, server, {"width": 1440, "height": 900})
    reports = []

    def report(route):
        reports.append((route.request.post_data_json, len(route.request.post_data.encode("utf-8"))))
        route.fulfill(status=200, content_type="application/pdf", body=b"%PDF-mock-report")

    page.route("**/api/v1/enrichment/parcel/report/**", report)
    try:
        with page.expect_download(timeout=30000) as download_info:
            page.click("#parcelReportLink")
        assert download_info.value.suggested_filename == "parcel-report.pdf"
        assert len(reports) == 1
        snapshot, request_size = reports[0]
        assert request_size <= 220_000
        sections = snapshot["sections"]
        expected_count = page.locator(".parcel-section").count()
        assert len(sections) == expected_count
        assert {section["id"] for section in sections} >= {"identity", "omi", "census", "coverage"}
        assert all(section["state"] in {"ready", "empty", "error"} for section in sections)
        omi = next(section for section in sections if section["id"] == "omi")
        assert "OMI zone and property type" in omi["body"]
        assert "B31" in omi["body"]
        assert omi["metadata"]["source"] == "OMI"

        # Dense panel text stays within the request budget while preserving
        # every section and marking the shortened bodies in the PDF payload.
        page.evaluate("""() => document.querySelectorAll('.parcel-section-body').forEach((body) => {
          body.textContent = 'x'.repeat(18000);
        })""")
        with page.expect_download(timeout=30000):
            page.click("#parcelReportLink")
        assert len(reports) == 2
        bounded_snapshot, request_size = reports[1]
        assert request_size <= 220_000
        assert len(bounded_snapshot["sections"]) == expected_count
        assert sum(len(section["body"]) for section in bounded_snapshot["sections"]) <= 180_000
        assert any("Section text was shortened" in section["body"] for section in bounded_snapshot["sections"])
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
        assert page.locator(".parcel-sheet-header #parcelShareButton").is_visible()
        assert page.locator(".parcel-sheet-header #parcelReportLink").is_visible()
        tops = page.evaluate("[...document.querySelectorAll('.parcel-detail-actions .secondary-action')].map(e => Math.round(e.getBoundingClientRect().top))")
        assert len(set(tops)) == 1, tops
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    finally:
        page.close()
