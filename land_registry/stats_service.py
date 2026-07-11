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
from functools import lru_cache
from typing import Any, Dict, List, Optional

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
from aecs4u_stats.omi import omi_db_available, quote_history, quotes_for_comune
from aecs4u_stats.osm.config import POI_CATEGORIES
from aecs4u_stats.osm.pois import pois_within_radius, resolve_poi_db

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
        "osm_pois": {
            "available": poi_db_available(),
            "categories": sorted(POI_CATEGORIES),
        },
        "omi_quotes": {
            "available": omi_db_available(),
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
