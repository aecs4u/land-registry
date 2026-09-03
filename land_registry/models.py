"""Pydantic transport schemas for the ``land-registry`` API.

Persistent/domain SQLModel classes are owned by ``aecs4u-domain``. The classes in
this module are request/response DTOs used to validate the HTTP contract and
must not be used as a second SQLModel persistence schema.
"""

from datetime import date, datetime, timezone
from typing import Generic, List, Any, Optional, Dict, Literal, TypeVar, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class HealthResponse(BaseModel):
    """Stable response contract for the unauthenticated liveness endpoint."""

    status: Literal["healthy"] = "healthy"
    service: Literal["land-registry"] = "land-registry"


class IngestionManifest(BaseModel):
    """Typed batch-ingestion handoff from an upstream acquisition pipeline."""

    source: str = Field(..., min_length=1)
    source_version: Optional[str] = None
    acquired_at: datetime
    checksum_sha256: str = Field(..., min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$")
    content_type: str = Field(..., min_length=1)
    size_bytes: int = Field(..., ge=0)
    feature_count: Optional[int] = Field(None, ge=0)
    adapter_version: str = Field(..., min_length=1)
    status: Literal["acquired", "validated", "published", "rejected", "quarantined"]
    dataset_version: Optional[str] = None

    @field_validator("acquired_at")
    @classmethod
    def acquired_at_is_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("acquired_at must include a UTC offset")
        return value.astimezone(timezone.utc)


class FreshnessMetadata(BaseModel):
    """Freshness fields shared by dataset status and exported metadata."""

    source_reference_date: Optional[date] = None
    loaded_at: Optional[datetime] = None
    published_at: Optional[datetime] = None
    age_seconds: Optional[float] = Field(None, ge=0)
    freshness_sla_seconds: Optional[int] = Field(None, ge=0)
    stale: Optional[bool] = None

    @field_validator("loaded_at", "published_at")
    @classmethod
    def timestamps_are_timezone_aware(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("timestamps must include a UTC offset")
        return value.astimezone(timezone.utc) if value is not None else None


class EnrichmentDatasetStatus(BaseModel):
    """Availability and freshness metadata for one optional upstream dataset."""

    model_config = ConfigDict(extra="allow")

    available: bool
    source: Optional[str] = "aecs4u-stats"
    dataset: Optional[str] = None
    source_version: Optional[str] = None
    path: Optional[str] = None
    note: Optional[str] = None
    categories: Optional[List[str]] = None
    freshness: FreshnessMetadata = Field(default_factory=FreshnessMetadata)


class LineageMetadata(BaseModel):
    """Provenance and measurement metadata for a data response.

    Geometry returned by the application is GeoJSON in EPSG:4326.  The source
    CRS remains explicit so a consumer can distinguish a transformed result
    from a source-native value.
    """

    source: str = Field(..., min_length=1, description="Owning source or service")
    dataset: Optional[str] = Field(None, description="Dataset or logical table")
    source_version: Optional[str] = Field(None, description="Published source version or snapshot")
    source_reference_date: Optional[date] = Field(None, description="Date represented by the source")
    processing_version: Optional[str] = Field(None, description="Application or pipeline version")
    processed_at: Optional[datetime] = Field(None, description="UTC processing timestamp")
    method: Optional[str] = Field(None, description="Acquisition, derivation, or aggregation method")
    source_crs: Optional[str] = Field(None, description="CRS of the source data, normally an EPSG identifier")
    output_crs: Literal["EPSG:4326"] = Field(default="EPSG:4326", description="CRS of geometry in this response")
    units: Dict[str, str] = Field(default_factory=dict, description="Units by field name")
    confidence: Optional[float] = Field(None, ge=0, le=1, description="Optional normalized confidence score")
    license: Optional[str] = Field(None, description="Data licence or usage restriction")

    @field_validator("processed_at")
    @classmethod
    def processed_at_is_timezone_aware(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("processed_at must include a UTC offset")
        return value.astimezone(timezone.utc) if value is not None else None


DataT = TypeVar("DataT")


class ErrorResponse(BaseModel):
    """Stable machine-readable envelope for documented HTTP errors."""

    detail: Union[str, Dict[str, Any], List[Any]]


class DataBlock(BaseModel, Generic[DataT]):
    """Typed availability envelope used when an enrichment is optional."""

    available: bool
    data: Optional[DataT] = None
    coverage: Literal["full", "partial", "unavailable"] = "full"
    lineage: LineageMetadata

    @model_validator(mode="after")
    def validate_availability(self):
        if not self.available and self.data is not None:
            raise ValueError("unavailable data blocks must have data=null")
        if not self.available and self.coverage != "unavailable":
            raise ValueError("unavailable data blocks must use coverage='unavailable'")
        if self.available and self.coverage == "unavailable":
            raise ValueError("available data blocks cannot use coverage='unavailable'")
        return self


class GeoJSONFeature(BaseModel):
    """GeoJSON feature returned by cadastral and spatial search APIs.

    Geometry coordinates remain JSON because GeoJSON uses different nesting
    for each geometry type. The application contract fixes these coordinates
    to EPSG:4326; measurement properties must declare their units in the
    endpoint schema or lineage metadata.
    """

    type: Literal["Feature"] = "Feature"
    id: Optional[Union[int, str]] = None
    geometry: Optional[Dict[str, Any]] = None
    properties: Dict[str, Any] = Field(default_factory=dict)


class GeoJSONFeatureCollection(BaseModel):
    """GeoJSON FeatureCollection in WGS84 longitude/latitude coordinates."""

    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: List[GeoJSONFeature] = Field(default_factory=list)
    count: Optional[int] = Field(None, ge=0)
    metadata: Optional[LineageMetadata] = Field(
        None,
        description="Optional source, CRS, units, and processing lineage metadata",
    )


class ComuniSearchResponse(BaseModel):
    """Sorted municipality identifiers available in the active dataset."""

    comuni: List[str] = Field(default_factory=list)


class CadastralLookupItem(BaseModel):
    """Normalized item returned by point and zone-overlay cadastral lookups."""

    feature_id: int
    regione: str
    provincia: str
    comune_code: str
    comune_name: Optional[str] = None
    foglio: Optional[int] = None
    particella: Optional[int] = None
    layer_type: str
    label: Optional[str] = None
    national_reference: Optional[str] = None
    relation: Optional[Literal["within", "intersects"]] = None
    geometry: Optional[Dict[str, Any]] = None
    parcel_identity_id: Optional[UUID] = None
    parcel_version_id: Optional[UUID] = None
    dataset_version: Optional[str] = None


class CadastralLookupResponse(BaseModel):
    """Typed response envelope for point and zone-overlay lookups."""

    success: bool = True
    total: int = Field(..., ge=0)
    items: List[CadastralLookupItem]
    metadata: Optional[LineageMetadata] = None


class ParcelIdentity(BaseModel):
    """Stable identity independent of a dataset snapshot or row number."""

    parcel_identity_id: UUID
    source: str = Field(..., min_length=1)
    source_key: str = Field(..., min_length=1)
    national_reference: Optional[str] = None
    source_gml_id: Optional[str] = None


class ParcelVersion(BaseModel):
    """A versioned observation of a parcel identity in one published dataset."""

    parcel_version_id: UUID
    parcel_identity_id: UUID
    dataset_version: str = Field(..., min_length=1)
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None
    status: Literal["active", "superseded", "retired", "unresolved"] = "active"
    lineage: LineageMetadata

    @field_validator("valid_to")
    @classmethod
    def valid_to_is_after_valid_from(cls, value, info):
        valid_from = info.data.get("valid_from")
        if value is not None and valid_from is not None and value < valid_from:
            raise ValueError("valid_to must be on or after valid_from")
        return value


class SavedParcelCreateRequest(BaseModel):
    """Request to save a parcel reference without binding it to a row/FID."""

    source: str = Field(default="cadastral", min_length=1, max_length=100)
    source_key: Optional[str] = Field(None, min_length=1, max_length=512)
    national_reference: Optional[str] = Field(None, min_length=1, max_length=512)
    parcel_identity_id: Optional[UUID] = None
    parcel_version_id: Optional[UUID] = None
    dataset_version: Optional[str] = Field(None, min_length=1, max_length=128)
    label: Optional[str] = Field(None, max_length=255)
    notes: Optional[str] = Field(None, max_length=5000)
    geometry: Optional[Dict[str, Any]] = None

    @model_validator(mode="after")
    def require_parcel_reference(self):
        if not self.parcel_identity_id and not self.national_reference and not self.source_key:
            raise ValueError("parcel_identity_id, national_reference, or source_key is required")
        return self


class SavedParcelUpdateRequest(BaseModel):
    """Mutable user metadata and observed-version fields for a saved parcel."""

    parcel_version_id: Optional[UUID] = None
    dataset_version: Optional[str] = Field(None, min_length=1, max_length=128)
    label: Optional[str] = Field(None, max_length=255)
    notes: Optional[str] = Field(None, max_length=5000)

    @model_validator(mode="after")
    def require_update_field(self):
        if self.parcel_version_id is None and self.dataset_version is None and self.label is None and self.notes is None:
            raise ValueError("at least one saved-parcel field is required")
        return self


class SavedParcelResponse(BaseModel):
    """User-owned saved parcel, resolvable across dataset versions."""

    id: int
    source: str
    source_key: Optional[str] = None
    national_reference: Optional[str] = None
    parcel_identity_id: Optional[UUID] = None
    parcel_version_id: Optional[UUID] = None
    dataset_version: Optional[str] = None
    label: Optional[str] = None
    notes: Optional[str] = None
    geometry: Optional[Dict[str, Any]] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class SavedParcelCollectionResponse(BaseModel):
    """Collection response for the authenticated user's saved parcels."""

    success: bool = True
    total: int
    items: List[SavedParcelResponse]


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
    polygon_type: str = Field(default="polygon", pattern=r"^(polygon|circle|rectangle|marker|polyline)$")
    color: str = Field(default="#3388ff", pattern=r"^#[0-9a-fA-F]{6}$", description="Hex color")
    tags: List[str] = Field(default_factory=list, description="Zone tags/categories")

    @field_validator("geojson")
    @classmethod
    def validate_geojson_feature(cls, v):
        if v.get("type") != "Feature":
            raise ValueError("geojson must be a GeoJSON Feature")
        if "geometry" not in v or v["geometry"] is None:
            raise ValueError("GeoJSON Feature must have a geometry")
        geom_type = v["geometry"].get("type", "")
        valid_types = {
            "Point",
            "MultiPoint",
            "LineString",
            "MultiLineString",
            "Polygon",
            "MultiPolygon",
            "GeometryCollection",
        }
        if geom_type not in valid_types:
            raise ValueError(f"Invalid geometry type: {geom_type}")
        return v

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v):
        if len(v) > 20:
            raise ValueError("Maximum 20 tags allowed")
        return [t.strip()[:50] for t in v if t.strip()]


class ZoneUpdateRequest(BaseModel):
    """Request to update an existing zone."""

    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    color: Optional[str] = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")
    geojson: Optional[Dict[str, Any]] = None
    is_visible: Optional[bool] = None
    tags: Optional[List[str]] = None

    @field_validator("geojson")
    @classmethod
    def validate_geojson_if_provided(cls, v):
        if v is not None:
            if v.get("type") != "Feature":
                raise ValueError("geojson must be a GeoJSON Feature")
            if "geometry" not in v or v["geometry"] is None:
                raise ValueError("GeoJSON Feature must have a geometry")
        return v

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v):
        if v is not None:
            if len(v) > 20:
                raise ValueError("Maximum 20 tags allowed")
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


class MicrozoneBulkVisibilityRequest(BaseModel):
    """Request to set visibility for microzones, optionally filtered by zone IDs."""

    is_visible: bool
    zone_ids: Optional[List[int]] = Field(default=None, max_length=100)


class MicrozoneCreateRequest(BaseModel):
    """Request to create a microzone within a zone."""

    name: str = Field(..., min_length=1, max_length=200, description="Microzone name")
    description: Optional[str] = Field(None, max_length=2000, description="Microzone description")
    geojson: Dict[str, Any] = Field(..., description="GeoJSON Feature object")
    microzone_type: str = Field(default="polygon", pattern=r"^(polygon|circle|rectangle|marker|polyline)$")
    color: str = Field(default="#3388ff", pattern=r"^#[0-9a-fA-F]{6}$", description="Hex color")
    tags: List[str] = Field(default_factory=list, description="Microzone tags/categories")

    @field_validator("geojson")
    @classmethod
    def validate_geojson_feature(cls, v):
        if v.get("type") != "Feature":
            raise ValueError("geojson must be a GeoJSON Feature")
        if "geometry" not in v or v["geometry"] is None:
            raise ValueError("GeoJSON Feature must have a geometry")
        geom_type = v["geometry"].get("type", "")
        valid_types = {
            "Point",
            "MultiPoint",
            "LineString",
            "MultiLineString",
            "Polygon",
            "MultiPolygon",
            "GeometryCollection",
        }
        if geom_type not in valid_types:
            raise ValueError(f"Invalid geometry type: {geom_type}")
        return v

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v):
        if len(v) > 20:
            raise ValueError("Maximum 20 tags allowed")
        return [t.strip()[:50] for t in v if t.strip()]


class MicrozoneUpdateRequest(BaseModel):
    """Request to update an existing microzone."""

    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    color: Optional[str] = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")
    geojson: Optional[Dict[str, Any]] = None
    is_visible: Optional[bool] = None
    tags: Optional[List[str]] = None

    @field_validator("geojson")
    @classmethod
    def validate_geojson_if_provided(cls, v):
        if v is not None:
            if v.get("type") != "Feature":
                raise ValueError("geojson must be a GeoJSON Feature")
            if "geometry" not in v or v["geometry"] is None:
                raise ValueError("GeoJSON Feature must have a geometry")
        return v

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v):
        if v is not None:
            if len(v) > 20:
                raise ValueError("Maximum 20 tags allowed")
            return [t.strip()[:50] for t in v if t.strip()]
        return v


class MicrozoneResponse(BaseModel):
    """Response model for a single microzone."""

    id: int
    zone_id: int
    name: Optional[str] = None
    description: Optional[str] = None
    microzone_type: str = "polygon"
    color: str = "#3388ff"
    area_sqm: Optional[float] = None
    area_km2: Optional[float] = None
    is_large_area: bool = False
    warning_threshold_km2: float = 0.3
    centroid_lat: Optional[float] = None
    centroid_lng: Optional[float] = None
    is_visible: bool = True
    tags: List[str] = Field(default_factory=list)
    created_at: str
    updated_at: str


class MicrozoneDetailResponse(MicrozoneResponse):
    """Full microzone response including geometry."""

    geojson: Dict[str, Any]


class MicrozoneListResponse(BaseModel):
    """Response for listing microzones."""

    success: bool = True
    microzones: List[MicrozoneResponse]
    total: int
