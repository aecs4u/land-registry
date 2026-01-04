"""
Settings and Configuration for Land Registry Application
Centralized configuration management using Pydantic BaseSettings
"""

from pydantic_settings import BaseSettings
from typing import Optional, List
import os


class AppSettings(BaseSettings):
    """Main application settings"""

    # Application settings
    app_name: str = "Land Registry Viewer"
    app_version: str = "1.0.0"
    debug: bool = False

    # Server settings
    host: str = "0.0.0.0"
    port: int = 8000
    reload: bool = False

    # File paths
    data_dir: str = "data"
    cadastral_structure_file: str = "cadastral_structure.json"
    drawn_polygons_dir: str = "drawn_polygons"

    # Map settings (focused on Italy)
    default_map_center: List[float] = [41.9, 12.5]  # Rome, Italy
    default_map_zoom: int = 6

    # Italy bounding box (restricts map view)
    italy_bounds_sw: List[float] = [35.0, 6.0]   # South-west (Sicily)
    italy_bounds_ne: List[float] = [48.0, 19.0]  # North-east (Alps)

    class Config:
        env_prefix = "LAND_REGISTRY_"
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"


class AuthSettings(BaseSettings):
    """Authentication settings for land-registry app.

    Note: Core Clerk settings (publishable key, secret key, JWKS URL) are
    handled by aecs4u-auth package via environment variables:
        NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY
        CLERK_SECRET_KEY
        CLERK_JWKS_URL

    These settings override the aecs4u-auth defaults for this specific app.
    """

    # Redirect URLs after authentication (app-specific overrides)
    after_sign_in_url: str = "/map"
    after_sign_up_url: str = "/map"

    class Config:
        env_prefix = "AUTH_"
        case_sensitive = False
        extra = "ignore"


class StorageSettings(BaseSettings):
    """
    Unified storage settings using aecs4u-storage package.

    This is the primary storage configuration. S3Settings and GCSStorageSettings
    are kept for backward compatibility but new code should use this.
    """

    # Provider: local, s3, gcs, azure
    provider: str = "s3"

    # S3 configuration
    s3_bucket: str = "apps-aecs4u"
    s3_region: str = "eu-west-3"
    s3_endpoint: Optional[str] = None

    # GCS configuration
    gcs_bucket: str = ""
    gcs_project: Optional[str] = None
    gcs_credentials_file: Optional[str] = None

    # Azure configuration
    azure_container: str = ""
    azure_connection_string: Optional[str] = None

    # Local storage
    local_path: str = "./data/uploads"

    # Path organization
    path_prefix: str = "land-registry"
    organize_by_date: bool = True

    # URL settings
    presigned_url_expiry: int = 3600
    public_base_url: Optional[str] = None

    # File validation
    max_file_size: int = 104857600  # 100MB
    # Use string for allowed_extensions to support comma-separated env var format
    allowed_extensions_str: str = "gpkg,geojson,shp,qpkg,json,csv,xlsx,pdf,png,jpg,jpeg"

    @property
    def allowed_extensions(self) -> List[str]:
        """Get allowed extensions as a list."""
        return [ext.strip() for ext in self.allowed_extensions_str.split(",") if ext.strip()]

    class Config:
        env_prefix = "STORAGE_"
        case_sensitive = False
        extra = "ignore"


class S3Settings(BaseSettings):
    """
    S3 storage settings (legacy - for backward compatibility).

    New code should use StorageSettings with STORAGE_* environment variables.
    """

    # S3 configuration
    bucket_name: str = "apps-aecs4u"
    region: str = "eu-west-3"
    endpoint_url: Optional[str] = None

    # AWS credentials (optional for public bucket access)
    aws_access_key_id: Optional[str] = None
    aws_secret_access_key: Optional[str] = None

    class Config:
        env_prefix = "S3_"
        case_sensitive = False
        extra = "ignore"

    # Backward compatibility aliases
    @property
    def s3_bucket_name(self) -> str:
        return self.bucket_name

    @property
    def s3_region(self) -> str:
        return self.region

    @property
    def s3_endpoint_url(self) -> Optional[str]:
        return self.endpoint_url


class DatabaseSettings(BaseSettings):
    """Database settings for SQLite (local) and Neon PostgreSQL (production)"""

    # SQLite database for local development and offline use
    sqlite_path: str = "data/land-registry.sqlite"
    cache_expiry_hours: int = 24

    # File availability cache database
    file_availability_db_path: str = "file_availability.db"

    # Neon PostgreSQL settings (for production/cloud deployment)
    # Set DATABASE_URL environment variable for Neon connection
    # Format: postgresql://user:password@host/database?sslmode=require
    database_url: Optional[str] = None

    # Use Neon for persistent storage (auto-detected based on DATABASE_URL)
    use_neon: bool = False

    # Use SQLite for local storage (default in development)
    use_sqlite: bool = True

    class Config:
        env_prefix = "DB_"
        case_sensitive = False
        extra = "ignore"


class GCSStorageSettings(BaseSettings):
    """Google Cloud Storage settings for permanent file storage"""

    # Primary storage bucket for app and user data
    gcs_bucket_name: str = "aecs4u-storage"

    # Path prefixes within the bucket
    gcs_app_data_prefix: str = "land-registry/app-data"
    gcs_user_data_prefix: str = "land-registry/user-data"
    gcs_uploads_prefix: str = "land-registry/uploads"
    gcs_exports_prefix: str = "land-registry/exports"

    # Use GCS for storage (vs local filesystem)
    use_gcs: bool = False

    # Signed URL expiration (seconds)
    gcs_signed_url_expiration: int = 3600

    class Config:
        env_prefix = "GCS_"
        case_sensitive = False
        extra = "ignore"


class SpatialiteSettings(BaseSettings):
    """SpatiaLite configuration for loading local geodata"""

    # Separate databases for MAP (fogli) and PLE (particelle)
    db_map_path: str = "data/cadastral_map.sqlite"
    db_ple_path: str = "data/cadastral_ple.sqlite"
    # Legacy single database (for backward compatibility)
    db_path: str = "data/cadastral.sqlite"
    table: str = "cadastral_parcels"
    geometry_column: str = "geometry"
    srid: int = 4326
    default_limit: int = 1000
    extension_path: Optional[str] = None  # Optional override for mod_spatialite path

    class Config:
        env_prefix = "SPATIALITE_"
        case_sensitive = False
        extra = "ignore"


class CadastralSettings(BaseSettings):
    """Cadastral data specific settings"""

    # File paths for different environments
    cadastral_structure_paths: List[str] = [
        "data/cadastral_structure.json",  # Local development
        "/app/data/cadastral_structure.json",  # Cloud Run
        "../data/cadastral_structure.json",  # Alternative
    ]

    # Local cadastral data directory (used in development mode)
    local_cadastral_data_path: Optional[str] = "data/catasto/ITALIA"

    # Use local files instead of S3 (set automatically based on environment)
    use_local_files: bool = False

    # Default file types for cadastral data
    default_file_types: List[str] = ["MAP", "PLE"]

    # File extensions supported
    supported_extensions: List[str] = [".qpkg", ".gpkg", ".shp", ".geojson", ".kml"]

    class Config:
        env_prefix = "CADASTRAL_"
        case_sensitive = False
        extra = "ignore"


class MapControlsSettings(BaseSettings):
    """Map controls and UI settings"""

    # Default control positions
    fullscreen_position: str = "topright"
    measure_position: str = "topleft"
    locate_position: str = "topleft"
    draw_position: str = "bottomleft"

    # Plugin settings
    enable_minimap: bool = True
    enable_mouse_position: bool = True
    enable_geocoder: bool = True
    enable_draw_tools: bool = True
    enable_measure_tools: bool = True

    # Advanced plugins
    enable_marker_cluster: bool = True
    enable_search: bool = True
    enable_tag_filter: bool = True
    enable_overlapping_marker_spiderfier: bool = True

    class Config:
        env_prefix = "MAP_CONTROLS_"
        case_sensitive = False
        extra = "ignore"


class PanelServerSettings(BaseSettings):
    """Panel server configuration"""

    # Server settings
    panel_host: str = "127.0.0.1"
    panel_port: int = 5006
    panel_threaded: bool = True
    panel_show: bool = False

    # WebSocket origins (dynamically populated based on main app port)
    panel_websocket_origins: List[str] = [
        "127.0.0.1:8000",
        "localhost:8000",
        "127.0.0.1:8001",
        "localhost:8001"
    ]

    # Health check settings
    panel_startup_timeout: int = 10  # seconds
    panel_startup_retry_delay: float = 0.5  # seconds
    panel_health_check_timeout: float = 5.0  # seconds

    # Panel application routes
    # NOTE: Currently all tables use the same Panel dashboard
    # Future work: Create separate Panel apps for each table type
    panel_dashboard_route: str = "/dashboard"
    panel_map_table_route: str = "/dashboard"  # TODO: Create /map_table Panel app
    panel_adjacency_table_route: str = "/dashboard"  # TODO: Create /adjacency_table Panel app
    panel_mapping_table_route: str = "/dashboard"  # TODO: Create /mapping_table Panel app

    class Config:
        env_prefix = "PANEL_"
        case_sensitive = False
        extra = "ignore"


def get_panel_url(route: str = "") -> str:
    """Helper to build Panel server URLs"""
    return f"http://{panel_settings.panel_host}:{panel_settings.panel_port}{route}"


# Global settings instances
app_settings = AppSettings()
auth_settings = AuthSettings()
storage_settings = StorageSettings()  # Primary unified storage config
s3_settings = S3Settings()  # Legacy - for backward compatibility
db_settings = DatabaseSettings()
gcs_settings = GCSStorageSettings()  # Legacy - for backward compatibility
cadastral_settings = CadastralSettings()
map_controls_settings = MapControlsSettings()
panel_settings = PanelServerSettings()
spatialite_settings = SpatialiteSettings()

# Auto-detect Neon PostgreSQL configuration
if os.getenv("DATABASE_URL"):
    db_settings.use_neon = True
    db_settings.database_url = os.getenv("DATABASE_URL")

# Auto-detect GCS configuration in production
if os.getenv("ENVIRONMENT") == "production" or os.getenv("GCS_USE_GCS") == "true":
    gcs_settings.use_gcs = True


def get_cadastral_structure_path() -> Optional[str]:
    """Get the first valid cadastral structure file path"""

    root_folder = os.path.dirname(os.path.abspath(__file__))

    # Build absolute paths
    possible_paths = [
        os.path.join(root_folder, "../data/cadastral_structure.json"),  # Local development
        os.path.join("/app/data/cadastral_structure.json"),  # Cloud Run
        os.path.join(os.getcwd(), "data/cadastral_structure.json"),  # Alternative
    ]

    for path in possible_paths:
        if os.path.exists(path):
            return os.path.abspath(path)

    return None


def get_data_directory() -> str:
    """Get the data directory path"""

    root_folder = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(root_folder, "..", app_settings.data_dir)


def get_drawn_polygons_directory() -> str:
    """Get the drawn polygons directory path"""

    data_dir = get_data_directory()
    drawn_dir = os.path.join(data_dir, app_settings.drawn_polygons_dir)

    # Create directory if it doesn't exist
    os.makedirs(drawn_dir, exist_ok=True)

    return drawn_dir


# Environment-specific overrides
if os.getenv("ENVIRONMENT") == "production":
    app_settings.debug = False
    app_settings.reload = False
    cadastral_settings.use_local_files = False  # Use S3 in production
elif os.getenv("ENVIRONMENT") == "development":
    app_settings.debug = True
    app_settings.reload = True
    cadastral_settings.use_local_files = True  # Use local files in development
else:
    # Auto-detect: if local cadastral data exists, use it (development mode)
    root_folder = os.path.dirname(os.path.abspath(__file__))
    local_path = os.path.join(root_folder, "..", cadastral_settings.local_cadastral_data_path or "")
    if os.path.exists(local_path) and os.path.isdir(local_path):
        cadastral_settings.use_local_files = True
        app_settings.debug = True
    else:
        cadastral_settings.use_local_files = False


def get_cadastral_data_root() -> Optional[str]:
    """
    Get the root path for cadastral data files.
    Returns local path in development, None in production (use S3).
    """
    if not cadastral_settings.use_local_files:
        return None

    root_folder = os.path.dirname(os.path.abspath(__file__))
    local_path = os.path.join(root_folder, "..", cadastral_settings.local_cadastral_data_path or "")

    if os.path.exists(local_path) and os.path.isdir(local_path):
        return os.path.abspath(local_path)

    return None
