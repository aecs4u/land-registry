"""Maintained provider links and licence-review notes for parcel reports.

The catalog identifies providers and records the basis for licence or reuse
statements. It deliberately does not supply dataset vintages: those must come
from the response metadata for the specific data used by a report.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SourceCatalogEntry:
    key: str
    display_name: str
    aliases: tuple[str, ...]
    provider_url: str
    license_name: str | None
    license_basis: str
    terms_url: str | None = None
    citation: str | None = None
    reuse_terms: str | None = None
    attribution_text: str | None = None


SOURCE_CATALOG = (
    SourceCatalogEntry(
        key="openstreetmap",
        display_name="OpenStreetMap",
        aliases=("OpenStreetMap", "OSM"),
        provider_url="https://www.openstreetmap.org/copyright",
        license_name="Open Database License (ODbL) 1.0",
        license_basis="official_terms_reviewed",
        terms_url="https://www.openstreetmap.org/copyright",
        attribution_text=(
            "Contains information from OpenStreetMap, made available under the Open Database License (ODbL). "
            "© OpenStreetMap contributors. https://www.openstreetmap.org/copyright"
        ),
    ),
    SourceCatalogEntry(
        key="omi",
        display_name="Agenzia delle Entrate – OMI",
        aliases=("Agenzia delle Entrate OMI", "Agenzia Entrate OMI", "OMI"),
        provider_url="https://www1.agenziaentrate.gov.it/servizi/geopoi_omi/index.htm",
        license_name=None,
        license_basis="citation_verified_license_pending",
        terms_url="https://telematici.agenziaentrate.gov.it/pdf/guidaFornitureOMI.pdf",
        citation="Agenzia Entrate - OMI",
        reuse_terms=(
            "The published guide requires the source citation but does not state a named reuse licence; "
            "complete reuse terms remain unverified."
        ),
    ),
    SourceCatalogEntry(
        key="zornade_egms_summary",
        display_name="Zornade Rischio Subsidenza Italia (EGMS 100 m summary)",
        aliases=(
            "Zornade Rischio Subsidenza Italia",
            "Zornade EGMS 100m summary",
        ),
        provider_url="https://zornade.com/data-downloads/",
        license_name="Open Database License (ODbL) 1.0",
        license_basis="provider_product_page_reviewed",
        terms_url="https://zornade.com/data-downloads/",
        reuse_terms=(
            "Zornade lists this summary dataset under ODbL 1.0. Copernicus Land Monitoring Service "
            "conditions also apply to the underlying source product."
        ),
        attribution_text=(
            "Zornade Rischio Subsidenza Italia, derived from European Union's Copernicus Land Monitoring "
            "Service information. Contains modified Copernicus Sentinel data. Produced with funding by the "
            "European Union. This product is not endorsed by the European Union."
        ),
    ),
    SourceCatalogEntry(
        key="copernicus_egms",
        display_name="European Union Copernicus Land Monitoring Service (EGMS)",
        aliases=("Copernicus EGMS", "European Ground Motion Service", "EGMS portal", "EGMS"),
        provider_url="https://egms.land.copernicus.eu/",
        license_name=None,
        license_basis="official_reuse_policy_reviewed",
        terms_url="https://land.copernicus.eu/en/data-policy",
        reuse_terms=(
            "Full, open and free access. The policy requires source and EU funding acknowledgement, "
            "identification of adapted or modified products, and no implied EU endorsement; no formal "
            "licence name is asserted here."
        ),
        attribution_text=(
            "Generated using European Union's Copernicus Land Monitoring Service information. "
            "Contains modified Copernicus Sentinel data. Produced with funding by the European Union. "
            "This product is not endorsed by the European Union."
        ),
    ),
    SourceCatalogEntry(
        key="ispra_pai_pgra",
        display_name="ISPRA PAI/PGRA",
        aliases=("ISPRA PAI/PGRA", "ISPRA PAI/PGRA polygon mosaics"),
        provider_url="https://idrogeo.isprambiente.it/",
        license_name="Creative Commons Attribution-ShareAlike 4.0 International (CC BY-SA 4.0)",
        license_basis="official_terms_reviewed",
        terms_url=(
            "https://idrogeo.isprambiente.it/cms/wp-content/uploads/2022/03/"
            "Licenza_Condizioni_Uso_Pericolosita_Indicatori_Rischio_ISPRA.pdf"
        ),
        citation="ISPRA (2020) Pericolosità e indicatori di rischio per frane e alluvioni",
        reuse_terms=(
            "ISPRA specifies CC BY-SA 4.0 for the 2020 PAI/PGRA hazard and risk dataset; "
            "cite the source and use the same licence for redistributed derived data."
        ),
        attribution_text=(
            "ISPRA (2020) Pericolosità e indicatori di rischio per frane e alluvioni. "
            "idrogeo.isprambiente.it. CC BY-SA 4.0."
        ),
    ),
    SourceCatalogEntry(
        key="ispra_coastal_dynamics",
        display_name="ISPRA — Linea di Costa 2020 v2.0",
        aliases=(
            "ISPRA coastal dynamics 2006-2020",
            "ISPRA Dinamica Litoranea 2006-2020",
            "ISPRA Linea di Costa 2020 v2.0",
        ),
        provider_url=(
            "https://sinacloud.isprambiente.it/arcgisadv/rest/services/coste/"
            "Assetto_costiero_anno_2020/FeatureServer/1"
        ),
        license_name="Creative Commons Attribution 4.0 International (CC BY 4.0)",
        license_basis="official_terms_reviewed",
        terms_url=(
            "https://geodati.gov.it/geoportale/visualizzazione-metadati/"
            "scheda-metadati?metadataid=ispra_rm%3A01Lineacosta2020_v2.0_DT"
        ),
        citation="ISPRA, Linea di Costa 2020 v2.0; Dinamica Litoranea 2006–2020",
        reuse_terms=(
            "The National Territorial Data Catalog (RNDT) metadata for ISPRA's Linea di Costa 2020 v2.0 "
            "states CC BY 4.0. The related 2006–2020 shoreline-dynamics layer classifies erosion as inland "
            "movement greater than 5 m, advance as seaward movement greater than 5 m, and smaller changes "
            "as stable. The 2020 shoreline characterization was derived mostly from Google Maps imagery "
            "from 2017–2020; this entry covers the ISPRA vector dataset, not the underlying imagery."
        ),
        attribution_text=(
            "ISPRA, Linea di Costa 2020 v2.0 — Dinamica Litoranea 2006–2020. "
            "CC BY 4.0. Source metadata: RNDT."
        ),
    ),
    SourceCatalogEntry(
        key="istat",
        display_name="ISTAT",
        aliases=("ISTAT",),
        provider_url="https://www.istat.it/dati/open-data/",
        license_name="Creative Commons Attribution 4.0 International (CC BY 4.0)",
        license_basis="official_terms_reviewed",
    ),
    SourceCatalogEntry(
        key="mef_irpef",
        display_name="MEF – Dipartimento delle Finanze",
        aliases=("MEF/IRPEF", "MEF", "IRPEF"),
        provider_url="https://www1.finanze.gov.it/finanze/analisi_stat/public/index.php?opendata=yes",
        license_name="Creative Commons Attribution 3.0 Unported (CC BY 3.0)",
        license_basis="official_methodology_reviewed",
        terms_url="https://www1.finanze.gov.it/finanze/stat_dbNewSerie/public/contenuti/nota_metodologica.pdf",
        citation="MEF – Dipartimento delle Finanze",
        reuse_terms=(
            "The Department of Finance's Open Data methodology states CC BY 3.0 for its tax-return data."
        ),
        attribution_text="Source: MEF – Dipartimento delle Finanze.",
    ),
    SourceCatalogEntry(
        key="ingv_mps04",
        display_name="INGV MPS04 seismic hazard model",
        aliases=("INGV MPS04", "MPS04"),
        provider_url="https://mps04-ws.pi.ingv.it/",
        license_name="Creative Commons Attribution 4.0 International (CC BY 4.0)",
        license_basis="official_general_policy_reviewed",
        terms_url="https://data.ingv.it/docs/note-legali.html",
        citation="Meletti et al. (2006), INGV MPS04 seismic hazard model, https://doi.org/10.13127/sh/mps04/ag",
        reuse_terms=(
            "INGV's open-data legal notice applies CC BY 4.0 unless a dataset states otherwise. "
            "This application reports the nearest native-grid value for a 10% exceedance probability in 50 years."
        ),
        attribution_text=(
            "INGV MPS04 seismic hazard model (Meletti et al., 2006), CC BY 4.0. "
            "Nearest-grid-point value reported for 10% exceedance probability in 50 years; no interpolation applied."
        ),
    ),
    SourceCatalogEntry(
        key="agenzia_demanio",
        display_name="Agenzia del Demanio",
        aliases=("Agenzia del Demanio",),
        provider_url="https://dati.agenziademanio.it/",
        license_name=None,
        license_basis="official_general_policy_reviewed",
        terms_url="https://dati.agenziademanio.it/",
        reuse_terms=(
            "OpenDemanio describes its open data as machine-readable and freely reusable. The reviewed page does not "
            "identify a named licence or dataset-specific conditions for these concession records, so no formal "
            "licence is asserted."
        ),
    ),
    SourceCatalogEntry(
        key="agenzia_entrate_cadastre",
        display_name="Agenzia delle Entrate – Catasto",
        aliases=("Agenzia delle Entrate INSPIRE", "Agenzia delle Entrate cadastral", "Catasto"),
        provider_url="https://geoportale.cartografia.agenziaentrate.gov.it/age-inspire/srv/spa/catalog.search",
        license_name=None,
        license_basis="official_service_access_reviewed",
        terms_url="https://www1.agenziaentrate.gov.it/web_app_entrate/accesso_ai_dati.html",
        reuse_terms=(
            "Agenzia delle Entrate describes SISTER and Portale per i Comuni access to cadastral data as convention-based "
            "for authorized public bodies. The reviewed access guide does not establish downstream reuse rights for "
            "the parcel records used here; no licence is asserted."
        ),
    ),
    SourceCatalogEntry(
        key="sister",
        display_name="SISTER",
        aliases=("SISTER",),
        provider_url="https://sister.agenziaentrate.gov.it/",
        license_name=None,
        license_basis="official_service_access_reviewed",
        terms_url="https://www1.agenziaentrate.gov.it/web_app_entrate/accesso_ai_dati.html",
        reuse_terms=(
            "The official access guide describes SISTER as a channel for cadastral and mortgage data access on a "
            "convention basis. It does not state downstream reuse terms for the cached property records shown here; "
            "no licence is asserted."
        ),
    ),
    SourceCatalogEntry(
        key="pvp",
        display_name="Portale delle Vendite Pubbliche",
        aliases=("PVP", "Portale delle Vendite Pubbliche"),
        provider_url="https://pvp.giustizia.it/pvp/it/homepage.page",
        license_name=None,
        license_basis="official_public_access_reviewed",
        terms_url="https://pvp.giustizia.it/pvp/it/guida.page",
        reuse_terms=(
            "The official site guide describes public access to search and view sale notices without credentials. It "
            "does not state a general reuse licence for notice text, attachments, or derived records; no licence is "
            "asserted."
        ),
    ),
    SourceCatalogEntry(
        key="nasa_firms",
        display_name="NASA FIRMS — NOAA-21 VIIRS NRT",
        aliases=("NASA FIRMS", "FIRMS", "VIIRS_NOAA21_NRT", "VIIRS NOAA-21 NRT"),
        provider_url="https://firms.modaps.eosdis.nasa.gov/content/descriptions/FIRMS_VIIRS_Firehotspots.html",
        license_name=None,
        license_basis="official_policy_scope_reviewed",
        terms_url="https://www.earthdata.nasa.gov/engage/open-data-services-software/data-use-policy",
        citation="NASA FIRMS VIIRS NOAA-21 Near Real-Time active fire detections",
        reuse_terms=(
            "The adapter requests VIIRS_NOAA21_NRT (NOAA-21/JPSS-2 VIIRS near-real-time), rather than a mixed "
            "sensor collection. NOAA NCEI labels its upstream NOAA JPSS VIIRS sensor-data record CC0, but that "
            "does not establish terms for NASA FIRMS's derived active-fire detections. NASA's policy says non-NASA "
            "data retain their sponsoring organization's terms. No product-specific reuse terms were found for this "
            "FIRMS feed, so no named licence is asserted. NOAA input-data metadata: "
            "https://www.ncei.noaa.gov/access/metadata/landing-page/bin/iso?id=gov.noaa.ncdc%3AC00864"
        ),
        attribution_text=(
            "NASA FIRMS VIIRS NOAA-21 Near Real-Time active fire detections. Acknowledge the source and do not "
            "imply NASA or NOAA endorsement."
        ),
    ),
)


def _normalise(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.casefold()).split())


def find_source_catalog_entry(source: object) -> SourceCatalogEntry | None:
    """Resolve known source labels without treating backend names as providers."""
    if not isinstance(source, str) or not source.strip():
        return None
    normalized_source = f" {_normalise(source)} "
    matches: list[tuple[int, SourceCatalogEntry]] = []
    for entry in SOURCE_CATALOG:
        for alias in entry.aliases:
            normalized_alias = _normalise(alias)
            if normalized_alias and f" {normalized_alias} " in normalized_source:
                matches.append((len(normalized_alias), entry))
    return max(matches, key=lambda match: match[0])[1] if matches else None
