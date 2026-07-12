"""Re-export shim: the GHSL service implementation migrated upstream to
``aecs4u_stats.ghsl`` (2026-07) — DEGURBA raster sampling + UCDB urban-centre
join, consumed here unchanged. Configure via ``GHSL_DATA_DIR`` (upstream)
or the existing ``ghsl_settings`` passed to ``GHSLService(...)``."""

from aecs4u_stats.ghsl.service import (
    GHSL_CLASS_LABELS,
    URBAN_CODES_BROAD,
    URBAN_CODES_STRICT,
    GHSLService,
    classify_urban_status,
    discover_raster,
)

__all__ = [
    "GHSL_CLASS_LABELS",
    "URBAN_CODES_BROAD",
    "URBAN_CODES_STRICT",
    "GHSLService",
    "classify_urban_status",
    "discover_raster",
]
