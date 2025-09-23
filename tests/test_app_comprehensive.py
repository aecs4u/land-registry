"""
Comprehensive tests for app.py to boost coverage significantly.
"""

import pytest
import tempfile
import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open
from fastapi.testclient import TestClient
from fastapi import UploadFile
import geopandas as gpd
from shapely.geometry import Polygon
import io

from land_registry.app import app
from land_registry.s3_storage import S3Storage, S3Settings


class TestAppComprehensive:
    """Comprehensive app endpoint tests for maximum coverage."""

    def test_root_endpoint_template_rendering(self):
        """Test root endpoint with proper template rendering."""
        client = TestClient(app)

        # Mock the template response to avoid file system dependencies
        with patch('land_registry.app.templates.TemplateResponse') as mock_template:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_template.return_value = mock_response

            with patch('land_registry.app.map_controls') as mock_controls:
                mock_controls.generate_html.return_value = "<div>Controls HTML</div>"
                mock_controls.generate_javascript.return_value = "var controls = {};"

                response = client.get("/")
                assert response.status_code == 200
                mock_template.assert_called_once()

    @patch('land_registry.app.get_s3_storage')
    def test_get_cadastral_structure_s3_success(self, mock_get_s3):
        """Test get cadastral structure from S3 success path."""
        client = TestClient(app)

        # Mock S3Storage
        mock_storage = MagicMock(spec=S3Storage)
        mock_storage.get_cadastral_structure.return_value = {
            "ABRUZZO": {"AQ": {"A018_ACCIANO": {"code": "A018", "name": "ACCIANO", "files": []}}}
        }
        mock_get_s3.return_value = mock_storage

        response = client.get("/api/v1/get-cadastral-structure/")
        assert response.status_code == 200
        data = response.json()
        assert "ABRUZZO" in data

    @patch('land_registry.app.get_s3_storage')
    @patch('builtins.open', mock_open(read_data='{"local": "data"}'))
    def test_get_cadastral_structure_s3_fallback(self, mock_get_s3):
        """Test get cadastral structure fallback to local file."""
        client = TestClient(app)

        # Mock S3Storage to return None (file not found)
        mock_storage = MagicMock(spec=S3Storage)
        mock_storage.get_cadastral_structure.return_value = None
        mock_get_s3.return_value = mock_storage

        response = client.get("/api/v1/get-cadastral-structure/")
        assert response.status_code == 200
        data = response.json()
        assert data == {"local": "data"}

    @patch('land_registry.app.get_s3_storage')
    @patch('builtins.open', side_effect=FileNotFoundError)
    def test_get_cadastral_structure_both_fail(self, mock_get_s3):
        """Test get cadastral structure when both S3 and local fail."""
        client = TestClient(app)

        mock_storage = MagicMock(spec=S3Storage)
        mock_storage.get_cadastral_structure.return_value = None
        mock_get_s3.return_value = mock_storage

        response = client.get("/api/v1/get-cadastral-structure/")
        assert response.status_code == 404

    @patch('builtins.open', side_effect=FileNotFoundError)
    def test_get_cadastral_structure_no_s3_no_local(self):
        """Test get cadastral structure when no S3 and no local file."""
        client = TestClient(app)

        response = client.get("/api/v1/get-cadastral-structure/")
        assert response.status_code == 404

    @patch('builtins.open', side_effect=json.JSONDecodeError("Invalid JSON", "", 0))
    def test_get_cadastral_structure_invalid_json(self):
        """Test get cadastral structure with invalid JSON."""
        client = TestClient(app)

        response = client.get("/api/v1/get-cadastral-structure/")
        assert response.status_code == 500

    def test_upload_qpkg_no_file(self):
        """Test upload QPKG endpoint with no file."""
        client = TestClient(app)

        response = client.post("/upload-qpkg/")
        assert response.status_code == 422

    def test_upload_qpkg_invalid_extension(self):
        """Test upload QPKG with invalid file extension."""
        client = TestClient(app)

        # Create a fake file with wrong extension
        file_content = b"fake file content"
        files = {"file": ("test.txt", io.BytesIO(file_content), "text/plain")}

        response = client.post("/upload-qpkg/", files=files)
        assert response.status_code == 400
        assert "Invalid file type" in response.json()["detail"]

    @patch('land_registry.app.extract_qpkg_data')
    def test_upload_qpkg_extraction_failure(self, mock_extract):
        """Test upload QPKG when extraction fails."""
        client = TestClient(app)

        mock_extract.return_value = None

        file_content = b"fake gpkg content"
        files = {"file": ("test.gpkg", io.BytesIO(file_content), "application/octet-stream")}

        response = client.post("/upload-qpkg/", files=files)
        assert response.status_code == 400
        assert "No geospatial data found" in response.json()["detail"]

    @patch('land_registry.app.extract_qpkg_data')
    def test_upload_qpkg_success(self, mock_extract):
        """Test successful QPKG upload."""
        client = TestClient(app)

        mock_extract.return_value = '{"type": "FeatureCollection", "features": []}'

        file_content = b"fake gpkg content"
        files = {"file": ("test.gpkg", io.BytesIO(file_content), "application/octet-stream")}

        response = client.post("/upload-qpkg/", files=files)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "geojson" in data

    @patch('land_registry.app.get_current_gdf')
    def test_get_adjacent_polygons_success(self, mock_get_gdf):
        """Test get adjacent polygons success."""
        client = TestClient(app)

        # Create sample GeoDataFrame
        polygon1 = Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])
        polygon2 = Polygon([(1, 0), (2, 0), (2, 1), (1, 1), (1, 0)])
        gdf = gpd.GeoDataFrame({
            'feature_id': [0, 1],
            'name': ['Polygon 1', 'Polygon 2']
        }, geometry=[polygon1, polygon2])

        mock_get_gdf.return_value = gdf

        with patch('land_registry.app.find_adjacent_polygons') as mock_find:
            mock_find.return_value = [1]

            response = client.post("/api/v1/get-adjacent-polygons/", json={
                "feature_id": 0,
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]
                },
                "touch_method": "touches"
            })

            assert response.status_code == 200
            data = response.json()
            assert data["selected_id"] == 0
            assert data["adjacent_ids"] == [1]

    @patch('land_registry.app.get_current_gdf')
    def test_get_adjacent_polygons_no_data(self, mock_get_gdf):
        """Test get adjacent polygons with no data loaded."""
        client = TestClient(app)

        mock_get_gdf.return_value = None

        response = client.post("/api/v1/get-adjacent-polygons/", json={
            "feature_id": 0,
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]
            },
            "touch_method": "touches"
        })

        assert response.status_code == 400
        assert "No data loaded" in response.json()["detail"]

    @patch('land_registry.app.get_current_gdf')
    def test_get_attributes_success(self, mock_get_gdf):
        """Test get attributes success."""
        client = TestClient(app)

        # Create sample GeoDataFrame with attributes
        gdf = gpd.GeoDataFrame({
            'id': [1, 2],
            'name': ['Feature 1', 'Feature 2'],
            'area': [100.5, 200.7]
        }, geometry=[
            Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]),
            Polygon([(1, 0), (2, 0), (2, 1), (1, 1), (1, 0)])
        ])

        mock_get_gdf.return_value = gdf

        response = client.get("/api/v1/get-attributes/")
        assert response.status_code == 200
        data = response.json()
        assert "columns" in data
        assert "data" in data
        assert "id" in data["columns"]
        assert "name" in data["columns"]
        assert "area" in data["columns"]

    @patch('land_registry.app.get_current_gdf')
    def test_get_attributes_no_data(self, mock_get_gdf):
        """Test get attributes with no data loaded."""
        client = TestClient(app)

        mock_get_gdf.return_value = None

        response = client.get("/api/v1/get-attributes/")
        assert response.status_code == 400
        assert "No data loaded" in response.json()["detail"]

    @patch('builtins.open', mock_open())
    @patch('os.makedirs')
    @patch('json.dump')
    def test_save_drawn_polygons_success(self, mock_json_dump, mock_makedirs):
        """Test save drawn polygons success."""
        client = TestClient(app)

        polygons = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]
                },
                "properties": {"name": "Test Polygon"}
            }]
        }

        response = client.post("/api/v1/save-drawn-polygons/", json={"polygons": polygons})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "filename" in data

    def test_save_drawn_polygons_invalid_input(self):
        """Test save drawn polygons with invalid input."""
        client = TestClient(app)

        response = client.post("/api/v1/save-drawn-polygons/", json={})
        assert response.status_code == 422

    @patch('land_registry.app.get_s3_storage')
    def test_load_cadastral_files_s3_success(self, mock_get_s3):
        """Test load cadastral files from S3 success."""
        client = TestClient(app)

        mock_storage = MagicMock(spec=S3Storage)
        mock_storage.read_geospatial_file.return_value = '{"type": "FeatureCollection", "features": []}'
        mock_get_s3.return_value = mock_storage

        response = client.post("/api/v1/load-cadastral-files/", json={
            "files": ["ABRUZZO/AQ/A018_ACCIANO/A018_map.gpkg"]
        })

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "geojson" in data

    @patch('land_registry.app.get_s3_storage')
    def test_load_cadastral_files_s3_no_valid_files(self, mock_get_s3):
        """Test load cadastral files from S3 with no valid files."""
        client = TestClient(app)

        mock_storage = MagicMock(spec=S3Storage)
        mock_storage.read_geospatial_file.return_value = None
        mock_get_s3.return_value = mock_storage

        response = client.post("/api/v1/load-cadastral-files/", json={
            "files": ["ABRUZZO/AQ/A018_ACCIANO/A018_map.gpkg"]
        })

        assert response.status_code == 400
        assert "No valid geospatial data found" in response.json()["detail"]

    def test_load_cadastral_files_no_files(self):
        """Test load cadastral files with empty file list."""
        client = TestClient(app)

        response = client.post("/api/v1/load-cadastral-files/", json={"files": []})
        assert response.status_code == 400
        assert "No files specified" in response.json()["detail"]

    @patch('land_registry.app.configure_s3_storage')
    def test_configure_s3_success(self, mock_configure):
        """Test S3 configuration success."""
        client = TestClient(app)

        mock_storage = MagicMock(spec=S3Storage)
        mock_storage.list_files.return_value = ["file1.gpkg", "file2.gpkg"]
        mock_configure.return_value = mock_storage

        response = client.post("/api/v1/configure-s3/", json={
            "bucket_name": "test-bucket",
            "region": "us-east-1",
            "endpoint_url": "https://s3.amazonaws.com",
            "aws_access_key_id": "test-key",
            "aws_secret_access_key": "test-secret"
        })

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["bucket_name"] == "test-bucket"
        assert data["file_count"] == 2

    @patch('land_registry.app.configure_s3_storage')
    def test_configure_s3_connection_failure(self, mock_configure):
        """Test S3 configuration with connection failure."""
        client = TestClient(app)

        mock_configure.side_effect = Exception("Connection failed")

        response = client.post("/api/v1/configure-s3/", json={
            "bucket_name": "test-bucket",
            "region": "us-east-1"
        })

        assert response.status_code == 400
        assert "Failed to configure S3" in response.json()["detail"]

    @patch('land_registry.app.get_s3_storage')
    def test_s3_status_configured(self, mock_get_s3):
        """Test S3 status when configured."""
        client = TestClient(app)

        mock_storage = MagicMock(spec=S3Storage)
        mock_storage.settings.s3_bucket_name = "test-bucket"
        mock_storage.settings.s3_region = "us-east-1"
        mock_storage.settings.s3_endpoint_url = "https://s3.amazonaws.com"
        mock_storage.settings.aws_access_key_id = "test-key"
        mock_storage.settings.aws_secret_access_key = "test-secret"
        mock_storage.list_files.return_value = ["file1.gpkg"]
        mock_get_s3.return_value = mock_storage

        response = client.get("/api/v1/s3-status/")
        assert response.status_code == 200
        data = response.json()
        assert data["configured"] is True
        assert data["bucket_name"] == "test-bucket"
        assert data["has_credentials"] is True
        assert data["file_count"] == 1

    def test_s3_status_not_configured(self):
        """Test S3 status when not configured."""
        client = TestClient(app)

        response = client.get("/api/v1/s3-status/")
        assert response.status_code == 200
        data = response.json()
        assert data["configured"] is False

    @patch('land_registry.app.get_s3_storage')
    def test_s3_status_connection_error(self, mock_get_s3):
        """Test S3 status with connection error."""
        client = TestClient(app)

        mock_storage = MagicMock(spec=S3Storage)
        mock_storage.settings.s3_bucket_name = "test-bucket"
        mock_storage.settings.s3_region = "us-east-1"
        mock_storage.settings.s3_endpoint_url = None
        mock_storage.settings.aws_access_key_id = "test-key"
        mock_storage.settings.aws_secret_access_key = "test-secret"
        mock_storage.list_files.side_effect = Exception("Connection error")
        mock_get_s3.return_value = mock_storage

        response = client.get("/api/v1/s3-status/")
        assert response.status_code == 200
        data = response.json()
        assert data["configured"] is True
        assert data["connection_error"] is True

    @patch('land_registry.app.extract_qpkg_data')
    @patch('land_registry.app.generate_folium_map')
    def test_generate_map_success(self, mock_generate_map, mock_extract):
        """Test generate map success."""
        client = TestClient(app)

        mock_extract.return_value = '{"type": "FeatureCollection", "features": []}'
        mock_generate_map.return_value = "<html>Map HTML</html>"

        file_content = b"fake gpkg content"
        files = {"file": ("test.gpkg", io.BytesIO(file_content), "application/octet-stream")}

        response = client.post("/api/v1/generate-map/", files=files)
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/html; charset=utf-8"

    @patch('land_registry.app.extract_qpkg_data')
    def test_generate_map_invalid_file(self, mock_extract):
        """Test generate map with invalid file."""
        client = TestClient(app)

        file_content = b"fake content"
        files = {"file": ("test.txt", io.BytesIO(file_content), "text/plain")}

        response = client.post("/api/v1/generate-map/", files=files)
        assert response.status_code == 400

    @patch('land_registry.app.extract_qpkg_data')
    def test_generate_map_no_data(self, mock_extract):
        """Test generate map with no geospatial data."""
        client = TestClient(app)

        mock_extract.return_value = None

        file_content = b"fake gpkg content"
        files = {"file": ("test.gpkg", io.BytesIO(file_content), "application/octet-stream")}

        response = client.post("/api/v1/generate-map/", files=files)
        assert response.status_code == 400

    def test_cadastral_data_html_endpoint(self):
        """Test cadastral data HTML endpoint."""
        client = TestClient(app)

        response = client.get("/cadastral-data.html")
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/html; charset=utf-8"


class TestAppErrorHandling:
    """Test error handling scenarios in app endpoints."""

    def test_invalid_adjacent_polygons_request(self):
        """Test adjacent polygons with invalid request format."""
        client = TestClient(app)

        response = client.post("/api/v1/get-adjacent-polygons/", json={
            "invalid": "request"
        })
        assert response.status_code == 422

    @patch('land_registry.app.get_current_gdf')
    def test_adjacent_polygons_feature_not_found(self, mock_get_gdf):
        """Test adjacent polygons when feature ID not found."""
        client = TestClient(app)

        gdf = gpd.GeoDataFrame({
            'feature_id': [0, 1],
            'name': ['Feature 0', 'Feature 1']
        }, geometry=[
            Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]),
            Polygon([(1, 0), (2, 0), (2, 1), (1, 1), (1, 0)])
        ])

        mock_get_gdf.return_value = gdf

        response = client.post("/api/v1/get-adjacent-polygons/", json={
            "feature_id": 999,  # Non-existent feature ID
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]
            },
            "touch_method": "touches"
        })

        assert response.status_code == 400
        assert "Feature with ID 999 not found" in response.json()["detail"]

    @patch('builtins.open', side_effect=PermissionError("Permission denied"))
    def test_save_drawn_polygons_permission_error(self):
        """Test save drawn polygons with permission error."""
        client = TestClient(app)

        polygons = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]
                }
            }]
        }

        response = client.post("/api/v1/save-drawn-polygons/", json={"polygons": polygons})
        assert response.status_code == 500

    def test_invalid_s3_config_request(self):
        """Test S3 config with invalid request."""
        client = TestClient(app)

        response = client.post("/api/v1/configure-s3/", json={})
        assert response.status_code == 422