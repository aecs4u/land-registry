/* Direct cadastral map experience.
 *
 * This page deliberately owns one MapLibre instance.  It consumes the
 * allow-listed map catalog and parcel/enrichment APIs; uploaded-file drawing
 * workflows remain on /map-legacy until their migration is complete.
 */
(function () {
  'use strict';

  const ITALY_BOUNDS = [[6.5, 36.3], [18.6, 47.2]];
  const DEFAULT_CENTER = [12.4964, 41.9028];
  const DEFAULT_ZOOM = 5.7;
  // Individual parcel outlines are too dense for a raster fallback below
  // this zoom; the canonical MVT layer retains its server-provided threshold.
  const PARCEL_MIN_ZOOM = 16;
  const ADMIN_SUBSTITUTE_LAYER_IDS = ['geo-boundaries', 'municipality-profiles'];
  const SEARCH_DEBOUNCE_MS = 250;
  const LAST_VIEW_KEY = 'land-registry.map.lastView';
  const BASEMAPS = ['light', 'dark', 'satellite'];
  const state = {
    map: null,
    fallbackMap: null,
    fallbackBasemap: null,
    fallbackLayers: new Map(),
    catalog: [],
    selectedReference: null,
    selectedFeature: null,
    savedReference: null,
    searchTimer: null,
    searchRequest: 0,
    searchController: null,
    searchActiveIndex: -1,
    initializing: false,
    layerFailures: new Set(),
    activeLayers: null,
    adminSubstituteAutoLayers: new Set(),
    shortlistItems: [],
    shortlistStatuses: [],
    shortlistSummary: null,
    dark: false,
    preferences: null,
    defaultLayers: new Set(['cadastral-parcels']),
    resizeObserver: null,
    statusTimer: null,
    tileErrors: new Set(),
  };

  const $ = (id) => document.getElementById(id);

  function syncMapNavigationAccessibility() {
    const lang = document.documentElement.lang.toLowerCase();
    if (lang.startsWith('en')) {
      document.querySelectorAll('[aria-label^="Seleziona lingua"]').forEach((control) => {
        const label = control.getAttribute('aria-label') || '';
        control.setAttribute('aria-label', label.replace(/^Seleziona lingua/, 'Select language'));
        const title = control.getAttribute('title') || '';
        if (title.startsWith('Seleziona lingua')) control.setAttribute('title', title.replace(/^Seleziona lingua/, 'Select language'));
      });
    }

    const toggle = document.querySelector('#sidebarToggle, [aria-controls="app-sidebar"]');
    const sidebar = document.querySelector('#app-sidebar, .sidebar');
    if (!toggle || !sidebar) return;
    const mobileOffscreen = window.matchMedia('(max-width: 700px)').matches
      && sidebar.getBoundingClientRect().right <= 0;
    const hidden = document.body.classList.contains('sidebar-hidden')
      || document.body.classList.contains('sidebar-collapsed')
      || sidebar.classList.contains('collapsed')
      || mobileOffscreen;
    toggle.setAttribute('aria-expanded', String(!hidden));
  }

  function observeMapNavigationAccessibility() {
    const fixLanguageLabel = () => {
      if (!document.documentElement.lang.toLowerCase().startsWith('en')) return false;
      const controls = document.querySelectorAll('[aria-label^="Seleziona lingua"]');
      controls.forEach((control) => {
        const label = control.getAttribute('aria-label') || '';
        control.setAttribute('aria-label', label.replace(/^Seleziona lingua/, 'Select language'));
        const title = control.getAttribute('title') || '';
        if (title.startsWith('Seleziona lingua')) control.setAttribute('title', title.replace(/^Seleziona lingua/, 'Select language'));
      });
      return controls.length > 0;
    };
    if (!fixLanguageLabel()) {
      const languageObserver = new MutationObserver(() => {
        if (fixLanguageLabel()) languageObserver.disconnect();
      });
      languageObserver.observe(document.body, { childList: true, subtree: true });
    }

    const toggle = document.querySelector('#sidebarToggle, [aria-controls="app-sidebar"]');
    const sidebar = document.querySelector('#app-sidebar, .sidebar');
    syncMapNavigationAccessibility();
    const sidebarObserver = new MutationObserver(syncMapNavigationAccessibility);
    sidebarObserver.observe(document.body, { attributes: true, attributeFilter: ['class'] });
    if (sidebar) sidebarObserver.observe(sidebar, { attributes: true, attributeFilter: ['class', 'style'] });
    window.addEventListener('resize', syncMapNavigationAccessibility);
    toggle?.addEventListener('click', () => requestAnimationFrame(syncMapNavigationAccessibility));
  }

  const mapStatus = (message, error = false) => {
    const element = $('mapStatus');
    if (!element) return;
    clearTimeout(state.statusTimer);
    element.textContent = message || '';
    element.classList.toggle('is-error', error);
    if (message && !error) {
      state.statusTimer = setTimeout(() => { if (element.textContent === message) element.textContent = ''; }, 4000);
    }
  };

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>'"]/g, (char) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
    })[char]);
  }

  function formatArea(value) {
    const area = Number(value);
    return Number.isFinite(area) ? `${area.toLocaleString('it-IT', { maximumFractionDigits: 1 })} m²` : '—';
  }

  function urlState() {
    const params = new URLSearchParams(window.location.search);
    const lat = Number(params.get('lat'));
    const lng = Number(params.get('lng'));
    const zoom = Number(params.get('zoom'));
    const hasView = Number.isFinite(lat) && Number.isFinite(lng)
      && lat >= ITALY_BOUNDS[0][1] && lat <= ITALY_BOUNDS[1][1]
      && lng >= ITALY_BOUNDS[0][0] && lng <= ITALY_BOUNDS[1][0];
    return {
      center: hasView ? [lng, lat] : DEFAULT_CENTER,
      zoom: Number.isFinite(zoom) ? Math.min(22, Math.max(5, zoom)) : DEFAULT_ZOOM,
      parcel: params.get('parcel') || null,
      invalidParcel: Boolean(params.get('parcel') && !(/^[A-Z]\d{3}[A-Z]?\d{4}\d{2}\.\w+$/i.test(params.get('parcel')) || /^[A-Z]\d{3}[_-].+\..+$/.test(params.get('parcel')))),
      parcelId: /^\d+$/.test(params.get('parcel_id') || '') ? Number(params.get('parcel_id')) : null,
      invalidView: params.has('lat') && params.has('lng') && Number.isFinite(lat) && Number.isFinite(lng) && !hasView,
      hasView,
    };
  }

  function mapHasUsableSize() {
    const element = $('directMap');
    return Boolean(element && element.clientWidth > 0 && element.clientHeight > 0);
  }

  function writeUrl() {
    const map = state.map || state.fallbackMap;
    if (!map || !mapHasUsableSize()) return;
    const center = map.getCenter();
    if (!Number.isFinite(center.lat) || !Number.isFinite(center.lng)) return;
    const params = new URLSearchParams(window.location.search);
    params.set('lat', center.lat.toFixed(6));
    params.set('lng', center.lng.toFixed(6));
    params.set('zoom', map.getZoom().toFixed(2));
    if (state.selectedReference) {
      params.set('parcel', state.selectedReference);
      if (state.selectedFeature?.id != null) params.set('parcel_id', String(state.selectedFeature.id));
      else params.delete('parcel_id');
    } else { params.delete('parcel'); params.delete('parcel_id'); }
    window.history.replaceState(null, '', `${window.location.pathname}?${params.toString()}`);
  }

  function currentBasemap() {
    return document.querySelector('input[name="basemap"]:checked')?.value || (state.dark ? 'dark' : 'light');
  }

  // CARTO now watermarks keyless tiles ("API key required"), so light and
  // dark use Esri's keyless canvas basemaps from the host that already serves
  // satellite imagery. Canvas tiles stop at z16 — background is the flat
  // color shown past maxzoom instead of MapLibre upscaling (blurring) the
  // last real tile; see styleForBasemap().
  const ESRI_TILES = 'https://server.arcgisonline.com/ArcGIS/rest/services';
  const BASEMAP_TILES = {
    light: {
      base: `${ESRI_TILES}/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}`,
      labels: `${ESRI_TILES}/Canvas/World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}`,
      maxzoom: 16,
      displayMaxzoom: 19,
      background: '#f2f2ef',
      attribution: '© Esri, HERE, Garmin, © OpenStreetMap contributors',
    },
    dark: {
      base: `${ESRI_TILES}/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}`,
      labels: `${ESRI_TILES}/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}`,
      maxzoom: 16,
      displayMaxzoom: 19,
      background: '#1a1a1a',
      attribution: '© Esri, HERE, Garmin, © OpenStreetMap contributors',
    },
    satellite: {
      base: `${ESRI_TILES}/World_Imagery/MapServer/tile/{z}/{y}/{x}`,
      labels: null,
      maxzoom: 19,
      background: '#3d3d3d',
      attribution: '© Esri, Maxar, Earthstar Geographics',
    },
  };

  // Same gate as the legacy map (map.js): CARTO only when an API key is
  // configured; window.cartoEnabled/cartoApiKey come from the template.
  function cartoTiles(kind) {
    if (kind === 'satellite' || !(window.cartoEnabled && window.cartoApiKey)) return null;
    const style = kind === 'dark' ? 'dark_all' : 'light_all';
    return {
      base: `https://cartodb-basemaps-a.global.ssl.fastly.net/${style}/{z}/{x}/{y}.png?api_key=${encodeURIComponent(window.cartoApiKey)}`,
      labels: null,
      maxzoom: 20,
      background: kind === 'dark' ? '#1a1a1a' : '#f2f2ef',
      attribution: '© OpenStreetMap contributors © CARTO',
    };
  }

  function styleForBasemap(kind) {
    const tiles = cartoTiles(kind) || BASEMAP_TILES[kind] || BASEMAP_TILES.light;
    const sources = {
      basemap: { type: 'raster', tiles: [tiles.base], tileSize: 256, maxzoom: tiles.maxzoom, attribution: tiles.attribution },
    };
    // A layer's own maxzoom (unlike the source's, which only governs tile
    // fetching) hides the layer entirely at/above that zoom, instead of
    // letting MapLibre upscale the last real tile into a blurry mess. One
    // zoom level of headroom past the source's native max keeps the mildly
    // scaled (still legible) tile up to z+1, then drops to the flat
    // `background` layer once the scale-up gets bad.
    const rasterCutoff = tiles.displayMaxzoom || tiles.maxzoom + 1;
    const layers = [
      { id: 'basemap-background', type: 'background', paint: { 'background-color': tiles.background } },
      { id: 'basemap', type: 'raster', source: 'basemap', maxzoom: rasterCutoff },
    ];
    if (tiles.labels) {
      sources['basemap-labels'] = { type: 'raster', tiles: [tiles.labels], tileSize: 256, maxzoom: tiles.maxzoom };
      layers.push({ id: 'basemap-labels', type: 'raster', source: 'basemap-labels', maxzoom: rasterCutoff });
    }
    // Symbol layers (parcel numbers) need glyphs; they are self-hosted under
    // /static/fonts (Noto Sans, OFL) so no third-party font host is involved.
    return { version: 8, glyphs: absoluteTileUrl('/static/fonts/{fontstack}/{range}.pbf'), sources, layers };
  }

  function layerVisibleAtZoom(layer) {
    return state.map && state.map.getZoom() >= Number(layer.min_zoom || 0);
  }

  function parcelMinZoom() {
    const layer = state.catalog.find((candidate) => candidate.id === 'cadastral-parcels');
    const zoom = Number(layer?.min_zoom);
    return Number.isFinite(zoom) ? zoom : PARCEL_MIN_ZOOM;
  }

  function isAdministrativeSubstitute(layer) {
    return layer?.role === 'admin-substitute' || ADMIN_SUBSTITUTE_LAYER_IDS.includes(layer?.id);
  }

  function administrativeSubstituteLayer() {
    return state.catalog.find(isAdministrativeSubstitute) || null;
  }

  function isPolygonLayer(layer) {
    return layer.kind !== 'point';
  }

  function layerColor(layerId) {
    const colors = {
      'geo-boundaries': '#526b84',
      'municipality-profiles': '#4f6f86',
      'cadastral-sheets': '#1976a8',
      'cadastral-parcels': '#d97925',
      'market-zones': '#7b61a8',
      'hazard-areas': '#c44444',
      'census-sections': '#3f8f73',
      'points-of-interest': '#b04a9b',
    };
    return colors[layerId] || '#47758f';
  }

  function layerMetadata(layer) {
    const coverage = layer.coverage === 'partial'
      ? `Partial coverage${layer.coverage_note ? ` · ${layer.coverage_note}` : ''}`
      : `From zoom ${layer.min_zoom || 0}`;
    const freshness = (layer.properties || []).includes('source_release')
      ? 'Date reported per feature'
      : 'Update date not reported';
    return `${coverage} · ${freshness} · ${layer.source || 'Source not reported'}`;
  }

  function formatConfidence(value) {
    if (value === null || value === undefined || value === '') return '';
    const number = Number(value);
    if (!Number.isFinite(number)) return String(value);
    const percent = number <= 1 ? number * 100 : number;
    return `${percent.toLocaleString('it-IT', { maximumFractionDigits: 1 })}%`;
  }

  function blockMetadataHtml(block) {
    if (!block) return '';
    const chips = [];
    const confidence = formatConfidence(block.confidence);
    if (confidence) chips.push(['Confidence', confidence]);
    if (block.spatial_resolution) chips.push(['Resolution', block.spatial_resolution]);
    if (block.spatial_resolution_m != null) chips.push(['Resolution', `${Number(block.spatial_resolution_m).toLocaleString('it-IT', { maximumFractionDigits: 0 })} m`]);
    const provenance = [
      block.dataset_version ? `Dataset: ${block.dataset_version}` : '',
      block.model_version ? `Model: ${block.model_version}` : '',
      block.match_method ? `Match: ${block.match_method}` : '',
    ].filter(Boolean).join(' · ');
    const benchmarkRows = Object.values(block.benchmarks || {}).filter((benchmark) => benchmark && benchmark.value !== null && benchmark.value !== undefined);
    if (!chips.length && !benchmarkRows.length && !block.source && !provenance) return '';
    return `
      ${chips.length ? `<div class="parcel-block-chips">${chips.map(([label, value]) => `<span><strong>${escapeHtml(label)}</strong> ${escapeHtml(value)}</span>`).join('')}</div>` : ''}
      ${benchmarkRows.length ? `<div class="parcel-block-benchmarks">${benchmarkRows.map((benchmark) => `<span>Benchmark ${escapeHtml([benchmark.label || '', benchmark.year || ''].filter(Boolean).join(' '))}: <strong>${escapeHtml(benchmark.value)} ${escapeHtml(benchmark.unit || '')}</strong></span>`).join('')}</div>` : ''}
      <p class="parcel-block-provenance">${escapeHtml(block.source || '')}${block.source && provenance ? '<br>' : ''}${escapeHtml(provenance)}</p>`;
  }

  function layerSourceId(layer) { return `source-${layer.id}`; }

  // MapLibre fetches tiles from blob: workers, where a root-relative URL
  // cannot resolve and no request is ever sent.  Concatenate rather than use
  // new URL(), which would percent-encode the {z}/{x}/{y} placeholders.
  function absoluteTileUrl(url) {
    return url.startsWith('/') ? `${window.location.origin}${url}` : url;
  }

  function addCatalogLayer(layer) {
    if (!layer || layer.id === 'raster-coverage') return;
    if (!state.map && state.fallbackMap) {
      addFallbackCatalogLayer(layer);
      return;
    }
    if (!state.map || !layer.tile_url) return;
    try {
      const sourceId = layerSourceId(layer);
      if (state.map.getSource(sourceId)) return;
      state.map.addSource(sourceId, {
        type: 'vector',
        tiles: [absoluteTileUrl(layer.tile_url)],
        minzoom: Number(layer.min_zoom || 0),
        maxzoom: 22,
      });
      const color = layerColor(layer.id);
      const isAdminSubstitute = isAdministrativeSubstitute(layer);
      if (isPolygonLayer(layer)) {
        state.map.addLayer({ id: `fill-${layer.id}`, type: 'fill', source: sourceId, 'source-layer': layer.id,
          minzoom: Number(layer.min_zoom || 0), paint: { 'fill-color': color, 'fill-opacity': layer.id === 'cadastral-parcels' ? 0.04 : (isAdminSubstitute ? 0.075 : 0.11) } });
        state.map.addLayer({ id: `line-${layer.id}`, type: 'line', source: sourceId, 'source-layer': layer.id,
          minzoom: Number(layer.min_zoom || 0), paint: { 'line-color': color, 'line-width': layer.id === 'cadastral-parcels' ? 0.8 : (isAdminSubstitute ? 1.1 : 1.4), 'line-opacity': isAdminSubstitute ? 0.62 : 0.8 } });
        if (layer.id === 'geo-boundaries') {
          const boundaryLevel = () => state.map.getZoom() >= 10 ? 'municipality' : (state.map.getZoom() >= 8 ? 'province' : 'region');
          const applyBoundaryLevel = () => {
            const filter = ['==', ['get', 'unit_type'], boundaryLevel()];
            state.map.setFilter('fill-geo-boundaries', filter);
            state.map.setFilter('line-geo-boundaries', filter);
          };
          applyBoundaryLevel();
          state.map.on('zoomend', applyBoundaryLevel);
        }
      } else {
        state.map.addLayer({ id: `point-${layer.id}`, type: 'circle', source: sourceId, 'source-layer': layer.id,
          minzoom: Number(layer.min_zoom || 0), paint: { 'circle-color': color, 'circle-radius': 4, 'circle-stroke-color': '#fff', 'circle-stroke-width': 1 } });
      }
      if (layer.id === 'cadastral-parcels') {
        addParcelLabels();
        state.map.on('mouseenter', 'fill-cadastral-parcels', () => { state.map.getCanvas().style.cursor = 'pointer'; });
        state.map.on('mouseleave', 'fill-cadastral-parcels', () => { state.map.getCanvas().style.cursor = ''; });
        let hoverPopup = null;
        let hoverKey = null;
        state.map.on('mousemove', 'fill-cadastral-parcels', (event) => {
          const feature = event.features?.[0];
          const props = feature?.properties || {};
          const parcel = props.parcel || props.particella || props.LABEL || props.canonical_reference;
          if (!parcel) return;
          const key = `${parcel}:${event.lngLat.lng.toFixed(5)}:${event.lngLat.lat.toFixed(5)}`;
          if (hoverKey === key) return;
          hoverKey = key;
          if (hoverPopup) hoverPopup.remove();
          hoverPopup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 8 })
            .setLngLat(event.lngLat)
            .setHTML(`<strong>Parcel ${escapeHtml(parcel)}</strong><br><small>${escapeHtml(props.municipality_name || props.municipality || props.municipality_id || '')}</small>`)
            .addTo(state.map);
        });
        state.map.on('mouseleave', 'fill-cadastral-parcels', () => {
          hoverKey = null;
          if (hoverPopup) { hoverPopup.remove(); hoverPopup = null; }
        });
      }
      reorderMapLayers();
    } catch (error) {
      // A malformed or unavailable optional layer must not prevent the rest
      // of the catalog from being displayed (or hide the basemap).
      console.warn(`Map layer ${layer.id} could not be added`, error);
      handleLayerFailure(layer);
    }
  }

  function addParcelLabels() {
    if (state.preferences?.parcel_labels === false) return;
    if (state.map.getLayer('label-cadastral-parcels')) return;
    state.map.addLayer({
      id: 'label-cadastral-parcels', type: 'symbol', source: layerSourceId({ id: 'cadastral-parcels' }), 'source-layer': 'cadastral-parcels',
      minzoom: 17, filter: ['all', ['!=', ['slice', ['to-string', ['coalesce', ['get', 'parcel'], '']], 0, 6], 'STRADA'], ['!=', ['slice', ['to-string', ['coalesce', ['get', 'parcel'], '']], 0, 5], 'ACQUA']], layout: { 'text-field': ['coalesce', ['get', 'parcel'], ['get', 'canonical_reference'], ''], 'text-font': ['noto-sans-regular'], 'text-size': 11, 'text-allow-overlap': false, 'text-padding': 2 },
      paint: { 'text-color': '#263b4d', 'text-halo-color': '#fff', 'text-halo-width': 1.5 },
    });
  }

  function handleLayerFailure(layer) {
    if (state.layerFailures.has(layer.id)) return;
    state.layerFailures.add(layer.id);
    const row = document.querySelector(`[data-layer-id="${CSS.escape(layer.id)}"]`);
    if (row) row.querySelector('.map-layer-meta').textContent = 'Unavailable';
    if (layer.id === 'cadastral-parcels') {
      addParcelRasterFallback();
      mapStatus('Vector parcel layer unavailable; using boundary fallback.', true);
    }
  }

  function addParcelRasterFallback() {
    if (state.map.getSource('parcel-raster-fallback')) return;
    state.map.addSource('parcel-raster-fallback', { type: 'raster', tiles: [absoluteTileUrl('/api/v1/tiles/cadastral-boundaries/{z}/{x}/{y}.png?layer=ple')], tileSize: 512, minzoom: PARCEL_MIN_ZOOM, maxzoom: 22 });
    state.map.addLayer({ id: 'parcel-raster-fallback', type: 'raster', source: 'parcel-raster-fallback', layout: { visibility: 'visible' }, paint: { 'raster-opacity': 0.85 } });
  }

  function renderLayerCatalog() {
    const list = $('mapLayerList');
    if (!list) return;
    list.replaceChildren();
    const initializingLayers = state.activeLayers === null;
    state.catalog.filter((layer) => layer.id !== 'raster-coverage').forEach((layer) => {
      const row = document.createElement('label');
      row.className = 'map-layer-row';
      row.dataset.layerId = layer.id;
      const input = document.createElement('input');
      input.type = 'checkbox';
      const defaults = state.defaultLayers.has(layer.id);
      input.checked = initializingLayers ? defaults : state.activeLayers.has(layer.id);
      input.disabled = Number(layer.min_zoom || 0) > 22;
      input.addEventListener('change', () => {
        state.adminSubstituteAutoLayers.delete(layer.id);
        if (state.activeLayers === null) state.activeLayers = new Set();
        if (input.checked) state.activeLayers.add(layer.id);
        else state.activeLayers.delete(layer.id);
        if (input.checked && (!state.map || !state.map.getSource(layerSourceId(layer)))) addCatalogLayer(layer);
        setLayerVisibility(layer.id, input.checked);
        reorderMapLayers();
        updateParcelZoomAffordance();
      });
      row.title = layerMetadata(layer);
      const copy = document.createElement('span');
      copy.innerHTML = `<strong><i class="map-layer-swatch" style="background:${layerColor(layer.id)}"></i>${escapeHtml(layer.title)}</strong><small class="map-layer-meta">${escapeHtml(layerMetadata(layer))}</small>`;
      row.append(input, copy);
      list.appendChild(row);
      if (input.checked) {
        if (state.activeLayers === null) state.activeLayers = new Set();
        state.activeLayers.add(layer.id);
        addCatalogLayer(layer);
      }
    });
    reorderMapLayers();
    updateParcelZoomAffordance();
    const partial = state.catalog.filter((layer) => layer.coverage === 'partial');
    if (partial.length) $('mapCoverageStatus').textContent = 'Some layers have partial regional coverage.';
  }

  function addFallbackCatalogLayer(layer) {
    if (!state.fallbackMap || !layer || layer.id === 'raster-coverage' || state.fallbackLayers.has(layer.id)) return;
    try {
      let overlay;
      if (layer.id === 'cadastral-parcels') {
        overlay = L.tileLayer(absoluteTileUrl('/api/v1/tiles/cadastral-boundaries/{z}/{x}/{y}.png?layer=ple'), {
          minZoom: Number(layer.min_zoom || 0), maxZoom: 22, maxNativeZoom: 16,
          attribution: 'Cadastral boundaries',
        });
      } else if (L.vectorGrid?.protobuf && layer.tile_url) {
        const color = layerColor(layer.id);
        const style = { color, weight: 1, opacity: .8, fillColor: color, fillOpacity: .12, radius: 4 };
        overlay = L.vectorGrid.protobuf(absoluteTileUrl(layer.tile_url), {
          vectorTileLayerStyles: { [layer.id]: style },
          maxNativeZoom: 22,
          interactive: false,
        });
      }
      if (!overlay) return;
      overlay.addTo(state.fallbackMap);
      state.fallbackLayers.set(layer.id, overlay);
    } catch (error) {
      console.warn(`Fallback map layer ${layer.id} could not be added`, error);
      handleLayerFailure(layer);
    }
  }

  function syncLayerCheckbox(layerId, checked) {
    const input = document.querySelector(`[data-layer-id="${CSS.escape(layerId)}"] input[type="checkbox"]`);
    if (input) input.checked = checked;
  }

  function enableAdministrativeSubstitute() {
    const layer = administrativeSubstituteLayer();
    if (!layer) {
      mapStatus('Zoom in to see cadastral parcels; administrative boundaries are not configured.', true);
      return;
    }
    if (state.activeLayers === null) state.activeLayers = new Set();
    const alreadyActive = state.activeLayers.has(layer.id);
    if (state.map) {
      if (!state.map.getSource(layerSourceId(layer))) addCatalogLayer(layer);
    } else if (state.fallbackMap && !state.fallbackLayers.has(layer.id)) {
      addFallbackCatalogLayer(layer);
    }
    state.activeLayers.add(layer.id);
    if (!alreadyActive) state.adminSubstituteAutoLayers.add(layer.id);
    setLayerVisibility(layer.id, true);
    syncLayerCheckbox(layer.id, true);
  }

  function releaseAdministrativeSubstitute() {
    state.adminSubstituteAutoLayers.forEach((layerId) => {
      state.activeLayers?.delete(layerId);
      setLayerVisibility(layerId, false);
      syncLayerCheckbox(layerId, false);
    });
    state.adminSubstituteAutoLayers.clear();
  }

  function updateParcelZoomAffordance() {
    const map = state.map || state.fallbackMap;
    if (!map) return;
    const belowParcelZoom = map.getZoom() < parcelMinZoom();
    if (belowParcelZoom) enableAdministrativeSubstitute();
    else releaseAdministrativeSubstitute();
    const panelOpen = !$('directParcelPanel')?.hidden;
    const bounds = map.getBounds?.();
    const parcels = state.catalog.find((layer) => layer.id === 'cadastral-parcels');
    const coverage = parcels?.coverage_bounds;
    const outsideCoverage = Boolean(coverage && bounds && (bounds.getEast() < coverage[0] || bounds.getWest() > coverage[2] || bounds.getNorth() < coverage[1] || bounds.getSouth() > coverage[3]));
    const note = $('mapHelpNote');
    if (!note) return;
    if (outsideCoverage) {
      note.textContent = 'No cadastral parcels published for this area yet (coverage: Veneto).';
    } else if (belowParcelZoom && !administrativeSubstituteLayer()) {
      note.textContent = 'Zoom in to see cadastral parcels; administrative boundaries are not configured.';
    } else {
      note.textContent = 'Zoom in to see cadastral parcels';
    }
    note.hidden = panelOpen || (!outsideCoverage && !belowParcelZoom);
  }

  async function loadLayerHealth() {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);
    try {
      const response = await fetch('/api/v1/map/layers/health', { signal: controller.signal });
      if (!response.ok) return;
      const payload = await response.json();
      (payload.layers || []).forEach((health) => {
        const row = document.querySelector(`[data-layer-id="${CSS.escape(health.id)}"]`);
        const meta = row?.querySelector('.map-layer-meta');
        if (!meta) return;
        const catalogLayer = state.catalog.find((layer) => layer.id === health.id);
        const metadata = catalogLayer ? ` · ${layerMetadata(catalogLayer)}` : '';
        if (payload.available === false) meta.textContent = `Not configured${metadata}`;
        else if (health.available && catalogLayer?.coverage === 'partial') meta.textContent = `Partial coverage · ${catalogLayer.coverage_note || 'some regions'}${metadata}`;
        else if (health.available) meta.textContent = `Available${metadata}`;
        else if (health.relation_exists === false) meta.textContent = `Not available${metadata}`;
        else meta.textContent = `Needs configuration${metadata}`;
      });
    } catch (_) {
      // Health is advisory; the catalog and map remain usable without it.
    } finally { clearTimeout(timeout); }
  }

  function reorderMapLayers() {
    if (!state.map) return;
    const bottomToTop = [
      'fill-geo-boundaries', 'fill-municipality-profiles', 'fill-market-zones', 'fill-postal-zones',
      'fill-hazard-areas', 'fill-census-sections', 'fill-cadastral-sheets', 'fill-urban-sections',
      'fill-cadastral-parcels', 'fill-enrichment-bulletin',
      'line-geo-boundaries', 'line-municipality-profiles', 'line-market-zones', 'line-postal-zones',
      'line-hazard-areas', 'line-census-sections', 'line-cadastral-sheets', 'line-urban-sections', 'line-cadastral-parcels',
      'point-points-of-interest', 'point-hazard-measurements', 'point-mps04-points',
      'point-enrichment-fires', 'point-auction-properties', 'point-sales-properties',
      'label-cadastral-parcels', 'fill-adjacent-parcels', 'line-adjacent-parcels',
      'selected-parcel-fill', 'selected-parcel-line',
    ];
    bottomToTop.forEach((id) => { if (state.map.getLayer(id)) state.map.moveLayer(id); });
  }

  function setLayerVisibility(layerId, visible) {
    if (!state.map && state.fallbackMap) {
      const layer = state.catalog.find((candidate) => candidate.id === layerId);
      const fallbackLayer = state.fallbackLayers.get(layerId);
      if (visible && layer && !fallbackLayer) addFallbackCatalogLayer(layer);
      else if (!visible && fallbackLayer) {
        state.fallbackMap.removeLayer(fallbackLayer);
        state.fallbackLayers.delete(layerId);
      }
      return;
    }
    const value = visible ? 'visible' : 'none';
    ['fill', 'line', 'point', 'label'].forEach((prefix) => {
      const id = `${prefix}-${layerId}`;
      // Skip no-op writes: any layout write marks the style dirty and forces
      // another render.
      if (state.map.getLayer(id) && (state.map.getLayoutProperty(id, 'visibility') ?? 'visible') !== value) {
        state.map.setLayoutProperty(id, 'visibility', value);
      }
    });
  }

  function renderSelectedGeometry(feature) {
    if (!feature?.geometry || !state.map) return;
    const selected = { type: 'Feature', geometry: feature.geometry, properties: {} };
    if (state.map.getSource('selected-parcel')) state.map.getSource('selected-parcel').setData(selected);
    else {
      state.map.addSource('selected-parcel', { type: 'geojson', data: selected });
      state.map.addLayer({ id: 'selected-parcel-fill', type: 'fill', source: 'selected-parcel', paint: { 'fill-color': '#f08a24', 'fill-opacity': .24 } });
      state.map.addLayer({ id: 'selected-parcel-line', type: 'line', source: 'selected-parcel', paint: { 'line-color': '#f08a24', 'line-width': 3, 'line-opacity': 1 } });
    }
  }

  function featureBounds(feature) {
    if (!feature?.geometry || !window.maplibregl) return null;
    const bounds = new maplibregl.LngLatBounds();
    const visit = (value) => {
      if (!Array.isArray(value)) return;
      if (value.length >= 2 && Number.isFinite(Number(value[0])) && Number.isFinite(Number(value[1]))) {
        bounds.extend([Number(value[0]), Number(value[1])]);
        return;
      }
      value.forEach(visit);
    };
    visit(feature.geometry.coordinates);
    return bounds.isEmpty() ? null : bounds;
  }

  function referenceFrom(properties = {}) {
    return properties.national_cadastral_reference || properties.national_cadastralreference || properties.NATIONALCADASTRALREFERENCE || properties.canonical_reference || properties.national_reference || null;
  }

  // Legacy's Table View shows every source column for the current selection
  // (table-manager.js); this is the single-parcel equivalent — every raw
  // property the catalog layer returns, not just the curated summary fields.
  function allAttributesHtml(properties = {}) {
    const rows = Object.entries(properties)
      .filter(([, value]) => value !== null && value !== undefined && typeof value !== 'object')
      .map(([key, value]) => `<tr><th>${escapeHtml(key.replaceAll('_', ' '))}</th><td>${escapeHtml(value)}</td></tr>`)
      .join('');
    if (!rows) return '';
    return `<details class="parcel-all-attributes"><summary>All attributes</summary><table class="parcel-detail-table"><tbody>${rows}</tbody></table></details>`;
  }

  function renderParcelPanel(feature, enrichment = null) {
    const props = feature?.properties || {};
    const reference = state.selectedReference || referenceFrom(props) || 'Selected parcel';
    const municipality = props.municipality_name || props.municipality || props.ADMINISTRATIVEUNIT || props.municipality_id || '—';
    const sheet = props.sheet || props.sheet_number || props.foglio || '—';
    const parcel = props.parcel || props.particella || props.LABEL || '—';
    // computed_area_sqm is the geodesic geometry area, used when the source
    // release did not publish area_sqm (e.g. the Veneto cadastral load).
    const area = props.area_sqm ?? props.area ?? props.area_display ?? props.computed_area_sqm;
    const freshness = props.source_release || enrichment?.read_model?.source_fingerprint || 'Source date not available';
    $('directParcelTitle').textContent = parcel !== '—' ? `Parcel ${parcel}` : 'Parcel details';
    const blocks = enrichment?.blocks || {};
    const blockLabels = { cadastral: 'Cadastral', valuation: 'OMI valuation', economics: 'Economy', demographics: 'Demographics', population: 'Population', buildings: 'Buildings', risk: 'Risks', address: 'Address' };
    const blockHtml = Object.entries(blockLabels).map(([key, label]) => {
      const block = blocks[key];
      if (!block?.available) return '';
      const values = Object.entries(block.data || {}).filter(([, value]) => value !== null && value !== undefined && typeof value !== 'object').slice(0, 5);
      const valueHtml = values.map(([name, value]) => `<dt>${escapeHtml(name.replaceAll('_', ' '))}</dt><dd>${escapeHtml(value)}</dd>`).join('');
      return `<details><summary>${escapeHtml(label)}</summary>${blockMetadataHtml(block)}${valueHtml ? `<dl>${valueHtml}</dl>` : '<p>Available; open the analysis view for full details.</p>'}</details>`;
    }).join('');
    const emptyEnrichment = enrichment?.unavailable
      ? '<p>Optional enrichment is temporarily unavailable.</p>'
      : '<p>No optional enrichment is available for this parcel.</p>';
    const enrichmentHtml = enrichment ? `<div class="parcel-enrichment">${blockHtml || emptyEnrichment}</div>` : '<div class="parcel-enrichment"><p>Loading optional enrichment…</p></div>';
    $('directParcelContent').innerHTML = `
      <div class="parcel-hero"><strong>${escapeHtml(reference)}</strong><span>${escapeHtml(municipality)}</span></div>
      <div class="parcel-kpis"><div class="parcel-kpi"><span>Area</span><strong>${escapeHtml(formatArea(area))}</strong></div><div class="parcel-kpi"><span>Sheet</span><strong>${escapeHtml(sheet)}</strong></div></div>
      <table class="parcel-detail-table"><tbody>
        <tr><th>Parcel</th><td>${escapeHtml(parcel)}</td></tr>
        <tr><th>Municipality</th><td>${escapeHtml(municipality)}</td></tr>
        <tr><th>Reference</th><td>${escapeHtml(reference)}</td></tr>
        <tr><th>Geometry</th><td>${feature?.geometry ? 'Available · WGS84' : 'Unavailable for this record'}</td></tr>
        <tr><th>Data</th><td>${escapeHtml(freshness)}</td></tr>
      </tbody></table>
      ${enrichment?.municipality ? `<p class="map-layer-meta">Municipality profile available.</p>` : ''}
      ${allAttributesHtml(props)}`;
    const parcelQuery = `?parcel=${encodeURIComponent(reference)}&report=1`;
    $('legacyAnalysisLink').href = `/map-legacy?parcel=${encodeURIComponent(reference)}`;
    $('parcelReportLink').href = `/map-legacy${parcelQuery}`;
    const computedArea = props.computed_area_sqm != null && props.area_sqm == null && props.area == null && props.area_display == null;
    if (computedArea) $('directParcelContent').querySelector('.parcel-kpi:first-child span').textContent = 'Area (computed)';
    const saved = state.savedReference === reference;
    $('parcelSaveButton').disabled = saved;
    $('parcelSaveButton').textContent = saved ? 'Saved' : 'Save parcel';
    $('directParcelContent').insertAdjacentHTML('beforeend', enrichmentHtml);
    $('directParcelPanel').hidden = false;
    $('mapHelpNote').hidden = true;
  }

  async function saveParcel() {
    const reference = state.selectedReference;
    if (!reference) return;
    const button = $('parcelSaveButton');
    button.disabled = true;
    button.textContent = 'Saving…';
    try {
      const response = await fetch('/api/v1/saved-parcels', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          source: 'cadastral',
          national_reference: reference,
          label: `Parcel ${reference}`,
          geometry: state.selectedFeature?.geometry || null,
        }),
      });
      if (response.status === 401) throw new Error('Sign in to save parcels');
      if (response.status === 409) {
        state.savedReference = reference;
        button.textContent = 'Already saved';
        mapStatus('Parcel already saved');
        return;
      }
      if (!response.ok) throw new Error('Could not save parcel');
      state.savedReference = reference;
      button.textContent = 'Saved';
      mapStatus('Parcel saved');
      void loadShortlist();
    } catch (error) {
      button.disabled = false;
      button.textContent = 'Save parcel';
      mapStatus(error.message || 'Could not save parcel', true);
    }
  }

  function statusLabel(status) {
    const entry = state.shortlistStatuses.find((item) => String(item.value) === String(status));
    return entry ? entry.label : status || '—';
  }

  function shortlistReference(item) {
    if (!item) return '';
    if (item.national_reference) return item.national_reference;
    const sourceKey = String(item.source_key || '');
    const match = sourceKey.match(/(?:^|\|)REF=([^|]+)/);
    if (match) return decodeURIComponent(match[1]);
    return item.parcel_identity_id || '';
  }

  function populateShortlistStatusFilter() {
    const select = $('shortlistStatusFilter');
    if (!select) return;
    const selected = select.value;
    select.innerHTML = '<option value="">All</option>' + state.shortlistStatuses
      .map((item) => `<option value="${escapeHtml(item.value)}">${escapeHtml(item.label || item.value)}</option>`)
      .join('');
    select.value = selected;
  }

  function renderShortlistSummary() {
    const summaryEl = $('shortlistSummary');
    if (!summaryEl) return;
    const summary = state.shortlistSummary || { total: state.shortlistItems.length, by_status: {} };
    const parts = Object.entries(summary.by_status || {})
      .filter(([, count]) => count > 0)
      .map(([status, count]) => `${escapeHtml(statusLabel(status))}: ${count}`);
    summaryEl.innerHTML = `Total ${summary.total || 0}${parts.length ? ` · ${parts.join(' · ')}` : ''}`;
  }

  function filteredShortlistItems() {
    const status = $('shortlistStatusFilter')?.value || '';
    const priority = $('shortlistPriorityFilter')?.value || '';
    const sort = $('shortlistSort')?.value || 'recency';
    const items = state.shortlistItems.filter((item) => {
      if (status && item.status !== status) return false;
      if (priority) {
        const itemPriority = item.priority ?? 0;
        if (String(itemPriority) !== priority) return false;
      }
      return true;
    });
    items.sort((a, b) => {
      if (sort === 'priority') return (Number(b.priority || 0) - Number(a.priority || 0)) || String(b.updated_at || '').localeCompare(String(a.updated_at || ''));
      if (sort === 'status') return String(a.status || '').localeCompare(String(b.status || '')) || String(b.updated_at || '').localeCompare(String(a.updated_at || ''));
      return String(b.updated_at || '').localeCompare(String(a.updated_at || ''));
    });
    return items;
  }

  function renderShortlist() {
    populateShortlistStatusFilter();
    renderShortlistSummary();
    const list = $('shortlistList');
    if (!list) return;
    const items = filteredShortlistItems();
    if (!items.length) {
      list.innerHTML = '<p class="map-muted">No parcels match the shortlist filters.</p>';
      return;
    }
    list.innerHTML = items.map((item) => {
      const reference = shortlistReference(item);
      const tags = (item.tags || []).slice(0, 4).map((tag) => `<span>#${escapeHtml(tag)}</span>`).join('');
      const hazard = item.active_hazard
        ? `<small class="shortlist-hazard">${escapeHtml(item.active_hazard.label || 'Active hazard')}${item.active_hazard.issue_time ? ` · ${escapeHtml(item.active_hazard.issue_time)}` : ''}</small>`
        : '';
      return `
        <article class="shortlist-item" role="listitem">
          <div class="shortlist-item-header">
            <div>
              <strong>${escapeHtml(item.label || reference || 'Saved parcel')}</strong>
              <small>${escapeHtml(reference || 'No cadastral reference')}</small>
              ${hazard}
            </div>
            <small>P${escapeHtml(item.priority ?? '—')}</small>
          </div>
          <div class="shortlist-badges">
            <span>${escapeHtml(statusLabel(item.status))}</span>
            ${tags}
          </div>
          ${item.notes ? `<small>${escapeHtml(item.notes).slice(0, 180)}</small>` : ''}
          <div class="shortlist-item-actions">
            <button type="button" data-shortlist-open="${escapeHtml(reference)}">Open</button>
          </div>
        </article>`;
    }).join('');
    list.querySelectorAll('[data-shortlist-open]').forEach((button) => {
      button.addEventListener('click', () => {
        const reference = button.getAttribute('data-shortlist-open');
        if (reference) void loadParcelByReference(reference, true);
      });
    });
  }

  async function loadShortlist() {
    const list = $('shortlistList');
    if (!list) return;
    if (!window.landRegistrySignedIn) {
      // Signed-out visitors have no saved parcels; don't provoke a 401.
      $('shortlistSummary').textContent = 'Sign in to load saved parcels';
      const next = encodeURIComponent(window.location.pathname + window.location.search);
      list.innerHTML = `<p class="map-muted"><a href="/auth/login?next=${next}">Sign in</a> to see your saved parcels.</p>`;
      return;
    }
    const hazardOnly = $('shortlistHazardFilter')?.checked;
    list.innerHTML = '<p class="map-muted">Loading shortlist…</p>';
    try {
      const response = await fetch(`/api/v1/saved-parcels${hazardOnly ? '?active_hazard=true' : ''}`);
      if (response.status === 401) {
        $('shortlistSummary').textContent = 'Sign in to load saved parcels';
        list.innerHTML = '<p class="map-muted">Authentication required.</p>';
        return;
      }
      if (!response.ok) throw new Error('Shortlist unavailable');
      const payload = await response.json();
      state.shortlistItems = payload.items || [];
      state.shortlistStatuses = payload.status_vocabulary || [];
      state.shortlistSummary = payload.summary || null;
      renderShortlist();
    } catch (error) {
      list.innerHTML = '<p class="map-muted">Shortlist unavailable.</p>';
      mapStatus(error.message || 'Shortlist unavailable', true);
    }
  }

  function showOverlayFeature(layer, feature, lngLat) {
    const properties = feature?.properties || {};
    const popup = new maplibregl.Popup({ maxWidth: '420px', closeButton: true })
      .setLngLat(lngLat)
      .addTo(state.map);

    const renderPopup = (details = null) => {
      const rows = Object.entries(properties)
        .filter(([, value]) => value !== null && value !== undefined && typeof value !== 'object')
        .slice(0, 6)
        .map(([key, value]) => `<tr><th>${escapeHtml(key.replaceAll('_', ' '))}</th><td>${escapeHtml(value)}</td></tr>`)
        .join('');
      const related = Object.entries(details?.related || {})
        .filter(([, value]) => Array.isArray(value) && value.length)
        .map(([name, items]) => {
          const label = {
            online_documents: 'Concession documents',
            online_document_matches: 'Matched documents',
            resources: 'Source resources',
            document_gaps: 'Document status',
            snapshot: 'Snapshot',
          }[name] || name.replaceAll('_', ' ');
          const itemHtml = items.slice(0, 8).map(item => Object.entries(item || {})
            .filter(([, value]) => value !== null && value !== undefined && value !== '')
            .slice(0, 8)
            .map(([key, value]) => `<div><b>${escapeHtml(key.replaceAll('_', ' '))}:</b> ${canonicalFieldHtml(key, value, item)}</div>`)
            .join('')).join('<hr>');
          return `<section class="map-document-group"><strong>${escapeHtml(label)}</strong>${itemHtml || '<small>No details</small>'}</section>`;
        }).join('');
      const documentHint = details && !related
        ? '<p class="map-muted">No concession documents are linked to this record.</p>'
        : '';
      const heading = layer.id === 'geo-boundaries' && properties.canonical_name ? `${properties.unit_type ? `${properties.unit_type}: ` : ''}${properties.canonical_name}` : layer.title;
      popup.setHTML(`<strong>${escapeHtml(heading)}</strong><table class="parcel-detail-table"><tbody>${rows || '<tr><td>No feature details</td></tr>'}</tbody></table>${related}${documentHint}<small>${escapeHtml(layer.source || 'Source not available')}</small>`);
    };

    renderPopup();
    if (layer.id !== 'maritime-concessions' || !layer.detail_url) return;
    const featureId = feature?.id ?? properties[layer.id_column] ?? properties.id;
    if (featureId === null || featureId === undefined) return;
    fetch(layer.detail_url.replace('{feature_id}', encodeURIComponent(featureId)))
      .then(response => response.ok ? response.json() : null)
      .then(details => { if (details) renderPopup(details); })
      .catch(() => { /* retain the base concession attributes if details fail */ });
  }

  function canonicalHttpUrl(value) {
    if (typeof value !== 'string' || !value.trim()) return null;
    try {
      const url = new URL(value.trim());
      return ['http:', 'https:'].includes(url.protocol) ? url.href : null;
    } catch (_) {
      return null;
    }
  }

  function canonicalFieldHtml(key, value, item) {
    const url = canonicalHttpUrl(value);
    if (!url) return escapeHtml(value);
    const label = item?.title || item?.resource_title || (
      key === 'source_page_url' ? 'Open source page' :
      key === 'document_url' ? 'Open document' :
      key === 'requested_url' ? 'Open requested document' :
      key === 'final_url' ? 'Open document' :
      'Open document'
    );
    return `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)}</a>`;
  }

  async function loadParcelByReference(reference, flyTo = true, featureId = null) {
    if (!reference) return null;
    mapStatus('Loading parcel…');
    try {
      // A clicked tile feature's id lets the server resolve the parcel by
      // primary key instead of scanning the unindexed reference columns.
      const idHint = Number.isInteger(featureId) && featureId > 0 ? `?id=${featureId}` : '';
      const response = await fetch(`/api/v1/enrichment/parcel/by-reference/${encodeURIComponent(reference)}${idHint}`);
      if (!response.ok) {
        let detail = ''; try { detail = (await response.json()).detail || ''; } catch (_) { /* non-JSON API error */ }
        throw new Error(response.status === 404 ? 'Parcel not found' : (detail || 'Parcel service unavailable'));
      }
      const feature = await response.json();
      state.selectedReference = feature.properties?.canonical_reference || feature.properties?.national_cadastral_reference || reference;
      state.selectedFeature = feature;
      state.tileErrors.clear();
      renderSelectedGeometry(feature);
      state.map?.getSource('adjacent-parcels')?.setData({ type: 'FeatureCollection', features: [] });
      const adjacentResults = $('parcelAdjacentResults');
      if (adjacentResults) adjacentResults.innerHTML = '';
      if (flyTo && state.map) {
        const bounds = Array.isArray(feature.bbox)
          ? new maplibregl.LngLatBounds([feature.bbox[0], feature.bbox[1]], [feature.bbox[2], feature.bbox[3]])
          : featureBounds(feature);
        if (bounds) state.map.fitBounds(bounds, { padding: 80, maxZoom: 19 });
      } else if (flyTo && state.fallbackMap && Array.isArray(feature.bbox)) {
        state.fallbackMap.fitBounds(
          [[feature.bbox[1], feature.bbox[0]], [feature.bbox[3], feature.bbox[2]]],
          { padding: [30, 30], maxZoom: 19 },
        );
      }
      renderParcelPanel(feature);
      writeUrl();
      mapStatus('Parcel selected');
      loadParcelEnrichment(state.selectedReference, feature);
      return feature;
    } catch (error) {
      mapStatus(error.message || 'Parcel unavailable', true);
      return null;
    }
  }

  async function loadParcelEnrichment(reference, feature) {
    try {
      const response = await fetch(`/api/v1/enrichment/parcel/details/${encodeURIComponent(reference)}`);
      // Replace the loading placeholder either way; a 404 simply means no
      // read-model row exists for this parcel yet.
      if (!response.ok) {
        if (state.selectedReference === reference) renderParcelPanel(feature, { unavailable: response.status !== 404 });
        return;
      }
      const enrichment = await response.json();
      renderParcelPanel(feature, enrichment);
    } catch (_) {
      // Identity remains useful without optional enrichment.
      if (state.selectedReference === reference) renderParcelPanel(feature, { unavailable: true });
    }
  }

  async function identifyAtPoint(lngLat, renderedFeature = null) {
    const reference = renderedFeature && referenceFrom(renderedFeature.properties);
    if (reference) return loadParcelByReference(reference, false, Number(renderedFeature.properties?.id));
    const params = new URLSearchParams({ lat: String(lngLat.lat), lng: String(lngLat.lng) });
    try {
      const response = await fetch(`/api/v1/enrichment/parcel/at-point?${params}`);
      if (!response.ok) throw new Error('No parcel at this point');
      const feature = await response.json();
      const foundReference = referenceFrom(feature.properties);
      if (foundReference) return loadParcelByReference(foundReference, false, Number(feature.id) || null);
      state.selectedFeature = feature;
      renderParcelPanel(feature);
    } catch (error) { mapStatus(error.message, true); }
  }

  function renderSearchResults(results) {
    const list = $('mapSearchResults');
    list.replaceChildren();
    list.hidden = !results.length;
    const input = $('mapSearchInput');
    input.setAttribute('aria-expanded', results.length ? 'true' : 'false');
    if (results.length && state.searchActiveIndex >= 0) input.setAttribute('aria-activedescendant', `map-search-result-${state.searchActiveIndex}`);
    else input.removeAttribute('aria-activedescendant');
    results.forEach((result, index) => {
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'map-search-result'; button.role = 'option'; button.id = `map-search-result-${index}`; button.tabIndex = -1;
      button.setAttribute('aria-selected', String(index === state.searchActiveIndex));
      const parcelContext = result.kind === 'parcel'
        ? [result.municipality_name, result.sheet ? `Sheet ${result.sheet}` : ''].filter(Boolean).join(' · ')
        : '';
      const context = parcelContext || result.context || (result.kind === 'parcel' ? 'Cadastral parcel' : 'Municipality');
      button.innerHTML = `<strong>${escapeHtml(result.label)}</strong><small>${escapeHtml(context)}</small>`;
      button.addEventListener('click', () => chooseSearchResult(result));
      list.appendChild(button);
    });
  }

  function chooseSearchResult(result) {
    renderSearchResults([]);
    $('mapSearchInput').value = result.label;
    if (result.kind === 'parcel') { $('mapSearchInput').focus(); loadParcelByReference(result.reference, true, result.id ?? null); return; }
    const profile = result.municipality || result;
    const bbox = [profile.west, profile.south, profile.east, profile.north].map(Number);
    if (result.kind !== 'place' && state.map && bbox.every(Number.isFinite) && bbox[0] < bbox[2] && bbox[1] < bbox[3]) {
      state.map.fitBounds([[bbox[0], bbox[1]], [bbox[2], bbox[3]]], { padding: 70, maxZoom: 12, duration: 800 });
      $('mapSearchInput').focus();
      return;
    }
    const lat = Number(profile.latitude ?? profile.lat ?? profile.centroid_lat);
    const lng = Number(profile.longitude ?? profile.lng ?? profile.lon ?? profile.centroid_lng);
    if (state.map && Number.isFinite(lat) && Number.isFinite(lng)) {
      // Frame the whole municipality rather than keeping a parcel-level zoom
      // carried over from the previous view; geocoded places get closer.
      const zoom = result.kind === 'place' ? (result.context === 'Address' ? 17 : 14) : 12;
      state.map.flyTo({ center: [lng, lat], zoom, duration: 800 });
      mapStatus(`Viewing ${result.label}`);
    } else {
      mapStatus(`${result.label} has no location on file`, true);
    }
    $('mapSearchInput').focus();
  }

  const tileRetry = { pending: new Set(), timer: null, attempts: {} };

  function scheduleTileRetry(sourceId) {
    const attempts = tileRetry.attempts[sourceId] || 0;
    if (attempts >= 2) {
      mapStatus('Some map data failed to load; pan or zoom to retry.', true);
      return;
    }
    tileRetry.pending.add(sourceId);
    clearTimeout(tileRetry.timer);
    tileRetry.timer = setTimeout(() => {
      tileRetry.pending.forEach((id) => {
        const source = state.map?.getSource(id);
        const tiles = source?.serialize?.().tiles;
        if (!source?.setTiles || !tiles) return;
        tileRetry.attempts[id] = (tileRetry.attempts[id] || 0) + 1;
        source.setTiles(tiles);
      });
      tileRetry.pending.clear();
    }, 1500 * (attempts + 1));
  }

  function submitSearch(query, explicit = false) {
    clearTimeout(state.searchTimer);
    state.searchTimer = null;
    void search(query, explicit);
  }

  // Human label for a Nominatim hit; its addresstype/type says whether the
  // result is a municipality, a street, a watercourse, and so on.
  function placeKindLabel(place) {
    const type = place.addresstype || place.type || '';
    if (['city', 'town', 'village', 'municipality', 'hamlet'].includes(type)) return 'Place';
    if (['road', 'street', 'house', 'building'].includes(type) || place.category === 'highway') return 'Address';
    return type ? type.replaceAll('_', ' ').replace(/^./, (c) => c.toUpperCase()) : 'Place';
  }

  // Only explicit submits reach the public Nominatim service: its usage
  // policy forbids autocomplete, and as-you-type calls would forward every
  // keystroke (including parcel references) to a third party.
  async function search(query, explicit = false) {
    const normalized = query.trim();
    const requestId = ++state.searchRequest;
    if (state.searchController) state.searchController.abort();
    if (normalized.length < 2) { renderSearchResults([]); $('mapSearchStatus').textContent = ''; return; }
    const controller = new AbortController();
    state.searchController = controller;
    const timeout = setTimeout(() => controller.abort(), 6500);
    $('mapSearchStatus').textContent = 'Searching…';
    try {
      const response = await fetch(`/api/v1/map/search?query=${encodeURIComponent(normalized)}`, { signal: controller.signal });
      if (!response.ok) throw new Error('Search service unavailable');
      const payload = await response.json();
      if (requestId !== state.searchRequest) return;
      let results = payload.results || [];
      if (!results.length && explicit && !(/^[A-Z]\d{3}[A-Z]?\d{4}\d{2}(?:\.|$)/i.test(normalized) || /^[A-Z]\d{3}[_-].*\./i.test(normalized))) {
        // The canonical profile store is optional in local/degraded
        // deployments. Keep municipality discovery usable through the same
        // Nominatim provider already used by the legacy location search.
        const placeResponse = await fetch(`https://nominatim.openstreetmap.org/search?format=jsonv2&countrycodes=it&limit=8&q=${encodeURIComponent(normalized)}`, { headers: { Accept: 'application/json' }, signal: controller.signal });
        if (placeResponse.ok) {
          const places = await placeResponse.json();
          // Nominatim frequently returns near-duplicate administrative
          // entries for the same place (e.g. the city and its metro/comune
          // boundary); collapse anything sharing a rounded coordinate pair.
          const seen = new Set();
          results = places
            .map((place) => ({ kind: 'place', label: place.display_name, context: placeKindLabel(place), lat: place.lat, lon: place.lon }))
            .filter((place) => {
              const key = `${Number(place.lat).toFixed(3)},${Number(place.lon).toFixed(3)}`;
              if (seen.has(key)) return false;
              seen.add(key);
              return true;
            });
        }
      }
      if (requestId !== state.searchRequest) return;
      state.searchActiveIndex = results.length ? 0 : -1;
      renderSearchResults(results);
      $('mapSearchStatus').textContent = results.length ? '' : (response.ok ? (explicit ? 'No result' : 'No municipality or parcel match — press Enter to search places') : 'Search service unavailable; try again.');
    } catch (error) {
      if (error.name === 'AbortError') return;
      if (requestId === state.searchRequest) $('mapSearchStatus').textContent = 'Search unavailable';
    } finally {
      clearTimeout(timeout);
      if (state.searchController === controller) state.searchController = null;
    }
  }

  function copyLink() {
    writeUrl();
    const url = window.location.href;
    const done = () => { mapStatus('Map link copied'); };
    if (navigator.clipboard?.writeText) navigator.clipboard.writeText(url).then(done).catch(() => fallbackCopy(url));
    else fallbackCopy(url);
  }

  function clearParcelSelection() {
    state.selectedReference = null;
    state.selectedFeature = null;
    if (state.map?.getSource('selected-parcel')) state.map.getSource('selected-parcel').setData({ type: 'FeatureCollection', features: [] });
    state.map?.getSource('adjacent-parcels')?.setData({ type: 'FeatureCollection', features: [] });
    const adjacentResults = $('parcelAdjacentResults');
    if (adjacentResults) adjacentResults.innerHTML = '';
    $('directParcelPanel').hidden = true;
    updateParcelZoomAffordance();
    writeUrl();
    mapStatus('Selection cleared');
  }

  function fallbackCopy(value) {
    const input = document.createElement('textarea'); input.value = value; input.style.position = 'fixed'; input.style.opacity = '0';
    document.body.appendChild(input); input.select();
    try { document.execCommand('copy'); mapStatus('Map link copied'); } catch (_) { mapStatus('Copy unavailable', true); }
    input.remove();
  }

  // GeolocationPositionError collapses several distinct failure modes into
  // one API: an actual user/browser denial, a device that can't fix a
  // position, and a timeout all reach the same error callback. Reporting
  // them as one "permission was not granted" message is misleading — e.g.
  // on a non-secure origin (not https/localhost) the browser rejects the
  // call as PERMISSION_DENIED without ever showing a prompt, which reads as
  // a user action that never happened.
  function geolocationErrorMessage(error) {
    switch (error?.code) {
      case error?.PERMISSION_DENIED: return 'Location permission was not granted';
      case error?.POSITION_UNAVAILABLE: return 'Your location could not be determined';
      case error?.TIMEOUT: return 'Location request timed out';
      default: return 'Location is not available';
    }
  }

  function locate() {
    if (!navigator.geolocation) { mapStatus('Location is not available', true); return; }
    if (!window.isSecureContext) { mapStatus('Location requires a secure (HTTPS) connection', true); return; }
    mapStatus('Requesting your location…');
    navigator.geolocation.getCurrentPosition((position) => {
      if (state.map) state.map.flyTo({ center: [position.coords.longitude, position.coords.latitude], zoom: 16, duration: 800 });
      else state.fallbackMap?.flyTo([position.coords.latitude, position.coords.longitude], 16, { duration: 0.8 });
      mapStatus('Location found');
    }, (error) => mapStatus(geolocationErrorMessage(error), true), { enableHighAccuracy: false, timeout: 8000 });
  }

  function setupMapResizeObserver() {
    const element = $('directMap');
    if (!element || !window.ResizeObserver) return;
    state.resizeObserver = new ResizeObserver(() => {
      if ((!state.map && !state.fallbackMap) || !mapHasUsableSize()) return;
      window.requestAnimationFrame(() => {
        if (state.map) state.map.resize();
        else state.fallbackMap?.invalidateSize();
      });
    });
    state.resizeObserver.observe(element);
  }

  // ---- Enrichment overlays (POI / active fires / civil-protection bulletin) ----
  // MapLibre port of enrichment-layers.js's Leaflet overlays, backed by the
  // same generic /api/v1/enrichment/* endpoints (no upload/GeoDataFrame
  // dependency, so these are safe to bring to the direct map as-is).
  const POI_CATEGORY_META = {
    universities: { color: '#6f42c1', label: 'Universities' },
    schools: { color: '#0d6efd', label: 'Schools' },
    kindergartens: { color: '#20c997', label: 'Kindergartens' },
    supermarkets: { color: '#fd7e14', label: 'Supermarkets' },
    shops: { color: '#e83e8c', label: 'Shops' },
    pharmacies: { color: '#198754', label: 'Pharmacies' },
    hospitals: { color: '#dc3545', label: 'Hospitals' },
    public_transport: { color: '#0dcaf0', label: 'Public transport' },
    parks: { color: '#84cc16', label: 'Parks' },
    restaurants: { color: '#ffc107', label: 'Restaurants' },
  };
  const POI_RADIUS_KM = 2;
  const BULLETIN_LEVELS = {
    red: { rank: 4, color: '#dc2626', label: 'Red alert' },
    orange: { rank: 3, color: '#f97316', label: 'Orange alert' },
    yellow: { rank: 2, color: '#facc15', label: 'Yellow alert' },
    green: { rank: 1, color: '#22c55e', label: 'No alert' },
    unknown: { rank: 0, color: '#94a3b8', label: 'Unavailable' },
  };
  const enrichmentOverlays = {
    poi: { active: false, fetchToken: 0, debounceTimer: null, hoverPopup: null },
    fires: { active: false },
    bulletin: { active: false, fetchToken: 0 },
  };

  // ---- Sales map integration --------------------------------------------
  // Sales remain an optional domain feed.  The land-registry API proxies the
  // sibling app's /sales/map-points contract, so this map can use the same
  // rich marker payload without coupling itself to the sales database.
  const salesOverlay = {
    active: false,
    points: [],
    fetchToken: 0,
    geo: null,
  };

  function numberValue(...values) {
    for (const value of values) {
      const parsed = Number(value);
      if (Number.isFinite(parsed)) return parsed;
    }
    return null;
  }

  function salesPointFeature(point) {
    const lat = numberValue(point.lat, point.latitude);
    const lng = numberValue(point.lng, point.lon, point.longitude);
    if (lat === null || lng === null) return null;
    return {
      type: 'Feature',
      id: point.id || point.sale_id || `${lat}:${lng}`,
      geometry: { type: 'Point', coordinates: [lng, lat] },
      properties: { ...point, lat, lng },
    };
  }

  function salesPrice(properties) {
    return numberValue(properties.price, properties.base_auction_price, properties.minimum_offer, properties.starting_price);
  }

  function salesScore(properties) {
    return numberValue(properties.saleability_score, properties.saleability, properties.dse_score);
  }

  function sortSales(points) {
    const sort = $('salesSort')?.value || 'saleability_desc';
    return [...points].sort((left, right) => {
      const a = left.properties || {}; const b = right.properties || {};
      if (sort === 'price_asc') return (salesPrice(a) ?? Infinity) - (salesPrice(b) ?? Infinity);
      if (sort === 'deadline_asc') return String(a.auction_date || a.offer_deadline || '9999').localeCompare(String(b.auction_date || b.offer_deadline || '9999'));
      return (salesScore(b) ?? -Infinity) - (salesScore(a) ?? -Infinity);
    });
  }

  function salesGeoJson() {
    const minimum = numberValue($('salesSaleabilityMin')?.value);
    const filtered = salesOverlay.points.filter((feature) => minimum === null || (salesScore(feature.properties || {}) ?? -Infinity) >= minimum);
    return { type: 'FeatureCollection', features: sortSales(filtered) };
  }

  function salesValueLabel(value, suffix = '') {
    const numeric = numberValue(value);
    return numeric === null ? '—' : `${numeric.toLocaleString('it-IT', { maximumFractionDigits: 1 })}${suffix}`;
  }

  function salesPopupHtml(properties) {
    const price = salesPrice(properties);
    const appraisal = numberValue(properties.appraisal_value, properties.market_value);
    const score = salesScore(properties);
    const detail = properties.detail_url || properties.source_link || (properties.id ? `/sales/${encodeURIComponent(properties.id)}` : '');
    const lat = numberValue(properties.lat, properties.latitude);
    const lng = numberValue(properties.lng, properties.longitude);
    const maps = lat !== null && lng !== null ? `https://www.google.com/maps/search/?api=1&query=${lat},${lng}` : '';
    const street = lat !== null && lng !== null ? `https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=${lat},${lng}` : '';
    const row = (label, value) => `<div><span class="map-popup-label">${escapeHtml(label)}</span> ${escapeHtml(value ?? '—')}</div>`;
    return `<div class="sales-popup"><strong>${escapeHtml(properties.display_title || properties.title || 'Sale')}</strong>`
      + row('Price', price === null ? (properties.display_price || '—') : `€${salesValueLabel(price)}`)
      + row('Appraisal', appraisal === null ? '—' : `€${salesValueLabel(appraisal)}`)
      + row('Auction date', properties.display_date || properties.auction_date || properties.offer_deadline || '—')
      + row('Court', properties.court || '—')
      + row('DSE', properties.dse_score == null ? '—' : `${salesValueLabel(properties.dse_score)}/100`)
      + row('Saleability', score === null ? '—' : salesValueLabel(score))
      + (properties.dse_effective_discount != null ? row('Effective discount', `${salesValueLabel(properties.dse_effective_discount)}%`) : '')
      + `<div class="sales-popup-actions">${detail ? `<a href="${escapeHtml(detail)}" target="_blank" rel="noopener noreferrer">Details</a>` : ''}`
      + (maps ? `<a href="${maps}" target="_blank" rel="noopener noreferrer">Google Maps</a>` : '')
      + (street ? `<a href="${street}" target="_blank" rel="noopener noreferrer">Street View</a>` : '')
      + '</div></div>';
  }

  function addSalesOverlayLayers() {
    if (!state.map) return;
    if (!state.map.getSource('sales-properties')) {
      state.map.addSource('sales-properties', { type: 'geojson', data: emptyFeatureCollection(), cluster: true, clusterMaxZoom: 14, clusterRadius: 45 });
    }
    if (!state.map.getLayer('sales-clusters')) state.map.addLayer({ id: 'sales-clusters', type: 'circle', source: 'sales-properties', filter: ['has', 'point_count'], paint: { 'circle-color': '#7c3aed', 'circle-radius': ['step', ['get', 'point_count'], 18, 25, 23, 100, 30], 'circle-opacity': .88, 'circle-stroke-color': '#fff', 'circle-stroke-width': 2 } });
    if (!state.map.getLayer('sales-cluster-count')) state.map.addLayer({ id: 'sales-cluster-count', type: 'symbol', source: 'sales-properties', filter: ['has', 'point_count'], layout: { 'text-field': ['get', 'point_count_abbreviated'], 'text-size': 11 }, paint: { 'text-color': '#fff' } });
    if (!state.map.getLayer('sales-unclustered')) state.map.addLayer({ id: 'sales-unclustered', type: 'circle', source: 'sales-properties', filter: ['!', ['has', 'point_count']], paint: { 'circle-radius': 7, 'circle-color': ['interpolate', ['linear'], ['coalesce', ['get', 'saleability_score'], 0], 0, '#dc2626', 50, '#f59e0b', 100, '#16a34a'], 'circle-opacity': .9, 'circle-stroke-color': '#fff', 'circle-stroke-width': 1.5 } });
    state.map.on('click', 'sales-clusters', (event) => {
      const feature = event.features?.[0];
      if (!feature) return;
      state.map.getSource('sales-properties')?.getClusterExpansionZoom(feature.properties.cluster_id, (error, zoom) => {
        if (!error) state.map.easeTo({ center: feature.geometry.coordinates, zoom });
      });
    });
    state.map.on('click', 'sales-unclustered', (event) => {
      const feature = event.features?.[0];
      if (feature) new maplibregl.Popup({ maxWidth: '340px' }).setLngLat(event.lngLat).setHTML(salesPopupHtml(feature.properties || {})).addTo(state.map);
    });
    ['sales-clusters', 'sales-unclustered'].forEach((layer) => {
      state.map.on('mouseenter', layer, () => { state.map.getCanvas().style.cursor = 'pointer'; });
      state.map.on('mouseleave', layer, () => { state.map.getCanvas().style.cursor = ''; });
    });
  }

  function salesRequestUrl() {
    const url = new URL(window.salesMapPointsUrl || '/api/v1/sales/map-points', window.location.origin);
    const current = new URLSearchParams(window.location.search);
    current.forEach((value, key) => { if (!['lat', 'lng', 'zoom', 'parcel', 'page'].includes(key)) url.searchParams.set(key, value); });
    url.searchParams.set('limit', '60000');
    url.searchParams.set('order_by', $('salesSort')?.value || 'saleability_desc');
    return url.toString();
  }

  async function loadSalesOverlay() {
    if (!salesOverlay.active || !state.map) return;
    addSalesOverlayLayers();
    const token = ++salesOverlay.fetchToken;
    const status = $('salesMapStatus');
    if (status) status.textContent = 'Loading sales…';
    try {
      const response = await fetch(salesRequestUrl(), { credentials: 'same-origin' });
      if (!response.ok) throw new Error(response.status === 401 ? 'Sales feed requires sign-in' : 'Sales feed unavailable');
      const payload = await response.json();
      if (token !== salesOverlay.fetchToken) return;
      const points = Array.isArray(payload) ? payload : (payload.points || payload.data || []);
      salesOverlay.points = points.map(salesPointFeature).filter(Boolean);
      state.map.getSource('sales-properties')?.setData(salesGeoJson());
      const count = salesGeoJson().features.length;
      if ($('salesCount')) $('salesCount').textContent = `(${count})`;
      if (status) status.textContent = `${count} plotted sale(s)`;
      if (count) {
        const bounds = new maplibregl.LngLatBounds();
        salesGeoJson().features.forEach((feature) => bounds.extend(feature.geometry.coordinates));
        if (!bounds.isEmpty()) state.map.fitBounds(bounds, { padding: 70, maxZoom: 15, duration: 500 });
      }
    } catch (error) {
      salesOverlay.points = [];
      state.map.getSource('sales-properties')?.setData(emptyFeatureCollection());
      if ($('salesCount')) $('salesCount').textContent = '';
      if (status) status.textContent = error.message || 'Sales feed unavailable';
    }
  }

  function toggleSalesOverlay() {
    salesOverlay.active = !salesOverlay.active;
    const button = $('toggleSalesLayer');
    button?.classList.toggle('active', salesOverlay.active);
    button?.setAttribute('aria-pressed', String(salesOverlay.active));
    if (salesOverlay.active) { void loadSalesGeoLayers(); loadSalesOverlay(); }
    else {
      salesOverlay.points = [];
      state.map?.getSource('sales-properties')?.setData(emptyFeatureCollection());
      if ($('salesCount')) $('salesCount').textContent = '';
      if ($('salesMapStatus')) $('salesMapStatus').textContent = '';
    }
  }

  function openMapAt(kind) {
    const map = state.map || state.fallbackMap;
    if (!map) return;
    const center = map.getCenter();
    const url = kind === 'street'
      ? `https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=${center.lat},${center.lng}`
      : `https://www.google.com/maps/search/?api=1&query=${center.lat},${center.lng}`;
    window.open(url, '_blank', 'noopener,noreferrer');
  }

  async function loadSalesGeoLayers() {
    if (!window.salesGeoLayersUrl || !state.map) return;
    try {
      const response = await fetch(window.salesGeoLayersUrl, { credentials: 'same-origin' });
      if (!response.ok) return;
      salesOverlay.geo = await response.json();
      const metric = $('salesGeoMetric');
      const metrics = salesOverlay.geo?.stats_meta?.metrics || [];
      if (metric) metric.innerHTML = '<option value="none">Default</option>' + metrics.map((item) => `<option value="${escapeHtml(item.key)}">${escapeHtml(item.label || item.key)}</option>`).join('');
      renderSalesGeoLayers();
    } catch (_) { /* administrative statistics are optional */ }
  }

  function renderSalesGeoLayers() {
    if (!state.map || !salesOverlay.geo) return;
    const type = $('salesGeoLayerType')?.value || 'none';
    ['region', 'province', 'municipality'].forEach((kind) => {
      const sourceId = `sales-geo-${kind}`; const layerId = `sales-geo-${kind}-points`;
      if (!state.map.getSource(sourceId)) state.map.addSource(sourceId, { type: 'geojson', data: emptyFeatureCollection() });
      if (!state.map.getLayer(layerId)) {
        state.map.addLayer({ id: layerId, type: 'circle', source: sourceId, paint: { 'circle-radius': kind === 'region' ? 13 : (kind === 'province' ? 10 : 6), 'circle-color': ['coalesce', ['get', 'color'], '#2563eb'], 'circle-opacity': .78, 'circle-stroke-color': '#fff', 'circle-stroke-width': 1.5 }, layout: { visibility: 'none' } });
        state.map.on('click', layerId, (event) => {
          const properties = event.features?.[0]?.properties || {};
          const title = properties.name || properties.code || kind;
          const rows = Object.entries(properties).filter(([key]) => !['color'].includes(key)).slice(0, 8).map(([key, value]) => `<div><span class="map-popup-label">${escapeHtml(key)}</span> ${escapeHtml(value)}</div>`).join('');
          new maplibregl.Popup({ maxWidth: '300px' }).setLngLat(event.lngLat).setHTML(`<div class="sales-popup"><strong>${escapeHtml(title)}</strong>${rows}</div>`).addTo(state.map);
        });
        state.map.on('mouseenter', layerId, () => { state.map.getCanvas().style.cursor = 'pointer'; });
        state.map.on('mouseleave', layerId, () => { state.map.getCanvas().style.cursor = ''; });
      }
      const items = salesOverlay.geo?.[`${kind}s`] || [];
      const metric = $('salesGeoMetric')?.value || 'none';
      const stats = kind === 'province' ? (salesOverlay.geo?.prov_stats || {}) : (salesOverlay.geo?.reg_stats || {});
      const values = items.map((item) => {
        const direct = numberValue(item[metric]);
        if (direct !== null) return direct;
        const key = kind === 'province' ? item.code : item.name;
        const history = stats[key] || {};
        const years = Object.keys(history).sort();
        return years.length ? numberValue(history[years[years.length - 1]]?.[metric]) : null;
      }).filter((value) => value !== null);
      const minimum = values.length ? Math.min(...values) : 0; const maximum = values.length ? Math.max(...values) : 0;
      const colorFor = (item) => {
        const defaultColor = kind === 'region' ? '#dc2626' : kind === 'province' ? '#f59e0b' : '#16a34a';
        if (metric === 'none') return defaultColor;
        const direct = numberValue(item[metric]);
        const key = kind === 'province' ? item.code : item.name;
        const history = stats[key] || {}; const years = Object.keys(history).sort();
        const value = direct ?? (years.length ? numberValue(history[years[years.length - 1]]?.[metric]) : null);
        if (value === null) return '#94a3b8';
        const t = maximum > minimum ? (value - minimum) / (maximum - minimum) : .5;
        const red = Math.round(253 * (1 - t) + 68 * t); const green = Math.round(231 * (1 - t) + 1 * t); const blue = Math.round(37 * (1 - t) + 84 * t);
        return `rgb(${red},${green},${blue})`;
      };
      const features = items.filter((item) => Number.isFinite(Number(item.lat)) && Number.isFinite(Number(item.lon))).map((item) => ({ type: 'Feature', geometry: { type: 'Point', coordinates: [Number(item.lon), Number(item.lat)] }, properties: { ...item, color: colorFor(item), metric_value: metric === 'none' ? null : item[metric] } }));
      state.map.getSource(sourceId)?.setData({ type: 'FeatureCollection', features });
      state.map.setLayoutProperty(layerId, 'visibility', type === kind ? 'visible' : 'none');
    });
  }

  function emptyFeatureCollection() { return { type: 'FeatureCollection', features: [] }; }

  function ensureOverlaySource(id) {
    if (!state.map.getSource(id)) state.map.addSource(id, { type: 'geojson', data: emptyFeatureCollection() });
  }

  function selectedPoiCategories() {
    const select = $('poiCategories');
    if (!select) return [];
    return [...select.selectedOptions].map((option) => option.value).filter(Boolean);
  }

  function poiQueryParams(center) {
    const params = new URLSearchParams({
      lat: center.lat,
      lng: center.lng,
      radius_km: $('poiRadius')?.value || String(POI_RADIUS_KM),
    });
    const categories = selectedPoiCategories();
    if (categories.length) params.set('categories', categories.join(','));
    return params;
  }

  function populatePoiCategories() {
    const select = $('poiCategories');
    if (!select) return;
    Object.entries(POI_CATEGORY_META).forEach(([key, meta]) => {
      const option = document.createElement('option');
      option.value = key; option.textContent = window.t?.(meta.label) || meta.label; option.selected = true;
      select.appendChild(option);
    });
  }

  function renderLegend(elementId, items) {
    const element = $(elementId);
    if (!element) return;
    element.innerHTML = items.map(([color, label]) => `<span class="enrichment-legend-item"><span class="enrichment-legend-dot" style="background:${color}"></span>${escapeHtml(window.t?.(label) || label)}</span>`).join('');
  }

  function bulletinSeverity(description) {
    const text = String(description || '').toUpperCase();
    if (text.includes('ROSSA')) return BULLETIN_LEVELS.red;
    if (text.includes('ARANCIONE')) return BULLETIN_LEVELS.orange;
    if (text.includes('GIALLA')) return BULLETIN_LEVELS.yellow;
    if (text.includes('NESSUNA ALLERTA') || text.includes('ASSENZA DI FENOMENI')) return BULLETIN_LEVELS.green;
    return BULLETIN_LEVELS.unknown;
  }

  function bulletinFeatureSeverity(properties = {}) {
    const represented = properties['Rappresentata nella mappa'];
    if (represented) return bulletinSeverity(represented);
    return [
      properties['Per rischio idraulico'],
      properties['Per rischio temporali'],
      properties['Per rischio idrogeologico'],
    ].map(bulletinSeverity).sort((a, b) => b.rank - a.rank)[0] || BULLETIN_LEVELS.unknown;
  }

  function fireColor(confidence) {
    const numeric = Number(confidence);
    if (!Number.isNaN(numeric)) {
      if (numeric >= 80) return '#dc2626';
      if (numeric >= 50) return '#f97316';
      return '#facc15';
    }
    const normalized = String(confidence || '').toLowerCase();
    if (normalized === 'h' || normalized === 'high') return '#dc2626';
    if (normalized === 'n' || normalized === 'nominal') return '#f97316';
    return '#facc15';
  }

  function addPoiOverlayLayer() {
    ensureOverlaySource('enrichment-poi');
    if (state.map.getLayer('point-enrichment-poi')) return;
    state.map.addLayer({ id: 'point-enrichment-poi', type: 'circle', source: 'enrichment-poi',
      paint: { 'circle-radius': 5, 'circle-color': ['get', 'color'], 'circle-stroke-color': '#fff', 'circle-stroke-width': 1, 'circle-opacity': .85 } });
    state.map.on('mouseenter', 'point-enrichment-poi', () => { state.map.getCanvas().style.cursor = 'pointer'; });
    state.map.on('mouseleave', 'point-enrichment-poi', () => {
      state.map.getCanvas().style.cursor = '';
      enrichmentOverlays.poi.hoverPopup?.remove();
      enrichmentOverlays.poi.hoverPopup = null;
    });
    state.map.on('mousemove', 'point-enrichment-poi', (event) => {
      const feature = event.features?.[0];
      if (!feature) return;
      enrichmentOverlays.poi.hoverPopup?.remove();
      enrichmentOverlays.poi.hoverPopup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 8 })
        .setLngLat(event.lngLat)
        .setHTML(`<strong>${escapeHtml(feature.properties.name)}</strong><br><small>${escapeHtml(feature.properties.label)}</small>`)
        .addTo(state.map);
    });
  }

  async function refreshPoiOverlay() {
    if (!enrichmentOverlays.poi.active || !state.map) return;
    const token = ++enrichmentOverlays.poi.fetchToken;
    const center = state.map.getCenter();
    try {
      const response = await fetch(`/api/v1/enrichment/pois/?${poiQueryParams(center).toString()}`);
      if (!response.ok || token !== enrichmentOverlays.poi.fetchToken) return;
      const data = await response.json();
      const present = [];
      const features = [];
      Object.entries(data.categories || {}).forEach(([category, list]) => {
        if (!list?.length) return;
        present.push(category);
        const meta = POI_CATEGORY_META[category] || { color: '#666', label: category };
        list.forEach((poi) => {
          if (poi.lat == null || poi.lng == null) return;
          features.push({ type: 'Feature', geometry: { type: 'Point', coordinates: [poi.lng, poi.lat] },
            properties: { name: poi.name || meta.label, label: meta.label, color: meta.color } });
        });
      });
      state.map.getSource('enrichment-poi')?.setData({ type: 'FeatureCollection', features });
      renderLegend('enrichmentPoiLegend', present.map((category) => [(POI_CATEGORY_META[category] || {}).color || '#666', (POI_CATEGORY_META[category] || {}).label || category]));
    } catch (_) { /* POI overlay is optional; the map stays usable without it */ }
  }

  function refreshPoiOverlayDebounced() {
    clearTimeout(enrichmentOverlays.poi.debounceTimer);
    enrichmentOverlays.poi.debounceTimer = setTimeout(refreshPoiOverlay, 400);
  }

  function togglePoiOverlay() {
    const button = $('toggleEnrichmentPois');
    enrichmentOverlays.poi.active = !enrichmentOverlays.poi.active;
    button?.classList.toggle('active', enrichmentOverlays.poi.active);
    button?.setAttribute('aria-pressed', String(enrichmentOverlays.poi.active));
    if (enrichmentOverlays.poi.active) {
      addPoiOverlayLayer();
      state.map.on('moveend', refreshPoiOverlayDebounced);
      refreshPoiOverlay();
    } else {
      state.map.off('moveend', refreshPoiOverlayDebounced);
      state.map.getSource('enrichment-poi')?.setData(emptyFeatureCollection());
      renderLegend('enrichmentPoiLegend', []);
    }
  }

  function openPoiExplorer() {
    const map = state.map || state.fallbackMap;
    if (!map) return;
    const center = map.getCenter();
    const params = poiQueryParams(center);
    let modal = $('poiExplorerModal');
    if (!modal) {
      modal = document.createElement('div');
      modal.id = 'poiExplorerModal';
      modal.className = 'poi-explorer-modal';
      modal.innerHTML = '<div class="poi-explorer-dialog" role="dialog" aria-modal="true" aria-label="POI Explorer"><div class="poi-explorer-toolbar"><strong>POI Explorer</strong><button type="button" class="map-card-close" id="poiExplorerClose">×</button></div><iframe title="POI Explorer map" id="poiExplorerFrame"></iframe></div>';
      document.body.appendChild(modal);
      $('poiExplorerClose')?.addEventListener('click', () => { modal.hidden = true; });
      modal.addEventListener('click', (event) => { if (event.target === modal) modal.hidden = true; });
    }
    modal.hidden = false;
    const frame = $('poiExplorerFrame');
    if (frame) frame.src = `/api/v1/enrichment/poi-map?${params.toString()}`;
  }

  function exportPoi(format) {
    const map = state.map || state.fallbackMap;
    if (!map) return;
    const params = poiQueryParams(map.getCenter());
    params.set('format', format);
    const link = document.createElement('a');
    link.href = `/api/v1/enrichment/poi-report?${params.toString()}`;
    link.download = `poi-explorer.${format}`;
    document.body.appendChild(link); link.click(); link.remove();
  }

  function addFiresOverlayLayer() {
    ensureOverlaySource('enrichment-fires');
    if (state.map.getLayer('point-enrichment-fires')) return;
    state.map.addLayer({ id: 'point-enrichment-fires', type: 'circle', source: 'enrichment-fires',
      paint: { 'circle-radius': 5, 'circle-color': ['get', 'color'], 'circle-stroke-color': '#fff', 'circle-stroke-width': 1, 'circle-opacity': .85 } });
    state.map.on('mouseenter', 'point-enrichment-fires', () => { state.map.getCanvas().style.cursor = 'pointer'; });
    state.map.on('mouseleave', 'point-enrichment-fires', () => { state.map.getCanvas().style.cursor = ''; });
    state.map.on('click', 'point-enrichment-fires', (event) => {
      const feature = event.features?.[0];
      if (!feature) return;
      const props = feature.properties;
      new maplibregl.Popup({ maxWidth: '280px' }).setLngLat(event.lngLat).setHTML(
        `<div class="fires-popup"><strong>NASA FIRMS detection</strong><div>${escapeHtml(props.observed || '—')}</div><div>FRP: ${props.frp != null ? escapeHtml(`${props.frp} MW`) : '—'}</div></div>`,
      ).addTo(state.map);
    });
  }

  async function refreshFiresOverlay() {
    if (!enrichmentOverlays.fires.active || !state.map) return;
    const countElement = $('enrichmentFiresCount');
    try {
      const response = await fetch('/api/v1/enrichment/fires');
      if (!response.ok) throw new Error('unavailable');
      const data = await response.json();
      const features = (data.detections || []).map((detection) => {
        const lat = Number(detection.latitude);
        const lng = Number(detection.longitude);
        if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
        return { type: 'Feature', geometry: { type: 'Point', coordinates: [lng, lat] },
          properties: { color: fireColor(detection.confidence), frp: detection.frp, observed: detection.acq_date || '' } };
      }).filter(Boolean);
      state.map.getSource('enrichment-fires')?.setData({ type: 'FeatureCollection', features });
      if (countElement) countElement.textContent = data.count ? `(${data.count})` : '(0)';
    } catch (_) {
      if (countElement) countElement.textContent = '';
    }
  }

  function toggleFiresOverlay() {
    const button = $('toggleEnrichmentFires');
    enrichmentOverlays.fires.active = !enrichmentOverlays.fires.active;
    button?.classList.toggle('active', enrichmentOverlays.fires.active);
    button?.setAttribute('aria-pressed', String(enrichmentOverlays.fires.active));
    if (enrichmentOverlays.fires.active) { addFiresOverlayLayer(); refreshFiresOverlay(); }
    else {
      state.map.getSource('enrichment-fires')?.setData(emptyFeatureCollection());
      const countElement = $('enrichmentFiresCount');
      if (countElement) countElement.textContent = '';
    }
  }

  function addBulletinOverlayLayer() {
    ensureOverlaySource('enrichment-bulletin');
    if (state.map.getLayer('fill-enrichment-bulletin')) return;
    state.map.addLayer({ id: 'fill-enrichment-bulletin', type: 'fill', source: 'enrichment-bulletin',
      paint: { 'fill-color': ['get', 'color'], 'fill-opacity': ['get', 'fillOpacity'] } });
    state.map.addLayer({ id: 'line-enrichment-bulletin', type: 'line', source: 'enrichment-bulletin',
      paint: { 'line-color': ['get', 'color'], 'line-width': 1.5, 'line-opacity': .9 } });
    state.map.on('mouseenter', 'fill-enrichment-bulletin', () => { state.map.getCanvas().style.cursor = 'pointer'; });
    state.map.on('mouseleave', 'fill-enrichment-bulletin', () => { state.map.getCanvas().style.cursor = ''; });
    state.map.on('click', 'fill-enrichment-bulletin', (event) => {
      const feature = event.features?.[0];
      if (!feature) return;
      const props = feature.properties;
      new maplibregl.Popup({ maxWidth: '320px' }).setLngLat(event.lngLat).setHTML(
        `<div class="bulletin-popup"><strong>${escapeHtml(props.zone || 'Alert area')}</strong><div style="color:${escapeHtml(props.color)};font-weight:600">${escapeHtml(window.t?.(props.label) || props.label)}</div></div>`,
      ).addTo(state.map);
    });
  }

  async function refreshBulletinOverlay() {
    if (!enrichmentOverlays.bulletin.active || !state.map) return;
    const token = ++enrichmentOverlays.bulletin.fetchToken;
    const countElement = $('enrichmentBulletinCount');
    if (countElement) countElement.textContent = '(…)';
    try {
      const response = await fetch('/api/v1/enrichment/bulletin');
      if (!response.ok || token !== enrichmentOverlays.bulletin.fetchToken) return;
      const data = await response.json();
      const topology = data?.today_zones;
      const object = topology?.objects ? Object.values(topology.objects)[0] : null;
      if (!window.topojson) {
        // Without the parser the layer would look like "no alerts today".
        if (countElement) countElement.textContent = '';
        mapStatus('Civil Protection alerts could not be drawn (TopoJSON parser unavailable).', true);
        return;
      }
      if (!topology || !object) {
        if (countElement) countElement.textContent = '(0)';
        renderLegend('enrichmentBulletinLegend', []);
        return;
      }
      const collection = window.topojson.feature(topology, object);
      const rawFeatures = collection.features || (collection.type === 'Feature' ? [collection] : []);
      const features = rawFeatures.map((feature) => {
        const severity = bulletinFeatureSeverity(feature.properties);
        return { type: 'Feature', geometry: feature.geometry,
          properties: { zone: feature.properties?.['Nome zona'] || 'Zona', color: severity.color, label: severity.label, fillOpacity: severity.rank > 1 ? .28 : 0.015 } };
      });
      state.map.getSource('enrichment-bulletin')?.setData({ type: 'FeatureCollection', features });
      if (countElement) countElement.textContent = `(${features.length})`;
      renderLegend('enrichmentBulletinLegend', ['red', 'orange', 'yellow', 'green'].map((key) => [BULLETIN_LEVELS[key].color, BULLETIN_LEVELS[key].label]));
    } catch (_) {
      if (countElement) countElement.textContent = '';
    }
  }

  function toggleBulletinOverlay() {
    const button = $('toggleEnrichmentBulletin');
    enrichmentOverlays.bulletin.active = !enrichmentOverlays.bulletin.active;
    button?.classList.toggle('active', enrichmentOverlays.bulletin.active);
    button?.setAttribute('aria-pressed', String(enrichmentOverlays.bulletin.active));
    if (enrichmentOverlays.bulletin.active) { addBulletinOverlayLayer(); refreshBulletinOverlay(); }
    else {
      state.map.getSource('enrichment-bulletin')?.setData(emptyFeatureCollection());
      const countElement = $('enrichmentBulletinCount');
      if (countElement) countElement.textContent = '';
      renderLegend('enrichmentBulletinLegend', []);
    }
  }

  // ---- Adjacency lookup (single-parcel analogue of the legacy multi-select
  // "Find Adjacent" spatial analysis) ----
  function addAdjacentParcelsLayer() {
    ensureOverlaySource('adjacent-parcels');
    if (state.map.getLayer('fill-adjacent-parcels')) return;
    state.map.addLayer({ id: 'fill-adjacent-parcels', type: 'fill', source: 'adjacent-parcels',
      paint: { 'fill-color': '#2563eb', 'fill-opacity': .18 } });
    state.map.addLayer({ id: 'line-adjacent-parcels', type: 'line', source: 'adjacent-parcels',
      paint: { 'line-color': '#2563eb', 'line-width': 2, 'line-opacity': .9 } });
  }

  function renderAdjacentResults(features) {
    const container = $('parcelAdjacentResults');
    if (!container) return;
    if (!features.length) { container.innerHTML = '<p class="map-muted">No adjacent parcels found.</p>'; return; }
    container.innerHTML = `<p class="map-layer-meta">${features.length} adjacent parcel(s)</p>` + features.map((feature) => {
      const reference = referenceFrom(feature.properties);
      const label = feature.properties?.parcel || reference || 'Parcel';
      return `<button type="button" class="secondary-action compact" data-adjacent-reference="${escapeHtml(reference || '')}" data-adjacent-id="${escapeHtml(feature.id ?? '')}">${escapeHtml(label)}</button>`;
    }).join(' ');
    container.querySelectorAll('[data-adjacent-reference]').forEach((button) => {
      button.addEventListener('click', () => {
        const reference = button.getAttribute('data-adjacent-reference');
        const featureId = Number(button.getAttribute('data-adjacent-id'));
        if (reference) void loadParcelByReference(reference, true, Number.isInteger(featureId) && featureId > 0 ? featureId : null);
      });
    });
  }

  async function findAdjacentParcels() {
    const reference = state.selectedReference;
    if (!reference) return;
    const button = $('parcelAdjacentButton');
    if (button) { button.disabled = true; button.textContent = 'Finding…'; }
    mapStatus('Looking for adjacent parcels…');
    try {
      const featureId = Number(state.selectedFeature?.id);
      const idHint = Number.isInteger(featureId) && featureId > 0 ? `?id=${featureId}` : '';
      const response = await fetch(`/api/v1/enrichment/parcel/adjacent/${encodeURIComponent(reference)}${idHint}`);
      if (!response.ok) throw new Error('Adjacency lookup unavailable');
      const collection = await response.json();
      const features = collection.features || [];
      addAdjacentParcelsLayer();
      state.map.getSource('adjacent-parcels')?.setData(collection);
      renderAdjacentResults(features);
      mapStatus(features.length ? `${features.length} adjacent parcel(s) found` : 'No adjacent parcels found');
    } catch (error) {
      mapStatus(error.message || 'Adjacency lookup unavailable', true);
    } finally {
      if (button) { button.disabled = false; button.textContent = 'Find adjacent parcels'; }
    }
  }

  // ---- Auction/market filters (legacy Actions panel: toggleAuctionLayer,
  // filterAuctionsByType, filterAuctionsByPrice, filterActiveAuctions) ----
  const auctionOverlay = { active: false };

  function addAuctionOverlayLayer() {
    ensureOverlaySource('auction-properties');
    if (state.map.getLayer('point-auction-properties')) return;
    state.map.addLayer({ id: 'point-auction-properties', type: 'circle', source: 'auction-properties',
      paint: { 'circle-radius': ['coalesce', ['get', 'marker_size'], 8], 'circle-color': ['coalesce', ['get', 'marker_color'], '#FF6B6B'], 'circle-stroke-color': '#fff', 'circle-stroke-width': 1, 'circle-opacity': .9 } });
    state.map.on('mouseenter', 'point-auction-properties', () => { state.map.getCanvas().style.cursor = 'pointer'; });
    state.map.on('mouseleave', 'point-auction-properties', () => { state.map.getCanvas().style.cursor = ''; });
    state.map.on('click', 'point-auction-properties', (event) => {
      const feature = event.features?.[0];
      if (!feature) return;
      const props = feature.properties;
      const price = Number(props.starting_price);
      new maplibregl.Popup({ maxWidth: '280px' }).setLngLat(event.lngLat).setHTML(
        `<div class="fires-popup"><strong>${escapeHtml(props.property_type || 'Property')}</strong><div>${escapeHtml(props.municipality || '')}</div>` +
        `<div>${escapeHtml(props.status || '')}${Number.isFinite(price) ? ` · €${price.toLocaleString('it-IT')}` : ''}</div>` +
        `${props.description ? `<div>${escapeHtml(props.description)}</div>` : ''}</div>`,
      ).addTo(state.map);
    });
  }

  async function loadAuctionOverlay() {
    if (!auctionOverlay.active || !state.map) return;
    const countElement = $('auctionCount');
    try {
      const response = await fetch('/api/v1/auction-properties/');
      if (!response.ok) throw new Error('unavailable');
      const data = await response.json();
      state.map.getSource('auction-properties')?.setData(data.geojson || emptyFeatureCollection());
      if (countElement) countElement.textContent = data.count ? `(${data.count})` : '(0)';
      applyAuctionFilter();
    } catch (_) {
      if (countElement) countElement.textContent = '';
    }
  }

  function applyAuctionFilter() {
    if (!state.map?.getLayer('point-auction-properties')) return;
    const type = $('auctionTypeFilter')?.value || '';
    const activeOnly = $('auctionActiveOnly')?.checked;
    const maxPrice = Number($('auctionMaxPrice')?.value);
    const clauses = ['all'];
    if (type) clauses.push(['==', ['get', 'property_type'], type]);
    if (activeOnly) clauses.push(['==', ['get', 'status'], 'active']);
    if (Number.isFinite(maxPrice) && maxPrice > 0) clauses.push(['<=', ['to-number', ['get', 'starting_price']], maxPrice]);
    state.map.setFilter('point-auction-properties', clauses.length > 1 ? clauses : null);
  }

  function toggleAuctionOverlay() {
    const button = $('toggleAuctionLayer');
    auctionOverlay.active = !auctionOverlay.active;
    button?.classList.toggle('active', auctionOverlay.active);
    button?.setAttribute('aria-pressed', String(auctionOverlay.active));
    if (auctionOverlay.active) { addAuctionOverlayLayer(); loadAuctionOverlay(); }
    else {
      state.map.getSource('auction-properties')?.setData(emptyFeatureCollection());
      const countElement = $('auctionCount');
      if (countElement) countElement.textContent = '';
    }
  }

  // ---- Attribute table (single-layer, viewport-scoped analogue of the
  // legacy Table View / table-manager.js) ----
  const tableView = { active: false, layerId: null, rawFeatures: [], page: 1, pageSize: 100, hasMore: false, controller: null };

  function populateTableLayerSelect() {
    const select = $('mapTableLayerSelect');
    if (!select) return;
    const options = state.catalog.filter((layer) => layer.id !== 'raster-coverage');
    select.innerHTML = options.map((layer) => `<option value="${escapeHtml(layer.id)}">${escapeHtml(layer.title)}</option>`).join('');
    if (!tableView.layerId || !options.some((layer) => layer.id === tableView.layerId)) {
      // Parcels are what users inspect here; fall back to the first layer
      // only when the view is still too far out for them.
      const parcels = options.find((layer) => layer.id === 'cadastral-parcels');
      const parcelsVisible = parcels && state.map && state.map.getZoom() >= (parcels.min_zoom ?? 0);
      tableView.layerId = parcelsVisible ? parcels.id : (options[0]?.id || null);
    }
    if (tableView.layerId) select.value = tableView.layerId;
  }

  function filteredTableFeatures() {
    const query = ($('mapTableFilter')?.value || '').trim().toLowerCase();
    if (!query) return tableView.rawFeatures;
    return tableView.rawFeatures.filter((feature) => Object.values(feature.properties || {})
      .some((value) => String(value ?? '').toLowerCase().includes(query)));
  }

  function renderTable() {
    const head = $('mapTableHead');
    const body = $('mapTableBody');
    if (!head || !body) return;
    const layer = state.catalog.find((candidate) => candidate.id === tableView.layerId);
    const columns = layer?.properties || [];
    const features = filteredTableFeatures();
    head.innerHTML = `<tr>${columns.map((column) => `<th>${escapeHtml(column)}</th>`).join('')}</tr>`;
    const pageItems = features;
    body.innerHTML = pageItems.length
      ? pageItems.map((feature) => `<tr data-feature-id="${escapeHtml(feature.id ?? '')}" tabindex="0">${columns.map((column) => `<td>${escapeHtml(feature.properties?.[column])}</td>`).join('')}</tr>`).join('')
      : `<tr><td colspan="${columns.length || 1}">No features in the current view.</td></tr>`;
    const pageInfo = $('mapTablePageInfo');
    if (pageInfo) pageInfo.textContent = `Page ${tableView.page}${tableView.hasMore ? '+' : ''}`;
    $('mapTablePrevButton').disabled = tableView.page <= 1;
    $('mapTableNextButton').disabled = !tableView.hasMore;
    body.querySelectorAll('tr[data-feature-id]').forEach((row) => {
      const activate = () => {
        const id = row.getAttribute('data-feature-id');
        const feature = pageItems.find((candidate) => String(candidate.id ?? '') === id);
        if (feature) focusTableFeature(feature);
      };
      row.addEventListener('click', activate);
      row.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); activate(); }
      });
    });
  }

  function focusTableFeature(feature) {
    const layer = state.catalog.find((candidate) => candidate.id === tableView.layerId);
    const bounds = featureBounds(feature);
    if (bounds) state.map.fitBounds(bounds, { padding: 60, maxZoom: 19 });
    if (layer) showOverlayFeature(layer, feature, bounds ? bounds.getCenter() : state.map.getCenter());
  }

  async function loadTableData() {
    if (!tableView.active || !tableView.layerId || !state.map) return;
    const bounds = state.map.getBounds();
    const params = new URLSearchParams({
      west: bounds.getWest().toFixed(6), south: bounds.getSouth().toFixed(6),
      east: bounds.getEast().toFixed(6), north: bounds.getNorth().toFixed(6), limit: String(tableView.pageSize), offset: String((tableView.page - 1) * tableView.pageSize),
    });
    const status = $('mapTableStatus');
    if (status) status.textContent = 'Loading…';
    // Layer switches and map moves overlap; only the latest request may
    // render, or a slower earlier layer overwrites the current one.
    tableView.controller?.abort();
    const controller = new AbortController();
    tableView.controller = controller;
    try {
      const response = await fetch(`/api/v1/map/layers/${encodeURIComponent(tableView.layerId)}/features?${params}`, { signal: controller.signal });
      if (!response.ok) throw new Error('Attribute table unavailable');
      const data = await response.json();
      if (tableView.controller !== controller) return;
      tableView.rawFeatures = data.features || [];
      tableView.hasMore = Boolean(data.truncated);
      renderTable();
      if (status) status.textContent = data.zoom_required
        ? `Zoom in to at least level ${data.zoom_required}`
        : `${tableView.rawFeatures.length} feature(s) on page${data.truncated ? ' · more available with Next' : ''}`;
    } catch (error) {
      if (error.name === 'AbortError' || tableView.controller !== controller) return;
      tableView.rawFeatures = [];
      tableView.hasMore = false;
      renderTable();
      if (status) status.textContent = error.message || 'Attribute table unavailable';
    }
  }

  function setTableViewOpen(open) {
    tableView.active = open;
    const card = $('mapTableCard');
    if (card) card.hidden = !open;
    $('tableToggle')?.setAttribute('aria-expanded', String(open));
    if (open) {
      populateTableLayerSelect();
      void loadTableData();
    }
  }

  // Only the mobile breakpoint (<=700px) hides .parcel-shortlist-card by
  // default and reveals it as a bottom sheet via .mobile-open; on desktop/
  // tablet it stays inline and this class has no visual effect.
  function setShortlistOpen(open) {
    const card = $('parcelShortlistCard');
    if (!card) return;
    card.classList.toggle('mobile-open', open);
    card.hidden = !open;
    $('shortlistToggle')?.setAttribute('aria-expanded', String(open));
    if (open) loadShortlist();
  }

  function setupControls() {
    populatePoiCategories();
    $('mapSearchForm').addEventListener('submit', (event) => {
      event.preventDefault();
      // With results already listed, Enter picks the first one (combobox
      // convention) instead of re-running the same query.
      const options = $('mapSearchResults').hidden ? [] : [...$('mapSearchResults').querySelectorAll('.map-search-result')];
      const active = options[state.searchActiveIndex] || options[0];
      if (active && !state.searchTimer) { active.click(); return; }
      submitSearch($('mapSearchInput').value, true);
    });
    $('mapSearchInput').addEventListener('keydown', (event) => {
      const options = [...$('mapSearchResults').querySelectorAll('.map-search-result')];
      if (event.key === 'Escape') { renderSearchResults([]); state.searchActiveIndex = -1; return; }
      if ((event.key === 'ArrowDown' || event.key === 'ArrowUp') && options.length) {
        event.preventDefault();
        const direction = event.key === 'ArrowDown' ? 1 : -1;
        state.searchActiveIndex = Math.max(0, Math.min(options.length - 1, state.searchActiveIndex + direction));
        options.forEach((option, index) => option.setAttribute('aria-selected', String(index === state.searchActiveIndex)));
        $('mapSearchInput').setAttribute('aria-activedescendant', options[state.searchActiveIndex].id);
      }
    });
    $('mapSearchInput').addEventListener('input', () => { clearTimeout(state.searchTimer); state.searchTimer = setTimeout(() => submitSearch($('mapSearchInput').value), SEARCH_DEBOUNCE_MS); });
    $('locateButton').addEventListener('click', locate);
    $('shareButton').addEventListener('click', copyLink);
    $('parcelSaveButton').addEventListener('click', saveParcel);
    $('parcelAdjacentButton').addEventListener('click', findAdjacentParcels);
    $('parcelShareButton').addEventListener('click', copyLink);
    $('parcelClearButton').addEventListener('click', () => { clearParcelSelection(); $('mapSearchInput').focus(); });
    $('toggleEnrichmentPois').addEventListener('click', togglePoiOverlay);
    $('openPoiExplorer')?.addEventListener('click', openPoiExplorer);
    $('poiExportJson')?.addEventListener('click', () => exportPoi('json'));
    $('poiExportPdf')?.addEventListener('click', () => exportPoi('pdf'));
    $('poiRadius')?.addEventListener('change', () => { if (enrichmentOverlays.poi.active) refreshPoiOverlay(); });
    $('poiCategories')?.addEventListener('change', () => { if (enrichmentOverlays.poi.active) refreshPoiOverlay(); });
    $('toggleEnrichmentFires').addEventListener('click', toggleFiresOverlay);
    $('refreshFiresButton').addEventListener('click', refreshFiresOverlay);
    $('toggleEnrichmentBulletin').addEventListener('click', toggleBulletinOverlay);
    $('refreshBulletinButton').addEventListener('click', refreshBulletinOverlay);
    $('toggleAuctionLayer').addEventListener('click', toggleAuctionOverlay);
    $('toggleSalesLayer')?.addEventListener('click', toggleSalesOverlay);
    $('refreshSalesButton')?.addEventListener('click', () => loadSalesOverlay());
    $('salesSort')?.addEventListener('change', () => { if (salesOverlay.active) loadSalesOverlay(); });
    $('salesSaleabilityMin')?.addEventListener('input', () => { if (salesOverlay.active) state.map?.getSource('sales-properties')?.setData(salesGeoJson()); });
    $('salesGeoLayerType')?.addEventListener('change', renderSalesGeoLayers);
    $('salesGeoMetric')?.addEventListener('change', renderSalesGeoLayers);
    $('googleMapsButton')?.addEventListener('click', () => openMapAt('google'));
    $('streetViewButton')?.addEventListener('click', () => openMapAt('street'));
    $('auctionTypeFilter').addEventListener('change', applyAuctionFilter);
    $('auctionActiveOnly').addEventListener('change', applyAuctionFilter);
    $('auctionMaxPrice').addEventListener('change', applyAuctionFilter);
    $('tableToggle').addEventListener('click', () => setTableViewOpen($('mapTableCard').hidden));
    $('mapTableCloseButton').addEventListener('click', () => setTableViewOpen(false));
    $('mapTableCard').addEventListener('keydown', (event) => {
      if (event.key !== 'Escape') return;
      setTableViewOpen(false);
      $('tableToggle').focus();
    });
    $('mapTableLayerSelect').addEventListener('change', (event) => { tableView.layerId = event.target.value; tableView.page = 1; void loadTableData(); });
    $('mapTableFilter').addEventListener('input', () => { tableView.page = 1; renderTable(); });
    $('mapTableRefreshButton').addEventListener('click', () => { tableView.page = 1; void loadTableData(); });
    $('mapTablePrevButton').addEventListener('click', () => { if (tableView.page > 1) { tableView.page -= 1; void loadTableData(); } });
    $('mapTableNextButton').addEventListener('click', () => { if (tableView.hasMore) { tableView.page += 1; void loadTableData(); } });
    $('shortlistRefreshButton').addEventListener('click', () => loadShortlist());
    ['shortlistStatusFilter', 'shortlistPriorityFilter', 'shortlistSort'].forEach((id) => {
      $(id).addEventListener('change', renderShortlist);
    });
    $('shortlistHazardFilter').addEventListener('change', () => loadShortlist());
    $('shortlistToggle')?.addEventListener('click', () => {
      if (!window.landRegistrySignedIn) {
        const next = encodeURIComponent(window.location.pathname + window.location.search);
        window.location.assign(`/auth/login?next=${next}`);
        return;
      }
      setShortlistOpen($('parcelShortlistCard').hidden);
    });
    $('shortlistCloseButton')?.addEventListener('click', () => setShortlistOpen(false));
    $('resetButton').addEventListener('click', () => {
      if (state.map) state.map.fitBounds(ITALY_BOUNDS, { padding: 20 });
      else if (state.fallbackMap) state.fallbackMap.fitBounds([[36.3, 6.5], [47.2, 18.6]], { padding: [20, 20] });
    });
    $('parcelCloseButton').addEventListener('click', () => { $('directParcelPanel').hidden = true; updateParcelZoomAffordance(); mapStatus(''); });
    $('layersCloseButton').addEventListener('click', () => { $('mapLayersCard').hidden = true; $('layersToggle').setAttribute('aria-expanded', 'false'); });
    $('layersToggle').addEventListener('click', () => { const card = $('mapLayersCard'); card.hidden = !card.hidden; $('layersToggle').setAttribute('aria-expanded', String(!card.hidden)); });
    document.querySelectorAll('input[name="basemap"]').forEach((input) => input.addEventListener('change', () => switchBasemap(input.value)));
    // The shared aecs4u-theme owns the navbar theme toggle. Keep the map
    // basemap in sync when the selected basemap is light/dark, while leaving
    // satellite imagery unchanged.
    if (!window.landRegistrySignedIn) {
      const salesButton = $('toggleSalesLayer');
      if (salesButton) { salesButton.disabled = true; salesButton.title = 'Sign in to use sales data'; salesButton.setAttribute('aria-disabled', 'true'); }
      const shortlist = $('parcelShortlistCard');
      if (shortlist) shortlist.hidden = true;
      const shortlistToggle = $('shortlistToggle');
      shortlistToggle?.classList.add('is-signed-out');
      if (shortlistToggle) { shortlistToggle.innerHTML = '<span aria-hidden="true">☰</span> Sign in to use your shortlist'; shortlistToggle.title = 'Sign in to use your shortlist'; }
    } else {
      $('shortlistToggle')?.classList.add('is-collapsible');
      const close = $('shortlistCloseButton');
      if (close) close.style.display = 'inline-block';
    }
    $('themeToggle')?.addEventListener('click', () => {
      window.setTimeout(() => {
        state.dark = document.documentElement.getAttribute('data-bs-theme') === 'dark';
        document.body.classList.toggle('direct-map-dark', state.dark);
        $('themeToggle')?.setAttribute('aria-pressed', String(state.dark));
        const basemap = currentBasemap();
        if (basemap !== 'satellite' && basemap !== (state.dark ? 'dark' : 'light')) {
          const radio = document.querySelector(`input[name="basemap"][value="${state.dark ? 'dark' : 'light'}"]`);
          if (radio) radio.checked = true;
          if (state.map) switchBasemap(state.dark ? 'dark' : 'light');
        }
      }, 0);
    });
  }

  function switchBasemap(kind) {
    if (!state.map && state.fallbackMap) {
      if (state.fallbackBasemap) state.fallbackMap.removeLayer(state.fallbackBasemap);
      const tiles = cartoTiles(kind) || BASEMAP_TILES[kind] || BASEMAP_TILES.light;
      state.fallbackBasemap = L.tileLayer(tiles.base, {
        maxZoom: 22,
        maxNativeZoom: tiles.maxzoom,
        attribution: tiles.attribution,
      }).addTo(state.fallbackMap);
      return;
    }
    const center = state.map.getCenter(); const zoom = state.map.getZoom();
    state.layerFailures.clear();
    state.map.setStyle(styleForBasemap(kind));
    state.map.once('style.load', () => { state.map.jumpTo({ center, zoom }); renderLayerCatalog(); if (state.selectedFeature) renderSelectedGeometry(state.selectedFeature); if (salesOverlay.active) void loadSalesOverlay(); if (salesOverlay.geo) renderSalesGeoLayers(); });
  }

  async function loadCatalog() {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8000);
    try {
      // Keep the canonical catalog endpoint explicit for deployment checks.
      // fetch('/api/v1/map/layers')
      const response = await fetch('/api/v1/map/layers', {
        credentials: 'same-origin',
        signal: controller.signal,
      });
      if (!response.ok) throw new Error('catalog unavailable');
      const payload = await response.json();
      if (!Array.isArray(payload.layers)) throw new Error('invalid layer catalog');
      state.catalog = payload.layers;
      renderLayerCatalog();
    } catch (_) {
      // Never leave the overlays panel stuck on its initial loading message
      // when the API is slow, canceled, or temporarily unavailable.
      $('mapLayerList').innerHTML = '<p class="map-muted">Layer catalog unavailable. The basemap remains usable.</p>';
      mapStatus('Map layer catalog unavailable', true);
    } finally {
      clearTimeout(timeout);
    }
  }

  // Signed-out visitors and a slow store both fall back to built-in defaults.
  async function loadPreferences() {
    if (!window.landRegistrySignedIn) return null;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 1500);
    try {
      const response = await fetch('/api/v1/user/preferences', { signal: controller.signal, credentials: 'same-origin' });
      if (!response.ok) return null;
      const payload = await response.json();
      return payload.preferences || null;
    } catch (_) {
      return null;
    } finally {
      clearTimeout(timeout);
    }
  }

  function readLastView() {
    try {
      const value = JSON.parse(window.localStorage.getItem(LAST_VIEW_KEY) || 'null');
      const center = value?.center;
      const zoom = Number(value?.zoom);
      if (value && Array.isArray(center) && center.length === 2
        && Number.isFinite(Number(center[0])) && Number.isFinite(Number(center[1]))
        && Number(center[0]) >= ITALY_BOUNDS[0][0] && Number(center[0]) <= ITALY_BOUNDS[1][0]
        && Number(center[1]) >= ITALY_BOUNDS[0][1] && Number(center[1]) <= ITALY_BOUNDS[1][1]
        && Number.isFinite(zoom)) {
        return { center: [Number(center[0]), Number(center[1])], zoom };
      }
    } catch (_) {
      // Storage can be unavailable in private windows.
    }
    return null;
  }

  function rememberView() {
    const map = state.map || state.fallbackMap;
    if (!map || state.preferences?.start_view !== 'last') return;
    const center = map.getCenter();
    try {
      window.localStorage.setItem(LAST_VIEW_KEY, JSON.stringify({ center: [center.lng, center.lat], zoom: map.getZoom() }));
    } catch (_) {
      // Storage can be unavailable in private windows.
    }
  }

  function centreOnUserLocation() {
    if (!navigator.geolocation) return;
    navigator.geolocation.getCurrentPosition(
      (position) => {
        if (state.map) state.map.flyTo({ center: [position.coords.longitude, position.coords.latitude], zoom: 15, duration: 900 });
        else state.fallbackMap?.flyTo([position.coords.latitude, position.coords.longitude], 15, { duration: 0.9 });
      },
      () => mapStatus('Location unavailable; showing Italy.'),
      { enableHighAccuracy: false, maximumAge: 300000, timeout: 8000 },
    );
  }

  function applyInitialBasemap(kind) {
    state.dark = document.documentElement.getAttribute('data-bs-theme') === 'dark';
    try { window.localStorage.removeItem('cadastre_dark_mode'); } catch (_) { /* storage may be disabled */ }
    document.body.classList.toggle('direct-map-dark', state.dark);
    $('themeToggle')?.setAttribute('aria-pressed', String(state.dark));
    const radio = document.querySelector(`input[name="basemap"][value="${kind}"]`);
    if (radio) radio.checked = true;
  }

  function loadScriptOnce(src) {
    return new Promise((resolve, reject) => {
      const existing = [...document.scripts].find((script) => script.src === src);
      if (existing?.dataset.loaded === 'true') return resolve();
      const script = existing || document.createElement('script');
      script.src = src;
      script.async = false;
      script.onload = () => { script.dataset.loaded = 'true'; resolve(); };
      script.onerror = () => reject(new Error(`Could not load ${src}`));
      if (!existing) document.head.appendChild(script);
    });
  }

  async function ensureLeafletFallback() {
    if (!document.querySelector('link[data-map-leaflet]')) {
      const link = document.createElement('link');
      link.rel = 'stylesheet';
      link.href = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';
      link.dataset.mapLeaflet = 'true';
      document.head.appendChild(link);
    }
    if (!window.L) await loadScriptOnce('https://unpkg.com/leaflet@1.9.4/dist/leaflet.js');
    if (!window.L?.vectorGrid) await loadScriptOnce('https://unpkg.com/leaflet.vectorgrid@1.3.0/dist/Leaflet.VectorGrid.bundled.js');
  }

  function createFallbackMap(initialView, basemap) {
    if (!window.L) throw new Error('Leaflet fallback library unavailable');
    const container = $('directMap');
    container.replaceChildren();
    state.fallbackMap = L.map(container, {
      zoomControl: false,
      maxBounds: [[36.3, 6.5], [47.2, 18.6]],
      minZoom: 5,
      maxZoom: 22,
    });
    state.fallbackMap.setView([initialView.center[1], initialView.center[0]], initialView.zoom);
    state.fallbackMap.addControl(L.control.zoom({ position: 'bottomright' }));
    switchBasemap(basemap);
    state.fallbackMap.on('moveend', writeUrl);
    state.fallbackMap.on('moveend', rememberView);
    state.fallbackMap.on('moveend', () => { if (tableView.active) void loadTableData(); });
    state.fallbackMap.on('zoomend', updateParcelZoomAffordance);
  }

  async function finishMapInit(restored) {
    if (state.map) {
      state.map.resize();
      if (!restored.hasView) writeUrl();
    } else if (!restored.hasView) {
      writeUrl();
    }
    if (restored.invalidView) mapStatus('Location outside Italy — showing Rome.', true);
    else if (restored.invalidParcel) mapStatus('Invalid parcel reference — selection was not loaded.', true);
    else mapStatus(state.fallbackMap ? 'Map ready (raster mode)' : 'Map ready');
    await loadCatalog();
    if (salesOverlay.active) { void loadSalesGeoLayers(); void loadSalesOverlay(); }
    void loadShortlist();
    void loadLayerHealth();
    if (restored.parcel && !restored.invalidParcel) await loadParcelByReference(restored.parcel, false, restored.parcelId);
    else if (!restored.hasView && state.preferences?.start_view === 'geolocate') centreOnUserLocation();
  }

  async function init() {
    if (state.initializing || state.map || state.fallbackMap) return;
    if (!$('directMap')) return;
    observeMapNavigationAccessibility();
    state.initializing = true;
    try {
      setupControls();
    } catch (error) {
      console.error('Map controls failed to initialise', error);
      mapStatus('Map controls could not be initialised; please reload the page.', true);
      state.initializing = false;
      return;
    }
    const restored = urlState();
    state.preferences = await loadPreferences();
    const basemap = BASEMAPS.includes(state.preferences?.default_basemap) ? state.preferences.default_basemap : (document.documentElement.getAttribute('data-bs-theme') === 'dark' ? 'dark' : 'light');
    if (Array.isArray(state.preferences?.default_layers)) state.defaultLayers = new Set(state.preferences.default_layers);
    applyInitialBasemap(basemap);
    let initialView = restored;
    if (!restored.hasView && state.preferences?.start_view === 'last') {
      const last = readLastView();
      if (last) initialView = { ...restored, center: last.center, zoom: Math.min(22, Math.max(5, last.zoom)) };
    }
    let maplibreError = null;
    if (window.maplibregl) {
      try {
        state.map = new maplibregl.Map({ container: 'directMap', style: styleForBasemap(basemap), center: initialView.center, zoom: initialView.zoom, maxBounds: ITALY_BOUNDS, minZoom: 5, maxZoom: 22, attributionControl: true });
      } catch (error) {
        maplibreError = error;
        console.warn('MapLibre unavailable; switching to raster map fallback', error);
      }
    }
    if (!state.map) {
      try {
        await ensureLeafletFallback();
        createFallbackMap(initialView, basemap);
      } catch (fallbackError) {
        console.error('Map failed to initialise', maplibreError || fallbackError);
        const reason = maplibreError instanceof Error && maplibreError.message ? ` (${maplibreError.message})` : '';
        mapStatus(`Map could not be initialised${reason}`, true);
        state.initializing = false;
        return;
      }
    }
    state.initializing = false;
    if (state.fallbackMap) {
      setupMapResizeObserver();
      window.addEventListener('resize', () => state.fallbackMap?.invalidateSize());
      void finishMapInit(restored);
      return;
    }
    setupMapResizeObserver();
    window.addEventListener('resize', () => state.map?.resize());
    state.map.addControl(new maplibregl.NavigationControl({ visualizePitch: false }), 'bottom-right');
    state.map.addControl(new maplibregl.FullscreenControl(), 'bottom-right');
    state.map.on('load', () => { state.tilesLoading = true; mapStatus('Loading map tiles…'); void finishMapInit(restored); });
    state.map.on('dataloading', (event) => { if (event.dataType === 'source' && event.tile) state.tilesLoading = true; });
    state.map.on('sourcedata', (event) => {
      if (event.sourceId && event.sourceDataType === 'content') { state.tileErrors.delete(event.sourceId); tileRetry.attempts[event.sourceId] = 0; }
    });
    // React only when a tile-loading phase has just settled. Doing this work
    // on every 'idle' formed a render loop (the affordance update touches
    // layer layout, which re-renders and idles again) and overwrote every
    // other status message with "Map ready" within a frame.
    state.map.on('idle', () => {
      if (!state.tilesLoading) return;
      state.tilesLoading = false;
      if (!state.mapReadyAnnounced && !state.tileErrors.size && !restored.invalidView && !restored.parcel) {
        state.mapReadyAnnounced = true;
        mapStatus('Map ready');
      } else if ($('mapStatus')?.textContent === 'Loading map tiles…') {
        mapStatus('');
      }
      updateParcelZoomAffordance();
    });
    // MapLibre never re-requests a tile that failed (e.g. a cold-cache 503),
    // which left silent holes in overlays; retry the source and say so.
    state.map.on('error', (event) => {
      if (event.sourceId && event.tile && !String(event.sourceId).startsWith('basemap')) {
        state.tileErrors.add(event.sourceId);
        scheduleTileRetry(event.sourceId);
      }
    });
    state.map.on('moveend', () => { tileRetry.attempts = {}; state.tileErrors.clear(); });
    state.map.on('moveend', writeUrl);
    state.map.on('moveend', rememberView);
    state.map.on('moveend', () => { if (tableView.active) { tableView.page = 1; void loadTableData(); } });
    state.map.on('moveend', updateParcelZoomAffordance);
    state.map.on('click', (event) => {
      const features = state.map.queryRenderedFeatures(event.point, { layers: state.map.getLayer('fill-cadastral-parcels') ? ['fill-cadastral-parcels'] : [] });
      if (features[0]) {
        identifyAtPoint(event.lngLat, features[0]);
        return;
      }
      const overlayLayers = state.catalog
        .filter((layer) => layer.id !== 'cadastral-parcels' && (state.map.getLayer(`fill-${layer.id}`) || state.map.getLayer(`point-${layer.id}`)))
        .map((layer) => state.map.getLayer(`fill-${layer.id}`) ? `fill-${layer.id}` : `point-${layer.id}`);
      const overlayFeature = overlayLayers.length ? state.map.queryRenderedFeatures(event.point, { layers: overlayLayers })[0] : null;
      if (overlayFeature) {
        const layer = state.catalog.find((candidate) => overlayFeature.layer?.id?.endsWith(candidate.id));
        if (layer) showOverlayFeature(layer, overlayFeature, event.lngLat);
        return;
      }
      if (state.map.getZoom() < parcelMinZoom()) {
        mapStatus('Zoom in to select a parcel');
        return;
      }
      identifyAtPoint(event.lngLat, null);
    });
  }

  document.addEventListener('DOMContentLoaded', init);
})();
