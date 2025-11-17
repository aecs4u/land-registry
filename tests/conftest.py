import pytest
import tempfile
import json
import os
from pathlib import Path
from unittest.mock import Mock, patch
from fastapi.testclient import TestClient
import geopandas as gpd
from shapely.geometry import Polygon, Point
import boto3
from moto import mock_aws

from land_registry.main import app
from land_registry.s3_storage import S3Settings, S3Storage


@pytest.fixture
def client():
    """FastAPI test client."""
    return TestClient(app)


@pytest.fixture
def sample_geojson():
    """Sample GeoJSON data for testing."""
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"id": 0, "name": "Test Polygon 1"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]
                }
            },
            {
                "type": "Feature", 
                "properties": {"id": 1, "name": "Test Polygon 2"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[1, 0], [2, 0], [2, 1], [1, 1], [1, 0]]]
                }
            }
        ]
    }


@pytest.fixture
def sample_gdf(sample_geojson):
    """Sample GeoDataFrame for testing."""
    return gpd.read_file(json.dumps(sample_geojson), driver='GeoJSON')


@pytest.fixture
def sample_cadastral_structure():
    """Sample cadastral structure data."""
    return {
        "LOMBARDIA": {
            "BG": {
                "ALBANO_SANT_ALESSANDRO_A": {
                    "name": "ALBANO SANT'ALESSANDRO",
                    "code": "A151",
                    "files": ["MAP_ALBANO_SANT_ALESSANDRO.gpkg", "PLE_ALBANO_SANT_ALESSANDRO.gpkg"]
                }
            }
        },
        "VENETO": {
            "VE": {
                "VENEZIA_L736": {
                    "name": "VENEZIA",
                    "code": "L736",
                    "files": ["MAP_VENEZIA.gpkg", "PLE_VENEZIA.gpkg"]
                }
            }
        }
    }


@pytest.fixture
def temp_qpkg_file():
    """Create a temporary QPKG file for testing."""
    with tempfile.NamedTemporaryFile(suffix='.qpkg', delete=False) as f:
        # Create a minimal ZIP structure that mimics a QPKG
        import zipfile
        with zipfile.ZipFile(f.name, 'w') as zip_file:
            # Add a fake shapefile
            zip_file.writestr('test.shp', b'fake shapefile data')
            zip_file.writestr('test.shx', b'fake shapefile index')
            zip_file.writestr('test.dbf', b'fake shapefile database')
            zip_file.writestr('test.prj', b'fake projection data')
        yield f.name
        os.unlink(f.name)


@pytest.fixture
def temp_gpkg_file():
    """Create a temporary GPKG file for testing."""
    with tempfile.NamedTemporaryFile(suffix='.gpkg', delete=False) as f:
        f.write(b'fake gpkg data')
        yield f.name
        os.unlink(f.name)


@pytest.fixture
def temp_cadastral_data_file(sample_cadastral_structure):
    """Create a temporary cadastral structure file."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(sample_cadastral_structure, f)
        yield f.name
        os.unlink(f.name)


@pytest.fixture
def mock_current_gdf(sample_gdf):
    """Mock the global current_gdf variable."""
    with patch('land_registry.map.current_gdf', sample_gdf):
        yield sample_gdf


@pytest.fixture
def polygon_selection_data():
    """Sample polygon selection data."""
    return {
        "feature_id": 0,
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]
        },
        "touch_method": "touches"
    }


@pytest.fixture
def drawn_polygons_data():
    """Sample drawn polygons data."""
    return {
        "geojson": {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"drawn": True},
                    "geometry": {
                        "type": "Polygon", 
                        "coordinates": [[[2, 2], [3, 2], [3, 3], [2, 3], [2, 2]]]
                    }
                }
            ]
        },
        "filename": "test_drawn_polygons.json"
    }


@pytest.fixture
def s3_settings():
    """S3 settings for testing."""
    return S3Settings(
        s3_bucket_name="test-bucket",
        s3_region="us-east-1",
        aws_access_key_id="test-key",
        aws_secret_access_key="test-secret"
    )


@pytest.fixture
def mock_s3_client():
    """Mock S3 client with moto."""
    with mock_aws():
        # Create S3 client
        client = boto3.client(
            "s3",
            region_name="us-east-1",
            aws_access_key_id="test-key",
            aws_secret_access_key="test-secret"
        )

        # Create test bucket
        client.create_bucket(Bucket="test-bucket")

        yield client


@pytest.fixture
def s3_storage_with_data(mock_s3_client, s3_settings, sample_geojson, sample_cadastral_structure):
    """S3 storage instance with test data."""
    storage = S3Storage(s3_settings)

    # Upload test cadastral structure
    mock_s3_client.put_object(
        Bucket="test-bucket",
        Key="ITALIA/cadastral_structure.json",
        Body=json.dumps(sample_cadastral_structure).encode()
    )

    # Create test shapefile data (minimal)
    test_geojson = json.dumps(sample_geojson)
    mock_s3_client.put_object(
        Bucket="test-bucket",
        Key="ITALIA/test_region/test_province/test_comune.geojson",
        Body=test_geojson.encode()
    )

    # Create test shapefile
    mock_s3_client.put_object(
        Bucket="test-bucket",
        Key="ITALIA/test_region/test_province/test_file.shp",
        Body=b"fake shapefile data"
    )

    return storage


@pytest.fixture
def sample_s3_files():
    """Sample list of S3 file paths."""
    return [
        "ITALIA/LOMBARDIA/BG/MAP_ALBANO_SANT_ALESSANDRO.gpkg",
        "ITALIA/VENETO/VE/MAP_VENEZIA.gpkg",
        "ITALIA/LOMBARDIA/BG/PLE_ALBANO_SANT_ALESSANDRO.gpkg"
    ]


@pytest.fixture
def s3_config_request():
    """Sample S3 configuration request."""
    return {
        "bucket_name": "test-bucket",
        "region": "us-east-1",
        "endpoint_url": None,
        "access_key_id": "test-key",
        "secret_access_key": "test-secret"
    }