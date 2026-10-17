"""Municipal PV-potential yield and editable cash-flow scenarios.

The source data comes from the ``solar.solar_potential_comuni`` foreign table
exposed by aecs4u-stats. The view stores municipal annual output and estimated
capacity; their ratio is a municipality-level kWh/kWp yield, not a parcel or
roof measurement. The user supplies installed capacity and financial inputs.

Use the shared aecs4u-stats economics implementation when installed. The small
compatibility calculation keeps this app usable with older stats wheels.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

try:
    from aecs4u_stats.pvgis.economics import (
        SolarEconomicsAssumptions as _SharedSolarEconomicsAssumptions,
        calculate_solar_economics as _shared_calculate_solar_economics,
    )
except ImportError:  # published aecs4u-stats wheel predates issue #31
    _SharedSolarEconomicsAssumptions = None
    _shared_calculate_solar_economics = None


PVGIS_MODEL = "PVGIS-derived municipal solar potential"
PVGIS_SOURCE_URL = "https://re.jrc.ec.europa.eu/pvg_tools/en/"


class SolarSourceUnavailable(RuntimeError):
    """The Stats municipal solar view has no usable yield for this comune."""


@dataclass(frozen=True)
class SolarScenarioAssumptions:
    installation_cost_eur_per_kwp: float = 1200.0
    electricity_price_eur_per_kwh: float = 0.30
    export_price_eur_per_kwh: float = 0.08
    self_consumption_fraction: float = 0.40
    annual_maintenance_cost_eur_per_kwp: float = 15.0
    annual_degradation_rate: float = 0.005
    discount_rate: float = 0.04
    lifetime_years: int = 25


def annual_yield_from_municipal_potential(profile: dict[str, Any] | None) -> dict[str, Any]:
    """Derive annual kWh/kWp from the Stats-side municipal potential view."""
    data = profile.get("data") if isinstance(profile, dict) else None
    if not isinstance(data, dict):
        raise SolarSourceUnavailable("Municipal solar potential is unavailable")
    try:
        annual_output = float(data["pv_pvout_modern_kwh_year_total"])
        estimated_capacity = float(data["pv_kwp_max_total"])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise SolarSourceUnavailable("Municipal solar potential has no modern output/capacity pair") from exc
    if (
        not math.isfinite(annual_output)
        or not math.isfinite(estimated_capacity)
        or annual_output <= 0
        or estimated_capacity <= 0
    ):
        raise SolarSourceUnavailable("Municipal solar potential has an invalid output/capacity pair")

    return {
        "annual_yield_kwh_per_kwp": annual_output / estimated_capacity,
        "municipal_output_kwh_year_total": annual_output,
        "municipal_capacity_kwp_total": estimated_capacity,
        "model": PVGIS_MODEL,
        "source_url": PVGIS_SOURCE_URL,
        "source": profile.get("source"),
        "dataset_version": profile.get("dataset_version"),
        "updated_at": profile.get("updated_at"),
        "match_method": profile.get("match_method") or "cadastral municipality code",
        "spatial_resolution": profile.get("spatial_resolution") or "municipality",
        "yield_method": (
            "modern municipal annual output divided by estimated municipal capacity; "
            "derived from the Stats solar potential release"
        ),
    }


def _compat_calculate_solar_economics(
    annual_yield_kwh_per_kwp: float,
    system_kwp: float,
    assumptions: SolarScenarioAssumptions,
) -> dict[str, Any]:
    """Mirror the issue #31 calculation for older installed stats releases."""
    first_year_generation = annual_yield_kwh_per_kwp * system_kwp
    capex = assumptions.installation_cost_eur_per_kwp * system_kwp
    cash_flows: list[dict[str, float | int]] = [{
        "year": 0,
        "generation_kwh": 0.0,
        "self_consumed_kwh": 0.0,
        "exported_kwh": 0.0,
        "revenue_eur": 0.0,
        "maintenance_cost_eur": 0.0,
        "cash_flow_eur": -capex,
        "discounted_cash_flow_eur": -capex,
        "cumulative_cash_flow_eur": -capex,
        "cumulative_discounted_cash_flow_eur": -capex,
    }]
    cumulative = cumulative_discounted = npv = -capex
    present_value_costs = capex
    present_value_generation = 0.0
    simple_payback: float | None = None
    discounted_payback: float | None = None

    for year in range(1, assumptions.lifetime_years + 1):
        generation = first_year_generation * (1 - assumptions.annual_degradation_rate) ** (year - 1)
        self_consumed = generation * assumptions.self_consumption_fraction
        exported = generation - self_consumed
        revenue = (
            self_consumed * assumptions.electricity_price_eur_per_kwh
            + exported * assumptions.export_price_eur_per_kwh
        )
        maintenance = assumptions.annual_maintenance_cost_eur_per_kwp * system_kwp
        cash_flow = revenue - maintenance
        discount_factor = (1 + assumptions.discount_rate) ** year
        discounted_cash_flow = cash_flow / discount_factor
        previous_cumulative, previous_discounted = cumulative, cumulative_discounted
        cumulative += cash_flow
        cumulative_discounted += discounted_cash_flow
        npv += discounted_cash_flow
        present_value_costs += maintenance / discount_factor
        present_value_generation += generation / discount_factor

        if simple_payback is None and previous_cumulative < 0 <= cumulative and cash_flow > 0:
            simple_payback = year - 1 + (-previous_cumulative / cash_flow)
        if discounted_payback is None and previous_discounted < 0 <= cumulative_discounted and discounted_cash_flow > 0:
            discounted_payback = year - 1 + (-previous_discounted / discounted_cash_flow)

        cash_flows.append({
            "year": year,
            "generation_kwh": generation,
            "self_consumed_kwh": self_consumed,
            "exported_kwh": exported,
            "revenue_eur": revenue,
            "maintenance_cost_eur": maintenance,
            "cash_flow_eur": cash_flow,
            "discounted_cash_flow_eur": discounted_cash_flow,
            "cumulative_cash_flow_eur": cumulative,
            "cumulative_discounted_cash_flow_eur": cumulative_discounted,
        })

    return {
        "system_kwp": system_kwp,
        "annual_yield_kwh_per_kwp": annual_yield_kwh_per_kwp,
        "first_year_generation_kwh": first_year_generation,
        "installation_cost_eur": capex,
        "npv_eur": npv,
        "lcoe_eur_per_kwh": present_value_costs / present_value_generation if present_value_generation > 0 else None,
        "simple_payback_years": simple_payback,
        "discounted_payback_years": discounted_payback,
        "lifetime_years": assumptions.lifetime_years,
        "assumptions": asdict(assumptions),
        "cash_flows": cash_flows,
        "model_scope": "pre-tax; no incentives, financing, or inverter replacement",
    }


def assumptions_from_request(scenario: Any) -> SolarScenarioAssumptions:
    """Convert the validated public request shape into the calculator's rates."""
    def get(name: str) -> Any:
        if isinstance(scenario, dict):
            return scenario[name]
        return getattr(scenario, name)

    return SolarScenarioAssumptions(
        installation_cost_eur_per_kwp=get("installation_cost_eur_per_kwp"),
        electricity_price_eur_per_kwh=get("electricity_price_eur_per_kwh"),
        export_price_eur_per_kwh=get("export_price_eur_per_kwh"),
        self_consumption_fraction=get("self_consumption_pct") / 100,
        annual_maintenance_cost_eur_per_kwp=get("annual_maintenance_cost_eur_per_kwp"),
        annual_degradation_rate=get("annual_degradation_pct") / 100,
        discount_rate=get("discount_rate_pct") / 100,
        lifetime_years=get("lifetime_years"),
    )


def calculate_economics(
    annual_yield_kwh_per_kwp: float,
    system_kwp: float,
    assumptions: SolarScenarioAssumptions,
) -> tuple[dict[str, Any], str]:
    """Use the shared stats calculator when installed, otherwise its compatibility mirror."""
    if _shared_calculate_solar_economics is not None and _SharedSolarEconomicsAssumptions is not None:
        shared_assumptions = _SharedSolarEconomicsAssumptions(**asdict(assumptions))
        result = _shared_calculate_solar_economics(
            annual_yield_kwh_per_kwp,
            system_kwp,
            shared_assumptions,
        )
        return result, "aecs4u_stats.pvgis.calculate_solar_economics"
    return (
        _compat_calculate_solar_economics(annual_yield_kwh_per_kwp, system_kwp, assumptions),
        "land_registry compatibility calculation matching aecs4u-stats issue #31",
    )
