import pytest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, mock_open, MagicMock
from fastapi.testclient import TestClient
import geopandas as gpd

from land_registry.app import app


class TestHealthEndpoint:
    """Tests for health check endpoint."""
    
    def test_health_check_success(self, client):
        """Test successful health check."""
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "healthy", "service": "land-registry"}


class TestRootEndpoint:
    """Tests for root endpoint."""
    
    def test_root_endpoint_returns_html(self, client):
        """Test root endpoint returns HTML."""
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Land Registry Viewer" in response.text


class TestCadastralStructureEndpoints:
    """Tests for cadastral structure endpoints."""
    
    @patch("land_registry.app.get_s3_storage")
    @patch("land_registry.app.os.path.exists")
    @patch("builtins.open", new_callable=mock_open)
    def test_get_cadastral_structure_success(self, mock_file, mock_exists, mock_get_storage, client, sample_cadastral_structure):
        """Test successful retrieval of cadastral structure."""
        # Mock S3 to return None, forcing fallback to local file
        mock_storage = MagicMock()
        mock_storage.get_cadastral_structure.return_value = None
        mock_get_storage.return_value = mock_storage

        mock_exists.return_value = True
        mock_file.return_value.read.return_value = json.dumps(sample_cadastral_structure)

        response = client.get("/api/v1/get-cadastral-structure/")
        assert response.status_code == 200
        assert response.json() == sample_cadastral_structure
    
    @patch("land_registry.app.get_s3_storage")
    @patch("land_registry.app.os.path.exists")
    def test_get_cadastral_structure_file_not_found(self, mock_exists, mock_get_storage, client):
        """Test cadastral structure endpoint when file not found."""
        # Mock S3 to return None, forcing fallback to local file
        mock_storage = MagicMock()
        mock_storage.get_cadastral_structure.return_value = None
        mock_get_storage.return_value = mock_storage

        mock_exists.return_value = False

        response = client.get("/api/v1/get-cadastral-structure/")
        assert response.status_code == 404
        assert "Cadastral structure file not found" in response.json()["detail"]
    
    @patch("land_registry.app.get_s3_storage")
    @patch("land_registry.app.os.path.exists")
    @patch("builtins.open", side_effect=json.JSONDecodeError("Invalid JSON", "", 0))
    def test_get_cadastral_structure_invalid_json(self, mock_file, mock_exists, mock_get_storage, client):
        """Test cadastral structure endpoint with invalid JSON."""
        # Mock S3 to return None, forcing fallback to local file
        mock_storage = MagicMock()
        mock_storage.get_cadastral_structure.return_value = None
        mock_get_storage.return_value = mock_storage

        mock_exists.return_value = True

        response = client.get("/api/v1/get-cadastral-structure/")
        assert response.status_code == 500
        assert "Error parsing cadastral structure file" in response.json()["detail"]
    
    @patch("land_registry.app.get_s3_storage")
    @patch("land_registry.app.os.path.exists")
    @patch("builtins.open", new_callable=mock_open)
    def test_cadastral_data_html_endpoint(self, mock_file, mock_exists, mock_get_storage, client, sample_cadastral_structure):
        """Test cadastral data HTML endpoint."""
        # Mock S3 to return None, forcing fallback to local file
        mock_storage = MagicMock()
        mock_storage.get_cadastral_structure.return_value = None
        mock_get_storage.return_value = mock_storage

        mock_exists.return_value = True
        mock_file.return_value.read.return_value = json.dumps(sample_cadastral_structure)

        response = client.get("/cadastral-data")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Italian Cadastral Data Structure" in response.text


class TestFileUploadEndpoints:
    """Tests for file upload endpoints."""
    
    @patch("land_registry.app.extract_qpkg_data")
    def test_upload_qpkg_success(self, mock_extract, client, sample_geojson):
        """Test successful QPKG file upload."""
        mock_extract.return_value = json.dumps(sample_geojson)
        
        with tempfile.NamedTemporaryFile(suffix='.qpkg') as temp_file:
            temp_file.write(b'fake qpkg content')
            temp_file.seek(0)
            
            response = client.post(
                "/api/v1/upload-qpkg/",
                files={"file": ("test.qpkg", temp_file, "application/octet-stream")}
            )
        
        assert response.status_code == 200
        assert "geojson" in response.json()
        mock_extract.assert_called_once()
    
    def test_upload_qpkg_invalid_file_type(self, client):
        """Test QPKG upload with invalid file type."""
        with tempfile.NamedTemporaryFile(suffix='.txt') as temp_file:
            temp_file.write(b'not a qpkg file')
            temp_file.seek(0)
            
            response = client.post(
                "/api/v1/upload-qpkg/",
                files={"file": ("test.txt", temp_file, "text/plain")}
            )
        
        assert response.status_code == 400
        assert "File must be a QPKG or GPKG file" in response.json()["detail"]
    
    @patch("land_registry.app.extract_qpkg_data")
    def test_upload_qpkg_no_geospatial_data(self, mock_extract, client):
        """Test QPKG upload when no geospatial data found."""
        mock_extract.return_value = None
        
        with tempfile.NamedTemporaryFile(suffix='.qpkg') as temp_file:
            temp_file.write(b'fake qpkg content')
            temp_file.seek(0)
            
            response = client.post(
                "/api/v1/upload-qpkg/",
                files={"file": ("test.qpkg", temp_file, "application/octet-stream")}
            )
        
        assert response.status_code == 400
        assert "No geospatial data found in QPKG" in response.json()["detail"]


class TestAdjacentPolygonsEndpoint:
    """Tests for adjacent polygons endpoint."""
    
    @patch("land_registry.app.get_current_gdf")
    @patch("land_registry.app.find_adjacent_polygons")
    def test_get_adjacent_polygons_success(self, mock_find_adjacent, mock_get_gdf, 
                                         client, sample_gdf, polygon_selection_data):
        """Test successful adjacent polygons retrieval."""
        mock_get_gdf.return_value = sample_gdf
        mock_find_adjacent.return_value = [1]  # Adjacent to feature 1
        
        response = client.post("/api/v1/get-adjacent-polygons/", json=polygon_selection_data)
        
        assert response.status_code == 200
        data = response.json()
        assert "geojson" in data
        assert data["selected_id"] == 0
        assert data["adjacent_ids"] == [1]
        assert data["total_count"] == 2
    
    @patch("land_registry.app.get_current_gdf")
    def test_get_adjacent_polygons_no_data_loaded(self, mock_get_gdf, client, polygon_selection_data):
        """Test adjacent polygons when no data loaded."""
        mock_get_gdf.return_value = None
        
        response = client.post("/api/v1/get-adjacent-polygons/", json=polygon_selection_data)
        
        assert response.status_code == 400
        assert "No data loaded" in response.json()["detail"]
    
    def test_get_adjacent_polygons_invalid_input(self, client):
        """Test adjacent polygons with invalid input."""
        invalid_data = {"feature_id": "invalid"}
        
        response = client.post("/api/v1/get-adjacent-polygons/", json=invalid_data)
        
        assert response.status_code == 422  # Validation error


class TestAttributesEndpoint:
    """Tests for attributes endpoint."""
    
    @patch("land_registry.app.get_current_gdf")
    def test_get_attributes_success(self, mock_get_gdf, client, sample_gdf):
        """Test successful attributes retrieval."""
        mock_get_gdf.return_value = sample_gdf
        
        response = client.get("/api/v1/get-attributes/")
        
        assert response.status_code == 200
        data = response.json()
        assert "columns" in data
        assert "data" in data
        assert "total_features" in data
        assert data["total_features"] == len(sample_gdf)
    
    @patch("land_registry.app.get_current_gdf")
    def test_get_attributes_no_data_loaded(self, mock_get_gdf, client):
        """Test attributes endpoint when no data loaded."""
        mock_get_gdf.return_value = None
        
        response = client.get("/api/v1/get-attributes/")
        
        assert response.status_code == 400
        assert "No data loaded" in response.json()["detail"]


class TestSaveDrawnPolygonsEndpoint:
    """Tests for save drawn polygons endpoint."""
    
    @patch("land_registry.app.Path.mkdir")
    @patch("builtins.open", new_callable=mock_open)
    def test_save_drawn_polygons_success(self, mock_file, mock_mkdir, client, drawn_polygons_data):
        """Test successful saving of drawn polygons."""
        response = client.post("/api/v1/save-drawn-polygons/", json=drawn_polygons_data)
        
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "filename" in data
        assert "filepath" in data
        assert data["feature_count"] == 1
    
    def test_save_drawn_polygons_invalid_input(self, client):
        """Test save drawn polygons with invalid input."""
        invalid_data = {"invalid": "data"}
        
        response = client.post("/api/v1/save-drawn-polygons/", json=invalid_data)
        
        assert response.status_code == 422  # Validation error


class TestControlsEndpoints:
    """Tests for controls endpoints."""
    
    def test_get_controls_success(self, client):
        """Test successful controls retrieval."""
        response = client.get("/api/v1/get-controls/")
        
        assert response.status_code == 200
        data = response.json()
        assert "groups" in data
        assert isinstance(data["groups"], list)
    
    def test_update_control_state_success(self, client):
        """Test successful control state update."""
        update_data = {"control_id": "test_control", "enabled": True}
        
        with patch("land_registry.app.map_controls.update_control_state", return_value=True):
            response = client.post("/api/v1/update-control-state/", json=update_data)
        
        assert response.status_code == 200
        assert response.json()["success"] is True
    
    def test_update_control_state_not_found(self, client):
        """Test control state update when control not found."""
        update_data = {"control_id": "nonexistent_control", "enabled": True}
        
        with patch("land_registry.app.map_controls.update_control_state", return_value=False):
            response = client.post("/api/v1/update-control-state/", json=update_data)
        
        assert response.status_code == 404
        assert "not found" in response.json()["detail"]


class TestGenerateMapEndpoint:
    """Tests for generate map endpoint."""
    
    @patch("land_registry.app.extract_qpkg_data")
    @patch("land_registry.app.folium.Map")
    def test_generate_map_success(self, mock_map, mock_extract, client, sample_geojson):
        """Test successful map generation."""
        mock_extract.return_value = json.dumps(sample_geojson)
        mock_map_instance = MagicMock()
        mock_map_instance._repr_html_.return_value = "<html>Generated Map</html>"
        mock_map.return_value = mock_map_instance
        
        with tempfile.NamedTemporaryFile(suffix='.qpkg') as temp_file:
            temp_file.write(b'fake qpkg content')
            temp_file.seek(0)
            
            response = client.post(
                "/api/v1/generate-map/",
                files={"file": ("test.qpkg", temp_file, "application/octet-stream")}
            )
        
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Generated Map" in response.text
    
    def test_generate_map_invalid_file_type(self, client):
        """Test generate map with invalid file type."""
        with tempfile.NamedTemporaryFile(suffix='.txt') as temp_file:
            temp_file.write(b'not a qpkg file')
            temp_file.seek(0)
            
            response = client.post(
                "/api/v1/generate-map/",
                files={"file": ("test.txt", temp_file, "text/plain")}
            )
        
        assert response.status_code == 400
        assert "File must be a QPKG or GPKG file" in response.json()["detail"]


class TestLoadCadastralFilesEndpoint:
    """Tests for load cadastral files endpoint."""
    
    def test_load_cadastral_files_no_files(self, client):
        """Test load cadastral files with no files specified."""
        request_data = {"files": []}
        
        response = client.post("/api/v1/load-cadastral-files/", json=request_data)
        
        assert response.status_code == 400
        assert "No files specified" in response.json()["detail"]
    
    def test_load_cadastral_files_invalid_input(self, client):
        """Test load cadastral files with invalid input."""
        invalid_data = {"invalid": "data"}
        
        response = client.post("/api/v1/load-cadastral-files/", json=invalid_data)

        assert response.status_code == 422  # Validation error


class TestS3Endpoints:
    """Tests for S3-related endpoints."""

    @patch("land_registry.app.configure_s3_storage")
    def test_configure_s3_success(self, mock_configure, client, s3_config_request):
        """Test successful S3 configuration."""
        # Mock S3Storage instance
        mock_storage = MagicMock()
        mock_storage.list_files.return_value = ["ITALIA/test1.shp", "ITALIA/test2.shp"]
        mock_configure.return_value = mock_storage

        response = client.post("/api/v1/configure-s3/", json=s3_config_request)

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["bucket_name"] == s3_config_request["bucket_name"]
        assert data["region"] == s3_config_request["region"]
        assert "test_files_found" in data

    @patch("land_registry.app.configure_s3_storage")
    def test_configure_s3_connection_test_failure(self, mock_configure, client, s3_config_request):
        """Test S3 configuration with connection test failure."""
        # Mock S3Storage instance that fails on list_files
        mock_storage = MagicMock()
        mock_storage.list_files.side_effect = Exception("Connection failed")
        mock_configure.return_value = mock_storage

        response = client.post("/api/v1/configure-s3/", json=s3_config_request)

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "connection test failed" in data["message"]
        assert "test_error" in data

    def test_configure_s3_invalid_input(self, client):
        """Test S3 configuration with invalid input."""
        invalid_data = {"bucket_name": ""}  # Invalid empty bucket name

        response = client.post("/api/v1/configure-s3/", json=invalid_data)

        assert response.status_code == 422  # Validation error

    @patch("land_registry.app.get_s3_storage")
    def test_s3_status_success(self, mock_get_storage, client):
        """Test successful S3 status retrieval."""
        # Mock S3Storage instance
        mock_storage = MagicMock()
        mock_storage.settings.s3_bucket_name = "test-bucket"
        mock_storage.settings.s3_region = "us-east-1"
        mock_storage.settings.s3_endpoint_url = None
        mock_storage.settings.aws_access_key_id = "test-key"
        mock_storage.settings.aws_secret_access_key = "test-secret"
        mock_storage.list_files.return_value = ["file1.shp", "file2.shp"]
        mock_get_storage.return_value = mock_storage

        response = client.get("/api/v1/s3-status/")

        assert response.status_code == 200
        data = response.json()
        assert data["bucket_name"] == "test-bucket"
        assert data["region"] == "us-east-1"
        assert data["has_credentials"] is True
        assert data["connection_status"] == "connected"
        assert data["cadastral_files_found"] == 2

    @patch("land_registry.app.get_s3_storage")
    def test_s3_status_connection_error(self, mock_get_storage, client):
        """Test S3 status with connection error."""
        # Mock S3Storage instance that fails on list_files
        mock_storage = MagicMock()
        mock_storage.settings.s3_bucket_name = "test-bucket"
        mock_storage.settings.s3_region = "us-east-1"
        mock_storage.settings.s3_endpoint_url = None
        mock_storage.settings.aws_access_key_id = None
        mock_storage.settings.aws_secret_access_key = None
        mock_storage.list_files.side_effect = Exception("Connection failed")
        mock_get_storage.return_value = mock_storage

        response = client.get("/api/v1/s3-status/")

        assert response.status_code == 200
        data = response.json()
        assert data["bucket_name"] == "test-bucket"
        assert data["has_credentials"] is False
        assert data["connection_status"] == "error"
        assert data["cadastral_files_found"] == 0


class TestS3IntegratedEndpoints:
    """Tests for endpoints that use S3 integration."""

    @patch("land_registry.app.get_s3_storage")
    def test_get_cadastral_structure_from_s3_success(self, mock_get_storage, client, sample_cadastral_structure):
        """Test cadastral structure retrieval from S3."""
        # Mock S3Storage instance
        mock_storage = MagicMock()
        mock_storage.get_cadastral_structure.return_value = sample_cadastral_structure
        mock_get_storage.return_value = mock_storage

        response = client.get("/api/v1/get-cadastral-structure/")

        assert response.status_code == 200
        assert response.json() == sample_cadastral_structure

    @patch("land_registry.app.get_s3_storage")
    @patch("land_registry.app.os.path.exists")
    @patch("builtins.open", new_callable=mock_open)
    def test_get_cadastral_structure_s3_fallback_to_local(self, mock_file, mock_exists,
                                                         mock_get_storage, client, sample_cadastral_structure):
        """Test cadastral structure fallback from S3 to local file."""
        # Mock S3Storage instance that returns None
        mock_storage = MagicMock()
        mock_storage.get_cadastral_structure.return_value = None
        mock_get_storage.return_value = mock_storage

        # Mock local file exists and content
        mock_exists.return_value = True
        mock_file.return_value.read.return_value = json.dumps(sample_cadastral_structure)

        response = client.get("/api/v1/get-cadastral-structure/")

        assert response.status_code == 200
        assert response.json() == sample_cadastral_structure

    @patch("land_registry.app.get_s3_storage")
    def test_load_cadastral_files_from_s3_success(self, mock_get_storage, client, sample_s3_files):
        """Test loading cadastral files from S3."""
        # Mock S3Storage instance
        mock_storage = MagicMock()
        mock_layers_data = [
            {
                "name": "test_layer",
                "file": "ITALIA/test_file.shp",
                "geojson": {"type": "FeatureCollection", "features": []},
                "feature_count": 10,
                "gdf": MagicMock()
            }
        ]
        mock_storage.read_multiple_files.return_value = mock_layers_data
        mock_get_storage.return_value = mock_storage

        request_data = {"files": ["test_file.shp"]}
        response = client.post("/api/v1/load-cadastral-files/", json=request_data)

        assert response.status_code == 200
        data = response.json()
        assert data["total_layers"] == 1
        assert data["source"] == "S3"
        assert "layers" in data

    @patch("land_registry.app.get_s3_storage")
    def test_load_cadastral_files_from_s3_no_valid_files(self, mock_get_storage, client):
        """Test loading cadastral files from S3 with no valid files."""
        # Mock S3Storage instance that returns empty list
        mock_storage = MagicMock()
        mock_storage.read_multiple_files.return_value = []
        mock_get_storage.return_value = mock_storage

        request_data = {"files": ["invalid_file.shp"]}
        response = client.post("/api/v1/load-cadastral-files/", json=request_data)

        assert response.status_code == 400
        assert "No valid geospatial files could be loaded from S3" in response.json()["detail"]