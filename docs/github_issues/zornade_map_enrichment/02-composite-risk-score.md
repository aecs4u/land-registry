## Summary
Show the composite 0–100 risk score (once available from `aecs4u_stats`) as
a gauge in the parcel panel header, next to the existing Rischi card.

## Scope
- `stats_service.py`: consume the new score field from
  `/api/v1/enrichment/risks/{istat_code}` once `aecs4u_stats.hazards`
  exposes it.
- `parcel-enrichment.js`: render a small gauge/badge.
- Graceful "not available" state when the upstream field is absent (older
  `aecs4u-stats` version, or missing component data for that comune).

## Acceptance Criteria
- Gauge renders correctly for a comune with a known score
  (e.g. Civitavecchia).
- No error/blank card when the score field isn't present.

## Dependencies
- aecs4u/aecs4u-stats#34 (formula + implementation — must land first)

## Notes
See `docs/ZORNADE_GAP_ANALYSIS.md` §2 item 7 and §4 Phase 3.3. Postponed —
backlog item, not scheduled.
