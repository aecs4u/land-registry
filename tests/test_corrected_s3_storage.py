"""
Corrected S3Storage tests that properly handle the client property and mocking.
"""

import pytest
import json
from unittest.mock import patch, MagicMock
import boto3
from botocore.exceptions import ClientError, NoCredentialsError
from moto import mock_aws

from land_registry.s3_storage import S3Storage, S3Settings


class TestCorrectedS3Settings:
    """Tests for S3Settings with proper validation."""

    def test_s3_settings_defaults(self):
        """Test S3Settings default values."""
        settings = S3Settings()
        assert settings.s3_bucket_name == "catasto-2025"
        assert settings.s3_region == "eu-central-1"
        assert settings.s3_endpoint_url is None
        # AWS credentials may be None or empty string depending on environment
        assert settings.aws_access_key_id is None or settings.aws_access_key_id == ""
        assert settings.aws_secret_access_key is None or settings.aws_secret_access_key == ""

    def test_s3_settings_custom_values(self):
        """Test S3Settings with custom values."""
        settings = S3Settings(
            s3_bucket_name="custom-bucket",
            s3_region="us-west-2",
            s3_endpoint_url="https://custom.endpoint.com",
            aws_access_key_id="custom-key",
            aws_secret_access_key="custom-secret"
        )
        assert settings.s3_bucket_name == "custom-bucket"
        assert settings.s3_region == "us-west-2"
        assert settings.s3_endpoint_url == "https://custom.endpoint.com"
        assert settings.aws_access_key_id == "custom-key"
        assert settings.aws_secret_access_key == "custom-secret"


class TestCorrectedS3Storage:
    """Tests for S3Storage with proper mocking."""

    def test_s3_storage_initialization(self):
        """Test S3Storage initialization."""
        settings = S3Settings(s3_bucket_name="test-bucket")
        storage = S3Storage(settings)
        assert storage.settings == settings
        assert storage._client is None

    def test_s3_storage_default_settings(self):
        """Test S3Storage with default settings."""
        storage = S3Storage()
        assert storage.settings.s3_bucket_name == "catasto-2025"
        assert storage._client is None

    @mock_aws
    def test_client_property_initialization(self):
        """Test that client property initializes boto3 client correctly."""
        settings = S3Settings(
            s3_bucket_name="test-bucket",
            s3_region="us-east-1",
            aws_access_key_id="test-key",
            aws_secret_access_key="test-secret"
        )
        storage = S3Storage(settings)

        # Access client property to trigger initialization
        client = storage.client
        assert client is not None
        assert storage._client is client

        # Second access should return same client
        client2 = storage.client
        assert client2 is client

    @mock_aws
    def test_file_exists_true(self):
        """Test file_exists returns True when file exists."""

        # Create real S3 bucket and object using moto
        s3_client = boto3.client('s3', region_name='us-east-1')
        s3_client.create_bucket(Bucket='test-bucket')
        s3_client.put_object(Bucket='test-bucket', Key='test-file.json', Body=b'test content')

        settings = S3Settings(
            s3_bucket_name="test-bucket",
            s3_region="us-east-1",
            aws_access_key_id="test-key",
            aws_secret_access_key="test-secret"
        )
        storage = S3Storage(settings)

        result = storage.file_exists("test-file.json")
        assert result is True

    @mock_aws
    def test_file_exists_false(self):
        """Test file_exists returns False when file doesn't exist."""

        # Create real S3 bucket using moto
        s3_client = boto3.client('s3', region_name='us-east-1')
        s3_client.create_bucket(Bucket='test-bucket')

        settings = S3Settings(
            s3_bucket_name="test-bucket",
            s3_region="us-east-1",
            aws_access_key_id="test-key",
            aws_secret_access_key="test-secret"
        )
        storage = S3Storage(settings)

        result = storage.file_exists("nonexistent-file.json")
        assert result is False

    @mock_aws
    def test_list_files_with_prefix_and_suffix(self):
        """Test list_files with prefix and suffix filters."""

        # Create real S3 bucket and objects using moto
        s3_client = boto3.client('s3', region_name='us-east-1')
        s3_client.create_bucket(Bucket='test-bucket')

        # Add test files
        test_files = [
            'ITALIA/region1_map.gpkg',
            'ITALIA/region2_map.gpkg',
            'ITALIA/region1_data.shp',
            'FRANCE/region1_map.gpkg'
        ]

        for file_key in test_files:
            s3_client.put_object(Bucket='test-bucket', Key=file_key, Body=b'test content')

        settings = S3Settings(
            s3_bucket_name="test-bucket",
            s3_region="us-east-1",
            aws_access_key_id="test-key",
            aws_secret_access_key="test-secret"
        )
        storage = S3Storage(settings)

        # Test with prefix and suffix
        files = storage.list_files(prefix="ITALIA/", suffix=".gpkg")
        assert len(files) == 2
        assert "ITALIA/region1_map.gpkg" in files
        assert "ITALIA/region2_map.gpkg" in files
        assert "ITALIA/region1_data.shp" not in files
        assert "FRANCE/region1_map.gpkg" not in files

    @mock_aws
    def test_list_files_empty_result(self):
        """Test list_files with no matching files."""

        # Create empty S3 bucket using moto
        s3_client = boto3.client('s3', region_name='us-east-1')
        s3_client.create_bucket(Bucket='test-bucket')

        settings = S3Settings(
            s3_bucket_name="test-bucket",
            s3_region="us-east-1",
            aws_access_key_id="test-key",
            aws_secret_access_key="test-secret"
        )
        storage = S3Storage(settings)

        files = storage.list_files()
        assert files == []

    def test_file_exists_with_client_error_handling(self):
        """Test file_exists with proper ClientError handling."""
        settings = S3Settings(s3_bucket_name="test-bucket")
        storage = S3Storage(settings)

        # Mock the client property to raise ClientError
        mock_client = MagicMock()
        error_response = {'Error': {'Code': 'AccessDenied'}}
        mock_client.head_object.side_effect = ClientError(error_response, 'HeadObject')

        # Replace the client property directly
        storage._client = mock_client

        # Should raise the ClientError, not return False
        with pytest.raises(ClientError):
            storage.file_exists("test-file.json")

    def test_file_exists_with_404_error(self):
        """Test file_exists returns False for 404 errors."""
        settings = S3Settings(s3_bucket_name="test-bucket")
        storage = S3Storage(settings)

        # Mock the client to raise 404 ClientError
        mock_client = MagicMock()
        error_response = {'Error': {'Code': '404'}}
        mock_client.head_object.side_effect = ClientError(error_response, 'HeadObject')

        storage._client = mock_client

        result = storage.file_exists("test-file.json")
        assert result is False

    def test_list_files_with_exception_handling(self):
        """Test list_files raises exceptions properly."""
        settings = S3Settings(s3_bucket_name="test-bucket")
        storage = S3Storage(settings)

        # Mock the client to raise an exception
        mock_client = MagicMock()
        mock_client.get_paginator.side_effect = Exception("Connection error")

        storage._client = mock_client

        # list_files now raises exceptions instead of returning empty list
        with pytest.raises(Exception):
            storage.list_files()

    @mock_aws
    def test_get_cadastral_structure_success(self):
        """Test successful cadastral structure retrieval."""

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

        # Create real S3 bucket and object using moto
        s3_client = boto3.client('s3', region_name='us-east-1')
        s3_client.create_bucket(Bucket='test-bucket')
        # Note: The method uses 'ITALIA/cadastral_structure.json' as default key
        s3_client.put_object(
            Bucket='test-bucket',
            Key='ITALIA/cadastral_structure.json',
            Body=json.dumps(structure_data).encode()
        )

        settings = S3Settings(
            s3_bucket_name="test-bucket",
            s3_region="us-east-1",
            aws_access_key_id="test-key",
            aws_secret_access_key="test-secret"
        )
        storage = S3Storage(settings)

        result = storage.get_cadastral_structure()
        assert result == structure_data

    @mock_aws
    def test_get_cadastral_structure_not_found(self):
        """Test cadastral structure when file not found."""

        # Create empty S3 bucket using moto
        s3_client = boto3.client('s3', region_name='us-east-1')
        s3_client.create_bucket(Bucket='test-bucket')

        settings = S3Settings(
            s3_bucket_name="test-bucket",
            s3_region="us-east-1",
            aws_access_key_id="test-key",
            aws_secret_access_key="test-secret"
        )
        storage = S3Storage(settings)

        result = storage.get_cadastral_structure()
        assert result is None

    def test_client_initialization_failure(self):
        """Test client property handles initialization failures."""
        settings = S3Settings(s3_bucket_name="test-bucket")
        storage = S3Storage(settings)

        # Mock boto3.client to raise an exception
        with patch('land_registry.s3_storage.boto3.client') as mock_boto3:
            mock_boto3.side_effect = NoCredentialsError()

            with pytest.raises(NoCredentialsError):
                _ = storage.client


class TestCorrectedS3Integration:
    """Integration tests for S3Storage functionality."""

    @mock_aws
    def test_end_to_end_workflow(self):
        """Test complete S3 workflow."""

        # Set up test data
        structure_data = {"test": "structure"}

        # Create real S3 bucket and objects using moto
        s3_client = boto3.client('s3', region_name='us-east-1')
        s3_client.create_bucket(Bucket='integration-test-bucket')

        # Add test files - use the correct key path
        s3_client.put_object(
            Bucket='integration-test-bucket',
            Key='ITALIA/cadastral_structure.json',
            Body=json.dumps(structure_data).encode()
        )
        s3_client.put_object(
            Bucket='integration-test-bucket',
            Key='ITALIA/test.gpkg',
            Body=b'fake gpkg content'
        )

        # Test the workflow
        settings = S3Settings(
            s3_bucket_name="integration-test-bucket",
            s3_region="us-east-1",
            aws_access_key_id="test-key",
            aws_secret_access_key="test-secret"
        )
        storage = S3Storage(settings)

        # Test file existence - use the correct key path
        assert storage.file_exists("ITALIA/cadastral_structure.json") is True
        assert storage.file_exists("nonexistent.json") is False

        # Test file listing
        files = storage.list_files(suffix=".gpkg")
        assert "ITALIA/test.gpkg" in files

        # Test cadastral structure retrieval
        structure = storage.get_cadastral_structure()
        assert structure == structure_data
