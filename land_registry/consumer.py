"""Focused FastAPI integration for applications consuming land-registry maps."""

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, FastAPI, Query
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from land_registry.dependencies import get_datashader_registry

logger = logging.getLogger(__name__)

consumer_router = APIRouter()


def _tile_service():
    return get_datashader_registry().get_service()


@consumer_router.get("/tiles/cadastral-boundaries/{z}/{x}/{y}.png")
async def cadastral_boundary_tile(
    z: int,
    x: int,
    y: int,
    layer: str = Query("map", pattern="^(map|ple)$"),
) -> Response:
    """Render foglio or particella boundaries for an embedded Leaflet map."""
    service = _tile_service()
    try:
        content = await asyncio.to_thread(service.generate_boundary_tile, x, y, z, layer)
    except Exception:
        logger.exception("Consumer cadastral tile failed for %s/%s/%s (%s)", z, x, y, layer)
        content = service._empty_tile()
    return Response(
        content=content,
        media_type="image/png",
        headers={
            "Cache-Control": "public, max-age=86400, stale-while-revalidate=604800"
        },
    )


@consumer_router.get("/cadastral-identify")
async def cadastral_identify(
    lat: float,
    lng: float,
    layer: str = Query("ple", pattern="^(map|ple)$"),
) -> dict:
    """Identify the cadastral polygon under a consumer-map click."""
    try:
        result = await asyncio.to_thread(_tile_service().identify_feature, lat, lng, layer)
    except Exception:
        logger.exception("Consumer cadastral identify failed at (%s, %s)", lat, lng)
        result = None
    return {"found": False} if result is None else {"found": True, **result}


def install_land_registry_consumer(
    app: FastAPI,
    *,
    prefix: str = "/land-registry",
) -> None:
    """Mount the package-owned map asset and focused API on a host app."""
    normalized = "/" + prefix.strip("/")
    static_dir = Path(__file__).resolve().parent / "static"
    app.mount(
        f"{normalized}/static",
        StaticFiles(directory=static_dir),
        name="land_registry_consumer_static",
    )
    app.include_router(
        consumer_router,
        prefix=f"{normalized}/api",
        tags=["land-registry"],
    )
