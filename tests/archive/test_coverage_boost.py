"""
Additional tests to boost coverage specifically targeting uncovered areas.
"""

import pytest
import tempfile
import os
from unittest.mock import patch, MagicMock
import geopandas as gpd
from shapely.geometry import Polygon
from fastapi.testclient import TestClient

from land_registry.main import app
from land_registry.s3_storage import S3Storage, S3Settings
from land_registry.map import extract_qpkg_data
from land_registry.map_controls import MapControlsManager, ControlButton, ControlSelect, ControlGroup


class TestAppHealthAndBasics:
    """Test basic app functionality to boost coverage."""

    def test_health_endpoint(self):
        """Test health endpoint."""
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "healthy", "service": "land-registry"}

    def test_root_endpoint(self):
        """Test root endpoint."""
        client = TestClient(app)
        with patch('land_registry.main.templates.TemplateResponse') as mock_template_response:
            mock_template_response.return_value.status_code = 200
            mock_template_response.return_value.body = b"<html>Test</html>"

            # Mock the TemplateResponse to return HTML content
            with patch('land_registry.main.map_controls') as mock_controls:
                mock_controls.generate_html.return_value = "<div>Controls</div>"
                mock_controls.generate_javascript.return_value = "var test = 1;"

                response = client.get("/")
                assert response.status_code == 200


class TestS3StorageCore:
    """Test core S3Storage functionality."""

    def test_s3_settings_initialization(self):
        """Test S3Settings initialization."""
        settings = S3Settings()
        assert settings.s3_bucket_name == "catasto-2025"
        assert settings.s3_region == "eu-central-1"

    def test_s3_storage_initialization(self):
        """Test S3Storage initialization."""
        settings = S3Settings(s3_bucket_name="test-bucket")
        storage = S3Storage(settings)
        assert storage.settings == settings
        assert storage._client is None

    @patch('land_registry.s3_storage.boto3.client')
    def test_s3_client_property(self, mock_boto3):
        """Test S3 client property initialization."""
        mock_client = MagicMock()
        mock_boto3.return_value = mock_client

        settings = S3Settings(s3_bucket_name="test-bucket")
        storage = S3Storage(settings)

        # Access client property
        client = storage.client
        assert client == mock_client
        assert storage._client == mock_client

    @patch('land_registry.s3_storage.boto3.client')
    def test_file_exists_true(self, mock_boto3):
        """Test file_exists returns True."""
        mock_client = MagicMock()
        mock_boto3.return_value = mock_client
        mock_client.head_object.return_value = {}

        settings = S3Settings(s3_bucket_name="test-bucket")
        storage = S3Storage(settings)

        result = storage.file_exists("test-file.json")
        assert result is True

    @patch('land_registry.s3_storage.boto3.client')
    def test_list_files_basic(self, mock_boto3):
        """Test basic list_files functionality."""
        mock_client = MagicMock()
        mock_boto3.return_value = mock_client

        # Mock paginator response
        mock_paginator = MagicMock()
        mock_client.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [
            {
                'Contents': [
                    {'Key': 'ITALIA/test1.shp'},
                    {'Key': 'ITALIA/test2.shp'}
                ]
            }
        ]

        settings = S3Settings(s3_bucket_name="test-bucket")
        storage = S3Storage(settings)

        files = storage.list_files(prefix="ITALIA/", suffix=".shp")
        assert len(files) == 2
        assert "ITALIA/test1.shp" in files
        assert "ITALIA/test2.shp" in files


class TestMapCore:
    """Test core map functionality."""

    def test_get_current_gdf_none(self):
        """Test get_current_gdf when None."""
        from land_registry.map import get_current_gdf
        with patch('land_registry.map.current_gdf', None):
            result = get_current_gdf()
            assert result is None

    def test_extract_qpkg_data_nonexistent_file(self):
        """Test extract_qpkg_data with nonexistent file."""
        with patch('land_registry.map.zipfile.ZipFile') as mock_zip:
            mock_zip.side_effect = FileNotFoundError("File not found")
            with patch('land_registry.map.gpd.read_file') as mock_read:
                mock_read.side_effect = FileNotFoundError("File not found")
                result = extract_qpkg_data("nonexistent_file.qpkg")
                assert result is None

    @patch('land_registry.map.gpd.read_file')
    def test_extract_qpkg_data_direct_file_success(self, mock_read_file):
        """Test direct file reading fallback."""
        # Create sample GeoDataFrame
        polygon = Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])
        gdf = gpd.GeoDataFrame({'id': [0]}, geometry=[polygon])
        mock_read_file.return_value = gdf

        with tempfile.NamedTemporaryFile(suffix='.gpkg', delete=False) as temp_file:
            temp_file.write(b'fake gpkg data')
            temp_file.flush()

            result = extract_qpkg_data(temp_file.name)
            assert result is not None
            assert '"type": "FeatureCollection"' in result

            os.unlink(temp_file.name)


class TestMapControls:
    """Test map controls functionality."""

    def test_control_button_creation(self):
        """Test ControlButton creation."""
        button = ControlButton(
            id="test_button",
            title="Test",
            icon="fa-test",
            onclick="test()"
        )
        assert button.id == "test_button"
        assert button.title == "Test"
        assert button.enabled is True

    def test_control_select_creation(self):
        """Test ControlSelect creation."""
        select = ControlSelect(
            id="test_select",
            title="Test Select",
            options=[{"value": "1", "label": "Option 1"}],
            onchange="change()"
        )
        assert select.id == "test_select"
        assert len(select.options) == 1
        assert select.enabled is True

    def test_control_group_creation(self):
        """Test ControlGroup creation."""
        button = ControlButton("btn1", "Button", "fa-btn", "click()")
        group = ControlGroup(
            id="test_group",
            title="Test Group",
            position={"top": "10px", "right": "10px"},
            controls=[button]
        )
        assert group.id == "test_group"
        assert len(group.controls) == 1
        assert group.draggable is True

    def test_map_controls_manager_init(self):
        """Test MapControlsManager initialization."""
        manager = MapControlsManager()
        assert len(manager.control_groups) > 0

    def test_map_controls_manager_html_generation(self):
        """Test HTML generation."""
        manager = MapControlsManager()
        html = manager.generate_html()
        assert isinstance(html, str)
        assert len(html) > 0

    def test_map_controls_manager_js_generation(self):
        """Test JavaScript generation."""
        manager = MapControlsManager()
        js = manager.generate_javascript()
        assert isinstance(js, str)
        assert len(js) > 0


class TestAPIEndpointsBasic:
    """Test basic API endpoint functionality."""

    def test_get_controls_endpoint(self):
        """Test get controls endpoint."""
        client = TestClient(app)
        response = client.get("/api/v1/get-controls/")
        assert response.status_code == 200
        data = response.json()
        assert "groups" in data
        assert isinstance(data["groups"], list)

    @patch('land_registry.main.map_controls.update_control_state')
    def test_update_control_state_success(self, mock_update):
        """Test control state update success."""
        mock_update.return_value = True

        client = TestClient(app)
        response = client.post("/api/v1/update-control-state/", json={
            "control_id": "test_control",
            "enabled": True
        })
        assert response.status_code == 200
        assert response.json()["success"] is True

    @patch('land_registry.main.map_controls.update_control_state')
    def test_update_control_state_not_found(self, mock_update):
        """Test control state update when control not found."""
        mock_update.return_value = False

        client = TestClient(app)
        response = client.post("/api/v1/update-control-state/", json={
            "control_id": "nonexistent",
            "enabled": True
        })
        assert response.status_code == 404

    @patch('land_registry.main.get_current_gdf')
    def test_get_attributes_no_data(self, mock_get_gdf):
        """Test get attributes when no data loaded."""
        mock_get_gdf.return_value = None

        client = TestClient(app)
        response = client.get("/api/v1/get-attributes/")
        assert response.status_code == 400
        assert "No data loaded" in response.json()["detail"]

    def test_save_drawn_polygons_invalid_input(self):
        """Test save drawn polygons with invalid input."""
        client = TestClient(app)
        response = client.post("/api/v1/save-drawn-polygons/", json={})
        assert response.status_code == 422

    def test_load_cadastral_files_no_files(self):
        """Test load cadastral files with no files."""
        client = TestClient(app)
        response = client.post("/api/v1/load-cadastral-files/", json={"files": []})
        assert response.status_code == 400
        assert "No files specified" in response.json()["detail"]


class TestS3ConfigEndpoints:
    """Test S3 configuration endpoints."""

    @patch('land_registry.main.configure_s3_storage')
    def test_configure_s3_basic(self, mock_configure):
        """Test basic S3 configuration."""
        mock_storage = MagicMock()
        mock_storage.list_files.return_value = ["test1.shp", "test2.shp"]
        mock_configure.return_value = mock_storage

        client = TestClient(app)
        response = client.post("/api/v1/configure-s3/", json={
            "bucket_name": "test-bucket",
            "region": "us-east-1"
        })
        assert response.status_code == 200
        assert response.json()["success"] is True

    @patch('land_registry.main.get_s3_storage')
    def test_s3_status_basic(self, mock_get_storage):
        """Test basic S3 status."""
        mock_storage = MagicMock()
        mock_storage.settings.s3_bucket_name = "test-bucket"
        mock_storage.settings.s3_region = "us-east-1"
        mock_storage.settings.s3_endpoint_url = None
        mock_storage.settings.aws_access_key_id = "test-key"
        mock_storage.settings.aws_secret_access_key = "test-secret"
        mock_storage.list_files.return_value = ["file1.shp"]
        mock_get_storage.return_value = mock_storage

        client = TestClient(app)
        response = client.get("/api/v1/s3-status/")
        assert response.status_code == 200
        data = response.json()
        assert data["bucket_name"] == "test-bucket"
        assert data["has_credentials"] is True


@pytest.mark.integration
class TestIntegrationScenarios:
    """Integration tests for common scenarios."""

    @patch('land_registry.main.get_current_gdf')
    @patch('land_registry.main.find_adjacent_polygons')
    def test_adjacent_polygons_workflow(self, mock_find_adjacent, mock_get_gdf):
        """Test complete adjacent polygons workflow."""
        # Create sample GeoDataFrame
        polygon1 = Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])
        polygon2 = Polygon([(1, 0), (2, 0), (2, 1), (1, 1), (1, 0)])
        gdf = gpd.GeoDataFrame({
            'id': [0, 1],
            'feature_id': [0, 1]
        }, geometry=[polygon1, polygon2])

        mock_get_gdf.return_value = gdf
        mock_find_adjacent.return_value = [1]

        client = TestClient(app)
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