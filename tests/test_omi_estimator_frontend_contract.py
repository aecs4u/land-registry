"""Contracts for the parcel OMI estimator and historical series UI."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PANEL_SOURCE = (ROOT / "land_registry" / "static" / "parcel-enrichment.js").read_text(
    encoding="utf-8"
)
STYLE_SOURCE = (ROOT / "land_registry" / "static" / "styles.css").read_text(
    encoding="utf-8"
)


def test_estimator_uses_parcel_area_and_selected_sale_range() -> None:
    assert "function _parcelAreaSqm" in PANEL_SOURCE
    assert "function _estimateOmiRange" in PANEL_SOURCE
    assert "area * minRate" in PANEL_SOURCE
    assert "area * maxRate" in PANEL_SOURCE
    assert "area && area <= 10000" in PANEL_SOURCE
    assert "La particella misura" in PANEL_SOURCE
    assert 'id="omiEstimateArea"' in PANEL_SOURCE
    assert 'id="omiEstimateResult"' in PANEL_SOURCE


def test_estimator_is_explicitly_non_appraisal() -> None:
    assert "Non è una perizia" in PANEL_SOURCE
    assert "corretta zona OMI" in PANEL_SOURCE
    assert "superficie commerciale" in PANEL_SOURCE


def test_history_uses_selected_zone_and_typology() -> None:
    assert "/api/v1/enrichment/omi/history" in PANEL_SOURCE
    assert "params.set('cod_tipologia'" in PANEL_SOURCE
    assert "function _renderOmiHistory" in PANEL_SOURCE
    assert "omiHistoryToken" in PANEL_SOURCE
    assert "row.stato_conservazione === selectedState" in PANEL_SOURCE


def test_history_chart_and_dark_mode_styles_exist() -> None:
    assert 'class="omi-history-chart"' in PANEL_SOURCE
    assert ".omi-history-chart" in STYLE_SOURCE
    assert "body.dark-mode .omi-estimate" in STYLE_SOURCE
