# Gap Analysis: land-registry vs. Zornade (app.zornade.com)

*Date: 2026-07-10. Based on a live review of app.zornade.com (v2.4.0) and the current state of this repository.*

Zornade is the closest public comparable to this project: a free Italian cadastral
intelligence map (~85M parcels, 7,899 comuni) built on official open data, with a
per-parcel enrichment panel (~17 data sections), live hazard overlays, and a public
REST API. This document maps its feature set against ours and defines a phased
development plan.

---

## 1. Current state of this project (what we already have)

| Capability | Where | Notes |
|---|---|---|
| FastAPI backend + Clerk JWT auth | `main.py`, `routers/auth.py` | Solid foundation |
| Folium iframe map + Leaflet + WebGL (glify) | `map.py`, `static/webgl-renderer.js` | Dual-map architecture; `window.map` often null |
| Datashader raster tiles (z/x/y PNG) | `datashader_service.py` | For 100K+ features |
| FlatGeobuf per-region loading + metadata | `/api/v1/fgb/*`, `scripts/merge_fgb_per_region.py` | Regional FGB files on disk/S3 |
| NDJSON progressive streaming loader | `/api/v1/load-cadastral-files-stream/` | Phase-2 work in progress |
| Cadastral hierarchy browse (region→prov→comune) | `/api/v1/get-regions/…`, `get-cadastral-structure/` | Backed by file tree, cached |
| Parcel search (comune/foglio/particella/sub) | `/api/v1/search/parcels` | **Only within the in-memory loaded GDF** |
| Adjacency analysis + export | `/api/v1/get-adjacent-polygons/`, `/export-adjacency/{fmt}` | Differentiator vs Zornade |
| Zones / microzones / drawn polygons | `/api/v1/zones/*`, `sqlite_db.py` | User-drawn geometry persistence |
| GHSL enrichment (DEGURBA + UCDB pop/GDP) | `ghsl_service.py` | Population/urbanization enrichment exists |
| OMI import pipeline (QI + VCN → SQLite) | `scripts/omi_import.py` | **Data pipeline only — no API/UI on top** |
| Auction properties (dummy data) | `/api/v1/auction-properties/` | Stub; differentiator if made real |
| Neon PostgreSQL + SQLite + SpatiaLite modules | `database.py`, `sqlite_db.py`, `spatialite.py` | Storage plumbing available |
| Panel/Bokeh attribute tables | `dashboard.py` | |
| i18n scaffolding | `i18n.py` | |
| Municipality flags (Wikidata) | `flags.py` | Fun extra, keep |
| **aecs4u-stats consumer** (ISTAT municipalities/population, OSM POIs) | `stats_service.py`, `routers/enrichment.py` | `/api/v1/enrichment/*`; join key = catasto comune code |

**Core architectural difference:** our viewer requires the user to pick and load
files into a global in-memory GeoDataFrame before anything renders; Zornade serves
the whole country instantly via zoom-gated vector tiles and moves all the value
into a per-parcel detail lookup + enrichment API.

## 2. Feature gap matrix

Legend: ✅ have · 🟡 partial (pipeline or stub exists, no product surface) · ❌ missing

| # | Zornade feature | Ours | Gap detail |
|---|---|---|---|
| 1 | Nationwide always-on parcel map (zoom-gated vector tiles, labelled polygons) | ❌ | We load files on demand into RAM; no vector tiles (datashader PNG only, no interactivity) |
| 2 | Click parcel → detail panel (80+ fields, 17 sections) | ❌ | We only show raw attribute tables of loaded features |
| 3 | URL state / deep links (`?lat&lng&zoom&parcel=`) + share | ❌ | No permalink support |
| 4 | Hierarchical parcel finder that works without pre-loading | 🟡 | Hierarchy endpoints exist (file-tree based); search only works on loaded data |
| 5 | Cadastral base data (particella, foglio, sezione, comune, area) | ✅/🟡 | Data present in FGB/WFS attributes; needs a per-parcel lookup keyed by stable ID |
| 6 | OMI quotations (per zone, by type, yields) + 22-semester history + value estimator | 🟡 | `omi_import.py` builds the SQLite DB; no zone-geometry join, endpoints, or UI |
| 7 | Environmental risks: seismic, flood (PGRA), landslide (PAI/IFFI), subsidence (InSAR), coastal erosion + composite risk score | ❌ | Nothing |
| 8 | Live overlays: satellite fire hotspots (FIRMS), Protezione Civile criticality bulletin | ❌ | Nothing |
| 9 | Solar PV potential per building (PVGIS-SARAH3, cash-flow, NPV, LCOE, payback) | ❌ | Nothing |
| 10 | Terrain: elevation, slope, aspect, roughness (Tinitaly DEM) | ❌ | Nothing |
| 11 | Demographics (ISTAT census sections: age, gender, indicators) | 🟡 | GHSL coarse pop/urbanization + **aecs4u-stats municipality population history**; no ISTAT sezioni yet |
| 12 | Economy: MEF/IRPEF income distribution, VIIRS night lights | 🟡 | aecs4u-stats has regional income/GDP/employment (Eurostat NUTS2/3); comune-level IRPEF and VIIRS missing |
| 13 | Land cover (CORINE / Urban Atlas) | ❌ | Nothing |
| 14 | Addresses (ANNCSU civic numbers) | ❌ | `docs/urban_address_requirements.md` suggests prior intent |
| 15 | Buildings (footprint, count, coverage %) | ❌ | Nothing |
| 16 | POI (Foursquare/OSM) & cultural constraints (MiBAC) | 🟡 | POI ✅ via aecs4u-stats (`/api/v1/enrichment/pois/`, needs the store built); MiBAC constraints missing |
| 17 | Public REST API v2 (`/parcels/{id}?include=…`, API keys, 1K–10K req/h rate limits, data catalog docs) | ❌ | Internal API only, session-state-dependent |
| 18 | PDF export of parcel report | ❌ | Nothing |
| 19 | Favorites / saved parcels | 🟡 | `saved_maps` + zones tables exist; no parcel-level favorites UX |
| 20 | Community: profiles, leaderboard, guest contributor mode | ❌ | Clerk auth exists; no community layer |
| 21 | Onboarding tour, donation/sponsor prompts | ❌ | Cosmetic, low priority |
| 22 | Basemap switcher (dark/light/satellite) + geolocation | 🟡 | Folium tile layers exist; not a first-class 3-way switcher; no geolocate button |

**Where we are ahead of Zornade:** QPKG/GPKG upload & inspection, polygon adjacency
analysis with export, user-drawn zones/microzones, datashader density/categorical
rendering for huge datasets, Panel/Bokeh tables, auction-property concept.

## 3. Architectural prerequisites (the real gap)

Zornade's product is ~20% map and ~80% *enrichment data platform*. Matching it
requires three structural changes before any feature work pays off:

**P1 — Parcel store with stable IDs (kill the global GDF for viewing).**
Load all regional parcels into PostGIS (Neon already provisioned; enable the
extension) or a SpatiaLite/GeoParquet+DuckDB store: `fid, comune_code, sezione,
foglio, particella, geometry, area, centroid`. Every enrichment joins on `fid`.
The in-memory GDF remains only for the upload/analysis workflows.

**P2 — Vector tiles instead of (or beside) datashader PNGs.**
Pre-generate PMTiles per region with tippecanoe from the existing FGB files
(`scripts/merge_fgb_per_region.py` output), zoom-gated ≥15, served as static
files from S3/Cloud Run. Interactive hover/click + labels come free in MapLibre.
Datashader stays for analytics views (density/categorical).

**P3 — A direct MapLibre GL page (retire the Folium iframe for the main viewer).**
The iframe pattern (documented pain in CLAUDE.md: `window.map` null, dual
architecture) cannot do vector tiles, feature-state hover, or URL-driven state
cleanly. Build `templates/map_v2.html` + `static/map-v2.js` as a parallel page;
keep the Folium page for the legacy upload/analysis flows until parity.

**P4 — Public-data ETL lives in `aecs4u-stats`; this app only consumes.**
*(Implemented 2026-07-11 as the first slice.)* The app now depends on
`aecs4u-stats` (git source in `pyproject.toml`), adapted by
`land_registry/stats_service.py` and exposed at `/api/v1/enrichment/*`:
- `GET /enrichment/status` — which stats stores are built on this host
- `GET /enrichment/municipality/{cadastral_code}` — ISTAT profile via the
  catasto comune code (e.g. `C773` → Civitavecchia, Roma, Lazio, NUTS3 ITI43)
  plus resident-population history
- `GET /enrichment/pois/?lat&lng&radius_km&categories` — OSM POIs grouped by
  category, nearest-first

Everything degrades gracefully when a store is missing. Stores are built with
the aecs4u-stats pipeline (`ISTAT_DATA_DIR`, default `~/.aecs4u_stats/istat`):
`python -m aecs4u_stats.istat.scripts.import_data` and
`python -m aecs4u_stats.osm.scripts.download_pois`.

The rule going forward: every new public dataset in the plan below (OMI zones,
ISTAT sezioni, DEM, risks, IRPEF, VIIRS, ANNCSU, MiBAC…) should be ingested as
an `aecs4u_stats` subpackage (models + downloader + queries, reusable by every
AECS4U app) and consumed here through `stats_service.py` — not ETL'd inside
this repo. `scripts/omi_import.py` is the first candidate to migrate upstream.

> Upstream fix needed: `aecs4u_stats.istat.scripts.import_data` matches ISTAT
> Excel headers case-sensitively; the current ISTAT file uses e.g. "Codice
> Catastale del Comune" (capital C) and "…con 110 Province…", so the import
> fails until headers are normalized. Make the importer's column resolution
> case/whitespace-insensitive.

## 4. Development plan

Phases ordered by user value ÷ effort; each phase is shippable on its own.
Effort assumes one developer, part-time equivalents.

### Phase 0 — Platform enablers (~2 weeks)
1. Enable PostGIS on Neon; write `scripts/import_parcels_to_postgis.py` (reuse
   `scripts/import_cadastral_to_db.py` patterns); ingest all regions with a
   stable `fid`, spatial + (comune, foglio, particella) indexes.
2. PMTiles build script (FGB → tippecanoe → per-region `.pmtiles` → S3);
   `GET /api/v2/tiles/parcels/{z}/{x}/{y}.mvt` or static PMTiles range requests.
3. New MapLibre page with: dark/light/satellite basemap switcher, URL state
   (`?lat&lng&zoom&parcel`), geolocate control, parcel layer with number labels
   at z≥16, hover highlight, click → select.
4. `GET /api/v2/parcels/{fid}` returning cadastral base section from PostGIS.

**Exit criterion:** open the app, zoom anywhere in Italy, see parcels, click one,
get its cadastral data — no file loading step.

### Phase 1 — Parcel detail panel + finder (~2 weeks)
1. Detail panel UI (collapsible sections, header KPIs: area, comune; risk/€ m²
   placeholders until later phases).
2. Rewire the hierarchical finder (Regione→Provincia→Comune→Foglio→Particella)
   to query PostGIS instead of the loaded GDF; fly-to + select on result.
3. Permalink/share button; favorites: `saved_parcels` table keyed to Clerk user
   (extend `sqlite_db.py` or move to Neon).
4. Section framework in the API: `?include=basic,geometry,…` so each later
   phase just registers a new section provider.

### Phase 2 — OMI + first enrichments (~2–3 weeks) — *biggest bang for the buck*
1. OMI: **migrate `scripts/omi_import.py` into `aecs4u_stats.omi`** (models +
   importer + queries), add zone polygons (AdE publishes OMI zone perimeters),
   spatial-join parcels→OMI zone; endpoints for current quotes by typology,
   22-semester history, and a value estimator (type × m² × quote range).
   Panel sections: *Quotazioni OMI*, *Storico OMI*, *Stima valore*.
2. Wire the already-shipped enrichment endpoints into the parcel detail panel
   (municipality section + POI section); build the ISTAT and POI stores in the
   deploy image or a mounted volume.
3. Terrain: Tinitaly 10m DEM (INGV) → `aecs4u_stats` raster-sampling subpackage
   → per-parcel elevation min/mean/max, slope, aspect, roughness.
4. Land cover: CORINE 2018 vector → majority class per parcel at ingest.
5. Demographics: ISTAT sezioni di censimento (Basi Territoriali + census data)
   as an `aecs4u_stats.istat` extension → join parcel centroid → sezione;
   expose density, gender, age structure, employment/vacancy indicators.
   (Augments GHSL + the municipality population history already exposed.)

### Phase 3 — Risks + live overlays (~2–3 weeks)
1. Seismic: DPC municipal seismic classification (zona 1–4) by comune code.
2. Flood: ISPRA PGRA hazard polygons (P1/P2/P3) → per-parcel intersection %.
3. Landslide: ISPRA PAI/IFFI (P1–P4 + areas of attention) → same treatment.
4. Composite 0–100 risk score + gauge in panel header (document the formula).
5. Live overlays (no ETL, fetch-and-cache ~15 min):
   - NASA FIRMS active fires (Italy bbox, 48h window) → marker layer with age.
   - DPC bollettino di criticità (published as open data daily) → zone polygons
     colored by alert level, toggleable.

### Phase 4 — Advanced enrichment (~3–4 weeks, independently shippable items)
1. Solar PV per parcel/building: JRC PVGIS API (SARAH3) on-demand with DB cache;
   compute kWp from roof area, production, CAPEX model, payback/NPV/LCOE chart.
2. Economy: MEF/Dip. Finanze IRPEF income brackets per comune/CAP (new
   `aecs4u_stats` dataset; regional Eurostat KPIs already exist in
   `aecs4u_stats.istat.queries`); VIIRS Black Marble annual composite sampled
   at centroid (rural→metropoli classification).
3. Addresses: ANNCSU open data → parcel address list (fulfils
   `docs/urban_address_requirements.md`).
4. Buildings: building footprints (catasto fabbricati layer already in our WFS
   data, or OSM/Microsoft footprints) → count, footprint m², coverage %.
5. POI: ✅ already served by `aecs4u_stats.osm` via `/api/v1/enrichment/pois/`
   (build the store with the Overpass downloader or the `pbf` extra). Cultural
   constraints: MiBAC vincoli (Vincoli in Rete / Carta del Rischio) intersection.
6. PDF export of the parcel report (server-side render, e.g. WeasyPrint).

### Phase 5 — Public API + community (~2–3 weeks)
1. Formalize `/api/v2` as the public surface: OpenAPI descriptions, per-section
   `include` filters, explicit source attribution in every response block.
2. API keys: Clerk-issued tokens, per-key rate limiting (e.g. `slowapi`:
   1K req/h anonymous, 10K keyed), usage counters in Postgres.
3. Data-catalog docs page (source, methodology, refresh cadence, license per
   field) — this transparency is a large part of Zornade's credibility.
4. Community (optional): public profiles, contribution leaderboard.
5. Revive auction properties with real data (PVP — portale vendite pubbliche)
   as a differentiator Zornade lacks.

### Quick wins (do anytime, < a day each)
- URL `?lat&lng&zoom` read/write on the existing Folium page.
- Geolocate button; basemap 3-way switcher.
- Welcome/onboarding modal with a short tour.
- Attribution footer with data-source citations.

## 5. Data source shopping list

| Dataset | Source | Access | Used in phase |
|---|---|---|---|
| Parcels/buildings WFS | Agenzia delle Entrate (INSPIRE) | already have | 0 |
| OMI quotes + zones | Agenzia delle Entrate OMI | ZIP downloads (importer exists) | 2 |
| DEM 10m Tinitaly | INGV | free registration | 2 |
| CORINE 2018 / Urban Atlas | Copernicus | open | 2 |
| Census sections + demographics | ISTAT Basi Territoriali | open | 2 |
| Seismic classification | Protezione Civile | open CSV | 3 |
| Flood hazard PGRA | ISPRA | open (WFS/shp) | 3 |
| Landslide PAI/IFFI | ISPRA | open | 3 |
| Active fires | NASA FIRMS | free API key | 3 |
| Criticality bulletins | DPC open data (GitHub) | open JSON/shp | 3 |
| Solar irradiance | JRC PVGIS API | open API | 4 |
| IRPEF income | MEF Dip. Finanze | open CSV | 4 |
| VIIRS night lights | NASA Black Marble | open | 4 |
| Civic addresses | ANNCSU | open | 4 |
| POI | OSM via **aecs4u-stats** | integrated | done |
| Municipalities + population | ISTAT via **aecs4u-stats** | integrated | done |
| Cultural constraints | MiBAC Vincoli in Rete | open (scrape/WFS) | 4 |
| Auctions | PVP portalevenditepubbliche.it | public listings | 5 |

## 6. Suggested sequencing summary

```
P0 platform (PostGIS + PMTiles + MapLibre + /parcels/{id})   ██ 2w
P1 detail panel + finder + permalinks + favorites            ██ 2w
P2 OMI + terrain + CORINE + ISTAT demographics               ███ 3w
P3 risks (seismic/flood/landslide) + score + live overlays   ███ 3w
P4 solar, income, VIIRS, addresses, buildings, POI, PDF      ████ 4w
P5 public API v2 + keys + catalog + community + auctions     ███ 3w
                                              total ≈ 17 weeks part-time
```
