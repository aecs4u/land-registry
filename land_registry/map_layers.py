"""Allow-listed PostGIS map-layer catalog and read-only feature source.

The map must not build SQL from table names supplied by a browser.  This
module keeps the database contract in one place and exposes only map-ready
PostGIS spatial relations that are safe to render.  Large layers use MVT; small
viewport requests use GeoJSON for identify/popups and debugging.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import threading
import time
import unicodedata
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from typing import Any, AsyncIterator, Optional
from urllib.parse import urlsplit, urlunsplit

from land_registry.cadastral_map_views import CADASTRAL_LAYER_IDS, REGIONAL_PROPERTIES, identity_sql, regional_health

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MapLayerColorMode:
    """One selectable entity a layer can be coloured by (a "Plot" option in the browser).

    ``kind="ramp"`` interpolates a numeric ``property`` through ``stops``
    ((value, colour), ascending); ``kind="classes"`` matches the property's exact
    value against ``cases`` ((value, colour, label)). Features whose value is
    missing or unlisted take ``nodata_color`` so unexpected source values stay visible.
    """

    id: str
    title: str
    property: str
    kind: str
    description: str = ""
    unit: str = ""
    stops: tuple[tuple[float, str], ...] = ()
    cases: tuple[tuple[str, str, str], ...] = ()
    nodata_color: str = "#9aa4ad"

    def __post_init__(self) -> None:
        if self.kind == "ramp":
            values = [value for value, _ in self.stops]
            if len(values) < 2 or values != sorted(values) or len(set(values)) != len(values):
                raise ValueError(f"Colour mode {self.id!r} needs at least two ascending ramp stops")
        elif self.kind == "classes":
            keys = [value for value, _, _ in self.cases]
            if not keys or len(set(keys)) != len(keys):
                raise ValueError(f"Colour mode {self.id!r} needs unique class values")
        else:
            raise ValueError(f"Colour mode {self.id!r} has unknown kind {self.kind!r}")

    def public(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "property": self.property,
            "kind": self.kind,
            "unit": self.unit,
            "nodata_color": self.nodata_color,
        }
        if self.kind == "ramp":
            value["stops"] = [{"value": at, "color": color} for at, color in self.stops]
        else:
            value["cases"] = [{"value": key, "color": color, "label": label} for key, color, label in self.cases]
        return value


@dataclass(frozen=True)
class MapLayerSpec:
    id: str
    title: str
    table: str
    geometry_column: str
    id_column: str = "id"
    source_srid: int = 4326
    min_zoom: int = 5
    max_features: int = 2000
    geojson_max_area: float = 250.0
    geojson_page_max_area: Optional[float] = None
    coverage: str = "full"
    coverage_note: str = ""
    # True when features exist only in parts of the layer's extent (for example urban
    # sections, which only some municipalities have): an empty view is then normal and
    # the browser says so instead of leaving a ticked layer that draws nothing.
    sparse: bool = False
    coverage_bounds: Optional[tuple[float, float, float, float]] = None
    properties: tuple[str, ...] = ()
    kind: str = "polygon"
    role: str = ""
    # Presentation contract: the browser draws every layer from these fields so
    # a new layer is a single catalog edit (no colour/order tables in the JS).
    group: str = "territory"
    color: str = "#47758f"
    # Optional fill/outline colour when the same vector source contains both
    # polygons and points; point styling continues to use ``color``.
    polygon_color: Optional[str] = None
    # Stack position, bottom to top; layers sharing a value keep catalog order.
    z_order: int = 100
    fill_opacity: float = 0.11
    line_width: float = 1.4
    # Optional ((min_zoom, unit_type), ...) ladder: which ``unit_type`` of a
    # multi-level layer is drawn at each zoom (ascending by min_zoom).
    unit_levels: tuple[tuple[int, str], ...] = ()
    # Optional data-driven fill: ("property", ((value, colour), ...)) is drawn
    # as a linear colour ramp instead of the flat ``color``.
    color_ramp: Optional[tuple[str, tuple[tuple[float, str], ...]]] = None
    # Optional categorical colouring: ("property", ((substring, colour), ...)).
    # The first case-insensitive substring found in the property wins; anything
    # else keeps the flat ``color``, so unexpected source values stay visible.
    # Seismic ``zone_label`` holds bare codes such as "2", "3S" or "2A-3A-3B", so
    # its cases are the digits 1-4 in order: a mixed label takes its most hazardous zone.
    color_match: Optional[tuple[str, tuple[tuple[str, str], ...]]] = None
    # Optional list of entities the visitor can choose to plot; the first listed (or
    # ``default_color_mode``) is drawn until they pick another. Replaces ``color_ramp``/
    # ``color_match`` for the layer, and every mode property must be a tile property.
    color_modes: tuple[MapLayerColorMode, ...] = ()
    default_color_mode: str = ""
    # Health checks report whether the serving relation needs a GiST index.
    require_gist_index: bool = True
    # Numeric column (square metres) ranking features by on-screen significance. A tile
    # drops features smaller than about one pixel before the per-tile cap, then keeps the
    # largest features if the cap is still exceeded, instead of an arbitrary subset.
    size_column: Optional[str] = None
    # Dense point and mixed layers below this zoom return grid clusters
    # (``point_count``) instead of individual features.
    cluster_below_zoom: int = 0
    # Change when tile geometry/selection changes so browsers fetch fresh tiles.
    tile_revision: str = ""
    # Highest zoom at which the browser requests tiles. Above it MapLibre
    # overzooms the tile it already has, so a z18 view reuses z16 tiles instead
    # of requesting 4-16x more of them. At z16 one tile unit is ~0.15 m, finer
    # than any source geometry here.
    tile_max_zoom: int = 16
    # Relations can move between schemas during canonical-database migrations.
    # Keep alternatives explicit and allow-listed just like the primary table.
    fallback_tables: tuple[str, ...] = ()
    # A source may be optional for deployments that have not provisioned it
    # yet. Optional layers stay visible in the UI but do not block preflight.
    preflight_required: bool = True
    # A layer can be served from a source-owned database instead of Stats.
    source_name: str = ""
    # Some derived or string-keyed layers are fully described by tile/GeoJSON
    # properties and cannot use the integer feature-details route.
    detail_url_enabled: bool = True

    def __post_init__(self) -> None:
        if not self.color_modes:
            return
        ids = [mode.id for mode in self.color_modes]
        if len(set(ids)) != len(ids):
            raise ValueError(f"Layer {self.id!r} has duplicate colour modes")
        if self.default_color_mode and self.default_color_mode not in ids:
            raise ValueError(f"Layer {self.id!r} default colour mode {self.default_color_mode!r} is not defined")
        missing = sorted({mode.property for mode in self.color_modes} - set(self.properties))
        if missing:
            raise ValueError(f"Layer {self.id!r} colour modes use properties not in the tile: {missing}")

    def public(self) -> dict[str, Any]:
        value = asdict(self)
        value["color_modes"] = [mode.public() for mode in self.color_modes]
        value["default_color_mode"] = (self.default_color_mode or self.color_modes[0].id) if self.color_modes else ""
        value["properties"] = list(self.properties)
        value["unit_levels"] = [{"min_zoom": zoom, "unit_type": unit} for zoom, unit in self.unit_levels]
        value["color_ramp"] = (
            {"property": self.color_ramp[0], "stops": [{"value": at, "color": color} for at, color in self.color_ramp[1]]}
            if self.color_ramp
            else None
        )
        value["color_match"] = (
            {"property": self.color_match[0], "cases": [{"contains": text, "color": color} for text, color in self.color_match[1]]}
            if self.color_match
            else None
        )
        value.pop("require_gist_index", None)
        value.pop("tile_revision", None)
        value.pop("size_column", None)
        value.pop("fallback_tables", None)
        value.pop("preflight_required", None)
        source_name = value.pop("source_name", "")
        detail_url_enabled = value.pop("detail_url_enabled", True)
        value["source"] = source_name or "aecs4u-stats PostgreSQL/PostGIS"
        value["tile_url"] = f"/api/v1/tiles/map-layers/{self.id}/{{z}}/{{x}}/{{y}}.pbf"
        if self.tile_revision:
            value["tile_url"] += f"?v={self.tile_revision}"
        value["geojson_url"] = f"/api/v1/map/layers/{self.id}/features"
        if detail_url_enabled:
            value["detail_url"] = f"/api/v1/map/layers/{self.id}/features/{{feature_id}}"
        if self.coverage_bounds is not None:
            value["coverage_bounds"] = list(self.coverage_bounds)
        return value


# EGMS ground motion (Zornade 100 m summary of Copernicus EGMS L3). The source labels are
# Italian words ("trascurabile" .. "molto_alto"); the browser shows these English labels,
# translated by gettext. Velocity bands are on |mm/yr| and match the source's own classes
# (2, 5, 10 and 20 mm/yr). Risk class is the velocity class, raised one level where the
# trend is accelerating. Negative vertical velocity means the ground is lowering.
_EGMS_SEVERITY = ("#7fb8a4", "#f2d35b", "#f19a3e", "#d6452b", "#7a1226")
_EGMS_COLOR_MODES: tuple[MapLayerColorMode, ...] = (
    MapLayerColorMode(
        "risk-class", "Risk class", "risk_class", "classes",
        description="Overall ground-movement class: vertical speed, raised one level where movement is accelerating.",
        cases=(
            ("1", _EGMS_SEVERITY[0], "Negligible"), ("2", _EGMS_SEVERITY[1], "Low"),
            ("3", _EGMS_SEVERITY[2], "Moderate"), ("4", _EGMS_SEVERITY[3], "High"),
            ("5", _EGMS_SEVERITY[4], "Very high"),
        ),
    ),
    MapLayerColorMode(
        "risk-index", "Risk index", "risk_index", "ramp",
        description="Continuous 0–1 score behind the risk class.",
        stops=((0, _EGMS_SEVERITY[0]), (0.1, _EGMS_SEVERITY[1]), (0.25, _EGMS_SEVERITY[2]), (0.5, _EGMS_SEVERITY[3]), (1, _EGMS_SEVERITY[4])),
    ),
    MapLayerColorMode(
        "mean-velocity", "Vertical velocity", "mean_velocity", "ramp", unit="mm/yr",
        description="Average vertical ground speed. Negative values: the ground is lowering (subsidence); positive values: uplift.",
        stops=(
            (-50, "#67001f"), (-20, "#b2182b"), (-10, "#d6604d"), (-5, "#f4a582"), (-2, "#fddbc7"),
            (0, "#f2f2f2"), (2, "#d1e5f0"), (5, "#92c5de"), (10, "#4393c3"),
        ),
    ),
    MapLayerColorMode(
        "velocity-class", "Velocity class", "velocity_class", "classes",
        description="Size of the vertical speed in either direction, grouped by mm/yr.",
        cases=(
            ("1", _EGMS_SEVERITY[0], "Negligible (< 2 mm/yr)"), ("2", _EGMS_SEVERITY[1], "Low (2–5 mm/yr)"),
            ("3", _EGMS_SEVERITY[2], "Moderate (5–10 mm/yr)"), ("4", _EGMS_SEVERITY[3], "High (10–20 mm/yr)"),
            ("5", _EGMS_SEVERITY[4], "Very high (> 20 mm/yr)"),
        ),
    ),
    MapLayerColorMode(
        "acceleration", "Acceleration", "acceleration", "ramp", unit="mm/yr²",
        description="Change in vertical speed. Negative values: movement is becoming more downward; positive values: more upward.",
        stops=((-3, "#762a83"), (-1.5, "#af8dc3"), (-0.5, "#e7d4e8"), (0, "#f7f7f7"), (0.5, "#d9f0d3"), (1.5, "#7fbf7b"), (3, "#1b7837")),
    ),
    MapLayerColorMode(
        "trend", "Trend", "trend", "classes",
        description="Whether the movement is speeding up, steady or slowing down.",
        cases=(
            ("accelerazione", "#d6452b", "Accelerating"), ("stabile", "#9aa7b0", "Stable"),
            ("decelerazione", "#3b82c4", "Decelerating"),
        ),
    ),
    MapLayerColorMode(
        "signal-quality", "Signal quality", "signal_quality", "classes",
        description="How reliable the satellite measurement is in each cell.",
        cases=(("buona", "#4f9d69", "Good"), ("moderata", "#e0a526", "Moderate"), ("scarsa", "#c0392b", "Poor")),
    ),
)

_MEF_IRPEF_COLOR_MODES: tuple[MapLayerColorMode, ...] = (
    MapLayerColorMode(
        "mean-taxable-income", "Mean taxable income", "mean_taxable_income_eur", "ramp",
        unit="EUR", description="Taxable income per taxpayer in the selected tax year.",
        stops=((0, "#f4f1de"), (10000, "#d8e6b5"), (20000, "#91c4a2"), (30000, "#4d9a91"), (45000, "#28566b")),
    ),
    MapLayerColorMode(
        "high-income-share", "Taxpayers above €120,000", "high_income_share_pct", "ramp",
        unit="%", description="Share of taxpayers in the MEF income bracket above €120,000.",
        stops=((0, "#f4f1de"), (1, "#d8e6b5"), (3, "#91c4a2"), (6, "#4d9a91"), (12, "#28566b")),
    ),
    MapLayerColorMode(
        "low-income-share", "Taxpayers below €10,000", "low_income_share_pct", "ramp",
        unit="%", description="Share of taxpayers with taxable income below €10,000.",
        stops=((0, "#f4f1de"), (10, "#d8e6b5"), (20, "#91c4a2"), (35, "#4d9a91"), (55, "#28566b")),
    ),
)


def color_mode_strings() -> list[str]:
    """Every user-facing colour-mode string, for the browser's translation table."""
    strings: list[str] = []
    for layer in MAP_LAYERS:
        for mode in layer.color_modes:
            strings.extend((mode.title, mode.description, *(label for _, _, label in mode.cases)))
    return [text for text in dict.fromkeys(strings) if text]


# Keep the property lists deliberately small: census and raw-tag columns can
# be very wide. Map layers should use prepared spatial relations; raw landing
# relations require bounded, source-filtered map queries.
MAP_LAYERS: tuple[MapLayerSpec, ...] = (
    MapLayerSpec("geo-boundaries-areas", "Geo Areas", "public.v_ripartizioni_map", "geom", properties=("id", "geo_unit_id", "canonical_name", "unit_type", "generalization", "source_release"), group="administrative-boundaries", color="#86699a", z_order=8, min_zoom=5, fill_opacity=0.035, line_width=1.2, require_gist_index=False, tile_revision="istat-areas-1"),
    MapLayerSpec("geo-boundaries", "Regions", "public.v_regioni_map", "geom", properties=("id", "geo_unit_id", "canonical_name", "unit_type", "generalization", "source_release"), role="admin-substitute", group="administrative-boundaries", color="#526b84", z_order=10, min_zoom=5, fill_opacity=0.045, line_width=1.2, require_gist_index=False, tile_revision="istat-regions-1"),
    MapLayerSpec("geo-boundaries-provinces", "Provinces", "public.v_province_map", "geom", properties=("id", "geo_unit_id", "canonical_name", "unit_type", "generalization", "source_release"), group="administrative-boundaries", color="#367891", z_order=11, min_zoom=7, fill_opacity=0.025, line_width=1.0, require_gist_index=False, tile_revision="istat-provinces-1"),
    MapLayerSpec("geo-boundaries-municipalities", "Municipalities", "public.v_comuni_map", "geom", properties=("id", "geo_unit_id", "canonical_name", "unit_type", "generalization", "source_release"), group="administrative-boundaries", color="#3d927e", z_order=12, min_zoom=9, fill_opacity=0.015, line_width=0.8, require_gist_index=False, tile_revision="istat-municipalities-1"),
    # Coverage changes as upstream publications are loaded. Do not hard-code a
    # region or a row count here; health() reports the current estimated extent
    # from PostGIS statistics when ANALYZE data is available.
    MapLayerSpec("cadastral-sheets", "Cadastral sheets", "spatial.cadastral_sheet", "geom", min_zoom=10, max_features=3000, geojson_max_area=4.0, coverage="unknown", properties=("id", "sheet_reference", "municipality_id", "level", "level_name", "area_sqm", "source_release"), group="cadastral", color="#1976a8", z_order=70, tile_revision="regional-2"),
    # GeoJSON is reserved for close inspection and overlays. Dense urban
    # viewports can contain tens of thousands of parcels even at z14; keep
    # those reads small enough that a page of clipped geometries stays bounded.
    MapLayerSpec("cadastral-parcels", "Cadastral parcels", "spatial.cadastral_parcel", "geom", min_zoom=14, max_features=10000, size_column="area_sqm", geojson_max_area=0.04, geojson_page_max_area=0.0005, coverage="unknown", properties=("id", "canonical_reference", "national_cadastral_reference", "parcel", "sheet", "municipality_id", "area_sqm", "source_release", "has_visura", "is_auction_sale"), group="cadastral", color="#d97925", z_order=90, fill_opacity=0.04, line_width=0.8, tile_revision="regional-5"),
    MapLayerSpec("urban-sections", "Cadastral urban sections", "public.sezioni_urbane", "geom", id_column="OGC_FID", min_zoom=11, coverage="partial", coverage_note="Only municipalities that have urban sections (about 520, such as Rome, Genoa, Verona and Trieste); most of Italy has none", sparse=True, properties=("OGC_FID", "nationalcadastralzoningreference", "administrativeunit", "sezione_urbana"), group="cadastral", color="#7c3aed", z_order=95, fill_opacity=0.13, line_width=2.8, require_gist_index=False),
    # DEPRECATED (2026-10-21): the Zornade-sourced zone polygons. Keep them serving tiles until a replacement
    # source exists, but do not build new features on it. Tiles read the omi database's zornade.zone_omi
    # directly (27k polygons, GiST-indexed); the old Stats-side copy and foreign-table chain are gone.
    MapLayerSpec("market-zones", "OMI market zones", "zornade.zone_omi", "geom", id_column="OGC_FID",
                 min_zoom=10, properties=("OGC_FID", "codcom", "codzona", "zona_descr", "comune_descrizione", "cod_tip_prev", "descr_tip_prev", "descr_tipologia_en", "stato_prev", "compr_min", "compr_max", "loc_min", "loc_max"), group="cadastral", color="#7b61a8", z_order=30, require_gist_index=False, tile_revision="3"),
    MapLayerSpec("postal-zones", "Postal zones", "stats_export.cap_subcomunali", "geom", id_column="OGC_FID", min_zoom=10, properties=("OGC_FID", "cap", "comune_cap", "comune", "provincia", "regione", "fonte"), group="administrative-boundaries", color="#8a7a3d", z_order=40, require_gist_index=False, tile_revision="3"),
    MapLayerSpec("flood-hazard", "Flood hazard areas", "hazards.flood_hazard", "geom", id_column="scenario_code", min_zoom=8, max_features=3000, size_column="area_sqm", properties=("scenario_code", "scenario_label", "area_sqm"), group="risk", color="#2b7bb9", z_order=50, fill_opacity=0.2, require_gist_index=False, tile_revision="3"),
    MapLayerSpec("landslide-hazard", "Landslide hazard areas", "hazards.landslide_hazard", "geom", id_column="hazard_code", min_zoom=8, max_features=3000, size_column="area_sqm", properties=("hazard_code", "hazard_label", "area_sqm"), group="risk", color="#a0522d", z_order=51, fill_opacity=0.2, require_gist_index=False, tile_revision="3"),
    MapLayerSpec("census-sections", "ISTAT census sections", "census_sections.sections", "geom", id_column="sez21_id", source_srid=32632, min_zoom=13, max_features=3000, properties=("sez21_id", "procom", "cod_reg", "pop21", "fam21", "abi21", "edi21"), group="demographics", color="#3f8f73", z_order=60, fill_opacity=0.3, color_ramp=("pop21", ((0, "#e8f4ef"), (50, "#bfe3d2"), (200, "#7fc1a3"), (500, "#3f8f73"), (1500, "#1f5d49"))), fallback_tables=("census_sections.sections",), require_gist_index=False, tile_revision="1"),
    MapLayerSpec(
        "mef-irpef-income", "MEF / IRPEF mean taxable income", "lr_map.v_mef_income_comuni_map", "geom",
        id_column="geo_unit_id", min_zoom=9, max_features=3000, geojson_max_area=0.5,
        properties=(
            "geo_unit_id", "canonical_name", "tax_year", "taxpayers",
            "mean_taxable_income_eur", "high_income_share_pct", "low_income_share_pct",
        ),
        group="demographics", color="#397a73", z_order=58, fill_opacity=0.44,
        color_modes=_MEF_IRPEF_COLOR_MODES, default_color_mode="mean-taxable-income",
        coverage="partial", coverage_note="Only municipalities with MEF data and a matching ISTAT code are shown",
        require_gist_index=False, preflight_required=False, detail_url_enabled=False, tile_revision="1",
    ),
    MapLayerSpec(
        "anncsu-addresses", "Civic addresses (ANNCSU)", "public.r19__addresses", "geom",
        id_column="access_id", source_srid=4258, min_zoom=9, max_features=5000,
        geojson_max_area=0.02,
        properties=(
            "access_id", "street_id", "address", "street", "locality", "civic_number",
            "metric_number", "exponent", "specificity", "snc_progressive", "comune_code",
            "istat_code", "coordinate_method", "coordinate_method_label", "region_code",
            "source_vintage", "longitude", "latitude", "altitude_m",
        ),
        kind="point", group="cadastral", color="#b34487", z_order=125,
        cluster_below_zoom=14, coverage="partial",
        coverage_note="Only regions with a ready ANNCSU point store are shown",
        require_gist_index=True, tile_revision="1", preflight_required=False,
        source_name="ANNCSU source-owned PostgreSQL/PostGIS database",
        detail_url_enabled=False,
    ),
    MapLayerSpec(
        "corine-landcover", "CORINE land cover (2018)", "public.r19__corine_clc2018", "geom",
        id_column="feature_id", source_srid=3035, min_zoom=8, max_features=5000,
        geojson_max_area=0.5,
        properties=("feature_id", "code_18", "level_1_code", "level_1_name_en", "remark", "area_ha", "source_version"),
        kind="polygon", group="territory", color="#63844b", z_order=44,
        fill_opacity=0.46,
        color_match=("level_1_name_en", (("Artificial surfaces", "#c9574b"), ("Agricultural areas", "#e1bd4e"), ("Forest and semi-natural areas", "#4d8b54"), ("Wetlands", "#61a7a0"), ("Water bodies", "#4c78a8"))),
        coverage="partial", coverage_note="Only regions with a ready CORINE polygon store are shown",
        require_gist_index=True, preflight_required=False, detail_url_enabled=False,
        source_name="CORINE source-owned PostgreSQL/PostGIS database", tile_revision="1",
    ),
    MapLayerSpec("mps04-points", "MPS04 seismic points", "hazards.mps04_points", "geom", id_column="point_id", min_zoom=7, max_features=3000, properties=("point_id", "grid_variant", "lon", "lat"), kind="point", group="risk", color="#5b6fb5", z_order=120, require_gist_index=False, tile_revision="1"),
    MapLayerSpec("seismic-classification", "Seismic classification", "hazards.v_seismic_classification_map", "geom", id_column="istat_code", min_zoom=6, max_features=3000, properties=("istat_code", "comune", "regione", "provincia", "zone", "zone_label"), group="risk", color="#7a4cb5", z_order=110, fill_opacity=0.2, color_match=("zone_label", (("1", "#7d1515"), ("2", "#c44444"), ("3", "#e6a700"), ("4", "#5b8f55"))), require_gist_index=False, tile_revision="1"),
    # EGMS cells are 100 m, so a z13 tile holds at most ~2,400 of them. Below that the
    # unordered per-tile LIMIT silently dropped most of a dense tile (z11 Palermo: 3,000 of
    # 13,879), so the layer starts at z13 where the cap can never truncate.
    # DEPRECATED (2026-10-21): the Zornade EGMS summary is the source; do not build new features on it.
    MapLayerSpec("surface-subsidence", "Ground movement (EGMS)", "egms.egms_subsidenza", "geom", id_column="risk_index", min_zoom=13, max_features=3000, geojson_max_area=4.0, properties=("risk_index", "mean_velocity", "acceleration", "risk_class", "risk_class_label", "velocity_class", "velocity_class_label", "trend", "signal_quality"), group="risk", color="#c45c2d", z_order=115, fill_opacity=0.5, line_width=0.4, color_modes=_EGMS_COLOR_MODES, default_color_mode="risk-class", require_gist_index=False, tile_revision="4"),
    MapLayerSpec(
        "landscape-constraints", "Landscape constraints",
        "spatial.protection_area", "geom", min_zoom=8, max_features=3000,
        coverage="partial",
        coverage_note="Published regional source coverage can be incomplete",
        properties=("id", "category", "source", "source_id", "source_label", "act_reference", "source_url", "is_binding", "source_release"),
        group="territory", color="#5c8a52", z_order=75, fill_opacity=0.2,
        color_match=("category", (("art136", "#4c772f"), ("art142", "#78a641"), ("art157", "#ad8a38"))),
        tile_revision="1",
    ),
    MapLayerSpec(
        "heritage-protections", "Cultural heritage protections",
        "spatial.protection_area", "geom", min_zoom=10, max_features=3000,
        coverage="partial",
        coverage_note="Published regional source coverage can be incomplete",
        properties=("id", "category", "source", "source_id", "source_label", "act_reference", "source_url", "is_binding", "source_release"),
        kind="mixed", group="territory", color="#9b4c83", z_order=76,
        polygon_color="#934274", fill_opacity=0.24, tile_revision="1",
    ),
    # DEPRECATED (2026-10-21): the municipal aggregate is the Zornade solar-potential dataset; do not
    # build new features on it.
    MapLayerSpec(
        "solar-potential", "Solar potential by municipality",
        "lr_map.v_solar_potential_comuni_map", "geom", id_column="id",
        properties=(
            "id", "geo_unit_id", "canonical_name", "n_buildings",
            "pvout_modern_kwh_year_total", "pvout_pessimistic_kwh_year_total",
            "kwp_max_total", "high_viability_pct", "medium_viability_pct",
            "low_viability_pct", "not_eligible_pct", "solar_data_version", "updated_at",
        ),
        min_zoom=5, max_features=5000, group="energy", color="#e6a700",
        z_order=45, fill_opacity=0.48,
        coverage_note="Municipality geometry joined from ISTAT by municipality code",
        color_ramp=("high_viability_pct", ((0, "#fff2b2"), (25, "#f6c453"), (50, "#e78a24"), (75, "#b84a1b"), (100, "#762a12"))),
        require_gist_index=False, tile_revision="2",
    ),
    MapLayerSpec(
        "maritime-concessions", "Maritime-domain concessions",
        "agenziademanio.v_concession_map_features", "geom",
        id_column="row_id", min_zoom=6, max_features=5000, cluster_below_zoom=12, geojson_max_area=4.0,
        coverage_note="MIT/SID snapshot; published coverage may be incomplete",
        properties=(
            "row_id", "idconc", "admin_label", "layer_kind", "geometry_type",
            "crs_original", "snapshot_id", "source_release",
            "source_feature_key", "dataset_release_id",
            "geometry_valid_4326", "geometry_repaired",
        ),
        kind="mixed", group="cadastral", color="#1f8ea3", z_order=130,
        polygon_color="#e45724", fill_opacity=0.42, line_width=3.0,
        require_gist_index=False, tile_revision="5",
    ),
)


def geojson_zoom_required(
    layer: MapLayerSpec,
    bbox: tuple[float, float, float, float],
    current_zoom: Optional[int] = None,
) -> Optional[int]:
    """Return an approximate zoom that brings this viewport under its GeoJSON area cap."""
    west, south, east, north = bbox
    area = max(0.0, east - west) * max(0.0, north - south)
    max_area = layer.geojson_page_max_area or layer.geojson_max_area
    if area <= max_area:
        return None
    base_zoom = layer.min_zoom if current_zoom is None else min(int(current_zoom), 22)
    additional_levels = 0
    while area > max_area and additional_levels < 22:
        area /= 4
        additional_levels += 1
    return min(22, max(layer.min_zoom, base_zoom + additional_levels))

_BY_ID = {layer.id: layer for layer in MAP_LAYERS}
_PROTECTION_LAYER_TYPES = {
    "landscape-constraints": "landscape",
    "heritage-protections": "cultural_heritage",
}
PROTECTION_LAYER_IDS = frozenset(_PROTECTION_LAYER_TYPES)
_HAZARDS_SOURCE_RELATIONS = {
    "flood-hazard": "hazards.flood_hazard",
    "landslide-hazard": "hazards.landslide_hazard",
    "mps04-points": "hazards.mps04_points",
    "seismic-classification": "hazards.v_seismic_classification_map",
}
_EGMS_SOURCE_RELATIONS = {"surface-subsidence": "egms.egms_subsidenza"}
_CENSUS_SOURCE_RELATION = "census_sections.sections"
_NPL_SOURCE = "verdict asteflorio.npl_asteflorio_asset_map_data and asteflorio.npl_asteflorio_asset_map_summary"
_SISTER_PROPERTY_SOURCE = "sister sister_legacy.v_sister_property_by_cadastral_parcel"
# Layers served straight from the database that owns them: layer id ->
# (source database, relation inside it). aecs4u-stats (the FDW mirror) is
# deprecated, so none of these go through it. The two ``lr_map`` views join
# municipality geometry to a metric table and are created by
# scripts/sql/istat-municipality-metric-map-views.sql.
_DIRECT_LAYER_SOURCES: dict[str, tuple[str, str]] = {
    "geo-boundaries-areas": ("istat", "public.v_ripartizioni_map"),
    "geo-boundaries": ("istat", "public.v_regioni_map"),
    "geo-boundaries-provinces": ("istat", "public.v_province_map"),
    "geo-boundaries-municipalities": ("istat", "public.v_comuni_map"),
    "mef-irpef-income": ("istat", "lr_map.v_mef_income_comuni_map"),
    "solar-potential": ("istat", "lr_map.v_solar_potential_comuni_map"),
    "postal-zones": ("cap", "stats_export.cap_subcomunali"),
    "urban-sections": ("sezioni_urbane", "public.sezioni_urbane"),
    "maritime-concessions": ("agenziademanio", "agenziademanio.v_concession_map_features"),
    "census-sections": ("census_sections", "census_sections.sections"),
    "market-zones": ("omi", "zornade.zone_omi"),
    # Former Stats-owned tables, now owned by the land_registry database.
    "landscape-constraints": ("land_registry", "spatial.protection_area"),
    "heritage-protections": ("land_registry", "spatial.protection_area"),
}
_ADMIN_BOUNDARY_UNIT_TYPES = {
    "geo-boundaries": "region",
    "geo-boundaries-provinces": "province",
    "geo-boundaries-municipalities": "municipality",
    "geo-boundaries-areas": "geographic_area",
}


def _istat_boundary_relation() -> str:
    """Normalize canonical ISTAT map views to the boundary tile schema."""
    return """(
        SELECT (1000000000 + cod_reg)::bigint AS id,
               cod_reg::bigint AS geo_unit_id,
               name AS canonical_name,
               'region'::text AS unit_type,
               NULL::text AS generalization,
               'ISTAT'::text AS source_release,
               geom
        FROM public.v_regioni_map
        UNION ALL
        SELECT (2000000000 + cod_uts)::bigint AS id,
               cod_uts::bigint AS geo_unit_id,
               name AS canonical_name,
               'province'::text AS unit_type,
               NULL::text AS generalization,
               'ISTAT'::text AS source_release,
               geom
        FROM public.v_province_map
        UNION ALL
        SELECT (3000000000 + pro_com)::bigint AS id,
               pro_com::bigint AS geo_unit_id,
               name AS canonical_name,
               'municipality'::text AS unit_type,
               NULL::text AS generalization,
               'ISTAT'::text AS source_release,
               geom
        FROM public.v_comuni_map
        UNION ALL
        SELECT (4000000000 + cod_rip)::bigint AS id,
               cod_rip::bigint AS geo_unit_id,
               name AS canonical_name,
               'geographic_area'::text AS unit_type,
               NULL::text AS generalization,
               'ISTAT'::text AS source_release,
               geom
        FROM public.v_ripartizioni_map
    )"""


def get_map_layer(layer_id: str) -> MapLayerSpec:
    try:
        return _BY_ID[layer_id]
    except KeyError as exc:
        raise KeyError(f"Unknown map layer: {layer_id}") from exc


def _one_pixel_area_m2(z: int, y: int) -> float:
    """Ground area of one screen pixel (512 px tiles) at the tile's latitude, in m^2.

    Features smaller than this render as less than a pixel, so tiles drop them before the
    per-tile cap. Using the tile's own latitude keeps the threshold from removing anything
    visible in the north of Italy, where a Mercator pixel covers less ground.
    """
    n = 1 << z
    latitude = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 0.5) / n))))
    side = 40075016.686 / n * math.cos(math.radians(latitude)) / 512
    return side * side


def map_layer_catalog(include_protection_ids: Optional[set[str]] = None) -> list[dict[str, Any]]:
    included = include_protection_ids or set()
    return [
        layer.public()
        for layer in MAP_LAYERS
        if layer.id not in PROTECTION_LAYER_IDS or layer.id in included
    ]


def _json_value(value: Any) -> Any:
    """Convert DB-native values into JSON-safe values without losing dates."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _municipality_name_key(value: Any) -> str:
    """Normalize municipality names for the NPL view's name-only location keys."""
    decomposed = unicodedata.normalize("NFKD", str(value or "").strip().casefold())
    return "".join(char for char in decomposed if char.isalnum())


def _json_row(row: Any) -> dict[str, Any]:
    return {name: _json_value(value) for name, value in row.items()}


def _sister_cadastral_values(value: Any, *, sheet: bool = False) -> set[str]:
    """Normalize the fixed-width sheet and parcel values used by AdE and SISTER."""
    text = str(value or "").strip()
    if not text:
        return set()
    values = {text}
    try:
        compact = str(int(text))
    except ValueError:
        compact = text.lstrip("0") or "0"
    values.add(compact)
    # Some AdE regional extracts encode a sheet as a six-character number
    # whose last two zeroes are a precision marker (e.g. 001800 -> SISTER 18).
    if sheet and compact.isdigit() and int(compact) >= 100 and int(compact) % 100 == 0:
        values.add(str(int(compact) // 100))
    return values


def _sister_reference_parts(reference: Any) -> tuple[str, str, str, str]:
    """Extract municipality, section, sheet and parcel from a cadastral reference."""
    value = str(reference or "").strip().upper()
    if "_" in value:
        head, suffix = value.split("_", 1)
        match = re.fullmatch(r"([A-Z]\d{3})([A-Z]?)", head)
        sheet, _, parcel = suffix.partition(".")
    else:
        match = re.match(r"^([A-Z]\d{3})([A-Z]?)(\d+)\.(.+)$", value)
        sheet, parcel = (match.group(3), match.group(4)) if match else ("", "")
    if not match:
        return "", "", "", ""
    return match.group(1), match.group(2), sheet, parcel.split("/", 1)[0]


# SISTER document types that are a visura per immobile (the `has_visura` flag).
_VISURA_DOCUMENT_TYPES = ("visura_fabbricati", "visura_terreni", "visura")

_PLACEHOLDER = re.compile(r"%(s|%)")


# Boolean parcel columns a viewport read may filter on in the database.
GEOJSON_FLAG_COLUMNS = frozenset({"is_auction_sale", "has_visura"})


def _asyncpg_sql(sql: str) -> str:
    """Translate DB-API ``%s``/``%%`` markup into asyncpg ``$n`` placeholders."""
    index = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal index
        if match.group(1) == "%":
            return "%"
        index += 1
        return f"${index}"

    return _PLACEHOLDER.sub(replace, sql)


class _AsyncpgConnectionSource:
    """Bounded asyncpg pool for map reads.

    This used to be a psycopg2 pool.  ``psycopg2-binary`` bundles its own
    libpq and OpenSSL; next to the system OpenSSL and the copies bundled by
    the GDAL/rasterio wheels, its TLS connection setup segfaulted the API
    worker (SIGSEGV, not catchable) on ordinary map page loads.  asyncpg uses
    the interpreter's ``ssl`` module, its timeouts are enforced by the event
    loop, and ``acquire`` waits for a free connection instead of raising
    ``PoolError`` when a burst of tile requests exceeds the pool size.
    """

    RETRY_AFTER_SECONDS = 10.0
    # ``public`` first (package importer layout); ``anncsu`` for databases that
    # keep the regional tables and PostGIS in that schema.
    _ANNCSU_SEARCH_PATH = "public,anncsu"

    # Every pooled connection that reads a foreign table keeps one backend open
    # on each remote server it touched, so the pool size multiplies across the
    # ~10 foreign servers behind the map. Four keeps a tile burst from using
    # up Postgres' connection slots; override with MAP_DB_POOL_MAX.
    DEFAULT_POOL_SIZE = 4
    # PostGIS is installed in the database-named schema (not ``public``) in
    # these source databases, so tile functions such as ST_TileEnvelope only
    # resolve with that schema on the search path.
    POSTGIS_SEARCH_PATHS = {
        "cap": "cap,public",
        "hazards": "hazards,public",
        "egms": "egms,public",
        "census_sections": "census_sections,public",
        "land_registry": "land_registry,public",
    }
    ACQUIRE_TIMEOUT_SECONDS = 15.0

    def __init__(
        self,
        dsn: str,
        max_connections: Optional[int] = None,
        connect_timeout: float = 3.0,
        search_path: Optional[str] = None,
    ):
        self.dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
        # Optional schema search path for databases whose PostGIS functions or
        # tables are not in ``public`` (e.g. the local ``anncsu`` database).
        self.search_path = search_path
        if max_connections is None:
            try:
                max_connections = int(os.getenv("MAP_DB_POOL_MAX", ""))
            except ValueError:
                max_connections = self.DEFAULT_POOL_SIZE
            max_connections = max(1, min(max_connections, 32))
        self.max_connections = max_connections
        self.connect_timeout = connect_timeout
        self._pool = None
        self._pool_loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock: Optional[asyncio.Lock] = None
        self._retry_at = 0.0

    @classmethod
    def from_environment(cls) -> Optional["_AsyncpgConnectionSource"]:
        if os.getenv("STATS_POSTGRES_ENABLE", "").strip().lower() not in ("1", "true", "yes", "on"):
            return None
        dsn = (
            os.getenv("STATS_POSTGRES_DSN")
            or os.getenv("AECS4U_STATS_DATABASE_URL")
            or os.getenv("AECS4U_STATS_SPATIAL_DATABASE_URL")
        )
        if not dsn or not dsn.startswith(("postgres://", "postgresql://", "postgresql+")):
            return None
        return cls(dsn)

    @classmethod
    def database_from_environment(cls, database: str) -> Optional["_AsyncpgConnectionSource"]:
        """Connect straight to one source-owned database.

        ``AECS4U_SOURCE_<DATABASE>_DATABASE_URL`` (or ``<DATABASE>_POSTGRES_DSN``)
        wins; otherwise ``STATS_POSTGRES_DSN`` supplies only the host and
        credentials and its database name is replaced with ``database``.
        """
        key = database.upper()
        search_path = cls.POSTGIS_SEARCH_PATHS.get(database)
        direct_dsn = os.getenv(f"AECS4U_SOURCE_{key}_DATABASE_URL") or os.getenv(f"{key}_POSTGRES_DSN")
        if direct_dsn:
            if direct_dsn.startswith(("postgres://", "postgresql://", "postgresql+")):
                return cls(direct_dsn.replace("postgresql+psycopg2://", "postgresql://", 1), search_path=search_path)
            return None
        base = cls.from_environment()
        if base is None:
            return None
        parsed = urlsplit(base.dsn)
        return cls(urlunsplit(parsed._replace(path=f"/{database}")), search_path=search_path)

    @classmethod
    def census_from_environment(cls) -> Optional["_AsyncpgConnectionSource"]:
        """Use the census database directly so its local GiST index serves tiles."""
        if os.getenv("STATS_POSTGRES_ENABLE", "").strip().lower() not in ("1", "true", "yes", "on"):
            return None
        direct_dsn = os.getenv("CENSUS_POSTGRES_DSN") or os.getenv("CENSUS_SECTIONS_POSTGRES_DSN")
        if direct_dsn:
            return cls(direct_dsn, search_path=cls.POSTGIS_SEARCH_PATHS["census_sections"])
        stats_dsn = (
            os.getenv("STATS_POSTGRES_DSN")
            or os.getenv("AECS4U_STATS_DATABASE_URL")
            or os.getenv("AECS4U_STATS_SPATIAL_DATABASE_URL")
        )
        if not stats_dsn or not stats_dsn.startswith(("postgres://", "postgresql://", "postgresql+")):
            return None
        parsed = urlsplit(stats_dsn.replace("postgresql+asyncpg://", "postgresql://", 1))
        if not parsed.scheme or not parsed.netloc:
            return None
        census_dsn = urlunsplit(
            (parsed.scheme, parsed.netloc, "/census_sections", parsed.query, parsed.fragment)
        )
        return cls(census_dsn, search_path=cls.POSTGIS_SEARCH_PATHS["census_sections"])

    @classmethod
    def hazards_from_environment(cls) -> Optional["_AsyncpgConnectionSource"]:
        """Use the hazards database directly so local GiST indexes serve tiles."""
        direct_dsn = os.getenv("HAZARDS_POSTGRES_DSN") or os.getenv("AECS4U_STATS_HAZARDS_DATABASE_URL")
        if direct_dsn:
            if direct_dsn.startswith(("postgres://", "postgresql://", "postgresql+")):
                return cls(direct_dsn, search_path=cls.POSTGIS_SEARCH_PATHS["hazards"])
            return None
        stats = cls.from_environment()
        if stats is None:
            return None
        parsed = urlsplit(stats.dsn)
        return cls(urlunsplit(parsed._replace(path="/hazards")), search_path=cls.POSTGIS_SEARCH_PATHS["hazards"])

    @classmethod
    def egms_from_environment(cls) -> Optional["_AsyncpgConnectionSource"]:
        """Use the EGMS database directly so its local GiST index serves tiles."""
        direct_dsn = os.getenv("EGMS_POSTGRES_DSN") or os.getenv("AECS4U_STATS_EGMS_DATABASE_URL")
        if direct_dsn:
            if direct_dsn.startswith(("postgres://", "postgresql://", "postgresql+")):
                return cls(direct_dsn, search_path=cls.POSTGIS_SEARCH_PATHS["egms"])
            return None
        stats = cls.from_environment()
        if stats is None:
            return None
        parsed = urlsplit(stats.dsn)
        return cls(urlunsplit(parsed._replace(path="/egms")), search_path=cls.POSTGIS_SEARCH_PATHS["egms"])

    @classmethod
    def anncsu_from_environment(cls) -> Optional["_AsyncpgConnectionSource"]:
        """Use the ANNCSU source-owned database directly for address-point tiles."""
        direct_dsn = (
            os.getenv("ANNCSU_POSTGRES_DSN")
            or os.getenv("AECS4U_SOURCE_ANNCSU_DATABASE_URL")
            or os.getenv("AECS4U_STATS_ANNCSU_DATABASE_URL")
        )
        if direct_dsn:
            if direct_dsn.startswith(("postgres://", "postgresql://", "postgresql+")):
                direct_dsn = direct_dsn.replace("postgresql+psycopg2://", "postgresql://", 1)
                return cls(direct_dsn, search_path=cls._ANNCSU_SEARCH_PATH)
            return None
        stats = cls.from_environment()
        if stats is None:
            return None
        parsed = urlsplit(stats.dsn)
        return cls(urlunsplit(parsed._replace(path="/anncsu")), search_path=cls._ANNCSU_SEARCH_PATH)

    @classmethod
    def corine_from_environment(cls) -> Optional["_AsyncpgConnectionSource"]:
        """Use the CORINE source-owned database directly for polygon tiles."""
        direct_dsn = (
            os.getenv("CORINE_POSTGRES_DSN")
            or os.getenv("AECS4U_STATS_CORINE_DATABASE_URL")
        )
        if direct_dsn:
            if direct_dsn.startswith(("postgres://", "postgresql://", "postgresql+")):
                direct_dsn = direct_dsn.replace("postgresql+psycopg2://", "postgresql://", 1)
                return cls(direct_dsn)
            return None
        stats = cls.from_environment()
        if stats is None:
            return None
        parsed = urlsplit(stats.dsn)
        return cls(urlunsplit(parsed._replace(path="/corine")))


    @classmethod
    def cadastral_from_environment(cls) -> Optional["_AsyncpgConnectionSource"]:
        """Use prepared views in the dedicated database when stats has none."""
        stats = cls.from_environment()
        if stats is None:
            return None
        direct_dsn = os.getenv("CADASTRAL_POSTGRES_DSN")
        if direct_dsn:
            return cls(direct_dsn)
        parsed = urlsplit(stats.dsn)
        return cls(urlunsplit(parsed._replace(path="/cadastral")))

    async def _get_pool(self):
        loop = asyncio.get_running_loop()
        if self._pool_loop is not loop:
            # A pool belongs to the loop that created it.  The server has one
            # loop; test clients and the preflight CLI each bring their own.
            self._pool, self._pool_loop, self._lock = None, loop, asyncio.Lock()
        if self._pool is not None:
            return self._pool
        async with self._lock:
            if self._pool is None:
                # Fail fast while the database is down instead of making every
                # tile in a burst wait for its own connect timeout.
                if time.monotonic() < self._retry_at:
                    raise ConnectionError("canonical map database was unreachable moments ago")
                import asyncpg

                try:
                    self._pool = await asyncpg.create_pool(
                        self.dsn,
                        min_size=1,
                        max_size=self.max_connections,
                        timeout=self.connect_timeout,
                        command_timeout=10,
                        # Every pooled connection that reads a foreign table
                        # keeps one backend open per remote server. Recycle idle
                        # connections quickly so a tile burst does not leave
                        # dozens of FDW backends holding Postgres connection
                        # slots ("too many clients already").
                        max_inactive_connection_lifetime=20,
                        statement_cache_size=0,
                        server_settings={
                            "jit": "off", "statement_timeout": "8000",
                            **({"search_path": self.search_path} if self.search_path else {}),
                        },
                    )
                except Exception:
                    self._retry_at = time.monotonic() + self.RETRY_AFTER_SECONDS
                    raise
        return self._pool

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[Any]:
        pool = await self._get_pool()
        async with pool.acquire(timeout=self.ACQUIRE_TIMEOUT_SECONDS) as connection:
            yield connection

    async def close(self) -> None:
        pool, self._pool = self._pool, None
        if pool is None:
            return
        try:
            same_loop = asyncio.get_running_loop() is self._pool_loop
        except RuntimeError:
            same_loop = False
        if same_loop:
            await pool.close()
        else:
            pool.terminate()


class PostgresMapLayerSource:
    """Read-only canonical map source with its own bounded connection pool."""

    def __init__(
        self,
        connection_source: Optional[_AsyncpgConnectionSource] = None,
        census_connection_source: Optional[_AsyncpgConnectionSource] = None,
        cadastral_connection_source: Optional[_AsyncpgConnectionSource] = None,
        hazards_connection_source: Optional[_AsyncpgConnectionSource] = None,
        egms_connection_source: Optional[_AsyncpgConnectionSource] = None,
        anncsu_connection_source: Optional[_AsyncpgConnectionSource] = None,
        corine_connection_source: Optional[_AsyncpgConnectionSource] = None,
    ):
        use_environment = connection_source is None
        if connection_source is None:
            connection_source = _AsyncpgConnectionSource.from_environment()
        self.connection_source = connection_source
        self.census_connection_source = census_connection_source
        self.cadastral_connection_source = cadastral_connection_source
        if self.cadastral_connection_source is None and use_environment and self.connection_source is not None:
            self.cadastral_connection_source = _AsyncpgConnectionSource.cadastral_from_environment()
        self.hazards_connection_source = hazards_connection_source
        if self.hazards_connection_source is None and use_environment and self.connection_source is not None:
            self.hazards_connection_source = _AsyncpgConnectionSource.hazards_from_environment()
        self.egms_connection_source = egms_connection_source
        if self.egms_connection_source is None and use_environment and self.connection_source is not None:
            self.egms_connection_source = _AsyncpgConnectionSource.egms_from_environment()
        self.anncsu_connection_source = anncsu_connection_source
        if self.anncsu_connection_source is None and use_environment and self.connection_source is not None:
            self.anncsu_connection_source = _AsyncpgConnectionSource.anncsu_from_environment()
        self.corine_connection_source = corine_connection_source
        if self.corine_connection_source is None and use_environment and self.connection_source is not None:
            self.corine_connection_source = _AsyncpgConnectionSource.corine_from_environment()
        self._use_direct_sources = use_environment and self.connection_source is not None
        self._direct_sources: dict[str, Optional[_AsyncpgConnectionSource]] = {}
        self._hazards_routes: dict[str, tuple[Any, float]] = {}
        self._egms_routes: dict[str, tuple[Any, float]] = {}
        self._cadastral_routes: dict[str, tuple[Any, float]] = {}
        self._resolved_relations: dict[str, tuple[str, float]] = {}
        self._anncsu_region_cache: tuple[list[dict[str, Any]], float] | None = None
        self._corine_region_cache: tuple[list[dict[str, Any]], float] | None = None
        self._protection_layer_ids_cache: tuple[set[str], float] | None = None
        self._npl_asset_features_cache: tuple[list[dict[str, Any]], int, float] | None = None

    def _database_source(self, database: str) -> Any:
        """Connection source for one source-owned database.

        An injected ``connection_source`` (tests, single-database setups) is
        used for every database, so callers never need to special-case it.
        """
        if not self._use_direct_sources:
            return self.connection_source
        if database not in self._direct_sources:
            self._direct_sources[database] = _AsyncpgConnectionSource.database_from_environment(database)
        return self._direct_sources[database]

    def _direct_source(self, layer: MapLayerSpec) -> Optional[_AsyncpgConnectionSource]:
        """Source-owned database behind a layer in ``_DIRECT_LAYER_SOURCES``."""
        direct = _DIRECT_LAYER_SOURCES.get(layer.id)
        if direct is None or not self._use_direct_sources:
            return None
        return self._database_source(direct[0])

    def _source_for(self, layer: MapLayerSpec) -> Any:
        if layer.id == "census-sections" and self.census_connection_source is not None:
            return self.census_connection_source
        direct = self._direct_source(layer)
        if direct is not None:
            return direct
        if layer.id == "census-sections" and self.census_connection_source is not None:
            return self.census_connection_source
        if layer.id in _HAZARDS_SOURCE_RELATIONS and self.hazards_connection_source is not None:
            return self.hazards_connection_source
        if layer.id in _EGMS_SOURCE_RELATIONS and self.egms_connection_source is not None:
            return self.egms_connection_source
        if layer.id == "anncsu-addresses":
            return self.anncsu_connection_source
        if layer.id == "corine-landcover":
            return self.corine_connection_source
        return self.connection_source

    async def _layer_source_for(self, layer: MapLayerSpec) -> Any:
        if layer.id == "census-sections" and self.census_connection_source is not None:
            return self.census_connection_source
        direct = self._direct_source(layer)
        if direct is not None:
            return direct
        if layer.id in _HAZARDS_SOURCE_RELATIONS and self.hazards_connection_source is not None:
            cached = self._hazards_routes.get(layer.id)
            if cached and cached[1] > time.monotonic():
                return cached[0]
            selected = self.connection_source
            try:
                async with self.hazards_connection_source.connection() as connection:
                    if await connection.fetchval(
                        "SELECT to_regclass($1)::text", _HAZARDS_SOURCE_RELATIONS[layer.id]
                    ):
                        selected = self.hazards_connection_source
            except Exception as exc:
                log.debug("Direct hazards map source unavailable for %s: %s", layer.id, exc)
            ttl = 300 if selected is self.hazards_connection_source else 10
            self._hazards_routes[layer.id] = (selected, time.monotonic() + ttl)
            return selected
        if layer.id in _EGMS_SOURCE_RELATIONS and self.egms_connection_source is not None:
            cached = self._egms_routes.get(layer.id)
            if cached and cached[1] > time.monotonic():
                return cached[0]
            selected = self.connection_source
            try:
                async with self.egms_connection_source.connection() as connection:
                    if await connection.fetchval(
                        "SELECT to_regclass($1)::text", _EGMS_SOURCE_RELATIONS[layer.id]
                    ):
                        selected = self.egms_connection_source
            except Exception as exc:
                log.debug("Direct EGMS map source unavailable for %s: %s", layer.id, exc)
            ttl = 300 if selected is self.egms_connection_source else 10
            self._egms_routes[layer.id] = (selected, time.monotonic() + ttl)
            return selected
        if layer.id == "anncsu-addresses":
            return self.anncsu_connection_source
        if layer.id == "corine-landcover":
            return self.corine_connection_source
        if layer.id not in CADASTRAL_LAYER_IDS or self.cadastral_connection_source is None:
            return self._source_for(layer)
        cached = self._cadastral_routes.get(layer.id)
        if cached and cached[1] > time.monotonic():
            return cached[0]
        # The cadastral layers are served by the dedicated ``cadastral`` database. The Stats-side canonical
        # copy (aecs4u-stats is deprecated) is empty and is no longer consulted.
        self._cadastral_routes[layer.id] = (self.cadastral_connection_source, time.monotonic() + 300)
        return self.cadastral_connection_source

    async def _anncsu_region_catalog(self, layer_source: Any) -> list[dict[str, Any]]:
        cached = self._anncsu_region_cache
        if cached and cached[1] > time.monotonic():
            return cached[0]
        async with layer_source.connection() as connection:
            rows = await connection.fetch("""
                SELECT DISTINCT ON (substring(c.relname FROM 2 FOR 2)::integer)
                       c.relname AS address_table,
                       n.nspname::text AS table_schema,
                       substring(c.relname FROM 2 FOR 2)::integer AS region_code,
                       greatest(c.reltuples, 0)::bigint AS row_estimate,
                       EXISTS (
                           SELECT 1 FROM pg_attribute a
                           WHERE a.attrelid = c.oid AND a.attname = 'geom'
                             AND a.attnum > 0 AND NOT a.attisdropped
                       ) AS geometry_column_exists,
                       COALESCE((
                           SELECT gc.srid FROM geometry_columns gc
                           WHERE gc.f_table_schema = n.nspname
                             AND gc.f_table_name = c.relname
                             AND gc.f_geometry_column = 'geom'
                           LIMIT 1
                       ), (
                           SELECT postgis_typmod_srid(a.atttypmod)
                           FROM pg_attribute a
                           WHERE a.attrelid = c.oid AND a.attname = 'geom'
                             AND a.attnum > 0 AND NOT a.attisdropped
                           LIMIT 1
                       ), 0)::integer AS geometry_srid,
                       EXISTS (
                           SELECT 1 FROM pg_index i
                           JOIN pg_class idx ON idx.oid = i.indexrelid
                           JOIN pg_am am ON am.oid = idx.relam
                           JOIN pg_attribute a ON a.attrelid = c.oid
                             AND a.attname = 'geom' AND a.attnum = ANY(i.indkey)
                           WHERE i.indrelid = c.oid AND i.indisvalid AND am.amname = 'gist'
                       ) AS gist_index_exists,
                       (
                           SELECT count(DISTINCT a.attname::text) = 14
                           FROM pg_attribute a
                           WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
                             AND a.attname::text = ANY(ARRAY[
                                 'access_id', 'street_id', 'comune_code', 'istat_code',
                                 'civic_number', 'exponent', 'specificity', 'metric_number',
                                 'snc_progressive', 'coordinate_method', 'longitude', 'latitude',
                                 'altitude_m', 'geom'
                             ]::text[])
                       ) AS required_columns_exist,
                       to_regclass(format(
                           '%I.%I', n.nspname, 'r' || substring(c.relname FROM 2 FOR 2) || '__streets'
                       )) IS NOT NULL AS streets_exist,
                       to_regclass(format(
                           '%I.%I', n.nspname, 'r' || substring(c.relname FROM 2 FOR 2) || '__anncsu_provenance'
                       )) IS NOT NULL AS provenance_exists
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = ANY(ARRAY['public', 'anncsu']::text[]) AND c.relkind IN ('r', 'p')
                  AND c.relname ~ '^r(0[1-9]|1[0-9]|20)__addresses$'
                ORDER BY substring(c.relname FROM 2 FOR 2)::integer, (n.nspname = 'public') DESC
            """)
        catalog = [dict(row) for row in rows]
        self._anncsu_region_cache = (catalog, time.monotonic() + 300)
        return catalog

    @staticmethod
    def _anncsu_ready_regions(catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            row for row in catalog
            if row.get("geometry_column_exists")
            and row.get("geometry_srid") == 4258
            and row.get("gist_index_exists")
            and row.get("required_columns_exist")
        ]

    @staticmethod
    def _anncsu_union_relation(regions: list[dict[str, Any]]) -> str:
        branches: list[str] = []
        for row in regions:
            region = int(row["region_code"])
            address_table = str(row["address_table"])
            if not re.fullmatch(r"r(?:0[1-9]|1[0-9]|20)__addresses", address_table):
                continue
            suffix = f"{region:02d}"
            # The source database keeps the regional tables in 'public' (package
            # importer) or, in some local databases, in an 'anncsu' schema.
            schema = str(row.get("table_schema") or "public")
            if schema not in {"public", "anncsu"}:
                continue
            if row.get("streets_exist"):
                streets = (
                    f'LEFT JOIN {schema}."r{suffix}__streets" AS s '
                    "ON s.street_id = a.street_id"
                )
            else:
                streets = (
                    "LEFT JOIN (SELECT NULL::text AS street_id, NULL::text AS odonimo, "
                    "NULL::text AS locality) AS s ON FALSE"
                )
            if row.get("provenance_exists"):
                provenance = (
                    f'LEFT JOIN LATERAL (SELECT source_vintage::text AS source_vintage '
                    f'FROM {schema}."r{suffix}__anncsu_provenance" LIMIT 1) AS p ON TRUE'
                )
            else:
                provenance = (
                    "LEFT JOIN (SELECT NULL::text AS source_vintage) AS p ON FALSE"
                )
            branches.append(f"""
                SELECT a.access_id::text AS access_id,
                       a.street_id::text AS street_id,
                       concat_ws(' ', nullif(s.odonimo::text, ''),
                           nullif(concat_ws(' ',
                               nullif(concat_ws('/',
                                   coalesce(nullif(a.civic_number::text, ''), nullif(a.metric_number::text, '')),
                                   nullif(a.exponent::text, '')
                               ), ''),
                               nullif(a.specificity::text, ''),
                               CASE WHEN nullif(a.snc_progressive::text, '') IS NOT NULL
                                    THEN 'SNC ' || a.snc_progressive::text END
                           ), ''),
                           nullif(s.locality::text, '')
                       ) AS address,
                       s.odonimo::text AS street,
                       s.locality::text AS locality,
                       a.civic_number::text AS civic_number,
                       a.metric_number::text AS metric_number,
                       a.exponent::text AS exponent,
                       a.specificity::text AS specificity,
                       a.snc_progressive::text AS snc_progressive,
                       a.comune_code::text AS comune_code,
                       a.istat_code::text AS istat_code,
                       a.coordinate_method::text AS coordinate_method,
                       CASE a.coordinate_method::text
                           WHEN '1' THEN 'Field survey, estimated accuracy < 5 m'
                           WHEN '2' THEN 'Field survey, estimated accuracy >= 5 m'
                           WHEN '3' THEN 'Indirect mapping, estimated accuracy < 5 m'
                           WHEN '4' THEN 'Indirect mapping, estimated accuracy >= 5 m'
                           WHEN '5' THEN 'Indirect mapping via Portale per i Comuni'
                           ELSE NULL
                       END AS coordinate_method_label,
                       {region}::smallint AS region_code,
                       p.source_vintage::text AS source_vintage,
                       a.longitude::double precision AS longitude,
                       a.latitude::double precision AS latitude,
                       a.altitude_m::double precision AS altitude_m,
                       a.geom AS geom
                FROM {schema}."{address_table}" AS a
                {streets}
                {provenance}
                WHERE a.geom IS NOT NULL
            """)
        if branches:
            return "(" + " UNION ALL ".join(branches) + ")"
        return """(
            SELECT NULL::text AS access_id, NULL::text AS street_id,
                   NULL::text AS address, NULL::text AS street,
                   NULL::text AS locality, NULL::text AS civic_number,
                   NULL::text AS metric_number, NULL::text AS exponent,
                   NULL::text AS specificity, NULL::text AS snc_progressive,
                   NULL::text AS comune_code, NULL::text AS istat_code,
                   NULL::text AS coordinate_method,
                   NULL::text AS coordinate_method_label,
                   NULL::smallint AS region_code, NULL::text AS source_vintage,
                   NULL::double precision AS longitude,
                   NULL::double precision AS latitude,
                   NULL::double precision AS altitude_m,
                   NULL::geometry(Point, 4258) AS geom
            WHERE FALSE
        )"""

    async def _corine_region_catalog(self, layer_source: Any) -> list[dict[str, Any]]:
        cached = self._corine_region_cache
        if cached and cached[1] > time.monotonic():
            return cached[0]
        async with layer_source.connection() as connection:
            rows = await connection.fetch("""
                SELECT c.relname AS data_table,
                       substring(c.relname FROM 2 FOR 2)::integer AS region_code,
                       greatest(c.reltuples, 0)::bigint AS row_estimate,
                       EXISTS (
                           SELECT 1 FROM pg_attribute a
                           WHERE a.attrelid = c.oid AND a.attname = 'geom'
                             AND a.attnum > 0 AND NOT a.attisdropped
                       ) AS geometry_column_exists,
                       COALESCE((
                           SELECT gc.srid FROM public.geometry_columns gc
                           WHERE gc.f_table_schema = 'public'
                             AND gc.f_table_name = c.relname
                             AND gc.f_geometry_column = 'geom'
                           LIMIT 1
                       ), (
                           SELECT public.postgis_typmod_srid(a.atttypmod)
                           FROM pg_attribute a
                           WHERE a.attrelid = c.oid AND a.attname = 'geom'
                             AND a.attnum > 0 AND NOT a.attisdropped
                           LIMIT 1
                       ), 0)::integer AS geometry_srid,
                       EXISTS (
                           SELECT 1 FROM pg_index i
                           JOIN pg_class idx ON idx.oid = i.indexrelid
                           JOIN pg_am am ON am.oid = idx.relam
                           JOIN pg_attribute a ON a.attrelid = c.oid
                             AND a.attname = 'geom' AND a.attnum = ANY(i.indkey)
                           WHERE i.indrelid = c.oid AND i.indisvalid AND am.amname = 'gist'
                       ) AS gist_index_exists,
                       (
                           SELECT count(DISTINCT a.attname::text) = 5
                           FROM pg_attribute a
                           WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
                             AND a.attname::text = ANY(ARRAY[
                                 'feature_id', 'code_18', 'remark', 'area_ha', 'geom'
                             ]::text[])
                       ) AS required_columns_exist,
                       to_regclass(
                           'public.r' || substring(c.relname FROM 2 FOR 2) || '___provenance'
                       ) IS NOT NULL AS provenance_exists
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
                  AND c.relname ~ '^r(0[1-9]|1[0-9]|20)__corine_clc2018$'
                ORDER BY region_code
            """)
        catalog = [dict(row) for row in rows]
        self._corine_region_cache = (catalog, time.monotonic() + 300)
        return catalog

    @staticmethod
    def _corine_ready_regions(catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            row for row in catalog
            if row.get("geometry_column_exists")
            and row.get("geometry_srid") == 3035
            and row.get("gist_index_exists")
            and row.get("required_columns_exist")
        ]

    @staticmethod
    def _corine_union_relation(regions: list[dict[str, Any]]) -> str:
        branches: list[str] = []
        for row in regions:
            region = int(row["region_code"])
            data_table = str(row["data_table"])
            if not re.fullmatch(r"r(?:0[1-9]|1[0-9]|20)__corine_clc2018", data_table):
                continue
            suffix = f"{region:02d}"
            if row.get("provenance_exists"):
                provenance = (
                    f'LEFT JOIN LATERAL (SELECT source_version::text AS source_version '
                    f'FROM public."r{suffix}___provenance" LIMIT 1) AS p ON TRUE'
                )
            else:
                provenance = "LEFT JOIN (SELECT NULL::text AS source_version) AS p ON FALSE"
            branches.append(f"""
                SELECT concat({region}::text, ':', a.feature_id::text) AS feature_id,
                       a.code_18::text AS code_18,
                       left(a.code_18::text, 1) AS level_1_code,
                       CASE left(a.code_18::text, 1)
                           WHEN '1' THEN 'Artificial surfaces'
                           WHEN '2' THEN 'Agricultural areas'
                           WHEN '3' THEN 'Forest and semi-natural areas'
                           WHEN '4' THEN 'Wetlands'
                           WHEN '5' THEN 'Water bodies'
                           ELSE 'Unclassified'
                       END AS level_1_name_en,
                       a.remark::text AS remark,
                       a.area_ha::double precision AS area_ha,
                       p.source_version::text AS source_version,
                       a.geom AS geom
                FROM public."{data_table}" AS a
                {provenance}
                WHERE a.geom IS NOT NULL
            """)
        if branches:
            return "(" + " UNION ALL ".join(branches) + ")"
        return """(
            SELECT NULL::text AS feature_id, NULL::text AS code_18,
                   NULL::text AS level_1_code, NULL::text AS level_1_name_en,
                   NULL::text AS remark, NULL::double precision AS area_ha,
                   NULL::text AS source_version,
                   NULL::geometry(Geometry, 3035) AS geom
            WHERE FALSE
        )"""

    async def _resolve_relation_for(self, layer: MapLayerSpec, layer_source: Any) -> str:
        """Resolve a configured schema fallback and cache it briefly."""
        direct = _DIRECT_LAYER_SOURCES.get(layer.id)
        if direct is not None and layer_source is self._direct_sources.get(direct[0]):
            return direct[1]
        if layer.id == "census-sections" and layer_source is self.census_connection_source:
            return _CENSUS_SOURCE_RELATION
        if layer_source is self.hazards_connection_source and layer.id in _HAZARDS_SOURCE_RELATIONS:
            return _HAZARDS_SOURCE_RELATIONS[layer.id]
        if layer_source is self.egms_connection_source and layer.id in _EGMS_SOURCE_RELATIONS:
            return _EGMS_SOURCE_RELATIONS[layer.id]
        if layer.id == "anncsu-addresses" and layer_source is self.anncsu_connection_source:
            cached = self._resolved_relations.get(layer.id)
            if cached and cached[1] > time.monotonic():
                return cached[0]
            catalog = await self._anncsu_region_catalog(layer_source)
            relation = self._anncsu_union_relation(self._anncsu_ready_regions(catalog))
            self._resolved_relations[layer.id] = (relation, time.monotonic() + 300)
            return relation
        if layer.id == "corine-landcover" and layer_source is self.corine_connection_source:
            cached = self._resolved_relations.get(layer.id)
            if cached and cached[1] > time.monotonic():
                return cached[0]
            catalog = await self._corine_region_catalog(layer_source)
            relation = self._corine_union_relation(self._corine_ready_regions(catalog))
            self._resolved_relations[layer.id] = (relation, time.monotonic() + 300)
            return relation
        cached = self._resolved_relations.get(layer.id)
        if cached and cached[1] > time.monotonic():
            return cached[0]
        candidates = (layer.table, *layer.fallback_tables)
        try:
            async with layer_source.connection() as connection:
                for relation in candidates:
                    if await connection.fetchval("SELECT to_regclass($1)::text", relation):
                        self._resolved_relations[layer.id] = (relation, time.monotonic() + 300)
                        return relation
        except Exception as exc:
            log.debug("Could not resolve map relation for %s: %s", layer.id, exc)
        # Preserve the primary relation during outages; the actual request will
        # report its normal transient database error and the cache can retry.
        return layer.table

    @property
    def available(self) -> bool:
        return self.connection_source is not None

    async def close(self) -> None:
        sources = {
            id(source): source
            for source in (
                self.connection_source, self.census_connection_source,
                self.cadastral_connection_source, self.hazards_connection_source,
                self.egms_connection_source, self.anncsu_connection_source,
                self.corine_connection_source,
            )
            if source is not None
        }
        for source in sources.values():
            await source.close()

    async def health(self, layer_ids: Optional[set[str]] = None) -> list[dict[str, Any]]:
        """Return relation/index readiness for all layers or a selected subset.

        Readiness probes can request only the layers required for the app's
        core workflow, so a slow optional source cannot make the whole service
        look unavailable.
        """
        selected_layers = [
            layer for layer in MAP_LAYERS
            if layer_ids is None or layer.id in layer_ids
        ]
        if not self.available:
            return [{
                "id": layer.id, "available": False,
                "schema": layer.table.split(".", 1)[0],
                "schema_exists": None, "database_name": None,
                "source_database": "aecs4u-stats",
                "preflight_required": layer.preflight_required,
            } for layer in selected_layers]
        checks = []
        sql = _asyncpg_sql("""
            WITH relation AS (
                SELECT %s::text AS layer_id,
                       to_regclass(%s::text)::oid AS relation_oid,
                       %s::text AS schema_name,
                       %s::text AS table_name,
                       %s::text AS geometry_column,
                       %s::integer AS expected_srid
            )
            SELECT r.layer_id,
                   r.relation_oid IS NOT NULL AS relation_exists,
                   coalesce(c.reltuples, 0)::bigint AS row_estimate,
                   EXISTS (
                       SELECT 1 FROM pg_attribute a
                       WHERE a.attrelid = r.relation_oid
                         AND a.attname = r.geometry_column
                         AND a.attnum > 0
                         AND NOT a.attisdropped
                   ) AS geometry_column_exists,
                   EXISTS (
                       SELECT 1 FROM pg_indexes i
                       WHERE i.schemaname = r.schema_name
                         AND i.tablename = r.table_name
                         AND i.indexdef ILIKE '%%USING gist%%'
                   ) AS gist_index_exists,
                   COALESCE((
                       SELECT gc.srid
                       FROM geometry_columns gc
                       WHERE gc.f_table_schema = r.schema_name
                         AND gc.f_table_name = r.table_name
                         AND gc.f_geometry_column = r.geometry_column
                       LIMIT 1
                   ), 0)::integer AS geometry_srid,
                   to_regnamespace(r.schema_name) IS NOT NULL AS schema_exists,
                   current_database() AS database_name
            FROM relation r
            LEFT JOIN pg_class c ON c.oid = r.relation_oid
        """)
        extent_sql = """
            WITH estimated AS (
                SELECT public.ST_EstimatedExtent($1, $2, $3, TRUE)::geometry AS geom
            ), wgs84 AS (
                SELECT ST_Transform(ST_SetSRID(geom, $4), 4326) AS geom
                FROM estimated
                WHERE geom IS NOT NULL
            )
            SELECT ST_XMin(Box3D(geom)) AS west,
                   ST_YMin(Box3D(geom)) AS south,
                   ST_XMax(Box3D(geom)) AS east,
                   ST_YMax(Box3D(geom)) AS north
            FROM wgs84
        """
        for layer in selected_layers:
            schema = layer.table.split(".", 1)[0]
            source_database = "aecs4u-stats"
            try:
                layer_source = await self._layer_source_for(layer)
                if layer_source is None:
                    if layer.id == "corine-landcover":
                        source_database = "corine"
                    elif layer.id == "anncsu-addresses":
                        source_database = "anncsu"
                    checks.append({
                        "id": layer.id, "available": False, "schema": schema,
                        "schema_exists": None, "database_name": None,
                        "source_database": source_database,
                        "preflight_required": layer.preflight_required,
                    })
                    continue
                if layer_source is self.cadastral_connection_source:
                    source_database = "cadastral"
                elif layer_source is self.census_connection_source:
                    source_database = "census"
                elif layer_source is self.hazards_connection_source:
                    source_database = "hazards"
                elif layer_source is self.egms_connection_source:
                    source_database = "egms"
                elif layer_source is self.anncsu_connection_source:
                    source_database = "anncsu"
                elif layer_source is self.corine_connection_source:
                    source_database = "corine"
                else:
                    source_database = next(
                        (name for name, direct in self._direct_sources.items() if direct is layer_source),
                        source_database,
                    )
                if layer.id == "anncsu-addresses":
                    catalog = await self._anncsu_region_catalog(layer_source)
                    ready = self._anncsu_ready_regions(catalog)
                    async with layer_source.connection() as connection:
                        database_name = await connection.fetchval("SELECT current_database()")
                    region_codes = [int(row["region_code"]) for row in ready]
                    checks.append({
                        "id": layer.id,
                        "available": bool(ready),
                        "relation_exists": bool(catalog),
                        "schema": ",".join(sorted({str(row.get("table_schema") or "public") for row in catalog})) or "public",
                        "schema_exists": bool(catalog),
                        "database_name": database_name,
                        "source_database": "anncsu",
                        "geometry_column_exists": any(row.get("geometry_column_exists") for row in catalog),
                        "gist_index_exists": bool(ready),
                        "requires_gist_index": layer.require_gist_index,
                        "geometry_srid": 4258 if ready else next(
                            (int(row.get("geometry_srid") or 0) for row in catalog if row.get("geometry_srid")), 0
                        ),
                        "expected_srid": layer.source_srid,
                        "srid_matches": bool(ready),
                        "row_estimate": sum(int(row.get("row_estimate") or 0) for row in ready),
                        "coverage": layer.coverage,
                        "coverage_note": layer.coverage_note,
                        "coverage_bounds": None,
                        "regions": region_codes,
                        "preflight_required": layer.preflight_required,
                    })
                    continue
                if layer.id == "corine-landcover":
                    catalog = await self._corine_region_catalog(layer_source)
                    ready = self._corine_ready_regions(catalog)
                    async with layer_source.connection() as connection:
                        database_name = await connection.fetchval("SELECT current_database()")
                    region_codes = [int(row["region_code"]) for row in ready]
                    checks.append({
                        "id": layer.id,
                        "available": bool(ready),
                        "relation_exists": bool(catalog),
                        "schema": "public",
                        "schema_exists": bool(catalog),
                        "database_name": database_name,
                        "source_database": "corine",
                        "geometry_column_exists": any(row.get("geometry_column_exists") for row in catalog),
                        "gist_index_exists": bool(ready),
                        "requires_gist_index": layer.require_gist_index,
                        "geometry_srid": 3035 if ready else next(
                            (int(row.get("geometry_srid") or 0) for row in catalog if row.get("geometry_srid")), 0
                        ),
                        "expected_srid": layer.source_srid,
                        "srid_matches": bool(ready),
                        "row_estimate": sum(int(row.get("row_estimate") or 0) for row in ready),
                        "coverage": layer.coverage,
                        "coverage_note": layer.coverage_note,
                        "coverage_bounds": None,
                        "regions": region_codes,
                        "preflight_required": layer.preflight_required,
                    })
                    continue
                relation = await self._resolve_relation_for(layer, layer_source)
                schema, table = relation.split(".", 1)
                async with layer_source.connection() as connection:
                    if layer.id in CADASTRAL_LAYER_IDS and layer_source is self.cadastral_connection_source:
                        check = await regional_health(connection, layer.id, relation)
                        check.update({
                            "schema": schema,
                            "schema_exists": bool(await connection.fetchval(
                                "SELECT to_regnamespace($1::text) IS NOT NULL", schema
                            )),
                            "database_name": await connection.fetchval("SELECT current_database()"),
                            "source_database": source_database,
                        })
                        checks.append(check)
                        continue
                    row = await connection.fetchrow(
                        sql, layer.id, relation, schema, table, layer.geometry_column, layer.source_srid
                    )
                    values = tuple(row) if row else (layer.id, False, 0, False, False, 0)
                    schema_exists = (
                        None if len(values) <= 6 or values[6] is None else bool(values[6])
                    )
                    database_name = str(values[7]) if len(values) > 7 and values[7] else None
                    geometry_srid = int(values[5] or 0)
                    if layer.id == "solar-potential" and values[1]:
                        # The consolidated solar relation contains attributes
                        # only. The map query joins those rows to ISTAT
                        # municipality geometry, so validate the geometry side
                        # of that virtual spatial source here.
                        geometry_srid = int(await connection.fetchval(
                            "SELECT ST_SRID(geom) FROM public.v_comuni_map "
                            "WHERE geom IS NOT NULL LIMIT 1"
                        ) or 0)
                        values = (values[0], values[1], values[2], geometry_srid > 0, values[4], geometry_srid)
                    # PostGIS's geometry_columns view cannot always infer the
                    # SRID of a geometry column exposed by a SQL view (for
                    # example, serving.comuni). In that
                    # case, inspect one non-null geometry so a usable view is
                    # not incorrectly marked unavailable in the layer UI.
                    if geometry_srid == 0 and values[1] and values[3]:
                        geometry_srid = int(await connection.fetchval(
                            f"SELECT ST_SRID({layer.geometry_column}) "
                            f"FROM {relation} "
                            f"WHERE {layer.geometry_column} IS NOT NULL LIMIT 1"
                        ) or 0)
                    has_spatial_index = bool(values[4])
                    check = {
                        "id": values[0],
                        "available": bool(
                            values[1] and values[3]
                            and (has_spatial_index or not layer.require_gist_index)
                            and geometry_srid == layer.source_srid
                        ),
                        "relation_exists": bool(values[1]),
                        "schema": schema,
                        "schema_exists": schema_exists,
                        "database_name": database_name,
                        "source_database": source_database,
                        "geometry_column_exists": bool(values[3]),
                        "gist_index_exists": has_spatial_index,
                        "requires_gist_index": layer.require_gist_index,
                        "geometry_srid": geometry_srid,
                        "expected_srid": layer.source_srid,
                        "srid_matches": geometry_srid == layer.source_srid,
                        "row_estimate": int(values[2] or 0),
                        "coverage": layer.coverage if values[1] else "unavailable",
                        "coverage_note": layer.coverage_note,
                        "coverage_bounds": list(layer.coverage_bounds) if layer.coverage_bounds else None,
                        "preflight_required": layer.preflight_required,
                    }
                    if layer.id == "mef-irpef-income":
                        # The layer relation is the lr_map view over the MEF
                        # source (a foreign table), so a failure to read it
                        # means the MEF source is unreachable.
                        mef_data = None
                        if values[1]:
                            mef_data = await connection.fetchrow(
                                "SELECT max(tax_year)::integer AS tax_year, "
                                "count(*)::bigint AS usable_rows FROM " + relation
                            )
                        check["data_source_ready"] = bool(
                            mef_data and int(mef_data["usable_rows"] or 0) > 0
                        )
                        check["tax_year"] = int(mef_data["tax_year"]) if mef_data and mef_data["tax_year"] is not None else None
                        check["row_estimate"] = int(mef_data["usable_rows"] or 0) if mef_data else 0
                        check["available"] = check["available"] and check["data_source_ready"]
                    if check["available"] and layer.id in {"cadastral-sheets", "cadastral-parcels", "urban-sections"}:
                        try:
                            extent = await connection.fetchrow(
                                extent_sql, schema, table, layer.geometry_column, layer.source_srid
                            )
                            if extent and all(extent[key] is not None for key in ("west", "south", "east", "north")):
                                bounds = [float(extent[key]) for key in ("west", "south", "east", "north")]
                                if bounds[0] < bounds[2] and bounds[1] < bounds[3]:
                                    check["estimated_bounds"] = bounds
                                    check["extent_source"] = "PostGIS geometry statistics"
                        except Exception as exc:
                            # Extents are advisory; a stale/missing ANALYZE
                            # sample must not disable an otherwise healthy layer.
                            log.debug("Could not estimate coverage for %s: %s", layer.id, exc)
            except Exception as exc:
                # Keep one database outage from marking every independent
                # catalog layer unhealthy.
                log.warning("Map layer health check failed for %s: %s", layer.id, exc)
                checks.append({
                    "id": layer.id, "available": False, "schema": schema,
                    "schema_exists": None, "database_name": None,
                    "source_database": source_database,
                    "preflight_required": layer.preflight_required,
                })
                continue
            checks.append(check)
        return checks

    def _properties_for(self, layer: MapLayerSpec, layer_source: Any = None) -> tuple[str, ...]:
        if layer.id in CADASTRAL_LAYER_IDS and layer_source is not None and layer_source is self.cadastral_connection_source:
            return (*layer.properties, *REGIONAL_PROPERTIES)
        return layer.properties

    @staticmethod
    def _column_ref(alias: str, column: str) -> str:
        """Quote catalog-defined column names, including OGR's mixed-case IDs."""
        return f'{alias}."{column.replace(chr(34), chr(34) * 2)}"'

    @staticmethod
    def _source_columns(layer: MapLayerSpec, properties: Optional[tuple[str, ...]] = None) -> str:
        # Every identifier comes from MAP_LAYERS above, never from a request.
        return ", ".join(PostgresMapLayerSource._column_ref("t", column) for column in (properties or layer.properties))

    @staticmethod
    def _source_join(layer: MapLayerSpec) -> str:
        return ""

    @staticmethod
    def _query_relation(layer: MapLayerSpec, resolved_relation: str) -> str:
        if layer.id in _ADMIN_BOUNDARY_UNIT_TYPES:
            return _istat_boundary_relation()
        if layer.id in _PROTECTION_LAYER_TYPES:
            protection_type = _PROTECTION_LAYER_TYPES[layer.id]
            return f"(SELECT * FROM {resolved_relation} WHERE protection_type = '{protection_type}')"
        return resolved_relation

    async def available_protection_layer_ids(self) -> set[str]:
        """Expose optional protection map layers only after their schema is ready."""
        cached = self._protection_layer_ids_cache
        if cached and cached[1] > time.monotonic():
            return set(cached[0])
        available: set[str] = set()
        source = self._database_source("land_registry")
        if source is not None:
            try:
                async with source.connection() as connection:
                    relation = await connection.fetchval(
                        "SELECT to_regclass('spatial.protection_area')::text"
                    )
                    if relation:
                        required = {
                            "id", "protection_type", "category", "source", "source_id",
                            "source_label", "act_reference", "source_url", "is_binding",
                            "source_release", "geom",
                        }
                        columns = await connection.fetch(
                            """
                            SELECT attname::text AS name
                            FROM pg_attribute
                            WHERE attrelid = to_regclass('spatial.protection_area')
                              AND attnum > 0 AND NOT attisdropped
                            """
                        )
                        column_names = {row["name"] for row in columns}
                        if required.issubset(column_names):
                            # A layer is offered only once rows of its own type are
                            # loaded; an empty type would otherwise render a blank layer.
                            for layer_id, protection_type in _PROTECTION_LAYER_TYPES.items():
                                if await connection.fetchval(
                                    "SELECT EXISTS (SELECT 1 FROM spatial.protection_area "
                                    "WHERE protection_type = $1)",
                                    protection_type,
                                ):
                                    available.add(layer_id)
            except Exception as exc:
                log.debug("Protection map-layer readiness check failed: %s", exc)
        self._protection_layer_ids_cache = (available, time.monotonic() + 300)
        return set(available)

    async def read_mvt(self, layer_id: str, z: int, x: int, y: int) -> bytes:
        layer = get_map_layer(layer_id)
        layer_source = await self._layer_source_for(layer)
        if layer_source is None:
            return b""
        columns = self._source_columns(layer, self._properties_for(layer, layer_source))
        # Clip to the tile (plus the MVT buffer) and simplify to ~1/4 pixel in
        # the source SRID before transforming.  One hazard polygon has ~250k
        # vertices; transforming and intersecting whole geometries pushed
        # cold-cache tiles past the statement timeout.  ST_AsMVTGeom still
        # does the exact clip in tile space.
        tile_width = (360.0 if layer.source_srid in {4326, 4258} else 40075016.686) / (1 << z)
        geometry_ref = self._column_ref("t", layer.geometry_column)
        geometry = geometry_ref
        if layer.kind != "point":
            padding = tile_width * 64 / 4096
            tolerance = tile_width / 1024
            geometry = f"ST_Simplify(ST_ClipByBox2D({geometry}, ST_Expand(bounds.source, {padding!r})), {tolerance!r}, true)"
        else:
            padding = 0.0

        # Keep the foreign-table spatial restriction independent of the local
        # one-row bounds CTE. postgres_fdw cannot push a join against that CTE
        # to the remote server, which otherwise transfers the full concession
        # relation before applying the bbox. Inline only validated tile ints so
        # the remote planner sees a constant PostGIS envelope and can use the
        # source GiST index. `bounds` remains for MVT geometry construction.
        tile_envelope = f"ST_TileEnvelope({int(z)}, {int(x)}, {int(y)})"
        source_envelope = f"ST_Transform({tile_envelope}, {layer.source_srid})"
        spatial_filter = f"{geometry_ref} && ST_Expand({source_envelope}, {padding!r})"
        relation = self._query_relation(layer, await self._resolve_relation_for(layer, layer_source))
        level_filter = ""
        if layer.id in _ADMIN_BOUNDARY_UNIT_TYPES:
            level = _ADMIN_BOUNDARY_UNIT_TYPES[layer.id]
            level_filter = " AND t.unit_type = %s"
        truncation_cap: Optional[int] = None
        if layer.kind in {"mixed", "point"} and z < layer.cluster_below_zoom:
            # Dense views: summarise into grid clusters instead of truncating. Each feature
            # is assigned to exactly one tile (no padding) so no cluster is counted twice;
            # the grid is global, so cells line up across tiles.
            cell = tile_width / 8
            feature_ctes = f"""
                mvtgeom AS (
                    SELECT count(*)::integer AS point_count,
                           ST_AsMVTGeom(
                               ST_Transform(ST_SetSRID(ST_MakePoint(avg(ST_X(c.pt)), avg(ST_Y(c.pt))), {int(layer.source_srid)}), 3857),
                               bounds.tile, 4096, 64, true) AS geom
                    FROM (
                        SELECT ST_Centroid({geometry_ref}) AS pt
                        FROM {relation} AS t
                        WHERE {geometry_ref} && {source_envelope}
                    ) AS c CROSS JOIN bounds
                    GROUP BY ST_SnapToGrid(c.pt, {cell!r}), bounds.tile
                )
            """
            params = (layer.id,)
        elif layer.kind == "mixed":
            # Points and polygons share this foreign table. A single unordered
            # LIMIT can return only the first physical rows (currently mostly
            # CSV points) for a dense low-zoom tile. Fetch bounded candidates
            # from both classes, then spend the tile cap on polygons first so
            # points cannot hide the concession boundaries.
            column_names = ", ".join(layer.properties)
            # Sub-pixel footprints collapse to nothing in ST_AsMVTGeom. Drop
            # them in the remote-pushable filter so they neither cross the FDW
            # nor consume the tile cap. Source units are degrees (or metres
            # for projected sources); the Mercator pixel is never smaller than
            # this, so only polygons that would vanish are skipped.
            min_polygon_area = 0.25 * (tile_width / 4096) ** 2
            point_filter = ""
            polygon_keys_sql = "SELECT NULL::text AS idconc, NULL::text AS snapshot_id WHERE FALSE"
            if layer.id == "maritime-concessions":
                # Keep marker-only concessions, but do not place a marker over
                # a matching footprint that survives clipping/quantization.
                # Probe a deduplicated key CTE: anti-joining against the
                # geometry-bearing polygon CTE was planned as a nested loop
                # (points x polygons) and took ~4s on a dense z7 tile.
                point_filter = """
                    AND NOT EXISTS (
                        SELECT 1 FROM polygon_keys AS p
                        WHERE p.idconc = t.idconc AND p.snapshot_id = t.snapshot_id
                    )
                """
                polygon_keys_sql = "SELECT DISTINCT idconc, snapshot_id FROM polygon_features"
            feature_ctes = f"""
                polygon_features AS (
                    SELECT * FROM (
                        SELECT {columns},
                               ST_AsMVTGeom(ST_Transform({geometry}, 3857), bounds.tile, 4096, 64, true) AS geom
                        FROM {relation} AS t {self._source_join(layer)} CROSS JOIN bounds
                        WHERE {spatial_filter} AND ST_Dimension({geometry_ref}) = 2
                          AND ST_Area({geometry_ref}) >= {min_polygon_area!r}
                    ) AS clipped
                    WHERE geom IS NOT NULL
                    LIMIT %s
                ), polygon_keys AS (
                    {polygon_keys_sql}
                ), point_features AS (
                    SELECT {columns},
                           ST_AsMVTGeom(ST_Transform(t.{layer.geometry_column}, 3857), bounds.tile, 4096, 64, true) AS geom
                    FROM {relation} AS t {self._source_join(layer)} CROSS JOIN bounds
                    WHERE {spatial_filter} AND ST_Dimension({geometry_ref}) = 0
                    {point_filter}
                    LIMIT %s
                ), prioritized_features AS (
                    SELECT *, 0 AS _priority FROM polygon_features
                    UNION ALL
                    SELECT *, 1 AS _priority FROM point_features
                ), mvtgeom AS (
                    SELECT {column_names}, geom
                    FROM prioritized_features
                    WHERE geom IS NOT NULL
                    ORDER BY _priority
                    LIMIT %s
                )
            """
            params = (layer.max_features, layer.max_features, layer.max_features, layer.id)
        else:
            # Fetch one row past the cap so a truncated tile can say so (see below).
            truncation_cap = layer.max_features
            if layer.size_column:
                size_ref = self._column_ref("t", layer.size_column)
                needed = dict.fromkeys(
                    self._column_ref("t", column)
                    for column in (*self._properties_for(layer, layer_source), layer.geometry_column, layer.size_column)
                )
                # Drop sub-pixel features, then keep the largest if the cap is still hit.
                # The ranking runs on ids and sizes only; geometry is clipped and simplified
                # for the surviving rows in the outer query.
                feature_ctes = f"""
                    mvtgeom AS (
                        SELECT {columns},
                               ST_AsMVTGeom(ST_Transform({geometry}, 3857), bounds.tile, 4096, 64, true) AS geom
                        FROM (
                            SELECT {", ".join(needed)}
                            FROM {relation} AS t
                            WHERE {spatial_filter}{level_filter}
                              AND ({size_ref} IS NULL OR {size_ref} >= {_one_pixel_area_m2(z, y)!r})
                            ORDER BY {size_ref} DESC NULLS LAST
                            LIMIT %s
                        ) AS t CROSS JOIN bounds
                    )
                """
            else:
                feature_ctes = f"""
                    mvtgeom AS (
                        SELECT {columns},
                               ST_AsMVTGeom(ST_Transform({geometry}, 3857), bounds.tile, 4096, 64, true) AS geom
                        FROM {relation} AS t {self._source_join(layer)} CROSS JOIN bounds
                        WHERE {spatial_filter}{level_filter}
                        LIMIT %s
                    )
                """
            params = (
                (level, truncation_cap + 1, layer.id) if level_filter else (truncation_cap + 1, layer.id)
            )

        if truncation_cap is None:
            final_select = """
            SELECT COALESCE(ST_AsMVT(mvtgeom, %s::text, 4096, 'geom'), ''::bytea)
            FROM mvtgeom
            WHERE geom IS NOT NULL
            """
        else:
            # Truncation is never silent: when more rows matched than the cap allows, the tile
            # carries a one-point ``_truncated`` layer at its centre. The map reads it with
            # querySourceFeatures and tells the user to zoom in.
            final_select = f"""
            SELECT COALESCE(ST_AsMVT(visible, %s::text, 4096, 'geom'), ''::bytea)
                   || CASE WHEN (SELECT count(*) FROM mvtgeom) > {int(truncation_cap)}
                           THEN COALESCE((
                               SELECT ST_AsMVT(marker, '_truncated', 4096, 'geom')
                               FROM (SELECT ST_AsMVTGeom(ST_Centroid(bounds.tile), bounds.tile, 4096, 64, true) AS geom,
                                            {int(truncation_cap)} AS cap
                                     FROM bounds) AS marker), ''::bytea)
                           ELSE ''::bytea END
            FROM (SELECT * FROM mvtgeom WHERE geom IS NOT NULL LIMIT {int(truncation_cap)}) AS visible
            """
        sql = f"""
            WITH bounds AS (
                SELECT {tile_envelope} AS tile,
                       {source_envelope} AS source
            ), {feature_ctes}
            {final_select}
        """
        async with layer_source.connection() as connection:
            tile = await connection.fetchval(_asyncpg_sql(sql), *params)
        return tile or b""

    async def read_protection_constraints(self, parcel_geometry: dict[str, Any]) -> dict[str, Any]:
        """Return protection records and coverage for one canonical parcel."""
        unknown_coverage = {"landscape": "unknown", "cultural_heritage": "unknown"}
        unavailable = {
            "status": "unavailable",
            "constraints": [],
            "coverage": unknown_coverage,
            "truncated": False,
            "reason": "not_built",
            "source": "SITAP / Vincoli in Rete / Catalogo Generale (land_registry database)",
        }
        source = self._database_source("land_registry")
        if source is None:
            unavailable["reason"] = "source_unavailable"
            return unavailable

        geometry_json = json.dumps(parcel_geometry, separators=(",", ":"))
        async with source.connection() as connection:
            if not await connection.fetchval(
                "SELECT to_regclass('spatial.protection_area')::text"
            ):
                return unavailable
            rows = await connection.fetch(
                """
                WITH parcel AS (
                    SELECT ST_SetSRID(ST_GeomFromGeoJSON($1), 4326) AS geom
                )
                SELECT a.id, a.protection_type, a.category, a.source, a.source_id,
                       a.source_label, a.act_reference, a.source_url, a.is_binding,
                       a.source_release, normalized.geometry_valid,
                       CASE WHEN NOT normalized.geometry_valid
                                  OR ST_Dimension(normalized.geom) = 0 THEN NULL
                            ELSE ST_Area(ST_Transform(ST_Intersection(normalized.geom, p.geom), 3035))
                                 / NULLIF(ST_Area(ST_Transform(p.geom, 3035)), 0)
                       END AS overlap_ratio
                FROM spatial.protection_area AS a
                CROSS JOIN parcel AS p
                CROSS JOIN LATERAL (
                    SELECT ST_IsValid(a.geom) AS geometry_valid,
                           CASE WHEN ST_IsValid(a.geom) THEN a.geom ELSE ST_MakeValid(a.geom) END AS geom
                ) AS normalized
                WHERE a.protection_type IN ('landscape', 'cultural_heritage')
                  AND a.geom && p.geom
                  AND normalized.geom && p.geom
                  AND ST_Intersects(normalized.geom, p.geom)
                  AND NOT ST_Touches(normalized.geom, p.geom)
                ORDER BY a.protection_type, a.id
                LIMIT 101
                """,
                geometry_json,
            )

            coverage: dict[str, str] = dict(unknown_coverage)
            coverage_relation = await connection.fetchval(
                "SELECT to_regclass('spatial.protection_coverage')::text"
            )
            if coverage_relation:
                for protection_type in unknown_coverage:
                    covered = await connection.fetchval(
                        """
                        WITH parcel AS (
                            SELECT ST_SetSRID(ST_GeomFromGeoJSON($1), 4326) AS geom
                        )
                        SELECT COALESCE(bool_or(ST_Covers(c.geom, p.geom)), FALSE)
                        FROM spatial.protection_coverage AS c
                        CROSS JOIN parcel AS p
                        WHERE c.protection_type = $2
                        """,
                        geometry_json,
                        protection_type,
                    )
                    if covered:
                        coverage[protection_type] = "covered"

        truncated = len(rows) > 100
        constraints = [
            {
                key: (float(row[key]) if key == "overlap_ratio" and row[key] is not None else row[key])
                for key in (
                    "id", "protection_type", "category", "source", "source_id",
                    "source_label", "act_reference", "source_url", "is_binding",
                    "source_release", "geometry_valid", "overlap_ratio",
                )
            }
            for row in rows[:100]
        ]
        return {
            "status": "published",
            "constraints": constraints,
            "coverage": coverage,
            "truncated": truncated,
            "reason": None,
            "source": "SITAP / Vincoli in Rete / Catalogo Generale (land_registry database)",
        }

    async def read_geojson(
        self,
        layer_id: str,
        bbox: tuple[float, float, float, float],
        limit: int,
        offset: int = 0,
        flag: Optional[str] = None,
        zoom: Optional[int] = None,
    ) -> dict[str, Any]:
        """Read a bounded GeoJSON page; ``flag`` keeps only rows where that boolean column is true.

        The flag filter runs in the database so a client that wants the few
        flagged parcels in a viewport (for example auction sales) does not have
        to download and discard every parcel page. It is ignored for layers
        that do not expose the column.
        """
        layer = get_map_layer(layer_id)
        layer_source = await self._layer_source_for(layer)
        if layer_source is None:
            return {"type": "FeatureCollection", "features": []}
        west, south, east, north = (float(value) for value in bbox)
        properties = self._properties_for(layer, layer_source)
        zoom_required = geojson_zoom_required(layer, (west, south, east, north), zoom)
        if zoom_required is not None:
            return {"type": "FeatureCollection", "features": [], "zoom_required": zoom_required}
        columns = self._source_columns(layer, properties)
        relation = self._query_relation(layer, await self._resolve_relation_for(layer, layer_source))
        flag_clause = ""
        if flag in GEOJSON_FLAG_COLUMNS and flag in properties:
            flag_clause = f"AND {self._column_ref('t', flag)} IS TRUE"
        # Keep the foreign-table bbox independent of the bounds CTE so
        # postgres_fdw can ship it to the remote PostGIS source.
        source_bbox = (
            f"ST_Transform(ST_MakeEnvelope({west!r}, {south!r}, {east!r}, {north!r}, 4326), "
            f"{layer.source_srid})"
        )
        unit_type = _ADMIN_BOUNDARY_UNIT_TYPES.get(layer.id)
        unit_type_clause = "AND t.unit_type = %s" if unit_type else ""
        sql = f"""
            WITH bounds AS (
                SELECT ST_MakeEnvelope(%s, %s, %s, %s, 4326) AS web,
                       ST_Transform(ST_MakeEnvelope(%s, %s, %s, %s, 4326), {layer.source_srid}) AS source
            )
            SELECT {columns},
                   ST_AsGeoJSON(ST_Intersection(ST_Transform({self._column_ref('t', layer.geometry_column)}, 4326), bounds.web)) AS geometry
            FROM {relation} AS t {self._source_join(layer)} CROSS JOIN bounds
            WHERE {self._column_ref('t', layer.geometry_column)} && {source_bbox}
              AND ST_Intersects({self._column_ref('t', layer.geometry_column)}, bounds.source)
              {unit_type_clause}
              {flag_clause}
            ORDER BY {self._column_ref('t', layer.id_column)}
            LIMIT %s OFFSET %s
        """
        async with layer_source.connection() as connection:
            rows = await connection.fetch(
                _asyncpg_sql(sql),
                west, south, east, north, west, south, east, north,
                *([unit_type] if unit_type else []),
                min(max(int(limit), 1), layer.max_features), max(int(offset), 0),
            )
        features = []
        for row in rows:
            values = dict(row)
            geometry = values.pop("geometry")
            features.append({
                "type": "Feature",
                "id": values.get(layer.id_column),
                "properties": {key: _json_value(values.get(key)) for key in self._properties_for(layer, layer_source)},
                "geometry": json.loads(geometry) if geometry else None,
            })
        return {"type": "FeatureCollection", "features": features}

    async def read_sister_parcels(
        self, bbox: tuple[float, float, float, float], limit: int = 3000
    ) -> dict[str, Any]:
        """Return visible parcel polygons whose cadastral keys occur in SISTER.

        SISTER and the canonical parcel geometry can live in separate databases.
        Keep the map query spatially bounded, then match only the visible parcel
        keys against the read-only SISTER view in aecs4u-stats.
        """
        layer = get_map_layer("cadastral-parcels")
        stats_source = self.connection_source
        if stats_source is None:
            return {"available": False, "type": "FeatureCollection", "features": []}
        parcel_source = await self._layer_source_for(layer)
        if parcel_source is None:
            return {"available": False, "type": "FeatureCollection", "features": []}

        west, south, east, north = (float(value) for value in bbox)
        zoom_required = geojson_zoom_required(layer, (west, south, east, north))
        if zoom_required is not None:
            return {
                "available": True,
                "type": "FeatureCollection",
                "features": [],
                "zoom_required": zoom_required,
            }

        relation = self._query_relation(layer, await self._resolve_relation_for(layer, parcel_source))
        web_bbox = f"ST_MakeEnvelope({west!r}, {south!r}, {east!r}, {north!r}, 4326)"
        source_bbox = f"ST_Transform({web_bbox}, {layer.source_srid})"
        if parcel_source is self.cadastral_connection_source:
            # The dedicated cadastral database publishes one normalized union
            # view with SISTER's municipality/province names already attached.
            context_columns = (
                "t.province AS province, t.municipality_name AS municipality_name, "
                "t.municipality_code AS municipality_code"
            )
            context_joins = ""
        else:
            # The aecs4u-stats canonical table stores only the municipality ID;
            # resolve names through its administrative spine below.
            context_columns = (
                "u.canonical_name AS municipality_name, province.canonical_name AS province, "
                "NULL::text AS municipality_code"
            )
            context_joins = """
                LEFT JOIN geo.geo_unit AS u ON u.id = t.municipality_id
                LEFT JOIN geo.geo_relation AS province_relation
                  ON province_relation.child_id = u.id
                 AND province_relation.relation_type = 'contains'
                LEFT JOIN geo.geo_unit AS province
                  ON province.id = province_relation.parent_id
                 AND province.unit_type = 'province'
            """

        candidate_sql = f"""
            WITH bounds AS (
                SELECT ST_MakeEnvelope(%s, %s, %s, %s, 4326) AS web
            )
            SELECT t.id, t.canonical_reference, t.national_cadastral_reference,
                   t.parcel, t.sheet, t.source_release,
                   {context_columns},
                   ST_AsGeoJSON(ST_Intersection(ST_Transform(t.{layer.geometry_column}, 4326), bounds.web)) AS geometry
            FROM {relation} AS t {context_joins} CROSS JOIN bounds
            WHERE t.{layer.geometry_column} && {source_bbox}
              AND ST_Intersects(t.{layer.geometry_column}, {source_bbox})
            ORDER BY t.{layer.id_column}
            LIMIT %s
        """
        async with parcel_source.connection() as connection:
            candidate_rows = await connection.fetch(
                _asyncpg_sql(candidate_sql),
                west, south, east, north,
                layer.max_features + 1,
            )
        candidates_truncated = len(candidate_rows) > layer.max_features
        candidate_rows = candidate_rows[:layer.max_features]

        candidate_context = []
        missing_codes = set()
        for row in candidate_rows:
            values = dict(row)
            reference = values.get("national_cadastral_reference") or values.get("canonical_reference")
            code, section, ref_sheet, ref_parcel = _sister_reference_parts(reference)
            sheet = values.get("sheet") or ref_sheet
            parcel = values.get("parcel") or ref_parcel
            municipality_name = str(values.get("municipality_name") or "").strip()
            province = str(values.get("province") or "").strip()
            if code and (not municipality_name or not province):
                missing_codes.add(code)
            candidate_context.append({
                "row": values,
                "code": code,
                "section": section,
                "sheet": _sister_cadastral_values(sheet, sheet=True),
                "parcel": _sister_cadastral_values(str(parcel).split("/", 1)[0]),
                "municipality": municipality_name.casefold(),
                "province": province.casefold(),
            })

        sister_source = self._database_source("sister")
        names_source = self._database_source("eurostat")
        if sister_source is None:
            return {
                "available": False, "type": "FeatureCollection", "features": [],
                "count": 0, "source": _SISTER_PROPERTY_SOURCE,
            }
        async with sister_source.connection() as connection:
            sister_relation = await connection.fetchval(
                "SELECT to_regclass('sister_legacy.v_sister_property_by_cadastral_parcel') IS NOT NULL"
            )
            if not sister_relation:
                return {
                    "available": False,
                    "type": "FeatureCollection",
                    "features": [],
                    "count": 0,
                    "source": _SISTER_PROPERTY_SOURCE,
                }

            municipality_by_code = {}
            if missing_codes and names_source is not None:
                try:
                    # Municipality and province names come from the Eurostat
                    # source database; SISTER holds only the cadastral rows.
                    async with names_source.connection() as names_connection:
                        municipality_rows = await names_connection.fetch(_asyncpg_sql("""
                            SELECT upper(m.cadastral_code) AS cadastral_code,
                                   m.italian_name AS municipality_name,
                                   p.name AS province
                            FROM stats_export.municipalities AS m
                            LEFT JOIN stats_export.provinces AS p ON p.id = m.province_id
                            WHERE upper(m.cadastral_code) = ANY(%s)
                        """), sorted(missing_codes))
                    municipality_by_code = {
                        str(row["cadastral_code"]).upper(): (
                            str(row["municipality_name"] or "").strip().casefold(),
                            str(row["province"] or "").strip().casefold(),
                        )
                        for row in municipality_rows
                    }
                except Exception as exc:
                    # Dedicated regional rows already carry this context. If
                    # the Eurostat database is unreachable, those rows can
                    # still be matched while other candidates skip.
                    log.debug("SISTER map municipality context unavailable: %s", exc)

            for candidate in candidate_context:
                if not candidate["municipality"] or not candidate["province"]:
                    names = municipality_by_code.get(candidate["code"], ("", ""))
                    candidate["municipality"] = candidate["municipality"] or names[0]
                    candidate["province"] = candidate["province"] or names[1]

            provinces = sorted({item["province"] for item in candidate_context if item["province"]})
            municipalities = sorted({item["municipality"] for item in candidate_context if item["municipality"]})
            sheets = sorted({value for item in candidate_context for value in item["sheet"]})
            parcels = sorted({value for item in candidate_context for value in item["parcel"]})
            sections = sorted({item["section"].upper() for item in candidate_context})
            sister_rows = []
            if provinces and municipalities and sheets and parcels:
                section_exists = await connection.fetchval("""
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema = 'sister_legacy'
                          AND table_name = 'v_sister_property_by_cadastral_parcel'
                          AND column_name = 'section'
                    )
                """)
                section_select = "upper(trim(coalesce(section, '')))" if section_exists else "''::text"
                section_filter = (
                    "AND upper(trim(coalesce(section, ''))) = ANY(%s)"
                    if section_exists else ""
                )
                query = f"""
                    SELECT DISTINCT lower(trim(coalesce(province, ''))) AS province,
                           lower(trim(coalesce(municipality, ''))) AS municipality,
                           trim(coalesce(sheet, '')) AS sheet,
                           trim(split_part(coalesce(parcel, ''), '/', 1)) AS parcel,
                           {section_select} AS section
                    FROM sister_legacy.v_sister_property_by_cadastral_parcel
                    WHERE lower(trim(coalesce(province, ''))) = ANY(%s)
                      AND lower(trim(coalesce(municipality, ''))) = ANY(%s)
                      AND (trim(coalesce(sheet, '')) = ANY(%s)
                           OR ltrim(trim(coalesce(sheet, '')), '0') = ANY(%s))
                      AND (trim(split_part(coalesce(parcel, ''), '/', 1)) = ANY(%s)
                           OR ltrim(trim(split_part(coalesce(parcel, ''), '/', 1)), '0') = ANY(%s))
                      {section_filter}
                    LIMIT 50001
                """
                params: tuple[Any, ...] = (
                    provinces, municipalities, sheets, sheets, parcels, parcels,
                )
                if section_exists:
                    params += (sections,)
                sister_rows = await connection.fetch(_asyncpg_sql(query), *params)
                sister_rows_truncated = len(sister_rows) > 50000
                sister_rows = sister_rows[:50000]
            else:
                sister_rows_truncated = False

        sister_keys = set()
        for row in sister_rows:
            values = dict(row)
            for sheet_value in _sister_cadastral_values(values.get("sheet"), sheet=True):
                for parcel_value in _sister_cadastral_values(
                    str(values.get("parcel") or "").split("/", 1)[0]
                ):
                    sister_keys.add((
                        str(values.get("province") or "").casefold(),
                        str(values.get("municipality") or "").casefold(),
                        str(values.get("section") or "").upper(),
                        sheet_value,
                        parcel_value,
                    ))

        matches = []
        for candidate in candidate_context:
            key_prefix = (
                candidate["province"], candidate["municipality"], candidate["section"].upper()
            )
            if not candidate["province"] or not candidate["municipality"]:
                continue
            if any(
                (*key_prefix, sheet_value, parcel_value) in sister_keys
                for sheet_value in candidate["sheet"]
                for parcel_value in candidate["parcel"]
            ):
                row = candidate["row"]
                geometry = row.get("geometry")
                properties = {
                    "id": row.get("id"),
                    "canonical_reference": row.get("canonical_reference"),
                    "national_cadastral_reference": row.get("national_cadastral_reference"),
                    "parcel": row.get("parcel"),
                    "sheet": row.get("sheet"),
                    "municipality_name": row.get("municipality_name") or candidate["municipality"],
                    "province": row.get("province") or candidate["province"],
                    "source_release": row.get("source_release"),
                    "sister_listed": True,
                }
                matches.append({
                    "type": "Feature",
                    "id": row.get("id"),
                    "properties": {key: _json_value(value) for key, value in properties.items()},
                    "geometry": json.loads(geometry) if isinstance(geometry, str) else geometry,
                })

        matches.sort(key=lambda feature: str(feature.get("id", "")))
        feature_limit = min(max(int(limit), 1), layer.max_features)
        truncated = candidates_truncated or sister_rows_truncated or len(matches) > feature_limit
        return {
            "available": True,
            "type": "FeatureCollection",
            "features": matches[:feature_limit],
            "count": min(len(matches), feature_limit),
            "truncated": truncated,
            "source": _SISTER_PROPERTY_SOURCE,
        }

    async def read_npl_asteflorio_assets(
        self,
        bbox: tuple[float, float, float, float],
        *,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """Return NPL Aste Florio assets at their available municipality locations.

        The imported verdict views do not currently carry a cadastral parcel key
        or coordinates. Match their municipality/province labels to the shared
        municipality reference and expose one point per municipality. Keep this
        source adapter isolated so more precise parcel geometry can be used when
        the source view gains it.
        """
        verdict_source = self._database_source("verdict")
        geography_source = self._database_source("eurostat")
        if verdict_source is None or geography_source is None:
            return {
                "available": False,
                "type": "FeatureCollection",
                "features": [],
                "count": 0,
                "source": _NPL_SOURCE,
            }

        west, south, east, north = (float(value) for value in bbox)
        cached = self._npl_asset_features_cache
        if force_refresh or cached is None or cached[2] <= time.monotonic():
            async with verdict_source.connection() as connection:
                source_ready = await connection.fetchval("""
                    SELECT to_regclass('asteflorio.npl_asteflorio_asset_map_data') IS NOT NULL
                       AND to_regclass('asteflorio.npl_asteflorio_asset_map_summary') IS NOT NULL
                """)
                if not source_ready:
                    return {
                        "available": False,
                        "type": "FeatureCollection",
                        "features": [],
                        "count": 0,
                        "source": _NPL_SOURCE,
                    }
                asset_rows = [dict(row) for row in await connection.fetch("""
                    SELECT map_asset_id, position_id, asset_number, position_title,
                           position_type, municipality, province_code, asset_type,
                           description, cadastral_category, consistency_text, floor,
                           gbv_eur, detail_url, location_precision, parcel_key_available
                    FROM asteflorio.npl_asteflorio_asset_map_data
                    ORDER BY municipality, province_code, position_id, asset_number
                """)]
                summary_rows = [dict(row) for row in await connection.fetch("""
                    SELECT municipality, province_code, asset_count, position_count
                    FROM asteflorio.npl_asteflorio_asset_map_summary
                    ORDER BY municipality, province_code
                """)]
            # Municipality coordinates come from the Eurostat source database;
            # the labels are matched in Python, so no cross-database join.
            async with geography_source.connection() as connection:
                municipality_rows = [dict(row) for row in await connection.fetch("""
                    SELECT m.id, m.italian_name, p.vehicle_code AS province_code,
                           p.name AS province_name, m.latitude, m.longitude
                    FROM stats_export.municipalities AS m
                    LEFT JOIN stats_export.provinces AS p ON p.id = m.province_id
                    WHERE m.italian_name IS NOT NULL
                """)]

            municipality_candidates: dict[str, list[dict[str, Any]]] = {}
            for row in municipality_rows:
                candidate = dict(row)
                key = _municipality_name_key(candidate.get("italian_name"))
                if key:
                    municipality_candidates.setdefault(key, []).append(candidate)

            assets_by_source_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
            asset_fields = (
                "map_asset_id", "position_id", "asset_number", "position_title",
                "position_type", "asset_type", "description", "cadastral_category",
                "consistency_text", "floor", "gbv_eur", "detail_url",
                "location_precision", "parcel_key_available",
            )
            for row in asset_rows:
                source_key = (
                    _municipality_name_key(row.get("municipality")),
                    str(row.get("province_code") or "").strip().upper(),
                )
                assets_by_source_key.setdefault(source_key, []).append({
                    field: _json_value(row.get(field)) for field in asset_fields
                })

            features_by_municipality: dict[Any, dict[str, Any]] = {}
            unlocated_asset_count = 0
            for row in summary_rows:
                source_key = (
                    _municipality_name_key(row.get("municipality")),
                    str(row.get("province_code") or "").strip().upper(),
                )
                candidates = municipality_candidates.get(source_key[0], [])
                exact_province = [
                    candidate for candidate in candidates
                    if str(candidate.get("province_code") or "").strip().upper() == source_key[1]
                ]
                location = exact_province[0] if len(exact_province) == 1 else (
                    candidates[0] if len(candidates) == 1 else None
                )
                try:
                    latitude = float(location["latitude"]) if location and location["latitude"] is not None else None
                    longitude = float(location["longitude"]) if location and location["longitude"] is not None else None
                except (TypeError, ValueError):
                    latitude = longitude = None
                if (
                    location is None or latitude is None or longitude is None
                    or not (-90 <= latitude <= 90 and -180 <= longitude <= 180)
                ):
                    unlocated_asset_count += int(row.get("asset_count") or 0)
                    continue

                municipality_id = location["id"]
                feature = features_by_municipality.get(municipality_id)
                if feature is None:
                    feature = {
                        "type": "Feature",
                        "id": f"npl-asteflorio-municipality-{municipality_id}",
                        "properties": {
                            "municipality": location.get("italian_name"),
                            "province": location.get("province_name"),
                            "province_code": location.get("province_code"),
                            "asset_count": 0,
                            "position_count": 0,
                            "location_precision": "municipality_only",
                            "parcel_key_available": False,
                            "assets": [],
                        },
                        "geometry": {
                            "type": "Point",
                            "coordinates": [longitude, latitude],
                        },
                    }
                    features_by_municipality[municipality_id] = feature
                feature["properties"]["asset_count"] += int(row.get("asset_count") or 0)
                feature["properties"]["position_count"] += int(row.get("position_count") or 0)
                source_assets = assets_by_source_key.get(source_key, [])
                feature["properties"]["assets"].extend(source_assets)
                feature["properties"]["parcel_key_available"] = (
                    feature["properties"]["parcel_key_available"]
                    or any(bool(asset.get("parcel_key_available")) for asset in source_assets)
                )

            features = list(features_by_municipality.values())
            for feature in features:
                assets = feature["properties"]["assets"]
                assets.sort(key=lambda asset: (
                    str(asset.get("position_id") or ""),
                    str(asset.get("asset_number") or ""),
                ))
                precisions = {
                    str(asset.get("location_precision") or "").strip()
                    for asset in assets if asset.get("location_precision")
                }
                if len(precisions) == 1:
                    feature["properties"]["location_precision"] = next(iter(precisions))
                elif len(precisions) > 1:
                    feature["properties"]["location_precision"] = "mixed"
                feature["properties"]["assets"] = json.dumps(assets, ensure_ascii=False)
            cached = (features, unlocated_asset_count, time.monotonic() + 60)
            self._npl_asset_features_cache = cached

        features, unlocated_asset_count, _expires_at = cached
        visible_features = [
            feature for feature in features
            if west <= feature["geometry"]["coordinates"][0] <= east
            and south <= feature["geometry"]["coordinates"][1] <= north
        ]
        return {
            "available": True,
            "type": "FeatureCollection",
            "features": visible_features,
            "count": len(visible_features),
            "asset_count": sum(int(feature["properties"]["asset_count"]) for feature in visible_features),
            "unlocated_asset_count": unlocated_asset_count,
            "source": _NPL_SOURCE,
        }

    async def read_sister_batch_selection(
        self,
        *,
        parcel_ids: Optional[list[int]] = None,
        geometry: Optional[dict[str, Any]] = None,
        limit: int = 5000,
    ) -> dict[str, Any]:
        """Resolve selected parcels into the cadastral fields SISTER needs."""
        layer = get_map_layer("cadastral-parcels")
        layer_source = await self._layer_source_for(layer)
        if layer_source is None:
            return {"type": "FeatureCollection", "features": [], "truncated": False}
        ids = list(dict.fromkeys(int(value) for value in (parcel_ids or [])))
        if not ids and geometry is None:
            return {"type": "FeatureCollection", "features": [], "truncated": False}

        properties = self._properties_for(layer, layer_source)
        columns = self._source_columns(layer, properties)
        relation = self._query_relation(layer, await self._resolve_relation_for(layer, layer_source))
        geometry_ref = self._column_ref("t", layer.geometry_column)
        if layer_source is self.cadastral_connection_source:
            context_columns = (
                "t.province AS province, t.municipality_name AS municipality_name, "
                "t.municipality_code AS municipality_code"
            )
            context_joins = ""
        else:
            context_columns = (
                "u.canonical_name AS municipality_name, province.canonical_name AS province, "
                "NULL::text AS municipality_code"
            )
            context_joins = """
                LEFT JOIN geo.geo_unit AS u ON u.id = t.municipality_id
                LEFT JOIN geo.geo_relation AS province_relation
                  ON province_relation.child_id = u.id
                 AND province_relation.relation_type = 'contains'
                LEFT JOIN geo.geo_unit AS province
                  ON province.id = province_relation.parent_id
                 AND province.unit_type = 'province'
            """

        if geometry is not None:
            ring = geometry["coordinates"][0]
            longitudes = [float(point[0]) for point in ring]
            latitudes = [float(point[1]) for point in ring]
            west, south, east, north = min(longitudes), min(latitudes), max(longitudes), max(latitudes)
            source_bbox = (
                f"ST_Transform(ST_MakeEnvelope({west!r}, {south!r}, {east!r}, {north!r}, 4326), "
                f"{layer.source_srid})"
            )
            selection_geometry = (
                f"ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326), {layer.source_srid})"
            )
            selection_sql = ""
            predicate = f"{geometry_ref} && {source_bbox} AND ST_Intersects({geometry_ref}, {selection_geometry})"
            from_sql = f"{relation} AS t {context_joins}"
            params: tuple[Any, ...] = (json.dumps(geometry, separators=(",", ":")),)
        else:
            selection_sql = ""
            predicate = f"{self._column_ref('t', layer.id_column)} = ANY(%s)"
            from_sql = f"{relation} AS t {context_joins}"
            params = (ids,)

        sql = f"""
            {selection_sql}
            SELECT {columns}, {context_columns},
                   ST_AsGeoJSON(ST_Transform({geometry_ref}, 4326)) AS geometry
            FROM {from_sql}
            WHERE {predicate}
            ORDER BY {self._column_ref('t', layer.id_column)}
            LIMIT %s
        """
        feature_limit = min(max(int(limit), 1), 5000)
        async with layer_source.connection() as connection:
            rows = await connection.fetch(_asyncpg_sql(sql), *params, feature_limit + 1)
        truncated = len(rows) > feature_limit
        rows = rows[:feature_limit]
        features = []
        for row in rows:
            values = dict(row)
            raw_geometry = values.pop("geometry", None)
            feature_properties = {key: _json_value(values.get(key)) for key in properties}
            for key in ("province", "municipality_name", "municipality_code"):
                if values.get(key) is not None:
                    feature_properties[key] = _json_value(values[key])
            features.append({
                "type": "Feature",
                "id": values.get(layer.id_column),
                "properties": feature_properties,
                "geometry": json.loads(raw_geometry) if raw_geometry else None,
            })
        return {"type": "FeatureCollection", "features": features, "truncated": truncated}

    @staticmethod
    def visura_candidate_codes(features: list[dict[str, Any]]) -> list[str]:
        """Distinct cadastral municipality codes of parcel features (for name resolution)."""
        codes = set()
        for feature in features:
            properties = feature.get("properties") or {}
            reference = properties.get("national_cadastral_reference") or properties.get("canonical_reference")
            code = str(properties.get("municipality_code") or _sister_reference_parts(reference)[0] or "").upper()
            if code:
                codes.add(code)
        return sorted(codes)

    async def read_visura_flags(
        self,
        features: list[dict[str, Any]],
        names_by_code: dict[str, tuple[str, tuple[str, ...]]],
    ) -> Optional[dict[Any, bool]]:
        """Map each cadastral parcel feature id to whether SISTER holds a property visura for it.

        Only visure per immobile count (``visura_fabbricati``, ``visura_terreni`` and the
        untyped ``visura``); subject visure and plans do not describe the parcel itself.
        ``names_by_code`` maps a cadastral municipality code to ``(municipality, province
        spellings)``. The regional parcel tables carry the province as a code (``PA``)
        and the SISTER views are not consistent (documents use the code, properties the
        name), so parcels are matched on the municipality, any accepted province spelling,
        section, sheet and parcel. Parcels whose municipality has no names are left out
        of the result. Returns ``None`` when SISTER cannot be consulted so callers can
        tell "unknown" from "no visura".
        """
        stats_source = self._database_source("sister")
        if stats_source is None or not features:
            return None

        resolved = {
            code.upper(): (
                municipality.strip().casefold(),
                frozenset(value.strip().casefold() for value in provinces if value and value.strip()),
            )
            for code, (municipality, provinces) in names_by_code.items()
            if municipality and any(value and value.strip() for value in provinces)
        }
        candidates = []
        for feature in features:
            properties = feature.get("properties") or {}
            reference = properties.get("national_cadastral_reference") or properties.get("canonical_reference")
            code, section, ref_sheet, ref_parcel = _sister_reference_parts(reference)
            code = str(properties.get("municipality_code") or code or "").upper()
            if code not in resolved:
                continue
            candidates.append({
                "id": feature.get("id", properties.get("id")),
                "municipality": resolved[code][0],
                "provinces": resolved[code][1],
                "section": section.upper(),
                "sheet": _sister_cadastral_values(properties.get("sheet") or ref_sheet, sheet=True),
                "parcel": _sister_cadastral_values(str(properties.get("parcel") or ref_parcel).split("/", 1)[0]),
            })
        if not candidates:
            return None

        provinces = sorted({value for item in candidates for value in item["provinces"]})
        municipalities = sorted({item["municipality"] for item in candidates})
        sheets = sorted({value for item in candidates for value in item["sheet"]})
        parcels = sorted({value for item in candidates for value in item["parcel"]})
        async with stats_source.connection() as connection:
            if not await connection.fetchval(
                "SELECT to_regclass('sister_legacy.v_sister_document_by_cadastral_parcel') IS NOT NULL"
            ):
                return None
            document_rows = await connection.fetch(_asyncpg_sql("""
                SELECT DISTINCT lower(trim(coalesce(province, ''))) AS province,
                       lower(trim(coalesce(municipality, ''))) AS municipality,
                       upper(trim(coalesce(section, ''))) AS section,
                       trim(coalesce(sheet, '')) AS sheet,
                       trim(split_part(coalesce(parcel, ''), '/', 1)) AS parcel
                FROM sister_legacy.v_sister_document_by_cadastral_parcel
                WHERE document_type = ANY(%s)
                  AND lower(trim(coalesce(province, ''))) = ANY(%s)
                  AND lower(trim(coalesce(municipality, ''))) = ANY(%s)
                  AND (trim(coalesce(sheet, '')) = ANY(%s)
                       OR ltrim(trim(coalesce(sheet, '')), '0') = ANY(%s))
                  AND (trim(split_part(coalesce(parcel, ''), '/', 1)) = ANY(%s)
                       OR ltrim(trim(split_part(coalesce(parcel, ''), '/', 1)), '0') = ANY(%s))
            """), list(_VISURA_DOCUMENT_TYPES), provinces, municipalities, sheets, sheets, parcels, parcels)

        keys = set()
        for row in document_rows:
            for sheet_value in _sister_cadastral_values(row["sheet"], sheet=True):
                for parcel_value in _sister_cadastral_values(row["parcel"]):
                    keys.add((row["province"], row["municipality"], row["section"], sheet_value, parcel_value))

        return {
            item["id"]: any(
                (province, item["municipality"], item["section"], sheet_value, parcel_value) in keys
                for province in item["provinces"]
                for sheet_value in item["sheet"]
                for parcel_value in item["parcel"]
            )
            for item in candidates
        }

    async def read_feature_at_point(self, layer_id: str, lat: float, lng: float) -> Optional[dict[str, Any]]:
        """Return the polygon containing a WGS84 point as a GeoJSON feature."""

        layer = get_map_layer(layer_id)
        layer_source = await self._layer_source_for(layer)
        if layer_source is None:
            return None
        columns = self._source_columns(layer, self._properties_for(layer, layer_source))
        relation = self._query_relation(layer, await self._resolve_relation_for(layer, layer_source))
        point = f"ST_SetSRID(ST_Point(%s, %s), 4326)"
        source_point = f"ST_Transform({point}, {layer.source_srid})"
        source_point_filter = (
            f"ST_Transform(ST_SetSRID(ST_Point({float(lng)!r}, {float(lat)!r}), 4326), "
            f"{layer.source_srid})"
        )
        unit_type = _ADMIN_BOUNDARY_UNIT_TYPES.get(layer.id)
        unit_type_clause = "AND t.unit_type = %s" if unit_type else ""
        sql = f"""
            WITH click AS (
                SELECT {point} AS web, {source_point} AS source
            )
            SELECT {columns},
                   ST_AsGeoJSON(ST_Transform({self._column_ref('t', layer.geometry_column)}, 4326)) AS geometry
            FROM {relation} AS t {self._source_join(layer)} CROSS JOIN click
            WHERE {self._column_ref('t', layer.geometry_column)} && {source_point_filter}
              AND ST_Covers({self._column_ref('t', layer.geometry_column)}, click.source)
              {unit_type_clause}
            LIMIT 1
        """
        async with layer_source.connection() as connection:
            params = [float(lng), float(lat), float(lng), float(lat)]
            if unit_type:
                params.append(unit_type)
            row = await connection.fetchrow(_asyncpg_sql(sql), *params)
        if not row:
            return None
        values = dict(row)
        geometry = values.pop("geometry", None)
        return {
            "type": "Feature",
            "id": values.get(layer.id_column),
            "properties": {key: _json_value(values.get(key)) for key in self._properties_for(layer, layer_source)},
            "geometry": json.loads(geometry) if geometry else None,
        }

    async def read_feature_by_reference(
        self, layer_id: str, reference: str, feature_id: Optional[int] = None
    ) -> Optional[dict[str, Any]]:
        """Return one cadastral feature by its canonical parcel reference."""

        layer = get_map_layer(layer_id)
        layer_source = await self._layer_source_for(layer)
        if layer_source is None:
            return None
        if layer.id != "cadastral-parcels":
            raise ValueError("Reference lookup is only supported for cadastral parcels")
        columns = self._source_columns(layer, self._properties_for(layer, layer_source))
        # Panel-facing extras: whole regional loads ship without area_sqm, and
        # the municipality is otherwise only an opaque geo_unit id.
        extras = (
            f"ST_AsGeoJSON(ST_Transform(t.{layer.geometry_column}, 4326)) AS geometry, "
            f"ST_Area(t.{layer.geometry_column}::geography) AS computed_area_sqm, "
            "(SELECT u.canonical_name FROM geo.geo_unit AS u WHERE u.id = t.municipality_id) AS municipality_name"
        )
        normalized = str(reference).strip()
        if layer_source is self.cadastral_connection_source:
            # The dedicated database stores names directly and has no geo_unit
            # spine. Its expression indexes support both IDs and references.
            normalized = normalized.upper()
            extras = (
                f"ST_AsGeoJSON(t.{layer.geometry_column}) AS geometry, "
                f"COALESCE(t.area_sqm, ST_Area(t.{layer.geometry_column}::geography)) AS computed_area_sqm"
            )
            predicate = f"t.id = {identity_sql(layer.id, '%s::text')} AND t.national_cadastral_reference = %s"
            params: tuple[Any, ...] = (normalized, normalized)
            if feature_id is not None:
                predicate += " AND t.id = %s"
                params += (int(feature_id),)
            sql = f"SELECT {columns}, {extras} FROM {layer.table} AS t WHERE {predicate} LIMIT 1"
        elif feature_id is not None:
            # A clicked vector-tile feature already carries its primary key.
            # The reference columns have no index in aecs4u-stats, so an OR
            # lookup over them is a sequential scan of every parcel; the PK
            # lookup is instant, and the reference check guards stale ids.
            sql = f"""
                SELECT {columns}, {extras}
                FROM {layer.table} AS t
                WHERE t.{layer.id_column} = %s
                  AND (t.national_cadastral_reference = %s OR t.canonical_reference = %s)
                LIMIT 1
            """
            params = (int(feature_id), normalized, normalized)
        else:
            sql = f"""
                SELECT {columns}, {extras}
                FROM {layer.table} AS t
                WHERE t.national_cadastral_reference = %s
                   OR t.canonical_reference = %s
                LIMIT 1
            """
            params = (normalized, normalized)
        async with layer_source.connection() as connection:
            row = await connection.fetchrow(_asyncpg_sql(sql), *params)
        if not row:
            return None
        values = dict(row)
        geometry = values.pop("geometry", None)
        properties = {key: _json_value(values.get(key)) for key in self._properties_for(layer, layer_source)}
        for key in ("computed_area_sqm", "municipality_name"):
            if values.get(key) is not None:
                properties[key] = _json_value(values[key])
        return {
            "type": "Feature",
            "id": values.get(layer.id_column),
            "properties": properties,
            "geometry": json.loads(geometry) if geometry else None,
        }

    async def search_parcels_by_reference(self, reference: str, limit: int = 10) -> list[dict[str, Any]]:
        """Return every parcel polygon matching an exact cadastral reference.

        This query is intentionally exact and bounded, and depends on the
        canonical reference indexes installed by scripts/sql/map-reference-indexes.sql.
        """
        layer = get_map_layer("cadastral-parcels")
        layer_source = await self._layer_source_for(layer)
        if layer_source is None:
            return []
        normalized = str(reference or "").strip().upper()
        if layer_source is self.cadastral_connection_source:
            sql = f"""
                SELECT id, canonical_reference, national_cadastral_reference,
                       parcel, sheet, municipality_id, municipality_name
                FROM {layer.table}
                WHERE id = {identity_sql(layer.id, '%s::text')}
                  AND national_cadastral_reference = %s
                LIMIT %s
            """
            async with layer_source.connection() as connection:
                rows = await connection.fetch(_asyncpg_sql(sql), normalized, normalized, min(max(int(limit), 1), 20))
            return [_json_row(row) for row in rows]
        # Resolve matches through the two reference indexes first. A single
        # "a = x OR b = y ORDER BY id LIMIT n" lets the planner walk the
        # primary key instead whenever column statistics are missing or stale
        # (observed on the unanalyzed Veneto load: a full scan past the 2 s
        # search timeout). Each UNION branch is an indexed equality lookup.
        sql = f"""
            WITH matches AS MATERIALIZED (
                SELECT {layer.id_column} AS id FROM {layer.table} WHERE canonical_reference = %s
                UNION
                SELECT {layer.id_column} AS id FROM {layer.table} WHERE national_cadastral_reference = %s
            )
            SELECT t.{layer.id_column} AS id, t.canonical_reference,
                   t.national_cadastral_reference, t.parcel, t.sheet,
                   t.municipality_id, u.canonical_name AS municipality_name
            FROM matches
            JOIN {layer.table} AS t ON t.{layer.id_column} = matches.id
            LEFT JOIN geo.geo_unit AS u ON u.id = t.municipality_id
            ORDER BY t.{layer.id_column}
            LIMIT %s
        """
        async with layer_source.connection() as connection:
            rows = await connection.fetch(
                _asyncpg_sql(sql), normalized, normalized, min(max(int(limit), 1), 20)
            )
        return [_json_row(row) for row in rows]

    async def read_adjacent_features(
        self, layer_id: str, reference: str, limit: int = 25, feature_id: Optional[int] = None,
        method: str = "intersects",
    ) -> list[dict[str, Any]]:
        """Return parcels adjacent to the parcel identified by ``reference``."""

        layer = get_map_layer(layer_id)
        layer_source = await self._layer_source_for(layer)
        if layer_source is None:
            return []
        if layer.id != "cadastral-parcels":
            raise ValueError("Adjacency lookup is only supported for cadastral parcels")
        predicates = {
            "touches": "ST_Touches",
            "intersects": "ST_Intersects",
            "overlaps": "ST_Overlaps",
        }
        predicate = predicates.get(method)
        if predicate is None:
            raise ValueError("Unsupported adjacency method")
        columns = self._source_columns(layer, self._properties_for(layer, layer_source))
        normalized = str(reference).strip()
        # Same primary-key fast path as read_feature_by_reference: the
        # reference columns are unindexed, so resolving the target by them is
        # a full scan that routinely hits the statement timeout.
        if layer_source is self.cadastral_connection_source:
            normalized = normalized.upper()
            target_predicate = f"id = {identity_sql(layer.id, '%s::text')} AND national_cadastral_reference = %s"
            target_params: tuple[Any, ...] = (normalized, normalized)
            if feature_id is not None:
                target_predicate += " AND id = %s"
                target_params += (int(feature_id),)
        elif feature_id is not None:
            target_predicate = (
                f"{layer.id_column} = %s AND (national_cadastral_reference = %s OR canonical_reference = %s)"
            )
            target_params = (int(feature_id), normalized, normalized)
        else:
            target_predicate = "national_cadastral_reference = %s OR canonical_reference = %s"
            target_params = (normalized, normalized)
        sql = f"""
            WITH target AS (
                SELECT {layer.id_column} AS target_id, {layer.geometry_column} AS geom
                FROM {layer.table}
                WHERE {target_predicate}
                LIMIT 1
            )
            SELECT {columns},
                   ST_AsGeoJSON(ST_Transform(t.{layer.geometry_column}, 4326)) AS geometry
            FROM {layer.table} AS t, target
            WHERE t.{layer.geometry_column} && target.geom
              AND {predicate}(t.{layer.geometry_column}, target.geom)
              AND t.{layer.id_column} <> target.target_id
            LIMIT %s
        """
        # Exclude the target by primary key: national_cadastral_reference is
        # NULL for whole regional loads, and NULL IS DISTINCT FROM 'x' is true,
        # so a reference-based exclusion returned the parcel as its own neighbour.
        async with layer_source.connection() as connection:
            rows = await connection.fetch(
                _asyncpg_sql(sql), *target_params, min(max(int(limit), 1), 50),
            )
        features = []
        for row in rows:
            values = dict(row)
            geometry = values.pop("geometry", None)
            features.append({
                "type": "Feature",
                "id": values.get(layer.id_column),
                "properties": {key: _json_value(values.get(key)) for key in self._properties_for(layer, layer_source)},
                "geometry": json.loads(geometry) if geometry else None,
            })
        return features

    async def read_parcel_by_id(self, feature_id: int) -> Optional[dict[str, Any]]:
        """Return one cadastral parcel, including WGS84 geometry, by stable map ID."""
        layer = get_map_layer("cadastral-parcels")
        layer_source = await self._layer_source_for(layer)
        if layer_source is None:
            return None
        columns = self._source_columns(layer, self._properties_for(layer, layer_source))
        relation = self._query_relation(layer, await self._resolve_relation_for(layer, layer_source))
        geometry_ref = self._column_ref("t", layer.geometry_column)
        if layer_source is self.cadastral_connection_source:
            geometry = f"ST_AsGeoJSON({geometry_ref})"
            computed_area = f"COALESCE(t.area_sqm, ST_Area({geometry_ref}::geography))"
            municipality_name = "t.municipality_name"
        else:
            geometry = f"ST_AsGeoJSON(ST_Transform({geometry_ref}, 4326))"
            computed_area = f"ST_Area({geometry_ref}::geography)"
            municipality_name = (
                "(SELECT u.canonical_name FROM geo.geo_unit AS u "
                "WHERE u.id = t.municipality_id)"
            )
        sql = f"""
            SELECT {columns},
                   {geometry} AS geometry,
                   {computed_area} AS computed_area_sqm,
                   {municipality_name} AS municipality_name
            FROM {relation} AS t
            WHERE t.{layer.id_column} = %s
            LIMIT 1
        """
        async with layer_source.connection() as connection:
            row = await connection.fetchrow(_asyncpg_sql(sql), int(feature_id))
        if not row:
            return None
        values = dict(row)
        geometry_value = values.pop("geometry", None)
        properties = {
            key: _json_value(values.get(key))
            for key in self._properties_for(layer, layer_source)
        }
        if values.get("computed_area_sqm") is not None:
            properties["area_sqm"] = _json_value(values["computed_area_sqm"])
        if values.get("municipality_name") is not None:
            properties["municipality_name"] = _json_value(values["municipality_name"])
        return {
            "type": "Feature",
            "id": values.get(layer.id_column),
            "properties": properties,
            "geometry": json.loads(geometry_value) if geometry_value else None,
        }

    async def read_feature_details(self, layer_id: str, feature_id: int) -> Optional[dict[str, Any]]:
        """Return one allow-listed feature and related maritime records."""

        layer = get_map_layer(layer_id)
        layer_source = await self._layer_source_for(layer)
        if layer_source is None:
            return None
        columns = self._source_columns(layer, self._properties_for(layer, layer_source))
        relation = await self._resolve_relation_for(layer, layer_source)
        async with layer_source.connection() as connection:
            try:
                row = await connection.fetchrow(
                    _asyncpg_sql(
                        f"select {columns} from {relation} t "
                        f"where {self._column_ref('t', layer.id_column)} = %s limit 1"
                    ),
                    feature_id,
                )
            except Exception as exc:
                if getattr(exc, "sqlstate", None) == "57014":
                    log.warning(
                        "Canonical map feature row lookup timed out for %s/%s: %s",
                        layer_id,
                        feature_id,
                        exc,
                    )
                raise
            if not row:
                return None
            result: dict[str, Any] = {
                "layer": layer.id,
                "id": feature_id,
                "properties": _json_row(row),
                "related": {},
            }
            if layer_id != "maritime-concessions":
                return result
            result["source"] = "agenziademanio"

            async def related_rows(sql: str, *params: Any) -> list[dict[str, Any]]:
                try:
                    return [_json_row(item) for item in await connection.fetch(_asyncpg_sql(sql), *params)]
                except Exception as exc:
                    # The concession layer can be published before its
                    # optional document-enrichment relations are provisioned.
                    log.debug("Related concession data unavailable: %s", exc)
                    return []

            snapshot_id = row.get("snapshot_id")
            idconc = row.get("idconc")
            source_row_id = row.get("row_id") or feature_id
            if snapshot_id is None:
                return result
            source_schema = relation.split(".", 1)[0]
            details = None
            try:
                detail_row = await connection.fetchrow(
                    _asyncpg_sql(
                        "select details from agenziademanio.v_concession_details "
                        "where snapshot_id = %s and row_id = %s limit 1"
                    ),
                    snapshot_id,
                    source_row_id,
                )
                if detail_row:
                    details = detail_row.get("details")
                    if isinstance(details, str):
                        details = json.loads(details)
            except Exception as exc:
                # Older Stats installations still expose the component foreign
                # tables; keep them usable while the merged detail view rolls out.
                log.debug("Merged concession detail view unavailable: %s", exc)

            if isinstance(details, dict):
                result["related"] = {}
                for key in (
                    "source_concession", "source_attributes", "source_geometry",
                    "snapshot", "resources", "administrative_assignment",
                    "document_summary", "document_gaps", "sicily_contracts",
                    "online_document_matches", "online_documents",
                    "institution_request_targets", "regional_source_audits",
                ):
                    value = details.get(key)
                    if value is None:
                        continue
                    result["related"][key] = value if isinstance(value, list) else [value]
            else:
                result["related"]["document_gaps"] = await related_rows(
                    "select * "
                    f"from {source_schema}.v_document_gaps "
                    "where source_row_id = %s order by gap_id",
                    source_row_id,
                )
                result["related"]["sicily_contracts"] = await related_rows(
                    "select * "
                    f"from {source_schema}.v_sicily_contracts "
                    "where source_row_id = %s order by row_id",
                    source_row_id,
                )
                matches = await related_rows(
                    "select * "
                    f"from {source_schema}.v_online_document_matches "
                    "where snapshot_id = %s and idconc = %s order by match_id",
                    snapshot_id,
                    idconc,
                ) if idconc is not None else []
                result["related"]["online_document_matches"] = matches
                urls = [item["document_url"] for item in matches if item.get("document_url")]
                institution_keys = sorted({
                    str(item["institution_key"])
                    for group in (
                        result["related"]["document_gaps"],
                        result["related"]["sicily_contracts"],
                    )
                    for item in group
                    if item.get("institution_key")
                })
                result["related"]["institution_request_targets"] = await related_rows(
                    "select * "
                    f"from {source_schema}.v_institution_request_targets "
                    "where snapshot_id = %s and institution_key = any(%s::text[]) "
                    "order by institution_key",
                    snapshot_id,
                    institution_keys,
                ) if institution_keys else []
                result["related"]["online_documents"] = await related_rows(
                    "select * "
                    f"from {source_schema}.v_online_documents "
                    "where snapshot_id = %s "
                    "and (document_url = any(%s::text[]) or institution_key = any(%s::text[])) "
                    "order by institution_key, document_url",
                    snapshot_id,
                    urls,
                    institution_keys,
                ) if urls or institution_keys else []
                result["related"]["snapshot"] = await related_rows(
                    "select * "
                    f"from {source_schema}.v_snapshots where snapshot_id = %s",
                    snapshot_id,
                )
                result["related"]["resources"] = await related_rows(
                    "select * "
                    f"from {source_schema}.v_resources "
                    "where snapshot_id = %s order by resource_id",
                    snapshot_id,
                )
                result["related"]["regional_source_audits"] = await related_rows(
                    "select * "
                    f"from {source_schema}.v_regional_source_audits "
                    "where snapshot_id = %s order by region_name, source_url",
                    snapshot_id,
                )

            # Spatial parcel links are source-owned in Agenzia and exposed in
            # the layer database.
            result["related"]["cadastral_parcels"] = await related_rows(
                "select * from agenziademanio.concession_parcel_links "
                "where snapshot_id = %s and concession_row_id = %s "
                "order by canonical_reference, parcel_id",
                snapshot_id,
                source_row_id,
            )
            return result

    async def read_istat_municipality_details(
        self, istat_code: str | int
    ) -> Optional[dict[str, Any]]:
        """Read the full Stats-side ISTAT municipality serving view.

        ``stats_export.elenco_comuni`` in the ISTAT database is the stable
        view over the active ``elenco_comuni`` release. Match both the text code
        and its numeric representation because map boundaries expose PRO_COM
        without leading zeroes while the source preserves the official code.
        """
        istat_source = self._database_source("istat")
        if istat_source is None:
            return None
        try:
            code = str(int(str(istat_code).strip()))
        except (TypeError, ValueError):
            return None
        if not code.isdigit() or int(code) <= 0 or int(code) > 999999:
            return None

        sql = """
            SELECT t.*
            FROM stats_export.elenco_comuni AS t
            WHERE ltrim(trim(t.code), '0') = ltrim(%s::text, '0')
               OR t.numeric_code = %s
            ORDER BY CASE WHEN t.code = %s THEN 0 ELSE 1 END
            LIMIT 1
        """
        async with istat_source.connection() as connection:
            row = await connection.fetchrow(_asyncpg_sql(sql), code, int(code), code)
        if row is None:
            return None

        municipality = _json_row(row)

        def related_record(keys: tuple[str, ...]) -> Optional[dict[str, Any]]:
            values = {key: municipality[key] for key in keys if key in municipality}
            return values if any(value is not None and value != "" for value in values.values()) else None

        return {
            "source": "istat stats_export.elenco_comuni",
            "municipality": municipality,
            "province": related_record((
                "uts_code", "uts_name", "uts_type", "province_abbreviation",
                "nuts3_2021", "nuts3_2024",
            )),
            "region": related_record(("region_code", "region_name", "nuts2_2021", "nuts2_2024")),
            "geographic_area": related_record(("area_code", "area_name")),
            "population_history": [],
        }

    async def search_municipalities(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Search the canonical ISTAT municipality map relation.

        This is intentionally separate from ``read_geojson``: place search
        must remain useful before the map reaches parcel zoom, and it should
        return compact centroids rather than geometry for whole areas.
        """
        istat_source = self._database_source("istat")
        if not istat_source:
            return []
        normalized = str(query or "").strip()
        if len(normalized) < 2:
            return []
        sql = f"""
            SELECT (3000000000 + t.pro_com)::bigint AS id,
                   t.pro_com::bigint AS geo_unit_id,
                   t.name AS canonical_name,
                   t.pro_com::text AS istat_code,
                   ST_Y(ST_Centroid(t.geom)) AS latitude,
                   ST_X(ST_Centroid(t.geom)) AS longitude,
                   ST_XMin(Box2D(ST_Transform(t.geom, 4326))) AS west,
                   ST_YMin(Box2D(ST_Transform(t.geom, 4326))) AS south,
                   ST_XMax(Box2D(ST_Transform(t.geom, 4326))) AS east,
                   ST_YMax(Box2D(ST_Transform(t.geom, 4326))) AS north
            FROM public.v_comuni_map AS t
            WHERE t.name ILIKE %s
               OR t.pro_com::text ILIKE %s
            ORDER BY CASE
                WHEN lower(t.name) = lower(%s) THEN 0
                WHEN lower(t.name) LIKE lower(%s) THEN 1
                ELSE 2
            END, t.name ASC
            LIMIT %s
        """
        pattern = f"%{normalized}%"
        prefix = f"{normalized}%"
        # Bind order follows the placeholders above: substring name match,
        # ISTAT-code prefix, then exact-name and name-prefix ranking.
        async with istat_source.connection() as connection:
            rows = await connection.fetch(
                _asyncpg_sql(sql), pattern, prefix, normalized, prefix, min(max(int(limit), 1), 50)
            )
        return [_json_row(row) for row in rows]


_source: Optional[PostgresMapLayerSource] = None
_search_source: Optional[PostgresMapLayerSource] = None
_search_warmup_task: Optional[asyncio.Task[None]] = None
_search_warmup_loop: Optional[asyncio.AbstractEventLoop] = None
_source_lock = threading.Lock()


def get_map_layer_source() -> PostgresMapLayerSource:
    global _source
    if _source is None:
        with _source_lock:
            if _source is None:
                _source = PostgresMapLayerSource()
    return _source


def get_map_search_source() -> PostgresMapLayerSource:
    """Use a small reserved pool for latency-sensitive interactive search.

    Municipality and parcel-reference search both run here so they never
    queue behind tile rendering on the shared pool.
    """
    global _search_source
    if _search_source is not None:
        return _search_source
    map_source = get_map_layer_source()
    tile_source = map_source.connection_source
    with _source_lock:
        if _search_source is None:
            if tile_source is None:
                _search_source = PostgresMapLayerSource(None)
            else:
                _search_connection_source = _AsyncpgConnectionSource(
                    tile_source.dsn,
                    max_connections=2,
                    connect_timeout=tile_source.connect_timeout,
                )
                cadastral_source = map_source.cadastral_connection_source
                search_cadastral_source = (
                    _AsyncpgConnectionSource(
                        cadastral_source.dsn, max_connections=2,
                        connect_timeout=cadastral_source.connect_timeout,
                    ) if cadastral_source is not None else None
                )
                _search_source = PostgresMapLayerSource(
                    _search_connection_source, cadastral_connection_source=search_cadastral_source
                )
    return _search_source


async def warm_map_search_source() -> None:
    """Warm both reserved pools and resolve the cadastral source before search.

    Parcel search can use a dedicated cadastral database. Warming only the
    Stats pool left the cadastral pool setup and source-selection probes inside
    the first search deadline.
    """
    source = get_map_search_source()
    connection_sources = {
        id(connection_source): connection_source
        for connection_source in (
            source.connection_source,
            source.cadastral_connection_source,
        )
        if connection_source is not None
    }
    if not connection_sources:
        return
    for connection_source in connection_sources.values():
        try:
            await connection_source._get_pool()
        except Exception as exc:
            log.warning("Map search pool warm-up failed (search will retry lazily): %s", exc)
    try:
        await source._layer_source_for(get_map_layer("cadastral-parcels"))
    except Exception as exc:
        log.warning(
            "Map cadastral search source warm-up failed (search will retry lazily): %s",
            exc,
        )


def start_map_search_warmup() -> None:
    """Schedule one shared search-pool warm-up for the current event loop."""
    global _search_warmup_task, _search_warmup_loop
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _search_warmup_task is not None and _search_warmup_loop is loop:
        return
    _search_warmup_loop = loop
    _search_warmup_task = loop.create_task(warm_map_search_source())


async def ensure_map_search_source_warm(timeout: float = 15.0) -> None:
    """Wait briefly for the shared startup warm-up before a first search."""
    task = _search_warmup_task
    if task is None or _search_warmup_loop is not asyncio.get_running_loop():
        start_map_search_warmup()
        task = _search_warmup_task
    if task is not None:
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)


async def warm_low_zoom_map_tiles() -> None:
    """Prime a compact set of Italy boundary tiles after startup.

    The single-worker pass warms shared PostGIS pages used by the common
    national view. It is delayed and limited to z5–z6 to avoid a large startup
    query burst; deployments can disable it with MAP_TILE_WARMUP_ENABLED=0.
    """
    if os.getenv("MAP_TILE_WARMUP_ENABLED", "1").strip().lower() in {"0", "false", "no", "off"}:
        return
    try:
        delay = max(0.0, float(os.getenv("MAP_TILE_WARMUP_DELAY_SECONDS", "90")))
    except ValueError:
        delay = 90.0
    if delay:
        await asyncio.sleep(delay)
    source = get_map_layer_source()
    if not source.available or source.connection_source is None:
        return
    async def database_is_busy() -> bool:
        async with source.connection_source.connection() as connection:
            return await connection.fetchval("""
                SELECT EXISTS (
                    SELECT 1 FROM pg_stat_activity
                    WHERE datname = current_database()
                      AND pid <> pg_backend_pid()
                      AND state = 'active'
                      AND (
                          (query ILIKE '%UPDATE spatial.cadastral_parcel%'
                           AND query ILIKE '%cadastral.veneto.IT.duckdb%')
                          OR query ILIKE '%ST_AsMVT%'
                          OR query ILIKE '%ST_AsMVTGeom%'
                      )
                )
            """)

    try:
        if await database_is_busy():
            log.info("Skipping low-zoom tile warm-up while cadastral updates or live tile queries are active")
            return
    except Exception as exc:
        log.info("Skipping low-zoom tile warm-up because database load could not be checked: %s", exc)
        return

    west, south, east, north = 6.5, 36.3, 18.6, 47.2
    for zoom in (5, 6):
        scale = 1 << zoom
        x_min = max(0, int((west + 180.0) / 360.0 * scale))
        x_max = min(scale - 1, int((east + 180.0) / 360.0 * scale))
        y_for_lat = lambda lat: int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * scale)
        y_min = max(0, y_for_lat(north))
        y_max = min(scale - 1, y_for_lat(south))
        for x in range(x_min, x_max + 1):
            for y in range(y_min, y_max + 1):
                try:
                    if await database_is_busy():
                        log.info("Pausing low-zoom tile warm-up because live map queries became active")
                        return
                    await source.read_mvt("geo-boundaries", zoom, x, y)
                except Exception as exc:
                    log.warning("Map tile warm-up failed for geo-boundaries/%s/%s/%s: %s", zoom, x, y, exc)
                    return
                await asyncio.sleep(0.1)
    log.info("Warmed low-zoom administrative boundary tiles for Italy (z5–z6)")


async def close_map_layer_source() -> None:
    global _source, _search_source
    with _source_lock:
        source, _source = _source, None
        search_source, _search_source = _search_source, None
    if search_source is not None:
        await search_source.close()
    if source is not None:
        await source.close()
