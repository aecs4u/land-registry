## Summary
Status: implemented in the direct map baseline.

Below `PARCEL_MIN_ZOOM` the direct map shows the "Zoom in to see cadastral
parcels" note over an otherwise empty basemap. Add an administrative boundary
layer (regions → provinces → municipalities) so the low-zoom view carries
geometry the user can orient by and click through, instead of nothing.

## Scope
- `map_layers.py`: expose `geo-boundaries` and `municipality-profiles` with the
  `admin-substitute` role so the client can identify orientation geometry from
  the allow-listed catalog.
- `map-v2.js`: render the admin substitute below the parcel threshold and hand
  off to parcels above it; keep `mapHelpNote` as the affordance.
- Optional: clicking an admin polygon flies to its extent, giving a
  zoom-by-clicking path from national view down to parcels.

## Acceptance Criteria
- Opening the map at the default national view shows administrative geometry,
  not an empty basemap.
- The handoff between admin geometry and parcels is visually clean at the
  threshold — no moment where both render at full weight.
- `mapHelpNote` still appears below the threshold and hides above it.

## Dependencies
- Admin boundary source in the serving schema (ISTAT limiti amministrativi).

## Notes
Implements `docs/MAP_SRS.md` MAP-FR-050 (Must). The affordance half of that
requirement is met by `updateParcelZoomAffordance`; the substitute-geometry
half is met by the `admin-substitute` catalog role and automatic low-zoom layer.
