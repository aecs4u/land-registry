"""
Map Controls System using Python/Folium
Defines map controls in Python and generates appropriate HTML/JavaScript
"""

from dataclasses import dataclass
from typing import List, Dict, Optional, Any, Union
import folium
from folium import plugins


@dataclass
class ControlButton:
    """Individual control button definition"""
    id: str
    title: str
    icon: str
    onclick: str
    enabled: bool = True
    tooltip: Optional[str] = None


@dataclass
class ControlSelect:
    """Dropdown/select control definition"""
    id: str
    title: str
    options: List[Dict[str, Any]]  # [{"value": "osm", "label": "OpenStreetMap"}, ...] # noqa
    onchange: str
    enabled: bool = True
    tooltip: Optional[str] = None
    default_value: Optional[str] = None


@dataclass
class ControlGroup:
    """Group of related control buttons and selects"""
    id: str
    title: str
    position: Dict[str, Any]  # e.g., {"top": "80px", "right": "10px"}
    controls: List[Union[ControlButton, ControlSelect]]
    draggable: bool = True


class MapControlsManager:
    """Manages map controls using Python definitions"""
    
    def __init__(self):
        self.control_groups = []
        self._define_control_groups()
    
    def _define_control_groups(self):
        """Define all control groups and their buttons"""
        
        # Navigation Controls
        navigation_controls = ControlGroup(
            id="navigationControls",
            title="Navigate",
            position={"top": "80px", "right": "10px"},
            controls=[
                ControlButton(
                    id="fitToPolygonsBtn",
                    title="Fit to All Polygons",
                    icon="🎯",
                    onclick="fitToPolygons()",
                    tooltip="Fit map to show all polygons"
                ),
                ControlButton(
                    id="fitSelectedBtn",
                    title="Fit to Selected Polygons",
                    icon="📍", 
                    onclick="fitToSelected()",
                    enabled=False,
                    tooltip="Fit map to selected polygons only"
                )
            ]
        )
        
        # Selection & Drawing Tools
        tools_controls = ControlGroup(
            id="selectionDrawingControls", 
            title="Tools",
            position={"top": "240px", "right": "10px"},
            controls=[
                ControlButton(
                    id="polygonSelectionBtn",
                    title="Toggle Polygon Selection Mode",
                    icon="✏️",
                    onclick="togglePolygonSelectionMode()",
                    tooltip="Enable/disable polygon selection"
                )
            ]
        )
        
        # View & Display Controls  
        display_controls = ControlGroup(
            id="viewDisplayControls",
            title="Display", 
            position={"top": "400px", "right": "10px"},
            controls=[
                ControlButton(
                    id="legendToggleBtn",
                    title="Toggle Legend",
                    icon="📋",
                    onclick="toggleLegend()",
                    tooltip="Show/hide map legend"
                ),
                ControlButton(
                    id="selectionInfoToggleBtn",
                    title="Toggle Selection Info",
                    icon="ℹ️", 
                    onclick="toggleSelectionInfo()",
                    tooltip="Show/hide selection information"
                ),
                ControlButton(
                    id="togglePolygonsBtn",
                    title="Toggle Polygons Visibility",
                    icon="👁️",
                    onclick="togglePolygonsVisibility()", 
                    tooltip="Show/hide all polygons"
                )
            ]
        )
        
        # Data Operations
        data_controls = ControlGroup(
            id="dataOperationsControls",
            title="Data",
            position={"bottom": "80px", "right": "10px"},
            controls=[
                ControlButton(
                    id="saveDrawings",
                    title="Save Drawings as JSON",
                    icon="💾",
                    onclick="saveDrawingsToJSON()", 
                    tooltip="Export drawn polygons to JSON"
                ),
                ControlButton(
                    id="loadDrawings",
                    title="Load Drawings",
                    icon="📁",
                    onclick="triggerLoadDrawings()",
                    tooltip="Import polygons from JSON file"
                ),
                ControlButton(
                    id="exportSelectionBtn", 
                    title="Export Selection",
                    icon="📤",
                    onclick="exportSelection()",
                    enabled=False,
                    tooltip="Export selected polygons"
                ),
                ControlButton(
                    id="importSelectionBtn",
                    title="Import Selection",
                    icon="📥", 
                    onclick="importSelection()",
                    tooltip="Import polygon selection"
                )
            ]
        )
        
        self.control_groups = [
            navigation_controls,
            tools_controls, 
            display_controls,
            data_controls
        ]
    
    def generate_html(self) -> str:
        """Generate HTML for all control groups"""
        html_parts = []
        
        for group in self.control_groups:
            # Generate group HTML
            position_style = "; ".join([f"{k}: {v}" for k, v in group.position.items()])
            
            group_html = f'''
                <!-- {group.title} Controls -->
                <div class="map-controls" id="{group.id}" style="{position_style};
">
                    <div class="control-group-header">{group.title}</div>
            '''
            
            # Add controls (buttons and selects)
            for i, control in enumerate(group.controls):
                # Add separator for certain controls (drawing tools)
                if hasattr(control, 'id'):
                    if control.id == "selectionInfoToggleBtn" and i > 0:
                        group_html += '                    <div class="control-separator"></div>\n'
                    elif control.id == "loadDrawings" and i > 0:
                        group_html += '                    <div class="control-separator"></div>\n'
                
                # Generate HTML based on control type
                if isinstance(control, ControlButton):
                    # Button HTML
                    disabled_attr = ' disabled' if not control.enabled else ''
                    tooltip_attr = f' title="{control.tooltip}"' if control.tooltip else f' title="{control.title}"'
                    group_html += f'                    <button onclick="{control.onclick}" id="{control.id}"{tooltip_attr}{disabled_attr}>{control.icon}</button>\n'
                    
                elif isinstance(control, ControlSelect):
                    # Select dropdown HTML
                    disabled_attr = ' disabled' if not control.enabled else ''
                    tooltip_attr = f' title="{control.tooltip}"' if control.tooltip else f' title="{control.title}"'
                    group_html += f'                    <select id="{control.id}" onchange="{control.onchange}"{tooltip_attr}{disabled_attr}>'
                    
                    for option in control.options:
                        selected_attr = ' selected' if control.default_value and option["value"] == control.default_value else ''
                        group_html += f'                        <option value="{option["value"]}"{selected_attr}>{option["label"]}</option>\n'
                    group_html += '                    </select>'
            
            group_html += '                </div>\n'
            html_parts.append(group_html)
        
        return '\n'.join(html_parts)
    
    def generate_folium_controls(self, folium_map: folium.Map) -> folium.Map:
        """Add Folium-based controls to the map where possible"""
        
        # Add fullscreen control
        plugins.Fullscreen().add_to(folium_map)
        
        # Add measure control for drawing/measuring
        plugins.MeasureControl().add_to(folium_map)
        
        # Add draw control for polygon drawing
        draw = plugins.Draw(
            export=True,
            position='topleft',
            draw_options={
                'polyline': False,
                'polygon': True,
                'circle': True,
                'rectangle': True,
                'marker': False,
                'circlemarker': False,
            },
            edit_options={'edit': True}
        )
        draw.add_to(folium_map)
        
        # Add locate control to find user's location
        plugins.LocateControl().add_to(folium_map)
        
        # Add layer control for basemap switching
        folium.LayerControl().add_to(folium_map)
        
        return folium_map
    
    def generate_javascript(self) -> str:
        """Generate JavaScript initialization code for the controls"""
        js_code = '''
        // Python-generated control initialization
        function initializePythonControls() {
            console.log('Initializing Python-generated controls');
            
            // Initialize draggable functionality
            initializeDraggableControls();
            
            // Initialize button states
            initializeButtonStates();
        }
        
        function initializeButtonStates() {
            // Set initial button states based on Python definitions
            const polygonBtn = document.getElementById('polygonSelectionBtn');
            if (polygonBtn && polygonSelectionMode) {
                polygonBtn.style.background = '#007cba';
                polygonBtn.style.color = 'white';
            }
        }
        
        // Call initialization after DOM is ready
        document.addEventListener('DOMContentLoaded', initializePythonControls);
        
        // Function to sync control state with Python backend
        async function syncControlState(controlId, enabled) {
            try {
                const response = await fetch('/update-control-state/', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        control_id: controlId,
                        enabled: enabled
                    })
                });
                return await response.json();
            } catch (error) {
                console.error('Error syncing control state:', error);
            }
        }
        
        // Function to load current control states from Python
        async function loadControlStates() {
            try {
                const response = await fetch('/get-controls/');
                const controlsData = await response.json();
                
                // Update UI based on Python state
                controlsData.groups.forEach(group => {
                    group.buttons.forEach(button => {
                        const element = document.getElementById(button.id);
                        if (element) {
                            element.disabled = !button.enabled;
                            if (button.tooltip) {
                                element.title = button.tooltip;
                            }
                        }
                    });
                });
                
                console.log('Control states loaded from Python backend');
            } catch (error) {
                console.error('Error loading control states:', error);
            }
        }
        '''
        return js_code
    
    def get_control_by_id(self, control_id: str) -> Optional[Union[ControlButton, ControlSelect]]:
        """Get a specific control by ID"""
        for group in self.control_groups:
            for control in group.controls:
                if control.id == control_id:
                    return control
        return None
    
    def update_control_state(self, control_id: str, enabled: bool) -> bool:
        """Update the enabled state of a control"""
        control = self.get_control_by_id(control_id)
        if control:
            control.enabled = enabled
            return True
        return False


# Global instance
map_controls = MapControlsManager()
