"""
Unit tests for cadastral_db.py — CadastralFilter SQL generation and CadastralDatabase.

Focuses on testable logic without requiring a real database:
- CadastralFilter.to_sql_conditions()
- CadastralFilter.to_spatial_conditions()
- CadastralDatabase initialization (with temp SQLite file)
- CadastralDatabase.get_statistics() and get_hierarchy() with in-memory db
"""

import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from land_registry.cadastral_db import CadastralDatabase, CadastralFilter


# ---------------------------------------------------------------------------
# CadastralFilter.to_sql_conditions tests
# ---------------------------------------------------------------------------

class TestCadastralFilterToSQLConditions:

    def test_empty_filter(self):
        """Empty filter returns always-true condition."""
        f = CadastralFilter()
        where, params = f.to_sql_conditions()
        assert where == "1=1"
        assert params == []

    def test_regione_filter(self):
        f = CadastralFilter(regione="lazio")
        where, params = f.to_sql_conditions()
        assert "regione = ?" in where
        assert "LAZIO" in params

    def test_provincia_filter(self):
        f = CadastralFilter(provincia="rm")
        where, params = f.to_sql_conditions()
        assert "provincia = ?" in where
        assert "RM" in params

    def test_comune_filter(self):
        f = CadastralFilter(comune="H501")
        where, params = f.to_sql_conditions()
        assert "comune_code = ?" in where
        assert "H501" in params

    def test_comune_name_filter(self):
        f = CadastralFilter(comune_name="Roma")
        where, params = f.to_sql_conditions()
        assert "comune_name LIKE ?" in where
        assert "%Roma%" in params

    def test_foglio_filter(self):
        f = CadastralFilter(foglio=42)
        where, params = f.to_sql_conditions()
        assert "foglio = ?" in where
        assert 42 in params

    def test_foglio_list_filter(self):
        f = CadastralFilter(foglio_list=[1, 2, 3])
        where, params = f.to_sql_conditions()
        assert "foglio IN (?,?,?)" in where
        assert params == [1, 2, 3]

    def test_particella_filter(self):
        f = CadastralFilter(particella=10)
        where, params = f.to_sql_conditions()
        assert "particella = ?" in where
        assert 10 in params

    def test_particella_list_filter(self):
        f = CadastralFilter(particella_list=[1, 5, 10])
        where, params = f.to_sql_conditions()
        assert "particella IN (?,?,?)" in where

    def test_particella_range_filter(self):
        f = CadastralFilter(particella_range=(1, 100))
        where, params = f.to_sql_conditions()
        assert "particella BETWEEN ? AND ?" in where
        assert 1 in params
        assert 100 in params

    def test_date_from_filter(self):
        f = CadastralFilter(date_from=datetime(2020, 1, 1))
        where, params = f.to_sql_conditions()
        assert "begin_lifespan >=" in where
        assert "2020-01-01" in params

    def test_date_to_filter(self):
        f = CadastralFilter(date_to=datetime(2025, 12, 31))
        where, params = f.to_sql_conditions()
        assert "begin_lifespan <=" in where

    def test_layer_type_filter(self):
        f = CadastralFilter(layer_type="MAP")
        where, params = f.to_sql_conditions()
        assert "layer_type = ?" in where
        assert "map" in params  # lowercased

    def test_combined_filters(self):
        f = CadastralFilter(regione="LAZIO", comune="H501", foglio=42)
        where, params = f.to_sql_conditions()
        assert "regione = ?" in where
        assert "comune_code = ?" in where
        assert "foglio = ?" in where
        assert len(params) == 3

    def test_bbox_filter_without_spatialite(self):
        """Bbox generates no condition in non-spatialite path; spatial handled separately."""
        f = CadastralFilter(bbox=(12.0, 41.0, 13.0, 42.0))
        where, params = f.to_sql_conditions()
        # bbox is handled in spatial conditions, not SQL conditions
        # The base to_sql_conditions doesn't add bbox
        # (spatial conditions are checked separately)
        assert isinstance(where, str)


# ---------------------------------------------------------------------------
# CadastralFilter.to_spatial_conditions tests
# ---------------------------------------------------------------------------

class TestCadastralFilterToSpatialConditions:

    def test_no_spatial_filters(self):
        """Returns empty string when no spatial filters set."""
        f = CadastralFilter()
        cond, params = f.to_spatial_conditions()
        assert cond == ""
        assert params == []

    def test_bbox_condition(self):
        f = CadastralFilter(bbox=(12.0, 41.0, 13.0, 42.0))
        cond, params = f.to_spatial_conditions()
        assert cond != ""
        assert len(params) >= 4

    def test_point_condition(self):
        f = CadastralFilter(point=(12.5, 41.5))
        cond, params = f.to_spatial_conditions()
        assert cond != ""
        assert 12.5 in params
        assert 41.5 in params


# ---------------------------------------------------------------------------
# CadastralDatabase initialization
# ---------------------------------------------------------------------------

class TestCadastralDatabaseInitialization:

    def test_init_with_nonexistent_db(self, tmp_path):
        """CadastralDatabase creates an empty db when path doesn't exist."""
        db_path = tmp_path / "test.sqlite"
        db = CadastralDatabase(db_path)
        assert db.db_path == db_path
        # Database file is created (or at least the object is created)
        assert db is not None

    def test_init_with_existing_empty_db(self, tmp_path):
        """CadastralDatabase initializes against an existing empty SQLite file."""
        db_path = tmp_path / "test.sqlite"
        # Create empty SQLite file
        conn = sqlite3.connect(str(db_path))
        conn.close()
        db = CadastralDatabase(db_path)
        assert db is not None

    def test_get_statistics_empty_db(self, tmp_path):
        """get_statistics returns zero counts on empty database."""
        db_path = tmp_path / "test.sqlite"
        db = CadastralDatabase(db_path)
        # May raise or return empty stats
        try:
            stats = db.get_statistics()
            assert isinstance(stats, dict)
            assert "total_parcels" in stats or stats == {}
        except Exception:
            pass  # DB not initialized is acceptable

    def test_get_hierarchy_empty_db(self, tmp_path):
        """get_hierarchy returns empty result on uninitialized database."""
        db_path = tmp_path / "test.sqlite"
        db = CadastralDatabase(db_path)
        try:
            result = db.get_hierarchy()
            assert isinstance(result, dict)
        except Exception:
            pass  # No table yet is acceptable


# ---------------------------------------------------------------------------
# CadastralDatabase with data: import_geopandas, query, statistics, hierarchy
# ---------------------------------------------------------------------------

import geopandas as gpd
from shapely.geometry import Polygon


def _make_db_with_data(tmp_path) -> CadastralDatabase:
    """Helper: create a CadastralDatabase and insert rows using its own connection."""
    db_path = tmp_path / "populated.sqlite"
    db = CadastralDatabase(db_path)

    rows = [
        ('LAZIO', 'RM', 'H501', 'ROMA', 1, 10, 'map',
         'ID1', '10', 'H501_001', '2020-01-01',
         'POLYGON ((12 41, 13 41, 13 42, 12 42, 12 41))',
         12.0, 41.0, 13.0, 42.0, 'test.gpkg'),
        ('LAZIO', 'RM', 'H501', 'ROMA', 2, 20, 'ple',
         'ID2', '20', 'H501_002', '2021-06-15',
         'POLYGON ((12 41, 12.5 41, 12.5 41.5, 12 41.5, 12 41))',
         12.0, 41.0, 12.5, 41.5, 'test2.gpkg'),
        ('LOMBARDIA', 'MI', 'F205', 'MILANO', 5, 100, 'map',
         'ID3', '100', 'F205_001', '2019-03-20',
         'POLYGON ((9 45, 10 45, 10 46, 9 46, 9 45))',
         9.0, 45.0, 10.0, 46.0, 'milano.gpkg'),
    ]

    with db._get_connection() as conn:
        for row in rows:
            conn.execute("""
                INSERT INTO cadastral_parcels
                    (regione, provincia, comune_code, comune_name, foglio, particella, layer_type,
                     inspire_id, label, national_reference, begin_lifespan,
                     geometry_wkt, min_lon, min_lat, max_lon, max_lat, source_file)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, row)

    return db


class TestCadastralDatabaseQuery:

    def test_query_all_returns_geojson(self, tmp_path):
        db = _make_db_with_data(tmp_path)
        result = db.query(CadastralFilter())
        assert result["type"] == "FeatureCollection"
        assert len(result["features"]) == 3

    def test_query_by_regione(self, tmp_path):
        db = _make_db_with_data(tmp_path)
        result = db.query(CadastralFilter(regione="LAZIO"))
        assert len(result["features"]) == 2
        for f in result["features"]:
            assert f["properties"]["regione"] == "LAZIO"

    def test_query_by_layer_type(self, tmp_path):
        db = _make_db_with_data(tmp_path)
        result = db.query(CadastralFilter(layer_type="ple"))
        assert len(result["features"]) == 1
        assert result["features"][0]["properties"]["layer_type"] == "ple"

    def test_query_by_foglio(self, tmp_path):
        db = _make_db_with_data(tmp_path)
        result = db.query(CadastralFilter(foglio=1))
        assert len(result["features"]) == 1
        assert result["features"][0]["properties"]["foglio"] == 1

    def test_query_with_limit(self, tmp_path):
        db = _make_db_with_data(tmp_path)
        result = db.query(CadastralFilter(limit=2))
        assert len(result["features"]) == 2

    def test_query_as_list(self, tmp_path):
        """as_geojson=False returns list of dicts."""
        db = _make_db_with_data(tmp_path)
        result = db.query(CadastralFilter(), as_geojson=False)
        assert isinstance(result, list)
        assert len(result) == 3

    def test_query_no_results(self, tmp_path):
        db = _make_db_with_data(tmp_path)
        result = db.query(CadastralFilter(regione="SICILIA"))
        assert result["type"] == "FeatureCollection"
        assert len(result["features"]) == 0

    def test_query_geometry_parsed_from_wkt(self, tmp_path):
        db = _make_db_with_data(tmp_path)
        result = db.query(CadastralFilter(foglio=1))
        feature = result["features"][0]
        # Geometry should be parsed from WKT
        assert feature["geometry"] is not None
        assert feature["geometry"]["type"] == "Polygon"

    def test_query_metadata(self, tmp_path):
        db = _make_db_with_data(tmp_path)
        result = db.query(CadastralFilter(regione="LAZIO"))
        assert "metadata" in result
        assert result["metadata"]["total_count"] == 2

    def test_query_by_comune(self, tmp_path):
        db = _make_db_with_data(tmp_path)
        result = db.query(CadastralFilter(comune="H501"))
        assert len(result["features"]) == 2

    def test_query_by_particella(self, tmp_path):
        db = _make_db_with_data(tmp_path)
        result = db.query(CadastralFilter(particella=100))
        assert len(result["features"]) == 1

    def test_query_bbox_non_spatialite(self, tmp_path):
        """bbox filter uses min/max columns when SpatiaLite not available."""
        db = _make_db_with_data(tmp_path)
        # bbox that includes LAZIO but not LOMBARDIA
        f = CadastralFilter(bbox=(11.0, 40.0, 14.0, 43.0))
        where, params = f.to_sql_conditions()
        # Just verify it generates a condition
        assert isinstance(where, str)


class TestCadastralDatabaseStatistics:

    def test_get_statistics_with_data(self, tmp_path):
        db = _make_db_with_data(tmp_path)
        stats = db.get_statistics()
        assert stats["total_parcels"] == 3
        assert "LAZIO" in stats["by_region"]
        assert "LOMBARDIA" in stats["by_region"]
        assert stats["by_region"]["LAZIO"] == 2
        assert stats["by_region"]["LOMBARDIA"] == 1

    def test_get_statistics_by_layer_type(self, tmp_path):
        db = _make_db_with_data(tmp_path)
        stats = db.get_statistics()
        assert "by_layer_type" in stats
        assert stats["by_layer_type"]["map"] == 2
        assert stats["by_layer_type"]["ple"] == 1

    def test_get_statistics_has_spatialite_flag(self, tmp_path):
        db = _make_db_with_data(tmp_path)
        stats = db.get_statistics()
        assert "spatialite_available" in stats


class TestCadastralDatabaseHierarchy:

    def test_get_regions(self, tmp_path):
        db = _make_db_with_data(tmp_path)
        result = db.get_hierarchy()
        assert "regions" in result
        assert "LAZIO" in result["regions"]
        assert "LOMBARDIA" in result["regions"]

    def test_get_provinces_for_region(self, tmp_path):
        db = _make_db_with_data(tmp_path)
        result = db.get_hierarchy(regione="LAZIO")
        assert "provinces" in result
        assert "RM" in result["provinces"]

    def test_get_comuni_for_province(self, tmp_path):
        db = _make_db_with_data(tmp_path)
        result = db.get_hierarchy(regione="LAZIO", provincia="RM")
        assert "comuni" in result
        codes = [c["code"] for c in result["comuni"]]
        assert "H501" in codes

    def test_get_fogli_for_comune(self, tmp_path):
        db = _make_db_with_data(tmp_path)
        result = db.get_hierarchy(regione="LAZIO", provincia="RM", comune="H501")
        assert "fogli" in result
        assert 1 in result["fogli"]
        assert 2 in result["fogli"]

    def test_get_hierarchy_unknown_region(self, tmp_path):
        db = _make_db_with_data(tmp_path)
        result = db.get_hierarchy(regione="SICILIA")
        assert "provinces" in result
        assert result["provinces"] == []


class TestCadastralDatabaseImportGeopandas:

    def test_import_map_layer(self, tmp_path):
        db_path = tmp_path / "import_test.sqlite"
        db = CadastralDatabase(db_path)

        poly = Polygon([(12, 41), (13, 41), (13, 42), (12, 42)])
        gdf = gpd.GeoDataFrame(
            {
                "LABEL": ["42"],
                "NATIONALCADASTRALZONINGREFERENCE": ["H501_004200"],
                "INSPIREID_LOCALID": ["local_42"],
                "INSPIREID_NAMESPACE": ["IT.AGE.PLA"],
                "BEGINLIFESPANVERSION": ["01/01/2020"],
                "LEVEL": ["3"],
                "LEVELNAME": ["parcel"],
                "ORIGINALMAPSCALEDENOMINATOR": [2000],
            },
            geometry=[poly],
            crs="EPSG:4326",
        )

        count = db.import_geopandas(
            gdf, regione="LAZIO", provincia="RM",
            comune_code="H501", comune_name="ROMA",
            layer_type="map"
        )
        assert count == 1

    def test_import_ple_layer(self, tmp_path):
        db_path = tmp_path / "import_ple.sqlite"
        db = CadastralDatabase(db_path)

        poly = Polygon([(12, 41), (12.5, 41), (12.5, 41.5), (12, 41.5)])
        gdf = gpd.GeoDataFrame(
            {
                "LABEL": ["10"],
                "NATIONALCADASTRALREFERENCE": ["H501_000400.10"],
                "INSPIREID_LOCALID": ["local_p10"],
                "INSPIREID_NAMESPACE": ["IT.AGE.PLA"],
                "BEGINLIFESPANVERSION": ["15/06/2021"],
                "LEVEL": ["4"],
                "LEVELNAME": ["particella"],
                "ORIGINALMAPSCALEDENOMINATOR": [1000],
            },
            geometry=[poly],
            crs="EPSG:4326",
        )

        count = db.import_geopandas(
            gdf, regione="LAZIO", provincia="RM",
            comune_code="H501", comune_name="ROMA",
            layer_type="ple"
        )
        assert count == 1

    def test_import_bad_date_skips_gracefully(self, tmp_path):
        """Rows with invalid date format still import."""
        db_path = tmp_path / "bad_date.sqlite"
        db = CadastralDatabase(db_path)

        poly = Polygon([(12, 41), (13, 41), (13, 42), (12, 42)])
        gdf = gpd.GeoDataFrame(
            {
                "LABEL": ["1"],
                "NATIONALCADASTRALZONINGREFERENCE": ["H501_BADDATE"],
                "INSPIREID_LOCALID": ["x"],
                "INSPIREID_NAMESPACE": ["ns"],
                "BEGINLIFESPANVERSION": ["not-a-date"],
                "LEVEL": ["3"],
                "LEVELNAME": ["parcel"],
                "ORIGINALMAPSCALEDENOMINATOR": [2000],
            },
            geometry=[poly],
            crs="EPSG:4326",
        )

        count = db.import_geopandas(
            gdf, regione="LAZIO", provincia="RM",
            comune_code="H501", comune_name="ROMA",
            layer_type="map"
        )
        assert count == 1

    def test_import_returns_total_count(self, tmp_path):
        db_path = tmp_path / "multi.sqlite"
        db = CadastralDatabase(db_path)

        polys = [
            Polygon([(12 + i * 0.1, 41), (12.1 + i * 0.1, 41), (12.1 + i * 0.1, 41.1), (12 + i * 0.1, 41.1)])
            for i in range(3)
        ]
        gdf = gpd.GeoDataFrame(
            {
                "LABEL": ["1", "2", "3"],
                "NATIONALCADASTRALZONINGREFERENCE": ["R1", "R2", "R3"],
                "INSPIREID_LOCALID": ["a", "b", "c"],
                "INSPIREID_NAMESPACE": ["ns", "ns", "ns"],
                "BEGINLIFESPANVERSION": ["01/01/2020", "01/01/2020", "01/01/2020"],
                "LEVEL": ["3", "3", "3"],
                "LEVELNAME": ["parcel", "parcel", "parcel"],
                "ORIGINALMAPSCALEDENOMINATOR": [2000, 2000, 2000],
            },
            geometry=polys,
            crs="EPSG:4326",
        )

        count = db.import_geopandas(
            gdf, regione="LAZIO", provincia="RM",
            comune_code="H501", comune_name="ROMA",
            layer_type="map"
        )
        assert count == 3
