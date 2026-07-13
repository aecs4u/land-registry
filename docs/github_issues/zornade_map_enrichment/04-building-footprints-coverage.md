## Summary
Show building count, total footprint m², and coverage % for a parcel (once
available from `aecs4u_stats`) — concrete, visual, answers "how much of this
parcel is built."

## Scope
- `stats_service.py`: consume the new buildings query function.
- New `/api/v1/enrichment/buildings/{...}` endpoint.
- Parcel panel card (count / m² / coverage %).

## Acceptance Criteria
- Correct count/m²/coverage % shown for a parcel with known buildings.
- Reasonable response time for dense urban parcels.

## Dependencies
- aecs4u/aecs4u-stats#29 (buildings subpackage — must land first)

## Notes
See `docs/ZORNADE_GAP_ANALYSIS.md` §2 item 15 and §4 Phase 4.4. Postponed —
backlog item, not scheduled.
