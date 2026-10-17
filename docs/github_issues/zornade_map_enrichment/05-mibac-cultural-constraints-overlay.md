## Summary
Provide landscape and cultural-heritage constraint overlays and a parcel-level
record list, using the shared protection contract in
`docs/PROTECTION_CONSTRAINTS_CONTRACT.md`.

**Implementation status:** the app now defines the required-ID parcel route,
lazy panel section, and schema-gated `landscape-constraints` and
`heritage-protections` map layers. The canonical area and coverage tables are
provisioned in PostGIS. The SITAP source database exposes the current normalized
geometry view through `postgres_fdw`; release `sitap-20261003-cc0760b0c41f`
loaded 8,452 landscape features into `spatial.protection_area`. The app's
schema-gated catalog exposes both layer IDs, and a real overlap query returned
a SITAP record. `spatial.protection_coverage` has no completeness rows, so
parcel coverage remains unknown. Vincoli in Rete still has no stable bulk
geometry release or confirmed reuse terms; Catalogo Generale remains a
non-binding reference source rather than a constraint layer.

## Scope
- Publish the agreed `spatial.protection_area` schema and source import/query in
  `aecs4u-stats`, including distinct source records and explicit release IDs.
- Provision the optional `spatial.protection_coverage` table before reporting
  that an empty result means no recorded constraint.
- [x] Verify the direct-map layer gate and a record-level overlap against the
  provisioned SITAP landscape source.
- [ ] Provision licensed cultural-heritage geometry and complete coverage
  evidence before claiming the heritage overlay is populated or a no-match is
  conclusive.

## Acceptance Criteria
- Layer toggles appear only when the expected source schema is available.
- Landscape polygons and mixed heritage polygons/points render with a legend.
- Parcel lookup returns all intersecting source records with a required feature
  ID, overlap ratios, capped results and explicit unknown/covered states.

## Dependencies
- aecs4u/aecs4u-stats#48 (review/release of the protection schema, importer,
  spatial query and coverage contract)
- aecs4u/aecs4u-stats#30 (legacy vincoli subpackage proposal, if still active)

## Notes
See `docs/ZORNADE_GAP_ANALYSIS.md` §2 items 7 and 16, Appendix A, and the
cross-repository protection contract. The current data-source blocker is
upstream publication and complete coverage; uncovered parcels remain
undetermined.
