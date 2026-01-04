"""
SpatiaLite-based cadastral database for efficient polygon storage and querying.

Provides exhaustive filtering capabilities for Italian cadastral data:
- Geographic hierarchy: Regione, Provincia, Comune
- Cadastral hierarchy: Foglio, Particella
- Spatial queries: Bounding box, point-in-polygon, intersects
- Temporal queries: By date range

Uses SpatiaLite (SQLite + spatial extensions) for:
- Single-file database (no server required)
- Full spatial SQL support
- Efficient spatial indexing (R-tree)
- Easy deployment and backup
"""

import sqlite3
import logging
from pathlib import Path
from typing import Optional, Generator
from datetime import datetime
from contextlib import contextmanager
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Check for spatialite support
SPATIALITE_AVAILABLE = False
SPATIALITE_LIB = None

# Try common spatialite library names
for lib_name in ['mod_spatialite', 'mod_spatialite.so', 'mod_spatialite.dylib',
                 'libspatialite.so', 'spatialite.dll']:
    try:
        conn = sqlite3.connect(':memory:')
        conn.enable_load_extension(True)
        conn.load_extension(lib_name.replace('.so', '').replace('.dylib', '').replace('.dll', ''))
        conn.close()
        SPATIALITE_LIB = lib_name.replace('.so', '').replace('.dylib', '').replace('.dll', '')
        SPATIALITE_AVAILABLE = True
        break
    except Exception:
        continue

if not SPATIALITE_AVAILABLE:
    logger.warning("SpatiaLite extension not found. Spatial queries will be limited.")


@dataclass
class CadastralFilter:
    """Exhaustive filter for cadastral polygon queries."""

    # Geographic hierarchy
    regione: Optional[str] = None
    provincia: Optional[str] = None
    comune: Optional[str] = None  # ADMINISTRATIVEUNIT code (e.g., I056)
    comune_name: Optional[str] = None  # Comune name for display

    # Cadastral hierarchy
    foglio: Optional[int] = None
    foglio_list: Optional[list[int]] = None  # Multiple fogli
    particella: Optional[int] = None
    particella_list: Optional[list[int]] = None  # Multiple particelle
    particella_range: Optional[tuple[int, int]] = None  # (min, max)
    particella_label: Optional[str] = None  # Text-based label (e.g., "STRADA001", "A")

    # Spatial filters
    bbox: Optional[tuple[float, float, float, float]] = None  # (min_lon, min_lat, max_lon, max_lat)
    point: Optional[tuple[float, float]] = None  # (lon, lat) - find parcels containing point
    intersects_wkt: Optional[str] = None  # WKT geometry to intersect

    # Temporal filters
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None

    # Data type
    layer_type: Optional[str] = None  # 'map' (fogli) or 'ple' (particelle)

    # Pagination
    limit: Optional[int] = None
    offset: int = 0

    def to_sql_conditions(self) -> tuple[str, list]:
        """Convert filter to SQL WHERE conditions and parameters."""
        conditions = []
        params = []

        # Geographic hierarchy
        if self.regione:
            conditions.append("regione = ?")
            params.append(self.regione.upper())

        if self.provincia:
            conditions.append("provincia = ?")
            params.append(self.provincia.upper())

        if self.comune:
            conditions.append("comune_code = ?")
            params.append(self.comune.upper())

        if self.comune_name:
            conditions.append("comune_name LIKE ?")
            params.append(f"%{self.comune_name}%")

        # Cadastral hierarchy
        if self.foglio is not None:
            conditions.append("foglio = ?")
            params.append(self.foglio)

        if self.foglio_list:
            placeholders = ",".join("?" * len(self.foglio_list))
            conditions.append(f"foglio IN ({placeholders})")
            params.extend(self.foglio_list)

        if self.particella is not None:
            conditions.append("particella = ?")
            params.append(self.particella)

        if self.particella_list:
            placeholders = ",".join("?" * len(self.particella_list))
            conditions.append(f"particella IN ({placeholders})")
            params.extend(self.particella_list)

        if self.particella_range:
            conditions.append("particella BETWEEN ? AND ?")
            params.extend(self.particella_range)

        if self.particella_label:
            conditions.append("label = ?")
            params.append(self.particella_label)

        # Temporal filters
        if self.date_from:
            conditions.append("begin_lifespan >= ?")
            params.append(self.date_from.strftime("%Y-%m-%d"))

        if self.date_to:
            conditions.append("begin_lifespan <= ?")
            params.append(self.date_to.strftime("%Y-%m-%d"))

        # Layer type
        if self.layer_type:
            conditions.append("layer_type = ?")
            params.append(self.layer_type.lower())

        return " AND ".join(conditions) if conditions else "1=1", params

    def to_spatial_conditions(self) -> tuple[str, list]:
        """Generate spatial SQL conditions (requires SpatiaLite)."""
        conditions = []
        params = []

        if self.bbox and SPATIALITE_AVAILABLE:
            # Bounding box query using R-tree index
            min_lon, min_lat, max_lon, max_lat = self.bbox
            conditions.append("""
                id IN (
                    SELECT id FROM idx_cadastral_parcels_geometry
                    WHERE minx <= ? AND maxx >= ? AND miny <= ? AND maxy >= ?
                )
            """)
            params.extend([max_lon, min_lon, max_lat, min_lat])

        if self.point and SPATIALITE_AVAILABLE:
            lon, lat = self.point
            conditions.append("ST_Contains(geometry, MakePoint(?, ?, 6706))")
            params.extend([lon, lat])

        if self.intersects_wkt and SPATIALITE_AVAILABLE:
            conditions.append("ST_Intersects(geometry, GeomFromText(?, 6706))")
            params.append(self.intersects_wkt)

        return " AND ".join(conditions) if conditions else "", params


class CadastralDatabase:
    """SpatiaLite database for cadastral data storage and querying."""

    def __init__(self, db_path: str | Path):
        """
        Initialize cadastral database.

        Args:
            db_path: Path to SQLite/SpatiaLite database file
        """
        self.db_path = Path(db_path)
        self._ensure_tables()

    @contextmanager
    def _get_connection(self, init_spatialite: bool = False) -> Generator[sqlite3.Connection, None, None]:
        """Get a database connection with SpatiaLite loaded if available."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row

        if SPATIALITE_AVAILABLE and SPATIALITE_LIB:
            try:
                conn.enable_load_extension(True)
                conn.load_extension(SPATIALITE_LIB)
                if init_spatialite:
                    # Only init metadata on first connection/table creation
                    conn.execute("SELECT InitSpatialMetaData(1)")
            except Exception as e:
                logger.debug(f"SpatiaLite load/init: {e}")

        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _ensure_tables(self) -> None:
        """Create database tables if they don't exist."""
        with self._get_connection(init_spatialite=True) as conn:
            # Main parcels table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cadastral_parcels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,

                    -- Geographic hierarchy
                    regione TEXT NOT NULL,
                    provincia TEXT NOT NULL,
                    comune_code TEXT NOT NULL,
                    comune_name TEXT,

                    -- Cadastral hierarchy
                    foglio INTEGER NOT NULL,
                    particella INTEGER,

                    -- Layer type
                    layer_type TEXT NOT NULL CHECK(layer_type IN ('map', 'ple')),

                    -- INSPIRE metadata
                    inspire_id TEXT,
                    inspire_namespace TEXT,
                    label TEXT,
                    national_reference TEXT UNIQUE,

                    -- Temporal
                    begin_lifespan DATE,
                    end_lifespan DATE,

                    -- Additional metadata
                    level TEXT,
                    level_name TEXT,
                    original_scale INTEGER,

                    -- Geometry stored as WKT (or blob for SpatiaLite)
                    geometry_wkt TEXT,

                    -- Bounds for non-spatial queries
                    min_lon REAL,
                    min_lat REAL,
                    max_lon REAL,
                    max_lat REAL,

                    -- Import metadata
                    source_file TEXT,
                    imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Create indexes for common queries
            conn.execute("CREATE INDEX IF NOT EXISTS idx_regione ON cadastral_parcels(regione)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_provincia ON cadastral_parcels(provincia)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_comune ON cadastral_parcels(comune_code)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_foglio ON cadastral_parcels(foglio)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_particella ON cadastral_parcels(particella)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_layer_type ON cadastral_parcels(layer_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_begin_lifespan ON cadastral_parcels(begin_lifespan)")

            # Create spatial index if SpatiaLite is available
            if SPATIALITE_AVAILABLE:
                try:
                    # Add geometry column if not exists
                    conn.execute("""
                        SELECT AddGeometryColumn('cadastral_parcels', 'geometry', 6706, 'MULTIPOLYGON', 'XY')
                    """)
                except Exception:
                    pass  # Column might already exist

                try:
                    # Create R-tree spatial index
                    conn.execute("SELECT CreateSpatialIndex('cadastral_parcels', 'geometry')")
                except Exception:
                    pass  # Index might already exist

            # Statistics table for quick lookups
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cadastral_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    regione TEXT,
                    provincia TEXT,
                    comune_code TEXT,
                    layer_type TEXT,
                    count INTEGER,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(regione, provincia, comune_code, layer_type)
                )
            """)

            conn.commit()

    def import_geopandas(
        self,
        gdf,  # GeoDataFrame
        regione: str,
        provincia: str,
        comune_code: str,
        comune_name: str,
        layer_type: str,  # 'map' or 'ple'
        source_file: Optional[str] = None
    ) -> int:
        """
        Import a GeoDataFrame into the database.

        Args:
            gdf: GeoDataFrame with cadastral data
            regione: Region name
            provincia: Province code
            comune_code: Municipality cadastral code
            comune_name: Municipality name
            layer_type: 'map' for fogli, 'ple' for particelle
            source_file: Source file path for tracking

        Returns:
            Number of records imported
        """
        imported = 0

        with self._get_connection() as conn:
            for idx, row in gdf.iterrows():
                try:
                    # Parse cadastral reference
                    label = str(row.get('LABEL', ''))

                    if layer_type == 'map':
                        ref_field = 'NATIONALCADASTRALZONINGREFERENCE'
                        # For MAP, LABEL is the foglio number
                        foglio = int(label) if label.isdigit() else 0
                        particella = None
                    else:
                        ref_field = 'NATIONALCADASTRALREFERENCE'
                        # Parse reference like "I056_000400.1"
                        ref = row.get(ref_field, '')
                        if '.' in ref:
                            # Extract foglio from reference: I056_000400.1 -> foglio 4
                            # Reference format: COMUNE_FOGLIO*100.PARTICELLA
                            foglio_part = ref.split('.')[0].split('_')[-1]
                            if foglio_part.isdigit():
                                # 000400 / 100 = 4
                                foglio = int(foglio_part) // 100
                            else:
                                foglio = 0
                        else:
                            foglio = 0

                        # Particella can be numeric (1, 42) or alphanumeric (A, STRADA001)
                        # Store as integer if numeric, otherwise store None and keep label
                        particella = int(label) if label.isdigit() else None

                    # Parse date
                    begin_date = row.get('BEGINLIFESPANVERSION', '')
                    if begin_date and isinstance(begin_date, str):
                        try:
                            # Format: DD/MM/YYYY
                            begin_date = datetime.strptime(begin_date, '%d/%m/%Y').date()
                        except ValueError:
                            begin_date = None
                    else:
                        begin_date = None

                    # Get geometry bounds
                    geom = row.geometry
                    if geom is not None and not geom.is_empty:
                        bounds = geom.bounds
                        geometry_wkt = geom.wkt
                    else:
                        bounds = (None, None, None, None)
                        geometry_wkt = None

                    # Insert record
                    conn.execute("""
                        INSERT OR REPLACE INTO cadastral_parcels (
                            regione, provincia, comune_code, comune_name,
                            foglio, particella, layer_type,
                            inspire_id, inspire_namespace, label, national_reference,
                            begin_lifespan, end_lifespan,
                            level, level_name, original_scale,
                            geometry_wkt, min_lon, min_lat, max_lon, max_lat,
                            source_file
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        regione.upper(),
                        provincia.upper(),
                        comune_code.upper(),
                        comune_name,
                        foglio,
                        particella,
                        layer_type.lower(),
                        row.get('INSPIREID_LOCALID'),
                        row.get('INSPIREID_NAMESPACE'),
                        str(row.get('LABEL', '')),
                        row.get(ref_field),
                        begin_date,
                        None,  # end_lifespan
                        row.get('LEVEL'),
                        row.get('LEVELNAME'),
                        row.get('ORIGINALMAPSCALEDENOMINATOR'),
                        geometry_wkt,
                        bounds[0], bounds[1], bounds[2], bounds[3],
                        source_file
                    ))

                    imported += 1

                except Exception as e:
                    logger.warning(f"Error importing row {idx}: {e}")

            # Update statistics
            conn.execute("""
                INSERT OR REPLACE INTO cadastral_stats (regione, provincia, comune_code, layer_type, count)
                SELECT regione, provincia, comune_code, layer_type, COUNT(*)
                FROM cadastral_parcels
                WHERE regione = ? AND provincia = ? AND comune_code = ? AND layer_type = ?
                GROUP BY regione, provincia, comune_code, layer_type
            """, (regione.upper(), provincia.upper(), comune_code.upper(), layer_type.lower()))

            conn.commit()

        return imported

    def query(
        self,
        filter: CadastralFilter,
        as_geojson: bool = True
    ) -> dict | list[dict]:
        """
        Query cadastral parcels with exhaustive filtering.

        Args:
            filter: CadastralFilter with query parameters
            as_geojson: Return as GeoJSON FeatureCollection

        Returns:
            GeoJSON FeatureCollection or list of records
        """
        # Build query
        base_conditions, base_params = filter.to_sql_conditions()
        spatial_conditions, spatial_params = filter.to_spatial_conditions()

        where_clause = base_conditions
        params = base_params

        if spatial_conditions:
            where_clause = f"({base_conditions}) AND ({spatial_conditions})"
            params.extend(spatial_params)

        query = f"""
            SELECT
                id, regione, provincia, comune_code, comune_name,
                foglio, particella, layer_type,
                inspire_id, label, national_reference,
                begin_lifespan, level_name, original_scale,
                geometry_wkt, min_lon, min_lat, max_lon, max_lat
            FROM cadastral_parcels
            WHERE {where_clause}
            ORDER BY regione, provincia, comune_code, foglio, particella
        """

        if filter.limit:
            query += f" LIMIT {filter.limit}"
        if filter.offset:
            query += f" OFFSET {filter.offset}"

        with self._get_connection() as conn:
            cursor = conn.execute(query, params)
            rows = cursor.fetchall()

        if as_geojson:
            features = []
            for row in rows:
                feature = {
                    "type": "Feature",
                    "id": row['id'],
                    "properties": {
                        "regione": row['regione'],
                        "provincia": row['provincia'],
                        "comune_code": row['comune_code'],
                        "comune_name": row['comune_name'],
                        "foglio": row['foglio'],
                        "particella": row['particella'],
                        "layer_type": row['layer_type'],
                        "inspire_id": row['inspire_id'],
                        "label": row['label'],
                        "national_reference": row['national_reference'],
                        "begin_lifespan": row['begin_lifespan'],
                        "level_name": row['level_name'],
                        "original_scale": row['original_scale'],
                    },
                    "geometry": None  # Will be parsed from WKT
                }

                # Parse WKT to GeoJSON geometry
                if row['geometry_wkt']:
                    try:
                        from shapely import wkt
                        from shapely.geometry import mapping
                        geom = wkt.loads(row['geometry_wkt'])
                        feature['geometry'] = mapping(geom)
                    except Exception as e:
                        logger.warning(f"Error parsing geometry: {e}")

                features.append(feature)

            return {
                "type": "FeatureCollection",
                "features": features,
                "metadata": {
                    "total_count": len(features),
                    "filter": {
                        "regione": filter.regione,
                        "provincia": filter.provincia,
                        "comune": filter.comune,
                        "foglio": filter.foglio,
                        "particella": filter.particella,
                        "layer_type": filter.layer_type,
                    }
                }
            }
        else:
            return [dict(row) for row in rows]

    def get_statistics(self) -> dict:
        """Get database statistics by region, province, comune."""
        with self._get_connection() as conn:
            # Total counts
            total = conn.execute("SELECT COUNT(*) FROM cadastral_parcels").fetchone()[0]

            # By region
            regions = conn.execute("""
                SELECT regione, COUNT(*) as count
                FROM cadastral_parcels
                GROUP BY regione
                ORDER BY regione
            """).fetchall()

            # By layer type
            by_type = conn.execute("""
                SELECT layer_type, COUNT(*) as count
                FROM cadastral_parcels
                GROUP BY layer_type
            """).fetchall()

            return {
                "total_parcels": total,
                "by_region": {r['regione']: r['count'] for r in regions},
                "by_layer_type": {t['layer_type']: t['count'] for t in by_type},
                "spatialite_available": SPATIALITE_AVAILABLE,
            }

    def get_hierarchy(
        self,
        regione: Optional[str] = None,
        provincia: Optional[str] = None,
        comune: Optional[str] = None
    ) -> dict:
        """
        Get available hierarchy values for cascading dropdowns.

        Args:
            regione: Filter by region
            provincia: Filter by province
            comune: Filter by comune

        Returns:
            Available values at next hierarchy level
        """
        with self._get_connection() as conn:
            if regione is None:
                # Get all regions
                rows = conn.execute("""
                    SELECT DISTINCT regione FROM cadastral_parcels ORDER BY regione
                """).fetchall()
                return {"regions": [r['regione'] for r in rows]}

            elif provincia is None:
                # Get provinces in region
                rows = conn.execute("""
                    SELECT DISTINCT provincia FROM cadastral_parcels
                    WHERE regione = ? ORDER BY provincia
                """, (regione.upper(),)).fetchall()
                return {"provinces": [r['provincia'] for r in rows]}

            elif comune is None:
                # Get comuni in province
                rows = conn.execute("""
                    SELECT DISTINCT comune_code, comune_name FROM cadastral_parcels
                    WHERE regione = ? AND provincia = ?
                    ORDER BY comune_name
                """, (regione.upper(), provincia.upper())).fetchall()
                return {"comuni": [{"code": r['comune_code'], "name": r['comune_name']} for r in rows]}

            else:
                # Get fogli in comune
                rows = conn.execute("""
                    SELECT DISTINCT foglio FROM cadastral_parcels
                    WHERE comune_code = ?
                    ORDER BY foglio
                """, (comune.upper(),)).fetchall()
                return {"fogli": [r['foglio'] for r in rows]}
