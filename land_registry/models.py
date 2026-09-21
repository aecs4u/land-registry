"""Compatibility exports for the shared land-registry API schemas.

The SQLModel schema definitions are owned by ``aecs4u-domain``. This module
keeps the historical ``land_registry.models`` import path available to the
application and its consumers.
"""

from typing import Any, Literal

from aecs4u_domain.real_estate.land_registry_schemas import (
    CacheMetadataBase,
    CadastralCacheInfoResponseBase,
    CadastralLookupItemBase,
    CadastralLookupResponseBase,
    CadastralStatisticsBase,
    ComuniSearchResponseBase,
    DataBlockBase,
    EnrichmentDatasetStatusBase,
    ErrorResponseBase,
    FileAvailabilityStatsBase,
    FreshnessMetadataBase,
    GeoJSONFeatureBase,
    GeoJSONFeatureCollectionBase,
    HealthResponseBase,
    IngestionManifestBase,
    LineageMetadataBase,
    MicrozoneBulkVisibilityRequestBase,
    MicrozoneCreateRequestBase,
    MicrozoneDetailResponseBase,
    MicrozoneListResponseBase,
    MicrozoneResponseBase,
    MicrozoneUpdateRequestBase,
    SavedParcelCollectionResponseBase,
    SavedParcelCreateRequestBase,
    SavedParcelResponseBase,
    SavedParcelUpdateRequestBase,
    ServiceUnavailableResponseBase,
    TableDataResponseBase,
    ZoneBulkVisibilityRequestBase,
    ZoneCreateRequestBase,
    ZoneDetailResponseBase,
    ZoneListResponseBase,
    ZoneResponseBase,
    ZoneUpdateRequestBase,
)
from aecs4u_domain.real_estate.models import ParcelIdentity, ParcelVersion
from pydantic import BaseModel, ConfigDict, Field as PydanticField, field_validator, model_validator
from sqlmodel import Field


def _compat_schema(name: str, base: type, **namespace: object) -> type:
    """Expose the pre-0.14 schema name while using the domain model as base."""

    namespace.setdefault("__module__", __name__)
    return type(name, (base,), namespace)


# aecs4u-domain 0.14 renamed transport schemas with a ``Base`` suffix so
# applications can extend them. Keep the historical local names and OpenAPI
# component names while inheriting the latest shared definitions.
CacheMetadata = _compat_schema("CacheMetadata", CacheMetadataBase)
CadastralCacheInfoResponse = _compat_schema(
    "CadastralCacheInfoResponse", CadastralCacheInfoResponseBase
)
CadastralLookupItem = _compat_schema("CadastralLookupItem", CadastralLookupItemBase)
CadastralLookupResponse = _compat_schema("CadastralLookupResponse", CadastralLookupResponseBase)
CadastralStatistics = _compat_schema("CadastralStatistics", CadastralStatisticsBase)
ComuniSearchResponse = _compat_schema("ComuniSearchResponse", ComuniSearchResponseBase)
DataBlock = _compat_schema("DataBlock", DataBlockBase)
EnrichmentDatasetStatus = _compat_schema("EnrichmentDatasetStatus", EnrichmentDatasetStatusBase)
ErrorResponse = _compat_schema("ErrorResponse", ErrorResponseBase)
FileAvailabilityStats = _compat_schema("FileAvailabilityStats", FileAvailabilityStatsBase)
FreshnessMetadata = _compat_schema("FreshnessMetadata", FreshnessMetadataBase)
GeoJSONFeature = _compat_schema("GeoJSONFeature", GeoJSONFeatureBase)
GeoJSONFeatureCollection = _compat_schema(
    "GeoJSONFeatureCollection",
    GeoJSONFeatureCollectionBase,
    metadata=property(lambda self: self.metadata_),
)
HealthResponse = _compat_schema("HealthResponse", HealthResponseBase)
IngestionManifest = _compat_schema("IngestionManifest", IngestionManifestBase)
LineageMetadata = _compat_schema("LineageMetadata", LineageMetadataBase)
MicrozoneBulkVisibilityRequest = _compat_schema(
    "MicrozoneBulkVisibilityRequest", MicrozoneBulkVisibilityRequestBase
)
MicrozoneCreateRequest = _compat_schema("MicrozoneCreateRequest", MicrozoneCreateRequestBase)
MicrozoneDetailResponse = _compat_schema("MicrozoneDetailResponse", MicrozoneDetailResponseBase)
MicrozoneListResponse = _compat_schema("MicrozoneListResponse", MicrozoneListResponseBase)
MicrozoneResponse = _compat_schema("MicrozoneResponse", MicrozoneResponseBase)
MicrozoneUpdateRequest = _compat_schema("MicrozoneUpdateRequest", MicrozoneUpdateRequestBase)
def _normalize_saved_parcel_tags(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        raw_tags = value.split(",")
    else:
        raw_tags = list(value)
    tags: list[str] = []
    for tag in raw_tags:
        cleaned = str(tag).strip()[:50]
        if cleaned and cleaned not in tags:
            tags.append(cleaned)
    if len(tags) > 20:
        raise ValueError("Maximum 20 tags allowed")
    return tags


class SavedParcelCreateRequest(SavedParcelCreateRequestBase, table=False):
    """Create request extended from flat favourites to shortlist metadata."""

    status: str | None = Field(default=None, min_length=1, max_length=64)
    priority: int | None = Field(default=None, ge=0, le=5)
    tags: list[str] | None = None

    @field_validator("tags", mode="before")
    @classmethod
    def validate_tags(cls, value):
        return _normalize_saved_parcel_tags(value)


class SavedParcelResponse(SavedParcelResponseBase, table=False):
    """Saved parcel plus shortlist workflow metadata."""

    status: str = "new"
    priority: int | None = None
    tags: list[str] = Field(default_factory=list)
    active_hazard: dict[str, Any] | None = None


class SavedParcelUpdateRequest(SavedParcelUpdateRequestBase, table=False):
    """Mutable shortlist metadata and observed-version fields."""

    status: str | None = Field(default=None, min_length=1, max_length=64)
    priority: int | None = Field(default=None, ge=0, le=5)
    tags: list[str] | None = None

    @field_validator("tags", mode="before")
    @classmethod
    def validate_tags(cls, value):
        return _normalize_saved_parcel_tags(value)

    @model_validator(mode="after")
    def require_update_field(self):
        if (
            self.parcel_version_id is None
            and self.dataset_version is None
            and self.label is None
            and self.notes is None
            and self.status is None
            and self.priority is None
            and self.tags is None
        ):
            raise ValueError("at least one saved-parcel field is required")
        return self


class SavedParcelCollectionResponse(SavedParcelCollectionResponseBase, table=False):
    """Collection response with shortlist configuration and summaries."""

    items: list[SavedParcelResponse]
    status_vocabulary: list[dict[str, Any]] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)


class UserPreferences(BaseModel):
    """Per-user interface and map defaults persisted in ``user_preferences``."""

    # Stored documents may carry keys owned by other features.
    model_config = ConfigDict(extra="ignore")

    language: Literal["it", "en"] | None = None
    default_basemap: Literal["light", "dark", "satellite"] = "light"
    start_view: Literal["italy", "last", "geolocate"] = "italy"
    default_layers: list[str] = PydanticField(default_factory=lambda: ["cadastral-parcels"])
    parcel_labels: bool = True

    @field_validator("default_layers")
    @classmethod
    def normalize_default_layers(cls, value: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(item.strip() for item in value if item and item.strip()))
        if len(cleaned) > 20:
            raise ValueError("at most 20 default layers are allowed")
        return cleaned


ServiceUnavailableResponse = _compat_schema("ServiceUnavailableResponse", ServiceUnavailableResponseBase)
TableDataResponse = _compat_schema("TableDataResponse", TableDataResponseBase)
ZoneBulkVisibilityRequest = _compat_schema("ZoneBulkVisibilityRequest", ZoneBulkVisibilityRequestBase)
ZoneCreateRequest = _compat_schema("ZoneCreateRequest", ZoneCreateRequestBase)
ZoneDetailResponse = _compat_schema("ZoneDetailResponse", ZoneDetailResponseBase)
ZoneListResponse = _compat_schema("ZoneListResponse", ZoneListResponseBase)
ZoneResponse = _compat_schema("ZoneResponse", ZoneResponseBase)
ZoneUpdateRequest = _compat_schema("ZoneUpdateRequest", ZoneUpdateRequestBase)

__all__ = [
    "CacheMetadata",
    "CadastralCacheInfoResponse",
    "CadastralLookupItem",
    "CadastralLookupResponse",
    "CadastralStatistics",
    "ComuniSearchResponse",
    "DataBlock",
    "EnrichmentDatasetStatus",
    "ErrorResponse",
    "FileAvailabilityStats",
    "FreshnessMetadata",
    "GeoJSONFeature",
    "GeoJSONFeatureCollection",
    "HealthResponse",
    "IngestionManifest",
    "LineageMetadata",
    "MicrozoneBulkVisibilityRequest",
    "MicrozoneCreateRequest",
    "MicrozoneDetailResponse",
    "MicrozoneListResponse",
    "MicrozoneResponse",
    "MicrozoneUpdateRequest",
    "ParcelIdentity",
    "ParcelVersion",
    "SavedParcelCollectionResponse",
    "SavedParcelCreateRequest",
    "SavedParcelResponse",
    "SavedParcelUpdateRequest",
    "ServiceUnavailableResponse",
    "TableDataResponse",
    "ZoneBulkVisibilityRequest",
    "ZoneCreateRequest",
    "ZoneDetailResponse",
    "ZoneListResponse",
    "ZoneResponse",
    "ZoneUpdateRequest",
]
