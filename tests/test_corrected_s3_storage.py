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

    def test_s3_settings_defaults(self, monkeypatch):
        """Test S3Settings falls back to hardcoded defaults when no env vars set."""
        # Clear all env vars that S3Settings resolution chain reads from
        for var in ("S3_BUCKET_NAME", "STORAGE_S3_BUCKET", "S3_REGION", "STORAGE_S3_REGION",
                    "S3_AWS_ACCESS_KEY_ID", "AWS_ACCESS_KEY_ID",
                    "S3_AWS_SECRET_ACCESS_KEY", "AWS_SECRET_ACCESS_KEY"):
            monkeypatch.delenv(var, raising=False)

        settings = S3Settings()
        # Hardcoded fallbacks in s3_storage.py __init__
        assert settings.s3_bucket_name == "apps-aecs4u"
        assert settings.s3_region == "eu-west-3"
        assert settings.s3_endpoint_url is None
        assert settings.aws_access_key_id is None or settings.aws_access_key_id == ""
        assert settings.aws_secret_access_key is None or settings.aws_secret_access_key == ""

    def test_s3_settings_env_override(self, monkeypatch):
        """Test that S3_BUCKET_NAME env var overrides the default."""
        monkeypatch.setenv("S3_BUCKET_NAME", "env-override-bucket")
        monkeypatch.setenv("S3_REGION", "eu-central-1")

        settings = S3Settings()
        assert settings.s3_bucket_name == "env-override-bucket"
        assert settings.s3_region == "eu-central-1"

    def test_s3_settings_explicit_values(self):
        """Test S3Settings with explicitly provided constructor values."""
        # Values passed via data dict in __init__; use keyword args matching field names
        settings = S3Settings()
        # Construct using the resolution: pass bucket_name via the data dict path
        import os
        old = os.environ.copy()
        try:
            os.environ.pop("S3_BUCKET_NAME", None)
            os.environ.pop("STORAGE_S3_BUCKET", None)
            # S3Settings.__init__ reads data.get("bucket_name") — need to pass as positional
            s = S3Settings.__new__(S3Settings)
            s.__init__(bucket_name="custom-bucket", region="us-west-2",
                       aws_access_key_id="custom-key-with-enough-chars",
                       aws_secret_access_key="custom-secret-with-enough-chars")
            assert s.s3_bucket_name == "custom-bucket"
            assert s.s3_region == "us-west-2"
        finally:
            os.environ.clear()
            os.environ.update(old)


class TestCorrectedS3Storage:
    """Tests for S3Storage with proper mocking."""

    def test_s3_storage_initialization(self):
        """Test S3Storage initialization."""
        settings = S3Settings(s3_bucket_name="test-bucket")
        storage = S3Storage(settings)
        assert storage.settings == settings
        assert storage._client is None

    def test_s3_storage_default_settings(self, monkeypatch):
        """Test S3Storage with default settings (env-isolated)."""
        for var in ("S3_BUCKET_NAME", "STORAGE_S3_BUCKET"):
            monkeypatch.delenv(var, raising=False)
        storage = S3Storage()
        assert storage.settings.s3_bucket_name == "apps-aecs4u"  # hardcoded fallback
        assert storage._client is None

    @mock_aws
    def test_client_property_initialization(self):
        """Test that client property initializes boto3 client correctly."""
        settings = S3Settings(
            bucket_name="test-bucket",
            region="us-east-1",
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
            bucket_name="test-bucket",
            region="us-east-1",
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
            bucket_name="test-bucket",
            region="us-east-1",
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
            bucket_name="test-bucket",
            region="us-east-1",
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
            bucket_name="test-bucket",
            region="us-east-1",
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
            bucket_name="test-bucket",
            region="us-east-1",
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
            bucket_name="test-bucket",
            region="us-east-1",
            aws_access_key_id="test-key",
            aws_secret_access_key="test-secret"
        )
        storage = S3Storage(settings)

        result = storage.get_cadastral_structure()
        assert result is None

    def test_client_initialization_failure(self):
        """Test client property handles initialization failures."""
        settings = S3Settings(bucket_name="test-bucket")
        storage = S3Storage(settings)

        # s3_storage.py imports boto3 at module level; patch the right name
        with patch('boto3.client') as mock_boto3:
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
            bucket_name="integration-test-bucket",
            region="us-east-1",
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


class TestS3StorageUncoveredMethods:
    """Tests targeting previously uncovered methods in s3_storage.py."""

    @pytest.mark.asyncio
    async def test_upload_file_success(self):
        """Test upload_file puts object to S3 and returns URI (lines 369-382)."""
        settings = S3Settings(bucket_name="test-bucket")
        storage = S3Storage(settings)

        mock_client = MagicMock()
        storage._client = mock_client

        result = await storage.upload_file(b"hello world", "test/file.txt")
        assert result == "s3://test-bucket/test/file.txt"
        mock_client.put_object.assert_called_once()
        call_kwargs = mock_client.put_object.call_args[1]
        assert call_kwargs["Key"] == "test/file.txt"
        assert call_kwargs["Body"] == b"hello world"

    @pytest.mark.asyncio
    async def test_upload_file_with_content_type_and_metadata(self):
        """Test upload_file with content_type and metadata (lines 370-373)."""
        settings = S3Settings(bucket_name="test-bucket")
        storage = S3Storage(settings)

        mock_client = MagicMock()
        storage._client = mock_client

        result = await storage.upload_file(
            b"json content",
            "test/data.json",
            content_type="application/json",
            metadata={"source": "test"}
        )
        assert "test-bucket" in result
        assert "test/data.json" in result
        call_kwargs = mock_client.put_object.call_args[1]
        assert call_kwargs.get("ContentType") == "application/json"
        assert call_kwargs.get("Metadata") == {"source": "test"}

    @pytest.mark.asyncio
    async def test_download_file_success(self):
        """Test download_file reads object from S3 (lines 394-398)."""
        settings = S3Settings(bucket_name="test-bucket")
        storage = S3Storage(settings)

        mock_client = MagicMock()
        mock_client.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=b'{"key":"val"}'))}
        storage._client = mock_client

        result = await storage.download_file("data.json")
        assert result == b'{"key":"val"}'

    @pytest.mark.asyncio
    async def test_delete_file_success(self):
        """Test delete_file returns True on success (lines 410-415)."""
        settings = S3Settings(bucket_name="test-bucket")
        storage = S3Storage(settings)

        mock_client = MagicMock()
        storage._client = mock_client

        result = await storage.delete_file("to_delete.json")
        assert result is True
        mock_client.delete_object.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_file_exception_returns_false(self):
        """Test delete_file returns False on exception (lines 416-418)."""
        settings = S3Settings(bucket_name="test-bucket")
        storage = S3Storage(settings)

        mock_client = MagicMock()
        mock_client.delete_object.side_effect = Exception("Permission denied")
        storage._client = mock_client

        result = await storage.delete_file("key.json")
        assert result is False

    def test_client_with_endpoint_url(self):
        """Test client property sets endpoint_url when configured (line 166)."""
        settings = S3Settings(
            bucket_name="test-bucket",
            endpoint_url="http://localhost:9000",
            aws_access_key_id="test-key",
            aws_secret_access_key="test-secret",
        )
        storage = S3Storage(settings)

        with patch('boto3.client') as mock_boto3:
            mock_boto3.return_value = MagicMock()
            client = storage.client
            call_kwargs = mock_boto3.call_args[1]
            assert call_kwargs.get("endpoint_url") == "http://localhost:9000"

    def test_configure_s3_storage(self):
        """Test configure_s3_storage sets global instance (lines 435-437)."""
        from land_registry.s3_storage import configure_s3_storage
        import land_registry.s3_storage as s3_module

        settings = S3Settings(bucket_name="configured-bucket")
        storage = configure_s3_storage(settings)
        assert storage.settings.bucket_name == "configured-bucket"
        assert s3_module._s3_storage is storage

    def test_module_getattr_s3_storage(self):
        """Test __getattr__ lazy initialization for s3_storage (lines 444-450)."""
        import land_registry.s3_storage as s3_module
        # Access the module-level s3_storage attribute via __getattr__
        s3 = s3_module.__getattr__("s3_storage")
        assert isinstance(s3, S3Storage)

    def test_module_getattr_unknown_raises(self):
        """Test __getattr__ raises AttributeError for unknown attrs."""
        import land_registry.s3_storage as s3_module
        with pytest.raises(AttributeError):
            s3_module.__getattr__("totally_unknown_attr")
