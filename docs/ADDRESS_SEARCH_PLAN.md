# Address search development plan

Status: implemented (Phases 1-6) on 2026-11-09; see "Implementation notes" at the end for deviations. Owner: map team. Applies to the `/map` search box
(`map_v2.html`, `static/map-v2.js` `search()`, `GET /api/v1/map/search`).

## Problem

Searching "via goethe 1, Palermo" does not find the building. Today:

- `/api/v1/map/search` only matches municipalities and parcel references.
- On an explicit submit with no result, the browser calls public Nominatim with
  free text (`map-v2.js`, `search()`), which often lacks Italian civic numbers
  and returns only the street, a namesake street or a POI.
- Hits are bare coordinates: nothing selects the parcel or building at the
  point, and nothing says whether the house number was matched.
- There is no address autocomplete (Nominatim policy forbids it).

## Goal

A civic address typed in the search box resolves to the exact access point,
then to the parcel that contains it, with an honest label when only the street
or municipality matched. No third party sees the text unless the user opts in
to the fallback.

## What already exists

| Piece | State |
|---|---|
| ANNCSU regional stores (`aecs4u_stats.anncsu`), Postgres schema `anncsu` locally | Tables `streets(street_id, comune_code, istat_code, odonimo, locality, total_accesses)` and `addresses(street_id, civic_number, exponent, longitude, latitude, coordinate_method)`; SQLite or Postgres, one per region |
| `anncsu_region_db_status(region)` | Availability and coverage check, already used by the parcel panel |
| `civic_addresses_for_geometry` | Reverse lookup only (parcel -> addresses). **No forward lookup exists.** |
| `queries.py` `search_streets` | Older street-registry store, street names only, no coordinates |
| Municipality search | Existing branch of `/map/search` returns ISTAT code and region |
| Parcel selection | `loadParcelByReference(reference, true, id)` |

## Phases

### Phase 0 - Data check (done locally, 2026-11-09)

Findings from the local aecs4u-stats Postgres, database `anncsu`:

- Sicily (region 19) is loaded: ANNCSU/20260915, 3,165,306 civic addresses,
  2,432,270 geocoded (77%). Tables `r19__streets`, `r19__addresses`,
  `r19__municipality_coverage`, `r19__anncsu_provenance`. Only region 19 is
  present locally, so other regions need the importer before they can be tested.
- "Via Goethe 1, Palermo" exists: street `575085`, istat `082053`, 86 accesses,
  number 1 at lon 13.350842, lat 38.120125 (`coordinate_method` 3). Exponents
  such as 14/A are stored in `exponent`.
- **The tables live in schema `anncsu`, not `public`.** The installed package
  (`anncsu_region_db_status`, `_postgres_civic_addresses_for_geometry`) only
  looks in `public`, so it reports region 19 as `missing`. The parcel panel's
  ANNCSU address block therefore reports "unavailable" against this database.
  Resolve first: either set the role/database `search_path` to include
  `anncsu`, or fix the package to qualify the schema. The address search must
  use an explicit `anncsu.` prefix or one shared helper, so it does not depend
  on `search_path`.
- Indexes today: `r19__addresses` has only `comune_idx` and a GiST on `geom`.
  There is no index on `street_id`, and none on the street name.
- Extensions installed: `plpgsql`, `postgis`, `postgres_fdw`. No `pg_trgm` and
  no `unaccent`.
- Still to check: production (schema, regions loaded, same indexes) and
  coordinate coverage of the municipalities users search most.

Decision gate: production must have the regional stores in a schema the
service can read, or Phase 2 is blocked.

**Gate status (2026-11-10): not met.** The local aecs4u-stats database passes
(see the status table at the end of this document); production has not been
checked because it could not be reached from the development workspace. The
gate stays open until the output of the script below, run against production,
is recorded here:
`python scripts/check_anncsu_address_store.py --probe "via goethe 1, Palermo:082053"`.
Until then the feature fails safe (no store or an unreachable database yields no
address results and the explicit-Enter Nominatim fallback applies, covered by
tests), and `MAP_ADDRESS_SEARCH_ENABLED=0` disables it.

### Phase 1 - Address parser (1 day)

New module `land_registry/address_search.py`, pure functions, no I/O.

- `parse_address(text) -> ParsedAddress(street, civic, exponent, municipality_hint)`.
- Normalisation: case and accent folding (Goethe/Göthe), street-type
  abbreviations (v., via, viale, vle, p.zza, piazza, c.so, corso, str., loc.),
  separators (`,`, `-`, `n.`, `n°`, `civ.`), civic forms `1`, `1/A`, `1A`,
  `1 bis`, `snc` (no number), trailing province `(PA)` or `PA`.
- Municipality hint is only the trailing token(s); resolved against the
  municipality search, never trusted blindly.
- Unit tests with a table of real inputs (including the Via Goethe case,
  ambiguous street names, no municipality, no number).

### Phase 2 - Forward lookup over ANNCSU (2 days)

- Add `find_civic_addresses(region, comune_code, street, civic, exponent, limit)`
  in this repo (`land_registry/anncsu_lookup.py`) that wraps both store types
  used by `anncsu_region_db_status`. Candidate for upstreaming into
  `aecs4u-stats` later; keep the interface small so the move is mechanical.
- Matching ladder, stopping at the first level with results:
  1. exact normalised street + civic + exponent;
  2. exact street + civic, any exponent;
  3. street only, return nearest civic numbers (`civic +/- n`) and the street
     centroid.
  4. fuzzy street (trigram or token-sorted similarity above a threshold).
- Indexes (none of these exist today): `(street_id, civic_number, exponent)`
  on `anncsu.rNN__addresses`, and a normalised street name. `unaccent` and
  `pg_trgm` are not installed, so either create them (needs a privileged role,
  owned by the aecs4u-stats migration) or add a precomputed `odonimo_norm`
  column (upper-cased, accent-folded, street type stripped) with a btree
  index for prefix search and token matching in Python for the fuzzy step.
  Prefer the precomputed column: no new extension to deploy. Put the SQL in
  `scripts/sql` (applied by `apply_map_sql.py`) or in the stats migration if
  the database is owned there.
- Query by municipality first (`istat_code` / `comune_code`), then street, so
  the per-municipality street list (a few hundred rows) is the search space.
- Result carries `match_level` (`address`, `address_exponent_dropped`,
  `street`, `fuzzy_street`), `coordinate_method`, and ANNCSU attribution
  (CC BY 4.0).

### Phase 3 - API (1 day)

Extend `GET /api/v1/map/search` (`routers/api.py`):

- New result kind `address`:
  `{kind, label, municipality_name, istat_code, lat, lon, match_level,
  coordinate_method, parcel_id?, parcel_reference?}`.
- Flow: parse -> resolve municipality (hint or, when absent, search the street
  name across the user's current region/viewport bbox if provided) -> lookup ->
  point-in-polygon against the cadastral layer to attach the parcel.
  Parcel lookup is best effort and bounded; failure returns the address
  without a parcel, never an error.
- Keep existing behaviour: 503 when the source is down, `X-Data-Stale`, 8 s
  query deadline, rate limits, in-memory result cache keyed by normalised
  query.
- Optional `bbox` or `near=lat,lon` parameter used only to rank ambiguous
  street names.
- Metrics: add `address` to `map_observability.py` (hit level counts only, no
  query text).
- Contract tests in `tests/` for each match level and for 503/stale paths.

### Phase 4 - Frontend (1.5 days)

- `renderSearchResults`: show address results first when the input parses as an
  address, with the match level as a visible label ("Exact address", "Street
  only - number not found").
- Selecting an address (required behaviour) runs one sequence in a new
  `chooseAddressResult(result)` called from `chooseSearchResult`:
  1. Fly to the point (zoom about 19).
  2. **Marker with the address as label**: add a `maplibregl.Marker` (same
     pattern as the auction-sale marker, `map-v2.js` near line 1773) with a
     visible text label of the formatted address (e.g. "Via Goethe 1,
     Palermo") plus a popup/tooltip with the match level and ANNCSU
     attribution. Keep one search marker in `state.searchMarker`; replace it on
     the next search, remove it when the search box is cleared or the parcel
     selection is cleared. The label is escaped text, never HTML.
  3. **Show the cadastral parcel**: if `parcel_reference` is present, call
     `loadParcelByReference(reference, false, parcel_id)`. This draws the
     existing selection highlight on the map (the parcel layer is loaded at
     the target zoom; if tiles are not ready yet, wait for the map `idle`
     event, then re-run the highlight).
  4. **Open the cadastral parcel window**: the same call opens the parcel
     sheet (`#directParcelPanel`), so the user sees the parcel details without
     a further click. The marker must not cover the sheet on mobile: anchor
     the label above the point and pan so the point is in the visible map area
     (account for the sheet width/height).
  5. If no parcel is found at the point, keep the marker and label, show
     "No cadastral parcel found at this address" in the search status, and do
     not open the sheet.
  6. If only the street matched, show the marker at the street centroid with
     the label "Via Goethe, Palermo (number 1 not found)", no parcel, and list
     nearby civic numbers as follow-up suggestions.
  Closing the parcel sheet keeps the marker; "Clear selection" removes both.
  The marker and the selected parcel are encoded in the share URL so a
  reloaded link restores them.
- Autocomplete: reuse the existing debounce. Once the input parses as street +
  municipality, request address suggestions as the user types. This is a local
  source, so Nominatim's no-autocomplete rule does not apply. Nominatim remains
  explicit-submit only.
- Keyboard and ARIA behaviour of the combobox stays unchanged (tests in
  `tests/browser`).
- i18n: new strings in the English and Italian `.po` files.
- Share URL (`?q=` or existing view params) keeps working for addresses.

### Phase 5 - Better external fallback (1 day)

- Move the Nominatim call server-side (`/api/v1/map/geocode`): proper
  User-Agent, caching, rate limit, one place to swap the provider.
- Use structured queries (`street`, `city`, `addressdetails=1`) built from the
  parsed address; rank house/building > street > place; drop hits whose city
  does not match the parsed municipality; keep the coordinate de-duplication.
- Only runs on explicit submit when ANNCSU returned nothing at `address` level.
- Update the privacy note in `map_v2.html` and `legal_pages.py`: the request is
  now made by our server, not the browser, and the text is still sent to
  OpenStreetMap.

### Phase 6 - Quality and rollout (1 day)

- Gold set of about 50 addresses (several regions, exponents, ambiguous names,
  frazioni, `snc`) with expected municipality and street, used as a regression
  test and to report the exact-hit rate before/after.
- Browser smoke test (`tests/browser`): search an address, pick it, assert the
  marker exists with the address text, the parcel highlight is present and
  `#directParcelPanel` is visible; repeat for the street-only and no-parcel
  cases (marker, no sheet), and on a phone-width viewport.
- Feature flag `MAP_ADDRESS_SEARCH_ENABLED`. Owner decision 2026-11-10: it defaults to **on** (it fails safe without data) instead of shipping dark; set it to `0` to disable per environment.
- Docs: update `CLAUDE.md` API list, `docs/MAP_SRS.md`, and the ANNCSU entry in
  `source_catalog.py`.

## Sequence and estimate

Phase 0 -> 1 -> 2 -> 3 -> 4 is the critical path, about 6.5 days. Phase 5 can
run in parallel after Phase 3. Phase 6 closes. Total about 8 days.

## Risks

| Risk | Mitigation |
|---|---|
| ANNCSU store missing or partial in production | Phase 0 gate; fall back to server-side geocoder and say so in the label |
| Coordinates missing or interpolated for some municipalities | Show `coordinate_method`; use street-level match instead of pretending precision |
| Same street name in many municipalities | Require or infer municipality; rank by viewport; show municipality in every label |
| Point falls on no parcel or on a boundary | Return address without parcel; pick the nearest parcel within a small tolerance, flagged as approximate |
| Query latency on national data | Per-region stores, normalised-name index, 8 s deadline, result cache |
| Licence | Keep CC BY 4.0 attribution on address results and in the data-sources page |

## Out of scope

Building-footprint selection (needs Ocular/footprint data; see
`DEVELOPMENT_STRATEGIST.md`), reverse geocoding of map clicks, and commercial
geocoders.

## Implementation notes (2026-11-09)

Code: `land_registry/address_search.py` (parser), `land_registry/anncsu_lookup.py`
(lookup), `routers/api.py` (`/map/search` address results, `/map/geocode`),
`static/map-v2.js` / `map-v2.css` (marker, parcel, window, shortlist
clearance), tests in `tests/test_address_search.py` and
`tests/browser/test_address_search_smoke.py`.

Deviations from the plan above:

- **Parcel is resolved by the browser, not `/map/search`.** After an exact
  address is picked, the page calls the existing
  `/api/v1/enrichment/parcel/at-point` and then `loadParcelByReference`, which
  draws the highlight and opens the parcel window. This keeps the search
  response fast and reuses the existing parcel path.
- **Indexes are optional and not applied.** `scripts/sql/anncsu-address-search-indexes.sql`
  holds the recommended indexes (run by hand on the `anncsu` database; it is
  not in `apply_map_sql.py`, which targets the stats database). The lookup
  needs no new extensions.
- **No indexes were required to meet the targets.** The lookup reads the municipality's street
  list once (cached for an hour), matches names in Python (exact, same name
  with another type, all words present, fuzzy via `difflib`), then reads one
  street's addresses through the existing `comune_code` index. A Via Goethe
  lookup takes about 0.1 s warm, 0.8 s cold. Add the `street_id` index
  only if larger streets become slow.
- **The schema is resolved by the lookup** (`anncsu`, then `public`). The
  installed `aecs4u_stats` reports region 19 as `missing` on this database;
  the parcel panel's ANNCSU block falls back to the schema-aware reader in
  `anncsu_lookup` in that case.
- **Autocomplete** needed no new code: the existing debounced `/map/search`
  call now returns address results as the user types. Nominatim stays
  submit-only.
- **Feature flag** `MAP_ADDRESS_SEARCH_ENABLED` defaults to on (owner decision
  2026-11-10, replacing the plan's "ship dark"); set it to `0` to disable.
- **Match-level metrics:** `map_metrics.record_event("address_search.<level>")`
  counts outcomes (`address`, `address_exponent_dropped`, `street`,
  `fuzzy_street`, `no_match`) and appears under `events` in
  `/api/v1/map/metrics`. Query text is never recorded.
- **Share URL:** the marker is stored as `addr`, `addr_lat`, `addr_lng` (label
  capped at 120 characters, coordinates checked against the map's Italy
  bounds) and restored on load; the parcel already used `parcel`/`parcel_id`.
- **Boundary tolerance:** when no parcel contains the access point, the page
  probes 8 directions at 4 m, then 10 m, and uses the nearest parcel,
  telling the user "Nearest parcel, within N m of the address point."
- **Gold set:** `tests/data/address_gold_sicily.json` has 50 Sicilian
  addresses (14 municipalities, 11 with a house-number letter, four spelling
  styles). `tests/test_address_gold_set.py` requires at least a 95% exact
  hit rate; the current rate is 50/50. It skips when the `anncsu` database is
  unreachable. Only Sicily exists locally, so other regions are not covered.
- **Phase 0 in other environments:** run
  `python scripts/check_anncsu_address_store.py --probe "via goethe 1, Palermo:082053"`
  with that environment's DSN. It prints each loaded region (schema, counts,
  geocoded share, source version, indexes) and exits 1 if no region is loaded
  or the probe is not an exact match. It has been run locally (region 19, probe
  exact in 0.24 s); it has not been run against production because this
  workspace has no production access.
- **Tests that were failing** are fixed: the two idle-handler tests now read
  `finishInitialTileLoad` (the idle logic moved there), and the two
  layer-catalog tests expect the new ANNCSU addresses and CORINE layers and
  the income colour modes. Two duplicate entries in the Italian `.po` were
  removed (kept the committed wording for the SISTER surface message).
- The `anncsu-addresses` layer in `map_layers.py` now accepts the regional
  tables in `public` or `anncsu` (catalog query, union relation and health
  report), and its connection uses `search_path=public,anncsu` because PostGIS
  also lives in `anncsu` in the local database.
- `docs/MAP_SRS.md` has the new requirements MAP-FR-064 to 067, and the ANNCSU
  entry in `source_catalog.py` now states the address-search reuse.
- **Fixes after manual testing (2026-11-10), "via antigone 99, 90151 palermo":**
  (1) five-digit postal codes (CAP) are removed before parsing, so
  "90151 palermo" resolves to Palermo; (2) the municipality name lookup scanned
  `istat.v_comuni_map` (about 3 s per query) and timed out under load, which
  the page showed as "no match". Names now come from an in-memory index of all
  ~7,900 municipalities, loaded once on the first search over the reserved
  search pool and refreshed every 6 hours; if it cannot load, the per-query
  search runs with a longer limit and a retry; if that also fails the API
  returns 503 and the page says the search is unavailable instead of "no match".
- Privacy wording (map card, `/privacy`) now says the server forwards the text
  to Nominatim, which no longer sees the visitor's IP address.

## Phase 0 gate status (2026-11-09)

| Environment | Status |
|---|---|
| Local aecs4u-stats Postgres | **Pass for search.** Region 19 loaded in schema `anncsu`; probe "via goethe 1, Palermo" exact in 0.24 s; gold set 50/50. The `anncsu-addresses` map layer now resolves the schema too (health `available: true`, region 19, schema `anncsu`; a tile and a features query return data). The parcel panel's ANNCSU block now works too: when the installed `aecs4u_stats` reader reports the store `missing` (it reads only `public`), `stats_service` uses the schema-aware twin in `anncsu_lookup` (`region_status`, `civic_addresses_for_geometry`); a Via Goethe parcel returns status `matched` with 19 access points. |
| Production | **Gate open: not verified.** (2026-11-10: the owner accepted continuing without production evidence; the gate itself was not waived.) The workspace has no production database access and `gcloud` needs interactive re-authentication; no public service URL is recorded in the repo. `docs/ZORNADE_GAP_ANALYSIS.md` records Sicily (region 19) as the only ANNCSU region provisioned in the shared source PostgreSQL. |

To close the gate, from a machine with production access, run
`python scripts/check_anncsu_address_store.py --probe "via goethe 1, Palermo:082053"`
with the production DSN. Expected: region 19 listed, a `public` schema (no WARN
line), probe `address`. If tables are in another schema the script prints the
`ALTER TABLE ... SET SCHEMA public` fix. Until then the feature degrades safely:
with no store or an unreachable database, `/api/v1/map/search` returns no address
results (covered by a test), and an explicit Enter falls back to
`/api/v1/map/geocode`. Regions other than Sicily have no ANNCSU data and always
use that fallback.
