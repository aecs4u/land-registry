"""
Parcel-enrichment endpoints backed by the ``aecs4u-stats`` package (ISTAT
municipalities/population, OSM POIs, OMI real-estate quotes, MEF/IRPEF income,
seismic/flood/landslide hazards, active fires, criticality bulletin).

Mounted under ``/api/v1/enrichment``. Every endpoint degrades gracefully when
the aecs4u-stats data stores are absent — check ``GET /enrichment/status``.
"""

import asyncio
import io
import json
import time
import uuid
from typing import Annotated, Dict, List, Literal, Optional

from aecs4u_stats.osm.config import POI_CATEGORIES
from aecs4u_stats.web import enrichment as _aecs4u_stats_enrichment
from fastapi import APIRouter, HTTPException, Path, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field, ValidationError

from land_registry import stats_service
from land_registry.map_layers import get_map_layer_source
from land_registry.models import EnrichmentDatasetStatus

enrichment_router = APIRouter()


class ParcelReportSection(BaseModel):
    id: str = Field(..., min_length=1, max_length=64)
    title: str = Field(..., min_length=1, max_length=120)
    state: Literal["ready", "empty", "error", "idle", "loading"]
    body: str = Field("", max_length=18_000)
    metadata: dict[str, str] = Field(default_factory=dict, max_length=16)


class ParcelReportSnapshot(BaseModel):
    sections: list[ParcelReportSection] = Field(..., min_length=1, max_length=32)


_REPORT_SECTION_IDS = {
    "identity", "omi", "pvp", "address", "buildings", "opendata", "risks", "parcel-hazards",
    "agenziademanio", "solar", "subsidence", "bulletin", "fires", "municipality", "income",
    "census", "safety", "demographics", "quality", "pois", "coverage",
}
_MAX_REPORT_SNAPSHOT_BYTES = 240_000


async def _parse_parcel_report_snapshot(request: Request) -> ParcelReportSnapshot:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > _MAX_REPORT_SNAPSHOT_BYTES:
                raise HTTPException(status_code=413, detail="Parcel report snapshot is too large")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Content-Length") from None
    chunks = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > _MAX_REPORT_SNAPSHOT_BYTES:
            raise HTTPException(status_code=413, detail="Parcel report snapshot is too large")
        chunks.append(chunk)
    try:
        payload = json.loads(b"".join(chunks))
        snapshot = ParcelReportSnapshot.model_validate(payload)
    except (json.JSONDecodeError, UnicodeDecodeError, ValidationError, TypeError) as exc:
        raise HTTPException(status_code=422, detail="Invalid parcel report snapshot") from exc

    ids = [section.id for section in snapshot.sections]
    if len(ids) != len(set(ids)) or any(section_id not in _REPORT_SECTION_IDS for section_id in ids):
        raise HTTPException(status_code=422, detail="Unknown or duplicate parcel report section")
    total = sum(len(section.body) for section in snapshot.sections)
    for section in snapshot.sections:
        if any(len(key) > 64 or len(value) > 500 for key, value in section.metadata.items()):
            raise HTTPException(status_code=422, detail="Parcel report metadata is too long")
    if total > 200_000:
        raise HTTPException(status_code=413, detail="Parcel report section text is too large")
    return snapshot


@enrichment_router.get("/bulletin")
async def get_bulletin() -> dict:
    result = await asyncio.to_thread(stats_service.get_criticality_bulletin)
    if result is None:
        raise HTTPException(status_code=503, detail="No stored Civil Protection bulletin available")
    return result


# Reuse upstream handlers for endpoints with identical behavior. The bulletin
# uses the local adapter above because its source is now hazards PostgreSQL.
enrichment_router.add_api_route("/fires", _aecs4u_stats_enrichment.get_fires, methods=["GET"])


async def _get_risks_with_postgres_fallback(istat_code: str):
    """Keep upstream hydrogeological data and fill seismic from PostgreSQL."""
    try:
        result = await _aecs4u_stats_enrichment.get_risks(istat_code)
    except Exception:
        result = None
    if not isinstance(result, dict):
        result = {"istat_code": str(istat_code), "seismic": None, "hydrogeological": None}
    if not result.get("seismic"):
        result["seismic"] = await stats_service.aget_seismic_by_istat_code(istat_code)
    return result


async def _get_quality_of_life(cadastral_code: str):
    if not await stats_service.aquality_of_life_db_available():
        raise HTTPException(status_code=503, detail="ISTAT BES store not built")
    result = await stats_service.aget_quality_of_life_indicators(cadastral_code)
    if result is None:
        raise HTTPException(status_code=404, detail="No BES data found")
    return result


async def _get_quality_of_life_series(
    cadastral_code: str, data_type: str, year: Optional[int] = None
):
    if not await stats_service.aquality_of_life_db_available():
        raise HTTPException(status_code=503, detail="ISTAT BES store not built")
    result = await stats_service.aget_quality_of_life_indicator(
        cadastral_code, data_type, year=year
    )
    if result is None:
        raise HTTPException(status_code=404, detail="No BES data found")
    return result


enrichment_router.add_api_route(
    "/risks/{istat_code}", _get_risks_with_postgres_fallback, methods=["GET"]
)
enrichment_router.add_api_route(
    "/parcels/in-bbox/", _aecs4u_stats_enrichment.get_parcels_in_bbox, methods=["GET"]
)
enrichment_router.add_api_route(
    "/parcels/{comune_code}", _aecs4u_stats_enrichment.get_parcels, methods=["GET"]
)
enrichment_router.add_api_route(
    "/fogli/{comune_code}", _aecs4u_stats_enrichment.get_fogli, methods=["GET"]
)
enrichment_router.add_api_route(
    "/crime/{cadastral_code}", _aecs4u_stats_enrichment.get_crime, methods=["GET"]
)
enrichment_router.add_api_route(
    "/quality-of-life/{cadastral_code}",
    _get_quality_of_life,
    methods=["GET"],
)
enrichment_router.add_api_route(
    "/quality-of-life/{cadastral_code}/{data_type}",
    _get_quality_of_life_series,
    methods=["GET"],
)
enrichment_router.add_api_route(
    "/demographics/{cadastral_code}", _aecs4u_stats_enrichment.get_demographics, methods=["GET"]
)
enrichment_router.add_api_route(
    "/demographics/{cadastral_code}/{data_type}",
    _aecs4u_stats_enrichment.get_demographics_series,
    methods=["GET"],
)


class OmiEstimateRequest(BaseModel):
    """Inputs that identify one published quote and the area to value."""

    comune: str = Field(..., min_length=1, max_length=16)
    zona: str = Field(..., min_length=1, max_length=16)
    cod_tipologia: str = Field(..., min_length=1, max_length=32)
    stato_conservazione: Optional[str] = Field(None, max_length=64)
    area_sqm: float = Field(..., gt=0, le=10_000_000)


# ``enrichment_status`` probes every store (file and database checks that can
# take many seconds on a host with missing stores). Run it off the event loop
# and reuse the answer briefly so concurrent callers do not repeat the probes.
_STATUS_CACHE_SECONDS = 60.0
_status_cache: Optional[tuple] = None  # (producer, monotonic time, payload)
_status_lock = asyncio.Lock()


@enrichment_router.get("/status", response_model=Dict[str, EnrichmentDatasetStatus])
async def get_enrichment_status():
    """Report which aecs4u-stats datasets are available on this host.

    The report is computed in a worker thread and cached for
    ``_STATUS_CACHE_SECONDS``, so a slow probe never blocks other requests.
    """
    global _status_cache
    producer = stats_service.enrichment_status
    cached = _status_cache
    now = time.monotonic()
    if cached and cached[0] is producer and now - cached[1] < _STATUS_CACHE_SECONDS:
        return cached[2]
    async with _status_lock:
        cached = _status_cache
        now = time.monotonic()
        if cached and cached[0] is producer and now - cached[1] < _STATUS_CACHE_SECONDS:
            return cached[2]
        payload = await asyncio.to_thread(producer)
        _status_cache = (producer, time.monotonic(), payload)
        return payload


@enrichment_router.get("/municipality/{cadastral_code}")
async def get_municipality(cadastral_code: str):
    """
    Municipality profile by catasto comune code (e.g. ``C773`` → Civitavecchia):
    ISTAT hierarchy (province, region, NUTS), coordinates, postal code and
    resident-population history.
    """
    if not (stats_service.istat_db_available() or stats_service.postgres_stats_available()):
        raise HTTPException(
            status_code=503,
            detail="ISTAT reference store not built. Run the aecs4u-stats import pipeline.",
        )
    result = await stats_service.aget_municipality_by_cadastral_code(cadastral_code)
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
    categories: Optional[List[str]] = Query(None, description="POI categories (default: all); see /enrichment/status"),
):
    """OSM points of interest around a point, grouped by category, nearest-first.

    Accepts both repeated ``categories`` params and the comma-separated form
    the map and POI explorer send; a single ``"a,b"`` value would otherwise be
    matched as one unknown category code and return nothing.
    """
    if categories:
        categories = [item.strip() for value in categories for item in value.split(",") if item.strip()]
    return await stats_service.aget_pois_near(
        lat, lng, radius_km=radius_km, categories=categories or None
    )


@enrichment_router.get("/poi-categories")
async def get_poi_categories():
    """Return the category catalogue used by the POI layer and explorer."""
    return [
        {"key": key, "label": key.replace("_", " ").title(), "query": query}
        for key, query in POI_CATEGORIES.items()
    ]


def _poi_category_query(value: Optional[str]) -> Optional[List[str]]:
    if not value:
        return None
    values = [item.strip() for item in value.split(",") if item.strip()]
    if values == ["__none__"]:
        return []
    return [item for item in values if item in POI_CATEGORIES]


@enrichment_router.get("/poi-report")
async def get_poi_report(
    lat: float = Query(..., ge=-90, le=90),
    lng: float = Query(..., ge=-180, le=180),
    radius_km: float = Query(2.0, gt=0, le=25),
    categories: Optional[str] = Query(None),
    format: str = Query("json", pattern="^(json|pdf)$"),
):
    """Export the current POI Explorer result as JSON or a compact PDF."""
    data = await stats_service.aget_pois_near(
        lat, lng, radius_km=radius_km, categories=_poi_category_query(categories)
    )
    if format == "json":
        return JSONResponse(data, headers={"Content-Disposition": "attachment; filename=poi-explorer.json"})
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
        output = io.BytesIO()
        pdf = canvas.Canvas(output, pagesize=A4)
        _, height = A4
        pdf.setTitle("POI Explorer")
        pdf.drawString(42, height - 48, f"POI Explorer — {lat:.5f}, {lng:.5f} — radius {radius_km:g} km")
        y = height - 76
        for category, items in (data.get("categories") or {}).items():
            for item in items:
                if y < 48:
                    pdf.showPage()
                    y = height - 48
                name = str(item.get("name") or category)[:100]
                distance = item.get("distance_km")
                suffix = f" ({distance:g} km)" if isinstance(distance, (int, float)) else ""
                pdf.drawString(48, y, f"{category}: {name}{suffix}")
                y -= 14
        pdf.save()
        return Response(output.getvalue(), media_type="application/pdf", headers={"Content-Disposition": "attachment; filename=poi-explorer.pdf"})
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"PDF export unavailable: {exc}") from exc


@enrichment_router.get("/poi-map", response_class=HTMLResponse)
async def get_poi_map(
    lat: float = Query(..., ge=-90, le=90),
    lng: float = Query(..., ge=-180, le=180),
    radius_km: float = Query(2.0, gt=0, le=25),
    categories: Optional[str] = Query(None),
):
    """Small embeddable Leaflet POI map used by the direct-map explorer."""
    data = await stats_service.aget_pois_near(
        lat, lng, radius_km=radius_km, categories=_poi_category_query(categories)
    )
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return HTMLResponse(f"""<!doctype html><html><head><meta charset='utf-8'><title>POI Explorer</title>
<link rel='stylesheet' href='https://unpkg.com/leaflet@1.9.4/dist/leaflet.css'></head>
<body style='margin:0'><div id='map' style='height:100vh'></div>
<script src='https://unpkg.com/leaflet@1.9.4/dist/leaflet.js'></script><script>
const data={payload}; const map=L.map('map').setView([{lat},{lng}], 13);
L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',{{attribution:'© OpenStreetMap contributors'}}).addTo(map);
L.circle([{lat},{lng}],{{radius:{radius_km}*1000,color:'#2563eb',fillOpacity:.06}}).addTo(map);
Object.entries(data.categories||{{}}).forEach(([category,items])=>items.forEach(item=>{{if(item.lat==null||item.lng==null)return;L.marker([item.lat,item.lng]).addTo(map).bindPopup('<b>'+String(item.name||category).replace(/[<>&]/g,'')+'</b><br>'+category);}}));
</script></body></html>""")


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
    return await stats_service.aget_omi_quotes(comune, zona=zona)


### None of /omi/history, /omi/at-point, /omi/estimate delegate, despite
### looking like pure duplicates of aecs4u_stats.web.enrichment's versions:
###
### - get_omi_history resolves the comune to an exact, pre-computed
###   region-prefixed cod_comune_istat integer via _omi_istat_keys() and
###   filters on that (falling back to aecs4u_stats.omi.queries.quote_history
###   only when the key can't be resolved) — a deliberate optimization
###   (see _fast_omi_quotes's docstring) to hit a covering index on the
###   multi-gigabyte quotazioni_valori table; upstream's quote_history()
###   applies UPPER()/CAST() to the filter columns, which prevents that same
###   index from being used. Delegating would trade a real perf
###   characteristic for no behavioral gain.
### - get_omi_zone_at_point and estimate_omi_value: aecs4u_stats's zone-code
###   robustness and estimate's disclaimer field were both ported upstream
###   (see docs/AECS4U_STATS_CONSOLIDATION_PLAN.md), but
###   tests/test_omi_spatial_join_contract.py and
###   tests/test_omi_server_estimate_contract.py directly monkeypatch
###   `enrichment_module.stats_service.get_omi_zone_at_point` /
###   `.estimate_omi_value` (this module's own land_registry.stats_service
###   reference) and construct `enrichment_module.OmiEstimateRequest`
###   directly — pinned contracts a router-level swap would silently break
###   without a coordinated test rewrite, out of scope for this pass.


@enrichment_router.get("/omi/history")
async def get_omi_history(
    comune: str = Query(..., description="Catasto code (e.g. C773) or ISTAT code"),
    zona: str = Query(..., description="OMI zone (e.g. B1)"),
    cod_tipologia: Optional[str] = Query(None, description="Typology code filter"),
    stato_conservazione: Optional[str] = Query(None, description="Conservation state filter"),
):
    """Full semester history of OMI quotes for one comune/zone (oldest-first)."""
    if not stats_service.omi_db_available_public():
        raise HTTPException(
            status_code=503,
            detail="OMI store not built. Run: python -m aecs4u_stats.omi.scripts.import_omi",
        )
    return stats_service.get_omi_history(
        comune,
        zona,
        cod_tipologia=cod_tipologia,
        stato_conservazione=stato_conservazione,
    )


@enrichment_router.get("/omi/at-point")
async def get_omi_zone_at_point(
    province: str = Query(..., min_length=2, description="Province name used by the OMI boundary store"),
    lat: float = Query(..., ge=-90, le=90),
    lng: float = Query(..., ge=-180, le=180),
    comune: Optional[str] = Query(None, description="Cadastral municipality code when known"),
):
    """OMI zone containing a point; unmatched/missing boundaries degrade gracefully."""
    result = await stats_service.aget_omi_zone_at_point(
        province, lat, lng, cadastral_code=comune
    )
    if result is not None:
        return result
    return await asyncio.to_thread(stats_service.get_omi_zone_at_point, province, lat, lng)


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
        result = await stats_service.aestimate_omi_value(
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
    try:
        income = await stats_service.aget_income_profile(request.comune)
    except Exception:
        income = None
    try:
        mean_income = float((income or {}).get("mean_taxable_income_eur"))
        quote = result.get("quote") or {}
        price_midpoint = (
            float(quote.get("prezzo_min_eur_sqm")) + float(quote.get("prezzo_max_eur_sqm"))
        ) / 2
        price_to_income = (
            request.area_sqm * price_midpoint / mean_income
            if mean_income > 0 and price_midpoint >= 0
            else None
        )
    except (TypeError, ValueError):
        price_to_income = None
    result["derived_metrics"] = {
        "price_to_mean_taxable_income_years": round(price_to_income, 1) if price_to_income is not None else None,
        "price_to_income_model_version": "omi-mef-price-income-v1",
        "income_year": (income or {}).get("year"),
        "income_dataset_version": (income or {}).get("dataset_version"),
        "interpretation": "indicative property-value / one mean municipal taxpayer's annual taxable income; not household affordability",
    }
    return result


@enrichment_router.get("/income/{cadastral_code}")
async def get_income(cadastral_code: str, year: Optional[int] = Query(None)):
    """MEF/IRPEF income profile: taxpayer count, mean taxable income, income-bracket distribution."""
    if not stats_service.mef_db_available_public():
        raise HTTPException(
            status_code=503,
            detail="MEF/IRPEF store not built. Run: python -m aecs4u_stats.mef.scripts.import_irpef --year <YYYY>",
        )
    result = await stats_service.aget_income_profile(cadastral_code, year=year)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No IRPEF data found for '{cadastral_code}'")
    return result


@enrichment_router.get("/egms/subsidence")
async def get_egms_subsidence(
    lat: float = Query(..., ge=-90, le=90),
    lng: float = Query(..., ge=-180, le=180),
):
    """EGMS vertical-motion summary for the 100 m cell covering a point."""
    return await asyncio.to_thread(stats_service.get_subsidence_at_point, lat, lng)


@enrichment_router.get("/mps04/pga")
async def get_mps04_pga(
    lat: float = Query(..., ge=-90, le=90),
    lng: float = Query(..., ge=-180, le=180),
):
    """INGV MPS04 PGA at the nearest native grid point to a parcel centroid."""
    return await asyncio.to_thread(stats_service.get_mps04_pga_at_point, lat, lng)


_CADASTRAL_BUILD_HINT = (
    "Cadastral parcel store not built. Run: "
    "python -m aecs4u_stats.cadastral.scripts.import_cadastral --regione <REGIONE>"
)


@enrichment_router.get("/parcel/by-reference/{national_reference}")
async def get_parcel_by_reference(
    national_reference: str,
    id: Annotated[Optional[int], Query(ge=1, description="Canonical feature id hint from the clicked vector tile")] = None,
):
    """One parcel Feature by exact NATIONALCADASTRALREFERENCE (e.g. H233_000100.1).

    Map clicks pass the vector-tile feature ``id`` so the canonical source can
    resolve the parcel by primary key instead of scanning the unindexed
    reference columns.
    """
    local_available = stats_service.cadastral_store_available()
    source = get_map_layer_source()
    result = None
    if id is not None:
        if source.available:
            try:
                result = await source.read_feature_by_reference(
                    "cadastral-parcels", national_reference, feature_id=id
                )
            except Exception as exc:
                raise HTTPException(status_code=503, detail="Canonical PostGIS map source unavailable") from exc
    elif source.available:
        try:
            matches = await source.search_parcels_by_reference(national_reference, limit=2)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Canonical PostGIS map source unavailable") from exc
        if len(matches) > 1:
            raise HTTPException(status_code=409, detail="This reference has multiple parcel polygons. Choose a specific map feature.")
        if matches:
            result = await source.read_feature_by_reference(
                "cadastral-parcels", national_reference, feature_id=int(matches[0]["id"])
            )
    if result is None and id is None and local_available:
        result = stats_service.get_parcel_by_reference(national_reference)
    if result is None and (id is not None or not local_available) and not source.available:
        raise HTTPException(status_code=503, detail=_CADASTRAL_BUILD_HINT)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No parcel with reference '{national_reference}'")
    return result


@enrichment_router.get("/parcel/hazards/{national_reference}")
async def get_parcel_hazards(
    national_reference: str,
    id: Annotated[Optional[int], Query(ge=1, description="Canonical feature id hint from the selected parcel")] = None,
):
    """Intersect a canonical parcel polygon with the installed ISPRA mosaics."""
    source = get_map_layer_source()
    parcel = await get_parcel_by_reference(national_reference, id=id if source.available else None)
    geometry = parcel.get("geometry") if isinstance(parcel, dict) else None
    if not geometry:
        raise HTTPException(status_code=404, detail="Parcel geometry is not available")
    return await asyncio.to_thread(stats_service.get_parcel_hazard_intersections, geometry)


@enrichment_router.get("/parcel/subsidence/{national_reference}")
async def get_parcel_subsidence(
    national_reference: str,
    id: Annotated[Optional[int], Query(ge=1, description="Canonical feature id hint from the selected parcel")] = None,
):
    """Summarize EGMS grid cells intersecting the canonical parcel polygon."""
    source = get_map_layer_source()
    parcel = await get_parcel_by_reference(national_reference, id=id if source.available else None)
    geometry = parcel.get("geometry") if isinstance(parcel, dict) else None
    if not geometry:
        raise HTTPException(status_code=404, detail="Parcel geometry is not available")
    return await asyncio.to_thread(stats_service.get_parcel_subsidence_intersections, geometry)


@enrichment_router.get("/parcel/report/{national_reference}")
async def get_parcel_report(
    national_reference: str,
    request: Request,
    id: Annotated[Optional[int], Query(ge=1, description="Canonical feature id from the selected parcel")] = None,
):
    """Download a server-rendered parcel dossier with a stable report ID."""
    return await _build_parcel_report(national_reference, request, id=id)


@enrichment_router.post("/parcel/report/{national_reference}")
async def post_parcel_report(
    national_reference: str,
    request: Request,
    id: Annotated[Optional[int], Query(ge=1, description="Canonical feature id from the selected parcel")] = None,
):
    """Download a parcel dossier including the independently loaded browser sections."""
    snapshot = await _parse_parcel_report_snapshot(request)
    return await _build_parcel_report(national_reference, request, id=id, sections=[
        section.model_dump() for section in snapshot.sections
    ])


async def _build_parcel_report(
    national_reference: str,
    request: Request,
    *,
    id: Optional[int] = None,
    sections: list[dict] | None = None,
):
    """Render a report using server identity/read-model data and optional panel text."""
    from land_registry.i18n import detect_locale, make_gettext
    from land_registry.parcel_report import profile_matches_parcel, render_parcel_report_pdf

    parcel = await get_parcel_by_reference(national_reference, id=id)
    try:
        profile = await stats_service.aget_parcel_enrichment(national_reference)
    except Exception:
        profile = None
    if not profile_matches_parcel(parcel, profile):
        profile = None

    report_id = f"PR-{uuid.uuid4().hex[:12].upper()}"
    pdf_bytes = await asyncio.to_thread(
        render_parcel_report_pdf,
        national_reference,
        parcel,
        profile,
        report_id,
        make_gettext(detect_locale(request)),
        sections,
    )
    return Response(
        pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'attachment; filename="parcel-report.pdf"',
            "X-Report-ID": report_id,
        },
    )


@enrichment_router.get("/parcel/adjacent/{national_reference}")
async def get_adjacent_parcels(
    national_reference: str,
    limit: int = Query(25, ge=1, le=50),
    id: Annotated[Optional[int], Query(ge=1, description="Canonical feature id of the target parcel")] = None,
):
    """Parcels that touch or overlap the parcel identified by its national reference.

    Canonical-source equivalent of the legacy "Find Adjacent" spatial analysis
    (folium-interface.js), scoped to one parcel instead of a multi-select.
    """
    source = get_map_layer_source()
    if not source.available:
        raise HTTPException(status_code=503, detail=_CADASTRAL_BUILD_HINT)
    try:
        features = await source.read_adjacent_features(
            "cadastral-parcels", national_reference, limit=limit, feature_id=id
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Canonical PostGIS map source unavailable") from exc
    return {"type": "FeatureCollection", "features": features}


@enrichment_router.get("/parcel/details/{national_reference}")
async def get_parcel_details(
    national_reference: str,
    refresh: bool = Query(False, description="Rebuild the parcel read-model row from source stores"),
    view: Literal["full", "panel"] = Query("full", description="Return the complete legacy payload or the compact primary-map panel model"),
):
    """Read the parcel-keyed, materialized enrichment profile.

    The first request builds the row from the cadastral, ISTAT census, and OMI
    stores. Later requests use one indexed SQLite lookup, keeping the parcel
    details panel independent of the latency of the source databases.
    """
    if view == "panel":
        result = await stats_service.aget_parcel_enrichment(national_reference, refresh, view="panel")
    else:
        result = await stats_service.aget_parcel_enrichment(national_reference, refresh)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No enrichment data found for '{national_reference}'")
    return result


@enrichment_router.get("/parcel/buildings/{national_reference}")
async def get_parcel_buildings(national_reference: str):
    """Return parcel-linked SISTER building categories and cached addresses."""
    return stats_service.get_buildings_for_parcel(national_reference)


@enrichment_router.get("/parcel/agenziademanio/{parcel_id}")
async def get_parcel_agenziademanio_concessions(
    parcel_id: Annotated[int, Path(ge=1, description="Canonical map ID of the selected parcel")],
):
    """Return Agenzia Demanio concessions spatially matched to this parcel."""
    return await stats_service.aget_agenziademanio_concessions_for_parcel(parcel_id)


def _parcel_reference_from_query(municipality: str, sheet: str, parcel: str) -> str:
    """Build the canonical parcel key from explicit cadastral components."""
    return f"{municipality.strip().upper()}_{sheet.strip()}.{parcel.strip()}"


async def _get_parcel_external(
    lookup,
    source: str,
    municipality: str,
    sheet: str,
    parcel: str,
    *,
    lookup_context: Optional[Dict[str, str]] = None,
):
    """Run one external parcel lookup with a bounded response time."""
    national_reference = _parcel_reference_from_query(municipality, sheet, parcel)
    # The municipality is part of the explicit API identity. Forward it to
    # the source adapter instead of making the adapter infer it from the local
    # cache; otherwise a missing cache could weaken the source-side filter to
    # sheet+parcel alone.
    lookup_municipality = lookup_context or {"cadastral_code": municipality.strip().upper()}
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(lookup, national_reference, lookup_municipality),
            timeout=10,
        )
    except asyncio.TimeoutError:
        return {"records": [], "count": 0, "available": False, "source": source, "error": "lookup_timeout"}
    except Exception:
        return {"records": [], "count": 0, "available": False, "source": source, "error": "lookup_failed"}


@enrichment_router.get("/parcel/opendata")
async def get_parcel_opendata(
    municipality_code: str = Query(..., min_length=1, max_length=16),
    sheet: str = Query(..., min_length=1, max_length=32),
    parcel: str = Query(..., min_length=1, max_length=32),
):
    """Return OpenData records for a cadastral municipality_code, sheet, and parcel."""
    return await _get_parcel_external(
        stats_service.get_opendata_for_parcel,
        "OpenData PostgreSQL",
        municipality_code,
        sheet,
        parcel,
    )


@enrichment_router.get("/parcel/pvp")
async def get_parcel_pvp(
    municipality_code: str = Query(..., min_length=1, max_length=16),
    sheet: str = Query(..., min_length=1, max_length=32),
    parcel: str = Query(..., min_length=1, max_length=32),
):
    """Return PVP records for a PVP municipality_code, sheet, and parcel."""
    return await _get_parcel_external(
        stats_service.get_pvp_for_parcel,
        "PVP modelview PostgreSQL",
        municipality_code,
        sheet,
        parcel,
        lookup_context={
            "pvp_municipality_code": municipality_code.strip(),
        },
    )


@enrichment_router.get("/parcel/at-point")
async def get_parcel_at_point(
    lat: float = Query(..., ge=-90, le=90),
    lng: float = Query(..., ge=-180, le=180),
):
    """The cadastral parcel containing a WGS84 point (click → parcel lookup)."""
    local_available = stats_service.cadastral_store_available()
    result = await asyncio.to_thread(stats_service.get_parcel_at_point, lat, lng) if local_available else None
    source = get_map_layer_source()
    if result is None:
        if source.available:
            try:
                result = await source.read_feature_at_point("cadastral-parcels", lat, lng)
            except Exception as exc:
                raise HTTPException(status_code=503, detail="Canonical PostGIS map source unavailable") from exc
    if result is None and not local_available and not source.available:
        raise HTTPException(status_code=503, detail=_CADASTRAL_BUILD_HINT)
    if result is None:
        raise HTTPException(status_code=404, detail="No parcel at this point (region store missing or open water)")
    return result


_CENSUS_BUILD_HINT = (
    "Census-sections store not built. Run: python -m aecs4u_stats.census.scripts.import_census_sections --all"
)


@enrichment_router.get("/census/at-point")
async def get_census_section_at_point(
    lat: float = Query(..., ge=-90, le=90),
    lng: float = Query(..., ge=-180, le=180),
    comune: Optional[str] = Query(None, description="Cadastral municipality code when known"),
):
    """The 2021 census section containing a WGS84 point.

    Keep this static route above ``/census/{cadastral_code}`` so Starlette
    does not interpret ``at-point`` as a cadastral code.
    """
    if not await stats_service.acensus_db_available():
        raise HTTPException(status_code=503, detail=_CENSUS_BUILD_HINT)
    result = await stats_service.aget_census_section_at_point(lat, lng, cadastral_code=comune)
    if result is None:
        raise HTTPException(status_code=404, detail="No census section at this point")
    return result


@enrichment_router.get("/census/{cadastral_code}")
async def get_census_sections(cadastral_code: str, limit: int = Query(5000, gt=0, le=20000)):
    """2021 ISTAT census sections covering a comune, as a GeoJSON
    FeatureCollection — population by age/sex, education, employment,
    foreign-resident, household-size and dwelling-occupancy indicators,
    plus derived rates, per section."""
    if not await stats_service.acensus_db_available():
        raise HTTPException(status_code=503, detail=_CENSUS_BUILD_HINT)
    result = await stats_service.aget_census_sections(cadastral_code, limit=limit)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No municipality found for cadastral code '{cadastral_code}'")
    return result


### /crime, /quality-of-life (x2), and /demographics (x2) delegate to
### aecs4u_stats.web.enrichment's matching functions (see the add_api_route
### block above) — verified equivalent: both this module's now-removed
### handlers and aecs4u_stats's own call the identical
### aecs4u_stats.istat.queries.ISTATQueryEngine methods
### (get_safety_overview_kpis/list_crime_types, list_bes_indicators/
### get_bes_indicator, list_demographic_indicators/get_demographic_indicator),
### which already exist upstream today — the try/except AttributeError
### fallback to _local_* helpers this module used to have was dead code, never
### actually exercised. No test in this repo references these routes or their
### local stats_service functions directly.
