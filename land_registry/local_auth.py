"""Local email/password authentication, wired alongside Clerk SSO.

aecs4u-auth ships a single shared ``users`` table used both to mirror Clerk
accounts and to hold local password-only accounts (distinguished by a
``local_`` ``clerk_id`` prefix — see aecs4u_auth.cli.users.create_user). This
module points that machinery at this app's own Postgres database and wires
the lookup/verify callbacks aecs4u-auth's ``get_current_user`` needs to accept
either identity when ``AUTH_MODE=clerk_and_local``.

Local accounts have no self-registration page — they're created out-of-band
with scripts/local_users.py. This is meant for a handful of admin/service
accounts, not public sign-up; the public flow is Clerk.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import DateTime
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from aecs4u_auth import SQLModelUserStorage, verify_password
from aecs4u_auth.users.models import User

logger = logging.getLogger(__name__)

_TIMESTAMP_COLUMNS = (
    "created_at",
    "updated_at",
    "synced_at",
    "clerk_created_at",
    "clerk_updated_at",
    "last_sign_in_at",
    "last_active_at",
)

# aecs4u-auth's UserBase declares these datetime fields with a plain
# `Field(default_factory=lambda: datetime.now(UTC))` and no
# `sa_column=Column(DateTime(timezone=True))`, so SQLModel maps them to a
# naive `DateTime()` — yet the package always *writes* tz-aware values
# (its own CLI included). SQLAlchemy compiles bind parameters from this
# mapped Python type (`::TIMESTAMP WITHOUT TIME ZONE`), not from the live
# database schema, so widening the actual column (see the migration in
# ensure_user_table()) isn't enough on its own — asyncpg's codec still
# rejects a tz-aware value cast to a naive-typed parameter. Patch the
# mapped type here, at import time, before any engine/table touches it.
for _col_name in _TIMESTAMP_COLUMNS:
    User.__table__.columns[_col_name].type = DateTime(timezone=True)
del _col_name

_engine: AsyncEngine | None = None
_storage: SQLModelUserStorage | None = None


def to_asyncpg_url(raw: str | None) -> str | None:
    """Convert a libpq-style Postgres URL into one SQLAlchemy's asyncpg dialect accepts.

    SQLAlchemy's asyncpg dialect forwards every query-string parameter as a
    keyword argument straight to ``asyncpg.connect()``, which has no
    ``sslmode`` parameter (only ``ssl``) — unlike psycopg2 and raw asyncpg's
    own DSN parser, both already in use elsewhere in this app (see
    land_registry/database.py). Neon's connection strings always carry
    ``sslmode=require``, so translate it rather than dropping TLS silently.
    """
    if not raw:
        return raw
    scheme, netloc, path, query, fragment = urlsplit(raw)
    if scheme in ("postgresql", "postgres"):
        scheme = "postgresql+asyncpg"
    params = dict(parse_qsl(query))
    sslmode = params.pop("sslmode", None)
    if sslmode and sslmode != "disable":
        params.setdefault("ssl", "require")
    return urlunsplit((scheme, netloc, path, urlencode(params), fragment))


def init_engine(database_url: str) -> None:
    """Create the async engine/storage backing local accounts. Call once at startup."""
    global _engine, _storage
    if _engine is not None:
        return
    # Neon's pooled endpoint (the `-pooler` host used everywhere in this app's
    # DATABASE_URL) is PgBouncer in transaction-pooling mode: a client's
    # asyncpg-side prepared-statement cache can outlive the physical backend
    # connection PgBouncer hands it next, so a schema change (or just bad
    # luck) surfaces as "cached statement plan is invalid" on an unrelated,
    # later query. Disabling asyncpg's statement cache is the documented fix.
    _connect_args = {"statement_cache_size": 0} if "asyncpg" in database_url else {}
    _engine = create_async_engine(
        database_url, echo=False, pool_pre_ping=True, connect_args=_connect_args
    )
    _storage = SQLModelUserStorage(engine=_engine)


async def ensure_user_table() -> None:
    """Create aecs4u-auth's shared ``users`` table if it doesn't exist yet.

    Only this one table is created here — not the full aecs4u_auth demo
    schema (api keys, applications, organizations) which this app doesn't use.
    """
    if _engine is None:
        return
    from sqlalchemy import text

    async with _engine.begin() as conn:
        await conn.run_sync(User.metadata.create_all)

        dialect = conn.engine.dialect.name
        if dialect != "postgresql":
            return  # SQLite dev fallback has no timezone-aware timestamp type to migrate to.

        result = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'users' AND data_type = 'timestamp without time zone' "
                "AND column_name = ANY(:cols)"
            ),
            {"cols": list(_TIMESTAMP_COLUMNS)},
        )
        naive_columns = [row[0] for row in result]
        for column in naive_columns:
            await conn.execute(
                text(
                    f'ALTER TABLE users ALTER COLUMN "{column}" '
                    f"TYPE timestamptz USING \"{column}\" AT TIME ZONE 'UTC'"
                )
            )
        if naive_columns:
            logger.info("Widened users timestamp columns to timestamptz: %s", naive_columns)


async def dispose_engine() -> None:
    global _engine, _storage
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _storage = None


def get_storage() -> SQLModelUserStorage:
    """Return the shared storage set up by init_engine() (scripts/local_users.py included)."""
    if _storage is None:
        raise RuntimeError("local_auth.init_engine() must run before the app serves requests")
    return _storage


_require_storage = get_storage  # internal alias used by the callbacks below


# --- aecs4u_auth.configure_user_integration() callbacks ---------------------


async def lookup_by_clerk_id(clerk_id: str, _db: Any = None) -> User | None:
    return await _require_storage().get_user_by_clerk_id(clerk_id)


async def create_from_clerk(clerk_user: Any, _db: Any = None) -> User:
    user = User(
        id=str(uuid.uuid4()),
        clerk_id=clerk_user.id,
        email=clerk_user.email,
        first_name=clerk_user.first_name or None,
        last_name=clerk_user.last_name or None,
        image_url=clerk_user.image_url,
        email_verified=True,
    )
    return await _require_storage().upsert_user(user)


async def lookup_by_username(username: str, _db: Any = None) -> User | None:
    return await _require_storage().get_user_by_username(username)


async def lookup_by_id(user_id: str, _db: Any = None) -> User | None:
    return await _require_storage().get_user(user_id)


async def verify_local_password(username: str, password: str) -> User | None:
    user = await _require_storage().get_user_by_username(username)
    if user and user.hashed_password and verify_password(password, user.hashed_password):
        return user
    return None
