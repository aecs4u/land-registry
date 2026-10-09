"""Authenticated purchase APIs and provider-signed fulfillment callbacks."""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx
import stripe
from aecs4u_billing.config import get_billing_config
from aecs4u_billing.stripe_client import get_stripe_client
from aecs4u_billing.webhooks.verify import verify_stripe_webhook
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import ValidationError

from land_registry.cadastral_purchases import (
    GatewayOrder,
    GatewayQuote,
    Provider,
    PurchaseEvent,
    PurchaseSettings,
    PurchaseStore,
    PurchaseTarget,
    gateway_post,
    https_url,
    is_purchase_admin,
    purchase_response,
)
from land_registry.routers.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/cadastral-purchases", tags=["cadastral-purchases"])


@lru_cache(maxsize=1)
def get_purchase_store():
    return PurchaseStore()


def get_purchase_settings():
    return PurchaseSettings()


Store = Annotated[PurchaseStore, Depends(get_purchase_store)]
Settings = Annotated[PurchaseSettings, Depends(get_purchase_settings)]
User = Annotated[object, Depends(get_current_user)]


def private_response(response: Response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Cookie, Authorization"


def same_origin(request: Request):
    # JSON requests use a custom header, so browser form submissions cannot
    # initiate spending. Origin validation also rejects credentialed CORS calls.
    origin = request.headers.get("origin")
    if (
        request.headers.get("X-Purchase-Request") != "1"
        or request.headers.get("sec-fetch-site") == "cross-site"
        or (origin and origin.rstrip("/") != str(request.base_url).rstrip("/"))
    ):
        raise HTTPException(403, "Purchase requests must originate from this app")


def configured(settings: PurchaseSettings, provider: Provider):
    if not settings.enabled(provider):
        raise HTTPException(503, "Purchases are not configured for this provider")


async def owned(store: PurchaseStore, purchase_id: str, user, *, customer_only=False):
    row = await store.get(
        purchase_id,
        str(user.id) if customer_only or not is_purchase_admin(user) else None,
    )
    if row is None:
        raise HTTPException(404, "Purchase not found")
    return row


@router.get("/providers")
async def providers(settings: Settings, user: User, response: Response):
    private_response(response)
    return {
        "providers": [
            {"id": provider, "name": name, "enabled": settings.enabled(provider)}
            for provider, name in (("sister", "SISTER"), ("opendata", "OpenData"))
        ],
        "admin": is_purchase_admin(user),
    }


@router.get("")
async def list_purchases(store: Store, user: User, response: Response):
    private_response(response)
    admin = is_purchase_admin(user)
    rows = await store.list(None if admin else str(user.id))
    return {
        "items": [
            {**purchase_response(row, admin=admin), "can_checkout": row["user_id"] == str(user.id)} for row in rows
        ],
        "admin": admin,
    }


@router.get("/map")
async def purchase_map(store: Store, user: User, response: Response):
    private_response(response)
    rows = await store.list(None if is_purchase_admin(user) else str(user.id), available=True)
    features = []
    for row in rows:
        target = json.loads(row["target_json"])
        features.append(
            {
                "type": "Feature",
                "id": row["id"],
                "geometry": target["geometry"],
                "properties": {
                    "purchase_id": row["id"],
                    "provider": row["provider"],
                    "national_reference": target["national_reference"],
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


@router.post("/quote", dependencies=[Depends(same_origin)])
async def quote_purchase(
    target: PurchaseTarget,
    store: Store,
    settings: Settings,
    user: User,
    response: Response,
    idempotency_key: Annotated[UUID, Header()],
):
    private_response(response)
    configured(settings, target.provider)
    purchase_id = str(uuid5(NAMESPACE_URL, f"cadastral-purchase:{user.id}:{idempotency_key}"))
    await store.create(purchase_id, str(user.id), target)
    row = await owned(store, purchase_id, user, customer_only=True)
    if json.loads(row["target_json"]) != target.model_dump(mode="json"):
        raise HTTPException(409, "Idempotency key was already used for a different parcel")
    if row["status"] != "quoting":
        return purchase_response(row)
    try:
        quote = GatewayQuote.model_validate(
            await gateway_post(
                settings,
                target.provider,
                "quotes",
                {"purchase_id": purchase_id, "target": target.model_dump()},
                purchase_id,
            )
        )
    except (httpx.HTTPError, ValueError):
        logger.warning("Cadastral quote gateway unavailable", exc_info=False)
        raise HTTPException(502, "Unable to get a quote. Retry this request.") from None
    await store.update(purchase_id, ("quoting",), status="quoted", quote_json=quote.model_dump_json())
    return purchase_response(await owned(store, purchase_id, user, customer_only=True))


@router.post("/{purchase_id}/checkout", dependencies=[Depends(same_origin)])
async def checkout_purchase(purchase_id: str, store: Store, settings: Settings, user: User, response: Response):
    private_response(response)
    row = await owned(store, purchase_id, user, customer_only=True)
    configured(settings, row["provider"])
    if row["status"] not in ("quoted", "submitting"):
        return purchase_response(row)
    quote = json.loads(row["quote_json"])
    if datetime.fromisoformat(quote["expires_at"]) <= datetime.now(UTC):
        raise HTTPException(409, "Quote expired. Request a new quote.")
    await store.update(purchase_id, ("quoted",), status="submitting")
    try:
        base_url = https_url(settings.public_base_url).rstrip("/")
        checkout = await asyncio.to_thread(
            get_stripe_client().create_checkout_session,
            mode="payment",
            customer_email=getattr(user, "email", None),
            client_reference_id=str(user.id),
            success_url=f"{base_url}/map?purchase={purchase_id}",
            cancel_url=f"{base_url}/map?purchase={purchase_id}&checkout=cancelled",
            metadata={"purchase_id": purchase_id, "user_id": str(user.id), "item_type": "cadastral-query"},
            idempotency_key=f"cadastral-checkout:{purchase_id}",
            price_data={
                "currency": "eur",
                "unit_amount": quote["amount_cents"],
                "product_data": {"name": f"Cadastral query · {row['provider'].upper()}"},
            },
        )
        checkout_url = https_url(checkout.url)
    except (stripe.error.StripeError, ValueError):
        logger.warning("Cadastral checkout gateway unavailable", exc_info=False)
        raise HTTPException(
            502, "Unable to open checkout. Retry this purchase; no new order will be created."
        ) from None
    await store.update(
        purchase_id,
        ("submitting",),
        status="awaiting_payment",
        billing_session_id=checkout.id,
        checkout_url=checkout_url,
    )
    return purchase_response(await owned(store, purchase_id, user, customer_only=True))


@router.post("/webhooks/billing")
async def billing_webhook(request: Request, store: Store, settings: Settings):
    """aecs4u-billing verifies payment; durable state makes fulfillment retries safe."""
    config = get_billing_config()
    if not config.stripe_webhook_secret:
        raise HTTPException(503, "Billing webhook not configured")
    body = await limited_body(request)
    try:
        payload = verify_stripe_webhook(body, request.headers.get("Stripe-Signature"), config.stripe_webhook_secret)
    except (ValueError, TypeError):
        raise HTTPException(401, "Invalid billing webhook signature") from None
    event_type = payload.get("type")
    if event_type not in (
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
        "checkout.session.expired",
        "checkout.session.async_payment_failed",
    ):
        return {"accepted": True, "changed": False}
    session = payload.get("data", {}).get("object", {})
    metadata = session.get("metadata") or {}
    if metadata.get("site_id") != config.site_id or metadata.get("item_type") != "cadastral-query":
        return {"accepted": True, "changed": False}
    row = await store.get(metadata.get("purchase_id", ""))
    if not row:
        raise HTTPException(404, "Purchase not found")
    quote = json.loads(row["quote_json"]) if row["quote_json"] else {}
    if (
        metadata.get("user_id") != row["user_id"]
        or session.get("client_reference_id") != row["user_id"]
        or not session.get("id")
        or row["billing_session_id"] not in (None, session["id"])
        or session.get("mode") != "payment"
        or session.get("currency") != "eur"
        or session.get("amount_total") != quote.get("amount_cents")
    ):
        raise HTTPException(409, "Billing session does not match purchase")
    if event_type in ("checkout.session.expired", "checkout.session.async_payment_failed"):
        await store.update(row["id"], ("submitting", "awaiting_payment"), status="cancelled")
        return {"accepted": True}
    settled = session.get("payment_status") == "paid" or (
        quote.get("amount_cents") == 0 and session.get("payment_status") == "no_payment_required"
    )
    if not settled:
        return {"accepted": True, "changed": False}
    await store.update(
        row["id"],
        ("submitting", "awaiting_payment"),
        status="paid",
        paid=1,
        billing_session_id=session["id"],
    )
    row = await store.get(row["id"])
    if row["status"] != "paid":
        return {"accepted": True, "changed": False}
    try:
        configured(settings, row["provider"])
        base_url = https_url(settings.public_base_url).rstrip("/")
        order = GatewayOrder.model_validate(
            await gateway_post(
                settings,
                row["provider"],
                "orders",
                {
                    "purchase_id": row["id"],
                    "quote_id": quote["quote_id"],
                    "target": json.loads(row["target_json"]),
                    "webhook_url": f"{base_url}/api/v1/cadastral-purchases/webhooks/{row['provider']}",
                },
                f"cadastral-order:{row['id']}",
            )
        )
    except (httpx.HTTPError, ValueError):
        # Payment remains durable. Stripe retries its callback until the gateway
        # accepts the same idempotent order; never charge the customer again.
        raise HTTPException(502, "Payment recorded; provider order must be retried") from None
    await store.update(row["id"], ("paid",), status="processing", order_id=order.order_id)
    return {"accepted": True, "changed": True}


async def limited_body(request: Request):
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 5_000_000:
            raise HTTPException(413, "Webhook payload too large")
    return bytes(body)


@router.get("/{purchase_id}")
async def get_purchase(purchase_id: str, store: Store, user: User, response: Response):
    private_response(response)
    return purchase_response(await owned(store, purchase_id, user), result=True, admin=is_purchase_admin(user))


@router.get("/{purchase_id}/document")
async def purchase_document(purchase_id: str, store: Store, user: User):
    row = await owned(store, purchase_id, user)
    if row["status"] != "available" or not row["paid"] or not row["document_pdf_base64"]:
        raise HTTPException(404, "Purchase document not available")
    return Response(
        base64.b64decode(row["document_pdf_base64"]),
        media_type="application/pdf",
        headers={
            "Cache-Control": "private, no-store",
            "Vary": "Cookie, Authorization",
            "Content-Disposition": f'attachment; filename="cadastral-{row["id"]}.pdf"',
        },
    )


@router.post("/webhooks/{provider}")
async def purchase_webhook(provider: Provider, request: Request, store: Store, settings: Settings):
    secret = getattr(settings, f"{provider}_webhook_secret")
    if not secret:
        raise HTTPException(503, "Webhook not configured")
    timestamp = request.headers.get("X-Purchase-Timestamp", "")
    try:
        if abs(time.time() - int(timestamp)) > 300:
            raise ValueError
    except ValueError:
        raise HTTPException(401, "Invalid webhook timestamp") from None
    body = await limited_body(request)
    signature = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, request.headers.get("X-Purchase-Signature", "")):
        raise HTTPException(401, "Invalid webhook signature")
    try:
        event = PurchaseEvent.model_validate_json(body)
        if event.document_pdf_base64 is not None:
            document = base64.b64decode(event.document_pdf_base64, validate=True)
            if not document.startswith(b"%PDF-"):
                raise ValueError("Not a PDF")
    except (ValidationError, ValueError):
        # Do not echo confidential provider records in validation errors.
        raise HTTPException(422, "Invalid purchase event") from None
    row = await store.get(event.purchase_id)
    if row is None or row["provider"] != provider:
        raise HTTPException(404, "Purchase not found")
    if not row["order_id"]:
        # A gateway may race the checkout response. Retry after the order binding persists.
        raise HTTPException(409, "Checkout is not yet registered; retry this event")
    if row["order_id"] != event.order_id:
        raise HTTPException(409, "Provider order does not match purchase")
    if not row["paid"]:
        raise HTTPException(409, "Payment has not been confirmed by billing")
    if row["status"] in ("available", "failed", "cancelled"):
        return {"accepted": True, "changed": False}
    updated = await store.update(
        event.purchase_id,
        ("processing",),
        status=event.status,
        record_json=json.dumps(event.record) if event.record is not None else None,
        document_pdf_base64=event.document_pdf_base64,
    )
    return {"accepted": True, "changed": updated is not None}
