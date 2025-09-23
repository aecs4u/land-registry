"""
Focused tests for specific uncovered functions in app.py to boost coverage.
"""

import pytest
import tempfile
import json
import os
from unittest.mock import patch, MagicMock, mock_open
from fastapi.testclient import TestClient
import geopandas as gpd
from shapely.geometry import Polygon

from land_registry.app import app
from land_registry.s3_storage import S3Storage, S3Settings


class TestAppFocusedCoverage:
    """Focused tests for critical uncovered app.py endpoints."""

    def test_health_endpoint_coverage(self):
        """Test health endpoint for complete coverage."""
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data == {"status": "healthy", "service": "land-registry"}

    @patch('land_registry.app.map_controls')
    def test_get_controls_endpoint_coverage(self, mock_controls):
        """Test get controls endpoint."""
        client = TestClient(app)

        # Mock the controls data
        mock_controls.control_groups = [
            MagicMock(id="group1", title="Group 1", controls=[]),
            MagicMock(id="group2", title="Group 2", controls=[])
        ]

        response = client.get("/api/v1/get-controls/")
        assert response.status_code == 200
        data = response.json()
        assert "groups" in data

    @patch('land_registry.app.map_controls')
    def test_update_control_state_endpoint_coverage(self, mock_controls):
        """Test update control state endpoint."""
        client = TestClient(app)

        mock_controls.update_control_state.return_value = True

        response = client.post("/api/v1/update-control-state/", json={
            "control_id": "test_control",
            "enabled": True
        })
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    @patch('land_registry.app.map_controls')
    def test_update_control_state_not_found_coverage(self, mock_controls):
        """Test update control state when control not found."""
        client = TestClient(app)

        mock_controls.update_control_state.return_value = False

        response = client.post("/api/v1/update-control-state/", json={
            "control_id": "nonexistent",
            "enabled": True
        })
        assert response.status_code == 404
        data = response.json()
        assert "Control not found" in data["detail"]

    @patch('builtins.open', mock_open(read_data='{"test": "structure"}'))
    def test_cadastral_data_html_success(self):
        """Test cadastral data HTML endpoint success."""
        client = TestClient(app)

        response = client.get("/cadastral-data.html")
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/html; charset=utf-8"

    @patch('builtins.open', side_effect=FileNotFoundError)
    def test_cadastral_data_html_not_found(self):
        """Test cadastral data HTML endpoint when file not found."""
        client = TestClient(app)

        response = client.get("/cadastral-data.html")
        assert response.status_code == 404

    @patch('builtins.open', side_effect=json.JSONDecodeError("Invalid JSON", "", 0))
    def test_cadastral_data_html_invalid_json(self):
        """Test cadastral data HTML endpoint with invalid JSON."""
        client = TestClient(app)

        response = client.get("/cadastral-data.html")
        assert response.status_code == 500


class TestAppEndpointsCoverage:
    """Additional endpoint coverage tests."""

    def test_load_cadastral_files_invalid_request(self):
        """Test load cadastral files with invalid request."""
        client = TestClient(app)

        response = client.post("/api/v1/load-cadastral-files/", json={})
        assert response.status_code == 422

    @patch('land_registry.app.extract_qpkg_data')
    @patch('land_registry.app.generate_folium_map')
    def test_generate_map_complete_workflow(self, mock_generate_map, mock_extract):
        """Test complete generate map workflow."""
        client = TestClient(app)

        mock_extract.return_value = '{"type": "FeatureCollection", "features": []}'
        mock_generate_map.return_value = "<html>Generated Map</html>"

        # Create a fake file
        file_content = b"fake gpkg content"
        files = {"file": ("test.gpkg", file_content, "application/octet-stream")}

        response = client.post("/api/v1/generate-map/", files=files)
        assert response.status_code == 200
        assert b"Generated Map" in response.content

    def test_upload_qpkg_missing_file(self):
        """Test upload QPKG with missing file parameter."""
        client = TestClient(app)

        response = client.post("/upload-qpkg/")
        assert response.status_code == 422

    def test_save_drawn_polygons_missing_data(self):
        """Test save drawn polygons with missing data."""
        client = TestClient(app)

        response = client.post("/api/v1/save-drawn-polygons/", json={})
        assert response.status_code == 422

    def test_configure_s3_missing_bucket(self):
        """Test configure S3 with missing bucket name."""
        client = TestClient(app)

        response = client.post("/api/v1/configure-s3/", json={})
        assert response.status_code == 422

    def test_get_adjacent_polygons_missing_data(self):
        """Test get adjacent polygons with missing data."""
        client = TestClient(app)

        response = client.post("/api/v1/get-adjacent-polygons/", json={})
        assert response.status_code == 422


class TestAppUtilityFunctions:
    """Test utility functions for complete coverage."""

    @patch('land_registry.app.get_current_gdf')
    def test_adjacent_polygons_complete_workflow(self, mock_get_gdf):
        """Test complete adjacent polygons workflow."""
        client = TestClient(app)

        # Create sample GeoDataFrame
        polygon1 = Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])
        polygon2 = Polygon([(1, 0), (2, 0), (2, 1), (1, 1), (1, 0)])
        gdf = gpd.GeoDataFrame({
            'feature_id': [10, 20],
            'name': ['Feature A', 'Feature B']
        }, geometry=[polygon1, polygon2])

        mock_get_gdf.return_value = gdf

        with patch('land_registry.app.find_adjacent_polygons') as mock_find:
            mock_find.return_value = [20]

            response = client.post("/api/v1/get-adjacent-polygons/", json={
                "feature_id": 10,
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]
                },
                "touch_method": "touches"
            })

            assert response.status_code == 200
            data = response.json()
            assert data["selected_id"] == 10
            assert data["adjacent_ids"] == [20]
            assert "selected_geojson" in data
            assert "adjacent_geojson" in data

    @patch('land_registry.app.get_current_gdf')
    def test_get_attributes_complete_workflow(self, mock_get_gdf):
        """Test complete get attributes workflow."""
        client = TestClient(app)

        # Create GeoDataFrame with various attribute types
        gdf = gpd.GeoDataFrame({
            'id': [1, 2, 3],
            'name': ['Feature 1', 'Feature 2', 'Feature 3'],
            'area': [100.5, 200.7, 150.2],
            'type': ['A', 'B', 'A']
        }, geometry=[
            Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]),
            Polygon([(1, 0), (2, 0), (2, 1), (1, 1), (1, 0)]),
            Polygon([(2, 0), (3, 0), (3, 1), (2, 1), (2, 0)])
        ])

        mock_get_gdf.return_value = gdf

        response = client.get("/api/v1/get-attributes/")
        assert response.status_code == 200
        data = response.json()
        assert "columns" in data
        assert "data" in data
        assert len(data["data"]) == 3
        assert "id" in data["columns"]
        assert "name" in data["columns"]
        assert "area" in data["columns"]