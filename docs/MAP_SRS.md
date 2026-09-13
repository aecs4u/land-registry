# Software Requirements Specification: Cadastral Map Experience

**Status:** Implementation baseline  
**Version:** 1.2  
**Date:** 2026-09-13  
**Product:** AECS4U Land Registry  

The first implementation baseline delivers the direct MapLibre shell, catalog-
driven MVT layers, raster fallback, parcel search/selection/enrichment, URL
sharing, report hand-off, authenticated saves, and privacy-preserving map
diagnostics. Upload and drawing parity remains intentionally isolated behind
`/map-legacy`.

## 1. Purpose

This document specifies the requirements for improving the AECS4U cadastral map from a file-oriented mapping interface into a fast, clear, searchable parcel-exploration experience.

The improved map shall let a user open the application, navigate to any supported Italian municipality, discover cadastral parcels, select a parcel, inspect its data, and share or export the result. Existing upload, drawing, adjacency, and spatial-analysis workflows shall remain available.

## 2. Scope

### 2.1 In scope

- Interactive map navigation across Italy and supported cadastral coverage.
- Zoom-dependent administrative, sheet, and parcel layers.
- Cadastral search and map navigation by municipality, sheet, parcel, or stable reference.
- Parcel hover, selection, detail panel, URL state, and sharing.
- Basemap and thematic-layer management.
- Cadastral, market, risk, demographic, POI, and other available enrichment layers.
- Drawing and spatial-analysis workflows retained from the existing application.
- Responsive, accessible, and observable map behavior.
- Migration from the current Folium iframe architecture toward a direct map application.

### 2.2 Out of scope for the first release

- Editing official cadastral records.
- Legal ownership verification or title registration.
- Replacing source-data authorities or guaranteeing cadastral completeness.
- Automated property valuation or financial advice.
- Community profiles, leaderboards, or social features.
- Server-rendered PDF generation beyond the existing browser print flow.

## 3. Product goals

1. **Immediate discovery:** users can reach cadastral data without first loading a local file.
2. **Fast exploration:** map movement and zooming remain responsive while data loads progressively.
3. **Clear interaction:** users always understand what is selected, which layers are visible, and whether data is available.
4. **Useful parcel context:** selecting a parcel opens a structured detail panel with relevant enrichment data.
5. **Reliable sharing:** map position and parcel selection can be copied and restored through a URL.
6. **Safe evolution:** legacy upload and analysis workflows continue to function during migration.

## 4. Users and primary use cases

### UC-01: Explore an area

1. User opens the map.
2. User searches for a place or navigates by pan and zoom.
3. The map loads relevant boundaries and parcels automatically when coverage and zoom thresholds allow.
4. User sees a coverage or loading state when data is unavailable or still loading.

### UC-02: Find a parcel

1. User enters a municipality, sheet, parcel number, or stable cadastral reference.
2. The system validates the query and presents matching results.
3. User selects a result.
4. The map flies to the parcel, highlights it, and opens the detail panel.

### UC-03: Identify a parcel on the map

1. User moves over parcels and receives a lightweight parcel-number tooltip.
2. User clicks a parcel.
3. The selected parcel receives a persistent visual highlight.
4. The detail panel shows cadastral identity, area, source, and available enrichments.

### UC-04: Inspect thematic information

1. User opens the Layers control.
2. User enables an available thematic layer, such as OMI zones, hazards, census sections, or POIs.
3. The map displays the layer using a documented legend.
4. The detail panel or popup identifies the layer source and data date.

### UC-05: Share a map state

1. User chooses Share or Copy link.
2. The system creates a URL containing map center, zoom, and selected parcel when applicable.
3. Opening the URL restores the same map state, subject to current data availability.

### UC-06: Analyze uploaded data

1. User opens the Analysis workflow.
2. User uploads or loads cadastral files.
3. User draws, selects, exports, or analyzes geometries as supported today.
4. The workflow does not interfere with the nationwide parcel-exploration map.

## 5. Functional requirements

### 5.1 Map shell and navigation

| ID | Requirement | Priority |
|---|---|---|
| MAP-FR-001 | The system shall render one authoritative interactive map instance for the primary map page. | Must |
| MAP-FR-002 | The map shall support pan, zoom, drag, keyboard navigation, fullscreen, and geolocation where permitted. | Must |
| MAP-FR-003 | The default view shall fit Italy or restore the view from URL state. | Must |
| MAP-FR-004 | The map shall constrain navigation to a configured Italian extent without preventing valid edge-area exploration. | Should |
| MAP-FR-005 | The map shall preserve the selected parcel while changing basemaps or toggling non-destructive overlays. | Must |
| MAP-FR-006 | Map controls shall be grouped into Navigation, Search, Layers, Analysis, and Share actions. | Must |
| MAP-FR-007 | Advanced controls shall be collapsible and shall not obscure the map on desktop or mobile. | Should |

### 5.2 Basemaps and layers

| ID | Requirement | Priority |
|---|---|---|
| MAP-FR-008 | The system shall provide a quiet light basemap, dark basemap, and satellite or hybrid basemap. | Must |
| MAP-FR-009 | Only one basemap shall be visible at a time. | Must |
| MAP-FR-010 | The Layers control shall distinguish basemaps from overlays. | Must |
| MAP-FR-011 | Each overlay shall expose a user-facing name, legend, source, coverage status, and last-updated date where known. | Must |
| MAP-FR-012 | Layers shall be loaded only when enabled and within their configured zoom or coverage range. | Must |
| MAP-FR-013 | The map shall show a clear empty, loading, partial-coverage, or unavailable state for a layer. | Must |
| MAP-FR-014 | The system shall support cadastral parcels, cadastral sheets, municipalities, OMI zones, hazards, census sections, POIs, and live overlays when their data sources are available. | Should |
| MAP-FR-050 | Below the parcel zoom threshold the map shall display an administrative substitute layer, such as regions or provinces, together with an explicit affordance stating that zooming in reveals parcels. An empty map is not an acceptable below-threshold state. | Must |
| MAP-FR-051 | Live hazard overlays shall express each detection's observation time as a relative age and shall state the feed's own last-refresh time. | Should |

### 5.3 Cadastral data rendering

| ID | Requirement | Priority |
|---|---|---|
| MAP-FR-015 | Cadastral parcels shall be served through zoom-gated vector tiles or an equivalent viewport-based mechanism for the primary map. | Must |
| MAP-FR-016 | Parcel boundaries shall not be rendered at national zoom levels when doing so would create visual clutter or excessive load. | Must |
| MAP-FR-017 | Parcel labels shall appear only at an appropriate high zoom level and shall avoid unnecessary overlap where technically feasible. | Should |
| MAP-FR-018 | Parcel geometry shall use a neutral, low-opacity default style that keeps the basemap readable. | Must |
| MAP-FR-019 | The selected parcel shall have a high-contrast outline and fill that remain visible in light and dark modes. | Must |
| MAP-FR-020 | Parcel rendering shall include stable identifiers needed for selection, URL restoration, and detail lookup. | Must |
| MAP-FR-021 | The system shall preserve the existing raster/datashader fallback for datasets or environments that cannot use vector tiles. | Should |

### 5.4 Search and finder

| ID | Requirement | Priority |
|---|---|---|
| MAP-FR-022 | The system shall provide one visible search entry point. | Must |
| MAP-FR-023 | Search shall support place names and cadastral references, including municipality, sheet, parcel, and national reference when available. | Must |
| MAP-FR-024 | The system shall debounce requests and show loading, no-result, validation, and error states. | Must |
| MAP-FR-025 | Search results shall display enough context to distinguish duplicate parcel numbers, including municipality and sheet. | Must |
| MAP-FR-026 | Selecting a result shall fly to the geometry, select it, and open the detail panel. | Must |
| MAP-FR-027 | Search shall work without requiring the user to load a local cadastral file first. | Must |

### 5.5 Hover, selection, and detail panel

| ID | Requirement | Priority |
|---|---|---|
| MAP-FR-028 | Hovering a parcel shall show a lightweight tooltip containing parcel number and municipality when available. | Must |
| MAP-FR-029 | Clicking a parcel shall select exactly one parcel by default and open its detail panel. | Must |
| MAP-FR-030 | The detail panel shall show cadastral identity, area, geometry/source status, and data freshness. | Must |
| MAP-FR-031 | The detail panel shall expose available municipality, OMI, income, demographic, POI, and risk information using collapsible sections. | Should |
| MAP-FR-032 | Missing enrichment data shall be explained as unavailable or not covered; the UI shall not imply a zero value. | Must |
| MAP-FR-033 | The user shall be able to close the panel without clearing the map selection. | Should |
| MAP-FR-034 | The user shall be able to clear the selection and return to the neutral map state. | Must |
| MAP-FR-035 | The user shall be able to copy a parcel link and open the printable parcel report flow. | Should |
| MAP-FR-052 | Any enrichment value that is modelled, interpolated, or estimated rather than directly observed shall be labelled as such, and shall carry a confidence or spatial-resolution indicator where the source provides one. | Must |
| MAP-FR-053 | Where a meaningful national or regional benchmark exists for a metric, the panel shall present the parcel value alongside that benchmark so the reader can judge magnitude without prior domain knowledge. | Should |
| MAP-FR-054 | An enrichment section produced by a computational model shall disclose its input assumptions and the model and dataset versions used to derive the result. | Must |
| MAP-FR-055 | Where a model's inputs are meaningfully user-adjustable, the panel may expose a parameter simulator that recomputes the result and restates that the output is indicative and not a professional appraisal. | Could |

### 5.6 URL state and sharing

| ID | Requirement | Priority |
|---|---|---|
| MAP-FR-036 | The URL shall support map latitude, longitude, and zoom parameters. | Must |
| MAP-FR-037 | The URL shall support a stable parcel reference when a parcel is selected. | Must |
| MAP-FR-038 | URL updates shall be throttled or performed on meaningful map movement events. | Should |
| MAP-FR-039 | Invalid or unavailable URL parameters shall fail gracefully and leave the user at the nearest valid view. | Must |
| MAP-FR-040 | Copy Link shall use the Clipboard API with a visible fallback when clipboard access is unavailable. | Should |

### 5.7 Drawing and analysis compatibility

| ID | Requirement | Priority |
|---|---|---|
| MAP-FR-041 | Existing polygon, polyline, rectangle, circle, and marker drawing workflows shall remain available through Analysis tools. | Must |
| MAP-FR-042 | Drawn geometries shall remain distinguishable from official cadastral geometry. | Must |
| MAP-FR-043 | Existing selection, adjacency, save, load, and GeoJSON export behavior shall remain functional. | Must |
| MAP-FR-044 | Nationwide parcel browsing and uploaded-file analysis shall use separate state scopes and shall not overwrite one another. | Must |

### 5.8 Responsive and accessible behavior

| ID | Requirement | Priority |
|---|---|---|
| MAP-FR-045 | On mobile, the detail panel shall become a bottom sheet or full-width panel. | Must |
| MAP-FR-046 | Every control shall have an accessible name, visible focus state, and tooltip or adjacent text label. | Must |
| MAP-FR-047 | Important states shall not rely on color alone. | Must |
| MAP-FR-048 | The map shall support keyboard access to search, layers, panel actions, and selection controls. | Should |
| MAP-FR-049 | Text and controls shall meet the project accessibility contrast target in light and dark themes. | Must |

### 5.9 Parcel shortlist and workspace

A saved parcel is a working item, not a bookmark. The requirements below extend
the existing authenticated `/api/v1/saved-parcels` contract from a flat favorites
list into a shortlist a user can actually work through.

| ID | Requirement | Priority |
|---|---|---|
| MAP-FR-056 | The user shall be able to save a selected parcel to a personal shortlist keyed by its stable cadastral reference. | Should |
| MAP-FR-057 | Each shortlist entry shall carry a status drawn from a configurable lifecycle vocabulary, including at least an initial state, one or more active states, and terminal states for discarded and archived entries. | Should |
| MAP-FR-058 | Each shortlist entry shall carry a user-assigned priority. | Should |
| MAP-FR-059 | Each shortlist entry shall support free-text notes and user-defined tags; attachments are optional. | Could |
| MAP-FR-060 | The shortlist shall be filterable and sortable by status, priority, and recency, and shall support filtering to entries whose parcels currently carry an active hazard alert or criticality warning. | Should |
| MAP-FR-061 | Selecting a shortlist entry shall restore the corresponding map state and open the parcel detail panel. | Should |
| MAP-FR-062 | The shortlist shall summarise its own contents, at minimum total entries and a count per active status. | Could |

Status vocabulary and any pipeline semantics are product configuration, not map
behavior; the map layer shall not assume a fixed set of status values.

## 6. Non-functional requirements

### 6.1 Performance

- Initial map shell should become interactive within 2 seconds on a typical broadband connection, excluding third-party basemap delay.
- Panning at a supported zoom level should not trigger a full-page reload.
- Viewport parcel requests should be bounded by zoom, bounding box, and maximum feature count.
- Vector tiles should be preferred for large polygon layers; GeoJSON should be limited to identify, detail, and small viewport responses.
- Search responses should normally complete within 500 ms from the application API when the data store is warm.
- Long-running enrichment calls shall load independently and shall not block parcel identity rendering.
- The client shall deduplicate concurrent identical requests and shall cache session identity and per-user metadata for the lifetime of the page. Repeated identical fetches within a single page load are a defect, not a tuning opportunity.
- The parcel detail response shall support selective inclusion of enrichment blocks so that callers can request minimal payloads, and enrichment sections whose payload is large shall be fetched on expansion rather than on selection.

### 6.2 Reliability

- A failed basemap or enrichment request shall not remove the parcel layer or crash the map.
- The map shall recover from transient tile and API failures through bounded retries or a clear retry action.
- Empty results shall be represented as valid states, not JavaScript errors.
- The system shall support deployments where optional data stores or API keys are absent.

### 6.3 Security and privacy

- Browser-supplied layer identifiers shall be validated against an allowlist.
- SQL shall never be constructed from raw browser-supplied table names or predicates.
- Parcel and enrichment endpoints shall enforce the application’s existing authentication and authorization rules.
- Error responses shall not expose SQL, filesystem paths, credentials, or stack traces.
- Share URLs shall contain map and parcel references only; they shall not contain private user data or credentials.

### 6.4 Data quality and provenance

- Every enrichment section shall identify its source and observation period when known.
- Each enrichment block shall additionally carry a machine-readable dataset version identifier alongside its human-readable source, so that a stored or printed report can be reproduced and audited against the exact inputs that produced it.
- Modelled values shall be distinguishable from measured values in both the API response and the UI.
- Coverage limitations shall be visible to users.
- Where a national dataset excludes specific territories, those exclusions shall be named rather than left to appear as missing data.
- Geometry shall be declared with its coordinate reference system and transformed consistently for display.
- Official cadastral geometry shall not be presented as editable or authoritative ownership information.

### 6.5 Browser support

The primary map shall support the current and previous major versions of Chrome, Firefox, Safari, and Edge, with a usable fallback for browsers without advanced vector-rendering support.

## 7. Target architecture

### 7.1 Frontend

The primary map shall use a direct map page with one authoritative map object. MapLibre GL JS is the preferred implementation for vector tiles and feature-state styling; Leaflet is acceptable if it meets the same requirements.

The frontend shall separate:

- map state: center, zoom, basemap, active overlays;
- parcel state: hover, selected parcel, search result;
- analysis state: uploaded layers and drawn geometries;
- enrichment state: independent loading and error states per section.

The existing Folium page may remain as a compatibility path for upload and analysis workflows until feature parity is complete.

### 7.2 Backend

The backend shall expose:

- a map-layer catalog and health/coverage metadata;
- vector-tile endpoints or static PMTiles for large layers;
- bounded viewport GeoJSON endpoints for identify and fallback rendering;
- parcel lookup by stable reference and point/bounding box;
- search endpoints for hierarchical cadastral lookup;
- independent enrichment endpoints;
- report/share-compatible parcel detail responses.
- privacy-preserving map request diagnostics and latency buckets.

All map layer definitions shall come from a server-side allowlist such as the existing map-layer catalog.

### 7.3 Data and storage

The primary map shall use a persistent parcel store with stable identifiers and spatial indexes. In-memory GeoDataFrames shall remain limited to user-upload and analysis workflows and shall not be the source of truth for nationwide browsing.

## 8. Visual and interaction guidelines

- Use a quiet light basemap as the default.
- Limit the default layer list to the layers most users need.
- Use consistent, color-blind-safe colors for parcel selection and thematic categories.
- Use opacity before saturation for overlays so roads and parcel boundaries remain readable.
- Keep popups lightweight; use the detail panel for structured information.
- Do not show parcel labels at zoom levels where they overlap heavily.
- Keep the selected parcel visible when the detail panel is open.
- Display loading indicators close to the affected layer or detail section.
- Frame a metric against a benchmark where one exists; a raw figure such as a density or an income value means little to a reader without a reference point.
- Disclose model assumptions inline, next to the result they produced, rather than on a separate help page.
- Attribute each section at the point of use. A source line per section is more useful than a single credits block at the end of the panel.

## 9. Acceptance criteria

The first production release is acceptable when all of the following are true:

1. A user can open the map and navigate to a supported municipality without uploading a file.
2. Parcels appear automatically at the configured zoom level, with a working fallback where vector tiles are unavailable.
3. A user can search by municipality and cadastral reference and navigate to a result.
4. Clicking a parcel opens the detail panel and preserves the selected state while the user changes basemaps.
5. The URL restores map position and selected parcel where the referenced data is available.
6. Layer controls show only one basemap, clearly separate overlays, and communicate layer availability.
7. Existing drawing, adjacency, save/load, and export workflows pass regression tests.
8. The map remains usable on mobile and with keyboard navigation for the primary controls.
9. Missing optional data sources produce clear UI states rather than broken map behavior.
10. Below the parcel zoom threshold the map shows administrative geometry and a zoom-in affordance, never an empty viewport.
11. Every enrichment section in the detail panel names its source, and any modelled value is marked as modelled.
12. Performance, security, and provenance requirements in this document are verified in testing.

## 10. Delivery plan

### Phase 1: Interaction and visual cleanup

- Consolidate controls and remove low-value default clutter.
- Improve parcel styles, selection, tooltips, legend, and responsive detail panel.
- Standardize loading, empty, error, and coverage states.

### Phase 2: Search and persistent parcel browsing

- Add stable-reference and hierarchical parcel search.
- Connect search results to point/parcel lookup and URL state.
- Ensure browser-based parcel exploration is independent of uploaded-file state.

### Phase 3: Direct vector-tile map

- Add the direct MapLibre or equivalent map page.
- Serve cadastral parcels through MVT or PMTiles.
- Add zoom-gated labels, hover state, click selection, and detail-panel integration.
- Retain the Folium path for legacy analysis until migration is complete.

### Phase 4: Enrichment and operational maturity

- Add thematic overlays, legends, data freshness, and coverage metadata.
- Add favorites and durable parcel links where product authorization permits.
- Add performance monitoring, tile/API metrics, and user-facing diagnostics.

The baseline implements these Phase 4 capabilities through the catalog health
states, parcel report/save actions, and `/api/v1/map/metrics`. Provider-level
telemetry and production-wide coverage guarantees remain deployment work.

### Phase 5: Presentation, provenance depth, and shortlist

Introduced with version 1.2 and delivered. See §13 for the conformance evidence.

- Administrative geometry below the parcel zoom threshold (issue 09) — done.
- Confidence indicators on modelled values (issue 10) — done.
- Dataset version identifiers for reproducible reports (issue 11) — done. This
  unblocks the durable server-rendered report with a report ID, which remains
  outstanding (gap analysis §2 item 18).
- Benchmark framing for panel metrics (issue 12) — done.
- Parcel shortlist and workspace (issue 13) — done.

Remaining follow-on work is extension rather than implementation: populating
confidence, benchmark and dataset-version fields for blocks whose upstream does
not yet expose them.

## 11. Traceability to current project assets

| Concern | Existing project area |
|---|---|
| Folium map generation | `land_registry/map.py` |
| Primary map interaction | `land_registry/static/map-v2.js` |
| Primary map page and styles | `land_registry/templates/map_v2.html`, `land_registry/static/map-v2.css` |
| Map request diagnostics | `land_registry/map_observability.py` |
| Legacy map interaction | `land_registry/static/map.js` and `land_registry/static/folium-interface.js` |
| Layer catalog and PostGIS source | `land_registry/map_layers.py` |
| Map controls | `land_registry/map_controls.py` |
| Parcel detail and enrichments | `land_registry/static/parcel-enrichment.js` |
| Dynamic enrichment overlays | `land_registry/static/enrichment-layers.js` |
| Existing map contracts | `tests/test_map_session_fixes_contract.py`, `tests/test_visualization_performance_contract.py` |
| Primary map contract | `tests/test_map_v2_contract.py` |
| Vector-tile migration rationale | `docs/github_issues/zornade_map_enrichment/01-vector-tiles-maplibre-viewer.md` |
| Open work for v1.2 requirements | `docs/github_issues/zornade_map_enrichment/09-…` through `13-…` |
| Broader product gap analysis | `docs/ZORNADE_GAP_ANALYSIS.md` |

## 12. Decisions recorded for this implementation

- MapLibre GL JS is the primary renderer; Leaflet/Folium remains the analysis
  compatibility path.
- API-served MVT is the first delivery format because the allow-listed PostGIS
  catalog is already available; PMTiles remains a future optimization for
  national static coverage.
- Coverage is reported per layer from the catalog and health endpoint; the
  application does not claim national completeness where data is partial.
- Saved parcels use the existing authenticated `/api/v1/saved-parcels`
  contract and its configured SQLite/PostgreSQL persistence backend.
- Printable reports continue to use the existing browser print flow reached
  through `/map-legacy?parcel=...&report=1`.

## 13. Conformance status for version 1.2 requirements

All requirements added in v1.2 are implemented. Re-audited against the primary
map implementation on 2026-09-13; the evidence column records where each one
lives so the claim can be re-checked rather than taken on trust.

Several rows are scoped "met for current blocks" rather than unconditionally.
That is deliberate: the envelope and rendering are general, and a block is
covered as soon as its upstream exposes the relevant field. No further UI work
is required to extend them.

| ID | Status | Evidence or gap |
|---|---|---|
| MAP-FR-050 | Met | `geo-boundaries` and `municipality-profiles` are exposed from the catalog with the `admin-substitute` role, and `map-v2.js` auto-enables one of those layers below the parcel threshold while keeping the zoom-in affordance visible. The auto layer is released again above the handoff unless the user explicitly chose it. |
| MAP-FR-051 | Met | FIRMS detections now render each observation as a relative age in both the parcel panel and live overlay tooltip/popup. FIRMS and DPC bulletin surfaces state the feed refresh/issue time when present and explicitly mark it as not provider-declared when absent. |
| MAP-FR-052 | Met for current modelled population block | The population block is labelled "Popolazione modellata" and now carries `spatial_resolution`, `dataset_version`, and `model_version` metadata in the parcel read model. `parcel-enrichment.js` and the direct map preview render confidence or resolution chips only when those fields are present. Future modelled blocks should populate the same envelope fields. |
| MAP-FR-053 | Met for current density, income, and OMI panel metrics | The parcel panel now renders inline benchmark context for census-section density, mean taxable income, and the selected OMI sale €/m² quote. Static national benchmarks are version-labelled for census density and IRPEF income; OMI uses the median of the currently loaded comparable municipal quotes to avoid a misleading national price reference. |
| MAP-FR-054 | Met for current versioned blocks | The common parcel block envelope now carries `dataset_version` and `model_version`. Cadastral, population/demographics, economics, and valuation blocks populate meaningful versions; the OMI estimator returns `model_version` and `dataset_version`; the panel and direct map render the provenance in source footnotes. Blocks with no meaningful upstream version remain unchanged. |
| MAP-FR-055 | Met for OMI | The estimator lets the user set typology, conservation state and surface, and recomputes against a server-side calculation with a local preview. |
| MAP-FR-056 – 062 | Met | `/api/v1/saved-parcels` now behaves as a shortlist: entries carry configurable lifecycle status, priority, notes, tags, timestamps, summary counts, and active-hazard filtering. Existing records migrate to the initial status without data loss. The direct map renders a shortlist workspace with status/priority/recency filters and opens entries through the same `?parcel=<national-reference>` restoration path. |

Non-functional clauses added in v1.2:

| Clause | Status |
|---|---|
| Client request deduplication and identity caching (§6.1) | Satisfied by construction. The primary map supersedes in-flight search requests through `AbortController` and bounds every fetch with a timeout; no per-request identity refetch loop exists in this architecture. |
| Selective inclusion of enrichment blocks (§6.1) | Met structurally. Enrichment is already split across independent endpoints per block rather than returned as one composite response, so callers request only what they need. |
| Machine-readable dataset version per block (§6.4) | Met for current versioned blocks. The shared block envelope carries `dataset_version`/`model_version`, and cadastral, population/demographics, economics, valuation and OMI estimator outputs populate meaningful values where upstream versions exist. |
| Named territorial exclusions (§6.4) | Met for layers. `map_layers.py` carries per-layer `coverage` and `coverage_note`, including explicit partial-coverage counts. |
| Modelled values distinguishable from measured (§6.4) | Met for current modelled population block. Modelled population is labelled and carries confidence/resolution/version metadata; future modelled blocks should reuse the same envelope fields. |

## 14. Ideas reviewed and deliberately not adopted

Recorded from the 2026-09-13 walkthrough of app.zornade.com so the reasoning is
not re-litigated. See `docs/ZORNADE_GAP_ANALYSIS.md` for the full feature matrix.

| Idea | Disposition |
|---|---|
| Community contribution channel — user-submitted corrections for wrong boundaries, missing or demolished buildings, and land-use errors, weighted by observation difficulty | Deferred, not rejected. This is a data-quality channel rather than a social feature, so §2.2's exclusion does not settle it. It needs a moderation and validation path before it can be accepted, and it is worth revisiting only once parcel coverage is stable. |
| XP, levels, badges, streaks, and a public leaderboard | Out of scope per §2.2. Observed in production carrying a 100-user community, which suggests the mechanic does not by itself create contribution volume. |
| Database-only architecture — browser talking directly to PostgREST/PostGIS with entitlement enforced as SQL functions, and no application server | Not adopted. It removes a tier this project uses for file upload, adjacency analysis, and datashader rendering. Worth noting that it does make per-viewport parcel access an SQL concern rather than a middleware one, which is a cleaner place for it. |
| Public REST API with scoped tokens, self-service key management, and a documented per-block data catalog | Out of scope for this SRS, which covers the map experience. Tracked as item 17 in the gap analysis. |
| Bulk GIS dataset downloads and a QGIS plugin as additional distribution surfaces | Out of scope here; recorded because it shows the same enrichment store can be packaged three ways without new data work. |

## 15. Sources for this revision

Version 1.2 adds MAP-FR-050 through MAP-FR-062, the supporting non-functional
clauses, the Phase 5 delivery items, and the conformance audit in §13. The
requirements derive from a live walkthrough of a competing Italian cadastral
platform on 2026-09-13, and reflect presentation and provenance patterns rather
than any transfer of data or implementation. The conformance status derives from
reading this repository's own primary map implementation on the same date.
