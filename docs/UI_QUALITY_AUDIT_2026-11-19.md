# Land Registry — Design & Quality Audit

**Audit date:** 2026-11-19 · **Document review:** 2026-11-20 · **Target:** `http://localhost:8011` (dev server) · **Method:** Playwright MCP (Chromium), scripted sweeps plus manual screenshots
**Screenshots:** [`ui-audit-2026-11-19/`](ui-audit-2026-11-19/) (49 files, `<page>-<desktop|tablet|mobile>.png`)

## 1. Scope and limits

Audited: every reachable HTML route, at 1440×900, 768×1024 and 375×812; light and dark themes; English and Italian; keyboard order on `/map`; the layers panel; search; response headers; navigation timing; the accessibility tree, plus scripted checks for labels, names, landmarks, headings, duplicate ids, target sizes and contrast.

**Read this before the findings — the environment limits what could be judged:**

- **No cadastral data at audit time.** `/ready` returned **503** with `canonical_map: degraded`, `layers_available: 0`, and `cadastral-parcels` unavailable. Every map layer showed "Needs configuration" and municipality search returned 503. Parcel selection, parcel panels, the Typst dossier, tile rendering, layer colour modes, the attribute table and adjacency analysis **could not be exercised**. Findings on those are untested, not clean.
- **Signed in as a dev "Admin" user** (`admin@aecs4u.com`) via an existing session. The local email/password login was not submitted, because no credentials were available. Clerk runs with **development keys**.
- **No axe-core run.** Accessibility results are scripted heuristics, not a full WCAG audit. Contrast was only spot-measured on light-theme pages.
- Request failures reported as `net::ERR_ABORTED` during the route sweep came from my own navigations cancelling in-flight requests. They are **not** defects and are excluded.
- Side effects of the audit: I set `?lang=it` and toggled the theme. Both were reset (`?lang=en`, theme storage cleared). I also searched "Roma" once.

### Review update (2026-11-19)

- Corrected the summary count: **4 high, 9 medium and 9 low findings (22 total)**.
- A fresh unauthenticated HTTP probe returned **302** from `/map` and `/map-legacy`, redirecting to `/auth/login`; `/ready` still returns **503**. The route matrix, screenshots, timings and UI findings below describe the original authenticated browser session and were not re-run in this review.
- The screenshot directory contains all 49 images referenced below, but the repository-wide `*.png` ignore rule excludes them from Git. They are available in this working copy, not in a fresh checkout unless separately preserved.
- The current worktree marks `/map-legacy` deprecated in `main.py` and adds deprecation headers. This review confirmed the source change, not the authenticated route response; see §7.

### Implementation follow-up (2026-11-20; source-only)

This table records current worktree changes, not a new browser audit. No tests, fresh Playwright sweep, or deployment were run for this follow-up. Original route measurements and screenshots remain historical evidence; the missing PostGIS source and production Clerk configuration remain open.

| Findings | Current source status |
|---|---|
| H1 | **Addressed in source; remeasure.** The data-source page now constrains card widths, switches to one column on small screens, and scrolls the wide field table within its own container. Confirm the rendered page no longer clips at 375 px and 768 px. |
| H2 | **Partially addressed; environment blocker remains.** Unavailable map layers are disabled and removed from selected-layer URLs, and the map reports unavailable data. The configured source is still absent, so cadastral map functionality remains unavailable. |
| H3–H4; M5 | **Legacy route bypassed in source; response pending.** `/map-legacy` now redirects to the canonical map, removing the legacy UI and its inconsistent sign-in state from that route. The legacy template/code remains in the repository, and the redirect response was not rechecked in a browser during this follow-up. |
| M1 | **Addressed in source; visual recheck pending.** The shortlist starts closed, map panels close one another, and the map utility controls now precede MapLibre's injected controls in DOM order. Small-screen overlap and keyboard reachability still need a browser check. |
| M2 | **Partially addressed.** The cited map fields and hidden file inputs now have accessible names, and the utility column is a named group. Icon-only controls remain visually unlabeled, so first-use discoverability still needs review. |
| M3 | **Partially addressed.** Map strings and shortlist labels use the translation catalog; the legal page template has an English/Italian selector. Recheck rendered language and `<html lang>` behavior in both locales. |
| M4 | **Addressed in source; remeasure.** Map controls and shared page actions now have 44 px minimum target rules. The original target counts and contrast readings have not been rerun. |
| M6 | **Addressed in source; verify.** Logout now has a branded template with a main landmark, navigation, language links, and a styled action. |
| M7 | **Addressed in source; rendered check pending.** The stale “Needs configuration” translation key is removed. Map health now labels an unavailable source “Temporarily unavailable” during a full-source outage and “Not available right now” for an individual layer. Confirm the rendered copy in both locales after the source is deployed. |
| M8 | **Partially addressed; browser check pending.** The login template collapses the email form under Clerk when Clerk is enabled, applies shared auth styling, and pins Clerk assets. Rendered layout, contrast, and Clerk compatibility were not rechecked. |
| M9 | **Further addressed in source; route verification pending.** Swagger and ReDoc now have language metadata, a main landmark and h1, local favicons/styles, pinned SRI-protected JavaScript/CSS, and ReDoc no longer requests Google Fonts. Confirm schema rendering and resolve any remaining resource errors in an authenticated browser session after deployment. |
| L1–L4, L6 | **Addressed or reduced in source; verify.** Page titles and auth callback copy were revised; a manifest route, real multi-size ICO, SVG/PNG icon fallbacks, and Apple touch icon were added; versioned assets receive immutable caching; and the map offers a dark basemap. Browser rendering and installation behavior were not checked. |
| L5 | **Partially addressed in source; visual check pending.** Generated `Parcel <reference>` labels now show a localized parcel type and one reference line. The priority is a compact circular value with a full accessible name. Check the rendered hierarchy, narrow widths, and status-chip localization in a browser. |
| L7–L9 | **Open or partially addressed.** The API docs/resource error, cadastral-data route behavior, and measured brand contrast were not rechecked. |

## 2. Summary

| Severity | Count | Headlines |
|---|---|---|
| High | 4 | `/data-sources` content clipped on mobile/tablet; map unusable without data source (env); legacy map CSP violations; inconsistent signed-in state on legacy map |
| Medium | 9 | Overlapping floating panels; icon-only controls and unlabeled inputs; mixed-language UI; small touch targets; legacy map polish; unstyled logout page; login layout/contrast; `/docs` issues; "Needs configuration" wording |
| Low | 9 | Inconsistent titles, no manifest, no-cache on large assets, untranslated chips, and others |

**What is in good shape:** no document-level horizontal scrollbar was observed, though `/data-sources` still clips content (H1); strong security headers; skip link, landmarks, live regions and visible focus rings on `/map`; fast server (TTFB ≈ 225 ms, load ≈ 350 ms on `/map`); a friendly 404; dark mode and language persistence work; Italian coverage on `/map` is largely complete.

## 3. Route matrix

| Route | Status | Title | h1 | Notes |
|---|---|---|---|---|
| `/` | 200 → `/map` | Cadastral Map - Land Registry | 1 | Redirects signed-in users to the map |
| `/landing` | 200 | Land Registry Viewer | 1 | Clean; no console errors |
| `/map` | 200 | Cadastral Map - Land Registry | 1 | See §4 |
| `/map-legacy` | 200 | Land Registry Viewer | 1 | 2.4 s load, ~41 CSP errors per load. **Now deprecated** (§7) |
| `/auth/login`, `/auth/register` | 200 → `/map` when signed in | — | — | Unauthenticated login view reviewed separately (M8) |
| `/auth/callback` | 200 | Completamento accesso - AECS4U.com | **0** | Italian title; no h1 |
| `/auth/logout` | 200 | Sign out | 1 | Unstyled page (M6) |
| `/privacy` `/terms` `/help` `/contact` `/notifications` | 200 | `<Name> · Land Registry Viewer` | 1 | Fine; short copy |
| `/data-sources` | 200 | Data sources and reuse · AECS4U | 1 | **Clipped on mobile/tablet (H1)** |
| `/profile` `/settings` | 200 | `<Name> \| Land Registry` | 1 | Fine |
| `/docs` | 200 | Land Registry Viewer - API | **0** | No `lang`, no `main`; one 503 resource (L7) |
| `/nonexistent-page` | 404 | Page not found · Land Registry Viewer | 1 | Good error page |
| `/cadastral-data` | 404 JSON | — | — | Route is defined in `main.py` but returns 404 here (L8) |
| `/health` | 200 | — | — | Liveness only (by design) |
| `/ready` | **503** | — | — | Map source degraded (H2) |

## 4. Findings

### High

**H1 — `/data-sources` is clipped at 375 px and 768 px.** The `main.data-source-page` element measures **982 px** wide inside a 375 px viewport (`body.scrollWidth` 977–982) while `html` and `body` use `overflow-x: hidden`. The page cannot scroll sideways, so the right part of each source card is unreachable. The document-level overflow check (`scrollWidth − innerWidth`) reports 0, which hides the problem — only `body.scrollWidth` shows it. The page contains one table plus about 40 cards; the likely cause is a fixed-width table or card grid. Screenshots: `datasources-mobile.png`, `datasources-tablet.png`.
*Fix:* make the card grid `min-width: 0` / single column under 768 px, wrap or scroll the table, and drop `overflow-x: hidden` on `body` so future overflow is visible.

**H2 — Core map functionality is unavailable without the PostGIS source (environment; original observation).** With `/ready` at 503, municipality search returned **503** `{"detail":"Municipality search temporarily unavailable"}` (inline message "Search service unavailable; try again." was shown, which is good). The layers panel marked Regions, Provinces, Municipalities, Postal zones, Cadastral sheets and Cadastral parcels as "Needs configuration". **Regions and Cadastral parcels were still checked by default**, and the map showed only "Zoom in to see cadastral parcels" — a prompt that could not succeed because the layer was unavailable. Current source changes and production blockers are recorded in the implementation follow-up above.
*Fix:* uncheck/disable layers that are unavailable, and explain the unavailable state to the user instead of asking them to zoom in.

**H3 — Legacy map throws CSP violations on every load.** `/map-legacy` renders **127 inline event handlers** (`onclick`, `onchange`, …). The policy sets `script-src-attr 'unsafe-hashes' 'sha256-…'`, so only handlers whose hash is listed run. About **41 console errors** per load report blocked handlers ("Executing inline event handler violates … `script-src-attr`"). The theme toggle and language select work, but any controls without a listed hash are silently dead, and the console is unusable for real errors. The same CSP on `/map` is `script-src-attr 'none'`, so the two pages are held to different rules.
*Fix:* move handlers to `addEventListener` (the repo already prefers that on `/map`), then drop the hash allow-list.

**H4 — Signed-in state is inconsistent between maps.** While `/map`, `/profile` and `/settings` show the avatar menu for "Admin", `/map-legacy` shows a **"Sign In"** button in the header and no user menu. A signed-in user can reasonably conclude they were signed out.

### Medium

**M1 — Floating panels overlap on `/map`.**
- The **Shortlist** workspace panel is open by default, covering about a third of the map at 1440 px. Its toggle button ("Shortlist") sits on top of the panel's lower card, hiding the "Unfavourite" button, and the "Map ready" status pill is stacked beneath it. (`map-desktop.png`)
- Opening **Layers** while Shortlist is open shows two large panels at once. (`map-layers-desktop.png`)
- At 375 px the right-hand toolbar's last button overlaps the fullscreen and zoom controls, and the search placeholder is truncated ("Search municipality …"). (`map-mobile.png`)
- At 1440 px the toolbar overlays the basemap label area on the right.
*Fix:* default panels to closed (or open at most one), anchor the toggle outside the panel, and give the toolbar and MapLibre controls separate positions on small screens.

**M2 — Icon-only controls and unlabeled inputs on `/map`.**
- **7 visible buttons have no accessible name**, including `#parcelFinderSubmit`.
- **4 form fields have no label:** `#mapTableFilter`, `#mapAttributeValue`, `#mapDrawingsFile`, `#sisterBatchAppendFile`.
- The right toolbar has 11 icon-only buttons without visible text; sighted new users must hover to learn what each does.
*Positive:* map controls (zoom, fullscreen, north reset), the theme toggle, language and user menu are all named, and the tab order is logical (skip link → brand → language → theme → user → search → map controls).

**M3 — Mixed languages.**
- With `?lang=it` most of `/map` is translated, but the Shortlist panel still shows English "Total 3 · New: 3", the status value "All", the "New" chip, and "Open". (`map-italian-desktop.png`)
- `<html lang>` is `en` on pages whose content is partly or wholly Italian.
- The sign-in page mixes an Italian heading and form (hard-coded in the installed `aecs4u-auth` template) with English Clerk widget text and an English footer line.
- `/data-sources` served Italian copy under an English navigation.

**M4 — Touch targets below 44 × 44 px.** `/map` has 59 undersized interactive elements at 1440 px and 19 at 375 px; `/map-legacy` has about 55; `/data-sources` 15–24; `/profile` 14. Most are toolbar icons, chips and footer links.

**M5 — `/map-legacy` visual polish.**
- Header: the brand is truncated to "Land Reg…" at 1440 px while a lone hamburger is centred in the bar. (`legacy-desktop.png`)
- **6 inputs unlabeled** (`zoneName`, `zoneDescription`, `zoneTags`, `maxPriceFilter`, `tableSearch`, `tableFilterValue`).
- **82 text nodes below 11 px** (about 70 below 12 px) — the dark toolbar's labels are hard to read.
- About 20 red star pins are scattered over Italy with the status "No data loaded"; the page does not say what they are.
- The Google basemap shows labels in Arabic, Greek and Cyrillic.

**M6 — `/auth/logout` is an unstyled stub.** A bare serif heading with a default button, no header, no footer, no language switch, `main` landmark or meta description; the styles are inline in `auth_pages.py`. It looks like a different product from the rest of the site. (`auth-logout-desktop.png`)

**M7 — "Needs configuration" was operator wording shown to end users** in the layers panel next to every unavailable layer, with no hint of whether the visitor could do anything about it. The implementation follow-up above records the current source change: the stale translation key was removed, and layer health now supplies visitor-facing outage/unavailable copy. Rendered text still needs a browser check after deployment.

**M8 — Sign-in page layout and contrast** (reviewed after the shared-login change; `auth-login-desktop.png`).
- The local email/password form sits **outside** the white card that holds the Clerk widget, directly on the purple gradient.
- The "Registrati" link is blue on purple and nearly unreadable; the legal footer text is also weak.
- The local submit button is narrow and left-aligned while Clerk's buttons span the card.
- Most of this comes from the installed `aecs4u-auth` 1.26.0 template; the unreleased `login_base.html` in the sibling repos addresses it.

**M9 — `/docs`.** The Swagger page is 17 514 px tall, has no `lang`, no `main` landmark and no h1, loads Swagger UI from `cdn.jsdelivr.net/npm/swagger-ui-dist@5` (a floating major version), and logs one 503 resource error on load. The full-page screenshot appears to repeat its first sections after the schema list (`docs-desktop.png`); I did not confirm whether that is a capture artefact. Only three tags (webhooks, auth, authentication) were present in the DOM despite a 190 KB `openapi.json`.

### Low

- **L1 — Inconsistent `<title>` patterns:** "Cadastral Map - Land Registry", "Privacy · Land Registry Viewer", "Profile | Land Registry", "Data sources and reuse · AECS4U", "Land Registry Viewer - API", "Completamento accesso - AECS4U.com". Pick one separator and one brand name.
- **L2 — `/auth/callback`** has an Italian title, no h1 and no spinner text in the page's main language.
- **L3 — No web app manifest** (`/manifest.json` 404). `theme-color`, canonical, Open Graph, `robots.txt`, `sitemap.xml` and an SVG favicon are present.
- **L4 — Static assets use `Cache-Control: no-cache`.** `map-v2.js` is about 460 KB decoded and `styles.css` about 153 KB (both gzip-encoded); each load revalidates. Versioned URLs with long-lived caching would avoid that.
- **L5 — Shortlist card details:** the title and the reference repeat on two lines ("Parcel G273_012600.797" / "G273_012600.797"); a truncated "P—" badge on the right is unexplained; the "New" status chip is not localized.
  - **Source follow-up (2026-11-20):** generated default parcel labels are now split into a localized type title plus one reference line, and the priority badge is a compact value with a full accessible label. The historical screenshot has not been replaced; custom labels, status-chip translation, and rendered layout still need browser verification.
- **L6 — Dark theme:** the basemap stays light, which glares against dark panels; the toggle icon shows the current state (moon in dark) rather than the action.
- **L7 — `/docs` logs a 503 resource error** (see M9); `/ready` 503 also appears as a console error when opened in a tab.
- **L8 — `/cadastral-data`** is a defined route (private template) but returns a 404 JSON body, and the browser aborts the navigation. Either the data directory is missing in this environment or the route is stale.
- **L9 — Brand link contrast:** "Land Registry" in the header measures 3.45:1 on white. That passes only as large bold text; confirm the size.

## 5. Security headers (from `/map`)

Present: `Strict-Transport-Security` (1 year, preload), `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, COOP/CORP `same-origin`, a restrictive `Permissions-Policy`, and a nonce-based `script-src`. Observations:

- `style-src` allows `'unsafe-inline'`.
- The production header lists `http://127.0.0.1:5006` and `ws://127.0.0.1:5006` (the embedded Panel server) in `script-src` / `connect-src`.
- `script-src` allows `cdn.tailwindcss.com`, `unpkg.com`, `cdn.redoc.ly` and more even though `/map` appears not to need them.
- Basemap tiles come from OpenStreetMap, Google (`mt1.google.com`), CARTO and Esri; check the privacy page covers them.

## 6. Performance (signed in, `/map`, local)

| Metric | Value |
|---|---|
| TTFB | ≈ 225 ms |
| DOMContentLoaded / load | ≈ 338 ms / ≈ 353 ms |
| Requests | 43 |
| `/api/v1/map/layers` | 18 ms |
| `/api/v1/user/preferences`, `/saved-parcels`, `/cadastral-purchases`, `/cadastral-purchases/map` | ≈ 250–305 ms each |
| `/map-legacy` load | ≈ 2.4 s |

The four per-user calls each take about a quarter of a second; if they are issued one after another they delay the Shortlist panel and purchase markers. Transfer sizes were mostly cache hits, so payload sizes were not measured.

## 7. Change made during this audit

During the original audit, `/map-legacy` was **marked deprecated** ([main.py](../land_registry/main.py)): the route sets `deprecated=True` in OpenAPI and the response carries `Deprecation: true` and `Link: </map>; rel="successor-version"` (verified during that run). The current worktree still contains the source change. The page worked during the audit; `/map?legacy=1` and `/map?view=mapping` rendered the same shell without these headers, and `/map` still linked to the analysis page. A test in `tests/test_auth_pages_smoke.py` checks the OpenAPI flag. The unauthenticated probe in this review redirected before I could recheck the response headers.

## 8. Recommended order of work

1. Restore or explicitly configure the map source for the target environment; while it is unavailable, keep unavailable layers disabled and verify the visitor-facing health and zoom messages in both locales (H2, M7).
2. Fix `/data-sources` overflow (H1).
3. Close the Shortlist panel by default and fix the overlaps (M1).
4. Label the unlabeled inputs and name the icon buttons on `/map` and `/map-legacy` (M2, M5).
5. Remove inline handlers from the legacy page, then tighten its CSP (H3), or retire the page now that it is deprecated.
6. Show the signed-in user on the legacy page (H4).
7. Re-run this audit against an environment with a populated PostGIS source to cover parcels, dossier, tiles and tables.

## 9. Screenshot index

Key evidence: [`datasources-mobile.png`](ui-audit-2026-11-19/datasources-mobile.png), [`map-mobile.png`](ui-audit-2026-11-19/map-mobile.png), [`map-layers-desktop.png`](ui-audit-2026-11-19/map-layers-desktop.png), [`legacy-desktop.png`](ui-audit-2026-11-19/legacy-desktop.png), [`auth-login-desktop.png`](ui-audit-2026-11-19/auth-login-desktop.png), and [`docs-desktop.png`](ui-audit-2026-11-19/docs-desktop.png).

The remaining screenshots follow the naming groups `map-*`, `legacy-*`, `landing-*`, `privacy-*`, `terms-*`, `help-*`, `contact-*`, `notifications-*`, `datasources-*`, `profile-*`, `settings-*`, `notfound-*`, and `docs-*` (each at `desktop`, `tablet`, and `mobile` where captured), plus `map-search-desktop`, `map-dark-desktop`, `map-italian-desktop`, `profile-dark-desktop`, `landing-dark-desktop`, and `auth-logout-{desktop,tablet,mobile}`. PNGs are ignored by Git in this repository; see the review note in §1.
