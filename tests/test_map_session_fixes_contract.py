"""Cross-stack contracts for docs/MAP_SESSION_FIXES_2026-07-12.md."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE = (ROOT / "land_registry" / "datashader_service.py").read_text(encoding="utf-8")
API = (ROOT / "land_registry" / "routers" / "api.py").read_text(encoding="utf-8")
LAYERS = (ROOT / "land_registry" / "static" / "enrichment-layers.js").read_text(
    encoding="utf-8"
)
MAP = (ROOT / "land_registry" / "map.py").read_text(encoding="utf-8")
MAIN = (ROOT / "land_registry" / "main.py").read_text(encoding="utf-8")
MAP_JS = (ROOT / "land_registry" / "static" / "map.js").read_text(encoding="utf-8")
FOLIUM = (ROOT / "land_registry" / "static" / "folium-interface.js").read_text(
    encoding="utf-8"
)
STYLES = (ROOT / "land_registry" / "static" / "styles.css").read_text(encoding="utf-8")


def test_boundary_tiles_are_layer_aware_outline_only_and_capped() -> None:
    boundary_body = SERVICE.split("def generate_boundary_tile", 1)[1].split(
        "def identify_feature", 1
    )[0]

    assert '"map": "cadastral_map"' in SERVICE
    assert '"ple": "cadastral_ple"' in SERVICE
    assert '("boundary", layer_type, x, y, z)' in SERVICE
    assert 'frame = frame.iloc[:remaining].copy()' in boundary_body
    assert 'boundary["geometry"] = boundary.boundary' in boundary_body
    assert "canvas.line(" in boundary_body
    assert "canvas.polygons(" not in boundary_body
    assert 'for layer_type in ("map", "ple")' in SERVICE


def test_boundary_api_and_frontend_expose_both_zoom_tiers_and_identify() -> None:
    assert '@api_router.get("/tiles/cadastral-boundaries/{z}/{x}/{y}.png")' in API
    assert 'pattern="^(map|ple)$"' in API
    assert '@api_router.get("/cadastral-identify")' in API
    assert "service.identify_feature" in API
    assert "?layer=map" in LAYERS
    assert "?layer=ple" in LAYERS
    assert "minZoom: 13, maxZoom: 22" in LAYERS
    assert "minZoom: 16, maxZoom: 22" in LAYERS
    assert "/api/v1/cadastral-identify" in LAYERS
    assert "/api/v1/enrichment/parcel/at-point" in LAYERS


def test_viewport_parcel_loader_is_zoom_gated_and_cancels_stale_requests() -> None:
    assert "VIEWPORT_PARCEL_MIN_ZOOM = 16" in LAYERS
    assert "/api/v1/enrichment/parcels/in-bbox/?${params}" in LAYERS
    assert "new AbortController()" in LAYERS
    assert "viewportParcelAbortController.abort()" in LAYERS
    assert "map.on('moveend', _refreshViewportParcelLayerDebounced)" in LAYERS
    assert "token !== viewportParcelRequestToken" in LAYERS
    assert "_clearViewportParcelLayer(map)" in LAYERS
    assert "window.refreshViewportParcelLayer" in LAYERS


def test_map_and_every_remote_tile_layer_support_overzoom() -> None:
    assert "max_zoom=22" in MAP
    assert MAP.count("max_zoom=22") >= 2  # map and basemaps
    assert "'max_native_zoom': 20" in MAP
    assert "'max_native_zoom': 19" in MAP
    assert "'max_native_zoom': 13" in MAP
    assert "max_native_zoom=layer_config['max_native_zoom']" in MAP
    assert "if 5 <= zoom <= 22:" in MAIN
    assert "default_basemap = 'CartoDB Positron (Light)'" in MAP
    assert "show=layer_config['name'] == default_basemap" in MAP
    assert "openweathermap.org" not in MAP


def test_dark_mode_synchronizes_theme_icon_and_live_css_selectors() -> None:
    assert "document.documentElement.setAttribute('data-bs-theme', 'dark')" in MAP_JS
    assert "document.documentElement.setAttribute('data-bs-theme', 'light')" in MAP_JS
    assert "document.getElementById('themeIcon')" in MAP_JS
    assert "icon.classList.toggle('fa-moon'" in MAP_JS
    assert "icon.classList.toggle('fa-sun'" in MAP_JS
    assert "body.dark-mode .tool-btn-ghost" in STYLES
    assert "body.dark-mode .map-tool-btn" in STYLES
    assert ':not(.tool-btn-ghost):not(.map-tool-btn)' in STYLES
    assert '[data-theme="dark"]' not in STYLES


def test_view_tools_are_real_and_clean_up_after_toggle() -> None:
    assert "function toggleMiniMap()" in FOLIUM
    assert "L.map(div" in FOLIUM
    assert "map.on('moveend', sync)" in FOLIUM
    assert "this._miniMap.remove()" in FOLIUM
    assert "clearTimeout(this._createTimer)" in FOLIUM
    assert "function toggleCoordinates()" in FOLIUM
    assert "map.on('mousemove', this._onMove)" in FOLIUM
    assert "Overview MiniMap (built-in)" in FOLIUM
    assert "map.fitBounds(ITALY_MAP_BOUNDS)" in FOLIUM
    assert "history.replaceState" in FOLIUM
    assert "map.on('moveend'" in FOLIUM


def test_map_header_has_one_compact_base_rule() -> None:
    assert STYLES.count("\n.map-header {\n") == 1
    base_rule = STYLES.split(".map-header {", 1)[1].split("}", 1)[0]
    assert "display: flex !important;" in base_rule
    assert "padding: 8px 15px;" in base_rule
    assert "#mapView .map-header {\n  padding-top: 20px;" not in STYLES
