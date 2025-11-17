"""
Pydantic models for API responses.
Ensures consistent response structure and auto-generates OpenAPI documentation.
"""

from typing import List, Any, Optional, Dict
from pydantic import BaseModel, Field


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
