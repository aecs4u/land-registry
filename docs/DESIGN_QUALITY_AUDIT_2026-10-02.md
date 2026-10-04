# Design and quality audit — 2026-10-02

**Target:** `http://localhost:8011` (dev server), every page reachable without credentials: `/` → `/map`, `/map-v2`, `/map-legacy`, `/landing`, `/auth/login`, `/auth/register`, `/map_table`, `/adjacency_table`, `/mapping_table`, `/docs`, and a 404.
**Branch:** `feature/map-srs-implementation` at `69d4fb7`, with the uncommitted working tree listed in `git status`.
**Tooling:** Playwright MCP (Chromium) at 1440×900, 768×1024 and 375×812, in light and dark themes, plus `curl` for routes, headers and API responses. Contrast, tab order, target size, landmarks and performance were measured with in-page scripts.
**Screenshots:** [`docs/design-audit-2026-10-02/`](design-audit-2026-10-02/)
**Previous audits:** [MAP_AUDIT_2026-09-26.md](MAP_AUDIT_2026-09-26.md), [MAP_AUDIT_2026-09-28.md](MAP_AUDIT_2026-09-28.md). Those covered `/map` layer data in depth. This audit covers the whole site: design consistency, accessibility, error handling, secondary pages, i18n and legal. Finding IDs are new and not reused from those audits.

---

## 1. Summary

The `/map` shell is well built. It has keyboard-reachable controls with visible focus, a skip link, landmarks, `aria-live` status regions, a working light/dark theme, a sidebar that collapses on narrow screens, and a first paint of about 250 ms. The problems are everywhere else, and in how `/map` behaves when its backend fails.

- **The map backend was unavailable for the whole audit.** All 15 catalog layers report `available: false`. Every tile request returns 503 `Canonical PostGIS map source unavailable`. Municipality search returns no results. `/health` still answers `{"status":"healthy"}` (C1).
- **The client hammers the failing backend without limit.** One idle Cesena view issued **4.2 requests/s**, and each of 6 tiles was retried 7 times in 10 s with no end. The retry cap in `map-v2.js` is reset by its own `sourcedata` handler (C2).
- **The UI describes the outage wrongly or not at all** (H1). Every layer row says "Not configured". Zooming to Cesena shows "No cadastral parcels published for this area yet (coverage: Veneto)". Searching "Cesena" says "No municipality or parcel match". The attribute table is an empty box. A stray empty status pill is visible. Verona at z16 shows 52 failed requests in 6 s and no message.
- **Basic site hygiene is missing** (H2). `/privacy`, `/terms`, `/help`, `/contact` and `/notifications` all return 404, although the cookie banner and every footer link to them.
- **Italian is not translated** (H3). `?lang=it` changes `<html lang>` and the switcher label and nothing else.
- **Secondary pages are broken or inconsistent.** The legacy map has no drawing tools, errors on every load and has overlapping controls on phones (H4, H7). Three table routes render a blank white page (H5). The landing page's primary button leads to a 404 and its statistics read 0 (H6). `GET /auth/logout` signs you out, which I triggered by accident (H8).
- **Brand and shell are inconsistent.** There are three page shells, three logos and at least four title patterns (M2).

| Severity | Count |
|---|---|
| Critical | 2 |
| High | 8 |
| Medium | 12 |
| Low | 7 |

---

## 2. Scope, method and caveats

1. Loaded each public route at 1440×900, then `/map` at 768×1024 and 375×812, and the legacy map at 375×812.
2. On `/map`:
   - Opened the layers panel and read every row.
   - Searched municipalities and chose a result.
   - Zoomed to Cesena (z14) and Verona (z16).
   - Opened the attribute table.
   - Toggled the theme.
   - Tabbed through the page.
   - Counted `/api/` requests over 10 s windows.
3. Ran in-page scripts for contrast (WCAG ratios against the effective background), tab order and focus indicators, target sizes, unlabeled controls, landmarks, headings, resource timings and SRI.
4. Probed routes and response headers with `curl`.

**Caveats:**

- **The backend was down, so data rendering was not exercised.** Parcel, sheet and OMI geometry, feature popups, parcel selection and the shortlist could not be tested. Those are covered by the 2026-09-26 and 2026-09-28 audits. Whether this is an environmental outage (missing DB connection for this process) or a regression is not determined here. I did not read the server's environment.
- **`/settings` was not audited authenticated.** The browser profile briefly held a Clerk **development-instance** session for `demo@aecs4u.com` (Clerk dev-browser handshake; not an app bug). I audited `/profile` while signed in. My link checker then fetched `/auth/logout`, which signed that session out (H8). I had no credentials to sign in again.
- Single run, Chromium only, dev server with `--reload`. Timings are indicative only.
- Contrast ratios come from computed styles. Elements with gradient backgrounds, such as the primary buttons, cannot be measured this way and were excluded.

---

## 3. Findings

### Critical

#### C1. Map data source unavailable; `/health` reports healthy
- `GET /api/v1/map/layers/health` returns `available: false` for all 15 layers.
- Tile requests return `503 {"detail":"Canonical PostGIS map source unavailable"}`: administrative boundaries at z5–z6, cadastral parcels at z14 and z16.
- `GET /api/v1/map/search?query=Cesena` returns `{"results":[], "source":"aecs4u-stats canonical map source"}` with HTTP 200.
- `GET /health` returns `{"status":"healthy","service":"land-registry"}` regardless. A load balancer or uptime check will treat an app that cannot serve its core function as healthy.
- **Fix:** make `/health` (or a separate `/ready`) report the map source state. Make search return 503 when the source is down rather than 200 with an empty list. Search should not look like "no match".

#### C2. Unbounded tile-retry storm against a failing backend
- Cesena z14, idle for 10 s: 42 responses, all 503, **4.2 req/s**, 6 unique tiles, each retried 7 times. A longer idle capture reached 487 geo-boundaries requests plus 42 parcel requests, with one tile requested 43 times.
- Verona z16: 52 failed parcel requests in 6 s.
- The retry cap exists (`land_registry/static/map-v2.js:1060-1065`, 2 attempts then "Some map data failed to load; pan or zoom to retry."). It never takes effect because the `sourcedata` handler at `map-v2.js:2349` runs `tileRetry.attempts[event.sourceId] = 0` on every `content` event. `source.setTiles()` (line 1074) emits that event again, so the counter resets after each retry. This is a likely cause, not confirmed by instrumenting the handler.
- The same pattern was recorded as C2 in the 2026-09-28 audit and its remediation log marks it fixed. It is still reproducible here.
- **Impact:** a degraded server receives sustained extra load per open tab, and a user who leaves the tab open generates traffic indefinitely.
- **Fix:** reset the counter only on a successful tile load (`event.tile` with no error), or key attempts by tile and apply exponential backoff with a hard cap. Add a regression test that simulates a 503 source.

### High

#### H1. The UI mislabels or hides the outage
All observed while C1 was active:

| Where | What the user sees | What is true |
|---|---|---|
| Layers panel, every row | "Not configured · …" (`map-v2.js:557`, shown when `payload.available === false`) | The source is down, not unconfigured |
| Cesena z14 | "No cadastral parcels published for this area yet (coverage: Veneto)" ([05](design-audit-2026-10-02/05-cesena-z14-no-data.png)) | Parcel tiles return 503. The Veneto bounds come from stale catalog metadata (`map-v2.js:529-535`); the working tree has moved parcels to national tables |
| Search "Cesena", no Enter | "No municipality or parcel match — press Enter to search places" ([03](design-audit-2026-10-02/03-search-cesena.png)) | The municipality service returned nothing because it is down |
| Verona z16 | No message at all | 52 failures in 6 s |
| Status pill | Empty, but visible as a small white rounded blob bottom-left in light and dark ([02](design-audit-2026-10-02/02-layers-panel-desktop.png)) | An empty element is rendered |
| Status pill, after load | "Map ready" while overlays are failing | Basemap is ready, data is not |
| Attribute table | An empty bordered box above the filter; no message, no row count ([17](design-audit-2026-10-02/17-attribute-table-verona-z16.png)) | No data and no error |
| Admin boundaries, checked | Nothing drawn at z6, checkbox still ticked | 503 on every tile |

**Fix:**
- Show a persistent, dismissible banner when a source is down ("Map data is temporarily unavailable").
- Use "Unavailable" rather than "Not configured" in the layer rows.
- Hide the pill when empty.
- Give the attribute table an empty/error state.
- Drive coverage notes from the catalog's live bounds, not a hard-coded "Veneto".

#### H2. Legal and support pages return 404
- `/privacy`, `/terms`, `/help`, `/contact` and `/notifications` all return 404 (JSON body, see M10).
- The cookie banner says "Read our Privacy Policy" and links to `/privacy`. The `/profile` footer links to Terms, Privacy, Help and Contact. The user menu links to `/notifications`.
- An Italian-market service that sets consent cookies and sends search text to a third party (M8) without a reachable privacy policy is a GDPR exposure.
- `/robots.txt` and `/sitemap.xml` also return 404 (L1).

#### H3. Italian locale is not translated
- `/map?lang=it` sets `<html lang="it">` and the switcher label to "Italiano". Navigation, layers, search, buttons and status text all remain English ([19](design-audit-2026-10-02/19-italian-locale.png)).
- Strings are hard-coded in `map-v2.js` and the templates, for example "Search municipality or parcel reference", "Sign in to use your shortlist", "Zoom in to see cadastral parcels", "Light / Dark / Satellite".
- Setting `lang="it"` on English text also makes screen readers apply the wrong pronunciation rules.
- The language switcher is therefore a control that does not work. Either translate or remove it.

#### H4. Legacy map (`/map-legacy`): errors on every load, drawing tools broken
Console on every load:
- `TypeError: L.Control.Draw is not a constructor` (`folium-interface.js:686`). Leaflet.draw is never loaded (no draw script in the page). The drawing and zone tools shown in the left toolbar and advertised on the landing page do not work.
- `GET /api/v1/get-regions/` returns **404 six to seven times per load** (`folium-interface.js` falls back to a DB source afterwards, then logs "Failed to load cadastral structure").
- `fitBounds failed: Bounds are not valid` warning.
- Basemap tiles come from `mt1.google.com`, Google's unofficial tile endpoint, not an API with terms and a key.
- Clerk loaded with development keys on every page (M12).
- No `<main>` landmark, no skip link (the `/map` shell has both).
- A different header and navigation from `/map` (no sidebar), so moving between the two feels like two apps ([10](design-audit-2026-10-02/10-map-legacy.png)).

#### H5. Table routes render a blank page
`/map_table`, `/adjacency_table` and `/mapping_table` return 200 with an entirely white page ([13-map_table](design-audit-2026-10-02/13-map_table.png)).
- Console: `WebSocket connection to 'ws://127.0.0.1:5006/dashboard/ws' failed: … 403`, then `Failed to connect to Bokeh server`.
- The Panel/Bokeh server address `127.0.0.1:5006` is embedded in page output and in the CSP (`connect-src … ws://127.0.0.1:5006`). That only works when the browser runs on the same machine as the server, so it cannot work in production or behind a proxy.
- There is no fallback or error text. The legacy page's "Table View / Adjacency / Mapping" tabs lead here.

#### H6. Landing page: broken primary action, stale content
- The primary "Cadastral Browser" button goes to `/cadastral-data`, which returns 404 ([09](design-audit-2026-10-02/09-landing-full.png)).
- All four statistic tiles read **0** (Regions, Provinces, Municipalities, Cadastral Files). No API request is made to populate them, so they look like static placeholders.
- The content is developer-facing and out of date: "Leaflet Map", "Folium Map Generation", "SpatiaLite databases", a "Tech Stack" chip list, "File Availability Cache". None describes the current MapLibre `/map` product.
- `/` redirects to `/map`, so this page is reachable only by typing `/landing`; the header "Land Registry" link redirects away from it.
- Title is "Land Registry Viewer"; the app shell uses "Cadastral Map - AECS4U".

#### H7. Legacy map on phones: controls overlap
At 375×812 ([11](design-audit-2026-10-02/11-map-legacy-mobile.png)):
- The "Map View / Table View / Adjacency / Mapping" tab strip overlaps the top toolbar.
- The search bar overlaps the zoom control, so "−" is hidden, and sits on top of the left drawing toolbar.
- There is no layout break for small screens. The `/map` page at the same width has no such overlap.

#### H8. `GET /auth/logout` logs the user out
- `curl` shows `/auth/logout` → 302 to `/auth/login`. My link checker issued a plain `fetch('/auth/logout')` and ended an authenticated session.
- A state-changing GET lets any third-party page log a user out with `<img src="https://…/auth/logout">`. Prefetching and link crawlers will also trigger it.
- **Fix:** make logout a `POST` with CSRF protection, or at least add a confirmation step.

### Medium

#### M1. Login and register pages
([12](design-audit-2026-10-02/12-login.png))
- Validation messages "Please provide a valid email" and "Password is required" are visible on a pristine form, before any input or submit. The inputs are `required` but carry no `aria-invalid` and no `aria-describedby` tying the message to the field.
- The header shows a "Sign In" button on the Sign In page.
- The logo is a 225×225 PNG with an opaque white background, rendered at about 225 px on a lavender card, so it reads as a pasted white square.
- At 1440×900 the form is cut off at "Don't have an account?" (document height 1103 px) with a coloured frame around the card that wastes space.
- The logo (a house with a gavel) suggests auctions, not cadastral data.

#### M2. Inconsistent brand, shell and titles
| Page | Shell | Name shown | `<title>` | Logo |
|---|---|---|---|---|
| `/map`, `/profile` | Header + collapsible sidebar | "AECS4U" | "Cadastral Map - AECS4U", "Profile - AECS4U" | none |
| `/landing`, `/map-legacy` | Header only | "Land Registry" | "Land Registry Viewer" | green grid icon |
| `/auth/*` | Header only | "Land Registry" | "Sign In", "Create Account" | gavel/house |
| `/map_table` etc. | none | n/a | "Map Table - Land Registry Viewer" | none |

Three typefaces are in play: Inter (`/map`), `system-ui` (`/landing`) and the default sans (auth).

#### M3. Cookie consent dialog is not an accessible dialog
- The element is `div#cookieConsent.cookie-modal-overlay` with no `role="dialog"`, no `aria-modal`, no `aria-labelledby`.
- On first load focus stays on `<body>`. The page behind remains reachable by keyboard, so a keyboard user can tab past the modal.
- The "Read our Privacy Policy" link leads to a 404 (H2).
- A positive: Decline persists (`cookieConsent` in `localStorage`, `cookie_consent` cookie), and the banner does not reappear.

#### M4. Contrast failures
Measured on computed styles:
| Element | Theme | Ratio | Required |
|---|---|---|---|
| "Layers" toggle in its pressed/active state (`.layers-toggle`) | Dark | **1.71** | 4.5 |
| Same | Light | 3.27 | 4.5 |
| Basemap "Light" segment label (12 px) | Light | 4.12 | 4.5 |
| "Points of Interest" toggle (`.map-live-toggle`, 12 px) | Dark | 4.14 | 4.5 |

In dark mode the active Layers button is a pale grey chip with blue text ([06](design-audit-2026-10-02/06-dark-theme-layers.png)), and the attribution pill stays white against the dark basemap.

#### M5. Layers panel is dense and exposes internals
- Each of 15 rows carries a 3–4 line meta string such as "Not configured · Partial coverage · Veneto only · Date reported per feature · aecs4u-stats PostgreSQL/PostGIS".
- "aecs4u-stats PostgreSQL/PostGIS" is repeated on every row and is meaningless to users.
- "Date reported per feature" and "Update date not reported" are repeated provenance notes that never vary in usefulness.
- "Upstream source currently contains 1,523 of the expected 2,847 sections" is a data-engineering note.
- Consider a one-line status plus a collapsible "About this layer" block.
- The layers panel at 768×1024 covers the "Zoom in to see cadastral parcels" hint and the attribution ([18](design-audit-2026-10-02/18-tablet-768-layers.png)).

#### M6. Layers panel: unclear default state
- "Cadastral parcels" is ticked by default at z6, where nothing can render, while the only visible hint is a small pill at the bottom of the map.
- After zooming to z14 via search, "Administrative boundaries" silently unticks (substitute layer logic, `map-v2.js:525-526`). A checkbox changing by itself, with no announcement, will confuse screen-reader and keyboard users.

#### M7. Search UX
- After choosing a result the input shows a long label whose first letter is clipped ("esena, Emilia-Romagna, 47521-47522, Italy", [05](design-audit-2026-10-02/05-cesena-z14-no-data.png)).
- The helper text under the field is cramped against the field border ([03](design-audit-2026-10-02/03-search-cesena.png)).
- Placeholder truncates at 375 px ("Search municipality o…").
- Result groups carry the kind only as grey text ("Place", "County").

#### M8. Search text is sent to a third party without disclosure
Pressing Enter issues `GET https://nominatim.openstreetmap.org/search?…&q=Cesena` from the browser (also in CSP `connect-src`). There is no notice in the UI, and the privacy policy does not exist (H2). Nominatim's usage policy also restricts autocomplete-style and heavy use.

#### M9. Attribute table panel accessibility and states
- `input#mapTableFilter` has no label, `aria-label` or `title`, only a placeholder.
- `ASIDE#mapTableCard` and the other panels (`mapLayersCard`, `parcelShortlistCard`, `directParcelPanel`) have no accessible name, and none is a `dialog`. Opening the table does not move focus into it. (`#mapLayersCard` was not checked for focus handling.)
- "Prev" and "Next" show no page number or total.
- No empty or error state (H1).

#### M10. Browser 404s are raw JSON
`GET /does-not-exist` returns `{"detail":"Not Found"}` with no HTML, title or navigation ([13-notfound](design-audit-2026-10-02/13-notfound.png)). Page routes should return an HTML error page, with JSON only for `/api/*`.

#### M11. `/map-v2` duplicates `/map`
`/map-v2` serves the same page and title, and rewrites the URL to `/map-v2?lat=…`. Two indexable URLs for one page; make one redirect to the other or add `rel="canonical"`.

#### M12. Security and supply-chain hygiene
- CSP `script-src` includes `'unsafe-inline' 'unsafe-eval'` and `blob:`, which largely removes XSS protection.
- No Subresource Integrity on third-party assets: `unpkg.com/maplibre-gl@4.7.1` (JS and CSS), `cdn.jsdelivr.net/…/topojson-client@3`, Google Fonts CSS. The Clerk script loads from `@latest` on the auth pages (`clerk.browser.js` via `clerk-js@latest`) and from a pinned `6.30.3` on `/map-legacy`.
- Clerk logs "loaded with development keys" on every page that loads it, including `/auth/login` and `/map-legacy`. Confirm production uses live keys.
- Interactive API docs at `/docs` and `/openapi.json` (127 KB) are public.
- Present and fine: HSTS with preload, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy`.

### Low

- **L1.** No `<meta name="description">` on any page, no Open Graph or canonical tags, and `/robots.txt` and `/sitemap.xml` return 404. Titles are not unique per view ("Cadastral Map - AECS4U" for `/map` and `/map-v2`).
- **L2.** The Light Gray basemap labels in English ("Turin", "Genoa", "Florence", "Vatican City") for an Italian-market app. Italian exonyms would be expected (Torino, Genova, Firenze, Città del Vaticano).
- **L3.** 13×13 px layer checkboxes on desktop are below the 24×24 px WCAG 2.2 target-size minimum (2.5.8). The label text is also clickable, which mitigates this. On mobile they render at about 24 px.
- **L4.** On `/profile` the avatar card shows the email twice (heading and subtitle) and the Account card shows "Name —" with no value.
- **L5.** Tab order on `/map` puts the map canvas and its controls before the main action buttons ("Locate me", "Layers"). The map canvas is a tab stop with no description of what it does with the keyboard.
- **L6.** The "Layers" and "Sign in to use your shortlist" buttons wrap to two lines at 375 px, and the two-line attribution is tall.
- **L7.** On the legacy map "Table View" wraps to two lines in the tab strip and map-label languages are mixed (Cyrillic, Greek, Arabic, Latin).

---

## 4. What worked

- **Keyboard and focus:** the first Tab stop is "Skip to main content". All 30 stops tested have a visible outline or shadow. Layers panel contents are removed from the tab order when closed.
- **Semantics on `/map`:** one `h1`, a labelled `region` for the map, `search` landmark, `combobox` search with `listbox` options, `aria-live="polite"` status regions, every icon button labelled, no images missing `alt`, no unlabeled controls other than M9.
- **Responsive:** no horizontal scroll at 375, 768 or 1440 on `/map`. The sidebar auto-collapses at 768. Map actions stay reachable at phone width ([07](design-audit-2026-10-02/07-mobile-first-load.png), [08](design-audit-2026-10-02/08-mobile-layers.png)).
- **Theme:** the light/dark toggle persists, switches the basemap with the theme, and the dark palette is cohesive ([06](design-audit-2026-10-02/06-dark-theme-layers.png)).
- **Performance:** DOMContentLoaded about 133 ms and first contentful paint about 244 ms on a warm local cache. `/map` HTML is 32 KB with 428 DOM nodes and no resource over 150 KB.
- **Cookie consent:** Decline is honoured and persisted.
- **Profile page:** clear layout, helpful empty state ("No saved parcels yet… Find a parcel") ([15](design-audit-2026-10-02/15-profile.png)).
- **Search fallback:** when the canonical source is empty, Enter falls through to Nominatim and results are correct and selectable (subject to M8).

---

## 5. Recommended order of work

1. **C2** — fix the retry counter reset and add a backoff test. It is a few lines and removes the sustained load.
2. **C1 / H1** — surface source health in `/health` and in the UI: one banner, "Unavailable" wording, an attribute-table empty state, no empty pill, search 503 instead of an empty 200.
3. **H2** — add privacy, terms, help and contact pages, or remove the links. Do this before inviting further users.
4. **H8** — change logout to POST.
5. **H6 / H5 / H4** — fix or remove the landing CTA and stats, the Panel table routes, and the legacy map's missing Leaflet.draw and `get-regions` calls. If `/map-legacy` is going away, redirect it and delete the dead routes.
6. **H3** — translate the map UI, or hide the language switcher until it works.
7. **M2 / M1 / M3 / M4** — unify shell, title and logo; fix pristine-form validation text; make the cookie banner a real dialog; fix the three contrast failures.
8. **M5–M12, L1–L7** — as capacity allows.

## 6. Retest checklist

- [ ] Backend restored: `/api/v1/map/layers/health` reports available and the Cesena (z14) and Verona (z16) parcel tiles return 200.
- [ ] With the backend stopped: at most a few requests per tile, then one visible message.
- [ ] `/privacy`, `/terms`, `/help` and `/contact` return 200 pages.
- [ ] `?lang=it` renders Italian on `/map`, `/profile` and auth pages.
- [ ] `/map_table`, `/adjacency_table` and `/mapping_table` render a table, or a clear error.
- [ ] `/settings` audited while signed in (not covered here).
- [ ] Parcel selection, popup, shortlist and sheet/OMI layers re-checked at z14–z18 with data present.

## 7. Screenshot index

| File | Shows |
|---|---|
| [01](design-audit-2026-10-02/01-map-desktop-dark.png) | `/map` first load with cookie dialog |
| [02](design-audit-2026-10-02/02-layers-panel-desktop.png) | Layers panel, "Not configured" rows, empty status pill |
| [03](design-audit-2026-10-02/03-search-cesena.png) | Search: "No municipality or parcel match" |
| [04](design-audit-2026-10-02/04-search-enter-result.png) | Search after Enter (Nominatim fallback) |
| [05](design-audit-2026-10-02/05-cesena-z14-no-data.png) | Cesena z14: misleading Veneto note |
| [06](design-audit-2026-10-02/06-dark-theme-layers.png) | Dark theme, low-contrast Layers button |
| [07](design-audit-2026-10-02/07-mobile-first-load.png), [08](design-audit-2026-10-02/08-mobile-layers.png) | `/map` at 375×812 |
| [09](design-audit-2026-10-02/09-landing-full.png) | `/landing` full page |
| [10](design-audit-2026-10-02/10-map-legacy.png), [11](design-audit-2026-10-02/11-map-legacy-mobile.png) | `/map-legacy` desktop and phone |
| [12](design-audit-2026-10-02/12-login.png) | Sign-in page |
| [13-*](design-audit-2026-10-02/) | `/map-v2`, `/map_table`, `/adjacency_table`, `/mapping_table`, `/docs`, 404 |
| [14](design-audit-2026-10-02/14-map-after-register-demo-user.png) | `/map` with the Clerk dev session |
| [15](design-audit-2026-10-02/15-profile.png) | `/profile` signed in |
| [16](design-audit-2026-10-02/16-register.png) | Create-account page |
| [17](design-audit-2026-10-02/17-attribute-table-verona-z16.png) | Attribute table with no data and no message |
| [18](design-audit-2026-10-02/18-tablet-768-layers.png) | `/map` at 768×1024 |
| [19](design-audit-2026-10-02/19-italian-locale.png) | `?lang=it`, still English |

## 8. Remediation log

**2026-10-02 — `/map` failure handling (C2, part of H1).** Changes in `land_registry/static/map-v2.js` and `map-v2.css`; verified in the browser against the same failing backend.

| Finding | Change | Result |
|---|---|---|
| C2 retry storm | The `sourcedata` handler resets the retry counter only for a tile that actually loaded (`event.tile?.state === 'loaded'`). `setTiles()` fires its own `content` event, which used to zero the counter. | Idle Cesena z14: 24 requests in 12 s, then **0** in the next 10 s (before: 4.2/s, unbounded). |
| H1 silent failure | New source-down banner with a Retry button, driven by the health endpoint and by exhausted tile retries. Retry starts one bounded round (36 requests, then 0 in the next 10 s). | Banner shown at Cesena z14 and Verona z16 ([20](design-audit-2026-10-02/20-fix-source-banner.png), [21](design-audit-2026-10-02/21-fix-table-and-layers.png)). |
| H1 "Not configured" | Layer rows read "Temporarily unavailable" when the whole source is down. | Verified in the layers panel. |
| H1 "No parcels published (coverage: Veneto)" | Not claimed while the source is down. | Cesena z14 shows the banner only. |
| H1 search wording | Says municipality and parcel search is unavailable while the source is down. | Code path only; not exercised in the browser. |
| H1 attribute table | The table row and status show the error ("Layer data could not be loaded…") or "No layers available" instead of "No features in the current view". | Verified at Verona z16. |
| H1 empty status pill | `.map-status-pill:empty { display: none; }` | Computed `display: none`. |

Still open from C1 and H1: `/health` still reports healthy and municipality search still answers 200 with an empty list when the source is down (both backend). Tests: 3 new contract tests in `tests/test_map_v2_contract.py`; the three map contract suites (59 tests) pass. The tests check source strings, so the browser runs above are the behavioural evidence.
