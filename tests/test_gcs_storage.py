"""
Tests for the Google Cloud Storage module.

Tests cover:
- GCS settings configuration
- File upload/download operations
- User data operations
- App data operations
- Signed URL generation
- Geospatial file operations

Note: These tests use mocking to avoid actual GCS calls.
"""

import json
import pytest
from unittest.mock import Mock, MagicMock, patch
from datetime import datetime, timedelta

from land_registry.gcs_storage import (
    GCSSettings,
    GCSStorage,
    get_gcs_storage,
    is_gcs_configured,
    gcs_settings,
)


class TestGCSSettings:
    """Test GCS settings configuration."""

    def test_default_settings(self):
        """Test default GCS settings values."""
        settings = GCSSettings()

        assert settings.gcs_bucket_name == "aecs4u-storage"
        assert settings.gcs_app_data_prefix == "land-registry/app-data"
        assert settings.gcs_user_data_prefix == "land-registry/user-data"
        assert settings.gcs_uploads_prefix == "land-registry/uploads"
        assert settings.gcs_exports_prefix == "land-registry/exports"
        assert settings.gcs_signed_url_expiration == 3600

    def test_settings_from_env(self, monkeypatch):
        """Test settings can be loaded from environment variables."""
        monkeypatch.setenv("GCS_GCS_BUCKET_NAME", "custom-bucket")
        monkeypatch.setenv("GCS_GCS_SIGNED_URL_EXPIRATION", "7200")

        settings = GCSSettings()

        assert settings.gcs_bucket_name == "custom-bucket"
        assert settings.gcs_signed_url_expiration == 7200

    def test_allowed_extensions(self):
        """Test default allowed extensions."""
        settings = GCSSettings()

        assert ".gpkg" in settings.gcs_allowed_extensions
        assert ".geojson" in settings.gcs_allowed_extensions
        assert ".shp" in settings.gcs_allowed_extensions
        assert ".qpkg" in settings.gcs_allowed_extensions


class TestGCSStorageInit:
    """Test GCS storage initialization."""

    def test_storage_with_custom_settings(self):
        """Test creating storage with custom settings."""
        custom_settings = GCSSettings(gcs_bucket_name="custom-bucket")
        storage = GCSStorage(settings=custom_settings)

        assert storage.settings.gcs_bucket_name == "custom-bucket"

    def test_storage_with_default_settings(self):
        """Test creating storage with default settings."""
        storage = GCSStorage()

        assert storage.settings == gcs_settings


class TestGCSStorageOperations:
    """Test GCS storage operations with mocking."""

    @pytest.fixture
    def mock_storage(self):
        """Create a GCS storage with mocked client."""
        storage = GCSStorage()
        storage._client = Mock()
        storage._bucket = Mock()
        return storage

    def test_upload_file_from_path(self, mock_storage):
        """Test uploading a file from a path."""
        mock_blob = Mock()
        mock_storage._bucket.blob.return_value = mock_blob

        result = mock_storage.upload_file(
            source_file="/path/to/file.gpkg",
            destination_path="uploads/user123/file.gpkg",
            content_type="application/geopackage",
        )

        mock_storage._bucket.blob.assert_called_once_with("uploads/user123/file.gpkg")
        mock_blob.upload_from_filename.assert_called_once()
        assert "gs://" in result
        assert "file.gpkg" in result

    def test_upload_file_with_metadata(self, mock_storage):
        """Test uploading a file with metadata."""
        mock_blob = Mock()
        mock_storage._bucket.blob.return_value = mock_blob

        metadata = {"uploaded_by": "user123", "version": "1.0"}
        mock_storage.upload_file(
            source_file="/path/to/file.gpkg",
            destination_path="uploads/file.gpkg",
            metadata=metadata,
        )

        assert mock_blob.metadata == metadata

    def test_download_file_to_path(self, mock_storage):
        """Test downloading a file to a local path."""
        mock_blob = Mock()
        mock_storage._bucket.blob.return_value = mock_blob

        result = mock_storage.download_file(
            source_path="uploads/file.gpkg",
            destination_file="/local/path/file.gpkg",
        )

        mock_blob.download_to_filename.assert_called_once_with("/local/path/file.gpkg")
        assert result == "/local/path/file.gpkg"

    def test_download_file_as_bytes(self, mock_storage):
        """Test downloading a file as bytes."""
        mock_blob = Mock()
        mock_blob.download_as_bytes.return_value = b"file content"
        mock_storage._bucket.blob.return_value = mock_blob

        result = mock_storage.download_file(source_path="uploads/file.txt")

        mock_blob.download_as_bytes.assert_called_once()
        assert result == b"file content"

    def test_delete_file(self, mock_storage):
        """Test deleting a file."""
        mock_blob = Mock()
        mock_storage._bucket.blob.return_value = mock_blob

        result = mock_storage.delete_file("uploads/file.gpkg")

        mock_blob.delete.assert_called_once()
        assert result is True

    def test_delete_file_error(self, mock_storage):
        """Test delete file handles errors."""
        mock_blob = Mock()
        mock_blob.delete.side_effect = Exception("Delete failed")
        mock_storage._bucket.blob.return_value = mock_blob

        result = mock_storage.delete_file("uploads/file.gpkg")

        assert result is False

    def test_file_exists(self, mock_storage):
        """Test checking if file exists."""
        mock_blob = Mock()
        mock_blob.exists.return_value = True
        mock_storage._bucket.blob.return_value = mock_blob

        result = mock_storage.file_exists("uploads/file.gpkg")

        assert result is True

    def test_list_files(self, mock_storage):
        """Test listing files with prefix."""
        mock_blob1 = Mock()
        mock_blob1.name = "uploads/file1.gpkg"
        mock_blob1.size = 1000
        mock_blob1.content_type = "application/geopackage"
        mock_blob1.time_created = datetime.now()
        mock_blob1.updated = datetime.now()
        mock_blob1.metadata = {"key": "value"}

        mock_blob2 = Mock()
        mock_blob2.name = "uploads/file2.gpkg"
        mock_blob2.size = 2000
        mock_blob2.content_type = "application/geopackage"
        mock_blob2.time_created = datetime.now()
        mock_blob2.updated = datetime.now()
        mock_blob2.metadata = None

        mock_storage._client.list_blobs.return_value = [mock_blob1, mock_blob2]

        result = mock_storage.list_files(prefix="uploads/")

        assert len(result) == 2
        assert result[0]["name"] == "uploads/file1.gpkg"
        assert result[0]["size"] == 1000
        assert result[1]["name"] == "uploads/file2.gpkg"

    def test_get_signed_url(self, mock_storage):
        """Test generating a signed URL."""
        mock_blob = Mock()
        mock_blob.generate_signed_url.return_value = "https://signed-url.example.com"
        mock_storage._bucket.blob.return_value = mock_blob

        result = mock_storage.get_signed_url("uploads/file.gpkg", expiration=7200)

        mock_blob.generate_signed_url.assert_called_once()
        assert result == "https://signed-url.example.com"


class TestUserDataOperations:
    """Test user-specific data operations."""

    @pytest.fixture
    def mock_storage(self):
        """Create a GCS storage with mocked client."""
        storage = GCSStorage()
        storage._client = Mock()
        storage._bucket = Mock()
        return storage

    def test_get_user_data_path(self, mock_storage):
        """Test building user data path."""
        path = mock_storage.get_user_data_path("user123", "preferences.json")

        assert path == "land-registry/user-data/user123/preferences.json"

    def test_save_user_data_dict(self, mock_storage):
        """Test saving user data as dict (JSON)."""
        mock_blob = Mock()
        mock_storage._bucket.blob.return_value = mock_blob

        data = {"theme": "dark", "language": "it"}
        result = mock_storage.save_user_data("user123", "preferences.json", data)

        mock_blob.upload_from_string.assert_called_once()
        call_args = mock_blob.upload_from_string.call_args
        assert '"theme": "dark"' in call_args[0][0]
        assert "gs://" in result

    def test_save_user_data_string(self, mock_storage):
        """Test saving user data as string."""
        mock_blob = Mock()
        mock_storage._bucket.blob.return_value = mock_blob

        result = mock_storage.save_user_data("user123", "notes.txt", "Some notes")

        mock_blob.upload_from_string.assert_called_once_with(
            "Some notes", content_type="text/plain"
        )

    def test_save_user_data_bytes(self, mock_storage):
        """Test saving user data as bytes."""
        mock_blob = Mock()
        mock_storage._bucket.blob.return_value = mock_blob

        result = mock_storage.save_user_data("user123", "data.bin", b"binary data")

        mock_blob.upload_from_string.assert_called_once_with(b"binary data")

    def test_load_user_data_json(self, mock_storage):
        """Test loading user data as JSON."""
        mock_blob = Mock()
        mock_blob.exists.return_value = True
        mock_blob.download_as_bytes.return_value = b'{"key": "value"}'
        mock_storage._bucket.blob.return_value = mock_blob

        result = mock_storage.load_user_data("user123", "data.json", as_json=True)

        assert result == {"key": "value"}

    def test_load_user_data_not_found(self, mock_storage):
        """Test loading non-existent user data."""
        mock_blob = Mock()
        mock_blob.exists.return_value = False
        mock_storage._bucket.blob.return_value = mock_blob

        result = mock_storage.load_user_data("user123", "missing.json")

        assert result is None

    def test_list_user_files(self, mock_storage):
        """Test listing user files."""
        mock_storage._client.list_blobs.return_value = []

        mock_storage.list_user_files("user123")

        mock_storage._client.list_blobs.assert_called_once()
        call_args = mock_storage._client.list_blobs.call_args
        assert "user123" in call_args[1]["prefix"]

    def test_delete_user_data(self, mock_storage):
        """Test deleting user data."""
        mock_blob = Mock()
        mock_storage._bucket.blob.return_value = mock_blob

        result = mock_storage.delete_user_data("user123", "file.json")

        mock_blob.delete.assert_called_once()
        assert result is True


class TestAppDataOperations:
    """Test application-wide data operations."""

    @pytest.fixture
    def mock_storage(self):
        """Create a GCS storage with mocked client."""
        storage = GCSStorage()
        storage._client = Mock()
        storage._bucket = Mock()
        return storage

    def test_get_app_data_path(self, mock_storage):
        """Test building app data path."""
        path = mock_storage.get_app_data_path("config.json")

        assert path == "land-registry/app-data/config.json"

    def test_save_app_data(self, mock_storage):
        """Test saving app data."""
        mock_blob = Mock()
        mock_storage._bucket.blob.return_value = mock_blob

        data = {"version": "1.0", "features": ["map", "search"]}
        result = mock_storage.save_app_data("config.json", data)

        mock_blob.upload_from_string.assert_called_once()
        assert "gs://" in result

    def test_load_app_data(self, mock_storage):
        """Test loading app data."""
        mock_blob = Mock()
        mock_blob.exists.return_value = True
        mock_blob.download_as_bytes.return_value = b'{"version": "1.0"}'
        mock_storage._bucket.blob.return_value = mock_blob

        result = mock_storage.load_app_data("config.json", as_json=True)

        assert result == {"version": "1.0"}


class TestUploadOperations:
    """Test user file upload operations."""

    @pytest.fixture
    def mock_storage(self):
        """Create a GCS storage with mocked client."""
        storage = GCSStorage()
        storage._client = Mock()
        storage._bucket = Mock()

        # Mock signed URL generation
        mock_blob = Mock()
        mock_blob.generate_signed_url.return_value = "https://signed-url.example.com"
        storage._bucket.blob.return_value = mock_blob

        return storage

    def test_upload_user_file(self, mock_storage):
        """Test uploading a user file."""
        result = mock_storage.upload_user_file(
            user_id="user123",
            file_content=b"file content",
            filename="cadastral.gpkg",
            content_type="application/geopackage",
        )

        assert "path" in result
        assert "gcs_uri" in result
        assert "download_url" in result
        assert "filename" in result
        assert "original_filename" in result
        assert result["original_filename"] == "cadastral.gpkg"

    def test_upload_user_file_invalid_extension(self, mock_storage):
        """Test upload rejects invalid file extensions."""
        with pytest.raises(ValueError) as exc_info:
            mock_storage.upload_user_file(
                user_id="user123",
                file_content=b"content",
                filename="malicious.exe",
            )

        assert "not allowed" in str(exc_info.value)


class TestGeospatialOperations:
    """Test geospatial file operations."""

    @pytest.fixture
    def mock_storage(self):
        """Create a GCS storage with mocked client."""
        storage = GCSStorage()
        storage._client = Mock()
        storage._bucket = Mock()
        return storage

    def test_save_geojson_for_user(self, mock_storage):
        """Test saving GeoJSON for a user."""
        mock_blob = Mock()
        mock_storage._bucket.blob.return_value = mock_blob

        # Mock GeoDataFrame
        mock_gdf = Mock()
        mock_gdf.to_json.return_value = '{"type": "FeatureCollection", "features": []}'

        result = mock_storage.save_geojson(
            gdf=mock_gdf,
            path="polygons.geojson",
            user_id="user123",
        )

        mock_gdf.to_json.assert_called_once()
        assert "gs://" in result

    def test_save_geojson_app_data(self, mock_storage):
        """Test saving GeoJSON as app data."""
        mock_blob = Mock()
        mock_storage._bucket.blob.return_value = mock_blob

        mock_gdf = Mock()
        mock_gdf.to_json.return_value = '{"type": "FeatureCollection"}'

        result = mock_storage.save_geojson(gdf=mock_gdf, path="boundaries.geojson")

        assert "gs://" in result


class TestGlobalFunctions:
    """Test global helper functions."""

    def test_get_gcs_storage_returns_instance(self):
        """Test get_gcs_storage returns a GCSStorage instance."""
        # Reset global instance
        import land_registry.gcs_storage as gcs_module
        gcs_module._gcs_storage = None

        storage = get_gcs_storage()

        assert isinstance(storage, GCSStorage)

    def test_get_gcs_storage_returns_same_instance(self):
        """Test get_gcs_storage returns the same instance."""
        storage1 = get_gcs_storage()
        storage2 = get_gcs_storage()

        assert storage1 is storage2

    @patch("land_registry.gcs_storage.get_gcs_storage")
    def test_is_gcs_configured_success(self, mock_get_storage):
        """Test is_gcs_configured returns True when configured."""
        mock_storage = Mock()
        mock_storage.bucket.exists.return_value = True
        mock_get_storage.return_value = mock_storage

        result = is_gcs_configured()

        assert result is True

    @patch("land_registry.gcs_storage.get_gcs_storage")
    def test_is_gcs_configured_failure(self, mock_get_storage):
        """Test is_gcs_configured returns False on error."""
        mock_get_storage.side_effect = Exception("Connection failed")

        result = is_gcs_configured()

        assert result is False
