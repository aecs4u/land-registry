import pytest
from fastapi import HTTPException

from land_registry.models import SolarEconomicsRequest
from land_registry.routers import enrichment
from land_registry.solar_economics import (
    SolarSourceTimeout,
    SolarSourceUnavailable,
    assumptions_from_request,
    calculate_economics,
)


def test_reference_case_matches_shared_economics_contract():
    request = SolarEconomicsRequest(
        system_kwp=5,
        tilt_degrees=30,
        aspect_degrees=0,
        system_losses_pct=14,
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


@pytest.mark.parametrize(
    ("source_error_type", "expected_status", "expected_detail"),
    [
        (SolarSourceTimeout, 504, "PVGIS did not respond in time"),
        (SolarSourceUnavailable, 503, "PVGIS is temporarily unavailable"),
    ],
)
@pytest.mark.asyncio
async def test_solar_economics_route_reports_pvgis_timeout_and_unavailable_states(
    monkeypatch, source_error_type, expected_status, expected_detail
):
    async def parcel_for_reference(_reference, *, id=None):
        return {"type": "Feature", "properties": {}, "geometry": {"type": "Polygon"}}

    def centroid(_parcel):
        return {"lat": 41.9, "lng": 12.5}

    async def failed_yield(**_kwargs):
        raise source_error_type("simulated upstream failure")

    monkeypatch.setattr(enrichment, "get_parcel_by_reference", parcel_for_reference)
    monkeypatch.setattr(enrichment.stats_service, "_parcel_centroid", centroid)
    monkeypatch.setattr(enrichment, "annual_yield_per_kwp", failed_yield)

    with pytest.raises(HTTPException) as caught:
        await enrichment.post_parcel_solar_economics("G273_000900.3205", SolarEconomicsRequest())

    assert caught.value.status_code == expected_status
    assert expected_detail in caught.value.detail
