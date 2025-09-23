// Land Registry Map JavaScript
// Initialize Leaflet map with all map providers
let map, currentGeoJsonLayer, drawnItems, drawControl;

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
    console.log('Polygon selection mode toggled');
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
};

window.toggleLegend = function() {
    const legend = document.querySelector('.leaflet-control-layers');
    if (legend) {
        legend.style.display = legend.style.display === 'none' ? 'block' : 'none';
    }
};

window.toggleSelectionInfo = function() {
    console.log('Selection info toggled');
};

window.togglePolygonsVisibility = function() {
    if (currentGeoJsonLayer) {
        if (map.hasLayer(currentGeoJsonLayer)) {
            map.removeLayer(currentGeoJsonLayer);
        } else {
            map.addLayer(currentGeoJsonLayer);
        }
    }
};

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
        console.log('Switching to basemap:', selectedValue);
    }
};

// Selection control functions
window.changeSelectionMode = function() {
    const mode = document.getElementById('selectionMode').value;
    console.log('Selection mode changed to:', mode);
};

window.findAdjacencyForSelected = function() {
    // Implementation would call the backend adjacency endpoint
    console.log('Finding adjacent polygons for selected features');
    const method = document.getElementById('adjacencyMethod').value;
    console.log('Using adjacency method:', method);
};

window.clearSelection = function() {
    // Clear all selected polygons
    const countElement = document.getElementById('selectedCount');
    if (countElement) {
        countElement.textContent = '0 polygons selected';
    }

    // Disable adjacency button when no selection
    const adjBtn = document.getElementById('findAdjacencyBtn');
    if (adjBtn) {
        adjBtn.disabled = true;
    }

    console.log('Selection cleared');
};

window.showAllPolygons = function() {
    // Show all polygons in the current dataset
    if (currentGeoJsonLayer) {
        currentGeoJsonLayer.addTo(map);
        map.fitBounds(currentGeoJsonLayer.getBounds());
    }
    console.log('Showing all polygons');
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
                <div class="control-group-header">Navigate ⋮⋮</div>
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
                <div class="control-group-header">Tools ⋮⋮</div>
                <button onclick="togglePolygonSelectionMode()" title="Enable/disable polygon selection">✏️</button>
                <div class="control-separator"></div>
                <button onclick="startDrawingMode()" title="Start drawing new polygons">🖊️</button>
                <button id="stopDrawing" onclick="stopDrawingMode()" disabled title="Stop drawing mode">⏹️</button>
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
                <div class="control-group-header">Display ⋮⋮</div>
                <button onclick="toggleLegend()" title="Show/hide map legend">📋</button>
                <div class="control-separator"></div>
                <button onclick="toggleSelectionInfo()" title="Show/hide selection information">ℹ️</button>
                <button onclick="togglePolygonsVisibility()" title="Show/hide all polygons">👁️</button>
            `;
            L.DomEvent.disableClickPropagation(container);
            return container;
        }
    });
    map.addControl(new DisplayControl());
}

function addDataControls(map) {
    const DataControl = L.Control.extend({
        options: {
            position: 'bottomright'
        },
        onAdd: function(map) {
            const container = L.DomUtil.create('div', 'leaflet-control-data');
            container.innerHTML = `
                <div class="control-group-header">Data ⋮⋮</div>
                <button onclick="saveDrawingsToJSON()" title="Export drawn polygons to JSON">💾</button>
                <div class="control-separator"></div>
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
                    style: {
                        color: '#3388ff',
                        weight: 2,
                        fillOpacity: 0.1
                    },
                    onEachFeature: function(feature, layer) {
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
            }
        } catch (error) {
            console.error('Error loading GeoJSON data:', error);
        }
    }
}

// Initialize the map with all providers
function initializeMap() {
    console.log('Initializing map...');

    // Prevent multiple initializations
    if (map) {
        console.log('Map already initialized, skipping...');
        return;
    }

    const mapElement = document.getElementById('map');
    console.log('Map element found:', mapElement);
    console.log('Map element dimensions:', mapElement ? `${mapElement.offsetWidth}x${mapElement.offsetHeight}` : 'N/A');

    // Create map centered on Italy
    map = L.map('map').setView([41.8719, 12.5674], 6);
    console.log('Leaflet map created:', map);

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

    // Add default layer
    mapProviders['OpenStreetMap'].addTo(map);

    // Add layer control
    L.control.layers(mapProviders, weatherOverlays, {
        position: 'topright',
        collapsed: true
    }).addTo(map);

    // Add drawing controls
    drawnItems = new L.FeatureGroup();
    map.addLayer(drawnItems);

    drawControl = new L.Control.Draw({
        position: 'topleft',
        draw: {
            polygon: true,
            circle: true,
            rectangle: true,
            polyline: true,
            marker: true
        },
        edit: {
            featureGroup: drawnItems
        }
    });
    map.addControl(drawControl);

    // Add fullscreen control
    L.control.fullscreen({
        position: 'topleft'
    }).addTo(map);

    // Add measure control
    L.control.measure({
        position: 'topleft'
    }).addTo(map);

    // Add custom navigation controls
    addNavigationControls(map);

    // Add custom tools controls
    addToolsControls(map);

    // Add custom display controls
    addDisplayControls(map);

    // Add custom data controls
    addDataControls(map);

    // Handle drawing events
    map.on(L.Draw.Event.CREATED, function(e) {
        const layer = e.layer;
        drawnItems.addLayer(layer);
    });

    // Load existing data if available
    loadGeoJsonData();
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
        console.log(`Loading ${filePaths.length} cadastral files directly from S3:`, filePaths);

        // Clear existing map layers
        clearMap();

        // Load files sequentially from S3
        const loadedLayers = [];
        let successfulLoads = 0;

        for (let i = 0; i < filePaths.length; i++) {
            const filePath = filePaths[i];
            loadBtn.textContent = `Loading... (${i + 1}/${filePaths.length})`;

            try {
                console.log(`Loading file ${i + 1}/${filePaths.length} via backend API: ${filePath}`);

                // Use backend API endpoint to load and process GPKG file
                const apiUrl = `/api/v1/load-cadastral-files/${encodeURIComponent(filePath)}`;
                console.log(`Fetching from backend API: ${apiUrl}`);

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

                    console.log(`Successfully loaded ${layerData.filename} with ${layerData.feature_count} features`);
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
            // Fit map to bounds of first loaded layer
            if (window.map && loadedLayers[0] && loadedLayers[0].geojson) {
                fitMapToGeoJson(loadedLayers[0].geojson);
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

    const style = options.style || {
        color: '#3388ff',
        weight: 2,
        opacity: 1,
        fillOpacity: 0.2
    };

    const layer = L.geoJSON(geojson, {
        style: style,
        onEachFeature: function(feature, layer) {
            // Add popup with feature properties
            if (feature.properties) {
                const popupContent = Object.entries(feature.properties)
                    .map(([key, value]) => `<strong>${key}:</strong> ${value}`)
                    .join('<br>');
                layer.bindPopup(popupContent);
            }
        },
        cadastralLayer: true // Mark as cadastral layer for identification
    });

    layer.addTo(map);

    // Store as current layer for fitting bounds
    currentGeoJsonLayer = layer;

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
        // Calculate total files
        let totalFiles = 0;
        const fileBreakdown = {};

        selectedMunicipalities.forEach(municipalityKey => {
            const [regionName, provinceCode, municipalityId] = municipalityKey.split('|');
            if (cadastralData[regionName] &&
                cadastralData[regionName][provinceCode] &&
                cadastralData[regionName][provinceCode][municipalityId]) {

                const municipalityData = cadastralData[regionName][provinceCode][municipalityId];
                const files = municipalityData.files || [];

                selectedFileTypes.forEach(fileType => {
                    const typeFiles = files.filter(file => file.includes(fileType));
                    if (!fileBreakdown[fileType]) fileBreakdown[fileType] = 0;
                    fileBreakdown[fileType] += typeFiles.length;
                    totalFiles += typeFiles.length;
                });
            }
        });

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
    }
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
        try {
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
                tableContainer.innerHTML = '<div class="no-data-message"><p>No features found in the loaded data.</p></div>';
                tableInfo.textContent = 'No data available';
            }
        } catch (error) {
            console.error('Error loading attribute table:', error);
            tableContainer.innerHTML = '<div class="no-data-message"><p>Error loading attribute data.</p></div>';
            tableInfo.textContent = 'Error loading data';
        }
    } else {
        tableContainer.innerHTML = '<div class="no-data-message"><p>No geospatial data loaded. Please upload a file or select cadastral data to view attributes.</p></div>';
        tableInfo.textContent = 'No data loaded';
    }
}

// Update drawing statistics
function updateDrawingStats() {
    const drawnCount = document.getElementById('drawnCount');
    const drawnSelected = document.getElementById('drawnSelected');
    const drawnPolygonsList = document.getElementById('drawnPolygonsList');

    if (drawnItems) {
        const layers = drawnItems.getLayers();
        const totalDrawn = layers.length;

        // Update counts
        if (drawnCount) drawnCount.textContent = totalDrawn;
        if (drawnSelected) drawnSelected.textContent = '0'; // TODO: implement selection tracking

        // Update list
        if (drawnPolygonsList) {
            if (totalDrawn === 0) {
                drawnPolygonsList.innerHTML = '<p>No polygons drawn yet</p>';
            } else {
                let listHTML = '<ul class="drawn-list">';
                layers.forEach((layer, index) => {
                    const type = layer instanceof L.Circle ? 'Circle' :
                                layer instanceof L.Rectangle ? 'Rectangle' :
                                layer instanceof L.Polygon ? 'Polygon' :
                                layer instanceof L.Polyline ? 'Polyline' :
                                layer instanceof L.Marker ? 'Marker' : 'Feature';
                    listHTML += `<li data-layer-index="${index}">${type} ${index + 1}</li>`;
                });
                listHTML += '</ul>';
                drawnPolygonsList.innerHTML = listHTML;
            }
        }
    }
}

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
    console.log('Showing Map View...');

    // Hide all views
    document.querySelectorAll('.view-content').forEach(view => {
        view.style.display = 'none';
        view.classList.remove('active');
        console.log(`Hiding view: ${view.id}`);
    });

    // Show map view explicitly
    const mapView = document.getElementById('mapView');
    const mapContainer = document.querySelector('.map-container');
    const mapElement = document.getElementById('map');

    if (mapView) {
        mapView.style.display = 'block';
        mapView.classList.add('active');
        console.log('Map view shown');
    }

    if (mapContainer) {
        mapContainer.style.display = 'block';
        mapContainer.style.height = '100%';
        console.log('Map container shown');
    }

    if (mapElement) {
        mapElement.style.display = 'block';
        mapElement.style.height = '100%';
        mapElement.style.minHeight = '600px';
        console.log('Map element configured');
        console.log('Map element dimensions:', `${mapElement.offsetWidth}x${mapElement.offsetHeight}`);
    }

    // Set active button state
    document.querySelectorAll('.view-toggle button').forEach(btn => btn.classList.remove('active'));
    const mapViewBtn = document.getElementById('mapViewBtn');
    if (mapViewBtn) {
        mapViewBtn.classList.add('active');
    }

    // Refresh map if needed - multiple attempts
    if (map) {
        console.log('Refreshing map...');
        setTimeout(() => {
            map.invalidateSize();
            console.log('Map size invalidated immediately');
        }, 50);

        setTimeout(() => {
            map.invalidateSize();
            console.log('Map size invalidated after 200ms');
        }, 200);

        setTimeout(() => {
            map.invalidateSize();
            console.log('Map size invalidated after 500ms');
        }, 500);
    } else {
        console.log('Map object not found!');
    }

    console.log('Map View switch completed');
};

window.handleTableViewClick = function() {
    // Hide all views
    document.querySelectorAll('.view-content').forEach(view => {
        view.style.display = 'none';
        view.classList.remove('active');
    });

    // Show table view
    document.getElementById('tableView').style.display = 'block';
    document.getElementById('tableView').classList.add('active');

    // Set active button state
    document.querySelectorAll('.view-toggle button').forEach(btn => btn.classList.remove('active'));
    document.getElementById('tableViewBtn').classList.add('active');

    // Load table data if available
    loadAttributeTable();

    console.log('Switched to Table View');
};

window.showAdjacencyView = function() {
    // Hide all views
    document.querySelectorAll('.view-content').forEach(view => {
        view.style.display = 'none';
        view.classList.remove('active');
    });

    // Show adjacency view
    document.getElementById('adjacencyView').style.display = 'block';
    document.getElementById('adjacencyView').classList.add('active');

    // Set active button state
    document.querySelectorAll('.view-toggle button').forEach(btn => btn.classList.remove('active'));
    document.getElementById('adjacencyViewBtn').classList.add('active');

    console.log('Switched to Adjacency View');
};

window.showMappingView = function() {
    // Hide all views
    document.querySelectorAll('.view-content').forEach(view => {
        view.style.display = 'none';
        view.classList.remove('active');
    });

    // Show mapping view
    document.getElementById('mappingView').style.display = 'block';
    document.getElementById('mappingView').classList.add('active');

    // Set active button state
    document.querySelectorAll('.view-toggle button').forEach(btn => btn.classList.remove('active'));
    document.getElementById('mappingViewBtn').classList.add('active');

    // Update drawing stats
    updateDrawingStats();

    console.log('Switched to Mapping View');
};

// Cadastral data
let cadastralData = null;

// Load cadastral data and populate selects
async function loadCadastralData() {
    console.log('Loading cadastral data...');
    try {
        const response = await fetch('/api/v1/get-cadastral-structure/');
        console.log('Cadastral data response status:', response.status);

        if (response.ok) {
            cadastralData = await response.json();
            console.log('Cadastral data loaded:', cadastralData);
            console.log('Number of regions:', Object.keys(cadastralData).length);

            populateRegionsSelect();
            setupCadastralEventListeners();
        } else {
            console.error('Failed to load cadastral data:', response.status, response.statusText);
            const errorText = await response.text();
            console.error('Error response:', errorText);
        }
    } catch (error) {
        console.error('Error loading cadastral data:', error);
    }
}

// Populate regions select
function populateRegionsSelect() {
    console.log('Populating regions select...');
    const regionsSelect = document.getElementById('cadastralRegions');
    console.log('Regions select element found:', regionsSelect);
    console.log('Cadastral data available:', !!cadastralData);

    if (!regionsSelect) {
        console.error('cadastralRegions select element not found!');
        return;
    }

    if (!cadastralData) {
        console.error('No cadastral data available!');
        return;
    }

    // Clear existing options
    regionsSelect.innerHTML = '';
    console.log('Cleared existing options');

    // Add region options
    const regions = Object.keys(cadastralData).sort();
    console.log('Regions to add:', regions);

    regions.forEach(regionName => {
        const option = document.createElement('option');
        option.value = regionName;
        option.textContent = regionName;
        regionsSelect.appendChild(option);
        console.log('Added region:', regionName);
    });

    console.log('Regions select populated with', regions.length, 'regions');
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

// Initialize map and controls when DOM is loaded
document.addEventListener('DOMContentLoaded', function() {
    console.log('DOM loaded, initializing map...');

    // Initialize map first
    setTimeout(() => {
        // Ensure Map View is shown
        console.log('Ensuring Map View is visible...');
        showMapView();

        // Initialize map
        console.log('Starting map initialization...');
        initializeMap();
        loadCadastralData();

        // Force map refresh multiple times to ensure it renders
        setTimeout(() => {
            if (map) {
                console.log('First invalidateSize call...');
                map.invalidateSize();
            }
        }, 200);

        setTimeout(() => {
            if (map) {
                console.log('Second invalidateSize call...');
                map.invalidateSize();
            }
        }, 1000);

        console.log('✅ Direct Leaflet map with all providers initialized');
        console.log('✅ Native Leaflet controls integrated');
    }, 100);
});

// GPKG Processing Function for Direct S3 Loading
async function processGpkgFile(arrayBuffer, filePath) {
    try {
        console.log(`Processing GPKG file: ${filePath}`);

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
            console.log('Trying to find table with geometry...');
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

        console.log(`Using table: ${tableName}`);

        // Get features from the main table
        const featuresQuery = `SELECT * FROM "${tableName}" LIMIT 100`;
        const result = db.exec(featuresQuery);

        if (result.length === 0) {
            console.error('No features found in table');
            return null;
        }

        const columns = result[0].columns;
        const rows = result[0].values;

        console.log(`Found ${rows.length} features with columns:`, columns);

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

        console.log(`Successfully processed ${features.length} features from ${filePath}`);
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
            console.log('sql.js loaded successfully');
            resolve();
        };
        script.onerror = () => {
            reject(new Error('Failed to load sql.js'));
        };
        document.head.appendChild(script);
    });
}