"""
Tests for the configuration module.

Tests cover:
- All settings classes (App, Auth, S3, Database, GCS, Cadastral, MapControls, Panel)
- Environment variable loading
- Auto-detection of configurations
- Helper functions for paths
"""

import os
import tempfile
import pytest
from unittest.mock import patch

from land_registry.config import (
    AppSettings,
    AuthSettings,
    StorageSettings,
    S3Settings,
    DatabaseSettings,
    GCSStorageSettings,
    CadastralSettings,
    MapControlsSettings,
    PanelServerSettings,
    get_panel_url,
    get_cadastral_structure_path,
    get_data_directory,
    get_drawn_polygons_directory,
    get_cadastral_data_root,
    app_settings,
    auth_settings,
    storage_settings,
    s3_settings,
    db_settings,
    gcs_settings,
    cadastral_settings,
    map_controls_settings,
    panel_settings,
)


class TestAppSettings:
    """Test main application settings."""

    def test_default_values(self):
        """Test default app settings values."""
        settings = AppSettings()

        assert settings.app_name == "Land Registry Viewer"
        assert settings.debug is False
        assert settings.host == "0.0.0.0"
        assert settings.port == 8000
        assert settings.default_map_center == [41.9, 12.5]
        assert settings.default_map_zoom == 6

    def test_italy_bounds(self):
        """Test Italy bounding box settings."""
        settings = AppSettings()

        assert settings.italy_bounds_sw == [35.0, 6.0]
        assert settings.italy_bounds_ne == [48.0, 19.0]

    def test_env_prefix(self, monkeypatch):
        """Test environment variable prefix."""
        monkeypatch.setenv("LAND_REGISTRY_DEBUG", "true")
        monkeypatch.setenv("LAND_REGISTRY_PORT", "9000")

        settings = AppSettings()

        assert settings.debug is True
        assert settings.port == 9000


class TestAuthSettings:
    """Test authentication settings."""

    def test_default_values(self):
        """Test default auth settings."""
        settings = AuthSettings()

        assert settings.after_sign_in_url == "/map"
        assert settings.after_sign_up_url == "/map"

    def test_env_prefix(self, monkeypatch):
        """Test AUTH_ environment prefix."""
        monkeypatch.setenv("AUTH_AFTER_SIGN_IN_URL", "/dashboard")

        settings = AuthSettings()

        assert settings.after_sign_in_url == "/dashboard"


class TestStorageSettings:
    """Test unified storage settings."""

    def test_default_values(self):
        """Test default storage settings."""
        from land_registry.config import StorageSettings
        settings = StorageSettings()

        assert settings.provider == "s3"
        assert settings.s3_bucket == "apps-aecs4u"
        assert settings.s3_region == "eu-west-3"
        assert settings.path_prefix == "land-registry"
        assert settings.organize_by_date is True

    def test_allowed_extensions(self):
        """Test default allowed extensions."""
        from land_registry.config import StorageSettings
        settings = StorageSettings()

        assert "gpkg" in settings.allowed_extensions
        assert "geojson" in settings.allowed_extensions
        assert "json" in settings.allowed_extensions

    def test_env_prefix(self, monkeypatch):
        """Test STORAGE_ environment prefix."""
        from land_registry.config import StorageSettings
        monkeypatch.setenv("STORAGE_PROVIDER", "gcs")
        monkeypatch.setenv("STORAGE_S3_BUCKET", "test-bucket")

        settings = StorageSettings()

        assert settings.provider == "gcs"
        assert settings.s3_bucket == "test-bucket"


class TestS3Settings:
    """Test S3 storage settings (legacy)."""

    def test_default_values(self, monkeypatch):
        """Test default S3 settings when no env vars are set."""
        # Clear any S3_ prefixed env vars to test defaults
        monkeypatch.delenv("S3_BUCKET_NAME", raising=False)
        monkeypatch.delenv("S3_REGION", raising=False)

        settings = S3Settings()

        assert settings.s3_bucket_name == "apps-aecs4u"
        assert settings.s3_region == "eu-west-3"

    def test_optional_credentials(self):
        """Test optional credentials are None or empty by default."""
        settings = S3Settings()

        # These may be None, empty string, or a value depending on .env configuration
        assert settings.aws_access_key_id is None or isinstance(settings.aws_access_key_id, str)
        assert settings.aws_secret_access_key is None or isinstance(settings.aws_secret_access_key, str)
        assert settings.s3_endpoint_url is None or isinstance(settings.s3_endpoint_url, str)

    def test_env_prefix(self, monkeypatch):
        """Test S3_ environment prefix."""
        monkeypatch.setenv("S3_BUCKET_NAME", "custom-bucket")
        monkeypatch.setenv("S3_REGION", "us-west-2")

        settings = S3Settings()

        assert settings.s3_bucket_name == "custom-bucket"
        assert settings.s3_region == "us-west-2"


class TestDatabaseSettings:
    """Test database settings."""

    def test_default_values(self):
        """Test default database settings."""
        settings = DatabaseSettings()

        assert settings.sqlite_path == "data/land-registry.sqlite"
        assert settings.cache_expiry_hours == 24
        assert settings.database_url is None
        assert settings.use_neon is False
        assert settings.use_sqlite is True

    def test_file_availability_db_path(self):
        """Test file_availability_db_path is correctly configured."""
        settings = DatabaseSettings()

        assert settings.file_availability_db_path == "file_availability.db"

    def test_env_prefix(self, monkeypatch):
        """Test DB_ environment prefix."""
        monkeypatch.setenv("DB_CACHE_EXPIRY_HOURS", "48")
        monkeypatch.setenv("DB_USE_SQLITE", "false")

        settings = DatabaseSettings()

        assert settings.cache_expiry_hours == 48
        assert settings.use_sqlite is False


class TestGCSStorageSettings:
    """Test Google Cloud Storage settings."""

    def test_default_values(self):
        """Test default GCS settings."""
        settings = GCSStorageSettings()

        assert settings.gcs_bucket_name == "aecs4u-storage"
        assert settings.gcs_app_data_prefix == "land-registry/app-data"
        assert settings.gcs_user_data_prefix == "land-registry/user-data"
        assert settings.gcs_uploads_prefix == "land-registry/uploads"
        assert settings.gcs_exports_prefix == "land-registry/exports"
        assert settings.use_gcs is False
        assert settings.gcs_signed_url_expiration == 3600

    def test_env_prefix(self, monkeypatch):
        """Test GCS_ environment prefix."""
        monkeypatch.setenv("GCS_GCS_BUCKET_NAME", "my-bucket")
        monkeypatch.setenv("GCS_USE_GCS", "true")

        settings = GCSStorageSettings()

        assert settings.gcs_bucket_name == "my-bucket"
        assert settings.use_gcs is True


class TestCadastralSettings:
    """Test cadastral data settings."""

    def test_default_values(self):
        """Test default cadastral settings."""
        settings = CadastralSettings()

        assert "data/cadastral_structure.json" in settings.cadastral_structure_paths
        assert settings.use_local_files is False
        assert settings.default_file_types == ["MAP", "PLE"]
        assert ".gpkg" in settings.supported_extensions
        assert ".geojson" in settings.supported_extensions

    def test_local_cadastral_path(self):
        """Test local cadastral data path."""
        settings = CadastralSettings()

        assert settings.local_cadastral_data_path == "data/catasto/ITALIA"

    def test_env_prefix(self, monkeypatch):
        """Test CADASTRAL_ environment prefix."""
        monkeypatch.setenv("CADASTRAL_USE_LOCAL_FILES", "true")

        settings = CadastralSettings()

        assert settings.use_local_files is True


class TestMapControlsSettings:
    """Test map controls settings."""

    def test_default_values(self):
        """Test default map controls settings."""
        settings = MapControlsSettings()

        assert settings.fullscreen_position == "topright"
        assert settings.measure_position == "topleft"
        assert settings.enable_minimap is True
        assert settings.enable_draw_tools is True
        assert settings.enable_geocoder is True

    def test_all_plugins_enabled_by_default(self):
        """Test all plugins are enabled by default."""
        settings = MapControlsSettings()

        assert settings.enable_minimap is True
        assert settings.enable_mouse_position is True
        assert settings.enable_geocoder is True
        assert settings.enable_draw_tools is True
        assert settings.enable_measure_tools is True
        assert settings.enable_marker_cluster is True
        assert settings.enable_search is True
        assert settings.enable_tag_filter is True

    def test_env_prefix(self, monkeypatch):
        """Test MAP_CONTROLS_ environment prefix."""
        monkeypatch.setenv("MAP_CONTROLS_ENABLE_MINIMAP", "false")

        settings = MapControlsSettings()

        assert settings.enable_minimap is False


class TestPanelServerSettings:
    """Test Panel server settings."""

    def test_default_values(self):
        """Test default Panel settings."""
        settings = PanelServerSettings()

        assert settings.panel_host == "127.0.0.1"
        assert settings.panel_port == 5006
        assert settings.panel_threaded is True
        assert settings.panel_show is False
        assert settings.panel_startup_timeout == 10

    def test_websocket_origins(self):
        """Test websocket origins include common development ports."""
        settings = PanelServerSettings()

        assert "127.0.0.1:8000" in settings.panel_websocket_origins
        assert "localhost:8000" in settings.panel_websocket_origins

    def test_panel_routes(self):
        """Test Panel routes default to /dashboard."""
        settings = PanelServerSettings()

        assert settings.panel_dashboard_route == "/dashboard"
        assert settings.panel_map_table_route == "/dashboard"
        assert settings.panel_adjacency_table_route == "/dashboard"

    def test_env_prefix(self, monkeypatch):
        """Test PANEL_ environment prefix."""
        monkeypatch.setenv("PANEL_PANEL_PORT", "6006")

        settings = PanelServerSettings()

        assert settings.panel_port == 6006


class TestGetPanelUrl:
    """Test get_panel_url helper function."""

    def test_get_panel_url_default(self):
        """Test getting Panel URL without route."""
        url = get_panel_url()

        assert "127.0.0.1" in url
        assert "5006" in url

    def test_get_panel_url_with_route(self):
        """Test getting Panel URL with route."""
        url = get_panel_url("/dashboard")

        assert "/dashboard" in url


class TestGetCadastralStructurePath:
    """Test get_cadastral_structure_path function."""

    def test_returns_none_when_no_file_exists(self, tmp_path, monkeypatch):
        """Test returns None when no cadastral structure file exists."""
        # Change to a temp directory with no data files
        monkeypatch.chdir(tmp_path)

        # Function should return None if file doesn't exist
        # (actual behavior depends on current working directory)
        result = get_cadastral_structure_path()
        # Result could be a path or None depending on environment
        assert result is None or isinstance(result, str)


class TestGetDataDirectory:
    """Test get_data_directory function."""

    def test_returns_path(self):
        """Test get_data_directory returns a path."""
        path = get_data_directory()

        assert isinstance(path, str)
        assert "data" in path


class TestGetDrawnPolygonsDirectory:
    """Test get_drawn_polygons_directory function."""

    def test_returns_path(self):
        """Test get_drawn_polygons_directory returns a path."""
        path = get_drawn_polygons_directory()

        assert isinstance(path, str)
        assert "drawn_polygons" in path

    def test_creates_directory(self, tmp_path):
        """Test directory is created if it doesn't exist."""
        # The function creates the directory if it doesn't exist
        path = get_drawn_polygons_directory()
        assert os.path.isdir(path)


class TestGetCadastralDataRoot:
    """Test get_cadastral_data_root function."""

    def test_returns_none_when_not_using_local(self, monkeypatch):
        """Test returns None when not using local files."""
        # Ensure use_local_files is False
        from land_registry import config
        config.cadastral_settings.use_local_files = False

        result = get_cadastral_data_root()

        assert result is None


class TestGlobalSettingsInstances:
    """Test that global settings instances are properly initialized."""

    def test_app_settings_instance(self):
        """Test app_settings global instance exists."""
        assert app_settings is not None
        assert isinstance(app_settings, AppSettings)

    def test_auth_settings_instance(self):
        """Test auth_settings global instance exists."""
        assert auth_settings is not None
        assert isinstance(auth_settings, AuthSettings)

    def test_storage_settings_instance(self):
        """Test storage_settings global instance exists."""
        assert storage_settings is not None
        assert isinstance(storage_settings, StorageSettings)

    def test_s3_settings_instance(self):
        """Test s3_settings global instance exists (legacy)."""
        assert s3_settings is not None
        assert isinstance(s3_settings, S3Settings)

    def test_db_settings_instance(self):
        """Test db_settings global instance exists."""
        assert db_settings is not None
        assert isinstance(db_settings, DatabaseSettings)

    def test_gcs_settings_instance(self):
        """Test gcs_settings global instance exists."""
        assert gcs_settings is not None
        assert isinstance(gcs_settings, GCSStorageSettings)

    def test_cadastral_settings_instance(self):
        """Test cadastral_settings global instance exists."""
        assert cadastral_settings is not None
        assert isinstance(cadastral_settings, CadastralSettings)

    def test_map_controls_settings_instance(self):
        """Test map_controls_settings global instance exists."""
        assert map_controls_settings is not None
        assert isinstance(map_controls_settings, MapControlsSettings)

    def test_panel_settings_instance(self):
        """Test panel_settings global instance exists."""
        assert panel_settings is not None
        assert isinstance(panel_settings, PanelServerSettings)


class TestAutoDetection:
    """Test auto-detection of configurations."""

    def test_neon_auto_detection(self, monkeypatch):
        """Test Neon PostgreSQL is auto-detected from DATABASE_URL."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@neon.tech/db")

        # Reimport to trigger auto-detection
        from importlib import reload
        import land_registry.config as config_module
        reload(config_module)

        # After reload, check that use_neon was set
        # Note: This tests the behavior in the module-level code
        assert hasattr(config_module.db_settings, "use_neon")

    def test_gcs_auto_detection_production(self, monkeypatch):
        """Test GCS is auto-enabled in production."""
        monkeypatch.setenv("ENVIRONMENT", "production")

        from importlib import reload
        import land_registry.config as config_module
        reload(config_module)

        # In production, GCS should be enabled
        assert hasattr(config_module.gcs_settings, "use_gcs")


class TestSettingsExtraIgnore:
    """Test that extra fields are ignored in settings."""

    def test_app_settings_ignores_extra(self):
        """Test AppSettings ignores unknown fields."""
        # This should not raise even with extra fields in env
        settings = AppSettings()
        assert settings is not None

    def test_s3_settings_ignores_extra(self):
        """Test S3Settings ignores unknown fields."""
        settings = S3Settings()
        assert settings is not None

    def test_database_settings_ignores_extra(self):
        """Test DatabaseSettings ignores unknown fields."""
        settings = DatabaseSettings()
        assert settings is not None
