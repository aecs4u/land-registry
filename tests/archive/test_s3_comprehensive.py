"""
Comprehensive tests for S3Storage to boost coverage.
"""

import pytest
import json
from unittest.mock import patch, MagicMock
import boto3
from botocore.exceptions import ClientError, NoCredentialsError, BotoCoreError
from moto import mock_aws

from land_registry.s3_storage import S3Storage, S3Settings


class TestS3StorageComprehensive:
    """Comprehensive S3Storage tests for maximum coverage."""

    @mock_aws
    def test_s3_storage_client_credentials_from_settings(self):
        """Test S3 client with credentials from settings."""
        settings = S3Settings(
            s3_bucket_name="test-bucket",
            s3_region="us-east-1",
            aws_access_key_id="test-key",
            aws_secret_access_key="test-secret"
        )
        storage = S3Storage(settings)

        # Access client to trigger initialization
        client = storage.client
        assert client is not None
        assert storage._client is client

    @mock_aws
    def test_s3_storage_client_with_endpoint_url(self):
        """Test S3 client with custom endpoint URL."""
        settings = S3Settings(
            s3_bucket_name="test-bucket",
            s3_region="us-east-1",
            s3_endpoint_url="https://custom-s3.example.com",
            aws_access_key_id="test-key",
            aws_secret_access_key="test-secret"
        )
        storage = S3Storage(settings)

        client = storage.client
        assert client is not None

    @mock_aws
    def test_file_exists_client_error(self):
        """Test file_exists with ClientError."""
        settings = S3Settings(s3_bucket_name="test-bucket")
        storage = S3Storage(settings)

        with patch.object(storage, 'client') as mock_client:
            mock_client.head_object.side_effect = ClientError(
                error_response={'Error': {'Code': 'AccessDenied'}},
                operation_name='HeadObject'
            )

            result = storage.file_exists("test-file.json")
            assert result is False

    @mock_aws
    def test_file_exists_not_found_error(self):
        """Test file_exists with 404 error."""
        settings = S3Settings(s3_bucket_name="test-bucket")
        storage = S3Storage(settings)

        with patch.object(storage, 'client') as mock_client:
            mock_client.head_object.side_effect = ClientError(
                error_response={'Error': {'Code': '404'}},
                operation_name='HeadObject'
            )

            result = storage.file_exists("test-file.json")
            assert result is False

    @mock_aws
    def test_list_files_empty_bucket(self):
        """Test list_files with empty bucket."""
        settings = S3Settings(s3_bucket_name="test-bucket")
        storage = S3Storage(settings)

        with patch.object(storage, 'client') as mock_client:
            mock_paginator = MagicMock()
            mock_client.get_paginator.return_value = mock_paginator
            mock_paginator.paginate.return_value = []

            files = storage.list_files()
            assert files == []

    @mock_aws
    def test_list_files_with_prefix_and_suffix(self):
        """Test list_files with both prefix and suffix filters."""
        settings = S3Settings(s3_bucket_name="test-bucket")
        storage = S3Storage(settings)

        with patch.object(storage, 'client') as mock_client:
            mock_paginator = MagicMock()
            mock_client.get_paginator.return_value = mock_paginator
            mock_paginator.paginate.return_value = [
                {
                    'Contents': [
                        {'Key': 'ITALIA/test_map.gpkg'},
                        {'Key': 'ITALIA/test_ple.gpkg'},
                        {'Key': 'ITALIA/other.shp'},
                        {'Key': 'FRANCE/test_map.gpkg'},
                    ]
                }
            ]

            files = storage.list_files(prefix="ITALIA/", suffix=".gpkg")
            assert len(files) == 2
            assert "ITALIA/test_map.gpkg" in files
            assert "ITALIA/test_ple.gpkg" in files
            assert "ITALIA/other.shp" not in files
            assert "FRANCE/test_map.gpkg" not in files

    @mock_aws
    def test_list_files_client_error(self):
        """Test list_files with ClientError."""
        settings = S3Settings(s3_bucket_name="test-bucket")
        storage = S3Storage(settings)

        with patch.object(storage, 'client') as mock_client:
            mock_client.get_paginator.side_effect = ClientError(
                error_response={'Error': {'Code': 'AccessDenied'}},
                operation_name='ListObjects'
            )

            files = storage.list_files()
            assert files == []

    @mock_aws
    def test_list_files_no_contents(self):
        """Test list_files when response has no Contents key."""
        settings = S3Settings(s3_bucket_name="test-bucket")
        storage = S3Storage(settings)

        with patch.object(storage, 'client') as mock_client:
            mock_paginator = MagicMock()
            mock_client.get_paginator.return_value = mock_paginator
            mock_paginator.paginate.return_value = [
                {'CommonPrefixes': []},  # No 'Contents' key
                {'Contents': [{'Key': 'test.gpkg'}]}
            ]

            files = storage.list_files()
            assert len(files) == 1
            assert "test.gpkg" in files

    @mock_aws
    def test_read_geospatial_file_success(self):
        """Test successful geospatial file reading."""
        settings = S3Settings(s3_bucket_name="test-bucket")
        storage = S3Storage(settings)

        # Mock geospatial data
        sample_geojson = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [0, 0]},
                "properties": {"name": "Test"}
            }]
        }

        with patch.object(storage, 'client') as mock_client:
            mock_response = {
                'Body': MagicMock()
            }
            mock_response['Body'].read.return_value = json.dumps(sample_geojson).encode()
            mock_client.get_object.return_value = mock_response

            with patch('land_registry.s3_storage.gpd.read_file') as mock_read:
                import geopandas as gpd
                from shapely.geometry import Point

                gdf = gpd.GeoDataFrame({
                    'name': ['Test']
                }, geometry=[Point(0, 0)])
                mock_read.return_value = gdf

                result = storage.read_geospatial_file("test.geojson")
                assert result is not None
                assert '"type": "FeatureCollection"' in result

    @mock_aws
    def test_read_geospatial_file_not_found(self):
        """Test reading non-existent geospatial file."""
        settings = S3Settings(s3_bucket_name="test-bucket")
        storage = S3Storage(settings)

        with patch.object(storage, 'client') as mock_client:
            mock_client.get_object.side_effect = ClientError(
                error_response={'Error': {'Code': 'NoSuchKey'}},
                operation_name='GetObject'
            )

            result = storage.read_geospatial_file("nonexistent.gpkg")
            assert result is None

    @mock_aws
    def test_read_geospatial_file_geopandas_error(self):
        """Test reading geospatial file with geopandas error."""
        settings = S3Settings(s3_bucket_name="test-bucket")
        storage = S3Storage(settings)

        with patch.object(storage, 'client') as mock_client:
            mock_response = {
                'Body': MagicMock()
            }
            mock_response['Body'].read.return_value = b"invalid geospatial data"
            mock_client.get_object.return_value = mock_response

            with patch('land_registry.s3_storage.gpd.read_file') as mock_read:
                mock_read.side_effect = Exception("Invalid file format")

                result = storage.read_geospatial_file("invalid.gpkg")
                assert result is None

    @mock_aws
    def test_get_cadastral_structure_success(self):
        """Test successful cadastral structure retrieval."""
        settings = S3Settings(s3_bucket_name="test-bucket")
        storage = S3Storage(settings)

        structure_data = {
            "ABRUZZO": {
                "AQ": {
                    "A018_ACCIANO": {
                        "code": "A018",
                        "name": "ACCIANO",
                        "files": ["A018_map.gpkg"]
                    }
                }
            }
        }

        with patch.object(storage, 'client') as mock_client:
            mock_response = {
                'Body': MagicMock()
            }
            mock_response['Body'].read.return_value = json.dumps(structure_data).encode()
            mock_client.get_object.return_value = mock_response

            result = storage.get_cadastral_structure()
            assert result == structure_data

    @mock_aws
    def test_get_cadastral_structure_not_found(self):
        """Test cadastral structure when file not found."""
        settings = S3Settings(s3_bucket_name="test-bucket")
        storage = S3Storage(settings)

        with patch.object(storage, 'client') as mock_client:
            mock_client.get_object.side_effect = ClientError(
                error_response={'Error': {'Code': 'NoSuchKey'}},
                operation_name='GetObject'
            )

            result = storage.get_cadastral_structure()
            assert result is None

    @mock_aws
    def test_get_cadastral_structure_invalid_json(self):
        """Test cadastral structure with invalid JSON."""
        settings = S3Settings(s3_bucket_name="test-bucket")
        storage = S3Storage(settings)

        with patch.object(storage, 'client') as mock_client:
            mock_response = {
                'Body': MagicMock()
            }
            mock_response['Body'].read.return_value = b"invalid json content"
            mock_client.get_object.return_value = mock_response

            result = storage.get_cadastral_structure()
            assert result is None

    @mock_aws
    def test_read_geospatial_file_with_tempfile(self):
        """Test reading geospatial file through temporary file path."""
        settings = S3Settings(s3_bucket_name="test-bucket")
        storage = S3Storage(settings)

        with patch.object(storage, 'client') as mock_client:
            mock_response = {
                'Body': MagicMock()
            }
            mock_response['Body'].read.return_value = b"fake gpkg data"
            mock_client.get_object.return_value = mock_response

            with patch('land_registry.s3_storage.tempfile.NamedTemporaryFile') as mock_temp:
                mock_temp_file = MagicMock()
                mock_temp_file.name = "/tmp/test.gpkg"
                mock_temp_file.__enter__.return_value = mock_temp_file
                mock_temp.return_value = mock_temp_file

                with patch('land_registry.s3_storage.gpd.read_file') as mock_read:
                    import geopandas as gpd
                    from shapely.geometry import Point

                    gdf = gpd.GeoDataFrame({
                        'id': [1]
                    }, geometry=[Point(0, 0)])
                    mock_read.return_value = gdf

                    result = storage.read_geospatial_file("test.gpkg")
                    assert result is not None

    @mock_aws
    def test_client_property_caching(self):
        """Test that client property is cached after first access."""
        settings = S3Settings(s3_bucket_name="test-bucket")
        storage = S3Storage(settings)

        # First access
        client1 = storage.client
        # Second access should return the same client
        client2 = storage.client

        assert client1 is client2
        assert storage._client is client1


class TestS3SettingsComprehensive:
    """Comprehensive S3Settings tests."""

    def test_s3_settings_all_fields(self):
        """Test S3Settings with all fields set."""
        settings = S3Settings(
            s3_bucket_name="custom-bucket",
            s3_region="eu-west-1",
            s3_endpoint_url="https://custom.s3.example.com",
            aws_access_key_id="custom-key",
            aws_secret_access_key="custom-secret"
        )

        assert settings.s3_bucket_name == "custom-bucket"
        assert settings.s3_region == "eu-west-1"
        assert settings.s3_endpoint_url == "https://custom.s3.example.com"
        assert settings.aws_access_key_id == "custom-key"
        assert settings.aws_secret_access_key == "custom-secret"

    def test_s3_settings_partial_fields(self):
        """Test S3Settings with partial field override."""
        settings = S3Settings(
            s3_bucket_name="custom-bucket",
            aws_access_key_id="custom-key"
        )

        assert settings.s3_bucket_name == "custom-bucket"
        assert settings.s3_region == "eu-central-1"  # Default
        assert settings.s3_endpoint_url is None  # Default
        assert settings.aws_access_key_id == "custom-key"
        assert settings.aws_secret_access_key is None  # Default


class TestS3ErrorHandling:
    """Test error handling in S3Storage operations."""

    @mock_aws
    def test_no_credentials_error(self):
        """Test handling of NoCredentialsError."""
        settings = S3Settings(s3_bucket_name="test-bucket")
        storage = S3Storage(settings)

        with patch('land_registry.s3_storage.boto3.client') as mock_boto3:
            mock_boto3.side_effect = NoCredentialsError()

            # Should handle gracefully
            result = storage.file_exists("test.txt")
            assert result is False

    @mock_aws
    def test_botocore_error(self):
        """Test handling of general BotoCoreError."""
        settings = S3Settings(s3_bucket_name="test-bucket")
        storage = S3Storage(settings)

        with patch.object(storage, 'client') as mock_client:
            mock_client.head_object.side_effect = BotoCoreError()

            result = storage.file_exists("test.txt")
            assert result is False

    @mock_aws
    def test_unexpected_error(self):
        """Test handling of unexpected errors."""
        settings = S3Settings(s3_bucket_name="test-bucket")
        storage = S3Storage(settings)

        with patch.object(storage, 'client') as mock_client:
            mock_client.head_object.side_effect = Exception("Unexpected error")

            result = storage.file_exists("test.txt")
            assert result is False

    @mock_aws
    def test_list_files_unexpected_error(self):
        """Test list_files with unexpected error."""
        settings = S3Settings(s3_bucket_name="test-bucket")
        storage = S3Storage(settings)

        with patch.object(storage, 'client') as mock_client:
            mock_client.get_paginator.side_effect = Exception("Unexpected error")

            files = storage.list_files()
            assert files == []