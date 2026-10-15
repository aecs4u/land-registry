"""Pictures, charts and number formats of the parcel dossier PDF."""

from __future__ import annotations

from land_registry import parcel_report
from land_registry.parcel_report_graphics import (
    bar_chart,
    geometry_perimeter_m,
    parcel_locator_drawing,
    range_bar_chart,
)

SQUARE = {"type": "Polygon", "coordinates": [[[12, 41], [12.001, 41], [12.001, 41.001], [12, 41.001], [12, 41]]]}
FAR_AWAY = {"type": "Polygon", "coordinates": [[[12.2, 41.2], [12.21, 41.2], [12.21, 41.21], [12.2, 41.21], [12.2, 41.2]]]}
NEXT_DOOR = {"type": "Polygon", "coordinates": [[[12.001, 41], [12.002, 41], [12.002, 41.001], [12.001, 41.001], [12.001, 41]]]}


def _points(shape):
    return list(zip(shape.points[0::2], shape.points[1::2], strict=True))


def test_locator_map_draws_the_parcel_and_its_neighbours_inside_the_frame():
    drawing = parcel_locator_drawing(SQUARE, [NEXT_DOOR, FAR_AWAY], 240, 200, area_label="1.250 m²")

    assert drawing is not None and (drawing.width, drawing.height) == (240, 200)
    paths = [shape for shape in drawing.contents if shape.__class__.__name__ == "Path"]
    # the parcel and the adjoining neighbour are drawn, plus the north arrow head
    assert len(paths) >= 3
    for path in paths:
        for x, y in _points(path):
            assert -0.01 <= x <= 240.01 and -0.01 <= y <= 200.01  # nothing leaks outside the frame


def test_locator_map_is_skipped_without_a_usable_geometry():
    assert parcel_locator_drawing(None, None, 200, 150) is None
    assert parcel_locator_drawing({"type": "Point", "coordinates": [12, 41]}, None, 200, 150) is None


def test_perimeter_of_a_square_in_metres():
    perimeter = geometry_perimeter_m(SQUARE)
    # 0.001° of latitude is ~111 m, of longitude ~84 m at 41°N
    assert 380 < perimeter < 400
    assert geometry_perimeter_m(None) is None


def test_charts_ignore_rows_without_values():
    assert range_bar_chart([("A", None, None), ("B", 5, 3)], 300) is None  # no value, and max < min
    assert bar_chart([("A", None)], 300) is None
    chart = range_bar_chart([("Abitazioni civili", 3200, 4300), ("Box", 1500, 2100)], 400)
    assert chart is not None and chart.height > 30
    assert bar_chart([("0–10 k€", 21.4), ("10–15 k€", 14.1)], 300) is not None


def test_number_format_follows_the_report_locale():
    assert parcel_report.format_number(1298.555, "it") == "1.298,56"
    assert parcel_report.format_number(1298.555, "en") == "1,298.56"
    assert parcel_report.format_number(1250, "it") == "1.250"
    assert parcel_report.format_number(7.4, "it", 1) == "7,4"


def test_integers_that_are_codes_or_years_are_not_grouped():
    tr = lambda message: message  # noqa: E731
    assert parcel_report._display(82053, tr, "it", "istat_code") == "82053"
    assert parcel_report._display(2023, tr, "it", "year") == "2023"
    assert parcel_report._display(512340, tr, "it", "taxpayers") == "512.340"
    assert parcel_report._display(1298.5550448205322, tr, "it", "area_sqm") == "1.298,56"


def test_income_bracket_labels_are_readable():
    assert parcel_report._bracket_label("10_15k") == "10–15 k€"
    assert parcel_report._bracket_label("over_120k") == "> 120 k€"


def test_report_renders_tables_and_charts_for_valuation_and_income_blocks():
    parcel = {
        "type": "Feature",
        "properties": {"municipality_name": "Palermo", "sheet_number": "4", "parcel_number": "740", "area_sqm": 1298.555},
        "geometry": SQUARE,
    }
    profile = {
        "parcel": parcel,
        "blocks": {
            "valuation": {
                "available": True,
                "source": "Agenzia Entrate - OMI",
                "dataset_version": "2025/2",
                "data": {"quotes": [
                    {"zona": "B12", "tipologia": "Abitazioni civili", "stato_conservazione": "Normale",
                     "prezzo_min": 3200, "prezzo_max": 4300, "locazione_min": 11.5, "locazione_max": 15.2,
                     "derived_metrics": {"gross_rental_yield_pct": 4.2}},
                    {"zona": "B12", "tipologia": "Box", "stato_conservazione": "Normale", "prezzo_min": 1500, "prezzo_max": 2100},
                ]},
            },
            "economics": {
                "available": True,
                "source": "MEF/IRPEF",
                "data": {
                    "year": 2023,
                    "income_distribution": [{"bracket": "0_10k", "pct": 21.4}, {"bracket": "over_120k", "pct": 1.2}],
                    "income_reference_averages": {"municipality": {"name": "Palermo", "mean_taxable_income_eur": 21534.7}},
                },
            },
            "risk": {"available": False, "data": None},
        },
    }
    sections = [{"id": "omi", "title": "OMI valuation", "state": "ready",
                 "body": "Zone: B12\nCurrent quote: 3.200–4.300 €/m²\nIndicative only",
                 "metadata": {"source": "OMI"}}]

    for locale in ("it", "en"):
        pdf = parcel_report.render_parcel_report_pdf(
            "G273_0004C0.740", parcel, profile, "PR-TEST", sections=sections, neighbours=[NEXT_DOOR], locale=locale
        )
        assert pdf.startswith(b"%PDF-") and pdf.rstrip().endswith(b"%%EOF")


def test_report_renders_without_geometry_blocks_or_neighbours():
    pdf = parcel_report.render_parcel_report_pdf("X_1", {"properties": {}}, None, "PR-EMPTY")
    assert pdf.startswith(b"%PDF-")


def test_multipolygon_with_a_hole_renders():
    hole = {"type": "MultiPolygon", "coordinates": [[
        [[12, 41], [12.002, 41], [12.002, 41.002], [12, 41.002], [12, 41]],
        [[12.0005, 41.0005], [12.0015, 41.0005], [12.0015, 41.0015], [12.0005, 41.0015], [12.0005, 41.0005]],
    ]]}
    assert parcel_locator_drawing(hole, None, 240, 200) is not None
    assert geometry_perimeter_m(hole) > 0
