# Parcel panel baseline (Phase 0)

*Captured 2026-10-09 against the local dev server (`http://127.0.0.1:8011`).
Raw data: [`baselines/parcel_panel_baseline_2026-10-09.json`](baselines/parcel_panel_baseline_2026-10-09.json).
Re-run with `python scripts/parcel_panel_baseline.py --output docs/baselines/<name>.json`
and compare.*

This is the "before" measurement for
[PARCEL_PANEL_GAP_ANALYSIS_2026-10.md](PARCEL_PANEL_GAP_ANALYSIS_2026-10.md). It
describes **this host**, which lacks several optional stores and the parcel
read-model cache, so absolute values are not production numbers. Use it to
compare phases on the same host.

## Method

- 20 unique parcels: five in each of four areas, found by walking a grid of
  about 150 m steps around a centre until five distinct parcels resolve.
  Areas: Roma centro and Milano centro (large cities), Tolfa town centre (small
  comune), Monte Romano countryside (rural: the sampled parcel there is 9.4 ha).
  Bolzano was tried first and returned 404 for every point, because Bolzano and
  Trento cadastre is not in the AdE extract.
- Per parcel: `GET /api/v1/enrichment/parcel/at-point`, then
  `GET /api/v1/enrichment/parcel/details/{reference}` twice.
- Each details response is classified by `read_model.cached`. Cold builds and
  cache hits are summarised separately.
- Read-only requests. `/api/v1/enrichment/status` is deliberately not called
  (finding 3).

## Results

| Area | Parcels | Lookup median | Details, first call median (min–max) | Details, second call median | Payload median |
|---|---|---|---|---|---|
| roma-centro | 5 | 0.29 s | 4.61 s (4.37–6.78) | 4.55 s | 11.4 kB |
| milano-centro | 5 | 0.24 s | 4.53 s (4.34–5.87) | 4.62 s | 17.9 kB |
| tolfa-comune | 5 | 0.24 s | 4.62 s (4.24–4.75) | 4.79 s | 11.1 kB |
| monte-romano-campagna | 5 | 0.24 s | 4.38 s (4.33–4.91) | 4.45 s | 14.9 kB |

| Measure | Value |
|---|---|
| Details calls | 40 ok, 0 failed |
| Cold builds (`cached: false`) | 40 calls; median 4.61 s, p95 6.94 s, max 7.72 s |
| Cache hits (`cached: true`) | **0 calls**; no warm-cache latency could be measured |
| Payload median | 12.7 kB |

Block coverage across the 20 parcels: `basic` 20/20, `cadastral` 20/20,
`valuation` 20/20. The other 19 declared blocks are unavailable on this host,
including `economics`, `population`, `demographics`, `buildings`, `opendata`
and `pvp`.

## Findings

1. **The read-model cache is not serving on this host.** Every repeat request is
   a cold build (`read_model.cached: false`, about 4.6 s), so the "one indexed
   lookup" path was never exercised. In `aget_parcel_enrichment` a hit requires
   `serving.parcel_enrichment_read_model` to exist and the async stats source to
   be reachable; otherwise the payload is rebuilt and `cached` stays false. The
   measurements cannot tell "table not provisioned" from "database unreachable".
   Consequences: warm-cache latency is **not measured** here and must be
   captured on a host where the table exists; and the panel must treat details
   as slow and lazy, showing identity from the tile feature immediately.
2. **Coverage on this host is 3 of 22 blocks.** The census-sections, ISTAT
   SQLite and BES stores are missing. Logs show `census_sections.IT.duckdb`
   expected under `/mnt/mobile/data/istat/` and `eurostat.IT.sqlite` under
   `/data/istat/`, two different roots. Phase 1 work on census, economics and
   POIs cannot be verified end to end here; use fixtures for unit tests and a
   fuller host for acceptance.
3. **`/enrichment/status` is slow and noisy.** It runs every store availability
   checker. Each raises and logs a full traceback at DEBUG when its store is
   missing. One call exceeded 15 s here, and the checkers are not cached. A
   60-second availability cache with a one-line cause per store would fix both;
   the panel's "not available on this host" states depend on it.
4. **Uncovered areas return 404 with no explanation.** The panel's empty state
   for a point with no parcel should say that provincial cadastres outside the AdE
   extract (Bolzano, Trento) are not covered.
5. **The dev server reloads on any file write in the repository**, including
   documents and scripts, causing short outages (`/health` failures). Allow time
   after edits before timing anything.

## Inventory of blocks (code, not host)

Pinned by `tests/test_parcel_block_inventory.py` with sources stubbed. Populated
by the builder: `basic`, `cadastral`, `population`, `demographics`,
`economics`, `buildings`, `opendata`, `pvp`, `valuation`. Declared and left
unavailable today: `address`, `addresses`, `risk`, `subsidence`, `terrain`,
`land_cover`, `land_use`, `valuation_history`, `coastal_erosion`,
`cultural_heritage`, `solar`, `poi`, `nightlights`.

## Decisions recorded in Phase 0

- **Constraints route:** not implemented; the contract states so. Implementation
  waits for the upstream tables (`aecs4u-stats#48`) and stays in Phase 3.
- **Panel form factor:** side sheet. **Energy scope:** parcel-level first, once
  a defensible footprint input exists.
- **Canonical panel:** `/map` is the only parcel-detail surface being extended;
  `/map-legacy` stays for upload and analysis and for the report hand-off.
  Behaviour and tested data transformations are ported, not shared as markup:
  the legacy cards depend on Bootstrap, Font Awesome, hard-coded Italian and
  fixed element IDs.

## Targets for Phase 1

| Measure | Now | Target |
|---|---|---|
| Blocks populated on this host | 3 of 22 | Unchanged unless a source is available; the panel must show a clear unavailable state for the rest |
| Sections rendered on `/map` | Identity, two KPIs and a generic five-value list per block | Every section the legacy panel renders, as typed sections |
| After a click | Identity from tile feature, then one 4.6 s call | Identity under 300 ms; sections fill independently; no section failure blanks the panel |
| Cold details median (this host) | 4.61 s | No worse than baseline plus 20 % |
| Warm-cache latency | Not measured | Capture on a host with the read-model table |
