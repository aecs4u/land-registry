"""
Google Cloud Storage module - backward compatibility wrapper.

This module provides backward compatibility for code using the old GCSStorage API.
It wraps the unified aecs4u-storage package.

For new code, prefer using the unified storage module:
    from land_registry.storage import get_storage, upload_file, download_file

Legacy usage (still supported):
    from land_registry.gcs_storage import GCSStorage, get_gcs_storage
"""

import json
import logging
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, BinaryIO, Dict, List, Optional, Union

from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class GCSSettings(BaseSettings):
    """
    Google Cloud Storage configuration settings.

    Note: This is kept for backward compatibility. New code should use
    STORAGE_* environment variables with the unified storage module.
    """

    # Primary storage bucket for app and user data
    gcs_bucket_name: str = "aecs4u-storage"

    # Optional: separate bucket for user uploads
    gcs_uploads_bucket: Optional[str] = None

    # GCP Project (auto-detected if running on GCP)
    gcp_project_id: Optional[str] = None

    # Path prefixes within the bucket
    gcs_app_data_prefix: str = "land-registry/app-data"
    gcs_user_data_prefix: str = "land-registry/user-data"
    gcs_uploads_prefix: str = "land-registry/uploads"
    gcs_exports_prefix: str = "land-registry/exports"

    # Signed URL settings
    gcs_signed_url_expiration: int = 3600  # 1 hour in seconds

    # Upload settings
    gcs_max_upload_size_mb: int = 100
    gcs_allowed_extensions: List[str] = [
        ".qpkg",
        ".gpkg",
        ".shp",
        ".geojson",
        ".kml",
        ".json",
        ".zip",
    ]

    # Cache settings
    gcs_cache_control: str = "public, max-age=3600"

    class Config:
        env_prefix = "GCS_"
        case_sensitive = False
        extra = "ignore"


# Global settings instance
gcs_settings = GCSSettings()


# Lazy import for google-cloud-storage
_storage_client = None


def _get_storage_client():
    """Lazy import and initialization of GCS client."""
    global _storage_client
    if _storage_client is None:
        try:
            from google.cloud import storage

            _storage_client = storage.Client(project=gcs_settings.gcp_project_id)
            logger.info(f"GCS client initialized for bucket: {gcs_settings.gcs_bucket_name}")
        except ImportError:
            logger.error(
                "google-cloud-storage not installed. Install with: pip install google-cloud-storage"
            )
            raise
        except Exception as e:
            logger.error(f"Failed to initialize GCS client: {e}")
            raise
    return _storage_client


class GCSStorage:
    """
    Google Cloud Storage client - backward compatibility wrapper.

    This class wraps the unified aecs4u-storage package while maintaining
    the original API for backward compatibility.

    For new code, prefer using:
        from land_registry.storage import get_storage, upload_file, download_file
    """

    def __init__(self, settings: Optional[GCSSettings] = None):
        self.settings = settings or gcs_settings
        self._client = None
        self._bucket = None
        self._manager = None

    def _get_manager(self):
        """Get or create the storage manager."""
        if self._manager is None:
            try:
                from aecs4u_storage import StorageConfig, StorageManager

                config = StorageConfig(
                    provider="gcs",
                    gcs_bucket=self.settings.gcs_bucket_name,
                    gcs_project=self.settings.gcp_project_id or "",
                )
                self._manager = StorageManager(config=config)
                logger.info(f"Storage manager initialized for GCS bucket: {self.settings.gcs_bucket_name}")
            except ImportError:
                logger.warning("aecs4u-storage not available, using direct GCS client")
                self._manager = None

        return self._manager

    @property
    def client(self):
        """Lazy initialization of GCS client."""
        if self._client is None:
            self._client = _get_storage_client()
        return self._client

    @property
    def bucket(self):
        """Get the primary storage bucket."""
        if self._bucket is None:
            self._bucket = self.client.bucket(self.settings.gcs_bucket_name)
        return self._bucket

    def get_uploads_bucket(self):
        """Get the uploads bucket (falls back to primary bucket)."""
        bucket_name = self.settings.gcs_uploads_bucket or self.settings.gcs_bucket_name
        return self.client.bucket(bucket_name)

    # -------------------------------------------------------------------------
    # File Operations
    # -------------------------------------------------------------------------

    def upload_file(
        self,
        source_file: Union[str, Path, BinaryIO],
        destination_path: str,
        content_type: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None,
    ) -> str:
        """
        Upload a file to GCS.

        Args:
            source_file: Local file path or file-like object
            destination_path: Path within the bucket
            content_type: MIME type (auto-detected if not provided)
            metadata: Custom metadata to attach to the file

        Returns:
            GCS URI of the uploaded file (gs://bucket/path)
        """
        blob = self.bucket.blob(destination_path)

        if metadata:
            blob.metadata = metadata

        if isinstance(source_file, (str, Path)):
            blob.upload_from_filename(str(source_file), content_type=content_type)
        else:
            blob.upload_from_file(source_file, content_type=content_type)

        logger.info(f"Uploaded file to gs://{self.settings.gcs_bucket_name}/{destination_path}")
        return f"gs://{self.settings.gcs_bucket_name}/{destination_path}"

    def download_file(
        self,
        source_path: str,
        destination_file: Optional[Union[str, Path]] = None,
    ) -> Union[bytes, str]:
        """
        Download a file from GCS.

        Args:
            source_path: Path within the bucket
            destination_file: Local file path (if None, returns bytes)

        Returns:
            File contents as bytes, or the local file path if destination provided
        """
        blob = self.bucket.blob(source_path)

        if destination_file:
            blob.download_to_filename(str(destination_file))
            logger.info(
                f"Downloaded gs://{self.settings.gcs_bucket_name}/{source_path} to {destination_file}"
            )
            return str(destination_file)
        else:
            content = blob.download_as_bytes()
            logger.info(
                f"Downloaded gs://{self.settings.gcs_bucket_name}/{source_path} ({len(content)} bytes)"
            )
            return content

    def delete_file(self, path: str) -> bool:
        """Delete a file from GCS."""
        try:
            blob = self.bucket.blob(path)
            blob.delete()
            logger.info(f"Deleted gs://{self.settings.gcs_bucket_name}/{path}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete {path}: {e}")
            return False

    def file_exists(self, path: str) -> bool:
        """Check if a file exists in GCS."""
        blob = self.bucket.blob(path)
        return blob.exists()

    def list_files(
        self,
        prefix: str = "",
        delimiter: Optional[str] = None,
        max_results: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        List files in GCS with a given prefix.

        Args:
            prefix: Path prefix to filter files
            delimiter: Optional delimiter for directory-like listing
            max_results: Maximum number of results to return

        Returns:
            List of file info dictionaries
        """
        blobs = self.client.list_blobs(
            self.settings.gcs_bucket_name,
            prefix=prefix,
            delimiter=delimiter,
            max_results=max_results,
        )

        files = []
        for blob in blobs:
            files.append(
                {
                    "name": blob.name,
                    "size": blob.size,
                    "content_type": blob.content_type,
                    "created": blob.time_created,
                    "updated": blob.updated,
                    "metadata": blob.metadata,
                }
            )

        logger.info(f"Listed {len(files)} files with prefix '{prefix}'")
        return files

    def get_signed_url(
        self,
        path: str,
        expiration: Optional[int] = None,
        method: str = "GET",
    ) -> str:
        """
        Generate a signed URL for temporary access to a file.

        Args:
            path: Path to the file in GCS
            expiration: URL expiration time in seconds (default from settings)
            method: HTTP method (GET, PUT)

        Returns:
            Signed URL string
        """
        blob = self.bucket.blob(path)
        expiration = expiration or self.settings.gcs_signed_url_expiration

        url = blob.generate_signed_url(
            version="v4",
            expiration=timedelta(seconds=expiration),
            method=method,
        )

        logger.info(f"Generated signed URL for {path} (expires in {expiration}s)")
        return url

    # -------------------------------------------------------------------------
    # User Data Operations
    # -------------------------------------------------------------------------

    def get_user_data_path(self, user_id: str, filename: str) -> str:
        """Build the GCS path for user-specific data."""
        return f"{self.settings.gcs_user_data_prefix}/{user_id}/{filename}"

    def save_user_data(
        self,
        user_id: str,
        filename: str,
        data: Union[bytes, str, Dict],
    ) -> str:
        """
        Save user-specific data to GCS.

        Args:
            user_id: User identifier
            filename: Name of the file
            data: Data to save (bytes, string, or dict for JSON)

        Returns:
            GCS URI of the saved file
        """
        path = self.get_user_data_path(user_id, filename)
        blob = self.bucket.blob(path)

        if isinstance(data, dict):
            content = json.dumps(data, indent=2)
            blob.upload_from_string(content, content_type="application/json")
        elif isinstance(data, str):
            blob.upload_from_string(data, content_type="text/plain")
        else:
            blob.upload_from_string(data)

        logger.info(f"Saved user data: {path}")
        return f"gs://{self.settings.gcs_bucket_name}/{path}"

    def load_user_data(
        self,
        user_id: str,
        filename: str,
        as_json: bool = False,
    ) -> Optional[Union[bytes, str, Dict]]:
        """
        Load user-specific data from GCS.

        Args:
            user_id: User identifier
            filename: Name of the file
            as_json: Parse content as JSON

        Returns:
            File contents, or None if not found
        """
        path = self.get_user_data_path(user_id, filename)
        blob = self.bucket.blob(path)

        if not blob.exists():
            logger.info(f"User data not found: {path}")
            return None

        content = blob.download_as_bytes()

        if as_json:
            return json.loads(content.decode("utf-8"))
        elif filename.endswith((".json", ".txt", ".geojson")):
            return content.decode("utf-8")
        else:
            return content

    def list_user_files(self, user_id: str) -> List[Dict[str, Any]]:
        """List all files for a specific user."""
        prefix = f"{self.settings.gcs_user_data_prefix}/{user_id}/"
        return self.list_files(prefix=prefix)

    def delete_user_data(self, user_id: str, filename: str) -> bool:
        """Delete user-specific data from GCS."""
        path = self.get_user_data_path(user_id, filename)
        return self.delete_file(path)

    # -------------------------------------------------------------------------
    # App Data Operations
    # -------------------------------------------------------------------------

    def get_app_data_path(self, filename: str) -> str:
        """Build the GCS path for application data."""
        return f"{self.settings.gcs_app_data_prefix}/{filename}"

    def save_app_data(self, filename: str, data: Union[bytes, str, Dict]) -> str:
        """Save application-wide data to GCS."""
        path = self.get_app_data_path(filename)
        blob = self.bucket.blob(path)

        if isinstance(data, dict):
            content = json.dumps(data, indent=2)
            blob.upload_from_string(content, content_type="application/json")
        elif isinstance(data, str):
            blob.upload_from_string(data, content_type="text/plain")
        else:
            blob.upload_from_string(data)

        logger.info(f"Saved app data: {path}")
        return f"gs://{self.settings.gcs_bucket_name}/{path}"

    def load_app_data(
        self,
        filename: str,
        as_json: bool = False,
    ) -> Optional[Union[bytes, str, Dict]]:
        """Load application-wide data from GCS."""
        path = self.get_app_data_path(filename)
        blob = self.bucket.blob(path)

        if not blob.exists():
            logger.info(f"App data not found: {path}")
            return None

        content = blob.download_as_bytes()

        if as_json:
            return json.loads(content.decode("utf-8"))
        elif filename.endswith((".json", ".txt", ".geojson")):
            return content.decode("utf-8")
        else:
            return content

    # -------------------------------------------------------------------------
    # Upload Operations (for user file uploads)
    # -------------------------------------------------------------------------

    def upload_user_file(
        self,
        user_id: str,
        file_content: Union[bytes, BinaryIO],
        filename: str,
        content_type: Optional[str] = None,
    ) -> Dict[str, str]:
        """
        Upload a file from a user.

        Args:
            user_id: User identifier
            file_content: File content as bytes or file-like object
            filename: Original filename
            content_type: MIME type

        Returns:
            Dict with upload info (path, url, etc.)
        """
        # Validate extension
        ext = Path(filename).suffix.lower()
        if ext not in self.settings.gcs_allowed_extensions:
            raise ValueError(
                f"File type {ext} not allowed. Allowed: {self.settings.gcs_allowed_extensions}"
            )

        # Generate unique path
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        safe_filename = f"{timestamp}_{filename}"
        path = f"{self.settings.gcs_uploads_prefix}/{user_id}/{safe_filename}"

        blob = self.bucket.blob(path)
        blob.metadata = {
            "uploaded_by": user_id,
            "original_filename": filename,
            "upload_timestamp": datetime.utcnow().isoformat(),
        }

        if isinstance(file_content, bytes):
            blob.upload_from_string(file_content, content_type=content_type)
        else:
            blob.upload_from_file(file_content, content_type=content_type)

        # Generate a download URL
        download_url = self.get_signed_url(path)

        logger.info(f"User {user_id} uploaded file: {path}")

        return {
            "path": path,
            "gcs_uri": f"gs://{self.settings.gcs_bucket_name}/{path}",
            "download_url": download_url,
            "filename": safe_filename,
            "original_filename": filename,
        }

    # -------------------------------------------------------------------------
    # Geospatial File Operations
    # -------------------------------------------------------------------------

    def read_geospatial_file(self, path: str):
        """
        Read a geospatial file from GCS and return as GeoDataFrame.

        Args:
            path: Path to the file in GCS

        Returns:
            GeoDataFrame or None if not found/error
        """
        try:
            import geopandas as gpd

            # Download to temp file
            with tempfile.NamedTemporaryFile(delete=False, suffix=Path(path).suffix) as temp_file:
                temp_path = temp_file.name

            try:
                self.download_file(path, temp_path)
                gdf = gpd.read_file(temp_path)
                logger.info(
                    f"Read {len(gdf)} features from gs://{self.settings.gcs_bucket_name}/{path}"
                )
                return gdf
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)

        except Exception as e:
            logger.error(f"Error reading geospatial file {path}: {e}")
            return None

    def save_geojson(
        self,
        gdf,
        path: str,
        user_id: Optional[str] = None,
    ) -> str:
        """
        Save a GeoDataFrame as GeoJSON to GCS.

        Args:
            gdf: GeoDataFrame to save
            path: Destination path (relative to user or app prefix)
            user_id: If provided, saves to user data path

        Returns:
            GCS URI of the saved file
        """
        geojson_str = gdf.to_json()

        if user_id:
            return self.save_user_data(user_id, path, geojson_str)
        else:
            return self.save_app_data(path, geojson_str)


# Global storage instance
_gcs_storage: Optional[GCSStorage] = None


def get_gcs_storage() -> GCSStorage:
    """Get the global GCS storage instance."""
    global _gcs_storage
    if _gcs_storage is None:
        _gcs_storage = GCSStorage()
    return _gcs_storage


def is_gcs_configured() -> bool:
    """Check if GCS is properly configured."""
    try:
        storage = get_gcs_storage()
        # Try to access the bucket
        storage.bucket.exists()
        return True
    except Exception:
        return False
