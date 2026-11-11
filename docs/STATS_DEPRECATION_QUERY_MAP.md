# aecs4u-stats deprecation: where each land-registry relation goes

`aecs4u-stats` (FDW mirror) is deprecated. Mapping source: `aecs4u-stats/aecs4u_stats/source_registry.py`
(verified against the live source databases on 2026-11-10). Classes:

- **A** single source: query the source database directly (relation shown below).
- **B** cross-source join: view in the geometry-owning database over narrow foreign tables
  (`scripts/sql/istat-municipality-metric-map-views.sql`).
- **C** stats-owned data with no source database: destination still to be decided.

| Stats relation | Class | Target (database.schema.relation) |
|---|---|---|
| istat.v_{comuni,province,regioni,ripartizioni}_map | A | istat.public.v_*_map |
| istat.elenco_comuni | A | istat.stats_export.elenco_comuni |
| serving.comune_population_by_year | A | istat.stats_export.comune_population_by_year |
| geo.municipalities / provinces, serving.municipalities / provinces / regions / municipality_population_stats | A | eurostat.stats_export.* |
| serving.irpef_comuni, mef_irpef.income_average_* | A | mef_irpef.mef_irpef.* |
| serving.relocation_* | A | relocation.public.relocation_* |
| census_sections.sections | A | census_sections.stats_export.sections |
| hazards.* | A | hazards.hazards.* |
| agenziademanio.concession_* | A | agenziademanio.agenziademanio.* |
| sister.v_sister_* | A | sister.sister_legacy.v_sister_* (legacy schema; confirm with the sister repo) |
| verdict.npl_asteflorio_* | A | verdict.asteflorio.* (names matched with eurostat geography in Python, two connections) |
| omi.omi_ref_zone, omi_fact_quotazioni_valori | A | omi.public.* |
| osm_pois.osm_pois | A | osm_pois.osm_pois.osm_pois |
| zornade.zornade_zone_omi | A | zornade.stats_export.zornade_zone_omi |
| pvp.v_map_sale_points / v_map_sales | A | pvp_enriched.modelview.* |
| solar.solar_potential_comuni joined to istat.v_comuni_map | B | istat.lr_map.v_solar_potential_comuni_map |
| serving.irpef_comuni joined to istat.v_comuni_map | B | istat.lr_map.v_mef_income_comuni_map |
| facts.poi, poi_category, tax_fact, market_quote_fact | A | aecs4u_domain.facts.* |
| spatial.protection_area, spatial.ispra_coastal_dynamics, serving.parcel_enrichment_read_model | C | land_registry.<same schema.table> (copied) |
| geo.geo_*, other spatial.* (empty in stats), serving.comuni (casts/filters) | C | not copied; see below |

## Connection settings

`STATS_POSTGRES_DSN` now only supplies host, port and credentials: the app replaces its database name
with the source database per layer/query. Override one source with `AECS4U_SOURCE_<DATABASE>_DATABASE_URL`
(for example `AECS4U_SOURCE_ISTAT_DATABASE_URL`). PostGIS lives in a database-named schema in `cap`,
`hazards`, `egms`, `census_sections` and `osm_pois`; pools set that search path automatically.

## Corrections found while verifying (2026-11-10)

- OMI: use the indexed base tables `omi.omi_ref_zone` / `omi.omi_fact_quotazioni_valori`; the `public.*`
  names are compatibility views (a `count(*)` over them took 7-10 s).
- MEF: the schema is `mef_irpef`, not `public` (the old stats foreign tables pointed at `public` and failed).
- SISTER views live in `sister_legacy`.
- `lr_map.v_solar_potential_comuni_map` / `v_mef_income_comuni_map` read local materialized views
  (`lr_map.mv_*`, unique-indexed). Refresh after a solar or MEF reload: `select lr_map.refresh_metric_views();`

## Stats-owned data moved to the `land_registry` database (2026-11-10)

Only three of the Stats-owned tables held data; they were copied (row counts verified equal) into
`land_registry`, keeping their schema names so queries only change connection:

| Table | Rows |
|---|---|
| `spatial.protection_area` (landscape / heritage constraints, GiST on `geom`) | 8,452 |
| `spatial.ispra_coastal_dynamics` | 99,229 |
| `serving.parcel_enrichment_read_model` | 87 |

Notes: PostGIS in `land_registry` lives in the schema `land_registry`, so connections use
`search_path=land_registry,public`; the foreign key from `protection_area.dataset_release_id` to the
retiring `catalog.dataset_release` was dropped (the column stays). The stats copies are untouched and can
be dropped with the stats database.

All other canonical tables (`spatial.cadastral_*`, `spatial.market_zone`, `spatial.postal_zone`,
`spatial.hazard_area`, `spatial.protection_coverage`, every `geo.geo_*`) are **empty** in stats and were not
copied: cadastral layers come from the dedicated `cadastral` database, and the code treats a missing
`protection_coverage` as "unknown".

## Remaining references to the stats database

Everything that reads live data now uses the owning database. What is left only touches **empty** canonical
tables and degrades to "unavailable":

- the canonical branches of the synchronous `_PostgresStatsSource` (`municipality_by_cadastral_code`,
  `omi_quotes_by_cadastral_code`, `income_profile_by_cadastral_code`, `context_for_parcel`) and of the async
  `omi_*` / `income_profile_*` / `context_for_parcel` methods (`geo.*`, `spatial.market_zone`, `facts.*`);
- `pvp_sales.py` keeps `pvp.map_sale_points_local` (stats copy) as its last-resort fallback after
  `pvp_enriched` (explicit DSN, or derived from the server settings);
- scripts under `scripts/sql/*-fdw-pushdown.sql` and `scripts/apply_map_sql.py`, which tune the stats FDW
  servers and are obsolete once the stats database is dropped.

Moved in the second pass: `datashader_service` (reads the `cadastral` database), the synchronous source's
Eurostat/ISTAT lookups (`stats_export`), and its parcel read model (`land_registry`).
