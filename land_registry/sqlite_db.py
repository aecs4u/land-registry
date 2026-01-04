"""
SQLite database module for local development and offline use.

Provides a lightweight local database for:
- User preferences and saved maps
- Drawn polygons and geometries
- Cadastral query cache
- File download tracking

This is used when Neon PostgreSQL is not available (local development).
"""

import os
import json
import sqlite3
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Generator

from land_registry.config import db_settings

logger = logging.getLogger(__name__)


def get_sqlite_path() -> str:
    """Get the absolute path to the SQLite database."""
    root_folder = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(root_folder, "..", db_settings.sqlite_path)


class SQLiteDatabase:
    """
    SQLite database connection manager for local development.
    Thread-safe with connection pooling via check_same_thread=False.
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or get_sqlite_path()
        self._ensure_database_exists()

    def _ensure_database_exists(self):
        """Ensure the database file and directory exist."""
        db_dir = os.path.dirname(self.db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)

        # Check if database needs initialization by looking for tables
        needs_init = not os.path.exists(self.db_path)
        if not needs_init:
            # File exists, check if it has tables
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='user_preferences'")
                if cursor.fetchone() is None:
                    needs_init = True
                conn.close()
            except Exception:
                needs_init = True

        if needs_init:
            logger.info(f"Initializing SQLite database at {self.db_path}")
            self._init_database()

    def _init_database(self):
        """Initialize the database schema."""
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # Enable foreign keys
            cursor.execute("PRAGMA foreign_keys = ON")

            # Create tables
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_preferences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT UNIQUE NOT NULL,
                    preferences TEXT DEFAULT '{}',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS saved_maps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT,
                    map_config TEXT NOT NULL,
                    layers TEXT DEFAULT '[]',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS cadastral_queries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT,
                    query_params TEXT NOT NULL,
                    result_count INTEGER,
                    executed_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS drawn_polygons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT,
                    name TEXT,
                    description TEXT,
                    geojson TEXT NOT NULL,
                    polygon_type TEXT DEFAULT 'polygon',
                    area_sqm REAL,
                    centroid_lat REAL,
                    centroid_lng REAL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS cadastral_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    s3_key TEXT UNIQUE NOT NULL,
                    file_type TEXT,
                    regione TEXT,
                    provincia TEXT,
                    comune TEXT,
                    file_size INTEGER,
                    last_modified TEXT,
                    is_available INTEGER DEFAULT 1,
                    cached_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    expires_at TEXT
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    description TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Create indexes
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_saved_maps_user_id ON saved_maps(user_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_cadastral_queries_user_id ON cadastral_queries(user_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_drawn_polygons_user_id ON drawn_polygons(user_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_cadastral_cache_comune ON cadastral_cache(regione, provincia, comune)")

            conn.commit()
            logger.info(f"SQLite database initialized at {self.db_path}")

    @contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Get a database connection with automatic commit/rollback."""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row  # Return rows as dict-like objects
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def execute(self, query: str, params: tuple = ()) -> List[sqlite3.Row]:
        """Execute a query and return results."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return cursor.fetchall()

    def execute_one(self, query: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        """Execute a query and return a single result."""
        results = self.execute(query, params)
        return results[0] if results else None

    def execute_insert(self, query: str, params: tuple = ()) -> int:
        """Execute an INSERT and return the last row ID."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return cursor.lastrowid

    # -------------------------------------------------------------------------
    # User Preferences
    # -------------------------------------------------------------------------

    def get_user_preferences(self, user_id: str) -> Dict[str, Any]:
        """Get user preferences as a dictionary."""
        row = self.execute_one(
            "SELECT preferences FROM user_preferences WHERE user_id = ?",
            (user_id,)
        )
        if row:
            return json.loads(row["preferences"])
        return {}

    def save_user_preferences(self, user_id: str, preferences: Dict[str, Any]) -> None:
        """Save user preferences."""
        prefs_json = json.dumps(preferences)
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO user_preferences (user_id, preferences, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                    preferences = excluded.preferences,
                    updated_at = CURRENT_TIMESTAMP
            """, (user_id, prefs_json))

    # -------------------------------------------------------------------------
    # Saved Maps
    # -------------------------------------------------------------------------

    def get_saved_maps(self, user_id: str) -> List[Dict[str, Any]]:
        """Get all saved maps for a user."""
        rows = self.execute(
            "SELECT * FROM saved_maps WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,)
        )
        return [dict(row) for row in rows]

    def get_saved_map(self, map_id: int) -> Optional[Dict[str, Any]]:
        """Get a specific saved map."""
        row = self.execute_one("SELECT * FROM saved_maps WHERE id = ?", (map_id,))
        return dict(row) if row else None

    def save_map(
        self,
        user_id: str,
        name: str,
        map_config: Dict[str, Any],
        description: str = None,
        layers: List[Dict] = None
    ) -> int:
        """Save a new map configuration."""
        return self.execute_insert("""
            INSERT INTO saved_maps (user_id, name, description, map_config, layers)
            VALUES (?, ?, ?, ?, ?)
        """, (
            user_id,
            name,
            description,
            json.dumps(map_config),
            json.dumps(layers or [])
        ))

    def update_saved_map(
        self,
        map_id: int,
        name: str = None,
        map_config: Dict[str, Any] = None,
        description: str = None,
        layers: List[Dict] = None
    ) -> None:
        """Update an existing saved map."""
        updates = []
        params = []

        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if description is not None:
            updates.append("description = ?")
            params.append(description)
        if map_config is not None:
            updates.append("map_config = ?")
            params.append(json.dumps(map_config))
        if layers is not None:
            updates.append("layers = ?")
            params.append(json.dumps(layers))

        if updates:
            updates.append("updated_at = CURRENT_TIMESTAMP")
            params.append(map_id)
            query = f"UPDATE saved_maps SET {', '.join(updates)} WHERE id = ?"
            self.execute(query, tuple(params))

    def delete_saved_map(self, map_id: int) -> None:
        """Delete a saved map."""
        self.execute("DELETE FROM saved_maps WHERE id = ?", (map_id,))

    # -------------------------------------------------------------------------
    # Drawn Polygons
    # -------------------------------------------------------------------------

    def get_drawn_polygons(self, user_id: str = None) -> List[Dict[str, Any]]:
        """Get drawn polygons, optionally filtered by user."""
        if user_id:
            rows = self.execute(
                "SELECT * FROM drawn_polygons WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,)
            )
        else:
            rows = self.execute("SELECT * FROM drawn_polygons ORDER BY created_at DESC")
        return [dict(row) for row in rows]

    def save_drawn_polygon(
        self,
        geojson: Dict[str, Any],
        user_id: str = None,
        name: str = None,
        description: str = None,
        polygon_type: str = "polygon",
        area_sqm: float = None,
        centroid_lat: float = None,
        centroid_lng: float = None
    ) -> int:
        """Save a drawn polygon."""
        return self.execute_insert("""
            INSERT INTO drawn_polygons
            (user_id, name, description, geojson, polygon_type, area_sqm, centroid_lat, centroid_lng)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            name,
            description,
            json.dumps(geojson),
            polygon_type,
            area_sqm,
            centroid_lat,
            centroid_lng
        ))

    def delete_drawn_polygon(self, polygon_id: int) -> None:
        """Delete a drawn polygon."""
        self.execute("DELETE FROM drawn_polygons WHERE id = ?", (polygon_id,))

    # -------------------------------------------------------------------------
    # Cadastral Query Cache
    # -------------------------------------------------------------------------

    def log_cadastral_query(
        self,
        query_params: Dict[str, Any],
        result_count: int = None,
        user_id: str = None
    ) -> int:
        """Log a cadastral query for history/analytics."""
        return self.execute_insert("""
            INSERT INTO cadastral_queries (user_id, query_params, result_count)
            VALUES (?, ?, ?)
        """, (user_id, json.dumps(query_params), result_count))

    def get_recent_queries(self, user_id: str = None, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent cadastral queries."""
        if user_id:
            rows = self.execute("""
                SELECT * FROM cadastral_queries
                WHERE user_id = ?
                ORDER BY executed_at DESC LIMIT ?
            """, (user_id, limit))
        else:
            rows = self.execute("""
                SELECT * FROM cadastral_queries
                ORDER BY executed_at DESC LIMIT ?
            """, (limit,))
        return [dict(row) for row in rows]

    # -------------------------------------------------------------------------
    # Cadastral File Cache
    # -------------------------------------------------------------------------

    def cache_cadastral_file(
        self,
        s3_key: str,
        file_type: str = None,
        regione: str = None,
        provincia: str = None,
        comune: str = None,
        file_size: int = None,
        is_available: bool = True
    ) -> None:
        """Cache cadastral file metadata."""
        expires_at = (datetime.now() + timedelta(hours=db_settings.cache_expiry_hours)).isoformat()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO cadastral_cache
                (s3_key, file_type, regione, provincia, comune, file_size, is_available, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(s3_key) DO UPDATE SET
                    file_type = excluded.file_type,
                    regione = excluded.regione,
                    provincia = excluded.provincia,
                    comune = excluded.comune,
                    file_size = excluded.file_size,
                    is_available = excluded.is_available,
                    cached_at = CURRENT_TIMESTAMP,
                    expires_at = excluded.expires_at
            """, (s3_key, file_type, regione, provincia, comune, file_size, int(is_available), expires_at))

    def get_cached_files(
        self,
        regione: str = None,
        provincia: str = None,
        comune: str = None,
        file_type: str = None
    ) -> List[Dict[str, Any]]:
        """Get cached cadastral files with optional filtering."""
        conditions = ["expires_at > datetime('now')"]
        params = []

        if regione:
            conditions.append("regione = ?")
            params.append(regione)
        if provincia:
            conditions.append("provincia = ?")
            params.append(provincia)
        if comune:
            conditions.append("comune = ?")
            params.append(comune)
        if file_type:
            conditions.append("file_type = ?")
            params.append(file_type)

        query = f"SELECT * FROM cadastral_cache WHERE {' AND '.join(conditions)}"
        rows = self.execute(query, tuple(params))
        return [dict(row) for row in rows]

    def is_file_cached(self, s3_key: str) -> bool:
        """Check if a file is in the cache and not expired."""
        row = self.execute_one("""
            SELECT is_available FROM cadastral_cache
            WHERE s3_key = ? AND expires_at > datetime('now')
        """, (s3_key,))
        return row is not None and row["is_available"]

    def clear_expired_cache(self) -> int:
        """Remove expired cache entries."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM cadastral_cache WHERE expires_at <= datetime('now')")
            return cursor.rowcount

    # -------------------------------------------------------------------------
    # App Settings
    # -------------------------------------------------------------------------

    def get_setting(self, key: str, default: str = None) -> Optional[str]:
        """Get an app setting value."""
        row = self.execute_one("SELECT value FROM app_settings WHERE key = ?", (key,))
        return row["value"] if row else default

    def set_setting(self, key: str, value: str, description: str = None) -> None:
        """Set an app setting value."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO app_settings (key, value, description, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    description = COALESCE(excluded.description, app_settings.description),
                    updated_at = CURRENT_TIMESTAMP
            """, (key, value, description))

    def get_all_settings(self) -> Dict[str, str]:
        """Get all app settings as a dictionary."""
        rows = self.execute("SELECT key, value FROM app_settings")
        return {row["key"]: row["value"] for row in rows}


# Global database instance (lazy initialized)
_sqlite_db: Optional[SQLiteDatabase] = None


def get_sqlite_db() -> SQLiteDatabase:
    """Get the global SQLite database instance."""
    global _sqlite_db
    if _sqlite_db is None:
        _sqlite_db = SQLiteDatabase()
    return _sqlite_db


def is_sqlite_available() -> bool:
    """Check if SQLite database is available and accessible."""
    try:
        db = get_sqlite_db()
        db.execute("SELECT 1")
        return True
    except Exception as e:
        logger.error(f"SQLite database not available: {e}")
        return False
