
import zipfile
import geopandas as gpd
from pathlib import Path
import tempfile
from typing import List, Dict, Any, Optional, Union
import folium
from folium import plugins
from folium.plugins import (
    Draw, Fullscreen, LocateControl, MeasureControl,
    Search, BeautifyIcon, FeatureGroupSubGroup, FloatImage,
    Geocoder, GroupedLayerControl, MarkerCluster, MiniMap,
    MousePosition, OverlappingMarkerSpiderfier, TagFilterButton,
    TimeSliderChoropleth, Timeline, TimelineSlider,
    TimestampedGeoJson, TreeLayerControl
)
import json
from branca.element import Template, MacroElement
from pydantic_settings import BaseSettings
from land_registry.settings import map_controls_settings


# Global variable to store current geodataframe
current_gdf = None

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
    print(f"Finding adjacent polygons: selected_idx={selected_idx}, method={touch_method}, gdf_len={len(gdf)}")
    
    if selected_idx >= len(gdf):
        print(f"Selected index {selected_idx} is out of bounds")
        return []
    
    selected_geom = gdf.iloc[selected_idx].geometry
    print(f"Selected geometry type: {selected_geom.geom_type}")
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
                print(f"Found adjacent polygon at index {idx}")
                adjacent_indices.append(idx)
                
        except Exception as e:
            print(f"Error checking adjacency for polygon {idx}: {e}")
            continue
    
    print(f"Total adjacent polygons found: {len(adjacent_indices)}")
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
    import geopandas as gpd
    from shapely.geometry import Point
    import pandas as pd

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
    print(f"Created auction properties layer with {len(auction_gdf)} properties")

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

    print(f"Filtered auction properties: {len(filtered)} of {len(auction_properties)} properties")
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
        print("No cadastral data or auction properties loaded")
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
        print(f"Found {len(result)} auction properties within {distance_km}km of cadastral data")
        return result
    else:
        print(f"No auction properties found within {distance_km}km of cadastral data")
        return None


# ============================================================================
# Merged Map Controls and Generator Classes
# ============================================================================

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


class MapControlsManager:
    """Unified map controls manager merging functionality from map_controls.py"""

    def __init__(self):
        self.settings = map_controls_settings

    def generate_html(self) -> str:
        """Generate HTML for map controls"""
        html_parts = []

        # Auction properties controls
        html_parts.append('''
        <div class="auction-controls">
            <h4>🏠 Properties at Auction</h4>
            <div class="control-row">
                <button onclick="toggleAuctionLayer()" class="control-btn">Toggle Auctions</button>
                <button onclick="filterActiveAuctions()" class="control-btn">Active Only</button>
            </div>
            <div class="control-row">
                <select id="auctionTypeFilter" onchange="filterAuctionsByType()">
                    <option value="">All Types</option>
                    <option value="residential">Residential</option>
                    <option value="commercial">Commercial</option>
                    <option value="agricultural">Agricultural</option>
                    <option value="industrial">Industrial</option>
                </select>
            </div>
            <div class="control-row">
                <label>Max Price: €</label>
                <input type="number" id="maxPriceFilter" onchange="filterAuctionsByPrice()" placeholder="150000">
            </div>
        </div>
        ''')

        # Drawing controls
        html_parts.append('''
        <div class="drawing-controls">
            <h4>✏️ Drawing Tools</h4>
            <div class="control-row">
                <button onclick="startDrawingMode()" class="control-btn">Draw Polygon</button>
                <button onclick="clearAllDrawings()" class="control-btn">Clear All</button>
            </div>
        </div>
        ''')

        # Layer controls
        html_parts.append('''
        <div class="layer-controls">
            <h4>🗂️ Layers</h4>
            <div class="control-row">
                <label><input type="checkbox" id="cadastralLayer" checked> Cadastral</label>
                <label><input type="checkbox" id="auctionLayer" checked> Auctions</label>
            </div>
        </div>
        ''')

        return ''.join(html_parts)

    def generate_javascript(self) -> str:
        """Generate JavaScript for map controls"""
        js_parts = []

        # Auction properties JavaScript
        js_parts.append('''
        // Auction Properties Layer Management
        let auctionMarkersGroup = null;

        window.toggleAuctionLayer = function() {
            if (auctionMarkersGroup) {
                if (map.hasLayer(auctionMarkersGroup)) {
                    map.removeLayer(auctionMarkersGroup);
                } else {
                    map.addLayer(auctionMarkersGroup);
                }
            } else {
                loadAuctionProperties();
            }
        };

        window.loadAuctionProperties = async function() {
            try {
                const response = await fetch('/api/v1/auction-properties/');
                const data = await response.json();

                if (data.geojson) {
                    displayAuctionProperties(data.geojson);
                }
            } catch (error) {
                console.error('Error loading auction properties:', error);
            }
        };

        window.displayAuctionProperties = function(geojson) {
            if (auctionMarkersGroup) {
                map.removeLayer(auctionMarkersGroup);
            }

            auctionMarkersGroup = L.geoJSON(geojson, {
                pointToLayer: function(feature, latlng) {
                    const props = feature.properties;
                    const color = props.marker_color || '#FF6B6B';
                    const size = props.marker_size || 8;

                    return L.circleMarker(latlng, {
                        radius: size,
                        fillColor: color,
                        color: '#000',
                        weight: 1,
                        opacity: 1,
                        fillOpacity: 0.8
                    });
                },
                onEachFeature: function(feature, layer) {
                    const props = feature.properties;
                    const popupContent = `
                        <div class="auction-popup">
                            <h4>${props.description || props.property_id}</h4>
                            <p><strong>Type:</strong> ${props.property_type}</p>
                            <p><strong>Status:</strong> ${props.status}</p>
                            <p><strong>Starting Price:</strong> €${props.starting_price?.toLocaleString()}</p>
                            <p><strong>Auction Date:</strong> ${props.auction_date}</p>
                        </div>
                    `;
                    layer.bindPopup(popupContent);
                }
            }).addTo(map);
        };

        window.filterActiveAuctions = function() {
            // Filter to show only active auctions
            console.log('Filtering active auctions');
        };

        window.filterAuctionsByType = function() {
            const typeFilter = document.getElementById('auctionTypeFilter').value;
            console.log('Filtering by type:', typeFilter);
        };

        window.filterAuctionsByPrice = function() {
            const maxPrice = document.getElementById('maxPriceFilter').value;
            console.log('Filtering by max price:', maxPrice);
        };
        ''')

        return ''.join(js_parts)

    def generate_folium_controls(self, map_instance: folium.Map):
        """Generate Folium-based controls for server-side map generation"""

        # Add comprehensive plugins based on settings
        if self.settings.enable_draw_tools:
            draw = Draw(
                export=True,
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

        if self.settings.enable_measure_tools:
            measure = MeasureControl(
                position=self.settings.measure_position,
                primary_length_unit='kilometers',
                secondary_length_unit='meters',
                primary_area_unit='hectares'
            )
            map_instance.add_child(measure)

        # Add fullscreen control
        fullscreen = Fullscreen(position=self.settings.fullscreen_position)
        map_instance.add_child(fullscreen)

        # Add locate control
        if self.settings.locate_position:
            locate = LocateControl(position=self.settings.locate_position)
            map_instance.add_child(locate)

        # Add minimap if enabled
        if self.settings.enable_minimap:
            minimap = MiniMap(
                position='bottomleft',
                width=150,
                height=150,
                collapsed_width=25,
                collapsed_height=25
            )
            map_instance.add_child(minimap)

        # Add mouse position if enabled
        if self.settings.enable_mouse_position:
            mouse_position = MousePosition(
                position='bottomright',
                separator=' | ',
                empty_string='NaN',
                lng_first=False,
                num_digits=20,
                prefix='Coordinates:'
            )
            map_instance.add_child(mouse_position)

        # Add marker cluster if enabled
        if self.settings.enable_marker_cluster:
            marker_cluster = MarkerCluster(name="Clustered Markers")
            map_instance.add_child(marker_cluster)

        return map_instance


class IntegratedMapGenerator:
    """Generates maps with auction properties and cadastral data"""

    def __init__(self):
        self.default_center = [41.9028, 12.4964]  # Rome, Italy
        self.default_zoom = 6
        self.controls_manager = MapControlsManager()

    def create_comprehensive_map(self, cadastral_geojson=None, auction_geojson=None,
                               center=None, zoom=None) -> folium.Map:
        """Create a comprehensive map with all layers and controls"""

        # Use provided center/zoom or defaults
        map_center = center or self.default_center
        map_zoom = zoom or self.default_zoom

        # Create base map
        m = folium.Map(
            location=map_center,
            zoom_start=map_zoom,
            tiles='OpenStreetMap'
        )

        # Add additional tile layers
        folium.TileLayer(
            tiles='https://{s}.google.com/vt/lyrs=s&x={x}&y={y}&z={z}',
            attr='Google Satellite',
            name='🛰️ Google Satellite',
            overlay=False,
            control=True,
            subdomains=['mt0', 'mt1', 'mt2', 'mt3']
        ).add_to(m)

        folium.TileLayer(
            tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
            attr='Esri WorldImagery',
            name='🌍 Esri Satellite',
            overlay=False,
            control=True
        ).add_to(m)

        # Add cadastral data if provided
        if cadastral_geojson:
            folium.GeoJson(
                cadastral_geojson,
                name='Cadastral Data',
                style_function=lambda feature: {
                    'fillColor': '#blue',
                    'color': '#darkblue',
                    'weight': 2,
                    'fillOpacity': 0.3,
                },
                popup=folium.GeoJsonPopup(fields=['property_id', 'area', 'type'] if 'properties' in str(cadastral_geojson) else [])
            ).add_to(m)

        # Add auction properties if provided
        if auction_geojson:
            self._add_auction_markers(m, auction_geojson)

        # Add all folium controls
        self.controls_manager.generate_folium_controls(m)

        # Add layer control
        folium.LayerControl().add_to(m)

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
map_controls = MapControlsManager()
map_generator = IntegratedMapGenerator()
