from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import folium
from folium import plugins
import geopandas as gpd
import pandas as pd
import json
import os
from pathlib import Path
from pydantic import BaseModel
import tempfile
from typing import Dict, Any, List, Optional

from land_registry.map import extract_qpkg_data, find_adjacent_polygons, get_current_gdf
from land_registry.map_controls import map_controls, ControlButton, ControlSelect
from land_registry.s3_storage import get_s3_storage, S3Settings, configure_s3_storage


class PolygonSelection(BaseModel):
    feature_id: int
    geometry: Dict[str, Any]
    touch_method: str = "touches"


app = FastAPI()

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
    """Serve the main map application with direct Leaflet implementation and Python sidebar"""
    # Generate Python-based controls HTML and JavaScript
    controls_html = map_controls.generate_html()
    controls_js = map_controls.generate_javascript()

    # Get current data status
    current_gdf = get_current_gdf()
    has_data = current_gdf is not None and not current_gdf.empty

    # Convert current data to GeoJSON if available
    geojson_data = None
    if current_gdf is not None and not current_gdf.empty:
        geojson_data = json.loads(current_gdf.to_json())

    # Create complete HTML with direct Leaflet implementation and Python sidebar
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Land Registry Viewer - Direct Leaflet with All Map Providers</title>
        <link rel="stylesheet" href="/static/styles.css">
        <!-- Leaflet CSS -->
        <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
        <!-- Leaflet Draw CSS -->
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet.draw/1.0.4/leaflet.draw.css" />
        <!-- Leaflet Fullscreen CSS -->
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet.fullscreen/2.4.0/Control.FullScreen.css" />
        <!-- Leaflet Measure CSS -->
        <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/leaflet-measure@3.1.0/dist/leaflet-measure.css" />
        <style>
            html, body {{
                height: 100%;
                margin: 0;
                padding: 0;
                font-family: Arial, sans-serif;
            }}

            /* Upload overlay styling */
            .upload-overlay {{
                position: fixed;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                background: rgba(0,0,0,0.7);
                display: none;
                z-index: 10000;
                backdrop-filter: blur(3px);
            }}

            .upload-box {{
                position: absolute;
                top: 50%;
                left: 50%;
                transform: translate(-50%, -50%);
                background: white;
                padding: 30px;
                border-radius: 12px;
                text-align: center;
                box-shadow: 0 10px 30px rgba(0,0,0,0.3);
                max-width: 400px;
                width: 90%;
            }}

            .upload-box h3 {{
                margin: 0 0 20px 0;
                color: #333;
                font-size: 20px;
            }}

            .upload-box input[type="file"] {{
                margin: 10px 0;
                padding: 8px;
                border: 2px dashed #007cba;
                border-radius: 6px;
                width: 100%;
                box-sizing: border-box;
            }}

            .upload-box button {{
                margin: 5px;
                padding: 10px 20px;
                border: none;
                border-radius: 6px;
                cursor: pointer;
                font-weight: bold;
                transition: background-color 0.3s;
            }}

            .upload-box button:first-of-type {{
                background: #007cba;
                color: white;
            }}

            .upload-box button:first-of-type:hover {{
                background: #005a8b;
            }}

            .upload-box button:last-of-type {{
                background: #f0f0f0;
                color: #333;
            }}

            .upload-box button:last-of-type:hover {{
                background: #e0e0e0;
            }}

            /* Map container */
            #map {{
                position: absolute;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                z-index: 1;
            }}

            /* Python controls sidebar */
            #controls-sidebar {{
                position: fixed;
                top: 20px;
                left: 20px;
                z-index: 1000;
                background: white;
                border-radius: 8px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                max-width: 280px;
                max-height: calc(100vh - 40px);
                overflow-y: auto;
            }}
        </style>
    </head>
    <body>
        <!-- Direct Leaflet map -->
        <div id="map"></div>

        <!-- Python-generated controls sidebar -->
        <div id="controls-sidebar">
            {controls_html}
        </div>

        <!-- File upload overlay (hidden by default, can be shown if needed) -->
        <div id="file-upload-overlay" class="upload-overlay" style="display: none;">
            <div class="upload-box">
                <h3>🗺️ Upload Land Registry Data</h3>
                <p>Upload QPKG or GPKG files to visualize cadastral data</p>
                <input type="file" id="file-input" accept=".qpkg,.gpkg">
                <div>
                    <button onclick="uploadFile()">📁 Upload File</button>
                    <button onclick="window.closeUpload && window.closeUpload()">❌ Cancel</button>
                </div>
            </div>
        </div>

        <!-- Leaflet JS -->
        <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
        <!-- Leaflet Draw JS -->
        <script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet.draw/1.0.4/leaflet.draw.js"></script>
        <!-- Leaflet Fullscreen JS -->
        <script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet.fullscreen/2.4.0/Control.FullScreen.js"></script>
        <!-- Leaflet Measure JS -->
        <script src="https://cdn.jsdelivr.net/npm/leaflet-measure@3.1.0/dist/leaflet-measure.js"></script>

        <script>
            // Initialize Leaflet map with all map providers
            let map, currentGeoJsonLayer, drawnItems, drawControl;

            // Initialize the map
            function initializeMap() {{
                // Create map centered on Italy
                map = L.map('map').setView([41.8719, 12.5674], 6);

                // Define all map providers
                const mapProviders = {{
                    'OpenStreetMap': L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
                        attribution: '© OpenStreetMap contributors'
                    }}),
                    '📍 Google Maps': L.tileLayer('https://mt1.google.com/vt/lyrs=m&x={{x}}&y={{y}}&z={{z}}', {{
                        attribution: '© Google'
                    }}),
                    '🛰️ Google Satellite': L.tileLayer('https://mt1.google.com/vt/lyrs=s&x={{x}}&y={{y}}&z={{z}}', {{
                        attribution: '© Google'
                    }}),
                    '⛰️ Google Terrain': L.tileLayer('https://mt1.google.com/vt/lyrs=p&x={{x}}&y={{y}}&z={{z}}', {{
                        attribution: '© Google'
                    }}),
                    '🌍 Google Hybrid': L.tileLayer('https://mt1.google.com/vt/lyrs=y&x={{x}}&y={{y}}&z={{z}}', {{
                        attribution: '© Google'
                    }}),
                    '🌐 ESRI World Imagery': L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}', {{
                        attribution: '© ESRI'
                    }}),
                    '🏔️ ESRI Terrain': L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Terrain_Base/MapServer/tile/{{z}}/{{y}}/{{x}}', {{
                        attribution: '© ESRI'
                    }}),
                    '⚪ CartoDB Light': L.tileLayer('https://cartodb-basemaps-{{s}}.global.ssl.fastly.net/light_all/{{z}}/{{x}}/{{y}}.png', {{
                        attribution: '© CartoDB'
                    }}),
                    '⚫ CartoDB Dark': L.tileLayer('https://cartodb-basemaps-{{s}}.global.ssl.fastly.net/dark_all/{{z}}/{{x}}/{{y}}.png', {{
                        attribution: '© CartoDB'
                    }})
                }};

                // Weather overlays
                const weatherOverlays = {{
                    '🌡️ Temperature': L.tileLayer('https://tile.openweathermap.org/map/temp_new/{{z}}/{{x}}/{{y}}.png?appid=b6907d289e10d714a6e88b30761fae22', {{
                        attribution: '© OpenWeatherMap'
                    }}),
                    '🌧️ Precipitation': L.tileLayer('https://tile.openweathermap.org/map/precipitation_new/{{z}}/{{x}}/{{y}}.png?appid=b6907d289e10d714a6e88b30761fae22', {{
                        attribution: '© OpenWeatherMap'
                    }}),
                    '💨 Wind Speed': L.tileLayer('https://tile.openweathermap.org/map/wind_new/{{z}}/{{x}}/{{y}}.png?appid=b6907d289e10d714a6e88b30761fae22', {{
                        attribution: '© OpenWeatherMap'
                    }}),
                    '☁️ Cloud Coverage': L.tileLayer('https://tile.openweathermap.org/map/clouds_new/{{z}}/{{x}}/{{y}}.png?appid=b6907d289e10d714a6e88b30761fae22', {{
                        attribution: '© OpenWeatherMap'
                    }})
                }};

                // Add default layer
                mapProviders['OpenStreetMap'].addTo(map);

                // Add layer control
                L.control.layers(mapProviders, weatherOverlays, {{
                    position: 'topright',
                    collapsed: true
                }}).addTo(map);

                // Add drawing controls
                drawnItems = new L.FeatureGroup();
                map.addLayer(drawnItems);

                drawControl = new L.Control.Draw({{
                    position: 'topleft',
                    draw: {{
                        polygon: true,
                        circle: true,
                        rectangle: true,
                        polyline: true,
                        marker: true
                    }},
                    edit: {{
                        featureGroup: drawnItems,
                        remove: true
                    }}
                }});
                map.addControl(drawControl);

                // Add fullscreen control
                map.addControl(new L.Control.Fullscreen({{
                    position: 'topright'
                }}));

                // Add measure control
                L.control.measure({{
                    position: 'topright',
                    primaryLengthUnit: 'meters',
                    secondaryLengthUnit: 'kilometers',
                    primaryAreaUnit: 'sqmeters',
                    secondaryAreaUnit: 'acres'
                }}).addTo(map);

                // Load GeoJSON data if available
                const geojsonData = {json.dumps(geojson_data) if geojson_data else 'null'};
                if (geojsonData) {{
                    currentGeoJsonLayer = L.geoJSON(geojsonData, {{
                        style: function(feature) {{
                            return {{
                                fillColor: '#0078ff',
                                color: '#0078ff',
                                weight: 2,
                                fillOpacity: 0.3,
                                opacity: 0.8
                            }};
                        }}
                    }}).addTo(map);

                    // Fit map to GeoJSON bounds
                    map.fitBounds(currentGeoJsonLayer.getBounds());
                }}

                // Add drawing event handlers
                map.on('draw:created', function(e) {{
                    drawnItems.addLayer(e.layer);
                    console.log('Shape created');
                }});

                map.on('draw:drawstart', function(e) {{
                    document.getElementById('startDrawing').disabled = true;
                    document.getElementById('stopDrawing').disabled = false;
                }});

                map.on('draw:drawstop', function(e) {{
                    document.getElementById('startDrawing').disabled = false;
                    document.getElementById('stopDrawing').disabled = true;
                }});

                // Initialize Python controls integration
                initializePythonControlsIntegration();
            }}

            // Map control functions for Python controls integration
            function initializePythonControlsIntegration() {{
                window.map = map; // Make map globally available

                window.zoomIn = function() {{
                    map.zoomIn();
                }};

                window.zoomOut = function() {{
                    map.zoomOut();
                }};

                window.fitToPolygons = function() {{
                    if (currentGeoJsonLayer) {{
                        map.fitBounds(currentGeoJsonLayer.getBounds());
                    }}
                }};

                window.togglePolygonSelectionMode = function() {{
                    console.log('Toggle polygon selection mode');
                }};

            window.startDrawingMode = function() {{
                if (drawControl && map) {{
                    // Enable drawing mode by showing polygon drawing option
                    const polygonButton = document.querySelector('.leaflet-draw-draw-polygon');
                    if (polygonButton) {{
                        polygonButton.click();
                    }}
                    document.getElementById('startDrawing').disabled = true;
                    document.getElementById('stopDrawing').disabled = false;
                }}
            }};

            window.stopDrawingMode = function() {{
                if (map) {{
                    // Cancel any active drawing
                    map.fire('draw:canceled');
                    document.getElementById('startDrawing').disabled = false;
                    document.getElementById('stopDrawing').disabled = true;
                }}
            }};

            window.clearAllDrawings = function() {{
                if (drawnItems) {{
                    drawnItems.clearLayers();
                    console.log('All drawings cleared');
                }}
            }};

            window.toggleLegend = function() {{
                const legend = document.querySelector('.leaflet-control-layers');
                if (legend) {{
                    legend.style.display = legend.style.display === 'none' ? 'block' : 'none';
                }}
            }};

            window.toggleSelectionInfo = function() {{
                // Toggle selection info panel
                console.log('Toggle selection info - feature to be implemented');
            }};

            window.togglePolygonsVisibility = function() {{
                if (currentGeoJsonLayer) {{
                    if (map.hasLayer(currentGeoJsonLayer)) {{
                        map.removeLayer(currentGeoJsonLayer);
                    }} else {{
                        map.addLayer(currentGeoJsonLayer);
                    }}
                }}
                // Also toggle drawn items
                if (drawnItems) {{
                    if (map.hasLayer(drawnItems)) {{
                        map.removeLayer(drawnItems);
                    }} else {{
                        map.addLayer(drawnItems);
                    }}
                }}
            }};

            window.toggleBasemapVisibility = function() {{
                // This function switches between basemap layers
                const layerControl = document.querySelector('.leaflet-control-layers');
                if (layerControl && !layerControl.classList.contains('leaflet-control-layers-expanded')) {{
                    layerControl.click(); // Open layer control for user to select
                }}
            }};

            window.saveDrawingsToJSON = function() {{
                if (drawnItems && drawnItems.getLayers().length > 0) {{
                    const drawnData = {{
                        type: "FeatureCollection",
                        features: []
                    }};

                    drawnItems.eachLayer(function(layer) {{
                        if (layer.toGeoJSON) {{
                            drawnData.features.push(layer.toGeoJSON());
                        }}
                    }});

                    const blob = new Blob([JSON.stringify(drawnData, null, 2)], {{type: 'application/json'}});
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = `drawn_polygons_${{new Date().toISOString().split('T')[0]}}.json`;
                    document.body.appendChild(a);
                    a.click();
                    document.body.removeChild(a);
                    URL.revokeObjectURL(url);
                }} else {{
                    alert('No drawings to save');
                }}
            }};

            window.triggerLoadDrawings = function() {{
                // Create a file input and trigger it
                const fileInput = document.createElement('input');
                fileInput.type = 'file';
                fileInput.accept = '.qpkg,.gpkg,.json,.geojson';
                fileInput.style.display = 'none';

                fileInput.onchange = function(e) {{
                    const file = e.target.files[0];
                    if (!file) return;

                    const formData = new FormData();
                    formData.append('file', file);

                    // Determine endpoint based on file type
                    const endpoint = file.name.toLowerCase().endsWith('.qpkg') || file.name.toLowerCase().endsWith('.gpkg')
                        ? '/upload-qpkg/'
                        : '/load-drawings/';

                    fetch(endpoint, {{
                        method: 'POST',
                        body: formData
                    }})
                    .then(response => response.json())
                    .then(data => {{
                        if (data.geojson || data.success) {{
                            location.reload(); // Reload to show new data
                        }}
                    }})
                    .catch(error => console.error('Upload error:', error));
                }};

                document.body.appendChild(fileInput);
                fileInput.click();
                document.body.removeChild(fileInput);
            }};

            window.exportSelection = function() {{
                // Export selected polygons (if any selection mechanism is implemented)
                if (currentGeoJsonLayer) {{
                    const geojsonData = currentGeoJsonLayer.toGeoJSON();
                    const blob = new Blob([JSON.stringify(geojsonData, null, 2)], {{type: 'application/json'}});
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = `selected_polygons_${{new Date().toISOString().split('T')[0]}}.json`;
                    document.body.appendChild(a);
                    a.click();
                    document.body.removeChild(a);
                    URL.revokeObjectURL(url);
                }} else {{
                    alert('No data to export');
                }}
            }};

            window.importSelection = function() {{
                // Same as triggerLoadDrawings for now
                triggerLoadDrawings();
            }};

            window.switchBasemap = function() {{
                // Get the basemap selector value and switch layers
                const selector = document.getElementById('basemapSelector');
                if (selector) {{
                    const selectedValue = selector.value;
                    console.log('Switching to basemap:', selectedValue);
                    // The layer control handles this automatically via the dropdown
                }}
            }};

            window.fitToSelected = function() {{
                // Fit map to selected features
                if (currentGeoJsonLayer) {{
                    map.fitBounds(currentGeoJsonLayer.getBounds());
                }}
            }};

            // File upload functionality
            window.uploadFile = function() {{
                const fileInput = document.getElementById('file-input');
                const file = fileInput.files[0];
                if (!file) return;

                const formData = new FormData();
                formData.append('file', file);

                fetch('/upload-qpkg/', {{
                    method: 'POST',
                    body: formData
                }})
                .then(response => response.json())
                .then(data => {{
                    if (data.geojson) {{
                        location.reload(); // Reload to show new data
                    }}
                }})
                .catch(error => console.error('Upload error:', error));
            }};

            window.closeUpload = function() {{
                const overlay = document.getElementById('file-upload-overlay');
                if (overlay) {{
                    overlay.style.display = 'none';
                }}
            }};

            window.showUpload = function() {{
                const overlay = document.getElementById('file-upload-overlay');
                if (overlay) {{
                    overlay.style.display = 'block';
                }}
            }};

            // Initialize map and Python controls
            document.addEventListener('DOMContentLoaded', function() {{
                initializeMap();
                {controls_js}
                console.log('✅ Direct Leaflet map with all providers initialized');
                console.log('✅ Python controls integrated with Leaflet map');
            }});
        </script>
    </body>
    </html>
    """

    return HTMLResponse(content=html_content)


@app.post("/upload-qpkg/")
async def upload_qpkg(file: UploadFile = File(...)):
    """Upload and process QPKG file"""
    global current_gdf
    
    if not (file.filename.endswith('.qpkg') or file.filename.endswith('.gpkg')):
        raise HTTPException(status_code=400, detail="File must be a QPKG or GPKG file")
    
    # Extract and process QPKG (same as before)
    with tempfile.NamedTemporaryFile(delete=False, suffix='.qpkg') as temp_file:
        content = await file.read()
        temp_file.write(content)
        temp_file_path = temp_file.name
    
    try:
        geojson_data = extract_qpkg_data(temp_file_path)
        if not geojson_data:
            raise HTTPException(status_code=400, detail="No geospatial data found in QPKG")
        
        # Add feature IDs if not present (current_gdf is set in extract_qpkg_data)
        gdf = get_current_gdf()
        if gdf is not None and 'feature_id' not in gdf.columns:
            gdf['feature_id'] = range(len(gdf))
        
        return {"geojson": json.loads(geojson_data)}
    
    finally:
        Path(temp_file_path).unlink(missing_ok=True)

@app.post("/get-adjacent-polygons/")
async def get_adjacent_polygons(selection: PolygonSelection):
    """Get selected polygon and its adjacent polygons"""
    current_gdf = get_current_gdf()
    
    if current_gdf is None:
        raise HTTPException(status_code=400, detail="No data loaded. Please upload a QPKG file first.")
    
    try:
        print(f"Finding adjacent polygons for feature {selection.feature_id} using method {selection.touch_method}")
        print(f"GeoDataFrame has {len(current_gdf)} features")
        
        # Find adjacent polygons
        adjacent_indices = find_adjacent_polygons(current_gdf, selection.feature_id, selection.touch_method)
        
        print(f"Found {len(adjacent_indices)} adjacent polygons: {adjacent_indices}")
        
        # Include the selected polygon
        all_indices = [selection.feature_id] + adjacent_indices
        
        # Filter GeoDataFrame to selected and adjacent polygons
        filtered_gdf = current_gdf.iloc[all_indices].copy()
        
        # Add selection status
        filtered_gdf['selection_type'] = ['selected' if i == selection.feature_id else 'adjacent' 
                                        for i in all_indices]
        
        # Convert to GeoJSON
        geojson_data = filtered_gdf.to_json()
        
        return {
            "geojson": json.loads(geojson_data),
            "selected_id": selection.feature_id,
            "adjacent_ids": adjacent_indices,
            "total_count": len(all_indices)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing selection: {str(e)}")


@app.get("/get-attributes/")
async def get_attributes():
    """Get all feature attributes from the loaded geospatial data"""
    current_gdf = get_current_gdf()
    
    if current_gdf is None:
        raise HTTPException(status_code=400, detail="No data loaded. Please upload a QPKG or GPKG file first.")
    
    try:
        # Convert GeoDataFrame to a list of dictionaries (excluding geometry)
        attributes_data = []
        for idx, row in current_gdf.iterrows():
            row_data = {"index": idx}
            for col in current_gdf.columns:
                if col != 'geometry':  # Exclude geometry column
                    value = row[col]
                    # Convert numpy types to Python types for JSON serialization
                    if hasattr(value, 'item'):
                        value = value.item()
                    elif hasattr(value, 'tolist'):
                        value = value.tolist()
                    row_data[col] = value
            attributes_data.append(row_data)
        
        # Get column information
        columns = [col for col in current_gdf.columns if col != 'geometry']
        
        return {
            "columns": columns,
            "data": attributes_data,
            "total_features": len(current_gdf)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting attributes: {str(e)}")


@app.get("/get-cadastral-structure/")
async def get_cadastral_structure():
    """Get the Italian cadastral data structure from S3 or local JSON file"""
    try:
        # Try S3 first
        s3_storage = get_s3_storage()
        cadastral_data = s3_storage.get_cadastral_structure()

        if cadastral_data:
            return cadastral_data

        # Fallback to local files
        possible_paths = [
            os.path.join(root_folder, "../data/cadastral_structure.json"),  # Local development
            os.path.join("/app/data/cadastral_structure.json"),  # Cloud Run
            os.path.join(os.getcwd(), "data/cadastral_structure.json"),  # Alternative
        ]

        cadastral_file_path = None
        for path in possible_paths:
            if os.path.exists(path):
                cadastral_file_path = path
                break

        if not cadastral_file_path:
            raise HTTPException(status_code=404, detail="Cadastral structure file not found in S3 or locally")

        with open(cadastral_file_path, 'r', encoding='utf-8') as f:
            cadastral_data = json.load(f)

        return cadastral_data

    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"Error parsing cadastral structure file: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading cadastral structure: {str(e)}")


@app.get("/cadastral-data", response_class=HTMLResponse)
async def show_cadastral_data(request: Request):
    """Display the Italian cadastral data structure in a readable HTML format"""
    try:
        # Try S3 first
        s3_storage = get_s3_storage()
        cadastral_data = s3_storage.get_cadastral_structure()

        if not cadastral_data:
            # Fallback to local files
            possible_paths = [
                os.path.join(root_folder, "../data/cadastral_structure.json"),  # Local development
                os.path.join("/app/data/cadastral_structure.json"),  # Cloud Run
                os.path.join(os.getcwd(), "data/cadastral_structure.json"),  # Alternative
            ]

            cadastral_file_path = None
            for path in possible_paths:
                if os.path.exists(path):
                    cadastral_file_path = path
                    break

            if not cadastral_file_path:
                raise HTTPException(status_code=404, detail="Cadastral structure file not found in S3 or locally")

            with open(cadastral_file_path, 'r', encoding='utf-8') as f:
                cadastral_data = json.load(f)
        
        # Generate HTML content
        html_content = """
<!DOCTYPE html>
<html>
<head>
    <title>Italian Cadastral Data Structure</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; background-color: #f5f5f5; }
        .container { max-width: 1200px; margin: 0 auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        h1 { color: #2c3e50; text-align: center; }
        h2 { color: #34495e; border-bottom: 2px solid #3498db; padding-bottom: 5px; }
        h3 { color: #7f8c8d; margin-top: 20px; }
        .region { margin-bottom: 30px; border: 1px solid #bdc3c7; border-radius: 5px; padding: 15px; }
        .province { margin-left: 20px; margin-bottom: 20px; }
        .comune { margin-left: 40px; margin-bottom: 10px; padding: 8px; background-color: #ecf0f1; border-radius: 3px; }
        .comune-name { font-weight: bold; color: #2980b9; }
        .comune-code { color: #7f8c8d; font-size: 0.9em; }
        .files { margin-left: 10px; font-size: 0.85em; color: #555; }
        .stats { background-color: #3498db; color: white; padding: 10px; border-radius: 5px; margin-bottom: 20px; text-align: center; }
        .search-box { margin-bottom: 20px; padding: 10px; background: #ecf0f1; border-radius: 5px; }
        .search-box input { width: 100%; padding: 8px; border: 1px solid #bdc3c7; border-radius: 3px; font-size: 14px; }
    </style>
    <script>
        function searchData() {
            const searchTerm = document.getElementById('searchInput').value.toLowerCase();
            const comunes = document.getElementsByClassName('comune');
            
            for (let comune of comunes) {
                const text = comune.textContent.toLowerCase();
                if (text.includes(searchTerm) || searchTerm === '') {
                    comune.style.display = 'block';
                } else {
                    comune.style.display = 'none';
                }
            }
        }
    </script>
</head>
<body>
    <div class="container">
        <h1>Italian Cadastral Data Structure</h1>
        
        <div class="search-box">
            <input type="text" id="searchInput" placeholder="Search for comune name or code..." onkeyup="searchData()">
        </div>
        
"""
        
        # Calculate statistics
        total_regions = len(cadastral_data)
        total_provinces = sum(len(region.values()) for region in cadastral_data.values())
        total_comunes = sum(
            len(province) for region in cadastral_data.values() 
            for province in region.values()
        )
        total_files = sum(
            len(comune['files']) for region in cadastral_data.values()
            for province in region.values()
            for comune in province.values()
        )
        
        html_content += f"""
        <div class="stats">
            <strong>Total: {total_regions} Regions | {total_provinces} Provinces | {total_comunes} Comunes | {total_files} Files</strong>
        </div>
        """
        
        # Generate content for each region
        for region_name, provinces in cadastral_data.items():
            html_content += f'<div class="region"><h2>{region_name}</h2>'
            
            for province_code, comunes in provinces.items():
                html_content += f'<div class="province"><h3>Province: {province_code}</h3>'
                
                for comune_key, comune_data in comunes.items():
                    html_content += f"""
                    <div class="comune">
                        <div class="comune-name">{comune_data['name']}</div>
                        <div class="comune-code">Code: {comune_data['code']}</div>
                        <div class="files">Files: {', '.join(comune_data['files'])}</div>
                    </div>
                    """
                
                html_content += '</div>'  # Close province
            
            html_content += '</div>'  # Close region
        
        html_content += """
        </div>
    </body>
    </html>
        """
        
        return HTMLResponse(content=html_content)
        
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"Error parsing cadastral structure file: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading cadastral structure: {str(e)}")


class CadastralFileRequest(BaseModel):
    files: List[str]  # List of file paths to load from S3


class S3ConfigRequest(BaseModel):
    bucket_name: str = "catasto-2025"
    region: str = "eu-central-1"
    endpoint_url: Optional[str] = None
    access_key_id: Optional[str] = None
    secret_access_key: Optional[str] = None


class DrawnPolygonsRequest(BaseModel):
    geojson: Dict[str, Any]  # GeoJSON data for drawn polygons
    filename: str = "drawn_polygons.json"  # Optional filename


@app.post("/load-cadastral-files/")
async def load_cadastral_files(request: CadastralFileRequest):
    """Load multiple cadastral files from S3 and return as separate GeoJSON layers"""
    global current_gdf

    if not request.files:
        raise HTTPException(status_code=400, detail="No files specified")

    try:
        s3_storage = get_s3_storage()

        # Prepare S3 keys with ITALIA/ prefix
        s3_keys = [f"ITALIA/{file_path}" for file_path in request.files]

        # Read multiple files from S3
        layers_data = s3_storage.read_multiple_files(s3_keys)

        if not layers_data:
            raise HTTPException(status_code=400, detail="No valid geospatial files could be loaded from S3")

        # Combine all GeoDataFrames for global access
        combined_gdf = None
        layers = []

        for layer_data in layers_data:
            layers.append({
                "name": layer_data["name"],
                "file": layer_data["file"],
                "geojson": layer_data["geojson"],
                "feature_count": layer_data["feature_count"]
            })

            # Combine GeoDataFrames
            gdf = layer_data["gdf"]
            if combined_gdf is None:
                combined_gdf = gdf.copy()
            else:
                combined_gdf = gpd.GeoDataFrame(
                    pd.concat([combined_gdf, gdf], ignore_index=True),
                    crs=combined_gdf.crs
                )

        # Set the combined data as current for other operations
        current_gdf = combined_gdf

        return {
            "layers": layers,
            "total_layers": len(layers),
            "total_features": len(combined_gdf) if combined_gdf is not None else 0,
            "source": "S3"
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error loading cadastral files from S3: {str(e)}")


@app.post("/save-drawn-polygons/")
async def save_drawn_polygons(request: DrawnPolygonsRequest):
    """Save drawn polygons as JSON file"""
    try:
        # Create a unique filename with timestamp
        timestamp = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
        filename = f"drawn_polygons_{timestamp}.json"
        filepath = Path("drawn_polygons") / filename
        
        # Create directory if it doesn't exist
        filepath.parent.mkdir(exist_ok=True)
        
        # Save GeoJSON data
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(request.geojson, f, indent=2, ensure_ascii=False)
        
        return {
            "message": "Polygons saved successfully",
            "filename": filename,
            "filepath": str(filepath),
            "feature_count": len(request.geojson.get("features", []))
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error saving drawn polygons: {str(e)}")




@app.post("/generate-map/")
async def generate_map(file: UploadFile = File(...)):
    """Generate map HTML from QPKG file"""
    if not (file.filename.endswith('.qpkg') or file.filename.endswith('.gpkg')):
        raise HTTPException(status_code=400, detail="File must be a QPKG or GPKG file")
    
    # Save uploaded file temporarily
    file_suffix = '.gpkg' if file.filename.endswith('.gpkg') else '.qpkg'
    with tempfile.NamedTemporaryFile(delete=False, suffix=file_suffix) as temp_file:
        content = await file.read()
        temp_file.write(content)
        temp_file_path = temp_file.name
    
    try:
        # Extract geospatial data
        geojson_data = extract_qpkg_data(temp_file_path)
        if not geojson_data:
            raise HTTPException(status_code=400, detail="No geospatial data found in QPKG")
        
        geojson_dict = json.loads(geojson_data)
        
        # Create folium map
        m = folium.Map(
            location=[41.8719, 12.5674],  # Center on Italy
            zoom_start=6,
            tiles='OpenStreetMap'
        )
        
        # Add additional basemap options
        folium.TileLayer(
            tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
            attr='Esri',
            name='Satellite',
            overlay=False,
            control=True
        ).add_to(m)
        
        # Add GeoJSON layer
        folium.GeoJson(
            geojson_dict,
            style_function=lambda _: {
                'fillColor': 'blue',
                'color': 'black',
                'weight': 2,
                'fillOpacity': 0.7,
            },
            tooltip=folium.features.GeoJsonTooltip(
                fields=list(geojson_dict['features'][0]['properties'].keys()) if geojson_dict['features'] else [],
                aliases=list(geojson_dict['features'][0]['properties'].keys()) if geojson_dict['features'] else [],
                localize=True
            )
        ).add_to(m)
        
        # Add Python-generated Folium controls
        m = map_controls.generate_folium_controls(m)
        
        # Fit bounds
        bounds = []
        for feature in geojson_dict['features']:
            if feature['geometry']['type'] == 'Point':
                coords = feature['geometry']['coordinates']
                bounds.append([coords[1], coords[0]])  # lat, lon
        
        if bounds:
            m.fit_bounds(bounds)
        
        return HTMLResponse(m._repr_html_())
    
    finally:
        # Clean up temp file
        Path(temp_file_path).unlink(missing_ok=True)


@app.get("/get-controls/")
async def get_controls():
    """Get current control definitions and states"""
    controls_data = {
        "groups": [
            {
                "id": group.id,
                "title": group.title,
                "position": group.position,
                "controls": [
                    {
                        "id": ctrl.id,
                        "title": ctrl.title,
                        "enabled": ctrl.enabled,
                        "tooltip": ctrl.tooltip,
                        "type": "button" if isinstance(ctrl, ControlButton) else "select",
                        "icon": getattr(ctrl, 'icon', None),
                        "onclick": getattr(ctrl, 'onclick', None),
                        "options": getattr(ctrl, 'options', None),
                        "onchange": getattr(ctrl, 'onchange', None),
                        "default_value": getattr(ctrl, 'default_value', None)
                    }
                    for ctrl in group.controls
                ]
            }
            for group in map_controls.control_groups
        ]
    }
    return controls_data


class ControlStateUpdate(BaseModel):
    control_id: str
    enabled: bool


@app.post("/update-control-state/")
async def update_control_state(update: ControlStateUpdate):
    """Update the state of a specific control"""
    success = map_controls.update_control_state(update.control_id, update.enabled)
    if success:
        return {"success": True, "message": f"Control {update.control_id} updated"}
    else:
        raise HTTPException(status_code=404, detail=f"Control {update.control_id} not found")


@app.post("/configure-s3/")
async def configure_s3(config: S3ConfigRequest):
    """Configure S3 storage settings"""
    try:
        # Create S3 settings from request
        s3_settings = S3Settings(
            s3_bucket_name=config.bucket_name,
            s3_region=config.region,
            s3_endpoint_url=config.endpoint_url,
            aws_access_key_id=config.access_key_id,
            aws_secret_access_key=config.secret_access_key
        )

        # Configure the global S3 storage
        s3_storage = configure_s3_storage(s3_settings)

        # Test connection by trying to list files
        try:
            files = s3_storage.list_files(prefix="ITALIA/", suffix=".shp")
            return {
                "success": True,
                "message": "S3 configured successfully",
                "bucket_name": config.bucket_name,
                "region": config.region,
                "test_files_found": len(files[:5]),  # Show first 5 files as test
                "sample_files": files[:5]
            }
        except Exception as test_error:
            return {
                "success": True,
                "message": "S3 configured but connection test failed",
                "bucket_name": config.bucket_name,
                "region": config.region,
                "test_error": str(test_error)
            }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error configuring S3: {str(e)}")


@app.get("/s3-status/")
async def get_s3_status():
    """Get current S3 configuration status"""
    try:
        s3_storage = get_s3_storage()
        settings = s3_storage.settings

        # Test basic connectivity
        try:
            files = s3_storage.list_files(prefix="ITALIA/", suffix=".shp")
            connection_status = "connected"
            file_count = len(files)
        except Exception as e:
            connection_status = "error"
            file_count = 0

        return {
            "bucket_name": settings.s3_bucket_name,
            "region": settings.s3_region,
            "endpoint_url": settings.s3_endpoint_url,
            "has_credentials": bool(settings.aws_access_key_id and settings.aws_secret_access_key),
            "connection_status": connection_status,
            "cadastral_files_found": file_count
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error checking S3 status: {str(e)}")
