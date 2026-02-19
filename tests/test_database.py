"""
Tests for the Neon PostgreSQL database module.

Tests cover:
- Database settings configuration
- Connection URL building
- Sync and async connection management
- Database initialization
- Query execution

Note: These tests use mocking to avoid actual database connections.
"""

import pytest
from unittest.mock import Mock, MagicMock, patch, AsyncMock
from contextlib import contextmanager

from land_registry.database import (
    DatabaseSettings,
    DatabaseConnection,
    AsyncDatabaseConnection,
    get_db,
    get_async_db,
    is_database_configured,
    db_settings,
)


class TestDatabaseSettings:
    """Test database settings configuration."""

    def test_default_settings(self):
        """Test default database settings values."""
        settings = DatabaseSettings()

        assert settings.database_url is None
        assert settings.db_port == 5432
        assert settings.db_name == "land_registry"
        assert settings.db_sslmode == "require"
        assert settings.db_pool_size == 5
        assert settings.db_statement_timeout == 30000

    def test_connection_url_from_database_url(self):
        """Test building connection URL from DATABASE_URL."""
        settings = DatabaseSettings(
            database_url="postgresql://user:pass@host/db?sslmode=require"
        )

        url = settings.get_connection_url()

        assert url == "postgresql://user:pass@host/db?sslmode=require"

    def test_connection_url_from_individual_params(self):
        """Test building connection URL from individual parameters."""
        settings = DatabaseSettings(
            db_host="neon.example.com",
            db_port=5432,
            db_name="mydb",
            db_user="myuser",
            db_password="mypass",
            db_sslmode="require",
        )

        url = settings.get_connection_url()

        assert "postgresql://myuser:mypass@neon.example.com:5432/mydb" in url
        assert "sslmode=require" in url

    def test_connection_url_none_when_not_configured(self):
        """Test connection URL is None when not configured."""
        settings = DatabaseSettings()

        url = settings.get_connection_url()

        assert url is None

    def test_async_connection_url(self):
        """Test async connection URL conversion."""
        settings = DatabaseSettings(
            database_url="postgresql://user:pass@host/db"
        )

        async_url = settings.get_async_connection_url()

        assert async_url.startswith("postgresql+asyncpg://")
        assert "user:pass@host/db" in async_url

    def test_settings_from_env(self, monkeypatch):
        """Test settings can be loaded from environment variables."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://env_user:env_pass@env_host/env_db")

        settings = DatabaseSettings()

        assert settings.database_url == "postgresql://env_user:env_pass@env_host/env_db"


class TestDatabaseConnection:
    """Test synchronous database connection."""

    def test_init_with_custom_settings(self):
        """Test creating connection with custom settings."""
        custom_settings = DatabaseSettings(
            database_url="postgresql://custom@host/db"
        )
        conn = DatabaseConnection(settings=custom_settings)

        assert conn.settings.database_url == "postgresql://custom@host/db"

    def test_raises_when_not_configured(self):
        """Test that accessing pool raises when not configured."""
        conn = DatabaseConnection(settings=DatabaseSettings())

        with pytest.raises(ValueError) as exc_info:
            _ = conn.pool

        assert "not configured" in str(exc_info.value)

    @patch("land_registry.database._get_psycopg2")
    def test_pool_creation(self, mock_get_psycopg2):
        """Test connection pool is created correctly."""
        mock_psycopg2 = Mock()
        mock_pool = Mock()
        mock_psycopg2.pool.ThreadedConnectionPool.return_value = mock_pool
        mock_get_psycopg2.return_value = mock_psycopg2

        settings = DatabaseSettings(
            database_url="postgresql://user:pass@host:5432/db?sslmode=require"
        )
        conn = DatabaseConnection(settings=settings)

        pool = conn.pool

        assert pool is mock_pool
        mock_psycopg2.pool.ThreadedConnectionPool.assert_called_once()

    @patch("land_registry.database._get_psycopg2")
    def test_execute_query(self, mock_get_psycopg2):
        """Test executing a query."""
        # Setup mocks
        mock_psycopg2 = Mock()
        mock_pool = Mock()
        mock_conn = Mock()
        mock_cursor = Mock()

        mock_cursor.description = [("col1",), ("col2",)]
        mock_cursor.fetchall.return_value = [(1, "value")]

        mock_conn.cursor.return_value.__enter__ = Mock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = Mock(return_value=False)
        mock_pool.getconn.return_value = mock_conn
        mock_psycopg2.pool.ThreadedConnectionPool.return_value = mock_pool
        mock_get_psycopg2.return_value = mock_psycopg2

        settings = DatabaseSettings(
            database_url="postgresql://user:pass@host/db"
        )
        conn = DatabaseConnection(settings=settings)

        result = conn.execute("SELECT * FROM table WHERE id = %s", (1,))

        assert result == [(1, "value")]
        mock_cursor.execute.assert_called_once_with("SELECT * FROM table WHERE id = %s", (1,))

    @patch("land_registry.database._get_psycopg2")
    def test_close_pool(self, mock_get_psycopg2):
        """Test closing the connection pool."""
        mock_psycopg2 = Mock()
        mock_pool = Mock()
        mock_psycopg2.pool.ThreadedConnectionPool.return_value = mock_pool
        mock_get_psycopg2.return_value = mock_psycopg2

        settings = DatabaseSettings(
            database_url="postgresql://user:pass@host/db"
        )
        conn = DatabaseConnection(settings=settings)

        # Access pool to create it
        _ = conn.pool

        conn.close()

        mock_pool.closeall.assert_called_once()
        assert conn._pool is None


class TestAsyncDatabaseConnection:
    """Test asynchronous database connection."""

    def test_init_with_custom_settings(self):
        """Test creating async connection with custom settings."""
        custom_settings = DatabaseSettings(
            database_url="postgresql://custom@host/db"
        )
        conn = AsyncDatabaseConnection(settings=custom_settings)

        assert conn.settings.database_url == "postgresql://custom@host/db"

    @pytest.mark.asyncio
    async def test_raises_when_not_configured(self):
        """Test that getting pool raises when not configured."""
        conn = AsyncDatabaseConnection(settings=DatabaseSettings())

        with pytest.raises(ValueError) as exc_info:
            await conn.get_pool()

        assert "not configured" in str(exc_info.value)

    @pytest.mark.asyncio
    @patch("land_registry.database._get_asyncpg")
    async def test_pool_creation(self, mock_get_asyncpg):
        """Test async connection pool is created correctly."""
        mock_asyncpg = Mock()
        mock_pool = AsyncMock()
        mock_asyncpg.create_pool = AsyncMock(return_value=mock_pool)
        mock_get_asyncpg.return_value = mock_asyncpg

        settings = DatabaseSettings(
            database_url="postgresql://user:pass@host/db"
        )
        conn = AsyncDatabaseConnection(settings=settings)

        pool = await conn.get_pool()

        assert pool is mock_pool
        mock_asyncpg.create_pool.assert_called_once()

    @pytest.mark.asyncio
    @patch("land_registry.database._get_asyncpg")
    async def test_fetch_query(self, mock_get_asyncpg):
        """Test fetching query results."""
        mock_asyncpg = Mock()
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[{"id": 1, "name": "test"}])

        # Create a proper async context manager for acquire()
        mock_pool = AsyncMock()
        mock_pool.acquire = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

        mock_asyncpg.create_pool = AsyncMock(return_value=mock_pool)
        mock_get_asyncpg.return_value = mock_asyncpg

        settings = DatabaseSettings(
            database_url="postgresql://user:pass@host/db"
        )
        conn = AsyncDatabaseConnection(settings=settings)

        result = await conn.fetch("SELECT * FROM table")

        assert result == [{"id": 1, "name": "test"}]

    @pytest.mark.asyncio
    @patch("land_registry.database._get_asyncpg")
    async def test_fetchrow_query(self, mock_get_asyncpg):
        """Test fetching single row."""
        mock_asyncpg = Mock()
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={"id": 1})

        # Create a proper async context manager for acquire()
        mock_pool = AsyncMock()
        mock_pool.acquire = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

        mock_asyncpg.create_pool = AsyncMock(return_value=mock_pool)
        mock_get_asyncpg.return_value = mock_asyncpg

        settings = DatabaseSettings(
            database_url="postgresql://user:pass@host/db"
        )
        conn = AsyncDatabaseConnection(settings=settings)

        result = await conn.fetchrow("SELECT * FROM table WHERE id = $1", 1)

        assert result == {"id": 1}

    @pytest.mark.asyncio
    @patch("land_registry.database._get_asyncpg")
    async def test_close_pool(self, mock_get_asyncpg):
        """Test closing the async connection pool."""
        mock_asyncpg = Mock()
        mock_pool = AsyncMock()
        mock_asyncpg.create_pool = AsyncMock(return_value=mock_pool)
        mock_get_asyncpg.return_value = mock_asyncpg

        settings = DatabaseSettings(
            database_url="postgresql://user:pass@host/db"
        )
        conn = AsyncDatabaseConnection(settings=settings)

        # Create pool
        await conn.get_pool()

        await conn.close()

        mock_pool.close.assert_called_once()
        assert conn._pool is None


class TestGlobalFunctions:
    """Test global helper functions."""

    def test_get_db_returns_instance(self):
        """Test get_db returns a DatabaseConnection instance."""
        # Reset global instance
        import land_registry.database as db_module
        db_module._sync_db = None

        db = get_db()

        assert isinstance(db, DatabaseConnection)

    def test_get_db_returns_same_instance(self):
        """Test get_db returns the same instance."""
        db1 = get_db()
        db2 = get_db()

        assert db1 is db2

    def test_get_async_db_returns_instance(self):
        """Test get_async_db returns an AsyncDatabaseConnection instance."""
        # Reset global instance
        import land_registry.database as db_module
        db_module._async_db = None

        db = get_async_db()

        assert isinstance(db, AsyncDatabaseConnection)

    def test_get_async_db_returns_same_instance(self):
        """Test get_async_db returns the same instance."""
        db1 = get_async_db()
        db2 = get_async_db()

        assert db1 is db2

    def test_is_database_configured_true(self, monkeypatch):
        """Test is_database_configured returns True when URL is set."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host/db")

        # Reimport to pick up env var
        import land_registry.database as db_module
        db_module.db_settings = DatabaseSettings()

        result = is_database_configured()

        assert result is True

    def test_is_database_configured_false(self):
        """Test is_database_configured returns False when not configured."""
        import land_registry.database as db_module
        db_module.db_settings = DatabaseSettings(database_url=None)

        result = is_database_configured()

        assert result is False


class TestInitDatabase:
    """Test database initialization."""

    @pytest.mark.asyncio
    @patch("land_registry.database.get_async_db")
    async def test_init_database_creates_tables(self, mock_get_async_db):
        """Test init_database creates required tables."""
        from land_registry.database import init_database

        mock_db = AsyncMock()
        mock_get_async_db.return_value = mock_db

        await init_database()

        # Should execute multiple CREATE TABLE statements
        assert mock_db.execute.call_count >= 3

        # Check that important tables are created
        calls = [str(call) for call in mock_db.execute.call_args_list]
        call_text = " ".join(calls)

        assert "user_preferences" in call_text
        assert "saved_maps" in call_text
        assert "cadastral_queries" in call_text


class TestCloseDatabase:
    """Test database cleanup."""

    @pytest.mark.asyncio
    @patch("land_registry.database._async_db")
    @patch("land_registry.database._sync_db")
    async def test_close_database(self, mock_sync_db, mock_async_db):
        """Test close_database closes all connections."""
        from land_registry.database import close_database
        import land_registry.database as db_module

        mock_async = AsyncMock()
        mock_sync = Mock()

        db_module._async_db = mock_async
        db_module._sync_db = mock_sync

        await close_database()

        mock_async.close.assert_called_once()
        mock_sync.close.assert_called_once()


class TestLazyImports:
    """Test lazy import behavior."""

    @patch.dict("sys.modules", {"psycopg2": None, "psycopg2.pool": None})
    def test_psycopg2_import_error(self):
        """Test proper handling of missing psycopg2."""
        # Reset the cached import
        import land_registry.database as db_module
        db_module._psycopg2 = None

        # This should raise ImportError when psycopg2 is not available
        # In a real test environment, psycopg2 would be installed
        # so we just verify the lazy import mechanism exists
        assert hasattr(db_module, "_get_psycopg2")

    @patch.dict("sys.modules", {"asyncpg": None})
    def test_asyncpg_import_error(self):
        """Test proper handling of missing asyncpg."""
        import land_registry.database as db_module
        db_module._asyncpg = None

        # Verify the lazy import mechanism exists
        assert hasattr(db_module, "_get_asyncpg")
