"""Shared zone and microzone UI/business rules."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Tuple

MICROZONE_WARNING_THRESHOLD_KM2 = 0.3


def area_sqm_to_km2(area_sqm: Optional[float]) -> Optional[float]:
    """Convert square meters to square kilometers when value is numeric."""
    if area_sqm is None:
        return None
    try:
        value = float(area_sqm)
    except (TypeError, ValueError):
        return None
    return value / 1_000_000.0


def is_large_microzone(
    area_sqm: Optional[float],
    threshold_km2: float = MICROZONE_WARNING_THRESHOLD_KM2,
) -> bool:
    """Return True when a microzone exceeds the warning threshold."""
    area_km2 = area_sqm_to_km2(area_sqm)
    if area_km2 is None:
        return False
    return area_km2 > threshold_km2


def _geodesic_area_sqm(geom: Any) -> Optional[float]:
    """Compute geodesic area in square meters for WGS84 lon/lat geometries."""
    try:
        from pyproj import Geod
    except Exception:
        return None

    geod = Geod(ellps="WGS84")

    geom_type = getattr(geom, "geom_type", "")
    if geom_type in {"Polygon", "MultiPolygon"}:
        area, _ = geod.geometry_area_perimeter(geom)
        return abs(float(area))

    # GeometryCollection can include polygonal and non-polygonal children.
    if hasattr(geom, "geoms"):
        total = 0.0
        for part in geom.geoms:
            part_area = _geodesic_area_sqm(part)
            if part_area is not None:
                total += part_area
        return total

    # Points/lines have zero area.
    return 0.0


def geometry_metrics_from_geojson(
    geojson_feature: Optional[Mapping[str, Any]],
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    Return area_sqm and centroid (lat, lng) from a GeoJSON Feature.

    The input is expected to be WGS84 lon/lat coordinates (Leaflet default).
    """
    if not isinstance(geojson_feature, Mapping):
        return None, None, None

    try:
        geometry_data = geojson_feature.get("geometry")
        if not isinstance(geometry_data, Mapping):
            return None, None, None

        from shapely.geometry import shape as shapely_shape

        geom = shapely_shape(geometry_data)
        if geom.is_empty or not geom.is_valid:
            return None, None, None

        centroid = geom.centroid
        area_sqm = _geodesic_area_sqm(geom)
        return area_sqm, float(centroid.y), float(centroid.x)
    except Exception:
        return None, None, None
