"""Parcel dossier PDF endpoint (Typst, via ``aecs4u-documents``).

``GET /api/v1/enrichment/parcel/dossier/{national_reference}`` replaces the deprecated ReportLab endpoints under
``/parcel/report/``. The report is built server-side from the parcel reference. ``POST`` can also carry validated
inputs for a previously calculated parcel PV scenario; the server re-fetches PVGIS and recalculates its economics.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict

from land_registry import parcel_dossier
from land_registry.i18n import detect_locale
from land_registry.map_layers import get_map_layer_source
from land_registry.models import SolarEconomicsRequest
from land_registry.routers.auth import get_current_user_optional

log = logging.getLogger(__name__)

parcel_dossier_router = APIRouter()

_NEIGHBOUR_TIMEOUT_SECONDS = 4.0


class ParcelDossierRequest(BaseModel):
    """Optional report inputs; ignore legacy panel snapshots from older clients."""

    model_config = ConfigDict(extra="ignore")

    solar_scenario: SolarEconomicsRequest | None = None


async def _neighbour_geometries(reference: str, feature_id: Optional[int]) -> list[dict[str, Any]]:
    """Outlines of the adjacent parcels for the locator map. Decorative: never fails the report."""
    try:
        source = get_map_layer_source()
        if not source.available:
            return []
        features = await asyncio.wait_for(
            source.read_adjacent_features(
                "cadastral-parcels", reference, limit=parcel_dossier.NEIGHBOUR_LIMIT, feature_id=feature_id, method="intersects"
            ),
            timeout=_NEIGHBOUR_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        log.info("Dossier neighbours unavailable for %s: %s", reference, exc)
        return []
    return [
        feature["geometry"]
        for feature in features or []
        if isinstance(feature, dict) and isinstance(feature.get("geometry"), dict)
    ]


async def _saved_parcel_for(user: Any, reference: str) -> Optional[dict[str, Any]]:
    """The signed-in user's saved-parcel entry (shortlist/favourite metadata) for this reference, if any."""
    user_id = getattr(user, "id", None)
    if not user_id:
        return None
    try:
        from land_registry.routers import api as api_router

        rows = await api_router._saved_parcel_rows(user_id)
        vocabulary = {str(item["value"]): item.get("label") for item in api_router._saved_parcel_status_vocabulary()}
    except Exception as exc:  # noqa: BLE001 - the appendix is optional
        log.info("Dossier saved-parcel lookup unavailable: %s", exc)
        return None
    for row in rows:
        if str(row.get("national_reference") or "") == reference:
            item = api_router._saved_parcel_row_to_response(row)
            item["status_label"] = vocabulary.get(str(item.get("status")))
            return item
    return None


async def build_parcel_dossier(
    national_reference: str,
    request: Request,
    *,
    feature_id: Optional[int] = None,
    user: Any = None,
    solar_scenario: SolarEconomicsRequest | None = None,
) -> Response:
    from land_registry.routers import enrichment

    try:
        parcel = await enrichment.get_parcel_by_reference(national_reference, id=feature_id)
    except HTTPException:
        raise
    locale = detect_locale(request)
    neighbours = await _neighbour_geometries(national_reference, feature_id)
    inputs = await parcel_dossier.gather_inputs(
        national_reference, parcel, neighbours=neighbours, solar_scenario=solar_scenario
    )
    report_id = parcel_dossier.new_report_id()
    saved = await _saved_parcel_for(user, national_reference)
    variables, files = parcel_dossier.build_dossier(
        inputs,
        locale=locale,
        report_id=report_id,
        page_url=parcel_dossier.map_url(str(request.base_url), national_reference),
        saved=saved,
    )
    try:
        pdf_bytes = await parcel_dossier.render_dossier_pdf(variables, files)
    except Exception as exc:  # noqa: BLE001 - surface a stable error instead of a stack trace
        log.exception("Parcel dossier compilation failed for %s", national_reference)
        raise HTTPException(status_code=503, detail="The parcel dossier could not be generated") from exc
    return Response(
        pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'attachment; filename="parcel-dossier.pdf"',
            "X-Report-ID": report_id,
        },
    )


@parcel_dossier_router.get("/parcel/dossier/{national_reference}")
async def get_parcel_dossier(
    national_reference: str,
    request: Request,
    id: Annotated[Optional[int], Query(ge=1, description="Canonical feature id from the selected parcel")] = None,
    user=Depends(get_current_user_optional),
):
    """Download the Typst parcel dossier (cover, summary, thematic sections, source register)."""
    return await build_parcel_dossier(national_reference, request, feature_id=id, user=user)


@parcel_dossier_router.post("/parcel/dossier/{national_reference}")
async def post_parcel_dossier(
    national_reference: str,
    request: Request,
    id: Annotated[Optional[int], Query(ge=1, description="Canonical feature id from the selected parcel")] = None,
    body: ParcelDossierRequest | None = None,
    user=Depends(get_current_user_optional),
):
    """Same report, optionally including a server-recomputed parcel PV scenario."""
    return await build_parcel_dossier(
        national_reference,
        request,
        feature_id=id,
        user=user,
        solar_scenario=body.solar_scenario if body else None,
    )
