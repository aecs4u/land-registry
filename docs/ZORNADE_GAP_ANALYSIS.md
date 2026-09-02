# Gap Analysis: land-registry vs. Zornade (app.zornade.com)

*Date: 2026-07-10, updated 2026-07-13. Based on a live review of app.zornade.com
(v2.4.0) and the current state of this repository.*

> **2026-07-11 update:** most of the "data pipeline" work this document
> originally scoped for `land_registry/scripts/` has been redirected upstream
> into the shared `aecs4u-stats` (public-data ETL + query layer) and
> `aecs4u-domain` (shared SQLModel schema) packages, consumed here via
> `land_registry/stats_service.py` and `/api/v1/enrichment/*` — see §3 P4 and
> the status column in §2/§5 for what has landed.

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
| OMI import pipeline (QI + VCN → SQLite) | `aecs4u_stats.omi` (migrated upstream; local `scripts/omi_import.py` deleted) | Consumed via `stats_service.py` + `/api/v1/enrichment/omi/*` |
| Auction properties (dummy data) | `/api/v1/auction-properties/` | Stub; differentiator if made real |
| Neon PostgreSQL + SQLite + SpatiaLite modules | `database.py`, `sqlite_db.py`, `spatialite.py` | Storage plumbing available |
| File/blob storage via **aecs4u-storage** (provider-agnostic: local/s3/gcs/azure/gdrive) | `storage.py` | Set `STORAGE_PROVIDER=gcs` + `STORAGE_GCS_BUCKET`/`STORAGE_GCS_PROJECT`/`STORAGE_GCS_CREDENTIALS_FILE` to use GCS; currently `s3` in `.env`. *(2026-07-12: removed the dead pre-aecs4u-storage `GCSStorageSettings`/`gcs_settings` and the old `gcs_storage.py` module's leftover `GCS_*` env vars from `.env.example` — they were never read by the live storage path.)* |
| Panel/Bokeh attribute tables | `dashboard.py` | |
| i18n scaffolding | `i18n.py` | |
| Municipality flags (Wikidata) | `flags.py` | Fun extra, keep |
| **aecs4u-stats consumer** (ISTAT municipalities/population/census/demographics/BES/safety, OSM POIs, OMI quotes, MEF/IRPEF income, DPC seismic zones, ISPRA IdroGEO flood/landslide, NASA FIRMS fires, DPC criticality bulletin) | `stats_service.py`, `routers/enrichment.py` | `/api/v1/enrichment/*`; join key = catasto comune code / ISTAT code. Census, demographics, safety and quality-of-life cards are wired into the parcel detail panel. |
| **aecs4u-domain `CadastralParcel` schema** | upstream, not yet consumed here | Shared SQLModel for the geospatial parcel (geometry, foglio/particella, soft FK to `PropertyKey`) — the schema Phase 0 (§4) needs for a real parcel store; not yet wired into this app |

**Core architectural difference:** our viewer requires the user to pick and load
files into a global in-memory GeoDataFrame before anything renders; Zornade serves
the whole country instantly via zoom-gated vector tiles and moves all the value
into a per-parcel detail lookup + enrichment API.

## 2. Feature gap matrix

Legend: ✅ have · 🟡 partial (pipeline or stub exists, no product surface) · ❌ missing

| # | Zornade feature | Ours | Gap detail |
|---|---|---|---|
| 1 | Nationwide always-on parcel map (zoom-gated vector tiles, labelled polygons) | ✅/🟡 | ✅ Backend proven: `aecs4u_stats.cadastral` (per-region DuckDB stores, built from local INSPIRE extracts) via `/api/v1/enrichment/parcels/{comune_code}`, `/parcels/in-bbox/`, `/fogli/{comune_code}`, `/parcel/by-reference/{ref}`. ✅ Cadastral boundaries use zoom-gated MVT with PNG fallback, and the map automatically loads viewport parcel GeoJSON at z≥16 with tooltips, click selection and detail-panel integration. Remaining gap: labels are hover tooltips rather than persistent MapLibre-style labels. |
| 2 | Click parcel → detail panel (80+ fields, 17 sections) | 🟡 | ✅ Detail panel now exists (`#parcelInfoPanel`/`parcel-enrichment.js`, 2026-07-12): base attributes + 5 enrichment cards (Comune, OMI, IRPEF, Rischi, POI), wired into both parcel-rendering paths. Missing: only fires on already-loaded file-based parcels, not the new `aecs4u_stats.cadastral` store; fewer sections than Zornade's 17 |
| 3 | URL state / deep links (`?lat&lng&zoom&parcel=`) + share | ✅ | `?lat=&lng=&zoom=` is restored on `GET /map` and updated after map movement. Selected boundary parcels now write `&parcel=<national-reference>`, restore through `/parcel/by-reference/{ref}`, and expose a Copy Link action in the detail panel. |
| 4 | Hierarchical parcel finder that works without pre-loading | 🟡 | Hierarchy endpoints exist (file-tree based); search only works on loaded data |
| 5 | Cadastral base data (particella, foglio, sezione, comune, area) | ✅/🟡 | Data present in FGB/WFS attributes; needs a per-parcel lookup keyed by stable ID |
| 6 | OMI quotations (per zone, by type, yields) + 22-semester history + value estimator | 🟡 | ✅ Quotes/history backend and parcel-panel UI are implemented: users select zone/typology/conservation state, edit the relevant m², see an explicitly non-appraisal value range, and inspect up to 24 semesters in a trend chart. ✅ `/omi/at-point` spatially matches the parcel centroid and `POST /omi/estimate` returns a versioned, report-ready server calculation from one exact quote; the UI retains instant local preview and manual selection fallbacks. Missing: build/mount all province boundary files in deployment. |
| 7 | Environmental risks: seismic, flood (PGRA), landslide (PAI/IFFI), subsidence (InSAR), coastal erosion + composite risk score | 🟡 | ✅ Seismic zone (DPC, local store) + flood/landslide indicators (ISPRA IdroGEO, live API, verified against real Civitavecchia data) via `/api/v1/enrichment/risks/{istat_code}`. Missing: subsidence (InSAR), coastal erosion, and a composite 0–100 score |
| 8 | Live overlays: satellite fire hotspots (FIRMS), Protezione Civile criticality bulletin | ✅ | FIRMS detections have a marker-layer toggle (`FIRMS_MAP_KEY` required). DPC bulletin details appear in the parcel panel and its 187 national alert-zone polygons can be toggled on the map with red/orange/yellow/green severity styling, hover labels, detail popups, issue timestamp and legend. |
| 9 | Solar PV potential per building (PVGIS-SARAH3, cash-flow, NPV, LCOE, payback) | ❌ | ✅ Client done and consolidated: `aecs4u_stats.pvgis` (v5_2 API client, local cache, CLI) is now the single PVGIS implementation org-wide — `aecs4u-energy` was rewritten to a thin shim over it (2026-07-12). Not yet consumed here; wire in via `aecs4u_stats.pvgis` directly (not via aecs4u-energy) plus the cash-flow/NPV/LCOE/payback computation, still missing everywhere |
| 10 | Terrain: elevation, slope, aspect, roughness (Tinitaly DEM) | ❌ | Nothing — needs an `aecs4u_stats` raster-sampling subpackage (see §4 Phase 2) |
| 11 | Demographics (ISTAT census sections: age, gender, indicators) | ✅ | Parcel-centroid lookup now shows the containing 2021 census section with residents, households, dwellings, buildings, employment, education, foreign-resident share, vacancy and household size. Province-level demographic series are previewed separately with explicit resolution disclosure. |
| 12 | Economy: MEF/IRPEF income distribution, VIIRS night lights | 🟡 | ✅ IRPEF done: `aecs4u_stats.mef` (comune-level income brackets, mean taxable income) via `/api/v1/enrichment/income/{cadastral_code}` — verified against real 2022 MEF data for Civitavecchia (25.5%/10.8%/26.4%/30.4% brackets, close to Zornade's reference-year figures). VIIRS night lights still missing |
| 13 | Land cover (CORINE / Urban Atlas) | ❌ | Nothing |
| 14 | Addresses (ANNCSU civic numbers) | ❌ | `docs/urban_address_requirements.md` suggests prior intent |
| 15 | Buildings (footprint, count, coverage %) | ❌ | Nothing |
| 16 | POI (Foursquare/OSM) & cultural constraints (MiBAC) | 🟡 | POI ✅ via aecs4u-stats (`/api/v1/enrichment/pois/`, needs the store built); MiBAC constraints missing |
| 17 | Public REST API v2 (`/parcels/{id}?include=…`, API keys, 1K–10K req/h rate limits, data catalog docs) | ❌ | Internal API only, session-state-dependent |
| 18 | PDF export of parcel report | 🟡 | ✅ The parcel panel now opens an A4 dossier containing cadastral identity, a geometry sketch, the current enrichment cards, selected OMI inputs/results, source notes and legal disclaimer. Print mode is isolated and exposes the browser's Print / Save PDF flow. Missing: server-rendered downloadable PDF with durable report ID/signature. |
| 19 | Favorites / saved parcels | 🟡 | `saved_maps` + zones tables exist; no parcel-level favorites UX |
| 20 | Community: profiles, leaderboard, guest contributor mode | ❌ | Clerk auth exists; no community layer |
| 21 | Onboarding tour, donation/sponsor prompts | ❌ | Cosmetic, low priority |
| 22 | Basemap switcher (dark/light/satellite) + geolocation | ✅ | Folium `LayerControl` already offers 10 basemaps (superset of Zornade's 3-way toggle) + weather overlays. ✅ Geolocate: `folium.plugins.LocateControl` now added server-side in `create_comprehensive_map` (the client-side `map.js` LocateControl code was dead — targets a `#map` div that doesn't exist on the injected-HTML page) |

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

> **2026-07-11:** the schema for this now exists upstream —
> `aecs4u_domain.real_estate.models.CadastralParcel` (geometry_wkt, area_sqm,
> centroid, municipality/sheet/parcel_number, soft FK to `PropertyKey`).
> Phase 0 below should populate that table (via `aecs4u-domain`'s Alembic
> migrations or a land-registry-side importer) rather than defining a new
> parcel table locally.

> **2026-07-12 — largely done upstream:** the bulk parcel store now lives in
> **`aecs4u_stats.cadastral`** (migrated from this repo's
> `scripts/import_cadastral_to_db.py`, now deleted): per-region DuckDB stores
> built from `/data/catasto/ITALIA` via DuckDB `spatial` (no geopandas),
> geodesic `area_sqm`/centroids, and `sheet_number` derived by spatial join
> (the `NATIONALCADASTRALREFERENCE` layout varies per comune — with sezione
> `H199C0075A0.63`, without `H233_000100.1` — so it is never parsed). Consumed
> here via `stats_service.get_parcels/get_fogli/get_parcel_by_reference/
> get_parcel_at_point/get_parcels_in_bbox` and exposed at
> `/api/v1/enrichment/parcels/*`, `/fogli/*`, `/parcel/at-point`,
> `/parcel/by-reference/*`. Build stores with
> `python -m aecs4u_stats.cadastral.scripts.import_cadastral --regione <R>`.
> Remaining P1 work in this repo: repoint the legacy `CadastralDatabase`
> (SpatiaLite) paths in `routers/api.py` / `dependencies.py` /
> `datashader_service.py` at the new store, then delete `cadastral_db.py`.

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
> **2026-07-26 — superseded (partially):** downloading now belongs to
> `property-scraper`; `aecs4u-stats` keeps the merge/query/enrichment side
> only. See
> `../../__architecture__/_docs/aecs4u_realestate_pipeline_architecture.md`.
> Nothing changes for `land-registry` itself — it never called the
> downloaders directly.

*(Implemented 2026-07-11, extended same day.)* The app depends on
`aecs4u-stats` (git source in `pyproject.toml`), adapted by
`land_registry/stats_service.py` and exposed at `/api/v1/enrichment/*`:

- `GET /enrichment/status` — which stats stores/APIs are available on this host
- `GET /enrichment/municipality/{cadastral_code}` — ISTAT profile via the
  catasto comune code (e.g. `C773` → Civitavecchia, Roma, Lazio, NUTS3 ITI43)
  plus resident-population history
- `GET /enrichment/pois/?lat&lng&radius_km&categories` — OSM POIs grouped by
  category, nearest-first
- `GET /enrichment/omi/quotes`, `/enrichment/omi/history`, `/enrichment/omi/at-point`
  and `POST /enrichment/omi/estimate` — OMI sale/rent €/m² quotes, semester
  history, spatial zone matching and a versioned indicative-value contract
  (`aecs4u_stats.omi`, ported from `scripts/omi_import.py`)
- `GET /enrichment/income/{cadastral_code}` — MEF/IRPEF taxpayer count, mean
  taxable income, 8-bracket distribution (`aecs4u_stats.mef`)
- `GET /enrichment/risks/{istat_code}` — DPC seismic zone + ISPRA IdroGEO
  flood/landslide indicators (`aecs4u_stats.hazards`)
- `GET /enrichment/fires` — NASA FIRMS active-fire detections (live API,
  needs `FIRMS_MAP_KEY`)
- `GET /enrichment/bulletin` — latest DPC hydro-criticality bulletin (live API)

Everything degrades gracefully when a store/API is missing. Local stores are
built with, e.g.:
```
python -m aecs4u_stats.istat.scripts.import_data
python -m aecs4u_stats.osm.scripts.download_pois
python -m aecs4u_stats.omi.scripts.import_omi --data-dir /path/to/OMI
python -m aecs4u_stats.mef.scripts.import_irpef --year 2022
python -m aecs4u_stats.hazards.scripts.import_seismic --file classificazione.xlsx
```
IdroGEO, FIRMS and the DPC bulletin are runtime API clients with in-process
TTL caching — no local store to build.

The rule going forward: every new public dataset in the plan below (ISTAT
sezioni, DEM, CORINE, VIIRS, ANNCSU, MiBAC…) should be ingested as an
`aecs4u_stats` subpackage (models + downloader + queries, reusable by every
AECS4U app) and consumed here through `stats_service.py` — not ETL'd inside
this repo. OMI zone polygons (`aecs4u_stats.omi.boundaries`) and PVGIS solar
(`aecs4u_stats.pvgis`, 2026-07-12 — `aecs4u-energy`'s former client is now a
thin shim over it) already follow this rule (see items 6 and 9 in §2).

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

### Phase 2 — OMI + first enrichments (~1–2 weeks remaining) — *biggest bang for the buck*
1. ~~OMI: migrate `scripts/omi_import.py` into `aecs4u_stats.omi`~~ ✅ done
   (importer + queries; `/api/v1/enrichment/omi/quotes`, `/omi/history`).
   The parcel panel now provides a zone/typology/state selector, editable-area
   indicative value range and up-to-24-semester chart. The `/omi/at-point`
   spatial lookup prioritizes the zone containing the parcel centroid when its
   province GeoJSON is installed. `POST /omi/estimate` verifies the selected
   quote and returns the versioned `omi-area-range-v1` calculation used by the
   UI and future reports. Remaining: deploy all province boundary files.
2. Wire the already-shipped enrichment endpoints into the parcel detail panel
   (municipality, POI, OMI, income sections); build the ISTAT/POI/OMI/MEF
   stores in the deploy image or a mounted volume.
3. Terrain: Tinitaly 10m DEM (INGV) → `aecs4u_stats` raster-sampling subpackage
   → per-parcel elevation min/mean/max, slope, aspect, roughness.
4. Land cover: CORINE 2018 vector → majority class per parcel at ingest.
5. ~~Demographics: ISTAT sezioni di censimento (Basi Territoriali + census data)
   joined by parcel centroid, plus province-level demographic indicators~~ ✅
   consumed through `/enrichment/census/at-point` and `/enrichment/demographics/*`;
   the parcel panel exposes the derived employment, education, foreign-resident,
   vacancy and household-size indicators and degrades cleanly without the stores.

### Phase 3 — Risks + live overlays (~1 week remaining, backend done)
1. ~~Seismic: DPC municipal seismic classification (zona 1–4) by comune code~~
   ✅ done (`aecs4u_stats.hazards.seismic`, needs the DPC XLSX imported once).
2. ~~Flood: ISPRA PGRA hazard indicators~~ / ~~Landslide: ISPRA PAI/IFFI~~ ✅ done
   — `aecs4u_stats.hazards.idrogeo`, live ISPRA IdroGEO API (comune-level
   area/population % by hazard class, not yet per-parcel intersection).
3. Composite 0–100 risk score + gauge in panel header (document the formula) —
   not yet built; combine seismic zone + IdroGEO P3/P4 % into one score.
4. Live overlays — backend and core map UI done:
   - ~~NASA FIRMS active fires~~ ✅ `aecs4u_stats.hazards.firms` (needs
     `FIRMS_MAP_KEY`) → still needs a marker layer with age on the map.
   - ~~DPC bollettino di criticità~~ ✅ `aecs4u_stats.hazards.bulletins`,
     verified live; today's TopoJSON zones now render through a map toggle with
     severity colors, legend, hover labels, risk-detail popups and refresh.

### Phase 4 — Advanced enrichment (~3–4 weeks, independently shippable items)
1. ~~Solar PV client~~ ✅ done, in-process reusable — `aecs4u_stats.pvgis`
   (2026-07-12). Wire it into `stats_service.py` and add the
   cash-flow/NPV/LCOE/payback computation on top; still not consumed here.
2. ~~Economy: MEF/Dip. Finanze IRPEF income brackets~~ ✅ done
   (`aecs4u_stats.mef`, see Phase 2/§3 P4). Remaining: VIIRS Black Marble
   annual composite sampled at centroid (rural→metropoli classification).
3. Addresses: ANNCSU open data → parcel address list (fulfils
   `docs/urban_address_requirements.md`).
4. Buildings: building footprints (catasto fabbricati layer already in our WFS
   data, or OSM/Microsoft footprints) → count, footprint m², coverage %.
5. POI: ✅ already served by `aecs4u_stats.osm` via `/api/v1/enrichment/pois/`
   (build the store with the Overpass downloader or the `pbf` extra). Cultural
   constraints: MiBAC vincoli (Vincoli in Rete / Carta del Rischio) intersection.
6. ~~Printable parcel dossier with browser Print / Save PDF~~ ✅ done. Remaining:
   optional server-rendered download (e.g. WeasyPrint), durable report ID and
   integrity signature.

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
- ✅ URL `?lat&lng&zoom` **read** on the existing Folium page (2026-07-11,
  `main.py`/`map.py`). Still open: write-back (update the URL as the user
  pans/zooms — needs a `moveend` listener via `getFoliumMapInstance()` in
  `folium-interface.js` and `history.replaceState`).
- ✅ Geolocate button (2026-07-11, `folium.plugins.LocateControl` added
  server-side in `map.py`). Basemap switcher: already existed (10-layer
  Folium `LayerControl`, superset of Zornade's 3-way toggle) — no work needed.
- Welcome/onboarding modal with a short tour — not done, low priority.
- Attribution footer with data-source citations — not done; the Folium map's
  own Leaflet attribution control covers basemap tiles, but nothing credits
  the enrichment data sources (ISTAT, OMI, MEF, ISPRA, DPC, NASA FIRMS).

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
| Solar irradiance | JRC PVGIS API | integrated via **aecs4u-stats** (`aecs4u_stats.pvgis`); not yet consumed here | 4 |
| IRPEF income | MEF Dip. Finanze | integrated via **aecs4u-stats** (`aecs4u_stats.mef`) | done |
| VIIRS night lights | NASA Black Marble | open | 4 |
| Civic addresses | ANNCSU | open | 4 |
| POI | OSM via **aecs4u-stats** | integrated | done |
| Municipalities + population | ISTAT via **aecs4u-stats** | integrated | done |
| OMI quotes (comune/zone/typology + history) | Agenzia delle Entrate via **aecs4u-stats** | integrated (`aecs4u_stats.omi`); point-in-zone API/UI integrated, province GeoJSON files must be deployed | done* |
| Seismic classification | Protezione Civile via **aecs4u-stats** | integrated (`aecs4u_stats.hazards.seismic`) | done |
| Flood (PGRA) + landslide (PAI/IFFI) indicators | ISPRA IdroGEO via **aecs4u-stats** | integrated, live API (`aecs4u_stats.hazards.idrogeo`) | done |
| Active fires | NASA FIRMS via **aecs4u-stats** | integrated, live API, needs `FIRMS_MAP_KEY` (`aecs4u_stats.hazards.firms`) | done |
| Criticality bulletins | DPC open data (GitHub) via **aecs4u-stats** | integrated, live API (`aecs4u_stats.hazards.bulletins`) | done |
| Cultural constraints | MiBAC Vincoli in Rete | open (scrape/WFS) | 4 |
| Auctions | PVP portalevenditepubbliche.it | public listings | 5 |

## 6. Suggested sequencing summary

Backend enrichment data (OMI, IRPEF income, seismic, flood/landslide, active
fires, criticality bulletin) landed upstream in `aecs4u-stats` on 2026-07-11,
ahead of the phase schedule below — mostly compressing Phase 2/3 effort into
UI + integration work rather than new data pipelines.

```
P0 platform (PostGIS + PMTiles + MapLibre + /parcels/{id})   ██ 2w
   → CadastralParcel schema now exists in aecs4u-domain; P0 is "populate
     + query", not "design the schema"
P1 detail panel + finder + permalinks + favorites            ██ 2w
P2 OMI zones + terrain + CORINE                               ██ 2w (was 3w)
   → OMI quotes/history, IRPEF income already served by the API
P3 risk score + live-overlay UI + subsidence/erosion          ██ 2w (was 3w)
   → seismic, flood/landslide, fires, bulletin already served by the API
P4 solar (via aecs4u-stats), VIIRS, addresses, buildings, POI, PDF   ███ 3w (was 4w)
   → POI + PVGIS client already served; solar still needs the cash-flow/NPV
     computation and to be wired into stats_service.py
P5 public API v2 + keys + catalog + community + auctions     ███ 3w
                                              total ≈ 14 weeks part-time
```
