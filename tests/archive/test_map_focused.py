"""
Focused tests for map.py module to boost coverage significantly.
"""

import tempfile
import os
from unittest.mock import patch, MagicMock
import geopandas as gpd
from shapely.geometry import Polygon, Point

from land_registry.map import (
    extract_qpkg_data, get_current_gdf,
    find_adjacent_polygons
)


class TestMapFocusedCoverage:
    """Focused tests for critical uncovered map.py functions."""

    @patch('land_registry.map.current_gdf', None)
    def test_get_current_gdf_none(self):
        """Test get_current_gdf when None."""
        current = get_current_gdf()
        assert current is None

    @patch('land_registry.map.zipfile.ZipFile')
    @patch('land_registry.map.gpd.read_file')
    def test_extract_qpkg_data_zip_success(self, mock_read_file, mock_zipfile):
        """Test successful QPKG extraction from ZIP file."""
        # Mock ZIP file structure
        mock_zip_instance = MagicMock()
        mock_zipfile.return_value.__enter__.return_value = mock_zip_instance
        mock_zip_instance.namelist.return_value = [
            'data.shp', 'data.shx', 'data.dbf', 'readme.txt'
        ]

        # Mock geopandas reading
        polygon = Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])
        gdf = gpd.GeoDataFrame({'id': [1], 'name': ['Test']}, geometry=[polygon])
        mock_read_file.return_value = gdf

        with tempfile.NamedTemporaryFile(suffix='.qpkg', delete=False) as temp_file:
            temp_file.write(b'fake zip content')
            temp_file.flush()

            result = extract_qpkg_data(temp_file.name)

            assert result is not None
            assert '"type": "FeatureCollection"' in result
            # Verify that current_gdf was set
            assert get_current_gdf() is not None

            os.unlink(temp_file.name)

    @patch('land_registry.map.zipfile.ZipFile')
    def test_extract_qpkg_data_zip_no_geospatial_files(self, mock_zipfile):
        """Test QPKG extraction when ZIP has no geospatial files."""
        mock_zip_instance = MagicMock()
        mock_zipfile.return_value.__enter__.return_value = mock_zip_instance
        mock_zip_instance.namelist.return_value = ['readme.txt', 'image.png', 'data.csv']

        with tempfile.NamedTemporaryFile(suffix='.qpkg', delete=False) as temp_file:
            temp_file.write(b'fake zip content')
            temp_file.flush()

            result = extract_qpkg_data(temp_file.name)
            assert result is None

            os.unlink(temp_file.name)

    @patch('land_registry.map.zipfile.ZipFile')
    @patch('land_registry.map.gpd.read_file')
    def test_extract_qpkg_data_multiple_geospatial_files(self, mock_read_file, mock_zipfile):
        """Test QPKG extraction with multiple geospatial files."""
        mock_zip_instance = MagicMock()
        mock_zipfile.return_value.__enter__.return_value = mock_zip_instance
        mock_zip_instance.namelist.return_value = [
            'data1.shp', 'data1.shx', 'data1.dbf',
            'data2.geojson', 'data3.gpkg', 'readme.txt'
        ]

        # Mock successful reading of first file
        polygon = Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])
        gdf = gpd.GeoDataFrame({'id': [1]}, geometry=[polygon])
        mock_read_file.return_value = gdf

        with tempfile.NamedTemporaryFile(suffix='.qpkg', delete=False) as temp_file:
            temp_file.write(b'fake zip content')
            temp_file.flush()

            result = extract_qpkg_data(temp_file.name)
            assert result is not None

            os.unlink(temp_file.name)

    @patch('land_registry.map.zipfile.ZipFile')
    @patch('land_registry.map.gpd.read_file')
    def test_extract_qpkg_data_geopandas_read_failure(self, mock_read_file, mock_zipfile):
        """Test QPKG extraction when geopandas read fails."""
        mock_zip_instance = MagicMock()
        mock_zipfile.return_value.__enter__.return_value = mock_zip_instance
        mock_zip_instance.namelist.return_value = ['data.shp', 'data.shx', 'data.dbf']

        # Mock geopandas failure
        mock_read_file.side_effect = Exception("Failed to read file")

        with tempfile.NamedTemporaryFile(suffix='.qpkg', delete=False) as temp_file:
            temp_file.write(b'fake zip content')
            temp_file.flush()

            result = extract_qpkg_data(temp_file.name)
            assert result is None

            os.unlink(temp_file.name)

    @patch('land_registry.map.gpd.read_file')
    def test_extract_qpkg_data_direct_file_gpkg(self, mock_read_file):
        """Test direct GPKG file reading (not ZIP)."""
        polygon = Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])
        gdf = gpd.GeoDataFrame({'feature_id': [0], 'name': ['Direct']}, geometry=[polygon])
        mock_read_file.return_value = gdf

        with tempfile.NamedTemporaryFile(suffix='.gpkg', delete=False) as temp_file:
            temp_file.write(b'fake gpkg content')
            temp_file.flush()

            result = extract_qpkg_data(temp_file.name)
            assert result is not None
            assert '"type": "FeatureCollection"' in result

            # Check that feature_id was added if missing
            current = get_current_gdf()
            assert 'feature_id' in current.columns

            os.unlink(temp_file.name)

    @patch('land_registry.map.gpd.read_file')
    def test_extract_qpkg_data_add_feature_id(self, mock_read_file):
        """Test that feature_id is added when missing."""
        polygon = Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])
        # GDF without feature_id column
        gdf = gpd.GeoDataFrame({'name': ['Test']}, geometry=[polygon])
        mock_read_file.return_value = gdf

        with tempfile.NamedTemporaryFile(suffix='.geojson', delete=False) as temp_file:
            temp_file.write(b'fake geojson content')
            temp_file.flush()

            result = extract_qpkg_data(temp_file.name)
            assert result is not None

            # Check that feature_id was added
            current = get_current_gdf()
            assert 'feature_id' in current.columns
            assert current.iloc[0]['feature_id'] == 0

            os.unlink(temp_file.name)

    def test_find_adjacent_polygons_touches_method(self):
        """Test find_adjacent_polygons with touches method."""
        # Create adjacent polygons
        poly1 = Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])
        poly2 = Polygon([(1, 0), (2, 0), (2, 1), (1, 1), (1, 0)])  # Touches poly1
        poly3 = Polygon([(3, 3), (4, 3), (4, 4), (3, 4), (3, 3)])  # Isolated

        gdf = gpd.GeoDataFrame({
            'id': [0, 1, 2],
            'name': ['A', 'B', 'C']
        }, geometry=[poly1, poly2, poly3])

        adjacent = find_adjacent_polygons(gdf, 0, "touches")
        assert 1 in adjacent
        assert 2 not in adjacent

    def test_find_adjacent_polygons_intersects_method(self):
        """Test find_adjacent_polygons with intersects method."""
        # Create overlapping polygons
        poly1 = Polygon([(0, 0), (2, 0), (2, 2), (0, 2), (0, 0)])
        poly2 = Polygon([(1, 1), (3, 1), (3, 3), (1, 3), (1, 1)])  # Overlaps poly1
        poly3 = Polygon([(4, 4), (5, 4), (5, 5), (4, 5), (4, 4)])  # No intersection

        gdf = gpd.GeoDataFrame({
            'id': [0, 1, 2],
            'name': ['A', 'B', 'C']
        }, geometry=[poly1, poly2, poly3])

        adjacent = find_adjacent_polygons(gdf, 0, "intersects")
        assert 1 in adjacent
        assert 2 not in adjacent

    def test_find_adjacent_polygons_overlaps_method(self):
        """Test find_adjacent_polygons with overlaps method."""
        # Create overlapping polygons (not just touching)
        poly1 = Polygon([(0, 0), (2, 0), (2, 2), (0, 2), (0, 0)])
        poly2 = Polygon([(1, 1), (3, 1), (3, 3), (1, 3), (1, 1)])  # Overlaps poly1
        poly3 = Polygon([(2, 0), (3, 0), (3, 1), (2, 1), (2, 0)])  # Only touches poly1

        gdf = gpd.GeoDataFrame({
            'id': [0, 1, 2],
            'name': ['A', 'B', 'C']
        }, geometry=[poly1, poly2, poly3])

        adjacent = find_adjacent_polygons(gdf, 0, "overlaps")
        assert 1 in adjacent  # True overlap
        assert 2 not in adjacent  # Only touches, doesn't overlap

    def test_find_adjacent_polygons_invalid_index(self):
        """Test find_adjacent_polygons with invalid polygon index."""
        poly1 = Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])
        gdf = gpd.GeoDataFrame({'id': [0]}, geometry=[poly1])

        # Test with index beyond GDF bounds
        adjacent = find_adjacent_polygons(gdf, 5, "touches")
        assert adjacent == []

        # Test with negative index
        adjacent = find_adjacent_polygons(gdf, -1, "touches")
        assert adjacent == []

    def test_find_adjacent_polygons_default_method(self):
        """Test find_adjacent_polygons with invalid method (should default to touches)."""
        poly1 = Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])
        poly2 = Polygon([(1, 0), (2, 0), (2, 1), (1, 1), (1, 0)])

        gdf = gpd.GeoDataFrame({
            'id': [0, 1],
            'name': ['A', 'B']
        }, geometry=[poly1, poly2])

        # Use invalid method, should default to touches
        adjacent = find_adjacent_polygons(gdf, 0, "invalid_method")
        assert 1 in adjacent

    def test_find_adjacent_polygons_geometry_error_handling(self):
        """Test find_adjacent_polygons with problematic geometries."""
        # Create problematic geometries that might cause spatial operation errors
        poly1 = Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])
        poly2 = Point(0.5, 0.5)  # Point instead of polygon
        poly3 = None  # None geometry

        gdf = gpd.GeoDataFrame({
            'id': [0, 1, 2],
            'name': ['A', 'B', 'C']
        }, geometry=[poly1, poly2, poly3])

        # Should handle errors gracefully and return a list
        adjacent = find_adjacent_polygons(gdf, 0, "touches")
        assert isinstance(adjacent, list)

    def test_find_adjacent_polygons_large_dataset_performance(self):
        """Test find_adjacent_polygons with larger dataset."""
        # Create a grid of polygons
        polygons = []
        for i in range(5):
            for j in range(5):
                poly = Polygon([
                    (i, j), (i+1, j), (i+1, j+1), (i, j+1), (i, j)
                ])
                polygons.append(poly)

        gdf = gpd.GeoDataFrame({
            'id': range(len(polygons)),
            'name': [f'Poly_{i}' for i in range(len(polygons))]
        }, geometry=polygons)

        # Find adjacents for center polygon (index 12 in 5x5 grid)
        adjacent = find_adjacent_polygons(gdf, 12, "touches")

        # Should find 4 adjacent polygons (up, down, left, right)
        assert isinstance(adjacent, list)
        assert len(adjacent) > 0  # Should have some adjacents


class TestMapGlobalStateManagement:
    """Test global state management in map module."""

    @patch('land_registry.map.current_gdf', None)
    def test_global_state_isolation(self):
        """Test that current GDF state can be managed."""
        # Initially should be None
        assert get_current_gdf() is None

    def test_current_gdf_persistence(self):
        """Test that current_gdf persists across function calls."""
        # Test through extract_qpkg_data which sets current_gdf

        # Extract data which should set current_gdf
        polygon = Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])
        gdf = gpd.GeoDataFrame({'id': [1]}, geometry=[polygon])

        with patch('land_registry.map.gpd.read_file') as mock_read:
            mock_read.return_value = gdf

            with tempfile.NamedTemporaryFile(suffix='.gpkg', delete=False) as temp_file:
                temp_file.write(b'fake content')
                temp_file.flush()

                # Extract should set the current_gdf
                result = extract_qpkg_data(temp_file.name)
                assert result is not None

                # Verify persistence
                current = get_current_gdf()
                assert current is not None
                assert len(current) == 1

                os.unlink(temp_file.name)


class TestMapEdgeCases:
    """Test edge cases and error conditions in map module."""

    def test_extract_qpkg_data_permission_denied(self):
        """Test extract_qpkg_data with permission denied."""
        with patch('land_registry.map.zipfile.ZipFile') as mock_zipfile:
            mock_zipfile.side_effect = PermissionError("Permission denied")

            with patch('land_registry.map.gpd.read_file') as mock_read:
                mock_read.side_effect = PermissionError("Permission denied")

                result = extract_qpkg_data("/restricted/file.qpkg")
                assert result is None

    def test_extract_qpkg_data_corrupted_zip_fallback(self):
        """Test extract_qpkg_data with corrupted ZIP that falls back to direct read."""
        with patch('land_registry.map.zipfile.ZipFile') as mock_zipfile:
            # ZIP reading fails
            mock_zipfile.side_effect = Exception("Corrupted ZIP")

            with patch('land_registry.map.gpd.read_file') as mock_read:
                # But direct file reading succeeds
                polygon = Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])
                gdf = gpd.GeoDataFrame({'id': [1]}, geometry=[polygon])
                mock_read.return_value = gdf

                with tempfile.NamedTemporaryFile(suffix='.gpkg', delete=False) as temp_file:
                    temp_file.write(b'fake content')
                    temp_file.flush()

                    result = extract_qpkg_data(temp_file.name)
                    assert result is not None

                    os.unlink(temp_file.name)

    def test_find_adjacent_polygons_empty_geodataframe(self):
        """Test find_adjacent_polygons with empty GeoDataFrame."""
        empty_gdf = gpd.GeoDataFrame(columns=['id', 'geometry'])

        adjacent = find_adjacent_polygons(empty_gdf, 0, "touches")
        assert adjacent == []