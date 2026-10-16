# Gap Analysis: land-registry vs. Zornade (app.zornade.com)

> **2026-10-15 consolidation note:** the focused audit of the primary `/map` parcel panel is included in
> [Appendix A](#appendix-a-parcel-panel-feature-and-gap-analysis-october-2026). Its audit findings date from
> 2026-10-09; implementation status has been updated through 2026-10-15.
> Appendix A is the current reference for panel findings where they differ from this July whole-product
> baseline. Sections 1–6 retain the broader product scope and roadmap, with status notes updated where
> implementation has since been verified.

*Baseline review: 2026-07-10, updated 2026-07-13, based on a live review of
app.zornade.com (v2.4.0). Status consolidated through 2026-10-16.*

> **2026-10-16 follow-up:** Appendix A.5.8 compares the publisher-described
> Zornade parcel-profile PDF with the land-registry Typst dossier. The dossier
> can now optionally include a server-recomputed PVGIS scenario when the user
> has successfully calculated one in the panel. A same-parcel PDF sample
> comparison remains open because no exported Zornade report was available.

> **2026-10-16 status review.** The matrix below rates 4 ✅, 3 ✅/🟡, 14 🟡 and
> 1 ❌ of 22 features (July baseline: 4, 1, 9 and 8). The ratings measure what
> is wired, not what returns data. Checked against parcel `G273_000900.3205` on
> the review host with the installed `aecs4u-stats` (`0.2.1`, `main` at
> `1109d6c`):
>
> - **Returns data:** ISPRA parcel hazards and MPS04 PGA (both from the shared
>   PostgreSQL database), the municipal composite risk score (shared helper),
>   the coastal-change store, OMI zone boundaries, income, census, POIs, PVP.
> - **Wired, no store provisioned (`not_built`):** terrain, CORINE, OSM building
>   footprints, ANNCSU addresses, protection constraints, night lights.
> - **Blocked by one database option:** EGMS parcel subsidence. The cells are in
>   the database, but the `egms_source` foreign server lacks `extensions=postgis`
>   (`scripts/sql/map-egms-fdw-pushdown.sql`, not applied), so a point lookup
>   takes about 39 s and the PostgreSQL path stays off.
> - **Upstream:** the terrain, CORINE, buildings, ANNCSU-address, composite-risk
>   and solar-economics code is merged to `aecs4u-stats` `main` and installed
>   here, but issues #28, #29 and #31–#34 are still open on GitHub; #48
>   (protection constraints) has not landed.
> - **Equivalence checks:** the shared solar-economics helper and the app's
>   compatibility calculation agree on all nine numeric fields and 26 cash-flow
>   rows for the 5 kWp reference case; the shared and compatibility composite
>   risk functions agree on seven representative cases, including partial,
>   missing and invalid inputs.
> - **Lock file:** `uv.lock` was re-locked on 2026-10-16 for `aecs4u-stats`
>   only (`5801a5e` → `a1ef4cb`, `main`), which also adds `aecs4u-documents`,
>   `typst` and `qrcode` that `pyproject.toml` already required. The deploy uses
>   `uv sync --frozen`, so the old lock would have shipped without them. `a1ef4cb`
>   is two commits past the installed `1109d6c` (an ISTAT query fallback and a
>   loader script); the installed venv has not been re-synced.
> - **Tests:** all 1,371 Python tests pass (the layer-catalog contract tests were
>   rewritten for the direct-source catalog), the JS suite passes (49) and the
>   Chromium smoke suite passes (14, 4 skipped).
>   `tests/test_corrected_s3_storage.py` cannot be collected here because `moto`
>   is not installed.
> - **Deferred by decision, not open work:** community features (row 20, SRS
>   §14), extra estimator inputs (V5), declared price (V10) and the gamified
>   tour/donation prompts. The same-parcel PDF comparison needs an exported
>   Zornade report, and the Italian copy needs a native proofread.

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

## 1. Project state at the July 2026 baseline

| Capability | Where | Notes |
|---|---|---|
| FastAPI backend + Clerk JWT auth | `main.py`, `routers/auth.py` | Solid foundation |
| Folium + Leaflet with optional WebGL (glify) | `map.py`, `static/webgl-renderer.js` | The legacy shell loads the pinned glify bundle and shared cadastral palettes for sheet, local density, property type and update date. Auto/WebGL/SVG modes, palette selection, a WebGL-unavailable notice and optional zoom-based Douglas–Peucker simplification are wired to cadastral layers. LOD uses the issue's `Math.pow(2, 16 - zoom) * 0.0001` tolerance and keeps original geometry for exports; runtime performance still needs measurement. The renderer accepts the Folium map instance instead of relying only on `window.map`. |
| Datashader raster tiles (z/x/y PNG) | `datashader_service.py` | For 100K+ features |
| FlatGeobuf per-region loading + metadata | `/api/v1/fgb/*`, `scripts/merge_fgb_per_region.py` | Regional FGB files on disk/S3 |
| NDJSON progressive streaming loader | `/api/v1/load-cadastral-files-stream/` | Phase-2 work in progress |
| Cadastral hierarchy browse (region→prov→comune) | `/api/v1/get-regions/…`, `get-cadastral-structure/` | Backed by file tree, cached |
| Parcel search (comune/foglio/particella/sub) | `/api/v1/search/parcels`, `/api/v1/map/search` | The legacy endpoint searches the loaded GDF; the primary map resolves the hierarchy on demand through per-region cadastral stores, without requiring a loaded GDF. Coverage still depends on the hierarchy index and provisioned stores. |
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

**Core architectural difference at the July baseline:** the legacy viewer
required the user to pick and load files into a global in-memory GeoDataFrame
before anything rendered. The primary `/map` now uses the allow-listed PostGIS
MVT catalog and per-region parcel search without that upload step; `/map-legacy`
retains the upload and analysis workflow. Production-wide parcel coverage and
serving-host performance remain unverified, while Zornade's whole-country
coverage remains the comparison target.

## 2. Feature gap matrix

Legend: ✅ have · 🟡 partial (some product surface exists; coverage, release or acceptance gaps remain) · ❌ missing

A 🟡 can mean the card is wired but its store is not provisioned on the review host (`not_built`); the 2026-10-16 status review at the top lists which rows return data today.

| # | Zornade feature | Ours | Gap detail |
|---|---|---|---|
| 1 | Nationwide always-on parcel map (zoom-gated vector tiles, labelled polygons) | ✅/🟡 | ✅ The primary MapLibre map uses the allow-listed PostGIS MVT catalog with a raster fallback, loads viewport parcels at z≥16, supports click selection and detail-panel integration, and renders persistent parcel-number labels from z16. Analysis tools now draw polygon, polyline, rectangle, circle and marker GeoJSON, with existing edit/import/export/save/load workflows. Remaining gap: verify production-wide parcel coverage; PMTiles is an alternate delivery path, not a prerequisite for the current MVT implementation. |
| 2 | Click parcel → detail panel (80+ fields, 17 sections) | 🟡 | The primary MapLibre map opens a sectioned, lazy-loaded panel from canonical parcel selection. It covers cadastral identity, OMI/value, census/demographics, income, SISTER/OpenData, POI, risks, live hazards, auctions, provenance and a locally provisioned coastal-change section. It is not yet equivalent to Zornade's 17 sections: terrain, CORINE and OSM footprints are wired and their `aecs4u-stats` subpackages are merged to `aecs4u-stats` main (commit `1109d6c`) and installed here, but no regional stores are provisioned (each returns `not_built`); the ANNCSU card is wired against the installed upstream importer, but no regional address store is provisioned (`not_built`). Protection constraints now have a schema-gated API, panel section and map layers, but the upstream source tables and regional coverage are not provisioned. The composite-score UI and adapter use the shared `hazards.composite_risk_score` helper, which is now installed (Palermo returns 28.6); the local compatibility copy remains only for older wheels. Target-host coverage and runtime review remain. See Appendix A for the current panel audit. |
| 3 | URL state / deep links (`?lat&lng&zoom&parcel=`) + share | ✅ | `?lat=&lng=&zoom=` is restored on `GET /map` and updated after map movement. Selected boundary parcels now write `&parcel=<national-reference>`, restore through `/parcel/by-reference/{ref}`, and expose a Copy Link action in the detail panel. |
| 4 | Hierarchical parcel finder that works without pre-loading | ✅/🟡 | The primary map now offers Regione→Provincia→Comune→Foglio→Particella browsing. It loads the hierarchy on demand and resolves the selected parcel through `/api/v1/map/search` and the per-region `aecs4u-stats` cadastral stores, without requiring a user-loaded GeoDataFrame. Available areas still depend on the hierarchy index and parcel stores provisioned on the host. |
| 5 | Cadastral base data (particella, foglio, sezione, comune, area) | ✅/🟡 | The canonical map source exposes stable feature IDs and cadastral properties; `/api/v2/parcels/{fid}` returns the basic attributes and optional geometry. Field completeness and national production coverage remain data-provisioning checks. |
| 6 | OMI quotations (per zone, by type, yields) + 22-semester history + value estimator | 🟡 | ✅ Quotes/history backend and parcel-panel UI are implemented: users select zone/typology/conservation state, enter the relevant commercial m², see an explicitly non-appraisal value range, and inspect up to 24 semesters in a trend chart. ✅ `/omi/at-point` spatially matches the parcel centroid and `POST /omi/estimate` returns a versioned, report-ready server calculation from one exact quote. All 100 cached province boundaries (50,466 polygons) are built under `/data/istat/omi_zones` on this host; a Rome point lookup matched B29. These polygons are a 2018/2 mirror and approximate where boundaries have since changed. Mount the same files on each serving host and verify parcel-level matches there. |
| 7 | Environmental risks: seismic, flood (PGRA), landslide (PAI/IFFI), subsidence (InSAR), coastal erosion + composite risk score | 🟡 | Parcel hazard polygons and EGMS 100 m subsidence cells are intersected with parcel geometry; DPC seismic classification and nearest-grid MPS04 PGA are separate components. The ISPRA 2006–2020 shoreline-change snapshot is provisioned in the configured stats database: 99,229 features across 664 municipalities; two real Civitavecchia parcels returned mapped segments, and a dense-area query correctly reports partial coverage at the 100-candidate cap. This is historical change, not a current forecast. The primary/legacy panels and v2 risk section calculate the versioned municipal index from existing seismic and hydrogeological inputs, using the shared helper when installed and an aligned app compatibility implementation for older wheels. On 2026-10-16 MPS04 PGA and ISPRA hazard polygons are read from the shared PostgreSQL database; EGMS cells are present there too, but lookups wait for `extensions=postgis` on the `egms_source` foreign server (`scripts/sql/map-egms-fdw-pushdown.sql`, not applied). |
| 8 | Live overlays: satellite fire hotspots (FIRMS), Protezione Civile criticality bulletin | ✅ | FIRMS detections have a marker-layer toggle (`FIRMS_MAP_KEY` required); marker popups show the UTC acquisition time, relative detection age and fire radiative power. DPC bulletin details appear in the parcel panel and its 187 national alert-zone polygons can be toggled on the map with red/orange/yellow/green severity styling, hover labels, detail popups, issue timestamp and legend. |
| 9 | Solar PV potential per building (PVGIS-SARAH3, cash-flow, NPV, LCOE, payback) | 🟡 | Municipality-level Zornade PV aggregates are consumed by the primary Energy section and map layer and appear in the Typst dossier with their municipal scope, source version and update time. A separate editable scenario now uses PVGIS 5.3/SARAH3 yield at the parcel centroid plus user-supplied capacity, tilt, aspect, losses and financial assumptions; it shows NPV, LCOE, payback and annual cash flows. It does not infer usable roof area or installable capacity. The shared `aecs4u-stats` calculator is used when installed; the installed shared calculator (`aecs4u_stats.pvgis.economics`) is used and a compatibility calculation remains only for older wheels; upstream #31 is still open. A live PVGIS response was parsed locally on 2026-10-16; provisioned-host behavior and timeout/unavailable states remain open. See issue draft 06. |
| 10 | Terrain: elevation, slope, aspect, roughness (TINITALY DEM) | 🟡 | The parcel API, lazy Terrain card and Typst dossier section are wired locally. The regional WCS importer and parcel query for issue #28 are merged to `aecs4u-stats` main (commit `1109d6c`) and installed here (issue #28 is still open); no regional raster store is provisioned, so runtime values are unavailable. |
| 11 | Demographics (ISTAT census sections: age, gender, indicators) | ✅ | Parcel-centroid lookup now shows the containing 2021 census section with residents, households, dwellings, buildings, employment, education, foreign-resident share, vacancy and household size. Province-level demographic series are previewed separately with explicit resolution disclosure. |
| 12 | Economy: MEF/IRPEF income distribution, VIIRS night lights | 🟡 | ✅ IRPEF done: `aecs4u_stats.mef` (comune-level income brackets, mean taxable income) via `/api/v1/enrichment/income/{cadastral_code}` — verified against real 2022 MEF data for Civitavecchia (25.5%/10.8%/26.4%/30.4% brackets, close to Zornade's reference-year figures). The VIIRS VNP46A4 importer applies the provider's 0.1 radiance scale factor and quality mask; raster query, lazy panel section and API v2 catalog entry are implemented. Install the annual local raster to serve values on a host. |
| 13 | Land cover (CORINE / Urban Atlas) | 🟡 | The CORINE panel card, dedicated API endpoint, catalog entry and dossier section are wired in the local app tree. The reusable `aecs4u_stats.corine` importer/query is merged to `aecs4u-stats` main (commit `1109d6c`) and installed here; no regional store is provisioned, so results are currently unavailable. CLC 2018 is broad context (25 ha minimum mapping unit, 100 m minimum width), not a parcel survey. See Appendix A.5.3 and issue #32. |
| 14 | Addresses (ANNCSU civic numbers) | 🟡 | Up to 10 de-duplicated SISTER address strings remain in the initial Value tab. A separate lazy ANNCSU card and `/api/v1/enrichment/anncsu-addresses/{reference}` spatially match geocoded access points; the same block is included in the dossier and `/api/v2` catalog. The regional importer/query are merged to `aecs4u-stats` main (commit `1109d6c`) and installed here, and no regional address store is provisioned. ANNCSU coordinates are present only where supplied by municipalities; match method, source vintage, EPSG:4258 and municipality coordinate coverage are carried through the response. The panel and dossier distinguish complete, partial and unknown municipal coordinate coverage from a parcel no-match. A no-geocoded-match state does not mean no civic address. See issue draft 08 and upstream `aecs4u-stats#33`. |
| 15 | Buildings (footprint, count, coverage %) | 🟡 | SISTER cadastral building/property records remain a separate source. The app has a lazy OSM footprint card, `/api/v1/enrichment/building-footprints/{reference}` adapter and dossier subsection for intersecting footprint count, area clipped to the parcel and mapped area share. The regional Geofabrik PBF importer and EPSG:3035 DuckDB query are merged to `aecs4u-stats` main (commit `1109d6c`) and installed here; no regional store is provisioned, so runtime values are unavailable. OSM mapping completeness remains unknown. See Appendix A.11 and issue draft 04. |
| 16 | POI (Foursquare/OSM) & cultural constraints (MiBAC) | 🟡 | The primary panel and map expose category counts and POI overlays when the configured store is available. The constraint endpoint, lazy card and map layers follow the protection contract; upstream source tables and regional coverage remain unavailable. |
| 17 | Public REST API v2 (`/parcels/{id}?include=…`, API keys, 1K–10K req/h rate limits, data catalog docs) | 🟡 | `/api/v2/parcels/{fid}` exposes cadastral, population, demographic, economic, nightlights, SISTER, ANNCSU, OpenData, PVP, municipality-solar and OMI blocks on request, alongside geometry and risk. ANNCSU returns matched civic points with coordinate method, vintage and municipality coordinate coverage when a regional store is provisioned; none is installed on this host. On a cold read, independent adapters are limited to requested sections; partial builds are not written as complete read models. Optional-provider failures are isolated to their sections with explicit lookup-failed reasons, preserving other requested sections. The parcel response has a typed OpenAPI section envelope, a concrete response example and independent risk-provider degradation. Risk includes DPC municipal classification, INGV MPS04 nearest-grid PGA, ISPRA parcel hazards, EGMS subsidence, optional local-store-backed coastal change and schema-gated protection constraints with explicit per-type coverage. The versioned municipal composite score uses the shared helper when installed and a compatible local implementation on older wheels; it is null when no valid municipal component is available. Upstream #34 release and representative live-comune validation remain pending. Protection data remain unavailable until their upstream table is provisioned, and provider reuse terms remain under review. Night lights report annual VNP46A4 radiance and sampling provenance when the local raster is installed. Clerk-account users can create, list and revoke up to the configured maximum of `parcels:read` keys; secrets are returned once. Each key has an atomic UTC-hour quota stored in PostgreSQL (default 10,000, configurable with `PUBLIC_API_REQUESTS_PER_HOUR`), with limit, remaining and reset headers. `/api/v2/catalog` publishes stable fields and descriptions, data shapes, matching/freshness notes and source reuse metadata. Provider-native record properties vary by release or deployment and are marked dynamic. Matched response sections include a stable `source_key` plus catalogued URL, licence/reuse status and attribution; cadastral fields now carry field-level source, match-method and resolution mappings. OpenData records now expose record-level retrieval, provider, origin, timestamps, matching and reuse metadata. OpenAPI Catasto is identified as the access service and its FAQ names Agenzia delle Entrate as the principal source, not as a source confirmed for each historic record. The OpenAPI contract restricts mass extraction and other uses; record redistribution rights and the applicable account terms remain unverified. |
| 18 | PDF export of parcel report | 🟡 | Zornade's [public guide](https://zornade.com/blog/salvare-stampare-mappa-catastale-pdf/) describes a free personal-use PDF of the parcel sheet with cadastral, risk, OMI, solar and demographic data; it says the PDF has no legal value. Its exact layout, fields and report provenance are not published, and this audit did not inspect a downloaded sample. land-registry serves a server-built A4 Typst dossier with a locator map, cadastral/location and available enrichment sections, a municipality profile with population history, province-level ISTAT quality-of-life indicators, municipality-scoped solar aggregates, source register, report ID and version metadata. After a successful Energy-tab calculation, POST can also carry the validated PV assumptions; the server recomputes the PVGIS yield and cash flows for an optional scenario section. Both are informational reports, not official cadastral extracts; land-registry also states that its dossier is not a professional appraisal. See Appendix A.5.8 for the comparison and remaining acceptance check; implementation details are in `PARCEL_DOSSIER.md`. |
| 19 | Favorites / saved parcels | 🟡 | ✅ The shortlist stores configurable status, priority, notes and tags, with inline per-item editing, status/priority/recency and active-hazard filters, and an `is_favorite` star toggled from the panel header. The FIRMS filter checks distance to parcel geometry within 25 km and shows nearest distance/acquisition time; DPC matches require parcel intersection and show bulletin issue time when available |
| 20 | Community: profiles, leaderboard, guest contributor mode | ❌ | Clerk auth exists; no community layer |
| 21 | Onboarding tour, donation/sponsor prompts | 🟡 | A dismissible three-step first-visit guide appears once per browser profile on the primary map and can be reopened from its help button. Donation/sponsor prompts remain a separate product decision. |
| 22 | Basemap switcher (dark/light/satellite) + geolocation | ✅ | The primary MapLibre map has an accessible basemap picker and a geolocation action; the legacy Folium map retains its broader LayerControl and weather overlays. |

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
AECS4U app) and consumed here through `stats_service.py`. The VIIRS Black
Marble importer/query currently live in this repo because the installed
`aecs4u_stats` release has no such subpackage; move that contract upstream
before another app consumes it. OMI zone polygons (`aecs4u_stats.omi.boundaries`) and PVGIS solar
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
4. 🟡 `GET /api/v2/parcels/{fid}` returns cadastral identity and optional WGS84
   geometry from the canonical PostGIS map source. The typed `include` contract
   also covers cadastral, census, demographics, income, SISTER, ANNCSU,
   OpenData, PVP, municipality-solar, OMI, terrain, nightlights, coastal-change
   and parcel-risk sections. `/api/v2/catalog` documents the 18 current
   sections and their stable fields, sources, coverage and methods. Runtime
   source labels are catalogued only when unambiguous. Remaining work is to
   verify the full contract against provisioned data and finish dynamic
   provider-field and reuse-term review; protection and other optional stores
   remain unavailable until provisioned.

**Exit criterion:** open the app, zoom anywhere in Italy, see parcels, click one,
get its cadastral data — no file loading step.

### Phase 1 — Parcel detail panel + finder (~2 weeks)
1. Detail panel UI (collapsible sections, header KPIs: area, comune; risk/€ m²
   placeholders until later phases).
2. ~~Rewire the hierarchical finder to avoid the loaded GDF~~ ✅ The direct-map
   finder loads the region/province/municipality hierarchy lazily, then uses the
   per-region cadastral store for sheet and parcel lookup; results fly to and
   select the matched parcel.
3. Permalink/share button; favorites: `saved_parcels` table keyed to Clerk user
   (extend `sqlite_db.py` or move to Neon).
4. 🟡 `/api/v2/parcels/{fid}` supports `basic`, `geometry`, existing cadastral,
   census, income, SISTER, OpenData, PVP, municipality-solar and OMI blocks,
   plus partial `risk`. Risk includes DPC municipal seismic classification,
   raw ISPRA/EGMS components and local-store-backed coastal change, plus the
   versioned municipal composite score from the shared helper or aligned app
   fallback when inputs are available. Upstream #34 release and live-comune
   validation remain pending. A provider registry and additional enrichment
   sections remain.

### Phase 2 — OMI + first enrichments (~1–2 weeks remaining) — *biggest bang for the buck*
1. ~~OMI: migrate `scripts/omi_import.py` into `aecs4u_stats.omi`~~ ✅ done
   (importer + queries; `/api/v1/enrichment/omi/quotes`, `/omi/history`).
   The parcel panel now provides a zone/typology/state selector, editable-area
   indicative value range and up-to-24-semester chart. The `/omi/at-point`
   spatial lookup prioritizes the zone containing the parcel centroid when its
   province GeoJSON is installed. All 100 province files are installed on the
   review host; the store contains 50,466 polygons from the 2018/2 boundary
   mirror, and a representative Rome point matched zone B29. The mirror can be
   stale where boundaries changed after 2018/2, so treat those matches as
   approximate and provision the same files on each serving host. `POST /omi/estimate` verifies the selected
   quote and returns the versioned `omi-area-range-v1` calculation used by the
   UI and future reports. Remaining: deploy all province boundary files.
2. Wire the already-shipped enrichment endpoints into the parcel detail panel
   (municipality, POI, OMI, income sections); build the ISTAT/POI/OMI/MEF
   stores in the deploy image or a mounted volume.
3. Terrain: TINITALY 10 m DEM (INGV) → regional WCS importer and zonal
   statistics are merged to `aecs4u-stats` main (commit `1109d6c`) and installed here; the parcel API/card and Typst dossier consume them once a regional store is imported. Outputs include elevation min/mean/max,
   slope, aspect, elevation range and Riley TRI.
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
3. Composite 0–100 risk score + panel badge/meter — the primary and legacy
   panels and v2 risk section consume `composite_score`. The app uses the
   shared `municipal_hazard_index_v1` helper when installed and an aligned
   compatibility copy for older wheels. The shared helper is now installed; on 2026-10-16 shared and compatibility outputs were identical on seven representative cases (complete, partial, missing and invalid inputs), and `aecs4u-stats#34` is still open.
4. Live overlays — backend and core map UI done:
   - ~~NASA FIRMS active fires~~ ✅ `aecs4u_stats.hazards.firms` (needs
     `FIRMS_MAP_KEY`); the direct-map marker popup shows UTC observation time,
     relative age and fire radiative power.
   - ~~DPC bollettino di criticità~~ ✅ `aecs4u_stats.hazards.bulletins`,
     verified live; today's TopoJSON zones now render through a map toggle with
     severity colors, legend, hover labels, risk-detail popups and refresh.

### Phase 4 — Advanced enrichment (~3–4 weeks, independently shippable items)
1. ~~Solar PV client~~ Municipality PV aggregates appear in the primary Energy
   section. A separate editable scenario now requests PVGIS 5.3/SARAH3 yield
   at the parcel centroid, scales it by user-supplied capacity, and calculates
   cash flow, NPV, LCOE and payback without requiring a footprint or inferring
   roof capacity. The panel can pass its validated assumptions to the Typst
   dossier, which recalculates the scenario server-side. The shared economics helper (`aecs4u-stats#31`) is installed and used; on 2026-10-16 the local compatibility calculator matched it on all nine numeric fields and all 26 annual cash-flow rows for the 5 kWp case, and remains only for older wheels. A live PVGIS response was parsed locally on
   2026-10-16; the verified shared-helper reference case, serving-host timeout
   behavior and rendered scenario PDF remain open.
2. ~~Economy: MEF/Dip. Finanze IRPEF income brackets~~ ✅ done
   (`aecs4u_stats.mef`, see Phase 2/§3 P4). VIIRS Black Marble VNP46A4 now
   has a token-aware annual importer, quality-filtered Italy raster, parcel
   pixel-mean query with an explicit representative-point fallback, lazy panel
   section, `/api/v2` section and catalog provenance. The local raster still
   needs to be imported on each serving host; night-light values are unavailable
   until that file is provisioned. Synthetic raster coverage verifies
   multi-pixel mean, subpixel fallback, invalid-quality and outside-coverage
   states plus required provenance tags; a subpixel fixture caught and fixed a
   zero-window rounding bug. The actual annual raster and country-wide coverage
   remain unverified. The coarse radiance is not classified into unsupported
   socioeconomic bands.
3. Addresses: the panel keeps the existing capped SISTER strings and now has a
   separate lazy ANNCSU spatial-match card, dedicated parcel endpoint, API v2
   section and dossier section. The upstream regional indirizzario/stradario
   importer and point-in-parcel query are merged to `aecs4u-stats` main (commit `1109d6c`) and installed here; no regional address store is provisioned here.
   A no-match remains distinct from a parcel without an address, and municipal
   coordinate coverage and coordinate method are reported when available.
4. Buildings: SISTER cadastral records are shown separately. The app-side OSM
   footprint endpoint, panel and dossier adapter are wired for parcel count,
   clipped footprint area and mapped share. Runtime values remain gated on a provisioned regional store (the upstream query, `aecs4u-stats#29`, is installed); OSM mapping completeness remains unknown.
5. POI: ✅ already served by `aecs4u_stats.osm` via `/api/v1/enrichment/pois/`
   (build the store with the Overpass downloader or the `pbf` extra). Cultural
   constraints: MiBAC vincoli (Vincoli in Rete / Carta del Rischio) intersection.
6. ~~Printable parcel dossier~~ ✅ done. The primary panel downloads a
   server-built Typst dossier with a report ID, version footer and source
   register; signed reports remain optional.

### Phase 5 — Public API + community (~2–3 weeks)
1. Formalize `/api/v2` as the public surface: the parcel endpoint documents
   its `include` filter, has a typed response envelope and OpenAPI example, and
   exposes the existing cadastral, census, income,
   SISTER, OpenData, PVP, municipality-solar and OMI read-model blocks with
   their source/version/coverage metadata, alongside basic and geometry. Risk
   is explicitly partial and carries DPC, INGV MPS04, ISPRA and EGMS component provenance;
   coastal change is an optional locally provisioned component, and the
   versioned municipal composite score uses the shared helper or aligned app
   fallback when valid inputs are available. The upstream #34 release and
   live-comune validation remain pending. OpenData records now include retrieval,
   provider, origin and reuse metadata, with OpenAPI's source statement marked
   as provider-level rather than record-level. Historic records do not retain
   record-specific origin or account terms, so their downstream reuse status
   remains unverified. Other endpoints, enrichment providers, complete dynamic
   field documentation and complete source attribution across response blocks
   remain open. On cold cache misses, independent adapters now run only for
   requested sections; partial results are not persisted as complete read
   models, while shared municipality and parcel context may still join related
   source data.
2. API keys: Clerk-account users can create, list and revoke up to the
   configured maximum of `parcels:read` keys at `/api/v1/user/api-keys`; raw
   secrets are shown once. Parcel reads require `X-API-Key` and atomically
   consume a per-key UTC-hour counter in PostgreSQL (10K by default, set with
   `PUBLIC_API_REQUESTS_PER_HOUR`). The additional per-IP 600/minute in-process
   limiter is a burst guard, not an account quota. Additional scopes and paid
   tiers remain future work.
3. Data-catalog docs page — `/data-sources` now combines maintained provider
   links, citations, attribution and reuse terms with a parcel-API field
   catalog for supported sections: returned field groups, matching method,
   spatial scope, version/freshness and update cadence where established.
   Key lifecycle and calling instructions are in
   [`PUBLIC_API_V2.md`](PUBLIC_API_V2.md).
   Unknown schedules and missing provider terms stay explicit. A granular
   listing of every dynamic source-returned column remains open.
4. Community (optional): public profiles, contribution leaderboard.
5. Revive auction properties with real data (PVP — portale vendite pubbliche)
   as a differentiator Zornade lacks.

### Quick wins (do anytime, < a day each)
- ✅ URL `?lat&lng&zoom` read/write on the Folium page. The server restores a
  valid center and zoom in `main.py`; `folium-interface.js` updates those
  parameters on `moveend` with `history.replaceState`, preserving other query
  parameters such as `legacy=1`.
- ✅ Geolocate button (2026-07-11, `folium.plugins.LocateControl` added
  server-side in `map.py`). Basemap switcher: already existed (10-layer
  Folium `LayerControl`, superset of Zornade's 3-way toggle) — no work needed.
- ✅ A dismissible three-step welcome tour appears once per browser profile on the primary map and remains available from the help button (2026-10-15). Donation/sponsor prompts remain a separate product decision.
- ✅ Provider citations and reuse notes are published at `/data-sources` and
  linked from the direct map header (2026-10-15). The page now also maps
  supported v2 parcel sections to field groups, methodology, spatial scope and
  version/update information, marking unknown cadence explicitly. A
  column-by-column catalog for dynamic source payloads remains open; the legacy
  Folium map still has only its basemap attribution control.

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
| Municipality solar potential | PVGIS-SARAH3-derived Zornade aggregate via `aecs4u-stats` | The primary Energy section and dossier consume municipality-wide values. Zornade's public guide says aggregates are open-licensed but does not name a licence; no formal licence is asserted. Parcel/roof economics remain unavailable pending `aecs4u-stats#31` | 4 |
| IRPEF income | MEF Dip. Finanze | integrated via **aecs4u-stats** (`aecs4u_stats.mef`) | done |
| VIIRS night lights | NASA Black Marble VNP46A4 | LAADS DAAC; authorized Earthdata token required for scripted download | 4 |
| Civic addresses | ANNCSU national/regional indirizzario and stradario | Monthly regional ZIP/CSV downloads; municipality-submitted point coordinates. Official metadata identifies ETRF2000/ETRS89 (EPSG:4258); public consultation terms state CC BY 4.0. Regional importer/query code is merged to `aecs4u-stats` main (commit `1109d6c`) and installed here; no address store is provisioned | 4 |
| POI | OSM via **aecs4u-stats** | integrated | done |
| Municipalities + population | ISTAT via **aecs4u-stats** | integrated | done |
| OMI quotes (comune/zone/typology + history) | Agenzia delle Entrate via **aecs4u-stats** | Integrated (`aecs4u_stats.omi`); the 100-province 2018/2 GeoJSON mirror is provisioned under `/data/istat/omi_zones` on the review host, with availability and a representative point match confirmed. Mount it on each serving host; boundaries are approximate where they have changed since 2018/2. | done on review host |
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
   → POI + municipality solar aggregates are already surfaced; parcel/roof
     economics, VIIRS, and upstream address/building coverage remain
P5 public API v2 + keys + catalog + community + auctions     ███ 3w
                                              total ≈ 14 weeks part-time
```

---

## Appendix A: Parcel panel feature and gap analysis (October 2026)

*Date: 2026-10-09 (audit and plan); implementation status updated through
2026-10-15 after the Phase 1–5 implementation slices, the compact panel cache
follow-up, parcel-level EGMS aggregation, section deep-link navigation, shared
cache provisioning, OMI boundary provisioning, and API/map-guide follow-ups.
Scope: the parcel detail experience that opens
when a parcel is clicked on the primary `/map` page, compared with the equivalent
modal in Zornade (app.zornade.com).*

This appendix complements the whole-product baseline in §§1–6 above and
[`MAP_SRS.md`](MAP_SRS.md) (requirements and conformance). It re-measures the
primary parcel panel against the current code, records the October 2026
implementation state, and gives a focused development plan. Data-source
details and licences for Zornade's blocks are in
[`zornade-cadastral-parcel-reference.md`](zornade-cadastral-parcel-reference.md).

### A.0 Status

| Phase | State | Evidence |
|---|---|---|
| 0 Alignment and baseline | **Local forced-build/repeat sample verifies 20 PostgreSQL hits (median 15 ms, p95 55 ms); no duplicate-reference collision was sampled. Production role and same-source latency acceptance remain open** | [PARCEL_PANEL_BASELINE_2026-10.md](PARCEL_PANEL_BASELINE_2026-10.md), [baseline script](../scripts/parcel_panel_baseline.py), [read-model SQL migration](../scripts/sql/parcel-enrichment-read-model.sql), [controlled sample](baselines/parcel_panel_controlled_2026-10-10.json); [constraints contract](PROTECTION_CONSTRAINTS_CONTRACT.md) clarified |
| 1 Primary panel and legacy parity | **Mostly done**. SISTER addresses are included in the read model and shown in the initial Value tab directly below cadastral identity; the separate ANNCSU lazy card is wired to the geocoded regional query. Both stores remain data-dependent, and live coverage is unverified. Header actions and the Italian catalog are in place. A 2026-10-15 local refresh-first sample measured cold-build p95 at 6.188 s, below the 8.140 s preliminary target, with eight available blocks; strict same-shape and production latency acceptance remain open. Independent native proofreading also remains | [panel core](../land_registry/static/parcel-panel-core.js), [section registry](../land_registry/static/parcel-panel.js), map templates, side-sheet styles, browser smoke suite, [latest baseline sample](baselines/parcel_panel_controlled_2026-10-15.json) |
| 2 Derived metrics | **App implementation and fixture tests done**; Italian-copy review and provisioned MEF/OMI quote-data review remain. The OMI boundary mirror is provisioned on the review host (100 provinces, 2018/2 vintage); deployment and representative coverage checks remain | OMI quote/history and MEF adapters; fixed-fixture metric tests; OMI estimator and income sections; local boundary availability and Rome point lookup |
| 3 Per-parcel geospatial | **Parcel hazard and EGMS grid-cell intersections implemented; MPS04 point estimate added; ISPRA coastal snapshot provisioned and queried**. The configured stats database contains 99,229 shoreline segments across 664 municipalities. Two real Civitavecchia parcels matched 35 and 67 segments; a dense-area query returned the explicit 100-candidate partial state. MPS04 and ISPRA hazard polygons are served from the shared PostgreSQL database (2026-10-16); EGMS is blocked only by the missing `extensions=postgis` pushdown. Protection constraints remain gated on `aecs4u-stats#48` | ISPRA and EGMS polygon-intersection routes and panel sections; `/mps04/pga` with nearest-grid provenance and unavailable state; coastal-store readiness, real-parcel lookups and candidate-cap behavior; protection-constraints contract |
| 4 Energy | **Municipal PV aggregates implemented in the panel and dossier**; parcel-level estimates and economics still wait for a defensible footprint and upstream work in `aecs4u-stats#31` | Energy tab and Typst dossier read the municipality solar block with its spatial scope and available source version |
| 5 Report/export | **Server-built Typst dossier implemented**; optional recalculated parcel PV scenario included when submitted; Zornade content parity, provider-term and provisioned-data reviews remain | `/parcel/dossier/{reference}` builds the A4 report from server data; GET retains the baseline report, and POST accepts validated PV assumptions only after a successful panel calculation. It includes a report ID/version footer and source register. The ReportLab `/parcel/report/{reference}` endpoints are deprecated; see `PARCEL_DOSSIER.md` and Appendix A.5.8 |
| 6 Optional | Partially implemented | Public API v2 parcel lookup, Clerk-account API-key lifecycle, settings-page controls, PostgreSQL shared quotas and `/api/v2/catalog` are available; provider-native property schemas, feedback and declared-price overlay remain |

Appendix A §§A.1–A.4.3 describe the audit **as found, before implementation**;
they remain as the rationale for the plan. §A.4.4 records the implementation
state on 2026-10-10, §A.5 rates the current panel, and open issues found while
implementing are in §A.11.

### A.1. Summary (as found)

1. **For data we already have, the main gap is presentation.** The backend
   assembles a parcel read model with a shared block envelope
   (`_build_parcel_enrichment`, called by the async details route at
   `/api/v1/enrichment/parcel/details/{ref}`); source and version metadata are
   populated where available, not for every field. The legacy page
   (`/map-legacy`, `parcel-enrichment.js`) already renders OMI estimation and
   history, income, census, risks, POIs, fires and the DPC bulletin. The primary
   `/map` panel (`renderParcelPanel` in `map-v2.js`) exposes only a small subset:
   it prints at most five scalar fields per block inside a 360 px card.
2. **The primary panel fills risk context from dedicated endpoints, not its
   read-model block.** The risk section combines comune-level DPC seismic zone
   and ISPRA flood/landslide shares with INGV MPS04 PGA looked up at the parcel
   centroid. It labels the nearest-grid estimate and its 10%-in-50-years
   assumption; the MPS04 store is not built on this review host, so the current
   host displays its explicit unavailable state. The address block also remains
   outside the read model and uses a cache-dependent SISTER lookup.
3. **Several of Zornade's parcel-level data points have no equivalent here.**
   These include terrain, land cover, parcel-intersected flood
   and landslide, parcel-level subsidence, coastal erosion, heritage and
   landscape constraints, OSM building footprints, civic addresses and
   per-building solar. Some related data exists at another resolution (for
   example, comune-level flood and landslide indicators). Some upstream
   subpackages exist in `aecs4u_stats` (`anncsu`, `egms`, `raster`, `pvgis`,
   `pv_potential`); DEM and CORINE support do not. VIIRS now has an app-side
   importer and query because the installed `aecs4u_stats` package has no
   Black Marble module; move that reusable data contract upstream before other
   applications consume it.
4. **Several derived metrics can use data already exposed.** Rental yield,
   OMI trend deltas, a within-comune zone percentile, a clearly defined
   price-to-income ratio and a grouped-data Gini estimate are feasible from
   existing quote, history and income data. They still need explicit comparison
   rules, assumptions and tests; “derived” does not make them assumption-free.
5. **We are ahead on workflow and have a useful provenance foundation.** Parcel
   blocks share an envelope for source, versions, confidence, benchmarks and
   coverage, though not every field is populated and the current
   `available: false` state does not distinguish every reason data is missing.
   Other differentiators include SISTER building records, PVP auctions, a
   cadastral purchase flow, adjacency analysis and a lifecycle shortlist. The
   plan preserves these while improving the panel.
6. **Recommended order:** make the primary panel the canonical parcel-detail
   surface, bring its existing-data sections up to legacy parity, then add
   derived metrics. Schedule new geospatial blocks only after their upstream
   data contracts and coverage are confirmed; follow with energy and report
   export. Effort estimates and dependencies are separated in §A.8.

### A.2. Method and evidence (as found)

| Source | What was done | Limits |
|---|---|---|
| Zornade, live | Authenticated session on 2026-10-09. Walked zoom 6 to 19 and opened two parcels in central Rome (`H501A048600.E` and a neighbour). Read the rendered text of every tab and scrolled each section. Recorded which backend calls the page makes; saw the PDF export action. | One municipality, urban. Response bodies were not read, so field names and payloads are inferred from rendered text and from the existing reference document. The PDF was not downloaded or inspected. Mobile layout and logged-out behaviour not examined. No form values were changed. |
| Zornade, published PDF description | Read the official [PDF and map export guide](https://zornade.com/blog/salvare-stampare-mappa-catastale-pdf/) (published 2026-01-25), which describes the parcel-sheet PDF as free for personal use, lists cadastral, risk, OMI, solar and demographic content, and says it has no legal value. | This is the publisher's description, not an inspection of an exported report. It does not specify the exact fields, pagination, source/version metadata, locale, or missing-data treatment. |
| Zornade, saved-parcel PDF description | Read the [saved-property workspace guide](https://zornade.com/blog/schede-immobiliari-salvate-workspace-2026/) (published 2026-10-02), which says a saved parcel can be exported in one click, the PDF carries a generation date and sources, and it includes cadastral identity, area, OMI comparison, coefficient-based estimate, risks, territorial data, addresses and constraints. | This is a newer publisher description and narrows the known scope, but still is not a downloaded PDF; exact field labels, page layout, source/version detail and unavailable-data treatment remain unverified. |
| land-registry, code | Read the primary and legacy panel code, enrichment router and service, map-layer catalog, SRS, July gap analysis, issue drafts and protection-constraints contract. The `aecs4u_stats` package list is an environment snapshot. | Static reading at audit time. Since then the panel has been run on a dev host (port 8011) and measured; see the baseline document. That host lacks several stores, so source availability there is not representative of production. |

Labels used below: **Observed** (seen on Zornade), **Verified** (read in this
repository), **Inferred** (reasoned from either, not confirmed).

Key implementation anchors: the primary renderer is `renderParcelPanel` in
[`map-v2.js`](../land_registry/static/map-v2.js); the block list, envelope and
payload assembly are `_PARCEL_DETAIL_BLOCKS`, `_detail_block` and
`_build_parcel_enrichment` in [`stats_service.py`](../land_registry/stats_service.py);
the route is in [`routers/enrichment.py`](../land_registry/routers/enrichment.py);
the legacy cards are in
[`parcel-enrichment.js`](../land_registry/static/parcel-enrichment.js).

This is a feature-level comparison. It does not propose copying Zornade's code,
text or data; the SRS derives requirements from presentation and provenance
patterns. Data-source licensing is tracked in the reference document and must
be checked independently before adding a new source.

### A.3. What Zornade's parcel modal contains (Observed, as found)

A centred sheet about 720 px wide on a 1280 px viewport, scrolling as one page,
with:

- **Header:** parcel label, comune, favourite, share, PDF export, close.
- **Save prompt:** a dismissible banner offering alerts for fire and criticality.
- **Stat strip:** area, building count, a 0 to 100 risk ring, price per m².
- **Tab bar** (sticky): Valore, Immobile, Energia, Territorio, Contesto,
  Community. These are scroll anchors with scroll-spy, not separate panes. The
  active tab also changes which sections start expanded.
- **Cards**, each with an icon, title, optional count badge, and an italic source
  line.

| Tab | Section | Content |
|---|---|---|
| Valore | Catastale | Parcel, ID, administrative unit, sheet, urban section |
| Valore | Superficie e posizione | Area, comune, region, province, CAP, main address |
| Valore | Valutazione | Inputs (area gross/net, property type, rooms, baths, condition, floor, lift, parking, outdoor, constraints) and optional declared price. Output: point estimate, range, reliability label, min/central/max per m², the exact OMI row used, trends at 6 months and 1, 3, 5, 10 years, zone percentile in the comune, gross rental yield, methodology note |
| Valore | Quotazioni OMI | All property types and states: sale band, rent band, yield, trend, sale-versus-rent comparison |
| Valore | Storico OMI | 22-semester chart, min to max band, rent line |
| Immobile | Edifici | Built-coverage grid, residential split, footprint, count |
| Immobile | Suolo | CORINE class, subclass, detail |
| Energia | Potenziale fotovoltaico | Roof compass, 20-year cash-flow curve, payback, NPV, LCOE, kWp, kWh per year, per-building viability, custom-system simulator, household consumption inputs |
| Territorio | Terreno | Elevation range, slope, aspect rose, roughness |
| Territorio | Rischi ambientali | Bars for seismic zone and PGA, subsidence, vertical movement |
| Territorio | Vincoli culturali | Protected assets with declared/unverified status |
| Contesto | Economia | Mean income, Gini gauge, price-to-income gauge, night-light scale, income distribution |
| Contesto | Demografia | Gender split, dependency and employment rings, age pyramid, education bars |
| Contesto | Indirizzi, POI | Address list, nearby places with category chips |
| Community | Contribution prompts | Crowd inputs on building, market, risks, cadastre |

Page behaviour (Observed): sections fill in progressively; the URL carries
`?parcel=<id>`; the whole profile comes from one parcel-detail RPC plus a
separate OMI-zone call; opening a parcel also records a valuation observation
on the signed-in account.

### A.4. State of the land-registry parcel panel at audit (Verified, as found)

#### A.4.1. Three layers, unevenly connected

| Layer | State |
|---|---|
| **Data and API** | `_build_parcel_enrichment` assembles the payload used by both the synchronous service helper and async parcel-details route. Populated blocks include `basic`, `cadastral`, `population`, `demographics`, `economics`, `nightlights`, `buildings` (SISTER), `opendata`, `pvp` and `valuation`; nightlights remain unavailable until a local raster is installed. Other names initialized as unavailable and not populated by this builder include `address`, `addresses`, `risk`, `subsidence`, `terrain`, `land_cover`, `land_use`, `valuation_history`, `coastal_erosion`, `cultural_heritage`, `solar` and `poi`. Separate endpoints exist for OMI quotes, history, at-point, estimate, income, risks, POIs, census, fires and bulletin. A forced local panel rebuild/repeat sample measured PostgreSQL cold and hit paths; deployed-role and duplicate-reference acceptance remain open. |
| **Legacy panel** (`/map-legacy`) | `parcel-enrichment.js` (about 1,100 lines) renders municipality, buildings, OpenData, PVP, OMI with typology and state selectors and a local range preview, server estimate, OMI history chart, income with benchmark, census indicators, crime, risks, POIs, fires, bulletin, provenance chips and API-response previews. |
| **Primary panel** (`/map`) | `renderParcelPanel` in `map-v2.js`: hero, two KPIs (area, sheet), a five-row table, then one `<details>` per block listing up to five scalar key/value pairs, then "All attributes". 360 px floating card; bottom sheet under the mobile breakpoint. Actions: purchase query, save, adjacent parcels, copy link, clear, open analysis (legacy), print report (legacy). |

#### A.4.2. Consequences

- Clicking a parcel on `/map` gives less information than clicking it on
  `/map-legacy`. Users reach the OMI estimator only by leaving the primary map.
- The `risk` and `address` labels in `blockLabels` do not render from the read
  model because those blocks are not populated by its builder. The legacy risk
  card instead calls the separate comune-level risks endpoint.
- Object-valued fields are dropped (`typeof value !== 'object'`), which removes
  the OMI quote list, income brackets and building records from the primary view.
- `docs/PROTECTION_CONSTRAINTS_CONTRACT.md` is the cross-repository schema and
  behaviour contract. Since the 2026-10-09 audit, the app has added
  `GET /api/v1/enrichment/parcel/constraints/{reference}?id={polygon id}`, a
  lazy panel section, and schema-gated map layers. The polygon ID is required
  because references can identify more than one feature. Live records remain
  unavailable until upstream tables and coverage are provisioned.
- At audit there was no shared renderer module. The migration made `/map` the
  canonical parcel panel; extending both panel implementations would have kept
  two sources of UI behavior.
- Existing contract tests pin both pages by reading the JS and templates
  (`test_map_v2_contract.py` 27 tests, `test_parcel_click_frontend_contract.py`,
  `test_omi_estimator_frontend_contract.py`, `test_parcel_report_frontend_contract.py`).
  Refactors must update them deliberately.

#### A.4.3. Upstream capability (`aecs4u_stats`, Verified by listing)

Present: `anncsu`, `cadastral`, `cap`, `census`, `egms`, `ghsl`, `hazards`,
`istat`, `mef`, `omi`, `osm`, `pvgis`, `pv_potential`, `raster` (population
rasters only), `sezioni_urbane`, `wsf`, `zornade_parity`. Absent: DEM terrain,
CORINE, VIIRS night lights, cultural constraints, per-building solar
economics. The installed `hazards.ispra_mosaics` package now supplies national
PAI/PGRA polygons, which the app intersects with the authoritative parcel
geometry; `egms` supplies 100 m cells that the app intersects with the parcel
polygon and aggregates by overlap area. The July rule stands:
datasets are ingested as `aecs4u_stats` subpackages and consumed through
`stats_service.py`, not ETL'd inside this repository. ISPRA's coastal-change
layer is now identified, but no installed `aecs4u_stats` query/store contract
supports it yet.

**Re-check after the upgrade (2026-10-10, package 0.2.1, `uv.lock` still pins
0.1.0).** The Python package still reads hazards and EGMS from per-host
DuckDB/SQLite files, but the shared stats database now exposes them as foreign
tables: `hazards.mps04_points` (17,602) and `mps04_hazard_curve` (1.67 M),
`hazards.flood_hazard` (P1–P3) and `landslide_hazard` (AA, P1–P4), and
`geo_surface_change.egms_subsidenza` (8.5 M cells). The app reported all three
as `not_built` because it only probed the file stores. MPS04 PGA is now read
from PostgreSQL first (`aget_mps04_pga_at_point`, 0.28 s; 0,154 g at the
sample parcel, nearest grid point 958 m away), with the file store as
fallback. The parcel-scale hazard card now takes its ISPRA candidates from
PostgreSQL as well (`aget_parcel_hazard_intersections`, about 0.25 s; the
sample parcel intersects no mapped polygon although its municipality shares
are high). The same intersection code serves both stores. EGMS point lookups
take about 39 s because the `egms_source` foreign server lacks
`extensions=postgis`, so the predicate is not pushed down. The EGMS PostgreSQL
path (`aget_parcel_subsidence_intersections`) therefore stays off until the
option is set and reports `egms_postgres_pushdown_not_configured`
(`scripts/sql/map-egms-fdw-pushdown.sql`, not applied). A failed PostgreSQL
lookup backs off for 60 s and uses the file store, so the first requests after
a dev reload can show the cards as unavailable. Still absent from the installed
stats package or its configured stores are DEM, CORINE, building footprints,
protection constraints, and the OMI quote-fact schema (OMI stays the one-row-
per-zone snapshot). The app now has local calculations for composite risk and
solar economics; the corresponding shared implementations are now installed from `aecs4u-stats` main and were verified equivalent on 2026-10-16.
Coastal change has since been provisioned in the configured stats database.

#### A.4.4. State after implementation slices on 2026-10-10

- `/map` renders a section registry (`parcel-panel.js`) with 21 sections in
  five subject tabs plus a Data tab: Value (cadastre, OMI, PVP), Property
  (address, buildings, OpenData),
  Territory (municipal risks, parcel hazards, Agenzia Demanio concessions,
  parcel-level EGMS summary, bulletin, fires), Context (municipality, income,
  census, safety, demographic and quality indicators, POIs), Energy (municipal
  solar aggregates) and Data (coverage). Pure logic lives
  in `parcel-panel-core.js` and is unit-tested under Node; each page keeps its
  own views, because the legacy cards depend on Bootstrap, Font Awesome,
  hard-coded Italian and fixed element IDs.
- Each section is a native `<details>` disclosure, open by default and
  individually collapsible. This closes the interaction part of MAP-FR-031;
  source coverage is still host-dependent.
- Sections call the existing `/api/v1/enrichment` endpoints. Identity renders
  from the tile feature without waiting for the slow parcel read model, which
  supplies provenance, the OpenData and solar cards, and the coverage section.
- The Property tab has a dedicated Cadastral OpenData card. It renders records
  from the `opendata` block already present in the parcel read model, includes
  the block provenance, and distinguishes an unavailable read model from an
  available profile with no usable record. The card adds no upstream query;
  live OpenData database readiness and representative match coverage remain
  unverified on this host.
- Panel detail requests include the selected canonical map feature ID. The
  PostgreSQL and SQLite caches scope those reads by both reference and feature
  ID, and validate the cached identity before returning it; a reference shared
  by multiple polygons cannot reuse another polygon's enrichment. If the
  canonical source is unavailable, an ID-specific request returns 503; if the
  reference and ID do not resolve to a feature, it returns 404 instead of
  guessing from the reference alone.
- `/parcel/at-point` prefers the canonical PostGIS feature when it is
  available, so a point lookup returns the same stable feature ID used by map
  tiles and the enrichment caches. It falls back to the local cadastral store
  when the canonical query fails or has no match.
- The read-model migration was applied and rechecked against shared
  `aecs4u-stats` on 2026-10-10. The table has the required columns and primary
  key; the migration grants `SELECT`, `INSERT` and `UPDATE` to `postgres`. The
  configured connection confirmed database `aecs4u-stats`, user `postgres`,
  and `rolsuper = true`. The operator reports that Cloud Run's
  `STATS_POSTGRES_DSN` uses the username `postgres`; GCP Secret Manager could
  not be independently inspected because its credentials require interactive
  reauthentication. The local `/api/v1/enrichment/status` endpoint reports
  `available: true` with no reason, and a subsequent 20-parcel sample used
  PostgreSQL for all 40 detail responses (median 9 ms, p95 15 ms). That capture
  predates the `cached` flag correction and does not establish cache-hit count.
  A new refresh-first sample verified 20 forced rebuilds and 20 subsequent
  PostgreSQL hits (repeat median 15 ms, p95 55 ms); all 40 responses preserved
  the requested canonical feature ID. It found no duplicate-reference group.
  The table grants are narrowly scoped in SQL, but they do not make a
  superuser least-privileged: `postgres` retains its pre-existing database-wide
  powers. The operator says Neon is the likely production target; before that
  cutover, provision a dedicated non-superuser runtime role, apply the same
  table grants there, and verify the deployed identity against that database.
- The read-model `risk` block remains unfilled. The `address` block includes
  SISTER address strings when matching cached property records contain them
  and marks that coverage partial. The visible address card still loads from
  its dedicated SISTER section endpoint rather than that read-model block.
  Municipal risks, parcel hazard and EGMS intersections come from independent
  section endpoints. `/map` downloads a server-built Typst dossier from
  `/api/v1/enrichment/parcel/dossier/{reference}`; it gathers current data on
  the server and does not use the browser's loaded section text. `/map-legacy`
  retains its browser print flow.
- The Data tab reports parcel read-model block coverage only. It marks partial
  blocks as partial, includes source and dataset version when present, and says
  that independently loaded panel sections can have data even when their
  corresponding read-model block is empty. The block `coverage` field now uses
  the shared `full` / `partial` / `unavailable` vocabulary; the legacy
  `coverage_status` alias carries the same values. A section-level 404 is
  presented as no matching data, while 503, network and 5xx failures are
  presented as temporary and retain retry. The UI does not infer geographic
  non-coverage or a failed determination when a source provides no such state.
- **Superseded 2026-10-10:** the earlier ReportLab `/parcel/report/{reference}`
  POST accepted a bounded browser-section snapshot and GET used server-only
  data. Both paths added report IDs, available versions and attribution. The
  current Typst dossier at `/parcel/dossier/{reference}` ignores request bodies
  and builds its sections from server-side sources; see Phase 5 and
  [`PARCEL_DOSSIER.md`](PARCEL_DOSSIER.md).
- The obsolete `renderParcelPanel` block table and `blockMetadataHtml` were
  removed from `map-v2.js`; `renderParcelPanel` is now thin page glue.
- Tabs now write and restore Italian tab fragments (`#valore`, `#immobile`,
  `#territorio`, `#contesto`, `#energia`, `#dati`); map viewport updates
  preserve the fragment. Individual cards restore from `#sezione-{id}` (and
  their DOM-ID form), and the scroll spy tracks the selected card in the URL.
  Deep-link alignment is maintained while earlier cards finish loading.
- Phase 2 adds gross rental yield, within-comune sale-price
  percentile, OMI trend deltas, price-to-mean-taxable-income and grouped-data
  Gini estimates. Each server response identifies its model version, and the
  panel labels the geographic and methodological limits. Fixed-fixture tests
  cover the arithmetic, adapter output and estimate route. Provisioned-data
  review remains, so the computed values have not been checked against live
  MEF and OMI responses.
- Phase 3 adds ISPRA hazard-polygon and EGMS 100 m cell intersections against
  the canonical parcel polygon. Both queries cap bbox candidates at 5,000 and
  mark partial results. EGMS velocity and acceleration are weighted by the
  intersected area; the panel reports the parcel area covered by returned cells
  and does not claim complete source coverage.
- Safety and quality-of-life routes prefer the local province/BES results and
  can fall back to country-level relocation indicators when those have no
  matching rows. The routes still label that fallback as country scope, but the
  panel no longer renders it: a country-wide index does not describe the
  parcel's province, so those cards show a province-level "not available"
  state. Quality of life leads with up to eight curated indicators (newest
  value, year and unit each) and keeps the full year tables behind a filter.
- Cards whose source cannot answer for the parcel (`unavailable`: SISTER cache
  misses, parcel hazards, EGMS, bulletin, country-only fallbacks) start
  collapsed with "Not available" in the header, while real negatives (no
  concession, no fire, no POI) stay open. The municipal solar card also starts
  collapsed, shows its high-viability share in the header and scales totals to
  MWh/GWh. Deep links still open the targeted card.
- The map catalogue now follows `?lang=` and the theme's `language` cookie as
  well as `lang`: before, the header picker changed the chrome but the panel
  stayed in the Accept-Language locale. App pickers write both cookies. Demographic
  routes can use the municipality population series, including the available
  age split, and identify it as municipality scope. These fallbacks improve
  availability without changing the geographic meaning of the source values;
  schema and coverage still require review against a provisioned production
  store.

**OpenData record provenance follow-up (2026-10-15):** the API and panel
read-model records now expose retrieval store, record ID, endpoint, timestamps,
cadastral match method, access provider, source-origin basis and reuse
terms/status. The primary parcel panel displays the available provider, source,
record identifier, retrieval store, endpoint, timestamps, match method and reuse
status on each OpenData record, including provenance-only records with no
recognized cadastral fields. The OpenData PostgreSQL adapter is backed by
OpenAPI Catasto.
OpenAPI names Agenzia delle Entrate as the principal source for the service,
but its historical stored query rows do not identify the source per record.
The published API terms restrict mass extraction and other uses;
redistribution rights and the applicable account agreement remain unverified.
See [`PUBLIC_API_V2.md`](PUBLIC_API_V2.md) and the OpenAPI [Catasto FAQ](https://console.openapi.com/it/apis/catasto/faq)
and [API terms](https://openapi.it/documenti/Termini-e-condizioni-Openapi.pdf).

#### A.4.5. Local coastal-change implementation (2026-10-15)

The ISPRA coastal-change slice now has a local PostGIS snapshot importer,
indexed 1 km parcel query, independent panel section, v2 API section and risk
component, and Typst dossier source-register entry. Its documented
classification uses a 5 m threshold; RNDT reports 5 m positional accuracy and
CC BY 4.0. On 2026-10-15, the configured stats database was provisioned from
the complete ISPRA object-ID list: 99,229 features across 664 municipalities,
with 2006–2020 vintage. Two real Civitavecchia parcels returned mapped lines;
a dense-area query reached the 100-candidate cap and returned
`coverage_status: partial`. The local readiness probe reports the source release
and import timestamp. Replicate the snapshot to each target stats database and
review source coverage there. An unmatched parcel remains unknown and the
historical layer is not a current forecast.

#### A.4.6. Address presentation follow-up (2026-10-15)

The eager SISTER Main address section now appears in the initial Value tab,
directly below cadastral identity. It shows up to ten de-duplicated strings
matched to the parcel and keeps the cache-coverage and non-geocoded caveat.
A separate lazy ANNCSU card now spatially matches geocoded civic points and is
also available through the parcel API and dossier. The upstream importer/query is merged to `aecs4u-stats` main (commit `1109d6c`) and installed here, but regional data provisioning is still needed for field verification; see [issue draft 08](github_issues/zornade_map_enrichment/08-civic-addresses-anncsu.md).

### A.5. Feature matrix

*The Zornade and Legacy columns reflect the 2026-10-09 audit. The Primary
column and implementation notes reflect the state through 2026-10-15.*

Rating: ✅ equivalent or better · 🟡 data or code exists, product surface
incomplete · ❌ missing · — comparison not assessed. "Primary" is `/map`;
"Legacy" is `/map-legacy`.

#### A.5.1. Shell and interaction

| # | Capability | Zornade | Backend | Primary | Legacy | Rating |
|---|---|---|---|---|---|---|
| S1 | Panel form factor | Centred sheet, about 720 px, one scroll | n/a | Side sheet, 380 to 540 px, docked right; bottom sheet at tablet and mobile widths | Sidebar-style panel | ✅ |
| S2 | Sticky header with actions | Yes | n/a | Fixed header exposes accessible Copy link, Download PDF and Close actions; purchase, save, adjacent, clear and legacy actions remain in the fixed bottom bar | Partial | ✅ |
| S3 | Stat strip (area, buildings, risk, price) | Yes | Data partly present | Six-cell strip: area, sheet, OMI sale price, municipal flood/landslide class (worst of the P3 and P4 shares, unknown shares skipped, not a score), OSM-mapped footprint count when its source is available, and a 0–100 municipal composite score meter when valid. Seismic zone stays in the Risks card. The SISTER unit count remains separate because it is not a physical footprint count. The score cell labels municipal scope and its tooltip says it is not parcel risk | No | 🟡 |
| S4 | Section navigation (tabs, scroll-spy) | Yes | n/a | Tab bar with scroll-spy, tab-fragment restoration, and direct card links using `#sezione-{id}` | No | ✅ |
| S5 | Card with icon, count, source line | Yes | Shared envelope has a `source` field; values may be null | Per-card footer: source, dataset and model version, match method, resolution, benchmarks; icon and count badge | Footnotes | ✅ |
| S6 | Progressive loading with skeletons | Yes | Latest local refresh-first sample: cold-build p95 6.188 s and PostgreSQL repeat-hit p95 0.157 s; response had eight available blocks | Per-section skeletons; sections load lazily and independently; 503 failures can be retried; 404 sources show unavailable without retry. The first nearby-PVP request now returns while its shared snapshot warms; the underlying national view load remains expensive | Per-card loading | ✅ |
| S7 | Deep link to a selected parcel | `?parcel=<id>` | `/parcel/by-reference` | ✅ `?parcel=<reference>` | ✅ | ✅ |
| S8 | Share, save | Favourite, share | `/saved-parcels` | ✅ Copy link, Save | ✅ | ✅ |
| S9 | Printable or PDF report | PDF action observed; official guide describes a free personal-use parcel sheet covering cadastral, risk, OMI, solar and demographic data; exact output not inspected | No public sample or documented report schema; exact fields, provenance and pagination unknown | Server-built A4 Typst dossier from current server data; locator map, cadastral/location, available enrichment sections, municipal profile/history, province-level ISTAT quality-of-life indicators, municipality-scoped solar, source register, report ID and version metadata | Browser print flow | 🟡 PDF capability exists; content parity is unverified (see A.5.8) |
| S10 | Mobile layout | Not examined | n/a | Bottom sheet; actions in one scrolling row (checked at 390 px) | n/a | ✅ |
| S11 | Shortlist with lifecycle, notes, tags | No (favourite only) | ✅ | ✅ Status, priority, notes and tags can be edited per item; filters cover lifecycle, priority, recency, favourites and active hazards | ✅ | ✅ ahead |

#### A.5.2. Value tab

| # | Capability | Zornade | Backend | Primary | Legacy | Rating |
|---|---|---|---|---|---|---|
| V1 | Cadastral identity (sheet, section, comune code) | Yes | ✅ `cadastral` | ✅ five-row table | ✅ | ✅ |
| V2 | OMI zone for the parcel | Yes | ✅ at-point, spatial join | Detected zone is shown and flagged when it has no quotes; source, match method and spatial resolution appear in the provenance footer. Local mirror matches disclose their 2018/2 boundary vintage and warn that the geometry is approximate | ✅ | ✅ |
| V3 | Quote table by type and state | Yes | ✅ `/omi/quotes` | Collapsible table lists all current municipality quotes by zone, type and condition, with sale/rent bands and derived metrics; estimator selector remains capped at 80 valid sale rows | ✅ | ✅ |
| V4 | Estimate with range and reliability label | Yes | ✅ `POST /omi/estimate`, versioned | Range preview, then server-verified calculation with model and dataset version | ✅ range, preview, server calc | ✅ |
| V5 | Estimator inputs | Area, type, rooms, baths, condition, floor, lift, parking, outdoor, constraints | Typology, state, area only | ❌ | Typology, state, area | 🟡 Expand only inputs that change the result |
| V6 | OMI history chart | 22 semesters | ✅ `/omi/history` | SVG min to max band with mean line, up to 24 semesters | ✅ up to 24 | ✅ |
| V7 | Trend deltas (6 m, 1, 3, 5, 10 y) | Yes | ✅ Versioned history deltas, same type/state, with compared periods | ✅ 6-month to 10-year rows under the chart | ❌ | ✅ |
| V8 | Rental yield | Yes | ✅ Annualized rent midpoint ÷ sale midpoint | ✅ Gross yield, with costs/taxes caveat | ❌ | ✅ |
| V9 | Zone percentile within comune | Yes | ✅ Midrank of per-zone median sale midpoints for the same type/state; requires at least 5 comparable zones | ✅ Percentile and comparable-zone count, or an insufficient-coverage message | ❌ | ✅ |
| V10 | Declared price override | Yes | Needs storage | ❌ | ❌ | ❌ Defer (see §A.9) |
| V11 | Modelled-value labelling, confidence, versions | Short reliability label | ✅ envelope | Chips on some blocks | ✅ | ✅ ahead |
| V12 | Benchmarks beside values | Partial | ✅ envelope | Year-labelled national density reference; same-tax-year national MEF income mean when available, with an official €22,810 static fallback for 2022 only; OMI median for the selected type, condition and semester with comparable-zone count, omitted below two zones | ✅ density, year-matched income when available, and OMI use the same comparison basis | ✅ |
| V13 | Auction records (PVP) | No | ✅ `pvp` block | Section with listings, capped at 8, http(s) links only | ✅ card | ✅ |
| V14 | Cadastral purchase workflow | No | ✅ | ✅ button | No | ✅ ahead |

#### A.5.3. Property tab

| # | Capability | Zornade | Backend | Primary | Legacy | Rating |
|---|---|---|---|---|---|---|
| P1 | Building count, footprint, coverage | OSM footprints | App adapter and parcel query are wired; upstream regional importer/query for [`aecs4u-stats#29`](https://github.com/aecs4u/aecs4u-stats/issues/29) is installed from `main` and no regional store is provisioned | Separate lazy OSM footprint card reports count, clipped area and mapped share when data are available; SISTER records remain a distinct source | Buildings card (SISTER) | 🟡 App path implemented; source provisioning and completeness remain |
| P2 | Cadastral building records, categories | No | ✅ SISTER, OpenData | Buildings and a dedicated OpenData card render their respective read-model records; OpenData store readiness is unverified | ✅ | ✅ |
| P3 | Land cover (CORINE) | Yes | App-side endpoint/query adapter and upstream regional store/importer are installed; no store is provisioned (`[aecs4u-stats#32](https://github.com/aecs4u/aecs4u-stats/issues/32)` remains open) | Lazy card shows class, area share, mapped coverage and resolution caveat when data are available | Included when source data match; otherwise explicitly unavailable | 🟡 Consumer path exists; upstream release and per-region import remain |
| P4 | Urban land use | Only in functional urban areas | ❌ | ❌ | ❌ | ❌ Low value |
| P5 | Addresses | Yes (ANNCSU) | SISTER `visura_properties.address` plus ANNCSU regional indirizzario point-in-parcel query; each source remains distinct | SISTER summary in initial Value tab; separate lazy ANNCSU card, API v1/v2 block and dossier section. No regional ANNCSU store provisioned on this host | ❌ | 🟡 Partial: spatial query is wired, live data coverage not verified |

#### A.5.4. Energy tab

| # | Capability | Zornade | Backend | Primary | Legacy | Rating |
|---|---|---|---|---|---|---|
| E1 | Municipal PV aggregates | No | ✅ `mp.pv_*` in context, map layer `solar-potential` | Energy tab shows municipality output/capacity/viability aggregates with available version and update metadata; clearly not parcel or rooftop estimates | No | ✅ |
| E2 | Per-building yield and viability | Yes | ❌ no per-roof model (OSM roof attributes are mostly absent, per `pv_potential.py`) | ❌ | ❌ | ❌ |
| E3 | Cash flow, payback, NPV, LCOE | Yes | ✅ PVGIS 5.3/SARAH3 centroid yield with user-supplied system and financial assumptions; shared stats calculator preferred, app compatibility calculation for older wheels | ✅ Editable Energy-tab scenario and annual cash-flow results | ❌ | 🟡 Roof area and installable capacity still need a defensible building-level source; upstream #31 remains open |
| E4 | Custom system simulator | Yes | ❌ | ❌ | ❌ | ❌ Later |

#### A.5.5. Territory tab

| # | Capability | Zornade | Backend | Primary | Legacy | Rating |
|---|---|---|---|---|---|---|
| T1 | Seismic zone | Zone and PGA | ✅ DPC comune zone + INGV MPS04 nearest native grid | Comune-level zone and parcel-centroid PGA (10% exceedance probability in 50 years); PGA is explicitly a nearest-grid estimate with no interpolation. PGA is read from the shared PostgreSQL database (2026-10-16). | ✅ | 🟡 |
| T2 | Flood and landslide | Per parcel, worst class | ✅ ISPRA national PAI/PGRA polygons; parcel-polygon intersection | Per-parcel intersected area and highest class, plus separately labelled comune shares | ✅ comune level | ✅ |
| T3 | Subsidence | Per parcel | ✅ `aecs4u_stats.egms`, bounded 100 m grid-cell bbox query | Intersecting cells summarized by area-weighted velocity and acceleration, movement-class area and coverage percentage; partial-query state shown | Layer only | ✅ |
| T4 | Composite risk score | Ring in header | 🟡 Shared helper preferred; app compatibility copy applies the same versioned equal-weight municipal formula on older wheels. [`aecs4u-stats#34`](https://github.com/aecs4u/aecs4u-stats/issues/34) remains open | 0–100 meter in the six-cell header strip and numeric badge in the Risks section when valid; absent/invalid values leave the other risk components unchanged | Legacy meter when valid; no score row when absent; Typst dossier shows the score and scope note | 🟡 Verify complete and partial real-comune responses and align with the released shared helper |
| T5 | Terrain (elevation, slope, aspect, roughness) | Yes | 🟡 Regional INGV TINITALY WCS importer and parcel query merged to `aecs4u-stats` main and installed (`[aecs4u-stats#28](https://github.com/aecs4u/aecs4u-stats/issues/28)` remains open); no region is provisioned | Parcel API, lazy card and Typst dossier are wired locally; values await the upstream package release and regional raster store | ❌ | 🟡 |
| T6 | Coastal erosion | Within 1 km of coast | Local PostGIS snapshot of ISPRA `Dinamica_Litoranea_2006_2020`; refresh via `scripts/import_ispra_coastal_dynamics.py` after applying its SQL schema | ✅ Lazy panel section; historical class, distance, 5 m threshold and 5 m positional accuracy | ❌ | 🟡 Snapshot provisioned in the configured stats database (99,229 features); real-parcel matches and 100-candidate partial state verified. Target-host coverage remains to review. An unmatched parcel has unknown coverage, not a no-erosion result |
| T7 | Heritage and landscape constraints | Heritage only | 🟡 App query contract and readiness checks are wired; source tables and regional coverage depend on [`aecs4u-stats#48`](https://github.com/aecs4u/aecs4u-stats/issues/48) | Lazy card lists records and overlap, labels catalogue-only records as non-binding, and preserves unknown coverage; live rows await source data | ❌ | 🟡 Upstream importer/schema and representative coverage remain |
| T8 | Live fire and criticality bulletin | Map overlay | ✅ | Bulletin and fires sections plus map layers | ✅ cards | ✅ |

#### A.5.6. Context tab

| # | Capability | Zornade | Backend | Primary | Legacy | Rating |
|---|---|---|---|---|---|---|
| C1 | Income | CAP level | ✅ comune level, brackets | Income section with benchmark | ✅ with benchmark | ✅ |
| C2 | Income distribution chart | Yes | ✅ brackets | Bracket bars in the income section | ❌ | ✅ |
| C3 | Gini, price-to-income | Yes | ✅ Versioned grouped-data Gini and selected-area price/value divided by mean taxpayer income | ✅ Both shown with assumptions and household-affordability caveat | ❌ | ✅ |
| C4 | Night lights | Yes | ✅ NASA Black Marble VNP46A4.002 local annual raster; import with `scripts/import_viirs_nightlights.py` | Lazy panel section and `/api/v2` `nightlights` section report good-quality pixel mean, vintage, sampling method and source; subpixel parcels use an identified representative-point pixel | ❌ | 🟡 Importer and query implemented; raster is not provisioned on this review host, so no live parcel value is verified |
| C5 | Census section indicators | Yes | ✅ section level | Census section with ratios; modelled values are flagged | ✅ indicators | ✅ |
| C6 | Age pyramid, gender split | Yes | ✅ Census 2021 `p2`/`p3` totals and `p14`–`p29`, `p30`–`p45`, `p67`–`p82` age/sex fields | Section-scoped male/female totals and a 16-band age pyramid when the complete field set is present; not parcel-level population | ❌ | ✅ |
| C7 | POIs | Yes | ✅ `/pois`; `poi` block empty | POI counts by category within 1 km | ✅ | ✅ |
| C8 | Modelled population | No | ✅ `population` block, labelled | Shown in the census section with the modelled-value note | ✅ | ✅ |

#### A.5.7. Community and platform

| # | Capability | Zornade | Ours | Disposition |
|---|---|---|---|---|
| X1 | XP, badges, leaderboard | Yes | No | **Not adopted.** SRS §2.2 and §14 |
| X2 | User contribution to correct data | Yes | No | **Deferred.** SRS §14 needs a moderation and validation path first |
| X3 | Public API with keys | Yes | Internal API | Tracked separately (gap analysis item 17). Not part of this plan |
| X4 | Guided tour | Yes | No | Defer; outside parcel-panel parity |

#### A.5.8. Cadastral parcel PDF report comparison

The 2026-10-09 Zornade review observed a PDF action in the parcel sheet but did
not download the output. This comparison uses Zornade's [published PDF and map
guide](https://zornade.com/blog/salvare-stampare-mappa-catastale-pdf/), its
[saved-parcel workspace guide](https://zornade.com/blog/schede-immobiliari-salvate-workspace-2026/)
and [app/API overview](https://app.zornade.com/api/), alongside the current
dossier implementation documented in [`PARCEL_DOSSIER.md`](PARCEL_DOSSIER.md).
Zornade distinguishes a personal-use parcel-profile PDF, a separate map image
and an official cadastral map extract. This comparison covers the parcel-profile
PDF. The January guide says the PDF has no legal value and names cadastral,
risk, OMI, solar and demographic content. The October workspace guide adds that
the PDF carries a generation date and sources and lists cadastral identity,
area, OMI comparison, a coefficient-based estimate, risks, territorial data,
addresses and constraints. These are publisher descriptions; no sample PDF or
field-by-field specification is available in the reviewed material.

| Aspect | Zornade parcel PDF | land-registry parcel dossier | Comparison |
|---|---|---|---|
| Purpose and status | Free parcel-profile export for personal use. The January guide says it has no legal value; the [product page](https://app.zornade.com/mappa-catastale/) offers PDF export with a free account. | A4 `Scheda tecnica particella`; explicitly not a cadastral extract or professional appraisal | Both are informational reports. Zornade's guide treats the official map extract as a separate product; land-registry also explicitly disclaims professional-appraisal status. |
| Cadastral identity | The October workspace guide names cadastral identity and area, but does not enumerate identifiers or their format. Foglio, particella, section, source identifier and null/missing behavior have not been checked in an exported sample. | Lists the national cadastral reference, foglio, particella, urban section, cadastral municipality code, area, perimeter, source release and INSPIRE local ID when available. | The stated subject overlaps, but field-level parity is unverified. Compare the same parcel's identifiers, null/missing fields and source release against an actual Zornade PDF before calling the cadastral data equivalent. |
| Parcel geometry and map | The guide describes the parcel-sheet PDF separately from a map image and official cadastral map extract; it does not establish whether the PDF itself contains a parcel outline or locator map. | Includes a locator diagram showing the parcel outline and neighbouring parcel outlines when geometry is available. The caption describes it as indicative, not a survey. | land-registry's embedded geometry is documented. Zornade's PDF map content and the geometry source/scale need inspection; the separately exported map image must not be treated as part of the PDF without evidence. |
| Report content | The January guide names cadastral, risk, OMI, solar and demographic information. The October workspace guide says the export mirrors the interface and specifically lists cadastral identity, area, OMI semester comparison, coefficient-based estimate, risks, territorial data, addresses and constraints. Exact fields and whether every interactive section appears are not shown. | Server-built sections for parcel identity/location and locator map; SISTER building records; risks (including parcel hazards, coastal change and protection records when their sources are available); census/population; municipal profile and population history; province-level ISTAT quality-of-life indicators; income; night lights; OMI quotes/history; and municipality-wide solar aggregates. Protection no-match states remain undetermined without full source coverage. Unavailable values are labelled rather than filled with zero. Solar and quality-of-life scope are stated and are not presented as parcel-level estimates. | The published scope overlaps substantially. The land-registry dossier currently documents additional municipality/province context and a separate source register; only matching exported PDFs can establish field-level parity and reveal differences in unavailable data. |
| PV/energy analysis | The January guide names solar in the parcel PDF. The [October workspace guide](https://zornade.com/blog/schede-immobiliari-salvate-workspace-2026/) describes per-building capacity, annual production, system cost, payback, hourly self-consumption and confidence for the saved-parcel profile, and says the PDF mirrors the interface; it does not explicitly enumerate these fields in the PDF. | The dossier always shows municipality-wide solar aggregates. After a successful Energy-tab calculation, the map download can POST the validated capacity, orientation and financial assumptions; the server recomputes PVGIS yield and economics and adds headline results, assumptions, annual cash flows and a chart. It labels the yield as a centroid location estimate, not a roof assessment. | land-registry now carries a user-input parcel scenario without deriving capacity from parcel area. Zornade's PDF fields and whether its per-building scenario appears still require an exported sample; its described hourly self-consumption is not matched by the annual-share model here. |
| Layout | The guide distinguishes the parcel-sheet PDF from a separate map image and official cadastral extract; page size, pagination, maps inside the report and section order are not documented. | A4, with cover and locator map, key figures, contents, summary, thematic sections, source register and optional saved-parcel appendix. A rendered sample is about 7–8 pages; page count varies with available data. | land-registry's report structure is documented and a sample is rendered; a visual, page-by-page comparison with Zornade remains open. |
| Sources and reproducibility | The October workspace guide says the PDF carries its generation date and sources, but does not specify data vintages, source identifiers, licence notes or whether the output preserves an immutable snapshot. | Report ID and available dataset/model versions; source register includes provider, attribution and reuse/licence status. Generated from server-side inputs at request time; no immutable historical snapshot is claimed. | Both descriptions mention sources and a date. land-registry documents version identifiers and licence metadata more explicitly; neither report is claimed to be a reproducible historical snapshot. |
| Locale and personal data | The cited guides do not specify PDF language or whether saved notes, photos and documents appear in the PDF. The workspace guide says these may exist on a saved parcel, so inclusion needs checking. | Italian and English labels follow the request locale. A signed-in user's saved-parcel status, priority, notes, tags and favourite appear in an optional personal appendix; anonymous reports omit it. | land-registry documents locale and personal appendix behavior. Zornade's user-specific PDF content remains unverified and should be checked before comparing privacy or sharing behavior. |
| Known gaps | Publisher descriptions now provide a category-level content list, but exact report fields, map inclusion, pagination, locale, personal-data inclusion and missing-data treatment need direct inspection. | The PDF contains the latest annual ISTAT BES edition at province/NUTS3 scale and an ISTAT municipality profile with available population history. Some provider reuse terms and target-host data coverage also remain under review. | The documented scope overlaps substantially. Visual parity and field-level parity remain open until representative PDFs for the same parcel are inspected side by side. |

**Acceptance check:** using a free Zornade account, obtain a representative
parcel-profile PDF and generate a land-registry dossier for the same parcel.
Compare section and field coverage, map/geometry, page flow, generation date,
source and licence notes, unavailable states, locale, saved-parcel content and
legal disclaimer. Update this comparison from the actual artifacts before
changing S9 from 🟡.

### A.6. Where land-registry is ahead, and should stay ahead

- Parcel blocks use a common envelope with fields for source, versions, spatial
  resolution, confidence, benchmarks, match method and coverage. Populate only
  what the source supports and state the resolution of every aggregated value.
  Zornade shows a source line and a reliability label.
- Distinguish "not covered", "not determined", "no matching records" and
  "temporarily unavailable" where the source contract supports those states;
  never render missing data as zero (MAP-FR-032, MAP-FR-052). The parcel
  envelope carries typed `full`, `partial` or `unavailable` coverage, while the
  panel separates no-match responses from retryable failures. Geographic
  non-coverage and undetermined values still require explicit source metadata.
- Cadastral building records from SISTER, OpenData records and PVP auctions,
  plus a purchase workflow, which Zornade does not have.
- Parcel adjacency analysis, uploaded-data analysis, a lifecycle shortlist,
  rate limiting, tile cache and privacy-preserving metrics.
- Valuation estimator already returns a versioned, auditable calculation
  (`omi-area-range-v1`). Zornade's equivalent rests on a stated energy-class
  premium cap; ours should not adopt a coefficient without its own evidence.

### A.7. Design recommendations

#### A.7.1. Panel architecture

1. **Section registry.** *(Implemented in `static/parcel-panel.js`.)* A single
   list where each entry declares `id`, `tab`, `title`, `icon`, whether it loads
   eagerly, a `load(ctx)` that fetches its own endpoint, a DOM-free
   `render(data, ctx)` returning HTML plus provenance metadata, and an optional
   `bind` for interactive parts. Add dependency flags only where needed; map
   zoom should not gate content after a parcel has already been selected. The
   registry keeps section order, loading and error states consistent.
2. **One canonical parcel panel.** *(Implemented.)* `/map` is the canonical
   surface for new parcel-panel work. Keep `/map-legacy` for upload and analysis
   workflows and its current report hand-off; do not require a shared renderer.
   Port tested data transformations and endpoint behavior from
   `parcel-enrichment.js`; extract pure helpers only when the primary panel and
   report both need them. Full parity remains a release gate.
3. **Layout.** *(Implemented.)* The desktop sheet uses
   `clamp(380px, 36vw, 540px)`; it becomes a bottom sheet at tablet and mobile
   breakpoints. The initial 480–560 px target was narrowed so the selected
   parcel remains visible on the map. Keep the header and tab bar fixed while
   section content scrolls.
4. **Navigation semantics.** Tab buttons scroll to the first section in each
   tab and update/restore tab fragments. *(Implemented.)* Individual cards
   restore from `#sezione-{id}` links; scrolling updates the fragment, and
   `aria-current` follows the active card through an IntersectionObserver. The
   tab bar is navigation, not a set of separate panes.
5. **Loading.** Render section skeletons immediately. *(Implemented.)* Identity
   renders at once from the tile feature. The parcel details endpoint is slow on
   a cold build, so no section waits for it; it feeds provenance and the
   coverage section. Other data loads from each section's own endpoint when the
   section nears the viewport. The SRS treats selective inclusion as met through
   separate endpoints; add `?include=` to parcel details only if Phase 0 payload
   and latency measurements show it is needed.
6. **Charts.** The repository has no charting library on `/map`. Use small
   hand-written SVG components (band bar, ring, history line with min/max band,
   stacked bars, population pyramid). They are short, theme with CSS variables
   in light and dark mode, and avoid a dependency. Reassess only if more than
   six chart types accumulate. The OMI history band is the only SVG chart so
   far; income distribution and the census pyramid use simple proportional
   bars.
7. **Provenance footer on every card.** One component renders source, dataset
   version, model version, spatial resolution and a modelled/estimated badge
   from the envelope. This replaces ad-hoc chips and covers MAP-FR-052 to 054
   uniformly.
8. **Internationalisation and themes.** All strings through `tr()` and the
   `.po` catalogue (Italian and English). Add new tokens to the theme overrides
   so dark mode stays consistent.

#### A.7.2. Backend

- Keep the comune-level risk values sourced from `/risks` and request MPS04 PGA
  separately at the parcel centroid through `/mps04/pga`; label its nearest
  native-grid match and 10%-in-50-years probability. Keep the eager SISTER
  address card distinct from the lazy ANNCSU point-in-parcel card. ANNCSU's
  regional importer/query now preserves source vintage, coordinate method and
  per-comune coordinate coverage; those upstream changes are installed and no regional store is provisioned on this host.
- New read-model blocks follow one recipe: upstream `aecs4u_stats` subpackage,
  query function, bounded `stats_service` adapter, and an envelope entry in
  `_build_parcel_enrichment`, followed by a panel registry entry. Geometry-heavy
  or independently loaded sections use dedicated routes and panel entries
  instead of extending the cold read-model build; keep those queries bounded
  and return explicit coverage and unavailable states.
- Compute derived metrics server-side where the inputs are server data, so the
  formula is versioned (`model_version`) and testable; compute only
  interactive previews in the browser, as the OMI estimator already does.
- Keep the `serving.parcel_enrichment_read_model` PostgreSQL cache table. Its
  schema is provisioned by the explicit
  [`parcel-enrichment-read-model.sql`](../scripts/sql/parcel-enrichment-read-model.sql)
  migration; request handlers never create or alter database objects. The
  intended warm path reads one indexed JSONB row; a cache miss rebuilds the
  payload from source stores, and `refresh=true` forces that rebuild. The
  migration is now applied on shared `aecs4u-stats`; table shape and the
  `postgres` role's schema/table privileges were checked, and the local status
  endpoint now reports whether the relation exists, whether the current role
  has the schema `USAGE` and table `SELECT`/`INSERT`/`UPDATE` permissions, and
  whether that role is a superuser. The configured `postgres` role is a
  superuser, so it does not prove least-privilege runtime access. The user
  reports Cloud Run uses that username, but its Secret Manager value could not
  be independently checked. A forced rebuild/repeat sample measured PostgreSQL
  repeat-hit latency at 15 ms median / 55 ms p95; its 20 parcels contained no
  duplicate-reference group. When the payload shape changes, bump the
  read-model fingerprint/version and define
  how existing rows refresh; a database schema migration alone will not
  backfill JSON. The
  primary map requests a compact `view=panel` projection that removes duplicate
  top-level data and limits the OMI preview. After a PostgreSQL cache miss, the
  compact view checks its SQLite fallback; after a cold build, it stores the
  projection there if PostgreSQL persistence does not succeed. SQLite entries
  use a parcel cache key, validate against the source fingerprint, and are
  capped at 500 panel entries. When a map feature ID is supplied, both cache
  keys also include that ID; cache hits are rejected if their payload identity
  does not match. Reference-only callers retain a reference-scoped key. A
  separate dev-host sample measured this
  application-side fallback; it does not establish PostgreSQL cache performance.
  `/api/v1/enrichment/status` now reports the table separately from general
  PostgreSQL connectivity as `parcel_enrichment_read_model`.

#### A.7.3. Derived metrics: definitions to document before building

| Metric | Inputs | Notes |
|---|---|---|
| Gross rental yield (`omi-gross-rental-yield-v1`) | Selected OMI rent and sale bands, same type and state | Rent midpoint × 12 ÷ sale midpoint; gross of costs and taxes |
| Trend deltas (`omi-semester-trend-v1`) | OMI history for the zone and selected type/state | Compare latest sale midpoint with the closest available semester at or before 1, 2, 6, 10 and 20 semesters back; return actual periods and omit unavailable horizons |
| Zone percentile (`omi-zone-percentile-midrank-v1`) | Latest OMI quotes for all zones in the comune, same type and state | One median sale midpoint per zone; midrank percentile and comparable-zone count. Suppress the percentile below five comparable zones |
| Price-to-income (`omi-mef-price-income-v1`) | Selected area's OMI sale midpoint value ÷ comune mean taxable income per taxpayer | Uses the user-entered/selected surface as area. This is years of one mean taxpayer's annual taxable income, not household affordability |
| Gini estimate (`mef-grouped-gini-v1`) | MEF frequencies across all eight income brackets | Grouped Lorenz trapezoid using bracket midpoints; assumes €0 for the ≤0 bracket and €150,000 for the >€120k bracket; within-bracket inequality is not observed |
| Composite risk score (`municipal_hazard_index_v1`) | DPC seismic zone and IdroGEO municipal landslide P3+P4 and flood P3 area shares | Equal-weight arithmetic mean of available 0–100 components; partial/invalid inputs are reported, never replaced with zero. Municipal context only; it is not a parcel or subsidence score. Shared package release remains tracked in issue 02 / `aecs4u-stats#34` |

### A.8. Development plan

Effort estimates are for one developer and include tests and documentation;
they exclude review, deployment and upstream data-pipeline work. Treat the
figures as planning ranges until the Phase 0 measurements and upstream contracts
are checked. Phase 0 and 1 establish the baseline and primary panel; later work
can be reordered where dependencies allow.

#### Source inventory to include in the plan

The availability values below preserve the requested starting inventory. They
describe data availability for this parcel-panel plan; they do not by themselves
mean that a provider adapter or panel section is absent. Where an adapter or
section already exists, the action is to validate source coverage and provenance
on a provisioned host. Resolve any source marked “to be selected” before adding
data, including its coverage, version and reuse terms.

| Panel data | Starting availability | Source / store | Planned action |
|---|---|---|---|
| Main address | Not available | `aecs4u-stats` PostgreSQL `sister.v_sister_property_by_cadastral_parcel.address` | Query the PostgreSQL SISTER view with exact municipality, province, sheet, parcel and section matching; label coverage as partial and non-geocoded. |
| Risks | Not available | DPC municipal seismic zone; INGV MPS04; ISPRA IdroGEO PAI/PGRA | Keep source scales separate. The shared equal-weight composite formula is prepared locally under `aecs4u-stats#34`; release it and verify complete/partial/unavailable behavior on a provisioned host. |
| Subsidence | Not available | Copernicus EGMS 100 m grid via `aecs4u_stats.egms` | Verify the provisioned store and parcel-intersection coverage; keep the overlap-weighted summary and partial-coverage state. |
| Terrain | Not available on this host | INGV TINITALY 1.1, 10 m; importer/query merged to `aecs4u-stats` main for `aecs4u-stats#28` | Import each served region and verify point/polygon results, NoData, coverage and fallback method; app API, panel, source catalog and Typst dossier are already wired. |
| Buildings | Not available | `aecs4u-stats` PostgreSQL `sister.v_sister_property_by_cadastral_parcel` (`category`, `cadastral_class`, consistency and income) | Query the PostgreSQL SISTER view with exact cadastral matching. Keep these records distinct from physical footprints, which remain gated on `aecs4u-stats#29`. |
| Land cover | Not available | CORINE 2018 official EEA vector service; upstream regional DuckDB store/query merged to `aecs4u-stats` main for `aecs4u-stats#32` | Import each served region, then verify parcel class, area-weighted distribution, coverage, release and no-match behavior on provisioned data. The local app endpoint, panel and dossier already preserve these fields and disclose CLC's coarse resolution. |
| OMI history | Partial pending serving-host review | Agenzia delle Entrate OMI via `aecs4u_stats.omi`; 100 province GeoJSON files (50,466 polygons) from the 2018/2 mirror are provisioned under `/data/istat/omi_zones` on the review host | Mount the mirror on each serving host, verify representative parcel-centroid matches and retain the 2018/2 boundary vintage as approximate geometry; validate quote-store semester history and provenance separately. |
| Coastal erosion | Provisioned in the configured stats database; target-host review remains | ISPRA `Linea di Costa 2020 v2.0`, `Dinamica_Litoranea_2006_2020`; CC BY 4.0 in RNDT metadata; 99,229 features across 664 municipalities | Apply `scripts/sql/ispra-coastal-dynamics.sql` and refresh with `scripts/import_ispra_coastal_dynamics.py --apply` on each target stats database. The configured database returned 35 and 67 segments for two real Civitavecchia parcels; a dense-area query verified explicit partial coverage at the 100-candidate cap. The panel and Typst dossier report the 2006–2020 class, 5 m classification threshold, 5 m source accuracy, source vintage and unknown coverage when no segment matches. |
| Cultural heritage | Not available | Planned protection-area source/query under `aecs4u-stats#48`; source coverage and reuse terms remain unverified | The app's required-ID endpoint, panel, map layers, dossier and v2 risk component are wired. Complete the upstream inventory and reuse review, provision the tables and coverage polygons, then verify representative parcels. |
| Points of interest | Not available | OpenStreetMap via `aecs4u_stats.osm` | Validate the configured POI store and parcel-centered results; surface source and search radius with the existing POI section. |
| Night lights | Partial; annual raster not provisioned on this host | NASA Black Marble VNP46A4.002; app-side importer applies the provider's 0.1 radiance scale factor and retains only quality-flag-0 radiance while building an Italy mosaic from four LAADS tiles | Set `LAADS_DAAC_TOKEN` for authorized downloads and run `scripts/import_viirs_nightlights.py --year <year> --apply`, or use `--build-only` with the four local HDF5 tiles. The parcel block reports annual radiance, vintage, quality-filtered pixel count and whether it used a parcel-pixel mean or representative-point pixel. It never treats missing or out-of-coverage samples as zero. The package contract should move into `aecs4u_stats` for reuse. |
| Cadastral OpenData | Not available | OpenData PostgreSQL `cadastral_queries` | Verify the external database connection and representative cadastral matches; retain query time and match method in provenance. |
| PVP auctions | Not available | PVP modelview PostgreSQL | Verify the external database connection and parcel matching; retain auction records and source provenance. |

#### Phase 0 — Alignment and baseline — **local PostgreSQL cold/repeat paths measured; duplicate-reference and production-role acceptance remain open** (about 0.5 week)

- Decisions recorded (§A.9). The app-side protection contract, required-ID
  route, lazy panel section and schema-gated map layers are implemented and
  documented in [`PROTECTION_CONSTRAINTS_CONTRACT.md`](PROTECTION_CONSTRAINTS_CONTRACT.md).
  Upstream tables, imports and complete coverage remain pending in Phase 3;
  the July gap analysis points here.
- Baseline captured: 20 unique parcels in four areas (two large cities, a small
  comune, open countryside) with latency, payload size and block coverage,
  stored in `docs/baselines/` with a before-state screenshot. The original
  pre-migration sample had zero PostgreSQL cache hits across 40 calls. A
  post-migration response sample recorded 40 calls against
  `aecs4u-stats PostgreSQL via asyncpg` (median 9 ms, p95 15 ms), but its old
  `cached` flag did not distinguish hits from successful writes. A new
  refresh-first sample measured 20 forced rebuilds (median 5.168 s, p95 10.580
  s) and 20 repeat hits (median 15 ms, p95 55 ms). It preserved all 20
  requested feature IDs but found no duplicate-reference group. The earlier
  post-implementation panel sample measured the local SQLite fallback. Methods,
  raw observations and caveats are in the baseline document.
- `tests/test_parcel_block_inventory.py` pins which blocks the builder fills.
  Extend its fixtures when a block is added.
- **Still not captured:** production-runtime acceptance and least-privilege
  readiness. The controlled refresh-first sample verifies local cold builds
  and repeat cache hits, but not the deployed Cloud Run identity. The migration at
  [`scripts/sql/parcel-enrichment-read-model.sql`](../scripts/sql/parcel-enrichment-read-model.sql)
  was applied to `aecs4u-stats` on 2026-10-10. Catalog checks confirmed the
  expected columns and primary key; the migration granted `SELECT`, `INSERT`
  and `UPDATE` to the local `postgres` role, which is a superuser. The local
  `/api/v1/enrichment/status` endpoint reports
  `parcel_enrichment_read_model.available: true` with `reason: null`. The
  status contract now checks the role's required schema/table privileges and
  reports its superuser flag. The operator reports that Cloud Run also uses
  `postgres`; the configured role is a superuser, so its access is not
  least-privileged even though the migration grants only the cache table.
  Production acceptance remains open until the
  likely Neon target and a dedicated non-superuser runtime role are configured
  and verified. A 2026-10-10 recheck after the canonical-first point-lookup
  change returned an ID for all 20 unique parcels across the same four areas,
  with distinct IDs and references; see the [feature-ID recheck](baselines/parcel_panel_feature_id_recheck_2026-10-10.json).
  The controlled refresh-first sample also matched the requested feature ID on
  all 40 detail responses. Neither sample contained a duplicate reference, and
  neither verifies feature-ID cache isolation on the production runtime.

#### Phase 1 — Primary panel shell and parity with legacy — **mostly done** (about 2 to 3 weeks)

| Task | Files | Status |
|---|---|---|
| Section registry and typed renderers for `/map` | `static/parcel-panel.js`, `static/parcel-panel-core.js`, `map-v2.js` | Done. Analysis hand-off remains on `/map-legacy`; the report action downloads the server-built Typst dossier |
| Side sheet, sticky header actions, stat strip, tab bar | both `map_v2.html` templates, `map-v2.css` | Done. Copy link, PDF and Close stay in the fixed header; visibility after section scrolling is browser-checked |
| OMI card: selectors, estimate, history chart and quote table | panel sections, `/omi/*` | Done (zone, type and state selector, local preview, server estimate, chart, and all current municipality quote rows). The estimator selector is capped at 80 valid sale quotes; the full table also includes rent-only rows |
| Income, census, POIs, risks, bulletin, PVP, fires, safety, indicators | panel sections | Done |
| Provenance footer and modelled-value note | `blockMetadataHtml` in the core module | Done where the source supplies metadata |
| Skeleton, error and unavailable states | panel code | Done. A 503 shows a retry; a 404 shows an unavailable message without one |
| Tab bar with scroll-spy | panel code | Done. Explicit tab selection remains active through smooth navigation while lazy sections load and change the scroll position |
| Tab fragment navigation (`#valore`) | panel code | Done. Tab fragments restore on panel open and respond to back/forward navigation |
| Direct link to an individual section card | panel code | Done. `#sezione-{id}` restores and loads the selected card; the URL tracks the active card while scrolling |
| Collapsible section cards (MAP-FR-031) | `static/parcel-panel.js`, `map-v2.css` | Done. Native disclosures open by default; browser smoke test covers close/reopen |
| Source/resolution-aware risk and address | `stats_service.py`, `routers/enrichment.py`, panel | Partial. Municipality risk and parcel-centroid PGA use separate sources; MPS04 is not provisioned on this host. SISTER addresses are included in the read model and loaded through a dedicated section endpoint, now shown directly below cadastral identity in the initial Value tab; live address coverage remains cache-dependent and unverified |
| i18n (it, en) | `translations/*` | Italian catalog covers the primary map templates and every literal `tr()` string in the direct-map and parcel-panel scripts; `tests/test_i18n_catalog.py` guards that coverage. The catalog loads from `.po`; English falls back to source strings. Targeted copy edits are recorded in §A.11; independent native proofread remains |
| Backend support | `stats_service.py`, `routers/enrichment.py`, `models.py` | Valuation availability now reflects content; `/api/v1/enrichment/status` runs in a thread and is cached for 60 s, and reports MPS04, ISPRA mosaic and EGMS store readiness with typed unsupported, not-built and probe-failure reasons |
| Tests | `tests/js/`, `tests/test_parcel_panel_js.py`, `tests/test_i18n_catalog.py`, `tests/browser/test_parcel_panel_smoke.py`, `tests/test_parcel_report.py`, updated `test_map_v2_contract.py` | Browser smoke coverage includes independent lazy loading, 503 retry isolation, OMI estimation, tab scroll-spy and mobile layout; other browser cases cover address, uncovered-province, EGMS, census, energy, PDF and panel interactions. The catalog contract covers every literal translation call in the primary map scripts. See §A.11 for recorded runs and mock-data limits. |

**Remaining before Phase 1 closes:** proofread the Italian wording and check
the panel against data from a fully provisioned host. The 2026-10-15
refresh-first sample measured a local cold-build p95 of 6.188 s against the
preliminary 8.140 s target, using the same 20 parcels as the 2026-10-10 sample;
the newer response had one additional available block, so a strict same-shape
comparison and production-host measurement remain open. SISTER-backed address
records are capped at ten unique strings and matched by cadastral municipality,
sheet and parcel; coverage depends on a cached SISTER property row. Municipality,
census, income, risk and address coverage still need representative
production-data review.

ANNCSU's regional importer/query and the app's parcel adapter are merged and installed, but this host has no regional indirizzario store. The lazy ANNCSU card, API v1/v2 section and
Typst dossier section therefore return an explicit unavailable state here.
The separate SISTER `visura_properties.address` card remains cache-dependent
and non-geocoded. Provision representative regional data and verify spatial
matches and coordinate-coverage reporting before closing `aecs4u-stats#33`.

**Done when:** the primary panel covers the existing-data sections available on
`/map-legacy`, sections are navigable and collapsible, no section failure blanks
the panel, and frontend contracts pass or have an explicit reason for change.
Current state: section coverage, navigation, collapsibility and failure
isolation work in fixture/browser checks; provisioned-host acceptance remains
open, and full legacy parity is not yet claimed.

#### Phase 2 — Derived metrics from data already exposed (about 1 to 2 weeks)

**Implementation and fixed-fixture tests done:** the backend and panel calculate
and display all five metrics in §A.7.3 with model-version fields. At the
verification checkpoint recorded in §A.11, a focused run passed 69 Python tests
across async PostgreSQL adapters, parcel fallback and block assembly, reports,
derived metrics, map contracts and panel contracts; the Node panel suite
passed. The full browser suite passed all 11 cases, including parcel-wide EGMS
rendering and PDF export. A stale map
contract that expected a nonexistent
`municipality-profiles` layer was corrected to assert the catalog's actual
`admin-substitute` role. OMI boundary GeoJSONs are provisioned on the review
host; the 2018/2 mirror is approximate and still needs target-host and parcel
coverage review. Provisioned MEF/OMI quote-data review and Italian-copy review
remain.

- Server-side computation of yield, trend deltas, zone percentile, price-to-income
  and Gini, each with a `model_version` and documented inputs (§A.7.3).
- Panel components already show trend rows, yield, percentile and comparable-
  zone count, income distribution bars, grouped Gini and price-to-income ratio.
  A visual min/central/max band and gauge styling remain optional presentation
  refinements; numeric ranges and caveats are already shown.
- Census 2021 exposes male/female totals and complete five-year age bands
  (`p2`/`p3`, `p14`–`p29`, `p30`–`p45`, `p67`–`p82`) in the installed schema.
  The primary census section now renders the sex split and a 16-band age
  pyramid only when all required values are present; labels state that they
  describe the census section containing the parcel.
- Add valuation inputs only when the server model uses them. An input that does
  not affect the result would mislead users.
- Fixed OMI/MEF fixtures cover known arithmetic and the estimate route; existing
  frontend contracts cover the estimator and report surfaces. Benchmark values
  remain labelled with year and dataset version.

**Done when:** each metric has a stated formula in the panel or its tooltip, a
test with known numbers, and a graceful "not available" path.

#### Phase 3 — Per-parcel geospatial blocks (estimate after upstream readiness)

Each block is a vertical slice: upstream query/data contract, `stats_service`
adapter, envelope entry, panel section and tests. The upstream package and data
builds are outside this repository and are not included in the effort estimates
above; estimate those separately. Hazard polygons, SISTER addresses and EGMS
intersections and the protection-constraints consumer contract are implemented;
their provisioned-data checks remain. The table retains those slices for
acceptance traceability, then lists remaining work in proposed priority order.
Source-dependent results stay gated until their upstream query is released and
representative coverage is available.

**Upstream gate check (2026-10-15):** authenticated GitHub CLI rechecked [`#28 DEM`](https://github.com/aecs4u/aecs4u-stats/issues/28), [`#29 building footprints`](https://github.com/aecs4u/aecs4u-stats/issues/29), [`#31 solar economics`](https://github.com/aecs4u/aecs4u-stats/issues/31), [`#32 CORINE`](https://github.com/aecs4u/aecs4u-stats/issues/32), [`#33 civic addresses`](https://github.com/aecs4u/aecs4u-stats/issues/33), [`#34 composite risk`](https://github.com/aecs4u/aecs4u-stats/issues/34), and [`#48 protection constraints`](https://github.com/aecs4u/aecs4u-stats/issues/48); all seven remain open. Issues #28–#34 were last updated 2026-07-13; #48 was last updated 2026-09-27. The local `aecs4u-stats` checkout contains the PVGIS economics calculation, CORINE import/query, TINITALY importer/query, composite-risk helper, building-footprint importer/query and ANNCSU regional address importer/query; these changes had not yet been committed when this check was made (see the 2026-10-16 recheck below). The app consumers for terrain, CORINE, composite risk, building footprints and civic addresses are wired, but no regional stores are provisioned. The ANNCSU query contract is present locally but has not been validated against provisioned civic-address data. The app-side protection contract, endpoint, panel and schema-gated layers are implemented. The latest #48 comment describes an uncommitted SITAP acquisition path covering a selected decree-polygon layer, not a complete Articles 136/157/142 inventory; Vincoli in Rete is discovery-only pending a stable bulk interface and confirmed reuse terms, and Catalogo Generale is not treated as binding data. The upstream issue still lacks the published schema, import, spatial queries and complete source coverage. Do not treat the acquisition work as an available upstream contract or infer that an uncovered parcel has no constraint.

**Upstream recheck (2026-10-16):** the importers described above are committed
in `aecs4u-stats` (`1109d6c`, "Add spatial data enrichment importers", included
in `main` at `a1ef4cb`) and installed in this environment. GitHub issues #28,
#29, #31, #32, #33, #34 and #48 are all still open, so none of them is closed by
the merge. With the package present, terrain, CORINE, footprints and ANNCSU
addresses now return `not_built` (no regional store) instead of
`not_supported`, and the composite score is computed by the shared helper.
Provisioning regional stores downloads large public datasets (TINITALY WCS,
CORINE, Geofabrik PBF, ANNCSU extracts) and has not been done.

**Coastal-erosion source selection (2026-10-10):** ISPRA's national `Linea di
Costa 2020 v2.0` dataset exposes the `Dinamica_Litoranea_2006_2020` line layer.
The ISPRA indicator defines erosion as shoreline retreat over 5 m between
2006 and 2020, seaward advance over 5 m, and smaller changes as stable. RNDT
metadata states CC BY 4.0 and 5 m positional accuracy; its lineage says the
2020 characterization used mostly Google Maps imagery from 2017–2020. This is
a suitable source for a historical coastal-change section, not a current erosion
forecast. The query contract and local/cache-backed ingestion are still absent
from `aecs4u-stats`; see [issue draft 14](github_issues/zornade_map_enrichment/14-coastal-erosion-ispra-source.md).

**Night-light source selection (2026-10-15):** NASA Black Marble VNP46A4.002
provides an annual 15 arc-second radiance grid. The app-side importer uses the
four LAADS tiles covering Italy and masks all pixels except quality flag 0;
parcel queries use a mean of pixel centres or identify a representative-point
fallback for subpixel parcels. The value is a coarse radiance proxy, not an
economic or building-light classification. The raster must be provisioned on
serving hosts, and the reusable importer/query contract should move into
`aecs4u_stats`; see [issue draft 15](github_issues/zornade_map_enrichment/15-viirs-nightlights-black-marble.md).

**Building-footprint source selection (2026-10-15):** use OSM polygons from
Geofabrik's five Italian macro-region PBF extracts, refreshed daily, then filter
against the exact ISTAT region boundary. This supports regional local stores and
keeps parcel requests on indexed spatial lookups instead of the shared Overpass
service. The Microsoft Global Building Footprints repository describes a
machine-detected global dataset and a CDLA Permissive 2.0 licence; its Italian
completeness still needs local assessment before it is used as parcel coverage.
OSM uses ODbL 1.0, requires attribution and carries share-alike terms for
database reuse. The consumer describes reported footprints as community-mapped
and leaves source coverage unknown; zero mapped polygons must not be read as
proof that no building exists. See [Geofabrik Italy downloads](https://download.geofabrik.de/europe/italy.html),
[Microsoft Global Building Footprints](https://github.com/microsoft/GlobalMLBuildingFootprints),
[OSM attribution and ODbL terms](https://www.openstreetmap.org/copyright), and
[issue draft 04](github_issues/zornade_map_enrichment/04-building-footprints-coverage.md).

**CORINE land-cover source selection (2026-10-15):** the official EEA ArcGIS
feature service exposes the CLC 2018 vector layer and supports regional
geometry queries. The upstream importer builds one EPSG:3035 DuckDB
store per ISTAT region; polygon queries choose the majority class by
intersection area and report mapped coverage. Product release
`CLC2018_V2020_20u1` has a 25 ha minimum mapping unit and 100 m minimum width,
so parcel results are coarse context. The app-side endpoint, panel card,
source catalog and dossier section are wired, and the upstream package is merged to `aecs4u-stats` main and installed. No regional store is provisioned. Next, import regional data for the changes tracked by
[`aecs4u-stats#32`](https://github.com/aecs4u/aecs4u-stats/issues/32), provision
the regions served, and verify representative parcel coverage before marking
the feature complete.

| Order | Block | Upstream state | Work |
|---|---|---|---|
| 1 | Protection constraints (landscape, heritage) | App route, lazy panel and schema-gated map layers wired to proposed contract; source tables depend on `aecs4u-stats#48` | Publish upstream schema/import/query, provision source and coverage tables, then verify required-ID lookups, overlap ratios, catalog gating and unknown/covered states on representative parcels |
| 2 | Per-parcel flood and landslide | ISPRA PAI/PGRA polygons available through `hazards.ispra_mosaics` | App-side polygon intersections now report affected area and highest class; verify dataset coverage and candidate-cap behavior on provisioned data |
| 3 | Addresses | SISTER `visura_properties.address` links to cadastral municipality/sheet/parcel; `aecs4u-stats` main now has the regional indirizzario/stradario importer and point-in-polygon query (installed here), without a provisioned regional store | App endpoint, lazy panel card, API v2 section and dossier section are wired. Provision a regional store and verify known parcel matches, source vintage, coordinate method and municipal coordinate coverage before treating the result as field-verified; a missing point match remains distinct from missing address data |
| 4 | Subsidence per parcel | `egms` provides a classified 100 m grid-cell bbox query; layer already served | Implemented: exact parcel intersections, area-weighted velocity and acceleration, movement-class areas, covered-area percentage and a 5,000-candidate partial state |
| 5 | Terrain | Upstream importer/query merged to `aecs4u-stats` main; `aecs4u-stats#28` remains open and no regional store is provisioned | Import served regions, then verify parcel and point values, missing-data behavior and provenance; API, panel and dossier integration are wired locally |
| 6 | Land cover | App endpoint/panel/dossier are wired locally; upstream `aecs4u-stats#32` store/import query is merged to `aecs4u-stats` main; no regional data is provisioned | Import regional CLC stores, then verify class distribution and coverage on representative parcels |
| 7 | Building footprints (OSM) | No parcel query; `aecs4u-stats#29` open | Coverage and count; keep SISTER as the cadastral source and label the two clearly |
| 8 | Composite risk score | Shared `municipal_hazard_index_v1` helper is preferred; an aligned app compatibility implementation serves older wheels. `aecs4u-stats#34` is still open; the shared helper is installed and matched the compatibility copy on seven representative cases (2026-10-16) | Check complete and partial real-comune responses on the serving host, then retain the shared score/version/components through each consumer |
| 9 | Night lights | NASA Black Marble VNP46A4.002 | `scripts/import_viirs_nightlights.py` downloads the four Italy tiles with an authorized LAADS token or builds from local HDF5; read-model, panel, API v2 section and source catalog are implemented. Provision the raster on serving hosts and verify representative urban/rural coverage. |
| 10 | Coastal erosion | ISPRA 2006–2020 snapshot provisioned in the configured stats database: 99,229 features across 664 municipalities; real-parcel matches and candidate-cap partial state verified | Apply the schema and refresh on each target stats database; retain the no-match/partial semantics and verify serving-host coverage |

**Done when**, per block: the envelope states source, version, resolution and
match method; empty and uncovered states are distinct; a test covers a parcel
with data, a parcel without, and a store that is down.

##### Local store provisioning and verification

Run the importers with the same Python environment and `ISTAT_DATA_DIR` used by
the application. The default output directory is `/data/istat`.

MPS04 raw WFS-derived GeoJSON must first be landed by the property-scraper
`ingv_mps04` job; the stats importer reads those files and performs no network
download. Build the native 0.05° grid with:

```sh
python -m aecs4u_stats.hazards.scripts.import_mps04 --grid-variant native
```

Build both ISPRA mosaics manually with:

```sh
python -m aecs4u_stats.hazards.scripts.import_ispra_mosaics --all
```

The ISPRA inputs are large (about 1.1 GB for landslide and 720 MB for flood,
before extraction and database-build space); these downloads do not belong in
routine application startup. Both importers accept `--db-path` overrides; keep
the importer output and the application's resolved store path aligned.

OMI zone polygons are a separate local mirror. On 2026-10-15, the 100 cached
province KMZs were converted to 100 GeoJSON files under
`/data/istat/omi_zones` on the review host. The files contain 50,466 polygons,
all tagged 2018/2; the application availability check is true and a point in
central Rome matched zone B29. On another host, build from the cached KMZs with:

```sh
python -m aecs4u_stats.omi.scripts.download_zone_boundaries --all --zones-dir /data/istat/omi_zones
```

The source mirror's 2018/2 polygons may not reflect later OMI boundary changes;
use them as approximate matching geometry, not authoritative current-vintage
boundaries. A file-presence check proves store readiness, not correct coverage
for every parcel; validate representative point matches on the serving host.

EGMS is landed directly by the property-scraper `zornade_rischio_subsidenza`
job as an indexed DuckDB table, with no separate stats import. If the scraper
and application do not use the same default store, configure `EGMS_DB_PATH` for
both processes.

After provisioning, fetch `/api/v1/enrichment/status` after its 60-second cache
expires and require `hazards_mps04`, `hazards_ispra_mosaics`, and
`egms_subsidence` to report `available: true` and `reason: null`. Then verify
representative parcel results: a built store proves host readiness, not local
coverage or a successful spatial match.

The ISPRA coastal snapshot is provisioned in the configured stats database as
of 2026-10-15. Its status reports `available: true`, 99,229 features and source
version `ISPRA_LINEA_COSTA_2020_V2_DYNAMICA_2006_2020`; two real Civitavecchia
parcels returned mapped segments, and a dense-area query verified the
100-candidate `partial` response. Repeat schema provisioning, import and these
coverage checks on each target stats database.

#### Phase 4 — Energy (about 2 to 3 weeks after upstream readiness)

**Municipal aggregate slice implemented:** the Energy tab reads the existing
`solar` block from the parcel read model. It shows available municipality-wide
building, capacity, annual-output and viability figures with the source's data
version and update date when exposed. The section states that these aggregates
are not estimates for the selected parcel, building, or roof.

**Editable cash-flow scenario implemented:** the Energy tab exposes capacity,
tilt, aspect, system losses and financial assumptions, then posts the scenario
to `/api/v1/enrichment/solar-economics/{reference}`. PVGIS 5.3/SARAH3 supplies
the long-term yield at the parcel centroid for a 1 kWp reference system; the
user's capacity scales it. The panel shows NPV, LCOE, simple and discounted
payback, first-year production and annual cash flows. Defaults are illustrative.
The shared stats calculator is used when present (it is installed here); an app compatibility calculation covers older wheels. Upstream #31 remains open. JRC describes PVGIS 6 as beta and under development, so this
implementation retains the documented PVGIS 5.3 API; revisit that choice when
PVGIS 6 stabilizes.

**Remaining:**

- The app compatibility calculation now matches the 5 kWp reference case in
  the sibling `aecs4u-stats` source checkout exactly, including all annual cash
  flows. The shared helper is now installed (`aecs4u-stats` main, `1109d6c`); on 2026-10-16 the shared and compatibility results were identical for this case (nine numeric fields, 26 annual cash-flow rows). The live PVGIS response parser was also checked on 2026-10-16 (Rome
  sample, 1,425.2 kWh/kWp/year and twelve monthly rows). Shared-venv tests now
  verify distinct timeout/unavailable responses and compile a scenario dossier
  containing the cash-flow chart, table and provenance. Provisioned-host
  behavior and PDF rendering remain open (MAP-FR-055, issue 06).
- Per-roof orientation and tilt are not available from OSM (verified by the
  `pv_potential.py` design note). The current scenario takes capacity, tilt and
  aspect from the user; do not infer them from parcel geometry or display
  per-building viability without a documented roof model.
- The scenario uses a user-supplied annual self-consumption share. Hourly
  household self-consumption modelling remains out of scope until an hourly
  demand model exists.

#### Phase 5 — Report and export (about 1 to 2 weeks)

> **Superseded 2026-10-10; POST scenario support added 2026-10-16.** The panel-snapshot ReportLab export described below is deprecated. The direct map downloads the Typst dossier from `/api/v1/enrichment/parcel/dossier/{reference}`, built from server data. GET produces the baseline report; POST may carry validated assumptions for a successfully calculated PVGIS scenario, which the server recalculates. Other legacy snapshot fields are ignored. Provenance logic (source catalog, versions footer, licence status) was carried over. See `PARCEL_DOSSIER.md`. The text below records the legacy implementation.

**Panel-complete server PDF implemented.** The direct-map action loads any
remaining registered sections in bounded batches, then POSTs their text,
status and available provenance to `/api/v1/enrichment/parcel/report/{reference}`.
The server accepts only known section IDs, bounds the request size and escapes
all captured text before rendering it. Each section contributes up to 18,000
characters; if the combined request exceeds its 220 KB client budget, longer
section text is shortened and marked in the report. The report combines those
section snapshots with the server parcel identity/read model, carries a report
ID and available dataset/model versions in the footer, and lists source,
release/vintage and licence on an attribution page. It refuses to attach a
reference-keyed model when its canonical geometry or feature ID does not match
the selected polygon. GET remains available as a server-only fallback.

**Source register improved:** `land_registry/source_catalog.py` maintains
provider and terms URLs, source aliases, licence or reuse terms, attribution,
and the basis/status for each statement. The report combines those catalog
entries with release, version, model and update values returned for the specific
block; it does not infer a data vintage from the report date or an update
timestamp. Unmatched sources and unreviewed provider terms are called out. OSM
reports include contributor credit, ODbL notice and the full copyright URL
required by the [OSM copyright and licence page](https://www.openstreetmap.org/copyright).
The environmental-risk snapshot records INGV MPS04 as an additional source
alongside its combined municipal DPC/ISPRA source, so the PDF source register
does not assign MPS04 terms to the other risk values.
The EGMS parcel adapter is identified as Zornade's 100 m derived summary: its
ODbL statement comes from the [Zornade dataset page](https://zornade.com/data-downloads/),
while the underlying Copernicus source is reported separately with the
[Copernicus Land Monitoring Service reuse conditions](https://land.copernicus.eu/en/data-policy)
and its source, modification, EU-funding and non-endorsement attribution. The
catalog does not claim that those Copernicus terms are a named licence. The MEF
IRPEF entry records CC BY 3.0 and the citation required in the [official
methodology](https://www1.finanze.gov.it/finanze/stat_dbNewSerie/public/contenuti/nota_metodologica.pdf).
For the 2020 ISPRA PAI/PGRA mosaics, the catalog records CC BY-SA 4.0 and the
source citation specified by [ISPRA's dataset terms](https://idrogeo.isprambiente.it/cms/wp-content/uploads/2022/03/Licenza_Condizioni_Uso_Pericolosita_Indicatori_Rischio_ISPRA.pdf).
The OMI source citation is recorded from the [provider's published
guide](https://telematici.agenziaentrate.gov.it/pdf/guidaFornitureOMI.pdf), and
the guide's reuse licence remains unverified because it specifies the citation
but not a named licence. The catalog records ISTAT's CC BY 4.0 terms from its
[open-data page](https://www.istat.it/dati/open-data/).
The INGV entry records the MPS04 citation and CC BY 4.0 under the provider's
[open-data legal notice](https://data.ingv.it/docs/note-legali.html), which
applies unless a dataset states otherwise; MPS04 is listed in the [INGV web
services catalogue](https://data.ingv.it/metadata/web_service_ita). The panel
credits INGV and states that its PGA value is the nearest native-grid point,
not an interpolation. The fires adapter now requests `VIIRS_NOAA21_NRT`
(NOAA-21/JPSS-2 VIIRS near-real-time) explicitly and records that product ID
in the response provenance; the [FIRMS VIIRS product description](https://firms.modaps.eosdis.nasa.gov/content/descriptions/FIRMS_VIIRS_Firehotspots.html)
documents NOAA-21 as a distinct 375 m product. NOAA announced that S-NPP data delivery ends on
2026-11-02 and directs users to NOAA-21 as primary and NOAA-20 as secondary
([NOAA transition notice](https://www.nesdis.noaa.gov/news/cessation-of-suomi-national-polar-orbiting-partnership-s-npp-data-users-onafter-november-2-2026)).
NASA Earthdata says its CC0 default applies to data from NASA-led missions,
while data from other providers retain their sponsoring organization's terms
([NASA Earthdata data-use policy](https://www.earthdata.nasa.gov/engage/open-data-services-software/data-use-policy)).
NOAA NCEI metadata labels the upstream NOAA JPSS VIIRS sensor-data record CC0
([NCEI product metadata](https://www.ncei.noaa.gov/access/metadata/landing-page/bin/iso?id=gov.noaa.ncdc%3AC00864)).
That is not a licence statement for NASA FIRMS's derived NOAA-21 active-fire
detections. The reviewed FIRMS product page does not state a reuse licence, so
the catalog leaves the feed's licence unassigned pending product-specific
terms.

**Provider access review (2026-10-10):** [OpenDemanio](https://dati.agenziademanio.it/)
describes its open data as freely reusable, but the reviewed page does not name a
licence or set concession-record-specific conditions. The Agenzia delle Entrate
[data-access guide](https://www1.agenziaentrate.gov.it/web_app_entrate/accesso_ai_dati.html)
describes SISTER and Portale per i Comuni access as convention-based for
authorized public bodies; it does not establish downstream reuse rights for the
cached records shown here. The OMI [supply guide](https://telematici.agenziaentrate.gov.it/pdf/guidaFornitureOMI.pdf)
requires its source citation but does not name a reuse licence. The PVP
[site guide](https://pvp.giustizia.it/pvp/it/guida.page) confirms public search
and viewing without credentials but does not state a general reuse licence for
notices or attachments. These access and citation statements do not establish
downstream reuse rights; the source catalog links the reviewed pages and leaves
those dataset licences unassigned.

**Remaining:** confirm dataset-specific reuse terms for OMI, cadastral/SISTER,
Demanio, PVP, and the NOAA-21 FIRMS feed; verify representative reports on
provisioned data. Combined IdroGEO/DPC panel
provenance remains unmatched rather than inheriting the PAI/PGRA licence because
it combines distinct providers. Panel content is the browser response captured
at export time; source provenance remains subject to what each endpoint returns.
Keep PDF layout separate from the interactive map DOM.

#### Phase 6 — Optional, only after a product decision

- Data-quality feedback channel (SRS §14: needs moderation first).
- Declared-price overlay on the estimate (needs storage, privacy review and a
  rule for how it affects shared views).

#### Summary schedule

The previously stated total of 11–13 developer-weeks is not supported by the
scope as written: Phase 3 includes unestimated upstream data work, and Phase 4
depends on a footprint input that is not yet available in the parcel block. Keep
the app-side ranges for Phases 0–2 and 4–5 provisional, and set a project total
only after Phase 3's upstream slices and the energy input are scoped.

#### Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Upstream stores not built on a given host | Sections empty in production | Show an explicit host/store availability state; include the source in `/api/v1/enrichment/status`; document build steps |
| Read-model rows stale after new blocks | New sections empty until refresh | Bump the payload fingerprint/version and define refresh/backfill behavior |
| Panel renders slowly on dense urban parcels | Poor perceived performance | Lazy sections, response-size budget and bounded source queries; measure cold-build and warm-cache latency separately |
| Frontend contract tests pin current markup | Churn in Phase 1 | Update tests with the change; keep selectors for the legacy page stable |
| Modelled values read as facts | Misleading users, legal exposure | Mandatory envelope, badge and disclaimer; no energy or valuation coefficient without evidence |
| Licence and attribution gaps | Source terms are unclear to users | Record each source's licence and release date in the data inventory and attribution page; populate envelope metadata when known |
| Scope creep toward Zornade's social features | Distraction | Held out by SRS §14 unless the product direction changes |

#### Success measures

- Section coverage on the sample set from Phase 0: share of parcels with each
  block populated, reported per comune.
- Primary panel shows at least every legacy section (Phase 1 gate).
- `parcel/details` p95 latency at or below the Phase 0 baseline plus 20 percent
  while blocks multiply; lazy sections should keep first render unchanged.
- Zero sections that fail the whole panel when a store is down (tested).
- Every numeric card names its source and data vintage/version when known; each
  modelled value also states its method and assumptions.
- Compare Zornade and land-registry PDF outputs for the same parcel using the
  acceptance check in A.5.8; keep S9 partial until the sample comparison is
  complete and known report-content gaps are resolved or explicitly accepted.

### A.9. Decisions and recommended defaults

1. **Constraints route.** The app-side required-ID endpoint, coverage states,
   panel, map layers, dossier and v2 risk component are implemented as of
   2026-10-15. The contract remains proposed until `aecs4u-stats#48` publishes
   the schema and source data. Verify the full parcel intersection and coverage
   behavior against provisioned records; revise the contract if the upstream
   schema changes.
2. ~~**Panel form factor.**~~ **Decided 2026-10-09: side sheet** that keeps the
   selected parcel visible on the map. The implemented desktop width is
   `clamp(380px, 36vw, 540px)`; the initial 480–560 px target was narrowed after
   layout review. No centred modal.
3. ~~**Legacy page.**~~ **Decided 2026-10-09:** keep `/map-legacy` for upload and
   analysis; make `/map` the canonical surface for new parcel-panel work and its
   server-rendered PDF action. Full parity is still an acceptance gate, not a
   result established by mocked-data checks. Share pure logic only, not markup.
4. **Estimator scope.** Recommended: retain the three current inputs (typology,
   state, area) until the server model accepts and uses additional inputs.
5. ~~**Energy.**~~ **Decided 2026-10-09: yes.** The first release is a parcel-level
   modelled estimate with visible assumptions; no per-roof compass or
   per-building viability until a documented roof model exists.
6. **Community features.** Continue to defer X1 and X2 as in SRS §2.2 and §14;
   revisit only with a moderation and validation design.
7. **Declared price.** Defer until visibility, privacy and shared-link behavior
   are specified; it is not required for the first estimator release.

### A.10. Notes on existing documents

- The whole-product overview in §§1–6 above is dated 2026-07 with later edits.
  Appendix A consolidates the focused panel audit from the removed document;
  keep the broader status matrix aligned as later phases ship.
- `docs/github_issues/zornade_map_enrichment/02` to `08` remain accurate as
  upstream-dependent issues. Map them to Phase 3 slices above; issue 01 (vector
  tiles) is done.
- `MAP_SRS.md` §13 reports v1.2 requirements as met "for current blocks". The
  primary panel exposes and collapses the currently available OMI, income,
  demographic, POI and risk sections; MAP-FR-031 is recorded as met for those
  sections. Production source coverage still needs provisioned-host review.

### A.11. Open issues found during implementation

**Previous verification run (2026-10-09):** 69 focused Python tests passed across async
PostgreSQL adapters, parcel fallback and block assembly, reports, derived
metrics, map contracts and panel contracts. Ruff checks, Italian catalog
compilation and `git diff --check` passed. The full browser suite passed all 11
cases, including parcel-wide EGMS rendering and PDF export. The
post-implementation baseline completed with 20 parcels and 40 successful
details calls; see [`PARCEL_PANEL_BASELINE_2026-10.md`](PARCEL_PANEL_BASELINE_2026-10.md).
That run predates the SISTER address-card and later panel follow-ups; see the
subsequent verification records below.

**Socioeconomic scope and translation follow-up (2026-10-09):** focused
adapter fixtures cover country-level relocation data and municipality
population/age series; route tests cover the country fallbacks when local
province-level results are empty. Panel copy now translates the geographic
scope and known indicator labels. The focused Python group passed 47 tests, the
Node panel suite passed 39 tests, the Italian catalog compiled, and
`git diff --check` passed. The relocation-view schema and live source coverage
still need verification on a provisioned PostgreSQL host.

**Address slice verification (2026-10-09):** 21 focused Python tests passed
for the SISTER address query, block inventory and PDF snapshot route; the
JavaScript parcel-panel suite passed. Ruff correctness selectors, JavaScript
syntax check, Italian catalog compilation and `git diff --check` passed. The 11
browser fixture now disables the app lifespan so these UI tests do not start
the unrelated Panel server or database warmups. The parcel-panel browser run
at that checkpoint passed 10 of 11 cases; the section-fill case timed out
before making enrichment requests, then passed in isolation.

**MPS04, sticky-header and report-source follow-up (2026-10-09):** 31 focused Python tests,
the Node panel suite and 27 map contract tests passed. All 11 parcel-panel
browser cases passed after both the fallback and active theme-override
templates received the header actions; the PDF-export browser case also passed
after the source-register metadata change. Risk-section reports now keep INGV
MPS04 attribution and model version separate from the combined municipal risk
source. Ruff, JavaScript syntax checks, Italian catalog compilation and
`git diff --check` passed. The local MPS04 database is not built on this host;
valid, unmatched and unavailable states are covered by fixed fixtures, but
live-grid values still need a provisioned-host check.

**Optional spatial-store readiness follow-up (2026-10-09):**
`/api/v1/enrichment/status`
now reports whether the MPS04, ISPRA mosaic and EGMS stores are supported and
built on the current host. The readiness probes call only each store's
availability function; query adapters are checked for presence without being
called with missing coordinates or bounds. The two focused readiness tests
passed. The broader run had 33 passes and one failure in the existing OpenAPI
health-schema name assertion (`HealthResponse` versus
`land_registry__models__HealthResponse`); the failure is unrelated to parcel
panel readiness. A later contract-test follow-up checks that the `/health`
reference resolves to the authoritative local `HealthResponse` schema while
allowing FastAPI to namespace duplicate class names from installed packages.

**Spatial-store provisioning audit (2026-10-09):** direct availability checks
from the application's Python environment report MPS04, ISPRA mosaics and EGMS
missing on this host. The MPS04 and ISPRA importer help output was verified;
the large ISPRA import and all store writes were left unrun. The Phase 3
runbook now records each store's landing/import path and the post-build status
and parcel-result checks.

**Pre-migration provisioning recheck (2026-10-10):** read-only availability
probes reported `reason: not_built` for `hazards_mps04`,
`hazards_ispra_mosaics`, and `egms_subsidence`. The later parcel-cache schema
migration does not provision those Phase 3 stores; their store-backed parcel
checks remain pending, and no imports or store writes were run.

**Current feature revalidation (2026-10-09):** 141 focused tests passed across
parcel block inventory, derived metrics, report source metadata, async
PostgreSQL adapters, Phase 0 contracts, map/panel frontend contracts and all 11
parcel-panel browser cases. The Node panel suite passed 39 tests; the Italian
catalog compiled, focused Ruff correctness/import checks passed, and
`git diff --check` passed. The mobile browser case exposed and now covers a
smooth-scroll race where lazy section growth moved the destination after the
tab click; the selected tab is held through the programmatic scroll. The
browser fixture uses mocked provider responses; this run does not replace live
coverage and report-source review on a provisioned host.

**Section deep-link follow-up (2026-10-10):** individual section fragments now
restore to their card and canonicalize to `#sezione-{id}`; the existing
`#parcel-section-{id}` form is accepted too. The panel keeps the target aligned
while earlier cards finish loading, preventing late content growth from
changing the selected card; manual pointer, wheel, touch, or keyboard input
releases that alignment. Chromium browser cases cover both fragment forms,
lazy loading, tab selection and URL tracking during subsequent scrolling. Four
consecutive isolated runs of the canonical-fragment case passed. The final
focused run passed all 143 Python tests, including all 13 browser cases; the
Node panel suite passed 39 tests, Ruff correctness selectors passed, JavaScript
syntax checks passed, the Italian catalog compiled and `git diff --check`
passed. The browser fixture remains mock-backed and does not prove live source
coverage.

**Canonical feature cache follow-up (2026-10-10):** the details route now
forwards the selected feature ID for both full and panel views. Shared and
SQLite cache keys include that ID, and cache hits verify the stored parcel
identity. Feature-specific cold reads require the canonical map source, so a
missing source returns 503 and an unmatched ID/reference pair returns 404.
The focused async enrichment and report tests passed 39 cases; Ruff correctness
checks and `git diff --check` passed. No live duplicate-reference dataset was
available, so this is verified with fixtures rather than provisioned parcels.

**Read-model coverage wording (2026-10-10):** the Data tab now labels partial
coverage separately, shows block source and dataset version when available, and
clarifies that independently loaded sections may have data even when the
read-model block is empty. The JavaScript panel tests passed, the Italian
catalog compiled to a temporary MO file, and `git diff --check` passed.

**ISTAT missing-store diagnostic follow-up (2026-10-10):** an enrichment
readiness request showed the optional safety, BES and demographic probes
raising `FileNotFoundError` because `/data/istat/eurostat.IT.sqlite` is absent
on this host. The checkout now checks the installed engine's DuckDB and SQLite
paths first and skips those probes when neither store exists; legacy SQLite
and raw BES fallbacks still run. A missing BES snapshot returns unavailable
without an open-error traceback, while failures against an existing store
retain traceback diagnostics. The ISTAT source itself is still unprovisioned
here, so these datasets remain unavailable until their data volume is mounted
or built.

**Reported status-endpoint traceback recheck (2026-10-10):** the pasted
`/api/v1/enrichment/status` log still shows the pre-fix behavior: it contains
the old exception traces and the request returns HTTP 200. The stack shows the
worker executing the old direct `checker()` call and the unconditional BES
open-error logging path. The current checkout routes availability probes
through `_installed_istat_availability`, skips them when both ISTAT stores are
absent, and treats a missing BES SQLite snapshot as unavailable. Focused
regression tests cover these paths. This verifies the checkout, not the worker
that produced the supplied log; that worker must load the updated code to stop
printing the old traces. The HTTP 200 is expected because this status route
reports optional-store readiness; it does not mean those datasets are
available. The ISTAT source still needs provisioning for these datasets to
report available.

**PVP cold-load follow-up (2026-10-10):** a browser run exposed that the parcel
panel's nearby-sales request could trigger a cold read of 717,150 rows from
`modelview.v_map_sale_points`, measured at about 51 seconds on this host. The
view has no spatial index suitable for the requested radius, so a bounded
nearby query cannot yet be shown to use an indexed plan. The panel endpoint now
starts the shared snapshot refresh and returns `503` with `Retry-After` while
the first snapshot is loading; the nearby-sales card includes a Retry action.
Python normalization and grid-index construction run in a worker thread, so
they do not monopolize the async event loop. The browser fixture now intercepts
the nearby-sales route instead of reaching the live database. The existing 13
browser cases passed after that change; a follow-up cold-cache case also
passes in isolation, verifying the Retry action recovers on a subsequent 200.
This keeps an individual parcel request
nonblocking, but it does not reduce the full-view query or initial PVP data
latency; an upstream spatially indexed/materialized query contract remains an
open performance requirement. No post-change live cold-load benchmark has been
captured.

**Cadastral reference and SISTER matching follow-up (2026-10-10):** the live
parcel sample uses AdE references such as `H501A048600.D`, while several cold
lookups previously accepted only the underscore form. The shared parser now
normalizes both spellings and extracts an optional urban section. SISTER
building, address and document matching applies that section where the schema
supports it and returns no section-specific match when the source cannot
distinguish sections. Fixtures cover both reference forms, section collisions,
and old schemas without a section field. The legacy SISTER SQLite cache exists,
but its `cadastral_locations`, `visura_properties`, `building_identifiers` and
`building_classifications` tables contain zero rows.

**Map panel SISTER PostgreSQL follow-up (2026-10-10):** the `map_v2.html` parcel
panel now reads Main address and Buildings from the local
`aecs4u-stats.sister.v_sister_property_by_cadastral_parcel` view. For sheet 9,
parcel 1452, that property view has zero rows. The cadastral map index contains
three matched identities: cadastre type F subunits 5 and 6 and cadastre type E
subunit 5, each with `property_count = 0`; together they have six visura document
metadata records. The exposed document view contains metadata only, so it
cannot supply an address or building classification. The panel correctly shows
no recorded address or building data for this parcel until structured property
rows are imported into the SISTER PostgreSQL view.

**Coverage-state follow-up (2026-10-10):** parcel blocks now emit the shared
`coverage` values `full`, `partial` or `unavailable`; the compatibility
`coverage_status` field uses the same vocabulary. The read-model fingerprint
was bumped so cached rows with the previous `not_available` value are rebuilt.
In section loading, 404 means no matching data and is not retried; 503,
network and 5xx errors are described as temporary and offer retry. The panel
does not infer geographic non-coverage or an undetermined measurement unless
the source supplies that state.

**Shared PostgreSQL response sample (2026-10-10):** after applying the
read-model migration, the baseline tool recorded 40 successful panel detail
responses for 20 parcels. Every response identified
`aecs4u-stats PostgreSQL via asyncpg`; median latency was 9 ms and nearest-rank
p95 was 15 ms. At that time, a cold rebuild also set `read_model.cached` true
when its upsert succeeded, so the sample cannot distinguish cache hits from
successful writes. The service now reports false for rebuilt responses, even
when it persists the new row, and true only when a stored row serves the
response. The old sample used the local `postgres` superuser and returned no
canonical feature IDs; it does not verify Neon production performance, Cloud
Run's effective privilege scope, or duplicate-reference cache keys. The
baseline tool now reports the cache database and separate response/hit timing.

**Controlled PostgreSQL cold/repeat sample (2026-10-10):** a fresh no-reload
local server ran `view=panel --refresh-first` for 20 parcels. The first request
for every parcel rebuilt and persisted the payload; the second request read
that row. All 40 responses returned the requested feature ID, and the 20
repeat hits came from PostgreSQL (median 15 ms, p95 55 ms). Forced builds had
median 5.168 s and p95 10.580 s. No reference collision appeared, so
duplicate-reference isolation remains unverified. The sample has seven
available blocks and is not matched to the original three-block full-response
baseline; see the [raw sample](baselines/parcel_panel_controlled_2026-10-10.json).

**Cold-build profile and adapter concurrency (2026-10-10):** profiling one
slow Tolfa parcel found `_build_parcel_enrichment` spent 4.035 s in total,
including 2.849 s in OpenData/SISTER lookup and 1.183 s in PVP lookup. Those
independent blocking database calls used to run serially. The initial async
request-path change ran OpenData and PVP concurrently, after spatial context
was resolved. Its attempted isolated-server recheck did not produce a sample:
the worker could not reach its configured database host, and the baseline's
initial health check ran before application startup completed. The collector
now polls `/health` for up to 60 seconds by default. The 2026-10-15 follow-up
below records a successful local run after moving all three independent
lookups ahead of the spatial-context wait.

**Cold-path overlap follow-up (2026-10-15):** the async details path now starts
the SISTER building/address query, OpenData lookup and PVP lookup alongside
the independent spatial-context query. It uses the async PostgreSQL SISTER
adapter and passes the result into the shared builder; synchronous callers
retain their existing path. A fresh local `view=panel --refresh-first` run
measured the same 20 `(reference, feature ID)` pairs as the 2026-10-10 sample:
40/40 detail requests succeeded, 0 feature-ID mismatches, cold median 4.614 s
and p95 6.188 s, followed by 20 PostgreSQL cache hits (median 14 ms, p95
157 ms). The `buildings` source query was available for all 20 parcels, but no
cached SISTER property rows matched this sample; that does not imply that the
physical parcels have no buildings. The response therefore had eight available
blocks instead of seven, so this is not a strict same-shape comparison. Its
cold p95 is below the preliminary 8.140 s threshold, even with the added
availability block. No duplicate-reference group appeared. See the [raw
sample](baselines/parcel_panel_controlled_2026-10-15.json) and
[`PARCEL_PANEL_BASELINE_2026-10.md`](PARCEL_PANEL_BASELINE_2026-10.md). This
remains local-host evidence; production role, source coverage and duplicate-
reference isolation remain open.

**Targeted Italian copy edit (2026-10-10):** refined the parcel coverage note,
nearby-sale missing-location and truncation messages, and country-scope safety
labels for clearer, more natural Italian. This is a focused copy edit, not an
independent native-language review of the full primary-map catalog; that review
remains open.

**Canonical point-lookup follow-up (2026-10-10):** the prior baseline reached
the local cadastral copy before querying PostGIS, so its 20 point responses
contained no canonical IDs. The endpoint now queries the canonical map source
first and uses the local copy only as fallback. A read-only recheck through the
application route handler sampled five unique parcels each in Rome, Milan,
Tolfa and Monte Romano: all 20 responses had distinct canonical feature IDs
and distinct references. Route tests also verify canonical-first behavior and
local fallback after a canonical query error. This confirms feature-ID
availability on the configured local source; it does not include duplicate
references or verify the likely Neon production source.

**Baseline identity and percentile follow-up (2026-10-10):** the collector now
de-duplicates by canonical `(reference, feature ID)` pair, so separate map
features sharing a cadastral reference can both be measured. Its summary counts
reference groups containing multiple feature IDs and checks each returned
read-model ID. It marks duplicate-reference isolation only if at least one
collision is present, every response retains the requested feature ID, and
each feature's repeat request is a cache hit. The p95 uses the nearest-rank
definition. Existing raw samples predate this change and do not demonstrate a
duplicate-reference cache read; a fresh capture must find such a collision to
close that acceptance item.

1. **Local cache paths are verified; production-role acceptance remains
   open.** A 2026-10-15 refresh-first `view=panel` run on the same 20 parcels
   as the 2026-10-10 sample measured cold-build p95 at 6.188 s and repeat-hit
   p95 at 0.157 s. All 40 responses preserved the requested feature ID. The
   configured `postgres` role is a superuser, and the operator reports that
   Cloud Run uses the same username, so these results do not establish
   least-privilege access. The GCP secret could not be independently
   inspected, and Neon, the likely production target, is not configured or
   verified. No duplicate-reference group appeared, so cache isolation for
   collisions remains unverified. The baseline collector preserves
   `(reference, feature ID)` identities and reports collisions when sampled;
   an actual collision is still needed to verify isolation. A prior
   `view=panel` sample measured the application-side SQLite fallback: 14 cold
   calls (median 6.461 s, p95 8.492 s) and 26 local cache hits (median 0.010 s,
   p95 0.026 s). Non-refresh requests try PostgreSQL first for both views;
   after a miss or unavailable read, only `view=panel` checks the bounded
   SQLite cache. Cold builds attempt PostgreSQL persistence, with SQLite
   persistence as the compact-panel fallback when that write does not succeed.
   `view=full` has no SQLite cache fallback.
2. **Store availability differs by host and capture.** The Phase 0 sample had
   three populated blocks; the post-implementation sample had six, the
   2026-10-10 controlled sample had seven, and the 2026-10-15 sample had eight
   (`basic`, `buildings`, `cadastral`, `demographics`, `economics`,
   `population`, `solar`, `valuation`) on all 20 parcels. The `buildings` block
   confirms the SISTER view query succeeded; it returned no cached property
   rows for these parcels. Coverage and specific source correctness
   still need provisioned-host review; some section interactions were exercised
   with mocked responses; the parcel-panel browser fixture stubs all enrichment
   responses, so it checks UI behavior rather than live source coverage. The
   EGMS, MPS04 and ISPRA mosaic stores report missing
   on this host according to direct package availability probes, so their new
   spatial queries have fixture and browser-contract coverage but
   no live-cell or live-grid verification here.
3. **Store path configuration.** Logs show census expected under
   `/mnt/mobile/data/istat/` and the ISTAT SQLite under `/data/istat/`. Two roots
   suggest an environment variable is set for one and not the other.
4. **Uncovered province lookup still returns 404.** The `/parcel/by-reference`
   API returns 404 for areas where cadastral data is not covered; the map turns
   that response into an explanation that Bolzano and Trento are not currently
   covered. The browser check exercises this path. Missing parcel geometry
   remains an upstream source gap; an address is also unavailable when no
   matching SISTER property row is cached.
5. **Historical full-suite issues.** `moto` is still missing, so
   `tests/test_corrected_s3_storage.py` cannot be imported in this environment.
   An earlier full-suite run also reported a psycopg2 segmentation fault while
   running `tests/test_datashader_service.py` and an environment-specific
   `test_map_backs_off_and_reports_outage` failure. The current isolated run of
   `tests/test_datashader_service.py` passes all 56 tests, and the loopback TLS
   helper passes all 12 tests; the earlier segfault is not reproduced in
   isolation. The full suite has not been rerun here. These issues are separate
   from the focused parcel checks; the earlier OpenAPI schema-name assertion
   was corrected and passed in the later focused run.
6. **Local details latency is below the preliminary target; production
   acceptance remains open.** Phase 1 targets a p95 no more than 20% above the
   6.783 s Phase 0 cold-build p95 (8.140 s). The 2026-10-15 refresh-first
   `view=panel` run measured cold p95 at 6.188 s across 20 forced builds and
   PostgreSQL repeat-hit p95 at 0.157 s. It used the same 20 parcel/feature
   pairs as the 2026-10-10 run and returned one additional available block
   (`buildings`), so the timing comparison is not strict same-shape evidence.
   It meets the numeric target locally; production-role latency, full source
   coverage and the under-300 ms identity target remain unverified. The browser
   suite does not time identity rendering.
7. **Use a no-reload process for benchmarks.** The repository's reload-mode
   server restarts on file writes and can briefly fail health checks. The
   complete post-implementation sample used a fresh no-reload server on port
   8012; the earlier port-8011 endpoint became unresponsive during a run that
   was discarded.
8. **Parcel hazard and EGMS queries cap candidates at 5,000.** Both responses
   mark capped results partial. Provisioned-host review should measure how
   often each cap is reached and verify source coverage and reported overlap.

**Dossier and browser acceptance follow-up (2026-10-15):** the live
Civitavecchia ISTAT BES lookup returned 90 indicators in annual edition 2025;
the current Typst dossier rendered that complete edition with province/NUTS3
scope, indicator units and reference periods. The parcel-panel browser smoke
suite passed all 14 cases with system Google Chrome. Its fixture now falls back
to a discovered system browser when the Playwright-managed Chromium binary is
absent; two stale assertions were updated to expand collapsed cards and check
the displayed MWh/GWh scaling. This remains fixture-backed for panel providers
and does not replace the same-parcel comparison against a downloaded Zornade
PDF or target-host source-coverage checks.

**FIRMS map observation age (2026-10-15):** fire-marker popups now derive a
UTC timestamp from the feed's acquisition date/time, display that timestamp
alongside its relative age, and retain fire radiative power. Invalid or missing
times remain explicitly unavailable. The implementation uses the existing
NASA FIRMS layer and its map toggle; no provider data was available for a live
feed check in this review.

**Panel follow-ups (2026-10-16):**

- The OMI estimator no longer prefills the parcel's land area as the commercial
  surface (a 1,283 m² villa plot was priced as 1,283 m² of dwelling). The
  surface starts empty with a land-area hint; the selector is grouped into the
  detected zone and other zones, condition codes and the placeholder type `0`
  are labelled, and income-bracket keys (`le_0`, `0_10k`, `over_120k`) are shown
  as ranges.
- The local OMI quotes come from `zornade.zornade_zone_omi`, one prevalent
  typology per zone with no semester, because the `geo.geo_identifier`,
  `spatial.market_zone` and `facts.market_quote_fact` relations are absent. For
  zone E5 the snapshot shows "Villas" at 770–1100 €/m², while Zornade's current
  semester lists villas at 1550–2300 and 770–1100 for economic apartments. The
  panel now says the semester is undeclared; the band itself needs the
  quote-fact schema.
- Cards whose source cannot answer (`unavailable`) start collapsed with "Not
  available" in the header; real negatives (no concession, no fire) stay open.
  The municipal solar card starts collapsed with its high-viability share in
  the header and scales totals to MWh/GWh.
- Quality of life leads with up to eight curated indicators (newest value, year
  and unit) and keeps the full year tables behind a filter. Country-level safety
  and quality-of-life fallbacks are no longer shown as parcel context. The
  source is labelled "ISTAT BES", but several indicators (for example ICityRank
  and Ecosistema urbano) come from other publishers; check the label upstream.
- Locale: the map catalogue now follows `?lang=` and the theme's `language`
  cookie as well as `lang`. Before, the header picker changed the chrome while
  the panel stayed in the Accept-Language locale. A `?lang=` choice is stored in
  both cookies and the app's own pickers write both.
- `enrichment_status()` read `_terrain.TINITALY_STORE_DIR`, which the installed
  package does not re-export; it is now imported from `terrain.config`. The
  status loop also tolerates a missing dataset key, and the night-lights block
  uses the shared envelope and coverage vocabulary.
- The status endpoint reports `istat_safety`, `istat_bes`, `istat_demographic`
  and `hazards_seismic` as unavailable because it probes the file stores, while
  the panel serves those datasets from PostgreSQL. Treat those four flags as
  unreliable until the probes read the database.

### A.12. Zornade backend calls seen while opening a parcel (Observed)

Hostnames and paths only; no tokens or payloads recorded.

| Call | Purpose |
|---|---|
| `rpc/get_parcels_in_bounds_secured` | Parcel polygons for the viewport, zoom 16 and above |
| `rpc/get_fogli_in_bounds`, `get_provinces_in_bounds`, `get_regions_in_bounds` | Administrative substitutes at lower zoom |
| `rpc/get_parcel_full_detail_rated_v4` | Whole parcel profile in one call |
| `rpc/omi_zone_facts_by_parcel` | OMI zone data for the valuation and quotes cards |
| `rpc/user_has_parcel_access` | Entitlement check |
| `rpc/get_recent_fire_hotspots`, `get_criticality_zones` | Live overlays |
| `functions/v1/valutazione/osservazione` | Records a valuation observation when a parcel opens |
| `rest/v1/saved_parcels` | Favourite state (ours: `is_favorite` on `saved_parcels`) |

### A.13. Mapping Zornade sections to land-registry blocks

| Zornade section | Envelope block | Source in this repo | Status |
|---|---|---|---|
| Catastale, Superficie | `basic`, `cadastral` | Cadastral store, ISTAT | ✅ |
| Indirizzi | `address`, `addresses`, `anncsu_addresses` | SISTER `aecs4u-stats.sister.v_sister_property_by_cadastral_parcel.address`; ANNCSU regional indirizzario for municipality-geocoded points | SISTER values remain a capped cadastral-key match; ANNCSU uses point-in-parcel matching with method, vintage and coordinate-coverage metadata. App integration is present; upstream release and regional store provisioning remain |
| Valutazione, OMI | `valuation` | `aecs4u_stats.omi` | Primary selector, estimate, yield and percentile ✅ |
| Storico OMI | `valuation_history` | `/omi/history` | Primary chart and versioned trend rows ✅ |
| Edifici | `buildings`, `building_footprints` | SISTER cadastral records and OSM regional building polygons | SISTER property records stay distinct from the OSM footprint count, clipped area and mapped parcel share; the app consumer is wired, while the upstream `aecs4u-stats#29` release and regional store remain pending |
| Suolo | `land_cover`, `land_use` | CORINE 2018 (`aecs4u_stats.corine`, installed) | The app exposes the CORINE class distribution and coverage through its API, panel and dossier; regional import is unavailable until upstream release and provisioning. `land_use` remains unimplemented |
| Fotovoltaico | `solar` | `solar.solar_potential_comuni`, `serving.municipality_profile` | Municipality-wide aggregates in the Energy tab; no parcel/roof estimate ✅ |
| Terreno | `terrain` | INGV TINITALY 1.1 (`aecs4u_stats.terrain`, installed) | Parcel API, lazy panel card and dossier are wired; the importer/query is installed and no regional raster store is provisioned |
| Rischi | `risk`, `subsidence`, `coastal_erosion`, `protection_constraints` | ISPRA polygon mosaics, ISPRA coastal dynamics, IdroGEO, EGMS, and schema-gated protection sources | Parcel hazard and EGMS cell intersections; local coastal snapshot is provisioned in the configured stats DB (99,229 features), with real-parcel matches and explicit candidate-cap partial coverage; protection API, panel, map layers, dossier and v2 risk component are wired, but upstream source tables and full coverage remain pending; target-host review remains |
| Vincoli | `cultural_heritage` | `spatial.protection_area` (upstream pending) | Required-ID API, lazy panel section and schema-gated map layer are wired; records and complete coverage await upstream stores |
| Economia | `economics`, `nightlights` | MEF, NASA Black Marble VIIRS VNP46A4 | Income, grouped Gini and price-to-income ✅; night-light importer, parcel query, panel and API catalog implemented; local annual raster provisioning remains |
| Demografia, popolazione | `demographics`, `population` | `census`, `sezioni_urbane`, `raster` | Census indicators plus section-scoped sex split and age pyramid ✅ |
| POI | `poi` | `osm` | Empty block, endpoint ✅ |
