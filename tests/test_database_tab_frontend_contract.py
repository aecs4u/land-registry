"""Contract checks for the Database tab's region dropdown.

The tab's select is populated entirely client-side, so a mismatch between the
template's element id and the script's lookup fails silently in the browser and
leaves the user with an empty dropdown.  These checks pin the wiring.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "land_registry" / "static"
TEMPLATE_DIR = ROOT / "land_registry" / "templates"


def test_region_select_id_matches_the_script_that_fills_it() -> None:
    """The template owns the id; the loader must look up that exact id.

    An earlier generation of this code used the Italian spelling 'dbRegione',
    which matches no element in the current template.
    """
    template = (TEMPLATE_DIR / "index.html").read_text(encoding="utf-8")
    source = (STATIC_DIR / "folium-interface.js").read_text(encoding="utf-8")

    assert 'id="dbRegion"' in template
    assert "getElementById('dbRegion')" in source


def test_database_tab_init_populates_regions() -> None:
    """Region loading must be wired into tab init, not just defined."""
    source = (STATIC_DIR / "folium-interface.js").read_text(encoding="utf-8")

    init_start = source.index("function initDatabaseTab()")
    init_body = source[init_start:init_start + 400]

    assert "loadDbRegions()" in init_body
    assert "async function loadDbRegions()" in source


def test_region_loader_falls_back_when_hierarchy_is_empty() -> None:
    """An unprovisioned SpatiaLite store answers 200 with an empty list.

    Falling back only on a non-OK response would leave the dropdown empty on
    every host that has FlatGeobuf data but no cadastral SQLite databases.
    """
    source = (STATIC_DIR / "folium-interface.js").read_text(encoding="utf-8")

    loader_start = source.index("async function loadDbRegions()")
    loader = source[loader_start:source.index("async function loadDbProvinces()")]

    assert "/api/v1/cadastral/hierarchy" in loader
    assert "/api/v1/fgb/regions" in loader
    assert "regions.length === 0" in loader


def test_cadastral_sidebar_uses_lazy_cascade_endpoints() -> None:
    """The map must not fetch the full national hierarchy at startup."""
    source = (STATIC_DIR / "map.js").read_text(encoding="utf-8")
    loader_start = source.index("async function _doLoadCadastralData()")
    loader_end = source.index("// Show error message in the regions select")
    loader = source[loader_start:loader_end]

    assert "/api/v1/get-regions/" in loader
    assert "/api/v1/get-cadastral-structure/" not in loader
    assert "/api/v1/get-provinces/" in source
    assert "/api/v1/get-municipalities/" in source


def test_explore_cascade_initializes_without_opening_the_panel() -> None:
    """Explore selectors must not depend solely on the toolbar click."""
    template = (TEMPLATE_DIR / "index.html").read_text(encoding="utf-8")
    source = (STATIC_DIR / "map.js").read_text(encoding="utf-8")
    init_source = (STATIC_DIR / "index-init.js").read_text(encoding="utf-8")

    assert 'id="searchRegion"' in template
    assert 'id="searchProvince"' in template
    assert 'id="searchMunicipality"' in template
    assert "window.refreshSearchComuneList" in source
    assert "initializeExploreSearch" in init_source
    assert "DOMContentLoaded" in init_source
    assert "/api/v1/fgb/regions" in source
