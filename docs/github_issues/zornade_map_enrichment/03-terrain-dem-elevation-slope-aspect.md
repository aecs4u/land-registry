## Summary
Surface per-parcel elevation/slope/aspect (once available from
`aecs4u_stats`) in the parcel panel — relevant to buildability, drainage,
and solar-exposure context.

## Scope
- `stats_service.py`: consume the new terrain query function.
- New `/api/v1/enrichment/terrain/{...}` endpoint.
- Parcel panel card showing elevation/slope/aspect.

## Acceptance Criteria
- Correct values shown for a parcel with a known DEM tile installed.
- Graceful "unavailable" state when the upstream store isn't built on this
  host.

## Dependencies
- `aecs4u/aecs4u-stats#28` tracks the upstream TINITALY subpackage. The regional importer and query implementation is applied in the sibling working tree, but still needs review, merge, and release before this app can use it.

## Notes
The app-side parcel API, lazy panel card, source catalog and Typst dossier
section are wired locally. Once the upstream package is released, install a
regional raster store and verify known parcels, no-data parcels, small-parcel
fallbacks, coverage, and provenance. See `docs/ZORNADE_GAP_ANALYSIS.md` §2
item 10, §4 Phase 2.3, and §5 (source row "Terrain").
