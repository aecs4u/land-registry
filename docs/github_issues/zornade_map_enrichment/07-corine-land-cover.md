## Summary
Show the majority CORINE 2018 land-cover class per parcel (once available
from `aecs4u_stats`) for land-use context.

## Scope
- `stats_service.py`: consume the new land-cover query function.
- New endpoint.
- Parcel panel card (short label + icon).

## Acceptance Criteria
- Correct class shown for a parcel with a known, unambiguous land cover.

## Dependencies
- aecs4u/aecs4u-stats#32 (CORINE subpackage — must land first)

## Notes
See `docs/ZORNADE_GAP_ANALYSIS.md` §2 item 13 and §4 Phase 2.4. Postponed —
backlog item, not scheduled.
