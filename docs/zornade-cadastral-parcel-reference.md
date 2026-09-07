# Zornade cadastral-parcel data reference

> Research note prepared from the authenticated Zornade web app on 2026-09-03. No API keys, session tokens, or authentication URLs are recorded here.

## Executive summary

Zornade exposes a cadastral-parcel detail endpoint that combines the parcel geometry and identity with 20 optional enrichment blocks. The catalog describes roughly 85 million parcels, 7,899 municipalities, and 15+ institutional or public-data sources.

The central endpoint is:

```http
GET https://api.zornade.com/api/v2/parcels/{id}
```

`id` may be either the numeric Zornade `fid` or an Agenzia delle Entrate `gml_id`. All API v2 endpoints are GET-only. Parcel requests require an API token in the `x-api-key` header and the `parcels:read` scope.

Use `include=` to request a smaller response, for example:

```http
GET /api/v2/parcels/12345?include=basic,risk,valuation
```

The documentation also presents an all-data form with no `include` parameter. For production clients, selective includes are preferable because geometry, historical valuation, POIs, and per-building solar details can make the response large.

## Include-block index

| Include | Main subject | Availability / important limitation |
|---|---|---|
| `basic` | Parcel identity, geometry, area, municipality | Core cadastral layer; ~85M parcels |
| `cadastral` | Foglio, urban section, Belfiore code, CAP | CAP is sub-municipal and spatially joined |
| `address` / `addresses` | Civic addresses | Up to 20 intersecting addresses; uneven coverage |
| `risk` | Seismic, flood, and landslide fields | Risk subfields are sourced from different layers |
| `subsidence` | InSAR ground motion | Spatially uneven PSI coverage |
| `terrain` | Elevation, slope, aspect, ruggedness | 10 m DEM-derived statistics; national coverage |
| `population` | Dasymetric parcel population estimate | Modelled estimate, not a census count |
| `buildings` | Building count, footprints, residential split | OSM/GBA dependent; footprint may exceed parcel area |
| `economics` | MEF income, brackets, Gini, affordability | CAP-level aggregation; privacy threshold |
| `demographics` | ISTAT census-section population and composition | Section-level values, not parcel-level observations |
| `land_cover` | CORINE Land Cover level 3 | One centroid-matched class |
| `land_use` | Urban Atlas urban land use | Only Functional Urban Areas; otherwise `null` |
| `valuation` | Current OMI prices and rents | Zone-level ranges; multiple property types/conditions |
| `valuation_history` | 22-semester OMI history | Rural zones may have code only, no prices |
| `coastal_erosion` | Shoreline change and coastal context | Parcels within 1 km of coast; up to five records |
| `cultural_heritage` | MiC/SIGECweb protected assets | Array of intersecting assets |
| `solar` | Per-building PV techno-economic model | Buildings ≥25 m² in GBA; modelled output |
| `poi` | Foursquare places on the parcel | Active places only; geometry intersection |
| `nightlights` | VIIRS/Black Marble radiance | Dasymetric proxy; 2023 composite in catalog |

## Core parcel identity: `basic`

Endpoint: `/parcels/{id}?include=basic`

Source and processing:

- Primary source: [Agenzia delle Entrate cadastral cartography WFS](https://www.agenziaentrate.gov.it/portale/web/guest/schede/fabbricatiterreni/consultazione-cartografia-catastale).
- Coverage: about 85 million parcels and 7,899 municipalities. Bolzano and Trento are supplemented from provincial portals because they are not covered by the AdE WFS.
- Freshness: WFS snapshot described as continuously updated; the `parcels` table is regenerated on request.
- Ingestion paginates by municipality with a 16 km buffer, validates geometries with `ST_MakeValid`, and checks expected counts using `wfs_parcel_count`.
- License stated by the catalog: free reuse subject to AdE conditions.

| Field | Type | Meaning |
|---|---|---|
| `fid` | integer | Zornade's internal numeric identifier; stable for a snapshot |
| `gml_id` | string | AdE GML identifier, in the form `CadastralParcel.IT.AGE.PLA.<comune><foglio><particella>.<lettera>` |
| `label` | string | Parcel number/letter shown on the cadastral map |
| `cadastral_reference` | string | Complete cadastral reference (foglio/parcel/sub), when supplied by the WFS |
| `area_m2` | number | Area in square metres; calculated after transforming to EPSG:3857 and applying `ST_Area` |
| `centroid` | object `{lat, lng}` | Geometric centroid in WGS84 / EPSG:4326 |
| `geometry` | GeoJSON Polygon | 2D WGS84 parcel geometry; excluded by default and included through `include=` |
| `municipality` | object | Contains `code` (Belfiore cadastral code), `name`, `province`, and `region` |

## Cadastral sheet, section, and CAP: `cadastral`

Endpoint: `/parcels/{id}?include=cadastral`

- Source: AdE for cadastral sheets plus [ISTAT sub-municipal CAP data](https://www.istat.it/it/archivio/156224).
- Coverage: about 302,000 cadastral sheets and 9,200 Italian sub-municipal CAPs.
- Freshness: sheet geometries are refreshed with each WFS rebuild; CAP data is listed as ISTAT 2024.
- Construction: the parcel centroid is tested with `ST_Contains` against `catasto_fogli` and `cap_subcomunali`; the municipality code is read from `parcels.administrativeunit`. The precomputed match is retained in `parcel_catasto`.
- License stated: CC BY 3.0 IT for ISTAT data.

| Field | Type | Meaning |
|---|---|---|
| `foglio` | string | Cadastral sheet number, combined with `sezione_urbana` where present |
| `sezione_urbana` | string | Administrative urban section, where applicable |
| `comune_code` | string | Belfiore cadastral municipality code, e.g. `H501` for Rome |
| `postal_code` | string | Sub-municipal CAP derived from a centroid spatial join |

## Addresses: `address` and `addresses`

Endpoint: `/parcels/{id}?include=address` (the catalog also names `addresses` for the complete list)

- Sources: [OpenAddresses](https://openaddresses.io/) and municipal feeds, including major-city feeds.
- Coverage: about 18.7 million Italian civic addresses, with uneven regional coverage.
- Freshness: OpenAddresses snapshot 2024; Rome, Milan, Turin, and Naples are supplemented by municipal feeds.
- `parcel_address` stores a deterministic primary address and a total count. The complete list is resolved at runtime with `ST_Intersects` on `addresses.geom`.
- Maximum: 20 addresses per call. The catalog warns that some municipal feeds contain exact duplicate civic records.
- License stated: mixed CC0 / ODbL depending on source.

| Field | Type | Meaning |
|---|---|---|
| `address` | object or `null` | Deterministic primary address: `street`, `number`, `exponent`, `locality` |
| `addresses[]` | array | Up to 20 intersecting addresses; each object has `street`, `number`, `exponent`, and `locality` |

## Risk data: `risk`

The `risk` block groups at least the following three distinct enrichment families.

### Seismic fields

Endpoint notation in the catalog: `/parcels/{id}?include=risk (seismic_zone, pga)`

- Source: [INGV](http://esse1-gis.mi.ingv.it/).
- Coverage: 100% of Italy through OPCM 3274/2003 zoning and regional updates.
- Freshness: MPS04 (INGV 2004) remains the catalog's normative reference; MPS19 is described as being progressively adopted.
- Construction: centroid-to-polygon `ST_Intersects` against `ingv_zonesismiche`; in transition areas the most recent ordinance polygon wins. Precomputed in `parcel_risk`.
- License stated: CC BY 4.0.

| Field | Type | Meaning |
|---|---|---|
| `seismic_zone` | integer 1–4 | Zone 1 is very high hazard; zone 4 is very low hazard |
| `pga` | number in g | Peak Ground Acceleration with 10% probability of exceedance in 50 years |

### Flood fields

- Source: [ISPRA PGRA hydrogeological risk portal](https://idrogeo.isprambiente.it/).
- Coverage: all seven Italian river-basin districts, PGRA 2021–2027 cycle.
- Freshness: 2021 plan, with partial 2024 updates.
- Construction: the centroid level and the full parcel geometry are both considered. The worst intersecting class is selected using `HPH > MPH > LPH`.
- License stated: CC BY 4.0.

| Field | Type | Meaning |
|---|---|---|
| `flood_level` | string or `null` | `HPH` high, `MPH` medium, `LPH` low, or `null`; returned inside `risk` |

### Landslide fields

- Sources: [ISPRA IFFI](https://www.isprambiente.gov.it/it/progetti/cartella-progetti-in-corso/suolo-e-territorio-1/iffi-inventario-dei-fenomeni-franosi-in-italia) plus the seven district basin authorities.
- Coverage: 100% Italy; catalog cites about 620,000 mapped landslides.
- Freshness: district PAI updates and IFFI release 2024.
- Construction: PAI layers are merged into `ispra_frane`; a lateral join over parcel geometry ranks intersecting levels with `P4 > P3 > P2 > P1 > AA`.
- License stated: CC BY 4.0.

| Field | Type | Meaning |
|---|---|---|
| `landslide_level` | string or `null` | Worst level: `P1`, `P2`, `P3`, `P4`, `AA` (attention area), or `null`; returned inside `risk` |

## Ground motion / subsidence: `subsidence`

Endpoint: `/parcels/{id}?include=subsidence`

- Source: [Copernicus European Ground Motion Service](https://land.copernicus.eu/pan-european/european-ground-motion-service).
- Coverage: strongest in the Po Valley, Adriatic coast, Rome, Naples, and volcanic areas; uneven over the Apennines and Alps.
- Freshness: PSI dataset for 2014–2023, processed from Sentinel-1.
- Construction: PSI points are matched to the parcel centroid. The current catalog explicitly flags `LIMIT 1` as non-deterministic if multiple points qualify; a nearest-point `ORDER BY centroid <-> geom LIMIT 1` with `ST_DWithin` is planned.
- License stated: Copernicus Data and Information Policy, free reuse.

| Field | Type | Meaning |
|---|---|---|
| `velocity_mm_year` | number mm/year | Line-of-sight velocity; negative means lowering/subsidence |
| `acceleration` | number mm/year² | Change in velocity, used as a stability proxy |
| `risk_class` | integer 0–4 | 0 stable; 4 severe subsidence |
| `risk_label` | string | `Stabile`, `Moderata`, `Significativa`, `Severa`, or `Estrema` |
| `risk_index` | number 0–1 | Normalized composite of velocity and acceleration |
| `direction` | string | `subsidence`, `uplift`, or `stable` |
| `trend` | string | `accelerating`, `decelerating`, or `linear` |

## Terrain: `terrain`

Endpoint: `/parcels/{id}?include=terrain`

- Source: [INGV TINItaly DEM](https://tinitaly.pi.ingv.it/).
- Coverage: 100% Italy at 10 m horizontal resolution.
- Freshness: TINItaly DEM v1.1, released 2023; derived rasters use GDAL DEM utilities.
- Construction: zonal statistics over the parcel for the DEM and derived slope, TRI, and aspect rasters; output is stored in `parcel_terrain`.
- License stated: CC BY 4.0.

| Field | Type | Meaning |
|---|---|---|
| `elevation_min` | number m | Minimum elevation in parcel |
| `elevation_max` | number m | Maximum elevation |
| `elevation_mean` | number m | Mean elevation |
| `elevation_std` | number m | Standard deviation of elevation / terrain heterogeneity |
| `ruggedness_index` | number m | Elevation range, `max - min` |
| `slope_mean` | number degrees | Mean slope from derived raster |
| `slope_max` | number degrees | Maximum slope, 0–90° |
| `tri_mean` | number m | Mean Terrain Ruggedness Index, after Riley et al. 1999 |
| `aspect_predominant` | string | `N`, `NE`, `E`, `SE`, `S`, `SW`, `W`, `NW`, or `flat` |

## Modelled parcel population: `population`

Endpoint: `/parcels/{id}?include=population`

- Sources: [WorldPop](https://www.worldpop.org/) plus OSM building footprints.
- Coverage: 100% Italy; accuracy is better where OSM/GBA building coverage is high.
- Freshness: WorldPop 2025, 30 m raster, plus building footprints listed as updated 2026-05.
- Construction: municipal WorldPop budget, parcel raster statistics, then proportional redistribution by residential footprint:

  `municipality_total × residential_footprint_parcel / residential_footprint_municipality`

- Parcels without buildings receive an estimate of zero. If the municipality has no residential footprint, a total-building-footprint fallback is used.
- Match to the ISTAT municipality is spatial and centroid-based.
- Privacy/licensing: CC BY 4.0 for WorldPop and ODbL for OSM.

| Field | Type | Meaning |
|---|---|---|
| `estimated` | number | Dasymetric resident estimate for the parcel |
| `sum` | number | Raw sum of WorldPop pixels within parcel |
| `pixel_count` | integer | Number of intersected WorldPop pixels |
| `mean` | number | Mean WorldPop value over intersected pixels |
| `dasymetric` | number | Dasymetric component of the estimate |
| `municipality_total` | number | Municipal WorldPop total over ISTAT boundary |
| `estimation_method` | string | `hybrid`, `raster_only`, or `fallback_legacy` |
| `estimation_confidence` | number 0–1 | Relative confidence in the estimate |
| `source` | string | Provenance string such as `worldpop_2025_30m+osm_dasymetric` |

## Buildings: `buildings`

Endpoint: `/parcels/{id}?include=buildings`

- Sources: [OpenStreetMap](https://www.openstreetmap.org/) and the Geoportale Nazionale GBA building atlas.
- Coverage: OSM nationwide; GBA is the official Geoportale coverage.
- Freshness: OSM snapshot 2026-05, listed as monthly; GBA version 2024.
- Construction: OSM footprints are intersected with the parcel and residential status is inferred from tags such as `building=residential`, `apartments`, or `house`. GBA provides an alternative and validation source. Output is stored in `parcel_buildings`.
- License stated: ODbL for OSM; official reuse for GBA.

| Field | Type | Meaning |
|---|---|---|
| `count` | integer | Total intersecting building footprints |
| `footprint_m2` | number m² | Total footprint area; may exceed parcel area when buildings are multi-storey |
| `residential_count` | integer | Footprints classified as residential |
| `residential_footprint_m2` | number m² | Cumulative residential footprint area |
| `source` | string | `osm`, `gba`, or `merged` |

## Economics: `economics`

Endpoint: `/parcels/{id}?include=economics`

- Source: [MEF Department of Finance income-tax declarations](https://www1.finanze.gov.it/finanze/analisi_stat/public/index.php).
- Coverage: CAPs with declarations; MEF suppresses CAPs with fewer than three declarants.
- Freshness: the latest MEF release available in Zornade at the time of the dataset refresh.
- Construction: MEF data is aggregated at CAP level. Gini is computed at runtime from the IRPEF income brackets. Affordability combines the median bracket with the median residential OMI price; parcel-to-CAP matching uses the centroid.
- License stated: free reuse for open MEF data.

| Field | Type | Meaning |
|---|---|---|
| `average_income` | number EUR | Mean taxable income per declarant |
| `taxpayers` | integer | Number of declarants in the CAP |
| `total_income` | number EUR | Aggregate income in the CAP |
| `net_tax` | number EUR | Total net IRPEF in the CAP |
| `tax_year` | integer | Tax year of the declarations |
| `gini_index` | number 0–1 | Gini coefficient estimated from IRPEF bracket frequencies |
| `affordability_index` | number years | Years of mean income needed for a standard apartment at median OMI price |
| `affordability_apt_size_m2` | number m² | Apartment size used; default is 80 m² |
| `avg_residential_price_m2` | number EUR/m² | Median residential OMI price for the CAP |
| `income_brackets` | object | `lte_zero`, `from_0_to_10k`, `from_10k_to_15k`, `from_15k_to_26k`, `from_26k_to_55k`, `from_55k_to_75k`, `from_75k_to_120k`, `over_120k` |

## ISTAT micro-demographics: `demographics`

Endpoint: `/parcels/{id}?include=demographics`

- Source: [ISTAT Permanent Census of Population and Housing](https://www.istat.it/it/censimenti/popolazione-e-abitazioni).
- Coverage: about 756,000 Italian census sections.
- Freshness: 2021 census edition, released 2023.
- Construction: `census_sections` contains section-level aggregates. Parcel-to-section matching uses centroid `ST_Contains`.
- Small counts may be rounded or suppressed for privacy. These values describe the matched census section, not a direct parcel survey.
- License stated: CC BY 3.0 IT.

| Field | Type | Meaning |
|---|---|---|
| `population_total` | integer | Total resident population in the section |
| `male` / `female` | integer | Gender split |
| `age_brackets` | object | Five-year classes from `0_4` through `75_plus` |
| `education` | object | `no_title`, `elementary`, `middle_school`, `high_school`, `university` frequencies |
| `employed` | integer | Resident employed population |
| `foreigners` | object | `total`, `eu`, and `non_eu` |
| `households` | object | `1_member` through `6_plus_members`, plus `total` |
| `dwellings` | object | `total`, `occupied`, and `vacant` |

## Land cover: `land_cover`

Endpoint: `/parcels/{id}?include=land_cover`

- Source: [Copernicus CORINE Land Cover](https://land.copernicus.eu/pan-european/corine-land-cover), EEA.
- Coverage: 100% Italy.
- Freshness: CLC 2018 is the latest confirmed release in the catalog; CLC 2024 is described as pre-release.
- Construction: `corine_landcover` is loaded from the Copernicus portal and matched to the parcel by centroid `ST_Intersects`; the precomputed match is stored in `parcel_landcover`.
- License stated: Copernicus Data Policy, free reuse including commercial reuse.

| Field | Type | Meaning |
|---|---|---|
| `code` | string | Three-digit CLC level-3 code, e.g. `112` for discontinuous urban fabric |
| `class` | string | Level-3 class label |
| `subclass` | string | Subclassification where available |
| `description` | string | Extended class description |

## Urban land use: `land_use`

Endpoint: `/parcels/{id}?include=land_use`

- Source: [Copernicus Urban Atlas](https://land.copernicus.eu/local/urban-atlas).
- Coverage: only European Functional Urban Areas; in Italy, the catalog describes roughly 80 cities above 50,000 inhabitants, including capitals and major centres.
- Freshness: Urban Atlas 2018, the latest Italian release listed.
- Parcels outside a covered FUA return `null` for this block by design.
- License stated: Copernicus Data Policy.

| Field | Type | Meaning |
|---|---|---|
| `code` | string | Five-digit Urban Atlas code, e.g. `11100` for continuous urban fabric |
| `class` | string | Urban Atlas class label |
| `level1` | string | Macro-category such as urban, industrial, green, agricultural, or water |
| `level2` | string | Level-2 subcategory |

## Current OMI valuation: `valuation`

Endpoint: `/parcels/{id}?include=valuation`

- Source: [Agenzia delle Entrate OMI](https://www.agenziaentrate.gov.it/portale/web/guest/schede/fabbricatiterreni/omi/banche-dati/quotazioni-immobiliari).
- Coverage: about 24,000 OMI zones, excluding some marginal areas.
- Freshness observed in the catalog: semester 2025-2, compared with 2025-1.
- Construction: OMI CSV value tables and municipal zone KML polygons are loaded into semester tables. The parcel centroid is joined to the OMI zone. The response returns all rows for the zone, so there may be several property types and conditions.
- License stated: free reuse for non-commercial purposes under AdE conditions.

| Field | Type | Meaning |
|---|---|---|
| `zone` | string | OMI zone code, e.g. `B29` |
| `zone_description` | string | Descriptive toponym |
| `fascia` | string | `B` central, `C` semi-central, `D` peripheral, `E` suburban, `R` extra-urban |
| `microzona` | integer | OMI micro-zone identifier |
| `municipality` | string | OMI municipality name |
| `property_type` | string | Residential, office, shop, warehouse, garage, industrial shed, etc. |
| `condition` | string | `NORMALE`, `OTTIMO`, or `SCADENTE` |
| `purchase.min_eur_m2` / `purchase.max_eur_m2` | number EUR/m² | Sale-price range |
| `rental.min_eur_m2` / `rental.max_eur_m2` | number EUR/m²/month | Rental range |
| `previous_semester` | object | Same type of information for the previous semester, used for trend comparison |

## OMI history: `valuation_history`

Endpoint: `/parcels/{id}?include=valuation_history`

- Source: AdE OMI historical archive, 2015/1 through 2025/2.
- Coverage: WFS parcels with an assigned urban OMI zone. Rural `Rxx` parcels return the zone code but may have no monetary values.
- Freshness: updated with OMI 2025/2; catalog says the result is precomputed in `parcel_omi_history` for a single-PK lookup.
- Scope in this release: `Abitazioni civili`, condition `NORMALE`.
- When OMI zoning changes between semesters, each historical entry preserves the zone active in that semester.
- License stated: free reuse for non-commercial purposes under AdE conditions.

| Field | Type | Meaning |
|---|---|---|
| `zone_current` | string | Current/latest OMI zone |
| `municipality_code` | string | Municipality code, e.g. `H501` |
| `property_type` | string | Always `Abitazioni civili` in this release |
| `condition` | string | Always `NORMALE` in this release |
| `semesters[]` | array | 22 chronological records from 2015/1 to 2025/2 |
| `semesters[].semester` | string | `YYYY_S`, e.g. `2024_2` |
| `semesters[].zone` | string | Zone active during that semester |
| `semesters[].purchase.{min,max,mid}_eur_m2` | number EUR/m² | Sale range; `mid` is min/max average; `null` for rural zones |
| `semesters[].rental.{min,max,mid}_eur_m2` | number EUR/m²/month | Rental range; `null` for rural zones |
| `semesters[].gross_yield_pct` | number % | `(rental_mid × 12) / purchase_mid × 100` |
| `metrics` | object or `null` | Planned CAGR, volatility, and drawdown analytics; currently `null` |
| `annotations` | object or `null` | Planned editorial zone-change annotations; currently `null` |
| `data_version` | string | Historical dataset identifier, e.g. `omi_2015_1..2025_2_v1` |

## Coastal erosion: `coastal_erosion`

Endpoint: `/parcels/{id}?include=coastal_erosion`

- Source: [ISPRA Information System on Coasts](https://www.isprambiente.gov.it/it/banche-dati/banche-dati-folder/sistema-informativo-coste).
- Coverage: Italian mainland and major islands, for parcels near the coast.
- Freshness: ISPRA dataset 2023.
- Construction: shoreline segments within 1 km of the parcel centroid are selected with `ST_DWithin`, ordered by distance, with a maximum of five records.
- License stated: CC BY 4.0.

| Field | Type | Meaning |
|---|---|---|
| `dynamic` | string | `erosion`, `accretion`, or `stable` |
| `avg_change_m_year` | number m/year | Mean shoreline change; negative means retreat |
| `max_change_m_year` | number m/year | Maximum recorded change on the segment |
| `severity_index` | number 0–1 | Composite severity index |
| `coast_type` | string | Sandy, rocky, pebbly, or artificial |
| `lithology` | string | Dominant lithology of the segment |
| `distance_m` | number m | Distance to the shoreline |

## Cultural and landscape heritage: `cultural_heritage`

Endpoint: `/parcels/{id}?include=cultural_heritage`

- Source: [MiC SIGECweb](https://sigecweb.beniculturali.it/), Institute for Cataloguing and Documentation.
- Coverage: about 230,000 formalized protected assets in Italy.
- Freshness: SIGECweb 2024.
- Construction: MiC vector data is loaded into `vincoli_culturali`; parcel geometry is matched against polygon, line, and point assets with `ST_Intersects`.
- License stated: free reuse for non-commercial purposes.

| Field | Type | Meaning |
|---|---|---|
| `id` | string | SIGECweb identifier |
| `name` | string | Official asset name |
| `type` | string | Type such as fountain, palace, church, or archaeological area |
| `class` | string | Cataloguing class |
| `address` | string | Asset address |
| `municipality` | string | Municipality containing the asset |
| `province` | string | Province abbreviation |
| `catalog_code` | string | Historical MiC catalogue code |

## Photovoltaic potential: `solar`

Endpoint: `/parcels/{id}?include=solar`

This is a deterministic Zornade model, not a direct official measurement or an entitlement/engineering assessment.

- Sources: PVGIS-SARAH3 v5.3 from the [JRC PVGIS service](https://re.jrc.ec.europa.eu/pvg_tools/en/), GBA buildings, CORINE 2018, and PNRR LiDAR.
- Coverage: GBA buildings with footprint at least 25 m² throughout Italy.
- Freshness: TMY irradiation 2005–2023 with 2020 baseline; CAPEX curve Italy 2025.
- Methodology version observed: Zornade `v1.1`.
- Pipeline stages: GBA footprint and CORINE use class; 36-bin LiDAR skyline; 8760-hour PVGIS TMY; `pvlib` physical model; CAPEX/autoconsumption/CES/RID financial model; five-level validation against PVGIS, GSE Atlaimpianti, and Terna.
- The model uses a deterministic seed of 42. The `data_version`, `methodology_version`, and `updated_at` fields should be retained with any downstream analysis.
- License stated: PVGIS free reuse; GBA official reuse; Zornade pipeline proprietary.

| Field | Type | Meaning |
|---|---|---|
| `n_buildings` | integer | Number of evaluated buildings |
| `kwp_max_total` | number kWp | Total installable capacity |
| `pvout_modern_kwh_year_total` | number kWh/year | Modern scenario output: 10% system loss plus 3% soiling |
| `pvout_pessimistic_kwh_year_total` | number kWh/year | Conservative scenario output with 14% losses |
| `lcoe_eur_per_kwh_avg` | number EUR/kWh | Capacity-weighted average levelized cost of energy |
| `npv_20y_eur_total` | number EUR | 20-year NPV at 5% discount, including the stated self-consumption/CES/RID assumptions |
| `capex_eur_total` | number EUR | Estimated initial investment |
| `payback_years_simple_avg` | number years | Mean simple payback time |
| `viability_class_max` | string | `alta`, `media`, `bassa`, or `non_idoneo`; best-building class |
| `roof_pitch_avg_deg` | number degrees | Footprint-weighted mean roof pitch |
| `roof_aspect_avg_deg` | number degrees | Mean roof azimuth; 0 north, 180 south |
| `buildings[]` | array | Per-building fields listed below |
| `data_version` | string | PVGIS/model/build identifiers |
| `methodology_version` | string | Current methodology identifier, `v1.1` |
| `updated_at` | ISO 8601 string | Parcel recalculation timestamp |

Per-building `buildings[]` fields include:

`building_fid`, `roof_shape`, `roof_pitch_deg`, `roof_aspect_deg`, `roof_area_real_m2`, `roof_geometry_regime`, `kwp_max`, `pvout_modern_kwh_year`, `pvout_pessimistic_kwh_year`, `kwh_per_kwp_year_modern`, `kwh_per_kwp_year_pessimistic`, `capex_eur`, `lcoe_eur_per_kwh`, `npv_20y_eur`, `payback_years_simple`, `viability_class`, `bipv_eligible`, `confidence_level`, `shading_data_source`, and `exclusion_reason`.

## Points of interest: `poi`

Endpoint: `/parcels/{id}?include=poi`

- Source: [Foursquare Open Source Places](https://opensource.foursquare.com/os-places/).
- Coverage: about 2.8 million active Italian POIs.
- Freshness: quarterly Foursquare snapshot.
- Construction: `fsq_places` excludes records where `date_closed IS NULL` is false, then uses parcel-geometry `ST_Intersects`.
- License stated: Apache 2.0.

| Field | Type | Meaning |
|---|---|---|
| `id` | string | Foursquare venue ID |
| `name` | string | Commercial or venue name |
| `categories` | string | Standardized category path, e.g. `[Food > Café]` |
| `address` | string | POI address |
| `locality` | string | Locality or neighbourhood |
| `coordinates` | object `{lat, lng}` | POI coordinates |

## Night-time lights: `nightlights`

Endpoint: `/parcels/{id}?include=nightlights`

- Source: [NASA Black Marble VIIRS VNP46A4 Collection 2](https://blackmarble.gsfc.nasa.gov/).
- Coverage: 100% Italy, but with fallback behavior where buildings or valid VIIRS pixels are missing.
- Freshness: annual VNP46A4 composite; the catalog names 2023 as the current release year.
- Construction: municipal VIIRS mean is computed first, then distributed to parcels proportionally by residential OSM footprint. Without OSM buildings the method is uniform; with no valid VIIRS pixels it is `no_ntl`.
- Native raster resolution is approximately 463 m and NASA already applies stray-light, lunar, and atmospheric corrections.
- This is a proxy for activity/anthropization, not a direct measure of income or electricity use.
- License stated: NASA Earth Science Data, free reuse including commercial reuse with citation.

| Field | Type | Meaning |
|---|---|---|
| `available` | boolean | Whether a `parcel_nightlights` record exists; if false, other fields are absent |
| `ntl_parcel` | number nW/cm²/sr | Parcel radiance assigned by residential-footprint dasymetry |
| `ntl_density` | number `(nW/cm²/sr)/km²` | `ntl_parcel / area_m2 × 1,000,000` |
| `ntl_class` | string | `dark` <0.5, `dim` 0.5–3, `medium` 3–10, `bright` 10–30, `very_bright` ≥30 |
| `ntl_share` | number 0–1 | Parcel's share of the municipal VIIRS budget; municipality total is approximately 1 |
| `ntl_mean_comune` | number nW/cm²/sr | Mean VIIRS radiance of the municipality |
| `estimation_method` | string | `dasymetric`, `uniform`, or `no_ntl` |
| `ntl_year` | integer | Composite year, e.g. 2023 |
| `source` | string | e.g. `viirs_vnp46a4_c2_2023` |
| `updated_at` | ISO 8601 string | Last parcel refresh timestamp |

## API usage and operational reference

### Authentication and scopes

The API documentation says:

- Base URL: `https://api.zornade.com/api/v2`.
- `GET /health` is unauthenticated; other endpoints require `x-api-key`.
- Do not send a JWT `Authorization: Bearer ...` header to API v2.
- Required scope for parcel search, locate, and detail: `parcels:read`.
- Other listed scopes are `geocoding:read` and `admin_data:read`.

Example shape, with a placeholder only:

```bash
curl -H "x-api-key: zrn_il_tuo_token" \
  "https://api.zornade.com/api/v2/parcels/28007851?include=basic,risk,valuation"
```

### Related endpoints

| Endpoint | Scope | Purpose |
|---|---|---|
| `/health` | none | Service status, version, and available endpoints |
| `/geocode/search` | `geocoding:read` | Direct text geocoding; up to 50 results |
| `/geocode/reverse` | `geocoding:read` | Reverse geocoding; radius up to 500 m |
| `/admin/regions` | `admin_data:read` | Region list |
| `/admin/provinces` | `admin_data:read` | Provinces, optionally filtered by region |
| `/admin/municipalities` | `admin_data:read` | Municipalities, filterable by province/region |
| `/parcels/search` | `parcels:read` | Search by municipality, foglio, label, and section |
| `/parcels/locate` | `parcels:read` | Point, multipoint, or bounding-box parcel lookup |
| `/parcels/{id}` | `parcels:read` | Full parcel detail with selective includes |

### Errors

The documented error body is `{ "error": "...", "message": "..." }`.

| HTTP | Documented meaning / code |
|---:|---|
| 400 | Invalid or missing parameters: `INVALID_PARAMS`, `OUT_OF_BOUNDS` |
| 401 | Missing/invalid key: `API_KEY_REQUIRED`, `INVALID_API_KEY` |
| 403 | Insufficient scope: `INSUFFICIENT_SCOPE` |
| 404 | Missing resource: `NOT_FOUND` |
| 405 | Unsupported method; API is GET-only |
| 429 | Rate limit exceeded: `RATE_LIMITED` |
| 502 | Database error: `QUERY_ERROR` |

### Rate-limit note

The same UI contains inconsistent limits that should be verified before building a high-volume client:

- The API landing and token page advertise 10,000 requests/hour.
- The response-header documentation says `X-RateLimit-Limit` is 1,000.
- The live interactive preview showed `X-RateLimit-Remaining: 999`, consistent with a 1,000-request limit for that preview/token.

Treat the response headers as authoritative at runtime and implement backoff for HTTP 429 using `retry_after_seconds` when supplied.

Documented response headers:

- `X-RateLimit-Limit`
- `X-RateLimit-Remaining`
- `X-RateLimit-Reset` (Unix timestamp)
- `Cache-Control`: `public, max-age=3600` for admin data and `no-store` for data responses

### Recommended client behavior

1. Call `/health` before business requests and record its runtime `version`.
2. Send only `x-api-key` to API v2.
3. Use `include=` to reduce payload and latency.
4. Log HTTP status, `error`, `message`, and rate-limit headers.
5. Retry 429 responses progressively and honor `retry_after_seconds`.
6. Store `data_version`, `methodology_version`, and source/freshness fields alongside analytical results.
7. Treat modelled, section-level, CAP-level, and centroid-matched data as contextual enrichment rather than cadastral/legal certification.

## Observed parcel example in the web app

The authenticated workspace contained one saved parcel, which was useful for checking how the data is presented in practice:

- Label: parcel `114`; Zornade FID `66482638`.
- Location: Caccamo, Palermo, Sicily; cadastral sheet `40`; CAP `90012`.
- Area: `1,535 m²`; land cover: non-irrigated arable land / agricultural surface.
- Terrain: elevation `340–344 m`, mean slope `7.7°`, predominant exposure `SE`, ruggedness `4.2 m`.
- Risk: seismic zone `2`, PGA `0.150g`; the UI summarized overall risk as high.
- Population: displayed estimate `0.00`, confidence `32%`; this illustrates that modelled parcel population can be zero even when the municipality has residents.
- Economics: mean income `€15,141`, tax year `2023`, Gini `0.416`, affordability `1.92`, OMI residential price about `€364/m²`.
- OMI: current rural zone `R2`; economic ranges were shown for economic residences, villas, and warehouses, plus a 22-semester historical chart.
- Alerts: the UI displayed a NASA FIRMS fire hotspot 954 m from the parcel and two pending community contributions (abandoned building and cadastral-boundary discrepancy).

This example is a UI snapshot, not a guarantee that every parcel will contain every block. Missing coverage, privacy suppression, out-of-FUA status, rural OMI zones, and source-specific matching rules can all produce `null`, empty arrays, or fallback method labels.

## Main caveats for future use

1. **A parcel response mixes authoritative geometry with modelled and contextual data.** AdE geometry and identifiers should be separated from estimates such as population, affordability, solar economics, and night lights.
2. **Spatial matching is not uniform.** Some blocks use the centroid, some use full-geometry intersection, some use raster zonal statistics, and some use a nearby feature search.
3. **Coverage claims are not the same as completeness.** OSM addresses/buildings, Urban Atlas, InSAR, coastal data, and POIs have uneven or conditional coverage.
4. **Time metadata matters.** The catalog combines different vintages, including OMI 2025/2, ISTAT 2021, CORINE 2018, EGMS 2014–2023, VIIRS 2023, and various 2024–2026 snapshots.
5. **Privacy suppression applies.** MEF suppresses small CAPs, and ISTAT can round or hide small counts.
6. **Historical OMI analytics are incomplete in the current release.** `metrics` and `annotations` are documented as currently `null`.
7. **Solar output is an estimate.** It depends on assumptions about roof geometry, shading, losses, CAPEX, discounting, self-consumption, CES, and RID.
8. **Validate limits and schemas at runtime.** The catalog reports API version 2.4.0 and conflicting request quotas; capture `/health`, headers, and data-version fields when running jobs.

## Further enrichment opportunities

The current catalog is already strong on cadastral geometry, market context, terrain, and hazards. The largest remaining opportunities are environmental constraints, climate exposure, accessibility, and explicit data-quality/provenance. The recommendations below are ranked by expected user value, national scalability, and implementation feasibility.

### Priority 1: high-value additions with national or near-national coverage

| Opportunity | Candidate parcel fields | Source and current evidence | Suggested matching / caveat |
|---|---|---|---|
| **Imperviousness and land-consumption change** | `impervious_density_pct`, `built_up_present`, `impervious_change_2021_2024`, `impervious_change_class`, `impervious_confidence` | [Copernicus HRL Imperviousness](https://land.copernicus.eu/en/products/high-resolution-layer-imperviousness) now provides 2024 density and built-up layers at 10 m/100 m, plus change products. | Raster zonal mean, max, percentile, and change area over the parcel. This fills the gap between broad CORINE classes and the building layer; retain raster vintage and confidence. |
| **Protected areas and Natura 2000** | `protected_area_intersects`, `protected_area_name`, `site_code`, `designation`, `distance_to_protected_area_m`, `natura2000_type` | [EEA Natura 2000 data](https://www.eea.europa.eu/en/datahub/datahubitem-view/6fc8ad2d-195d-40f4-bdec-576e7d1268e4?activeAccordion=1095295) has a downloadable end-2024 vector release. | Full-geometry intersection and nearest-boundary distance. Do not collapse a site boundary into a generic “protected = yes”: designation and overlap percentage matter for planning. |
| **Riparian and waterway context** | `riparian_zone_intersects`, `riparian_class`, `distance_to_river_m`, `riparian_change_2012_2018` | [Copernicus Riparian Zones](https://land.copernicus.eu/en/products/riparian-zones/rz-land-cover-land-use-2018) is a vector product with land-cover/use classes for buffered rivers. | Nearest river/riparian polygon plus overlap. It complements flood hazard by describing ecological and land-use proximity, not just inundation. Coverage is limited to mapped selected rivers. |
| **Contaminated land and remediation status** | `contaminated_site_intersects`, `site_name`, `site_type`, `remediation_stage`, `competent_authority`, `sin_flag`, `orphan_site_flag`, `distance_m` | ISPRA's [MOSAICO contaminated-sites database](https://www.isprambiente.gov.it/it/banche-dati/banche-dati-folder/suolo-e-territorio/siti-contaminati) supports consultation/download of procedure type, authority, parties, and progress; ISPRA also publishes SIN information. | Spatial join against site perimeters, with nearest-site distance and status. Treat “near a contaminated site” as a screening flag, not a health or legal conclusion; regional completeness and access levels vary. |
| **Historic wildfire exposure** | `burned_area_intersects_1y/5y`, `fire_count_5y`, `last_burn_year`, `burned_area_m2`, `aib_susceptibility`, `distance_to_burn_scar_m` | The [National Civil Protection wildfire bulletin](https://rischi.protezionecivile.gov.it/it/approfondimento/bollettino-di-previsione-nazionale-incendi-boschivi/) publishes daily susceptibility levels, while regional AIB plans contain burned areas and recent-fire cartography. [Copernicus EMS](https://emergency.copernicus.eu/data/) also exposes EFFIS wildfire data. | Maintain two products: historical burned scars and current forecast/susceptibility. The national bulletin is province-scale, so do not present it as a precise parcel forecast. |
| **Climate stress and future scenarios** | `heatwave_days`, `hot_days_30/35/40c`, `high_utci_days`, `drought_magnitude`, `max_5day_precip_mm`, `soil_moisture`, scenario/year fields | The [Copernicus Climate Data Store climate indicators](https://cds.climate.copernicus.eu/datasets/sis-ecde-climate-indicators) covers heat, drought, precipitation, soil moisture, river discharge, and future projections. | Assign grid-cell values by centroid or area-weighted raster statistics, with historical baseline and scenario metadata. Keep reanalysis/observation values separate from projections; licensing differs across datasets. |

These six blocks would materially improve due diligence: they answer whether a parcel is changing, environmentally constrained, exposed to contamination/fire/climate stress, or located near protected and riparian systems.

### Priority 2: strong differentiators for investment and planning

#### Vegetation, tree cover, and ecosystem condition

The existing CORINE layer is useful but coarse and dated. Copernicus' [High Resolution Layer Tree Cover and Forests](https://land.copernicus.eu/en/products/high-resolution-layer-forests-and-tree-cover?tab=technical_summary) offers tree-cover density, dominant leaf type, forest type, and change layers at 10 m for recent reference years. The 2024 HR-VLCC release is now available through the Copernicus Data Space.

Recommended fields:

- `tree_cover_density_mean_pct` and `tree_cover_density_max_pct`;
- `dominant_leaf_type` and `forest_type`;
- `tree_cover_gain_loss_class` over the latest available change period;
- `green_surface_pct` and `vegetation_confidence`.

Use zonal statistics rather than a single centroid class. This would support heat mitigation, agricultural screening, biodiversity context, and a better explanation of why a parcel is classified as “agricultural” or “urban”.

#### Water resources and hydrological context

ISPRA's [BIGBANG hydrological model](https://www.isprambiente.gov.it/pre_meteo/idro/BIGBANG_ISPRA.html) produces national monthly 1 km estimates of precipitation, actual evapotranspiration, surface runoff, aquifer recharge, and storage. ISPRA's water reporting also covers ecological/chemical status of surface waters and chemical/quantitative status of groundwater.

Recommended fields:

- `hydrographic_district`, `river_basin`, and nearest named water body;
- `distance_to_surface_water_m` and `waterbody_type`;
- `mean_annual_runoff_mm`, `aquifer_recharge_mm`, and `soil_water_storage`;
- `groundwater_status` and `surface_water_status` where a reporting body is available;
- `water_stress_or_drought_class` with reference month/year.

The 1 km hydrology grid is contextual rather than parcel-precise. The product should expose resolution and aggregation method so users do not mistake it for a local drainage or groundwater survey.

#### Accessibility and mobility

The [MIT National Access Point](https://nap.mit.gov.it/) publishes regional GTFS datasets and national transport datasets covering local transit, rail, maritime transport, airports, cycleways, parking, and taxis. This is a high-value complement to POIs because a POI count does not say whether a parcel is reachable.

Recommended fields:

- `nearest_transit_stop_distance_m` and `nearest_rail_station_distance_m`;
- `transit_routes_within_500m`, service span, and weekday departures;
- `distance_to_motorway_junction_m`, `distance_to_primary_road_m`, and road-network travel time;
- `walk_time_to_services` for schools, healthcare, food, and daily retail;
- `accessibility_index` with a clearly documented travel mode and timestamp.

Use network distances and scheduled service frequency, not straight-line distance alone. GTFS snapshots need a `feed_valid_from/to` field and should never be treated as permanent infrastructure availability.

#### Daytime population and commuting flows

The [ISTAT census release](https://www.istat.it/statistiche-per-temi/censimenti/popolazione-e-abitazioni/risultati/) includes 2021 commuting matrices and newer 2023 section-level population, housing, and vehicle variables. This can distinguish a residential parcel from a workday activity area—something night lights and resident estimates cannot do reliably.

Potential derived indicators:

- `inbound_commuters`, `outbound_commuters`, and `commuting_balance` at municipality or urban-zone level;
- `daytime_activity_index` and `residential_vs_employment_area`;
- vehicle ownership per household, where privacy thresholds allow;
- updated 2023 section-level housing and population indicators.

The flows are origin-destination aggregates, so they should be labelled as area context and not assigned as if they were observed at the individual parcel.

#### Air quality and environmental noise

ISPRA's [national air-quality forecast system](https://www.isprambiente.gov.it/it/attivita/aria-1/qualita-dellaria/mappe-previsionali) provides hourly modelled maps for PM10, PM2.5, ozone, NO2, and dust. ISPRA also documents noise-exposure indicators based on strategic and local acoustic maps, including Lden and Lnight.

Potential fields:

- annual and seasonal mean/concentration percentiles for PM2.5, PM10, NO2, and O3;
- number of modeled exceedance days and `air_quality_data_vintage`;
- `noise_lden_db`, `noise_lnight_db`, and exceedance flags where acoustic maps exist;
- distance to the nearest monitoring station and station type.

Keep modeled surfaces, observations, and legal noise maps in separate namespaces. Coverage is not uniform and a missing acoustic map is not evidence of low noise.

### Priority 3: advanced hazard and planning enrichment

#### Historical earthquakes, active faults, and volcanic context

The existing seismic zone/PGA block describes general hazard, but INGV also exposes historical catalogues and fault information through its open-data and mapping systems. The [INGV open-data page](https://istituto.ingv.it/it/open-science/open-data) references recent shakemaps, MPS04, volcanic monitoring, and historical catalogues such as ASMI and CFTI. The [DISS/MUDA mapping context](https://muda.mi.ingv.it/mappa.php?lang=it) includes active seismogenic sources, recent and historical seismicity, and monitoring stations.

Potential fields:

- `distance_to_seismogenic_source_m` and source identifier;
- maximum historical intensity within a defined radius;
- event count/magnitude quantiles over 10, 50, and 100 years;
- distance to active volcano and volcanic hazard zone;
- current volcanic alert/monitoring context for relevant areas.

Do not turn historical event frequency into a building-safety score. The result should be a contextual hazard history with source catalogue, search radius, and observation window.

#### Tsunami exposure for coastal parcels

The [Italian Tsunami Hazard Model 2025 (ITHM25)](https://cat.ingv.it/it/tsunami-ready/comuni-italiani-che-partecipano-al-programma?catid=2&id=495%3Apericolosita-tsunami-ithm25&view=article) covers Italian coasts. Its published results use hazard curves at 1,085 points of interest, with return periods from 475 to 9,975 years and multiple percentiles.

Potential fields:

- nearest ITHM25 point and `distance_m`;
- hazard curve values by return period and percentile;
- coastal evacuation-zone or inundation scenario, when an official inundation layer is available;
- `tsunami_data_vintage` and interpolation method.

Because the points are around 5 km apart on average, any parcel assignment must disclose the distance and whether it is nearest-point context or a true inundation intersection.

#### Planning and development controls

Municipal PRG/PGT/PSC/PUG plans, zoning, building setbacks, landscape rules, and local urban regeneration constraints are among the most commercially valuable missing fields. They are also the least standardized: formats, naming, coordinate systems, update dates, and publication licenses differ by municipality.

Recommended approach:

1. Start with a city/region pilot where planning layers are already downloadable as WMS/WFS or open GIS files.
2. Normalize the result into `planning_zone_code`, `planning_zone_label`, `allowed_uses`, `building_ratio`, `max_height_m`, `setback_rules`, and `plan_date`.
3. Keep the original plan document and layer URL as provenance.
4. Mark the result as “screening only”; a municipal certificate or professional planning review remains authoritative.

### Data that should remain gated or clearly out of scope

Some attractive fields cannot be responsibly treated as a national open enrichment:

- **Ownership, beneficial owner, mortgages, easements, and title history:** these are person/property-right data and require regulated or authenticated cadastral/land-registry access. The AdE citizen services provide personal or controlled access to cadastral and hypothecary information, not a public national parcel feed. See [AdE cadastral assistance](https://assistenzaipocat.agenziaentrate.gov.it/homeCittadino.asp).
- **Cadastral rent, land income, category, class, and subalterno:** potentially valuable, but availability, licensing, and linkage to the national parcel geometry need to be confirmed with AdE before building a bulk product. Do not infer them from OMI prices or building tags.
- **Utility capacity, grid connection availability, and planning permission:** these are operational or administrative facts, often held by utilities or municipalities and not reliably inferable from distance to a power line or from solar potential.

## Recommended implementation roadmap

### Phase A — national, high return

1. HRL imperviousness 2024 plus 2021–2024 change.
2. Natura 2000 and protected-area intersection/distance.
3. Contaminated sites/SIN and remediation-status flags.
4. Historic burned-area recurrence and last-burn year.
5. Updated climate indicators for heat and drought.

### Phase B — differentiated product value

1. Tree cover/forest and vegetation change.
2. Riparian and surface-water context.
3. GTFS-based accessibility and network travel times.
4. ISTAT 2023 section refresh and commuting/activity indicators.
5. Air-quality and acoustic-map overlays where coverage exists.

### Phase C — targeted premium layers

1. Municipal planning-zone pilots.
2. INGV fault, historical earthquake, volcanic, and tsunami context.
3. Region-specific agricultural and irrigation layers.
4. Utility/grid and renewable-connection intelligence, subject to authoritative access.

## Schema improvements that multiply the value of every enrichment

Every block should ideally return a common metadata envelope in addition to its domain fields:

```json
{
  "value": {},
  "available": true,
  "source": "provider_dataset",
  "source_url": "https://example.gov/dataset",
  "data_vintage": "2024",
  "updated_at": "2026-09-03T00:00:00Z",
  "match_method": "centroid|intersects|zonal_stats|nearest|network",
  "match_distance_m": null,
  "coverage_status": "full|partial|not_covered|suppressed",
  "confidence": null,
  "license": "CC BY 4.0"
}
```

This would make missingness and uncertainty machine-readable. Additional high-value quality fields are:

- `overlap_m2` and `overlap_pct` for intersected zones;
- `distance_m` for every nearest-feature result;
- `resolution_m` for rasters and model grids;
- `source_record_id` and original feature URL;
- `methodology_version` for every derived score, not only solar;
- `valid_from`, `valid_to`, and `observed_at` for time-varying data;
- `data_quality_flags` for privacy suppression, fallback estimation, duplicate source records, and centroid-vs-boundary ambiguity.

## Derived cross-block products worth building

The next product advantage is likely to come from transparent combinations rather than another isolated field:

| Derived product | Inputs | Output |
|---|---|---|
| **Development pressure** | Imperviousness change, buildings, OMI history, land use | Recent artificial-surface change, built-up intensity, and market trend |
| **Climate resilience** | Heat, drought, tree cover, imperviousness, water proximity | Heat/drought exposure with green/water mitigation context |
| **Environmental constraint** | Natura 2000, protected areas, riparian zones, contamination, coastal erosion | Explainable constraint flags with distances and overlap percentages |
| **Accessibility** | GTFS, roads, POIs, population, commuting | Network-based service access and daytime activity profile |
| **Renewable suitability** | Solar model, grid proximity, imperviousness, planning zones, protected areas | Technical potential separated from grid/planning feasibility |
| **Data confidence** | Coverage, vintage, match method, source quality, fallback flags | A parcel-level confidence profile instead of a single opaque score |

## PostgreSQL-specific enrichment opportunities

Inspected against the local `aecs4u-stats` PostgreSQL database on
2026-09-03. The database contains substantially more reusable material than
the current parcel-facing surface exposes. The priorities below are ordered by
the amount of value unlocked per unit of new acquisition work.

### Priority 0: make the parcel spine operational

This is the highest-leverage opportunity because nearly every useful spatial
enrichment needs a parcel geometry and a stable parcel identifier.

| Database evidence | Current state | Consequence |
|---|---|---|
| `spatial.cadastral_parcel` | **0 rows** | No parcel can currently receive a canonical spatial join. |
| `spatial.cadastral_sheet` | **0 rows** | Sheet-level QA and map context are also unavailable in the canonical database. |
| `spatial.cadastral_urban_section` | 1,523 geometries | Urban-section context exists, but cannot be attached to a parcel yet. |
| `serving.parcel_risk_profile` | **0 rows** | The intended parcel risk serving view is correctly wired but has no input rows. |
| `fdw_probe.*_ple` | Five foreign parcel tables are defined | There is already a route to local cadastral files, but it is not a stable materialized pipeline. |

The FDW definitions point to national, Lombardia, Milano, Valle d'Aosta, and
Arnad FlatGeobuf/GDAL VRT sources under `/data/catasto`. A direct national
probe encountered a missing or stale source layer in one GPKG, so the first
implementation task is an input-inventory and VRT repair rather than a blind
bulk insert. The foreign-table schema already exposes the right bridge fields:
`fid`, `inspireid_localid`, `label`, `nationalcadastralreference`,
`administrativeunit`, and `geom`.

Recommended parcel-spine deliverable:

1. Repair or regenerate the VRT/source manifest, recording every missing,
   malformed, and successfully readable comune layer.
2. Materialize `spatial.cadastral_parcel` and `spatial.cadastral_sheet` with
   `national_cadastral_reference` and a deterministic source key. Keep the
   source release and source file in the catalog.
3. Normalize all canonical geometry columns to an explicit 2D `geometry(...,
   4326)` contract, while retaining the source CRS in provenance. The current
   database contains both 4326 geometries and census-section geometry in 32632.
4. Run a pilot on one complete region before national loading. Validate source
   row counts, geometry validity, area distributions, duplicate natural keys,
   municipality assignment, and foglio/particella lookup success.
5. Refresh `serving.parcel_risk_profile` only after the pilot passes. Do not
   expose a parcel score if its underlying parcel geometry is missing or only
   coordinate-matched.

### Priority 1: turn existing spatial tables into a parcel due-diligence profile

Once the parcel spine is populated, the database already contains the main
ingredients for a strong first version of parcel due diligence:

- `spatial.hazard_area`: about 1.17 million canonical flood/landslide
  features, with `hazard_type`, `class_code`, geometry, source key, and
  release lineage;
- `facts.hazard_measurement`: about 25.5 million canonical point/polygon
  hazard measurements, including EGMS subsidence metrics and MPS04 seismic
  points; `facts.hazard_curve_point` stores the seismic probability/period
  curves;
- `spatial.postal_zone`: 9,230 sub-municipal CAP geometries;
- `spatial.market_zone`: 27,212 OMI geometries;
- `census_sections.sections`: section geometry plus 122 census indicators;
- `facts.poi`: about 212,000 points in ten normalized categories, with about
  1.15 million raw OSM tags;
- `facts.raster_asset`: two externally stored Italy-wide rasters, the 30 m
  population asset and the 10 m World Settlement Footprint asset.

This suggests a concrete `parcel_context` serving product rather than a flat
copy of every source field. Each result should include the value, source
release, matching method, overlap or distance, and a coverage flag.

Suggested parcel fields:

| Product | Inputs | Useful parcel-level output |
|---|---|---|
| Hazard overlap | `spatial.hazard_area` | One row per hazard type/class, overlap m², overlap %, maximum class, and nearest distance when there is no intersection. |
| Subsidence | `facts.hazard_measurement` | Mean velocity, acceleration, risk index, min/max/median over intersecting cells, cell count, and spatial coverage. |
| Seismic | `facts.hazard_curve_point` plus `facts.hazard_measurement` | PGA/SA values by return-period or exceedance probability, nearest-point distance, and curve vintage. |
| Administrative context | `geo.geo_boundary`, CAP zones, urban sections | Municipality, province, region, CAP candidates, urban-section overlap, and boundary confidence. |
| Market context | OMI geometry and quote facts | Current OMI zone, typology/condition price and rent ranges, latest period, and data age. |
| Local services | POIs and streets | Counts and nearest distances by category, plus selected service attributes from raw tags. |
| Raster context | population and WSF assets | Zonal mean/max/percentiles, built-up share, population estimate, and raster coverage metadata. |

The preferred physical model is a narrow, versioned intersection table such as
`facts.parcel_spatial_context`, keyed by `(parcel_id, dataset_release_id,
context_type, source_feature_key)`. A separate flattened view can serve the
application. This preserves one-to-many hazard and POI relationships without
making the parcel API return an unbounded nested payload.

### Priority 1: expose OMI market depth, not only OMI membership

The database contains approximately 10.7 million rows in
`facts.market_quote_fact` and 27,212 spatial OMI zones. The current
`serving.market_zone_snapshot` is principally an inventory view: it exposes
zone membership, quote count, and latest period. That leaves considerable
parcel-useful information unused:

- price and rent ranges by typology and conservation condition;
- time-series change in the lower, upper, and midpoint range;
- spread between typologies and condition states;
- quote coverage and age as a confidence signal;
- municipality-to-zone heterogeneity rather than one municipality average.

The canonical `facts.market_transaction_fact` table is currently empty. This
means a future liquidity product should not infer transaction activity from
quote rows. It should load an authoritative volume/transaction source, or
label the result explicitly as `quote_depth`, not `liquidity`.

Recommended derived metrics:

```text
parcel_omi_midpoint_eur_m2
parcel_omi_rent_midpoint_eur_m2
parcel_omi_price_change_1y_pct
parcel_omi_price_change_3y_pct
parcel_omi_typology_count
parcel_omi_condition_spread_pct
parcel_omi_quote_count
parcel_omi_latest_period
parcel_omi_market_confidence
```

The metrics should be calculated from the parcel's intersected OMI zone and
should retain the underlying `period`, `typology`, `condition`, and source
rows for auditability.

### Priority 1: upgrade POIs and streets into accessibility intelligence

The normalized POI categories are useful but intentionally narrow: public
transport, hospitals, parks, pharmacies, universities, shops, supermarkets,
schools, kindergartens, and restaurants. The raw tag store preserves a much
richer signal, including address tags, websites, school grades, opening hours,
wheelchair/accessibility attributes, and transport tags where mapped.

Potential additions:

- distance to the nearest school, pharmacy, hospital, supermarket, park, and
  public-transport stop, using a consistent network or straight-line method;
- accessible-service counts using `wheelchair`, entrance, lift, and related
  tags where present;
- school type and grade-band summaries;
- opening-hours and service-availability indicators, with an explicit
  `unknown` state when tags are absent;
- parking, charging, bicycle, and mobility-service categories if present in
  the raw OSM tag universe;
- street-density and access-count context from the 1.21 million ANNCSU street
  rows and their 618,630 distinct street-name/locality pairs.

ANNCSU is currently represented as streets and access counts, not a complete
parcel-address/civic-number point layer. It can therefore improve address
normalization and street context, but it should not be presented as proof that
a particular parcel has a mapped civic number. A future address resolver
should return both `address_match_method` and `address_confidence`.

### Priority 1: repair the census subject grain before parcel weighting

The census section table contains the geometry and 122 non-identity
indicators, but the canonical unpivot currently attaches observations to the
municipality `geo_unit`. Multiple sections in one municipality therefore share
the same canonical subject even though their values differ. This is a data
model limitation, not simply a missing field.

The recommended design is to add a `census_section` geographic level or a
dedicated `spatial.census_section` entity keyed by `sez21_id`, then expose:

- area-weighted or population-weighted section context for each parcel;
- population and household density near the parcel;
- dwelling, building, age, employment, education, commuting, and vehicle
  indicators using the official indicator dictionary;
- a `section_overlap_pct` and `section_match_method` so small or boundary-
  crossing parcels are not falsely treated as belonging to one section.

This would also make the 30 m raster population asset more useful: section
values can be used for reconciliation and anomaly detection rather than being
silently mixed with raster estimates.

### Priority 1: expose municipal tax and income composition carefully

`facts.tax_fact` contains 313,092 long-form facts covering 21 measures over
2021 and 2022 for roughly 7,900 municipalities. The underlying MEF table also
retains frequency/amount pairs for property income, employment, pensions,
self-employment, businesses, tax brackets, taxable income, and net tax.

The current municipality profile exposes only a small aggregate slice. A
parcel-facing product can safely use municipality-level context such as:

- median or mean taxable-income proxies, clearly labelled as aggregates;
- shares of taxpayers in income brackets;
- property-income frequency and amount;
- employment/pension/self-employment composition;
- year-over-year change and coverage completeness;
- tax-base strength as a market-demand context feature.

These must remain aggregate indicators. They should never be interpreted as
the fiscal position of the parcel owner or as a valuation of a specific
property.

### Priority 2: build a raster zonal-statistics layer

The raster catalog already registers two external assets with footprints and
resolution metadata:

- `zornade_popolazione_ita_30m`, approximately 30.9 m native grid spacing;
- `zornade_wsf_italy_10m`, 10 m World Settlement Footprint coverage.

Neither asset is currently represented as parcel-level statistics. Add a
small, release-aware table with `parcel_id`, `raster_asset_id`, statistic,
value, pixel count, valid-pixel percentage, and extraction method. Useful
statistics are mean, sum, max, percentile, and built-up/settlement share.

The first version should compute statistics outside the request path in batch.
External file paths currently live under `/media/emanuele/storage`; the
serving layer should use durable URIs and checksums so a different host does
not silently produce missing values.

### Priority 2: add a planning and development-feasibility layer

The canonical database has cadastral urban sections and environmental hazard
layers, but not a parcel-facing municipal planning-control layer. This is the
largest remaining gap between “what is physically present” and “what can be
legally or economically done”.

A practical pilot should target municipalities with open, versioned planning
data and model:

- zoning destination and permitted uses;
- building-volume/coverage limits where machine-readable;
- landscape, archaeological, hydrogeological, and protected-area overlays;
- implementation-plan or building-permit status when published;
- plan vintage, authority, source URL, and a `planning_data_completeness`
  score.

Do not extrapolate one municipality's planning rules nationally. The API should
return `not_available` when a municipality has no authoritative machine-readable
plan rather than treating the absence of data as absence of a restriction.

### Priority 3: derive explainable cross-block scores

The database is ready for composite products, but they should be built as
auditable views over atomic observations:

| Score | Atomic inputs | Guardrail |
|---|---|---|
| Development readiness | Parcel geometry, OMI, planning, hazards, built-up status, address confidence | Never turn missing planning data into a positive score. |
| Climate resilience | Population raster, WSF, tree/land-cover additions, heat/drought data, water proximity | Separate exposure from mitigation; report both. |
| Environmental constraint | Flood/landslide, subsidence, protected/heritage overlays, coastal context | Return every contributing overlay and overlap percentage. |
| Accessibility | POIs, ANNCSU streets, transport, network distance | Distinguish mapped absence from dataset absence. |
| Market opportunity | OMI ranges, quote age/depth, transaction data when available, tax-base context | Do not call quote counts “liquidity”. |
| Renewable feasibility | PV potential, built-up/roof proxy, protected areas, planning, grid data if acquired | Separate solar resource, installable potential, and connection feasibility. |

Each score should include a versioned formula, normalized component values,
missing-input penalties, and a human-readable explanation. Store the formula
version alongside the result so a future recalculation is distinguishable from
a historical snapshot.

### Database-quality work required before broad exposure

The live quality-check history records unresolved or historical failures in
geometry validity/CRS, source-key uniqueness, source-release lineage,
dimension coverage, municipality parity, and source-to-canonical matching.
These should be rerun after any repair because the current check table contains
multiple validation snapshots and is not itself a “latest status” view.

Two operational issues are especially relevant:

- The direct national FDW probe found a stale/missing cadastral source layer;
  source manifests must be validated before a national materialization run.
- A costly ad-hoc PostGIS query previously triggered a broken JIT bitcode
  summary on this server. The migration helper now disables JIT for its own
  statements, but API jobs and maintenance queries should use bounded spatial
  batches and the same safe setting until the PostgreSQL/PostGIS installation
  is repaired.

### Recommended implementation sequence from the database evidence

1. **Repair cadastral source manifests and populate a pilot parcel region.**
2. **Create parcel intersection facts** for OMI, hazards, CAP, urban sections,
   and POIs, with overlap/distance and release metadata.
3. **Refresh the parcel risk profile** and add a separate market-context view;
   keep market quotes distinct from transaction/liquidity measures.
4. **Fix census section grain** and add raster zonal statistics.
5. **Expose aggregate municipal tax/income composition and richer POI tags.**
6. **Pilot planning controls in a small set of open-data municipalities.**
7. **Build the composite scores only after atomic coverage and confidence
   fields are visible.**

## Second-pass findings from live PostgreSQL state

The follow-up audit looked beyond table existence and checked active release
registrations, dimension metadata, placeholder facts, indexes, and statistics.
These findings add opportunities that are easy to miss when looking only at
the dataset catalog.

### Release catalog: create a single current-release contract

The catalog contains multiple active `source_file` registrations for the same
physical path:

- OMI: three registrations for the same `omi.IT.sqlite` path;
- MEF IRPEF: two registrations;
- OSM POIs: two registrations;
- Eurostat: two registrations for each country file.

The database does preserve release IDs, checksums, and lineage fields, but the
duplicate registrations make “latest” ambiguous unless every query applies
the same rule. This is particularly risky for parcel enrichment because an
OMI or POI result can otherwise be associated with the wrong vintage while
still passing a foreign-key check.

Recommended improvements:

- add an explicit `is_current` or `superseded_by_release_id` contract at the
  dataset-release level;
- expose a `catalog.current_dataset_release` view that resolves one current
  release per dataset and geography;
- enforce one active source file per `(dataset, normalized_path, checksum)`;
- retain historical releases for reproducibility but exclude them from
  default serving queries;
- include `release_selection_rule` in every materialized parcel-enrichment
  run;
- add a checksum-equality check so a repeated import is classified as a
  duplicate retrieval, not a new data vintage.

This will make time-aware parcel comparisons safer: a change in a parcel score
should be attributable to a source-vintage change, a geometry change, or a
formula change rather than to an arbitrary release-row selection.

### Statistical dimensions: the fact rows are present, the dictionary is not

The live database contains **8,183,715** rows in
`facts.observation_dimension`, but both `facts.indicator_dimension` and
`facts.indicator_member` contain **0** rows. A sample of the dimension facts
shows Eurostat dimensions such as `unit`, `nace_r2`, `s_adj`, and `indic_bt`.

This is not immediately blocking for the current parcel products, but it is a
major missed capability for context selection and aggregation. The raw
dimension values are there; the model lacks the declared vocabulary needed to
answer questions such as “which observation represents annual, seasonally
adjusted residential construction?”

Recommended work:

1. Seed `facts.indicator_dimension` from the authoritative Eurostat dataflow
   schema for each indicator.
2. Populate `facts.indicator_member` with member code, label, and codelist
   provenance rather than inferring meanings from values at query time.
3. Add dimension-aware unique keys and query helpers for `(indicator,
   geography, period, dimension members)`.
4. Use the same pattern for any future climate, air-quality, mobility, or
   energy indicators that have multiple units, scenarios, percentiles, or
   measurement methods.

The result would allow parcel context to distinguish a comparable economic
series from a merely similarly named one, and would prevent silent mixing of
units or scenarios in derived scores.

### Empty fact tables reveal product gaps, not just incomplete ETL

The following canonical tables are structurally ready but empty:

| Empty relation | Opportunity unlocked by populating it |
|---|---|
| `facts.market_transaction_fact` | Separate transaction volume, turnover, or share from advertised quote depth. |
| `facts.justice_kpi_fact` | Municipality/province-level legal-process context, useful for auction, foreclosure, and transaction-friction analysis. |
| `facts.indicator_dimension` / `indicator_member` | Reliable multi-dimensional economic, climate, and mobility queries. |
| `spatial.cadastral_parcel` / `cadastral_sheet` | The actual parcel anchor for all due-diligence enrichment. |
| `serving.parcel_risk_profile` | A stable application-facing parcel risk summary. |

The market transaction table is the most directly relevant. It already has a
good canonical shape—geography, OMI zone, period, typology, measure, and
value—but no rows. This is an opportunity to add measures such as transaction
count, normalized turnover, or volume-to-stock ratios when an authoritative
source is acquired. Until then, any product using quote counts should use names
such as `quote_depth` or `market_observation_count`, never `liquidity`.

The justice table is a lower-priority enrichment. It should be used only as
aggregate geographic context and only after defining the relationship between
a tribunal catchment area and a parcel's municipality. It must not be
interpreted as a property-specific legal status or as evidence about an owner.

### OMI facts support a richer temporal product than the current snapshot

Database statistics show that `facts.market_quote_fact` covers 43 semester
periods, 18 typologies, and three conservation conditions. The current serving
snapshot reduces this rich history largely to quote count and latest period.

This supports a second-generation OMI product with:

- current lower/upper/midpoint price and rent ranges;
- a history curve per typology and condition;
- number of observed semesters and gaps in the series;
- volatility and range compression/expansion;
- typology substitution signals, such as residential versus commercial
  coverage;
- a “last observed” age penalty;
- comparison of the parcel's zone against its municipality and province.

The product should keep the raw semester and typology dimensions accessible.
Do not collapse all 18 typologies into one average unless the weighting rule is
explicit and the resulting value is labelled as a derived estimate.

### Add indexes designed for parcel-serving workloads

The canonical schema currently has strong primary keys and spatial GIST
indexes, but several large tables lack the secondary indexes needed by the
proposed enrichment joins:

- `facts.market_quote_fact` has no visible index on zone, period, typology,
  and condition;
- `facts.hazard_curve_point` has no visible index on
  `hazard_measurement_id`;
- `facts.poi_tag` has only its primary-key index, not a `(poi_id, tag_key)`
  lookup index;
- `facts.hazard_measurement` has a geometry index but no dedicated index on
  hazard type, metric, release, or source key;
- `spatial.hazard_area` has a geometry index but no explicit class/type
  filter index;
- `facts.market_transaction_fact` will need a zone/period index before it is
  populated at scale.

Candidate indexes should be benchmarked with representative parcel joins,
not added indiscriminately. The expected pattern is a cheap attribute/release
filter followed by a spatial candidate filter and then exact geometry
intersection. For raw POI exploration, a GIN index over `raw_tags` may be
useful, but a normalized derived tag table is preferable for stable API
fields.

### Refresh optimizer statistics as part of every bulk-load release

`pg_stat_user_tables.n_live_tup` reports zero for many relations that are
known to contain substantial data, including the OMI, census, POI, tax, and
canonical geography tables. The catalog's `pg_class.reltuples` estimates and
direct counts show the data is present, but monitoring statistics are stale or
not a reliable row-count source in the current installation.

Add to the release workflow:

```text
bulk load
→ create/refresh spatial and attribute indexes
→ ANALYZE affected tables
→ run bounded EXPLAIN plans for representative parcel joins
→ refresh serving views
→ record row counts, table statistics timestamp, and query timings
```

This matters for enrichment because a bad estimate on a 25-million-row hazard
table can choose a catastrophic nested-loop plan, while the same SQL appears
fine on a small pilot. Keep heavy geometry work out of request paths and
materialize parcel intersections in batches.

### Reduce legacy duplication while preserving auditability

The database retains both source-shaped OMI tables and canonical OMI facts,
alongside multiple catalog releases. This is valuable for auditability but
expensive for repeated queries and creates two semantic surfaces for the same
concept.

A useful enrichment-platform improvement is a three-layer contract:

```text
raw/source-shaped tables
    ↓ immutable, release-keyed
canonical facts and spatial entities
    ↓ one current-release selection
serving parcel and municipality products
```

The serving layer should never join raw and canonical representations in the
same query. Add a canonical-to-source reconciliation report containing row
counts, checksum, period coverage, key coverage, and value spot checks. This
will make future enrichment sources easier to onboard without duplicating
large tables or accidentally mixing vintages.

### Additional derived products now suggested by the database

The second-pass findings support these concrete products:

| Product | Why the current database makes it feasible |
|---|---|
| **Parcel evidence bundle** | One versioned object combining all intersections, source records, distances, overlaps, and confidence flags. |
| **Market time-series card** | 43 OMI semesters × 18 typologies × 3 conditions already provide the temporal dimensions. |
| **Data freshness/coverage card** | Catalog releases, source files, quality checks, raster metadata, and periods are already modeled. |
| **Cadastral resolution confidence** | FDW/native cadastral reference, municipality crosswalk, geometry match, and source-key lineage can be scored separately. |
| **Service accessibility profile** | POI geometry, raw tags, ANNCSU streets, and CAP zones support service counts and nearest-distance measures. |
| **Environmental exposure ledger** | Hazard polygons, EGMS metrics, MPS04 curves, raster assets, and parcel overlap statistics can be reported as evidence rather than one opaque score. |
| **Municipal demand context** | Census indicators, tax facts, OMI history, PV aggregates, and POI counts can be combined at the municipality level without exposing personal data. |

The strongest near-term sequence remains: repair the cadastral materialization,
then build versioned parcel intersections, then expose the market and evidence
cards. The dimension registry, release-selection view, secondary indexes, and
post-load statistics should be treated as enabling infrastructure for those
products rather than optional cleanup.
