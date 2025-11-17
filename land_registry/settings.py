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

    # Authentication settings (Clerk)
    clerk_publishable_key: Optional[str] = None
    clerk_secret_key: Optional[str] = None
    clerk_domain: Optional[str] = None

    class Config:
        env_prefix = "LAND_REGISTRY_"
        case_sensitive = False


class S3Settings(BaseSettings):
    """S3 storage settings"""

    # S3 configuration
    s3_bucket_name: str = "catasto-2025"
    s3_region: str = "eu-central-1"
    s3_endpoint_url: Optional[str] = None

    # AWS credentials (optional for public bucket access)
    aws_access_key_id: Optional[str] = None
    aws_secret_access_key: Optional[str] = None

    # Public bucket fallback
    use_public_bucket_fallback: bool = True
    public_bucket_unsigned: bool = True

    class Config:
        env_prefix = "S3_"
        case_sensitive = False


class DatabaseSettings(BaseSettings):
    """Database settings"""

    # SQLite database for file availability caching
    db_path: str = "land_registry.db"
    cache_expiry_hours: int = 24

    class Config:
        env_prefix = "DB_"
        case_sensitive = False


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


class MapControlsSettings(BaseSettings):
    """Map controls and UI settings"""

    # Default control positions
    fullscreen_position: str = "topright"
    measure_position: str = "topleft"
    locate_position: str = "topleft"
    draw_position: str = "topleft"

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


def get_panel_url(route: str = "") -> str:
    """Helper to build Panel server URLs"""
    return f"http://{panel_settings.panel_host}:{panel_settings.panel_port}{route}"


# Global settings instances
app_settings = AppSettings()
s3_settings = S3Settings()
db_settings = DatabaseSettings()
cadastral_settings = CadastralSettings()
map_controls_settings = MapControlsSettings()
panel_settings = PanelServerSettings()


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