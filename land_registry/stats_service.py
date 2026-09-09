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

import asyncio
import logging
import hashlib
import csv
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree

try:
    from dotenv import load_dotenv

    # ``main.py`` imports the enrichment router before ``land_registry.config``
    # loads .env, so this module must make its own DSN lookup deterministic.
    load_dotenv(override=False)
except ImportError:  # pragma: no cover - dotenv is an application dependency
    pass

try:
    from aecs4u_stats.cadastral import (
        cadastral_db_available,
        fogli_for_comune,
        parcel_at_point,
        parcel_by_reference,
        parcels_for_comune,
        parcels_in_bbox,
    )
except ImportError:
    # Keep the application and cadastral map usable when an older installed
    # aecs4u-stats wheel predates the optional cadastral subpackage. The
    # enrichment endpoints already treat an unbuilt cadastral store as an
    # unavailable dataset; importing the adapter must follow the same rule.
    def cadastral_db_available(*args, **kwargs) -> bool:
        return False

    def fogli_for_comune(*args, **kwargs) -> list:
        return []

    def parcel_at_point(*args, **kwargs):
        return None

    def parcel_by_reference(*args, **kwargs):
        return None

    def parcels_for_comune(*args, **kwargs) -> Dict[str, Any]:
        return {"type": "FeatureCollection", "features": [], "metadata": {}}

    def parcels_in_bbox(*args, **kwargs) -> Dict[str, Any]:
        return {"type": "FeatureCollection", "features": [], "metadata": {}}
try:
    from aecs4u_stats.census import census_db_available as _census_db_available
    from aecs4u_stats.census import section_at_point as _census_section_at_point
    from aecs4u_stats.census import sections_for_comune as _census_sections_for_comune
except ImportError:
    # Census sections are an optional aecs4u-stats dataset. Keep the adapter
    # importable with older package releases that predate this subpackage.
    def _census_db_available(*args, **kwargs) -> bool:
        return False

    def _census_section_at_point(*args, **kwargs):
        return None

    def _census_sections_for_comune(*args, **kwargs):
        return None

try:
    from aecs4u_stats.census.config import CENSUS_STORE_PATH as _CENSUS_STORE_PATH
except ImportError:
    _CENSUS_STORE_PATH = None
from aecs4u_stats.hazards import (
    active_fires as _active_fires,
    get_comune_hazards,
    latest_criticality_bulletin,
    seismic_db_available,
    seismic_zone,
    summarize_hazards,
)
try:
    from aecs4u_stats.hazards.config import HAZARDS_DB_PATH as _HAZARDS_DB_PATH
except ImportError:
    _HAZARDS_DB_PATH = None
from aecs4u_stats.istat.config import ISTAT_SQLITE_PATH
from aecs4u_stats.mef import MEF_DB_PATH as _MEF_DB_PATH
from aecs4u_stats.mef import income_by_cadastral_code, mef_db_available
from aecs4u_stats.omi import (
    OMI_DB_PATH,
    omi_db_available,
    quote_history,
    quotes_for_comune,
)
try:
    from aecs4u_stats.omi.boundaries import (
        OMI_ZONES_DIR,
        zone_boundaries,
        zone_boundaries_available as _zone_boundaries_available,
    )
except ImportError:
    # OMI values exist in older aecs4u-stats releases, while the optional
    # boundary mirror was added later. Keep quote/enrichment imports usable
    # and report only the boundary dataset as unavailable.
    OMI_ZONES_DIR = Path(os.getenv("OMI_ZONES_DIR", "/data/istat/omi_zones"))

    def _zone_boundaries_available(*args, **kwargs) -> bool:
        return False

    def zone_boundaries(*args, **kwargs) -> Dict[str, Any]:
        return {"type": "FeatureCollection", "features": []}
from aecs4u_stats.osm.config import POI_CATEGORIES
from aecs4u_stats.osm.pois import pois_within_radius, resolve_poi_db
from shapely.geometry import Point, shape

logger = logging.getLogger(__name__)


_PARCEL_DETAIL_BLOCKS = (
    "basic", "cadastral", "address", "addresses", "risk", "subsidence", "terrain",
    "population", "buildings", "economics", "demographics", "land_cover",
    "land_use", "valuation", "valuation_history", "coastal_erosion",
    "cultural_heritage", "solar", "poi", "nightlights", "opendata", "pvp",
)


def _detail_block(
    data: Any = None,
    *,
    source: Optional[str] = None,
    match_method: Optional[str] = None,
    available: Optional[bool] = None,
) -> Dict[str, Any]:
    """Return the common block envelope described by the parcel reference."""
    is_available = bool(data) if available is None else bool(available)
    return {
        "available": is_available,
        "data": data if is_available else None,
        "source": source,
        "data_vintage": None,
        "updated_at": None,
        "match_method": match_method,
        "match_distance_m": None,
        "coverage_status": "full" if is_available else "not_available",
        "confidence": None,
        "license": None,
    }


def _call_with_hard_timeout(func, timeout, *args, **kwargs):
    """Run ``func`` in a worker thread with a wall-clock timeout that a
    blocking C call can't defeat by ignoring its own timeout parameter.

    Observed on this host: psycopg2 connection setup can hang well past
    ``connect_timeout``/``statement_timeout`` (even a raw ``socket.connect``
    with an explicit Python-level timeout can hang) once ``pydantic_settings``
    has been imported in the process — environment-specific, cause unclear,
    but it made every Postgres-backed enrichment path capable of hanging a
    request indefinitely. Raises ``TimeoutError`` if ``func`` hasn't returned
    in time; the worker thread (daemon) is then abandoned rather than joined,
    since Python cannot forcibly cancel a blocking C call.
    """
    box: list = []

    def _target():
        try:
            box.append(("ok", func(*args, **kwargs)))
        except Exception as exc:  # noqa: BLE001 - re-raised on the caller's thread
            box.append(("error", exc))

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise TimeoutError(f"{getattr(func, '__qualname__', func)} did not return within {timeout}s")
    status, payload = box[0]
    if status == "error":
        raise payload
    return payload


def _sister_db_path() -> Optional[Path]:
    """Resolve the read-only SQLite database populated by the sister app."""
    configured = os.getenv("SISTER_DB_PATH")
    candidates = []
    if configured:
        candidates.append(Path(configured).expanduser())
    # This is the shared-volume path used by the sister service in production.
    candidates.append(Path("/data/aecs4u.it/sister/data/sister.sqlite"))
    # Keep local development usable when both repositories are checked out.
    candidates.append(Path(__file__).resolve().parents[2] / "sister" / "data" / "sister.sqlite")
    for candidate in candidates:
        try:
            if candidate.is_file() and candidate.stat().st_size > 0:
                return candidate
        except OSError:
            continue
    return None


class _SisterBuildingSource:
    """Read building classifications cached by the sister/SISTER service.

    SISTER has had two compatible schemas in the wild: older databases store
    the cadastral coordinates directly on ``building_identifiers`` while the
    current models normalize them through ``cadastral_locations``.  The query
    below detects the schema once per connection and supports both layouts.
    No SISTER request or write is triggered by a map click.
    """

    def __init__(self, path: Path):
        self.path = path

    def available(self) -> bool:
        try:
            return self.path.is_file() and self.path.stat().st_size > 0
        except OSError:
            return False

    @staticmethod
    def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
        return {
            row[1]
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }

    @staticmethod
    def _parcel_values(value: str, *, sheet: bool = False) -> list[str]:
        normalized = str(value or "").strip()
        try:
            comparable = str(int(normalized))
        except ValueError:
            comparable = normalized.lstrip("0") or "0"
        values = [normalized, comparable]
        # AdE INSPIRE references in the regional extracts use a six-digit
        # sheet token such as 001800 for sheet 18 (two trailing zeroes are a
        # fixed precision marker). Sister stores the canonical sheet as 18.
        if sheet and comparable.isdigit() and int(comparable) >= 100 and int(comparable) % 100 == 0:
            values.append(str(int(comparable) // 100))
        return list(dict.fromkeys(values))

    def buildings_for_parcel(
        self,
        national_reference: str,
        cadastral_code: str,
        municipality: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Return SISTER building categories for one cadastral parcel."""
        reference = str(national_reference or "").strip()
        prefix, separator, suffix = reference.partition("_")
        sheet_text, dot, parcel_text = suffix.partition(".")
        parcel_text = parcel_text.split("/", 1)[0]
        if not separator or not dot or not sheet_text or not parcel_text:
            return {"buildings": [], "source": "SISTER SQLite", "available": False}

        sheet_values = self._parcel_values(sheet_text, sheet=True)
        parcel_values = self._parcel_values(parcel_text)
        province = str((municipality or {}).get("province") or "").strip()
        municipality_name = str((municipality or {}).get("name") or "").strip()
        if not province or not municipality_name:
            return {"buildings": [], "source": "SISTER SQLite", "available": False}

        sheet_placeholders = ",".join("?" for _ in sheet_values)
        parcel_placeholders = ",".join("?" for _ in parcel_values)

        def parcel_filter(field: str) -> str:
            return (
                f"(trim({field}) IN ({sheet_placeholders}) "
                f"OR ltrim(trim({field}), '0') IN ({sheet_placeholders}))"
            )

        def parcel_params(values: list[str]) -> tuple[str, ...]:
            return (*values, *values)

        try:
            connection = sqlite3.connect(
                f"file:{self.path}?mode=ro", uri=True, timeout=1.5
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            try:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                classifications = "building_classifications"
                results: list[dict] = []

                # Structured property rows are the direct equivalent of the
                # sister query used by its result page (category is the
                # cadastral building type).
                if {"visura_properties", "cadastral_locations"}.issubset(tables):
                    rows = connection.execute(
                        f"""
                        SELECT vp.property_type, vp.category, vp.cadastral_class,
                               vp.consistency, vp.income, vp.census_zone,
                               vp.address, cl.sheet, cl.parcel, cl.subunit
                        FROM visura_properties vp
                        JOIN cadastral_locations cl ON cl.id = vp.location_id
                        WHERE lower(trim(cl.province)) = lower(trim(?))
                          AND lower(trim(cl.municipality)) = lower(trim(?))
                          AND {parcel_filter('cl.sheet')}
                          AND (trim(cl.parcel) IN ({parcel_placeholders})
                               OR ltrim(trim(cl.parcel), '0') IN ({parcel_placeholders}))
                          AND (lower(trim(coalesce(vp.property_type, ''))) = 'building'
                               OR vp.category IS NOT NULL)
                        ORDER BY vp.id
                        """,
                        (province, municipality_name, *parcel_params(sheet_values), *parcel_params(parcel_values)),
                    ).fetchall()
                    results.extend(dict(row) for row in rows)

                # Building identifiers/classifications are the sister XML
                # model query. Support both its denormalized legacy schema and
                # the normalized location_id schema.
                if {"building_identifiers", classifications}.issubset(tables):
                    identifier_columns = self._columns(connection, "building_identifiers")
                    classification_columns = self._columns(connection, classifications)
                    if {"province", "municipality", "sheet", "parcel"}.issubset(identifier_columns):
                        location_filter = (
                            "lower(trim(bi.province)) = lower(trim(?)) "
                            "AND lower(trim(bi.municipality)) = lower(trim(?)) "
                            f"AND {parcel_filter('bi.sheet')} "
                            f"AND (trim(bi.parcel) IN ({parcel_placeholders}) "
                            f"OR ltrim(trim(bi.parcel), '0') IN ({parcel_placeholders}))"
                        )
                        location_params = (province, municipality_name, *parcel_params(sheet_values), *parcel_params(parcel_values))
                    elif "location_id" in identifier_columns and "cadastral_locations" in tables:
                        location_filter = (
                            "lower(trim(cl.province)) = lower(trim(?)) "
                            "AND lower(trim(cl.municipality)) = lower(trim(?)) "
                            f"AND {parcel_filter('cl.sheet')} "
                            f"AND (trim(cl.parcel) IN ({parcel_placeholders}) "
                            f"OR ltrim(trim(cl.parcel), '0') IN ({parcel_placeholders}))"
                        )
                        location_params = (province, municipality_name, *parcel_params(sheet_values), *parcel_params(parcel_values))
                    else:
                        location_filter = "0"
                        location_params = ()

                    if location_filter != "0":
                        joins = (
                            "LEFT JOIN cadastral_locations cl ON cl.id = bi.location_id"
                            if "location_id" in identifier_columns
                            else ""
                        )
                        classification_join = []
                        for parent in ("building_unit_id", "current_state_id", "history_document_id"):
                            if parent in identifier_columns and parent in classification_columns:
                                classification_join.append(f"bc.{parent} = bi.{parent}")
                        if classification_join:
                            classification_on = " OR ".join(classification_join)
                            sheet_column = "cl.sheet" if "location_id" in identifier_columns else "bi.sheet"
                            parcel_column = "cl.parcel" if "location_id" in identifier_columns else "bi.parcel"
                            subunit_column = "bi.subunit" if "subunit" in identifier_columns else "NULL"
                            rows = connection.execute(
                                f"""
                                SELECT 'building' AS property_type, bc.category,
                                       bc.cadastral_class, bc.consistency_value,
                                       bc.cadastral_income, bc.census_zone,
                                       NULL AS address,
                                       {sheet_column} AS sheet,
                                       {parcel_column} AS parcel,
                                       {subunit_column} AS subunit
                                FROM building_identifiers bi
                                {joins}
                                JOIN building_classifications bc
                                  ON ({classification_on})
                                WHERE {location_filter}
                                ORDER BY bi.id
                                """,
                                location_params,
                            ).fetchall()
                            results.extend(dict(row) for row in rows)
            finally:
                connection.close()
        except (OSError, sqlite3.Error) as exc:
            logger.warning("SISTER building lookup failed for %s: %s", reference, exc)
            return {"buildings": [], "source": "SISTER SQLite", "available": False}

        buildings = []
        seen = set()
        for row in results:
            category = row.get("category")
            key = (
                category,
                row.get("cadastral_class"),
                row.get("subunit"),
            )
            if key in seen:
                continue
            seen.add(key)
            buildings.append(
                {
                    "property_type": "building",
                    "building_type": category,
                    "category": category,
                    "cadastral_class": row.get("cadastral_class"),
                    "consistency": row.get("consistency") or row.get("consistency_value"),
                    "cadastral_income": row.get("income") or row.get("cadastral_income"),
                    "census_zone": row.get("census_zone"),
                    "address": row.get("address"),
                    "sheet": row.get("sheet"),
                    "parcel": row.get("parcel"),
                    "subunit": row.get("subunit"),
                }
            )
        return {
            "buildings": buildings,
            "count": len(buildings),
            "available": bool(buildings),
            "source": "SISTER SQLite building_classifications",
        }


class _SisterDocumentSource:
    """Read cadastral visura documents cached by the sister application.

    The sister database keeps the original PDF/P7M and the parsed XML content
    in ``visura_documents``/``document_metadata``.  The normalized property
    tables are optional and are empty in some historical caches, so matching
    also inspects the cadastral identifiers in the stored XML.  Reads are
    strictly exact on municipality, sheet and parcel; a municipality-only
    document must never be shown for a selected parcel.
    """

    _DOCUMENT_TYPES = {
        "elenco_immobili",
        "visura_fabbricati",
        "visura_terreni",
        "visura_soggetto",
    }
    _IDENTIFIER_TAGS = {
        "DatiRichiesta",
        "IdentificativoDefinitivo",
        "IdentificativoDefinitivoRiferimento",
        "IdentificativoCorrelato",
    }

    def __init__(self, path: Path):
        self.path = path

    def available(self) -> bool:
        try:
            return self.path.is_file() and self.path.stat().st_size > 0
        except OSError:
            return False

    @staticmethod
    def _local_name(tag: str) -> str:
        return str(tag or "").rsplit("}", 1)[-1]

    @staticmethod
    def _normalized_values(value: Any, *, sheet: bool = False) -> set[str]:
        raw = str(value or "").strip()
        candidates = [raw]
        # Some SISTER imports retain the cadastral-section prefix in the
        # sheet field (for example ``RA/103``). The parcel reference uses the
        # numeric component, so compare both the original token and its
        # suffix. This also handles the occasional ``section-sheet`` form.
        for separator in ("/", "-"):
            if separator in raw:
                candidates.append(raw.rsplit(separator, 1)[-1].strip())
        values = [
            item
            for candidate in candidates
            for item in _SisterBuildingSource._parcel_values(candidate, sheet=sheet)
        ]
        return {str(item).strip().casefold() for item in values if str(item).strip()}

    @staticmethod
    def _text(element: Optional[ElementTree.Element]) -> Optional[str]:
        if element is None:
            return None
        value = " ".join(part.strip() for part in element.itertext() if part.strip())
        return value or None

    @classmethod
    def _xml_summary(cls, content: Any) -> Optional[dict]:
        if not isinstance(content, str) or not content.strip():
            return None
        try:
            root = ElementTree.fromstring(content)
        except ElementTree.ParseError:
            return None

        requests = [
            element.attrib
            for element in root.iter()
            if cls._local_name(element.tag) == "DatiRichiesta"
        ]
        identities = []
        for element in root.iter():
            if cls._local_name(element.tag) not in cls._IDENTIFIER_TAGS:
                continue
            attrs = element.attrib
            if not attrs.get("Foglio") or not attrs.get("ParticellaNum"):
                continue
            row = {
                key: attrs.get(key)
                for key in (
                    "Provincia", "CodiceComune", "Comune", "SezCensuaria",
                    "Foglio", "ParticellaNum", "Subalterno", "SezUrbana",
                )
                if attrs.get(key) not in (None, "")
            }
            for request in requests:
                for key in ("Provincia", "CodiceComune", "Comune"):
                    if key not in row and request.get(key):
                        row[key] = request[key]
            identities.append(row)

        properties = []
        for element in root.iter():
            tag = cls._local_name(element.tag)
            if tag not in {"ImmobileFabbricatiS", "ImmobileTerreniS"}:
                continue
            row = {}
            for child in element.iter():
                child_tag = cls._local_name(child.tag)
                if child_tag in {"IdentificativoDefinitivo", "IdentificativoDefinitivoRiferimento"}:
                    for key in ("Foglio", "ParticellaNum", "Subalterno", "Provincia", "Comune"):
                        if child.attrib.get(key):
                            row[key.casefold()] = child.attrib[key]
                elif child_tag in {"DatiClassamentoF", "ClassamentoT"}:
                    for key, value in child.attrib.items():
                        row[key.casefold()] = value
                elif child_tag in {"IndirizzoImm", "Nominativo"}:
                    value = cls._text(child)
                    if value:
                        row["indirizzo" if child_tag == "IndirizzoImm" else "nominativo"] = value
            if row:
                properties.append(row)

        owners = []
        for element in root.iter():
            if cls._local_name(element.tag) != "Intestato":
                continue
            owner = {}
            for child in element:
                tag = cls._local_name(child.tag)
                if tag == "Nominativo":
                    owner["nominativo"] = cls._text(child)
                elif tag == "CF":
                    owner["codice_fiscale"] = cls._text(child)
                elif tag == "DirittiReali":
                    owner["diritti_reali"] = dict(child.attrib)
            if owner:
                owners.append(owner)

        summary = {}
        if identities:
            summary["identificativi"] = identities[:24]
        if properties:
            summary["immobili"] = properties[:24]
        if owners:
            summary["intestatari"] = owners[:24]
        return summary or None

    @classmethod
    def _content_matches(
        cls,
        content: Any,
        code: str,
        sheets: set[str],
        parcels: set[str],
        province_values: set[str],
        municipality_values: set[str],
    ) -> bool:
        if not isinstance(content, str) or not content.strip():
            return False
        try:
            root = ElementTree.fromstring(content)
        except ElementTree.ParseError:
            return False
        requests = [
            element.attrib
            for element in root.iter()
            if cls._local_name(element.tag) == "DatiRichiesta"
        ]
        for element in root.iter():
            if cls._local_name(element.tag) not in cls._IDENTIFIER_TAGS:
                continue
            attrs = element.attrib
            sheet = cls._normalized_values(attrs.get("Foglio"), sheet=True)
            parcel = cls._normalized_values(attrs.get("ParticellaNum"))
            if not sheet.intersection(sheets) or not parcel.intersection(parcels):
                continue
            request = next((item for item in requests if item.get("CodiceComune") == code), {})
            actual_code = str(attrs.get("CodiceComune") or request.get("CodiceComune") or "").strip().upper()
            if actual_code and actual_code != code:
                continue
            actual_province = str(attrs.get("Provincia") or request.get("Provincia") or "").strip().casefold()
            actual_municipality = str(attrs.get("Comune") or request.get("Comune") or "").strip().casefold()
            if actual_province and province_values and actual_province not in province_values:
                continue
            if actual_municipality and municipality_values and actual_municipality not in municipality_values:
                continue
            return True
        return False

    @classmethod
    def _record(cls, row: sqlite3.Row, match_method: str) -> dict:
        content = row["content"]
        file_path = str(row["file_path"] or "")
        file_present = bool(file_path and Path(file_path).is_file())
        if not file_present and file_path.startswith("/mnt/mobile/"):
            file_present = Path(file_path.removeprefix("/mnt/mobile")).is_file()
        result = {
            "document_id": row["id"],
            "document_type": row["document_type"],
            "filename": row["filename"],
            "file_format": row["file_format"],
            "file_size": row["file_size"],
            "created_at": row["created_at"],
            "document_available": file_present,
            "match_method": match_method,
        }
        for key in ("view_subtype", "protocol", "year", "title", "reference_date", "registry_view_type", "service_type", "generation_date"):
            if row[key] not in (None, ""):
                result[key] = row[key]
        summary = cls._xml_summary(content)
        if summary:
            result["data"] = summary
        return {
            "endpoint": row["document_type"],
            "timestamp": row["created_at"],
            "result": result,
        }

    def documents_for_parcel(
        self,
        national_reference: str,
        cadastral_code: str,
        municipality: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        reference = str(national_reference or "").strip()
        code, sheets, parcels = _parcel_reference_parts(reference)
        if code != str(cadastral_code or code).strip().upper() or not sheets or not parcels:
            return {"records": [], "count": 0, "available": False, "source": "SISTER SQLite documents"}
        municipality = municipality or {}
        municipality_values = {
            str(value).strip().casefold()
            for value in (municipality.get("name"), municipality.get("municipality"))
            if value not in (None, "")
        }
        province_values = {
            str(value).strip().casefold()
            for value in (municipality.get("province"), municipality.get("province_sigla"))
            if value not in (None, "")
        }
        sheets_set = {value.casefold() for value in sheets}
        parcels_set = {value.casefold() for value in parcels}

        try:
            connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=1.5)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            try:
                tables = {
                    row[0]
                    for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                }
                if "visura_documents" not in tables:
                    return {"records": [], "count": 0, "available": False, "source": "SISTER SQLite documents"}
                has_metadata = "document_metadata" in tables
                has_locations = "cadastral_locations" in tables
                metadata_join = "LEFT JOIN document_metadata m ON m.id = d.id" if has_metadata else ""
                location_join = "LEFT JOIN cadastral_locations l ON l.id = m.location_id" if has_metadata and has_locations else ""
                select_metadata = ", m.*" if has_metadata else ""
                select_location = ", l.province AS location_province, l.municipality AS location_municipality, l.sheet AS location_sheet, l.parcel AS location_parcel" if has_metadata and has_locations else ""
                rows = connection.execute(
                    f"""
                    SELECT d.*{select_metadata}{select_location}
                    FROM visura_documents d
                    {metadata_join}
                    {location_join}
                    WHERE lower(trim(d.document_type)) IN ({','.join('?' for _ in self._DOCUMENT_TYPES)})
                    ORDER BY d.id
                    """,
                    tuple(sorted(self._DOCUMENT_TYPES)),
                ).fetchall()
            finally:
                connection.close()
        except (OSError, sqlite3.Error) as exc:
            logger.warning("SISTER document lookup failed for %s: %s", reference, exc)
            return {"records": [], "count": 0, "available": False, "source": "SISTER SQLite documents"}

        records = []
        seen = set()
        for row in rows:
            location_match = (
                bool(self._normalized_values(row["location_sheet"], sheet=True).intersection(sheets_set))
                and bool(self._normalized_values(row["location_parcel"]).intersection(parcels_set))
                and (
                    not municipality_values
                    or str(row["location_municipality"] or "").strip().casefold() in municipality_values
                )
                and (
                    not province_values
                    or str(row["location_province"] or "").strip().casefold() in province_values
                )
            ) if "location_sheet" in row.keys() else False
            content_match = self._content_matches(
                row["content"] if "content" in row.keys() else None,
                code,
                sheets_set,
                parcels_set,
                province_values,
                municipality_values,
            )
            if not location_match and not content_match:
                continue
            document_id = row["id"]
            if document_id in seen:
                continue
            seen.add(document_id)
            records.append(self._record(row, "cadastral_location" if location_match else "document_xml"))
        return {
            "records": records,
            "count": len(records),
            "available": bool(records),
            "source": "SISTER SQLite documents",
            "match_method": "municipality+sheet+parcel",
            "cadastral_code": code,
        }


_sister_building_source: Optional[_SisterBuildingSource] = None
_sister_building_source_path: Optional[Path] = None
_sister_building_source_loaded = False
_sister_document_source: Optional[_SisterDocumentSource] = None
_sister_document_source_path: Optional[Path] = None
_sister_document_source_loaded = False


def _get_sister_building_source() -> Optional[_SisterBuildingSource]:
    global _sister_building_source, _sister_building_source_path, _sister_building_source_loaded
    path = _sister_db_path()
    if _sister_building_source_loaded and path == _sister_building_source_path:
        return _sister_building_source
    _sister_building_source_path = path
    _sister_building_source = _SisterBuildingSource(path) if path else None
    _sister_building_source_loaded = True
    return _sister_building_source


def sister_buildings_available() -> bool:
    source = _get_sister_building_source()
    return source is not None and source.available()


def _get_sister_document_source() -> Optional[_SisterDocumentSource]:
    global _sister_document_source, _sister_document_source_path, _sister_document_source_loaded
    path = _sister_db_path()
    if _sister_document_source_loaded and path == _sister_document_source_path:
        return _sister_document_source
    _sister_document_source_path = path
    _sister_document_source = _SisterDocumentSource(path) if path else None
    _sister_document_source_loaded = True
    return _sister_document_source


def sister_documents_available() -> bool:
    source = _get_sister_document_source()
    return source is not None and source.available()


class _PostgresPoiSource:
    """Read-only adapter over the canonical aecs4u-stats PostGIS ``facts.poi``
    table — the migrated replacement for the local OSM POI SQLite store.

    Mirrors ``PostgresCadastralBoundarySource`` in datashader_service.py:
    same DSN env-var chain, same lazy connection pool, same "unavailable is
    not an error" degrade-to-fallback behaviour.
    """

    def __init__(self, dsn: str, max_connections: int = 4):
        dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
        if "connect_timeout=" not in dsn:
            separator = "&" if "?" in dsn else "?"
            dsn = f"{dsn}{separator}connect_timeout=3"
        self.dsn = dsn
        self.max_connections = max_connections
        self._pool = None
        self._pool_lock = threading.Lock()
        self._retry_at = 0.0

    @classmethod
    def from_environment(cls):
        if not _postgres_stats_enabled():
            return None
        dsn = (
            os.getenv("AECS4U_STATS_POSTGRES_DSN")
            or os.getenv("AECS4U_STATS_DATABASE_URL")
            or os.getenv("AECS4U_STATS_SPATIAL_DATABASE_URL")
        )
        if not dsn or not dsn.startswith(("postgres://", "postgresql://", "postgresql+")):
            return None
        try:
            return cls(dsn)
        except Exception as exc:
            logger.warning("Postgres POI source unavailable: %s", exc)
            return None

    def available(self) -> bool:
        return time.monotonic() >= self._retry_at

    def _get_pool(self):
        if self._pool is None:
            with self._pool_lock:
                if self._pool is None:
                    import psycopg2.pool

                    self._pool = _call_with_hard_timeout(
                        psycopg2.pool.ThreadedConnectionPool, 5, 1, self.max_connections, self.dsn
                    )
        return self._pool

    @contextmanager
    def _connection(self):
        pool = self._get_pool()
        connection = pool.getconn()
        try:
            # facts.poi's GiST index triggers a PostGIS JIT bitcode-version
            # crash on this server unless JIT is disabled per-connection.
            with connection.cursor() as cursor:
                cursor.execute("SET jit = off")
            yield connection
        except Exception:
            connection.rollback()
            raise
        finally:
            pool.putconn(connection, close=bool(getattr(connection, "closed", 0)))

    def pois_near(
        self, lat: float, lng: float, radius_km: float = 1.0, categories: Optional[List[str]] = None
    ) -> Dict[str, List[dict]]:
        sql = """
            SELECT c.code, p.name, ST_Y(p.geom), ST_X(p.geom),
                   ST_Distance(
                       p.geom::geography,
                       ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography
                   ) / 1000.0 AS distance_km
            FROM facts.poi p
            JOIN facts.poi_category c ON c.id = p.category_id
            WHERE ST_DWithin(
                p.geom::geography,
                ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
                %s
            )
        """
        params: list = [lng, lat, lng, lat, radius_km * 1000.0]
        if categories:
            placeholders = ",".join(["%s"] * len(categories))
            sql += f" AND c.code IN ({placeholders})"
            params.extend(categories)
        sql += " ORDER BY distance_km"

        grouped: Dict[str, List[dict]] = {}
        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                for code, name, poi_lat, poi_lng, distance_km in cursor.fetchall():
                    grouped.setdefault(code, []).append(
                        {
                            "lat": poi_lat,
                            "lng": poi_lng,
                            "name": name,
                            "distance_km": round(float(distance_km), 3),
                        }
                    )
        return grouped


def _postgres_stats_enabled() -> bool:
    """Return whether the configured aecs4u-stats Postgres source is enabled.

    Deliberately opt-in, DSN-presence alone is NOT enough: a blocking
    psycopg2 call has been reproduced on this host freezing the *entire*
    process — even the unrelated ``/health`` endpoint stops responding while
    it's stuck — and neither connect_timeout/statement_timeout, a
    thread-join timeout, nor ``asyncio.wait_for`` can recover from it
    (consistent with the call never releasing the GIL). Defaulting to
    "DSN configured -> enabled" silently reintroduces that freeze on every
    request the moment ``AECS4U_STATS_POSTGRES_DSN`` is set, e.g. via
    ``.env``. Require an explicit ``AECS4U_STATS_POSTGRES_ENABLE=1`` until
    that's diagnosed/fixed, or Postgres access is moved behind real process
    isolation (a subprocess that can be SIGKILLed).
    """
    return os.getenv("AECS4U_STATS_POSTGRES_ENABLE", "").strip().lower() in ("1", "true", "yes", "on")


def _postgres_stats_dsn() -> Optional[str]:
    """Return the explicitly configured aecs4u-stats PostgreSQL DSN.

    Do not fall back to the application's main ``DATABASE_URL`` here: that
    database contains land-registry application data, not the canonical
    aecs4u-stats datasets.
    """
    if not _postgres_stats_enabled():
        return None
    dsn = os.getenv("AECS4U_STATS_POSTGRES_DSN")
    if not dsn or not dsn.startswith(("postgres://", "postgresql://", "postgresql+")):
        return None
    return dsn


def _postgres_scalar(value: Any) -> Any:
    """Convert psycopg2 values into JSON-safe API values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "item"):
        try:
            return value.item()
        except (AttributeError, ValueError):
            pass
    if hasattr(value, "isoformat"):
        return value.isoformat()
    try:
        from decimal import Decimal

        if isinstance(value, Decimal):
            return float(value)
    except ImportError:  # pragma: no cover
        pass
    return str(value)


def _census_ratios(properties: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """Derive the small, stable indicators used by the parcel panel."""
    def ratio(numerator: Any, denominator: Any) -> Optional[float]:
        try:
            denominator = float(denominator)
            if denominator == 0:
                return None
            return round(float(numerator) / denominator, 4)
        except (TypeError, ValueError):
            return None

    working_age = sum(float(properties.get(name) or 0) for name in (
        "p17", "p18", "p19", "p20", "p21", "p22", "p23", "p24", "p25", "p26",
    ))
    return {
        "education_tertiary_rate": ratio(properties.get("p90"), properties.get("p83")),
        "employment_rate_working_age": ratio(properties.get("p101"), working_age),
        "foreign_resident_share": ratio(properties.get("st1"), properties.get("p1")),
        "vacancy_rate": ratio(properties.get("a3"), properties.get("a8")),
        "avg_household_size": ratio(properties.get("p1"), properties.get("pf1")),
    }


class _PostgresStatsSourcePlaceholder(_PostgresPoiSource):
    """Read-only parcel context adapter over the local aecs4u-stats PostGIS DB.

    The current database has the context ingredients but its canonical parcel
    spine is not populated.  This adapter therefore reports both the context
    rows and ``parcel_spine_available`` instead of manufacturing a parcel join.
    """

    @classmethod
    def from_environment(cls):
        dsn = _postgres_stats_dsn()
        if not dsn:
            return None


def _asyncpg_sql(sql: str) -> str:
    """Translate the adapter's DB-API placeholders to asyncpg placeholders."""
    index = 0

    def replace(_match):
        nonlocal index
        index += 1
        return f"${index}"

    return re.sub(r"%s", replace, sql)


class _AsyncPostgresSource:
    """Async PostgreSQL adapter used by FastAPI enrichment endpoints.

    The old synchronous adapter uses psycopg2, whose native client has
    crashed this process on connection setup.  Keep it available for legacy
    callers/tests, but all request-path PostgreSQL access goes through this
    asyncpg pool instead.
    """

    def __init__(self, dsn: str, *, max_connections: int = 4):
        self.dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
        self.max_connections = max_connections
        self._pool = None
        self._retry_at = 0.0

    async def _get_pool(self):
        if self._pool is None:
            import asyncpg

            self._pool = await asyncpg.create_pool(
                self.dsn,
                min_size=1,
                max_size=self.max_connections,
                command_timeout=20,
                statement_cache_size=0,
            )
        return self._pool

    async def close(self):
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def _fetch(self, sql: str, params: tuple = ()):
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            return await connection.fetch(_asyncpg_sql(sql), *params)

    async def _fetchrow(self, sql: str, params: tuple = ()):
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            return await connection.fetchrow(_asyncpg_sql(sql), *params)

    async def municipality_by_cadastral_code(self, cadastral_code: str) -> Optional[Dict[str, Any]]:
        row = await self._fetchrow(
            """
            SELECT gi_cad.code AS cadastral_code,
                   gi_istat.code AS istat_code,
                   u.canonical_name,
                   province.canonical_name AS province,
                   province_nuts.code AS nuts3,
                   region.canonical_name AS region,
                   mp.observation_count,
                   mp.tax_fact_count,
                   mp.total_imponibile
            FROM geo.geo_identifier gi_cad
            JOIN geo.geo_unit u ON u.id = gi_cad.geo_unit_id
            LEFT JOIN geo.geo_identifier gi_istat
              ON gi_istat.geo_unit_id = u.id
             AND gi_istat.scheme = 'ISTAT_COMUNE'
            LEFT JOIN geo.geo_relation province_relation
              ON province_relation.child_id = u.id
             AND province_relation.relation_type = 'contains'
            LEFT JOIN geo.geo_unit province
              ON province.id = province_relation.parent_id
             AND province.unit_type = 'province'
            LEFT JOIN geo.geo_identifier province_nuts
              ON province_nuts.geo_unit_id = province.id
             AND province_nuts.scheme = 'NUTS'
            LEFT JOIN geo.geo_relation region_relation
              ON region_relation.child_id = province.id
             AND region_relation.relation_type = 'contains'
            LEFT JOIN geo.geo_unit region
              ON region.id = region_relation.parent_id
             AND region.unit_type = 'region'
            LEFT JOIN serving.municipality_profile mp
              ON mp.geo_unit_id = u.id
            WHERE gi_cad.scheme = 'CATASTALE_COMUNE'
              AND UPPER(gi_cad.code) = UPPER(%s)
            LIMIT 1
            """,
            (cadastral_code.strip().upper(),),
        )
        if row is None:
            return None
        values = {key: _postgres_scalar(row[key]) for key in row.keys()}
        istat_code = values.get("istat_code")
        try:
            procom = int(istat_code) if istat_code is not None else None
        except (TypeError, ValueError):
            procom = None
        return {
            "cadastral_code": values.get("cadastral_code"),
            "istat_code": istat_code,
            "official_name": values.get("canonical_name"),
            "procom": procom,
            "name": values.get("canonical_name"),
            "province": values.get("province"),
            "province_sigla": None,
            "region": values.get("region"),
            "nuts3_2021": values.get("nuts3"),
            "nuts3": values.get("nuts3"),
            "is_provincial_capital": False,
            "latitude": None,
            "longitude": None,
            "postal_code": None,
            "population": None,
            "population_history": [],
            "profile": {
                key: values.get(key)
                for key in ("observation_count", "tax_fact_count", "total_imponibile")
            },
            "source": "aecs4u-stats PostgreSQL via asyncpg",
        }

    async def omi_quotes_by_cadastral_code(self, cadastral_code: str) -> Dict[str, Any]:
        rows = await self._fetch(
            """
            SELECT mz.omi_zone_key, q.period, q.typology, q.condition,
                   q.price_min, q.price_max, q.rent_min, q.rent_max
            FROM geo.geo_identifier gi_cad
            JOIN spatial.market_zone mz ON mz.municipality_id = gi_cad.geo_unit_id
            JOIN facts.market_quote_fact q ON q.market_zone_id = mz.id
            WHERE gi_cad.scheme = 'CATASTALE_COMUNE'
              AND UPPER(gi_cad.code) = UPPER(%s)
              AND q.period = (
                  SELECT MAX(q_latest.period)
                  FROM geo.geo_identifier gi_latest
                  JOIN spatial.market_zone mz_latest
                    ON mz_latest.municipality_id = gi_latest.geo_unit_id
                  JOIN facts.market_quote_fact q_latest
                    ON q_latest.market_zone_id = mz_latest.id
                  WHERE gi_latest.scheme = 'CATASTALE_COMUNE'
                    AND UPPER(gi_latest.code) = UPPER(%s)
              )
            ORDER BY mz.omi_zone_key, q.typology, q.condition
            """,
            (cadastral_code.strip().upper(), cadastral_code.strip().upper()),
        )
        quotes = []
        for row in rows:
            period = str(row["period"] or "")
            match = re.match(r"^(\d{4})-S(\d)$", period)
            quotes.append({
                "zona": row["omi_zone_key"],
                "period": period,
                "anno": int(match.group(1)) if match else None,
                "semestre": int(match.group(2)) if match else None,
                "cod_tipologia": row["typology"],
                "tipologia": row["typology"],
                "stato_conservazione": row["condition"],
                "prezzo_min": _postgres_scalar(row["price_min"]),
                "prezzo_max": _postgres_scalar(row["price_max"]),
                "locazione_min": _postgres_scalar(row["rent_min"]),
                "locazione_max": _postgres_scalar(row["rent_max"]),
            })
        return {
            "comune": cadastral_code,
            "zona": None,
            "quotes": quotes,
            "source": "Agenzia delle Entrate OMI via aecs4u-stats PostgreSQL via asyncpg",
        }

    async def income_profile_by_cadastral_code(self, cadastral_code: str) -> Optional[Dict[str, Any]]:
        rows = await self._fetch(
            """
            SELECT t.year, t.measure, t.frequency, t.amount
            FROM facts.tax_fact t
            JOIN geo.geo_identifier gi
              ON gi.geo_unit_id = t.municipality_id
             AND gi.scheme = 'CATASTALE_COMUNE'
            WHERE UPPER(gi.code) = UPPER(%s)
            ORDER BY t.year DESC, t.measure
            """,
            (cadastral_code.strip().upper(),),
        )
        if not rows:
            return None
        latest_year = rows[0]["year"]
        latest = [row for row in rows if row["year"] == latest_year]
        by_measure = {str(row["measure"]): row for row in latest}
        taxpayers = (by_measure.get("imponibile") or {}).get("frequency")
        total_income = (by_measure.get("imponibile") or {}).get("amount")
        try:
            mean_income = float(total_income) / float(taxpayers)
        except (TypeError, ValueError, ZeroDivisionError):
            mean_income = None
        distribution = []
        for row in latest:
            measure = str(row["measure"] or "")
            if not measure.startswith("bracket_"):
                continue
            try:
                pct = round(float(row["frequency"]) / float(taxpayers) * 100, 1)
            except (TypeError, ValueError, ZeroDivisionError):
                pct = None
            distribution.append({
                "bracket": measure.removeprefix("bracket_").replace("_", "–"),
                "frequency": _postgres_scalar(row["frequency"]),
                "pct": pct,
            })
        return {
            "cadastral_code": cadastral_code,
            "year": _postgres_scalar(latest_year),
            "taxpayers": _postgres_scalar(taxpayers),
            "mean_taxable_income_eur": round(mean_income, 2) if mean_income is not None else None,
            "income_distribution": distribution,
            "source": "MEF/IRPEF via aecs4u-stats PostgreSQL via asyncpg",
        }

    async def pois_near(self, lat: float, lng: float, radius_km: float = 1.0, categories=None):
        sql = """
            SELECT c.code, p.name, ST_Y(p.geom), ST_X(p.geom),
                   ST_Distance(
                       p.geom::geography,
                       ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography
                   ) / 1000.0 AS distance_km
            FROM facts.poi p
            JOIN facts.poi_category c ON c.id = p.category_id
            WHERE ST_DWithin(
                p.geom::geography,
                ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
                %s
            )
        """
        params = [lng, lat, lng, lat, radius_km * 1000.0]
        if categories:
            placeholders = ",".join(["%s"] * len(categories))
            sql += f" AND c.code IN ({placeholders})"
            params.extend(categories)
        sql += " ORDER BY distance_km"
        rows = await self._fetch(sql, tuple(params))
        grouped: Dict[str, List[dict]] = {}
        for row in rows:
            grouped.setdefault(row["code"], []).append({
                "lat": row["st_y"],
                "lng": row["st_x"],
                "name": row["name"],
                "distance_km": round(float(row["distance_km"]), 3),
            })
        return grouped

    async def context_for_parcel(
        self,
        national_reference: str,
        cadastral_code: str,
        point: Optional[Dict[str, float]],
    ) -> Optional[Dict[str, Any]]:
        """Load optional spatial/tax context without leaving the event loop."""
        if not point:
            return None
        row = await self._fetchrow(
            """
            SELECT u.id, u.canonical_name, u.unit_type,
                   gi_istat.code AS istat_code,
                   gi_cad.code AS cadastral_code,
                   mp.observation_count, mp.tax_fact_count,
                   mp.total_imponibile, mp.market_zone_count,
                   mp.pv_n_buildings, mp.pv_pvout_pessimistic_kwh_year_total,
                   mp.pv_pvout_modern_kwh_year_total, mp.pv_pvout_per_capita_kwh,
                   mp.pv_kwp_max_total, mp.pv_high_viability_pct,
                   mp.pv_medium_viability_pct, mp.pv_low_viability_pct,
                   mp.pv_not_eligible_pct, mp.pv_observation_count
            FROM geo.geo_identifier gi_cad
            JOIN geo.geo_unit u ON u.id = gi_cad.geo_unit_id
            LEFT JOIN geo.geo_identifier gi_istat
              ON gi_istat.geo_unit_id = u.id AND gi_istat.scheme = 'ISTAT_COMUNE'
            LEFT JOIN serving.municipality_profile mp ON mp.geo_unit_id = u.id
            WHERE gi_cad.scheme = 'CATASTALE_COMUNE'
              AND UPPER(gi_cad.code) = UPPER(%s)
            LIMIT 1
            """,
            (cadastral_code,),
        )
        if row is None:
            return None
        values = {key: _postgres_scalar(row[key]) for key in row.keys()}
        municipality_id = values.pop("id")
        profile_keys = (
            "observation_count", "tax_fact_count", "total_imponibile", "market_zone_count",
            "pv_n_buildings", "pv_pvout_pessimistic_kwh_year_total",
            "pv_pvout_modern_kwh_year_total", "pv_pvout_per_capita_kwh", "pv_kwp_max_total",
            "pv_high_viability_pct", "pv_medium_viability_pct", "pv_low_viability_pct",
            "pv_not_eligible_pct", "pv_observation_count",
        )
        profile = {key: values.pop(key, None) for key in profile_keys}
        x, y = point["lng"], point["lat"]
        omi_row = await self._fetchrow(
            """
            SELECT mz.id, mz.omi_zone_key, mz.valid_from, mz.valid_to,
                   mz.source_release, mz.dataset_release_id,
                   mzs.quote_count, mzs.latest_period
            FROM spatial.market_zone mz
            LEFT JOIN serving.market_zone_snapshot mzs ON mzs.id = mz.id
            WHERE mz.municipality_id = %s
              AND ST_Covers(mz.geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
            LIMIT 1
            """,
            (municipality_id, x, y),
        )
        postal_row = await self._fetchrow(
            """
            SELECT pz.cap
            FROM spatial.postal_zone pz
            WHERE pz.municipality_id = %s
              AND ST_Covers(pz.geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
            LIMIT 1
            """,
            (municipality_id, x, y),
        )
        tax_rows = await self._fetch(
            """
            SELECT year, measure, frequency, amount
            FROM facts.tax_fact
            WHERE municipality_id = %s
            ORDER BY year DESC, measure
            """,
            (municipality_id,),
        )
        municipality = values
        municipality["profile"] = profile
        return {
            "parcel_spine_available": False,
            "parcel_spine": None,
            "parcel_spine_reference": national_reference,
            "municipality": municipality,
            "municipality_profile": profile,
            "omi": (
                {key: _postgres_scalar(omi_row[key]) for key in omi_row.keys()}
                if omi_row else None
            ),
            "postal_code": _postgres_scalar(postal_row["cap"]) if postal_row else None,
            "tax_facts": [
                {key: _postgres_scalar(row[key]) for key in row.keys()}
                for row in tax_rows
            ],
            "census": None,
            "source": "aecs4u-stats PostgreSQL via asyncpg",
        }

    async def get_read_model(self, parcel_key: str) -> Optional[Dict[str, Any]]:
        row = await self._fetchrow(
            """
            SELECT parcel_key, payload, source_fingerprint, refreshed_at
            FROM serving.parcel_enrichment_read_model
            WHERE parcel_key = %s
            """,
            (parcel_key,),
        )
        if row is None:
            return None
        payload = row["payload"]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (TypeError, ValueError, json.JSONDecodeError):
                return None
        if not isinstance(payload, dict):
            return None
        payload["read_model"] = {
            "key": row["parcel_key"],
            "source_fingerprint": row["source_fingerprint"],
            "refreshed_at": _postgres_scalar(row["refreshed_at"]),
            "cached": True,
            "database": "aecs4u-stats PostgreSQL via asyncpg",
        }
        return payload

    async def upsert_read_model(
        self, parcel_key: str, payload: Dict[str, Any], source_fingerprint: Optional[str]
    ) -> bool:
        pool = await self._get_pool()
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
        async with pool.acquire() as connection:
            await connection.execute(
                _asyncpg_sql(
                    """
                    INSERT INTO serving.parcel_enrichment_read_model
                        (parcel_key, payload, source_fingerprint, refreshed_at, updated_at)
                    VALUES (%s, %s::jsonb, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT (parcel_key) DO UPDATE SET
                        payload = EXCLUDED.payload,
                        source_fingerprint = EXCLUDED.source_fingerprint,
                        refreshed_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    """
                ),
                parcel_key,
                serialized,
                source_fingerprint,
            )
        return True


_async_stats_source: Optional[_AsyncPostgresSource] = None
_async_poi_source: Optional[_AsyncPostgresSource] = None


async def _get_async_postgres_source(*, poi: bool = False) -> Optional[_AsyncPostgresSource]:
    global _async_stats_source, _async_poi_source
    target = _async_poi_source if poi else _async_stats_source
    if target is not None:
        return target
    dsn = _postgres_stats_dsn()
    if not dsn:
        return None
    target = _AsyncPostgresSource(dsn)
    if poi:
        _async_poi_source = target
    else:
        _async_stats_source = target
    return target


async def aget_municipality_by_cadastral_code(cadastral_code: str) -> Optional[Dict[str, Any]]:
    """Async PostgreSQL-first municipality lookup with local fallback."""
    source = await _get_async_postgres_source()
    if source is not None and time.monotonic() >= source._retry_at:
        try:
            result = await asyncio.wait_for(
                source.municipality_by_cadastral_code(cadastral_code), timeout=6
            )
            if result is not None:
                return result
        except Exception:
            source._retry_at = time.monotonic() + 60
            logger.warning("Async PostgreSQL municipality lookup failed; using local fallback", exc_info=True)
    return await asyncio.to_thread(get_municipality_by_cadastral_code, cadastral_code, use_postgres=False)


async def aget_omi_quotes(comune: str, zona: Optional[str] = None) -> Dict[str, Any]:
    """Async PostgreSQL-first OMI lookup with local fallback."""
    source = await _get_async_postgres_source()
    if source is not None and time.monotonic() >= source._retry_at:
        try:
            result = await asyncio.wait_for(
                source.omi_quotes_by_cadastral_code(comune), timeout=8
            )
            if zona:
                result["quotes"] = [
                    quote for quote in result.get("quotes", [])
                    if str(quote.get("zona", "")).upper() == zona.strip().upper()
                ]
            return result
        except Exception:
            source._retry_at = time.monotonic() + 60
            logger.warning("Async PostgreSQL OMI lookup failed; using local fallback", exc_info=True)
    return await asyncio.to_thread(get_omi_quotes, comune, zona=zona, use_postgres=False)


async def aget_income_profile(cadastral_code: str, year: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Async PostgreSQL-first income lookup with local fallback."""
    source = await _get_async_postgres_source()
    if source is not None and time.monotonic() >= source._retry_at:
        try:
            result = await asyncio.wait_for(
                source.income_profile_by_cadastral_code(cadastral_code), timeout=8
            )
            if result is not None and (year is None or result.get("year") == year):
                return result
        except Exception:
            source._retry_at = time.monotonic() + 60
            logger.warning("Async PostgreSQL income lookup failed; using local fallback", exc_info=True)
    return await asyncio.to_thread(get_income_profile, cadastral_code, year=year, use_postgres=False)


async def aget_pois_near(
    lat: float, lng: float, radius_km: float = 1.0, categories: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Async PostgreSQL-first POI lookup with local fallback."""
    source = await _get_async_postgres_source(poi=True)
    if source is not None and time.monotonic() >= source._retry_at:
        try:
            grouped = await asyncio.wait_for(
                source.pois_near(lat, lng, radius_km=radius_km, categories=categories), timeout=6
            )
            return {
                "center": {"lat": lat, "lng": lng},
                "radius_km": radius_km,
                "total": sum(len(values) for values in grouped.values()),
                "categories": grouped,
                "source": "OpenStreetMap via aecs4u-stats (PostGIS asyncpg)",
            }
        except Exception:
            source._retry_at = time.monotonic() + 60
            logger.warning("Async PostgreSQL POI lookup failed; using local fallback", exc_info=True)
    grouped = await asyncio.to_thread(
        pois_within_radius, lat, lng, radius_km=radius_km, categories=categories
    )
    return {
        "center": {"lat": lat, "lng": lng},
        "radius_km": radius_km,
        "total": sum(len(values) for values in grouped.values()),
        "categories": grouped,
        "source": "OpenStreetMap via aecs4u-stats",
    }


class _PostgresStatsSource(_PostgresPoiSource):
    """Compatibility implementation for synchronous non-request callers."""

    @classmethod
    def from_environment(cls):
        dsn = _postgres_stats_dsn()
        if not dsn:
            return None
        try:
            return cls(dsn)
        except Exception as exc:
            logger.warning("Postgres stats source unavailable: %s", exc)
            return None

    def __init__(self, dsn: str, max_connections: int = 4):
        super().__init__(dsn, max_connections=max_connections)
        self._read_model_ready = False
        self._read_model_lock = threading.Lock()

    def _ensure_read_model(self) -> bool:
        """Check that the migrated parcel cache exists, without doing DDL.

        Serving schema objects are deployment artifacts.  A GET endpoint must
        not attempt to create tables, both because the application role should
        be read-only and because concurrent cold requests could race schema
        changes.  ``migration.canonical_views.ensure_serving_schema`` owns
        provisioning this table.
        """
        if self._read_model_ready:
            return True
        with self._read_model_lock:
            if self._read_model_ready:
                return True
            with self._connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT to_regclass('serving.parcel_enrichment_read_model')")
                    self._read_model_ready = bool((cursor.fetchone() or (None,))[0])
            if not self._read_model_ready:
                logger.error("Serving parcel enrichment read model is not provisioned")
            return self._read_model_ready

    def municipality_by_cadastral_code(self, cadastral_code: str) -> Optional[Dict[str, Any]]:
        """Read the municipality hierarchy and profile from aecs4u-stats."""
        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT u.id,
                           gi_cad.code AS cadastral_code,
                           gi_istat.code AS istat_code,
                           u.canonical_name,
                           province.canonical_name AS province,
                           province_istat.code AS province_istat_code,
                           province_nuts.code AS nuts3,
                           region.canonical_name AS region,
                           mp.observation_count,
                           mp.tax_fact_count,
                           mp.total_imponibile
                    FROM geo.geo_identifier gi_cad
                    JOIN geo.geo_unit u ON u.id = gi_cad.geo_unit_id
                    LEFT JOIN geo.geo_identifier gi_istat
                      ON gi_istat.geo_unit_id = u.id
                     AND gi_istat.scheme = 'ISTAT_COMUNE'
                    LEFT JOIN geo.geo_relation province_relation
                      ON province_relation.child_id = u.id
                     AND province_relation.relation_type = 'contains'
                    LEFT JOIN geo.geo_unit province
                      ON province.id = province_relation.parent_id
                     AND province.unit_type = 'province'
                    LEFT JOIN geo.geo_identifier province_istat
                      ON province_istat.geo_unit_id = province.id
                     AND province_istat.scheme = 'ISTAT_PROVINCIA'
                    LEFT JOIN geo.geo_identifier province_nuts
                      ON province_nuts.geo_unit_id = province.id
                     AND province_nuts.scheme = 'NUTS'
                    LEFT JOIN geo.geo_relation region_relation
                      ON region_relation.child_id = province.id
                     AND region_relation.relation_type = 'contains'
                    LEFT JOIN geo.geo_unit region
                      ON region.id = region_relation.parent_id
                     AND region.unit_type = 'region'
                    LEFT JOIN serving.municipality_profile mp
                      ON mp.geo_unit_id = u.id
                    WHERE gi_cad.scheme = 'CATASTALE_COMUNE'
                      AND UPPER(gi_cad.code) = UPPER(%s)
                    LIMIT 1
                    """,
                    (cadastral_code.strip().upper(),),
                )
                row = cursor.fetchone()
                if row is None:
                    return None
                columns = [column.name for column in cursor.description]

        values = {
            key: _postgres_scalar(value)
            for key, value in zip(columns, row, strict=True)
        }
        istat_code = values.get("istat_code")
        try:
            procom = int(istat_code) if istat_code is not None else None
        except (TypeError, ValueError):
            procom = None
        return {
            "cadastral_code": values.get("cadastral_code"),
            "istat_code": istat_code,
            "official_name": values.get("canonical_name"),
            "procom": procom,
            "name": values.get("canonical_name"),
            "province": values.get("province"),
            "province_sigla": None,
            "region": values.get("region"),
            "nuts3_2021": values.get("nuts3"),
            "nuts3": values.get("nuts3"),
            "is_provincial_capital": False,
            "latitude": None,
            "longitude": None,
            "postal_code": None,
            "population": None,
            "population_history": [],
            "profile": {
                key: values.get(key)
                for key in ("observation_count", "tax_fact_count", "total_imponibile")
            },
            "source": "aecs4u-stats PostgreSQL",
        }

    def omi_quotes_by_cadastral_code(self, cadastral_code: str) -> Dict[str, Any]:
        """Read the latest OMI quote period from PostgreSQL fact tables."""
        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT mz.omi_zone_key,
                           q.period,
                           q.typology,
                           q.condition,
                           q.price_min,
                           q.price_max,
                           q.rent_min,
                           q.rent_max
                    FROM geo.geo_identifier gi_cad
                    JOIN spatial.market_zone mz
                      ON mz.municipality_id = gi_cad.geo_unit_id
                    JOIN facts.market_quote_fact q
                      ON q.market_zone_id = mz.id
                    WHERE gi_cad.scheme = 'CATASTALE_COMUNE'
                      AND UPPER(gi_cad.code) = UPPER(%s)
                      AND q.period = (
                          SELECT MAX(q_latest.period)
                          FROM geo.geo_identifier gi_latest
                          JOIN spatial.market_zone mz_latest
                            ON mz_latest.municipality_id = gi_latest.geo_unit_id
                          JOIN facts.market_quote_fact q_latest
                            ON q_latest.market_zone_id = mz_latest.id
                          WHERE gi_latest.scheme = 'CATASTALE_COMUNE'
                            AND UPPER(gi_latest.code) = UPPER(%s)
                      )
                    ORDER BY mz.omi_zone_key, q.typology, q.condition
                    """,
                    (cadastral_code.strip().upper(), cadastral_code.strip().upper()),
                )
                rows = cursor.fetchall()

        quotes = []
        for zone, period, typology, condition, price_min, price_max, rent_min, rent_max in rows:
            period_text = str(period or "")
            match = re.match(r"^(\d{4})-S(\d)$", period_text)
            quotes.append({
                "zona": zone,
                "period": period_text,
                "anno": int(match.group(1)) if match else None,
                "semestre": int(match.group(2)) if match else None,
                "cod_tipologia": typology,
                "tipologia": typology,
                "stato_conservazione": condition,
                "prezzo_min": _postgres_scalar(price_min),
                "prezzo_max": _postgres_scalar(price_max),
                "locazione_min": _postgres_scalar(rent_min),
                "locazione_max": _postgres_scalar(rent_max),
            })
        return {
            "comune": cadastral_code,
            "zona": None,
            "quotes": quotes,
            "source": "Agenzia delle Entrate OMI via aecs4u-stats PostgreSQL",
        }

    def income_profile_by_cadastral_code(self, cadastral_code: str) -> Optional[Dict[str, Any]]:
        """Read the latest MEF/IRPEF facts for a municipality from PostgreSQL."""
        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT t.year, t.measure, t.frequency, t.amount
                    FROM facts.tax_fact t
                    JOIN geo.geo_identifier gi
                      ON gi.geo_unit_id = t.municipality_id
                     AND gi.scheme = 'CATASTALE_COMUNE'
                    WHERE UPPER(gi.code) = UPPER(%s)
                    ORDER BY t.year DESC, t.measure
                    """,
                    (cadastral_code.strip().upper(),),
                )
                rows = cursor.fetchall()

        if not rows:
            return None
        latest_year = rows[0][0]
        latest = [row for row in rows if row[0] == latest_year]
        by_measure = {str(row[1]): row for row in latest}
        taxpayers = (by_measure.get("imponibile") or (None, None, None, None))[2]
        total_income = (by_measure.get("imponibile") or (None, None, None, None))[3]
        try:
            mean_income = float(total_income) / float(taxpayers)
        except (TypeError, ValueError, ZeroDivisionError):
            mean_income = None

        distribution = []
        for year, measure, frequency, amount in latest:
            measure_text = str(measure or "")
            if not measure_text.startswith("bracket_"):
                continue
            try:
                pct = round(float(frequency) / float(taxpayers) * 100, 1)
            except (TypeError, ValueError, ZeroDivisionError):
                pct = None
            distribution.append({
                "bracket": measure_text.removeprefix("bracket_").replace("_", "–"),
                "frequency": _postgres_scalar(frequency),
                "pct": pct,
            })
        return {
            "cadastral_code": cadastral_code,
            "year": _postgres_scalar(latest_year),
            "taxpayers": _postgres_scalar(taxpayers),
            "mean_taxable_income_eur": round(mean_income, 2) if mean_income is not None else None,
            "income_distribution": distribution,
            "source": "MEF/IRPEF via aecs4u-stats PostgreSQL",
        }

    def get_read_model(self, parcel_key: str) -> Optional[Dict[str, Any]]:
        """Return one PostgreSQL materialized read-model payload."""
        if not self._ensure_read_model():
            return None
        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT parcel_key, payload, source_fingerprint, refreshed_at
                    FROM serving.parcel_enrichment_read_model
                    WHERE parcel_key = %s
                    """,
                    (parcel_key,),
                )
                row = cursor.fetchone()
        if not row:
            return None
        payload = row[1]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (TypeError, ValueError, json.JSONDecodeError):
                logger.warning("Invalid PostgreSQL parcel enrichment payload for %s", parcel_key)
                return None
        if not isinstance(payload, dict):
            return None
        payload["read_model"] = {
            "key": row[0],
            "source_fingerprint": row[2],
            "refreshed_at": _postgres_scalar(row[3]),
            "cached": True,
            "database": "aecs4u-stats PostgreSQL",
        }
        return payload

    def upsert_read_model(
        self,
        parcel_key: str,
        payload: Dict[str, Any],
        source_fingerprint: Optional[str],
    ) -> bool:
        """Atomically replace one PostgreSQL parcel read-model row."""
        if not self._ensure_read_model():
            return False
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO serving.parcel_enrichment_read_model
                        (parcel_key, payload, source_fingerprint, refreshed_at, updated_at)
                    VALUES (%s, %s::jsonb, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT (parcel_key) DO UPDATE SET
                        payload = EXCLUDED.payload,
                        source_fingerprint = EXCLUDED.source_fingerprint,
                        refreshed_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (parcel_key, serialized, source_fingerprint),
                )
            connection.commit()
        return True

    def context_for_parcel(
        self,
        national_reference: str,
        cadastral_code: str,
        point: Optional[Dict[str, float]],
    ) -> Optional[Dict[str, Any]]:
        """Load municipality, census, OMI, postal, and tax context."""
        if not point:
            return None
        try:
            with self._connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SET statement_timeout = '20s'")
                    cursor.execute(
                        """
                        SELECT u.id, u.canonical_name, u.unit_type,
                               gi_istat.code AS istat_code,
                               gi_cad.code AS cadastral_code,
                               mp.observation_count, mp.tax_fact_count,
                               mp.total_imponibile, mp.market_zone_count,
                               mp.pv_n_buildings, mp.pv_pvout_pessimistic_kwh_year_total,
                               mp.pv_pvout_modern_kwh_year_total, mp.pv_pvout_per_capita_kwh,
                               mp.pv_kwp_max_total, mp.pv_high_viability_pct,
                               mp.pv_medium_viability_pct, mp.pv_low_viability_pct,
                               mp.pv_not_eligible_pct, mp.pv_observation_count
                        FROM geo.geo_identifier gi_cad
                        JOIN geo.geo_unit u ON u.id = gi_cad.geo_unit_id
                        LEFT JOIN geo.geo_identifier gi_istat
                          ON gi_istat.geo_unit_id = u.id
                         AND gi_istat.scheme = 'ISTAT_COMUNE'
                        LEFT JOIN serving.municipality_profile mp ON mp.geo_unit_id = u.id
                        WHERE gi_cad.scheme = 'CATASTALE_COMUNE'
                          AND UPPER(gi_cad.code) = UPPER(%s)
                        LIMIT 1
                        """,
                        (cadastral_code,),
                    )
                    municipality_row = cursor.fetchone()
                    if municipality_row is None:
                        return None
                    municipality_columns = [column.name for column in cursor.description]
                    municipality_values = dict(zip(municipality_columns, municipality_row, strict=True))

                    municipality_id = municipality_values["id"]
                    point_sql = "ST_SetSRID(ST_MakePoint(%s, %s), 4326)"

                    # The cadastral parcel spine is optional and, on older
                    # deployments, has millions of rows without an index on
                    # canonical_reference. Do not scan it during enrichment;
                    # the selected FGB feature already supplies the parcel
                    # identity and the remaining PostgreSQL joins are indexed.
                    parcel_row = None
                    parcel_columns = ()

                    cursor.execute(
                        f"""
                        SELECT mz.id, mz.omi_zone_key, mz.valid_from, mz.valid_to,
                               mz.source_release, mz.dataset_release_id,
                               mzs.quote_count, mzs.latest_period
                        FROM spatial.market_zone mz
                        LEFT JOIN serving.market_zone_snapshot mzs ON mzs.id = mz.id
                        WHERE mz.municipality_id = %s
                          AND ST_Covers(mz.geom, {point_sql})
                        LIMIT 1
                        """,
                        (municipality_id, point["lng"], point["lat"]),
                    )
                    omi_row = cursor.fetchone()
                    omi_columns = [column.name for column in cursor.description]

                    cursor.execute(
                        f"""
                        SELECT pz.cap
                        FROM spatial.postal_zone pz
                        WHERE pz.municipality_id = %s
                          AND ST_Covers(pz.geom, {point_sql})
                        LIMIT 1
                        """,
                        (municipality_id, point["lng"], point["lat"]),
                    )
                    postal_row = cursor.fetchone()

                    cursor.execute(
                        """
                        SELECT year, measure, frequency, amount
                        FROM facts.tax_fact
                        WHERE municipality_id = %s
                        ORDER BY year DESC, measure
                        """,
                        (municipality_id,),
                    )
                    tax_rows = cursor.fetchall()

                    # Census geometry is stored in UTM 32N for this release;
                    # the fixed CRS keeps the GiST candidate filter usable.
                    cursor.execute(
                        f"""
                        SELECT s.*
                        FROM census_sections.sections s
                        WHERE s.procom = %s
                          AND s.geom && ST_Transform({point_sql}, 32632)
                          AND ST_Covers(
                                s.geom,
                                ST_Transform({point_sql}, 32632)
                              )
                        LIMIT 1
                        """,
                        (
                            municipality_values["istat_code"],
                            point["lng"], point["lat"],
                            point["lng"], point["lat"],
                        ),
                    )
                    census_row = cursor.fetchone()
                    census_columns = [column.name for column in cursor.description]

            municipality = {
                key: _postgres_scalar(value)
                for key, value in municipality_values.items()
                if key != "id"
            }
            profile_keys = (
                "observation_count", "tax_fact_count", "total_imponibile",
                "market_zone_count", "pv_n_buildings",
                "pv_pvout_pessimistic_kwh_year_total", "pv_pvout_modern_kwh_year_total",
                "pv_pvout_per_capita_kwh", "pv_kwp_max_total", "pv_high_viability_pct",
                "pv_medium_viability_pct", "pv_low_viability_pct", "pv_not_eligible_pct",
                "pv_observation_count",
            )
            profile = {key: municipality.pop(key, None) for key in profile_keys}
            omi = None
            if omi_row:
                omi = {
                    key: _postgres_scalar(value)
                    for key, value in zip(omi_columns, omi_row, strict=True)
                }
            census = None
            if census_row:
                properties = {
                    key: _postgres_scalar(value)
                    for key, value in zip(census_columns, census_row, strict=True)
                    if key != "geom"
                }
                properties["ratios"] = _census_ratios(properties)
                census = {"type": "Feature", "properties": properties, "geometry": None}
            tax = [
                {
                    key: _postgres_scalar(value)
                    for key, value in zip(("year", "measure", "frequency", "amount"), row, strict=True)
                }
                for row in tax_rows
            ]
            return {
                "parcel_spine_available": parcel_row is not None,
                "parcel_spine": {
                    key: _postgres_scalar(value)
                    for key, value in zip(parcel_columns, parcel_row, strict=True)
                } if parcel_row else None,
                "parcel_spine_reference": national_reference,
                "municipality": municipality,
                "municipality_profile": profile,
                "omi": omi,
                "postal_code": _postgres_scalar(postal_row[0]) if postal_row else None,
                "tax_facts": tax,
                "census": census,
                "source": "aecs4u-stats PostgreSQL",
            }
        except Exception as exc:
            # A failed context lookup must suppress the optional write-back
            # attempt below.  Without this circuit-breaker, an unreachable
            # PostgreSQL source is contacted once here and again by
            # ``upsert_read_model`` while the local FGB/SQLite fallback is
            # already sufficient to serve the panel.  The two connection
            # timeouts can exceed the frontend's request timeout and make the
            # otherwise available coverage appear empty.
            self._retry_at = time.monotonic() + 60
            logger.warning("Postgres parcel context lookup failed for %s: %s", cadastral_code, exc)
            return None


def _external_postgres_dsn(
    *names: str,
    sibling_env: Optional[tuple[Path, str]] = None,
) -> Optional[str]:
    """Read a separately configured read-only PostgreSQL source.

    These databases are not part of ``aecs4u-stats`` and must never be
    confused with the application's ``DATABASE_URL``.  Supporting the
    producer projects' existing names makes deployment configuration simple,
    while the AECS4U-prefixed names are the recommended land-registry names.
    """
    for name in names:
        value = os.getenv(name, "").strip()
        if value.startswith(("postgres://", "postgresql://", "postgresql+")):
            return value
    # Local checkouts keep the source-specific credentials in their own
    # repository .env files. Read only the explicitly named source variable;
    # in particular, never import a sibling DATABASE_URL into this process.
    if sibling_env:
        path, name = sibling_env
        try:
            from dotenv import dotenv_values

            value = str(dotenv_values(path).get(name) or "").strip()
            if value.startswith(("postgres://", "postgresql://", "postgresql+")):
                return value
        except (OSError, TypeError, ValueError):
            pass
    return None


def _external_postgres_lookup_isolated(
    source_name: str,
    dsn: str,
    national_reference: str,
    municipality: Optional[Dict[str, Any]],
    timeout: float = 12,
) -> Dict[str, Any]:
    """Query an external PostgreSQL source outside the API process.

    ``psycopg2-binary`` contains native libpq code.  A malformed/incompatible
    native client can terminate the interpreter with SIGSEGV, which cannot be
    caught by Python's exception handling or by ``asyncio.wait_for``.  The
    external OpenData/PVP databases are optional, so process isolation is the
    appropriate failure boundary: a crashed worker becomes an unavailable
    source while the map/API remains alive.
    """
    request = json.dumps({
        "source": source_name,
        "dsn": dsn,
        "national_reference": national_reference,
        "municipality": municipality,
    })
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "land_registry.external_postgres_worker"],
            input=request,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=os.environ.copy(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("External %s PostgreSQL worker failed: %s", source_name, exc)
        return _external_unavailable(f"{source_name} PostgreSQL")

    if completed.returncode != 0:
        logger.warning(
            "External %s PostgreSQL worker exited with code %s%s",
            source_name,
            completed.returncode,
            " (native crash)" if completed.returncode < 0 else "",
        )
        return _external_unavailable(f"{source_name} PostgreSQL")
    try:
        result = json.loads(completed.stdout)
    except (TypeError, ValueError, json.JSONDecodeError):
        logger.warning("External %s PostgreSQL worker returned invalid JSON", source_name)
        return _external_unavailable(f"{source_name} PostgreSQL")
    return result if isinstance(result, dict) else _external_unavailable(f"{source_name} PostgreSQL")


class _ExternalPostgresSource(_PostgresPoiSource):
    """Small read-only PostgreSQL adapter for an external sibling service."""

    @contextmanager
    def _connection(self):
        """Use a per-statement bound for the recursive read-only lookup."""
        with super()._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '2000ms'")
            yield connection

    @classmethod
    def from_dsn(cls, dsn: Optional[str]):
        if not dsn:
            return None
        try:
            return cls(dsn, max_connections=2)
        except Exception as exc:  # pragma: no cover - defensive config path
            logger.warning("External PostgreSQL source unavailable: %s", exc)
            return None


def _cursor_columns(cursor) -> list[str]:
    columns = []
    for description in cursor.description:
        name = getattr(description, "name", None)
        columns.append(name if name is not None else description[0])
    return columns


def _rows_as_dicts(cursor) -> list[dict]:
    columns = _cursor_columns(cursor)
    return [
        {key: _postgres_scalar(value) for key, value in zip(columns, row, strict=True)}
        for row in cursor.fetchall()
    ]


def _json_value(value: Any) -> Any:
    """Decode JSON returned by either psycopg2 JSON or a text JSON column."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return value
    return value


def _parcel_reference_parts(national_reference: str) -> tuple[str, list[str], list[str]]:
    """Return cadastral code, equivalent sheet values, and parcel values."""
    reference = str(national_reference or "").strip()
    code, separator, suffix = reference.partition("_")
    sheet, dot, parcel = suffix.partition(".")
    if not separator or not dot:
        return code.strip().upper(), [], []
    parcel = parcel.split("/", 1)[0]

    def values(value: str, sheet_value: bool = False) -> list[str]:
        value = str(value or "").strip()
        try:
            compact = str(int(value))
        except ValueError:
            compact = value.lstrip("0") or "0"
        result = [value, compact]
        if sheet_value and compact.isdigit() and int(compact) >= 100 and int(compact) % 100 == 0:
            result.append(str(int(compact) // 100))
        return list(dict.fromkeys(result))

    return code.strip().upper(), values(sheet, True), values(parcel)


def _open_data_result_preview(result: Any) -> Any:
    """Keep OpenData JSON useful for the panel without returning documents."""
    result = _json_value(result)
    if not isinstance(result, (dict, list)):
        return result
    # The property endpoints return ``immobili``/``fabbricati``/``terreni``;
    # retain those values and their surrounding response metadata.  The UI
    # also supports arbitrary scalar keys for older response shapes.
    if isinstance(result, dict):
        preview = {}
        for key, value in result.items():
            if key.lower() in {"documento", "pdf", "base64", "content_base64"}:
                continue
            preview[key] = value
        return preview
    return result


def _open_data_result_matches(
    result: Any,
    municipality_name: str,
    province: str,
    sheets: list[str],
    parcels: list[str],
    check_administration: bool = True,
    cadastral_code: str = "",
) -> bool:
    """Match legacy OpenData JSON when normalized parameter tables are absent."""
    sheet_set = {value.lstrip("0") or "0" for value in sheets}
    parcel_set = {value.lstrip("0") or "0" for value in parcels}
    province_fold = province.casefold()
    municipality_fold = municipality_name.casefold()
    cadastral_code_fold = str(cadastral_code or "").strip().casefold()

    def scalar_values(value: Any):
        if isinstance(value, dict):
            for child in value.values():
                yield from scalar_values(child)
        elif isinstance(value, list):
            for child in value:
                yield from scalar_values(child)
        else:
            yield str(value or "").strip()

    def visit(value: Any) -> bool:
        if isinstance(value, dict):
            folded = {str(key).casefold(): child for key, child in value.items()}
            sheet = folded.get("foglio") or folded.get("sheet")
            parcel = folded.get("particella") or folded.get("parcel")
            municipality = folded.get("comune") or folded.get("municipality")
            province_value = folded.get("provincia") or folded.get("province")
            cadastral_value = (
                folded.get("codice_comune")
                or folded.get("cod_comune")
                or folded.get("cadastral_code")
                or folded.get("municipality_code")
                or folded.get("codice_catastale")
            )
            if sheet is not None and parcel is not None:
                sheet_matches = any(
                    (item.lstrip("0") or "0") in sheet_set for item in scalar_values(sheet)
                )
                parcel_matches = any(
                    (item.lstrip("0") or "0") in parcel_set for item in scalar_values(parcel)
                )
                municipality_matches = (not check_administration) or municipality is None or any(
                    municipality_fold == item.casefold() for item in scalar_values(municipality)
                )
                province_matches = (not check_administration) or province_value is None or any(
                    province_fold == item.casefold() for item in scalar_values(province_value)
                )
                code_matches = cadastral_value is None or not cadastral_code_fold or any(
                    cadastral_code_fold == item.casefold()
                    for item in scalar_values(cadastral_value)
                )
                if sheet_matches and parcel_matches and municipality_matches and province_matches and code_matches:
                    return True
            return any(visit(child) for child in value.values())
        if isinstance(value, list):
            return any(visit(child) for child in value)
        return False

    return visit(_json_value(result))


class _OpenDataPostgresSource(_ExternalPostgresSource):
    """Read successful parcel cadastral queries stored by OpenData."""

    @classmethod
    def from_environment(cls):
        return cls.from_dsn(_external_postgres_dsn(
            "AECS4U_OPENDATA_POSTGRES_DSN",
            "OPENDATA_POSTGRES_DSN",
            "OPENDATA_DATABASE_URL",
            "PROD_DATABASE_URL",
            sibling_env=(Path(__file__).resolve().parents[2] / "opendata" / ".env", "PROD_DATABASE_URL"),
        ))

    def parcel_data(self, national_reference: str, municipality: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        code, sheets, parcels = _parcel_reference_parts(national_reference)
        if not sheets or not parcels:
            return {"records": [], "count": 0, "available": False, "source": "OpenData PostgreSQL"}
        municipality = municipality or {}
        province = str(municipality.get("province") or "").strip()
        municipality_name = str(municipality.get("name") or "").strip()
        sheet_params = ",".join(["%s"] * len(sheets))
        parcel_params = ",".join(["%s"] * len(parcels))
        municipality_filter = ""
        municipality_params: list[str] = []
        if province and municipality_name:
            municipality_filter = """
              AND lower(trim(cl.province)) = lower(trim(%s))
              AND lower(trim(cl.municipality)) = lower(trim(%s))
            """
            municipality_params = [province, municipality_name]
        sql = f"""
            SELECT q.id, q.endpoint, q.status, q.timestamp, q.query_datetime,
                   q.result, cl.cadastre_type, cl.province, cl.municipality,
                   cl.section, cl.sheet, cl.parcel, cl.subunit
            FROM cadastral_queries q
            JOIN cadastral_location_parameters lp ON lp.query_id = q.id
            JOIN cadastral_locations cl ON cl.id = lp.location_id
            WHERE q.endpoint IN ('elenco_immobili', 'prospetto_catastale')
            AND lower(trim(coalesce(q.status, ''))) NOT LIKE '%error%'
            AND lower(trim(coalesce(q.status, ''))) NOT LIKE '%fail%'
              {municipality_filter}
              AND (
                   trim(cl.sheet) IN ({sheet_params})
                   OR ltrim(trim(cl.sheet), '0') IN ({sheet_params})
              )
              AND (
                   split_part(trim(cl.parcel), '/', 1) IN ({parcel_params})
                   OR ltrim(split_part(trim(cl.parcel), '/', 1), '0') IN ({parcel_params})
              )
            ORDER BY q.timestamp DESC
            LIMIT 8
        """
        params = [*municipality_params, *sheets, *sheets, *parcels, *parcels]
        with self._connection() as connection:
            with connection.cursor() as cursor:
                try:
                    cursor.execute(sql, params)
                    records = _rows_as_dicts(cursor)
                except Exception:
                    # Older OpenData deployments have ``cadastral_queries``
                    # but not the normalized location-parameter tables. Their
                    # JSON response still contains the cadastral coordinates;
                    # inspect only the small property-query history and match
                    # those values in Python.
                    connection.rollback()
                    try:
                        cursor.execute(
                            """
                            SELECT id, endpoint, status, timestamp, NULL AS query_datetime, result
                            FROM cadastral_queries
                            WHERE endpoint IN ('elenco_immobili', 'prospetto_catastale')
                              AND lower(trim(coalesce(status, ''))) NOT LIKE '%error%'
                              AND lower(trim(coalesce(status, ''))) NOT LIKE '%fail%'
                            ORDER BY timestamp DESC
                            LIMIT 100
                            """
                        )
                    except Exception:
                        connection.rollback()
                        # Pre-consolidation OpenData installations called the
                        # same table ``cadastral_prospects``.
                        cursor.execute(
                            """
                            SELECT id, endpoint, status, timestamp, NULL AS query_datetime, result
                            FROM cadastral_prospects
                            WHERE endpoint IN ('elenco_immobili', 'prospetto_catastale')
                              AND lower(trim(coalesce(status, ''))) NOT LIKE '%error%'
                              AND lower(trim(coalesce(status, ''))) NOT LIKE '%fail%'
                            ORDER BY timestamp DESC
                            LIMIT 100
                            """
                        )
                    records = [
                        record for record in _rows_as_dicts(cursor)
                        if _open_data_result_matches(
                            record.get("result"), municipality_name, province, sheets, parcels
                        )
                    ][:8]
                if not records:
                    # Normalized parameter rows may be absent or may contain
                    # stale municipality spelling. Search the stored response
                    # payload by its cadastral values before reporting no data.
                    try:
                        cursor.execute(
                            """
                            SELECT id, endpoint, status, timestamp,
                                   NULL AS query_datetime, result
                            FROM cadastral_queries
                            WHERE endpoint IN ('elenco_immobili', 'prospetto_catastale')
                              AND lower(trim(coalesce(status, ''))) NOT LIKE '%error%'
                              AND lower(trim(coalesce(status, ''))) NOT LIKE '%fail%'
                            ORDER BY timestamp DESC
                            LIMIT 100
                            """
                        )
                        candidates = _rows_as_dicts(cursor)
                        records = [
                            record for record in candidates
                            if _open_data_result_matches(
                                record.get("result"), municipality_name, province,
                                sheets, parcels, check_administration=False,
                                cadastral_code=code,
                            )
                        ][:8]
                        if records:
                            municipality_filter = ""
                    except Exception:
                        connection.rollback()
        for record in records:
            record["result"] = _open_data_result_preview(record.get("result"))
        return {
            "records": records,
            "count": len(records),
            "available": bool(records),
            "source": "OpenData PostgreSQL cadastral_queries",
            "match_method": "municipality+sheet+parcel" if municipality_filter else "sheet+parcel",
            "cadastral_code": code,
        }


class _PvpPostgresSource(_ExternalPostgresSource):
    """Read parcel candidates from the normalized PVP modelview database."""

    _RELATION_MAX_DEPTH = 6
    _RELATION_MAX_NODES = 250
    _RELATION_MAX_CHILDREN = 100

    @staticmethod
    def _quote_identifier(identifier: str) -> str:
        """Quote an identifier returned by PostgreSQL catalog metadata."""
        return '"' + str(identifier).replace('"', '""') + '"'

    @staticmethod
    def _relation_short_name(table: str, *, plural: bool) -> str:
        """Make a stable JSON relation name from a PostgreSQL table name."""
        name = str(table)
        for prefix in ("modelview_", "ref_pvp_"):
            if name.startswith(prefix):
                name = name.removeprefix(prefix)
                break
        if plural:
            return name
        if name.endswith("ies"):
            return name[:-3] + "y"
        if name.endswith("s") and not name.endswith("ss"):
            return name[:-1]
        return name

    def _pvp_relation_metadata(self, cursor) -> tuple[dict[str, list[str]], list[dict[str, Any]]]:
        """Read primary-key and FK metadata for the PVP modelview graph.

        The modelview schema has changed over time and includes optional
        reference tables. Reading PostgreSQL's catalogs keeps this resolver
        compatible with those versions instead of hard-coding every model.
        """
        cursor.execute(
            """
            SELECT n.nspname, c.relname,
                   array_agg(a.attname ORDER BY key_columns.ordinality)
            FROM pg_index i
            JOIN pg_class c ON c.oid = i.indrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN LATERAL unnest(i.indkey) WITH ORDINALITY
                 AS key_columns(attnum, ordinality) ON TRUE
            JOIN pg_attribute a
              ON a.attrelid = c.oid
             AND a.attnum = key_columns.attnum
            WHERE i.indisprimary
              AND n.nspname = 'public'
              AND (c.relname LIKE 'modelview_%' OR c.relname LIKE 'ref_pvp_%')
            GROUP BY n.nspname, c.relname
            """
        )
        primary_keys = {
            (str(schema), str(table)): [str(column) for column in columns]
            for schema, table, columns in cursor.fetchall()
        }

        cursor.execute(
            """
            SELECT child_ns.nspname, child.relname,
                   parent_ns.nspname, parent.relname,
                   array_agg(child_column.attname ORDER BY local_columns.ordinality),
                   array_agg(parent_column.attname ORDER BY local_columns.ordinality),
                   constraint_info.conname
            FROM pg_constraint constraint_info
            JOIN pg_class child ON child.oid = constraint_info.conrelid
            JOIN pg_namespace child_ns ON child_ns.oid = child.relnamespace
            JOIN pg_class parent ON parent.oid = constraint_info.confrelid
            JOIN pg_namespace parent_ns ON parent_ns.oid = parent.relnamespace
            JOIN LATERAL unnest(constraint_info.conkey) WITH ORDINALITY
                 AS local_columns(attnum, ordinality) ON TRUE
            JOIN LATERAL unnest(constraint_info.confkey) WITH ORDINALITY
                 AS remote_columns(attnum, ordinality)
              ON remote_columns.ordinality = local_columns.ordinality
            JOIN pg_attribute child_column
              ON child_column.attrelid = child.oid
             AND child_column.attnum = local_columns.attnum
            JOIN pg_attribute parent_column
              ON parent_column.attrelid = parent.oid
             AND parent_column.attnum = remote_columns.attnum
            WHERE constraint_info.contype = 'f'
              AND child_ns.nspname = 'public'
              AND parent_ns.nspname = 'public'
              AND (
                    child.relname LIKE 'modelview_%'
                    OR parent.relname LIKE 'modelview_%'
                    OR parent.relname LIKE 'ref_pvp_%'
                  )
            GROUP BY child_ns.nspname, child.relname,
                     parent_ns.nspname, parent.relname,
                     constraint_info.conname
            """
        )
        foreign_keys = []
        for child_schema, child_table, parent_schema, parent_table, child_columns, parent_columns, name in cursor.fetchall():
            foreign_keys.append(
                {
                    "child": (str(child_schema), str(child_table)),
                    "parent": (str(parent_schema), str(parent_table)),
                    "child_columns": [str(column) for column in child_columns],
                    "parent_columns": [str(column) for column in parent_columns],
                    "name": str(name),
                }
            )
        return primary_keys, foreign_keys

    def _pvp_rows_by_columns(
        self,
        cursor,
        table: tuple[str, str],
        columns: list[str],
        values: list[Any],
        *,
        limit: int,
        lookup_cache: Optional[dict[tuple[Any, ...], list[dict[str, Any]]]] = None,
    ) -> list[dict[str, Any]]:
        """Read JSON rows by a catalog-discovered key without interpolating values."""
        if not columns or len(columns) != len(values):
            return []
        try:
            cache_key = (table, tuple(columns), tuple(values), limit)
            hash(cache_key)
        except TypeError:
            cache_key = None
        if lookup_cache is not None and cache_key is not None and cache_key in lookup_cache:
            return lookup_cache[cache_key]
        schema, table_name = (self._quote_identifier(part) for part in table)
        column_sql = [self._quote_identifier(column) for column in columns]
        where = " AND ".join(f"t.{column} = %s" for column in column_sql)
        cursor.execute(
            f"""
            SELECT to_jsonb(t)
            FROM {schema}.{table_name} AS t
            WHERE {where}
            LIMIT {int(limit)}
            """,
            values,
        )
        rows = []
        for result in cursor.fetchall():
            raw = _json_value(result[0] if isinstance(result, (tuple, list)) else result)
            if isinstance(raw, dict):
                rows.append(raw)
        if lookup_cache is not None and cache_key is not None:
            lookup_cache[cache_key] = rows
        return rows

    def _resolve_pvp_record_relations(
        self,
        cursor,
        record: dict[str, Any],
        metadata: Optional[tuple[dict[str, list[str]], list[dict[str, Any]]]] = None,
        context: Optional[dict[str, Any]] = None,
    ) -> Optional[dict[str, Any]]:
        """Return one recursively merged PVP entity graph.

        Rows keep their original columns, while resolved FK objects are added
        under ``_relations``. A ``_ref`` marker terminates cycles such as
        sale -> procedure -> sale. Reverse FK edges are included as arrays,
        which exposes one-to-many entities such as events and attachments.
        """
        primary_keys, foreign_keys = metadata or self._pvp_relation_metadata(cursor)
        if not primary_keys or not foreign_keys:
            return None
        context = context if context is not None else {}
        lookup_cache = context.setdefault("lookup_cache", {})
        # The worker itself has a 12-second hard limit. Leave time for JSON
        # serialization and process shutdown after the relationship queries.
        context.setdefault("deadline", time.monotonic() + 8.0)

        outgoing: dict[tuple[str, str], list[dict[str, Any]]] = {}
        incoming: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for relation in foreign_keys:
            outgoing.setdefault(relation["child"], []).append(relation)
            incoming.setdefault(relation["parent"], []).append(relation)

        roots: list[tuple[str, str, Any]] = []
        registry_id = record.get("registry_id")
        if registry_id is not None and ("public", "modelview_registries") in primary_keys:
            roots.append(("public", "modelview_registries", registry_id))
        asset_pk = record.get("asset_pk")
        if asset_pk is not None and ("public", "modelview_assets") in primary_keys:
            roots.append(("public", "modelview_assets", asset_pk))
        if not roots:
            return None

        row_cache: dict[tuple[tuple[str, str], tuple[Any, ...]], dict[str, Any]] = {}
        node_count = 0

        def row_key(table: tuple[str, str], row: dict[str, Any]) -> Optional[tuple[tuple[str, str], tuple[Any, ...]]]:
            columns = primary_keys.get(table)
            if not columns or any(row.get(column) is None for column in columns):
                return None
            try:
                values = tuple(row.get(column) for column in columns)
                hash(values)
            except TypeError:
                return None
            return table, values

        def load_row(table: tuple[str, str], values: tuple[Any, ...]) -> Optional[dict[str, Any]]:
            if time.monotonic() >= context["deadline"]:
                return None
            key = (table, values)
            if key in row_cache:
                return row_cache[key]
            rows = self._pvp_rows_by_columns(
                cursor,
                table,
                primary_keys.get(table, []),
                list(values),
                limit=1,
                lookup_cache=lookup_cache,
            )
            if not rows:
                return None
            row_cache[key] = rows[0]
            return rows[0]

        def relation_name(relation: dict[str, Any], *, reverse: bool, used: set[str]) -> str:
            table = relation["child"] if reverse else relation["parent"]
            name = self._relation_short_name(table[1], plural=reverse)
            if name in used:
                suffix = "_".join(relation["child_columns"])
                name = f"{name}_by_{suffix}"
            used.add(name)
            return name

        def build(table: tuple[str, str], row: dict[str, Any], depth: int, stack: tuple[str, ...]) -> dict[str, Any]:
            nonlocal node_count
            entity_id = row_key(table, row)
            entity_ref = (
                f"{table[0]}.{table[1]}:"
                + "|".join(str(value) for value in (entity_id[1] if entity_id else ()))
            )
            if entity_ref in stack:
                return {"_ref": entity_ref}
            if node_count >= self._RELATION_MAX_NODES:
                return {"_table": table[1], "_ref": entity_ref, "_truncated": "node_limit"}
            node_count += 1

            merged = dict(row)
            merged["_table"] = table[1]
            merged["_primary_key"] = {
                column: row.get(column) for column in primary_keys.get(table, [])
            }
            if depth >= self._RELATION_MAX_DEPTH:
                merged["_relations_truncated"] = "depth_limit"
                return merged

            next_stack = (*stack, entity_ref)
            relations: dict[str, Any] = {}
            used_names: set[str] = set()

            for relation in outgoing.get(table, []):
                if time.monotonic() >= context["deadline"]:
                    merged["_relations_truncated"] = "time_limit"
                    break
                values = [row.get(column) for column in relation["child_columns"]]
                if any(value is None for value in values):
                    continue
                target_table = relation["parent"]
                target_rows = self._pvp_rows_by_columns(
                    cursor,
                    target_table,
                    relation["parent_columns"],
                    values,
                    limit=1,
                    lookup_cache=lookup_cache,
                )
                if not target_rows:
                    continue
                name = relation_name(relation, reverse=False, used=used_names)
                relations[name] = build(target_table, target_rows[0], depth + 1, next_stack)

            for relation in incoming.get(table, []):
                if time.monotonic() >= context["deadline"]:
                    merged["_relations_truncated"] = "time_limit"
                    break
                # Do not walk back into an ancestor table. Besides avoiding
                # cycles, this prevents repeatedly loading the complete asset
                # collection while resolving sale -> assets.
                child_ref_prefix = f"{relation['child'][0]}.{relation['child'][1]}:"
                if any(ref.startswith(child_ref_prefix) for ref in next_stack):
                    continue
                parent_values = [row.get(column) for column in relation["parent_columns"]]
                if any(value is None for value in parent_values):
                    continue
                child_rows = self._pvp_rows_by_columns(
                    cursor,
                    relation["child"],
                    relation["child_columns"],
                    parent_values,
                    limit=self._RELATION_MAX_CHILDREN,
                    lookup_cache=lookup_cache,
                )
                if not child_rows:
                    continue
                name = relation_name(relation, reverse=True, used=used_names)
                relations[name] = [
                    build(relation["child"], child, depth + 1, next_stack)
                    for child in child_rows
                ]

            if relations:
                merged["_relations"] = relations
            return merged

        resolved: dict[str, Any] = {}
        seen_roots: set[tuple[tuple[str, str], tuple[Any, ...]]] = set()
        for schema, table_name, value in roots:
            table = (schema, table_name)
            values = (value,)
            root_key = (table, values)
            if root_key in seen_roots:
                continue
            seen_roots.add(root_key)
            row = load_row(table, values)
            if row is None:
                continue
            key = "registry" if table_name == "modelview_registries" else "asset"
            resolved[key] = build(table, row, 0, ())
        return resolved or None

    @classmethod
    def from_environment(cls):
        return cls.from_dsn(_external_postgres_dsn(
            "AECS4U_PVP_MODELVIEW_POSTGRES_DSN",
            "PVP_MODELVIEW_POSTGRES_DSN",
            "PVP_MODELVIEW_DATABASE_URL",
            "PROD_MODELVIEW_DATABASE_URL_PVP",
            sibling_env=(Path(__file__).resolve().parents[2] / "property-scraper" / ".env", "PROD_MODELVIEW_DATABASE_URL_PVP"),
        ))

    def parcel_data(self, national_reference: str, municipality: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        code, sheets, parcels = _parcel_reference_parts(national_reference)
        if not sheets or not parcels:
            return {"records": [], "count": 0, "available": False, "source": "PVP modelview PostgreSQL"}
        municipality = municipality or {}
        municipality_name = str(municipality.get("name") or "").strip()
        province = str(municipality.get("province") or "").strip()
        pvp_municipality_code = str(
            municipality.get("pvp_municipality_code")
            or municipality.get("istat_code")
            or municipality.get("municipality_code")
            or ""
        ).strip()
        sheet_params = ",".join(["%s"] * len(sheets))
        parcel_params = ",".join(["%s"] * len(parcels))
        municipality_filter = ""
        municipality_params: list[str] = []
        if municipality_name or province or pvp_municipality_code:
            municipality_filter = """
              AND (
                   trim(coalesce(ad.municipality_code, '')) = trim(%s)
                   OR (
                       lower(trim(coalesce(s.city, ''))) = lower(trim(%s))
                       AND lower(trim(coalesce(s.province, ''))) = lower(trim(%s))
                   )
              )
            """
            municipality_params = [pvp_municipality_code, municipality_name, province]
        sql = f"""
            SELECT r.id AS registry_id, r.asset_id, a.id AS asset_pk,
                   r.section, r.sheet, r.parcel,
                   r.sub, r.sub2, r.sub_parcel,
                   a.asset_id AS source_asset_id, a.description, a.address_text,
                   a.asset_label, a.lot_type_code, a.lot_category_code,
                   a.asset_type_code, a.availability, a.surface_area,
                   a.number_of_rooms, a.floor, a.energy_class, a.market_value,
                   a.annual_rental_income, a.commercial_area,
                   s.id AS sale_id, s.source, s.source_url, s.sale_date,
                   s.offer_deadline_date, s.announcement_status,
                   s.minimum_offer, s.base_auction_price, s.award_price,
                   s.city, s.province, s.description AS sale_description,
                   ad.street, ad.house_number, ad.postal_code,
                   ad.municipality_code,
                   pr.registry_year, pr.registry_number, pr.appraisal_value
            FROM modelview_registries r
            JOIN LATERAL (
                SELECT a0.*
                FROM modelview_assets a0
                WHERE a0.id = r.asset_id
                   OR a0.asset_id = r.asset_id
                ORDER BY (a0.id = r.asset_id) DESC, a0.id
                LIMIT 1
            ) a ON TRUE
            LEFT JOIN modelview_sales s ON s.id = a.sale_id
            LEFT JOIN modelview_addresses ad ON ad.id = a.address_id
            LEFT JOIN modelview_procedures pr ON pr.id = a.procedure_id
            WHERE (
                    trim(r.sheet) IN ({sheet_params})
                    OR ltrim(trim(r.sheet), '0') IN ({sheet_params})
                  )
              AND (
                    split_part(trim(r.parcel), '/', 1) IN ({parcel_params})
                    OR ltrim(split_part(trim(r.parcel), '/', 1), '0') IN ({parcel_params})
                  )
              {municipality_filter}
            ORDER BY s.sale_date DESC NULLS LAST, r.id
            LIMIT 25
        """
        params = [*sheets, *sheets, *parcels, *parcels, *municipality_params]
        match_method = "municipality+code+sheet+parcel" if municipality_filter else "code+sheet+parcel"
        relations_resolved = False
        relation_error = None
        relation_context: dict[str, Any] = {}
        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                records = _rows_as_dicts(cursor)
                if not records:
                    # Some modelview deployments persist cadastral identity on
                    # the asset JSON/columns and do not populate
                    # modelview_registries. Inspect the asset row as JSON so
                    # this remains compatible with both schemas without
                    # referring to columns that may not exist in older DBs.
                    asset_sql = f"""
                        SELECT
                            a.id AS asset_pk, a.asset_id,
                            NULL AS section, NULL AS sheet, NULL AS parcel,
                            NULL AS sub, NULL AS sub2, NULL AS sub_parcel,
                            a.asset_id AS source_asset_id, a.description,
                            a.address_text, a.asset_label, a.lot_type_code,
                            a.lot_category_code, a.asset_type_code,
                            a.availability, a.surface_area, a.number_of_rooms,
                            a.floor, a.energy_class, a.market_value,
                            a.annual_rental_income, a.commercial_area,
                            s.id AS sale_id, s.source, s.source_url, s.sale_date,
                            s.offer_deadline_date, s.announcement_status,
                            s.minimum_offer, s.base_auction_price, s.award_price,
                            s.city, s.province,
                            s.description AS sale_description,
                            ad.street, ad.house_number, ad.postal_code,
                            ad.municipality_code,
                            pr.registry_year, pr.registry_number,
                            pr.appraisal_value
                        FROM modelview_assets a
                        LEFT JOIN modelview_sales s ON s.id = a.sale_id
                        LEFT JOIN modelview_addresses ad ON ad.id = a.address_id
                        LEFT JOIN modelview_procedures pr ON pr.id = a.procedure_id
                        WHERE (
                            EXISTS (
                                SELECT 1
                                FROM jsonb_each_text(to_jsonb(a)) e
                                WHERE (
                                      lower(e.key) IN
                                          ('sheet', 'foglio', 'cadastral_sheet',
                                           'sheet_number', 'cadastral_sheet_number')
                                      OR lower(e.key) LIKE '%sheet%'
                                      OR lower(e.key) LIKE '%foglio%'
                                )
                                  AND (
                                      trim(e.value) IN ({sheet_params})
                                      OR ltrim(trim(e.value), '0') IN ({sheet_params})
                                  )
                            )
                            AND EXISTS (
                                SELECT 1
                                FROM jsonb_each_text(to_jsonb(a)) e
                                WHERE (
                                      lower(e.key) IN
                                          ('parcel', 'particella', 'cadastral_parcel',
                                           'parcel_number', 'cadastral_parcel_number')
                                      OR lower(e.key) LIKE '%parcel%'
                                      OR lower(e.key) LIKE '%particella%'
                                )
                                  AND (
                                      split_part(trim(e.value), '/', 1) IN ({parcel_params})
                                      OR ltrim(split_part(trim(e.value), '/', 1), '0') IN ({parcel_params})
                                  )
                            )
                            AND EXISTS (
                                SELECT 1
                                FROM jsonb_each_text(to_jsonb(a)) e
                                WHERE (
                                      lower(e.key) LIKE '%municipality%'
                                      OR lower(e.key) LIKE '%comune%'
                                      OR lower(e.key) LIKE '%cadastral%'
                                )
                                  AND lower(e.value) LIKE lower(%s)
                            )
                        )
                        OR EXISTS (
                            SELECT 1
                            FROM jsonb_each_text(to_jsonb(a)) e
                            WHERE lower(e.value) LIKE lower(%s)
                              AND lower(e.value) LIKE lower(%s)
                              AND (
                                  lower(e.key) LIKE '%municipality%'
                                  OR lower(e.key) LIKE '%comune%'
                                  OR lower(e.key) LIKE '%cadastral%'
                              )
                        )
                        ORDER BY s.sale_date DESC NULLS LAST, a.id
                        LIMIT 25
                    """
                    asset_params = [
                        *sheets, *sheets, *parcels, *parcels,
                        f"%{code}%", f"%{code}%", f"%{parcels[0]}%",
                    ]
                    cursor.execute(asset_sql, asset_params)
                    records = _rows_as_dicts(cursor)
                    if records:
                        match_method = "asset_fields"
                if records:
                    try:
                        relation_metadata = self._pvp_relation_metadata(cursor)
                        for record in records:
                            try:
                                resolved = self._resolve_pvp_record_relations(
                                    cursor, record, relation_metadata, relation_context
                                )
                            except Exception as exc:  # noqa: BLE001 - one malformed relation must not hide the match
                                logger.warning(
                                    "PVP relation expansion failed for record %s: %s",
                                    record.get("registry_id") or record.get("asset_pk"),
                                    exc,
                                )
                                continue
                            if resolved:
                                record["resolved_relations"] = resolved
                                relations_resolved = True
                    except Exception as exc:  # noqa: BLE001 - catalog differences are optional
                        relation_error = str(exc) or type(exc).__name__
                        logger.warning("PVP relation metadata unavailable: %s", exc)
        return {
            "records": records,
            "count": len(records),
            "available": bool(records),
            "source": "PVP modelview PostgreSQL",
            "match_method": match_method,
            "confidence": "high" if records and municipality_filter else "medium" if records else None,
            "cadastral_code": code,
            "relations_resolved": relations_resolved,
            "merged_records": [
                record["resolved_relations"]
                for record in records
                if record.get("resolved_relations")
            ],
            **({"relations_error": relation_error} if relation_error else {}),
        }


class _SisterPostgresSource(_ExternalPostgresSource):
    """Read the richer SISTER PostgreSQL store when explicitly configured."""

    @classmethod
    def from_environment(cls):
        return cls.from_dsn(_external_postgres_dsn(
            "AECS4U_SISTER_POSTGRES_DSN",
            "SISTER_POSTGRES_DSN",
            "SISTER_DATABASE_URL",
        ))

    @staticmethod
    def _match_location(row: dict, sheets: set[str], parcels: set[str], province_values: set[str], municipality_values: set[str]) -> bool:
        sheet_values = _SisterDocumentSource._normalized_values(row.get("location_sheet"), sheet=True)
        parcel_values = _SisterDocumentSource._normalized_values(row.get("location_parcel"))
        return (
            bool(sheet_values.intersection(sheets))
            and bool(parcel_values.intersection(parcels))
            and (not province_values or str(row.get("location_province") or "").strip().casefold() in province_values)
            and (not municipality_values or str(row.get("location_municipality") or "").strip().casefold() in municipality_values)
        )

    def parcel_data(self, national_reference: str, municipality: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        code, sheets, parcels = _parcel_reference_parts(national_reference)
        if not sheets or not parcels:
            return {"records": [], "count": 0, "available": False, "source": "SISTER PostgreSQL documents"}
        municipality = municipality or {}
        province_values = {
            str(value).strip().casefold()
            for value in (municipality.get("province"), municipality.get("province_sigla"))
            if value not in (None, "")
        }
        municipality_values = {
            str(value).strip().casefold()
            for value in (municipality.get("name"), municipality.get("municipality"))
            if value not in (None, "")
        }
        sheets_set = {value.casefold() for value in sheets}
        parcels_set = {value.casefold() for value in parcels}

        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT d.id, d.response_id, d.document_type, d.file_format,
                           d.filename, d.file_path, d.file_size, d.created_at,
                           d.subject, d.requested_at, m.content,
                           m.view_subtype, m.protocol, m.year, m.title,
                           m.reference_date, m.registry_view_type, m.service_type,
                           m.generation_date, l.province AS location_province,
                           l.municipality AS location_municipality,
                           l.sheet AS location_sheet, l.parcel AS location_parcel
                    FROM public.visura_documents d
                    LEFT JOIN public.document_metadata m ON m.id = d.id
                    LEFT JOIN public.cadastral_locations l ON l.id = m.location_id
                    WHERE lower(trim(d.document_type)) = ANY(%s)
                    ORDER BY d.id
                    """,
                    ([item for item in sorted(_SisterDocumentSource._DOCUMENT_TYPES)],),
                )
                rows = _rows_as_dicts(cursor)

                matched = []
                response_ids = []
                for row in rows:
                    location_match = self._match_location(row, sheets_set, parcels_set, province_values, municipality_values)
                    content_match = _SisterDocumentSource._content_matches(
                        row.get("content"), code, sheets_set, parcels_set, province_values, municipality_values
                    )
                    if not location_match and not content_match:
                        continue
                    matched.append((row, "cadastral_location" if location_match else "document_xml"))
                    if row.get("response_id"):
                        response_ids.append(row["response_id"])

                properties_by_response: dict[str, list[dict]] = {}
                if response_ids:
                    cursor.execute(
                        """
                        SELECT vp.response_id, vp.property_type, vp.address,
                               vp.partita, vp.category, vp.cadastral_class,
                               vp.consistency, vp.income, vp.census_zone,
                               vp.quality, vp.area, vp.dominical_income,
                               vp.agricultural_income, l.sheet, l.parcel,
                               l.subunit
                        FROM public.visura_properties vp
                        JOIN public.cadastral_locations l ON l.id = vp.location_id
                        WHERE vp.response_id = ANY(%s)
                          AND ltrim(trim(l.sheet), '0') = ANY(%s)
                          AND ltrim(trim(l.parcel), '0') = ANY(%s)
                        ORDER BY vp.id
                        """,
                        (list(dict.fromkeys(response_ids)), list({value.lstrip('0') or '0' for value in sheets}), list({value.lstrip('0') or '0' for value in parcels})),
                    )
                    for row in _rows_as_dicts(cursor):
                        properties_by_response.setdefault(str(row.get("response_id")), []).append(row)

                owners_by_response: dict[str, list[dict]] = {}
                if response_ids:
                    cursor.execute(
                        """
                        SELECT vo.response_id, cs.display_name, cs.fiscal_code,
                               cs.subject_type, r.right_type, r.right_code,
                               r.right_description, r.ownership_share,
                               r.start_date, r.end_date
                        FROM public.visura_owners vo
                        LEFT JOIN public.cadastral_subjects cs ON cs.id = vo.subject_id
                        LEFT JOIN public.ownership_rights r ON r.id = vo.right_id
                        WHERE vo.response_id = ANY(%s)
                        ORDER BY vo.id
                        """,
                        (list(dict.fromkeys(response_ids)),),
                    )
                    for row in _rows_as_dicts(cursor):
                        owners_by_response.setdefault(str(row.get("response_id")), []).append(row)

        records = []
        for row, match_method in matched:
            record = _SisterDocumentSource._record(row, match_method)
            data = record["result"].setdefault("data", {})
            response_key = str(row.get("response_id") or "")
            if response_key in properties_by_response and "immobili" not in data:
                data["immobili"] = properties_by_response[response_key][:24]
            if response_key in owners_by_response and "intestatari" not in data:
                data["intestatari"] = owners_by_response[response_key][:24]
            records.append(record)
        return {
            "records": records,
            "count": len(records),
            "available": bool(records),
            "source": "SISTER PostgreSQL documents",
            "match_method": "municipality+sheet+parcel",
            "cadastral_code": code,
        }


_sister_postgres_source: "_SisterPostgresSource | None" = None
_sister_postgres_source_loaded = False
_opendata_postgres_source: "_OpenDataPostgresSource | None" = None
_opendata_postgres_source_loaded = False
_pvp_postgres_source: "_PvpPostgresSource | None" = None
_pvp_postgres_source_loaded = False


def _get_sister_postgres_source() -> "_SisterPostgresSource | None":
    global _sister_postgres_source, _sister_postgres_source_loaded
    if not _sister_postgres_source_loaded:
        _sister_postgres_source = _SisterPostgresSource.from_environment()
        _sister_postgres_source_loaded = True
    return _sister_postgres_source


def _get_opendata_postgres_source() -> "_OpenDataPostgresSource | None":
    global _opendata_postgres_source, _opendata_postgres_source_loaded
    if not _opendata_postgres_source_loaded:
        _opendata_postgres_source = _OpenDataPostgresSource.from_environment()
        _opendata_postgres_source_loaded = True
    return _opendata_postgres_source


def _get_pvp_postgres_source() -> "_PvpPostgresSource | None":
    global _pvp_postgres_source, _pvp_postgres_source_loaded
    if not _pvp_postgres_source_loaded:
        _pvp_postgres_source = _PvpPostgresSource.from_environment()
        _pvp_postgres_source_loaded = True
    return _pvp_postgres_source


def _external_unavailable(source_name: str) -> Dict[str, Any]:
    return {"records": [], "count": 0, "available": False, "source": source_name}


def get_opendata_for_parcel(national_reference: str, municipality: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if municipality is None or not municipality.get("name"):
        # The cadastral code is enough to resolve the municipality from the
        # local ISTAT cache. This also supplies the province/name needed for
        # the exact SISTER document match below.
        inferred_municipality = get_municipality_by_cadastral_code(
            str(national_reference).split("_", 1)[0], use_postgres=False
        )
        municipality = {**(inferred_municipality or {}), **(municipality or {})}

    sister_postgres = _get_sister_postgres_source()
    sister_postgres_result = None
    if sister_postgres is not None and sister_postgres.available():
        try:
            sister_result = _external_postgres_lookup_isolated(
                "Sister", sister_postgres.dsn, national_reference, municipality
            )
            if sister_result.get("records"):
                return sister_result
            # Keep a successful SISTER no-match result as the diagnostic
            # fallback if the optional OpenData database is unavailable too.
            if not sister_result.get("error"):
                sister_postgres_result = sister_result
        except Exception as exc:
            sister_postgres._retry_at = time.monotonic() + 60
            logger.warning("SISTER PostgreSQL parcel lookup failed: %s", exc)

    sister_source = _get_sister_document_source()
    sister_result = (
        sister_source.documents_for_parcel(
            national_reference,
            str(national_reference).split("_", 1)[0],
            municipality,
        )
        if sister_source is not None and sister_source.available()
        else {"records": [], "count": 0, "available": False, "source": "SISTER SQLite documents"}
    )
    # SISTER already contains the original visura and its parsed XML. Prefer
    # that local exact match so the panel is not delayed by an optional remote
    # PostgreSQL source (which may be configured but unreachable).
    if sister_result.get("records"):
        return sister_result
    dsn = _external_postgres_dsn(
        "AECS4U_OPENDATA_POSTGRES_DSN", "OPENDATA_POSTGRES_DSN",
        "OPENDATA_DATABASE_URL", "PROD_DATABASE_URL",
        sibling_env=(Path(__file__).resolve().parents[2] / "opendata" / ".env", "PROD_DATABASE_URL"),
    )
    source = _get_opendata_postgres_source()
    if dsn is None or source is None or not source.available():
        if sister_result.get("records"):
            return sister_result
        if sister_postgres_result is not None:
            return sister_postgres_result
        return sister_result if sister_source is not None and sister_source.available() else _external_unavailable("OpenData PostgreSQL")
    try:
        result = _external_postgres_lookup_isolated("OpenData", dsn, national_reference, municipality)
        return result if result.get("records") else sister_postgres_result or result
    except Exception as exc:
        source._retry_at = time.monotonic() + 60
        logger.warning("OpenData PostgreSQL parcel lookup failed: %s", exc)
        return sister_result if sister_result.get("records") else sister_postgres_result or _external_unavailable("OpenData PostgreSQL")


def get_pvp_for_parcel(national_reference: str, municipality: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    dsn = _external_postgres_dsn(
        "AECS4U_PVP_MODELVIEW_POSTGRES_DSN", "PVP_MODELVIEW_POSTGRES_DSN",
        "PVP_MODELVIEW_DATABASE_URL", "PROD_MODELVIEW_DATABASE_URL_PVP",
        sibling_env=(Path(__file__).resolve().parents[2] / "property-scraper" / ".env", "PROD_MODELVIEW_DATABASE_URL_PVP"),
    )
    source = _get_pvp_postgres_source()
    if dsn is None or source is None or not source.available():
        return _external_unavailable("PVP modelview PostgreSQL")
    if municipality is None or not municipality.get("pvp_municipality_code"):
        inferred_municipality = get_municipality_by_cadastral_code(
            str(national_reference).split("_", 1)[0], use_postgres=False
        )
        municipality = {**(inferred_municipality or {}), **(municipality or {})}
    if municipality.get("istat_code") and not municipality.get("pvp_municipality_code"):
        municipality["pvp_municipality_code"] = str(municipality["istat_code"]).strip()
    try:
        return _external_postgres_lookup_isolated("PVP", dsn, national_reference, municipality)
    except Exception as exc:
        source._retry_at = time.monotonic() + 60
        logger.warning("PVP PostgreSQL parcel lookup failed: %s", exc)
        return _external_unavailable("PVP modelview PostgreSQL")


def external_postgres_status() -> Dict[str, Dict[str, Any]]:
    return {
        "opendata_postgres": {
            "available": bool(_get_opendata_postgres_source()),
            "source": "OpenData PostgreSQL",
        },
        "pvp_modelview_postgres": {
            "available": bool(_get_pvp_postgres_source()),
            "source": "PVP modelview PostgreSQL",
        },
    }


_postgres_poi_source: "_PostgresPoiSource | None" = None
_postgres_poi_source_loaded = False
_postgres_stats_source: "_PostgresStatsSource | None" = None
_postgres_stats_source_loaded = False


def _get_postgres_poi_source() -> "_PostgresPoiSource | None":
    global _postgres_poi_source, _postgres_poi_source_loaded
    if not _postgres_poi_source_loaded:
        _postgres_poi_source = _PostgresPoiSource.from_environment()
        _postgres_poi_source_loaded = True
    return _postgres_poi_source


def _get_postgres_stats_source() -> "_PostgresStatsSource | None":
    global _postgres_stats_source, _postgres_stats_source_loaded
    if not _postgres_stats_source_loaded:
        _postgres_stats_source = _PostgresStatsSource.from_environment()
        _postgres_stats_source_loaded = True
    return _postgres_stats_source


def postgres_stats_available() -> bool:
    """Return whether the configured aecs4u-stats PostgreSQL source is usable."""
    source = _get_postgres_stats_source()
    return source is not None and source.available()


def istat_db_available() -> bool:
    """True when the ISTAT reference SQLite store exists on this host."""
    path = _istat_sqlite_path()
    return path.exists() and path.stat().st_size > 0


def _istat_sqlite_path() -> Path:
    """Resolve the installed ISTAT municipality store.

    ``aecs4u-stats`` currently calls this file ``eurostat.IT.sqlite`` while
    the shared provisioned data volume used by the applications still ships
    the same schema as ``istat.sqlite``.  Prefer an explicitly configured
    directory, then the shared volume, and finally the upstream default.
    Keeping this resolution in the consumer avoids requiring a symlink or a
    data-file rename on deployments that mount the legacy store read-only.
    """
    configured_dir = os.getenv("ISTAT_DATA_DIR")
    candidates = []
    if configured_dir:
        base = Path(configured_dir).expanduser()
        candidates.extend((base / "eurostat.IT.sqlite", base / "istat.sqlite"))

    shared_dir = Path("/data/istat")
    candidates.extend((shared_dir / "eurostat.IT.sqlite", shared_dir / "istat.sqlite"))
    candidates.append(Path(ISTAT_SQLITE_PATH))

    for candidate in candidates:
        try:
            if candidate.exists() and candidate.stat().st_size > 0:
                return candidate
        except OSError:
            continue
    return Path(ISTAT_SQLITE_PATH)


def _census_store_path() -> Optional[Path]:
    """Resolve the census store, including the shared volume fallback."""
    configured_dir = os.getenv("ISTAT_DATA_DIR")
    candidates = []
    if _CENSUS_STORE_PATH is not None:
        candidates.append(Path(_CENSUS_STORE_PATH))
    if configured_dir:
        candidates.append(Path(configured_dir).expanduser() / "census_sections.IT.duckdb")
    candidates.append(Path("/data/istat/census_sections.IT.duckdb"))

    for candidate in candidates:
        try:
            if candidate.exists() and candidate.stat().st_size > 0:
                return candidate
        except OSError:
            continue
    return candidates[0] if candidates else None


def poi_db_available() -> bool:
    """True when POIs are servable: the aecs4u-stats PostGIS store when
    configured, otherwise the local OSM POI SQLite store."""
    source = _get_postgres_poi_source()
    if source is not None and source.available():
        return True
    return resolve_poi_db() is not None


def omi_db_available_public() -> bool:
    """True when PostgreSQL or the compatibility OMI store is available."""
    return postgres_stats_available() or omi_db_available(_omi_sqlite_path())


def _omi_sqlite_path() -> Path:
    """Resolve OMI data from the configured or shared aecs4u-stats volume."""
    configured_dir = os.getenv("ISTAT_DATA_DIR")
    candidates = []
    if configured_dir:
        candidates.append(Path(configured_dir).expanduser() / "omi.IT.sqlite")
    candidates.append(Path("/data/istat/omi.IT.sqlite"))
    candidates.append(Path(OMI_DB_PATH))
    for candidate in candidates:
        try:
            if candidate.exists() and candidate.stat().st_size > 0:
                return candidate
        except OSError:
            continue
    return Path(OMI_DB_PATH)


def _omi_zones_dir() -> Path:
    """Resolve the optional OMI boundary mirror beside the shared data store."""
    configured_dir = os.getenv("OMI_ZONES_DIR")
    candidates = []
    if configured_dir:
        candidates.append(Path(configured_dir).expanduser())
    candidates.append(Path("/data/istat/omi_zones"))
    candidates.append(Path(OMI_ZONES_DIR))
    for candidate in candidates:
        try:
            if candidate.exists():
                return candidate
        except OSError:
            continue
    return Path(OMI_ZONES_DIR)


def zone_boundaries_available(province: Optional[str] = None) -> bool:
    """Return whether OMI zone boundaries exist, nationally or by province."""
    if province is not None:
        return _zone_boundaries_available(province, zones_dir=_omi_zones_dir())
    zones_dir = _omi_zones_dir()
    return zones_dir.exists() and any(zones_dir.glob("*.geojson"))


def mef_db_available_public() -> bool:
    """True when PostgreSQL or the compatibility MEF store is available."""
    return postgres_stats_available() or mef_db_available(_mef_sqlite_path())


def _mef_sqlite_path() -> Path:
    """Resolve the MEF store from the shared ISTAT volume before package defaults."""
    configured_dir = os.getenv("ISTAT_DATA_DIR")
    candidates = []
    if configured_dir:
        candidates.append(Path(configured_dir).expanduser() / "mef_irpef.IT.sqlite")
    candidates.append(Path("/data/istat/mef_irpef.IT.sqlite"))
    if _MEF_DB_PATH is not None:
        candidates.append(Path(_MEF_DB_PATH))
    for candidate in candidates:
        try:
            if candidate.exists() and candidate.stat().st_size > 0:
                return candidate
        except OSError:
            continue
    return Path(candidates[-1])


def _hazards_sqlite_path() -> Path:
    """Resolve the seismic/hazard store from the shared ISTAT volume."""
    configured_dir = os.getenv("ISTAT_DATA_DIR")
    candidates = []
    if configured_dir:
        candidates.append(Path(configured_dir).expanduser() / "hazards.IT.sqlite")
    candidates.append(Path("/data/istat/hazards.IT.sqlite"))
    if _HAZARDS_DB_PATH is not None:
        candidates.append(Path(_HAZARDS_DB_PATH))
    for candidate in candidates:
        try:
            if candidate.exists() and candidate.stat().st_size > 0:
                return candidate
        except OSError:
            continue
    return Path(candidates[-1])


def enrichment_status() -> Dict[str, Any]:
    """Availability report for every aecs4u-stats dataset we consume."""
    status = {
        "istat_municipalities": {
            "available": istat_db_available() or postgres_stats_available(),
            "path": str(_istat_sqlite_path()),
        },
        "cadastral_parcels": {
            "available": cadastral_store_available(),
            "note": "per-region DuckDB stores or provisioned Agenzia delle Entrate INSPIRE FGB files",
        },
        "census_sections": {
            "available": census_db_available() or postgres_stats_available(),
            "note": "2021 ISTAT census sections; build with python -m aecs4u_stats.census.scripts.import_census_sections --all",
        },
        "aecs4u_stats_postgres": {
            "available": postgres_stats_available(),
            "source": "aecs4u-stats PostgreSQL",
            "note": "Canonical context database; cadastral parcel spine is required for full parcel joins",
        },
        "sister_buildings": {
            "available": sister_buildings_available(),
            "path": str(_sister_db_path()) if _sister_db_path() else None,
            "source": "SISTER SQLite",
            "note": "Cached building classifications; populated after a SISTER cadastral visura is stored",
        },
        "sister_documents": {
            "available": sister_documents_available(),
            "path": str(_sister_db_path()) if _sister_db_path() else None,
            "source": "SISTER SQLite visura_documents",
            "note": "Exact parcel matches from visura_fabbricati, visura_terreni, elenco_immobili and visura_soggetto",
        },
        "sister_postgres": {
            "available": bool(_get_sister_postgres_source()),
            "source": "SISTER PostgreSQL",
            "note": "Optional richer parsed property and owner rows; configure SISTER_POSTGRES_DSN",
        },
        "opendata_postgres": {
            "available": bool(_get_opendata_postgres_source()),
            "source": "OpenData PostgreSQL cadastral_queries",
            "note": "Latest successful cadastral property/prospect records matched by municipality, sheet and parcel",
        },
        "pvp_modelview_postgres": {
            "available": bool(_get_pvp_postgres_source()),
            "source": "PVP modelview PostgreSQL",
            "note": "Auction candidates matched by municipality, sheet and parcel; one-to-many records are retained",
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
            "available": omi_db_available_public(),
        },
        "omi_zone_boundaries": {
            "available": zone_boundaries_available(),
            "path": str(_omi_zones_dir()),
        },
        "mef_irpef": {
            "available": mef_db_available_public(),
        },
        "hazards_seismic": {
            "available": seismic_db_available(_hazards_sqlite_path()),
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

    # Keep the existing dataset-specific keys (path, note, categories) while
    # making the status contract explicit.  Timestamps remain null until the
    # upstream store exposes publication metadata; the adapter must not invent
    # freshness values from a local file mtime.
    for dataset_name, dataset_status in status.items():
        dataset_status.setdefault("source", "aecs4u-stats")
        dataset_status.setdefault("dataset", dataset_name)
        dataset_status.setdefault("source_version", None)
        dataset_status.setdefault("freshness", {})

    return status


@lru_cache(maxsize=1)
def _istat_engine():
    """Lazily created read-only SQLModel engine over the ISTAT store."""
    from sqlmodel import create_engine

    return create_engine(f"sqlite:///{_istat_sqlite_path()}", echo=False)


def _municipality_sqlite_profile(cadastral_code: str) -> Optional[Dict[str, Any]]:
    """Read the normalized municipality profile directly from the active DB.

    This complements SQLModel for legacy/shared SQLite snapshots where the
    ORM query can return the hierarchy but omit newly added nullable columns.
    """
    try:
        import sqlite3

        connection = sqlite3.connect(f"file:{_istat_sqlite_path()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            municipality = connection.execute(
                "SELECT * FROM municipalities WHERE UPPER(cadastral_code) = ? LIMIT 1",
                (cadastral_code.strip().upper(),),
            ).fetchone()
            if municipality is None:
                return None
            province = connection.execute(
                "SELECT * FROM provinces WHERE id = ?", (municipality["province_id"],)
            ).fetchone()
            region = connection.execute(
                "SELECT * FROM regions WHERE id = ?", (municipality["region_id"],)
            ).fetchone()
            population = connection.execute(
                "SELECT year, resident_population FROM municipality_population_stats "
                "WHERE municipality_id = ? ORDER BY year DESC",
                (municipality["id"],),
            ).fetchall()
        finally:
            connection.close()
        return {
            "cadastral_code": municipality["cadastral_code"],
            "istat_code": municipality["alphanumeric_code"],
            "official_name": municipality["official_name"],
            "procom": municipality["id"],
            "name": municipality["italian_name"] or municipality["official_name"],
            "province": province["name"] if province else None,
            "province_sigla": province["vehicle_code"] if province else None,
            "region": region["name"] if region else None,
            "nuts3_2021": province["nuts3_2021"] if province else None,
            "nuts3": province["nuts3_2024"] if province else None,
            "is_provincial_capital": bool(municipality["is_provincial_capital"]),
            "latitude": municipality["latitude"],
            "longitude": municipality["longitude"],
            "postal_code": municipality["postal_code"],
            "tax_code": municipality["tax_code"],
            "email": municipality["email"],
            "pec_email": municipality["pec_email"],
            "website": municipality["website"],
            "wikipedia_url": municipality["wikipedia_url"],
            "wikidata_url": municipality["wikidata_url"],
            "coat_of_arms_url": municipality["coat_of_arms_url"],
            "population": {
                "year": population[0]["year"],
                "resident_population": population[0]["resident_population"],
            } if population else None,
            "population_history": [
                {"year": row["year"], "resident_population": row["resident_population"]}
                for row in reversed(population)
            ],
            "source": "ISTAT via aecs4u-stats",
        }
    except (OSError, KeyError, sqlite3.Error):
        return None


def get_municipality_by_cadastral_code(
    cadastral_code: str,
    *,
    use_postgres: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Municipality profile for a catasto comune code (e.g. ``C773`` → Civitavecchia).

    The cadastral code is the join key between Agenzia delle Entrate parcel
    attributes (``ADMINISTRATIVEUNIT`` / NATIONALCADASTRALREFERENCE prefix) and
    the ISTAT administrative hierarchy.

    Returns ``None`` when the store is missing or the code is unknown.
    """
    postgres_source = _get_postgres_stats_source() if use_postgres else None
    if postgres_source is not None and postgres_source.available():
        try:
            result = _call_with_hard_timeout(
                postgres_source.municipality_by_cadastral_code,
                6,
                cadastral_code,
            )
            if result is not None:
                return result
        except Exception:
            postgres_source._retry_at = time.monotonic() + 60
            logger.warning(
                "Postgres municipality lookup failed; using local ISTAT fallback",
                exc_info=True,
            )

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
            result = {
                "cadastral_code": muni.cadastral_code,
                "istat_code": muni.alphanumeric_code,
                "official_name": muni.official_name,
                "procom": muni.id,  # numeric national comune code — join key for census sections
                "name": muni.italian_name or muni.official_name,
                "province": province.name if province else None,
                "province_sigla": province.vehicle_code if province else None,
                "region": region.name if region else None,
                "nuts3_2021": province.nuts3_2021 if province else None,
                "nuts3": province.nuts3_2024 if province else None,
                "is_provincial_capital": muni.is_provincial_capital,
                "latitude": muni.latitude,
                "longitude": muni.longitude,
                "postal_code": muni.postal_code,
                "tax_code": muni.tax_code,
                "email": muni.email,
                "pec_email": muni.pec_email,
                "website": muni.website,
                "wikipedia_url": muni.wikipedia_url,
                "wikidata_url": muni.wikidata_url,
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
            direct = _municipality_sqlite_profile(code)
            if direct:
                for key, value in direct.items():
                    if result.get(key) in (None, [], "") and value not in (None, [], ""):
                        result[key] = value
            return result
    except Exception as e:  # pragma: no cover - defensive: store may be partial
        logger.warning("ISTAT municipality lookup failed for %s: %s", code, e)
        return None


def get_buildings_for_parcel(
    national_reference: str,
    cadastral_code: Optional[str] = None,
    municipality: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return cadastral building types cached by the sister application.

    ``category`` is the Agenzia delle Entrate cadastral building category
    extracted from SISTER's ``BuildingClassification`` model (for example
    A/2, C/6 or D/1).  An empty result is a valid response when no building
    visura has yet been cached for the selected parcel.
    """
    source = _get_sister_building_source()
    if source is None or not source.available():
        return {
            "buildings": [],
            "count": 0,
            "available": False,
            "source": "SISTER SQLite",
        }
    reference = str(national_reference or "").strip()
    code = (cadastral_code or reference.split("_", 1)[0]).strip().upper()
    if municipality is None:
        municipality = get_municipality_by_cadastral_code(code)
    try:
        return source.buildings_for_parcel(reference, code, municipality)
    except Exception as exc:  # pragma: no cover - defensive optional source
        logger.warning("SISTER building lookup failed for %s: %s", reference, exc)
        return {
            "buildings": [],
            "count": 0,
            "available": False,
            "source": "SISTER SQLite",
        }


def get_pois_near(
    lat: float,
    lng: float,
    radius_km: float = 1.0,
    categories: Optional[List[str]] = None,
    *,
    use_postgres: bool = False,
) -> Dict[str, Any]:
    """
    POIs around a point, grouped by category with ``distance_km``, nearest-first.

    Prefers the aecs4u-stats PostGIS ``facts.poi`` table when configured
    (``AECS4U_STATS_POSTGRES_DSN``), falling back to the local OSM POI
    SQLite store, and finally to an empty result when neither is available.
    """
    postgres_source = _get_postgres_poi_source() if use_postgres else None
    if postgres_source is not None and postgres_source.available():
        try:
            # Bound the *entire* attempt (connect + query), not just pool
            # construction — a crashing/hanging query is just as capable of
            # never returning as a hanging connect() on this host.
            grouped = _call_with_hard_timeout(
                postgres_source.pois_near, 6, lat, lng, radius_km=radius_km, categories=categories
            )
            return {
                "center": {"lat": lat, "lng": lng},
                "radius_km": radius_km,
                "total": sum(len(v) for v in grouped.values()),
                "categories": grouped,
                "source": "OpenStreetMap via aecs4u-stats (PostGIS)",
            }
        except Exception:
            postgres_source._retry_at = time.monotonic() + 60
            logger.warning("Postgres POI query failed; falling back to local SQLite store", exc_info=True)

    grouped = pois_within_radius(lat, lng, radius_km=radius_km, categories=categories)
    return {
        "center": {"lat": lat, "lng": lng},
        "radius_km": radius_km,
        "total": sum(len(v) for v in grouped.values()),
        "categories": grouped,
        "source": "OpenStreetMap via aecs4u-stats",
    }


def _latest_omi_semester(db_path: Path) -> Optional[tuple[int, int]]:
    """Read the latest QI semester from the compact import log.

    The quote table is multi-gigabyte on the shared volume and its historical
    ``latest_semester`` query has to sort the full table. The import log holds
    the same publication marker in a tiny indexed-by-rowid table.
    """
    try:
        import sqlite3

        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            entries = connection.execute(
                "SELECT csv_entry FROM omi_import_log "
                "WHERE target_table = 'quotazioni_valori'"
            ).fetchall()
        finally:
            connection.close()
        semesters = []
        for (entry,) in entries:
            match = re.search(
                r"QI_(?:\d+_\d+_)?(\d{4})([12])_(?:VALORI|ZONE)\.csv$",
                str(entry or ""),
            )
            if match:
                semesters.append((int(match.group(1)), int(match.group(2))))
        return max(semesters) if semesters else None
    except (OSError, sqlite3.Error):
        return None


def _omi_istat_keys(comune: str) -> list[int]:
    """Resolve a comune to the indexed, region-prefixed OMI ISTAT key."""
    value = str(comune or "").strip().upper()
    if not value:
        return []
    try:
        import sqlite3

        connection = sqlite3.connect(f"file:{_istat_sqlite_path()}?mode=ro", uri=True)
        try:
            if value[0].isalpha():
                row = connection.execute(
                    "SELECT alphanumeric_code, region_id FROM municipalities "
                    "WHERE UPPER(cadastral_code) = ? LIMIT 1",
                    (value,),
                ).fetchone()
            else:
                numeric = value.lstrip("0") or "0"
                row = connection.execute(
                    "SELECT alphanumeric_code, region_id FROM municipalities "
                    "WHERE CAST(alphanumeric_code AS INTEGER) = CAST(? AS INTEGER) "
                    "LIMIT 1",
                    (numeric,),
                ).fetchone()
        finally:
            connection.close()
        if row and row[0] is not None and row[1] is not None:
            # AdE stores the region code followed by the five-digit ISTAT
            # municipality code (Cinisi: 19 + 082031).
            return [int(f"{int(row[1]):02d}{int(row[0]):06d}")]
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return []
    return []


def _fast_omi_quotes(
    comune: str,
    *,
    zona: Optional[str] = None,
    anno: Optional[int] = None,
    semestre: Optional[int] = None,
    db_path: Optional[Path] = None,
) -> Optional[list[dict]]:
    """Read OMI rows through the existing ``cod_comune_istat`` index.

    The upstream compatibility query applies ``UPPER``/``CAST`` to columns
    and therefore scans the multi-gigabyte table. The imported ISTAT store
    provides the exact region-prefixed key needed for the covering index.
    ``None`` means the key could not be resolved and lets callers retain the
    upstream fallback for legacy stores.
    """
    keys = _omi_istat_keys(comune)
    if not keys:
        return None
    db_path = db_path or _omi_sqlite_path()
    latest = (anno, semestre) if anno is not None and semestre is not None else _latest_omi_semester(db_path)
    if latest is None:
        return []
    anno, semestre = latest
    try:
        import sqlite3

        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        placeholders = ",".join("?" for _ in keys)
        sql = (
            f"SELECT * FROM quotazioni_valori WHERE cod_comune_istat IN ({placeholders}) "
            "AND anno = ? AND semestre = ?"
        )
        params: list[Any] = [*keys, anno, semestre]
        if zona:
            sql += " AND UPPER(zona) = ?"
            params.append(zona.strip().upper())
        try:
            rows = connection.execute(sql, params).fetchall()
        finally:
            connection.close()
        return [dict(row) for row in rows]
    except (OSError, sqlite3.Error):
        return []


def get_omi_quotes(
    comune: str, zona: Optional[str] = None, *, use_postgres: bool = False
) -> Dict[str, Any]:
    """
    OMI (Osservatorio Mercato Immobiliare) quotes for a comune's latest
    semester: per-zone/typology sale + rent €/m² ranges.

    ``comune`` accepts either the catasto code (e.g. ``C773``) or the ISTAT
    numeric code. Pass ``zona`` (e.g. ``B1``) to filter to one OMI zone.
    """
    postgres_source = _get_postgres_stats_source() if use_postgres else None
    if postgres_source is not None and postgres_source.available():
        try:
            result = _call_with_hard_timeout(
                postgres_source.omi_quotes_by_cadastral_code,
                8,
                comune,
            )
            if zona:
                result["quotes"] = [
                    quote for quote in result.get("quotes", [])
                    if str(quote.get("zona", "")).upper() == zona.strip().upper()
                ]
            return result
        except Exception:
            postgres_source._retry_at = time.monotonic() + 60
            logger.warning(
                "Postgres OMI lookup failed; using local OMI fallback",
                exc_info=True,
            )

    db_path = _omi_sqlite_path()
    latest = _latest_omi_semester(db_path)
    kwargs = {"db_path": db_path}
    if latest:
        kwargs.update({"anno": latest[0], "semestre": latest[1]})
    rows = _fast_omi_quotes(comune, zona=zona, **kwargs)
    if rows is None:
        rows = quotes_for_comune(comune, zona=zona, **kwargs)
    return {
        "comune": comune,
        "zona": zona,
        "quotes": rows,
        "source": "Agenzia delle Entrate OMI via aecs4u-stats",
    }


def get_omi_history(comune: str, zona: str, cod_tipologia: Optional[str] = None) -> Dict[str, Any]:
    """Full semester history (oldest-first) of OMI quotes for one comune/zone."""
    keys = _omi_istat_keys(comune)
    rows = []
    if keys:
        try:
            import sqlite3

            connection = sqlite3.connect(f"file:{_omi_sqlite_path()}?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            placeholders = ",".join("?" for _ in keys)
            sql = (
                "SELECT anno, semestre, zona, cod_tipologia, tipologia, stato_conservazione, "
                "prezzo_min, prezzo_max, locazione_min, locazione_max "
                f"FROM quotazioni_valori WHERE cod_comune_istat IN ({placeholders}) AND UPPER(zona) = ?"
            )
            params: list[Any] = [*keys, zona.strip().upper()]
            if cod_tipologia is not None:
                sql += " AND CAST(cod_tipologia AS TEXT) = ?"
                params.append(str(cod_tipologia))
            sql += " ORDER BY anno, semestre"
            try:
                rows = [dict(row) for row in connection.execute(sql, params).fetchall()]
            finally:
                connection.close()
        except (OSError, sqlite3.Error):
            rows = []
    else:
        rows = quote_history(comune, zona, cod_tipologia=cod_tipologia, db_path=_omi_sqlite_path())
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
    db_path = _omi_sqlite_path()
    latest = _latest_omi_semester(db_path)
    kwargs = {"db_path": db_path}
    if latest:
        kwargs.update({"anno": latest[0], "semestre": latest[1]})
    rows = _fast_omi_quotes(comune, zona=zona, **kwargs)
    if rows is None:
        rows = quotes_for_comune(comune, zona=zona, **kwargs)
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
    return zone_boundaries(province, zones_dir=_omi_zones_dir())


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


def get_income_profile(
    cadastral_code: str, year: Optional[int] = None, *, use_postgres: bool = False
) -> Optional[Dict[str, Any]]:
    """
    MEF/IRPEF income profile for a comune by catasto code: taxpayer count,
    mean taxable income, and the 8-bracket income distribution.
    """
    postgres_source = _get_postgres_stats_source() if use_postgres else None
    if postgres_source is not None and postgres_source.available():
        try:
            result = _call_with_hard_timeout(
                postgres_source.income_profile_by_cadastral_code,
                8,
                cadastral_code,
            )
            if result is not None and (year is None or result.get("year") == year):
                return result
        except Exception:
            postgres_source._retry_at = time.monotonic() + 60
            logger.warning(
                "Postgres IRPEF lookup failed; using local MEF fallback",
                exc_info=True,
            )
    return income_by_cadastral_code(cadastral_code, year=year, db_path=_mef_sqlite_path())


def get_environmental_risks(istat_code: int | str) -> Optional[Dict[str, Any]]:
    """
    Environmental risk profile for a comune (ISTAT code): DPC seismic zone
    (1-4, local store) plus ISPRA IdroGEO flood/landslide indicators (live
    API). Either half may be ``None`` if its source is unavailable.
    """
    seismic = seismic_zone(istat_code, db_path=_hazards_sqlite_path())
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
    """True when the package store or the provisioned INSPIRE FGB source exists."""
    if cadastral_db_available():
        return True
    for root in _cadastral_fgb_roots():
        try:
            if root.is_dir() and next(root.glob("*/*/*/*_ple.fgb"), None) is not None:
                return True
        except OSError:
            continue
    return False


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
    # Avoid entering the DuckDB adapter when its optional per-region store is
    # absent; its connection setup may attempt to load the spatial extension.
    result = parcel_by_reference(national_reference) if cadastral_db_available() else None
    return result if result is not None else _local_parcel_by_reference(national_reference)


def _local_parcel_by_reference(national_reference: str) -> Optional[Dict[str, Any]]:
    """Read a parcel from the provisioned INSPIRE GeoPackage when no DuckDB
    cadastral store has been built yet.

    This is intentionally a cold-path fallback: the read model stores the
    resulting feature, so subsequent panel requests do not reopen the source.
    """
    reference = str(national_reference).strip()
    code = reference.split("_", 1)[0].upper() if "_" in reference else ""
    if not code:
        return None

    def _fgb_candidates() -> list[Path]:
        """Return municipal FGB candidates without recursively walking the tree."""
        configured_dirs = (
            os.getenv("SPATIALITE_FGB_DIRECTORY"),
            os.getenv("FGB_DIRECTORY"),
            os.getenv("CADASTRAL_DATA_DIR"),
        )
        fgb_roots = []
        for configured_dir in configured_dirs:
            if configured_dir:
                root = Path(configured_dir).expanduser()
                if root.name.upper() == "ITALIA":
                    fgb_roots.append(root)
                elif (root / "ITALIA").is_dir():
                    fgb_roots.append(root / "ITALIA")
                else:
                    fgb_roots.append(root)
        # The application container mounts the shared data volume at /data;
        # the historical developer default under /mnt/mobile is not present
        # in that deployment.
        fgb_roots.extend((Path("/data/catasto/ITALIA"), Path("/data/catasto")))
        try:
            from land_registry.config import spatialite_settings

            configured_root = Path(spatialite_settings.fgb_directory).expanduser()
            if configured_root.name.upper() == "ITALIA":
                fgb_roots.append(configured_root)
            elif (configured_root / "ITALIA").is_dir():
                fgb_roots.append(configured_root / "ITALIA")
            else:
                fgb_roots.append(configured_root)
        except (ImportError, OSError, TypeError):
            pass

        candidates = []
        seen = set()

        # Use the ISTAT hierarchy to jump directly to REGION/PROVINCE. This
        # avoids scanning every municipality directory on the shared volume.
        hierarchy = None
        try:
            hierarchy = get_municipality_by_cadastral_code(code)
        except Exception:  # the cadastral fallback must work without ISTAT
            hierarchy = None
        region_name = str((hierarchy or {}).get("region") or "").strip().upper()
        province_code = str((hierarchy or {}).get("province_sigla") or "").strip().upper()

        def _add_from_province_dir(province_dir: Path) -> None:
            if not province_dir.is_dir():
                return
            try:
                for path in province_dir.glob(f"*/{code}_*_ple.fgb"):
                    if path not in seen:
                        seen.add(path)
                        candidates.append(path)
            except OSError:
                return

        for root in fgb_roots:
            if not root.is_dir():
                continue
            bases = [root]
            if root.name.upper() != "ITALIA":
                bases.append(root / "ITALIA")
            for base in bases:
                if region_name and province_code:
                    _add_from_province_dir(base / region_name / province_code)
                if candidates:
                    break
            if candidates:
                break

        if candidates:
            return candidates

        # ISTAT may be absent or use a different directory spelling. The
        # fallback only enumerates the three known directory levels; it never
        # performs a recursive glob over the full cadastral volume.
        for root in fgb_roots:
            if not root.is_dir():
                continue
            base = root if root.name.upper() == "ITALIA" else root / "ITALIA"
            try:
                for region_dir in base.iterdir():
                    if not region_dir.is_dir():
                        continue
                    for province_dir in region_dir.iterdir():
                        _add_from_province_dir(province_dir)
                        if candidates:
                            break
                    if candidates:
                        break
            except OSError:
                continue
            if candidates:
                break
        return candidates

    # FlatGeobuf is the provisioned cadastral source for the map. Check it
    # before the legacy GeoPackage tree: the latter can contain millions of
    # files and its fallback discovery is not an acceptable request path.
    try:
        import pyogrio
        from shapely.geometry import mapping

        escaped = reference.replace("'", "''")
        for path in _fgb_candidates():
            for column in ("NATIONALCADASTRALREFERENCE", "national_cadastral_reference"):
                try:
                    frame = pyogrio.read_dataframe(
                        path,
                        where=f"{column} = '{escaped}'",
                        use_arrow=False,
                    )
                except Exception:  # a different FGB schema/file must not break the panel
                    continue
                if frame is None or frame.empty:
                    continue
                row = frame.iloc[0]
                properties = {
                    key: (value.item() if hasattr(value, "item") else value)
                    for key, value in row.to_dict().items()
                    if key != "geometry"
                }
                geometry = row.get("geometry")
                return {
                    "type": "Feature",
                    "properties": properties,
                    "geometry": mapping(geometry) if geometry is not None else None,
                }
    except (ImportError, OSError, ValueError):
        logger.debug("FlatGeobuf parcel fallback is unavailable", exc_info=True)

    roots = []
    configured = os.getenv("CADASTRAL_DATA_DIR")
    if configured:
        configured_root = Path(configured)
        roots.append(configured_root / "ITALIA" if (configured_root / "ITALIA").is_dir() else configured_root)
    roots.extend((Path("/data/catasto/ITALIA"), Path("/data/catasto")))
    paths = []
    seen = set()
    for root in roots:
        if not root.is_dir():
            continue
        # Shared cadastral volume layout is ITALIA/REGIONE/PROVINCIA/COMUNE.
        direct_paths = list(root.glob(f"*/*/*/{code}_*_ple.gpkg"))
        for path in direct_paths:
            if path not in seen:
                seen.add(path)
                paths.append(path)
        if paths:
            break
        for path in root.rglob(f"{code}_*_ple.gpkg"):
            if path not in seen:
                seen.add(path)
                paths.append(path)
        if paths:
            break
    try:
        import sqlite3
        from shapely import wkb
        from shapely.geometry import mapping

        # GeoPackages are SQLite containers. Query the indexed/ordinary
        # attribute row directly instead of asking pyogrio to materialize the
        # entire feature layer; this keeps a parcel-keyed cold build sub-second
        # even for large regional extracts.
        for path in paths:
            try:
                connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
                connection.row_factory = sqlite3.Row
                try:
                    tables = connection.execute(
                        "SELECT table_name FROM gpkg_contents WHERE data_type = 'features'"
                    ).fetchall()
                    for table_row in tables:
                        table = str(table_row[0]).replace('"', '""')
                        row = connection.execute(
                            f'SELECT * FROM "{table}" WHERE NATIONALCADASTRALREFERENCE = ? LIMIT 1',
                            (reference,),
                        ).fetchone()
                        if row is None:
                            continue
                        properties = {key: row[key] for key in row.keys() if key != "geom"}
                        blob = row["geom"]
                        flags = blob[3] if blob and len(blob) >= 8 else 0
                        envelope_type = (flags >> 1) & 0x07
                        envelope_sizes = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}
                        geometry = wkb.loads(bytes(blob)[8 + envelope_sizes.get(envelope_type, 0):])
                        return {
                            "type": "Feature",
                            "properties": properties,
                            "geometry": mapping(geometry),
                        }
                finally:
                    connection.close()
            except (OSError, sqlite3.Error, TypeError, ValueError):
                continue

        import fiona

        for path in paths:
            try:
                with fiona.open(path) as source:
                    for item in source:
                        properties = dict(item.get("properties") or {})
                        candidate = (
                            properties.get("NATIONALCADASTRALREFERENCE")
                            or properties.get("national_cadastral_reference")
                        )
                        if str(candidate or "").strip() != reference:
                            continue
                        return json.loads(json.dumps({
                            "type": "Feature",
                            "properties": properties,
                            "geometry": item.get("geometry"),
                        }))
            except (OSError, ValueError):
                continue
    except ImportError:
        try:
            import pyogrio
            for path in paths:
                try:
                    escaped = reference.replace("'", "''")
                    frame = pyogrio.read_dataframe(
                        path,
                        where=f"NATIONALCADASTRALREFERENCE = '{escaped}'",
                        use_arrow=False,
                    )
                    if frame.empty:
                        continue
                    row = frame.iloc[0]
                    properties = {
                        key: (value.item() if hasattr(value, "item") else value)
                        for key, value in row.to_dict().items()
                        if key != "geometry"
                    }
                    geometry = row.get("geometry")
                    return {
                        "type": "Feature",
                        "properties": properties,
                        "geometry": mapping(geometry) if geometry is not None else None,
                    }
                except (OSError, ValueError):
                    continue
        except ImportError:
            logger.warning("Neither Fiona nor pyogrio is available; local cadastral fallback is disabled")

    return None


def _cadastral_fgb_roots() -> list[Path]:
    """Return candidate roots for the shared municipal INSPIRE FGB files."""
    configured_dirs = (
        os.getenv("SPATIALITE_FGB_DIRECTORY"),
        os.getenv("FGB_DIRECTORY"),
        os.getenv("CADASTRAL_DATA_DIR"),
    )
    roots: list[Path] = []
    for configured_dir in configured_dirs:
        if not configured_dir:
            continue
        root = Path(configured_dir).expanduser()
        roots.append(root if root.name.upper() == "ITALIA" else root / "ITALIA" if (root / "ITALIA").is_dir() else root)
    roots.extend((Path("/data/catasto/ITALIA"), Path("/data/catasto")))
    try:
        from land_registry.config import spatialite_settings

        configured_root = Path(spatialite_settings.fgb_directory).expanduser()
        roots.append(
            configured_root
            if configured_root.name.upper() == "ITALIA"
            else configured_root / "ITALIA" if (configured_root / "ITALIA").is_dir() else configured_root
        )
    except (ImportError, OSError, TypeError, AttributeError):
        pass
    result = []
    seen = set()
    for root in roots:
        if root not in seen:
            seen.add(root)
            result.append(root)
    return result


def _cadastral_code_at_point(lat: float, lng: float) -> Optional[str]:
    """Resolve a point to a cadastral municipality using ISTAT boundaries."""
    boundary_path = Path("/data/istat/istat_boundaries.IT.duckdb")
    if not boundary_path.is_file():
        return None
    try:
        import duckdb

        connection = duckdb.connect(str(boundary_path), read_only=True)
        try:
            connection.execute("LOAD spatial")
            row = connection.execute(
                """
                SELECT pro_com
                FROM comuni
                WHERE ST_Covers(geom, ST_Point(?, ?))
                LIMIT 1
                """,
                [float(lng), float(lat)],
            ).fetchone()
        finally:
            connection.close()
        if not row:
            return None

        connection = sqlite3.connect(f"file:{_istat_sqlite_path()}?mode=ro", uri=True)
        try:
            result = connection.execute(
                "SELECT cadastral_code FROM municipalities WHERE id = ? LIMIT 1",
                (int(row[0]),),
            ).fetchone()
        finally:
            connection.close()
        return str(result[0]).strip().upper() if result else None
    except Exception:
        logger.debug("Could not resolve cadastral municipality at %.6f, %.6f", lat, lng, exc_info=True)
        return None


def _local_parcel_at_point(lat: float, lng: float) -> Optional[Dict[str, Any]]:
    """Find a parcel in the provisioned municipal FGB source."""
    code = _cadastral_code_at_point(lat, lng)
    if not code:
        return None
    municipality = get_municipality_by_cadastral_code(code, use_postgres=False)
    region = str((municipality or {}).get("region") or "").strip().upper()
    province = str((municipality or {}).get("province_sigla") or "").strip().upper()
    if not region or not province:
        return None

    try:
        import pyogrio
        from shapely.geometry import mapping

        point = Point(float(lng), float(lat))
        for root in _cadastral_fgb_roots():
            province_dir = root / region / province
            if not province_dir.is_dir():
                continue
            paths = sorted(province_dir.glob(f"*/{code}_*_ple.fgb"))
            for path in paths:
                # The municipal files have a spatial index, so this reads only
                # the small candidate window around the requested point.
                frame = pyogrio.read_dataframe(
                    path,
                    bbox=(lng - 1e-5, lat - 1e-5, lng + 1e-5, lat + 1e-5),
                    use_arrow=False,
                )
                if frame is None or frame.empty:
                    continue
                hits = frame[frame.geometry.covers(point)]
                if hits.empty:
                    continue
                row = hits.iloc[0]
                properties = {
                    key: (value.item() if hasattr(value, "item") else value)
                    for key, value in row.to_dict().items()
                    if key != "geometry"
                }
                geometry = row.get("geometry")
                return {
                    "type": "Feature",
                    "properties": properties,
                    "geometry": mapping(geometry) if geometry is not None else None,
                }
    except (ImportError, OSError, ValueError, TypeError):
        logger.warning("Local FGB parcel lookup failed at %.6f, %.6f", lat, lng, exc_info=True)
    return None


def get_parcel_at_point(lat: float, lng: float) -> Optional[Dict[str, Any]]:
    """The parcel Feature containing a WGS84 point, or ``None``."""
    if cadastral_db_available():
        result = parcel_at_point(lat, lng)
        if result is not None:
            return result
    return _local_parcel_at_point(lat, lng)


def _parcel_reference(feature: Optional[Dict[str, Any]]) -> Optional[str]:
    """Read the canonical parcel key from a GeoJSON feature."""
    if not feature:
        return None
    props = feature.get("properties") or {}
    value = (
        props.get("national_cadastral_reference")
        or props.get("NATIONALCADASTRALREFERENCE")
        or props.get("national_reference")
    )
    return str(value).strip() if value else None


def _parcel_centroid(feature: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """Return a WGS84 centroid from the parcel GeoJSON geometry."""
    geometry = feature.get("geometry")
    if not geometry:
        return None
    try:
        centroid = shape(geometry).centroid
        return {"lat": float(centroid.y), "lng": float(centroid.x)}
    except (TypeError, ValueError, AttributeError):
        return None


def _parcel_enrichment_fingerprint() -> str:
    """Fingerprint source snapshots without reading the large data files."""
    paths = (_istat_sqlite_path(), _census_store_path(), _omi_sqlite_path())
    parts = []
    for path in paths:
        if path is None:
            parts.append("-")
            continue
        try:
            stat = Path(path).stat()
            parts.append(f"{path}:{stat.st_size}:{stat.st_mtime_ns}")
        except OSError:
            parts.append(f"{path}:-")
    postgres_dsn = _postgres_stats_dsn()
    postgres_marker = hashlib.sha256(postgres_dsn.encode("utf-8")).hexdigest() if postgres_dsn else "-"
    sister_path = _sister_db_path()
    if sister_path is None:
        sister_marker = "-"
    else:
        try:
            sister_stat = sister_path.stat()
            sister_marker = f"{sister_path}:{sister_stat.st_size}:{sister_stat.st_mtime_ns}"
        except OSError:
            sister_marker = f"{sister_path}:-"
    external_dsns = (
        _external_postgres_dsn(
            "AECS4U_OPENDATA_POSTGRES_DSN", "OPENDATA_POSTGRES_DSN",
            "OPENDATA_DATABASE_URL", "PROD_DATABASE_URL",
            sibling_env=(Path(__file__).resolve().parents[2] / "opendata" / ".env", "PROD_DATABASE_URL"),
        ),
        _external_postgres_dsn(
            "AECS4U_PVP_MODELVIEW_POSTGRES_DSN", "PVP_MODELVIEW_POSTGRES_DSN",
            "PVP_MODELVIEW_DATABASE_URL", "PROD_MODELVIEW_DATABASE_URL_PVP",
            sibling_env=(Path(__file__).resolve().parents[2] / "property-scraper" / ".env", "PROD_MODELVIEW_DATABASE_URL_PVP"),
        ),
    )
    external_markers = [
        hashlib.sha256(value.encode("utf-8")).hexdigest() if value else "-"
        for value in external_dsns
    ]
    material = "parcel-read-model-v5|" + "|".join(parts) + "|postgres:" + postgres_marker + "|sister:" + sister_marker + "|external:" + "|".join(external_markers)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _census_section_for_parcel(
    point: Optional[Dict[str, float]],
    municipality: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Resolve a parcel centroid against its municipality's census sections.

    The provisioned 2021 store contains Sicily geometry in UTM 32N while the
    upstream helper currently assumes that the geometry is WGS84. Restricting
    the read to one ``PROCOM`` also avoids scanning the national store.
    """
    if not point or not municipality or municipality.get("procom") is None:
        return None
    store_path = _census_store_path()
    if store_path is None:
        return None
    try:
        import duckdb
        from pyproj import Transformer
        from shapely import wkb

        connection = duckdb.connect(str(store_path), read_only=True)
        try:
            columns = [row[0] for row in connection.execute("DESCRIBE sections").fetchall()]
            geometry_index = columns.index("geom")
            rows = connection.execute(
                "SELECT * FROM sections WHERE procom = ?",
                [int(municipality["procom"])],
            ).fetchall()
        finally:
            connection.close()

        # Sicily's provisioned census geometry is EPSG:32632. Keep the second
        # candidate for stores generated from the neighbouring UTM zone.
        projected_points = []
        for epsg in (32632, 32633):
            transformer = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
            projected_points.append((epsg, Point(*transformer.transform(point["lng"], point["lat"]))))

        for row in rows:
            geometry = wkb.loads(bytes(row[geometry_index]))
            projected_point = next(
                (candidate for epsg, candidate in projected_points if geometry.covers(candidate)),
                None,
            )
            if projected_point is None:
                continue
            props = {}
            for name, value in zip(columns, row, strict=True):
                if name == "geom":
                    continue
                if hasattr(value, "item"):
                    value = value.item()
                props[name] = value
            props["ratios"] = {
                "education_tertiary_rate": round(props["p90"] / props["p83"], 4)
                if props.get("p90") is not None and props.get("p83") not in (None, 0) else None,
                "employment_rate_working_age": round(
                    props["p101"] / sum(props.get(name) or 0 for name in (
                        "p17", "p18", "p19", "p20", "p21", "p22",
                        "p23", "p24", "p25", "p26",
                    )),
                    4,
                ) if props.get("p101") is not None and sum(
                    props.get(name) or 0 for name in (
                        "p17", "p18", "p19", "p20", "p21", "p22",
                        "p23", "p24", "p25", "p26",
                    )
                ) else None,
                "foreign_resident_share": round(props["st1"] / props["p1"], 4)
                if props.get("st1") is not None and props.get("p1") not in (None, 0) else None,
                "vacancy_rate": round(props["a3"] / props["a8"], 4)
                if props.get("a3") is not None and props.get("a8") not in (None, 0) else None,
                "avg_household_size": round(props["p1"] / props["pf1"], 4)
                if props.get("p1") is not None and props.get("pf1") not in (None, 0) else None,
            }
            return {"type": "Feature", "properties": props, "geometry": None}
    except Exception as exc:  # pragma: no cover - optional store/CRS fallback
        logger.warning("Parcel census lookup failed: %s", exc)
    return None


_MISSING = object()


def _build_parcel_enrichment(
    national_reference: str,
    *,
    municipality_override: Any = _MISSING,
    omi_override: Any = _MISSING,
    income_override: Any = _MISSING,
    postgres_context_override: Any = None,
) -> Optional[Dict[str, Any]]:
    """Build the stable portion of one parcel's read-optimized profile."""
    parcel = get_parcel_by_reference(national_reference)
    if parcel is None:
        return None

    props = parcel.get("properties") or {}
    cadastral_code = str(
        props.get("municipality_code")
        or props.get("ADMINISTRATIVEUNIT")
        or national_reference.split("_", 1)[0]
    ).strip().upper()
    municipality = (
        get_municipality_by_cadastral_code(cadastral_code)
        if municipality_override is _MISSING
        else municipality_override
    )
    point = _parcel_centroid(parcel)

    # Keep the independently indexed PostgreSQL lookups ahead of the optional
    # spatial context query. A slow census/hazard geometry query must not
    # prevent OMI and IRPEF values from reaching the panel.
    omi = get_omi_quotes(cadastral_code) if omi_override is _MISSING else omi_override
    income_profile = (
        get_income_profile(cadastral_code)
        if income_override is _MISSING
        else income_override
    )

    postgres_source = _get_postgres_stats_source()
    postgres_context = (
        None if postgres_context_override is _MISSING else postgres_context_override
    )
    if postgres_context_override is _MISSING and postgres_source is not None and postgres_source.available():
        postgres_context = postgres_source.context_for_parcel(national_reference, cadastral_code, point)

    # PostgreSQL is authoritative when its indexed municipality context is
    # available. The local ISTAT profile remains only a compatibility path
    # for deployments where the PostgreSQL source is disabled or offline.
    if postgres_context and postgres_context.get("municipality"):
        municipality = postgres_context["municipality"]

    buildings = get_buildings_for_parcel(
        national_reference,
        cadastral_code=cadastral_code,
        municipality=municipality,
    )
    # These are independent source systems. Keep their raw, source-labelled
    # records in the read model so a later panel load does not need to repeat
    # two remote database queries.
    opendata = get_opendata_for_parcel(national_reference, municipality)
    pvp = get_pvp_for_parcel(national_reference, municipality)

    omi_zone = None
    if point and municipality and municipality.get("province"):
        omi_zone = get_omi_zone_at_point(
            municipality["province"], point["lat"], point["lng"]
        )

    postgres_omi = (postgres_context or {}).get("omi")
    postgres_census = (postgres_context or {}).get("census")
    if postgres_omi and postgres_omi.get("omi_zone_key"):
        omi_zone = {
            "province": (municipality or {}).get("province_sigla"),
            "point": point,
            "matched": True,
            "zone": postgres_omi["omi_zone_key"],
            "source": "aecs4u-stats PostgreSQL spatial.market_zone",
        }
    census = postgres_census or _census_section_for_parcel(point, municipality)
    tax_facts = (postgres_context or {}).get("tax_facts") or []
    tax_years = [row.get("year") for row in tax_facts if row.get("year") is not None]
    latest_tax_year = max(tax_years) if tax_years else None
    latest_tax = [row for row in tax_facts if row.get("year") == latest_tax_year]
    tax_by_measure = {row.get("measure"): row for row in latest_tax}
    taxpayers = (tax_by_measure.get("imponibile") or {}).get("frequency")
    total_income = (tax_by_measure.get("imponibile") or {}).get("amount")
    economics = None
    if latest_tax:
        economics = {
            "tax_year": latest_tax_year,
            "taxpayers": taxpayers,
            "total_income": total_income,
            "average_income": round(float(total_income) / float(taxpayers), 2)
            if total_income is not None and taxpayers not in (None, 0) else None,
            "net_tax": (tax_by_measure.get("imposta_netta") or {}).get("amount"),
            "income_brackets": {
                row["measure"].removeprefix("bracket_"): row.get("frequency")
                for row in latest_tax
                if str(row.get("measure", "")).startswith("bracket_")
            },
            "source_facts": latest_tax,
        }
    elif income_profile:
        economics = {
            "tax_year": income_profile.get("year"),
            "taxpayers": income_profile.get("taxpayers"),
            "total_income": None,
            "average_income": income_profile.get("mean_taxable_income_eur"),
            "net_tax": None,
            "income_brackets": {
                str(row.get("bracket")): row.get("frequency")
                for row in income_profile.get("income_distribution", [])
            },
            "source_facts": income_profile.get("income_distribution", []),
        }
    blocks = {
        name: _detail_block()
        for name in _PARCEL_DETAIL_BLOCKS
    }
    blocks.update({
        "basic": _detail_block(
            parcel,
            source="Agenzia delle Entrate INSPIRE cadastral extract",
            match_method="parcel_reference",
        ),
        "cadastral": _detail_block(
            {
                "foglio": props.get("sheet_number") or props.get("foglio"),
                "sezione_urbana": props.get("urban_section") or props.get("sezione_urbana"),
                "comune_code": cadastral_code,
                "postal_code": (municipality or {}).get("postal_code")
                or (postgres_context or {}).get("postal_code"),
                "postgres_parcel_spine_available": bool(
                    (postgres_context or {}).get("parcel_spine_available")
                ),
            },
            source="Agenzia delle Entrate / ISTAT",
            match_method="parcel_reference",
        ),
        "population": _detail_block(
            census,
            source="ISTAT Permanent Census 2021 via aecs4u-stats PostgreSQL"
            if postgres_census else "ISTAT Basi Territoriali 2021",
            match_method="centroid",
        ),
        "demographics": _detail_block(
            census,
            source="ISTAT Permanent Census 2021 via aecs4u-stats PostgreSQL"
            if postgres_census else "ISTAT Basi Territoriali 2021",
            match_method="centroid",
        ),
        "economics": _detail_block(
            economics,
            source="MEF/IRPEF facts via aecs4u-stats PostgreSQL",
            match_method="municipality",
        ),
        "buildings": _detail_block(
            buildings.get("buildings"),
            source=buildings.get("source"),
            match_method="cadastral_reference",
            available=buildings.get("available") is True,
        ),
        "opendata": _detail_block(
            opendata.get("records"),
            source=opendata.get("source"),
            match_method=opendata.get("match_method"),
            available=opendata.get("available") is True,
        ),
        "pvp": _detail_block(
            pvp.get("records"),
            source=pvp.get("source"),
            match_method=pvp.get("match_method"),
            available=pvp.get("available") is True,
        ),
        "valuation": _detail_block(
            {
                "zone": omi_zone,
                "quotes": (omi or {}).get("quotes", []),
                "postgres_snapshot": postgres_omi,
            },
            source="Agenzia delle Entrate OMI via aecs4u-stats PostgreSQL"
            if postgres_omi else "Agenzia delle Entrate OMI",
            match_method="centroid",
        ),
    })
    return {
        "national_reference": national_reference,
        "parcel": parcel,
        "municipality": municipality,
        "centroid": point,
        "omi": omi,
        "omi_zone": omi_zone,
        "census": census,
        "buildings": buildings,
        "opendata": opendata,
        "pvp": pvp,
        "blocks": blocks,
        "postgres_context": postgres_context,
        "source": "aecs4u-stats PostgreSQL context with cadastral parcel fallback",
    }


def get_parcel_enrichment(
    national_reference: str,
    refresh: bool = False,
) -> Optional[Dict[str, Any]]:
    """Read or refresh one parcel-keyed materialized enrichment profile.

    The read model is stored in the aecs4u-stats PostgreSQL ``serving`` schema.
    A cold key is built once; subsequent panel loads only perform one indexed
    row lookup. The application SQLite database is not used by this path.
    """
    reference = str(national_reference).strip()
    if not reference:
        return None

    # Synchronous compatibility callers use the local fallback. FastAPI uses
    # ``aget_parcel_enrichment`` for the asyncpg-backed path below.
    postgres_source = None
    fingerprint = _parcel_enrichment_fingerprint()
    if postgres_source is not None and postgres_source.available() and not refresh:
        try:
            cached = postgres_source.get_read_model(reference)
            cached_meta = (cached or {}).get("read_model") or {}
            if cached is not None and cached_meta.get("source_fingerprint") == fingerprint:
                return cached
        except Exception:
            # A broken optional cache must not prevent the parcel itself and
            # local ISTAT/OMI stores from populating the details panel.
            postgres_source._retry_at = time.monotonic() + 60
            logger.warning("PostgreSQL parcel read-model unavailable; using local enrichment fallback", exc_info=True)

    payload = _build_parcel_enrichment(reference)
    if payload is None:
        return None
    cached_ok = False
    if postgres_source is not None and postgres_source.available():
        try:
            cached_ok = postgres_source.upsert_read_model(
                reference,
                payload,
                source_fingerprint=fingerprint,
            )
        except Exception:
            postgres_source._retry_at = time.monotonic() + 60
            logger.warning("Could not persist parcel enrichment read model", exc_info=True)
    payload["read_model"] = {
        "key": reference,
        "source_fingerprint": fingerprint,
        "cached": bool(cached_ok),
        "database": "aecs4u-stats PostgreSQL",
    }
    return payload


async def aget_parcel_enrichment(
    national_reference: str,
    refresh: bool = False,
) -> Optional[Dict[str, Any]]:
    """Async PostgreSQL-backed parcel enrichment path.

    Local cadastral/geospatial work remains off the event loop, while every
    optional PostgreSQL operation uses the shared asyncpg pool directly.
    """
    reference = str(national_reference).strip()
    if not reference:
        return None

    source = await _get_async_postgres_source()
    fingerprint = _parcel_enrichment_fingerprint()
    if source is not None and time.monotonic() >= source._retry_at and not refresh:
        try:
            cached = await source.get_read_model(reference)
            metadata = (cached or {}).get("read_model") or {}
            if cached is not None and metadata.get("source_fingerprint") == fingerprint:
                return cached
        except Exception:
            source._retry_at = time.monotonic() + 60
            logger.warning("Async PostgreSQL parcel read-model unavailable; rebuilding locally", exc_info=True)

    parcel = await asyncio.to_thread(get_parcel_by_reference, reference)
    if parcel is None:
        return None
    props = parcel.get("properties") or {}
    cadastral_code = str(
        props.get("municipality_code")
        or props.get("ADMINISTRATIVEUNIT")
        or reference.split("_", 1)[0]
    ).strip().upper()
    point = await asyncio.to_thread(_parcel_centroid, parcel)
    municipality_task = asyncio.create_task(aget_municipality_by_cadastral_code(cadastral_code))
    omi_task = asyncio.create_task(aget_omi_quotes(cadastral_code))
    income_task = asyncio.create_task(aget_income_profile(cadastral_code))
    municipality, omi, income_profile = await asyncio.gather(
        municipality_task, omi_task, income_task
    )

    postgres_context = None
    if source is not None and time.monotonic() >= source._retry_at:
        try:
            postgres_context = await asyncio.wait_for(
                source.context_for_parcel(reference, cadastral_code, point), timeout=12
            )
            if postgres_context and postgres_context.get("municipality"):
                postgres_context["municipality"] = {
                    **(municipality or {}),
                    **postgres_context["municipality"],
                }
                municipality = postgres_context["municipality"]
        except Exception:
            source._retry_at = time.monotonic() + 60
            logger.warning("Async PostgreSQL parcel context failed; using local fallback", exc_info=True)

    # Local cadastral/geospatial work is isolated from the event loop. Passing
    # the context override also prevents the legacy synchronous adapter from
    # being touched while the payload is assembled.
    payload = await asyncio.to_thread(
        _build_parcel_enrichment,
        reference,
        municipality_override=municipality,
        omi_override=omi,
        income_override=income_profile,
        postgres_context_override=postgres_context,
    )
    if payload is None:
        return None

    cached_ok = False
    if source is not None and time.monotonic() >= source._retry_at:
        try:
            cached_ok = await source.upsert_read_model(reference, payload, fingerprint)
        except Exception:
            source._retry_at = time.monotonic() + 60
            logger.warning("Could not persist async PostgreSQL parcel read model", exc_info=True)
    payload["read_model"] = {
        "key": reference,
        "source_fingerprint": fingerprint,
        "cached": bool(cached_ok),
        "database": "aecs4u-stats PostgreSQL via asyncpg",
    }
    return payload


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
    path = _census_store_path()
    if not path:
        return False
    try:
        if _census_db_available(path):
            return True
    except Exception:
        logger.debug("Installed census adapter could not inspect %s", path, exc_info=True)
    # Older aecs4u-stats wheels do not ship the census package, but the shared
    # volume can still contain the canonical DuckDB store.
    try:
        import duckdb

        connection = duckdb.connect(str(path), read_only=True)
        try:
            return bool(
                connection.execute(
                    "SELECT 1 FROM information_schema.tables WHERE table_name = 'sections' LIMIT 1"
                ).fetchone()
            )
        finally:
            connection.close()
    except Exception:
        logger.debug("Local census store is unreadable: %s", path, exc_info=True)
        return False


def _local_census_sections_for_comune(
    procom: int,
    limit: int = 5000,
) -> Optional[Dict[str, Any]]:
    """Read census sections directly from the shared DuckDB compatibility store."""
    path = _census_store_path()
    if path is None:
        return None
    try:
        import duckdb
        from pyproj import Transformer
        from shapely import wkb
        from shapely.geometry import mapping
        from shapely.ops import transform as transform_geometry

        connection = duckdb.connect(str(path), read_only=True)
        try:
            columns = [row[0] for row in connection.execute("DESCRIBE sections").fetchall()]
            rows = connection.execute(
                "SELECT * FROM sections WHERE procom = ? LIMIT ?",
                [int(procom), int(limit)],
            ).fetchall()
        finally:
            connection.close()

        to_wgs84 = Transformer.from_crs("EPSG:32632", "EPSG:4326", always_xy=True).transform
        features = []
        geometry_index = columns.index("geom")
        for row in rows:
            props = {}
            for name, value in zip(columns, row, strict=True):
                if name == "geom":
                    continue
                if hasattr(value, "item"):
                    value = value.item()
                props[name] = value
            props["ratios"] = _census_ratios(props)
            geometry = wkb.loads(bytes(row[geometry_index]))
            geometry = transform_geometry(to_wgs84, geometry)
            features.append({
                "type": "Feature",
                "properties": props,
                "geometry": mapping(geometry),
            })
        return {
            "type": "FeatureCollection",
            "features": features,
            "metadata": {
                "procom": int(procom),
                "count": len(features),
                "source": "ISTAT Basi Territoriali 2021 local DuckDB",
            },
        }
    except (ImportError, OSError, TypeError, ValueError, RuntimeError):
        logger.warning("Local census section lookup failed for PROCOM %s", procom, exc_info=True)
        return None


def get_census_sections(cadastral_code: str, limit: int = 5000) -> Optional[Dict[str, Any]]:
    """Census sections covering a comune (by catasto code) as a GeoJSON
    FeatureCollection — each Feature's properties carry the 119 raw ISTAT
    indicator counts plus a ``ratios`` sub-dict (education/employment/
    foreign-resident/vacancy rate, avg household size)."""
    if not census_db_available():
        return None
    muni = get_municipality_by_cadastral_code(cadastral_code)
    if muni is None or muni.get("procom") is None:
        return None
    try:
        fc = _census_sections_for_comune(muni["procom"], limit=limit, store_path=_census_store_path())
    except (AttributeError, TypeError, RuntimeError):
        fc = None
    if not isinstance(fc, dict):
        fc = _local_census_sections_for_comune(muni["procom"], limit=limit)
    if fc is None:
        return None
    fc.setdefault("metadata", {})["source"] = "ISTAT Basi Territoriali 2021 via aecs4u-stats"
    return fc


def get_census_section_at_point(lat: float, lng: float) -> Optional[Dict[str, Any]]:
    """The census section containing a WGS84 point, or ``None``."""
    if not census_db_available():
        return None
    parcel = get_parcel_at_point(lat, lng)
    if parcel:
        props = parcel.get("properties") or {}
        code = props.get("municipality_code") or props.get("ADMINISTRATIVEUNIT")
        municipality = get_municipality_by_cadastral_code(str(code)) if code else None
        result = _census_section_for_parcel(
            {"lat": lat, "lng": lng}, municipality
        )
        if result is not None:
            return result
    # A point can be in a census section without being in a cadastral parcel
    # (for example a public road). Resolve its municipality independently and
    # use the CRS-aware local matcher before consulting older package helpers.
    code = _cadastral_code_at_point(lat, lng)
    if code:
        municipality = get_municipality_by_cadastral_code(code, use_postgres=False)
        result = _census_section_for_parcel({"lat": lat, "lng": lng}, municipality)
        if result is not None:
            return result
    try:
        result = _census_section_at_point(lat, lng, store_path=_census_store_path())
    except (AttributeError, TypeError, RuntimeError):
        result = None
    return result


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
    engine = _istat_query_engine()
    checker = getattr(engine, "safety_available", None)
    if callable(checker):
        try:
            if checker():
                return True
        except Exception:
            logger.debug("Installed ISTAT safety checker failed", exc_info=True)
    return _istat_sqlite_table_available("istat_safety_indicators")


def bes_db_available() -> bool:
    engine = _istat_query_engine()
    checker = getattr(engine, "bes_available", None)
    if callable(checker):
        try:
            return bool(checker())
        except Exception:
            logger.debug("Installed ISTAT BES checker failed", exc_info=True)
    return (
        _istat_sqlite_table_available("istat_bes_quality_of_life")
        or _istat_sqlite_table_available("istat_bes_indicators")
        or bool(_istat_bes_rows())
    )


def demographic_db_available() -> bool:
    engine = _istat_query_engine()
    checker = getattr(engine, "demographic_available", None)
    if callable(checker):
        try:
            if checker():
                return True
        except Exception:
            logger.debug("Installed ISTAT demographic checker failed", exc_info=True)
    return _istat_sqlite_table_available("istat_demographic_indicators")


def _istat_sqlite_table_available(table: str) -> bool:
    """Check an optional ISTAT table in the active SQLite snapshot."""
    try:
        connection = sqlite3.connect(f"file:{_istat_sqlite_path()}?mode=ro", uri=True)
        try:
            return bool(
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
                    (table,),
                ).fetchone()
            )
        finally:
            connection.close()
    except (OSError, sqlite3.Error):
        return False


@lru_cache(maxsize=1)
def _istat_bes_rows() -> tuple[Dict[str, Any], ...]:
    """Load the provisioned BES snapshot when the legacy DB table is absent.

    Recent aecs4u-stats releases name the SQLite table
    ``istat_bes_quality_of_life``; older application code looked for
    ``istat_bes_indicators``.  During the transition the shared volume may
    contain only the downloaded raw CSV, so support both forms without
    modifying the source database on a request.
    """
    table = None
    try:
        connection = sqlite3.connect(f"file:{_istat_sqlite_path()}?mode=ro", uri=True)
        try:
            tables = {
                str(row[0]).lower()
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            for candidate in ("istat_bes_quality_of_life", "istat_bes_indicators"):
                if candidate in tables:
                    table = candidate
                    break
            if table:
                columns = {
                    str(row[1]).lower(): str(row[1])
                    for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
                }
                required = {"ref_area", "data_type", "time_period", "value"}
                if required.issubset(columns):
                    selected = [columns[name] for name in ("ref_area", "data_type", "time_period", "value")]
                    sex = columns.get("sex")
                    domain = columns.get("domain")
                    edition = columns.get("edition")
                    for optional in (sex, domain, edition):
                        selected.append(optional or "NULL")
                    quoted = ", ".join(
                        item if item == "NULL" else f'"{item.replace(chr(34), chr(34) * 2)}"'
                        for item in selected
                    )
                    rows = connection.execute(
                        f"SELECT {quoted} FROM \"{table.replace(chr(34), chr(34) * 2)}\""
                    ).fetchall()
                    return tuple(
                        {
                            "ref_area": row[0], "data_type": row[1],
                            "time_period": row[2], "value": row[3],
                            "sex": row[4], "domain": row[5], "edition": row[6],
                        }
                        for row in rows
                    )
        finally:
            connection.close()
    except (OSError, sqlite3.Error):
        logger.debug("SQLite BES snapshot could not be read", exc_info=True)

    configured_dir = os.getenv("ISTAT_DATA_DIR")
    directories = []
    if configured_dir:
        directories.append(Path(configured_dir).expanduser() / "raw")
    directories.extend((Path("/data/istat/raw"), _istat_sqlite_path().parent / "raw"))
    files = []
    seen = set()
    for directory in directories:
        try:
            for path in sorted(directory.glob("istat_bes_quality_of_life_*.csv"), reverse=True):
                if path not in seen and path.is_file() and path.stat().st_size > 0:
                    seen.add(path)
                    files.append(path)
        except OSError:
            continue
    records: dict[tuple[str, str, str, str], Dict[str, Any]] = {}
    for path in files:
        try:
            with path.open(newline="", encoding="utf-8") as handle:
                for row in csv.DictReader(handle):
                    ref_area = str(row.get("REF_AREA") or "").strip()
                    data_type = str(row.get("DATA_TYPE") or "").strip()
                    period = str(row.get("TIME_PERIOD") or "").strip()
                    if not ref_area or not data_type or not period:
                        continue
                    key = (ref_area, data_type, period, str(row.get("SEX") or "").strip())
                    candidate = {
                        "ref_area": ref_area,
                        "data_type": data_type,
                        "time_period": period,
                        "value": row.get("value"),
                        "sex": str(row.get("SEX") or "").strip() or None,
                        "domain": str(row.get("DOMAIN") or "").strip() or None,
                        "edition": str(row.get("EDITION") or "").strip() or None,
                    }
                    previous = records.get(key)
                    if previous is None or str(candidate["edition"] or "") >= str(previous["edition"] or ""):
                        records[key] = candidate
        except (OSError, UnicodeError, csv.Error):
            logger.debug("BES CSV snapshot could not be read: %s", path, exc_info=True)
    return tuple(records.values())


def _local_bes_indicators(nuts3: str) -> list[str]:
    return sorted({
        str(row["data_type"])
        for row in _istat_bes_rows()
        if str(row.get("ref_area") or "").upper() == str(nuts3).upper()
        and row.get("data_type")
    })


def _local_bes_series(
    nuts3: str,
    data_type: str,
    year: Optional[int] = None,
) -> list[Dict[str, Any]]:
    rows = [
        row for row in _istat_bes_rows()
        if str(row.get("ref_area") or "").upper() == str(nuts3).upper()
        and str(row.get("data_type") or "") == str(data_type)
    ]
    if year is not None:
        rows = [row for row in rows if str(row.get("time_period")) == str(year)]
    if not rows:
        return []
    total_rows = [row for row in rows if str(row.get("sex") or "").upper() == "T"]
    selected = total_rows or rows
    result = []
    for row in sorted(selected, key=lambda item: str(item.get("time_period") or "")):
        value = row.get("value")
        try:
            value = float(value) if value not in (None, "") else None
            if isinstance(value, float) and value.is_integer():
                value = int(value)
        except (TypeError, ValueError):
            pass
        result.append({
            "year": str(row.get("time_period")),
            "value": value,
            "sex": row.get("sex"),
            "domain": row.get("domain"),
        })
    return result


def _local_crime_profile(
    cadastral_code: str,
    nuts3: str,
    province: Optional[str],
    year: Optional[int],
) -> Optional[Dict[str, Any]]:
    """Read province-level crime facts from legacy ISTAT SQLite snapshots."""
    if not _istat_sqlite_table_available("istat_safety_indicators"):
        return None
    try:
        connection = sqlite3.connect(f"file:{_istat_sqlite_path()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            params: list[Any] = [nuts3]
            condition = "ref_area = ?"
            if year is not None:
                condition += " AND CAST(year AS INTEGER) = ?"
                params.append(year)
            rows = connection.execute(
                f"SELECT year, crime_type, crimes FROM istat_safety_indicators WHERE {condition}",
                params,
            ).fetchall()
        finally:
            connection.close()
        if not rows:
            return None
        latest_year = max(int(row["year"]) for row in rows)
        latest = [row for row in rows if int(row["year"]) == latest_year]
        return {
            "cadastral_code": cadastral_code,
            "province": province,
            "nuts3": nuts3,
            "year": latest_year,
            "total_crimes": sum(int(row["crimes"] or 0) for row in latest),
            "crime_types": len({str(row["crime_type"]) for row in latest}),
            "crime_types_available": sorted({str(row["crime_type"]) for row in latest}),
            "source": "ISTAT (delitti denunciati) via local SQLite snapshot",
        }
    except (OSError, sqlite3.Error, TypeError, ValueError):
        logger.warning("Local ISTAT crime lookup failed for %s", cadastral_code, exc_info=True)
        return None


def _local_demographic_indicators(nuts3: str) -> list[str]:
    if not _istat_sqlite_table_available("istat_demographic_indicators"):
        return []
    try:
        connection = sqlite3.connect(f"file:{_istat_sqlite_path()}?mode=ro", uri=True)
        try:
            rows = connection.execute(
                "SELECT DISTINCT DATA_TYPE FROM istat_demographic_indicators "
                "WHERE REF_AREA = ? ORDER BY DATA_TYPE",
                (nuts3,),
            ).fetchall()
        finally:
            connection.close()
        return [str(row[0]) for row in rows if row[0]]
    except (OSError, sqlite3.Error):
        return []


def _local_demographic_series(
    nuts3: str,
    data_type: str,
    year: Optional[int] = None,
) -> list[Dict[str, Any]]:
    if not _istat_sqlite_table_available("istat_demographic_indicators"):
        return []
    try:
        connection = sqlite3.connect(f"file:{_istat_sqlite_path()}?mode=ro", uri=True)
        try:
            params: list[Any] = [nuts3, data_type]
            condition = "REF_AREA = ? AND DATA_TYPE = ?"
            if year is not None:
                condition += " AND CAST(TIME_PERIOD AS INTEGER) = ?"
                params.append(year)
            rows = connection.execute(
                f"SELECT TIME_PERIOD, value FROM istat_demographic_indicators WHERE {condition} "
                "ORDER BY CAST(TIME_PERIOD AS INTEGER)",
                params,
            ).fetchall()
        finally:
            connection.close()
        return [{"year": str(row[0]), "value": row[1]} for row in rows]
    except (OSError, sqlite3.Error):
        return []


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
    try:
        kpis = engine.get_safety_overview_kpis(ref_area=muni["nuts3"], year=year)
    except AttributeError:
        return _local_crime_profile(
            cadastral_code,
            muni["nuts3"],
            muni.get("province"),
            year,
        )
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
    engine = _istat_query_engine()
    try:
        indicators = engine.list_bes_indicators(ref_area=muni["nuts3"])
    except AttributeError:
        indicators = _local_bes_indicators(muni["nuts3"])
    if not indicators:
        return None
    return {
        "cadastral_code": cadastral_code, "nuts3": muni["nuts3"],
        "indicators": indicators,
        "source": "ISTAT BES via aecs4u-stats local snapshot",
    }


def get_quality_of_life_indicator(
    cadastral_code: str, data_type: str, year: Optional[int] = None
) -> Optional[Dict[str, Any]]:
    """Time series for one BES indicator (see ``get_quality_of_life_indicators``)."""
    muni = _resolve_nuts3(cadastral_code)
    if muni is None:
        return None
    engine = _istat_query_engine()
    try:
        series = engine.get_bes_indicator(data_type, ref_area=muni["nuts3"], year=year)
    except AttributeError:
        series = _local_bes_series(muni["nuts3"], data_type, year=year)
    if not series:
        return None
    return {
        "cadastral_code": cadastral_code, "nuts3": muni["nuts3"], "data_type": data_type,
        "series": series, "source": "ISTAT BES via aecs4u-stats local snapshot",
    }


def get_demographic_indicators(cadastral_code: str) -> Optional[Dict[str, Any]]:
    """Demographic indicator codes available for a comune's province."""
    muni = _resolve_nuts3(cadastral_code)
    if muni is None:
        return None
    engine = _istat_query_engine()
    try:
        indicators = engine.list_demographic_indicators(ref_area=muni["nuts3"])
    except AttributeError:
        indicators = _local_demographic_indicators(muni["nuts3"])
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
    engine = _istat_query_engine()
    try:
        series = engine.get_demographic_indicator(data_type, ref_area=muni["nuts3"], year=year)
    except AttributeError:
        series = _local_demographic_series(muni["nuts3"], data_type, year=year)
    if not series:
        return None
    return {
        "cadastral_code": cadastral_code, "nuts3": muni["nuts3"], "data_type": data_type,
        "series": series, "source": "ISTAT demographic indicators via aecs4u-stats",
    }
