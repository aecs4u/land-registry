# Local PostgreSQL geographic primary-key groups

Snapshot: 2026-10-07

This inventory groups geographic identifiers by entity type and marks PostgreSQL relation kind. Names use `database.schema.table` form. “Normal/ordinary” means a local PostgreSQL table (`relkind = 'r'`); “foreign” means a foreign table (`relkind = 'f'`).

Foreign tables do not have a locally declared primary key in the entries below. Where a foreign table maps to a canonical geographic table, its remote target is shown separately; that does not mean PostgreSQL enforces the remote primary key on the foreign table.

## Region

### Normal tables with a local PK

| PK field | PostgreSQL type | Tables |
| --- | --- | --- |
| `code` | `text` | `pvp.pvp.ref_pvp_regions`; `asteannunci.public.ref_pvp_regions`; `asteavvisi.public.ref_pvp_regions`; `astegiudiziarie.public.ref_pvp_regions`; `asteravenna.public.ref_pvp_regions`; `vench.modelview.ref_regions`; `venditegiudiziarie.modelview.ref_regions`; `zvg_portal.modelview.ref_regions` |
| `id` (surrogate) | `bigint` | `venditegiudiziarie.modelview.regions` |

### Foreign table aliases (no local PK)

`<database>.modelview.ref_pvp_regions` exists as a foreign table in: `astagiudiziaria`, `astalegale`, `asteannunci`, `asteavvisi`, `astegiudiziarie`, `asteravenna`, `astetelematiche`, `canaleaste`, `demo`, `encherespubliques`, `immobiliallasta`, `infoencheres`, `licitor`, `miglioriaste`, `propertyauctions`, `pvp_enriched`, `spazioaste`, `subastas_judiciales`, `vench`, `venditegiudiziarie`, `zvg_portal`. These map to remote `pvp.ref_pvp_regions`; the canonical table `pvp.pvp.ref_pvp_regions` has PK `code text`.

## Province

### Normal tables with a local PK

| PK field | PostgreSQL type | Tables |
| --- | --- | --- |
| `code` | `text` | `pvp.pvp.ref_pvp_provinces`; `asteannunci.public.ref_pvp_provinces`; `asteavvisi.public.ref_pvp_provinces`; `astegiudiziarie.public.ref_pvp_provinces`; `asteravenna.public.ref_pvp_provinces`; `vench.modelview.ref_provinces`; `venditegiudiziarie.modelview.ref_provinces`; `zvg_portal.modelview.ref_provinces` |
| `id` (surrogate) | `bigint` | `astagiudiziaria.astagiudiziaria.astagiudiziaria_provinces`; `subastas_judiciales.modelview.astagiudiziaria_provinces`; `venditegiudiziarie.modelview.provinces` |

### Foreign table aliases (no local PK)

`<database>.modelview.ref_pvp_provinces` exists as a foreign table in the same 21 databases listed under Region. These map to remote `pvp.ref_pvp_provinces`; the canonical table `pvp.pvp.ref_pvp_provinces` has PK `code text`.

## Municipality / comune

### Normal tables with a local PK

| PK field | PostgreSQL type | Tables |
| --- | --- | --- |
| `code` | `text` | `pvp.pvp.ref_pvp_municipalities`; `asteannunci.public.ref_pvp_municipalities`; `asteavvisi.public.ref_pvp_municipalities`; `astegiudiziarie.public.ref_pvp_municipalities`; `asteravenna.public.ref_pvp_municipalities`; `vench.modelview.ref_municipalities`; `venditegiudiziarie.modelview.ref_municipalities`; `zvg_portal.modelview.ref_municipalities`; `istat.release_r1.elenco_comuni` |
| `source_code` (legacy-code crosswalk) | `text` | `pvp.istat_boundaries.municipality_code_aliases` — 88 aliases to current ISTAT codes |
| `comune_id` | `bigint` | `omi.public.omi_ref_comuni`; `omi.public.omi_fact_compravendite_commerciali`; `omi.public.omi_fact_compravendite_pertinenze`; `omi.public.omi_fact_compravendite_residenziali` |
| `id` (surrogate) | `bigint` | `venditegiudiziarie.modelview.municipalities`; `real_estates.public.survey_municipalities` |
| `(municipality_id, year)` (composite) | `(bigint, bigint)` | `venditegiudiziarie.modelview.municipality_population_stats` — municipality plus reporting year |

### Foreign table aliases (no local PK)

`<database>.modelview.ref_pvp_municipalities` exists as a foreign table in the same 21 databases listed under Region. These map to remote `pvp.ref_pvp_municipalities`; the canonical table `pvp.pvp.ref_pvp_municipalities` has PK `code text`.

## OMI zone

### Normal tables with a local PK

| PK field | PostgreSQL type | Tables |
| --- | --- | --- |
| `zona_id` | `bigint` | `omi.public.omi_ref_zone`; `omi.public.omi_fact_quotazioni_zone` |
| `id` (surrogate) | `bigint` | `real_estates.public.ref_omi_zone` — the table's `zone_code` is not its primary key |

## Other geographic areas and locations

### Normal tables with a local PK

| Geographic key | PostgreSQL type | Tables |
| --- | --- | --- |
| `key` | `text` | `aecs4u_energy.public.pvgis_locations` |
| `(location_key, year, month)` (composite) | `(text, integer, integer)` | `aecs4u_energy.public.pvgis_monthly` — location plus reporting period |
| `id` (surrogate) | `bigint` | `venditegiudiziarie.modelview.geographic_areas`; `sister.sister.geographic_places` |
| `id` (surrogate) | `integer` | `jam.jam.locations` |

## ISTAT and ISTAT-boundaries relations

The `istat` database is present. Its only geographic PK found in this scan is `istat.release_r1.elenco_comuni.code` (`text`), already listed under Municipality / comune. Other administrative relations are present but do not declare a PK:

| Relation(s) | PostgreSQL relation kind | Local PK |
| --- | --- | --- |
| `istat.public.comuni`; `istat.public.province`; `istat.public.regioni`; `istat.public.ripartizioni`; `istat.legacy_stats_archive.elenco_comuni_r1_preconsolidation` | Normal table | None declared |
| `istat.istat_comuni.elenco_comuni`; `istat.public.v_comuni_map`; `istat.public.v_province_map`; `istat.public.v_regioni_map`; `istat.public.v_ripartizioni_map`; `istat.stats_export.comuni`; `istat.stats_export.elenco_comuni_r1_snapshot`; `istat.stats_export.province`; `istat.stats_export.regioni`; `istat.stats_export.ripartizioni` | View | None declared |
| `aecs4u-stats.istat_boundaries.comuni`; `aecs4u-stats.istat_boundaries.province`; `aecs4u-stats.istat_boundaries.regioni`; `aecs4u-stats.istat_boundaries.ripartizioni` | View | None declared |
| `pvp.istat_boundaries.comuni` | Foreign table imported from `istat.public.comuni` | None declared locally |
| `pvp.istat_boundaries.elenco_comuni` | Foreign table imported from `istat.release_r1.elenco_comuni` | None declared locally |
| `pvp.modelview.istat_municipality_code_map`; `pvp.modelview.modelview_addresses_with_istat` | View | None declared |
| `aecs4u-stats.istat_boundaries.v_comuni_map`; `aecs4u-stats.istat_boundaries.v_province_map`; `aecs4u-stats.istat_boundaries.v_regioni_map`; `aecs4u-stats.istat_boundaries.v_ripartizioni_map`; `aecs4u-stats.source_istat.elenco_comuni`; `aecs4u-stats.source_istat_archive.elenco_comuni_r1_snapshot`; `aecs4u-stats.source_istat_boundaries.comuni`; `aecs4u-stats.source_istat_boundaries.province`; `aecs4u-stats.source_istat_boundaries.regioni`; `aecs4u-stats.source_istat_boundaries.ripartizioni` | Foreign table | None declared |

There is no separate database named `istat_boundaries` in this local PostgreSQL cluster. `istat_boundaries` is a schema in `aecs4u-stats`; it contains the views and foreign tables shown above.

In `pvp`, `modelview.istat_municipality_code_map` resolves direct `pro_com` codes, ISTAT release-history codes, 88 official SITUAS legacy-code aliases, and unique exact municipality-name matches from the PVP reference. `modelview.modelview_addresses_with_istat` left-joins that map and the live FDW table `istat_boundaries.comuni`; its geometry and names are read from ISTAT rather than copied locally. The `municipality_code_aliases` table stores only the 88 code-to-code relationships, sourced from SITUAS report 129 as of 2026-10-07. The address table remains unchanged.

The latest check preserved all 489,526 address rows and matched all 7,159 distinct nonblank `municipality_code` values. There are 17,843 address rows with no ISTAT match because their `municipality_code` is blank; 56 of those rows have a nonblank `foreign_municipality` label. The SITUAS history supports unique current-code targets for all 88 residual legacy codes. See [ISTAT's administrative-code and change-history portal](https://www.istat.it/classificazione/codici-dei-comuni-delle-province-e-delle-regioni/) for the source.

## Other foreign geographic aliases without a local PK

These relations are foreign tables and have no locally declared primary key, so they are not included in the PK groups above:

- `aecs4u-stats.geo_admin.geographic_areas`, `aecs4u-stats.geo_admin.municipalities`, `aecs4u-stats.geo_admin.municipality_population_stats`, `aecs4u-stats.geo_admin.provinces`, `aecs4u-stats.geo_admin.regions`.
- `aecs4u-stats.source_eurostat.geographic_areas`, `aecs4u-stats.source_eurostat.it__geographic_areas`, `aecs4u-stats.source_eurostat.it__municipalities`, `aecs4u-stats.source_eurostat.it__provinces`, `aecs4u-stats.source_eurostat.it__regions`, `aecs4u-stats.source_eurostat.municipalities`, `aecs4u-stats.source_eurostat.municipality_population_stats`, `aecs4u-stats.source_eurostat.provinces`, `aecs4u-stats.source_eurostat.regions`.

## Classification notes

- The exact paths `asteannunci.public.ref_pvp_regions`, `asteavvisi.public.ref_pvp_regions`, `astegiudiziarie.public.ref_pvp_regions`, `asteravenna.public.ref_pvp_regions`, `vench.modelview.ref_regions`, `venditegiudiziarie.modelview.ref_regions`, and `zvg_portal.modelview.ref_regions` are normal/ordinary tables in the local catalog. Their foreign counterparts are separate relations named `<database>.modelview.ref_pvp_regions`.
- Foreign-table remote targets were read from PostgreSQL foreign-table mappings. Primary-key fields are reported as local constraints only for ordinary tables.
- Generic record IDs are omitted when a table only contains geographic attributes or foreign keys. For example, cadastral record tables and OMI quotation-value rows use record IDs; their geographic columns are not the primary key. No direct cadastral section/parcel or postal-code primary-key group was found in the inspected schemas.
- Repeated `ref_*` tables are listed in each database where they exist; they are separate ordinary tables when identified as such above.
