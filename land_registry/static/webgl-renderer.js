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
     * @returns {L.Layer} Leaflet layer (GeoJSON or glify)
     */
    renderGeoJSON(geojson, options = {}) {
        if (!geojson || !geojson.features) {
            console.warn('[WebGL] No features to render');
            return null;
        }

        const featureCount = geojson.features.length;
        const useWebGL = this._shouldUseWebGL(geojson, options);

        console.log(`[WebGL] Rendering ${featureCount} features using ${useWebGL ? 'WebGL ⚡' : 'SVG 🎨'}`);

        if (useWebGL) {
            return this._renderWebGL(geojson, options);
        } else {
            return this._renderSVG(geojson, options);
        }
    },

    /**
     * Render using WebGL (Leaflet.glify) for high performance
     * @private
     */
    _renderWebGL(geojson, options) {
        const features = geojson.features;

        // Filter polygon features (glify supports polygons, lines, points separately)
        const polygons = features.filter(f =>
            f.geometry && (f.geometry.type === 'Polygon' || f.geometry.type === 'MultiPolygon')
        );

        if (polygons.length === 0) {
            console.warn('[WebGL] No polygon features found, falling back to SVG');
            return this._renderSVG(geojson, options);
        }

        // Prepare data in glify format
        const glifyData = polygons.map(feature => ({
            type: 'Feature',
            geometry: feature.geometry,
            properties: feature.properties || {}
        }));

        try {
            // Create WebGL layer with glify.shapes
            const glifyLayer = L.glify.shapes({
                map: window.map,
                data: {
                    type: 'FeatureCollection',
                    features: glifyData
                },
                color: (index, feature) => {
                    // Custom color function or default
                    if (options.colorFunction && typeof options.colorFunction === 'function') {
                        return options.colorFunction(index, feature);
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
            this.glifyLayers.push(glifyLayer);
            this.currentLayers.push(glifyLayer);

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
        const layer = L.geoJSON(geojson, {
            style: (feature) => {
                return {
                    color: options.color || '#3388ff',
                    weight: options.weight || 2,
                    fillOpacity: options.fillOpacity !== undefined ? options.fillOpacity : 0.4,
                    opacity: 1
                };
            },
            onEachFeature: (feature, layer) => {
                // Apply stripe pattern (existing logic from map.js)
                if (options.useStripes !== false && typeof window.createStripePattern === 'function') {
                    layer.on('add', function() {
                        const pathElement = layer.getElement();
                        if (pathElement) {
                            const angle = window.getRandomStripeAngle ? window.getRandomStripeAngle() : 45;
                            const patternId = window.createStripePattern(angle, options.color || '#3388ff');
                            pathElement.style.fill = `url(#${patternId})`;
                        }
                    });
                }

                // Click handler
                layer.on('click', (e) => {
                    L.DomEvent.stopPropagation(e);
                    if (options.onClick && typeof options.onClick === 'function') {
                        options.onClick(feature, layer, e);
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

        this.currentRenderer = 'svg';
        this.currentLayers.push(layer);

        console.log(`[SVG] Rendered ${geojson.features.length} features using SVG`);

        return layer;
    },

    /**
     * Determine if WebGL should be used based on feature count and options
     * @private
     */
    _shouldUseWebGL(geojson, options) {
        // Force mode if specified
        if (options.forceRenderer === 'webgl') {
            if (!this.isWebGLSupported) {
                console.warn('[WebGL] Forced WebGL mode but WebGL not supported, using SVG');
                return false;
            }
            return true;
        }
        if (options.forceRenderer === 'svg') {
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
        // Remove glify layers
        this.glifyLayers.forEach(glifyLayer => {
            if (glifyLayer && glifyLayer.remove) {
                glifyLayer.remove();
            }
        });
        this.glifyLayers = [];

        // Remove standard layers
        this.currentLayers.forEach(layer => {
            if (layer && layer.remove && window.map.hasLayer(layer)) {
                window.map.removeLayer(layer);
            }
        });
        this.currentLayers = [];

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

    // Auto-initialize when map is ready
    window.addEventListener('DOMContentLoaded', () => {
        // Wait for map to be initialized
        const checkMapReady = setInterval(() => {
            if (window.map) {
                WebGLRenderer.init();
                clearInterval(checkMapReady);
            }
        }, 100);

        // Timeout after 10 seconds
        setTimeout(() => clearInterval(checkMapReady), 10000);
    });
}

console.log('[WebGL] webgl-renderer.js loaded successfully');
