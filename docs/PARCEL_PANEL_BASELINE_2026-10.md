# Parcel panel baseline (Phase 0)

*Captured 2026-10-09 against the local dev server (`http://127.0.0.1:8011`).
Raw data: [`baselines/parcel_panel_baseline_2026-10-09.json`](baselines/parcel_panel_baseline_2026-10-09.json).
Post-implementation panel-view sample:
[`baselines/parcel_panel_after_cache_2026-10-09.json`](baselines/parcel_panel_after_cache_2026-10-09.json).
The full-response comparison sample is
[`baselines/parcel_panel_after_implementation_2026-10-09.json`](baselines/parcel_panel_after_implementation_2026-10-09.json).
The post-migration warm PostgreSQL sample is
[`baselines/parcel_panel_postgres_warm_2026-10-10.json`](baselines/parcel_panel_postgres_warm_2026-10-10.json).
The controlled refresh-first sample is
[`baselines/parcel_panel_controlled_2026-10-10.json`](baselines/parcel_panel_controlled_2026-10-10.json).
Re-run with `python scripts/parcel_panel_baseline.py --output docs/baselines/<name>.json`
and compare.*

This is the "before" measurement for
[PARCEL_PANEL_GAP_ANALYSIS_2026-10.md](PARCEL_PANEL_GAP_ANALYSIS_2026-10.md). It
describes **this host**. The initial samples predate the cache migration and
several optional stores remain absent, so they are not production coverage
numbers. Use the later sample below to distinguish the shared PostgreSQL cache
from the application-side SQLite fallback.

## Method

- Percentiles use the nearest-rank definition. On 2026-10-10, the stored p95
  summary fields were recalculated from the unchanged row-level timings to use
  this definition; request observations were not modified.
- 20 unique parcels: five in each of four areas, found by walking a grid of
  about 150 m steps around a centre until five distinct parcels resolve.
  Areas: Roma centro and Milano centro (large cities), Tolfa town centre (small
  comune), Monte Romano countryside (rural: the sampled parcel there is 9.4 ha).
  Bolzano was tried first and returned 404 for every point, because Bolzano and
  Trento cadastre is not in the AdE extract.
- The current collector treats `(national reference, canonical feature ID)` as
  parcel identity and reports how many references resolve to multiple feature
  IDs. Historical samples collected before this change de-duplicated by
  reference and cannot establish duplicate-reference cache isolation.
- The duplicate-reference isolation result is `null` when the sample contains
  no reference collision; it is true only when every colliding feature returns
  its own ID on both calls and its repeat request is a cache hit.
- `--refresh-first` forces a source rebuild for the first detail request per
  parcel, then measures an ordinary repeat request. This separates cold build
  latency from an existing cache hit without deleting cache rows.
- Before sampling, the collector polls `/health` for up to 60 seconds by
  default so a fresh no-reload worker can finish startup. Override the limit
  with `--startup-timeout` when the host needs more or less time.
- Per parcel: `GET /api/v1/enrichment/parcel/at-point`, then
  `GET /api/v1/enrichment/parcel/details/{reference}` twice.
- Each details response is classified by `read_model.cached`. Cold builds and
  cache hits are summarised separately. `cached` means the response was served
  from a persisted row; `database` can identify a successful write even on a
  cold rebuild.
- GET requests only. `--view panel` may populate the application's compact
  panel cache. `/api/v1/enrichment/status` was not called during these baseline
  measurements because it probes every store. Separate readiness diagnostics
  run on 2026-10-10 are recorded in the gap analysis.

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
| Cold builds (`cached: false`) | 40 calls; median 4.61 s, p95 6.783 s, max 7.72 s |
| Cache hits (`cached: true`) | **0 calls**; no warm-cache latency could be measured |
| Payload median | 12.7 kB |

Block coverage across the 20 parcels: `basic` 20/20, `cadastral` 20/20,
`valuation` 20/20. The other 19 declared blocks are unavailable on this host,
including `economics`, `population`, `demographics`, `buildings`, `opendata`
and `pvp`.

## Findings at Phase 0

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
3. **`/enrichment/status` was slow on this host.** It runs every store
   availability checker. The current route moves the probe to a worker thread
   and caches its result for 60 seconds, so repeated status requests do not
   rerun every check.
4. **Uncovered-area explanation was missing.** The primary map now explains the
   Bolzano and Trento coverage limit when parcel-by-reference returns 404; a
   browser check covers that path. The generic no-parcel point state remains a
   separate behavior to review.
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
| Warm-cache latency | Forced rebuild/repeat sample: local PostgreSQL median 0.015 s, p95 0.055 s; local app uses superuser credentials | Verify the deployed Cloud Run role and duplicate-reference cache behavior |

## Post-implementation panel-view sample

Captured 2026-10-09 against a fresh, no-reload dev server on port 8012 using
`--view panel`. The 20-parcel sample includes five parcels in each of the same
four areas, with 40 successful detail calls and no failures. Raw observations
are in the linked JSON file above.

| Measure | Panel view |
|---|---:|
| Cold builds | 14; median 6.461 s, p95 8.492 s, max 8.492 s |
| Cache hits | 26; median 0.010 s, p95 0.026 s, max 0.048 s |
| First-call cache hits | 6 of 20 |
| Repeat-call cache hits | 20 of 20 |
| Details payload median | 18,539 bytes |
| Available blocks | 6 of 22 on all 20 sampled parcels |

The six first-call hits were already warm when this complete run began (the
earlier interrupted local run had populated them). The 14 cold calls and 26
hits are classified by the response's `read_model.cached` field. The warm path
measured here is the application-side compact panel cache; this does not verify
the PostgreSQL read-model path.

The compact response was about 70% smaller than the 62,480-byte median from the
post-implementation full response sample linked above. It is still larger than the original
12,718-byte Phase 0 response because the later sample returned six populated
blocks rather than three. Those payload comparisons use different response
shapes and reflect changing source availability, so compare them as context,
not as a controlled same-data benchmark. The panel-view cold-build median was
6.461 s; latency remains a provisioned-host acceptance item.

## PostgreSQL sample after cache migration — hit classification unverified

Captured 2026-10-10 against a fresh, no-reload local server on port 8013 using
`--view panel`. The sample resolves the same 20 parcels, then makes two detail
requests per parcel. All 40 responses report `read_model.cached: true` and
`read_model.database: aecs4u-stats PostgreSQL via asyncpg`; the raw observations
are in the linked JSON file.

| Measure | Shared PostgreSQL responses |
|---|---:|
| Details responses | 40 ok, 0 failed |
| Responses identifying PostgreSQL | 40/40 |
| Cache-hit classification | Unverified; captured `cached` flag conflated cache hits with successful writes |
| Response latency | median 0.009 s, p95 0.015 s, max 0.021 s |
| Details payload median | 19,102 bytes |
| Available blocks | 7 of 22 on all 20 sampled parcels |
| Canonical feature IDs returned by local point lookup | 0 of 20 |

This confirms that the local app used the shared PostgreSQL read-model path for
all 40 responses, but it does not establish which requests were served from
cache. At capture time, a cold rebuild also returned `cached: true` when its
PostgreSQL upsert succeeded. The response-latency statistics are valid, while
the stored hit count and hit-latency classification are marked unverified in
the raw JSON. The response flag now means a cache hit, and the baseline script
has `--refresh-first` to measure a forced rebuild followed by its repeat hit.
The local connection uses the `postgres` superuser; the Cloud Run service
role's table access is still unverified. The local point lookup also omitted
canonical IDs, so this sample does not cover feature-specific duplicate-
reference reads. Seven populated blocks reflect this host's available data
and are not a production coverage claim.

## Controlled refresh-first sample

Captured 2026-10-10 against a fresh no-reload local server on port 8014 using
`--view panel --refresh-first`. The first request for each of 20 unique parcels
forced a rebuild and persisted the read model; the second request used the
ordinary cache path. All 40 requests returned HTTP 200 through PostgreSQL.

| Measure | Result |
|---|---:|
| Canonical feature IDs | 20/20 |
| Feature-ID matches in successful detail responses | 40/40 |
| Duplicate-reference groups in this sample | 0 |
| Forced cold builds | 20; median 5.168 s, p95 10.580 s, max 10.634 s |
| Repeat cache hits | 20; median 0.015 s, p95 0.055 s, max 0.721 s |
| Available blocks | 7 of 22 on all 20 sampled parcels |
| Median detail payload | 19,685 bytes |

This verifies the local feature-specific cache hit path after a forced rebuild.
Duplicate-reference isolation remains unverified because the sample contained
no reference collision. This is not a matched comparison to the original Phase
0 timing: the original measured the full response with three available blocks,
while this run measured the compact panel response with seven. The sample does
show a cold p95 above the preliminary 8.140 s target; repeat on the same
response shape and source coverage before treating that comparison as an
acceptance result. Local PostgreSQL credentials and data coverage also differ
from the likely Neon production target.
