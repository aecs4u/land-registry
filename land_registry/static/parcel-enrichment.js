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
    const INDICATOR_PREVIEW_LIMIT = 4;
    let activeRenderToken = 0;
    let omiHistoryToken = 0;

    function _escapeHtml(value) {
        return String(value ?? '').replace(/[&<>'"]/g, (char) => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
        })[char]);
    }

    function _formatNumber(value, maximumFractionDigits = 0) {
        if (value === null || value === undefined || value === '') return '—';
        const number = Number(value);
        return Number.isFinite(number)
            ? number.toLocaleString('it-IT', { maximumFractionDigits })
            : '—';
    }

    function _formatPercent(value) {
        if (value === null || value === undefined || value === '') return '—';
        const number = Number(value);
        return Number.isFinite(number) ? `${(number * 100).toLocaleString('it-IT', { maximumFractionDigits: 1 })}%` : '—';
    }

    function _indicatorLabel(code) {
        return String(code || '')
            .replace(/[_-]+/g, ' ')
            .replace(/\s+/g, ' ')
            .trim()
            .toLowerCase()
            .replace(/(^|\s)\p{L}/gu, (letter) => letter.toUpperCase());
    }

    /**
     * Parse the catasto comune code from a WFS INSPIRE parcel feature.
     * NATIONALCADASTRALREFERENCE format: {COMUNE}_{FOGLIO_PADDED}.{PARTICELLA}[/{SUB}]
     * e.g. "C773_0020.846" -> "C773"
     */
    function _cadastralCodeFromFeature(feature) {
        const props = (feature && feature.properties) || {};
        const ref = props.NATIONALCADASTRALREFERENCE
            || props.nationalcadastralreference
            || props.national_cadastral_reference
            || props.national_reference;
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

    async function _postJson(url, payload) {
        try {
            const resp = await fetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
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

    function _parcelAreaSqm(feature) {
        const props = (feature && feature.properties) || {};
        for (const key of ['area_sqm', 'area_m2']) {
            const value = Number(props[key]);
            if (Number.isFinite(value) && value > 0) return value;
        }
        const hectares = Number(props.area_ha);
        return Number.isFinite(hectares) && hectares > 0 ? hectares * 10000 : null;
    }

    function _validOmiQuotes(data, zoneMatch) {
        const quotes = ((data && data.quotes) || []).filter((quote) => {
            const min = Number(quote.prezzo_min), max = Number(quote.prezzo_max);
            return Number.isFinite(min) && Number.isFinite(max) && min >= 0 && max >= min;
        });
        const preferredZone = zoneMatch && zoneMatch.matched && String(zoneMatch.zone || '').toUpperCase();
        if (!preferredZone) return quotes;
        return quotes
            .map((quote, index) => ({ quote, index }))
            .sort((a, b) => {
                const aMatch = String(a.quote.zona || '').toUpperCase() === preferredZone ? 0 : 1;
                const bMatch = String(b.quote.zona || '').toUpperCase() === preferredZone ? 0 : 1;
                return aMatch - bMatch || a.index - b.index;
            })
            .map(item => item.quote);
    }

    function _renderOmi(data, feature, zoneMatch) {
        if (!data || !data.quotes || data.quotes.length === 0) {
            return _emptyState('Nessuna quotazione OMI disponibile per questo comune.');
        }
        const quotes = _validOmiQuotes(data, zoneMatch);
        if (!quotes.length) return _emptyState('Le quotazioni OMI disponibili non contengono intervalli di compravendita validi.');
        const area = _parcelAreaSqm(feature);
        const defaultArea = area && area <= 10000 ? Math.round(area) : '';
        const largeParcelNotice = area > 10000
            ? `<div class="enrichment-empty text-muted">La particella misura ${_formatNumber(area)} m²: inserire la superficie commerciale del fabbricato da stimare.</div>`
            : '';
        const detectedZone = zoneMatch && zoneMatch.matched && String(zoneMatch.zone || '').toUpperCase();
        const detectedZoneHasQuotes = detectedZone && quotes.some(
            quote => String(quote.zona || '').toUpperCase() === detectedZone
        );
        const zoneNotice = detectedZoneHasQuotes
            ? `<div class="omi-zone-match"><i class="fa-solid fa-location-crosshairs"></i><span>Zona OMI rilevata automaticamente</span><strong>${_escapeHtml(detectedZone)}</strong></div>`
            : detectedZone
                ? `<div class="omi-zone-match is-warning"><i class="fa-solid fa-triangle-exclamation"></i><span>Zona ${_escapeHtml(detectedZone)} rilevata, ma senza quotazioni disponibili: selezionare e verificare un'alternativa.</span></div>`
                : `<div class="enrichment-empty text-muted">Zona OMI non rilevata automaticamente: verificare la selezione.</div>`;
        const options = quotes.slice(0, 80).map((quote, index) => {
            const period = quote.anno && quote.semestre ? ` · ${quote.anno} S${quote.semestre}` : '';
            const state = quote.stato_conservazione ? ` · ${quote.stato_conservazione}` : '';
            return `<option value="${index}">Zona ${_escapeHtml(quote.zona || '—')} · ${_escapeHtml(quote.tipologia || quote.cod_tipologia || 'Tipologia')}${_escapeHtml(state + period)}</option>`;
        }).join('');
        return `
            ${zoneNotice}
            <div class="omi-controls">
                <label for="omiQuoteSelect">Zona e tipologia OMI</label>
                <select id="omiQuoteSelect" class="tool-select">${options}</select>
                <label for="omiEstimateArea">Superficie utilizzata nella stima (m²)</label>
                <input id="omiEstimateArea" class="tool-input" type="number" min="1" step="1" value="${defaultArea}" placeholder="es. 100">
            </div>
            ${largeParcelNotice}
            <div id="omiSelectedQuote" class="omi-selected-quote"></div>
            <div id="omiEstimateResult" class="omi-estimate"></div>
            <div class="enrichment-empty text-muted omi-disclaimer">Stima indicativa: superficie × intervallo OMI selezionato. Non è una perizia, non considera consistenza commerciale, stato reale o corretta zona OMI. Per fabbricati inserire la superficie commerciale, non necessariamente l'area della particella.</div>
            <div id="omiHistory" class="omi-history"><div class="enrichment-loading"><span class="spinner-border spinner-border-sm" role="status"></span><span>Caricamento storico…</span></div></div>
            ${_sourceFootnote(data.source)}
        `;
    }

    function _estimateOmiRange(quote, areaSqm) {
        const area = Number(areaSqm), minRate = Number(quote && quote.prezzo_min), maxRate = Number(quote && quote.prezzo_max);
        if (![area, minRate, maxRate].every(Number.isFinite) || area <= 0 || minRate < 0 || maxRate < minRate) return null;
        return { min: area * minRate, max: area * maxRate };
    }

    function _formatCurrency(value) {
        const number = Number(value);
        return Number.isFinite(number)
            ? number.toLocaleString('it-IT', { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 })
            : '—';
    }

    function _renderOmiHistory(data, selectedQuote) {
        const selectedState = selectedQuote && selectedQuote.stato_conservazione;
        const history = ((data && data.history) || []).filter((row) => {
            const min = Number(row.prezzo_min), max = Number(row.prezzo_max);
            const sameState = !selectedState || !row.stato_conservazione || row.stato_conservazione === selectedState;
            return sameState && Number.isFinite(min) && Number.isFinite(max);
        }).slice(-24);
        if (!history.length) return _emptyState('Storico OMI non disponibile per questa selezione.');

        const values = history.map((row) => (Number(row.prezzo_min) + Number(row.prezzo_max)) / 2);
        const minValue = Math.min(...values), maxValue = Math.max(...values);
        const width = 280, height = 86, padX = 8, padY = 10;
        const x = (index) => padX + (history.length === 1 ? 0 : index * (width - padX * 2) / (history.length - 1));
        const y = (value) => height - padY - (maxValue === minValue ? 0.5 : (value - minValue) / (maxValue - minValue)) * (height - padY * 2);
        const points = values.map((value, index) => `${x(index).toFixed(1)},${y(value).toFixed(1)}`).join(' ');
        const first = history[0], last = history[history.length - 1];
        const change = values[0] ? ((values[values.length - 1] - values[0]) / values[0]) * 100 : null;
        return `
            <div class="omi-history-heading">
                <strong>Storico compravendita</strong>
                <span>${history.length} semestri${change == null ? '' : ` · ${change >= 0 ? '+' : ''}${change.toLocaleString('it-IT', { maximumFractionDigits: 1 })}%`}</span>
            </div>
            <svg class="omi-history-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="Andamento del valore OMI medio">
                <polyline points="${points}" fill="none" stroke="currentColor" stroke-width="2.5" vector-effect="non-scaling-stroke"></polyline>
                <circle cx="${x(history.length - 1).toFixed(1)}" cy="${y(values[values.length - 1]).toFixed(1)}" r="3.5" fill="currentColor"></circle>
            </svg>
            <div class="omi-history-axis"><span>${_escapeHtml(`${first.anno} S${first.semestre}`)}</span><span>${_escapeHtml(`${last.anno} S${last.semestre}`)}</span></div>
            <div class="enrichment-row"><span>Ultimo intervallo</span><strong>${_formatNumber(last.prezzo_min)}–${_formatNumber(last.prezzo_max)} €/m²</strong></div>
        `;
    }

    function _bindOmiControls(cardBody, data, feature, cadastralCode, renderToken, zoneMatch) {
        const quotes = _validOmiQuotes(data, zoneMatch).slice(0, 80);
        const select = cardBody.querySelector('#omiQuoteSelect');
        const areaInput = cardBody.querySelector('#omiEstimateArea');
        const quoteEl = cardBody.querySelector('#omiSelectedQuote');
        const estimateEl = cardBody.querySelector('#omiEstimateResult');
        const historyEl = cardBody.querySelector('#omiHistory');
        if (!select || !areaInput || !quoteEl || !estimateEl || !historyEl || !quotes.length) return;
        let estimateTimer = null;
        let estimateRequestToken = 0;

        function selectedQuote() {
            return quotes[Number(select.value)] || quotes[0];
        }

        function scheduleServerEstimate(quote, area) {
            const token = ++estimateRequestToken;
            if (estimateTimer) clearTimeout(estimateTimer);
            if (!Number.isFinite(area) || area <= 0) return;
            estimateTimer = setTimeout(async () => {
                const result = await _postJson('/api/v1/enrichment/omi/estimate', {
                    comune: cadastralCode,
                    zona: quote.zona,
                    cod_tipologia: String(quote.cod_tipologia),
                    stato_conservazione: quote.stato_conservazione || null,
                    area_sqm: area,
                });
                if (token !== estimateRequestToken || renderToken !== activeRenderToken || !result.ok) return;
                const serverRange = result.data && result.data.value_range_eur;
                if (!serverRange) return;
                estimateEl.innerHTML = `
                    <span>Valore indicativo</span>
                    <strong>${_formatCurrency(serverRange.min)} – ${_formatCurrency(serverRange.max)}</strong>
                    <small class="omi-estimate-status"><i class="fa-solid fa-circle-check"></i> Calcolo verificato dal server</small>`;
            }, 250);
        }

        function updateEstimate() {
            const quote = selectedQuote();
            const range = _estimateOmiRange(quote, areaInput.value);
            quoteEl.innerHTML = `
                <div class="enrichment-row"><span>Compravendita</span><strong>${_formatNumber(quote.prezzo_min)}–${_formatNumber(quote.prezzo_max)} €/m²</strong></div>
                ${quote.locazione_min != null ? `<div class="enrichment-row"><span>Locazione</span><strong>${_formatNumber(quote.locazione_min, 2)}–${_formatNumber(quote.locazione_max, 2)} €/m²/mese</strong></div>` : ''}`;
            estimateEl.innerHTML = range
                ? `<span>Valore indicativo</span><strong>${_formatCurrency(range.min)} – ${_formatCurrency(range.max)}</strong><small class="omi-estimate-status">Anteprima locale</small>`
                : '<span>Inserire una superficie valida per calcolare la stima.</span>';
            scheduleServerEstimate(quote, Number(areaInput.value));
        }

        async function updateHistory() {
            const quote = selectedQuote();
            const token = ++omiHistoryToken;
            historyEl.innerHTML = '<div class="enrichment-loading"><span class="spinner-border spinner-border-sm" role="status"></span><span>Caricamento storico…</span></div>';
            const params = new URLSearchParams({ comune: cadastralCode, zona: quote.zona || '' });
            if (quote.cod_tipologia != null) params.set('cod_tipologia', quote.cod_tipologia);
            const result = await _fetchJson(`/api/v1/enrichment/omi/history?${params}`);
            if (token !== omiHistoryToken || renderToken !== activeRenderToken) return;
            historyEl.innerHTML = _renderOmiHistory(result.ok ? result.data : null, quote);
        }

        select.addEventListener('change', () => { updateEstimate(); updateHistory(); });
        areaInput.addEventListener('input', updateEstimate);
        updateEstimate();
        updateHistory();
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

    function _renderCensus(feature) {
        const props = feature && feature.properties;
        if (!props) return _emptyState('Sezione di censimento non disponibile per questa particella.');
        const ratios = props.ratios || {};
        const population = props.p1 ?? props.pop21;
        const households = props.pf1 ?? props.fam21;
        const dwellings = props.a8 ?? props.abi21;
        const buildings = props.e3 ?? props.edi21;
        return `
            <div class="enrichment-row"><span>Sezione 2021</span><strong>${_escapeHtml(props.sez21_id || '—')}</strong></div>
            <div class="enrichment-row"><span>Residenti</span><strong>${_formatNumber(population)}</strong></div>
            <div class="enrichment-row"><span>Famiglie</span><strong>${_formatNumber(households)}</strong></div>
            <div class="enrichment-row"><span>Abitazioni</span><strong>${_formatNumber(dwellings)}</strong></div>
            <div class="enrichment-row"><span>Edifici residenziali</span><strong>${_formatNumber(buildings)}</strong></div>
            <div class="enrichment-row"><span>Occupazione 15–64</span><strong>${_formatPercent(ratios.employment_rate_working_age)}</strong></div>
            <div class="enrichment-row"><span>Istruzione terziaria</span><strong>${_formatPercent(ratios.education_tertiary_rate)}</strong></div>
            <div class="enrichment-row"><span>Residenti stranieri</span><strong>${_formatPercent(ratios.foreign_resident_share)}</strong></div>
            <div class="enrichment-row"><span>Abitazioni non occupate</span><strong>${_formatPercent(ratios.vacancy_rate)}</strong></div>
            <div class="enrichment-row"><span>Componenti per famiglia</span><strong>${_formatNumber(ratios.avg_household_size, 2)}</strong></div>
            ${_sourceFootnote('ISTAT Basi Territoriali 2021 via aecs4u-stats')}
        `;
    }

    function _renderCrime(data) {
        if (!data) return _emptyState('Dati sulla sicurezza non disponibili.');
        return `
            <div class="enrichment-row"><span>Ambito territoriale</span><strong>${_escapeHtml(data.province || data.nuts3 || 'Provincia')}</strong></div>
            <div class="enrichment-row"><span>Anno</span><strong>${_escapeHtml(data.year || '—')}</strong></div>
            <div class="enrichment-row"><span>Delitti denunciati</span><strong>${_formatNumber(data.total_crimes)}</strong></div>
            <div class="enrichment-row"><span>Tipologie rilevate</span><strong>${_formatNumber(data.crime_types)}</strong></div>
            <div class="enrichment-empty text-muted">Dato aggregato a livello provinciale, non riferito alla singola particella.</div>
            ${_sourceFootnote(data.source)}
        `;
    }

    async function _fetchIndicatorPreview(catalogUrl, seriesBaseUrl) {
        const catalogResult = await _fetchJson(catalogUrl);
        if (!catalogResult.ok || !catalogResult.data || !catalogResult.data.indicators) return null;
        const codes = catalogResult.data.indicators.slice(0, INDICATOR_PREVIEW_LIMIT);
        const series = await Promise.all(codes.map(async (code) => {
            const result = await _fetchJson(`${seriesBaseUrl}/${encodeURIComponent(code)}`);
            const values = result.ok && result.data ? result.data.series || [] : [];
            return { code, latest: values.length ? values[values.length - 1] : null };
        }));
        return {
            source: catalogResult.data.source,
            nuts3: catalogResult.data.nuts3,
            total: catalogResult.data.indicators.length,
            series,
        };
    }

    function _renderIndicatorPreview(data, emptyMessage) {
        if (!data || !data.series || data.series.every((item) => !item.latest)) {
            return _emptyState(emptyMessage);
        }
        const rows = data.series.filter((item) => item.latest).map((item) => `
            <div class="enrichment-row">
                <span title="${_escapeHtml(item.code)}">${_escapeHtml(_indicatorLabel(item.code))}</span>
                <strong>${_formatNumber(item.latest.value, 2)} <small>${_escapeHtml(item.latest.year || '')}</small></strong>
            </div>`).join('');
        return `
            ${rows}
            ${data.total > data.series.length ? `<div class="enrichment-empty text-muted">Anteprima di ${data.series.length} indicatori su ${data.total} disponibili.</div>` : ''}
            <div class="enrichment-empty text-muted">Indicatori a livello provinciale (${_escapeHtml(data.nuts3 || 'NUTS3')}).</div>
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

    function _renderFires(data) {
        if (!data || !data.count) return _emptyState('Nessun incendio attivo rilevato nelle vicinanze (25 km).');
        const sorted = (data.detections || []).slice().sort((a, b) => {
            const da = `${a.acq_date || ''}${a.acq_time || ''}`;
            const db = `${b.acq_date || ''}${b.acq_time || ''}`;
            return db.localeCompare(da);
        });
        const rows = sorted.slice(0, 5).map(d => `
            <div class="enrichment-row">
                <span>${d.acq_date || '—'}${d.acq_time ? ' · ' + d.acq_time.slice(0, 2) + ':' + d.acq_time.slice(2) : ''}</span>
                <strong title="Potenza radiativa del fuoco">${d.frp != null ? d.frp + ' MW' : '—'}</strong>
            </div>`).join('');
        return `
            <div class="enrichment-row"><span>Rilevamenti (25 km)</span><strong>${data.count}</strong></div>
            ${rows}
            ${data.count > 5 ? `<div class="enrichment-empty text-muted">+ altri ${data.count - 5}</div>` : ''}
            ${_sourceFootnote(data.source)}
        `;
    }

    /** Map a DPC bulletin risk description to a Bootstrap badge class + short label. */
    function _bulletinSeverity(description) {
        const text = (description || '').toUpperCase();
        if (text.includes('ROSSA')) return { cls: 'bg-danger', label: 'ALLERTA ROSSA' };
        if (text.includes('ARANCIONE')) return { cls: 'bg-warning text-dark', label: 'ALLERTA ARANCIONE' };
        if (text.includes('GIALLA')) return { cls: 'bg-warning text-dark', label: 'ALLERTA GIALLA' };
        return { cls: 'bg-success', label: 'Nessuna allerta' };
    }

    /** Fold accents/case for loose comune-name matching ("Sant'Egidio" ~ "sant egidio"). */
    function _foldName(name) {
        return name.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
    }

    /** Find the bulletin zone whose Comuni list contains muniName (accent/case-insensitive). */
    function _findBulletinZone(bulletinData, muniName) {
        if (!bulletinData || !muniName) return null;
        const zones = bulletinData.today_zones;
        if (!zones || !zones.objects) return null;
        const target = _foldName(muniName);
        for (const obj of Object.values(zones.objects)) {
            for (const geom of (obj.geometries || [])) {
                const comuni = (geom.properties && geom.properties['Comuni']) || [];
                const match = comuni.some(c => _foldName(c) === target);
                if (match) return geom.properties;
            }
        }
        return null;
    }

    function _renderBulletin(bulletinData, muniName) {
        if (!bulletinData) return _emptyState('Bollettino Protezione Civile non disponibile.');
        const zone = _findBulletinZone(bulletinData, muniName);
        if (!zone) return _emptyState('Comune non trovato nel bollettino odierno.');
        const risks = [
            ['Idraulico', zone['Per rischio idraulico']],
            ['Temporali', zone['Per rischio temporali']],
            ['Idrogeologico', zone['Per rischio idrogeologico']],
        ];
        const rows = risks.map(([label, desc]) => {
            const sev = _bulletinSeverity(desc);
            return `<div class="enrichment-row"><span>${label}</span><span class="badge ${sev.cls}">${sev.label}</span></div>`;
        }).join('');
        return `
            <div class="enrichment-row"><span>Zona</span><strong>${zone['Nome zona'] || '—'}</strong></div>
            ${rows}
            ${_sourceFootnote(bulletinData.source)}
        `;
    }

    // ---- Orchestration -------------------------------------------------

    async function renderParcelEnrichment(feature, layer) {
        const renderToken = ++activeRenderToken;
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
            _loadingCard('people-group', 'Censimento 2021'),
            _loadingCard('shield-halved', 'Sicurezza'),
            _loadingCard('users', 'Indicatori demografici'),
            _loadingCard('leaf', 'Qualità della vita'),
            _loadingCard('triangle-exclamation', 'Rischi ambientali'),
            _loadingCard('bullhorn', 'Bollettino di criticità'),
            centroid ? _loadingCard('map-pin', 'Punti di interesse') : '',
            centroid ? _loadingCard('fire', 'Incendi attivi') : '',
        ].join('');
        const [muniEl, omiEl, incomeEl, censusEl, crimeEl, demographicsEl, qualityEl,
            riskEl, bulletinEl, poiEl, firesEl] = container.querySelectorAll('.enrichment-card');

        // Municipality first — we need its istat_code for the risks lookup and
        // its name for the bulletin's comune-to-zone lookup.
        const muniResult = await _fetchJson(`/api/v1/enrichment/municipality/${encodeURIComponent(cadastralCode)}`);
        if (renderToken !== activeRenderToken) return;
        const muniData = muniResult.ok ? muniResult.data : null;
        if (muniEl) muniEl.querySelector('.enrichment-card-body').innerHTML = _renderMunicipality(muniData);

        const istatCode = muniData ? muniData.istat_code : null;

        const tasks = [
            Promise.all([
                _fetchJson(`/api/v1/enrichment/omi/quotes?comune=${encodeURIComponent(cadastralCode)}`),
                centroid && muniData && muniData.province
                    ? _fetchJson(`/api/v1/enrichment/omi/at-point?${new URLSearchParams({
                        province: muniData.province,
                        lat: centroid.lat,
                        lng: centroid.lng,
                    })}`)
                    : Promise.resolve({ ok: false, data: null }),
            ]).then(([r, zoneResult]) => {
                    if (!omiEl || renderToken !== activeRenderToken) return;
                    const omiData = r.ok ? r.data : null;
                    const zoneMatch = zoneResult.ok ? zoneResult.data : null;
                    const body = omiEl.querySelector('.enrichment-card-body');
                    body.innerHTML = _renderOmi(omiData, feature, zoneMatch);
                    if (omiData) _bindOmiControls(body, omiData, feature, cadastralCode, renderToken, zoneMatch);
                }),
            _fetchJson(`/api/v1/enrichment/income/${encodeURIComponent(cadastralCode)}`)
                .then(r => { if (incomeEl && renderToken === activeRenderToken) incomeEl.querySelector('.enrichment-card-body').innerHTML = _renderIncome(r.ok ? r.data : null); }),
            _fetchJson(`/api/v1/enrichment/crime/${encodeURIComponent(cadastralCode)}`)
                .then(r => { if (crimeEl && renderToken === activeRenderToken) crimeEl.querySelector('.enrichment-card-body').innerHTML = _renderCrime(r.ok ? r.data : null); }),
            _fetchIndicatorPreview(
                `/api/v1/enrichment/demographics/${encodeURIComponent(cadastralCode)}`,
                `/api/v1/enrichment/demographics/${encodeURIComponent(cadastralCode)}`
            ).then(data => {
                if (demographicsEl && renderToken === activeRenderToken) {
                    demographicsEl.querySelector('.enrichment-card-body').innerHTML = _renderIndicatorPreview(data, 'Indicatori demografici non disponibili.');
                }
            }),
            _fetchIndicatorPreview(
                `/api/v1/enrichment/quality-of-life/${encodeURIComponent(cadastralCode)}`,
                `/api/v1/enrichment/quality-of-life/${encodeURIComponent(cadastralCode)}`
            ).then(data => {
                if (qualityEl && renderToken === activeRenderToken) {
                    qualityEl.querySelector('.enrichment-card-body').innerHTML = _renderIndicatorPreview(data, 'Indicatori di qualità della vita non disponibili.');
                }
            }),
            istatCode
                ? _fetchJson(`/api/v1/enrichment/risks/${encodeURIComponent(istatCode)}`)
                    .then(r => { if (riskEl && renderToken === activeRenderToken) riskEl.querySelector('.enrichment-card-body').innerHTML = _renderRisks(r.ok ? r.data : null); })
                : Promise.resolve().then(() => { if (riskEl && renderToken === activeRenderToken) riskEl.querySelector('.enrichment-card-body').innerHTML = _emptyState('Comune non identificato: impossibile recuperare i rischi.'); }),
            _fetchJson('/api/v1/enrichment/bulletin')
                .then(r => { if (bulletinEl && renderToken === activeRenderToken) bulletinEl.querySelector('.enrichment-card-body').innerHTML = _renderBulletin(r.ok ? r.data : null, muniData ? muniData.name : null); }),
        ];
        if (centroid && censusEl) {
            tasks.push(
                _fetchJson(`/api/v1/enrichment/census/at-point?lat=${centroid.lat}&lng=${centroid.lng}`)
                    .then(r => {
                        if (renderToken === activeRenderToken) censusEl.querySelector('.enrichment-card-body').innerHTML = _renderCensus(r.ok ? r.data : null);
                    })
            );
        } else if (censusEl) {
            censusEl.querySelector('.enrichment-card-body').innerHTML = _emptyState('Geometria della particella non disponibile per individuare la sezione di censimento.');
        }
        if (centroid && poiEl) {
            tasks.push(
                _fetchJson(`/api/v1/enrichment/pois/?lat=${centroid.lat}&lng=${centroid.lng}&radius_km=1`)
                    .then(r => { if (renderToken === activeRenderToken) poiEl.querySelector('.enrichment-card-body').innerHTML = _renderPois(r.ok ? r.data : null); })
            );
        }
        if (centroid && firesEl) {
            tasks.push(
                _fetchJson(`/api/v1/enrichment/fires?lat=${centroid.lat}&lng=${centroid.lng}&radius_km=25`)
                    .then(r => { if (renderToken === activeRenderToken) firesEl.querySelector('.enrichment-card-body').innerHTML = _renderFires(r.ok ? r.data : null); })
            );
        }
        await Promise.allSettled(tasks);
    }

    window.renderParcelEnrichment = renderParcelEnrichment;
})();
