# Map/Cadastral Session Fixes — 2026-07-12

Summary of bugs found and fixed in one working session on the map view
(`land_registry/templates/index.html` and its map stack), triggered by
testing the cadastral-boundary layer live and comparing against
app.zornade.com. Grouped by root cause, not chronologically. For the
longer-term feature roadmap vs. Zornade, see `ZORNADE_GAP_ANALYSIS.md` —
this doc is a changelog of concrete fixes, not a plan.

## 1. Cadastral boundary layer (parcels weren't showing, overlay hid the map)

**Files:** `datashader_service.py`, `routers/api.py`, `static/enrichment-layers.js`

- `generate_boundary_tile()` only ever read `cadastral_map.*.fgb` (foglio/sheet
  outlines). The much larger `cadastral_ple.*.fgb` files (individual parcel
  geometry, confirmed present on disk, up to 5.6 GB each) were never touched,
  so particella boundaries could never render regardless of zoom.
- The tile renderer also filled every polygon with a translucent count-shaded
  color before drawing the outline. Since foglio sheets tile the land
  contiguously, that fill covered the *entire* land area — this is what made
  the base satellite imagery look hidden under a solid tan layer.
- **Fix:** `generate_boundary_tile()` / `_region_fgb_bounds()` /
  `_candidate_fgb_files()` are now layer-aware (`"map"` vs `"ple"`), each
  reading and caching bounds from the correct FGB set. The fill was dropped
  entirely — only a crisp outline is rasterized (amber for sheets, teal for
  parcels), transparent everywhere else.
- `/api/v1/tiles/cadastral-boundaries/{z}/{x}/{y}.png` gained a `layer=map|ple`
  query param. The frontend now adds **two** stacked tile layers under the one
  "Cadastral Boundaries" toggle: sheets from zoom 13, parcels from zoom 16
  (particelle are far more numerous/tiny, so only useful once zoomed in).
- **Cold-start cost:** the first-ever request touching the `ple` layer took
  ~66s (indexing `pyogrio.read_info()` across 19 multi-GB files). Extended the
  existing `warmup_jit()` startup hook (already called from `main.py`'s
  lifespan) to pre-index both layers' FGB bounds, so this cost is paid once at
  process start instead of on a user's first click.

## 2. No tooltip on parcel click ("just shows Region: ...")

**Files:** `datashader_service.py`, `routers/api.py`, `static/enrichment-layers.js`

The boundary tiles are rasterized PNGs — Leaflet can't attach a per-feature
tooltip to a bitmap. Clicking always fell through to the one vector layer
underneath (`map.py`'s "Italy Regions" GeoJSON, tooltip: `Region: <name>`),
which covers huge areas and carries no parcel-level detail.

- **Fix:** added `identify_feature(lat, lng, layer_type)` — does a
  point-in-polygon lookup against the same source FGB files (small bbox read,
  then `geometry.contains(point)`), returning label/reference/comune/
  provincia/regione.
- New endpoint: `GET /api/v1/cadastral-identify?lat=&lng=&layer=map|ple`.
- Frontend: while the boundary layer is active, clicking the map calls this
  endpoint and opens a Leaflet popup with **Particella**/**Foglio**, Comune,
  Provincia, Regione — verified against real data (e.g. particella
  `E204_000400.194` in Grottaferrata).

## 3. Zoom capped at 18, satellite disappeared before that

**Files:** `map.py`, `static/enrichment-layers.js`

`create_comprehensive_map()` added every base tile layer (Google/ESRI/CartoDB)
via plain `folium.TileLayer(...)` with no `max_zoom`, so each defaulted to
Folium's built-in ceiling of 18 — while the cadastral overlay layers were
already configured with `maxZoom: 19` (pre-existing), which raised the *map's*
real ceiling to 19. Zoom 19 was a dead zone: no base imagery layer could
render there, so the map appeared to vanish, leaving only boundary lines
floating over nothing.

- **Fix:** raised the map's own `max_zoom` to 22 and gave every base/weather
  tile layer an explicit `max_native_zoom` matching that provider's real
  resolution ceiling (Google ~20, ESRI World Imagery 19, ESRI Terrain 13,
  CartoDB 20, OpenWeatherMap 19), with `max_zoom=22` on all of them. Past its
  native zoom, Leaflet now **upscales** the last real tile instead of
  rendering nothing. The cadastral overlay has no native-resolution ceiling of
  its own (rasterized on demand from vector geometry), so it stays crisp at
  any zoom and was bumped to `maxZoom: 22` to match.
- Only Google Satellite is now visible initially and weather layers start
  disabled. Folium otherwise mounted every configured tile layer at once,
  allowing slow or failed tiles from inactive providers to checkerboard over
  the intended default map.

## 4. Dark-mode CSS: invisible button text

**File:** `static/styles.css`

All dark-mode styling for the tool panels (`.tool-btn-ghost`, `.map-tool-btn`,
etc.) was written as `[data-theme="dark"] ...`, but the app only ever sets
`body.dark-mode` (`toggleDarkMode()` in `map.js`) — the attribute selector
never matched anything, so those rules were dead. Buttons fell through to a
much broader `body.dark-mode button:not(...)` catch-all that painted every
button solid blue (`#1a73e8`) with no matching text-color override, making
labels nearly unreadable.

- **Fix:** converted the dead selectors to `body.dark-mode` (careful,
  hand-verified regex — first automated attempt via `sed` briefly corrupted
  the descendant-combinator spacing and had to be reverted/redone). Narrowed
  the blanket button rule with `:not(.tool-btn-ghost):not(.map-tool-btn)` so
  it no longer overrides their intended navy/gray backgrounds.

## 5. Navbar stayed light in dark mode; theme icon never flipped

**Files:** `static/map.js`

Two disconnected dark-mode systems were running side by side:

- The installed `aecs4u_theme` package styles the navbar off
  `data-bs-theme="dark"` on `<html>` (its own `ThemeManager`, which looks for
  a `#themeToggle` button that doesn't exist on this page — the id here is
  `#themeToggleBtn` — so its click handler never attaches).
- This app's own `toggleDarkMode()` only ever toggled `body.dark-mode`, which
  drives all of `styles.css` but not the theme package's CSS.

Result: toggling dark mode changed the sidebar/map chrome but left the navbar
(and its gear/user icons) stuck light. Separately, `updateThemeIcon()` queried
`.theme-icon`, an element that doesn't exist (the real markup is
`<i id="themeIcon" class="fas fa-moon">`), so the moon/sun icon never updated
either.

- **Fix:** `enableDarkMode()`/`disableDarkMode()` now also set
  `data-bs-theme` on `<html>`, keeping both systems in sync from the one
  button. `updateThemeIcon()` now targets `#themeIcon` and toggles the
  `fa-moon`/`fa-sun` classes instead of writing emoji text into a selector
  that never matched.

## 6. Stub "View Tools" buttons

**File:** `static/folium-interface.js`

`toggleMiniMap()`, `toggleCoordinates()`, and `showPluginInfo()` were no-ops —
each just `console.log`'d a claim that the feature was "controlled by Folium
[plugin] configuration," but no such plugin was ever added server-side in
`map.py` (confirmed: only `MeasureControl`, `LocateControl`, and
`TreeLayerControl` are real). Clicking "Plugin Info" literally just shows:

> "Server-generated map uses Folium plugins. Check the map controls for
> available functionality."

— i.e. the exact unhelpful placeholder text, not real information.

- **Fix — MiniMap:** hand-rolled rather than using `folium.plugins.MiniMap` /
  `L.Control.MiniMap`, because the Folium-generated map runs on Folium's
  bundled Leaflet 1.9.3 while the page's own `L` is 1.9.4 — the plugin's
  internal `instanceof LatLngBounds` checks reject the main map's objects and
  throw. The replacement only passes primitive lat/lng/zoom numbers between
  the two Leaflet instances (safe), renders a small overview map with a
  synced viewport rectangle, bottom-right corner.
- **Fix — Coordinates:** live cursor lat/lng readout, bottom-left corner,
  updates on `mousemove`.
- **Fix — Plugin Info:** now reports which plugins are *actually* loaded
  instead of a canned sentence.
- **Fix — Reset View:** was previously acceptable but is now explicit —
  `map.fitBounds()` back to the Italy bounds constant instead of a vague
  fallback path.
- **Bonus:** added shareable permalinks. The backend already restores
  `?lat=&lng=&zoom=` on `GET /map` (`main.py`), but nothing ever *wrote* those
  params back to the URL. A `moveend` listener now does, via
  `history.replaceState`, so the current view is copy-pasteable. The backend
  parser accepts the same zoom 5–22 range as Leaflet, so overzoomed links also
  survive a refresh.

## 7. Map header was tall and part-broken

**File:** `static/styles.css`

Two separate `.map-header` rules existed in the stylesheet (one with
`padding: 8px`, a later one with `padding: 20px` that silently won). Fixed to
a single compact, flex, single-line header (title + status inline) —
consistent with the `#mapView .map-header { display: ... !important }`
"debug" override rule, which also needed updating from `block` to `flex` to
match.

## Known follow-ups / not addressed this session

- `uv.lock`, `routers/enrichment.py`, and `stats_service.py` were modified by
  a **different, concurrent session** during this work (confirmed: this
  session's own `git status` was clean at start, and none of those files were
  touched here). They add a Civil Protection criticality-bulletin layer and
  other enrichment work. Left untouched — reconcile with that session before
  committing.
- The broader feature gap vs. Zornade (nationwide always-on tiles, deep-link
  parcel selection, 17-section detail panel, etc.) is tracked separately in
  `ZORNADE_GAP_ANALYSIS.md` and wasn't the target of this session.
- MiniMap's tile source is plain OpenStreetMap regardless of the main map's
  active basemap/dark-mode state — cosmetic, not wired to match.
