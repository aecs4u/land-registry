## Summary
Toggleable overlay of MiBAC vincoli (once available from `aecs4u_stats`),
following the same pattern as the seismic/hazard and Civil Protection
bulletin overlays already shipped.

## Scope
- `stats_service.py`: consume the new vincoli query function.
- New endpoint.
- `enrichment-layers.js` toggle + legend, matching the bulletin/fires layer
  pattern (`toggleBulletinLayer`/`BULLETIN_LEVELS`) already in
  `static/enrichment-layers.js`.

## Acceptance Criteria
- Layer toggles on/off independently of the other overlays.
- Constraint polygons render with a legend explaining the constraint type.

## Dependencies
- aecs4u/aecs4u-stats#30 (vincoli subpackage — must land first)

## Notes
See `docs/ZORNADE_GAP_ANALYSIS.md` §2 item 16, §4 Phase 4.5, and §5 (data
source row "MiBAC Vincoli in Rete"). Postponed — backlog item, not
scheduled.
