"""Offline browser checks for the map purchase dialog; no live payments."""

import json
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

playwright_sync = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def browser():
    with playwright_sync.sync_playwright() as playwright:
        try:
            instance = playwright.chromium.launch()
        except playwright_sync.Error as exc:
            pytest.skip(f"Chromium unavailable: {exc}")
        yield instance
        instance.close()


def purchase_page(browser, *, signed_in=True, available=False):
    page = browser.new_page(viewport={"width": 960, "height": 800})
    template = Environment(loader=FileSystemLoader(ROOT / "land_registry/templates"))
    dialog = template.get_template("partials/cadastral_purchases.html").render(_=lambda text: text)
    html = (
        """<!doctype html><html lang="en"><head><link rel="stylesheet" href="/purchases.css"></head><body>
    <button id="parcelPurchaseButton">Purchase cadastral query</button>
    <button id="purchasesOpenButton">My purchases</button><p id="mapStatus"></p>
    """
        + dialog
        + """<script>
    window.landRegistrySignedIn = SIGNED_IN;
    const sources = {};
    const layers = {};
    window.testMap = {
        isStyleLoaded: () => true,
        getSource: (id) => sources[id],
        addSource: (id, source) => { sources[id] = {data: source.data, setData(data) { this.data = data; }}; },
        addLayer: (layer) => { layers[layer.id] = layer; },
        getLayer: (id) => layers[id],
        on: () => {},
        fitBounds: (bounds) => { window.testBounds = bounds; },
    };
    window.landRegistryPurchaseMap = {
        selection: () => ({reference: 'H501_0012.345', feature: {
            properties: {municipality_code: 'H501', sheet: '12', parcel: '345'},
            geometry: {type: 'Point', coordinates: [12.5, 41.9]}
        }}),
        maps: () => ({map: window.testMap}),
    };
    </script><script src="/purchases.js" defer></script></body></html>"""
    )
    html = html.replace("SIGNED_IN", "true" if signed_in else "false")
    calls = []
    item = {
        "id": "purchase-test",
        "provider": "sister",
        "status": "available" if available else "quoted",
        "can_checkout": True,
        "quote": {"amount_cents": 1290, "currency": "EUR", "expires_at": "2030-01-01T10:00:00Z"},
        "target": {"national_reference": "H501_0012.345"},
    }
    collection = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [12.5, 41.9]},
                "properties": {"purchase_id": item["id"], "national_reference": "H501_0012.345", "provider": "sister"},
            }
        ]
        if available
        else [],
    }

    def serve(route):
        request = route.request
        path = request.url.split("purchase.test", 1)[-1]
        if path == "/map":
            return route.fulfill(content_type="text/html", body=html)
        if path == "/purchases.js":
            return route.fulfill(
                content_type="application/javascript",
                body=(ROOT / "land_registry/static/cadastral-purchases.js").read_text(),
            )
        if path == "/purchases.css":
            return route.fulfill(
                content_type="text/css", body=(ROOT / "land_registry/static/cadastral-purchases.css").read_text()
            )
        calls.append((path, request.method, request.post_data))
        prefix = "/api/v1/cadastral-purchases"
        if path == prefix + "/providers":
            data = {"providers": [{"id": "sister", "name": "SISTER", "enabled": True}]}
        elif path == prefix + "/map":
            data = collection
        elif path == prefix + "/quote":
            data = item
        elif path == prefix + "/purchase-test/checkout":
            data = {"status": "awaiting_payment", "checkout_url": "https://purchase.test/checkout"}
        elif path == prefix + "/purchase-test":
            data = {
                **item,
                "record": {"owner": "Private customer record"},
                "document_url": prefix + "/purchase-test/document",
            }
        elif path == "/checkout":
            return route.fulfill(content_type="text/html", body="Hosted payment page")
        else:
            data = {"items": [item] if available else [], "admin": False}
        route.fulfill(content_type="application/json", body=json.dumps(data))

    page.route("**/*", serve)
    page.goto("https://purchase.test/map", wait_until="domcontentloaded")
    return page, calls


def test_customer_reviews_quote_and_opens_billing_checkout(browser):
    page, calls = purchase_page(browser)
    page.click("#parcelPurchaseButton")
    playwright_sync.expect(page.locator("#cadastralPurchaseForm")).to_be_visible()
    playwright_sync.expect(page.locator("#purchaseProvider")).to_have_value("sister")
    page.click("#purchaseQuoteButton")
    playwright_sync.expect(page.locator("#purchaseQuoteSummary")).to_contain_text("€12.90")
    quote = next(json.loads(body) for path, method, body in calls if path.endswith("/quote"))
    assert quote["municipality_code"] == "H501"
    assert quote["sheet"] == "12" and quote["parcel"] == "345"
    assert not any(path.endswith("/checkout") for path, _, _ in calls)
    page.click("#purchasePayButton")
    page.wait_for_url("https://purchase.test/checkout")
    assert page.inner_text("body") == "Hosted payment page"
    page.close()


def test_available_record_is_drawn_and_can_be_opened_and_located(browser):
    page, _ = purchase_page(browser, available=True)
    page.click("#purchasesOpenButton")
    page.get_by_role("button", name="View purchased record").click()
    playwright_sync.expect(page.locator("#purchaseRecord")).to_contain_text("Private customer record")
    assert page.locator("#purchaseDocument").get_attribute("href").endswith("/purchase-test/document")
    assert page.evaluate("window.testMap.getSource('private-cadastral-purchases').data.features.length") == 1
    page.get_by_role("button", name="Show on map").click()
    assert page.evaluate("window.testBounds") == [[12.5, 41.9], [12.5, 41.9]]
    playwright_sync.expect(page.locator("#cadastralPurchaseDialog")).not_to_be_visible()
    page.close()


def test_signed_out_dialog_offers_sign_in_without_private_requests(browser):
    page, calls = purchase_page(browser, signed_in=False)
    page.click("#parcelPurchaseButton")
    playwright_sync.expect(page.get_by_role("link", name="Sign in", exact=True)).to_be_visible()
    playwright_sync.expect(page.locator("#cadastralPurchaseForm")).not_to_be_visible()
    assert calls == []
    page.close()
