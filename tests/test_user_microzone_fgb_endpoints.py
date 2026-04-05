"""
Tests for user profile, microzone CRUD, and FGB endpoints.
These cover previously untested sections of routers/api.py.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from land_registry.main import app
from land_registry.routers.auth import (
    get_current_user as _get_current_user,
    get_current_user_optional as _get_current_user_optional,
)

MOCK_USER = MagicMock(id="test-user-xyz", email="test@example.com")


def _fake_user():
    return MOCK_USER


def _fake_user_optional():
    return MOCK_USER


@pytest.fixture
def client():
    return TestClient(app, follow_redirects=True)


@pytest.fixture
def authed_client():
    """Client with auth dependencies overridden."""
    app.dependency_overrides[_get_current_user] = _fake_user
    app.dependency_overrides[_get_current_user_optional] = _fake_user_optional
    yield TestClient(app, follow_redirects=True)
    app.dependency_overrides.pop(_get_current_user, None)
    app.dependency_overrides.pop(_get_current_user_optional, None)


# ---------------------------------------------------------------------------
# User Profile Endpoints
# ---------------------------------------------------------------------------

class TestUserProfileEndpoints:
    """Tests for /user/profile and /user/drawings."""

    def test_profile_unauthenticated(self, client):
        """Unauthenticated request returns 401."""
        # get_current_user_optional returns None when no auth → 401 from handler
        response = client.get("/api/v1/user/profile")
        assert response.status_code == 401

    def test_profile_authenticated_no_files(self, authed_client, tmp_path, monkeypatch):
        """Profile returns empty stats when user dir doesn't exist."""
        # Point user directory to a non-existent sub-path
        fake_user_dir = tmp_path / "nonexistent_user"

        with patch("land_registry.routers.api.get_user_directory", return_value=fake_user_dir):
            response = authed_client.get("/api/v1/user/profile")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        profile = data["profile"]
        assert profile["drawing_sessions"] == 0
        assert profile["total_features"] == 0
        assert profile["latest_drawing"] is None

    def test_profile_authenticated_with_files(self, authed_client, tmp_path):
        """Profile returns correct stats when user has drawing files."""
        user_dir = tmp_path / "user_dir"
        user_dir.mkdir()

        # Create some fake drawing files
        (user_dir / "drawings_001.geojson").write_text('{"type":"FeatureCollection","features":[{"type":"Feature","geometry":null,"properties":{}}]}')
        (user_dir / "drawings_002.geojson").write_text('{"type":"FeatureCollection","features":[]}')

        # Create latest.geojson with metadata
        latest = {
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "geometry": None, "properties": {}}],
            "metadata": {"timestamp": "2026-04-01T10:00:00"}
        }
        (user_dir / "latest.geojson").write_text(json.dumps(latest))

        with patch("land_registry.routers.api.get_user_directory", return_value=user_dir):
            response = authed_client.get("/api/v1/user/profile")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        profile = data["profile"]
        assert profile["drawing_sessions"] == 2
        assert profile["total_features"] == 1
        assert profile["latest_drawing"] == "2026-04-01T10:00:00"

    def test_drawings_unauthenticated(self, client):
        """Unauthenticated request returns 401."""
        response = client.get("/api/v1/user/drawings")
        assert response.status_code == 401

    def test_drawings_authenticated_empty(self, authed_client, tmp_path):
        """Drawings returns empty list when user dir doesn't exist."""
        fake_user_dir = tmp_path / "nonexistent"

        with patch("land_registry.routers.api.get_user_directory", return_value=fake_user_dir):
            response = authed_client.get("/api/v1/user/drawings")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["drawings"] == []

    def test_drawings_authenticated_with_files(self, authed_client, tmp_path):
        """Drawings returns list of drawing files."""
        user_dir = tmp_path / "user_dir"
        user_dir.mkdir()

        content = '{"type":"FeatureCollection","features":[{"type":"Feature","geometry":null,"properties":{}}],"metadata":{"name":"test"}}'
        (user_dir / "drawings_abc.geojson").write_text(content)

        with patch("land_registry.routers.api.get_user_directory", return_value=user_dir):
            response = authed_client.get("/api/v1/user/drawings")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert len(data["drawings"]) == 1
        drawing = data["drawings"][0]
        assert drawing["filename"] == "drawings_abc.geojson"
        assert drawing["feature_count"] == 1


# ---------------------------------------------------------------------------
# FlatGeobuf Endpoints
# ---------------------------------------------------------------------------

class TestFGBEndpoints:
    """Tests for /fgb/regions, /fgb/metadata/{region}/{type}."""

    def _patch_fgb_dir(self, fgb_dir_str):
        """Return a context manager that patches spatialite_settings.fgb_directory."""
        return patch(
            "land_registry.config.spatialite_settings",
            new_callable=lambda: type("S", (), {"fgb_directory": fgb_dir_str, "__class__": type})
        )

    def test_list_fgb_regions_no_directory(self, client, tmp_path):
        """Returns empty regions list when FGB directory doesn't exist."""
        nonexistent = str(tmp_path / "no_fgb_dir")

        with patch("land_registry.config.spatialite_settings") as mock_settings:
            mock_settings.fgb_directory = nonexistent
            response = client.get("/api/v1/fgb/regions")

        assert response.status_code == 200
        data = response.json()
        assert data["regions"] == []

    def test_list_fgb_regions_empty_directory(self, client, tmp_path):
        """Returns empty regions list when FGB directory is empty."""
        fgb_dir = tmp_path / "fgb"
        fgb_dir.mkdir()

        with patch("land_registry.config.spatialite_settings") as mock_settings:
            mock_settings.fgb_directory = str(fgb_dir)
            response = client.get("/api/v1/fgb/regions")

        assert response.status_code == 200
        data = response.json()
        assert data["regions"] == []

    def test_list_fgb_regions_with_files(self, client, tmp_path):
        """Returns region list when FGB files exist."""
        fgb_dir = tmp_path / "fgb"
        fgb_dir.mkdir()

        # Create fake FGB files matching the pattern
        (fgb_dir / "cadastral_map.basilicata.fgb").write_bytes(b"fake fgb data")
        (fgb_dir / "cadastral_ple.basilicata.fgb").write_bytes(b"fake fgb data")
        (fgb_dir / "cadastral_map.lazio.fgb").write_bytes(b"fake fgb data")
        # Non-matching file should be ignored
        (fgb_dir / "other_file.fgb").write_bytes(b"ignored")

        with patch("land_registry.config.spatialite_settings") as mock_settings:
            mock_settings.fgb_directory = str(fgb_dir)
            response = client.get("/api/v1/fgb/regions")

        assert response.status_code == 200
        data = response.json()
        regions = data["regions"]
        assert len(regions) == 2  # basilicata and lazio
        region_slugs = {r["slug"] for r in regions}
        assert "basilicata" in region_slugs
        assert "lazio" in region_slugs
        # basilicata has both map and ple
        basilicata = next(r for r in regions if r["slug"] == "basilicata")
        assert basilicata["map_file"] == "cadastral_map.basilicata.fgb"
        assert basilicata["ple_file"] == "cadastral_ple.basilicata.fgb"

    def test_get_fgb_metadata_invalid_layer_type(self, client):
        """Returns 400 for invalid layer_type."""
        response = client.get("/api/v1/fgb/metadata/basilicata/invalid")
        assert response.status_code == 400
        assert "layer_type" in response.json()["detail"].lower()

    def test_get_fgb_metadata_file_not_found(self, client, tmp_path):
        """Returns 404 when FGB file doesn't exist."""
        fgb_dir = tmp_path / "fgb"
        fgb_dir.mkdir()

        with patch("land_registry.config.spatialite_settings") as mock_settings:
            mock_settings.fgb_directory = str(fgb_dir)
            response = client.get("/api/v1/fgb/metadata/basilicata/map")

        assert response.status_code == 404

    def test_get_fgb_metadata_success(self, client, tmp_path):
        """Returns metadata when FGB file exists."""
        fgb_dir = tmp_path / "fgb"
        fgb_dir.mkdir()
        fgb_file = fgb_dir / "cadastral_map.basilicata.fgb"
        fgb_file.write_bytes(b"fake fgb content")

        with patch("land_registry.config.spatialite_settings") as mock_settings:
            mock_settings.fgb_directory = str(fgb_dir)
            response = client.get("/api/v1/fgb/metadata/basilicata/map")

        assert response.status_code == 200
        data = response.json()
        assert data["filename"] == "cadastral_map.basilicata.fgb"
        assert data["layer_type"] == "map"
        assert data["size"] == len(b"fake fgb content")


# ---------------------------------------------------------------------------
# Microzone CRUD Endpoints
# ---------------------------------------------------------------------------

class TestMicrozoneEndpoints:
    """Tests for microzone CRUD under /zones/{zone_id}/microzones/."""

    VALID_ZONE = {
        "name": "Microzone Test Zone",
        "description": "Zone for microzone tests",
        "geojson": {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[12.0, 41.0], [13.0, 41.0], [13.0, 42.0], [12.0, 42.0], [12.0, 41.0]]]
            },
            "properties": {}
        },
        "polygon_type": "polygon",
        "color": "#0000ff",
        "tags": []
    }

    VALID_MICROZONE = {
        "name": "Test Microzone",
        "description": "A test microzone",
        "geojson": {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[12.1, 41.1], [12.5, 41.1], [12.5, 41.5], [12.1, 41.5], [12.1, 41.1]]]
            },
            "properties": {}
        },
        "microzone_type": "polygon",
        "color": "#ff0000",
        "tags": ["a", "b"]
    }

    def _create_zone(self, client) -> int:
        resp = client.post("/api/v1/zones/", json=self.VALID_ZONE)
        assert resp.status_code == 201
        return resp.json()["zone"]["id"]

    def test_list_microzones_zone_not_found(self, authed_client):
        response = authed_client.get("/api/v1/zones/99999/microzones/")
        assert response.status_code == 404

    def test_create_microzone_zone_not_found(self, authed_client):
        response = authed_client.post(
            "/api/v1/zones/99999/microzones/",
            json=self.VALID_MICROZONE
        )
        assert response.status_code == 404

    def test_create_and_list_microzones(self, authed_client):
        zone_id = self._create_zone(authed_client)

        # Initially empty
        list_resp = authed_client.get(f"/api/v1/zones/{zone_id}/microzones/")
        assert list_resp.status_code == 200
        assert list_resp.json()["microzones"] == []

        # Create microzone
        create_resp = authed_client.post(
            f"/api/v1/zones/{zone_id}/microzones/",
            json=self.VALID_MICROZONE
        )
        assert create_resp.status_code == 201
        data = create_resp.json()
        assert data["success"] is True
        assert "microzone" in data
        assert data["microzone"]["name"] == "Test Microzone"
        mz_id = data["microzone"]["id"]

        # Now list has one item
        list_resp2 = authed_client.get(f"/api/v1/zones/{zone_id}/microzones/")
        assert list_resp2.status_code == 200
        assert len(list_resp2.json()["microzones"]) == 1
        assert list_resp2.json()["total"] == 1

    def test_get_microzone_not_found(self, authed_client):
        zone_id = self._create_zone(authed_client)
        response = authed_client.get(f"/api/v1/zones/{zone_id}/microzones/99999")
        assert response.status_code == 404

    def test_get_microzone_success(self, authed_client):
        zone_id = self._create_zone(authed_client)

        create_resp = authed_client.post(
            f"/api/v1/zones/{zone_id}/microzones/",
            json=self.VALID_MICROZONE
        )
        mz_id = create_resp.json()["microzone"]["id"]

        get_resp = authed_client.get(f"/api/v1/zones/{zone_id}/microzones/{mz_id}")
        assert get_resp.status_code == 200
        data = get_resp.json()
        assert data["success"] is True
        assert data["microzone"]["id"] == mz_id

    def test_update_microzone_no_fields(self, authed_client):
        zone_id = self._create_zone(authed_client)
        create_resp = authed_client.post(
            f"/api/v1/zones/{zone_id}/microzones/",
            json=self.VALID_MICROZONE
        )
        mz_id = create_resp.json()["microzone"]["id"]

        # Update with no fields → 400
        patch_resp = authed_client.patch(
            f"/api/v1/zones/{zone_id}/microzones/{mz_id}",
            json={}
        )
        assert patch_resp.status_code == 400

    def test_update_microzone_success(self, authed_client):
        zone_id = self._create_zone(authed_client)
        create_resp = authed_client.post(
            f"/api/v1/zones/{zone_id}/microzones/",
            json=self.VALID_MICROZONE
        )
        mz_id = create_resp.json()["microzone"]["id"]

        patch_resp = authed_client.patch(
            f"/api/v1/zones/{zone_id}/microzones/{mz_id}",
            json={"name": "Updated Microzone", "color": "#00ff00"}
        )
        assert patch_resp.status_code == 200
        data = patch_resp.json()
        assert data["success"] is True
        assert data["microzone"]["name"] == "Updated Microzone"

    def test_delete_microzone_not_found(self, authed_client):
        zone_id = self._create_zone(authed_client)
        response = authed_client.delete(f"/api/v1/zones/{zone_id}/microzones/99999")
        assert response.status_code == 404

    def test_delete_microzone_success(self, authed_client):
        zone_id = self._create_zone(authed_client)
        create_resp = authed_client.post(
            f"/api/v1/zones/{zone_id}/microzones/",
            json=self.VALID_MICROZONE
        )
        mz_id = create_resp.json()["microzone"]["id"]

        del_resp = authed_client.delete(f"/api/v1/zones/{zone_id}/microzones/{mz_id}")
        assert del_resp.status_code == 200
        assert del_resp.json()["success"] is True

        # Verify gone
        get_resp = authed_client.get(f"/api/v1/zones/{zone_id}/microzones/{mz_id}")
        assert get_resp.status_code == 404

    def test_list_microzones_wrong_zone(self, authed_client):
        """Microzone from one zone should not be visible in another zone."""
        zone1_id = self._create_zone(authed_client)
        zone2_id = self._create_zone(authed_client)

        create_resp = authed_client.post(
            f"/api/v1/zones/{zone1_id}/microzones/",
            json=self.VALID_MICROZONE
        )
        mz_id = create_resp.json()["microzone"]["id"]

        # Should not appear in zone2
        list_resp = authed_client.get(f"/api/v1/zones/{zone2_id}/microzones/")
        assert list_resp.status_code == 200
        mz_ids = [m["id"] for m in list_resp.json()["microzones"]]
        assert mz_id not in mz_ids


# ---------------------------------------------------------------------------
# Bulk Microzone Visibility
# ---------------------------------------------------------------------------

class TestBulkVisibilityEndpoints:
    """Tests for /microzones/visibility endpoint."""

    def test_bulk_microzone_visibility_empty_zones(self, authed_client):
        """Empty zone_ids updates all microzones."""
        response = authed_client.post(
            "/api/v1/microzones/visibility",
            json={"is_visible": False}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "updated" in data

    def test_bulk_microzone_visibility_specific_zones(self, authed_client):
        """Specific zone_ids filters which microzones are updated."""
        response = authed_client.post(
            "/api/v1/microzones/visibility",
            json={"is_visible": True, "zone_ids": [1, 2, 3]}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
