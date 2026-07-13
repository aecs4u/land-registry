"""Contract checks for the nationwide parcel-click frontend integration."""

from pathlib import Path


STATIC_DIR = Path(__file__).resolve().parents[1] / "land_registry" / "static"


def test_boundary_click_uses_enrichment_parcel_lookup() -> None:
    source = (STATIC_DIR / "enrichment-layers.js").read_text(encoding="utf-8")

    assert "/api/v1/enrichment/parcel/at-point" in source
    assert "showParcelInfo(feature, cadastralSelectionLayer)" in source
    assert "L.geoJSON(feature" in source


def test_selected_parcel_is_deep_linkable_and_restorable() -> None:
    source = (STATIC_DIR / "enrichment-layers.js").read_text(encoding="utf-8")

    assert "url.searchParams.set('parcel', reference)" in source
    assert "/api/v1/enrichment/parcel/by-reference/" in source
    assert "history.replaceState" in source
    assert "props.national_cadastral_reference" in source


def test_parcel_panel_close_clears_map_selection() -> None:
    source = (STATIC_DIR / "map.js").read_text(encoding="utf-8")

    assert "window.clearCadastralParcelSelection()" in source
    assert "window.currentParcelFeature = null" in source


def test_parcel_panel_exposes_copyable_deep_link() -> None:
    source = (STATIC_DIR / "map.js").read_text(encoding="utf-8")

    assert 'id="copyParcelLinkBtn"' in source
    assert "window.copyParcelLink" in source
    assert "navigator.clipboard.writeText(window.location.href)" in source


def test_stats_parcel_property_names_are_supported_by_panel() -> None:
    map_source = (STATIC_DIR / "map.js").read_text(encoding="utf-8")
    enrichment_source = (STATIC_DIR / "parcel-enrichment.js").read_text(encoding="utf-8")

    assert "props.national_cadastral_reference" in map_source
    assert "props.municipality_name" in map_source
    assert "props.national_cadastral_reference" in enrichment_source
    assert "labelParts.length > 1" in map_source
