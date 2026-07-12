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

    window.togglePoiLayer = togglePoiLayer;
    window.toggleFiresLayer = toggleFiresLayer;
    window.refreshFiresLayer = function () { if (firesActive) _refreshFiresLayer(); };
})();
