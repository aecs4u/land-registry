"""
Helper utilities for loading geodata from a SpatiaLite database.
"""

import logging
import sqlite3
from contextlib import contextmanager
from typing import Optional

import geopandas as gpd

from land_registry.config import spatialite_settings

logger = logging.getLogger(__name__)


@contextmanager
def _spatialite_connection(db_path: Optional[str] = None):
    """Yield a SQLite connection with the SpatiaLite extension loaded."""
    database_path = db_path or spatialite_settings.db_path
    conn = sqlite3.connect(database_path)
    try:
        conn.enable_load_extension(True)

        extension_path = spatialite_settings.extension_path or "mod_spatialite"
        try:
            conn.load_extension(extension_path)
        except sqlite3.OperationalError as exc:
            # Surface a clearer message while still raising to the caller
            logger.error(
                "Failed to load SpatiaLite extension '%s': %s",
                extension_path,
                exc,
            )
            raise

        yield conn
    finally:
        conn.close()


def load_layer(
    table: Optional[str] = None,
    where: Optional[str] = None,
    limit: Optional[int] = None,
    layer_type: Optional[str] = None,
):
    """
    Load a layer from SpatiaLite into a GeoDataFrame.

    Args:
        table: Table/view name to query (defaults to settings.table)
        where: Optional SQL WHERE clause (without the 'WHERE' keyword)
        limit: Optional row limit (defaults to settings.default_limit)
        layer_type: Layer type ('map' or 'ple') to determine database path
    """
    table_name = table or spatialite_settings.table
    row_limit = limit or spatialite_settings.default_limit

    # Determine which database to use based on layer_type
    if layer_type == 'ple':
        db_path = spatialite_settings.db_ple_path
    else:
        # Default to MAP database
        db_path = spatialite_settings.db_map_path

    # Build query with optional filter/limit
    sql = (
        f"SELECT *, ST_AsBinary({spatialite_settings.geometry_column}) AS geom "
        f"FROM {table_name}"
    )
    if where:
        sql += f" WHERE {where}"
    if row_limit:
        sql += f" LIMIT {row_limit}"

    with _spatialite_connection(db_path) as conn:
        gdf = gpd.GeoDataFrame.from_postgis(
            sql,
            conn,
            geom_col="geom",
            crs=f"EPSG:{spatialite_settings.srid}",
        )

    return gdf
