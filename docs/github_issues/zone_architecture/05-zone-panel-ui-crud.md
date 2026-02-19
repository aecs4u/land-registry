## Summary
Build the Zone Management sidebar section with CRUD, collapse behavior, and live counters.

## Scope
- New Zone button in panel header
- Zone rename/delete inline actions
- Zone visibility checkbox
- Expand/collapse zone card
- Global zone count badge

## Acceptance Criteria
- All zone actions call APIs and update UI state without full page reload.
- Expanded/collapsed state is stable during updates.
- Zone count reflects current state after create/delete.

## Dependencies
- Issue 03
- Issue 01
