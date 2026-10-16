## Summary
Provide landscape and cultural-heritage constraint overlays and a parcel-level
record list, using the shared protection contract in
`docs/PROTECTION_CONSTRAINTS_CONTRACT.md`.

**Implementation status:** the app now defines the required-ID parcel route,
lazy panel section, and schema-gated `landscape-constraints` and
`heritage-protections` map layers. The catalog exposes the layers only after
the expected `spatial.protection_area` relation and columns exist. The upstream
schema, importer and regional stores are still pending, so live overlay coverage
has not been verified.

## Scope
- Publish the agreed `spatial.protection_area` schema and source import/query in
  `aecs4u-stats`, including distinct source records and explicit release IDs.
- Provision the optional `spatial.protection_coverage` table before reporting
  that an empty result means no recorded constraint.
- Verify the direct-map layer legend and record-level overlap against provisioned
  SITAP and cultural-heritage records.

## Acceptance Criteria
- Layer toggles appear only when the expected source schema is available.
- Landscape polygons and mixed heritage polygons/points render with a legend.
- Parcel lookup returns all intersecting source records with a required feature
  ID, overlap ratios, capped results and explicit unknown/covered states.

## Dependencies
- aecs4u/aecs4u-stats#48 (protection schema, importer, spatial query and coverage contract)
- aecs4u/aecs4u-stats#30 (legacy vincoli subpackage proposal, if still active)

## Notes
See `docs/ZORNADE_GAP_ANALYSIS.md` §2 items 7 and 16, Appendix A, and the
cross-repository protection contract. The current data-source blocker is
upstream publication and complete coverage; uncovered parcels remain
undetermined.
