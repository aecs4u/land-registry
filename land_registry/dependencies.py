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

import contextvars
import logging
import threading
import base64
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

_request_session: contextvars.ContextVar = contextvars.ContextVar("_request_session", default=None)


def empty_datashader_tile() -> bytes:
    """Return a valid transparent PNG without importing optional Datashader."""
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/"
        "eKJx6QAAAABJRU5ErkJggg=="
    )


class _UnavailableDatashaderService:
    """Stable no-op service used when optional raster dependencies are absent."""

    available = False

    def warmup_jit(self) -> None:
        """Keep the optional service interface safe during application startup."""
        return None

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

    def identify_feature(self, *args, **kwargs):
        return None

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
                    from land_registry.datashader_service import DatashaderTileService
                except Exception as e:
                    logger.error("Datashader dependencies unavailable: %s", e)
                    self._service = _UnavailableDatashaderService()
                    return self._service

                db = None
                try:
                    from land_registry.cadastral_db import CadastralDatabase
                    from land_registry.config import db_settings
                    db = CadastralDatabase(Path(db_settings.sqlite_path))
                except Exception as e:
                    # Boundary tiles can still use the canonical PostGIS
                    # source when the local SQLite database is unavailable.
                    logger.warning("Local cadastral DB unavailable: %s", e)

                try:
                    self._service = DatashaderTileService(db)
                    logger.info("DatashaderTileService initialized")
                except Exception as e:
                    logger.error("Failed to initialize DatashaderTileService: %s", e)
                    self._service = _UnavailableDatashaderService()
            return self._service

    def close(self) -> None:
        """Close an initialized service without creating one at shutdown."""
        with self._lock:
            if self._service is not None:
                self._service.close()
                self._service = None


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

class SessionScopedMapState(MapState):
    """``MapState`` that keeps one independent copy per browser session.

    The loaded GeoDataFrame, layers and auction points used to be process-wide,
    so two users uploading files at once saw each other's data. Every call site
    goes through ``_map_state``; this proxy resolves the right copy from the
    session of the request being served (set by ``MapSessionMiddleware``).

    Outside a request (tests, CLI) or before a session has stored anything,
    reads fall through to a shared default state, so existing behaviour is
    unchanged there. The first write inside a request mints the session's id.
    """

    MAX_SESSIONS = 16
    SESSION_KEY = "map_state_id"

    def __init__(self):
        super().__init__()
        self._default = MapState()
        self._sessions: "OrderedDict[str, MapState]" = OrderedDict()
        self._sessions_lock = threading.Lock()

    def _resolve(self, create: bool) -> MapState:
        session = _request_session.get()
        if session is None:
            return self._default
        key = session.get(self.SESSION_KEY)
        if key is None:
            if not create:
                return self._default
            key = session[self.SESSION_KEY] = uuid.uuid4().hex
        with self._sessions_lock:
            state = self._sessions.get(key)
            if state is None:
                if not create:
                    # Nothing stored for this session (or it was evicted). The default
                    # state is only ever written outside requests, so in production
                    # it is empty and this never exposes another user's data.
                    return self._default
                state = self._sessions[key] = MapState()
                while len(self._sessions) > self.MAX_SESSIONS:
                    self._sessions.popitem(last=False)
            self._sessions.move_to_end(key)
            return state

    def get_gdf(self):
        return self._resolve(False).get_gdf()

    def set_gdf(self, gdf) -> None:
        self._resolve(True).set_gdf(gdf)

    def get_display_df(self):
        return self._resolve(False).get_display_df()

    def get_layers(self) -> dict:
        return self._resolve(False).get_layers()

    def set_layers(self, layers: dict) -> None:
        self._resolve(True).set_layers(layers)

    def clear_layers(self) -> None:
        self._resolve(True).clear_layers()

    def get_auction_properties(self):
        return self._resolve(False).get_auction_properties()

    def set_auction_properties(self, props) -> None:
        self._resolve(True).set_auction_properties(props)


class MapSessionMiddleware:
    """Expose the request's session dict to ``SessionScopedMapState``.

    Must be registered *inside* the session middleware (i.e. added before it)
    so ``scope["session"]`` is already populated when this runs.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        session = scope.get("session")
        path = scope.get("path", "")
        # A streaming handler writes its state after the response headers
        # (and so the Set-Cookie) have gone out, which would lose a brand-new
        # session's id. Mint it up front for API calls that can load data.
        # Tile and map-catalog responses are public and cacheable, so they
        # must never carry a Set-Cookie.
        if (
            session is not None
            and path.startswith("/api/v1/")
            and not path.startswith(("/api/v1/tiles/", "/api/v1/map/"))
            and SessionScopedMapState.SESSION_KEY not in session
        ):
            session[SessionScopedMapState.SESSION_KEY] = uuid.uuid4().hex
        token = _request_session.set(session)
        try:
            await self.app(scope, receive, send)
        finally:
            _request_session.reset(token)


_map_state = SessionScopedMapState()
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
