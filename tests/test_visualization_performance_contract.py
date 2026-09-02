"""Contracts for the browser-side cadastral rendering performance path."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "land_registry" / "static"


def test_cadastral_layers_use_adaptive_canvas_and_delegated_events() -> None:
    source = (STATIC / "folium-interface.js").read_text(encoding="utf-8")

    assert "CADASTRAL_CANVAS_THRESHOLD" in source
    assert "L.canvas({ padding: 0.5 })" in source
    assert "layer.on('click', event =>" in source
    assert "onEachFeature" not in source.split("function _createCadastralLayer", 1)[1].split(
        "// Priority property order", 1
    )[0]
    assert "_createCadastralLayer(geojson" in source
    assert "CADASTRAL_LABEL_ZOOM = 17" in source
    assert "CADASTRAL_LABEL_LIMIT = 1000" in source
    assert "_cadastralLabelLayers" in source
    assert "Only inspect cadastral layers" in source
    assert "zoomend moveend" in source


def test_progressive_loader_does_not_split_every_network_chunk() -> None:
    source = (STATIC / "progressive-loader.js").read_text(encoding="utf-8")
    loader_body = source.split("async load", 1)[1]

    assert "buffer.indexOf('\\n')" in loader_body
    assert "const lines = buffer.split('\\n')" not in loader_body


def test_progressive_loads_feed_table_and_analysis_state() -> None:
    interface = (STATIC / "folium-interface.js").read_text(encoding="utf-8")
    api = (ROOT / "land_registry" / "routers" / "api.py").read_text(encoding="utf-8")
    map_source = (STATIC / "map.js").read_text(encoding="utf-8")

    assert "window.progressiveGeoJsonData = { type: 'FeatureCollection', features: [] };" in interface
    assert "window.progressiveGeoJsonData.features.push(...geojson.features);" in interface
    assert "gdf[\"feature_id\"] = range(feature_offset" in api
    assert "window.progressiveGeoJsonData?.features?.length" in map_source
    assert "window.syncMapPolygonSelection" in map_source
    assert "function getCadastralFeatureLayers()" in map_source
    assert "const featureLayers = getCadastralFeatureLayers();" in map_source
    assert "cadastralInteractionBound" in interface


def test_datashader_cache_access_is_synchronized() -> None:
    source = (ROOT / "land_registry" / "datashader_service.py").read_text(encoding="utf-8")

    assert "self._tile_cache_lock = threading.RLock()" in source
    assert "def _get_cached_tile" in source
    assert "def _cache_tile" in source
    assert "DATASHADER_TILE_CACHE_DIR" in source
    assert "os.replace(temporary_path, path)" in source
    assert "def _prune_disk_cache" in source


def test_map_bootstrap_uses_one_serialized_payload_and_no_remote_geometry() -> None:
    main_source = (ROOT / "land_registry/main.py").read_text(encoding="utf-8")
    template = (ROOT / "land_registry/templates/index.html").read_text(encoding="utf-8")
    map_source = (ROOT / "land_registry/map.py").read_text(encoding="utf-8")

    assert '"geojson_data": geojson_data' in main_source
    assert "JSON.parse({{ geojson_data" not in template
    assert "raw.githubusercontent.com" not in map_source
    assert "openweathermap.org" not in map_source
    # Exactly one basemap is visible on load; which one depends on whether the
    # optional CartoDB dependency is configured (see the carto tests below).
    assert "show=layer_config['name'] == default_basemap" in map_source


def test_selected_parcel_panel_and_file_selection_do_not_block_or_duplicate() -> None:
    styles = (STATIC / "styles.css").read_text(encoding="utf-8")
    map_source = (STATIC / "map.js").read_text(encoding="utf-8")

    close_rule = styles.split(".close-panel-btn {", 1)[1].split("}", 1)[0]
    assert "width: auto;" in close_rule
    assert "endsWith(`_${fileTypeLower}.fgb`)" in map_source


def test_boundary_tiles_use_retina_size_and_native_zoom_cap() -> None:
    service = (ROOT / "land_registry/datashader_service.py").read_text(encoding="utf-8")
    enrichment = (STATIC / "enrichment-layers.js").read_text(encoding="utf-8")
    consumer = (STATIC / "consumer-map.js").read_text(encoding="utf-8")

    assert "self.tile_size = 512" in service
    assert "line_width=1" in service
    assert "antialias=True" not in service
    for source in (enrichment, consumer):
        assert "tileSize: 512" in source
        assert "maxNativeZoom: 19" in source


def test_boundary_service_targets_canonical_stats_postgis_first() -> None:
    source = (ROOT / "land_registry/datashader_service.py").read_text(encoding="utf-8")

    assert '"map": "spatial.cadastral_sheet"' in source
    assert '"ple": "spatial.cadastral_parcel"' in source
    assert "ST_Intersection(t.geom" in source
    assert "t.geom &&" in source
    assert "ST_Covers(t.geom, click.geom)" in source
    assert "PostgresCadastralBoundarySource.from_environment()" in source
    assert "postgres_frame = self._read_postgres_boundaries" in source
    assert "def read_mvt" in source
    assert "ST_AsMVTGeom" in source
    assert "ST_AsMVT(mvtgeom, 'cadastral', 4096, 'geom')" in source


def test_centroid_derivation_uses_metric_crs_for_geographic_data() -> None:
    source = (ROOT / "land_registry/datashader_service.py").read_text(encoding="utf-8")

    assert "gdf.estimate_utm_crs()" in source
    assert "to_crs(metric_crs).geometry.centroid" in source


def test_optional_datashader_failure_is_stable_and_empty_tiles_are_cached() -> None:
    dependencies = (ROOT / "land_registry" / "dependencies.py").read_text(encoding="utf-8")
    api = (ROOT / "land_registry" / "routers" / "api.py").read_text(encoding="utf-8")
    service = (ROOT / "land_registry" / "datashader_service.py").read_text(encoding="utf-8")

    assert "class _UnavailableDatashaderService" in dependencies
    assert "self._service = _UnavailableDatashaderService()" in dependencies
    assert "empty_datashader_tile()" in api
    assert "def _empty_cached_tile" in service


def test_large_html_and_scripts_use_gzip_middleware() -> None:
    main = (ROOT / "land_registry" / "main.py").read_text(encoding="utf-8")

    assert "from starlette.middleware.gzip import GZipMiddleware" in main
    assert "app.add_middleware(GZipMiddleware" in main


def test_map_shell_offloads_expensive_serialization_and_map_building() -> None:
    main = (ROOT / "land_registry" / "main.py").read_text(encoding="utf-8")
    shell = main.split("async def _build_main_map_shell_context", 1)[1].split(
        "@app.get(\"/map\")", 1
    )[0]

    assert "await asyncio.to_thread(" in shell
    assert "current_gdf.to_json()" in shell
    assert "map_generator.create_comprehensive_map" in shell
    assert "await asyncio.to_thread(get_cadastral_stats)" in shell


def test_cadastral_structure_cache_serializes_expensive_misses() -> None:
    source = (ROOT / "land_registry/cadastral_utils.py").read_text(encoding="utf-8")

    assert "_cadastral_cache_lock = threading.Lock()" in source
    assert "with _cadastral_cache_lock:" in source


def test_main_map_can_use_mvt_with_raster_fallback() -> None:
    api = (ROOT / "land_registry/routers/api.py").read_text(encoding="utf-8")
    enrichment = (STATIC / "enrichment-layers.js").read_text(encoding="utf-8")
    base = (ROOT / "land_registry/templates/base.html").read_text(encoding="utf-8")

    assert 'cadastral-boundaries/{z}/{x}/{y}.pbf' in api
    assert "generate_boundary_mvt" in api
    assert "L.vectorGrid.protobuf" in enrichment
    assert "cadastral-boundaries/{z}/{x}/{y}.pbf?layer=${layerType}" in enrichment
    assert "switchToRasterFallback" in enrichment
    assert "tileerror" in enrichment
    assert "status_code=503" in api
    assert "leaflet.vectorgrid@1.3.0" in base


def test_map_runtime_does_not_reload_leaflet_or_block_on_plugin_scripts() -> None:
    base = (ROOT / "land_registry/templates/base.html").read_text(encoding="utf-8")

    assert "request.url.path != '/map'" in base
    assert "leaflet@1.9.4/dist/leaflet.js\" defer" in base
    assert "leaflet-control-geocoder@4.0.0" in base
    assert "leaflet.markercluster@1.5.3" in base
    assert "leaflet-minimap@3.6.1" in base
    assert "leaflet.locatecontrol@0.90.1" in base
    assert "leaflet-search@4.0.0" in base
    assert "leaflet-rastercoords" not in base
    assert "@clerk/clerk-js@6.30.3" in base
    assert "@clerk/clerk-js@latest" not in base
    assert "leaflet-treelayers" not in base
    assert "glify-browser.js" not in base
    assert "webgl-renderer.js" not in base
    assert "{% if request.url.path != '/map' %}" in base
    assert "{% else %}" in base
    assert "leaflet.draw.js\" defer" in base
    assert "leaflet.draw.css" in base


def test_legacy_layer_control_has_no_broken_weather_overlays() -> None:
    map_source = (STATIC / "map.js").read_text(encoding="utf-8")
    templates = [
        (ROOT / "land_registry/templates/index.html").read_text(encoding="utf-8"),
        (ROOT / "land_registry/templates/map.html").read_text(encoding="utf-8"),
    ]

    assert "weatherOverlays" not in map_source
    assert all("Weather overlays" not in template for template in templates)
    assert all("toggleTreeLayers()" not in template for template in templates)


def test_main_map_has_scale_and_one_server_layer_control() -> None:
    source = (ROOT / "land_registry/map.py").read_text(encoding="utf-8")

    assert "class ScaleControl" in source
    assert "map_instance.add_child(ScaleControl())" in source
    assert source.count("folium.LayerControl(") == 1


def test_panel_table_routes_do_not_block_the_event_loop() -> None:
    source = (ROOT / "land_registry/main.py").read_text(encoding="utf-8")

    for route_name, panel_url in (
        ("show_map_table", "PANEL_MAP_TABLE_URL"),
        ("show_adjacency_table", "PANEL_ADJACENCY_TABLE_URL"),
        ("show_mapping_table", "PANEL_MAPPING_TABLE_URL"),
    ):
        route_body = source.split(f"async def {route_name}", 1)[1].split(
            "@app.get", 1
        )[0]
        assert "await asyncio.to_thread(server_document," in route_body
        assert panel_url in route_body
