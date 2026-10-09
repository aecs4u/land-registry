"""Contracts for the direct cadastral map migration target."""

import re
from pathlib import Path

from land_registry.map_observability import MapMetrics, map_route_bucket

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / "land_registry/templates/map_v2.html").read_text(encoding="utf-8")
THEME_TEMPLATE = (ROOT / "land_registry/templates/theme_overrides/map_v2.html").read_text(encoding="utf-8")
BASE_TEMPLATE = (ROOT / "land_registry/templates/base.html").read_text(encoding="utf-8")
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
        "Approximate data extent",
        "Coverage not reported",
        "role === 'admin-substitute'",
        "enableAdministrativeSubstitute",
        "releaseAdministrativeSubstitute",
        "updateParcelZoomAffordance",
        "Zoom in to select a parcel",
        "blockMetadataHtml",
        "Concession documents",
        "detail_url.replace",
        "canonicalHttpUrl",
        "target=\"_blank\"",
        "document_url",
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
    # moveend also fires after zooms; the coverage note depends on bounds too.
    assert "state.map.on('moveend', updateParcelZoomAffordance)" in SCRIPT
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


def test_theme_map_owns_the_viewport_and_primary_accessibility_contract():
    assert 'id="main-content"' not in THEME_TEMPLATE
    assert 'id="directMapShell"' in THEME_TEMPLATE
    assert 'id="directMapHeading"' in THEME_TEMPLATE
    assert 'id="mapSearchForm"' in THEME_TEMPLATE
    assert 'id="directMap" class="direct-map" role="region"' in THEME_TEMPLATE
    assert "display: flex; flex-direction: column" in STYLES
    assert "flex: 1 1 auto" in STYLES
    assert "ResizeObserver" in SCRIPT
    assert "mapHasUsableSize" in SCRIPT
    assert "geolocation=(self)" in MAIN
    assert '"login_redirect_url": "/map"' in MAIN


def test_direct_map_closes_enrichment_overlay_and_adjacency_gaps():
    # Legacy's Explore panel toggle buttons (enrichment-layers.js), ported to
    # MapLibre GeoJSON sources on the direct map instead of Leaflet layer
    # groups — same generic /api/v1/enrichment/* endpoints.
    for value in (
        "toggleEnrichmentPois",
        "toggleEnrichmentFires",
        "toggleEnrichmentBulletin",
        "/api/v1/enrichment/pois/",
        "/api/v1/enrichment/fires",
        "/api/v1/enrichment/bulletin",
        "enrichment-poi",
        "enrichment-fires",
        "enrichment-bulletin",
        "window.topojson",
        # Single-parcel analogue of the legacy multi-select "Find Adjacent".
        "parcelAdjacentButton",
        "findAdjacentParcels",
        "/api/v1/enrichment/parcel/adjacent/",
        "adjacent-parcels",
        # Full raw-property dump, the single-parcel analogue of the legacy
        # attribute table (table-manager.js).
        "allAttributesHtml",
    ):
        assert value in SCRIPT

    for template in (TEMPLATE, THEME_TEMPLATE):
        assert 'id="toggleEnrichmentPois"' in template
        assert 'id="toggleEnrichmentFires"' in template
        assert 'id="toggleEnrichmentBulletin"' in template
        assert 'id="parcelAdjacentButton"' in template
        assert 'id="parcelAdjacentResults"' in template
    assert "topojson-client" in BASE_TEMPLATE

    assert "enrichment-legend" in STYLES
    assert "parcel-all-attributes" in STYLES


def test_direct_map_closes_market_filter_and_attribute_table_gaps():
    # The direct-map auction toggle reads the enriched PVP map feed, clusters
    # its points, and applies the type, price, and upcoming filters locally.
    for value in (
        "toggleAuctionLayer",
        "/api/v1/sales/map-points?period=all",
        "auction-properties",
        "auction-clusters",
        "applyAuctionFilter",
        "/api/v1/sales/pvp/",
        "pvp_record",
        "auctionTypeFilter",
        "auctionMaxPrice",
        "auctionActiveOnly",
        # Legacy Table View (table-manager.js), scoped to one catalog layer's
        # current viewport instead of the whole uploaded GeoDataFrame.
        "tableView",
        "setTableViewOpen",
        "loadTableData",
        "/api/v1/map/layers/${encodeURIComponent(tableView.layerId)}/features",
        "mapTableLayerSelect",
        "mapTableFilter",
        "focusTableFeature",
    ):
        assert value in SCRIPT

    for template in (TEMPLATE, THEME_TEMPLATE):
        assert 'id="toggleAuctionLayer"' in template
        assert 'id="auctionTypeFilter"' in template
        assert 'id="auctionMaxPrice"' in template
        assert 'id="tableToggle"' in template
        assert 'id="mapTableCard"' in template
        assert 'id="mapTableLayerSelect"' in template

    assert "map-table-card" in STYLES
    assert "map-attribute-table" in STYLES


def test_adjacent_parcels_endpoint_is_registered():
    ENRICHMENT_SOURCE = (ROOT / "land_registry/routers/enrichment.py").read_text(encoding="utf-8")
    assert '@enrichment_router.get("/parcel/adjacent/{national_reference}")' in ENRICHMENT_SOURCE
    assert "read_adjacent_features" in ENRICHMENT_SOURCE


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


def test_direct_map_sales_and_poi_explorer_contract():
    for value in (
        "salesMapPointsUrl", "/api/v1/sales/map-points", "toggleSalesLayer",
        "sales-clusters", "sales-unclustered", "saleability_score",
        "appraisal_value", "dse_score", "salesPopupHtml", "salesGeoLayerType",
        "salesGeoMetric", "poiRadius", "poiCategories",
        "/api/v1/enrichment/poi-map", "/api/v1/enrichment/poi-report",
        "poiExportJson", "poiExportPdf", "googleMapsButton", "streetViewButton",
    ):
        assert value in SCRIPT or value in TEMPLATE
    for template in (TEMPLATE, THEME_TEMPLATE):
        assert 'id="toggleSalesLayer"' in template
        assert 'id="salesGeoLayerType"' in template
        assert 'id="poiCategories"' in template
        assert 'id="poiExportPdf"' in template


def test_map_audit_fixes_include_coverage_state_accessibility_and_table_paging():
    assert "coverage_bounds" in SCRIPT and "Outside the estimated data extent; coverage may vary." in SCRIPT
    assert "parcel_id" in SCRIPT and "searchActiveIndex" in SCRIPT
    assert "aria-activedescendant" in SCRIPT
    assert "offset: String((tableView.page - 1) * tableView.pageSize)" in SCRIPT
    assert "map-mobile-action-bar" in TEMPLATE
    assert "map-mobile-action-bar" in STYLES
    assert "retryTile" not in SCRIPT  # retries are bounded through the source tile set
    assert "Retry-After" in API and "MAP_PUBLIC_METRICS" in API
    assert "unpkg.com/leaflet@1.9.4/dist/leaflet.js" not in TEMPLATE


def test_theme_map_loads_topojson_parser_for_civil_protection_bulletin():
    # The bulletin is published as TopoJSON; without the parser the overlay
    # drew nothing and reported "(0)" alerts.
    assert "topojson-client" in THEME_TEMPLATE
    assert THEME_TEMPLATE.index("topojson-client") < THEME_TEMPLATE.index("asset_url('map-v2.js')")


def test_idle_handler_only_reacts_after_a_tile_loading_phase():
    # Unconditional idle work re-rendered the map forever and replaced every
    # status message with "Map ready".
    idle = SCRIPT[SCRIPT.index("state.map.on('idle'"):]
    idle = idle[:idle.index("});") + 3]
    assert "if (!state.tilesLoading) return;" in idle
    assert "state.mapReadyAnnounced" in idle
    assert "getLayoutProperty(id, 'visibility')" in SCRIPT


def test_attribute_table_keeps_pagination_visible_above_shortlist_chip():
    assert ".map-table-card { z-index: 4;" in STYLES
    assert ".map-table-card:not([hidden]) { display: flex; flex-direction: column; overflow: hidden; }" in STYLES


def _element_ids(template: str) -> list[str]:
    import re

    return re.findall(r'\bid="([^"]+)"', template)


def test_standalone_and_theme_templates_define_the_same_map_controls():
    # The theme override is what /map renders when aecs4u-theme is installed;
    # map_v2.html is the fallback. They are kept as separate files, so this
    # guards against one gaining or losing a control the script depends on.
    shell_only = {"themeButton", "sidebar-group-land-registry", "sidebar-group-account"}
    assert set(_element_ids(TEMPLATE)) ^ set(_element_ids(THEME_TEMPLATE)) == shell_only


def test_map_templates_have_no_duplicate_element_ids():
    for template in (TEMPLATE, THEME_TEMPLATE):
        ids = _element_ids(template)
        assert len(ids) == len(set(ids)), sorted({i for i in ids if ids.count(i) > 1})


def test_map_templates_load_pinned_assets_with_integrity_and_topojson_before_script():
    for template in (TEMPLATE, THEME_TEMPLATE):
        assert "topojson-client@3.1.0" in template
        assert template.index("topojson-client") < template.index("asset_url('map-v2.js')")
        for needle in ("maplibre-gl.js", "maplibre-gl.css", "topojson-client.min.js"):
            tag = template[template.rindex("<", 0, template.index(needle)):template.index(">", template.index(needle))]
            assert 'integrity="sha384-' in tag and 'crossorigin="anonymous"' in tag, needle
        # Leaflet is lazy-loaded only when WebGL is unavailable.
        assert "leaflet.css" not in template


def test_layers_card_is_catalog_driven_and_groups_overlays():
    for value in ("buildLayerItem", "updateLayerBadges", "applyLayerOpacity", "bindUnitLevels", "layer.unit_levels", "z_order", "layer.fill_opacity"):
        assert value in SCRIPT
    assert "const colors = {" not in SCRIPT  # colours live in the catalog now
    assert "map-layer-group" in STYLES and "map-layer-badges" in STYLES
    for template in (TEMPLATE, THEME_TEMPLATE):
        assert 'id="refreshAuctionButton"' in template
        assert '<select id="poiCategories"' not in template


def test_theme_map_injects_i18n_strings_through_a_block_the_theme_renders():
    # aecs4u-theme's base.html has no i18n_data block, so a template that only
    # fills that block silently loses window._i18n.
    assert "window._i18n" in THEME_TEMPLATE.split("{% block extra_js %}", 1)[1]
    assert THEME_TEMPLATE.index("window._i18n") < THEME_TEMPLATE.index("asset_url('map-v2.js')")


def test_tile_retry_counter_only_resets_on_a_loaded_tile():
    # setTiles() emits a 'content' sourcedata event of its own. Resetting the
    # retry counter on any such event meant a 503 source was retried forever.
    handler = SCRIPT[SCRIPT.index("state.map.on('sourcedata'"):]
    handler = handler[:handler.index("});")]
    assert "event.tile?.state === 'loaded'" in handler
    assert "tileRetry.attempts[event.sourceId] = 0" in handler


def test_exhausted_tile_retries_raise_a_source_banner_instead_of_a_status_pill():
    retry = SCRIPT[SCRIPT.index("function scheduleTileRetry"):]
    retry = retry[:retry.index("tileRetry.pending.add")]
    assert "refreshSourceTileDown()" in retry
    refresh = SCRIPT[SCRIPT.index("function refreshSourceTileDown"):]
    refresh = refresh[:refresh.index("function scheduleTileRetry")]
    assert "state.sourceTileDown =" in refresh
    assert "isKnownUnavailableTileSource" in refresh
    assert "updateSourceBanner()" in refresh
    assert "function retrySourceTiles" in SCRIPT
    assert "mapSourceRetry" in SCRIPT
    assert ".map-source-banner" in STYLES
    assert ".map-status-pill:empty { display: none; }" in STYLES


def test_source_outage_is_not_reported_as_configuration_or_coverage():
    health = SCRIPT[SCRIPT.index("async function loadLayerHealth"):]
    health = health[:health.index("finally {")]
    assert "state.sourceHealthDown = payload.available === false" in health
    assert "Temporarily unavailable" in health
    assert "`Not configured${metadata}`" not in health
    # An approximate extent must not be interpreted as coverage during an outage.
    assert "!sourceDown && viewportOutside(bounds, estimatedBounds)" in SCRIPT
    # A missing catalog yields a message in the attribute table, not a blank box.
    table = SCRIPT[SCRIPT.index("async function loadTableData"):]
    assert "Layer data is temporarily unavailable." in table[:table.index("const bounds")]
