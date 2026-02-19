## Summary
Add zone-level business actions for Sales and Rents navigation.

## Scope
- Sales button on zone card linking to mappings view scoped to zone.
- Rents button on zone card linking to mappings view scoped to zone.
- Shared URL contract for passing zone context.

## Acceptance Criteria
- Sales and Rents deep links preserve selected zone context.
- Target page can resolve and load zone geometry/context reliably.
- Behavior is documented for analytics and QA.

## Dependencies
- Issue 03
- Issue 05

## URL Contract
- Base route: `/map`
- Query params:
  - `view=mapping` (required for mapping-view deep links)
  - `zone_id=<int>` (required for zone-scoped flows)
  - `workflow=sales|rents` (required for business intent)
- Example links:
  - `/map?view=mapping&zone_id=42&workflow=sales`
  - `/map?view=mapping&zone_id=42&workflow=rents`

## QA / Analytics Notes
- Sales and Rents actions must always update URL query params with the contract above.
- Reloading or sharing the URL must reopen Mapping view with the same zone context.
- Mapping view should display:
  - selected workflow (`sales` or `rents`)
  - selected zone label/id
  - fallback error state when zone no longer exists or is not accessible.
