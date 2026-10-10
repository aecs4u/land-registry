# Parcel detail panel: feature and gap analysis, and development plan

*Date: 2026-10-09 (audit and plan), status updated 2026-10-10 after the Phase
1–5 implementation slices, the compact panel cache follow-up, parcel-level
EGMS aggregation, section deep-link navigation, and shared cache provisioning.
Scope: the parcel detail experience that opens
when a parcel is clicked on the primary `/map` page, compared with the equivalent
modal in Zornade (app.zornade.com).*

This document **extends** [`ZORNADE_GAP_ANALYSIS.md`](ZORNADE_GAP_ANALYSIS.md)
(July 2026, whole-product scope) and [`MAP_SRS.md`](MAP_SRS.md) (requirements
and conformance). It does not repeat their matrices. It re-measures the parcel
panel alone against the current code, records what has changed since those
documents, and turns the result into a phased plan. Data-source details and
licences for Zornade's blocks are in
[`zornade-cadastral-parcel-reference.md`](zornade-cadastral-parcel-reference.md).

## Status

| Phase | State | Evidence |
|---|---|---|
| 0 Alignment and baseline | **Local forced-build/repeat sample verifies 20 PostgreSQL hits (median 15 ms, p95 55 ms); no duplicate-reference collision was sampled. Production role and same-source latency acceptance remain open** | [PARCEL_PANEL_BASELINE_2026-10.md](PARCEL_PANEL_BASELINE_2026-10.md), [baseline script](../scripts/parcel_panel_baseline.py), [read-model SQL migration](../scripts/sql/parcel-enrichment-read-model.sql), [controlled sample](baselines/parcel_panel_controlled_2026-10-10.json); [constraints contract](PROTECTION_CONSTRAINTS_CONTRACT.md) clarified |
| 1 Primary panel and legacy parity | **Mostly done**. SISTER addresses are included in the read model and loaded by a dedicated panel section; both depend on cached property records, and live coverage is unverified. Header actions and the Italian catalog are in place. A targeted copy edit is recorded in §11; independent native proofreading and controlled latency acceptance remain | [panel core](../land_registry/static/parcel-panel-core.js), [section registry](../land_registry/static/parcel-panel.js), map templates, side-sheet styles, browser smoke suite, post-implementation baseline comparison |
| 2 Derived metrics | **App implementation and fixture tests done**; provisioned-data and Italian-copy review remain | OMI quote/history and MEF adapters; fixed-fixture metric tests; OMI estimator and income sections |
| 3 Per-parcel geospatial | **Parcel hazard and EGMS grid-cell intersections implemented; MPS04 point estimate added**; MPS04, ISPRA mosaic and EGMS stores are absent on this review host. Coastal-change source selected, but its upstream query/store is not implemented; constraints remain gated on `aecs4u-stats#48` | ISPRA and EGMS polygon-intersection routes and panel sections; `/mps04/pga` with nearest-grid provenance and unavailable state; host readiness at `/api/v1/enrichment/status`; provisioning steps below; coastal query tracked in local issue draft 14 |
| 4 Energy | **Municipal PV aggregates implemented**; parcel-level estimates and economics still wait for a defensible footprint and upstream work in `aecs4u-stats#31` | Energy tab reads the municipality solar block with its spatial scope and available source version |
| 5 Report/export | **Panel-complete PDF and source catalog implemented**; provider-term and provisioned-data reviews remain | Bounded POST snapshot for the panel export and GET server-only fallback at `/parcel/report/{reference}`, report ID/version footer, provider links, licence status and OSM print attribution |
| 6 Optional | Not started; requires a product decision | Feedback, public API v2, declared-price overlay |

Sections 1–4.3 describe the audit **as found, before implementation**;
they remain as the rationale for the plan. §4.4 records the implementation state
on 2026-10-10, and §5 rates the current panel. Open issues found while
implementing are in §11.

## 1. Summary (as found)

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
   These include terrain, land cover, night lights, parcel-intersected flood
   and landslide, parcel-level subsidence, coastal erosion, heritage and
   landscape constraints, OSM building footprints, civic addresses and
   per-building solar. Some related data exists at another resolution (for
   example, comune-level flood and landslide indicators). Some upstream
   subpackages exist in `aecs4u_stats` (`anncsu`, `egms`, `raster`, `pvgis`,
   `pv_potential`); others (DEM, CORINE, VIIRS) do not.
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
   export. Effort estimates and dependencies are separated in §8.

## 2. Method and evidence (as found)

| Source | What was done | Limits |
|---|---|---|
| Zornade, live | Authenticated session on 2026-10-09. Walked zoom 6 to 19 and opened two parcels in central Rome (`H501A048600.E` and a neighbour). Read the rendered text of every tab and scrolled each section. Recorded which backend calls the page makes. | One municipality, urban. Response bodies were not read, so field names and payloads are inferred from rendered text and from the existing reference document. Mobile layout and logged-out behaviour not examined. No form values were changed. |
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

## 3. What Zornade's parcel modal contains (Observed, as found)

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

## 4. State of the land-registry parcel panel at audit (Verified, as found)

### 4.1 Three layers, unevenly connected

| Layer | State |
|---|---|
| **Data and API** | `_build_parcel_enrichment` assembles the payload used by both the synchronous service helper and the async parcel-details route. Populated blocks include `basic`, `cadastral`, `population`, `demographics`, `economics`, `buildings` (SISTER), `opendata`, `pvp` and `valuation`; availability depends on the parcel and source stores. The remaining names in `_PARCEL_DETAIL_BLOCKS` are initialized as unavailable and are not populated by this builder: `address`, `addresses`, `risk`, `subsidence`, `terrain`, `land_cover`, `land_use`, `valuation_history`, `coastal_erosion`, `cultural_heritage`, `solar`, `poi`, `nightlights`. Separate endpoints exist for OMI quotes, history, at-point, estimate, income, risks, POIs, census, fires and bulletin. A forced local panel rebuild/repeat sample measured PostgreSQL cold and hit paths; deployed-role and duplicate-reference acceptance remain open. |
| **Legacy panel** (`/map-legacy`) | `parcel-enrichment.js` (about 1,100 lines) renders municipality, buildings, OpenData, PVP, OMI with typology and state selectors and a local range preview, server estimate, OMI history chart, income with benchmark, census indicators, crime, risks, POIs, fires, bulletin, provenance chips and API-response previews. |
| **Primary panel** (`/map`) | `renderParcelPanel` in `map-v2.js`: hero, two KPIs (area, sheet), a five-row table, then one `<details>` per block listing up to five scalar key/value pairs, then "All attributes". 360 px floating card; bottom sheet under the mobile breakpoint. Actions: purchase query, save, adjacent parcels, copy link, clear, open analysis (legacy), print report (legacy). |

### 4.2 Consequences

- Clicking a parcel on `/map` gives less information than clicking it on
  `/map-legacy`. Users reach the OMI estimator only by leaving the primary map.
- The `risk` and `address` labels in `blockLabels` do not render from the read
  model because those blocks are not populated by its builder. The legacy risk
  card instead calls the separate comune-level risks endpoint.
- Object-valued fields are dropped (`typeof value !== 'object'`), which removes
  the OMI quote list, income brackets and building records from the primary view.
- `docs/PROTECTION_CONSTRAINTS_CONTRACT.md` is a proposed integration contract,
  not evidence that the endpoint has shipped. It specifies
  `GET /api/v1/enrichment/parcel/constraints/{reference}?id={polygon id}`; the
  route and `PROTECTION_AREA_TABLE` symbol are absent from the current tree. The
  endpoint is parcel-specific and requires the polygon ID because references
  can identify more than one feature. Confirm whether to implement the contract
  after its upstream tables are published or supersede it (see §9).
- At audit there was no shared renderer module. The migration made `/map` the
  canonical parcel panel; extending both panel implementations would have kept
  two sources of UI behavior.
- Existing contract tests pin both pages by reading the JS and templates
  (`test_map_v2_contract.py` 27 tests, `test_parcel_click_frontend_contract.py`,
  `test_omi_estimator_frontend_contract.py`, `test_parcel_report_frontend_contract.py`).
  Refactors must update them deliberately.

### 4.3 Upstream capability (`aecs4u_stats`, Verified by listing)

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

### 4.4 State after implementation slices on 2026-10-10

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
  section endpoints. `/map` downloads a server-rendered PDF from the parcel
  read model; `/map-legacy` retains its browser print flow.
- The Data tab reports parcel read-model block coverage only. It marks partial
  blocks as partial, includes source and dataset version when present, and says
  that independently loaded panel sections can have data even when their
  corresponding read-model block is empty. The block `coverage` field now uses
  the shared `full` / `partial` / `unavailable` vocabulary; the legacy
  `coverage_status` alias carries the same values. A section-level 404 is
  presented as no matching data, while 503, network and 5xx failures are
  presented as temporary and retain retry. The UI does not infer geographic
  non-coverage or a failed determination when a source provides no such state.
- `POST /api/v1/enrichment/parcel/report/{reference}` is the direct-map export:
  it adds bounded text, state and provenance snapshots captured from registered
  sections to the A4 PDF. `GET /api/v1/enrichment/parcel/report/{reference}?id=...`
  remains the server-only fallback and does not include sections loaded from
  independent endpoints. Both paths include a unique report ID, available
  dataset/model versions and an attribution page. Static report labels follow
  the request locale. The report verifies that the reference-keyed read model
  matches the selected canonical polygon before attaching enrichment. Missing
  licence or release metadata is shown as unreported rather than inferred.
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
- The coastal-erosion source is selected as ISPRA's 2006–2020 shoreline-change
  layer, whose documented classification uses a 5 m threshold and whose RNDT
  metadata states CC BY 4.0. The upstream query and local store remain a Phase 3
  dependency; this historical layer must not be described as a current forecast.
- Safety and quality-of-life routes prefer the local province/BES results and
  can fall back to country-level relocation indicators when those have no
  matching rows. The response and panel label that fallback as country scope
  and state that it does not describe the province or parcel. Demographic
  routes can use the municipality population series, including the available
  age split, and identify it as municipality scope. These fallbacks improve
  availability without changing the geographic meaning of the source values;
  schema and coverage still require review against a provisioned production
  store.

## 5. Feature matrix

*The Zornade and Legacy columns reflect the 2026-10-09 audit. The Primary
column reflects the implementation state on 2026-10-10.*

Rating: ✅ equivalent or better · 🟡 data or code exists, product surface
incomplete · ❌ missing · — comparison not assessed. "Primary" is `/map`;
"Legacy" is `/map-legacy`.

### 5.1 Shell and interaction

| # | Capability | Zornade | Backend | Primary | Legacy | Rating |
|---|---|---|---|---|---|---|
| S1 | Panel form factor | Centred sheet, about 720 px, one scroll | n/a | Side sheet, 380 to 540 px, docked right; bottom sheet at tablet and mobile widths | Sidebar-style panel | ✅ |
| S2 | Sticky header with actions | Yes | n/a | Fixed header exposes accessible Copy link, Download PDF and Close actions; purchase, save, adjacent, clear and legacy actions remain in the fixed bottom bar | Partial | ✅ |
| S3 | Stat strip (area, buildings, risk, price) | Yes | Data partly present | Five-cell strip: area, sheet, SISTER record count, OMI sale price, seismic zone. The SISTER count is not a physical footprint count; no composite risk ring | No | 🟡 |
| S4 | Section navigation (tabs, scroll-spy) | Yes | n/a | Tab bar with scroll-spy, tab-fragment restoration, and direct card links using `#sezione-{id}` | No | ✅ |
| S5 | Card with icon, count, source line | Yes | Shared envelope has a `source` field; values may be null | Per-card footer: source, dataset and model version, match method, resolution, benchmarks; icon and count badge | Footnotes | ✅ |
| S6 | Progressive loading with skeletons | Yes | Local forced rebuild p95 10.580 s; PostgreSQL repeat-hit median 15 ms, p95 55 ms | Per-section skeletons; sections load lazily and independently; 503 failures can be retried; 404 sources show unavailable without retry. The first nearby-PVP request now returns while its shared snapshot warms; the underlying national view load remains expensive | Per-card loading | ✅ |
| S7 | Deep link to a selected parcel | `?parcel=<id>` | `/parcel/by-reference` | ✅ `?parcel=<reference>` | ✅ | ✅ |
| S8 | Share, save | Favourite, share | `/saved-parcels` | ✅ Copy link, Save | ✅ | ✅ |
| S9 | Printable or PDF report | Yes | read-model blocks and version metadata | Direct-map PDF includes every registered section's captured text, state and available provenance, plus the server read model; report ID, version footer and source register | ✅ browser print, A4 dossier | ✅ |
| S10 | Mobile layout | Not examined | n/a | Bottom sheet; actions in one scrolling row (checked at 390 px) | n/a | ✅ |
| S11 | Shortlist with lifecycle, notes, tags | No (favourite only) | ✅ | ✅ | ✅ | ✅ ahead |

### 5.2 Value tab

| # | Capability | Zornade | Backend | Primary | Legacy | Rating |
|---|---|---|---|---|---|---|
| V1 | Cadastral identity (sheet, section, comune code) | Yes | ✅ `cadastral` | ✅ five-row table | ✅ | ✅ |
| V2 | OMI zone for the parcel | Yes | ✅ at-point, spatial join | Detected zone is shown, flagged when it has no quotes | ✅ | ✅ |
| V3 | Quote table by type and state | Yes | ✅ `/omi/quotes` | Collapsible table lists all current municipality quotes by zone, type and condition, with sale/rent bands and derived metrics; estimator selector remains capped at 80 valid sale rows | ✅ | ✅ |
| V4 | Estimate with range and reliability label | Yes | ✅ `POST /omi/estimate`, versioned | Range preview, then server-verified calculation with model and dataset version | ✅ range, preview, server calc | ✅ |
| V5 | Estimator inputs | Area, type, rooms, baths, condition, floor, lift, parking, outdoor, constraints | Typology, state, area only | ❌ | Typology, state, area | 🟡 Expand only inputs that change the result |
| V6 | OMI history chart | 22 semesters | ✅ `/omi/history` | SVG min to max band with mean line, up to 24 semesters | ✅ up to 24 | ✅ |
| V7 | Trend deltas (6 m, 1, 3, 5, 10 y) | Yes | ✅ Versioned history deltas, same type/state, with compared periods | ✅ 6-month to 10-year rows under the chart | ❌ | ✅ |
| V8 | Rental yield | Yes | ✅ Annualized rent midpoint ÷ sale midpoint | ✅ Gross yield, with costs/taxes caveat | ❌ | ✅ |
| V9 | Zone percentile within comune | Yes | ✅ Midrank of per-zone median sale midpoints for the same type/state; requires at least 5 comparable zones | ✅ Percentile and comparable-zone count, or an insufficient-coverage message | ❌ | ✅ |
| V10 | Declared price override | Yes | Needs storage | ❌ | ❌ | ❌ Defer (see §9) |
| V11 | Modelled-value labelling, confidence, versions | Short reliability label | ✅ envelope | Chips on some blocks | ✅ | ✅ ahead |
| V12 | Benchmarks beside values | Partial | ✅ envelope | Benchmarks on OMI quotes, income and census density | ✅ density, income, OMI | ✅ |
| V13 | Auction records (PVP) | No | ✅ `pvp` block | Section with listings, capped at 8, http(s) links only | ✅ card | ✅ |
| V14 | Cadastral purchase workflow | No | ✅ | ✅ button | No | ✅ ahead |

### 5.3 Property tab

| # | Capability | Zornade | Backend | Primary | Legacy | Rating |
|---|---|---|---|---|---|---|
| P1 | Building count, footprint, coverage | OSM footprints | SISTER records only; no parcel-footprint query (`[aecs4u-stats#29](https://github.com/aecs4u/aecs4u-stats/issues/29)` remains open) | SISTER overlay and button | Buildings card (SISTER) | 🟡 Different source |
| P2 | Cadastral building records, categories | No | ✅ SISTER, OpenData | Buildings and a dedicated OpenData card render their respective read-model records; OpenData store readiness is unverified | ✅ | ✅ |
| P3 | Land cover (CORINE) | Yes | ❌ no store (`[aecs4u-stats#32](https://github.com/aecs4u/aecs4u-stats/issues/32)` remains open) | ❌ | ❌ | ❌ |
| P4 | Urban land use | Only in functional urban areas | ❌ | ❌ | ❌ | ❌ Low value |
| P5 | Addresses | Yes (ANNCSU) | SISTER `visura_properties.address`; exact municipality/sheet/parcel match, capped and de-duplicated | Dedicated SISTER section plus a partial read-model block; both cache-dependent | ❌ | 🟡 Partial: no geocoded or complete address coverage |

### 5.4 Energy tab

| # | Capability | Zornade | Backend | Primary | Legacy | Rating |
|---|---|---|---|---|---|---|
| E1 | Municipal PV aggregates | No | ✅ `mp.pv_*` in context, map layer `solar-potential` | Energy tab shows municipality output/capacity/viability aggregates with available version and update metadata; clearly not parcel or rooftop estimates | No | ✅ |
| E2 | Per-building yield and viability | Yes | ❌ no per-roof model (OSM roof attributes are mostly absent, per `pv_potential.py`) | ❌ | ❌ | ❌ |
| E3 | Cash flow, payback, NPV, LCOE | Yes | ❌ (no economics API yet; `[aecs4u-stats#31](https://github.com/aecs4u/aecs4u-stats/issues/31)` remains open) | ❌ | ❌ | ❌ |
| E4 | Custom system simulator | Yes | ❌ | ❌ | ❌ | ❌ Later |

### 5.5 Territory tab

| # | Capability | Zornade | Backend | Primary | Legacy | Rating |
|---|---|---|---|---|---|---|
| T1 | Seismic zone | Zone and PGA | ✅ DPC comune zone + INGV MPS04 nearest native grid | Comune-level zone and parcel-centroid PGA (10% exceedance probability in 50 years); PGA is explicitly a nearest-grid estimate with no interpolation. The MPS04 store is absent on this review host. | ✅ | 🟡 |
| T2 | Flood and landslide | Per parcel, worst class | ✅ ISPRA national PAI/PGRA polygons; parcel-polygon intersection | Per-parcel intersected area and highest class, plus separately labelled comune shares | ✅ comune level | ✅ |
| T3 | Subsidence | Per parcel | ✅ `aecs4u_stats.egms`, bounded 100 m grid-cell bbox query | Intersecting cells summarized by area-weighted velocity and acceleration, movement-class area and coverage percentage; partial-query state shown | Layer only | ✅ |
| T4 | Composite risk score | Ring in header | ❌ (formula and shared API are not implemented; `[aecs4u-stats#34](https://github.com/aecs4u/aecs4u-stats/issues/34)` remains open) | ❌ | ❌ | ❌ |
| T5 | Terrain (elevation, slope, aspect, roughness) | Yes | ❌ no DEM subpackage (`[aecs4u-stats#28](https://github.com/aecs4u/aecs4u-stats/issues/28)` remains open) | ❌ | ❌ | ❌ |
| T6 | Coastal erosion | Within 1 km of coast | ISPRA `Dinamica_Litoranea_2006_2020` shoreline-change layer identified; no upstream query contract yet | ❌ | ❌ | ❌ Low priority; show the 2006–2020 class and never treat an unmatched segment as no erosion |
| T7 | Heritage and landscape constraints | Heritage only | Contract only; no app route or source tables/query. Upstream work depends on [`aecs4u-stats#48`](https://github.com/aecs4u/aecs4u-stats/issues/48), still open | ❌ | ❌ | ❌ |
| T8 | Live fire and criticality bulletin | Map overlay | ✅ | Bulletin and fires sections plus map layers | ✅ cards | ✅ |

### 5.6 Context tab

| # | Capability | Zornade | Backend | Primary | Legacy | Rating |
|---|---|---|---|---|---|---|
| C1 | Income | CAP level | ✅ comune level, brackets | Income section with benchmark | ✅ with benchmark | ✅ |
| C2 | Income distribution chart | Yes | ✅ brackets | Bracket bars in the income section | ❌ | ✅ |
| C3 | Gini, price-to-income | Yes | ✅ Versioned grouped-data Gini and selected-area price/value divided by mean taxpayer income | ✅ Both shown with assumptions and household-affordability caveat | ❌ | ✅ |
| C4 | Night lights | Yes | ❌ | ❌ | ❌ | ❌ Low priority |
| C5 | Census section indicators | Yes | ✅ section level | Census section with ratios; modelled values are flagged | ✅ indicators | ✅ |
| C6 | Age pyramid, gender split | Yes | ✅ Census 2021 `p2`/`p3` totals and `p14`–`p29`, `p30`–`p45`, `p67`–`p82` age/sex fields | Section-scoped male/female totals and a 16-band age pyramid when the complete field set is present; not parcel-level population | ❌ | ✅ |
| C7 | POIs | Yes | ✅ `/pois`; `poi` block empty | POI counts by category within 1 km | ✅ | ✅ |
| C8 | Modelled population | No | ✅ `population` block, labelled | Shown in the census section with the modelled-value note | ✅ | ✅ |

### 5.7 Community and platform

| # | Capability | Zornade | Ours | Disposition |
|---|---|---|---|---|
| X1 | XP, badges, leaderboard | Yes | No | **Not adopted.** SRS §2.2 and §14 |
| X2 | User contribution to correct data | Yes | No | **Deferred.** SRS §14 needs a moderation and validation path first |
| X3 | Public API with keys | Yes | Internal API | Tracked separately (gap analysis item 17). Not part of this plan |
| X4 | Guided tour | Yes | No | Defer; outside parcel-panel parity |

## 6. Where land-registry is ahead, and should stay ahead

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

## 7. Design recommendations

### 7.1 Panel architecture

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

### 7.2 Backend

- Keep the comune-level risk values sourced from `/risks` and request MPS04 PGA
  separately at the parcel centroid through `/mps04/pga`; label its nearest
  native-grid match and 10%-in-50-years probability. The current address card
  uses a cadastral-key match to cached SISTER property records, so it is
  incomplete and not geocoded. ANNCSU remains street-level; broader coverage
  needs a geocoded source or upstream parcel join.
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

### 7.3 Derived metrics: definitions to document before building

| Metric | Inputs | Notes |
|---|---|---|
| Gross rental yield (`omi-gross-rental-yield-v1`) | Selected OMI rent and sale bands, same type and state | Rent midpoint × 12 ÷ sale midpoint; gross of costs and taxes |
| Trend deltas (`omi-semester-trend-v1`) | OMI history for the zone and selected type/state | Compare latest sale midpoint with the closest available semester at or before 1, 2, 6, 10 and 20 semesters back; return actual periods and omit unavailable horizons |
| Zone percentile (`omi-zone-percentile-midrank-v1`) | Latest OMI quotes for all zones in the comune, same type and state | One median sale midpoint per zone; midrank percentile and comparable-zone count. Suppress the percentile below five comparable zones |
| Price-to-income (`omi-mef-price-income-v1`) | Selected area's OMI sale midpoint value ÷ comune mean taxable income per taxpayer | Uses the user-entered/selected surface as area. This is years of one mean taxpayer's annual taxable income, not household affordability |
| Gini estimate (`mef-grouped-gini-v1`) | MEF frequencies across all eight income brackets | Grouped Lorenz trapezoid using bracket midpoints; assumes €0 for the ≤0 bracket and €150,000 for the >€120k bracket; within-bracket inequality is not observed |
| Composite risk score | Seismic zone, flood and landslide percentages or per-parcel classes, subsidence class | Do not invent weights silently: publish the formula and version in the envelope, and keep issue 02's dependency on `aecs4u-stats#34` in mind |

## 8. Development plan

Effort estimates are for one developer and include tests and documentation;
they exclude review, deployment and upstream data-pipeline work. Treat the
figures as planning ranges until the Phase 0 measurements and upstream contracts
are checked. Phase 0 and 1 establish the baseline and primary panel; later work
can be reordered where dependencies allow.

### Source inventory to include in the plan

The availability values below preserve the requested starting inventory. They
describe data availability for this parcel-panel plan; they do not by themselves
mean that a provider adapter or panel section is absent. Where an adapter or
section already exists, the action is to validate source coverage and provenance
on a provisioned host. Resolve any source marked “to be selected” before adding
data, including its coverage, version and reuse terms.

| Panel data | Starting availability | Source / store | Planned action |
|---|---|---|---|
| Main address | Not available | `aecs4u-stats` PostgreSQL `sister.v_sister_property_by_cadastral_parcel.address` | Query the PostgreSQL SISTER view with exact municipality, province, sheet, parcel and section matching; label coverage as partial and non-geocoded. |
| Risks | Not available | DPC municipal seismic zone; INGV MPS04; ISPRA PAI/PGRA mosaics | Validate each source independently and preserve its spatial scope. Add a composite only through the documented formula in `aecs4u-stats#34`. |
| Subsidence | Not available | Copernicus EGMS 100 m grid via `aecs4u_stats.egms` | Verify the provisioned store and parcel-intersection coverage; keep the overlap-weighted summary and partial-coverage state. |
| Terrain | Not available | INGV TINItaly DEM, planned under `aecs4u-stats#28` | Build the upstream DEM query and zonal statistics before adding the panel block. |
| Buildings | Not available | `aecs4u-stats` PostgreSQL `sister.v_sister_property_by_cadastral_parcel` (`category`, `cadastral_class`, consistency and income) | Query the PostgreSQL SISTER view with exact cadastral matching. Keep these records distinct from physical footprints, which remain gated on `aecs4u-stats#29`. |
| Land cover | Not available | CORINE, planned under `aecs4u-stats#32` | Add the upstream parcel lookup and report classification year/version and coverage. |
| OMI history | Not available | Agenzia delle Entrate OMI via `aecs4u_stats.omi` | Validate the OMI store on the target host and retain semester history, selected typology/state and dataset provenance. |
| Coastal erosion | Not available | ISPRA `Linea di Costa 2020 v2.0`, `Dinamica_Litoranea_2006_2020`; CC BY 4.0 in RNDT metadata | Add the bounded upstream query in [issue draft 14](github_issues/zornade_map_enrichment/14-coastal-erosion-ispra-source.md). Report the 2006–2020 class, 5 m classification threshold, source vintage and mapped-segment coverage; no match is not evidence of no erosion. |
| Cultural heritage | Not available | Planned protection-area source/query under `aecs4u-stats#48`; source coverage and reuse terms remain unverified | Complete the upstream inventory and terms review, then implement the polygon-ID contract and explicit coverage states. |
| Points of interest | Not available | OpenStreetMap via `aecs4u_stats.osm` | Validate the configured POI store and parcel-centered results; surface source and search radius with the existing POI section. |
| Night lights | Not available | VIIRS night-light product; acquisition/source contract to be selected | Add a distinct night-lights dataset and spatial summary; do not treat the VIIRS fire-hotspot feed as night-light coverage. |
| Cadastral OpenData | Not available | OpenData PostgreSQL `cadastral_queries` | Verify the external database connection and representative cadastral matches; retain query time and match method in provenance. |
| PVP auctions | Not available | PVP modelview PostgreSQL | Verify the external database connection and parcel matching; retain auction records and source provenance. |

### Phase 0 — Alignment and baseline — **local PostgreSQL cold/repeat paths measured; duplicate-reference and production-role acceptance remain open** (about 0.5 week)

- Decisions recorded (§9). The constraints contract now states that the
  land-registry side is not implemented and points to Phase 3; the July gap
  analysis points here.
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

### Phase 1 — Primary panel shell and parity with legacy — **mostly done** (about 2 to 3 weeks)

| Task | Files | Status |
|---|---|---|
| Section registry and typed renderers for `/map` | `static/parcel-panel.js`, `static/parcel-panel-core.js`, `map-v2.js` | Done. Analysis hand-off remains on `/map-legacy`; the report action downloads the server PDF |
| Side sheet, sticky header actions, stat strip, tab bar | both `map_v2.html` templates, `map-v2.css` | Done. Copy link, PDF and Close stay in the fixed header; visibility after section scrolling is browser-checked |
| OMI card: selectors, estimate, history chart and quote table | panel sections, `/omi/*` | Done (zone, type and state selector, local preview, server estimate, chart, and all current municipality quote rows). The estimator selector is capped at 80 valid sale quotes; the full table also includes rent-only rows |
| Income, census, POIs, risks, bulletin, PVP, fires, safety, indicators | panel sections | Done |
| Provenance footer and modelled-value note | `blockMetadataHtml` in the core module | Done where the source supplies metadata |
| Skeleton, error and unavailable states | panel code | Done. A 503 shows a retry; a 404 shows an unavailable message without one |
| Tab bar with scroll-spy | panel code | Done. Explicit tab selection remains active through smooth navigation while lazy sections load and change the scroll position |
| Tab fragment navigation (`#valore`) | panel code | Done. Tab fragments restore on panel open and respond to back/forward navigation |
| Direct link to an individual section card | panel code | Done. `#sezione-{id}` restores and loads the selected card; the URL tracks the active card while scrolling |
| Collapsible section cards (MAP-FR-031) | `static/parcel-panel.js`, `map-v2.css` | Done. Native disclosures open by default; browser smoke test covers close/reopen |
| Source/resolution-aware risk and address | `stats_service.py`, `routers/enrichment.py`, panel | Partial. Municipality risk and parcel-centroid PGA use separate sources; MPS04 is not provisioned on this host. SISTER addresses are included in the read model and loaded by the panel through a dedicated section endpoint; both depend on cached property records, and live address coverage is unverified |
| i18n (it, en) | `translations/*` | Italian catalog covers the primary map templates and every literal `tr()` string in the direct-map and parcel-panel scripts; `tests/test_i18n_catalog.py` guards that coverage. The catalog loads from `.po`; English falls back to source strings. Targeted copy edits are recorded in §11; independent native proofread remains |
| Backend support | `stats_service.py`, `routers/enrichment.py`, `models.py` | Valuation availability now reflects content; `/api/v1/enrichment/status` runs in a thread and is cached for 60 s, and reports MPS04, ISPRA mosaic and EGMS store readiness with typed unsupported, not-built and probe-failure reasons |
| Tests | `tests/js/`, `tests/test_parcel_panel_js.py`, `tests/test_i18n_catalog.py`, `tests/browser/test_parcel_panel_smoke.py`, `tests/test_parcel_report.py`, updated `test_map_v2_contract.py` | Browser smoke coverage includes independent lazy loading, 503 retry isolation, OMI estimation, tab scroll-spy and mobile layout; other browser cases cover address, uncovered-province, EGMS, census, energy, PDF and panel interactions. The catalog contract covers every literal translation call in the primary map scripts. See §11 for recorded runs and mock-data limits. |

**Remaining before Phase 1 closes:** proofread the Italian wording, compare
details p95 with a controlled same-source and same-response-shape measurement, and check the panel
against data from a fully provisioned host. SISTER-backed address records
are capped at ten unique strings and matched by cadastral municipality, sheet
and parcel; coverage depends on a cached SISTER property row. Municipality,
census, income, risk and address coverage still need representative
production-data review.

ANNCSU on this host provides street records and civic-number counts, not
geocoded civic points or a parcel join. The address card therefore uses the
separate SISTER `visura_properties.address` field and clearly states that the
result is cache-dependent and is not a complete geocoded address register.

**Done when:** the primary panel covers the existing-data sections available on
`/map-legacy`, sections are navigable and collapsible, no section failure blanks
the panel, and frontend contracts pass or have an explicit reason for change.
Current state: section coverage, navigation, collapsibility and failure
isolation work in fixture/browser checks; provisioned-host acceptance remains
open, and full legacy parity is not yet claimed.

### Phase 2 — Derived metrics from data already exposed (about 1 to 2 weeks)

**Implementation and fixed-fixture tests done:** the backend and panel calculate
and display all five metrics in §7.3 with model-version fields. At the
verification checkpoint recorded in §11, a focused run passed 69 Python tests
across async PostgreSQL adapters, parcel fallback and block assembly, reports,
derived metrics, map contracts and panel contracts; the Node panel suite
passed. The full browser suite passed all 11 cases, including parcel-wide EGMS
rendering and PDF export. A stale map
contract that expected a nonexistent
`municipality-profiles` layer was corrected to assert the catalog's actual
`admin-substitute` role. Provisioned MEF/OMI review and Italian-copy review
remain.

- Server-side computation of yield, trend deltas, zone percentile, price-to-income
  and Gini, each with a `model_version` and documented inputs (§7.3).
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

### Phase 3 — Per-parcel geospatial blocks (estimate after upstream readiness)

Each block is a vertical slice: upstream query/data contract, `stats_service`
adapter, envelope entry, panel section and tests. The upstream package and data
builds are outside this repository and are not included in the effort estimates
above; estimate those separately. Hazard polygons, SISTER addresses and EGMS
intersections are implemented; their provisioned-data checks remain. The table
retains those slices for acceptance traceability, then lists remaining work in
proposed priority order. Implement gated app-side work after its upstream query
is released and representative coverage is available.

**Upstream gate check (2026-10-10):** GitHub issue metadata reconfirmed [`#28 DEM`](https://github.com/aecs4u/aecs4u-stats/issues/28), [`#29 building footprints`](https://github.com/aecs4u/aecs4u-stats/issues/29), [`#31 solar economics`](https://github.com/aecs4u/aecs4u-stats/issues/31), [`#32 CORINE`](https://github.com/aecs4u/aecs4u-stats/issues/32), [`#34 composite risk`](https://github.com/aecs4u/aecs4u-stats/issues/34), and [`#48 protection constraints`](https://github.com/aecs4u/aecs4u-stats/issues/48) are open. The installed package has no corresponding query contract for DEM (#28), parcel footprints (#29), CORINE (#32) or protection constraints (#48); the solar economics API (#31) and shared composite-risk formula/API (#34) are also outstanding. The latest #48 comment describes an uncommitted SITAP acquisition path covering a selected decree-polygon layer, not a complete Articles 136/157/142 inventory; Vincoli in Rete is discovery-only pending a stable bulk interface and confirmed reuse terms, and Catalogo Generale is not treated as binding data. The issue still lacks the published stats schema, import, spatial queries, and unknown-coverage behavior. Do not treat the acquisition work as an available upstream contract or infer that an uncovered parcel has no constraint.

**Coastal-erosion source selection (2026-10-10):** ISPRA's national `Linea di
Costa 2020 v2.0` dataset exposes the `Dinamica_Litoranea_2006_2020` line layer.
The ISPRA indicator defines erosion as shoreline retreat over 5 m between
2006 and 2020, seaward advance over 5 m, and smaller changes as stable. RNDT
metadata states CC BY 4.0 and 5 m positional accuracy; its lineage says the
2020 characterization used mostly Google Maps imagery from 2017–2020. This is
a suitable source for a historical coastal-change section, not a current erosion
forecast. The query contract and local/cache-backed ingestion are still absent
from `aecs4u-stats`; see [issue draft 14](github_issues/zornade_map_enrichment/14-coastal-erosion-ispra-source.md).

| Order | Block | Upstream state | Work |
|---|---|---|---|
| 1 | Protection constraints (landscape, heritage) | Proposed contract; tables depend on `aecs4u-stats#48` | Implement the full contract, including the required polygon `id`, coverage states and readiness gating after upstream tables and query ship; current route and layer symbols are absent |
| 2 | Per-parcel flood and landslide | ISPRA PAI/PGRA polygons available through `hazards.ispra_mosaics` | App-side polygon intersections now report affected area and highest class; verify dataset coverage and candidate-cap behavior on provisioned data |
| 3 | Addresses | SISTER `visura_properties.address` is linked to cadastral municipality/sheet/parcel; ANNCSU remains street-level without point coordinates | Implemented first slice: exact cadastral lookup returns at most 10 de-duplicated addresses. Verify cache coverage; use a geocoded address source or upstream join for complete spatial coverage |
| 4 | Subsidence per parcel | `egms` provides a classified 100 m grid-cell bbox query; layer already served | Implemented: exact parcel intersections, area-weighted velocity and acceleration, movement-class areas, covered-area percentage and a 5,000-candidate partial state |
| 5 | Terrain | No query; `aecs4u-stats#28` open | New DEM subpackage (INGV TINItaly) with zonal statistics |
| 6 | Land cover | No query; `aecs4u-stats#32` open | CORINE subpackage |
| 7 | Building footprints (OSM) | No parcel query; `aecs4u-stats#29` open | Coverage and count; keep SISTER as the cadastral source and label the two clearly |
| 8 | Composite risk score | Needs `aecs4u-stats#34`, open | Use the shared documented formula and gauge; do not duplicate or invent a per-app weighting |
| 9 | Night lights | None | VIIRS subpackage; lowest priority |
| 10 | Coastal erosion | ISPRA 2006–2020 shoreline-change layer is identified; CC BY 4.0 metadata; upstream query and store absent | Add a local, bounded query for mapped shoreline segments within 1 km of the parcel; preserve the 2006–2020 vintage and treat unmatched coverage as unknown |

**Done when**, per block: the envelope states source, version, resolution and
match method; empty and uncovered states are distinct; a test covers a parcel
with data, a parcel without, and a store that is down.

#### Local store provisioning and verification

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

EGMS is landed directly by the property-scraper `zornade_rischio_subsidenza`
job as an indexed DuckDB table, with no separate stats import. If the scraper
and application do not use the same default store, configure `EGMS_DB_PATH` for
both processes.

After provisioning, fetch `/api/v1/enrichment/status` after its 60-second cache
expires and require `hazards_mps04`, `hazards_ispra_mosaics`, and
`egms_subsidence` to report `available: true` and `reason: null`. Then verify
representative parcel results: a built store proves host readiness, not local
coverage or a successful spatial match.

### Phase 4 — Energy (about 2 to 3 weeks after upstream readiness)

**Municipal aggregate slice implemented:** the Energy tab reads the existing
`solar` block from the parcel read model. It shows available municipality-wide
building, capacity, annual-output and viability figures with the source's data
version and update date when exposed. The section states that these aggregates
are not estimates for the selected parcel, building, or roof.

**Remaining:**

- The parcel-level estimate depends on a documented footprint or other
  defensible parcel-level area input. Do not infer usable roof area from parcel
  area. If no suitable footprint source is available, defer the estimate or
  label a separately justified proxy rather than presenting it as building
  potential.
- Once its area input and assumptions are validated, compute the parcel-level
  estimate using `aecs4u_stats.pv_potential` and `pvgis`, labelled modelled with
  its assumptions (MAP-FR-054).
- Add cash flow, payback, NPV and LCOE with every assumption visible and
  editable (MAP-FR-055): installation cost per kWp, tariffs, discount rate,
  degradation, horizon. This depends on `aecs4u-stats#31`; the 2–3 week
  land-registry estimate starts after the upstream calculation is available.
  Issue 06's acceptance criteria apply.
- Per-roof orientation and tilt are not available from OSM (verified by the
  `pv_potential.py` design note). Do not display a compass or per-building
  viability unless a roof model with a documented source exists; state that the
  estimate is parcel-level.
- Household self-consumption modelling is out of scope until the hourly model
  exists.

### Phase 5 — Report and export (about 1 to 2 weeks)

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

### Phase 6 — Optional, only after a product decision

- Data-quality feedback channel (SRS §14: needs moderation first).
- Public API v2 for parcel blocks (separate document).
- Declared-price overlay on the estimate (needs storage, privacy review and a
  rule for how it affects shared views).

### Summary schedule

The previously stated total of 11–13 developer-weeks is not supported by the
scope as written: Phase 3 includes unestimated upstream data work, and Phase 4
depends on a footprint input that is not yet available in the parcel block. Keep
the app-side ranges for Phases 0–2 and 4–5 provisional, and set a project total
only after Phase 3's upstream slices and the energy input are scoped.

### Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Upstream stores not built on a given host | Sections empty in production | Show an explicit host/store availability state; include the source in `/api/v1/enrichment/status`; document build steps |
| Read-model rows stale after new blocks | New sections empty until refresh | Bump the payload fingerprint/version and define refresh/backfill behavior |
| Panel renders slowly on dense urban parcels | Poor perceived performance | Lazy sections, response-size budget and bounded source queries; measure cold-build and warm-cache latency separately |
| Frontend contract tests pin current markup | Churn in Phase 1 | Update tests with the change; keep selectors for the legacy page stable |
| Modelled values read as facts | Misleading users, legal exposure | Mandatory envelope, badge and disclaimer; no energy or valuation coefficient without evidence |
| Licence and attribution gaps | Source terms are unclear to users | Record each source's licence and release date in the data inventory and attribution page; populate envelope metadata when known |
| Scope creep toward Zornade's social features | Distraction | Held out by SRS §14 unless the product direction changes |

### Success measures

- Section coverage on the sample set from Phase 0: share of parcels with each
  block populated, reported per comune.
- Primary panel shows at least every legacy section (Phase 1 gate).
- `parcel/details` p95 latency at or below the Phase 0 baseline plus 20 percent
  while blocks multiply; lazy sections should keep first render unchanged.
- Zero sections that fail the whole panel when a store is down (tested).
- Every numeric card names its source and data vintage/version when known; each
  modelled value also states its method and assumptions.

## 9. Decisions and recommended defaults

1. **Constraints route.** The contract is proposed and the route is not in the
   tree. Recommended: implement it after `aecs4u-stats#48` publishes the tables,
   following the full contract, including the required polygon `id` and explicit
   coverage states. Revise the contract if the upstream schema changes.
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

## 10. Notes on existing documents

- `ZORNADE_GAP_ANALYSIS.md` is dated 2026-07 with later edits. Its 2026-10-09
  note now points here and clarifies that row 2 describes the legacy panel; keep
  the broader status matrix aligned as later phases ship.
- `docs/github_issues/zornade_map_enrichment/02` to `08` remain accurate as
  upstream-dependent issues. Map them to Phase 3 slices above; issue 01 (vector
  tiles) is done.
- `MAP_SRS.md` §13 reports v1.2 requirements as met "for current blocks". The
  primary panel exposes and collapses the currently available OMI, income,
  demographic, POI and risk sections; MAP-FR-031 is recorded as met for those
  sections. Production source coverage still needs provisioned-host review.

## 11. Open issues found during implementation

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
independent blocking database calls used to run serially. The async request
path now runs them concurrently in worker threads and passes both results into
the builder; synchronous callers keep the existing builder behavior. This
removes the avoidable serial wait measured in that profile, but no post-change
endpoint timing has been captured. A fresh sample is still needed to establish
the actual latency change and compare matched parcels, response shape and
source coverage. The attempted isolated-server recheck did not produce a
sample: the sandboxed worker could not reach its configured database host, and
the baseline's initial one-shot health check ran before application startup
completed. The collector now polls `/health` for up to 60 seconds by default
before sampling; a database-backed latency recheck still requires a worker that
can reach its configured data sources.

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
   open.** A refresh-first 20-parcel `view=panel` run recorded 20 PostgreSQL
   rebuilds (median 5.168 s, p95 10.580 s) followed by 20 PostgreSQL hits
   (median 15 ms, p95 55 ms). All 40 successful responses preserved the
   requested feature ID. This verifies local cache read and write behavior;
   it does not establish Neon production performance or privilege scope. The
   configured `postgres`
   role is a superuser, and the operator reports that Cloud Run uses the same
   username, so these grants do not establish least-privilege access. The
   GCP secret could not be independently inspected, and Neon, the operator's
   likely production target, is not yet configured or verified. The earlier
   The configured local PostGIS source returned canonical feature IDs for all
   20 sampled parcels, but no duplicate-reference group appeared, so cache
   isolation for collisions remains unverified.
   The baseline collector now preserves `(reference, feature ID)` identities
   and reports references that resolve to multiple features, but a new sample
   must find an actual collision before duplicate-reference cache isolation is
   accepted. A prior `view=panel` sample measured the application-side SQLite
   fallback: 14 cold calls (median 6.461 s, p95 8.492 s) and 26 local cache
   hits (median 0.010 s, p95 0.026 s). Non-refresh
   requests try PostgreSQL first for both views; after a miss or unavailable
   read, only `view=panel` checks the bounded SQLite cache. Cold builds attempt
   PostgreSQL persistence, with SQLite persistence as the compact panel
   fallback when that write does not succeed. `view=full` has no SQLite cache
   fallback.
2. **Store availability differs by host and capture.** The Phase 0 sample had
   three populated blocks; the post-implementation sample had six
   (`basic`, `cadastral`, `demographics`, `economics`, `population`,
   `valuation`) on all 20 parcels. Coverage and specific source correctness
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
6. **Details latency target is not established.** Phase 1 targets a p95 no more
   than 20% above the 6.783 s Phase 0 cold-build p95 (8.140 s). The full
   post-implementation sample measured 12.930 s; the compact `view=panel`
   sample measured 8.492 s across only 14 cold calls, with six first calls
   already warm. The latest refresh-first panel sample measured 10.580 s p95
   across 20 forced builds, 2.440 s above the threshold. All these samples
   differ in response shape or available blocks: the original baseline returned
   three blocks in the full view, while the latest returned seven in the compact
   view. This is a measured latency risk, but not a matched acceptance result.
   The cold-build profile identified serial OpenData/SISTER and PVP database
   calls, and the async path now starts them concurrently; the latest controlled
   p95 predates this change. Capture a fresh sample and compare the same parcels,
   view and source availability. The browser suite also does not time the
   under-300 ms identity target.
7. **Use a no-reload process for benchmarks.** The repository's reload-mode
   server restarts on file writes and can briefly fail health checks. The
   complete post-implementation sample used a fresh no-reload server on port
   8012; the earlier port-8011 endpoint became unresponsive during a run that
   was discarded.
8. **Parcel hazard and EGMS queries cap candidates at 5,000.** Both responses
   mark capped results partial. Provisioned-host review should measure how
   often each cap is reached and verify source coverage and reported overlap.

## Appendix A. Zornade backend calls seen while opening a parcel (Observed)

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
| `rest/v1/saved_parcels` | Favourite state |

## Appendix B. Mapping Zornade sections to land-registry blocks

| Zornade section | Envelope block | Source in this repo | Status |
|---|---|---|---|
| Catastale, Superficie | `basic`, `cadastral` | Cadastral store, ISTAT | ✅ |
| Indirizzi | `address`, `addresses` | `aecs4u-stats.sister.v_sister_property_by_cadastral_parcel.address`; ANNCSU street records remain unjoined | Dedicated primary-panel section plus a partial read-model block; cadastral match is source-dependent and not geocoded |
| Valutazione, OMI | `valuation` | `aecs4u_stats.omi` | Primary selector, estimate, yield and percentile ✅ |
| Storico OMI | `valuation_history` | `/omi/history` | Primary chart and versioned trend rows ✅ |
| Edifici | `buildings` | `aecs4u-stats.sister.v_sister_property_by_cadastral_parcel` | Different source |
| Suolo | `land_cover`, `land_use` | none | ❌ |
| Fotovoltaico | `solar` | `solar.solar_potential_comuni`, `serving.municipality_profile` | Municipality-wide aggregates in the Energy tab; no parcel/roof estimate ✅ |
| Terreno | `terrain` | none | ❌ |
| Rischi | `risk`, `subsidence`, `coastal_erosion` | ISPRA polygon mosaics, IdroGEO, EGMS | Parcel hazard and EGMS cell intersections; read-model block empty |
| Vincoli | `cultural_heritage` | `spatial.protection_area` (planned) | Contract only |
| Economia | `economics`, `nightlights` | `mef`, OMI | Income, grouped Gini and price-to-income ✅; night lights ❌ |
| Demografia, popolazione | `demographics`, `population` | `census`, `sezioni_urbane`, `raster` | Census indicators plus section-scoped sex split and age pyramid ✅ |
| POI | `poi` | `osm` | Empty block, endpoint ✅ |
