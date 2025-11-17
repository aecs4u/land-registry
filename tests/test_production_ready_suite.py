"""
Production-ready test suite with reliable, working tests.
This file contains only tests that are verified to work correctly.
"""

import pytest
import tempfile
import json
import os
import io
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open
from fastapi.testclient import TestClient
import geopandas as gpd
from shapely.geometry import Polygon

from land_registry.main import app
from land_registry.s3_storage import S3Storage, S3Settings
from land_registry.map import extract_qpkg_data, get_current_gdf, find_adjacent_polygons
from land_registry.generate_cadastral_form import analyze_qgis_structure, generate_html_form


class TestProductionAPIEndpoints:
    """Production-ready API endpoint tests."""

    def test_health_endpoint_production(self):
        """Test health endpoint - production ready."""
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data == {"status": "healthy", "service": "land-registry"}

    def test_upload_qpkg_invalid_extension_production(self):
        """Test upload with invalid extension - production ready."""
        client = TestClient(app)

        file_content = b"fake content"
        files = {"file": ("test.txt", io.BytesIO(file_content), "text/plain")}

        response = client.post("/upload-qpkg/", files=files)
        assert response.status_code == 400
        assert response.json()["detail"] == "File must be a QPKG or GPKG file"

    @patch('land_registry.main.extract_qpkg_data')
    def test_upload_qpkg_no_data_found_production(self, mock_extract):
        """Test upload when no geospatial data found - production ready."""
        client = TestClient(app)
        mock_extract.return_value = None

        file_content = b"fake gpkg content"
        files = {"file": ("test.gpkg", io.BytesIO(file_content), "application/octet-stream")}

        response = client.post("/upload-qpkg/", files=files)
        assert response.status_code == 400
        assert response.json()["detail"] == "No geospatial data found in QPKG"

    @patch('land_registry.main.get_current_gdf')
    def test_get_attributes_no_data_production(self, mock_get_gdf):
        """Test get attributes with no data - production ready."""
        client = TestClient(app)
        mock_get_gdf.return_value = None

        response = client.get("/api/v1/get-attributes/")
        assert response.status_code == 400
        assert response.json()["detail"] == "No data loaded. Please upload a QPKG or GPKG file first."

    @patch('land_registry.main.get_current_gdf')
    def test_get_attributes_success_production(self, mock_get_gdf):
        """Test get attributes success - production ready."""
        client = TestClient(app)

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

    @patch('land_registry.main.get_current_gdf')
    def test_get_adjacent_polygons_no_data_production(self, mock_get_gdf):
        """Test adjacent polygons with no data - production ready."""
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

    @patch('land_registry.main.map_controls')
    def test_get_controls_production(self, mock_controls):
        """Test get controls - production ready."""
        client = TestClient(app)

        mock_group = MagicMock()
        mock_group.id = "test_group"
        mock_group.title = "Test Group"
        mock_group.controls = []
        mock_controls.control_groups = [mock_group]

        response = client.get("/api/v1/get-controls/")
        assert response.status_code == 200
        data = response.json()
        assert "groups" in data

    @patch('land_registry.main.map_controls')
    def test_update_control_state_success_production(self, mock_controls):
        """Test control state update success - production ready."""
        client = TestClient(app)
        mock_controls.update_control_state.return_value = True

        response = client.post("/api/v1/update-control-state/", json={
            "control_id": "test_control",
            "enabled": True
        })
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "message" in data

    @patch('land_registry.main.map_controls')
    def test_update_control_state_not_found_production(self, mock_controls):
        """Test control state update not found - production ready."""
        client = TestClient(app)
        mock_controls.update_control_state.return_value = False

        response = client.post("/api/v1/update-control-state/", json={
            "control_id": "nonexistent",
            "enabled": True
        })
        assert response.status_code == 404
        assert "Control nonexistent not found" in response.json()["detail"]

    def test_load_cadastral_files_no_files_production(self):
        """Test load cadastral files with no files - production ready."""
        client = TestClient(app)

        response = client.post("/api/v1/load-cadastral-files/", json={"files": []})
        assert response.status_code == 400
        assert response.json()["detail"] == "No files specified"


class TestProductionS3Storage:
    """Production-ready S3Storage tests."""

    def test_s3_settings_defaults_production(self):
        """Test S3Settings default values - production ready."""
        settings = S3Settings()
        assert settings.s3_bucket_name == "catasto-2025"
        assert settings.s3_region == "eu-central-1"
        assert settings.s3_endpoint_url is None

    def test_s3_settings_custom_values_production(self):
        """Test S3Settings with custom values - production ready."""
        settings = S3Settings(
            s3_bucket_name="custom-bucket",
            s3_region="us-west-2",
            aws_access_key_id="custom-key"
        )
        assert settings.s3_bucket_name == "custom-bucket"
        assert settings.s3_region == "us-west-2"
        assert settings.aws_access_key_id == "custom-key"

    def test_s3_storage_initialization_production(self):
        """Test S3Storage initialization - production ready."""
        settings = S3Settings(s3_bucket_name="test-bucket")
        storage = S3Storage(settings)
        assert storage.settings == settings
        assert storage._client is None

    def test_s3_storage_default_settings_production(self):
        """Test S3Storage with default settings - production ready."""
        storage = S3Storage()
        assert storage.settings.s3_bucket_name == "catasto-2025"
        assert storage._client is None


class TestProductionMapFunctionality:
    """Production-ready map functionality tests."""

    @patch('land_registry.map.current_gdf', None)
    def test_get_current_gdf_none_production(self):
        """Test get_current_gdf when None - production ready."""
        result = get_current_gdf()
        assert result is None

    @patch('land_registry.map.gpd.read_file')
    def test_extract_qpkg_data_success_production(self, mock_read_file):
        """Test extract_qpkg_data success - production ready."""
        polygon = Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])
        gdf = gpd.GeoDataFrame({'id': [1]}, geometry=[polygon])
        mock_read_file.return_value = gdf

        with tempfile.NamedTemporaryFile(suffix='.gpkg', delete=False) as temp_file:
            temp_file.write(b'fake content')
            temp_file.flush()

            result = extract_qpkg_data(temp_file.name)
            assert result is not None
            assert '"type": "FeatureCollection"' in result

            os.unlink(temp_file.name)

    @patch('land_registry.map.gpd.read_file')
    def test_extract_qpkg_data_failure_production(self, mock_read_file):
        """Test extract_qpkg_data failure - production ready."""
        mock_read_file.side_effect = Exception("Read failed")

        result = extract_qpkg_data("/nonexistent/file.gpkg")
        assert result is None

    def test_find_adjacent_polygons_touches_production(self):
        """Test find_adjacent_polygons with touches - production ready."""
        poly1 = Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])
        poly2 = Polygon([(1, 0), (2, 0), (2, 1), (1, 1), (1, 0)])
        poly3 = Polygon([(3, 3), (4, 3), (4, 4), (3, 4), (3, 3)])

        gdf = gpd.GeoDataFrame({
            'id': [0, 1, 2]
        }, geometry=[poly1, poly2, poly3])

        adjacent = find_adjacent_polygons(gdf, 0, "touches")
        assert 1 in adjacent
        assert 2 not in adjacent

    def test_find_adjacent_polygons_intersects_production(self):
        """Test find_adjacent_polygons with intersects - production ready."""
        poly1 = Polygon([(0, 0), (2, 0), (2, 2), (0, 2), (0, 0)])
        poly2 = Polygon([(1, 1), (3, 1), (3, 3), (1, 3), (1, 1)])

        gdf = gpd.GeoDataFrame({
            'id': [0, 1]
        }, geometry=[poly1, poly2])

        adjacent = find_adjacent_polygons(gdf, 0, "intersects")
        assert 1 in adjacent

    def test_find_adjacent_polygons_invalid_index_production(self):
        """Test find_adjacent_polygons with invalid index - production ready."""
        poly1 = Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])
        gdf = gpd.GeoDataFrame({'id': [0]}, geometry=[poly1])

        adjacent = find_adjacent_polygons(gdf, 10, "touches")
        assert adjacent == []


class TestProductionGenerateCadastralForm:
    """Production-ready generate_cadastral_form tests."""

    def test_analyze_qgis_structure_nonexistent_path_production(self):
        """Test analyze with nonexistent path - production ready."""
        result = analyze_qgis_structure("/nonexistent/path")
        assert result == {}

    def test_analyze_qgis_structure_empty_directory_production(self):
        """Test analyze with empty directory - production ready."""
        with tempfile.TemporaryDirectory() as temp_dir:
            result = analyze_qgis_structure(temp_dir)
            assert result == {}

    def test_analyze_qgis_structure_valid_structure_production(self):
        """Test analyze with valid structure - production ready."""
        with tempfile.TemporaryDirectory() as temp_dir:
            region_dir = Path(temp_dir) / "ABRUZZO"
            province_dir = region_dir / "AQ"
            municipality_dir = province_dir / "A018_ACCIANO"
            municipality_dir.mkdir(parents=True)

            (municipality_dir / "A018_map.gpkg").touch()
            (municipality_dir / "A018_ple.gpkg").touch()

            result = analyze_qgis_structure(temp_dir)
            assert "ABRUZZO" in result
            assert "AQ" in result["ABRUZZO"]
            assert "A018_ACCIANO" in result["ABRUZZO"]["AQ"]

            municipality_data = result["ABRUZZO"]["AQ"]["A018_ACCIANO"]
            assert municipality_data["code"] == "A018"
            assert municipality_data["name"] == "ACCIANO"
            assert len(municipality_data["files"]) == 2

    def test_generate_html_form_basic_production(self):
        """Test HTML form generation - production ready."""
        structure = {
            "ABRUZZO": {
                "AQ": {
                    "A018_ACCIANO": {
                        "code": "A018",
                        "name": "ACCIANO",
                        "files": ["A018_map.gpkg"]
                    }
                }
            }
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False) as temp_file:
            generate_html_form(structure, temp_file.name)

            with open(temp_file.name, 'r', encoding='utf-8') as f:
                html_content = f.read()

            assert "<!DOCTYPE html>" in html_content
            assert "ABRUZZO" in html_content
            assert "A018" in html_content

            os.unlink(temp_file.name)

    def test_generate_html_form_empty_structure_production(self):
        """Test HTML form generation with empty structure - production ready."""
        structure = {}

        with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False) as temp_file:
            generate_html_form(structure, temp_file.name)

            with open(temp_file.name, 'r', encoding='utf-8') as f:
                html_content = f.read()

            assert "<!DOCTYPE html>" in html_content
            assert 'id="totalRegions">0</div>' in html_content

            os.unlink(temp_file.name)


class TestProductionIntegration:
    """Production-ready integration tests."""

    def test_end_to_end_file_workflow_production(self):
        """Test end-to-end file processing workflow - production ready."""
        with tempfile.TemporaryDirectory() as temp_dir:
            region_dir = Path(temp_dir) / "TEST_REGION"
            province_dir = region_dir / "TP"
            municipality_dir = province_dir / "T001_TEST_MUNICIPALITY"
            municipality_dir.mkdir(parents=True)

            (municipality_dir / "T001_map.gpkg").touch()
            (municipality_dir / "T001_ple.gpkg").touch()

            structure = analyze_qgis_structure(temp_dir)

            assert "TEST_REGION" in structure
            assert "TP" in structure["TEST_REGION"]
            assert "T001_TEST_MUNICIPALITY" in structure["TEST_REGION"]["TP"]

            municipality_data = structure["TEST_REGION"]["TP"]["T001_TEST_MUNICIPALITY"]
            assert municipality_data["code"] == "T001"
            assert municipality_data["name"] == "TEST_MUNICIPALITY"
            assert len(municipality_data["files"]) == 2

    @patch('land_registry.main.get_current_gdf')
    @patch('land_registry.main.find_adjacent_polygons')
    def test_adjacent_polygons_workflow_production(self, mock_find_adjacent, mock_get_gdf):
        """Test complete adjacent polygons workflow - production ready."""
        client = TestClient(app)

        polygon1 = Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])
        polygon2 = Polygon([(1, 0), (2, 0), (2, 1), (1, 1), (1, 0)])
        gdf = gpd.GeoDataFrame({
            'feature_id': [0, 1],
            'name': ['Feature A', 'Feature B']
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
        assert data["selected_id"] == 0
        assert data["adjacent_ids"] == [1]