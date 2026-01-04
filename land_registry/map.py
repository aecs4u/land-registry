
from branca.element import Template, MacroElement
import colorsys
import folium
from folium.plugins import (
    Draw, Fullscreen, MeasureControl
)
from folium.plugins.treelayercontrol import TreeLayerControl
import geopandas as gpd
import logging
import pandas as pd
from pathlib import Path
from pydantic_settings import BaseSettings
import random
from shapely.geometry import Point
import tempfile
from typing import List, Dict, Any, Optional, Union
import zipfile

from land_registry.config import map_controls_settings, app_settings

# Configure logger
logger = logging.getLogger(__name__)


class ControlButton(BaseSettings):
    """Individual control button definition"""
    id: str
    title: str
    icon: str
    onclick: str
    enabled: bool = True
    tooltip: Optional[str] = None


class ControlSelect(BaseSettings):
    """Dropdown/select control definition"""
    id: str
    title: str
    options: List[Dict[str, Any]]  # [{"value": "osm", "label": "OpenStreetMap"}, ...]
    onchange: str
    enabled: bool = True
    tooltip: Optional[str] = None
    default_value: Optional[str] = None


class ControlGroup(BaseSettings):
    """Group of related control buttons and selects"""
    id: str
    title: str
    position: Dict[str, Any]  # e.g., {"top": "80px", "right": "10px"}
    controls: List[Union[ControlButton, ControlSelect]]
    draggable: bool = True


class ExportControl(MacroElement):
    """Custom Folium control for exporting drawn features as GeoJSON"""
    
    _template = Template("""
        {% macro script(this, kwargs) %}
            L.Control.Export = L.Control.extend({
                options: {
                    position: 'bottomright'
                },
                
                onAdd: function (map) {
                    var container = L.DomUtil.create('div', 'leaflet-bar leaflet-control');
                    var button = L.DomUtil.create('a', 'leaflet-control-export', container);
                    
                    button.href = '#';
                    button.title = 'Export drawn features as GeoJSON';
                    button.innerHTML = '📤';
                    button.style.fontSize = '18px';
                    button.style.display = 'flex';
                    button.style.alignItems = 'center';
                    button.style.justifyContent = 'center';
                    button.style.width = '30px';
                    button.style.height = '30px';
                    button.style.textDecoration = 'none';
                    button.style.color = '#000';
                    button.style.backgroundColor = 'white';
                    
                    L.DomEvent.on(button, 'click', function (e) {
                        L.DomEvent.stop(e);
                        this.exportDrawnFeatures(map);
                    }, this);
                    
                    L.DomEvent.disableClickPropagation(container);
                    return container;
                },
                
                exportDrawnFeatures: function(map) {
                    // Find drawn features by checking all layers
                    var drawnFeatures = [];
                    
                    // Iterate through all layers on the map
                    map.eachLayer(function(layer) {
                        // Skip tile layers and other base layers
                        if (layer instanceof L.TileLayer) {
                            return;
                        }
                        
                        // Check if this is a drawn shape (check by constructor name)
                        var isDrawnShape = layer instanceof L.Polygon || 
                                          layer instanceof L.Polyline || 
                                          layer instanceof L.Circle || 
                                          layer instanceof L.Rectangle || 
                                          layer instanceof L.Marker || 
                                          layer instanceof L.CircleMarker;
                        
                        // Also check if it's in a FeatureGroup (Draw plugin uses FeatureGroups)
                        if (layer instanceof L.FeatureGroup || layer instanceof L.LayerGroup) {
                            // Check if this is the drawnItems FeatureGroup from Draw plugin
                            layer.eachLayer(function(subLayer) {
                                if (subLayer.toGeoJSON) {
                                    try {
                                        var geojson = subLayer.toGeoJSON();
                                        drawnFeatures.push(geojson);
                                        console.log('Found drawn feature:', geojson);
                                    } catch (e) {
                                        console.warn('Could not convert layer to GeoJSON:', e);
                                    }
                                }
                            });
                        }
                        // Direct drawn shapes (not in FeatureGroup)
                        else if (isDrawnShape && layer.toGeoJSON) {
                            try {
                                var geojson = layer.toGeoJSON();
                                drawnFeatures.push(geojson);
                                console.log('Found drawn feature:', geojson);
                            } catch (e) {
                                console.warn('Could not convert layer to GeoJSON:', e);
                            }
                        }
                    });
                    
                    console.log('Total drawn features found:', drawnFeatures.length);
                    
                    if (drawnFeatures.length === 0) {
                        alert('No drawn features to export. Please draw some shapes first using the draw tools.');
                        return;
                    }
                    
                    var geojson = {
                        type: 'FeatureCollection',
                        features: drawnFeatures
                    };
                    
                    // Create download
                    var dataStr = JSON.stringify(geojson, null, 2);
                    var dataBlob = new Blob([dataStr], {type: 'application/json'});
                    var url = URL.createObjectURL(dataBlob);
                    
                    var link = document.createElement('a');
                    link.href = url;
                    link.download = 'drawn_features_' + new Date().toISOString().slice(0, 10) + '.geojson';
                    document.body.appendChild(link);
                    link.click();
                    document.body.removeChild(link);
                    URL.revokeObjectURL(url);
                    
                    alert('Exported ' + drawnFeatures.length + ' feature(s) as GeoJSON');
                }
            });
            
            new L.Control.Export().addTo({{ this._parent.get_name() }});
        {% endmacro %}
    """)
    
    def __init__(self):
        super(ExportControl, self).__init__()
        self._name = 'ExportControl'


class CustomZoomControl(MacroElement):
    """Custom Folium control for advanced zoom operations (Fit All, Fit Selected, Box Zoom, Reset)"""

    _template = Template("""
        {% macro script(this, kwargs) %}
            L.Control.CustomZoom = L.Control.extend({
                options: {
                    position: 'topleft'
                },

                onAdd: function (map) {
                    var container = L.DomUtil.create('div', 'leaflet-bar leaflet-control leaflet-control-custom-zoom');

                    // Fit All button
                    var fitAllBtn = L.DomUtil.create('a', 'leaflet-control-zoom-fit-all', container);
                    fitAllBtn.href = '#';
                    fitAllBtn.title = 'Fit to all data';
                    fitAllBtn.innerHTML = '⊞';
                    fitAllBtn.setAttribute('role', 'button');
                    fitAllBtn.setAttribute('aria-label', 'Fit to all data');
                    this._styleButton(fitAllBtn);

                    L.DomEvent.on(fitAllBtn, 'click', function (e) {
                        L.DomEvent.stop(e);
                        this._fitToAllLayers(map);
                    }, this);

                    // Fit Selected button
                    var fitSelectedBtn = L.DomUtil.create('a', 'leaflet-control-zoom-fit-selected', container);
                    fitSelectedBtn.href = '#';
                    fitSelectedBtn.title = 'Fit to selected polygons';
                    fitSelectedBtn.innerHTML = '◎';
                    fitSelectedBtn.setAttribute('role', 'button');
                    fitSelectedBtn.setAttribute('aria-label', 'Fit to selected polygons');
                    this._styleButton(fitSelectedBtn);

                    L.DomEvent.on(fitSelectedBtn, 'click', function (e) {
                        L.DomEvent.stop(e);
                        this._fitToSelected(map);
                    }, this);

                    // Box Zoom button
                    var boxZoomBtn = L.DomUtil.create('a', 'leaflet-control-zoom-box', container);
                    boxZoomBtn.href = '#';
                    boxZoomBtn.title = 'Zoom to window (draw rectangle)';
                    boxZoomBtn.innerHTML = '⬚';
                    boxZoomBtn.setAttribute('role', 'button');
                    boxZoomBtn.setAttribute('aria-label', 'Zoom to window');
                    this._styleButton(boxZoomBtn);

                    this._boxZoomActive = false;
                    this._boxZoomBtn = boxZoomBtn;
                    this._boxZoomStartPoint = null;
                    this._boxZoomRect = null;

                    L.DomEvent.on(boxZoomBtn, 'click', function (e) {
                        L.DomEvent.stop(e);
                        this._toggleBoxZoom(map);
                    }, this);

                    // Reset View button
                    var resetBtn = L.DomUtil.create('a', 'leaflet-control-zoom-reset', container);
                    resetBtn.href = '#';
                    resetBtn.title = 'Reset to Italy view';
                    resetBtn.innerHTML = '🏠';
                    resetBtn.setAttribute('role', 'button');
                    resetBtn.setAttribute('aria-label', 'Reset view');
                    this._styleButton(resetBtn);

                    L.DomEvent.on(resetBtn, 'click', function (e) {
                        L.DomEvent.stop(e);
                        map.setView([41.8719, 12.5674], 6);
                        console.log('[CustomZoomControl] View reset to Italy');
                    }, this);

                    // Fullscreen button
                    var fullscreenBtn = L.DomUtil.create('a', 'leaflet-control-zoom-fullscreen', container);
                    fullscreenBtn.href = '#';
                    fullscreenBtn.title = 'Toggle fullscreen';
                    fullscreenBtn.innerHTML = '⛶';
                    fullscreenBtn.setAttribute('role', 'button');
                    fullscreenBtn.setAttribute('aria-label', 'Toggle fullscreen');
                    this._styleButton(fullscreenBtn);
                    this._isFullscreen = false;
                    this._fullscreenBtn = fullscreenBtn;

                    L.DomEvent.on(fullscreenBtn, 'click', function (e) {
                        L.DomEvent.stop(e);
                        this._toggleFullscreen(map);
                    }, this);

                    L.DomEvent.disableClickPropagation(container);

                    // Store reference to map for box zoom handlers
                    this._map = map;

                    return container;
                },

                _styleButton: function(btn) {
                    btn.style.fontSize = '16px';
                    btn.style.display = 'flex';
                    btn.style.alignItems = 'center';
                    btn.style.justifyContent = 'center';
                    btn.style.width = '30px';
                    btn.style.height = '30px';
                    btn.style.textDecoration = 'none';
                    btn.style.color = '#000';
                    btn.style.backgroundColor = 'white';
                    btn.style.lineHeight = '30px';
                },

                _fitToAllLayers: function(map) {
                    var bounds = null;

                    map.eachLayer(function(layer) {
                        // Skip tile layers
                        if (layer instanceof L.TileLayer) return;

                        if (layer.getBounds && typeof layer.getBounds === 'function') {
                            try {
                                var layerBounds = layer.getBounds();
                                if (layerBounds && layerBounds.isValid()) {
                                    if (bounds === null) {
                                        bounds = L.latLngBounds(layerBounds);
                                    } else {
                                        bounds.extend(layerBounds);
                                    }
                                }
                            } catch (e) {
                                // Layer might not have valid bounds
                            }
                        }
                    });

                    if (bounds && bounds.isValid()) {
                        map.fitBounds(bounds, { padding: [20, 20], maxZoom: 18 });
                        console.log('[CustomZoomControl] Fitted to all layers');
                    } else {
                        alert('No polygon data loaded on the map.');
                    }
                },

                _fitToSelected: function(map) {
                    // Try to get selected polygons from parent window
                    var selectedPolygons = [];
                    try {
                        if (window.parent && window.parent.selectedPolygons) {
                            selectedPolygons = window.parent.selectedPolygons;
                        } else if (window.selectedPolygons) {
                            selectedPolygons = window.selectedPolygons;
                        }
                    } catch (e) {
                        console.warn('[CustomZoomControl] Could not access selected polygons:', e);
                    }

                    if (!selectedPolygons || selectedPolygons.length === 0) {
                        alert('No polygons selected. Click on polygons to select them first.');
                        return;
                    }

                    var bounds = null;
                    selectedPolygons.forEach(function(polygon) {
                        if (polygon.getBounds) {
                            var layerBounds = polygon.getBounds();
                            if (layerBounds.isValid()) {
                                if (bounds === null) {
                                    bounds = L.latLngBounds(layerBounds);
                                } else {
                                    bounds.extend(layerBounds);
                                }
                            }
                        }
                    });

                    if (bounds && bounds.isValid()) {
                        map.fitBounds(bounds, { padding: [20, 20], maxZoom: 18 });
                        console.log('[CustomZoomControl] Fitted to selected polygons');
                    }
                },

                _toggleBoxZoom: function(map) {
                    var self = this;
                    this._boxZoomActive = !this._boxZoomActive;

                    if (this._boxZoomActive) {
                        this._boxZoomBtn.style.backgroundColor = '#e0e0ff';
                        map.dragging.disable();
                        map.getContainer().style.cursor = 'crosshair';

                        // Set up event handlers
                        this._mousedownHandler = function(e) {
                            if (self._boxZoomActive) {
                                self._boxZoomStartPoint = e.latlng;
                                if (self._boxZoomRect) {
                                    map.removeLayer(self._boxZoomRect);
                                    self._boxZoomRect = null;
                                }
                            }
                        };

                        this._mousemoveHandler = function(e) {
                            if (self._boxZoomActive && self._boxZoomStartPoint) {
                                var bounds = L.latLngBounds(self._boxZoomStartPoint, e.latlng);
                                if (self._boxZoomRect) {
                                    self._boxZoomRect.setBounds(bounds);
                                } else {
                                    self._boxZoomRect = L.rectangle(bounds, {
                                        color: '#3388ff',
                                        weight: 2,
                                        fillOpacity: 0.2,
                                        dashArray: '5, 5'
                                    }).addTo(map);
                                }
                            }
                        };

                        this._mouseupHandler = function(e) {
                            if (self._boxZoomActive && self._boxZoomStartPoint) {
                                var bounds = L.latLngBounds(self._boxZoomStartPoint, e.latlng);
                                var startPoint = map.latLngToContainerPoint(self._boxZoomStartPoint);
                                var endPoint = map.latLngToContainerPoint(e.latlng);
                                var distance = Math.sqrt(
                                    Math.pow(endPoint.x - startPoint.x, 2) +
                                    Math.pow(endPoint.y - startPoint.y, 2)
                                );

                                if (distance > 20) {
                                    map.fitBounds(bounds, { padding: [10, 10] });
                                }

                                // Clean up
                                self._deactivateBoxZoom(map);
                            }
                        };

                        map.on('mousedown', this._mousedownHandler);
                        map.on('mousemove', this._mousemoveHandler);
                        map.on('mouseup', this._mouseupHandler);

                        console.log('[CustomZoomControl] Box zoom mode activated');
                    } else {
                        this._deactivateBoxZoom(map);
                    }
                },

                _deactivateBoxZoom: function(map) {
                    if (this._boxZoomRect) {
                        map.removeLayer(this._boxZoomRect);
                        this._boxZoomRect = null;
                    }
                    this._boxZoomStartPoint = null;
                    this._boxZoomActive = false;
                    this._boxZoomBtn.style.backgroundColor = 'white';
                    map.dragging.enable();
                    map.getContainer().style.cursor = '';

                    if (this._mousedownHandler) {
                        map.off('mousedown', this._mousedownHandler);
                    }
                    if (this._mousemoveHandler) {
                        map.off('mousemove', this._mousemoveHandler);
                    }
                    if (this._mouseupHandler) {
                        map.off('mouseup', this._mouseupHandler);
                    }

                    console.log('[CustomZoomControl] Box zoom mode deactivated');
                },

                _toggleFullscreen: function(map) {
                    var container = map.getContainer();
                    var self = this;

                    if (!this._isFullscreen) {
                        // Enter fullscreen
                        if (container.requestFullscreen) {
                            container.requestFullscreen();
                        } else if (container.webkitRequestFullscreen) {
                            container.webkitRequestFullscreen();
                        } else if (container.msRequestFullscreen) {
                            container.msRequestFullscreen();
                        }
                        this._isFullscreen = true;
                        this._fullscreenBtn.innerHTML = '⛶';
                        this._fullscreenBtn.title = 'Exit fullscreen';
                        console.log('[CustomZoomControl] Entered fullscreen');
                    } else {
                        // Exit fullscreen
                        if (document.exitFullscreen) {
                            document.exitFullscreen();
                        } else if (document.webkitExitFullscreen) {
                            document.webkitExitFullscreen();
                        } else if (document.msExitFullscreen) {
                            document.msExitFullscreen();
                        }
                        this._isFullscreen = false;
                        this._fullscreenBtn.innerHTML = '⛶';
                        this._fullscreenBtn.title = 'Toggle fullscreen';
                        console.log('[CustomZoomControl] Exited fullscreen');
                    }

                    // Listen for fullscreen change events to sync state
                    var fullscreenChangeHandler = function() {
                        var isNowFullscreen = !!(document.fullscreenElement ||
                                                  document.webkitFullscreenElement ||
                                                  document.msFullscreenElement);
                        self._isFullscreen = isNowFullscreen;
                        if (isNowFullscreen) {
                            self._fullscreenBtn.title = 'Exit fullscreen';
                        } else {
                            self._fullscreenBtn.title = 'Toggle fullscreen';
                        }
                        // Invalidate map size after fullscreen change
                        setTimeout(function() {
                            map.invalidateSize();
                        }, 100);
                    };

                    document.addEventListener('fullscreenchange', fullscreenChangeHandler);
                    document.addEventListener('webkitfullscreenchange', fullscreenChangeHandler);
                    document.addEventListener('msfullscreenchange', fullscreenChangeHandler);
                }
            });

            new L.Control.CustomZoom().addTo({{ this._parent.get_name() }});
        {% endmacro %}
    """)

    def __init__(self):
        super(CustomZoomControl, self).__init__()
        self._name = 'CustomZoomControl'


# Global variable to store current geodataframe
current_gdf = None

# Global variable to store layer data for multi-layer support
current_layers = {}

# Global variable to store auction properties
auction_properties = None


def extract_qpkg_data(file_path):
    """Extract geospatial data from QPKG or GPKG file"""
    global current_gdf
    
    # If it's a GPKG file, read directly
    if file_path.endswith('.gpkg'):
        try:
            gdf = gpd.read_file(file_path)
            current_gdf = gdf
            return gdf.to_json()
        except Exception:
            return None
    
    # If it's a QPKG file, try to extract and search for geospatial files
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            # Extract QPKG (it's essentially a ZIP file)
            with zipfile.ZipFile(file_path, 'r') as zip_ref:
                zip_ref.extractall(temp_dir)
            
            # Look for common geospatial file formats
            temp_path = Path(temp_dir)
            geospatial_files = []
            
            for ext in ['*.shp', '*.geojson', '*.gpkg', '*.kml']:
                geospatial_files.extend(temp_path.rglob(ext))
            
            # Read the first found geospatial file
            if geospatial_files:
                gdf = gpd.read_file(geospatial_files[0])
                current_gdf = gdf
                return gdf.to_json()
    
    except (zipfile.BadZipFile, zipfile.LargeZipFile):
        # If QPKG is not a ZIP file, try to read it directly as a geospatial file
        try:
            gdf = gpd.read_file(file_path)
            current_gdf = gdf
            return gdf.to_json()
        except Exception:
            pass
    
    return None


def get_current_gdf():
    """Get the current GeoDataFrame"""
    return current_gdf


def set_current_gdf(gdf):
    """Set the current GeoDataFrame"""
    global current_gdf
    current_gdf = gdf


def get_current_layers():
    """Get the current layers data"""
    return current_layers


def set_current_layers(layers_data):
    """Set the current layers data"""
    global current_layers
    current_layers = layers_data


def clear_current_layers():
    """Clear all current layers"""
    global current_layers
    current_layers = {}


def find_adjacent_polygons(gdf: gpd.GeoDataFrame, selected_idx: int, touch_method: str = "touches") -> List[int]:
    """
    Find polygons adjacent to the selected polygon.
    
    Args:
        gdf: GeoDataFrame containing polygons
        selected_idx: Index of the selected polygon
        touch_method: Method to determine adjacency ('touches', 'intersects', 'overlaps')
    
    Returns:
        List of indices of adjacent polygons
    """
    logger.info(f"Finding adjacent polygons: selected_idx={selected_idx}, method={touch_method}, gdf_len={len(gdf)}")

    if selected_idx >= len(gdf):
        logger.warning(f"Selected index {selected_idx} is out of bounds (gdf length: {len(gdf)})")
        return []

    selected_geom = gdf.iloc[selected_idx].geometry
    logger.debug(f"Selected geometry type: {selected_geom.geom_type}")
    adjacent_indices = []

    for idx, row in gdf.iterrows():
        if idx == selected_idx:
            continue

        try:
            # Check spatial relationship
            if touch_method == "touches":
                is_adjacent = selected_geom.touches(row.geometry)
            elif touch_method == "intersects":
                is_adjacent = selected_geom.intersects(row.geometry) and not selected_geom.within(row.geometry)
            elif touch_method == "overlaps":
                is_adjacent = selected_geom.overlaps(row.geometry)
            else:
                # Default to touches
                is_adjacent = selected_geom.touches(row.geometry)

            if is_adjacent:
                logger.debug(f"Found adjacent polygon at index {idx}")
                adjacent_indices.append(idx)

        except Exception as e:
            logger.error(f"Error checking adjacency for polygon {idx}: {e}")
            continue

    logger.info(f"Total adjacent polygons found: {len(adjacent_indices)}")
    return adjacent_indices


def create_auction_properties_layer(auction_data: List[dict] = None):
    """
    Create a layer highlighting properties at auctions

    Args:
        auction_data: List of auction properties with format:
        [
            {
                "property_id": "A001_001",
                "cadastral_code": "A001",
                "coordinates": [lat, lon],
                "auction_date": "2024-01-15",
                "starting_price": 150000,
                "property_type": "residential",
                "status": "active"
            }
        ]
    """
    global auction_properties

    # Sample auction data if none provided
    if auction_data is None:
        auction_data = [
            {
                "property_id": "A018_001",
                "cadastral_code": "A018",
                "coordinates": [42.2025, 13.6625],
                "auction_date": "2024-02-15",
                "starting_price": 125000,
                "property_type": "residential",
                "status": "active",
                "description": "Casa indipendente - Acciano"
            },
            {
                "property_id": "A018_002",
                "cadastral_code": "A018",
                "coordinates": [42.2045, 13.6645],
                "auction_date": "2024-03-20",
                "starting_price": 85000,
                "property_type": "agricultural",
                "status": "active",
                "description": "Terreno agricolo - Acciano"
            },
            {
                "property_id": "A018_003",
                "cadastral_code": "A018",
                "coordinates": [42.2015, 13.6605],
                "auction_date": "2024-01-30",
                "starting_price": 200000,
                "property_type": "commercial",
                "status": "sold",
                "description": "Immobile commerciale - Acciano"
            }
        ]

    # Convert to GeoDataFrame
    df = pd.DataFrame(auction_data)

    # Handle coordinates - check if we have lat/lon columns or coordinates list
    if 'latitude' in df.columns and 'longitude' in df.columns:
        geometry = [Point(lon, lat) for lat, lon in zip(df['latitude'], df['longitude'])]
    else:
        geometry = [Point(coord[1], coord[0]) for coord in df['coordinates']]  # lon, lat

    auction_gdf = gpd.GeoDataFrame(df, geometry=geometry, crs='EPSG:4326')

    # Add styling attributes
    auction_gdf['marker_color'] = auction_gdf['status'].map({
        'active': '#FF6B6B',      # Red for active auctions
        'sold': '#95E1D3',        # Green for sold properties
        'cancelled': '#FFA726'    # Orange for cancelled
    })

    auction_gdf['marker_size'] = auction_gdf['property_type'].map({
        'residential': 8,
        'commercial': 12,
        'agricultural': 6,
        'industrial': 10
    })

    auction_properties = auction_gdf
    logger.info(f"Created auction properties layer with {len(auction_gdf)} properties")

    return auction_gdf


def get_auction_properties_geojson():
    """Get auction properties as GeoJSON for frontend display"""
    global auction_properties

    if auction_properties is None:
        # Create default auction layer
        create_auction_properties_layer()

    if auction_properties is not None and not auction_properties.empty:
        return auction_properties.to_json()
    else:
        return None


def filter_auction_properties(status: str = None, property_type: str = None,
                            max_price: float = None):
    """
    Filter auction properties by criteria

    Args:
        status: Filter by auction status ('active', 'sold', 'cancelled')
        property_type: Filter by property type ('residential', 'commercial', etc.)
        max_price: Filter by maximum starting price
    """
    global auction_properties

    if auction_properties is None:
        create_auction_properties_layer()

    filtered = auction_properties.copy()

    if status:
        filtered = filtered[filtered['status'] == status]

    if property_type:
        filtered = filtered[filtered['property_type'] == property_type]

    if max_price:
        filtered = filtered[filtered['starting_price'] <= max_price]

    logger.info(f"Filtered auction properties: {len(filtered)} of {len(auction_properties)} properties")
    return filtered


def get_auction_properties():
    """Get the current auction properties GeoDataFrame"""
    global auction_properties
    return auction_properties


def highlight_auction_properties_near_cadastral(distance_km: float = 1.0):
    """
    Find auction properties near the currently loaded cadastral data

    Args:
        distance_km: Search radius in kilometers
    """
    global current_gdf, auction_properties

    if current_gdf is None or auction_properties is None:
        logger.warning("No cadastral data or auction properties loaded")
        return None

    # Reproject to a projected CRS for distance calculations (UTM Zone 33N for Italy)
    cadastral_utm = current_gdf.to_crs('EPSG:32633')
    auction_utm = auction_properties.to_crs('EPSG:32633')

    # Create buffer around cadastral polygons
    buffer_distance = distance_km * 1000  # Convert km to meters
    cadastral_buffered = cadastral_utm.geometry.buffer(buffer_distance)

    # Find auction properties within buffer
    nearby_auctions = []
    for idx, auction_point in auction_utm.iterrows():
        for cadastral_buffer in cadastral_buffered:
            if auction_point.geometry.within(cadastral_buffer):
                nearby_auctions.append(idx)
                break

    if nearby_auctions:
        result = auction_properties.iloc[nearby_auctions]
        logger.info(f"Found {len(result)} auction properties within {distance_km}km of cadastral data")
        return result
    else:
        logger.info(f"No auction properties found within {distance_km}km of cadastral data")
        return None


class MapControlsManager:
    """Unified map controls manager merging functionality from map_controls.py"""

    def __init__(self):
        self.settings = map_controls_settings

    def generate_folium_controls(self, map_instance: folium.Map, use_tree_control=True):
        """Generate Folium-based controls for server-side map generation"""

        # Add comprehensive plugins based on settings
        if self.settings.enable_draw_tools:
            draw = Draw(
                export=False,  # Disabled - using custom ExportControl instead
                position=self.settings.draw_position,
                draw_options={
                    'polyline': True,
                    'polygon': True,
                    'circle': True,
                    'rectangle': True,
                    'marker': True,
                    'circlemarker': True
                }
            )
            map_instance.add_child(draw)
            
            # Add custom Export control
            export_control = ExportControl()
            map_instance.add_child(export_control)

        if self.settings.enable_measure_tools:
            measure = MeasureControl(
                position=self.settings.measure_position,
                primary_length_unit='kilometers',
                secondary_length_unit='meters',
                primary_area_unit='hectares'
            )
            measure.add_to(map_instance)

        # Add custom zoom controls (Fit All, Fit Selected, Box Zoom, Reset, Fullscreen)
        custom_zoom_control = CustomZoomControl()
        map_instance.add_child(custom_zoom_control)

        # Add basic LayerControl for map providers (at the end after all layers are added)
        # This will automatically detect all TileLayer objects on the map
        basic_layer_control = folium.LayerControl(position='topright')
        basic_layer_control.add_to(map_instance)

        # Add TreeLayerControl for geo data files only
        overlay_tree = self._prepare_geo_data_tree(map_instance)
        if overlay_tree:
            tree_control = TreeLayerControl(overlay_tree=overlay_tree, position='topright')
            tree_control.add_to(map_instance)

    def _prepare_geo_data_tree(self, map_instance: folium.Map):
        """Prepare TreeLayerControl structure for geo data files only"""

        # Get current layers data to organize by geographic hierarchy
        current_layers_data = get_current_layers()

        if not current_layers_data:
            # Fallback: Find all GeoJson layers if no structured cadastral data
            geo_layers = []
            for child in map_instance._children.values():
                if hasattr(child, '_name') and 'GeoJson' in str(type(child)):
                    layer_name = getattr(child, '_name', 'Data Layer')
                    geo_layers.append({
                        "label": layer_name,
                        "layer": child
                    })

            if geo_layers:
                return {
                    "label": "Geo Data Layers",
                    "selectAllCheckbox": "Un/select all",
                    "children": geo_layers
                }
            return None

        # Organize layers by Region > Province > Municipality structure
        regions = {}

        # Parse each layer's source_file path to extract hierarchy
        for layer_name, layer_data in current_layers_data.items():
            if 'geojson' in layer_data and 'source_file' in layer_data:
                source_file = layer_data['source_file']

                # Extract path components: ITALIA/Region/Province/Municipality/filename
                path_parts = source_file.split('/')
                if len(path_parts) >= 5 and path_parts[0] == 'ITALIA':
                    region_name = path_parts[1]
                    province_code = path_parts[2]
                    municipality_code = path_parts[3]
                    filename = path_parts[4]

                    # Create hierarchical structure
                    if region_name not in regions:
                        regions[region_name] = {
                            "label": region_name,
                            "selectAllCheckbox": True,
                            "children": []
                        }

                    # Find or create province
                    region_node = regions[region_name]
                    province_node = None
                    for child in region_node["children"]:
                        if child["label"] == f"Province {province_code}":
                            province_node = child
                            break

                    if not province_node:
                        province_node = {
                            "label": f"Province {province_code}",
                            "selectAllCheckbox": True,
                            "children": []
                        }
                        region_node["children"].append(province_node)

                    # Find or create municipality
                    municipality_node = None
                    for child in province_node["children"]:
                        if child["label"] == f"Municipality {municipality_code}":
                            municipality_node = child
                            break

                    if not municipality_node:
                        municipality_node = {
                            "label": f"Municipality {municipality_code}",
                            "selectAllCheckbox": True,
                            "children": []
                        }
                        province_node["children"].append(municipality_node)

                    # Add the file layer to municipality
                    # Find the actual layer object in the map
                    for child in map_instance._children.values():
                        if hasattr(child, '_name') and 'GeoJson' in str(type(child)):
                            child_layer_name = getattr(child, '_name', '')
                            if layer_name in child_layer_name:
                                municipality_node["children"].append({
                                    "label": filename,
                                    "layer": child
                                })
                                break

        # Convert regions dict to children list
        cadastral_children = []
        for region_name in sorted(regions.keys()):
            cadastral_children.append(regions[region_name])

        if cadastral_children:
            return {
                "label": "ITALIA - Cadastral Data",
                "selectAllCheckbox": "Un/select all",
                "children": cadastral_children
            }

        return None


class IntegratedMapGenerator:
    """Generates maps with auction properties and cadastral data"""

    def __init__(self):
        self.default_center = [41.9028, 12.4964]  # Rome, Italy
        self.default_zoom = 6
        self.controls_manager = MapControlsManager()

    def create_comprehensive_map(self, cadastral_geojson=None, cadastral_layers=None, auction_geojson=None,
                               center=None, zoom=None) -> folium.Map:
        """Create a comprehensive map with all layers and controls"""

        # Use provided center/zoom or defaults
        map_center = center or self.default_center
        map_zoom = zoom or self.default_zoom

        # Define Italy's bounding box to restrict view (from settings)
        italy_bounds = [
            app_settings.italy_bounds_sw,  # South-west corner (Sicily)
            app_settings.italy_bounds_ne   # North-east corner (Alps)
        ]

        # Create base map with Google Satellite as default, restricted to Italy
        m = folium.Map(
            location=map_center,
            zoom_start=map_zoom,
            tiles=None,  # We'll add Google Satellite as the first tile layer
            max_bounds=True,  # Enable boundary restrictions
            max_bounds_viscosity=1.0,  # How strongly to enforce the bounds
            min_zoom=5,  # Prevent zooming out too far
            max_zoom=18,  # Standard max zoom for satellite imagery
        )

        # Set the bounds after map creation
        m.fit_bounds(italy_bounds)

        # Add all tile layers from map.js mapProviders (Google Satellite last as default)
        tile_layers = [
            {
                'tiles': 'https://mt1.google.com/vt/lyrs=m&x={x}&y={y}&z={z}',
                'attr': '© Google',
                'name': 'Google Maps'
            },
            {
                'tiles': 'https://mt1.google.com/vt/lyrs=p&x={x}&y={y}&z={z}',
                'attr': '© Google',
                'name': 'Google Terrain'
            },
            {
                'tiles': 'https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}',
                'attr': '© Google',
                'name': 'Google Hybrid'
            },
            {
                'tiles': 'https://mt1.google.com/vt/lyrs=m,transit&x={x}&y={y}&z={z}',
                'attr': '© Google',
                'name': 'Google Maps with Transit'
            },
            {
                'tiles': 'https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}',
                'attr': '© Google',
                'name': 'Google Maps with Traffic'
            },
            {
                'tiles': 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
                'attr': '© ESRI',
                'name': 'ESRI World Imagery'
            },
            {
                'tiles': 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Terrain_Base/MapServer/tile/{z}/{y}/{x}',
                'attr': '© ESRI',
                'name': 'ESRI World Terrain'
            },
            {
                'tiles': 'https://cartodb-basemaps-{s}.global.ssl.fastly.net/light_all/{z}/{x}/{y}.png',
                'attr': '© CartoDB',
                'name': 'CartoDB Positron (Light)'
            },
            {
                'tiles': 'https://cartodb-basemaps-{s}.global.ssl.fastly.net/dark_all/{z}/{x}/{y}.png',
                'attr': '© CartoDB',
                'name': 'CartoDB Dark Matter'
            },
            {
                'tiles': 'https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}',
                'attr': '© Google',
                'name': 'Google Satellite'
            }
        ]

        # Add all tile layers to the map, with Google Satellite as the last (default) layer
        for i, layer_config in enumerate(tile_layers):
            tile_layer = folium.TileLayer(
                tiles=layer_config['tiles'],
                attr=layer_config['attr'],
                name=layer_config['name'],
                overlay=False,
                control=True
            )
            tile_layer.add_to(m)

            # Note: Google Satellite is the last layer added, making it the default active base layer

        # Add weather overlays from map.js
        weather_overlays = [
            {
                'tiles': 'https://tile.openweathermap.org/map/temp_new/{z}/{x}/{y}.png?appid=b6907d289e10d714a6e88b30761fae22',
                'attr': '© OpenWeatherMap',
                'name': 'Temperature Layer'
            },
            {
                'tiles': 'https://tile.openweathermap.org/map/precipitation_new/{z}/{x}/{y}.png?appid=b6907d289e10d714a6e88b30761fae22',
                'attr': '© OpenWeatherMap',
                'name': 'Precipitation Layer'
            },
            {
                'tiles': 'https://tile.openweathermap.org/map/wind_new/{z}/{x}/{y}.png?appid=b6907d289e10d714a6e88b30761fae22',
                'attr': '© OpenWeatherMap',
                'name': 'Wind Speed Layer'
            },
            {
                'tiles': 'https://tile.openweathermap.org/map/clouds_new/{z}/{x}/{y}.png?appid=b6907d289e10d714a6e88b30761fae22',
                'attr': '© OpenWeatherMap',
                'name': 'Cloud Coverage Layer'
            }
        ]

        # Add weather overlays to the map
        for overlay_config in weather_overlays:
            folium.TileLayer(
                tiles=overlay_config['tiles'],
                attr=overlay_config['attr'],
                name=overlay_config['name'],
                overlay=True,
                control=True
            ).add_to(m)

        # Add Italy regional borders for visual reference
        try:
            italy_regions_url = "https://raw.githubusercontent.com/openpolis/geojson-italy/master/geojson/limits_IT_regions.geojson"
            folium.GeoJson(
                italy_regions_url,
                name="Italy Regions",
                style_function=lambda feature: {
                    'fillColor': 'transparent',
                    'color': '#0066cc',
                    'weight': 2,
                    'fillOpacity': 0,
                    'opacity': 0.7,
                },
                tooltip=folium.GeoJsonTooltip(
                    fields=['reg_name'],
                    aliases=['Region:'],
                    labels=True
                )
            ).add_to(m)
        except Exception as e:
            logger.warning(f"Failed to load Italy regional borders: {e}")

        # Add markers for Italian regional capitals
        regional_capitals = [
            {"name": "Roma", "region": "Lazio", "coords": [41.9028, 12.4964], "population": "2.8M"},
            {"name": "Milano", "region": "Lombardia", "coords": [45.4642, 9.1900], "population": "1.4M"},
            {"name": "Napoli", "region": "Campania", "coords": [40.8518, 14.2681], "population": "959K"},
            {"name": "Torino", "region": "Piemonte", "coords": [45.0703, 7.6869], "population": "870K"},
            {"name": "Palermo", "region": "Sicilia", "coords": [38.1157, 13.3615], "population": "663K"},
            {"name": "Genova", "region": "Liguria", "coords": [44.4056, 8.9463], "population": "561K"},
            {"name": "Bologna", "region": "Emilia-Romagna", "coords": [44.4949, 11.3426], "population": "391K"},
            {"name": "Firenze", "region": "Toscana", "coords": [43.7696, 11.2558], "population": "367K"},
            {"name": "Bari", "region": "Puglia", "coords": [41.1171, 16.8719], "population": "315K"},
            {"name": "Catanzaro", "region": "Calabria", "coords": [38.9097, 16.5877], "population": "86K"},
            {"name": "Venezia", "region": "Veneto", "coords": [45.4408, 12.3155], "population": "258K"},
            {"name": "Trieste", "region": "Friuli-Venezia Giulia", "coords": [45.6495, 13.7768], "population": "203K"},
            {"name": "Trento", "region": "Trentino-Alto Adige", "coords": [46.0664, 11.1257], "population": "118K"},
            {"name": "Perugia", "region": "Umbria", "coords": [43.1107, 12.3908], "population": "165K"},
            {"name": "Ancona", "region": "Marche", "coords": [43.6158, 13.5189], "population": "100K"},
            {"name": "L'Aquila", "region": "Abruzzo", "coords": [42.3498, 13.3995], "population": "70K"},
            {"name": "Campobasso", "region": "Molise", "coords": [41.5630, 14.6631], "population": "49K"},
            {"name": "Potenza", "region": "Basilicata", "coords": [40.6389, 15.8056], "population": "67K"},
            {"name": "Cagliari", "region": "Sardegna", "coords": [39.2238, 9.1217], "population": "150K"},
            {"name": "Aosta", "region": "Valle d'Aosta", "coords": [45.7372, 7.3206], "population": "34K"},
        ]

        # Create a feature group for regional capitals
        capitals_group = folium.FeatureGroup(name="Regional Capitals", show=True)

        for capital in regional_capitals:
            # Create a custom icon with a star marker
            icon = folium.Icon(
                color='red',
                icon='star',
                prefix='fa'
            )

            # Create popup with capital information
            popup_html = f"""
            <div style="font-family: Arial, sans-serif; width: 200px;">
                <h4 style="margin: 0 0 10px 0; color: #0066cc;">{capital['name']}</h4>
                <p style="margin: 5px 0;"><strong>Region:</strong> {capital['region']}</p>
                <p style="margin: 5px 0;"><strong>Population:</strong> {capital['population']}</p>
                <p style="margin: 5px 0; font-size: 11px; color: #666;">Regional Capital</p>
            </div>
            """

            # Add marker
            folium.Marker(
                location=capital['coords'],
                popup=folium.Popup(popup_html, max_width=250),
                tooltip=f"{capital['name']} - {capital['region']}",
                icon=icon
            ).add_to(capitals_group)

        capitals_group.add_to(m)

        # Add cadastral data layers
        if cadastral_layers:
            # Multiple layers mode - add each layer separately with random colors

            def generate_random_color():
                """Generate a random color in hex format"""
                hue = random.random()
                saturation = 0.7 + random.random() * 0.3  # 70-100% saturation
                lightness = 0.4 + random.random() * 0.3   # 40-70% lightness
                rgb = colorsys.hsv_to_rgb(hue, saturation, lightness)
                return '#{:02x}{:02x}{:02x}'.format(int(rgb[0]*255), int(rgb[1]*255), int(rgb[2]*255))

            def generate_darker_color(hex_color):
                """Generate a darker version of the given hex color"""
                hex_color = hex_color.lstrip('#')
                rgb = tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
                darker_rgb = tuple(max(0, int(c * 0.7)) for c in rgb)
                return '#{:02x}{:02x}{:02x}'.format(*darker_rgb)

            for layer_name, layer_data in cadastral_layers.items():
                if 'geojson' in layer_data:
                    # Generate random color for this layer
                    fill_color = generate_random_color()
                    border_color = generate_darker_color(fill_color)

                    folium.GeoJson(
                        layer_data['geojson'],
                        name=f'📁 {layer_name}',
                        style_function=lambda feature, fill_color=fill_color, border_color=border_color: {
                            'fillColor': fill_color,
                            'color': border_color,
                            'weight': 2,
                            'fillOpacity': 0.6,
                            'opacity': 0.8,
                        },
                        popup=folium.GeoJsonPopup(
                            fields=['layer_name', 'source_file', 'feature_id'],
                            labels=True
                        ),
                        tooltip=folium.GeoJsonTooltip(
                            fields=['layer_name'],
                            labels=True,
                            sticky=True
                        )
                    ).add_to(m)
        elif cadastral_geojson:
            # Single layer mode (backward compatibility)
            folium.GeoJson(
                cadastral_geojson,
                name='📊 Cadastral Data',
                style_function=lambda feature: {
                    'fillColor': '#3388ff',  # Standard Leaflet blue
                    'color': '#1a5490',      # Darker blue for border
                    'weight': 2,
                    'fillOpacity': 0.3,
                },
                popup=folium.GeoJsonPopup(fields=['comune_name', 'foglio', 'particella', 'layer_type'] if 'properties' in str(cadastral_geojson) else [])
            ).add_to(m)

        # Add auction properties if provided
        if auction_geojson:
            self._add_auction_markers(m, auction_geojson)

        # Add all folium controls
        self.controls_manager.generate_folium_controls(m)

        # Only TreeLayerControl is used (added within generate_folium_controls)
        # Basic LayerControl has been removed as requested

        return m

    def _add_auction_markers(self, map_instance: folium.Map, auction_geojson):
        """Add auction property markers to the map"""

        def style_function(feature):
            props = feature['properties']
            status = props.get('status', 'active')

            color_map = {
                'active': '#FF6B6B',      # Red
                'sold': '#95E1D3',        # Green
                'cancelled': '#FFA726'    # Orange
            }

            return {
                'fillColor': color_map.get(status, '#FF6B6B'),
                'color': '#000000',
                'weight': 1,
                'fillOpacity': 0.8,
                'radius': props.get('marker_size', 8)
            }

        def popup_function(feature):
            props = feature['properties']
            popup_html = f"""
            <div style="width: 200px;">
                <h4>{props.get('description', props.get('property_id', 'Property'))}</h4>
                <p><b>Type:</b> {props.get('property_type', 'N/A')}</p>
                <p><b>Status:</b> {props.get('status', 'N/A')}</p>
                <p><b>Price:</b> €{props.get('starting_price', 0):,}</p>
                <p><b>Date:</b> {props.get('auction_date', 'N/A')}</p>
            </div>
            """
            return popup_html

        # Add auction markers
        folium.GeoJson(
            auction_geojson,
            name='🏠 Auction Properties',
            style_function=style_function,
            popup=folium.GeoJsonPopup(fields=[], labels=False),
            marker=folium.CircleMarker()
        ).add_to(map_instance)


# ============================================================================
# Global instances for backward compatibility
# ============================================================================

# Create global instances that can be imported elsewhere
# map_controls = MapControlsManager()
map_generator = IntegratedMapGenerator()
