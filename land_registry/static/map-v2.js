/* Direct cadastral map experience.
 *
 * This page deliberately owns one MapLibre instance.  It consumes the
 * allow-listed map catalog and parcel/enrichment APIs. Its workbench keeps
 * selection and drawing edits in the current viewport until the user exports
 * GeoJSON or saves drawings through the existing authenticated API.
 */
(function () {
  'use strict';

  const ITALY_BOUNDS = [[6.5, 36.3], [18.6, 47.2]];
  const DEFAULT_CENTER = [12.4964, 41.9028];
  const DEFAULT_ZOOM = 5.7;
  // Individual parcel outlines are too dense for a raster fallback below
  // this zoom; the canonical MVT layer retains its server-provided threshold.
  const PARCEL_MIN_ZOOM = 16;
  const ADMIN_SUBSTITUTE_LAYER_IDS = ['geo-boundaries'];
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
    selectedAuctionSaleMarker: null,
    savedReference: null,
    searchTimer: null,
    searchRequest: 0,
    searchController: null,
    searchActiveIndex: -1,
    initializing: false,
    layerFailures: new Set(),
    activeLayers: null,
    adminSubstituteAutoLayers: new Set(),
    adminSubstituteUserDisabled: new Set(),
    shortlistItems: [],
    shortlistStatuses: [],
    shortlistSummary: null,
    dark: false,
    preferences: null,
    defaultLayers: new Set(['cadastral-parcels']),
    resizeObserver: null,
    statusTimer: null,
    tileErrors: new Set(),
    unavailableLayerIds: new Set(),
    // Two independent signals that the map data source is down: the health
    // endpoint, and tiles that kept failing after their retries ran out.
    sourceHealthDown: false,
    sourceTileDown: false,
    layerOpacity: new Map(),
    postalLabelsEnabled: false,
    cadastralSheetLabelsEnabled: false,
    parcelPvpMarkers: { points: [], visible: false },
    parcelPvpMarkerLayer: null,
    sisterBatch: { features: new Map(), mode: null, vertices: [], circleCenter: null, busy: false, requestId: 0, notice: '' },
    sisterBatchFallbackLayer: null,
    sisterBatchDrawingFallbackLayer: null,
    workbench: {
      selection: new Map(), mode: null, vertices: [], circleCenter: null,
      drawings: [], selectedDrawing: null, editingDrawing: false, editVertex: null,
      drawingLayer: null, drawingVertexLayer: null, fallbackDrawingLayer: null,
      fallbackSelectionLayer: null, fallbackLayers: new Map(), minimap: null, minimapViewport: null,
      adjacencyRequest: 0,
    },
  };

  const $ = (id) => document.getElementById(id);
  // Server-rendered strings (window._i18n) win; the English literal is the fallback.
  const tr = (key, values) => {
    const text = (window._i18n && window._i18n[key]) || key;
    return values ? text.replace(/\{(\w+)\}/g, (_, name) => values[name] ?? '') : text;
  };
  const LAYER_GROUPS = ['administrative', 'cadastral', 'market', 'risk', 'demographics', 'energy', 'territory'];

  // Progress belongs to a task, so an older request cannot dismiss a newer
  // one. Quick operations stay invisible; elapsed time is not an estimate.
  const mapTasks = new Map();
  let mapTaskTimer = null;

  function renderMapProgress() {
    const panel = $('mapTaskProgress');
    if (!panel) return;
    const now = performance.now();
    const tasks = [...mapTasks.values()].filter((task) => now - task.started >= 500);
    panel.hidden = !tasks.length;
    if (!tasks.length) return;
    const task = tasks[0];
    const label = $('mapTaskProgressLabel');
    if (label.textContent !== task.name) label.textContent = task.name;
    $('mapTaskProgressElapsed').textContent = `${Math.floor((now - task.started) / 1000)}s`;
    const bar = $('mapTaskProgressBar');
    bar.classList.toggle('is-indeterminate', task.percent === null);
    bar.setAttribute('aria-valuetext', task.percent === null ? task.label : `${task.label} ${Math.round(task.percent)}%`);
    if (task.percent === null) bar.removeAttribute('aria-valuenow');
    else bar.setAttribute('aria-valuenow', String(Math.round(task.percent)));
    $('mapTaskProgressFill').style.width = task.percent === null ? '' : `${task.percent}%`;
    $('mapTaskProgressDetail').textContent = [
      task.label !== task.name ? task.label : '',
      task.detail,
      tasks.length > 1 ? tr('{n} tasks in progress', { n: tasks.length }) : '',
    ].filter(Boolean).join(' · ');
  }

  function beginMapProgress(key, label) {
    mapTasks.get(key)?.finish();
    const task = {
      started: performance.now(), name: tr(label), label: tr(label), percent: null, detail: '', cleanup: null,
      current: () => mapTasks.get(key) === task,
      update(message, percent = null, detail = '') {
        if (!task.current()) return;
        task.label = tr(message);
        task.percent = percent === null ? null : Math.max(0, Math.min(100, percent));
        task.detail = detail;
        renderMapProgress();
      },
      finish() {
        task.cleanup?.();
        task.cleanup = null;
        if (task.current()) mapTasks.delete(key);
        if (!mapTasks.size) { clearInterval(mapTaskTimer); mapTaskTimer = null; }
        renderMapProgress();
      },
    };
    mapTasks.set(key, task);
    if (mapTaskTimer === null) mapTaskTimer = setInterval(renderMapProgress, 250);
    renderMapProgress();
    return task;
  }

  async function readMapJson(response, task) {
    if (!response.body?.getReader) return response.json();
    const length = Number(response.headers.get('Content-Length'));
    const encoding = response.headers.get('Content-Encoding');
    const total = length > 0 && (!encoding || encoding === 'identity') ? length : null;
    const reader = response.body.getReader();
    const cancelReader = () => { void reader.cancel().catch(() => {}); };
    task.cleanup = cancelReader;
    const decoder = new TextDecoder();
    const chunks = [];
    let received = 0;
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        chunks.push(decoder.decode(value, { stream: true }));
        received += value.byteLength;
        task.update('Downloading map records…', total ? received / total * 100 : null,
          tr('{n} MB received', { n: (received / 1048576).toFixed(1) }));
      }
      chunks.push(decoder.decode());
    } finally {
      if (task.cleanup === cancelReader) task.cleanup = null;
      reader.releaseLock();
    }
    if (!task.current()) throw new DOMException('Map task superseded', 'AbortError');
    task.update('Reading map records…');
    await new Promise((resolve) => setTimeout(resolve, 0));
    return JSON.parse(chunks.join(''));
  }

  async function prepareSalesPoints(payload, points, task, markPvp = false) {
    const features = [];
    for (let offset = 0; offset < points.length; offset += 5000) {
      if (!task.current()) return null;
      expandPvpSalesPoints(payload, points.slice(offset, offset + 5000)).forEach((point) => {
        const feature = salesPointFeature(point);
        if (!feature) return;
        if (markPvp) feature.properties.pvp_record = true;
        features.push(feature);
      });
      const completed = Math.min(offset + 5000, points.length);
      task.update('Preparing sale markers…', completed / points.length * 100,
        tr('{n} of {total} records', { n: completed.toLocaleString(), total: points.length.toLocaleString() }));
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
    return task.current() ? features : null;
  }

  function drawSaleMarkers(sourceId, task, draw) {
    task.update('Drawing sale markers…');
    // GeoJSON clustering happens in a worker after setData returns. Keep the
    // bar until that source reports completion, rather than claiming 100%.
    return new Promise((resolve, reject) => {
      const map = state.map;
      const cleanup = () => {
        map.off('sourcedata', onData);
        map.off('error', onError);
        task.cleanup = null;
      };
      const onData = (event) => {
        if (event.sourceId === sourceId && event.sourceDataType === 'content' && event.isSourceLoaded) { cleanup(); resolve(); }
      };
      const onError = (event) => {
        if (event.sourceId !== sourceId) return;
        cleanup();
        reject(event.error || new Error('Sale markers could not be drawn'));
      };
      task.cleanup = () => { cleanup(); resolve(); };
      map.on('sourcedata', onData);
      map.on('error', onError);
      try { draw(); } catch (error) { cleanup(); reject(error); }
    });
  }

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
    const hidden = document.body.classList.contains('sidebar-hidden')
      || document.body.classList.contains('sidebar-collapsed')
      || sidebar.classList.contains('collapsed');
    toggle.setAttribute('aria-expanded', String(!hidden));
    sidebar.inert = hidden;
    sidebar.setAttribute('aria-hidden', String(hidden));
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
    const displayedMessage = typeof message === 'string' ? tr(message) : message;
    clearTimeout(state.statusTimer);
    element.textContent = displayedMessage || '';
    element.classList.toggle('is-error', error);
    if (displayedMessage && !error) {
      state.statusTimer = setTimeout(() => { if (element.textContent === displayedMessage) element.textContent = ''; }, 4000);
    }
  };

  // One persistent notice for "the data source is down", instead of per-layer
  // guesses ("not configured", "no parcels published") or an empty status pill.
  function updateSourceBanner() {
    const down = state.sourceHealthDown || state.sourceTileDown;
    let banner = $('mapSourceBanner');
    if (!banner && down) {
      banner = document.createElement('div');
      banner.id = 'mapSourceBanner';
      banner.className = 'map-source-banner';
      banner.setAttribute('role', 'status');
      const message = document.createElement('span');
      message.textContent = tr('Map data is temporarily unavailable. The basemap still works; layers will return when the service recovers.');
      const retry = document.createElement('button');
      retry.type = 'button';
      retry.className = 'secondary-action compact';
      retry.id = 'mapSourceRetry';
      retry.textContent = tr('Retry');
      retry.addEventListener('click', retrySourceTiles);
      banner.append(message, retry);
      ($('directMapShell')?.querySelector('.direct-map-stage') || document.body).appendChild(banner);
    }
    if (banner) banner.hidden = !down;
    const table = $('mapTableCard');
    if (table) table.classList.toggle('is-source-down', down);
  }

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
    const requestedBasemap = params.get('basemap');
    const basemapSelector = $('basemapEngineSelect');
    const basemap = requestedBasemap && basemapSelector
      && [...basemapSelector.options].some((option) => option.value === requestedBasemap)
      ? requestedBasemap : null;
    const layers = params.has('layers')
      ? params.get('layers').split(',').map((layerId) => layerId.trim()).filter(Boolean)
      : null;
    const hasView = Number.isFinite(lat) && Number.isFinite(lng)
      && lat >= ITALY_BOUNDS[0][1] && lat <= ITALY_BOUNDS[1][1]
      && lng >= ITALY_BOUNDS[0][0] && lng <= ITALY_BOUNDS[1][0];
    return {
      center: hasView ? [lng, lat] : DEFAULT_CENTER,
      zoom: Number.isFinite(zoom) ? Math.min(22, Math.max(5, zoom)) : DEFAULT_ZOOM,
      basemap,
      layers,
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
    params.set('basemap', currentBasemap());
    if (state.activeLayers !== null) {
      params.set('layers', [...state.activeLayers].sort().join(','));
    }
    if (state.selectedReference) {
      params.set('parcel', state.selectedReference);
      if (state.selectedFeature?.id != null) params.set('parcel_id', String(state.selectedFeature.id));
      else params.delete('parcel_id');
    } else { params.delete('parcel'); params.delete('parcel_id'); }
    window.history.replaceState(null, '', `${window.location.pathname}?${params.toString()}${window.location.hash}`);
  }

  function writeBasemapUrl() {
    const params = new URLSearchParams(window.location.search);
    params.set('basemap', currentBasemap());
    window.history.replaceState(null, '', `${window.location.pathname}?${params.toString()}${window.location.hash}`);
  }

  function writeSelectedLayersUrl() {
    if (state.activeLayers === null) return;
    const params = new URLSearchParams(window.location.search);
    params.set('layers', [...state.activeLayers].sort().join(','));
    window.history.replaceState(null, '', `${window.location.pathname}?${params.toString()}${window.location.hash}`);
  }

  function currentBasemap() {
    return $('basemapEngineSelect')?.value || (state.dark ? 'dark' : 'light');
  }

  // Keep V2 usable if the shared workbench bundle fails to load. This catalog
  // mirrors map-workbench.js so the selector still works during that failure.
  function fallbackBasemapEngines() {
    const esri = 'https://server.arcgisonline.com/ArcGIS/rest/services';
    const engines = [
      {
        id: 'light', base: `${esri}/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}`,
        labels: `${esri}/Canvas/World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}`,
        maxzoom: 16, displayMaxzoom: 19, background: '#f2f2ef',
        attribution: '© Esri, HERE, Garmin, © OpenStreetMap contributors',
      },
      {
        id: 'dark', base: `${esri}/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}`,
        labels: `${esri}/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}`,
        maxzoom: 16, displayMaxzoom: 19, background: '#1a1a1a',
        attribution: '© Esri, HERE, Garmin, © OpenStreetMap contributors',
      },
      {
        id: 'satellite', base: `${esri}/World_Imagery/MapServer/tile/{z}/{y}/{x}`,
        maxzoom: 19, background: '#3d3d3d', attribution: '© Esri, Maxar, Earthstar Geographics',
      },
      {
        id: 'osm', base: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
        maxzoom: 19, displayMaxzoom: 22, background: '#e9e4d6', attribution: '© OpenStreetMap contributors',
      },
      {
        id: 'google-road', base: 'https://mt1.google.com/vt/lyrs=m&x={x}&y={y}&z={z}',
        maxzoom: 20, displayMaxzoom: 22, background: '#e9e4d6', attribution: '© Google',
      },
      {
        id: 'google-satellite', base: 'https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}',
        maxzoom: 20, displayMaxzoom: 22, background: '#3d3d3d', attribution: '© Google',
      },
      {
        id: 'google-terrain', base: 'https://mt1.google.com/vt/lyrs=p&x={x}&y={y}&z={z}',
        maxzoom: 20, displayMaxzoom: 22, background: '#e9e4d6', attribution: '© Google',
      },
      {
        id: 'google-hybrid', base: 'https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}',
        maxzoom: 20, displayMaxzoom: 22, background: '#3d3d3d', attribution: '© Google',
      },
      {
        id: 'google-transit', base: 'https://mt1.google.com/vt/lyrs=m,transit&x={x}&y={y}&z={z}',
        maxzoom: 20, displayMaxzoom: 22, background: '#e9e4d6', attribution: '© Google',
      },
      {
        id: 'google-traffic', base: 'https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}',
        maxzoom: 20, displayMaxzoom: 22, background: '#e9e4d6', attribution: '© Google',
      },
      {
        id: 'esri-terrain', base: 'https://services.arcgisonline.com/ArcGIS/rest/services/World_Terrain_Base/MapServer/tile/{z}/{y}/{x}',
        maxzoom: 13, displayMaxzoom: 22, background: '#e9e4d6', attribution: '© ESRI',
      },
    ];
    if (window.cartoEnabled && window.cartoApiKey) {
      const key = encodeURIComponent(window.cartoApiKey);
      engines.push(
        {
          id: 'carto-light', base: `https://cartodb-basemaps-{s}.global.ssl.fastly.net/light_all/{z}/{x}/{y}.png?api_key=${key}`,
          maxzoom: 20, displayMaxzoom: 22, background: '#f2f2ef', attribution: '© OpenStreetMap contributors © CARTO',
        },
        {
          id: 'carto-dark', base: `https://cartodb-basemaps-{s}.global.ssl.fastly.net/dark_all/{z}/{x}/{y}.png?api_key=${key}`,
          maxzoom: 20, displayMaxzoom: 22, background: '#1a1a1a', attribution: '© OpenStreetMap contributors © CARTO',
        },
      );
    }
    return engines;
  }

  function basemapEngine(kind) {
    let engines;
    try {
      engines = window.MapWorkbench?.basemapEngines(window.cartoEnabled, window.cartoApiKey);
    } catch (error) {
      console.warn('Shared basemap catalog failed; using the V2 fallback catalog', error);
    }
    if (!Array.isArray(engines) || !engines.length) engines = fallbackBasemapEngines();
    return engines.find((engine) => engine.id === kind) || engines.find((engine) => engine.id === 'light');
  }

  function styleForBasemap(kind) {
    const tiles = basemapEngine(kind);
    if (!tiles) throw new Error('Basemap catalog is unavailable');
    // Leaflet expands {s}; MapLibre raster sources use concrete tile URLs.
    const base = tiles.base.replace('{s}', 'a');
    const sources = {
      basemap: { type: 'raster', tiles: [base], tileSize: 256, maxzoom: tiles.maxzoom, attribution: tiles.attribution },
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
      sources['basemap-labels'] = { type: 'raster', tiles: [tiles.labels.replace('{s}', 'a')], tileSize: 256, maxzoom: tiles.maxzoom };
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

  // Polygon and mixed layers draw fill + outline; point and mixed layers draw
  // circles. A mixed layer (e.g. maritime concessions) carries both geometries.
  function isPolygonLayer(layer) {
    return layer.kind !== 'point';
  }

  function isPointLayer(layer) {
    return layer.kind === 'point' || layer.kind === 'mixed';
  }

  // Colour, opacity and stack order come from the server catalog (MapLayerSpec).
  function catalogLayer(layerId) {
    return state.catalog.find((candidate) => candidate.id === layerId);
  }

  // A catalog ``color_ramp`` (numeric) or ``color_match`` (categorical) becomes
  // a data-driven colour expression; otherwise the flat catalog colour is used.
  function layerFillColor(layer, fallback) {
    const ramp = layer.color_ramp;
    if (ramp?.stops?.length) {
      return ['interpolate', ['linear'], ['to-number', ['get', ramp.property], 0], ...ramp.stops.flatMap((stop) => [Number(stop.value), stop.color])];
    }
    const match = layer.color_match;
    if (match?.cases?.length) {
      const text = ['downcase', ['to-string', ['coalesce', ['get', match.property], '']]];
      return ['case', ...match.cases.flatMap((entry) => [['>=', ['index-of', String(entry.contains).toLowerCase(), text], 0], entry.color]), fallback];
    }
    return fallback;
  }

  function layerColor(layerId) {
    return catalogLayer(layerId)?.color || '#47758f';
  }

  function layerMetadata(layer) {
    const coverage = Array.isArray(layer.estimated_bounds)
      ? tr('Approximate data extent')
      : layer.coverage === 'partial'
        ? `${tr('Partial coverage')}${layer.coverage_note ? ` · ${tr(layer.coverage_note)}` : ''}`
        : layer.coverage === 'unknown'
          ? `${tr('Coverage not reported')}${layer.coverage_note ? ` · ${tr(layer.coverage_note)}` : ''}`
          : `${tr('From zoom')} ${layer.min_zoom || 0}`;
    const freshness = (layer.properties || []).includes('source_release')
      ? tr('Source version shown in feature details')
      : tr('Source date not reported');
    return `${coverage} · ${freshness} · ${layer.source || 'Source not reported'}`;
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
        // Above this zoom MapLibre overzooms the tiles it has instead of
        // requesting finer ones (catalog tile_max_zoom, default 16).
        maxzoom: Math.min(22, Number(layer.tile_max_zoom || 22)),
      });
      const color = layerColor(layer.id);
      const polygonColor = layer.polygon_color || layerFillColor(layer, color);
      const isAdminSubstitute = isAdministrativeSubstitute(layer);
      const userOpacity = state.layerOpacity.get(layer.id);
      if (isPolygonLayer(layer)) {
        state.map.addLayer({ id: `fill-${layer.id}`, type: 'fill', source: sourceId, 'source-layer': layer.id,
          ...(layer.kind === 'mixed' ? { filter: ['==', ['geometry-type'], 'Polygon'] } : {}),
          minzoom: Number(layer.min_zoom || 0), paint: { 'fill-color': polygonColor, 'fill-opacity': userOpacity ?? Number(layer.fill_opacity ?? 0.11) } });
        state.map.addLayer({ id: `line-${layer.id}`, type: 'line', source: sourceId, 'source-layer': layer.id,
          ...(layer.kind === 'mixed' ? { filter: ['==', ['geometry-type'], 'Polygon'] } : {}),
          minzoom: Number(layer.min_zoom || 0), paint: { 'line-color': polygonColor, 'line-width': Number(layer.line_width ?? 1.4), 'line-opacity': isAdminSubstitute ? 0.62 : (layer.kind === 'mixed' ? 1 : 0.8) } });
        if (Array.isArray(layer.unit_levels) && layer.unit_levels.length) bindUnitLevels(layer);
      }
      if (isPointLayer(layer)) {
        state.map.addLayer({ id: `point-${layer.id}`, type: 'circle', source: sourceId, 'source-layer': layer.id,
          ...(layer.kind === 'mixed' ? { filter: ['==', ['geometry-type'], 'Point'] } : {}),
          minzoom: Number(layer.min_zoom || 0), paint: { 'circle-color': color, 'circle-radius': 4, 'circle-stroke-color': '#fff', 'circle-stroke-width': 1, 'circle-opacity': layer.kind === 'point' ? (userOpacity ?? 1) : 1 } });
      }
      if (layer.id === 'maritime-concessions') {
        state.map.addLayer({
          id: `label-${layer.id}`, type: 'symbol', source: sourceId, 'source-layer': layer.id,
          minzoom: 15, filter: ['==', ['geometry-type'], 'Polygon'],
          layout: {
            'text-field': ['get', 'idconc'], 'text-font': ['noto-sans-regular'],
            'text-size': 11, 'text-anchor': 'top', 'text-offset': [0, 0.7],
          },
          paint: { 'text-color': polygonColor, 'text-halo-color': '#fff', 'text-halo-width': 1.5 },
        });
      }
      if (layer.id === 'cadastral-sheets') addCadastralSheetLabels();
      if (layer.id === 'cadastral-parcels') {
        addParcelLabels();
        state.map.on('mouseenter', 'fill-cadastral-parcels', () => { state.map.getCanvas().style.cursor = 'pointer'; });
        state.map.on('mouseleave', 'fill-cadastral-parcels', () => { state.map.getCanvas().style.cursor = ''; });
        // One popup instance, re-anchored on the cursor. The key used to
        // include the cursor position, so every mouse move destroyed and
        // rebuilt a DOM popup; now the markup changes only with the parcel.
        let hoverPopup = null;
        let hoverKey = null;
        let hoverFrame = 0;
        let hover = null;
        const renderHover = () => {
          hoverFrame = 0;
          const pending = hover;
          hover = null;
          if (!pending) return;
          if (!hoverPopup) hoverPopup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 8 });
          hoverPopup.setLngLat(pending.lngLat);
          if (hoverKey !== pending.key) {
            hoverKey = pending.key;
            hoverPopup.setHTML(`<strong>Parcel ${escapeHtml(pending.parcel)}</strong><br><small>${escapeHtml(pending.municipality)}</small>`);
          }
          if (!hoverPopup.isOpen()) hoverPopup.addTo(state.map);
        };
        state.map.on('mousemove', 'fill-cadastral-parcels', (event) => {
          // MapLibre deletes event.features once this handler returns, so read
          // everything now and defer only the DOM work to the next frame.
          const props = event.features?.[0]?.properties || {};
          const parcel = props.parcel || props.particella || props.LABEL || props.canonical_reference;
          if (!parcel) return;
          const municipality = props.municipality_name || props.municipality || props.municipality_id || '';
          hover = { parcel, municipality, key: `${parcel}:${municipality}`, lngLat: event.lngLat };
          if (!hoverFrame) hoverFrame = requestAnimationFrame(renderHover);
        });
        state.map.on('mouseleave', 'fill-cadastral-parcels', () => {
          hoverKey = null;
          hover = null;
          if (hoverPopup) hoverPopup.remove();
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

  // A multi-level layer (administrative boundaries) draws one ``unit_type`` per
  // zoom band; the ladder comes from the catalog's ``unit_levels``.
  function bindUnitLevels(layer) {
    const levels = [...layer.unit_levels].sort((a, b) => a.min_zoom - b.min_zoom);
    let applied = null;
    const apply = () => {
      const zoom = state.map.getZoom();
      const level = levels.filter((entry) => zoom >= entry.min_zoom).pop() || levels[0];
      if (level.unit_type === applied) return;
      applied = level.unit_type;
      const filter = ['==', ['get', 'unit_type'], level.unit_type];
      ['fill', 'line'].forEach((prefix) => { if (state.map.getLayer(`${prefix}-${layer.id}`)) state.map.setFilter(`${prefix}-${layer.id}`, filter); });
    };
    apply();
    // `zoom` (not `zoomend`) so the level follows a pinch or wheel gesture.
    state.map.on('zoom', apply);
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

  function addCadastralSheetLabels() {
    if (!state.map || state.map.getLayer('label-cadastral-sheets')) return;
    const label = ['coalesce', ['get', 'level_name'], ['get', 'sheet_reference'], ''];
    state.map.addLayer({
      id: 'label-cadastral-sheets', type: 'symbol', source: layerSourceId({ id: 'cadastral-sheets' }), 'source-layer': 'cadastral-sheets',
      minzoom: 12,
      filter: ['!=', ['to-string', label], ''],
      layout: {
        'text-field': label,
        'text-font': ['noto-sans-regular'],
        'text-size': ['interpolate', ['linear'], ['zoom'], 12, 10, 16, 13],
        'text-anchor': 'center',
        'text-allow-overlap': false,
        'text-padding': 4,
      },
      paint: { 'text-color': '#124f70', 'text-halo-color': '#fff', 'text-halo-width': 1.5 },
    });
    const visible = state.activeLayers?.has('cadastral-sheets') && state.cadastralSheetLabelsEnabled;
    state.map.setLayoutProperty('label-cadastral-sheets', 'visibility', visible ? 'visible' : 'none');
  }

  function addPostalZoneLabels() {
    if (!state.map || state.map.getLayer('label-postal-zones')) return;
    state.map.addLayer({
      id: 'label-postal-zones', type: 'symbol', source: layerSourceId({ id: 'postal-zones' }), 'source-layer': 'postal-zones',
      minzoom: 10,
      filter: ['!=', ['to-string', ['coalesce', ['get', 'cap'], '']], ''],
      layout: {
        'text-field': ['to-string', ['get', 'cap']],
        'text-font': ['noto-sans-regular'],
        'text-size': 12,
        'text-allow-overlap': false,
        'text-padding': 2,
      },
      paint: { 'text-color': '#5b4d20', 'text-halo-color': '#fff', 'text-halo-width': 1.5 },
    });
    if (!state.postalLabelsEnabled) state.map.setLayoutProperty('label-postal-zones', 'visibility', 'none');
    reorderMapLayers();
  }

  function handleLayerFailure(layer) {
    if (state.layerFailures.has(layer.id)) return;
    state.layerFailures.add(layer.id);
    const row = document.querySelector(`[data-layer-id="${CSS.escape(layer.id)}"]`);
    if (row) row.querySelector('.map-layer-meta').textContent = tr('Temporarily unavailable');
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

  function layerOpacitySetting(layer) {
    // Point layers expose whole-marker opacity; polygon and mixed layers expose
    // the fill, which is the faint part of the default style.
    return layer.kind === 'point'
      ? { label: tr('Opacity'), min: 0.1, max: 1, step: 0.05, value: state.layerOpacity.get(layer.id) ?? 1 }
      : { label: tr('Fill'), min: 0, max: 0.6, step: 0.01, value: state.layerOpacity.get(layer.id) ?? Number(layer.fill_opacity ?? 0.11) };
  }

  function applyLayerOpacity(layer, value) {
    state.layerOpacity.set(layer.id, value);
    if (!state.map) return;
    const target = layer.kind === 'point' ? `point-${layer.id}` : `fill-${layer.id}`;
    const property = layer.kind === 'point' ? 'circle-opacity' : 'fill-opacity';
    if (state.map.getLayer(target)) state.map.setPaintProperty(target, property, value);
  }

  async function zoomToLayerPolygons(layer, button) {
    const map = state.map || state.fallbackMap;
    if (!map || !layer.geojson_url) return;
    const viewport = map.getBounds();
    const params = new URLSearchParams({
      west: String(Math.max(-180, viewport.getWest())),
      south: String(Math.max(-90, viewport.getSouth())),
      east: String(Math.min(180, viewport.getEast())),
      north: String(Math.min(90, viewport.getNorth())),
      limit: String(Math.min(layer.max_features || 2000, 5000)),
    });
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8000);
    button.disabled = true;
    mapStatus(tr('Loading polygon footprints…'));
    const task = beginMapProgress(`polygons-${layer.id}`, 'Loading polygon footprints…');
    try {
      // Read native viewport geometry: tiny polygons can disappear during MVT
      // quantization, but their real footprint can still be inspected at zoom.
      const response = await fetch(`${layer.geojson_url}?${params}`, { signal: controller.signal });
      if (!response.ok) throw new Error('Polygon geometry unavailable');
      const payload = await response.json();
      if (!state.activeLayers?.has(layer.id)) return;
      if (payload.zoom_required) {
        mapStatus(tr('Zoom in to find polygon footprints.'));
        return;
      }
      const polygons = (payload.features || []).filter(feature => ['Polygon', 'MultiPolygon'].includes(feature.geometry?.type));
      if (!polygons.length) {
        mapStatus(tr('No polygon footprints are published in this view.'));
        return;
      }
      let footprints = polygons.map(feature => {
        let west = Infinity, south = Infinity, east = -Infinity, north = -Infinity;
        const visit = coordinates => {
          if (!Array.isArray(coordinates)) return;
          if (typeof coordinates[0] === 'number' && typeof coordinates[1] === 'number') {
            const [lng, lat] = coordinates;
            if (!Number.isFinite(lng) || !Number.isFinite(lat)) return;
            west = Math.min(west, lng); south = Math.min(south, lat);
            east = Math.max(east, lng); north = Math.max(north, lat);
          } else coordinates.forEach(visit);
        };
        visit(feature.geometry.coordinates);
        return { west, south, east, north };
      }).filter(bounds => Number.isFinite(bounds.west) && Number.isFinite(bounds.south) && bounds.east > bounds.west && bounds.north > bounds.south);
      if (!footprints.length) throw new Error('Polygon bounds unavailable');
      const west = Math.min(...footprints.map(bounds => bounds.west)), south = Math.min(...footprints.map(bounds => bounds.south));
      const east = Math.max(...footprints.map(bounds => bounds.east)), north = Math.max(...footprints.map(bounds => bounds.north));
      if (state.map) state.map.fitBounds([[west, south], [east, north]], { padding: 80, maxZoom: 20, duration: 600 });
      else state.fallbackMap.fitBounds([[south, west], [north, east]], { padding: [80, 80], maxZoom: 20 });
      $('mapLayersCard').hidden = true;
      $('layersToggle').setAttribute('aria-expanded', 'false');
      mapStatus(tr('Polygon footprints in view: {n}', { n: footprints.length }));
    } catch (_) {
      mapStatus(tr('Polygon footprints could not be loaded.'), true);
    } finally {
      task.finish();
      clearTimeout(timeout);
      button.disabled = false;
    }
  }

  function buildLayerItem(layer, initializingLayers) {
    const item = document.createElement('div');
    item.className = 'map-layer-item';
    item.dataset.layerId = layer.id;
    const row = document.createElement('label');
    row.className = 'map-layer-row';
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.checked = initializingLayers ? state.defaultLayers.has(layer.id) : state.activeLayers.has(layer.id);
    const badges = document.createElement('small');
    badges.className = 'map-layer-badges';
    badges.hidden = true;
    const opacity = document.createElement('label');
    opacity.className = 'map-layer-opacity';
    opacity.hidden = !input.checked || !state.map;
    const setting = layerOpacitySetting(layer);
    const slider = document.createElement('input');
    slider.type = 'range';
    Object.assign(slider, { min: setting.min, max: setting.max, step: setting.step, value: setting.value });
    slider.addEventListener('input', () => applyLayerOpacity(layer, Number(slider.value)));
    const caption = document.createElement('span');
    caption.textContent = setting.label;
    opacity.append(caption, slider);
    const labelControls = ['postal-zones', 'cadastral-sheets'].includes(layer.id) ? document.createElement('label') : null;
    if (labelControls) {
      labelControls.className = 'map-layer-label-controls';
      labelControls.hidden = !input.checked;
      const labelInput = document.createElement('input');
      labelInput.type = 'checkbox';
      labelInput.checked = layer.id === 'postal-zones'
        ? state.postalLabelsEnabled
        : state.cadastralSheetLabelsEnabled;
      const labelText = document.createElement('span');
      labelText.textContent = tr(layer.id === 'postal-zones' ? 'Show CAP labels' : 'Show sheet labels');
      labelInput.addEventListener('change', () => {
        const labelLayerId = `label-${layer.id}`;
        if (layer.id === 'postal-zones') {
          state.postalLabelsEnabled = labelInput.checked;
          if (labelInput.checked) addPostalZoneLabels();
        } else {
          state.cadastralSheetLabelsEnabled = labelInput.checked;
          if (labelInput.checked) addCadastralSheetLabels();
        }
        const labelLayer = state.map?.getLayer(labelLayerId);
        if (labelLayer) state.map.setLayoutProperty(labelLayerId, 'visibility', labelInput.checked ? 'visible' : 'none');
      });
      labelControls.append(labelInput, labelText);
    }
    const geometryControls = layer.kind === 'mixed' ? document.createElement('div') : null;
    if (geometryControls) {
      geometryControls.className = 'map-layer-geometry-controls';
      geometryControls.hidden = !input.checked;
      const legend = document.createElement('div');
      legend.className = 'map-layer-geometry-legend';
      legend.innerHTML = `<span><i class="map-layer-swatch" style="background:${escapeHtml(layer.polygon_color || layerColor(layer.id))}"></i>${escapeHtml(tr('Polygons'))}</span><span><i class="map-layer-swatch is-point" style="background:${escapeHtml(layerColor(layer.id))}"></i>${escapeHtml(tr('Points'))}</span>`;
      const zoomButton = document.createElement('button');
      zoomButton.type = 'button';
      zoomButton.className = 'map-layer-zoom';
      zoomButton.textContent = tr('Zoom to polygons');
      zoomButton.addEventListener('click', () => { void zoomToLayerPolygons(layer, zoomButton); });
      geometryControls.append(legend, zoomButton);
    }
    input.addEventListener('change', () => {
      state.adminSubstituteAutoLayers.delete(layer.id);
      if (isAdministrativeSubstitute(layer)) {
        if (input.checked) state.adminSubstituteUserDisabled.delete(layer.id);
        else state.adminSubstituteUserDisabled.add(layer.id);
      }
      if (state.activeLayers === null) state.activeLayers = new Set();
      if (input.checked) state.activeLayers.add(layer.id);
      else state.activeLayers.delete(layer.id);
      if (input.checked && (!state.map || !state.map.getSource(layerSourceId(layer)))) addCatalogLayer(layer);
      setLayerVisibility(layer.id, input.checked);
      opacity.hidden = !input.checked || !state.map;
      if (labelControls) labelControls.hidden = !input.checked;
      if (geometryControls) geometryControls.hidden = !input.checked;
      reorderMapLayers();
      updateParcelZoomAffordance();
      writeSelectedLayersUrl();
    });
    // Tiles are capped server-side, so dense areas can omit features; say so.
    const cap = layer.max_features ? ` · ${tr('Up to {n} features per tile', { n: Number(layer.max_features).toLocaleString('it-IT') })}` : '';
    row.title = `${layerMetadata(layer)}${cap}`;
    const copy = document.createElement('span');
    copy.innerHTML = `<strong><i class="map-layer-swatch" style="background:${layerColor(layer.id)}"></i>${escapeHtml(tr(layer.title))}</strong><small class="map-layer-meta">${escapeHtml(layerMetadata(layer))}</small>`;
    row.append(input, copy);
    item.append(row, badges, opacity);
    if (labelControls) item.appendChild(labelControls);
    if (geometryControls) item.appendChild(geometryControls);
    return { item, input };
  }

  function renderLayerCatalog() {
    const list = $('mapLayerList');
    if (!list) return;
    const sisterLayerOptions = $('sisterLayerOptions');
    list.replaceChildren();
    const initializingLayers = state.activeLayers === null;
    if (initializingLayers) state.activeLayers = new Set();
    const visible = state.catalog.filter((layer) => layer.id !== 'raster-coverage');
    const groups = [...new Set([...LAYER_GROUPS, ...visible.map((layer) => layer.group || 'territory')])];
    groups.forEach((group) => {
      const members = visible.filter((layer) => (layer.group || 'territory') === group);
      if (!members.length) return;
      const section = document.createElement('details');
      section.className = 'map-layer-group';
      section.open = true;
      const summary = document.createElement('summary');
      summary.textContent = tr(group.charAt(0).toUpperCase() + group.slice(1));
      section.appendChild(summary);
      members.forEach((layer) => {
        const { item, input } = buildLayerItem(layer, initializingLayers);
        section.appendChild(item);
        if (layer.id === 'cadastral-parcels' && sisterLayerOptions) item.appendChild(sisterLayerOptions);
        if (input.checked) {
          if (state.activeLayers === null) state.activeLayers = new Set();
          state.activeLayers.add(layer.id);
          addCatalogLayer(layer);
        }
      });
      list.appendChild(section);
    });
    reorderMapLayers();
    updateParcelZoomAffordance();
    updateCoverageStatus();
    writeSelectedLayersUrl();
  }

  function updateCoverageStatus() {
    const status = $('mapCoverageStatus');
    if (!status) return;
    const estimated = state.catalog.filter((layer) => Array.isArray(layer.estimated_bounds));
    const partial = state.catalog.filter((layer) => layer.coverage === 'partial');
    if (estimated.length) {
      status.textContent = `${tr('Approximate data extent')}: ${estimated.map((layer) => tr(layer.title)).join(', ')}. ${tr('The extent is estimated; local gaps may remain.')}`;
    } else if (partial.length) {
      status.textContent = tr('Partial coverage: {layers}', {
        layers: partial.map((layer) => `${tr(layer.title)}${layer.coverage_note ? ` (${layer.coverage_note})` : ''}`).join('; '),
      });
    } else {
      status.textContent = state.catalog.some((layer) => layer.coverage === 'unknown')
        ? tr('Coverage not reported')
        : '';
    }
  }

  function viewportOutside(bounds, coverage) {
    return Boolean(coverage && bounds && (bounds.getEast() < coverage[0] || bounds.getWest() > coverage[2] || bounds.getNorth() < coverage[1] || bounds.getSouth() > coverage[3]));
  }

  // Per-row hints for checked layers that cannot draw anything right now:
  // either the map is below the layer's minimum zoom or the viewport is
  // outside the area the layer covers.
  function updateLayerBadges() {
    const map = state.map || state.fallbackMap;
    if (!map || !state.catalog.length) return;
    const zoom = map.getZoom();
    const bounds = map.getBounds?.();
    state.catalog.forEach((layer) => {
      const badges = document.querySelector(`[data-layer-id="${CSS.escape(layer.id)}"] .map-layer-badges`);
      if (!badges) return;
      const hints = [];
      if (state.activeLayers?.has(layer.id)) {
        if (zoom < Number(layer.min_zoom || 0)) hints.push(tr('Zoom in: visible from zoom {n}', { n: layer.min_zoom }));
        if (viewportOutside(bounds, layer.estimated_bounds || layer.coverage_bounds)) {
          hints.push(layer.estimated_bounds
            ? tr('Outside the estimated data extent; coverage may vary.')
            : tr('Outside coverage: {note}', { note: layer.coverage_note || '' }));
        }
      }
      badges.textContent = hints.join(' · ');
      badges.hidden = !hints.length;
    });
  }

  function addFallbackCatalogLayer(layer) {
    if (!state.fallbackMap || !layer || layer.id === 'raster-coverage' || state.fallbackLayers.has(layer.id)) return;
    try {
      let overlay;
      if (layer.id === 'cadastral-parcels' && !L.vectorGrid?.protobuf) {
        overlay = L.tileLayer(absoluteTileUrl('/api/v1/tiles/cadastral-boundaries/{z}/{x}/{y}.png?layer=ple'), {
          minZoom: Number(layer.min_zoom || 0), maxZoom: 22, maxNativeZoom: 16,
          attribution: 'Cadastral boundaries',
        });
      } else if (L.vectorGrid?.protobuf && layer.tile_url) {
        const color = layerColor(layer.id);
        const style = (properties) => {
          const polygon = layer.kind === 'mixed'
            ? /polygon/i.test(String(properties.geometry_type || ''))
            : isPolygonLayer(layer);
          const featureColor = polygon ? (layer.polygon_color || color) : color;
          return {
            color: polygon ? featureColor : '#fff',
            weight: polygon ? Number(layer.line_width ?? 1.4) : 1,
            opacity: 1,
            fill: true,
            fillColor: featureColor,
            fillOpacity: polygon ? Number(layer.fill_opacity ?? .12) : 1,
            radius: 4,
          };
        };
        overlay = L.vectorGrid.protobuf(absoluteTileUrl(layer.tile_url), {
          vectorTileLayerStyles: { [layer.id]: style },
          minZoom: Number(layer.min_zoom || 0),
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
    if (!input) return;
    input.checked = checked;
    const opacity = input.closest('.map-layer-item')?.querySelector('.map-layer-opacity');
    if (opacity) opacity.hidden = !checked || !state.map;
    const geometryControls = input.closest('.map-layer-item')?.querySelector('.map-layer-geometry-controls');
    if (geometryControls) geometryControls.hidden = !checked;
  }

  function enableAdministrativeSubstitute() {
    const layer = administrativeSubstituteLayer();
    if (!layer) {
      mapStatus('Zoom in to see cadastral parcels; administrative boundaries are not configured.', true);
      return;
    }
    if (state.adminSubstituteUserDisabled.has(layer.id)) return;
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
    writeSelectedLayersUrl();
  }

  function releaseAdministrativeSubstitute() {
    state.adminSubstituteAutoLayers.forEach((layerId) => {
      state.activeLayers?.delete(layerId);
      setLayerVisibility(layerId, false);
      syncLayerCheckbox(layerId, false);
    });
    state.adminSubstituteAutoLayers.clear();
    writeSelectedLayersUrl();
  }

  function updateParcelZoomAffordance() {
    const map = state.map || state.fallbackMap;
    if (!map) return;
    const belowParcelZoom = map.getZoom() < parcelMinZoom();
    if (belowParcelZoom) enableAdministrativeSubstitute();
    else {
      const substitute = administrativeSubstituteLayer();
      if (substitute) state.adminSubstituteUserDisabled.delete(substitute.id);
      releaseAdministrativeSubstitute();
    }
    const panelOpen = !$('directParcelPanel')?.hidden;
    const bounds = map.getBounds?.();
    const parcels = state.catalog.find((layer) => layer.id === 'cadastral-parcels');
    const estimatedBounds = parcels?.estimated_bounds || parcels?.coverage_bounds;
    // While the source is down the catalog's coverage bounds say nothing about
    // what is published, so do not claim an area has no parcels.
    const sourceDown = state.sourceHealthDown || state.sourceTileDown;
    const outsideCoverage = !sourceDown && viewportOutside(bounds, estimatedBounds);
    updateLayerBadges();
    const note = $('mapHelpNote');
    if (!note) return;
    if (outsideCoverage) {
      note.textContent = tr('Outside the estimated data extent; coverage may vary.');
    } else if (belowParcelZoom && !administrativeSubstituteLayer() && !sourceDown) {
      note.textContent = tr('Zoom in to see cadastral parcels; administrative boundaries are not configured.');
    } else {
      note.textContent = tr('Zoom in to see cadastral parcels');
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
      state.sourceHealthDown = payload.available === false;
      state.unavailableLayerIds = new Set(
        payload.available === false
          ? []
          : (payload.layers || []).filter((health) => !health.available).map((health) => health.id),
      );
      [...state.tileErrors].forEach((sourceId) => {
        if (isKnownUnavailableTileSource(sourceId)) {
          state.tileErrors.delete(sourceId);
          tileRetry.pending.delete(sourceId);
          delete tileRetry.attempts[sourceId];
        }
      });
      refreshSourceTileDown();
      updateParcelZoomAffordance();
      (payload.layers || []).forEach((health) => {
        const row = document.querySelector(`[data-layer-id="${CSS.escape(health.id)}"]`);
        const meta = row?.querySelector('.map-layer-meta');
        if (!meta) return;
        const catalogLayer = state.catalog.find((layer) => layer.id === health.id);
        if (catalogLayer && health.source_database) {
          catalogLayer.source = `${health.source_database} PostgreSQL/PostGIS`;
        }
        if (catalogLayer && Array.isArray(health.estimated_bounds)) {
          catalogLayer.estimated_bounds = health.estimated_bounds;
          catalogLayer.extent_source = health.extent_source || '';
        }
        const metadata = catalogLayer ? ` · ${layerMetadata(catalogLayer)}` : '';
        // The whole source being down is an outage, not a configuration
        // state; drop the provenance text so the row says only that.
        if (payload.available === false) meta.textContent = tr('Temporarily unavailable');
        else if (health.available && catalogLayer?.coverage === 'partial') meta.textContent = `Partial coverage · ${catalogLayer.coverage_note || 'some regions'}${metadata}`;
        else if (health.available) meta.textContent = `Available${metadata}`;
        else if (health.relation_exists === false) meta.textContent = `Not available${metadata}`;
        else meta.textContent = `Needs configuration${metadata}`;
        // Health is advisory: checks can fail independently, and readiness
        // requirements such as a spatial index do not necessarily mean the
        // map layer cannot be drawn. Keep catalog controls selectable and
        // report availability in the row metadata instead of overriding the
        // user's layer choices when this asynchronous check completes.
        if (catalogLayer) row.title = layerMetadata(catalogLayer);
      });
      updateCoverageStatus();
      updateLayerBadges();
      updateParcelZoomAffordance();
    } catch (_) {
      // Health is advisory; the catalog and map remain usable without it.
    } finally { clearTimeout(timeout); }
  }

  // Every layer add/toggle asks for a re-stack, and a restored session adds
  // many layers in one tick. Coalesce the requests so the (comparatively
  // expensive) re-stack runs once per burst instead of once per layer.
  let reorderQueued = false;
  function reorderMapLayers() {
    if (!state.map || reorderQueued) return;
    reorderQueued = true;
    queueMicrotask(() => { reorderQueued = false; applyMapLayerOrder(); });
  }

  function applyMapLayerOrder() {
    if (!state.map) return;
    // Catalog layers stack by their server-side ``z_order``; live overlays
    // (bulletin, fires, auctions, sales) have fixed slots above them.
    const ordered = [...state.catalog].sort((a, b) => Number(a.z_order ?? 100) - Number(b.z_order ?? 100));
    const idsFor = (prefix, include) => ordered.filter(include).map((layer) => `${prefix}-${layer.id}`);
    const bottomToTop = [
      ...idsFor('fill', isPolygonLayer), 'fill-enrichment-bulletin',
      ...idsFor('line', isPolygonLayer),
      ...idsFor('point', isPointLayer),
      'point-enrichment-fires', 'auction-clusters', 'auction-cluster-count', 'point-auction-properties', 'point-sales-properties',
      'label-cadastral-parcels', ...idsFor('label', layer => layer.id === 'postal-zones' || layer.id === 'cadastral-sheets' || layer.kind === 'mixed'),
      'fill-adjacent-parcels', 'line-adjacent-parcels', 'fill-sister-listed-parcels', 'line-sister-listed-parcels',
      'fill-auction-listed-parcels', 'line-auction-listed-parcels',
      'selected-parcel-fill', 'selected-parcel-line', 'parcel-pvp-nearby-markers', 'parcel-pvp-nearby-labels',
      'sister-batch-selection-fill', 'sister-batch-selection-line',
      'sister-batch-drawing-fill', 'sister-batch-drawing-line',
      'urban-contour-fill', 'urban-contour-line', 'urban-contour-rural',
      'user-drawings-fill', 'user-drawings-line', 'user-drawings-point',
      'workbench-preview-fill', 'workbench-preview-line',
      'analysis-selection-fill', 'analysis-selection-line', 'analysis-selection-point',
      'drawing-vertices-points',
    ];
    const present = bottomToTop.filter((id) => state.map.getLayer(id));
    // Each moveLayer dirties the style and forces a re-render. When the managed
    // layers already sit in order at the top of the stack (the common case),
    // skip the pass entirely.
    const current = state.map.getLayersOrder?.();
    if (current && present.length && current.slice(-present.length).join('\u0000') === present.join('\u0000')) return;
    present.forEach((id) => state.map.moveLayer(id));
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
      const layerVisibility = prefix === 'label' && layerId === 'postal-zones'
        ? (visible && state.postalLabelsEnabled ? 'visible' : 'none')
        : prefix === 'label' && layerId === 'cadastral-sheets'
          ? (visible && state.cadastralSheetLabelsEnabled ? 'visible' : 'none')
          : value;
      // Skip no-op writes: any layout write marks the style dirty and forces
      // another render.
      if (state.map.getLayer(id) && (state.map.getLayoutProperty(id, 'visibility') ?? 'visible') !== layerVisibility) {
        state.map.setLayoutProperty(id, 'visibility', layerVisibility);
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

  function selectedFeaturePosition(feature) {
    const bbox = feature?.bbox;
    if (Array.isArray(bbox) && bbox.length >= 4) {
      const [west, south, east, north] = bbox.slice(0, 4).map(Number);
      if ([west, south, east, north].every(Number.isFinite)) return [(west + east) / 2, (south + north) / 2];
    }
    const coordinates = feature?.geometry?.coordinates;
    if (!coordinates) return null;
    const bounds = [Infinity, Infinity, -Infinity, -Infinity];
    const visit = (value) => {
      if (!Array.isArray(value)) return;
      if (value.length >= 2 && !Array.isArray(value[0]) && !Array.isArray(value[1])) {
        const lng = Number(value[0]);
        const lat = Number(value[1]);
        if (Number.isFinite(lng) && Number.isFinite(lat)) {
          bounds[0] = Math.min(bounds[0], lng);
          bounds[1] = Math.min(bounds[1], lat);
          bounds[2] = Math.max(bounds[2], lng);
          bounds[3] = Math.max(bounds[3], lat);
        }
        return;
      }
      value.forEach(visit);
    };
    visit(coordinates);
    return bounds.every(Number.isFinite) ? [(bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2] : null;
  }

  function clearSelectedAuctionSaleMarker() {
    state.selectedAuctionSaleMarker?.remove();
    state.selectedAuctionSaleMarker = null;
  }

  function selectedAuctionSalePopupHtml(feature) {
    const props = feature?.properties || {};
    const parcel = props.parcel_number ?? props.parcel ?? props.particella ?? null;
    const municipality = props.municipality_name || props.municipality || props.ADMINISTRATIVEUNIT || props.municipality_id || null;
    const context = [
      parcel !== null && parcel !== '' ? tr('Parcel {parcel}', { parcel }) : null,
      referenceFrom(props),
      municipality,
    ].filter(Boolean).join(' · ');
    return `<div class="auction-sale-tooltip"><strong>${escapeHtml(tr('Auction sale'))}</strong><br><span>${escapeHtml(tr('This cadastral parcel is marked for auction sale.'))}</span>${context ? `<small>${escapeHtml(context)}</small>` : ''}</div>`;
  }

  function renderSelectedAuctionSaleMarker(feature) {
    clearSelectedAuctionSaleMarker();
    const flag = feature?.properties?.is_auction_sale;
    if (!(flag === true || flag === 1 || flag === '1' || flag === 'true')) return;
    const position = selectedFeaturePosition(feature);
    if (!position) return;
    const popupHtml = selectedAuctionSalePopupHtml(feature);
    if (state.map && window.maplibregl) {
      const element = document.createElement('button');
      element.type = 'button';
      element.className = 'selected-auction-sale-marker';
      element.textContent = '€';
      element.title = tr('Auction sale');
      element.setAttribute('aria-label', tr('Auction sale'));
      const popup = new maplibregl.Popup({ closeButton: true, closeOnClick: false, maxWidth: '280px', offset: 18 })
        .setHTML(popupHtml);
      state.selectedAuctionSaleMarker = new maplibregl.Marker({ element, anchor: 'center' })
        .setLngLat(position)
        .setPopup(popup)
        .addTo(state.map);
      state.selectedAuctionSaleMarker.togglePopup();
    } else if (state.fallbackMap && window.L) {
      const icon = L.divIcon({
        className: 'selected-auction-sale-marker-wrap',
        html: '<span class="selected-auction-sale-marker" aria-hidden="true">€</span>',
        iconSize: [30, 30],
        iconAnchor: [15, 15],
      });
      state.selectedAuctionSaleMarker = L.marker([position[1], position[0]], {
        icon,
        title: tr('Auction sale'),
        keyboard: true,
      }).addTo(state.fallbackMap).bindPopup(popupHtml, { maxWidth: 280 });
      state.selectedAuctionSaleMarker.openPopup();
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

  // The parcel panel is a side sheet whose sections load independently
  // (static/parcel-panel.js). This function owns the page-level glue: title,
  // links, action buttons and the hand-over of the parcel read model. It is
  // called once when a parcel is selected and again, with the read model (or
  // {unavailable: true}), when /parcel/details answers.
  function renderParcelPanel(feature, enrichment = null) {
    const props = feature?.properties || {};
    const reference = state.selectedReference || referenceFrom(props) || null;
    if (enrichment) {
      window.ParcelPanel?.setReadModel(enrichment);
      return;
    }
    // ParcelPanel.show restores section fragments and scrolls to their target.
    // Reveal the sheet first so the browser can calculate the scroll position.
    $('directParcelPanel').hidden = false;
    const municipality = props.municipality_name || props.municipality || props.ADMINISTRATIVEUNIT || props.municipality_id || '';
    const parcel = props.parcel_number ?? props.parcel ?? props.particella ?? props.LABEL;
    $('directParcelTitle').textContent = parcel !== undefined && parcel !== null && parcel !== '' ? tr('Parcel {parcel}', { parcel }) : tr('Parcel details');
    $('directParcelSubtitle').textContent = [reference, municipality].filter(Boolean).join(' · ');
    if (window.ParcelPanel) {
      window.ParcelPanel.show(feature, { reference, featureId: feature.id ?? props.id ?? null });
      // Without a reference there is no read model to wait for.
      if (!reference) window.ParcelPanel.setReadModel({ unavailable: true });
    } else {
      // The panel script failed to load: identity is still useful.
      $('directParcelContent').textContent = [reference, municipality].filter(Boolean).join(' · ') || tr('Parcel details');
    }
    $('legacyAnalysisLink').href = `/map-legacy?parcel=${encodeURIComponent(reference || '')}`;
    const reportLink = $('parcelReportLink');
    const featureId = feature.id ?? props.id ?? null;
    const reportIdQuery = Number.isInteger(Number(featureId)) && Number(featureId) > 0
      ? `?id=${encodeURIComponent(String(Number(featureId)))}`
      : '';
    reportLink.href = reference
      ? `/api/v1/enrichment/parcel/report/${encodeURIComponent(reference)}${reportIdQuery}`
      : '#';
    reportLink.setAttribute('aria-disabled', String(!reference));
    reportLink.tabIndex = reference ? 0 : -1;
    const saved = state.savedReference === reference;
    $('parcelSaveButton').disabled = saved;
    $('parcelSaveButton').textContent = saved ? tr('Saved') : tr('Save parcel');
    $('directParcelPanel').hidden = false;
    $('mapHelpNote').hidden = true;
  }

  async function saveParcel() {
    const reference = state.selectedReference;
    if (!reference) return;
    const button = $('parcelSaveButton');
    button.disabled = true;
    button.textContent = tr('Saving…');
    const task = beginMapProgress('save-parcel', 'Saving parcel…');
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
        button.textContent = tr('Already saved');
        mapStatus('Parcel already saved');
        return;
      }
      if (!response.ok) throw new Error('Could not save parcel');
      state.savedReference = reference;
      button.textContent = tr('Saved');
      mapStatus('Parcel saved');
      void loadShortlist();
    } catch (error) {
      button.disabled = false;
      button.textContent = tr('Save parcel');
      mapStatus(error.message || 'Could not save parcel', true);
    } finally { task.finish(); }
  }

  async function downloadParcelReport(event) {
    const link = event.currentTarget;
    if (!link || link.getAttribute('aria-disabled') === 'true' || link.dataset.exporting === 'true') return;
    // Retain the server-only GET as a useful fallback if the panel bundle did
    // not load. With the bundle present, export every independently loaded
    // section before asking the server to render the PDF.
    if (typeof window.ParcelPanel?.createReportSnapshot !== 'function') return;
    event.preventDefault();
    const endpoint = link.href;
    const originalLabel = link.dataset.label || link.textContent.trim();
    link.dataset.label = originalLabel;
    link.dataset.exporting = 'true';
    link.setAttribute('aria-busy', 'true');
    link.textContent = tr('Preparing report…');
    link.title = '';
    try {
      const snapshot = await window.ParcelPanel.createReportSnapshot((loaded, total) => {
        link.textContent = tr('Loading report data: {loaded}/{total}', { loaded, total });
      });
      const response = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/pdf' },
        body: JSON.stringify(snapshot),
      });
      if (!response.ok) {
        let detail = '';
        try { detail = (await response.json()).detail || ''; } catch (_) { /* use the localized fallback */ }
        throw new Error(detail || `HTTP ${response.status}`);
      }
      const blob = await response.blob();
      const objectUrl = URL.createObjectURL(blob);
      const download = document.createElement('a');
      download.href = objectUrl;
      download.download = 'parcel-report.pdf';
      document.body.append(download);
      download.click();
      download.remove();
      setTimeout(() => URL.revokeObjectURL(objectUrl), 30_000);
    } catch (error) {
      link.textContent = tr('Report could not be prepared. Retry.');
      link.title = error && error.message ? error.message : tr('Report could not be prepared. Retry.');
      return;
    } finally {
      link.dataset.exporting = 'false';
      link.setAttribute('aria-busy', 'false');
      if (link.textContent === tr('Preparing report…')) link.textContent = originalLabel;
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
      $('shortlistSummary').textContent = tr('Sign in to load saved parcels');
      const next = encodeURIComponent(window.location.pathname + window.location.search);
      list.innerHTML = `<p class="map-muted"><a href="/auth/login?next=${next}">${escapeHtml(tr('Sign in'))}</a> ${escapeHtml(tr('to see your saved parcels.'))}</p>`;
      return;
    }
    const hazardOnly = $('shortlistHazardFilter')?.checked;
    const task = beginMapProgress('shortlist', 'Loading shortlist…');
    list.innerHTML = `<p class="map-muted">${escapeHtml(tr('Loading shortlist…'))}</p>`;
    try {
      const response = await fetch(`/api/v1/saved-parcels${hazardOnly ? '?active_hazard=true' : ''}`);
      if (response.status === 401) {
        $('shortlistSummary').textContent = tr('Sign in to load saved parcels');
        list.innerHTML = `<p class="map-muted">${escapeHtml(tr('Authentication required.'))}</p>`;
        return;
      }
      if (!response.ok) throw new Error('Shortlist unavailable');
      const payload = await response.json();
      state.shortlistItems = payload.items || [];
      state.shortlistStatuses = payload.status_vocabulary || [];
      state.shortlistSummary = payload.summary || null;
      renderShortlist();
    } catch (error) {
      list.innerHTML = `<p class="map-muted">${escapeHtml(tr('Shortlist unavailable.'))}</p>`;
      mapStatus(error.message || 'Shortlist unavailable', true);
    } finally { task.finish(); }
  }

  function showOverlayFeature(layer, feature, lngLat) {
    const properties = feature?.properties || {};
    const popup = new maplibregl.Popup({ maxWidth: '420px', closeButton: true })
      .setLngLat(lngLat)
      .addTo(state.map);

    const renderPopup = (details = null) => {
      const concession = layer.id === 'maritime-concessions';
      const displayProperties = details?.properties || properties;
      const relatedItems = details?.related || {};
      const scalarRows = entries => entries
        .filter(([, value]) => value !== null && value !== undefined && typeof value !== 'object' && value !== '')
        .map(([key, value]) => `<tr><th>${escapeHtml(key)}</th><td>${escapeHtml(value)}</td></tr>`)
        .join('');
      const snapshot = relatedItems.snapshot?.[0] || {};
      const rows = concession
        ? scalarRows([
          [tr('Concession ID'), displayProperties.idconc],
          [tr('Administrative label'), displayProperties.admin_label],
          [tr('Geometry'), displayProperties.geometry_type],
          [tr('Record type'), displayProperties.layer_kind],
          [tr('Source release'), displayProperties.source_release],
          [tr('Snapshot date'), snapshot.reference_date],
          [tr('Geometry status'), displayProperties.geometry_repaired === true ? tr('Repaired for map') : null],
        ])
        : scalarRows(Object.entries(properties)
          .filter(([, value]) => value !== null && value !== undefined && typeof value !== 'object')
          .slice(0, 6)
          .map(([key, value]) => [key.replaceAll('_', ' '), value]));
      const related = Object.entries(relatedItems)
        .filter(([, value]) => Array.isArray(value) && value.length)
        .map(([name, items]) => {
          const label = {
            online_documents: tr('Concession documents'),
            online_document_matches: tr('Matched documents'),
            resources: tr('Source resources'),
            document_gaps: tr('Document status'),
            snapshot: tr('Snapshot'),
          }[name] || name.replaceAll('_', ' ');
          const itemHtml = items.slice(0, 8).map(item => Object.entries(item || {})
            .filter(([, value]) => value !== null && value !== undefined && value !== '')
            .slice(0, 8)
            .map(([key, value]) => `<div><b>${escapeHtml(key.replaceAll('_', ' '))}:</b> ${canonicalFieldHtml(key, value, item)}</div>`)
            .join('')).join('<hr>');
          return `<section class="map-document-group"><strong>${escapeHtml(label)}</strong>${itemHtml || '<small>No details</small>'}</section>`;
        }).join('');
      const documents = relatedItems.online_documents || [];
      const gaps = relatedItems.document_gaps || [];
      const relatedSummary = concession && details && (documents.length || gaps.length)
        ? `<p class="map-muted">${escapeHtml(tr('Related records'))}: ${documents.length} ${escapeHtml(tr('documents'))} · ${gaps.length} ${escapeHtml(tr('document status records'))}</p>`
        : '';
      const documentHint = concession && details && !documents.length
        ? `<p class="map-muted">${escapeHtml(tr('No concession documents are linked to this record.'))}</p>`
        : '';
      const heading = concession
        ? `${tr('Maritime concession')}${displayProperties.idconc ? ` · ${displayProperties.idconc}` : ''}`
        : layer.id === 'geo-boundaries' && properties.canonical_name
          ? `${properties.unit_type ? `${properties.unit_type}: ` : ''}${properties.canonical_name}`
          : layer.title;
      popup.setHTML(`<strong>${escapeHtml(heading)}</strong><table class="parcel-detail-table"><tbody>${rows || '<tr><td>No feature details</td></tr>'}</tbody></table>${relatedSummary}${related}${documentHint}<small>${escapeHtml(layer.source || 'Source not available')}</small>`);
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
    const task = beginMapProgress('parcel', 'Loading parcel…');
    try {
      // A clicked tile feature's id lets the server resolve the parcel by
      // primary key instead of scanning the unindexed reference columns.
      const idHint = Number.isInteger(featureId) && featureId > 0 ? `?id=${featureId}` : '';
      const response = await fetch(`/api/v1/enrichment/parcel/by-reference/${encodeURIComponent(reference)}${idHint}`);
      if (!response.ok) {
        let detail = ''; try { detail = (await response.json()).detail || ''; } catch (_) { /* non-JSON API error */ }
        if (response.status === 409) {
          $('mapSearchInput').value = reference;
          mapStatus(detail || 'This reference matches multiple parcel polygons. Choose one from the results.', true);
          submitSearch(reference, true);
          return null;
        }
        throw new Error(response.status === 404
          ? 'This parcel is not in the available cadastral data. The autonomous provinces of Bolzano and Trento are not currently covered.'
          : (detail || 'Parcel service unavailable'));
      }
      const feature = await response.json();
      state.selectedReference = feature.properties?.canonical_reference || feature.properties?.national_cadastral_reference || reference;
      state.selectedFeature = feature;
      state.tileErrors.clear();
      renderSelectedGeometry(feature);
      renderSelectedAuctionSaleMarker(feature);
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
      if (sisterBuildingsOverlay.active) void loadSisterBuildingsOverlay();
      return feature;
    } catch (error) {
      mapStatus(error.message || 'Parcel unavailable', true);
      return null;
    } finally { task.finish(); }
  }

  async function loadParcelEnrichment(reference, feature) {
    const task = beginMapProgress('parcel-enrichment', 'Loading parcel details…');
    try {
      const params = new URLSearchParams({ view: 'panel' });
      if (Number.isInteger(feature?.id) && feature.id > 0) params.set('id', String(feature.id));
      const response = await fetch(`/api/v1/enrichment/parcel/details/${encodeURIComponent(reference)}?${params}`);
      // Replace the loading placeholder either way; a 404 means no enrichment
      // profile could be built, so the selected parcel identity remains useful.
      if (!response.ok) {
        if (state.selectedReference === reference) renderParcelPanel(feature, { unavailable: response.status !== 404 });
        return;
      }
      const enrichment = await response.json();
      renderParcelPanel(feature, enrichment);
    } catch (_) {
      // Identity remains useful without optional enrichment.
      if (state.selectedReference === reference) renderParcelPanel(feature, { unavailable: true });
    } finally { task.finish(); }
  }

  async function identifyAtPoint(lngLat, renderedFeature = null) {
    const reference = renderedFeature && referenceFrom(renderedFeature.properties);
    if (reference) return loadParcelByReference(reference, false, Number(renderedFeature.properties?.id));
    const params = new URLSearchParams({ lat: String(lngLat.lat), lng: String(lngLat.lng) });
    const task = beginMapProgress('identify-parcel', 'Finding parcel…');
    try {
      const response = await fetch(`/api/v1/enrichment/parcel/at-point?${params}`);
      if (!response.ok) throw new Error('No parcel at this point');
      const feature = await response.json();
      const foundReference = referenceFrom(feature.properties);
      if (foundReference) return loadParcelByReference(foundReference, false, Number(feature.id) || null);
      state.selectedFeature = feature;
      renderSelectedGeometry(feature);
      renderSelectedAuctionSaleMarker(feature);
      renderParcelPanel(feature);
    } catch (error) { mapStatus(error.message, true); }
    finally { task.finish(); }
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
        ? [result.municipality_name, result.sheet ? `Sheet ${result.sheet}` : '', result.id != null ? `Polygon ID ${result.id}` : ''].filter(Boolean).join(' · ')
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

  function isKnownUnavailableTileSource(sourceId) {
    const prefix = 'source-';
    return String(sourceId).startsWith(prefix)
      && state.unavailableLayerIds.has(String(sourceId).slice(prefix.length));
  }

  function refreshSourceTileDown() {
    state.sourceTileDown = [...state.tileErrors].some((sourceId) =>
      !isKnownUnavailableTileSource(sourceId) && (tileRetry.attempts[sourceId] || 0) >= 2,
    );
    updateSourceBanner();
  }

  function scheduleTileRetry(sourceId) {
    if (!state.sourceHealthDown && isKnownUnavailableTileSource(sourceId)) {
      state.tileErrors.delete(sourceId);
      tileRetry.pending.delete(sourceId);
      delete tileRetry.attempts[sourceId];
      refreshSourceTileDown();
      return;
    }
    const attempts = tileRetry.attempts[sourceId] || 0;
    if (attempts >= 2) {
      // Out of retries: stop requesting and tell the user once. Panning or
      // the banner's Retry button starts a fresh round.
      refreshSourceTileDown();
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
    }, 8000 * (2 ** attempts));
  }

  function retrySourceTiles() {
    tileRetry.attempts = {};
    state.tileErrors.clear();
    state.sourceTileDown = false;
    updateSourceBanner();
    void loadLayerHealth();
    window.dispatchEvent(new CustomEvent('cadastral-map-ready'));
    state.catalog.forEach((layer) => {
      const source = state.map?.getSource(layerSourceId(layer));
      const tiles = source?.serialize?.().tiles;
      if (source?.setTiles && tiles) source.setTiles(tiles);
    });
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
    const task = beginMapProgress('search', 'Searching…');
    $('mapSearchStatus').textContent = 'Searching…';
    try {
      const response = await fetch(`/api/v1/map/search?query=${encodeURIComponent(normalized)}`, { signal: controller.signal });
      // A 503 means the canonical source is down, which is not "no match":
      // say so, and still let an explicit search fall back to place names.
      const serviceDown = !response.ok;
      const payload = serviceDown ? {} : await response.json();
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
      $('mapSearchStatus').textContent = results.length ? '' : (!serviceDown ? (explicit ? 'No result' : (state.sourceHealthDown ? 'Municipality and parcel search is unavailable — press Enter to search places' : 'No municipality or parcel match — press Enter to search places')) : 'Search service unavailable; try again.');
    } catch (error) {
      if (error.name === 'AbortError') return;
      if (requestId === state.searchRequest) $('mapSearchStatus').textContent = 'Search unavailable';
    } finally {
      task.finish();
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
    clearSelectedAuctionSaleMarker();
    if (state.map?.getSource('selected-parcel')) state.map.getSource('selected-parcel').setData({ type: 'FeatureCollection', features: [] });
    state.map?.getSource('adjacent-parcels')?.setData({ type: 'FeatureCollection', features: [] });
    sisterBuildingsOverlay.fetchToken += 1;
    sisterBuildingsOverlay.records = [];
    state.map?.getSource('sister-building-parcel')?.setData(emptyFeatureCollection());
    if ($('sisterBuildingsCount')) $('sisterBuildingsCount').textContent = '';
    if ($('sisterBuildingsStatus')) $('sisterBuildingsStatus').textContent = sisterBuildingsOverlay.active ? 'Select a parcel to check SISTER records.' : '';
    const adjacentResults = $('parcelAdjacentResults');
    if (adjacentResults) adjacentResults.innerHTML = '';
    window.ParcelPanel?.clear();
    $('directParcelSubtitle').textContent = '';
    $('directParcelPanel').hidden = true;
    updateParcelZoomAffordance();
    writeUrl();
    mapStatus('Selection cleared');
  }

  function sisterBatchKey(feature) {
    const id = feature?.properties?.id ?? feature?.id;
    if (id !== null && id !== undefined && String(id)) return `id:${id}`;
    const reference = referenceFrom(feature?.properties || {});
    return reference ? `ref:${reference}` : null;
  }

  function renderSisterBatchSelection() {
    const features = [...state.sisterBatch.features.values()];
    const collection = { type: 'FeatureCollection', features };
    if (state.map) {
      if (state.map.getSource('sister-batch-selection')) state.map.getSource('sister-batch-selection').setData(collection);
      else {
        state.map.addSource('sister-batch-selection', { type: 'geojson', data: collection });
        state.map.addLayer({ id: 'sister-batch-selection-fill', type: 'fill', source: 'sister-batch-selection', paint: { 'fill-color': '#0e7490', 'fill-opacity': .2 } });
        state.map.addLayer({ id: 'sister-batch-selection-line', type: 'line', source: 'sister-batch-selection', paint: { 'line-color': '#0e7490', 'line-width': 3 } });
      }
      reorderMapLayers();
    } else if (state.fallbackMap && window.L) {
      if (state.sisterBatchFallbackLayer) state.fallbackMap.removeLayer(state.sisterBatchFallbackLayer);
      state.sisterBatchFallbackLayer = window.L.geoJSON(collection, {
        style: { color: '#0e7490', weight: 3, opacity: 1, fillColor: '#0e7490', fillOpacity: .2 },
      }).addTo(state.fallbackMap);
    }
    renderSisterBatchControls();
  }

  function setSisterBatchDrawing(geometry = null) {
    const feature = geometry ? { type: 'Feature', properties: {}, geometry } : null;
    const collection = { type: 'FeatureCollection', features: feature ? [feature] : [] };
    if (state.map) {
      if (state.map.getSource('sister-batch-drawing')) state.map.getSource('sister-batch-drawing').setData(collection);
      else {
        state.map.addSource('sister-batch-drawing', { type: 'geojson', data: collection });
        state.map.addLayer({ id: 'sister-batch-drawing-fill', type: 'fill', source: 'sister-batch-drawing', paint: { 'fill-color': '#8b5cf6', 'fill-opacity': .1 } });
        state.map.addLayer({ id: 'sister-batch-drawing-line', type: 'line', source: 'sister-batch-drawing', paint: { 'line-color': '#7c3aed', 'line-width': 2, 'line-dasharray': [2, 2] } });
      }
      reorderMapLayers();
    } else if (state.fallbackMap && window.L) {
      if (state.sisterBatchDrawingFallbackLayer) state.fallbackMap.removeLayer(state.sisterBatchDrawingFallbackLayer);
      state.sisterBatchDrawingFallbackLayer = feature
        ? window.L.geoJSON(feature, { style: { color: '#7c3aed', weight: 2, dashArray: '6 5', fillColor: '#8b5cf6', fillOpacity: .1 } }).addTo(state.fallbackMap)
        : null;
    }
  }

  function sisterBatchInstruction() {
    if (state.sisterBatch.mode === 'click') return tr('Click parcels to add or remove them from the batch.');
    if (state.sisterBatch.mode === 'polygon') return tr('Click to add polygon corners, then double-click to select parcels that intersect it.');
    if (state.sisterBatch.mode === 'circle') return tr('Click the circle center, move to set its radius, then click again.');
    return '';
  }

  function renderSisterBatchControls(message) {
    if (message !== undefined) state.sisterBatch.notice = message;
    const selected = [...state.sisterBatch.features.entries()];
    const count = selected.length;
    const output = $('sisterBatchList');
    const references = selected.map(([, feature]) => referenceFrom(feature.properties || {})).filter(Boolean);
    const ready = selected.filter(([, feature]) => sisterBatchCsvFields(feature).length > 0).length;
    const tools = [
      ['sisterBatchClickButton', 'click'],
      ['sisterBatchPolygonButton', 'polygon'],
      ['sisterBatchCircleButton', 'circle'],
    ];
    tools.forEach(([id, mode]) => $(id)?.setAttribute('aria-pressed', String(state.sisterBatch.mode === mode)));
    if (output) {
      output.innerHTML = selected.map(([key, feature]) => {
        const reference = referenceFrom(feature.properties || {}) || tr('Parcel');
        return `<li class="sister-batch-item"><span class="sister-batch-item-label">${escapeHtml(reference)}</span><button type="button" class="sister-batch-remove" data-sister-batch-remove="${escapeHtml(key)}" aria-label="${escapeHtml(tr('Remove {reference}', { reference }))}">×</button></li>`;
      }).join('');
    }
    const status = $('sisterBatchStatus');
    if (status) {
      status.textContent = state.sisterBatch.notice || sisterBatchInstruction() || (count
        ? (ready === count
          ? tr('{n} parcels selected', { n: count.toLocaleString() })
          : tr('{n} selected; {missing} need cadastral details before export', { n: count.toLocaleString(), missing: (count - ready).toLocaleString() }))
        : tr('No parcels selected'));
    }
    if ($('sisterBatchClearButton')) $('sisterBatchClearButton').disabled = !count && !state.sisterBatch.mode && !state.sisterBatch.busy;
    if ($('sisterBatchExportButton')) $('sisterBatchExportButton').disabled = !count || ready !== count || state.sisterBatch.busy;
    tools.forEach(([id]) => { if ($(id)) $(id).disabled = state.sisterBatch.busy; });
  }

  function setSisterBatchMode(mode) {
    const next = state.sisterBatch.mode === mode ? null : mode;
    workbench.mode = null;
    workbench.vertices = [];
    workbench.circleCenter = null;
    workbench.editingDrawing = false;
    workbench.editVertex = null;
    $('mapEditDrawingButton')?.setAttribute('aria-pressed', 'false');
    updateWorkbenchModeUi();
    renderWorkbenchPreview();
    state.sisterBatch.mode = next;
    state.sisterBatch.notice = '';
    state.sisterBatch.vertices = [];
    state.sisterBatch.circleCenter = null;
    setSisterBatchDrawing(null);
    if (state.map) {
      state.map.getCanvas().style.cursor = next ? 'crosshair' : '';
      if (next === 'polygon') state.map.doubleClickZoom.disable();
      else state.map.doubleClickZoom.enable();
    } else if (state.fallbackMap) {
      state.fallbackMap.getContainer().style.cursor = next ? 'crosshair' : '';
      if (next === 'polygon') state.fallbackMap.doubleClickZoom.disable();
      else state.fallbackMap.doubleClickZoom.enable();
    }
    renderSisterBatchControls();
  }

  function clearSisterBatchSelection() {
    state.sisterBatch.requestId += 1;
    state.sisterBatch.busy = false;
    state.sisterBatch.notice = '';
    state.sisterBatch.features.clear();
    setSisterBatchMode(null);
    renderSisterBatchSelection();
  }

  function addSisterBatchFeatures(features) {
    const additions = features.filter((feature) => {
      const key = sisterBatchKey(feature);
      return key && !state.sisterBatch.features.has(key);
    });
    if (state.sisterBatch.features.size + additions.length > 5000) {
      throw new Error(tr('Selection is limited to 5,000 parcels. Clear some parcels or draw a smaller area.'));
    }
    additions.forEach((feature) => state.sisterBatch.features.set(sisterBatchKey(feature), feature));
    renderSisterBatchSelection();
    return additions.length;
  }

  async function resolveSisterBatchIds(parcelIds) {
    const requestId = ++state.sisterBatch.requestId;
    state.sisterBatch.busy = true;
    renderSisterBatchControls(tr('Resolving parcel details…'));
    try {
      const response = await fetch('/api/v1/map/sister-batch-selection', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ parcel_ids: parcelIds }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || tr('Parcel selection is temporarily unavailable.'));
      if (requestId !== state.sisterBatch.requestId) return;
      addSisterBatchFeatures(payload.features || []);
      state.sisterBatch.notice = '';
    } catch (error) {
      if (requestId === state.sisterBatch.requestId) renderSisterBatchControls(error.message || tr('Parcel selection is temporarily unavailable.'));
    } finally {
      if (requestId === state.sisterBatch.requestId) {
        state.sisterBatch.busy = false;
        renderSisterBatchControls();
      }
    }
  }

  async function resolveSisterBatchGeometry(geometry) {
    const requestId = ++state.sisterBatch.requestId;
    setSisterBatchMode(null);
    state.sisterBatch.busy = true;
    renderSisterBatchControls(tr('Selecting parcels in the drawn area…'));
    try {
      const response = await fetch('/api/v1/map/sister-batch-selection', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ geometry }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || tr('Parcel selection is temporarily unavailable.'));
      if (requestId !== state.sisterBatch.requestId) return;
      if (payload.truncated) throw new Error(tr('More than 5,000 parcels match. Draw a smaller area to keep the batch complete.'));
      const added = addSisterBatchFeatures(payload.features || []);
      renderSisterBatchControls(tr('Added {added} parcels; {total} selected.', { added: added.toLocaleString(), total: state.sisterBatch.features.size.toLocaleString() }));
    } catch (error) {
      if (requestId === state.sisterBatch.requestId) renderSisterBatchControls(error.message || tr('Parcel selection is temporarily unavailable.'));
    } finally {
      if (requestId === state.sisterBatch.requestId) {
        state.sisterBatch.busy = false;
        renderSisterBatchControls();
      }
    }
  }

  async function selectSisterBatchParcel(lngLat, renderedFeature = null) {
    const requestId = state.sisterBatch.requestId;
    let feature = renderedFeature;
    if (!feature || !(feature.properties?.id ?? feature.id)) {
      try {
        const params = new URLSearchParams({ lat: String(lngLat.lat), lng: String(lngLat.lng) });
        const response = await fetch(`/api/v1/enrichment/parcel/at-point?${params}`);
        if (!response.ok) throw new Error(tr('No parcel at this point.'));
        feature = await response.json();
      } catch (error) {
        if (requestId === state.sisterBatch.requestId) renderSisterBatchControls(error.message || tr('No parcel at this point.'));
        return;
      }
    }
    if (requestId !== state.sisterBatch.requestId) return;
    const key = sisterBatchKey(feature);
    if (!key) { renderSisterBatchControls(tr('This parcel has no cadastral reference.')); return; }
    if (state.sisterBatch.features.has(key)) {
      state.sisterBatch.features.delete(key);
      state.sisterBatch.notice = '';
      renderSisterBatchSelection();
      return;
    }
    const id = Number(feature.properties?.id ?? feature.id);
    if (!Number.isSafeInteger(id) || id <= 0) { renderSisterBatchControls(tr('This parcel has no usable map ID.')); return; }
    if (state.sisterBatch.features.size >= 5000) { renderSisterBatchControls(tr('Selection is limited to 5,000 parcels. Clear some parcels or draw a smaller area.')); return; }
    await resolveSisterBatchIds([id]);
  }

  function coordinateDistanceMeters(first, second) {
    const radians = value => value * Math.PI / 180;
    const lat1 = radians(first[1]); const lat2 = radians(second[1]);
    const dLat = lat2 - lat1; const dLng = radians(second[0] - first[0]);
    const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLng / 2) ** 2;
    return 6371008.8 * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  }

  function circleSelectionPolygon(center, edge) {
    const radius = coordinateDistanceMeters(center, edge);
    if (radius < 2) return null;
    const angular = radius / 6371008.8;
    const lat1 = center[1] * Math.PI / 180;
    const lon1 = center[0] * Math.PI / 180;
    const coordinates = [];
    for (let index = 0; index < 64; index += 1) {
      const bearing = 2 * Math.PI * index / 64;
      const lat2 = Math.asin(Math.sin(lat1) * Math.cos(angular) + Math.cos(lat1) * Math.sin(angular) * Math.cos(bearing));
      const lon2 = lon1 + Math.atan2(Math.sin(bearing) * Math.sin(angular) * Math.cos(lat1), Math.cos(angular) - Math.sin(lat1) * Math.sin(lat2));
      coordinates.push([lon2 * 180 / Math.PI, lat2 * 180 / Math.PI]);
    }
    coordinates.push(coordinates[0]);
    return { type: 'Polygon', coordinates: [coordinates] };
  }

  function previewSisterBatchDrawing(cursor) {
    let geometry = null;
    if (state.sisterBatch.mode === 'polygon' && state.sisterBatch.vertices.length >= 2) {
      const ring = [...state.sisterBatch.vertices, cursor];
      if (ring.length >= 3) ring.push(ring[0]);
      geometry = ring.length >= 4 ? { type: 'Polygon', coordinates: [ring] } : { type: 'LineString', coordinates: ring };
    } else if (state.sisterBatch.mode === 'circle' && state.sisterBatch.circleCenter) {
      geometry = circleSelectionPolygon(state.sisterBatch.circleCenter, cursor);
    }
    setSisterBatchDrawing(geometry);
  }

  function addSisterBatchPolygonVertex(lngLat) {
    const point = [lngLat.lng, lngLat.lat];
    const vertices = state.sisterBatch.vertices;
    if (!vertices.length || coordinateDistanceMeters(vertices[vertices.length - 1], point) >= 2) vertices.push(point);
    renderSisterBatchControls(sisterBatchInstruction());
    previewSisterBatchDrawing(point);
  }

  function finishSisterBatchPolygon() {
    const vertices = state.sisterBatch.vertices;
    const ring = [...vertices];
    if (ring.length > 2 && coordinateDistanceMeters(ring[0], ring[ring.length - 1]) < 3) ring.pop();
    if (ring.length < 3) { setSisterBatchMode(null); renderSisterBatchControls(tr('Add at least three polygon corners.')); return; }
    ring.push(ring[0]);
    setSisterBatchDrawing(null);
    void resolveSisterBatchGeometry({ type: 'Polygon', coordinates: [ring] });
  }

  function finishSisterBatchCircle(edge) {
    const geometry = circleSelectionPolygon(state.sisterBatch.circleCenter, [edge.lng, edge.lat]);
    if (!geometry) { renderSisterBatchControls(tr('Move farther from the center to set a circle radius.')); return; }
    setSisterBatchDrawing(null);
    void resolveSisterBatchGeometry(geometry);
  }

  function handleSisterBatchMapClick(lngLat, renderedFeature = null) {
    const mode = state.sisterBatch.mode;
    if (mode === 'click') { void selectSisterBatchParcel(lngLat, renderedFeature); return true; }
    if (mode === 'polygon') { addSisterBatchPolygonVertex(lngLat); return true; }
    if (mode === 'circle') {
      const point = [lngLat.lng, lngLat.lat];
      if (!state.sisterBatch.circleCenter) {
        state.sisterBatch.circleCenter = point;
        renderSisterBatchControls(sisterBatchInstruction());
      } else finishSisterBatchCircle(lngLat);
      return true;
    }
    return false;
  }

  function sisterBatchCsvFields(feature) {
    const properties = feature?.properties || {};
    const reference = String(referenceFrom(properties) || '').trim().toUpperCase();
    const normalized = reference;
    const parts = normalized.includes('_')
      ? (() => { const [head, rest] = normalized.split('_', 2); const match = head.match(/^([A-Z]\d{3})([A-Z]?)$/); const [sheet, parcel] = rest.split('.', 2); return match ? { section: match[2] || '', sheet, parcel } : null; })()
      : (() => { const match = normalized.match(/^([A-Z]\d{3})([A-Z]?)(\d+)\.(.+)$/); return match ? { section: match[2] || '', sheet: match[3], parcel: match[4] } : null; })();
    if (!parts || String(parts.parcel || '').includes('/')) return [];
    const rawSheet = String(properties.sheet || parts.sheet || '').trim();
    let sheet = rawSheet.replace(/^0+/, '') || '0';
    if (/^\d+$/.test(sheet) && Number(sheet) >= 100 && Number(sheet) % 100 === 0) sheet = String(Number(sheet) / 100);
    const parcel = String(properties.parcel || parts.parcel || '').trim();
    const municipality = String(properties.municipality_name || '').trim().toUpperCase();
    const province = String(properties.province || '').trim();
    if (!sheet || !parcel || !municipality || !province) return [];
    return [
      'visura-storica', province, municipality, sheet, parcel, '', 'T', '', 'analitica', parts.section, '',
      `Land Registry map selection; parcel ${reference}; Storica Analitica visura per immobile - Catasto Terreni.`,
      'visura-storica', province, municipality, sheet, parcel, '', 'F', '', 'analitica', parts.section, '',
      `Land Registry map selection; parcel ${reference}; Storica Analitica visura per immobile - Catasto Fabbricati.`,
    ];
  }

  function sisterBatchCsvRows(feature) {
    const fields = sisterBatchCsvFields(feature);
    if (!fields.length) return [];
    const groups = [fields.slice(0, 12), fields.slice(12, 24)];
    return groups;
  }

  function csvCell(value) {
    const text = String(value ?? '');
    return `"${text.replace(/"/g, '""')}"`;
  }

  function exportSisterBatch() {
    const headers = ['command', 'provincia', 'comune', 'foglio', 'particella', 'subalterno', 'tipo_catasto', 'codice_fiscale', 'vista', 'sezione', 'source_files', 'scope_note'];
    const rows = [...state.sisterBatch.features.values()].flatMap(sisterBatchCsvRows);
    if (!rows.length) { renderSisterBatchControls(tr('No complete cadastral references are ready to export.')); return; }
    const csv = [headers, ...rows].map(row => row.map(csvCell).join(',')).join('\r\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `land_registry_sister_batch_${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    renderSisterBatchControls(tr('Downloaded {rows} SISTER requests for {parcels} parcels.', { rows: rows.length.toLocaleString(), parcels: state.sisterBatch.features.size.toLocaleString() }));
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
  const sisterBuildingsOverlay = { active: false, fetchToken: 0, records: [] };
  const sisterParcelsOverlay = { active: false, fetchToken: 0, debounceTimer: null, controller: null, leafletLayer: null };
  const auctionParcelsOverlay = { active: false, fetchToken: 0, debounceTimer: null, controller: null, leafletLayer: null };

  // ---- Sales map integration --------------------------------------------
  // PVP map points come from the enriched v_map_sales database view through
  // the land-registry API. Details are loaded only when a marker is opened.
  const salesOverlay = {
    active: false,
    points: [],
    fetchToken: 0,
    geo: null,
    // Viewport-bounded loading (see viewportBbox): last loaded box, in-flight
    // request, and the moveend refetch timer.
    loaded: null,
    abort: null,
    moveBound: false,
    moveTimer: null,
  };

  function numberValue(...values) {
    for (const value of values) {
      const parsed = Number(value);
      if (Number.isFinite(parsed)) return parsed;
    }
    return null;
  }

  // The PVP feed sends compact rows ([id, lng, lat, price, date, category,
  // approximate]) described by payload.fields; the sales-service feed sends
  // objects. Normalise both to objects before building features.
  function expandPvpSalesPoints(payload, points) {
    if (!Array.isArray(payload?.fields)) return points;
    const labels = Object.fromEntries((payload.categories || []).map((item) => [item.key, item.label]));
    return points.map((row) => {
      const point = Array.isArray(row) ? Object.fromEntries(payload.fields.map((name, index) => [name, row[index]])) : row;
      return {
        ...point,
        display_title: labels[point.category] || 'Sale',
        display_date: point.date || undefined,
        pvp_record: true,
      };
    });
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
      if (sort === 'deadline_asc') return String(a.auction_date || a.offer_deadline || a.display_date || a.date || a.sale_datetime || '9999').localeCompare(String(b.auction_date || b.offer_deadline || b.display_date || b.date || b.sale_datetime || '9999'));
      return (salesScore(b) ?? -Infinity) - (salesScore(a) ?? -Infinity);
    });
  }

  function salesGeoJson() {
    return { type: 'FeatureCollection', features: sortSales(salesOverlay.points) };
  }

  function salesValueLabel(value, suffix = '') {
    const numeric = numberValue(value);
    return numeric === null ? '—' : `${numeric.toLocaleString('it-IT', { maximumFractionDigits: 1 })}${suffix}`;
  }

  function salesPopupHtml(properties) {
    const price = salesPrice(properties);
    const appraisal = numberValue(properties.appraisal_value, properties.market_value);
    const score = salesScore(properties);
    const saleId = properties.sale_id ?? properties.id;
    const detail = properties.nearby_pvp_record
      ? (saleId ? `http://localhost:8016/sales/${encodeURIComponent(saleId)}`
        : (properties.detail_url || properties.source_url || properties.url || properties.source_link || ''))
      : properties.pvp_record
        ? (saleId ? `http://localhost:8016/sales/${encodeURIComponent(saleId)}` : '')
        : (properties.no_detail ? '' : (properties.detail_url || properties.source_link || (properties.id ? `/sales/${encodeURIComponent(properties.id)}` : '')));
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
      + (properties.approximate ? row('Location', 'Approximate geocoded location') : '')
      + (properties.city || properties.province ? row('Place', [properties.city, properties.province].filter(Boolean).join(' · ')) : '')
      + (properties.address ? row('Address', properties.address) : '')
      + (properties.description ? row('Description', properties.description) : '')
      + (properties.detail_error ? row('Details', 'Could not load this sale record') : '')
      + `<div class="sales-popup-actions">${detail ? `<a href="${escapeHtml(detail)}" target="_blank" rel="noopener noreferrer">${properties.pvp_record || properties.nearby_pvp_record ? 'Auction listing' : 'Details'}</a>` : ''}`
      + (properties.url ? `<a href="${escapeHtml(properties.url)}" target="_blank" rel="noopener noreferrer">Source notice</a>` : '')
      + (maps ? `<a href="${maps}" target="_blank" rel="noopener noreferrer">Google Maps</a>` : '')
      + (street ? `<a href="${street}" target="_blank" rel="noopener noreferrer">Street View</a>` : '')
      + '</div></div>';
  }

  function nearbyPvpMarkerFeatures(points) {
    if (!Array.isArray(points)) return [];
    return points.map((point, index) => {
      const lng = numberValue(point.lng, point.longitude);
      const lat = numberValue(point.lat, point.latitude);
      const id = point.id ?? point.sale_id;
      if (id == null || lng === null || lat === null) return null;
      const category = String(point.category || 'other');
      const title = point.property_type || category.replace(/_/g, ' ');
      const sourceUrl = point.source_url || point.url || point.source_link || '';
      const rawPrice = point.price ?? point.minimum_offer ?? point.base_auction_price;
      return {
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [lng, lat] },
        properties: {
          id: String(id), sale_id: String(id), nearby_pvp_record: 1,
          rank: Number(point.rank) || index + 1,
          category, price: rawPrice == null ? null : numberValue(rawPrice),
          date: point.date || point.sale_date || '', display_date: point.date || point.sale_date || '',
          display_title: title, approximate: Number(point.approximate || point.coordinate_is_approximate) || 0,
          source_url: sourceUrl, url: sourceUrl,
          description: point.description || '', lat, lng,
        },
      };
    }).filter(Boolean);
  }

  function renderParcelPvpNearbyMarkers() {
    const points = state.parcelPvpMarkers.points;
    const features = nearbyPvpMarkerFeatures(points);
    if (state.map) {
      if (!state.map.isStyleLoaded()) {
        state.map.once('style.load', renderParcelPvpNearbyMarkers);
        return;
      }
      if (!state.map.getSource('parcel-pvp-nearby-sales') && !state.parcelPvpMarkers.visible) return;
      if (!state.map.getSource('parcel-pvp-nearby-sales')) {
        state.map.addSource('parcel-pvp-nearby-sales', { type: 'geojson', data: emptyFeatureCollection() });
        state.map.addLayer({ id: 'parcel-pvp-nearby-markers', type: 'circle', source: 'parcel-pvp-nearby-sales',
          paint: { 'circle-radius': 10, 'circle-color': '#f97316', 'circle-opacity': .96, 'circle-stroke-color': '#fff', 'circle-stroke-width': 2.5 } });
        state.map.addLayer({ id: 'parcel-pvp-nearby-labels', type: 'symbol', source: 'parcel-pvp-nearby-sales',
          layout: { 'text-field': ['get', 'rank'], 'text-font': ['noto-sans-regular'], 'text-size': 10, 'text-allow-overlap': true },
          paint: { 'text-color': '#fff', 'text-halo-color': '#c2410c', 'text-halo-width': .5 } });
        state.map.on('click', 'parcel-pvp-nearby-markers', async (event) => {
          const feature = event.features?.[0];
          if (!feature) return;
          const properties = feature.properties || {};
          const popup = new maplibregl.Popup({ maxWidth: '360px' }).setLngLat(event.lngLat).setHTML(salesPopupHtml(properties)).addTo(state.map);
          if (!properties.nearby_pvp_record || !properties.id) return;
          try {
            const response = await fetch(`/api/v1/sales/pvp/${encodeURIComponent(properties.id)}`, { credentials: 'same-origin' });
            if (!response.ok) throw new Error('Sale details unavailable');
            const detail = await response.json();
            if (popup.isOpen()) popup.setHTML(salesPopupHtml({ ...properties, ...detail, nearby_pvp_record: 1, display_title: detail.property_type || properties.display_title }));
          } catch (_) {
            if (popup.isOpen()) popup.setHTML(salesPopupHtml({ ...properties, detail_error: true }));
          }
        });
        state.map.on('mouseenter', 'parcel-pvp-nearby-markers', () => { state.map.getCanvas().style.cursor = 'pointer'; });
        state.map.on('mouseleave', 'parcel-pvp-nearby-markers', () => { state.map.getCanvas().style.cursor = ''; });
      }
      state.map.getSource('parcel-pvp-nearby-sales')?.setData({ type: 'FeatureCollection', features });
      ['parcel-pvp-nearby-markers', 'parcel-pvp-nearby-labels'].forEach((id) => {
        if (state.map.getLayer(id)) state.map.setLayoutProperty(id, 'visibility', state.parcelPvpMarkers.visible ? 'visible' : 'none');
      });
      reorderMapLayers();
      return;
    }
    if (!state.fallbackMap || !window.L) return;
    if (!state.parcelPvpMarkerLayer) state.parcelPvpMarkerLayer = window.L.layerGroup().addTo(state.fallbackMap);
    state.parcelPvpMarkerLayer.clearLayers();
    if (!state.parcelPvpMarkers.visible) return;
    features.forEach((feature) => {
      const properties = feature.properties;
      window.L.circleMarker([properties.lat, properties.lng], {
        radius: 8, color: '#fff', weight: 2, fillColor: '#f97316', fillOpacity: .96,
      }).bindPopup(salesPopupHtml(properties)).addTo(state.parcelPvpMarkerLayer);
    });
  }

  function setParcelPvpNearbyMarkers(detail = {}) {
    state.parcelPvpMarkers = {
      points: Array.isArray(detail.points) ? detail.points : [],
      visible: Boolean(detail.visible),
    };
    renderParcelPvpNearbyMarkers();
  }

  function addSalesOverlayLayers() {
    if (!state.map) return;
    if (!state.map.getSource('sales-properties')) {
      state.map.addSource('sales-properties', { type: 'geojson', data: emptyFeatureCollection(), cluster: true, clusterMaxZoom: 14, clusterRadius: 45 });
    }
    if (!state.map.getLayer('sales-clusters')) state.map.addLayer({ id: 'sales-clusters', type: 'circle', source: 'sales-properties', filter: ['has', 'point_count'], paint: { 'circle-color': '#7c3aed', 'circle-radius': ['step', ['get', 'point_count'], 18, 25, 23, 100, 30], 'circle-opacity': .88, 'circle-stroke-color': '#fff', 'circle-stroke-width': 2 } });
    if (!state.map.getLayer('sales-cluster-count')) state.map.addLayer({ id: 'sales-cluster-count', type: 'symbol', source: 'sales-properties', filter: ['has', 'point_count'], layout: { 'text-field': ['get', 'point_count_abbreviated'], 'text-font': ['noto-sans-regular'], 'text-size': 11 }, paint: { 'text-color': '#fff' } });
    if (!state.map.getLayer('sales-unclustered')) state.map.addLayer({ id: 'sales-unclustered', type: 'circle', source: 'sales-properties', filter: ['!', ['has', 'point_count']], paint: { 'circle-radius': ['case', ['==', ['get', 'approximate'], 1], 5, 7], 'circle-color': ['match', ['get', 'category'], 'residential', '#2563eb', 'land', '#16a34a', 'parking_storage', '#f59e0b', 'commercial', '#db2777', 'industrial', '#7c3aed', 'building', '#0891b2', 'movable', '#ea580c', '#64748b'], 'circle-opacity': ['case', ['==', ['get', 'approximate'], 1], .55, .92], 'circle-stroke-color': '#fff', 'circle-stroke-width': 1.5 } });
    state.map.on('click', 'sales-clusters', (event) => {
      const feature = event.features?.[0];
      if (!feature) return;
      state.map.getSource('sales-properties')?.getClusterExpansionZoom(feature.properties.cluster_id, (error, zoom) => {
        if (!error) state.map.easeTo({ center: feature.geometry.coordinates, zoom });
      });
    });
    state.map.on('click', 'sales-unclustered', async (event) => {
      const feature = event.features?.[0];
      if (!feature) return;
      const properties = feature.properties || {};
      const popup = new maplibregl.Popup({ maxWidth: '360px' }).setLngLat(event.lngLat).setHTML(salesPopupHtml(properties)).addTo(state.map);
      if (!properties.pvp_record || !properties.id) return;
      const task = beginMapProgress('sale-detail', 'Loading auction details…');
      try {
        const response = await fetch(`/api/v1/sales/pvp/${encodeURIComponent(properties.id)}`, { credentials: 'same-origin' });
        if (!response.ok) throw new Error('Sale details unavailable');
        const detail = await response.json();
        const combined = { ...properties, ...detail, display_title: detail.property_type || properties.display_title };
        if (popup.isOpen()) popup.setHTML(salesPopupHtml(combined));
      } catch (_) {
        if (popup.isOpen()) popup.setHTML(salesPopupHtml({ ...properties, detail_error: true }));
      } finally { task.finish(); }
    });
    ['sales-clusters', 'sales-unclustered'].forEach((layer) => {
      state.map.on('mouseenter', layer, () => { state.map.getCanvas().style.cursor = 'pointer'; });
      state.map.on('mouseleave', layer, () => { state.map.getCanvas().style.cursor = ''; });
    });
  }

  // ---- Viewport-bounded loading for point overlays -------------------------
  // The sales/auction feed has ~700k points nationwide. Ask only for what the
  // map can show (the view plus a margin), capped, and refetch on moveend when
  // the view leaves the loaded box or a truncated result can show more detail.
  const VIEWPORT_POINT_LIMIT = 20000;
  const VIEWPORT_MARGIN = 0.15;
  const VIEWPORT_RELOAD_DEBOUNCE_MS = 350;
  // Below this zoom a viewport holds hundreds of thousands of points that
  // cannot be told apart, and a capped sample would misrepresent them, so the
  // overlays ask the user to zoom in instead of loading anything.
  const POINT_OVERLAY_MIN_ZOOM = 8;

  function belowPointOverlayZoom() {
    return !!state.map && state.map.getZoom() < POINT_OVERLAY_MIN_ZOOM;
  }

  function roundOut(value, step, up) { return (up ? Math.ceil(value / step) : Math.floor(value / step)) * step; }

  function currentViewBounds() {
    if (!state.map) return null;
    const bounds = state.map.getBounds();
    return [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()];
  }

  // west,south,east,north with a margin, snapped outward to 0.01 degrees so
  // small pans give identical (cacheable) requests.
  function viewportBbox() {
    const view = currentViewBounds();
    if (!view) return null;
    const [west, south, east, north] = view;
    const dx = (east - west) * VIEWPORT_MARGIN;
    const dy = (north - south) * VIEWPORT_MARGIN;
    const snap = (value, up, min, max) => Math.min(max, Math.max(min, Number(roundOut(value, 0.01, up).toFixed(2))));
    return [snap(west - dx, false, -180, 180), snap(south - dy, false, -90, 90), snap(east + dx, true, -180, 180), snap(north + dy, true, -90, 90)];
  }

  function bboxContains(outer, inner) {
    return !!outer && !!inner && inner[0] >= outer[0] && inner[1] >= outer[1] && inner[2] <= outer[2] && inner[3] <= outer[3];
  }

  // True when the points already on the map cover the current view fully and
  // a capped result has nothing more to show at this zoom.
  function viewportLoaded(overlay) {
    const loaded = overlay.loaded;
    if (!loaded) return false;
    if (!bboxContains(loaded.bbox, currentViewBounds())) return false;
    return !(loaded.truncated && state.map.getZoom() > loaded.zoom + 0.5);
  }

  function bindViewportReload(overlay, load) {
    if (overlay.moveBound || !state.map) return;
    overlay.moveBound = true;
    state.map.on('moveend', () => {
      if (!overlay.active) return;
      clearTimeout(overlay.moveTimer);
      overlay.moveTimer = setTimeout(() => {
        if (overlay.active && !viewportLoaded(overlay)) void load(false, 0, null, true);
      }, VIEWPORT_RELOAD_DEBOUNCE_MS);
    });
  }

  // Cancel whatever is in flight for an overlay and return a signal for the next request.
  function nextViewportSignal(overlay) {
    overlay.abort?.abort();
    overlay.abort = new AbortController();
    return overlay.abort.signal;
  }

  function salesRequestUrl(forceRefresh = false) {
    const url = new URL(window.salesMapPointsUrl || '/api/v1/sales/map-points', window.location.origin);
    const current = new URLSearchParams(window.location.search);
    current.forEach((value, key) => { if (!['lat', 'lng', 'zoom', 'parcel', 'page'].includes(key)) url.searchParams.set(key, value); });
    url.searchParams.set('period', $('salesPeriod')?.value || 'all');
    const category = $('salesCategory')?.value || '';
    if (category) url.searchParams.set('category', category);
    else url.searchParams.delete('category');
    url.searchParams.set('limit', String(VIEWPORT_POINT_LIMIT));
    url.searchParams.set('order_by', $('salesSort')?.value || 'saleability_desc');
    const bbox = viewportBbox();
    if (bbox) url.searchParams.set('bbox', bbox.join(','));
    if (forceRefresh) url.searchParams.set('refresh', '1');
    else url.searchParams.delete('refresh');
    return url.toString();
  }

  async function loadSalesOverlay(forceRefresh = false, retryAttempt = 0, pendingTask = null, viewportRefresh = false) {
    if (!salesOverlay.active || !state.map) return;
    if (belowPointOverlayZoom()) {
      addSalesOverlayLayers();
      bindViewportReload(salesOverlay, loadSalesOverlay);
      salesOverlay.fetchToken += 1;
      salesOverlay.abort?.abort();
      salesOverlay.points = [];
      salesOverlay.loaded = null;
      state.map.getSource('sales-properties')?.setData(emptyFeatureCollection());
      pendingTask?.finish();
      if ($('salesCount')) $('salesCount').textContent = `(${tr('zoom in')})`;
      if ($('salesMapStatus')) $('salesMapStatus').textContent = tr('Zoom in to see sales (from zoom {z})', { z: POINT_OVERLAY_MIN_ZOOM });
      return;
    }
    const task = pendingTask || beginMapProgress('sales', forceRefresh ? 'Refreshing sales…' : 'Loading sales…');
    let retryScheduled = false;
    const token = ++salesOverlay.fetchToken;
    const status = $('salesMapStatus');
    if (status) status.textContent = 'Loading sales…';
    try {
      addSalesOverlayLayers();
      bindViewportReload(salesOverlay, loadSalesOverlay);
      const requestedBbox = viewportBbox();
      const requestedZoom = state.map.getZoom();
      salesOverlay.loaded = { bbox: requestedBbox, truncated: false, zoom: requestedZoom };
      const response = await fetch(salesRequestUrl(forceRefresh), { credentials: 'same-origin', signal: nextViewportSignal(salesOverlay) });
      if (response.status === 503 && retryAttempt < 6) {
        const retrySeconds = Number(response.headers.get('Retry-After')) || 5;
        task.update('Preparing sales data…', null, tr('Retrying in {n}s', { n: retrySeconds }));
        retryScheduled = true;
        if (status) status.textContent = 'Sales data is loading…';
        window.setTimeout(() => {
          if (salesOverlay.active && token === salesOverlay.fetchToken) void loadSalesOverlay(false, retryAttempt + 1, task);
          else task.finish();
        }, Math.max(1000, retrySeconds * 1000));
        return;
      }
      if (!response.ok) throw new Error(response.status === 401 ? 'Sales feed requires sign-in' : 'Sales feed unavailable');
      const payload = await readMapJson(response, task);
      if (token !== salesOverlay.fetchToken) return;
      const categorySelect = $('salesCategory');
      if (categorySelect && Array.isArray(payload.categories)) {
        const selected = categorySelect.value;
        const allTypesLabel = categorySelect.options[0]?.textContent || tr('All types');
        const options = payload.categories.map((item) => `<option value="${escapeHtml(item.key)}">${escapeHtml(item.label)} (${Number(item.count || 0).toLocaleString()})</option>`).join('');
        categorySelect.innerHTML = `<option value="">${escapeHtml(allTypesLabel)}</option>${options}`;
        if ([...categorySelect.options].some((option) => option.value === selected)) categorySelect.value = selected;
      }
      const points = Array.isArray(payload) ? payload : (payload.points || payload.data || []);
      const features = await prepareSalesPoints(payload, points, task);
      if (!features || token !== salesOverlay.fetchToken || !salesOverlay.active) return;
      salesOverlay.points = features;
      const collection = salesGeoJson();
      await drawSaleMarkers('sales-properties', task, () => state.map.getSource('sales-properties').setData(collection));
      if (token !== salesOverlay.fetchToken || !salesOverlay.active) return;
      salesOverlay.loaded = { bbox: Array.isArray(payload.bbox) ? payload.bbox : requestedBbox, truncated: !!payload.truncated, zoom: requestedZoom };
      const count = collection.features.length;
      const more = payload.truncated ? ` ${tr('of {n} in view; zoom in for more', { n: Number(payload.matched || count).toLocaleString() })}` : '';
      if ($('salesCount')) $('salesCount').textContent = `(${count}${payload.truncated ? '+' : ''})`;
      if (status) status.textContent = `${count.toLocaleString()} ${tr('mapped record(s)')}${more} · ${payload.source?.relation || 'PVP view'}`;
      // Avoid zooming a user out from a parcel or fitting a national set of
      // tens of thousands of points. Small result sets can still be brought
      // into view from the map's initial country-level extent.
      if (!viewportRefresh && count && count <= 5000 && state.map.getZoom() < 7) {
        const bounds = new maplibregl.LngLatBounds();
        collection.features.forEach((feature) => bounds.extend(feature.geometry.coordinates));
        if (!bounds.isEmpty()) state.map.fitBounds(bounds, { padding: 70, maxZoom: 15, duration: 500 });
      }
    } catch (error) {
      if (error?.name === 'AbortError' || token !== salesOverlay.fetchToken || !salesOverlay.active) return;
      salesOverlay.points = [];
      salesOverlay.loaded = null;
      state.map.getSource('sales-properties')?.setData(emptyFeatureCollection());
      if ($('salesCount')) $('salesCount').textContent = '';
      if (status) status.textContent = error.message || 'Sales feed unavailable';
    } finally { if (!retryScheduled) task.finish(); }
  }

  function toggleSalesOverlay() {
    salesOverlay.active = !salesOverlay.active;
    const button = $('toggleSalesLayer');
    button?.classList.toggle('active', salesOverlay.active);
    button?.setAttribute('aria-pressed', String(salesOverlay.active));
    if (salesOverlay.active) { void loadSalesGeoLayers(); loadSalesOverlay(); }
    else {
      salesOverlay.fetchToken += 1;
      salesOverlay.abort?.abort();
      salesOverlay.loaded = null;
      mapTasks.get('sales')?.finish();
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

  // The administrative layer and "Color by" controls only mean something once
  // the statistics feed has loaded, so say why they are inert instead of
  // leaving a lone "Default" option.
  function syncSalesGeoControls() {
    const layerType = $('salesGeoLayerType');
    const metric = $('salesGeoMetric');
    const hint = $('salesGeoHint');
    let message = '';
    if (!window.salesGeoLayersUrl) message = tr('Administrative statistics are not configured.');
    else if (!salesOverlay.geo) message = tr('Turn on Sales to load administrative statistics.');
    else if (metric && metric.options.length < 2) message = tr('No statistics metrics available.');
    if (layerType) layerType.disabled = !salesOverlay.geo;
    if (metric) metric.disabled = !salesOverlay.geo || metric.options.length < 2;
    if (hint) { hint.textContent = message; hint.hidden = !message; }
  }

  async function loadSalesGeoLayers() {
    if (!window.salesGeoLayersUrl || !state.map) { syncSalesGeoControls(); return; }
    try {
      const response = await fetch(window.salesGeoLayersUrl, { credentials: 'same-origin' });
      if (!response.ok) { syncSalesGeoControls(); return; }
      salesOverlay.geo = await response.json();
      const metric = $('salesGeoMetric');
      const metrics = salesOverlay.geo?.stats_meta?.metrics || [];
      if (metric) metric.innerHTML = `<option value="none">${escapeHtml(tr('Default'))}</option>` + metrics.map((item) => `<option value="${escapeHtml(item.key)}">${escapeHtml(item.label || item.key)}</option>`).join('');
      syncSalesGeoControls();
      renderSalesGeoLayers();
    } catch (_) { syncSalesGeoControls(); /* administrative statistics are optional */ }
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

  function sisterBuildingPopupHtml(properties = {}) {
    let records = Array.isArray(properties.records) ? properties.records : [];
    if (!records.length && typeof properties.records_json === 'string') {
      try { records = JSON.parse(properties.records_json); } catch (_) { records = []; }
    }
    const value = (record, ...keys) => keys.map((key) => record?.[key]).find((item) => item !== null && item !== undefined && item !== '');
    const rows = records.map((record) => {
      const category = value(record, 'category', 'building_type') || 'Building record';
      const detail = [
        ['Class', value(record, 'cadastral_class')],
        ['Subunit', value(record, 'subunit')],
        ['Address', value(record, 'address')],
        ['Consistency', value(record, 'consistency')],
        ['Cadastral income', value(record, 'cadastral_income')],
      ].filter(([, item]) => item !== undefined).map(([label, item]) => `<div><small>${escapeHtml(label)}</small> ${escapeHtml(item)}</div>`).join('');
      return `<section class="sister-building-record"><strong>${escapeHtml(category)}</strong>${detail || '<small>Additional SISTER details are available in the parcel panel.</small>'}</section>`;
    }).join('');
    return `<div class="sister-building-popup"><strong>${escapeHtml(properties.reference || 'Selected parcel')}</strong><p>${records.length} SISTER building record(s)</p>${rows || '<p>No building records for this parcel.</p>'}</div>`;
  }

  function addSisterBuildingsOverlayLayer() {
    ensureOverlaySource('sister-building-parcel');
    if (state.map.getLayer('fill-sister-building-parcel')) return;
    state.map.addLayer({ id: 'fill-sister-building-parcel', type: 'fill', source: 'sister-building-parcel',
      paint: { 'fill-color': '#9333ea', 'fill-opacity': .2 } });
    state.map.addLayer({ id: 'line-sister-building-parcel', type: 'line', source: 'sister-building-parcel',
      paint: { 'line-color': '#7e22ce', 'line-width': 3, 'line-opacity': 1 } });
    state.map.on('mouseenter', 'fill-sister-building-parcel', () => { state.map.getCanvas().style.cursor = 'pointer'; });
    state.map.on('mouseleave', 'fill-sister-building-parcel', () => { state.map.getCanvas().style.cursor = ''; });
    state.map.on('click', 'fill-sister-building-parcel', (event) => {
      const feature = event.features?.[0];
      if (feature) new maplibregl.Popup({ maxWidth: '360px' }).setLngLat(event.lngLat).setHTML(sisterBuildingPopupHtml(feature.properties || {})).addTo(state.map);
    });
  }

  function ensureSisterParcelsOverlayLayer() {
    if (!state.map) return;
    ensureOverlaySource('sister-listed-parcels');
    if (!state.map.getLayer('fill-sister-listed-parcels')) {
      state.map.addLayer({ id: 'fill-sister-listed-parcels', type: 'fill', source: 'sister-listed-parcels',
        paint: { 'fill-color': '#0f9d91', 'fill-opacity': .34 } });
      state.map.addLayer({ id: 'line-sister-listed-parcels', type: 'line', source: 'sister-listed-parcels',
        paint: { 'line-color': '#087f79', 'line-width': 2, 'line-opacity': 1 } });
      reorderMapLayers();
    }
    ['fill-sister-listed-parcels', 'line-sister-listed-parcels'].forEach((id) => {
      if (state.map.getLayoutProperty(id, 'visibility') !== 'visible') {
        state.map.setLayoutProperty(id, 'visibility', 'visible');
      }
    });
  }

  function sisterParcelsMoveEnd() {
    clearTimeout(sisterParcelsOverlay.debounceTimer);
    sisterParcelsOverlay.debounceTimer = setTimeout(refreshSisterParcelsOverlay, 350);
  }

  async function refreshSisterParcelsOverlay() {
    const map = state.map || state.fallbackMap;
    if (!sisterParcelsOverlay.active || !map) return;
    const token = ++sisterParcelsOverlay.fetchToken;
    sisterParcelsOverlay.controller?.abort();
    const status = $('sisterParcelsStatus');
    const count = $('sisterParcelsCount');
    const zoom = map.getZoom();
    if (zoom < 14) {
      if (state.map?.getSource('sister-listed-parcels')) state.map.getSource('sister-listed-parcels').setData(emptyFeatureCollection());
      sisterParcelsOverlay.leafletLayer?.remove();
      sisterParcelsOverlay.leafletLayer = null;
      if (count) count.textContent = '';
      if (status) status.textContent = tr('Zoom in to at least level {n} to see parcels with Visura.', { n: 14 });
      return;
    }
    const bounds = map.getBounds();
    const pageSize = 5000;
    const maxCandidates = 25000;
    const params = new URLSearchParams({
      west: String(Math.max(-180, bounds.getWest())),
      south: String(Math.max(-90, bounds.getSouth())),
      east: String(Math.min(180, bounds.getEast())),
      north: String(Math.min(90, bounds.getNorth())),
      limit: String(pageSize),
    });
    const controller = new AbortController();
    sisterParcelsOverlay.controller = controller;
    const timeout = setTimeout(() => controller.abort(), 45000);
    if (status) status.textContent = tr('Loading parcels with Visura…');
    const task = beginMapProgress('sister-parcels', 'Loading parcels with Visura…');
    try {
      let offset = 0;
      let candidateCount = 0;
      let unknownCount = 0;
      let truncated = false;
      let zoomRequired = null;
      const features = [];
      do {
        params.set('offset', String(offset));
        const response = await fetch(`/api/v1/map/layers/cadastral-parcels/features?${params}`, { signal: controller.signal });
        if (!response.ok) throw new Error(tr('SISTER parcel layer is temporarily unavailable.'));
        const payload = await response.json();
        if (token !== sisterParcelsOverlay.fetchToken || !sisterParcelsOverlay.active) return;
        const candidates = Array.isArray(payload.features) ? payload.features : [];
        if (payload.zoom_required) zoomRequired = payload.zoom_required;
        candidateCount += candidates.length;
        features.push(...candidates.filter((feature) => feature.properties?.has_visura === true));
        unknownCount += candidates.filter((feature) => feature.properties?.has_visura == null).length;
        truncated = Boolean(payload.truncated);
        if (!truncated || !candidates.length || payload.zoom_required) break;
        offset += candidates.length;
      } while (truncated && offset < maxCandidates);
      // Keep the overlay bounded for dense city views. The status below asks
      // the user to zoom in if more candidates remain beyond this safety cap.
      const collection = { type: 'FeatureCollection', features };
      if (state.map) {
        ensureSisterParcelsOverlayLayer();
        state.map.getSource('sister-listed-parcels')?.setData(collection);
      } else if (state.fallbackMap) {
        sisterParcelsOverlay.leafletLayer?.remove();
        sisterParcelsOverlay.leafletLayer = features.length ? L.geoJSON(collection, {
          style: { color: '#087f79', weight: 2, fillColor: '#0f9d91', fillOpacity: .34 },
          onEachFeature: (feature, layer) => layer.on('click', (event) => {
            void identifyAtPoint(event.latlng, feature);
          }),
        }).addTo(state.fallbackMap) : null;
      }
      if (count) count.textContent = candidateCount && !features.length && unknownCount === candidateCount ? '' : `(${features.length})`;
      if (status) status.textContent = zoomRequired
        ? tr('Zoom in to at least level {n} to see parcels with Visura.', { n: zoomRequired })
        : candidateCount && !features.length && unknownCount === candidateCount
          ? tr('Visura data is not available.')
          : `${features.length} ${tr('parcels with Visura in this view')}${unknownCount ? ` · ${tr('Some parcel statuses are unavailable.')}` : ''}${truncated ? ` · ${tr('Zoom in to see more')}` : ''}`;
    } catch (error) {
      if (error.name === 'AbortError' || token !== sisterParcelsOverlay.fetchToken) return;
      state.map?.getSource('sister-listed-parcels')?.setData(emptyFeatureCollection());
      sisterParcelsOverlay.leafletLayer?.remove();
      sisterParcelsOverlay.leafletLayer = null;
      if (count) count.textContent = '';
      if (status) status.textContent = error.message || tr('SISTER parcel layer is temporarily unavailable.');
    } finally {
      clearTimeout(timeout);
      task.finish();
      if (sisterParcelsOverlay.controller === controller) sisterParcelsOverlay.controller = null;
    }
  }

  function toggleSisterParcelsOverlay() {
    const checkbox = $('toggleSisterParcels');
    sisterParcelsOverlay.active = Boolean(checkbox?.checked);
    const map = state.map || state.fallbackMap;
    if (sisterParcelsOverlay.active) {
      if (state.map) ensureSisterParcelsOverlayLayer();
      map?.on('moveend', sisterParcelsMoveEnd);
      if (state.fallbackMap) map.on('zoomend', sisterParcelsMoveEnd);
      void refreshSisterParcelsOverlay();
      return;
    }
    map?.off('moveend', sisterParcelsMoveEnd);
    if (state.fallbackMap) map.off('zoomend', sisterParcelsMoveEnd);
    clearTimeout(sisterParcelsOverlay.debounceTimer);
    sisterParcelsOverlay.fetchToken += 1;
    sisterParcelsOverlay.controller?.abort();
    mapTasks.get('sister-parcels')?.finish();
    state.map?.getSource('sister-listed-parcels')?.setData(emptyFeatureCollection());
    ['fill-sister-listed-parcels', 'line-sister-listed-parcels'].forEach((id) => {
      if (state.map?.getLayer(id) && state.map.getLayoutProperty(id, 'visibility') !== 'none') {
        state.map.setLayoutProperty(id, 'visibility', 'none');
      }
    });
    sisterParcelsOverlay.leafletLayer?.remove();
    sisterParcelsOverlay.leafletLayer = null;
    if ($('sisterParcelsCount')) $('sisterParcelsCount').textContent = '';
    if ($('sisterParcelsStatus')) $('sisterParcelsStatus').textContent = '';
  }

  function ensureAuctionParcelsOverlayLayer() {
    if (!state.map) return;
    ensureOverlaySource('auction-listed-parcels');
    if (!state.map.getLayer('fill-auction-listed-parcels')) {
      state.map.addLayer({ id: 'fill-auction-listed-parcels', type: 'fill', source: 'auction-listed-parcels',
        paint: { 'fill-color': '#f97316', 'fill-opacity': .28 } });
      state.map.addLayer({ id: 'line-auction-listed-parcels', type: 'line', source: 'auction-listed-parcels',
        paint: { 'line-color': '#c2410c', 'line-width': 1.6, 'line-dasharray': [2, 1], 'line-opacity': 1 } });
      state.map.on('mouseenter', 'fill-auction-listed-parcels', () => { state.map.getCanvas().style.cursor = 'pointer'; });
      state.map.on('mouseleave', 'fill-auction-listed-parcels', () => { state.map.getCanvas().style.cursor = ''; });
    }
    ['fill-auction-listed-parcels', 'line-auction-listed-parcels'].forEach((id) => {
      if (state.map.getLayoutProperty(id, 'visibility') !== 'visible') {
        state.map.setLayoutProperty(id, 'visibility', 'visible');
      }
    });
    reorderMapLayers();
  }

  function auctionParcelsMoveEnd() {
    clearTimeout(auctionParcelsOverlay.debounceTimer);
    auctionParcelsOverlay.debounceTimer = setTimeout(refreshAuctionParcelsOverlay, 350);
  }

  async function refreshAuctionParcelsOverlay() {
    const map = state.map || state.fallbackMap;
    if (!auctionParcelsOverlay.active || !map) return;
    const token = ++auctionParcelsOverlay.fetchToken;
    auctionParcelsOverlay.controller?.abort();
    const status = $('auctionParcelsStatus');
    const count = $('auctionParcelsCount');
    if (map.getZoom() < 14) {
      state.map?.getSource('auction-listed-parcels')?.setData(emptyFeatureCollection());
      auctionParcelsOverlay.leafletLayer?.remove();
      auctionParcelsOverlay.leafletLayer = null;
      if (count) count.textContent = '';
      if (status) status.textContent = tr('Zoom in to at least level {n} to see auction sale parcels.', { n: 14 });
      return;
    }
    const bounds = map.getBounds();
    const pageSize = 5000;
    const maxCandidates = 25000;
    const params = new URLSearchParams({
      west: String(Math.max(-180, bounds.getWest())),
      south: String(Math.max(-90, bounds.getSouth())),
      east: String(Math.min(180, bounds.getEast())),
      north: String(Math.min(90, bounds.getNorth())),
      limit: String(pageSize),
    });
    const controller = new AbortController();
    auctionParcelsOverlay.controller = controller;
    const timeout = setTimeout(() => controller.abort(), 45000);
    if (status) status.textContent = tr('Loading auction sale parcels…');
    const task = beginMapProgress('auction-parcels', 'Loading auction sale parcels…');
    try {
      let offset = 0;
      let truncated = false;
      let zoomRequired = null;
      const features = [];
      do {
        params.set('offset', String(offset));
        const response = await fetch(`/api/v1/map/layers/cadastral-parcels/features?${params}`, { signal: controller.signal });
        if (!response.ok) throw new Error(tr('Auction parcel layer is temporarily unavailable.'));
        const payload = await response.json();
        if (token !== auctionParcelsOverlay.fetchToken || !auctionParcelsOverlay.active) return;
        const candidates = Array.isArray(payload.features) ? payload.features : [];
        if (payload.zoom_required) zoomRequired = payload.zoom_required;
        features.push(...candidates.filter((feature) => feature.properties?.is_auction_sale === true));
        truncated = Boolean(payload.truncated);
        if (!truncated || !candidates.length || payload.zoom_required) break;
        offset += candidates.length;
      } while (truncated && offset < maxCandidates);
      const collection = { type: 'FeatureCollection', features };
      if (state.map) {
        ensureAuctionParcelsOverlayLayer();
        state.map.getSource('auction-listed-parcels')?.setData(collection);
      } else if (state.fallbackMap) {
        auctionParcelsOverlay.leafletLayer?.remove();
        auctionParcelsOverlay.leafletLayer = features.length ? L.geoJSON(collection, {
          style: { color: '#c2410c', weight: 2, dashArray: '5 3', fillColor: '#f97316', fillOpacity: .28 },
          onEachFeature: (feature, layer) => layer.on('click', (event) => {
            void identifyAtPoint(event.latlng, feature);
          }),
        }).addTo(state.fallbackMap) : null;
      }
      if (count) count.textContent = `(${features.length})`;
      if (status) status.textContent = zoomRequired
        ? tr('Zoom in to at least level {n} to see auction sale parcels.', { n: zoomRequired })
        : `${features.length} ${tr('auction sale parcels in this view')}${truncated ? ` · ${tr('Zoom in to see more')}` : ''}`;
    } catch (error) {
      if (error.name === 'AbortError' || token !== auctionParcelsOverlay.fetchToken) return;
      state.map?.getSource('auction-listed-parcels')?.setData(emptyFeatureCollection());
      auctionParcelsOverlay.leafletLayer?.remove();
      auctionParcelsOverlay.leafletLayer = null;
      if (count) count.textContent = '';
      if (status) status.textContent = error.message || tr('Auction parcel layer is temporarily unavailable.');
    } finally {
      clearTimeout(timeout);
      task.finish();
      if (auctionParcelsOverlay.controller === controller) auctionParcelsOverlay.controller = null;
    }
  }

  function toggleAuctionParcelsOverlay() {
    const checkbox = $('toggleAuctionHighlight');
    auctionParcelsOverlay.active = Boolean(checkbox?.checked);
    const map = state.map || state.fallbackMap;
    if (auctionParcelsOverlay.active) {
      if (state.map) ensureAuctionParcelsOverlayLayer();
      map?.on('moveend', auctionParcelsMoveEnd);
      if (state.fallbackMap) map.on('zoomend', auctionParcelsMoveEnd);
      void refreshAuctionParcelsOverlay();
      return;
    }
    map?.off('moveend', auctionParcelsMoveEnd);
    if (state.fallbackMap) map?.off('zoomend', auctionParcelsMoveEnd);
    clearTimeout(auctionParcelsOverlay.debounceTimer);
    auctionParcelsOverlay.fetchToken += 1;
    auctionParcelsOverlay.controller?.abort();
    mapTasks.get('auction-parcels')?.finish();
    state.map?.getSource('auction-listed-parcels')?.setData(emptyFeatureCollection());
    ['fill-auction-listed-parcels', 'line-auction-listed-parcels'].forEach((id) => {
      if (state.map?.getLayer(id) && state.map.getLayoutProperty(id, 'visibility') !== 'none') {
        state.map.setLayoutProperty(id, 'visibility', 'none');
      }
    });
    auctionParcelsOverlay.leafletLayer?.remove();
    auctionParcelsOverlay.leafletLayer = null;
    if ($('auctionParcelsCount')) $('auctionParcelsCount').textContent = '';
    if ($('auctionParcelsStatus')) $('auctionParcelsStatus').textContent = '';
  }

  const parcelFlagRefreshDelay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

  async function refreshParcelFlag(flag) {
    const isVisura = flag === 'has_visura';
    const button = $(isVisura ? 'refreshVisuraFlagsButton' : 'refreshAuctionFlagsButton');
    const status = $(isVisura ? 'sisterParcelsStatus' : 'auctionParcelsStatus');
    const refreshOverlay = isVisura ? refreshSisterParcelsOverlay : refreshAuctionParcelsOverlay;
    if (!button || button.disabled) return;

    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    if (status) status.textContent = tr('Starting parcel flag refresh…');
    try {
      const auth = window.authManager;
      if (!auth?.authenticatedFetch) throw new Error(tr('Sign in to refresh parcel flags.'));
      const startResponse = await auth.authenticatedFetch(`/api/v1/map/parcel-flags/${flag}/refresh`, { method: 'POST' });
      if (!startResponse.ok) {
        const errorPayload = await startResponse.json().catch(() => ({}));
        throw new Error(errorPayload.detail || tr('Parcel flag refresh request failed.'));
      }
      let job = await startResponse.json();
      if (status) status.textContent = tr('Parcel flag refresh is running…');

      while (job.status === 'queued' || job.status === 'running') {
        await parcelFlagRefreshDelay(2000);
        const response = await auth.authenticatedFetch(
          `/api/v1/map/parcel-flags/refresh/${encodeURIComponent(job.job_id)}`
        );
        if (!response.ok) {
          const errorPayload = await response.json().catch(() => ({}));
          throw new Error(errorPayload.detail || tr('Could not read parcel flag refresh status.'));
        }
        job = await response.json();
      }

      if (job.status !== 'completed') {
        throw new Error(job.error || tr('Parcel flag refresh failed.'));
      }
      const result = job.result || {};
      if (status) {
        status.textContent = isVisura
          ? tr('Refresh completed: {updated} rows updated ({scanned} scanned).', {
              updated: result.updated ?? 0,
              scanned: result.scanned ?? 0,
            })
          : tr('Refresh completed: {updated} rows marked and {cleared} stale rows cleared.', {
              updated: result.set_true ?? 0,
              cleared: result.cleared ?? 0,
            });
      }
      void refreshOverlay();
    } catch (error) {
      console.error('Cadastral parcel flag refresh failed:', error);
      if (status) status.textContent = tr('Parcel flag refresh failed: {message}', {
        message: error?.message || tr('Unknown error'),
      });
    } finally {
      button.disabled = false;
      button.removeAttribute('aria-busy');
    }
  }

  async function loadSisterBuildingsOverlay() {
    if (!sisterBuildingsOverlay.active || !state.map) return;
    const reference = state.selectedReference;
    const feature = state.selectedFeature;
    const status = $('sisterBuildingsStatus');
    const count = $('sisterBuildingsCount');
    const token = ++sisterBuildingsOverlay.fetchToken;
    addSisterBuildingsOverlayLayer();
    state.map.getSource('sister-building-parcel')?.setData(emptyFeatureCollection());
    sisterBuildingsOverlay.records = [];
    if (count) count.textContent = '';
    if (!reference || !feature?.geometry) {
      if (status) status.textContent = 'Select a parcel to check SISTER records.';
      return;
    }
    if (status) status.textContent = 'Loading SISTER records…';
    const task = beginMapProgress('sister-buildings', 'Loading SISTER records…');
    try {
      const response = await fetch(`/api/v1/enrichment/parcel/buildings/${encodeURIComponent(reference)}`);
      if (!response.ok) throw new Error('SISTER records are temporarily unavailable.');
      const payload = await response.json();
      if (token !== sisterBuildingsOverlay.fetchToken || reference !== state.selectedReference) return;
      const records = Array.isArray(payload.buildings) ? payload.buildings : [];
      sisterBuildingsOverlay.records = records;
      if (records.length) {
        state.map.getSource('sister-building-parcel')?.setData({
          type: 'FeatureCollection',
          features: [{ type: 'Feature', id: feature.id, geometry: feature.geometry, properties: { reference, records_json: JSON.stringify(records), record_count: records.length } }],
        });
      }
      if (count) count.textContent = records.length ? `(${records.length})` : '(0)';
      if (status) status.textContent = records.length
        ? `${records.length} SISTER building record(s) for the selected parcel. Click its outline for details.`
        : (payload.available ? 'No SISTER building records found for this parcel.' : 'SISTER building records are not available for this parcel.');
    } catch (error) {
      if (token !== sisterBuildingsOverlay.fetchToken) return;
      if (status) status.textContent = error.message || 'SISTER records are temporarily unavailable.';
    } finally { task.finish(); }
  }

  function toggleSisterBuildingsOverlay() {
    sisterBuildingsOverlay.active = !sisterBuildingsOverlay.active;
    const button = $('toggleSisterBuildings');
    button?.classList.toggle('active', sisterBuildingsOverlay.active);
    button?.setAttribute('aria-pressed', String(sisterBuildingsOverlay.active));
    if (sisterBuildingsOverlay.active) void loadSisterBuildingsOverlay();
    else {
      sisterBuildingsOverlay.fetchToken += 1;
      mapTasks.get('sister-buildings')?.finish();
      sisterBuildingsOverlay.records = [];
      state.map?.getSource('sister-building-parcel')?.setData(emptyFeatureCollection());
      if ($('sisterBuildingsCount')) $('sisterBuildingsCount').textContent = '';
      if ($('sisterBuildingsStatus')) $('sisterBuildingsStatus').textContent = '';
    }
  }

  function selectedPoiCategories() {
    const group = $('poiCategories');
    if (!group) return [];
    return [...group.querySelectorAll('input[type="checkbox"]:checked')].map((input) => input.value).filter(Boolean);
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
    const group = $('poiCategories');
    if (!group) return;
    Object.entries(POI_CATEGORY_META).forEach(([key, meta]) => {
      const label = document.createElement('label');
      const input = document.createElement('input');
      input.type = 'checkbox'; input.value = key; input.checked = true;
      const text = document.createElement('span');
      text.textContent = window.t?.(meta.label) || meta.label;
      label.append(input, text);
      group.appendChild(label);
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
    const task = beginMapProgress('poi', 'Loading points of interest…');
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
    finally { task.finish(); }
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
      enrichmentOverlays.poi.fetchToken += 1;
      mapTasks.get('poi')?.finish();
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
    const task = beginMapProgress('fires', 'Loading fire detections…');
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
    } finally { task.finish(); }
  }

  function toggleFiresOverlay() {
    const button = $('toggleEnrichmentFires');
    enrichmentOverlays.fires.active = !enrichmentOverlays.fires.active;
    button?.classList.toggle('active', enrichmentOverlays.fires.active);
    button?.setAttribute('aria-pressed', String(enrichmentOverlays.fires.active));
    if (enrichmentOverlays.fires.active) { addFiresOverlayLayer(); refreshFiresOverlay(); }
    else {
      mapTasks.get('fires')?.finish();
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
    const task = beginMapProgress('bulletin', 'Loading weather alerts…');
    try {
      const response = await fetch('/api/v1/enrichment/bulletin');
      if (!response.ok || token !== enrichmentOverlays.bulletin.fetchToken) return;
      const data = await response.json();
      const topology = data?.today_zones;
      if (data?.stale) {
        state.map.getSource('enrichment-bulletin')?.setData(emptyFeatureCollection());
        if (countElement) countElement.textContent = '';
        renderLegend('enrichmentBulletinLegend', []);
        mapStatus('Civil Protection bulletin has expired; current alerts are unavailable.', true);
        return;
      }
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
    } finally { task.finish(); }
  }

  function toggleBulletinOverlay() {
    const button = $('toggleEnrichmentBulletin');
    enrichmentOverlays.bulletin.active = !enrichmentOverlays.bulletin.active;
    button?.classList.toggle('active', enrichmentOverlays.bulletin.active);
    button?.setAttribute('aria-pressed', String(enrichmentOverlays.bulletin.active));
    if (enrichmentOverlays.bulletin.active) { addBulletinOverlayLayer(); refreshBulletinOverlay(); }
    else {
      enrichmentOverlays.bulletin.fetchToken += 1;
      mapTasks.get('bulletin')?.finish();
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
    const task = beginMapProgress('adjacent-parcels', 'Looking for adjacent parcels…');
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
      task.finish();
      if (button) { button.disabled = false; button.textContent = 'Find adjacent parcels'; }
    }
  }

  // ---- Auction/market filters (legacy Actions panel: toggleAuctionLayer,
  // filterAuctionsByType, filterAuctionsByPrice, filterActiveAuctions) ----
  const auctionOverlay = { active: false, points: [], fetchToken: 0, loaded: null, abort: null, moveBound: false, moveTimer: null };

  function addAuctionOverlayLayer() {
    if (!state.map.getSource('auction-properties')) {
      state.map.addSource('auction-properties', { type: 'geojson', data: emptyFeatureCollection(), cluster: true, clusterMaxZoom: 14, clusterRadius: 45 });
    }
    if (state.map.getLayer('point-auction-properties')) return;
    state.map.addLayer({ id: 'auction-clusters', type: 'circle', source: 'auction-properties', filter: ['has', 'point_count'],
      paint: { 'circle-color': '#0f766e', 'circle-radius': ['step', ['get', 'point_count'], 17, 25, 22, 100, 29], 'circle-opacity': .9, 'circle-stroke-color': '#fff', 'circle-stroke-width': 2 } });
    state.map.addLayer({ id: 'auction-cluster-count', type: 'symbol', source: 'auction-properties', filter: ['has', 'point_count'],
      layout: { 'text-field': ['get', 'point_count_abbreviated'], 'text-font': ['noto-sans-regular'], 'text-size': 11 }, paint: { 'text-color': '#fff' } });
    state.map.addLayer({ id: 'point-auction-properties', type: 'circle', source: 'auction-properties', filter: ['!', ['has', 'point_count']],
      paint: { 'circle-radius': ['case', ['==', ['get', 'approximate'], 1], 5, 7],
        'circle-color': ['match', ['get', 'category'], 'residential', '#2563eb', 'land', '#16a34a', 'parking_storage', '#f59e0b', 'commercial', '#db2777', 'industrial', '#7c3aed', 'building', '#0891b2', 'movable', '#ea580c', '#64748b'],
        'circle-opacity': ['case', ['==', ['get', 'approximate'], 1], .55, .92], 'circle-stroke-color': '#fff', 'circle-stroke-width': 1.5 } });
    state.map.on('click', 'auction-clusters', (event) => {
      const feature = event.features?.[0];
      if (!feature) return;
      state.map.getSource('auction-properties')?.getClusterExpansionZoom(feature.properties.cluster_id, (error, zoom) => {
        if (!error) state.map.easeTo({ center: feature.geometry.coordinates, zoom });
      });
    });
    state.map.on('mouseenter', 'point-auction-properties', () => { state.map.getCanvas().style.cursor = 'pointer'; });
    state.map.on('mouseleave', 'point-auction-properties', () => { state.map.getCanvas().style.cursor = ''; });
    state.map.on('mouseenter', 'auction-clusters', () => { state.map.getCanvas().style.cursor = 'pointer'; });
    state.map.on('mouseleave', 'auction-clusters', () => { state.map.getCanvas().style.cursor = ''; });
    state.map.on('click', 'point-auction-properties', async (event) => {
      const feature = event.features?.[0];
      if (!feature) return;
      const props = feature.properties;
      const popup = new maplibregl.Popup({ maxWidth: '360px' }).setLngLat(event.lngLat).setHTML(salesPopupHtml(props)).addTo(state.map);
      if (!props.id) return;
      const task = beginMapProgress('sale-detail', 'Loading auction details…');
      try {
        const response = await fetch(`/api/v1/sales/pvp/${encodeURIComponent(props.id)}`, { credentials: 'same-origin' });
        if (!response.ok) throw new Error('Sale details unavailable');
        const detail = await response.json();
        if (popup.isOpen()) popup.setHTML(salesPopupHtml({ ...props, ...detail, display_title: detail.property_type || props.display_title }));
      } catch (_) {
        if (popup.isOpen()) popup.setHTML(salesPopupHtml({ ...props, detail_error: true }));
      } finally { task.finish(); }
    });
    reorderMapLayers();
  }

  async function loadAuctionOverlay(forceRefresh = false, retryAttempt = 0, pendingTask = null, viewportRefresh = false) {
    if (!auctionOverlay.active || !state.map) return;
    if (belowPointOverlayZoom()) {
      bindViewportReload(auctionOverlay, loadAuctionOverlay);
      auctionOverlay.fetchToken += 1;
      auctionOverlay.abort?.abort();
      auctionOverlay.points = [];
      auctionOverlay.loaded = null;
      state.map.getSource('auction-properties')?.setData(emptyFeatureCollection());
      pendingTask?.finish();
      if ($('auctionCount')) $('auctionCount').textContent = `(${tr('zoom in')})`;
      mapStatus(tr('Zoom in to see auction listings (from zoom {z})', { z: POINT_OVERLAY_MIN_ZOOM }));
      return;
    }
    const task = pendingTask || beginMapProgress('auctions', forceRefresh ? 'Refreshing auction listings…' : 'Loading auction listings…');
    let retryScheduled = false;
    const countElement = $('auctionCount');
    const token = ++auctionOverlay.fetchToken;
    if (countElement && !viewportRefresh) countElement.textContent = '(…)';
    try {
      bindViewportReload(auctionOverlay, loadAuctionOverlay);
      const bbox = viewportBbox();
      const zoom = state.map.getZoom();
      // Record the requested box now so a pan inside it does not start a second request.
      auctionOverlay.loaded = { bbox, truncated: false, zoom };
      const query = new URLSearchParams({ period: 'all', limit: String(VIEWPORT_POINT_LIMIT) });
      if (bbox) query.set('bbox', bbox.join(','));
      if (forceRefresh) query.set('refresh', '1');
      const response = await fetch(`/api/v1/sales/map-points?${query}`, { credentials: 'same-origin', signal: nextViewportSignal(auctionOverlay) });
      if (response.status === 503 && retryAttempt < 6) {
        const retrySeconds = Number(response.headers.get('Retry-After')) || 5;
        task.update('Preparing auction data…', null, tr('Retrying in {n}s', { n: retrySeconds }));
        retryScheduled = true;
        if (countElement) countElement.textContent = '(loading…)';
        window.setTimeout(() => {
          if (auctionOverlay.active && token === auctionOverlay.fetchToken) void loadAuctionOverlay(false, retryAttempt + 1, task);
          else task.finish();
        }, Math.max(1000, retrySeconds * 1000));
        return;
      }
      if (!response.ok) throw new Error(response.status === 503 ? 'Enriched PVP sales data is unavailable' : 'Auction records unavailable');
      const payload = await readMapJson(response, task);
      if (token !== auctionOverlay.fetchToken) return;
      const rawPoints = Array.isArray(payload.points) ? payload.points : [];
      const features = await prepareSalesPoints(payload, rawPoints, task, true);
      if (!features || token !== auctionOverlay.fetchToken || !auctionOverlay.active) return;
      auctionOverlay.points = features;
      const select = $('auctionTypeFilter');
      if (select && Array.isArray(payload.categories)) {
        const selected = select.value;
        const allTypesLabel = select.options[0]?.textContent || 'All types';
        const options = payload.categories.map((item) => `<option value="${escapeHtml(item.key)}">${escapeHtml(item.label)}</option>`).join('');
        select.innerHTML = `<option value="">${escapeHtml(allTypesLabel)}</option>${options}`;
        if ([...select.options].some((option) => option.value === selected)) select.value = selected;
      }
      await drawSaleMarkers('auction-properties', task, applyAuctionFilter);
      if (token !== auctionOverlay.fetchToken || !auctionOverlay.active) return;
      auctionOverlay.loaded = { bbox: Array.isArray(payload.bbox) ? payload.bbox : bbox, truncated: !!payload.truncated, zoom };
      const inView = auctionOverlay.points.length.toLocaleString();
      if (!viewportRefresh || payload.truncated) {
        mapStatus(payload.truncated
          ? tr('{n} auction records shown of {total} in view; zoom in for more', { n: inView, total: Number(payload.matched || 0).toLocaleString() })
          : `${inView} auction records in view from ${payload.source?.relation || 'PVP view'}`);
      }
    } catch (error) {
      if (error?.name === 'AbortError' || token !== auctionOverlay.fetchToken || !auctionOverlay.active) return;
      auctionOverlay.points = [];
      auctionOverlay.loaded = null;
      state.map.getSource('auction-properties')?.setData(emptyFeatureCollection());
      if (countElement) countElement.textContent = '';
      mapStatus(error.message || 'Auction records unavailable', true);
    } finally { if (!retryScheduled) task.finish(); }
  }

  function applyAuctionFilter() {
    if (!state.map?.getLayer('point-auction-properties')) return;
    const type = $('auctionTypeFilter')?.value || '';
    const activeOnly = $('auctionActiveOnly')?.checked;
    const maxPrice = Number($('auctionMaxPrice')?.value);
    const now = new Date();
    const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
    const features = auctionOverlay.points.filter((feature) => {
      const properties = feature.properties || {};
      if (type && properties.category !== type) return false;
      if (activeOnly && (!properties.date || properties.date < today)) return false;
      const price = numberValue(properties.price);
      return !(Number.isFinite(maxPrice) && maxPrice > 0) || (price !== null && price <= maxPrice);
    });
    state.map.getSource('auction-properties')?.setData({ type: 'FeatureCollection', features });
    const countElement = $('auctionCount');
    if (countElement) countElement.textContent = `(${features.length.toLocaleString()})`;
  }

  function toggleAuctionOverlay() {
    const button = $('toggleAuctionLayer');
    auctionOverlay.active = !auctionOverlay.active;
    button?.classList.toggle('active', auctionOverlay.active);
    button?.setAttribute('aria-pressed', String(auctionOverlay.active));
    if (auctionOverlay.active) { addAuctionOverlayLayer(); loadAuctionOverlay(); }
    else {
      auctionOverlay.fetchToken += 1;
      auctionOverlay.abort?.abort();
      clearTimeout(auctionOverlay.moveTimer);
      auctionOverlay.loaded = null;
      mapTasks.get('auctions')?.finish();
      mapTasks.get('auction-filter')?.finish();
      auctionOverlay.points = [];
      state.map?.getSource('auction-properties')?.setData(emptyFeatureCollection());
      const countElement = $('auctionCount');
      if (countElement) countElement.textContent = '';
    }
  }

  function updateAuctionFilter() {
    if (!auctionOverlay.active || !state.map?.getLayer('point-auction-properties')) return;
    if (mapTasks.has('auctions')) { applyAuctionFilter(); return; }
    const task = beginMapProgress('auction-filter', 'Updating auction markers…');
    void drawSaleMarkers('auction-properties', task, applyAuctionFilter)
      .catch((error) => { if (task.current()) mapStatus(error.message || 'Auction records unavailable', true); })
      .finally(() => task.finish());
  }

  // ---- Shared workbench selection, drawings, and map utilities ----
  const workbench = state.workbench;

  function analysisFeatureKey(feature) {
    const key = window.MapWorkbench?.featureKey(feature) || sisterBatchKey(feature);
    if (!key) return null;
    const source = feature?.layer?.source || '';
    const sourceLayer = feature?.layer?.['source-layer'] || '';
    const layerId = source.startsWith('source-') ? source.slice(7) : sourceLayer || tableView.layerId || 'map';
    return `${layerId}:${key}`;
  }

  function setGeoJSONOverlay(sourceId, collection, layers) {
    if (state.map) {
      if (!state.map.isStyleLoaded()) {
        state.map.once('style.load', () => setGeoJSONOverlay(sourceId, collection, layers));
        return;
      }
      if (!state.map.getSource(sourceId)) {
        state.map.addSource(sourceId, { type: 'geojson', data: collection });
        layers.forEach((layer) => state.map.addLayer({ ...layer, source: sourceId }));
      } else state.map.getSource(sourceId).setData(collection);
      reorderMapLayers();
      return;
    }
    if (!state.fallbackMap || !window.L) return;
    const oldLayer = workbench.fallbackLayers.get(sourceId);
    if (oldLayer) state.fallbackMap.removeLayer(oldLayer);
    const nextLayer = collection.features.length
      ? window.L.geoJSON(collection, {
        style: { color: sourceId === 'analysis-selection' ? '#d97706' : '#2563eb', weight: 3, fillColor: sourceId === 'analysis-selection' ? '#f59e0b' : '#3b82f6', fillOpacity: .2 },
        pointToLayer: (_, latlng) => window.L.circleMarker(latlng, { radius: 6, color: '#2563eb', weight: 2, fillOpacity: .7 }),
      }).addTo(state.fallbackMap) : null;
    workbench.fallbackLayers.set(sourceId, nextLayer);
  }

  function renderAnalysisSelection() {
    const features = [...workbench.selection.values()];
    workbench.adjacencyRequest += 1;
    setGeoJSONOverlay('analysis-selection', { type: 'FeatureCollection', features }, [
      { id: 'analysis-selection-fill', type: 'fill', paint: { 'fill-color': '#f59e0b', 'fill-opacity': .24 } },
      { id: 'analysis-selection-line', type: 'line', paint: { 'line-color': '#b45309', 'line-width': 3 } },
      { id: 'analysis-selection-point', type: 'circle', paint: { 'circle-color': '#f59e0b', 'circle-radius': 7, 'circle-stroke-color': '#fff', 'circle-stroke-width': 2 } },
    ]);
    const count = $('mapAnalysisSelectionCount');
    if (count) count.textContent = tr('{n} selected', { n: features.length.toLocaleString() });
    const clear = $('mapClearAnalysisSelectionButton');
    if (clear) clear.disabled = features.length === 0;
    renderAdjacencyRows(features, []);
    const adjacencyStatus = $('mapAdjacencyStatus');
    if (adjacencyStatus) adjacencyStatus.textContent = features.length
      ? tr('Selection changed. Run adjacency analysis to refresh the results.')
      : tr('Select parcels in the table to analyze them.');
    renderTable();
  }

  function toggleAnalysisFeature(feature) {
    const key = analysisFeatureKey(feature);
    if (!key) return;
    if (workbench.selection.has(key)) workbench.selection.delete(key);
    else addAnalysisFeature(feature);
    renderAnalysisSelection();
  }

  function addAnalysisFeature(feature) {
    const key = analysisFeatureKey(feature);
    if (!key || workbench.selection.has(key)) return true;
    if (workbench.selection.size >= 5000) return false;
    workbench.selection.set(key, feature);
    return true;
  }

  function pointInRing(point, ring) {
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const [xi, yi] = ring[i]; const [xj, yj] = ring[j];
      const crosses = ((yi > point[1]) !== (yj > point[1]))
        && point[0] < ((xj - xi) * (point[1] - yi)) / ((yj - yi) || Number.EPSILON) + xi;
      if (crosses) inside = !inside;
    }
    return inside;
  }

  function pointInPolygon(point, polygon) {
    if (!polygon?.length || !pointInRing(point, polygon[0])) return false;
    return !polygon.slice(1).some((ring) => pointInRing(point, ring));
  }

  function orientation(a, b, c) {
    const value = (b[1] - a[1]) * (c[0] - b[0]) - (b[0] - a[0]) * (c[1] - b[1]);
    return Math.abs(value) < 1e-12 ? 0 : (value > 0 ? 1 : 2);
  }

  function segmentsIntersect(a, b, c, d) {
    const o1 = orientation(a, b, c); const o2 = orientation(a, b, d);
    const o3 = orientation(c, d, a); const o4 = orientation(c, d, b);
    if (o1 !== o2 && o3 !== o4) return true;
    const onSegment = (p, q, r) => q[0] <= Math.max(p[0], r[0]) && q[0] >= Math.min(p[0], r[0])
      && q[1] <= Math.max(p[1], r[1]) && q[1] >= Math.min(p[1], r[1]);
    return (o1 === 0 && onSegment(a, c, b)) || (o2 === 0 && onSegment(a, d, b))
      || (o3 === 0 && onSegment(c, a, d)) || (o4 === 0 && onSegment(c, b, d));
  }

  function ringsIntersect(left, right) {
    for (let i = 1; i < left.length; i += 1) {
      for (let j = 1; j < right.length; j += 1) {
        if (segmentsIntersect(left[i - 1], left[i], right[j - 1], right[j])) return true;
      }
    }
    return false;
  }

  function polygonIntersects(left, right) {
    if (!left?.length || !right?.length) return false;
    if (ringsIntersect(left[0], right[0])) return true;
    return pointInPolygon(left[0][0], right) || pointInPolygon(right[0][0], left);
  }

  function geometryIntersectsPolygon(geometry, polygon) {
    if (!geometry) return false;
    if (geometry.type === 'Point') return pointInPolygon(geometry.coordinates, polygon);
    if (geometry.type === 'MultiPoint') return geometry.coordinates.some((point) => pointInPolygon(point, polygon));
    if (geometry.type === 'LineString') return ringsIntersect(geometry.coordinates, polygon[0])
      || geometry.coordinates.some((point) => pointInPolygon(point, polygon));
    if (geometry.type === 'MultiLineString') return geometry.coordinates.some((line) => geometryIntersectsPolygon({ type: 'LineString', coordinates: line }, polygon));
    if (geometry.type === 'Polygon') return polygonIntersects(geometry.coordinates, polygon);
    if (geometry.type === 'MultiPolygon') return geometry.coordinates.some((part) => polygonIntersects(part, polygon));
    if (geometry.type === 'GeometryCollection') return geometry.geometries.some((part) => geometryIntersectsPolygon(part, polygon));
    return false;
  }

  function geometryCoordinates(geometry, output = []) {
    if (!geometry) return output;
    if (geometry.type === 'GeometryCollection') geometry.geometries.forEach((part) => geometryCoordinates(part, output));
    else if (geometry.type === 'Point') output.push(geometry.coordinates);
    else geometry.coordinates?.forEach((part) => {
      if (typeof part?.[0] === 'number') output.push(part);
      else part.forEach((point) => output.push(point));
    });
    return output;
  }

  function pointInFeature(point, feature) {
    const geometry = feature?.geometry;
    if (geometry?.type === 'Polygon') return pointInPolygon(point, geometry.coordinates);
    if (geometry?.type === 'MultiPolygon') return geometry.coordinates.some((polygon) => pointInPolygon(point, polygon));
    return false;
  }

  function mapFeaturesInPolygon(polygon) {
    const features = new Map();
    if (state.map) {
      const positions = polygon[0].map((coordinate) => state.map.project(coordinate));
      const box = positions.reduce((bounds, point) => ({
        minX: Math.min(bounds.minX, point.x), maxX: Math.max(bounds.maxX, point.x),
        minY: Math.min(bounds.minY, point.y), maxY: Math.max(bounds.maxY, point.y),
      }), { minX: Infinity, maxX: -Infinity, minY: Infinity, maxY: -Infinity });
      const candidates = state.map.queryRenderedFeatures([[box.minX, box.minY], [box.maxX, box.maxY]], {
        layers: state.map.getLayer('fill-cadastral-parcels') ? ['fill-cadastral-parcels'] : [],
      });
      candidates.forEach((feature) => {
        const key = analysisFeatureKey(feature);
        if (key && geometryIntersectsPolygon(feature.geometry, polygon)) features.set(key, feature);
      });
    }
    tableView.rawFeatures.forEach((feature) => {
      const key = analysisFeatureKey(feature);
      if (key && geometryIntersectsPolygon(feature.geometry, polygon)) features.set(key, feature);
    });
    return [...features.values()];
  }

  function toggleWorkbenchMode(mode) {
    if (state.sisterBatch.mode) setSisterBatchMode(null);
    workbench.editingDrawing = false;
    workbench.editVertex = null;
    $('mapEditDrawingButton')?.setAttribute('aria-pressed', 'false');
    workbench.mode = workbench.mode === mode ? null : mode;
    workbench.vertices = [];
    workbench.circleCenter = null;
    workbench.editVertex = null;
    updateWorkbenchModeUi();
    renderWorkbenchPreview();
  }

  function updateWorkbenchModeUi() {
    document.querySelectorAll('[data-map-draw]').forEach((button) => {
      button.setAttribute('aria-pressed', String(workbench.mode === `draw-${button.dataset.mapDraw}`));
    });
    const selectMap = $('mapSelectOnMapButton');
    if (selectMap) selectMap.setAttribute('aria-pressed', String(workbench.mode === 'select-click'));
    const polygon = $('mapSelectPolygonButton');
    if (polygon) polygon.setAttribute('aria-pressed', String(workbench.mode === 'select-polygon'));
    const circle = $('mapSelectCircleButton');
    if (circle) circle.setAttribute('aria-pressed', String(workbench.mode === 'select-circle'));
    const canvas = state.map?.getCanvas();
    if (canvas) canvas.style.cursor = workbench.mode || workbench.editingDrawing ? 'crosshair' : '';
    if (state.fallbackMap) state.fallbackMap.getContainer().style.cursor = workbench.mode || workbench.editingDrawing ? 'crosshair' : '';
    const drawDoubleClick = ['select-polygon', 'draw-polygon', 'draw-line'].includes(workbench.mode);
    if (state.map?.doubleClickZoom) drawDoubleClick ? state.map.doubleClickZoom.disable() : state.map.doubleClickZoom.enable();
    if (state.fallbackMap?.doubleClickZoom) drawDoubleClick ? state.fallbackMap.doubleClickZoom.disable() : state.fallbackMap.doubleClickZoom.enable();
    const status = $('mapDrawingStatus');
    if (status && workbench.mode?.startsWith('draw-')) status.textContent = tr('Click to add vertices; double-click to finish the drawing.');
  }

  function workbenchCircleSelectionRing(center, edge) {
    const radius = Math.hypot(edge[0] - center[0], edge[1] - center[1]);
    const latitudeScale = Math.max(.2, Math.cos(center[1] * Math.PI / 180));
    const coordinates = [];
    for (let step = 0; step <= 64; step += 1) {
      const angle = step / 64 * Math.PI * 2;
      coordinates.push([center[0] + Math.cos(angle) * radius / latitudeScale, center[1] + Math.sin(angle) * radius]);
    }
    return [coordinates];
  }

  function interactionClick(lngLat, feature = null) {
    if (workbench.editingDrawing) {
      editDrawingAt(lngLat);
      return true;
    }
    if (!workbench.mode) return false;
    const point = [lngLat.lng, lngLat.lat];
    if (workbench.mode === 'select-click') {
      if (feature) toggleAnalysisFeature(feature);
      else {
        const candidates = state.map ? state.map.queryRenderedFeatures(state.map.project(point), { layers: state.map.getLayer('fill-cadastral-parcels') ? ['fill-cadastral-parcels'] : [] }) : tableView.rawFeatures;
        const found = candidates.find((candidate) => pointInFeature(point, candidate));
        if (found) toggleAnalysisFeature(found);
      }
      return true;
    }
    if (workbench.mode === 'select-polygon' || workbench.mode === 'draw-polygon' || workbench.mode === 'draw-line') {
      workbench.vertices.push(point);
      renderWorkbenchPreview();
      return true;
    }
    if (workbench.mode === 'select-circle') {
      if (!workbench.circleCenter) workbench.circleCenter = point;
      else {
      const polygon = workbenchCircleSelectionRing(workbench.circleCenter, point);
        const selected = mapFeaturesInPolygon(polygon);
        selected.forEach((parcel) => addAnalysisFeature(parcel));
        workbench.circleCenter = null;
        toggleWorkbenchMode('select-circle');
        renderAnalysisSelection();
        if (selected.length && workbench.selection.size >= 5000) mapStatus(tr('Selection is limited to 5,000 features.'), true);
      }
      return true;
    }
    if (workbench.mode === 'draw-point') {
      const feature = { type: 'Feature', id: `drawing-${Date.now()}`, properties: { name: tr('Marker') }, geometry: { type: 'Point', coordinates: point } };
      workbench.drawings.push(feature);
      workbench.selectedDrawing = feature.id;
      workbench.mode = null;
      renderWorkbenchDrawings();
      updateWorkbenchModeUi();
      return true;
    }
    return false;
  }

  function finishWorkbenchPolygon() {
    const mode = workbench.mode;
    const vertices = workbench.vertices;
    if (!vertices.length) return;
    workbench.vertices = [];
    if (mode === 'select-polygon') {
      if (vertices.length < 3) { mapStatus(tr('Add at least three corners to select parcels.'), true); return; }
      const polygon = [...vertices, vertices[0]];
      const selected = mapFeaturesInPolygon([polygon]);
      selected.forEach((feature) => addAnalysisFeature(feature));
      workbench.mode = null;
      renderAnalysisSelection();
      if (selected.length && workbench.selection.size >= 5000) mapStatus(tr('Selection is limited to 5,000 features.'), true);
    } else if (mode === 'draw-polygon' && vertices.length >= 3) {
      const coordinates = [...vertices, vertices[0]];
      const feature = { type: 'Feature', id: `drawing-${Date.now()}`, properties: { name: tr('Polygon') }, geometry: { type: 'Polygon', coordinates: [coordinates] } };
      workbench.drawings.push(feature);
      workbench.selectedDrawing = feature.id;
      workbench.mode = null;
      renderWorkbenchDrawings();
    } else if (mode === 'draw-line' && vertices.length >= 2) {
      const feature = { type: 'Feature', id: `drawing-${Date.now()}`, properties: { name: tr('Polyline') }, geometry: { type: 'LineString', coordinates: vertices } };
      workbench.drawings.push(feature);
      workbench.selectedDrawing = feature.id;
      workbench.mode = null;
      renderWorkbenchDrawings();
    }
    updateWorkbenchModeUi();
    renderWorkbenchPreview();
  }

  function renderWorkbenchPreview(cursor = null) {
    const vertices = [...workbench.vertices];
    if (cursor) vertices.push([cursor.lng, cursor.lat]);
    let feature = null;
    if (workbench.circleCenter && cursor) feature = { type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: workbenchCircleSelectionRing(workbench.circleCenter, [cursor.lng, cursor.lat]) } };
    else if (vertices.length >= 2) feature = { type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: vertices } };
    const collection = feature ? { type: 'FeatureCollection', features: [feature] } : emptyFeatureCollection();
    setGeoJSONOverlay('workbench-preview', collection, [
      { id: 'workbench-preview-fill', type: 'fill', paint: { 'fill-color': '#7c3aed', 'fill-opacity': .12 } },
      { id: 'workbench-preview-line', type: 'line', paint: { 'line-color': '#7c3aed', 'line-width': 2, 'line-dasharray': [2, 2] } },
    ]);
  }

  function drawingVertices(feature) { return geometryCoordinates(feature.geometry); }

  function renderWorkbenchDrawings() {
    const collection = { type: 'FeatureCollection', features: workbench.drawings };
    setGeoJSONOverlay('user-drawings', collection, [
      { id: 'user-drawings-fill', type: 'fill', paint: { 'fill-color': '#2563eb', 'fill-opacity': .16 } },
      { id: 'user-drawings-line', type: 'line', paint: { 'line-color': '#1d4ed8', 'line-width': 3 } },
      { id: 'user-drawings-point', type: 'circle', paint: { 'circle-color': '#2563eb', 'circle-radius': 7, 'circle-stroke-color': '#fff', 'circle-stroke-width': 2 } },
    ]);
    const list = $('mapDrawingList');
    if (list) list.innerHTML = workbench.drawings.map((feature) => {
      const label = feature.properties?.name || feature.geometry.type;
      return `<li><button type="button" class="map-drawing-select${feature.id === workbench.selectedDrawing ? ' is-selected' : ''}" data-drawing-id="${escapeHtml(feature.id)}">${escapeHtml(label)}</button></li>`;
    }).join('');
    const hasDrawings = workbench.drawings.length > 0;
    ['mapEditDrawingButton', 'mapDeleteDrawingButton'].forEach((id) => { if ($(id)) $(id).disabled = !workbench.selectedDrawing; });
    ['mapExportDrawingsButton', 'mapSaveDrawingsButton', 'mapClearDrawingsButton'].forEach((id) => { if ($(id)) $(id).disabled = !hasDrawings; });
    const status = $('mapDrawingStatus');
    if (status && !workbench.mode) status.textContent = hasDrawings ? tr('{n} drawing(s)', { n: workbench.drawings.length }) : tr('No drawings yet.');
    renderDrawingVertices();
  }

  function renderDrawingVertices() {
    const feature = workbench.drawings.find((candidate) => candidate.id === workbench.selectedDrawing);
    const points = workbench.editingDrawing && feature ? drawingVertices(feature).map((coordinates, index) => ({
      type: 'Feature', properties: { index }, geometry: { type: 'Point', coordinates },
    })) : [];
    const collection = { type: 'FeatureCollection', features: points };
    setGeoJSONOverlay('drawing-vertices', collection, [
      { id: 'drawing-vertices-points', type: 'circle', paint: { 'circle-color': '#fff', 'circle-radius': 6, 'circle-stroke-color': '#1d4ed8', 'circle-stroke-width': 2 } },
    ]);
  }

  function editDrawingAt(lngLat) {
    const feature = workbench.drawings.find((candidate) => candidate.id === workbench.selectedDrawing);
    if (!feature) return;
    if (workbench.editVertex === null) {
      const coordinates = drawingVertices(feature);
      let nearest = -1; let distance = Infinity;
      coordinates.forEach((point, index) => {
        const pixel = state.map ? state.map.project(point) : state.fallbackMap.latLngToContainerPoint([point[1], point[0]]);
        const cursor = state.map ? state.map.project([lngLat.lng, lngLat.lat]) : state.fallbackMap.latLngToContainerPoint(lngLat);
        const candidateDistance = Math.hypot(pixel.x - cursor.x, pixel.y - cursor.y);
        if (candidateDistance < distance) { distance = candidateDistance; nearest = index; }
      });
      if (nearest >= 0 && distance < 24) {
        workbench.editVertex = nearest;
        $('mapDrawingStatus').textContent = tr('Click the map to move the highlighted vertex.');
      } else $('mapDrawingStatus').textContent = tr('Click near a vertex to select it.');
      return;
    }
    const replacement = [lngLat.lng, lngLat.lat];
    if (feature.geometry.type === 'Point') feature.geometry.coordinates = replacement;
    else {
      const coordinates = geometryCoordinates(feature.geometry);
      const old = coordinates[workbench.editVertex];
      replaceCoordinate(feature.geometry, old, replacement);
    }
    workbench.editVertex = null;
    renderWorkbenchDrawings();
    $('mapDrawingStatus').textContent = tr('Vertex moved. Click another vertex to continue.');
  }

  function replaceCoordinate(geometry, oldPoint, newPoint) {
    const walk = (value) => {
      if (Array.isArray(value) && value.length >= 2 && typeof value[0] === 'number') {
        if (value[0] === oldPoint[0] && value[1] === oldPoint[1]) { value[0] = newPoint[0]; value[1] = newPoint[1]; }
      } else if (Array.isArray(value)) value.forEach(walk);
    };
    walk(geometry.coordinates);
  }

  function drawingCollection() {
    return { type: 'FeatureCollection', features: workbench.drawings.map((feature) => ({
      type: 'Feature', id: feature.id, properties: { ...(feature.properties || {}) }, geometry: feature.geometry,
    })) };
  }

  async function saveWorkbenchDrawings() {
    const auth = window.authManager;
    if (!window.landRegistrySignedIn || !auth?.authenticatedFetch) {
      mapStatus(tr('Sign in to save your drawings.'), true);
      return;
    }
    try {
      const response = await auth.authenticatedFetch('/api/v1/save-drawn-polygons', {
        method: 'POST', body: JSON.stringify({ geojson: drawingCollection(), timestamp: new Date().toISOString() }),
      });
      const payload = await response.json();
      if (!response.ok || !payload.success) throw new Error(payload.detail || payload.error || tr('Drawings could not be saved.'));
      $('mapDrawingStatus').textContent = tr('Saved {n} drawing(s).', { n: workbench.drawings.length });
    } catch (error) { $('mapDrawingStatus').textContent = error.message || tr('Drawings could not be saved.'); }
  }

  async function loadWorkbenchDrawings() {
    const auth = window.authManager;
    if (!window.landRegistrySignedIn || !auth?.authenticatedFetch) {
      mapStatus(tr('Sign in to load saved drawings.'), true);
      return;
    }
    try {
      const response = await auth.authenticatedFetch('/api/v1/load-drawn-polygons');
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || tr('Drawings could not be loaded.'));
      if (!payload.success || !payload.geojson) { $('mapDrawingStatus').textContent = tr('No saved drawings found.'); return; }
      const collection = window.MapWorkbench.toFeatureCollection(payload.geojson);
      workbench.drawings = collection.features.map((feature, index) => ({
        ...feature, id: String(feature.id || `drawing-loaded-${index + 1}`), properties: { ...(feature.properties || {}) },
      }));
      workbench.selectedDrawing = workbench.drawings[0]?.id || null;
      renderWorkbenchDrawings();
      $('mapDrawingStatus').textContent = tr('Loaded {n} drawing(s).', { n: workbench.drawings.length });
    } catch (error) { $('mapDrawingStatus').textContent = error.message || tr('Drawings could not be loaded.'); }
  }

  async function importWorkbenchDrawings(file) {
    if (!file) return;
    if (file.size > 25 * 1024 * 1024) {
      $('mapDrawingStatus').textContent = tr('GeoJSON files must be smaller than 25 MB.');
      return;
    }
    try {
      const collection = await window.MapWorkbench.readGeoJSONFile(file);
      const imported = collection.features.map((feature, index) => ({
        ...feature,
        id: `drawing-import-${Date.now()}-${index}`,
        properties: { ...(feature.properties || {}) },
      }));
      workbench.drawings.push(...imported);
      workbench.selectedDrawing = imported[0]?.id || workbench.selectedDrawing;
      renderWorkbenchDrawings();
      $('mapDrawingStatus').textContent = tr('Imported {n} feature(s).', { n: imported.length });
    } catch (error) { $('mapDrawingStatus').textContent = error.message || tr('GeoJSON could not be imported.'); }
  }

  function renderUrbanContour() {
    if (!state.map) return;
    const sourceId = 'urban-contour-analysis';
    const enabled = $('urbanContourEnabled')?.checked !== false;
    const mode = $('urbanContourMode')?.value || 'broad';
    const features = enabled && tableView.layerId === 'cadastral-parcels'
      ? tableView.rawFeatures.map((feature) => {
        const classification = window.MapWorkbench?.classifyUrbanStatus(feature, mode);
        return classification && classification.status !== 'unknown' ? {
          ...feature, properties: { ...feature.properties, __map_urban_status: classification.status },
        } : null;
      }).filter(Boolean) : [];
    setGeoJSONOverlay(sourceId, { type: 'FeatureCollection', features }, [
      { id: 'urban-contour-fill', type: 'fill', filter: ['==', ['get', '__map_urban_status'], 'urban'], paint: { 'fill-color': '#dc2626', 'fill-opacity': .2 } },
      { id: 'urban-contour-line', type: 'line', filter: ['==', ['get', '__map_urban_status'], 'urban'], paint: { 'line-color': '#b91c1c', 'line-width': 2.5 } },
      { id: 'urban-contour-rural', type: 'line', filter: ['==', ['get', '__map_urban_status'], 'not_urban'], paint: { 'line-color': '#059669', 'line-width': 1.5, 'line-dasharray': [2, 2] } },
    ]);
    const status = $('urbanContourStatus');
    if (status) status.textContent = !enabled ? tr('Urban contour overlay disabled.')
      : tableView.layerId !== 'cadastral-parcels' ? tr('Choose cadastral parcels in the table to inspect urban classes.')
        : tr('{n} features classified on the current page.', { n: features.length });
  }

  function setupMapUtilities() {
    const output = $('mapCoordinates');
    $('mapCoordinatesToggle')?.addEventListener('click', (event) => {
      const show = output.hidden;
      output.hidden = !show;
      event.currentTarget.setAttribute('aria-pressed', String(show));
    });
    $('mapMiniMapToggle')?.addEventListener('click', (event) => {
      const container = $('mapMiniMap');
      container.hidden = !container.hidden;
      event.currentTarget.setAttribute('aria-pressed', String(!container.hidden));
      if (!container.hidden) { createWorkbenchMinimap(); syncWorkbenchMinimap(); }
      else if (workbench.minimap) { workbench.minimap.remove(); workbench.minimap = null; }
    });
    const bindMap = (map, isFallback = false) => {
      if (!map) return;
      map.on('mousemove', (event) => {
        const point = isFallback ? { lng: event.latlng.lng, lat: event.latlng.lat } : event.lngLat;
        if (!point) return;
        if (output && !output.hidden) output.textContent = `${point.lng.toFixed(5)}, ${point.lat.toFixed(5)}`;
      });
      map.on('move', syncWorkbenchMinimap);
      map.on('resize', syncWorkbenchMinimap);
    };
    if (state.map) bindMap(state.map);
    else if (state.fallbackMap) bindMap(state.fallbackMap, true);
  }

  function createWorkbenchMinimap() {
    const container = $('mapMiniMap');
    if (workbench.minimap || !container || container.hidden) return;
    try {
      if (state.map && window.maplibregl) {
        workbench.minimap = new maplibregl.Map({
          container, style: styleForBasemap(currentBasemap()),
          center: state.map.getCenter().toArray(), zoom: Math.max(3, state.map.getZoom() - 4),
          interactive: false, attributionControl: false, renderWorldCopies: false,
        });
        workbench.minimap.on('load', () => {
          workbench.minimap.addSource('overview-viewport', { type: 'geojson', data: emptyFeatureCollection() });
          workbench.minimap.addLayer({ id: 'overview-viewport-fill', type: 'fill', source: 'overview-viewport', paint: { 'fill-color': '#2563eb', 'fill-opacity': .14 } });
          workbench.minimap.addLayer({ id: 'overview-viewport-line', type: 'line', source: 'overview-viewport', paint: { 'line-color': '#1d4ed8', 'line-width': 2 } });
          syncWorkbenchMinimap();
        });
      } else if (state.fallbackMap && window.L) {
        workbench.minimap = window.L.map(container, { zoomControl: false, attributionControl: false, dragging: false, scrollWheelZoom: false, doubleClickZoom: false, boxZoom: false, keyboard: false, tap: false });
        const tiles = basemapEngine(currentBasemap());
        window.L.tileLayer(tiles.base, { maxZoom: 18, attribution: tiles.attribution }).addTo(workbench.minimap);
        workbench.minimap.setView(state.fallbackMap.getCenter(), Math.max(3, state.fallbackMap.getZoom() - 4));
        workbench.minimapViewport = window.L.rectangle(state.fallbackMap.getBounds(), { color: '#1d4ed8', weight: 2, fillOpacity: .12 }).addTo(workbench.minimap);
      }
    } catch (error) { console.warn('Overview map could not be initialized', error); }
  }

  function syncWorkbenchMinimap() {
    if (!workbench.minimap || $('mapMiniMap')?.hidden) return;
    const map = state.map || state.fallbackMap;
    if (!map) return;
    const bounds = map.getBounds();
    const west = bounds.getWest(); const east = bounds.getEast(); const south = bounds.getSouth(); const north = bounds.getNorth();
    if (state.map && workbench.minimap.getSource('overview-viewport')) {
      workbench.minimap.getSource('overview-viewport').setData({
        type: 'FeatureCollection', features: [{ type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [[[west, south], [east, south], [east, north], [west, north], [west, south]]] } }],
      });
      workbench.minimap.jumpTo({ center: map.getCenter(), zoom: Math.max(3, map.getZoom() - 4) });
    } else if (state.fallbackMap) {
      workbench.minimap.setView(state.fallbackMap.getCenter(), Math.max(3, state.fallbackMap.getZoom() - 4), { animate: false });
      workbench.minimapViewport?.setBounds(state.fallbackMap.getBounds());
    }
  }

  // ---- Attribute table (single-layer, viewport-scoped analogue of the
  // legacy Table View / table-manager.js) ----
  const tableView = { active: false, layerId: null, rawFeatures: [], page: 1, pageSize: 100, hasMore: false, controller: null, error: '', sortField: '', sortDirection: 'asc', attributeFilter: null };

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
    // Booleans match both their raw form and the Yes/No shown in the cell.
    const searchable = (value) => (typeof value === 'boolean'
      ? `${value} ${tr(value ? 'Yes' : 'No')}`
      : String(value ?? ''));
    const filtered = tableView.rawFeatures.filter((feature) => {
      if (query && !Object.values(feature.properties || {}).some((value) => searchable(value).toLowerCase().includes(query))) return false;
      const filter = tableView.attributeFilter;
      return !filter || window.MapWorkbench?.matchesValue(feature.properties?.[filter.field], filter.value, filter.operation);
    });
    return tableView.sortField && window.MapWorkbench
      ? window.MapWorkbench.sortRows(filtered, tableView.sortField, tableView.sortDirection)
      : filtered;
  }

  function renderTable() {
    const head = $('mapTableHead');
    const body = $('mapTableBody');
    if (!head || !body) return;
    const layer = state.catalog.find((candidate) => candidate.id === tableView.layerId);
    const features = filteredTableFeatures();
    // Catalog properties first, then any column the endpoint adds per feature
    // (e.g. has_visura on cadastral parcels) so a new column needs no catalog edit.
    const columns = [...(layer?.properties || [])];
    tableView.rawFeatures.forEach((feature) => Object.keys(feature.properties || {}).forEach((key) => {
      if (!columns.includes(key)) columns.push(key);
    }));
    // Booleans read as words; null (unknown, e.g. SISTER unreachable) stays blank.
    const cellText = (value) => (value === true ? tr('Yes') : value === false ? tr('No') : value);
    const attributeField = $('mapAttributeField');
    if (attributeField) {
      const previous = attributeField.value;
      attributeField.innerHTML = `<option value="">${escapeHtml(tr('Attribute'))}</option>${columns.map((column) => `<option value="${escapeHtml(column)}">${escapeHtml(column)}</option>`).join('')}`;
      if (columns.includes(previous)) attributeField.value = previous;
    }
    head.innerHTML = `<tr><th>${escapeHtml(tr('Select'))}</th>${columns.map((column) => {
      const marker = tableView.sortField === column ? (tableView.sortDirection === 'asc' ? ' ↑' : ' ↓') : '';
      return `<th><button type="button" class="map-table-sort" data-sort-field="${escapeHtml(column)}" aria-label="${escapeHtml(tr('Sort by {field}', { field: column }))}">${escapeHtml(column)}${marker}</button></th>`;
    }).join('')}</tr>`;
    const pageItems = features;
    body.innerHTML = pageItems.length
      ? pageItems.map((feature) => {
        const key = analysisFeatureKey(feature);
        const checked = key && workbench.selection.has(key);
        return `<tr data-feature-key="${escapeHtml(key || '')}" tabindex="0" class="${checked ? 'is-analysis-selected' : ''}"><td><input type="checkbox" aria-label="${escapeHtml(tr('Select feature'))}" data-select-feature="${escapeHtml(key || '')}" ${checked ? 'checked' : ''}></td>${columns.map((column) => `<td>${escapeHtml(cellText(feature.properties?.[column]))}</td>`).join('')}</tr>`;
      }).join('')
      : `<tr><td colspan="${columns.length + 1}">${escapeHtml(tableView.error || tr('No features in the current view.'))}</td></tr>`;
    const pageInfo = $('mapTablePageInfo');
    if (pageInfo) pageInfo.textContent = `Page ${tableView.page}${tableView.hasMore ? '+' : ''}`;
    $('mapTablePrevButton').disabled = tableView.page <= 1;
    $('mapTableNextButton').disabled = !tableView.hasMore;
    head.querySelectorAll('[data-sort-field]').forEach((button) => button.addEventListener('click', () => {
      const field = button.dataset.sortField;
      tableView.sortDirection = tableView.sortField === field && tableView.sortDirection === 'asc' ? 'desc' : 'asc';
      tableView.sortField = field;
      renderTable();
    }));
    body.querySelectorAll('tr[data-feature-key]').forEach((row) => {
      const activate = () => {
        const key = row.getAttribute('data-feature-key');
        const feature = pageItems.find((candidate) => analysisFeatureKey(candidate) === key);
        if (feature) focusTableFeature(feature);
      };
      row.addEventListener('click', activate);
      row.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); activate(); }
      });
    });
    body.querySelectorAll('[data-select-feature]').forEach((checkbox) => checkbox.addEventListener('click', (event) => event.stopPropagation()));
    body.querySelectorAll('[data-select-feature]').forEach((checkbox) => checkbox.addEventListener('change', () => {
      const key = checkbox.dataset.selectFeature;
      const feature = pageItems.find((candidate) => analysisFeatureKey(candidate) === key);
      if (!feature || !key) return;
      if (checkbox.checked) workbench.selection.set(key, feature);
      else workbench.selection.delete(key);
      renderAnalysisSelection();
    }));
  }

  function focusTableFeature(feature) {
    const layer = state.catalog.find((candidate) => candidate.id === tableView.layerId);
    const bounds = featureBounds(feature);
    if (bounds) state.map.fitBounds(bounds, { padding: 60, maxZoom: 19 });
    if (layer) showOverlayFeature(layer, feature, bounds ? bounds.getCenter() : state.map.getCenter());
  }

  function selectCurrentTablePage(mode = 'all') {
    let limitReached = false;
    for (const feature of filteredTableFeatures()) {
      const key = analysisFeatureKey(feature);
      if (!key) continue;
      if (mode === 'invert') {
        if (workbench.selection.has(key)) workbench.selection.delete(key);
        else if (!addAnalysisFeature(feature)) { limitReached = true; break; }
      } else if (!addAnalysisFeature(feature)) { limitReached = true; break; }
    }
    renderAnalysisSelection();
    if (limitReached) mapStatus(tr('Selection is limited to 5,000 features.'), true);
  }

  function selectTableMatches() {
    const field = $('mapAttributeField')?.value;
    const value = $('mapAttributeValue')?.value;
    const operation = $('mapAttributeOperation')?.value || 'contains';
    if (!field || (!['empty', 'not_empty'].includes(operation) && String(value ?? '') === '')) {
      $('mapAdjacencyStatus').textContent = tr('Choose an attribute and value first.');
      return;
    }
    const matched = tableView.rawFeatures.filter((feature) => window.MapWorkbench?.matchesValue(
      feature.properties?.[field], value, operation,
    ));
    let added = 0;
    for (const feature of matched) {
      const key = analysisFeatureKey(feature);
      if (key && addAnalysisFeature(feature)) added += 1;
      else if (workbench.selection.size >= 5000) break;
    }
    renderAnalysisSelection();
    $('mapAdjacencyStatus').textContent = tr('Selected {n} matching feature(s) from this page.', { n: added });
  }

  function applyAttributeFilter() {
    const field = $('mapAttributeField')?.value;
    const value = $('mapAttributeValue')?.value;
    const operation = $('mapAttributeOperation')?.value || 'contains';
    if (!field || (!['empty', 'not_empty'].includes(operation) && String(value ?? '') === '')) {
      tableView.attributeFilter = null;
      $('mapTableStatus').textContent = tr('Choose an attribute and value to filter rows.');
      renderTable();
      return;
    }
    tableView.attributeFilter = { field, value, operation };
    renderTable();
  }

  function setWorkbenchTab(name) {
    const tabs = {
      table: ['mapWorkbenchTableTab', 'mapTableViewPanel'],
      adjacency: ['mapWorkbenchAdjacencyTab', 'mapAdjacencyViewPanel'],
      drawings: ['mapWorkbenchDrawingsTab', 'mapDrawingsViewPanel'],
    };
    Object.entries(tabs).forEach(([key, [tabId, panelId]]) => {
      const active = key === name;
      $(tabId)?.setAttribute('aria-selected', String(active));
      const panel = $(panelId);
      if (panel) panel.hidden = !active;
    });
    if (name === 'drawings') renderWorkbenchDrawings();
  }

  function renderAdjacencyRows(selected, adjacent) {
    const selectedContainer = $('mapAdjacencySelectedRows');
    const adjacentContainer = $('mapAdjacencyRows');
    const labelFor = (feature) => referenceFrom(feature.properties || {})
      || feature.properties?.name || feature.geometry?.type || tr('Feature');
    if (selectedContainer) selectedContainer.innerHTML = selected.length
      ? `<ul class="map-analysis-result-list">${selected.map((feature) => `<li>${escapeHtml(labelFor(feature))}</li>`).join('')}</ul>`
      : `<p class="map-muted">${escapeHtml(tr('No selected features.'))}</p>`;
    if (adjacentContainer) adjacentContainer.innerHTML = adjacent.length
      ? `<ul class="map-analysis-result-list">${adjacent.map((feature) => `<li><button type="button" class="map-adjacency-feature" data-adjacent-ref="${escapeHtml(referenceFrom(feature.properties || ''))}" data-adjacent-id="${escapeHtml(feature.id ?? feature.properties?.id ?? '')}">${escapeHtml(labelFor(feature))}</button></li>`).join('')}</ul>`
      : `<p class="map-muted">${escapeHtml(tr('No adjacent features found.'))}</p>`;
    adjacentContainer?.querySelectorAll('[data-adjacent-ref]').forEach((button) => button.addEventListener('click', () => {
      const reference = button.dataset.adjacentRef;
      const feature = adjacent.find((candidate) => referenceFrom(candidate.properties || {}) === reference);
      if (feature) focusTableFeature(feature);
      else if (reference) loadParcelByReference(reference, true, Number(button.dataset.adjacentId) || null);
    }));
  }

  async function analyzeSelectedAdjacency() {
    const status = $('mapAdjacencyStatus');
    const selected = [...workbench.selection.values()];
    if (!selected.length) { status.textContent = tr('Select parcels in the table to analyze them.'); return; }
    if (tableView.layerId !== 'cadastral-parcels') { status.textContent = tr('Adjacency analysis is available for cadastral parcels.'); return; }
    const targets = selected.filter((feature) => referenceFrom(feature.properties || {}));
    if (!targets.length) { status.textContent = tr('Selected features have no cadastral reference.'); return; }
    const requestId = ++workbench.adjacencyRequest;
    const method = $('mapAdjacencyMethod')?.value || 'intersects';
    const adjacent = new Map();
    renderAdjacencyRows(targets, []);
    status.textContent = tr('Analyzing {n} selected parcel(s)…', { n: targets.length });
    const task = beginMapProgress('workbench-adjacency', 'Analyzing selected parcels…');
    let complete = 0;
    try {
      for (let offset = 0; offset < targets.length; offset += 4) {
        const batch = targets.slice(offset, offset + 4);
        const results = await Promise.all(batch.map(async (feature) => {
          const reference = referenceFrom(feature.properties || {});
          const id = feature.id ?? feature.properties?.id ?? feature.properties?.feature_id;
          const params = new URLSearchParams({ method, limit: '50' });
          if (id !== undefined && Number.isFinite(Number(id))) params.set('id', String(id));
          const response = await fetch(`/api/v1/enrichment/parcel/adjacent/${encodeURIComponent(reference)}?${params}`);
          if (!response.ok) throw new Error(tr('Adjacency lookup failed for {reference}.', { reference }));
          const payload = await response.json();
          return payload.features || [];
        }));
        if (requestId !== workbench.adjacencyRequest) return;
        results.flat().forEach((feature) => {
          const key = analysisFeatureKey(feature);
          if (key && !workbench.selection.has(key)) adjacent.set(key, feature);
        });
        complete += batch.length;
        task.update('Analyzing selected parcels…', complete / targets.length * 100, tr('{n} of {total} parcels', { n: complete, total: targets.length }));
        renderAdjacencyRows(targets, [...adjacent.values()]);
      }
      status.textContent = tr('{n} adjacent parcel(s) found using {method}.', { n: adjacent.size, method });
      if (state.map && adjacent.size) state.map.getSource('adjacent-parcels')?.setData({ type: 'FeatureCollection', features: [...adjacent.values()] });
    } catch (error) {
      if (requestId === workbench.adjacencyRequest) status.textContent = error.message || tr('Adjacency analysis failed.');
    } finally { task.finish(); }
  }

  async function loadTableData() {
    if (tableView.active && !tableView.layerId) {
      // No catalog means no layer to query; say so rather than show an empty box.
      tableView.rawFeatures = [];
      tableView.hasMore = false;
      tableView.error = state.sourceHealthDown || state.sourceTileDown
        ? tr('Layer data is temporarily unavailable.')
        : tr('No layers available to list.');
      renderTable();
      const status = $('mapTableStatus');
      if (status) status.textContent = tableView.error;
      return;
    }
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
    const task = beginMapProgress('table', 'Loading attribute table…');
    try {
      const response = await fetch(`/api/v1/map/layers/${encodeURIComponent(tableView.layerId)}/features?${params}`, { signal: controller.signal });
      if (!response.ok) throw new Error('Attribute table unavailable');
      const data = await response.json();
      if (tableView.controller !== controller) return;
      tableView.rawFeatures = data.features || [];
      tableView.hasMore = Boolean(data.truncated);
      tableView.error = '';
      renderTable();
      renderUrbanContour();
      if (status) status.textContent = data.zoom_required
        ? `Zoom in to at least level ${data.zoom_required}`
        : `${tableView.rawFeatures.length} feature(s) on page${data.truncated ? ' · more available with Next' : ''}`;
    } catch (error) {
      if (error.name === 'AbortError' || tableView.controller !== controller) return;
      tableView.rawFeatures = [];
      tableView.hasMore = false;
      tableView.error = tr('Layer data could not be loaded. Try Refresh in a moment.');
      renderTable();
      if (status) status.textContent = error.message || 'Attribute table unavailable';
    } finally { task.finish(); }
  }

  function setTableViewOpen(open) {
    tableView.active = open;
    const card = $('mapTableCard');
    if (card) card.hidden = !open;
    $('tableToggle')?.setAttribute('aria-expanded', String(open));
    if (open) {
      populateTableLayerSelect();
      void loadTableData();
    } else {
      tableView.controller?.abort();
      mapTasks.get('table')?.finish();
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
    $('parcelReportLink')?.addEventListener('click', downloadParcelReport);
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
    $('toggleSisterBuildings')?.addEventListener('click', toggleSisterBuildingsOverlay);
    $('toggleSisterParcels')?.addEventListener('change', toggleSisterParcelsOverlay);
    $('toggleAuctionHighlight')?.addEventListener('change', toggleAuctionParcelsOverlay);
    $('sisterBatchClickButton')?.addEventListener('click', () => setSisterBatchMode('click'));
    $('sisterBatchPolygonButton')?.addEventListener('click', () => setSisterBatchMode('polygon'));
    $('sisterBatchCircleButton')?.addEventListener('click', () => setSisterBatchMode('circle'));
    $('sisterBatchClearButton')?.addEventListener('click', clearSisterBatchSelection);
    $('sisterBatchExportButton')?.addEventListener('click', exportSisterBatch);
    $('sisterBatchList')?.addEventListener('click', (event) => {
      const button = event.target.closest('[data-sister-batch-remove]');
      if (!button) return;
      state.sisterBatch.features.delete(button.getAttribute('data-sister-batch-remove'));
      state.sisterBatch.notice = '';
      renderSisterBatchSelection();
    });
    renderSisterBatchControls();
    $('refreshVisuraFlagsButton')?.addEventListener('click', () => refreshParcelFlag('has_visura'));
    $('refreshAuctionFlagsButton')?.addEventListener('click', () => refreshParcelFlag('is_auction_sale'));
    $('toggleAuctionLayer').addEventListener('click', toggleAuctionOverlay);
    $('refreshAuctionButton')?.addEventListener('click', () => loadAuctionOverlay(true));
    $('toggleSalesLayer')?.addEventListener('click', toggleSalesOverlay);
    $('refreshSalesButton')?.addEventListener('click', () => loadSalesOverlay(true));
    $('salesSort')?.addEventListener('change', () => { if (salesOverlay.active) loadSalesOverlay(); });
    $('salesPeriod')?.addEventListener('change', () => { if (salesOverlay.active) loadSalesOverlay(); });
    $('salesCategory')?.addEventListener('change', () => { if (salesOverlay.active) loadSalesOverlay(); });
    $('salesGeoLayerType')?.addEventListener('change', renderSalesGeoLayers);
    $('salesGeoMetric')?.addEventListener('change', renderSalesGeoLayers);
    syncSalesGeoControls();
    $('googleMapsButton')?.addEventListener('click', () => openMapAt('google'));
    $('streetViewButton')?.addEventListener('click', () => openMapAt('street'));
    $('auctionTypeFilter').addEventListener('change', updateAuctionFilter);
    $('auctionActiveOnly').addEventListener('change', updateAuctionFilter);
    $('auctionMaxPrice').addEventListener('change', updateAuctionFilter);
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
    $('mapTablePageSize').addEventListener('change', (event) => { tableView.pageSize = Math.max(1, Math.min(500, Number(event.target.value) || 100)); tableView.page = 1; void loadTableData(); });
    $('mapTablePrevButton').addEventListener('click', () => { if (tableView.page > 1) { tableView.page -= 1; void loadTableData(); } });
    $('mapTableNextButton').addEventListener('click', () => { if (tableView.hasMore) { tableView.page += 1; void loadTableData(); } });
    $('mapSelectAllButton').addEventListener('click', () => selectCurrentTablePage('all'));
    $('mapInvertSelectionButton').addEventListener('click', () => selectCurrentTablePage('invert'));
    $('mapClearAnalysisSelectionButton').addEventListener('click', () => { workbench.selection.clear(); renderAnalysisSelection(); });
    $('mapSelectByAttributeButton').addEventListener('click', selectTableMatches);
    $('mapApplyAttributeFilter').addEventListener('click', applyAttributeFilter);
    $('mapClearAttributeFilter').addEventListener('click', () => { tableView.attributeFilter = null; renderTable(); });
    $('mapSelectOnMapButton').addEventListener('click', () => toggleWorkbenchMode('select-click'));
    $('mapSelectPolygonButton').addEventListener('click', () => toggleWorkbenchMode('select-polygon'));
    $('mapSelectCircleButton').addEventListener('click', () => toggleWorkbenchMode('select-circle'));
    $('mapRunAdjacencyButton').addEventListener('click', () => { setWorkbenchTab('adjacency'); void analyzeSelectedAdjacency(); });
    $('mapWorkbenchTableTab').addEventListener('click', () => setWorkbenchTab('table'));
    $('mapWorkbenchAdjacencyTab').addEventListener('click', () => setWorkbenchTab('adjacency'));
    $('mapWorkbenchDrawingsTab').addEventListener('click', () => setWorkbenchTab('drawings'));
    document.querySelectorAll('[data-map-draw]').forEach((button) => button.addEventListener('click', () => {
      toggleWorkbenchMode(`draw-${button.dataset.mapDraw}`);
    }));
    $('mapEditDrawingButton').addEventListener('click', () => {
      if (!workbench.selectedDrawing) return;
      workbench.editingDrawing = !workbench.editingDrawing;
      workbench.mode = null;
      workbench.editVertex = null;
      $('mapEditDrawingButton').setAttribute('aria-pressed', String(workbench.editingDrawing));
      $('mapDrawingStatus').textContent = workbench.editingDrawing
        ? tr('Click near a vertex, then click where it should move.')
        : tr('Drawing edit mode ended.');
      updateWorkbenchModeUi();
      renderDrawingVertices();
    });
    $('mapDeleteDrawingButton').addEventListener('click', () => {
      workbench.drawings = workbench.drawings.filter((feature) => feature.id !== workbench.selectedDrawing);
      workbench.selectedDrawing = workbench.drawings[0]?.id || null;
      workbench.editingDrawing = false;
      renderWorkbenchDrawings();
    });
    $('mapExportDrawingsButton').addEventListener('click', () => {
      try { window.MapWorkbench.downloadGeoJSON(drawingCollection(), 'map-drawings.geojson'); }
      catch (error) { $('mapDrawingStatus').textContent = error.message; }
    });
    $('mapImportDrawingsButton').addEventListener('click', () => $('mapDrawingsFile').click());
    $('mapDrawingsFile').addEventListener('change', (event) => {
      void importWorkbenchDrawings(event.target.files?.[0]);
      event.target.value = '';
    });
    $('mapSaveDrawingsButton').addEventListener('click', () => void saveWorkbenchDrawings());
    $('mapLoadDrawingsButton').addEventListener('click', () => void loadWorkbenchDrawings());
    $('mapClearDrawingsButton').addEventListener('click', () => {
      workbench.drawings = [];
      workbench.selectedDrawing = null;
      workbench.editingDrawing = false;
      renderWorkbenchDrawings();
    });
    $('mapDrawingList').addEventListener('click', (event) => {
      const button = event.target.closest('[data-drawing-id]');
      if (!button) return;
      workbench.selectedDrawing = button.dataset.drawingId;
      workbench.editingDrawing = false;
      $('mapEditDrawingButton').setAttribute('aria-pressed', 'false');
      renderWorkbenchDrawings();
    });
    $('urbanContourEnabled').addEventListener('change', renderUrbanContour);
    $('urbanContourMode').addEventListener('change', renderUrbanContour);
    renderAnalysisSelection();
    renderWorkbenchDrawings();
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
    window.ParcelPanel?.mount({ content: $('directParcelContent'), nav: $('parcelSectionNav'), strip: $('parcelStatStrip'), tr });
    $('parcelCloseButton').addEventListener('click', () => { $('directParcelPanel').hidden = true; updateParcelZoomAffordance(); mapStatus(''); });
    $('layersCloseButton').addEventListener('click', () => { $('mapLayersCard').hidden = true; $('layersToggle').setAttribute('aria-expanded', 'false'); });
    const basemapToggle = $('basemapToggle');
    const basemapPopover = $('mapBasemapPopover');
    const setBasemapPickerOpen = (open) => {
      basemapPopover.hidden = !open;
      basemapToggle.setAttribute('aria-expanded', String(open));
    };
    basemapToggle.addEventListener('click', () => {
      const open = basemapPopover.hidden;
      if (open) {
        $('mapLayersCard').hidden = true;
        $('layersToggle').setAttribute('aria-expanded', 'false');
      }
      setBasemapPickerOpen(open);
    });
    document.addEventListener('click', (event) => {
      if (!basemapPopover.hidden && !event.target.closest('.map-basemap-control')) setBasemapPickerOpen(false);
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && !basemapPopover.hidden) {
        setBasemapPickerOpen(false);
        basemapToggle.focus();
      }
    });
    $('layersToggle').addEventListener('click', () => {
      const card = $('mapLayersCard');
      card.hidden = !card.hidden;
      $('layersToggle').setAttribute('aria-expanded', String(!card.hidden));
      if (!card.hidden) setBasemapPickerOpen(false);
    });
    $('basemapEngineSelect').addEventListener('change', (event) => {
      switchBasemap(event.target.value);
      setBasemapPickerOpen(false);
      basemapToggle.focus();
    });
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
        if (['light', 'dark'].includes(basemap) && basemap !== (state.dark ? 'dark' : 'light')) {
          const selector = $('basemapEngineSelect');
          if (selector) selector.value = state.dark ? 'dark' : 'light';
          if (state.map || state.fallbackMap) switchBasemap(state.dark ? 'dark' : 'light');
        }
      }, 0);
    });
  }

  function switchBasemap(kind) {
    writeBasemapUrl();
    if (!state.map && state.fallbackMap) {
      if (state.fallbackBasemap) state.fallbackMap.removeLayer(state.fallbackBasemap);
      const tiles = basemapEngine(kind);
      state.fallbackBasemap = L.tileLayer(tiles.base, {
        maxZoom: 22,
        maxNativeZoom: tiles.maxzoom,
        attribution: tiles.attribution,
      }).addTo(state.fallbackMap);
      return;
    }
    if (!state.map) return;
    const previousStyle = state.map.getStyle();
    const basemapStyle = styleForBasemap(kind);
    // Keep live overlay data, filters and visibility in the next style.
    // Style diffs can remove overlays without firing another style.load,
    // so rebuilding them in that event left auction markers missing.
    const overlaySources = Object.fromEntries(
      Object.entries(previousStyle?.sources || {}).filter(([id]) => !id.startsWith('basemap')),
    );
    const overlayLayers = (previousStyle?.layers || []).filter((layer) => !layer.id.startsWith('basemap'));
    state.layerFailures.clear();
    state.map.setStyle({
      ...previousStyle,
      ...basemapStyle,
      sources: { ...basemapStyle.sources, ...overlaySources },
      layers: [...basemapStyle.layers, ...overlayLayers],
    });
  }

  async function loadCatalog() {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8000);
    const task = beginMapProgress('catalog', 'Loading layer catalog…');
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
      task.finish();
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
    const selector = $('basemapEngineSelect');
    if (selector) selector.value = kind;
    writeBasemapUrl();
  }

  function loadScriptOnce(src, integrity) {
    return new Promise((resolve, reject) => {
      const existing = [...document.scripts].find((script) => script.src === src);
      if (existing?.dataset.loaded === 'true') return resolve();
      const script = existing || document.createElement('script');
      script.src = src;
      script.async = false;
      if (integrity) { script.integrity = integrity; script.crossOrigin = 'anonymous'; }
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
      link.integrity = 'sha384-sHL9NAb7lN7rfvG5lfHpm643Xkcjzp4jFvuavGOndn6pjVqS6ny56CAt3nsEVT4H';
      link.crossOrigin = 'anonymous';
      link.dataset.mapLeaflet = 'true';
      document.head.appendChild(link);
    }
    if (!window.L) await loadScriptOnce('https://unpkg.com/leaflet@1.9.4/dist/leaflet.js', 'sha384-cxOPjt7s7Iz04uaHJceBmS+qpjv2JkIHNVcuOrM+YHwZOmJGBXI00mdUXEq65HTH');
    if (!window.L?.vectorGrid) await loadScriptOnce('https://unpkg.com/leaflet.vectorgrid@1.3.0/dist/Leaflet.VectorGrid.bundled.js', 'sha384-FON5fTjCTtPuBgUS1r2H/PGXstH0Rk23YKjZmB6qITkbFqBcqtey/rPo9eXwOWpx');
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
    state.fallbackMap.on('click', (event) => {
      if (state.sisterBatch.mode) { handleSisterBatchMapClick(event.latlng); return; }
      if (interactionClick(event.latlng)) return;
      handleSisterBatchMapClick(event.latlng);
    });
    state.fallbackMap.on('mousemove', (event) => {
      if (state.sisterBatch.mode === 'polygon' || state.sisterBatch.mode === 'circle') previewSisterBatchDrawing(event.latlng);
      if (workbench.mode === 'select-circle' || workbench.circleCenter || workbench.vertices.length) renderWorkbenchPreview(event.latlng);
    });
    state.fallbackMap.on('dblclick', () => {
      if (state.sisterBatch.mode === 'polygon') finishSisterBatchPolygon();
      else finishWorkbenchPolygon();
    });
  }

  async function finishMapInit(restored) {
    if (state.map) {
      state.map.resize();
      if (!restored.hasView) writeUrl();
    } else if (!restored.hasView) {
      writeUrl();
    }
    setupMapUtilities();
    renderAnalysisSelection();
    renderWorkbenchDrawings();
    renderParcelPvpNearbyMarkers();
    if (restored.invalidView) mapStatus('Location outside Italy — showing Rome.', true);
    else if (restored.invalidParcel) mapStatus('Invalid parcel reference — selection was not loaded.', true);
    else mapStatus(state.fallbackMap ? 'Map ready (raster mode)' : 'Map ready');
    await loadCatalog();
    if ($('toggleSisterParcels')?.checked) toggleSisterParcelsOverlay();
    const requestedView = new URLSearchParams(window.location.search);
    if (requestedView.get('panel') === 'table') setTableViewOpen(true);
    const routeNotices = {
      table: 'Map table moved into the map.',
      adjacency: 'Select a parcel to find adjacent parcels from its details panel.',
    };
    const notice = routeNotices[requestedView.get('notice')];
    if (notice) mapStatus(tr(notice));
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
    state.preferences = await loadPreferences();
    const restored = urlState();
    const basemap = restored.basemap || (BASEMAPS.includes(state.preferences?.default_basemap) ? state.preferences.default_basemap : (document.documentElement.getAttribute('data-bs-theme') === 'dark' ? 'dark' : 'light'));
    if (Array.isArray(state.preferences?.default_layers)) state.defaultLayers = new Set(state.preferences.default_layers);
    if (restored.layers !== null) state.defaultLayers = new Set(restored.layers);
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
    state.map.on('load', () => {
      state.tilesLoading = true;
      if (!mapTasks.has('map-tiles')) beginMapProgress('map-tiles', 'Loading map tiles…');
      mapStatus('Loading map tiles…');
      void finishMapInit(restored);
    });
    state.map.on('dataloading', (event) => {
      if (event.dataType !== 'source' || !event.tile) return;
      state.tilesLoading = true;
      if (!mapTasks.has('map-tiles')) beginMapProgress('map-tiles', 'Loading map tiles…');
    });
    state.map.on('sourcedata', (event) => {
      // Only a tile that actually loaded counts as recovery. setTiles() itself
      // fires a 'content' sourcedata event, so resetting on any such event
      // zeroed the retry counter and let a 503 retry forever.
      if (event.sourceId && event.sourceDataType === 'content' && event.tile?.state === 'loaded') {
        state.tileErrors.delete(event.sourceId);
        tileRetry.attempts[event.sourceId] = 0;
        refreshSourceTileDown();
      }
    });
    // React only when a tile-loading phase has just settled. Doing this work
    // on every 'idle' formed a render loop (the affordance update touches
    // layer layout, which re-renders and idles again) and overwrote every
    // other status message with "Map ready" within a frame.
    state.map.on('idle', () => {
      if (!state.tilesLoading) return;
      state.tilesLoading = false;
      mapTasks.get('map-tiles')?.finish();
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
      if (state.sisterBatch.mode) {
        const parcelFeatures = state.map.getLayer('fill-cadastral-parcels')
          ? state.map.queryRenderedFeatures(event.point, { layers: ['fill-cadastral-parcels'] }) : [];
        handleSisterBatchMapClick(event.lngLat, parcelFeatures[0] || null);
        return;
      }
      const workbenchParcels = state.map.getLayer('fill-cadastral-parcels')
        ? state.map.queryRenderedFeatures(event.point, { layers: ['fill-cadastral-parcels'] }) : [];
      if (interactionClick(event.lngLat, workbenchParcels[0] || null)) return;
      if (state.map.getLayer('parcel-pvp-nearby-markers')
        && state.map.queryRenderedFeatures(event.point, { layers: ['parcel-pvp-nearby-markers'] }).length) return;
      const auctionParcelFeatures = state.map.getLayer('fill-auction-listed-parcels')
        ? state.map.queryRenderedFeatures(event.point, { layers: ['fill-auction-listed-parcels'] }) : [];
      if (auctionParcelFeatures[0]) {
        identifyAtPoint(event.lngLat, auctionParcelFeatures[0]);
        return;
      }
      const sisterParcelFeatures = state.map.getLayer('fill-sister-listed-parcels')
        ? state.map.queryRenderedFeatures(event.point, { layers: ['fill-sister-listed-parcels'] }) : [];
      if (sisterParcelFeatures[0]) {
        identifyAtPoint(event.lngLat, sisterParcelFeatures[0]);
        return;
      }
      const features = state.map.queryRenderedFeatures(event.point, { layers: state.map.getLayer('fill-cadastral-parcels') ? ['fill-cadastral-parcels'] : [] });
      if (features[0]) {
        identifyAtPoint(event.lngLat, features[0]);
        return;
      }
      const overlayLayers = state.catalog
        .filter((layer) => layer.id !== 'cadastral-parcels' && (state.map.getLayer(`fill-${layer.id}`) || state.map.getLayer(`point-${layer.id}`)))
        .flatMap((layer) => ['fill', 'line', 'point', 'label'].map(prefix => `${prefix}-${layer.id}`).filter(id => state.map.getLayer(id)));
      const overlayFeature = overlayLayers.length ? state.map.queryRenderedFeatures(event.point, { layers: overlayLayers })[0] : null;
      if (overlayFeature) {
        const layer = state.catalog.find((candidate) => overlayFeature.layer?.id?.endsWith(candidate.id));
        if (layer) showOverlayFeature(layer, overlayFeature, event.lngLat);
        return;
      }
      // Clicks on these dedicated interactive layers are handled by their own
      // layer listeners below. They are not parcel-selection clicks, even
      // though MapLibre also emits the map-level click event for them.
      const handledLayerIds = state.map.getStyle().layers
        .map((layer) => layer.id)
        .filter((id) => [
          'sales-clusters', 'sales-unclustered', 'auction-clusters',
          'point-auction-properties', 'fill-sister-building-parcel', 'fill-sister-listed-parcels',
          'fill-auction-listed-parcels',
          'point-enrichment-fires', 'fill-enrichment-bulletin', 'parcel-pvp-nearby-markers',
        ].includes(id) || (id.startsWith('sales-geo-') && id.endsWith('-points')));
      if (handledLayerIds.length && state.map.queryRenderedFeatures(event.point, { layers: handledLayerIds }).length) return;
      if (state.map.getZoom() < parcelMinZoom()) {
        mapStatus('Zoom in to select a parcel');
        return;
      }
      identifyAtPoint(event.lngLat, null);
    });
    state.map.on('mousemove', (event) => {
      if (state.sisterBatch.mode === 'polygon' || state.sisterBatch.mode === 'circle') previewSisterBatchDrawing(event.lngLat);
      if (workbench.mode === 'select-circle' || workbench.circleCenter || workbench.vertices.length) renderWorkbenchPreview(event.lngLat);
    });
    state.map.on('dblclick', () => {
      if (state.sisterBatch.mode === 'polygon') finishSisterBatchPolygon();
      else finishWorkbenchPolygon();
    });
  }

  window.landRegistryPurchaseMap = {
    selection: () => ({ feature: state.selectedFeature, reference: state.selectedReference }),
    maps: () => ({ map: state.map, fallbackMap: state.fallbackMap }),
  };

  window.addEventListener('parcel-pvp-nearby-markers', (event) => setParcelPvpNearbyMarkers(event.detail));
  document.addEventListener('DOMContentLoaded', init);
})();
