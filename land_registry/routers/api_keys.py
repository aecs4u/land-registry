"""Clerk-owned API-key management for the versioned parcel API."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select, text
from sqlmodel.ext.asyncio.session import AsyncSession

from land_registry.config import public_api_settings
from land_registry.routers.auth import get_current_user

try:
    from aecs4u_auth.api_keys import ApiKeyStatus, ApiKeyTable, generate_api_key, hash_api_key
    from aecs4u_auth.users.models import User

    _API_KEY_BACKEND_AVAILABLE = True
except ImportError:
    ApiKeyStatus = None
    ApiKeyTable = Any
    User = Any
    generate_api_key = None
    hash_api_key = None
    _API_KEY_BACKEND_AVAILABLE = False

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/user/api-keys")
api_key_header = APIKeyHeader(name="X-API-Key", scheme_name="ParcelAPIKey", auto_error=False)

MAX_ACTIVE_API_KEYS = public_api_settings.max_active_keys
DEFAULT_API_KEY_REQUESTS_PER_HOUR = public_api_settings.requests_per_hour
PARCEL_READ_SCOPE = "parcels:read"


class ApiKeyCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80, description="A label to identify this key")

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("name must contain a non-whitespace character")
        return cleaned


class ApiKeySummary(BaseModel):
    id: str
    name: str
    key_prefix: str
    scopes: list[str]
    requests_per_hour: int
    status: str
    expires_at: datetime | None
    last_used_at: datetime | None
    total_requests: int
    created_at: datetime


class ApiKeyListResponse(BaseModel):
    keys: list[ApiKeySummary]
    max_active_keys: int


class ApiKeyCreatedResponse(ApiKeySummary):
    key: str = Field(description="Shown once. Store it securely; it cannot be retrieved again.")


class ApiKeyRevokedResponse(BaseModel):
    id: str
    status: str
    revoked_at: datetime


def _database_engine():
    """Return the configured shared PostgreSQL auth engine or fail closed."""
    if not _API_KEY_BACKEND_AVAILABLE:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API key authentication is not installed",
        )
    try:
        from land_registry.local_auth import get_engine

        engine = get_engine()
    except Exception as exc:
        # Configuration and driver errors are deployment unavailability too;
        # keep them behind the documented 503 response instead of leaking a
        # framework-level 500 or a credential-bearing connection string in logs.
        logger.warning(
            "Unable to initialize public API key PostgreSQL storage (%s)",
            type(exc).__name__,
        )
        engine = None
    if engine is None or engine.dialect.name != "postgresql":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API key storage requires the configured PostgreSQL database",
        )
    return engine


def _user_id(user: Any) -> str:
    value = getattr(user, "id", None)
    if not value:
        raise HTTPException(status_code=401, detail="Authenticated account is unavailable")
    clerk_id = str(getattr(user, "clerk_id", "") or "")
    if clerk_id.startswith("local_"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Developer API keys require a Clerk account",
        )
    return str(value)


def _status_value(value: Any) -> str:
    return str(value.value if hasattr(value, "value") else value)


def _summary(key: ApiKeyTable) -> ApiKeySummary:
    return ApiKeySummary(
        id=key.id,
        name=key.name,
        key_prefix=key.key_prefix,
        scopes=list(key.scopes or []),
        requests_per_hour=DEFAULT_API_KEY_REQUESTS_PER_HOUR,
        status=_status_value(key.status),
        expires_at=key.expires_at,
        last_used_at=key.last_used_at,
        total_requests=int(key.total_requests or 0),
        created_at=key.created_at,
    )


@router.get(
    "",
    response_model=ApiKeyListResponse,
    responses={401: {"description": "Authentication required"}, 503: {"description": "PostgreSQL is unavailable"}},
)
async def list_api_keys(user=Depends(get_current_user)) -> ApiKeyListResponse:
    """List the caller's API keys without exposing any key secret."""
    engine = _database_engine()
    async with AsyncSession(engine) as session:
        result = await session.exec(
            select(ApiKeyTable)
            .where(ApiKeyTable.user_id == _user_id(user))
            .order_by(ApiKeyTable.created_at.desc())
        )
        keys = result.all()
    return ApiKeyListResponse(
        keys=[_summary(key) for key in keys],
        max_active_keys=MAX_ACTIVE_API_KEYS,
    )


@router.post(
    "",
    response_model=ApiKeyCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        401: {"description": "Authentication required"},
        409: {"description": "Maximum active API keys reached"},
        422: {"description": "Invalid API-key name"},
        503: {"description": "PostgreSQL is unavailable"},
    },
)
async def create_api_key(
    payload: ApiKeyCreateRequest,
    user=Depends(get_current_user),
) -> ApiKeyCreatedResponse:
    """Create a scoped key and return its secret once."""
    engine = _database_engine()
    user_id = _user_id(user)
    now = datetime.now(UTC)
    raw_key, key_prefix, key_hash = generate_api_key(prefix="landregistry")

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            # Lock the owner row so simultaneous create requests cannot exceed
            # the active-key limit for one account.
            owner = await session.exec(
                select(User.id).where(User.id == user_id).with_for_update()
            )
            if owner.first() is None:
                raise HTTPException(status_code=401, detail="Authenticated account is unavailable")

            active_count_result = await session.exec(
                select(func.count())
                .select_from(ApiKeyTable)
                .where(
                    ApiKeyTable.user_id == user_id,
                    ApiKeyTable.status == ApiKeyStatus.ACTIVE,
                )
            )
            if int(active_count_result.one()) >= MAX_ACTIVE_API_KEYS:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"A maximum of {MAX_ACTIVE_API_KEYS} active API keys is allowed",
                )

            key = ApiKeyTable(
                id=str(uuid4()),
                user_id=user_id,
                name=payload.name,
                key_hash=key_hash,
                key_prefix=key_prefix,
                scopes=[PARCEL_READ_SCOPE],
                requests_per_hour=DEFAULT_API_KEY_REQUESTS_PER_HOUR,
                environment="production",
                status=ApiKeyStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            )
            session.add(key)
            await session.flush()

    return ApiKeyCreatedResponse(**_summary(key).model_dump(), key=raw_key)


@router.delete(
    "/{key_id}",
    response_model=ApiKeyRevokedResponse,
    responses={401: {"description": "Authentication required"}, 404: {"description": "Key not found"}, 503: {"description": "PostgreSQL is unavailable"}},
)
async def revoke_api_key(key_id: str, user=Depends(get_current_user)) -> ApiKeyRevokedResponse:
    """Revoke one of the caller's keys immediately."""
    engine = _database_engine()
    now = datetime.now(UTC)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            result = await session.exec(
                select(ApiKeyTable)
                .where(ApiKeyTable.id == key_id, ApiKeyTable.user_id == _user_id(user))
                .with_for_update()
            )
            key = result.first()
            if key is None:
                raise HTTPException(status_code=404, detail="API key not found")
            key.status = ApiKeyStatus.REVOKED
            key.revoked_at = now
            key.revoked_reason = "user_revoked"
            key.updated_at = now
            session.add(key)
    return ApiKeyRevokedResponse(id=key_id, status="revoked", revoked_at=now)


async def require_parcel_api_key(
    request: Request,
    response: Response,
    raw_key: str | None = Depends(api_key_header),
) -> ApiKeySummary:
    """Authenticate a key, enforce its read scope and consume shared quota."""
    if not raw_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key required in the X-API-Key header",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    engine = _database_engine()
    now = datetime.now(UTC)
    hour_window = int(now.timestamp() // 3600)
    window_reset = (hour_window + 1) * 3600
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                result = await session.exec(
                    select(ApiKeyTable)
                    .where(ApiKeyTable.key_hash == hash_api_key(raw_key))
                    .with_for_update()
                )
                key = result.first()
                if key is None or not key.is_valid():
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid or inactive API key",
                        headers={"WWW-Authenticate": "ApiKey"},
                    )
                if not key.has_scope(PARCEL_READ_SCOPE):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="API key does not grant parcels:read",
                    )

                # This deployment currently has one quota tier. Read the
                # centralized setting on every request so changing the
                # deployment limit applies to existing keys as well.
                limit = DEFAULT_API_KEY_REQUESTS_PER_HOUR
                usage = await session.execute(
                    text(
                        "INSERT INTO api_key_hourly_usage "
                        "(api_key_id, window_start, request_count) "
                        "VALUES (:api_key_id, :window_start, 1) "
                        "ON CONFLICT (api_key_id) DO UPDATE SET "
                        "window_start = EXCLUDED.window_start, "
                        "request_count = CASE "
                        "WHEN api_key_hourly_usage.window_start = EXCLUDED.window_start "
                        "THEN api_key_hourly_usage.request_count + 1 ELSE 1 END "
                        "WHERE api_key_hourly_usage.window_start <> EXCLUDED.window_start "
                        "OR api_key_hourly_usage.request_count < :limit "
                        "RETURNING request_count"
                    ),
                    {
                        "api_key_id": key.id,
                        "window_start": hour_window,
                        "limit": limit,
                    },
                )
                usage_row = usage.first()
                if usage_row is None:
                    raise HTTPException(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        detail="API key hourly quota exceeded",
                        headers={
                            "Retry-After": str(max(1, window_reset - int(now.timestamp()))),
                            "X-RateLimit-Limit": str(limit),
                            "X-RateLimit-Remaining": "0",
                            "X-RateLimit-Reset": str(window_reset),
                        },
                    )

                used = int(usage_row[0])
                key.last_used_at = now
                key.total_requests = int(key.total_requests or 0) + 1
                key.successful_requests = int(key.successful_requests or 0) + 1
                key.updated_at = now
                session.add(key)

        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, limit - used))
        response.headers["X-RateLimit-Reset"] = str(window_reset)
        return _summary(key)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Public API key verification or quota check failed", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API key service is temporarily unavailable",
        ) from exc

