"""
Unit tests for land_registry/dependencies.py.

Tests:
- MapState: all accessors and mutators
- _discover_ple_databases: empty dir, with files
- CadastralRegistry: get_db_map, get_db_ple, get_all_ple, get_db
- DatashaderRegistry: get_service
- Provider functions: get_map_state, get_cadastral_registry, get_datashader_registry
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

from land_registry.dependencies import (
    CadastralRegistry,
    DatashaderRegistry,
    MapState,
    _discover_ple_databases,
    get_cadastral_registry,
    get_datashader_registry,
    get_map_state,
)


# ---------------------------------------------------------------------------
# MapState
# ---------------------------------------------------------------------------

@pytest.fixture
def state():
    return MapState()


class TestMapState:

    def test_get_gdf_initially_none(self, state):
        assert state.get_gdf() is None

    def test_set_and_get_gdf(self, state):
        gdf = gpd.GeoDataFrame({"geometry": [Point(12, 41)]})
        state.set_gdf(gdf)
        assert state.get_gdf() is not None
        assert len(state.get_gdf()) == 1

    def test_set_gdf_invalidates_display_df(self, state):
        gdf = gpd.GeoDataFrame({"id": [1], "geometry": [Point(12, 41)]})
        state.set_gdf(gdf)
        _ = state.get_display_df()  # Prime the cache
        # Set new gdf should clear cache
        gdf2 = gpd.GeoDataFrame({"id": [99], "geometry": [Point(13, 42)]})
        state.set_gdf(gdf2)
        # display_df should now reflect new gdf
        df = state.get_display_df()
        assert df is not None
        assert df.iloc[0]["id"] == 99

    def test_get_display_df_without_gdf(self, state):
        assert state.get_display_df() is None

    def test_get_display_df_strips_geometry(self, state):
        gdf = gpd.GeoDataFrame({"id": [1, 2], "geometry": [Point(12, 41), Point(13, 42)]})
        state.set_gdf(gdf)
        df = state.get_display_df()
        assert "geometry" not in df.columns
        assert "id" in df.columns

    def test_get_display_df_cached(self, state):
        gdf = gpd.GeoDataFrame({"id": [1], "geometry": [Point(12, 41)]})
        state.set_gdf(gdf)
        df1 = state.get_display_df()
        df2 = state.get_display_df()
        assert df1 is df2  # Same object (cached)

    def test_get_layers_initially_empty(self, state):
        assert state.get_layers() == {}

    def test_set_and_get_layers(self, state):
        layers = {"layer1": "data1", "layer2": "data2"}
        state.set_layers(layers)
        assert state.get_layers() == layers

    def test_set_layers_makes_copy(self, state):
        layers = {"layer1": "data"}
        state.set_layers(layers)
        layers["layer2"] = "new"  # Modify original
        assert "layer2" not in state.get_layers()

    def test_clear_layers(self, state):
        state.set_layers({"a": 1, "b": 2})
        state.clear_layers()
        assert state.get_layers() == {}

    def test_get_auction_properties_initially_none(self, state):
        assert state.get_auction_properties() is None

    def test_set_and_get_auction_properties(self, state):
        mock_props = MagicMock()
        state.set_auction_properties(mock_props)
        assert state.get_auction_properties() is mock_props

    def test_set_auction_properties_none(self, state):
        state.set_auction_properties(MagicMock())
        state.set_auction_properties(None)
        assert state.get_auction_properties() is None


# ---------------------------------------------------------------------------
# _discover_ple_databases
# ---------------------------------------------------------------------------

class TestDiscoverPleDatabases:

    def test_no_data_directory(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)  # Chdir to tmp where no 'data' dir exists
        result = _discover_ple_databases()
        assert result == {}

    def test_empty_data_directory(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir()
        result = _discover_ple_databases()
        assert result == {}

    def test_discovers_ple_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "cadastral_ple.lazio.sqlite").touch()
        (data_dir / "cadastral_ple.lombardia.sqlite").touch()
        (data_dir / "cadastral_map.sqlite").touch()  # Should be ignored

        result = _discover_ple_databases()
        assert "lazio" in result
        assert "lombardia" in result
        assert len(result) == 2

    def test_ignores_non_matching_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "other_file.sqlite").touch()
        result = _discover_ple_databases()
        assert result == {}


# ---------------------------------------------------------------------------
# CadastralRegistry
# ---------------------------------------------------------------------------

class TestCadastralRegistry:

    def test_get_db_map_creates_db(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        registry = CadastralRegistry()
        db = registry.get_db_map()
        assert db is not None

    def test_get_db_map_cached(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        registry = CadastralRegistry()
        db1 = registry.get_db_map()
        db2 = registry.get_db_map()
        assert db1 is db2

    def test_get_db_ple_no_databases(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        registry = CadastralRegistry()
        result = registry.get_db_ple()
        assert result is None

    def test_get_db_ple_with_region_not_found(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        registry = CadastralRegistry()
        # Patch _discover_ple_databases to return empty
        with patch("land_registry.dependencies._discover_ple_databases", return_value={}):
            result = registry.get_db_ple(region="LAZIO")
        assert result is None

    def test_get_db_ple_with_region_found(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        db_path = tmp_path / "data" / "cadastral_ple.lazio.sqlite"
        db_path.parent.mkdir(parents=True, exist_ok=True)

        registry = CadastralRegistry()
        with patch("land_registry.dependencies._discover_ple_databases",
                   return_value={"lazio": db_path}):
            result = registry.get_db_ple(region="LAZIO")
        assert result is not None

    def test_get_db_ple_cached_second_call(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        db_path = tmp_path / "data" / "cadastral_ple.lazio.sqlite"
        db_path.parent.mkdir(parents=True, exist_ok=True)

        registry = CadastralRegistry()
        with patch("land_registry.dependencies._discover_ple_databases",
                   return_value={"lazio": db_path}):
            db1 = registry.get_db_ple(region="LAZIO")
            db2 = registry.get_db_ple(region="LAZIO")
        assert db1 is db2

    def test_get_db_ple_region_not_in_available(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        registry = CadastralRegistry()
        with patch("land_registry.dependencies._discover_ple_databases",
                   return_value={"lazio": tmp_path / "lazio.sqlite"}):
            result = registry.get_db_ple(region="SICILIA")
        assert result is None

    def test_get_db_ple_no_region_returns_first(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        db_path = tmp_path / "data" / "cadastral_ple.lazio.sqlite"
        db_path.parent.mkdir(parents=True, exist_ok=True)

        registry = CadastralRegistry()
        with patch("land_registry.dependencies._discover_ple_databases",
                   return_value={"lazio": db_path, "lombardia": db_path}):
            result = registry.get_db_ple()
        assert result is not None

    def test_get_all_ple_empty(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        registry = CadastralRegistry()
        with patch("land_registry.dependencies._discover_ple_databases", return_value={}):
            result = registry.get_all_ple()
        assert result == {}

    def test_get_all_ple_with_databases(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        db_path = tmp_path / "data" / "cadastral_ple.lazio.sqlite"
        db_path.parent.mkdir(parents=True, exist_ok=True)

        registry = CadastralRegistry()
        with patch("land_registry.dependencies._discover_ple_databases",
                   return_value={"lazio": db_path}):
            result = registry.get_all_ple()
        assert "lazio" in result

    def test_get_db_map_type(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        registry = CadastralRegistry()
        db = registry.get_db(layer_type="map")
        assert db is not None

    def test_get_db_ple_type(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        registry = CadastralRegistry()
        with patch("land_registry.dependencies._discover_ple_databases", return_value={}):
            result = registry.get_db(layer_type="ple")
        assert result is None


# ---------------------------------------------------------------------------
# DatashaderRegistry
# ---------------------------------------------------------------------------

class TestDatashaderRegistry:

    def test_get_service_returns_service(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        registry = DatashaderRegistry()
        service = registry.get_service()
        from land_registry.datashader_service import DatashaderTileService
        assert isinstance(service, DatashaderTileService)

    def test_get_service_cached(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        registry = DatashaderRegistry()
        s1 = registry.get_service()
        s2 = registry.get_service()
        assert s1 is s2


# ---------------------------------------------------------------------------
# Provider functions
# ---------------------------------------------------------------------------

class TestProviderFunctions:

    def test_get_map_state_returns_map_state(self):
        result = get_map_state()
        assert isinstance(result, MapState)

    def test_get_cadastral_registry_returns_registry(self):
        result = get_cadastral_registry()
        assert isinstance(result, CadastralRegistry)

    def test_get_datashader_registry_returns_registry(self):
        result = get_datashader_registry()
        assert isinstance(result, DatashaderRegistry)

    def test_provider_functions_return_singletons(self):
        """Same object returned each time (module-level singletons)."""
        assert get_map_state() is get_map_state()
        assert get_cadastral_registry() is get_cadastral_registry()
        assert get_datashader_registry() is get_datashader_registry()
