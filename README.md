# Land Registry Viewer

A web application for visualizing Italian cadastral (land registry) data with interactive mapping capabilities.

## Architecture / scope

This is the **map visualization** layer of the AECS4U real-estate data
pipeline — it consumes `aecs4u-stats`'s query/enrichment layer
(`land_registry/stats_service.py`) and never downloads or scrapes data
itself. See
[`../__architecture__/README.md`](../__architecture__/README.md#detailed-real-estate-repository-boundaries)
for the full pipeline and ownership boundaries — it supersedes the P4 rule in
`docs/ZORNADE_GAP_ANALYSIS.md` (downloaders no longer live in `aecs4u-stats`;
see that pipeline doc §3).

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
- **ENVIRONMENT**: Set to `development` or `production` (auto-detects if unset)
- **CADASTRAL_USE_LOCAL_FILES**: Use local files (true) or S3 (false)
- **PANEL_PANEL_PORT**: Panel server port (default: 5006)
- **STORAGE_S3_BUCKET**: S3 bucket for cadastral data (production)

See [.env.example](.env.example) for all available options.

### Running the Application

```bash
# Start the development server (fast shutdown)
python run_dev.py

# Or use uvicorn directly
uvicorn land_registry.main:app --reload --host 0.0.0.0 --port 8000

# Or use the unified CLI
land-registry serve --target main --host 0.0.0.0 --port 8000 --reload
```

Access the application at `http://localhost:8000`

The Panel server will automatically start on `http://localhost:5006` for interactive dashboards.

### CLI

The project includes a unified CLI entrypoint:

```bash
land-registry --help
```

Full reference: [docs/CLI.md](docs/CLI.md)

Useful commands:

```bash
# Run app targets
land-registry serve --target main --reload
land-registry serve --target main --port 8001

# Inspect effective configuration and routes
land-registry info
land-registry routes --target main

# Cadastral/cache operations
land-registry cadastral-stats --json
land-registry cache show
land-registry cache clear --all
```

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

The data were extracted from [servizio cartografico dell'Agenzia delle Entrate](https://www.agenziaentrate.gov.it/portale/accedi-al-servizio-cartografici).

## API Endpoints

### Pages
- `GET /` - Redirects to `/map`
- `GET /map` - Main map application (Folium iframe + sidebar + Panel dashboards)
- `GET /landing` - Feature summary landing page
- `GET /cadastral-data` - Browse Italian cadastral data hierarchy
- `GET /map_table` - Standalone tabulator table view (Panel-embedded)
- `GET /adjacency_table` - Adjacency analysis table (Panel-embedded)
- `GET /mapping_table` - Mapping/drawing table (Panel-embedded)

### File Upload & Processing
- `POST /api/v1/upload-qpkg/` - Upload and process QPKG/GPKG files
- `POST /api/v1/generate-map/` - Generate static Folium map from uploads

### Cadastral Data
- `GET /api/v1/get-cadastral-structure/` - Load Italian cadastral hierarchy (cached)
- `POST /api/v1/load-cadastral-files/` - Load multiple cadastral files (returns all at once)
- `POST /api/v1/load-cadastral-files-stream/` - Streaming loader (NDJSON, progressive rendering)
- `GET /api/v1/cadastral-cache-info` - Cache metadata, statistics, and file availability

### Spatial Analysis
- `POST /api/v1/get-adjacent-polygons/` - Find adjacent polygons
- `GET /api/v1/get-attributes/` - Retrieve feature attributes

### Table Data
- `GET /api/v1/table-data` - Paginated table data with filtering and sorting
- `GET /api/v1/adjacency-data` - Adjacency analysis data (503 — not yet implemented)
- `GET /api/v1/mapping-data` - Mapping/drawing data (503 — not yet implemented)

### High-Performance Visualization
- `GET /api/v1/tiles/datashader/{z}/{x}/{y}.png` - Datashader rasterized tiles
- `GET /api/v1/datashader/heatmap/{region}` - Full-region density heatmap

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
├── main.py                        # FastAPI app, Panel lifecycle, main page endpoints
├── config.py                      # Pydantic settings for all configuration
├── models.py                      # Pydantic models for API responses
├── cadastral_utils.py             # Cadastral data loading and TTL caching
├── cadastral_db.py                # CadastralDatabase class (query parcels)
├── map.py                         # Geospatial processing, global GDF state
├── map_controls.py                # Python-defined map control buttons/selects
├── datashader_service.py          # Server-side tile generation (large datasets)
├── dashboard.py                   # Panel/Bokeh dashboard (Tabulator table)
├── shared_state.py                # SharedState: FastAPI ↔ Panel data bridge
├── file_availability_db.py        # SQLite cache for S3 file availability
├── s3_storage.py                  # S3 client for cadastral files (production)
├── gcs_storage.py                 # GCS client (aecs4u-storage integration)
├── sqlite_db.py                   # SQLite database for zones and microzones
├── core/
│   └── clerk.py                   # Clerk JWT auth (optional)
├── routers/
│   ├── api.py                     # All /api/v1/* endpoints
│   ├── auth.py                    # Clerk JWT authentication endpoints
│   └── auth_pages.py              # HTML auth pages (login/register)
├── templates/
│   ├── base.html                  # Base template with JS/CSS dependencies
│   ├── index.html                 # Main map shell (extends base.html)
│   ├── landing.html               # Feature landing page
│   ├── cadastral_data.html        # Cadastral data browser
│   └── tabulator.html             # Standalone Panel table view
└── static/
    ├── map.js                     # Client-side map logic, Canvas fallback, zone management
    ├── folium-interface.js        # Folium iframe interaction, progressive loading
    ├── webgl-renderer.js          # Optional legacy GPU renderer
    ├── progressive-loader.js      # NDJSON stream consumer
    ├── table-manager.js           # Tabulator table management
    ├── styles.css                 # All styles including dark mode
    └── vendor/
        └── glify-browser.js       # Bundled legacy renderer dependency

data/
├── cadastral_structure.json       # Italian administrative boundaries (optional)
└── catasto/ITALIA/                # Local cadastral GPKG files (development)

tests/
├── conftest.py                    # Shared fixtures (TestClient, sample data)
├── test_cadastral_utils.py        # CadastralData, caching, source loading
├── test_main_endpoints.py         # Main page endpoints, table-data API
├── test_api_endpoints.py          # API v1 endpoint tests
├── test_config.py                 # Configuration settings tests
└── archive/                       # Older test files (kept for reference)
```

## Panel Server Configuration

The Panel/Bokeh dashboard provides interactive Tabulator tables. It runs as a separate HTTP server (default port 5006) alongside the main FastAPI app.

### How it works

1. FastAPI starts a daemon thread running `pn.serve()` during app startup
2. The lifespan context retries a health check (up to 10 seconds by default) to confirm Panel is ready
3. The main app embeds Panel documents via `bokeh.embed.server_document()` calls
4. On hot-reload, if Panel's port is already in use, the existing server is reused

### Key settings (`PANEL_*` prefix)

| Variable | Default | Description |
|---|---|---|
| `PANEL_PANEL_HOST` | `127.0.0.1` | Panel server bind address |
| `PANEL_PANEL_PORT` | `5006` | Panel server port |
| `PANEL_PANEL_STARTUP_TIMEOUT` | `10` | Seconds to wait for Panel to start |
| `PANEL_PANEL_STARTUP_RETRY_DELAY` | `0.5` | Seconds between health check retries |
| `PANEL_PANEL_HEALTH_CHECK_TIMEOUT` | `5.0` | HTTP timeout for each health check |
| `PANEL_PANEL_SHOW` | `false` | Open Panel in browser automatically |

### Troubleshooting Panel

**Panel tables show blank / connection refused**
- Check that port 5006 is not blocked by a firewall or already used by another process
- Look for `Panel server health check passed` in the startup logs
- Increase `PANEL_PANEL_STARTUP_TIMEOUT` if Panel is slow to start on your machine

**`Address already in use` on startup**
- Normal during hot-reload — the existing Panel server is reused automatically
- If the app is stuck, kill the process holding port 5006: `fuser -k 5006/tcp`

**Panel works but tables are empty**
- The Panel server and FastAPI share state via `SharedState` in `shared_state.py`
- Data is pushed to Panel after each cadastral file load via the API endpoints

## Environment Variables

All environment variables are documented in [.env.example](.env.example). Key categories:

- **Application Settings**: `LAND_REGISTRY_*`
- **Storage**: `STORAGE_*` (unified), `S3_*` (legacy)
- **Database**: `DB_*`
- **Panel Server**: `PANEL_*`
- **Cadastral Data**: `CADASTRAL_*`
- **Map Controls**: `MAP_CONTROLS_*`
- **Authentication**: `CLERK_*`, `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`

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
