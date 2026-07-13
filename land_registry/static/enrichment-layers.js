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
    let cadastralBoundaryActive = false;
    let cadastralSelectionLayer = null;
    let cadastralLookupToken = 0;

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
            if (rows.length) L.popup().setLatLng([lat, lng]).setContent(rows.join('<br>')).openOn(map);
        } catch (error) {
            console.warn('[EnrichmentLayers] Cadastral identify failed', error);
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
            cadastralMapLayer = L.tileLayer(
                '/api/v1/tiles/cadastral-boundaries/{z}/{x}/{y}.png?layer=map',
                { pane, minZoom: 13, maxZoom: 22, attribution: 'Cadastral data' }
            );
            cadastralPleLayer = L.tileLayer(
                '/api/v1/tiles/cadastral-boundaries/{z}/{x}/{y}.png?layer=ple',
                { pane, minZoom: 16, maxZoom: 22 }
            );
        }

        cadastralBoundaryActive = !cadastralBoundaryActive;
        if (cadastralBoundaryActive) {
            cadastralMapLayer.addTo(map);
            cadastralPleLayer.addTo(map);
            map.on('click', _onCadastralBoundaryClick);
        } else {
            map.removeLayer(cadastralMapLayer);
            map.removeLayer(cadastralPleLayer);
            map.off('click', _onCadastralBoundaryClick);
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
    window.clearCadastralParcelSelection = _clearCadastralParcelSelection;
})();
