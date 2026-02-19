# Zone Workflow Architecture and Development Plan

This document defines the target architecture for zone-based workflows and the implementation plan.

## 1. Product scope and user cases

Core user cases:
- Display cadastral regions.
- Import cadastral regions from files and/or databases.
- Analyze cadastral regions.
- Create and manage zones.

Extended user cases:
- Point lookup: submit longitude/latitude and identify the containing cadastral region.
- Zone overlay lookup: submit a zone and return cadastral regions that are `within` and/or `intersect` it.

## 2. Target architecture

### 2.1 Application shell and information architecture

- Canonical shell route: `/map`.
- `/` redirects to `/map` to avoid split user experiences.
- Sidebar is organized by user jobs:
  - `Data Catalog`
  - `Data`
  - `Explore`
  - `Analyze`
  - `Zones`
  - `Actions`
  - `Account`

### 2.2 Domain model

`zones` and `microzones` are first-class persisted entities.

`zones`:
- Parent entity owned by user (`user_id`).
- Geometry (`geojson`) plus metadata (`name`, `description`, `zone_type`, `color`, `tags`, `is_visible`).
- Derived metrics (`area_sqm`, centroid).

`microzones`:
- Child entity linked via FK `zone_id`.
- Same geometry + metadata shape as zones (`microzone_type`, `is_visible`, etc.).
- Enforced parent-child lifecycle (`ON DELETE CASCADE`).

Rules layer:
- Shared rule module `land_registry/zone_rules.py` defines microzone warning threshold logic.
- Current threshold: `0.3 km^2` (warning when strictly greater).

### 2.3 API architecture

Zone API:
- `POST /api/v1/zones/`
- `GET /api/v1/zones/`
- `GET /api/v1/zones/{zone_id}`
- `PATCH /api/v1/zones/{zone_id}`
- `DELETE /api/v1/zones/{zone_id}`
- `GET /api/v1/zones/geojson`
- `POST /api/v1/zones/visibility`

Microzone API (nested):
- `POST /api/v1/zones/{zone_id}/microzones/`
- `GET /api/v1/zones/{zone_id}/microzones/`
- `GET /api/v1/zones/{zone_id}/microzones/{microzone_id}`
- `PATCH /api/v1/zones/{zone_id}/microzones/{microzone_id}`
- `DELETE /api/v1/zones/{zone_id}/microzones/{microzone_id}`

Spatial lookup APIs (planned):
- Point-in-cadastre lookup endpoint.
- Zone overlay lookup endpoint with relation filter (`within`, `intersects`).

### 2.4 Frontend architecture

Primary UI implementation:
- `land_registry/templates/index.html`
- `land_registry/static/map.js`
- `land_registry/static/styles.css`

State model:
- Zone map layer cache: `window.savedZones`.
- Zone layer group: `window.zoneLayerGroup`.
- Microzone UI cache: `window.zoneMicrozones`.

Behavioral principles:
- Sidebar updates are API-driven and incremental (no full-page reload).
- Visibility toggles are source of truth in DB and mirrored in UI/map.
- Zone cards are expandable and host all microzone controls.

## 3. Workflow design

### 3.1 Zone workflow

1. Draw geometry on map.
2. Click `New Zone`.
3. Save zone metadata.
4. Zone appears in list and map.
5. User can rename/delete/toggle visibility and expand details.

### 3.2 Microzone workflow

1. Expand a zone card.
2. Click `Add Microzone` after drawing geometry.
3. Microzone is created under parent zone.
4. User can rename/delete/toggle visibility.
5. UI shows:
   - Per-zone microzone count.
   - Area badge in km^2.
   - Warning badge when area exceeds threshold.
   - Empty state when no microzones exist.

### 3.3 Planned spatial lookup workflows

Point lookup:
1. User submits longitude/latitude via form or API.
2. Backend performs point-in-polygon query.
3. Response includes matched cadastral entity and identifiers.

Zone overlay lookup:
1. User selects or submits a zone geometry.
2. Backend computes spatial relation against cadastral dataset.
3. Response returns matching cadastral entities by relation.

## 4. Development plan and status

Plan source:
- `docs/github_issues/zone_architecture/01-10`

Completed:
- Issue 01: unify app shell and task-first sidebar IA.
- Issue 02: zone/microzone normalized schema + migration path.
- Issue 03: zone CRUD API on normalized model.
- Issue 04: nested microzone CRUD API.
- Issue 05: zone panel UI CRUD, counters, and card structure.

In progress:
- Issue 06: microzone UI area badges and warning behavior.

Planned next sequence:
1. Issue 07: bulk selection and sidebar search.
2. Issue 08: point lookup + zone-overlay lookup APIs and forms.
3. Issue 09: draw-all-micros map rendering + keyboard navigation.
4. Issue 10: zone-driven Sales/Rents navigation contracts.

## 5. Delivery model

Branching model per issue:
1. Create feature branch `feature/issue-XX-...`.
2. Implement code + tests.
3. Commit and push feature branch.
4. Merge into `main` with merge commit.
5. Push `main`.
6. Move to next issue.

Definition of done for each issue:
- Acceptance criteria in issue markdown are met.
- API/schema/UI consistency is preserved.
- At least one targeted test exists for new business rules.
- Basic static checks pass (`py_compile`, JS syntax checks).

## 6. Key implementation files

Backend:
- `land_registry/sqlite_db.py`
- `land_registry/routers/api.py`
- `land_registry/models.py`
- `land_registry/zone_rules.py`

Frontend:
- `land_registry/templates/index.html`
- `land_registry/static/map.js`
- `land_registry/static/styles.css`

Planning:
- `docs/github_issues/zone_architecture/`

