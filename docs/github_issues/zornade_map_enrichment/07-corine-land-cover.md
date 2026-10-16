## Summary
Show the majority CORINE 2018 land-cover class per parcel from `aecs4u_stats`
for broad land-use context.

## Implementation status

The local app tree now has a dedicated parcel endpoint, lazy panel card,
source-catalog entry and Typst dossier section. The upstream
`aecs4u_stats.corine` importer/query is applied in the sibling working tree but
is not committed or released. No regional store is provisioned, so live values
remain unavailable.

## Scope
- `stats_service.py`: consume the upstream regional status and area-weighted
  geometry query while preserving source, version, coverage and method.
- `GET /api/v1/enrichment/land-cover/{national_reference}`: serve the CORINE
  block and preserve the selected canonical feature ID when supplied.
- Parcel panel card (short label + icon), lazy loaded in the Territory tab.
- Include the same source block in the printable parcel dossier.

## Acceptance Criteria
- Polygon parcels select the dominant class by intersection area in EPSG:3035
  and report the class distribution, mapped area and parcel coverage.
- Missing stores, missing geometry and no matching polygon remain explicit
  unavailable/unknown states; none is represented as class code 0.
- Expose the CLC release, match method and 25 ha / 100 m mapping limits.
- Verify representative parcels in each provisioned region, including partial
  coverage and no-match cases.

## Dependencies
- aecs4u/aecs4u-stats#32 (CORINE subpackage — must be committed/released and
  regional stores imported before live values can be served)

## Notes
See `docs/ZORNADE_GAP_ANALYSIS.md` §2 item 13, Appendix A.5.3 and §4 Phase 2.
