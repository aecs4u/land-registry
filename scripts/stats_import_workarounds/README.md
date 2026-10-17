# Regional store imports with `aecs4u-stats` 0.2.1 workarounds

These wrappers reproduce the Sicilia (ISTAT region 19) pilot imports of
2026-10-16 against `aecs4u-stats` `main` (`1109d6c`/`a1ef4cb`). They patch the
installed package **in memory only** and should be deleted once the upstream
defects below are fixed. They are filed upstream as `aecs4u-stats` #64 (ANNCSU
URL), #65 and #66 (terrain), #67 and #68 (footprints). Run them from the repository root with the project
venv.

| Dataset | Command | Upstream defect worked around |
|---|---|---|
| ISTAT boundaries (needed first) | `python -m aecs4u_stats.istat.scripts.import_boundaries --year 2026 --full-detail` | none |
| CORINE | `python -m aecs4u_stats.corine.scripts.import_corine --region 19` | none |
| TINITALY terrain | `python scripts/stats_import_workarounds/run_terrain.py --region 19` | `terrain/importer.py` uses `TINITALY_GRID_ORIGIN` without importing it (`NameError`); no retry, so one HTTP 500 from the INGV WCS aborts the ~110-chunk import |
| OSM footprints | `PYTHONPATH=<dir with pyrosm> python scripts/stats_import_workarounds/run_buildings.py --cache <parquet> --region 19` | `[buildings]` extra not installed (needs `pyrosm`, `python-rapidjson`); pyrosm 0.21 rejects the tuple bounding box; the `_provenance` insert has 11 placeholders for 10 columns, so DuckDB rolls the whole import back at the end |
| ANNCSU addresses | download both ZIPs below, then `python -m aecs4u_stats.anncsu.scripts.import_addresses --region 19 --indirizzario-zip … --stradario-zip …` | `anncsu_download_url()` appends `=`, which the official endpoint answers with 404 |

ANNCSU downloads (the endpoint rejects the default curl agent, so send a
browser `User-Agent`; no trailing `=`):

```sh
U=https://anncsu.open.agenziaentrate.gov.it/age-inspire/opendata/anncsu/getds.php
curl -A "Mozilla/5.0" -o indirizzarioSicilia20260915.zip "$U?INDIR_SICI"
curl -A "Mozilla/5.0" -o stradarioSicilia20260915.zip   "$U?STRAD_SICI"
```

Install `pyrosm` outside the project venv with
`uv pip install --python .venv/bin/python --target <dir> --no-deps pyrosm python-rapidjson`.

Stores land under `/data/istat/{terrain,corine,buildings,anncsu}`. Sizes for
Sicilia: terrain 483 MB, CORINE 20 MB, footprints 197 MB (plus a 206 MB PBF),
ANNCSU 486 MB, boundaries 126 MB.
