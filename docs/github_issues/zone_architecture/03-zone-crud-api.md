## Summary
Implement dedicated Zone CRUD API endpoints aligned with product workflows.

## Scope
- Create zone
- Rename zone
- Delete zone
- Toggle visibility
- List zones (with counts and metadata)

## Acceptance Criteria
- All endpoints validate input with strict schemas.
- Auth and ownership are enforced.
- API responses are consistent and documented in OpenAPI.
- Visibility changes are immediately reflected in fetch responses.

## Dependencies
- Issue 02
