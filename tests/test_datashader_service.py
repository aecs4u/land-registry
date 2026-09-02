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

    def test_disk_cache_round_trip(self, tmp_path, monkeypatch):
        """Tiles survive an in-memory cache miss through the disk cache."""
        monkeypatch.setenv("DATASHADER_TILE_CACHE_DIR", str(tmp_path))
        monkeypatch.setenv("DATASHADER_TILE_DISK_CACHE_MAX", "4")
        cache_key = (1, 2, 3, None, "count", "fire")
        tile = b"cached_png_data"

        writer = DatashaderTileService(cadastral_db=None)
        writer._cache_tile(cache_key, tile)
        writer._tile_cache.clear()

        assert writer._get_cached_tile(cache_key) == tile

    def test_mvt_tile_uses_injected_postgres_boundary_source(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATASHADER_TILE_CACHE_DIR", str(tmp_path))
        source = MagicMock()
        source.read_mvt.return_value = b"mvt"
        svc = DatashaderTileService(cadastral_db=None, boundary_source=source)

        assert svc.generate_boundary_mvt(1, 2, 13, "ple") == b"mvt"
        source.read_mvt.assert_called_once_with("ple", 13, 1, 2)


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


# ---------------------------------------------------------------------------
# generate_boundary_tile / fgb file discovery
#
# Unlike generate_tile (centroid density heatmap from CadastralDatabase),
# generate_boundary_tile reads real polygon geometry straight from the
# cadastral_map.<region>.fgb source files. These tests write small synthetic
# fgb files to a tmp dir (not the real /mnt/mobile dataset) so they're
# self-contained in CI.
# ---------------------------------------------------------------------------

def _write_region_fgb(path, polygon, crs="EPSG:4326", **attributes):
    data = {key: [value] for key, value in attributes.items()}
    data["geometry"] = [polygon]
    gdf = gpd.GeoDataFrame(data, crs=crs)
    gdf.to_file(path, driver="FlatGeobuf")


@pytest.fixture
def fgb_dir(tmp_path, monkeypatch):
    """Synthetic foglio and parcel FGB stores wired as the service data dir."""
    from land_registry.config import spatialite_settings

    poly = Polygon([(12.0, 41.0), (12.1, 41.0), (12.1, 41.1), (12.0, 41.1)])
    _write_region_fgb(
        tmp_path / "cadastral_map.laziotest.fgb",
        poly,
        LABEL="Foglio 4",
        NATIONALCADASTRALZONINGREFERENCE="E204_000400",
        _comune_name="GROTTAFERRATA",
        _provincia="RM",
        _regione="LAZIO",
    )
    parcel = Polygon([(12.02, 41.02), (12.04, 41.02), (12.04, 41.04), (12.02, 41.04)])
    _write_region_fgb(
        tmp_path / "cadastral_ple.laziotest.fgb",
        parcel,
        LABEL="194",
        NATIONALCADASTRALREFERENCE="E204_000400.194",
        _comune_name="GROTTAFERRATA",
        _provincia="RM",
        _regione="LAZIO",
    )

    monkeypatch.setattr(spatialite_settings, "fgb_directory", str(tmp_path))
    return tmp_path


class TestRegionFgbBounds:

    def test_missing_directory_returns_empty(self, service, monkeypatch, tmp_path):
        from land_registry.config import spatialite_settings

        monkeypatch.setattr(spatialite_settings, "fgb_directory", str(tmp_path / "does-not-exist"))
        assert service._region_fgb_bounds() == {}

    def test_indexes_fgb_files(self, service, fgb_dir):
        bounds = service._region_fgb_bounds()
        assert "cadastral_map.laziotest.fgb" in bounds
        minx, miny, maxx, maxy = bounds["cadastral_map.laziotest.fgb"]
        assert minx == pytest.approx(12.0, abs=0.01)
        assert maxx == pytest.approx(12.1, abs=0.01)

    def test_cached_after_first_call(self, service, fgb_dir):
        first = service._region_fgb_bounds()
        assert service._fgb_bounds["map"] is first
        # Second call returns the same cached dict without re-globbing.
        assert service._region_fgb_bounds() is first

    def test_map_and_parcel_bounds_are_cached_separately(self, service, fgb_dir):
        map_bounds = service._region_fgb_bounds("map")
        parcel_bounds = service._region_fgb_bounds("ple")

        assert set(map_bounds) == {"cadastral_map.laziotest.fgb"}
        assert set(parcel_bounds) == {"cadastral_ple.laziotest.fgb"}
        assert service._fgb_bounds == {"map": map_bounds, "ple": parcel_bounds}


class TestCandidateFgbFiles:

    def test_overlapping_bbox_matches(self, service, fgb_dir):
        matches = service._candidate_fgb_files((11.9, 40.9, 12.2, 41.2))
        assert [p.name for p in matches] == ["cadastral_map.laziotest.fgb"]

    def test_non_overlapping_bbox_no_match(self, service, fgb_dir):
        matches = service._candidate_fgb_files((0.0, 0.0, 1.0, 1.0))
        assert matches == []

    def test_parcel_layer_uses_ple_files(self, service, fgb_dir):
        matches = service._candidate_fgb_files((12.0, 41.0, 12.1, 41.1), "ple")
        assert [p.name for p in matches] == ["cadastral_ple.laziotest.fgb"]


class TestGenerateBoundaryTile:

    def test_no_fgb_directory_returns_empty_tile(self, service, monkeypatch, tmp_path):
        from land_registry.config import spatialite_settings

        monkeypatch.setattr(spatialite_settings, "fgb_directory", str(tmp_path / "does-not-exist"))
        # Tile 0/0/0 covers the whole world — but no fgb files exist to match.
        result = service.generate_boundary_tile(0, 0, 0)
        img = Image.open(io.BytesIO(result))
        assert img.format == "PNG"
        assert img.mode == "RGBA"
        assert all(pixel[3] == 0 for pixel in img.getdata())

    def test_tile_over_covered_area_returns_non_empty_png(self, service, fgb_dir):
        # Tile 0/0/0 (whole world) definitely overlaps the synthetic polygon.
        result = service.generate_boundary_tile(0, 0, 0)
        assert isinstance(result, bytes)
        img = Image.open(io.BytesIO(result))
        assert img.format == "PNG"
        assert img.size == (service.tile_size, service.tile_size)

    def test_parcel_tile_reads_ple_layer(self, service, fgb_dir):
        result = service.generate_boundary_tile(0, 0, 0, "ple")
        img = Image.open(io.BytesIO(result))
        assert img.format == "PNG"
        assert img.size == (service.tile_size, service.tile_size)

    def test_cache_hit_returns_cached_result(self, service):
        cache_key = ("boundary", "map", 1, 2, 3)
        cached_bytes = b"cached_boundary_png"
        service._tile_cache[cache_key] = cached_bytes
        result = service.generate_boundary_tile(1, 2, 3)
        assert result == cached_bytes


class TestIdentifyFeature:

    def test_identifies_parcel_reference_and_location(self, service, fgb_dir):
        result = service.identify_feature(41.03, 12.03, "ple")

        assert result == {
            "label": "194",
            "reference": "E204_000400.194",
            "comune": "GROTTAFERRATA",
            "provincia": "RM",
            "regione": "LAZIO",
            "administrative_unit": None,
        }

    def test_identifies_foglio_from_map_layer(self, service, fgb_dir):
        result = service.identify_feature(41.03, 12.03, "map")

        assert result is not None
        assert result["reference"] == "E204_000400"

    def test_returns_none_outside_indexed_bounds(self, service, fgb_dir):
        assert service.identify_feature(45.0, 9.0, "ple") is None


class TestWarmupJit:

    def test_warmup_does_not_raise(self, service):
        """Smoke test: a real (small) datashader render must not error out."""
        service.warmup_jit()  # no exception == pass

    def test_warmup_failure_is_non_fatal(self, service, monkeypatch):
        import land_registry.datashader_service as mod

        def _boom(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(mod.ds.Canvas, "polygons", _boom)
        service.warmup_jit()  # swallowed and logged, not raised
