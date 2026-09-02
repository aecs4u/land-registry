from bokeh.embed import server_document
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.gzip import GZipMiddleware
import json
import logging
import os
import duckdb
import pandas as pd
import panel as pn
from typing import Optional
import asyncio
import threading
from tornado.ioloop import IOLoop

from land_registry.cadastral_utils import load_cadastral_structure, get_cadastral_stats
from land_registry.dashboard import TEMPLATE
from land_registry.file_availability_db import file_availability_db
from land_registry.i18n import LocaleMiddleware, detect_locale, make_gettext, contextvar_gettext
from land_registry.map import get_current_gdf, map_generator
from land_registry.dependencies import _map_state
from land_registry.routers.api import api_router
from land_registry.routers.auth_pages import router as auth_pages_router
from land_registry.routers.enrichment import enrichment_router
from land_registry.s3_storage import get_s3_storage
from land_registry.config import app_settings, panel_settings, get_panel_url
from land_registry.models import TableDataResponse, ServiceUnavailableResponse

# Import aecs4u-auth for authentication setup (optional)
from land_registry.core.clerk import _AUTH_AVAILABLE

if _AUTH_AVAILABLE:
    from aecs4u_auth import setup_auth, AuthConfig, get_auth_config, create_clerk_router
else:
    from types import SimpleNamespace

    def get_auth_config():
        return SimpleNamespace(clerk_publishable_key="")

# Import aecs4u-theme (optional)
try:
    from aecs4u_theme import setup_theme_from_env
    _THEME_AVAILABLE = True
except ImportError:
    _THEME_AVAILABLE = False

# Configure logging
logging.basicConfig(
    level=logging.INFO if not app_settings.debug else logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Panel server configuration (from settings)
PANEL_HOST = panel_settings.panel_host
PANEL_PORT = panel_settings.panel_port
PANEL_BASE_URL = get_panel_url()
PANEL_DASHBOARD_URL = get_panel_url(panel_settings.panel_dashboard_route)

# Panel table URLs (currently all point to same dashboard)
PANEL_MAP_TABLE_URL = get_panel_url(panel_settings.panel_map_table_route)
PANEL_ADJACENCY_TABLE_URL = get_panel_url(panel_settings.panel_adjacency_table_route)
PANEL_MAPPING_TABLE_URL = get_panel_url(panel_settings.panel_mapping_table_route)

# Panel server management
_panel_server = None  # Will hold the Bokeh Server instance
_panel_thread: Optional[threading.Thread] = None
_panel_ioloop: Optional[IOLoop] = None
_panel_already_running = False  # Track if we're reusing an existing server


def _is_port_in_use(host: str, port: int) -> bool:
    """Check if a port is already in use."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return False
        except OSError:
            return True


def _run_panel_server_blocking():
    """
    Run Panel server in a blocking manner (runs in separate thread).
    Stores references for proper cleanup.
    """
    global _panel_server, _panel_ioloop
    try:
        logger.info(f"Starting Panel server on {PANEL_HOST}:{PANEL_PORT}")

        # Build websocket origins list including main app port and Panel server itself
        websocket_origins = list(panel_settings.panel_websocket_origins)
        websocket_origins.extend([
            f"{PANEL_HOST}:{app_settings.port}",
            f"localhost:{app_settings.port}",
            # Panel server must allow connections from itself
            f"{PANEL_HOST}:{PANEL_PORT}",
            f"localhost:{PANEL_PORT}"
        ])

        # Create a new IOLoop for this thread
        _panel_ioloop = IOLoop(make_current=True)

        # Use pn.serve which returns a Server instance when threaded=False
        _panel_server = pn.serve(
            {"dashboard": TEMPLATE},
            port=PANEL_PORT,
            address=PANEL_HOST,
            allow_websocket_origin=websocket_origins,
            show=panel_settings.panel_show,
            threaded=False,  # We manage threading ourselves
            start=True,  # Start the server
            session_token_expiration=86400,  # 24 hours to avoid token expiration errors
        )
    except OSError as e:
        if "Address already in use" in str(e):
            # Port is in use - likely from a previous hot-reload
            logger.warning(f"Panel port {PANEL_PORT} already in use - will reuse existing server")
        else:
            logger.error(f"Panel server failed: {e}", exc_info=True)
            raise
    except Exception as e:
        logger.error(f"Panel server failed: {e}", exc_info=True)
        raise


def _stop_panel_server():
    """
    Stop the Panel server gracefully.
    """
    global _panel_server, _panel_ioloop, _panel_thread

    if _panel_server is not None:
        try:
            logger.info("Stopping Panel server...")
            # Stop the server
            _panel_server.stop()
            _panel_server = None
            logger.info("Panel server stopped")
        except Exception as e:
            logger.error(f"Error stopping Panel server: {e}", exc_info=True)

    if _panel_ioloop is not None:
        try:
            # Stop the IOLoop - this will cause the thread to exit
            _panel_ioloop.add_callback(_panel_ioloop.stop)
            _panel_ioloop = None
        except Exception as e:
            logger.error(f"Error stopping Panel IOLoop: {e}", exc_info=True)

    if _panel_thread is not None and _panel_thread.is_alive():
        try:
            # Wait for thread to finish
            _panel_thread.join(timeout=5.0)
            if _panel_thread.is_alive():
                logger.warning("Panel thread did not stop gracefully")
            else:
                logger.info("Panel thread stopped")
        except Exception as e:
            logger.error(f"Error joining Panel thread: {e}", exc_info=True)
        finally:
            _panel_thread = None


async def _health_check_panel() -> bool:
    """
    Health check for Panel server with retry logic.
    Returns True if Panel server is accessible, False otherwise.
    """
    import httpx

    max_retries = int(panel_settings.panel_startup_timeout / panel_settings.panel_startup_retry_delay)

    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=panel_settings.panel_health_check_timeout, follow_redirects=True) as client:
                response = await client.get(PANEL_BASE_URL)
                # Accept 200 (OK), 302 (redirect), or 3xx (redirects) as success
                if response.status_code in (200, 302) or (300 <= response.status_code < 400):
                    logger.info(f"Panel server health check passed (attempt {attempt + 1}/{max_retries}, status {response.status_code})")
                    logger.info(f"Panel server accessible at {PANEL_BASE_URL}")
                    return True
                else:
                    logger.debug(f"Panel server returned status {response.status_code} (attempt {attempt + 1}/{max_retries})")
        except Exception as e:
            logger.debug(f"Panel health check attempt {attempt + 1}/{max_retries} failed: {e}")

        if attempt < max_retries - 1:
            await asyncio.sleep(panel_settings.panel_startup_retry_delay)

    logger.error(f"Panel server health check failed after {max_retries} attempts")
    return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for FastAPI app.
    Manages Panel server startup/shutdown and resource cleanup.
    """
    global _panel_thread, _panel_already_running

    # ========== STARTUP ==========
    logger.info(f"Starting {app_settings.app_name} v{app_settings.app_version}")

    # Check if Panel server is already running (e.g., from a previous hot-reload)
    if _is_port_in_use(PANEL_HOST, PANEL_PORT):
        logger.info(f"Panel port {PANEL_PORT} already in use - checking if it's accessible...")
        # Try a health check to see if it's our Panel server
        panel_ready = await _health_check_panel()
        if panel_ready:
            logger.info("Existing Panel server is healthy - reusing it")
            _panel_already_running = True
        else:
            # Port is bound but server not responding - wait for it to be released
            logger.warning("Port in use but Panel not responding - waiting for release...")
            _panel_already_running = False
            # Wait up to 5 seconds for the port to be released
            for i in range(10):
                await asyncio.sleep(0.5)
                if not _is_port_in_use(PANEL_HOST, PANEL_PORT):
                    logger.info("Port released, proceeding with startup")
                    break
            else:
                logger.warning("Port still in use after waiting - will try to start anyway")
    else:
        _panel_already_running = False

    # Start Panel server only if not already running
    if not _panel_already_running:
        _panel_thread = threading.Thread(
            target=_run_panel_server_blocking,
            name="PanelServer",
            daemon=True  # Ensures thread stops when main process exits
        )
        _panel_thread.start()
        logger.info("Panel server thread started")

        # Wait for Panel server to be ready with health checks
        panel_ready = await _health_check_panel()

        if not panel_ready:
            # If we can't start Panel, continue anyway but log warning
            # This allows the main app to function without Panel dashboard
            logger.warning("Panel server failed to start - continuing without dashboard")
            # Don't raise - let the app run without Panel
            # raise RuntimeError("Panel server failed to start")

    # Pay datashader/numba's one-time JIT compile cost (~7s) in the background
    # so the first real cadastral boundary tile request doesn't stall on it.
    try:
        from land_registry.dependencies import get_datashader_registry

        service = get_datashader_registry().get_service()
        asyncio.create_task(asyncio.to_thread(service.warmup_jit))
    except Exception as e:
        logger.warning(f"Could not schedule datashader JIT warm-up (non-fatal): {e}")

    logger.info(f"Application startup complete - Panel server ready at {PANEL_DASHBOARD_URL}")

    yield

    # ========== SHUTDOWN ==========
    logger.info("Shutting down application...")

    # Stop Panel server gracefully (only if we started it)
    if not _panel_already_running:
        _stop_panel_server()
    else:
        logger.info("Keeping existing Panel server running (for hot-reload)")

    # Close database connections
    try:
        file_availability_db.close_connection()
        logger.info("Database connections closed")
    except Exception as e:
        logger.error(f"Error closing database: {e}", exc_info=True)

    # Clear S3 client cache
    try:
        s3_storage = get_s3_storage()
        if hasattr(s3_storage, '_client') and s3_storage._client:
            s3_storage._client = None
        logger.info("S3 client cleared")
    except Exception as e:
        logger.error(f"Error clearing S3 client: {e}", exc_info=True)

    logger.info("Application shutdown complete")


# Create FastAPI app with lifespan
app = FastAPI(
    title=app_settings.app_name,
    version=app_settings.app_version,
    debug=app_settings.debug,
    lifespan=lifespan
)

# aecs4u_auth's SecurityHeadersMiddleware (added below by setup_auth) stamps
# every response with Cross-Origin-Resource-Policy: same-origin, which blocks
# the cadastral tile endpoint from being embedded as a Leaflet TileLayer on
# real-estates' sales map (a different origin) — browsers reject the <img>
# load with ERR_BLOCKED_BY_RESPONSE.NotSameOrigin even though CORS headers are
# fine. Added *before* setup_auth() so it wraps outermost and runs last on the
# way out, letting it override that one header for just this public,
# non-sensitive tile route without loosening CORP anywhere else.
class _CadastralTileCorpMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith("/api/v1/tiles/cadastral-boundaries/"):
            async def send_wrapper(message):
                if message["type"] == "http.response.start":
                    headers = [
                        (k, v) for k, v in message["headers"]
                        if k.lower() != b"cross-origin-resource-policy"
                    ]
                    headers.append((b"cross-origin-resource-policy", b"cross-origin"))
                    message["headers"] = headers
                await send(message)
            await self.app(scope, receive, send_wrapper)
        else:
            await self.app(scope, receive, send)


app.add_middleware(_CadastralTileCorpMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)

# Setup authentication using aecs4u-auth (if available)
if _AUTH_AVAILABLE:
    # This automatically:
    # - Adds session middleware
    # - Includes auth routes at /auth prefix
    # - Sets up exception handlers for RedirectToLogin
    # - Mounts static files for frontend auth integration
    setup_auth(
        app,
        config=AuthConfig(
            site_id="land-registry",
            site_name=app_settings.app_name,
            # Override default redirect URLs for this app
            clerk_after_sign_in_url="/map",
            clerk_after_sign_up_url="/map",
        ),
        include_routes=True,
        mount_static=True,
        setup_exception_handlers=True,
    )
else:
    logger.warning("aecs4u-auth not installed - running without authentication")

# Setup theme using aecs4u-theme — reads AECS4U_SITE_NAME, THEME_PRIMARY_COLOR etc. from env
if _THEME_AVAILABLE:
    setup_theme_from_env(app, static_url_path="/static/aecs4u-theme")
else:
    logger.warning("aecs4u-theme not installed - running without theme package")

# Locale detection middleware (cookie → Accept-Language → default 'it')
app.add_middleware(LocaleMiddleware)

# Include HTML auth pages (login/register forms) at /auth prefix
# These provide GET endpoints for browser-accessible pages
app.include_router(auth_pages_router, prefix="/auth", tags=["auth"])

# Include Clerk API routes (/auth/clerk/session, /auth/clerk/logout, /auth/clerk/callback)
# These are the backend endpoints that clerk-auth.js calls for session sync and logout
if _AUTH_AVAILABLE:
    _clerk_pair = create_clerk_router()
    app.include_router(_clerk_pair.api_router, tags=["auth"])

# Include the API router with /api/v1 prefix
app.include_router(api_router, prefix="/api/v1")

# Parcel enrichment backed by aecs4u-stats (ISTAT reference data, OSM POIs)
app.include_router(enrichment_router, prefix="/api/v1/enrichment", tags=["enrichment"])

root_folder = os.path.dirname(__file__)

# Get absolute paths for static files and templates
static_dir = os.path.join(root_folder, "static")
templates_dir = os.path.join(root_folder, "templates")

# Ensure directories exist
if not os.path.exists(static_dir):
    logger.warning(f"Static directory not found at {static_dir}")
if not os.path.exists(templates_dir):
    logger.warning(f"Templates directory not found at {templates_dir}")

# Serve static files (HTML, CSS, JS) with absolute path
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
else:
    logger.error("Static files directory not found - static content will not be served")

templates = Jinja2Templates(directory=templates_dir)
templates.env.globals["_"] = contextvar_gettext
# locale is passed per-request in template context (set by each route handler)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/static/aecs4u-theme/icons/favicon.png", status_code=301)


@app.get("/health")
async def health_check():
    """Health check endpoint for Cloud Run"""
    return {"status": "healthy", "service": "land-registry"}


async def _build_main_map_shell_context(request: Request) -> dict:
    """Build shared template context for the canonical map shell.

    This is async so expensive map/data preparation can run off the event loop.
    """
    # Optional ?lat=&lng=&zoom= permalink state (e.g. shared/bookmarked links).
    # Falls back to the default Rome view when absent or malformed.
    map_center = [41.9028, 12.4964]
    map_zoom = 6
    try:
        lat_param = request.query_params.get("lat")
        lng_param = request.query_params.get("lng")
        if lat_param is not None and lng_param is not None:
            lat, lng = float(lat_param), float(lng_param)
            if -90 <= lat <= 90 and -180 <= lng <= 180:
                map_center = [lat, lng]
        zoom_param = request.query_params.get("zoom")
        if zoom_param is not None:
            zoom = int(float(zoom_param))
            # Keep permalink restoration aligned with the Leaflet map and
            # cadastral overlays, which intentionally support overzoom to 22.
            if 5 <= zoom <= 22:
                map_zoom = zoom
    except (TypeError, ValueError):
        pass
    # Get current data status
    current_gdf = get_current_gdf()
    has_data = current_gdf is not None and not current_gdf.empty

    # NOTE: _sync_to_panel is disabled as it can cause deadlock due to param watchers
    # blocking the async event loop when self.version += 1 triggers Panel recompute.
    # Data sync happens via API endpoints instead.

    # Convert current data to GeoJSON if available. GeoPandas serialization is
    # CPU-heavy for large selections, so keep it off the async event loop.
    geojson_data = None
    if current_gdf is not None and not current_gdf.empty:
        geojson_data = await asyncio.to_thread(
            lambda: json.loads(current_gdf.to_json())
        )

    # Cadastral features are rendered by the client from window.geoJsonData.
    # Embedding them in Folium as well duplicates the payload and parse cost.
    folium_map = await asyncio.to_thread(
        map_generator.create_comprehensive_map,
        cadastral_geojson=None,
        cadastral_layers=None,
        auction_geojson=None,  # Could add auction data here
        center=map_center,  # Rome, Italy by default; overridable via ?lat=&lng=
        zoom=map_zoom,
    )

    # Inject Folium HTML directly into the page (not as srcdoc iframe).
    # An srcdoc iframe creates a separate browsing context, which prevents
    # folium-interface.js from finding `.leaflet-container` or `window[mapId]`
    # — breaking both polygon rendering and map controls.
    # Browsers handle nested <html>/<body> tags from Folium's output gracefully
    # (treat them as parse errors and absorb content into the parent document),
    # so Leaflet initialises in the parent window where all JS expects it.
    folium_map_html = folium_map.get_root().render()

    # Load cadastral statistics without blocking concurrent map/API requests.
    stats = await asyncio.to_thread(get_cadastral_stats)

    locale = detect_locale(request)
    gt = make_gettext(locale)

    # JS-side translated strings (injected as window._i18n via i18n_data block)
    i18n_strings = {
        "0 polygons selected": gt("0 polygons selected"),
        "1 polygon selected": gt("1 polygon selected"),
        "{n} polygons selected": gt("{n} polygons selected"),
        "No polygons loaded": gt("No polygons loaded"),
        "{n} polygons on map": gt("{n} polygons on map"),
        "Loading...": gt("Loading..."),
        "Preparing...": gt("Preparing..."),
        "0 files": gt("0 files"),
        "0 features": gt("0 features"),
        "No data loaded": gt("No data loaded"),
        "Analyzing spatial relationships...": gt("Analyzing spatial relationships..."),
        "No zones saved yet.": gt("No zones saved yet."),
        "No zones match the search.": gt("No zones match the search."),
        "Select region…": gt("Select region…"),
        "Select province…": gt("Select province…"),
        "Select municipality…": gt("Select municipality…"),
        "Select a municipality before searching.": gt("Select a municipality before searching."),
        "No results": gt("No results"),
    }

    return {
        "request": request,
        "_": gt,
        "locale": locale,
        "i18n_strings": i18n_strings,
        "folium_map_html": folium_map_html,
        "geojson_data": geojson_data,
        "has_data": has_data,
        "total_regions": stats['total_regions'],
        "total_provinces": stats['total_provinces'],
        "total_municipalities": stats['total_municipalities'],
        "total_files": stats['total_files'],
        "carto_enabled": map_generator.controls_manager.settings.carto_enabled,
        "carto_api_key": map_generator.controls_manager.settings.carto_api_key,
        "clerk_publishable_key": get_auth_config().clerk_publishable_key,
    }


@app.get("/map", response_class=HTMLResponse)
async def serve_map_shell(request: Request):
    """Serve the canonical map shell with full workflow capabilities."""
    context = await _build_main_map_shell_context(request)
    context.pop("request", None)  # starlette 1.x injects request automatically
    return templates.TemplateResponse(request, "index.html", context)


@app.get("/map_table")
async def show_map_table(request: Request):
    """
    Display map table using Panel.
    Uses configured Panel route from settings (currently points to main dashboard).
    """
    tabulator = await asyncio.to_thread(server_document, PANEL_MAP_TABLE_URL)
    return templates.TemplateResponse(request, "tabulator.html", {
        "tabulator": tabulator
    })


@app.get("/adjacency_table")
async def show_adjacency_table(request: Request):
    """
    Display adjacency analysis table using Panel.
    Uses configured Panel route from settings (currently points to main dashboard).
    """
    tabulator = await asyncio.to_thread(server_document, PANEL_ADJACENCY_TABLE_URL)
    return templates.TemplateResponse(request, "tabulator.html", {
        "tabulator": tabulator
    })


@app.get("/mapping_table")
async def show_mapping_table(request: Request):
    """
    Display mapping/drawing table using Panel.
    Uses configured Panel route from settings (currently points to main dashboard).
    """
    tabulator = await asyncio.to_thread(server_document, PANEL_MAPPING_TABLE_URL)
    return templates.TemplateResponse(request, "tabulator.html", {
        "tabulator": tabulator
    })


@app.get("/", response_class=HTMLResponse)
async def redirect_root_to_map():
    """Redirect root to canonical map shell."""
    return RedirectResponse(url="/map", status_code=307)


@app.get("/landing", response_class=HTMLResponse)
async def landing_page(request: Request):
    """Landing page summarizing all application features"""
    stats = get_cadastral_stats()
    locale = detect_locale(request)
    return templates.TemplateResponse(request, "landing.html", {
        "_": make_gettext(locale),
        "locale": locale,
        "total_regions": stats['total_regions'],
        "total_provinces": stats['total_provinces'],
        "total_municipalities": stats['total_municipalities'],
        "total_files": stats['total_files'],
        "app_name": app_settings.app_name,
        "app_version": app_settings.app_version,
    })


@app.get("/cadastral-data", response_class=HTMLResponse)
async def show_cadastral_data(request: Request):
    """Display the Italian cadastral data structure in a readable HTML format"""
    try:
        # Load cadastral data using utility
        cadastral = load_cadastral_structure()
        if not cadastral:
            raise HTTPException(
                status_code=404,
                detail="Cadastral structure file not found in S3 or locally"
            )

        cadastral_data = cadastral.data
        stats = cadastral.stats

        # Load municipality flags data
        municipality_flags = {}
        try:
            flags_paths = [
                os.path.join(root_folder, "../data/municipality_flags.json"),
                "/app/data/municipality_flags.json",
                os.path.join(os.getcwd(), "data/municipality_flags.json"),
            ]

            for path in flags_paths:
                if os.path.exists(path):
                    with open(path, 'r', encoding='utf-8') as f:
                        municipality_flags = json.load(f)
                    logger.info(f"Loaded municipality flags from {path}")
                    break
        except Exception as e:
            logger.warning(f"Could not load municipality flags: {e}")

        # Calculate file availability from SQLite cache
        available_files = 0
        missing_files = 0
        uncached_files = 0

        try:
            # Collect all file paths
            all_file_paths = []
            for region_name, region_data in cadastral_data.items():
                for province_code, province_data in region_data.items():
                    for municipality_key, municipality_data in province_data.items():
                        if isinstance(municipality_data, dict):
                            files = municipality_data.get('files', [])
                            # Collect S3 keys for cache lookup
                            for file_name in files:
                                s3_key = f"ITALIA/{region_name}/{province_code}/{municipality_key}/{file_name}"
                                all_file_paths.append(s3_key)

            # Get cached file availability status
            cached_statuses = file_availability_db.get_file_status_batch(
                all_file_paths,
                max_age_hours=24
            )

            # Count available and missing files from cache
            for s3_key in all_file_paths:
                if s3_key in cached_statuses:
                    status_code = cached_statuses[s3_key]
                    if status_code == 200:
                        available_files += 1
                    elif status_code == 404:
                        missing_files += 1
                    # Other status codes (errors) are not counted as available or missing

            # Files not in cache are considered unknown
            uncached_files = len(all_file_paths) - len(cached_statuses)

        except Exception as cache_error:
            logger.error(f"Could not access file availability cache: {cache_error}", exc_info=True)

        # Render template with cadastral data and flags
        locale = detect_locale(request)
        return templates.TemplateResponse(request, "cadastral_data.html", {
            "_": make_gettext(locale),
            "locale": locale,
            "cadastral_data": cadastral_data,
            "municipality_flags": municipality_flags,
            "total_regions": stats['total_regions'],
            "total_provinces": stats['total_provinces'],
            "total_municipalities": stats['total_municipalities'],
            "total_files": stats['total_files'],
            "available_files": available_files,
            "missing_files": missing_files,
            "uncached_files": uncached_files,
            "clerk_publishable_key": get_auth_config().clerk_publishable_key,
        })

    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error parsing cadastral structure file: {str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error reading cadastral structure: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Error reading cadastral structure: {str(e)}"
        )


@app.get("/api/v1/table-data", response_model=TableDataResponse)
async def get_table_data(
    page: int = 1,
    size: int = 100,
    search: Optional[str] = None,
    sort_field: Optional[str] = None,
    sort_dir: Optional[str] = "asc",
    filter_field: Optional[str] = None,
    filter_value: Optional[str] = None
):
    """Get paginated table data for the current GeoDataFrame with server-side filtering and sorting."""
    def _query(df: pd.DataFrame) -> dict:
        if df is None or len(df) == 0:
            return {"data": [], "total": 0, "page": page, "size": size,
                    "total_pages": 0, "columns": []}

        # Preserve original GeoDataFrame row index so the frontend can look up geometry
        df = df.reset_index(drop=True).copy()
        df["__idx__"] = df.index

        con = duckdb.connect()
        con.register("parcels", df)

        # --- build WHERE clause -------------------------------------------------
        conditions: list[str] = []
        params: list = []

        if filter_field and filter_value:
            # Quote identifier; reject names that contain double-quotes to avoid injection
            if '"' not in filter_field and filter_field in df.columns:
                conditions.append(f'LOWER(CAST("{filter_field}" AS VARCHAR)) LIKE ?')
                params.append(f"%{filter_value.lower()}%")

        if search:
            col_conditions = [
                f'LOWER(CAST("{c}" AS VARCHAR)) LIKE ?'
                for c in df.columns
                if '"' not in c
            ]
            if col_conditions:
                conditions.append(f"({' OR '.join(col_conditions)})")
                params.extend([f"%{search.lower()}%"] * len(col_conditions))

        where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        # --- count total matching rows -----------------------------------------
        total = con.execute(
            f"SELECT COUNT(*) FROM parcels {where_sql}", params
        ).fetchone()[0]

        if total == 0:
            return {"data": [], "total": 0, "page": page, "size": size,
                    "total_pages": 0, "columns": list(df.columns)}

        # --- ORDER BY -----------------------------------------------------------
        order_sql = ""
        if sort_field and '"' not in sort_field and sort_field in df.columns:
            direction = "DESC" if (sort_dir or "asc").lower() == "desc" else "ASC"
            order_sql = f'ORDER BY "{sort_field}" {direction}'

        # --- paginated fetch ----------------------------------------------------
        offset = (page - 1) * size
        result = con.execute(
            f"SELECT * FROM parcels {where_sql} {order_sql} LIMIT ? OFFSET ?",
            params + [size, offset],
        ).fetchdf()

        total_pages = (total + size - 1) // size
        records = result.to_dict("records")
        return {
            "data": records,
            "total": total,
            "page": page,
            "size": size,
            "total_pages": total_pages,
            "columns": [c for c in df.columns if c != "__idx__"],
            "filtered_total": total,
        }

    try:
        df = await asyncio.to_thread(_map_state.get_display_df)
        return await asyncio.to_thread(_query, df)
    except Exception as e:
        logger.error(f"Error fetching table data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error fetching table data: {str(e)}")


@app.get(
    "/api/v1/adjacency-data",
    response_model=TableDataResponse,
    responses={503: {"model": ServiceUnavailableResponse}}
)
async def get_adjacency_data(
    page: int = 1,
    size: int = 100,
    search: Optional[str] = None,
    sort_field: Optional[str] = None,
    sort_dir: Optional[str] = "asc",
    filter_field: Optional[str] = None,
    filter_value: Optional[str] = None
):
    """
    Get paginated adjacency analysis data with server-side filtering and sorting.

    NOTE: This feature is currently not implemented.
    """
    # Adjacency analysis feature is not yet implemented
    logger.info("Adjacency data endpoint called - feature not implemented")
    raise HTTPException(
        status_code=503,
        detail="Adjacency analysis feature is not yet implemented. This endpoint will be available in a future release.",
        headers={"Retry-After": ""}
    )


@app.get(
    "/api/v1/mapping-data",
    response_model=TableDataResponse,
    responses={503: {"model": ServiceUnavailableResponse}}
)
async def get_mapping_data(
    page: int = 1,
    size: int = 100,
    search: Optional[str] = None,
    sort_field: Optional[str] = None,
    sort_dir: Optional[str] = "asc",
    filter_field: Optional[str] = None,
    filter_value: Optional[str] = None
):
    """
    Get paginated mapping/drawing data with server-side filtering and sorting.

    NOTE: This feature is currently not implemented.
    """
    # Mapping/drawing data storage feature is not yet implemented
    logger.info("Mapping data endpoint called - feature not implemented")
    raise HTTPException(
        status_code=503,
        detail="Mapping/drawing data storage feature is not yet implemented. This endpoint will be available in a future release.",
        headers={"Retry-After": ""}
    )
