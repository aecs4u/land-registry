"""Fixed-fixture contracts for the parcel panel's derived metrics and spatial slices."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from land_registry import stats_service
from land_registry.routers import enrichment as enrichment_module


def _income_distribution(first="0-10k", second="10-15k"):
    labels = ["le–0", "0–10k", "10–15k", "15–26k", "26–55k", "55–75k", "75–120k", "over–120k"]
    selected = {first.replace("-", "–"), second.replace("-", "–")}
    return [
        {"bracket": label, "frequency": int(label in selected)}
        for label in labels
    ]


def test_omi_quote_metrics_annualize_rent_and_use_zone_midrank() -> None:
    quotes = [
        {
            "zona": zone,
            "cod_tipologia": "20",
            "stato_conservazione": "NORMALE",
            "prezzo_min": midpoint - 10,
            "prezzo_max": midpoint + 10,
            "locazione_min": 0.9,
            "locazione_max": 1.1,
        }
        for zone, midpoint in zip(("A1", "A2", "B1", "B2", "C1"), (100, 200, 300, 400, 500), strict=True)
    ]

    annotated = stats_service._omi_quote_derived_metrics(quotes)
    selected = next(row for row in annotated if row["zona"] == "B1")["derived_metrics"]

    assert selected["gross_rental_yield_pct"] == 4.0
    assert selected["zone_percentile_pct"] == 50.0
    assert selected["zone_percentile_comparable_zones"] == 5
    assert selected["gross_rental_yield_model_version"] == "omi-gross-rental-yield-v1"
    assert selected["zone_percentile_model_version"] == "omi-zone-percentile-midrank-v1"


def test_zone_percentile_is_withheld_below_five_comparable_zones() -> None:
    quotes = [
        {
            "zona": f"A{index}", "cod_tipologia": "20", "stato_conservazione": "NORMALE",
            "prezzo_min": 100 + index * 10, "prezzo_max": 200 + index * 10,
        }
        for index in range(4)
    ]

    metrics = stats_service._omi_quote_derived_metrics(quotes)[0]["derived_metrics"]

    assert metrics["zone_percentile_pct"] is None
    assert metrics["zone_percentile_comparable_zones"] == 4
    assert metrics["zone_percentile_minimum_comparable_zones"] == 5


def test_grouped_gini_uses_eight_bracket_representatives() -> None:
    profile = {"income_distribution": _income_distribution()}

    metrics = stats_service._income_profile_derived_metrics(profile)["derived_metrics"]

    assert metrics["grouped_gini_estimate"] == 0.214
    assert metrics["grouped_gini_model_version"] == "mef-grouped-gini-v1"
    assert metrics["grouped_gini_assumptions"]["bracket_representatives_eur"]["over_120k"] == 150000


def test_omi_trend_deltas_report_actual_comparison_periods() -> None:
    rows = [
        {"anno": 2024, "semestre": 2, "prezzo_min": 70, "prezzo_max": 90},
        {"anno": 2025, "semestre": 1, "prezzo_min": 90, "prezzo_max": 110},
        {"anno": 2025, "semestre": 2, "prezzo_min": 190, "prezzo_max": 210},
    ]

    deltas = stats_service._omi_history_trend_deltas(rows)

    assert deltas[0] == {
        "horizon_semesters": 1, "from": "2025-1", "to": "2025-2", "change_pct": 100.0,
    }
    assert deltas[1]["from"] == "2024-2"
    assert deltas[1]["change_pct"] == 150.0


@pytest.mark.asyncio
async def test_price_to_mean_income_ratio_is_added_by_estimate_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(enrichment_module.stats_service, "omi_db_available_public", lambda: True)
    monkeypatch.setattr(
        enrichment_module.stats_service,
        "estimate_omi_value",
        lambda **kwargs: {
            "model_version": "omi-area-range-v1",
            "quote": {"prezzo_min_eur_sqm": 1000, "prezzo_max_eur_sqm": 1500},
            "value_range_eur": {"min": 80000, "max": 120000},
        },
    )

    async def income(_comune):
        return {"mean_taxable_income_eur": 25000, "year": 2024, "dataset_version": "MEF_IRPEF_2024"}

    monkeypatch.setattr(enrichment_module.stats_service, "aget_income_profile", income)
    request = enrichment_module.OmiEstimateRequest(
        comune="H501", zona="B1", cod_tipologia="20", area_sqm=80,
    )

    result = await enrichment_module.estimate_omi_value(request)

    assert result["derived_metrics"]["price_to_mean_taxable_income_years"] == 4.0
    assert result["derived_metrics"]["price_to_income_model_version"] == "omi-mef-price-income-v1"
    assert "not household affordability" in result["derived_metrics"]["interpretation"]


def _polygon(points):
    return {"type": "Polygon", "coordinates": [points]}


def test_parcel_hazard_intersections_measure_union_and_highest_class(monkeypatch) -> None:
    parcel = _polygon([[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]])
    landslide = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"hazard_code": "P3"}, "geometry": parcel},
            {"type": "Feature", "properties": {"hazard_code": "P4"}, "geometry": _polygon([[0, 0], [0.5, 0], [0.5, 0.5], [0, 0.5], [0, 0]])},
        ],
        "metadata": {"total_count": 2},
    }
    flood = {"type": "FeatureCollection", "features": [], "metadata": {"total_count": 0}}
    monkeypatch.setattr(stats_service, "_ispra_mosaics_db_available", lambda: True)
    monkeypatch.setattr(stats_service, "_landslide_hazard_in_bbox", lambda *args, **kwargs: landslide)
    monkeypatch.setattr(stats_service, "_flood_hazard_in_bbox", lambda *args, **kwargs: flood)

    result = stats_service.get_parcel_hazard_intersections(parcel)

    assert result["available"] is True
    assert result["coverage_complete"] is True
    assert result["landslide"]["worst_class"] == "P4"
    assert result["landslide"]["affected_area_pct"] == pytest.approx(100.0)
    assert result["flood"]["affected_area_pct"] == 0


def test_parcel_hazard_intersections_mark_the_candidate_cap_partial(monkeypatch) -> None:
    parcel = _polygon([[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]])
    empty_at_cap = {"type": "FeatureCollection", "features": [], "metadata": {"total_count": 5000}}
    monkeypatch.setattr(stats_service, "_ispra_mosaics_db_available", lambda: True)
    monkeypatch.setattr(stats_service, "_landslide_hazard_in_bbox", lambda *args, **kwargs: empty_at_cap)
    monkeypatch.setattr(stats_service, "_flood_hazard_in_bbox", lambda *args, **kwargs: empty_at_cap)

    result = stats_service.get_parcel_hazard_intersections(parcel)

    assert result["available"] is True
    assert result["coverage_complete"] is False
    assert result["candidate_limit_reached"] is True


def test_egms_point_adapter_returns_the_grid_properties(monkeypatch) -> None:
    monkeypatch.setattr(stats_service, "_egms_db_available", lambda: True)
    monkeypatch.setattr(
        stats_service, "_subsidence_at_point",
        lambda lat, lng, direction: {"properties": {"risk_class_label": "Moderate", "mean_velocity": -4.5}},
    )

    result = stats_service.get_subsidence_at_point(41.9, 12.5)

    assert result["available"] is True
    assert result["matched"] is True
    assert result["properties"]["mean_velocity"] == -4.5
    assert result["spatial_resolution"] == "100 m grid cell"
    assert result["source"] == "Zornade Rischio Subsidenza Italia (Copernicus EGMS L3 Ortho)"
    assert result["source_url"] == "https://zornade.com/data-downloads/"
    assert result["license"] == "ODbL"


def test_egms_parcel_summary_area_weights_intersecting_cells(monkeypatch) -> None:
    parcel = _polygon([[12, 41], [12.002, 41], [12.002, 41.001], [12, 41.001], [12, 41]])
    cells = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"mean_velocity": -2, "acceleration": 0.2, "risk_class_label": "Stable"},
                "geometry": _polygon([[12, 41], [12.001, 41], [12.001, 41.001], [12, 41.001], [12, 41]]),
            },
            {
                "type": "Feature",
                "properties": {"mean_velocity": -4, "acceleration": 0.6, "risk_class_label": "Moderate"},
                "geometry": _polygon([[12.001, 41], [12.002, 41], [12.002, 41.001], [12.001, 41.001], [12.001, 41]]),
            },
        ],
    }
    monkeypatch.setattr(stats_service, "_egms_db_available", lambda: True)
    monkeypatch.setattr(stats_service, "_subsidence_in_bbox", lambda *args, **kwargs: cells)

    result = stats_service.get_parcel_subsidence_intersections(parcel)

    assert result["available"] is True
    assert result["matched"] is True
    assert result["query_complete"] is True
    assert result["cell_count"] == 2
    assert result["covered_area_pct"] == pytest.approx(100.0, abs=0.01)
    assert result["mean_velocity_mm_per_year"] == pytest.approx(-3.0, abs=0.001)
    assert result["mean_acceleration_mm_per_year_squared"] == pytest.approx(0.4, abs=0.001)
    assert {row["class"] for row in result["class_distribution"]} == {"Stable", "Moderate"}


def test_egms_parcel_summary_marks_candidate_limit_partial(monkeypatch) -> None:
    parcel = _polygon([[12, 41], [12.002, 41], [12.002, 41.001], [12, 41.001], [12, 41]])
    cell = {
        "type": "Feature",
        "properties": {"mean_velocity": -2},
        "geometry": parcel,
    }
    monkeypatch.setattr(stats_service, "_EGMS_PARCEL_CANDIDATE_LIMIT", 2)
    monkeypatch.setattr(stats_service, "_egms_db_available", lambda: True)
    monkeypatch.setattr(
        stats_service, "_subsidence_in_bbox",
        lambda *args, **kwargs: {"type": "FeatureCollection", "features": [cell, cell, cell]},
    )

    result = stats_service.get_parcel_subsidence_intersections(parcel)

    assert result["available"] is True
    assert result["candidate_limit_reached"] is True
    assert result["query_complete"] is False
    assert result["candidate_count"] == 2


def test_egms_parcel_summary_distinguishes_no_intersecting_cells(monkeypatch) -> None:
    parcel = _polygon([[12, 41], [12.002, 41], [12.002, 41.001], [12, 41.001], [12, 41]])
    monkeypatch.setattr(stats_service, "_egms_db_available", lambda: True)
    monkeypatch.setattr(
        stats_service, "_subsidence_in_bbox",
        lambda *args, **kwargs: {"type": "FeatureCollection", "features": []},
    )

    result = stats_service.get_parcel_subsidence_intersections(parcel)

    assert result["available"] is True
    assert result["matched"] is False
    assert result["query_complete"] is True
    assert result["covered_area_pct"] == 0


def test_egms_parcel_summary_reports_missing_store(monkeypatch) -> None:
    monkeypatch.setattr(stats_service, "_egms_db_available", lambda: False)

    result = stats_service.get_parcel_subsidence_intersections(
        _polygon([[12, 41], [12.002, 41], [12.002, 41.001], [12, 41.001], [12, 41]])
    )

    assert result["available"] is False
    assert result["reason"] == "egms_not_built"


def test_mps04_pga_uses_nearest_native_grid_and_explicit_probability(monkeypatch) -> None:
    seen = {}
    monkeypatch.setattr(stats_service, "_mps04_db_available", lambda: True)

    def pga_at_point(lat, lng, **kwargs):
        seen.update(lat=lat, lng=lng, **kwargs)
        return {
            "point_id": "p-17", "point_lat": 41.9, "point_lon": 12.5,
            "value_median": 0.123, "value_p16": 0.101, "value_p84": 0.147,
        }

    monkeypatch.setattr(stats_service, "_mps04_pga_at_point", pga_at_point)

    result = stats_service.get_mps04_pga_at_point(41.89, 12.49)

    assert seen == {
        "lat": 41.89, "lng": 12.49, "exceedance_probability_pct": 10.0,
        "grid_variant": "native", "max_distance_m": 5000,
    }
    assert result["available"] is True
    assert result["matched"] is True
    assert result["pga_g"] == 0.123
    assert result["pga_p16_g"] == 0.101
    assert result["pga_p84_g"] == 0.147
    assert result["exposure_period_years"] == 50
    assert result["model_version"] == "MPS04"
    assert result["match_method"] == "nearest_grid_point"


def test_mps04_pga_reports_unbuilt_store_without_fabricating_zero(monkeypatch) -> None:
    monkeypatch.setattr(stats_service, "_mps04_db_available", lambda: False)

    result = stats_service.get_mps04_pga_at_point(41.89, 12.49)

    assert result["available"] is False
    assert result["reason"] == "mps04_not_built"
    assert "pga_g" not in result


@pytest.mark.asyncio
async def test_mps04_route_offloads_point_lookup(monkeypatch) -> None:
    seen = {}

    def lookup(lat, lng):
        seen.update(lat=lat, lng=lng)
        return {"available": True, "matched": False}

    monkeypatch.setattr(enrichment_module.stats_service, "get_mps04_pga_at_point", lookup)

    result = await enrichment_module.get_mps04_pga(lat=41.89, lng=12.49)

    assert seen == {"lat": 41.89, "lng": 12.49}
    assert result == {"available": True, "matched": False}


@pytest.mark.asyncio
async def test_parcel_hazard_route_resolves_canonical_geometry(monkeypatch) -> None:
    seen = {}
    monkeypatch.setattr(enrichment_module, "get_map_layer_source", lambda: SimpleNamespace(available=True))

    async def resolve(reference, id=None):
        seen["reference"] = reference
        seen["id"] = id
        return {"type": "Feature", "geometry": _polygon([[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]])}

    monkeypatch.setattr(enrichment_module, "get_parcel_by_reference", resolve)
    monkeypatch.setattr(enrichment_module.stats_service, "get_parcel_hazard_intersections", lambda geometry: {"available": True})

    result = await enrichment_module.get_parcel_hazards("H501_1.2", id=7)

    assert seen == {"reference": "H501_1.2", "id": 7}
    assert result == {"available": True}


@pytest.mark.asyncio
async def test_parcel_subsidence_route_resolves_canonical_geometry(monkeypatch) -> None:
    seen = {}
    monkeypatch.setattr(enrichment_module, "get_map_layer_source", lambda: SimpleNamespace(available=True))

    async def resolve(reference, id=None):
        seen["reference"] = reference
        seen["id"] = id
        return {"type": "Feature", "geometry": _polygon([[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]])}

    monkeypatch.setattr(enrichment_module, "get_parcel_by_reference", resolve)
    monkeypatch.setattr(
        enrichment_module.stats_service,
        "get_parcel_subsidence_intersections",
        lambda geometry: {"available": True, "match_method": "parcel_polygon_intersection"},
    )

    result = await enrichment_module.get_parcel_subsidence("H501_1.2", id=7)

    assert seen == {"reference": "H501_1.2", "id": 7}
    assert result["match_method"] == "parcel_polygon_intersection"
