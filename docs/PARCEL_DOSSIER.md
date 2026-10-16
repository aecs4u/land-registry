# Parcel dossier (Typst PDF)

**Status:** implemented 2026-10-10; municipality profile, quality-of-life, municipal solar and ANNCSU civic-address sections added 2026-10-15; optional user-input PVGIS scenario added 2026-10-16. The risks and constraints section now includes schema-gated protection records and explicit unknown coverage. Replaces the deprecated ReportLab report.

## Why

The ReportLab report (`parcel_report.py`) rendered the flattened text of the
browser panel and produced about 36 pages for a single parcel. The dossier is a
structured, server-built document modelled on a competitor's parcel sheet:
cover with locator map and key figures, contents, summary, thematic sections,
source register and an optional personal appendix. A sample parcel renders in
7-8 pages, in Italian or English.

## Endpoints

| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/v1/enrichment/parcel/dossier/{national_reference}?id=<feature id>` | Returns `application/pdf`, `Content-Disposition: parcel-dossier.pdf`, `X-Report-ID` |
| `POST` | same | Optionally accepts `{"solar_scenario": {...}}` after a successful panel calculation; only validated assumptions are accepted, then PVGIS and economics are recomputed server-side. Other legacy snapshot fields are ignored. |
| `GET`/`POST` | `/api/v1/enrichment/parcel/report/{national_reference}` | **Deprecated** ReportLab report; sends `Deprecation: true` and `Link: <.../parcel/dossier/{ref}>; rel="successor-version"` |

Locale comes from the request (`detect_locale`). The signed-in user's saved-parcel
entry (status, priority, notes, tags, favourite) goes into the personal appendix
only when it exists; anonymous requests simply omit the appendix.

## Layout of the code

| File | Responsibility |
|---|---|
| `land_registry/parcel_dossier.py` | `gather_inputs` (calls the stats services), `build_dossier` (turns inputs into the template `data.json` blocks and SVG files), `render_dossier_pdf`, source register, labels (`_TEXT`, it/en) |
| `land_registry/parcel_dossier_svg.py` | Locator map, bar/range/column/band/cash-flow charts, QR code as SVG strings |
| `land_registry/documents/parcel_dossier.typ` | Layout only; all text arrives in `data.json` |
| `land_registry/routers/parcel_dossier.py` | Endpoint, adjacent-parcel outlines (4 s timeout, never fatal), saved-parcel lookup |

Rendering uses `aecs4u-documents` (`DocumentEngine(extra_templates_dir=...)`,
`render(template, variables, extra_files={...})`) on typst-py. Sections are
described as blocks (`kv`, `table`, `image`, `note`, `unavailable`), so adding a
section means producing blocks in `build_dossier`, not editing the template.

## Rules

- **Evidence first.** Missing data is shown as "n.d." or a "dato non
  disponibile" note, never as 0. An empty SISTER cache reads "no record", not
  "0 buildings".
- **Provenance.** Every section's sources feed the source register through
  `source_catalog.find_source_catalog_entry`, with licence status and dataset
  versions. Keep new sources there.
- **Protection constraints.** Read the shared protection table contract when
  provisioned. Empty results remain undetermined unless coverage contains the
  whole parcel; catalogue-only entries are labelled as non-binding.
- **No ReportLab extensions.** Do not add features to `parcel_report.py`.

## Carried over from ReportLab

Source register and licence status, dataset-version footer, parcel/profile
matching, locale-aware number formatting and income-bracket labels, and the
locator-map projection, clipping and scale-bar logic (now SVG).

## To do (future work)

Items are ordered by how much of the work is already possible with data we hold.
Anything marked "needs data" must stay out of the report, or show an
"unavailable" block, until the source exists. Never fill it with a placeholder
or 0.

### A. Ported from ReportLab, data already available

- [x] Municipal solar section: the dossier reads the municipality aggregate
  from `solar.solar_potential_comuni`, includes its dataset version and update
  time when supplied, and labels its municipality scope. These are not parcel,
  building or roof estimates.
- [x] Municipality profile: current municipality fields and available
  resident-population history from the ISTAT profile are shown with municipal
  scope and source provenance.
- [x] Quality-of-life indicators: the latest annual ISTAT BES edition is
  shown at province/NUTS3 level, with each indicator's value, unit and
  reference period. The special COVID supplement is omitted; older editions
  are not repeated in the PDF.
- [x] Exclude captured panel-section text. The dossier remains server-built;
  POST accepts only the validated inputs for the optional PV scenario, then
  recomputes PVGIS production and cash flows on the server.

### B. Competitor-sheet content, needs data or upstream work

- [x] Geocoded civic addresses. The dossier has a separate ANNCSU section with
  address components, coordinate method, municipal coordinate coverage and an
  explicit no-match caveat. The regional importer/query is implemented in the
  sibling `aecs4u-stats` working tree but remains unreleased; no regional store
  is provisioned for a runtime sample, so spatial matching remains unverified.
- [x] Building footprints and counts: the OSM footprint query is wired into
  the dossier alongside separate SISTER records, with count, clipped area and
  mapped-area share. The regional Geofabrik importer/query is applied in the
  sibling `aecs4u-stats` working tree but remains unreleased; no region store
  is provisioned for a runtime sample. OSM completeness remains unknown. The
  Ocular imagery workflow would need its own model version, imagery date and
  confidence metadata.
- [x] Terrain: the dossier consumes parcel elevation, slope, aspect and
  roughness from the TINITALY adapter, including coverage and representative
  point fallback. The importer/query remains unreleased in the sibling
  `aecs4u-stats` working tree, and no regional raster store is provisioned;
  representative values remain unverified.
- [x] Land cover: the dossier consumes CORINE class, area share, distribution
  and mapped coverage when available, with mapping-resolution caveats. The
  upstream importer/query remains unreleased and no regional store is
  provisioned, so live values remain unverified.
- [x] Optional parcel PV cash-flow and payback summary. A successful Energy
  tab calculation can be included in the dossier POST. The server recalculates
  PVGIS yield and cash flows, then prints the scenario assumptions, headline
  results, annual cash-flow table and chart with source/version metadata. It
  does not infer capacity from parcel area or require an OSM footprint. The
  shared calculator release (`aecs4u-stats#31`) remains pending; the app's
  compatibility implementation remains active for older wheels.
- [ ] Verify timeout/unavailable behavior and a rendered scenario PDF on a
  provisioned serving host; the current implementation degrades to an explicit
  unavailable note without failing the rest of the report.
- [x] Composite risk score: the dossier includes the versioned municipal
  context score when valid and states its scope and limitations. The app uses
  the shared helper when installed and a compatible local implementation for
  older wheels; the upstream release and representative live-comune review
  remain pending.
- [ ] Modelled population estimate for the parcel's surroundings (currently only
  census-section indicators and the age distribution).
- [ ] Raster basemap tiles behind the locator map (vector outline only today).
  Check basemap provider terms before embedding tiles in a PDF.

### C. Housekeeping

- [ ] Regenerate `uv.lock` (`uv lock`) after the new `aecs4u-documents` and
  `qrcode` dependencies.
- [x] Run `tests/browser/test_parcel_panel_smoke.py` in a Chromium environment;
  all 14 cases passed with system Google Chrome on 2026-10-15.
- [x] Remove the unused `createReportSnapshot` browser helper; repository
  clients use the server-built dossier GET route.
- [ ] Remove the deprecated `/parcel/report/` POST path after its compatibility
  period; keep the documented deprecation headers until then.
- [ ] Retire `parcel_report.py`, `parcel_report_graphics.py` and
  `/parcel/report/` after a deprecation period, together with their tests.
- [x] Italian strings cover the primary-map drawing and adjacency labels;
  `test_italian_catalog_covers_literal_primary_map_strings` passed on
  2026-10-15 (unrelated to the dossier).
- [ ] Review provider terms for sources shown in the register that are still
  "unreviewed" (OMI, cadastral/SISTER, Demanio, PVP, NOAA-21 FIRMS).
- [ ] Verify representative reports on provisioned data (the review host has no
  MPS04, ISPRA mosaic or EGMS stores).

## Operational notes

- `pyproject.toml` declares `aecs4u-documents>=0.4.0` (uv git source, `main`) and
  `qrcode>=7.4`. `uv.lock` has not been regenerated; run `uv lock` when a
  network-enabled uv CLI is available. The shared venv currently has
  `aecs4u-documents 0.4.0` and `qrcode 8.2`; no additional dossier runtime
  dependency is needed there.
- Lockfile follow-up was attempted on 2026-10-16. `/snap/bin/uv` is blocked by
  snap confinement in this environment. Redirecting its cache to `/tmp` lets
  uv start, but `uv lock --offline` cannot resolve the uncached
  `aecs4u-documents` Git dependency. A network-enabled lock run was not
  completed, so `uv.lock` remains unchanged.
- Fonts: the template asks for Inter, then Liberation Sans, then DejaVu Sans.
- Tests: `tests/test_parcel_dossier.py` (endpoint, deprecation headers, it/en,
  unavailable states, municipal solar scope/version and PDF rendering).
  `tests/browser/test_parcel_panel_smoke.py` passed all 14 browser cases,
  including the panel's plain-GET dossier download and map interactions. The
  fixture accepts `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` or discovers system
  Chromium/Chrome when the Playwright-managed browser is absent.
