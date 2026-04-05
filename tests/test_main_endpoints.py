"""
Tests for main.py-specific endpoints.

Covers:
- GET /health
- GET / (redirect)
- GET /landing
- GET /cadastral-data (error paths)
- GET /api/v1/table-data (pagination, search, filter, sort, empty, error)
- GET /api/v1/adjacency-data (503)
- GET /api/v1/mapping-data (503)
"""

import json
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import geopandas as gpd
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import Polygon

from land_registry.main import app
from land_registry.dependencies import _map_state


@contextmanager
def gdf_loaded(gdf):
    """Context manager that seeds _map_state with a GeoDataFrame and cleans up after."""
    _map_state.set_gdf(gdf)
    try:
        yield
    finally:
        _map_state.set_gdf(None)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def following_client():
    """Client that follows redirects."""
    return TestClient(app, follow_redirects=True)


@pytest.fixture
def sample_gdf():
    polys = [
        Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
        Polygon([(1, 0), (2, 0), (2, 1), (1, 1)]),
        Polygon([(2, 0), (3, 0), (3, 1), (2, 1)]),
    ]
    gdf = gpd.GeoDataFrame(
        {
            "name": ["A", "B", "C"],
            "value": [10, 20, 30],
            "geometry": polys,
        },
        crs="EPSG:4326",
    )
    return gdf


SAMPLE_CADASTRAL = {
    "LOMBARDIA": {
        "BG": {
            "ALBANO_A151": {
                "name": "ALBANO",
                "code": "A151",
                "files": ["MAP_ALBANO.gpkg"],
            }
        }
    }
}


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


class TestHealth:
    def test_returns_healthy(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "healthy", "service": "land-registry"}


# ---------------------------------------------------------------------------
# / redirect
# ---------------------------------------------------------------------------


class TestRootRedirect:
    def test_redirects_to_map(self, client):
        response = client.get("/")
        assert response.status_code in (307, 302, 301)
        assert "/map" in response.headers["location"]


# ---------------------------------------------------------------------------
# /landing
# ---------------------------------------------------------------------------


class TestLandingPage:
    @patch("land_registry.main.get_cadastral_stats")
    def test_returns_html_with_stats(self, mock_stats, following_client):
        mock_stats.return_value = {
            "total_regions": 5,
            "total_provinces": 20,
            "total_municipalities": 1000,
            "total_files": 3000,
        }
        response = following_client.get("/landing")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    @patch("land_registry.main.get_cadastral_stats")
    def test_stats_are_used(self, mock_stats, following_client):
        mock_stats.return_value = {
            "total_regions": 7,
            "total_provinces": 33,
            "total_municipalities": 7904,
            "total_files": 15000,
        }
        response = following_client.get("/landing")
        assert response.status_code == 200
        assert "7,904" in response.text or "7904" in response.text


# ---------------------------------------------------------------------------
# /cadastral-data
# ---------------------------------------------------------------------------


class TestCadastralDataPage:
    @patch("land_registry.main.load_cadastral_structure")
    def test_returns_404_when_no_data(self, mock_load, following_client):
        mock_load.return_value = None
        response = following_client.get("/cadastral-data")
        assert response.status_code == 404

    @patch("land_registry.main.load_cadastral_structure")
    def test_returns_html_with_data(self, mock_load, following_client):
        mock_cadastral = MagicMock()
        mock_cadastral.data = SAMPLE_CADASTRAL
        mock_cadastral.stats = {
            "total_regions": 1,
            "total_provinces": 1,
            "total_municipalities": 1,
            "total_files": 1,
        }
        mock_load.return_value = mock_cadastral

        with patch("land_registry.main.file_availability_db") as mock_db:
            mock_db.get_file_status_batch.return_value = {}
            response = following_client.get("/cadastral-data")

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    @patch("land_registry.main.load_cadastral_structure")
    def test_handles_cache_error_gracefully(self, mock_load, following_client):
        """File availability cache errors should not crash the page."""
        mock_cadastral = MagicMock()
        mock_cadastral.data = SAMPLE_CADASTRAL
        mock_cadastral.stats = {
            "total_regions": 1,
            "total_provinces": 1,
            "total_municipalities": 1,
            "total_files": 1,
        }
        mock_load.return_value = mock_cadastral

        with patch("land_registry.main.file_availability_db") as mock_db:
            mock_db.get_file_status_batch.side_effect = RuntimeError("db error")
            response = following_client.get("/cadastral-data")

        # Should still return 200 even if cache lookup fails
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# /api/v1/table-data
# ---------------------------------------------------------------------------


class TestTableDataEndpoint:
    def test_empty_when_no_gdf(self, client):
        with gdf_loaded(None):
            response = client.get("/api/v1/table-data")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["data"] == []
        assert data["columns"] == []

    def test_returns_paginated_data(self, client, sample_gdf):
        with gdf_loaded(sample_gdf):
            response = client.get("/api/v1/table-data?page=1&size=2")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        assert data["total_pages"] == 2
        assert len(data["data"]) == 2
        assert "name" in data["columns"]
        assert "value" in data["columns"]
        assert "geometry" not in data["columns"]

    def test_second_page(self, client, sample_gdf):
        with gdf_loaded(sample_gdf):
            response = client.get("/api/v1/table-data?page=2&size=2")
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) == 1  # only 1 row on page 2

    def test_global_search_filters_rows(self, client, sample_gdf):
        with gdf_loaded(sample_gdf):
            response = client.get("/api/v1/table-data?search=B")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["data"][0]["name"] == "B"

    def test_global_search_case_insensitive(self, client, sample_gdf):
        with gdf_loaded(sample_gdf):
            response = client.get("/api/v1/table-data?search=a")
        assert response.status_code == 200
        data = response.json()
        # "A" matches name="A"
        assert data["total"] >= 1

    def test_field_filter(self, client, sample_gdf):
        with gdf_loaded(sample_gdf):
            response = client.get("/api/v1/table-data?filter_field=name&filter_value=C")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["data"][0]["name"] == "C"

    def test_sort_ascending(self, client, sample_gdf):
        with gdf_loaded(sample_gdf):
            response = client.get("/api/v1/table-data?sort_field=value&sort_dir=asc")
        assert response.status_code == 200
        data = response.json()
        values = [row["value"] for row in data["data"]]
        assert values == sorted(values)

    def test_sort_descending(self, client, sample_gdf):
        with gdf_loaded(sample_gdf):
            response = client.get("/api/v1/table-data?sort_field=value&sort_dir=desc")
        assert response.status_code == 200
        data = response.json()
        values = [row["value"] for row in data["data"]]
        assert values == sorted(values, reverse=True)

    def test_unknown_sort_field_ignored(self, client, sample_gdf):
        with gdf_loaded(sample_gdf):
            response = client.get("/api/v1/table-data?sort_field=nonexistent&sort_dir=asc")
        assert response.status_code == 200
        assert response.json()["total"] == 3

    def test_unknown_filter_field_ignored(self, client, sample_gdf):
        with gdf_loaded(sample_gdf):
            response = client.get("/api/v1/table-data?filter_field=nonexistent&filter_value=X")
        assert response.status_code == 200
        assert response.json()["total"] == 3

    def test_gdf_without_geometry_column(self, client):
        """GDF without geometry column should still work."""
        df = gpd.GeoDataFrame({"name": ["X", "Y"], "val": [1, 2]})
        with gdf_loaded(df):
            response = client.get("/api/v1/table-data")
        assert response.status_code == 200

    def test_filtered_total_in_response(self, client, sample_gdf):
        with gdf_loaded(sample_gdf):
            response = client.get("/api/v1/table-data?search=A")
        data = response.json()
        assert "filtered_total" in data


# ---------------------------------------------------------------------------
# /api/v1/adjacency-data
# ---------------------------------------------------------------------------


class TestAdjacencyDataEndpoint:
    def test_returns_503(self, client):
        response = client.get("/api/v1/adjacency-data")
        assert response.status_code == 503
        assert "not yet implemented" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# /api/v1/mapping-data
# ---------------------------------------------------------------------------


class TestMappingDataEndpoint:
    def test_returns_503(self, client):
        response = client.get("/api/v1/mapping-data")
        assert response.status_code == 503
        assert "not yet implemented" in response.json()["detail"].lower()
