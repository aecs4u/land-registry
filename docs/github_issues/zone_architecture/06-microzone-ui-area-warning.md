## Summary
Build microzone controls inside each zone card, including area badges and large-area warnings.

## Scope
- Add Microzone button per zone
- Microzone rename/delete inline actions
- Microzone visibility checkbox
- Area badge in km2
- Warning badge when area > 0.3 km2
- Microzone count badge per zone
- Empty microzone state text

## Acceptance Criteria
- Area values are rendered from API data.
- Warning threshold behavior is consistent and test-covered.
- Per-zone microzone counts update after CRUD actions.

## Dependencies
- Issue 04
- Issue 05
