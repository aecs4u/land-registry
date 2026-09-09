// ========================================
// ENRICHMENT MAP LAYERS
// ========================================
// Toggleable Leaflet overlays for the aecs4u-stats-backed enrichment API
// (land_registry/routers/enrichment.py), shown directly on the map rather
// than per-parcel (see parcel-enrichment.js for the click-a-parcel panel).
//
// Ported from real-estates' sales map (POI layer + legend, moveend-refresh
// pattern) but backed by land-registry's own /api/v1/enrichment/* endpoints
// instead of a local aecs4u_stats import — no new backend work needed since
// both apps already read the same aecs4u-stats datasets.

(function () {
    // Fixed category set from aecs4u_stats.osm.config.POI_CATEGORIES — land-registry
    // has no /poi-categories catalogue endpoint (unlike real-estates), so this is a
    // small hardcoded mirror of that fixed, rarely-changing list.
    const POI_CATEGORY_META = {
        universities: { color: '#6f42c1', label: 'Università' },
        schools: { color: '#0d6efd', label: 'Scuole' },
        kindergartens: { color: '#20c997', label: 'Asili' },
        supermarkets: { color: '#fd7e14', label: 'Supermercati' },
        shops: { color: '#e83e8c', label: 'Negozi' },
        pharmacies: { color: '#198754', label: 'Farmacie' },
        hospitals: { color: '#dc3545', label: 'Ospedali' },
        public_transport: { color: '#0dcaf0', label: 'Trasporto pubblico' },
        parks: { color: '#84cc16', label: 'Parchi' },
        restaurants: { color: '#ffc107', label: 'Ristoranti' },
    };
    const POI_RADIUS_KM = 2;

    let poiLayerGroup = null;
    let poiActive = false;
    let poiFetchToken = 0;
    let poiDebounceTimer = null;

    let firesLayerGroup = null;
    let firesActive = false;

    let bulletinLayerGroup = null;
    let bulletinActive = false;
    let bulletinFetchToken = 0;

    function _getFoliumMap() {
        const els = document.querySelectorAll('.leaflet-container');
        if (els.length === 0) return null;
        return window[els[0].id] || null;
    }

    async function _fetchJson(url) {
        try {
            const resp = await fetch(url);
            if (!resp.ok) return null;
            return await resp.json();
        } catch (e) {
            return null;
        }
    }

    function _legendItem(color, label) {
        return `<span class="enrichment-legend-item"><span class="enrichment-legend-dot" style="background:${color}"></span>${label}</span>`;
    }

    // ---- Canonical aecs4u-stats layers --------------------------------
    //
    // The catalog is server-owned: the browser never chooses a table name.
    // VectorGrid is preferred for large layers; bounded GeoJSON is retained
    // as a compatibility fallback for installations without the plugin.
    const CANONICAL_LAYER_COLORS = ['#7c3aed', '#0f766e', '#2563eb', '#b45309', '#be123c', '#15803d'];
    const canonicalLayerSpecs = {};
    const canonicalLayerObjects = {};
    const canonicalLayerOpacities = {};
    let canonicalLayerMap = null;
    let canonicalRefreshAttached = false;
    let canonicalLayerCatalogLoaded = false;

    function _canonicalStyle(spec, feature) {
        const index = Object.keys(canonicalLayerSpecs).indexOf(spec.id);
        const color = CANONICAL_LAYER_COLORS[Math.max(index, 0) % CANONICAL_LAYER_COLORS.length];
        if (spec.kind === 'point') {
            return { radius: 4, color, fillColor: color, fillOpacity: 0.75, weight: 1 };
        }
        return { color, weight: 1.5, opacity: 0.85, fillColor: color, fillOpacity: 0.12 };
    }

    function _canonicalPopup(properties, details) {
        const rows = Object.entries(properties || {})
            .filter(([, value]) => value !== null && value !== undefined && value !== '')
            .slice(0, 10)
            .map(([key, value]) => `<div><b>${_escapeHtml(key)}:</b> ${_escapeHtml(value)}</div>`)
            .join('');
        const related = Object.entries((details && details.related) || {})
            .map(([name, value]) => {
                if (!Array.isArray(value)) return '';
                const items = value.slice(0, 3).map(item => Object.entries(item || {})
                    .filter(([, field]) => field !== null && field !== undefined && field !== '')
                    .slice(0, 5)
                    .map(([key, field]) => `<div><b>${_escapeHtml(key)}:</b> ${_escapeHtml(field)}</div>`)
                    .join('')).join('<hr>');
                return `<div class="canonical-layer-related"><b>${_escapeHtml(name)}</b> (${value.length})${items ? `<div>${items}</div>` : ''}</div>`;
            }).filter(Boolean).join('');
        return `<div class="canonical-layer-popup">${rows || _escapeHtml('No attributes')}${related}</div>`;
    }

    async function _canonicalLoadFeatureDetails(spec, featureId, setContent) {
        if (!spec.detail_url || featureId === null || featureId === undefined) return;
        try {
            const response = await fetch(spec.detail_url.replace('{feature_id}', encodeURIComponent(featureId)));
            if (!response.ok) return;
            const details = await response.json();
            setContent(_canonicalPopup(details.properties, details));
        } catch (error) {
            console.warn('[EnrichmentLayers] Canonical feature detail failed', error);
        }
    }

    function _canonicalSetStatus(layerId, state, message) {
        const status = document.getElementById(`canonicalStatus-${layerId}`);
        if (!status) return;
        status.className = `canonical-layer-status ${state || ''}`.trim();
        status.textContent = message || '';
    }

    function _canonicalApplyOpacity(entry, value) {
        if (!entry || !entry.layer) return;
        const opacity = Math.max(0, Math.min(1, Number(value)));
        const style = { opacity, fillOpacity: opacity * 0.12 };
        if (typeof entry.layer.setStyle === 'function') {
            entry.layer.setStyle(style);
        } else if (typeof entry.layer.eachLayer === 'function') {
            entry.layer.eachLayer(layer => {
                if (typeof layer.setStyle === 'function') layer.setStyle(style);
            });
        }
        entry.opacity = opacity;
    }

    function setCanonicalMapLayerOpacity(layerId, value) {
        const opacity = Math.max(0, Math.min(1, Number(value)));
        canonicalLayerOpacities[layerId] = opacity;
        const output = document.getElementById(`canonicalOpacityValue-${layerId}`);
        if (output) output.textContent = `${Math.round(opacity * 100)}%`;
        const entry = canonicalLayerObjects[layerId];
        if (!entry) return;
        _canonicalApplyOpacity(entry, opacity);
    }

    function _canonicalBoundsParams(map) {
        const bounds = map.getBounds();
        return new URLSearchParams({
            west: bounds.getWest().toFixed(6),
            south: bounds.getSouth().toFixed(6),
            east: bounds.getEast().toFixed(6),
            north: bounds.getNorth().toFixed(6),
        });
    }

    async function _refreshCanonicalGeoJsonLayer(spec) {
        const map = canonicalLayerMap || _getFoliumMap();
        const entry = canonicalLayerObjects[spec.id];
        if (!map || !entry || entry.mode !== 'geojson' || !entry.active) return;
        const token = ++entry.token;
        _canonicalSetStatus(spec.id, 'loading', 'Loading…');
        try {
            const response = await fetch(`${spec.geojson_url}?${_canonicalBoundsParams(map)}`);
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const collection = await response.json();
            if (token !== entry.token || !entry.active) return;
            const next = L.geoJSON(collection, {
                style: feature => _canonicalStyle(spec, feature),
                pointToLayer: (feature, latlng) => L.circleMarker(latlng, _canonicalStyle(spec, feature)),
                onEachFeature: (feature, layer) => {
                    layer.bindPopup(_canonicalPopup(feature.properties));
                    layer.on('popupopen', () => _canonicalLoadFeatureDetails(
                        spec,
                        feature.id ?? (feature.properties || {})[spec.id_column],
                        content => layer.setPopupContent(content),
                    ));
                },
            });
            if (entry.layer && map.hasLayer(entry.layer)) map.removeLayer(entry.layer);
            entry.layer = next.addTo(map);
            _canonicalApplyOpacity(entry, entry.opacity);
            _canonicalSetStatus(spec.id, collection.zoom_required ? 'zoom-required' : 'ready',
                collection.zoom_required ? `Zoom ${collection.zoom_required}+` : 'Ready');
        } catch (error) {
            if (token === entry.token && entry.active) _canonicalSetStatus(spec.id, 'error', 'Unavailable');
            console.warn('[EnrichmentLayers] Canonical GeoJSON failed', error);
        }
    }

    function _useCanonicalGeoJsonFallback(spec, map) {
        const entry = canonicalLayerObjects[spec.id];
        if (!entry || !entry.active) return;
        if (entry.layer && map.hasLayer(entry.layer)) map.removeLayer(entry.layer);
        entry.mode = 'geojson';
        _canonicalSetStatus(spec.id, 'loading', 'Using GeoJSON…');
        _refreshCanonicalGeoJsonLayer(spec);
    }

    function _createCanonicalLayer(spec, map) {
        const opacityControl = document.getElementById(`canonicalOpacity-${spec.id}`);
        const entry = canonicalLayerObjects[spec.id] = {
            active: true, layer: null, mode: 'vector', token: 0,
            opacity: canonicalLayerOpacities[spec.id] ?? (opacityControl ? Number(opacityControl.value) : 0.85),
        };
        _canonicalSetStatus(spec.id, map.getZoom() < spec.min_zoom ? 'zoom-required' : 'loading',
            map.getZoom() < spec.min_zoom ? `Zoom ${spec.min_zoom}+` : 'Loading…');
        if (L.vectorGrid && typeof L.vectorGrid.protobuf === 'function') {
            const style = _canonicalStyle(spec);
            entry.layer = L.vectorGrid.protobuf(spec.tile_url, {
                pane: map.getPane('canonicalMapLayerPane') ? 'canonicalMapLayerPane' : undefined,
                minZoom: spec.min_zoom,
                maxZoom: 22,
                interactive: true,
                vectorTileLayerStyles: { [spec.id]: style },
                getFeatureId: feature => feature.properties?.[spec.id_column] || feature.properties?.id,
            });
            entry.layer.on('click', event => {
                const properties = event.layer && event.layer.properties;
                if (properties) {
                    const popup = L.popup().setLatLng(event.latlng).setContent(_canonicalPopup(properties)).openOn(map);
                    _canonicalLoadFeatureDetails(
                        spec,
                        properties[spec.id_column] ?? properties.id,
                        content => popup.setContent(content),
                    );
                }
            });
            entry.layer.on('tileload', () => _canonicalSetStatus(spec.id, 'ready', 'Ready'));
            entry.layer.on('tileerror', () => _useCanonicalGeoJsonFallback(spec, map));
        } else {
            entry.mode = 'geojson';
            entry.layer = L.layerGroup();
        }
        entry.layer.addTo(map);
        _canonicalApplyOpacity(entry, entry.opacity);
        if (entry.mode === 'geojson') _refreshCanonicalGeoJsonLayer(spec);
    }

    function toggleCanonicalMapLayer(layerId) {
        const spec = canonicalLayerSpecs[layerId];
        const map = canonicalLayerMap || _getFoliumMap();
        const button = document.getElementById(`toggleCanonicalLayer-${layerId}`);
        if (!spec || !map) return;
        const entry = canonicalLayerObjects[layerId];
        if (!entry || !entry.active) {
            _createCanonicalLayer(spec, map);
            if (!canonicalRefreshAttached) {
                map.on('moveend', _refreshCanonicalGeoJsonLayers);
                canonicalRefreshAttached = true;
            }
            if (button) button.classList.add('active');
        } else {
            entry.active = false;
            if (entry.layer && map.hasLayer(entry.layer)) map.removeLayer(entry.layer);
            if (button) button.classList.remove('active');
            _canonicalSetStatus(layerId, '', 'Hidden');
        }
    }

    function _refreshCanonicalGeoJsonLayers() {
        Object.keys(canonicalLayerSpecs).forEach(layerId => {
            const entry = canonicalLayerObjects[layerId];
            if (entry && entry.active && entry.mode === 'geojson') {
                _refreshCanonicalGeoJsonLayer(canonicalLayerSpecs[layerId]);
            }
        });
    }

    async function _loadCanonicalLayerCatalog() {
        if (canonicalLayerCatalogLoaded) return;
        const container = document.getElementById('canonicalMapLayers');
        if (!container) return;
        canonicalLayerCatalogLoaded = true;
        const data = await _fetchJson('/api/v1/map/layers');
        if (!data || !data.layers) {
            container.innerHTML = '<span class="enrichment-legend-meta">Database layers unavailable</span>';
            return;
        }
        data.layers.forEach(spec => { canonicalLayerSpecs[spec.id] = spec; });
        container.innerHTML = data.layers.map((spec, index) => `
            <div class="canonical-layer-control">
                <button id="toggleCanonicalLayer-${_escapeHtml(spec.id)}" class="tool-btn-ghost canonical-layer-toggle"
                        onclick="toggleCanonicalMapLayer('${_escapeHtml(spec.id)}')"
                        title="${_escapeHtml(spec.table)} · zoom ${spec.min_zoom}+">
                    <span class="canonical-layer-swatch" style="background:${CANONICAL_LAYER_COLORS[index % CANONICAL_LAYER_COLORS.length]}"></span>${_escapeHtml(spec.title)}
                    <span id="canonicalStatus-${_escapeHtml(spec.id)}" class="canonical-layer-status">Available</span>
                </button>
                <label class="canonical-layer-opacity" for="canonicalOpacity-${_escapeHtml(spec.id)}">
                    Opacity <input id="canonicalOpacity-${_escapeHtml(spec.id)}" type="range" min="0" max="1" step="0.05" value="0.85"
                                   oninput="setCanonicalMapLayerOpacity('${_escapeHtml(spec.id)}', this.value)" aria-label="${_escapeHtml(spec.title)} opacity">
                    <output id="canonicalOpacityValue-${_escapeHtml(spec.id)}">85%</output>
                </label>
                <span class="canonical-layer-meta">aecs4u-stats · zoom ${spec.min_zoom}+ · ${_escapeHtml(spec.coverage_note || 'Coverage available')}</span>
            </div>`).join('');
        if (!data.available) {
            container.insertAdjacentHTML('beforeend', '<span class="enrichment-legend-meta">PostGIS source is not configured</span>');
            container.querySelectorAll('button').forEach(button => { button.disabled = true; });
            return;
        }
        const health = await _fetchJson('/api/v1/map/layers/health');
        if (!health || !health.layers) {
            container.insertAdjacentHTML('beforeend', '<span class="enrichment-legend-meta">Layer health is temporarily unavailable</span>');
            return;
        }
        const readiness = new Map((health && health.layers || []).map(item => [item.id, item]));
        let unavailable = 0;
        let partial = 0;
        container.querySelectorAll('.canonical-layer-toggle').forEach(button => {
            const layerId = button.id.replace('toggleCanonicalLayer-', '');
            const status = readiness.get(layerId);
            if (status && !status.available) {
                unavailable += 1;
                button.disabled = true;
                button.title = `${button.title} · database layer not ready`;
                _canonicalSetStatus(layerId, 'error', 'Not ready');
            }
            if (status && status.coverage === 'partial') {
                partial += 1;
                button.title = `${button.title} · partial coverage`;
                if (status.available) _canonicalSetStatus(layerId, 'partial', 'Partial');
            }
        });
        if (unavailable) {
            container.insertAdjacentHTML('beforeend', `<span class="enrichment-legend-meta">${unavailable} database layer(s) unavailable</span>`);
        }
        if (partial) {
            container.insertAdjacentHTML('beforeend', `<span class="enrichment-legend-meta">${partial} layer(s) have partial database coverage</span>`);
        }
    }

    // ---- POIs ---------------------------------------------------------

    async function _refreshPoiLayer() {
        const map = _getFoliumMap();
        if (!map || !poiLayerGroup) return;
        const token = ++poiFetchToken;
        const center = map.getCenter();
        const data = await _fetchJson(`/api/v1/enrichment/pois/?lat=${center.lat}&lng=${center.lng}&radius_km=${POI_RADIUS_KM}`);
        if (token !== poiFetchToken) return; // superseded by a newer move

        poiLayerGroup.clearLayers();
        const legendEl = document.getElementById('enrichmentPoiLegend');
        if (!data || !data.total) {
            if (legendEl) legendEl.innerHTML = '';
            return;
        }

        const present = [];
        Object.entries(data.categories || {}).forEach(([cat, list]) => {
            if (!list || !list.length) return;
            present.push(cat);
            const meta = POI_CATEGORY_META[cat] || { color: '#666', label: cat };
            list.forEach(poi => {
                if (poi.lat == null || poi.lng == null) return;
                L.circleMarker([poi.lat, poi.lng], {
                    radius: 5,
                    color: meta.color,
                    fillColor: meta.color,
                    fillOpacity: 0.85,
                    weight: 1,
                }).bindTooltip(`${poi.name || meta.label} · ${meta.label}`).addTo(poiLayerGroup);
            });
        });
        if (legendEl) {
            legendEl.innerHTML = present
                .map(cat => _legendItem((POI_CATEGORY_META[cat] || {}).color || '#666', (POI_CATEGORY_META[cat] || {}).label || cat))
                .join('');
        }
    }

    function _refreshPoiLayerDebounced() {
        clearTimeout(poiDebounceTimer);
        poiDebounceTimer = setTimeout(_refreshPoiLayer, 400);
    }

    function togglePoiLayer() {
        const map = _getFoliumMap();
        const btn = document.getElementById('toggleEnrichmentPois');
        if (!map) { console.warn('[EnrichmentLayers] Map not ready'); return; }
        if (!poiLayerGroup) poiLayerGroup = L.layerGroup();

        poiActive = !poiActive;
        if (poiActive) {
            poiLayerGroup.addTo(map);
            map.on('moveend', _refreshPoiLayerDebounced);
            _refreshPoiLayer();
        } else {
            map.off('moveend', _refreshPoiLayerDebounced);
            map.removeLayer(poiLayerGroup);
            const legendEl = document.getElementById('enrichmentPoiLegend');
            if (legendEl) legendEl.innerHTML = '';
        }
        if (btn) btn.classList.toggle('active', poiActive);
    }

    // ---- Active fires (NASA FIRMS) -------------------------------------

    function _fireColor(confidence) {
        const numeric = Number(confidence);
        if (!Number.isNaN(numeric)) {
            if (numeric >= 80) return '#dc2626';
            if (numeric >= 50) return '#f97316';
            return '#facc15';
        }
        const c = (confidence || '').toString().toLowerCase();
        if (c === 'h' || c === 'high') return '#dc2626';
        if (c === 'n' || c === 'nominal') return '#f97316';
        return '#facc15';
    }

    async function _refreshFiresLayer() {
        const map = _getFoliumMap();
        if (!map || !firesLayerGroup) return;
        firesLayerGroup.clearLayers();
        const countEl = document.getElementById('enrichmentFiresCount');
        const data = await _fetchJson('/api/v1/enrichment/fires');
        if (!data) { if (countEl) countEl.textContent = ''; return; }

        (data.detections || []).forEach(d => {
            const lat = Number(d.latitude), lng = Number(d.longitude);
            if (Number.isNaN(lat) || Number.isNaN(lng)) return;
            const color = _fireColor(d.confidence);
            L.circleMarker([lat, lng], {
                radius: 5,
                color,
                fillColor: color,
                fillOpacity: 0.85,
                weight: 1,
            }).bindTooltip(`${d.acq_date || ''}${d.frp ? ' · ' + d.frp + ' MW' : ''}`).addTo(firesLayerGroup);
        });
        if (countEl) countEl.textContent = data.count ? `(${data.count})` : '(0)';
    }

    function toggleFiresLayer() {
        const map = _getFoliumMap();
        const btn = document.getElementById('toggleEnrichmentFires');
        if (!map) { console.warn('[EnrichmentLayers] Map not ready'); return; }
        if (!firesLayerGroup) firesLayerGroup = L.layerGroup();

        firesActive = !firesActive;
        if (firesActive) {
            firesLayerGroup.addTo(map);
            _refreshFiresLayer();
        } else {
            map.removeLayer(firesLayerGroup);
            const countEl = document.getElementById('enrichmentFiresCount');
            if (countEl) countEl.textContent = '';
        }
        if (btn) btn.classList.toggle('active', firesActive);
    }

    // ---- Civil Protection criticality bulletin ------------------------

    const BULLETIN_LEVELS = {
        red: { rank: 4, color: '#dc2626', label: 'Allerta rossa' },
        orange: { rank: 3, color: '#f97316', label: 'Allerta arancione' },
        yellow: { rank: 2, color: '#facc15', label: 'Allerta gialla' },
        green: { rank: 1, color: '#22c55e', label: 'Nessuna allerta' },
        unknown: { rank: 0, color: '#94a3b8', label: 'Non disponibile' },
    };

    function _bulletinSeverity(description) {
        const text = String(description || '').toUpperCase();
        if (text.includes('ROSSA')) return BULLETIN_LEVELS.red;
        if (text.includes('ARANCIONE')) return BULLETIN_LEVELS.orange;
        if (text.includes('GIALLA')) return BULLETIN_LEVELS.yellow;
        if (text.includes('NESSUNA ALLERTA') || text.includes('ASSENZA DI FENOMENI')) return BULLETIN_LEVELS.green;
        return BULLETIN_LEVELS.unknown;
    }

    function _bulletinFeatureSeverity(properties) {
        const props = properties || {};
        const represented = props['Rappresentata nella mappa'];
        if (represented) return _bulletinSeverity(represented);
        return [
            props['Per rischio idraulico'],
            props['Per rischio temporali'],
            props['Per rischio idrogeologico'],
        ].map(_bulletinSeverity).sort((a, b) => b.rank - a.rank)[0] || BULLETIN_LEVELS.unknown;
    }

    function _bulletinPopup(properties) {
        const props = properties || {};
        const severity = _bulletinFeatureSeverity(props);
        const rows = [
            ['Rischio idraulico', props['Per rischio idraulico']],
            ['Rischio temporali', props['Per rischio temporali']],
            ['Rischio idrogeologico', props['Per rischio idrogeologico']],
        ].map(([label, value]) => `<div><b>${label}:</b> ${_escapeHtml(value || '—')}</div>`).join('');
        return `
            <div class="bulletin-popup">
                <strong>${_escapeHtml(props['Nome zona'] || 'Zona di allerta')}</strong>
                <div style="color:${severity.color};font-weight:600">${severity.label}</div>
                ${rows}
            </div>`;
    }

    function _renderBulletinLegend(data, zoneCount) {
        const legendEl = document.getElementById('enrichmentBulletinLegend');
        if (!legendEl) return;
        const levels = ['red', 'orange', 'yellow', 'green'];
        const issue = data && (data.name || data.stamp);
        legendEl.innerHTML = levels.map((key) => {
            const level = BULLETIN_LEVELS[key];
            return _legendItem(level.color, level.label);
        }).join('') + (issue
            ? `<span class="enrichment-legend-meta">${_escapeHtml(issue)} · ${zoneCount} zone</span>`
            : '');
    }

    async function _refreshBulletinLayer() {
        const map = _getFoliumMap();
        if (!map || !bulletinLayerGroup) return;
        const token = ++bulletinFetchToken;
        const countEl = document.getElementById('enrichmentBulletinCount');
        const legendEl = document.getElementById('enrichmentBulletinLegend');
        if (countEl) countEl.textContent = '(…)';
        if (legendEl) legendEl.innerHTML = '<span class="enrichment-legend-meta">Caricamento bollettino…</span>';

        const data = await _fetchJson('/api/v1/enrichment/bulletin');
        if (token !== bulletinFetchToken || !bulletinActive) return;
        bulletinLayerGroup.clearLayers();

        const topology = data && data.today_zones;
        const object = topology && topology.objects ? Object.values(topology.objects)[0] : null;
        if (!topology || !object || !window.topojson || typeof window.topojson.feature !== 'function') {
            if (countEl) countEl.textContent = '(0)';
            if (legendEl) legendEl.innerHTML = '<span class="enrichment-legend-meta">Bollettino non disponibile</span>';
            return;
        }

        const collection = window.topojson.feature(topology, object);
        const features = collection.features || (collection.type === 'Feature' ? [collection] : []);
        const zoneLayer = L.geoJSON({ type: 'FeatureCollection', features }, {
            pane: map.getPane('bulletinPane') ? 'bulletinPane' : undefined,
            style: (feature) => {
                const severity = _bulletinFeatureSeverity(feature.properties);
                return {
                    color: severity.color,
                    fillColor: severity.color,
                    weight: 1.5,
                    opacity: 0.9,
                    fillOpacity: severity.rank > 1 ? 0.35 : 0.12,
                };
            },
            onEachFeature: (feature, layer) => {
                const props = feature.properties || {};
                const severity = _bulletinFeatureSeverity(props);
                layer.bindTooltip(`${_escapeHtml(props['Nome zona'] || 'Zona')} · ${severity.label}`);
                layer.bindPopup(_bulletinPopup(props), { maxWidth: 360 });
                layer.on('mouseover', () => layer.setStyle({ weight: 3, fillOpacity: 0.5 }));
                layer.on('mouseout', () => zoneLayer.resetStyle(layer));
            },
        });
        zoneLayer.addTo(bulletinLayerGroup);
        if (countEl) countEl.textContent = `(${features.length})`;
        _renderBulletinLegend(data, features.length);
    }

    function toggleBulletinLayer() {
        const map = _getFoliumMap();
        const btn = document.getElementById('toggleEnrichmentBulletin');
        if (!map) { console.warn('[EnrichmentLayers] Map not ready'); return; }
        if (!map.getPane('bulletinPane')) {
            map.createPane('bulletinPane');
            map.getPane('bulletinPane').style.zIndex = 430;
        }
        if (!bulletinLayerGroup) bulletinLayerGroup = L.layerGroup();

        bulletinActive = !bulletinActive;
        if (bulletinActive) {
            bulletinLayerGroup.addTo(map);
            _refreshBulletinLayer();
        } else {
            bulletinFetchToken += 1;
            map.removeLayer(bulletinLayerGroup);
            const countEl = document.getElementById('enrichmentBulletinCount');
            const legendEl = document.getElementById('enrichmentBulletinLegend');
            if (countEl) countEl.textContent = '';
            if (legendEl) legendEl.innerHTML = '';
        }
        if (btn) btn.classList.toggle('active', bulletinActive);
    }

    // ---- Cadastral boundaries (own datashader tile endpoint) -------------
    //
    // Same /api/v1/tiles/cadastral-boundaries/{z}/{x}/{y}.png endpoint that
    // real-estates' sales map already consumes cross-origin (see the CORP
    // override in main.py) — here it's same-origin, so no CORS/CORP concerns.
    // Two stacked outline-only (no fill) tile layers so the base map is
    // never hidden underneath:
    //   - layer=map: foglio (sheet) outlines, from minZoom 13
    //   - layer=ple: individual particella (parcel) outlines, from minZoom
    //     16 — particelle are tiny and far more numerous, so they only make
    //     sense once you're zoomed in close.
    // minZoom mirrors real-estates: below it Leaflet simply never requests tiles.

    let cadastralMapLayer = null;
    let cadastralPleLayer = null;
    let cadastralVectorMapLayer = null;
    let cadastralVectorPleLayer = null;
    let cadastralVectorTilesFailed = false;
    let cadastralBoundaryActive = false;
    let cadastralSelectionLayer = null;
    let cadastralLookupToken = 0;

    // Viewport parcels are loaded as real GeoJSON only once individual
    // particelle are meaningful.  The boundary tiles remain responsible for
    // the lightweight map-wide outlines; this layer supplies the parcel
    // features for hover/click details in the current viewport.
    const VIEWPORT_PARCEL_MIN_ZOOM = 16;
    const VIEWPORT_PARCEL_LIMIT = 5000;
    let viewportParcelMap = null;
    let viewportParcelLayer = null;
    let viewportParcelRefreshTimer = null;
    let viewportParcelAbortController = null;
    let viewportParcelRequestToken = 0;
    let viewportParcelLastBoundsKey = '';

    function _escapeHtml(value) {
        return String(value ?? '').replace(/[&<>'"]/g, (char) => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
        })[char]);
    }

    function _parcelReference(feature) {
        const props = (feature && feature.properties) || {};
        return props.NATIONALCADASTRALREFERENCE
            || props.nationalcadastralreference
            || props.national_cadastral_reference
            || props.national_reference
            || props.reference
            || null;
    }

    function _ensureViewportParcelPane(map) {
        if (!map || !map.createPane || map.getPane('cadastralViewportPane')) return;
        const pane = map.createPane('cadastralViewportPane');
        // Keep the loaded features above the base map, while the dedicated
        // boundary tile pane remains available for the crisp outlines.
        pane.style.zIndex = '435';
    }

    function _clearViewportParcelLayer(map) {
        if (map && viewportParcelLayer && map.hasLayer(viewportParcelLayer)) {
            map.removeLayer(viewportParcelLayer);
        }
        viewportParcelLayer = null;
    }

    function _viewportParcelBoundsKey(map, bounds) {
        return [
            map.getZoom(),
            bounds.getWest().toFixed(5),
            bounds.getSouth().toFixed(5),
            bounds.getEast().toFixed(5),
            bounds.getNorth().toFixed(5),
        ].join(':');
    }

    function _createViewportParcelLayer(featureCollection, map) {
        _ensureViewportParcelPane(map);
        const pane = map.getPane('cadastralViewportPane') ? 'cadastralViewportPane' : undefined;
        const layer = L.geoJSON(featureCollection, {
            pane,
            interactive: true,
            style: {
                color: '#0f766e',
                weight: 1,
                opacity: 0.5,
                fillColor: '#2dd4bf',
                fillOpacity: 0.035,
            },
            onEachFeature(feature, featureLayer) {
                featureLayer.on('click', event => {
                    L.DomEvent.stopPropagation(event);
                    _selectParcelFeature(feature, map);
                });
                const reference = _parcelReference(feature);
                if (reference) {
                    featureLayer.bindTooltip(`Particella ${_escapeHtml(reference)}`, {
                        sticky: true,
                        direction: 'top',
                        opacity: 0.9,
                    });
                }
            },
        });
        layer.options = layer.options || {};
        layer.options.cadastralViewportLayer = true;
        return layer;
    }

    async function _refreshViewportParcelLayer() {
        const map = viewportParcelMap || _getFoliumMap();
        if (!map) return;
        if (!cadastralBoundaryActive) return;

        const token = ++viewportParcelRequestToken;
        if (viewportParcelAbortController) viewportParcelAbortController.abort();
        viewportParcelAbortController = null;

        if (map.getZoom() < VIEWPORT_PARCEL_MIN_ZOOM) {
            viewportParcelLastBoundsKey = '';
            _clearViewportParcelLayer(map);
            return;
        }

        const bounds = map.getBounds();
        if (!bounds || !bounds.isValid()) return;
        const boundsKey = _viewportParcelBoundsKey(map, bounds);
        if (boundsKey === viewportParcelLastBoundsKey && viewportParcelLayer) return;
        viewportParcelLastBoundsKey = boundsKey;

        const controller = new AbortController();
        viewportParcelAbortController = controller;
        const params = new URLSearchParams({
            min_lng: bounds.getWest().toFixed(6),
            min_lat: bounds.getSouth().toFixed(6),
            max_lng: bounds.getEast().toFixed(6),
            max_lat: bounds.getNorth().toFixed(6),
            limit: String(VIEWPORT_PARCEL_LIMIT),
            include_geometry: 'true',
        });

        try {
            const response = await fetch(`/api/v1/enrichment/parcels/in-bbox/?${params}`, {
                signal: controller.signal,
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const featureCollection = await response.json();
            if (token !== viewportParcelRequestToken || map !== _getFoliumMap()) return;

            const nextLayer = _createViewportParcelLayer(featureCollection, map);
            _clearViewportParcelLayer(map);
            viewportParcelLayer = nextLayer;
            nextLayer.addTo(map);
        } catch (error) {
            if (error.name !== 'AbortError') {
                // Keep the last successful viewport visible and allow a later
                // move/refresh to retry the failed request.
                viewportParcelLastBoundsKey = '';
                console.warn('[EnrichmentLayers] Viewport parcel load failed', error);
            }
        } finally {
            if (viewportParcelAbortController === controller) {
                viewportParcelAbortController = null;
            }
        }
    }

    function _refreshViewportParcelLayerDebounced() {
        clearTimeout(viewportParcelRefreshTimer);
        viewportParcelRefreshTimer = setTimeout(_refreshViewportParcelLayer, 250);
    }

    function _attachViewportParcelLoader(map) {
        if (!map || viewportParcelMap === map) return;
        if (viewportParcelMap) viewportParcelMap.off('moveend', _refreshViewportParcelLayerDebounced);
        viewportParcelMap = map;
        map.on('moveend', _refreshViewportParcelLayerDebounced);
        _refreshViewportParcelLayer();
    }

    function _setParcelUrl(feature) {
        const url = new URL(window.location.href);
        const reference = _parcelReference(feature);
        if (reference) url.searchParams.set('parcel', reference);
        else url.searchParams.delete('parcel');
        history.replaceState(null, '', url);
    }

    function _clearCadastralParcelSelection(options = {}) {
        const map = _getFoliumMap();
        if (map && cadastralSelectionLayer && map.hasLayer(cadastralSelectionLayer)) {
            map.removeLayer(cadastralSelectionLayer);
        }
        cadastralSelectionLayer = null;
        if (options.clearUrl !== false) _setParcelUrl(null);
    }

    function _selectParcelFeature(feature, map) {
        if (!feature || feature.type !== 'Feature' || !feature.geometry || !map) return false;
        _clearCadastralParcelSelection({ clearUrl: false });
        cadastralSelectionLayer = L.geoJSON(feature, {
            interactive: false,
            style: {
                color: '#14b8a6',
                weight: 3,
                opacity: 1,
                fillColor: '#2dd4bf',
                fillOpacity: 0.22,
            },
        }).addTo(map);
        if (typeof cadastralSelectionLayer.bringToFront === 'function') {
            cadastralSelectionLayer.bringToFront();
        }
        map.closePopup();
        _setParcelUrl(feature);
        if (typeof showParcelInfo === 'function') {
            showParcelInfo(feature, cadastralSelectionLayer);
        }
        return true;
    }

    async function _lookupParcelAtPoint(lat, lng, map) {
        const token = ++cadastralLookupToken;
        try {
            const response = await fetch(
                `/api/v1/enrichment/parcel/at-point?lat=${encodeURIComponent(lat)}&lng=${encodeURIComponent(lng)}`
            );
            if (!response.ok || token !== cadastralLookupToken) return false;
            return _selectParcelFeature(await response.json(), map);
        } catch (error) {
            console.warn('[EnrichmentLayers] Parcel lookup failed', error);
            return false;
        }
    }

    async function _showLegacyCadastralPopup(lat, lng, layer, map) {
        try {
            const response = await fetch(
                `/api/v1/cadastral-identify?lat=${encodeURIComponent(lat)}&lng=${encodeURIComponent(lng)}&layer=${layer}`
            );
            if (!response.ok) return;
            const data = await response.json();
            if (!data.found) return;
            const rows = [];
            const reference = data.reference || data.label;
            if (reference) {
                rows.push(`<b>${layer === 'ple' ? 'Particella' : 'Foglio'}:</b> ${_escapeHtml(reference)}`);
            }
            if (data.comune) rows.push(`<b>Comune:</b> ${_escapeHtml(data.comune)}`);
            if (data.provincia) rows.push(`<b>Provincia:</b> ${_escapeHtml(data.provincia)}`);
            if (data.regione) rows.push(`<b>Regione:</b> ${_escapeHtml(data.regione)}`);
            if (rows.length) {
                // The click that opens this popup also opens the docked-right
                // "Dettagli Particella" panel; without accounting for it,
                // Leaflet's own auto-pan only clears the viewport edge, so a
                // popup near the right side rendered partly underneath the
                // panel that just appeared over it.
                const panelEl = document.getElementById('parcelInfoPanel');
                const panelWidth = (panelEl && panelEl.offsetWidth > 0) ? panelEl.offsetWidth : 0;
                L.popup({ autoPanPaddingBottomRight: L.point(panelWidth + 20, 20) })
                    .setLatLng([lat, lng]).setContent(rows.join('<br>')).openOn(map);
            }
        } catch (error) {
            console.warn('[EnrichmentLayers] Cadastral identify failed', error);
        }
    }

    function switchToRasterFallback(map) {
        if (cadastralVectorTilesFailed) return;
        cadastralVectorTilesFailed = true;
        [cadastralVectorMapLayer, cadastralVectorPleLayer].forEach(layer => {
            if (layer && map.hasLayer(layer)) map.removeLayer(layer);
        });
        cadastralMapLayer = L.tileLayer(
            '/api/v1/tiles/cadastral-boundaries/{z}/{x}/{y}.png?layer=map',
            { pane: 'cadastralBoundaryPane', minZoom: 13, maxZoom: 22, maxNativeZoom: 19, tileSize: 512, attribution: 'Cadastral data' }
        );
        cadastralPleLayer = L.tileLayer(
            '/api/v1/tiles/cadastral-boundaries/{z}/{x}/{y}.png?layer=ple',
            { pane: 'cadastralBoundaryPane', minZoom: 16, maxZoom: 22, maxNativeZoom: 19, tileSize: 512 }
        );
        // The raster fallback hits the same backend as the vector tiles it's
        // falling back from, so when that backend is simply unavailable
        // (e.g. PostGIS not configured in this environment), it fails too —
        // and without a guard here, Leaflet just kept requesting the full
        // tile grid on every pan/zoom (28+ failed requests per load).
        // One failure means the service is down for this session; stop
        // asking rather than retry per-tile.
        let rasterTilesFailed = false;
        const giveUpOnRasterTiles = () => {
            if (rasterTilesFailed) return;
            rasterTilesFailed = true;
            [cadastralMapLayer, cadastralPleLayer].forEach(layer => {
                if (layer && map.hasLayer(layer)) map.removeLayer(layer);
            });
            cadastralBoundaryActive = false;
            const btn = document.getElementById('toggleEnrichmentCadastral');
            if (btn) btn.classList.remove('active');
            if (typeof showToastNotification === 'function') {
                const t = window.t || (key => key);
                showToastNotification(t('Cadastral boundaries are unavailable right now'), 'warning');
            }
        };
        cadastralMapLayer.on('tileerror', giveUpOnRasterTiles);
        cadastralPleLayer.on('tileerror', giveUpOnRasterTiles);
        if (cadastralBoundaryActive) {
            cadastralMapLayer.addTo(map);
            cadastralPleLayer.addTo(map);
        }
    }

    function toggleCadastralBoundaryLayer() {
        const map = _getFoliumMap();
        const btn = document.getElementById('toggleEnrichmentCadastral');
        if (!map) { console.warn('[EnrichmentLayers] Map not ready'); return; }
        if (!cadastralMapLayer) {
            if (map.createPane && !map.getPane('cadastralBoundaryPane')) {
                map.createPane('cadastralBoundaryPane').style.zIndex = 440;
            }
            const pane = map.getPane('cadastralBoundaryPane') ? 'cadastralBoundaryPane' : undefined;
            // maxZoom 22 matches the map's own ceiling (map.py) — each base
            // tile layer now has a max_native_zoom below that and lets
            // Leaflet over-zoom (upscale) past its native resolution instead
            // of going blank, so this overlay is never left floating over a
            // dead base map. Unlike the base imagery, this endpoint has no
            // native-resolution ceiling of its own — it rasterizes on demand
            // from vector geometry, so it stays crisp at any zoom.
            if (!cadastralVectorTilesFailed && L.vectorGrid && typeof L.vectorGrid.protobuf === 'function') {
                const vectorOptions = {
                    pane,
                    minZoom: 13,
                    maxZoom: 22,
                    vectorTileLayerStyles: {
                        cadastral: { weight: 1, color: '#cc5500', opacity: 0.85, fill: false }
                    },
                    interactive: false,
                    getFeatureId: feature => feature.properties?.id || feature.properties?.feature_id
                };
                const vectorTileUrl = layerType => `/api/v1/tiles/cadastral-boundaries/{z}/{x}/{y}.pbf?layer=${layerType}`;
                cadastralVectorMapLayer = L.vectorGrid.protobuf(
                    vectorTileUrl('map'),
                    vectorOptions
                );
                cadastralVectorPleLayer = L.vectorGrid.protobuf(
                    vectorTileUrl('ple'),
                    { ...vectorOptions, minZoom: 16, vectorTileLayerStyles: {
                        cadastral: { weight: 1, color: '#1f7a8c', opacity: 0.9, fill: false }
                    }}
                );
                cadastralVectorMapLayer.on('tileerror', () => switchToRasterFallback(map));
                cadastralVectorPleLayer.on('tileerror', () => switchToRasterFallback(map));
            } else {
                switchToRasterFallback(map);
            }
        }

        cadastralBoundaryActive = !cadastralBoundaryActive;
        if (cadastralBoundaryActive) {
            (cadastralVectorMapLayer || cadastralMapLayer).addTo(map);
            (cadastralVectorPleLayer || cadastralPleLayer).addTo(map);
            map.on('click', _onCadastralBoundaryClick);
            _refreshViewportParcelLayer();
        } else {
            [cadastralVectorMapLayer, cadastralVectorPleLayer, cadastralMapLayer, cadastralPleLayer]
                .forEach(layer => { if (layer && map.hasLayer(layer)) map.removeLayer(layer); });
            map.off('click', _onCadastralBoundaryClick);
            viewportParcelRequestToken += 1;
            if (viewportParcelAbortController) viewportParcelAbortController.abort();
            viewportParcelAbortController = null;
            viewportParcelLastBoundsKey = '';
            _clearViewportParcelLayer(map);
        }
        if (btn) btn.classList.toggle('active', cadastralBoundaryActive);
    }

    // Boundary tiles are rasterized PNGs with no per-feature tooltip of
    // their own (unlike the vector "Italy Regions" layer, which is why
    // hovering there without this only ever showed "Region: <name>").
    // This does a live lookup instead, mirroring the same minZoom tiers
    // as the tile layers themselves.
    async function _onCadastralBoundaryClick(e) {
        const map = _getFoliumMap();
        if (!map) return;
        const zoom = map.getZoom();
        if (zoom < 13) return;
        const layer = zoom >= 16 ? 'ple' : 'map';
        const { lat, lng } = e.latlng;
        if (layer === 'ple' && await _lookupParcelAtPoint(lat, lng, map)) return;
        await _showLegacyCadastralPopup(lat, lng, layer, map);
    }

    // The map object and deferred Leaflet plugins are registered by separate
    // scripts.  Attach the viewport listener as soon as the map exists, and
    // wait briefly for VectorGrid so the default boundary layer can use MVT
    // before falling back to PNG tiles.
    function _initializeCadastralOverlays(attempt = 0) {
        const map = _getFoliumMap();
        if (!map) {
            if (attempt < 40) setTimeout(() => _initializeCadastralOverlays(attempt + 1), 250);
            return;
        }

        canonicalLayerMap = map;
        if (map.createPane && !map.getPane('canonicalMapLayerPane')) {
            map.createPane('canonicalMapLayerPane').style.zIndex = 425;
        }
        _loadCanonicalLayerCatalog();

        // Do not let Leaflet create a full vector-tile grid when the shared
        // PostGIS source is known to be unavailable. The old path generated
        // dozens of predictable 503s before switching to another endpoint
        // backed by the same unavailable service.
        if (attempt === 0) {
            _fetchJson('/api/v1/map/layers').then(catalog => {
                if (!catalog) {
                    _initializeCadastralOverlays(attempt + 1);
                    return;
                }
                if (catalog && catalog.available === false) {
                    const button = document.getElementById('toggleEnrichmentCadastral');
                    if (button) {
                        button.disabled = true;
                        button.title = 'Cadastral boundary service is not configured';
                    }
                    const status = document.getElementById('canonicalMapLayers');
                    if (status) status.insertAdjacentHTML('beforeend', '<span class="enrichment-legend-meta">Cadastral boundaries unavailable</span>');
                    return;
                }
                _attachViewportParcelLoader(map);
                const vectorGridReady = L.vectorGrid && typeof L.vectorGrid.protobuf === 'function';
                if (!cadastralBoundaryActive && (vectorGridReady || attempt >= 40)) toggleCadastralBoundaryLayer();
            });
            return;
        }

        _attachViewportParcelLoader(map);
        const vectorGridReady = L.vectorGrid && typeof L.vectorGrid.protobuf === 'function';
        if (!cadastralBoundaryActive && (vectorGridReady || attempt >= 40)) {
            toggleCadastralBoundaryLayer();
            return;
        }
        if (!cadastralBoundaryActive) {
            setTimeout(() => _initializeCadastralOverlays(attempt + 1), 100);
        }
    }

    _initializeCadastralOverlays();

    // Restore a shared parcel URL after the Folium map has registered itself.
    (function _restoreParcelFromUrl() {
        const reference = new URL(window.location.href).searchParams.get('parcel');
        if (!reference) return;
        let attempts = 0;
        async function restore() {
            const map = _getFoliumMap();
            if (!map) {
                if (++attempts < 40) setTimeout(restore, 250);
                return;
            }
            try {
                const response = await fetch(`/api/v1/enrichment/parcel/by-reference/${encodeURIComponent(reference)}`);
                if (!response.ok) return;
                const feature = await response.json();
                if (_selectParcelFeature(feature, map) && cadastralSelectionLayer.getBounds) {
                    const bounds = cadastralSelectionLayer.getBounds();
                    if (bounds.isValid()) map.fitBounds(bounds, { padding: [80, 80], maxZoom: 18 });
                }
            } catch (error) {
                console.warn('[EnrichmentLayers] Shared parcel restore failed', error);
            }
        }
        restore();
    })();

    window.togglePoiLayer = togglePoiLayer;
    window.toggleFiresLayer = toggleFiresLayer;
    window.refreshFiresLayer = function () { if (firesActive) _refreshFiresLayer(); };
    window.toggleBulletinLayer = toggleBulletinLayer;
    window.refreshBulletinLayer = function () { if (bulletinActive) _refreshBulletinLayer(); };
    window.toggleCadastralBoundaryLayer = toggleCadastralBoundaryLayer;
    window.toggleCanonicalMapLayer = toggleCanonicalMapLayer;
    window.setCanonicalMapLayerOpacity = setCanonicalMapLayerOpacity;
    window.refreshViewportParcelLayer = _refreshViewportParcelLayer;
    window.clearCadastralParcelSelection = _clearCadastralParcelSelection;
})();
