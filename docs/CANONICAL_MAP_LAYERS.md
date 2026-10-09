# Canonical database map layers

The map consumes an allow-listed catalog of canonical aecs4u-stats PostGIS
relations. The catalog is exposed at `GET /api/v1/map/layers`; deployment
readiness is exposed at `GET /api/v1/map/layers/health`.

## Enablement

Apply the aecs4u-stats serving migrations, then configure the map process with:

```dotenv
STATS_POSTGRES_DSN=postgresql://…/aecs4u-stats
STATS_POSTGRES_ENABLE=1
```

The application role needs `SELECT` on the canonical and serving relations.
When cadastral sheets and parcels have moved to the dedicated `cadastral`
database, prepare map views over its `public.<region>__fogli` and
`public.<region>__particelle` tables:

```bash
.venv/bin/python scripts/prepare_cadastral_map.py --dry-run
.venv/bin/python scripts/prepare_cadastral_map.py
```

The script validates WGS84 geometry and existing spatial indexes, builds
identity indexes concurrently, and publishes `spatial.cadastral_sheet` and
`spatial.cadastral_parcel` views without copying or changing source rows.
Rerun it after adding a regional table. The app prefers existing canonical
relations in aecs4u-stats and uses these views when those relations are absent.
`CADASTRAL_POSTGRES_DSN` overrides the dedicated connection; by default it
uses the stats connection's host and credentials with database `cadastral`.
The application role needs `SELECT` on these views and `USAGE` on their schema.
Health checks inspect the views' regional tables and geometry GiST indexes,
sum their estimated row counts, and report an approximate statistical extent.
This does not assert complete coverage. Tiles, GeoJSON, parcel selection,
reference search, adjacent parcels, and the Leaflet vector fallback use the
same prepared source. Identity IDs represent a national cadastral entity;
multiple imported polygon parts with the same reference share that ID.

Map layers use the consolidated `aecs4u-stats` relations directly: municipal,
province, and region boundaries from `istat.v_*_map`; OMI zones from
`zornade.zornade_zone_omi`; urban sections from `sezioni_urbane.sezioni_urbane`;
postal areas from the `cap_subcomunali.cap_subcomunali` view over
`geo_postal.cap_subcomunali`; and EGMS data from `geo_surface_change`.
Flood, landslide, MPS04, and seismic-classification tiles connect directly to
the corresponding `hazards.public` relations so their local GiST indexes serve
tile requests. EGMS ground-movement tiles connect directly to
`egms.public.egms_subsidenza` for the same reason. Configure
`EGMS_POSTGRES_DSN` or `AECS4U_STATS_EGMS_DATABASE_URL` to override the
default connection, which derives the `egms` database URL from
`STATS_POSTGRES_DSN`. Configure `HAZARDS_POSTGRES_DSN` or
`AECS4U_STATS_HAZARDS_DATABASE_URL`; when neither is set, the app derives a
`hazards` database URL from `STATS_POSTGRES_DSN`.
The solar layer joins `solar.solar_potential_comuni` to the canonical ISTAT
municipality map at query time because the solar relation contains metrics but
does not duplicate boundary geometry. These foreign-table sources have no
local GiST index, so their foreign servers must push down spatial predicates
for efficient tile reads. If either direct database is unavailable, map reads
fall back to the stats foreign tables; apply
`scripts/sql/map-hazards-fdw-pushdown.sql` on `aecs4u-stats` to enable PostGIS
bbox pushdown for hazards fallback. The serving role must own `hazards_source`
or run the script as a superuser. EGMS fallback requires `postgis` in the
`egms_source` foreign server's `extensions` option for bbox pushdown.
The census layer reads the stats database's `census_sections.sections` foreign
table, mapped to `public.sections` in the `census_sections` database. Its
foreign server must list `postgis` in its `extensions` option on both sides;
otherwise PostGIS bbox filters stay local and every source geometry crosses
the FDW connection for each tile. Apply
`scripts/sql/map-census-sections-fdw-pushdown.sql` on aecs4u-stats to enable
that pushdown while preserving other listed extensions. The parcel enrichment
cache is provisioned by `migration.canonical_views.ensure_serving_schema`;
request handlers do not perform DDL.

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
- cadastral urban sections, OMI market zones, and postal zones;
- flood and landslide areas, census sections, MPS04 and INGV seismic points;
- EGMS ground movement, solar potential by municipality, and MIT/SID
  maritime-domain concessions.

Dense point data is tile-first. The GeoJSON endpoint refuses overly broad
viewports and returns an empty collection with the required zoom level rather
than initiating an unsafe national scan.

Embedded consumers receive the same catalog, health, MVT, and bounded GeoJSON
endpoints below their configured prefix. Their map uses MVT when
Leaflet.VectorGrid is present and automatically falls back to viewport-scoped
GeoJSON when it is not.

Non-spatial landing tables remain source/detail data. The map reads the local
foreign table `aecs4u-stats.agenziademanio.concessions`, mapped to
`agenziademanio.agenziademanio.v_concession_map_features`. The foreign server
targets the `agenziademanio` PostgreSQL database. This view reads the stored
EPSG:4326 geometries and GiST index on `public.concessions`, without parsing or
transforming source JSON during tile requests. It also exposes the administrative
label, source release, and geometry validation/repair flags used in map details.
Related detail foreign tables map to the database's `agenziademanio.v_*` views.
Run `scripts/sql/agenziademanio-map-features.sql` in the source database to
prepare this view. The view includes polygon footprints and WGS84 point
records, excluding duplicate point imports in other coordinate systems.
Run `scripts/update_agenziademanio_fdw.py` to create any missing foreign-table
definitions and refresh the mappings in place.

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

Release only when every catalog item reports `relation_exists`,
`geometry_column_exists`, and `srid_matches` as true, and every layer that
requires a local spatial index reports `gist_index_exists`. Smoke tests should
cover both an MVT tile and a GeoJSON viewport. Cadastral nationwide coverage
and the upstream urban-section count remain data-acquisition requirements;
the map reports available canonical rows but does not claim national parity
until those sources are loaded. The Cloud Run workflow separately verifies
that the deployed service can reach all 14 catalog layers after rollout.
