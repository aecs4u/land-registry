// ========================================
// FOLIUM MAP INTERFACE FUNCTIONS
// ========================================
// These functions are for server-generated Folium maps (used in index.html)

// Global variables for Folium map interface
window.selectedPolygons = [];
window.geoJsonData = null;
window.hasData = false;
window.cadastralDataCache = null;
window.currentFileSelection = [];

// Function to update Find Adjacent Polygons button state
function updateAdjacencyButtonState() {
    const findAdjacencyBtn = document.getElementById('findAdjacencyBtn');
    const clearSelectionBtn = document.getElementById('clearSelectionBtn');
    const selectedCount = document.getElementById('selectedCount');

    const hasSelection = window.selectedPolygons.length > 0;

    if (findAdjacencyBtn) {
        findAdjacencyBtn.disabled = !hasSelection;
    }

    if (clearSelectionBtn) {
        clearSelectionBtn.disabled = !hasSelection;
    }

    if (selectedCount) {
        const countText = window.selectedPolygons.length === 1
            ? '1 polygon selected'
            : `${window.selectedPolygons.length} polygons selected`;
        selectedCount.textContent = countText;
    }
}

// Function to update polygon management button states
function updatePolygonManagementState() {
    const removePolygonsBtn = document.getElementById('removePolygonsBtn');
    const zoomToPolygonsBtn = document.getElementById('zoomToPolygonsBtn');
    const polygonCount = document.getElementById('polygonCount');

    // Count polygon layers on the map
    let polygonLayerCount = 0;
    let totalFeatureCount = 0;

    try {
        const mapElements = document.querySelectorAll('.folium-map');
        if (mapElements.length > 0) {
            const mapId = mapElements[0].id;
            if (window[mapId]) {
                const foliumMap = window[mapId];

                foliumMap.eachLayer(function(layer) {
                    // Skip base tile layers
                    if (layer._url && layer._url.includes('tile')) {
                        return;
                    }

                    // Count GeoJSON layers and feature layers
                    if (layer.getBounds && typeof layer.getBounds === 'function') {
                        polygonLayerCount++;

                        // Try to count features in the layer
                        if (layer.getLayers && typeof layer.getLayers === 'function') {
                            totalFeatureCount += layer.getLayers().length;
                        } else {
                            totalFeatureCount += 1; // Single feature layer
                        }
                    }
                });
            }
        }
    } catch (error) {
        console.warn('Error counting polygon layers:', error);
    }

    const hasPolygons = polygonLayerCount > 0 || (window.geoJsonData && window.geoJsonData.features && window.geoJsonData.features.length > 0);

    // Update button states
    if (removePolygonsBtn) {
        removePolygonsBtn.disabled = !hasPolygons;
    }

    if (zoomToPolygonsBtn) {
        zoomToPolygonsBtn.disabled = !hasPolygons;
    }

    // Update polygon count display
    if (polygonCount) {
        if (hasPolygons) {
            if (totalFeatureCount > 0) {
                polygonCount.textContent = `${totalFeatureCount} features in ${polygonLayerCount} layers loaded`;
            } else {
                polygonCount.textContent = `${polygonLayerCount} polygon layers loaded`;
            }
        } else {
            polygonCount.textContent = 'No polygons loaded';
        }
    }
}

// Function to add polygon selection handlers to Folium map
function initializePolygonSelection() {
    // Wait for Folium map to be fully loaded
    setTimeout(() => {
        // Look for Leaflet map in the DOM
        const mapElements = document.querySelectorAll('.folium-map');
        if (mapElements.length > 0) {
            console.log('Found Folium map, initializing polygon selection');

            // Get the Leaflet map instance from the first folium map
            const mapElement = mapElements[0];
            const mapId = mapElement.id;

            if (window[mapId]) {
                const leafletMap = window[mapId];
                console.log('Leaflet map instance found:', leafletMap);

                // Add event listeners for all layers
                leafletMap.eachLayer(function(layer) {
                    if (layer instanceof L.GeoJSON) {
                        console.log('Found GeoJSON layer, adding selection handlers');

                        layer.eachLayer(function(subLayer) {
                            subLayer.on('click', function(e) {
                                // Handle polygon selection
                                const isSelected = subLayer._selected || false;

                                if (isSelected) {
                                    // Deselect polygon
                                    subLayer._selected = false;
                                    subLayer.setStyle({
                                        fillColor: subLayer.options.originalFillColor || subLayer.options.fillColor,
                                        fillOpacity: subLayer.options.originalFillOpacity || 0.3
                                    });

                                    // Remove from selected polygons array
                                    const index = window.selectedPolygons.findIndex(p => p === subLayer);
                                    if (index > -1) {
                                        window.selectedPolygons.splice(index, 1);
                                    }
                                } else {
                                    // Select polygon
                                    subLayer._selected = true;

                                    // Store original colors if not already stored
                                    if (!subLayer.options.originalFillColor) {
                                        subLayer.options.originalFillColor = subLayer.options.fillColor;
                                        subLayer.options.originalFillOpacity = subLayer.options.fillOpacity;
                                    }

                                    // Highlight selected polygon
                                    subLayer.setStyle({
                                        fillColor: '#ff0000',  // Red for selection
                                        fillOpacity: 0.7
                                    });

                                    // Add to selected polygons array
                                    window.selectedPolygons.push(subLayer);
                                }

                                // Update button state
                                updateAdjacencyButtonState();

                                console.log('Polygon clicked, selected count:', window.selectedPolygons.length);
                            });
                        });
                    }
                });
            }
        }
    }, 1000); // Wait 1 second for Folium to fully initialize
}

// View switching functionality
function showMapView() {
    document.querySelectorAll('.view-content').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.view-toggle button').forEach(el => el.classList.remove('active'));
    document.getElementById('mapView').classList.add('active');
    document.getElementById('mapViewBtn').classList.add('active');
}

function handleTableViewClick() {
    document.querySelectorAll('.view-content').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.view-toggle button').forEach(el => el.classList.remove('active'));
    document.getElementById('tableView').classList.add('active');
    document.getElementById('tableViewBtn').classList.add('active');
}

// NOTE: showAdjacencyView() and showMappingView() are defined in table-manager.js
// which loads before this file. Do not redefine them here to avoid conflicts.

// Upload tab switching
function showUploadTab(tabName) {
    document.querySelectorAll('.upload-content > div').forEach(el => el.style.display = 'none');
    document.querySelectorAll('.upload-tab').forEach(el => el.classList.remove('active'));

    if (tabName === 'file') {
        document.getElementById('fileUpload').style.display = 'block';
        document.querySelector('.upload-tab').classList.add('active');
    } else if (tabName === 'cadastral') {
        document.getElementById('cadastralSelection').style.display = 'block';
        document.querySelectorAll('.upload-tab')[1].classList.add('active');
    }
}

// Polygon selection and adjacency functions
function findAdjacencyForSelected() {
    console.log('Find Adjacent Polygons clicked');

    if (!window.selectedPolygons || window.selectedPolygons.length === 0) {
        alert('Please select one or more polygons first.');
        return;
    }

    console.log(`Finding adjacent polygons for ${window.selectedPolygons.length} selected polygon(s)`);

    showAdjacencyView();

    const selectedPolygonInfo = document.getElementById('selectedPolygonInfo');
    const adjacentPolygonsInfo = document.getElementById('adjacentPolygonsInfo');

    if (selectedPolygonInfo) {
        const count = window.selectedPolygons.length;
        selectedPolygonInfo.innerHTML = `
            <h4>Selected Polygons (${count})</h4>
            <p>Processing ${count} polygon${count > 1 ? 's' : ''} for adjacency analysis...</p>
        `;
    }

    if (adjacentPolygonsInfo) {
        adjacentPolygonsInfo.innerHTML = `
            <p>Analyzing spatial relationships...</p>
            <div style="color: orange;">Note: Adjacency analysis requires server-side processing.
            This is a demonstration of the selection functionality.</div>
        `;
    }
}

function clearPolygonSelection() {
    console.log('Clearing polygon selection');

    if (window.selectedPolygons) {
        window.selectedPolygons.forEach(polygon => {
            polygon._selected = false;
            polygon.setStyle({
                fillColor: polygon.options.originalFillColor || polygon.options.fillColor,
                fillOpacity: polygon.options.originalFillOpacity || 0.3
            });
        });

        window.selectedPolygons = [];
        updateAdjacencyButtonState();
        console.log('Polygon selection cleared');
    }
}

// Placeholder functions for server-generated map compatibility
function toggleAuctionLayer() {
    console.log('Auction layer toggle - functionality handled by Folium map');
}

function filterActiveAuctions() {
    console.log('Filter active auctions - functionality handled by Folium map');
}

function loadAuctionProperties() {
    console.log('Load auction properties - functionality handled by Folium map');
}

function uploadFile() {
    console.log('File upload functionality requires server endpoint integration');
}

// Auto-zoom utility functions
function autoZoomToLoadedData() {
    setTimeout(function() {
        autoZoomToAllPolygons();
    }, 500);
}

// Auto-zoom function is now unified in map.js - calling that implementation

// Function to calculate bounds from all layers on the Folium map
function calculateAllLayersBounds(foliumMap) {
    let minLat = Infinity, maxLat = -Infinity;
    let minLng = Infinity, maxLng = -Infinity;
    let hasFeatures = false;

    // Iterate through all layers on the map
    foliumMap.eachLayer(function(layer) {
        // Skip base tile layers (they have infinite bounds)
        if (layer._url && layer._url.includes('tile')) {
            return;
        }

        // Check if layer has getBounds method (GeoJSON, Polygon, etc.)
        if (layer.getBounds && typeof layer.getBounds === 'function') {
            try {
                const layerBounds = layer.getBounds();
                if (layerBounds && layerBounds.isValid()) {
                    const sw = layerBounds.getSouthWest();
                    const ne = layerBounds.getNorthEast();

                    minLat = Math.min(minLat, sw.lat);
                    maxLat = Math.max(maxLat, ne.lat);
                    minLng = Math.min(minLng, sw.lng);
                    maxLng = Math.max(maxLng, ne.lng);
                    hasFeatures = true;
                }
            } catch (e) {
                // Layer might not have valid bounds, skip it
                console.debug('Could not get bounds for layer:', e);
            }
        }
        // Handle individual markers or points
        else if (layer.getLatLng && typeof layer.getLatLng === 'function') {
            try {
                const latLng = layer.getLatLng();
                if (latLng) {
                    minLat = Math.min(minLat, latLng.lat);
                    maxLat = Math.max(maxLat, latLng.lat);
                    minLng = Math.min(minLng, latLng.lng);
                    maxLng = Math.max(maxLng, latLng.lng);
                    hasFeatures = true;
                }
            } catch (e) {
                console.debug('Could not get latlng for layer:', e);
            }
        }
    });

    // If no features found using layer methods, fall back to GeoJSON data
    if (!hasFeatures && window.geoJsonData) {
        return calculateGeoJsonBounds(window.geoJsonData);
    }

    // Return null if no valid coordinates found
    if (!hasFeatures || minLat === Infinity || maxLat === -Infinity ||
        minLng === Infinity || maxLng === -Infinity) {
        return null;
    }

    return {
        minLat: minLat,
        maxLat: maxLat,
        minLng: minLng,
        maxLng: maxLng
    };
}

function calculateGeoJsonBounds(geoJsonData) {
    if (!geoJsonData || !geoJsonData.features || geoJsonData.features.length === 0) {
        return null;
    }

    let minLat = Infinity, maxLat = -Infinity;
    let minLng = Infinity, maxLng = -Infinity;

    geoJsonData.features.forEach(feature => {
        if (feature.geometry && feature.geometry.coordinates) {
            const coords = feature.geometry.coordinates;

            // Handle different geometry types
            if (feature.geometry.type === 'Polygon') {
                coords[0].forEach(coord => {
                    const lng = coord[0], lat = coord[1];
                    if (lat < minLat) minLat = lat;
                    if (lat > maxLat) maxLat = lat;
                    if (lng < minLng) minLng = lng;
                    if (lng > maxLng) maxLng = lng;
                });
            } else if (feature.geometry.type === 'MultiPolygon') {
                coords.forEach(polygon => {
                    polygon[0].forEach(coord => {
                        const lng = coord[0], lat = coord[1];
                        if (lat < minLat) minLat = lat;
                        if (lat > maxLat) maxLat = lat;
                        if (lng < minLng) minLng = lng;
                        if (lng > maxLng) maxLng = lng;
                    });
                });
            } else if (feature.geometry.type === 'Point') {
                const lng = coords[0], lat = coords[1];
                if (lat < minLat) minLat = lat;
                if (lat > maxLat) maxLat = lat;
                if (lng < minLng) minLng = lng;
                if (lng > maxLng) maxLng = lng;
            } else if (feature.geometry.type === 'LineString') {
                coords.forEach(coord => {
                    const lng = coord[0], lat = coord[1];
                    if (lat < minLat) minLat = lat;
                    if (lat > maxLat) maxLat = lat;
                    if (lng < minLng) minLng = lng;
                    if (lng > maxLng) maxLng = lng;
                });
            }
        }
    });

    // Return null if no valid coordinates found
    if (minLat === Infinity || maxLat === -Infinity ||
        minLng === Infinity || maxLng === -Infinity) {
        return null;
    }

    return {
        minLat: minLat,
        maxLat: maxLat,
        minLng: minLng,
        maxLng: maxLng
    };
}

// Plugin management functions (limited functionality for server maps)
function toggleMiniMap() {
    console.log('MiniMap toggle - controlled by Folium map configuration');
}

function showPluginInfo() {
    alert('Server-generated map uses Folium plugins. Check the map controls for available functionality.');
}

function resetMapView() {
    // First try to auto-zoom to fit all polygons
    autoZoomToAllPolygons();

    // If no polygons found or user wants complete reset, reload page
    setTimeout(function() {
        if (confirm('Reset to original view? This will reload the page.')) {
            window.location.reload();
        }
    }, 1000);
}

// Function to remove all polygon layers from the map
function removeAllPolygons() {
    if (!confirm('Are you sure you want to remove all loaded polygons? This action cannot be undone.')) {
        return;
    }

    try {
        const mapElements = document.querySelectorAll('.folium-map');
        if (mapElements.length > 0) {
            const mapId = mapElements[0].id;
            if (window[mapId]) {
                const foliumMap = window[mapId];
                const layersToRemove = [];

                // Collect polygon layers to remove
                foliumMap.eachLayer(function(layer) {
                    // Skip base tile layers
                    if (layer._url && layer._url.includes('tile')) {
                        return;
                    }

                    // Remove GeoJSON layers and feature layers
                    if (layer.getBounds && typeof layer.getBounds === 'function') {
                        layersToRemove.push(layer);
                    }
                });

                // Remove collected layers
                layersToRemove.forEach(function(layer) {
                    foliumMap.removeLayer(layer);
                });

                // Clear related data
                window.geoJsonData = null;
                window.hasData = false;
                window.selectedPolygons = [];

                // Update UI states
                updatePolygonManagementState();
                updateAdjacencyButtonState();

                // Update table info
                const tableInfo = document.getElementById('tableInfo');
                if (tableInfo) {
                    tableInfo.textContent = 'No data loaded';
                }

                console.log(`Removed ${layersToRemove.length} polygon layers from the map`);
                alert(`Successfully removed ${layersToRemove.length} polygon layers from the map.`);
            }
        }
    } catch (error) {
        console.error('Error removing polygon layers:', error);
        alert('Error removing polygons. Please try refreshing the page.');
    }
}

function toggleCoordinates() {
    console.log('Coordinates toggle - controlled by Folium MousePosition plugin');
}

function toggleTreeLayers() {
    console.log('TreeLayers toggle - controlled by Folium LayerControl');
}

function refreshTreeLayers() {
    console.log('TreeLayers refresh - controlled by Folium LayerControl');
}

function expandAllTreeLayers() {
    console.log('TreeLayers expand - controlled by Folium LayerControl');
}

function collapseAllTreeLayers() {
    console.log('TreeLayers collapse - controlled by Folium LayerControl');
}

// Cadastral data loading functionality
async function loadCadastralSelection() {
    const loadButton = document.getElementById('loadCadastralBtn');
    if (!window.currentFileSelection || window.currentFileSelection.length === 0) {
        alert('Please select municipalities and file types first.');
        return;
    }

    const originalText = loadButton.textContent;
    loadButton.disabled = true;
    loadButton.textContent = 'Loading...';

    try {
        const requestData = {
            file_paths: window.currentFileSelection.map(file => file.path),
            file_types: getSelectedFileTypes()
        };

        const response = await fetch('/api/v1/load-cadastral-files', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(requestData)
        });

        if (response.ok) {
            const result = await response.json();

            const tableInfo = document.getElementById('tableInfo');
            if (tableInfo) {
                const totalLayers = result.total_layers || 0;
                const totalFeatures = result.total_features_count || 0;
                tableInfo.textContent = `Total: ${totalFeatures} features across ${totalLayers} layers`;
            }

            const newLayers = result.successful_layers || 0;
            const newFeatures = result.features_count || 0;
            const totalLayers = result.total_layers || 0;
            const totalFeatures = result.total_features_count || 0;

            const successMessage = `Successfully added ${newLayers} new layers with ${newFeatures} new features.\n\n` +
                `New layers:\n${Object.keys(result.layers || {})
                    .filter(layer => !result.layers[layer].error)
                    .map(layer => `- ${layer} (${result.layers[layer].feature_count} features)`)
                    .join('\n')}\n\n` +
                `Total: ${totalFeatures} features across ${totalLayers} layers.\n` +
                `New layers have been added to the existing map without removing current layers.`;

            alert(successMessage);

            // Update UI state instead of reloading
            updatePolygonManagementState();

            // Auto-zoom to include new and existing polygons
            setTimeout(function() {
                if (typeof autoZoomToAllPolygons === 'function') {
                    autoZoomToAllPolygons();
                }
            }, 1000);

            // Update window data for compatibility
            window.hasData = true;

            console.log('Successfully loaded new cadastral layers without page reload');

        } else {
            const errorData = await response.json();
            throw new Error(errorData.detail || 'Failed to load cadastral files');
        }

    } catch (error) {
        console.error('Error loading cadastral files:', error);
        alert(`Error loading files: ${error.message}`);
    } finally {
        loadButton.disabled = false;
        loadButton.textContent = originalText;
    }
}

// loadCadastralStructure function moved to map.js for consolidation

// populateCadastralSelects function moved to map.js for consolidation

// updateProvincesSelect function moved to map.js for consolidation
// updateMunicipalitiesSelect function moved to map.js for consolidation
// updateSelectionSummary function moved to map.js for consolidation
// hideSelectionSummary function moved to map.js for consolidation
// getSelectedFileTypes function moved to map.js for consolidation
// toggleFileType function moved to map.js for consolidation

// Initialize the Folium interface when DOM is loaded
document.addEventListener('DOMContentLoaded', function() {
    console.log('Folium map interface initializing...');

    // Update table info with data status
    const tableInfo = document.getElementById('tableInfo');
    if (tableInfo) {
        if (window.hasData) {
            tableInfo.textContent = 'Cadastral data loaded in server-generated map';
        } else {
            tableInfo.textContent = 'No data loaded - using default map view';
        }
    }

    // Initialize polygon selection
    initializePolygonSelection();

    // Ensure map view is active by default
    showMapView();

    // Load cadastral structure using shared function from map.js
    if (typeof loadCadastralData === 'function') {
        loadCadastralData().then(() => {
            if (window.cadastralData) {
                console.log('Cadastral structure loaded successfully');
                window.cadastralDataCache = window.cadastralData;
            } else {
                console.log('Failed to load cadastral structure - selects will remain empty');
            }
        });
    } else {
        console.warn('loadCadastralData function not available from map.js');
    }

    // Update UI elements based on data availability
    if (window.hasData) {
        console.log('Cadastral data available in server-generated map');
    } else {
        console.log('No cadastral data - showing default map view');
    }
});
