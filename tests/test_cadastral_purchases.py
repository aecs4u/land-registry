"""Purchase isolation, aecs4u-billing handoff and signed fulfillment contracts."""

import base64
import hashlib
import hmac
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from land_registry.cadastral_purchases import PurchaseSettings, PurchaseStore
from land_registry.routers import cadastral_purchases as routes

API = "/api/v1/cadastral-purchases"
HEADERS = {"X-Purchase-Request": "1"}
TARGET = {
    "provider": "sister",
    "municipality_code": "H501",
    "sheet": "12",
    "parcel": "345",
    "registry": "land",
    "national_reference": "H501_0012.345",
    "subaltern": None,
    "geometry": {"type": "Point", "coordinates": [12.5, 41.9]},
}
PDF = base64.b64encode(b"%PDF-1.7\nprivate-cadastral-document").decode()


@pytest.fixture
def purchase_app(tmp_path, monkeypatch):
    store = PurchaseStore(str(tmp_path / "orders.sqlite"))
    settings = PurchaseSettings(
        public_base_url="https://land.example",
        sister_gateway_url="https://sister.example/purchases",
        sister_api_token="private-token",
        sister_webhook_secret="provider-secret",
        opendata_gateway_url="https://opendata.example/purchases",
        opendata_api_token="private-token",
        opendata_webhook_secret="opendata-secret",
    )
    config = SimpleNamespace(site_id="land-registry", stripe_api_key="sk_test", stripe_webhook_secret="stripe-secret")
    monkeypatch.setattr(routes, "get_billing_config", lambda: config)
    monkeypatch.setattr("aecs4u_billing.config.get_billing_config", lambda: config)
    user = SimpleNamespace(id="customer-a", email="a@example.test", roles=["customer"], is_superuser=False)
    sessions = {}

    def checkout(**kwargs):
        pid = kwargs["metadata"]["purchase_id"]
        sessions[pid] = {
            "id": f"cs_{pid}",
            "mode": "payment",
            "currency": "eur",
            "payment_status": "paid",
            "amount_total": kwargs["price_data"]["unit_amount"],
            "client_reference_id": kwargs["client_reference_id"],
            "metadata": {**kwargs["metadata"], "site_id": "land-registry"},
        }
        return SimpleNamespace(id=f"cs_{pid}", url=f"https://checkout.stripe.com/c/pay/{pid}")

    billing = Mock()
    billing.create_checkout_session.side_effect = checkout
    monkeypatch.setattr(routes, "get_stripe_client", lambda: billing)

    async def gateway(settings, provider, path, payload, key):
        pid = payload["purchase_id"]
        if path == "quotes":
            return {
                "quote_id": f"q_{pid}",
                "amount_cents": 1290,
                "currency": "EUR",
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            }
        assert path == "orders"
        return {"order_id": f"order_{pid}"}

    gateway_mock = AsyncMock(side_effect=gateway)
    monkeypatch.setattr(routes, "gateway_post", gateway_mock)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1")
    app.dependency_overrides[routes.get_current_user] = lambda: user
    app.dependency_overrides[routes.get_purchase_store] = lambda: store
    app.dependency_overrides[routes.get_purchase_settings] = lambda: settings
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client,
            app=app,
            user=user,
            settings=settings,
            store=store,
            config=config,
            billing=billing,
            gateway=gateway_mock,
            sessions=sessions,
        )


def quote_order(env, target=None, key=None):
    response = env.client.post(
        API + "/quote", json=target or TARGET, headers={**HEADERS, "Idempotency-Key": str(key or uuid4())}
    )
    assert response.status_code == 200, response.text
    return response.json()


def checkout_order(env, target=None):
    item = quote_order(env, target)
    response = env.client.post(f"{API}/{item['id']}/checkout", headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


def billing_event(env, pid, *, event_type="checkout.session.completed", changes=None):
    session = {**env.sessions[pid], **(changes or {})}
    payload = {"id": "evt_test", "type": event_type, "data": {"object": session}}
    body = json.dumps(payload).encode()
    timestamp = str(int(time.time()))
    signature = hmac.new(b"stripe-secret", timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return env.client.post(
        API + "/webhooks/billing", content=body, headers={"Stripe-Signature": f"t={timestamp},v1={signature}"}
    )


def provider_event(env, pid, *, provider="sister", status="available", timestamp=None, changes=None, secret=None):
    payload = {"purchase_id": pid, "order_id": f"order_{pid}", "status": status}
    if status == "available":
        payload.update(record={"owners": [{"name": "Private Owner"}]}, document_pdf_base64=PDF)
    payload.update(changes or {})
    body = json.dumps(payload).encode()
    timestamp = str(timestamp or int(time.time()))
    secret = secret or getattr(env.settings, f"{provider}_webhook_secret")
    signature = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return env.client.post(
        f"{API}/webhooks/{provider}",
        content=body,
        headers={"X-Purchase-Timestamp": timestamp, "X-Purchase-Signature": signature},
    )


def complete_order(env, target=None):
    item = checkout_order(env, target)
    assert billing_event(env, item["id"]).status_code == 200
    assert provider_event(env, item["id"], provider=item["provider"]).status_code == 200
    return item


@pytest.mark.parametrize("provider", ["sister", "opendata"])
def test_purchase_workflow_uses_billing_then_private_fulfillment(purchase_app, provider):
    env = purchase_app
    item = checkout_order(env, {**TARGET, "provider": provider})
    pid = item["id"]
    assert item["status"] == "awaiting_payment"
    assert item["paid"] is False
    assert env.gateway.await_count == 1  # A quote never places a provider order.
    assert env.client.get(API + "/map").json()["features"] == []
    assert "record" not in env.client.get(f"{API}/{pid}").json()
    args = env.billing.create_checkout_session.call_args.kwargs
    assert args["mode"] == "payment"
    assert args["price_data"]["unit_amount"] == 1290
    assert args["metadata"]["user_id"] == "customer-a"
    assert args["idempotency_key"] == f"cadastral-checkout:{pid}"
    assert billing_event(env, pid).status_code == 200
    assert env.client.get(f"{API}/{pid}").json()["status"] == "processing"
    assert env.gateway.call_args.args[2] == "orders"
    assert provider_event(env, pid, provider=provider).status_code == 200
    response = env.client.get(f"{API}/{pid}")
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json()["record"]["owners"][0]["name"] == "Private Owner"
    document = env.client.get(response.json()["document_url"])
    assert document.content == base64.b64decode(PDF)
    assert document.headers["cache-control"] == "private, no-store"
    features = env.client.get(API + "/map").json()["features"]
    assert features[0]["properties"]["purchase_id"] == pid
    assert "Private Owner" not in json.dumps(features)


def test_purchase_owner_and_admin_access(purchase_app):
    env = purchase_app
    item = complete_order(env)
    pid = item["id"]
    env.user.id = "customer-b"
    for path in (f"/{pid}", f"/{pid}/document"):
        assert env.client.get(API + path).status_code == 404
    assert env.client.post(f"{API}/{pid}/checkout", headers=HEADERS).status_code == 404
    assert env.client.get(API).json()["items"] == []
    assert env.client.get(API + "/map").json()["features"] == []
    env.user.roles = ["admin"]
    assert env.client.get(f"{API}/{pid}").status_code == 200
    assert env.client.get(f"{API}/{pid}/document").status_code == 200
    assert len(env.client.get(API + "/map").json()["features"]) == 1
    assert env.client.get(API).json()["items"][0]["can_checkout"] is False
    assert env.client.post(f"{API}/{pid}/checkout", headers=HEADERS).status_code == 404


def test_anonymous_cannot_read_or_buy(purchase_app):
    from fastapi import HTTPException

    env = purchase_app

    def denied():
        raise HTTPException(401, "Sign in required")

    env.app.dependency_overrides[routes.get_current_user] = denied
    for path in ("", "/map", "/providers", "/unknown", "/unknown/document"):
        assert env.client.get(API + path).status_code == 401
    assert (
        env.client.post(API + "/quote", json=TARGET, headers={**HEADERS, "Idempotency-Key": str(uuid4())}).status_code
        == 401
    )


def test_quotes_and_checkouts_are_idempotent_and_price_is_server_owned(purchase_app):
    env = purchase_app
    key = uuid4()
    first = quote_order(env, key=key)
    second = quote_order(env, key=key)
    assert first["id"] == second["id"]
    assert env.gateway.await_count == 1
    assert (
        env.client.post(
            API + "/quote", json={**TARGET, "parcel": "346"}, headers={**HEADERS, "Idempotency-Key": str(key)}
        ).status_code
        == 409
    )
    path = f"{API}/{first['id']}/checkout"
    assert env.client.post(path, headers=HEADERS).json() == env.client.post(path, headers=HEADERS).json()
    assert env.billing.create_checkout_session.call_count == 1
    assert (
        env.client.post(
            API + "/quote", json={**TARGET, "amount_cents": 1}, headers={**HEADERS, "Idempotency-Key": str(uuid4())}
        ).status_code
        == 422
    )
    env.user.id = "customer-b"
    assert quote_order(env, key=key)["id"] != first["id"]


def test_provider_cannot_grant_unpaid_access(purchase_app):
    env = purchase_app
    item = checkout_order(env)
    assert provider_event(env, item["id"]).status_code == 409
    assert provider_event(env, item["id"], changes={"paid": True}).status_code == 422
    assert env.client.get(API + "/map").json()["features"] == []


def test_unpaid_checkout_does_not_order_query(purchase_app):
    env = purchase_app
    item = checkout_order(env)
    response = billing_event(env, item["id"], changes={"payment_status": "unpaid"})
    assert response.status_code == 200
    assert env.gateway.await_count == 1
    assert env.client.get(f"{API}/{item['id']}").json()["paid"] is False
    assert billing_event(env, item["id"], event_type="checkout.session.async_payment_succeeded").status_code == 200
    assert env.gateway.await_count == 2


@pytest.mark.parametrize("amount, expected_orders", [(0, 1), (1290, 0)])
def test_no_payment_required_only_authorizes_a_zero_amount_quote(purchase_app, amount, expected_orders):
    env = purchase_app
    real_gateway = env.gateway.side_effect

    async def gateway(*args):
        response = await real_gateway(*args)
        if args[2] == "quotes":
            response["amount_cents"] = amount
        return response

    env.gateway.side_effect = gateway
    item = checkout_order(env)
    assert billing_event(env, item["id"], changes={"payment_status": "no_payment_required"}).status_code == 200
    assert env.gateway.await_count == 1 + expected_orders
    assert env.client.get(f"{API}/{item['id']}").json()["paid"] is bool(expected_orders)


@pytest.mark.parametrize(
    "changes",
    [
        {"amount_total": 1},
        {"currency": "usd"},
        {"client_reference_id": "customer-b"},
        {"id": "cs_wrong"},
        {"mode": "subscription"},
    ],
)
def test_billing_verifies_order_customer_amount_and_session(purchase_app, changes):
    env = purchase_app
    item = checkout_order(env)
    assert billing_event(env, item["id"], changes=changes).status_code == 409
    assert env.gateway.await_count == 1


def test_signatures_and_provider_bindings(purchase_app):
    env = purchase_app
    item = checkout_order(env)
    assert env.client.post(API + "/webhooks/billing", json={}).status_code == 401
    assert provider_event(env, item["id"], secret="wrong-secret").status_code == 401
    assert provider_event(env, item["id"], timestamp=int(time.time()) - 600).status_code == 401
    assert billing_event(env, item["id"]).status_code == 200
    assert provider_event(env, item["id"], provider="opendata").status_code == 404
    assert provider_event(env, item["id"], changes={"order_id": "another-order"}).status_code == 409


def test_duplicate_callbacks_do_not_reorder_or_overwrite_results(purchase_app):
    env = purchase_app
    item = complete_order(env)
    pid = item["id"]
    calls = env.gateway.await_count
    assert billing_event(env, pid).status_code == 200
    assert env.gateway.await_count == calls
    assert provider_event(env, pid, changes={"record": {"tampered": True}}).json()["changed"] is False
    assert provider_event(env, pid, status="failed").json()["changed"] is False
    assert provider_event(env, pid, status="processing").json()["changed"] is False
    assert env.client.get(f"{API}/{pid}").json()["record"] == {"owners": [{"name": "Private Owner"}]}


def test_delivery_failure_is_durable_and_retries_without_charging_again(purchase_app):
    env = purchase_app
    item = checkout_order(env)
    real_gateway = env.gateway.side_effect
    env.gateway.side_effect = httpx.ConnectError("Provider down")
    assert billing_event(env, item["id"]).status_code == 502
    detail = env.client.get(f"{API}/{item['id']}").json()
    assert detail["paid"] is True and detail["status"] == "paid"
    env.gateway.side_effect = real_gateway
    assert billing_event(env, item["id"]).status_code == 200
    assert env.billing.create_checkout_session.call_count == 1
    assert env.gateway.call_args.args[-1] == f"cadastral-order:{item['id']}"


@pytest.mark.parametrize(
    "headers", [{}, {**HEADERS, "Origin": "https://evil.example"}, {**HEADERS, "Sec-Fetch-Site": "cross-site"}]
)
def test_browser_requests_cannot_initiate_cross_origin_spending(purchase_app, headers):
    headers = {**headers, "Idempotency-Key": str(uuid4())}
    assert purchase_app.client.post(API + "/quote", json=TARGET, headers=headers).status_code == 403


def test_unconfigured_providers_fail_closed_but_existing_purchases_remain_readable(purchase_app):
    env = purchase_app
    item = complete_order(env)
    env.settings.sister_api_token = ""
    assert env.client.get(API + "/providers").json()["providers"][0]["enabled"] is False
    assert (
        env.client.post(API + "/quote", json=TARGET, headers={**HEADERS, "Idempotency-Key": str(uuid4())}).status_code
        == 503
    )
    assert env.client.get(f"{API}/{item['id']}").status_code == 200


@pytest.mark.parametrize(
    "changes",
    [
        {"record": None, "document_pdf_base64": None},
        {"document_pdf_base64": "not-base64"},
        {"document_pdf_base64": base64.b64encode(b"Not a PDF").decode()},
    ],
)
def test_malformed_provider_records_are_rejected_without_disclosure(purchase_app, changes):
    env = purchase_app
    item = checkout_order(env)
    assert billing_event(env, item["id"]).status_code == 200
    response = provider_event(env, item["id"], changes=changes)
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid purchase event"}
    assert env.client.get(API + "/map").json()["features"] == []


def test_quote_expiry_cannot_create_checkout(purchase_app):
    env = purchase_app
    item = quote_order(env)
    import asyncio

    quote = {**item["quote"], "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()}
    asyncio.run(env.store.update(item["id"], ("quoted",), quote_json=json.dumps(quote)))
    assert env.client.post(f"{API}/{item['id']}/checkout", headers=HEADERS).status_code == 409
    assert env.billing.create_checkout_session.call_count == 0


def test_templates_mount_private_purchase_workflow_and_map_bridge():
    root = Path(__file__).resolve().parents[1]
    for template in ("map_v2.html", "theme_overrides/map_v2.html"):
        text = (root / "land_registry/templates" / template).read_text()
        assert 'id="parcelPurchaseButton"' in text
        assert 'id="purchasesOpenButton"' in text
        assert 'include "partials/cadastral_purchases.html"' in text
        assert "asset_url('cadastral-purchases.js')" in text
    text = (root / "land_registry/static/map-v2.js").read_text()
    assert "window.landRegistryPurchaseMap" in text
    assert "cadastral-map-ready" in text


@pytest.mark.parametrize("use_theme", [False, True])
def test_map_renders_purchase_partial_with_both_template_loaders(monkeypatch, use_theme):
    import asyncio

    from starlette.requests import Request

    from land_registry import main

    if use_theme and main._theme_setup is None:
        pytest.skip("Theme package not available")
    if not use_theme:
        monkeypatch.setattr(main, "_theme_setup", None)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/map",
            "root_path": "",
            "scheme": "http",
            "server": ("testserver", 80),
            "headers": [],
            "query_string": b"",
        }
    )
    response = asyncio.run(main.serve_direct_map(request, None))
    assert response.status_code == 200
    assert b'id="cadastralPurchaseDialog"' in response.body
    assert b'id="parcelPurchaseButton"' in response.body
    if use_theme:
        # The new shared partial must not shadow the theme's base template.
        assert "aecs4u_theme" in main._theme_setup.templates.env.get_template("base.html").filename
