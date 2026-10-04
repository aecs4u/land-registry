# Protection constraints: aecs4u-stats → land-registry contract

**Status:** proposed on 2026-09-27 for [aecs4u-stats#48](https://github.com/aecs4u/aecs4u-stats/issues/48). land-registry is ready for it now. Until aecs4u-stats publishes the tables below, the two map layers stay out of the catalog, and the parcel panel shows no constraint section.

This is what land-registry reads. Table and column names can change, but they must change in both repositories together. On the land-registry side they live in `land_registry/map_layers.py` (`PROTECTION_AREA_TABLE`, `PROTECTION_COVERAGE_TABLE`, `_PROTECTION_PROPERTIES`).

## `spatial.protection_area` (required)

Store one row per source record, act and category. Never merge rows: two decrees over the same area are two rows, and a parcel lookup returns both.

| Column | Type | Notes |
|---|---|---|
| `id` | `bigint` primary key | Stable within a release. |
| `protection_type` | `text not null` | `landscape` (SITAP) or `cultural_heritage` (Vincoli in Rete, Catalogo Generale). The map ignores other values. |
| `category` | `text` | Legal basis, e.g. `art136`, `art142_c`, `art157`, `art10`, `art45`. Displayed with `_` shown as a space. |
| `source` | `text` | `sitap`, `vincoli_in_rete` or `catalogo_generale`. |
| `source_id` | `text` | The source's own identifier (for SITAP, `codvin`/`codvr`), as property-scraper normalizes it. |
| `source_label` | `text` | The protected object's or area's name (`oggetto`/`denominazione`). |
| `act_reference` | `text` | Decree or act reference (law, number, date). |
| `source_url` | `text` | Link to the source record. Only `http(s)` URLs are rendered as links. |
| `is_binding` | `boolean not null` | `false` for Catalogo Generale records, which show a catalogue entry but not a binding constraint. |
| `source_release` | `text` | property-scraper's `release_id`, or the aecs4u-stats release label. |
| `geom` | `geometry(Geometry, 4326) not null` | Polygons or points. Must be valid (`ST_IsValid`); invalid rows get no overlap figure. |

**Also expected:**
- `source_feature_key` and `dataset_release_id` (FK to `catalog.dataset_release`), as on `spatial.hazard_area`.
- A GiST index on `geom`. The layer health check only reports the layer as available once one exists.
- `ANALYZE` after each load. Without statistics, the planner has repeatedly chosen full scans on these tables (see `MAP_AUDIT_2026-09-26.md`, C1 and H10).

## `spatial.protection_coverage` (optional; needed to say "none recorded")

| Column | Type | Notes |
|---|---|---|
| `protection_type` | `text not null` | As above. |
| `source` | `text` | The source that is complete within `geom`. |
| `geom` | `geometry(Geometry, 4326) not null` | The area where this source is loaded and treated as complete, e.g. region polygons. |

When a parcel intersects no rows of a type, land-registry reports "none recorded" only if a coverage row of that type contains the whole parcel (`ST_Covers`). Everywhere else, including when this table does not exist, it reports **not determined**. Missing or unknown coverage is never presented as unconstrained.

## What land-registry does with them

- **Map layers.** Both layers read `spatial.protection_area`, one per `protection_type`, and are marked partial coverage. The catalog lists them once the table exists; it re-checks every five minutes, without a restart.

  | Layer | Filter | Geometry | From zoom |
  |---|---|---|---|
  | `landscape-constraints` | `protection_type = 'landscape'` | Polygons | z8 |
  | `heritage-protections` | `protection_type = 'cultural_heritage'` | Polygons and points | z10 |

- **Parcel lookup.** `GET /api/v1/enrichment/parcel/constraints/{reference}?id={polygon id}`.
  - The polygon `id` is required, because parcel references are not unique.
  - A row matches when it intersects the parcel. Rows that only touch its boundary are excluded.
  - Each polygon row carries `overlap_ratio`, the share of the parcel it covers. Points have `null`.
  - Up to 100 rows are returned; `truncated` flags more.

  ```json
  {
    "parcel_id": 5226175,
    "status": "published",
    "constraints": [{"id": 1, "protection_type": "landscape", "category": "art136", "source": "sitap",
                     "source_id": "…", "source_label": "…", "act_reference": "…", "source_url": "…",
                     "is_binding": true, "source_release": "…", "overlap_ratio": 0.57}],
    "coverage": {"landscape": "covered", "cultural_heritage": "unknown"},
    "truncated": false
  }
  ```

  `status` is `unavailable` until `spatial.protection_area` exists. The response is 404 for an unknown parcel, and 503 if the query fails, for example on a column mismatch.

- **Parcel panel.** A "Protection constraints" section appears above the optional enrichment. It lists each record with its act, overlap share, source link, and a "catalogue record, not a binding constraint" note where applicable. It ends with "Source data, not a legal certificate; confirm with the competent Soprintendenza."

- **Not wired yet.** The account-settings page does not offer these layers as opening defaults while they are marked `pending_release`. Remove that flag in `map_layers.py` once the tables ship.
