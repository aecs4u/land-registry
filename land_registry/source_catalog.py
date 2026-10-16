"""Maintained provider and parcel-field data catalogues.

The provider catalog records links and the basis for licence or reuse
statements. The parcel-field catalog documents supported API sections and
their methods, spatial scopes and freshness semantics. Neither catalog embeds
runtime dataset vintages; those come from response metadata for the data used.
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
        key="anncsu",
        display_name="ANNCSU — Agenzia delle Entrate and ISTAT",
        aliases=("ANNCSU", "ANNCSU — Agenzia delle Entrate and ISTAT"),
        provider_url="https://www.anncsu.gov.it/it/consultazione-dellarchivio/open-data/",
        license_name="CC BY 4.0",
        license_basis="official_open_data_terms_reviewed",
        terms_url="https://www.anncsu.gov.it/it/consultazione-dellarchivio/ricerca-strade-e-numeri-civici/",
        citation="ANNCSU — Agenzia delle Entrate and ISTAT",
        attribution_text="ANNCSU — Agenzia delle Entrate and ISTAT",
    ),
    SourceCatalogEntry(
        key="mibac_protection_sources",
        display_name="Italian Ministry of Culture protection-data sources",
        aliases=(
            "SITAP / Vincoli in Rete / Catalogo Generale via aecs4u-stats",
            "SITAP",
            "Vincoli in Rete",
            "Catalogo Generale",
        ),
        provider_url="https://cultura.gov.it/sitap-sistema-informativo-territoriale-ambientale-e-paesaggistico",
        license_name=None,
        license_basis="upstream_contract_review_pending",
        citation="SITAP; Vincoli in Rete; Catalogo Generale dei Beni Culturali",
        reuse_terms=(
            "The upstream spatial protection-data contract and dataset-specific reuse terms are pending. "
            "Catalogo Generale records are catalogue references and are not treated as binding constraints."
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
        key="ondata_omi_boundaries",
        display_name="OnData OMI zone boundary mirror",
        aliases=("OnData mirror of Agenzia delle Entrate OMI zones via aecs4u-stats",),
        provider_url="https://github.com/ondata/quotazioni-immobiliari-agenzia-entrate",
        license_name=None,
        license_basis="adapter_declaration_review_pending",
        citation="OnData OMI zone boundary mirror (2018/2)",
        reuse_terms=(
            "The local aecs4u-stats adapter documents this as a mirror of per-province OMI KMZ boundaries. "
            "Mirror-specific reuse terms have not been reviewed; the 2018/2 polygons are approximate geometry."
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
        key="copernicus_corine",
        display_name="Copernicus Land Monitoring Service — CORINE Land Cover 2018",
        aliases=("CORINE Land Cover 2018", "CORINE", "CLC2018", "Copernicus CORINE"),
        provider_url="https://land.copernicus.eu/en/products/corine-land-cover/clc2018",
        license_name=None,
        license_basis="official_data_use_terms_reviewed",
        terms_url="https://land.copernicus.eu/en/faq/data-use-terms-and-conditions",
        citation="CORINE Land Cover 2018, release V2020_20u1; DOI 10.2909/71c95a07-e296-44fc-b22b-415f42acfdf0.",
        reuse_terms=(
            "Copernicus Land Monitoring Service data are free to use, including commercially, and may be "
            "redistributed with source attribution. No named licence is asserted here. Cite the product, "
            "access service and DOI."
        ),
        attribution_text=(
            "Copernicus Land Monitoring Service, CORINE Land Cover 2018, release V2020_20u1, "
            "DOI 10.2909/71c95a07-e296-44fc-b22b-415f42acfdf0."
        ),
    ),
    SourceCatalogEntry(
        key="ingv_tinitaly",
        display_name="INGV TINITALY 1.1 digital terrain model",
        aliases=("TINITALY", "TINITALY/1.1", "TINItaly DEM", "INGV terrain"),
        provider_url="https://tinitaly.pi.ingv.it/",
        license_name="CC BY 4.0",
        license_basis="provider_product_page_reviewed",
        terms_url="https://creativecommons.org/licenses/by/4.0/",
        citation=(
            "Tarquini S., Isola I., Favalli M., Battistini A., Dotta G. (2023). "
            "TINITALY, a digital elevation model of Italy with a 10 meters cell size "
            "(Version 1.1). Istituto Nazionale di Geofisica e Vulcanologia (INGV). "
            "https://doi.org/10.13127/tinitaly/1.1"
        ),
        reuse_terms="CC BY 4.0; attribution is required.",
        attribution_text=(
            "Tarquini S., Isola I., Favalli M., Battistini A., Dotta G. (2023), "
            "TINITALY/1.1, INGV, DOI 10.13127/tinitaly/1.1."
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
        key="dpc_seismic",
        display_name="Dipartimento della Protezione Civile — Classificazione sismica",
        aliases=(
            "DPC municipal seismic classification",
            "Dipartimento della Protezione Civile municipal seismic classification",
            "Classificazione sismica DPC",
        ),
        provider_url="https://rischi.protezionecivile.gov.it/it/approfondimento/classificazione-sismica/",
        license_name=None,
        license_basis="official_public_access_reviewed",
        citation="Dipartimento della Protezione Civile, Classificazione sismica; national CSV listed as updated 31 May 2025.",
        reuse_terms=(
            "The official page links the national municipal classification CSV and regional provisions. "
            "No named reuse licence for the CSV is asserted here; the vintage of the application's installed "
            "classification may differ and is reported only when available from the data block."
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
        key="openapi_catasto",
        display_name="OpenAPI Catasto",
        aliases=("OpenAPI Catasto", "catasto.openapi.it"),
        provider_url="https://console.openapi.com/it/apis/catasto/documentation",
        license_name=None,
        license_basis="official_provider_terms_reviewed_no_data_license_asserted",
        terms_url="https://openapi.it/documenti/Termini-e-condizioni-Openapi.pdf",
        citation="OpenAPI Catasto API; its FAQ identifies Agenzia delle Entrate as the principal source.",
        reuse_terms=(
            "OpenAPI's published API data-service terms restrict mass extraction and specify other limits on use. "
            "They do not establish a general licence to redistribute stored cadastral records. The applicable "
            "account agreement and underlying data-provider conditions need review before reuse is asserted."
        ),
        attribution_text=(
            "Cadastral information retrieved through OpenAPI Catasto. OpenAPI identifies Agenzia delle Entrate "
            "as its principal source; the source for an individual record is not included in the stored query."
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
        key="zornade_municipal_solar",
        display_name="Zornade municipal solar-potential aggregate",
        aliases=(
            "Zornade municipal solar-potential model",
            "Zornade solar potential",
            "aecs4u-stats solar.solar_potential_comuni",
            "aecs4u-stats serving.municipality_profile",
            "aecs4u-stats municipality solar aggregates",
        ),
        provider_url="https://zornade.com/blog/potenziale-solare-particella-gratis-vs-catasto-gps",
        license_name=None,
        license_basis="provider_public_guide_open_license_name_unverified",
        terms_url="https://zornade.com/blog/potenziale-solare-particella-gratis-vs-catasto-gps",
        citation="Zornade municipal solar-potential dataset; retain the per-record solar_data_version.",
        reuse_terms=(
            "Zornade's public guide states that its municipality and province aggregates are available under an open "
            "licence, but it does not name that licence. This catalog therefore asserts no named licence; review the "
            "specific dataset terms before redistribution. The reported model version identifies PVGIS-SARAH3 and "
            "the Zornade methodology separately from this aggregate's reuse terms."
        ),
        attribution_text=(
            "Zornade municipal solar-potential aggregate; preserve the source and model version reported for the "
            "specific record."
        ),
    ),
    SourceCatalogEntry(
        key="pvgis",
        display_name="European Commission Joint Research Centre — PVGIS",
        aliases=("PVGIS", "PVGIS 5.3 / PVGIS-SARAH3", "PVGIS-SARAH3"),
        provider_url=(
            "https://joint-research-centre.ec.europa.eu/photovoltaic-geographical-information-system-pvgis/"
            "using-pvgis-5/api-non-interactive-service_en"
        ),
        license_name=None,
        license_basis="official_api_documentation_reviewed_data_reuse_terms_unconfirmed",
        terms_url="https://photovoltaic-geographic-information-system.ec.europa.eu/",
        citation="European Commission Joint Research Centre, PVGIS 5.3, PVGIS-SARAH3.",
        reuse_terms=(
            "PVGIS provides an open API. A named reuse licence for the returned irradiance and production values "
            "has not been established in this catalog; review the current JRC terms before redistribution."
        ),
        attribution_text="European Commission Joint Research Centre, Photovoltaic Geographical Information System (PVGIS).",
    ),
    SourceCatalogEntry(
        key="nasa_black_marble",
        display_name="NASA Black Marble VIIRS VNP46A4",
        aliases=("NASA Black Marble VIIRS VNP46A4", "NASA Black Marble", "VNP46A4.002"),
        provider_url="https://ladsweb.modaps.eosdis.nasa.gov/missions-and-measurements/products/VNP46A4",
        license_name=None,
        license_basis="official_data_use_policy_reviewed",
        terms_url="https://www.earthdata.nasa.gov/engage/open-data-services-software/data-use-policy",
        citation="NASA VIIRS Land Science Investigator-led Processing System, VNP46A4.002, NASA LAADS DAAC, https://doi.org/10.5067/VIIRS/VNP46A4.002",
        reuse_terms=(
            "NASA's data-use policy applies to NASA data; cite the product and do not imply NASA endorsement. "
            "No separate named licence is asserted for this product in this catalog. The local importer requires "
            "an authorized Earthdata/LAADS token for download."
        ),
        attribution_text=(
            "NASA VIIRS Land Science Investigator-led Processing System, VNP46A4.002, NASA LAADS DAAC. "
            "https://doi.org/10.5067/VIIRS/VNP46A4.002"
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


@dataclass(frozen=True)
class ParcelApiFieldCatalogEntry:
    section: str
    fields: str
    source_keys: tuple[str, ...]
    provider_note: str
    method_scope: str
    version_freshness: str
    data_shape: str = "Provider-defined JSON value."
    stable_data_fields: tuple[str, ...] = ()
    provider_dynamic_fields: bool = True
    data_notes: str = ""
    stable_field_descriptions: tuple[tuple[str, str], ...] = ()


# Describe the currently supported /api/v2/parcels/{fid}?include=… sections.
# Cadence is stated only where the source contract supports it; otherwise the
# catalog explicitly says that the application does not report a schedule.
PARCEL_API_FIELD_CATALOG = (
    ParcelApiFieldCatalogEntry(
        section="basic",
        fields="Canonical parcel identity and source properties.",
        source_keys=("agenzia_entrate_cadastre",),
        provider_note="",
        method_scope="Stable feature ID lookup against the canonical cadastral map source.",
        version_freshness="The source_release value is returned when present; provider refresh cadence is not reported.",
        data_shape="Object containing canonical cadastral source properties.",
        data_notes="Property names follow the configured canonical parcel source and may vary by source release.",
    ),
    ParcelApiFieldCatalogEntry(
        section="geometry",
        fields="Cadastral parcel polygon.",
        source_keys=("agenzia_entrate_cadastre",),
        provider_note="",
        method_scope="Stable feature ID lookup; geometry is returned in EPSG:4326.",
        version_freshness="The source_release value is returned when present; provider refresh cadence is not reported.",
        data_shape="GeoJSON Geometry object in EPSG:4326.",
        stable_data_fields=("type", "coordinates"),
        provider_dynamic_fields=False,
        stable_field_descriptions=(
            ("type", "string — GeoJSON geometry type, normally Polygon or MultiPolygon."),
            ("coordinates", "array — nested longitude/latitude coordinate pairs in EPSG:4326."),
        ),
    ),
    ParcelApiFieldCatalogEntry(
        section="cadastral",
        fields="Comune code, sheet, urban section and postal code when available.",
        source_keys=("agenzia_entrate_cadastre", "istat"),
        provider_note="",
        method_scope="National cadastral reference match; parcel and municipality context.",
        version_freshness="The source release is returned when present; provider refresh cadence is not reported.",
        data_shape="Object.",
        stable_data_fields=(
            "foglio", "sezione_urbana", "comune_code", "postal_code",
            "postgres_parcel_spine_available", "field_provenance",
        ),
        provider_dynamic_fields=False,
        stable_field_descriptions=(
            ("foglio", "string | null — cadastral sheet number."),
            ("sezione_urbana", "string | null — urban cadastral section."),
            ("comune_code", "string — cadastral municipality code."),
            ("postal_code", "string | null — municipality postal code, when known."),
            ("postgres_parcel_spine_available", "boolean — whether the indexed parcel spine supplied context."),
            (
                "field_provenance",
                "object — per-field source, catalog key when available, match method and spatial resolution.",
            ),
        ),
    ),
    ParcelApiFieldCatalogEntry(
        section="population",
        fields="Residents, households, dwellings and census counts.",
        source_keys=("istat",),
        provider_note="",
        method_scope="Parcel centroid matched to the containing 2021 ISTAT census section.",
        version_freshness="ISTAT_BASI_TERRITORIALI_2021 snapshot; the next publication cadence is not represented.",
        data_shape="ISTAT GeoJSON Feature-like object with source properties.",
        stable_data_fields=("type", "properties", "geometry"),
        data_notes="The properties object retains fields from the configured ISTAT census source.",
        stable_field_descriptions=(
            ("type", "string — GeoJSON feature type."),
            ("properties", "object — ISTAT 2021 census-section values; source property keys are retained."),
            ("geometry", "GeoJSON geometry or null — census-section boundary, when the source returns it."),
        ),
    ),
    ParcelApiFieldCatalogEntry(
        section="terrain",
        fields=(
            "TINITALY elevation min/mean/max/standard deviation, slope mean/max, "
            "predominant aspect, elevation range and Riley Terrain Ruggedness Index."
        ),
        source_keys=("ingv_tinitaly",),
        provider_note="",
        method_scope=(
            "Polygon summaries use valid 10 m pixel centres in EPSG:32632. Slope and aspect use Horn's "
            "3x3 method; TRI uses the Riley et al. (1999) eight-neighbour formula. Subpixel polygons "
            "use an identified representative-point sample."
        ),
        version_freshness="TINITALY/1.1 (2023), a fixed elevation-model release.",
        data_shape="Object with elevation/slope/aspect/ruggedness statistics, coverage and sample counts.",
        stable_data_fields=(
            "elevation_min_m", "elevation_mean_m", "elevation_max_m", "elevation_std_m",
            "ruggedness_index_m", "slope_mean_deg", "slope_max_deg", "tri_mean_m",
            "aspect_mean_deg", "aspect_predominant", "sample_count", "slope_sample_count",
            "coverage_pct", "data_vintage", "dataset_version", "region_code", "match_method",
            "spatial_resolution", "spatial_resolution_m", "doi", "license", "attribution",
        ),
        provider_dynamic_fields=False,
        data_notes=(
            "TINITALY is a 10 m bare-earth terrain model, not a roof/building surface or parcel survey. "
            "Elevation statistics describe sampled valid pixels; no-data and unavailable stores are not zero."
        ),
        stable_field_descriptions=(
            ("elevation_min_m", "number | null — minimum sampled elevation in metres above sea level."),
            ("elevation_mean_m", "number | null — mean sampled elevation in metres."),
            ("elevation_max_m", "number | null — maximum sampled elevation in metres."),
            ("elevation_std_m", "number | null — standard deviation of sampled elevation in metres."),
            ("ruggedness_index_m", "number | null — parcel elevation range, max minus min, in metres."),
            ("slope_mean_deg", "number | null — mean Horn slope in degrees."),
            ("slope_max_deg", "number | null — maximum Horn slope in degrees."),
            ("tri_mean_m", "number | null — mean Riley Terrain Ruggedness Index in metres."),
            ("aspect_predominant", "string | null — predominant eight-sector aspect or flat."),
            ("coverage_pct", "number | null — valid pixel-centre share of polygon; null for point samples or subpixel fallback."),
            ("match_method", "string — point_sample, zonal_pixel_centres or representative_point_fallback."),
            ("spatial_resolution_m", "number — TINITALY horizontal cell size in metres (10)."),
        ),
    ),
    ParcelApiFieldCatalogEntry(
        section="land_cover",
        fields="Dominant CORINE 2018 class and area-weighted class distribution.",
        source_keys=("copernicus_corine",),
        provider_note="",
        method_scope=(
            "Polygon parcels are intersected with CLC polygons in EPSG:3035 and grouped by covered area; "
            "Point queries return the containing CLC class."
        ),
        version_freshness=(
            "CLC2018_V2020_20u1 (reference year 2018); the product is a fixed historical snapshot."
        ),
        data_shape="Object with the dominant class, distribution, mapped area and coverage.",
        stable_data_fields=(
            "class_code", "class_name_en", "class_name_it", "level_1_code", "level_2_code",
            "distribution", "covered_area_sqm", "parcel_area_sqm", "coverage_pct", "data_year",
            "dataset_version", "region_code", "match_method", "spatial_resolution", "doi",
        ),
        provider_dynamic_fields=False,
        data_notes=(
            "CLC has a 25 ha minimum mapping unit and 100 m minimum width. A parcel result is broad-area "
            "land-cover context; coverage_pct reports the share of parcel area intersecting mapped CLC polygons."
        ),
        stable_field_descriptions=(
            ("class_code", "string — dominant three-level CORINE nomenclature code."),
            ("class_name_en", "string | null — English class label."),
            ("class_name_it", "string | null — Italian class label."),
            ("distribution", "array — classes ordered by intersected area, with class and area shares."),
            ("covered_area_sqm", "number — sum of parcel area intersecting CLC polygons, in square metres."),
            ("parcel_area_sqm", "number — input parcel polygon area in EPSG:3035, in square metres."),
            ("coverage_pct", "number — parcel area intersecting mapped CLC polygons, as a percentage."),
            ("data_year", "integer — CLC reference year (2018)."),
            ("dataset_version", "string — source release identifier."),
            ("match_method", "string — area_weighted_intersection or point_in_polygon."),
            ("spatial_resolution", "string — source minimum mapping unit and minimum linear width."),
        ),
    ),
    ParcelApiFieldCatalogEntry(
        section="demographics",
        fields="Age and sex, education, employment, foreign-resident share, vacancy and household size.",
        source_keys=("istat",),
        provider_note="",
        method_scope="Parcel centroid matched to the containing 2021 ISTAT census section; some ratios are derived from census counts.",
        version_freshness="ISTAT_BASI_TERRITORIALI_2021 snapshot; the next publication cadence is not represented.",
        data_shape="ISTAT GeoJSON Feature-like object with source properties and derived ratios.",
        stable_data_fields=("type", "properties", "geometry", "properties.ratios.education_tertiary_rate", "properties.ratios.employment_rate_working_age", "properties.ratios.foreign_resident_share", "properties.ratios.vacancy_rate", "properties.ratios.avg_household_size"),
        data_notes="The properties object retains fields from the configured ISTAT census source; area_sqm is also added when available.",
        stable_field_descriptions=(
            ("type", "string — GeoJSON feature type."),
            ("properties", "object — ISTAT 2021 census-section values plus the derived ratios below."),
            ("geometry", "GeoJSON geometry or null — census-section boundary, when the source returns it."),
            ("properties.ratios.education_tertiary_rate", "number | null — p90 divided by p83."),
            ("properties.ratios.employment_rate_working_age", "number | null — p101 divided by the working-age population fields p17–p26."),
            ("properties.ratios.foreign_resident_share", "number | null — st1 divided by p1."),
            ("properties.ratios.vacancy_rate", "number | null — a3 divided by a8."),
            ("properties.ratios.avg_household_size", "number | null — p1 divided by pf1."),
        ),
    ),
    ParcelApiFieldCatalogEntry(
        section="economics",
        fields="Tax year, taxpayers, average taxable income and income brackets.",
        source_keys=("mef_irpef",),
        provider_note="",
        method_scope="Cadastral municipality crosswalk to municipality-level tax-return aggregates.",
        version_freshness="MEF_IRPEF_<year> identifies the tax year; data are annual, but application import timing is not reported.",
        data_shape="Object.",
        stable_data_fields=("tax_year", "taxpayers", "total_income", "average_income", "net_tax", "income_brackets", "source_facts"),
        provider_dynamic_fields=False,
        stable_field_descriptions=(
            ("tax_year", "integer | null — tax-return year."),
            ("taxpayers", "integer | null — taxpayers represented by the municipality aggregate."),
            ("total_income", "number | null — total taxable income in euros, when the source provides it."),
            ("average_income", "number | null — mean taxable income in euros per taxpayer."),
            ("net_tax", "number | null — reported net tax amount in euros."),
            ("income_brackets", "object — source bracket identifiers mapped to taxpayer frequencies."),
            ("source_facts", "array — source measure rows for the selected tax year."),
        ),
    ),
    ParcelApiFieldCatalogEntry(
        section="buildings",
        fields="SISTER cadastral property records; this is not a physical-footprint count.",
        source_keys=("sister",),
        provider_note="",
        method_scope="Cadastral-reference match to available SISTER property records; coverage is source/store dependent.",
        version_freshness="The block does not currently report a dataset version or refresh schedule.",
        data_shape="Array of SISTER cadastral property records.",
        data_notes="Record property names are defined by the configured SISTER model view and can vary by deployment.",
    ),
    ParcelApiFieldCatalogEntry(
        section="address",
        fields="Up to ten distinct SISTER address strings; addresses are not geocoded.",
        source_keys=("sister",),
        provider_note="",
        method_scope="Exact cadastral-reference match; partial and dependent on cached property records.",
        version_freshness="The block does not currently report a dataset version or refresh schedule.",
        data_shape="Object.",
        stable_data_fields=("addresses", "count", "truncated"),
        provider_dynamic_fields=False,
        stable_field_descriptions=(
            ("addresses", "array of strings — up to ten distinct SISTER address strings; not geocoded."),
            ("count", "integer — number of returned distinct address strings."),
            ("truncated", "boolean — true when the source had more addresses than the response cap."),
        ),
    ),
    ParcelApiFieldCatalogEntry(
        section="anncsu_addresses",
        fields="Geocoded ANNCSU civic access points inside the parcel, with coordinate method and municipal coordinate coverage.",
        source_keys=("anncsu",),
        provider_note="A missing point match does not establish that the parcel has no address; coordinate submission is incomplete.",
        method_scope="Exact point-in-polygon match against parcel geometry in EPSG:4326; source coordinates are ETRS89/ETRF2000 (EPSG:4258).",
        version_freshness="ANNCSU regional extracts are published monthly; source vintage and local import time are returned when known.",
        data_shape="Object containing matched access records, coordinate method, and municipality coordinate coverage.",
        stable_data_fields=("addresses", "status", "address_count", "candidate_limit_reached", "truncated", "municipality_coordinate_coverage_pct"),
        provider_dynamic_fields=False,
        stable_field_descriptions=(
            ("addresses", "array — matched address components, coordinates and coordinate method."),
            ("status", "string — matched, partial or no_geocoded_match."),
            ("address_count", "integer — number of matched geocoded access points before the response cap."),
            ("candidate_limit_reached", "boolean — true when the spatial candidate cap may have omitted matches."),
            ("truncated", "boolean — true when matched results exceed the response cap."),
            ("municipality_coordinate_coverage_pct", "number | null — share of ANNCSU access records with coordinates in the parcel's municipality."),
        ),
    ),
    ParcelApiFieldCatalogEntry(
        section="nightlights",
        fields="Annual VIIRS Black Marble nighttime radiance sampled from the parcel polygon.",
        source_keys=("nasa_black_marble",),
        provider_note="A coarse annual radiance proxy; it does not measure parcel or building lighting directly.",
        method_scope=(
            "Mean of valid good-quality pixel centres inside the parcel; when none fall inside, the representative-point "
            "pixel is used and reported explicitly. A raster match outside Italy coverage or without good-quality pixels "
            "is not converted to zero."
        ),
        version_freshness="VNP46A4.002 annual composite; the observation year and local import time are returned. The app does not report a refresh schedule.",
        data_shape="Object with annual radiance, units, sample counts, coverage and match method.",
        stable_data_fields=(
            "intensity_nw_cm2_sr", "units", "valid_pixel_count", "sampled_pixel_count",
            "matched", "match_method", "quality_policy",
        ),
        provider_dynamic_fields=False,
        data_notes="The 15 arc-second grid is roughly 463 m north-south; small parcels can be represented by one pixel.",
        stable_field_descriptions=(
            ("intensity_nw_cm2_sr", "number — mean nighttime radiance in nW/(cm² sr), based on valid sampled pixels."),
            ("units", "string — radiance unit, nW/(cm² sr)."),
            ("valid_pixel_count", "integer — good-quality raster pixels used in the reported value."),
            ("sampled_pixel_count", "integer — pixels sampled within the parcel, or one representative-point pixel for subpixel parcels."),
            ("matched", "boolean — whether a good-quality pixel was available for the parcel sample."),
            ("match_method", "string — parcel pixel mean or representative-point pixel."),
            ("quality_policy", "string — only NASA quality flag 0 pixels are included."),
        ),
    ),
    ParcelApiFieldCatalogEntry(
        section="opendata",
        fields="Available cadastral document records.",
        source_keys=("sister", "openapi_catasto", "agenzia_entrate_cadastre"),
        provider_note=(
            "Each record reports its retrieval store, access service and reuse status. OpenData PostgreSQL records "
            "come through OpenAPI Catasto; OpenAPI names Agenzia delle Entrate as its principal source, but does "
            "not identify the source per stored record."
        ),
        method_scope="Cadastral-reference lookup with municipality and province context; results depend on configured stores.",
        version_freshness="Version and refresh schedule are not reported consistently by the configured sources.",
        data_shape="Array of provider-native records with a stable provenance object.",
        stable_data_fields=(
            "records[].provenance.record_id", "records[].provenance.endpoint",
            "records[].provenance.retrieval_source", "records[].provenance.recorded_at",
            "records[].provenance.query_datetime", "records[].provenance.match_method",
            "records[].provenance.provider", "records[].provenance.data_origin",
            "records[].provenance.provider.key", "records[].provenance.provider.name",
            "records[].provenance.provider.status", "records[].provenance.provider.basis",
            "records[].provenance.provider.provider_url",
            "records[].provenance.data_origin.key", "records[].provenance.data_origin.name",
            "records[].provenance.data_origin.status", "records[].provenance.data_origin.basis",
            "records[].provenance.data_origin.provider_url",
            "records[].provenance.data_origin.record_specific",
            "records[].provenance.reuse", "records[].provenance.reuse.status",
            "records[].provenance.reuse.license_name",
            "records[].provenance.reuse.license_basis",
            "records[].provenance.reuse.terms_url",
            "records[].provenance.reuse.terms_summary",
            "records[].provenance.reuse.attribution",
        ),
        provider_dynamic_fields=True,
        data_notes=(
            "Provider-native result fields follow the configured source. Unknown record origins or reuse terms are "
            "returned explicitly as unknown; an API-service provider is not treated as proof of downstream reuse rights."
        ),
        stable_field_descriptions=(
            ("records[].provenance.record_id", "string | integer | null — identifier assigned by the document or query store."),
            ("records[].provenance.endpoint", "string | null — source query endpoint or document type."),
            ("records[].provenance.retrieval_source", "string | null — database or cache that supplied this record to the app."),
            ("records[].provenance.recorded_at", "string | number | null — source record timestamp or creation time, in its stored representation."),
            ("records[].provenance.query_datetime", "string | null — query timestamp when the source stores one."),
            ("records[].provenance.match_method", "string | null — cadastral matching method used for this record or lookup."),
            ("records[].provenance.provider", "object — identified access/provider service, or an explicit unknown/uncatalogued status."),
            ("records[].provenance.provider.key", "string | null — maintained provider-catalog key."),
            ("records[].provenance.provider.name", "string | null — display name of the access/provider service."),
            ("records[].provenance.provider.status", "string — catalogued, uncatalogued or unknown."),
            ("records[].provenance.provider.basis", "string — evidence used to identify the provider."),
            ("records[].provenance.provider.provider_url", "string | null — provider or service documentation URL."),
            ("records[].provenance.data_origin", "object — record origin when known; OpenAPI's Agenzia delle Entrate origin is provider-stated, not record-specific."),
            ("records[].provenance.data_origin.key", "string | null — maintained catalog key for the stated data origin."),
            ("records[].provenance.data_origin.name", "string | null — display name of the stated data origin."),
            ("records[].provenance.data_origin.status", "string — catalogued, uncatalogued or unknown."),
            ("records[].provenance.data_origin.basis", "string — evidence and scope for the origin statement."),
            ("records[].provenance.data_origin.provider_url", "string | null — source documentation URL."),
            ("records[].provenance.data_origin.record_specific", "boolean | null — whether the stated origin is tied to this individual record."),
            ("records[].provenance.reuse", "object — licence, terms link, attribution and reuse status; no permission is inferred from missing terms."),
            ("records[].provenance.reuse.status", "string — reuse status, including provider_terms_reviewed_reuse_unconfirmed, downstream_reuse_unverified or unknown."),
            ("records[].provenance.reuse.license_name", "string | null — named licence when established."),
            ("records[].provenance.reuse.license_basis", "string — basis for the licence/reuse statement."),
            ("records[].provenance.reuse.terms_url", "string | null — terms reviewed for this access/provider service."),
            ("records[].provenance.reuse.terms_summary", "string | null — catalogued summary; not a substitute for the linked terms."),
            ("records[].provenance.reuse.attribution", "string | null — catalogued attribution text or citation."),
        ),
    ),
    ParcelApiFieldCatalogEntry(
        section="pvp",
        fields="Public-auction records matched to the parcel.",
        source_keys=("pvp",),
        provider_note="",
        method_scope="Parcel reference and municipality crosswalk against the PVP model view.",
        version_freshness="The block does not currently report a dataset version or refresh schedule.",
        data_shape="Array of public-auction records.",
        data_notes="Record property names follow the configured PVP model view and may vary by deployment.",
    ),
    ParcelApiFieldCatalogEntry(
        section="solar",
        fields="Municipality-wide solar aggregates; not a parcel roof estimate.",
        source_keys=("zornade_municipal_solar",),
        provider_note="Zornade municipal aggregate served through aecs4u-stats; no named reuse licence is established here.",
        method_scope="Cadastral municipality match; spatial resolution is the municipality.",
        version_freshness="Source dataset_version and updated_at are passed through when available; refresh cadence is not asserted.",
        data_shape="Object with optional municipality-wide solar aggregate values.",
        data_notes="The source model version identifies the PVGIS-SARAH3 vintage and Zornade methodology; these are modeled municipal aggregates, not measurements or parcel/roof estimates.",
        stable_data_fields=("pv_n_buildings", "pv_pvout_pessimistic_kwh_year_total", "pv_pvout_modern_kwh_year_total", "pv_pvout_per_capita_kwh", "pv_kwp_max_total", "pv_high_viability_pct", "pv_medium_viability_pct", "pv_low_viability_pct", "pv_not_eligible_pct", "pv_observation_count"),
        provider_dynamic_fields=False,
        stable_field_descriptions=(
            ("pv_n_buildings", "integer — buildings in the municipality solar aggregate."),
            ("pv_pvout_pessimistic_kwh_year_total", "number — estimated annual output in kWh for the pessimistic scenario."),
            ("pv_pvout_modern_kwh_year_total", "number — estimated annual output in kWh for the modern scenario."),
            ("pv_pvout_per_capita_kwh", "number — annual output in kWh per resident."),
            ("pv_kwp_max_total", "number — estimated aggregate peak capacity in kWp."),
            ("pv_high_viability_pct", "number — share of assessed buildings with high viability, percent."),
            ("pv_medium_viability_pct", "number — share of assessed buildings with medium viability, percent."),
            ("pv_low_viability_pct", "number — share of assessed buildings with low viability, percent."),
            ("pv_not_eligible_pct", "number — share of assessed buildings marked ineligible, percent."),
            ("pv_observation_count", "integer — source observations represented, when provided."),
        ),
    ),
    ParcelApiFieldCatalogEntry(
        section="valuation",
        fields="OMI zone, quotations and available PostgreSQL snapshot.",
        source_keys=("omi", "ondata_omi_boundaries"),
        provider_note=(
            "When the local boundary mirror supplies the match, its 2018/2 vintage is approximate; "
            "quote vintage and boundary vintage are separate."
        ),
        method_scope=(
            "Parcel centroid matched with PostGIS ST_Covers or local point-in-polygon; the local mirror may be stale. "
            "Quote scope is the selected zone/municipality."
        ),
        version_freshness=(
            "OMI release or quote semester is returned when available. Local zone matches report boundary_vintage "
            "separately; the application import schedule is not guaranteed."
        ),
        data_shape="Object containing a zone match, OMI quotes and an optional PostgreSQL snapshot.",
        stable_data_fields=("zone", "quotes", "postgres_snapshot"),
        data_notes=(
            "Quote and snapshot fields follow the OMI source schema and can change with its release. "
            "The local boundary mirror reports its source, vintage, spatial method and resolution when matched."
        ),
        stable_field_descriptions=(
            ("zone", "object | null — matched OMI-zone context."),
            ("zone.boundary_source", "string | optional — local OMI boundary mirror source."),
            ("zone.boundary_vintage", "string | optional — boundary geometry vintage, separate from quote release/semester."),
            ("zone.match_method", "string | optional — PostGIS ST_Covers or local point-in-polygon."),
            ("zone.spatial_resolution", "string | optional — OMI zone polygon scope."),
            ("quotes", "array — OMI quote records; quote fields follow the source release."),
            ("postgres_snapshot", "object | null — indexed OMI snapshot when available."),
        ),
    ),
    ParcelApiFieldCatalogEntry(
        section="coastal_erosion",
        fields="Mapped shoreline-change segments within 1 km of the parcel.",
        source_keys=("ispra_coastal_dynamics",),
        provider_note="Historical 2006–2020 shoreline change; not a current erosion forecast. No nearby mapped segment is an unknown-coverage result.",
        method_scope="Local spatial intersection against the parcel polygon expanded to a 1 km distance; candidate limit 100.",
        version_freshness="ISPRA Linea di Costa 2020 v2.0, shoreline dynamics 2006–2020; the local snapshot import time is returned, but source update cadence is not reported here.",
        data_shape="Object with source metadata, per-class counts and up to 25 nearby segment summaries.",
        stable_data_fields=("available", "matched", "coverage_status", "search_radius_m", "positional_accuracy_m", "classification_threshold_m", "segment_count", "classification_counts", "nearest_segment", "segments", "query_complete"),
        provider_dynamic_fields=False,
        data_notes="Erosion is inland movement over 5 m; advance is seaward movement over 5 m; changes within ±5 m are stable. The dataset excludes some highly variable coastal segments.",
        stable_field_descriptions=(
            ("available", "boolean — whether the local ISPRA snapshot was queried successfully."),
            ("matched", "boolean — whether a mapped shoreline-change segment was found within the search radius."),
            ("coverage_status", "string — mapped segments found, no mapped segment within 1 km, partial query, or unavailable."),
            ("search_radius_m", "number — maximum parcel-to-segment distance, 1,000 m."),
            ("positional_accuracy_m", "number — ISPRA documented positional accuracy, 5 m."),
            ("classification_threshold_m", "number — shoreline movement classification threshold, 5 m."),
            ("segment_count", "integer — returned mapped segments within the radius, capped at 100."),
            ("classification_counts", "object — counts grouped by ISPRA class."),
            ("nearest_segment", "object | null — nearest segment summary and parcel distance."),
            ("segments", "array — up to 25 nearest segment summaries."),
            ("query_complete", "boolean — false when the local candidate cap was reached."),
        ),
    ),
    ParcelApiFieldCatalogEntry(
        section="risk",
        fields="DPC municipal seismic class, INGV MPS04 nearest-grid PGA, ISPRA parcel hazards and coastal dynamics, EGMS subsidence, source-gated protection records, and an optional municipality-level composite context index.",
        source_keys=("dpc_seismic", "ingv_mps04", "ispra_pai_pgra", "ispra_coastal_dynamics", "copernicus_egms", "mibac_protection_sources"),
        provider_note="DPC classification is municipality-level; MPS04 is a nearest-grid estimate. See component metadata for each source. The local DPC vintage may not be reported.",
        method_scope="DPC municipality lookup, MPS04 parcel-centroid nearest native-grid point (10% exceedance in 50 years), ISPRA polygon intersection, area-weighted overlap of 100 m EGMS cells, and parcel intersection against provisioned protection records; component scopes remain distinct.",
        version_freshness="MPS04 model version and ISPRA/EGMS/protection source versions are component-level; DPC version and refresh timing may be unavailable.",
        data_shape="Object containing independently available risk components.",
        stable_data_fields=(
            "seismic_zone", "mps04_pga", "parcel_hazards", "subsidence",
            "coastal_erosion", "composite_score", "composite_score_version",
            "composite_score_status", "composite_score_components",
            "composite_score_missing_components", "composite_score_invalid_components",
            "composite_score_component_count", "composite_score_component_total",
            "composite_score_formula", "composite_score_interpretation",
            "protection_constraints", "missing_components",
        ),
        data_notes="MPS04 is sampled at the parcel centroid and explicitly reports unavailable or no-match states. coastal_erosion is independently available when the ISPRA local snapshot is provisioned. protection_constraints is available only when its upstream table exists; per-type coverage remains explicit and catalogue-only records are not binding constraints. composite_score is a versioned heuristic municipality-level index with explicit partial-data status; it is not a parcel risk estimate or probability.",
        stable_field_descriptions=(
            ("seismic_zone", "object — DPC municipality classification, independently available."),
            ("mps04_pga", "object — INGV MPS04 PGA at the parcel centroid's nearest native 0.05° grid point for 10% exceedance probability in 50 years; includes method, match distance and explicit availability/no-match state."),
            ("parcel_hazards", "object — ISPRA flood and landslide parcel intersections, independently available."),
            ("subsidence", "object — EGMS-derived subsidence intersection summary, independently available."),
            ("coastal_erosion", "object — ISPRA 2006–2020 shoreline-change segments within 1 km, independently available when the local store is built."),
            ("protection_constraints", "object — source-gated SITAP, Vincoli in Rete and Catalogo Generale parcel matches; includes per-type coverage, binding status and a result cap."),
            ("protection_constraints.status", "string — published or unavailable; published means the source table was queried, not that national coverage is complete."),
            ("protection_constraints.constraints", "array — up to 100 intersecting source records, including category, act, binding status, source release and overlap where calculable."),
            ("protection_constraints.coverage", "object — landscape and cultural_heritage each report covered or unknown for the whole parcel."),
            ("protection_constraints.truncated", "boolean — true when more than 100 intersecting records exist."),
            ("composite_score", "number | null — municipal_hazard_index_v1, an equally weighted heuristic index of DPC seismic zone, IdroGEO landslide P3+P4 area share and flood P3 area share; null when no component is available."),
            ("composite_score_version", "string | null — version identifier for the shared municipal hazard index."),
            ("composite_score_status", "string | null — complete, partial, insufficient_data or unavailable."),
            ("composite_score_components", "object | null — available component values used to calculate the municipal index."),
            ("composite_score_missing_components", "array of strings | null — index components omitted from the score due to unavailable or invalid source values."),
            ("composite_score_invalid_components", "array of strings | null — components omitted because a source value was invalid."),
            ("composite_score_component_count", "integer | null — number of component scores included."),
            ("composite_score_component_total", "integer | null — number of defined index components."),
            ("composite_score_formula", "string | null — documented equal-weight formula and component mappings."),
            ("composite_score_interpretation", "string | null — the stated limits and municipality-level scope of the index."),
            ("missing_components", "array of strings — unavailable components, including composite_score."),
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
