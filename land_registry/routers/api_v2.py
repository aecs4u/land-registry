"""Versioned public parcel lookup endpoints."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict

from land_registry import stats_service
from land_registry.map_layers import get_map_layer_source
from land_registry.source_catalog import (
    PARCEL_API_FIELD_CATALOG,
    SOURCE_CATALOG,
    find_source_catalog_entry,
)
from land_registry.stats_service import CanonicalParcelLookupError
from land_registry.routers.api_keys import ApiKeySummary, require_parcel_api_key

router = APIRouter()
logger = logging.getLogger(__name__)
_ENRICHMENT_SECTION_ORDER = tuple(
    field.section
    for field in PARCEL_API_FIELD_CATALOG
    if field.section not in {"basic", "geometry", "risk", "coastal_erosion"}
)
_ENRICHMENT_SECTIONS = frozenset(_ENRICHMENT_SECTION_ORDER)
_PARCEL_SECTIONS = frozenset(field.section for field in PARCEL_API_FIELD_CATALOG)
_SECTION_SOURCES = {
    "cadastral": "Agenzia delle Entrate / ISTAT",
    "population": "ISTAT Permanent Census 2021",
    "demographics": "ISTAT Permanent Census 2021",
    "economics": "MEF/IRPEF via aecs4u-stats",
    "buildings": "SISTER cadastral property records via aecs4u-stats",
    "address": "SISTER cadastral property records via aecs4u-stats",
    "anncsu_addresses": "ANNCSU via aecs4u-stats",
    "opendata": "aecs4u-stats OpenData source",
    "pvp": "Portale Vendite Pubbliche via aecs4u-stats",
    "solar": "aecs4u-stats municipality solar aggregates",
    "valuation": "Agenzia delle Entrate OMI via aecs4u-stats",
    "nightlights": "NASA Black Marble VIIRS VNP46A4",
    "terrain": "INGV TINITALY 1.1 digital terrain model",
    "land_cover": "Copernicus Land Monitoring Service — CORINE Land Cover 2018",
    "coastal_erosion": "ISPRA Dinamica Litoranea 2006-2020",
}


class ParcelApiFieldProvenance(BaseModel):
    """Field-level provider mapping for sections assembled from mixed sources."""

    source: str
    source_key: str | None = None
    match_method: str | None = None
    spatial_resolution: str | None = None


class ParcelApiSection(BaseModel):
    """Stable section envelope; ``data`` retains provider-specific fields."""

    model_config = ConfigDict(extra="forbid")

    available: bool | None = None
    data: Any = None
    source: str | None = None
    source_key: str | None = None
    field_provenance: dict[str, "ParcelApiFieldProvenance"] | None = None
    source_url: str | None = None
    dataset_version: Any = None
    data_vintage: Any = None
    model_version: Any = None
    updated_at: Any = None
    match_method: str | None = None
    match_distance_m: float | None = None
    coverage: str | None = None
    coverage_status: str | None = None
    confidence: Any = None
    spatial_resolution: str | None = None
    spatial_resolution_m: float | None = None
    benchmarks: Any = None
    license: str | None = None
    license_basis: str | None = None
    attribution: str | None = None
    terms_url: str | None = None
    reuse_terms: str | None = None
    reason: str | None = None
    crs: str | None = None


_CADASTRAL_FIELD_PROVENANCE = {
    "foglio": {
        "source": "Agenzia delle Entrate INSPIRE cadastral extract",
        "source_key": "agenzia_entrate_cadastre",
        "match_method": "national_cadastral_reference",
        "spatial_resolution": "parcel",
    },
    "sezione_urbana": {
        "source": "Agenzia delle Entrate INSPIRE cadastral extract",
        "source_key": "agenzia_entrate_cadastre",
        "match_method": "national_cadastral_reference",
        "spatial_resolution": "parcel",
    },
    "comune_code": {
        "source": "Agenzia delle Entrate INSPIRE cadastral extract",
        "source_key": "agenzia_entrate_cadastre",
        "match_method": "national_cadastral_reference",
        "spatial_resolution": "parcel",
    },
    "postal_code": {
        "source": "ISTAT municipality crosswalk",
        "source_key": "istat",
        "match_method": "cadastral_municipality_crosswalk",
        "spatial_resolution": "municipality",
    },
    "postgres_parcel_spine_available": {
        "source": "aecs4u-stats parcel-spine readiness",
        "match_method": "application_store_availability",
        "spatial_resolution": "application store",
    },
}


class ParcelApiResponse(BaseModel):
    """Response envelope for the public stable-feature-ID lookup."""

    model_config = ConfigDict(extra="forbid")

    fid: int
    reference: str | None = None
    sections: dict[str, ParcelApiSection]


class ParcelApiCatalogSource(BaseModel):
    key: str
    display_name: str
    provider_url: str
    license_name: str | None = None
    license_basis: str
    terms_url: str | None = None
    citation: str | None = None
    reuse_terms: str | None = None
    attribution_text: str | None = None


class ParcelApiCatalogSection(BaseModel):
    section: str
    fields: str
    source_keys: list[str]
    provider_note: str
    method_scope: str
    version_freshness: str
    data_shape: str
    stable_data_fields: list[str]
    provider_dynamic_fields: bool
    data_notes: str
    stable_field_descriptions: dict[str, str]
    sources: list[ParcelApiCatalogSource]


class ParcelApiCatalogResponse(BaseModel):
    api_version: str
    endpoint: str
    default_include: list[str]
    sections: list[ParcelApiCatalogSection]
    sources: list[ParcelApiCatalogSource]


def _public_enrichment_section(block: dict) -> dict:
    """Keep the established block contract, including unavailable coverage."""
    fields = (
        "available", "data", "source", "source_url", "dataset_version",
        "data_vintage", "model_version", "updated_at", "match_method",
        "match_distance_m", "coverage", "coverage_status", "confidence",
        "spatial_resolution", "spatial_resolution_m", "benchmarks",
        "license", "license_basis", "attribution", "terms_url", "reuse_terms", "reason",
    )
    section = {name: block.get(name) for name in fields if name in block}
    if section.get("available") is False and not section.get("reason"):
        section["reason"] = "source_unavailable"
    return _add_catalog_provenance(section, block.get("source"))


def _add_catalog_provenance(section: dict, source_label: object) -> dict:
    """Add provider metadata only when the runtime source label is unambiguous."""
    label = str(source_label or "").casefold()
    catalog_entry = find_source_catalog_entry(source_label)
    if catalog_entry is None:
        return section
    if any(separator in label for separator in (" / ", " and ", " or ", "multiple providers")):
        # A compound label is only catalogued when the exact aggregate itself
        # has a maintained entry; do not infer one provider's terms for an
        # arbitrary multi-provider response.
        exact_aliases = {alias.casefold() for alias in catalog_entry.aliases}
        if label not in exact_aliases:
            return section
    # The OpenData adapter may draw from SISTER or from a different store. A
    # SISTER substring alone is not enough to assert its access terms.
    if catalog_entry.key == "sister" and "opendata" in label:
        return section
    section["source_key"] = catalog_entry.key
    if not section.get("source_url"):
        section["source_url"] = catalog_entry.provider_url
    if not section.get("license"):
        section["license"] = catalog_entry.license_name
    if not section.get("license_basis"):
        section["license_basis"] = catalog_entry.license_basis
    if not section.get("attribution"):
        section["attribution"] = catalog_entry.attribution_text or catalog_entry.citation
    if not section.get("terms_url"):
        section["terms_url"] = catalog_entry.terms_url
    if not section.get("reuse_terms"):
        section["reuse_terms"] = catalog_entry.reuse_terms
    return section


def _unavailable_enrichment_section(section: str, reason: str) -> dict:
    unavailable = {
        "available": False,
        "data": None,
        "source": _SECTION_SOURCES[section],
        "dataset_version": None,
        "coverage": "unavailable",
        "coverage_status": "unavailable",
        "reason": reason,
    }
    return _add_catalog_provenance(unavailable, unavailable["source"])


@router.get(
    "/catalog",
    response_model=ParcelApiCatalogResponse,
    summary="Get the parcel API field and source catalog",
    description=(
        "Returns the supported parcel sections, stable data fields, provider-defined "
        "field behavior, matching methods, spatial resolution, freshness notes and "
        "source reuse metadata. This documentation endpoint is public and does not "
        "consume an API key quota."
    ),
)
async def get_parcel_api_catalog() -> dict:
    """Publish the maintained field contract and provider reuse metadata."""
    source_by_key = {entry.key: entry for entry in SOURCE_CATALOG}

    def public_source(source) -> dict:
        return {
            "key": source.key,
            "display_name": source.display_name,
            "provider_url": source.provider_url,
            "license_name": source.license_name,
            "license_basis": source.license_basis,
            "terms_url": source.terms_url,
            "citation": source.citation,
            "reuse_terms": source.reuse_terms,
            "attribution_text": source.attribution_text,
        }

    sections = []
    for field in PARCEL_API_FIELD_CATALOG:
        section = asdict(field)
        section["stable_field_descriptions"] = dict(field.stable_field_descriptions)
        section["sources"] = [
            public_source(source_by_key[key])
            for key in field.source_keys
            if key in source_by_key
        ]
        sections.append(section)
    sources = [public_source(source) for source in SOURCE_CATALOG]
    return {
        "api_version": "2",
        "endpoint": "/api/v2/parcels/{fid}",
        "default_include": ["basic", "geometry"],
        "sections": sections,
        "sources": sources,
    }


async def _parcel_seismic_component(reference: str | None) -> dict:
    """Resolve the existing DPC municipality classification for a parcel."""
    match = re.match(r"^([A-Z]\d{3})", str(reference or "").strip().upper())
    if not match:
        return {"available": False, "reason": "cadastral_municipality_code_unavailable"}
    try:
        municipality = await stats_service.aget_municipality_by_cadastral_code(match.group(1))
        istat_code = (municipality or {}).get("istat_code")
        if not istat_code:
            return {"available": False, "reason": "istat_municipality_code_unavailable"}
    except Exception:
        logger.warning("Version 2 seismic lookup failed for cadastral code %s", match.group(1), exc_info=True)
        return {"available": False, "reason": "seismic_lookup_failed"}
    try:
        result = await stats_service.aget_seismic_by_istat_code(istat_code)
    except Exception:
        logger.warning("Version 2 DPC lookup failed for ISTAT code %s", istat_code, exc_info=True)
        result = None
    if result is None:
        component = {"available": False, "reason": "seismic_source_unavailable"}
    else:
        component = _add_catalog_provenance({
            "available": True,
            "data": result,
            "source": "Dipartimento della Protezione Civile municipal seismic classification",
            "dataset_version": result.get("dataset_version") if isinstance(result, dict) else None,
            "spatial_resolution": "municipality",
            "match_method": "cadastral_municipality_to_istat_municipality",
            "coverage": "full",
        }, "Dipartimento della Protezione Civile municipal seismic classification")
    if getattr(stats_service, "_composite_risk_score", None) is not None:
        try:
            municipal_risks = await asyncio.wait_for(
                asyncio.to_thread(stats_service.get_environmental_risks, istat_code),
                timeout=22,
            )
        except asyncio.TimeoutError:
            municipal_risks = None
        except Exception:
            logger.warning("Version 2 municipal composite risk lookup failed", exc_info=True)
            municipal_risks = None
        if isinstance(municipal_risks, dict):
            component.update({
                "composite_score": municipal_risks.get("composite_score"),
                "composite_score_version": municipal_risks.get("composite_score_version"),
                "composite_score_status": municipal_risks.get("composite_score_status"),
                "composite_score_components": municipal_risks.get("composite_score_components"),
                "composite_score_missing_components": municipal_risks.get(
                    "composite_score_missing_components"
                ),
                "composite_score_invalid_components": municipal_risks.get(
                    "composite_score_invalid_components"
                ),
                "composite_score_component_count": municipal_risks.get(
                    "composite_score_component_count"
                ),
                "composite_score_component_total": municipal_risks.get(
                    "composite_score_component_total"
                ),
                "composite_score_formula": municipal_risks.get("composite_score_formula"),
                "composite_score_interpretation": municipal_risks.get(
                    "composite_score_interpretation"
                ),
            })
    return component


async def _parcel_mps04_component(geometry: dict | None) -> dict:
    """Return INGV PGA at the parcel centroid with its nearest-grid method."""
    source_name = "INGV MPS04 seismic hazard model"
    base = {
        "available": False,
        "data": None,
        "source": source_name,
        "model_version": "MPS04",
        "match_method": "nearest_grid_point",
        "spatial_resolution": "0.05° native grid; nearest grid point",
        "coverage": "unavailable",
        "coverage_status": "unavailable",
    }
    def unavailable(reason: str) -> dict:
        return _add_catalog_provenance({**base, "reason": reason}, source_name)

    if not geometry:
        return unavailable("parcel_geometry_unavailable")
    try:
        point = stats_service._parcel_centroid({"geometry": geometry})
    except Exception:
        logger.warning("Version 2 MPS04 parcel centroid failed", exc_info=True)
        return unavailable("parcel_centroid_lookup_failed")
    if not point:
        return unavailable("parcel_centroid_unavailable")
    try:
        result = await asyncio.wait_for(
            stats_service.aget_mps04_pga_at_point(point["lat"], point["lng"]),
            timeout=12,
        )
    except asyncio.TimeoutError:
        return unavailable("mps04_lookup_timed_out")
    except Exception:
        logger.warning("Version 2 MPS04 lookup failed", exc_info=True)
        return unavailable("mps04_lookup_failed")
    if not isinstance(result, dict):
        return unavailable("mps04_source_unavailable")

    available = result.get("available") is True
    matched = result.get("matched") is True
    coverage = "matched" if matched else "no_match" if available else "unavailable"
    component = {
        **base,
        "available": available,
        "matched": result.get("matched") is True if available else None,
        "data": result if available else None,
        "source_url": result.get("source_url"),
        "dataset_version": result.get("dataset_version"),
        "model_version": result.get("model_version") or "MPS04",
        "match_method": result.get("match_method") or "nearest_grid_point",
        "match_distance_m": result.get("distance_m"),
        "spatial_resolution": result.get("spatial_resolution") or base["spatial_resolution"],
        "coverage": coverage,
        "coverage_status": coverage,
        "license": result.get("license"),
        "license_basis": result.get("license_basis"),
        "attribution": result.get("attribution"),
        "terms_url": result.get("terms_url"),
        "reason": result.get("reason"),
    }
    return _add_catalog_provenance(component, result.get("source") or source_name)


async def _parcel_risk_component(name: str, lookup, geometry: dict) -> dict:
    """Keep one provider failure from hiding the other parcel risk components."""
    try:
        result = await asyncio.wait_for(lookup(geometry), timeout=25)
    except asyncio.TimeoutError:
        return {"available": False, "reason": f"{name}_lookup_timed_out"}
    except Exception:
        logger.warning("Version 2 %s lookup failed", name, exc_info=True)
        return {"available": False, "reason": f"{name}_lookup_failed"}
    if not isinstance(result, dict):
        return {"available": False, "reason": f"{name}_source_unavailable"}
    if result.get("available") is False and not result.get("reason"):
        result = {**result, "reason": f"{name}_source_unavailable"}
    return _add_catalog_provenance(result, result.get("source"))


async def _parcel_protection_component(geometry: dict | None) -> dict:
    """Return the gated constraint lookup without implying complete coverage."""
    if not geometry:
        return {"available": False, "coverage_status": "unavailable", "reason": "parcel_geometry_unavailable"}
    component = await _parcel_risk_component(
        "protection_constraints",
        lambda parcel_geometry: get_map_layer_source().read_protection_constraints(parcel_geometry),
        geometry,
    )
    published = component.get("status") == "published"
    coverage = component.get("coverage") or {}
    coverage_status = (
        "full"
        if published and all(coverage.get(kind) == "covered" for kind in ("landscape", "cultural_heritage"))
        else "partial" if published else "unavailable"
    )
    component["available"] = published
    component["coverage_status"] = coverage_status
    return component


@router.get(
    "/parcels/{fid}",
    response_model=ParcelApiResponse,
    response_model_exclude_unset=True,
    summary="Get a cadastral parcel by stable feature ID",
    description=(
        "Returns cadastral identity, optional WGS84 geometry, DPC municipality "
        "seismic classification, INGV MPS04 nearest-grid PGA, parcel hazard "
        "intersections, and "
        "available read-model sections including demographics, economics, "
        "buildings, SISTER addresses, geocoded ANNCSU civic addresses, solar aggregates and valuation. Risk includes "
        "optional locally provisioned 2006–2020 coastal-change and protection-constraint components, each with "
        "its own availability and coverage state, and "
        "remains partial until the shared composite score is available. "
        "Use the comma-separated include parameter to request only the sections "
        "needed by the client; unavailable sections retain their coverage reason. "
        "Supply a Clerk-account API key with the parcels:read scope in X-API-Key. "
        "Each key has a shared 10,000-request UTC-hour quota."
    ),
    responses={
        200: {
            "description": "Parcel identity and requested section envelopes.",
            "content": {
                "application/json": {
                    "example": {
                        "fid": 42,
                        "reference": "H501A048600.E",
                        "sections": {
                            "basic": {
                                "data": {
                                    "national_cadastral_reference": "H501A048600.E",
                                    "comune_name": "Roma",
                                },
                                "source": "Agenzia delle Entrate INSPIRE cadastral extract",
                                "source_key": "agenzia_entrate_cadastre",
                                "dataset_version": "2025",
                            },
                            "geometry": {
                                "data": {
                                    "type": "Polygon",
                                    "coordinates": [[
                                        [12.49, 41.89],
                                        [12.51, 41.89],
                                        [12.51, 41.91],
                                        [12.49, 41.91],
                                        [12.49, 41.89],
                                    ]],
                                },
                                "crs": "EPSG:4326",
                                "source": "Agenzia delle Entrate INSPIRE cadastral extract",
                                "source_key": "agenzia_entrate_cadastre",
                            },
                        },
                    }
                }
            },
        },
        404: {"description": "No parcel has this feature ID."},
        401: {"description": "An active X-API-Key is required."},
        403: {"description": "The API key lacks the parcels:read scope."},
        429: {"description": "The API key's shared hourly quota is exhausted."},
        422: {"description": "The feature ID or include parameter is invalid."},
        503: {"description": "The canonical source or a required lookup is unavailable."},
    },
)
async def get_parcel_v2(
    api_key: ApiKeySummary = Depends(require_parcel_api_key),
    fid: int = Path(..., ge=1, description="Stable cadastral map feature ID"),
    include: str = Query(
        "basic,geometry",
        description=f"Comma-separated sections: {','.join(sorted(_PARCEL_SECTIONS))}",
    ),
) -> dict:
    requested = {part.strip().lower() for part in include.split(",") if part.strip()}
    unknown = requested - _PARCEL_SECTIONS
    if not requested or unknown:
        allowed = ", ".join(sorted(_PARCEL_SECTIONS))
        raise HTTPException(status_code=422, detail=f"include must contain only: {allowed}")

    source = get_map_layer_source()
    if not source.available:
        raise HTTPException(status_code=503, detail="Canonical PostGIS map source unavailable")
    try:
        feature = await asyncio.wait_for(source.read_parcel_by_id(fid), timeout=8)
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=503, detail="Parcel lookup timed out") from exc
    except Exception as exc:
        logger.warning("Version 2 parcel lookup failed for %s: %s", fid, exc)
        raise HTTPException(status_code=503, detail="Parcel lookup is temporarily unavailable") from exc
    if feature is None:
        raise HTTPException(status_code=404, detail="Parcel not found")

    properties = feature.get("properties") or {}
    reference = properties.get("national_cadastral_reference") or properties.get("canonical_reference")
    dataset_version = properties.get("source_release")
    sections: dict[str, dict] = {}
    if "basic" in requested:
        sections["basic"] = _add_catalog_provenance({
            "data": properties,
            "source": "Agenzia delle Entrate INSPIRE cadastral extract",
            "dataset_version": dataset_version,
        }, "Agenzia delle Entrate INSPIRE cadastral extract")
    if "geometry" in requested:
        sections["geometry"] = _add_catalog_provenance({
            "data": feature.get("geometry"),
            "crs": "EPSG:4326",
            "source": "Agenzia delle Entrate INSPIRE cadastral extract",
            "dataset_version": dataset_version,
        }, "Agenzia delle Entrate INSPIRE cadastral extract")
    enrichment = None
    requested_enrichment = requested & _ENRICHMENT_SECTIONS
    if requested_enrichment:
        if reference:
            try:
                enrichment = await asyncio.wait_for(
                    stats_service.aget_parcel_enrichment(
                        reference,
                        view="full",
                        feature_id=fid,
                        requested_blocks=requested_enrichment,
                    ),
                    timeout=25,
                )
            except asyncio.TimeoutError as exc:
                raise HTTPException(status_code=503, detail="Parcel enrichment lookup timed out") from exc
            except CanonicalParcelLookupError as exc:
                raise HTTPException(status_code=503, detail="Canonical parcel source unavailable for enrichment") from exc
            except Exception as exc:
                logger.warning("Version 2 parcel enrichment lookup failed for %s: %s", fid, exc)
                raise HTTPException(status_code=503, detail="Parcel enrichment is temporarily unavailable") from exc

        enrichment_blocks = (enrichment or {}).get("blocks")
        for section in _ENRICHMENT_SECTION_ORDER:
            if section not in requested_enrichment:
                continue
            block = enrichment_blocks.get(section) if isinstance(enrichment_blocks, dict) else None
            if isinstance(block, dict):
                public_section = _public_enrichment_section(block)
                if section == "cadastral":
                    public_section["field_provenance"] = _CADASTRAL_FIELD_PROVENANCE
                sections[section] = public_section
            else:
                reason = "parcel_reference_unavailable" if not reference else "parcel_enrichment_unavailable"
                sections[section] = _unavailable_enrichment_section(section, reason)
    if "coastal_erosion" in requested:
        coastal = (
            await _parcel_risk_component(
                "coastal_erosion",
                stats_service.aget_parcel_coastal_erosion,
                feature["geometry"],
            )
            if feature.get("geometry")
            else {"available": False, "reason": "parcel_geometry_unavailable"}
        )
        sections["coastal_erosion"] = _public_enrichment_section({
            "available": coastal.get("available"),
            "data": coastal,
            "source": coastal.get("source", _SECTION_SOURCES["coastal_erosion"]),
            "source_url": coastal.get("source_url"),
            "dataset_version": coastal.get("dataset_version"),
            "data_vintage": coastal.get("data_vintage"),
            "updated_at": coastal.get("updated_at"),
            "match_method": coastal.get("match_method"),
            "coverage": coastal.get("coverage"),
            "coverage_status": coastal.get("coverage_status"),
            "spatial_resolution": coastal.get("spatial_resolution"),
            "license": coastal.get("license"),
            "attribution": coastal.get("attribution"),
            "reason": coastal.get("reason"),
        })
    if "risk" in requested:
        geometry = feature.get("geometry")
        if not geometry:
            hazard_data = {"available": False, "reason": "parcel_geometry_unavailable"}
            subsidence_data = {"available": False, "reason": "parcel_geometry_unavailable"}
            coastal_data = {"available": False, "reason": "parcel_geometry_unavailable"}
            mps04_data = await _parcel_mps04_component(None)
            protection_data = await _parcel_protection_component(None)
        else:
            hazard_data, subsidence_data, coastal_data, mps04_data, protection_data = await asyncio.gather(
                _parcel_risk_component(
                    "parcel_hazard", stats_service.aget_parcel_hazard_intersections, geometry
                ),
                _parcel_risk_component(
                    "subsidence", stats_service.aget_parcel_subsidence_intersections, geometry
                ),
                _parcel_risk_component(
                    "coastal_erosion", stats_service.aget_parcel_coastal_erosion, geometry
                ),
                _parcel_mps04_component(geometry),
                _parcel_protection_component(geometry),
            )
        try:
            seismic_data = await asyncio.wait_for(
                _parcel_seismic_component(reference), timeout=25
            )
        except asyncio.TimeoutError:
            seismic_data = {"available": False, "reason": "seismic_lookup_timed_out"}
        risk_components = {
            "seismic_zone": seismic_data,
            "mps04_pga": mps04_data,
            "parcel_hazards": hazard_data,
            "subsidence": subsidence_data,
            "coastal_erosion": coastal_data,
            "protection_constraints": protection_data,
        }
        component_available = any(item.get("available") is True for item in risk_components.values()) or (
            seismic_data.get("composite_score") is not None
        )
        risk_data = {
            **risk_components,
            "composite_score": seismic_data.get("composite_score"),
            "composite_score_version": seismic_data.get("composite_score_version"),
            "composite_score_status": seismic_data.get("composite_score_status"),
            "composite_score_components": seismic_data.get("composite_score_components"),
            "composite_score_missing_components": seismic_data.get(
                "composite_score_missing_components"
            ),
            "composite_score_invalid_components": seismic_data.get(
                "composite_score_invalid_components"
            ),
            "composite_score_component_count": seismic_data.get(
                "composite_score_component_count"
            ),
            "composite_score_component_total": seismic_data.get(
                "composite_score_component_total"
            ),
            "composite_score_formula": seismic_data.get("composite_score_formula"),
            "composite_score_interpretation": seismic_data.get("composite_score_interpretation"),
            "missing_components": [
                *[
                    name for name, item in risk_components.items()
                    if item.get("available") is not True or item.get("matched") is False
                ],
                *(["composite_score"] if seismic_data.get("composite_score") is None else []),
            ],
        }
        sections["risk"] = {
            "available": component_available,
            "data": risk_data if component_available else None,
            "source": "Multiple providers; see component-level provenance",
            "dataset_version": None,
            "coverage": "partial" if component_available else "unavailable",
            "coverage_status": "partial" if component_available else "unavailable",
            **({} if component_available else {"reason": "parcel_risk_sources_unavailable"}),
        }
    return {"fid": feature.get("id", fid), "reference": reference, "sections": sections}
