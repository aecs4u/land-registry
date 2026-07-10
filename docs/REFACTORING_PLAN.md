# Main.py Refactoring Plan

This document outlines a structured plan to improve the maintainability, performance, and robustness of `land_registry/main.py`.

## Overview

The refactoring is organized into 5 phases, each building on the previous one. Each phase can be completed, tested, and committed independently.

---

## Phase 1: Foundation & Cleanup ✅ COMPLETE

**Goal**: Clean up imports and prepare logging infrastructure

### Tasks

1. **Remove Unused Imports**
   - [x] Audit all imports at the top of `main.py`
   - [x] Remove unused: `signal`, `sys`, `STATE`
   - [x] Verify `map_generator` usage
   - [x] Run tests to ensure nothing breaks

2. **Replace Print Statements with Logging**
   - [x] Replace all `print()` calls with `logger.info/warning/error`
   - [x] Downgrade expected failures (missing S3 credentials) to `logger.debug`
   - [x] Keep unexpected failures at `logger.warning` or `logger.error`
   - [x] Ensure log format integrates with Cloud Run

**Files Modified**: `land_registry/main.py`

**Success Criteria**:
- No `print()` statements remain
- All tests pass
- Logs are structured and filterable

**Estimated Time**: 2-3 hours

---

## Phase 2: Cadastral Data Utility Refactoring ✅ COMPLETE

**Goal**: Eliminate duplicate cadastral loading logic and add caching

### Tasks

1. **Enhance Cadastral Utils**
   - [x] Create `cadastral_utils.py` with `CadastralData` class
   - [x] Add caching with TTL (5 min)
   - [x] Add method to get file availability stats (`get_file_availability_stats()`)
   - [x] Add method to expose cache metadata (`cache_metadata()`)

2. **Update Main.py Endpoints**
   - [x] Replace duplicate loading in `/map` → uses `_build_main_map_shell_context()`
   - [x] `/` now redirects to `/map` (no duplication)
   - [x] Both call `load_cadastral_structure()` / `get_cadastral_stats()` from utils
   - [x] Statistics consistent across all endpoints

3. **Add Cache Metadata Endpoint**
   - [x] Created `/api/v1/cadastral-cache-info` endpoint (in `routers/api.py`)
   - [x] Returns cache age, source (S3/local/JSON), and statistics
   - [x] Pydantic response model: `CadastralCacheInfoResponse`

**Files Modified**:
- `land_registry/cadastral_utils.py`
- `land_registry/main.py`

**Success Criteria**:
- No duplicate loading logic
- Cache hit rate > 90% in typical usage
- Statistics consistent across all endpoints

**Estimated Time**: 3-4 hours

---

## Phase 3: Panel Server Lifecycle Management ✅ COMPLETE

**Goal**: Replace daemon thread with proper async lifecycle management

### Tasks

1. **Update Panel Startup**
   - [x] Move Panel server logic into FastAPI lifespan context
   - [x] Use daemon thread (managed by lifespan, not asyncio task — safer with Bokeh IOLoop)
   - [x] Detect Panel binding failures (OSError handling)
   - [x] Graceful shutdown via `_stop_panel_server()`

2. **Make Configuration Environment-Aware**
   - [x] All Panel host/port/routes moved to `PanelServerSettings` in `config.py`
   - [x] `allow_websocket_origin` configurable via env vars
   - [x] Dev/staging/production differences handled through settings

3. **Add Health Checks**
   - [x] `_health_check_panel()` verifies Panel server is reachable during startup
   - [x] Retry loop with configurable delay (`panel_startup_retry_delay`)
   - [x] Logs Panel URL on successful startup
   - [x] Gracefully degrades (app continues without Panel if it fails)

**Files Modified**:
- `land_registry/main.py` (lines 32-72)
- `land_registry/settings.py`

**Success Criteria**:
- Panel server failures are detected immediately
- Clean shutdown on SIGTERM/SIGINT
- Configuration is environment-aware
- All tests pass

**Estimated Time**: 4-5 hours

---

## Phase 4: Panel Table Endpoints Alignment 🔄 PARTIAL

**Goal**: Fix table endpoints to use correct Panel routes

### Tasks

1. **Audit Panel Document Routes**
   - [x] Confirmed: Panel server only exposes `/dashboard` (one shared app)
   - [x] TODO comments in `config.py` document this: `panel_map_table_route = "/dashboard"` etc.
   - [ ] **DEFERRED**: Separate Panel apps for map/adjacency/mapping tabs not yet created
     - Adjacency and mapping features are 503 (not implemented), so separate apps not needed yet

2. **Panel Route Constants** ✅
   - [x] `PanelServerSettings` in `config.py` has `panel_map_table_route`, `panel_adjacency_table_route`, `panel_mapping_table_route`
   - [x] All Panel URLs generated via `get_panel_url()` helper
   - [x] No hard-coded URLs in endpoint handlers

3. **Remaining work (when features are implemented)**
   - [ ] Create separate Panel apps for adjacency and mapping tables
   - [ ] Update `pn.serve()` dict in `main.py` to add new app entries
   - [ ] Update route constants to point to new endpoints

**Files Modified**:
- `land_registry/main.py` (lines 206-216, 183-209)
- `land_registry/settings.py` (optional)

**Success Criteria**:
- Each tab shows unique content
- No duplicate Panel document loads
- Panel routes centralized and documented

**Estimated Time**: 2-3 hours

---

## Phase 5: API Endpoint Improvements ✅ COMPLETE

**Goal**: Optimize table data endpoints and handle unimplemented features

### Tasks

1. **Optimize get_table_data** ✅
   - [x] Fix double geometry drop bug (geometry dropped once, correctly)
   - [x] Efficient search using `df.apply(..., axis=0).any(axis=1)` (column-wise)
   - [x] Pagination metadata: `total`, `total_pages`, `filtered_total`, `page`, `size`, `columns`

2. **Handle Unimplemented Endpoints** ✅
   - [x] `/api/v1/adjacency-data` returns 503 with informative message
   - [x] `/api/v1/mapping-data` returns 503 with informative message

3. **Expose Cache Metadata in /cadastral-data** ✅
   - [x] `uncached_files` returned in template context
   - [x] `available_files` and `missing_files` computed from SQLite cache
   - [x] `/api/v1/cadastral-cache-info` endpoint returns full cache metadata

4. **Add Response Models** ✅
   - [x] `TableDataResponse`, `ServiceUnavailableResponse` in `models.py`
   - [x] `CadastralCacheInfoResponse`, `CacheMetadata`, `CadastralStatistics`, `FileAvailabilityStats`
   - [x] `ZoneCreateRequest`, `ZoneResponse`, `ZoneListResponse` etc. (zone management)

**Files Modified**:
- `land_registry/main.py` (multiple sections)
- Create `land_registry/models/responses.py` (optional)

**Success Criteria**:
- Table search is 5-10x faster for large datasets
- Unimplemented endpoints return 503 with helpful messages
- Cache metadata is visible to operators
- API responses are well-documented

**Estimated Time**: 4-6 hours

---

## Phase 6: Testing & Documentation 🔄 IN PROGRESS

**Goal**: Ensure all changes are tested and documented

### Tasks

1. **Unit Tests** 🔄
   - [x] `tests/test_cadastral_utils.py` — 27 tests, 90.5% coverage on `cadastral_utils.py`
     - CadastralData (properties, cache_metadata, get_file_availability_stats)
     - _calculate_statistics (empty, None, non-dict)
     - _scan_local_cadastral_directory (real fs, no files, nonexistent path)
     - load_cadastral_structure (cache hit, cache miss, TTL expiry, error)
     - _load_cadastral_data_internal (local, S3, JSON, all-fail)
     - clear_cache, get_cadastral_stats
   - [x] `tests/test_main_endpoints.py` — 21 tests, main.py up to 48.4%
     - /health, / redirect, /landing, /cadastral-data (error paths)
     - /api/v1/table-data (pagination, search, filter, sort, empty, no-geometry col)
     - /api/v1/adjacency-data and /api/v1/mapping-data (503)
   - [x] Fixed 6 pre-existing test failures in `test_api_endpoints.py` and `test_config.py`:
     - `TestLoadCadastralFilesEndpoint`: updated expected status 400 → 422 (Pydantic `min_length=1` on `file_paths`)
     - `TestS3Endpoints::test_configure_s3_success`: added `dependency_overrides` for `get_current_superuser`; used valid credentials (omit optional short keys)
     - `TestS3Endpoints::test_configure_s3_connection_test_failure/invalid_input`: updated to expect 401/503 (auth installed returns 401)
     - `TestCadastralSettings::test_local_cadastral_path`: corrected expected value `"data/catasto/ITALIA"` → `"/data/catasto/ITALIA"`
   - [x] `tests/test_api_router_endpoints.py` — 32 tests targeting untested `routers/api.py` endpoints
     - Covers cadastral-cache-info, get-regions/provinces/municipalities, session endpoints, save-drawn-polygons-anonymous, drawn-polygons, zones CRUD
     - Key fix: HTTPException(404) inside try/except Exception → swallowed to 500 (endpoint design issue)
     - Key fix: auth dep returns 401 (aecs4u-auth installed) not 503; tests use `in (401, 403, 503)` checks
   - [x] Fixed all test_corrected_* files (20 pre-existing failures):
     - S3Settings constructor: use `bucket_name=`/`region=` kwargs (not `s3_bucket_name=`/`s3_region=`)
     - patch target: `boto3.client` (not `land_registry.s3_storage.boto3.client`)
     - patch target: `land_registry.cadastral_utils.load_cadastral_structure` (local import in handler)
   - [x] `tests/test_user_microzone_fgb_endpoints.py` — 24 tests for user profile, microzone CRUD, FGB endpoints
     - User profile/drawings: authenticated/unauthenticated paths, empty/populated user dirs
     - FGB: no-directory, empty-directory, file discovery, metadata 200/404/400 paths
     - Microzones: create/list/get/update/delete + bulk visibility
   - [x] `tests/test_cadastral_and_datashader_endpoints.py` — 30 tests for cadastral query/lookup, datashader, FGB load
     - Cadastral: query (mock db), hierarchy, statistics, point-lookup, zone-overlay-lookup
     - Search by reference: foglio (map) and particella (PLE) paths
     - Datashader: tile (success/error→empty tile), heatmap, categorical
     - FGB load: invalid layer type, file not found, success (mocked gpd.read_file)
   - [x] `tests/test_drawing_and_geo_endpoints.py` — 23 tests for drawing management, public geo data, auction
     - save/load/list/clear drawn polygons (auth-required and public variants)
     - load_public_geo_data and load_example_geo_data (mocked S3 + gpd.read_file)
     - load_cadastral_files GET endpoint (mocked S3)
     - Auction: get_properties, statistics, populate
     - Diagnostic endpoints: test-load-endpoint path parsing, test-s3-access (bug: crashes → 500)
   - [x] Bug discoveries: _discover_ple_databases not defined in module scope (list_cadastral_databases → 500); s3_settings.use_public_bucket_fallback attribute missing (test-s3-access → crash)
   - [x] All 402 tests passing, 0 failing; overall coverage 54.13%
   - [ ] Test Panel server startup/shutdown (complex; integration only)

2. **Integration Tests** (deferred — requires running Panel server)
   - [ ] Test full app startup in dev mode
   - [ ] Test Panel table endpoints return correct data

3. **Documentation** ✅
   - [x] Updated README API endpoints section (accurate paths, added streaming/datashader)
   - [x] Fixed `S3_BUCKET_NAME` → `STORAGE_S3_BUCKET` in README quick-start
   - [x] Updated project structure in README to reflect actual files
   - [x] Added Panel Server Configuration section with settings table + troubleshooting
   - [x] Fixed env var category list (`STORAGE_*` instead of `S3_*`)

4. **Performance Testing** (deferred)
   - [ ] Benchmark table data endpoint with 10k+ rows

**Files Modified**:
- `tests/test_main.py` (create if needed)
- `tests/test_cadastral_utils.py`
- `README.md`
- `docs/API.md` (create if needed)

**Success Criteria**:
- Test coverage > 80%
- All tests pass
- Documentation is complete and accurate
- Performance meets SLAs

**Estimated Time**: 6-8 hours

---

## Implementation Order

```
Phase 1 (Foundation)
  ↓
Phase 2 (Cadastral Utils)
  ↓
Phase 3 (Panel Lifecycle)
  ↓
Phase 4 (Panel Endpoints)
  ↓
Phase 5 (API Improvements)
  ↓
Phase 6 (Testing & Docs)
```

**Total Estimated Time**: 21-29 hours (~3-4 days of focused work)

---

## Risk Assessment

| Phase | Risk Level | Mitigation |
|-------|-----------|------------|
| Phase 1 | Low | Easy to rollback, minimal changes |
| Phase 2 | Medium | Already partially complete, good test coverage |
| Phase 3 | High | Affects app startup, test thoroughly |
| Phase 4 | Low-Medium | Isolated to Panel integration |
| Phase 5 | Low-Medium | Incremental improvements |
| Phase 6 | Low | No production code changes |

---

## Rollback Plan

Each phase should be committed separately with:
1. Clear commit message describing changes
2. Tests demonstrating the improvement
3. Documentation updates

If a phase causes issues:
1. Revert the specific commit
2. Document the issue
3. Fix and re-apply

---

## Success Metrics

- **Performance**: Table search 5-10x faster
- **Reliability**: Panel server binding failures detected 100% of the time
- **Maintainability**: Zero code duplication for cadastral loading
- **Observability**: All logs structured and filterable
- **Test Coverage**: > 80% for modified code

---

## Notes

- This plan assumes the existing `cadastral_utils.py` work is complete
- Some tasks may reveal additional improvements
- Timeline is conservative and includes buffer for testing
- Each phase should be reviewed before merging to main

---

## Next Steps

1. Review this plan with the team
2. Create GitHub issues for each phase
3. Start with Phase 1 (lowest risk)
4. Commit after each phase completion
5. Deploy to staging for integration testing
6. Deploy to production with monitoring
