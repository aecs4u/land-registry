# FastAPI Application Quality Audit

**Date:** 2026-02-19
**Branch:** `feature/issue-06-microzone-ui`

## Codebase Summary

- **23 Python modules** in `land_registry/`, **3 routers**, **14 scripts**, **~12 active test files**
- FastAPI backend + Leaflet frontend + Panel dashboard (embedded via Bokeh server)
- S3, GCS, SpatiaLite, Neon PostgreSQL integrations
- Clerk authentication via optional `aecs4u-auth` package

---

## 1. CRITICAL SECURITY ISSUES

### 1.1 Secrets committed to version control

**Severity: CRITICAL** | `.env`

The `.env` file is tracked in git and contains **real production credentials**:

| Secret | Line |
|--------|------|
| Google OAuth client secret | `:11-12` |
| Clerk secret key | `:14` |
| AWS access key ID + secret | `:28-29` |
| Neon PostgreSQL password | `:54` |
| Logfire token | `:69` |

**Fix:** Immediately rotate all exposed credentials. Add `.env` to `.gitignore`. Use `.env.example` with placeholder values only.

---

### 1.2 SQL Injection in SpatiaLite query

**Severity: CRITICAL** | `land_registry/spatialite.py:68-75`

The `load_layer()` function interpolates user-supplied `table` and `where` parameters directly into SQL:

```python
sql = f"SELECT *, ST_AsBinary({spatialite_settings.geometry_column}) AS geom FROM {table_name}"
if where:
    sql += f" WHERE {where}"
```

The `where` parameter comes directly from the API request body at `land_registry/routers/api.py:158-163` via the `/load-spatialite/` endpoint. An attacker can execute arbitrary SQL.

**Fix:** Use parameterized queries. For `WHERE` clauses, build conditions programmatically using an allowlist of columns and operators rather than accepting raw SQL strings.

---

### 1.3 JWT decoded without signature verification

**Severity: HIGH** | `land_registry/routers/api.py:261-282`

`get_user_from_token()` decodes JWT payloads using raw base64 without any signature verification:

```python
payload_part = token.split('.')[1]
payload_part += '=' * (4 - len(payload_part) % 4)
payload = base64.b64decode(payload_part)
user_data = json.loads(payload)
return user_data.get('sub')
```

While marked deprecated, it's still importable and referenced. Any client can forge an arbitrary user ID.

**Fix:** Remove this function entirely. All call sites should use the verified `get_current_user` dependency from `aecs4u-auth`.

---

### 1.4 XSS via URL injection in auth pages

**Severity: MEDIUM** | `land_registry/routers/auth_pages.py:38-72`

The `login_page` renders the `next` query parameter directly into JavaScript inside an f-string HTML template:

```python
after_sign_in = next or auth_settings.after_sign_in_url
# ... later in the HTML:
# window.location.href = "{after_sign_in}";
```

A crafted URL like `/auth/login?next=";alert(1);//` would break out of the string literal.

**Fix:** Either validate `next` against an allowlist of internal paths, or use proper HTML templating with auto-escaping (Jinja2).

---

## 2. ARCHITECTURAL ISSUES

### 2.1 Extensive mutable global state

**Severity: HIGH**

The application relies heavily on module-level mutable globals, making it unsafe for multi-worker deployments and very hard to test:

| Global | Location | Issue |
|--------|----------|-------|
| `current_gdf` | `land_registry/map.py` | In-process GeoDataFrame shared across all requests |
| `current_layers` | `land_registry/map.py` | Shared layers dict |
| `_panel_server`, `_panel_thread` | `land_registry/main.py:57-60` | Embedded server state |
| `_s3_storage` | `land_registry/s3_storage.py:422` | Singleton S3 client |
| `_storage_manager` | `land_registry/storage.py:60` | Singleton storage |
| `_sync_db`, `_async_db` | `land_registry/database.py:250-251` | DB connections |
| `_anonymous_save_timestamps` | `land_registry/routers/api.py:852` | Rate limit state |
| `STATE` | `land_registry/dashboard.py:15` | SharedState singleton |

With `current_gdf` being a single in-process variable, **one user's data load overwrites another user's view**. This makes the app effectively single-user.

**Fix:** Consider a session-based or user-scoped data store (e.g., Redis-backed, or per-session temp files). For storage/db singletons, use FastAPI's dependency injection with `Depends()`.

---

### 2.2 Duplicate class definitions

**Severity: MEDIUM**

- `DatabaseSettings` is defined both in `land_registry/config.py:153` and `land_registry/database.py:23` with different fields
- `S3Settings` is defined both in `land_registry/config.py:118` and `land_registry/s3_storage.py:27` with different implementations

This leads to confusion about which settings instance is actually used. Import order determines behavior.

---

### 2.3 API endpoints split across `app` and `api_router`

**Severity: LOW** | `land_registry/main.py:569-707`

Three data endpoints (`/api/v1/table-data`, `/api/v1/adjacency-data`, `/api/v1/mapping-data`) are defined directly on the `app` object in `main.py` rather than on `api_router`. This bypasses any middleware or dependencies configured on the router.

---

### 2.4 Embedded Panel/Bokeh server in the FastAPI process

**Severity: MEDIUM** | `land_registry/main.py:74-116`

Running a Tornado-based Panel server in a separate thread inside the FastAPI process creates:
- Complex lifecycle management (startup/shutdown/hot-reload)
- Port conflicts during development
- Incompatibility with multiple uvicorn workers
- Extra thread and IOLoop overhead

**Recommendation:** Run Panel as a separate service, or switch to a simpler table rendering approach (HTMX + server-rendered HTML, or client-side tabulator.js).

---

## 3. PERFORMANCE ISSUES

### 3.1 Synchronous blocking calls in async endpoints

**Severity: HIGH**

Multiple `async def` endpoints perform blocking I/O without `asyncio.to_thread()`, starving the event loop:

| Endpoint | Blocking call | Location |
|----------|--------------|----------|
| `/upload-qpkg/` | `extract_qpkg_data()` | `land_registry/routers/api.py:318` |
| `/get-attributes/` | `iterrows()` on GeoDataFrame | `land_registry/routers/api.py:463` |
| `/load-public-geo-data/` | `boto3 S3 get_object` + `gpd.read_file` | `land_registry/routers/api.py:684-699` |
| `/get-regions/` etc. | `json.load()` on files | `land_registry/routers/api.py:585-672` |
| `/generate-map/` | folium map rendering | `land_registry/routers/api.py:334-404` |
| `/cadastral-data` | Entire cadastral structure processing | `land_registry/main.py:466-566` |
| `/map` | `_build_main_map_shell_context()` | `land_registry/main.py:339-397` |

**Fix:** Wrap all CPU/IO-bound work in `await asyncio.to_thread(...)` or use `def` (non-async) endpoints.

### 3.2 Inefficient `iterrows()` usage

**Severity: MEDIUM** | `land_registry/routers/api.py:463`

`get_attributes()` iterates through a GeoDataFrame using `iterrows()`, the slowest iteration method in pandas:

```python
for idx, row in current_gdf.iterrows():
    row_data = {"index": idx}
    for col in current_gdf.columns:
        ...
```

**Fix:** Use `df.drop(columns='geometry').to_dict('records')` which is orders of magnitude faster.

### 3.3 Unbounded in-memory data

**Severity: MEDIUM**

`current_gdf` can grow unboundedly (the `/load-cadastral-files/` endpoint concatenates GeoDataFrames). There is no size limit, eviction policy, or memory pressure monitoring.

---

## 4. API DESIGN ISSUES

### 4.1 Untyped request body

**Severity: MEDIUM** | `land_registry/routers/api.py:979`

`load_multiple_cadastral_files` accepts raw `dict` instead of a Pydantic model:

```python
async def load_multiple_cadastral_files(request: dict):
```

This bypasses all input validation and OpenAPI schema generation. There's even a `CadastralFileRequest` model defined at `land_registry/routers/api.py:76` that could be used.

### 4.2 Error messages leak internals

**Severity: MEDIUM**

Many endpoints return raw exception messages to the client:

```python
raise HTTPException(status_code=500, detail=f"Error loading cadastral file: {str(e)}")
```

This can expose file paths, connection strings, SQL errors, and stack traces.

**Fix:** Log full exceptions server-side; return generic error messages to clients. In debug mode, return details.

### 4.3 Inconsistent response shapes

**Severity: LOW**

Some endpoints return structured models (`TableDataResponse`), while others return ad-hoc dicts with varying keys (`success`, `geojson`, `feature_count`, etc.). No standard envelope pattern.

### 4.4 Dead code

**Severity: LOW** | `land_registry/routers/api.py:992`

```python
request.get('file_types', [])  # Return value discarded
```

---

## 5. CONFIGURATION & DEPENDENCY ISSUES

### 5.1 `moto` in production dependencies

**Severity: MEDIUM** | `pyproject.toml:42`

```toml
dependencies = [
    ...
    "moto>=5.1.13",  # This is a test-only mocking library
]
```

`moto` (and its large transitive dependency tree) is listed in production dependencies when it should only be in the `dev` dependency group.

### 5.2 Module-level settings mutation

**Severity: MEDIUM** | `land_registry/config.py:337-401`

Settings objects are mutated at module import time based on environment detection:

```python
if os.getenv("ENVIRONMENT") == "production":
    app_settings.debug = False
    ...
```

This makes behavior unpredictable during testing (import order matters) and prevents proper settings isolation.

### 5.3 Hardcoded paths

**Severity: LOW** | `land_registry/config.py:219`

```python
fgb_directory: str = "/mnt/mobile/data/aecs4u.it/land-registry"
```

Machine-specific paths as defaults will fail on other developer machines or in CI.

---

## 6. TEST QUALITY

### 6.1 Very low coverage threshold

**Severity: MEDIUM** | `pyproject.toml:93`

```toml
"--cov-fail-under=20"
```

A 20% minimum coverage threshold provides almost no safety net.

### 6.2 Large archive of abandoned tests

**Severity: LOW**

12 test files in `tests/archive/` suggest repeated test rewrites rather than iterative improvement. Several archived tests have names like `test_coverage_boost.py` and `test_final_coverage_push.py`, indicating tests written to inflate metrics rather than verify behavior.

---

## 7. MISSING CAPABILITIES

| Missing | Impact |
|---------|--------|
| **CORS middleware** | Frontend served from different origin will be blocked |
| **Request body size limits** | File uploads have no enforced max size at the framework level |
| **Structured logging** | Logging uses basic `logging.basicConfig` - no JSON format for production |
| **Graceful degradation for Panel** | If Panel fails to start, the `/map` page will still try to render Bokeh embeds that won't load |
| **Database migrations** | PostgreSQL tables are created via raw SQL in code - no migration tool (Alembic) |
| **Health check depth** | `/health` returns hardcoded `{"status": "healthy"}` without checking DB, S3, or Panel connectivity |

---

## Priority Summary

| Priority | Count | Items |
|----------|-------|-------|
| **CRITICAL** | 2 | Secrets in git, SQL injection |
| **HIGH** | 3 | JWT without verification, global mutable state, blocking async |
| **MEDIUM** | 9 | XSS, duplicate classes, Panel embedding, moto in prod deps, iterrows, untyped endpoints, error leaking, settings mutation, low coverage |
| **LOW** | 5 | Inconsistent responses, dead code, hardcoded paths, endpoint placement, archived tests |

**Recommendation:** Tackle the two CRITICAL items (credential rotation + SQL injection fix) immediately, followed by the HIGH items in the next sprint.
