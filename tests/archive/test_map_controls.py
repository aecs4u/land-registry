from unittest.mock import Mock
from land_registry.map_controls import (
    ControlButton, ControlSelect, ControlGroup, MapControlsManager
)


class TestControlButton:
    """Tests for ControlButton dataclass."""
    
    def test_control_button_creation(self):
        """Test creating a control button."""
        button = ControlButton(
            id="testBtn",
            title="Test Button",
            icon="🔘",
            onclick="testFunction()",
            enabled=True,
            tooltip="Test tooltip"
        )
        
        assert button.id == "testBtn"
        assert button.title == "Test Button"
        assert button.icon == "🔘"
        assert button.onclick == "testFunction()"
        assert button.enabled is True
        assert button.tooltip == "Test tooltip"
    
    def test_control_button_default_values(self):
        """Test control button with default values."""
        button = ControlButton(
            id="testBtn",
            title="Test Button",
            icon="🔘",
            onclick="testFunction()"
        )
        
        assert button.enabled is True
        assert button.tooltip is None


class TestControlSelect:
    """Tests for ControlSelect dataclass."""
    
    def test_control_select_creation(self):
        """Test creating a control select."""
        select = ControlSelect(
            id="testSelect",
            title="Test Select",
            options=[
                {"value": "opt1", "label": "Option 1"},
                {"value": "opt2", "label": "Option 2"}
            ],
            onchange="handleSelectChange()",
            enabled=True,
            tooltip="Test select tooltip",
            default_value="opt1"
        )
        
        assert select.id == "testSelect"
        assert select.title == "Test Select"
        assert len(select.options) == 2
        assert select.onchange == "handleSelectChange()"
        assert select.enabled is True
        assert select.tooltip == "Test select tooltip"
        assert select.default_value == "opt1"
    
    def test_control_select_default_values(self):
        """Test control select with default values."""
        select = ControlSelect(
            id="testSelect",
            title="Test Select",
            options=[],
            onchange="handleChange()"
        )
        
        assert select.enabled is True
        assert select.tooltip is None
        assert select.default_value is None


class TestControlGroup:
    """Tests for ControlGroup dataclass."""
    
    def test_control_group_creation(self):
        """Test creating a control group."""
        button = ControlButton(
            id="btn1",
            title="Button 1",
            icon="🔘",
            onclick="func1()"
        )
        
        select = ControlSelect(
            id="sel1",
            title="Select 1",
            options=[{"value": "val1", "label": "Label 1"}],
            onchange="change1()"
        )
        
        group = ControlGroup(
            id="testGroup",
            title="Test Group",
            position={"top": "10px", "right": "10px"},
            controls=[button, select],
            draggable=True
        )
        
        assert group.id == "testGroup"
        assert group.title == "Test Group"
        assert group.position == {"top": "10px", "right": "10px"}
        assert len(group.controls) == 2
        assert group.draggable is True
    
    def test_control_group_default_draggable(self):
        """Test control group with default draggable value."""
        group = ControlGroup(
            id="testGroup",
            title="Test Group",
            position={"top": "10px", "right": "10px"},
            controls=[]
        )
        
        assert group.draggable is True


class TestMapControlsManager:
    """Tests for MapControlsManager class."""
    
    def test_map_controls_manager_initialization(self):
        """Test MapControlsManager initialization."""
        manager = MapControlsManager()
        
        assert hasattr(manager, 'control_groups')
        assert isinstance(manager.control_groups, list)
        assert len(manager.control_groups) > 0
    
    def test_generate_html(self):
        """Test HTML generation for controls."""
        manager = MapControlsManager()
        html = manager.generate_html()
        
        assert isinstance(html, str)
        assert len(html) > 0
        # Check for basic HTML structure
        assert '<div' in html
        assert 'class=' in html
    
    def test_generate_javascript(self):
        """Test JavaScript generation for controls."""
        manager = MapControlsManager()
        js = manager.generate_javascript()
        
        assert isinstance(js, str)
        assert len(js) > 0
        # Check for basic JavaScript structure
        assert 'function' in js or 'const' in js or 'var' in js
    
    def test_update_control_state_success(self):
        """Test successful control state update."""
        manager = MapControlsManager()
        
        # Find a control that exists
        control_id = None
        for group in manager.control_groups:
            if group.controls:
                control_id = group.controls[0].id
                break
        
        if control_id:
            result = manager.update_control_state(control_id, False)
            assert result is True
            
            # Verify the state was updated
            for group in manager.control_groups:
                for control in group.controls:
                    if control.id == control_id:
                        assert control.enabled is False
                        break
    
    def test_update_control_state_not_found(self):
        """Test control state update for non-existent control."""
        manager = MapControlsManager()
        
        result = manager.update_control_state("nonexistent_control", True)
        assert result is False
    
    def test_get_control_by_id(self):
        """Test finding control by ID."""
        manager = MapControlsManager()

        # Get first control ID
        control_id = None
        expected_control = None
        for group in manager.control_groups:
            if group.controls:
                expected_control = group.controls[0]
                control_id = expected_control.id
                break

        if control_id:
            found_control = manager.get_control_by_id(control_id)
            assert found_control == expected_control
    
    def test_get_control_by_id_not_found(self):
        """Test finding non-existent control by ID."""
        manager = MapControlsManager()

        found_control = manager.get_control_by_id("nonexistent_control")
        assert found_control is None
    
    def test_generate_folium_controls(self):
        """Test generating Folium controls."""
        manager = MapControlsManager()
        mock_map = Mock()
        
        # Test that the method exists and can be called
        result = manager.generate_folium_controls(mock_map)
        
        # Should return the map object
        assert result == mock_map
    
    def test_control_groups_structure(self):
        """Test that control groups have the expected structure."""
        manager = MapControlsManager()
        
        assert len(manager.control_groups) > 0
        
        for group in manager.control_groups:
            # Each group should have required attributes
            assert hasattr(group, 'id')
            assert hasattr(group, 'title')
            assert hasattr(group, 'position')
            assert hasattr(group, 'controls')
            assert hasattr(group, 'draggable')
            
            # Position should be a dict
            assert isinstance(group.position, dict)
            
            # Controls should be a list
            assert isinstance(group.controls, list)
            
            # Each control should be either ControlButton or ControlSelect
            for control in group.controls:
                assert isinstance(control, (ControlButton, ControlSelect))
                assert hasattr(control, 'id')
                assert hasattr(control, 'title')
                assert hasattr(control, 'enabled')
    
    def test_control_button_properties(self):
        """Test that control buttons have required properties."""
        manager = MapControlsManager()
        
        for group in manager.control_groups:
            for control in group.controls:
                if isinstance(control, ControlButton):
                    assert hasattr(control, 'icon')
                    assert hasattr(control, 'onclick')
                    assert isinstance(control.icon, str)
                    assert isinstance(control.onclick, str)
    
    def test_control_select_properties(self):
        """Test that control selects have required properties."""
        manager = MapControlsManager()
        
        for group in manager.control_groups:
            for control in group.controls:
                if isinstance(control, ControlSelect):
                    assert hasattr(control, 'options')
                    assert hasattr(control, 'onchange')
                    assert isinstance(control.options, list)
                    assert isinstance(control.onchange, str)
                    
                    # Each option should be a dict with value and label
                    for option in control.options:
                        assert isinstance(option, dict)
                        assert 'value' in option
                        assert 'label' in option


class TestMapControlsIntegration:
    """Integration tests for map controls functionality."""
    
    def test_html_javascript_integration(self):
        """Test that HTML and JavaScript work together."""
        manager = MapControlsManager()
        
        html = manager.generate_html()
        js = manager.generate_javascript()
        
        # Basic checks
        assert isinstance(html, str)
        assert isinstance(js, str)
        assert len(html) > 0
        assert len(js) > 0
        
        # Look for control IDs in both HTML and JavaScript
        control_ids = []
        for group in manager.control_groups:
            for control in group.controls:
                control_ids.append(control.id)
        
        # At least some control IDs should appear in HTML
        html_has_ids = any(control_id in html for control_id in control_ids)
        assert html_has_ids
    
    def test_state_persistence(self):
        """Test that control state changes persist."""
        manager = MapControlsManager()
        
        # Find a control to test with
        test_control = None
        for group in manager.control_groups:
            if group.controls:
                test_control = group.controls[0]
                break
        
        if test_control:
            original_state = test_control.enabled
            new_state = not original_state
            
            # Update state
            success = manager.update_control_state(test_control.id, new_state)
            assert success
            
            # Verify state changed
            assert test_control.enabled == new_state
            
            # Restore original state
            manager.update_control_state(test_control.id, original_state)
            assert test_control.enabled == original_state