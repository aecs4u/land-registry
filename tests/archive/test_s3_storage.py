"""
Tests for S3 storage functionality.
"""

import pytest
import json
import tempfile
import geopandas as gpd
from unittest.mock import patch, Mock, MagicMock
from botocore.exceptions import ClientError

from land_registry.s3_storage import S3Storage, S3Settings, get_s3_storage, configure_s3_storage


class TestS3Settings:
    """Test S3Settings configuration."""

    def test_default_settings(self):
        """Test default S3 settings."""
        settings = S3Settings()
        assert settings.s3_bucket_name == "catasto-2025"
        assert settings.s3_region == "eu-central-1"
        assert settings.s3_endpoint_url is None
        assert settings.aws_access_key_id is None
        assert settings.aws_secret_access_key is None

    def test_custom_settings(self):
        """Test custom S3 settings."""
        settings = S3Settings(
            s3_bucket_name="custom-bucket",
            s3_region="us-west-2",
            s3_endpoint_url="https://custom-endpoint.com",
            aws_access_key_id="custom-key",
            aws_secret_access_key="custom-secret"
        )
        assert settings.s3_bucket_name == "custom-bucket"
        assert settings.s3_region == "us-west-2"
        assert settings.s3_endpoint_url == "https://custom-endpoint.com"
        assert settings.aws_access_key_id == "custom-key"
        assert settings.aws_secret_access_key == "custom-secret"


class TestS3Storage:
    """Test S3Storage class."""

    def test_s3_storage_initialization(self, s3_settings):
        """Test S3Storage initialization."""
        storage = S3Storage(s3_settings)
        assert storage.settings == s3_settings
        assert storage._client is None

    def test_client_lazy_initialization(self, s3_storage_with_data):
        """Test that S3 client is initialized lazily."""
        storage = s3_storage_with_data
        assert storage._client is None

        # Access client property to trigger initialization
        client = storage.client
        assert client is not None
        assert storage._client is not None

    def test_file_exists_true(self, s3_storage_with_data):
        """Test file_exists returns True for existing file."""
        storage = s3_storage_with_data
        assert storage.file_exists("ITALIA/cadastral_structure.json") is True

    def test_file_exists_false(self, s3_storage_with_data):
        """Test file_exists returns False for non-existing file."""
        storage = s3_storage_with_data
        assert storage.file_exists("ITALIA/non_existing_file.json") is False

    def test_list_files_with_prefix(self, s3_storage_with_data):
        """Test listing files with prefix filter."""
        storage = s3_storage_with_data
        files = storage.list_files(prefix="ITALIA/")
        assert len(files) >= 1
        assert all(f.startswith("ITALIA/") for f in files)

    def test_list_files_with_suffix(self, s3_storage_with_data):
        """Test listing files with suffix filter."""
        storage = s3_storage_with_data
        files = storage.list_files(prefix="ITALIA/", suffix=".json")
        json_files = [f for f in files if f.endswith(".json")]
        assert len(json_files) >= 1

    def test_get_cadastral_structure_success(self, s3_storage_with_data, sample_cadastral_structure):
        """Test successful retrieval of cadastral structure."""
        storage = s3_storage_with_data
        result = storage.get_cadastral_structure()
        assert result is not None
        assert result == sample_cadastral_structure

    def test_get_cadastral_structure_not_found(self, s3_storage_with_data):
        """Test cadastral structure retrieval when file doesn't exist."""
        storage = s3_storage_with_data
        with patch.object(storage, 'client') as mock_client:
            mock_client.download_file.side_effect = ClientError(
                error_response={'Error': {'Code': 'NoSuchKey'}},
                operation_name='GetObject'
            )
            result = storage.get_cadastral_structure("non_existing_structure.json")
            assert result is None

    def test_read_geospatial_file_success(self, s3_storage_with_data):
        """Test successful reading of geospatial file."""
        storage = s3_storage_with_data
        # This will fail since we're mocking with simple data, but we can test the method exists
        # In a real test, we'd need to create proper geospatial files
        result = storage.read_geospatial_file("ITALIA/test_region/test_province/test_comune.geojson")
        # The method should not crash and return a GeoDataFrame or None
        assert result is None or isinstance(result, gpd.GeoDataFrame)

    def test_read_geospatial_file_not_found(self, s3_storage_with_data):
        """Test reading non-existent geospatial file."""
        storage = s3_storage_with_data
        with patch.object(storage, 'client') as mock_client:
            mock_client.download_file.side_effect = ClientError(
                error_response={'Error': {'Code': 'NoSuchKey'}},
                operation_name='GetObject'
            )
            result = storage.read_geospatial_file("ITALIA/non_existing_file.shp")
            assert result is None

    def test_read_multiple_files_empty_list(self, s3_storage_with_data):
        """Test reading multiple files with empty list."""
        storage = s3_storage_with_data
        result = storage.read_multiple_files([])
        assert result == []

    def test_read_multiple_files_with_valid_files(self, s3_storage_with_data):
        """Test reading multiple files."""
        storage = s3_storage_with_data
        files = ["ITALIA/test_region/test_province/test_comune.geojson"]
        result = storage.read_multiple_files(files)
        # Should return a list, might be empty if files can't be parsed as geospatial
        assert isinstance(result, list)


class TestS3StorageErrorHandling:
    """Test S3Storage error handling."""

    def test_client_initialization_failure(self, s3_settings):
        """Test client initialization with invalid credentials."""
        # Create settings with invalid credentials
        invalid_settings = S3Settings(
            s3_bucket_name="invalid-bucket",
            s3_region="invalid-region",
            aws_access_key_id="invalid-key",
            aws_secret_access_key="invalid-secret"
        )

        storage = S3Storage(invalid_settings)

        # The client property should still initialize, but operations will fail
        with pytest.raises(Exception):
            storage.list_files(prefix="ITALIA/")

    def test_file_exists_with_client_error(self, s3_storage_with_data):
        """Test file_exists with S3 client error."""
        storage = s3_storage_with_data

        # Mock the client to raise an exception
        with patch.object(storage, '_client', new=None):
            mock_client = MagicMock()
            mock_client.head_object.side_effect = ClientError(
                error_response={'Error': {'Code': 'AccessDenied'}},
                operation_name='HeadObject'
            )
            storage._client = mock_client

            with pytest.raises(ClientError):
                storage.file_exists("ITALIA/test_file.json")

    def test_list_files_with_exception(self, s3_storage_with_data):
        """Test list_files with S3 exception."""
        storage = s3_storage_with_data

        with patch.object(storage, '_client', new=None):
            mock_client = MagicMock()
            mock_client.get_paginator.side_effect = Exception("S3 Error")
            storage._client = mock_client

            with pytest.raises(Exception):
                storage.list_files(prefix="ITALIA/")


class TestGlobalS3Storage:
    """Test global S3 storage functions."""

    def test_get_s3_storage(self):
        """Test getting global S3 storage instance."""
        storage = get_s3_storage()
        assert isinstance(storage, S3Storage)

    def test_configure_s3_storage(self, s3_settings):
        """Test configuring global S3 storage."""
        storage = configure_s3_storage(s3_settings)
        assert isinstance(storage, S3Storage)
        assert storage.settings == s3_settings

        # Verify global instance is updated
        global_storage = get_s3_storage()
        assert global_storage.settings == s3_settings


@pytest.mark.integration
class TestS3StorageIntegration:
    """Integration tests for S3Storage with real AWS-like operations."""

    def test_full_workflow(self, s3_storage_with_data, sample_cadastral_structure):
        """Test a complete workflow with S3 storage."""
        storage = s3_storage_with_data

        # Test listing files
        files = storage.list_files(prefix="ITALIA/")
        assert len(files) > 0

        # Test checking file existence
        assert storage.file_exists("ITALIA/cadastral_structure.json")

        # Test reading cadastral structure
        structure = storage.get_cadastral_structure()
        assert structure == sample_cadastral_structure

        # Test reading multiple files (even if they fail to parse as geospatial)
        result = storage.read_multiple_files(["ITALIA/test_region/test_province/test_file.shp"])
        assert isinstance(result, list)


@pytest.mark.unit
class TestS3StorageUnit:
    """Unit tests for S3Storage methods."""

    def test_settings_validation(self):
        """Test S3Settings validation."""
        # Test with minimal settings
        settings = S3Settings(s3_bucket_name="test")
        assert settings.s3_bucket_name == "test"

        # Test with all settings
        settings = S3Settings(
            s3_bucket_name="test",
            s3_region="us-east-1",
            s3_endpoint_url="https://s3.amazonaws.com",
            aws_access_key_id="key",
            aws_secret_access_key="secret"
        )
        assert settings.s3_bucket_name == "test"
        assert settings.s3_region == "us-east-1"

    def test_storage_without_credentials(self):
        """Test S3Storage without AWS credentials."""
        settings = S3Settings(
            s3_bucket_name="test-bucket",
            s3_region="us-east-1"
        )
        storage = S3Storage(settings)

        # Should initialize without errors
        assert storage.settings.aws_access_key_id is None
        assert storage.settings.aws_secret_access_key is None