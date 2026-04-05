"""
Unit tests for land_registry/storage.py.

Tests all storage functions by mocking the StorageManager.
Uses pytest-asyncio for async tests.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import geopandas as gpd
import pytest
import pytest_asyncio
from shapely.geometry import Point

import land_registry.storage as storage_module
from land_registry.storage import (
    close_storage,
    delete_file,
    delete_user_data,
    download_file,
    download_geospatial,
    file_exists,
    get_download_url,
    get_file_metadata,
    get_public_url,
    get_storage,
    get_upload_url,
    init_storage,
    list_files,
    list_user_files,
    load_app_data,
    load_user_data,
    save_app_data,
    save_drawn_polygons,
    save_user_data,
    upload_file,
    upload_geojson,
)


@pytest.fixture(autouse=True)
def reset_storage_manager():
    """Reset the global _storage_manager before and after each test."""
    storage_module._storage_manager = None
    yield
    storage_module._storage_manager = None


def _make_mock_manager():
    """Create a fully mocked StorageManager."""
    manager = MagicMock()
    manager.upload = AsyncMock(return_value=MagicMock(success=True, key="test/file.gpkg"))
    manager.download = AsyncMock(return_value=b"file_content")
    manager.delete = AsyncMock(return_value=True)
    manager.exists = AsyncMock(return_value=True)
    manager.get_metadata = AsyncMock(return_value=MagicMock(key="test/file.gpkg", size=100))
    manager.list_files = AsyncMock(return_value=MagicMock(files=[], continuation_token=None))
    manager.get_upload_url = AsyncMock(return_value=MagicMock(url="https://example.com/upload", key="test.gpkg"))
    manager.get_download_url = AsyncMock(return_value=MagicMock(url="https://example.com/download"))
    manager.get_public_url = MagicMock(return_value="https://cdn.example.com/test.gpkg")
    manager.close = AsyncMock()
    return manager


# ---------------------------------------------------------------------------
# init_storage and get_storage
# ---------------------------------------------------------------------------

class TestInitStorage:

    def test_init_storage_creates_manager(self):
        mock_manager = _make_mock_manager()
        with patch("land_registry.storage.StorageManager", return_value=mock_manager):
            with patch("land_registry.storage.StorageConfig"):
                result = init_storage()
        assert result is mock_manager
        assert storage_module._storage_manager is mock_manager

    def test_get_storage_creates_on_first_call(self):
        mock_manager = _make_mock_manager()
        with patch("land_registry.storage.StorageManager", return_value=mock_manager):
            with patch("land_registry.storage.StorageConfig"):
                result = get_storage()
        assert result is mock_manager

    def test_get_storage_returns_cached(self):
        mock_manager = _make_mock_manager()
        storage_module._storage_manager = mock_manager
        result = get_storage()
        assert result is mock_manager


# ---------------------------------------------------------------------------
# upload_file
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestUploadFile:

    async def test_upload_file_delegates_to_manager(self):
        mock_manager = _make_mock_manager()
        storage_module._storage_manager = mock_manager
        result = await upload_file(b"content", "file.gpkg")
        mock_manager.upload.assert_called_once()
        assert result.success is True

    async def test_upload_file_with_folder(self):
        mock_manager = _make_mock_manager()
        storage_module._storage_manager = mock_manager
        await upload_file(b"content", "file.gpkg", folder="cadastral")
        call_kwargs = mock_manager.upload.call_args.kwargs
        assert call_kwargs.get("folder") == "cadastral"

    async def test_upload_file_with_metadata(self):
        mock_manager = _make_mock_manager()
        storage_module._storage_manager = mock_manager
        await upload_file(b"data", "file.gpkg", metadata={"key": "value"})
        mock_manager.upload.assert_called_once()


# ---------------------------------------------------------------------------
# download_file
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestDownloadFile:

    async def test_download_returns_bytes(self):
        mock_manager = _make_mock_manager()
        storage_module._storage_manager = mock_manager
        result = await download_file("test/file.gpkg")
        assert result == b"file_content"

    async def test_download_calls_manager(self):
        mock_manager = _make_mock_manager()
        storage_module._storage_manager = mock_manager
        await download_file("some/key")
        mock_manager.download.assert_called_once_with("some/key")


# ---------------------------------------------------------------------------
# delete_file
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestDeleteFile:

    async def test_delete_returns_true(self):
        mock_manager = _make_mock_manager()
        storage_module._storage_manager = mock_manager
        result = await delete_file("test/file.gpkg")
        assert result is True

    async def test_delete_calls_manager(self):
        mock_manager = _make_mock_manager()
        storage_module._storage_manager = mock_manager
        await delete_file("some/key")
        mock_manager.delete.assert_called_once_with("some/key")


# ---------------------------------------------------------------------------
# file_exists
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestFileExists:

    async def test_file_exists_true(self):
        mock_manager = _make_mock_manager()
        storage_module._storage_manager = mock_manager
        result = await file_exists("test/file.gpkg")
        assert result is True

    async def test_file_exists_false(self):
        mock_manager = _make_mock_manager()
        mock_manager.exists = AsyncMock(return_value=False)
        storage_module._storage_manager = mock_manager
        result = await file_exists("missing/file.gpkg")
        assert result is False


# ---------------------------------------------------------------------------
# get_file_metadata
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestGetFileMetadata:

    async def test_returns_metadata(self):
        mock_manager = _make_mock_manager()
        storage_module._storage_manager = mock_manager
        result = await get_file_metadata("test/file.gpkg")
        assert result is not None


# ---------------------------------------------------------------------------
# list_files
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestListFiles:

    async def test_list_files_returns_result(self):
        mock_manager = _make_mock_manager()
        storage_module._storage_manager = mock_manager
        result = await list_files(prefix="ITALIA/")
        assert result is not None

    async def test_list_files_with_limit(self):
        mock_manager = _make_mock_manager()
        storage_module._storage_manager = mock_manager
        await list_files(limit=50)
        mock_manager.list_files.assert_called_once()


# ---------------------------------------------------------------------------
# get_upload_url / get_download_url / get_public_url
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestPresignedUrls:

    async def test_get_upload_url(self):
        mock_manager = _make_mock_manager()
        storage_module._storage_manager = mock_manager
        result = await get_upload_url("file.gpkg", folder="uploads")
        assert result.url == "https://example.com/upload"

    async def test_get_download_url(self):
        mock_manager = _make_mock_manager()
        storage_module._storage_manager = mock_manager
        result = await get_download_url("test/file.gpkg")
        assert result.url == "https://example.com/download"

    def test_get_public_url(self):
        mock_manager = _make_mock_manager()
        storage_module._storage_manager = mock_manager
        result = get_public_url("test/file.gpkg")
        assert "cdn.example.com" in result


# ---------------------------------------------------------------------------
# upload_geojson
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestUploadGeojson:

    async def test_upload_geojson_from_gdf(self):
        gdf = gpd.GeoDataFrame(
            {"id": [1]},
            geometry=[Point(12.5, 41.5)],
            crs="EPSG:4326"
        )
        mock_manager = _make_mock_manager()
        storage_module._storage_manager = mock_manager
        result = await upload_geojson(gdf, "test.geojson", folder="cadastral")
        mock_manager.upload.assert_called_once()

    async def test_upload_geojson_with_user_id(self):
        gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[Point(12.5, 41.5)], crs="EPSG:4326")
        mock_manager = _make_mock_manager()
        storage_module._storage_manager = mock_manager
        await upload_geojson(gdf, "test.geojson", user_id="user1")
        # Verify folder contains user_id path
        call_kwargs = mock_manager.upload.call_args.kwargs
        assert "users/user1" in call_kwargs.get("folder", "")


# ---------------------------------------------------------------------------
# save_drawn_polygons
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestSaveDrawnPolygons:

    async def test_save_with_auto_filename(self):
        mock_manager = _make_mock_manager()
        storage_module._storage_manager = mock_manager
        geojson = {"type": "FeatureCollection", "features": []}
        await save_drawn_polygons(geojson)
        mock_manager.upload.assert_called_once()

    async def test_save_with_custom_filename(self):
        mock_manager = _make_mock_manager()
        storage_module._storage_manager = mock_manager
        geojson = {"type": "FeatureCollection", "features": []}
        await save_drawn_polygons(geojson, filename="my_drawing.geojson")
        mock_manager.upload.assert_called_once()

    async def test_save_with_user_id(self):
        mock_manager = _make_mock_manager()
        storage_module._storage_manager = mock_manager
        geojson = {"type": "FeatureCollection", "features": []}
        await save_drawn_polygons(geojson, user_id="user1")
        call_kwargs = mock_manager.upload.call_args.kwargs
        assert "users/user1" in call_kwargs.get("folder", "")


# ---------------------------------------------------------------------------
# save_app_data / load_app_data
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestAppData:

    async def test_save_app_data_dict(self):
        mock_manager = _make_mock_manager()
        storage_module._storage_manager = mock_manager
        await save_app_data("config.json", {"key": "value"})
        mock_manager.upload.assert_called_once()

    async def test_save_app_data_string(self):
        mock_manager = _make_mock_manager()
        storage_module._storage_manager = mock_manager
        await save_app_data("config.txt", "text content")
        mock_manager.upload.assert_called_once()

    async def test_save_app_data_bytes(self):
        mock_manager = _make_mock_manager()
        storage_module._storage_manager = mock_manager
        await save_app_data("data.bin", b"binary content")
        mock_manager.upload.assert_called_once()

    async def test_save_app_data_with_subfolder(self):
        mock_manager = _make_mock_manager()
        storage_module._storage_manager = mock_manager
        await save_app_data("cache/structure.json", {"data": "value"})
        call_kwargs = mock_manager.upload.call_args.kwargs
        # Folder should include "app-data/cache"
        assert "app-data" in call_kwargs.get("folder", "")

    async def test_load_app_data_file_exists(self):
        mock_manager = _make_mock_manager()
        mock_manager.exists = AsyncMock(return_value=True)
        mock_manager.download = AsyncMock(return_value=b'{"key": "val"}')
        storage_module._storage_manager = mock_manager
        result = await load_app_data("config.json", as_json=True)
        assert result == {"key": "val"}

    async def test_load_app_data_as_bytes(self):
        mock_manager = _make_mock_manager()
        mock_manager.exists = AsyncMock(return_value=True)
        mock_manager.download = AsyncMock(return_value=b"raw_bytes")
        storage_module._storage_manager = mock_manager
        result = await load_app_data("data.bin")
        assert result == b"raw_bytes"

    async def test_load_app_data_file_not_found(self):
        mock_manager = _make_mock_manager()
        mock_manager.exists = AsyncMock(return_value=False)
        storage_module._storage_manager = mock_manager
        result = await load_app_data("missing.json")
        assert result is None

    async def test_load_app_data_exception_returns_none(self):
        mock_manager = _make_mock_manager()
        mock_manager.exists = AsyncMock(side_effect=Exception("Network error"))
        storage_module._storage_manager = mock_manager
        result = await load_app_data("config.json")
        assert result is None


# ---------------------------------------------------------------------------
# save_user_data / load_user_data
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestUserData:

    async def test_save_user_data_dict(self):
        mock_manager = _make_mock_manager()
        storage_module._storage_manager = mock_manager
        await save_user_data("user1", "settings.json", {"theme": "dark"})
        mock_manager.upload.assert_called_once()

    async def test_save_user_data_with_subfolder(self):
        mock_manager = _make_mock_manager()
        storage_module._storage_manager = mock_manager
        await save_user_data("user1", "drawings/my_drawing.geojson", b"data")
        call_kwargs = mock_manager.upload.call_args.kwargs
        assert "users/user1" in call_kwargs.get("folder", "")

    async def test_load_user_data_exists(self):
        mock_manager = _make_mock_manager()
        mock_manager.exists = AsyncMock(return_value=True)
        mock_manager.download = AsyncMock(return_value=b'{"x":1}')
        storage_module._storage_manager = mock_manager
        result = await load_user_data("user1", "data.json", as_json=True)
        assert result == {"x": 1}

    async def test_load_user_data_not_found(self):
        mock_manager = _make_mock_manager()
        mock_manager.exists = AsyncMock(return_value=False)
        storage_module._storage_manager = mock_manager
        result = await load_user_data("user1", "missing.json")
        assert result is None

    async def test_load_user_data_exception_returns_none(self):
        mock_manager = _make_mock_manager()
        mock_manager.exists = AsyncMock(side_effect=Exception("Error"))
        storage_module._storage_manager = mock_manager
        result = await load_user_data("user1", "data.json")
        assert result is None


# ---------------------------------------------------------------------------
# list_user_files
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestListUserFiles:

    async def test_list_user_files(self):
        mock_manager = _make_mock_manager()
        storage_module._storage_manager = mock_manager
        await list_user_files("user1")
        call_kwargs = mock_manager.list_files.call_args.kwargs
        assert "users/user1/" in call_kwargs.get("prefix", "")

    async def test_list_user_files_with_prefix(self):
        mock_manager = _make_mock_manager()
        storage_module._storage_manager = mock_manager
        await list_user_files("user1", prefix="drawings")
        call_kwargs = mock_manager.list_files.call_args.kwargs
        assert "users/user1/drawings" in call_kwargs.get("prefix", "")


# ---------------------------------------------------------------------------
# delete_user_data
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestDeleteUserData:

    async def test_delete_user_data(self):
        mock_manager = _make_mock_manager()
        storage_module._storage_manager = mock_manager
        result = await delete_user_data("user1", "settings.json")
        assert result is True
        call_args = mock_manager.delete.call_args[0]
        assert "users/user1/settings.json" in call_args[0]


# ---------------------------------------------------------------------------
# close_storage
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestCloseStorage:

    async def test_close_storage(self):
        mock_manager = _make_mock_manager()
        storage_module._storage_manager = mock_manager
        await close_storage()
        mock_manager.close.assert_called_once()
        assert storage_module._storage_manager is None

    async def test_close_storage_no_manager(self):
        """Closing without an active manager is a no-op."""
        storage_module._storage_manager = None
        await close_storage()  # Should not raise
