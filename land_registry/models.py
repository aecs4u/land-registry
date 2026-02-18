"""
Pydantic models for API responses.
Ensures consistent response structure and auto-generates OpenAPI documentation.
"""

from typing import List, Any, Optional, Dict
from pydantic import BaseModel, Field, field_validator


class TableDataResponse(BaseModel):
    """Response model for paginated table data"""
    data: List[Dict[str, Any]] = Field(..., description="Table rows")
    total: int = Field(..., description="Total number of records (before pagination)")
    page: int = Field(..., description="Current page number")
    size: int = Field(..., description="Page size")
    total_pages: int = Field(..., description="Total number of pages")
    columns: List[str] = Field(..., description="Column names")
    filtered_total: Optional[int] = Field(None, description="Total after filtering (if different from total)")


class CacheMetadata(BaseModel):
    """Cache metadata for cadastral data"""
    loaded_at: float = Field(..., description="Unix timestamp when data was loaded")
    age_seconds: float = Field(..., description="Age of cached data in seconds")
    source: str = Field(..., description="Data source: 'local', 's3', or 'json'")
    ttl_seconds: int = Field(..., description="Cache TTL in seconds")
    is_expired: bool = Field(..., description="Whether cache has expired")


class CadastralStatistics(BaseModel):
    """Statistics about cadastral data structure"""
    total_regions: int = Field(..., description="Number of regions")
    total_provinces: int = Field(..., description="Number of provinces")
    total_municipalities: int = Field(..., description="Number of municipalities")
    total_files: int = Field(..., description="Total number of cadastral files")


class FileAvailabilityStats(BaseModel):
    """Statistics about file availability across municipalities"""
    municipalities_with_files: int = Field(..., description="Municipalities with files")
    municipalities_without_files: int = Field(..., description="Municipalities without files")
    total_municipalities: int = Field(..., description="Total municipalities")
    coverage_percentage: float = Field(..., description="Percentage of municipalities with files")


class CadastralCacheInfoResponse(BaseModel):
    """Response for /api/v1/cadastral-cache-info endpoint"""
    cache: CacheMetadata = Field(..., description="Cache metadata")
    statistics: CadastralStatistics = Field(..., description="Cadastral statistics")
    file_availability: FileAvailabilityStats = Field(..., description="File availability statistics")


class ServiceUnavailableResponse(BaseModel):
    """Response for 503 Service Unavailable"""
    detail: str = Field(..., description="Error message")
    feature: str = Field(..., description="Feature name that is unavailable")
    status: str = Field(default="not_implemented", description="Feature status")
    expected_availability: Optional[str] = Field(None, description="Expected availability timeline")


# ============================================================================
# Zone Management Models
# ============================================================================

class ZoneCreateRequest(BaseModel):
    """Request to create a new zone from a drawn geometry."""
    name: str = Field(..., min_length=1, max_length=200, description="Zone name")
    description: Optional[str] = Field(None, max_length=2000, description="Zone description")
    geojson: Dict[str, Any] = Field(..., description="GeoJSON Feature object")
    polygon_type: str = Field(
        default="polygon",
        pattern=r"^(polygon|circle|rectangle|marker|polyline)$"
    )
    color: str = Field(default="#3388ff", pattern=r"^#[0-9a-fA-F]{6}$", description="Hex color")
    tags: List[str] = Field(default_factory=list, description="Zone tags/categories")

    @field_validator('geojson')
    @classmethod
    def validate_geojson_feature(cls, v):
        if v.get('type') != 'Feature':
            raise ValueError('geojson must be a GeoJSON Feature')
        if 'geometry' not in v or v['geometry'] is None:
            raise ValueError('GeoJSON Feature must have a geometry')
        geom_type = v['geometry'].get('type', '')
        valid_types = {'Point', 'MultiPoint', 'LineString', 'MultiLineString',
                       'Polygon', 'MultiPolygon', 'GeometryCollection'}
        if geom_type not in valid_types:
            raise ValueError(f'Invalid geometry type: {geom_type}')
        return v

    @field_validator('tags')
    @classmethod
    def validate_tags(cls, v):
        if len(v) > 20:
            raise ValueError('Maximum 20 tags allowed')
        return [t.strip()[:50] for t in v if t.strip()]


class ZoneUpdateRequest(BaseModel):
    """Request to update an existing zone."""
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    color: Optional[str] = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")
    geojson: Optional[Dict[str, Any]] = None
    is_visible: Optional[bool] = None
    tags: Optional[List[str]] = None

    @field_validator('geojson')
    @classmethod
    def validate_geojson_if_provided(cls, v):
        if v is not None:
            if v.get('type') != 'Feature':
                raise ValueError('geojson must be a GeoJSON Feature')
            if 'geometry' not in v or v['geometry'] is None:
                raise ValueError('GeoJSON Feature must have a geometry')
        return v

    @field_validator('tags')
    @classmethod
    def validate_tags(cls, v):
        if v is not None:
            if len(v) > 20:
                raise ValueError('Maximum 20 tags allowed')
            return [t.strip()[:50] for t in v if t.strip()]
        return v


class ZoneResponse(BaseModel):
    """Response model for a single zone (without geometry for list performance)."""
    id: int
    name: Optional[str] = None
    description: Optional[str] = None
    polygon_type: str = "polygon"
    color: str = "#3388ff"
    area_sqm: Optional[float] = None
    centroid_lat: Optional[float] = None
    centroid_lng: Optional[float] = None
    is_visible: bool = True
    tags: List[str] = Field(default_factory=list)
    created_at: str
    updated_at: str


class ZoneDetailResponse(ZoneResponse):
    """Full zone response including geometry."""
    geojson: Dict[str, Any]


class ZoneListResponse(BaseModel):
    """Response for listing zones."""
    success: bool = True
    zones: List[ZoneResponse]
    total: int


class ZoneBulkVisibilityRequest(BaseModel):
    """Request to set visibility for multiple zones."""
    zone_ids: List[int] = Field(..., min_length=1, max_length=100)
    is_visible: bool
