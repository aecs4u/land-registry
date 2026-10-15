"""Typst parcel dossier: SVG geometry, data assembly, rendering and the endpoint."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from land_registry import parcel_dossier as pd
from land_registry import parcel_dossier_svg as svg

# A ~35 m x 20 m parcel in Palermo and one neighbour sharing its western edge.
PARCEL_RING = [[13.3070, 38.1908], [13.3074, 38.1908], [13.3074, 38.1910], [13.3070, 38.1910], [13.3070, 38.1908]]
NEIGHBOUR_RING = [[13.3066, 38.1908], [13.3070, 38.1908], [13.3070, 38.1910], [13.3066, 38.1910], [13.3066, 38.1908]]
REFERENCE = "G273_000900.3205"


def _parcel(**overrides):
    props = {
        "id": 4431568767636311,
        "canonical_reference": REFERENCE,
        "national_cadastral_reference": REFERENCE,
        "parcel": "3205",
        "sheet": "9",
        "area_sqm": 1283.1,
        "region": "SICILIA",
        "province": "PA",
        "municipality_code": "G273",
        "municipality_name": "PALERMO",
        "inspire_localid": f"IT.AGE.PLA.{REFERENCE}",
        "source_release": "sicilia__particelle",
    }
    props.update(overrides)
    return {"type": "Feature", "id": props["id"], "properties": props, "geometry": {"type": "Polygon", "coordinates": [PARCEL_RING]}}


def _inputs(**overrides):
    base = {
        "reference": REFERENCE,
        "parcel": _parcel(),
        "neighbours": [{"type": "Polygon", "coordinates": [NEIGHBOUR_RING]}],
        "point": (38.1909, 13.3072),
        "municipality": {"name": "Palermo", "province": "Palermo", "province_sigla": "PA", "region": "Sicilia", "istat_code": "82053", "nuts3": "ITG12"},
        "omi_zone": {"matched": True, "zone": "E5", "description": "CARDILLO-LANZA DI SCALEA-OLIMPO", "source": "Agenzia delle Entrate OMI zones via aecs4u-stats PostgreSQL"},
        "omi_quotes": {
            "source": "Agenzia delle Entrate OMI current zone snapshot via aecs4u-stats PostgreSQL",
            "derived_metrics_model": "omi-quote-metrics-v1",
            "quotes": [
                {"zona": "E5", "tipologia": "Villas and small villas", "stato_conservazione": "P", "prezzo_min": "770", "prezzo_max": "1100",
                 "locazione_min": "2.7", "locazione_max": "3.8", "derived_metrics": {"gross_rental_yield_pct": 4.17, "zone_percentile_pct": 40.0}},
                {"zona": "E5", "tipologia": "Magazzini", "stato_conservazione": "N", "prezzo_min": "300", "prezzo_max": "410",
                 "locazione_min": "2.1", "locazione_max": "2.8", "derived_metrics": {}},
            ],
        },
        "omi_history": {"history": []},
        "census": {"properties": {"sez21_id": 820530002831, "p1": 239, "area_sqm": 145000, "pf1": 98, "a8": 132, "e3": 51,
                                  **{f"p{n}": 10 + n for n in range(14, 30)}, "ratios": {"vacancy_rate": 0.28}}, "source": "ISTAT Permanent Census 2021 via aecs4u-stats PostgreSQL"},
        "income": {"year": 2022, "taxpayers": 342450, "mean_taxable_income_eur": 22103.06, "source": "MEF/IRPEF via aecs4u-stats PostgreSQL serving.irpef_comuni",
                   "dataset_version": "MEF_IRPEF_2022",
                   "income_reference_averages": {"municipality": {"name": "PALERMO", "mean_taxable_income_eur": 22103.06},
                                                  "nation": {"name": "Italia", "mean_taxable_income_eur": 22806.31}},
                   "income_distribution": [{"bracket": "0_10k", "pct": 32.7}, {"bracket": "over_120k", "pct": 1.0}]},
        "risks": {"seismic": {"zone": 2, "zone_label": "2"}, "hydrogeological": {"source": "ISPRA IdroGEO (PAI/PGRA), CC BY 4.0",
                  "landslide": {"area_pct": {"P3_high": 1.6, "P4_very_high": 11.1}}, "flood": {"area_pct": {"P2_medium_probability": 6.4}}}},
        "mps04": {"available": False},
        "hazards": {"available": False},
        "subsidence": {"available": False},
        "buildings": {"available": True, "count": 0, "land": [], "addresses": [], "source": "aecs4u-stats PostgreSQL sister.v_sister_property_by_cadastral_parcel"},
    }
    base.update(overrides)
    return base


def _build(inputs=None, **kwargs):
    return pd.build_dossier(
        inputs or _inputs(),
        locale=kwargs.pop("locale", "it"),
        report_id="PR-TEST000001",
        now=datetime(2026, 10, 10, 12, 0, tzinfo=UTC),
        page_url="http://localhost:8011/map?parcel=" + REFERENCE,
        **kwargs,
    )


# -- SVG geometry -----------------------------------------------------------------------------------------------------


def test_locator_svg_draws_parcel_over_neighbours_with_scale_and_north():
    drawing = svg.locator_svg({"type": "Polygon", "coordinates": [PARCEL_RING]}, [{"type": "Polygon", "coordinates": [NEIGHBOUR_RING]}], 520, 250)

    assert drawing and drawing.startswith("<svg")
    assert drawing.count('fill-rule="evenodd"') == 2  # neighbour, then the highlighted parcel
    assert ">N<" in drawing and " m<" in drawing


def test_locator_svg_returns_none_without_geometry_and_charts_without_data():
    assert svg.locator_svg(None, [], 100, 100) is None
    assert svg.locator_svg({"type": "Point", "coordinates": [1, 2]}, [], 100, 100) is None
    assert svg.bar_chart_svg([("a", None)], 300) is None
    assert svg.range_bar_chart_svg([("a", 10, 5)], 300) is None  # high < low is not drawn
    assert svg.band_line_chart_svg([{"label": "x", "min": 1, "max": 2}], 300) is None  # one point is not a series


def test_clip_ring_and_perimeter_helpers():
    clipped = svg.clip_ring([(-5, -5), (5, -5), (5, 5), (-5, 5)], 0, 0, 2, 2)
    xs = [x for x, _ in clipped]
    ys = [y for _, y in clipped]
    assert min(xs) >= 0 and max(xs) <= 2 and min(ys) >= 0 and max(ys) <= 2

    perimeter = svg.geometry_perimeter_m({"type": "Polygon", "coordinates": [PARCEL_RING]})
    assert 100 < perimeter < 200
    assert svg.nice_length(130) == 100.0 and svg.fmt_length(2000) == "2 km"


def test_svg_text_is_escaped():
    chart = svg.bar_chart_svg([("<script>&", 5.0)], 300)
    assert "<script>" not in chart and "&lt;script&gt;&amp;" in chart


# -- formatting ---------------------------------------------------------------------------------------------------------


def test_number_and_bracket_formatting_follow_the_locale():
    assert pd.format_number(1298.56, "it") == "1.298,56"
    assert pd.format_number(1298.56, "en") == "1,298.56"
    assert pd.format_number(22103, "it") == "22.103"
    assert pd.bracket_label("10_15k") == "10–15 k€"
    assert pd.bracket_label("over_120k") == "> 120 k€"
    assert pd.bracket_label("le_0") == "≤ 0"


# -- assembly -----------------------------------------------------------------------------------------------------------


def test_build_dossier_summarises_the_parcel_from_sourced_values():
    variables, files = _build()
    kpis = {item["label"]: item for item in variables["kpis"]}

    assert variables["parcel"]["municipality"] == "Palermo"
    assert variables["parcel"]["subline"].startswith("Foglio 9 · Particella 3205")
    assert kpis["Superficie"]["value"] == "1.283 m²"
    assert kpis["€/m² (OMI)"]["value"] == "935" and kpis["€/m² (OMI)"]["hint"] == "770–1.100"
    assert kpis["Zona sismica"]["value"] == "2"
    assert [section["id"] for section in variables["sections"]] == ["map", "cadastre", "location", "buildings", "risks", "population", "income", "omi"]
    assert {"cover_map.svg", "locator_map.svg", "chart_age.svg", "chart_income_avg.svg", "qr.svg"} <= set(files)
    assert variables["report"]["id"] == "PR-TEST000001"


def test_missing_evidence_is_stated_not_converted_to_a_value():
    # No SISTER record is "unknown", never "0 buildings".
    variables, _ = _build()
    kpis = {item["label"]: item["value"] for item in variables["kpis"]}
    assert kpis["Edifici (SISTER)"] == "n.d."
    buildings = next(s for s in variables["sections"] if s["id"] == "buildings")
    assert buildings["blocks"][0]["type"] == "unavailable"

    sparse, files = _build(
        _inputs(
            municipality=None, omi_zone=None, omi_quotes=None, omi_history=None, census=None, income=None, risks=None,
            mps04=None, hazards=None, subsidence=None, buildings=None, point=None,
        )
    )
    sparse_kpis = {item["label"]: item["value"] for item in sparse["kpis"]}
    assert sparse_kpis["€/m² (OMI)"] == sparse_kpis["Zona sismica"] == sparse_kpis["Densità (ab/km²)"] == "n.d."
    for section in sparse["sections"]:
        if section["id"] in {"buildings", "population", "income", "omi"}:
            assert section["blocks"][0]["type"] == "unavailable", section["id"]
    assert sparse["sources"] and sparse["sources"][0]["title"] == "Dati catastali e identificativi"
    assert "chart_age.svg" not in files


def test_source_register_lists_each_provider_once_with_versions_not_invented():
    variables, _ = _build()
    titles = [source["title"] for source in variables["sources"]]
    assert "Economia e redditi" in titles and "Quotazioni OMI" in titles
    income = next(s for s in variables["sources"] if s["title"] == "Economia e redditi")
    rows = dict((label, value) for label, value in income["rows"])
    assert rows["Versione dataset"] == "MEF_IRPEF_2022"
    assert rows["Versione modello"] == "Non riportato nei metadati della fonte"
    assert "Economia e redditi=MEF_IRPEF_2022" in variables["versions"]


def test_saved_parcel_appendix_is_only_present_when_the_user_saved_the_parcel():
    without, _ = _build()
    assert without["personal"] == []

    saved = {"label": "Corner lot", "status": "researching", "status_label": "In ricerca", "priority": 4, "is_favorite": True, "tags": ["infill"], "notes": "Call"}
    variables, _ = _build(saved=saved)
    rows = dict((label, value) for label, value in variables["personal"][0]["rows"])
    assert rows["Stato"] == "In ricerca" and rows["Preferita"] == "Sì" and rows["Priorità"] == "4"
    assert "Appendice: dati personali" in variables["contents"]


def test_english_locale_uses_english_labels_and_separators():
    variables, _ = _build(locale="en")
    assert variables["text"]["doc_title"] == "Parcel data sheet"
    kpis = {item["label"]: item["value"] for item in variables["kpis"]}
    assert kpis["Area"] == "1,283 m²"


# -- rendering and endpoint ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dossier_compiles_to_a_multi_page_pdf():
    variables, files = _build(saved={"label": "Corner lot", "status": "new", "is_favorite": False})
    pdf = await pd.render_dossier_pdf(variables, files)

    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 20_000


@pytest.mark.asyncio
async def test_dossier_compiles_with_no_data_at_all():
    sparse = _inputs(
        municipality=None, omi_zone=None, omi_quotes=None, omi_history=None, census=None, income=None, risks=None,
        mps04=None, hazards=None, subsidence=None, buildings=None, point=None, neighbours=[],
    )
    variables, files = pd.build_dossier(sparse, locale="it", report_id="PR-SPARSE00001", page_url=None)
    pdf = await pd.render_dossier_pdf(variables, files)
    assert pdf.startswith(b"%PDF")


def _client(monkeypatch):
    from land_registry.routers import enrichment
    from land_registry.routers import parcel_dossier as dossier_router

    async def fake_parcel(reference, id=None):
        return _parcel()

    async def fake_gather(reference, parcel, *, neighbours=None):
        return _inputs()

    async def no_neighbours(reference, feature_id):
        return []

    monkeypatch.setattr(enrichment, "get_parcel_by_reference", fake_parcel)
    monkeypatch.setattr(pd, "gather_inputs", fake_gather)
    monkeypatch.setattr(dossier_router, "_neighbour_geometries", no_neighbours)
    app = FastAPI()
    app.include_router(dossier_router.parcel_dossier_router, prefix="/api/v1/enrichment")
    return TestClient(app)


def test_dossier_endpoint_returns_a_pdf_with_a_report_id(monkeypatch):
    client = _client(monkeypatch)

    response = client.get(f"/api/v1/enrichment/parcel/dossier/{REFERENCE}?id=4431568767636311", headers={"accept-language": "it"})

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["x-report-id"].startswith("PR-")
    assert "parcel-dossier.pdf" in response.headers["content-disposition"]
    assert response.content.startswith(b"%PDF")


def test_dossier_endpoint_accepts_and_ignores_a_posted_snapshot(monkeypatch):
    client = _client(monkeypatch)

    response = client.post(f"/api/v1/enrichment/parcel/dossier/{REFERENCE}", json={"sections": [{"id": "identity", "body": "x"}]})

    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")


# -- deprecation --------------------------------------------------------------------------------------------------------


def test_reportlab_renderer_is_marked_deprecated():
    from land_registry import parcel_report

    assert "DEPRECATED" in (parcel_report.__doc__ or "")
    with pytest.warns(DeprecationWarning, match="parcel_dossier"):
        parcel_report.render_parcel_report_pdf(REFERENCE, _parcel(), None, "PR-DEPRECATED1")
