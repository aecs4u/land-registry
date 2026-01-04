// ========================================
// FOLIUM MAP INTERFACE FUNCTIONS
// ========================================
// These functions are for server-generated Folium maps (used in index.html)

// Global variables for Folium map interface
window.selectedPolygons = [];
window.drawnItems = null; // Initialize global drawnItems for export
window.geoJsonData = null;
window.hasData = false;
window.cadastralDataCache = null;
window.currentFileSelection = [];

// Export function (migrated from map.js)
window.exportDrawingsAsGeoJSON = function() {
    if (!window.drawnItems || window.drawnItems.getLayers().length === 0) {
        alert('No drawings to export');
        return;
    }

    const geojson = {
        type: 'FeatureCollection',
        features: []
    };

    window.drawnItems.eachLayer(function(layer) {
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
};

// Custom zoom control buttons for Folium map
function addCustomZoomControls(map) {
    console.log('[FoliumInterface] Adding custom zoom controls to map');

    // Try to find the zoom control, or fall back to fullscreen control bar
    let zoomControl = document.querySelector('.leaflet-control-zoom');

    // If no zoom control, try to find the fullscreen control bar
    if (!zoomControl) {
        zoomControl = document.querySelector('.leaflet-bar.leaflet-control');
        if (zoomControl) {
            console.log('[FoliumInterface] Using fullscreen control bar as container');
        }
    }

    if (!zoomControl) {
        console.warn('[FoliumInterface] Could not find zoom control or control bar, retrying in 500ms');
        setTimeout(() => addCustomZoomControls(map), 500);
        return;
    }

    // Check if buttons already exist
    if (document.querySelector('.leaflet-control-zoom-fit-all')) {
        console.log('[FoliumInterface] Custom zoom buttons already exist');
        return;
    }

    console.log('[FoliumInterface] Found control bar, adding custom buttons');

    // Create Fit All button
    const fitAllBtn = document.createElement('a');
    fitAllBtn.className = 'leaflet-control-zoom-fit-all';
    fitAllBtn.href = '#';
    fitAllBtn.title = 'Fit to all data';
    fitAllBtn.innerHTML = '⊞';
    fitAllBtn.setAttribute('role', 'button');
    fitAllBtn.setAttribute('aria-label', 'Fit to all data');

    fitAllBtn.onclick = function(e) {
        e.preventDefault();
        e.stopPropagation();
        autoZoomToAllPolygons();
        return false;
    };

    // Create Fit Selected button
    const fitSelectedBtn = document.createElement('a');
    fitSelectedBtn.className = 'leaflet-control-zoom-fit-selected';
    fitSelectedBtn.href = '#';
    fitSelectedBtn.title = 'Fit to selected polygons';
    fitSelectedBtn.innerHTML = '◎';
    fitSelectedBtn.setAttribute('role', 'button');
    fitSelectedBtn.setAttribute('aria-label', 'Fit to selected polygons');

    fitSelectedBtn.onclick = function(e) {
        e.preventDefault();
        e.stopPropagation();
        zoomToSelectedPolygons();
        return false;
    };

    // Create Window Zoom (box zoom) button
    const boxZoomBtn = document.createElement('a');
    boxZoomBtn.className = 'leaflet-control-zoom-box';
    boxZoomBtn.href = '#';
    boxZoomBtn.title = 'Zoom to window (draw rectangle)';
    boxZoomBtn.innerHTML = '⬚';
    boxZoomBtn.setAttribute('role', 'button');
    boxZoomBtn.setAttribute('aria-label', 'Zoom to window');

    let boxZoomActive = false;
    let boxZoomStartPoint = null;
    let boxZoomRect = null;

    boxZoomBtn.onclick = function(e) {
        e.preventDefault();
        e.stopPropagation();

        boxZoomActive = !boxZoomActive;

        if (boxZoomActive) {
            boxZoomBtn.classList.add('active');
            boxZoomBtn.style.backgroundColor = '#e0e0ff';
            map.dragging.disable();
            map.getContainer().style.cursor = 'crosshair';
            console.log('[FoliumInterface] Box zoom mode activated');
        } else {
            boxZoomBtn.classList.remove('active');
            boxZoomBtn.style.backgroundColor = '';
            map.dragging.enable();
            map.getContainer().style.cursor = '';
            if (boxZoomRect) {
                map.removeLayer(boxZoomRect);
                boxZoomRect = null;
            }
            console.log('[FoliumInterface] Box zoom mode deactivated');
        }
        return false;
    };

    // Box zoom mouse handlers
    map.on('mousedown', function(e) {
        if (boxZoomActive) {
            boxZoomStartPoint = e.latlng;
            if (boxZoomRect) {
                map.removeLayer(boxZoomRect);
            }
        }
    });

    map.on('mousemove', function(e) {
        if (boxZoomActive && boxZoomStartPoint) {
            const bounds = L.latLngBounds(boxZoomStartPoint, e.latlng);
            if (boxZoomRect) {
                boxZoomRect.setBounds(bounds);
            } else {
                boxZoomRect = L.rectangle(bounds, {
                    color: '#3388ff',
                    weight: 2,
                    fillOpacity: 0.2,
                    dashArray: '5, 5'
                }).addTo(map);
            }
        }
    });

    map.on('mouseup', function(e) {
        if (boxZoomActive && boxZoomStartPoint) {
            const bounds = L.latLngBounds(boxZoomStartPoint, e.latlng);

            // Only zoom if the rectangle is meaningful (not just a click)
            const startPoint = map.latLngToContainerPoint(boxZoomStartPoint);
            const endPoint = map.latLngToContainerPoint(e.latlng);
            const distance = Math.sqrt(
                Math.pow(endPoint.x - startPoint.x, 2) +
                Math.pow(endPoint.y - startPoint.y, 2)
            );

            if (distance > 20) {
                map.fitBounds(bounds, { padding: [10, 10] });
            }

            // Clean up
            if (boxZoomRect) {
                map.removeLayer(boxZoomRect);
                boxZoomRect = null;
            }
            boxZoomStartPoint = null;

            // Deactivate box zoom mode
            boxZoomActive = false;
            boxZoomBtn.classList.remove('active');
            boxZoomBtn.style.backgroundColor = '';
            map.dragging.enable();
            map.getContainer().style.cursor = '';
        }
    });

    // Create Reset View button
    const resetViewBtn = document.createElement('a');
    resetViewBtn.className = 'leaflet-control-zoom-reset';
    resetViewBtn.href = '#';
    resetViewBtn.title = 'Reset to Italy view';
    resetViewBtn.innerHTML = '🏠';
    resetViewBtn.setAttribute('role', 'button');
    resetViewBtn.setAttribute('aria-label', 'Reset view');

    resetViewBtn.onclick = function(e) {
        e.preventDefault();
        e.stopPropagation();
        // Reset to Italy view
        map.setView([41.8719, 12.5674], 6);
        console.log('[FoliumInterface] View reset to Italy');
        return false;
    };

    // Append buttons to zoom control
    zoomControl.appendChild(fitAllBtn);
    zoomControl.appendChild(fitSelectedBtn);
    zoomControl.appendChild(boxZoomBtn);
    zoomControl.appendChild(resetViewBtn);

    console.log('[FoliumInterface] Custom zoom buttons added successfully');
}

// Function to zoom to selected polygons
function zoomToSelectedPolygons() {
    if (!window.selectedPolygons || window.selectedPolygons.length === 0) {
        alert('No polygons selected. Click on polygons to select them first.');
        return;
    }

    let bounds = null;
    window.selectedPolygons.forEach(polygon => {
        if (polygon.getBounds) {
            const layerBounds = polygon.getBounds();
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
        const mapElements = document.querySelectorAll('.leaflet-container');
        if (mapElements.length > 0) {
            const mapId = mapElements[0].id;
            if (window[mapId]) {
                window[mapId].fitBounds(bounds, { padding: [20, 20], maxZoom: 18 });
                console.log('[FoliumInterface] Zoomed to selected polygons');
            }
        }
    }
}

// Function to auto-zoom to all polygons on the map
function autoZoomToAllPolygons() {
    console.log('[FoliumInterface] Auto-zooming to all polygons');

    const mapElements = document.querySelectorAll('.leaflet-container');
    if (mapElements.length === 0) {
        console.warn('[FoliumInterface] No map elements found');
        return;
    }

    const mapId = mapElements[0].id;
    const foliumMap = window[mapId];

    if (!foliumMap) {
        console.warn('[FoliumInterface] Folium map instance not found');
        return;
    }

    const bounds = calculateAllLayersBounds(foliumMap);

    if (bounds) {
        const latLngBounds = L.latLngBounds(
            [bounds.minLat, bounds.minLng],
            [bounds.maxLat, bounds.maxLng]
        );

        if (latLngBounds.isValid()) {
            foliumMap.fitBounds(latLngBounds, {
                padding: [20, 20],
                maxZoom: 18
            });
            console.log('[FoliumInterface] Map zoomed to fit all polygons');
        } else {
            console.warn('[FoliumInterface] Invalid bounds calculated');
        }
    } else {
        console.log('[FoliumInterface] No polygon data found to zoom to');
        alert('No polygon data loaded on the map.');
    }
}

// Function to initialize drawing controls on the Folium map
function initializeDrawingControls() {
    setTimeout(() => {
        const mapElements = document.querySelectorAll('.leaflet-container');
        console.log('Drawing controls init: found', mapElements.length, 'map elements');
        if (mapElements.length > 0) {
            const mapId = mapElements[0].id;
            console.log('Map ID:', mapId, 'Map object exists:', !!window[mapId], 'Already initialized:', !!window.drawingInitialized);
            if (window[mapId] && !window.drawingInitialized) {
                const map = window[mapId];
                console.log('Initializing drawing controls on Folium map');

                // Initialize FeatureGroup for drawn items
                window.drawnItems = new L.FeatureGroup();
                map.addLayer(window.drawnItems);

                // Initialize Draw Control
                const drawControl = new L.Control.Draw({
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
                        rectangle: {
                            shapeOptions: {
                                color: '#3388ff'
                            }
                        },
                        marker: true,
                        circlemarker: false
                    },
                    edit: {
                        featureGroup: window.drawnItems,
                        remove: true
                    }
                });
                map.addControl(drawControl);

                // NOTE: Export Control is now handled server-side via map.py ExportControl MacroElement
                // The client-side implementation below is commented out to avoid duplication
                /*
                const ExportControl = L.Control.extend({
                    options: {
                        position: 'bottomright'
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
                            if (typeof window.exportDrawingsAsGeoJSON === 'function') {
                                window.exportDrawingsAsGeoJSON();
                            } else {
                                alert('Export function not available');
                            }
                        });

                        return container;
                    }
                });

                map.addControl(new ExportControl());
                console.log('✅ Export control added to map at bottomright position');
                */

                // Note: Custom zoom controls are now initialized separately via initializeCustomZoomControlsWithRetry()

                // Add event listeners for drawing
                map.on('draw:created', function(e) {
                    const type = e.layerType;
                    const layer = e.layer;

                    // Add unique ID and properties
                    layer.feature = {
                        type: 'Feature',
                        properties: {
                            id: 'drawn_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9),
                            type: type,
                            created: new Date().toISOString(),
                            area: type === 'polygon' || type === 'rectangle' || type === 'circle' ?
                                  (L.GeometryUtil ? L.GeometryUtil.geodesicArea(layer.getLatLngs()[0]) : 'N/A') : null
                        }
                    };
                    
                    window.drawnItems.addLayer(layer);
                    console.log('Feature drawn:', type);
                });

                window.drawingInitialized = true;
            } else {
                console.warn('Drawing controls not initialized. Map object:', !!window[mapId], 'Already init:', !!window.drawingInitialized);
            }
        } else {
            console.warn('No leaflet-container elements found for drawing controls');
        }
    }, 2500); // Wait for map initialization
}

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
        const mapElements = document.querySelectorAll('.leaflet-container');
        if (mapElements.length > 0) {
            const mapId = mapElements[0].id;
            if (window[mapId]) {
                const foliumMap = window[mapId];

                foliumMap.eachLayer(function(layer) {
                    // Skip base tile layers
                    if (layer._url && layer._url.includes('tile')) {
                        return;
                    }
                    
                    // Skip drawing layers
                    if (window.drawnItems && window.drawnItems.hasLayer(layer)) {
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
        const mapElements = document.querySelectorAll('.leaflet-container');
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
        document.querySelectorAll('.upload-tab')[0].classList.add('active');
    } else if (tabName === 'cadastral') {
        document.getElementById('cadastralSelection').style.display = 'block';
        document.querySelectorAll('.upload-tab')[1].classList.add('active');
    } else if (tabName === 'database') {
        document.getElementById('databaseQuery').style.display = 'block';
        document.querySelectorAll('.upload-tab')[2].classList.add('active');
        // Initialize database stats and filters on first view
        initDatabaseFilters();
    }
}

// ========================================
// DATABASE QUERY FUNCTIONS
// ========================================

// Cache for database hierarchy data
window.dbHierarchyCache = {
    regions: [],
    provinces: {},
    comuni: {},
    fogli: {}
};

// Initialize database filters and load statistics
async function initDatabaseFilters() {
    // Only initialize once
    if (window.dbFiltersInitialized) return;

    try {
        // Load database statistics
        const statsResponse = await fetch('/api/v1/cadastral/statistics');
        if (statsResponse.ok) {
            const stats = await statsResponse.json();
            document.getElementById('dbTotalParcels').textContent = stats.total_parcels?.toLocaleString() || '0';
            document.getElementById('dbTotalRegions').textContent = Object.keys(stats.by_region || {}).length || '0';
        }

        // Load regions for dropdown
        const hierResponse = await fetch('/api/v1/cadastral/hierarchy');
        if (hierResponse.ok) {
            const data = await hierResponse.json();
            window.dbHierarchyCache.regions = data.regions || [];

            const regionSelect = document.getElementById('dbRegione');
            regionSelect.innerHTML = '<option value="">All Regions</option>';
            window.dbHierarchyCache.regions.forEach(region => {
                const opt = document.createElement('option');
                opt.value = region;
                opt.textContent = region;
                regionSelect.appendChild(opt);
            });
        }

        window.dbFiltersInitialized = true;
    } catch (error) {
        console.error('Error initializing database filters:', error);
    }
}

// Update provinces dropdown when region changes
async function updateDbProvinces() {
    const regionSelect = document.getElementById('dbRegione');
    const provinciaSelect = document.getElementById('dbProvincia');
    const comuneSelect = document.getElementById('dbComune');
    const foglioSelect = document.getElementById('dbFoglio');
    const particellaInput = document.getElementById('dbParticella');

    const regione = regionSelect.value;

    // Reset dependent dropdowns
    provinciaSelect.innerHTML = '<option value="">All Provinces</option>';
    comuneSelect.innerHTML = '<option value="">All Municipalities</option>';
    foglioSelect.innerHTML = '<option value="">All Fogli</option>';

    provinciaSelect.disabled = !regione;
    comuneSelect.disabled = true;
    foglioSelect.disabled = true;
    particellaInput.disabled = true;

    if (!regione) return;

    try {
        // Check cache first
        if (window.dbHierarchyCache.provinces[regione]) {
            populateProvinceSelect(window.dbHierarchyCache.provinces[regione]);
            return;
        }

        const response = await fetch(`/api/v1/cadastral/hierarchy?regione=${encodeURIComponent(regione)}`);
        if (response.ok) {
            const data = await response.json();
            window.dbHierarchyCache.provinces[regione] = data.provinces || [];
            populateProvinceSelect(data.provinces || []);
        }
    } catch (error) {
        console.error('Error loading provinces:', error);
    }
}

function populateProvinceSelect(provinces) {
    const provinciaSelect = document.getElementById('dbProvincia');
    provinces.forEach(prov => {
        const opt = document.createElement('option');
        opt.value = prov;
        opt.textContent = prov;
        provinciaSelect.appendChild(opt);
    });
}

// Update comuni dropdown when province changes
async function updateDbComuni() {
    const regionSelect = document.getElementById('dbRegione');
    const provinciaSelect = document.getElementById('dbProvincia');
    const comuneSelect = document.getElementById('dbComune');
    const foglioSelect = document.getElementById('dbFoglio');
    const particellaInput = document.getElementById('dbParticella');

    const regione = regionSelect.value;
    const provincia = provinciaSelect.value;

    // Reset dependent dropdowns
    comuneSelect.innerHTML = '<option value="">All Municipalities</option>';
    foglioSelect.innerHTML = '<option value="">All Fogli</option>';

    comuneSelect.disabled = !provincia;
    foglioSelect.disabled = true;
    particellaInput.disabled = true;

    if (!provincia) return;

    try {
        const cacheKey = `${regione}|${provincia}`;
        if (window.dbHierarchyCache.comuni[cacheKey]) {
            populateComuneSelect(window.dbHierarchyCache.comuni[cacheKey]);
            return;
        }

        const response = await fetch(`/api/v1/cadastral/hierarchy?regione=${encodeURIComponent(regione)}&provincia=${encodeURIComponent(provincia)}`);
        if (response.ok) {
            const data = await response.json();
            window.dbHierarchyCache.comuni[cacheKey] = data.comuni || [];
            populateComuneSelect(data.comuni || []);
        }
    } catch (error) {
        console.error('Error loading comuni:', error);
    }
}

function populateComuneSelect(comuni) {
    const comuneSelect = document.getElementById('dbComune');
    comuni.forEach(comune => {
        const opt = document.createElement('option');
        // comune might be {code, name} or just a string
        if (typeof comune === 'object') {
            opt.value = comune.code;
            opt.textContent = `${comune.name} (${comune.code})`;
        } else {
            opt.value = comune;
            opt.textContent = comune;
        }
        comuneSelect.appendChild(opt);
    });
}

// Update fogli dropdown when comune changes
async function updateDbFogli() {
    const regionSelect = document.getElementById('dbRegione');
    const provinciaSelect = document.getElementById('dbProvincia');
    const comuneSelect = document.getElementById('dbComune');
    const foglioSelect = document.getElementById('dbFoglio');
    const particellaInput = document.getElementById('dbParticella');

    const regione = regionSelect.value;
    const provincia = provinciaSelect.value;
    const comune = comuneSelect.value;

    // Reset foglio dropdown
    foglioSelect.innerHTML = '<option value="">All Fogli</option>';

    foglioSelect.disabled = !comune;
    particellaInput.disabled = !comune;

    if (!comune) return;

    try {
        const cacheKey = `${regione}|${provincia}|${comune}`;
        if (window.dbHierarchyCache.fogli[cacheKey]) {
            populateFoglioSelect(window.dbHierarchyCache.fogli[cacheKey]);
            return;
        }

        const response = await fetch(`/api/v1/cadastral/hierarchy?regione=${encodeURIComponent(regione)}&provincia=${encodeURIComponent(provincia)}&comune=${encodeURIComponent(comune)}`);
        if (response.ok) {
            const data = await response.json();
            window.dbHierarchyCache.fogli[cacheKey] = data.fogli || [];
            populateFoglioSelect(data.fogli || []);
        }
    } catch (error) {
        console.error('Error loading fogli:', error);
    }
}

function populateFoglioSelect(fogli) {
    const foglioSelect = document.getElementById('dbFoglio');
    fogli.forEach(foglio => {
        const opt = document.createElement('option');
        opt.value = foglio;
        opt.textContent = `Foglio ${foglio}`;
        foglioSelect.appendChild(opt);
    });
}

// Toggle collapsible section
function toggleDbSection(sectionId) {
    const section = document.getElementById(sectionId);
    const content = section.querySelector('.collapsible-content');
    const icon = section.querySelector('.collapse-icon');

    if (section.classList.contains('collapsed')) {
        section.classList.remove('collapsed');
        content.style.display = 'block';
        icon.textContent = '▼';
    } else {
        section.classList.add('collapsed');
        content.style.display = 'none';
        icon.textContent = '▶';
    }
}

// Use current map bounds as spatial filter
function useMapBoundsAsFilter() {
    if (!window.map) {
        alert('Map not available');
        return;
    }

    const bounds = window.map.getBounds();
    document.getElementById('dbBboxMinLon').value = bounds.getWest().toFixed(6);
    document.getElementById('dbBboxMaxLon').value = bounds.getEast().toFixed(6);
    document.getElementById('dbBboxMinLat').value = bounds.getSouth().toFixed(6);
    document.getElementById('dbBboxMaxLat').value = bounds.getNorth().toFixed(6);
}

// Clear bbox filter
function clearBboxFilter() {
    document.getElementById('dbBboxMinLon').value = '';
    document.getElementById('dbBboxMaxLon').value = '';
    document.getElementById('dbBboxMinLat').value = '';
    document.getElementById('dbBboxMaxLat').value = '';
}

// Build query request from form values
function buildDbQueryRequest() {
    const request = {};

    // Geographic filters
    const regione = document.getElementById('dbRegione').value;
    const provincia = document.getElementById('dbProvincia').value;
    const comune = document.getElementById('dbComune').value;

    if (regione) request.regione = regione;
    if (provincia) request.provincia = provincia;
    if (comune) request.comune = comune;

    // Cadastral filters
    const foglio = document.getElementById('dbFoglio').value;
    const particella = document.getElementById('dbParticella').value;
    const layerType = document.querySelector('input[name="dbLayerType"]:checked').value;

    if (foglio) request.foglio = parseInt(foglio);
    if (layerType) request.layer_type = layerType;

    // Parse particella (can be single number or range like "1-100")
    if (particella) {
        if (particella.includes('-')) {
            const [min, max] = particella.split('-').map(n => parseInt(n.trim()));
            if (!isNaN(min)) request.particella_min = min;
            if (!isNaN(max)) request.particella_max = max;
        } else if (particella.includes(',')) {
            request.particella_list = particella.split(',').map(n => parseInt(n.trim())).filter(n => !isNaN(n));
        } else {
            const num = parseInt(particella);
            if (!isNaN(num)) request.particella = num;
        }
    }

    // Spatial filters
    const minLon = document.getElementById('dbBboxMinLon').value;
    const maxLon = document.getElementById('dbBboxMaxLon').value;
    const minLat = document.getElementById('dbBboxMinLat').value;
    const maxLat = document.getElementById('dbBboxMaxLat').value;

    if (minLon) request.bbox_min_lon = parseFloat(minLon);
    if (maxLon) request.bbox_max_lon = parseFloat(maxLon);
    if (minLat) request.bbox_min_lat = parseFloat(minLat);
    if (maxLat) request.bbox_max_lat = parseFloat(maxLat);

    return request;
}

// Preview query count
async function previewDbQuery() {
    const request = buildDbQueryRequest();
    request.count_only = true;

    const btn = document.getElementById('previewDbBtn');
    btn.disabled = true;
    btn.textContent = 'Counting...';

    try {
        const response = await fetch('/api/v1/cadastral/query', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(request)
        });

        if (response.ok) {
            const data = await response.json();
            const count = data.count || data.total || 0;
            document.getElementById('dbResultCount').textContent = count.toLocaleString();
            document.getElementById('dbQueryInfo').style.display = 'block';
        } else {
            const error = await response.json();
            alert(`Query error: ${error.detail || 'Unknown error'}`);
        }
    } catch (error) {
        console.error('Preview query error:', error);
        alert(`Error: ${error.message}`);
    } finally {
        btn.disabled = false;
        btn.textContent = 'Preview Count';
    }
}

// Execute database query and load results on map
async function executeDbQuery() {
    const request = buildDbQueryRequest();
    request.limit = 10000; // Reasonable limit for map display

    const btn = document.getElementById('executeDbBtn');
    btn.disabled = true;
    btn.textContent = 'Loading...';

    try {
        const response = await fetch('/api/v1/cadastral/query', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(request)
        });

        if (response.ok) {
            const data = await response.json();

            if (data.geojson && data.geojson.features && data.geojson.features.length > 0) {
                // Clear existing layers and add new ones
                if (window.geoJsonLayer) {
                    window.map.removeLayer(window.geoJsonLayer);
                }

                window.geoJsonData = data.geojson;
                window.hasData = true;

                // Add to map with styling
                window.geoJsonLayer = L.geoJSON(data.geojson, {
                    style: function(feature) {
                        return {
                            color: '#3388ff',
                            weight: 2,
                            opacity: 0.8,
                            fillOpacity: 0.3
                        };
                    },
                    onEachFeature: function(feature, layer) {
                        if (feature.properties) {
                            let popupContent = '<div class="popup-content">';
                            for (const [key, value] of Object.entries(feature.properties)) {
                                if (value !== null && value !== undefined) {
                                    popupContent += `<b>${key}:</b> ${value}<br>`;
                                }
                            }
                            popupContent += '</div>';
                            layer.bindPopup(popupContent);
                        }

                        // Click handler for selection
                        layer.on('click', function(e) {
                            handlePolygonClick(e, layer, feature);
                        });
                    }
                }).addTo(window.map);

                // Fit bounds to loaded data
                const bounds = window.geoJsonLayer.getBounds();
                if (bounds.isValid()) {
                    window.map.fitBounds(bounds, { padding: [50, 50] });
                }

                // Update UI
                const count = data.geojson.features.length;
                document.getElementById('tableInfo').textContent = `${count.toLocaleString()} parcels loaded from database`;
                document.getElementById('polygonCount').textContent = `${count.toLocaleString()} polygons loaded`;

                // Enable polygon management buttons
                document.getElementById('removePolygonsBtn').disabled = false;
                document.getElementById('zoomToPolygonsBtn').disabled = false;

                // Update result info
                document.getElementById('dbResultCount').textContent = count.toLocaleString();
                document.getElementById('dbQueryInfo').style.display = 'block';

            } else {
                alert('No parcels found matching the filters');
            }
        } else {
            const error = await response.json();
            alert(`Query error: ${error.detail || 'Unknown error'}`);
        }
    } catch (error) {
        console.error('Execute query error:', error);
        alert(`Error: ${error.message}`);
    } finally {
        btn.disabled = false;
        btn.textContent = 'Load Parcels';
    }
}

// Search by cadastral reference
async function searchCadastralReference() {
    const searchInput = document.getElementById('dbQuickSearch');
    const reference = searchInput.value.trim();

    if (!reference) {
        alert('Please enter a cadastral reference');
        return;
    }

    try {
        const response = await fetch(`/api/v1/cadastral/search/${encodeURIComponent(reference)}`);

        if (response.ok) {
            const data = await response.json();

            if (data.geojson && data.geojson.features && data.geojson.features.length > 0) {
                // Clear existing layers and add new ones
                if (window.geoJsonLayer) {
                    window.map.removeLayer(window.geoJsonLayer);
                }

                window.geoJsonData = data.geojson;
                window.hasData = true;

                // Add to map with highlight styling
                window.geoJsonLayer = L.geoJSON(data.geojson, {
                    style: function(feature) {
                        return {
                            color: '#ff7800',
                            weight: 3,
                            opacity: 1,
                            fillOpacity: 0.5
                        };
                    },
                    onEachFeature: function(feature, layer) {
                        if (feature.properties) {
                            let popupContent = '<div class="popup-content">';
                            for (const [key, value] of Object.entries(feature.properties)) {
                                if (value !== null && value !== undefined) {
                                    popupContent += `<b>${key}:</b> ${value}<br>`;
                                }
                            }
                            popupContent += '</div>';
                            layer.bindPopup(popupContent).openPopup();
                        }
                    }
                }).addTo(window.map);

                // Fit bounds to result
                const bounds = window.geoJsonLayer.getBounds();
                if (bounds.isValid()) {
                    window.map.fitBounds(bounds, { padding: [100, 100], maxZoom: 18 });
                }

                document.getElementById('tableInfo').textContent = `Found ${data.geojson.features.length} parcel(s) for reference: ${reference}`;
            } else {
                alert(`No parcels found for reference: ${reference}`);
            }
        } else {
            const error = await response.json();
            alert(`Search error: ${error.detail || 'Not found'}`);
        }
    } catch (error) {
        console.error('Search error:', error);
        alert(`Error: ${error.message}`);
    }
}

// Clear all database filters
function clearDbFilters() {
    // Reset geographic dropdowns
    document.getElementById('dbRegione').value = '';
    document.getElementById('dbProvincia').innerHTML = '<option value="">All Provinces</option>';
    document.getElementById('dbComune').innerHTML = '<option value="">All Municipalities</option>';
    document.getElementById('dbFoglio').innerHTML = '<option value="">All Fogli</option>';

    document.getElementById('dbProvincia').disabled = true;
    document.getElementById('dbComune').disabled = true;
    document.getElementById('dbFoglio').disabled = true;

    // Reset cadastral filters
    document.getElementById('dbParticella').value = '';
    document.getElementById('dbParticella').disabled = true;
    document.querySelector('input[name="dbLayerType"][value=""]').checked = true;

    // Reset spatial filters
    clearBboxFilter();

    // Reset search
    document.getElementById('dbQuickSearch').value = '';

    // Hide result info
    document.getElementById('dbQueryInfo').style.display = 'none';
}

// ========================================
// SIMPLIFIED DATABASE TAB FUNCTIONS
// ========================================
// These work with the simpler database tab HTML and /load-spatialite/ endpoint

// Check database status and update UI
async function checkDbStatus() {
    const statusText = document.getElementById('dbStatusText');
    const statusIndicator = document.getElementById('dbStatusIndicator');
    
    if (!statusText || !statusIndicator) return;
    
    try {
        // Try a test query with limit 0 to check connection
        const response = await fetch('/api/v1/load-spatialite/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ limit: 1 })
        });
        
        if (response.ok) {
            const data = await response.json();
            if (data.success) {
                statusIndicator.className = 'db-status connected';
                statusText.textContent = `Connected (${data.columns?.length || 0} columns)`;
            } else {
                statusIndicator.className = 'db-status error';
                statusText.textContent = 'Database empty or not found';
            }
        } else {
            const err = await response.json().catch(() => ({}));
            statusIndicator.className = 'db-status error';
            statusText.textContent = err.detail?.substring(0, 50) || 'Connection error';
        }
    } catch (error) {
        statusIndicator.className = 'db-status error';
        statusText.textContent = 'Database unavailable';
        console.error('Database status check error:', error);
    }
}

// Load provinces for database tab (placeholder - extend for hierarchy API)
async function loadDbProvinces() {
    const regionSelect = document.getElementById('dbRegion');
    const provinceSelect = document.getElementById('dbProvince');
    const comuneSelect = document.getElementById('dbComune');
    
    if (!regionSelect || !provinceSelect) return;
    
    const region = regionSelect.value;
    
    // Reset downstream selects
    provinceSelect.innerHTML = '<option value="">All Provinces</option>';
    comuneSelect.innerHTML = '<option value="">All Comuni</option>';
    
    if (region) {
        provinceSelect.disabled = false;
        // Could load from hierarchy API here if available
    } else {
        provinceSelect.disabled = true;
        comuneSelect.disabled = true;
    }
}

// Load comuni for database tab
async function loadDbComuni() {
    const provinceSelect = document.getElementById('dbProvince');
    const comuneSelect = document.getElementById('dbComune');
    
    if (!provinceSelect || !comuneSelect) return;
    
    const province = provinceSelect.value;
    
    comuneSelect.innerHTML = '<option value="">All Comuni</option>';
    
    if (province) {
        comuneSelect.disabled = false;
    } else {
        comuneSelect.disabled = true;
    }
}

// Main function to load data from local SpatiaLite database
async function loadFromDatabase() {
    const btn = document.getElementById('loadDbBtn');
    const resultInfo = document.getElementById('dbResultInfo');
    const resultText = document.getElementById('dbResultText');
    
    if (btn) {
        btn.disabled = true;
        btn.textContent = '⏳ Loading...';
    }
    
    try {
        // Build WHERE clause from filters
        const region = document.getElementById('dbRegion')?.value;
        const province = document.getElementById('dbProvince')?.value;
        const comune = document.getElementById('dbComune')?.value;
        const layerType = document.getElementById('dbLayerType')?.value;
        const limit = parseInt(document.getElementById('dbLimit')?.value) || 1000;
        
        let whereClause = [];
        if (region) whereClause.push(`regione = '${region}'`);
        if (province) whereClause.push(`provincia = '${province}'`);
        if (comune) whereClause.push(`comune_code = '${comune}'`);
        if (layerType) whereClause.push(`layer_type = '${layerType}'`);
        
        const request = {
            limit: limit
        };
        
        if (whereClause.length > 0) {
            request.where = whereClause.join(' AND ');
        }
        
        console.log('[Database] Loading with request:', request);
        
        const response = await fetch('/api/v1/load-spatialite/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(request)
        });
        
        if (response.ok) {
            const data = await response.json();
            
            if (data.success && data.geojson && data.geojson.features?.length > 0) {
                // Data loaded successfully - it's now in the global GDF
                window.geoJsonData = data.geojson;
                window.hasData = true;
                
                const count = data.feature_count || data.geojson.features.length;
                
                // Add to map
                addGeoJSONToMap(data.geojson, 'Database Query');
                
                // Update UI
                if (resultInfo) resultInfo.style.display = 'block';
                if (resultText) resultText.textContent = `✅ Loaded ${count.toLocaleString()} features`;
                
                document.getElementById('tableInfo').textContent = `${count.toLocaleString()} parcels from database`;
                updatePolygonManagementState();
                
                // Auto-zoom to loaded data
                setTimeout(() => autoZoomToAllPolygons(), 500);
                
            } else if (data.success && data.feature_count === 0) {
                if (resultInfo) resultInfo.style.display = 'block';
                if (resultText) resultText.textContent = '⚠️ No features found for this query';
            } else {
                throw new Error(data.message || 'Unknown error');
            }
        } else {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${response.status}`);
        }
    } catch (error) {
        console.error('[Database] Load error:', error);
        if (resultInfo) resultInfo.style.display = 'block';
        if (resultText) resultText.textContent = `❌ Error: ${error.message}`;
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = '🔍 Query Database';
        }
    }
}

// Helper function to add GeoJSON to the Folium map
function addGeoJSONToMap(geojson, layerName) {
    const mapElements = document.querySelectorAll('.leaflet-container');
    if (mapElements.length === 0) {
        console.warn('[Database] No map found');
        return;
    }
    
    const mapId = mapElements[0].id;
    const foliumMap = window[mapId];
    
    if (!foliumMap) {
        console.warn('[Database] Folium map instance not found');
        return;
    }
    
    // Create GeoJSON layer with styling
    const geoJsonLayer = L.geoJSON(geojson, {
        style: function(feature) {
            const layerType = feature.properties?.layer_type;
            return {
                color: layerType === 'ple' ? '#e74c3c' : '#3498db',
                weight: 2,
                opacity: 0.8,
                fillOpacity: 0.3,
                fillColor: layerType === 'ple' ? '#e74c3c' : '#3498db'
            };
        },
        onEachFeature: function(feature, layer) {
            // Add popup with properties
            if (feature.properties) {
                let popupContent = '<div class="popup-content">';
                const priorityProps = ['regione', 'provincia', 'comune_name', 'foglio', 'particella', 'layer_type'];
                
                // Show priority props first
                priorityProps.forEach(key => {
                    if (feature.properties[key] !== null && feature.properties[key] !== undefined) {
                        popupContent += `<b>${key}:</b> ${feature.properties[key]}<br>`;
                    }
                });
                
                popupContent += '</div>';
                layer.bindPopup(popupContent);
            }
            
            // Add click handler for selection
            layer.on('click', function(e) {
                const isSelected = layer._selected || false;
                
                if (isSelected) {
                    layer._selected = false;
                    layer.setStyle({
                        fillColor: layer.options.originalFillColor || layer.options.fillColor,
                        fillOpacity: 0.3
                    });
                    const index = window.selectedPolygons.findIndex(p => p === layer);
                    if (index > -1) window.selectedPolygons.splice(index, 1);
                } else {
                    layer._selected = true;
                    if (!layer.options.originalFillColor) {
                        layer.options.originalFillColor = layer.options.fillColor;
                    }
                    layer.setStyle({
                        fillColor: '#ff0000',
                        fillOpacity: 0.7
                    });
                    window.selectedPolygons.push(layer);
                }
                
                updateAdjacencyButtonState();
            });
        }
    });
    
    // Add to map
    geoJsonLayer.addTo(foliumMap);
    console.log(`[Database] Added ${geojson.features.length} features to map as "${layerName}"`);
    
    // Store reference for later removal
    if (!window.dbLayers) window.dbLayers = [];
    window.dbLayers.push(geoJsonLayer);
}

// Initialize database tab when shown
function initDatabaseTab() {
    // Only init once
    if (window.dbTabInitialized) return;
    
    checkDbStatus();
    window.dbTabInitialized = true;
}

// Override initDatabaseFilters to use simpler init
window.initDatabaseFiltersOriginal = window.initDatabaseFilters || function() {};
window.initDatabaseFilters = function() {
    initDatabaseTab();
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
        const mapElements = document.querySelectorAll('.leaflet-container');
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
                    
                    // Skip drawing layers
                    if (window.drawnItems && window.drawnItems.hasLayer(layer)) {
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

            const newLayers = result.successful_layers || 0;
            const newFeatures = result.features_count || 0;
            const totalLayers = result.total_layers || 0;
            const totalFeatures = result.total_features_count || 0;

            const successMessage = `Successfully loaded ${newLayers} layers with ${newFeatures} features.\n\n` +
                `Loaded layers:\n${Object.keys(result.layers || {})
                    .filter(layer => !result.layers[layer].error)
                    .map(layer => `- ${layer} (${result.layers[layer].feature_count} features)`)
                    .join('\n')}\n\n` +
                `Total: ${totalFeatures} features across ${totalLayers} layers.\n\n` +
                `The page will now reload to display the updated map.`;

            // Store the new layer bounds in sessionStorage so we can zoom only to them after reload
            if (result.new_bounds) {
                sessionStorage.setItem('newLayerBounds', JSON.stringify(result.new_bounds));
                console.log('[FoliumInterface] Stored new layer bounds:', result.new_bounds);
            }
            // Also store layer names as fallback
            const newLayerNames = Object.keys(result.layers || {}).filter(layer => !result.layers[layer].error);
            if (newLayerNames.length > 0) {
                sessionStorage.setItem('newlyLoadedLayers', JSON.stringify(newLayerNames));
                console.log('[FoliumInterface] Stored newly loaded layers:', newLayerNames);
            }

            alert(successMessage);

            // Reload the page to regenerate the Folium map with the new data
            window.location.reload();

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

// Initialize custom zoom controls independently (with retry)
function initializeCustomZoomControlsWithRetry(retryCount = 0) {
    const maxRetries = 20;
    const retryDelay = 500;

    // First try to find the leaflet-container and map instance
    const mapElements = document.querySelectorAll('.leaflet-container');
    if (mapElements.length > 0) {
        const mapId = mapElements[0].id;
        if (window[mapId]) {
            console.log('[FoliumInterface] Found map instance for zoom controls, initializing...');
            addCustomZoomControls(window[mapId]);
            return;
        }
    }

    // Fallback: Try to find any leaflet control bar and add buttons without map reference
    const controlBar = document.querySelector('.leaflet-bar.leaflet-control');
    if (controlBar && !document.querySelector('.leaflet-control-zoom-fit-all')) {
        console.log('[FoliumInterface] Found control bar without map instance, adding buttons with fallback...');
        addCustomZoomControlsWithoutMap();
        return;
    }

    if (retryCount < maxRetries) {
        console.log(`[FoliumInterface] Controls not ready, retry ${retryCount + 1}/${maxRetries}`);
        setTimeout(() => initializeCustomZoomControlsWithRetry(retryCount + 1), retryDelay);
    } else {
        console.warn('[FoliumInterface] Could not initialize zoom controls after max retries');
    }
}

// Fallback function to add zoom controls without direct map reference
function addCustomZoomControlsWithoutMap() {
    console.log('[FoliumInterface] Adding custom zoom controls (fallback mode)');

    const controlBar = document.querySelector('.leaflet-bar.leaflet-control');
    if (!controlBar) {
        console.warn('[FoliumInterface] No control bar found');
        return;
    }

    // Check if buttons already exist
    if (document.querySelector('.leaflet-control-zoom-fit-all')) {
        console.log('[FoliumInterface] Custom zoom buttons already exist');
        return;
    }

    // Helper to get map instance
    function getMapInstance() {
        const mapElements = document.querySelectorAll('.leaflet-container');
        if (mapElements.length > 0) {
            const mapId = mapElements[0].id;
            return window[mapId];
        }
        return null;
    }

    // Create Fit All button
    const fitAllBtn = document.createElement('a');
    fitAllBtn.className = 'leaflet-control-zoom-fit-all';
    fitAllBtn.href = '#';
    fitAllBtn.title = 'Fit to all data';
    fitAllBtn.innerHTML = '⊞';
    fitAllBtn.setAttribute('role', 'button');
    fitAllBtn.setAttribute('aria-label', 'Fit to all data');

    fitAllBtn.onclick = function(e) {
        e.preventDefault();
        e.stopPropagation();
        autoZoomToAllPolygons();
        return false;
    };

    // Create Fit Selected button
    const fitSelectedBtn = document.createElement('a');
    fitSelectedBtn.className = 'leaflet-control-zoom-fit-selected';
    fitSelectedBtn.href = '#';
    fitSelectedBtn.title = 'Fit to selected polygons';
    fitSelectedBtn.innerHTML = '◎';
    fitSelectedBtn.setAttribute('role', 'button');
    fitSelectedBtn.setAttribute('aria-label', 'Fit to selected polygons');

    fitSelectedBtn.onclick = function(e) {
        e.preventDefault();
        e.stopPropagation();
        zoomToSelectedPolygons();
        return false;
    };

    // Create Window Zoom (box zoom) button
    const boxZoomBtn = document.createElement('a');
    boxZoomBtn.className = 'leaflet-control-zoom-box';
    boxZoomBtn.href = '#';
    boxZoomBtn.title = 'Zoom to window (draw rectangle)';
    boxZoomBtn.innerHTML = '⬚';
    boxZoomBtn.setAttribute('role', 'button');
    boxZoomBtn.setAttribute('aria-label', 'Zoom to window');

    let boxZoomActive = false;
    let boxZoomStartPoint = null;
    let boxZoomRect = null;

    boxZoomBtn.onclick = function(e) {
        e.preventDefault();
        e.stopPropagation();

        const map = getMapInstance();
        if (!map) {
            alert('Map not ready. Please try again.');
            return false;
        }

        boxZoomActive = !boxZoomActive;

        if (boxZoomActive) {
            boxZoomBtn.classList.add('active');
            boxZoomBtn.style.backgroundColor = '#e0e0ff';
            map.dragging.disable();
            map.getContainer().style.cursor = 'crosshair';

            // Set up temporary event handlers
            map._boxZoomHandler = {
                mousedown: function(ev) {
                    if (boxZoomActive) {
                        boxZoomStartPoint = ev.latlng;
                        if (boxZoomRect) {
                            map.removeLayer(boxZoomRect);
                        }
                    }
                },
                mousemove: function(ev) {
                    if (boxZoomActive && boxZoomStartPoint) {
                        const bounds = L.latLngBounds(boxZoomStartPoint, ev.latlng);
                        if (boxZoomRect) {
                            boxZoomRect.setBounds(bounds);
                        } else {
                            boxZoomRect = L.rectangle(bounds, {
                                color: '#3388ff',
                                weight: 2,
                                fillOpacity: 0.2,
                                dashArray: '5, 5'
                            }).addTo(map);
                        }
                    }
                },
                mouseup: function(ev) {
                    if (boxZoomActive && boxZoomStartPoint) {
                        const bounds = L.latLngBounds(boxZoomStartPoint, ev.latlng);
                        const startPoint = map.latLngToContainerPoint(boxZoomStartPoint);
                        const endPoint = map.latLngToContainerPoint(ev.latlng);
                        const distance = Math.sqrt(
                            Math.pow(endPoint.x - startPoint.x, 2) +
                            Math.pow(endPoint.y - startPoint.y, 2)
                        );

                        if (distance > 20) {
                            map.fitBounds(bounds, { padding: [10, 10] });
                        }

                        // Clean up
                        if (boxZoomRect) {
                            map.removeLayer(boxZoomRect);
                            boxZoomRect = null;
                        }
                        boxZoomStartPoint = null;
                        boxZoomActive = false;
                        boxZoomBtn.classList.remove('active');
                        boxZoomBtn.style.backgroundColor = '';
                        map.dragging.enable();
                        map.getContainer().style.cursor = '';

                        // Remove handlers
                        map.off('mousedown', map._boxZoomHandler.mousedown);
                        map.off('mousemove', map._boxZoomHandler.mousemove);
                        map.off('mouseup', map._boxZoomHandler.mouseup);
                    }
                }
            };

            map.on('mousedown', map._boxZoomHandler.mousedown);
            map.on('mousemove', map._boxZoomHandler.mousemove);
            map.on('mouseup', map._boxZoomHandler.mouseup);

            console.log('[FoliumInterface] Box zoom mode activated');
        } else {
            boxZoomBtn.classList.remove('active');
            boxZoomBtn.style.backgroundColor = '';
            map.dragging.enable();
            map.getContainer().style.cursor = '';
            if (boxZoomRect) {
                map.removeLayer(boxZoomRect);
                boxZoomRect = null;
            }
            if (map._boxZoomHandler) {
                map.off('mousedown', map._boxZoomHandler.mousedown);
                map.off('mousemove', map._boxZoomHandler.mousemove);
                map.off('mouseup', map._boxZoomHandler.mouseup);
            }
            console.log('[FoliumInterface] Box zoom mode deactivated');
        }
        return false;
    };

    // Create Reset View button
    const resetViewBtn = document.createElement('a');
    resetViewBtn.className = 'leaflet-control-zoom-reset';
    resetViewBtn.href = '#';
    resetViewBtn.title = 'Reset to Italy view';
    resetViewBtn.innerHTML = '🏠';
    resetViewBtn.setAttribute('role', 'button');
    resetViewBtn.setAttribute('aria-label', 'Reset view');

    resetViewBtn.onclick = function(e) {
        e.preventDefault();
        e.stopPropagation();
        const map = getMapInstance();
        if (map) {
            map.setView([41.8719, 12.5674], 6);
            console.log('[FoliumInterface] View reset to Italy');
        } else {
            alert('Map not ready. Please try again.');
        }
        return false;
    };

    // Append buttons to control bar
    controlBar.appendChild(fitAllBtn);
    controlBar.appendChild(fitSelectedBtn);
    controlBar.appendChild(boxZoomBtn);
    controlBar.appendChild(resetViewBtn);

    console.log('[FoliumInterface] Custom zoom buttons added successfully (fallback mode)');
}

// Function to zoom to newly loaded layers only (called after page reload)
function zoomToNewlyLoadedLayers() {
    // First try to get bounds from API (more reliable)
    const boundsJson = sessionStorage.getItem('newLayerBounds');
    const newLayersJson = sessionStorage.getItem('newlyLoadedLayers');

    if (!boundsJson && !newLayersJson) {
        console.log('[FoliumInterface] No newly loaded layers stored, using default zoom behavior');
        return false;
    }

    // Clear sessionStorage entries
    sessionStorage.removeItem('newLayerBounds');
    sessionStorage.removeItem('newlyLoadedLayers');

    // Wait for map to be ready
    setTimeout(() => {
        const mapElements = document.querySelectorAll('.leaflet-container');
        if (mapElements.length === 0) {
            console.warn('[FoliumInterface] No map container found');
            return;
        }

        const mapId = mapElements[0].id;
        const foliumMap = window[mapId];
        if (!foliumMap) {
            console.warn('[FoliumInterface] Folium map instance not found');
            return;
        }

        // Try using bounds from API first (most reliable)
        if (boundsJson) {
            try {
                const apiBounds = JSON.parse(boundsJson);
                if (apiBounds.south && apiBounds.west && apiBounds.north && apiBounds.east) {
                    const bounds = L.latLngBounds(
                        [apiBounds.south, apiBounds.west],
                        [apiBounds.north, apiBounds.east]
                    );
                    if (bounds.isValid()) {
                        foliumMap.fitBounds(bounds, { padding: [20, 20], maxZoom: 18 });
                        console.log('[FoliumInterface] Zoomed to new layers using API bounds:', apiBounds);
                        return;
                    }
                }
            } catch (e) {
                console.warn('[FoliumInterface] Could not parse API bounds:', e);
            }
        }

        // Fall back to layer name matching
        if (newLayersJson) {
            try {
                const newLayerNames = JSON.parse(newLayersJson);
                console.log('[FoliumInterface] Trying layer name matching for:', newLayerNames);

                let bounds = null;
                let foundNewLayers = 0;

                foliumMap.eachLayer(function(layer) {
                    // Skip tile layers
                    if (layer instanceof L.TileLayer) return;

                    // Check if this layer matches any of the new layer names
                    let layerName = '';
                    if (layer.options && layer.options.name) {
                        layerName = layer.options.name;
                    } else if (layer._name) {
                        layerName = layer._name;
                    } else if (layer.feature && layer.feature.properties) {
                        layerName = layer.feature.properties.name || layer.feature.properties.layer || '';
                    }

                    // Check if any new layer name is contained in the layer name
                    const isNewLayer = newLayerNames.some(newName => {
                        return layerName.toLowerCase().includes(newName.toLowerCase()) ||
                               newName.toLowerCase().includes(layerName.toLowerCase());
                    });

                    if (isNewLayer && layer.getBounds && typeof layer.getBounds === 'function') {
                        try {
                            const layerBounds = layer.getBounds();
                            if (layerBounds && layerBounds.isValid()) {
                                if (bounds === null) {
                                    bounds = L.latLngBounds(layerBounds);
                                } else {
                                    bounds.extend(layerBounds);
                                }
                                foundNewLayers++;
                            }
                        } catch (e) {
                            // Layer might not have valid bounds
                        }
                    }
                });

                if (bounds && bounds.isValid() && foundNewLayers > 0) {
                    foliumMap.fitBounds(bounds, { padding: [20, 20], maxZoom: 18 });
                    console.log(`[FoliumInterface] Zoomed to ${foundNewLayers} newly loaded layers via name matching`);
                    return;
                }
            } catch (e) {
                console.warn('[FoliumInterface] Layer name matching failed:', e);
            }
        }

        // Final fallback: zoom to all layers
        console.log('[FoliumInterface] Could not identify new layers, zooming to all data');
        autoZoomToAllPolygons();
    }, 1500); // Wait for map initialization

    return true;
}

// Initialize the Folium interface when DOM is loaded
document.addEventListener('DOMContentLoaded', function() {
    console.log('Folium map interface initializing...');

    // Check if we should zoom to newly loaded layers (after a cadastral file load)
    const hasNewLayers = zoomToNewlyLoadedLayers();

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

    // Initialize drawing controls (NEW)
    initializeDrawingControls();

    // Initialize custom zoom controls independently (with retry mechanism)
    initializeCustomZoomControlsWithRetry();

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

// ========================================
// FLATGEOBUF (FGB) DATA SOURCE FUNCTIONS
// ========================================

// Global state for FGB
window.fgbDataSource = 'spatialite'; // 'spatialite' or 'fgb'
window.fgbRegions = [];
window.fgbCurrentLayer = null;

// Switch data source
function selectDataSource(source) {
    window.fgbDataSource = source;

    // Update UI
    document.getElementById('sourceSpatialite').classList.toggle('active', source === 'spatialite');
    document.getElementById('sourceFgb').classList.toggle('active', source === 'fgb');

    // Show/hide appropriate filters
    document.getElementById('spatialiteFilters').style.display = source === 'spatialite' ? 'block' : 'none';
    document.getElementById('fgbFilters').style.display = source === 'fgb' ? 'block' : 'none';

    // Show/hide appropriate action buttons
    document.getElementById('spatialiteActions').style.display = source === 'spatialite' ? 'flex' : 'none';
    document.getElementById('spatialiteClear').style.display = source === 'spatialite' ? 'flex' : 'none';
    document.getElementById('fgbActions').style.display = source === 'fgb' ? 'flex' : 'none';
    document.getElementById('fgbClear').style.display = source === 'fgb' ? 'flex' : 'none';

    // Load FGB regions if switching to FGB
    if (source === 'fgb' && window.fgbRegions.length === 0) {
        loadFgbRegions();
    }

    console.log('[DataSource] Switched to ' + source);
}

// Load available FGB regions from API
async function loadFgbRegions() {
    try {
        const response = await fetch('/api/v1/fgb/regions');
        if (response.ok) {
            const data = await response.json();
            window.fgbRegions = data.regions || [];

            // Populate region select
            const regionSelect = document.getElementById('fgbRegione');
            regionSelect.innerHTML = '<option value="">Select a Region</option>';
            window.fgbRegions.forEach(region => {
                const opt = document.createElement('option');
                opt.value = region.slug;
                opt.textContent = region.name;
                opt.dataset.mapFile = region.map_file;
                opt.dataset.pleFile = region.ple_file;
                regionSelect.appendChild(opt);
            });

            console.log('[FGB] Loaded ' + window.fgbRegions.length + ' regions');
        } else {
            console.error('[FGB] Failed to load regions:', response.status);
            alert('Failed to load FGB regions. The server may not have FGB files available.');
        }
    } catch (error) {
        console.error('[FGB] Error loading regions:', error);
        alert('Error loading FGB regions: ' + error.message);
    }
}

// Update FGB layer info when region or layer type changes
function updateFgbLayerInfo() {
    const regionSelect = document.getElementById('fgbRegione');
    const layerType = document.querySelector('input[name="fgbLayerType"]:checked').value;
    const selectedOption = regionSelect.options[regionSelect.selectedIndex];

    if (!selectedOption.value) {
        document.getElementById('fgbFileInfo').style.display = 'none';
        document.getElementById('loadFgbBtn').disabled = true;
        return;
    }

    // Get file info from selected region
    const fileName = layerType === 'map' ? selectedOption.dataset.mapFile : selectedOption.dataset.pleFile;

    // Fetch file metadata from API
    fetchFgbFileMetadata(selectedOption.value, layerType, fileName);
}

// Fetch FGB file metadata
async function fetchFgbFileMetadata(regionSlug, layerType, fileName) {
    try {
        const response = await fetch('/api/v1/fgb/metadata/' + regionSlug + '/' + layerType);
        if (response.ok) {
            const data = await response.json();

            // Update UI
            document.getElementById('fgbFileName').textContent = data.filename || fileName;
            document.getElementById('fgbFileSize').textContent = formatFileSize(data.size || 0);
            document.getElementById('fgbFeatureCount').textContent = (data.feature_count || 0).toLocaleString();
            document.getElementById('fgbFileInfo').style.display = 'block';
            document.getElementById('loadFgbBtn').disabled = false;

            console.log('[FGB] Metadata loaded for ' + regionSlug + '/' + layerType + ':', data);
        } else {
            console.warn('[FGB] Failed to load metadata:', response.status);
            document.getElementById('fgbFileInfo').style.display = 'none';
            document.getElementById('loadFgbBtn').disabled = false; // Still allow loading
        }
    } catch (error) {
        console.error('[FGB] Error fetching metadata:', error);
        document.getElementById('fgbFileInfo').style.display = 'none';
        document.getElementById('loadFgbBtn').disabled = false; // Still allow loading
    }
}

// Load FGB region data
async function loadFgbRegion() {
    const regionSelect = document.getElementById('fgbRegione');
    const layerType = document.querySelector('input[name="fgbLayerType"]:checked').value;
    const selectedOption = regionSelect.options[regionSelect.selectedIndex];

    if (!selectedOption.value) {
        alert('Please select a region');
        return;
    }

    const regionSlug = selectedOption.value;
    const regionName = selectedOption.textContent;
    const btn = document.getElementById('loadFgbBtn');

    btn.disabled = true;
    btn.textContent = 'Loading...';

    try {
        console.log('[FGB] Loading ' + layerType + ' data for ' + regionName);

        // Load FGB file via API
        const response = await fetch('/api/v1/fgb/load/' + regionSlug + '/' + layerType);

        if (response.ok) {
            const data = await response.json();

            if (data.geojson && data.geojson.features && data.geojson.features.length > 0) {
                // Clear existing layers
                if (window.geoJsonLayer) {
                    window.map.removeLayer(window.geoJsonLayer);
                }

                // Store data
                window.geoJsonData = data.geojson;
                window.hasData = true;

                // Add to map
                window.geoJsonLayer = L.geoJSON(data.geojson, {
                    style: function(feature) {
                        return {
                            color: layerType === 'ple' ? '#e74c3c' : '#3388ff',
                            weight: 2,
                            opacity: 0.8,
                            fillOpacity: 0.3
                        };
                    },
                    onEachFeature: function(feature, layer) {
                        if (feature.properties) {
                            let popupContent = '<div class="popup-content">';
                            for (const [key, value] of Object.entries(feature.properties)) {
                                if (value !== null && value !== undefined) {
                                    popupContent += '<b>' + key + ':</b> ' + value + '<br>';
                                }
                            }
                            popupContent += '</div>';
                            layer.bindPopup(popupContent);
                        }

                        // Click handler for selection
                        layer.on('click', function(e) {
                            handlePolygonClick(e, layer, feature);
                        });
                    }
                }).addTo(window.map);

                // Fit bounds to loaded data
                const bounds = window.geoJsonLayer.getBounds();
                if (bounds.isValid()) {
                    window.map.fitBounds(bounds, { padding: [50, 50] });
                }

                // Update UI
                const count = data.geojson.features.length;
                document.getElementById('tableInfo').textContent = count.toLocaleString() + ' features loaded from ' + regionName + ' (' + layerType.toUpperCase() + ')';
                document.getElementById('polygonCount').textContent = count.toLocaleString() + ' polygons loaded';

                // Enable polygon management buttons
                document.getElementById('removePolygonsBtn').disabled = false;
                document.getElementById('zoomToPolygonsBtn').disabled = false;

                console.log('[FGB] Loaded ' + count + ' features from ' + regionName);
            } else {
                alert('No features found in this region/layer');
            }
        } else {
            const error = await response.json();
            alert('Error loading FGB data: ' + (error.detail || 'Unknown error'));
        }
    } catch (error) {
        console.error('[FGB] Load error:', error);
        alert('Error: ' + error.message);
    } finally {
        btn.disabled = false;
        btn.textContent = 'Load Region Data';
    }
}

// Clear FGB selection
function clearFgbSelection() {
    document.getElementById('fgbRegione').value = '';
    document.getElementById('fgbFileInfo').style.display = 'none';
    document.getElementById('loadFgbBtn').disabled = true;
}

// Format file size for display
function formatFileSize(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

// Make functions globally available
window.selectDataSource = selectDataSource;
window.loadFgbRegions = loadFgbRegions;
window.updateFgbLayerInfo = updateFgbLayerInfo;
window.loadFgbRegion = loadFgbRegion;
window.clearFgbSelection = clearFgbSelection;

console.log('[FGB] Functions loaded successfully');
