"""
S3 Storage module for reading cadastral files from AWS S3 bucket.
"""

import boto3
import geopandas as gpd
import tempfile
import os
from pathlib import Path
from typing import Optional, List, Dict, Any
from pydantic_settings import BaseSettings
from botocore.exceptions import ClientError, NoCredentialsError
import logging

logger = logging.getLogger(__name__)


class S3Settings(BaseSettings):
    """S3 configuration settings"""
    s3_bucket_name: str = "catasto-2025"
    s3_region: str = "eu-central-1"
    s3_endpoint_url: Optional[str] = None
    aws_access_key_id: Optional[str] = None
    aws_secret_access_key: Optional[str] = None

    class Config:
        env_prefix = "S3_"


class S3Storage:
    """S3 storage client for reading cadastral files"""

    def __init__(self, settings: Optional[S3Settings] = None):
        self.settings = settings or S3Settings()
        self._client = None

    @property
    def client(self):
        """Lazy initialization of S3 client"""
        if self._client is None:
            try:
                # Create S3 client with optional credentials
                client_kwargs = {
                    "service_name": "s3",
                    "region_name": self.settings.s3_region,
                }

                if self.settings.s3_endpoint_url:
                    client_kwargs["endpoint_url"] = self.settings.s3_endpoint_url

                if self.settings.aws_access_key_id and self.settings.aws_secret_access_key:
                    client_kwargs["aws_access_key_id"] = self.settings.aws_access_key_id
                    client_kwargs["aws_secret_access_key"] = self.settings.aws_secret_access_key

                self._client = boto3.client(**client_kwargs)
                logger.info(f"S3 client initialized for bucket: {self.settings.s3_bucket_name}")

            except Exception as e:
                logger.error(f"Failed to initialize S3 client: {e}")
                raise

        return self._client

    @client.setter
    def client(self, value):
        """Setter for client property (useful for testing/mocking)"""
        self._client = value

    @client.deleter
    def client(self):
        """Deleter for client property (useful for testing/mocking)"""
        self._client = None

    def file_exists(self, s3_key: str) -> bool:
        """Check if a file exists in S3"""
        try:
            self.client.head_object(Bucket=self.settings.s3_bucket_name, Key=s3_key)
            return True
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                return False
            else:
                logger.error(f"Error checking file existence: {e}")
                raise

    def list_files(self, prefix: str = "", suffix: str = "") -> List[str]:
        """List files in S3 bucket with optional prefix and suffix filters"""
        try:
            paginator = self.client.get_paginator('list_objects_v2')
            page_iterator = paginator.paginate(
                Bucket=self.settings.s3_bucket_name,
                Prefix=prefix
            )

            files = []
            for page in page_iterator:
                if 'Contents' in page:
                    for obj in page['Contents']:
                        key = obj['Key']
                        if suffix and not key.endswith(suffix):
                            continue
                        files.append(key)

            logger.info(f"Found {len(files)} files with prefix '{prefix}' and suffix '{suffix}'")
            return files

        except Exception as e:
            logger.error(f"Error listing files: {e}")
            raise

    def read_geospatial_file(self, s3_key: str) -> Optional[gpd.GeoDataFrame]:
        """Read a geospatial file from S3 and return as GeoDataFrame"""
        try:
            # Create a temporary file to download S3 object
            with tempfile.NamedTemporaryFile(delete=False, suffix=Path(s3_key).suffix) as temp_file:
                temp_path = temp_file.name

            try:
                # Download file from S3
                self.client.download_file(
                    Bucket=self.settings.s3_bucket_name,
                    Key=s3_key,
                    Filename=temp_path
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
            if e.response['Error']['Code'] == 'NoSuchKey':
                logger.warning(f"File not found in S3: {s3_key}")
                return None
            else:
                logger.error(f"S3 error reading file {s3_key}: {e}")
                raise
        except Exception as e:
            logger.error(f"Error reading geospatial file {s3_key}: {e}")
            raise

    def get_cadastral_structure(self, structure_key: str = "ITALIA/cadastral_structure.json") -> Optional[Dict[str, Any]]:
        """Read cadastral structure JSON from S3"""
        try:
            import json

            # Create a temporary file to download S3 object
            with tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.json') as temp_file:
                temp_path = temp_file.name

            try:
                # Download file from S3
                self.client.download_file(
                    Bucket=self.settings.s3_bucket_name,
                    Key=structure_key,
                    Filename=temp_path
                )

                # Read JSON
                with open(temp_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                logger.info(f"Successfully read cadastral structure from {structure_key}")
                return data

            finally:
                # Clean up temporary file
                if os.path.exists(temp_path):
                    os.unlink(temp_path)

        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                logger.warning(f"Cadastral structure file not found in S3: {structure_key}")
                return None
            else:
                logger.error(f"S3 error reading cadastral structure: {e}")
                raise
        except Exception as e:
            logger.error(f"Error reading cadastral structure: {e}")
            raise

    def read_multiple_files(self, s3_keys: List[str]) -> List[Dict[str, Any]]:
        """Read multiple geospatial files from S3 and return as list of layer data"""
        layers = []

        for s3_key in s3_keys:
            try:
                gdf = self.read_geospatial_file(s3_key)

                if gdf is not None and len(gdf) > 0:
                    # Add metadata
                    layer_name = Path(s3_key).stem
                    gdf['layer_name'] = layer_name
                    gdf['source_file'] = s3_key

                    # Add feature IDs if not present
                    if 'feature_id' not in gdf.columns:
                        gdf['feature_id'] = range(len(gdf))

                    # Convert to GeoJSON
                    import json
                    layer_geojson = json.loads(gdf.to_json())

                    layers.append({
                        "name": layer_name,
                        "file": s3_key,
                        "geojson": layer_geojson,
                        "feature_count": len(gdf),
                        "gdf": gdf  # Keep GeoDataFrame for combining
                    })

                    logger.info(f"Successfully processed layer: {layer_name} ({len(gdf)} features)")
                else:
                    logger.warning(f"No data found in file: {s3_key}")

            except Exception as e:
                logger.error(f"Error processing file {s3_key}: {e}")
                continue

        return layers


# Global S3 storage instance
s3_storage = S3Storage()


def get_s3_storage() -> S3Storage:
    """Get the global S3 storage instance"""
    return s3_storage


def configure_s3_storage(settings: S3Settings) -> S3Storage:
    """Configure S3 storage with custom settings"""
    global s3_storage
    s3_storage = S3Storage(settings)
    return s3_storage