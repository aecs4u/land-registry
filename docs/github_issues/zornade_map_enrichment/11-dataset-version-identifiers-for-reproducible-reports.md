## Summary
Status: implemented for current blocks with meaningful upstream versions.

Enrichment sections render a human-readable source string via
`_sourceFootnote(data.source)`. That is enough to credit a source but not
enough to reproduce a result. Add a machine-readable dataset version alongside
it so a saved or printed parcel report can be re-derived from the exact inputs
that produced it.

## Scope
- Enrichment responses: add a `dataset_version` field per block, distinct from
  the display `source` string — e.g. an OMI archive span, a VIIRS product id, a
  census vintage, or a model version where the value is computed rather than
  looked up.
- `parcel-enrichment.js`: render it as a muted continuation of the existing
  source footnote; it is provenance, not a headline.
- Parcel report / print flow: carry the versions into the dossier so a printed
  report states what it was computed from.

## Acceptance Criteria
- Every enrichment block that has an upstream version exposes it in the API
  response and renders it in the panel footnote.
- Two reports generated from the same parcel and the same dataset versions are
  identical in their enrichment values.
- Blocks with no meaningful version render the existing source line unchanged.

## Dependencies
- Version metadata exposed per store in `aecs4u_stats`. Several stores already
  carry `source_release` in the layer catalog — reuse that field where it
  applies rather than inventing a parallel one.

## Notes
Implements the provenance clauses added to `docs/MAP_SRS.md` §6.4 in v1.2.
Prerequisite for the durable server-rendered report with a report ID noted as
missing in `docs/ZORNADE_GAP_ANALYSIS.md` §2 item 18.

The implemented block envelope now carries `dataset_version` and `model_version`.
Current populated blocks:

- cadastral/basic: parcel source release or `ADE_INSPIRE_CADASTRAL_EXTRACT`;
- population/demographics: `ISTAT_BASI_TERRITORIALI_2021` or source release;
- economics: `MEF_IRPEF_{year}`;
- valuation/OMI: explicit OMI release/period where available;
- OMI estimator: `omi-area-range-v1` plus OMI quote period.

Blocks with no meaningful upstream version intentionally render the existing
source line unchanged.

**OMI boundary provenance (2026-10-15):** the automatic zone match now carries
its boundary source, vintage, match method and spatial resolution separately
from the quote dataset version. The local OnData mirror reports vintage
`2018/2`; both parcel panels disclose that this geometry is approximate, and
the Typst dossier records the mirror and matching metadata in its source
register. The `/api/v2` catalog documents these optional fields. A PostgreSQL
zone match reports `PostGIS ST_Covers` and the polygon scope; its vintage stays
unset when the source does not provide one.

**Dossier provenance follow-up (2026-10-15):** the municipality profile now
records municipality scope and source without inventing a vintage. The
quality-of-life section records its latest annual ISTAT BES edition as
`data_vintage`, plus province/NUTS3 resolution and the municipality NUTS3 match
method. A live Civitavecchia lookup returned edition `2025` with 90 annual
indicators; the Typst renderer compiled that data into a PDF. Solar aggregates
continue to pass through their source version and update time when supplied.
