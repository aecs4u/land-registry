## Summary
Show the composite 0–100 municipal hazard context score as a gauge in the
parcel panel header, next to the existing Rischi card.

**Implementation status:** the primary, legacy, and v2 risk consumers are
wired. The primary panel shows the valid 0–100 score in its header stat strip
and in the Risks card, with the municipal scope stated. The app uses the shared
helper when installed and an aligned local compatibility implementation with
older stats wheels. Representative live comune responses and the released
shared package remain pending.

`municipal_hazard_index_v1` averages the available component scores equally:
DPC seismic zone anchors map zones 1–4 to 100, 66.67, 33.33 and 0; IdroGEO
landslide P3+P4 area % and flood P3 area % are used directly. Missing
components are excluded and reported. The index describes municipal context;
it is not a probability, parcel estimate or engineering assessment.

## Scope
- `stats_service.py`: calculate the versioned score from existing seismic and
  hydrogeological inputs, preferring `aecs4u_stats.hazards` and falling back to
  the aligned local compatibility implementation.
- `parcel-enrichment.js`: render a small gauge/badge.
- Graceful `insufficient_data` state when no valid component data are present.

## Acceptance Criteria
- Gauge renders correctly for a comune with a known score
  (e.g. Civitavecchia).
- No error/blank card when the score field isn't present.

## Dependencies
- `aecs4u/aecs4u-stats#34` (upstream release, formula alignment, and a real-host response remain pending)

## Notes
See `docs/ZORNADE_GAP_ANALYSIS.md` §2 item 7 and §4 Phase 3.3. The UI consumer
and local compatibility path are wired. End-to-end acceptance remains pending
until representative complete and partial comune responses are checked and the
shared package is released.
