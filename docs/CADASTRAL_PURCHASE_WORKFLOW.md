# Private cadastral query purchases

Select a parcel on `/map` (`map_v2.html` or its theme override), choose **Purchase
cadastral query**, review the cadastral identifiers and provider, request a
quote, then explicitly continue to secure checkout. **My purchases** (the € map
control) opens previous orders. The selected map geometry is retained with the
order; a delivered query appears as a purple overlay and can be opened or
downloaded from the purchase dialog.

Payments use `aecs4u-billing>=1.2.0`: `setup_billing`,
`StripeClient.create_checkout_session(mode="payment")`, and
`verify_stripe_webhook`. Prices, customer identity, purchase metadata, and
idempotency keys are set by the backend. No generic client-controlled commerce
checkout is exposed for cadastral products.

```mermaid
sequenceDiagram
    participant Customer
    participant Map as Land Registry map
    participant Billing as aecs4u-billing / Stripe
    participant Provider as SISTER / OpenData adapter
    Customer->>Map: Select parcel and request quote
    Map->>Provider: POST quotes (no order / no charge)
    Provider-->>Map: EUR price and quote expiry
    Customer->>Map: Review quote and continue to checkout
    Map->>Billing: Create one-time Checkout session
    Billing-->>Customer: Hosted payment page
    Billing->>Map: Signed payment webhook
    Map->>Map: Verify session, owner, amount; persist payment
    Map->>Provider: Idempotent POST orders
    Provider-->>Map: Provider order ID
    Provider->>Map: Signed delivery webhook with record/PDF
    Map->>Map: Persist available result in private store
    Map-->>Customer: Refresh private overlay and notify availability
```

## Access and storage

The app stores order projections in `cadastral_purchases` using its existing
PostgreSQL database when `DB_USE_NEON` and `DB_DATABASE_URL` are enabled, or the
existing SQLite application database locally. The existing `DATABASE_URL`
resolution in `config.py` also enables PostgreSQL. Purchased JSON/PDF results
stay in this table and never enter public parcel enrichment, MVT tiles, or
shared cadastral caches.

Every list, detail, GeoJSON, and PDF request requires authentication. Customer
queries include the authenticated user's ID; unowned IDs return 404. The
`admin` role (including the auth package's superuser contract) can inspect all
customers' purchases. Even admins can initiate checkout only for their own
orders. Private responses use `Cache-Control: private, no-store`. The map
collection contains geometry, parcel reference and purchase ID, with full
records fetched through the authenticated detail endpoint.

## Configuration and external integration

Set `STRIPE_API_KEY`, `STRIPE_WEBHOOK_SECRET` and an HTTPS
`CADASTRAL_PURCHASE_PUBLIC_BASE_URL`. Register the Stripe webhook at:

```
https://<app-host>/api/v1/cadastral-purchases/webhooks/billing
```

Subscribe to `checkout.session.completed`,
`checkout.session.async_payment_succeeded`, `checkout.session.async_payment_failed`,
and `checkout.session.expired`. A success-page redirect is not payment proof.
Unpaid completed sessions await the later async payment event. Payment events
must match the persisted Checkout session, customer, site, currency and amount.
Zero-total quotes can be fulfilled only when a verified billing event confirms
`no_payment_required` for that same zero-total session.

For each provider, configure the gateway URL, API token and separate webhook
secret from `.env.example`. A provider is selectable only when billing and all
its settings are present.

**Integration boundary:** this repository supplies the map, billing handoff,
private order store and webhook consumers. A deployed provider adapter must
implement the following contract over the actual SISTER/OpenData acquisition
service. Existing parcel enrichment endpoints are read-only and are not order
submission endpoints. The application does not scrape the SISTER login page
or presume that the public OpenData cache is a paid-query API.

### Quote adapter

The app calls `POST <gateway>/quotes` with a bearer token and
`Idempotency-Key: <purchase-id>`:

```json
{
  "purchase_id": "<app-purchase-uuid>",
  "target": {
    "provider": "sister",
    "municipality_code": "H501",
    "sheet": "12",
    "parcel": "345",
    "subaltern": null,
    "registry": "land",
    "national_reference": "H501_0012.345",
    "geometry": {"type": "Point", "coordinates": [12.5, 41.9]}
  }
}
```

Response:

```json
{
  "quote_id": "<provider-quote-id>",
  "amount_cents": 1290,
  "currency": "EUR",
  "expires_at": "<future ISO 8601 timestamp with timezone>"
}
```

The amount is illustrative, not a configured price. Quotes do not charge or
order a query. The adapter must supply the customer-facing total and honor
the quote for payment sessions started before its expiry, including delayed
payment confirmations and delivery retries. It must validate supported
registries and cadastral identifiers before returning a price.

### Fulfillment adapter

After verified payment, the app calls `POST <gateway>/orders` with the bearer
token and stable `Idempotency-Key: cadastral-order:<purchase-id>`:

```json
{
  "purchase_id": "<app-purchase-uuid>",
  "quote_id": "<provider-quote-id>",
  "target": "<same target object as the quote>",
  "webhook_url": "https://<app-host>/api/v1/cadastral-purchases/webhooks/sister"
}
```

The real `target` value is an object; the string above abbreviates it. Return
`{"order_id":"<provider-order-id>"}`. Retrying an idempotency key must return
the same order and must not reacquire or charge for the query. The adapter does
not process customer payments; `aecs4u-billing` owns them.

Delivery callbacks use the corresponding `/webhooks/sister` or
`/webhooks/opendata` route, carrying:

```json
{
  "purchase_id": "<app-purchase-uuid>",
  "order_id": "<provider-order-id>",
  "status": "available",
  "record": {"cadastral_fields": "<private result>"},
  "document_pdf_base64": "<optional base64 PDF>"
}
```

At least a nonempty JSON record or PDF is required for `available`. Other
allowed statuses are `processing`, `failed`, and `cancelled`, with no result
fields. Payment status is never accepted from the provider.

Sign the exact raw JSON bytes using the provider's configured webhook secret:

```
X-Purchase-Timestamp: <Unix seconds>
X-Purchase-Signature: hex(HMAC-SHA256(secret, timestamp + "." + raw_body))
```

Callbacks older than five minutes, mismatched providers/orders, unconfirmed
payments, malformed PDFs, and payloads larger than 5 MB are rejected. Sign each
delivery attempt with a fresh timestamp. A callback arriving before the order
ID is persisted receives 409 and must be retried. Terminal states cannot be
overwritten by duplicate or late callbacks.

## Recovery and operational limits

Order states are `quoting → quoted → submitting → awaiting_payment → paid →
processing → available`, with `failed`/`cancelled` terminal outcomes. Quote and
checkout retries reuse stable keys. If provider submission fails after payment,
the durable `paid` state is retained and the Stripe webhook returns 502 so its
delivery retries resubmit the same provider order. If Stripe exhausts its
retry period, resend the original verified payment event after provider recovery.
Provider failures after payment are shown as requiring support; automatic
refunds are not part of this workflow.

The map refreshes every 15 seconds while visible and once when the tab becomes
visible again; availability is announced in the map status and purchase dialog.
Results are not saved in browser local storage. Lists/overlays currently show
the most recent 500 matching purchases. PDFs are delivered through authenticated
app endpoints, not public storage links. The selected parcel geometry is the
map placement, not geometry inferred from the purchased document.

Configure and verify both provider adapters and Stripe test-mode callbacks
before enabling live purchases. Unit tests mock the external services; no live
payments are made during verification.
