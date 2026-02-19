# Land Registry Viewer

A web application for visualizing Italian cadastral (land registry) data with interactive mapping capabilities.

## Features

- **File Upload Support**: Upload and process QPKG (QGIS project packages) or GPKG files
- **Interactive Mapping**: Leaflet-based map interface with drawing tools
- **Spatial Analysis**: Find adjacent polygons using spatial relationships
- **Cadastral Data Integration**: Direct loading from structured Italian cadastral database with S3 support
- **Drawing Tools**: Create and save new polygons and circles as GeoJSON
- **Multiple Map Layers**: Support for terrain, satellite, and other map types
- **Interactive Dashboards**: Panel/Bokeh-based data visualization for table views
- **Cache Monitoring**: Real-time cache statistics and health monitoring via API endpoints
- **Environment-Aware Configuration**: Automatic detection of development vs production environments

## Quick Start

### Prerequisites

- Python 3.8+
- uv package manager

### Installation

```bash
# Install dependencies
uv sync
```

### Configuration

Copy the example environment file and customize as needed:

```bash
cp .env.example .env
```

Key configuration options:
- **ENVIRONMENT**: Set to `development` or `production`
- **CADASTRAL_USE_LOCAL_FILES**: Use local files (true) or S3 (false)
- **PANEL_PANEL_PORT**: Panel server port (default: 5006)
- **S3_BUCKET_NAME**: S3 bucket for cadastral data (production)

See [.env.example](.env.example) for all available options.

### Running the Application

```bash
# Start the development server (fast shutdown)
python run_dev.py

# Or use uvicorn directly
uvicorn land_registry.main:app --reload --host 0.0.0.0 --port 8000
```

Access the application at `http://localhost:8000`

The Panel server will automatically start on `http://localhost:5006` for interactive dashboards.

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

### Main Application
- `GET /` - Main application interface with interactive Leaflet map
- `GET /map_table` - Tabulator-based table view (embedded Panel dashboard)
- `GET /adjacency_table` - Adjacency analysis table view (planned)
- `GET /mapping_table` - Mapping table view (planned)

### Data Processing
- `POST /upload-qpkg/` - Process uploaded geospatial files
- `POST /get-adjacent-polygons/` - Spatial adjacency analysis
- `POST /load-cadastral-files/` - Load Italian cadastral data
- `POST /save-drawn-polygons/` - Save user-drawn features

### API v1 Endpoints
- `GET /api/v1/table-data` - Paginated table data with filtering and sorting
- `GET /api/v1/cadastral-cache-info` - Cache metadata, statistics, and file availability
- `GET /api/v1/adjacency-data` - Adjacency analysis data (503 - not implemented)
- `GET /api/v1/mapping-data` - Mapping data (503 - not implemented)

### Health & Monitoring
- `GET /health` - Application health check

For detailed API documentation, visit `/docs` (Swagger UI) or `/redoc` (ReDoc) when the server is running.

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

### Zone Workflow Architecture and Plan
- See `docs/ZONE_WORKFLOW_ARCHITECTURE_AND_PLAN.md` for:
  - Target zone/microzone architecture
  - User workflows and API contracts
  - Issue-by-issue development plan and current status

### Backend Architecture
- **Web Framework**: FastAPI with async/await support
- **Geospatial Processing**: GeoPandas, Shapely for spatial operations
- **Data Visualization**: Panel/Bokeh for interactive dashboards
- **Storage**: S3 integration for cadastral data (production), local files (development)
- **Caching**: In-memory caching with TTL for cadastral structure (5-minute default)
- **Configuration**: Pydantic Settings for environment-aware configuration

### Frontend Technologies
- **Mapping**: Leaflet.js with extensive plugin ecosystem
  - Leaflet Draw for polygon/circle creation
  - Leaflet Measure for distance/area measurement
  - Leaflet Control Geocoder for address search
  - Marker clustering and minimap support
- **Tables**: Tabulator.js for interactive data tables (via Panel)
- **Authentication**: Clerk integration (optional)

### Data Formats
- Supports QPKG, GPKG, Shapefile, GeoJSON, KML
- Cadastral data stored in hierarchical JSON structure
- File availability tracking via SQLite database

### Recent Improvements (Phase 2-5 Refactoring)

**Phase 2: Cadastral Data Utilities**
- Centralized cadastral data loading with source tracking
- Cache metadata API (`/api/v1/cadastral-cache-info`)
- File availability statistics across municipalities

**Phase 3: Panel Server Lifecycle**
- Async lifecycle management with proper health checks
- Configurable retry logic and timeout settings
- Clean shutdown and immediate failure detection

**Phase 4: Panel Endpoints Alignment**
- Unified Panel dashboard routes
- Environment-based configuration for all Panel settings

**Phase 5: API Response Models**
- Pydantic models for type-safe API responses
- Auto-generated OpenAPI/Swagger documentation
- Proper 503 responses for unimplemented features

## Project Structure

```
land_registry/
├── main.py                        # Main FastAPI application with Panel integration
├── settings.py                    # Pydantic settings for configuration management
├── models.py                      # Pydantic models for API responses
├── cadastral_utils.py             # Cadastral data loading and caching
├── s3_storage.py                  # S3 integration for cadastral files
├── dashboard.py                   # Panel dashboard application
├── shared_state.py                # Shared state between FastAPI and Panel
├── routers/
│   └── api.py                     # API v1 endpoints
├── templates/
│   ├── base.html                  # Base template with Leaflet libraries
│   ├── index.html                 # Landing page with map
│   └── tabulator.html             # Table view template
└── static/
    ├── map.js                     # Leaflet map initialization
    ├── table-manager.js           # Tabulator table management
    ├── folium-interface.js        # Folium bridge utilities
    └── styles.css                 # Application styles

data/
├── cadastral_structure.json       # Italian administrative boundaries
└── drawn_polygons/                # User-created features

scripts/
├── run_tests.py                   # Test runner utilities
└── validate_tests.py              # Test validation
```

## Environment Variables

All environment variables are documented in [.env.example](.env.example). Key categories:

- **Application Settings**: `LAND_REGISTRY_*`
- **S3 Storage**: `S3_*`
- **Database**: `DB_*`
- **Panel Server**: `PANEL_*`
- **Cadastral Data**: `CADASTRAL_*`
- **Map Controls**: `MAP_CONTROLS_*`
- **Authentication**: `CLERK_*`

## Monitoring & Operations

### Cache Monitoring

Check cache health and statistics:

```bash
curl http://localhost:8000/api/v1/cadastral-cache-info
```

Response includes:
- Cache age and TTL status
- Data source (local/S3/JSON)
- Regional/provincial/municipal statistics
- File availability coverage percentage

### Health Checks

```bash
curl http://localhost:8000/health
```

### Logs

The application uses structured logging with different levels:
- **INFO**: Startup, shutdown, major operations
- **DEBUG**: Detailed execution flow (set `LAND_REGISTRY_DEBUG=true`)
- **WARNING**: Non-critical issues (S3 fallback, missing files)
- **ERROR**: Critical failures with stack traces
