# Main.py Refactoring Plan

This document outlines a structured plan to improve the maintainability, performance, and robustness of `land_registry/main.py`.

## Overview

The refactoring is organized into 5 phases, each building on the previous one. Each phase can be completed, tested, and committed independently.

---

## Phase 1: Foundation & Cleanup (Low Risk)

**Goal**: Clean up imports and prepare logging infrastructure

### Tasks

1. **Remove Unused Imports**
   - [ ] Audit all imports at the top of `main.py`
   - [ ] Remove unused: `signal`, `sys`, potentially `STATE` if not used
   - [ ] Verify `map_generator` usage
   - [ ] Run tests to ensure nothing breaks

2. **Replace Print Statements with Logging**
   - [ ] Replace all `print()` calls with `logger.info/warning/error`
   - [ ] Downgrade expected failures (missing S3 credentials) to `logger.debug`
   - [ ] Keep unexpected failures at `logger.warning` or `logger.error`
   - [ ] Ensure log format integrates with Cloud Run

**Files Modified**: `land_registry/main.py`

**Success Criteria**:
- No `print()` statements remain
- All tests pass
- Logs are structured and filterable

**Estimated Time**: 2-3 hours

---

## Phase 2: Cadastral Data Utility Refactoring (Medium Risk)

**Goal**: Eliminate duplicate cadastral loading logic and add caching

### Tasks

1. **Enhance Cadastral Utils** (Already partially done)
   - [x] Create `cadastral_utils.py` with `CadastralData` class
   - [x] Add caching with TTL
   - [ ] **NEW**: Add method to get file availability stats
   - [ ] **NEW**: Add method to expose cache metadata (age, source)

2. **Update Main.py Endpoints**
   - [ ] Replace duplicate loading in `/map` (lines 118-199)
   - [ ] Replace duplicate loading in `/` (lines 236-320)
   - [ ] Both should call `load_cadastral_structure()` from utils
   - [ ] Verify statistics are consistent across endpoints

3. **Add Cache Metadata Endpoint**
   - [ ] Create `/api/v1/cadastral-cache-info` endpoint
   - [ ] Return cache age, source (S3/local/JSON), and statistics
   - [ ] Useful for operators to monitor cache health

**Files Modified**:
- `land_registry/cadastral_utils.py`
- `land_registry/main.py`

**Success Criteria**:
- No duplicate loading logic
- Cache hit rate > 90% in typical usage
- Statistics consistent across all endpoints

**Estimated Time**: 3-4 hours

---

## Phase 3: Panel Server Lifecycle Management (Medium-High Risk)

**Goal**: Replace daemon thread with proper async lifecycle management

### Tasks

1. **Update Panel Startup**
   - [ ] Move Panel server logic into FastAPI lifespan context
   - [ ] Use `asyncio.create_task()` instead of daemon thread
   - [ ] Add proper cancellation signals for shutdown
   - [ ] Detect and surface Panel binding failures

2. **Make Configuration Environment-Aware**
   - [ ] Move hard-coded host/port to `app_settings`
   - [ ] Make `allow_websocket_origin` configurable
   - [ ] Support different configs for dev/staging/production
   - [ ] Document environment variables in `.env.example`

3. **Add Health Checks**
   - [ ] Verify Panel server is reachable during startup
   - [ ] Add retry logic with exponential backoff
   - [ ] Fail fast if Panel cannot start after N attempts
   - [ ] Log Panel URL on successful startup

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

## Phase 4: Panel Table Endpoints Alignment (Low-Medium Risk)

**Goal**: Fix table endpoints to use correct Panel routes

### Tasks

1. **Audit Panel Document Routes**
   - [ ] Verify Panel server exposes `/app_panel/map_table`
   - [ ] Verify Panel server exposes `/app_panel/adjacency_table`
   - [ ] Verify Panel server exposes `/app_panel/mapping_table`
   - [ ] Document actual Panel routing in comments

2. **Fix Landing Page Table Embeds** (lines 206-216)
   - [ ] Update `map_table` to use `/app_panel/map_table`
   - [ ] Update `adjacency_table` to use `/app_panel/adjacency_table`
   - [ ] Update `mapping_table` to use `/app_panel/mapping_table`
   - [ ] Test each tab shows different content

3. **Add Panel Route Constants**
   - [ ] Define Panel routes in `settings.py` or constants file
   - [ ] Use constants instead of hard-coded URLs
   - [ ] Makes it easier to update Panel routing

**Files Modified**:
- `land_registry/main.py` (lines 206-216, 183-209)
- `land_registry/settings.py` (optional)

**Success Criteria**:
- Each tab shows unique content
- No duplicate Panel document loads
- Panel routes centralized and documented

**Estimated Time**: 2-3 hours

---

## Phase 5: API Endpoint Improvements (Low-Medium Risk)

**Goal**: Optimize table data endpoints and handle unimplemented features

### Tasks

1. **Optimize get_table_data** (lines 342-385)
   - [ ] Fix double geometry drop bug
   - [ ] Replace `df.astype(str).apply(...)` with efficient search
   - [ ] Precompute lowercase column for global search
   - [ ] Consider restricting search to specific columns
   - [ ] Add pagination metadata (current_page, total_pages, etc.)
   - [ ] Benchmark performance improvement

2. **Handle Unimplemented Endpoints** (lines 393-451)
   - [ ] Replace empty placeholders with proper 503 responses
   - [ ] Add feature flags to enable/disable endpoints
   - [ ] Document which features are not yet implemented
   - [ ] Provide expected timeline in error messages

3. **Expose Cache Metadata in /cadastral-data** (lines 407-449)
   - [ ] Include `uncached_files` in response (currently computed but not returned)
   - [ ] Add `cache_timestamp` to show freshness
   - [ ] Add `cache_source` (S3/local/JSON) for transparency
   - [ ] Consider adding pagination for large structures

4. **Add Response Models**
   - [ ] Create Pydantic models for API responses
   - [ ] Ensures consistent response structure
   - [ ] Auto-generates OpenAPI documentation
   - [ ] Makes frontend integration easier

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

## Phase 6: Testing & Documentation (Critical)

**Goal**: Ensure all changes are tested and documented

### Tasks

1. **Unit Tests**
   - [ ] Test cadastral data loading with mocked S3
   - [ ] Test cache TTL expiration
   - [ ] Test Panel server startup/shutdown
   - [ ] Test table data pagination and search
   - [ ] Test error handling for all edge cases

2. **Integration Tests**
   - [ ] Test full app startup in dev mode
   - [ ] Test full app startup in production mode
   - [ ] Test Panel table endpoints return correct data
   - [ ] Test cache behavior under load

3. **Documentation**
   - [ ] Update README with new environment variables
   - [ ] Document Panel configuration
   - [ ] Add troubleshooting guide
   - [ ] Update API documentation

4. **Performance Testing**
   - [ ] Benchmark table data endpoint with 10k+ rows
   - [ ] Measure cache hit rate over time
   - [ ] Test concurrent request handling
   - [ ] Profile memory usage

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
