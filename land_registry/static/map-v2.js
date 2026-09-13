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
  const state = {
    map: null,
    catalog: [],
    selectedReference: null,
    selectedFeature: null,
    savedReference: null,
    searchTimer: null,
    searchRequest: 0,
    searchController: null,
    layerFailures: new Set(),
    activeLayers: null,
    adminSubstituteAutoLayers: new Set(),
    shortlistItems: [],
    shortlistStatuses: [],
    shortlistSummary: null,
    dark: false,
  };

  const $ = (id) => document.getElementById(id);
  const mapStatus = (message, error = false) => {
    const element = $('mapStatus');
    if (!element) return;
    element.textContent = message || '';
    element.classList.toggle('is-error', error);
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
    return {
      center: Number.isFinite(lat) && Number.isFinite(lng) ? [lng, lat] : DEFAULT_CENTER,
      zoom: Number.isFinite(zoom) ? Math.min(22, Math.max(5, zoom)) : DEFAULT_ZOOM,
      parcel: params.get('parcel') || null,
    };
  }

  function writeUrl() {
    if (!state.map) return;
    const center = state.map.getCenter();
    const params = new URLSearchParams(window.location.search);
    params.set('lat', center.lat.toFixed(6));
    params.set('lng', center.lng.toFixed(6));
    params.set('zoom', state.map.getZoom().toFixed(2));
    if (state.selectedReference) params.set('parcel', state.selectedReference);
    else params.delete('parcel');
    window.history.replaceState(null, '', `${window.location.pathname}?${params.toString()}`);
  }

  function currentBasemap() {
    return state.dark ? 'dark' : 'light';
  }

  function styleForBasemap(kind) {
    const raster = kind === 'satellite'
      ? 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'
      : kind === 'dark'
        ? 'https://cartodb-basemaps-a.global.ssl.fastly.net/dark_all/{z}/{x}/{y}.png'
        : 'https://cartodb-basemaps-a.global.ssl.fastly.net/light_all/{z}/{x}/{y}.png';
    return {
      version: 8,
      sources: {
        basemap: { type: 'raster', tiles: [raster], tileSize: 256, attribution: kind === 'satellite' ? '© Esri' : '© OpenStreetMap © CARTO' },
      },
      layers: [{ id: 'basemap', type: 'raster', source: 'basemap' }],
    };
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

  function addCatalogLayer(layer) {
    if (!state.map || !layer.tile_url || layer.id === 'raster-coverage') return;
    const sourceId = layerSourceId(layer);
    if (state.map.getSource(sourceId)) return;
    state.map.addSource(sourceId, {
      type: 'vector',
      tiles: [layer.tile_url],
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
    state.map.on('error', (event) => {
      const url = event?.error?.url || '';
      if (url.includes(`/map-layers/${layer.id}/`)) handleLayerFailure(layer);
    });
  }

  function addParcelLabels() {
    if (state.map.getLayer('label-cadastral-parcels')) return;
    state.map.addLayer({
      id: 'label-cadastral-parcels', type: 'symbol', source: layerSourceId({ id: 'cadastral-parcels' }), 'source-layer': 'cadastral-parcels',
      minzoom: 17, layout: { 'text-field': ['coalesce', ['get', 'parcel'], ['get', 'canonical_reference'], ''], 'text-size': 11, 'text-allow-overlap': false, 'text-padding': 2 },
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
    state.map.addSource('parcel-raster-fallback', { type: 'raster', tiles: ['/api/v1/tiles/cadastral-boundaries/{z}/{x}/{y}.png?layer=ple'], tileSize: 512, minzoom: PARCEL_MIN_ZOOM, maxzoom: 22 });
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
      const defaults = layer.id === 'cadastral-parcels';
      input.checked = initializingLayers ? defaults : state.activeLayers.has(layer.id);
      input.disabled = Number(layer.min_zoom || 0) > 22;
      input.addEventListener('change', () => {
        state.adminSubstituteAutoLayers.delete(layer.id);
        if (state.activeLayers === null) state.activeLayers = new Set();
        if (input.checked) state.activeLayers.add(layer.id);
        else state.activeLayers.delete(layer.id);
        if (input.checked && !state.map.getSource(layerSourceId(layer))) addCatalogLayer(layer);
        setLayerVisibility(layer.id, input.checked);
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
    updateParcelZoomAffordance();
    const partial = state.catalog.filter((layer) => layer.coverage === 'partial');
    if (partial.length) $('mapCoverageStatus').textContent = 'Some layers have partial regional coverage.';
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
    if (!state.map.getSource(layerSourceId(layer))) addCatalogLayer(layer);
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
    if (!state.map) return;
    const belowParcelZoom = state.map.getZoom() < parcelMinZoom();
    if (belowParcelZoom) enableAdministrativeSubstitute();
    else releaseAdministrativeSubstitute();
    const panelOpen = !$('directParcelPanel')?.hidden;
    $('mapHelpNote').hidden = !belowParcelZoom || panelOpen;
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
        else if (health.available) meta.textContent = `Available${metadata}`;
        else if (health.relation_exists === false) meta.textContent = `Not available${metadata}`;
        else meta.textContent = `Needs configuration${metadata}`;
      });
    } catch (_) {
      // Health is advisory; the catalog and map remain usable without it.
    } finally { clearTimeout(timeout); }
  }

  function setLayerVisibility(layerId, visible) {
    ['fill', 'line', 'point', 'label'].forEach((prefix) => {
      const id = `${prefix}-${layerId}`;
      if (state.map.getLayer(id)) state.map.setLayoutProperty(id, 'visibility', visible ? 'visible' : 'none');
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

  function renderParcelPanel(feature, enrichment = null) {
    const props = feature?.properties || {};
    const reference = state.selectedReference || referenceFrom(props) || 'Selected parcel';
    const municipality = props.municipality_name || props.municipality || props.ADMINISTRATIVEUNIT || props.municipality_id || '—';
    const sheet = props.sheet || props.sheet_number || props.foglio || '—';
    const parcel = props.parcel || props.particella || props.LABEL || '—';
    const area = props.area_sqm ?? props.area ?? props.area_display;
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
    const enrichmentHtml = enrichment ? `<div class="parcel-enrichment">${blockHtml || '<p>No optional enrichment is available for this parcel.</p>'}</div>` : '<div class="parcel-enrichment"><p>Loading optional enrichment…</p></div>';
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
      ${enrichment?.municipality ? `<p class="map-layer-meta">Municipality profile available.</p>` : ''}`;
    const parcelQuery = `?parcel=${encodeURIComponent(reference)}&report=1`;
    $('legacyAnalysisLink').href = `/map-legacy?parcel=${encodeURIComponent(reference)}`;
    $('parcelReportLink').href = `/map-legacy${parcelQuery}`;
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
    const rows = Object.entries(properties)
      .filter(([, value]) => value !== null && value !== undefined && typeof value !== 'object')
      .slice(0, 6)
      .map(([key, value]) => `<tr><th>${escapeHtml(key.replaceAll('_', ' '))}</th><td>${escapeHtml(value)}</td></tr>`)
      .join('');
    new maplibregl.Popup({ maxWidth: '320px', closeButton: true })
      .setLngLat(lngLat)
      .setHTML(`<strong>${escapeHtml(layer.title)}</strong><table class="parcel-detail-table"><tbody>${rows || '<tr><td>No feature details</td></tr>'}</tbody></table><small>${escapeHtml(layer.source || 'Source not available')}</small>`)
      .addTo(state.map);
  }

  async function loadParcelByReference(reference, flyTo = true) {
    if (!reference) return null;
    mapStatus('Loading parcel…');
    try {
      const response = await fetch(`/api/v1/enrichment/parcel/by-reference/${encodeURIComponent(reference)}`);
      if (!response.ok) throw new Error(response.status === 404 ? 'Parcel not found' : 'Parcel service unavailable');
      const feature = await response.json();
      state.selectedReference = reference;
      state.selectedFeature = feature;
      renderSelectedGeometry(feature);
      if (flyTo) {
        const bounds = Array.isArray(feature.bbox)
          ? new maplibregl.LngLatBounds([feature.bbox[0], feature.bbox[1]], [feature.bbox[2], feature.bbox[3]])
          : featureBounds(feature);
        if (bounds) state.map.fitBounds(bounds, { padding: 80, maxZoom: 19 });
      }
      renderParcelPanel(feature);
      writeUrl();
      mapStatus('Parcel selected');
      loadParcelEnrichment(reference, feature);
      return feature;
    } catch (error) {
      mapStatus(error.message || 'Parcel unavailable', true);
      return null;
    }
  }

  async function loadParcelEnrichment(reference, feature) {
    try {
      const response = await fetch(`/api/v1/enrichment/parcel/details/${encodeURIComponent(reference)}`);
      if (!response.ok) return;
      const enrichment = await response.json();
      renderParcelPanel(feature, enrichment);
    } catch (_) { /* identity remains useful without optional enrichment */ }
  }

  async function identifyAtPoint(lngLat, renderedFeature = null) {
    const reference = renderedFeature && referenceFrom(renderedFeature.properties);
    if (reference) return loadParcelByReference(reference, false);
    const params = new URLSearchParams({ lat: String(lngLat.lat), lng: String(lngLat.lng) });
    try {
      const response = await fetch(`/api/v1/enrichment/parcel/at-point?${params}`);
      if (!response.ok) throw new Error('No parcel at this point');
      const feature = await response.json();
      const foundReference = referenceFrom(feature.properties);
      if (foundReference) return loadParcelByReference(foundReference, false);
      state.selectedFeature = feature;
      renderParcelPanel(feature);
    } catch (error) { mapStatus(error.message, true); }
  }

  function renderSearchResults(results) {
    const list = $('mapSearchResults');
    list.replaceChildren();
    list.hidden = !results.length;
    $('mapSearchInput').setAttribute('aria-expanded', results.length ? 'true' : 'false');
    results.forEach((result, index) => {
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'map-search-result'; button.role = 'option'; button.id = `map-search-result-${index}`;
      const parcelContext = result.kind === 'parcel'
        ? [result.municipality_name, result.sheet ? `Sheet ${result.sheet}` : ''].filter(Boolean).join(' · ')
        : '';
      const context = parcelContext || (result.kind === 'parcel' ? 'Cadastral parcel' : 'Municipality');
      button.innerHTML = `<strong>${escapeHtml(result.label)}</strong><small>${escapeHtml(context)}</small>`;
      button.addEventListener('click', () => chooseSearchResult(result));
      list.appendChild(button);
    });
  }

  function chooseSearchResult(result) {
    renderSearchResults([]);
    $('mapSearchInput').value = result.label;
    if (result.kind === 'parcel') { loadParcelByReference(result.reference, true); return; }
    const profile = result.municipality || result;
    const lat = Number(profile.latitude ?? profile.lat ?? profile.centroid_lat);
    const lng = Number(profile.longitude ?? profile.lng ?? profile.lon ?? profile.centroid_lng);
    if (Number.isFinite(lat) && Number.isFinite(lng)) state.map.flyTo([lng, lat], Math.max(state.map.getZoom(), 13), { duration: .8 });
    mapStatus(`Viewing ${result.label}`);
  }

  async function search(query) {
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
      const payload = response.ok ? await response.json() : { results: [] };
      if (requestId !== state.searchRequest) return;
      let results = payload.results || [];
      if (!results.length) {
        // The canonical profile store is optional in local/degraded
        // deployments. Keep municipality discovery usable through the same
        // Nominatim provider already used by the legacy location search.
        const placeResponse = await fetch(`https://nominatim.openstreetmap.org/search?format=jsonv2&countrycodes=it&limit=8&q=${encodeURIComponent(normalized)}`, { headers: { Accept: 'application/json' }, signal: controller.signal });
        if (placeResponse.ok) {
          const places = await placeResponse.json();
          results = places.map((place) => ({ kind: 'place', label: place.display_name, lat: place.lat, lon: place.lon }));
        }
      }
      if (requestId !== state.searchRequest) return;
      renderSearchResults(results);
      $('mapSearchStatus').textContent = results.length ? '' : 'No result';
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

  function locate() {
    if (!navigator.geolocation) { mapStatus('Location is not available', true); return; }
    mapStatus('Requesting your location…');
    navigator.geolocation.getCurrentPosition((position) => {
      state.map.flyTo([position.coords.longitude, position.coords.latitude], 16, { duration: .8 });
      mapStatus('Location found');
    }, () => mapStatus('Location permission was not granted', true), { enableHighAccuracy: false, timeout: 8000 });
  }

  function setupControls() {
    $('mapSearchSubmit').addEventListener('click', () => search($('mapSearchInput').value));
    $('mapSearchInput').addEventListener('input', () => { clearTimeout(state.searchTimer); state.searchTimer = setTimeout(() => search($('mapSearchInput').value), SEARCH_DEBOUNCE_MS); });
    $('mapSearchInput').addEventListener('keydown', (event) => { if (event.key === 'Enter') { event.preventDefault(); search(event.currentTarget.value); } });
    $('locateButton').addEventListener('click', locate);
    $('shareButton').addEventListener('click', copyLink);
    $('parcelSaveButton').addEventListener('click', saveParcel);
    $('parcelShareButton').addEventListener('click', copyLink);
    $('parcelClearButton').addEventListener('click', clearParcelSelection);
    $('shortlistRefreshButton').addEventListener('click', () => loadShortlist());
    ['shortlistStatusFilter', 'shortlistPriorityFilter', 'shortlistSort'].forEach((id) => {
      $(id).addEventListener('change', renderShortlist);
    });
    $('shortlistHazardFilter').addEventListener('change', () => loadShortlist());
    $('resetButton').addEventListener('click', () => state.map.fitBounds(ITALY_BOUNDS, { padding: 20 }));
    $('parcelCloseButton').addEventListener('click', () => { $('directParcelPanel').hidden = true; updateParcelZoomAffordance(); mapStatus(''); });
    $('layersCloseButton').addEventListener('click', () => { $('mapLayersCard').hidden = true; $('layersToggle').setAttribute('aria-expanded', 'false'); });
    $('layersToggle').addEventListener('click', () => { const card = $('mapLayersCard'); card.hidden = !card.hidden; $('layersToggle').setAttribute('aria-expanded', String(!card.hidden)); });
    document.querySelectorAll('input[name="basemap"]').forEach((input) => input.addEventListener('change', () => switchBasemap(input.value)));
    $('themeButton').addEventListener('click', () => {
      state.dark = !state.dark;
      document.body.classList.toggle('direct-map-dark', state.dark);
      $('themeButton').setAttribute('aria-pressed', String(state.dark));
      switchBasemap(currentBasemap());
    });
  }

  function switchBasemap(kind) {
    const center = state.map.getCenter(); const zoom = state.map.getZoom();
    state.layerFailures.clear();
    state.map.setStyle(styleForBasemap(kind));
    state.map.once('style.load', () => { state.map.jumpTo({ center, zoom }); renderLayerCatalog(); if (state.selectedFeature) renderSelectedGeometry(state.selectedFeature); });
  }

  async function loadCatalog() {
    try {
      const response = await fetch('/api/v1/map/layers');
      if (!response.ok) throw new Error('catalog unavailable');
      const payload = await response.json(); state.catalog = payload.layers || [];
      renderLayerCatalog();
    } catch (_) {
      $('mapLayerList').innerHTML = '<p class="map-muted">Layer catalog unavailable. The basemap remains usable.</p>';
      mapStatus('Map layer catalog unavailable', true);
    }
  }

  async function init() {
    if (!window.maplibregl || !$('directMap')) return;
    setupControls();
    const restored = urlState();
    state.map = new maplibregl.Map({ container: 'directMap', style: styleForBasemap('light'), center: restored.center, zoom: restored.zoom, maxBounds: ITALY_BOUNDS, minZoom: 5, maxZoom: 22, attributionControl: true });
    state.map.addControl(new maplibregl.NavigationControl({ visualizePitch: false }), 'bottom-right');
    state.map.addControl(new maplibregl.FullscreenControl(), 'bottom-right');
    state.map.on('load', async () => {
      mapStatus('Map ready');
      await loadCatalog();
      void loadShortlist();
      void loadLayerHealth();
      if (restored.parcel) await loadParcelByReference(restored.parcel, false);
    });
    state.map.on('moveend', writeUrl);
    state.map.on('zoomend', updateParcelZoomAffordance);
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
      identifyAtPoint(event.lngLat, null);
    });
  }

  document.addEventListener('DOMContentLoaded', init);
})();
