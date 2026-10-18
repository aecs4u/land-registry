"""Rebuild ``hazards.public.flood_hazard`` from the ISPRA 2020 flood mosaic.

The table previously held polygons from a wrong EPSG:3035 -> EPSG:4326 reprojection
(neither correct nor a plain axis swap), so every flood tile came back empty. This
reads the original shapefiles, reprojects with pyproj (``always_xy``), computes
``area_sqm`` in the equal-area source CRS and loads a staging table, which it checks
before anything replaces the live one::

    python scripts/rebuild_flood_hazard.py --limit 200      # dry run into a temp table
    python scripts/rebuild_flood_hazard.py                  # build + verify flood_hazard_new
    python scripts/rebuild_flood_hazard.py --swap           # build, verify and rename it into place

The live table is renamed ``flood_hazard_old_badcrs`` (not dropped) on ``--swap``.
Loopback connections use ``sslmode=disable``: psycopg2-binary's bundled TLS has
crashed the worker on this host.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np
import psycopg2
import pyogrio
import pyproj
import shapely
from psycopg2.extras import execute_values

ZIP = "/vsizip//mnt/mobile/data/aecs4u.it/property-scraper/ispra/mosaics/flood/mosaic.zip/"
LAYERS = (
    ("P1", "LPH_Mosaicatura_ISPRA_2020_pericolosita_idraulica_bassa"),
    ("P2", "MPH_Mosaicatura_ISPRA_2020_pericolosita_idraulica_media"),
    ("P3", "HPH_Mosaicatura_ISPRA_2020_pericolosita_idraulica_elevata"),
)
EXPECTED = {"P1": 27176, "P2": 29493, "P3": 28791}
# Generous box around Italy and its islands: (min_lon, min_lat, max_lon, max_lat).
ITALY = (6.0, 35.0, 19.0, 47.5)
CHUNK = 500


def _transformer() -> pyproj.Transformer:
    return pyproj.Transformer.from_crs(3035, 4326, always_xy=True)


def _reproject(geometries, transformer):
    def apply(coords: np.ndarray) -> np.ndarray:
        x, y = transformer.transform(coords[:, 0], coords[:, 1])
        return np.column_stack((x, y))

    return [shapely.transform(g, apply) for g in geometries]


def load_layer(cursor, table: str, code: str, name: str, limit: int | None, transformer) -> int:
    path = ZIP + name + ".shp"
    total = pyogrio.read_info(path)["features"]
    wanted = min(total, limit) if limit else total
    if not limit and total != EXPECTED[code]:
        raise SystemExit(f"{code}: expected {EXPECTED[code]} features, shapefile has {total}")
    loaded = 0
    started = time.monotonic()
    while loaded < wanted:
        frame = pyogrio.read_dataframe(
            path, skip_features=loaded, max_features=min(CHUNK, wanted - loaded)
        )
        if frame.empty:
            break
        areas = frame.geometry.area.to_numpy()  # LAEA is equal-area, metres squared
        wkb = shapely.to_wkb(np.array(_reproject(frame.geometry.to_numpy(), transformer), dtype=object))
        rows = [
            (code, label, float(area), psycopg2.Binary(geometry))
            for label, area, geometry in zip(frame["scenario"], areas, wkb)
        ]
        execute_values(
            cursor,
            f"INSERT INTO {table} (scenario_code, scenario_label, area_sqm, geom) VALUES %s",
            rows,
            template="(%s, %s, %s, ST_SetSRID(ST_GeomFromWKB(%s), 4326))",
        )
        loaded += len(frame)
        print(f"{code}: {loaded}/{wanted} ({time.monotonic() - started:.0f}s)", flush=True)
    return loaded


def verify(cursor, table: str, limit: int | None) -> None:
    cursor.execute(
        f"""SELECT count(*), min(ST_XMin(geom)), min(ST_YMin(geom)), max(ST_XMax(geom)), max(ST_YMax(geom)),
                   count(*) FILTER (WHERE ST_XMin(geom) < %s OR ST_YMin(geom) < %s
                                       OR ST_XMax(geom) > %s OR ST_YMax(geom) > %s),
                   count(*) FILTER (WHERE geom IS NULL OR ST_IsEmpty(geom))
            FROM {table}""",
        ITALY,
    )
    total, west, south, east, north, outside, empty = cursor.fetchone()
    print(f"extent lon {west:.3f}..{east:.3f} lat {south:.3f}..{north:.3f}; "
          f"{outside} rows outside Italy, {empty} empty, {total} total")
    if outside or empty:
        raise SystemExit("verification failed: rows outside Italy or empty geometries")
    if not limit and total != sum(EXPECTED.values()):
        raise SystemExit(f"verification failed: {total} rows, expected {sum(EXPECTED.values())}")
    # Planar LAEA area should match a spheroidal area of the reprojected polygon closely.
    cursor.execute(
        f"""WITH s AS (SELECT area_sqm, geom FROM {table} WHERE area_sqm > 1000 ORDER BY random() LIMIT 2000)
            SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY ST_Area(geom::geography) / area_sqm),
                   percentile_cont(0.05) WITHIN GROUP (ORDER BY ST_Area(geom::geography) / area_sqm),
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY ST_Area(geom::geography) / area_sqm) FROM s"""
    )
    median, low, high = cursor.fetchone()
    print(f"geography/LAEA area ratio: median {median:.4f} (p5 {low:.4f}, p95 {high:.4f})")
    if not 0.98 <= median <= 1.02:
        raise SystemExit("verification failed: reprojected areas do not match source areas")
    # Known place: the first HPH polygon sits near Marsala, Sicily (about 12.63E, 37.64N).
    cursor.execute(
        f"""SELECT count(*) FROM {table}
            WHERE geom && ST_MakeEnvelope(12.6, 37.6, 12.65, 37.66, 4326)"""
    )
    print(f"polygons near Marsala (12.6-12.65E, 37.6-37.66N): {cursor.fetchone()[0]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dsn", default=f"host=127.0.0.1 dbname=hazards user=postgres sslmode=disable "
                                         f"passfile={os.path.expanduser('~/.pgpass')}")
    parser.add_argument("--limit", type=int, help="dry run: load only this many features per scenario")
    parser.add_argument("--swap", action="store_true", help="rename the verified staging table into place")
    args = parser.parse_args()

    table = "public.flood_hazard_dryrun" if args.limit else "public.flood_hazard_new"
    transformer = _transformer()
    connection = psycopg2.connect(args.dsn)
    connection.autocommit = True
    cursor = connection.cursor()
    cursor.execute(f"DROP TABLE IF EXISTS {table}")
    cursor.execute(
        f"CREATE TABLE {table} (scenario_code text, scenario_label text, area_sqm double precision, "
        "geom geometry(Geometry, 4326))"
    )
    for code, name in LAYERS:
        load_layer(cursor, table, code, name, args.limit, transformer)
    verify(cursor, table, args.limit)
    if args.limit:
        cursor.execute(f"DROP TABLE {table}")
        print("dry run passed; temp table dropped")
        return
    print("creating GiST index", flush=True)
    cursor.execute(f"CREATE INDEX flood_hazard_new_geom_gist ON {table} USING gist (geom)")
    cursor.execute(f"VACUUM (ANALYZE) {table}")
    if not args.swap:
        print("staged and verified in public.flood_hazard_new; the live table is untouched")
        return
    cursor.execute("BEGIN")
    cursor.execute("ALTER TABLE public.flood_hazard RENAME TO flood_hazard_old_badcrs")
    cursor.execute("ALTER INDEX public.flood_hazard_geom_gist RENAME TO flood_hazard_old_badcrs_geom_gist")
    cursor.execute("ALTER TABLE public.flood_hazard_new RENAME TO flood_hazard")
    cursor.execute("ALTER INDEX public.flood_hazard_new_geom_gist RENAME TO flood_hazard_geom_gist")
    cursor.execute("COMMIT")
    print("swapped: public.flood_hazard is now the corrected table; old one kept as flood_hazard_old_badcrs")


if __name__ == "__main__":
    sys.exit(main())
