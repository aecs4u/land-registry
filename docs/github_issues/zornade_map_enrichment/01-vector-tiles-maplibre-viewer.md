## Summary
Replace the manual-load Folium/datashader viewer with always-on vector tiles
so any comune's parcels render on pan/zoom with no upload/load step first —
this is the single biggest gap vs. Zornade and the architectural prerequisite
several other map features build on.

**Implementation status (2026-09-12):** the direct MapLibre shell is now the
primary `/map` route. It consumes the existing allow-listed MVT catalog,
supports lazy layers, parcel identify/detail, search, URL state, and the
boundary-raster fallback. `/map-legacy` retains the Folium upload/analysis
workflow. The direct panel also hands off to the existing printable report
flow and authenticated saved-parcel API, while `/api/v1/map/metrics` records
normalized map request health. Remaining work is production-wide parcel
coverage/PMTiles delivery and parity for advanced drawing tools.

## Scope
- Serve the PMTiles produced by `aecs4u_stats.cadastral` (see dependency
  below) as static files from S3/Cloud Run (or a
  `/api/v2/tiles/parcels/{z}/{x}/{y}.mvt` / PMTiles range-request endpoint).
- New MapLibre-based page (`templates/map_v2.html`, `static/map-v2.js`) —
  parallel to the existing Folium page, not a replacement, until parity.
- Parcel layer: number labels at z≥16, hover highlight, click → select
  (reuse the existing `/parcel/at-point` flow already wired to the parcel
  detail panel).

## Acceptance Criteria
- Opening the new page and panning anywhere in Italy shows parcels with no
  upload/load step.
- Clicking a parcel opens the existing detail panel (`#parcelInfoPanel`).
- The current Folium page remains untouched and functional for the
  upload/analysis workflows it already serves.

## Dependencies
- aecs4u/aecs4u-stats#35 (PMTiles export — must land first)

## Notes
Biggest-ceiling, multi-week item (see `docs/ZORNADE_GAP_ANALYSIS.md` §3
P0–P3 and §4 Phase 0 for the full architectural rationale and effort
estimate). Postponed — do not start without confirming priority first.
