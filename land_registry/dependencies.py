"""
FastAPI dependency providers for application-level singletons.

Centralises all mutable application state behind thread-safe containers
and exposes them as FastAPI Depends() provider functions.

Usage in route handlers::

    from land_registry.dependencies import get_map_state, MapState

    @router.get("/example")
    async def example(state: MapState = Depends(get_map_state)):
        gdf = state.get_gdf()

For testing, override via app.dependency_overrides::

    app.dependency_overrides[get_map_state] = lambda: MockMapState()
"""

import logging
import threading
import base64
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


def empty_datashader_tile() -> bytes:
    """Return a valid transparent PNG without importing optional Datashader."""
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/"
        "eKJx6QAAAABJRU5ErkJggg=="
    )


class _UnavailableDatashaderService:
    """Stable no-op service used when optional raster dependencies are absent."""

    available = False

    def _empty_tile(self) -> bytes:
        return empty_datashader_tile()

    def generate_tile(self, *args, **kwargs) -> bytes:
        return empty_datashader_tile()

    def generate_boundary_tile(self, *args, **kwargs) -> bytes:
        return empty_datashader_tile()

    def generate_boundary_mvt(self, *args, **kwargs) -> bytes:
        return b""

    def boundary_mvt_available(self) -> bool:
        return False

    def close(self) -> None:
        return None


# ============================================================================
# MapState — wraps current_gdf, current_layers, auction_properties
# ============================================================================

class MapState:
    """Thread-safe container for the active geospatial session data."""

    def __init__(self):
        self._lock = threading.Lock()
        self._gdf = None          # GeoDataFrame | None
        self._display_df = None   # pd.DataFrame | None — geometry-free cache
        self._layers: dict = {}
        self._auction_properties = None  # GeoDataFrame | None

    # -- GeoDataFrame ---------------------------------------------------------

    def get_gdf(self):
        with self._lock:
            return self._gdf

    def set_gdf(self, gdf) -> None:
        with self._lock:
            self._gdf = gdf
            self._display_df = None  # Invalidate display cache

    def get_display_df(self) -> Optional[pd.DataFrame]:
        """Return a geometry-free DataFrame for tabular display.

        The result is cached and invalidated automatically whenever
        :meth:`set_gdf` is called, so callers never pay the cost of
        copying and dropping the geometry column more than once per
        GeoDataFrame load.
        """
        with self._lock:
            if self._display_df is None and self._gdf is not None:
                self._display_df = pd.DataFrame(
                    self._gdf.drop(columns=["geometry"], errors="ignore")
                )
            return self._display_df

    # -- Layers ---------------------------------------------------------------

    def get_layers(self) -> dict:
        with self._lock:
            return self._layers

    def set_layers(self, layers: dict) -> None:
        with self._lock:
            self._layers = dict(layers)

    def clear_layers(self) -> None:
        with self._lock:
            self._layers = {}

    # -- Auction properties ---------------------------------------------------

    def get_auction_properties(self):
        with self._lock:
            return self._auction_properties

    def set_auction_properties(self, props) -> None:
        with self._lock:
            self._auction_properties = props


# ============================================================================
# CadastralRegistry — wraps _cadastral_db_map and _cadastral_db_ple_by_region
# ============================================================================

def _discover_ple_databases() -> dict:
    """
    Discover all per-region PLE databases in the data directory.

    Looks for files matching: cadastral_ple.<region>.sqlite

    Returns:
        Dict mapping region slug to Path
    """
    data_dir = Path("data")
    if not data_dir.exists():
        return {}

    ple_dbs = {}
    for db_file in data_dir.glob("cadastral_ple.*.sqlite"):
        parts = db_file.stem.split(".")
        if len(parts) >= 2:
            region_slug = parts[1]
            ple_dbs[region_slug] = db_file
    return ple_dbs


class CadastralRegistry:
    """Thread-safe lazy cache for CadastralDatabase instances."""

    def __init__(self):
        self._lock = threading.Lock()
        self._db_map = None
        self._db_ple_by_region: dict = {}

    def get_db_map(self):
        """Get or create the MAP (fogli) database instance."""
        from land_registry.cadastral_db import CadastralDatabase
        with self._lock:
            if self._db_map is None:
                db_path = Path("data/cadastral_map.sqlite")
                if not db_path.exists():
                    logger.warning(f"MAP database not found: {db_path}")
                    db_path.parent.mkdir(parents=True, exist_ok=True)
                self._db_map = CadastralDatabase(db_path)
            return self._db_map

    def get_db_ple(self, region: Optional[str] = None):
        """
        Get or create a PLE database instance for the given region.

        Args:
            region: Region name (e.g. 'LOMBARDIA'). If None returns the first
                    available PLE database.

        Returns:
            CadastralDatabase instance or None if no PLE databases exist.
        """
        from land_registry.cadastral_db import CadastralDatabase
        available_dbs = _discover_ple_databases()

        if not available_dbs:
            logger.warning("No PLE databases found in data directory")
            return None

        with self._lock:
            if region:
                region_slug = region.lower().replace(' ', '_').replace('-', '_')
                if region_slug in self._db_ple_by_region:
                    return self._db_ple_by_region[region_slug]
                if region_slug in available_dbs:
                    self._db_ple_by_region[region_slug] = CadastralDatabase(
                        available_dbs[region_slug]
                    )
                    return self._db_ple_by_region[region_slug]
                logger.warning(
                    f"PLE database for region '{region}' not found. "
                    f"Available: {list(available_dbs.keys())}"
                )
                return None
            else:
                first_region = sorted(available_dbs.keys())[0]
                if first_region not in self._db_ple_by_region:
                    self._db_ple_by_region[first_region] = CadastralDatabase(
                        available_dbs[first_region]
                    )
                return self._db_ple_by_region[first_region]

    def get_all_ple(self) -> dict:
        """Load and return all available PLE databases."""
        from land_registry.cadastral_db import CadastralDatabase
        available_dbs = _discover_ple_databases()
        with self._lock:
            for region_slug, db_path in available_dbs.items():
                if region_slug not in self._db_ple_by_region:
                    self._db_ple_by_region[region_slug] = CadastralDatabase(db_path)
            return self._db_ple_by_region

    def get_db(self, layer_type: Optional[str] = None, region: Optional[str] = None):
        """Dispatch to MAP or PLE database based on layer_type."""
        if layer_type == 'map':
            return self.get_db_map()
        return self.get_db_ple(region)


# ============================================================================
# DatashaderRegistry — wraps _datashader_service
# ============================================================================

class DatashaderRegistry:
    """Thread-safe lazy singleton for DatashaderTileService."""

    def __init__(self):
        self._lock = threading.Lock()
        self._service = None

    def get_service(self):
        """Get or create the DatashaderTileService instance."""
        with self._lock:
            if self._service is None:
                try:
                    from land_registry.cadastral_db import CadastralDatabase
                    from land_registry.datashader_service import DatashaderTileService
                    from land_registry.config import db_settings
                    db = CadastralDatabase(Path(db_settings.sqlite_path))
                    self._service = DatashaderTileService(db)
                    logger.info("DatashaderTileService initialized")
                except Exception as e:
                    logger.error(f"Failed to initialize DatashaderTileService: {e}")
                    # Do not re-import the module that just failed: missing
                    # optional dependencies would otherwise turn every tile
                    # request into another 500 and import traceback.
                    self._service = _UnavailableDatashaderService()
            return self._service


# ============================================================================
# GHSLRegistry — wraps GHSLService
# ============================================================================

class GHSLRegistry:
    """Thread-safe lazy singleton for GHSLService."""

    def __init__(self):
        self._lock = threading.Lock()
        self._service = None

    def get_service(self):
        """Get or create the GHSLService instance (may return None if unavailable)."""
        with self._lock:
            if self._service is None:
                try:
                    from land_registry.ghsl_service import GHSLService
                    from land_registry.config import ghsl_settings
                    self._service = GHSLService(
                        data_dir=ghsl_settings.ghsl_data_dir,
                        raster_path=ghsl_settings.ghsl_raster_path,
                    )
                    logger.info("GHSLService created (data_dir=%s)", ghsl_settings.ghsl_data_dir)
                except Exception as e:
                    logger.warning("Failed to create GHSLService: %s", e)
            return self._service


# ============================================================================
# Module-level singletons (one per process)
# ============================================================================

_map_state = MapState()
_cadastral_registry = CadastralRegistry()
_datashader_registry = DatashaderRegistry()
_ghsl_registry = GHSLRegistry()


# ============================================================================
# FastAPI Depends() provider functions
# ============================================================================

def get_map_state() -> MapState:
    return _map_state


def get_cadastral_registry() -> CadastralRegistry:
    return _cadastral_registry


def get_datashader_registry() -> DatashaderRegistry:
    return _datashader_registry
