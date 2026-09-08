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
