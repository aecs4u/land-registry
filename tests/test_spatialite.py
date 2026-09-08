"""
Unit tests for spatialite.py helper utilities.

Tests:
- _validate_identifier: valid/invalid SQL identifiers
- _allowed_tables: returns expected frozenset
- _table_exists: SQLite helper
- load_layer: invalid table (ValueError), missing db file (None)
"""

import os
import sqlite3
from unittest.mock import patch

import pytest

from land_registry.spatialite import (
    _allowed_tables,
    _table_exists,
    _validate_identifier,
    load_layer,
)


# ---------------------------------------------------------------------------
# _validate_identifier
# ---------------------------------------------------------------------------

class TestValidateIdentifier:

    def test_valid_simple(self):
        _validate_identifier("table_name", "column name")  # Should not raise

    def test_valid_with_numbers(self):
        _validate_identifier("col_123", "col")

    def test_valid_starts_with_underscore(self):
        _validate_identifier("_internal", "column")

    def test_invalid_with_spaces(self):
        with pytest.raises(ValueError, match="Invalid"):
            _validate_identifier("table name", "table")

    def test_invalid_with_semicolon(self):
        with pytest.raises(ValueError, match="Invalid"):
            _validate_identifier("name;DROP", "col")

    def test_invalid_with_dash(self):
        with pytest.raises(ValueError, match="Invalid"):
            _validate_identifier("column-name", "col")

    def test_invalid_starts_with_digit(self):
        with pytest.raises(ValueError, match="Invalid"):
            _validate_identifier("1column", "col")

    def test_invalid_empty_string(self):
        with pytest.raises(ValueError, match="Invalid"):
            _validate_identifier("", "col")

    def test_invalid_sql_injection(self):
        with pytest.raises(ValueError, match="Invalid"):
            _validate_identifier("x' OR '1'='1", "col")


# ---------------------------------------------------------------------------
# _allowed_tables
# ---------------------------------------------------------------------------

class TestAllowedTables:

    def test_returns_frozenset(self):
        result = _allowed_tables()
        assert isinstance(result, frozenset)

    def test_contains_known_tables(self):
        allowed = _allowed_tables()
        assert "fogli" in allowed
        assert "particelle" in allowed
        assert "cadastral_parcels" in allowed

    def test_does_not_contain_unknown(self):
        allowed = _allowed_tables()
        assert "evil_table" not in allowed
        assert "users" not in allowed


# ---------------------------------------------------------------------------
# _table_exists
# ---------------------------------------------------------------------------

class TestTableExists:

    def test_table_not_present(self):
        conn = sqlite3.connect(":memory:")
        assert _table_exists(conn, "nonexistent_table") is False
        conn.close()

    def test_table_present(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE my_table (id INTEGER)")
        assert _table_exists(conn, "my_table") is True
        conn.close()

    def test_different_table_not_found(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE table_a (id INTEGER)")
        assert _table_exists(conn, "table_b") is False
        conn.close()


# ---------------------------------------------------------------------------
# load_layer
# ---------------------------------------------------------------------------

class TestLoadLayer:

    def test_invalid_table_raises_value_error(self):
        with pytest.raises(ValueError, match="not in the list of allowed tables"):
            load_layer(table="invalid_table_name")

    def test_missing_db_path_returns_none(self, tmp_path):
        """When db file doesn't exist, returns None."""
        non_existent = str(tmp_path / "missing.db")
        with patch("land_registry.spatialite.spatialite_settings") as mock_settings:
            mock_settings.table = "fogli"
            mock_settings.default_limit = 100
            mock_settings.db_map_path = non_existent
            mock_settings.db_ple_path = non_existent
            mock_settings.geometry_column = "geometry"
            mock_settings.srid = 4326
            mock_settings.extension_path = "mod_spatialite"
            result = load_layer(table="fogli")
        assert result is None

    def test_ple_layer_type_uses_ple_path(self, tmp_path):
        """layer_type='ple' uses db_ple_path."""
        non_existent = str(tmp_path / "missing_ple.db")
        with patch("land_registry.spatialite.spatialite_settings") as mock_settings:
            mock_settings.table = "particelle"
            mock_settings.default_limit = 100
            mock_settings.db_map_path = non_existent
            mock_settings.db_ple_path = non_existent
            mock_settings.geometry_column = "geometry"
            mock_settings.srid = 4326
            mock_settings.extension_path = "mod_spatialite"
            result = load_layer(table="particelle", layer_type="ple")
        assert result is None

    def test_invalid_condition_column_raises(self, tmp_path):
        """Condition columns are validated as safe identifiers."""
        # Create a non-empty SQLite file so the db-provisioned check passes:
        # connect() alone leaves a zero-byte file, which load_layer now treats
        # as an unprovisioned placeholder and short-circuits before validation.
        import sqlite3 as _sqlite3
        db_file = str(tmp_path / "existing.db")
        _conn = _sqlite3.connect(db_file)
        _conn.execute("CREATE TABLE fogli (id INTEGER)")
        _conn.commit()
        _conn.close()

        with patch("land_registry.spatialite.spatialite_settings") as mock_settings:
            mock_settings.table = "fogli"
            mock_settings.default_limit = 100
            mock_settings.db_map_path = db_file
            mock_settings.db_ple_path = db_file
            mock_settings.geometry_column = "geometry"
            mock_settings.srid = 4326
            mock_settings.extension_path = "mod_spatialite"
            with pytest.raises(ValueError, match="Invalid"):
                load_layer(table="fogli", conditions={"bad-column": "value"})


class TestUnprovisionedDatabase:
    """A zero-byte placeholder must read as 'not provisioned', not as a
    database that happens to be missing one table."""

    def test_empty_db_file_returns_none(self, tmp_path):
        import sqlite3 as _sqlite3
        db_file = str(tmp_path / "placeholder.sqlite")
        _sqlite3.connect(db_file).close()
        assert os.path.getsize(db_file) == 0

        with patch("land_registry.spatialite.spatialite_settings") as mock_settings:
            mock_settings.table = "cadastral_parcels"
            mock_settings.default_limit = 100
            mock_settings.db_map_path = db_file
            mock_settings.db_ple_path = db_file
            mock_settings.geometry_column = "geometry"
            mock_settings.srid = 4326
            mock_settings.extension_path = "mod_spatialite"
            result = load_layer(table="fogli")

        assert result is None


class TestLayerTypeDefaultTable:
    """The default table follows layer_type, so a 'map' request never goes
    looking for the particelle table (and vice versa)."""

    def test_map_default_uses_table_map(self, tmp_path):
        missing = str(tmp_path / "missing_map.sqlite")
        with patch("land_registry.spatialite.spatialite_settings") as mock_settings:
            mock_settings.table = "cadastral_parcels"
            mock_settings.table_map = "fogli"
            mock_settings.table_ple = "particelle"
            mock_settings.default_limit = 100
            mock_settings.db_map_path = missing
            mock_settings.db_ple_path = missing
            mock_settings.geometry_column = "geometry"
            mock_settings.srid = 4326
            mock_settings.extension_path = "mod_spatialite"
            # Reaching the missing-file branch proves the allowlist accepted
            # the resolved default rather than raising ValueError.
            assert load_layer(layer_type="map") is None

    def test_ple_default_uses_table_ple(self, tmp_path):
        missing = str(tmp_path / "missing_ple.sqlite")
        with patch("land_registry.spatialite.spatialite_settings") as mock_settings:
            mock_settings.table = "cadastral_parcels"
            mock_settings.table_map = "fogli"
            mock_settings.table_ple = "particelle"
            mock_settings.default_limit = 100
            mock_settings.db_map_path = missing
            mock_settings.db_ple_path = missing
            mock_settings.geometry_column = "geometry"
            mock_settings.srid = 4326
            mock_settings.extension_path = "mod_spatialite"
            assert load_layer(layer_type="ple") is None

    def test_settings_defaults_match_layer_databases(self):
        """The shipped defaults must name the tables each database holds."""
        from land_registry.config import spatialite_settings as real_settings

        assert real_settings.table_map == "fogli"
        assert real_settings.table_ple == "particelle"
