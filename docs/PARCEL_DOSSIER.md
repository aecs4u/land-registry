# Parcel dossier (Typst PDF)

**Status:** implemented 2026-10-10. Replaces the deprecated ReportLab report.

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
| `POST` | same | Accepted for clients that still send a panel snapshot; the body is ignored |
| `GET`/`POST` | `/api/v1/enrichment/parcel/report/{national_reference}` | **Deprecated** ReportLab report; sends `Deprecation: true` and `Link: <.../parcel/dossier/{ref}>; rel="successor-version"` |

Locale comes from the request (`detect_locale`). The signed-in user's saved-parcel
entry (status, priority, notes, tags, favourite) goes into the personal appendix
only when it exists; anonymous requests simply omit the appendix.

## Layout of the code

| File | Responsibility |
|---|---|
| `land_registry/parcel_dossier.py` | `gather_inputs` (calls the stats services), `build_dossier` (turns inputs into the template `data.json` blocks and SVG files), `render_dossier_pdf`, source register, labels (`_TEXT`, it/en) |
| `land_registry/parcel_dossier_svg.py` | Locator map, bar/range/column/band charts, QR code as SVG strings |
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

- [ ] Municipal solar section (municipality PV aggregates from
  `solar.solar_potential_comuni` / `serving.municipality_profile`), with its
  spatial scope stated. Municipality-wide only; no parcel or roof estimate.
- [ ] Quality-of-life indicators section.
- [ ] Municipality profile section (the ReportLab report's municipality block).
- [ ] Captured panel-section text for sections the server cannot rebuild itself
  (the old POST snapshot). Decide whether this is wanted at all: the dossier
  was built to be server-only, so prefer adding a server-side block instead.

### B. Competitor-sheet content, needs data or upstream work

- [ ] Building footprints and building counts. Needs OSM footprints or the
  Ocular imagery workflow (keep workflow/model version, imagery date,
  confidence). Today the buildings section uses SISTER records only.
- [ ] Terrain: elevation and slope. Needs a DEM source and a per-parcel
  zonal-statistics query; none exists in the stack.
- [ ] Land cover (CORINE or equivalent). No source loaded.
- [ ] PV cash-flow and payback chart. Waits on a defensible footprint and on
  `aecs4u-stats#31`; state assumptions visibly if it is ever added.
- [ ] Composite risk score. Needs a documented, deterministic method; until
  then keep the per-hazard chips.
- [ ] Modelled population estimate for the parcel's surroundings (currently only
  census-section indicators and the age distribution).
- [ ] Raster basemap tiles behind the locator map (vector outline only today).
  Check basemap provider terms before embedding tiles in a PDF.

### C. Housekeeping

- [ ] Regenerate `uv.lock` (`uv lock`) after the new `aecs4u-documents` and
  `qrcode` dependencies.
- [ ] Run `tests/browser/test_parcel_panel_smoke.py` in a Chromium environment.
- [ ] Remove the unused `createReportSnapshot` helper and the legacy POST path
  once clients have moved off `/parcel/report/`.
- [ ] Retire `parcel_report.py`, `parcel_report_graphics.py` and
  `/parcel/report/` after a deprecation period, together with their tests.
- [ ] Add Italian strings for the pre-existing missing catalog keys so
  `test_italian_catalog_covers_literal_primary_map_strings` passes (drawing and
  adjacency strings; unrelated to the dossier).
- [ ] Review provider terms for sources shown in the register that are still
  "unreviewed" (OMI, cadastral/SISTER, Demanio, PVP, NOAA-21 FIRMS).
- [ ] Verify representative reports on provisioned data (the review host has no
  MPS04, ISPRA mosaic or EGMS stores).

## Operational notes

- `pyproject.toml` declares `aecs4u-documents>=0.4.0` (uv git source, `main`) and
  `qrcode>=7.4`. `uv.lock` has not been regenerated; run `uv lock`.
- Fonts: the template asks for Inter, then Liberation Sans, then DejaVu Sans.
- Tests: `tests/test_parcel_dossier.py` (endpoint, deprecation headers, it/en,
  unavailable states). `tests/browser/test_parcel_panel_smoke.py` asserts the
  panel downloads the dossier with a plain GET; it has not been run in a
  Chromium environment yet.
