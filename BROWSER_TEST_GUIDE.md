# Browser Testing Guide - Datashader + WebGL Visualization

## Quick Start

### 1. Start the Development Server

```bash
python run_dev.py
```

Or:
```bash
uv run uvicorn land_registry.app:app --reload --host 127.0.0.1 --port 8000
```

### 2. Open Your Browser

Navigate to: http://localhost:8000

## Test 1: Datashader Tiles (Server-Side Rendering)

### Open Browser Console (F12) and run:

```javascript
// Test 1: Add datashader tile layer for a region
addDatashaderLayer('LOMBARDIA', 'fire', 0.7);
```

**What to expect:**
- Tiles should start loading as you pan/zoom
- URL pattern: `/api/v1/tiles/datashader/{z}/{x}/{y}.png?colormap=fire&region=LOMBARDIA`
- Check Network tab - should see PNG tiles being loaded
- Density heatmap visualization with logarithmic scaling

**Try different colormaps:**
```javascript
updateDatashaderColormap('viridis');  // Blue-green-yellow
updateDatashaderColormap('blues');    // Blue gradient
updateDatashaderColormap('fire');     // Red-yellow gradient
```

**Adjust opacity:**
```javascript
setDatashaderOpacity(0.5);  // 50% transparent
setDatashaderOpacity(1.0);  // Fully opaque
```

**Remove layer:**
```javascript
removeDatashaderLayer();
```

### Test Heatmap API Directly

Open in new tab:
```
http://localhost:8000/api/v1/datashader/heatmap/LOMBARDIA?width=1200&height=800&colormap=fire
```

Should show a full-region density heatmap PNG image.

## Test 2: WebGL Renderer (Client-Side GPU)

### Load Cadastral Data

Use the interface to load some cadastral data:
1. Go to **Selection** tab
2. Select a region (e.g., LOMBARDIA)
3. Select a province
4. Select a municipality
5. Click "Load Selected Files"

### Check Console Output

You should see logs like:
```
[WebGL] GPU acceleration available ✓
[loadGeoJsonData] Loading 2543 features
[WebGL] Rendering 2543 features using WebGL ⚡
[WebGL] Successfully rendered 2543 polygons with GPU acceleration
```

**For small datasets (<1000 features):**
```
[WebGL] Rendering 234 features using SVG 🎨
[SVG] Rendered 234 features using SVG
```

### Force WebGL Mode

```javascript
// Get current renderer
console.log(WebGLRenderer.getCurrentRenderer());  // 'webgl' or 'svg'

// Check WebGL support
console.log(WebGLRenderer.isWebGLSupported);  // true/false

// Check threshold
console.log(WebGLRenderer.getThreshold());  // 1000 (default)

// Adjust threshold
WebGLRenderer.setThreshold(500);  // Use WebGL for >500 features
```

### Manual Rendering Test

```javascript
// Create test GeoJSON
const testGeoJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [9.0, 45.0], [9.5, 45.0], [9.5, 45.5], [9.0, 45.5], [9.0, 45.0]
                ]]
            },
            "properties": {"name": "Test Polygon"}
        }
    ]
};

// Force WebGL
const layer = WebGLRenderer.renderGeoJSON(testGeoJSON, {
    forceRenderer: 'webgl',
    color: '#ff6b6b',
    fillOpacity: 0.6
});

if (layer) {
    layer.addTo(map);
    console.log('WebGL layer added!');
}

// Force SVG
const layer2 = WebGLRenderer.renderGeoJSON(testGeoJSON, {
    forceRenderer: 'svg',
    color: '#4ecdc4',
    fillOpacity: 0.4
});

if (layer2) {
    layer2.addTo(map);
    console.log('SVG layer added!');
}
```

## Test 3: Performance Comparison

### Measure Load Time

```javascript
// Clear existing data
if (window.currentGeoJsonLayer) {
    map.removeLayer(window.currentGeoJsonLayer);
}

// Load data and measure
console.time('Load Time');
// ... load your data using the UI ...
// Watch console for:
console.timeEnd('Load Time');
```

### Monitor FPS (Frames Per Second)

1. Open Chrome DevTools
2. Go to **Performance** tab
3. Click **Record** (circle button)
4. Pan and zoom the map for 10 seconds
5. Stop recording
6. Check FPS in the timeline:
   - **WebGL mode**: Should be 30-60 FPS even with 10K+ parcels
   - **SVG mode**: May drop below 20 FPS with large datasets

### Memory Usage

1. Open Chrome DevTools
2. Go to **Memory** tab
3. Take a heap snapshot before loading data
4. Load large dataset
5. Take another snapshot
6. Compare:
   - **WebGL**: Typically <500MB for 10K parcels
   - **Datashader tiles**: <100MB (only PNG images)

## Test 4: Hybrid Mode (Auto-Switching)

### Scenario: Large Dataset

1. Load a large dataset (5000+ parcels)
2. Check console - should use **WebGL**
3. Zoom out very far
4. Optionally switch to datashader tiles for overview:
   ```javascript
   addDatashaderLayer('LOMBARDIA');
   ```

### Scenario: Small Dataset

1. Load a small dataset (100-500 parcels)
2. Check console - should use **SVG**
3. Visual quality should be excellent (stripe patterns visible)

## Test 5: Browser Compatibility

### Test WebGL Detection

```javascript
// Check if WebGL is available
if (WebGLRenderer.isWebGLSupported) {
    console.log('✓ WebGL supported - will use GPU acceleration');
} else {
    console.log('✗ WebGL not supported - will use SVG fallback');
}

// Check Leaflet.glify
if (typeof L.glify !== 'undefined') {
    console.log('✓ Leaflet.glify loaded');
} else {
    console.log('✗ Leaflet.glify not loaded');
}
```

### Test in Different Browsers

1. **Chrome/Edge**: Full WebGL support expected
2. **Firefox**: Full WebGL support expected
3. **Safari**: WebGL support expected (may have minor differences)
4. **Mobile Safari (iOS)**: Limited WebGL, should gracefully fallback to SVG
5. **Mobile Chrome (Android)**: Should work with WebGL

## Expected Console Messages

### Successful WebGL Rendering

```
[WebGL] webgl-renderer.js loaded successfully
[WebGL] GPU acceleration available ✓
[loadGeoJsonData] Loading 5234 features
[WebGL] Rendering 5234 features using WebGL ⚡
[WebGL] Successfully rendered 5234 polygons with GPU acceleration
```

### SVG Fallback (Small Dataset)

```
[WebGL] GPU acceleration available ✓
[loadGeoJsonData] Loading 234 features
[WebGL] Rendering 234 features using SVG 🎨
[SVG] Rendered 234 features using SVG
```

### Datashader Tiles

```
[Datashader] Added tile layer for region: LOMBARDIA with colormap: fire
```

## Troubleshooting

### Issue: Tiles Not Loading

**Check Network Tab:**
- Look for `/api/v1/tiles/datashader/` requests
- Should return 200 OK with PNG images
- If 500 error, check server logs

**Solution:**
```bash
# Check server logs
tail -f logs/app.log | grep -i datashader

# Test endpoint directly
curl "http://localhost:8000/api/v1/tiles/datashader/8/135/91.png" > test.png
open test.png  # macOS
xdg-open test.png  # Linux
```

### Issue: WebGL Not Activating

**Check console:**
```javascript
console.log('WebGL Support:', WebGLRenderer.isWebGLSupported);
console.log('Current Renderer:', WebGLRenderer.getCurrentRenderer());
console.log('Feature Count:', window.geoJsonData?.features?.length);
```

**Force WebGL:**
```javascript
WebGLRenderer.setThreshold(0);  // Always use WebGL
// Then reload data
```

### Issue: Poor Performance

**Check which renderer is active:**
```javascript
console.log(WebGLRenderer.getCurrentRenderer());
```

**If using SVG for large dataset:**
```javascript
// Force WebGL
const layer = WebGLRenderer.renderGeoJSON(window.geoJsonData, {
    forceRenderer: 'webgl'
});
```

**If dataset is > 50K parcels:**
Use datashader tiles instead:
```javascript
addDatashaderLayer('REGION_NAME');
```

## Success Criteria

✅ **Datashader Tiles:**
- Tiles load on pan/zoom
- Density visualization appears
- Network tab shows PNG tile requests
- Different colormaps work

✅ **WebGL Rendering:**
- Large datasets (>1000 parcels) use WebGL
- Console shows "WebGL ⚡" message
- Smooth pan/zoom at 30+ FPS
- Click interactions work

✅ **SVG Fallback:**
- Small datasets (<1000 parcels) use SVG
- Stripe patterns visible
- Popups work correctly
- High visual quality

✅ **Hybrid System:**
- Auto-switching based on feature count
- No errors in console
- All existing features work (selection, popups, etc.)

## Performance Benchmarks

| Test | Expected Result |
|------|-----------------|
| Load 1K parcels (SVG) | <1 second |
| Load 1K parcels (WebGL) | <1 second |
| Load 10K parcels (WebGL) | <5 seconds |
| Pan/zoom 10K parcels (WebGL) | 30-60 FPS |
| Datashader tiles | <0.5s per tile |
| Memory usage (10K parcels WebGL) | <500MB |
| Memory usage (datashader tiles) | <100MB |

## Next Steps

1. ✅ Verify all tests pass in browser
2. 📊 Test with real cadastral data
3. 🎨 Add UI controls for renderer selection
4. ⚙️ Fine-tune thresholds based on your data
5. 🚀 Deploy to production

Enjoy your high-performance cadastral visualization! 🎉
