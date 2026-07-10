"""
Tests for the SQLite database module.

Tests cover:
- Database initialization and schema creation
- User preferences CRUD operations
- Saved maps CRUD operations
- Drawn polygons CRUD operations
- Cadastral query logging
- Cadastral file caching
- App settings management
"""

import json
import os
import tempfile
import pytest
from datetime import datetime, timedelta

from land_registry.sqlite_db import SQLiteDatabase, get_sqlite_db, is_sqlite_available


@pytest.fixture
def temp_db():
    """Create a temporary SQLite database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
        db_path = f.name

    db = SQLiteDatabase(db_path=db_path)
    yield db

    # Cleanup
    if os.path.exists(db_path):
        os.unlink(db_path)


class TestSQLiteDatabaseInitialization:
    """Test database initialization and schema."""

    def test_database_creates_file(self, temp_db):
        """Test that database file is created."""
        assert os.path.exists(temp_db.db_path)

    def test_database_creates_tables(self, temp_db):
        """Test that all required tables are created."""
        rows = temp_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        table_names = [row["name"] for row in rows]

        expected_tables = [
            "app_settings",
            "cadastral_cache",
            "cadastral_queries",
            "drawn_polygons",
            "saved_maps",
            "user_preferences",
        ]

        for table in expected_tables:
            assert table in table_names, f"Table {table} should exist"

    def test_database_creates_indexes(self, temp_db):
        """Test that indexes are created."""
        rows = temp_db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
        )
        index_names = [row["name"] for row in rows]

        expected_indexes = [
            "idx_saved_maps_user_id",
            "idx_cadastral_queries_user_id",
            "idx_drawn_polygons_user_id",
            "idx_cadastral_cache_comune",
        ]

        for index in expected_indexes:
            assert index in index_names, f"Index {index} should exist"


class TestUserPreferences:
    """Test user preferences operations."""

    def test_get_preferences_empty(self, temp_db):
        """Test getting preferences for non-existent user."""
        prefs = temp_db.get_user_preferences("nonexistent_user")
        assert prefs == {}

    def test_save_and_get_preferences(self, temp_db):
        """Test saving and retrieving preferences."""
        user_id = "test_user_1"
        preferences = {
            "theme": "dark",
            "language": "it",
            "map_zoom": 10,
            "show_labels": True,
        }

        temp_db.save_user_preferences(user_id, preferences)
        retrieved = temp_db.get_user_preferences(user_id)

        assert retrieved == preferences

    def test_update_preferences(self, temp_db):
        """Test updating existing preferences."""
        user_id = "test_user_2"

        # Save initial preferences
        temp_db.save_user_preferences(user_id, {"theme": "light"})

        # Update preferences
        updated_prefs = {"theme": "dark", "new_setting": "value"}
        temp_db.save_user_preferences(user_id, updated_prefs)

        retrieved = temp_db.get_user_preferences(user_id)
        assert retrieved == updated_prefs


class TestSavedMaps:
    """Test saved maps operations."""

    def test_save_map(self, temp_db):
        """Test saving a map configuration."""
        user_id = "test_user"
        map_config = {
            "center": [41.9, 12.5],
            "zoom": 10,
            "base_layer": "OpenStreetMap",
        }

        map_id = temp_db.save_map(
            user_id=user_id,
            name="Test Map",
            map_config=map_config,
            description="A test map",
            layers=[{"name": "Cadastral", "visible": True}],
        )

        assert map_id > 0

    def test_get_saved_maps(self, temp_db):
        """Test retrieving saved maps for a user."""
        user_id = "test_user"

        # Save multiple maps
        temp_db.save_map(user_id, "Map 1", {"zoom": 5})
        temp_db.save_map(user_id, "Map 2", {"zoom": 10})
        temp_db.save_map("other_user", "Other Map", {"zoom": 8})

        maps = temp_db.get_saved_maps(user_id)

        assert len(maps) == 2
        assert all(m["user_id"] == user_id for m in maps)

    def test_get_saved_map_by_id(self, temp_db):
        """Test retrieving a specific map by ID."""
        map_id = temp_db.save_map("user", "Test", {"zoom": 5}, description="Test description")

        retrieved = temp_db.get_saved_map(map_id)

        assert retrieved is not None
        assert retrieved["name"] == "Test"
        assert retrieved["description"] == "Test description"
        assert json.loads(retrieved["map_config"]) == {"zoom": 5}

    def test_update_saved_map(self, temp_db):
        """Test updating a saved map."""
        map_id = temp_db.save_map("user", "Original Name", {"zoom": 5})

        temp_db.update_saved_map(
            map_id,
            name="Updated Name",
            map_config={"zoom": 15},
            description="New description",
        )

        retrieved = temp_db.get_saved_map(map_id)

        assert retrieved["name"] == "Updated Name"
        assert retrieved["description"] == "New description"
        assert json.loads(retrieved["map_config"]) == {"zoom": 15}

    def test_delete_saved_map(self, temp_db):
        """Test deleting a saved map."""
        map_id = temp_db.save_map("user", "To Delete", {"zoom": 5})

        temp_db.delete_saved_map(map_id)

        assert temp_db.get_saved_map(map_id) is None


class TestDrawnPolygons:
    """Test drawn polygons operations."""

    def test_save_drawn_polygon(self, temp_db):
        """Test saving a drawn polygon."""
        geojson = {
            "type": "Polygon",
            "coordinates": [
                [[12.0, 41.0], [12.1, 41.0], [12.1, 41.1], [12.0, 41.1], [12.0, 41.0]]
            ],
        }

        polygon_id = temp_db.save_drawn_polygon(
            geojson=geojson,
            user_id="test_user",
            name="Test Polygon",
            description="A test polygon",
            polygon_type="polygon",
            area_sqm=1000.5,
            centroid_lat=41.05,
            centroid_lng=12.05,
        )

        assert polygon_id > 0

    def test_get_drawn_polygons_all(self, temp_db):
        """Test retrieving all drawn polygons."""
        temp_db.save_drawn_polygon({"type": "Polygon"}, user_id="user1", name="Poly 1")
        temp_db.save_drawn_polygon({"type": "Polygon"}, user_id="user2", name="Poly 2")

        polygons = temp_db.get_drawn_polygons()

        assert len(polygons) == 2

    def test_get_drawn_polygons_by_user(self, temp_db):
        """Test retrieving polygons for a specific user."""
        temp_db.save_drawn_polygon({"type": "Polygon"}, user_id="user1", name="Poly 1")
        temp_db.save_drawn_polygon({"type": "Polygon"}, user_id="user1", name="Poly 2")
        temp_db.save_drawn_polygon({"type": "Polygon"}, user_id="user2", name="Poly 3")

        user1_polygons = temp_db.get_drawn_polygons(user_id="user1")

        assert len(user1_polygons) == 2
        assert all(p["user_id"] == "user1" for p in user1_polygons)

    def test_delete_drawn_polygon(self, temp_db):
        """Test deleting a drawn polygon."""
        polygon_id = temp_db.save_drawn_polygon({"type": "Polygon"}, name="To Delete")

        temp_db.delete_drawn_polygon(polygon_id)

        polygons = temp_db.get_drawn_polygons()
        assert len(polygons) == 0


class TestCadastralQueries:
    """Test cadastral query logging operations."""

    def test_log_cadastral_query(self, temp_db):
        """Test logging a cadastral query."""
        query_params = {
            "regione": "LAZIO",
            "provincia": "ROMA",
            "comune": "ROMA",
            "file_type": "MAP",
        }

        query_id = temp_db.log_cadastral_query(
            query_params=query_params,
            result_count=42,
            user_id="test_user",
        )

        assert query_id > 0

    def test_get_recent_queries(self, temp_db):
        """Test retrieving recent queries."""
        # Log multiple queries
        for i in range(5):
            temp_db.log_cadastral_query(
                query_params={"query": i},
                result_count=i * 10,
                user_id="test_user",
            )

        queries = temp_db.get_recent_queries(user_id="test_user", limit=3)

        assert len(queries) == 3

    def test_get_recent_queries_all_users(self, temp_db):
        """Test retrieving recent queries for all users."""
        temp_db.log_cadastral_query({"q": 1}, user_id="user1")
        temp_db.log_cadastral_query({"q": 2}, user_id="user2")

        queries = temp_db.get_recent_queries(limit=10)

        assert len(queries) == 2


class TestCadastralCache:
    """Test cadastral file caching operations."""

    def test_cache_cadastral_file(self, temp_db):
        """Test caching a cadastral file."""
        temp_db.cache_cadastral_file(
            s3_key="ITALIA/LAZIO/ROMA/ROMA/MAP.gpkg",
            file_type="MAP",
            regione="LAZIO",
            provincia="ROMA",
            comune="ROMA",
            file_size=1024000,
            is_available=True,
        )

        # Verify it was cached
        assert temp_db.is_file_cached("ITALIA/LAZIO/ROMA/ROMA/MAP.gpkg")

    def test_is_file_cached_false(self, temp_db):
        """Test checking for non-cached file."""
        assert not temp_db.is_file_cached("nonexistent/file.gpkg")

    def test_get_cached_files_by_region(self, temp_db):
        """Test retrieving cached files by region."""
        temp_db.cache_cadastral_file("key1", regione="LAZIO", provincia="ROMA")
        temp_db.cache_cadastral_file("key2", regione="LAZIO", provincia="VITERBO")
        temp_db.cache_cadastral_file("key3", regione="LOMBARDIA", provincia="MILANO")

        lazio_files = temp_db.get_cached_files(regione="LAZIO")

        assert len(lazio_files) == 2

    def test_get_cached_files_by_comune(self, temp_db):
        """Test retrieving cached files by comune."""
        temp_db.cache_cadastral_file(
            "key1", regione="LAZIO", provincia="ROMA", comune="ROMA"
        )
        temp_db.cache_cadastral_file(
            "key2", regione="LAZIO", provincia="ROMA", comune="TIVOLI"
        )

        roma_files = temp_db.get_cached_files(
            regione="LAZIO", provincia="ROMA", comune="ROMA"
        )

        assert len(roma_files) == 1

    def test_cache_update_existing(self, temp_db):
        """Test updating an existing cache entry."""
        s3_key = "test/file.gpkg"

        temp_db.cache_cadastral_file(s3_key, file_size=100)
        temp_db.cache_cadastral_file(s3_key, file_size=200)

        # Should only have one entry
        rows = temp_db.execute("SELECT * FROM cadastral_cache WHERE s3_key = ?", (s3_key,))
        assert len(rows) == 1
        assert rows[0]["file_size"] == 200


class TestAppSettings:
    """Test app settings operations."""

    def test_get_setting_default(self, temp_db):
        """Test getting a setting with default value."""
        value = temp_db.get_setting("nonexistent", default="default_value")
        assert value == "default_value"

    def test_set_and_get_setting(self, temp_db):
        """Test setting and getting a setting."""
        temp_db.set_setting("test_key", "test_value", "Test description")

        value = temp_db.get_setting("test_key")
        assert value == "test_value"

    def test_update_setting(self, temp_db):
        """Test updating an existing setting."""
        temp_db.set_setting("key", "value1")
        temp_db.set_setting("key", "value2")

        assert temp_db.get_setting("key") == "value2"

    def test_get_all_settings(self, temp_db):
        """Test getting all settings."""
        temp_db.set_setting("key1", "value1")
        temp_db.set_setting("key2", "value2")

        settings = temp_db.get_all_settings()

        assert "key1" in settings
        assert "key2" in settings
        assert settings["key1"] == "value1"
        assert settings["key2"] == "value2"


class TestGlobalInstance:
    """Test global database instance functions."""

    def test_is_sqlite_available(self):
        """Test checking SQLite availability."""
        # This should work if the default database exists or can be created
        result = is_sqlite_available()
        assert isinstance(result, bool)

    def test_get_sqlite_db_returns_same_instance(self):
        """Test that get_sqlite_db returns the same instance."""
        db1 = get_sqlite_db()
        db2 = get_sqlite_db()
        assert db1 is db2


class TestConnectionManagement:
    """Test connection handling."""

    def test_connection_context_manager(self, temp_db):
        """Test that connection context manager works correctly."""
        with temp_db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            result = cursor.fetchone()
            assert result[0] == 1

    def test_connection_rollback_on_error(self, temp_db):
        """Test that connection rolls back on error."""
        try:
            with temp_db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO app_settings (key, value) VALUES (?, ?)",
                    ("rollback_test", "value"),
                )
                # Force an error
                raise ValueError("Test error")
        except ValueError:
            pass

        # The insert should have been rolled back
        value = temp_db.get_setting("rollback_test")
        assert value is None

    def test_execute_returns_rows(self, temp_db):
        """Test that execute returns row objects."""
        temp_db.set_setting("test", "value")

        rows = temp_db.execute("SELECT * FROM app_settings WHERE key = ?", ("test",))

        assert len(rows) == 1
        # Row should be dict-like
        assert rows[0]["key"] == "test"
        assert rows[0]["value"] == "value"

    def test_execute_one_returns_single_row(self, temp_db):
        """Test that execute_one returns a single row."""
        temp_db.set_setting("single", "row")

        row = temp_db.execute_one(
            "SELECT * FROM app_settings WHERE key = ?", ("single",)
        )

        assert row is not None
        assert row["key"] == "single"

    def test_execute_one_returns_none_for_no_results(self, temp_db):
        """Test that execute_one returns None when no results."""
        row = temp_db.execute_one(
            "SELECT * FROM app_settings WHERE key = ?", ("nonexistent",)
        )

        assert row is None
