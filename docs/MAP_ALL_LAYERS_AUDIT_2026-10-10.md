# `/map` with every layer on — design and quality audit — 2026-10-10

**Target:** `http://localhost:8011/map?lat=45.012983&lng=7.689691&zoom=10.94&parcel=L219_130200.76&parcel_id=3819097225571325#sezione-omi` (Torino, parcel 76, sheet 1302), dev server, signed out.
**Scenario:** all 14 catalog layers and every available live layer switched on at once, then the view moved to z13, z15 and z17 around the parcel.
**Tooling:** Playwright MCP (Chromium, 1440×900 and 390×844), `queryRenderedFeatures` and style inspection through a hooked MapLibre instance, axe-core 4.10, PerformanceObserver, `curl`, `pg_stat_activity` and the dev server log.
**Screenshots:** [`docs/map-all-layers-audit-2026-10-10/`](map-all-layers-audit-2026-10-10/) (indexed in §9).
**Related:** [APP_AUDIT_2026-10-09.md](APP_AUDIT_2026-10-09.md) covers the rest of the site and the parcel sheet in isolation; findings from it that recur here are cross-referenced as *(10-09 …)*.

**Requirement recorded from this review:** *only the data that can be visualised in the current view should be loaded, not all the items.* Finding C1 was the main violation and is **fixed and verified** (§8); §7 lists the other places that still need the same treatment.

---

## 1. Summary

With a single layer on, the map is fast and legible. With everything on it is neither: the whole dataset of one live layer is downloaded and parsed in the browser, the tile fan-out takes up to 20 s to settle, the two right-hand panels cover each other, and the selected parcel cannot be told apart from the layers around it.

- **The auction layer loaded the entire national dataset** (717,150 sales points, 8.5 MB gzipped, 8–9 s per request, no cache header) regardless of what was in view, then prepared 710,000 markers in the browser. JS heap reached **446 MB** (C1). *Fixed: it now loads only the viewport, 0.6 MB in 0.1–0.3 s, heap 14–49 MB.*
- **Enabling all layers is a request storm.** 160 tile requests at z13 took 19.5 s to settle with a 1.7 s main-thread block; at z14–15 on the same Postgres the developer's log shows `sorry, too many clients already` and a wall of 503s for census, solar, maritime, subsidence and boundaries tiles (C2).
- **The parcel sheet is covered by the Layers panel** by 292 px at 1440 px, and by half its height on a phone (H1).
- **The selected parcel disappears**: its highlight (`#f08a24`) sits among parcel outlines (`#d97925`) and the EGMS grid (`#c45c2d`), a 12 px orange square on a field of orange (H2).
- **Thirteen translucent fills stack into a mauve wash**; the basemap becomes unreadable, nothing explains the colour ramps, and the dark theme does not read as dark (H3).

| Severity | Count |
|---|---|
| Critical | 2 |
| High | 8 |
| Medium | 10 |
| Low | 5 |

---

## 2. Method

1. Loaded the exact URL cold, recorded the initial state, URL handling, console and network.
2. Opened the Layers panel, inventoried every row and live toggle, then clicked every enabled checkbox and live toggle in one tick.
3. At z10.94, z13, z15 (and z17 where the browser survived) waited for `areTilesLoaded()`, then recorded per layer: tile requests, statuses, bytes, slowest tile, and `queryRenderedFeatures` counts; long tasks; heap; frame times during a scripted pan.
4. Read the style (draw order, paint) and measured panel geometry.
5. axe on the Layers panel and the page; identify click; dark theme; 390 px width.
6. Cross-checked API payload sizes and timings with `curl`, and Postgres connection ownership with `pg_stat_activity`.

**Limits.** Signed out (Sales layer is disabled). The z17 stage was lost when the browser connection dropped on a machine with ~600 MB free RAM, and one earlier run died at the same point, so the browser's memory use with everything on is itself a data point (C1). Cold-start numbers include first-time foreign-server connections and a loaded developer machine (load average 2–6).

---

## 3. Layer inventory

14 catalog layers in 7 groups (Administrative, Cadastral, Market, Risk, Demographics, Energy, Territory) and 6 live layers.

| Layer | Min zoom | Default | Notes |
|---|---|---|---|
| Administrative boundaries | 5 | on | |
| Postal zones | 10 | off | "Show CAP labels" |
| Cadastral sheets | 10 | off | "Show sheet labels" |
| Cadastral parcels | 14 | **on** | "Highlight requests (0)", two "Refresh" buttons |
| Cadastral urban sections | 11 | off | no data in Torino |
| OMI market zones | 10 | off | |
| Flood hazard areas | 8 | off | **empty everywhere** (10-09 C2, repair still pending) |
| Landslide hazard areas | 8 | off | |
| MPS04 seismic points | 7 | off | |
| Seismic classification | 6 | off | |
| Ground movement (EGMS) | 8 | off | very dense |
| ISTAT census sections | 11 | off | labelled "Needs configuration" yet serves data (M2) |
| Solar potential by municipality | 5 | off | |
| Maritime-domain concessions | 6 | off | inland: empty (correct) |

Live layers: Points of Interest, Active Fires, Civil Protection Alerts, Auction listings, SISTER building records, and Sales (**disabled when signed out**, while Auction listings loads the same dataset anyway; M7).

---

## 4. Per-layer results with everything on

Four tiles per layer at z10.94, 16 (12 for boundaries/urban/census) at z13, 12 at z15. "Features" is `queryRenderedFeatures` for the visible layers in the viewport.

| Layer | z10.94 KB / features | z13 KB / features | z13 slowest tile | z15 KB / features | z15 slowest tile |
|---|---|---|---|---|---|
| geo-boundaries | – / 194 | 5 / 50 | 0.2 s | 2 / 24 | 35 ms |
| postal-zones | 106 / 282 | 150 / 76 | 0.3 s | 30 / 62 | 25 ms |
| cadastral-sheets | 186 / 1,435 | 248 / 400 | 0.3 s | 18 / 124 | 10 ms |
| cadastral-parcels | below z14 | below z14 | – | – / **8,318** | – |
| urban-sections | below z11 | 3 / 22 | 0.3 s | 0 / 0 | 8 ms |
| market-zones | 159 / 671 | 194 / 130 | **19.9 s** | 13 / 94 | 26 ms |
| flood-hazard | 0 / 0 | 0 / 0 (16 empty) | 17.5 s | 0 / 0 | 13 ms |
| landslide-hazard | 153 / 2,472 | 180 / 408 | 17.9 s | 0 / 0 | 11 ms |
| mps04-points | 5 / 28 | 6 / 1 | 17.9 s | 0 / 0 | 14 ms |
| seismic-classification | 27 / 194 | 32 / 50 | 18.5 s | 2 / 24 | 17 ms |
| surface-subsidence | **413** / **9,595** | **834** / **8,739** | 18.6 s | 52 / 995 | **7.5 s** |
| census-sections | below z11 | 271 / **3,245** | 19.1 s | 106 / 1,347 | **7.8 s** |
| solar-potential | 59 / 194 | 71 / 50 | 19.4 s | 6 / 24 | **8.4 s** |
| maritime-concessions | 0 / 0 | 0 / 0 | 19.5 s | 0 / 0 | **8.6 s** |

| Stage | Settle time | Long tasks | Other |
|---|---|---|---|
| z10.94 | 2.6 s | 490, 84, 57 ms | no tile errors |
| z13 | **19.5 s** | 722 ms, **1,717 ms** | 160 tile requests, no 503 in this run |
| z15 | **11.9 s** | 442 ms | 156 tile requests, 0 × 503; heap **446 MB**; pan p50 17 ms, p95 33 ms, max 67 ms |

Tile statuses were all 200 in my runs; the 503 storm below comes from the developer's own log of a z14–15 session.

---

## 5. Findings

### Critical

#### C1. Layers load the whole dataset, not the current view
- `GET /api/v1/sales/map-points?period=all` returns `points` with **717,150** rows (`[id, lon, lat, price, date, category, flag]`), **8,478,035 bytes gzipped**, in **7.9 s and 9.0 s** on two consecutive calls. No `Cache-Control`/`ETag` header, so every page load and every refresh pays this.
- The client then runs "Preparing sale markers… 710,000 of 717,150 records" and "Drawing sale markers…". With Auction listings toggled on, the toast read **18 s**, **26 s** and **11–13 s** in three runs, and the page heap reached **446 MB**. On this machine the browser died twice while the layer was loading.
- The view at that moment (z10.94–15 around Torino) contained a few hundred of those points.
- Sales is "disabled when signed out" but the same national dataset loads through Auction listings (M7).
- Related whole-dataset or oversized loads: `/api/v1/enrichment/bulletin` returns the national bulletin (**1.3 MB**) for any view; `/api/v1/sales/pvp/{id}` is called once per nearby sale at load (8 calls in the first 8 s, an N+1 pattern); `/api/v1/map/layers/cadastral-parcels/features` is fetched for "Highlight requests".
- **Requirement (from this review):** only the data that can be visualised in the current view is loaded. See §7 for the design.
- **Status: fixed for sales and auction points** (§8). `map-points` accepts `bbox` and `limit`; the client sends the viewport, refetches on `moveend`, aborts stale requests, and shows "Zoom in to see auction listings (from zoom 8)" below z8 instead of loading anything. Measured on the same machine and data:

| | Before | After |
|---|---|---|
| Request | `period=all` (national) | `period=all&limit=20000&bbox=7.35,44.87,8.03,45.15` |
| Payload (gzip) | 8,478,035 B | 589–613 KB (10,290–11,490 points in view) |
| Server time | 7.9–9.0 s, every call | 0.05–0.12 s warm |
| Client heap after load | 446 MB | 14–49 MB |
| Pan of 60 px | – | 0 requests (inside the loaded box) |
| Far move / zoom in | – | 1 request, `truncated:true` when the view holds more than the cap |
| National zoom (< z8) | 717k markers prepared | 0 requests, hint shown |

  The first request after a server restart waits for the shared snapshot to load (about a minute for 717k rows; §7, point 2). *Changed: the snapshot is now published as soon as the rows are read and built; the two optional parcel-position queries run afterwards and swap in a refined copy. The cold wait is therefore the base read (about 15 s in the 09-28 audit) plus the build; it has not been re-measured against the production database.*

#### C2. Everything on exhausts Postgres connections and fans out into a request storm
- **Fan-out:** at z13, 10 tile layers × 16 tiles + 3 × 12 = **160 tile requests** in one burst, 17.5–19.9 s per tile (mostly queueing: browsers allow 6 HTTP/1.1 connections per host and the app's map pool is 8), settle 19.5 s, and a 1.7 s main-thread long task from decoding about 1.9 MB of MVT.
- **Connection exhaustion:** the developer's log of a z14/15 session shows `could not connect to server "census_sections_source" / "solar_source" / "agenziademanio_source" / "consolidate_istat"` and `sorry, too many clients already`, producing 503 for census, solar, maritime, subsidence and boundaries tiles within one second, then a POI request that failed the same way.
- `pg_stat_activity` at idle shows **70 of 100** connections, about 35 of them idle `postgres_fdw` backends (zornade 5, istat 5, omi 5, eurostat 4, sister 3, mef_irpef 3, census_sections 2, hazards 2, …). Every pooled connection that reads a foreign table keeps one remote backend per server, and `market-zones` goes through two hops (zornade → omi). Together with DBeaver sessions and sibling dev apps, a burst runs out of the 97 usable slots.
- In my own runs the same scenario produced no 503 (the machine was less loaded), so this is pressure-dependent; the 7.5–8.6 s first tiles for subsidence, census, solar and maritime at z15 are cold foreign-server connections.
- The same developer log shows the client requesting **z17 and z18 tiles of municipality-scale layers** (`solar-potential/18/…`, `census-sections/18/…`, `market-zones/18/…`, `flood-hazard/18/…`, `maritime-concessions/18/…`): the geometry is identical at every zoom above ~16, so each of those requests is pure waste and each one opens foreign-server work.
- **Status: mitigated.** Every vector source now has `maxzoom` from the catalog (`tile_max_zoom`, 16): measured at z18 the browser requested only z16 tiles (26) and z10 tiles (12), none at z17 or z18, i.e. 4–16× fewer requests per layer at the highest zooms. The map's Postgres pools default to **4 connections instead of 8** (`MAP_DB_POOL_MAX`), and wait up to 15 s for a free one instead of failing after 5 s, which halves the number of idle foreign-server backends the app can hold. Not yet done: a per-foreign-server semaphore, `keep_connections 'off'` on the foreign servers, and a pooler in front of Postgres.

### High

#### H1. The Layers panel covers the parcel sheet (desktop and mobile)
- At 1440 px the sheet is x 857–1375 (z-index 4) and the Layers card x 1083–1375 (z-index 7): **292 px overlap**. The sheet's right-hand values (Sale, Rent, Gross yield, Percentile), the "Seismic zone" KPI and its buttons (copy link, PDF, close ×) are hidden ([screenshot](map-all-layers-audit-2026-10-10/05-all-layers-z15-with-panels.png)).
- At 390 px the sheet (y 126–690) and the Layers card (y 328–824) overlap by more than half; the loading toast sits on top of both, and the round action buttons cover the sheet's close button *(10-09 H1)*.
- **Fix:** one right dock at a time (opening Layers collapses the sheet to its header), or place Layers in the left dock under the search card.

#### H2. The selected parcel cannot be found with the layers on
- Selection fill `#f08a24` at 0.24 opacity and a 3 px line sit on top of cadastral outlines `#d97925` (0.8 opacity, 0.8 px), the EGMS grid `#c45c2d` (line 1.4 px, fill 0.25), and the orange tint of the parcels fill. At z15 the parcel is a 12 px orange square at the view centre ([map-only screenshot](map-all-layers-audit-2026-10-10/05-all-layers-z15-map-only.png)).
- Nothing else marks it: no pin, no halo, no panning to keep it clear of the panels.
- **Fix:** a white halo/casing under a saturated, contrasting selection stroke; a pin or pulse on selection; dim other fills while a selection is active.

#### H3. Thirteen translucent fills produce an unreadable, unexplained map
- Opacity per fill (from the style): parcels 0.04, geo-boundaries 0.075, market and postal zones 0.11, flood and landslide 0.2, seismic 0.2, EGMS 0.25, census 0.3, **solar 0.48**. Composited over a light basemap they become a mauve wash ([z10.94](map-all-layers-audit-2026-10-10/04-all-layers-z10.94.png), [z13](map-all-layers-audit-2026-10-10/04-all-layers-z13.png)).
- **No legend** for the colour-ramped layers (solar potential 0–100 %, census population, seismic zones 1–4, EGMS risk); the only legend element in the panel is "Polygons / Points" for maritime concessions. 18 swatches show one flat colour per layer, not the ramp.
- Dark theme (screenshot [07](map-all-layers-audit-2026-10-10/07-all-layers-dark-z15.png)): the chrome turns dark but the map stays a mid-tone mauve because the same fills lighten the dark basemap; the overlay palette was not tuned for dark.
- The EGMS layer alone draws 9,595 features at z10.94 and 8,739 at z13 as a regular orange grid that dominates everything else.
- **Fix:** layer presets (Cadastre, Risk, Market) instead of 14 independent toggles; a global "dim basemap/other layers" control; per-layer legends with the ramp; thin or hatch the EGMS grid.

#### H4. Time to settle with all layers on is 12–20 s; no request prioritisation
- 160 tile requests at z13 (19.5 s) and 156 at z15 (11.9 s). Every layer requests the full viewport plus buffer at the same priority, including layers that the user turned on a second ago and layers that are mostly empty (flood 16/16 empty, maritime 16/16 empty, landslide 12/12 empty at z15).
- **Fix:** request tiles in the order of the user's last toggle, use 512 px tiles for sparse layers (a quarter of the requests), serve over HTTP/2 (the dev server speaks HTTP/1.1), and short-circuit layers whose coverage bounds do not intersect the view (maritime concessions inland, flood outside coverage).

#### H5. The Layers panel is 4,000 px of controls
- 81 controls (30 checkboxes, 14 range sliders, buttons), `scrollHeight` **3,993 px** inside a 748 px card, seven `<details>` groups. Each row repeats a four-part grey metadata line ("Available · From zoom 5 · Source version shown in feature details · aecs4u-stats PostgreSQL/PostGIS") *(10-09 M6)*.
- All 14 opacity sliders have the identical accessible name "Fill" (min 0, max 0.6), so a screen-reader user cannot tell them apart. "Highlight requests (0)" has two adjacent "Refresh" buttons with no distinguishing label.
- **Fix:** compact rows (title + one status chip), advanced controls on expand, unique slider names ("Fill opacity, Cadastral parcels"), one Refresh per feature.

#### H6. With cadastral parcels on, other layers cannot be identified
- A click at a point where nine layers overlapped selected only the parcel under the cursor ("Parcel 35 TORINO", status "Parcel selected"); no flood, seismic, market-zone, postal, census or solar detail appeared. The identify code resolves the parcel first and consults overlays only when there is no parcel (single observation; the code path is `queryRenderedFeatures` on the parcel layer, then on overlay layers).
- **Fix:** a "stacked features" list for the clicked point (one row per layer) or a modifier-click to cycle layers.

#### H7. Flood hazard is still empty (known, repair not applied) *(10-09 C2)*
- 4/4 tiles at z10.94, 16/16 at z13, 12/12 at z15 are empty in Torino although the layer's health says available and 85,460 rows. The wrong-CRS geometry repair script exists and has not been run.

#### H8. Deep link and the first paint with a parcel in the URL
- CLS on this URL is **0.29** (the plain `/map` was 0.18): the sheet opens, the sidebar collapses and the map resizes in sequence. Long tasks of 71 and 84 ms before interaction.
- The `#sezione-omi` fragment has no matching element id (`parcel-section-omi` exists); the code maps it and scrolls the OMI section into place, which works, but the hash stays in the URL and the tab strip shows two active tabs ("Value" and "Coverage").
- `parcel/details` took **5.2 s** for this parcel; 8 `/sales/pvp/{id}` requests follow within the first seconds.

### Medium

#### M1. The sheet needs the whole panel width but gets 56 % of it when Layers is open
Values such as "Sale 700–1400 €/m²" and "Gross rental yield 11,83 %" are only visible after closing Layers; the sheet keeps the same scroll position, so the user returns to a different part of it.

#### M2. Layer status contradicts behaviour
`census-sections` is labelled **"Needs configuration"** in the panel and `/api/v1/map/layers/health` reported it unavailable at the start of this session, yet its checkbox is enabled and it served 271 KB / 3,245 features at z13. Either the label or the health check is wrong *(similar to 10-09 H9 for urban sections)*.

#### M3. Layers below their minimum zoom are enabled but invisible
At z10.94 census-sections, urban-sections and cadastral-parcels are ticked but draw nothing. The panel shows "Zoom in: visible from zoom 14" for parcels only; the other two show no hint, and the page-level help note ("Zoom in to see cadastral parcels") was hidden.

#### M4. Accessibility (axe, all layers on)
| Scope | Rule | Impact | Nodes |
|---|---|---|---|
| Layers panel | `color-contrast` | serious | 2 (live-layer checkbox label, `#sisterParcelsCount`) |
| Page | `color-contrast` | serious | 4 |
| Layers card and sheet | `landmark-complementary-is-top-level` | moderate | 2 |

All 81 controls have an accessible name. See H5 for the slider naming.

#### M5. Toasts and progress
- "Loading auction listings… 26 s · Drawing sale markers…" and "Loading fire detections… 2s · 5 tasks in progress" sit bottom-left over the Shortlist button and, on mobile, over the Layers card.
- There is no cancel, and a long task is described only by elapsed time.

#### M6. CSP preload error recurs
`Connecting to 'https://fonts.googleapis.com/css2?family=Inter…' violates … connect-src` and `Couldn't load preload assets` on every parcel selection *(10-09 M9)*.

#### M7. Sales is gated, Auction listings is not
Sales toggle is disabled for signed-out users ("Sign in to use sales data") while "Auction listings" downloads and renders the same 717k-point dataset for anyone. Decide which is intended and gate the download accordingly.

#### M8. Parcel rendering weight
8,318 parcel features rendered at z15 (a 12 × 8 tile window of dense urban fabric) plus 995 EGMS and 1,347 census features; a 442 ms long task on first draw. Fine on desktop GPU, risky on phones.

#### M9. First-time foreign-server connections add 7.5–8.6 s to the first tile of four layers
Subsidence, census, solar and maritime each took 7.5–8.6 s for their first z15 tile (cold `postgres_fdw` connection to `egms`, `census_sections`, `solar`, `agenziademanio`). Warm requests are under 30 ms. A startup warm-up (one trivial query per foreign server) or longer-lived pooled connections would remove it.

#### M10. Browser memory
Heap reached 446 MB with all layers on at z15, and the Chromium instance driven by the test was killed twice at that point. See C1; fixing the sales payload is the main lever.

### Low

- **L1.** The right-hand action column keeps two dimmed icons (locate/"…") with no tooltip explaining why they are disabled.
- **L2.** Ten layers get 4 tiles at z10.94 even when their data cannot be seen at that zoom (e.g. seismic points: 28 features for 5 KB).
- **L3.** `Show CAP labels` and `Show sheet labels` checkboxes are separate from the layer checkbox; they stay ticked when the layer is switched off.
- **L4.** The layer swatch colours (grey for boundaries, olive for postal, orange for parcels) do not match the ramp colours actually drawn for solar, seismic and census.
- **L5.** The hover popup ("Parcel 35 / TORINO") stays over the selected parcel after a click and hides its label in the dark-theme screenshot.

---

## 6. What worked

- With a single layer on, every tile was 200 and sub-100 ms warm; Administrative boundaries, Postal zones, Cadastral sheets, Seismic classification and Solar potential settle in under 3 s together at z10.94.
- Panning with everything on at z15 held 17 ms median / 33 ms p95 frame time (64 fps class), with 0 frames over 100 ms.
- The deep link restores the view, the selected parcel, the sheet and scrolls the OMI section into view (`#sezione-omi` → `parcel-section-omi`).
- The panel shows a per-layer "Zoom in: visible from zoom N" hint for parcels, a coverage note for approximate extents, and unique swatches per layer.
- Draw order is sensible: fills below lines below points below labels, the selection and analysis layers last (50 style layers in total).
- No console errors apart from the CSP font preload; no horizontal overflow at 390 px; all 81 controls in the panel have an accessible name.

---

## 7. Design: load only what the current view can show

Principle: **a request is made only for features that intersect the viewport (plus a small buffer) at the current zoom, in the smallest representation that can be drawn.** Concrete changes, in order of value:

1. **Sales / auction points (C1). Done.** `GET /api/v1/sales/map-points?period=…&bbox=west,south,east,north&limit=N` returns only the points inside the box, capped (default 20,000, nearest the centre first) with `matched` and `truncated`. Without `bbox` the legacy national feed is unchanged for existing consumers. The client refetches on `moveend` (350 ms debounce), aborts the in-flight request, skips the fetch when the view is still inside the loaded box, and loads nothing below z8. Still open: server-side aggregation (grid or H3 counts) so a national view can show clusters honestly instead of a hint.
2. **Make the points a tile layer** (the better long-term form): `/api/v1/tiles/sales-points/{z}/{x}/{y}.pbf` with PostGIS `ST_AsMVT` and server-side clustering, so MapLibre handles bbox, caching and cancellation and the client drops its 700k-record preparation step.
3. **Bulletin.** Request only the municipalities intersecting the view (`?bbox=`), or tile the bulletin zones; today every view receives the national 1.3 MB TopoJSON.
4. **Nearby sales.** Return the fields the sheet needs in `nearby-points` instead of one `/sales/pvp/{id}` call per point.
5. **Fires, POIs.** Already radius-based; pass the viewport bounds instead of centre + radius so a wide view and a narrow view do not request the same disc.
6. **Per-layer coverage gating.** Skip tile requests for layers whose `coverage_bounds` do not intersect the view or whose min zoom is above the current zoom (the client already knows both).
7. **Server concurrency.** A per-source semaphore and short-lived foreign connections (C2).

---

## 8. Changes made during this audit

Nothing is committed.

| Area | Files | What |
|---|---|---|
| Sales/auction points (C1, §7.1) | `land_registry/pvp_sales.py`, `land_registry/routers/api.py` | `bbox` (+`limit`) on `/api/v1/sales/map-points`; grid-index candidate selection; `matched`/`truncated`; category counts describe the viewport; 400 on malformed or inverted boxes |
| | `land_registry/static/map-v2.js` | `viewportBbox`, `viewportLoaded`, `bindViewportReload`, abortable requests, `POINT_OVERLAY_MIN_ZOOM = 8`, truncation message; both the Auction and Sales overlays |
| | `tests/test_pvp_sales.py`, `tests/test_map_v2_contract.py` | 4 server tests (bbox, limit/truncation, viewport category counts, endpoint validation) and a client contract |
| Tile fan-out (C2) | `land_registry/map_layers.py`, `land_registry/static/map-v2.js` | `tile_max_zoom` (16) in the catalog and as the vector source `maxzoom`; pool default 4 (`MAP_DB_POOL_MAX`), acquire wait 15 s |
| | `tests/test_map_layers_contract.py` | catalog cap and pool-size tests |
| Italian | `land_registry/translations/it/LC_MESSAGES/land_registry.po` | 6 new strings (the `.mo` is not rebuilt: `msgfmt` already fails on two pre-existing duplicates, "Not available" and "Parcel") |
| Earlier this session | `land_registry/pg_ssl.py`, `bulletin_store.py`, `datashader_service.py` | TLS off for loopback psycopg2 (the bulletin segfault) |

**Tests.** The four focused suites report 107 passed and 12 failed. All 12 fail without these changes: the 11 recorded in the 10-09 audit (stale layer ids such as `hazard-areas`, plus the `uploaded-file` string) and `test_adjacent_parcels_endpoint_returns_feature_collection`, which fails because the working tree's `get_adjacent_parcels` now passes an extra `method=` argument that the test does not expect.

**Dev-server note.** Saving Python files restarts the reload worker; in-flight tile requests are then cancelled ("timeout graceful shutdown exceeded", HTTP 500 in the log) and the first sales request afterwards waits for the cold snapshot. Neither is an application fault.

---

## 9. Screenshot index

All under [`docs/map-all-layers-audit-2026-10-10/`](map-all-layers-audit-2026-10-10/).

| File | Shows |
|---|---|
| `01-initial-url.png` | the exact URL, cold, before any toggle |
| `02-layers-panel-initial.png` | the Layers panel with the defaults |
| `03-all-layers-z13.png`, `03-all-layers-znull.png` | first all-layers run at z13 and z10.94 |
| `04-all-layers-z10.94.png`, `04-all-layers-z13.png` | all layers on at z10.94 and z13 |
| `05-all-layers-z15-with-panels.png`, `05-all-layers-z15-map-only.png` | z15 with both panels, and with the panels hidden (H1, H2) |
| `06-identify-click.png` | click on a point where nine layers overlap (H6) |
| `07-all-layers-dark-z15.png` | dark theme with everything on (H3) |
| `08-all-layers-mobile-390.png`, `08-all-layers-mobile-390-layers.png` | 390 px with sheet, and with the Layers card (H1) |
