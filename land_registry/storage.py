"""
Unified storage module using aecs4u-storage package.

This module provides a unified interface for file storage operations,
abstracting away the underlying provider (S3, GCS, Azure, or local).

Usage:
    from land_registry.storage import get_storage, upload_file, download_file

    # Upload a file
    result = await upload_file(content, "myfile.gpkg", folder="cadastral")

    # Download a file
    content = await download_file("cadastral/2024/12/14/myfile.gpkg")

    # Get presigned URL for direct upload
    presigned = await get_upload_url("large-file.gpkg", folder="uploads")
"""

import logging
import json
import tempfile
import os
from typing import Optional, List, Dict, Any, Union, BinaryIO
from pathlib import Path

import geopandas as gpd

# Import from aecs4u-storage (optional)
try:
    from aecs4u_storage import (
        StorageConfig,
        StorageManager,
        setup_storage,
        get_storage_manager,
    )
    from aecs4u_storage.models import (
        StoredFile,
        UploadResult,
        PresignedUrl,
        DeleteResult,
        ListResult,
    )
    _STORAGE_AVAILABLE = True
except ImportError:
    _STORAGE_AVAILABLE = False
    StorageConfig = None
    StorageManager = None
    setup_storage = None
    get_storage_manager = None
    StoredFile = None
    UploadResult = None
    PresignedUrl = None
    DeleteResult = None
    ListResult = None

logger = logging.getLogger(__name__)

# Global storage manager instance
_storage_manager: Optional[StorageManager] = None


def get_storage_config() -> StorageConfig:
    """Get storage configuration from environment variables."""
    return StorageConfig()


def init_storage(config: Optional[StorageConfig] = None) -> StorageManager:
    """
    Initialize the storage manager.

    Args:
        config: Optional StorageConfig. If not provided, uses environment variables.

    Returns:
        StorageManager instance
    """
    global _storage_manager

    if config is None:
        config = get_storage_config()

    _storage_manager = StorageManager(config=config)
    logger.info(f"Storage initialized with provider: {config.provider}")

    return _storage_manager


def get_storage() -> StorageManager:
    """
    Get the global storage manager instance.

    Initializes the manager if not already done.

    Returns:
        StorageManager instance
    """
    global _storage_manager

    if _storage_manager is None:
        _storage_manager = init_storage()

    return _storage_manager


async def upload_file(
    content: Union[bytes, BinaryIO],
    filename: str,
    content_type: Optional[str] = None,
    folder: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    preserve_filename: bool = False,
) -> UploadResult:
    """
    Upload a file to storage.

    Args:
        content: File content as bytes or file-like object
        filename: Original filename
        content_type: MIME type (auto-detected if not provided)
        folder: Optional subfolder (e.g., "cadastral", "uploads")
        metadata: Optional metadata dict
        preserve_filename: If True, keep original filename instead of UUID

    Returns:
        UploadResult with success status and file info
    """
    manager = get_storage()
    return await manager.upload(
        file=content,
        filename=filename,
        content_type=content_type,
        folder=folder,
        metadata=metadata,
        preserve_filename=preserve_filename,
    )


async def download_file(key: str) -> bytes:
    """
    Download a file from storage.

    Args:
        key: Storage key/path of the file

    Returns:
        File content as bytes
    """
    manager = get_storage()
    return await manager.download(key)


async def delete_file(key: str) -> bool:
    """
    Delete a file from storage.

    Args:
        key: Storage key/path of the file

    Returns:
        True if deleted successfully
    """
    manager = get_storage()
    return await manager.delete(key)


async def file_exists(key: str) -> bool:
    """
    Check if a file exists in storage.

    Args:
        key: Storage key/path of the file

    Returns:
        True if file exists
    """
    manager = get_storage()
    return await manager.exists(key)


async def get_file_metadata(key: str) -> Optional[StoredFile]:
    """
    Get metadata for a file.

    Args:
        key: Storage key/path of the file

    Returns:
        StoredFile with metadata, or None if not found
    """
    manager = get_storage()
    return await manager.get_metadata(key)


async def list_files(
    prefix: str = "",
    limit: int = 100,
    continuation_token: Optional[str] = None,
) -> ListResult:
    """
    List files in storage.

    Args:
        prefix: Filter by path prefix
        limit: Maximum number of files to return
        continuation_token: Token for pagination

    Returns:
        ListResult with files and pagination info
    """
    manager = get_storage()
    return await manager.list_files(
        prefix=prefix,
        limit=limit,
        continuation_token=continuation_token,
    )


async def get_upload_url(
    filename: str,
    content_type: Optional[str] = None,
    folder: Optional[str] = None,
    expires_in: Optional[int] = None,
) -> PresignedUrl:
    """
    Get a presigned URL for direct upload.

    Args:
        filename: Original filename
        content_type: MIME type
        folder: Optional subfolder
        expires_in: URL expiry time in seconds

    Returns:
        PresignedUrl with upload URL and metadata
    """
    manager = get_storage()
    return await manager.get_upload_url(
        filename=filename,
        content_type=content_type,
        folder=folder,
        expires_in=expires_in,
    )


async def get_download_url(
    key: str,
    filename: Optional[str] = None,
    expires_in: Optional[int] = None,
) -> PresignedUrl:
    """
    Get a presigned URL for download.

    Args:
        key: Storage key/path of the file
        filename: Optional filename for Content-Disposition
        expires_in: URL expiry time in seconds

    Returns:
        PresignedUrl with download URL
    """
    manager = get_storage()
    return await manager.get_download_url(
        key=key,
        filename=filename,
        expires_in=expires_in,
    )


def get_public_url(key: str) -> Optional[str]:
    """
    Get the public URL for a file (if available).

    Args:
        key: Storage key/path of the file

    Returns:
        Public URL or None if not available
    """
    manager = get_storage()
    return manager.get_public_url(key)


# =============================================================================
# Geospatial-specific operations
# =============================================================================

async def upload_geojson(
    gdf: gpd.GeoDataFrame,
    filename: str,
    folder: Optional[str] = None,
    user_id: Optional[str] = None,
) -> UploadResult:
    """
    Upload a GeoDataFrame as GeoJSON to storage.

    Args:
        gdf: GeoDataFrame to upload
        filename: Target filename (should end with .geojson)
        folder: Optional subfolder
        user_id: Optional user ID for organizing by user

    Returns:
        UploadResult with success status and file info
    """
    # Convert GeoDataFrame to GeoJSON
    geojson_str = gdf.to_json()
    content = geojson_str.encode('utf-8')

    # Build folder path
    if user_id:
        folder = f"users/{user_id}/{folder}" if folder else f"users/{user_id}"

    return await upload_file(
        content=content,
        filename=filename,
        content_type="application/geo+json",
        folder=folder,
        metadata={
            "feature_count": len(gdf),
            "crs": str(gdf.crs) if gdf.crs else None,
        },
    )


async def download_geospatial(key: str) -> Optional[gpd.GeoDataFrame]:
    """
    Download and parse a geospatial file from storage.

    Supports: .gpkg, .geojson, .shp (within archives)

    Args:
        key: Storage key/path of the file

    Returns:
        GeoDataFrame or None if failed
    """
    try:
        content = await download_file(key)

        # Create temp file with appropriate extension
        suffix = Path(key).suffix
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            gdf = gpd.read_file(tmp_path)
            logger.info(f"Read {len(gdf)} features from {key}")
            return gdf
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    except Exception as e:
        logger.error(f"Failed to read geospatial file {key}: {e}")
        return None


async def save_drawn_polygons(
    geojson_data: Dict[str, Any],
    filename: Optional[str] = None,
    user_id: Optional[str] = None,
) -> UploadResult:
    """
    Save user-drawn polygons to storage.

    Args:
        geojson_data: GeoJSON dict with drawn features
        filename: Optional custom filename
        user_id: Optional user ID

    Returns:
        UploadResult with success status
    """
    from datetime import datetime

    if filename is None:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        filename = f"drawn_polygons_{timestamp}.geojson"

    content = json.dumps(geojson_data, indent=2).encode('utf-8')

    folder = "drawn-polygons"
    if user_id:
        folder = f"users/{user_id}/drawn-polygons"

    return await upload_file(
        content=content,
        filename=filename,
        content_type="application/geo+json",
        folder=folder,
        preserve_filename=True,
    )


# =============================================================================
# App data operations (for configuration, caches, etc.)
# =============================================================================

async def save_app_data(
    key: str,
    data: Union[Dict, str, bytes],
) -> UploadResult:
    """
    Save application data to storage.

    Args:
        key: Storage key (e.g., "config.json", "cache/structure.json")
        data: Data to save (dict will be JSON-encoded)

    Returns:
        UploadResult
    """
    if isinstance(data, dict):
        content = json.dumps(data, indent=2).encode('utf-8')
        content_type = "application/json"
    elif isinstance(data, str):
        content = data.encode('utf-8')
        content_type = "text/plain"
    else:
        content = data
        content_type = "application/octet-stream"

    # Extract folder and filename from key
    parts = key.rsplit('/', 1)
    if len(parts) == 2:
        folder, filename = parts
        folder = f"app-data/{folder}"
    else:
        folder = "app-data"
        filename = key

    return await upload_file(
        content=content,
        filename=filename,
        content_type=content_type,
        folder=folder,
        preserve_filename=True,
    )


async def load_app_data(
    key: str,
    as_json: bool = False,
) -> Optional[Union[bytes, Dict]]:
    """
    Load application data from storage.

    Args:
        key: Storage key
        as_json: If True, parse as JSON and return dict

    Returns:
        Data as bytes or dict, or None if not found
    """
    full_key = f"app-data/{key}" if not key.startswith("app-data/") else key

    try:
        if not await file_exists(full_key):
            return None

        content = await download_file(full_key)

        if as_json:
            return json.loads(content.decode('utf-8'))
        return content

    except Exception as e:
        logger.error(f"Failed to load app data {key}: {e}")
        return None


# =============================================================================
# User data operations
# =============================================================================

async def save_user_data(
    user_id: str,
    key: str,
    data: Union[Dict, str, bytes],
) -> UploadResult:
    """
    Save user-specific data to storage.

    Args:
        user_id: User identifier
        key: Storage key within user's space
        data: Data to save

    Returns:
        UploadResult
    """
    if isinstance(data, dict):
        content = json.dumps(data, indent=2).encode('utf-8')
        content_type = "application/json"
    elif isinstance(data, str):
        content = data.encode('utf-8')
        content_type = "text/plain"
    else:
        content = data
        content_type = "application/octet-stream"

    # Build folder path
    parts = key.rsplit('/', 1)
    if len(parts) == 2:
        subfolder, filename = parts
        folder = f"users/{user_id}/{subfolder}"
    else:
        folder = f"users/{user_id}"
        filename = key

    return await upload_file(
        content=content,
        filename=filename,
        content_type=content_type,
        folder=folder,
        preserve_filename=True,
    )


async def load_user_data(
    user_id: str,
    key: str,
    as_json: bool = False,
) -> Optional[Union[bytes, Dict]]:
    """
    Load user-specific data from storage.

    Args:
        user_id: User identifier
        key: Storage key within user's space
        as_json: If True, parse as JSON

    Returns:
        Data as bytes or dict, or None if not found
    """
    full_key = f"users/{user_id}/{key}"

    try:
        if not await file_exists(full_key):
            return None

        content = await download_file(full_key)

        if as_json:
            return json.loads(content.decode('utf-8'))
        return content

    except Exception as e:
        logger.error(f"Failed to load user data {key} for {user_id}: {e}")
        return None


async def list_user_files(
    user_id: str,
    prefix: str = "",
    limit: int = 100,
) -> ListResult:
    """
    List files for a specific user.

    Args:
        user_id: User identifier
        prefix: Optional prefix within user's space
        limit: Maximum files to return

    Returns:
        ListResult with user's files
    """
    full_prefix = f"users/{user_id}/{prefix}" if prefix else f"users/{user_id}/"
    return await list_files(prefix=full_prefix, limit=limit)


async def delete_user_data(user_id: str, key: str) -> bool:
    """
    Delete user-specific data from storage.

    Args:
        user_id: User identifier
        key: Storage key within user's space

    Returns:
        True if deleted successfully
    """
    full_key = f"users/{user_id}/{key}"
    return await delete_file(full_key)


# =============================================================================
# Cleanup
# =============================================================================

async def close_storage() -> None:
    """Close the storage manager and release resources."""
    global _storage_manager

    if _storage_manager is not None:
        await _storage_manager.close()
        _storage_manager = None
        logger.info("Storage manager closed")
