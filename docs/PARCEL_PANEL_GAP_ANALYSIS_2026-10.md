# Parcel detail panel: feature and gap analysis, and development plan

*Date: 2026-10-09. Scope: the parcel detail experience that opens when a parcel is
clicked on the primary `/map` page, compared with the equivalent modal in
Zornade (app.zornade.com).*

This document **extends** [`ZORNADE_GAP_ANALYSIS.md`](ZORNADE_GAP_ANALYSIS.md)
(July 2026, whole-product scope) and [`MAP_SRS.md`](MAP_SRS.md) (requirements
and conformance). It does not repeat their matrices. It re-measures the parcel
panel alone against the current code, records what has changed since those
documents, and turns the result into a phased plan. Data-source details and
licences for Zornade's blocks are in
[`zornade-cadastral-parcel-reference.md`](zornade-cadastral-parcel-reference.md).

## 1. Summary

1. **For data we already have, the main gap is presentation.** The backend
   assembles a parcel read model with a shared block envelope
   (`_build_parcel_enrichment`, called by the async details route at
   `/api/v1/enrichment/parcel/details/{ref}`); source and version metadata are
   populated where available, not for every field. The legacy page
   (`/map-legacy`, `parcel-enrichment.js`) already renders OMI estimation and
   history, income, census, risks, POIs, fires and the DPC bulletin. The primary
   `/map` panel (`renderParcelPanel` in `map-v2.js`) exposes only a small subset:
   it prints at most five scalar fields per block inside a 360 px card.
2. **The primary panel has labels for two blocks its read model does not fill.**
   `risk` and `address` fall back to unavailable envelopes. The legacy risk card
   obtains comune-level data from `/api/v1/enrichment/risks/{istat_code}`, but
   the primary panel does not call that endpoint and shows no hazard detail.
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

## 2. Method and evidence

| Source | What was done | Limits |
|---|---|---|
| Zornade, live | Authenticated session on 2026-10-09. Walked zoom 6 to 19 and opened two parcels in central Rome (`H501A048600.E` and a neighbour). Read the rendered text of every tab and scrolled each section. Recorded which backend calls the page makes. | One municipality, urban. Response bodies were not read, so field names and payloads are inferred from rendered text and from the existing reference document. Mobile layout and logged-out behaviour not examined. No form values were changed. |
| land-registry, code | Read the primary and legacy panel code, enrichment router and service, map-layer catalog, SRS, July gap analysis, issue drafts and protection-constraints contract. The `aecs4u_stats` package list is an environment snapshot. | Static reading, not a running instance. Test status was not re-run. The package version and whether a given upstream store is built on a given host were not checked. |

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

## 3. What Zornade's parcel modal contains (Observed)

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

## 4. Current state of the land-registry parcel panel (Verified)

### 4.1 Three layers, unevenly connected

| Layer | State |
|---|---|
| **Data and API** | `_build_parcel_enrichment` assembles the payload used by both the synchronous service helper and the async parcel-details route. Populated blocks include `basic`, `cadastral`, `population`, `demographics`, `economics`, `buildings` (SISTER), `opendata`, `pvp` and `valuation`; availability depends on the parcel and source stores. The remaining names in `_PARCEL_DETAIL_BLOCKS` are initialized as unavailable and are not populated by this builder: `address`, `addresses`, `risk`, `subsidence`, `terrain`, `land_cover`, `land_use`, `valuation_history`, `coastal_erosion`, `cultural_heritage`, `solar`, `poi`, `nightlights`. Separate endpoints exist for OMI quotes, history, at-point, estimate, income, risks, POIs, census, fires and bulletin. A warm read-model cache hit is one indexed lookup in `aecs4u-stats` PostgreSQL; a miss builds from source stores and writes the result. |
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
- There is no shared renderer module. The migration should make `/map` the
  canonical parcel panel; extending both panel implementations would preserve
  two sources of UI behavior.
- Existing contract tests pin both pages by reading the JS and templates
  (`test_map_v2_contract.py` 27 tests, `test_parcel_click_frontend_contract.py`,
  `test_omi_estimator_frontend_contract.py`, `test_parcel_report_frontend_contract.py`).
  Refactors must update them deliberately.

### 4.3 Upstream capability (`aecs4u_stats`, Verified by listing)

Present: `anncsu`, `cadastral`, `cap`, `census`, `egms`, `ghsl`, `hazards`,
`istat`, `mef`, `omi`, `osm`, `pvgis`, `pv_potential`, `raster` (population
rasters only), `sezioni_urbane`, `wsf`, `zornade_parity`. Absent: DEM terrain,
CORINE, VIIRS night lights, per-parcel flood and landslide intersection,
cultural constraints, per-building solar economics. The July rule stands:
datasets are ingested as `aecs4u_stats` subpackages and consumed through
`stats_service.py`, not ETL'd inside this repository.

## 5. Feature matrix

Rating: ✅ equivalent or better · 🟡 data or code exists, product surface
incomplete · ❌ missing · — comparison not assessed. "Primary" is `/map`;
"Legacy" is `/map-legacy`.

### 5.1 Shell and interaction

| # | Capability | Zornade | Backend | Primary | Legacy | Rating |
|---|---|---|---|---|---|---|
| S1 | Panel form factor | Centred sheet, about 720 px, one scroll | n/a | 360 px floating card | Sidebar-style panel | 🟡 |
| S2 | Sticky header with actions | Yes | n/a | Actions below content, far from the title | Partial | 🟡 |
| S3 | Stat strip (area, buildings, risk, price) | Yes | Data partly present | Two KPIs | No | 🟡 |
| S4 | Section navigation (tabs, scroll-spy) | Yes | n/a | No | No | ❌ |
| S5 | Card with icon, count, source line | Yes | Shared envelope has a `source` field; values may be null | Source shown as chip, not as consistent footer | Footnotes | 🟡 |
| S6 | Progressive loading with skeletons | Yes | Warm cache hit is one indexed read; cold build uses source lookups | Spinner text, then one render | Per-card loading | 🟡 |
| S7 | Deep link to a selected parcel | `?parcel=<id>` | `/parcel/by-reference` | ✅ `?parcel=<reference>` | ✅ | ✅ |
| S8 | Share, save | Favourite, share | `/saved-parcels` | ✅ Copy link, Save | ✅ | ✅ |
| S9 | Printable or PDF report | Yes | n/a | Via legacy only | ✅ browser print, A4 dossier | 🟡 |
| S10 | Mobile layout | Not examined | n/a | Bottom sheet at breakpoint | n/a | — |
| S11 | Shortlist with lifecycle, notes, tags | No (favourite only) | ✅ | ✅ | ✅ | ✅ ahead |

### 5.2 Value tab

| # | Capability | Zornade | Backend | Primary | Legacy | Rating |
|---|---|---|---|---|---|---|
| V1 | Cadastral identity (sheet, section, comune code) | Yes | ✅ `cadastral` | ✅ five-row table | ✅ | ✅ |
| V2 | OMI zone for the parcel | Yes | ✅ at-point, spatial join | Block is available, but nested zone and quote data are dropped | ✅ | 🟡 |
| V3 | Quote table by type and state | Yes | ✅ `/omi/quotes` | ❌ dropped (objects) | ✅ | 🟡 |
| V4 | Estimate with range and reliability label | Yes | ✅ `POST /omi/estimate`, versioned | ❌ | ✅ range, preview, server calc | 🟡 |
| V5 | Estimator inputs | Area, type, rooms, baths, condition, floor, lift, parking, outdoor, constraints | Typology, state, area only | ❌ | Typology, state, area | 🟡 Expand only inputs that change the result |
| V6 | OMI history chart | 22 semesters | ✅ `/omi/history` | ❌ | ✅ up to 24 | 🟡 |
| V7 | Trend deltas (6 m, 1, 3, 5, 10 y) | Yes | Derivable from history | ❌ | ❌ | ❌ |
| V8 | Rental yield | Yes | Derivable from sale and rent bands | ❌ | ❌ | ❌ |
| V9 | Zone percentile within comune | Yes | Derivable from `/omi/quotes` | ❌ | ❌ | ❌ |
| V10 | Declared price override | Yes | Needs storage | ❌ | ❌ | ❌ Defer (see §9) |
| V11 | Modelled-value labelling, confidence, versions | Short reliability label | ✅ envelope | Chips on some blocks | ✅ | ✅ ahead |
| V12 | Benchmarks beside values | Partial | ✅ envelope | Inconsistent | ✅ density, income, OMI | 🟡 |
| V13 | Auction records (PVP) | No | ✅ `pvp` block | Via map layer, not in panel body | ✅ card | 🟡 ahead |
| V14 | Cadastral purchase workflow | No | ✅ | ✅ button | No | ✅ ahead |

### 5.3 Property tab

| # | Capability | Zornade | Backend | Primary | Legacy | Rating |
|---|---|---|---|---|---|---|
| P1 | Building count, footprint, coverage | OSM footprints | SISTER records only; no OSM footprint block | SISTER overlay and button | Buildings card (SISTER) | 🟡 Different source |
| P2 | Cadastral building records, categories | No | ✅ SISTER, OpenData | Overlay and button; records are not shown in panel | ✅ | 🟡 |
| P3 | Land cover (CORINE) | Yes | ❌ no store | ❌ | ❌ | ❌ |
| P4 | Urban land use | Only in functional urban areas | ❌ | ❌ | ❌ | ❌ Low value |
| P5 | Addresses | Yes (ANNCSU) | `aecs4u_stats.anncsu` exists; `address` block empty | ❌ | ❌ | 🟡 |

### 5.4 Energy tab

| # | Capability | Zornade | Backend | Primary | Legacy | Rating |
|---|---|---|---|---|---|---|
| E1 | Municipal PV aggregates | No | ✅ `mp.pv_*` in context, map layer `solar-potential` | Layer only | No | 🟡 |
| E2 | Per-building yield and viability | Yes | ❌ no per-roof model (OSM roof attributes are mostly absent, per `pv_potential.py`) | ❌ | ❌ | ❌ |
| E3 | Cash flow, payback, NPV, LCOE | Yes | ❌ (issue 06; economics not in `aecs4u_stats.pvgis`) | ❌ | ❌ | ❌ |
| E4 | Custom system simulator | Yes | ❌ | ❌ | ❌ | ❌ Later |

### 5.5 Territory tab

| # | Capability | Zornade | Backend | Primary | Legacy | Rating |
|---|---|---|---|---|---|---|
| T1 | Seismic zone | Zone and PGA | ✅ DPC, comune level | ❌ `risk` block empty | ✅ | 🟡 |
| T2 | Flood and landslide | Per parcel, worst class | ✅ ISPRA IdroGEO, comune percentages only | ❌ | ✅ comune level | 🟡 |
| T3 | Subsidence | Per parcel | Map layer `surface-subsidence`; `aecs4u_stats.egms` exists | Layer only | No | 🟡 |
| T4 | Composite risk score | Ring in header | ❌ (issue 02, blocked upstream) | ❌ | ❌ | ❌ |
| T5 | Terrain (elevation, slope, aspect, roughness) | Yes | ❌ no DEM subpackage (issue 03) | ❌ | ❌ | ❌ |
| T6 | Coastal erosion | Within 1 km of coast | ❌ | ❌ | ❌ | ❌ Low priority |
| T7 | Heritage and landscape constraints | Heritage only | Proposed contract; route not in tree | ❌ | ❌ | 🟡 |
| T8 | Live fire and criticality bulletin | Map overlay | ✅ | ✅ layers | ✅ cards | ✅ |

### 5.6 Context tab

| # | Capability | Zornade | Backend | Primary | Legacy | Rating |
|---|---|---|---|---|---|---|
| C1 | Income | CAP level | ✅ comune level, brackets | ❌ objects dropped | ✅ with benchmark | 🟡 |
| C2 | Income distribution chart | Yes | ✅ brackets | ❌ | ❌ | ❌ |
| C3 | Gini, price-to-income | Yes | Derivable from brackets and OMI | ❌ | ❌ | ❌ |
| C4 | Night lights | Yes | ❌ | ❌ | ❌ | ❌ Low priority |
| C5 | Census section indicators | Yes | ✅ section level | Feature wrapper only; nested census properties are dropped | ✅ indicators | 🟡 |
| C6 | Age pyramid, gender split | Yes | Verify age/sex fields in the installed census schema; values are nested in the block | ❌ | ❌ | ❌ Inferred |
| C7 | POIs | Yes | ✅ `/pois`; `poi` block empty | ❌ | ✅ | 🟡 |
| C8 | Modelled population | No | ✅ `population` block, labelled | Feature wrapper only; nested population values are dropped | ✅ | 🟡 |

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
  never render missing data as zero (MAP-FR-032, MAP-FR-052). The current
  `available` boolean alone is not enough for every new block.
- Cadastral building records from SISTER, OpenData records and PVP auctions,
  plus a purchase workflow, which Zornade does not have.
- Parcel adjacency analysis, uploaded-data analysis, a lifecycle shortlist,
  rate limiting, tile cache and privacy-preserving metrics.
- Valuation estimator already returns a versioned, auditable calculation
  (`omi-area-range-v1`). Zornade's equivalent rests on a stated energy-class
  premium cap; ours should not adopt a coefficient without its own evidence.

## 7. Design recommendations

### 7.1 Panel architecture

1. **Section registry.** A single list in a new module, for example
   `static/parcel-panel/sections.js`, where each entry declares `id`, `tab`,
   `title`, `icon`, required block keys, an optional endpoint loader, and
   `render(ctx)`. Add dependency flags only where needed; map zoom should not
   gate content after a parcel has already been selected. The registry keeps
   section order, loading and error states consistent.
2. **One canonical parcel panel.** Make `/map` the only parcel-detail surface
   being extended. Keep `/map-legacy` for upload and spatial-analysis workflows
   and preserve its existing report hand-off during migration, but do not make
   parity work depend on refactoring both pages into shared renderers. Port the
   tested data transformations and endpoint behavior from
   `parcel-enrichment.js`; extract a pure helper only when both the primary
   panel and report need it.
3. **Layout.** On desktop, a right-hand sheet 480 to 560 px wide (wider than
   today, narrower than Zornade's modal, so the selected parcel stays visible
   on the map). Keep the bottom sheet under the mobile breakpoint. Sticky
   header (identity and actions) and sticky tab bar.
4. **Navigation semantics.** Implement tabs as a `nav` of buttons that scroll to
   section anchors, with `aria-current` driven by an IntersectionObserver.
   Avoid `role="tab"` unless panes are truly separate, which they are not.
5. **Loading.** Render section skeletons immediately. Keep the current parcel
   details endpoint for core identity and blocks; load heavy or optional data
   (OMI history, POIs, constraints) from their own endpoints when the section
   nears the viewport. The SRS treats selective inclusion as met through
   separate endpoints; add `?include=` to parcel details only if Phase 0
   payload and latency measurements show it is needed.
6. **Charts.** The repository has no charting library on `/map`. Use small
   hand-written SVG components (band bar, ring, history line with min/max band,
   stacked bars, population pyramid). They are short, theme with CSS variables
   in light and dark mode, and avoid a dependency. Reassess only if more than
   six chart types accumulate.
7. **Provenance footer on every card.** One component renders source, dataset
   version, model version, spatial resolution and a modelled/estimated badge
   from the envelope. This replaces ad-hoc chips and covers MAP-FR-052 to 054
   uniformly.
8. **Internationalisation and themes.** All strings through `tr()` and the
   `.po` catalogue (Italian and English). Add new tokens to the theme overrides
   so dark mode stays consistent.

### 7.2 Backend

- Fill `risk` and `address` first (§8, Phase 1): they already have sources.
- New blocks follow one recipe: upstream `aecs4u_stats` subpackage, a query
  function, a `stats_service` adapter with a hard timeout, an envelope entry in
  `get_parcel_enrichment`, then a registry entry in the panel.
- Compute derived metrics server-side where the inputs are server data, so the
  formula is versioned (`model_version`) and testable; compute only
  interactive previews in the browser, as the OMI estimator already does.
- Keep the `serving.parcel_enrichment_read_model` PostgreSQL materialisation.
  A warm request reads one indexed JSONB row; a cache miss rebuilds the payload
  from source stores, and `refresh=true` forces that rebuild. When its payload
  shape changes, bump the read-model fingerprint/version and define how existing
  rows refresh; a database schema migration alone will not backfill JSON.

### 7.3 Derived metrics: definitions to document before building

| Metric | Inputs | Notes |
|---|---|---|
| Gross rental yield | OMI rent band, OMI sale band, same type and state | Midpoint rent × 12 ÷ midpoint sale. State the row used |
| Trend deltas | OMI history for the zone and comparable typology/state | Compare the latest semester with 1, 2, 6, 10 and 20 semesters back; show the periods, not just percentages |
| Zone percentile | Latest OMI quotes for all zones in the comune, same type and state | Define the per-zone statistic (for example, band midpoint), state N zones and omit if N is small |
| Price-to-income | Comparable residential OMI sale midpoint per m² × a stated representative area ÷ comune mean taxable income per taxpayer | Define the representative area and denominator; label as an indicative comune-level ratio, not household affordability |
| Gini estimate | MEF income-bracket frequencies | Approximation from grouped data; document assumptions for within-bracket incomes and the open-ended top bracket, and label it comune level |
| Composite risk score | Seismic zone, flood and landslide percentages or per-parcel classes, subsidence class | Do not invent weights silently: publish the formula and version in the envelope, and keep issue 02's dependency on `aecs4u-stats#34` in mind |

## 8. Development plan

Effort estimates are for one developer and include tests and documentation;
they exclude review, deployment and upstream data-pipeline work. Treat the
figures as planning ranges until the Phase 0 measurements and upstream contracts
are checked. Phase 0 and 1 establish the baseline and primary panel; later work
can be reordered where dependencies allow.

### Phase 0 — Alignment and baseline (about 0.5 week)

- Record the decisions already made in §9 and close the remaining product
  choices. Confirm the constraints contract against the upstream table plan;
  its endpoint is a dependency-gated item, not a Phase 0 implementation task.
- Keep the broader gap analysis and constraints contract aligned as work ships.
  The current gap analysis links here, and the constraints contract now states
  that its proposed land-registry implementation is not yet in the tree.
- Capture before-state: screenshot the primary and legacy panels for the same
  parcel; use `scripts/parcel_panel_baseline.py` to measure details latency,
  payload size and block coverage. Before treating it as the planned sample,
  verify or replace its default sites: the point labelled `bolzano-rurale`
  appears urban, `--per-area` counts points rather than unique parcels, and
  duplicate references are skipped. Collect at least 20 unique parcels across
  a large city, a small comune and a genuinely rural area. Record
  `read_model.cached` and report warm-cache and cold-build latency separately;
  the current collector does not yet do this.
- Use the current inventory contract in
  `tests/test_parcel_block_inventory.py` to pin populated versus unavailable
  blocks; extend its fixtures when a new block is added.

**Done when:** decisions recorded, baseline numbers stored in `docs/`, no code
change required.

### Phase 1 — Primary panel shell and parity with legacy (about 2 to 3 weeks)

| Task | Files | Notes |
|---|---|---|
| Section registry and typed renderers for `/map` | new `static/parcel-panel/*.js`, `map-v2.js` | `/map` becomes canonical; keep legacy upload/analysis and report hand-off working |
| New layout: wider sheet, sticky header and tab bar, stat strip | `map_v2.html`, `map-v2.css`, `theme_overrides/map_v2.html` | Keep bottom sheet on mobile |
| Port OMI card: quote table, typology and state selectors, estimate, history chart | panel renderers, `/omi/*` endpoints | Reuse existing estimator logic and its contract tests |
| Port income, census indicators, POIs, risks, bulletin and PVP | panel renderers and existing endpoints | Replace scalar-only display with typed sections; retain a safe fallback for unknown blocks |
| Add source/resolution-aware risk and address sections | `stats_service.py`, `routers/enrichment.py`, panel | Risk remains comune-level; ANNCSU availability and address-to-parcel matching must be confirmed before claiming parcel coverage |
| Provenance footer component and modelled badge | panel renderers | Populate only metadata supported by each source |
| Skeleton and error states per section | panel code | Explain unavailable versus not covered |
| Tab bar with scroll-spy; deep link to a section (`#valore`) | panel code | Non-breaking |
| i18n entries (it, en) | `translations/*` | Run catalogue test |
| Tests | `tests/test_map_v2_contract.py` and siblings, new browser smoke | Update pinned strings deliberately; add a Playwright smoke that opens a parcel and checks sections |

**Done when:** a parcel opened on `/map` shows at least what `/map-legacy` shows
for the same parcel; sections are navigable; no section fails the whole panel;
all existing frontend contract tests pass or are updated with reasons.

### Phase 2 — Derived metrics from data already exposed (about 1 to 2 weeks)

- Server-side computation of yield, trend deltas, zone percentile, price-to-income
  and Gini, each with a `model_version` and documented inputs (§7.3).
- Panel components: stat strip values, trend row, band bar for min/central/max,
  income distribution bars, Gini and price-to-income gauges, zone percentile
  line.
- Age pyramid and gender split only if the installed census schema provides
  suitable fields; verify names and definitions before designing the charts.
- Add valuation inputs only when the server model uses them. An input that does
  not affect the result would mislead users.
- Unit tests with fixed OMI and MEF fixtures; contract tests on the new
  fields; benchmark values labelled with year and dataset version.

**Done when:** each metric has a stated formula in the panel or its tooltip, a
test with known numbers, and a graceful "not available" path.

### Phase 3 — Per-parcel geospatial blocks (estimate after upstream readiness)

Each block is a vertical slice: upstream query/data contract, `stats_service`
adapter, envelope entry, panel section and tests. The upstream package and data
builds are outside this repository and are not included in the effort estimates
above; estimate those separately. Implement an app-side slice after its upstream
query is released and representative coverage is available. Suggested order by
value and dependency risk:

| Order | Block | Upstream state | Work |
|---|---|---|---|
| 1 | Protection constraints (landscape, heritage) | Proposed contract; tables depend on `aecs4u-stats#48` | Implement the full contract, including the required polygon `id`, coverage states and readiness gating; current route and layer symbols are absent |
| 2 | Per-parcel flood and landslide | ISPRA PAI/PGRA polygons not yet intersected per parcel | New `hazards` function: worst class by parcel geometry; replaces the comune percentage where available |
| 3 | Addresses | `anncsu` exists | Parcel-to-address join with a cap and de-duplication |
| 4 | Subsidence per parcel | `egms` exists, layer already served | Nearest point within a distance; state the distance used |
| 5 | Terrain | None | New DEM subpackage (INGV TINItaly) with zonal statistics; issue 03 |
| 6 | Land cover | None | CORINE subpackage; issue 07 |
| 7 | Building footprints (OSM) | None | Coverage and count; issue 04; keep SISTER as the cadastral source and label the two clearly |
| 8 | Composite risk score | Needs `aecs4u-stats#34` | Documented formula and gauge; issue 02 |
| 9 | Night lights | None | VIIRS subpackage; lowest priority |

**Done when**, per block: the envelope states source, version, resolution and
match method; empty and uncovered states are distinct; a test covers a parcel
with data, a parcel without, and a store that is down.

### Phase 4 — Energy (about 2 to 3 weeks)

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

- Server-rendered PDF of the parcel dossier with a report ID and the dataset and
  model versions in the footer. This is the outstanding item in SRS Phase 5 and
  gap analysis item 18.
- Build report sections from the canonical panel's data model and section
  definitions. Keep PDF-specific layout separate from the interactive map DOM.
- Attribution page for every source used, with licence and release date.

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
2. ~~**Panel form factor.**~~ **Decided 2026-10-09: side sheet** (480 to 560 px)
   that keeps the selected parcel visible on the map. No centred modal.
3. **Legacy page.** Recommended: keep `/map-legacy` for upload and analysis,
   preserve the report hand-off during migration, and stop extending its parcel
   panel after `/map` reaches parity.
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
- `MAP_SRS.md` §13 reports v1.2 requirements as met "for current blocks". That
  statement is true for the blocks that exist, but the primary panel renders only
  a subset of them. Re-audit MAP-FR-031 (panel exposes OMI, income, demographic,
  POI and risk sections) after Phase 1.

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
| Indirizzi | `address`, `addresses` | `aecs4u_stats.anncsu` | Empty block |
| Valutazione, OMI | `valuation` | `aecs4u_stats.omi` | Data ✅, primary UI ❌ |
| Storico OMI | `valuation_history` | `/omi/history` | Empty block, endpoint ✅ |
| Edifici | `buildings` | SISTER | Different source |
| Suolo | `land_cover`, `land_use` | none | ❌ |
| Fotovoltaico | `solar` | `pv_potential`, `pvgis` | Municipal only |
| Terreno | `terrain` | none | ❌ |
| Rischi | `risk`, `subsidence`, `coastal_erosion` | `hazards`, `egms` | Comune level, block empty |
| Vincoli | `cultural_heritage` | `spatial.protection_area` (planned) | Contract only |
| Economia | `economics`, `nightlights` | `mef` | Income ✅ |
| Demografia, popolazione | `demographics`, `population` | `census`, `sezioni_urbane`, `raster` | ✅ section level |
| POI | `poi` | `osm` | Empty block, endpoint ✅ |
