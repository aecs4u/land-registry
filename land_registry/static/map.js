// Land Registry Map JavaScript
// Initialize Leaflet map with all map providers
let map, currentGeoJsonLayer, drawnItems, drawControl;

// Debug mode - set to true during development to enable console logging
const DEBUG_MODE = false;

// Conditional debug logging - only logs when DEBUG_MODE is true
function debugLog(...args) {
    if (DEBUG_MODE) {
        console.log('[MapJS]', ...args);
    }
}

// Selection management
let selectedPolygons = new Set();
let selectionEnabled = true;
let adjacentPolygonsFound = false;
let adjacentPolygonIndices = [];

// Polyline management
let drawnPolylines = [];
let selectedPolyline = null;
let polylineHistory = [];

// Function to create SVG stripe patterns
function createStripePattern(angle, color = '#3388ff') {
    const patternId = `stripe-pattern-${angle}-${Math.random().toString(36).substring(2, 11)}`;

    // Create SVG defs if it doesn't exist
    let svgDefs = document.querySelector('#map-patterns defs');
    if (!svgDefs) {
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.id = 'map-patterns';
        svg.style.position = 'absolute';
        svg.style.width = '0';
        svg.style.height = '0';
        svg.style.pointerEvents = 'none';

        svgDefs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
        svg.appendChild(svgDefs);
        document.body.appendChild(svg);
    }

    // Create pattern element
    const pattern = document.createElementNS('http://www.w3.org/2000/svg', 'pattern');
    pattern.id = patternId;
    pattern.setAttribute('patternUnits', 'userSpaceOnUse');
    pattern.setAttribute('width', '8');
    pattern.setAttribute('height', '8');
    pattern.setAttribute('patternTransform', `rotate(${angle})`);

    // Create background rectangle for better visibility
    const background = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    background.setAttribute('width', '8');
    background.setAttribute('height', '8');
    background.setAttribute('fill', color);
    background.setAttribute('fill-opacity', '0.1');

    // Create stripe lines
    const line1 = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    line1.setAttribute('x1', '0');
    line1.setAttribute('y1', '0');
    line1.setAttribute('x2', '0');
    line1.setAttribute('y2', '8');
    line1.setAttribute('stroke', color);
    line1.setAttribute('stroke-width', '1');

    const line2 = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    line2.setAttribute('x1', '4');
    line2.setAttribute('y1', '0');
    line2.setAttribute('x2', '4');
    line2.setAttribute('y2', '8');
    line2.setAttribute('stroke', color);
    line2.setAttribute('stroke-width', '1');

    pattern.appendChild(background);
    pattern.appendChild(line1);
    pattern.appendChild(line2);
    svgDefs.appendChild(pattern);

    return patternId;
}

// Function to get random angle in 45-degree steps
function getRandomStripeAngle() {
    const angles = [0, 45, 90, 135, 180, 225, 270, 315];
    return angles[Math.floor(Math.random() * angles.length)];
}

// Selection functions
function togglePolygonSelection(layer) {
    debugLog('togglePolygonSelection called. Selection enabled:', selectionEnabled);

    if (!selectionEnabled) {
        debugLog('Selection is disabled, returning');
        return;
    }

    const layerId = L.Util.stamp(layer);
    debugLog('Toggling polygon with ID:', layerId);

    if (selectedPolygons.has(layerId)) {
        // Deselect
        selectedPolygons.delete(layerId);
        applyDefaultStyle(layer);
        debugLog('Deselected polygon. Total selected:', selectedPolygons.size);
    } else {
        // Select
        selectedPolygons.add(layerId);
        applySelectedStyle(layer);
        debugLog('Selected polygon. Total selected:', selectedPolygons.size);
    }

    updateSelectionCounter();
}

function applySelectedStyle(layer) {
    const pathElement = layer.getElement();
    if (pathElement) {
        pathElement.style.stroke = '#ff6600';
        pathElement.style.strokeWidth = '4';
        pathElement.style.strokeOpacity = '1';
        pathElement.classList.add('selected-polygon');
    }
}

function applyDefaultStyle(layer) {
    const pathElement = layer.getElement();
    if (pathElement) {
        pathElement.style.stroke = '#3388ff';
        pathElement.style.strokeWidth = '2';
        pathElement.style.strokeOpacity = '1';
        pathElement.classList.remove('selected-polygon');
    }
}

function updateSelectionCounter() {
    const counter = document.getElementById('selectedCount');
    if (counter) {
        const count = selectedPolygons.size;
        counter.textContent = `${count} polygon${count !== 1 ? 's' : ''} selected`;
    }
    updateSelectionButtons();
}

function updateSelectionButtons() {
    const selectAllBtn = document.getElementById('selectAllBtn');
    const deselectAllBtn = document.getElementById('deselectAllBtn');

    const hasData = currentGeoJsonLayer && currentGeoJsonLayer.getLayers().length > 0;
    const hasSelections = selectedPolygons.size > 0;

    if (selectAllBtn) {
        selectAllBtn.disabled = !hasData || !selectionEnabled;
    }
    if (deselectAllBtn) {
        deselectAllBtn.disabled = !hasSelections || !selectionEnabled;
    }
}

function updateDataDependentButtons() {
    const hasData = currentGeoJsonLayer && currentGeoJsonLayer.getLayers().length > 0;

    // Update selection buttons
    updateSelectionButtons();

    // Update adjacency buttons
    updateAdjacencyButtons();

    // Update display control buttons
    const selectionInfoBtn = document.querySelector('[onclick="toggleSelectionInfo()"]');
    const polygonsVisibilityBtn = document.querySelector('[onclick="togglePolygonsVisibility()"]');

    if (selectionInfoBtn) {
        selectionInfoBtn.disabled = !hasData;
    }
    if (polygonsVisibilityBtn) {
        polygonsVisibilityBtn.disabled = !hasData;
    }

    debugLog('Data-dependent buttons updated. Has data:', hasData);
}

function updateAdjacencyButtons() {
    const selectAdjacentBtn = document.getElementById('selectAdjacentBtn');
    const clearSelectionBtn = document.getElementById('clearSelectionBtn');
    const showAllPolygonsBtn = document.getElementById('showAllPolygonsBtn');

    // These buttons are only enabled when adjacent polygons have been found
    if (selectAdjacentBtn) {
        selectAdjacentBtn.disabled = !adjacentPolygonsFound;
    }
    if (clearSelectionBtn) {
        clearSelectionBtn.disabled = !adjacentPolygonsFound;
    }
    if (showAllPolygonsBtn) {
        showAllPolygonsBtn.disabled = !adjacentPolygonsFound;
    }
}

function selectAllPolygons() {
    if (!currentGeoJsonLayer) return;

    currentGeoJsonLayer.eachLayer(function(layer) {
        const layerId = L.Util.stamp(layer);
        selectedPolygons.add(layerId);
        applySelectedStyle(layer);
    });

    updateSelectionCounter();
}

function deselectAllPolygons() {
    if (!currentGeoJsonLayer) return;

    currentGeoJsonLayer.eachLayer(function(layer) {
        const layerId = L.Util.stamp(layer);
        selectedPolygons.delete(layerId);
        applyDefaultStyle(layer);
    });

    selectedPolygons.clear();
    updateSelectionCounter();
}

function toggleSelectionMode() {
    selectionEnabled = !selectionEnabled;
    updateSelectionToggleButton();
}


function updateSelectionToggleButton() {
    debugLog('updateSelectionToggleButton called. Selection enabled:', selectionEnabled);
    const btn = document.getElementById('toggleSelectionBtn');
    if (btn) {
        const iconSpan = btn.querySelector('.toggle-icon');
        const stateSpan = btn.querySelector('.toggle-state');

        if (selectionEnabled) {
            btn.classList.add('active');
            btn.classList.remove('inactive');
            if (iconSpan) iconSpan.textContent = '✏️';
            if (stateSpan) stateSpan.textContent = 'ON';
            btn.title = 'Disable polygon selection';
            debugLog('Button set to active state');
        } else {
            btn.classList.remove('active');
            btn.classList.add('inactive');
            if (iconSpan) iconSpan.textContent = '🚫';
            if (stateSpan) stateSpan.textContent = 'OFF';
            btn.title = 'Enable polygon selection';
            debugLog('Button set to inactive state');
        }
    } else {
        debugLog('Toggle button not found!');
    }
    // Update selection buttons when toggle state changes
    updateSelectionButtons();
}

// Polyline management functions
function saveDrawnPolylines() {
    if (drawnPolylines.length === 0) {
        alert('No polylines to save');
        return;
    }

    const polylinesGeoJSON = {
        type: 'FeatureCollection',
        features: drawnPolylines.map((polyline, index) => ({
            type: 'Feature',
            properties: {
                id: index,
                name: `Polyline ${index + 1}`,
                created: new Date().toISOString()
            },
            geometry: {
                type: 'LineString',
                coordinates: polyline.getLatLngs().map(latlng => [latlng.lng, latlng.lat])
            }
        }))
    };

    // Send to server to save
    fetch('/save-drawn-polylines/', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(polylinesGeoJSON)
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            alert(`Saved ${drawnPolylines.length} polylines successfully`);
        } else {
            alert('Error saving polylines: ' + data.error);
        }
    })
    .catch(error => {
        console.error('Error saving polylines:', error);
        alert('Error saving polylines');
    });
}

function deleteSelectedPolyline() {
    if (!selectedPolyline) {
        alert('No polyline selected');
        return;
    }

    // Add to history for undo
    polylineHistory.push({
        action: 'delete',
        polyline: selectedPolyline,
        index: drawnPolylines.indexOf(selectedPolyline)
    });

    // Remove from map and array
    map.removeLayer(selectedPolyline);
    drawnPolylines = drawnPolylines.filter(p => p !== selectedPolyline);

    selectedPolyline = null;
    updatePolylineControls();
    updatePolylineList();
}

function undoLastPolyline() {
    if (polylineHistory.length === 0) {
        alert('No actions to undo');
        return;
    }

    const lastAction = polylineHistory.pop();

    if (lastAction.action === 'delete') {
        // Restore deleted polyline
        lastAction.polyline.addTo(map);
        drawnPolylines.splice(lastAction.index, 0, lastAction.polyline);
        addPolylineClickHandler(lastAction.polyline);
    } else if (lastAction.action === 'create') {
        // Remove created polyline
        map.removeLayer(lastAction.polyline);
        drawnPolylines = drawnPolylines.filter(p => p !== lastAction.polyline);
        if (selectedPolyline === lastAction.polyline) {
            selectedPolyline = null;
        }
    }

    updatePolylineControls();
    updatePolylineList();
}

function clearAllPolylines() {
    if (drawnPolylines.length === 0) {
        alert('No polylines to clear');
        return;
    }

    if (confirm(`Are you sure you want to delete all ${drawnPolylines.length} polylines?`)) {
        // Add to history for undo
        polylineHistory.push({
            action: 'clear',
            polylines: [...drawnPolylines]
        });

        // Remove all polylines
        drawnPolylines.forEach(polyline => {
            map.removeLayer(polyline);
        });

        drawnPolylines = [];
        selectedPolyline = null;
        updatePolylineControls();
        updatePolylineList();
    }
}

function addPolylineClickHandler(polyline) {
    polyline.on('click', function(e) {
        L.DomEvent.stopPropagation(e);

        // Deselect previous selection
        if (selectedPolyline) {
            selectedPolyline.setStyle({ color: '#3388ff', weight: 3 });
        }

        // Select new polyline
        selectedPolyline = polyline;
        polyline.setStyle({ color: '#ff6600', weight: 5 });

        updatePolylineControls();
        updatePolylineStats();
    });
}

function updatePolylineControls() {
    const deleteBtn = document.getElementById('deletePolylineBtn');
    const undoBtn = document.getElementById('undoPolylineBtn');
    const clearBtn = document.getElementById('clearPolylinesBtn');
    const saveBtn = document.getElementById('savePolylinesBtn');

    if (deleteBtn) deleteBtn.disabled = !selectedPolyline;
    if (undoBtn) undoBtn.disabled = polylineHistory.length === 0;
    if (clearBtn) clearBtn.disabled = drawnPolylines.length === 0;
    if (saveBtn) saveBtn.disabled = drawnPolylines.length === 0;
}

function updatePolylineList() {
    const listElement = document.getElementById('drawnPolylinesList');
    if (!listElement) return;

    if (drawnPolylines.length === 0) {
        listElement.innerHTML = '<p>No polylines drawn yet</p>';
    } else {
        const listItems = drawnPolylines.map((polyline, index) => {
            const isSelected = polyline === selectedPolyline;
            const length = calculatePolylineLength(polyline);
            return `
                <div class="polyline-item ${isSelected ? 'selected' : ''}" onclick="selectPolylineFromList(${index})">
                    <strong>Polyline ${index + 1}</strong>
                    <br>Length: ${length.toFixed(2)} km
                    ${isSelected ? ' (Selected)' : ''}
                </div>
            `;
        }).join('');
        listElement.innerHTML = listItems;
    }
}

function updatePolylineStats() {
    const countElement = document.getElementById('mappingPolylineCount') || document.getElementById('polylineCount');
    const selectedElement = document.getElementById('selectedPolyline');

    if (countElement) countElement.textContent = drawnPolylines.length;
    if (selectedElement) {
        if (selectedPolyline) {
            const index = drawnPolylines.indexOf(selectedPolyline) + 1;
            selectedElement.textContent = `Polyline ${index}`;
        } else {
            selectedElement.textContent = 'None';
        }
    }
}

function selectPolylineFromList(index) {
    if (index >= 0 && index < drawnPolylines.length) {
        // Deselect previous
        if (selectedPolyline) {
            selectedPolyline.setStyle({ color: '#3388ff', weight: 3 });
        }

        // Select new
        selectedPolyline = drawnPolylines[index];
        selectedPolyline.setStyle({ color: '#ff6600', weight: 5 });

        updatePolylineControls();
        updatePolylineStats();
        updatePolylineList();
    }
}

function calculatePolylineLength(polyline) {
    const latlngs = polyline.getLatLngs();
    let totalLength = 0;

    for (let i = 0; i < latlngs.length - 1; i++) {
        totalLength += latlngs[i].distanceTo(latlngs[i + 1]);
    }

    return totalLength / 1000; // Convert to kilometers
}

// Function definitions for controls (map interface functions)
window.zoomIn = function() {
    map.zoomIn();
};

window.zoomOut = function() {
    map.zoomOut();
};

window.fitToPolygons = function() {
    if (currentGeoJsonLayer) {
        map.fitBounds(currentGeoJsonLayer.getBounds());
    }
};

window.fitToSelected = function() {
    if (currentGeoJsonLayer) {
        map.fitBounds(currentGeoJsonLayer.getBounds());
    }
};

window.togglePolygonSelectionMode = function() {
    selectionEnabled = !selectionEnabled;
    updateSelectionToggleButton();
    debugLog('Polygon selection mode toggled. Enabled:', selectionEnabled);
};

window.selectAllPolygons = function() {
    selectAllPolygons();
};

window.deselectAllPolygons = function() {
    deselectAllPolygons();
};

window.toggleSelectionInfo = function() {
    const hasData = currentGeoJsonLayer && currentGeoJsonLayer.getLayers().length > 0;
    if (!hasData) {
        debugLog('No data loaded - selection info cannot be toggled');
        return;
    }

    const selectedCount = document.getElementById('selectedCount');
    if (selectedCount) {
        if (selectedCount.style.display === 'none') {
            selectedCount.style.display = 'block';
            debugLog('Selection info shown');
        } else {
            selectedCount.style.display = 'none';
            debugLog('Selection info hidden');
        }
    }
};

window.togglePolygonsVisibility = function() {
    const hasData = currentGeoJsonLayer && currentGeoJsonLayer.getLayers().length > 0;
    if (!hasData) {
        debugLog('No data loaded - polygons visibility cannot be toggled');
        return;
    }

    if (currentGeoJsonLayer) {
        if (map.hasLayer(currentGeoJsonLayer)) {
            map.removeLayer(currentGeoJsonLayer);
            debugLog('Polygons hidden');
        } else {
            map.addLayer(currentGeoJsonLayer);
            debugLog('Polygons shown');
        }
    }
};

function clearAllData() {
    // Clear current data
    if (currentGeoJsonLayer && map.hasLayer(currentGeoJsonLayer)) {
        map.removeLayer(currentGeoJsonLayer);
    }
    currentGeoJsonLayer = null;

    // Clear selections
    selectedPolygons.clear();

    // Update UI
    updateSelectionCounter();
    updateDataDependentButtons();

    debugLog('All data cleared, buttons disabled');
}

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
    debugLog('Filtering active auctions');
};

window.filterAuctionsByType = function() {
    const typeFilter = document.getElementById('auctionTypeFilter').value;
    debugLog('Filtering by type:', typeFilter);
};

window.filterAuctionsByPrice = function() {
    const maxPrice = document.getElementById('maxPriceFilter').value;
    debugLog('Filtering by max price:', maxPrice);
};

window.startDrawingMode = function() {
    const polygonBtn = document.querySelector('[title="Draw a polygon"]');
    if (polygonBtn) {
        polygonBtn.click();
    }
};

window.stopDrawingMode = function() {
    map.removeControl(drawControl);
    map.addControl(drawControl);
};

window.clearAllDrawings = function() {
    drawnItems.clearLayers();
    updateDrawingControls();
};

// Enhanced Drawing Management Functions

function updateDrawingControls() {
    const count = drawnItems.getLayers().length;
    const drawingInfo = document.getElementById('drawingInfo');
    if (drawingInfo) {
        drawingInfo.textContent = `${count} drawings created`;
    }

    // Update button states
    const saveBtn = document.getElementById('saveDrawingsBtn');
    const clearBtn = document.getElementById('clearDrawingsBtn');
    const exportBtn = document.getElementById('exportDrawingsBtn');

    if (saveBtn) saveBtn.disabled = count === 0;
    if (clearBtn) clearBtn.disabled = count === 0;
    if (exportBtn) exportBtn.disabled = count === 0;

    // Update stats and features list
    updateDrawingStats();
    updateFeaturesListDisplay();
}

async function saveDrawnPolygons() {
    if (!window.authManager || !window.authManager.isSignedIn()) {
        alert('Please sign in to save your drawings');
        return;
    }

    if (drawnItems.getLayers().length === 0) {
        alert('No drawings to save');
        return;
    }

    const geojson = {
        type: 'FeatureCollection',
        features: []
    };

    drawnItems.eachLayer(function(layer) {
        const feature = layer.toGeoJSON();
        if (layer.feature && layer.feature.properties) {
            feature.properties = layer.feature.properties;
        }
        geojson.features.push(feature);
    });

    try {
        const response = await window.authManager.authenticatedFetch('/api/v1/save-drawn-polygons', {
            method: 'POST',
            body: JSON.stringify({
                geojson: geojson,
                timestamp: new Date().toISOString(),
                user_id: window.authManager.getUserId()
            })
        });

        const data = await response.json();
        if (data.success) {
            alert(`Successfully saved ${geojson.features.length} drawings`);
        } else {
            throw new Error(data.error || 'Failed to save drawings');
        }
    } catch (error) {
        console.error('Error saving drawings:', error);
        if (error.message.includes('Authentication')) {
            alert('Session expired. Please sign in again.');
        } else {
            alert('Error saving drawings: ' + error.message);
        }
    }
}

async function loadDrawnPolygons() {
    if (!window.authManager || !window.authManager.isSignedIn()) {
        alert('Please sign in to load your saved drawings');
        return;
    }

    try {
        const response = await window.authManager.authenticatedFetch('/api/v1/load-drawn-polygons');
        const data = await response.json();

        if (data.success && data.geojson) {
            // Clear existing drawings
            drawnItems.clearLayers();

            // Load saved drawings
            data.geojson.features.forEach(feature => {
                const layer = L.geoJSON(feature, {
                    style: function(feature) {
                        return {
                            color: '#3388ff',
                            weight: 4,
                            opacity: 0.8,
                            fillOpacity: 0.4
                        };
                    }
                });

                // Add feature properties back to layer
                layer.eachLayer(function(sublayer) {
                    sublayer.feature = feature;
                    drawnItems.addLayer(sublayer);
                });
            });

            updateDrawingControls();
            alert(`Loaded ${data.geojson.features.length} saved drawings`);
        } else {
            alert('No saved drawings found');
        }
    } catch (error) {
        console.error('Error loading drawings:', error);
        if (error.message.includes('Authentication')) {
            alert('Session expired. Please sign in again.');
        } else {
            alert('Error loading drawings: ' + error.message);
        }
    }
}

function exportDrawingsAsGeoJSON() {
    if (drawnItems.getLayers().length === 0) {
        alert('No drawings to export');
        return;
    }

    const geojson = {
        type: 'FeatureCollection',
        features: []
    };

    drawnItems.eachLayer(function(layer) {
        const feature = layer.toGeoJSON();
        if (layer.feature && layer.feature.properties) {
            feature.properties = layer.feature.properties;
        }
        geojson.features.push(feature);
    });

    // Create download link
    const dataStr = JSON.stringify(geojson, null, 2);
    const dataBlob = new Blob([dataStr], {type: 'application/json'});
    const url = URL.createObjectURL(dataBlob);

    const link = document.createElement('a');
    link.href = url;
    link.download = `drawn_polygons_${new Date().toISOString().slice(0, 10)}.geojson`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
}

function importDrawingsFromGeoJSON() {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.geojson,.json';

    input.onchange = function(e) {
        const file = e.target.files[0];
        if (!file) return;

        const reader = new FileReader();
        reader.onload = function(e) {
            try {
                const geojson = JSON.parse(e.target.result);

                // Validate GeoJSON structure
                if (!geojson.type || geojson.type !== 'FeatureCollection' || !geojson.features) {
                    throw new Error('Invalid GeoJSON format');
                }

                // Load features
                geojson.features.forEach(feature => {
                    const layer = L.geoJSON(feature, {
                        style: function(feature) {
                            return {
                                color: '#3388ff',
                                weight: 4,
                                opacity: 0.8,
                                fillOpacity: 0.4
                            };
                        }
                    });

                    layer.eachLayer(function(sublayer) {
                        sublayer.feature = feature;
                        drawnItems.addLayer(sublayer);
                    });
                });

                updateDrawingControls();
                alert(`Imported ${geojson.features.length} drawings from ${file.name}`);
            } catch (error) {
                console.error('Error importing GeoJSON:', error);
                alert('Error importing file: ' + error.message);
            }
        };
        reader.readAsText(file);
    };

    input.click();
}

// Additional drawing management functions

function fitDrawingsToView() {
    if (drawnItems.getLayers().length === 0) {
        alert('No drawings to fit to view');
        return;
    }

    try {
        const group = new L.featureGroup(drawnItems.getLayers());
        map.fitBounds(group.getBounds(), { padding: [20, 20] });
        debugLog('Fitted', drawnItems.getLayers().length, 'drawings to view');
    } catch (error) {
        console.error('Error fitting drawings to view:', error);
    }
}

function updateDrawingStats() {
    const layers = drawnItems.getLayers();
    let polygonCount = 0;
    let polylineCount = 0;
    let markerCount = 0;

    layers.forEach(layer => {
        if (layer.feature && layer.feature.properties) {
            const type = layer.feature.properties.type;
            switch (type) {
                case 'polygon':
                case 'rectangle':
                case 'circle':
                    polygonCount++;
                    break;
                case 'polyline':
                    polylineCount++;
                    break;
                case 'marker':
                    markerCount++;
                    break;
            }
        }
    });

    // Update stat displays
    const polygonCountEl = document.getElementById('polygonCount');
    const polylineCountEl = document.getElementById('mappingPolylineCount') || document.getElementById('polylineCount');
    const markerCountEl = document.getElementById('markerCount');

    if (polygonCountEl) polygonCountEl.textContent = polygonCount;
    if (polylineCountEl) polylineCountEl.textContent = polylineCount;
    if (markerCountEl) markerCountEl.textContent = markerCount;
}

function updateFeaturesListDisplay() {
    const featuresContainer = document.querySelector('.features-container');
    if (!featuresContainer) return;

    const layers = drawnItems.getLayers();

    if (layers.length === 0) {
        featuresContainer.innerHTML = '<p class="no-features">No drawings yet. Use the drawing tools on the map to create polygons, lines, or markers.</p>';
        return;
    }

    const featuresHtml = layers.map(layer => {
        const props = layer.feature?.properties || {};
        const type = props.type || 'unknown';
        const id = props.id || 'unnamed';
        const created = props.created ? new Date(props.created).toLocaleString() : 'Unknown';
        const area = props.area && props.area !== 'N/A' ?
                     (typeof props.area === 'number' ? (props.area / 1000000).toFixed(2) + ' km²' : props.area) : '';

        return `
            <div class="feature-item" data-layer-id="${id}">
                <div class="feature-info">
                    <div class="feature-type">${type.charAt(0).toUpperCase() + type.slice(1)}</div>
                    <div class="feature-details">
                        Created: ${created}
                        ${area ? `<br>Area: ${area}` : ''}
                    </div>
                </div>
                <div class="feature-actions">
                    <button class="feature-btn zoom" onclick="zoomToFeature('${id}')">🔍</button>
                    <button class="feature-btn delete" onclick="deleteFeature('${id}')">🗑️</button>
                </div>
            </div>
        `;
    }).join('');

    featuresContainer.innerHTML = featuresHtml;
}

function zoomToFeature(featureId) {
    drawnItems.eachLayer(layer => {
        if (layer.feature?.properties?.id === featureId) {
            const bounds = layer.getBounds ? layer.getBounds() : L.latLngBounds([layer.getLatLng()]);
            map.fitBounds(bounds, { padding: [50, 50] });

            // Temporarily highlight the feature
            const originalStyle = layer.options;
            layer.setStyle({ color: '#ff6b6b', weight: 6, opacity: 1 });
            setTimeout(() => {
                layer.setStyle(originalStyle);
            }, 2000);
        }
    });
}

function deleteFeature(featureId) {
    if (!confirm('Are you sure you want to delete this feature?')) return;

    drawnItems.eachLayer(layer => {
        if (layer.feature?.properties?.id === featureId) {
            drawnItems.removeLayer(layer);
            updateDrawingControls();
        }
    });
}

// Enhanced updateDrawingControls function
// NOTE: updateDrawingControls defined earlier in file (line 652)

// Global functions for button access
window.saveDrawnPolygons = saveDrawnPolygons;
window.loadDrawnPolygons = loadDrawnPolygons;
window.exportDrawingsAsGeoJSON = exportDrawingsAsGeoJSON;
window.importDrawingsFromGeoJSON = importDrawingsFromGeoJSON;
window.fitDrawingsToView = fitDrawingsToView;
window.zoomToFeature = zoomToFeature;
window.deleteFeature = deleteFeature;

window.toggleLegend = function() {
    const legend = document.querySelector('.leaflet-control-layers');
    if (legend) {
        legend.style.display = legend.style.display === 'none' ? 'block' : 'none';
    }
};

// NOTE: toggleSelectionInfo and togglePolygonsVisibility defined earlier in file (lines 500 and 519)

window.toggleBasemapVisibility = function() {
    const layerControl = document.querySelector('.leaflet-control-layers');
    if (layerControl) {
        layerControl.style.display = layerControl.style.display === 'none' ? 'block' : 'none';
    }
};

window.saveDrawingsToJSON = function() {
    const drawings = drawnItems.toGeoJSON();
    const blob = new Blob([JSON.stringify(drawings, null, 2)], {type: 'application/json'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `drawn_polygons_${new Date().toISOString().split('T')[0]}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
};

window.triggerLoadDrawings = function() {
    const fileInput = document.createElement('input');
    fileInput.type = 'file';
    fileInput.accept = '.qpkg,.gpkg,.json,.geojson';
    fileInput.style.display = 'none';
    fileInput.onchange = function(e) {
        const file = e.target.files[0];
        if (!file) return;

        const formData = new FormData();
        formData.append('file', file);

        fetch('/api/v1/upload-qpkg/', {
            method: 'POST',
            body: formData
        })
        .then(response => response.json())
        .then(data => {
            if (data.geojson || data.success) {
                location.reload();
            }
        })
        .catch(error => console.error('Upload error:', error));
    };

    document.body.appendChild(fileInput);
    fileInput.click();
    document.body.removeChild(fileInput);
};

window.exportSelection = function() {
    if (currentGeoJsonLayer) {
        const geojsonData = currentGeoJsonLayer.toGeoJSON();
        const blob = new Blob([JSON.stringify(geojsonData, null, 2)], {type: 'application/json'});
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `selected_polygons_${new Date().toISOString().split('T')[0]}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    } else {
        alert('No data to export');
    }
};

window.importSelection = function() {
    triggerLoadDrawings();
};

window.switchBasemap = function() {
    const selector = document.getElementById('basemapSelector');
    if (selector) {
        const selectedValue = selector.value;
        debugLog('Switching to basemap:', selectedValue);
    }
};

// Plugin management functions
window.toggleMiniMap = function() {
    const miniMapControl = document.querySelector('.leaflet-control-minimap');
    if (miniMapControl) {
        const toggleBtn = miniMapControl.querySelector('.leaflet-control-minimap-toggle-display');
        if (toggleBtn) {
            toggleBtn.click();
            debugLog('MiniMap toggled');
        }
    } else {
        debugLog('MiniMap not found');
    }
};

window.showPluginInfo = function() {
    const plugins = [
        { name: 'Geocoder', status: typeof L.Control.Geocoder !== 'undefined' },
        { name: 'MiniMap', status: typeof L.Control.MiniMap !== 'undefined' },
        { name: 'Locate', status: typeof L.Control.Locate !== 'undefined' },
        { name: 'MousePosition', status: typeof L.Control.MousePosition !== 'undefined' },
        { name: 'Measure', status: typeof L.control.measure !== 'undefined' },
        { name: 'Fullscreen', status: typeof L.control.fullscreen !== 'undefined' },
        { name: 'TreeLayers', status: typeof L.Control.TreeLayers !== 'undefined' },
        { name: 'Draw', status: typeof L.Control.Draw !== 'undefined' }
    ];

    const activePlugins = plugins.filter(p => p.status).map(p => p.name);
    const inactivePlugins = plugins.filter(p => !p.status).map(p => p.name);

    let message = `Active Plugins (${activePlugins.length}):\n${activePlugins.join(', ')}\n\n`;
    if (inactivePlugins.length > 0) {
        message += `Inactive Plugins (${inactivePlugins.length}):\n${inactivePlugins.join(', ')}`;
    }

    alert(message);
    debugLog('Plugin Status:', plugins);
};

window.resetMapView = function() {
    if (map) {
        // Reset to Italy bounds
        map.setView([41.8719, 12.5674], 6);
        debugLog('Map view reset to default');
    }
};

window.toggleCoordinates = function() {
    const mousePositionControl = document.querySelector('.leaflet-control-mouseposition');
    if (mousePositionControl) {
        const isVisible = mousePositionControl.style.display !== 'none';
        mousePositionControl.style.display = isVisible ? 'none' : 'block';
        debugLog('Coordinate display toggled:', !isVisible);
    } else {
        debugLog('Mouse position control not found');
    }
};

// Update plugin status indicators in the sidebar
function updatePluginStatusIndicators() {
    const pluginChecks = [
        { name: 'Geocoder', check: () => typeof L.Control.Geocoder !== 'undefined' },
        { name: 'MiniMap', check: () => typeof L.Control.MiniMap !== 'undefined' },
        { name: 'Locate', check: () => typeof L.Control.Locate !== 'undefined' },
        { name: 'Coordinates', check: () => typeof L.Control.MousePosition !== 'undefined' },
        { name: 'Measure', check: () => typeof L.control.measure !== 'undefined' },
        { name: 'Fullscreen', check: () => typeof L.control.fullscreen !== 'undefined' },
        { name: 'Layers', check: () => typeof L.Control.TreeLayers !== 'undefined' },
        { name: 'Drawing', check: () => typeof L.Control.Draw !== 'undefined' }
    ];

    pluginChecks.forEach(plugin => {
        const pluginItems = document.querySelectorAll('.plugin-item');
        pluginItems.forEach(item => {
            const nameElement = item.querySelector('.plugin-name');
            if (nameElement && nameElement.textContent.toLowerCase() === plugin.name.toLowerCase()) {
                const isActive = plugin.check();
                item.classList.remove('active', 'inactive');
                item.classList.add(isActive ? 'active' : 'inactive');
                item.title = isActive ?
                    `${plugin.name} - Active and available` :
                    `${plugin.name} - Not available or failed to load`;
            }
        });
    });

    debugLog('Plugin status indicators updated');
}

// TreeLayers management functions
window.toggleTreeLayers = function() {
    const treeControl = document.querySelector('.leaflet-control-treelayers');
    if (treeControl) {
        const isHidden = treeControl.style.display === 'none';
        treeControl.style.display = isHidden ? 'block' : 'none';
        debugLog('TreeLayers control toggled:', !isHidden ? 'visible' : 'hidden');
    }
};

window.expandAllTreeLayers = function() {
    if (window.treeLayersControl && typeof window.treeLayersControl.expandAll === 'function') {
        window.treeLayersControl.expandAll();
        debugLog('All TreeLayers expanded');
    }
};

window.collapseAllTreeLayers = function() {
    if (window.treeLayersControl && typeof window.treeLayersControl.collapseAll === 'function') {
        window.treeLayersControl.collapseAll();
        debugLog('All TreeLayers collapsed');
    }
};

window.refreshTreeLayers = function() {
    if (window.treeLayersControl) {
        window.treeLayersControl.refresh();
        debugLog('TreeLayers control refreshed');
    }
};

// Selection control functions
window.changeSelectionMode = function() {
    const mode = document.getElementById('selectionMode').value;
    debugLog('Selection mode changed to:', mode);
};

window.findAdjacencyForSelected = async function() {
    if (selectedPolygons.size === 0) {
        alert('Please select at least one polygon first');
        return;
    }

    if (!currentGeoJsonLayer) {
        alert('No data loaded');
        return;
    }

    const method = document.getElementById('adjacencyMethod')?.value || 'touches';
    debugLog('Finding adjacent polygons for selected features using method:', method);

    try {
        // Get the first selected layer ID from selectedPolygons Set
        const selectedLayerId = Array.from(selectedPolygons)[0];
        
        // Find the actual layer and its feature data
        let selectedLayer = null;
        let featureIndex = 0;
        let foundIndex = 0;
        
        currentGeoJsonLayer.eachLayer(function(layer) {
            const layerId = L.Util.stamp(layer);
            if (layerId === selectedLayerId) {
                selectedLayer = layer;
                featureIndex = foundIndex;
            }
            foundIndex++;
        });

        if (!selectedLayer || !selectedLayer.feature) {
            alert('Could not find the selected polygon data');
            return;
        }

        // Extract feature_id from properties or use the index
        const feature = selectedLayer.feature;
        const featureId = feature.properties?.feature_id ?? feature.properties?.id ?? featureIndex;
        const geometry = feature.geometry;

        debugLog('Selected feature_id:', featureId, 'geometry type:', geometry?.type);

        // Call the backend adjacency endpoint with correct path and payload
        const response = await fetch('/api/v1/get-adjacent-polygons/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                feature_id: featureId,
                geometry: geometry,
                touch_method: method
            })
        });

        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            throw new Error(errorData.detail || `HTTP error! status: ${response.status}`);
        }

        const data = await response.json();
        debugLog('Adjacency analysis result:', data);

        if (data.adjacent_ids && data.adjacent_ids.length > 0) {
            // Adjacent polygons found - enable the buttons
            adjacentPolygonsFound = true;
            adjacentPolygonIndices = data.adjacent_ids;
            updateAdjacencyButtons();

            // Highlight the adjacent polygons
            debugLog(`Found ${data.adjacent_ids.length} adjacent polygons:`, data.adjacent_ids);
            alert(`Found ${data.adjacent_ids.length} adjacent polygons`);
        } else {
            // No adjacent polygons found
            adjacentPolygonsFound = false;
            adjacentPolygonIndices = [];
            updateAdjacencyButtons();
            debugLog('No adjacent polygons found');
            alert('No adjacent polygons found');
        }

    } catch (error) {
        console.error('Error finding adjacent polygons:', error);
        alert('Error finding adjacent polygons: ' + error.message);
        adjacentPolygonsFound = false;
        adjacentPolygonIndices = [];
        updateAdjacencyButtons();
    }
};


window.clearSelection = function() {
    // Clear all selected polygons
    selectedPolygons.clear();

    // Reset visual styles for all polygons
    if (currentGeoJsonLayer) {
        currentGeoJsonLayer.eachLayer(function(layer) {
            applyDefaultStyle(layer);
        });
    }

    // Reset adjacency state
    adjacentPolygonsFound = false;
    adjacentPolygonIndices = [];

    // Update UI
    updateSelectionCounter();
    updateAdjacencyButtons();

    debugLog('Selection cleared');
};

window.toggleSelectAdjacentPolygons = function() {
    if (!adjacentPolygonsFound || adjacentPolygonIndices.length === 0) {
        debugLog('No adjacent polygons found to select');
        return;
    }

    if (!currentGeoJsonLayer) {
        debugLog('No data layer available');
        return;
    }

    // Get all layers as an array for index-based access
    const layers = [];
    currentGeoJsonLayer.eachLayer(function(layer) {
        layers.push(layer);
    });

    // Check if any adjacent polygons are currently selected
    let adjacentSelected = 0;
    adjacentPolygonIndices.forEach(index => {
        if (index < layers.length) {
            const layer = layers[index];
            const layerId = L.Util.stamp(layer);
            if (selectedPolygons.has(layerId)) {
                adjacentSelected++;
            }
        }
    });

    // If all or most adjacent polygons are selected, deselect them
    // Otherwise, select all adjacent polygons
    const shouldSelect = adjacentSelected < adjacentPolygonIndices.length / 2;

    debugLog(`Adjacent polygons selected: ${adjacentSelected}/${adjacentPolygonIndices.length}`);
    debugLog(`Action: ${shouldSelect ? 'Select' : 'Deselect'} all adjacent polygons`);

    // Toggle selection for each adjacent polygon
    adjacentPolygonIndices.forEach(index => {
        if (index < layers.length) {
            const layer = layers[index];
            const layerId = L.Util.stamp(layer);

            if (shouldSelect) {
                // Select the polygon
                if (!selectedPolygons.has(layerId)) {
                    selectedPolygons.add(layerId);
                    applySelectedStyle(layer);
                }
            } else {
                // Deselect the polygon
                if (selectedPolygons.has(layerId)) {
                    selectedPolygons.delete(layerId);
                    applyDefaultStyle(layer);
                }
            }
        }
    });

    // Update UI
    updateSelectionCounter();
    updateSelectionButtons();

    debugLog(`${shouldSelect ? 'Selected' : 'Deselected'} ${adjacentPolygonIndices.length} adjacent polygons`);
};

window.showAllPolygons = function() {
    // Show all polygons in the current dataset
    if (currentGeoJsonLayer) {
        currentGeoJsonLayer.addTo(map);
        map.fitBounds(currentGeoJsonLayer.getBounds());
    }
    debugLog('Showing all polygons');
};

// Custom Leaflet Controls
function addNavigationControls(map) {
    const NavigationControl = L.Control.extend({
        options: {
            position: 'topright'
        },
        onAdd: function(map) {
            const container = L.DomUtil.create('div', 'leaflet-control-navigation');
            container.innerHTML = `
                <button onclick="fitToPolygons()" title="Fit map to show all polygons">🎯</button>
                <button id="fitSelectedBtn" onclick="fitToSelected()" disabled title="Fit map to selected polygons only">📍</button>
            `;
            L.DomEvent.disableClickPropagation(container);
            return container;
        }
    });
    map.addControl(new NavigationControl());
}

function addToolsControls(map) {
    const ToolsControl = L.Control.extend({
        options: {
            position: 'topright'
        },
        onAdd: function(map) {
            const container = L.DomUtil.create('div', 'leaflet-control-tools');
            container.innerHTML = `
                <button id="toggleSelectionBtn" onclick="togglePolygonSelectionMode()" class="toggle-button active" title="Enable/disable polygon selection">
                    <span class="toggle-icon">✏️</span>
                    <span class="toggle-state">ON</span>
                </button>
                <button onclick="selectAllPolygons()" id="selectAllBtn" title="Select all polygons">📋</button>
                <button onclick="deselectAllPolygons()" id="deselectAllBtn" title="Deselect all polygons">🔲</button>
                <button onclick="clearAllDrawings()" title="Clear all drawn polygons">🗑️</button>
            `;
            L.DomEvent.disableClickPropagation(container);
            return container;
        }
    });
    map.addControl(new ToolsControl());
}

function addDisplayControls(map) {
    const DisplayControl = L.Control.extend({
        options: {
            position: 'topright'
        },
        onAdd: function(map) {
            const container = L.DomUtil.create('div', 'leaflet-control-display');
            container.innerHTML = `
                <button onclick="toggleLegend()" title="Show/hide map legend">📋</button>
                <button onclick="toggleSelectionInfo()" title="Show/hide selection information">ℹ️</button>
                <button onclick="togglePolygonsVisibility()" title="Show/hide all polygons">👁️</button>
            `;
            L.DomEvent.disableClickPropagation(container);
            return container;
        }
    });
    map.addControl(new DisplayControl());
}

function addAdjacencyControls(map) {
    const AdjacencyControl = L.Control.extend({
        options: {
            position: 'topright'
        },
        onAdd: function(map) {
            const container = L.DomUtil.create('div', 'leaflet-control-adjacency');
            container.innerHTML = `
                <button onclick="showAllPolygons()" id="showAllPolygonsBtn" class="adjacency-show-btn" title="Show/Hide All Adjacent Polygons" disabled></button>
                <button onclick="toggleSelectAdjacentPolygons()" id="selectAdjacentBtn" class="adjacency-select-btn" title="Select/Deselect All Adjacent Polygons" disabled></button>
                <button onclick="clearSelection()" id="clearSelectionBtn" class="adjacency-delete-btn" title="Delete All Adjacent Polygons" disabled></button>
            `;
            L.DomEvent.disableClickPropagation(container);
            return container;
        }
    });
    map.addControl(new AdjacencyControl());
}

function addAuctionControls(map) {
    const AuctionControl = L.Control.extend({
        options: {
            position: 'topright'
        },
        onAdd: function(map) {
            const container = L.DomUtil.create('div', 'leaflet-control-auction');
            container.innerHTML = `
                <button onclick="toggleAuctionLayer()" title="Toggle auction properties layer">🏠</button>
                <button onclick="filterActiveAuctions()" title="Show only active auctions">⏰</button>
                <button onclick="loadAuctionProperties()" title="Load auction properties">📊</button>
            `;
            L.DomEvent.disableClickPropagation(container);
            return container;
        }
    });
    map.addControl(new AuctionControl());
}
function addDataControls(map) {
    const DataControl = L.Control.extend({
        options: {
            position: 'topright'
        },
        onAdd: function(map) {
            const container = L.DomUtil.create('div', 'leaflet-control-data');
            container.innerHTML = `
                <button onclick="saveDrawingsToJSON()" title="Export drawn polygons to JSON">💾</button>
                <button onclick="triggerLoadDrawings()" title="Import polygons from JSON file">📁</button>
                <button id="exportSelectionBtn" onclick="exportSelection()" disabled title="Export selected polygons">📤</button>
                <button onclick="importSelection()" title="Import polygon selection">📥</button>
            `;
            L.DomEvent.disableClickPropagation(container);
            return container;
        }
    });
    map.addControl(new DataControl());
}

// Load GeoJSON data from injected window variable
function loadGeoJsonData() {
    if (window.geoJsonData) {
        try {
            const geoJsonData = window.geoJsonData;
            if (geoJsonData && geoJsonData.features && geoJsonData.features.length > 0) {
                currentGeoJsonLayer = L.geoJSON(geoJsonData, {
                    style: function(feature) {
                        return {
                            color: '#3388ff',
                            weight: 2,
                            fillOpacity: 0.4
                        };
                    },
                    onEachFeature: function(feature, layer) {
                        // Apply stripe pattern to each polygon
                        layer.on('add', function() {
                            const pathElement = layer.getElement();
                            if (pathElement) {
                                const angle = getRandomStripeAngle();
                                const patternId = createStripePattern(angle, '#3388ff');
                                pathElement.style.fill = `url(#${patternId})`;
                            }
                        });

                        // Add click handler for selection
                        layer.on('click', function(e) {
                            debugLog('Polygon clicked!');
                            L.DomEvent.stopPropagation(e);
                            togglePolygonSelection(layer);
                        });

                        if (feature.properties) {
                            let popupContent = '<strong>Feature Properties:</strong><br>';
                            for (let key in feature.properties) {
                                popupContent += `<strong>${key}:</strong> ${feature.properties[key]}<br>`;
                            }
                            layer.bindPopup(popupContent);
                        }
                    }
                }).addTo(map);

                // Fit map to data bounds
                map.fitBounds(currentGeoJsonLayer.getBounds());

                // Update search control with loaded data
                if (window.searchControl && currentGeoJsonLayer) {
                    window.searchControl.setLayer(currentGeoJsonLayer);
                }

                // Initialize selection counter
                selectedPolygons.clear();
                adjacentPolygonsFound = false;
                adjacentPolygonIndices = [];
                updateSelectionCounter();
                updateDataDependentButtons();

                // Update tree layers control with loaded data
                if (window.treeLayersControl && currentGeoJsonLayer) {
                    try {
                        // Update the TreeLayers control with current data
                        const overlayLayers = window.treeLayersControl.getOverlayLayers();
                        if (overlayLayers['📊 Data Layers']) {
                            overlayLayers['📊 Data Layers']['🏛️ Current Cadastral Data'] = currentGeoJsonLayer;
                            window.treeLayersControl.refresh();
                            debugLog('TreeLayers control updated with cadastral data');
                        }
                    } catch (e) {
                        console.warn('Could not update TreeLayers control:', e);
                    }
                }
            }
        } catch (error) {
            console.error('Error loading GeoJSON data:', error);
        }
    }
}

// Initialize the map with all providers
function initializeMap() {
    console.log('[MapJS] initializeMap() called');
    debugLog('Initializing map...');

    // Prevent multiple initializations
    if (map) {
        console.log('[MapJS] Map already initialized, skipping...');
        debugLog('Map already initialized, skipping...');
        return;
    }

    const mapElement = document.getElementById('map');
    console.log('[MapJS] Map element found:', !!mapElement);
    debugLog('Map element found:', mapElement);
    debugLog('Map element dimensions:', mapElement ? `${mapElement.offsetWidth}x${mapElement.offsetHeight}` : 'N/A');

    if (!mapElement) {
        console.error('[MapJS] Cannot initialize map - #map element not found');
        return;
    }

    // Create map centered on Italy
    map = L.map('map').setView([41.8719, 12.5674], 6);
    console.log('[MapJS] Leaflet map created successfully');
    debugLog('Leaflet map created:', map);

    // Add custom bottom-center position for controls
    const corners = map._controlCorners;
    const container = map._controlContainer;

    function createCorner(vSide, hSide) {
        const className = 'leaflet-' + vSide + ' leaflet-' + hSide;
        corners[vSide + hSide] = L.DomUtil.create('div', className, container);
    }

    createCorner('bottom', 'center');

    // Define all map providers
    const mapProviders = {
        'OpenStreetMap': L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '© OpenStreetMap contributors'
        }),
        '📍 Google Maps': L.tileLayer('https://mt1.google.com/vt/lyrs=m&x={x}&y={y}&z={z}', {
            attribution: '© Google'
        }),
        '🛰️ Google Satellite': L.tileLayer('https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}', {
            attribution: '© Google'
        }),
        '⛰️ Google Terrain': L.tileLayer('https://mt1.google.com/vt/lyrs=p&x={x}&y={y}&z={z}', {
            attribution: '© Google'
        }),
        '🌍 Google Hybrid': L.tileLayer('https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}', {
            attribution: '© Google'
        }),
        '🚌 Google Transit': L.tileLayer('https://mt1.google.com/vt/lyrs=m,transit&x={x}&y={y}&z={z}', {
            attribution: '© Google'
        }),
        '🚗 Google Traffic': L.tileLayer('https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}', {
            attribution: '© Google'
        }),
        '🌐 ESRI World Imagery': L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
            attribution: '© ESRI'
        }),
        '🏔️ ESRI Terrain': L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Terrain_Base/MapServer/tile/{z}/{y}/{x}', {
            attribution: '© ESRI'
        }),
        '⚪ CartoDB Light': L.tileLayer('https://cartodb-basemaps-{s}.global.ssl.fastly.net/light_all/{z}/{x}/{y}.png', {
            attribution: '© CartoDB'
        }),
        '⚫ CartoDB Dark': L.tileLayer('https://cartodb-basemaps-{s}.global.ssl.fastly.net/dark_all/{z}/{x}/{y}.png', {
            attribution: '© CartoDB'
        })
    };

    // Weather overlays
    const weatherOverlays = {
        '🌡️ Temperature': L.tileLayer('https://tile.openweathermap.org/map/temp_new/{z}/{x}/{y}.png?appid=b6907d289e10d714a6e88b30761fae22', {
            attribution: '© OpenWeatherMap'
        }),
        '🌧️ Precipitation': L.tileLayer('https://tile.openweathermap.org/map/precipitation_new/{z}/{x}/{y}.png?appid=b6907d289e10d714a6e88b30761fae22', {
            attribution: '© OpenWeatherMap'
        }),
        '💨 Wind Speed': L.tileLayer('https://tile.openweathermap.org/map/wind_new/{z}/{x}/{y}.png?appid=b6907d289e10d714a6e88b30761fae22', {
            attribution: '© OpenWeatherMap'
        }),
        '☁️ Cloud Coverage': L.tileLayer('https://tile.openweathermap.org/map/clouds_new/{z}/{x}/{y}.png?appid=b6907d289e10d714a6e88b30761fae22', {
            attribution: '© OpenWeatherMap'
        })
    };

    // Add default layer - Google Maps
    mapProviders['📍 Google Maps'].addTo(map);



    /* // MOVED TO folium-interface.js
    // Add drawing controls
    drawnItems = new L.FeatureGroup();
    map.addLayer(drawnItems);

    drawControl = new L.Control.Draw({
        position: 'bottomleft',
        draw: {
            polygon: {
                allowIntersection: false,
                showArea: true,
                drawError: {
                    color: '#e1e100',
                    message: '<strong>Oh snap!</strong> you can\'t draw that!'
                },
                shapeOptions: {
                    color: '#3388ff',
                    weight: 4,
                    opacity: 0.8,
                    fillOpacity: 0.4
                }
            },
            circle: {
                shapeOptions: {
                    color: '#662288',
                    opacity: 0.8,
                    weight: 4
                }
            },
            marker: true,
            circlemarker: false
        },
        edit: {
            featureGroup: drawnItems,
            remove: true
        }
    });
    map.addControl(drawControl);

    // Add custom Export control
    const ExportControl = L.Control.extend({
        options: {
            position: 'topleft'
        },

        onAdd: function (map) {
            const container = L.DomUtil.create('div', 'leaflet-bar leaflet-control');
            const button = L.DomUtil.create('a', 'leaflet-control-export', container);
            button.href = '#';
            button.title = 'Export GeoJSON';
            button.innerHTML = '📤';
            button.style.fontSize = '18px';
            button.style.display = 'flex';
            button.style.alignItems = 'center';
            button.style.justifyContent = 'center';
            
            L.DomEvent.on(button, 'click', function (e) {
                L.DomEvent.stop(e);
                if (typeof exportDrawingsAsGeoJSON === 'function') {
                    exportDrawingsAsGeoJSON();
                } else {
                    alert('Export function not available');
                }
            });

            return container;
        }
    });

    map.addControl(new ExportControl());

    // Add drawing event listeners
    map.on('draw:created', function(e) {
        const type = e.layerType;
        const layer = e.layer;

        // Add unique ID to each drawn feature
        layer.feature = {
            type: 'Feature',
            properties: {
                id: 'drawn_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9),
                type: type,
                created: new Date().toISOString(),
                area: type === 'polygon' || type === 'rectangle' || type === 'circle' ?
                      L.GeometryUtil ? L.GeometryUtil.geodesicArea(layer.getLatLngs()[0]) : 'N/A' : null
            }
        };
    });
    */

    /* // MOVED TO folium-interface.js
    map.on('draw:created', function(e) {
        const type = e.layerType;
        const layer = e.layer;
        // ...
        drawnItems.addLayer(layer);
        updateDrawingControls();
    });

    map.on('draw:edited', function(e) {
        // ...
        updateDrawingControls();
    });

    map.on('draw:deleted', function(e) {
        // ...
        updateDrawingControls();
    });
    */

    map.on('draw:deleted', function(e) {
        const layers = e.layers;
        debugLog('Deleted', layers.getLayers().length, 'features');
        updateDrawingControls();
    });

    // Initialize drawing controls state
    updateDrawingControls();

    // Fullscreen control
    L.control.fullscreen({
        position: 'topright',
        title: 'View Fullscreen',
        titleCancel: 'Exit Fullscreen',
        content: null,
        forceSeparateButton: true
    }).addTo(map);

    // Add measure control
    L.control.measure({
        position: 'topleft'
    }).addTo(map);

    // Add fit buttons to the existing zoom control after map loads
    setTimeout(() => {
        console.log('[MapJS] Adding custom zoom buttons...');

        // Update zoom control icons with more intuitive ones
        const zoomInBtn = document.querySelector('.leaflet-control-zoom-in');
        const zoomOutBtn = document.querySelector('.leaflet-control-zoom-out');
        const fullscreenBtn = document.querySelector('.leaflet-control-fullscreen-button');

        console.log('[MapJS] Found zoom buttons:', { zoomIn: !!zoomInBtn, zoomOut: !!zoomOutBtn });

        if (zoomInBtn) {
            zoomInBtn.innerHTML = '+';
            zoomInBtn.title = 'Zoom In';
            zoomInBtn.style.fontSize = '18px';
            zoomInBtn.style.fontWeight = 'bold';
        }
        if (zoomOutBtn) {
            zoomOutBtn.innerHTML = '−';
            zoomOutBtn.title = 'Zoom Out';
            zoomOutBtn.style.fontSize = '18px';
            zoomOutBtn.style.fontWeight = 'bold';
        }
        if (fullscreenBtn) {
            fullscreenBtn.innerHTML = '⛶';
            fullscreenBtn.title = 'Toggle Fullscreen';
            fullscreenBtn.style.fontSize = '16px';
        }

        const zoomControl = document.querySelector('.leaflet-control-zoom');
        console.log('[MapJS] Found zoom control container:', !!zoomControl);

        if (zoomControl) {
            // Create Fit All button (zoom to all data)
            const fitAllBtn = L.DomUtil.create('a', 'leaflet-control-zoom-fit-all', zoomControl);
            fitAllBtn.innerHTML = '⊞';
            fitAllBtn.href = '#';
            fitAllBtn.title = 'Fit map to show all loaded data';
            fitAllBtn.setAttribute('role', 'button');
            fitAllBtn.setAttribute('aria-label', 'Fit map to show all polygons');

            // Create Fit Selected button (zoom to selection)
            const fitSelectedBtn = L.DomUtil.create('a', 'leaflet-control-zoom-fit-selected', zoomControl);
            fitSelectedBtn.innerHTML = '◎';
            fitSelectedBtn.href = '#';
            fitSelectedBtn.title = 'Fit map to selected polygons';
            fitSelectedBtn.setAttribute('role', 'button');
            fitSelectedBtn.setAttribute('aria-label', 'Fit map to selected polygons only');

            // Create Window Zoom button (box zoom)
            const boxZoomBtn = L.DomUtil.create('a', 'leaflet-control-zoom-box', zoomControl);
            boxZoomBtn.innerHTML = '⬚';
            boxZoomBtn.href = '#';
            boxZoomBtn.title = 'Draw a box to zoom into (hold Shift+drag also works)';
            boxZoomBtn.setAttribute('role', 'button');
            boxZoomBtn.setAttribute('aria-label', 'Window zoom - draw a rectangle to zoom');

            // Create Reset View button (zoom to Italy)
            const resetViewBtn = L.DomUtil.create('a', 'leaflet-control-zoom-reset', zoomControl);
            resetViewBtn.innerHTML = '🏠';
            resetViewBtn.href = '#';
            resetViewBtn.title = 'Reset to default Italy view';
            resetViewBtn.setAttribute('role', 'button');
            resetViewBtn.setAttribute('aria-label', 'Reset map view to Italy');

            // Add event handlers
            L.DomEvent.on(fitAllBtn, 'click', function(e) {
                L.DomEvent.stopPropagation(e);
                L.DomEvent.preventDefault(e);
                if (currentGeoJsonLayer) {
                    map.fitBounds(currentGeoJsonLayer.getBounds(), { padding: [20, 20] });
                } else {
                    // Default to Italy bounds if no data
                    map.fitBounds([[35.49, 6.63], [47.09, 18.52]]);
                }
            });

            L.DomEvent.on(fitSelectedBtn, 'click', function(e) {
                L.DomEvent.stopPropagation(e);
                L.DomEvent.preventDefault(e);
                // Fit to selected polygons if any are selected
                if (selectedPolygons.size > 0) {
                    const bounds = L.latLngBounds();
                    selectedPolygons.forEach(layer => {
                        if (layer.getBounds) {
                            bounds.extend(layer.getBounds());
                        }
                    });
                    if (bounds.isValid()) {
                        map.fitBounds(bounds, { padding: [30, 30] });
                    }
                } else if (currentGeoJsonLayer) {
                    // Fall back to all data if nothing selected
                    map.fitBounds(currentGeoJsonLayer.getBounds(), { padding: [20, 20] });
                }
            });

            // Box zoom state
            let boxZoomActive = false;
            let boxZoomStartPoint = null;
            let boxZoomRect = null;

            L.DomEvent.on(boxZoomBtn, 'click', function(e) {
                L.DomEvent.stopPropagation(e);
                L.DomEvent.preventDefault(e);

                boxZoomActive = !boxZoomActive;

                if (boxZoomActive) {
                    boxZoomBtn.classList.add('active');
                    boxZoomBtn.style.backgroundColor = '#3388ff';
                    boxZoomBtn.style.color = 'white';
                    map.getContainer().style.cursor = 'crosshair';
                    map.dragging.disable();

                    // Add mouse handlers for box zoom
                    map.on('mousedown', startBoxZoom);
                    map.on('mousemove', updateBoxZoom);
                    map.on('mouseup', endBoxZoom);
                } else {
                    deactivateBoxZoom();
                }
            });

            function startBoxZoom(e) {
                if (!boxZoomActive) return;
                boxZoomStartPoint = e.latlng;

                // Create rectangle for visual feedback
                boxZoomRect = L.rectangle([boxZoomStartPoint, boxZoomStartPoint], {
                    color: '#3388ff',
                    weight: 2,
                    fillOpacity: 0.2,
                    dashArray: '5, 5'
                }).addTo(map);
            }

            function updateBoxZoom(e) {
                if (!boxZoomActive || !boxZoomStartPoint || !boxZoomRect) return;
                boxZoomRect.setBounds([boxZoomStartPoint, e.latlng]);
            }

            function endBoxZoom(e) {
                if (!boxZoomActive || !boxZoomStartPoint) return;

                const bounds = L.latLngBounds(boxZoomStartPoint, e.latlng);

                // Remove the rectangle
                if (boxZoomRect) {
                    map.removeLayer(boxZoomRect);
                    boxZoomRect = null;
                }

                // Only zoom if the box is large enough
                if (bounds.isValid() && bounds.getNorthEast().distanceTo(bounds.getSouthWest()) > 100) {
                    map.fitBounds(bounds, { padding: [10, 10] });
                }

                boxZoomStartPoint = null;
                deactivateBoxZoom();
            }

            function deactivateBoxZoom() {
                boxZoomActive = false;
                boxZoomBtn.classList.remove('active');
                boxZoomBtn.style.backgroundColor = '';
                boxZoomBtn.style.color = '';
                map.getContainer().style.cursor = '';
                map.dragging.enable();

                map.off('mousedown', startBoxZoom);
                map.off('mousemove', updateBoxZoom);
                map.off('mouseup', endBoxZoom);

                if (boxZoomRect) {
                    map.removeLayer(boxZoomRect);
                    boxZoomRect = null;
                }
            }

            L.DomEvent.on(resetViewBtn, 'click', function(e) {
                L.DomEvent.stopPropagation(e);
                L.DomEvent.preventDefault(e);
                // Reset to default Italy view
                map.setView([41.9, 12.5], 6);
            });

            // Disable map dragging on buttons
            L.DomEvent.disableClickPropagation(fitAllBtn);
            L.DomEvent.disableClickPropagation(fitSelectedBtn);
            L.DomEvent.disableClickPropagation(boxZoomBtn);
            L.DomEvent.disableClickPropagation(resetViewBtn);

            console.log('[MapJS] Custom zoom buttons added successfully');
        } else {
            console.error('[MapJS] Zoom control not found! Cannot add custom buttons.');
            // Try to find what controls exist
            const allControls = document.querySelectorAll('.leaflet-control');
            console.log('[MapJS] Available controls:', Array.from(allControls).map(c => c.className));
        }
    }, 500);  // Increased timeout to ensure controls are rendered

    // Add geocoder control (search functionality)
    if (typeof L.Control.Geocoder !== 'undefined') {
        L.Control.geocoder({
            defaultMarkGeocode: false,
            position: 'topright'
        }).on('markgeocode', function(e) {
            const bbox = e.geocode.bbox;
            const poly = L.polygon([
                bbox.getSouthEast(),
                bbox.getNorthEast(),
                bbox.getNorthWest(),
                bbox.getSouthWest()
            ]).addTo(map);
            map.fitBounds(poly.getBounds());
        }).addTo(map);
    }

    // Add minimap control
    if (typeof L.Control.MiniMap !== 'undefined') {
        const osmUrl = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png';
        const osmAttrib = '© OpenStreetMap contributors';
        const miniMapLayer = new L.TileLayer(osmUrl, {minZoom: 0, maxZoom: 13, attribution: osmAttrib});
        const miniMap = new L.Control.MiniMap(miniMapLayer, {
            position: 'bottomleft',
            width: 150,
            height: 150,
            collapsedWidth: 25,
            collapsedHeight: 25,
            toggleDisplay: true
        }).addTo(map);
    }

    // Add LocateControl for user location
    if (typeof L.Control.Locate !== 'undefined') {
        const locateControl = L.control.locate({
            position: 'topleft',
            strings: {
                title: "Show me where I am",
            },
            locateOptions: {
                maxZoom: 16
            }
        }).addTo(map);
    }

    // Add Search Control
    if (typeof L.Control.Search !== 'undefined') {
        const searchControl = new L.Control.Search({
            position: 'topright',
            layer: null, // Will be set when data is loaded
            propertyName: 'properties', // Search in feature properties
            marker: false,
            moveToLocation: function(latlng, title, map) {
                map.setView(latlng, 16);
            },
            buildTip: function(text, val) {
                return '<b>' + text + '</b>';
            }
        });
        map.addControl(searchControl);

        // Store reference for later use when data is loaded
        window.searchControl = searchControl;
    }

    // Add Mouse Position Control
    if (typeof L.Control.MousePosition !== 'undefined') {
        const mousePositionControl = L.control.mousePosition({
            position: 'bottomright',
            separator: ' | ',
            emptyString: 'Unavailable',
            lngFirst: false,
            numDigits: 5,
            lngFormatter: function(num) {
                return 'Lng: ' + L.Util.formatNum(num, 5);
            },
            latFormatter: function(num) {
                return 'Lat: ' + L.Util.formatNum(num, 5);
            }
        }).addTo(map);
    }

    // Add TreeLayers Control (on the right side) - Enhanced Version
    if (typeof L.Control.TreeLayers !== 'undefined') {
        // Initialize with comprehensive base layers and overlay structure
        const baseLayers = {
            '🗺️ Base Maps': {
                '🌍 OpenStreetMap': mapProviders['OpenStreetMap'],
                '📍 Google Maps': mapProviders['📍 Google Maps'],
                '🛰️ Google Satellite': mapProviders['🛰️ Google Satellite'],
                '⛰️ Google Terrain': mapProviders['⛰️ Google Terrain'],
                '🌍 Google Hybrid': mapProviders['🌍 Google Hybrid'],
                '🌐 ESRI World Imagery': mapProviders['🌐 ESRI World Imagery'],
                '🏔️ ESRI Terrain': mapProviders['🏔️ ESRI Terrain'],
                '⚪ CartoDB Light': mapProviders['⚪ CartoDB Light'],
                '⚫ CartoDB Dark': mapProviders['⚫ CartoDB Dark']
            }
        };

        const overlayLayers = {
            '📊 Data Layers': {
                '🏛️ Current Cadastral Data': null, // Will be populated when data is loaded
                '✏️ Drawn Features': drawnItems,
                '🏠 Auction Properties': auctionMarkersGroup
            },
            '🌤️ Weather Overlays': {
                '🌡️ Temperature': weatherOverlays['🌡️ Temperature'],
                '🌧️ Precipitation': weatherOverlays['🌧️ Precipitation'],
                '💨 Wind Speed': weatherOverlays['💨 Wind Speed'],
                '☁️ Cloud Coverage': weatherOverlays['☁️ Cloud Coverage']
            }
        };

        const treeLayersControl = L.control.treeLayers(baseLayers, overlayLayers, {
            position: 'topright',
            collapsed: true
        }).addTo(map);

        // Store reference for later updates
        window.treeLayersControl = treeLayersControl;

        // Make TreeLayers more prominent with custom styling
        setTimeout(() => {
            const treeControl = document.querySelector('.leaflet-control-treelayers');
            if (treeControl) {
                treeControl.style.maxWidth = '300px';
                treeControl.style.minWidth = '250px';
                treeControl.style.background = 'rgba(255, 255, 255, 0.95)';
                treeControl.style.border = '2px solid #007cba';
                treeControl.style.borderRadius = '8px';
                treeControl.style.boxShadow = '0 4px 12px rgba(0,124,186,0.3)';
                debugLog('TreeLayers control enhanced and made more visible');
            }
        }, 500);
    }

    // Add custom navigation controls
    addNavigationControls(map);

    // Add custom tools controls
    addToolsControls(map);

    // Add custom display controls
    addDisplayControls(map);

    // Add custom data controls
    addDataControls(map);

    // Add custom adjacency controls
    addAdjacencyControls(map);

    // Add custom auction controls
    addAuctionControls(map);

    // Handle drawing events
    map.on(L.Draw.Event.CREATED, function(e) {
        const layer = e.layer;
        drawnItems.addLayer(layer);

        // Check if it's a polyline
        if (layer instanceof L.Polyline && !(layer instanceof L.Polygon)) {
            // Add to polylines array
            drawnPolylines.push(layer);

            // Add click handler for selection
            addPolylineClickHandler(layer);

            // Add to history for undo
            polylineHistory.push({
                action: 'create',
                polyline: layer
            });

            // Update controls and stats
            updatePolylineControls();
            updatePolylineStats();
            updatePolylineList();
        }
    });

    map.on(L.Draw.Event.DELETED, function(e) {
        const layers = e.layers;
        layers.eachLayer(function(layer) {
            // Check if deleted layer is a polyline
            if (drawnPolylines.includes(layer)) {
                // Add to history for undo
                polylineHistory.push({
                    action: 'delete',
                    polyline: layer,
                    index: drawnPolylines.indexOf(layer)
                });

                // Remove from polylines array
                drawnPolylines = drawnPolylines.filter(p => p !== layer);

                // Clear selection if it was selected
                if (selectedPolyline === layer) {
                    selectedPolyline = null;
                }

                // Update controls and stats
                updatePolylineControls();
                updatePolylineStats();
                updatePolylineList();
            }
        });
    });

    // Load existing data if available
    loadGeoJsonData();

    // Initialize polyline controls
    updatePolylineControls();
    updatePolylineStats();

    // Initialize toggle button state
    updateSelectionToggleButton();

    // Initialize data-dependent buttons (disabled initially)
    updateDataDependentButtons();

    // Initialize adjacency buttons as disabled
    adjacentPolygonsFound = false;
    updateAdjacencyButtons();

    // Update plugin status indicators
    updatePluginStatusIndicators();
}

// Original sidebar functionality
window.showUploadTab = function(tab) {
    // Hide all tabs
    document.getElementById('fileUpload').style.display = 'none';
    document.getElementById('cadastralSelection').style.display = 'none';

    // Remove active class from all tabs
    document.querySelectorAll('.upload-tab').forEach(t => t.classList.remove('active'));

    // Show selected tab
    if (tab === 'file') {
        document.getElementById('fileUpload').style.display = 'block';
        document.querySelector('.upload-tab').classList.add('active');
    } else {
        document.getElementById('cadastralSelection').style.display = 'block';
        document.querySelectorAll('.upload-tab')[1].classList.add('active');
    }
};

window.uploadFile = function() {
    const fileInput = document.getElementById('fileInput');
    const file = fileInput.files[0];
    if (!file) return;

    const formData = new FormData();
    formData.append('file', file);

    fetch('/api/v1/upload-qpkg/', {
        method: 'POST',
        body: formData
    })
    .then(response => response.json())
    .then(data => {
        if (data.geojson) {
            location.reload();
        }
    })
    .catch(error => console.error('Upload error:', error));
};

window.toggleFileType = function(element, fileType) {
    element.classList.toggle('selected');
    const checkbox = element.querySelector('input[type="checkbox"]');
    checkbox.checked = element.classList.contains('selected');
    updateSelectionSummary();
};

window.loadCadastralSelection = async function() {
    const regionsSelect = document.getElementById('cadastralRegions');
    const provincesSelect = document.getElementById('cadastralProvinces');
    const municipalitiesSelect = document.getElementById('cadastralMunicipalities');
    const fileTypesContainer = document.getElementById('cadastralFileTypes');
    const loadBtn = document.getElementById('loadCadastralBtn');

    if (!regionsSelect || !provincesSelect || !municipalitiesSelect || !fileTypesContainer || !cadastralData) {
        console.error('Missing required elements or cadastral data');
        return;
    }

    // Get selected file types
    const selectedFileTypes = [];
    const fileTypeCheckboxes = fileTypesContainer.querySelectorAll('input[type="checkbox"]:checked');
    fileTypeCheckboxes.forEach(checkbox => {
        selectedFileTypes.push(checkbox.value);
    });

    if (selectedFileTypes.length === 0) {
        alert('Please select at least one file type (MAP or PLE).');
        return;
    }

    // Get selected municipalities
    const selectedMunicipalities = Array.from(municipalitiesSelect.selectedOptions).map(option => option.value);

    if (selectedMunicipalities.length === 0) {
        alert('Please select at least one municipality.');
        return;
    }

    // Build list of file paths
    const filePaths = [];

    selectedMunicipalities.forEach(municipalityKey => {
        const [region, province, municipality] = municipalityKey.split('|');

        if (cadastralData[region] && cadastralData[region][province] && cadastralData[region][province][municipality]) {
            const municipalityData = cadastralData[region][province][municipality];
            const files = municipalityData.files || [];

            files.forEach(filename => {
                // Check if file matches selected file types
                const matchesFileType = selectedFileTypes.some(fileType => {
                    return filename.toLowerCase().includes(fileType.toLowerCase());
                });

                if (matchesFileType) {
                    const filePath = `ITALIA/${region}/${province}/${municipality}/${filename}`;
                    filePaths.push(filePath);
                }
            });
        }
    });

    if (filePaths.length === 0) {
        alert('No files found for the selected criteria.');
        return;
    }

    // Disable button and show loading state
    loadBtn.disabled = true;
    loadBtn.textContent = 'Loading...';

    try {
        debugLog(`Loading ${filePaths.length} cadastral files directly from S3:`, filePaths);

        // Clear existing map layers
        clearMap();

        // Load files sequentially from S3
        const loadedLayers = [];
        let successfulLoads = 0;

        for (let i = 0; i < filePaths.length; i++) {
            const filePath = filePaths[i];
            loadBtn.textContent = `Loading... (${i + 1}/${filePaths.length})`;

            try {
                debugLog(`Loading file ${i + 1}/${filePaths.length} via backend API: ${filePath}`);

                // Use backend API endpoint to load and process GPKG file
                const apiUrl = `/api/v1/load-cadastral-files/${encodeURIComponent(filePath)}`;
                debugLog(`Fetching from backend API: ${apiUrl}`);

                const response = await fetch(apiUrl, {
                    method: 'GET',
                    headers: {
                        'Accept': 'application/json'
                    }
                });

                if (!response.ok) {
                    console.warn(`Failed to load ${filePath} via API: HTTP ${response.status}`);
                    continue; // Skip this file and continue with next
                }

                // Get the response data from the API
                const responseData = await response.json();

                if (responseData && responseData.success && responseData.geojson) {
                    const geojsonData = responseData.geojson;
                    const layerData = {
                        filename: responseData.filename || filePath.split('/').pop(),
                        feature_count: responseData.feature_count || (geojsonData.features ? geojsonData.features.length : 0),
                        geojson: geojsonData
                    };

                    // Add the layer to our collection
                    loadedLayers.push(layerData);
                    successfulLoads++;

                    // Add to map immediately
                    addGeoJsonToMap(geojsonData, {
                        name: layerData.filename || `Layer ${i + 1}`,
                        style: getLayerStyle(i)
                    });

                    debugLog(`Successfully loaded ${layerData.filename} with ${layerData.feature_count} features`);
                } else {
                    console.warn(`Failed to load GPKG file via API: ${filePath}`, responseData);
                }

            } catch (fileError) {
                console.warn(`Error loading file ${filePath} via API: ${fileError.message}`);
                continue; // Skip this file and continue with next
            }
        }

        // Final processing after all files
        if (successfulLoads > 0) {
            // Fit map to combined bounds of ONLY the newly loaded layers
            if (window.map && loadedLayers.length > 0) {
                debugLog(`Zooming to ${loadedLayers.length} newly loaded layers only`);
                fitMapToNewLayers(loadedLayers);
            }


            // Show success message
            const totalFeatures = loadedLayers.reduce((sum, layer) => sum + layer.feature_count, 0);
            alert(`Successfully loaded ${successfulLoads}/${filePaths.length} cadastral files with ${totalFeatures} features.`);

            // Update table info if table view is active
            const tableInfo = document.getElementById('tableInfo');
            if (tableInfo) {
                tableInfo.textContent = `${totalFeatures} features loaded`;
            }

            // Switch to map view
            showMapView();
        } else {
            alert(`No geospatial data could be loaded. Files checked:\n${filePaths.map(p => `/api/v1/load-cadastral-files/${p}`).join('\n')}`);
        }

    } catch (error) {
        console.error('Error loading cadastral files:', error);
        alert(`Error loading cadastral files: ${error.message}`);
    } finally {
        // Re-enable button
        loadBtn.disabled = false;
        loadBtn.textContent = 'Load Selected Files';
    }
};

// Helper functions for loading cadastral data
function clearMap() {
    if (currentGeoJsonLayer) {
        map.removeLayer(currentGeoJsonLayer);
        currentGeoJsonLayer = null;
    }
    // Clear any other dynamic layers
    map.eachLayer(function(layer) {
        if (layer !== map.getDefaultTileLayer &&
            layer.options && layer.options.cadastralLayer) {
            map.removeLayer(layer);
        }
    });
}

function addGeoJsonToMap(geojson, options = {}) {
    if (!map || !geojson) return;

    const layer = L.geoJSON(geojson, {
        style: function(feature) {
            return {
                color: options.color || '#3388ff',
                weight: 2,
                opacity: 1,
                fillOpacity: 0.4
            };
        },
        onEachFeature: function(feature, layer) {
            // Apply stripe pattern to each polygon
            layer.on('add', function() {
                const pathElement = layer.getElement();
                if (pathElement) {
                    const angle = getRandomStripeAngle();
                    const patternId = createStripePattern(angle, options.color || '#3388ff');
                    pathElement.style.fill = `url(#${patternId})`;
                }
            });

            // Add click handler for selection
            layer.on('click', function(e) {
                debugLog('Polygon clicked (addGeoJsonToMap)!');
                L.DomEvent.stopPropagation(e);
                togglePolygonSelection(layer);
            });

            // Add popup with feature properties - prioritizing cadastral data
            if (feature.properties) {
                const popupContent = formatCadastralPopup(feature.properties);
                layer.bindPopup(popupContent, { maxWidth: 350 });
            }
        },
        cadastralLayer: true // Mark as cadastral layer for identification
    });

    layer.addTo(map);

    // Store as current layer for fitting bounds
    currentGeoJsonLayer = layer;

    // Update search control with new layer
    if (window.searchControl) {
        window.searchControl.setLayer(layer);
    }

    // Initialize selection counter for new data
    selectedPolygons.clear();
    adjacentPolygonsFound = false;
    adjacentPolygonIndices = [];
    updateSelectionCounter();
    updateDataDependentButtons();

    // Update tree layers control with new layer
    if (window.treeLayersControl) {
        const layerName = options.name || 'New Cadastral Layer';
        // Add layer to tree control
        try {
            const overlayLayers = window.treeLayersControl.getOverlayLayers();
            if (!overlayLayers['📊 Data Layers']) {
                overlayLayers['📊 Data Layers'] = {};
            }
            overlayLayers['📊 Data Layers'][`🏛️ ${layerName}`] = layer;
            window.treeLayersControl.refresh();
            debugLog(`TreeLayers control updated with new layer: ${layerName}`);
        } catch (e) {
            console.warn('Could not update tree layers control:', e);
        }
    }

    return layer;
}

function getLayerStyle(index) {
    const colors = ['#3388ff', '#ff6b6b', '#4ecdc4', '#45b7d1', '#f9ca24', '#6c5ce7', '#fdcb6e'];
    return {
        color: colors[index % colors.length],
        weight: 2,
        opacity: 1,
        fillOpacity: 0.3
    };
}

/**
 * Format cadastral popup content with priority fields
 * Parses and displays foglio, particella, alterno, subalterno from cadastral references
 * @param {Object} properties - Feature properties from GeoJSON
 * @returns {string} HTML content for popup
 */
function formatCadastralPopup(properties) {
    if (!properties) return 'No properties available';

    let html = '<div class="cadastral-popup">';

    // Priority cadastral fields to display prominently
    const cadastralRef = properties.NATIONALCADASTRALREFERENCE || properties.NATIONALCADASTRALZONINGREFERENCE;
    const label = properties.LABEL;
    const adminUnit = properties.ADMINISTRATIVEUNIT;
    const levelName = properties.LEVELNAME;

    // Parse cadastral reference to extract components
    // Format: CODE_FOGLIO.PARTICELLA or CODE_FOGLIO.PARTICELLA.SUBALTERNO
    let foglio = null, particella = null, subalterno = null, alterno = null;

    if (cadastralRef) {
        // Try to parse the reference
        // Examples: A018_000100, A018_000100.1, A018_000100.1.2
        const parts = cadastralRef.split('_');
        if (parts.length >= 2) {
            const cadastralParts = parts[1].split('.');
            foglio = cadastralParts[0] ? cadastralParts[0].replace(/^0+/, '') || '0' : null;  // Remove leading zeros
            particella = cadastralParts[1] || null;
            subalterno = cadastralParts[2] || null;
            alterno = cadastralParts[3] || null;
        }
    }

    // Header section with main cadastral info
    html += '<div class="popup-header">';
    if (levelName) {
        html += `<span class="level-badge">${levelName}</span>`;
    }
    if (adminUnit) {
        html += `<span class="admin-unit">${adminUnit}</span>`;
    }
    html += '</div>';

    // Main cadastral data section
    html += '<div class="cadastral-data">';

    // Label (usually the main identifier shown on map)
    if (label) {
        html += `<div class="data-row primary"><span class="label">Etichetta:</span><span class="value">${label}</span></div>`;
    }

    // Foglio
    if (foglio) {
        html += `<div class="data-row"><span class="label">Foglio:</span><span class="value">${foglio}</span></div>`;
    }

    // Particella
    if (particella) {
        html += `<div class="data-row"><span class="label">Particella:</span><span class="value">${particella}</span></div>`;
    }

    // Subalterno (if present)
    if (subalterno) {
        html += `<div class="data-row"><span class="label">Subalterno:</span><span class="value">${subalterno}</span></div>`;
    }

    // Alterno (if present)
    if (alterno) {
        html += `<div class="data-row"><span class="label">Alterno:</span><span class="value">${alterno}</span></div>`;
    }

    html += '</div>';

    // Full reference (collapsed by default for detailed info)
    if (cadastralRef) {
        html += `<div class="data-row reference"><span class="label">Riferimento:</span><span class="value small">${cadastralRef}</span></div>`;
    }

    // Additional properties section (collapsible)
    const excludedKeys = ['geometry', 'NATIONALCADASTRALREFERENCE', 'NATIONALCADASTRALZONINGREFERENCE',
                          'LABEL', 'ADMINISTRATIVEUNIT', 'LEVELNAME', 'gml_id', 'lowerCorner', 'upperCorner',
                          'INSPIREID_LOCALID', 'INSPIREID_NAMESPACE', 'LEVELNAME_LOCALE'];

    const additionalProps = Object.entries(properties)
        .filter(([key]) => !excludedKeys.includes(key) && properties[key] !== null && properties[key] !== '')
        .map(([key, value]) => `<div class="data-row small"><span class="label">${formatPropertyName(key)}:</span><span class="value">${value}</span></div>`)
        .join('');

    if (additionalProps) {
        html += '<details class="additional-props"><summary>Altri dettagli</summary>' + additionalProps + '</details>';
    }

    html += '</div>';

    return html;
}

/**
 * Format property name for display (convert UPPERCASE_NAME to Title Case)
 */
function formatPropertyName(name) {
    return name.toLowerCase()
        .split('_')
        .map(word => word.charAt(0).toUpperCase() + word.slice(1))
        .join(' ');
}

function fitMapToGeoJson(geojson) {
    if (!map || !geojson) return;

    try {
        const layer = L.geoJSON(geojson);
        const bounds = layer.getBounds();
        if (bounds.isValid()) {
            map.fitBounds(bounds, { padding: [10, 10] });
        }
    } catch (error) {
        console.warn('Could not fit map to GeoJSON bounds:', error);
    }
}

/**
 * Fit map to the combined bounds of newly loaded layers only
 * This zooms the map to show exactly the new data that was just imported
 * @param {Array} newLayers - Array of layer objects with geojson property (newly loaded only)
 */
function fitMapToNewLayers(newLayers) {
    if (!map || !newLayers || newLayers.length === 0) return;


    try {
        // Create a combined bounds from all layers
        let combinedBounds = null;

        newLayers.forEach(layerData => {
            if (layerData.geojson) {
                const layer = L.geoJSON(layerData.geojson);
                const layerBounds = layer.getBounds();

                if (layerBounds.isValid()) {
                    if (combinedBounds === null) {
                        combinedBounds = L.latLngBounds(layerBounds);
                    } else {
                        combinedBounds.extend(layerBounds);
                    }
                }
            }
        });

        // Fit to combined bounds with padding
        if (combinedBounds && combinedBounds.isValid()) {
            map.fitBounds(combinedBounds, {
                padding: [20, 20],
                maxZoom: 18  // Prevent zooming in too close on small areas
            });
            debugLog(`Fitted map to combined bounds of ${newLayers.length} newly loaded layers`);
        }
    } catch (error) {
        console.warn('Could not fit map to combined layer bounds:', error);
        // Fallback: try to fit to first layer
        if (newLayers[0] && newLayers[0].geojson) {
            fitMapToGeoJson(newLayers[0].geojson);
        }
    }
}

function updateSelectionSummary() {
    const regionsSelect = document.getElementById('cadastralRegions');
    const provincesSelect = document.getElementById('cadastralProvinces');
    const municipalitiesSelect = document.getElementById('cadastralMunicipalities');
    const fileTypesContainer = document.getElementById('cadastralFileTypes');
    const selectionSummary = document.getElementById('selectionSummary');
    const summaryContent = document.getElementById('summaryContent');
    const filesList = document.getElementById('filesList');
    const loadBtn = document.getElementById('loadCadastralBtn');

    if (!regionsSelect || !provincesSelect || !municipalitiesSelect || !selectionSummary || !cadastralData) {
        return;
    }

    // Get selected items
    const selectedRegions = Array.from(regionsSelect.selectedOptions).map(option => option.value);
    const selectedProvinces = Array.from(provincesSelect.selectedOptions).map(option => option.value);
    const selectedMunicipalities = Array.from(municipalitiesSelect.selectedOptions).map(option => option.value);

    // Get selected file types
    const selectedFileTypes = [];
    if (fileTypesContainer) {
        fileTypesContainer.querySelectorAll('input[type="checkbox"]:checked').forEach(checkbox => {
            selectedFileTypes.push(checkbox.value);
        });
    }

    // Check if we have enough selections to enable the load button
    const hasValidSelection = selectedRegions.length > 0 &&
                             selectedProvinces.length > 0 &&
                             selectedMunicipalities.length > 0 &&
                             selectedFileTypes.length > 0;

    // Enable/disable load button
    if (loadBtn) {
        loadBtn.disabled = !hasValidSelection;
    }

    // Show/hide summary
    if (hasValidSelection) {
        // Calculate total files and build file selection array
        let totalFiles = 0;
        const fileBreakdown = {};
        const fileSelection = [];  // Array to hold file paths for loading

        selectedMunicipalities.forEach(municipalityKey => {
            const [regionName, provinceCode, municipalityId] = municipalityKey.split('|');
            if (cadastralData[regionName] &&
                cadastralData[regionName][provinceCode] &&
                cadastralData[regionName][provinceCode][municipalityId]) {

                const municipalityData = cadastralData[regionName][provinceCode][municipalityId];
                const files = municipalityData.files || [];

                selectedFileTypes.forEach(fileType => {
                    // Case-insensitive match for file type (MAP/map, PLE/ple)
                    const fileTypeLower = fileType.toLowerCase();
                    const typeFiles = files.filter(file => file.toLowerCase().includes(fileTypeLower));
                    if (!fileBreakdown[fileType]) fileBreakdown[fileType] = 0;
                    fileBreakdown[fileType] += typeFiles.length;
                    totalFiles += typeFiles.length;

                    // Add matching files to selection array with full path info
                    typeFiles.forEach(fileName => {
                        fileSelection.push({
                            path: `${regionName}/${provinceCode}/${municipalityId}/${fileName}`,
                            region: regionName,
                            province: provinceCode,
                            municipality: municipalityId,
                            fileName: fileName,
                            fileType: fileType
                        });
                    });
                });
            }
        });

        // Update global file selection for loadCadastralSelection() to use
        window.currentFileSelection = fileSelection;
        debugLog('Updated currentFileSelection with', fileSelection.length, 'files');

        // Update summary content
        summaryContent.innerHTML = `
            <div><strong>Selected:</strong></div>
            <div>• ${selectedRegions.length} region(s): ${selectedRegions.join(', ')}</div>
            <div>• ${selectedProvinces.length} province(s): ${selectedProvinces.join(', ')}</div>
            <div>• ${selectedMunicipalities.length} municipality(ies)</div>
            <div>• ${selectedFileTypes.length} file type(s): ${selectedFileTypes.join(', ')}</div>
            <div><strong>Total files: ${totalFiles}</strong></div>
        `;

        // Update files breakdown
        if (totalFiles > 0) {
            let filesHTML = '<div class="files-breakdown"><strong>Files breakdown:</strong>';
            Object.entries(fileBreakdown).forEach(([type, count]) => {
                filesHTML += `<div>• ${type}: ${count} files</div>`;
            });
            filesHTML += '</div>';
            filesList.innerHTML = filesHTML;
        } else {
            filesList.innerHTML = '<div class="no-files">No files found for this selection</div>';
        }

        selectionSummary.style.display = 'block';
    } else {
        selectionSummary.style.display = 'none';
        // Clear file selection when no valid selection
        window.currentFileSelection = [];
    }
}

// Get selected file types from checkboxes
function getSelectedFileTypes() {
    const fileTypesContainer = document.getElementById('cadastralFileTypes');
    const selectedTypes = [];

    if (fileTypesContainer) {
        fileTypesContainer.querySelectorAll('input[type="checkbox"]:checked').forEach(checkbox => {
            selectedTypes.push(checkbox.value);
        });
    }

    return selectedTypes;
}

// Load attribute table data
function loadAttributeTable() {
    const tableContainer = document.getElementById('attributeTable');
    const tableInfo = document.getElementById('tableInfo');

    // Get features from currentGeoJsonLayer or window.geoJsonData
    let features = null;
    if (currentGeoJsonLayer) {
        // Extract features from the Leaflet layer
        const geoJsonData = currentGeoJsonLayer.toGeoJSON();
        features = geoJsonData.features;
    } else if (window.geoJsonData) {
        features = window.geoJsonData.features;
    }

    if (features && features.length > 0) {
        // Update info
        tableInfo.textContent = `${features.length} features loaded`;

        // Create table
        let tableHTML = '<table class="data-table"><thead><tr>';

        // Get all property keys
        const allKeys = new Set();
        features.forEach(feature => {
            if (feature.properties) {
                Object.keys(feature.properties).forEach(key => allKeys.add(key));
            }
        });

        // Add headers
        allKeys.forEach(key => {
            tableHTML += `<th>${key}</th>`;
        });
        tableHTML += '</tr></thead><tbody>';

        // Add rows
        features.forEach((feature, index) => {
            tableHTML += `<tr data-feature-index="${index}">`;
            allKeys.forEach(key => {
                const value = feature.properties && feature.properties[key] ? feature.properties[key] : '';
                tableHTML += `<td>${value}</td>`;
            });
            tableHTML += '</tr>';
        });

        tableHTML += '</tbody></table>';
        tableContainer.innerHTML = tableHTML;
    } else {
        tableContainer.innerHTML = '<div class="no-data-message"><p>No geospatial data loaded. Please upload a file or select cadastral data to view attributes.</p></div>';
        tableInfo.textContent = 'No data loaded';
    }
}

// NOTE: updateDrawingStats defined earlier in file (line 873)

// Save drawn polygons
window.saveDrawnPolygons = function() {
    if (drawnItems && drawnItems.getLayers().length > 0) {
        const drawings = drawnItems.toGeoJSON();

        // Send to backend
        fetch('/api/v1/save-drawn-polygons/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(drawings)
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                alert(`Drawn polygons saved successfully! File: ${data.filename}`);
            } else {
                alert('Error saving drawn polygons: ' + (data.error || 'Unknown error'));
            }
        })
        .catch(error => {
            console.error('Error saving drawn polygons:', error);
            alert('Error saving drawn polygons');
        });
    } else {
        alert('No polygons to save');
    }
};

// Clear drawn polygons
window.clearDrawnPolygons = function() {
    if (drawnItems) {
        drawnItems.clearLayers();
        updateDrawingStats();
    }
};

// View toggle functions
window.showMapView = function() {
    debugLog('Showing Map View...');

    // Hide all views
    document.querySelectorAll('.view-content').forEach(view => {
        view.style.display = 'none';
        view.classList.remove('active');
        debugLog(`Hiding view: ${view.id}`);
    });

    // Show map view explicitly
    const mapView = document.getElementById('mapView');
    const mapContainer = document.querySelector('.map-container');
    const mapElement = document.getElementById('map');

    if (mapView) {
        mapView.style.display = 'flex';
        mapView.classList.add('active');
        debugLog('Map view shown');
    }

    if (mapContainer) {
        mapContainer.style.display = 'flex';
        mapContainer.style.height = '100%';
        debugLog('Map container shown');
    }

    if (mapElement) {
        mapElement.style.display = 'block';
        mapElement.style.height = '100%';
        mapElement.style.minHeight = '600px';
        debugLog('Map element configured');
        debugLog('Map element dimensions:', `${mapElement.offsetWidth}x${mapElement.offsetHeight}`);
    }

    // Set active button state
    document.querySelectorAll('.view-toggle button').forEach(btn => btn.classList.remove('active'));
    const mapViewBtn = document.getElementById('mapViewBtn');
    if (mapViewBtn) {
        mapViewBtn.classList.add('active');
    }

    // Refresh map if needed - multiple attempts
    if (map) {
        debugLog('Refreshing map...');
        setTimeout(() => {
            map.invalidateSize();
            debugLog('Map size invalidated immediately');
        }, 50);

        setTimeout(() => {
            map.invalidateSize();
            debugLog('Map size invalidated after 200ms');
        }, 200);

        setTimeout(() => {
            map.invalidateSize();
            debugLog('Map size invalidated after 500ms');
        }, 500);
    } else {
        debugLog('Map object not found!');
    }

    debugLog('Map View switch completed');
};

window.handleTableViewClick = function() {
    // Hide all views
    document.querySelectorAll('.view-content').forEach(view => {
        view.style.display = 'none';
        view.classList.remove('active');
    });

    // Show table view
    document.getElementById('tableView').style.display = 'flex';
    document.getElementById('tableView').classList.add('active');

    // Set active button state
    document.querySelectorAll('.view-toggle button').forEach(btn => btn.classList.remove('active'));
    document.getElementById('tableViewBtn').classList.add('active');

    // Load table data if available
    loadAttributeTable();

    debugLog('Switched to Table View');
};

window.showAdjacencyView = function() {
    // Hide all views
    document.querySelectorAll('.view-content').forEach(view => {
        view.style.display = 'none';
        view.classList.remove('active');
    });

    // Show adjacency view
    document.getElementById('adjacencyView').style.display = 'flex';
    document.getElementById('adjacencyView').classList.add('active');

    // Set active button state
    document.querySelectorAll('.view-toggle button').forEach(btn => btn.classList.remove('active'));
    document.getElementById('adjacencyViewBtn').classList.add('active');

    debugLog('Switched to Adjacency View');
};

window.showMappingView = function() {
    // Hide all views
    document.querySelectorAll('.view-content').forEach(view => {
        view.style.display = 'none';
        view.classList.remove('active');
    });

    // Show mapping view
    document.getElementById('mappingView').style.display = 'flex';
    document.getElementById('mappingView').classList.add('active');

    // Set active button state
    document.querySelectorAll('.view-toggle button').forEach(btn => btn.classList.remove('active'));
    document.getElementById('mappingViewBtn').classList.add('active');

    // Update drawing stats
    updateDrawingStats();

    debugLog('Switched to Mapping View');
};

// Cadastral data
let cadastralData = null;
let cadastralDataLoading = false;
let cadastralDataLoaded = false;
let cadastralDataPromise = null;

// Load cadastral data and populate selects
async function loadCadastralData() {
    // If already loaded, return immediately
    if (cadastralDataLoaded && cadastralData) {
        debugLog('Cadastral data already loaded, returning cached data');
        return cadastralData;
    }

    // If currently loading, return the existing promise
    if (cadastralDataLoading && cadastralDataPromise) {
        debugLog('Cadastral data already loading, waiting for existing request...');
        return cadastralDataPromise;
    }

    cadastralDataLoading = true;
    debugLog('Loading cadastral data...');

    // Create and store the promise so concurrent calls can wait on it
    cadastralDataPromise = _doLoadCadastralData();
    return cadastralDataPromise;
}

// Internal function to actually load cadastral data
async function _doLoadCadastralData() {
    const regionsSelect = document.getElementById('cadastralRegions');
    if (regionsSelect) {
        // Show loading state
        regionsSelect.innerHTML = '<option value="">Loading regions...</option>';
    }

    try {
        const response = await fetch('/api/v1/get-cadastral-structure/');
        debugLog('Cadastral data response status:', response.status);

        if (response.ok) {
            cadastralData = await response.json();
            // Also set on window for access from other scripts (folium-interface.js)
            window.cadastralData = cadastralData;
            debugLog('Cadastral data loaded:', cadastralData);
            debugLog('Number of regions:', Object.keys(cadastralData).length);

            if (cadastralData && Object.keys(cadastralData).length > 0) {
                cadastralDataLoaded = true;
                populateRegionsSelect();
                setupCadastralEventListeners();
                return cadastralData;
            } else {
                console.error('Cadastral data is empty');
                showCadastralError('No cadastral data available');
                return null;
            }
        } else {
            console.error('Failed to load cadastral data:', response.status, response.statusText);
            const errorText = await response.text();
            console.error('Error response:', errorText);
            showCadastralError('No regions available');
            return null;
        }
    } catch (error) {
        console.error('Error loading cadastral data:', error);
        showCadastralError('Could not connect — try reloading');
        return null;
    } finally {
        cadastralDataLoading = false;
    }
}

// Show error message in the regions select
function showCadastralError(message) {
    const regionsSelect = document.getElementById('cadastralRegions');
    if (regionsSelect) {
        regionsSelect.innerHTML = `<option value="" disabled>${message}</option>`;
    }
}

// Populate regions select
function populateRegionsSelect() {
    debugLog('Populating regions select...');
    const regionsSelect = document.getElementById('cadastralRegions');
    debugLog('Regions select element found:', !!regionsSelect);
    debugLog('Cadastral data available:', !!cadastralData);

    if (!regionsSelect) {
        console.error('CRITICAL: cadastralRegions select element not found!');
        debugLog('Available elements with cadastral in ID:',
            Array.from(document.querySelectorAll('[id*="cadastral"]')).map(el => el.id));
        return;
    }

    if (!cadastralData) {
        console.error('CRITICAL: No cadastral data available!');
        return;
    }

    debugLog('Cadastral data keys:', Object.keys(cadastralData));
    debugLog('Sample region data:', cadastralData[Object.keys(cadastralData)[0]]);

    // Clear existing options
    regionsSelect.innerHTML = '';
    debugLog('Cleared existing options');

    // Add region options
    const regions = Object.keys(cadastralData).sort();
    debugLog('Regions to add:', regions);
    debugLog('Number of regions:', regions.length);

    regions.forEach(regionName => {
        const option = document.createElement('option');
        option.value = regionName;
        option.textContent = regionName;
        regionsSelect.appendChild(option);
        debugLog('Added region:', regionName);
    });

    debugLog('Regions select populated with', regions.length, 'regions');
}

// Update provinces based on selected regions
function updateProvincesSelect() {
    const regionsSelect = document.getElementById('cadastralRegions');
    const provincesSelect = document.getElementById('cadastralProvinces');
    const municipalitiesSelect = document.getElementById('cadastralMunicipalities');

    if (!regionsSelect || !provincesSelect || !municipalitiesSelect || !cadastralData) return;

    // Get selected regions (filter out empty values)
    const selectedRegions = Array.from(regionsSelect.selectedOptions)
        .map(option => option.value)
        .filter(value => value !== '');

    // Clear provinces and municipalities
    provincesSelect.innerHTML = '';
    municipalitiesSelect.innerHTML = '';
    municipalitiesSelect.disabled = true;

    if (selectedRegions.length === 0) {
        provincesSelect.disabled = true;
        return;
    }

    // Enable provinces select
    provincesSelect.disabled = false;

    // Collect all provinces from selected regions
    const allProvinces = new Set();
    selectedRegions.forEach(regionName => {
        if (cadastralData[regionName]) {
            Object.keys(cadastralData[regionName]).forEach(provinceCode => {
                allProvinces.add(provinceCode);
            });
        }
    });

    // Add province options
    Array.from(allProvinces).sort().forEach(provinceCode => {
        const option = document.createElement('option');
        option.value = provinceCode;
        option.textContent = provinceCode;
        provincesSelect.appendChild(option);
    });

    // Update selection summary
    updateSelectionSummary();
}

// Update municipalities based on selected regions and provinces
function updateMunicipalitiesSelect() {
    const regionsSelect = document.getElementById('cadastralRegions');
    const provincesSelect = document.getElementById('cadastralProvinces');
    const municipalitiesSelect = document.getElementById('cadastralMunicipalities');

    if (!regionsSelect || !provincesSelect || !municipalitiesSelect || !cadastralData) return;

    // Get selected regions and provinces (filter out empty values)
    const selectedRegions = Array.from(regionsSelect.selectedOptions)
        .map(option => option.value)
        .filter(value => value !== '');
    const selectedProvinces = Array.from(provincesSelect.selectedOptions)
        .map(option => option.value)
        .filter(value => value !== '');

    // Clear municipalities
    municipalitiesSelect.innerHTML = '';

    if (selectedRegions.length === 0 || selectedProvinces.length === 0) {
        municipalitiesSelect.disabled = true;
        return;
    }

    // Enable municipalities select
    municipalitiesSelect.disabled = false;

    // Collect all municipalities from selected regions and provinces
    const allMunicipalities = new Map(); // Use Map to store municipality -> region mapping
    selectedRegions.forEach(regionName => {
        if (cadastralData[regionName]) {
            selectedProvinces.forEach(provinceCode => {
                if (cadastralData[regionName][provinceCode]) {
                    Object.keys(cadastralData[regionName][provinceCode]).forEach(municipalityKey => {
                        const municipalityData = cadastralData[regionName][provinceCode][municipalityKey];
                        const municipalityName = municipalityData.name || municipalityKey;
                        allMunicipalities.set(`${regionName}|${provinceCode}|${municipalityKey}`, municipalityName);
                    });
                }
            });
        }
    });

    // Add municipality options (sorted by name)
    Array.from(allMunicipalities.entries())
        .sort((a, b) => a[1].localeCompare(b[1]))
        .forEach(([key, name]) => {
            const option = document.createElement('option');
            option.value = key;
            option.textContent = name;
            municipalitiesSelect.appendChild(option);
        });

    // Update selection summary
    updateSelectionSummary();
}

// Setup event listeners for cadastral selects
function setupCadastralEventListeners() {
    const regionsSelect = document.getElementById('cadastralRegions');
    const provincesSelect = document.getElementById('cadastralProvinces');
    const municipalitiesSelect = document.getElementById('cadastralMunicipalities');

    if (regionsSelect) {
        regionsSelect.addEventListener('change', updateProvincesSelect);
    }

    if (provincesSelect) {
        provincesSelect.addEventListener('change', updateMunicipalitiesSelect);
    }

    if (municipalitiesSelect) {
        municipalitiesSelect.addEventListener('change', updateSelectionSummary);
    }
}

// Additional cadastral functions moved from folium-interface.js for consolidation
// NOTE: Duplicate functions have been removed - using definitions from earlier in file

// Initialize map and controls when DOM is loaded
document.addEventListener('DOMContentLoaded', function() {
    debugLog('DOM loaded, initializing map...');

    // Debug: Check if key elements exist
    const mapElement = document.getElementById('map');
    const regionsSelect = document.getElementById('cadastralRegions');
    const mapContainer = document.querySelector('.map-container');
    debugLog('Map element found:', !!mapElement);
    debugLog('Regions select found:', !!regionsSelect);
    debugLog('Map container found:', !!mapContainer);

    if (!mapElement) {
        console.warn('Map element not found - using Folium map instead');
        // Continue to load cadastral data even without client-side map
    }

    // IMPORTANT: Load cadastral data immediately for sidebar selects
    // This must happen regardless of map initialization
    debugLog('Loading cadastral data for sidebar selects...');
    loadCadastralData();

    // // Add a manual test button for debugging
    // const testButton = document.createElement('button');
    // testButton.textContent = 'Test Initialize';
    // testButton.style.position = 'fixed';
    // testButton.style.top = '10px';
    // testButton.style.right = '10px';
    // testButton.style.zIndex = '10000';
    // testButton.style.backgroundColor = 'red';
    // testButton.style.color = 'white';
    // testButton.onclick = function() {
    //     debugLog('Manual test button clicked');
    //     debugLog('Triggering map initialization...');
    //     initializeMap();
    //     debugLog('Triggering cadastral data load...');
    //     loadCadastralData();
    // };
    // document.body.appendChild(testButton);

    // Initialize map first
    setTimeout(() => {
        // Ensure Map View is shown
        debugLog('Ensuring Map View is visible...');
        showMapView();

        // Initialize map (may fail if using Folium map instead)
        debugLog('Starting map initialization...');
        try {
            initializeMap();
        } catch (e) {
            console.warn('Client-side map initialization skipped (using Folium map):', e.message);
        }

        // Always load cadastral data for the sidebar selects
        loadCadastralData();

        // Force map refresh multiple times to ensure it renders
        setTimeout(() => {
            if (map) {
                debugLog('First invalidateSize call...');
                map.invalidateSize();
            }
        }, 200);

        setTimeout(() => {
            if (map) {
                debugLog('Second invalidateSize call...');
                map.invalidateSize();
            }
        }, 1000);

        debugLog('✅ Direct Leaflet map with all providers initialized');
        debugLog('✅ Native Leaflet controls integrated');
    }, 100);
});

// GPKG Processing Function for Direct S3 Loading
async function processGpkgFile(arrayBuffer, filePath) {
    try {
        debugLog(`Processing GPKG file: ${filePath}`);

        // Check if sql.js is available
        if (typeof initSqlJs === 'undefined') {
            console.warn('sql.js not available. Loading from CDN...');

            // Dynamically load sql.js
            await loadSqlJs();
        }

        // Initialize SQL.js
        const SQL = await initSqlJs({
            locateFile: file => `https://cdnjs.cloudflare.com/ajax/libs/sql.js/1.8.0/${file}`
        });

        // Open the GPKG database
        const db = new SQL.Database(new Uint8Array(arrayBuffer));

        // Query to get geometry data (GPKG uses WKB format)
        const query = `
            SELECT
                fid,
                geom,
                *
            FROM gpkg_contents
            WHERE data_type = 'features'
            LIMIT 1
        `;

        let tableName = null;
        try {
            const contentResult = db.exec(query);
            if (contentResult.length > 0 && contentResult[0].values.length > 0) {
                tableName = contentResult[0].values[0][0]; // table_name is first column
            }
        } catch (e) {
            debugLog('Trying to find table with geometry...');
            // Fallback: look for tables with geometry columns
            const tablesQuery = `
                SELECT name FROM sqlite_master
                WHERE type='table' AND name NOT LIKE 'gpkg_%' AND name NOT LIKE 'sqlite_%'
                LIMIT 1
            `;
            const tablesResult = db.exec(tablesQuery);
            if (tablesResult.length > 0 && tablesResult[0].values.length > 0) {
                tableName = tablesResult[0].values[0][0];
            }
        }

        if (!tableName) {
            console.error('No suitable table found in GPKG');
            return null;
        }

        debugLog(`Using table: ${tableName}`);

        // Get features from the main table
        const featuresQuery = `SELECT * FROM "${tableName}" LIMIT 100`;
        const result = db.exec(featuresQuery);

        if (result.length === 0) {
            console.error('No features found in table');
            return null;
        }

        const columns = result[0].columns;
        const rows = result[0].values;

        debugLog(`Found ${rows.length} features with columns:`, columns);

        // Convert to GeoJSON format
        const features = [];

        for (let i = 0; i < Math.min(rows.length, 50); i++) { // Limit to 50 features for performance
            const row = rows[i];
            const feature = {
                type: 'Feature',
                id: i,
                properties: {},
                geometry: null
            };

            // Build properties from all non-geometry columns
            for (let j = 0; j < columns.length; j++) {
                const colName = columns[j];
                const value = row[j];

                if (colName.toLowerCase() === 'geom' || colName.toLowerCase() === 'geometry') {
                    // Skip geometry column for properties, we'll handle it separately
                    continue;
                } else {
                    feature.properties[colName] = value;
                    feature.properties.feature_id = i;
                }
            }

            // For now, create a simple placeholder geometry since WKB parsing is complex
            // In a full implementation, you'd parse the WKB binary geometry data
            feature.geometry = {
                type: 'Point',
                coordinates: [12.0 + (i * 0.01), 42.0 + (i * 0.01)] // Placeholder coordinates
            };

            features.push(feature);
        }

        db.close();

        const geojson = {
            type: 'FeatureCollection',
            features: features
        };

        debugLog(`Successfully processed ${features.length} features from ${filePath}`);
        return geojson;

    } catch (error) {
        console.error(`Error processing GPKG file ${filePath}:`, error);
        return null;
    }
}

// Function to dynamically load sql.js
async function loadSqlJs() {
    return new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = 'https://cdnjs.cloudflare.com/ajax/libs/sql.js/1.8.0/sql-wasm.js';
        script.onload = () => {
            debugLog('sql.js loaded successfully');
            resolve();
        };
        script.onerror = () => {
            reject(new Error('Failed to load sql.js'));
        };
        document.head.appendChild(script);
    });
}

// =============================================================================
// UNIFIED AUTO-ZOOM FUNCTIONALITY
// Works with both regular Leaflet maps (map.html) and Folium maps (index.html)
// =============================================================================

/**
 * Unified function to auto-zoom to all polygons on any map type
 * Supports both regular Leaflet maps and Folium server-generated maps
 */
function autoZoomToAllPolygons() {
    debugLog('🔍 autoZoomToAllPolygons called from map.js (unified implementation)');

    try {
        // First try: Regular Leaflet map (map.html)
        if (typeof map !== 'undefined' && map && map.getBounds) {
            debugLog('📍 Found regular Leaflet map, attempting auto-zoom');
            return autoZoomLeafletMap();
        }

        // Second try: Folium map (index.html)
        const foliumMap = findFoliumMap();
        if (foliumMap) {
            debugLog('🗺️ Found Folium map, attempting auto-zoom');
            return autoZoomFoliumMap(foliumMap);
        }

        // Third try: Wait and retry (for delayed initialization)
        debugLog('⏳ No map found, waiting 500ms and retrying...');
        setTimeout(() => {
            const retryFoliumMap = findFoliumMap();
            if (retryFoliumMap) {
                debugLog('🗺️ Found Folium map on retry');
                autoZoomFoliumMap(retryFoliumMap);
            } else if (typeof map !== 'undefined' && map) {
                debugLog('📍 Found Leaflet map on retry');
                autoZoomLeafletMap();
            } else {
                console.warn('❌ No compatible map found after retry');
            }
        }, 500);

    } catch (error) {
        console.error('❌ Error in unified auto-zoom:', error);
    }
}

/**
 * Auto-zoom for regular Leaflet maps (map.html)
 */
function autoZoomLeafletMap() {
    try {
        if (!map || !map.getBounds) {
            console.warn('❌ Leaflet map not properly initialized');
            return false;
        }

        const bounds = calculateLeafletBounds();
        if (bounds && bounds.isValid()) {
            map.fitBounds(bounds, {
                padding: [20, 20]
            });
            debugLog('✅ Auto-zoomed Leaflet map to fit all polygons');

            // Update polygon management state if available
            if (typeof updatePolygonManagementState === 'function') {
                updatePolygonManagementState();
            }
            return true;
        } else {
            debugLog('❌ No valid bounds found for Leaflet map');
            return false;
        }
    } catch (error) {
        console.error('❌ Error auto-zooming Leaflet map:', error);
        return false;
    }
}

/**
 * Auto-zoom for Folium maps (index.html)
 */
function autoZoomFoliumMap(foliumMap) {
    try {
        if (!foliumMap || !foliumMap.fitBounds) {
            console.warn('❌ Folium map not properly initialized');
            return false;
        }

        const bounds = calculateFoliumBounds(foliumMap);
        if (bounds) {
            foliumMap.fitBounds([
                [bounds.minLat, bounds.minLng],
                [bounds.maxLat, bounds.maxLng]
            ], {
                padding: [20, 20]
            });
            debugLog('✅ Auto-zoomed Folium map to fit all polygons');

            // Update polygon management state if available
            if (typeof updatePolygonManagementState === 'function') {
                updatePolygonManagementState();
            }
            return true;
        } else {
            debugLog('❌ No valid bounds found for Folium map');
            return false;
        }
    } catch (error) {
        console.error('❌ Error auto-zooming Folium map:', error);
        return false;
    }
}

/**
 * Find Folium map instance in the DOM
 */
function findFoliumMap() {
    try {
        const mapElements = document.querySelectorAll('.folium-map');
        debugLog(`Found ${mapElements.length} .folium-map elements`);

        if (mapElements.length > 0) {
            const mapId = mapElements[0].id;
            debugLog('Map ID found:', mapId);

            if (mapId && window[mapId]) {
                debugLog('Folium map object found in window:', window[mapId]);
                return window[mapId];
            } else {
                console.warn('No map object found in window with ID:', mapId);
            }
        } else {
            debugLog('No .folium-map elements found');
        }
        return null;
    } catch (error) {
        console.error('Error finding Folium map:', error);
        return null;
    }
}

/**
 * Calculate bounds for regular Leaflet maps
 */
function calculateLeafletBounds() {
    const bounds = L.latLngBounds();
    let hasFeatures = false;

    try {
        // Include GeoJSON layer bounds
        if (currentGeoJsonLayer && currentGeoJsonLayer.getBounds) {
            const layerBounds = currentGeoJsonLayer.getBounds();
            if (layerBounds.isValid()) {
                bounds.extend(layerBounds);
                hasFeatures = true;
            }
        }

        // Include drawn items bounds
        if (drawnItems && drawnItems.getBounds) {
            const drawnBounds = drawnItems.getBounds();
            if (drawnBounds.isValid()) {
                bounds.extend(drawnBounds);
                hasFeatures = true;
            }
        }

        // Check all layers on the map
        map.eachLayer(function(layer) {
            if (layer.getBounds && typeof layer.getBounds === 'function') {
                try {
                    const layerBounds = layer.getBounds();
                    if (layerBounds && layerBounds.isValid && layerBounds.isValid()) {
                        bounds.extend(layerBounds);
                        hasFeatures = true;
                    }
                } catch (e) {
                    console.debug('Could not get bounds for layer:', e);
                }
            }
        });

        return hasFeatures && bounds.isValid() ? bounds : null;
    } catch (error) {
        console.error('Error calculating Leaflet bounds:', error);
        return null;
    }
}

/**
 * Calculate bounds for Folium maps
 */
function calculateFoliumBounds(foliumMap) {
    let minLat = Infinity, maxLat = -Infinity;
    let minLng = Infinity, maxLng = -Infinity;
    let hasFeatures = false;

    try {
        // Iterate through all layers on the Folium map
        foliumMap.eachLayer(function(layer) {
            // Skip base tile layers
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

        // Fallback to window.geoJsonData if no features found
        if (!hasFeatures && window.geoJsonData) {
            return calculateGeoJsonBounds(window.geoJsonData);
        }

        // Return bounds if valid
        if (hasFeatures && minLat !== Infinity && maxLat !== -Infinity &&
            minLng !== Infinity && maxLng !== -Infinity) {
            return {
                minLat: minLat,
                maxLat: maxLat,
                minLng: minLng,
                maxLng: maxLng
            };
        }

        return null;
    } catch (error) {
        console.error('Error calculating Folium bounds:', error);
        return null;
    }
}

/**
 * Calculate bounds from GeoJSON data (fallback method)
 */
function calculateGeoJsonBounds(geoJsonData) {
    if (!geoJsonData || !geoJsonData.features || geoJsonData.features.length === 0) {
        return null;
    }

    let minLat = Infinity, maxLat = -Infinity;
    let minLng = Infinity, maxLng = -Infinity;

    try {
        geoJsonData.features.forEach(feature => {
            if (feature.geometry && feature.geometry.coordinates) {
                const coords = feature.geometry.coordinates;

                // Handle different geometry types
                if (feature.geometry.type === 'Polygon') {
                    coords[0].forEach(coord => {
                        const lng = coord[0], lat = coord[1];
                        minLat = Math.min(minLat, lat);
                        maxLat = Math.max(maxLat, lat);
                        minLng = Math.min(minLng, lng);
                        maxLng = Math.max(maxLng, lng);
                    });
                } else if (feature.geometry.type === 'MultiPolygon') {
                    coords.forEach(polygon => {
                        polygon[0].forEach(coord => {
                            const lng = coord[0], lat = coord[1];
                            minLat = Math.min(minLat, lat);
                            maxLat = Math.max(maxLat, lat);
                            minLng = Math.min(minLng, lng);
                            maxLng = Math.max(maxLng, lng);
                        });
                    });
                } else if (feature.geometry.type === 'Point') {
                    const lng = coords[0], lat = coords[1];
                    minLat = Math.min(minLat, lat);
                    maxLat = Math.max(maxLat, lat);
                    minLng = Math.min(minLng, lng);
                    maxLng = Math.max(maxLng, lng);
                } else if (feature.geometry.type === 'LineString') {
                    coords.forEach(coord => {
                        const lng = coord[0], lat = coord[1];
                        minLat = Math.min(minLat, lat);
                        maxLat = Math.max(maxLat, lat);
                        minLng = Math.min(minLng, lng);
                        maxLng = Math.max(maxLng, lng);
                    });
                }
            }
        });

        // Return valid bounds
        if (minLat !== Infinity && maxLat !== -Infinity &&
            minLng !== Infinity && maxLng !== -Infinity) {
            return {
                minLat: minLat,
                maxLat: maxLat,
                minLng: minLng,
                maxLng: maxLng
            };
        }

        return null;
    } catch (error) {
        console.error('Error calculating GeoJSON bounds:', error);
        return null;
    }
}


// ============================================================================
// Zone Manager
// ============================================================================

window.savedZones = {};       // zone_id -> { layer, visible }
window.zoneLayerGroup = null; // L.LayerGroup for all zone layers
window.zoneMicrozones = {};   // zone_id -> [microzones]
window.zoneSearchTerm = '';
window.savedMicrozones = {};  // microzone_id -> { zoneId, layer, visible }
window.microzoneLayerGroup = null;
window.microzonesDrawnByZone = {}; // zone_id -> boolean
window.zoneKeyboardState = {
    initialized: false,
    pressedKeys: new Set(),
    jumpIndex: -1
};
window.currentMappingZoneContext = null;

var MICROZONE_WARNING_THRESHOLD_KM2 = 0.3;
var ZONE_CARD_FOCUS_DURATION_MS = 1300;

function getLeafletMapInstance() {
    if (map && typeof map.addLayer === 'function') {
        return map;
    }
    var mapElements = document.querySelectorAll('.leaflet-container');
    for (var i = 0; i < mapElements.length; i += 1) {
        var mapId = mapElements[i].id;
        var candidate = window[mapId];
        if (candidate && typeof candidate.addLayer === 'function') {
            return candidate;
        }
    }
    return null;
}

function initZoneManager() {
    if (!window.zoneLayerGroup) {
        window.zoneLayerGroup = new L.LayerGroup();
    }
    if (!window.microzoneLayerGroup) {
        window.microzoneLayerGroup = new L.LayerGroup();
    }

    var leafletMap = getLeafletMapInstance();
    if (leafletMap) {
        if (!leafletMap.hasLayer(window.zoneLayerGroup)) {
            leafletMap.addLayer(window.zoneLayerGroup);
        }
        if (!leafletMap.hasLayer(window.microzoneLayerGroup)) {
            leafletMap.addLayer(window.microzoneLayerGroup);
        }
    }

    initializeZoneKeyboardShortcuts();
    updateSaveAsZoneButton();
    console.log('Zone Manager initialized');
}

function initializeZoneKeyboardShortcuts() {
    if (window.zoneKeyboardState.initialized) return;
    document.addEventListener('keydown', handleZoneManagerKeyDown);
    document.addEventListener('keyup', handleZoneManagerKeyUp);
    window.zoneKeyboardState.initialized = true;
}

function shouldIgnoreGlobalShortcut(event) {
    if (event.defaultPrevented) return true;
    if (event.ctrlKey || event.metaKey || event.altKey) return true;
    var target = event.target;
    if (!target) return false;
    var tag = target.tagName ? target.tagName.toLowerCase() : '';
    if (tag === 'input' || tag === 'textarea' || tag === 'select' || target.isContentEditable) {
        return true;
    }
    return false;
}

function handleZoneManagerKeyDown(event) {
    if (shouldIgnoreGlobalShortcut(event)) return;

    var key = event.key;
    if (window.zoneKeyboardState.pressedKeys.has(key)) return;
    window.zoneKeyboardState.pressedKeys.add(key);

    var leafletMap = getLeafletMapInstance();
    if (!leafletMap) return;

    var handled = false;
    if (key === 'ArrowUp') {
        leafletMap.panBy([0, -120]);
        handled = true;
    } else if (key === 'ArrowDown') {
        leafletMap.panBy([0, 120]);
        handled = true;
    } else if (key === 'ArrowLeft') {
        leafletMap.panBy([-120, 0]);
        handled = true;
    } else if (key === 'ArrowRight') {
        leafletMap.panBy([120, 0]);
        handled = true;
    } else if (key === '+' || key === '=') {
        leafletMap.zoomIn();
        handled = true;
    } else if (key === '-' || key === '_') {
        leafletMap.zoomOut();
        handled = true;
    } else if (key === '[') {
        jumpToRelativeZone(-1);
        handled = true;
    } else if (key === ']') {
        jumpToRelativeZone(1);
        handled = true;
    }

    if (handled) {
        event.preventDefault();
    }
}

function handleZoneManagerKeyUp(event) {
    window.zoneKeyboardState.pressedKeys.delete(event.key);
}

function getVisibleZoneIdsForJump() {
    var ids = [];
    document.querySelectorAll('#zoneList .zone-item').forEach(function(item) {
        if (item.style.display === 'none') return;
        var checkbox = item.querySelector('.zone-vis-toggle');
        if (checkbox && !checkbox.checked) return;
        var zoneId = parseInt(item.getAttribute('data-zone-id'), 10);
        if (Number.isFinite(zoneId)) {
            ids.push(zoneId);
        }
    });
    return ids;
}

function jumpToRelativeZone(direction) {
    var ids = getVisibleZoneIdsForJump();
    if (ids.length === 0) return;

    if (window.zoneKeyboardState.jumpIndex < 0 || window.zoneKeyboardState.jumpIndex >= ids.length) {
        window.zoneKeyboardState.jumpIndex = direction > 0 ? 0 : ids.length - 1;
    } else {
        window.zoneKeyboardState.jumpIndex = (window.zoneKeyboardState.jumpIndex + direction + ids.length) % ids.length;
    }

    var zoneId = ids[window.zoneKeyboardState.jumpIndex];
    focusZoneCard(zoneId, true);
    zoomToZone(zoneId);
}

function updateSaveAsZoneButton() {
    var hasDrawings = !!window.drawnItems && window.drawnItems.getLayers().length > 0;
    ['saveAsZoneBtn', 'addZoneBtn'].forEach(function(btnId) {
        var btn = document.getElementById(btnId);
        if (btn) {
            btn.disabled = !hasDrawings;
        }
    });
}

function showZoneSaveForm() {
    if (!window.drawnItems || window.drawnItems.getLayers().length === 0) {
        alert('Draw a shape on the map first.');
        return;
    }
    document.getElementById('zoneSaveForm').style.display = 'block';
    document.getElementById('zoneName').focus();
}

function cancelZoneSave() {
    document.getElementById('zoneSaveForm').style.display = 'none';
    document.getElementById('zoneName').value = '';
    document.getElementById('zoneDescription').value = '';
    document.getElementById('zoneColor').value = '#3388ff';
    document.getElementById('zoneTags').value = '';
}

async function _authenticatedFetch(url, options) {
    options = options || {};
    options.headers = options.headers || {};
    options.headers['Content-Type'] = 'application/json';

    // Try to get auth token from Clerk
    if (window.Clerk && window.Clerk.session) {
        try {
            var token = await window.Clerk.session.getToken();
            if (token) {
                options.headers['Authorization'] = 'Bearer ' + token;
            }
        } catch (e) {
            console.warn('Could not get auth token:', e);
        }
    }
    return fetch(url, options);
}

async function saveCurrentDrawingAsZone() {
    var name = document.getElementById('zoneName').value.trim();
    if (!name) {
        alert('Please enter a zone name.');
        return;
    }

    if (!window.drawnItems || window.drawnItems.getLayers().length === 0) {
        alert('No drawn shapes to save.');
        return;
    }

    // Get the last drawn layer
    var layers = window.drawnItems.getLayers();
    var layer = layers[layers.length - 1];
    var geojson = layer.toGeoJSON();

    // Ensure it's a Feature
    if (geojson.type !== 'Feature') {
        geojson = { type: 'Feature', geometry: geojson, properties: {} };
    }

    var description = document.getElementById('zoneDescription').value.trim();
    var color = document.getElementById('zoneColor').value;
    var tagsStr = document.getElementById('zoneTags').value.trim();
    var tags = tagsStr ? tagsStr.split(',').map(function(t) { return t.trim(); }).filter(Boolean) : [];

    // Determine polygon type
    var polygonType = 'polygon';
    if (layer instanceof L.Circle) polygonType = 'circle';
    else if (layer instanceof L.Rectangle) polygonType = 'rectangle';
    else if (layer instanceof L.Marker) polygonType = 'marker';
    else if (layer instanceof L.Polyline && !(layer instanceof L.Polygon)) polygonType = 'polyline';

    try {
        var response = await _authenticatedFetch('/api/v1/zones/', {
            method: 'POST',
            body: JSON.stringify({
                name: name,
                description: description || null,
                geojson: geojson,
                polygon_type: polygonType,
                color: color,
                tags: tags
            })
        });

        if (!response.ok) {
            var err = await response.json();
            alert('Error saving zone: ' + (err.detail || 'Unknown error'));
            return;
        }

        var data = await response.json();
        var zone = data.zone;

        // Remove from drawnItems and add to zoneLayerGroup
        window.drawnItems.removeLayer(layer);
        renderZoneOnMap(zone, true);
        updateSaveAsZoneButton();

        // Add to zone list
        addZoneToList(zone);

        // Hide save form
        cancelZoneSave();

        console.log('Zone saved:', zone.id, zone.name);

    } catch (e) {
        console.error('Error saving zone:', e);
        alert('Failed to save zone. Please try again.');
    }
}

function getZoneItemElement(zoneId) {
    return document.querySelector('.zone-item[data-zone-id="' + zoneId + '"]');
}

function isZoneVisibleInUi(zoneId) {
    var toggle = document.querySelector('.zone-item[data-zone-id="' + zoneId + '"] .zone-vis-toggle');
    if (toggle) {
        return !!toggle.checked;
    }
    var entry = window.savedZones[zoneId];
    if (entry && typeof entry.visible === 'boolean') {
        return entry.visible;
    }
    return false;
}

function microzonesContainGeojson(microzones) {
    return Array.isArray(microzones) && microzones.length > 0 && microzones.every(function(item) {
        return item && typeof item.geojson === 'object' && item.geojson !== null;
    });
}

function getMicrozoneCacheForZone(zoneId) {
    var cache = window.zoneMicrozones[zoneId];
    return Array.isArray(cache) ? cache : [];
}

function findCachedMicrozone(zoneId, microzoneId) {
    var microzones = getMicrozoneCacheForZone(zoneId);
    for (var i = 0; i < microzones.length; i += 1) {
        if (Number(microzones[i].id) === Number(microzoneId)) {
            return microzones[i];
        }
    }
    return null;
}

function formatWorkflowLabel(workflow) {
    return workflow === 'rents' ? 'Rents' : 'Sales';
}

function shouldShowMicrozoneLayer(microzone) {
    if (!microzone || !microzone.geojson) return false;
    if (!microzone.is_visible) return false;
    return isZoneVisibleInUi(microzone.zone_id);
}

function createMicrozoneLayer(microzone) {
    if (!microzone || !microzone.geojson) return null;

    var color = microzone.color || '#3388ff';
    var zoneId = Number(microzone.zone_id || 0);
    var name = escapeHtml(microzone.name || 'Unnamed microzone');
    var layer = L.geoJSON(microzone.geojson, {
        style: function() {
            return {
                color: color,
                weight: 2,
                opacity: 0.9,
                dashArray: '5 4',
                fillOpacity: 0.12,
                fillColor: color
            };
        },
        pointToLayer: function(feature, latlng) {
            return L.circleMarker(latlng, {
                radius: 6,
                color: color,
                fillColor: color,
                fillOpacity: 0.45
            });
        }
    });

    layer.bindPopup(
        '<strong>' + name + '</strong>' +
        '<br><small>Microzone #' + microzone.id + ' in Zone #' + zoneId + '</small>'
    );
    return layer;
}

function cacheMicrozoneLayer(microzone) {
    if (!microzone || !microzone.geojson) return null;

    var microzoneId = Number(microzone.id);
    var existing = window.savedMicrozones[microzoneId];
    if (existing && existing.layer && window.microzoneLayerGroup) {
        window.microzoneLayerGroup.removeLayer(existing.layer);
    }

    var layer = createMicrozoneLayer(microzone);
    if (!layer) return null;

    var entry = {
        zoneId: Number(microzone.zone_id || 0),
        layer: layer,
        visible: !!microzone.is_visible
    };
    window.savedMicrozones[microzoneId] = entry;
    return entry;
}

function removeMicrozoneLayer(microzoneId, purgeCache) {
    var id = Number(microzoneId);
    var entry = window.savedMicrozones[id];
    if (!entry) return;

    if (entry.layer && window.microzoneLayerGroup) {
        window.microzoneLayerGroup.removeLayer(entry.layer);
    }
    if (purgeCache !== false) {
        delete window.savedMicrozones[id];
    }
}

function removeMicrozoneLayersForZone(zoneId, purgeCache) {
    var targetZoneId = Number(zoneId);
    Object.keys(window.savedMicrozones).forEach(function(key) {
        var entry = window.savedMicrozones[key];
        if (entry && Number(entry.zoneId) === targetZoneId) {
            removeMicrozoneLayer(Number(key), purgeCache);
        }
    });
}

function pruneMicrozoneLayerCacheForZones(validZoneIdSet) {
    Object.keys(window.savedMicrozones).forEach(function(key) {
        var entry = window.savedMicrozones[key];
        if (!entry || !validZoneIdSet.has(Number(entry.zoneId))) {
            removeMicrozoneLayer(Number(key), true);
        }
    });
}

function pruneMicrozoneLayerCacheForZoneMicrozones(zoneId, microzones) {
    var allowedIds = new Set();
    (microzones || []).forEach(function(item) {
        allowedIds.add(Number(item.id));
    });
    Object.keys(window.savedMicrozones).forEach(function(key) {
        var entry = window.savedMicrozones[key];
        if (!entry || Number(entry.zoneId) !== Number(zoneId)) return;
        if (!allowedIds.has(Number(key))) {
            removeMicrozoneLayer(Number(key), true);
        }
    });
}

function syncMicrozoneLayer(microzone) {
    if (!microzone) return false;
    var microzoneId = Number(microzone.id);
    var entry = window.savedMicrozones[microzoneId];
    if (!entry && microzone.geojson) {
        entry = cacheMicrozoneLayer(microzone);
    }
    if (!entry || !entry.layer || !window.microzoneLayerGroup) {
        return false;
    }

    entry.visible = !!microzone.is_visible;
    var shouldShow = shouldShowMicrozoneLayer(microzone);
    if (shouldShow) {
        if (!window.microzoneLayerGroup.hasLayer(entry.layer)) {
            window.microzoneLayerGroup.addLayer(entry.layer);
        }
        return true;
    }

    window.microzoneLayerGroup.removeLayer(entry.layer);
    return false;
}

function syncMicrozoneLayersForZone(zoneId) {
    var microzones = getMicrozoneCacheForZone(zoneId);
    if (!microzones.length) {
        removeMicrozoneLayersForZone(zoneId, false);
        return 0;
    }

    var renderedCount = 0;
    microzones.forEach(function(microzone) {
        if (syncMicrozoneLayer(microzone)) {
            renderedCount += 1;
        }
    });
    return renderedCount;
}

function syncAllMicrozoneLayers() {
    var total = 0;
    Object.keys(window.zoneMicrozones).forEach(function(zoneId) {
        total += syncMicrozoneLayersForZone(Number(zoneId));
    });
    return total;
}

function fitMapToLayerBounds(layers, padding) {
    var leafletMap = getLeafletMapInstance();
    if (!leafletMap || !Array.isArray(layers) || layers.length === 0) {
        return false;
    }

    var bounds = null;
    layers.forEach(function(layer) {
        if (!layer || typeof layer.getBounds !== 'function') return;
        var layerBounds = layer.getBounds();
        if (!layerBounds || !layerBounds.isValid()) return;
        if (!bounds) {
            bounds = L.latLngBounds(layerBounds.getSouthWest(), layerBounds.getNorthEast());
        } else {
            bounds.extend(layerBounds);
        }
    });

    if (!bounds || !bounds.isValid()) return false;
    leafletMap.fitBounds(bounds, { padding: padding || [30, 30] });
    return true;
}

async function drawAllMicrozonesForZone(zoneId, options) {
    var opts = options || {};
    var shouldZoom = opts.zoom !== false;
    try {
        var microzones = await loadMicrozonesForZone(zoneId, true, true);
        window.microzonesDrawnByZone[zoneId] = true;

        pruneMicrozoneLayerCacheForZoneMicrozones(zoneId, microzones);
        var layersToZoom = [];
        microzones.forEach(function(microzone) {
            if (syncMicrozoneLayer(microzone)) {
                var entry = window.savedMicrozones[microzone.id];
                if (entry && entry.layer) {
                    layersToZoom.push(entry.layer);
                }
            }
        });

        if (shouldZoom && layersToZoom.length > 0) {
            fitMapToLayerBounds(layersToZoom, [28, 28]);
        }
    } catch (e) {
        console.error('Error drawing microzones for zone', zoneId, e);
    }
}

async function loadAllZones() {
    try {
        // Load zone list (without geometries)
        var listResponse = await _authenticatedFetch('/api/v1/zones/');
        if (!listResponse.ok) {
            if (listResponse.status === 401) {
                console.log('Not authenticated - skipping zone load');
                updateZoneCountBadge(0);
                return;
            }
            console.error('Failed to load zones:', listResponse.status);
            return;
        }

        var listData = await listResponse.json();
        var zones = listData.zones || [];
        updateZoneCountBadge(zones.length);

        var zoneIdSet = new Set();
        zones.forEach(function(zone) {
            zoneIdSet.add(Number(zone.id));
        });

        // Remove stale microzone cache state for deleted zones
        Object.keys(window.microzonesDrawnByZone).forEach(function(zoneId) {
            if (!zoneIdSet.has(Number(zoneId))) {
                delete window.microzonesDrawnByZone[zoneId];
            }
        });
        pruneMicrozoneLayerCacheForZones(zoneIdSet);

        // Render list
        renderZoneList(zones);

        // Load visible geometries onto map
        var geoResponse = await _authenticatedFetch('/api/v1/zones/geojson');
        window.savedZones = {};
        zones.forEach(function(zone) {
            window.savedZones[zone.id] = { layer: null, visible: !!zone.is_visible };
        });

        if (geoResponse.ok) {
            var geoData = await geoResponse.json();

            // Clear existing zone layers
            if (window.zoneLayerGroup) {
                window.zoneLayerGroup.clearLayers();
            }

            // Render each feature
            if (geoData.features) {
                geoData.features.forEach(function(feature) {
                    var zoneId = feature.properties.zone_id;
                    var zoneName = feature.properties.zone_name;
                    var zoneColor = feature.properties.zone_color || '#3388ff';

                    var zoneLayer = L.geoJSON(feature, {
                        style: function() {
                            return {
                                color: zoneColor,
                                weight: 3,
                                opacity: 0.8,
                                fillOpacity: 0.25,
                                fillColor: zoneColor
                            };
                        },
                        pointToLayer: function(f, latlng) {
                            return L.circleMarker(latlng, {
                                radius: 8,
                                color: zoneColor,
                                fillColor: zoneColor,
                                fillOpacity: 0.5
                            });
                        }
                    });

                    zoneLayer.bindPopup(
                        '<strong>' + (zoneName || 'Unnamed Zone') + '</strong>' +
                        '<br><small>ID: ' + zoneId + '</small>'
                    );

                    if (window.zoneLayerGroup) {
                        window.zoneLayerGroup.addLayer(zoneLayer);
                    }

                    window.savedZones[zoneId] = {
                        layer: zoneLayer,
                        visible: true
                    };
                });
            }
        }

        // Re-draw previously rendered microzones with updated visibility
        var redrawPromises = [];
        Object.keys(window.microzonesDrawnByZone).forEach(function(zoneIdRaw) {
            var zoneId = Number(zoneIdRaw);
            if (!window.microzonesDrawnByZone[zoneId]) return;
            if (!zoneIdSet.has(zoneId)) return;
            redrawPromises.push(loadMicrozonesForZone(zoneId, true, true));
        });
        if (redrawPromises.length > 0) {
            await Promise.all(redrawPromises);
        }
        syncAllMicrozoneLayers();

        console.log('Loaded', zones.length, 'zones');

    } catch (e) {
        console.error('Error loading zones:', e);
    }
}

function updateZoneCountBadge(count) {
    var badge = document.getElementById('zoneCount');
    if (badge) {
        badge.textContent = String(count || 0);
    }
}

function getAllZoneIdsFromList() {
    var ids = [];
    document.querySelectorAll('#zoneList .zone-item').forEach(function(item) {
        var raw = item.getAttribute('data-zone-id');
        var parsed = parseInt(raw, 10);
        if (Number.isFinite(parsed)) {
            ids.push(parsed);
        }
    });
    return ids;
}

function applyZoneSearchFilter() {
    var input = document.getElementById('zoneSearchInput');
    var noResults = document.getElementById('zoneSearchNoResults');
    var term = input ? input.value.trim().toLowerCase() : '';
    window.zoneSearchTerm = term;

    var items = Array.from(document.querySelectorAll('#zoneList .zone-item'));
    var visibleCount = 0;
    items.forEach(function(item) {
        var zoneName = '';
        var zoneNameEl = item.querySelector('.zone-name');
        if (zoneNameEl && zoneNameEl.textContent) {
            zoneName = zoneNameEl.textContent.toLowerCase();
        }

        var isMatch = !term || zoneName.indexOf(term) !== -1;
        item.style.display = isMatch ? '' : 'none';
        if (isMatch) {
            visibleCount += 1;
        }
    });

    if (noResults) {
        var shouldShow = term.length > 0 && items.length > 0 && visibleCount === 0;
        noResults.style.display = shouldShow ? 'block' : 'none';
    }
}

function clearZoneSearch() {
    var input = document.getElementById('zoneSearchInput');
    if (input) {
        input.value = '';
    }
    applyZoneSearchFilter();
}

function escapeHtml(value) {
    if (value === null || value === undefined) return '';
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function getMicrozoneAreaKm2(microzone) {
    if (!microzone) return null;
    if (typeof microzone.area_km2 === 'number' && Number.isFinite(microzone.area_km2)) {
        return microzone.area_km2;
    }
    var areaSqm = microzone.area_sqm;
    if (typeof areaSqm !== 'number') {
        areaSqm = parseFloat(areaSqm);
    }
    if (!Number.isFinite(areaSqm)) {
        return null;
    }
    return areaSqm / 1000000;
}

function isLargeMicrozoneArea(microzone, areaKm2) {
    if (microzone && typeof microzone.is_large_area === 'boolean') {
        return microzone.is_large_area;
    }
    var threshold = MICROZONE_WARNING_THRESHOLD_KM2;
    if (microzone && typeof microzone.warning_threshold_km2 === 'number' && Number.isFinite(microzone.warning_threshold_km2)) {
        threshold = microzone.warning_threshold_km2;
    }
    return typeof areaKm2 === 'number' && areaKm2 > threshold;
}

function updateZoneMicroCount(zoneId, count) {
    var value = Number.isFinite(count) ? count : 0;
    var headerBadge = document.getElementById('zoneMicroCount_' + zoneId);
    if (headerBadge) {
        headerBadge.textContent = String(value);
    }
    var bodyBadge = document.getElementById('zoneMicroCountBody_' + zoneId);
    if (bodyBadge) {
        bodyBadge.textContent = String(value);
    }
}

function microzoneItemHtml(zoneId, microzone) {
    var name = escapeHtml(microzone.name || 'Unnamed microzone');
    var color = microzone.color || '#3388ff';
    var checked = microzone.is_visible ? ' checked' : '';
    var hiddenClass = microzone.is_visible ? '' : ' hidden';
    var areaKm2 = getMicrozoneAreaKm2(microzone);
    var areaText = areaKm2 === null ? '-- km²' : areaKm2.toFixed(2) + ' km²';
    var warning = isLargeMicrozoneArea(microzone, areaKm2);

    return '' +
        '<div class="microzone-item' + hiddenClass + '" data-zone-id="' + zoneId + '" data-microzone-id="' + microzone.id + '">' +
        '  <div class="microzone-main">' +
        '    <input class="microzone-vis-toggle" type="checkbox"' + checked + ' onchange="event.stopPropagation(); toggleMicrozoneVisibility(' + zoneId + ', ' + microzone.id + ', this.checked);" />' +
        '    <span class="microzone-color-swatch" style="background:' + color + ';"></span>' +
        '    <span class="microzone-name">' + name + '</span>' +
        '    <button class="micro-rename-btn" onclick="event.stopPropagation(); renameMicrozone(' + zoneId + ', ' + microzone.id + ')" title="Rename microzone">&#x270F;</button>' +
        '    <button class="micro-delete-btn" onclick="event.stopPropagation(); deleteMicrozone(' + zoneId + ', ' + microzone.id + ')" title="Delete microzone">&times;</button>' +
        '  </div>' +
        '  <div class="microzone-meta">' +
        '    <span class="microzone-area-badge">' + areaText + '</span>' +
        (warning ? '    <span class="microzone-warning-badge">may cause slowdowns</span>' : '') +
        '  </div>' +
        '</div>';
}

function renderMicrozoneList(zoneId, microzones) {
    var container = document.getElementById('microzoneList_' + zoneId);
    if (!container) return;

    var items = Array.isArray(microzones) ? microzones : [];
    if (items.length === 0) {
        container.innerHTML = '<p class="microzone-empty">No microzones yet.</p>';
        updateZoneMicroCount(zoneId, 0);
        return;
    }

    var html = '';
    items.forEach(function(microzone) {
        html += microzoneItemHtml(zoneId, microzone);
    });
    container.innerHTML = html;
    updateZoneMicroCount(zoneId, items.length);
}

async function loadMicrozonesForZone(zoneId, forceRefresh, includeGeojson) {
    var wantsGeojson = !!includeGeojson;
    var cached = window.zoneMicrozones[zoneId];
    var cacheHasGeojson = microzonesContainGeojson(cached);
    if (!forceRefresh && Array.isArray(cached) && (!wantsGeojson || cacheHasGeojson)) {
        renderMicrozoneList(zoneId, cached);
        return cached;
    }

    try {
        var url = '/api/v1/zones/' + zoneId + '/microzones/';
        if (wantsGeojson) {
            url += '?include_geojson=true';
        }
        var response = await _authenticatedFetch(url);
        if (!response.ok) {
            renderMicrozoneList(zoneId, []);
            return [];
        }
        var data = await response.json();
        var microzones = data.microzones || [];
        window.zoneMicrozones[zoneId] = microzones;
        renderMicrozoneList(zoneId, microzones);

        if (window.microzonesDrawnByZone[zoneId]) {
            if (microzonesContainGeojson(microzones)) {
                pruneMicrozoneLayerCacheForZoneMicrozones(zoneId, microzones);
                syncMicrozoneLayersForZone(zoneId);
            } else if (!wantsGeojson) {
                loadMicrozonesForZone(zoneId, true, true).catch(function(e) {
                    console.error('Error reloading microzones with geojson for zone', zoneId, e);
                });
            }
        }
        return microzones;
    } catch (e) {
        console.error('Error loading microzones for zone', zoneId, e);
        renderMicrozoneList(zoneId, []);
        return [];
    }
}

function getDrawnLayerType(layer) {
    if (layer instanceof L.Circle) return 'circle';
    if (layer instanceof L.Rectangle) return 'rectangle';
    if (layer instanceof L.Marker) return 'marker';
    if (layer instanceof L.Polyline && !(layer instanceof L.Polygon)) return 'polyline';
    return 'polygon';
}

function normalizeGeoJsonFeature(layer) {
    var geojson = layer.toGeoJSON();
    if (geojson.type !== 'Feature') {
        return { type: 'Feature', geometry: geojson, properties: {} };
    }
    return geojson;
}

async function createMicrozoneFromDrawing(zoneId) {
    if (!window.drawnItems || window.drawnItems.getLayers().length === 0) {
        alert('Draw a shape on the map first, then add it as a microzone.');
        return;
    }

    var name = prompt('Microzone name:');
    if (!name || !name.trim()) {
        return;
    }

    var layers = window.drawnItems.getLayers();
    var layer = layers[layers.length - 1];
    var geojson = normalizeGeoJsonFeature(layer);
    var microzoneType = getDrawnLayerType(layer);

    try {
        var response = await _authenticatedFetch('/api/v1/zones/' + zoneId + '/microzones/', {
            method: 'POST',
            body: JSON.stringify({
                name: name.trim(),
                description: null,
                geojson: geojson,
                microzone_type: microzoneType,
                color: '#3388ff',
                tags: []
            })
        });

        if (!response.ok) {
            var error = await response.json().catch(function() { return {}; });
            alert('Failed to create microzone: ' + (error.detail || 'unknown error'));
            return;
        }

        var data = await response.json();
        var microzone = data.microzone;

        window.drawnItems.removeLayer(layer);
        updateSaveAsZoneButton();

        if (!Array.isArray(window.zoneMicrozones[zoneId])) {
            window.zoneMicrozones[zoneId] = [];
        }
        window.zoneMicrozones[zoneId].unshift(microzone);
        renderMicrozoneList(zoneId, window.zoneMicrozones[zoneId]);

        if (window.microzonesDrawnByZone[zoneId]) {
            syncMicrozoneLayer(microzone);
        }
    } catch (e) {
        console.error('Error creating microzone:', e);
        alert('Failed to create microzone. Please try again.');
    }
}

function findMicrozoneIndex(zoneId, microzoneId) {
    if (!Array.isArray(window.zoneMicrozones[zoneId])) return -1;
    return window.zoneMicrozones[zoneId].findIndex(function(item) {
        return Number(item.id) === Number(microzoneId);
    });
}

async function renameMicrozone(zoneId, microzoneId) {
    var currentName = 'Microzone';
    var index = findMicrozoneIndex(zoneId, microzoneId);
    if (index >= 0) {
        currentName = window.zoneMicrozones[zoneId][index].name || currentName;
    }
    var newName = prompt('Rename microzone:', currentName);
    if (!newName || !newName.trim()) return;

    try {
        var response = await _authenticatedFetch('/api/v1/zones/' + zoneId + '/microzones/' + microzoneId, {
            method: 'PATCH',
            body: JSON.stringify({ name: newName.trim() })
        });
        if (!response.ok) {
            alert('Failed to rename microzone.');
            return;
        }
        var data = await response.json();
        if (index >= 0) {
            window.zoneMicrozones[zoneId][index] = data.microzone;
            renderMicrozoneList(zoneId, window.zoneMicrozones[zoneId]);
            if (window.microzonesDrawnByZone[zoneId]) {
                syncMicrozoneLayer(data.microzone);
            }
        } else {
            await loadMicrozonesForZone(zoneId, true, window.microzonesDrawnByZone[zoneId]);
        }
    } catch (e) {
        console.error('Error renaming microzone:', e);
    }
}

async function deleteMicrozone(zoneId, microzoneId) {
    if (!confirm('Delete this microzone?')) return;

    try {
        var response = await _authenticatedFetch('/api/v1/zones/' + zoneId + '/microzones/' + microzoneId, {
            method: 'DELETE'
        });
        if (!response.ok) {
            alert('Failed to delete microzone.');
            return;
        }

        removeMicrozoneLayer(microzoneId, true);

        if (Array.isArray(window.zoneMicrozones[zoneId])) {
            window.zoneMicrozones[zoneId] = window.zoneMicrozones[zoneId].filter(function(item) {
                return Number(item.id) !== Number(microzoneId);
            });
            renderMicrozoneList(zoneId, window.zoneMicrozones[zoneId]);
        } else {
            await loadMicrozonesForZone(zoneId, true, window.microzonesDrawnByZone[zoneId]);
        }
    } catch (e) {
        console.error('Error deleting microzone:', e);
    }
}

async function toggleMicrozoneVisibility(zoneId, microzoneId, forceVisible) {
    var visible = !!forceVisible;
    try {
        var response = await _authenticatedFetch('/api/v1/zones/' + zoneId + '/microzones/' + microzoneId, {
            method: 'PATCH',
            body: JSON.stringify({ is_visible: visible })
        });
        if (!response.ok) {
            alert('Failed to update microzone visibility.');
            return;
        }
        var data = await response.json();
        var index = findMicrozoneIndex(zoneId, microzoneId);
        if (index >= 0) {
            window.zoneMicrozones[zoneId][index] = data.microzone;
            renderMicrozoneList(zoneId, window.zoneMicrozones[zoneId]);
        } else {
            await loadMicrozonesForZone(zoneId, true, window.microzonesDrawnByZone[zoneId]);
        }

        if (window.microzonesDrawnByZone[zoneId]) {
            syncMicrozoneLayer(data.microzone);
        } else if (!visible) {
            removeMicrozoneLayer(microzoneId, false);
        }
    } catch (e) {
        console.error('Error toggling microzone visibility:', e);
    }
}

function toggleZoneCard(zoneId) {
    var body = document.getElementById('zoneBody_' + zoneId);
    if (!body) return;

    var isOpen = body.style.display === 'block';
    body.style.display = isOpen ? 'none' : 'block';

    var indicator = document.querySelector('.zone-item[data-zone-id="' + zoneId + '"] .zone-expand-indicator');
    if (indicator) {
        indicator.textContent = isOpen ? '▼' : '▲';
    }

    if (!isOpen) {
        loadMicrozonesForZone(zoneId, false, window.microzonesDrawnByZone[zoneId]);
    }
}

function highlightZoneCard(zoneId) {
    var item = getZoneItemElement(zoneId);
    if (!item) return;
    item.classList.add('zone-item-focused');
    window.setTimeout(function() {
        item.classList.remove('zone-item-focused');
    }, ZONE_CARD_FOCUS_DURATION_MS);
}

function focusZoneCard(zoneId, ensureOpen) {
    var item = getZoneItemElement(zoneId);
    if (!item) return;

    if (ensureOpen) {
        var body = document.getElementById('zoneBody_' + zoneId);
        if (body && body.style.display !== 'block') {
            body.style.display = 'block';
            var indicator = item.querySelector('.zone-expand-indicator');
            if (indicator) indicator.textContent = '▲';
            loadMicrozonesForZone(zoneId, false, window.microzonesDrawnByZone[zoneId]);
        }
    }

    item.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    highlightZoneCard(zoneId);
}

function openZoneWorkflow(zoneId, workflow, event) {
    if (event && typeof event.preventDefault === 'function') {
        event.preventDefault();
    }

    var resolvedWorkflow = workflow === 'rents' ? 'rents' : 'sales';
    if (typeof window.showMappingView === 'function') {
        window.showMappingView({
            zoneId: Number(zoneId),
            workflow: resolvedWorkflow,
            source: 'zone-card'
        });
    }
    return false;
}

function ensureMappingZoneContextElement() {
    var existing = document.getElementById('mappingZoneContext');
    if (existing) return existing;

    var header = document.querySelector('#mappingView .mapping-header');
    if (!header) return null;

    var element = document.createElement('div');
    element.id = 'mappingZoneContext';
    element.className = 'mapping-zone-context';
    element.style.display = 'none';
    header.appendChild(element);
    return element;
}

window.applyMappingZoneContext = async function(context) {
    var contextEl = ensureMappingZoneContextElement();
    if (!contextEl) return;

    if (!context || !Number.isFinite(Number(context.zoneId))) {
        window.currentMappingZoneContext = null;
        contextEl.style.display = 'none';
        contextEl.classList.remove('is-error');
        contextEl.innerHTML = '';
        return;
    }

    var zoneId = Number(context.zoneId);
    var workflow = context.workflow === 'rents' ? 'rents' : 'sales';
    window.currentMappingZoneContext = {
        zoneId: zoneId,
        workflow: workflow
    };

    contextEl.style.display = 'inline-flex';
    contextEl.classList.remove('is-error');
    contextEl.innerHTML =
        '<span class="mapping-zone-workflow">' + formatWorkflowLabel(workflow) + '</span>' +
        '<span class="mapping-zone-name">Loading zone #' + zoneId + '...</span>';

    try {
        var response = await _authenticatedFetch('/api/v1/zones/' + zoneId);
        if (!response.ok) {
            throw new Error('Zone not found');
        }
        var data = await response.json();
        var zone = data.zone || {};

        contextEl.innerHTML =
            '<span class="mapping-zone-workflow">' + formatWorkflowLabel(workflow) + '</span>' +
            '<span class="mapping-zone-name">' + escapeHtml(zone.name || ('Zone #' + zoneId)) + '</span>';

        if (!window.savedZones[zoneId] || !window.savedZones[zoneId].layer) {
            renderZoneOnMap(zone, true);
        }

        focusZoneCard(zoneId, true);
        await zoomToZone(zoneId);
    } catch (e) {
        contextEl.classList.add('is-error');
        contextEl.innerHTML =
            '<span class="mapping-zone-workflow">' + formatWorkflowLabel(workflow) + '</span>' +
            '<span class="mapping-zone-name">Zone #' + zoneId + ' unavailable</span>';
    }
};

function zoneCardHtml(zone) {
    var visibleClass = zone.is_visible ? '' : ' hidden';
    var dateTxt = zone.created_at ? zone.created_at.substring(0, 10) : '';
    var typeTxt = zone.polygon_type || 'polygon';
    var safeName = escapeHtml(zone.name || 'Unnamed');
    var color = zone.color || '#3388ff';
    var checked = zone.is_visible ? ' checked' : '';

    return '' +
        '<div class="zone-item' + visibleClass + '" data-zone-id="' + zone.id + '">' +
        '  <div class="zone-item-header" onclick="toggleZoneCard(' + zone.id + ')">' +
        '    <div class="zone-item-main">' +
        '      <input class="zone-vis-toggle" type="checkbox"' + checked + ' onchange="event.stopPropagation(); toggleZoneVisibility(' + zone.id + ', this.checked);" />' +
        '      <span class="zone-color-swatch" style="background:' + color + ';"></span>' +
        '      <span class="zone-name">' + safeName + '</span>' +
        '      <span class="zone-micro-count" id="zoneMicroCount_' + zone.id + '">0</span>' +
        '      <span class="zone-expand-indicator">▼</span>' +
        '    </div>' +
        '    <div class="zone-item-actions">' +
        '      <button class="zone-rename-btn" onclick="event.stopPropagation(); editZone(' + zone.id + ')" title="Rename zone">&#x270F;</button>' +
        '      <button class="zone-delete-btn" onclick="event.stopPropagation(); deleteZone(' + zone.id + ')" title="Delete zone">&#x1F5D1;</button>' +
        '    </div>' +
        '  </div>' +
        '  <div class="zone-item-body" id="zoneBody_' + zone.id + '" style="display:none;">' +
        '    <div class="zone-item-meta"><small>' + typeTxt + ' | ' + dateTxt + ' | micros: <span id="zoneMicroCountBody_' + zone.id + '">0</span></small></div>' +
        '    <div class="zone-business-actions">' +
        '      <button type="button" class="zone-sales-btn" onclick="event.stopPropagation(); return openZoneWorkflow(' + zone.id + ', &quot;sales&quot;, event);">Sales</button>' +
        '      <button type="button" class="zone-rents-btn" onclick="event.stopPropagation(); return openZoneWorkflow(' + zone.id + ', &quot;rents&quot;, event);">Rents</button>' +
        '    </div>' +
        '    <div class="microzone-toolbar">' +
        '      <button type="button" class="add-micro-btn" onclick="event.stopPropagation(); createMicrozoneFromDrawing(' + zone.id + ')">Add Microzone</button>' +
        '      <button type="button" class="draw-all-micros-btn" onclick="event.stopPropagation(); drawAllMicrozonesForZone(' + zone.id + ')">Draw All Micros</button>' +
        '    </div>' +
        '    <div class="microzone-list" id="microzoneList_' + zone.id + '">' +
        '      <p class="microzone-empty">No microzones yet.</p>' +
        '    </div>' +
        '  </div>' +
        '</div>';
}

function renderZoneList(zones) {
    var container = document.getElementById('zoneList');
    var bulkActions = document.getElementById('zoneBulkActions');
    var noResults = document.getElementById('zoneSearchNoResults');
    if (!container) return;

    if (!zones || zones.length === 0) {
        container.innerHTML = '<p class="zone-empty-message">No zones saved yet. Draw a shape and save it as a zone.</p>';
        if (bulkActions) bulkActions.style.display = 'none';
        if (noResults) noResults.style.display = 'none';
        updateZoneCountBadge(0);
        window.zoneMicrozones = {};
        return;
    }

    if (bulkActions) bulkActions.style.display = 'block';
    updateZoneCountBadge(zones.length);
    window.zoneMicrozones = {};

    var html = '';
    zones.forEach(function(zone) {
        html += zoneCardHtml(zone);
    });

    container.innerHTML = html;
    zones.forEach(function(zone) {
        loadMicrozonesForZone(zone.id, true, window.microzonesDrawnByZone[zone.id]);
    });
    applyZoneSearchFilter();
}

function addZoneToList(zone) {
    var container = document.getElementById('zoneList');
    if (!container) return;

    // Remove empty message if present
    var emptyMsg = container.querySelector('.zone-empty-message');
    if (emptyMsg) emptyMsg.remove();

    var bulkActions = document.getElementById('zoneBulkActions');
    if (bulkActions) bulkActions.style.display = 'block';
    container.insertAdjacentHTML('afterbegin', zoneCardHtml(zone));
    updateZoneCountBadge(container.querySelectorAll('.zone-item').length);
    window.zoneMicrozones[zone.id] = [];
    window.savedZones[zone.id] = { layer: null, visible: zone.is_visible !== false };
    loadMicrozonesForZone(zone.id, true, false);
    applyZoneSearchFilter();
}

function renderZoneOnMap(zone, forceVisible) {
    if (!window.zoneLayerGroup || !zone || !zone.geojson) return;

    var zoneId = Number(zone.id);
    var existing = window.savedZones[zoneId];
    if (existing && existing.layer && window.zoneLayerGroup) {
        window.zoneLayerGroup.removeLayer(existing.layer);
    }

    var geojson = zone.geojson;
    var color = zone.color || '#3388ff';
    var name = zone.name || 'Unnamed Zone';
    var shouldRender = forceVisible === true || zone.is_visible !== false;

    var zoneLayer = L.geoJSON(geojson, {
        style: function() {
            return {
                color: color,
                weight: 3,
                opacity: 0.8,
                fillOpacity: 0.25,
                fillColor: color
            };
        },
        pointToLayer: function(f, latlng) {
            return L.circleMarker(latlng, {
                radius: 8,
                color: color,
                fillColor: color,
                fillOpacity: 0.5
            });
        }
    });

    zoneLayer.bindPopup('<strong>' + escapeHtml(name) + '</strong><br><small>ID: ' + zoneId + '</small>');
    if (shouldRender) {
        window.zoneLayerGroup.addLayer(zoneLayer);
    }

    window.savedZones[zoneId] = {
        layer: zoneLayer,
        visible: shouldRender
    };
}

async function toggleZoneVisibility(zoneId, forceVisible) {
    var entry = window.savedZones[zoneId];
    var newVisible = typeof forceVisible === 'boolean' ? forceVisible : !(entry && entry.visible);

    try {
        var response = await _authenticatedFetch('/api/v1/zones/' + zoneId, {
            method: 'PATCH',
            body: JSON.stringify({ is_visible: newVisible })
        });

        if (!response.ok) return;

        if (!entry) {
            entry = { layer: null, visible: false };
            window.savedZones[zoneId] = entry;
        }

        if (newVisible) {
            if (entry.layer && window.zoneLayerGroup) {
                window.zoneLayerGroup.addLayer(entry.layer);
                entry.visible = true;
            } else {
                var zoneResponse = await _authenticatedFetch('/api/v1/zones/' + zoneId);
                if (zoneResponse.ok) {
                    var zoneData = await zoneResponse.json();
                    renderZoneOnMap(zoneData.zone, true);
                    entry = window.savedZones[zoneId];
                }
            }
        } else if (entry.layer && window.zoneLayerGroup) {
            window.zoneLayerGroup.removeLayer(entry.layer);
            entry.visible = false;
        } else {
            entry.visible = false;
        }

        // Update list item styling
        var item = getZoneItemElement(zoneId);
        if (item) {
            item.classList.toggle('hidden', !newVisible);
        }
        var toggle = document.querySelector('.zone-item[data-zone-id="' + zoneId + '"] .zone-vis-toggle');
        if (toggle) {
            toggle.checked = newVisible;
        }

        syncMicrozoneLayersForZone(zoneId);
    } catch (e) {
        console.error('Error toggling zone visibility:', e);
    }
}

async function zoomToZone(zoneId) {
    var entry = window.savedZones[zoneId];
    if (entry && entry.layer) {
        fitMapToLayerBounds([entry.layer], [30, 30]);
        return;
    }

    // If layer not loaded, fetch from API
    try {
        var response = await _authenticatedFetch('/api/v1/zones/' + zoneId);
        if (response.ok) {
            var data = await response.json();
            renderZoneOnMap(data.zone, true);
            var newEntry = window.savedZones[zoneId];
            if (newEntry && newEntry.layer) {
                fitMapToLayerBounds([newEntry.layer], [30, 30]);
            }
        }
    } catch (e) {
        console.error('Error zooming to zone:', e);
    }
}

async function editZone(zoneId) {
    var item = getZoneItemElement(zoneId);
    if (!item) return;

    // Fetch current zone data
    try {
        var response = await _authenticatedFetch('/api/v1/zones/' + zoneId);
        if (!response.ok) return;
        var data = await response.json();
        var zone = data.zone;

        item.innerHTML =
            '<div class="zone-edit-form">' +
            '  <input type="text" class="zone-edit-name" value="' + (zone.name || '').replace(/"/g, '&quot;') + '" placeholder="Zone name" />' +
            '  <textarea class="zone-edit-desc" rows="2" placeholder="Description">' + (zone.description || '') + '</textarea>' +
            '  <div style="display:flex;gap:6px;align-items:center;">' +
            '    <input type="color" class="zone-edit-color" value="' + (zone.color || '#3388ff') + '" />' +
            '    <button onclick="submitZoneEdit(' + zoneId + ')" class="zone-save-btn" style="flex:1;">Save</button>' +
            '    <button onclick="loadAllZones()" class="zone-cancel-btn">Cancel</button>' +
            '  </div>' +
            '</div>';

    } catch (e) {
        console.error('Error editing zone:', e);
    }
}

async function submitZoneEdit(zoneId) {
    var item = getZoneItemElement(zoneId);
    if (!item) return;

    var nameInput = item.querySelector('.zone-edit-name');
    var descInput = item.querySelector('.zone-edit-desc');
    var colorInput = item.querySelector('.zone-edit-color');

    var updates = {};
    if (nameInput) updates.name = nameInput.value.trim() || null;
    if (descInput) updates.description = descInput.value.trim() || null;
    if (colorInput) updates.color = colorInput.value;

    try {
        var response = await _authenticatedFetch('/api/v1/zones/' + zoneId, {
            method: 'PATCH',
            body: JSON.stringify(updates)
        });

        if (response.ok) {
            // Refresh zone list and map
            loadAllZones();
        } else {
            alert('Failed to update zone.');
        }

    } catch (e) {
        console.error('Error submitting zone edit:', e);
    }
}

async function deleteZone(zoneId) {
    if (!confirm('Delete this zone? This cannot be undone.')) return;

    try {
        var response = await _authenticatedFetch('/api/v1/zones/' + zoneId, {
            method: 'DELETE'
        });

        if (!response.ok) {
            alert('Failed to delete zone.');
            return;
        }

        // Remove from map
        var entry = window.savedZones[zoneId];
        if (entry && entry.layer && window.zoneLayerGroup) {
            window.zoneLayerGroup.removeLayer(entry.layer);
        }
        delete window.savedZones[zoneId];
        delete window.zoneMicrozones[zoneId];
        delete window.microzonesDrawnByZone[zoneId];
        removeMicrozoneLayersForZone(zoneId, true);

        // Remove from list
        var item = getZoneItemElement(zoneId);
        if (item) item.remove();

        // Check if list is now empty
        var container = document.getElementById('zoneList');
        if (container && container.querySelectorAll('.zone-item').length === 0) {
            container.innerHTML = '<p class="zone-empty-message">No zones saved yet. Draw a shape and save it as a zone.</p>';
            var bulkActions = document.getElementById('zoneBulkActions');
            if (bulkActions) bulkActions.style.display = 'none';
            updateZoneCountBadge(0);
        } else if (container) {
            updateZoneCountBadge(container.querySelectorAll('.zone-item').length);
        }

        console.log('Zone deleted:', zoneId);
        applyZoneSearchFilter();

    } catch (e) {
        console.error('Error deleting zone:', e);
    }
}

async function showAllZones() {
    var ids = getAllZoneIdsFromList();
    if (ids.length === 0) return;

    try {
        await Promise.all([
            _authenticatedFetch('/api/v1/zones/visibility', {
                method: 'POST',
                body: JSON.stringify({ zone_ids: ids, is_visible: true })
            }),
            _authenticatedFetch('/api/v1/microzones/visibility', {
                method: 'POST',
                body: JSON.stringify({ zone_ids: ids, is_visible: true })
            })
        ]);
        await loadAllZones();
        applyZoneSearchFilter();
    } catch (e) {
        console.error('Error showing all zones:', e);
    }
}

async function hideAllZones() {
    var ids = getAllZoneIdsFromList();
    if (ids.length === 0) return;

    try {
        await Promise.all([
            _authenticatedFetch('/api/v1/zones/visibility', {
                method: 'POST',
                body: JSON.stringify({ zone_ids: ids, is_visible: false })
            }),
            _authenticatedFetch('/api/v1/microzones/visibility', {
                method: 'POST',
                body: JSON.stringify({ zone_ids: ids, is_visible: false })
            })
        ]);
        await loadAllZones();
        applyZoneSearchFilter();

    } catch (e) {
        console.error('Error hiding all zones:', e);
    }
}
