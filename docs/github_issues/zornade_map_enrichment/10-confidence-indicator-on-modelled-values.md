## Summary
Status: implemented for the current modelled population block.

`parcel-enrichment.js` already labels the population card "Popolazione
modellata", which is the right instinct. Extend it: a modelled value should
also carry *how much* to trust it — a confidence percentage or the spatial
resolution the estimate was derived at.

## Scope
- `stats_service.py` / enrichment endpoints: surface the confidence or
  resolution field where the upstream model produces one (dasymetric population
  is the first case; risk scores and any interpolated raster sample follow the
  same pattern).
- `parcel-enrichment.js`: render a compact confidence chip next to the modelled
  value, plus the derivation in the section footnote
  (e.g. `worldpop_2025_30m + osm_dasymetric`).
- Where the upstream value has no confidence field, render nothing rather than
  a fabricated default.

## Acceptance Criteria
- A modelled value shows its confidence or resolution when upstream provides it.
- A modelled value with no upstream confidence renders cleanly with no chip and
  no placeholder.
- Measured values are visually distinguishable from modelled ones.

## Dependencies
- Upstream confidence fields in `aecs4u_stats`; ships incrementally per block.

## Notes
Implements `docs/MAP_SRS.md` MAP-FR-052 (Must). The labelling half exists at
`parcel-enrichment.js`; the metadata half is now carried through the common
parcel block envelope. Population currently exposes a spatial-resolution chip
(`ISTAT census section 2021`) plus dataset/model versions. Future upstream
confidence percentages should populate the same `confidence` field and render
without further UI work.
