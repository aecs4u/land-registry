# App design and quality audit

**Date:** 2026-09-26  
**Target:** `http://127.0.0.1:8011`  
**Status:** Public and standard signed-in flows reviewed; admin-only coverage is blocked by rejected credentials

## Summary

Playwright inspection covered the cadastral map at desktop and mobile sizes, map search and layer controls, the attribute table and shortlist, sign-in and registration, profile and settings, the legacy analysis map, the cadastral data catalog, and linked legal/support routes. The standard demo account signed in successfully. The admin credentials named by the user were rejected with “Invalid username or password,” so admin-only pages could not be reviewed.

The highest impact findings are broken legal/support links, a mobile sidebar that blocks map interaction, municipality search results without place names, and a runtime error in the legacy map’s drawing control.

## Findings

### P1 — Legal and support links lead to 404 pages

- **Area:** Trust, support, navigation
- **Evidence:** Direct navigation to `/privacy`, `/terms`, `/help`, and `/contact` returned HTTP 404. These paths are linked from the shared footer. `/privacy` is also linked from the cookie policy dialog.
- **Impact:** Users cannot reach the app’s privacy policy, terms, help, or contact information from its own links.
- **Recommendation:** Publish these pages or update/remove links until the destinations exist.

### P1 — The expanded mobile sidebar blocks the map

- **Area:** Responsive layout, map interaction
- **Evidence:** At 390×844, the expanded sidebar occupied the viewport and intercepted clicks on map controls. A Playwright click on **Layers** timed out because the sidebar’s “Land Registry” button intercepted pointer events. After collapsing the sidebar, the map controls worked and the map used the full viewport width.
- **Impact:** On narrow screens, users can be unable to search or use map tools until they discover and collapse the sidebar.
- **Recommendation:** Start with the sidebar closed on narrow screens, or make it a dismissible drawer that does not cover controls while open.

### P1 — Municipality search suggestions show codes without names

- **Area:** Search usability
- **Evidence:** Searching for “Roma” returned suggestions named only with numeric identifiers such as `58091`, followed by the generic type “Municipality.” Selecting a result changed the search field to the code rather than a municipality name.
- **Impact:** People cannot confidently distinguish matching municipalities, especially when a query returns many codes.
- **Recommendation:** Show the municipality name prominently and the ISTAT code as secondary detail; preserve the selected place name in the field.
- **Cause and status:** a remediation regression, not a design choice. The map-layer column builder (`_source_columns` in `land_registry/map_layers.py`) dropped `canonical_name` from every layer instead of only administrative boundaries. Search results and municipality-profile tiles therefore lost their names. Fixed on 2026-09-26: `/api/v1/map/search?query=Roma` again returns “Roma”, “Romagnano al Monte”, …, and a regression test covers the column list. Not yet re-checked in the browser.

### P1 — The legacy map throws during drawing-control initialization

- **Area:** Runtime quality, legacy map tools
- **Evidence:** Opening `/map-legacy` produced `TypeError: L.Control.Draw is not a constructor` in `folium-interface.js` at line 686. The page also logged `fitBounds failed: Bounds are not valid.` Navigation timed out before the page eventually became inspectable.
- **Impact:** The drawing control fails to initialize, and the invalid-bounds warning indicates the initial extent logic is receiving unusable data.
- **Recommendation:** Correct the Leaflet Draw dependency/initialization and guard the initial fit-bounds operation when there are no valid geometries.

### P2 — Sidebar toggle exposes stale expanded state to assistive technology

- **Area:** Accessibility
- **Evidence:** After collapsing the mobile sidebar, it moved to x = −390 (fully offscreen), but the toggle continued to expose `aria-expanded="true"` in the accessibility snapshot. Its navigation links also remained in the accessibility tree while offscreen.
- **Impact:** Screen-reader users receive an incorrect state and may navigate links that are visually unavailable.
- **Recommendation:** Keep `aria-expanded` synchronized with the drawer state and hide/inert the closed navigation subtree.

### P2 — Sign-in form shows field errors before the user enters anything

- **Area:** Form validation
- **Evidence:** On initial load, the empty sign-in form displayed “Please provide a valid email” and “Password is required.” After filling a valid-format email and a password, those same messages remained visible. The admin sign-in attempt also displayed “Invalid username or password.”
- **Impact:** The form presents stale validation feedback, making it unclear whether entered data is accepted or what action is needed.
- **Recommendation:** Hide field errors until validation runs, clear them as values become valid, and associate each error with its field using `aria-describedby` and `aria-invalid`.

### P2 — Map opens outside the stated cadastral coverage area

- **Area:** Default map state, coverage communication
- **Evidence:** The default view is centered near Rome, while layer metadata identifies cadastral sheets and parcels as “Veneto only.” At the default/low zoom, the map reports “No cadastral parcels published for this area yet (coverage: Veneto).”
- **Impact:** New users land in an area where the primary cadastral layers have no coverage and may read the state as missing data rather than regional coverage limits.
- **Recommendation:** Center the initial map on an area with cadastral data, or clearly call out the Veneto-only coverage before users begin searching.

### P2 — Empty shortlist copy implies filters excluded saved parcels

- **Area:** Signed-in empty state
- **Evidence:** After signing in with the demo account, the shortlist opened with `Total 0` and “No parcels match the shortlist filters.” The profile page instead stated “No saved parcels yet.”
- **Impact:** With zero saved parcels, the shortlist wording suggests a filter problem instead of explaining that the account has no saved items.
- **Recommendation:** Use an explicit first-use empty state when the shortlist is empty, and reserve “no matches” for a non-empty shortlist filtered to zero results.

### P2 — Cadastral data availability starts with ambiguous zero counts

- **Area:** Data catalog, status communication
- **Evidence:** The catalog lists 30,367 files but initially reports “Available: 0,” “Missing: 0,” and “0.0% Available,” without saying that availability has not yet been checked.
- **Impact:** Users may interpret the initial uncomputed state as evidence that no files are available.
- **Recommendation:** Label availability as “Not checked” until the scan runs, then show the checked counts and percentage.

### P2 — English UI has an Italian language-control accessible name

- **Area:** Localization, accessibility
- **Evidence:** On the English cadastral map, the language button’s accessible name is “Seleziona lingua: English,” while the sign-in page uses “Select language: English.”
- **Impact:** The language control is inconsistently localized for screen-reader users and across pages.
- **Recommendation:** Use the active locale consistently for the language-control label.

### P3 — Cadastral data catalog has horizontal overflow on mobile

- **Area:** Responsive layout
- **Evidence:** At 390px viewport width, the catalog document measured 400px wide. Its outer content starts at x=10 and extends 390px, leaving 10px of horizontal overflow.
- **Impact:** A small portion of the page can sit outside the viewport and may cause horizontal panning.
- **Recommendation:** Constrain the outer container to the viewport width and check mobile gutters for cumulative width.

## Positive observations

- The main cadastral map loaded with no JavaScript errors. Search, map actions, basemap radio controls, the layer panel, and the attribute table responded.
- Invalid search text produced a clear “No municipality or parcel match” message; pressing Enter then returned “No result.”
- The attribute table exposed column headers, row data, a layer selector, a filter field, and pagination. Its row viewport and containing panel are independently scrollable.
- The cadastral data catalog filters stack into a single column on mobile, and its regional accordions fit the narrow viewport.
- The standard demo account signed in and exposed profile/settings content. Unauthenticated visits to `/profile` and `/settings` redirected to sign-in with the requested return path.

## Coverage and limits

| Area | Viewport / account | Result |
|---|---|---|
| Cadastral map | 1440×900, signed out and demo signed in | Search, map actions, basemaps, layers, shortlist, attribute table inspected |
| Cadastral map | 390×844 | Sidebar and map-control interaction inspected; overlay issue reproduced |
| Sign-in and registration | 1440×900 and 390×844 | Form structure and validation feedback inspected |
| Profile and settings | 1440×900, demo account | Profile, language and map settings inspected |
| Legacy analysis map | 390×844 | Page eventually loaded after navigation timeout; drawing-control and bounds errors observed |
| Cadastral data catalog | 1440×900 and 390×844 | Filters, regional list, initial status, and document overflow inspected |
| Legal/support pages | Direct route checks | `/privacy`, `/terms`, `/help`, and `/contact` returned 404 |
| Admin-only views | Admin login attempt | Not inspected; app rejected the provided admin account as invalid |

The main map produced no JavaScript errors during inspection. Its WebGL “GPU stall due to ReadPixels” warnings appeared during browser rendering/screenshot activity and are not listed as app defects. The sign-in page also emitted a Clerk development-key warning; confirm production keys are used in deployed environments.
