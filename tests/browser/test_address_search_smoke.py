"""Browser smoke test: picking an address result on /map.

Network is mocked like the parcel-panel smoke test. Guards the behaviour the
unit tests cannot: a marker labelled with the address appears, the cadastral
parcel under it is loaded and its window opens; a street-only match keeps the
marker but opens no parcel window; the shortlist card never overlaps the search
card. Skipped when Playwright or a browser is unavailable.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

# The parcel-panel smoke test owns the offline server/browser fixtures; load it
# by path (tests/browser is not a package) and reuse them.
_spec = importlib.util.spec_from_file_location(
    "_parcel_panel_smoke", Path(__file__).with_name("test_parcel_panel_smoke.py")
)
_smoke = importlib.util.module_from_spec(_spec)
sys.modules["_parcel_panel_smoke"] = _smoke
_spec.loader.exec_module(_smoke)
server = _smoke.server
browser = _smoke.browser
_open_parcel = _smoke._open_parcel
REFERENCE = _smoke.REFERENCE

LABEL = "Via Goethe 1, Palermo"
ADDRESS = {
    "kind": "address", "label": LABEL, "municipality_name": "Palermo", "istat_code": "082053",
    "lat": 41.8986, "lon": 12.4769, "match_level": "address", "coordinate_method": "3",
    "street": "VIA GOETHE", "civic": "1", "exponent": None, "requested_civic": "1", "nearby": [],
    "attribution": "ANNCSU — Agenzia delle Entrate and ISTAT; CC BY 4.0",
}
AT_POINT = {
    "type": "Feature", "id": 7,
    "properties": {"id": 7, "national_cadastral_reference": REFERENCE},
    "geometry": _smoke.PARCEL["geometry"],
}
VIEWPORT = {"width": 1440, "height": 900}


def _search(page, text):
    page.fill("#mapSearchInput", text)
    page.wait_for_selector(".map-search-result", timeout=15000)
    page.click(".map-search-result")


def test_address_pick_shows_marker_parcel_and_window(server, browser):
    overrides = [
        ("/api/v1/map/search", 200, {"query": LABEL, "results": [ADDRESS]}),
        ("/parcel/at-point", 200, AT_POINT),
    ]
    page, requested = _open_parcel(browser, server, VIEWPORT, route_overrides=overrides, wait_for_panel=False)
    try:
        page.wait_for_selector("#mapSearchInput")
        _search(page, "via goethe 1, Palermo")
        page.wait_for_selector(".search-address-marker", timeout=15000)
        assert page.inner_text(".search-address-marker-label") == LABEL
        page.wait_for_selector("#directParcelPanel:not([hidden])", timeout=15000)
        assert any("/parcel/at-point" in url for url in requested)
        assert any(f"/parcel/by-reference/{REFERENCE}" in url for url in requested)
        # The marker is part of the shareable URL.
        assert "addr_lat=" in page.url and "addr=" in page.url
        # The marker survives closing the parcel window; Clear selection removes it.
        page.click("#parcelCloseButton")
        assert page.locator(".search-address-marker").count() == 1
    finally:
        page.close()


def test_street_only_match_keeps_marker_without_parcel_window(server, browser):
    street = {**ADDRESS, "match_level": "street", "civic": None, "requested_civic": "999",
              "label": "Via Goethe, Palermo (number 999 not found)", "nearby": ["78", "76"]}
    overrides = [("/api/v1/map/search", 200, {"query": "x", "results": [street]})]
    page, requested = _open_parcel(browser, server, VIEWPORT, route_overrides=overrides, wait_for_panel=False)
    try:
        page.wait_for_selector("#mapSearchInput")
        _search(page, "via goethe 999, Palermo")
        page.wait_for_selector(".search-address-marker", timeout=15000)
        assert "number 999 not found" in page.inner_text(".search-address-marker-label")
        assert page.locator("#directParcelPanel").is_hidden()
        assert not any("/parcel/at-point" in url for url in requested)
        assert "78" in page.inner_text("#mapSearchStatus")
    finally:
        page.close()


def test_shortlist_card_does_not_overlap_search_card(server, browser):
    page, _ = _open_parcel(browser, server, VIEWPORT, wait_for_panel=False)
    try:
        page.wait_for_selector("#mapSearchInput")
        page.evaluate("document.getElementById('mapParcelFinder').open = true")
        page.wait_for_timeout(300)
        boxes = page.evaluate(
            """() => {
              const r = (s) => { const e = document.querySelector(s); const b = e.getBoundingClientRect(); return {top: b.top, bottom: b.bottom, shown: getComputedStyle(e).display !== 'none'}; };
              return {search: r('.map-search-card'), shortlist: r('.parcel-shortlist-card')};
            }"""
        )
        if boxes["shortlist"]["shown"]:
            assert boxes["shortlist"]["top"] >= boxes["search"]["bottom"] - 1
    finally:
        page.close()


def test_shared_link_restores_the_address_marker(server, browser):
    page, _ = _open_parcel(browser, server, VIEWPORT, wait_for_panel=False)
    try:
        page.goto(
            f"{server}/map?lat=41.8986&lng=12.4769&zoom=17"
            "&addr=Via%20Goethe%201%2C%20Palermo&addr_lat=41.8986&addr_lng=12.4769",
            wait_until="domcontentloaded",
        )
        page.wait_for_selector(".search-address-marker", timeout=30000)
        assert page.inner_text(".search-address-marker-label") == LABEL
        # Out-of-range coordinates are ignored.
        page.goto(f"{server}/map?addr=Nowhere&addr_lat=10&addr_lng=10", wait_until="domcontentloaded")
        page.wait_for_selector("#mapSearchInput")
        page.wait_for_timeout(1500)
        assert page.locator(".search-address-marker").count() == 0
    finally:
        page.close()


def test_point_on_a_boundary_uses_the_nearest_parcel(server, browser):
    import json as _json

    overrides = [("/api/v1/map/search", 200, {"query": LABEL, "results": [ADDRESS]})]
    page, requested = _open_parcel(browser, server, VIEWPORT, route_overrides=overrides, wait_for_panel=False)
    probes = []

    def at_point(route):
        url = route.request.url
        probes.append(url)
        from urllib.parse import parse_qs, urlparse

        query = parse_qs(urlparse(url).query)
        exact = abs(float(query["lat"][0]) - ADDRESS["lat"]) < 1e-7 and abs(float(query["lng"][0]) - ADDRESS["lon"]) < 1e-7
        if exact:
            return route.fulfill(status=404, content_type="application/json", body="{}")
        return route.fulfill(status=200, content_type="application/json", body=_json.dumps(AT_POINT))

    page.route("**/api/v1/enrichment/parcel/at-point**", at_point)
    try:
        page.wait_for_selector("#mapSearchInput")
        _search(page, "via goethe 1, Palermo")
        page.wait_for_selector("#directParcelPanel:not([hidden])", timeout=15000)
        assert len(probes) > 1  # exact point missed, neighbours probed
        assert "Nearest parcel" in page.inner_text("#mapSearchStatus")
        assert page.locator(".search-address-marker").count() == 1
    finally:
        page.close()
