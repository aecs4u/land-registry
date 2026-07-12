// ========================================
// PARCEL ENRICHMENT PANEL
// ========================================
// Fetches /api/v1/enrichment/* data for the clicked parcel and renders it as
// a stack of collapsible cards appended into the existing #parcelInfoPanel
// (built by showParcelInfo() in map.js). Every section degrades gracefully
// when a store/API isn't available on the server — this mirrors the backend
// contract in land_registry/stats_service.py.

(function () {
    const SECTIONS_CONTAINER_ID = 'parcelEnrichmentSections';

    /**
     * Parse the catasto comune code from a WFS INSPIRE parcel feature.
     * NATIONALCADASTRALREFERENCE format: {COMUNE}_{FOGLIO_PADDED}.{PARTICELLA}[/{SUB}]
     * e.g. "C773_0020.846" -> "C773"
     */
    function _cadastralCodeFromFeature(feature) {
        const props = (feature && feature.properties) || {};
        const ref = props.NATIONALCADASTRALREFERENCE || props.nationalcadastralreference;
        if (ref && typeof ref === 'string' && ref.includes('_')) {
            return ref.split('_')[0].trim().toUpperCase();
        }
        return null;
    }

    /** Best-effort centroid for a clicked layer, for POI/fires radius queries. */
    function _layerCentroid(layer) {
        try {
            const bounds = layer.getBounds ? layer.getBounds() : null;
            if (bounds && bounds.isValid()) {
                const c = bounds.getCenter();
                return { lat: c.lat, lng: c.lng };
            }
        } catch (e) { /* not a polygon layer */ }
        return null;
    }

    async function _fetchJson(url) {
        try {
            const resp = await fetch(url);
            if (!resp.ok) return { ok: false, status: resp.status, data: null };
            return { ok: true, status: resp.status, data: await resp.json() };
        } catch (e) {
            return { ok: false, status: 0, data: null };
        }
    }

    function _card(icon, title, bodyHtml) {
        return `
            <div class="card enrichment-card mb-2">
                <div class="card-header enrichment-card-header">
                    <i class="fa-solid fa-${icon}"></i> ${title}
                </div>
                <div class="card-body enrichment-card-body">${bodyHtml}</div>
            </div>`;
    }

    function _loadingCard(icon, title) {
        return _card(icon, title, `
            <div class="enrichment-loading">
                <span class="spinner-border spinner-border-sm" role="status"></span>
                <span>${window._i18n && window._i18n['Loading...'] || 'Loading...'}</span>
            </div>`);
    }

    function _emptyState(message) {
        return `<div class="enrichment-empty text-muted">${message}</div>`;
    }

    function _sourceFootnote(source) {
        return source ? `<div class="enrichment-source">${source}</div>` : '';
    }

    // ---- Section renderers -------------------------------------------------

    function _renderMunicipality(data) {
        if (!data) return _emptyState('Nessun dato comunale disponibile.');
        const pop = data.population;
        return `
            <div class="enrichment-row"><span>Comune</span><strong>${data.name || '—'}</strong></div>
            <div class="enrichment-row"><span>Provincia</span><strong>${data.province || '—'} (${data.province_sigla || '—'})</strong></div>
            <div class="enrichment-row"><span>Regione</span><strong>${data.region || '—'}</strong></div>
            <div class="enrichment-row"><span>CAP</span><strong>${data.postal_code || '—'}</strong></div>
            ${pop ? `<div class="enrichment-row"><span>Popolazione (${pop.year})</span><strong>${(pop.resident_population || 0).toLocaleString('it-IT')}</strong></div>` : ''}
            ${_sourceFootnote(data.source)}
        `;
    }

    function _renderOmi(data) {
        if (!data || !data.quotes || data.quotes.length === 0) {
            return _emptyState('Nessuna quotazione OMI disponibile per questo comune.');
        }
        const rows = data.quotes.slice(0, 8).map(q => `
            <div class="enrichment-omi-row">
                <div class="enrichment-omi-zone">Zona ${q.zona || '—'} · ${q.tipologia || '—'}</div>
                <div class="enrichment-omi-values">
                    <span title="Compravendita €/m²">${q.prezzo_min ?? '—'}–${q.prezzo_max ?? '—'} €/m²</span>
                    ${q.locazione_min ? `<span title="Locazione €/m²/mese">${q.locazione_min}–${q.locazione_max} €/m²/mese</span>` : ''}
                </div>
            </div>`).join('');
        return rows + _sourceFootnote(data.source);
    }

    function _renderIncome(data) {
        if (!data) return _emptyState('Nessun dato IRPEF disponibile per questo comune.');
        const brackets = (data.income_distribution || []).map(b => `
            <div class="enrichment-bracket-row">
                <span class="enrichment-bracket-label">${b.bracket}</span>
                <div class="enrichment-bracket-bar-track">
                    <div class="enrichment-bracket-bar" style="width:${Math.min(b.pct || 0, 100)}%"></div>
                </div>
                <span class="enrichment-bracket-pct">${b.pct != null ? b.pct + '%' : '—'}</span>
            </div>`).join('');
        return `
            <div class="enrichment-row"><span>Contribuenti</span><strong>${(data.taxpayers || 0).toLocaleString('it-IT')}</strong></div>
            <div class="enrichment-row"><span>Reddito medio</span><strong>€ ${data.mean_taxable_income_eur != null ? Math.round(data.mean_taxable_income_eur).toLocaleString('it-IT') : '—'}</strong></div>
            <div class="enrichment-brackets">${brackets}</div>
            ${_sourceFootnote(data.source)}
        `;
    }

    function _seismicLabel(zone) {
        const labels = { 1: 'Zona 1 — alta sismicità', 2: 'Zona 2', 3: 'Zona 3', 4: 'Zona 4 — bassa sismicità' };
        return labels[zone] || `Zona ${zone}`;
    }

    function _riskBadgeClass(pct) {
        if (pct == null) return 'bg-secondary';
        if (pct >= 5) return 'bg-danger';
        if (pct >= 1) return 'bg-warning text-dark';
        return 'bg-success';
    }

    function _renderRisks(data) {
        if (!data || (!data.seismic && !data.hydrogeological)) {
            return _emptyState('Nessun dato di rischio disponibile per questo comune.');
        }
        let html = '';
        if (data.seismic) {
            html += `<div class="enrichment-row"><span>Rischio sismico</span><strong>${_seismicLabel(data.seismic.zone)}</strong></div>`;
        }
        const hg = data.hydrogeological;
        if (hg) {
            const floodP3 = hg.flood && hg.flood.area_pct ? hg.flood.area_pct.P3_high_probability : null;
            const landslideP4 = hg.landslide && hg.landslide.area_pct ? hg.landslide.area_pct.P4_very_high : null;
            html += `
                <div class="enrichment-row">
                    <span>Rischio alluvione (P3)</span>
                    <span class="badge ${_riskBadgeClass(floodP3)}">${floodP3 != null ? floodP3 + '% area' : '—'}</span>
                </div>
                <div class="enrichment-row">
                    <span>Rischio frana (P4)</span>
                    <span class="badge ${_riskBadgeClass(landslideP4)}">${landslideP4 != null ? landslideP4 + '% area' : '—'}</span>
                </div>`;
        }
        return html + _sourceFootnote('ISPRA IdroGEO / DPC via aecs4u-stats');
    }

    function _renderPois(data) {
        if (!data || !data.total) return _emptyState('Nessun punto di interesse trovato entro 1 km.');
        const entries = Object.entries(data.categories || {})
            .filter(([, list]) => list.length > 0)
            .sort((a, b) => b[1].length - a[1].length);
        if (entries.length === 0) return _emptyState('Nessun punto di interesse trovato entro 1 km.');
        const rows = entries.map(([cat, list]) => `
            <div class="enrichment-row"><span>${cat}</span><strong>${list.length}</strong></div>
        `).join('');
        return rows + _sourceFootnote(data.source);
    }

    // ---- Orchestration -------------------------------------------------

    async function renderParcelEnrichment(feature, layer) {
        const content = document.getElementById('parcelInfoContent');
        if (!content) return;

        const cadastralCode = _cadastralCodeFromFeature(feature);
        const centroid = _layerCentroid(layer);

        let container = document.getElementById(SECTIONS_CONTAINER_ID);
        if (!container) {
            container = document.createElement('div');
            container.id = SECTIONS_CONTAINER_ID;
            content.appendChild(container);
        }

        if (!cadastralCode) {
            container.innerHTML = `<div class="enrichment-empty text-muted">Codice catastale del comune non disponibile per questa particella (manca NATIONALCADASTRALREFERENCE).</div>`;
            return;
        }

        // Scaffold loading state immediately, then fill sections as they resolve.
        container.innerHTML = [
            _loadingCard('location-dot', 'Comune'),
            _loadingCard('chart-line', 'Quotazioni OMI'),
            _loadingCard('coins', 'Reddito IRPEF'),
            _loadingCard('triangle-exclamation', 'Rischi ambientali'),
            centroid ? _loadingCard('map-pin', 'Punti di interesse') : '',
        ].join('');
        const [muniEl, omiEl, incomeEl, riskEl, poiEl] = container.querySelectorAll('.enrichment-card');

        // Municipality first — we need its istat_code for the risks lookup.
        const muniResult = await _fetchJson(`/api/v1/enrichment/municipality/${encodeURIComponent(cadastralCode)}`);
        const muniData = muniResult.ok ? muniResult.data : null;
        if (muniEl) muniEl.querySelector('.enrichment-card-body').innerHTML = _renderMunicipality(muniData);

        const istatCode = muniData ? muniData.istat_code : null;

        const tasks = [
            _fetchJson(`/api/v1/enrichment/omi/quotes?comune=${encodeURIComponent(cadastralCode)}`)
                .then(r => { if (omiEl) omiEl.querySelector('.enrichment-card-body').innerHTML = _renderOmi(r.ok ? r.data : null); }),
            _fetchJson(`/api/v1/enrichment/income/${encodeURIComponent(cadastralCode)}`)
                .then(r => { if (incomeEl) incomeEl.querySelector('.enrichment-card-body').innerHTML = _renderIncome(r.ok ? r.data : null); }),
            istatCode
                ? _fetchJson(`/api/v1/enrichment/risks/${encodeURIComponent(istatCode)}`)
                    .then(r => { if (riskEl) riskEl.querySelector('.enrichment-card-body').innerHTML = _renderRisks(r.ok ? r.data : null); })
                : Promise.resolve().then(() => { if (riskEl) riskEl.querySelector('.enrichment-card-body').innerHTML = _emptyState('Comune non identificato: impossibile recuperare i rischi.'); }),
        ];
        if (centroid && poiEl) {
            tasks.push(
                _fetchJson(`/api/v1/enrichment/pois/?lat=${centroid.lat}&lng=${centroid.lng}&radius_km=1`)
                    .then(r => { poiEl.querySelector('.enrichment-card-body').innerHTML = _renderPois(r.ok ? r.data : null); })
            );
        }
        await Promise.allSettled(tasks);
    }

    window.renderParcelEnrichment = renderParcelEnrichment;
})();
