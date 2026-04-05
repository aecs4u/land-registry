"""
Tests for cadastral_utils.py.

Covers:
- CadastralData class (properties, cache_metadata, get_file_availability_stats)
- _calculate_statistics
- _scan_local_cadastral_directory
- _load_cadastral_data_internal (local, S3, JSON fallback)
- load_cadastral_structure (cache hit, cache miss, TTL expiration)
- clear_cache
- get_cadastral_stats
"""

import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import land_registry.cadastral_utils as cu
from land_registry.cadastral_utils import (
    CadastralData,
    _calculate_statistics,
    _scan_local_cadastral_directory,
    clear_cache,
    get_cadastral_stats,
    load_cadastral_structure,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_DATA = {
    "LOMBARDIA": {
        "BG": {
            "ALBANO_A151": {
                "name": "ALBANO SANT'ALESSANDRO",
                "code": "A151",
                "files": ["MAP_ALBANO.gpkg", "PLE_ALBANO.gpkg"],
            }
        }
    },
    "VENETO": {
        "VE": {
            "VENEZIA_L736": {
                "name": "VENEZIA",
                "code": "L736",
                "files": ["MAP_VENEZIA.gpkg"],
            },
            "PADOVA_G224": {
                "name": "PADOVA",
                "code": "G224",
                "files": [],  # municipality with no files
            },
        }
    },
}


@pytest.fixture(autouse=True)
def reset_cache():
    """Clear the module-level cache before each test."""
    clear_cache()
    yield
    clear_cache()


@pytest.fixture
def sample_cadastral_data():
    return CadastralData(SAMPLE_DATA, _calculate_statistics(SAMPLE_DATA), source="local")


# ---------------------------------------------------------------------------
# CadastralData
# ---------------------------------------------------------------------------


class TestCadastralData:
    def test_properties(self, sample_cadastral_data):
        cd = sample_cadastral_data
        assert cd.total_regions == 2
        assert cd.total_provinces == 2
        assert cd.total_municipalities == 3
        assert cd.total_files == 3  # 2 + 1 + 0 (empty list)
        assert cd.source == "local"

    def test_cache_age_increases(self, sample_cadastral_data):
        age1 = sample_cadastral_data.cache_age()
        time.sleep(0.05)
        age2 = sample_cadastral_data.cache_age()
        assert age2 > age1

    def test_cache_metadata_fields(self, sample_cadastral_data):
        meta = sample_cadastral_data.cache_metadata()
        assert "loaded_at" in meta
        assert "age_seconds" in meta
        assert meta["source"] == "local"
        assert "ttl_seconds" in meta
        assert "is_expired" in meta
        assert meta["is_expired"] is False

    def test_cache_metadata_expired(self):
        cd = CadastralData({}, {}, source="json")
        # Force the loaded_at far in the past
        cd.loaded_at = time.time() - 99999
        meta = cd.cache_metadata()
        assert meta["is_expired"] is True

    def test_get_file_availability_stats(self, sample_cadastral_data):
        stats = sample_cadastral_data.get_file_availability_stats()
        assert stats["municipalities_with_files"] == 2
        assert stats["municipalities_without_files"] == 1
        assert stats["total_municipalities"] == 3
        assert stats["coverage_percentage"] == pytest.approx(66.67, abs=0.1)

    def test_get_file_availability_stats_empty(self):
        cd = CadastralData({}, {}, source="local")
        stats = cd.get_file_availability_stats()
        assert stats["municipalities_with_files"] == 0
        assert stats["municipalities_without_files"] == 0
        assert stats["coverage_percentage"] == 0


# ---------------------------------------------------------------------------
# _calculate_statistics
# ---------------------------------------------------------------------------


class TestCalculateStatistics:
    def test_correct_counts(self):
        stats = _calculate_statistics(SAMPLE_DATA)
        assert stats["total_regions"] == 2
        assert stats["total_provinces"] == 2
        assert stats["total_municipalities"] == 3
        assert stats["total_files"] == 3

    def test_empty_data(self):
        stats = _calculate_statistics({})
        assert all(v == 0 for v in stats.values())

    def test_none_data(self):
        stats = _calculate_statistics(None)
        assert all(v == 0 for v in stats.values())

    def test_non_dict_data(self):
        stats = _calculate_statistics("invalid")
        assert all(v == 0 for v in stats.values())


# ---------------------------------------------------------------------------
# _scan_local_cadastral_directory
# ---------------------------------------------------------------------------


class TestScanLocalCadastralDirectory:
    def test_scans_real_directory_structure(self, tmp_path):
        # Create: root/LOMBARDIA/BG/A151_ALBANO/file.gpkg
        gpkg = tmp_path / "LOMBARDIA" / "BG" / "A151_ALBANO"
        gpkg.mkdir(parents=True)
        (gpkg / "MAP_ALBANO.gpkg").write_bytes(b"fake")
        (gpkg / "PLE_ALBANO.gpkg").write_bytes(b"fake")

        result = _scan_local_cadastral_directory(str(tmp_path))

        assert "LOMBARDIA" in result
        assert "BG" in result["LOMBARDIA"]
        muni = result["LOMBARDIA"]["BG"]["A151_ALBANO"]
        assert muni["code"] == "A151"
        assert muni["name"] == "ALBANO"
        assert sorted(muni["files"]) == ["MAP_ALBANO.gpkg", "PLE_ALBANO.gpkg"]

    def test_excludes_municipalities_without_gpkg(self, tmp_path):
        empty_muni = tmp_path / "VENETO" / "VE" / "B001_EMPTY"
        empty_muni.mkdir(parents=True)
        # No .gpkg files

        result = _scan_local_cadastral_directory(str(tmp_path))
        assert result.get("VENETO", {}).get("VE", {}).get("B001_EMPTY") is None

    def test_nonexistent_path_returns_empty(self):
        result = _scan_local_cadastral_directory("/nonexistent/path/xyz")
        assert result == {}

    def test_ignores_non_directory_entries(self, tmp_path):
        # Place a file at region level — should be ignored
        (tmp_path / "not_a_dir.txt").write_text("ignore me")
        region = tmp_path / "TOSCANA" / "FI" / "C001_FIRENZE"
        region.mkdir(parents=True)
        (region / "MAP.gpkg").write_bytes(b"x")

        result = _scan_local_cadastral_directory(str(tmp_path))
        assert "TOSCANA" in result
        assert "not_a_dir.txt" not in result


# ---------------------------------------------------------------------------
# load_cadastral_structure — cache behaviour
# ---------------------------------------------------------------------------


class TestLoadCadastralStructure:
    def _make_mock_cadastral(self, source="local"):
        mock_storage = MagicMock()
        mock_storage.get_cadastral_structure.return_value = None
        return mock_storage

    @patch("land_registry.cadastral_utils._load_cadastral_data_internal")
    def test_loads_and_caches(self, mock_internal):
        mock_internal.return_value = (SAMPLE_DATA, "local")

        result1 = load_cadastral_structure()
        result2 = load_cadastral_structure()  # should use cache

        assert mock_internal.call_count == 1  # only loaded once
        assert result1 is result2

    @patch("land_registry.cadastral_utils._load_cadastral_data_internal")
    def test_cache_bypass(self, mock_internal):
        mock_internal.return_value = (SAMPLE_DATA, "local")

        load_cadastral_structure(use_cache=True)
        load_cadastral_structure(use_cache=False)

        assert mock_internal.call_count == 2

    @patch("land_registry.cadastral_utils._load_cadastral_data_internal")
    def test_cache_ttl_expiration(self, mock_internal):
        mock_internal.return_value = (SAMPLE_DATA, "local")

        result = load_cadastral_structure()
        assert result is not None

        # Simulate cache expiry by backdating loaded_at
        cu._cadastral_cache.loaded_at = time.time() - (cu._cache_ttl_seconds + 1)

        load_cadastral_structure()
        assert mock_internal.call_count == 2  # reloaded after expiry

    @patch("land_registry.cadastral_utils._load_cadastral_data_internal")
    def test_returns_none_when_data_empty(self, mock_internal):
        mock_internal.return_value = (None, "unknown")

        result = load_cadastral_structure()
        assert result is None

    @patch("land_registry.cadastral_utils._load_cadastral_data_internal")
    def test_returns_none_on_exception(self, mock_internal):
        mock_internal.side_effect = RuntimeError("boom")

        result = load_cadastral_structure()
        assert result is None

    @patch("land_registry.cadastral_utils._load_cadastral_data_internal")
    def test_statistics_populated(self, mock_internal):
        mock_internal.return_value = (SAMPLE_DATA, "s3")

        result = load_cadastral_structure()
        assert result.total_regions == 2
        assert result.total_provinces == 2
        assert result.source == "s3"


# ---------------------------------------------------------------------------
# _load_cadastral_data_internal — source selection
# ---------------------------------------------------------------------------


class TestLoadCadastralDataInternal:
    @patch("land_registry.cadastral_utils.cadastral_settings")
    @patch("land_registry.cadastral_utils.get_cadastral_data_root")
    @patch("land_registry.cadastral_utils._scan_local_cadastral_directory")
    def test_prefers_local_when_available(self, mock_scan, mock_root, mock_settings):
        mock_settings.use_local_files = True
        mock_root.return_value = "/some/path"
        mock_scan.return_value = SAMPLE_DATA

        from land_registry.cadastral_utils import _load_cadastral_data_internal
        data, source = _load_cadastral_data_internal()

        assert source == "local"
        assert data is SAMPLE_DATA

    @patch("land_registry.cadastral_utils.cadastral_settings")
    @patch("land_registry.cadastral_utils.get_cadastral_data_root")
    @patch("land_registry.cadastral_utils.get_s3_storage")
    def test_falls_back_to_s3(self, mock_get_s3, mock_root, mock_settings):
        mock_settings.use_local_files = False
        mock_root.return_value = None

        mock_s3 = MagicMock()
        mock_s3.get_cadastral_structure.return_value = SAMPLE_DATA
        mock_get_s3.return_value = mock_s3

        from land_registry.cadastral_utils import _load_cadastral_data_internal
        data, source = _load_cadastral_data_internal()

        assert source == "s3"
        assert data is SAMPLE_DATA

    @patch("land_registry.cadastral_utils.cadastral_settings")
    @patch("land_registry.cadastral_utils.get_cadastral_data_root")
    @patch("land_registry.cadastral_utils.get_s3_storage")
    @patch("land_registry.cadastral_utils.get_cadastral_structure_path")
    def test_falls_back_to_json(self, mock_path, mock_get_s3, mock_root, mock_settings, tmp_path):
        mock_settings.use_local_files = False
        mock_root.return_value = None

        mock_s3 = MagicMock()
        mock_s3.get_cadastral_structure.return_value = None
        mock_get_s3.return_value = mock_s3

        # Write a JSON file
        json_file = tmp_path / "cadastral_structure.json"
        json_file.write_text(json.dumps(SAMPLE_DATA))
        mock_path.return_value = str(json_file)

        from land_registry.cadastral_utils import _load_cadastral_data_internal
        data, source = _load_cadastral_data_internal()

        assert source == "json"
        assert data == SAMPLE_DATA

    @patch("land_registry.cadastral_utils.cadastral_settings")
    @patch("land_registry.cadastral_utils.get_cadastral_data_root")
    @patch("land_registry.cadastral_utils.get_s3_storage")
    @patch("land_registry.cadastral_utils.get_cadastral_structure_path")
    def test_returns_none_when_all_sources_fail(self, mock_path, mock_get_s3, mock_root, mock_settings):
        mock_settings.use_local_files = False
        mock_root.return_value = None

        mock_s3 = MagicMock()
        mock_s3.get_cadastral_structure.return_value = None
        mock_get_s3.return_value = mock_s3

        mock_path.return_value = None

        from land_registry.cadastral_utils import _load_cadastral_data_internal
        data, source = _load_cadastral_data_internal()

        assert data is None
        assert source == "unknown"


# ---------------------------------------------------------------------------
# clear_cache
# ---------------------------------------------------------------------------


class TestClearCache:
    @patch("land_registry.cadastral_utils._load_cadastral_data_internal")
    def test_clear_forces_reload(self, mock_internal):
        mock_internal.return_value = (SAMPLE_DATA, "local")

        load_cadastral_structure()
        clear_cache()
        load_cadastral_structure()

        assert mock_internal.call_count == 2


# ---------------------------------------------------------------------------
# get_cadastral_stats
# ---------------------------------------------------------------------------


class TestGetCadastralStats:
    @patch("land_registry.cadastral_utils._load_cadastral_data_internal")
    def test_returns_real_stats(self, mock_internal):
        mock_internal.return_value = (SAMPLE_DATA, "local")

        stats = get_cadastral_stats()
        assert stats["total_regions"] == 2
        assert stats["total_provinces"] == 2

    @patch("land_registry.cadastral_utils._load_cadastral_data_internal")
    def test_returns_zeros_on_failure(self, mock_internal):
        mock_internal.return_value = (None, "unknown")

        stats = get_cadastral_stats()
        assert stats == {
            "total_regions": 0,
            "total_provinces": 0,
            "total_municipalities": 0,
            "total_files": 0,
        }
