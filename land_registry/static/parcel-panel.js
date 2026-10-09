/* Parcel panel: the side sheet shown when a parcel is selected on /map.
 *
 * A section registry drives everything. Each section declares the tab it
 * belongs to, how to load its data and how to render it. Sections load
 * lazily (when they approach the viewport) and independently, so one slow or
 * failing source never blanks the panel. Section ``render`` functions are
 * DOM-free (they return HTML strings) so they can be tested under Node; only
 * mount/show/bind touch the DOM.
 *
 * Data comes from the existing /api/v1/enrichment endpoints. The parcel
 * read model (/parcel/details/{ref}, slow on a cold build) is requested by the
 * host page and handed over with setReadModel(); it feeds provenance and the
 * coverage section but no other section waits for it.
 */
(function (root, factory) {
  'use strict';
  const core = (typeof module === 'object' && module.exports)
    ? require('./parcel-panel-core.js')
    : root.ParcelPanelCore;
  const api = factory(core, root);
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.ParcelPanel = api;
}(typeof self !== 'undefined' ? self : this, function (Core, root) {
  'use strict';

  const API = '/api/v1/enrichment';
  const REQUEST_TIMEOUT_MS = 12000;
  const READ_MODEL_WAIT_MS = 30000;
  const LAZY_ROOT_MARGIN = '320px 0px';
  const INDICATOR_PREVIEW_LIMIT = 4;
  const PVP_RECORD_LIMIT = 8;
  const FIRE_RECORD_LIMIT = 5;
  const FIRE_RADIUS_KM = 25;
  const POI_RADIUS_KM = 1;
  const BENCHMARKS = {
    population_density_per_km2: { label: 'Italy', value: 196, unit: 'residents/km²', year: 2021 },
    average_income_eur: { label: 'Italy', value: 23000, unit: '€/taxpayer', year: 2022 },
  };
  const TABS = [
    { id: 'value', title: 'Value' },
    { id: 'property', title: 'Property' },
    { id: 'territory', title: 'Territory' },
    { id: 'context', title: 'Context' },
    { id: 'data', title: 'Data' },
  ];
  // Block names shown in the coverage section, in display order.
  const COVERAGE_BLOCKS = [
    ['basic', 'Parcel identity'], ['cadastral', 'Cadastre and postcode'], ['address', 'Main address'],
    ['risk', 'Risks'], ['subsidence', 'Subsidence'], ['terrain', 'Terrain'], ['population', 'Modelled population'],
    ['buildings', 'Buildings'], ['economics', 'Economy'], ['demographics', 'ISTAT demographics'],
    ['land_cover', 'Land cover'], ['valuation', 'OMI valuation'], ['valuation_history', 'OMI history'],
    ['coastal_erosion', 'Coastal erosion'], ['cultural_heritage', 'Cultural heritage'], ['solar', 'Solar potential'],
    ['poi', 'Points of interest'], ['nightlights', 'Night lights'], ['opendata', 'Cadastral OpenData'], ['pvp', 'PVP auctions'],
  ];

  const esc = Core.escapeHtml;

  // ---- Small view helpers (pure) ---------------------------------------------

  function rows(items) {
    const body = items
      .filter((item) => item && Core.isPresent(item[1]) && item[1] !== '—')
      .map(([label, value]) => `<div><dt>${esc(label)}</dt><dd>${value}</dd></div>`)
      .join('');
    return body ? `<dl class="parcel-rows">${body}</dl>` : '';
  }

  const text = (value) => esc(value);
  const note = (message) => `<p class="parcel-note">${esc(message)}</p>`;
  const empty = (message) => ({ body: note(message), empty: true });

  function badge(level, label) {
    return `<span class="parcel-badge" data-level="${esc(level)}">${esc(label)}</span>`;
  }

  function benchmarkInline(benchmark, formatter, tr) {
    if (!benchmark || !Core.isPresent(benchmark.value)) return '';
    const value = formatter ? formatter(benchmark.value) : Core.formatNumber(benchmark.value, 1);
    const label = [tr(benchmark.label || 'Benchmark'), benchmark.year || ''].filter(Boolean).join(' ');
    return `<small class="parcel-benchmark">${esc(tr('Benchmark'))} ${esc(label)}: <strong>${esc(value)}</strong></small>`;
  }

  function feedRefreshNote(data, tr) {
    const value = Core.feedRefreshValue(data);
    if (!value) return `<p class="parcel-feed">${esc(tr('Last feed refresh: not declared by the provider'))}</p>`;
    const date = Core.parseDateTime(value);
    const label = date ? `${Core.formatDateTime(date)} (${Core.relativeAge(date, tr)})` : String(value);
    return `<p class="parcel-feed">${esc(tr('Last feed refresh'))}: ${esc(label)}</p>`;
  }

  /** Describe a failed request in words a user can act on. */
  function failureMessage(status, tr) {
    if (status === 404 || status === 503) return tr('This data source is not available on this server.');
    return tr('This data is temporarily unavailable.');
  }

  // ---- Section registry ---------------------------------------------------------
  //
  // load(ctx)   -> Promise<data>; throw an Error with .status for failures.
  // render(data, ctx) -> { body, meta?, badge?, empty? } (no DOM access).
  // bind(el, data, ctx)  optional, after the body is in the DOM.

  const SECTIONS = [
    {
      id: 'identity', tab: 'value', icon: 'landmark', title: 'Cadastre', eager: true,
      load: async (ctx) => ctx.feature,
      render: (feature, ctx) => {
        const { tr } = ctx;
        const props = (feature && feature.properties) || {};
        const cadastral = ctx.block('cadastral');
        const cadastralData = (cadastral && cadastral.data) || {};
        const area = Core.parcelAreaSqm(props);
        const municipality = props.municipality_name || props.municipality || props.ADMINISTRATIVEUNIT || null;
        const attributes = Object.entries(props)
          .filter(([, value]) => value !== null && value !== undefined && typeof value !== 'object')
          .map(([key, value]) => `<tr><th>${esc(key.replaceAll('_', ' '))}</th><td>${esc(value)}</td></tr>`)
          .join('');
        const body = rows([
          [tr('Reference'), text(ctx.reference)],
          [tr('Parcel'), text(props.parcel_number ?? props.parcel ?? props.particella ?? props.LABEL)],
          [tr('Sheet'), text(props.sheet_number ?? props.sheet ?? props.foglio)],
          [tr('Urban section'), text(cadastralData.sezione_urbana ?? props.urban_section)],
          [tr('Municipality'), municipality ? `${text(municipality)}${ctx.cadastralCode ? ` <small>(${text(ctx.cadastralCode)})</small>` : ''}` : null],
          [tr('Province'), text(props.province)],
          [tr('Region'), text(props.region)],
          [tr('Postcode'), text(cadastralData.postal_code)],
          [tr('Area'), area ? `${Core.formatArea(area.value)}${area.computed ? ` <small>${esc(tr('computed from the geometry'))}</small>` : ''}` : null],
          [tr('Geometry'), feature && feature.geometry ? esc(tr('Available · WGS84')) : esc(tr('Unavailable for this record'))],
        ])
          + (attributes ? `<details class="parcel-all-attributes"><summary>${esc(tr('All attributes'))}</summary><table class="parcel-detail-table"><tbody>${attributes}</tbody></table></details>` : '');
        return {
          body,
          meta: {
            source: props.source_release || (cadastral && cadastral.source) || null,
            dataset_version: cadastral && cadastral.dataset_version,
            match_method: cadastral && cadastral.match_method,
          },
        };
      },
    },

    {
      id: 'omi', tab: 'value', icon: 'euro-sign', title: 'OMI valuation', eager: true,
      load: async (ctx) => {
        if (!ctx.cadastralCode) return null;
        const [quotes, zone] = await Promise.all([
          ctx.fetchJson(`${API}/omi/quotes?${new URLSearchParams({ comune: ctx.cadastralCode })}`),
          ctx.zoneMatch(),
        ]);
        if (!quotes.ok) throw Object.assign(new Error('OMI quotes unavailable'), { status: quotes.status });
        return { ...quotes.data, zone };
      },
      render: (data, ctx) => {
        const { tr } = ctx;
        if (!data) return empty(tr('The municipality code is not available for this parcel.'));
        const quotes = Core.validOmiQuotes(data, data.zone);
        if (!(data.quotes || []).length) return empty(tr('No OMI quotes are available for this municipality.'));
        if (!quotes.length) return empty(tr('The available OMI quotes contain no valid sale interval.'));
        const area = Core.parcelAreaSqm(ctx.props);
        const initial = Core.defaultEstimateArea(area && area.value);
        const detected = data.zone && data.zone.matched && String(data.zone.zone || '').toUpperCase();
        const detectedHasQuotes = detected && quotes.some((quote) => String(quote.zona || '').toUpperCase() === detected);
        const zoneNotice = detectedHasQuotes
          ? `<p class="parcel-callout" data-tone="ok">${esc(tr('OMI zone detected automatically'))}: <strong>${esc(detected)}</strong></p>`
          : detected
            ? `<p class="parcel-callout" data-tone="warn">${esc(tr('Zone {zone} was detected but has no quotes available: select and check an alternative.', { zone: detected }))}</p>`
            : `<p class="parcel-callout" data-tone="muted">${esc(tr('The OMI zone was not detected automatically: check the selection.'))}</p>`;
        const options = quotes.slice(0, 80).map((quote, index) => {
          const period = quote.anno && quote.semestre ? ` · ${quote.anno} S${quote.semestre}` : '';
          const state = quote.stato_conservazione ? ` · ${quote.stato_conservazione}` : '';
          return `<option value="${index}">${esc(`${tr('Zone')} ${quote.zona || '—'} · ${quote.tipologia || quote.cod_tipologia || tr('Type')}${state}${period}`)}</option>`;
        }).join('');
        const meta = { source: data.source, dataset_version: data.dataset_version };
        return {
          badge: quotes.length,
          meta,
          body: `${zoneNotice}
            <div class="parcel-form">
              <label for="parcelOmiQuote">${esc(tr('OMI zone and property type'))}</label>
              <select id="parcelOmiQuote" class="parcel-input">${options}</select>
              <label for="parcelOmiArea">${esc(tr('Surface used for the estimate (m²)'))}</label>
              <input id="parcelOmiArea" class="parcel-input" type="number" inputmode="decimal" min="1" step="1" value="${esc(initial.value)}" placeholder="${esc(tr('e.g. 100'))}">
            </div>
            ${initial.tooLarge ? note(tr('The parcel measures {area}: enter the commercial surface of the building to estimate.', { area: Core.formatArea(area.value) })) : ''}
            <div id="parcelOmiQuoteRows" class="parcel-quote-rows"></div>
            <div id="parcelOmiEstimate" class="parcel-estimate" aria-live="polite"></div>
            <p class="parcel-note parcel-disclaimer">${esc(tr('Indicative estimate: surface × the selected OMI interval. It is not an appraisal and does not account for the building\'s commercial consistency, actual condition or whether the OMI zone is the right one.'))}</p>
            <div id="parcelOmiHistory" class="parcel-history"></div>`,
        };
      },
      bind: (el, data, ctx) => bindOmi(el, data, ctx),
    },

    {
      id: 'pvp', tab: 'value', icon: 'gavel', title: 'PVP auctions', lazy: true,
      load: async (ctx) => {
        const municipality = await ctx.municipality();
        const parts = Core.referenceParts(ctx.props, ctx.reference);
        if (!ctx.reference || !municipality || !municipality.istat_code || !parts.sheet || !parts.parcel) return { unresolved: true };
        const query = new URLSearchParams({ municipality_code: municipality.istat_code, sheet: parts.sheet, parcel: parts.parcel });
        const result = await ctx.fetchJson(`${API}/parcel/pvp?${query}`);
        if (!result.ok) throw Object.assign(new Error('PVP unavailable'), { status: result.status });
        return result.data;
      },
      render: (data, ctx) => {
        const { tr } = ctx;
        if (data && data.unresolved) return empty(tr('The municipality is not identified, so auctions cannot be matched.'));
        const records = Array.isArray(data && data.records) ? data.records : [];
        const meta = { source: data && data.source, match_method: (data && data.match_method) || 'municipality_code+sheet+parcel' };
        if (!records.length) return { ...empty(tr('No PVP auction found for this parcel.')), meta };
        const items = records.slice(0, PVP_RECORD_LIMIT).map((record, index) => {
          const link = Core.safeHttpUrl(record.source_url);
          const street = `${record.street || ''} ${record.house_number || ''}`.trim();
          return `<article class="parcel-record"><h4>${esc(tr('Listing'))} ${index + 1} <small>${esc(record.source || 'PVP')}</small></h4>${rows([
            [tr('Sale ID'), record.sale_id != null ? text(record.sale_id) : null],
            [tr('Description'), record.description || record.sale_description ? text(record.description || record.sale_description) : null],
            [tr('Address'), street ? text(street) : null],
            [tr('Status'), record.announcement_status ? text(record.announcement_status) : null],
            [tr('Sale date'), record.sale_date ? text(record.sale_date) : null],
            [tr('Minimum offer'), record.minimum_offer != null ? Core.formatCurrency(record.minimum_offer) : null],
            [tr('Auction base price'), record.base_auction_price != null ? Core.formatCurrency(record.base_auction_price) : null],
            [tr('Listing surface'), record.surface_area != null ? `${Core.formatNumber(record.surface_area, 2)} m²` : null],
            [tr('Listing'), link ? `<a href="${esc(link)}" target="_blank" rel="noopener noreferrer">${esc(tr('Open listing'))}</a>` : null],
          ])}</article>`;
        }).join('');
        return {
          badge: records.length,
          meta,
          body: `${items}${records.length > PVP_RECORD_LIMIT ? note(tr('Showing {shown} of {total} listings.', { shown: PVP_RECORD_LIMIT, total: records.length })) : ''}${note(tr('PVP records are auction candidates matched by sheet and parcel, not official cadastral identifiers.'))}`,
        };
      },
    },

    {
      id: 'buildings', tab: 'property', icon: 'building', title: 'Buildings', lazy: true,
      load: async (ctx) => {
        if (!ctx.reference) return null;
        const result = await ctx.fetchJson(`${API}/parcel/buildings/${encodeURIComponent(ctx.reference)}`);
        if (!result.ok) throw Object.assign(new Error('Buildings unavailable'), { status: result.status });
        return result.data;
      },
      render: (data, ctx) => {
        const { tr } = ctx;
        const buildings = Array.isArray(data && data.buildings) ? data.buildings : [];
        const meta = { source: data && data.source };
        if (!buildings.length) return { ...empty(tr('No building is recorded in the SISTER cache for this parcel.')), meta };
        return {
          badge: buildings.length,
          meta,
          body: buildings.map((building, index) => `<article class="parcel-record">${buildings.length > 1 ? `<h4>${esc(tr('Building'))} ${index + 1}</h4>` : ''}${rows([
            [tr('Building type'), text(building.building_type || building.category || '—')],
            [tr('Cadastral class'), building.cadastral_class ? text(building.cadastral_class) : null],
            [tr('Consistency'), building.consistency != null ? text(building.consistency) : null],
            [tr('Cadastral income'), building.cadastral_income != null ? `€ ${Core.formatNumber(building.cadastral_income, 2)}` : null],
            [tr('Address'), building.address ? text(building.address) : null],
          ])}</article>`).join(''),
        };
      },
    },

    {
      id: 'opendata', tab: 'property', icon: 'database', title: 'Cadastral OpenData', lazy: true,
      load: async (ctx) => {
        const parts = Core.referenceParts(ctx.props, ctx.reference);
        if (!ctx.reference || !parts.municipality || !parts.sheet || !parts.parcel) return null;
        const query = new URLSearchParams({ municipality_code: parts.municipality, sheet: parts.sheet, parcel: parts.parcel });
        const result = await ctx.fetchJson(`${API}/parcel/opendata?${query}`);
        if (!result.ok) throw Object.assign(new Error('OpenData unavailable'), { status: result.status });
        return result.data;
      },
      render: (data, ctx) => {
        const { tr } = ctx;
        const records = Array.isArray(data && data.records) ? data.records : [];
        const meta = { source: data && data.source, match_method: data && data.match_method };
        if (!records.length) return { ...empty(tr('No cadastral OpenData found for this parcel.')), meta };
        return {
          badge: records.length,
          meta,
          body: records.map((record, index) => `<article class="parcel-record"><h4>${esc(tr('Result'))} ${index + 1} <small>${esc(record.endpoint || tr('Cadastre'))}</small></h4>${rows([
            [tr('Query date'), record.timestamp ? text(record.timestamp) : null],
            ...Core.flattenValues(record.result).map(([label, value]) => [label, text(value)]),
          ])}</article>`).join(''),
        };
      },
    },

    {
      id: 'risks', tab: 'territory', icon: 'triangle-exclamation', title: 'Environmental risks', eager: true,
      load: async (ctx) => {
        const municipality = await ctx.municipality();
        if (!municipality || !municipality.istat_code) return { unresolved: true };
        const result = await ctx.fetchJson(`${API}/risks/${encodeURIComponent(municipality.istat_code)}`);
        if (!result.ok) throw Object.assign(new Error('Risks unavailable'), { status: result.status });
        return result.data;
      },
      render: (data, ctx) => {
        const { tr } = ctx;
        if (data && data.unresolved) return empty(tr('The municipality is not identified, so risks cannot be retrieved.'));
        if (!data || (!data.seismic && !data.hydrogeological)) return empty(tr('No risk data is available for this municipality.'));
        const levelLabel = { high: tr('High'), medium: tr('Medium'), low: tr('Low'), unknown: '—' };
        const hydro = data.hydrogeological;
        const flood = hydro && hydro.flood && hydro.flood.area_pct ? hydro.flood.area_pct.P3_high_probability : null;
        const landslide = hydro && hydro.landslide && hydro.landslide.area_pct ? hydro.landslide.area_pct.P4_very_high : null;
        const share = (value) => {
          const number = Core.toNumber(value);
          return number === null ? '—' : `${badge(Core.riskLevel(number), levelLabel[Core.riskLevel(number)])} ${esc(Core.formatNumber(number, 1))}% ${esc(tr('of the area'))}`;
        };
        return {
          meta: { source: 'ISPRA IdroGEO / DPC via aecs4u-stats', spatial_resolution: tr('municipality') },
          body: `${note(tr('Municipality-level data: it describes the whole municipality, not this parcel.'))}${rows([
            [tr('Seismic zone'), data.seismic ? text(Core.seismicLabel(data.seismic.zone, tr)) : null],
            [tr('Flood hazard (P3)'), hydro ? share(flood) : null],
            [tr('Landslide hazard (P4)'), hydro ? share(landslide) : null],
          ])}`,
        };
      },
      // The strip shows the seismic zone as soon as it is known.
      bind: (el, data, ctx) => {
        if (data && data.seismic && Core.isPresent(data.seismic.zone)) ctx.setStat('seismic', ctx.tr('Zone {n}', { n: data.seismic.zone }));
      },
    },

    {
      id: 'bulletin', tab: 'territory', icon: 'bullhorn', title: 'Criticality bulletin', lazy: true,
      load: async (ctx) => {
        const [bulletin, municipality] = await Promise.all([ctx.fetchJson(`${API}/bulletin`), ctx.municipality()]);
        if (!bulletin.ok) throw Object.assign(new Error('Bulletin unavailable'), { status: bulletin.status });
        return { bulletin: bulletin.data, name: municipality ? municipality.name : null };
      },
      render: ({ bulletin, name }, ctx) => {
        const { tr } = ctx;
        if (!bulletin) return empty(tr('The Civil Protection bulletin is not available.'));
        if (bulletin.stale) return { body: note(tr('The bulletin has expired: today\'s alerts are not available.')) + feedRefreshNote(bulletin, tr), empty: true };
        const zone = Core.findBulletinZone(bulletin, name);
        if (!zone) return { body: note(tr('The municipality is not in today\'s bulletin.')) + feedRefreshNote(bulletin, tr), empty: true };
        const levels = {
          red: [tr('Red alert'), 'high'], orange: [tr('Orange alert'), 'high'], yellow: [tr('Yellow alert'), 'medium'], none: [tr('No alert'), 'low'],
        };
        const line = (label, description) => {
          const [name2, tone] = levels[Core.bulletinSeverity(description)];
          return [label, badge(tone, name2)];
        };
        return {
          meta: { source: bulletin.source },
          body: `${rows([
            [tr('Zone'), zone['Nome zona'] ? text(zone['Nome zona']) : null],
            line(tr('Hydraulic'), zone['Per rischio idraulico']),
            line(tr('Thunderstorms'), zone['Per rischio temporali']),
            line(tr('Hydrogeological'), zone['Per rischio idrogeologico']),
          ])}${feedRefreshNote(bulletin, tr)}`,
        };
      },
    },

    {
      id: 'fires', tab: 'territory', icon: 'fire', title: 'Active fires', lazy: true,
      load: async (ctx) => {
        if (!ctx.centroid) return { unresolved: true };
        const query = new URLSearchParams({ lat: ctx.centroid.lat, lng: ctx.centroid.lng, radius_km: FIRE_RADIUS_KM });
        const result = await ctx.fetchJson(`${API}/fires?${query}`);
        if (!result.ok) throw Object.assign(new Error('Fires unavailable'), { status: result.status });
        return result.data;
      },
      render: (data, ctx) => {
        const { tr } = ctx;
        if (data && data.unresolved) return empty(tr('The parcel geometry is not available.'));
        const meta = { source: data && data.source };
        if (!data || !data.count) {
          return { body: note(tr('No active fire detected within {km} km.', { km: FIRE_RADIUS_KM })) + (data ? feedRefreshNote(data, tr) : ''), meta, empty: true };
        }
        const sorted = (data.detections || []).slice().sort((a, b) => `${b.acq_date || ''}${b.acq_time || ''}`.localeCompare(`${a.acq_date || ''}${a.acq_time || ''}`));
        const items = sorted.slice(0, FIRE_RECORD_LIMIT).map((detection) => {
          const observed = Core.parseFireObservationTime(detection);
          const label = observed ? Core.formatDateTime(observed) : `${detection.acq_date || '—'}`;
          const age = observed ? Core.relativeAge(observed, tr) : '';
          return [`${esc(label)}${age ? ` <small>${esc(age)}</small>` : ''}`, detection.frp != null ? `${esc(detection.frp)} MW` : '—'];
        });
        return {
          badge: data.count,
          meta,
          body: `${rows([[tr('Detections within {km} km', { km: FIRE_RADIUS_KM }), text(data.count)]])}`
            + `<dl class="parcel-rows">${items.map(([label, value]) => `<div><dt>${label}</dt><dd>${value}</dd></div>`).join('')}</dl>`
            + `${data.count > FIRE_RECORD_LIMIT ? note(tr('+ {n} more', { n: data.count - FIRE_RECORD_LIMIT })) : ''}${feedRefreshNote(data, tr)}`,
        };
      },
    },

    {
      id: 'municipality', tab: 'context', icon: 'location-dot', title: 'Municipality', lazy: true,
      load: async (ctx) => ctx.municipality(),
      render: (data, ctx) => {
        const { tr } = ctx;
        if (!data) return empty(tr('No municipal data is available.'));
        const population = data.population;
        const history = Array.isArray(data.population_history) ? data.population_history : [];
        const site = Core.safeHttpUrl(data.website);
        const wiki = Core.safeHttpUrl(data.wikipedia_url);
        const link = (url, label) => (url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>` : null);
        const email = (value) => (/^[^\s@<>"']+@[^\s@<>"']+$/.test(String(value || '')) ? `<a href="mailto:${esc(value)}">${esc(value)}</a>` : null);
        return {
          meta: { source: data.source, dataset_version: data.dataset_version },
          body: `${rows([
            [tr('Municipality'), data.name ? text(data.name) : null],
            [tr('Official name'), data.official_name && data.official_name !== data.name ? text(data.official_name) : null],
            [tr('Province'), data.province ? `${text(data.province)}${data.province_sigla ? ` (${text(data.province_sigla)})` : ''}` : null],
            [tr('Region'), data.region ? text(data.region) : null],
            [tr('Cadastral code'), data.cadastral_code ? text(data.cadastral_code) : null],
            [tr('ISTAT code'), data.istat_code ? text(data.istat_code) : null],
            [tr('NUTS3 code'), data.nuts3 || data.nuts3_2021 ? text(data.nuts3 || data.nuts3_2021) : null],
            [tr('Postcode'), data.postal_code ? text(data.postal_code) : null],
            [tr('Status'), data.is_provincial_capital ? esc(tr('Provincial capital')) : null],
            [`${tr('Population')}${population ? ` (${population.year})` : ''}`, population ? Core.formatNumber(population.resident_population) : null],
            [tr('Email'), email(data.email)],
            [tr('PEC'), email(data.pec_email)],
            [tr('Website'), link(site, tr('Open website'))],
            [tr('Wikipedia'), link(wiki, tr('Open page'))],
          ])}${history.length > 1 ? `<details class="parcel-history-list"><summary>${esc(tr('Population history ({n} years)', { n: history.length }))}</summary>${rows(history.slice().reverse().map((item) => [String(item.year), Core.formatNumber(item.resident_population)]))}</details>` : ''}`,
        };
      },
    },

    {
      id: 'income', tab: 'context', icon: 'coins', title: 'Income (IRPEF)', lazy: true,
      load: async (ctx) => {
        if (!ctx.cadastralCode) return null;
        const result = await ctx.fetchJson(`${API}/income/${encodeURIComponent(ctx.cadastralCode)}`);
        if (!result.ok) throw Object.assign(new Error('Income unavailable'), { status: result.status });
        return result.data;
      },
      render: (data, ctx) => {
        const { tr } = ctx;
        if (!data) return empty(tr('No IRPEF data is available for this municipality.'));
        const benchmark = data.benchmark_average_income_eur || (data.benchmarks && data.benchmarks.average_income_eur) || BENCHMARKS.average_income_eur;
        const brackets = Core.incomeBracketRows(data.income_distribution);
        const bars = brackets.length
          ? `<div class="parcel-bars" role="list" aria-label="${esc(tr('Taxpayers by income bracket'))}">${brackets.map((row) => `<div class="parcel-bar-row" role="listitem"><span>${esc(row.label)}</span><div class="parcel-bar-track"><div class="parcel-bar" style="width:${row.pct ?? 0}%"></div></div><span>${row.pct === null ? '—' : `${esc(Core.formatNumber(row.pct, 1))}%`}</span></div>`).join('')}</div>`
          : '';
        return {
          meta: { source: data.source, dataset_version: data.dataset_version, spatial_resolution: tr('municipality') },
          body: `${rows([
            [tr('Taxpayers'), data.taxpayers != null ? Core.formatNumber(data.taxpayers) : null],
            [tr('Mean taxable income'), data.mean_taxable_income_eur != null
              ? `€ ${Core.formatNumber(data.mean_taxable_income_eur)}${benchmarkInline(benchmark, (value) => `€ ${Core.formatNumber(value)}`, tr)}`
              : null],
          ])}${bars}`,
        };
      },
    },

    {
      id: 'census', tab: 'context', icon: 'people-group', title: 'Census 2021', lazy: true,
      load: async (ctx) => {
        if (!ctx.centroid) return { unresolved: true };
        const query = new URLSearchParams({ lat: ctx.centroid.lat, lng: ctx.centroid.lng });
        const result = await ctx.fetchJson(`${API}/census/at-point?${query}`);
        if (!result.ok) throw Object.assign(new Error('Census unavailable'), { status: result.status });
        return result.data;
      },
      render: (data, ctx) => {
        const { tr } = ctx;
        if (data && data.unresolved) return empty(tr('The parcel geometry is not available to locate the census section.'));
        const population = ctx.block('population');
        const summary = Core.censusSummary(data, population, BENCHMARKS.population_density_per_km2);
        if (!summary) return empty(tr('No census section is available for this parcel.'));
        const r = summary.ratios;
        const modelled = population && (population.confidence != null || population.spatial_resolution);
        return {
          meta: { ...(population || {}), source: (population && population.source) || 'ISTAT Basi Territoriali 2021 via aecs4u-stats' },
          body: `${rows([
            [tr('Census section 2021'), summary.section ? text(summary.section) : null],
            [tr('Residents'), summary.population != null ? Core.formatNumber(summary.population) : null],
            [tr('Section density'), summary.density != null
              ? `${Core.formatNumber(summary.density, 1)} ${esc(tr('residents/km²'))}${benchmarkInline(summary.benchmark, (value) => `${Core.formatNumber(value, 0)} ${tr('residents/km²')}`, tr)}`
              : null],
            [tr('Households'), summary.households != null ? Core.formatNumber(summary.households) : null],
            [tr('Dwellings'), summary.dwellings != null ? Core.formatNumber(summary.dwellings) : null],
            [tr('Residential buildings'), summary.buildings != null ? Core.formatNumber(summary.buildings) : null],
            [tr('Employment 15–64'), Core.isPresent(r.employment_rate_working_age) ? Core.formatRatio(r.employment_rate_working_age) : null],
            [tr('Tertiary education'), Core.isPresent(r.education_tertiary_rate) ? Core.formatRatio(r.education_tertiary_rate) : null],
            [tr('Foreign residents'), Core.isPresent(r.foreign_resident_share) ? Core.formatRatio(r.foreign_resident_share) : null],
            [tr('Vacant dwellings'), Core.isPresent(r.vacancy_rate) ? Core.formatRatio(r.vacancy_rate) : null],
            [tr('Household size'), Core.isPresent(r.avg_household_size) ? Core.formatNumber(r.avg_household_size, 2) : null],
          ])}${modelled ? note(tr('Modelled values: they are estimates for the census section, not observations of this parcel.')) : ''}`,
        };
      },
    },

    {
      id: 'safety', tab: 'context', icon: 'shield-halved', title: 'Safety', lazy: true,
      load: async (ctx) => {
        if (!ctx.cadastralCode) return null;
        const result = await ctx.fetchJson(`${API}/crime/${encodeURIComponent(ctx.cadastralCode)}`);
        if (!result.ok) throw Object.assign(new Error('Safety unavailable'), { status: result.status });
        return result.data;
      },
      render: (data, ctx) => {
        const { tr } = ctx;
        if (!data) return empty(tr('Safety data is not available.'));
        return {
          meta: { source: data.source, spatial_resolution: tr('province') },
          body: `${rows([
            [tr('Territorial scope'), text(data.province || data.nuts3 || tr('Province'))],
            [tr('Year'), data.year != null ? text(data.year) : null],
            [tr('Reported crimes'), data.total_crimes != null ? Core.formatNumber(data.total_crimes) : null],
            [tr('Crime types recorded'), data.crime_types != null ? Core.formatNumber(data.crime_types) : null],
          ])}${note(tr('Province-level data, not specific to this parcel.'))}`,
        };
      },
    },

    {
      id: 'demographics', tab: 'context', icon: 'users', title: 'Demographic indicators', lazy: true,
      load: (ctx) => loadIndicators(ctx, 'demographics'),
      render: (data, ctx) => renderIndicators(data, ctx, 'Demographic indicators are not available.'),
    },

    {
      id: 'quality', tab: 'context', icon: 'leaf', title: 'Quality of life', lazy: true,
      load: (ctx) => loadIndicators(ctx, 'quality-of-life'),
      render: (data, ctx) => renderIndicators(data, ctx, 'Quality-of-life indicators are not available.'),
    },

    {
      id: 'pois', tab: 'context', icon: 'map-pin', title: 'Points of interest', lazy: true,
      load: async (ctx) => {
        if (!ctx.centroid) return { unresolved: true };
        const query = new URLSearchParams({ lat: ctx.centroid.lat, lng: ctx.centroid.lng, radius_km: POI_RADIUS_KM });
        const result = await ctx.fetchJson(`${API}/pois/?${query}`);
        if (!result.ok) throw Object.assign(new Error('POIs unavailable'), { status: result.status });
        return result.data;
      },
      render: (data, ctx) => {
        const { tr } = ctx;
        if (data && data.unresolved) return empty(tr('The parcel geometry is not available.'));
        const entries = Object.entries((data && data.categories) || {}).filter(([, list]) => list.length > 0).sort((a, b) => b[1].length - a[1].length);
        const meta = { source: data && data.source };
        if (!data || !data.total || !entries.length) return { ...empty(tr('No point of interest found within {km} km.', { km: POI_RADIUS_KM })), meta };
        return { badge: data.total, meta, body: rows(entries.map(([category, list]) => [tr(category), String(list.length)])) };
      },
    },

    {
      id: 'coverage', tab: 'data', icon: 'layer-group', title: 'Data coverage', eager: true,
      load: (ctx) => Promise.race([
        ctx.readModelPromise(),
        new Promise((resolve) => setTimeout(() => resolve({ unavailable: true }), READ_MODEL_WAIT_MS)),
      ]),
      render: (readModel, ctx) => {
        const { tr } = ctx;
        if (!readModel || readModel.unavailable) return empty(tr('The parcel data profile is temporarily unavailable.'));
        const blocks = readModel.blocks || {};
        if (!Object.keys(blocks).length) return empty(tr('No data profile has been built for this parcel yet.'));
        const available = COVERAGE_BLOCKS.filter(([name]) => (blocks[name] || {}).available === true).length;
        return {
          badge: `${available}/${COVERAGE_BLOCKS.length}`,
          body: `${rows(COVERAGE_BLOCKS.map(([name, label]) => {
            const block = blocks[name] || {};
            return [tr(label), block.available === true
              ? badge('low', tr('Available'))
              : badge('unknown', tr('Not available'))];
          }))}${note(tr('Blocks not present in the local datasets are listed as not available; they are never shown as zero.'))}`,
        };
      },
    },
  ];

  // ---- Shared loaders and renderers ------------------------------------------------

  async function loadIndicators(ctx, kind) {
    if (!ctx.cadastralCode) return null;
    const base = `${API}/${kind}/${encodeURIComponent(ctx.cadastralCode)}`;
    const catalog = await ctx.fetchJson(base);
    if (!catalog.ok) throw Object.assign(new Error('Indicators unavailable'), { status: catalog.status });
    if (!catalog.data || !catalog.data.indicators) return null;
    const codes = catalog.data.indicators.slice(0, INDICATOR_PREVIEW_LIMIT);
    const series = await Promise.all(codes.map(async (code) => {
      const result = await ctx.fetchJson(`${base}/${encodeURIComponent(code)}`);
      const values = result.ok && result.data ? result.data.series || [] : [];
      return { code, latest: values.length ? values[values.length - 1] : null };
    }));
    return { source: catalog.data.source, nuts3: catalog.data.nuts3, total: catalog.data.indicators.length, series };
  }

  function renderIndicators(data, ctx, emptyMessage) {
    const { tr } = ctx;
    if (!data || !data.series || data.series.every((item) => !item.latest)) return empty(tr(emptyMessage));
    const shown = data.series.filter((item) => item.latest);
    return {
      badge: data.total,
      meta: { source: data.source, spatial_resolution: tr('province') },
      body: `${rows(shown.map((item) => [Core.indicatorLabel(item.code), `${Core.formatNumber(item.latest.value, 2)} <small>${esc(item.latest.year || '')}</small>`]))}`
        + `${data.total > data.series.length ? note(tr('Preview of {shown} of {total} available indicators.', { shown: data.series.length, total: data.total })) : ''}`
        + note(tr('Province-level indicators ({nuts3}).', { nuts3: data.nuts3 || 'NUTS3' })),
    };
  }

  // ---- OMI estimator (DOM) -------------------------------------------------------------

  function bindOmi(el, data, ctx) {
    const { tr } = ctx;
    const quotes = Core.validOmiQuotes(data, data.zone).slice(0, 80);
    const select = el.querySelector('#parcelOmiQuote');
    const areaInput = el.querySelector('#parcelOmiArea');
    const quoteRows = el.querySelector('#parcelOmiQuoteRows');
    const estimate = el.querySelector('#parcelOmiEstimate');
    const history = el.querySelector('#parcelOmiHistory');
    if (!select || !areaInput || !quoteRows || !estimate || !history || !quotes.length) return;
    const benchmark = Core.quoteBenchmark(quotes);
    let estimateTimer = null;
    let estimateToken = 0;
    let historyToken = 0;
    const current = () => quotes[Number(select.value)] || quotes[0];
    const alive = () => ctx.isCurrent();

    function showQuote() {
      const quote = current();
      quoteRows.innerHTML = rows([
        [tr('Sale'), `${Core.formatNumber(quote.prezzo_min)}–${Core.formatNumber(quote.prezzo_max)} €/m²${benchmarkInline(benchmark, (value) => `${Core.formatNumber(value)} €/m²`, tr)}`],
        [tr('Rent'), Core.toNumber(quote.locazione_min) !== null ? `${Core.formatNumber(quote.locazione_min, 2)}–${Core.formatNumber(quote.locazione_max, 2)} €/m²/${esc(tr('month'))}` : null],
      ]);
      ctx.setStat('price', `${Core.formatNumber(quote.prezzo_min)}–${Core.formatNumber(quote.prezzo_max)} €/m²`);
    }

    function showEstimate() {
      const quote = current();
      const range = Core.estimateOmiRange(quote, areaInput.value);
      estimate.innerHTML = range
        ? `<span>${esc(tr('Indicative value'))}</span><strong>${esc(Core.formatCurrency(range.min))} – ${esc(Core.formatCurrency(range.max))}</strong><small>${esc(tr('Local preview'))}</small>`
        : `<span>${esc(tr('Enter a valid surface to compute the estimate.'))}</span>`;
      scheduleServerEstimate(quote, Number(areaInput.value));
    }

    function scheduleServerEstimate(quote, area) {
      const token = ++estimateToken;
      clearTimeout(estimateTimer);
      if (!Number.isFinite(area) || area <= 0) return;
      estimateTimer = setTimeout(async () => {
        const result = await ctx.postJson(`${API}/omi/estimate`, {
          comune: ctx.cadastralCode,
          zona: quote.zona,
          cod_tipologia: String(quote.cod_tipologia),
          stato_conservazione: quote.stato_conservazione || null,
          area_sqm: area,
        });
        if (token !== estimateToken || !alive() || !result.ok) return;
        const server = result.data && result.data.value_range_eur;
        if (!server) return;
        const versions = [result.data.model_version, result.data.dataset_version].filter(Boolean).join(' · ');
        estimate.innerHTML = `<span>${esc(tr('Indicative value'))}</span><strong>${esc(Core.formatCurrency(server.min))} – ${esc(Core.formatCurrency(server.max))}</strong>`
          + `<small>${esc(tr('Calculated and verified by the server'))}${versions ? ` · ${esc(versions)}` : ''}</small>`;
      }, 250);
    }

    async function showHistory() {
      const quote = current();
      const token = ++historyToken;
      history.innerHTML = `<p class="parcel-note">${esc(tr('Loading history…'))}</p>`;
      const query = new URLSearchParams({ comune: ctx.cadastralCode, zona: quote.zona || '' });
      if (quote.cod_tipologia != null) query.set('cod_tipologia', quote.cod_tipologia);
      const result = await ctx.fetchJson(`${API}/omi/history?${query}`);
      if (token !== historyToken || !alive()) return;
      const series = Core.omiHistorySeries(result.ok && result.data ? result.data.history : [], quote);
      if (!series.points.length) {
        history.innerHTML = note(tr('OMI history is not available for this selection.'));
        return;
      }
      const change = series.change === null ? '' : ` · ${series.change >= 0 ? '+' : ''}${Core.formatNumber(series.change, 1)}%`;
      history.innerHTML = `<h4>${esc(tr('Sale price history'))} <small>${esc(tr('{n} semesters', { n: series.points.length }))}${esc(change)}</small></h4>`
        + Core.historyChartSvg(series, tr('Trend of the mean OMI value'))
        + `<div class="parcel-chart-axis"><span>${esc(series.first.label)}</span><span>${esc(series.last.label)}</span></div>`
        + rows([[tr('Latest interval'), `${Core.formatNumber(series.last.min)}–${Core.formatNumber(series.last.max)} €/m²`]]);
    }

    select.addEventListener('change', () => { showQuote(); showEstimate(); showHistory(); });
    areaInput.addEventListener('input', showEstimate);
    showQuote();
    showEstimate();
    showHistory();
  }

  // ---- Panel (DOM) -----------------------------------------------------------------------

  const view = {
    mounted: false, token: 0, controller: null, observers: [], ctx: null,
    readModel: undefined, readModelWaiters: [], sections: new Map(), data: new Map(),
  };

  function stripCell(key, label) {
    return `<div class="parcel-stat" data-stat="${key}"><span class="parcel-stat-label">${esc(label)}</span><strong class="parcel-stat-value">—</strong></div>`;
  }

  function sectionShell(section, tr) {
    return `<section class="parcel-section" id="parcel-section-${section.id}" data-section="${section.id}" data-tab="${section.tab}" data-state="idle" aria-labelledby="parcel-section-${section.id}-title" aria-busy="false">
      <header class="parcel-section-header"><i class="fas fa-${section.icon}" aria-hidden="true"></i><h3 id="parcel-section-${section.id}-title">${esc(tr(section.title))}</h3><span class="parcel-section-badge" hidden></span></header>
      <div class="parcel-section-body"></div>
      <div class="parcel-section-footer" hidden></div>
    </section>`;
  }

  function makeContext(feature, options, tr) {
    const controller = new AbortController();
    const token = ++view.token;
    const props = (feature && feature.properties) || {};
    const reference = options.reference || null;
    const memo = new Map();
    const ctx = {
      tr, token, feature, props, reference,
      signal: controller.signal,
      cadastralCode: Core.cadastralCodeOf(props, reference),
      centroid: Core.centroidOf(props, feature && feature.geometry),
      isCurrent: () => token === view.token,
      block: (name) => {
        const model = view.readModel;
        return model && model.blocks && model.blocks[name] && model.blocks[name].available ? model.blocks[name] : null;
      },
      setStat: (key, value) => {
        if (!ctx.isCurrent()) return;
        const cell = view.statStrip && view.statStrip.querySelector(`[data-stat="${key}"] .parcel-stat-value`);
        if (cell) cell.textContent = value;
      },
      fetchJson: async (url) => {
        const request = new AbortController();
        const timer = setTimeout(() => request.abort(), REQUEST_TIMEOUT_MS);
        controller.signal.addEventListener('abort', () => request.abort(), { once: true });
        try {
          const response = await fetch(url, { signal: request.signal, headers: { Accept: 'application/json' } });
          let data = null;
          try { data = await response.json(); } catch (_) { /* non-JSON error body */ }
          return { ok: response.ok, status: response.status, data };
        } catch (_) {
          return { ok: false, status: 0, data: null };
        } finally { clearTimeout(timer); }
      },
      postJson: async (url, payload) => {
        try {
          const response = await fetch(url, {
            method: 'POST', signal: controller.signal,
            headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: JSON.stringify(payload),
          });
          let data = null;
          try { data = await response.json(); } catch (_) { /* non-JSON error body */ }
          return { ok: response.ok, status: response.status, data };
        } catch (_) {
          return { ok: false, status: 0, data: null };
        }
      },
      memo: (key, factory) => {
        if (!memo.has(key)) memo.set(key, factory());
        return memo.get(key);
      },
    };
    ctx.municipality = () => ctx.memo('municipality', async () => {
      if (!ctx.cadastralCode) return null;
      const result = await ctx.fetchJson(`${API}/municipality/${encodeURIComponent(ctx.cadastralCode)}`);
      return result.ok ? result.data : null;
    });
    ctx.zoneMatch = () => ctx.memo('zone', async () => {
      const municipality = await ctx.municipality();
      if (!municipality || !municipality.province || !ctx.centroid) return null;
      const query = new URLSearchParams({ province: municipality.province, lat: ctx.centroid.lat, lng: ctx.centroid.lng });
      const result = await ctx.fetchJson(`${API}/omi/at-point?${query}`);
      return result.ok ? result.data : null;
    });
    ctx.readModelPromise = () => new Promise((resolve) => {
      if (view.readModel !== undefined) resolve(view.readModel);
      else view.readModelWaiters.push(resolve);
    });
    return { ctx, controller };
  }

  function setState(el, state) {
    el.dataset.state = state;
    el.setAttribute('aria-busy', state === 'loading' ? 'true' : 'false');
  }

  function paint(el, section, result, ctx) {
    const body = el.querySelector('.parcel-section-body');
    const footer = el.querySelector('.parcel-section-footer');
    const count = el.querySelector('.parcel-section-badge');
    body.innerHTML = result.body;
    const metaHtml = result.meta ? Core.blockMetadataHtml(result.meta, ctx.tr) : '';
    footer.innerHTML = metaHtml;
    footer.hidden = !metaHtml;
    count.textContent = result.badge != null ? String(result.badge) : '';
    count.hidden = result.badge == null;
    setState(el, result.empty ? 'empty' : 'ready');
  }

  async function loadSection(section, el, ctx) {
    if (el.dataset.state === 'loading' || el.dataset.state === 'ready' || el.dataset.state === 'empty') return;
    setState(el, 'loading');
    el.querySelector('.parcel-section-body').innerHTML = `<div class="parcel-skeleton" aria-hidden="true"><span></span><span></span><span></span></div><span class="visually-hidden">${esc(ctx.tr('Loading…'))}</span>`;
    try {
      const data = await section.load(ctx);
      if (!ctx.isCurrent()) return;
      view.data.set(section.id, data);
      paint(el, section, section.render(data, ctx), ctx);
      if (section.bind) section.bind(el, data, ctx);
    } catch (error) {
      if (!ctx.isCurrent()) return;
      setState(el, 'error');
      const retryLabel = ctx.tr('Retry');
      el.querySelector('.parcel-section-body').innerHTML = `<p class="parcel-note" role="status">${esc(failureMessage(error && error.status, ctx.tr))}</p>`
        + `<button type="button" class="parcel-retry secondary-action">${esc(retryLabel)}</button>`;
      el.querySelector('.parcel-retry').addEventListener('click', () => {
        setState(el, 'idle');
        void loadSection(section, el, ctx);
      });
    }
  }

  function disconnectObservers() {
    view.observers.forEach((observer) => observer.disconnect());
    view.observers = [];
  }

  function observeSections(ctx) {
    const container = view.content;
    const entries = [...view.sections.entries()];
    if (typeof IntersectionObserver !== 'function') {
      entries.forEach(([id, { section, el }]) => { void loadSection(section, el, ctx); });
      return;
    }
    const lazy = new IntersectionObserver((items) => {
      items.filter((item) => item.isIntersecting).forEach((item) => {
        lazy.unobserve(item.target);
        const entry = view.sections.get(item.target.dataset.section);
        if (entry) void loadSection(entry.section, entry.el, ctx);
      });
    }, { root: container, rootMargin: LAZY_ROOT_MARGIN });
    // Section tabs follow whichever section sits nearest the top.
    const spy = new IntersectionObserver((items) => {
      const visible = items.filter((item) => item.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
      if (visible) setActiveTab(visible.target.dataset.tab);
    }, { root: container, rootMargin: '0px 0px -70% 0px', threshold: 0 });
    entries.forEach(([id, { section, el }]) => {
      spy.observe(el);
      if (section.eager) void loadSection(section, el, ctx);
      else lazy.observe(el);
    });
    view.observers.push(lazy, spy);
  }

  function setActiveTab(tabId) {
    if (!view.nav) return;
    view.nav.querySelectorAll('button[data-tab]').forEach((button) => {
      if (button.dataset.tab === tabId) button.setAttribute('aria-current', 'true');
      else button.removeAttribute('aria-current');
    });
  }

  function renderNav(tr) {
    view.nav.innerHTML = TABS.map((tab) => `<button type="button" data-tab="${tab.id}">${esc(tr(tab.title))}</button>`).join('');
    view.nav.querySelectorAll('button').forEach((button) => button.addEventListener('click', () => {
      const first = view.content.querySelector(`.parcel-section[data-tab="${button.dataset.tab}"]`);
      if (!first) return;
      const reduce = root.matchMedia && root.matchMedia('(prefers-reduced-motion: reduce)').matches;
      first.scrollIntoView({ block: 'start', behavior: reduce ? 'auto' : 'smooth' });
      setActiveTab(button.dataset.tab);
    }));
  }

  function mount({ content, nav, strip, tr }) {
    view.content = content;
    view.nav = nav;
    view.statStrip = strip;
    view.tr = tr || ((key, values) => Core.translate((k) => k, key, values));
    view.mounted = true;
  }

  function show(feature, options = {}) {
    if (!view.mounted) return;
    const tr = view.tr;
    if (view.controller) view.controller.abort();
    disconnectObservers();
    view.readModel = undefined;
    view.readModelWaiters = [];
    view.data = new Map();
    view.sections = new Map();
    const made = makeContext(feature, options, tr);
    view.controller = made.controller;
    view.ctx = made.ctx;
    const area = Core.parcelAreaSqm(made.ctx.props);
    view.statStrip.innerHTML = stripCell('area', area && area.computed ? tr('Area (computed)') : tr('Area'))
      + stripCell('sheet', tr('Sheet')) + stripCell('price', tr('Sale price (OMI)')) + stripCell('seismic', tr('Seismic zone'));
    made.ctx.setStat('area', area ? Core.formatArea(area.value) : '—');
    made.ctx.setStat('sheet', String(made.ctx.props.sheet_number ?? made.ctx.props.sheet ?? made.ctx.props.foglio ?? '—'));
    renderNav(tr);
    view.content.innerHTML = SECTIONS.map((section) => sectionShell(section, tr)).join('');
    SECTIONS.forEach((section) => {
      view.sections.set(section.id, { section, el: view.content.querySelector(`#parcel-section-${section.id}`) });
    });
    view.content.scrollTop = 0;
    setActiveTab(TABS[0].id);
    observeSections(made.ctx);
  }

  /** Hand over the parcel read model (or {unavailable: true}); refreshes dependent sections. */
  function setReadModel(model) {
    if (!view.mounted || !view.ctx) return;
    view.readModel = model || { unavailable: true };
    const waiters = view.readModelWaiters || [];
    view.readModelWaiters = [];
    waiters.forEach((resolve) => resolve(view.readModel));
    // Sections already painted from block-dependent data pick up provenance.
    ['identity', 'census'].forEach((id) => {
      const entry = view.sections.get(id);
      if (!entry || !['ready', 'empty'].includes(entry.el.dataset.state)) return;
      const data = view.data.get(id);
      paint(entry.el, entry.section, entry.section.render(data, view.ctx), view.ctx);
    });
  }

  function clear() {
    if (view.controller) view.controller.abort();
    disconnectObservers();
    view.token += 1;
    view.ctx = null;
    if (view.content) view.content.innerHTML = '';
    if (view.statStrip) view.statStrip.innerHTML = '';
  }

  return { mount, show, setReadModel, clear, sections: SECTIONS, tabs: TABS, coverageBlocks: COVERAGE_BLOCKS };
}));
