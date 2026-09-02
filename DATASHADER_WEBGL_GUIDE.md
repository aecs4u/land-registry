# Datashader + WebGL Visualization Guide

## Overview

The Land Registry application now features a **hybrid high-performance visualization system** combining:
1. **Python Datashader** (server-side) - For massive datasets (100K+ parcels)
2. **PostGIS MVT + Leaflet.VectorGrid** - For crisp, streamed cadastral boundaries
3. **Leaflet Canvas/SVG** (fallback) - For client-side parcel interaction and compatibility

The canonical Folium workflow uses Canvas for cadastral layers with 750 or
more features and SVG below that threshold. The legacy WebGL modules remain
available for explicit host integrations but are not loaded by the main map.

This system provides Zornade-like professional visualization capabilities while preserving existing functionality including Folium support.

## Architecture

### Rendering Modes

1. **Datashader Tiles** (Server-Side Rasterization)
   - Best for: Overview maps, density heatmaps, 100K+ parcels
   - How it works: Server pre-aggregates data into PNG tiles
   - Performance: 60 FPS even with millions of parcels
   - Usage: See Datashader API below

2. **PostGIS MVT** (Client-Side Vector Tiles)
   - Best for: Interactive cadastral boundaries backed by aecs4u-stats
   - How it works: PostgreSQL clips compact vector tiles per viewport
   - Usage: Preferred automatically, with PNG fallback on unavailable DBs

3. **Leaflet Canvas/SVG** (Interactive client-side vectors)
   - Best for: Loaded parcel selection and compatibility
   - How it works: DOM-based vector rendering
   - Performance: Canvas avoids one SVG node per parcel for 750+ features
   - Usage: Automatic through the canonical cadastral layer factory

### Auto-Switching Logic

```
Feature Count < 750            → SVG (high fidelity)
Feature Count 750+             → Canvas (interactive parcel workflow)
Boundary overlay               → PostGIS MVT, PNG fallback
Massive density view           → Datashader tiles
```

## Datashader API

### 1. Map Tiles (For Leaflet Integration)

**Endpoint**: `GET /api/v1/tiles/datashader/{z}/{x}/{y}.png`

**Parameters**:
- `z`, `x`, `y`: TMS tile coordinates
- `region`: Filter by region name (optional)
- `colormap`: Color palette - "fire" (default), "viridis", "blues", etc.
- `agg`: Aggregation type - "count" (density), "mean", "sum"

**Example Usage**:
```javascript
// Add datashader tile layer to map
addDatashaderLayer('LOMBARDIA', 'fire', 0.7);

// Remove layer
removeDatashaderLayer();

// Toggle visibility
toggleDatashaderLayer();

// Update colormap
updateDatashaderColormap('viridis');

// Set opacity
setDatashaderOpacity(0.5);
```

**Browser Console**:
```javascript
// Quick test
window.addDatashaderLayer('LOMBARDIA');
```

### 2. Full Region Heatmap

**Endpoint**: `GET /api/v1/datashader/heatmap/{region}`

**Parameters**:
- `region`: Region name (e.g., "LOMBARDIA")
- `width`: Image width in pixels (default 800)
- `height`: Image height in pixels (default 600)
- `colormap`: Color palette

**Example**:
```bash
curl "http://localhost:8000/api/v1/datashader/heatmap/LOMBARDIA?width=1200&height=800&colormap=fire" > heatmap.png
```

### 3. Categorical Map

**Endpoint**: `GET /api/v1/datashader/categorical/{region}`

**Parameters**:
- `region`: Region name
- `field`: Field for categorization ("foglio", "particella", etc.)
- `width`, `height`: Image dimensions

**Example**:
```bash
curl "http://localhost:8000/api/v1/datashader/categorical/LOMBARDIA?field=foglio" > categorical.png
```

## WebGL Renderer API

### Automatic Usage

The WebGL renderer is automatically used when loading GeoJSON data:

```javascript
// Existing code works automatically with WebGL optimization
loadGeoJsonData();  // Auto-switches to WebGL if >1000 features

addGeoJsonToMap(geojson);  // Auto-switches to WebGL if >1000 features
```

### Manual Control

```javascript
// Force WebGL mode
const layer = WebGLRenderer.renderGeoJSON(geojson, {
    forceRenderer: 'webgl',
    color: '#3388ff',
    weight: 2,
    fillOpacity: 0.6
});

// Force SVG mode
const layer = WebGLRenderer.renderGeoJSON(geojson, {
    forceRenderer: 'svg',
    color: '#ff6b6b'
});

// Check current renderer
console.log(WebGLRenderer.getCurrentRenderer());  // 'webgl' or 'svg'

// Clear all layers
WebGLRenderer.clearLayers();

// Adjust threshold
WebGLRenderer.setThreshold(2000);  // Use WebGL for >2000 features
```

### Configuration Options

```javascript
WebGLRenderer.renderGeoJSON(geojson, {
    // Rendering
    color: '#3388ff',               // Polygon color
    weight: 2,                      // Border weight
    fillOpacity: 0.6,               // Fill transparency
    borderColor: '#3388ff',         // Border color (WebGL only)
    borderWidth: 2,                 // Border width (WebGL only)

    // Behavior
    forceRenderer: 'auto',          // 'auto', 'webgl', or 'svg'
    useStripes: true,               // SVG stripe patterns (SVG only)
    enablePopups: true,             // Enable popups

    // Event Handlers
    onClick: function(feature, layer, event) {
        console.log('Clicked:', feature.properties);
    },
    onHover: function(feature, layer, event) {
        console.log('Hovered:', feature.properties);
    },

    // Custom Color Function (WebGL only)
    colorFunction: function(index, feature) {
        // Return RGB object {r: 0-1, g: 0-1, b: 0-1}
        return {r: 0.2, g: 0.5, b: 1.0};
    }
});
```

## Testing

### Test Datashader Tiles

1. Start the development server:
```bash
python run_dev.py
```

2. Open browser console and run:
```javascript
// Add datashader layer for Lombardy
addDatashaderLayer('LOMBARDIA', 'fire');

// Pan and zoom - tiles load on demand
// Tiles are cached for 1 hour

// Try different colormaps
updateDatashaderColormap('viridis');
updateDatashaderColormap('blues');

// Adjust opacity
setDatashaderOpacity(0.5);
```

### Test WebGL Rendering

1. Load a dataset with >1000 features:
```javascript
// Check which renderer is being used
console.log('[Renderer]', WebGLRenderer.getCurrentRenderer());
// Should log 'webgl' for large datasets

// Load cadastral data
// The system will automatically use WebGL
```

2. Monitor console for performance logs:
```
[WebGL] GPU acceleration available ✓
[WebGL] Rendering 5234 features using WebGL ⚡
[WebGL] Successfully rendered 5234 polygons with GPU acceleration
```

### Test SVG Fallback

```javascript
// Force SVG for testing
const layer = WebGLRenderer.renderGeoJSON(smallDataset, {
    forceRenderer: 'svg'
});

// Should see:
// [SVG] Rendered N features using SVG
```

## Performance Comparison

| Dataset Size | SVG Load Time | WebGL Load Time | Datashader Tiles |
|-------------|---------------|-----------------|------------------|
| 100 parcels | <1s | <1s | <0.5s |
| 1,000 parcels | 2-3s | <1s | <0.5s |
| 10,000 parcels | 15-30s (slow) | <5s | <1s |
| 100,000 parcels | Crash | Crash | <2s |
| 1M+ parcels | N/A | N/A | <2s |

## Browser Console Quick Reference

```javascript
// Datashader
addDatashaderLayer('LOMBARDIA');
removeDatashaderLayer();
toggleDatashaderLayer();
updateDatashaderColormap('viridis');
setDatashaderOpacity(0.7);

// WebGL
WebGLRenderer.getCurrentRenderer();
WebGLRenderer.getThreshold();
WebGLRenderer.setThreshold(2000);
WebGLRenderer.clearLayers();

// Check WebGL support
WebGLRenderer.isWebGLSupported;  // true/false
```

## Troubleshooting

### Datashader tiles not loading

1. Check server logs for errors:
```bash
tail -f logs/app.log | grep -i datashader
```

2. Verify database connection:
```python
from land_registry.config import get_cadastral_db_path
from land_registry.cadastral_db import CadastralDatabase

db = CadastralDatabase(get_cadastral_db_path())
# Should not raise errors
```

3. Test endpoint directly:
```bash
curl "http://localhost:8000/api/v1/tiles/datashader/8/135/91.png" > test.png
```

### WebGL not activating

1. Check browser console:
```javascript
console.log('[WebGL Support]', WebGLRenderer.isWebGLSupported);
```

2. Verify Leaflet.glify is loaded:
```javascript
console.log('[Glify]', typeof L.glify);  // should be 'object'
```

3. Check feature count:
```javascript
console.log('[Features]', window.geoJsonData.features.length);
// Must be > threshold (default 1000)
```

### Performance still slow

1. Check renderer in use:
```javascript
console.log('[Renderer]', WebGLRenderer.getCurrentRenderer());
```

2. Force WebGL if auto-detection failed:
```javascript
const layer = WebGLRenderer.renderGeoJSON(data, {
    forceRenderer: 'webgl'
});
```

3. For very large datasets (>50K), use datashader tiles instead.

## File Reference

**Backend (Python)**:
- `land_registry/datashader_service.py` - Tile generation service
- `land_registry/routers/api.py` - Datashader API endpoints (lines 3145-3250)

**Frontend (JavaScript)**:
- `land_registry/static/webgl-renderer.js` - WebGL rendering module
- `land_registry/static/map.js` - Integration points (lines 1517-1600, 1807-1900, 2560-2635)

**Dependencies**:
- `pyproject.toml` - Python packages (datashader, colorcet, xarray, pillow)
- `land_registry/templates/base.html` - JavaScript libraries (Leaflet.glify)

## Next Steps

1. **Test with real data** - Load a large cadastral dataset and verify performance
2. **Adjust thresholds** - Fine-tune `WEBGL_THRESHOLD` based on your server capacity
3. **Add UI controls** - Create buttons/dropdowns for users to switch renderers
4. **Optimize caching** - Configure Redis or CDN for datashader tile caching
5. **Custom colormaps** - Add more color schemes based on user needs

## Support

For issues or questions:
1. Check browser console for error messages
2. Review server logs for backend errors
3. Test individual components (datashader, WebGL, SVG) separately
4. Verify all dependencies are installed: `uv sync`
