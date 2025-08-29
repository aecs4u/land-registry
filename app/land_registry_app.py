from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import folium
import geopandas as gpd
import pandas as pd
import json
import os
from pathlib import Path
from pydantic import BaseModel
import tempfile
from typing import Dict, Any, List

from land_registry.map import extract_qpkg_data, find_adjacent_polygons, get_current_gdf
from land_registry.map_controls import map_controls, ControlButton, ControlSelect


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
    """Serve the main map application"""
    # Generate controls HTML from Python definitions
    controls_html = map_controls.generate_html()
    controls_js = map_controls.generate_javascript()
    
    return templates.TemplateResponse("map.html", {
        "request": request,
        "controls_html": controls_html,
        "controls_js": controls_js
    })


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
    """Get the Italian cadastral data structure from JSON file"""
    try:
        cadastral_file_path = os.path.join(root_folder, "../data/cadastral_structure.json")
        
        if not os.path.exists(cadastral_file_path):
            raise HTTPException(status_code=404, detail="Cadastral structure file not found")
        
        with open(cadastral_file_path, 'r', encoding='utf-8') as f:
            cadastral_data = json.load(f)
        
        return cadastral_data
        
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"Error parsing cadastral structure file: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading cadastral structure: {str(e)}")


class CadastralFileRequest(BaseModel):
    files: List[str]  # List of file paths to load


class DrawnPolygonsRequest(BaseModel):
    geojson: Dict[str, Any]  # GeoJSON data for drawn polygons
    filename: str = "drawn_polygons.json"  # Optional filename


@app.post("/load-cadastral-files/")
async def load_cadastral_files(request: CadastralFileRequest):
    """Load multiple cadastral files and return as separate GeoJSON layers"""
    global current_gdf
    
    if not request.files:
        raise HTTPException(status_code=400, detail="No files specified")
    
    # Assume cadastral files are in a specific directory structure
    cadastral_base_path = "s3://catasto-2025/ITALIA/"
    
    # Check if the base path is an S3 path
    is_s3_path = cadastral_base_path.startswith("s3://")
    
    try:
        layers = []
        combined_gdf = None
        
        for file_path in request.files:
            # Construct full file path
            full_path = cadastral_base_path / file_path
            
            if not full_path.exists():
                print(f"File not found: {full_path}")
                continue
                
            try:
                # Read the geospatial file
                gdf = gpd.read_file(full_path)
                
                if gdf is not None and len(gdf) > 0:
                    # Add layer identifier
                    layer_name = Path(file_path).stem
                    gdf['layer_name'] = layer_name
                    gdf['source_file'] = file_path
                    
                    # Add feature IDs if not present
                    if 'feature_id' not in gdf.columns:
                        gdf['feature_id'] = range(len(gdf))
                    
                    # Convert to GeoJSON
                    layer_geojson = json.loads(gdf.to_json())
                    
                    layers.append({
                        "name": layer_name,
                        "file": file_path,
                        "geojson": layer_geojson,
                        "feature_count": len(gdf)
                    })
                    
                    # Combine all data for global access
                    if combined_gdf is None:
                        combined_gdf = gdf.copy()
                    else:
                        combined_gdf = gpd.GeoDataFrame(
                            pd.concat([combined_gdf, gdf], ignore_index=True),
                            crs=combined_gdf.crs
                        )
                        
            except Exception as e:
                print(f"Error reading file {file_path}: {e}")
                continue
        
        if not layers:
            raise HTTPException(status_code=400, detail="No valid geospatial files could be loaded")
        
        # Set the combined data as current for other operations
        current_gdf = combined_gdf
        
        return {
            "layers": layers,
            "total_layers": len(layers),
            "total_features": len(combined_gdf) if combined_gdf is not None else 0
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error loading cadastral files: {str(e)}")


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


@app.post("/upload-qpkg/")
async def upload_qpkg(file: UploadFile = File(...)):
    """Upload and process QPKG file"""
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
        
        return {"geojson": json.loads(geojson_data)}
    
    finally:
        # Clean up temp file
        Path(temp_file_path).unlink(missing_ok=True)


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
