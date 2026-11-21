# Design & Quality Audit — land-registry.aecs4u.com

- **Audit date:** 2026-11-19 · **Document review:** 2026-11-20
- **Method:** Original Playwright MCP (Chromium) audit: unauthenticated pass followed by an authenticated pass (login done manually by the owner). Viewports 375×812, 768×1024, 1024×640, 1440×900. Light and dark theme. This review rechecked unauthenticated HTTP responses and source/policy details; it did not repeat the authenticated browser sweep.
- **Scope:** `/landing`, `/auth/login`, `/auth/register`, `/map`, `/map-legacy` (HTTP-level only), `/privacy`, `/terms`, `/help`, `/contact`, `/data-sources`, `/docs`, health/ready endpoints, `robots.txt`, `sitemap.xml`, response headers, console and network behaviour.
- **Screenshots:** 18 local files under `.playwright-mcp/`. Key evidence: [map mobile](../.playwright-mcp/audit_map_mobile.png), [map layers](../.playwright-mcp/audit_map_layers.png), [transient tablet 503](../.playwright-mcp/audit_map_tablet.png), [login](../.playwright-mcp/audit_auth_login-desktop.png). The repository-wide `*.png` ignore rule excludes these files from Git.
- **Not covered:** parcel selection/dossier PDF, shortlist editing, SISTER purchases (Stripe), file upload on `/map-legacy`, drawing tools, data tables. These were unreachable or out of safe scope because **every data layer is currently down** (see C1). Re-run the audit once the data source is restored.
- **Caveat:** Findings are from observation of the live site; causes marked *likely* are inferences, not verified against server logs.
- **Accessibility caveat:** No axe-core or formal WCAG conformance run was performed. Target-size checks and contrast readings are audit heuristics/spot checks.

### Review update (2026-11-19)

- **Live HTTP recheck:** `/ready` still returns **503** with `canonical_map` and `map_search` degraded; `/api/v1/map/search?query=Roma` returns **503**. `/health` and `/auth/login` return **200**. Unauthenticated `/map`, `/privacy`, `/terms`, `/help`, `/contact`, and `/data-sources` redirect to `/auth/login`.
- The referenced missing theme stylesheet still returns **404 `application/json`**. `robots.txt` still disallows crawling except `/landing` and `/static/`, and points to an HTTP sitemap whose only URL is `http://land-registry.aecs4u.com/landing`.
- **Source review at that point:** the worktree workflow treated `/ready` as a warning, had a `pk_test` publishable-key fallback, and used the `{s}.tile.openstreetmap.org` hostname pattern. Those observations describe the pre-response source snapshot; see the implementation follow-up below. They did not establish which environment values or source revision were active in Cloud Run.
- The screenshots and detailed UI findings remain from the original authenticated Playwright pass. They are present locally but ignored by Git, so a fresh checkout will not contain the images.

### Production recheck (2026-11-20)

- Public production checks returned `/ready` **503**, `/auth/login` **200**, and `/privacy` plus `/data-sources` **302**. The login HTML still loads `clerk-js@latest`, renders a development publishable key, and contains the old app-token `localStorage` writes. This confirms the current source fixes for C2–C4 have not reached production. No secret values were read.
- A production `HEAD` request confirmed the missing map-detail stylesheet still returns **404 `application/json`**. Its response headers also show the old broad CSP (`'unsafe-inline'`, `blob:`, unused CDN/API origins) and `X-Frame-Options: DENY`, which conflicts with `frame-ancestors 'self'`.
- A read-only Secret Manager metadata query matched `LAND_REGISTRY_CLERK_SECRET_KEY` and did not find `LAND_REGISTRY_CADASTRAL_POSTGRES_DSN`. No secret values were read. This confirms the map DSN blocker. The public login proves the rendered publishable key is for a development instance; the production secret-key value and expected JWKS configuration remain unverified.
- Production minimum instances remain **0**, per the user's choice. No Cloud Run revision was created in this work.
- A status-only production refresh later on 2026-11-20 returned `/ready` **503**, `/auth/login` **200**, and `/privacy`, `/terms`, `/help`, `/contact`, and `/data-sources` **302**. The map-detail stylesheet and `/.well-known/security.txt` returned **404**; `/robots.txt` and `/sitemap.xml` returned **200**. Response bodies were not fetched in this refresh. A current Secret Manager metadata refresh could not run because the local gcloud account requires interactive reauthentication; the earlier metadata query remains the last successful evidence. No secret values were read.

### Production refresh (2026-11-20; connected browser)

- `/ready` returned **503** with `reason: map source not configured`. `/.well-known/security.txt` returned **404** with an `application/json` response.
- `/auth/login?lang=en` returned **200** but still has an empty `<title>`, a development Clerk publishable key, `clerk-js@latest`, and app-owned `localStorage` token writes. Its CSP still includes the former broad `blob:`, `unsafe-inline`, Tailwind, Google Maps and other CDN permissions; `X-Frame-Options` remains `DENY`.
- Direct navigation to `/privacy` ended at `/auth/login?next=%2Fprivacy`, confirming that the legal page is still gated. The map-detail stylesheet returned **404 `application/json`**. `/robots.txt` and `/sitemap.xml` returned **200**, but the robots sitemap URL and sitemap locations still use `http://`.
- A current Secret Manager metadata refresh again requires interactive gcloud reauthentication, so the map DSN and Clerk secret metadata could not be rechecked. No secret values were read. No production revision was deployed.

### Implementation follow-up (2026-11-20; source-only)

Statuses below describe the current worktree. These edits have **not** been deployed. No automated test suite or fresh Playwright sweep was run. Static parsing, English/Italian message-catalog checks, and `git diff --check` passed; a direct HTML smoke check confirmed `/docs` and `/redoc` render language metadata, a main landmark and h1, local styles, SRI, and no Google Fonts request. The Italian Clerk module-preload edit was made after that smoke check and has not been rendered or browser-verified. These checks do not establish end-to-end behavior or production response. Finding counts and screenshots remain the original audit snapshot.

| Findings | Status in this worktree |
|---|---|
| C1 | **Blocked on production data configuration.** The workflow requires the dedicated `LAND_REGISTRY_CADASTRAL_POSTGRES_DSN`, binds it as `CADASTRAL_POSTGRES_DSN`, deploys a tagged candidate without production traffic, and promotes it only after candidate `/health`, `/ready`, and map-health checks pass. The last production probe still reports `/ready` 503 with `map source not configured`; the map is not restored. |
| C2 | **Production configuration remains unresolved.** The public login page still renders a development Clerk publishable key. Secret Manager metadata contains a `LAND_REGISTRY_CLERK_SECRET_KEY` entry, but its value was not read; whether it is a production secret and whether the configured JWKS URL matches it remain unverified. The deployment workflow rejects development keys/domains and requires the live pair plus HTTPS JWKS URL. |
| C3–C4 | **Addressed in source; still failing in production.** The current source removes app-owned tokens from JavaScript-accessible storage, and legal, help, contact, and data-source routes are public. The production login HTML still contains the old `localStorage` token writes; `/privacy` and `/data-sources` still redirect to login. Cookie behavior needs a browser check after deployment. |
| H1–H4, H6–H8 | **Addressed in source; still failing or unverified in production.** Map readiness now follows layer health; unavailable layers are disabled and removed from selected-layer URLs; search has a retry plus a direct user-triggered place-search fallback when the local search is unavailable; the welcome guide persists on open and uses a native modal with a mobile bottom-sheet presentation; the shortlist starts closed and map panels are mutually exclusive; `/map-legacy` redirects to `/map`; browser 404/500/503 responses have branded pages; the stale stylesheet link was removed; and generated public URLs use the configured HTTPS origin. Nominatim is not queried as the visitor types because the [public policy prohibits autocomplete](https://operations.osmfoundation.org/policies/nominatim/). The refreshed production probe still returns the old stylesheet 404 and the public-page redirects; production headers and the security.txt route remain on the prior revision. |
| H5 | **Partially addressed by design.** In-app errors render a branded, retryable 503 page. Minimum instances remain **0**, as requested. A Cloud Run platform response can still arrive before the app handles a cold-start request, so the app cannot brand every 503. |
| H9; M1–M6, M13; L3–L8, L10–L12 | **Partially addressed; remeasure.** Source edits cover target sizing, accessible names, login flow and metadata, localization, focus affordances, contrast, URL validation, and map/status layout. The duplicate email form is collapsed when Clerk is active. The Clerk wait is now limited to eight seconds and a failure opens the email/password fallback. Legal pages now have an English/Italian selector and Italian translations in source; the Contact page now links to the support address published by [AECS4U](https://realestates.aecs4u.com/help), while the verified security reporting contact is configured separately in `security.txt` source, pending deployment. For M3, generated `Parcel <reference>` shortlist labels now use a localized type title and one reference line; the priority badge is compact and has an accessible full label. The map utility controls now precede MapLibre's injected controls in DOM order. The welcome guide now uses a native modal dialog, including its mobile bottom-sheet presentation, so browser focus containment and return-focus are provided by the dialog API. Each map template contains 11 polite live regions, and the map utility stack is a named group. Remote Inter/Lexend loading is disabled in the shared theme, but the measured type-size scale, target sizes, contrast ratios, rendered focus order, translated output, and small-screen behavior still need browser remeasurement. |
| M7 | **Further addressed in source; production still serves the previous revision.** ClerkJS/UI, MapLibre, TopoJSON, Bootstrap, FontAwesome, Swagger UI, ReDoc, and the Italian Clerk localization module now use exact versions with SRI where their active page templates support it. Italian Clerk localization now preloads the dedicated 61 KB `it-IT.mjs` module with SHA-384 integrity instead of importing the 4 MB all-locales bundle. Shared-theme Google Fonts loading is disabled, ReDoc no longer requests Google Fonts, and the privacy text now names the active asset CDNs and states that external font requests are disabled. Legacy Leaflet code is retired from the public route; dynamic third-party asset requests and module-preload integrity reuse still need a post-deploy browser check. |
| M8 | **Partially addressed in source; production still serves the old policy.** The current default script policy drops blanket inline/eval and blob permission, removes the unused Tailwind and Google Maps JavaScript API origins, adds `object-src 'none'`, adds `upgrade-insecure-requests` outside debug mode, and aligns X-Frame-Options with `frame-ancestors 'self'`. `style-src` and `font-src` no longer allow Google Fonts; `connect-src` permits the canonical OSM tile host and `mt1.google.com` for selectable Google basemaps. Inline styles and broad HTTPS image sources remain for current UI/map providers; no CSP reporting endpoint is configured. Production response headers have not received these changes. |
| M9 | **Partially addressed; traffic behavior open.** The primary OSM source uses the canonical tile URL and visible attribution. First-load request counts, browser Referer, cache behavior, and the observed z9 overfetch have not been remeasured. Do not assume the request volume is fixed. |
| M10–M12, M14; L1, L3, L9 | **Addressed or reduced in source; verify after deployment.** Versioned assets receive immutable caching while HTML is private/no-store; search accepts `query=` and `q=`, `/map/catalog` aliases the layer catalog, invalid map coordinates/zoom use a safe default with visitor feedback, and metrics documentation now states the opt-in privacy gate; the public landing page no longer links to private API docs. The landing language selector uses full language names and footer links have 44 px minimum height. Pages now have SVG and PNG favicon options, an Apple touch icon, a real multi-size ICO response, and PNG manifest icons. Metrics remain intentionally hidden (404) unless `MAP_PUBLIC_METRICS` is enabled. The account display name still requires Clerk profile verification; measured brand contrast has not been rechecked. |
| L2 | **Addressed in source; production unverified.** The owner supplied the verified `security@aecs4u.com` reporting address. The `/.well-known/security.txt` route emits a `Contact` mailto field, an `Expires` value 180 days ahead, supported languages, and a canonical URL. `robots.txt` explicitly allows this path. The source change has not been deployed, so the original production 404 remains the last live evidence. |

The latest connected-browser production recheck on 2026-11-20 returned `/ready` **503** (`map source not configured`), `/auth/login` **200**, and `/.well-known/security.txt` **404**. `/privacy` still redirects to login, and the map-detail stylesheet still returns 404 `application/json`. The login body confirmed the development Clerk key, `@latest` asset, and old `localStorage` token code; the production CSP and X-Frame-Options remain unchanged. No Cloud Run revision was created. Worktree changes remain pending deployment.

Static checks on the current source passed: JavaScript syntax (`node --check`), Python AST parsing, Babel parsing of both PO catalogs, `git diff --check`, and trailing-whitespace scans for the updated files. These checks do not verify rendered UI, auth-cookie behavior, map tile traffic, or the live policy headers. The connected-browser production refresh above reconfirms that the source-only fixes have not reached the live revision.

Severity: **Critical** = broken or legal/security exposure · **High** = significant user impact · **Medium** = clear defect · **Low** = polish.

---

## Summary

| Severity | Count |
|---|---|
| Critical | 4 |
| High | 9 |
| Medium | 14 |
| Low | 12 |
| **Total** | **39** |

At the time of the live audit, **the production map had no data**: cadastral/administrative layers, tiles and search returned 503 while the UI said "Map ready". The last production probe still returned 503; source changes now suppress the false ready state but have not been deployed. The other major audit risks were development Clerk keys, app tokens in `localStorage`, gated legal pages, and the unstable mobile first-run experience.

---

## Critical

### C1. Map data source not configured / all data layers unavailable
- `GET /ready` → **503** `reason: map source not configured`. The live recheck also reports both `canonical_map` and `map_search` as degraded.
- `GET /api/v1/map/search?query=Roma` → **503** `Map search source unavailable`.
- Vector tiles `GET /api/v1/tiles/map-layers/geo-boundaries/{z}/{x}/{y}.pbf` → **503** for every tile.
- In the Layers panel, 20 of the data layers show "Temporarily unavailable"; only the client-side POI-type toggles remain.
- The site is effectively a basemap viewer. CLAUDE.md documents `/ready` as the deploy signal; at audit time the deploy workflow treated it only as a *warning*, so this shipped.
- **Fix:** configure the PostGIS map source secret on Cloud Run (see recent commits `ee309b9`, `2dfefaf`: shared-DSN fallback was disabled by default, which is the *likely* trigger). Make `/ready` failure block the deploy (or at least page someone).

### C2. Clerk **development** keys in production
- Login page loads `pk_test_…` publishable key; console warns "Clerk has been loaded with development keys… strict usage limits".
- The page shows a "Development mode" badge to end users.
- Dev instances are rate-limited, have no production SLA, and accounts are not portable to a production instance.
- **Fix:** create a Clerk production instance, set the `pk_live_` key and matching backend secret.

### C3. Session tokens stored in `localStorage`
- The browser storage inspection found `access_token` and `refresh_token` entries in `localStorage` (plus Clerk keys). Same-origin injected JavaScript can read browser storage. Token lifetime and rotation were not measured, so the refresh token should not be described as long-lived without checking its issuer and expiry.
- **Fix:** use a server-owned `HttpOnly; Secure; SameSite=Lax` session where the auth flow supports it, remove app-owned tokens from JavaScript-accessible storage, and verify the refresh flow and cookie behavior.

### C4. Legal pages gated behind login (unauthenticated check)
- Unauthenticated `GET /privacy`, `/terms`, `/help`, `/contact` → **302 → `/auth/login`** with a JSON body `{"detail":"Authentication required"}`.
- The login and register pages link to "Terms of Service" and "Privacy Policy" — a user must accept terms they cannot read before signing up (GDPR transparency and consent problem). `/landing` also links `/privacy`.
- **Fix:** make `/privacy`, `/terms`, `/help`, `/contact`, `/data-sources` public.
- Also: the 302 response carries `application/json`, so a browser following it is fine, but crawlers and `curl` see a JSON redirect body.

---

## High

### H1. UI states contradict each other
- Status chip says **"Map ready"** while a red banner says "Map data is temporarily unavailable".
- Layers "Regions" and "Cadastral parcels" are **checked** but labelled "Temporarily unavailable"; the URL still advertises `layers=cadastral-parcels,geo-boundaries`.
- Disabled layers are still interactive checkboxes (the help text says "a greyed layer has no data", but they aren't visibly greyed).
- **Fix:** derive "ready" from actual layer/tile health; force-uncheck and `disabled` unavailable layers; strip unavailable layers from the URL.

### H2. Search fails silently into a dead end
- Four test queries (`Roma`, `G273`, a nonsense string, `Roma foglio 100 particella 20`) all returned 503 and a small grey "Search service unavailable; try again." line. No retry action, no results, 4 console errors.
- The helper text promises an OpenStreetMap Nominatim fallback, but only on Enter, and it is unclear whether it works when the primary source is down.
- **Fix:** surface a retry button, offer the Nominatim fallback automatically when the local source is 503, debounce requests.

### H3. Mobile first-run: modal blocks the entire app
- At 375 px the "Welcome to the cadastral map" tour opens as a full-height modal over the search card; the search card and the right-hand tool column are partly visible and blurred behind it. The tour is 3 steps and large.
- Tour reappeared in one tablet load even though `land-registry.map.welcome-seen.v1` was already set later in the same session — possible race between flag write and display check (not fully confirmed).
- **Fix:** persist the flag on *open* rather than on dismiss, make the tour a dismissible bottom sheet on small screens.

### H4. Overlapping floating UI on tablet and small windows
- At 768×1024 the search card, the error banner, the Shortlist panel and the Layers panel overlap each other; the error banner is clipped by the Layers panel (text cut off), the Shortlist panel runs under the search card's help text.
- The Shortlist panel opens **by default** (it showed 3 items on tablet and an empty state on desktop), occupying ~30 % of the map width before the user asks for it.
- **Fix:** one-panel-at-a-time rule below ~1100 px; collapse shortlist by default; stack banners in a single notification region.

### H5. Transient plain-text "Service Unavailable" for `/map`
- One request to `/map` at tablet size returned a bare-text **"Service Unavailable"** page (no styling, no retry guidance). Six follow-up requests were all 200 (first 1.3 s, then ~400–530 ms), so this looks like a Cloud Run cold start/instance restart.
- **Follow-up (2026-11-20):** Keep `min-instances` at **0**, per the owner's choice. App-handled HTML 503 errors now use a branded page with a retry action. A Cloud Run response before the application starts may still be unbranded; startup probe/timeout behavior and production rendering have not been rechecked.

### H6. No branded error pages; API-style errors for browser navigation
- `/nonexistent-xyz` → bare JSON `{"detail":"Not Found"}`. Same for expired routes.
- **Fix:** content-negotiate: HTML 404/500 page for `Accept: text/html`.

### H7. Missing stylesheet served with wrong MIME type
- `GET /static/aecs4u-theme/css/components/map_detail_sheet.css?v=1.6.0` → **404** with `application/json`; Chrome logs a MIME-type error on every `/map` load. Parcel/area detail sheets likely render unstyled.
- **Fix:** ship the file (theme package version mismatch — `aecs4u-theme` 1.6.0 references a file that is not bundled) or remove the `<link>`.

### H8. `robots.txt` / `sitemap.xml` / OG tags use `http://`
- `Sitemap: http://land-registry.aecs4u.com/sitemap.xml`, `<loc>http://…/landing`, `og:url` and `og:image` are all `http://` (behind a TLS-terminating proxy; the app does not honour `X-Forwarded-Proto`). Crawlers and social unfurlers get redirects; mixed-content risk on og:image.
- `robots.txt` disallows everything except `/landing` and `/static/`; the legal pages are blocked from indexing as well as gated.
- **Fix:** run uvicorn with `--proxy-headers --forwarded-allow-ips`, or build absolute URLs from a configured `PUBLIC_BASE_URL`. Once public legal pages are restored, update `robots.txt` to allow those routes.

### H9. Touch targets below 44 px (WCAG 2.5.5 / 2.5.8)
- `/map`: 39 interactive elements below 32 px on at least one side — zoom/fullscreen/compass buttons (29×29), shortlist close (24×24), filter checkboxes (24×24), "Privacy" link (42×15), MapLibre attribution link (50×14), "Refresh/Retry" (≈ 61×30, 12 px text).
- `/auth/login`: 14 elements <44 px, including Clerk's "Sign up" link (47×18) and the custom "Registrati" (65×16); `/landing`: footer "Privacy" (43×18).
- WCAG 2.2 AA requires ≥ 24×24 (met except a few); the 44 px guidance is not met on mobile.

---

## Medium

### M1. Duplicate login forms with two languages
- `/auth/login` renders **two** sign-in UIs on one page: Clerk's component ("Sign in to land-registry", English) *and* a custom form ("Accedi con email", Italian) below it, plus a separate Italian heading "Accedi / Accedi per entrare nella tua area riservata" above. Two "Sign up"/"Registrati" links point to different URLs (`/auth/register?next=/map` vs `/auth/register`).
- Accessibility: duplicate form landmarks/labels; an `Accedi` `<h2>` precedes the `<h1>` (heading order inverted).
- **Fix:** keep one flow; localise the Clerk component (`localization` prop) or hide the unused one.

### M2. Empty `<title>` on `/auth/login`
- `<title></title>`; the register page's title is "Create Account" (English) while the rest is Italian.
- Titles also differ by page for the same product: "Land Registry Viewer" (`/landing`, English) vs "Visualizzatore del Catasto" (legal pages, `/map-legacy`) vs "Cadastral Map - Land Registry" (`/map`) vs "Mappa catastale - Land Registry" (`/map` in Italian). The product has no single name.

### M3. Language handling is inconsistent
- `/landing` `<html lang="it">` but its `<title>` is English; `/auth/login` `<html lang="it">` with an English Clerk UI; `/map` `<html lang="en">` while the page may be switched to Italian (`?lang`/cookie) — verify `lang` updates on switch.
- At the original live snapshot, legal pages, help, contact and data-sources were **Italian only** (no EN toggle) while `/map` offered English. The current source adds an English/Italian selector and Italian translations, but the rendered language still needs rechecking. The original Contact page had no address or form; the source now renders a `mailto:` link to the support address published by [AECS4U](https://realestates.aecs4u.com/help), unless `LEGAL_CONTACT_EMAIL` is explicitly configured. Verify the rendered page after deployment.
- Raw IDs shown as titles in Shortlist ("Parcel G273_012600.797", subtitle repeats the same ID, priority truncated to "P—").
- **Source follow-up (2026-11-20):** the generated default label `Parcel <reference>` now renders as the localized parcel type followed by a single reference line. The priority is a compact circular value with a full accessible name and tooltip, so it does not depend on fitting the word "Priority" into the card header. Custom labels are preserved. This has not had a rendered/browser recheck; language switching, long custom labels, and narrow-card layout remain open.

### M4. Dark mode defects
- Dark theme works (`data-theme="dark"`, bg `#111827`), but:
  - The basemap stays a bright OSM raster → very high contrast jump against dark panels. Consider a dark basemap option.
  - Contrast failures (<4.5:1): the red error banner and its Retry button (2.96:1), "★"/"Unfavourite" (2.92:1).
  - Search field helper copy and shortlist cards are dark-on-darker; panel backgrounds nearly black (`#0b0f1a`-like) vs navy header — three different dark surface colours.
- Light theme: only 1 measurable contrast failure (an avatar glyph, probably measurement noise).

### M5. Input accessibility gaps on `/map`
- In the original live snapshot, 4 controls had no accessible name: `#mapTableFilter`, `#mapAttributeValue` (placeholder-only), and two hidden file inputs (`#mapDrawingsFile`, `#sisterBatchAppendFile`). The current source labels all four.
- The original rendered page exposed 34 `aria-live` regions. The two current map templates contain 11 `aria-live="polite"` regions; verify the rendered count and confirm the status chip and error banner do not create duplicate announcements.
- The original landmark scan found `<header>` ×7 and `<aside>` ×5. Current map asides have accessible labels or labelled-by headings, and the map utility stack is a named group. Recheck the accessibility tree after render.
- `/map` had 2 `[role=dialog]`/`.modal` nodes in the DOM at load; confirm focus trap and return-focus on tour close.

### M6. Focus order and focus visibility
- Tab order on `/map` is sensible (skip link → logo → language → theme → user → search → …) and every tested stop showed a visible focus ring. Positive.
- However the original first 14 tab stops ended at the zoom controls; the Layers button, shortlist, and right-hand tool column were not reached in that count. The current source moves the utility column before the map element, so its controls precede MapLibre's injected controls in DOM order. Recheck reachability and visible focus in a browser.

### M7. Third-party dependencies and supply chain
- At the original source snapshot, `clerk-js@latest` was used on the login page (console says `@5`); Bootstrap 5.3.2, FontAwesome 6.5.0, Inter+Lexend from Google Fonts, jsdelivr, unpkg, and tailwindcss were also in use/allowed. The follow-up now pins ClerkJS and Clerk UI; see its status above. Comment in HTML says "SRI when provided" but no SRI was set for several external assets.
- In the original CSP snapshot, `cdn.tailwindcss.com` (Play CDN, not for production), `cdn.redoc.ly`, `maps.googleapis.com`, `mt1.google.com`, and `api.stripe.com` were allowed even where unused.
- Google Fonts loaded at runtime → third-party request that leaks IP (GDPR); at the original source snapshot, the privacy page listed Clerk but not Google Fonts/CDNs/OSM tiles. The follow-up now discloses external assets and fonts.

### M8. CSP could be tighter
- In the original header snapshot, `style-src 'unsafe-inline'`; `script-src` allowed `blob:` and 5+ CDNs; there was no `object-src 'none'` or `upgrade-insecure-requests`. `img-src https:` was a blanket allow.
- Positive at that snapshot: nonces on scripts, `script-src-attr 'none'`, `frame-ancestors 'self'`, HSTS preload, XFO DENY, nosniff, COOP/CORP, Permissions-Policy.
- Minor inconsistency at that snapshot: `X-Frame-Options: DENY` vs `frame-ancestors 'self'`; source follow-up aligns these to same-origin framing.

### M9. Excessive OSM tile requests
- At zoom 7.6 (Italy view) the page requested ~170 `a.tile.openstreetmap.org` z9 tiles in one session (the first load) while the view is at z7.6, then ~15 on the next load. Likely a prefetch/`maxZoom`/`tileSize` interaction after the URL zoom was rewritten. One request returned 503 from OSM.
- At the original source snapshot, `map-v2.js` used the `{s}.tile.openstreetmap.org` hostname pattern. The [current OSM tile policy](https://operations.osmfoundation.org/policies/tiles/) specifies `https://tile.openstreetmap.org/{z}/{x}/{y}.png`, requires visible attribution, a valid User-Agent, a browser Referer and caching, and prohibits bulk prefetch/offline use. It does **not** require a commercial tile provider for ordinary interactive viewing; heavy traffic may be blocked because capacity is limited. The follow-up changes the primary source URL but does not remeasure request volume.
- **Fix:** investigate and reduce the first-load overfetch, use the policy's canonical endpoint or another provider, and verify caching, Referer and attribution. Plan another provider or self-hosting if expected traffic or availability needs exceed OSM's best-effort service.

### M10. Cache headers
- HTML pages have no `Cache-Control`; `/static/styles.css` (152 KB) is `no-cache` (revalidates each load — fine, but fingerprinted URLs elsewhere use `?v=`; use immutable long-cache with the hash).
- `/ready` correctly `no-store`.

### M11. API error contracts
- At the original source snapshot, `/api/v1/map/search` without params returned a 422 FastAPI detail array, but accepted only `query=` and not `q=`. The follow-up adds the alias.
- In the original production snapshot, `/api/v1/map/catalog` and `/api/v1/map/metrics` both returned 404. The catalog path had no alias; metrics was intentionally hidden unless enabled, but the documentation omitted that gate and its error detail differed in capitalization from the generic 404.

### M12. Invalid map URL parameters are accepted
- `/map?lat=abc&lng=999&zoom=-5` → 200 with no feedback. Verify the client clamps/ignores rather than throws (not confirmed visually).

### M13. Login page flash / slow Clerk mount
- `/auth/login` shows "Caricamento accesso…" placeholder while Clerk loads, a Clerk-component duplicate form, and an `autocomplete` console hint ("input elements should have autocomplete attributes: current-password") for the custom password field. Clerk `afterSignInUrl` prop is deprecated (console warning).

### M14. `/docs` is gated and `Redoc`/`openapi.json` behind login
- Consistent with a private API, but `/landing` links to "API documentation" → unauthenticated visitors hit a login wall. Either hide the link or make a public docs subset.

---

## Low / polish

1. In the original live snapshot, `favicon.ico` was served as SVG (`image/svg+xml`); some clients ignore a `.ico` with SVG content. The source now serves a real multi-size ICO, provides SVG/PNG fallbacks and an Apple touch icon, and lists PNG icons in the web manifest. Browser installation and icon rendering remain to be checked after deployment.
2. `/.well-known/security.txt` returned 404 in the original live audit. The source now publishes the owner-verified security contact and required `Contact` field; verify the route after deployment. See [RFC 9116](https://www.rfc-editor.org/rfc/rfc9116.html).
3. `/landing` footer links: "Privacy" and "API documentation" are small (18 px high); the language switch shows flag emoji + "IT/EN" (emoji flags render inconsistently on Windows and are not a language indicator).
4. Login/register pages have no `meta description`, OG tags or canonical; `theme-color` `#3b82f6` differs from the favicon blue `#1a5490`.
5. Landing heading order H1 → H2 → six H3 → H2 is fine; the login page order H2 → H1 is not (see M1).
6. In the original live snapshot, font sizes drifted across 12, 13, 14, 15, 15.2, 16, 17.6, 18 and 20 px on the three public pages, with Inter and Lexend in use. The source now disables remote Inter/Lexend requests; the type-size scale still needs consolidation and rendered review.
7. Panel sections on `/map` rely on small 12–13 px grey text (helper text, status chip, "Zoom in to see cadastral parcels" toast) — check against 4.5:1 in both themes.
8. The "Zoom in to see cadastral parcels" toast and the "Map ready" chip sit permanently at the bottom, colliding with the Shortlist toggle on tablet.
9. User menu shows "Admin / admin@aecs4u.com" — the account display name is "Admin", whereas an earlier session in this audit showed a person's name; confirm profile sync with Clerk.
10. `/map` embeds Italian-language text on an English page in places (e.g. data names) — acceptable for place names, but check UI strings for untranslated keys.
11. Italian `.po` coverage: legacy page title "Visualizzatore del Catasto", `/map` "Cadastral Map – Land Registry" — unify naming.
12. `lat`/`lng` retain 6 decimals and `zoom` 2 decimals in the shareable URL. The current source uses `history.replaceState`, so panning does not add map positions to browser history; the back-button behavior was not rechecked in a browser.

---

## Positive observations

- Security headers are broadly strong (HSTS preload, nonce CSP, `script-src-attr 'none'`, COOP/CORP, Permissions-Policy denying geolocation/camera/mic/payment).
- Skip-to-main-content link present on every page; visible focus rings; `<main>` landmark present; no images without `alt`; no duplicate element IDs; no horizontal scroll at any tested width.
- `/map` first paint is fast: TTFB ~380 ms, DOMContentLoaded ~620 ms, 43 requests, ~8 KB of own transfer (gzip). `/health` is cheap and correct; `/ready` returns a useful structured payload.
- Landing page copy is clear, bilingual, with descriptions and OG metadata.
- First-run tour, "Browse by cadastral hierarchy", shareable view URLs, layer-search filter and per-layer opacity sliders are good UX foundations.
- Legal pages (once reachable) are short, readable, and honest about data limits ("not a substitute for an official visura").

---

## Recommended order of work

1. **Unblock production configuration.** Reauthenticate the local gcloud account, confirm the dedicated `LAND_REGISTRY_CADASTRAL_POSTGRES_DSN` secret, and provision a matching production Clerk publishable/secret/JWKS configuration. Keep secret values in Secret Manager and GitHub protected variables, not in the repository. The production endpoints currently show the previous revision.
2. **Deploy the reviewed candidate with `min-instances=0`.** The workflow should keep the current revision serving traffic until the candidate passes `/health`, `/ready`, and canonical map-health checks. Then verify that legal pages are public, the login uses production Clerk, cookies behave correctly, the stale stylesheet is gone, the CSP matches source, the public URLs are HTTPS, and `/.well-known/security.txt` returns the verified contact.
3. **Repeat the authenticated browser audit** at the original four viewport sizes, both themes, and both languages. Measure remaining target sizes, contrast, focus order and dialog behavior; verify the mobile/tablet layout, login fallback, and all map-layer/search states. These remain unproven by source inspection and static checks.
4. **Resolve M9 with traffic evidence.** Measure first-load tile requests, caching and provider attribution with restored data. Reduce any overfetch before relying on the best-effort OSM tile service at higher traffic.
5. **Finish M7 review.** Confirm the Italian Clerk module preload and dynamic import reuse its integrity-protected response; verify third-party requests and CSP behavior after deployment, including that no debug-only origins appear in production.
6. **Re-run the feature audit with data restored** to cover parcel selection, dossier PDF, shortlist editing, SISTER/Stripe flow, `/map-legacy` upload and drawing tools.
