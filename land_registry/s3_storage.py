"""
S3 Storage module - backward compatibility wrapper.

This module provides backward compatibility for code using the old S3Storage API.
It wraps the unified aecs4u-storage package.

For new code, prefer using the unified storage module:
    from land_registry.storage import get_storage, upload_file, download_file

Legacy usage (still supported):
    from land_registry.s3_storage import S3Storage, get_s3_storage
"""

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import geopandas as gpd
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class S3Settings(BaseSettings):
    """
    S3 configuration settings.

    Note: This is kept for backward compatibility. New code should use
    STORAGE_* environment variables with the unified storage module.

    Configuration resolution order:
    1. S3_* prefixed environment variables (legacy)
    2. STORAGE_S3_* / AWS_* environment variables (new unified)
    3. Default values
    """

    # Using underscore prefix for fields that need custom resolution
    _bucket_name: Optional[str] = None
    _region: Optional[str] = None
    endpoint_url: Optional[str] = None
    _aws_access_key_id: Optional[str] = None
    _aws_secret_access_key: Optional[str] = None

    class Config:
        env_prefix = "S3_"
        extra = "ignore"

    def __init__(self, **data):
        super().__init__(**data)
        # Resolve bucket name: S3_BUCKET_NAME > STORAGE_S3_BUCKET > default
        self._bucket_name = (
            os.getenv("S3_BUCKET_NAME")
            or os.getenv("STORAGE_S3_BUCKET")
            or data.get("bucket_name")
            or "apps-aecs4u"
        )
        # Resolve region: S3_REGION > STORAGE_S3_REGION > default
        self._region = (
            os.getenv("S3_REGION")
            or os.getenv("STORAGE_S3_REGION")
            or data.get("region")
            or "eu-west-3"
        )
        # Resolve credentials: S3_* > AWS_* > data > None
        self._aws_access_key_id = (
            os.getenv("S3_AWS_ACCESS_KEY_ID")
            or os.getenv("AWS_ACCESS_KEY_ID")
            or data.get("aws_access_key_id")
        )
        self._aws_secret_access_key = (
            os.getenv("S3_AWS_SECRET_ACCESS_KEY")
            or os.getenv("AWS_SECRET_ACCESS_KEY")
            or data.get("aws_secret_access_key")
        )

    @property
    def bucket_name(self) -> str:
        return self._bucket_name or "apps-aecs4u"

    @property
    def region(self) -> str:
        return self._region or "eu-west-3"

    @property
    def aws_access_key_id(self) -> Optional[str]:
        return self._aws_access_key_id

    @property
    def aws_secret_access_key(self) -> Optional[str]:
        return self._aws_secret_access_key

    # Aliases for backward compatibility
    @property
    def s3_bucket_name(self) -> str:
        return self.bucket_name

    @property
    def s3_region(self) -> str:
        return self.region

    @property
    def s3_endpoint_url(self) -> Optional[str]:
        return self.endpoint_url


class S3Storage:
    """
    S3 storage client - backward compatibility wrapper.

    This class wraps the unified aecs4u-storage package while maintaining
    the original API for backward compatibility.

    For new code, prefer using:
        from land_registry.storage import get_storage, upload_file, download_file
    """

    def __init__(self, settings: Optional[S3Settings] = None):
        self.settings = settings or S3Settings()
        self._manager = None
        self._client = None  # For direct boto3 access if needed

    def _get_manager(self):
        """Get or create the storage manager."""
        if self._manager is None:
            try:
                from aecs4u_storage import StorageConfig, StorageManager

                config = StorageConfig(
                    provider="s3",
                    s3_bucket=self.settings.bucket_name,
                    s3_region=self.settings.region,
                    s3_access_key=self.settings.aws_access_key_id or "",
                    s3_secret_key=self.settings.aws_secret_access_key or "",
                    s3_endpoint=self.settings.endpoint_url or "",
                )
                self._manager = StorageManager(config=config)
                logger.info(f"Storage manager initialized for bucket: {self.settings.bucket_name}")
            except ImportError:
                logger.warning("aecs4u-storage not available, falling back to direct boto3")
                self._manager = None

        return self._manager

    @property
    def client(self):
        """
        Get boto3 S3 client for direct access.

        This is provided for backward compatibility and advanced use cases
        that require direct boto3 access.
        """
        if self._client is None:
            import boto3
            from botocore.config import Config
            from botocore import UNSIGNED

            client_kwargs = {
                "service_name": "s3",
                "region_name": self.settings.region,
            }

            if self.settings.endpoint_url:
                client_kwargs["endpoint_url"] = self.settings.endpoint_url

            # Use credentials if provided, otherwise try unsigned for public buckets
            if self.settings.aws_access_key_id and self.settings.aws_secret_access_key:
                client_kwargs["aws_access_key_id"] = self.settings.aws_access_key_id
                client_kwargs["aws_secret_access_key"] = self.settings.aws_secret_access_key
            else:
                # Try unsigned requests for public buckets
                client_kwargs["config"] = Config(signature_version=UNSIGNED)

            self._client = boto3.client(**client_kwargs)
            logger.info(f"Boto3 S3 client initialized for bucket: {self.settings.bucket_name}")

        return self._client

    @client.setter
    def client(self, value):
        """Setter for client property (useful for testing/mocking)."""
        self._client = value

    @client.deleter
    def client(self):
        """Deleter for client property (useful for testing/mocking)."""
        self._client = None

    def file_exists(self, s3_key: str) -> bool:
        """Check if a file exists in S3."""
        try:
            from botocore.exceptions import ClientError

            self.client.head_object(Bucket=self.settings.bucket_name, Key=s3_key)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return False
            logger.error(f"Error checking file existence: {e}")
            raise

    def list_files(self, prefix: str = "", suffix: str = "") -> List[str]:
        """List files in S3 bucket with optional prefix and suffix filters."""
        try:
            paginator = self.client.get_paginator("list_objects_v2")
            page_iterator = paginator.paginate(
                Bucket=self.settings.bucket_name,
                Prefix=prefix,
            )

            files = []
            for page in page_iterator:
                if "Contents" in page:
                    for obj in page["Contents"]:
                        key = obj["Key"]
                        if suffix and not key.endswith(suffix):
                            continue
                        files.append(key)

            logger.info(f"Found {len(files)} files with prefix '{prefix}' and suffix '{suffix}'")
            return files

        except Exception as e:
            logger.error(f"Error listing files: {e}")
            raise

    def read_geospatial_file(self, s3_key: str) -> Optional[gpd.GeoDataFrame]:
        """Read a geospatial file from S3 and return as GeoDataFrame."""
        try:
            from botocore.exceptions import ClientError

            # Create a temporary file to download S3 object
            with tempfile.NamedTemporaryFile(delete=False, suffix=Path(s3_key).suffix) as temp_file:
                temp_path = temp_file.name

            try:
                # Download file from S3
                self.client.download_file(
                    Bucket=self.settings.bucket_name,
                    Key=s3_key,
                    Filename=temp_path,
                )

                # Read with geopandas
                gdf = gpd.read_file(temp_path)
                logger.info(f"Successfully read {len(gdf)} features from {s3_key}")

                return gdf

            finally:
                # Clean up temporary file
                if os.path.exists(temp_path):
                    os.unlink(temp_path)

        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                logger.warning(f"File not found in S3: {s3_key}")
                return None
            logger.error(f"S3 error reading file {s3_key}: {e}")
            raise
        except Exception as e:
            logger.error(f"Error reading geospatial file {s3_key}: {e}")
            raise

    def get_cadastral_structure(
        self, structure_key: str = "ITALIA/cadastral_structure.json"
    ) -> Optional[Dict[str, Any]]:
        """Read cadastral structure JSON from S3."""
        try:
            from botocore.exceptions import ClientError

            # Create a temporary file to download S3 object
            with tempfile.NamedTemporaryFile(mode="w+", delete=False, suffix=".json") as temp_file:
                temp_path = temp_file.name

            try:
                # Download file from S3
                self.client.download_file(
                    Bucket=self.settings.bucket_name,
                    Key=structure_key,
                    Filename=temp_path,
                )

                # Read JSON
                with open(temp_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                logger.info(f"Successfully read cadastral structure from {structure_key}")
                return data

            finally:
                # Clean up temporary file
                if os.path.exists(temp_path):
                    os.unlink(temp_path)

        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code in ("NoSuchKey", "404"):
                logger.warning(f"Cadastral structure file not found in S3: {structure_key}")
                return None
            logger.error(f"S3 error reading cadastral structure: {e}")
            raise
        except Exception as e:
            logger.error(f"Error reading cadastral structure: {e}")
            raise

    def read_multiple_files(self, s3_keys: List[str]) -> List[Dict[str, Any]]:
        """Read multiple geospatial files from S3 and return as list of layer data."""
        layers = []

        for s3_key in s3_keys:
            try:
                gdf = self.read_geospatial_file(s3_key)

                if gdf is not None and len(gdf) > 0:
                    # Add metadata
                    layer_name = Path(s3_key).stem
                    gdf["layer_name"] = layer_name
                    gdf["source_file"] = s3_key

                    # Add feature IDs if not present
                    if "feature_id" not in gdf.columns:
                        gdf["feature_id"] = range(len(gdf))

                    # Convert to GeoJSON
                    layer_geojson = json.loads(gdf.to_json())

                    layers.append(
                        {
                            "name": layer_name,
                            "file": s3_key,
                            "geojson": layer_geojson,
                            "feature_count": len(gdf),
                            "gdf": gdf,  # Keep GeoDataFrame for combining
                        }
                    )

                    logger.info(f"Successfully processed layer: {layer_name} ({len(gdf)} features)")
                else:
                    logger.warning(f"No data found in file: {s3_key}")

            except Exception as e:
                logger.error(f"Error processing file {s3_key}: {e}")
                continue

        return layers

    async def upload_file(
        self,
        content: bytes,
        key: str,
        content_type: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None,
    ) -> str:
        """
        Upload a file to S3.

        Args:
            content: File content as bytes
            key: S3 key/path for the file
            content_type: Optional MIME type
            metadata: Optional metadata dict

        Returns:
            S3 URI (s3://bucket/key)
        """
        extra_args = {}
        if content_type:
            extra_args["ContentType"] = content_type
        if metadata:
            extra_args["Metadata"] = metadata

        self.client.put_object(
            Bucket=self.settings.bucket_name,
            Key=key,
            Body=content,
            **extra_args,
        )

        return f"s3://{self.settings.bucket_name}/{key}"

    async def download_file(self, key: str) -> bytes:
        """
        Download a file from S3.

        Args:
            key: S3 key/path of the file

        Returns:
            File content as bytes
        """
        response = self.client.get_object(
            Bucket=self.settings.bucket_name,
            Key=key,
        )
        return response["Body"].read()

    async def delete_file(self, key: str) -> bool:
        """
        Delete a file from S3.

        Args:
            key: S3 key/path of the file

        Returns:
            True if successful
        """
        try:
            self.client.delete_object(
                Bucket=self.settings.bucket_name,
                Key=key,
            )
            return True
        except Exception as e:
            logger.error(f"Error deleting file {key}: {e}")
            return False


# Global S3 storage instance
_s3_storage: Optional[S3Storage] = None


def get_s3_storage() -> S3Storage:
    """Get the global S3 storage instance."""
    global _s3_storage
    if _s3_storage is None:
        _s3_storage = S3Storage()
    return _s3_storage


def configure_s3_storage(settings: S3Settings) -> S3Storage:
    """Configure S3 storage with custom settings."""
    global _s3_storage
    _s3_storage = S3Storage(settings)
    return _s3_storage


# Backward compatibility alias
s3_storage = None  # Lazy initialized


def __getattr__(name):
    """Lazy initialization of s3_storage global."""
    if name == "s3_storage":
        global _s3_storage
        if _s3_storage is None:
            _s3_storage = S3Storage()
        return _s3_storage
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
