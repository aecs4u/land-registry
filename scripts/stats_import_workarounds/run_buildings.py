"""Run the OSM footprint importer with two local workarounds (installed package untouched).

1. pyrosm 0.21 rejects the tuple bounding box aecs4u-stats passes; it is
   converted to a list. The parsed frame is cached so a retry skips the parse.
2. aecs4u-stats main (1109d6c/a1ef4cb) inserts 11 placeholders into the
   10-column ``_provenance`` table, which makes DuckDB roll back the whole
   import after the rows are written. The SQL string is corrected in memory.
"""
import pathlib, runpy, sys
import aecs4u_stats.buildings.importer as importer

source = pathlib.Path(importer.__file__).read_text(encoding="utf-8")
bad = "INSERT INTO _provenance VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
good = "INSERT INTO _provenance VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
assert source.count(bad) == 1, "importer changed upstream; re-check the workaround"
exec(compile(source.replace(bad, good), importer.__file__, "exec"), importer.__dict__)

CACHE = pathlib.Path(sys.argv[sys.argv.index("--cache") + 1]) if "--cache" in sys.argv else None
if CACHE:
    i = sys.argv.index("--cache"); del sys.argv[i:i + 2]

def _read_buildings(pbf_path, bbox):
    import geopandas as gpd
    if CACHE and CACHE.exists():
        return gpd.read_parquet(CACHE)
    from pyrosm import OSM
    frame = OSM(str(pbf_path), bounding_box=list(bbox)).get_buildings()
    if CACHE:
        frame.to_parquet(CACHE)
    return frame

importer._read_buildings = _read_buildings
sys.argv = ["import_buildings", *sys.argv[1:]]
runpy.run_module("aecs4u_stats.buildings.scripts.import_buildings", run_name="__main__", alter_sys=True)
