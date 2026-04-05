"""
Unit tests for FileAvailabilityDB.

Tests all SQLite-backed methods using a temp database file.
"""

import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from land_registry.file_availability_db import FileAvailabilityDB


@pytest.fixture
def db(tmp_path):
    """Fresh FileAvailabilityDB backed by a temp file."""
    db_path = str(tmp_path / "test_availability.db")
    return FileAvailabilityDB(db_path=db_path)


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------

class TestInit:

    def test_creates_db_file(self, tmp_path):
        db_path = str(tmp_path / "new.db")
        assert not os.path.exists(db_path)
        FileAvailabilityDB(db_path=db_path)
        assert os.path.exists(db_path)

    def test_idempotent_init(self, db):
        """Calling init_database again doesn't fail (IF NOT EXISTS tables)."""
        db.init_database()  # Second call should be safe


# ---------------------------------------------------------------------------
# get_file_status / set_file_status
# ---------------------------------------------------------------------------

class TestFileStatus:

    def test_cache_miss_returns_none(self, db):
        result = db.get_file_status("s3://bucket/missing_file.gpkg")
        assert result is None

    def test_set_then_get(self, db):
        db.set_file_status("s3://bucket/test.gpkg", 200)
        result = db.get_file_status("s3://bucket/test.gpkg")
        assert result == 200

    def test_set_404_status(self, db):
        db.set_file_status("s3://bucket/not_found.gpkg", 404)
        result = db.get_file_status("s3://bucket/not_found.gpkg")
        assert result == 404

    def test_update_existing_status(self, db):
        """Setting status twice updates the record."""
        db.set_file_status("s3://bucket/file.gpkg", 200)
        db.set_file_status("s3://bucket/file.gpkg", 404)
        result = db.get_file_status("s3://bucket/file.gpkg")
        assert result == 404

    def test_expired_cache_returns_none(self, db):
        """Entry older than max_age_hours is ignored."""
        import sqlite3
        # Manually insert an old entry
        old_time = datetime.now() - timedelta(hours=48)
        with sqlite3.connect(db.db_path) as conn:
            conn.execute(
                "INSERT INTO file_status (file_path, status_code, last_checked) VALUES (?, ?, ?)",
                ("old_file.gpkg", 200, old_time)
            )
            conn.commit()

        result = db.get_file_status("old_file.gpkg", max_age_hours=24)
        assert result is None

    def test_recent_cache_returned(self, db):
        """Entry within max_age_hours is returned."""
        db.set_file_status("recent.gpkg", 200)
        result = db.get_file_status("recent.gpkg", max_age_hours=24)
        assert result == 200


# ---------------------------------------------------------------------------
# get_file_status_batch / set_file_status_batch
# ---------------------------------------------------------------------------

class TestBatchOperations:

    def test_empty_input_returns_empty_dict(self, db):
        result = db.get_file_status_batch([])
        assert result == {}

    def test_batch_cache_miss(self, db):
        result = db.get_file_status_batch(["file1.gpkg", "file2.gpkg"])
        assert result == {}

    def test_batch_set_then_get(self, db):
        statuses = {
            "file1.gpkg": 200,
            "file2.gpkg": 404,
            "file3.gpkg": 403,
        }
        db.set_file_status_batch(statuses)
        result = db.get_file_status_batch(list(statuses.keys()))
        assert result == statuses

    def test_batch_set_empty_dict_noop(self, db):
        """Setting empty batch is a no-op."""
        db.set_file_status_batch({})
        assert db.get_stats()["total_entries"] == 0

    def test_batch_partial_match(self, db):
        """Only returns statuses for paths that were cached."""
        db.set_file_status_batch({"file1.gpkg": 200})
        result = db.get_file_status_batch(["file1.gpkg", "file_unknown.gpkg"])
        assert "file1.gpkg" in result
        assert "file_unknown.gpkg" not in result


# ---------------------------------------------------------------------------
# clear_cache
# ---------------------------------------------------------------------------

class TestClearCache:

    def test_clear_removes_all_entries(self, db):
        db.set_file_status("a.gpkg", 200)
        db.set_file_status("b.gpkg", 404)
        db.clear_cache()
        assert db.get_file_status("a.gpkg") is None
        assert db.get_file_status("b.gpkg") is None
        assert db.get_stats()["total_entries"] == 0

    def test_clear_empty_db_is_safe(self, db):
        db.clear_cache()  # Should not raise


# ---------------------------------------------------------------------------
# cleanup_old_entries
# ---------------------------------------------------------------------------

class TestCleanupOldEntries:

    def test_removes_old_entries(self, db):
        import sqlite3
        old_time = datetime.now() - timedelta(days=10)
        with sqlite3.connect(db.db_path) as conn:
            conn.execute(
                "INSERT INTO file_status (file_path, status_code, last_checked) VALUES (?, ?, ?)",
                ("old.gpkg", 200, old_time)
            )
            conn.commit()

        db.set_file_status("recent.gpkg", 200)
        db.cleanup_old_entries(max_age_days=7)

        assert db.get_file_status("old.gpkg", max_age_hours=999999) is None
        assert db.get_file_status("recent.gpkg") == 200

    def test_empty_db_cleanup_is_safe(self, db):
        db.cleanup_old_entries()


# ---------------------------------------------------------------------------
# get_stats
# ---------------------------------------------------------------------------

class TestGetStats:

    def test_empty_db_stats(self, db):
        stats = db.get_stats()
        assert stats["total_entries"] == 0
        assert stats["available_files"] == 0
        assert stats["missing_files"] == 0
        assert stats["error_files"] == 0

    def test_stats_counts_correctly(self, db):
        db.set_file_status("a.gpkg", 200)
        db.set_file_status("b.gpkg", 200)
        db.set_file_status("c.gpkg", 404)
        db.set_file_status("d.gpkg", 403)

        stats = db.get_stats()
        assert stats["total_entries"] == 4
        assert stats["available_files"] == 2
        assert stats["missing_files"] == 1
        assert stats["error_files"] == 1


# ---------------------------------------------------------------------------
# Auction Properties
# ---------------------------------------------------------------------------

SAMPLE_PROPERTY = {
    "property_id": "TEST_001",
    "cadastral_code": "H501",
    "region": "LAZIO",
    "province": "RM",
    "municipality": "ROMA",
    "property_type": "residential",
    "status": "active",
    "auction_date": "2024-03-15",
    "starting_price": 125000.0,
    "final_price": None,
    "description": "Test property",
    "latitude": 41.9,
    "longitude": 12.5
}


class TestAuctionProperties:

    def test_insert_auction_property(self, db):
        result = db.insert_auction_property(SAMPLE_PROPERTY)
        assert result is True

    def test_insert_duplicate_replaces(self, db):
        db.insert_auction_property(SAMPLE_PROPERTY)
        prop2 = {**SAMPLE_PROPERTY, "starting_price": 150000.0}
        result = db.insert_auction_property(prop2)
        assert result is True

    def test_get_auction_properties_empty(self, db):
        result = db.get_auction_properties()
        assert result == []

    def test_get_auction_properties_after_insert(self, db):
        db.insert_auction_property(SAMPLE_PROPERTY)
        result = db.get_auction_properties()
        assert len(result) == 1
        assert result[0]["property_id"] == "TEST_001"

    def test_get_auction_properties_filter_status(self, db):
        db.insert_auction_property(SAMPLE_PROPERTY)
        sold = {**SAMPLE_PROPERTY, "property_id": "TEST_002", "status": "sold"}
        db.insert_auction_property(sold)

        result = db.get_auction_properties(filters={"status": "active"})
        assert len(result) == 1
        assert result[0]["status"] == "active"

    def test_get_auction_properties_filter_type(self, db):
        db.insert_auction_property(SAMPLE_PROPERTY)
        agri = {**SAMPLE_PROPERTY, "property_id": "TEST_003", "property_type": "agricultural"}
        db.insert_auction_property(agri)

        result = db.get_auction_properties(filters={"property_type": "agricultural"})
        assert len(result) == 1
        assert result[0]["property_type"] == "agricultural"

    def test_get_auction_properties_filter_max_price(self, db):
        db.insert_auction_property(SAMPLE_PROPERTY)
        expensive = {**SAMPLE_PROPERTY, "property_id": "TEST_004", "starting_price": 500000.0}
        db.insert_auction_property(expensive)

        result = db.get_auction_properties(filters={"max_price": 200000.0})
        assert len(result) == 1
        assert result[0]["property_id"] == "TEST_001"

    def test_get_auction_properties_filter_cadastral_code(self, db):
        db.insert_auction_property(SAMPLE_PROPERTY)
        other = {**SAMPLE_PROPERTY, "property_id": "TEST_005", "cadastral_code": "A001"}
        db.insert_auction_property(other)

        result = db.get_auction_properties(filters={"cadastral_code": "H501"})
        assert all(r["cadastral_code"] == "H501" for r in result)

    def test_populate_dummy_auction_data(self, db):
        count = db.populate_dummy_auction_data()
        assert count == 6
        all_props = db.get_auction_properties()
        assert len(all_props) == 6

    def test_get_auction_statistics_empty(self, db):
        stats = db.get_auction_statistics()
        assert stats["total_properties"] == 0

    def test_get_auction_statistics_with_data(self, db):
        db.populate_dummy_auction_data()
        stats = db.get_auction_statistics()
        assert stats["total_properties"] == 6
        assert "status_counts" in stats
        assert "type_counts" in stats
        assert "avg_starting_price" in stats
        assert stats["avg_starting_price"] is not None

    def test_get_auction_statistics_avg_final_price(self, db):
        db.insert_auction_property({**SAMPLE_PROPERTY, "final_price": 150000.0})
        stats = db.get_auction_statistics()
        assert stats["avg_final_price"] == pytest.approx(150000.0)


# ---------------------------------------------------------------------------
# close_connection
# ---------------------------------------------------------------------------

class TestCloseConnection:

    def test_close_is_noop(self, db):
        """close_connection doesn't raise."""
        db.close_connection()
