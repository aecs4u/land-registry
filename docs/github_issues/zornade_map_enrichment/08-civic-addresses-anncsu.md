## Summary
Show civic address(es) for a parcel (once available from `aecs4u_stats`),
fulfilling the intent already noted in `docs/urban_address_requirements.md`.

## Scope
- `stats_service.py`: consume the new address query function.
- New endpoint.
- Parcel panel: address line(s) near the top of the detail panel.

## Acceptance Criteria
- Address resolves correctly for a parcel with a known civic number.
- Handles parcels with zero or multiple addresses without crashing.

## Dependencies
- aecs4u/aecs4u-stats#33 (ANNCSU subpackage — must land first)

## Notes
See `docs/ZORNADE_GAP_ANALYSIS.md` §2 item 14, §4 Phase 4.3, and
`docs/urban_address_requirements.md`. Postponed — backlog item, not
scheduled.
