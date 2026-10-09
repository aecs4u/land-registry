"""Private cadastral orders. Provider adapters implement the documented gateway contract.

Purchased records deliberately never enter the public enrichment/tile caches.
SQLite is for local development; deployments use the existing PostgreSQL store.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings
from shapely.geometry import shape

from land_registry.config import db_settings
from land_registry.database import get_async_db
from land_registry.sqlite_db import get_sqlite_path

Provider = Literal["sister", "opendata"]


class PurchaseSettings(BaseSettings):
    model_config = ConfigDict(env_prefix="CADASTRAL_PURCHASE_", extra="ignore")

    public_base_url: str = ""
    sister_gateway_url: str = ""
    sister_api_token: str = ""
    sister_webhook_secret: str = ""
    opendata_gateway_url: str = ""
    opendata_api_token: str = ""
    opendata_webhook_secret: str = ""

    def enabled(self, provider: Provider) -> bool:
        from aecs4u_billing.config import get_billing_config

        billing = get_billing_config()
        return bool(
            self.public_base_url
            and getattr(self, f"{provider}_gateway_url")
            and getattr(self, f"{provider}_api_token")
            and getattr(self, f"{provider}_webhook_secret")
            and billing.stripe_api_key
            and billing.stripe_webhook_secret
        )


def https_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("An HTTPS URL without embedded credentials is required")
    return value


class PurchaseTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    provider: Provider
    municipality_code: str = Field(pattern=r"^[A-Za-z][0-9]{3}$")
    sheet: str = Field(min_length=1, max_length=16, pattern=r"^[A-Za-z0-9/-]+$")
    parcel: str = Field(min_length=1, max_length=16, pattern=r"^[A-Za-z0-9/-]+$")
    subaltern: str | None = Field(default=None, max_length=16, pattern=r"^[A-Za-z0-9/-]+$")
    registry: Literal["land", "buildings"] = "land"
    national_reference: str = Field(min_length=1, max_length=256)
    geometry: dict[str, Any]

    @field_validator("municipality_code")
    @classmethod
    def uppercase_code(cls, value):
        return value.upper()

    @field_validator("geometry")
    @classmethod
    def valid_geometry(cls, value):
        if value.get("type") not in ("Point", "Polygon", "MultiPolygon"):
            raise ValueError("A point or parcel polygon is required")
        if len(json.dumps(value)) > 200_000:
            raise ValueError("Geometry is too large")
        try:
            geometry = shape(value)
            if geometry.is_empty or not geometry.is_valid or geometry.has_z:
                raise ValueError("Invalid geometry")
            west, south, east, north = geometry.bounds
            if not (-180 <= west <= east <= 180 and -90 <= south <= north <= 90):
                raise ValueError("Geometry must use WGS84 coordinates")
        except (TypeError, KeyError) as exc:
            raise ValueError("Invalid geometry") from exc
        return value


class GatewayQuote(BaseModel):
    quote_id: str = Field(min_length=1, max_length=256)
    amount_cents: int = Field(strict=True, ge=0)
    currency: Literal["EUR"] = "EUR"
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def future_expiry(cls, value):
        if value.tzinfo is None or value <= datetime.now(UTC):
            raise ValueError("Quote must have a future timezone-aware expiry")
        return value


class GatewayOrder(BaseModel):
    order_id: str = Field(min_length=1, max_length=256)


class PurchaseEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purchase_id: str = Field(min_length=1, max_length=36)
    order_id: str = Field(min_length=1, max_length=256)
    status: Literal["processing", "available", "failed", "cancelled"]
    record: dict[str, Any] | None = None
    document_pdf_base64: str | None = None

    @model_validator(mode="after")
    def requires_paid_result(self):
        if self.status == "available" and not (self.record or self.document_pdf_base64):
            raise ValueError("An available purchase must include a record or document")
        if self.status != "available" and (self.record is not None or self.document_pdf_base64 is not None):
            raise ValueError("Results are only accepted for available purchases")
        return self


async def gateway_post(settings: PurchaseSettings, provider: Provider, path: str, payload: dict, key: str):
    """Only configured server-side adapters can place provider orders or set prices."""
    base_url = https_url(getattr(settings, f"{provider}_gateway_url")).rstrip("/")
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        response = await client.post(
            f"{base_url}/{path}",
            json=payload,
            headers={
                "Authorization": f"Bearer {getattr(settings, f'{provider}_api_token')}",
                "Idempotency-Key": key,
            },
        )
        response.raise_for_status()
        return response.json()


SCHEMA = """
CREATE TABLE IF NOT EXISTS cadastral_purchases (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'quoting',
    target_json TEXT NOT NULL,
    quote_json TEXT,
    order_id TEXT,
    billing_session_id TEXT UNIQUE,
    checkout_url TEXT,
    paid INTEGER NOT NULL DEFAULT 0,
    record_json TEXT,
    document_pdf_base64 TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(provider, order_id)
)
"""


class PurchaseStore:
    """Small persistent store with conditional updates for concurrent webhook/checkout requests."""

    def __init__(self, sqlite_path: str | None = None):
        self.sqlite_path = sqlite_path
        self._postgres_ready = False
        self._schema_lock = asyncio.Lock()

    async def query(self, sql: str, *args) -> list[dict]:
        if self.sqlite_path is None and db_settings.use_neon and db_settings.database_url:
            # Existing asyncpg pool owns connection lifecycle and credentials.
            parts = sql.split("?")
            sql = parts[0] + "".join(f"${i}{part}" for i, part in enumerate(parts[1:], 1))
            async with get_async_db().get_connection() as conn:
                if not self._postgres_ready:
                    async with self._schema_lock:
                        if not self._postgres_ready:
                            async with conn.transaction():
                                await conn.execute(SCHEMA)
                                await conn.execute(
                                    "CREATE INDEX IF NOT EXISTS idx_cadastral_purchases_owner "
                                    "ON cadastral_purchases(user_id, updated_at)"
                                )
                            self._postgres_ready = True
                return [dict(row) for row in await conn.fetch(sql, *args)]
        return await asyncio.to_thread(self._sqlite_query, sql, args)

    def _sqlite_query(self, sql: str, args: tuple):
        path = self.sqlite_path or get_sqlite_path()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path, timeout=15) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute(SCHEMA)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cadastral_purchases_owner ON cadastral_purchases(user_id, updated_at)"
            )
            return [dict(row) for row in conn.execute(sql, args).fetchall()]

    async def get(self, purchase_id: str, user_id: str | None = None):
        sql = "SELECT * FROM cadastral_purchases WHERE id = ?"
        args = [purchase_id]
        if user_id is not None:
            sql += " AND user_id = ?"
            args.append(user_id)
        rows = await self.query(sql, *args)
        return rows[0] if rows else None

    async def list(self, user_id: str | None, *, available: bool = False):
        clauses, args = [], []
        if user_id is not None:
            clauses.append("user_id = ?")
            args.append(user_id)
        if available:
            clauses.append("status = 'available' AND paid = 1")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        return await self.query(
            "SELECT * FROM cadastral_purchases" + where + " ORDER BY updated_at DESC LIMIT 500", *args
        )

    async def create(self, purchase_id: str, user_id: str, target: PurchaseTarget):
        now = datetime.now(UTC).isoformat()
        await self.query(
            "INSERT INTO cadastral_purchases (id, user_id, provider, target_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (id) DO NOTHING RETURNING id",
            purchase_id,
            user_id,
            target.provider,
            target.model_dump_json(),
            now,
            now,
        )

    async def update(self, purchase_id: str, statuses: tuple[str, ...], **fields):
        allowed = {
            "status",
            "quote_json",
            "order_id",
            "billing_session_id",
            "checkout_url",
            "paid",
            "record_json",
            "document_pdf_base64",
        }
        if not fields.keys() <= allowed:
            raise ValueError("Unknown purchase field")
        fields["updated_at"] = datetime.now(UTC).isoformat()
        sql = "UPDATE cadastral_purchases SET " + ", ".join(f"{key} = ?" for key in fields)
        sql += " WHERE id = ? AND status IN (" + ",".join("?" for _ in statuses) + ") RETURNING *"
        rows = await self.query(sql, *fields.values(), purchase_id, *statuses)
        return rows[0] if rows else None


def is_purchase_admin(user) -> bool:
    if getattr(user, "is_superuser", False):
        return True
    if callable(getattr(user, "has_role", None)) and user.has_role("admin"):
        return True
    roles = getattr(user, "roles", None) or getattr(user, "role", None) or []
    if isinstance(roles, str):
        roles = roles.split(",")
    elif not isinstance(roles, (tuple, list, set)):
        roles = [roles]
    return any(str(getattr(role, "value", role)).strip().lower() == "admin" for role in roles)


def purchase_response(row: dict, *, result: bool = False, admin: bool = False):
    target = json.loads(row["target_json"])
    quote = json.loads(row["quote_json"]) if row["quote_json"] else None
    response = {
        "id": row["id"],
        "provider": row["provider"],
        "status": row["status"],
        "paid": bool(row["paid"]),
        "target": target,
        "quote": quote,
        "checkout_url": row["checkout_url"] if row["status"] == "awaiting_payment" else None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if admin:
        response["customer_id"] = row["user_id"]
    if result and row["status"] == "available" and row["paid"]:
        response["record"] = json.loads(row["record_json"]) if row["record_json"] else None
        response["document_url"] = (
            f"/api/v1/cadastral-purchases/{row['id']}/document" if row["document_pdf_base64"] else None
        )
    return response
