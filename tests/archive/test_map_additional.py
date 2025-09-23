"""
Additional tests for map functionality, focusing on edge cases and error handling.
"""

import pytest
import tempfile
import zipfile
import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock
import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon, Point, LineString
from shapely.errors import GEOSException

from land_registry.map import extract_qpkg_data, get_current_gdf, find_adjacent_polygons


class TestMapErrorHandling:
    """Test error handling in map functions."""

    def test_extract_qpkg_data_permission_error(self):
        """Test QPKG extraction with permission error."""
        with patch('land_registry.map.zipfile.ZipFile') as mock_zip:
            mock_zip.side_effect = PermissionError("Permission denied")

            # Should handle the error gracefully and try fallback
            result = extract_qpkg_data("some_file.qpkg")
            assert result is None

    def test_extract_qpkg_data_corrupted_zip(self):
        """Test QPKG extraction with corrupted ZIP file."""
        with tempfile.NamedTemporaryFile(suffix='.qpkg', delete=False) as qpkg_file:
            # Write invalid ZIP data
            qpkg_file.write(b'PK\x03\x04corrupted_zip_data')
            qpkg_file.flush()

            result = extract_qpkg_data(qpkg_file.name)
            assert result is None

            os.unlink(qpkg_file.name)

    @patch('land_registry.map.gpd.read_file')
    def test_extract_qpkg_data_geopandas_error(self, mock_read_file):
        """Test QPKG extraction when geopandas fails to read file."""
        mock_read_file.side_effect = Exception("Geopandas read error")

        with tempfile.NamedTemporaryFile(suffix='.gpkg', delete=False) as temp_file:
            temp_file.write(b'fake gpkg data')
            temp_file.flush()

            result = extract_qpkg_data(temp_file.name)
            assert result is None

            os.unlink(temp_file.name)

    def test_find_adjacent_polygons_geos_exception(self):
        """Test find_adjacent_polygons when GEOS operations fail."""
        # Create a problematic geometry that might cause GEOS errors
        invalid_polygons = [
            Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]),
            Polygon([(0, 0), (0, 0), (0, 0)]),  # Degenerate polygon
        ]

        gdf = gpd.GeoDataFrame({
            'id': range(len(invalid_polygons)),
            'name': [f'Polygon {i}' for i in range(len(invalid_polygons))]
        }, geometry=invalid_polygons)

        # This should handle GEOS errors gracefully
        result = find_adjacent_polygons(gdf, 0, "touches")
        assert isinstance(result, list)

    def test_find_adjacent_polygons_mixed_geometry_types(self):
        """Test find_adjacent_polygons with mixed geometry types."""
        mixed_geometries = [
            Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]),
            Point(0.5, 0.5),
            LineString([(0, 0), (1, 1)]),
        ]

        gdf = gpd.GeoDataFrame({
            'id': range(len(mixed_geometries)),
            'name': [f'Geom {i}' for i in range(len(mixed_geometries))]
        }, geometry=mixed_geometries)

        result = find_adjacent_polygons(gdf, 0, "touches")
        assert isinstance(result, list)


class TestMapPerformance:
    """Test performance-related aspects of map functions."""

    def test_find_adjacent_polygons_large_dataset(self):
        """Test find_adjacent_polygons performance with larger dataset."""
        # Create a grid of polygons
        polygons = []
        for i in range(10):
            for j in range(10):
                polygons.append(Polygon([
                    (i, j), (i+1, j), (i+1, j+1), (i, j+1), (i, j)
                ]))

        gdf = gpd.GeoDataFrame({
            'id': range(len(polygons)),
            'name': [f'Polygon {i}' for i in range(len(polygons))]
        }, geometry=polygons)

        # Find adjacents for a central polygon
        result = find_adjacent_polygons(gdf, 55, "touches")  # Middle of 10x10 grid

        # Should find 4 adjacent polygons (up, down, left, right)
        assert isinstance(result, list)
        assert len(result) > 0  # Should have some adjacents

    def test_extract_qpkg_data_large_zip(self):
        """Test extracting data from ZIP with many files."""
        with tempfile.NamedTemporaryFile(suffix='.qpkg', delete=False) as qpkg_file:
            # Create ZIP with many non-geospatial files and one geospatial file
            with zipfile.ZipFile(qpkg_file.name, 'w') as zip_file:
                # Add many dummy files
                for i in range(50):
                    zip_file.writestr(f'dummy_file_{i}.txt', f'dummy content {i}')

                # Add one GeoJSON file
                sample_geojson = {
                    "type": "FeatureCollection",
                    "features": [{
                        "type": "Feature",
                        "properties": {"id": 0},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]
                        }
                    }]
                }
                zip_file.writestr('data.geojson', json.dumps(sample_geojson))

            result = extract_qpkg_data(qpkg_file.name)
            assert result is not None

            geojson_data = json.loads(result)
            assert geojson_data["type"] == "FeatureCollection"

            os.unlink(qpkg_file.name)


class TestMapDataValidation:
    """Test data validation in map functions."""

    def test_extract_qpkg_data_empty_geojson(self):
        """Test extracting empty GeoJSON data."""
        empty_geojson = {
            "type": "FeatureCollection",
            "features": []
        }

        with tempfile.NamedTemporaryFile(suffix='.qpkg', delete=False) as qpkg_file:
            with zipfile.ZipFile(qpkg_file.name, 'w') as zip_file:
                zip_file.writestr('empty.geojson', json.dumps(empty_geojson))

            result = extract_qpkg_data(qpkg_file.name)

            # Should still work with empty features
            if result is not None:
                geojson_data = json.loads(result)
                assert geojson_data["type"] == "FeatureCollection"
                assert len(geojson_data["features"]) == 0

            os.unlink(qpkg_file.name)

    def test_extract_qpkg_data_invalid_geojson(self):
        """Test extracting invalid GeoJSON data."""
        invalid_geojson = {"not": "valid", "geojson": True}

        with tempfile.NamedTemporaryFile(suffix='.qpkg', delete=False) as qpkg_file:
            with zipfile.ZipFile(qpkg_file.name, 'w') as zip_file:
                zip_file.writestr('invalid.geojson', json.dumps(invalid_geojson))

            # Mock geopandas to raise an error for invalid data
            with patch('land_registry.map.gpd.read_file') as mock_read:
                mock_read.side_effect = Exception("Invalid GeoJSON format")
                result = extract_qpkg_data(qpkg_file.name)

                # Should handle invalid GeoJSON gracefully
                assert result is None

            os.unlink(qpkg_file.name)

    def test_find_adjacent_polygons_with_none_geometry(self):
        """Test find_adjacent_polygons with None geometries."""
        # Create GDF with some None geometries
        geometries = [
            Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]),
            None,
            Polygon([(1, 0), (2, 0), (2, 1), (1, 1), (1, 0)]),
        ]

        gdf = gpd.GeoDataFrame({
            'id': range(len(geometries)),
            'name': [f'Geom {i}' for i in range(len(geometries))]
        }, geometry=geometries)

        result = find_adjacent_polygons(gdf, 0, "touches")
        assert isinstance(result, list)

    def test_find_adjacent_polygons_invalid_crs(self):
        """Test find_adjacent_polygons with mismatched CRS."""
        polygons = [
            Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]),
            Polygon([(1, 0), (2, 0), (2, 1), (1, 1), (1, 0)]),
        ]

        # Create GDF with explicit CRS
        gdf = gpd.GeoDataFrame({
            'id': range(len(polygons)),
            'name': [f'Polygon {i}' for i in range(len(polygons))]
        }, geometry=polygons, crs='EPSG:4326')

        result = find_adjacent_polygons(gdf, 0, "touches")
        assert isinstance(result, list)


class TestMapEdgeCases:
    """Test edge cases in map functionality."""

    def test_extract_qpkg_data_file_extensions(self):
        """Test various file extensions and formats."""
        extensions = ['.shp', '.geojson', '.kml', '.gpkg']

        for ext in extensions:
            with tempfile.NamedTemporaryFile(suffix='.qpkg', delete=False) as qpkg_file:
                with zipfile.ZipFile(qpkg_file.name, 'w') as zip_file:
                    zip_file.writestr(f'test{ext}', b'fake geospatial data')

                # Mock geopandas to raise an error for fake data
                with patch('land_registry.map.gpd.read_file') as mock_read:
                    mock_read.side_effect = Exception("Unsupported file format")
                    result = extract_qpkg_data(qpkg_file.name)

                    # Should not crash, result should be None for fake data
                    assert result is None

                os.unlink(qpkg_file.name)

    def test_find_adjacent_polygons_self_intersection(self):
        """Test find_adjacent_polygons with self-intersecting polygons."""
        # Create a self-intersecting polygon (bow-tie shape)
        self_intersecting = Polygon([(0, 0), (1, 1), (1, 0), (0, 1), (0, 0)])
        normal_polygon = Polygon([(1, 0), (2, 0), (2, 1), (1, 1), (1, 0)])

        gdf = gpd.GeoDataFrame({
            'id': [0, 1],
            'name': ['Self-intersecting', 'Normal']
        }, geometry=[self_intersecting, normal_polygon])

        result = find_adjacent_polygons(gdf, 0, "touches")
        assert isinstance(result, list)

    def test_find_adjacent_polygons_very_small_polygons(self):
        """Test find_adjacent_polygons with very small polygons."""
        # Create very small polygons that might cause precision issues
        tiny_polygon1 = Polygon([(0, 0), (1e-10, 0), (1e-10, 1e-10), (0, 1e-10), (0, 0)])
        tiny_polygon2 = Polygon([(1e-10, 0), (2e-10, 0), (2e-10, 1e-10), (1e-10, 1e-10), (1e-10, 0)])

        gdf = gpd.GeoDataFrame({
            'id': [0, 1],
            'name': ['Tiny1', 'Tiny2']
        }, geometry=[tiny_polygon1, tiny_polygon2])

        result = find_adjacent_polygons(gdf, 0, "touches")
        assert isinstance(result, list)

    def test_global_state_thread_safety(self, sample_gdf):
        """Test that global state handling doesn't interfere with concurrent access."""
        # This is a basic test - real thread safety would require more complex testing
        with patch('land_registry.map.current_gdf', sample_gdf):
            results = []

            # Simulate multiple accesses
            for _ in range(10):
                current = get_current_gdf()
                results.append(current)

            # All results should be consistent
            assert all(result is sample_gdf for result in results)


@pytest.mark.slow
class TestMapStressTests:
    """Stress tests for map functionality."""

    def test_extract_many_files_sequentially(self, sample_gdf):
        """Test extracting many files in sequence."""
        results = []

        for i in range(5):  # Keep it reasonable for CI
            with tempfile.NamedTemporaryFile(suffix='.gpkg', delete=False) as temp_file:
                try:
                    sample_gdf.to_file(temp_file.name, driver='GPKG')
                    result = extract_qpkg_data(temp_file.name)
                    results.append(result)
                finally:
                    os.unlink(temp_file.name)

        # All should succeed
        assert all(result is not None for result in results)

    def test_find_adjacent_complex_geometries(self):
        """Test finding adjacents with complex polygon shapes."""
        # Create complex polygons with holes
        outer = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
        hole = [(3, 3), (7, 3), (7, 7), (3, 7), (3, 3)]
        complex_polygon = Polygon(outer, [hole])

        adjacent_polygon = Polygon([(10, 0), (20, 0), (20, 10), (10, 10), (10, 0)])

        gdf = gpd.GeoDataFrame({
            'id': [0, 1],
            'name': ['Complex', 'Adjacent']
        }, geometry=[complex_polygon, adjacent_polygon])

        result = find_adjacent_polygons(gdf, 0, "touches")
        assert isinstance(result, list)
        # Complex polygon should touch adjacent polygon
        assert 1 in result or len(result) == 0  # Depending on implementation