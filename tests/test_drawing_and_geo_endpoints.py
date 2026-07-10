"""
Tests for drawing management, public geo data, and auction endpoints.

Covers:
- POST /api/v1/save-drawn-polygons (auth-required save)
- GET /api/v1/load-drawn-polygons (auth-required load)
- GET /api/v1/list-drawn-polygons
- DELETE /api/v1/clear-drawn-polygons
- POST /api/v1/load-public-geo-data/
- GET /api/v1/load-example-geo-data/
- POST /api/v1/load-cadastral-file/  (S3-backed)
- GET /api/v1/auction-properties/statistics/
- POST /api/v1/auction-properties/populate/
"""

import json
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import geopandas as gpd
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import Polygon

from land_registry.main import app
from land_registry.routers.auth import (
    get_current_user_optional as _get_current_user_optional,
)

MOCK_USER = MagicMock(id="draw-user-123", email="draw@example.com")

SAMPLE_GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[12.0, 41.0], [13.0, 41.0], [13.0, 42.0], [12.0, 42.0], [12.0, 41.0]]]
            },
            "properties": {"name": "test"}
        }
    ]
}


@pytest.fixture
def client():
    return TestClient(app, follow_redirects=True)


@pytest.fixture
def authed_client():
    app.dependency_overrides[_get_current_user_optional] = lambda: MOCK_USER
    yield TestClient(app, follow_redirects=True)
    app.dependency_overrides.pop(_get_current_user_optional, None)


# ---------------------------------------------------------------------------
# save-drawn-polygons (authenticated)
# ---------------------------------------------------------------------------

class TestSaveDrawnPolygons:

    def test_save_unauthenticated(self, client):
        """Returns 401 when not authenticated."""
        response = client.post("/api/v1/save-drawn-polygons", json={
            "geojson": SAMPLE_GEOJSON,
            "timestamp": "2026-04-05T10:00:00"
        })
        assert response.status_code == 401

    def test_save_success(self, authed_client, tmp_path):
        """Saves drawing file and returns success."""
        user_dir = tmp_path / "draw-user-123"

        with patch("land_registry.routers.api.get_user_directory", return_value=user_dir):
            response = authed_client.post("/api/v1/save-drawn-polygons", json={
                "geojson": SAMPLE_GEOJSON,
                "timestamp": "2026-04-05T10:00:00"
            })

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "filename" in data
        # Verify file was written
        assert user_dir.exists()
        assert any(f.name.startswith("drawings_") for f in user_dir.iterdir())


# ---------------------------------------------------------------------------
# load-drawn-polygons (authenticated)
# ---------------------------------------------------------------------------

class TestLoadDrawnPolygons:

    def test_load_unauthenticated(self, client):
        """Returns 401 when not authenticated."""
        response = client.get("/api/v1/load-drawn-polygons")
        assert response.status_code == 401

    def test_load_no_file(self, authed_client, tmp_path):
        """Returns success=False when no latest.geojson."""
        user_dir = tmp_path / "draw-user-123"
        user_dir.mkdir()

        with patch("land_registry.routers.api.get_user_directory", return_value=user_dir):
            response = authed_client.get("/api/v1/load-drawn-polygons")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False

    def test_load_with_file(self, authed_client, tmp_path):
        """Returns GeoJSON when latest.geojson exists."""
        user_dir = tmp_path / "draw-user-123"
        user_dir.mkdir()

        saved = {**SAMPLE_GEOJSON, "metadata": {"user_id": "draw-user-123"}}
        (user_dir / "latest.geojson").write_text(json.dumps(saved))

        with patch("land_registry.routers.api.get_user_directory", return_value=user_dir):
            response = authed_client.get("/api/v1/load-drawn-polygons")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "geojson" in data
        # metadata should be stripped
        assert "metadata" not in data["geojson"]


# ---------------------------------------------------------------------------
# list-drawn-polygons
# ---------------------------------------------------------------------------

class TestListDrawnPolygons:

    def test_list_no_directory(self, client, tmp_path, monkeypatch):
        """Returns empty list when drawn_polygons dir doesn't exist."""
        monkeypatch.chdir(tmp_path)
        response = client.get("/api/v1/list-drawn-polygons")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["files"] == []

    def test_list_with_files(self, client, tmp_path, monkeypatch):
        """Returns file list when drawings exist."""
        monkeypatch.chdir(tmp_path)
        drawings_dir = tmp_path / "drawn_polygons"
        drawings_dir.mkdir()

        content = json.dumps(SAMPLE_GEOJSON)
        (drawings_dir / "drawings_001.geojson").write_text(content)

        response = client.get("/api/v1/list-drawn-polygons")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert len(data["files"]) == 1
        assert data["files"][0]["filename"] == "drawings_001.geojson"
        assert data["files"][0]["feature_count"] == 1


# ---------------------------------------------------------------------------
# clear-drawn-polygons
# ---------------------------------------------------------------------------

class TestClearDrawnPolygons:

    def test_clear_no_directory(self, client, tmp_path, monkeypatch):
        """Returns success when no drawings dir."""
        monkeypatch.chdir(tmp_path)
        response = client.delete("/api/v1/clear-drawn-polygons")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_clear_with_files(self, client, tmp_path, monkeypatch):
        """Deletes all .geojson files from drawn_polygons/."""
        monkeypatch.chdir(tmp_path)
        drawings_dir = tmp_path / "drawn_polygons"
        drawings_dir.mkdir()
        (drawings_dir / "drawings_001.geojson").write_text(json.dumps(SAMPLE_GEOJSON))
        (drawings_dir / "drawings_002.geojson").write_text(json.dumps(SAMPLE_GEOJSON))

        response = client.delete("/api/v1/clear-drawn-polygons")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "2" in data["message"]
        # Verify files are deleted
        assert list(drawings_dir.glob("*.geojson")) == []


# ---------------------------------------------------------------------------
# load-public-geo-data and load-example-geo-data
# ---------------------------------------------------------------------------

class TestPublicGeoDataEndpoints:

    def test_load_public_geo_data_success(self, client):
        """Returns GeoJSON when S3 access and file read succeed."""
        poly = Polygon([(15, 40), (16, 40), (16, 41), (15, 41), (15, 40)])
        mock_gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[poly], crs="EPSG:4326")

        mock_s3 = MagicMock()
        mock_body = BytesIO(b"fake gpkg")
        mock_s3.get_object.return_value = {"Body": mock_body, "ContentLength": 10}

        with patch("land_registry.routers.api.boto3.client", return_value=mock_s3):
            with patch("land_registry.routers.api.gpd.read_file", return_value=mock_gdf):
                response = client.post("/api/v1/load-public-geo-data/", json={
                    "s3_key": "ITALIA/LAZIO/RM/H501_ROMA/H501_ROMA_map.gpkg",
                    "layer": 0
                })

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["feature_count"] == 1
        assert "geojson" in data

    def test_load_public_geo_data_s3_error(self, client):
        """Returns 500 when S3 access fails."""
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = Exception("Access denied")

        with patch("land_registry.routers.api.boto3.client", return_value=mock_s3):
            response = client.post("/api/v1/load-public-geo-data/", json={
                "s3_key": "ITALIA/LAZIO/RM/H501_ROMA/H501_ROMA_map.gpkg",
                "layer": 0
            })

        assert response.status_code == 500

    def test_load_example_geo_data(self, client):
        """Example endpoint delegates to load_public_geo_data."""
        poly = Polygon([(15, 40), (16, 40), (16, 41), (15, 41), (15, 40)])
        mock_gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[poly], crs="EPSG:4326")

        mock_s3 = MagicMock()
        mock_body = BytesIO(b"fake gpkg")
        mock_s3.get_object.return_value = {"Body": mock_body, "ContentLength": 10}

        with patch("land_registry.routers.api.boto3.client", return_value=mock_s3):
            with patch("land_registry.routers.api.gpd.read_file", return_value=mock_gdf):
                response = client.get("/api/v1/load-example-geo-data/")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True


# ---------------------------------------------------------------------------
# Auction property endpoints
# ---------------------------------------------------------------------------

class TestAuctionEndpoints:

    def test_get_auction_statistics(self, client):
        """Returns statistics from file_availability_db."""
        with patch("land_registry.routers.api.file_availability_db") as mock_db:
            mock_db.get_auction_statistics.return_value = {
                "total": 100,
                "by_region": {"LAZIO": 50}
            }
            response = client.get("/api/v1/auction-properties/statistics/")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "statistics" in data

    def test_get_auction_statistics_error(self, client):
        """Returns 500 when db raises exception."""
        with patch("land_registry.routers.api.file_availability_db") as mock_db:
            mock_db.get_auction_statistics.side_effect = Exception("DB error")
            response = client.get("/api/v1/auction-properties/statistics/")

        assert response.status_code == 500

    def test_populate_auction_data(self, client):
        """Populates dummy auction data and returns count."""
        with patch("land_registry.routers.api.file_availability_db") as mock_db:
            mock_db.populate_dummy_auction_data.return_value = 42
            response = client.post("/api/v1/auction-properties/populate/")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["count"] == 42


# ---------------------------------------------------------------------------
# get_auction_properties (with mock db)
# ---------------------------------------------------------------------------

class TestGetAuctionProperties:

    def test_get_auction_properties_empty_then_populated(self, client):
        """Auto-populates when db is empty, then returns features."""
        fake_props = [
            {
                "id": 1, "property_id": "P001", "cadastral_code": "H501",
                "region": "LAZIO", "province": "RM", "municipality": "ROMA",
                "property_type": "residential", "status": "active",
                "auction_date": "2026-04-10", "starting_price": 100000,
                "final_price": None, "description": "Test",
                "latitude": 41.9, "longitude": 12.5
            }
        ]

        with patch("land_registry.routers.api.file_availability_db") as mock_db:
            mock_db.get_auction_properties.side_effect = [[], fake_props]
            mock_db.populate_dummy_auction_data.return_value = 1
            response = client.get("/api/v1/auction-properties/")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["count"] == 1
        assert data["geojson"]["type"] == "FeatureCollection"
        assert len(data["geojson"]["features"]) == 1

    def test_get_auction_properties_already_populated(self, client):
        """Returns existing features when db already has data."""
        fake_props = [
            {
                "id": 1, "property_id": "P001", "cadastral_code": "H501",
                "region": "LAZIO", "province": "RM", "municipality": "ROMA",
                "property_type": "commercial", "status": "sold",
                "auction_date": "2026-03-01", "starting_price": 200000,
                "final_price": 180000, "description": "Commercial",
                "latitude": 41.9, "longitude": 12.5
            }
        ]

        with patch("land_registry.routers.api.file_availability_db") as mock_db:
            mock_db.get_auction_properties.return_value = fake_props
            response = client.get("/api/v1/auction-properties/")

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1

    def test_get_auction_properties_none_after_populate(self, client):
        """Returns empty geojson when populate doesn't produce results."""
        with patch("land_registry.routers.api.file_availability_db") as mock_db:
            mock_db.get_auction_properties.return_value = []
            mock_db.populate_dummy_auction_data.return_value = 0
            response = client.get("/api/v1/auction-properties/")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["count"] == 0


# ---------------------------------------------------------------------------
# test-load-endpoint and test-s3-access
# ---------------------------------------------------------------------------

class TestDiagnosticEndpoints:

    def test_load_endpoint_path_param(self, client):
        """Returns received path and s3_key."""
        response = client.get("/api/v1/test-load-endpoint/ITALIA/LAZIO/test.gpkg")
        assert response.status_code == 200
        data = response.json()
        assert "received_path" in data
        assert "s3_key" in data
        assert "ITALIA/" in data["s3_key"]

    def test_load_endpoint_path_without_italia(self, client):
        """Prepends ITALIA/ when path doesn't start with it."""
        response = client.get("/api/v1/test-load-endpoint/LAZIO/test.gpkg")
        assert response.status_code == 200
        data = response.json()
        assert data["s3_key"].startswith("ITALIA/")

    def test_s3_access_raises_due_to_missing_attribute(self, client):
        """test_s3_access references s3_settings.use_public_bucket_fallback which
        doesn't exist on S3Settings — the endpoint raises AttributeError."""
        import pytest
        # TestClient re-raises server exceptions by default; use raise_server_exceptions=False
        no_raise_client = TestClient(app, raise_server_exceptions=False)
        response = no_raise_client.get("/api/v1/test-s3-access/")
        assert response.status_code == 500


# ---------------------------------------------------------------------------
# load-cadastral-files/{file_path:path} (S3-backed GET endpoint)
# ---------------------------------------------------------------------------

class TestLoadCadastralFileEndpoint:

    FILE_PATH = "ITALIA/LAZIO/RM/H501_ROMA/H501_ROMA_map.gpkg"

    def test_success(self, client):
        """Returns GeoJSON when S3 fetch and file read succeed."""
        poly = Polygon([(12, 41), (13, 41), (13, 42), (12, 42), (12, 41)])
        mock_gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[poly], crs="EPSG:4326")

        mock_s3 = MagicMock()
        mock_body = BytesIO(b"fake gpkg content")
        mock_s3.get_object.return_value = {
            "Body": mock_body,
            "ContentLength": len(b"fake gpkg content")
        }

        with patch("land_registry.routers.api.boto3.client", return_value=mock_s3):
            with patch("land_registry.routers.api.gpd.read_file", return_value=mock_gdf):
                response = client.get(f"/api/v1/load-cadastral-files/{self.FILE_PATH}")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["feature_count"] == 1

    def test_s3_error(self, client):
        """Returns 500 when S3 get_object fails."""
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = Exception("S3 error")

        with patch("land_registry.routers.api.boto3.client", return_value=mock_s3):
            response = client.get(f"/api/v1/load-cadastral-files/{self.FILE_PATH}")

        assert response.status_code == 500
