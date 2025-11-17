"""
Final comprehensive test suite to push coverage to maximum achievable level.
This focuses on working, stable tests that provide maximum coverage boost.
"""

import pytest
import tempfile
import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open
from fastapi.testclient import TestClient
import geopandas as gpd
from shapely.geometry import Polygon, Point
import io

from land_registry.main import app
from land_registry.s3_storage import S3Storage, S3Settings
from land_registry.map import extract_qpkg_data, get_current_gdf, find_adjacent_polygons
from land_registry.generate_cadastral_form import analyze_qgis_structure, generate_html_form, main


class TestFinalAppCoverage:
    """Final push for app.py coverage with stable tests."""

    def test_health_endpoint_complete(self):
        """Test health endpoint thoroughly."""
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "land-registry"

    @patch('land_registry.main.map_controls')
    def test_get_controls_complete(self, mock_controls):
        """Test get controls endpoint completely."""
        client = TestClient(app)

        # Mock control groups
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
    def test_update_control_state_success(self, mock_controls):
        """Test control state update success."""
        client = TestClient(app)
        mock_controls.update_control_state.return_value = True

        response = client.post("/api/v1/update-control-state/", json={
            "control_id": "test_control",
            "enabled": True
        })
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    @patch('land_registry.main.map_controls')
    def test_update_control_state_failure(self, mock_controls):
        """Test control state update failure."""
        client = TestClient(app)
        mock_controls.update_control_state.return_value = False

        response = client.post("/api/v1/update-control-state/", json={
            "control_id": "nonexistent",
            "enabled": True
        })
        assert response.status_code == 404

    @patch('land_registry.main.get_current_gdf')
    def test_get_attributes_success(self, mock_get_gdf):
        """Test get attributes success path."""
        client = TestClient(app)

        # Create test GeoDataFrame
        gdf = gpd.GeoDataFrame({
            'id': [1, 2],
            'name': ['Feature 1', 'Feature 2'],
            'area': [100.0, 200.0]
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
        assert len(data["data"]) == 2

    @patch('land_registry.main.get_current_gdf')
    def test_get_attributes_no_data(self, mock_get_gdf):
        """Test get attributes with no data."""
        client = TestClient(app)
        mock_get_gdf.return_value = None

        response = client.get("/api/v1/get-attributes/")
        assert response.status_code == 400

    @patch('land_registry.main.extract_qpkg_data')
    def test_upload_qpkg_success_path(self, mock_extract):
        """Test successful QPKG upload."""
        client = TestClient(app)
        mock_extract.return_value = '{"type": "FeatureCollection", "features": []}'

        file_content = b"fake gpkg content"
        files = {"file": ("test.gpkg", io.BytesIO(file_content), "application/octet-stream")}

        response = client.post("/upload-qpkg/", files=files)
        assert response.status_code == 200
        data = response.json()
        assert "geojson" in data

    def test_upload_qpkg_invalid_file_type(self):
        """Test upload QPKG with invalid file type."""
        client = TestClient(app)

        file_content = b"fake content"
        files = {"file": ("test.txt", io.BytesIO(file_content), "text/plain")}

        response = client.post("/upload-qpkg/", files=files)
        assert response.status_code == 400

    @patch('land_registry.main.extract_qpkg_data')
    def test_upload_qpkg_no_data_extracted(self, mock_extract):
        """Test upload QPKG when no data is extracted."""
        client = TestClient(app)
        mock_extract.return_value = None

        file_content = b"fake gpkg content"
        files = {"file": ("test.gpkg", io.BytesIO(file_content), "application/octet-stream")}

        response = client.post("/upload-qpkg/", files=files)
        assert response.status_code == 400

    @patch('builtins.open', mock_open())
    @patch('os.makedirs')
    @patch('json.dump')
    def test_save_drawn_polygons_success(self, mock_json_dump, mock_makedirs):
        """Test save drawn polygons success."""
        client = TestClient(app)

        polygons_data = {
            "polygons": {
                "type": "FeatureCollection",
                "features": [{
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]
                    },
                    "properties": {"name": "Test"}
                }]
            }
        }

        response = client.post("/api/v1/save-drawn-polygons/", json=polygons_data)
        assert response.status_code == 200
        data = response.json()
        assert "filename" in data

    def test_load_cadastral_files_no_files(self):
        """Test load cadastral files with no files."""
        client = TestClient(app)

        response = client.post("/api/v1/load-cadastral-files/", json={"files": []})
        assert response.status_code == 400

    @patch('builtins.open', mock_open(read_data='{"test": "data"}'))
    def test_get_cadastral_structure_local_success(self):
        """Test get cadastral structure from local file."""
        client = TestClient(app)

        response = client.get("/api/v1/get-cadastral-structure/")
        assert response.status_code == 200
        data = response.json()
        assert data == {"test": "data"}

    @patch('builtins.open', side_effect=FileNotFoundError)
    def test_get_cadastral_structure_file_not_found(self):
        """Test get cadastral structure when file not found."""
        client = TestClient(app)

        response = client.get("/api/v1/get-cadastral-structure/")
        assert response.status_code == 404


class TestFinalMapCoverage:
    """Final push for map.py coverage with stable tests."""

    @patch('land_registry.map.current_gdf', None)
    def test_get_current_gdf_none(self):
        """Test get current GDF when None."""
        result = get_current_gdf()
        assert result is None

    @patch('land_registry.map.gpd.read_file')
    def test_extract_qpkg_data_direct_file_success(self, mock_read_file):
        """Test direct file reading success."""
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
    def test_extract_qpkg_data_read_failure(self, mock_read_file):
        """Test extract QPKG data when file reading fails."""
        mock_read_file.side_effect = Exception("Read failed")

        result = extract_qpkg_data("/nonexistent/file.gpkg")
        assert result is None

    def test_find_adjacent_polygons_touches(self):
        """Test find adjacent polygons with touches method."""
        poly1 = Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])
        poly2 = Polygon([(1, 0), (2, 0), (2, 1), (1, 1), (1, 0)])
        poly3 = Polygon([(3, 3), (4, 3), (4, 4), (3, 4), (3, 3)])

        gdf = gpd.GeoDataFrame({
            'id': [0, 1, 2]
        }, geometry=[poly1, poly2, poly3])

        adjacent = find_adjacent_polygons(gdf, 0, "touches")
        assert 1 in adjacent
        assert 2 not in adjacent

    def test_find_adjacent_polygons_intersects(self):
        """Test find adjacent polygons with intersects method."""
        poly1 = Polygon([(0, 0), (2, 0), (2, 2), (0, 2), (0, 0)])
        poly2 = Polygon([(1, 1), (3, 1), (3, 3), (1, 3), (1, 1)])

        gdf = gpd.GeoDataFrame({
            'id': [0, 1]
        }, geometry=[poly1, poly2])

        adjacent = find_adjacent_polygons(gdf, 0, "intersects")
        assert 1 in adjacent

    def test_find_adjacent_polygons_overlaps(self):
        """Test find adjacent polygons with overlaps method."""
        poly1 = Polygon([(0, 0), (2, 0), (2, 2), (0, 2), (0, 0)])
        poly2 = Polygon([(1, 1), (3, 1), (3, 3), (1, 3), (1, 1)])

        gdf = gpd.GeoDataFrame({
            'id': [0, 1]
        }, geometry=[poly1, poly2])

        adjacent = find_adjacent_polygons(gdf, 0, "overlaps")
        assert 1 in adjacent

    def test_find_adjacent_polygons_invalid_index(self):
        """Test find adjacent polygons with invalid index."""
        poly1 = Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])
        gdf = gpd.GeoDataFrame({'id': [0]}, geometry=[poly1])

        adjacent = find_adjacent_polygons(gdf, 10, "touches")
        assert adjacent == []

    def test_find_adjacent_polygons_default_method(self):
        """Test find adjacent polygons with invalid method."""
        poly1 = Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])
        poly2 = Polygon([(1, 0), (2, 0), (2, 1), (1, 1), (1, 0)])

        gdf = gpd.GeoDataFrame({
            'id': [0, 1]
        }, geometry=[poly1, poly2])

        adjacent = find_adjacent_polygons(gdf, 0, "invalid_method")
        assert 1 in adjacent


class TestFinalGenerateCadastralForm:
    """Final push for generate_cadastral_form.py coverage."""

    def test_analyze_qgis_structure_nonexistent_path(self):
        """Test analyze with nonexistent path."""
        result = analyze_qgis_structure("/nonexistent/path")
        assert result == {}

    def test_analyze_qgis_structure_valid_structure(self):
        """Test analyze with valid structure."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create sample structure
            region_dir = Path(temp_dir) / "ABRUZZO"
            province_dir = region_dir / "AQ"
            municipality_dir = province_dir / "A018_ACCIANO"
            municipality_dir.mkdir(parents=True)

            # Create GPKG files
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

    def test_generate_html_form_basic(self):
        """Test HTML form generation."""
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

    def test_generate_html_form_empty_structure(self):
        """Test HTML form generation with empty structure."""
        structure = {}

        with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False) as temp_file:
            generate_html_form(structure, temp_file.name)

            with open(temp_file.name, 'r', encoding='utf-8') as f:
                html_content = f.read()

            assert "<!DOCTYPE html>" in html_content
            assert 'id="totalRegions">0</div>' in html_content

            os.unlink(temp_file.name)

    @patch('land_registry.generate_cadastral_form.analyze_qgis_structure')
    @patch('land_registry.generate_cadastral_form.generate_html_form')
    @patch('builtins.open', mock_open())
    @patch('json.dump')
    def test_main_success(self, mock_json_dump, mock_file_open, mock_generate_html, mock_analyze):
        """Test main function success."""
        mock_structure = {"ABRUZZO": {"AQ": {"A018_ACCIANO": {"code": "A018", "name": "ACCIANO", "files": []}}}}
        mock_analyze.return_value = mock_structure

        main()

        mock_analyze.assert_called_once()
        mock_generate_html.assert_called_once()
        mock_json_dump.assert_called_once()

    @patch('land_registry.generate_cadastral_form.analyze_qgis_structure')
    def test_main_no_data(self, mock_analyze):
        """Test main function with no data."""
        mock_analyze.return_value = {}

        main()  # Should not raise exception

        mock_analyze.assert_called_once()


class TestFinalS3Storage:
    """Final push for S3Storage coverage with stable tests."""

    def test_s3_settings_initialization(self):
        """Test S3Settings initialization."""
        settings = S3Settings()
        assert settings.s3_bucket_name == "catasto-2025"
        assert settings.s3_region == "eu-central-1"

    def test_s3_settings_custom_values(self):
        """Test S3Settings with custom values."""
        settings = S3Settings(
            s3_bucket_name="custom-bucket",
            s3_region="us-west-2",
            aws_access_key_id="custom-key"
        )
        assert settings.s3_bucket_name == "custom-bucket"
        assert settings.s3_region == "us-west-2"
        assert settings.aws_access_key_id == "custom-key"

    def test_s3_storage_initialization(self):
        """Test S3Storage initialization."""
        settings = S3Settings(s3_bucket_name="test-bucket")
        storage = S3Storage(settings)
        assert storage.settings == settings
        assert storage._client is None


class TestFinalIntegration:
    """Final integration tests for maximum coverage."""

    @patch('land_registry.main.get_current_gdf')
    @patch('land_registry.main.find_adjacent_polygons')
    def test_adjacent_polygons_workflow(self, mock_find_adjacent, mock_get_gdf):
        """Test complete adjacent polygons workflow."""
        client = TestClient(app)

        # Create test GeoDataFrame
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

    def test_end_to_end_file_workflow(self):
        """Test end-to-end file processing workflow."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create test directory structure for cadastral analysis
            region_dir = Path(temp_dir) / "TEST_REGION"
            province_dir = region_dir / "TP"
            municipality_dir = province_dir / "T001_TEST_MUNICIPALITY"
            municipality_dir.mkdir(parents=True)

            # Create test files
            (municipality_dir / "T001_map.gpkg").touch()
            (municipality_dir / "T001_ple.gpkg").touch()

            # Analyze structure
            structure = analyze_qgis_structure(temp_dir)

            # Verify structure
            assert "TEST_REGION" in structure
            assert "TP" in structure["TEST_REGION"]
            assert "T001_TEST_MUNICIPALITY" in structure["TEST_REGION"]["TP"]

            municipality_data = structure["TEST_REGION"]["TP"]["T001_TEST_MUNICIPALITY"]
            assert municipality_data["code"] == "T001"
            assert municipality_data["name"] == "TEST_MUNICIPALITY"
            assert len(municipality_data["files"]) == 2