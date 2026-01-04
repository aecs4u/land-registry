"""
Database module for Neon PostgreSQL integration.

Provides connection management and query utilities for the land registry app.
Uses Neon serverless PostgreSQL for persistent storage of:
- User data and preferences
- Saved map configurations
- Cadastral query history
- Application metadata
"""

import os
import logging
from contextlib import asynccontextmanager, contextmanager
from typing import Optional, Any, AsyncGenerator, Generator
from urllib.parse import urlparse

from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class DatabaseSettings(BaseSettings):
    """Database configuration settings for Neon PostgreSQL."""

    # Neon PostgreSQL connection string
    # Format: postgresql://user:password@host/database?sslmode=require
    database_url: Optional[str] = None

    # Alternative: individual connection parameters
    db_host: Optional[str] = None
    db_port: int = 5432
    db_name: str = "land_registry"
    db_user: Optional[str] = None
    db_password: Optional[str] = None
    db_sslmode: str = "require"  # Neon requires SSL

    # Connection pool settings
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout: int = 30
    db_pool_recycle: int = 1800  # Recycle connections after 30 minutes

    # Query settings
    db_echo: bool = False  # Log SQL queries (useful for debugging)
    db_statement_timeout: int = 30000  # 30 seconds in milliseconds

    class Config:
        env_prefix = ""  # Use standard env var names
        case_sensitive = False
        extra = "ignore"

    def get_connection_url(self) -> Optional[str]:
        """Build the database connection URL."""
        # Prefer DATABASE_URL if set
        if self.database_url:
            return self.database_url

        # Build from individual parameters
        if self.db_host and self.db_user and self.db_password:
            return (
                f"postgresql://{self.db_user}:{self.db_password}"
                f"@{self.db_host}:{self.db_port}/{self.db_name}"
                f"?sslmode={self.db_sslmode}"
            )

        return None

    def get_async_connection_url(self) -> Optional[str]:
        """Build the async database connection URL (for asyncpg)."""
        url = self.get_connection_url()
        if url:
            # Replace postgresql:// with postgresql+asyncpg://
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return None


# Global settings instance
db_settings = DatabaseSettings()


# Lazy imports for database libraries
_psycopg2 = None
_asyncpg = None
_sqlalchemy = None


def _get_psycopg2():
    """Lazy import of psycopg2."""
    global _psycopg2
    if _psycopg2 is None:
        try:
            import psycopg2
            import psycopg2.pool
            _psycopg2 = psycopg2
        except ImportError:
            logger.warning("psycopg2 not installed. Install with: pip install psycopg2-binary")
            raise
    return _psycopg2


def _get_asyncpg():
    """Lazy import of asyncpg."""
    global _asyncpg
    if _asyncpg is None:
        try:
            import asyncpg
            _asyncpg = asyncpg
        except ImportError:
            logger.warning("asyncpg not installed. Install with: pip install asyncpg")
            raise
    return _asyncpg


class DatabaseConnection:
    """
    Synchronous database connection manager using psycopg2.
    Suitable for simple queries and scripts.
    """

    def __init__(self, settings: Optional[DatabaseSettings] = None):
        self.settings = settings or db_settings
        self._pool = None

    @property
    def pool(self):
        """Lazy initialization of connection pool."""
        if self._pool is None:
            psycopg2 = _get_psycopg2()
            url = self.settings.get_connection_url()
            if not url:
                raise ValueError("Database URL not configured. Set DATABASE_URL environment variable.")

            parsed = urlparse(url)
            self._pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=1,
                maxconn=self.settings.db_pool_size,
                host=parsed.hostname,
                port=parsed.port or 5432,
                database=parsed.path.lstrip("/").split("?")[0],
                user=parsed.username,
                password=parsed.password,
                sslmode=self.settings.db_sslmode,
            )
            logger.info(f"Database connection pool created for {parsed.hostname}")

        return self._pool

    @contextmanager
    def get_connection(self) -> Generator:
        """Get a connection from the pool."""
        conn = self.pool.getconn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self.pool.putconn(conn)

    def execute(self, query: str, params: tuple = None) -> list:
        """Execute a query and return results."""
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                if cur.description:
                    return cur.fetchall()
                return []

    def execute_many(self, query: str, params_list: list) -> None:
        """Execute a query with multiple parameter sets."""
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.executemany(query, params_list)

    def close(self):
        """Close the connection pool."""
        if self._pool:
            self._pool.closeall()
            self._pool = None
            logger.info("Database connection pool closed")


class AsyncDatabaseConnection:
    """
    Asynchronous database connection manager using asyncpg.
    Suitable for FastAPI endpoints and async operations.
    """

    def __init__(self, settings: Optional[DatabaseSettings] = None):
        self.settings = settings or db_settings
        self._pool = None

    async def get_pool(self):
        """Lazy initialization of async connection pool."""
        if self._pool is None:
            asyncpg = _get_asyncpg()
            url = self.settings.get_connection_url()
            if not url:
                raise ValueError("Database URL not configured. Set DATABASE_URL environment variable.")

            self._pool = await asyncpg.create_pool(
                url,
                min_size=1,
                max_size=self.settings.db_pool_size,
                max_inactive_connection_lifetime=self.settings.db_pool_recycle,
                command_timeout=self.settings.db_statement_timeout / 1000,  # Convert to seconds
            )
            logger.info("Async database connection pool created")

        return self._pool

    @asynccontextmanager
    async def get_connection(self) -> AsyncGenerator:
        """Get a connection from the async pool."""
        pool = await self.get_pool()
        async with pool.acquire() as conn:
            yield conn

    async def execute(self, query: str, *args) -> str:
        """Execute a query (INSERT, UPDATE, DELETE)."""
        async with self.get_connection() as conn:
            return await conn.execute(query, *args)

    async def fetch(self, query: str, *args) -> list:
        """Execute a query and fetch all results."""
        async with self.get_connection() as conn:
            return await conn.fetch(query, *args)

    async def fetchrow(self, query: str, *args) -> Optional[Any]:
        """Execute a query and fetch a single row."""
        async with self.get_connection() as conn:
            return await conn.fetchrow(query, *args)

    async def fetchval(self, query: str, *args) -> Optional[Any]:
        """Execute a query and fetch a single value."""
        async with self.get_connection() as conn:
            return await conn.fetchval(query, *args)

    async def close(self):
        """Close the async connection pool."""
        if self._pool:
            await self._pool.close()
            self._pool = None
            logger.info("Async database connection pool closed")


# Global connection instances (lazy initialized)
_sync_db: Optional[DatabaseConnection] = None
_async_db: Optional[AsyncDatabaseConnection] = None


def get_db() -> DatabaseConnection:
    """Get the global synchronous database connection."""
    global _sync_db
    if _sync_db is None:
        _sync_db = DatabaseConnection()
    return _sync_db


def get_async_db() -> AsyncDatabaseConnection:
    """Get the global asynchronous database connection."""
    global _async_db
    if _async_db is None:
        _async_db = AsyncDatabaseConnection()
    return _async_db


async def init_database():
    """
    Initialize database tables.
    Call this on application startup.
    """
    async_db = get_async_db()

    # Create tables if they don't exist
    await async_db.execute("""
        CREATE TABLE IF NOT EXISTS user_preferences (
            id SERIAL PRIMARY KEY,
            user_id VARCHAR(255) UNIQUE NOT NULL,
            preferences JSONB DEFAULT '{}',
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        )
    """)

    await async_db.execute("""
        CREATE TABLE IF NOT EXISTS saved_maps (
            id SERIAL PRIMARY KEY,
            user_id VARCHAR(255) NOT NULL,
            name VARCHAR(255) NOT NULL,
            description TEXT,
            map_config JSONB NOT NULL,
            layers JSONB DEFAULT '[]',
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        )
    """)

    await async_db.execute("""
        CREATE TABLE IF NOT EXISTS cadastral_queries (
            id SERIAL PRIMARY KEY,
            user_id VARCHAR(255),
            query_params JSONB NOT NULL,
            result_count INTEGER,
            executed_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        )
    """)

    await async_db.execute("""
        CREATE INDEX IF NOT EXISTS idx_saved_maps_user_id ON saved_maps(user_id)
    """)

    await async_db.execute("""
        CREATE INDEX IF NOT EXISTS idx_cadastral_queries_user_id ON cadastral_queries(user_id)
    """)

    logger.info("Database tables initialized")


async def close_database():
    """
    Close database connections.
    Call this on application shutdown.
    """
    global _sync_db, _async_db

    if _async_db:
        await _async_db.close()
        _async_db = None

    if _sync_db:
        _sync_db.close()
        _sync_db = None

    logger.info("Database connections closed")


def is_database_configured() -> bool:
    """Check if database is configured."""
    return db_settings.get_connection_url() is not None
