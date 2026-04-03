"""
Helper utilities for loading geodata from a SpatiaLite database.
"""

import logging
import os
import re
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, Optional

import geopandas as gpd

from land_registry.config import spatialite_settings

logger = logging.getLogger(__name__)

# Identifier pattern: letters, digits, underscores only (no SQL metacharacters).
_IDENTIFIER_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


def _validate_identifier(name: str, label: str) -> None:
    """Raise ValueError if *name* is not a safe SQL identifier."""
    if not _IDENTIFIER_RE.match(name):
        raise ValueError(f"Invalid {label} '{name}': only letters, digits and underscores are allowed")


def _allowed_tables() -> frozenset:
    """Return the set of table names that callers are permitted to query."""
    return frozenset({
        spatialite_settings.table,
        "fogli",
        "particelle",
        "cadastral_parcels",
    })


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


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    """Check if a table exists in the database."""
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,)
    )
    return cursor.fetchone() is not None


def load_layer(
    table: Optional[str] = None,
    conditions: Optional[Dict[str, Any]] = None,
    limit: Optional[int] = None,
    layer_type: Optional[str] = None,
):
    """
    Load a layer from SpatiaLite into a GeoDataFrame.

    Args:
        table: Table/view name to query (defaults to settings.table).
               Must be in the allowlist of known table names.
        conditions: Optional mapping of ``{column_name: value}`` pairs used
                    to build a parameterised WHERE clause.  Column names must
                    be valid SQL identifiers (letters/digits/underscores only).
        limit: Optional row limit (defaults to settings.default_limit).
        layer_type: Layer type ('map' or 'ple') to determine database path.

    Returns:
        GeoDataFrame with the queried data, or None if table doesn't exist.
    """
    table_name = table or spatialite_settings.table
    row_limit = limit or spatialite_settings.default_limit

    # --- Table-name allowlist check -------------------------------------------
    allowed = _allowed_tables()
    if table_name not in allowed:
        raise ValueError(
            f"Table '{table_name}' is not in the list of allowed tables: {sorted(allowed)}"
        )

    # --- Determine which database to use --------------------------------------
    if layer_type == 'ple':
        db_path = spatialite_settings.db_ple_path
    else:
        db_path = spatialite_settings.db_map_path

    # --- Check if database file exists ----------------------------------------
    if not db_path or not os.path.exists(db_path):
        logger.warning(f"SpatiaLite database not found at: {db_path}")
        return None

    # --- Build parameterised query -------------------------------------------
    geom_col = spatialite_settings.geometry_column
    sql = (
        f"SELECT *, ST_AsBinary({geom_col}) AS geom "
        f"FROM {table_name}"
    )
    params: list = []

    if conditions:
        clauses = []
        for col, val in conditions.items():
            _validate_identifier(col, "column name")
            clauses.append(f"{col} = ?")
            params.append(val)
        sql += " WHERE " + " AND ".join(clauses)

    if row_limit:
        sql += " LIMIT ?"
        params.append(row_limit)

    with _spatialite_connection(db_path) as conn:
        # Check if table exists before querying
        if not _table_exists(conn, table_name):
            logger.warning(f"Table '{table_name}' does not exist in database: {db_path}")
            return None

        gdf = gpd.GeoDataFrame.from_postgis(
            sql,
            conn,
            geom_col="geom",
            crs=f"EPSG:{spatialite_settings.srid}",
            params=params if params else None,
        )

    return gdf
