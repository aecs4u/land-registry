/**
 * WebGL-based cadastral renderer using Leaflet.glify
 * Provides GPU-accelerated rendering for large datasets with automatic fallback to SVG
 *
 * Key Features:
 * - Auto-switching between WebGL and SVG based on feature count
 * - GPU acceleration via Leaflet.glify for smooth rendering of 10,000+ parcels
 * - Graceful degradation for browsers without WebGL support
 * - Maintains compatibility with existing Leaflet ecosystem
 */

const WebGLRenderer = {
    // Configuration
    WEBGL_THRESHOLD: 1000,  // Use WebGL for datasets with >1000 features
    MAX_SVG_FEATURES: 5000, // Hard limit for SVG rendering (performance constraint)

    // State
    isWebGLSupported: false,
    currentRenderer: null,
    currentLayers: [],
    glifyLayers: [],  // Track WebGL layers separately for cleanup
    renderLayers: new Set(),
    currentMap: null,

    /**
     * Initialize WebGL support detection and logging
     * @returns {boolean} True if WebGL is supported
     */
    init() {
        this.isWebGLSupported = this._detectWebGL();

        if (this.isWebGLSupported) {
            console.log('[WebGL] GPU acceleration available ✓');
        } else {
            console.warn('[WebGL] GPU acceleration NOT available - will use SVG fallback');
        }

        return this.isWebGLSupported;
    },

    /**
     * Render GeoJSON data using optimal renderer (WebGL or SVG)
     * @param {Object} geojson - GeoJSON FeatureCollection
     * @param {Object} options - Rendering options
     * @param {Function} options.onClick - Click handler function(feature, layer, event)
     * @param {Function} options.onHover - Hover handler function(feature, layer, event)
     * @param {string} options.color - Polygon color
     * @param {number} options.weight - Border weight
     * @param {number} options.fillOpacity - Fill opacity
     * @param {string} options.forceRenderer - Force 'webgl' or 'svg' mode
     * @param {boolean} options.enablePopups - Enable popups (default true)
     * @param {boolean} options.useStripes - Use SVG stripe patterns (SVG only, default true)
     * @returns {L.Layer} Leaflet layer adapter for SVG, Canvas or glify
     */
    renderGeoJSON(geojson, options = {}) {
        if (!geojson || !geojson.features) {
            console.warn('[WebGL] No features to render');
            return null;
        }

        return this._renderManagedLayer(geojson, options);
    },

    /**
     * Leaflet adapter for a WebGL polygon layer. A nearly transparent Canvas
     * layer keeps Leaflet's existing feature hit-testing, selection and popup
     * behavior while glify draws the visible polygons.
     * @private
     */
    _renderManagedLayer(geojson, options) {
        const renderer = this;
        const RenderLayer = L.Layer.extend({
            initialize() {
                this.options = { cadastralLayer: true };
                this._sourceGeoJSON = geojson;
                this._renderOptions = options;
                this._activeLayer = null;
                this._glifyLayer = null;
                this._mapRef = null;
                this._bounds = null;
                this._settingsHandler = () => this._renderCurrent();
                this._zoomHandler = () => {
                    const lodEnabled = this._renderOptions.lodEnabled ??
                        window.MapRenderingControls?.getSettings?.().lodEnabled;
                    if (lodEnabled) this._renderCurrent();
                };
            },

            onAdd(map) {
                this._mapRef = map;
                renderer.renderLayers.add(this);
                map.on('zoomend', this._zoomHandler);
                window.addEventListener('map-rendering-settings-change', this._settingsHandler);
                this._renderCurrent();
            },

            _removeRenderedLayers() {
                const map = this._mapRef;
                if (this._activeLayer) {
                    if (map?.hasLayer(this._activeLayer)) map.removeLayer(this._activeLayer);
                    renderer.currentLayers = renderer.currentLayers.filter(layer => layer !== this._activeLayer);
                    this._activeLayer = null;
                }
                if (this._glifyLayer) {
                    this._glifyLayer.remove?.();
                    renderer.glifyLayers = renderer.glifyLayers.filter(layer => layer !== this._glifyLayer);
                    this._glifyLayer = null;
                }
                if (!renderer.glifyLayers.length) renderer.currentMap = null;
            },

            _renderCurrent() {
                const map = this._mapRef;
                if (!map) return;
                this._removeRenderedLayers();

                const settings = window.MapRenderingControls?.getSettings?.() || {};
                const mode = options.forceRenderer || settings.rendererMode || 'auto';
                const lodEnabled = options.lodEnabled ?? !!settings.lodEnabled;
                const renderData = lodEnabled
                    ? renderer._simplifyGeoJSON(geojson, map.getZoom())
                    : geojson;
                const useWebGL = renderer._shouldUseWebGL(renderData, { ...options, forceRenderer: mode });
                console.log(`[WebGL] Rendering ${renderData.features.length} features using ${useWebGL ? 'WebGL ⚡' : 'SVG 🎨'}${lodEnabled ? ' (LOD)' : ''}`);

                const polygonCount = renderData.features.reduce((count, feature) =>
                    count + Number(feature.geometry?.type === 'Polygon' || feature.geometry?.type === 'MultiPolygon'), 0);
                if (useWebGL && polygonCount > 0) {
                    const candidate = renderer._renderWebGL(renderData, {
                        ...options,
                        map,
                        onClick: null,
                        onHover: null,
                        enablePopups: false,
                    });
                    if (renderer.currentRenderer === 'webgl') {
                        this._glifyLayer = candidate;
                        this._activeLayer = renderer._renderSVG(renderData, {
                            ...options,
                            originalFeatures: geojson.features,
                            hitTestOnly: true,
                            useStripes: false,
                            enablePopups: false,
                        });
                    } else {
                        renderer.currentLayers = renderer.currentLayers.filter(layer => layer !== candidate);
                        this._activeLayer = renderer._renderSVG(renderData, {
                            ...options,
                            originalFeatures: geojson.features,
                        });
                    }
                } else {
                    this._activeLayer = renderer._renderSVG(renderData, {
                        ...options,
                        originalFeatures: geojson.features,
                    });
                }
                this._activeLayer?.addTo(map);
            },

            onRemove(map) {
                map.off('zoomend', this._zoomHandler);
                window.removeEventListener('map-rendering-settings-change', this._settingsHandler);
                this._removeRenderedLayers();
                this._mapRef = null;
                renderer.renderLayers.delete(this);
            },

            eachLayer(callback, context) { this._activeLayer?.eachLayer(callback, context); },
            getLayers() { return this._activeLayer?.getLayers() || []; },
            getBounds() {
                if (!this._bounds) this._bounds = renderer._geometryBounds(geojson);
                return this._bounds;
            },
            toGeoJSON() { return this._sourceGeoJSON; },
        });
        return new RenderLayer();
    },

    _geometryBounds(geojson) {
        const bounds = L.latLngBounds();
        const visit = coordinates => {
            if (!Array.isArray(coordinates)) return;
            if (coordinates.length >= 2 && Number.isFinite(coordinates[0]) && Number.isFinite(coordinates[1])) {
                bounds.extend([coordinates[1], coordinates[0]]);
                return;
            }
            coordinates.forEach(visit);
        };
        geojson.features.forEach(feature => visit(feature.geometry?.coordinates));
        return bounds;
    },

    _simplifyGeoJSON(geojson, zoom) {
        const tolerance = Math.pow(2, 16 - zoom) * 0.0001;
        if (!Number.isFinite(tolerance) || tolerance <= 0) return geojson;
        return {
            ...geojson,
            features: geojson.features.map(feature => {
                if (!feature.geometry) return feature;
                const simplified = this._simplifyGeometry(feature.geometry, tolerance);
                return simplified === feature.geometry ? feature : { ...feature, geometry: simplified };
            }),
        };
    },

    _simplifyGeometry(geometry, tolerance) {
        const line = coordinates => this._simplifyLine(coordinates, tolerance);
        const ring = coordinates => this._simplifyRing(coordinates, tolerance);
        let coordinates;
        switch (geometry.type) {
            case 'LineString': coordinates = line(geometry.coordinates); break;
            case 'MultiLineString': coordinates = geometry.coordinates.map(line); break;
            case 'Polygon': coordinates = geometry.coordinates.map(ring); break;
            case 'MultiPolygon': coordinates = geometry.coordinates.map(polygon => polygon.map(ring)); break;
            default: return geometry;
        }
        return { ...geometry, coordinates };
    },

    _simplifyLine(points, tolerance) {
        if (!Array.isArray(points) || points.length <= 2) return points;
        const toleranceSquared = tolerance * tolerance;
        const keep = new Uint8Array(points.length);
        keep[0] = 1;
        keep[points.length - 1] = 1;
        const stack = [[0, points.length - 1]];
        while (stack.length) {
            const [first, last] = stack.pop();
            let maxDistance = toleranceSquared;
            let maxIndex = -1;
            for (let i = first + 1; i < last; i++) {
                const distance = this._pointSegmentDistanceSquared(points[i], points[first], points[last]);
                if (distance > maxDistance) { maxDistance = distance; maxIndex = i; }
            }
            if (maxIndex >= 0) {
                keep[maxIndex] = 1;
                stack.push([first, maxIndex], [maxIndex, last]);
            }
        }
        return points.filter((_, index) => keep[index]);
    },

    _simplifyRing(ring, tolerance) {
        if (!Array.isArray(ring) || ring.length <= 4) return ring;
        const closed = ring[0][0] === ring[ring.length - 1][0] && ring[0][1] === ring[ring.length - 1][1];
        const points = closed ? ring.slice(0, -1) : ring.slice();
        if (points.length <= 3) return ring;
        let farthest = 1, greatestDistance = -1;
        for (let i = 1; i < points.length; i++) {
            const dx = points[i][0] - points[0][0], dy = points[i][1] - points[0][1];
            const distance = dx * dx + dy * dy;
            if (distance > greatestDistance) { greatestDistance = distance; farthest = i; }
        }
        const firstPart = this._simplifyLine(points.slice(0, farthest + 1), tolerance);
        const wrappedPart = this._simplifyLine(points.slice(farthest).concat([points[0]]), tolerance);
        let simplified = firstPart.concat(wrappedPart.slice(1, -1));
        if (simplified.length < 3) {
            let third = -1, greatestArea = -1;
            for (let i = 1; i < points.length; i++) {
                const area = this._pointSegmentDistanceSquared(points[i], points[0], points[farthest]);
                if (area > greatestArea) { greatestArea = area; third = i; }
            }
            simplified = [points[0], points[farthest], points[third]];
        }
        simplified.push(simplified[0]);
        return simplified;
    },

    _pointSegmentDistanceSquared(point, start, end) {
        const dx = end[0] - start[0], dy = end[1] - start[1];
        if (dx === 0 && dy === 0) {
            const px = point[0] - start[0], py = point[1] - start[1];
            return px * px + py * py;
        }
        const t = Math.max(0, Math.min(1,
            ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / (dx * dx + dy * dy)));
        const px = start[0] + t * dx - point[0], py = start[1] + t * dy - point[1];
        return px * px + py * py;
    },

    /**
     * Render using WebGL (Leaflet.glify) for high performance
     * @private
     */
    _renderWebGL(geojson, options) {
        const features = geojson.features;

        // Filter polygon features (glify supports polygons, lines, points separately)
        const polygons = features.map((feature, index) => ({ feature, index })).filter(({ feature }) =>
            feature.geometry && (feature.geometry.type === 'Polygon' || feature.geometry.type === 'MultiPolygon')
        );

        if (polygons.length === 0) {
            console.warn('[WebGL] No polygon features found, falling back to SVG');
            return this._renderSVG(geojson, options);
        }

        // Prepare data in glify format
        const glifyIndexes = new WeakMap();
        const glifyData = polygons.map(({ feature, index }) => {
            const renderedFeature = {
                type: 'Feature',
                geometry: feature.geometry,
                properties: feature.properties || {},
            };
            glifyIndexes.set(renderedFeature, index);
            return renderedFeature;
        });

        try {
            const map = options.map || (window.getFoliumMapInstance && window.getFoliumMapInstance()) || window.map;
            if (!map) {
                console.warn('[WebGL] No Leaflet map instance found, falling back to SVG');
                return this._renderSVG(geojson, options);
            }
            // Create WebGL layer with glify.shapes
            const glifyLayer = L.glify.shapes({
                map,
                data: {
                    type: 'FeatureCollection',
                    features: glifyData
                },
                color: (index, feature) => {
                    // Custom color function or default
                    if (options.colorFunction && typeof options.colorFunction === 'function') {
                        const sourceIndex = feature && glifyIndexes.has(feature) ? glifyIndexes.get(feature) : index;
                        const color = options.colorFunction(sourceIndex, feature);
                        return typeof color === 'string' ? this._hexToRgb(color) : color;
                    }
                    return this._hexToRgb(options.color || '#3388ff');
                },
                opacity: options.fillOpacity !== undefined ? options.fillOpacity : 0.6,
                border: options.border !== false,
                borderColor: () => {
                    return this._hexToRgb(options.borderColor || '#3388ff');
                },
                borderWidth: options.borderWidth || 2,

                // Click handling
                click: (e, feature, index) => {
                    if (options.onClick && typeof options.onClick === 'function') {
                        // Create pseudo-layer object for compatibility
                        const pseudoLayer = {
                            feature: feature,
                            getElement: () => null,
                            _leaflet_id: index
                        };
                        options.onClick(feature, pseudoLayer, e);
                    }
                },

                // Hover handling
                hover: (e, feature, index) => {
                    if (options.onHover && typeof options.onHover === 'function') {
                        const pseudoLayer = {
                            feature: feature,
                            getElement: () => null,
                            _leaflet_id: index
                        };
                        options.onHover(feature, pseudoLayer, e);
                    }
                }
            });

            this.currentRenderer = 'webgl';
            this.currentMap = map;
            this.glifyLayers.push(glifyLayer);

            // Note: Popups in WebGL mode require custom implementation
            // Consider showing feature info in a sidebar/panel instead
            if (options.enablePopups !== false) {
                console.info('[WebGL] Popups in WebGL mode shown on click in sidebar (not as Leaflet popups)');
            }

            console.log(`[WebGL] Successfully rendered ${polygons.length} polygons with GPU acceleration`);

            return glifyLayer;

        } catch (error) {
            console.error('[WebGL] Failed to create glify layer, falling back to SVG:', error);
            return this._renderSVG(geojson, options);
        }
    },

    /**
     * Render using SVG (existing Leaflet.GeoJSON) for high fidelity
     * @private
     */
    _renderSVG(geojson, options) {
        const featureIndexes = new WeakMap();
        geojson.features.forEach((feature, index) => {
            if (feature && typeof feature === 'object') featureIndexes.set(feature, index);
        });
        const colorForFeature = feature => {
            const index = featureIndexes.has(feature) ? featureIndexes.get(feature) : 0;
            const color = options.colorFunction && typeof options.colorFunction === 'function'
                ? options.colorFunction(index, feature)
                : options.color;
            return typeof color === 'string' ? color : (options.color || '#3388ff');
        };
        const canvasHitRenderer = options.hitTestOnly && typeof L.canvas === 'function'
            ? L.canvas({ padding: 0.5 }) : undefined;
        const layer = L.geoJSON(geojson, {
            ...(canvasHitRenderer ? { renderer: canvasHitRenderer } : {}),
            style: (feature) => {
                const color = colorForFeature(feature);
                return {
                    color,
                    fillColor: color,
                    weight: options.hitTestOnly ? 0.01 : (options.weight || 2),
                    fillOpacity: options.hitTestOnly ? 0.001 : (options.fillOpacity !== undefined ? options.fillOpacity : 0.4),
                    opacity: options.hitTestOnly ? 0.001 : 1,
                };
            },
            onEachFeature: (feature, layer) => {
                const featureIndex = featureIndexes.get(feature) || 0;
                const sourceFeature = options.originalFeatures?.[featureIndex] || feature;
                if (sourceFeature !== feature) {
                    layer._sourceFeature = sourceFeature;
                    layer.feature = sourceFeature;
                }
                if (options.hitTestOnly) layer._webglHitTest = true;
                // Apply stripe pattern (existing logic from map.js)
                if (options.useStripes !== false && typeof window.createStripePattern === 'function') {
                    layer.on('add', function() {
                        const pathElement = layer.getElement();
                        if (pathElement) {
                            const angle = window.getRandomStripeAngle ? window.getRandomStripeAngle() : 45;
                            const patternId = window.createStripePattern(angle, colorForFeature(feature));
                            pathElement.style.fill = `url(#${patternId})`;
                        }
                    });
                }

                // Click handler
                layer.on('click', (e) => {
                    L.DomEvent.stopPropagation(e);
                    if (options.onClick && typeof options.onClick === 'function') {
                        options.onClick(sourceFeature, layer, e, featureIndex);
                    }
                });

                // Hover handler
                if (options.onHover && typeof options.onHover === 'function') {
                    layer.on('mouseover', (e) => {
                        options.onHover(feature, layer, e);
                    });
                }

                // Popup
                if (options.enablePopups !== false && feature.properties) {
                    let popupContent = '<div class="popup-content">';

                    // Format properties for popup
                    Object.keys(feature.properties).forEach(key => {
                        if (key !== 'geometry' && key !== 'id') {
                            const value = feature.properties[key];
                            popupContent += `<strong>${key}:</strong> ${value}<br>`;
                        }
                    });

                    popupContent += '</div>';
                    layer.bindPopup(popupContent, { maxWidth: 350 });
                }
            }
        });

        if (!options.hitTestOnly) this.currentRenderer = 'svg';
        this.currentLayers.push(layer);

        console.log(`[SVG] Rendered ${geojson.features.length} features using SVG`);

        return layer;
    },

    /**
     * Determine if WebGL should be used based on feature count and options
     * @private
     */
    _shouldUseWebGL(geojson, options) {
        const mode = options.forceRenderer || window.MapRenderingControls?.getSettings?.().rendererMode || 'auto';
        // Force mode if specified
        if (mode === 'webgl') {
            if (!this.isWebGLSupported) {
                console.warn('[WebGL] Forced WebGL mode but WebGL not supported, using SVG');
                return false;
            }
            return true;
        }
        if (mode === 'svg') {
            return false;
        }

        const featureCount = geojson.features.length;

        // Check if dataset is too large for SVG
        if (featureCount > this.MAX_SVG_FEATURES) {
            if (!this.isWebGLSupported) {
                console.error(`[WebGL] Dataset too large (${featureCount} features, max ${this.MAX_SVG_FEATURES}) and WebGL not available`);
                // Try SVG anyway, may be slow
            }
            return this.isWebGLSupported;
        }

        // Use WebGL for large datasets if available
        if (featureCount >= this.WEBGL_THRESHOLD && this.isWebGLSupported) {
            return true;
        }

        // Default to SVG for small datasets
        return false;
    },

    /**
     * Detect WebGL support in browser
     * @private
     */
    _detectWebGL() {
        try {
            const canvas = document.createElement('canvas');
            const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');

            if (!gl) {
                return false;
            }

            // Check if Leaflet.glify is loaded
            if (typeof L.glify === 'undefined') {
                console.warn('[WebGL] Leaflet.glify library not loaded');
                return false;
            }

            return true;
        } catch (e) {
            console.error('[WebGL] Detection failed:', e);
            return false;
        }
    },

    /**
     * Convert hex color to RGB array for glify
     * @private
     */
    _hexToRgb(hex) {
        // Remove # if present
        hex = hex.replace(/^#/, '');

        // Parse hex
        const bigint = parseInt(hex, 16);
        const r = (bigint >> 16) & 255;
        const g = (bigint >> 8) & 255;
        const b = bigint & 255;

        // Glify expects RGB values normalized to 0-1
        return {
            r: r / 255,
            g: g / 255,
            b: b / 255
        };
    },

    /**
     * Clear all WebGL layers from map
     */
    clearLayers() {
        const map = this.currentMap || (window.getFoliumMapInstance && window.getFoliumMapInstance()) || window.map;
        this.renderLayers.forEach(layer => {
            const layerMap = layer._mapRef || map;
            if (layerMap?.hasLayer(layer)) layerMap.removeLayer(layer);
        });
        // Remove glify layers
        this.glifyLayers.forEach(glifyLayer => {
            if (glifyLayer && glifyLayer.remove) {
                glifyLayer.remove();
            }
        });
        this.glifyLayers = [];

        // Remove standard layers
        this.currentLayers.forEach(layer => {
            if (layer && typeof layer.remove === 'function') layer.remove();
            else if (layer && map?.hasLayer(layer)) map.removeLayer(layer);
        });
        this.currentLayers = [];
        this.currentMap = null;
        this.currentRenderer = null;

        console.log('[WebGL] Cleared all rendering layers');
    },

    /**
     * Get current renderer type
     * @returns {string} 'webgl', 'svg', or null
     */
    getCurrentRenderer() {
        return this.currentRenderer;
    },

    /**
     * Get feature count threshold for WebGL usage
     * @returns {number} Threshold feature count
     */
    getThreshold() {
        return this.WEBGL_THRESHOLD;
    },

    /**
     * Set threshold for WebGL usage
     * @param {number} threshold - New threshold value
     */
    setThreshold(threshold) {
        if (threshold > 0) {
            this.WEBGL_THRESHOLD = threshold;
            console.log(`[WebGL] Threshold updated to ${threshold} features`);
        }
    }
};

// Initialize on load
if (typeof window !== 'undefined') {
    window.WebGLRenderer = WebGLRenderer;

    // Initialize: detect WebGL support once L.glify and DOM are ready
    const doInit = () => {
        if (typeof L !== 'undefined' && typeof L.glify !== 'undefined') {
            WebGLRenderer.init();
        } else {
            // L.glify may load after DOM ready (e.g. deferred scripts), retry briefly
            let retries = 0;
            const check = setInterval(() => {
                if ((typeof L !== 'undefined' && typeof L.glify !== 'undefined') || ++retries > 30) {
                    clearInterval(check);
                    WebGLRenderer.init();
                }
            }, 100);
        }
    };

    if (document.readyState === 'loading') {
        window.addEventListener('DOMContentLoaded', doInit);
    } else {
        doInit();
    }
}

console.log('[WebGL] webgl-renderer.js loaded successfully');
