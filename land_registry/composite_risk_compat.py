"""Compatibility implementation of the shared municipal hazard index.

Keep this aligned with ``aecs4u_stats.hazards.composite_risk_score`` until
the shared helper is available in every supported stats package release.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any

COMPOSITE_RISK_SCORE_VERSION = "municipal_hazard_index_v1"
_SEISMIC_ZONE_SCORES = {1: 100.0, 2: 200.0 / 3.0, 3: 100.0 / 3.0, 4: 0.0}
_COMPONENT_NAMES = ("seismic_zone", "landslide_p3_p4_area_pct", "flood_p3_area_pct")


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _zone_number(seismic: Mapping[str, Any] | None) -> int | None:
    if not isinstance(seismic, Mapping):
        return None
    raw = seismic.get("zone")
    if isinstance(raw, bool):
        return None
    try:
        zone = int(raw)
        if str(raw).strip() in {str(zone), f"{zone}.0"} and zone in _SEISMIC_ZONE_SCORES:
            return zone
    except (TypeError, ValueError, OverflowError):
        pass
    label = str(seismic.get("zone_label") or raw or "")
    match = re.match(r"^\s*([1-4])(?:\s|[A-Za-z].*)?$", label)
    return int(match.group(1)) if match else None


def _area_share(value: Any) -> float | None:
    number = _number(value)
    return number if number is not None and 0.0 <= number <= 100.0 else None


def composite_risk_score(
    seismic: Mapping[str, Any] | None,
    hydrogeological: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Calculate the versioned, equal-weight municipal hazard context index."""
    components: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    invalid: list[str] = []

    zone = _zone_number(seismic)
    if zone is None:
        missing.append("seismic_zone")
    else:
        components["seismic_zone"] = {
            "score": round(_SEISMIC_ZONE_SCORES[zone], 2),
            "zone": zone,
            "source": "DPC municipal seismic classification",
        }

    hydro = hydrogeological if isinstance(hydrogeological, Mapping) else {}
    landslide = hydro.get("landslide") if isinstance(hydro.get("landslide"), Mapping) else {}
    landslide_areas = landslide.get("area_pct") if isinstance(landslide.get("area_pct"), Mapping) else {}
    landslide_p4 = _area_share(landslide_areas.get("P4_very_high"))
    landslide_p3 = _area_share(landslide_areas.get("P3_high"))
    if landslide_p4 is None or landslide_p3 is None:
        missing.append("landslide_p3_p4_area_pct")
        if (
            landslide_areas.get("P4_very_high") is not None and landslide_p4 is None
        ) or (landslide_areas.get("P3_high") is not None and landslide_p3 is None):
            invalid.append("landslide_p3_p4_area_pct")
    elif landslide_p4 + landslide_p3 > 100.0:
        missing.append("landslide_p3_p4_area_pct")
        invalid.append("landslide_p3_p4_area_pct")
    else:
        share = landslide_p4 + landslide_p3
        components["landslide_p3_p4_area_pct"] = {
            "score": round(share, 2),
            "area_pct": round(share, 2),
            "p3_area_pct": round(landslide_p3, 2),
            "p4_area_pct": round(landslide_p4, 2),
            "source": str(hydro.get("source") or "ISPRA IdroGEO PAI"),
        }

    flood = hydro.get("flood") if isinstance(hydro.get("flood"), Mapping) else {}
    flood_areas = flood.get("area_pct") if isinstance(flood.get("area_pct"), Mapping) else {}
    flood_p3 = _area_share(flood_areas.get("P3_high_probability"))
    if flood_p3 is None:
        missing.append("flood_p3_area_pct")
        if flood_areas.get("P3_high_probability") is not None:
            invalid.append("flood_p3_area_pct")
    else:
        components["flood_p3_area_pct"] = {
            "score": round(flood_p3, 2),
            "area_pct": round(flood_p3, 2),
            "source": str(hydro.get("source") or "ISPRA IdroGEO PGRA"),
        }

    scores = [float(value["score"]) for value in components.values()]
    score = round(sum(scores) / len(scores), 2) if scores else None
    status = "complete" if len(components) == len(_COMPONENT_NAMES) else (
        "partial" if components else "insufficient_data"
    )
    return {
        "score": score,
        "version": COMPOSITE_RISK_SCORE_VERSION,
        "status": status,
        "components": components,
        "missing_components": missing,
        "invalid_components": invalid,
        "component_count": len(components),
        "component_total": len(_COMPONENT_NAMES),
        "formula": (
            "Arithmetic mean of available component scores with equal weights; "
            "seismic zone anchors 100/66.67/33.33/0 for zones 1/2/3/4; "
            "landslide P3+P4 and flood P3 area percentages are used directly."
        ),
        "interpretation": (
            "Heuristic municipality-level hazard context index; not a probability, "
            "expected loss, parcel risk estimate, or engineering assessment."
        ),
    }


__all__ = ["COMPOSITE_RISK_SCORE_VERSION", "composite_risk_score"]
