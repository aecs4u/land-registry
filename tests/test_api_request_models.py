"""
Unit tests for Pydantic request models in land_registry/routers/api.py.

Tests all field validators, covering uncovered paths in the models.
"""

import pytest
from pydantic import ValidationError

from land_registry.routers.api import (
    CadastralFileRequest,
    CadastralQueryRequest,
    DrawnPolygonsRequest,
    PolygonSelection,
    PublicGeoDataRequest,
    ZoneOverlayLookupRequest,
)


# ---------------------------------------------------------------------------
# PolygonSelection
# ---------------------------------------------------------------------------

class TestPolygonSelection:

    def test_valid_polygon_geometry(self):
        ps = PolygonSelection(
            feature_id=1,
            geometry={"type": "Polygon", "coordinates": [[[12, 41], [13, 41], [13, 42], [12, 41]]]},
        )
        assert ps.feature_id == 1

    def test_geometry_missing_type_raises(self):
        with pytest.raises(ValidationError, match="type"):
            PolygonSelection(
                feature_id=0,
                geometry={"coordinates": [[12, 41]]},  # no 'type' key
            )

    def test_geometry_invalid_type_raises(self):
        with pytest.raises(ValidationError):
            PolygonSelection(
                feature_id=0,
                geometry={"type": "GeometryCollection", "geometries": []},
            )

    def test_valid_touch_methods(self):
        for method in ("touches", "intersects", "overlaps"):
            ps = PolygonSelection(
                feature_id=1,
                geometry={"type": "Point", "coordinates": [12, 41]},
                touch_method=method,
            )
            assert ps.touch_method == method

    def test_invalid_touch_method_raises(self):
        with pytest.raises(ValidationError):
            PolygonSelection(
                feature_id=1,
                geometry={"type": "Point", "coordinates": [12, 41]},
                touch_method="contains",
            )

    def test_negative_feature_id_raises(self):
        with pytest.raises(ValidationError):
            PolygonSelection(
                feature_id=-1,
                geometry={"type": "Point", "coordinates": [12, 41]},
            )


# ---------------------------------------------------------------------------
# CadastralFileRequest
# ---------------------------------------------------------------------------

class TestCadastralFileRequest:

    def test_valid_paths(self):
        req = CadastralFileRequest(file_paths=["LAZIO/RM/file.gpkg"])
        assert len(req.file_paths) == 1

    def test_path_traversal_raises(self):
        with pytest.raises(ValidationError):
            CadastralFileRequest(file_paths=["../etc/passwd.gpkg"])

    def test_absolute_path_raises(self):
        with pytest.raises(ValidationError):
            CadastralFileRequest(file_paths=["/absolute/path.gpkg"])

    def test_unsupported_extension_raises(self):
        with pytest.raises(ValidationError):
            CadastralFileRequest(file_paths=["LAZIO/RM/file.exe"])

    def test_valid_extensions(self):
        for ext in (".gpkg", ".fgb", ".geojson", ".shp", ".kml", ".qpkg"):
            req = CadastralFileRequest(file_paths=[f"REGION/PROV/file{ext}"])
            assert len(req.file_paths) == 1

    def test_empty_paths_raises(self):
        with pytest.raises(ValidationError):
            CadastralFileRequest(file_paths=[])


# ---------------------------------------------------------------------------
# DrawnPolygonsRequest
# ---------------------------------------------------------------------------

class TestDrawnPolygonsRequest:

    VALID_GJ = {"type": "FeatureCollection", "features": []}

    def test_valid_request(self):
        req = DrawnPolygonsRequest(geojson=self.VALID_GJ, filename="my_drawing.geojson")
        assert req.filename == "my_drawing.geojson"

    def test_filename_path_traversal_raises(self):
        with pytest.raises(ValidationError):
            DrawnPolygonsRequest(geojson=self.VALID_GJ, filename="../secret.geojson")

    def test_filename_with_slash_raises(self):
        with pytest.raises(ValidationError):
            DrawnPolygonsRequest(geojson=self.VALID_GJ, filename="sub/file.geojson")

    def test_filename_backslash_raises(self):
        with pytest.raises(ValidationError):
            DrawnPolygonsRequest(geojson=self.VALID_GJ, filename="sub\\file.geojson")

    def test_filename_wrong_extension_raises(self):
        with pytest.raises(ValidationError):
            DrawnPolygonsRequest(geojson=self.VALID_GJ, filename="drawing.txt")

    def test_filename_invalid_chars_raises(self):
        with pytest.raises(ValidationError):
            DrawnPolygonsRequest(geojson=self.VALID_GJ, filename="file<name>.geojson")

    def test_geojson_not_feature_collection_raises(self):
        with pytest.raises(ValidationError):
            DrawnPolygonsRequest(
                geojson={"type": "Feature", "geometry": None, "properties": {}},
                filename="drawing.geojson",
            )

    def test_geojson_missing_features_raises(self):
        with pytest.raises(ValidationError):
            DrawnPolygonsRequest(
                geojson={"type": "FeatureCollection"},  # no 'features' key
                filename="drawing.geojson",
            )

    def test_valid_json_extension(self):
        req = DrawnPolygonsRequest(geojson=self.VALID_GJ, filename="data.json")
        assert req.filename == "data.json"


# ---------------------------------------------------------------------------
# PublicGeoDataRequest
# ---------------------------------------------------------------------------

class TestPublicGeoDataRequest:

    def test_valid_request(self):
        req = PublicGeoDataRequest(s3_key="ITALIA/LAZIO/RM/ROMA/file.gpkg")
        assert req.layer == 0

    def test_path_traversal_raises(self):
        with pytest.raises(ValidationError):
            PublicGeoDataRequest(s3_key="ITALIA/../secret.gpkg")

    def test_unsupported_extension_raises(self):
        with pytest.raises(ValidationError):
            PublicGeoDataRequest(s3_key="ITALIA/LAZIO/RM/file.exe")

    def test_valid_extensions(self):
        for ext in (".gpkg", ".fgb", ".geojson", ".shp", ".kml"):
            req = PublicGeoDataRequest(s3_key=f"ITALIA/LAZIO/RM/file{ext}")
            assert req.s3_key.endswith(ext)


# ---------------------------------------------------------------------------
# ZoneOverlayLookupRequest
# ---------------------------------------------------------------------------

class TestZoneOverlayLookupRequest:

    VALID_POLYGON_FEATURE = {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[12.0, 41.0], [13.0, 41.0], [13.0, 42.0], [12.0, 42.0], [12.0, 41.0]]]
        },
        "properties": {}
    }

    def test_valid_request(self):
        req = ZoneOverlayLookupRequest(zone_geojson=self.VALID_POLYGON_FEATURE)
        assert req.relation == "intersects"

    def test_non_feature_type_raises(self):
        with pytest.raises(ValidationError, match="GeoJSON Feature"):
            ZoneOverlayLookupRequest(
                zone_geojson={"type": "FeatureCollection", "features": []}
            )

    def test_geometry_not_dict_raises(self):
        with pytest.raises(ValidationError):
            ZoneOverlayLookupRequest(
                zone_geojson={"type": "Feature", "geometry": None, "properties": {}}
            )

    def test_invalid_geometry_type_raises(self):
        with pytest.raises(ValidationError):
            ZoneOverlayLookupRequest(
                zone_geojson={
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [12.5, 41.5]},
                    "properties": {}
                }
            )

    def test_multipolygon_is_valid(self):
        req = ZoneOverlayLookupRequest(
            zone_geojson={
                "type": "Feature",
                "geometry": {"type": "MultiPolygon", "coordinates": []},
                "properties": {}
            }
        )
        assert req is not None


# ---------------------------------------------------------------------------
# CadastralQueryRequest.to_cadastral_filter
# ---------------------------------------------------------------------------

class TestCadastralQueryRequestToFilter:

    def test_basic_filter(self):
        req = CadastralQueryRequest(regione="LAZIO")
        f = req.to_cadastral_filter()
        assert f.regione == "LAZIO"

    def test_bbox_built_when_all_coords_provided(self):
        req = CadastralQueryRequest(
            bbox_min_lon=12.0,
            bbox_min_lat=41.0,
            bbox_max_lon=13.0,
            bbox_max_lat=42.0,
        )
        f = req.to_cadastral_filter()
        assert f.bbox == (12.0, 41.0, 13.0, 42.0)

    def test_bbox_none_when_partial_coords(self):
        req = CadastralQueryRequest(bbox_min_lon=12.0)  # Only one coord
        f = req.to_cadastral_filter()
        assert f.bbox is None

    def test_point_built_when_both_coords(self):
        req = CadastralQueryRequest(point_lon=12.5, point_lat=41.5)
        f = req.to_cadastral_filter()
        assert f.point == (12.5, 41.5)

    def test_point_none_when_only_one(self):
        req = CadastralQueryRequest(point_lon=12.5)
        f = req.to_cadastral_filter()
        assert f.point is None

    def test_particella_range_built(self):
        req = CadastralQueryRequest(particella_min=1, particella_max=100)
        f = req.to_cadastral_filter()
        assert f.particella_range == (1, 100)

    def test_particella_range_none_when_partial(self):
        req = CadastralQueryRequest(particella_min=1)
        f = req.to_cadastral_filter()
        assert f.particella_range is None

    def test_date_from_parsed(self):
        req = CadastralQueryRequest(date_from="2024-01-15")
        f = req.to_cadastral_filter()
        assert f.date_from is not None
        assert f.date_from.year == 2024
        assert f.date_from.month == 1

    def test_date_to_parsed(self):
        req = CadastralQueryRequest(date_to="2024-12-31")
        f = req.to_cadastral_filter()
        assert f.date_to is not None
        assert f.date_to.year == 2024
        assert f.date_to.day == 31

    def test_no_dates(self):
        req = CadastralQueryRequest()
        f = req.to_cadastral_filter()
        assert f.date_from is None
        assert f.date_to is None
