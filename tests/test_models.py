"""
Unit tests for land_registry/models.py Pydantic validators.

Tests all field validators for ZoneCreateRequest, ZoneUpdateRequest,
MicrozoneCreateRequest, and MicrozoneUpdateRequest.
"""

import pytest
from pydantic import ValidationError

from land_registry.models import (
    MicrozoneCreateRequest,
    MicrozoneUpdateRequest,
    ZoneCreateRequest,
    ZoneUpdateRequest,
)


VALID_GEOJSON_FEATURE = {
    "type": "Feature",
    "geometry": {
        "type": "Polygon",
        "coordinates": [[[12.0, 41.0], [13.0, 41.0], [13.0, 42.0], [12.0, 42.0], [12.0, 41.0]]]
    },
    "properties": {}
}

VALID_POINT_FEATURE = {
    "type": "Feature",
    "geometry": {
        "type": "Point",
        "coordinates": [12.5, 41.5]
    },
    "properties": {}
}


# ---------------------------------------------------------------------------
# ZoneCreateRequest
# ---------------------------------------------------------------------------

class TestZoneCreateRequest:

    def test_valid_minimal(self):
        req = ZoneCreateRequest(
            name="Test Zone",
            geojson=VALID_GEOJSON_FEATURE,
        )
        assert req.name == "Test Zone"

    def test_invalid_geojson_not_feature_type(self):
        with pytest.raises(ValidationError, match="geojson must be a GeoJSON Feature"):
            ZoneCreateRequest(
                name="Zone",
                geojson={"type": "FeatureCollection", "features": []},
            )

    def test_invalid_geojson_no_geometry(self):
        with pytest.raises(ValidationError):
            ZoneCreateRequest(
                name="Zone",
                geojson={"type": "Feature", "geometry": None, "properties": {}},
            )

    def test_invalid_geojson_bad_geometry_type(self):
        with pytest.raises(ValidationError, match="Invalid geometry type"):
            ZoneCreateRequest(
                name="Zone",
                geojson={
                    "type": "Feature",
                    "geometry": {"type": "NotAGeomType", "coordinates": []},
                    "properties": {}
                },
            )

    def test_valid_all_geometry_types(self):
        for geom_type in ["Point", "MultiPoint", "LineString", "MultiLineString",
                          "Polygon", "MultiPolygon", "GeometryCollection"]:
            if geom_type == "GeometryCollection":
                geom = {"type": geom_type, "geometries": []}
            else:
                geom = {"type": geom_type, "coordinates": []}
            ZoneCreateRequest(
                name="Zone",
                geojson={"type": "Feature", "geometry": geom, "properties": {}},
            )

    def test_tags_validation_strips_whitespace(self):
        req = ZoneCreateRequest(
            name="Zone",
            geojson=VALID_GEOJSON_FEATURE,
            tags=["  tag1  ", "tag2  "],
        )
        assert "tag1" in req.tags

    def test_tags_validation_removes_empty(self):
        req = ZoneCreateRequest(
            name="Zone",
            geojson=VALID_GEOJSON_FEATURE,
            tags=["", "  ", "valid"],
        )
        assert req.tags == ["valid"]

    def test_tags_truncated_to_50_chars(self):
        long_tag = "a" * 100
        req = ZoneCreateRequest(
            name="Zone",
            geojson=VALID_GEOJSON_FEATURE,
            tags=[long_tag],
        )
        assert len(req.tags[0]) == 50

    def test_too_many_tags_raises(self):
        with pytest.raises(ValidationError, match="Maximum 20 tags allowed"):
            ZoneCreateRequest(
                name="Zone",
                geojson=VALID_GEOJSON_FEATURE,
                tags=[f"tag{i}" for i in range(21)],
            )


# ---------------------------------------------------------------------------
# ZoneUpdateRequest
# ---------------------------------------------------------------------------

class TestZoneUpdateRequest:

    def test_all_none_is_valid(self):
        req = ZoneUpdateRequest()
        assert req.name is None
        assert req.geojson is None

    def test_valid_geojson_update(self):
        req = ZoneUpdateRequest(geojson=VALID_GEOJSON_FEATURE)
        assert req.geojson is not None

    def test_none_geojson_is_valid(self):
        req = ZoneUpdateRequest(geojson=None)
        assert req.geojson is None

    def test_invalid_geojson_not_feature(self):
        with pytest.raises(ValidationError, match="geojson must be a GeoJSON Feature"):
            ZoneUpdateRequest(
                geojson={"type": "FeatureCollection", "features": []}
            )

    def test_invalid_geojson_null_geometry(self):
        with pytest.raises(ValidationError):
            ZoneUpdateRequest(
                geojson={"type": "Feature", "geometry": None, "properties": {}}
            )

    def test_tags_validation_with_none(self):
        req = ZoneUpdateRequest(tags=None)
        assert req.tags is None

    def test_tags_strips_whitespace(self):
        req = ZoneUpdateRequest(tags=["  hello  "])
        assert "hello" in req.tags

    def test_tags_removes_empty(self):
        req = ZoneUpdateRequest(tags=["", "  ", "real"])
        assert req.tags == ["real"]

    def test_too_many_tags_raises(self):
        with pytest.raises(ValidationError, match="Maximum 20 tags allowed"):
            ZoneUpdateRequest(tags=[f"t{i}" for i in range(21)])


# ---------------------------------------------------------------------------
# MicrozoneCreateRequest
# ---------------------------------------------------------------------------

class TestMicrozoneCreateRequest:

    def test_valid_minimal(self):
        req = MicrozoneCreateRequest(
            name="Test MZ",
            geojson=VALID_GEOJSON_FEATURE,
        )
        assert req.name == "Test MZ"

    def test_invalid_geojson_not_feature(self):
        with pytest.raises(ValidationError, match="geojson must be a GeoJSON Feature"):
            MicrozoneCreateRequest(
                name="MZ",
                geojson={"type": "FeatureCollection", "features": []},
            )

    def test_invalid_geojson_no_geometry(self):
        with pytest.raises(ValidationError):
            MicrozoneCreateRequest(
                name="MZ",
                geojson={"type": "Feature", "geometry": None, "properties": {}},
            )

    def test_invalid_geojson_bad_geom_type(self):
        with pytest.raises(ValidationError, match="Invalid geometry type"):
            MicrozoneCreateRequest(
                name="MZ",
                geojson={
                    "type": "Feature",
                    "geometry": {"type": "BadType", "coordinates": []},
                    "properties": {}
                },
            )

    def test_valid_microzone_types(self):
        for mz_type in ["polygon", "circle", "rectangle", "marker", "polyline"]:
            MicrozoneCreateRequest(
                name="MZ",
                geojson=VALID_GEOJSON_FEATURE,
                microzone_type=mz_type,
            )

    def test_invalid_microzone_type(self):
        with pytest.raises(ValidationError):
            MicrozoneCreateRequest(
                name="MZ",
                geojson=VALID_GEOJSON_FEATURE,
                microzone_type="note",  # Not in allowed types
            )

    def test_tags_validation_too_many(self):
        with pytest.raises(ValidationError, match="Maximum 20 tags allowed"):
            MicrozoneCreateRequest(
                name="MZ",
                geojson=VALID_GEOJSON_FEATURE,
                tags=[f"t{i}" for i in range(21)],
            )

    def test_tags_strips_whitespace(self):
        req = MicrozoneCreateRequest(
            name="MZ",
            geojson=VALID_GEOJSON_FEATURE,
            tags=["  padded  "],
        )
        assert "padded" in req.tags


# ---------------------------------------------------------------------------
# MicrozoneUpdateRequest
# ---------------------------------------------------------------------------

class TestMicrozoneUpdateRequest:

    def test_all_none_is_valid(self):
        req = MicrozoneUpdateRequest()
        assert req.name is None

    def test_valid_geojson_update(self):
        req = MicrozoneUpdateRequest(geojson=VALID_GEOJSON_FEATURE)
        assert req.geojson is not None

    def test_none_geojson_passes_validator(self):
        req = MicrozoneUpdateRequest(geojson=None)
        assert req.geojson is None

    def test_invalid_geojson_not_feature(self):
        with pytest.raises(ValidationError, match="geojson must be a GeoJSON Feature"):
            MicrozoneUpdateRequest(
                geojson={"type": "FeatureCollection", "features": []}
            )

    def test_geojson_null_geometry_raises(self):
        with pytest.raises(ValidationError):
            MicrozoneUpdateRequest(
                geojson={"type": "Feature", "geometry": None, "properties": {}}
            )

    def test_tags_none_is_valid(self):
        req = MicrozoneUpdateRequest(tags=None)
        assert req.tags is None

    def test_tags_strips_whitespace(self):
        req = MicrozoneUpdateRequest(tags=["  tag  "])
        assert "tag" in req.tags

    def test_tags_removes_empty_strings(self):
        req = MicrozoneUpdateRequest(tags=["", "real"])
        assert req.tags == ["real"]

    def test_too_many_tags_raises(self):
        with pytest.raises(ValidationError, match="Maximum 20 tags allowed"):
            MicrozoneUpdateRequest(tags=[f"t{i}" for i in range(21)])
