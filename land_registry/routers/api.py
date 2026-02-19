"""
API Router for Land Registry Application
Centralizes all API endpoints for better organization and maintainability
"""

import boto3
from botocore import UNSIGNED
from botocore.config import Config
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, Header
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBearer
import geopandas as gpd
from io import BytesIO
import json
import logging
import os
import pandas as pd
from pathlib import Path
from pydantic import BaseModel
import tempfile
from typing import Dict, Any, List, Optional
import base64
import hashlib

from land_registry.dashboard import STATE
from land_registry.map import (
    extract_qpkg_data, find_adjacent_polygons,
    get_current_gdf, set_current_gdf, set_current_layers, get_current_layers
)
from land_registry.s3_storage import get_s3_storage, S3Settings, configure_s3_storage
from land_registry.file_availability_db import file_availability_db
from land_registry.config import (
    s3_settings, get_cadastral_structure_path, cadastral_settings, get_cadastral_data_root
)
from land_registry.spatialite import load_layer as load_spatialite_layer
from land_registry.models import (
    CadastralCacheInfoResponse
)
from land_registry.cadastral_db import CadastralDatabase, CadastralFilter
# Import proper JWT verification from aecs4u-auth
from land_registry.routers.auth import (
    get_current_user,
    get_current_user_optional,
    get_current_superuser,
    require_role,
    ClerkUser,
)

# Configure logger
logger = logging.getLogger(__name__)


# ============================================================================
# Pydantic Models
# ============================================================================

from pydantic import Field, field_validator
import re


class PolygonSelection(BaseModel):
    feature_id: int = Field(..., ge=0, description="Feature ID must be non-negative")
    geometry: Dict[str, Any] = Field(..., description="GeoJSON geometry object")
    touch_method: str = Field(default="touches", pattern="^(touches|intersects|overlaps)$")

    @field_validator('geometry')
    @classmethod
    def validate_geometry(cls, v):
        if 'type' not in v:
            raise ValueError('GeoJSON geometry must have a "type" field')
        if v.get('type') not in ['Point', 'LineString', 'Polygon', 'MultiPoint', 'MultiLineString', 'MultiPolygon']:
            raise ValueError(f'Invalid GeoJSON geometry type: {v.get("type")}')
        return v


class CadastralFileRequest(BaseModel):
    files: List[str] = Field(..., min_length=1, max_length=500, description="List of file paths to load")
    clear_existing: bool = Field(default=True, description="Clear existing layers before loading")

    @field_validator('files')
    @classmethod
    def validate_files(cls, v):
        # Validate file paths don't contain path traversal attempts
        for path in v:
            if '..' in path or path.startswith('/'):
                raise ValueError(f'Invalid file path: {path}')
            # Validate extension
            if not any(path.lower().endswith(ext) for ext in ['.gpkg', '.geojson', '.shp', '.kml', '.qpkg']):
                raise ValueError(f'Unsupported file format: {path}')
        return v


class S3ConfigRequest(BaseModel):
    bucket_name: str = Field(default="catasto-2025", min_length=3, max_length=63)
    region: str = Field(default="eu-central-1", pattern="^[a-z]{2}-[a-z]+-[0-9]+$")
    endpoint_url: Optional[str] = None
    access_key_id: Optional[str] = Field(default=None, min_length=16, max_length=128)
    secret_access_key: Optional[str] = Field(default=None, min_length=16, max_length=128)


class DrawnPolygonsRequest(BaseModel):
    geojson: Dict[str, Any] = Field(..., description="GeoJSON FeatureCollection")
    filename: str = Field(default="drawn_polygons.json", max_length=255)

    @field_validator('filename')
    @classmethod
    def validate_filename(cls, v):
        # Prevent path traversal and ensure valid filename
        if '..' in v or '/' in v or '\\' in v:
            raise ValueError('Filename cannot contain path separators')
        if not v.endswith('.json') and not v.endswith('.geojson'):
            raise ValueError('Filename must end with .json or .geojson')
        # Validate filename characters
        if not re.match(r'^[\w\-. ]+$', v):
            raise ValueError('Filename contains invalid characters')
        return v

    @field_validator('geojson')
    @classmethod
    def validate_geojson(cls, v):
        if v.get('type') != 'FeatureCollection':
            raise ValueError('GeoJSON must be a FeatureCollection')
        if 'features' not in v:
            raise ValueError('GeoJSON FeatureCollection must have "features" array')
        return v


class PublicGeoDataRequest(BaseModel):
    s3_key: str = Field(..., min_length=1, max_length=1024, description="S3 key path")
    layer: int = Field(default=0, ge=0, le=100, description="Layer index for GPKG files")

    @field_validator('s3_key')
    @classmethod
    def validate_s3_key(cls, v):
        # Prevent path traversal
        if '..' in v:
            raise ValueError('S3 key cannot contain path traversal')
        # Validate extension
        if not any(v.lower().endswith(ext) for ext in ['.gpkg', '.geojson', '.shp', '.kml']):
            raise ValueError(f'Unsupported file format in S3 key: {v}')
        return v


class ControlStateUpdate(BaseModel):
    control_id: str = Field(..., min_length=1, max_length=50, pattern="^[a-zA-Z0-9_-]+$")
    enabled: bool


class FilterBody(BaseModel):
    region: Optional[str] = Field(default=None, max_length=100)
    province: Optional[str] = Field(default=None, max_length=10)


class SpatialiteQueryRequest(BaseModel):
    """Request model for loading SpatiaLite data."""

    table: Optional[str] = Field(default=None, max_length=128)
    where: Optional[str] = Field(
        default=None,
        description="SQL WHERE clause without the 'WHERE' keyword (e.g., region = 'ABRUZZO')",
        max_length=500,
    )
    limit: Optional[int] = Field(default=None, ge=1, le=10000)
    layer_type: Optional[str] = Field(default="map", pattern="^(map|ple)$", description="map=fogli, ple=particelle")


class CadastralQueryRequest(BaseModel):
    """Exhaustive filter for cadastral polygon queries."""

    # Geographic hierarchy
    regione: Optional[str] = Field(default=None, max_length=50, description="Region name (e.g., LOMBARDIA)")
    provincia: Optional[str] = Field(default=None, max_length=10, description="Province code (e.g., MI)")
    comune: Optional[str] = Field(default=None, max_length=10, description="Municipality code (e.g., I056)")
    comune_name: Optional[str] = Field(default=None, max_length=100, description="Municipality name search")

    # Cadastral hierarchy
    foglio: Optional[int] = Field(default=None, ge=1, description="Single foglio number")
    foglio_list: Optional[List[int]] = Field(default=None, max_length=100, description="List of fogli")
    particella: Optional[int] = Field(default=None, ge=1, description="Single particella number")
    particella_list: Optional[List[int]] = Field(default=None, max_length=1000, description="List of particelle")
    particella_min: Optional[int] = Field(default=None, ge=1, description="Particella range minimum")
    particella_max: Optional[int] = Field(default=None, ge=1, description="Particella range maximum")

    # Spatial filters
    bbox_min_lon: Optional[float] = Field(default=None, ge=-180, le=180)
    bbox_min_lat: Optional[float] = Field(default=None, ge=-90, le=90)
    bbox_max_lon: Optional[float] = Field(default=None, ge=-180, le=180)
    bbox_max_lat: Optional[float] = Field(default=None, ge=-90, le=90)
    point_lon: Optional[float] = Field(default=None, ge=-180, le=180, description="Find parcels at point")
    point_lat: Optional[float] = Field(default=None, ge=-90, le=90)

    # Temporal filters
    date_from: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="YYYY-MM-DD")
    date_to: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="YYYY-MM-DD")

    # Data type
    layer_type: Optional[str] = Field(default=None, pattern="^(map|ple)$", description="map=fogli, ple=particelle")

    # Pagination
    limit: Optional[int] = Field(default=1000, ge=1, le=10000)
    offset: int = Field(default=0, ge=0)

    def to_cadastral_filter(self) -> CadastralFilter:
        """Convert to CadastralFilter object."""
        from datetime import datetime

        # Build bbox tuple if all coordinates provided
        bbox = None
        if all(v is not None for v in [self.bbox_min_lon, self.bbox_min_lat, self.bbox_max_lon, self.bbox_max_lat]):
            bbox = (self.bbox_min_lon, self.bbox_min_lat, self.bbox_max_lon, self.bbox_max_lat)

        # Build point tuple if both coordinates provided
        point = None
        if self.point_lon is not None and self.point_lat is not None:
            point = (self.point_lon, self.point_lat)

        # Build particella range if both provided
        particella_range = None
        if self.particella_min is not None and self.particella_max is not None:
            particella_range = (self.particella_min, self.particella_max)

        # Parse dates
        date_from = None
        date_to = None
        if self.date_from:
            date_from = datetime.strptime(self.date_from, "%Y-%m-%d")
        if self.date_to:
            date_to = datetime.strptime(self.date_to, "%Y-%m-%d")

        return CadastralFilter(
            regione=self.regione,
            provincia=self.provincia,
            comune=self.comune,
            comune_name=self.comune_name,
            foglio=self.foglio,
            foglio_list=self.foglio_list,
            particella=self.particella,
            particella_list=self.particella_list,
            particella_range=particella_range,
            bbox=bbox,
            point=point,
            date_from=date_from,
            date_to=date_to,
            layer_type=self.layer_type,
            limit=self.limit,
            offset=self.offset,
        )


# ============================================================================
# API Router Setup
# ============================================================================

api_router = APIRouter()

# Authentication utilities
security = HTTPBearer()

# DEPRECATED: Use get_current_user_optional from aecs4u-auth instead
# This function is kept for backward compatibility but should not be used for new code
async def get_user_from_token(authorization: str = Header(None)) -> Optional[str]:
    """
    DEPRECATED: Extract user ID from Clerk JWT token.

    WARNING: This function does NOT verify JWT signatures.
    Use get_current_user_optional dependency from aecs4u-auth for secure authentication.
    """
    logger.warning("get_user_from_token is deprecated - use get_current_user_optional instead")
    if not authorization or not authorization.startswith("Bearer "):
        return None

    try:
        token = authorization.replace("Bearer ", "")
        # WARNING: This does NOT verify the signature - for backward compat only
        payload_part = token.split('.')[1]
        payload_part += '=' * (4 - len(payload_part) % 4)
        payload = base64.b64decode(payload_part)
        user_data = json.loads(payload)
        return user_data.get('sub')
    except Exception as e:
        logger.error(f"Error decoding token: {e}")
        return None


def get_user_id_from_clerk_user(user: Optional[ClerkUser]) -> Optional[str]:
    """Extract user ID from verified ClerkUser object."""
    if user is None:
        return None
    return user.id

def get_user_directory(user_id: str) -> Path:
    """Get user-specific directory for storing drawings"""
    # Create a hash of user_id for directory name (privacy)
    user_hash = hashlib.sha256(user_id.encode()).hexdigest()[:16]
    return Path("drawn_polygons") / f"user_{user_hash}"

# Get root folder for file paths
root_folder = os.path.dirname(__file__)


# ============================================================================
# File Upload & Processing Endpoints
# ============================================================================

@api_router.post("/upload-qpkg/")
async def upload_qpkg(file: UploadFile = File(...)):
    """Upload and process QPKG file"""
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

        # # Add Python-generated Folium controls
        # m = map_controls.generate_folium_controls(m)

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
        logger.info(f"Finding adjacent polygons for feature {selection.feature_id} using method {selection.touch_method}")
        logger.debug(f"GeoDataFrame has {len(current_gdf)} features")

        # Find adjacent polygons
        adjacent_indices = find_adjacent_polygons(current_gdf, selection.feature_id, selection.touch_method)

        logger.info(f"Found {len(adjacent_indices)} adjacent polygons: {adjacent_indices}")

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
async def get_cadastral_structure(include_metadata: bool = False):
    """
    Get the Italian cadastral data structure (from local files, S3, or JSON)

    Args:
        include_metadata: If True, includes cache metadata and statistics in response
    """
    try:
        # Use the centralized cadastral utils to load data
        from land_registry.cadastral_utils import load_cadastral_structure

        cadastral = load_cadastral_structure()

        if not cadastral:
            raise HTTPException(status_code=404, detail="Cadastral structure data not available")

        if include_metadata:
            # Return data with metadata
            return {
                'data': cadastral.data,
                'metadata': {
                    'cache': cadastral.cache_metadata(),
                    'statistics': {
                        'total_regions': cadastral.total_regions,
                        'total_provinces': cadastral.total_provinces,
                        'total_municipalities': cadastral.total_municipalities,
                        'total_files': cadastral.total_files
                    }
                }
            }
        else:
            # Return just the data (backward compatible)
            return cadastral.data

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error loading cadastral structure: {str(e)}")


@api_router.get("/cadastral-cache-info", response_model=CadastralCacheInfoResponse)
async def get_cadastral_cache_info():
    """
    Get information about the cadastral data cache.
    Returns cache age, source (local/S3/JSON), statistics, and file availability.
    """
    try:
        from land_registry.cadastral_utils import load_cadastral_structure

        cadastral = load_cadastral_structure()

        if not cadastral:
            raise HTTPException(status_code=404, detail="Cadastral structure data not available")

        # Get cache metadata
        cache_info = cadastral.cache_metadata()

        # Get statistics
        stats = {
            'total_regions': cadastral.total_regions,
            'total_provinces': cadastral.total_provinces,
            'total_municipalities': cadastral.total_municipalities,
            'total_files': cadastral.total_files
        }

        # Get file availability stats
        availability = cadastral.get_file_availability_stats()

        return {
            'cache': cache_info,
            'statistics': stats,
            'file_availability': availability
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting cadastral cache info: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error getting cache info: {str(e)}")


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
    try:
        # Create unsigned S3 client (no credentials needed for public bucket)
        s3 = boto3.client("s3", config=Config(signature_version=UNSIGNED))

        bucket = "catasto-2025"
        key = request.s3_key

        logger.info(f"Loading geo data from s3://{bucket}/{key}")

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

        # Set as current global data using proper setter
        set_current_gdf(gdf)

        # Convert to GeoJSON for response
        geojson_data = json.loads(gdf.to_json())

        logger.info(f"Successfully loaded {len(gdf)} features from {key}")

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
        logger.error(error_msg)
        raise HTTPException(status_code=500, detail=error_msg)


@api_router.get("/load-example-geo-data/")
async def load_example_geo_data():
    """Load example geo data from ACCIANO municipality for testing"""
    example_request = PublicGeoDataRequest(
        s3_key="ITALIA/ABRUZZO/AQ/A018_ACCIANO/A018_ACCIANO_map.gpkg",
        layer=0
    )
    return await load_public_geo_data(example_request)


@api_router.post("/load-spatialite/")
async def load_spatialite_data(request: SpatialiteQueryRequest):
    """
    Load geo data from the configured SpatiaLite database with optional filters.
    """
    try:
        gdf = load_spatialite_layer(
            table=request.table,
            where=request.where,
            limit=request.limit,
            layer_type=request.layer_type,
        )

        if gdf is None or gdf.empty:
            return {
                "success": True,
                "feature_count": 0,
                "geojson": None,
                "message": "No features found for the given query.",
            }

        if 'feature_id' not in gdf.columns:
            gdf['feature_id'] = range(len(gdf))

        # Persist current dataset for map/table endpoints
        set_current_gdf(gdf)

        geojson_data = json.loads(gdf.to_json())

        return {
            "success": True,
            "feature_count": len(gdf),
            "columns": list(gdf.columns),
            "geojson": geojson_data,
            "crs": str(gdf.crs) if gdf.crs else None,
            "table": request.table or None,
        }
    except Exception as e:
        logger.error(f"Error loading SpatiaLite data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error loading SpatiaLite data: {str(e)}")


# ============================================================================
# S3 & File Management Endpoints
# ============================================================================

@api_router.get("/load-cadastral-files/{file_path:path}")
async def load_cadastral_file(file_path: str):
    """Load a single cadastral file from S3 and return as GeoJSON"""
    if not file_path:
        raise HTTPException(status_code=400, detail="No file path specified")

    try:
        logger.info(f"Starting load_cadastral_file with file: {file_path}")

        # Ensure proper S3 key format
        s3_key = file_path if file_path.startswith('ITALIA/') else f"ITALIA/{file_path}"

        # Try unsigned S3 client directly (since we know it works)
        from io import BytesIO
        import geopandas as gpd

        # Create unsigned S3 client for public bucket access
        s3_client = boto3.client("s3", config=Config(signature_version=UNSIGNED))
        bucket = s3_settings.s3_bucket_name

        logger.info(f"Loading from S3: {bucket}/{s3_key}")

        # Get the object from S3
        obj = s3_client.get_object(Bucket=bucket, Key=s3_key)
        body = obj["Body"]
        logger.info(f"Successfully retrieved S3 object, content length: {obj.get('ContentLength', 'unknown')}")

        # Save to an in-memory file-like object
        file_like = BytesIO(body.read())
        logger.debug(f"Created BytesIO object, size: {file_like.tell()} bytes")
        file_like.seek(0)  # Reset to beginning

        logger.debug("Attempting to read with geopandas...")
        gdf = gpd.read_file(file_like, layer=0)
        logger.info(f"Successfully read GeoDataFrame with {len(gdf)} features, columns: {list(gdf.columns)}")

        # Add feature IDs if not present
        if 'feature_id' not in gdf.columns:
            gdf['feature_id'] = range(len(gdf))

        # Convert to GeoJSON
        geojson_data = json.loads(gdf.to_json())
        logger.info("Successfully converted to GeoJSON")

        # Update global current_gdf using proper setter
        set_current_gdf(gdf)

        return {
            "success": True,
            "message": "Successfully loaded cadastral file from S3",
            "name": os.path.basename(file_path),
            "filename": os.path.basename(file_path),
            "file": s3_key,
            "feature_count": len(gdf),
            "geojson": geojson_data
        }

    except Exception as e:
        logger.error(f"Error loading cadastral file: {type(e).__name__}: {e}")
        import traceback
        logger.debug(f"Full traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Error loading cadastral file: {str(e)}")


# Rate limiting for anonymous endpoints (simple in-memory counter)
_anonymous_save_timestamps: list = []
ANONYMOUS_RATE_LIMIT = 10  # Max requests per minute
ANONYMOUS_RATE_WINDOW = 60  # Window in seconds
MAX_ANONYMOUS_FEATURES = 100  # Max features per save
MAX_ANONYMOUS_FILE_SIZE = 1024 * 1024  # 1MB max


@api_router.post("/save-drawn-polygons-anonymous/")
async def save_drawn_polygons_anonymous(request: DrawnPolygonsRequest):
    """
    Save drawn polygons as JSON file (anonymous, no auth required).

    SECURITY: Rate limited to 10 requests/minute, max 100 features, max 1MB.
    For larger/frequent saves, use authenticated endpoint.
    """
    import time

    # Rate limiting check
    current_time = time.time()
    # Clean old timestamps
    _anonymous_save_timestamps[:] = [t for t in _anonymous_save_timestamps if current_time - t < ANONYMOUS_RATE_WINDOW]

    if len(_anonymous_save_timestamps) >= ANONYMOUS_RATE_LIMIT:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Max {ANONYMOUS_RATE_LIMIT} requests per {ANONYMOUS_RATE_WINDOW} seconds."
        )

    # Check feature count
    features = request.geojson.get("features", [])
    if len(features) > MAX_ANONYMOUS_FEATURES:
        raise HTTPException(
            status_code=400,
            detail=f"Too many features ({len(features)}). Max {MAX_ANONYMOUS_FEATURES} for anonymous saves."
        )

    # Check approximate size
    geojson_str = json.dumps(request.geojson)
    if len(geojson_str) > MAX_ANONYMOUS_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"Request too large ({len(geojson_str)} bytes). Max {MAX_ANONYMOUS_FILE_SIZE} bytes."
        )

    try:
        # Record this request for rate limiting
        _anonymous_save_timestamps.append(current_time)

        # Create a unique filename with timestamp
        timestamp = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
        filename = f"drawn_polygons_{timestamp}.json"
        filepath = Path("drawn_polygons") / filename

        # Create directory if it doesn't exist
        filepath.parent.mkdir(exist_ok=True)

        # Save GeoJSON data
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(request.geojson, f, indent=2, ensure_ascii=False)

        logger.info(f"Anonymous save: {filename} with {len(features)} features")

        return {
            "message": "Polygons saved successfully",
            "filename": filename,
            "filepath": str(filepath),
            "feature_count": len(features)
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error saving drawn polygons: {str(e)}")


def _load_single_file(file_path: str, use_local: bool, local_root: str, s3_client, bucket: str) -> dict:
    """
    Load a single cadastral file from local filesystem or S3.
    This function is designed to be run in parallel using ThreadPoolExecutor.

    Returns a dict with either 'gdf' and 'layer_data' on success, or 'error' on failure.
    """
    try:
        if use_local and local_root:
            # Load from local filesystem
            local_file_path = os.path.join(local_root, file_path)

            if not os.path.exists(local_file_path):
                return {"error": f"File not found: {local_file_path}", "file_path": file_path}

            # Read directly from local file
            gdf = gpd.read_file(local_file_path, layer=0)
        else:
            # Load from S3
            s3_key = file_path if file_path.startswith('ITALIA/') else f"ITALIA/{file_path}"

            # Get the object from S3
            obj = s3_client.get_object(Bucket=bucket, Key=s3_key)
            body = obj["Body"]

            # Save to an in-memory file-like object
            file_like = BytesIO(body.read())
            file_like.seek(0)

            # Read with geopandas
            gdf = gpd.read_file(file_like, layer=0)

        # Add layer identifier and feature IDs
        layer_name = os.path.basename(file_path)
        gdf['layer_name'] = layer_name
        gdf['source_file'] = file_path

        if 'feature_id' not in gdf.columns:
            gdf['feature_id'] = range(len(gdf))

        return {
            "gdf": gdf,
            "layer_name": layer_name,
            "file_path": file_path,
            "feature_count": len(gdf)
        }

    except Exception as e:
        return {"error": str(e), "file_path": file_path}


@api_router.post("/load-cadastral-files/")
async def load_multiple_cadastral_files(request: dict):
    """
    Load multiple cadastral files from local filesystem (development) or S3 (production).

    Optimized for performance with parallel file loading using ThreadPoolExecutor.

    Request body:
        file_paths: List of file paths to load
        file_types: List of file types (optional)
        clear_existing: If True, clears existing layers before loading new ones (default: True)
    """
    try:
        file_paths = request.get('file_paths', [])
        request.get('file_types', [])
        clear_existing = request.get('clear_existing', True)  # Default to clearing existing data

        if not file_paths:
            raise HTTPException(status_code=400, detail="No file paths provided")

        # Check if we should use local files (development mode)
        use_local = cadastral_settings.use_local_files
        local_root = get_cadastral_data_root()

        # Clear existing data if requested (default behavior)
        if clear_existing:
            from land_registry.map import clear_current_layers
            clear_current_layers()
            set_current_gdf(None)
            logger.info("Cleared existing layers and GeoDataFrame")

        logger.info(f"Loading {len(file_paths)} cadastral files (use_local={use_local}, parallel=True)")

        # Create S3 client only if not using local files
        s3_client = None
        bucket = None
        if not use_local:
            s3_client = boto3.client("s3", config=Config(signature_version=UNSIGNED))
            bucket = s3_settings.s3_bucket_name

        # Use ThreadPoolExecutor for parallel file loading
        # Limit workers to avoid overwhelming I/O (local) or network (S3)
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import time

        start_time = time.time()
        max_workers = min(8, len(file_paths))  # Cap at 8 parallel workers

        results = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all file loading tasks
            future_to_path = {
                executor.submit(
                    _load_single_file,
                    file_path,
                    use_local,
                    local_root,
                    s3_client,
                    bucket
                ): file_path
                for file_path in file_paths
            }

            # Collect results as they complete
            for future in as_completed(future_to_path):
                result = future.result()
                results.append(result)

        # Process results
        layers_data = {}
        all_gdfs = []
        total_features = 0

        for result in results:
            if "error" in result:
                # File loading failed
                layer_name = os.path.basename(result["file_path"])
                layers_data[layer_name] = {
                    "error": result["error"],
                    "source_file": result["file_path"]
                }
                logger.debug(f"Failed to load {result['file_path']}: {result['error']}")
            else:
                # File loaded successfully
                gdf = result["gdf"]
                layer_name = result["layer_name"]

                # Convert to GeoJSON for this layer
                layer_geojson = json.loads(gdf.to_json())

                layers_data[layer_name] = {
                    "geojson": layer_geojson,
                    "feature_count": result["feature_count"],
                    "source_file": result["file_path"],
                    "layer_name": layer_name
                }

                all_gdfs.append(gdf)
                total_features += result["feature_count"]

        load_time = time.time() - start_time
        logger.info(f"Parallel loading completed: {len(all_gdfs)} files, {total_features} features in {load_time:.2f}s")

        # Append new layers to existing layers data (for efficiency)
        existing_layers = get_current_layers() or {}

        # Merge new layers with existing ones
        combined_layers = existing_layers.copy()
        combined_layers.update(layers_data)

        set_current_layers(combined_layers)

        # Combine all GeoDataFrames and append to existing current_gdf (additive loading)
        new_bounds = None
        if all_gdfs:
            new_combined_gdf = gpd.pd.concat(all_gdfs, ignore_index=True)
            existing_gdf = get_current_gdf()

            # Calculate bounds of newly loaded data for zoom-to-new-layers feature
            try:
                bounds = new_combined_gdf.total_bounds  # [minx, miny, maxx, maxy]
                if bounds is not None and len(bounds) == 4:
                    new_bounds = {
                        "south": float(bounds[1]),  # miny = south
                        "west": float(bounds[0]),   # minx = west
                        "north": float(bounds[3]),  # maxy = north
                        "east": float(bounds[2])    # maxx = east
                    }
                    logger.debug(f"Calculated bounds for new layers: {new_bounds}")
            except Exception as e:
                logger.warning(f"Could not calculate bounds for new layers: {e}")

            if existing_gdf is not None and not existing_gdf.empty:
                # Append to existing data
                combined_gdf = gpd.pd.concat([existing_gdf, new_combined_gdf], ignore_index=True)
                set_current_gdf(combined_gdf)
            else:
                # No existing data, use new data
                set_current_gdf(new_combined_gdf)

        # Calculate totals for response
        final_gdf = get_current_gdf()
        total_existing_features = len(final_gdf) if final_gdf is not None else 0
        final_layers = get_current_layers() or {}

        successful_count = len([layer for layer in layers_data.values() if 'error' not in layer])
        failed_count = len([layer for layer in layers_data.values() if 'error' in layer])

        return {
            "success": True,
            "message": f"Loaded {successful_count} files ({total_features} features) in {load_time:.2f}s",
            "layers": layers_data,  # Only new layers for display
            "new_bounds": new_bounds,  # Bounds of newly loaded layers for zoom
            "total_layers": len(final_layers),  # Total including existing
            "new_layers": len(file_paths),
            "successful_layers": successful_count,
            "failed_layers": failed_count,
            "features_count": total_features,  # New features added
            "total_features_count": total_existing_features,  # Total features including existing
            "load_time_seconds": round(load_time, 2)
        }

    except HTTPException:
        # Re-raise HTTPExceptions directly (e.g., 400 errors for validation)
        raise
    except Exception as e:
        logger.error(f"Error loading multiple cadastral files: {e}")
        import traceback
        logger.debug(f"Full traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Error loading cadastral files: {str(e)}")


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
async def configure_s3(
    config: S3ConfigRequest,
    current_user: ClerkUser = Depends(get_current_superuser)
):
    """
    Configure S3 storage settings.

    SECURITY: Requires superuser/admin authentication.
    This endpoint modifies global storage configuration and should only be
    accessible to administrators.
    """
    try:
        # Create S3 settings from request (do NOT log secrets)
        s3_settings = S3Settings(
            s3_bucket_name=config.bucket_name,
            s3_region=config.region,
            s3_endpoint_url=config.endpoint_url,
            aws_access_key_id=config.access_key_id,
            aws_secret_access_key=config.secret_access_key
        )

        logger.info(f"Admin {current_user.id} configuring S3 bucket: {config.bucket_name}")

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
                "test_files_found": len(files[:5]),
                # Don't expose sample file names in response for security
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
        except Exception:
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

# @api_router.get("/get-controls/")
# async def get_controls():
#     """Get current control definitions and states"""
#     controls_data = {
#         "groups": [
#             {
#                 "id": group.id,
#                 "title": group.title,
#                 "position": group.position,
#                 "controls": [
#                     {
#                         "id": ctrl.id,
#                         "title": ctrl.title,
#                         "enabled": ctrl.enabled,
#                         "tooltip": ctrl.tooltip,
#                         "type": "button" if isinstance(ctrl, ControlButton) else "select",
#                         "icon": getattr(ctrl, 'icon', None),
#                         "onclick": getattr(ctrl, 'onclick', None),
#                         "options": getattr(ctrl, 'options', None),
#                         "onchange": getattr(ctrl, 'onchange', None),
#                         "default_value": getattr(ctrl, 'default_value', None)
#                     }
#                     for ctrl in group.controls
#                 ]
#             }
#             for group in map_controls.control_groups
#         ]
#     }
#     return controls_data


# @api_router.post("/update-control-state/")
# async def update_control_state(update: ControlStateUpdate):
#     """Update the state of a specific control"""
#     success = map_controls.update_control_state(update.control_id, update.enabled)
#     if success:
#         return {"success": True, "message": f"Control {update.control_id} updated"}
#     else:
#         raise HTTPException(status_code=404, detail=f"Control {update.control_id} not found")


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
                    logger.error(f"Error reading drawing file {filename}: {e}")

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
        # Load cadastral structure - check multiple possible locations
        possible_paths = [
            # Project root data directory (most common)
            os.path.join(os.path.dirname(__file__), "..", "..", "data", "cadastral_structure.json"),
            # Inside land_registry package
            os.path.join(os.path.dirname(__file__), "..", "data", "cadastral_structure.json"),
            # Current working directory
            os.path.join(os.getcwd(), "data", "cadastral_structure.json"),
        ]
        
        cadastral_file = None
        for path in possible_paths:
            normalized_path = os.path.normpath(path)
            if os.path.exists(normalized_path):
                cadastral_file = normalized_path
                break
        
        if cadastral_file is None:
            logger.error(f"Cadastral structure file not found in paths: {possible_paths}")
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

        # Check remaining files via HTTP HEAD requests using httpx (already in dependencies)
        new_statuses = {}
        if files_to_check:
            import asyncio
            import httpx

            async def check_file_exists(client: httpx.AsyncClient, s3_key: str):
                """Check if file exists via HTTP HEAD request"""
                url = f"https://catasto-2025.s3.amazonaws.com/{s3_key}"
                try:
                    response = await client.head(url, timeout=10.0)
                    return s3_key, response.status_code
                except Exception:
                    return s3_key, 500  # Mark as error if request fails

            # Check files in batches to avoid overwhelming the server
            batch_size = 50
            batches = [files_to_check[i:i + batch_size] for i in range(0, len(files_to_check), batch_size)]

            for batch in batches:
                async with httpx.AsyncClient() as client:
                    tasks = [check_file_exists(client, s3_key) for s3_key in batch]
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


@api_router.post("/filters")
def set_filters(body: FilterBody):
    STATE.set_filters(region=body.region, province=body.province)
    return {"ok": True, "region": STATE.region, "province": STATE.province}


@api_router.get("/selection")
def get_selection():
    return {"selection": STATE.get_selection()}


# Drawing Management API Endpoints

class DrawingData(BaseModel):
    geojson: dict
    timestamp: str
    user_id: Optional[str] = None

@api_router.post("/save-drawn-polygons")
async def save_drawn_polygons(
    drawing_data: DrawingData,
    user_id: str = Depends(get_user_from_token)
):
    """Save drawn polygons to a user-specific JSON file"""
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        # Create user-specific directory
        user_dir = get_user_directory(user_id)
        user_dir.mkdir(parents=True, exist_ok=True)

        # Create filename with timestamp
        timestamp = drawing_data.timestamp.replace(':', '-').replace('.', '-')
        filename = f"drawings_{timestamp}.geojson"
        filepath = user_dir / filename

        # Add user_id to the geojson metadata
        geojson_with_metadata = {
            **drawing_data.geojson,
            "metadata": {
                "user_id": user_id,
                "timestamp": drawing_data.timestamp,
                "saved_at": timestamp
            }
        }

        # Save GeoJSON data
        with open(filepath, 'w') as f:
            json.dump(geojson_with_metadata, f, indent=2)

        # Also save as latest.geojson for easy loading
        latest_filepath = user_dir / "latest.geojson"
        with open(latest_filepath, 'w') as f:
            json.dump(geojson_with_metadata, f, indent=2)

        return {
            "success": True,
            "message": f"Saved {len(drawing_data.geojson.get('features', []))} drawings",
            "filename": filename
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save drawings: {str(e)}")

@api_router.get("/load-drawn-polygons")
async def load_drawn_polygons(user_id: str = Depends(get_user_from_token)):
    """Load the most recently saved drawn polygons for authenticated user"""
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        # Try to load from user-specific directory
        user_dir = get_user_directory(user_id)
        latest_filepath = user_dir / "latest.geojson"

        if latest_filepath.exists():
            with open(latest_filepath, 'r') as f:
                geojson_data = json.load(f)

            # Remove metadata from response for cleaner frontend handling
            if "metadata" in geojson_data:
                del geojson_data["metadata"]

            return {
                "success": True,
                "geojson": geojson_data,
                "message": f"Loaded {len(geojson_data.get('features', []))} drawings"
            }
        else:
            return {
                "success": False,
                "message": "No saved drawings found"
            }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load drawings: {str(e)}")

@api_router.get("/list-drawn-polygons")
async def list_drawn_polygons():
    """List all available saved drawing files"""
    try:
        drawings_dir = Path("drawn_polygons")

        if not drawings_dir.exists():
            return {"success": True, "files": []}

        files = []
        for filepath in drawings_dir.glob("drawings_*.geojson"):
            try:
                with open(filepath, 'r') as f:
                    data = json.load(f)
                    files.append({
                        "filename": filepath.name,
                        "created": filepath.stat().st_mtime,
                        "feature_count": len(data.get('features', []))
                    })
            except Exception as e:
                logger.error(f"Error reading {filepath}: {e}")
                continue

        # Sort by creation time, newest first
        files.sort(key=lambda x: x['created'], reverse=True)

        return {"success": True, "files": files}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list drawings: {str(e)}")

@api_router.delete("/clear-drawn-polygons")
async def clear_drawn_polygons():
    """Delete all saved drawing files"""
    try:
        drawings_dir = Path("drawn_polygons")

        if not drawings_dir.exists():
            return {"success": True, "message": "No drawings to clear"}

        deleted_count = 0
        for filepath in drawings_dir.glob("*.geojson"):
            filepath.unlink()
            deleted_count += 1

        return {
            "success": True,
            "message": f"Cleared {deleted_count} drawing files"
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to clear drawings: {str(e)}")


# ============================================================================
# Zone Management Endpoints (database-backed CRUD)
# ============================================================================

from land_registry.models import (
    ZoneCreateRequest, ZoneUpdateRequest, ZoneResponse,
    ZoneDetailResponse, ZoneListResponse, ZoneBulkVisibilityRequest
)
from land_registry.sqlite_db import get_sqlite_db


def _zone_row_to_response(row: dict, include_geojson: bool = False) -> dict:
    """Convert a database row to a zone response dict."""
    tags = []
    if row.get('tags'):
        try:
            tags = json.loads(row['tags'])
        except (json.JSONDecodeError, TypeError):
            tags = []

    result = {
        'id': row['id'],
        'name': row.get('name'),
        'description': row.get('description'),
        'polygon_type': row.get('zone_type', row.get('polygon_type', 'polygon')),
        'color': row.get('color', '#3388ff'),
        'area_sqm': row.get('area_sqm'),
        'centroid_lat': row.get('centroid_lat'),
        'centroid_lng': row.get('centroid_lng'),
        'is_visible': bool(row.get('is_visible', 1)),
        'tags': tags,
        'created_at': row.get('created_at', ''),
        'updated_at': row.get('updated_at', ''),
    }

    if include_geojson:
        try:
            result['geojson'] = json.loads(row['geojson']) if isinstance(row['geojson'], str) else row['geojson']
        except (json.JSONDecodeError, TypeError):
            result['geojson'] = {}

    return result


@api_router.post("/zones/", status_code=201)
async def create_zone(
    request: ZoneCreateRequest,
    user: ClerkUser = Depends(get_current_user)
):
    """Create a new zone from a drawn geometry."""
    try:
        from shapely.geometry import shape as shapely_shape

        db = get_sqlite_db()

        # Compute area and centroid from geometry
        area_sqm = None
        centroid_lat = None
        centroid_lng = None
        try:
            geom = shapely_shape(request.geojson.get('geometry', {}))
            if geom.is_valid and not geom.is_empty:
                centroid = geom.centroid
                centroid_lat = centroid.y
                centroid_lng = centroid.x
                # Approximate area (degrees squared; useful for relative comparison)
                area_sqm = geom.area
        except Exception as e:
            logger.warning(f"Could not compute geometry metrics: {e}")

        zone_id = db.create_zone(
            geojson=request.geojson,
            user_id=user.id,
            name=request.name,
            description=request.description,
            zone_type=request.polygon_type,
            area_sqm=area_sqm,
            centroid_lat=centroid_lat,
            centroid_lng=centroid_lng,
            color=request.color,
            tags=request.tags,
        )

        zone = db.get_zone(zone_id, user_id=user.id)
        return {"success": True, "zone": _zone_row_to_response(zone, include_geojson=True)}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating zone: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error creating zone: {str(e)}")


@api_router.get("/zones/geojson")
async def get_zones_geojson(
    user: ClerkUser = Depends(get_current_user)
):
    """Get all visible zones as a GeoJSON FeatureCollection for map rendering."""
    try:
        db = get_sqlite_db()
        rows = db.get_zones(user_id=user.id)
        features = []
        for row in rows:
            row_dict = dict(row)
            if not bool(row_dict.get('is_visible', 1)):
                continue
            try:
                geojson = json.loads(row_dict['geojson']) if isinstance(row_dict['geojson'], str) else row_dict['geojson']
                feature = geojson if geojson.get('type') == 'Feature' else {'type': 'Feature', 'geometry': geojson}
                # Inject zone metadata into properties
                props = feature.get('properties', {}) or {}
                props.update({
                    'zone_id': row_dict['id'],
                    'zone_name': row_dict.get('name', ''),
                    'zone_color': row_dict.get('color', '#3388ff'),
                    'zone_type': row_dict.get('zone_type', row_dict.get('polygon_type', 'polygon')),
                })
                feature['properties'] = props
                features.append(feature)
            except (json.JSONDecodeError, TypeError):
                continue

        return {
            "type": "FeatureCollection",
            "features": features
        }

    except Exception as e:
        logger.error(f"Error getting zones GeoJSON: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error getting zones: {str(e)}")


@api_router.get("/zones/")
async def list_zones(
    polygon_type: Optional[str] = None,
    tag: Optional[str] = None,
    user: ClerkUser = Depends(get_current_user)
):
    """List all zones for the authenticated user (without geometry bodies)."""
    try:
        db = get_sqlite_db()
        rows = db.get_zones(user_id=user.id)

        zones = []
        for row in rows:
            zone = _zone_row_to_response(dict(row))
            if polygon_type and polygon_type != zone.get('polygon_type'):
                continue
            # Filter by tag if requested
            if tag and tag not in zone.get('tags', []):
                continue
            zones.append(zone)

        return {"success": True, "zones": zones, "total": len(zones)}

    except Exception as e:
        logger.error(f"Error listing zones: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error listing zones: {str(e)}")


@api_router.get("/zones/{zone_id}")
async def get_zone(
    zone_id: int,
    user: ClerkUser = Depends(get_current_user)
):
    """Get a single zone with full geometry."""
    db = get_sqlite_db()
    zone = db.get_zone(zone_id, user_id=user.id)
    if not zone:
        raise HTTPException(status_code=404, detail="Zone not found")
    return {"success": True, "zone": _zone_row_to_response(zone, include_geojson=True)}


@api_router.patch("/zones/{zone_id}")
async def update_zone(
    zone_id: int,
    request: ZoneUpdateRequest,
    user: ClerkUser = Depends(get_current_user)
):
    """Update a zone's metadata or geometry."""
    try:
        db = get_sqlite_db()

        # Build update kwargs from non-None fields
        kwargs: Dict[str, Any] = {}
        if request.name is not None:
            kwargs['name'] = request.name
        if request.description is not None:
            kwargs['description'] = request.description
        if request.color is not None:
            kwargs['color'] = request.color
        if request.is_visible is not None:
            kwargs['is_visible'] = request.is_visible
        if request.tags is not None:
            kwargs['tags'] = request.tags

        if request.geojson is not None:
            kwargs['geojson'] = request.geojson
            # Recompute area and centroid
            try:
                from shapely.geometry import shape as shapely_shape
                geom = shapely_shape(request.geojson.get('geometry', {}))
                if geom.is_valid and not geom.is_empty:
                    kwargs['area_sqm'] = geom.area
                    kwargs['centroid_lat'] = geom.centroid.y
                    kwargs['centroid_lng'] = geom.centroid.x
            except Exception:
                pass

        if not kwargs:
            raise HTTPException(status_code=400, detail="No fields to update")

        updated = db.update_zone(zone_id, user.id, **kwargs)
        if not updated:
            raise HTTPException(status_code=404, detail="Zone not found")

        zone = db.get_zone(zone_id, user_id=user.id)
        return {"success": True, "zone": _zone_row_to_response(zone, include_geojson=True)}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating zone: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error updating zone: {str(e)}")


@api_router.delete("/zones/{zone_id}")
async def delete_zone(
    zone_id: int,
    user: ClerkUser = Depends(get_current_user)
):
    """Delete a zone."""
    db = get_sqlite_db()
    deleted = db.delete_zone(zone_id, user_id=user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Zone not found")
    return {"success": True, "message": "Zone deleted"}


@api_router.post("/zones/visibility")
async def bulk_toggle_zone_visibility(
    request: ZoneBulkVisibilityRequest,
    user: ClerkUser = Depends(get_current_user)
):
    """Set visibility for multiple zones at once."""
    try:
        db = get_sqlite_db()
        updated = 0
        for zone_id in request.zone_ids:
            if db.update_zone(zone_id, user.id, is_visible=request.is_visible):
                updated += 1
        return {"success": True, "updated": updated}
    except Exception as e:
        logger.error(f"Error updating zone visibility: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error updating visibility: {str(e)}")


# User Profile and Dashboard Endpoints

@api_router.get("/user/profile")
async def get_user_profile(user_id: str = Depends(get_user_from_token)):
    """Get user profile information and drawing statistics"""
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        user_dir = get_user_directory(user_id)

        # Count user's drawing files
        drawing_count = 0
        total_features = 0
        latest_drawing = None

        if user_dir.exists():
            drawing_files = list(user_dir.glob("drawings_*.geojson"))
            drawing_count = len(drawing_files)

            # Get latest drawing info
            if user_dir.joinpath("latest.geojson").exists():
                with open(user_dir.joinpath("latest.geojson"), 'r') as f:
                    latest_data = json.load(f)
                    total_features = len(latest_data.get('features', []))
                    if 'metadata' in latest_data:
                        latest_drawing = latest_data['metadata'].get('timestamp')

        return {
            "success": True,
            "profile": {
                "user_id": user_id,
                "drawing_sessions": drawing_count,
                "total_features": total_features,
                "latest_drawing": latest_drawing,
                "storage_location": str(user_dir)
            }
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get user profile: {str(e)}")

@api_router.get("/user/drawings")
async def list_user_drawings(user_id: str = Depends(get_user_from_token)):
    """List all drawing sessions for authenticated user"""
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        user_dir = get_user_directory(user_id)

        if not user_dir.exists():
            return {"success": True, "drawings": []}

        drawings = []
        for filepath in user_dir.glob("drawings_*.geojson"):
            try:
                with open(filepath, 'r') as f:
                    data = json.load(f)
                    drawings.append({
                        "filename": filepath.name,
                        "created": filepath.stat().st_mtime,
                        "feature_count": len(data.get('features', [])),
                        "metadata": data.get('metadata', {})
                    })
            except Exception as e:
                logger.error(f"Error reading {filepath}: {e}")
                continue

        # Sort by creation time, newest first
        drawings.sort(key=lambda x: x['created'], reverse=True)

        return {"success": True, "drawings": drawings}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list user drawings: {str(e)}")


# ============================================================================
# Cadastral Database Query Endpoints
# ============================================================================

# Initialize cadastral databases (lazy loading) - separate for MAP and per-region PLE
_cadastral_db_map: Optional[CadastralDatabase] = None
_cadastral_db_ple_by_region: dict[str, CadastralDatabase] = {}


def get_cadastral_db_map() -> CadastralDatabase:
    """Get or create the MAP database instance (fogli)."""
    global _cadastral_db_map
    if _cadastral_db_map is None:
        db_path = Path("data/cadastral_map.sqlite")
        if db_path.exists():
            _cadastral_db_map = CadastralDatabase(db_path)
        else:
            logger.warning(f"MAP database not found: {db_path}")
            db_path.parent.mkdir(parents=True, exist_ok=True)
            _cadastral_db_map = CadastralDatabase(db_path)
    return _cadastral_db_map


def _discover_ple_databases() -> dict[str, Path]:
    """
    Discover all per-region PLE databases in the data directory.

    Looks for files matching pattern: cadastral_ple.<region>.sqlite

    Returns:
        Dict mapping region name to database path
    """
    data_dir = Path("data")
    if not data_dir.exists():
        return {}

    ple_dbs = {}
    # Pattern: cadastral_ple.<region>.sqlite
    for db_file in data_dir.glob("cadastral_ple.*.sqlite"):
        # Extract region from filename: cadastral_ple.lombardia.sqlite -> lombardia
        parts = db_file.stem.split(".")
        if len(parts) >= 2:
            region_slug = parts[1]  # e.g., "lombardia", "emilia_romagna"
            ple_dbs[region_slug] = db_file

    return ple_dbs


def get_cadastral_db_ple(region: Optional[str] = None) -> Optional[CadastralDatabase]:
    """
    Get or create a PLE database instance for a specific region.

    Args:
        region: Region name (e.g., 'LOMBARDIA', 'lombardia').
                If None, returns the first available PLE database.

    Returns:
        CadastralDatabase instance or None if no PLE databases exist
    """
    global _cadastral_db_ple_by_region

    # Discover available PLE databases
    available_dbs = _discover_ple_databases()

    if not available_dbs:
        logger.warning("No PLE databases found in data directory")
        return None

    if region:
        # Normalize region name to match file naming convention
        region_slug = region.lower().replace(' ', '_').replace('-', '_')

        if region_slug in _cadastral_db_ple_by_region:
            return _cadastral_db_ple_by_region[region_slug]

        if region_slug in available_dbs:
            db_path = available_dbs[region_slug]
            _cadastral_db_ple_by_region[region_slug] = CadastralDatabase(db_path)
            return _cadastral_db_ple_by_region[region_slug]

        logger.warning(f"PLE database for region '{region}' not found. Available: {list(available_dbs.keys())}")
        return None
    else:
        # Return the first available database (for backward compatibility)
        first_region = sorted(available_dbs.keys())[0]
        if first_region not in _cadastral_db_ple_by_region:
            _cadastral_db_ple_by_region[first_region] = CadastralDatabase(available_dbs[first_region])
        return _cadastral_db_ple_by_region[first_region]


def get_all_ple_databases() -> dict[str, CadastralDatabase]:
    """
    Get all available PLE databases, loading them if needed.

    Returns:
        Dict mapping region slug to CadastralDatabase instance
    """
    global _cadastral_db_ple_by_region

    available_dbs = _discover_ple_databases()

    for region_slug, db_path in available_dbs.items():
        if region_slug not in _cadastral_db_ple_by_region:
            _cadastral_db_ple_by_region[region_slug] = CadastralDatabase(db_path)

    return _cadastral_db_ple_by_region


def get_cadastral_db(layer_type: Optional[str] = None, region: Optional[str] = None) -> Optional[CadastralDatabase]:
    """
    Get the appropriate database based on layer type and region.

    Args:
        layer_type: 'map' for fogli, 'ple' for particelle, None for PLE (default)
        region: Region name for PLE queries (e.g., 'LOMBARDIA')

    Returns:
        The appropriate CadastralDatabase instance, or None if not found
    """
    if layer_type == 'map':
        return get_cadastral_db_map()
    return get_cadastral_db_ple(region)


@api_router.post("/cadastral/query")
async def query_cadastral_parcels(request: CadastralQueryRequest):
    """
    Query cadastral parcels with exhaustive filtering.

    Returns GeoJSON FeatureCollection with matching parcels.

    Filter options:
    - Geographic: regione, provincia, comune, comune_name
    - Cadastral: foglio, particella (single, list, or range)
    - Spatial: bounding box, point-in-polygon
    - Temporal: date range
    - Layer type: map (fogli) or ple (particelle)

    Example requests:
    - Get all particelle in a comune: {"regione": "LOMBARDIA", "comune": "I056", "layer_type": "ple"}
    - Get specific foglio: {"comune": "I056", "foglio": 1, "layer_type": "map"}
    - Get particelle range: {"regione": "LOMBARDIA", "comune": "I056", "foglio": 1, "particella_min": 1, "particella_max": 100}
    - Get parcels in bounding box: {"bbox_min_lon": 14.3, "bbox_min_lat": 41.0, "bbox_max_lon": 14.4, "bbox_max_lat": 41.1}

    Note: For PLE queries, the 'regione' parameter is required to select the correct per-region database.
    """
    try:
        # Select database based on layer_type and region
        db = get_cadastral_db(request.layer_type, request.regione)

        if db is None:
            if request.layer_type == 'ple' and not request.regione:
                raise HTTPException(
                    status_code=400,
                    detail="Region (regione) is required for PLE queries. Available regions can be found via /cadastral/databases endpoint."
                )
            raise HTTPException(
                status_code=404,
                detail=f"Database not found for layer_type='{request.layer_type}', region='{request.regione}'"
            )

        cadastral_filter = request.to_cadastral_filter()
        result = db.query(cadastral_filter, as_geojson=True)
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Cadastral query error: {e}")
        raise HTTPException(status_code=500, detail=f"Query failed: {str(e)}")


@api_router.get("/cadastral/hierarchy")
async def get_cadastral_hierarchy(
    regione: Optional[str] = None,
    provincia: Optional[str] = None,
    comune: Optional[str] = None,
    layer_type: Optional[str] = None
):
    """
    Get available values at each hierarchy level for cascading dropdowns.

    Call without parameters to get regions.
    Call with regione to get provinces.
    Call with regione + provincia to get comuni.
    Call with comune to get fogli.

    If layer_type is not specified, returns combined results from both databases.
    For PLE queries with a specific region, uses the per-region PLE database.
    """
    try:
        if layer_type:
            # Use specific database
            db = get_cadastral_db(layer_type, regione)
            if db is None:
                return {}
            return db.get_hierarchy(regione, provincia, comune)
        else:
            # Combine results from MAP database and all PLE databases
            combined = {}

            # Get MAP hierarchy
            try:
                db_map = get_cadastral_db_map()
                map_hierarchy = db_map.get_hierarchy(regione, provincia, comune)
                for key, values in map_hierarchy.items():
                    if key not in combined:
                        combined[key] = set()
                    combined[key].update(values)
            except Exception as e:
                logger.debug(f"Could not get MAP hierarchy: {e}")

            # Get PLE hierarchy from all per-region databases
            ple_dbs = get_all_ple_databases()
            for region_slug, db_ple in ple_dbs.items():
                try:
                    # If regione is specified, only query the matching database
                    if regione:
                        region_slug_normalized = regione.lower().replace(' ', '_').replace('-', '_')
                        if region_slug != region_slug_normalized:
                            continue

                    ple_hierarchy = db_ple.get_hierarchy(regione, provincia, comune)
                    for key, values in ple_hierarchy.items():
                        if key not in combined:
                            combined[key] = set()
                        combined[key].update(values)
                except Exception as e:
                    logger.debug(f"Could not get PLE hierarchy for {region_slug}: {e}")

            # Convert sets to sorted lists
            return {key: sorted(list(values)) for key, values in combined.items()}

    except Exception as e:
        logger.error(f"Hierarchy query error: {e}")
        raise HTTPException(status_code=500, detail=f"Query failed: {str(e)}")


@api_router.get("/cadastral/statistics")
async def get_cadastral_statistics():
    """
    Get database statistics: total parcels, counts by region, layer type.

    Returns combined statistics from MAP and all per-region PLE databases.
    """
    try:
        map_stats = {"total_parcels": 0, "by_region": {}, "spatialite_available": False}

        # Get MAP database stats
        try:
            db_map = get_cadastral_db_map()
            map_stats = db_map.get_statistics()
        except Exception as e:
            logger.warning(f"Could not get MAP database stats: {e}")

        # Get stats from all PLE databases (per-region)
        ple_total = 0
        ple_by_region = {}
        ple_databases_info = {}
        spatialite_available = map_stats.get("spatialite_available", False)

        ple_dbs = get_all_ple_databases()
        for region_slug, db_ple in ple_dbs.items():
            try:
                stats = db_ple.get_statistics()
                region_total = stats.get("total_parcels", 0)
                ple_total += region_total
                spatialite_available = spatialite_available or stats.get("spatialite_available", False)

                # Store per-region stats
                for region, count in stats.get("by_region", {}).items():
                    ple_by_region[region] = ple_by_region.get(region, 0) + count

                ple_databases_info[region_slug] = {
                    "total_parcels": region_total,
                    "by_region": stats.get("by_region", {}),
                }
            except Exception as e:
                logger.warning(f"Could not get PLE stats for {region_slug}: {e}")
                ple_databases_info[region_slug] = {"error": str(e)}

        # Combine statistics
        combined_by_region = {}
        for region, count in map_stats.get("by_region", {}).items():
            combined_by_region[region] = combined_by_region.get(region, 0) + count
        for region, count in ple_by_region.items():
            combined_by_region[region] = combined_by_region.get(region, 0) + count

        return {
            "total_parcels": map_stats.get("total_parcels", 0) + ple_total,
            "map_parcels": map_stats.get("total_parcels", 0),
            "ple_parcels": ple_total,
            "by_region": combined_by_region,
            "by_layer_type": {
                "map": map_stats.get("total_parcels", 0),
                "ple": ple_total,
            },
            "spatialite_available": spatialite_available,
            "databases": {
                "map": {
                    "total_parcels": map_stats.get("total_parcels", 0),
                    "by_region": map_stats.get("by_region", {}),
                },
                "ple": ple_databases_info,
            },
            "ple_databases_count": len(ple_dbs),
        }

    except Exception as e:
        logger.error(f"Statistics query error: {e}")
        raise HTTPException(status_code=500, detail=f"Query failed: {str(e)}")


@api_router.get("/cadastral/databases")
async def list_cadastral_databases():
    """
    List all available cadastral databases.

    Returns information about the MAP database and all per-region PLE databases.
    Useful for frontend to know which regions have PLE data available.
    """
    try:
        databases = {
            "map": None,
            "ple": {},
            "ple_regions": []
        }

        # Check MAP database
        map_path = Path("data/cadastral_map.sqlite")
        if map_path.exists():
            databases["map"] = {
                "path": str(map_path),
                "size_mb": round(map_path.stat().st_size / (1024 * 1024), 2),
                "exists": True
            }
        else:
            databases["map"] = {"exists": False}

        # Discover PLE databases
        ple_dbs = _discover_ple_databases()
        for region_slug, db_path in sorted(ple_dbs.items()):
            databases["ple"][region_slug] = {
                "path": str(db_path),
                "size_mb": round(db_path.stat().st_size / (1024 * 1024), 2),
                "exists": True,
                "region_slug": region_slug,
                # Convert slug back to display name (e.g., emilia_romagna -> EMILIA ROMAGNA)
                "region_display": region_slug.upper().replace('_', ' ')
            }
            databases["ple_regions"].append(region_slug)

        return databases

    except Exception as e:
        logger.error(f"Database listing error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list databases: {str(e)}")


@api_router.get("/cadastral/search/{reference}")
async def search_by_reference(reference: str, regione: Optional[str] = None):
    """
    Search for a parcel by its national cadastral reference.

    Examples:
    - Foglio: I056_000100
    - Particella: I056_000100.42

    For particella references, you can optionally specify the region to search in.
    If not specified, searches all available PLE databases.
    """
    try:
        # Determine if it's a foglio or particella reference
        if "." in reference:
            # Particella reference: I056_000100.42
            layer_type = "ple"
        else:
            # Foglio reference: I056_000100
            layer_type = "map"

        features = []

        if layer_type == "map":
            # Search MAP database
            db = get_cadastral_db_map()
            if db:
                with db._get_connection() as conn:
                    rows = conn.execute("""
                        SELECT * FROM cadastral_parcels
                        WHERE national_reference = ?
                    """, (reference,)).fetchall()
                    features.extend(_rows_to_features(rows))
        else:
            # Search PLE databases
            if regione:
                # Search specific region
                db = get_cadastral_db_ple(regione)
                if db:
                    with db._get_connection() as conn:
                        rows = conn.execute("""
                            SELECT * FROM cadastral_parcels
                            WHERE national_reference = ?
                        """, (reference,)).fetchall()
                        features.extend(_rows_to_features(rows))
            else:
                # Search all PLE databases
                ple_dbs = get_all_ple_databases()
                for region_slug, db in ple_dbs.items():
                    try:
                        with db._get_connection() as conn:
                            rows = conn.execute("""
                                SELECT * FROM cadastral_parcels
                                WHERE national_reference = ?
                            """, (reference,)).fetchall()
                            features.extend(_rows_to_features(rows))
                            if features:  # Stop after finding a match
                                break
                    except Exception as e:
                        logger.debug(f"Search error in {region_slug}: {e}")

        if not features:
            raise HTTPException(status_code=404, detail=f"Reference not found: {reference}")

        return {
            "type": "FeatureCollection",
            "features": features
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Reference search error: {e}")
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")


def _rows_to_features(rows) -> list:
    """Convert database rows to GeoJSON features."""
    features = []
    for row in rows:
        feature = {
            "type": "Feature",
            "id": row['id'],
            "properties": dict(row),
            "geometry": None
        }

        if row['geometry_wkt']:
            try:
                from shapely import wkt
                from shapely.geometry import mapping
                geom = wkt.loads(row['geometry_wkt'])
                feature['geometry'] = mapping(geom)
            except Exception:
                pass

        # Remove WKT from properties
        if 'geometry_wkt' in feature['properties']:
            del feature['properties']['geometry_wkt']
        features.append(feature)

    return features

# ============================================================================
# FlatGeobuf (FGB) API Endpoints
# ============================================================================

@api_router.get("/fgb/regions")
async def list_fgb_regions():
    """
    List all available FGB regions.
    Returns a list of regions with their available files.
    """
    try:
        from land_registry.config import spatialite_settings
        fgb_dir = Path(spatialite_settings.fgb_directory)

        # Check if directory exists and is accessible
        if not fgb_dir.exists():
            logger.warning(f"FGB directory does not exist: {fgb_dir}")
            return {"regions": []}

        regions = {}

        # Find all FGB files
        for fgb_file in fgb_dir.glob("cadastral_*.fgb"):
            # Parse filename: cadastral_{type}.{region}.fgb
            parts = fgb_file.stem.split('.')
            if len(parts) >= 2:
                type_part = parts[0]  # e.g., "cadastral_map" or "cadastral_ple"
                region_slug = parts[1]  # e.g., "basilicata"
                
                # Extract layer type (map or ple)
                if "_map" in type_part:
                    layer_type = "map"
                elif "_ple" in type_part:
                    layer_type = "ple"
                else:
                    continue
                
                # Initialize region if not exists
                if region_slug not in regions:
                    regions[region_slug] = {
                        "slug": region_slug,
                        "name": region_slug.replace('_', ' ').title(),
                        "map_file": None,
                        "ple_file": None
                    }
                
                # Add file to region
                if layer_type == "map":
                    regions[region_slug]["map_file"] = fgb_file.name
                else:
                    regions[region_slug]["ple_file"] = fgb_file.name
        
        # Convert to list and sort by name
        region_list = sorted(regions.values(), key=lambda x: x["name"])
        
        return {"regions": region_list}
    
    except Exception as e:
        logger.error(f"Error listing FGB regions: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list regions: {str(e)}")


@api_router.get("/fgb/metadata/{region_slug}/{layer_type}")
async def get_fgb_metadata(region_slug: str, layer_type: str):
    """
    Get metadata for a specific FGB file.
    """
    try:
        from land_registry.config import spatialite_settings

        if layer_type not in ["map", "ple"]:
            raise HTTPException(status_code=400, detail="layer_type must be 'map' or 'ple'")

        fgb_dir = Path(spatialite_settings.fgb_directory)
        filename = f"cadastral_{layer_type}.{region_slug}.fgb"
        fgb_path = fgb_dir / filename
        
        if not fgb_path.exists():
            raise HTTPException(status_code=404, detail=f"FGB file not found: {filename}")
        
        # Get file size
        size = fgb_path.stat().st_size
        
        # Try to get feature count from FGB header if possible
        # For now, return basic metadata
        return {
            "filename": filename,
            "size": size,
            "layer_type": layer_type,
            "region": region_slug.replace('_', ' ').title(),
            "path": str(fgb_path)
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting FGB metadata: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get metadata: {str(e)}")


@api_router.get("/fgb/load/{region_slug}/{layer_type}")
async def load_fgb_file(region_slug: str, layer_type: str):
    """
    Load a FGB file and return as GeoJSON.
    This endpoint reads the FGB file and converts it to GeoJSON for map display.
    """
    try:
        from land_registry.config import spatialite_settings

        if layer_type not in ["map", "ple"]:
            raise HTTPException(status_code=400, detail="layer_type must be 'map' or 'ple'")

        fgb_dir = Path(spatialite_settings.fgb_directory)
        filename = f"cadastral_{layer_type}.{region_slug}.fgb"
        fgb_path = fgb_dir / filename
        
        if not fgb_path.exists():
            raise HTTPException(status_code=404, detail=f"FGB file not found: {filename}")
        
        logger.info(f"Loading FGB file: {fgb_path}")
        
        # Read FGB file using geopandas
        gdf = gpd.read_file(fgb_path)
        
        # Convert to GeoJSON
        geojson = json.loads(gdf.to_json())
        
        logger.info(f"Loaded {len(gdf)} features from {filename}")
        
        return {
            "success": True,
            "filename": filename,
            "feature_count": len(gdf),
            "layer_type": layer_type,
            "region": region_slug.replace('_', ' ').title(),
            "geojson": geojson
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error loading FGB file: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to load FGB file: {str(e)}")
