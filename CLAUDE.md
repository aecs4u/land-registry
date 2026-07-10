# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Land Registry Viewer for Italian cadastral (land registry) data. Supports file uploads (QPKG/GPKG), direct loading from a structured Italian cadastral database (local or S3), and high-performance visualization of large datasets. Built with FastAPI backend, Folium server-rendered maps, and Leaflet frontend with WebGL acceleration.

## Architecture

### Backend (FastAPI)

- **land_registry/main.py** - Application entry point, route definitions, Folium map generation, Panel/Bokeh integration
- **land_registry/routers/api.py** - All REST API endpoints (file loading, spatial analysis, datashader tiles, FGB, streaming)
- **land_registry/routers/auth.py** - Clerk JWT authentication
- **land_registry/map.py** - Core geospatial processing (QPKG extraction, polygon adjacency, global GeoDataFrame state)
- **land_registry/datashader_service.py** - Server-side tile generation using datashader for massive datasets
- **land_registry/cadastral_db.py** - CadastralDatabase class for querying parcel data
- **land_registry/config.py** - Application settings (S3, local paths, environment detection)
- **land_registry/dashboard.py** - Panel/Bokeh dashboard integration

### Frontend

- **land_registry/templates/base.html** - Base template with all JS/CSS dependencies
- **land_registry/templates/index.html** - Main map page (extends base.html)
- **land_registry/static/map.js** - Client-side map logic, WebGL integration, zone management
- **land_registry/static/folium-interface.js** - Folium iframe map interaction, cadastral selection, progressive loading
- **land_registry/static/webgl-renderer.js** - GPU-accelerated rendering via Leaflet.glify with SVG fallback
- **land_registry/static/progressive-loader.js** - NDJSON stream consumer for incremental layer rendering
- **land_registry/static/table-manager.js** - Tabulator table management
- **land_registry/static/styles.css** - All application styles including dark mode
- **land_registry/static/vendor/glify-browser.js** - Bundled Leaflet.glify (local, not CDN)

### Map Architecture (Important)

The map uses a **Folium iframe** pattern, not a direct Leaflet instance:
1. Server generates Folium HTML → embedded as `<iframe srcdoc="...">`
2. The Leaflet map instance is accessed via `window[mapId]` where `mapId` comes from `.leaflet-container` elements
3. **`window.map` is often null** — use the Folium map pattern (`getFoliumMapInstance()`) when adding layers dynamically
4. `map.js` functions like `addGeoJsonToMap()` only work when a client-side map div exists (not in Folium mode)

## Key Dependencies

- **FastAPI** (>=0.100.0) - Web framework
- **geopandas** - Geospatial data processing
- **folium** - Server-side map generation (rendered as iframe)
- **datashader** (>=0.16.0) - GPU/CPU-accelerated tile generation for large datasets
- **colorcet** (>=3.0.1) - Professional color palettes for datashader
- **panel** / **bokeh** - Dashboard tables
- **Leaflet.js** - Frontend mapping (loaded in Folium iframe)
- **Leaflet.glify** - WebGL polygon rendering (bundled locally)

## Development Commands

```bash
# Install dependencies
uv sync

# Run the development server (recommended - fast shutdown)
python run_dev.py

# Run tests
uv run pytest

# Run tests with coverage
uv run pytest --cov=land_registry --cov-report=html

# Format code
uv run black .

# Sort imports
uv run isort .
```

The dev server runs on **port 8000**. Panel/Bokeh dashboard runs on **port 5006** (embedded).

## API Endpoints

### File Upload & Processing
- `GET /` - Landing page
- `GET /map` - Main map application
- `POST /api/v1/upload-qpkg/` - Upload and process QPKG/GPKG files
- `POST /api/v1/generate-map/` - Generate static Folium map from uploads

### Cadastral Data Loading
- `GET /api/v1/get-cadastral-structure/` - Load Italian cadastral hierarchy (cached)
- `POST /api/v1/load-cadastral-files/` - Load multiple cadastral files (parallel, returns all at once)
- `POST /api/v1/load-cadastral-files-stream/` - **Streaming** loader (NDJSON, progressive rendering)

### Spatial Analysis
- `POST /api/v1/get-adjacent-polygons/` - Find adjacent polygons using spatial predicates
- `GET /api/v1/get-attributes/` - Retrieve feature attributes

### High-Performance Visualization
- `GET /api/v1/tiles/datashader/{z}/{x}/{y}.png` - Datashader rasterized map tiles (Leaflet TileLayer compatible)
- `GET /api/v1/datashader/heatmap/{region}` - Full-region density heatmap
- `GET /api/v1/datashader/categorical/{region}` - Categorical map by field

### FlatGeobuf
- `GET /api/v1/fgb/regions` - List available FGB regions

## Data Flow

### Classic Loading (page reload)
1. User selects files in sidebar → `loadCadastralSelection()` → POST `/api/v1/load-cadastral-files`
2. Backend loads files in parallel (ThreadPoolExecutor, max 8 workers) → stores in global state
3. Page reloads → server embeds GeoJSON in template → `loadGeoJsonData()` renders via WebGLRenderer

### Progressive Loading (no reload)
1. User selects files → `_loadCadastralProgressive()` → POST `/api/v1/load-cadastral-files-stream/`
2. Backend streams NDJSON events: `start` → `progress` → `layer` (with GeoJSON) → `complete`
3. Frontend renders each layer on the Folium map as it arrives, shows progress overlay

### Rendering Pipeline
- **<1000 features**: SVG rendering (L.geoJSON with stripe patterns)
- **1000-50K features**: WebGL rendering (Leaflet.glify GPU acceleration)
- **100K+ features**: Datashader server-side tiles (rasterized density/categorical maps)

## Important Technical Notes

- **Global State**: Uses global `current_gdf` and `current_layers` to store active data across requests
- **Dual Map Architecture**: Folium iframe vs client-side Leaflet — see Map Architecture section above
- **Local Cadastral Data**: `/data/catasto/ITALIA/` (auto-detected in development)
- **S3 Support**: Production mode uses unsigned S3 client for cadastral data
- **WebGL Bundling**: Leaflet.glify is bundled locally (`static/vendor/`) — CDN was blocked by ORB

## Deployment

**All Cloud Run deployments MUST use `lighthouse cloudrun deploy`. Never use `gcloud run deploy` directly.**

```bash
lighthouse cloudrun deploy           # deploy to production
lighthouse cloudrun deploy --follow  # deploy and stream logs
```
- **Panel Integration**: Bokeh dashboard embedded via `server_document()` (run in thread pool to avoid blocking)
