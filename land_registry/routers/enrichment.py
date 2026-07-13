"""
Parcel-enrichment endpoints backed by the ``aecs4u-stats`` package (ISTAT
municipalities/population, OSM POIs, OMI real-estate quotes, MEF/IRPEF income,
seismic/flood/landslide hazards, active fires, criticality bulletin).

Mounted under ``/api/v1/enrichment``. Every endpoint degrades gracefully when
the aecs4u-stats data stores are absent — check ``GET /enrichment/status``.
"""

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from land_registry import stats_service

enrichment_router = APIRouter()


class OmiEstimateRequest(BaseModel):
    """Inputs that identify one published quote and the area to value."""

    comune: str = Field(..., min_length=1, max_length=16)
    zona: str = Field(..., min_length=1, max_length=16)
    cod_tipologia: str = Field(..., min_length=1, max_length=32)
    stato_conservazione: Optional[str] = Field(None, max_length=64)
    area_sqm: float = Field(..., gt=0, le=10_000_000)


@enrichment_router.get("/status")
async def get_enrichment_status():
    """Report which aecs4u-stats datasets are available on this host."""
    return stats_service.enrichment_status()


@enrichment_router.get("/municipality/{cadastral_code}")
async def get_municipality(cadastral_code: str):
    """
    Municipality profile by catasto comune code (e.g. ``C773`` → Civitavecchia):
    ISTAT hierarchy (province, region, NUTS), coordinates, postal code and
    resident-population history.
    """
    if not stats_service.istat_db_available():
        raise HTTPException(
            status_code=503,
            detail="ISTAT reference store not built. Run the aecs4u-stats import pipeline.",
        )
    result = stats_service.get_municipality_by_cadastral_code(cadastral_code)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"No municipality found for cadastral code '{cadastral_code}'",
        )
    return result


@enrichment_router.get("/pois/")
async def get_pois(
    lat: float = Query(..., ge=-90, le=90),
    lng: float = Query(..., ge=-180, le=180),
    radius_km: float = Query(1.0, gt=0, le=25),
    categories: Optional[List[str]] = Query(
        None, description="POI categories (default: all); see /enrichment/status"
    ),
):
    """OSM points of interest around a point, grouped by category, nearest-first."""
    return stats_service.get_pois_near(lat, lng, radius_km=radius_km, categories=categories)


@enrichment_router.get("/omi/quotes")
async def get_omi_quotes(
    comune: str = Query(..., description="Catasto code (e.g. C773) or ISTAT code"),
    zona: Optional[str] = Query(None, description="OMI zone (e.g. B1); default: all zones"),
):
    """OMI sale/rent €/m² quotes for a comune's latest semester, by zone and typology."""
    if not stats_service.omi_db_available_public():
        raise HTTPException(
            status_code=503,
            detail="OMI store not built. Run: python -m aecs4u_stats.omi.scripts.import_omi",
        )
    return stats_service.get_omi_quotes(comune, zona=zona)


@enrichment_router.get("/omi/history")
async def get_omi_history(
    comune: str = Query(..., description="Catasto code (e.g. C773) or ISTAT code"),
    zona: str = Query(..., description="OMI zone (e.g. B1)"),
    cod_tipologia: Optional[str] = Query(None, description="Typology code filter"),
):
    """Full semester history of OMI quotes for one comune/zone (oldest-first)."""
    if not stats_service.omi_db_available_public():
        raise HTTPException(
            status_code=503,
            detail="OMI store not built. Run: python -m aecs4u_stats.omi.scripts.import_omi",
        )
    return stats_service.get_omi_history(comune, zona, cod_tipologia=cod_tipologia)


@enrichment_router.get("/omi/at-point")
async def get_omi_zone_at_point(
    province: str = Query(..., min_length=2, description="Province name used by the OMI boundary store"),
    lat: float = Query(..., ge=-90, le=90),
    lng: float = Query(..., ge=-180, le=180),
):
    """OMI zone containing a point; unmatched/missing boundaries degrade gracefully."""
    return stats_service.get_omi_zone_at_point(province, lat, lng)


@enrichment_router.post("/omi/estimate")
async def estimate_omi_value(request: OmiEstimateRequest):
    """Compute an auditable indicative range from one exact OMI quote row."""
    if not stats_service.omi_db_available_public():
        raise HTTPException(
            status_code=503,
            detail="OMI store not built. Run: python -m aecs4u_stats.omi.scripts.import_omi",
        )
    result = stats_service.estimate_omi_value(
        comune=request.comune,
        zona=request.zona,
        cod_tipologia=request.cod_tipologia,
        stato_conservazione=request.stato_conservazione,
        area_sqm=request.area_sqm,
    )
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="No unique OMI quote matches the requested zone, typology and conservation state",
        )
    return result


@enrichment_router.get("/income/{cadastral_code}")
async def get_income(cadastral_code: str, year: Optional[int] = Query(None)):
    """MEF/IRPEF income profile: taxpayer count, mean taxable income, income-bracket distribution."""
    if not stats_service.mef_db_available_public():
        raise HTTPException(
            status_code=503,
            detail="MEF/IRPEF store not built. Run: python -m aecs4u_stats.mef.scripts.import_irpef --year <YYYY>",
        )
    result = stats_service.get_income_profile(cadastral_code, year=year)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No IRPEF data found for '{cadastral_code}'")
    return result


@enrichment_router.get("/risks/{istat_code}")
async def get_risks(istat_code: str):
    """
    Environmental risk profile: DPC seismic zone (local store) plus ISPRA
    IdroGEO flood/landslide indicators (live API).
    """
    result = stats_service.get_environmental_risks(istat_code)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"No hazard data found for ISTAT code '{istat_code}' (seismic store missing and IdroGEO unreachable/unknown code)",
        )
    return result


@enrichment_router.get("/fires")
async def get_fires(
    lat: Optional[float] = Query(None, ge=-90, le=90),
    lng: Optional[float] = Query(None, ge=-180, le=180),
    radius_km: float = Query(50.0, gt=0, le=500),
):
    """
    Active-fire detections (NASA FIRMS) near a point, or nationwide when no
    point is given. Requires FIRMS_MAP_KEY on the server.
    """
    return stats_service.get_active_fires(radius_km=radius_km, lat=lat, lng=lng)


@enrichment_router.get("/bulletin")
async def get_bulletin():
    """Latest Protezione Civile hydro-criticality (allerta meteo) bulletin."""
    result = stats_service.get_criticality_bulletin()
    if result is None:
        raise HTTPException(status_code=503, detail="Protezione Civile bulletin unreachable")
    return result


_CADASTRAL_BUILD_HINT = (
    "Cadastral parcel store not built. Run: "
    "python -m aecs4u_stats.cadastral.scripts.import_cadastral --regione <REGIONE>"
)


@enrichment_router.get("/parcels/in-bbox/")
async def get_parcels_in_bbox(
    min_lng: float = Query(..., ge=-180, le=180),
    min_lat: float = Query(..., ge=-90, le=90),
    max_lng: float = Query(..., ge=-180, le=180),
    max_lat: float = Query(..., ge=-90, le=90),
    limit: int = Query(5000, gt=0, le=20000),
    include_geometry: bool = Query(True),
):
    """Parcels intersecting a WGS84 bounding box, across all built region stores."""
    if not stats_service.cadastral_store_available():
        raise HTTPException(status_code=503, detail=_CADASTRAL_BUILD_HINT)
    return stats_service.get_parcels_in_bbox(
        min_lng, min_lat, max_lng, max_lat, limit=limit, include_geometry=include_geometry,
    )


@enrichment_router.get("/parcels/{comune_code}")
async def get_parcels(
    comune_code: str,
    foglio: Optional[str] = Query(None, description="Sheet label, e.g. 75 or 75A"),
    particella: Optional[str] = Query(None, description="Parcel label, e.g. 63 or STRADA304"),
    limit: int = Query(1000, gt=0, le=10000),
    offset: int = Query(0, ge=0),
    include_geometry: bool = Query(True),
):
    """Cadastral parcels of a comune as a GeoJSON FeatureCollection
    (per-region DuckDB stores built from the AdE INSPIRE extracts)."""
    if not stats_service.cadastral_store_available():
        raise HTTPException(status_code=503, detail=_CADASTRAL_BUILD_HINT)
    return stats_service.get_parcels(
        comune_code, foglio=foglio, particella=particella,
        limit=limit, offset=offset, include_geometry=include_geometry,
    )


@enrichment_router.get("/fogli/{comune_code}")
async def get_fogli(comune_code: str):
    """Sheet (foglio) list for a comune with per-sheet parcel counts."""
    if not stats_service.cadastral_store_available():
        raise HTTPException(status_code=503, detail=_CADASTRAL_BUILD_HINT)
    return stats_service.get_fogli(comune_code)


@enrichment_router.get("/parcel/by-reference/{national_reference}")
async def get_parcel_by_reference(national_reference: str):
    """One parcel Feature by exact NATIONALCADASTRALREFERENCE (e.g. H233_000100.1)."""
    if not stats_service.cadastral_store_available():
        raise HTTPException(status_code=503, detail=_CADASTRAL_BUILD_HINT)
    result = stats_service.get_parcel_by_reference(national_reference)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No parcel with reference '{national_reference}'")
    return result


@enrichment_router.get("/parcel/at-point")
async def get_parcel_at_point(
    lat: float = Query(..., ge=-90, le=90),
    lng: float = Query(..., ge=-180, le=180),
):
    """The cadastral parcel containing a WGS84 point (click → parcel lookup)."""
    if not stats_service.cadastral_store_available():
        raise HTTPException(status_code=503, detail=_CADASTRAL_BUILD_HINT)
    result = stats_service.get_parcel_at_point(lat, lng)
    if result is None:
        raise HTTPException(status_code=404, detail="No parcel at this point (region store missing or open water)")
    return result


_CENSUS_BUILD_HINT = (
    "Census-sections store not built. Run: "
    "python -m aecs4u_stats.census.scripts.import_census_sections --all"
)


@enrichment_router.get("/census/at-point")
async def get_census_section_at_point(
    lat: float = Query(..., ge=-90, le=90),
    lng: float = Query(..., ge=-180, le=180),
):
    """The 2021 census section containing a WGS84 point.

    Keep this static route above ``/census/{cadastral_code}`` so Starlette
    does not interpret ``at-point`` as a cadastral code.
    """
    if not stats_service.census_db_available():
        raise HTTPException(status_code=503, detail=_CENSUS_BUILD_HINT)
    result = stats_service.get_census_section_at_point(lat, lng)
    if result is None:
        raise HTTPException(status_code=404, detail="No census section at this point")
    return result


@enrichment_router.get("/census/{cadastral_code}")
async def get_census_sections(cadastral_code: str, limit: int = Query(5000, gt=0, le=20000)):
    """2021 ISTAT census sections covering a comune, as a GeoJSON
    FeatureCollection — population by age/sex, education, employment,
    foreign-resident, household-size and dwelling-occupancy indicators,
    plus derived rates, per section."""
    if not stats_service.census_db_available():
        raise HTTPException(status_code=503, detail=_CENSUS_BUILD_HINT)
    result = stats_service.get_census_sections(cadastral_code, limit=limit)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No municipality found for cadastral code '{cadastral_code}'")
    return result


@enrichment_router.get("/crime/{cadastral_code}")
async def get_crime(cadastral_code: str, year: Optional[int] = Query(None)):
    """Reported-crime (delitti denunciati) profile for a comune's province
    (ISTAT publishes this at province, not comune, granularity)."""
    if not stats_service.safety_db_available():
        raise HTTPException(
            status_code=503,
            detail="ISTAT safety/crime store not built. Download with `aecs4u-stats istat datasets download`.",
        )
    result = stats_service.get_crime_profile(cadastral_code, year=year)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No crime data found for '{cadastral_code}'")
    return result


@enrichment_router.get("/quality-of-life/{cadastral_code}")
async def get_quality_of_life(cadastral_code: str):
    """BES territorial quality-of-life indicator codes available for a
    comune's province."""
    if not stats_service.bes_db_available():
        raise HTTPException(
            status_code=503,
            detail="ISTAT BES store not built. Download with `aecs4u-stats istat datasets download`.",
        )
    result = stats_service.get_quality_of_life_indicators(cadastral_code)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No BES data found for '{cadastral_code}'")
    return result


@enrichment_router.get("/quality-of-life/{cadastral_code}/{data_type}")
async def get_quality_of_life_series(cadastral_code: str, data_type: str, year: Optional[int] = Query(None)):
    """Time series for one BES indicator (see ``/quality-of-life/{cadastral_code}``
    for available codes)."""
    if not stats_service.bes_db_available():
        raise HTTPException(
            status_code=503,
            detail="ISTAT BES store not built. Download with `aecs4u-stats istat datasets download`.",
        )
    result = stats_service.get_quality_of_life_indicator(cadastral_code, data_type, year=year)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No BES data for '{data_type}' at '{cadastral_code}'")
    return result


@enrichment_router.get("/demographics/{cadastral_code}")
async def get_demographics(cadastral_code: str):
    """ISTAT demographic indicator codes available for a comune's province."""
    if not stats_service.demographic_db_available():
        raise HTTPException(
            status_code=503,
            detail="ISTAT demographic-indicators store not built. Download with `aecs4u-stats istat datasets download`.",
        )
    result = stats_service.get_demographic_indicators(cadastral_code)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No demographic data found for '{cadastral_code}'")
    return result


@enrichment_router.get("/demographics/{cadastral_code}/{data_type}")
async def get_demographics_series(cadastral_code: str, data_type: str, year: Optional[int] = Query(None)):
    """Time series for one demographic indicator (see
    ``/demographics/{cadastral_code}`` for available codes)."""
    if not stats_service.demographic_db_available():
        raise HTTPException(
            status_code=503,
            detail="ISTAT demographic-indicators store not built. Download with `aecs4u-stats istat datasets download`.",
        )
    result = stats_service.get_demographic_indicator(cadastral_code, data_type, year=year)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No demographic data for '{data_type}' at '{cadastral_code}'")
    return result
