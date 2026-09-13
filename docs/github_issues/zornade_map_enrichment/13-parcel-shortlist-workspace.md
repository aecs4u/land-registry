## Summary
Status: implemented.

Promote saved parcels from a flat favorites list into a shortlist a user can
work through: status, priority, notes, tags, and filtering by whether a parcel
currently carries an active hazard alert.

## Scope
- Extend the authenticated `/api/v1/saved-parcels` contract with `status`,
  `priority`, `notes`, `tags`, and `updated_at`. Keep the status vocabulary as
  product configuration — the map must not hard-code a fixed set of values.
- A shortlist view: filter and sort by status, priority and recency; filter to
  entries whose parcels intersect an active DPC criticality zone or FIRMS
  detection, reusing the hazard layers already wired up.
- Selecting an entry restores map state and opens the detail panel, reusing the
  existing `?parcel=<national-reference>` restoration path.
- Summary counts: total and per active status.

## Acceptance Criteria
- A saved parcel round-trips status, priority, notes and tags.
- The hazard filter returns only parcels with a currently active DPC criticality
  polygon intersection or FIRMS fire signal, and states the alert/feed issue
  time when available.
- Selecting an entry lands on the same map state as the shared-link path.
- Existing saved-parcel records migrate without loss and default to the initial
  status.

## Implementation Notes
- SQLite and PostgreSQL saved-parcel stores now include `status`, `priority`,
  and `tags`, with old rows defaulting to `new`.
- The API response includes `status_vocabulary` and `summary`, keeping the map
  from hard-coding lifecycle values.
- The primary direct map includes a shortlist workspace with status, priority,
  recency and active-hazard filters.

## Dependencies
- Existing `/api/v1/saved-parcels` persistence backend (SQLite/PostgreSQL).
- Hazard alert layers already delivered (gap analysis §2 item 8).

## Notes
Implements `docs/MAP_SRS.md` §5.9 (MAP-FR-056 – MAP-FR-062), added in v1.2.
This is the feature that changes what the product is for: the same parcel data
becomes a sourcing and triage tool rather than a viewer. Phase 4 work — it
depends on parcel coverage being stable, not on further enrichment.
