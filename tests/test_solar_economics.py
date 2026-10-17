import pytest
from fastapi import HTTPException

from land_registry.models import SolarEconomicsRequest
from land_registry.routers import enrichment
from land_registry.solar_economics import (
    SolarSourceUnavailable,
    annual_yield_from_municipal_potential,
    assumptions_from_request,
    calculate_economics,
)


def test_reference_case_matches_shared_economics_contract():
    request = SolarEconomicsRequest(
        system_kwp=5,
        installation_cost_eur_per_kwp=1200,
        electricity_price_eur_per_kwh=0.30,
        export_price_eur_per_kwh=0.08,
        self_consumption_pct=40,
        annual_maintenance_cost_eur_per_kwp=15,
        annual_degradation_pct=0.5,
        discount_rate_pct=4,
        lifetime_years=25,
    )

    economics, _method = calculate_economics(
        annual_yield_kwh_per_kwp=1500,
        system_kwp=request.system_kwp,
        assumptions=assumptions_from_request(request),
    )

    assert economics["first_year_generation_kwh"] == pytest.approx(7500)
    assert economics["installation_cost_eur"] == pytest.approx(6000)
    assert economics["npv_eur"] == pytest.approx(11562.14618450342)
    assert economics["lcoe_eur_per_kwh"] == pytest.approx(0.06431359719162845)
    assert economics["simple_payback_years"] == pytest.approx(5.119331066236384)
    assert economics["discounted_payback_years"] == pytest.approx(5.853429522472389)
    assert len(economics["cash_flows"]) == 26
    assert economics["cash_flows"][0]["cash_flow_eur"] == pytest.approx(-6000)


def test_municipal_potential_yield_is_derived_from_modern_output_and_capacity():
    production = annual_yield_from_municipal_potential({
        "available": True,
        "source": "aecs4u-stats solar.solar_potential_comuni",
        "dataset_version": "pvgis:v5.3;sarah3:2025-10;model:v1.1",
        "updated_at": "2026-05-11T10:35:00",
        "data": {
            "pv_pvout_modern_kwh_year_total": 482335613.9766447,
            "pv_kwp_max_total": 330034.64707303175,
        },
    })

    assert production["annual_yield_kwh_per_kwp"] == pytest.approx(1461.47, abs=0.01)
    assert production["spatial_resolution"] == "municipality"
    assert production["dataset_version"].startswith("pvgis:v5.3")


@pytest.mark.asyncio
async def test_solar_economics_route_uses_stats_municipal_potential(monkeypatch):
    async def parcel_for_reference(_reference, *, id=None):
        return {"type": "Feature", "properties": {"comune_code": "G273"}, "geometry": None}

    async def municipality_for_code(_code):
        return {"istat_code": "082053", "official_name": "Palermo"}

    async def solar_profile_for_istat(_code):
        return {
            "available": True,
            "source": "aecs4u-stats solar.solar_potential_comuni",
            "dataset_version": "pvgis:v5.3;sarah3:2025-10;model:v1.1",
            "updated_at": "2026-05-11T10:35:00",
            "match_method": "cadastral municipality code",
            "spatial_resolution": "municipality",
            "data": {
                "pv_pvout_modern_kwh_year_total": 482335613.9766447,
                "pv_kwp_max_total": 330034.64707303175,
            },
        }

    monkeypatch.setattr(enrichment, "get_parcel_by_reference", parcel_for_reference)
    monkeypatch.setattr(enrichment.stats_service, "aget_municipality_by_cadastral_code", municipality_for_code)
    monkeypatch.setattr(enrichment.stats_service, "aget_municipality_solar_potential", solar_profile_for_istat)

    result = await enrichment.post_parcel_solar_economics(
        "G273_000900.3205", SolarEconomicsRequest(system_kwp=5)
    )

    assert result["location"]["istat_code"] == "082053"
    assert result["location"]["spatial_resolution"] == "municipality"
    assert result["production"]["annual_yield_kwh_per_kwp"] == pytest.approx(1461.47, abs=0.01)
    assert result["provenance"]["source"] == "aecs4u-stats solar.solar_potential_comuni"
    assert result["calculation_method"]


@pytest.mark.asyncio
async def test_solar_economics_route_reports_unavailable_municipal_view(monkeypatch):
    async def parcel_for_reference(_reference, *, id=None):
        return {"type": "Feature", "properties": {"comune_code": "G273"}, "geometry": None}

    async def municipality_for_code(_code):
        return {"istat_code": "082053", "official_name": "Palermo"}

    async def no_solar_profile(_code):
        return None

    monkeypatch.setattr(enrichment, "get_parcel_by_reference", parcel_for_reference)
    monkeypatch.setattr(enrichment.stats_service, "aget_municipality_by_cadastral_code", municipality_for_code)
    monkeypatch.setattr(enrichment.stats_service, "aget_municipality_solar_potential", no_solar_profile)

    with pytest.raises(HTTPException) as caught:
        await enrichment.post_parcel_solar_economics("G273_000900.3205", SolarEconomicsRequest())

    assert caught.value.status_code == 503
    assert "Municipal solar potential is unavailable" in caught.value.detail
