# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Land Registry Viewer application that allows users to visualize Italian cadastral (land registry) data. The application supports both file uploads (QPKG/GPKG) and direct loading of cadastral files from a structured Italian cadastral database. Built with FastAPI backend and Leaflet frontend with drawing capabilities.

## Architecture

The project structure follows a FastAPI web application pattern:

- **app/land_registry_app.py** - Main FastAPI application with REST endpoints
- **app/map.py** - Core geospatial data processing (QPKG extraction, polygon adjacency analysis)
- **app/templates/map.html** - Interactive web interface with Leaflet maps and drawing tools
- **app/static/styles.css** - Application styling
- **land_registry/generate_cadastral_form.py** - Utility to analyze and generate HTML forms for Italian cadastral data structure
- **data/** - Contains JSON files with cadastral structure and drawn polygon data

## Key Dependencies

- **FastAPI** (>=0.100.0) - Web framework and API endpoints
- **geopandas** - Geospatial data processing and format conversion
- **folium** - Alternative map generation (used in generate-map endpoint)
- **SQLModel** (>=0.0.8) - Database modeling (appears unused in current implementation)
- **Leaflet.js** - Frontend interactive mapping with drawing support
- **Leaflet Draw** - Drawing tools for creating polygons and circles

## Development Commands

The project uses pyproject.toml with uv as the package manager:

```bash
# Install dependencies
uv sync

# Run the development server
uvicorn app.land_registry_app:app --reload --host 0.0.0.0 --port 8000

# Run tests
uv run pytest

# Run tests with coverage
uv run pytest --cov=land_registry --cov-report=html

# Format code
uv run black .

# Sort imports
uv run isort .

# Lint code
uv run flake8
```

## API Endpoints

### File Upload & Processing
- `GET /` - Serve main map application interface
- `POST /upload-qpkg/` - Upload and process QPKG/GPKG files, extract geospatial data as GeoJSON
- `POST /generate-map/` - Generate static folium map HTML from QPKG/GPKG files

### Spatial Analysis
- `POST /get-adjacent-polygons/` - Find polygons adjacent to a selected polygon using spatial relationships (touches/intersects/overlaps)
- `GET /get-attributes/` - Retrieve all feature attributes from loaded geospatial data

### Cadastral Data Management
- `GET /get-cadastral-structure/` - Load Italian cadastral data structure from JSON
- `POST /load-cadastral-files/` - Load multiple cadastral files from structured Italian cadastral database
- `POST /save-drawn-polygons/` - Save user-drawn polygons as GeoJSON files

## Data Flow & Core Features

### File Processing
1. User uploads QPKG (QGIS project packages) or GPKG files via web interface
2. Backend extracts ZIP contents and searches for geospatial files (.shp, .geojson, .gpkg, .kml)
3. Uses geopandas to read geospatial data and convert to GeoJSON
4. Frontend receives GeoJSON and renders on interactive Leaflet map

### Spatial Analysis
1. User selects a polygon on the map
2. Backend analyzes spatial relationships using shapely geometry operations
3. Finds adjacent polygons based on touch/intersect/overlap methods
4. Returns selected polygon and adjacent polygons as separate GeoJSON layers

### Drawing & Data Creation
1. Users can draw new polygons/circles using Leaflet Draw tools
2. Drawn features are saved as GeoJSON files with timestamps
3. Features can be imported back as new layers for analysis

### Cadastral Database Integration
1. Application reads structured Italian cadastral data (Regione > Provincia > Comune hierarchy)
2. Users can select specific geographic areas and file types (MAP/PLE)
3. Multiple cadastral files are loaded and combined for analysis

## Important Technical Notes

- **Global State**: Uses global `current_gdf` variable to store active GeoDataFrame across requests
- **Temporary Files**: QPKG/GPKG uploads create temporary files that are automatically cleaned up
- **Spatial Indexing**: Adjacency analysis relies on shapely spatial predicates (touches, intersects, overlaps)
- **Italian Cadastral Structure**: Hardcoded path to cadastral database at `/media/emanuele/ddbb5477-3ef2-4097-b731-3784cb7767c1/catasto/ITALIA`
- **Drawing Storage**: User-drawn polygons saved to `drawn_polygons/` directory with timestamps