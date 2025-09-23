from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import json
import os

from land_registry.map import map_controls
from land_registry.s3_storage import get_s3_storage
from land_registry.routers.api import api_router
from land_registry.file_availability_db import file_availability_db
from land_registry.settings import app_settings, get_cadastral_structure_path


app = FastAPI(
    title=app_settings.app_name,
    version=app_settings.app_version,
    debug=app_settings.debug
)

# Include the API router with /api/v1 prefix
app.include_router(api_router, prefix="/api/v1")

root_folder = os.path.dirname(__file__)

# Get absolute paths for static files and templates
static_dir = os.path.join(root_folder, "static")
templates_dir = os.path.join(root_folder, "templates")

# Ensure directories exist
if not os.path.exists(static_dir):
    print(f"Warning: Static directory not found at {static_dir}")
if not os.path.exists(templates_dir):
    print(f"Warning: Templates directory not found at {templates_dir}")

# Serve static files (HTML, CSS, JS) with absolute path
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
else:
    print("Static files directory not found - static content will not be served")

templates = Jinja2Templates(directory=templates_dir)


@app.get("/health")
async def health_check():
    """Health check endpoint for Cloud Run"""
    return {"status": "healthy", "service": "land-registry"}


@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    """Serve the main map application using template with comprehensive map providers"""
    # Generate Python-based controls HTML and JavaScript
    controls_html = map_controls.generate_html()
    controls_js = map_controls.generate_javascript()

    # Get current data status
    from land_registry.map import get_current_gdf
    current_gdf = get_current_gdf()
    has_data = current_gdf is not None and not current_gdf.empty

    # Convert current data to GeoJSON if available
    geojson_data = None
    if current_gdf is not None and not current_gdf.empty:
        geojson_data = json.loads(current_gdf.to_json())

    # Load cadastral structure data
    total_regions = 0
    total_provinces = 0
    total_municipalities = 0
    total_files = 0

    try:
        # Try to load cadastral structure to get real counts
        cadastral_data = None

        # Try S3 first (but handle credentials gracefully)
        try:
            s3_storage = get_s3_storage()
            cadastral_data = s3_storage.get_cadastral_structure()
        except Exception as s3_error:
            cadastral_data = None

        if not cadastral_data:
            # Fallback to local file using centralized settings
            cadastral_path = get_cadastral_structure_path()
            if cadastral_path:
                with open(cadastral_path, 'r', encoding='utf-8') as f:
                    cadastral_data = json.load(f)

        # Calculate totals from cadastral data
        if cadastral_data and isinstance(cadastral_data, dict):
            total_regions = len(cadastral_data)
            total_provinces = sum(len(region) for region in cadastral_data.values())
            total_municipalities = sum(
                len(province)
                for region in cadastral_data.values()
                for province in region.values()
            )
            # Correct file counting
            total_files = sum(
                len(municipality.get('files', []))
                for region in cadastral_data.values()
                for province in region.values()
                for municipality in province.values()
                if isinstance(municipality, dict)
            )
    except Exception as e:
        print(f"Warning: Could not load cadastral structure data: {e}")
        # Keep default values of 0

    # Render template with context
    return templates.TemplateResponse("map.html", {
        "request": request,
        "controls_html": controls_html,
        "controls_js": controls_js,
        "geojson_data": json.dumps(geojson_data) if geojson_data else None,
        "has_data": has_data,
        "total_regions": total_regions,
        "total_provinces": total_provinces,
        "total_municipalities": total_municipalities,
        "total_files": total_files
    })


@app.get("/cadastral-data", response_class=HTMLResponse)
async def show_cadastral_data(request: Request):
    """Display the Italian cadastral data structure in a readable HTML format"""
    try:
        cadastral_data = None

        # Try S3 first (but handle credentials gracefully)
        try:
            s3_storage = get_s3_storage()
            cadastral_data = s3_storage.get_cadastral_structure()
        except Exception as s3_error:
            print(f"S3 access failed: {s3_error}")
            cadastral_data = None

        if not cadastral_data:
            # Fallback to local files using centralized settings
            cadastral_file_path = get_cadastral_structure_path()

            if not cadastral_file_path:
                raise HTTPException(status_code=404, detail="Cadastral structure file not found in S3 or locally")

            with open(cadastral_file_path, 'r', encoding='utf-8') as f:
                cadastral_data = json.load(f)

        # Load municipality flags data
        municipality_flags = {}
        try:
            flags_paths = [
                os.path.join(root_folder, "../data/municipality_flags.json"),
                os.path.join("/app/data/municipality_flags.json"),
                os.path.join(os.getcwd(), "data/municipality_flags.json"),
            ]

            for path in flags_paths:
                if os.path.exists(path):
                    with open(path, 'r', encoding='utf-8') as f:
                        municipality_flags = json.load(f)
                    break
        except Exception as e:
            print(f"Warning: Could not load municipality flags: {e}")

        # Calculate statistics and file availability
        total_regions = len(cadastral_data) if cadastral_data else 0
        total_provinces = sum(len(region) for region in cadastral_data.values()) if cadastral_data else 0
        total_municipalities = sum(
            len(province)
            for region in cadastral_data.values()
            for province in region.values()
        ) if cadastral_data else 0

        # Count files and get availability from SQLite cache
        total_files = 0
        available_files = 0
        missing_files = 0

        if cadastral_data:
            # Collect all file paths
            all_file_paths = []
            for region_name, region_data in cadastral_data.items():
                for province_code, province_data in region_data.items():
                    for municipality_key, municipality_data in province_data.items():
                        if isinstance(municipality_data, dict):
                            files = municipality_data.get('files', [])
                            total_files += len(files)

                            # Collect S3 keys for cache lookup
                            for file_name in files:
                                s3_key = f"ITALIA/{region_name}/{province_code}/{municipality_key}/{file_name}"
                                all_file_paths.append(s3_key)

            # Get cached file availability status
            try:
                cached_statuses = file_availability_db.get_file_status_batch(all_file_paths, max_age_hours=24)

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
                print(f"Warning: Could not access file availability cache: {cache_error}")
                available_files = 0
                missing_files = 0

        # Render template with cadastral data and flags
        return templates.TemplateResponse("cadastral_data.html", {
            "request": request,
            "cadastral_data": cadastral_data,
            "municipality_flags": municipality_flags,
            "total_regions": total_regions,
            "total_provinces": total_provinces,
            "total_municipalities": total_municipalities,
            "total_files": total_files,
            "available_files": available_files,
            "missing_files": missing_files
        })

    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"Error parsing cadastral structure file: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading cadastral structure: {str(e)}")