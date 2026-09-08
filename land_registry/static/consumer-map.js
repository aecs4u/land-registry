/**
 * Embeddable land-registry map for sibling AECS4U applications.
 *
 * The host provides Leaflet and a container. Land Registry owns basemaps,
 * cadastral boundary layers, identify requests, and generic map controls;
 * consumers remain responsible for their domain markers and popups.
 */
(function (global) {
    'use strict';

    function joinUrl(base, path) {
        return String(base || '').replace(/\/$/, '') + path;
    }

    function escapeHtml(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function create(options) {
        options = options || {};
        if (!global.L) throw new Error('LandRegistryMap requires Leaflet');
        if (!options.container) throw new Error('LandRegistryMap requires a container');

        var labels = options.labels || {};
        var apiBase = String(options.apiBase || '/land-registry/api').replace(/\/$/, '');
        var hasFullscreen = !!(L.Control && L.Control.FullScreen);
        var map = L.map(options.container, {
            zoomControl: true,
            scrollWheelZoom: options.scrollWheelZoom !== false,
            fullscreenControl: options.fullscreenControl !== false && hasFullscreen,
            maxZoom: options.maxZoom || 22
        }).setView(options.center || [41.9028, 12.4964], options.zoom || 6);

        map.createPane('cadastralPane').style.zIndex = String(options.cadastralZIndex || 440);

        var streets = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            maxZoom: 22,
            maxNativeZoom: 19,
            attribution: '&copy; OpenStreetMap contributors'
        });
        var satellite = L.tileLayer(
            'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
            { maxZoom: 22, maxNativeZoom: 19, attribution: '&copy; Esri, Maxar, Earthstar Geographics' }
        );
        var terrain = L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', {
            maxZoom: 22,
            maxNativeZoom: 17,
            attribution: '&copy; OpenTopoMap (CC-BY-SA)'
        });
        streets.addTo(map);

        var sheets = L.tileLayer(
            joinUrl(apiBase, '/tiles/cadastral-boundaries/{z}/{x}/{y}.png?layer=map'),
            { pane: 'cadastralPane', opacity: 0.75, minZoom: 13, maxZoom: 22, maxNativeZoom: 19, tileSize: 512, attribution: labels.cadastral || 'Cadastral data' }
        );
        var parcels = L.tileLayer(
            joinUrl(apiBase, '/tiles/cadastral-boundaries/{z}/{x}/{y}.png?layer=ple'),
            { pane: 'cadastralPane', opacity: 0.85, minZoom: 16, maxZoom: 22, maxNativeZoom: 19, tileSize: 512 }
        );
        var cadastral = L.layerGroup([sheets, parcels]);
        // Keep the boundary group attached to the map; the child layers are
        // zoom-gated and therefore do not request detail at overview zoom.
        cadastral.addTo(map);

        map.createPane('canonicalPane').style.zIndex = String(options.canonicalZIndex || 425);
        var canonicalLayers = {};
        var canonicalLayerModes = {};
        var canonicalLayerOptions = {};
        var canonicalLayerTokens = {};
        var canonicalRefreshAttached = false;
        function canonicalGeoJsonUrl(layerId) {
            var bounds = map.getBounds();
            var params = new URLSearchParams({
                west: bounds.getWest().toFixed(6),
                south: bounds.getSouth().toFixed(6),
                east: bounds.getEast().toFixed(6),
                north: bounds.getNorth().toFixed(6)
            });
            var configured = canonicalLayerOptions[layerId] || {};
            if (configured.maxFeatures) params.set('limit', String(configured.maxFeatures));
            return joinUrl(apiBase, '/map/layers/' + encodeURIComponent(layerId) + '/features') + '?' + params.toString();
        }
        function refreshCanonicalGeoJson(layerId) {
            if (canonicalLayerModes[layerId] !== 'geojson' || !canonicalLayers[layerId]) return;
            var token = (canonicalLayerTokens[layerId] || 0) + 1;
            canonicalLayerTokens[layerId] = token;
            fetch(canonicalGeoJsonUrl(layerId)).then(function (response) {
                if (!response.ok) throw new Error('Canonical GeoJSON request failed');
                return response.json();
            }).then(function (collection) {
                if (canonicalLayerTokens[layerId] !== token || !canonicalLayers[layerId]) return;
                var configured = canonicalLayerOptions[layerId] || {};
                var replacement = L.geoJSON(collection, {
                    style: configured.style || { color: '#7c3aed', weight: 1.5, fillOpacity: 0.12 },
                    pointToLayer: function (_feature, latlng) {
                        return L.circleMarker(latlng, { radius: 4, color: '#7c3aed', fillColor: '#7c3aed', fillOpacity: 0.75, weight: 1 });
                    },
                    onEachFeature: function (feature, featureLayer) {
                        var properties = feature.properties || {};
                        var rows = Object.keys(properties).slice(0, 10).map(function (key) {
                            return '<div><b>' + escapeHtml(key) + ':</b> ' + escapeHtml(properties[key]) + '</div>';
                        }).join('');
                        featureLayer.bindPopup(rows || 'No attributes');
                    }
                });
                map.removeLayer(canonicalLayers[layerId]);
                canonicalLayers[layerId] = replacement.addTo(map);
            }).catch(function (error) {
                if (global.console) console.warn('[LandRegistryMap] canonical GeoJSON failed', error);
            });
        }
        function refreshCanonicalGeoJsonLayers() {
            Object.keys(canonicalLayerModes).forEach(refreshCanonicalGeoJson);
        }
        function addCanonicalLayer(layerId, layerOptions) {
            layerOptions = layerOptions || {};
            if (canonicalLayers[layerId]) return canonicalLayers[layerId];
            canonicalLayerOptions[layerId] = layerOptions;
            if (global.L.vectorGrid && typeof global.L.vectorGrid.protobuf === 'function') {
                var url = joinUrl(apiBase, '/tiles/map-layers/' + encodeURIComponent(layerId) + '/{z}/{x}/{y}.pbf');
                var layer = global.L.vectorGrid.protobuf(url, {
                    pane: 'canonicalPane',
                    minZoom: layerOptions.minZoom || 0,
                    maxZoom: layerOptions.maxZoom || 22,
                    interactive: true,
                    vectorTileLayerStyles: { [layerId]: layerOptions.style || { color: '#7c3aed', weight: 1.5, fillOpacity: 0.12 } },
                    getFeatureId: feature => feature.properties && (feature.properties.id || feature.properties.point_id || feature.properties.sez21_id),
                });
                layer.on('click', function (event) {
                    var properties = (event.layer && event.layer.properties) || {};
                    var rows = Object.keys(properties).slice(0, 10).map(function (key) {
                        return '<div><b>' + escapeHtml(key) + ':</b> ' + escapeHtml(properties[key]) + '</div>';
                    }).join('');
                    L.popup().setLatLng(event.latlng).setContent(rows || 'No attributes').openOn(map);
                });
                canonicalLayers[layerId] = layer.addTo(map);
                canonicalLayerModes[layerId] = 'vector';
            } else {
                canonicalLayers[layerId] = L.layerGroup().addTo(map);
                canonicalLayerModes[layerId] = 'geojson';
                refreshCanonicalGeoJson(layerId);
            }
            if (!canonicalRefreshAttached) {
                map.on('moveend', refreshCanonicalGeoJsonLayers);
                canonicalRefreshAttached = true;
            }
            return canonicalLayers[layerId];
        }
        function removeCanonicalLayer(layerId) {
            if (canonicalLayers[layerId]) {
                map.removeLayer(canonicalLayers[layerId]);
                delete canonicalLayers[layerId];
                delete canonicalLayerModes[layerId];
                delete canonicalLayerOptions[layerId];
                delete canonicalLayerTokens[layerId];
            }
        }

        var baseLayers = {};
        baseLayers[labels.streets || 'Streets'] = streets;
        baseLayers[labels.satellite || 'Satellite'] = satellite;
        baseLayers[labels.terrain || 'Terrain'] = terrain;
        L.control.layers(baseLayers, null, { position: 'topright', collapsed: true }).addTo(map);
        L.control.scale({ metric: true, imperial: false, position: 'bottomleft' }).addTo(map);

        function identify(event) {
            if (!map.hasLayer(cadastral) || map.getZoom() < 13) return;
            var layer = map.getZoom() >= 16 ? 'ple' : 'map';
            var url = joinUrl(apiBase, '/cadastral-identify')
                + '?lat=' + encodeURIComponent(event.latlng.lat)
                + '&lng=' + encodeURIComponent(event.latlng.lng)
                + '&layer=' + layer;
            fetch(url).then(function (response) {
                if (!response.ok) throw new Error('Cadastral identify failed');
                return response.json();
            }).then(function (data) {
                if (!data.found) return;
                var rows = [];
                var reference = data.reference || data.label;
                if (reference) rows.push('<b>' + (layer === 'ple' ? (labels.parcel || 'Parcel') : (labels.sheet || 'Sheet')) + ':</b> ' + escapeHtml(reference));
                if (data.comune) rows.push('<b>' + (labels.municipality || 'Municipality') + ':</b> ' + escapeHtml(data.comune));
                if (data.provincia) rows.push('<b>' + (labels.province || 'Province') + ':</b> ' + escapeHtml(data.provincia));
                if (data.regione) rows.push('<b>' + (labels.region || 'Region') + ':</b> ' + escapeHtml(data.regione));
                if (rows.length) L.popup().setLatLng(event.latlng).setContent(rows.join('<br>')).openOn(map);
            }).catch(function (error) {
                if (global.console) console.warn('[LandRegistryMap] identify failed', error);
            });
        }
        map.on('click', identify);

        return {
            map: map,
            baseLayers: { streets: streets, satellite: satellite, terrain: terrain },
            cadastral: { layer: cadastral, sheets: sheets, parcels: parcels },
            canonical: { add: addCanonicalLayer, remove: removeCanonicalLayer, layers: canonicalLayers },
            destroy: function () { map.off('click', identify); map.remove(); }
        };
    }

    global.LandRegistryMap = { create: create, version: '1.0.0' };
})(window);
