"""Parcel-located PVGIS yield and editable cash-flow scenarios.

PVGIS supplies a long-term annual yield for a 1 kWp reference system at the
parcel centroid. The user supplies the installed capacity, surface position,
system losses and all financial assumptions. This module never converts
parcel area into usable roof area.

Use the shared aecs4u-stats economics implementation when it is installed.
The small compatibility implementation below keeps this app usable with
older published stats wheels until the shared implementation is released.
"""

from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass
from typing import Any

import httpx

try:
    from aecs4u_stats.pvgis.economics import (
        SolarEconomicsAssumptions as _SharedSolarEconomicsAssumptions,
        calculate_solar_economics as _shared_calculate_solar_economics,
    )
except ImportError:  # published aecs4u-stats wheel predates issue #31
    _SharedSolarEconomicsAssumptions = None
    _shared_calculate_solar_economics = None


PVGIS_BASE_URL = "https://re.jrc.ec.europa.eu/api/v5_3/PVcalc"
PVGIS_MODEL = "PVGIS 5.3 / PVGIS-SARAH3"
PVGIS_DATA_PERIOD = "2005–2023"
PVGIS_SOURCE_URL = (
    "https://joint-research-centre.ec.europa.eu/photovoltaic-geographical-information-system-pvgis/"
    "using-pvgis-5/api-non-interactive-service_en"
)
PVGIS_TIMEOUT = httpx.Timeout(15.0, connect=4.0)
_CACHE_TTL_SECONDS = 24 * 60 * 60
_CACHE_MAX_ENTRIES = 512
_YIELD_CACHE: OrderedDict[tuple[Any, ...], tuple[float, dict[str, Any]]] = OrderedDict()
_CACHE_LOCK = threading.Lock()


class SolarSourceTimeout(RuntimeError):
    """PVGIS did not respond within the request time budget."""


class SolarSourceUnavailable(RuntimeError):
    """PVGIS returned an error or a response without a usable yield."""


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


def _yield_cache_key(lat: float, lng: float, tilt: float, aspect: float, loss: float) -> tuple[Any, ...]:
    # PVGIS radiation data has a coarser spatial resolution than these rounded
    # inputs. Four decimals keep the request reproducible while avoiding a
    # separate cache entry for sub-metre coordinate noise.
    return (round(lat, 4), round(lng, 4), round(tilt, 1), round(aspect, 1), round(loss, 1), PVGIS_MODEL)


def _cached_yield(key: tuple[Any, ...]) -> dict[str, Any] | None:
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _YIELD_CACHE.get(key)
        if cached is None:
            return None
        expires_at, payload = cached
        if expires_at <= now:
            _YIELD_CACHE.pop(key, None)
            return None
        _YIELD_CACHE.move_to_end(key)
        return dict(payload)


def _store_yield(key: tuple[Any, ...], payload: dict[str, Any]) -> None:
    with _CACHE_LOCK:
        _YIELD_CACHE[key] = (time.monotonic() + _CACHE_TTL_SECONDS, dict(payload))
        _YIELD_CACHE.move_to_end(key)
        while len(_YIELD_CACHE) > _CACHE_MAX_ENTRIES:
            _YIELD_CACHE.popitem(last=False)


def _monthly_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    outputs = payload.get("outputs")
    monthly = outputs.get("monthly") if isinstance(outputs, dict) else None
    if isinstance(monthly, list):
        rows = monthly
    elif isinstance(monthly, dict):
        rows = monthly.get("fixed")
        if not isinstance(rows, list):
            rows = next((value for value in monthly.values() if isinstance(value, list)), None)
    else:
        rows = None
    if not isinstance(rows, list) or len(rows) != 12:
        raise SolarSourceUnavailable("PVGIS did not return twelve monthly production values")
    return rows


async def annual_yield_per_kwp(
    lat: float,
    lng: float,
    tilt_degrees: float,
    aspect_degrees: float,
    system_losses_pct: float,
) -> dict[str, Any]:
    """Fetch (or reuse) PVGIS long-term yield for a 1 kWp building-mounted system."""
    key = _yield_cache_key(lat, lng, tilt_degrees, aspect_degrees, system_losses_pct)
    cached = _cached_yield(key)
    if cached is not None:
        return cached

    params = {
        "lat": key[0],
        "lon": key[1],
        "raddatabase": "PVGIS-SARAH3",
        "peakpower": 1,
        "loss": key[4],
        "angle": key[2],
        "aspect": key[3],
        "mountingplace": "building",
        "fixed": 1,
        "trackingtype": 0,
        "outputformat": "json",
    }
    try:
        async with httpx.AsyncClient(timeout=PVGIS_TIMEOUT) as client:
            response = await client.get(PVGIS_BASE_URL, params=params)
            response.raise_for_status()
            payload = response.json()
    except httpx.TimeoutException as exc:
        raise SolarSourceTimeout("PVGIS request timed out") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise SolarSourceUnavailable("PVGIS request failed") from exc

    if not isinstance(payload, dict):
        raise SolarSourceUnavailable("PVGIS returned an invalid response")
    try:
        monthly_rows = _monthly_rows(payload)
        monthly_yield = [float(row["E_m"]) for row in monthly_rows]
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise SolarSourceUnavailable("PVGIS returned an invalid production response") from exc
    if any(not math.isfinite(value) or value < 0 for value in monthly_yield):
        raise SolarSourceUnavailable("PVGIS returned invalid monthly production values")

    annual_yield = sum(monthly_yield)
    if annual_yield <= 0:
        raise SolarSourceUnavailable("PVGIS returned no annual production")
    result = {
        "annual_yield_kwh_per_kwp": annual_yield,
        "monthly_yield_kwh_per_kwp": monthly_yield,
        "reference_system_kwp": 1.0,
        "mounting_place": "building",
        "model": PVGIS_MODEL,
        "data_period": PVGIS_DATA_PERIOD,
        "source_url": PVGIS_SOURCE_URL,
    }
    _store_yield(key, result)
    return result


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
