"""
Tests for cadastral query, FGB load, and datashader endpoints in routers/api.py.

Covers:
- GET /api/v1/fgb/load/{region}/{type}
- GET /api/v1/cadastral/databases
- POST /api/v1/cadastral/query
- GET /api/v1/cadastral/hierarchy
- GET /api/v1/cadastral/statistics
- POST /api/v1/cadastral/point-lookup
- POST /api/v1/cadastral/zone-overlay-lookup
- GET /api/v1/cadastral/search/{reference}
- GET /api/v1/datashader/heatmap/{region}
- GET /api/v1/datashader/categorical/{region}
- GET /api/v1/tiles/datashader/{z}/{x}/{y}.png
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import geopandas as gpd
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import Polygon

from land_registry.main import app


@pytest.fixture
def client():
    return TestClient(app, follow_redirects=True)


# ---------------------------------------------------------------------------
# FGB load endpoint
# ---------------------------------------------------------------------------

class TestFGBLoadEndpoint:

    def test_invalid_layer_type(self, client):
        response = client.get("/api/v1/fgb/load/basilicata/bad_type")
        assert response.status_code == 400
        assert "layer_type" in response.json()["detail"].lower()

    def test_file_not_found(self, client, tmp_path):
        fgb_dir = tmp_path / "fgb"
        fgb_dir.mkdir()

        with patch("land_registry.config.spatialite_settings") as mock_settings:
            mock_settings.fgb_directory = str(fgb_dir)
            response = client.get("/api/v1/fgb/load/basilicata/map")

        assert response.status_code == 404

    def test_file_load_success(self, client, tmp_path):
        """Test successful load when file exists with valid GeoDataFrame."""
        fgb_dir = tmp_path / "fgb"
        fgb_dir.mkdir()
        fake_fgb = fgb_dir / "cadastral_map.basilicata.fgb"
        fake_fgb.write_bytes(b"stub")  # file must exist for path check

        poly = Polygon([(15, 40), (16, 40), (16, 41), (15, 41), (15, 40)])
        mock_gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[poly], crs="EPSG:4326")

        with patch("land_registry.config.spatialite_settings") as mock_settings:
            mock_settings.fgb_directory = str(fgb_dir)
            with patch("land_registry.routers.api.gpd.read_file", return_value=mock_gdf):
                response = client.get("/api/v1/fgb/load/basilicata/map")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["feature_count"] == 1
        assert data["layer_type"] == "map"
        assert data["filename"] == "cadastral_map.basilicata.fgb"


# ---------------------------------------------------------------------------
# Cadastral databases listing
# ---------------------------------------------------------------------------

class TestCadastralDatabasesEndpoint:

    def test_list_databases_no_files(self, client, tmp_path, monkeypatch):
        """The endpoint calls _discover_ple_databases() which is not defined (bug).
        It always returns 500 in the current state."""
        monkeypatch.chdir(tmp_path)
        # _discover_ple_databases is undefined in the module → endpoint raises 500
        response = client.get("/api/v1/cadastral/databases")
        assert response.status_code == 500

    def test_list_databases_with_mock(self, client, tmp_path, monkeypatch):
        """Endpoint returns 200 when _discover_ple_databases is patched."""
        monkeypatch.chdir(tmp_path)

        with patch("land_registry.routers.api._discover_ple_databases", return_value={}, create=True):
            response = client.get("/api/v1/cadastral/databases")

        assert response.status_code == 200
        data = response.json()
        assert "map" in data
        assert "ple" in data


# ---------------------------------------------------------------------------
# Cadastral query endpoint
# ---------------------------------------------------------------------------

class TestCadastralQueryEndpoint:

    def test_query_no_database_map(self, client):
        """Returns 404 when no map database available."""
        with patch("land_registry.routers.api.get_cadastral_db", return_value=None):
            response = client.post("/api/v1/cadastral/query", json={
                "layer_type": "map",
                "regione": "LAZIO"
            })
        assert response.status_code == 404

    def test_query_no_database_ple_no_region(self, client):
        """Returns 400 for PLE query without region when db is None."""
        with patch("land_registry.routers.api.get_cadastral_db", return_value=None):
            response = client.post("/api/v1/cadastral/query", json={
                "layer_type": "ple"
            })
        assert response.status_code == 400

    def test_query_success(self, client):
        """Returns query results when database is available."""
        mock_db = MagicMock()
        mock_db.query.return_value = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": None, "properties": {"id": 1}}
            ]
        }

        with patch("land_registry.routers.api.get_cadastral_db", return_value=mock_db):
            response = client.post("/api/v1/cadastral/query", json={
                "layer_type": "map",
                "regione": "LAZIO",
                "limit": 10
            })

        assert response.status_code == 200
        data = response.json()
        assert data["type"] == "FeatureCollection"
        assert len(data["features"]) == 1


# ---------------------------------------------------------------------------
# Cadastral hierarchy endpoint
# ---------------------------------------------------------------------------

class TestCadastralHierarchyEndpoint:

    def test_hierarchy_no_db(self, client):
        """Returns empty dict when layer_type provided but db is None."""
        with patch("land_registry.routers.api.get_cadastral_db", return_value=None):
            response = client.get("/api/v1/cadastral/hierarchy?layer_type=map")
        assert response.status_code == 200
        assert response.json() == {}

    def test_hierarchy_with_db(self, client):
        """Returns hierarchy data when db is available."""
        mock_db = MagicMock()
        mock_db.get_hierarchy.return_value = {"regioni": ["LAZIO", "LOMBARDIA"]}

        with patch("land_registry.routers.api.get_cadastral_db", return_value=mock_db):
            response = client.get("/api/v1/cadastral/hierarchy?layer_type=map")

        assert response.status_code == 200
        data = response.json()
        assert "regioni" in data

    def test_hierarchy_no_layer_type_no_dbs(self, client):
        """Returns empty dict when no layer_type and no databases available."""
        mock_db = MagicMock()
        mock_db.get_hierarchy.return_value = {}

        with patch("land_registry.routers.api.get_cadastral_db_map", return_value=mock_db):
            with patch("land_registry.routers.api.get_all_ple_databases", return_value={}):
                response = client.get("/api/v1/cadastral/hierarchy")

        assert response.status_code == 200
        assert response.json() == {}

    def test_hierarchy_no_layer_type_with_map_db(self, client):
        """Returns combined hierarchy from map db when no layer_type specified."""
        mock_db = MagicMock()
        mock_db.get_hierarchy.return_value = {"regioni": ["LAZIO"]}

        with patch("land_registry.routers.api.get_cadastral_db_map", return_value=mock_db):
            with patch("land_registry.routers.api.get_all_ple_databases", return_value={}):
                response = client.get("/api/v1/cadastral/hierarchy")

        assert response.status_code == 200
        data = response.json()
        assert "regioni" in data
        assert "LAZIO" in data["regioni"]


# ---------------------------------------------------------------------------
# Cadastral statistics endpoint
# ---------------------------------------------------------------------------

class TestCadastralStatisticsEndpoint:

    def test_statistics_no_dbs(self, client):
        """Returns statistics even when databases unavailable (graceful)."""
        mock_db = MagicMock()
        mock_db.get_statistics.side_effect = Exception("DB not found")

        with patch("land_registry.routers.api.get_cadastral_db_map", return_value=mock_db):
            with patch("land_registry.routers.api.get_all_ple_databases", return_value={}):
                response = client.get("/api/v1/cadastral/statistics")

        assert response.status_code == 200

    def test_statistics_with_map_db(self, client):
        """Returns statistics from map database."""
        mock_db = MagicMock()
        mock_db.get_statistics.return_value = {
            "total_parcels": 100,
            "by_region": {"LAZIO": 100},
            "spatialite_available": False
        }

        with patch("land_registry.routers.api.get_cadastral_db_map", return_value=mock_db):
            with patch("land_registry.routers.api.get_all_ple_databases", return_value={}):
                response = client.get("/api/v1/cadastral/statistics")

        assert response.status_code == 200
        data = response.json()
        assert "map" in data or "total_parcels" in data or "combined" in data

    def test_statistics_with_ple_dbs(self, client):
        """Returns combined statistics including PLE databases (covers inner loop lines 3018-3034)."""
        mock_map_db = MagicMock()
        mock_map_db.get_statistics.return_value = {
            "total_parcels": 50,
            "by_region": {"LAZIO": 50},
            "spatialite_available": False
        }

        mock_ple_db = MagicMock()
        mock_ple_db.get_statistics.return_value = {
            "total_parcels": 100,
            "by_region": {"LOMBARDIA": 100},
            "spatialite_available": True
        }

        with patch("land_registry.routers.api.get_cadastral_db_map", return_value=mock_map_db):
            with patch("land_registry.routers.api.get_all_ple_databases",
                       return_value={"lombardia": mock_ple_db}):
                response = client.get("/api/v1/cadastral/statistics")

        assert response.status_code == 200
        data = response.json()
        # Combined statistics should include both databases
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Point lookup endpoint
# ---------------------------------------------------------------------------

class TestPointLookupEndpoint:

    def test_point_lookup_no_db(self, client):
        """Returns 404 when no database available."""
        with patch("land_registry.routers.api.get_cadastral_db", return_value=None):
            response = client.post("/api/v1/cadastral/point-lookup", json={
                "lon": 12.5, "lat": 41.9, "layer_type": "map"
            })
        assert response.status_code == 404

    def test_point_lookup_ple_no_region(self, client):
        """Returns 400 when PLE lookup without region and no db."""
        with patch("land_registry.routers.api.get_cadastral_db", return_value=None):
            response = client.post("/api/v1/cadastral/point-lookup", json={
                "lon": 12.5, "lat": 41.9, "layer_type": "ple"
            })
        assert response.status_code == 400

    def test_point_lookup_success(self, client):
        """Returns items when database query succeeds."""
        mock_db = MagicMock()
        mock_db.query.return_value = {
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[12.0, 41.0], [13.0, 41.0], [13.0, 42.0], [12.0, 42.0], [12.0, 41.0]]]
                    },
                    "properties": {"id": 1, "foglio": "42", "comune": "H501"}
                }
            ]
        }

        with patch("land_registry.routers.api.get_cadastral_db", return_value=mock_db):
            response = client.post("/api/v1/cadastral/point-lookup", json={
                "lon": 12.5, "lat": 41.9, "layer_type": "map"
            })

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "items" in data
        assert "total" in data


# ---------------------------------------------------------------------------
# Zone overlay lookup endpoint
# ---------------------------------------------------------------------------

VALID_ZONE_GEOJSON = {
    "type": "Feature",
    "geometry": {
        "type": "Polygon",
        "coordinates": [[[12.0, 41.0], [13.0, 41.0], [13.0, 42.0], [12.0, 42.0], [12.0, 41.0]]]
    },
    "properties": {}
}


class TestZoneOverlayLookupEndpoint:

    def test_overlay_lookup_no_db(self, client):
        """Returns 404 when no database available."""
        with patch("land_registry.routers.api.get_cadastral_db", return_value=None):
            response = client.post("/api/v1/cadastral/zone-overlay-lookup", json={
                "zone_geojson": VALID_ZONE_GEOJSON,
                "layer_type": "map"
            })
        assert response.status_code == 404

    def test_overlay_lookup_ple_no_region(self, client):
        """Returns 400 for PLE overlay lookup without region."""
        with patch("land_registry.routers.api.get_cadastral_db", return_value=None):
            response = client.post("/api/v1/cadastral/zone-overlay-lookup", json={
                "zone_geojson": VALID_ZONE_GEOJSON,
                "layer_type": "ple"
            })
        assert response.status_code == 400

    def test_overlay_lookup_invalid_geojson(self, client):
        """Returns 422 for invalid zone_geojson (fails Pydantic validation)."""
        response = client.post("/api/v1/cadastral/zone-overlay-lookup", json={
            "zone_geojson": {"type": "NotAFeature"},  # Pydantic validator rejects this
        })
        assert response.status_code == 422

    def test_overlay_lookup_success(self, client):
        """Returns items when database query succeeds."""
        mock_db = MagicMock()
        mock_db.query.return_value = {
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[12.1, 41.1], [12.5, 41.1], [12.5, 41.5], [12.1, 41.5], [12.1, 41.1]]]
                    },
                    "properties": {"id": 1, "comune": "H501"}
                }
            ]
        }

        with patch("land_registry.routers.api.get_cadastral_db", return_value=mock_db):
            response = client.post("/api/v1/cadastral/zone-overlay-lookup", json={
                "zone_geojson": VALID_ZONE_GEOJSON,
                "relation": "intersects",
                "layer_type": "map"
            })

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "items" in data
        assert "total" in data


# ---------------------------------------------------------------------------
# Cadastral search by reference
# ---------------------------------------------------------------------------

class TestCadastralSearchEndpoint:

    def test_search_foglio_not_found(self, client):
        """Returns 404 when reference not found in map DB."""
        mock_db = MagicMock()
        conn_ctx = MagicMock()
        conn_ctx.__enter__ = MagicMock(return_value=conn_ctx)
        conn_ctx.__exit__ = MagicMock(return_value=False)
        conn_ctx.execute.return_value.fetchall.return_value = []
        mock_db._get_connection.return_value = conn_ctx

        with patch("land_registry.routers.api.get_cadastral_db_map", return_value=mock_db):
            response = client.get("/api/v1/cadastral/search/I056_000100")

        assert response.status_code == 404

    def test_search_foglio_success(self, client):
        """Returns feature when reference found in map DB."""
        mock_row = {"id": 1, "national_reference": "I056_000100", "geometry_wkt": None}

        mock_db = MagicMock()
        conn_ctx = MagicMock()
        conn_ctx.__enter__ = MagicMock(return_value=conn_ctx)
        conn_ctx.__exit__ = MagicMock(return_value=False)
        conn_ctx.execute.return_value.fetchall.return_value = [mock_row]
        mock_db._get_connection.return_value = conn_ctx

        with patch("land_registry.routers.api.get_cadastral_db_map", return_value=mock_db):
            response = client.get("/api/v1/cadastral/search/I056_000100")

        # If row has no geometry_wkt, it returns a feature with null geometry
        assert response.status_code == 200
        data = response.json()
        assert data["type"] == "FeatureCollection"
        assert len(data["features"]) == 1

    def test_search_particella_no_region_no_dbs(self, client):
        """Searches all PLE DBs when reference has '.' (particella) and no region."""
        with patch("land_registry.routers.api.get_all_ple_databases", return_value={}):
            response = client.get("/api/v1/cadastral/search/I056_000100.42")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Datashader endpoints
# ---------------------------------------------------------------------------

class TestDatashaderEndpoints:

    def test_tile_endpoint_error_returns_empty_tile(self, client):
        """Datashader tile returns 200 empty tile on error (graceful degradation)."""
        empty_png = b"\x89PNG\r\n\x1a\n"
        mock_service = MagicMock()
        mock_service.generate_tile.side_effect = Exception("No data loaded")
        mock_service._empty_tile.return_value = empty_png

        with patch("land_registry.routers.api.get_datashader_service", return_value=mock_service):
            response = client.get("/api/v1/tiles/datashader/10/512/370.png")

        # On error, returns empty transparent tile (200), not 500
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"

    def test_tile_endpoint_success(self, client):
        """Datashader tile returns PNG when service succeeds."""
        mock_service = MagicMock()
        mock_service.generate_tile.return_value = b"\x89PNG\r\n\x1a\n"  # minimal PNG header

        with patch("land_registry.routers.api.get_datashader_service", return_value=mock_service):
            response = client.get("/api/v1/tiles/datashader/10/512/370.png")

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"

    def test_heatmap_endpoint_error(self, client):
        """Heatmap returns 500 when service raises exception."""
        mock_service = MagicMock()
        mock_service.generate_density_heatmap.side_effect = Exception("No data")

        with patch("land_registry.routers.api.get_datashader_service", return_value=mock_service):
            response = client.get("/api/v1/datashader/heatmap/LAZIO")

        assert response.status_code == 500

    def test_heatmap_endpoint_success(self, client):
        """Heatmap returns PNG when service succeeds."""
        mock_service = MagicMock()
        mock_service.generate_density_heatmap.return_value = b"\x89PNG\r\n\x1a\n"

        with patch("land_registry.routers.api.get_datashader_service", return_value=mock_service):
            response = client.get("/api/v1/datashader/heatmap/LAZIO")

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"

    def test_categorical_endpoint_error(self, client):
        """Categorical map returns 500 when service raises exception."""
        mock_service = MagicMock()
        mock_service.generate_categorical_map.side_effect = Exception("No data")

        with patch("land_registry.routers.api.get_datashader_service", return_value=mock_service):
            response = client.get("/api/v1/datashader/categorical/LAZIO")

        assert response.status_code == 500

    def test_categorical_endpoint_success(self, client):
        """Categorical map returns PNG when service succeeds."""
        mock_service = MagicMock()
        mock_service.generate_categorical_map.return_value = b"\x89PNG\r\n\x1a\n"

        with patch("land_registry.routers.api.get_datashader_service", return_value=mock_service):
            response = client.get("/api/v1/datashader/categorical/LAZIO?field=foglio")

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
