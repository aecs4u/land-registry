"""CartoDB basemaps are an optional dependency.

Carto now requires an API key for their raster basemaps and answers an unkeyed
request with HTTP 200 and an "API KEY REQUIRED" watermark burned into the tile
rather than an error status. An unkeyed deployment therefore cannot detect the
failure and simply renders a defaced map, so the layers must only be offered
when a key is configured — and the app must not contact carto.com otherwise.
"""

import re

import pytest

from land_registry.config import MapControlsSettings


CARTO_HOST = "cartodb-basemaps"


def _render(monkeypatch, api_key: str) -> str:
    """Render the comprehensive map with the given Carto key configured."""
    import land_registry.map as map_module

    monkeypatch.setattr(
        map_module,
        "map_controls_settings",
        MapControlsSettings(carto_api_key=api_key),
    )
    generator = map_module.IntegratedMapGenerator()
    return generator.create_comprehensive_map().get_root().render()


def _shown_basemap(html: str) -> str:
    """URL of the single tile layer Folium adds to the map on load."""
    declared = re.findall(r'var (tile_layer_\w+) = L\.tileLayer\(\s*"([^"]+)"', html)
    added = set(re.findall(r"tile_layer_(\w+)\.addTo\(map_\w+\)", html))
    shown = [url for var, url in declared if var.removeprefix("tile_layer_") in added]
    assert len(shown) == 1, f"expected exactly one visible basemap, got {len(shown)}"
    return shown[0]


def test_carto_enabled_reflects_configured_key() -> None:
    assert MapControlsSettings(carto_api_key="").carto_enabled is False
    assert MapControlsSettings(carto_api_key="   ").carto_enabled is False
    assert MapControlsSettings(carto_api_key="abc123").carto_enabled is True


def test_without_key_the_map_never_references_carto(monkeypatch) -> None:
    html = _render(monkeypatch, "")

    assert CARTO_HOST not in html
    assert "CartoDB" not in html


def test_without_key_a_key_free_basemap_is_shown_on_load(monkeypatch) -> None:
    html = _render(monkeypatch, "")

    shown = _shown_basemap(html)
    assert CARTO_HOST not in shown
    # The configured fallback is Google Maps (lyrs=m), the key-free light
    # street map closest to Positron as a cadastral reference layer.
    assert "lyrs=m" in shown


def test_with_key_carto_layers_are_offered_and_signed(monkeypatch) -> None:
    html = _render(monkeypatch, "secret-key")

    carto_urls = sorted(set(re.findall(r"https://cartodb-basemaps[^\"\\ ]+", html)))
    assert len(carto_urls) == 2, carto_urls
    assert any("light_all" in url for url in carto_urls)
    assert any("dark_all" in url for url in carto_urls)
    for url in carto_urls:
        assert "api_key=secret-key" in url


def test_with_key_carto_positron_is_the_default_basemap(monkeypatch) -> None:
    html = _render(monkeypatch, "secret-key")

    shown = _shown_basemap(html)
    assert CARTO_HOST in shown
    assert "light_all" in shown


def test_api_key_is_url_encoded(monkeypatch) -> None:
    html = _render(monkeypatch, "key with/special+chars")

    assert "api_key=key%20with%2Fspecial%2Bchars" in html
    # The raw key must never reach the URL unencoded.
    assert "api_key=key with" not in html


@pytest.mark.parametrize("api_key", ["", "secret-key"])
def test_exactly_one_basemap_is_visible_either_way(monkeypatch, api_key: str) -> None:
    # Folium otherwise renders every base layer at once and lets them
    # checkerboard over each other; _shown_basemap asserts the count.
    _shown_basemap(_render(monkeypatch, api_key))


def test_client_side_providers_are_gated_on_the_flag() -> None:
    from pathlib import Path

    static = Path(__file__).resolve().parents[1] / "land_registry" / "static"
    map_js = (static / "map.js").read_text(encoding="utf-8")
    template = (
        Path(__file__).resolve().parents[1]
        / "land_registry"
        / "templates"
        / "index.html"
    ).read_text(encoding="utf-8")

    assert "window.cartoEnabled" in template
    assert "if (window.cartoEnabled && window.cartoApiKey)" in map_js
    # Providers are created only after the server-side feature flag and key
    # have been injected; no undefined Carto layers reach a control.
    assert "'⚪ CartoDB Light': mapProviders['⚪ CartoDB Light']," not in map_js
    assert "mapProviders['⚪ CartoDB Light'] = L.tileLayer" in map_js
