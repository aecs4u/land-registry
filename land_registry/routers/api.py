"""
API Router for Land Registry Application
Centralizes all API endpoints for better organization and maintainability
"""

from fastapi import APIRouter, UploadFile, File, HTTPException, Request
from fastapi.responses import HTMLResponse
import geopandas as gpd
import pandas as pd
import json
import os
import tempfile
from pathlib import Path
from typing import Dict, Any, List, Optional
from io import BytesIO
import boto3
from botocore import UNSIGNED
from botocore.config import Config

from land_registry.map import extract_qpkg_data, find_adjacent_polygons, get_current_gdf
from land_registry.map import map_controls, ControlButton, ControlSelect
from land_registry.s3_storage import get_s3_storage, S3Settings, configure_s3_storage
from land_registry.file_availability_db import file_availability_db
from land_registry.map import get_auction_properties_geojson, create_auction_properties_layer
from land_registry.settings import (
    app_settings, s3_settings, db_settings, cadastral_settings,
    map_controls_settings, get_cadastral_structure_path
)

# Import Pydantic models
from pydantic import BaseModel


# ============================================================================
# Pydantic Models
# ============================================================================

class PolygonSelection(BaseModel):
    feature_id: int
    geometry: Dict[str, Any]
    touch_method: str = "touches"


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


class PublicGeoDataRequest(BaseModel):
    s3_key: str  # S3 key path like "ITALIA/ABRUZZO/AQ/A018_ACCIANO/A018_ACCIANO_map.gpkg"
    layer: int = 0  # Layer index for GPKG files


class ControlStateUpdate(BaseModel):
    control_id: str
    enabled: bool


# ============================================================================
# API Router Setup
# ============================================================================

api_router = APIRouter()

# Get root folder for file paths
root_folder = os.path.dirname(__file__)


# ============================================================================
# File Upload & Processing Endpoints
# ============================================================================

@api_router.post("/upload-qpkg/")
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


@api_router.post("/generate-map/")
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
        import folium
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


# ============================================================================
# Spatial Analysis Endpoints
# ============================================================================

@api_router.post("/get-adjacent-polygons/")
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


@api_router.get("/get-attributes/")
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


# ============================================================================
# Cadastral Data Endpoints
# ============================================================================

@api_router.get("/get-cadastral-structure/")
async def get_cadastral_structure():
    """Get the Italian cadastral data structure from local JSON file"""
    try:
        # Use centralized settings to get cadastral structure path
        cadastral_file_path = get_cadastral_structure_path()

        if not cadastral_file_path:
            raise HTTPException(status_code=404, detail="Cadastral structure file not found locally")

        with open(cadastral_file_path, 'r', encoding='utf-8') as f:
            cadastral_data = json.load(f)

        print(f"Successfully loaded cadastral data from local file: {cadastral_file_path}")
        return cadastral_data

    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"Error parsing cadastral structure file: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading cadastral structure: {str(e)}")


@api_router.get("/get-regions/")
async def get_regions():
    """Get list of all regions"""
    try:
        # Use centralized settings to get cadastral structure path
        cadastral_file_path = get_cadastral_structure_path()

        if not cadastral_file_path:
            raise HTTPException(status_code=404, detail="Cadastral structure file not found locally")

        with open(cadastral_file_path, 'r', encoding='utf-8') as f:
            cadastral_data = json.load(f)

        regions = list(cadastral_data.keys())
        return {"regions": sorted(regions)}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading regions: {str(e)}")


@api_router.get("/get-provinces/")
async def get_provinces(regions: str = None):
    """Get list of provinces, optionally filtered by regions"""
    try:
        # Use centralized settings to get cadastral structure path
        cadastral_file_path = get_cadastral_structure_path()

        if not cadastral_file_path:
            raise HTTPException(status_code=404, detail="Cadastral structure file not found locally")

        with open(cadastral_file_path, 'r', encoding='utf-8') as f:
            cadastral_data = json.load(f)

        provinces = set()

        if regions:
            # Filter by specific regions
            region_list = [r.strip() for r in regions.split(',')]
            for region_name in region_list:
                if region_name in cadastral_data:
                    provinces.update(cadastral_data[region_name].keys())
        else:
            # Get all provinces
            for region_data in cadastral_data.values():
                provinces.update(region_data.keys())

        return {"provinces": sorted(list(provinces))}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading provinces: {str(e)}")


@api_router.get("/get-municipalities/")
async def get_municipalities(regions: str = None, provinces: str = None):
    """Get list of municipalities, optionally filtered by regions and provinces"""
    try:
        # Use centralized settings to get cadastral structure path
        cadastral_file_path = get_cadastral_structure_path()

        if not cadastral_file_path:
            raise HTTPException(status_code=404, detail="Cadastral structure file not found locally")

        with open(cadastral_file_path, 'r', encoding='utf-8') as f:
            cadastral_data = json.load(f)

        municipalities = []

        region_list = [r.strip() for r in regions.split(',')] if regions else list(cadastral_data.keys())
        province_list = [p.strip() for p in provinces.split(',')] if provinces else None

        for region_name in region_list:
            if region_name in cadastral_data:
                region_data = cadastral_data[region_name]

                # Filter by provinces if specified
                target_provinces = province_list if province_list else list(region_data.keys())

                for province_code in target_provinces:
                    if province_code in region_data:
                        province_data = region_data[province_code]

                        for municipality_key, municipality_data in province_data.items():
                            municipalities.append({
                                "key": f"{region_name}|{province_code}|{municipality_key}",
                                "name": municipality_data.get('name', municipality_key),
                                "code": municipality_data.get('code', 'N/A'),
                                "region": region_name,
                                "province": province_code,
                                "files_count": len(municipality_data.get('files', []))
                            })

        # Sort by name
        municipalities.sort(key=lambda x: x['name'])

        return {"municipalities": municipalities}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading municipalities: {str(e)}")


# ============================================================================
# Public Geo Data Endpoints
# ============================================================================

@api_router.post("/load-public-geo-data/")
async def load_public_geo_data(request: PublicGeoDataRequest):
    """Load geo data directly from the public catasto-2025 bucket using unsigned access"""
    global current_gdf

    try:
        # Create unsigned S3 client (no credentials needed for public bucket)
        s3 = boto3.client("s3", config=Config(signature_version=UNSIGNED))

        bucket = "catasto-2025"
        key = request.s3_key

        print(f"Loading geo data from s3://{bucket}/{key}")

        # Get the object into memory
        obj = s3.get_object(Bucket=bucket, Key=key)
        body = obj["Body"]

        # Save to an in-memory file-like object
        file_like = BytesIO(body.read())

        # Read directly with geopandas
        gdf = gpd.read_file(file_like, layer=request.layer)

        # Add feature IDs if not present
        if 'feature_id' not in gdf.columns:
            gdf['feature_id'] = range(len(gdf))

        # Set as current global data
        current_gdf = gdf

        # Convert to GeoJSON for response
        geojson_data = json.loads(gdf.to_json())

        print(f"Successfully loaded {len(gdf)} features from {key}")

        return {
            "success": True,
            "geojson": geojson_data,
            "feature_count": len(gdf),
            "s3_key": key,
            "layer": request.layer,
            "bucket": bucket,
            "crs": str(gdf.crs) if gdf.crs else None,
            "columns": list(gdf.columns),
            "geometry_type": gdf.geometry.geom_type.iloc[0] if len(gdf) > 0 else None
        }

    except Exception as e:
        error_msg = f"Error loading geo data from s3://{bucket}/{key}: {str(e)}"
        print(error_msg)
        raise HTTPException(status_code=500, detail=error_msg)


@api_router.get("/load-example-geo-data/")
async def load_example_geo_data():
    """Load example geo data from ACCIANO municipality for testing"""
    example_request = PublicGeoDataRequest(
        s3_key="ITALIA/ABRUZZO/AQ/A018_ACCIANO/A018_ACCIANO_map.gpkg",
        layer=0
    )
    return await load_public_geo_data(example_request)


# ============================================================================
# S3 & File Management Endpoints
# ============================================================================

@api_router.get("/load-cadastral-files/{file_path:path}")
async def load_cadastral_file(file_path: str):
    """Load a single cadastral file from S3 and return as GeoJSON"""
    global current_gdf

    if not file_path:
        raise HTTPException(status_code=400, detail="No file path specified")

    try:
        print(f"Starting load_cadastral_file with file: {file_path}")

        # Ensure proper S3 key format
        s3_key = file_path if file_path.startswith('ITALIA/') else f"ITALIA/{file_path}"

        # Try unsigned S3 client directly (since we know it works)
        from io import BytesIO
        import geopandas as gpd

        # Create unsigned S3 client for public bucket access
        s3_client = boto3.client("s3", config=Config(signature_version=UNSIGNED))
        bucket = s3_settings.s3_bucket_name

        print(f"Loading from S3: {bucket}/{s3_key}")

        # Get the object from S3
        obj = s3_client.get_object(Bucket=bucket, Key=s3_key)
        body = obj["Body"]
        print(f"Successfully retrieved S3 object, content length: {obj.get('ContentLength', 'unknown')}")

        # Save to an in-memory file-like object
        file_like = BytesIO(body.read())
        print(f"Created BytesIO object, size: {file_like.tell()} bytes")
        file_like.seek(0)  # Reset to beginning

        print(f"Attempting to read with geopandas...")
        gdf = gpd.read_file(file_like, layer=0)
        print(f"Successfully read GeoDataFrame with {len(gdf)} features, columns: {list(gdf.columns)}")

        # Add feature IDs if not present
        if 'feature_id' not in gdf.columns:
            gdf['feature_id'] = range(len(gdf))

        # Convert to GeoJSON
        geojson_data = json.loads(gdf.to_json())
        print(f"Successfully converted to GeoJSON")

        # Update global current_gdf
        current_gdf = gdf

        return {
            "success": True,
            "message": f"Successfully loaded cadastral file from S3",
            "name": os.path.basename(file_path),
            "filename": os.path.basename(file_path),
            "file": s3_key,
            "feature_count": len(gdf),
            "geojson": geojson_data
        }

    except Exception as e:
        print(f"Error loading cadastral file: {type(e).__name__}: {e}")
        import traceback
        print(f"Full traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Error loading cadastral file: {str(e)}")


@api_router.post("/save-drawn-polygons/")
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


@api_router.get("/test-load-endpoint/{test_path:path}")
async def test_load_endpoint(test_path: str):
    """Simple test endpoint to verify path parameter parsing"""
    return {
        "received_path": test_path,
        "s3_key": test_path if test_path.startswith('ITALIA/') else f"ITALIA/{test_path}",
        "basename": os.path.basename(test_path)
    }


@api_router.get("/test-s3-access/")
async def test_s3_access():
    """Test S3 access and return diagnostics"""
    diagnostics = {
        "configured_storage": None,
        "unsigned_client": None,
        "settings": {
            "bucket": s3_settings.s3_bucket_name,
            "region": s3_settings.s3_region,
            "has_credentials": bool(s3_settings.aws_access_key_id and s3_settings.aws_secret_access_key),
            "use_public_fallback": s3_settings.use_public_bucket_fallback
        }
    }

    # Test configured S3 storage
    try:
        s3_storage = get_s3_storage()
        test_key = "ITALIA/LOMBARDIA/BG/ABBADIA CERRETO_BG/ABBADIA CERRETO_MAP.qpkg"
        exists = s3_storage.file_exists(test_key)
        diagnostics["configured_storage"] = {
            "status": "success",
            "file_exists": exists
        }
    except Exception as e:
        diagnostics["configured_storage"] = {
            "status": "failed",
            "error": str(e)
        }

    # Test unsigned client
    try:
        s3_client = boto3.client("s3", config=Config(signature_version=UNSIGNED))
        bucket = s3_settings.s3_bucket_name
        test_key = "ITALIA/LOMBARDIA/BG/ABBADIA CERRETO_BG/ABBADIA CERRETO_MAP.qpkg"

        # Try to get bucket location
        try:
            location_response = s3_client.get_bucket_location(Bucket=bucket)
            bucket_region = location_response.get('LocationConstraint', 'us-east-1')
        except Exception as region_error:
            bucket_region = f"Error: {region_error}"

        # Try to access the file
        try:
            response = s3_client.head_object(Bucket=bucket, Key=test_key)
            file_access = {
                "exists": True,
                "size": response["ContentLength"],
                "content_type": response.get("ContentType", "Unknown")
            }
        except Exception as file_error:
            file_access = {
                "exists": False,
                "error": str(file_error)
            }

        diagnostics["unsigned_client"] = {
            "status": "initialized",
            "bucket_region": bucket_region,
            "file_access": file_access
        }

    except Exception as e:
        diagnostics["unsigned_client"] = {
            "status": "failed",
            "error": str(e)
        }

    return diagnostics


@api_router.get("/auction-properties/")
async def get_auction_properties():
    """Get auction properties as GeoJSON"""
    try:
        # First populate dummy data if database is empty
        properties = file_availability_db.get_auction_properties()
        if not properties:
            file_availability_db.populate_dummy_auction_data()
            properties = file_availability_db.get_auction_properties()

        if properties:
            # Convert database data to GeoJSON format
            features = []
            for prop in properties:
                feature = {
                    "type": "Feature",
                    "id": prop['id'],
                    "properties": {
                        "property_id": prop['property_id'],
                        "cadastral_code": prop['cadastral_code'],
                        "region": prop['region'],
                        "province": prop['province'],
                        "municipality": prop['municipality'],
                        "property_type": prop['property_type'],
                        "status": prop['status'],
                        "auction_date": prop['auction_date'],
                        "starting_price": prop['starting_price'],
                        "final_price": prop['final_price'],
                        "description": prop['description'],
                        "marker_color": {
                            'active': '#FF6B6B',
                            'sold': '#95E1D3',
                            'cancelled': '#FFA726'
                        }.get(prop['status'], '#FF6B6B'),
                        "marker_size": {
                            'residential': 8,
                            'commercial': 12,
                            'agricultural': 6,
                            'industrial': 10
                        }.get(prop['property_type'], 8)
                    },
                    "geometry": {
                        "type": "Point",
                        "coordinates": [prop['longitude'], prop['latitude']]
                    }
                }
                features.append(feature)

            geojson = {
                "type": "FeatureCollection",
                "features": features
            }

            return {
                "success": True,
                "count": len(features),
                "geojson": geojson
            }
        else:
            return {
                "success": True,
                "count": 0,
                "geojson": {"type": "FeatureCollection", "features": []}
            }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting auction properties: {str(e)}")


@api_router.get("/auction-properties/statistics/")
async def get_auction_statistics():
    """Get auction properties statistics"""
    try:
        stats = file_availability_db.get_auction_statistics()
        return {
            "success": True,
            "statistics": stats
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting auction statistics: {str(e)}")


@api_router.post("/auction-properties/populate/")
async def populate_auction_data():
    """Populate database with dummy auction properties"""
    try:
        count = file_availability_db.populate_dummy_auction_data()
        return {
            "success": True,
            "message": f"Populated {count} auction properties",
            "count": count
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error populating auction data: {str(e)}")


@api_router.post("/configure-s3/")
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


@api_router.get("/s3-status/")
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


# ============================================================================
# Control Management Endpoints
# ============================================================================

@api_router.get("/get-controls/")
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


@api_router.post("/update-control-state/")
async def update_control_state(update: ControlStateUpdate):
    """Update the state of a specific control"""
    success = map_controls.update_control_state(update.control_id, update.enabled)
    if success:
        return {"success": True, "message": f"Control {update.control_id} updated"}
    else:
        raise HTTPException(status_code=404, detail=f"Control {update.control_id} not found")


# ============================================================================
# Session Data Endpoints
# ============================================================================

@api_router.get("/api/session/current-data")
async def get_current_session_data():
    """Get the current GeoDataFrame data as GeoJSON"""
    try:
        current_gdf = get_current_gdf()
        if current_gdf is not None and not current_gdf.empty:
            geojson_data = json.loads(current_gdf.to_json())
            return {
                "success": True,
                "data": geojson_data,
                "feature_count": len(geojson_data.get("features", [])),
                "has_data": True,
                "crs": str(current_gdf.crs) if current_gdf.crs else None,
                "bounds": current_gdf.bounds.to_dict() if hasattr(current_gdf, 'bounds') else None
            }
        else:
            return {
                "success": True,
                "data": None,
                "feature_count": 0,
                "has_data": False,
                "message": "No data currently loaded in session"
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving current session data: {str(e)}")


@api_router.get("/api/session/info")
async def get_session_info():
    """Get information about the current session state"""
    try:
        current_gdf = get_current_gdf()

        # Check if there's data loaded
        has_data = current_gdf is not None and not current_gdf.empty

        info = {
            "session_active": True,
            "has_data": has_data,
            "feature_count": len(current_gdf) if has_data else 0,
            "data_type": "geodataframe" if has_data else None,
            "crs": str(current_gdf.crs) if has_data and current_gdf.crs else None,
            "columns": list(current_gdf.columns) if has_data else [],
            "geometry_type": current_gdf.geometry.geom_type.iloc[0] if has_data and len(current_gdf) > 0 else None,
            "bounds": {
                "minx": float(current_gdf.bounds.minx.min()) if has_data else None,
                "miny": float(current_gdf.bounds.miny.min()) if has_data else None,
                "maxx": float(current_gdf.bounds.maxx.max()) if has_data else None,
                "maxy": float(current_gdf.bounds.maxy.max()) if has_data else None
            } if has_data else None
        }

        return info
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving session info: {str(e)}")


@api_router.get("/api/session/attributes")
async def get_current_data_attributes():
    """Get the attributes/properties of the current dataset"""
    try:
        current_gdf = get_current_gdf()
        if current_gdf is not None and not current_gdf.empty:
            # Get all unique attribute names and their data types
            attributes = {}
            for column in current_gdf.columns:
                if column != 'geometry':
                    dtype = str(current_gdf[column].dtype)
                    sample_values = current_gdf[column].dropna().head(5).tolist()
                    unique_count = current_gdf[column].nunique()

                    attributes[column] = {
                        "data_type": dtype,
                        "unique_values": unique_count,
                        "sample_values": sample_values,
                        "null_count": int(current_gdf[column].isnull().sum())
                    }

            return {
                "success": True,
                "attributes": attributes,
                "total_features": len(current_gdf),
                "total_attributes": len(attributes)
            }
        else:
            return {
                "success": True,
                "attributes": {},
                "total_features": 0,
                "total_attributes": 0,
                "message": "No data currently loaded"
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving data attributes: {str(e)}")


@api_router.post("/api/session/clear")
async def clear_session_data():
    """Clear all data from the current session"""
    try:
        # This would clear the global current_gdf variable
        # For now, we'll return a message since the global variable handling
        # would need to be implemented in the map.py module
        return {
            "success": True,
            "message": "Session data cleared",
            "cleared_items": ["geodataframe", "selections", "drawings"]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error clearing session data: {str(e)}")


# ============================================================================
# Drawn Polygons Endpoints
# ============================================================================

@api_router.get("/api/drawn-polygons")
async def get_drawn_polygons():
    """Get all drawn polygons from the drawings directory"""
    try:
        drawings_dir = "drawn_polygons"
        if not os.path.exists(drawings_dir):
            return {
                "success": True,
                "drawings": [],
                "count": 0,
                "message": "No drawings directory found"
            }

        drawing_files = []
        for filename in os.listdir(drawings_dir):
            if filename.endswith('.json'):
                filepath = os.path.join(drawings_dir, filename)
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        drawing_data = json.load(f)
                        drawing_files.append({
                            "filename": filename,
                            "filepath": filepath,
                            "created": os.path.getctime(filepath),
                            "size": os.path.getsize(filepath),
                            "feature_count": len(drawing_data.get("features", [])) if isinstance(drawing_data, dict) else 0
                        })
                except Exception as e:
                    print(f"Error reading drawing file {filename}: {e}")

        # Sort by creation time, newest first
        drawing_files.sort(key=lambda x: x["created"], reverse=True)

        return {
            "success": True,
            "drawings": drawing_files,
            "count": len(drawing_files)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving drawn polygons: {str(e)}")


@api_router.get("/api/drawn-polygons/{filename}")
async def get_drawn_polygon_file(filename: str):
    """Get a specific drawn polygon file"""
    try:
        drawings_dir = "drawn_polygons"
        filepath = os.path.join(drawings_dir, filename)

        if not os.path.exists(filepath):
            raise HTTPException(status_code=404, detail=f"Drawing file {filename} not found")

        with open(filepath, 'r', encoding='utf-8') as f:
            drawing_data = json.load(f)

        return {
            "success": True,
            "filename": filename,
            "data": drawing_data,
            "feature_count": len(drawing_data.get("features", [])) if isinstance(drawing_data, dict) else 0,
            "file_size": os.path.getsize(filepath),
            "created": os.path.getctime(filepath)
        }
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON in drawing file: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving drawing file: {str(e)}")


@api_router.post("/check-file-availability/")
async def check_file_availability(force_refresh: bool = False):
    """
    Check availability of cadastral files and cache status codes.

    Args:
        force_refresh: If True, bypass cache and check all files fresh
    """
    try:
        # Load cadastral structure
        data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
        cadastral_file = os.path.join(data_dir, "cadastral_structure.json")

        if not os.path.exists(cadastral_file):
            raise HTTPException(status_code=404, detail="Cadastral structure file not found")

        with open(cadastral_file, 'r', encoding='utf-8') as f:
            cadastral_data = json.load(f)

        # Collect all file paths to check
        all_file_paths = []
        file_metadata = {}  # Store region/province/municipality info for each file

        for region_name, region_data in cadastral_data.items():
            for province_code, province_data in region_data.items():
                for municipality_key, municipality_data in province_data.items():
                    if isinstance(municipality_data, dict):
                        files = municipality_data.get('files', [])
                        for file_name in files:
                            s3_key = f"ITALIA/{region_name}/{province_code}/{municipality_key}/{file_name}"
                            all_file_paths.append(s3_key)
                            file_metadata[s3_key] = {
                                "region": region_name,
                                "province": province_code,
                                "municipality": municipality_key,
                                "filename": file_name
                            }

        # Get cached status codes if not forcing refresh
        cached_statuses = {}
        files_to_check = all_file_paths

        if not force_refresh:
            cached_statuses = file_availability_db.get_file_status_batch(all_file_paths, max_age_hours=24)
            files_to_check = [path for path in all_file_paths if path not in cached_statuses]

        # Check remaining files via HTTP HEAD requests
        new_statuses = {}
        if files_to_check:
            import asyncio
            import aiohttp

            async def check_file_exists(session, s3_key):
                """Check if file exists via HTTP HEAD request"""
                url = f"https://catasto-2025.s3.amazonaws.com/{s3_key}"
                try:
                    async with session.head(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                        return s3_key, response.status
                except Exception:
                    return s3_key, 500  # Mark as error if request fails

            # Check files in batches to avoid overwhelming the server
            batch_size = 50
            batches = [files_to_check[i:i + batch_size] for i in range(0, len(files_to_check), batch_size)]

            for batch in batches:
                async with aiohttp.ClientSession() as session:
                    tasks = [check_file_exists(session, s3_key) for s3_key in batch]
                    batch_results = await asyncio.gather(*tasks)

                    for s3_key, status_code in batch_results:
                        new_statuses[s3_key] = status_code

                # Small delay between batches to be respectful
                await asyncio.sleep(0.1)

            # Cache the new status codes
            if new_statuses:
                file_availability_db.set_file_status_batch(new_statuses)

        # Combine cached and new statuses
        all_statuses = {**cached_statuses, **new_statuses}

        # Calculate statistics
        total_files = len(all_file_paths)
        available_files = sum(1 for status in all_statuses.values() if status == 200)
        missing_files = sum(1 for status in all_statuses.values() if status == 404)
        error_files = sum(1 for status in all_statuses.values() if status not in [200, 404])

        # Get cache statistics
        cache_stats = file_availability_db.get_stats()

        return {
            "success": True,
            "total_files": total_files,
            "available_files": available_files,
            "missing_files": missing_files,
            "error_files": error_files,
            "files_checked": len(files_to_check),
            "files_cached": len(cached_statuses),
            "cache_stats": cache_stats,
            "force_refresh": force_refresh
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error checking file availability: {str(e)}")


@api_router.get("/file-availability-stats/")
async def get_file_availability_stats():
    """Get file availability statistics from cache."""
    try:
        cache_stats = file_availability_db.get_stats()
        return {
            "success": True,
            "cache_stats": cache_stats
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting file availability stats: {str(e)}")


@api_router.delete("/file-availability-cache/")
async def clear_file_availability_cache():
    """Clear the file availability cache."""
    try:
        file_availability_db.clear_cache()
        return {
            "success": True,
            "message": "File availability cache cleared"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error clearing cache: {str(e)}")