## Summary
Introduce a first-class Zone/Microzone domain model with parent-child relationships and spatial metadata.

## Problem
Current persistence stores only a single drawn polygon entity and cannot represent nested microzones cleanly.

## Scope
- Add `zones` table.
- Add `microzones` table with `zone_id` FK.
- Add visibility flags at both levels.
- Add geometry, area, centroid metadata.
- Add migration path from existing drawn polygon records.

## Acceptance Criteria
- Zones can exist without microzones.
- Microzones cannot exist without a parent zone.
- Area fields are persisted and queryable.
- Existing data can be migrated without loss.

## Dependencies
- Issue 01 (canonical shell decision)
