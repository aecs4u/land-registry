# Canonical database map layers

The map consumes an allow-listed catalog of canonical aecs4u-stats PostGIS
relations. The catalog is exposed at `GET /api/v1/map/layers`; deployment
readiness is exposed at `GET /api/v1/map/layers/health`.

## Enablement

Apply the aecs4u-stats serving migrations, then configure the map process with:

```dotenv
AECS4U_STATS_POSTGRES_DSN=postgresql://…/aecs4u-stats
AECS4U_STATS_POSTGRES_ENABLE=1
```

The application role only needs `SELECT` on the canonical and serving
relations. The parcel enrichment cache is provisioned by
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
- cadastral urban sections, OMI market zones, postal zones;
- hazard areas, census sections, points of interest;
- hazard measurements, raster coverage footprints, MPS04 points;
- municipality profiles, market-zone snapshots, and MIT/SID maritime-domain
  concessions.

Dense point data is tile-first. The GeoJSON endpoint refuses overly broad
viewports and returns an empty collection with the required zoom level rather
than initiating an unsafe national scan.

Embedded consumers receive the same catalog, health, MVT, and bounded GeoJSON
endpoints below their configured prefix. Their map uses MVT when
Leaflet.VectorGrid is present and automatically falls back to viewport-scoped
GeoJSON when it is not.

Non-spatial landing tables remain source/detail data. The maritime concessions
landing table is an explicit exception: its derived `geom` field is normalized
to WGS84, indexed, and exposed as a mixed point/polygon layer while the source
`geometry_json` and `crs_original` columns remain available for audit.

## Release gate

Release only when every catalog item reports `relation_exists`,
`geometry_column_exists`, and `gist_index_exists` as true, and smoke tests
cover both an MVT tile and a GeoJSON viewport. Cadastral nationwide coverage
and the upstream urban-section count remain data-acquisition requirements;
the map reports available canonical rows but does not claim national parity
until those sources are loaded. The Cloud Run workflow separately verifies
that the deployed service can reach all 15 catalog layers after rollout.
