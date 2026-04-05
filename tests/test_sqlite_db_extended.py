"""
Additional tests for sqlite_db.py covering uncovered code paths.

Targets:
- update_zone with all optional fields (description, color, geojson, is_visible, tags, area_sqm, centroid_lat, centroid_lng)
- delete_zone without user_id
- get_microzones (all conditions None)
- get_microzone without user_id
- update_microzone with all optional fields
- delete_microzone without user_id
- get_drawn_polygon by id (with/without user_id)
- update_drawn_polygon
"""

import json
import pytest

from land_registry.sqlite_db import SQLiteDatabase


@pytest.fixture
def db(tmp_path):
    """Fresh SQLite db for each test."""
    return SQLiteDatabase(db_path=str(tmp_path / "test.db"))


def _create_zone(db: SQLiteDatabase, user_id: str = "user1", name: str = "Test Zone") -> int:
    return db.create_zone(
        user_id=user_id,
        name=name,
        description="A test zone",
        geojson={"type": "Feature", "geometry": None, "properties": {}},
        zone_type="polygon",
        color="#3388ff",
    )


def _create_microzone(db: SQLiteDatabase, zone_id: int, user_id: str = "user1", name: str = "mz") -> int:
    return db.create_microzone(
        zone_id=zone_id,
        geojson={"type": "Feature", "geometry": None, "properties": {}},
        user_id=user_id,
        name=name,
        microzone_type="polygon",
    )


# ---------------------------------------------------------------------------
# update_zone — optional fields
# ---------------------------------------------------------------------------

class TestUpdateZoneOptionalFields:

    def test_update_description(self, db):
        zone_id = _create_zone(db)
        result = db.update_zone(zone_id, "user1", description="New desc")
        assert result is True
        zone = db.get_zone(zone_id, user_id="user1")
        assert zone["description"] == "New desc"

    def test_update_color(self, db):
        zone_id = _create_zone(db)
        result = db.update_zone(zone_id, "user1", color="#ff0000")
        assert result is True
        zone = db.get_zone(zone_id, user_id="user1")
        assert zone["color"] == "#ff0000"

    def test_update_geojson(self, db):
        zone_id = _create_zone(db)
        new_geojson = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [12, 41]}, "properties": {}}
        result = db.update_zone(zone_id, "user1", geojson=new_geojson)
        assert result is True

    def test_update_is_visible(self, db):
        zone_id = _create_zone(db)
        result = db.update_zone(zone_id, "user1", is_visible=False)
        assert result is True
        zone = db.get_zone(zone_id, user_id="user1")
        assert zone["is_visible"] == 0

    def test_update_tags(self, db):
        zone_id = _create_zone(db)
        result = db.update_zone(zone_id, "user1", tags=["tag1", "tag2"])
        assert result is True

    def test_update_area_sqm(self, db):
        zone_id = _create_zone(db)
        result = db.update_zone(zone_id, "user1", area_sqm=5000.0)
        assert result is True

    def test_update_centroid(self, db):
        zone_id = _create_zone(db)
        result = db.update_zone(zone_id, "user1", centroid_lat=41.5, centroid_lng=12.5)
        assert result is True

    def test_update_no_fields_returns_false(self, db):
        zone_id = _create_zone(db)
        result = db.update_zone(zone_id, "user1")
        assert result is False

    def test_update_wrong_user_returns_false(self, db):
        zone_id = _create_zone(db, user_id="user1")
        result = db.update_zone(zone_id, "user2", name="Hacked")
        assert result is False


# ---------------------------------------------------------------------------
# delete_zone without user_id
# ---------------------------------------------------------------------------

class TestDeleteZoneWithoutUserId:

    def test_delete_zone_no_user_id(self, db):
        zone_id = _create_zone(db)
        result = db.delete_zone(zone_id)  # No user_id
        assert result is True
        zone = db.get_zone(zone_id, user_id="user1")
        assert zone is None


# ---------------------------------------------------------------------------
# get_microzones various paths
# ---------------------------------------------------------------------------

class TestGetMicrozones:

    def test_get_all_microzones_no_filter(self, db):
        zone_id = _create_zone(db)
        _create_microzone(db, zone_id)
        _create_microzone(db, zone_id, name="mz2")
        result = db.get_microzones()  # No zone_id or user_id
        assert len(result) == 2

    def test_get_microzones_by_zone_id_only(self, db):
        zone1 = _create_zone(db, user_id="u1", name="Z1")
        zone2 = _create_zone(db, user_id="u2", name="Z2")
        _create_microzone(db, zone1, user_id="u1")
        _create_microzone(db, zone2, user_id="u2")
        result = db.get_microzones(zone_id=zone1)
        assert len(result) == 1

    def test_get_microzones_by_user_id_only(self, db):
        zone_id = _create_zone(db)
        _create_microzone(db, zone_id, user_id="user1")
        _create_microzone(db, zone_id, user_id="user2")
        result = db.get_microzones(user_id="user1")
        assert len(result) == 1


# ---------------------------------------------------------------------------
# get_microzone without user_id
# ---------------------------------------------------------------------------

class TestGetMicrozoneNoUserId:

    def test_get_microzone_without_user_id(self, db):
        zone_id = _create_zone(db)
        mz_id = _create_microzone(db, zone_id)
        result = db.get_microzone(mz_id)  # No user_id
        assert result is not None
        assert result["id"] == mz_id

    def test_get_microzone_not_found_returns_none(self, db):
        result = db.get_microzone(9999)
        assert result is None


# ---------------------------------------------------------------------------
# update_microzone — optional fields
# ---------------------------------------------------------------------------

class TestUpdateMicrozone:

    def test_update_name(self, db):
        zone_id = _create_zone(db)
        mz_id = _create_microzone(db, zone_id)
        result = db.update_microzone(mz_id, "user1", name="New Name")
        assert result is True

    def test_update_description(self, db):
        zone_id = _create_zone(db)
        mz_id = _create_microzone(db, zone_id)
        result = db.update_microzone(mz_id, "user1", description="A description")
        assert result is True

    def test_update_color(self, db):
        zone_id = _create_zone(db)
        mz_id = _create_microzone(db, zone_id)
        result = db.update_microzone(mz_id, "user1", color="#ff0000")
        assert result is True

    def test_update_geojson(self, db):
        zone_id = _create_zone(db)
        mz_id = _create_microzone(db, zone_id)
        new_geojson = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [12, 41]}, "properties": {}}
        result = db.update_microzone(mz_id, "user1", geojson=new_geojson)
        assert result is True

    def test_update_is_visible(self, db):
        zone_id = _create_zone(db)
        mz_id = _create_microzone(db, zone_id)
        result = db.update_microzone(mz_id, "user1", is_visible=False)
        assert result is True

    def test_update_tags(self, db):
        zone_id = _create_zone(db)
        mz_id = _create_microzone(db, zone_id)
        result = db.update_microzone(mz_id, "user1", tags=["a", "b"])
        assert result is True

    def test_update_area_and_centroid(self, db):
        zone_id = _create_zone(db)
        mz_id = _create_microzone(db, zone_id)
        result = db.update_microzone(mz_id, "user1", area_sqm=100.0, centroid_lat=41.5, centroid_lng=12.5)
        assert result is True

    def test_update_no_fields_returns_false(self, db):
        zone_id = _create_zone(db)
        mz_id = _create_microzone(db, zone_id)
        result = db.update_microzone(mz_id, "user1")
        assert result is False

    def test_update_wrong_user_returns_false(self, db):
        zone_id = _create_zone(db)
        mz_id = _create_microzone(db, zone_id, user_id="user1")
        result = db.update_microzone(mz_id, "user2", name="Hacked")
        assert result is False


# ---------------------------------------------------------------------------
# delete_microzone without user_id
# ---------------------------------------------------------------------------

class TestDeleteMicrozoneWithoutUserId:

    def test_delete_microzone_no_user_id(self, db):
        zone_id = _create_zone(db)
        mz_id = _create_microzone(db, zone_id)
        result = db.delete_microzone(mz_id)  # No user_id
        assert result is True
        assert db.get_microzone(mz_id) is None


# ---------------------------------------------------------------------------
# get_drawn_polygon by ID (with and without user_id)
# ---------------------------------------------------------------------------

class TestGetDrawnPolygonById:

    def _create_polygon(self, db: SQLiteDatabase) -> int:
        return db.save_drawn_polygon(
            user_id="user1",
            name="Test Poly",
            geojson={"type": "Feature", "geometry": None, "properties": {}},
        )

    def test_get_polygon_with_user_id(self, db):
        poly_id = self._create_polygon(db)
        result = db.get_drawn_polygon(poly_id, user_id="user1")
        assert result is not None
        assert result["id"] == poly_id

    def test_get_polygon_without_user_id(self, db):
        poly_id = self._create_polygon(db)
        result = db.get_drawn_polygon(poly_id)  # No user_id
        assert result is not None
        assert result["id"] == poly_id

    def test_get_polygon_wrong_user_returns_none(self, db):
        poly_id = self._create_polygon(db)
        result = db.get_drawn_polygon(poly_id, user_id="other_user")
        assert result is None

    def test_get_nonexistent_polygon_returns_none(self, db):
        result = db.get_drawn_polygon(9999, user_id="user1")
        assert result is None


# ---------------------------------------------------------------------------
# update_drawn_polygon
# ---------------------------------------------------------------------------

class TestUpdateDrawnPolygon:

    def _create_polygon(self, db: SQLiteDatabase) -> int:
        return db.save_drawn_polygon(
            user_id="user1",
            name="Original",
            geojson={"type": "Feature", "geometry": None, "properties": {}},
        )

    def test_update_name(self, db):
        poly_id = self._create_polygon(db)
        result = db.update_drawn_polygon(poly_id, "user1", name="Updated")
        assert result is True

    def test_update_description(self, db):
        poly_id = self._create_polygon(db)
        result = db.update_drawn_polygon(poly_id, "user1", description="New description")
        assert result is True

    def test_update_color(self, db):
        poly_id = self._create_polygon(db)
        result = db.update_drawn_polygon(poly_id, "user1", color="#ff0000")
        assert result is True

    def test_update_geojson(self, db):
        poly_id = self._create_polygon(db)
        new_gj = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [12, 41]}, "properties": {}}
        result = db.update_drawn_polygon(poly_id, "user1", geojson=new_gj)
        assert result is True

    def test_update_is_visible(self, db):
        poly_id = self._create_polygon(db)
        result = db.update_drawn_polygon(poly_id, "user1", is_visible=False)
        assert result is True

    def test_update_tags(self, db):
        poly_id = self._create_polygon(db)
        result = db.update_drawn_polygon(poly_id, "user1", tags=["tag1"])
        assert result is True

    def test_update_area_and_centroid(self, db):
        poly_id = self._create_polygon(db)
        result = db.update_drawn_polygon(poly_id, "user1", area_sqm=500.0, centroid_lat=41.0, centroid_lng=12.5)
        assert result is True

    def test_update_no_fields_returns_false(self, db):
        poly_id = self._create_polygon(db)
        result = db.update_drawn_polygon(poly_id, "user1")
        assert result is False

    def test_update_wrong_user_returns_false(self, db):
        poly_id = self._create_polygon(db)
        result = db.update_drawn_polygon(poly_id, "other_user", name="Hacked")
        assert result is False


# ---------------------------------------------------------------------------
# get_zones / get_zone without user_id (lines 556, 567)
# ---------------------------------------------------------------------------

class TestGetZonesNoUserId:

    def test_get_zones_no_user_id(self, db):
        _create_zone(db, user_id="u1", name="Z1")
        _create_zone(db, user_id="u2", name="Z2")
        result = db.get_zones()  # No user_id filter
        assert len(result) == 2

    def test_get_zone_no_user_id(self, db):
        zone_id = _create_zone(db, user_id="user1")
        result = db.get_zone(zone_id)  # No user_id
        assert result is not None
        assert result["id"] == zone_id

    def test_get_zone_no_user_id_not_found(self, db):
        result = db.get_zone(9999)
        assert result is None


# ---------------------------------------------------------------------------
# delete_drawn_polygon with user_id (lines 533-539)
# ---------------------------------------------------------------------------

class TestDeleteDrawnPolygonWithUserId:

    def test_delete_with_user_id(self, db):
        poly_id = db.save_drawn_polygon(
            user_id="user1",
            name="Test Poly",
            geojson={"type": "Feature", "geometry": None, "properties": {}},
        )
        result = db.delete_drawn_polygon(poly_id, user_id="user1")
        assert result is True
        assert db.get_drawn_polygon(poly_id) is None

    def test_delete_with_wrong_user_id(self, db):
        poly_id = db.save_drawn_polygon(
            user_id="user1",
            name="Test Poly",
            geojson={"type": "Feature", "geometry": None, "properties": {}},
        )
        result = db.delete_drawn_polygon(poly_id, user_id="other_user")
        assert result is False
        # Polygon should still exist
        assert db.get_drawn_polygon(poly_id) is not None


# ---------------------------------------------------------------------------
# get_cached_files with filters (lines 909-920)
# ---------------------------------------------------------------------------

class TestGetCachedFiles:

    def test_get_cached_files_with_regione(self, db):
        db.cache_cadastral_file("ITALIA/LAZIO/RM/ROMA/file.gpkg", regione="LAZIO", file_type="map")
        db.cache_cadastral_file("ITALIA/LOMBARDIA/MI/MILANO/file.gpkg", regione="LOMBARDIA", file_type="map")
        result = db.get_cached_files(regione="LAZIO")
        assert len(result) == 1
        assert result[0]["regione"] == "LAZIO"

    def test_get_cached_files_with_provincia(self, db):
        db.cache_cadastral_file("ITALIA/LAZIO/RM/ROMA/file.gpkg", regione="LAZIO", provincia="RM")
        result = db.get_cached_files(provincia="RM")
        assert len(result) == 1

    def test_get_cached_files_with_comune(self, db):
        db.cache_cadastral_file("ITALIA/LAZIO/RM/H501/file.gpkg", regione="LAZIO", comune="H501")
        result = db.get_cached_files(comune="H501")
        assert len(result) == 1

    def test_get_cached_files_with_file_type(self, db):
        db.cache_cadastral_file("ITALIA/LAZIO/RM/ROMA/map.gpkg", file_type="map")
        db.cache_cadastral_file("ITALIA/LAZIO/RM/ROMA/ple.gpkg", file_type="ple")
        result = db.get_cached_files(file_type="map")
        assert len(result) == 1


# ---------------------------------------------------------------------------
# clear_expired_cache (lines 936-939)
# ---------------------------------------------------------------------------

class TestClearExpiredCache:

    def test_clear_expired_cache_no_entries(self, db):
        count = db.clear_expired_cache()
        assert count == 0

    def test_clear_expired_cache_returns_rowcount(self, db):
        # Cache a file - fresh entries won't expire immediately
        db.cache_cadastral_file("ITALIA/LAZIO/RM/ROMA/file.gpkg")
        # Unexpired entries should not be deleted
        count = db.clear_expired_cache()
        assert count == 0


# ---------------------------------------------------------------------------
# update_saved_map with various fields (lines 388-401)
# ---------------------------------------------------------------------------

class TestUpdateSavedMap:

    def _create_map(self, db: SQLiteDatabase) -> int:
        return db.save_map(
            user_id="user1",
            name="Test Map",
            map_config={"zoom": 6, "center": [41.9, 12.5]},
        )

    def test_update_name(self, db):
        map_id = self._create_map(db)
        db.update_saved_map(map_id, name="Updated Map")
        saved = db.get_saved_map(map_id)
        assert saved["name"] == "Updated Map"

    def test_update_description(self, db):
        map_id = self._create_map(db)
        db.update_saved_map(map_id, description="New description")
        saved = db.get_saved_map(map_id)
        assert saved["description"] == "New description"

    def test_update_map_config(self, db):
        map_id = self._create_map(db)
        new_config = {"zoom": 10, "center": [45.0, 9.0]}
        db.update_saved_map(map_id, map_config=new_config)
        saved = db.get_saved_map(map_id)
        import json
        config = json.loads(saved["map_config"]) if isinstance(saved["map_config"], str) else saved["map_config"]
        assert config["zoom"] == 10

    def test_update_layers(self, db):
        map_id = self._create_map(db)
        db.update_saved_map(map_id, layers=[{"name": "layer1"}])
        saved = db.get_saved_map(map_id)
        assert saved is not None

    def test_update_no_fields_is_noop(self, db):
        map_id = self._create_map(db)
        db.update_saved_map(map_id)  # No fields — branch condition at line 401 is False
        saved = db.get_saved_map(map_id)
        assert saved["name"] == "Test Map"  # Unchanged


# ---------------------------------------------------------------------------
# is_sqlite_available exception path (lines 987-989)
# ---------------------------------------------------------------------------

class TestIsSqliteAvailable:

    def test_exception_returns_false(self):
        from unittest.mock import patch, MagicMock
        from land_registry.sqlite_db import is_sqlite_available

        mock_db = MagicMock()
        mock_db.execute.side_effect = Exception("DB broken")

        with patch("land_registry.sqlite_db.get_sqlite_db", return_value=mock_db):
            result = is_sqlite_available()
        assert result is False
