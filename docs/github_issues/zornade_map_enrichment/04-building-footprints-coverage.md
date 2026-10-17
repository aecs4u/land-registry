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
- Stage regional DuckDB imports in EPSG:3035, then publish serving tables to
  the source-owned PostgreSQL `buildings` database and query them with
  PostGIS. Use parcel intersections with a union area so overlaps do not
  inflate coverage.
- Microsoft Global Building Footprints remains a possible comparison source,
  but its local Italian completeness needs assessment before relying on it.
- Show OSM attribution and ODbL 1.0 terms. State clearly that OSM is
  community-mapped and that footprint coverage does not establish completeness.

## Acceptance Criteria
- Correct count/m²/coverage % shown for a parcel with known buildings.
- Reasonable response time for dense urban parcels.

## PostgreSQL provisioning (2026-10-17)

Sicilia is imported into `buildings.public.r19__osm_buildings` (1,273,472
footprints, EPSG:3035), with source provenance and a GiST geometry index. The
installed app query reports the regional store ready and returned parcel
footprint metrics in a Palermo smoke query. Other regional tables have not
been provisioned; OSM footprint absence is never interpreted as complete
building coverage.

## Dependencies
- aecs4u/aecs4u-stats#29 (shared buildings importer/query and remaining
  regional provisioning)

## Notes
See `docs/ZORNADE_GAP_ANALYSIS.md` §2 item 15 and §4 Phase 4.4. The consumer
route, lazy panel card and dossier adapter are wired in the land-registry
working tree. The shared PostgreSQL reader and migration plan are installed;
the Sicilia source is provisioned. Additional regions still need importing
and parcel acceptance against representative dense and sparse areas.
