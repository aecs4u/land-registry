"""
Unit tests for land_registry/map_controls.py.

Tests MapControlsManager, ControlButton, ControlSelect, ControlGroup.
"""

import pytest
import folium

from land_registry.map_controls import (
    ControlButton,
    ControlGroup,
    ControlSelect,
    MapControlsManager,
    map_controls,
)


# ---------------------------------------------------------------------------
# Dataclass instantiation
# ---------------------------------------------------------------------------

class TestControlButton:

    def test_basic_button(self):
        btn = ControlButton(id="btn1", title="My Button", icon="🎯", onclick="doThing()")
        assert btn.id == "btn1"
        assert btn.title == "My Button"
        assert btn.icon == "🎯"
        assert btn.onclick == "doThing()"
        assert btn.enabled is True
        assert btn.tooltip is None

    def test_disabled_button(self):
        btn = ControlButton(id="b", title="T", icon="X", onclick="f()", enabled=False)
        assert btn.enabled is False

    def test_button_with_tooltip(self):
        btn = ControlButton(id="b", title="T", icon="X", onclick="f()", tooltip="Tip text")
        assert btn.tooltip == "Tip text"


class TestControlSelect:

    def test_basic_select(self):
        opts = [{"value": "a", "label": "A"}, {"value": "b", "label": "B"}]
        sel = ControlSelect(id="sel1", title="My Select", options=opts, onchange="onChange()")
        assert sel.id == "sel1"
        assert len(sel.options) == 2
        assert sel.enabled is True
        assert sel.default_value is None

    def test_select_with_default(self):
        opts = [{"value": "x", "label": "X"}]
        sel = ControlSelect(id="s", title="T", options=opts, onchange="f()", default_value="x")
        assert sel.default_value == "x"


class TestControlGroup:

    def test_basic_group(self):
        btn = ControlButton(id="b", title="T", icon="I", onclick="f()")
        group = ControlGroup(
            id="g1",
            title="Group",
            position={"top": "10px", "right": "5px"},
            controls=[btn],
        )
        assert group.id == "g1"
        assert group.draggable is True
        assert len(group.controls) == 1


# ---------------------------------------------------------------------------
# MapControlsManager instantiation
# ---------------------------------------------------------------------------

class TestMapControlsManagerInit:

    def test_creates_control_groups(self):
        mgr = MapControlsManager()
        assert len(mgr.control_groups) == 4

    def test_control_group_ids_present(self):
        mgr = MapControlsManager()
        ids = [g.id for g in mgr.control_groups]
        assert "navigationControls" in ids
        assert "selectionDrawingControls" in ids
        assert "viewDisplayControls" in ids
        assert "dataOperationsControls" in ids


# ---------------------------------------------------------------------------
# generate_html
# ---------------------------------------------------------------------------

class TestGenerateHtml:

    def test_returns_string(self):
        mgr = MapControlsManager()
        html = mgr.generate_html()
        assert isinstance(html, str)
        assert len(html) > 0

    def test_contains_button_ids(self):
        mgr = MapControlsManager()
        html = mgr.generate_html()
        assert "fitToPolygonsBtn" in html
        assert "fitSelectedBtn" in html
        assert "legendToggleBtn" in html

    def test_disabled_button_has_disabled_attr(self):
        mgr = MapControlsManager()
        html = mgr.generate_html()
        # fitSelectedBtn is enabled=False
        assert "fitSelectedBtn" in html
        # The disabled attribute should appear near the disabled button
        assert " disabled" in html

    def test_separators_inserted(self):
        mgr = MapControlsManager()
        html = mgr.generate_html()
        assert "control-separator" in html

    def test_select_control_rendered(self):
        """A ControlSelect is rendered as a <select> element."""
        mgr = MapControlsManager()
        opts = [{"value": "osm", "label": "OpenStreetMap"}, {"value": "sat", "label": "Satellite"}]
        sel = ControlSelect(
            id="basemapSelect",
            title="Basemap",
            options=opts,
            onchange="changeBasemap()",
            default_value="osm",
        )
        group = ControlGroup(
            id="testGroup",
            title="Test",
            position={"top": "10px"},
            controls=[sel],
        )
        mgr.control_groups = [group]
        html = mgr.generate_html()
        assert "<select" in html
        assert "basemapSelect" in html
        assert "OpenStreetMap" in html
        assert 'selected' in html  # default_value is set


# ---------------------------------------------------------------------------
# get_control_by_id
# ---------------------------------------------------------------------------

class TestGetControlById:

    def test_existing_control_returned(self):
        mgr = MapControlsManager()
        ctrl = mgr.get_control_by_id("fitToPolygonsBtn")
        assert ctrl is not None
        assert ctrl.id == "fitToPolygonsBtn"

    def test_nonexistent_control_returns_none(self):
        mgr = MapControlsManager()
        result = mgr.get_control_by_id("nonExistentId")
        assert result is None

    def test_disabled_control_can_be_retrieved(self):
        mgr = MapControlsManager()
        ctrl = mgr.get_control_by_id("fitSelectedBtn")
        assert ctrl is not None
        assert ctrl.enabled is False


# ---------------------------------------------------------------------------
# update_control_state
# ---------------------------------------------------------------------------

class TestUpdateControlState:

    def test_enable_disabled_control(self):
        mgr = MapControlsManager()
        # fitSelectedBtn starts disabled
        result = mgr.update_control_state("fitSelectedBtn", True)
        assert result is True
        ctrl = mgr.get_control_by_id("fitSelectedBtn")
        assert ctrl.enabled is True

    def test_disable_enabled_control(self):
        mgr = MapControlsManager()
        result = mgr.update_control_state("fitToPolygonsBtn", False)
        assert result is True
        ctrl = mgr.get_control_by_id("fitToPolygonsBtn")
        assert ctrl.enabled is False

    def test_nonexistent_control_returns_false(self):
        mgr = MapControlsManager()
        result = mgr.update_control_state("bogusId", True)
        assert result is False


# ---------------------------------------------------------------------------
# generate_javascript
# ---------------------------------------------------------------------------

class TestGenerateJavascript:

    def test_returns_string(self):
        mgr = MapControlsManager()
        js = mgr.generate_javascript()
        assert isinstance(js, str)

    def test_contains_init_function(self):
        mgr = MapControlsManager()
        js = mgr.generate_javascript()
        assert "initializePythonControls" in js

    def test_contains_sync_function(self):
        mgr = MapControlsManager()
        js = mgr.generate_javascript()
        assert "syncControlState" in js


# ---------------------------------------------------------------------------
# generate_folium_controls
# ---------------------------------------------------------------------------

class TestGenerateFoliumControls:

    def test_returns_folium_map(self):
        mgr = MapControlsManager()
        m = folium.Map(location=[41.9, 12.5], zoom_start=6)
        result = mgr.generate_folium_controls(m)
        assert result is m  # Returns the same map object


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

class TestModuleSingleton:

    def test_map_controls_is_instance(self):
        assert isinstance(map_controls, MapControlsManager)

    def test_map_controls_has_groups(self):
        assert len(map_controls.control_groups) == 4
