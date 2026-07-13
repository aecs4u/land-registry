## Summary
Once `aecs4u_stats.pvgis` exposes cash-flow/NPV/LCOE/payback economics on
top of its existing irradiance client, wire the result into the land-registry
parcel panel.

## Scope
- `stats_service.py`: consume the new solar-economics function.
- New `/api/v1/enrichment/solar/{...}` endpoint.
- Parcel panel card with headline numbers (NPV, LCOE, payback years) and the
  assumptions used visibly disclosed (install cost/kWp, panel efficiency,
  discount rate) — editable in the UI, not hidden constants.

## Acceptance Criteria
- Numbers match `aecs4u_stats.pvgis`'s own verified reference case.
- Assumptions are visible/editable in the UI.

## Dependencies
- aecs4u/aecs4u-stats#31 (cash-flow calculation — must land first)

## Notes
See `docs/ZORNADE_GAP_ANALYSIS.md` §2 item 9 and §4 Phase 4.1. Postponed —
backlog item, not scheduled.
