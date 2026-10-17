## Summary

Importing Sicilia (ISTAT region 19) exposed five defects in the regional
importers. The fixes are present in the installed `aecs4u-stats` `main`
revision `b71a743`, which the root `uv.lock` now pins. Runtime checks confirm
the corrected ANNCSU URL, terrain grid-origin import and retry path, building
provenance placeholder count, and pyrosm bounding-box handling. Focused
regression tests pass. The scripts in `scripts/stats_import_workarounds/` are
retained as historical reproduction aids; new imports use the installed
package. This issue keeps the original reproduction notes below.

## 1. `anncsu`: download URL ends in `=` and returns 404

`anncsu_download_url()` builds `…/getds.php?INDIR_SICI=`. The official endpoint
answers that with 404; `…/getds.php?INDIR_SICI` (no `=`) returns the ZIP
(`indirizzarioSicilia20260915.zip`, 37 MB; stradario 1.4 MB). The endpoint also
sits behind Akamai and answers the default curl agent with 403, so send a browser
`User-Agent`.

**Fix:** drop the trailing `=` in `aecs4u_stats/anncsu/config.py`
(`anncsu_download_url`) and set a `User-Agent` on the download client.
**Test:** assert the URL for a region equals the link on the official download
page.

## 2. `terrain`: `NameError: TINITALY_GRID_ORIGIN`

`terrain/importer.py` uses `TINITALY_GRID_ORIGIN` (line ~279) but only
`terrain.config` defines it. `ruff check --select F821` reports it. Every
`import_terrain` run fails before the first download.

**Fix:** import it from `aecs4u_stats.terrain.config`. Add `ruff F821` to CI so
undefined names cannot ship.

## 3. `terrain`: no retry for the INGV WCS

The WCS returns intermittent HTTP 500s (about one request in ten during the
Sicilia run, clustered around Etna). `import_terrain_region` raises on the first
one and discards the staged store, so a ~110-chunk import cannot finish without
a retry.

**Fix:** retry `_download_coverage` on 5xx and transport errors with exponential
backoff (six attempts, 3 s to 60 s worked: 109 chunks stored, exit 0).

## 4. `buildings`: `_provenance` insert has 11 placeholders for 10 columns

`INSERT INTO _provenance VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)` supplies ten
values for a ten-column table, so DuckDB raises `InvalidInputException` after all
1.27 million rows are written and the whole import rolls back (about 20 minutes
lost per attempt).

**Fix:** use ten placeholders. **Test:** import a tiny PBF fixture end to end so
the provenance statement runs.

## 5. `buildings`: `pyrosm` handling

- `[buildings]` pulls `pyrosm`, but `from pyrosm import OSM` also needs
  `python-rapidjson`, which pyrosm 0.21 does not declare; the importer reports
  only "install aecs4u-stats[buildings]".
- `_read_buildings` passes `bounding_box` as a tuple. pyrosm 0.21 accepts only a
  list, a Shapely polygon or a ring (`ValueError: bounding_box should be a
  list…`).

**Fix:** pass `list(bbox)`, add `python-rapidjson` to the extra, and surface the
underlying `ImportError` instead of the generic message.

## Verification after the fixes

The regional map databases are now served from PostgreSQL: ANNCSU R19 has
3,165,306 address rows (2,432,270 geocoded), CORINE R19 has 7,181 polygons and
OSM buildings R19 has 1,273,472 footprints. CORINE/building geometry is EPSG:3035;
ANNCSU access points use EPSG:4258. The SQLite/DuckDB files are retained as
import artifacts, while the app's default query readers use the source-owned
PostgreSQL databases. These counts are exact PostgreSQL table/provenance
counts, not estimates.

Sicilia stores built with the workarounds returned, for parcel
`G273_000900.3205`: elevation 20 m; CORINE 112 at 100% of the parcel; 3 OSM
footprints, 122.6 m², 9.56% coverage; no geocoded ANNCSU point; EGMS
−0.80 mm/y. Zornade shows the same elevation, class, building count and
coverage for the parcel.
