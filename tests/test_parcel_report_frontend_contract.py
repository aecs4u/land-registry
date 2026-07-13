"""Contracts for the printable parcel dossier and browser PDF export."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAP_SOURCE = (ROOT / "land_registry" / "static" / "map.js").read_text(encoding="utf-8")
REPORT_SOURCE = (ROOT / "land_registry" / "static" / "parcel-report.js").read_text(
    encoding="utf-8"
)
STYLE_SOURCE = (ROOT / "land_registry" / "static" / "styles.css").read_text(
    encoding="utf-8"
)
TEMPLATE_SOURCE = (ROOT / "land_registry" / "templates" / "index.html").read_text(
    encoding="utf-8"
)


def test_parcel_panel_exposes_report_action_and_overlay() -> None:
    assert 'id="parcelReportBtn"' in MAP_SOURCE
    assert "openParcelReport()" in MAP_SOURCE
    assert 'id="parcelReportOverlay"' in TEMPLATE_SOURCE
    assert 'id="parcelReportContent"' in TEMPLATE_SOURCE
    assert 'src="/static/parcel-report.js"' in TEMPLATE_SOURCE
    assert "get('report') !== '1'" in REPORT_SOURCE
    assert "_openDeepLinkedReport" in REPORT_SOURCE


def test_report_snapshots_identity_geometry_and_enrichments() -> None:
    assert "function _parcelReference" in REPORT_SOURCE
    assert "function _geometrySvg" in REPORT_SOURCE
    assert "function _snapshotPanel" in REPORT_SOURCE
    assert "window.currentParcelFeature" in REPORT_SOURCE
    assert "#parcelEnrichmentSections" in STYLE_SOURCE
    assert "Avvertenza" in REPORT_SOURCE


def test_interactive_controls_are_frozen_to_report_values() -> None:
    assert "clone.querySelectorAll('select')" in REPORT_SOURCE
    assert "select.options[select.selectedIndex]" in REPORT_SOURCE
    assert "clone.querySelectorAll('input')" in REPORT_SOURCE
    assert "parcel-report-control-value" in REPORT_SOURCE
    assert "details.open = true" in REPORT_SOURCE
    assert "clone.appendChild(extraProperties)" in REPORT_SOURCE


def test_print_mode_is_a4_and_isolates_the_dossier() -> None:
    assert "window.print()" in REPORT_SOURCE
    assert "parcel-report-printing" in REPORT_SOURCE
    assert "@page { size: A4" in STYLE_SOURCE
    assert "@media print" in STYLE_SOURCE
    assert "body.parcel-report-printing #parcelReportOverlay" in STYLE_SOURCE
    assert ".parcel-report-toolbar { display: none !important; }" in STYLE_SOURCE
