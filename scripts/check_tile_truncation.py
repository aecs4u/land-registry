"""Report which map tiles still exceed their layer's per-tile feature cap.

For every catalog layer, count the source rows that survive a tile's spatial filter and the
layer's visibility filter (features under about one pixel are dropped, see ``size_column``)
and compare with ``max_features``. Tiles over the cap are truncated to the largest features
and carry a ``_truncated`` marker layer. Layers that cluster at low zoom are skipped there.

Needs the map databases (``.env``); it only runs SELECT count(*) queries::

    python scripts/check_tile_truncation.py                 # every layer
    python scripts/check_tile_truncation.py landslide-hazard census-sections

Re-run it after changing a layer's cap, ``min_zoom`` or ``size_column``, or after loading
much denser data.
"""

from __future__ import annotations

import asyncio
import math
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

from land_registry.map_layers import MAP_LAYERS, _asyncpg_sql, _one_pixel_area_m2, get_map_layer_source  # noqa: E402

# Dense places across the country; add more to widen the check.
PLACES = {
    "Rome": (41.9, 12.5), "Naples": (40.85, 14.27), "Milan": (45.46, 9.19), "Florence": (43.77, 11.25),
    "Palermo": (38.12, 13.36), "Venice": (45.44, 12.33), "Turin": (45.07, 7.69), "Bologna": (44.49, 11.34),
    "Ferrara": (44.84, 11.62), "Genoa": (44.41, 8.93), "Cagliari": (39.22, 9.12), "Bari": (41.12, 16.87),
    "L'Aquila": (42.35, 13.4), "Catania": (37.5, 15.09), "Trieste": (45.65, 13.78),
}
MAX_ZOOM = 14


def tile(lat: float, lng: float, z: int) -> tuple[int, int]:
    n = 2 ** z
    return int((lng + 180) / 360 * n), int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)


async def check(only: set[str]) -> int:
    source = get_map_layer_source()
    gate = asyncio.Semaphore(3)
    problems = 0
    for layer in MAP_LAYERS:
        if only and layer.id not in only:
            continue
        try:
            layer_source = await source._layer_source_for(layer)
            if layer_source is None:
                print(f"{layer.id:24} no source configured")
                continue
            relation = source._query_relation(layer, await source._resolve_relation_for(layer, layer_source))
        except Exception as exc:  # noqa: BLE001 - report and carry on with the other layers
            print(f"{layer.id:24} setup failed: {exc!s:.70}")
            continue
        geometry = source._column_ref("t", layer.geometry_column)

        async def count(z: int, name: str, lat: float, lng: float):
            x, y = tile(lat, lng, z)
            width = (360.0 if layer.source_srid == 4326 else 40075016.686) / (1 << z)
            padding = 0.0 if layer.kind == "point" else width * 64 / 4096
            extra, params = "", []
            if layer.id == "geo-boundaries":
                level = next((u for m, u in reversed(layer.unit_levels) if z >= m), layer.unit_levels[0][1])
                extra, params = " AND t.unit_type = %s", [level]
            if layer.size_column:
                size = source._column_ref("t", layer.size_column)
                extra += f" AND ({size} IS NULL OR {size} >= {_one_pixel_area_m2(z, y)!r})"
            sql = (
                f"SELECT count(*) FROM {relation} AS t {source._source_join(layer)} WHERE {geometry} && "
                f"ST_Expand(ST_Transform(ST_TileEnvelope({z},{x},{y}), {layer.source_srid}), {padding!r}){extra}"
            )
            async with gate:
                try:
                    async with layer_source.connection() as connection:
                        await connection.execute("SET statement_timeout = 60000")
                        return z, name, await connection.fetchval(_asyncpg_sql(sql), *params)
                except Exception as exc:  # noqa: BLE001
                    return z, name, f"error: {str(exc)[:40]}"

        jobs = [
            count(z, name, lat, lng)
            for z in range(layer.min_zoom, MAX_ZOOM + 1)
            if not (layer.kind == "mixed" and z < layer.cluster_below_zoom)
            for name, (lat, lng) in PLACES.items()
        ]
        results = await asyncio.gather(*jobs)
        over = [(z, name, n) for z, name, n in results if isinstance(n, int) and n > layer.max_features]
        errors = [r for r in results if not isinstance(r[2], int)]
        problems += len(over)
        worst = max(over, key=lambda r: r[2]) if over else None
        print(
            f"{layer.id:24} cap={layer.max_features:<6} over cap: {len(over):3}/{len(results):<4}"
            + (f" zooms {sorted({z for z, _, _ in over})}; worst {worst[1]} z{worst[0]} has {worst[2]} rows" if worst else "")
            + (f"  [{len(errors)} errors]" if errors else "")
        )
    return problems


if __name__ == "__main__":
    over = asyncio.run(check(set(sys.argv[1:])))
    print(f"\n{over} tile(s) over their cap (these keep the largest features and carry the _truncated marker)")
