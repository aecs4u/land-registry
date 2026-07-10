"""
Tests for spatialite loading, bulk cadastral loading, and drawn-polygons-with-files endpoints.

Covers:
- POST /api/v1/load-spatialite/ (lines 781-820)
- POST /api/v1/load-cadastral-files/ (lines 1160-1320) — parallel file loader
- GET /api/v1/api/drawn-polygons (with-files path, lines 1761-1787)
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import geopandas as gpd
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import Polygon

from land_registry.main import app


@pytest.fixture
def client():
    return TestClient(app, follow_redirects=True)


# ---------------------------------------------------------------------------
# load-spatialite endpoint
# ---------------------------------------------------------------------------

class TestLoadSpatialiteEndpoint:

    def test_no_features_found(self, client):
        """Returns empty result when spatialite query returns nothing."""
        with patch("land_registry.routers.api.load_spatialite_layer", return_value=None):
            response = client.post("/api/v1/load-spatialite/", json={
                "table": "fogli",
                "limit": 100
            })

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["feature_count"] == 0
        assert data["geojson"] is None

    def test_empty_gdf(self, client):
        """Returns empty result when spatialite returns empty GeoDataFrame."""
        empty_gdf = gpd.GeoDataFrame({"geometry": []})

        with patch("land_registry.routers.api.load_spatialite_layer", return_value=empty_gdf):
            response = client.post("/api/v1/load-spatialite/", json={
                "table": "fogli"
            })

        assert response.status_code == 200
        data = response.json()
        assert data["feature_count"] == 0

    def test_success_with_data(self, client):
        """Returns GeoJSON when spatialite returns valid data."""
        poly = Polygon([(12, 41), (13, 41), (13, 42), (12, 42), (12, 41)])
        mock_gdf = gpd.GeoDataFrame({"id": [1], "foglio": ["42"]}, geometry=[poly], crs="EPSG:4326")

        with patch("land_registry.routers.api.load_spatialite_layer", return_value=mock_gdf):
            with patch("land_registry.routers.api.set_current_gdf"):
                response = client.post("/api/v1/load-spatialite/", json={
                    "table": "fogli",
                    "limit": 100
                })

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["feature_count"] == 1
        assert "geojson" in data
        assert "columns" in data

    def test_spatialite_error(self, client):
        """Returns 500 when spatialite query raises exception."""
        with patch("land_registry.routers.api.load_spatialite_layer",
                   side_effect=Exception("SpatiaLite not available")):
            response = client.post("/api/v1/load-spatialite/", json={
                "table": "fogli"
            })

        assert response.status_code == 500


# ---------------------------------------------------------------------------
# load-cadastral-files (POST, parallel loader)
# ---------------------------------------------------------------------------

class TestLoadMultipleCadastralFilesEndpoint:

    VALID_FILES = [
        "ITALIA/LAZIO/RM/H501_ROMA/H501_ROMA_map.gpkg",
        "ITALIA/LAZIO/VT/L719_VITERBO/L719_VITERBO_map.gpkg"
    ]

    def _mock_gdf(self):
        poly = Polygon([(12, 41), (13, 41), (13, 42), (12, 42), (12, 41)])
        return gpd.GeoDataFrame({"id": [1]}, geometry=[poly], crs="EPSG:4326")

    def test_load_single_file_success(self, client):
        """Loads one file successfully via parallel loader."""
        mock_gdf = self._mock_gdf()
        mock_result = {
            "gdf": mock_gdf,
            "layer_name": "H501_ROMA_map.gpkg",
            "feature_count": 1,
            "file_path": self.VALID_FILES[0]
        }

        with patch("land_registry.routers.api._load_single_file", return_value=mock_result):
            with patch("land_registry.routers.api.cadastral_settings") as mock_settings:
                mock_settings.use_local_files = False
                with patch("land_registry.routers.api.boto3.client", return_value=MagicMock()):
                    with patch("land_registry.routers.api.set_current_gdf"):
                        with patch("land_registry.routers.api.set_current_layers"):
                            with patch("land_registry.routers.api.get_current_gdf", return_value=None):
                                with patch("land_registry.routers.api.get_current_layers", return_value={}):
                                    response = client.post("/api/v1/load-cadastral-files/", json={
                                        "file_paths": [self.VALID_FILES[0]]
                                    })

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "features_count" in data or "total_features_count" in data

    def test_load_with_error_result(self, client):
        """Handles file loading errors gracefully."""
        mock_result = {
            "error": "File not found on S3",
            "file_path": self.VALID_FILES[0]
        }

        with patch("land_registry.routers.api._load_single_file", return_value=mock_result):
            with patch("land_registry.routers.api.cadastral_settings") as mock_settings:
                mock_settings.use_local_files = False
                with patch("land_registry.routers.api.boto3.client", return_value=MagicMock()):
                    with patch("land_registry.routers.api.set_current_gdf"):
                        with patch("land_registry.routers.api.set_current_layers"):
                            with patch("land_registry.routers.api.get_current_gdf", return_value=None):
                                with patch("land_registry.routers.api.get_current_layers", return_value={}):
                                    response = client.post("/api/v1/load-cadastral-files/", json={
                                        "file_paths": [self.VALID_FILES[0]]
                                    })

        assert response.status_code == 200
        data = response.json()
        # Error reported in layers dict but endpoint still succeeds
        assert data["success"] is True
        assert data["failed_layers"] == 1
        assert data["features_count"] == 0

    def test_load_local_files(self, client, tmp_path, monkeypatch):
        """Uses local files when use_local_files=True."""
        mock_gdf = self._mock_gdf()
        mock_result = {
            "gdf": mock_gdf,
            "layer_name": "local_file.gpkg",
            "feature_count": 1,
            "file_path": "ITALIA/LAZIO/RM/H501_ROMA/H501_ROMA_map.gpkg"
        }

        with patch("land_registry.routers.api._load_single_file", return_value=mock_result):
            with patch("land_registry.routers.api.cadastral_settings") as mock_settings:
                mock_settings.use_local_files = True
                with patch("land_registry.routers.api.get_cadastral_data_root", return_value=str(tmp_path)):
                    with patch("land_registry.routers.api.set_current_gdf"):
                        with patch("land_registry.routers.api.set_current_layers"):
                            with patch("land_registry.routers.api.get_current_gdf", return_value=None):
                                with patch("land_registry.routers.api.get_current_layers", return_value={}):
                                    response = client.post("/api/v1/load-cadastral-files/", json={
                                        "file_paths": [self.VALID_FILES[0]]
                                    })

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# /api/drawn-polygons with files (lines 1761-1787)
# ---------------------------------------------------------------------------

class TestDrawnPolygonsWithFiles:

    def test_get_drawn_polygons_with_files(self, client, tmp_path, monkeypatch):
        """Returns list of drawing files when directory has .json files."""
        monkeypatch.chdir(tmp_path)
        drawings_dir = tmp_path / "drawn_polygons"
        drawings_dir.mkdir()

        drawing_content = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": None, "properties": {}}
            ]
        }
        (drawings_dir / "drawings_001.json").write_text(json.dumps(drawing_content))
        (drawings_dir / "drawings_002.json").write_text(json.dumps(drawing_content))
        # Non-.json file should be ignored
        (drawings_dir / "other.txt").write_text("ignored")

        response = client.get("/api/v1/api/drawn-polygons")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert len(data["drawings"]) == 2
        drawing = data["drawings"][0]
        assert "filename" in drawing
        assert "feature_count" in drawing
        assert drawing["feature_count"] == 1

    def test_get_drawn_polygons_ignores_corrupt_files(self, client, tmp_path, monkeypatch):
        """Skips corrupt JSON files gracefully."""
        monkeypatch.chdir(tmp_path)
        drawings_dir = tmp_path / "drawn_polygons"
        drawings_dir.mkdir()

        (drawings_dir / "valid.json").write_text('{"type":"FeatureCollection","features":[]}')
        (drawings_dir / "corrupt.json").write_text("not valid json{{{")

        response = client.get("/api/v1/api/drawn-polygons")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        # Corrupt file is skipped; valid file counts
        assert len(data["drawings"]) == 1
        assert data["drawings"][0]["filename"] == "valid.json"


# ---------------------------------------------------------------------------
# File availability stats and cache endpoints
# ---------------------------------------------------------------------------

class TestFileAvailabilityEndpoints:

    def test_get_file_availability_stats(self, client):
        """Returns stats from file_availability_db."""
        with patch("land_registry.routers.api.file_availability_db") as mock_db:
            mock_db.get_stats.return_value = {"total": 100, "available": 80}
            response = client.get("/api/v1/file-availability-stats/")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "cache_stats" in data

    def test_get_file_availability_stats_error(self, client):
        """Returns 500 when db raises exception."""
        with patch("land_registry.routers.api.file_availability_db") as mock_db:
            mock_db.get_stats.side_effect = Exception("DB error")
            response = client.get("/api/v1/file-availability-stats/")

        assert response.status_code == 500

    def test_clear_file_availability_cache(self, client):
        """Clears cache successfully."""
        with patch("land_registry.routers.api.file_availability_db") as mock_db:
            mock_db.clear_cache.return_value = None
            response = client.delete("/api/v1/file-availability-cache/")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_clear_file_availability_cache_error(self, client):
        """Returns 500 when clearing cache fails."""
        with patch("land_registry.routers.api.file_availability_db") as mock_db:
            mock_db.clear_cache.side_effect = Exception("DB error")
            response = client.delete("/api/v1/file-availability-cache/")

        assert response.status_code == 500


# ---------------------------------------------------------------------------
# State filter and selection endpoints
# ---------------------------------------------------------------------------

class TestStateEndpoints:

    def test_set_filters(self, client):
        """Sets region/province filters in STATE."""
        with patch("land_registry.routers.api.STATE") as mock_state:
            mock_state.region = "LAZIO"
            mock_state.province = "RM"
            response = client.post("/api/v1/filters", json={
                "region": "LAZIO",
                "province": "RM"
            })

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True

    def test_get_selection(self, client):
        """Returns current selection from STATE."""
        with patch("land_registry.routers.api.STATE") as mock_state:
            mock_state.get_selection.return_value = {"features": []}
            response = client.get("/api/v1/selection")

        assert response.status_code == 200
        data = response.json()
        assert "selection" in data
