"""
Adapter over the ``aecs4u-stats`` package: Italian public reference data
(ISTAT municipalities/population, OSM POIs, OMI real-estate quotes, MEF/IRPEF
income, natural hazards) consumed by the parcel-enrichment endpoints.

All lookups degrade gracefully: when the underlying data stores have not been
built on this host (see ``ISTAT_DATA_DIR``, default ``~/.aecs4u_stats/istat``),
functions return ``None``/empty results instead of raising, so the map keeps
working without the enrichment layer.

Data stores are built with the aecs4u-stats pipeline, e.g.:
    python -m aecs4u_stats.istat.scripts.import_data
    python -m aecs4u_stats.osm.scripts.download_pois
    python -m aecs4u_stats.omi.scripts.import_omi --data-dir /path/to/OMI
    python -m aecs4u_stats.mef.scripts.import_irpef --year 2022
    python -m aecs4u_stats.hazards.scripts.import_seismic --file classificazione.xlsx

The IdroGEO (flood/landslide), FIRMS (active fires) and DPC bulletin datasets
are runtime API clients (no local store) — see ``get_environmental_risks``,
``get_active_fires`` and ``get_criticality_bulletin`` below.
"""

import logging
import math
import re
from functools import lru_cache
from typing import Any, Dict, List, Optional

from aecs4u_stats.cadastral import (
    cadastral_db_available,
    fogli_for_comune,
    parcel_at_point,
    parcel_by_reference,
    parcels_for_comune,
    parcels_in_bbox,
)
from aecs4u_stats.census import census_db_available as _census_db_available
from aecs4u_stats.census import section_at_point as _census_section_at_point
from aecs4u_stats.census import sections_for_comune as _census_sections_for_comune
from aecs4u_stats.hazards import (
    active_fires as _active_fires,
    get_comune_hazards,
    latest_criticality_bulletin,
    seismic_db_available,
    seismic_zone,
    summarize_hazards,
)
from aecs4u_stats.istat.config import ISTAT_SQLITE_PATH
from aecs4u_stats.mef import income_by_cadastral_code, mef_db_available
from aecs4u_stats.omi import (
    OMI_ZONES_DIR,
    omi_db_available,
    quote_history,
    quotes_for_comune,
    zone_boundaries,
    zone_boundaries_available,
)
from aecs4u_stats.osm.config import POI_CATEGORIES
from aecs4u_stats.osm.pois import pois_within_radius, resolve_poi_db
from shapely.geometry import Point, shape

logger = logging.getLogger(__name__)


def istat_db_available() -> bool:
    """True when the ISTAT reference SQLite store exists on this host."""
    return ISTAT_SQLITE_PATH.exists() and ISTAT_SQLITE_PATH.stat().st_size > 0


def poi_db_available() -> bool:
    """True when the OSM POI SQLite store exists on this host."""
    return resolve_poi_db() is not None


def omi_db_available_public() -> bool:
    """True when the OMI store exists on this host."""
    return omi_db_available()


def mef_db_available_public() -> bool:
    """True when the MEF/IRPEF store exists on this host."""
    return mef_db_available()


def enrichment_status() -> Dict[str, Any]:
    """Availability report for every aecs4u-stats dataset we consume."""
    return {
        "istat_municipalities": {
            "available": istat_db_available(),
            "path": str(ISTAT_SQLITE_PATH),
        },
        "cadastral_parcels": {
            "available": cadastral_db_available(),
            "note": "per-region DuckDB stores; build with python -m aecs4u_stats.cadastral.scripts.import_cadastral",
        },
        "census_sections": {
            "available": _census_db_available(),
            "note": "2021 ISTAT census sections; build with python -m aecs4u_stats.census.scripts.import_census_sections --all",
        },
        "istat_safety": {
            "available": safety_db_available(),
            "note": "reported-crime rates (delitti denunciati), province level",
        },
        "istat_bes": {
            "available": bes_db_available(),
            "note": "BES territorial quality-of-life indicators, province level",
        },
        "istat_demographic": {
            "available": demographic_db_available(),
            "note": "ISTAT demographic indicators, province level",
        },
        "osm_pois": {
            "available": poi_db_available(),
            "categories": sorted(POI_CATEGORIES),
        },
        "omi_quotes": {
            "available": omi_db_available(),
        },
        "omi_zone_boundaries": {
            "available": OMI_ZONES_DIR.exists() and any(OMI_ZONES_DIR.glob("*.geojson")),
            "path": str(OMI_ZONES_DIR),
        },
        "mef_irpef": {
            "available": mef_db_available(),
        },
        "hazards_seismic": {
            "available": seismic_db_available(),
        },
        "hazards_idrogeo": {
            "available": True,  # runtime API, no local store
            "note": "ISPRA IdroGEO — live API, requires network access",
        },
        "hazards_firms": {
            "available": True,
            "note": "NASA FIRMS — live API, requires FIRMS_MAP_KEY",
        },
        "hazards_bulletin": {
            "available": True,
            "note": "Protezione Civile — live API, requires network access",
        },
    }


@lru_cache(maxsize=1)
def _istat_engine():
    """Lazily created read-only SQLModel engine over the ISTAT store."""
    from sqlmodel import create_engine

    return create_engine(f"sqlite:///{ISTAT_SQLITE_PATH}", echo=False)


def get_municipality_by_cadastral_code(cadastral_code: str) -> Optional[Dict[str, Any]]:
    """
    Municipality profile for a catasto comune code (e.g. ``C773`` → Civitavecchia).

    The cadastral code is the join key between Agenzia delle Entrate parcel
    attributes (``ADMINISTRATIVEUNIT`` / NATIONALCADASTRALREFERENCE prefix) and
    the ISTAT administrative hierarchy.

    Returns ``None`` when the store is missing or the code is unknown.
    """
    if not istat_db_available():
        return None

    from sqlmodel import Session, select

    from aecs4u_stats.istat.models import (
        IstatMunicipality,
        IstatProvince,
        IstatRegion,
        MunicipalityPopulationStat,
    )

    code = cadastral_code.strip().upper()
    try:
        with Session(_istat_engine()) as session:
            muni = session.exec(
                select(IstatMunicipality).where(IstatMunicipality.cadastral_code == code)
            ).first()
            if muni is None:
                return None

            province = session.get(IstatProvince, muni.province_id)
            region = session.get(IstatRegion, muni.region_id)
            pop_stats = session.exec(
                select(MunicipalityPopulationStat)
                .where(MunicipalityPopulationStat.municipality_id == muni.id)
                .order_by(MunicipalityPopulationStat.year.desc())  # type: ignore[union-attr]
            ).all()

            latest_pop = pop_stats[0] if pop_stats else None
            return {
                "cadastral_code": muni.cadastral_code,
                "istat_code": muni.alphanumeric_code,
                "procom": muni.id,  # numeric national comune code — join key for census sections
                "name": muni.italian_name or muni.official_name,
                "province": province.name if province else None,
                "province_sigla": province.vehicle_code if province else None,
                "region": region.name if region else None,
                "nuts3": province.nuts3_2024 if province else None,
                "is_provincial_capital": muni.is_provincial_capital,
                "latitude": muni.latitude,
                "longitude": muni.longitude,
                "postal_code": muni.postal_code,
                "website": muni.website,
                "wikipedia_url": muni.wikipedia_url,
                "coat_of_arms_url": muni.coat_of_arms_url,
                "population": {
                    "year": latest_pop.year,
                    "resident_population": latest_pop.resident_population,
                }
                if latest_pop
                else None,
                "population_history": [
                    {"year": s.year, "resident_population": s.resident_population}
                    for s in reversed(pop_stats)
                ],
                "source": "ISTAT via aecs4u-stats",
            }
    except Exception as e:  # pragma: no cover - defensive: store may be partial
        logger.warning("ISTAT municipality lookup failed for %s: %s", code, e)
        return None


def get_pois_near(
    lat: float,
    lng: float,
    radius_km: float = 1.0,
    categories: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    POIs around a point, grouped by category with ``distance_km``, nearest-first.

    Backed by the aecs4u-stats OSM POI store; returns empty category lists when
    the store has not been built.
    """
    grouped = pois_within_radius(lat, lng, radius_km=radius_km, categories=categories)
    return {
        "center": {"lat": lat, "lng": lng},
        "radius_km": radius_km,
        "total": sum(len(v) for v in grouped.values()),
        "categories": grouped,
        "source": "OpenStreetMap via aecs4u-stats",
    }


def get_omi_quotes(comune: str, zona: Optional[str] = None) -> Dict[str, Any]:
    """
    OMI (Osservatorio Mercato Immobiliare) quotes for a comune's latest
    semester: per-zone/typology sale + rent €/m² ranges.

    ``comune`` accepts either the catasto code (e.g. ``C773``) or the ISTAT
    numeric code. Pass ``zona`` (e.g. ``B1``) to filter to one OMI zone.
    """
    rows = quotes_for_comune(comune, zona=zona)
    return {
        "comune": comune,
        "zona": zona,
        "quotes": rows,
        "source": "Agenzia delle Entrate OMI via aecs4u-stats",
    }


def get_omi_history(comune: str, zona: str, cod_tipologia: Optional[str] = None) -> Dict[str, Any]:
    """Full semester history (oldest-first) of OMI quotes for one comune/zone."""
    rows = quote_history(comune, zona, cod_tipologia=cod_tipologia)
    return {
        "comune": comune,
        "zona": zona,
        "history": rows,
        "source": "Agenzia delle Entrate OMI via aecs4u-stats",
    }


def estimate_omi_value(
    comune: str,
    zona: str,
    cod_tipologia: str,
    area_sqm: float,
    stato_conservazione: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Return a reproducible, explicitly non-appraisal OMI value range."""
    try:
        area = float(area_sqm)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(area) or area <= 0:
        return None
    rows = quotes_for_comune(comune, zona=zona)
    type_code = str(cod_tipologia).strip().upper()
    state = stato_conservazione.strip().casefold() if stato_conservazione else None
    matches = [
        row
        for row in rows
        if str(row.get("cod_tipologia", "")).strip().upper() == type_code
        and (
            state is None
            or str(row.get("stato_conservazione", "")).strip().casefold() == state
        )
    ]
    if len(matches) != 1:
        return None

    quote = matches[0]
    try:
        min_rate = float(quote["prezzo_min"])
        max_rate = float(quote["prezzo_max"])
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(min_rate) or not math.isfinite(max_rate) or min_rate < 0 or max_rate < min_rate:
        return None

    return {
        "methodology": "omi-area-range-v1",
        "input": {
            "comune": comune,
            "zona": zona.strip().upper(),
            "cod_tipologia": str(quote.get("cod_tipologia", cod_tipologia)),
            "stato_conservazione": quote.get("stato_conservazione"),
            "area_sqm": area,
        },
        "quote": {
            "anno": quote.get("anno"),
            "semestre": quote.get("semestre"),
            "tipologia": quote.get("tipologia"),
            "prezzo_min_eur_sqm": min_rate,
            "prezzo_max_eur_sqm": max_rate,
        },
        "value_range_eur": {
            "min": round(area * min_rate, 2),
            "max": round(area * max_rate, 2),
        },
        "disclaimer": (
            "Stima indicativa ottenuta moltiplicando la superficie dichiarata per "
            "l'intervallo OMI selezionato. Non è una perizia né una valutazione immobiliare."
        ),
        "source": "Agenzia delle Entrate OMI via aecs4u-stats",
    }


@lru_cache(maxsize=128)
def _load_omi_zone_boundaries(province: str) -> Dict[str, Any]:
    """Load one province's boundary collection once per worker process."""
    return zone_boundaries(province)


def _omi_zone_code(properties: Dict[str, Any]) -> Optional[str]:
    """Extract a quote-compatible zone code from heterogeneous AdE KML fields."""
    normalized = {
        re.sub(r"[^a-z0-9]", "", str(key).lower()): value
        for key, value in properties.items()
    }
    candidates = [
        normalized.get(key)
        for key in ("codzona", "codicezona", "zonaomi", "zonecode", "zona", "name")
    ]
    candidates.extend(properties.values())
    for value in candidates:
        if value is None or isinstance(value, (dict, list)):
            continue
        text = str(value).strip().upper()
        exact = re.fullmatch(r"[A-Z][0-9]{1,3}", text)
        match = exact or re.search(r"(?:^|\b)([A-Z][0-9]{1,3})(?:\b|$)", text)
        if match:
            return match.group(0) if exact else match.group(1)
    return None


def get_omi_zone_at_point(province: str, lat: float, lng: float) -> Dict[str, Any]:
    """Match a WGS84 point to an OMI zone polygon for one province."""
    result: Dict[str, Any] = {
        "province": province,
        "point": {"lat": lat, "lng": lng},
        "matched": False,
        "zone": None,
        "source": "Agenzia delle Entrate OMI via aecs4u-stats",
    }
    if not zone_boundaries_available(province):
        result["reason"] = "boundary_data_unavailable"
        return result

    point = Point(lng, lat)
    for feature in _load_omi_zone_boundaries(province).get("features", []):
        geometry = feature.get("geometry")
        if not geometry:
            continue
        try:
            if not shape(geometry).covers(point):
                continue
        except (TypeError, ValueError):
            logger.warning("Invalid OMI boundary geometry for province %s", province)
            continue
        properties = feature.get("properties") or {}
        zone = _omi_zone_code(properties)
        if not zone:
            result["reason"] = "zone_code_unavailable"
            return result
        result.update({"matched": True, "zone": zone})
        return result

    result["reason"] = "point_outside_zones"
    return result


def get_income_profile(cadastral_code: str, year: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """
    MEF/IRPEF income profile for a comune by catasto code: taxpayer count,
    mean taxable income, and the 8-bracket income distribution.
    """
    return income_by_cadastral_code(cadastral_code, year=year)


def get_environmental_risks(istat_code: int | str) -> Optional[Dict[str, Any]]:
    """
    Environmental risk profile for a comune (ISTAT code): DPC seismic zone
    (1-4, local store) plus ISPRA IdroGEO flood/landslide indicators (live
    API). Either half may be ``None`` if its source is unavailable.
    """
    seismic = seismic_zone(istat_code)
    raw_hazards = get_comune_hazards(istat_code)
    hazards = summarize_hazards(raw_hazards) if raw_hazards else None

    if seismic is None and hazards is None:
        return None

    return {
        "istat_code": istat_code,
        "seismic": seismic,
        "hydrogeological": hazards,
    }


def get_active_fires(radius_km: float = 50.0, lat: Optional[float] = None, lng: Optional[float] = None) -> Dict[str, Any]:
    """
    Active-fire detections (NASA FIRMS) near a point, or across all of Italy
    when no point is given. Requires the ``FIRMS_MAP_KEY`` environment
    variable; returns an empty list otherwise.
    """
    from aecs4u_stats.hazards.config import ITALY_BBOX

    if lat is not None and lng is not None:
        km_per_deg = 111.0
        dlat = radius_km / km_per_deg
        dlng = radius_km / (km_per_deg * max(0.1, abs(math.cos(math.radians(lat)))))
        bbox = (lng - dlng, lat - dlat, lng + dlng, lat + dlat)
    else:
        bbox = ITALY_BBOX

    detections = _active_fires(bbox=bbox)
    return {
        "bbox": bbox,
        "count": len(detections),
        "detections": detections,
        "source": "NASA FIRMS via aecs4u-stats",
    }


def get_criticality_bulletin() -> Optional[Dict[str, Any]]:
    """Latest Protezione Civile hydro-criticality bulletin (allerta meteo), or
    ``None`` if the live API is unreachable."""
    return latest_criticality_bulletin()


def cadastral_store_available() -> bool:
    """True when at least one per-region cadastral parcel store is built."""
    return cadastral_db_available()


def get_parcels(
    comune: str,
    foglio: Optional[str] = None,
    particella: Optional[str] = None,
    limit: int = 1000,
    offset: int = 0,
    include_geometry: bool = True,
) -> Dict[str, Any]:
    """Cadastral parcels of a comune (optionally one foglio/particella) as a
    GeoJSON FeatureCollection, from the aecs4u-stats per-region DuckDB stores.

    Parcel ``sheet_number`` is spatially derived at build time — reliable
    nationally, unlike parsing NATIONALCADASTRALREFERENCE."""
    fc = parcels_for_comune(
        comune, foglio=foglio, particella=particella,
        limit=limit, offset=offset, include_geometry=include_geometry,
    )
    fc["metadata"]["source"] = "Agenzia delle Entrate INSPIRE via aecs4u-stats"
    return fc


def get_fogli(comune: str) -> Dict[str, Any]:
    """Sheet (foglio) list for a comune with per-sheet parcel counts."""
    return {
        "comune": comune,
        "fogli": fogli_for_comune(comune),
        "source": "Agenzia delle Entrate INSPIRE via aecs4u-stats",
    }


def get_parcel_by_reference(national_reference: str) -> Optional[Dict[str, Any]]:
    """One parcel Feature by exact NATIONALCADASTRALREFERENCE, or ``None``."""
    return parcel_by_reference(national_reference)


def get_parcel_at_point(lat: float, lng: float) -> Optional[Dict[str, Any]]:
    """The parcel Feature containing a WGS84 point, or ``None``."""
    return parcel_at_point(lat, lng)


def get_parcels_in_bbox(
    min_lng: float, min_lat: float, max_lng: float, max_lat: float,
    limit: int = 5000, include_geometry: bool = True,
) -> Dict[str, Any]:
    """Parcels intersecting a WGS84 bbox, across all built region stores."""
    fc = parcels_in_bbox(min_lng, min_lat, max_lng, max_lat, limit=limit,
                         include_geometry=include_geometry)
    fc["metadata"]["source"] = "Agenzia delle Entrate INSPIRE via aecs4u-stats"
    return fc


# ---------------------------------------------------------------------------
# ISTAT census sections (2021) — population/education/employment/foreign-
# resident/household/dwelling-occupancy indicators at ~756,000-section
# national granularity, the finest official socioeconomic signal available.
# ---------------------------------------------------------------------------

def census_db_available() -> bool:
    """True when the 2021 census-sections store has been built on this host."""
    return _census_db_available()


def get_census_sections(cadastral_code: str, limit: int = 5000) -> Optional[Dict[str, Any]]:
    """Census sections covering a comune (by catasto code) as a GeoJSON
    FeatureCollection — each Feature's properties carry the 119 raw ISTAT
    indicator counts plus a ``ratios`` sub-dict (education/employment/
    foreign-resident/vacancy rate, avg household size)."""
    muni = get_municipality_by_cadastral_code(cadastral_code)
    if muni is None or muni.get("procom") is None:
        return None
    fc = _census_sections_for_comune(muni["procom"], limit=limit)
    fc["metadata"]["source"] = "ISTAT Basi Territoriali 2021 via aecs4u-stats"
    return fc


def get_census_section_at_point(lat: float, lng: float) -> Optional[Dict[str, Any]]:
    """The census section containing a WGS84 point, or ``None``."""
    return _census_section_at_point(lat, lng)


# ---------------------------------------------------------------------------
# ISTAT safety/crime, BES quality-of-life, demographic indicators — province
# (NUTS3) level. Resolved from a comune's catasto code via its province's
# nuts3 code (see get_municipality_by_cadastral_code); no comune-level
# granularity is published for these datasets.
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _istat_query_engine():
    """Lazily created DuckDB-backed ISTATQueryEngine (separate from
    ``_istat_engine()`` above, which is the SQLite/SQLModel municipality store)."""
    from aecs4u_stats.istat.queries import ISTATQueryEngine

    return ISTATQueryEngine()


def safety_db_available() -> bool:
    return _istat_query_engine().safety_available()


def bes_db_available() -> bool:
    return _istat_query_engine().bes_available()


def demographic_db_available() -> bool:
    return _istat_query_engine().demographic_available()


def _resolve_nuts3(cadastral_code: str) -> Optional[Dict[str, Any]]:
    muni = get_municipality_by_cadastral_code(cadastral_code)
    if muni is None or not muni.get("nuts3"):
        return None
    return muni


def get_crime_profile(cadastral_code: str, year: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Reported-crime (delitti denunciati) profile for a comune's province —
    ISTAT publishes this at province (NUTS3), not comune, granularity."""
    muni = _resolve_nuts3(cadastral_code)
    if muni is None:
        return None
    engine = _istat_query_engine()
    kpis = engine.get_safety_overview_kpis(ref_area=muni["nuts3"], year=year)
    if not kpis or kpis.get("year") is None:
        return None
    return {
        "cadastral_code": cadastral_code,
        "province": muni["province"],
        "nuts3": muni["nuts3"],
        **kpis,
        "crime_types_available": engine.list_crime_types(ref_area=muni["nuts3"]),
        "source": "ISTAT (delitti denunciati) via aecs4u-stats",
    }


def get_quality_of_life_indicators(cadastral_code: str) -> Optional[Dict[str, Any]]:
    """BES indicator codes available for a comune's province."""
    muni = _resolve_nuts3(cadastral_code)
    if muni is None:
        return None
    indicators = _istat_query_engine().list_bes_indicators(ref_area=muni["nuts3"])
    if not indicators:
        return None
    return {
        "cadastral_code": cadastral_code, "nuts3": muni["nuts3"],
        "indicators": indicators, "source": "ISTAT BES via aecs4u-stats",
    }


def get_quality_of_life_indicator(
    cadastral_code: str, data_type: str, year: Optional[int] = None
) -> Optional[Dict[str, Any]]:
    """Time series for one BES indicator (see ``get_quality_of_life_indicators``)."""
    muni = _resolve_nuts3(cadastral_code)
    if muni is None:
        return None
    series = _istat_query_engine().get_bes_indicator(data_type, ref_area=muni["nuts3"], year=year)
    if not series:
        return None
    return {
        "cadastral_code": cadastral_code, "nuts3": muni["nuts3"], "data_type": data_type,
        "series": series, "source": "ISTAT BES via aecs4u-stats",
    }


def get_demographic_indicators(cadastral_code: str) -> Optional[Dict[str, Any]]:
    """Demographic indicator codes available for a comune's province."""
    muni = _resolve_nuts3(cadastral_code)
    if muni is None:
        return None
    indicators = _istat_query_engine().list_demographic_indicators(ref_area=muni["nuts3"])
    if not indicators:
        return None
    return {
        "cadastral_code": cadastral_code, "nuts3": muni["nuts3"],
        "indicators": indicators, "source": "ISTAT demographic indicators via aecs4u-stats",
    }


def get_demographic_indicator(
    cadastral_code: str, data_type: str, year: Optional[int] = None
) -> Optional[Dict[str, Any]]:
    """Time series for one demographic indicator (see ``get_demographic_indicators``)."""
    muni = _resolve_nuts3(cadastral_code)
    if muni is None:
        return None
    series = _istat_query_engine().get_demographic_indicator(data_type, ref_area=muni["nuts3"], year=year)
    if not series:
        return None
    return {
        "cadastral_code": cadastral_code, "nuts3": muni["nuts3"], "data_type": data_type,
        "series": series, "source": "ISTAT demographic indicators via aecs4u-stats",
    }
