# Land Registry Viewer

A web application for visualizing Italian cadastral (land registry) data with interactive mapping capabilities.

## Features

- **File Upload Support**: Upload and process QPKG (QGIS project packages) or GPKG files
- **Interactive Mapping**: Leaflet-based map interface with drawing tools
- **Spatial Analysis**: Find adjacent polygons using spatial relationships
- **Cadastral Data Integration**: Direct loading from structured Italian cadastral database
- **Drawing Tools**: Create and save new polygons and circles as GeoJSON
- **Multiple Map Layers**: Support for terrain, satellite, and other map types

## Quick Start

### Prerequisites

- Python 3.8+
- uv package manager

### Installation

```bash
# Install dependencies
uv sync
```

### Running the Application

```bash
# Start the development server
uvicorn app.land_registry_app:app --reload --host 0.0.0.0 --port 8000
```

Access the application at `http://localhost:8000`

## Usage

### Upload Files
1. Navigate to the main interface
2. Upload QPKG or GPKG files containing geospatial data
3. View the extracted data on the interactive map

### Spatial Analysis
1. Select a polygon on the map
2. Use "Get Adjacent Polygons" to find neighboring features
3. View results with different styling for selected and adjacent polygons

### Drawing Tools
1. Use the drawing controls to create new polygons or circles
2. Save drawn features as GeoJSON files
3. Import saved drawings as new map layers

### Cadastral Data
1. Browse the Italian cadastral structure (Regione > Provincia > Comune)
2. Load specific cadastral files (MAP/PLE types)
3. Combine multiple files for comprehensive analysis

## API Endpoints

- `GET /` - Main application interface
- `POST /upload-qpkg/` - Process uploaded geospatial files
- `POST /get-adjacent-polygons/` - Spatial adjacency analysis
- `POST /load-cadastral-files/` - Load Italian cadastral data
- `POST /save-drawn-polygons/` - Save user-drawn features

## Development

```bash
# Run tests
uv run pytest

# Format code
uv run black .

# Sort imports
uv run isort .

# Lint code
uv run flake8
```

## Technical Details

- **Backend**: FastAPI with geopandas for geospatial processing
- **Frontend**: Leaflet.js with Leaflet Draw for interactive mapping
- **Data Formats**: Supports QPKG, GPKG, Shapefile, GeoJSON, KML
- **Spatial Operations**: Uses shapely for geometric analysis

## Data Structure

```
data/
├── cadastral_structure.json    # Italian administrative boundaries
└── drawn_polygons/            # User-created features

app/
├── land_registry_app.py       # Main FastAPI application
├── map.py                     # Geospatial processing utilities
└── templates/map.html         # Web interface
```
