from bokeh.embed import server_document
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import json
import logging
import os
import pandas as pd
import panel as pn
from threading import Thread
from typing import Optional
import asyncio

from land_registry.cadastral_utils import load_cadastral_structure, get_cadastral_stats
from land_registry.dashboard import STATE, TEMPLATE
from land_registry.file_availability_db import file_availability_db
from land_registry.map import get_current_gdf, get_current_layers, map_generator
from land_registry.routers.api import api_router
from land_registry.s3_storage import get_s3_storage
from land_registry.settings import app_settings, get_cadastral_structure_path

# Configure logging
logging.basicConfig(
    level=logging.INFO if not app_settings.debug else logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Panel server configuration
PANEL_HOST = "127.0.0.1"
PANEL_PORT = 5006
PANEL_BASE_URL = f"http://{PANEL_HOST}:{PANEL_PORT}"
PANEL_DASHBOARD_URL = f"{PANEL_BASE_URL}/dashboard"

# Panel server thread reference
_panel_thread: Optional[Thread] = None


def _run_panel():
    """Run Panel server in a background thread"""
    try:
        logger.info(f"Starting Panel server on {PANEL_HOST}:{PANEL_PORT}")
        pn.serve(
            {"dashboard": TEMPLATE},
            port=PANEL_PORT,
            address=PANEL_HOST,
            allow_websocket_origin=[
                f"{PANEL_HOST}:{app_settings.port}",
                f"localhost:{app_settings.port}",
                "127.0.0.1:8000",
                "localhost:8000"
            ],
            show=False,
            threaded=True,
        )
    except Exception as e:
        logger.error(f"Panel server failed to start: {e}", exc_info=True)
        raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for FastAPI app.
    Manages Panel server startup/shutdown and resource cleanup.
    """
    global _panel_thread

    # Startup
    logger.info(f"Starting {app_settings.app_name} v{app_settings.app_version}")

    # Start Panel server in background thread
    _panel_thread = Thread(target=_run_panel, daemon=True, name="PanelServer")
    _panel_thread.start()

    # Wait briefly for Panel server to start
    await asyncio.sleep(1)

    # Verify Panel server is accessible
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(PANEL_BASE_URL)
            if response.status_code == 200:
                logger.info(f"Panel server started successfully at {PANEL_BASE_URL}")
            else:
                logger.warning(f"Panel server returned status {response.status_code}")
    except Exception as e:
        logger.warning(f"Could not verify Panel server status: {e}")

    yield

    # Shutdown
    logger.info("Shutting down application...")

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

# Include the API router with /api/v1 prefix
app.include_router(api_router, prefix="/api/v1")

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


@app.get("/health")
async def health_check():
    """Health check endpoint for Cloud Run"""
    return {"status": "healthy", "service": "land-registry"}


@app.get("/map", response_class=HTMLResponse)
async def read_root(request: Request):
    """Serve the main map application using template with comprehensive map providers"""
    # Get current data status
    current_gdf = get_current_gdf()
    has_data = current_gdf is not None and not current_gdf.empty

    # Convert current data to GeoJSON if available
    geojson_data = None
    if current_gdf is not None and not current_gdf.empty:
        geojson_data = json.loads(current_gdf.to_json())

    # Load cadastral statistics using utility
    stats = get_cadastral_stats()

    # Render template with context
    return templates.TemplateResponse("map.html", {
        "request": request,
        "geojson_data": json.dumps(geojson_data) if geojson_data else None,
        "has_data": has_data,
        "total_regions": stats['total_regions'],
        "total_provinces": stats['total_provinces'],
        "total_municipalities": stats['total_municipalities'],
        "total_files": stats['total_files'],
        "clerk_publishable_key": app_settings.clerk_publishable_key,
        "clerk_domain": app_settings.clerk_domain
    })


@app.get("/map_table")
def show_map_table(request: Request):
    """Display map table using Panel"""
    # Note: All tables currently use the same Panel dashboard
    # TODO: Create separate Panel apps for each table type
    tabulator = server_document(PANEL_DASHBOARD_URL)
    return templates.TemplateResponse("tabulator.html", {
        "request": request,
        "tabulator": tabulator
    })


@app.get("/adjacency_table")
def show_adjacency_table(request: Request):
    """Display adjacency analysis table using Panel"""
    # Note: All tables currently use the same Panel dashboard
    # TODO: Create separate Panel apps for each table type
    tabulator = server_document(PANEL_DASHBOARD_URL)
    return templates.TemplateResponse("tabulator.html", {
        "request": request,
        "tabulator": tabulator
    })


@app.get("/mapping_table")
def show_mapping_table(request: Request):
    """Display mapping/drawing table using Panel"""
    # Note: All tables currently use the same Panel dashboard
    # TODO: Create separate Panel apps for each table type
    tabulator = server_document(PANEL_DASHBOARD_URL)
    return templates.TemplateResponse("tabulator.html", {
        "request": request,
        "tabulator": tabulator
    })


@app.get("/", response_class=HTMLResponse)
async def serve_index(request: Request):
    """Serve the server-generated map application using index.html template"""
    # Get current data status
    current_gdf = get_current_gdf()
    has_data = current_gdf is not None and not current_gdf.empty

    # Convert current data to GeoJSON if available
    geojson_data = None
    if current_gdf is not None and not current_gdf.empty:
        geojson_data = json.loads(current_gdf.to_json())

    # Get current layers data
    current_layers = get_current_layers()

    # Generate comprehensive Folium map using IntegratedMapGenerator
    folium_map = map_generator.create_comprehensive_map(
        cadastral_geojson=geojson_data if not current_layers else None,
        cadastral_layers=current_layers if current_layers else None,
        auction_geojson=None,  # Could add auction data here
        center=[41.9028, 12.4964],  # Rome, Italy
        zoom=6
    )

    # Convert Folium map to HTML
    folium_map_html = folium_map._repr_html_()

    # Load cadastral statistics using utility
    stats = get_cadastral_stats()

    # Get Panel table documents
    # Note: All tables currently use the same Panel dashboard
    # TODO: Create separate Panel apps for each table type
    map_table = server_document(PANEL_DASHBOARD_URL)
    adjacency_table = server_document(PANEL_DASHBOARD_URL)
    mapping_table = server_document(PANEL_DASHBOARD_URL)

    # Render template with context including server-generated Folium map and Tabulator
    return templates.TemplateResponse("index.html", {
        "request": request,
        "folium_map_html": folium_map_html,
        "map_table": map_table,
        "adjacency_table": adjacency_table,
        "mapping_table": mapping_table,
        "geojson_data": json.dumps(geojson_data) if geojson_data else None,
        "has_data": has_data,
        "total_regions": stats['total_regions'],
        "total_provinces": stats['total_provinces'],
        "total_municipalities": stats['total_municipalities'],
        "total_files": stats['total_files'],
        "clerk_publishable_key": app_settings.clerk_publishable_key,
        "clerk_domain": app_settings.clerk_domain
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
        return templates.TemplateResponse("cadastral_data.html", {
            "request": request,
            "cadastral_data": cadastral_data,
            "municipality_flags": municipality_flags,
            "total_regions": stats['total_regions'],
            "total_provinces": stats['total_provinces'],
            "total_municipalities": stats['total_municipalities'],
            "total_files": stats['total_files'],
            "available_files": available_files,
            "missing_files": missing_files,
            "uncached_files": uncached_files  # Now included in response
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


@app.get("/api/v1/table-data")
async def get_table_data(
    page: int = 1,
    size: int = 100,
    search: Optional[str] = None,
    sort_field: Optional[str] = None,
    sort_dir: Optional[str] = "asc",
    filter_field: Optional[str] = None,
    filter_value: Optional[str] = None
):
    """Get paginated table data for the current GeoDataFrame with server-side filtering and sorting"""
    try:
        # Run heavy I/O in thread pool to avoid blocking event loop
        current_gdf = await asyncio.to_thread(get_current_gdf)

        if current_gdf is None or current_gdf.empty:
            return {
                "data": [],
                "total": 0,
                "page": page,
                "size": size,
                "total_pages": 0,
                "columns": []
            }

        # Convert GeoDataFrame to regular DataFrame for table display
        df = current_gdf.copy()

        # Drop geometry column once and convert to pandas DataFrame
        if 'geometry' in df.columns:
            df = pd.DataFrame(df.drop(columns=['geometry']))
        else:
            df = pd.DataFrame(df)

        # Apply field-specific filter if provided
        if filter_field and filter_value and filter_field in df.columns:
            # Convert to string only for the specific column
            df = df[df[filter_field].astype(str).str.contains(
                filter_value, case=False, na=False, regex=False
            )]

        # Apply global search filter if provided
        if search:
            # Build search mask efficiently by checking each column
            search_lower = search.lower()
            mask = df.apply(
                lambda col: col.astype(str).str.lower().str.contains(
                    search_lower, na=False, regex=False
                ),
                axis=0
            ).any(axis=1)
            df = df[mask]

        # Apply sorting if provided
        if sort_field and sort_field in df.columns:
            ascending = sort_dir.lower() == "asc"
            df = df.sort_values(by=sort_field, ascending=ascending)

        # Calculate pagination
        total = len(df)
        total_pages = (total + size - 1) // size  # Ceiling division
        start_idx = (page - 1) * size
        end_idx = start_idx + size

        # Get page data
        page_data = df.iloc[start_idx:end_idx]

        # Convert to records format for JSON serialization
        data = page_data.to_dict('records')

        return {
            "data": data,
            "total": total,
            "page": page,
            "size": size,
            "total_pages": total_pages,
            "columns": list(df.columns) if not df.empty else [],
            "filtered_total": total  # Total after filtering
        }

    except Exception as e:
        logger.error(f"Error fetching table data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error fetching table data: {str(e)}")


@app.get("/api/v1/adjacency-data")
async def get_adjacency_data(
    page: int = 1,
    size: int = 100,
    search: Optional[str] = None,
    sort_field: Optional[str] = None,
    sort_dir: Optional[str] = "asc",
    filter_field: Optional[str] = None,
    filter_value: Optional[str] = None
):
    """Get paginated adjacency analysis data with server-side filtering and sorting"""
    try:
        # For now, return empty data since adjacency analysis is not implemented in the current map.py
        # This endpoint will be populated when adjacency analysis functionality is available
        return {
            "data": [],
            "total": 0,
            "page": page,
            "size": size,
            "total_pages": 0,
            "columns": [],
            "filtered_total": 0
        }
    except Exception as e:
        logger.error(f"Error fetching adjacency data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error fetching adjacency data: {str(e)}")


@app.get("/api/v1/mapping-data")
async def get_mapping_data(
    page: int = 1,
    size: int = 100,
    search: Optional[str] = None,
    sort_field: Optional[str] = None,
    sort_dir: Optional[str] = "asc",
    filter_field: Optional[str] = None,
    filter_value: Optional[str] = None
):
    """Get paginated mapping/drawing data with server-side filtering and sorting"""
    try:
        # For now, return empty data since drawn polygon storage is not implemented
        # This endpoint will be populated when drawing functionality stores data properly
        return {
            "data": [],
            "total": 0,
            "page": page,
            "size": size,
            "total_pages": 0,
            "columns": [],
            "filtered_total": 0
        }
    except Exception as e:
        logger.error(f"Error fetching mapping data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error fetching mapping data: {str(e)}")
