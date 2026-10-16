## Summary
Show building count, total footprint m², and coverage % for a parcel (once
available from `aecs4u_stats`) — concrete, visual, answers "how much of this
parcel is built."

## Scope
- `stats_service.py`: consume the new buildings query function.
- New `/api/v1/enrichment/building-footprints/{...}` endpoint (separate from
  SISTER cadastral building records).
- Parcel panel card (count / m² / coverage %).
- Typst dossier values and source-register provenance.

## Source decision
- Use OSM building polygons from Geofabrik's five daily Italian macro-region
  PBF extracts, then filter them against the exact ISTAT region boundary.
- Store regional DuckDB files in EPSG:3035 and use parcel intersections with
  a union area so overlaps do not inflate coverage.
- Microsoft Global Building Footprints remains a possible comparison source,
  but its local Italian completeness needs assessment before relying on it.
- Show OSM attribution and ODbL 1.0 terms. State clearly that OSM is
  community-mapped and that footprint coverage does not establish completeness.

## Acceptance Criteria
- Correct count/m²/coverage % shown for a parcel with known buildings.
- Reasonable response time for dense urban parcels.

## Dependencies
- aecs4u/aecs4u-stats#29 (buildings subpackage — must land first)

## Notes
See `docs/ZORNADE_GAP_ANALYSIS.md` §2 item 15 and §4 Phase 4.4. The consumer
route, lazy panel card and dossier adapter are wired in the land-registry
working tree. The shared importer and query are applied in the sibling
`aecs4u-stats` working tree but remain unreleased; a provisioned regional-store
runtime sample is still required.
