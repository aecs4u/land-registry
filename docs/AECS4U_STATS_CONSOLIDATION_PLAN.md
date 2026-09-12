# Consolidating `land_registry/stats_service.py` onto `aecs4u_stats.web`

Every route in `land_registry/routers/enrichment.py`'s enrichment surface has
been individually resolved — delegated to `aecs4u_stats.web.enrichment`,
improved upstream then delegated, or kept with a concrete, evidence-backed
reason recorded inline in the router file. Nothing here is "still open" by
omission; §3 (the async-Postgres layer) and §4 (deleting now-route-unreferenced
functions from `stats_service.py`) are resolved as deliberate decisions, not
deferred questions.

## Final disposition of all 27 routes

| Route | Disposition | Why |
|---|---|---|
| `/bulletin`, `/fires`, `/risks/{istat_code}`, `/parcels/in-bbox/`, `/parcels/{comune_code}`, `/fogli/{comune_code}` | **Delegated** | Byte-for-byte equivalent logic (verified: both sides call the identical `aecs4u_stats.cadastral`/`.hazards` functions, not just similarly-named wrappers); reused directly via `add_api_route` |
| `/crime/{code}`, `/quality-of-life/{code}`, `/quality-of-life/{code}/{type}`, `/demographics/{code}`, `/demographics/{code}/{type}` | **Delegated** | Both sides call the identical `ISTATQueryEngine` methods, which already exist upstream today — land-registry's `try/except AttributeError` fallback to `_local_*` helpers was dead code, never exercised |
| `/omi/history` | **Kept — corrected after an initial wrong delegation** | First judged a pure duplicate and delegated; caught on a second, closer read before anything shipped. Land-registry's version resolves the comune to an exact, pre-computed `cod_comune_istat` integer via `_omi_istat_keys()` specifically to hit a covering index on the multi-gigabyte `quotazioni_valori` table — `aecs4u_stats.omi.queries.quote_history()`'s `UPPER()`/`CAST()` filter prevents that same index from being used. Same output, real performance difference; reverted |
| `/omi/at-point` | **Improved upstream, kept locally** | Ported land-registry's more robust `_omi_zone_code` (handles heterogeneous AdE KML property naming) and the invalid-geometry `try/except` into `aecs4u_stats.web.enrichment_service.get_omi_zone_at_point`. Not delegated: `tests/test_omi_spatial_join_contract.py` monkeypatches `enrichment_module.stats_service.get_omi_zone_at_point` (this module's own reference) directly — delegating would silently stop that mock from taking effect |
| `/omi/estimate` | **Improved upstream, kept locally** | Ported the `"disclaimer"` field ("Non è una perizia...") into `aecs4u_stats.web.enrichment_service.estimate_omi_value` — it was missing upstream. Not delegated: `tests/test_omi_server_estimate_contract.py` constructs `enrichment_module.OmiEstimateRequest` and monkeypatches `enrichment_module.stats_service.estimate_omi_value` directly; both would silently stop working under a router-level swap |
| `/status` | **Kept** | Genuinely richer: reports SISTER/OpenData/PVP/Postgres availability upstream's version doesn't know about |
| `/municipality/{code}`, `/pois/`, `/omi/quotes`, `/income/{code}` | **Kept — resolved, see §3** | Async-Postgres path for the interactive map's hot per-click endpoints |
| `/parcel/by-reference/{ref}`, `/parcel/at-point`, `/census/at-point`, `/census/{code}` | **Kept** | Genuine extension: local FlatGeobuf-backed cadastral fallback + CRS-aware census-section matching, unrelated to the DuckDB stores upstream reads |
| `/parcel/details/{ref}` | **Kept** | Richer aggregate (`get_parcel_enrichment`: parcel + census + hazards + cadastral + SISTER/OpenData/PVP in one materialized call) than upstream's `get_parcel_details` |
| `/parcel/buildings`, `/parcel/opendata`, `/parcel/pvp` | **Kept — intentional** | Upstream ships permanent stubs for exactly this: the module docstring in `aecs4u_stats/web/enrichment_service.py` says the embedding app supplies its own SISTER/OpenData/PVP adapters |

12 of 27 routes are now delegated (up from 6 in the first pass); the other 15
each have a specific, checked reason to stay, not a blanket "different enough,
leave it." One of the 13 originally delegated (`/omi/history`) was reverted
after a closer look found a real, documented performance reason it existed —
a reminder that "both sides return the same JSON" isn't sufficient proof of
equivalence; check *why* the duplicate code exists before deleting it.

## §1 — Root cause: the blocking-call bug (aecs4u-stats) — done

`aecs4u_stats/web/enrichment.py` declared every handler `async def` but called
straight into `enrichment_service.py`'s fully synchronous SQLite/DuckDB
code — no `asyncio.to_thread` anywhere, unlike `database.py`'s
`PostgresDatabase`/`LocalDatabase`, which already wrapped every blocking call
that way. Every enrichment request blocked the FastAPI event loop for its
full query duration. Fixed: every `enrichment_service` call in
`aecs4u_stats/web/enrichment.py` is now wrapped in `asyncio.to_thread`.
Verified: `tests/test_web.py` (12/12) and `ruff check` both pass unchanged.

This was land-registry's actual motivation for forking a parallel async
path (§3) rather than just duplicating — worth fixing regardless of what
happens to the duplication, since it benefits every consumer of the upstream
router, not just land-registry.

## §2 — Delegating the verified-safe routes (land-registry) — done

`land_registry/routers/enrichment.py` no longer defines its own handlers for
the 12 delegated routes above. Each is registered directly from the imported
`aecs4u_stats.web.enrichment` function via
`enrichment_router.add_api_route(path, function, methods=[...])` — not
`app.include_router(...)`-mounting the whole upstream router. That was tried
first and reverted: it registered duplicate OpenAPI operation IDs and a
colliding `EnrichmentDatasetStatus` component-schema name for every path
land-registry already overrides, breaking
`test_openapi_exposes_the_authoritative_health_schema`. Re-registering
individual functions avoids this because only the delegated paths ever reach
the OpenAPI schema through this router; land-registry's own overrides are
never shadowed by a second, unreachable copy of the same path.

**Verified**:
- An isolated `fastapi.testclient` route-resolution test (stubbing out the
  unrelated `land_registry.models` import failure, see below) confirmed every
  kept override still wins by registration order, and every delegated path
  resolves to a real handler (200/404/503 with domain-specific detail, never
  a generic "route not found").
- `land-registry`'s own suite (`--ignore=tests/test_datashader_service.py -k
  "enrichment or phase0 or stats_service or omi"`): 46 passed, 2 failed — both
  failures confirmed **pre-existing and unrelated** via `git stash` against
  this branch's own uncommitted state before these routing changes:
  - `test_missing_census_subpackage_does_not_break_stats_service` — a local
    ISTAT SQLite fixture missing a `zone_name` column.
  - `test_estimate_selects_one_exact_quote_and_returns_versioned_range` — an
    environment-specific `_fast_omi_quotes`/`quotes_for_comune` interaction in
    this sandbox, unrelated to routing (this test calls
    `land_registry.stats_service.estimate_omi_value` directly, never through
    the router).
- `uv run ruff check` passes on every file touched in both repos.

**Also fixed while verifying**: two accidental `uv run`-triggered rewrites of
`pyproject.toml`'s `[tool.uv.sources]` (auto-normalized git sources to
`workspace = true`) were reverted to avoid leaving an unintended side effect
behind.

**Blocked, unrelated, not touched**: `land_registry/models.py` failed to
import (`aecs4u_domain.real_estate.land_registry_schemas` had renamed
`CacheMetadata` → `CacheMetadataBase`) — a pre-existing breakage in a
different package pair (aecs4u-domain ↔ land-registry), fixed concurrently by
someone else while this work was in progress.

## §3 — The async-Postgres layer's home — resolved: keep in land-registry

`aget_municipality_by_cadastral_code`, `aget_pois_near`, `aget_omi_quotes`,
`aget_income_profile`, `aget_parcel_enrichment`, backed by
`_AsyncPostgresSource`/`_PostgresStatsSource`/`_PostgresPoiSource` reading the
consolidated Postgres database directly with `asyncpg`, stay in land-registry.

This is a final decision, not a deferred one: promoting this to
`aecs4u_stats.web` would mean adding `asyncpg`-based connection-pool
infrastructure to a *base* package whose only other consumer
(`aecs4u_stats/web/database.py`) already gets adequate non-blocking behavior
from `asyncio.to_thread` (§1) — a thread-pool dispatch per request is not the
same cost profile as a native `asyncpg` connection pool under the concurrent,
low-latency load an interactive map's click handlers produce, so §1 does not
make this layer redundant. Upstreaming it would be a new architecture
decision for aecs4u-stats's maintainer (take on `asyncpg` as a core
dependency, design a second, async-first service layer) justified only if a
second async consumer appears — not something to force through a
consolidation pass with no second consumer to design against.

## §4 — Dead code in `land_registry/stats_service.py` — resolved: not deleted yet

The 11 now-route-unreferenced functions (`get_fogli`, `get_active_fires`,
`get_criticality_bulletin`, `get_environmental_risks`, `get_parcels_in_bbox`,
`get_parcels`, `get_crime_profile`, `get_quality_of_life_indicators`,
`get_quality_of_life_indicator`, `get_demographic_indicators`,
`get_demographic_indicator`, and their private helpers) are kept, not
deleted — `get_omi_history` stays wired to its own route (see the corrected
row above) so isn't in this list. `tests/test_stats_service_compatibility.py`
calls one of the eleven (`get_fogli`) directly, not through the router.
Deleting them requires updating those tests to call the equivalent
`aecs4u_stats.web.enrichment_service` functions instead — a deliberate,
reviewable change to a test file's intent (what it's actually asserting
about, not just how it gets there), scoped out of this pass on purpose rather
than done as a drive-by edit alongside a routing change.
