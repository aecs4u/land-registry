"""
Unit tests for shared_state.py (SharedState) and zone_rules.py.

shared_state: thread-safe reactive state, all methods exercised.
zone_rules: pure functions for area computation and geometry metrics.
"""

import pandas as pd
import pytest
from shapely.geometry import MultiPolygon, Point, Polygon

from land_registry.shared_state import SharedState
from land_registry.zone_rules import (
    MICROZONE_WARNING_THRESHOLD_KM2,
    _geodesic_area_sqm,
    area_sqm_to_km2,
    geometry_metrics_from_geojson,
    is_large_microzone,
)


# ===========================================================================
# SharedState
# ===========================================================================

@pytest.fixture
def state():
    df = pd.DataFrame({"id": [1, 2, 3], "region": ["LAZIO", "LAZIO", "LOMBARDIA"], "province": ["RM", "VT", "MI"]})
    return SharedState(base_df=df)


class TestSharedStateSetFilters:

    def test_set_region(self, state):
        state.set_filters(region="LAZIO")
        assert state.region == "LAZIO"

    def test_set_province(self, state):
        state.set_filters(province="RM")
        assert state.province == "RM"

    def test_set_both(self, state):
        state.set_filters(region="LOMBARDIA", province="MI")
        assert state.region == "LOMBARDIA"
        assert state.province == "MI"

    def test_increments_version(self, state):
        initial_version = state.version
        state.set_filters(region="LAZIO")
        assert state.version == initial_version + 1

    def test_none_region_not_overwritten(self, state):
        state.set_filters(region="LAZIO")
        state.set_filters(region=None, province="RM")
        assert state.region == "LAZIO"  # Not overwritten

    def test_none_province_not_overwritten(self, state):
        state.set_filters(province="RM")
        state.set_filters(region="LAZIO", province=None)
        assert state.province == "RM"


class TestSharedStateUpdateDataframe:

    def test_replaces_dataframe(self, state):
        new_df = pd.DataFrame({"id": [99]})
        state.update_dataframe(new_df)
        assert len(state.base_df) == 1
        assert state.base_df.iloc[0]["id"] == 99

    def test_resets_selection(self, state):
        state.set_selection([1, 2])
        new_df = pd.DataFrame({"id": [99]})
        state.update_dataframe(new_df)
        assert state.selection == []

    def test_increments_version(self, state):
        initial_version = state.version
        state.update_dataframe(pd.DataFrame())
        assert state.version == initial_version + 1


class TestSharedStateSelection:

    def test_get_selection_empty(self, state):
        result = state.get_selection()
        assert result == []

    def test_set_then_get_selection(self, state):
        state.set_selection([10, 20, 30])
        result = state.get_selection()
        assert result == [10, 20, 30]

    def test_get_selection_returns_copy(self, state):
        state.set_selection([1, 2])
        sel = state.get_selection()
        sel.append(99)
        assert state.get_selection() == [1, 2]  # Not modified


class TestSharedStateFilteredDf:

    def test_no_filters_returns_all(self, state):
        df = state.filtered_df()
        assert len(df) == 3

    def test_region_filter(self, state):
        state.set_filters(region="LAZIO")
        df = state.filtered_df()
        assert len(df) == 2
        assert all(df["region"] == "LAZIO")

    def test_province_filter(self, state):
        state.set_filters(province="RM")
        df = state.filtered_df()
        assert len(df) == 1
        assert df.iloc[0]["province"] == "RM"

    def test_region_and_province_filter(self, state):
        state.set_filters(region="LAZIO", province="RM")
        df = state.filtered_df()
        assert len(df) == 1

    def test_unknown_region_returns_empty(self, state):
        state.set_filters(region="SICILIA")
        df = state.filtered_df()
        assert len(df) == 0

    def test_filter_on_missing_column(self):
        """Filters on columns that don't exist are skipped."""
        df = pd.DataFrame({"id": [1, 2]})  # no region/province columns
        s = SharedState(base_df=df)
        s.set_filters(region="LAZIO", province="RM")
        result = s.filtered_df()
        assert len(result) == 2  # No filter applied

    def test_returns_copy(self, state):
        df = state.filtered_df()
        df["new_col"] = 999
        assert "new_col" not in state.filtered_df().columns


# ===========================================================================
# zone_rules
# ===========================================================================

class TestAreaSqmToKm2:

    def test_none_returns_none(self):
        assert area_sqm_to_km2(None) is None

    def test_zero(self):
        assert area_sqm_to_km2(0) == 0.0

    def test_one_million(self):
        assert area_sqm_to_km2(1_000_000) == pytest.approx(1.0)

    def test_half_million(self):
        assert area_sqm_to_km2(500_000) == pytest.approx(0.5)

    def test_string_number(self):
        """String representation of a number should be converted."""
        assert area_sqm_to_km2("1000000") == pytest.approx(1.0)

    def test_invalid_string_returns_none(self):
        assert area_sqm_to_km2("not a number") is None

    def test_invalid_type_returns_none(self):
        assert area_sqm_to_km2([1, 2, 3]) is None


class TestIsLargeMicrozone:

    def test_none_area_returns_false(self):
        assert is_large_microzone(None) is False

    def test_small_area(self):
        # 100_000 sqm = 0.1 km2 < 0.3 threshold
        assert is_large_microzone(100_000) is False

    def test_large_area(self):
        # 500_000 sqm = 0.5 km2 > 0.3 threshold
        assert is_large_microzone(500_000) is True

    def test_exactly_at_threshold(self):
        # 300_000 sqm = 0.3 km2, NOT > threshold (strict >)
        assert is_large_microzone(300_000) is False

    def test_custom_threshold(self):
        assert is_large_microzone(200_000, threshold_km2=0.1) is True


class TestGeodesicAreaSqm:

    def test_simple_polygon(self):
        """A 1° x 1° box near Rome should have a reasonable area."""
        poly = Polygon([(12.0, 41.0), (13.0, 41.0), (13.0, 42.0), (12.0, 42.0)])
        area = _geodesic_area_sqm(poly)
        assert area is not None
        assert area > 0
        # 1° x 1° near lat 41° ≈ 7000-8000 km2
        assert 5e9 < area < 1e10

    def test_multipolygon(self):
        poly1 = Polygon([(12.0, 41.0), (12.5, 41.0), (12.5, 41.5), (12.0, 41.5)])
        poly2 = Polygon([(13.0, 42.0), (13.5, 42.0), (13.5, 42.5), (13.0, 42.5)])
        mp = MultiPolygon([poly1, poly2])
        area = _geodesic_area_sqm(mp)
        assert area is not None
        assert area > 0

    def test_point_returns_zero(self):
        pt = Point(12.5, 41.5)
        area = _geodesic_area_sqm(pt)
        assert area == 0.0

    def test_geometry_collection(self):
        """GeometryCollection with polygons sums their areas."""
        from shapely.geometry import GeometryCollection
        poly = Polygon([(12.0, 41.0), (13.0, 41.0), (13.0, 42.0), (12.0, 42.0)])
        pt = Point(12.5, 41.5)
        gc = GeometryCollection([poly, pt])
        area = _geodesic_area_sqm(gc)
        assert area is not None
        assert area > 0


class TestGeometryMetricsFromGeojson:

    def test_none_input_returns_none_tuple(self):
        result = geometry_metrics_from_geojson(None)
        assert result == (None, None, None)

    def test_non_mapping_returns_none_tuple(self):
        result = geometry_metrics_from_geojson("not a dict")
        assert result == (None, None, None)

    def test_list_returns_none_tuple(self):
        result = geometry_metrics_from_geojson([1, 2, 3])
        assert result == (None, None, None)

    def test_missing_geometry_key(self):
        result = geometry_metrics_from_geojson({"type": "Feature", "properties": {}})
        assert result == (None, None, None)

    def test_null_geometry(self):
        result = geometry_metrics_from_geojson({
            "type": "Feature",
            "geometry": None,
            "properties": {}
        })
        assert result == (None, None, None)

    def test_geometry_not_mapping(self):
        result = geometry_metrics_from_geojson({
            "type": "Feature",
            "geometry": "not a dict",
            "properties": {}
        })
        assert result == (None, None, None)

    def test_valid_polygon(self):
        feature = {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[12.0, 41.0], [13.0, 41.0], [13.0, 42.0], [12.0, 42.0], [12.0, 41.0]]]
            },
            "properties": {}
        }
        area_sqm, lat, lng = geometry_metrics_from_geojson(feature)
        assert area_sqm is not None
        assert area_sqm > 0
        assert lat is not None
        assert lng is not None
        # Centroid should be near (41.5, 12.5)
        assert lat == pytest.approx(41.5, abs=0.1)
        assert lng == pytest.approx(12.5, abs=0.1)

    def test_invalid_geometry_returns_none(self):
        feature = {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": "invalid"
            },
            "properties": {}
        }
        result = geometry_metrics_from_geojson(feature)
        assert result == (None, None, None)
