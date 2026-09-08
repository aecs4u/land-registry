"""Focused FastAPI integration for applications consuming land-registry maps."""

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Query
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from land_registry.dependencies import get_datashader_registry
from land_registry.map_layers import get_map_layer, get_map_layer_source, map_layer_catalog

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


@consumer_router.get("/map/layers")
async def canonical_map_layers() -> dict:
    """Expose the canonical layer catalog to embedded map consumers."""
    source = get_map_layer_source()
    return {"source": "aecs4u-stats PostgreSQL/PostGIS", "available": source.available, "layers": map_layer_catalog()}


@consumer_router.get("/map/layers/health")
async def canonical_map_layers_health() -> dict:
    """Expose the same preflight checks to embedded map consumers."""
    source = get_map_layer_source()
    if not source.available:
        return {"available": False, "layers": source.health()}
    try:
        return {"available": True, "layers": await asyncio.to_thread(source.health)}
    except Exception as exc:
        logger.warning("Consumer canonical layer health check failed: %s", exc)
        raise HTTPException(status_code=503, detail="Canonical PostGIS map source unavailable") from exc


@consumer_router.get("/map/layers/{layer_id}/features")
async def canonical_map_layer_features(
    layer_id: str,
    west: float = Query(..., ge=-180, le=180),
    south: float = Query(..., ge=-90, le=90),
    east: float = Query(..., ge=-180, le=180),
    north: float = Query(..., ge=-90, le=90),
    limit: int = Query(2000, ge=1, le=5000),
) -> dict:
    """Serve a bounded GeoJSON fallback for hosts without VectorGrid."""
    try:
        layer = get_map_layer(layer_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if west >= east or south >= north:
        raise HTTPException(status_code=400, detail="Invalid map bounding box")
    if (east - west) * (north - south) > layer.geojson_max_area:
        return {
            "type": "FeatureCollection",
            "features": [],
            "layer": layer.id,
            "truncated": False,
            "zoom_required": layer.min_zoom,
        }
    source = get_map_layer_source()
    if not source.available:
        raise HTTPException(status_code=503, detail="Canonical PostGIS map source unavailable")
    try:
        payload = await asyncio.to_thread(
            source.read_geojson, layer.id, (west, south, east, north), min(limit, layer.max_features)
        )
    except Exception as exc:
        logger.warning("Consumer canonical GeoJSON failed for %s: %s", layer_id, exc)
        raise HTTPException(status_code=503, detail="Canonical PostGIS map layer unavailable") from exc
    payload["layer"] = layer.id
    payload["truncated"] = len(payload["features"]) >= min(limit, layer.max_features)
    return payload


@consumer_router.get("/tiles/map-layers/{layer_id}/{z}/{x}/{y}.pbf")
async def canonical_map_layer_tile(layer_id: str, z: int, x: int, y: int) -> Response:
    """Serve one canonical map layer to an embedded consumer map."""
    try:
        layer = get_map_layer(layer_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if z < layer.min_zoom or z > 22 or x < 0 or y < 0 or x >= (1 << z) or y >= (1 << z):
        return Response(content=b"", media_type="application/vnd.mapbox-vector-tile")
    source = get_map_layer_source()
    if not source.available:
        raise HTTPException(status_code=503, detail="Canonical PostGIS map source unavailable")
    try:
        content = await asyncio.to_thread(source.read_mvt, layer.id, z, x, y)
    except Exception as exc:
        logger.warning("Consumer canonical tile failed for %s/%s/%s/%s: %s", layer_id, z, x, y, exc)
        raise HTTPException(status_code=503, detail="Canonical PostGIS map layer unavailable") from exc
    return Response(
        content=content,
        media_type="application/vnd.mapbox-vector-tile",
        headers={"Cache-Control": "public, max-age=3600"},
    )


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
