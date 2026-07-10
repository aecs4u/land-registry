## Summary
Add dedicated spatial lookup APIs for key user workflows.

## Scope
- Point lookup endpoint: given lon/lat, return containing cadastral regions.
- Zone overlay endpoint: given zone, return cadastral regions by relation (`within` or `intersects`).

## Acceptance Criteria
- Endpoints have strict request/response models.
- Spatial queries are indexed and performant for expected dataset size.
- Results include key cadastral identifiers for UI display.
- OpenAPI examples are included for both endpoints.

## Dependencies
- Issue 02
