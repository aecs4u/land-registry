"""
Corrected API endpoint tests that match actual application behavior.
These tests are written based on examining the actual routers/api.py implementation.
"""

import io
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
import geopandas as gpd
from shapely.geometry import Polygon

from land_registry.main import app


class TestCorrectedAPIEndpoints:
    """Tests that match actual API behavior."""

    def test_health_endpoint(self):
        """Test health endpoint - verified working."""
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data == {"status": "healthy", "service": "land-registry"}

    def test_upload_qpkg_invalid_extension_actual_response(self):
        """Test upload with invalid extension - check actual error message."""
        client = TestClient(app)

        file_content = b"fake content"
        files = {"file": ("test.txt", io.BytesIO(file_content), "text/plain")}

        response = client.post("/api/v1/upload-qpkg/", files=files)
        assert response.status_code == 400
        assert response.json()["detail"] == "File must be a QPKG or GPKG file"

    @patch('land_registry.routers.api.extract_qpkg_data')
    def test_upload_qpkg_no_data_found_actual_response(self, mock_extract):
        """Test upload when no geospatial data found - check actual error message."""
        client = TestClient(app)
        mock_extract.return_value = None  # This triggers the error

        file_content = b"fake gpkg content"
        files = {"file": ("test.gpkg", io.BytesIO(file_content), "application/octet-stream")}

        response = client.post("/api/v1/upload-qpkg/", files=files)
        assert response.status_code == 400
        assert response.json()["detail"] == "No geospatial data found in QPKG"

    @patch('land_registry.routers.api.extract_qpkg_data')
    @patch('land_registry.routers.api.get_current_gdf')
    def test_upload_qpkg_success_actual_response(self, mock_get_gdf, mock_extract):
        """Test successful upload - check actual response structure."""
        client = TestClient(app)

        # Mock successful extraction
        geojson_str = '{"type": "FeatureCollection", "features": []}'
        mock_extract.return_value = geojson_str

        # Mock current GDF
        mock_gdf = MagicMock()
        mock_gdf.columns = ['geometry']  # No feature_id column
        mock_get_gdf.return_value = mock_gdf

        file_content = b"fake gpkg content"
        files = {"file": ("test.gpkg", io.BytesIO(file_content), "application/octet-stream")}

        response = client.post("/api/v1/upload-qpkg/", files=files)
        assert response.status_code == 200
        data = response.json()
        assert "geojson" in data
        assert data["geojson"]["type"] == "FeatureCollection"

    @patch('land_registry.routers.api.get_current_gdf')
    def test_get_attributes_no_data_actual_response(self, mock_get_gdf):
        """Test get attributes with no data - check actual error message."""
        client = TestClient(app)
        mock_get_gdf.return_value = None

        response = client.get("/api/v1/get-attributes/")
        assert response.status_code == 400
        assert response.json()["detail"] == "No data loaded. Please upload a QPKG or GPKG file first."

    @patch('land_registry.routers.api.get_current_gdf')
    def test_get_attributes_success_actual_response(self, mock_get_gdf):
        """Test get attributes success - check actual response structure."""
        client = TestClient(app)

        # Create test GeoDataFrame
        polygon = Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])
        gdf = gpd.GeoDataFrame({
            'id': [1, 2],
            'name': ['Feature 1', 'Feature 2']
        }, geometry=[polygon, polygon])
        mock_get_gdf.return_value = gdf

        response = client.get("/api/v1/get-attributes/")
        assert response.status_code == 200
        data = response.json()
        assert "columns" in data
        assert "data" in data
        assert len(data["data"]) == 2

    @patch('land_registry.routers.api.get_current_gdf')
    def test_get_adjacent_polygons_no_data_actual_response(self, mock_get_gdf):
        """Test adjacent polygons with no data - check actual error message."""
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
        assert response.json()["detail"] == "No data loaded. Please upload a QPKG file first."

    @patch('land_registry.routers.api.get_current_gdf')
    @patch('land_registry.routers.api.find_adjacent_polygons')
    def test_get_adjacent_polygons_success_actual_response(self, mock_find_adjacent, mock_get_gdf):
        """Test adjacent polygons success - check actual response structure."""
        client = TestClient(app)

        # Create test GeoDataFrame with feature_id column
        polygon1 = Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])
        polygon2 = Polygon([(1, 0), (2, 0), (2, 1), (1, 1), (1, 0)])
        gdf = gpd.GeoDataFrame({
            'feature_id': [0, 1],
            'name': ['Feature 0', 'Feature 1']
        }, geometry=[polygon1, polygon2])
        mock_get_gdf.return_value = gdf
        mock_find_adjacent.return_value = [1]

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
        assert "geojson" in data
        assert "selected_id" in data
        assert "adjacent_ids" in data
        assert "total_count" in data
        assert data["selected_id"] == 0
        assert data["adjacent_ids"] == [1]

    # NOTE: Controls endpoints are currently disabled/commented out in routers/api.py
    # Uncomment these tests when the endpoints are re-enabled

    def test_load_cadastral_files_no_files_actual_response(self):
        """Test load cadastral files with no files - check actual error."""
        client = TestClient(app)

        # The actual endpoint expects 'file_paths' not 'files'
        response = client.post("/api/v1/load-cadastral-files/", json={"file_paths": []})
        assert response.status_code == 400
        assert response.json()["detail"] == "No file paths provided"


class TestCorrectedS3Endpoints:
    """Tests for S3 endpoints that match actual behavior."""

    @patch('land_registry.routers.api.configure_s3_storage')
    def test_configure_s3_success_actual_response(self, mock_configure):
        """Test S3 configuration success - check actual response structure."""
        client = TestClient(app)

        # Mock successful S3 configuration
        mock_storage = MagicMock()
        mock_storage.list_files.return_value = ["file1.gpkg", "file2.gpkg"]
        mock_configure.return_value = mock_storage

        response = client.post("/api/v1/configure-s3/", json={
            "bucket_name": "test-bucket",
            "region": "us-east-1"
        })

        assert response.status_code == 200
        data = response.json()
        assert "success" in data
        assert "message" in data
        assert "bucket_name" in data
        assert "region" in data
        assert "test_files_found" in data
        assert "sample_files" in data

    @patch('land_registry.routers.api.configure_s3_storage')
    def test_configure_s3_failure_actual_response(self, mock_configure):
        """Test S3 configuration failure - check actual error."""
        client = TestClient(app)
        mock_configure.side_effect = Exception("Connection failed")

        response = client.post("/api/v1/configure-s3/", json={
            "bucket_name": "test-bucket",
            "region": "us-east-1"
        })

        assert response.status_code == 500
        assert "Error configuring S3" in response.json()["detail"]

    @patch('land_registry.routers.api.get_s3_storage')
    def test_s3_status_configured_actual_response(self, mock_get_s3):
        """Test S3 status when configured - check actual response structure."""
        client = TestClient(app)

        # Mock S3 storage
        mock_storage = MagicMock()
        mock_storage.settings.s3_bucket_name = "test-bucket"
        mock_storage.settings.s3_region = "us-east-1"
        mock_storage.settings.s3_endpoint_url = None
        mock_storage.settings.aws_access_key_id = "test-key"
        mock_storage.settings.aws_secret_access_key = "test-secret"
        mock_storage.list_files.return_value = ["file1.gpkg"]
        mock_get_s3.return_value = mock_storage

        response = client.get("/api/v1/s3-status/")
        assert response.status_code == 200
        data = response.json()
        assert "bucket_name" in data
        assert "region" in data
        assert "endpoint_url" in data
        assert "has_credentials" in data
        assert "connection_status" in data
        assert "cadastral_files_found" in data

    def test_s3_status_not_configured_actual_response(self):
        """Test S3 status when not configured - check actual response."""
        client = TestClient(app)

        # When get_s3_storage raises an exception (not configured)
        with patch('land_registry.routers.api.get_s3_storage', side_effect=Exception("S3 not configured")):
            response = client.get("/api/v1/s3-status/")
            assert response.status_code == 500
            data = response.json()
            assert "Error checking S3 status" in data["detail"]


class TestCorrectedCadastralStructure:
    """Tests for cadastral structure endpoints with correct behavior."""

    @patch('land_registry.cadastral_utils.load_cadastral_structure')
    def test_get_cadastral_structure_local_success(self, mock_load):
        """Test getting cadastral structure successfully."""
        client = TestClient(app)

        # Mock the cadastral structure loader
        mock_cadastral = MagicMock()
        mock_cadastral.data = {"test": "structure"}
        mock_load.return_value = mock_cadastral

        response = client.get("/api/v1/get-cadastral-structure/")
        assert response.status_code == 200
        data = response.json()
        assert data == {"test": "structure"}

    @patch('land_registry.cadastral_utils.load_cadastral_structure')
    def test_get_cadastral_structure_file_not_found_actual_response(self, mock_load):
        """Test get cadastral structure when file not found - check actual error."""
        client = TestClient(app)

        # Return None to indicate data not found
        mock_load.return_value = None

        response = client.get("/api/v1/get-cadastral-structure/")
        assert response.status_code == 404
        assert "not available" in response.json()["detail"]

    @patch('land_registry.cadastral_utils.load_cadastral_structure')
    def test_get_cadastral_structure_invalid_json_actual_response(self, mock_load):
        """Test get cadastral structure with invalid JSON - check actual error."""
        client = TestClient(app)

        # Raise exception to simulate parsing error
        mock_load.side_effect = Exception("Error parsing")

        response = client.get("/api/v1/get-cadastral-structure/")
        assert response.status_code == 500
        assert "Error loading cadastral structure" in response.json()["detail"]
