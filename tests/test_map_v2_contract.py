"""Contracts for the direct cadastral map migration target."""

import re
from pathlib import Path

from land_registry.map_observability import MapMetrics, map_route_bucket

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / "land_registry/templates/map_v2.html").read_text(encoding="utf-8")
SCRIPT = (ROOT / "land_registry/static/map-v2.js").read_text(encoding="utf-8")
STYLES = (ROOT / "land_registry/static/map-v2.css").read_text(encoding="utf-8")
API = (ROOT / "land_registry/routers/api.py").read_text(encoding="utf-8")
MAIN = (ROOT / "land_registry/main.py").read_text(encoding="utf-8")
OBSERVABILITY = (ROOT / "land_registry/map_observability.py").read_text(encoding="utf-8")


def test_direct_map_has_one_authoritative_map_and_migration_escape_hatch():
    assert 'id="directMap"' in TEMPLATE
    assert "new maplibregl.Map" in SCRIPT
    assert 'href="/map-legacy"' in TEMPLATE
    assert "uploaded-file analysis" in SCRIPT or "uploaded-file" in SCRIPT
    assert 'async def serve_map_shell' in MAIN
    assert 'return await serve_direct_map(request' in MAIN
    assert '@app.get("/map-legacy"' in MAIN


def test_direct_map_resolves_tiles_and_skips_signed_out_user_calls():
    # Root-relative tile URLs never resolve inside MapLibre's blob: workers.
    assert "absoluteTileUrl(layer.tile_url)" in SCRIPT
    # Same CARTO API-key gate as the legacy map; keyless CARTO is watermarked.
    assert "window.cartoApiKey" in TEMPLATE and "window.cartoApiKey" in SCRIPT
    assert "World_Light_Gray_Base" in SCRIPT
    # Signed-out visitors must not request per-user endpoints (401 noise).
    assert '"signed_in": user is not None' in MAIN
    assert "window.landRegistrySignedIn" in TEMPLATE and "window.landRegistrySignedIn" in SCRIPT
    # Parcel labels need glyphs; they are self-hosted, not fetched from a font CDN.
    assert "/static/fonts/{fontstack}/{range}.pbf" in SCRIPT
    assert "'text-font': ['noto-sans-regular']" in SCRIPT
    assert (ROOT / "land_registry/static/fonts/noto-sans-regular/0-255.pbf").is_file()
    assert (ROOT / "land_registry/static/fonts/OFL.txt").is_file()


def test_direct_map_uses_catalog_vector_tiles_and_raster_fallback():
    assert "fetch('/api/v1/map/layers')" in SCRIPT
    assert "layer.tile_url" in SCRIPT
    assert "/api/v1/tiles/cadastral-boundaries/{z}/{x}/{y}.png?layer=ple" in SCRIPT
    assert "handleLayerFailure" in SCRIPT
    assert "FullscreenControl" in SCRIPT
    assert "map-layer-swatch" in SCRIPT


def test_direct_map_supports_selection_search_url_state_and_enrichment():
    for value in (
        "/api/v1/map/search?query=",
        "/api/v1/enrichment/parcel/at-point?",
        "/api/v1/enrichment/parcel/by-reference/",
        "/api/v1/enrichment/parcel/details/",
        "history.replaceState",
        "selected-parcel",
        "copyLink",
        "clearParcelSelection",
        "parcel-enrichment",
        "mousemove",
        "parcelReportLink",
        "report=1",
        "searchController.abort()",
        "controller.signal",
        "Geometry",
        "parcelSaveButton",
        "/api/v1/saved-parcels",
        "Sign in to save parcels",
        "activeLayers",
        "initializingLayers",
        "Date reported per feature",
        "Update date not reported",
        "role === 'admin-substitute'",
        "enableAdministrativeSubstitute",
        "releaseAdministrativeSubstitute",
        "updateParcelZoomAffordance",
        "blockMetadataHtml",
        "dataset_version",
        "model_version",
        "spatial_resolution",
        "parcel-block-chips",
        "benchmarks",
        "parcel-block-benchmarks",
        "loadShortlist",
        "status_vocabulary",
        "shortlistSummary",
        "shortlistStatusFilter",
        "shortlistPriorityFilter",
        "shortlistHazardFilter",
        "active_hazard=true",
        "data-shortlist-open",
        "shortlistReference(item)",
        "REF=([^|]+)",
    ):
        assert value in SCRIPT

    assert 'id="parcelShortlistCard"' in TEMPLATE
    assert 'id="shortlistStatusFilter"' in TEMPLATE
    assert 'id="shortlistHazardFilter"' in TEMPLATE
    assert "parcel-shortlist-card" in STYLES


def test_direct_map_shortlist_opens_legacy_source_keys_by_reference():
    helper = re.search(
        r"function shortlistReference\(item\) \{(?P<body>.*?)\n  \}",
        SCRIPT,
        re.DOTALL,
    )
    assert helper is not None
    body = helper.group("body")

    national_reference = body.index("item.national_reference")
    source_key = body.index("String(item.source_key || '')")
    ref_match = body.index("sourceKey.match(/(?:^|\\|)REF=([^|]+)/)")
    decode = body.index("decodeURIComponent(match[1])")
    identity_fallback = body.index("item.parcel_identity_id || ''")

    assert national_reference < source_key < ref_match < decode < identity_fallback


def test_direct_map_shows_admin_substitute_below_parcel_zoom():
    assert "ADMIN_SUBSTITUTE_LAYER_IDS" in SCRIPT
    assert "geo-boundaries" in SCRIPT
    assert "municipality-profiles" in SCRIPT
    assert "parcelMinZoom()" in SCRIPT
    assert "state.map.getZoom() < parcelMinZoom()" in SCRIPT
    assert "state.adminSubstituteAutoLayers.add(layer.id)" in SCRIPT
    assert "state.adminSubstituteAutoLayers.forEach" in SCRIPT
    assert "syncLayerCheckbox(layer.id, true)" in SCRIPT
    assert "syncLayerCheckbox(layerId, false)" in SCRIPT
    assert "state.map.on('zoomend', updateParcelZoomAffordance)" in SCRIPT
    assert "parcel-block-chips" in STYLES
    assert "parcel-block-benchmarks" in STYLES


def test_direct_map_is_responsive_and_accessible():
    assert "@media (max-width: 700px)" in STYLES
    assert "focus-visible" in STYLES
    assert 'role="search"' in TEMPLATE
    assert 'aria-live="polite"' in TEMPLATE
    assert 'role="listbox"' in TEMPLATE
    assert 'aria-pressed="false"' in TEMPLATE
    assert "setAttribute('aria-pressed'" in SCRIPT


def test_map_search_is_allowlisted_and_bounded():
    assert '@api_router.get("/map/search")' in API
    assert "max_length=120" in API
    assert "source.search_municipalities" in API
    assert "get_parcel_by_reference" in API
    assert "component_match" in API
    assert "stats_service.get_parcels" in API
    assert '"feature": parcel' not in API
    assert "municipality_name" in SCRIPT


def test_map_exposes_privacy_preserving_operational_diagnostics():
    assert '@api_router.get("/map/metrics")' in API
    assert "MapMetricsMiddleware" in MAIN
    assert "latency_buckets_ms" in OBSERVABILITY
    assert "map_route_bucket" in OBSERVABILITY
    assert "query_string" not in OBSERVABILITY


def test_map_metrics_normalize_routes_and_bound_latency_state():
    metrics = MapMetrics()
    metrics.record("search", 200, 90)
    metrics.record("search", 503, 12001)
    snapshot = metrics.snapshot()

    assert map_route_bucket("/api/v1/map/search") == "search"
    assert map_route_bucket("/api/v1/tiles/map-layers/cadastral-parcels/16/1/2.pbf") == "canonical_vector_tile"
    assert map_route_bucket("/api/v1/map/search?query=secret") is None
    assert snapshot["requests"] == 2
    assert snapshot["errors"] == 1
    assert snapshot["routes"]["search"]["latency_buckets_ms"]["100"] == 1
    assert snapshot["routes"]["search"]["latency_buckets_ms"]["inf"] == 1
