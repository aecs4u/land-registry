import pytest
import tempfile
import zipfile
import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock
import geopandas as gpd
from shapely.geometry import Polygon, Point

from land_registry.map import extract_qpkg_data, get_current_gdf, find_adjacent_polygons


class TestExtractQpkgData:
    """Tests for QPKG/GPKG data extraction."""
    
    def test_extract_gpkg_data_success(self, sample_gdf):
        """Test successful GPKG data extraction."""
        with tempfile.NamedTemporaryFile(suffix='.gpkg', delete=False) as temp_file:
            # Save sample data as GPKG
            sample_gdf.to_file(temp_file.name, driver='GPKG')
            
            # Extract data
            result = extract_qpkg_data(temp_file.name)
            
            assert result is not None
            # Verify it's valid GeoJSON
            geojson_data = json.loads(result)
            assert geojson_data["type"] == "FeatureCollection"
            assert len(geojson_data["features"]) == len(sample_gdf)
            
            # Check that current_gdf was set
            current_gdf = get_current_gdf()
            assert current_gdf is not None
            assert len(current_gdf) == len(sample_gdf)
            
            os.unlink(temp_file.name)
    
    def test_extract_gpkg_data_file_not_found(self):
        """Test GPKG extraction with non-existent file."""
        result = extract_qpkg_data("nonexistent.gpkg")
        assert result is None
    
    def test_extract_qpkg_data_with_shapefile(self, sample_gdf):
        """Test QPKG extraction containing shapefile."""
        with tempfile.NamedTemporaryFile(suffix='.qpkg', delete=False) as qpkg_file:
            # Create a ZIP file with shapefile components
            with zipfile.ZipFile(qpkg_file.name, 'w') as zip_file:
                # Create temporary shapefile
                with tempfile.TemporaryDirectory() as temp_dir:
                    shp_path = os.path.join(temp_dir, 'test.shp')
                    sample_gdf.to_file(shp_path)
                    
                    # Add shapefile components to ZIP
                    for ext in ['.shp', '.shx', '.dbf', '.prj']:
                        file_path = shp_path.replace('.shp', ext)
                        if os.path.exists(file_path):
                            zip_file.write(file_path, f'test{ext}')
            
            # Extract data
            result = extract_qpkg_data(qpkg_file.name)
            
            assert result is not None
            # Verify it's valid GeoJSON
            geojson_data = json.loads(result)
            assert geojson_data["type"] == "FeatureCollection"
            
            os.unlink(qpkg_file.name)
    
    def test_extract_qpkg_data_with_geojson(self, sample_geojson):
        """Test QPKG extraction containing GeoJSON."""
        with tempfile.NamedTemporaryFile(suffix='.qpkg', delete=False) as qpkg_file:
            # Create a ZIP file with GeoJSON
            with zipfile.ZipFile(qpkg_file.name, 'w') as zip_file:
                zip_file.writestr('test.geojson', json.dumps(sample_geojson))
            
            # Extract data
            result = extract_qpkg_data(qpkg_file.name)
            
            assert result is not None
            # Verify it's valid GeoJSON
            geojson_data = json.loads(result)
            assert geojson_data["type"] == "FeatureCollection"
            
            os.unlink(qpkg_file.name)
    
    def test_extract_qpkg_data_no_geospatial_files(self):
        """Test QPKG extraction with no geospatial files."""
        with tempfile.NamedTemporaryFile(suffix='.qpkg', delete=False) as qpkg_file:
            # Create a ZIP file with non-geospatial files
            with zipfile.ZipFile(qpkg_file.name, 'w') as zip_file:
                zip_file.writestr('readme.txt', 'No geospatial data here')
                zip_file.writestr('image.png', b'fake image data')
            
            # Extract data
            result = extract_qpkg_data(qpkg_file.name)
            
            assert result is None
            
            os.unlink(qpkg_file.name)
    
    def test_extract_qpkg_data_invalid_zip(self):
        """Test QPKG extraction with invalid ZIP file."""
        with tempfile.NamedTemporaryFile(suffix='.qpkg', delete=False) as qpkg_file:
            qpkg_file.write(b'not a zip file')
            qpkg_file.flush()
            
            # This should try to read as a geospatial file directly, but fail
            result = extract_qpkg_data(qpkg_file.name)
            
            assert result is None
            
            os.unlink(qpkg_file.name)
    
    def test_extract_qpkg_fallback_direct_read(self, sample_gdf):
        """Test QPKG fallback to direct file reading."""
        with tempfile.NamedTemporaryFile(suffix='.qpkg', delete=False) as temp_file:
            # Write as GeoJSON directly (not a ZIP)
            geojson_str = sample_gdf.to_json()
            temp_file.write(geojson_str.encode())
            temp_file.flush()
            
            result = extract_qpkg_data(temp_file.name)
            
            assert result is not None
            geojson_data = json.loads(result)
            assert geojson_data["type"] == "FeatureCollection"
            
            os.unlink(temp_file.name)


class TestGetCurrentGdf:
    """Tests for getting current GeoDataFrame."""
    
    @patch('land_registry.map.current_gdf', None)
    def test_get_current_gdf_none(self):
        """Test getting current GDF when it's None."""
        result = get_current_gdf()
        assert result is None
    
    def test_get_current_gdf_exists(self, sample_gdf):
        """Test getting current GDF when it exists."""
        with patch('land_registry.map.current_gdf', sample_gdf):
            result = get_current_gdf()
            assert result is sample_gdf


class TestFindAdjacentPolygons:
    """Tests for finding adjacent polygons."""
    
    def create_adjacent_polygons_gdf(self):
        """Create a GeoDataFrame with adjacent polygons for testing."""
        # Create adjacent squares
        polygons = [
            Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]),  # Polygon 0
            Polygon([(1, 0), (2, 0), (2, 1), (1, 1), (1, 0)]),  # Polygon 1 (touches 0)
            Polygon([(0, 1), (1, 1), (1, 2), (0, 2), (0, 1)]),  # Polygon 2 (touches 0)
            Polygon([(2, 2), (3, 2), (3, 3), (2, 3), (2, 2)]),  # Polygon 3 (isolated)
        ]
        
        gdf = gpd.GeoDataFrame({
            'id': range(len(polygons)),
            'name': [f'Polygon {i}' for i in range(len(polygons))]
        }, geometry=polygons)
        
        return gdf
    
    def create_overlapping_polygons_gdf(self):
        """Create a GeoDataFrame with overlapping polygons for testing."""
        polygons = [
            Polygon([(0, 0), (2, 0), (2, 2), (0, 2), (0, 0)]),  # Large polygon 0
            Polygon([(1, 1), (3, 1), (3, 3), (1, 3), (1, 1)]),  # Overlapping polygon 1
            Polygon([(4, 4), (5, 4), (5, 5), (4, 5), (4, 4)]),  # Isolated polygon 2
        ]
        
        gdf = gpd.GeoDataFrame({
            'id': range(len(polygons)),
            'name': [f'Polygon {i}' for i in range(len(polygons))]
        }, geometry=polygons)
        
        return gdf
    
    def test_find_adjacent_polygons_touches(self):
        """Test finding adjacent polygons using 'touches' method."""
        gdf = self.create_adjacent_polygons_gdf()
        
        # Find polygons adjacent to polygon 0
        adjacent = find_adjacent_polygons(gdf, 0, "touches")
        
        # Should find polygons 1 and 2 (they touch polygon 0)
        assert set(adjacent) == {1, 2}
    
    def test_find_adjacent_polygons_intersects(self):
        """Test finding adjacent polygons using 'intersects' method."""
        gdf = self.create_overlapping_polygons_gdf()
        
        # Find polygons adjacent to polygon 0
        adjacent = find_adjacent_polygons(gdf, 0, "intersects")
        
        # Should find polygon 1 (it intersects with polygon 0)
        assert 1 in adjacent
        assert 2 not in adjacent
    
    def test_find_adjacent_polygons_overlaps(self):
        """Test finding adjacent polygons using 'overlaps' method."""
        gdf = self.create_overlapping_polygons_gdf()
        
        # Find polygons adjacent to polygon 0
        adjacent = find_adjacent_polygons(gdf, 0, "overlaps")
        
        # Should find polygon 1 (it overlaps with polygon 0)
        assert 1 in adjacent
        assert 2 not in adjacent
    
    def test_find_adjacent_polygons_invalid_method(self):
        """Test finding adjacent polygons with invalid method (should default to touches)."""
        gdf = self.create_adjacent_polygons_gdf()
        
        # Use invalid method, should default to 'touches'
        adjacent = find_adjacent_polygons(gdf, 0, "invalid_method")
        
        # Should still find polygons 1 and 2
        assert set(adjacent) == {1, 2}
    
    def test_find_adjacent_polygons_out_of_bounds(self):
        """Test finding adjacent polygons with out-of-bounds index."""
        gdf = self.create_adjacent_polygons_gdf()
        
        # Use index beyond the GDF length
        adjacent = find_adjacent_polygons(gdf, 10, "touches")
        
        # Should return empty list
        assert adjacent == []
    
    def test_find_adjacent_polygons_no_adjacents(self):
        """Test finding adjacent polygons when none exist."""
        gdf = self.create_adjacent_polygons_gdf()
        
        # Find adjacents for isolated polygon 3
        adjacent = find_adjacent_polygons(gdf, 3, "touches")
        
        # Should return empty list
        assert adjacent == []
    
    def test_find_adjacent_polygons_single_polygon(self):
        """Test finding adjacent polygons with only one polygon."""
        single_polygon = [Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])]
        gdf = gpd.GeoDataFrame({
            'id': [0],
            'name': ['Single Polygon']
        }, geometry=single_polygon)
        
        adjacent = find_adjacent_polygons(gdf, 0, "touches")
        
        # Should return empty list
        assert adjacent == []
    
    def test_find_adjacent_polygons_geometry_error(self):
        """Test finding adjacent polygons when geometry operations fail."""
        # Create a GDF with potentially problematic geometry
        polygons = [
            Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]),
            Point(0.5, 0.5),  # Point instead of polygon - might cause issues
        ]
        
        gdf = gpd.GeoDataFrame({
            'id': range(len(polygons)),
            'name': [f'Geom {i}' for i in range(len(polygons))]
        }, geometry=polygons)
        
        # This should handle the error gracefully
        adjacent = find_adjacent_polygons(gdf, 0, "touches")
        
        # Should return a list (possibly empty) without crashing
        assert isinstance(adjacent, list)
    
    def test_find_adjacent_polygons_empty_gdf(self):
        """Test finding adjacent polygons with empty GeoDataFrame."""
        empty_gdf = gpd.GeoDataFrame(columns=['geometry'])
        
        # This should handle gracefully
        adjacent = find_adjacent_polygons(empty_gdf, 0, "touches")
        
        # Should return empty list
        assert adjacent == []


class TestMapIntegration:
    """Integration tests for map functionality."""
    
    def test_extract_and_find_adjacent_workflow(self, sample_gdf):
        """Test complete workflow of extracting data and finding adjacent polygons."""
        # First, extract data (simulate by setting current_gdf)
        with patch('land_registry.map.current_gdf', sample_gdf):
            current = get_current_gdf()
            assert current is not None
            
            # Then find adjacent polygons
            adjacent = find_adjacent_polygons(current, 0, "touches")
            assert isinstance(adjacent, list)
    
    def test_global_state_management(self, sample_gdf):
        """Test that global state is managed correctly."""
        # Test that extract_qpkg_data updates global state
        with tempfile.NamedTemporaryFile(suffix='.gpkg', delete=False) as temp_file:
            sample_gdf.to_file(temp_file.name, driver='GPKG')
            
            result = extract_qpkg_data(temp_file.name)
            assert result is not None
            
            # Check global state was updated
            current = get_current_gdf()
            assert current is not None
            assert len(current) == len(sample_gdf)
            
            os.unlink(temp_file.name)