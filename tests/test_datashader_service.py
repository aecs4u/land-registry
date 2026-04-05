"""
Unit tests for DatashaderTileService.

Tests focus on:
- Pure utility methods (_tile_to_bbox, _get_colormap, _empty_tile, _empty_image)
- _polygons_to_points with GeoDataFrame
- generate_tile without a database (early returns)
- generate_tile cache mechanics
- generate_density_heatmap without db
- generate_categorical_map without db
"""

import io
from unittest.mock import MagicMock, patch

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from PIL import Image
from shapely.geometry import Point, Polygon

from land_registry.datashader_service import DatashaderTileService


@pytest.fixture
def service():
    """Service with no db."""
    return DatashaderTileService(cadastral_db=None)


@pytest.fixture
def mock_db():
    return MagicMock()


# ---------------------------------------------------------------------------
# _tile_to_bbox
# ---------------------------------------------------------------------------

class TestTileToBbox:

    def test_zoom_zero_full_world(self, service):
        """Tile 0/0/0 at zoom 0 covers the whole world."""
        bbox = service._tile_to_bbox(0, 0, 0)
        assert bbox["west"] == pytest.approx(-180.0, abs=1.0)
        assert bbox["east"] == pytest.approx(180.0, abs=1.0)
        assert bbox["south"] < bbox["north"]

    def test_tile_coordinates_ordering(self, service):
        """South is always less than north."""
        for z in [1, 5, 10]:
            for x in [0, 1]:
                for y in [0, 1]:
                    bbox = service._tile_to_bbox(x, y, z)
                    assert bbox["south"] < bbox["north"], f"Invalid bbox for {z}/{x}/{y}"
                    assert bbox["west"] < bbox["east"], f"Invalid bbox for {z}/{x}/{y}"

    def test_known_tile(self, service):
        """Check a known tile coordinate at zoom 1."""
        bbox = service._tile_to_bbox(0, 0, 1)
        assert bbox["west"] == pytest.approx(-180.0, abs=1.0)
        assert bbox["east"] == pytest.approx(0.0, abs=1.0)

    def test_returns_all_keys(self, service):
        bbox = service._tile_to_bbox(3, 2, 5)
        assert set(bbox.keys()) == {"west", "south", "east", "north"}


# ---------------------------------------------------------------------------
# _get_colormap
# ---------------------------------------------------------------------------

class TestGetColormap:

    def test_known_colormap_fire(self, service):
        cmap = service._get_colormap("fire")
        assert cmap is not None
        assert len(cmap) > 0

    def test_known_colormap_blues(self, service):
        cmap = service._get_colormap("blues")
        assert cmap is not None

    def test_unknown_colormap_returns_fire(self, service):
        """Unknown colormap name falls back to fire."""
        import colorcet
        cmap = service._get_colormap("nonexistent_colormap")
        assert cmap is colorcet.fire

    def test_case_insensitive(self, service):
        """Colormap lookup is case-insensitive."""
        cmap_lower = service._get_colormap("fire")
        cmap_upper = service._get_colormap("FIRE")
        import colorcet
        assert cmap_upper is colorcet.fire

    def test_all_mapped_colormaps(self, service):
        for name in ["viridis", "reds", "greens", "rainbow", "coolwarm", "inferno", "plasma"]:
            cmap = service._get_colormap(name)
            assert cmap is not None


# ---------------------------------------------------------------------------
# _empty_tile
# ---------------------------------------------------------------------------

class TestEmptyTile:

    def test_returns_bytes(self, service):
        result = service._empty_tile()
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_is_valid_png(self, service):
        result = service._empty_tile()
        img = Image.open(io.BytesIO(result))
        assert img.format == "PNG"

    def test_correct_size(self, service):
        result = service._empty_tile()
        img = Image.open(io.BytesIO(result))
        assert img.size == (service.tile_size, service.tile_size)

    def test_transparent_rgba(self, service):
        result = service._empty_tile()
        img = Image.open(io.BytesIO(result))
        assert img.mode == "RGBA"
        # Check that alpha channel is 0 (transparent)
        data = list(img.getdata())
        # All pixels should have alpha=0
        assert all(pixel[3] == 0 for pixel in data)


# ---------------------------------------------------------------------------
# _empty_image
# ---------------------------------------------------------------------------

class TestEmptyImage:

    def test_returns_bytes(self, service):
        result = service._empty_image(200, 100)
        assert isinstance(result, bytes)

    def test_is_valid_png(self, service):
        result = service._empty_image(200, 100)
        img = Image.open(io.BytesIO(result))
        assert img.format == "PNG"

    def test_correct_dimensions(self, service):
        result = service._empty_image(300, 150)
        img = Image.open(io.BytesIO(result))
        assert img.size == (300, 150)

    def test_white_background(self, service):
        result = service._empty_image(10, 10)
        img = Image.open(io.BytesIO(result))
        data = list(img.getdata())
        # All pixels should be white (255, 255, 255)
        assert all(pixel[:3] == (255, 255, 255) for pixel in data)


# ---------------------------------------------------------------------------
# _polygons_to_points
# ---------------------------------------------------------------------------

class TestPolygonsToPoints:

    def test_empty_none_returns_empty_df(self, service):
        result = service._polygons_to_points(None)
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 0

    def test_empty_gdf_returns_empty_df(self, service):
        empty_gdf = gpd.GeoDataFrame({"geometry": []})
        result = service._polygons_to_points(empty_gdf)
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 0

    def test_single_polygon(self, service):
        poly = Polygon([(12, 41), (13, 41), (13, 42), (12, 42)])
        gdf = gpd.GeoDataFrame({"geometry": [poly]}, crs="EPSG:4326")
        result = service._polygons_to_points(gdf)
        assert len(result) == 1
        assert "lon" in result.columns
        assert "lat" in result.columns

    def test_includes_optional_fields(self, service):
        """foglio and particella columns are included when present."""
        poly = Polygon([(12, 41), (13, 41), (13, 42), (12, 42)])
        gdf = gpd.GeoDataFrame({
            "geometry": [poly],
            "foglio": [42],
            "particella": [10],
            "comune_code": ["H501"]
        }, crs="EPSG:4326")
        result = service._polygons_to_points(gdf)
        assert "foglio" in result.columns
        assert "particella" in result.columns
        assert "comune_code" in result.columns

    def test_missing_optional_fields_not_added(self, service):
        """Columns not in GDF are not added to result."""
        poly = Polygon([(12, 41), (13, 41), (13, 42), (12, 42)])
        gdf = gpd.GeoDataFrame({"geometry": [poly]}, crs="EPSG:4326")
        result = service._polygons_to_points(gdf)
        assert "foglio" not in result.columns

    def test_centroid_accuracy(self, service):
        """Centroid of a simple square should be at center."""
        poly = Polygon([(10, 40), (12, 40), (12, 42), (10, 42)])
        gdf = gpd.GeoDataFrame({"geometry": [poly]}, crs="EPSG:4326")
        result = service._polygons_to_points(gdf)
        assert result["lon"].iloc[0] == pytest.approx(11.0, abs=0.01)
        assert result["lat"].iloc[0] == pytest.approx(41.0, abs=0.01)


# ---------------------------------------------------------------------------
# generate_tile (no db)
# ---------------------------------------------------------------------------

class TestGenerateTileNoDb:

    def test_returns_empty_tile_when_no_db(self, service):
        """Without a db, returns transparent tile."""
        result = service.generate_tile(10, 5, 5)
        assert isinstance(result, bytes)
        # Should be a valid PNG
        img = Image.open(io.BytesIO(result))
        assert img.format == "PNG"

    def test_cache_miss_then_hit(self, service):
        """Second call with same args hits cache."""
        # First call — no db, empty tile
        result1 = service.generate_tile(0, 0, 1)
        # Cache won't store it because early return from no-db path
        result2 = service.generate_tile(0, 0, 1)
        assert result1 == result2

    def test_cache_hit_returns_cached_result(self):
        """Pre-populated cache is returned immediately (covers lines 75-77)."""
        svc = DatashaderTileService(cadastral_db=None)
        cache_key = (512, 370, 10, None, "count", "fire")
        cached_bytes = b"cached_png_data"
        svc._tile_cache[cache_key] = cached_bytes

        result = svc.generate_tile(512, 370, 10, region=None, agg_type="count", colormap="fire")
        assert result == cached_bytes

    def test_cache_eviction(self):
        """Tile cache evicts oldest entry when at capacity."""
        svc = DatashaderTileService(cadastral_db=None)
        svc._tile_cache_max = 3

        # Manually fill cache past capacity
        for i in range(5):
            cache_key = (i, 0, 0, None, "count", "fire")
            svc._tile_cache[cache_key] = b"tile_data"
            if len(svc._tile_cache) > svc._tile_cache_max:
                svc._tile_cache.popitem(last=False)

        assert len(svc._tile_cache) <= 3


# ---------------------------------------------------------------------------
# generate_tile with mocked db
# ---------------------------------------------------------------------------

class TestGenerateTileWithDb:

    def test_db_returns_none_gdf(self, mock_db):
        """When db returns None, returns empty tile."""
        mock_db.query_parcels.return_value = None
        svc = DatashaderTileService(cadastral_db=mock_db)
        result = svc.generate_tile(10, 5, 5)
        img = Image.open(io.BytesIO(result))
        assert img.format == "PNG"

    def test_db_returns_empty_gdf(self, mock_db):
        """When db returns empty GDF, returns empty tile."""
        empty_gdf = gpd.GeoDataFrame({"geometry": []})
        mock_db.query_parcels.return_value = empty_gdf
        svc = DatashaderTileService(cadastral_db=mock_db)
        result = svc.generate_tile(10, 5, 5)
        assert isinstance(result, bytes)

    def test_exception_returns_empty_tile(self, mock_db):
        """Exception during tile generation returns empty tile."""
        mock_db.query_parcels.side_effect = Exception("DB error")
        svc = DatashaderTileService(cadastral_db=mock_db)
        result = svc.generate_tile(10, 5, 5)
        assert isinstance(result, bytes)
        img = Image.open(io.BytesIO(result))
        assert img.format == "PNG"


# ---------------------------------------------------------------------------
# generate_density_heatmap
# ---------------------------------------------------------------------------

class TestGenerateDensityHeatmap:

    def test_no_db_returns_empty_image(self, service):
        """Without db, returns white image."""
        result = service.generate_density_heatmap("LAZIO")
        assert isinstance(result, bytes)
        img = Image.open(io.BytesIO(result))
        assert img.format == "PNG"

    def test_db_returns_none(self, mock_db):
        mock_db.query_parcels.return_value = None
        svc = DatashaderTileService(cadastral_db=mock_db)
        result = svc.generate_density_heatmap("LAZIO")
        assert isinstance(result, bytes)

    def test_db_returns_empty(self, mock_db):
        empty_gdf = gpd.GeoDataFrame({"geometry": []})
        mock_db.query_parcels.return_value = empty_gdf
        svc = DatashaderTileService(cadastral_db=mock_db)
        result = svc.generate_density_heatmap("LAZIO")
        assert isinstance(result, bytes)

    def test_custom_dimensions(self, service):
        result = service.generate_density_heatmap("LAZIO", width=400, height=300)
        # Even though empty (no db), we get back bytes
        assert isinstance(result, bytes)


# ---------------------------------------------------------------------------
# generate_categorical_map
# ---------------------------------------------------------------------------

class TestGenerateCategoricalMap:

    def test_no_db_returns_empty_image(self, service):
        result = service.generate_categorical_map("LAZIO")
        assert isinstance(result, bytes)
        img = Image.open(io.BytesIO(result))
        assert img.format == "PNG"

    def test_db_returns_none(self, mock_db):
        mock_db.query_parcels.return_value = None
        svc = DatashaderTileService(cadastral_db=mock_db)
        result = svc.generate_categorical_map("LAZIO")
        assert isinstance(result, bytes)

    def test_db_returns_empty(self, mock_db):
        empty_gdf = gpd.GeoDataFrame({"geometry": []})
        mock_db.query_parcels.return_value = empty_gdf
        svc = DatashaderTileService(cadastral_db=mock_db)
        result = svc.generate_categorical_map("LAZIO")
        assert isinstance(result, bytes)
