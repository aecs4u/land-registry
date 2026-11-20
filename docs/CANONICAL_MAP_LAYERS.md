# Canonical database map layers

The map consumes an allow-listed catalog of PostGIS relations in their
source-owned databases. The catalog is exposed at `GET /api/v1/map/layers`; deployment
readiness is exposed at `GET /api/v1/map/layers/health`.

## Enablement

Configure an explicit DSN for each source database used by the deployment.
Map layers do not read Stats-side copies or derive source connections from
`STATS_POSTGRES_DSN`. Source DSNs use `AECS4U_SOURCE_<DATABASE>_DATABASE_URL`
or `<DATABASE>_POSTGRES_DSN`; cadastral map layers use
`CADASTRAL_POSTGRES_DSN`. `STATS_POSTGRES_ENABLE` controls Stats-backed
services separately.

Prepare cadastral map views over the dedicated database's
`public.<region>__fogli` and
`public.<region>__particelle` tables:

```bash
.venv/bin/python scripts/prepare_cadastral_map.py --dry-run
.venv/bin/python scripts/prepare_cadastral_map.py
```

The script validates WGS84 geometry and existing spatial indexes, builds
identity indexes concurrently, and publishes `spatial.cadastral_sheet` and
`spatial.cadastral_parcel` views without copying source rows. It also ensures
each regional parcel table has a nullable `has_visura` column and an
`is_auction_sale` boolean defaulting to false, and exposes both from
`spatial.cadastral_parcel`. The auction refresh reads structured
`modelview.modelview_registries.sheet` and `.parcel` values in `pvp_enriched`,
joins them through the asset and sale to resolve municipality/province, and
matches cadastral parcel identities. Explicit parcel lists are expanded; only
unambiguous matches are marked true, and old flags without a structured match
are cleared.
Populate or refresh the stored parcel flags after preparing the regional view:

```bash
.venv/bin/python scripts/refresh_cadastral_parcel_visura.py --dry-run
.venv/bin/python scripts/refresh_cadastral_parcel_visura.py
.venv/bin/python scripts/refresh_cadastral_parcel_auction.py --dry-run
.venv/bin/python scripts/refresh_cadastral_parcel_auction.py --apply
```

SISTER-unavailable batches are left unchanged; unmatched references are not
marked false. Rerun map preparation after adding a regional table, then
refresh its SISTER flags. The map requires `CADASTRAL_POSTGRES_DSN` (or
`AECS4U_SOURCE_CADASTRAL_DATABASE_URL`) and does not fall back to Stats.
The application role needs `SELECT` on these views and `USAGE` on their schema.
Health checks inspect the views' regional tables and geometry GiST indexes,
sum their estimated row counts, and report an approximate statistical extent.
This does not assert complete coverage. Tiles, GeoJSON, parcel selection,
reference search, adjacent parcels, and the Leaflet vector fallback use the
same prepared source. Identity IDs represent a national cadastral entity;
multiple imported polygon parts with the same reference share that ID.

> **Deprecated (2026-10-21): Zornade-sourced data.** `market-zones`
> (`zornade.zornade_zone_omi`), `surface-subsidence` (Zornade EGMS summary)
> and `solar-potential` (Zornade municipal aggregate) keep serving, but no new
> features should depend on them. Licence and attribution text for these
> sources is unchanged in `source_catalog.py`.

Each map layer connects to its owning database. Municipal, province, and
region boundaries and municipal metric maps use the `istat` database; OMI
zones use `zornade.zone_omi`; urban sections use `public.sezioni_urbane`;
postal areas use `stats_export.cap_subcomunali`; and census sections use
`census_sections.sections`. Flood, landslide, MPS04, and seismic-classification
tiles connect to their corresponding `hazards` relations. EGMS ground-movement
tiles connect to `egms.egms_subsidenza`. Configure direct database URLs with
`HAZARDS_POSTGRES_DSN` and `EGMS_POSTGRES_DSN` (or the matching
`AECS4U_SOURCE_<DATABASE>_DATABASE_URL`). If a source DSN is missing or its
database is unavailable, that layer has no map source; it does not fall back
to Stats or an FDW mirror.
ANBSC confiscated assets (`anbsc-confiscated-assets`) come from the `anbsc`
database and read only its export schema, `anbsc_v2_ref.v_municipality_map`
(one point per ISTAT municipality, keyed by `istat_code`, with asset counts and
the summed estimated value of the properties). Never read the internal
`anbsc_v2` tables: `anbsc_v2_ref` is the stable contract and the reader role
cannot reach anything else. The public ANBSC data has no asset locations, so
the layer is municipality-level only (coverage `partial`, `sparse`). Configure
`ANBSC_POSTGRES_DSN` (or `AECS4U_SOURCE_ANBSC_DATABASE_URL`); the connecting
role needs `anbsc_reader`. PostGIS lives in the `postgis` schema of that
database, so the connection uses `search_path = anbsc_v2_ref, postgis`. The
layer is optional: without the DSN it is listed but never blocks preflight.
Two colour modes are offered: number of assets and estimated value.

The solar layer joins `lr_map.v_solar_potential_comuni_map` to the ISTAT
municipality geometry in the `istat` database. These map reads use
source-owned database connections and do not require Stats FDW fallback
relations. The census layer reads `census_sections.sections` directly from
the census database. The parcel enrichment cache is provisioned by
`migration.canonical_views.ensure_serving_schema`; request handlers do not
perform DDL.

Before publishing regional cadastral stores, run the read-only source
preflight:

```bash
python -m migration.cadastral_preflight --root /data/catasto/ITALIA --strict
```

It verifies region/municipality parcel-and-sheet coverage and filesystem
headroom. A failed preflight must stop publication; it does not modify source
files or the database.

Before releasing the map application, run the live database gate:

```bash
uv run land-registry-map-preflight
```

This fails if the PostGIS source is disabled, a catalog relation or geometry
column is missing, a GiST index is missing, a geometry has the wrong source
CRS, or a layer is still marked partial.
For development work against the current known partial publication, use
`uv run land-registry-map-preflight --allow-partial`.

## Layer contract

The following layers are rendered through PostGIS MVT, with bounded GeoJSON
available for viewport fallback and popup inspection:

- administrative boundaries, cadastral sheets, cadastral parcels;
- cadastral urban sections, OMI market zones, postal zones, and ANNCSU civic-address points;
- flood and landslide areas, census sections, MPS04 and INGV seismic points;
- EGMS ground movement, solar potential by municipality, MEF/IRPEF municipal
  income, CORINE land cover, and MIT/SID maritime-domain concessions.

Dense point data is tile-first. The GeoJSON endpoint refuses overly broad
viewports and returns an empty collection with the required zoom level rather
than initiating an unsafe national scan.

ANNCSU points are read from the source-owned `anncsu` PostgreSQL database.
The map includes only regional address tables with EPSG:4258 point geometry
and a GiST index; currently available regions determine the layer's partial
coverage. Dense tiles cluster points below zoom 14. Feature popups show the
civic address, coordinate method, municipality code, source vintage, and ANNCSU
access ID. The source is published under CC BY 4.0.

The MEF/IRPEF layer joins `serving.irpef_comuni` to the current ISTAT
municipality map by normalized ISTAT municipality code. It uses the latest
common tax year and displays mean taxable income per taxpayer; the selectable
color modes also show taxpayer shares calculated from the published brackets
below €10,000 and above €120,000.
This is a municipal taxpayer measure, not a household-income measure. The
layer is optional and reports its year and data readiness in map health.

CORINE land cover is read from the source-owned `corine` PostgreSQL database.
Only regional `rXX__corine_clc2018` tables with EPSG:3035 geometry, required
attributes, and a GiST index are included. The map colors CORINE 2018 polygons
by their level-1 class and reports partial coverage when only some regional
stores are ready.

## Remaining spatial sources

Quality-of-life observations are available in the source table view, but the
configured `quality_of_life.v_quality_of_life_map` currently fails because its
province-boundary dependency `geo_admin.provinces` is missing. Keep the
province indicators in the existing parcel details until the source provides a
working geometry join; the map needs an explicit indicator and edition choice.

Relocation indicators in this deployment describe countries, so they do not
support a meaningful Italian municipality or parcel overlay. No canonical
Successioni relation or geographic key is currently published to this app; add
its map visualization after the source supplies a stable municipality or
parcel join.

Parcel GeoJSON uses a tighter viewport cap of 0.0005 square degrees because a
single dense city view can intersect tens of thousands of parcels. Map clients
send their current zoom so the response can recommend a suitable next level.

Embedded consumers receive the same catalog, health, MVT, and bounded GeoJSON
endpoints below their configured prefix. Their map uses MVT when
Leaflet.VectorGrid is present and automatically falls back to viewport-scoped
GeoJSON when it is not.

Non-spatial landing tables remain source/detail data. The map reads the local
foreign table `aecs4u-stats.agenziademanio.concessions`, mapped to
`agenziademanio.agenziademanio.v_concession_map_features`. The foreign server
targets the `agenziademanio` PostgreSQL database. This view reads the stored
EPSG:4326 geometries and GiST index on `public.concessions`, without parsing or
transforming source JSON during tile requests. Detail lookups filter by `row_id`;
the source also needs the btree index created by
`scripts/sql/agenziademanio-feature-row-index.sql` to avoid scanning the full
concession table. The view also exposes the administrative
label, source release, and geometry validation/repair flags used in map details.
Related detail foreign tables map to the database's `agenziademanio.v_*` views.
The `agenziademanio.concession_details` foreign table maps to
`agenziademanio.v_concession_details`, a one-row-per-feature detail view that
merges the original concession record and attributes with its snapshot,
resource, administrative assignment, document and Sicily-contract records,
institution targets, and regional source audits. One-to-many source rows are
aggregated as JSON arrays so a concession stays one detail record. The source-owned
`concession_parcel_links` crosswalk is exposed by the land-registry detail API.
Run `scripts/sql/agenziademanio-map-features.sql` in the source database to
prepare this view. The view includes polygon footprints and WGS84 point
records, excluding duplicate point imports in other coordinate systems.
Run `property-scraper/scripts/repair_agenziademanio_fdw.py` in the source
repository to create the source-side detail view and link its foreign table.
`aecs4u-stats/migration/agenziademanio_foreign_views.sql` defines the matching
Stats foreign table.
Run `scripts/update_agenziademanio_fdw.py` to create any missing foreign-table
definitions and refresh the mappings in place.

Concession-to-parcel matches are stored in the source-owned
`agenziademanio.agenziademanio.concession_parcel_links`, one row per concession
feature and parcel identity. `aecs4u-stats.agenziademanio.concession_parcel_links`
exposes that relation through `postgres_fdw`. This is a spatially calculated
crosswalk, not a source cadastral reference: polygon rows require positive-area
overlap, while point rows use `ST_Covers` (a point on a parcel boundary can
therefore match both adjacent parcels). It records the parcel ID/reference,
source releases, match method, and polygon overlap area in EPSG:3035 square
metres. Refresh it after either source changes by running
`.venv/bin/python scripts/refresh_agenziademanio_parcel_links.py --apply`.
The command reads concessions from `aecs4u-stats`, performs the spatial match
against `cadastral.spatial.cadastral_parcel` where the regional GiST indexes
are available, and atomically replaces the crosswalk in the `agenziademanio`
database. The refresh derives that connection from `STATS_POSTGRES_DSN` by
switching the database name, or uses `AGENZIADEMANIO_POSTGRES_DSN` when set.
Without `--apply`, it reports the calculated link counts as a dry
run. The cadastral parcel view must first be prepared with
`scripts/prepare_cadastral_map.py`. The schema and indexes are defined in
`scripts/sql/refresh-agenziademanio-parcel-links.sql`.
The selected-parcel sidebar reads this crosswalk by the parcel's canonical map
ID through `/api/v1/enrichment/parcel/agenziademanio/{parcel_id}`. The section
reports the concession ID/label, source snapshot and release, match method,
and polygon overlap area where applicable.

Concession tile queries apply the tile envelope directly to the foreign
geometry column so `postgres_fdw` can send the spatial predicate to the remote
GiST index. Mixed tiles cap output at 5,000 features and prioritize polygons;
point features use remaining capacity.
Points with the same concession ID and snapshot as a drawable polygon in the
tile are omitted so their markers cannot cover a small footprint. When a
polygon is too small for tile encoding, its available point record remains
visible. Concession tile URLs carry a revision to invalidate older browser
tiles after changes to the geometry selection.

The concessions layer labels polygon footprints with their concession ID from
zoom 15. Its legend separates polygon footprints from point records, and
**Zoom to polygons** fits the native polygons in the current viewport, up to
zoom 20. Native GeoJSON is used for this action so a very small footprint can
be found even if it was omitted during vector-tile quantization. Enabling the
layer leaves the camera in place; the user chooses when to zoom to its
footprints. The popup presents the concession ID, administrative label,
geometry and source metadata, snapshot date, and linked document/status records.
Some views contain only small footprints or point records; the control reports
when no polygon is published in the viewport.

No polygon geometry is generated from markers. A point record is suppressed
when its same-ID, same-snapshot polygon is drawable in the same tile; otherwise
the point remains available as a separate map feature.

## Auction sales

The auction map bulk load reads `pvp_enriched.modelview.v_map_sale_points`.
It validates each address once, ranks asset/lot/sale address links in batches,
and resolves municipality fallbacks once per distinct city/province. The view
reads live source tables and keeps the same coordinate selection as
`modelview.v_map_sales`, including approximate municipality points for missing
or mismatched coordinates. Individual marker details still query
`modelview.v_map_sales` by sale ID.

Run `scripts/sql/pvp-enriched-modelview-map-view.sql` for initial installation;
it includes `pvp-enriched-modelview-map-points.sql`. Existing installations can
run the points script directly. It creates covering indexes concurrently and
exposes the compact view as `aecs4u-stats.pvp.v_map_sale_points`, with FDW batch
size 10,000. The loader falls back to the full sales view when the compact
view has not been installed.

The map loader permits 64 MB of `work_mem` per sort/hash operation only within
its bulk-load transaction. It keeps the hourly in-memory snapshot and shares
an in-progress load between concurrent refresh requests. Source changes are
visible on the next snapshot refresh without rebuilding a database cache.

## Release gate

Release only when every required catalog item reports `relation_exists`,
`geometry_column_exists`, and `srid_matches` as true, and every layer that
requires a local spatial index reports `gist_index_exists`. Smoke tests should
cover both an MVT tile and a GeoJSON viewport. Cadastral nationwide coverage
and the upstream urban-section count remain data-acquisition requirements;
optional data sources such as ANNCSU do not block release when their regional
stores are absent. The map reports available canonical rows but does not claim
national parity until those sources are loaded. The Cloud Run workflow separately
verifies that the deployed service can reach the advertised catalog layers after
rollout.
