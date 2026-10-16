#!/usr/bin/env python
"""Refresh the local ISPRA 2006–2020 coastal-change snapshot.

Provision ``spatial.ispra_coastal_dynamics`` with
``scripts/sql/ispra-coastal-dynamics.sql`` first. A default run validates a
complete source read without changing PostgreSQL; pass ``--apply`` to replace
the existing snapshot atomically.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Iterable
import json
import os
from pathlib import Path
import sys
from typing import Any

import asyncpg
import httpx
from shapely.geometry import LineString, MultiLineString

ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = (
    "https://sinacloud.isprambiente.it/arcgisadv/rest/services/coste/"
    "Assetto_costiero_anno_2020/FeatureServer/1/query"
)
SOURCE_RELEASE = "ISPRA_LINEA_COSTA_2020_V2_DYNAMICA_2006_2020"
BATCH_SIZE = 400
OUT_FIELDS = (
    "objectid,objectid_1,cod_istat,modifica_2006_2020,tipo_costa,lung_modif,"
    "variaz_med,variaz_max,indice_sev"
)

CREATE_STAGE_SQL = """
CREATE TEMP TABLE _ispra_coastal_dynamics_stage (
    object_id bigint PRIMARY KEY,
    cod_istat integer,
    change_class text,
    coast_type integer,
    shoreline_length_m double precision,
    mean_variation_m double precision,
    max_variation_m double precision,
    severity_index integer,
    raw_attributes jsonb NOT NULL,
    geom_wkb bytea NOT NULL
) ON COMMIT PRESERVE ROWS
"""

STAGE_TO_FINAL_SQL = f"""
INSERT INTO spatial.ispra_coastal_dynamics (
    object_id, cod_istat, change_class, coast_type, shoreline_length_m,
    mean_variation_m, max_variation_m, severity_index, raw_attributes,
    geom, geom_metric, source_release, imported_at
)
SELECT object_id, cod_istat, change_class, coast_type, shoreline_length_m,
       mean_variation_m, max_variation_m, severity_index, raw_attributes,
       ST_Multi(ST_SetSRID(ST_GeomFromWKB(geom_wkb), 4326))::geometry(MultiLineString, 4326),
       ST_Transform(
           ST_Multi(ST_SetSRID(ST_GeomFromWKB(geom_wkb), 4326)), 6933
       )::geometry(MultiLineString, 6933),
       '{SOURCE_RELEASE}', CURRENT_TIMESTAMP
FROM _ispra_coastal_dynamics_stage
"""


def _database_dsn() -> str:
    from dotenv import dotenv_values

    values = {**dotenv_values(ROOT / ".env"), **os.environ}
    dsn = next(
        (
            str(values[name]).strip()
            for name in (
                "STATS_POSTGRES_DSN",
                "AECS4U_STATS_POSTGRES_DSN",
                "AECS4U_STATS_DATABASE_URL",
                "AECS4U_STATS_SPATIAL_DATABASE_URL",
            )
            if values.get(name)
        ),
        "",
    )
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    if not dsn.startswith(("postgres://", "postgresql://")):
        raise RuntimeError(
            "Set STATS_POSTGRES_DSN or AECS4U_STATS_DATABASE_URL to the aecs4u-stats database"
        )
    return dsn


def _chunks(values: list[int], size: int) -> Iterable[list[int]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


async def _post_json(client: httpx.AsyncClient, params: dict[str, Any]) -> dict[str, Any]:
    response = await client.post(ENDPOINT, data=params)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("error"):
        raise RuntimeError(f"ISPRA ArcGIS query failed: {payload.get('error') if isinstance(payload, dict) else 'invalid JSON'}")
    return payload


async def _read_object_ids(client: httpx.AsyncClient) -> list[int]:
    payload = await _post_json(
        client,
        {"where": "1=1", "returnIdsOnly": "true", "f": "json"},
    )
    object_ids = payload.get("objectIds")
    if not isinstance(object_ids, list):
        raise RuntimeError("ISPRA response did not contain an objectIds list")
    return sorted({int(value) for value in object_ids})


def _shape_from_arcgis_geometry(value: Any):
    if not isinstance(value, dict) or not isinstance(value.get("paths"), list):
        return None
    paths = value["paths"]
    if not paths or any(not isinstance(path, list) or len(path) < 2 for path in paths):
        return None
    try:
        line = LineString(paths[0]) if len(paths) == 1 else MultiLineString(paths)
        if line.is_empty or not line.is_valid:
            return None
        return line
    except Exception:
        return None


async def _read_feature_batch(
    client: httpx.AsyncClient, object_ids: list[int]
) -> list[tuple[Any, ...]]:
    payload = await _post_json(
        client,
        {
            "objectIds": ",".join(str(value) for value in object_ids),
            "outFields": OUT_FIELDS,
            "returnGeometry": "true",
            "outSR": "4326",
            "f": "json",
        },
    )
    features = payload.get("features")
    if not isinstance(features, list):
        raise RuntimeError("ISPRA response did not contain a feature list")
    rows = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        attributes = feature.get("attributes") or {}
        try:
            object_id_value = attributes.get("objectid_1")
            if object_id_value is None:
                object_id_value = attributes.get("objectid")
            object_id = int(object_id_value)
        except (TypeError, ValueError):
            continue
        line = _shape_from_arcgis_geometry(feature.get("geometry"))
        if line is None:
            continue

        def optional_int(name: str) -> int | None:
            try:
                return int(attributes[name]) if attributes.get(name) is not None else None
            except (TypeError, ValueError):
                return None

        def optional_float(name: str) -> float | None:
            try:
                return float(attributes[name]) if attributes.get(name) is not None else None
            except (TypeError, ValueError):
                return None

        rows.append((
            object_id,
            optional_int("cod_istat"),
            attributes.get("modifica_2006_2020"),
            optional_int("tipo_costa"),
            optional_float("lung_modif"),
            optional_float("variaz_med"),
            optional_float("variaz_max"),
            optional_int("indice_sev"),
            json.dumps(attributes, ensure_ascii=False, separators=(",", ":")),
            line.wkb,
        ))
    return rows


async def refresh(*, apply: bool, max_features: int | None) -> tuple[int, int]:
    timeout = httpx.Timeout(45.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        object_ids = await _read_object_ids(client)
        if max_features is not None:
            object_ids = object_ids[:max_features]
        print(f"ISPRA object IDs selected: {len(object_ids)}")
        if not object_ids:
            raise RuntimeError("ISPRA returned no features; refusing to replace the local snapshot")

        pool = await asyncpg.create_pool(_database_dsn(), min_size=1, max_size=1) if apply else None
        loaded = 0
        processed = 0
        try:
            if pool is not None:
                async with pool.acquire() as connection:
                    async with connection.transaction():
                        await connection.execute(CREATE_STAGE_SQL)
                        for batch in _chunks(object_ids, BATCH_SIZE):
                            rows = await _read_feature_batch(client, batch)
                            processed += len(batch)
                            if len(rows) != len(batch) or {int(row[0]) for row in rows} != set(batch):
                                raise RuntimeError(
                                    "ISPRA did not return one valid line geometry for every requested "
                                    "feature ID; refusing an incomplete refresh"
                                )
                            if rows:
                                await connection.copy_records_to_table(
                                    "_ispra_coastal_dynamics_stage",
                                    records=rows,
                                    columns=(
                                        "object_id", "cod_istat", "change_class", "coast_type",
                                        "shoreline_length_m", "mean_variation_m", "max_variation_m",
                                        "severity_index", "raw_attributes", "geom_wkb",
                                    ),
                                )
                                loaded += len(rows)
                            print(f"validated {processed} / {len(object_ids)} features")
                        stage_count = await connection.fetchval(
                            "SELECT COUNT(*) FROM _ispra_coastal_dynamics_stage"
                        )
                        if not stage_count:
                            raise RuntimeError("No valid geometries were returned; refusing to replace the local snapshot")
                        await connection.execute("TRUNCATE spatial.ispra_coastal_dynamics")
                        await connection.execute(STAGE_TO_FINAL_SQL)
                        final_count = await connection.fetchval(
                            "SELECT COUNT(*) FROM spatial.ispra_coastal_dynamics"
                        )
                        if final_count != stage_count:
                            raise RuntimeError("Imported row count does not match the staged row count")
                        loaded = int(final_count)
            else:
                for batch in _chunks(object_ids, BATCH_SIZE):
                    rows = await _read_feature_batch(client, batch)
                    processed += len(batch)
                    if len(rows) != len(batch) or {int(row[0]) for row in rows} != set(batch):
                        raise RuntimeError(
                            "ISPRA did not return one valid line geometry for every requested "
                            "feature ID"
                        )
                    loaded += len(rows)
                    print(f"validated {processed} / {len(object_ids)} features")
        finally:
            if pool is not None:
                await pool.close()
    return len(object_ids), loaded


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="atomically replace the local PostgreSQL snapshot")
    parser.add_argument("--max-features", type=int, help="validate a bounded sample; cannot be used with --apply")
    args = parser.parse_args()
    if args.max_features is not None and args.max_features < 1:
        parser.error("--max-features must be positive")
    if args.apply and args.max_features is not None:
        parser.error("--max-features is a dry-run option and cannot be used with --apply")
    requested, loaded = await refresh(apply=args.apply, max_features=args.max_features)
    if args.apply:
        print(f"Replaced local ISPRA snapshot with {loaded} features (requested {requested})")
    else:
        print(f"Dry run complete: {loaded} valid geometries from {requested} selected features; database unchanged")


if __name__ == "__main__":
    asyncio.run(main())
