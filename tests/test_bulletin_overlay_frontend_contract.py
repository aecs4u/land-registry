"""Contracts for the Civil Protection bulletin map overlay."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAYER_SOURCE = (ROOT / "land_registry" / "static" / "enrichment-layers.js").read_text(
    encoding="utf-8"
)
INDEX_SOURCE = (ROOT / "land_registry" / "templates" / "index.html").read_text(
    encoding="utf-8"
)
BASE_SOURCE = (ROOT / "land_registry" / "templates" / "base.html").read_text(
    encoding="utf-8"
)


def test_bulletin_controls_are_exposed_in_the_map_panel() -> None:
    assert 'id="toggleEnrichmentBulletin"' in INDEX_SOURCE
    assert 'id="enrichmentBulletinLegend"' in INDEX_SOURCE
    assert "toggleBulletinLayer()" in INDEX_SOURCE
    assert "refreshBulletinLayer()" in INDEX_SOURCE


def test_overlay_fetches_and_converts_bulletin_topology() -> None:
    assert "/api/v1/enrichment/bulletin" in LAYER_SOURCE
    assert "window.topojson.feature(topology, object)" in LAYER_SOURCE
    assert "topojson-client@3" in BASE_SOURCE


def test_overlay_styles_all_dpc_alert_levels() -> None:
    for label in ("ROSSA", "ARANCIONE", "GIALLA", "NESSUNA ALLERTA"):
        assert label in LAYER_SOURCE
    assert "Rappresentata nella mappa" in LAYER_SOURCE
    assert "Per rischio idraulico" in LAYER_SOURCE
    assert "Per rischio temporali" in LAYER_SOURCE
    assert "Per rischio idrogeologico" in LAYER_SOURCE


def test_overlay_ignores_superseded_fetches_and_cleans_up() -> None:
    assert "bulletinFetchToken" in LAYER_SOURCE
    assert "!bulletinActive" in LAYER_SOURCE
    assert "map.removeLayer(bulletinLayerGroup)" in LAYER_SOURCE
