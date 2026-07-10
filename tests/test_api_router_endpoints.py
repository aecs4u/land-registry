"""
Tests for untested API router endpoints in routers/api.py.

Covers:
- GET /api/v1/cadastral-cache-info
- GET /api/v1/get-regions/, /get-provinces/, /get-municipalities/
- GET/POST /api/v1/api/session/*
- POST /api/v1/save-drawn-polygons-anonymous/
- GET /api/v1/api/drawn-polygons, /api/drawn-polygons/{filename}
- Zones CRUD (with auth dep overridden)
"""

import json
import tempfile
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import geopandas as gpd
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import Polygon

from land_registry.main import app
from land_registry.routers.auth import get_current_user as _get_current_user


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_CADASTRAL = {
    "LOMBARDIA": {
        "BG": {
            "ALBANO_A151": {
                "name": "ALBANO SANT ALESSANDRO",
                "code": "A151",
                "files": ["MAP_ALBANO.gpkg"],
            }
        },
        "BS": {
            "BRESCIA_B157": {
                "name": "BRESCIA",
                "code": "B157",
                "files": ["MAP_BRESCIA.gpkg", "PLE_BRESCIA.gpkg"],
            }
        },
    },
    "VENETO": {
        "VE": {
            "VENEZIA_L736": {
                "name": "VENEZIA",
                "code": "L736",
                "files": [],
            }
        }
    },
}

MOCK_USER = MagicMock(id="test-user-123", email="test@example.com")


def _fake_user():
    return MOCK_USER


@pytest.fixture
def client():
    return TestClient(app, follow_redirects=True)


@pytest.fixture
def authed_client():
    """Client with get_current_user dependency overridden."""
    app.dependency_overrides[_get_current_user] = _fake_user
    yield TestClient(app, follow_redirects=True)
    app.dependency_overrides.pop(_get_current_user, None)


@pytest.fixture
def sample_gdf():
    polys = [Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
             Polygon([(1, 0), (2, 0), (2, 1), (1, 1)])]
    return gpd.GeoDataFrame(
        {"name": ["A", "B"], "value": [10, 20], "geometry": polys},
        crs="EPSG:4326",
    )


# ---------------------------------------------------------------------------
# /api/v1/cadastral-cache-info
# ---------------------------------------------------------------------------


class TestCadastralCacheInfo:
    @patch("land_registry.cadastral_utils.load_cadastral_structure")
    def test_returns_cache_metadata(self, mock_load, client):
        from land_registry.cadastral_utils import CadastralData, _calculate_statistics, clear_cache
        clear_cache()
        cd = CadastralData(SAMPLE_CADASTRAL, _calculate_statistics(SAMPLE_CADASTRAL), source="local")
        mock_load.return_value = cd

        response = client.get("/api/v1/cadastral-cache-info")
        assert response.status_code == 200
        data = response.json()
        assert "cache" in data
        assert "statistics" in data
        assert "file_availability" in data
        assert data["statistics"]["total_regions"] == 2
        assert data["cache"]["source"] == "local"
        assert "coverage_percentage" in data["file_availability"]

    @patch("land_registry.cadastral_utils.load_cadastral_structure")
    def test_returns_404_when_no_data(self, mock_load, client):
        from land_registry.cadastral_utils import clear_cache
        clear_cache()
        mock_load.return_value = None
        response = client.get("/api/v1/cadastral-cache-info")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# /api/v1/get-regions/, /get-provinces/, /get-municipalities/
# ---------------------------------------------------------------------------


class TestRegionsProvinciesMunicipalities:
    @pytest.fixture
    def cadastral_json_file(self, tmp_path):
        f = tmp_path / "cadastral_structure.json"
        f.write_text(json.dumps(SAMPLE_CADASTRAL))
        return str(f)

    def test_get_regions(self, client, cadastral_json_file):
        with patch("land_registry.routers.api.get_cadastral_structure_path",
                   return_value=cadastral_json_file):
            response = client.get("/api/v1/get-regions/")
        assert response.status_code == 200
        regions = response.json()["regions"]
        assert "LOMBARDIA" in regions
        assert "VENETO" in regions
        assert regions == sorted(regions)

    def test_get_regions_no_file(self, client):
        # HTTPException(404) raised inside try/except Exception gets swallowed → 500
        with patch("land_registry.routers.api.get_cadastral_structure_path",
                   return_value=None):
            response = client.get("/api/v1/get-regions/")
        assert response.status_code == 500

    def test_get_provinces_all(self, client, cadastral_json_file):
        with patch("land_registry.routers.api.get_cadastral_structure_path",
                   return_value=cadastral_json_file):
            response = client.get("/api/v1/get-provinces/")
        assert response.status_code == 200
        provinces = response.json()["provinces"]
        assert "BG" in provinces
        assert "BS" in provinces
        assert "VE" in provinces

    def test_get_provinces_filtered_by_region(self, client, cadastral_json_file):
        with patch("land_registry.routers.api.get_cadastral_structure_path",
                   return_value=cadastral_json_file):
            response = client.get("/api/v1/get-provinces/?regions=VENETO")
        assert response.status_code == 200
        provinces = response.json()["provinces"]
        assert provinces == ["VE"]

    def test_get_municipalities_all(self, client, cadastral_json_file):
        with patch("land_registry.routers.api.get_cadastral_structure_path",
                   return_value=cadastral_json_file):
            response = client.get("/api/v1/get-municipalities/")
        assert response.status_code == 200
        munis = response.json()["municipalities"]
        names = [m["name"] for m in munis]
        assert "BRESCIA" in names
        assert "VENEZIA" in names

    def test_get_municipalities_filtered_by_province(self, client, cadastral_json_file):
        with patch("land_registry.routers.api.get_cadastral_structure_path",
                   return_value=cadastral_json_file):
            response = client.get("/api/v1/get-municipalities/?regions=LOMBARDIA&provinces=BS")
        assert response.status_code == 200
        munis = response.json()["municipalities"]
        assert len(munis) == 1
        assert munis[0]["name"] == "BRESCIA"
        assert munis[0]["files_count"] == 2

    def test_get_municipalities_no_file(self, client):
        # HTTPException inside except Exception block → swallowed to 500
        with patch("land_registry.routers.api.get_cadastral_structure_path",
                   return_value=None):
            response = client.get("/api/v1/get-municipalities/")
        assert response.status_code == 500


# ---------------------------------------------------------------------------
# /api/v1/api/session/*
# ---------------------------------------------------------------------------


class TestSessionEndpoints:
    def test_current_data_empty(self, client):
        with patch("land_registry.routers.api.get_current_gdf", return_value=None):
            response = client.get("/api/v1/api/session/current-data")
        assert response.status_code == 200
        data = response.json()
        assert data["has_data"] is False
        assert data["feature_count"] == 0
        assert data["data"] is None

    def test_current_data_with_gdf(self, client, sample_gdf):
        with patch("land_registry.routers.api.get_current_gdf", return_value=sample_gdf):
            response = client.get("/api/v1/api/session/current-data")
        assert response.status_code == 200
        data = response.json()
        assert data["has_data"] is True
        assert data["feature_count"] == 2
        assert data["data"] is not None

    def test_session_info_empty(self, client):
        with patch("land_registry.routers.api.get_current_gdf", return_value=None):
            response = client.get("/api/v1/api/session/info")
        assert response.status_code == 200
        data = response.json()
        assert data["session_active"] is True
        assert data["has_data"] is False
        assert data["feature_count"] == 0

    def test_session_info_with_data(self, client, sample_gdf):
        with patch("land_registry.routers.api.get_current_gdf", return_value=sample_gdf):
            response = client.get("/api/v1/api/session/info")
        assert response.status_code == 200
        data = response.json()
        assert data["has_data"] is True
        assert data["feature_count"] == 2
        assert data["crs"] is not None
        assert "geometry" in data["columns"]
        assert data["bounds"] is not None

    def test_session_attributes_empty(self, client):
        with patch("land_registry.routers.api.get_current_gdf", return_value=None):
            response = client.get("/api/v1/api/session/attributes")
        assert response.status_code == 200
        data = response.json()
        assert data["total_features"] == 0
        assert data["attributes"] == {}

    def test_session_attributes_with_data(self, client, sample_gdf):
        with patch("land_registry.routers.api.get_current_gdf", return_value=sample_gdf):
            response = client.get("/api/v1/api/session/attributes")
        assert response.status_code == 200
        data = response.json()
        assert data["total_features"] == 2
        assert "name" in data["attributes"]
        assert "value" in data["attributes"]
        assert "geometry" not in data["attributes"]
        assert data["attributes"]["name"]["data_type"] is not None

    def test_session_clear(self, client):
        response = client.post("/api/v1/api/session/clear")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "cleared_items" in data


# ---------------------------------------------------------------------------
# /api/v1/save-drawn-polygons-anonymous/
# ---------------------------------------------------------------------------


class TestSaveDrawnPolygonsAnonymous:
    VALID_GEOJSON = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Polygon",
                             "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
                "properties": {"name": "test"}
            }
        ]
    }

    def test_save_success(self, client, tmp_path, monkeypatch):
        # Redirect drawn_polygons dir writes to tmp_path
        monkeypatch.chdir(tmp_path)
        response = client.post(
            "/api/v1/save-drawn-polygons-anonymous/",
            json={"geojson": self.VALID_GEOJSON, "filename": "test_polygons.json"}
        )
        assert response.status_code == 200
        data = response.json()
        # Response has: message, filename, filepath, feature_count (no "success" key)
        assert "filename" in data
        assert data["filename"].endswith(".json")
        assert data["feature_count"] == 1

    def test_too_many_features(self, client):
        many_features = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature",
                 "geometry": {"type": "Point", "coordinates": [0, 0]},
                 "properties": {}}
            ] * 101  # MAX_ANONYMOUS_FEATURES is 100
        }
        response = client.post(
            "/api/v1/save-drawn-polygons-anonymous/",
            json={"geojson": many_features, "filename": "test.json"}
        )
        assert response.status_code == 400
        assert "Too many features" in response.json()["detail"]

    def test_invalid_filename_extension(self, client):
        response = client.post(
            "/api/v1/save-drawn-polygons-anonymous/",
            json={"geojson": self.VALID_GEOJSON, "filename": "test.txt"}
        )
        assert response.status_code == 422  # Pydantic validator rejects .txt


# ---------------------------------------------------------------------------
# /api/v1/api/drawn-polygons
# ---------------------------------------------------------------------------


class TestDrawnPolygonsEndpoints:
    def test_get_drawn_polygons_no_dir(self, client):
        with patch("os.path.exists", return_value=False):
            response = client.get("/api/v1/api/drawn-polygons")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["drawings"] == []

    def test_get_specific_polygon_not_found(self, client):
        # HTTPException(404) raised inside try/except Exception is swallowed → 500
        with patch("os.path.exists", return_value=False):
            response = client.get("/api/v1/api/drawn-polygons/nonexistent.json")
        assert response.status_code == 500


# ---------------------------------------------------------------------------
# Zones CRUD (auth overridden)
# ---------------------------------------------------------------------------


class TestZonesEndpoints:
    VALID_ZONE = {
        "name": "Test Zone",
        "description": "A test zone",
        "geojson": {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[12.0, 41.0], [13.0, 41.0], [13.0, 42.0], [12.0, 42.0], [12.0, 41.0]]]
            },
            "properties": {}
        },
        "polygon_type": "polygon",
        "color": "#ff0000",
        "tags": ["test"]
    }

    def test_list_zones_empty(self, authed_client):
        response = authed_client.get("/api/v1/zones/")
        assert response.status_code == 200
        data = response.json()
        assert "zones" in data
        assert isinstance(data["zones"], list)

    def test_zones_geojson_empty(self, authed_client):
        response = authed_client.get("/api/v1/zones/geojson")
        assert response.status_code == 200
        data = response.json()
        assert data["type"] == "FeatureCollection"
        assert isinstance(data["features"], list)

    def test_create_zone(self, authed_client):
        response = authed_client.post("/api/v1/zones/", json=self.VALID_ZONE)
        assert response.status_code == 201
        data = response.json()
        assert data["success"] is True
        assert "zone" in data
        assert data["zone"]["name"] == "Test Zone"
        return data["zone"]["id"]

    def test_get_zone_not_found(self, authed_client):
        response = authed_client.get("/api/v1/zones/99999")
        assert response.status_code == 404

    def test_create_and_get_zone(self, authed_client):
        # Create
        create_resp = authed_client.post("/api/v1/zones/", json=self.VALID_ZONE)
        assert create_resp.status_code == 201
        zone_id = create_resp.json()["zone"]["id"]

        # Get
        get_resp = authed_client.get(f"/api/v1/zones/{zone_id}")
        assert get_resp.status_code == 200
        data = get_resp.json()
        assert data["zone"]["id"] == zone_id

    def test_create_and_delete_zone(self, authed_client):
        # Create
        create_resp = authed_client.post("/api/v1/zones/", json=self.VALID_ZONE)
        zone_id = create_resp.json()["zone"]["id"]

        # Delete
        del_resp = authed_client.delete(f"/api/v1/zones/{zone_id}")
        assert del_resp.status_code == 200

        # Verify gone
        get_resp = authed_client.get(f"/api/v1/zones/{zone_id}")
        assert get_resp.status_code == 404

    def test_create_and_update_zone(self, authed_client):
        create_resp = authed_client.post("/api/v1/zones/", json=self.VALID_ZONE)
        zone_id = create_resp.json()["zone"]["id"]

        patch_resp = authed_client.patch(
            f"/api/v1/zones/{zone_id}",
            json={"name": "Updated Zone", "color": "#00ff00"}
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["zone"]["name"] == "Updated Zone"

    def test_zones_geojson_with_data(self, authed_client):
        # Create a zone first
        authed_client.post("/api/v1/zones/", json=self.VALID_ZONE)

        response = authed_client.get("/api/v1/zones/geojson")
        assert response.status_code == 200
        data = response.json()
        assert data["type"] == "FeatureCollection"
        # Should have at least one feature from the created zone
        assert len(data["features"]) >= 1

    def test_zone_invalid_geojson(self, authed_client):
        invalid_zone = dict(self.VALID_ZONE)
        invalid_zone["geojson"] = {"type": "NotAFeature", "geometry": None}
        response = authed_client.post("/api/v1/zones/", json=invalid_zone)
        assert response.status_code == 422

    def test_update_zone_with_geojson(self, authed_client):
        """Updating geojson triggers area/centroid recalculation (covers lines 2349-2357)."""
        create_resp = authed_client.post("/api/v1/zones/", json=self.VALID_ZONE)
        zone_id = create_resp.json()["zone"]["id"]

        new_geojson = {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[11.0, 40.0], [12.0, 40.0], [12.0, 41.0], [11.0, 41.0], [11.0, 40.0]]]
            },
            "properties": {}
        }
        patch_resp = authed_client.patch(
            f"/api/v1/zones/{zone_id}",
            json={"geojson": new_geojson}
        )
        assert patch_resp.status_code == 200
        data = patch_resp.json()
        assert data["success"] is True


# ---------------------------------------------------------------------------
# Zones without auth → 503
# ---------------------------------------------------------------------------


class TestZonesRequireAuth:
    def test_list_zones_no_auth_returns_4xx_or_503(self, client):
        response = client.get("/api/v1/zones/")
        # 401 when aecs4u-auth is installed and no token provided;
        # 503 when auth is not configured at all
        assert response.status_code in (401, 403, 503)

    def test_create_zone_no_auth_returns_4xx_or_503(self, client):
        response = client.post("/api/v1/zones/", json={})
        assert response.status_code in (401, 403, 422, 503)
