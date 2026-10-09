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
  const REPORT_TEXT_LIMIT = 180000;
  const REPORT_JSON_LIMIT = 220000;
  const REPORT_METADATA_KEYS = new Set([
    'source', 'dataset_version', 'data_vintage', 'model_version', 'updated_at',
    'license', 'licence', 'spatial_resolution', 'spatial_resolution_m', 'match_method',
    'additional_source', 'additional_model_version', 'additional_license',
  ]);
  const LAZY_ROOT_MARGIN = '320px 0px';
  const PVP_RECORD_LIMIT = 8;
  const FIRE_RECORD_LIMIT = 5;
  const FIRE_RADIUS_KM = 25;
  const POI_RADIUS_KM = 1;
  const BENCHMARKS = {
    population_density_per_km2: { label: 'Italy', value: 196, unit: 'residents/km²', year: 2021 },
  };
  const TABS = [
    { id: 'value', title: 'Value' },
    { id: 'property', title: 'Property' },
    { id: 'territory', title: 'Territory' },
    { id: 'context', title: 'Context' },
    { id: 'energy', title: 'Energy' },
    { id: 'data', title: 'Data' },
  ];
  const TAB_HASHES = {
    value: 'valore', property: 'immobile', territory: 'territorio', context: 'contesto', energy: 'energia', data: 'dati',
  };
  const HASH_TABS = Object.fromEntries(TABS.flatMap((tab) => [
    [TAB_HASHES[tab.id], tab.id], [tab.id, tab.id],
  ]));
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
        const quoteList = Array.isArray(data.quotes) ? data.quotes : [];
        const quotes = Core.validOmiQuotes(data, data.zone);
        if (!quoteList.length) return empty(tr('No OMI quotes are available for this municipality.'));
        const meta = { source: data.source, dataset_version: data.dataset_version };
        const allQuotes = omiQuoteTable(quoteList, tr);
        if (!quotes.length) return {
          ...empty(tr('No valid sale quote is available for an estimate.')),
          badge: quoteList.length,
          meta,
          body: `${note(tr('No valid sale quote is available for an estimate.'))}${allQuotes}`,
        };
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
        const quoteOverflowNote = quotes.length > 80
          ? note(tr('The estimator selector shows the first {limit} sale quotes; the table lists all current quotes.', { limit: 80 }))
          : '';
        return {
          badge: quoteList.length,
          meta,
          body: `${zoneNotice}${quoteOverflowNote}
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
            ${allQuotes}
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
      id: 'address', tab: 'property', icon: 'location-dot', title: 'Main address', eager: true,
      load: async (ctx) => {
        if (!ctx.reference) return { unresolved: true };
        const result = await ctx.sisterParcelRecords();
        if (!result.ok) throw Object.assign(new Error('Address unavailable'), { status: result.status });
        return result.data;
      },
      render: (data, ctx) => {
        const { tr } = ctx;
        if (data && data.unresolved) return empty(tr('The parcel identifier is not available.'));
        const addresses = Array.isArray(data && data.addresses) ? data.addresses : [];
        const meta = data ? { source: data.address_source || data.source, match_method: 'cadastral_reference' } : null;
        if (!addresses.length) {
          return { ...empty(tr('No address is recorded in the SISTER cache for this parcel.')), meta };
        }
        const addressRows = addresses.map((address, index) => [
          addresses.length === 1 ? tr('Address') : tr('Address {n}', { n: index + 1 }),
          text(address),
        ]);
        const total = Number(data.address_count ?? addresses.length);
        return {
          badge: total,
          meta,
          body: `${rows(addressRows)}${note(tr('Addresses are taken from SISTER cadastral property records matched by municipality, sheet and parcel. Coverage may be incomplete; this is not a geocoded address register.'))}`
            + (data.addresses_truncated ? note(tr('Showing {shown} of {total} addresses.', { shown: addresses.length, total })) : ''),
        };
      },
    },

    {
      id: 'buildings', tab: 'property', icon: 'building', title: 'Buildings', eager: true,
      load: async (ctx) => {
        if (!ctx.reference) return null;
        const result = await ctx.sisterParcelRecords();
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
      bind: (_el, data, ctx) => {
        const count = data && data.available === true
          ? Number(data.count ?? (Array.isArray(data.buildings) ? data.buildings.length : 0))
          : null;
        ctx.setStat('buildings', Number.isFinite(count) && count >= 0 ? Core.formatNumber(count) : '—');
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
      id: 'solar', tab: 'energy', icon: 'sun', title: 'Municipal solar potential', lazy: true,
      load: (ctx) => Promise.race([
        ctx.readModelPromise(),
        new Promise((resolve) => setTimeout(() => resolve({ unavailable: true }), READ_MODEL_WAIT_MS)),
      ]),
      render: (_readModel, ctx) => {
        const { tr } = ctx;
        const block = ctx.block('solar');
        const data = (block && block.data) || {};
        if (!block) return empty(tr('Municipal solar potential is not available for this municipality.'));
        const amount = (key, suffix, digits = 1) => {
          const value = Core.toNumber(data[key]);
          return value === null ? null : `${Core.formatNumber(value, digits)}${suffix}`;
        };
        const body = rows([
          [tr('Buildings included in the estimate'), amount('pv_n_buildings', '', 0)],
          [tr('Estimated maximum PV capacity'), amount('pv_kwp_max_total', ' kWp')],
          [tr('Annual PV output (pessimistic)'), amount('pv_pvout_pessimistic_kwh_year_total', ' kWh/year', 0)],
          [tr('Annual PV output (modern)'), amount('pv_pvout_modern_kwh_year_total', ' kWh/year', 0)],
          [tr('PV output per resident'), amount('pv_pvout_per_capita_kwh', ' kWh', 0)],
          [tr('High viability'), amount('pv_high_viability_pct', '%')],
          [tr('Medium viability'), amount('pv_medium_viability_pct', '%')],
          [tr('Low viability'), amount('pv_low_viability_pct', '%')],
          [tr('Not eligible'), amount('pv_not_eligible_pct', '%')],
          [tr('Solar source observations'), amount('pv_observation_count', '', 0)],
        ]);
        if (!body) return empty(tr('Municipal solar potential is not available for this municipality.'));
        const buildingCount = Core.toNumber(data.pv_n_buildings);
        return {
          badge: buildingCount === null ? null : Core.formatNumber(buildingCount, 0),
          meta: {
            source: block.source,
            dataset_version: block.dataset_version,
            updated_at: block.updated_at,
            spatial_resolution: block.spatial_resolution || tr('municipality'),
            match_method: block.match_method || 'municipality',
          },
          body: `${body}${note(tr('Municipal aggregates only. They are not estimates for this parcel, building, or roof.'))}`,
        };
      },
    },

    {
      id: 'risks', tab: 'territory', icon: 'triangle-exclamation', title: 'Environmental risks', eager: true,
      load: async (ctx) => {
        const municipality = await ctx.municipality();
        if ((!municipality || !municipality.istat_code) && !ctx.centroid) return { unresolved: true };
        const [riskResult, pgaResult] = await Promise.all([
          municipality && municipality.istat_code
            ? ctx.fetchJson(`${API}/risks/${encodeURIComponent(municipality.istat_code)}`)
            : Promise.resolve({ ok: true, data: null }),
          ctx.centroid
            ? ctx.fetchJson(`${API}/mps04/pga?${new URLSearchParams({ lat: ctx.centroid.lat, lng: ctx.centroid.lng })}`)
            : Promise.resolve({ ok: true, data: { available: false, reason: 'parcel_point_unavailable' } }),
        ]);
        return {
          ...(riskResult.ok && riskResult.data ? riskResult.data : {}),
          pga: pgaResult.ok ? pgaResult.data : { available: false, reason: 'mps04_request_failed' },
        };
      },
      render: (data, ctx) => {
        const { tr } = ctx;
        if (data && data.unresolved) return empty(tr('The municipality is not identified, so risks cannot be retrieved.'));
        if (!data || (!data.seismic && !data.hydrogeological && !data.pga)) return empty(tr('No risk data is available for this municipality.'));
        const levelLabel = { high: tr('High'), medium: tr('Medium'), low: tr('Low'), unknown: '—' };
        const hydro = data.hydrogeological;
        const pga = data.pga;
        const flood = hydro && hydro.flood && hydro.flood.area_pct ? hydro.flood.area_pct.P3_high_probability : null;
        const landslide = hydro && hydro.landslide && hydro.landslide.area_pct ? hydro.landslide.area_pct.P4_very_high : null;
        const share = (value) => {
          const number = Core.toNumber(value);
          return number === null ? '—' : `${badge(Core.riskLevel(number), levelLabel[Core.riskLevel(number)])} ${esc(Core.formatNumber(number, 1))}% ${esc(tr('of the area'))}`;
        };
        const pgaValue = pga && pga.available && pga.matched && pga.pga_g != null
          ? `${Core.formatNumber(pga.pga_g, 3)} g${pga.pga_p16_g != null && pga.pga_p84_g != null
            ? ` (${Core.formatNumber(pga.pga_p16_g, 3)}–${Core.formatNumber(pga.pga_p84_g, 3)} g)` : ''}`
          : pga && pga.available && !pga.matched
            ? tr('No MPS04 grid point was found within 5 km.')
            : tr('Not available on this server.');
        return {
          meta: {
            source: 'ISPRA IdroGEO / DPC via aecs4u-stats',
            additional_source: 'INGV MPS04 seismic hazard model',
            additional_model_version: (pga && pga.model_version) || 'MPS04',
            additional_license: (pga && pga.license) || 'CC BY 4.0',
            spatial_resolution: 'municipality plus parcel-centroid nearest grid point',
            match_method: 'municipality lookup; MPS04 nearest native grid point',
          },
          body: `${note(tr('The seismic zone and flood or landslide shares describe the whole municipality. PGA is a parcel-centroid estimate from the nearest INGV MPS04 grid point, not an interpolated or site-specific value.'))}${note(tr('PGA source: INGV MPS04 under CC BY 4.0. The estimate uses a 10 percent exceedance probability in 50 years and the nearest native grid point; no interpolation is applied.'))}${rows([
            [tr('Seismic zone'), data.seismic ? text(Core.seismicLabel(data.seismic.zone, tr)) : null],
            [tr('PGA (10 percent in 50 years)'), pgaValue],
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
      id: 'parcel-hazards', tab: 'territory', icon: 'draw-polygon', title: 'Parcel-scale hazards', lazy: true,
      load: async (ctx) => {
        if (!ctx.reference) return { unresolved: true };
        const query = new URLSearchParams();
        if (ctx.featureId != null) query.set('id', ctx.featureId);
        const queryString = query.toString();
        const suffix = queryString ? `?${queryString}` : '';
        const result = await ctx.fetchJson(`${API}/parcel/hazards/${encodeURIComponent(ctx.reference)}${suffix}`);
        if (!result.ok) throw Object.assign(new Error('Parcel hazards unavailable'), { status: result.status });
        return result.data;
      },
      render: (data, ctx) => {
        const { tr } = ctx;
        if (data && data.unresolved) return empty(tr('The parcel identifier is not available.'));
        if (!data || !data.available) return {
          ...empty(tr('Parcel-scale ISPRA hazard polygons are not available on this server.')),
          meta: data ? { source: data.source, dataset_version: data.dataset_version } : null,
        };
        const classLabel = (value) => value || tr('No mapped hazard intersects this parcel.');
        const summaryRows = (label, item) => [
          [label, classLabel(item.worst_class)],
          [tr('Affected parcel area'), `${Core.formatNumber(item.affected_area_sqm, 1)} m² (${Core.formatNumber(item.affected_area_pct, 2)}%)`],
          [tr('Intersecting hazard classes'), item.classes && item.classes.length
            ? item.classes.map((row) => `${row.code}: ${Core.formatNumber(row.overlap_pct, 2)}%`).map(text).join(', ')
            : tr('No mapped hazard intersects this parcel.')],
        ];
        return {
          meta: { source: data.source, dataset_version: data.dataset_version, spatial_resolution: tr('parcel polygon intersection'), match_method: data.match_method },
          body: `${rows([
            [tr('Parcel surface'), `${Core.formatNumber(data.parcel_area_sqm, 1)} m²`],
            ...summaryRows(tr('Landslide: highest mapped class'), data.landslide || {}),
            ...summaryRows(tr('Flood: highest mapped scenario'), data.flood || {}),
          ])}${note(tr('Class areas can overlap and are not additive. These are intersections with the mapped ISPRA polygons. Source polygons: CC BY-SA 4.0.'))}`
            + (data.coverage_complete ? '' : note(tr('The hazard query is partial because its feature limit was reached or some polygon geometries could not be measured.'))),
        };
      },
    },

    {
      id: 'agenziademanio', tab: 'territory', icon: 'anchor', title: 'Agenzia Demanio concessions', lazy: true,
      load: async (ctx) => {
        const parcelId = Number(ctx.featureId);
        if (!Number.isSafeInteger(parcelId) || parcelId <= 0) return { unresolved: true };
        const result = await ctx.fetchJson(`${API}/parcel/agenziademanio/${encodeURIComponent(parcelId)}`);
        if (!result.ok) throw Object.assign(new Error('Agenzia Demanio links unavailable'), { status: result.status });
        return result.data;
      },
      render: (data, ctx) => {
        const { tr } = ctx;
        if (data && data.unresolved) return empty(tr('The parcel identifier is not available.'));
        const meta = data ? {
          source: data.source,
          spatial_resolution: data.spatial_resolution,
        } : null;
        if (!data || !data.available) {
          return { ...empty(tr('Agenzia Demanio parcel links are not available on this server.')), meta };
        }
        const matches = Array.isArray(data.matches) ? data.matches : [];
        if (!matches.length) return { ...empty(tr('No Agenzia Demanio concession is spatially linked to this parcel.')), meta };
        const items = matches.map((record) => {
          const heading = record.admin_label
            || (record.idconc ? `${tr('Concession')} ${record.idconc}` : tr('Concession'));
          const matchLabel = record.match_method === 'polygon_overlap'
            ? tr('Positive-area polygon overlap')
            : tr('Point covered by parcel');
          return `<article class="parcel-record"><h4>${esc(heading)}</h4>${rows([
            [tr('Concession ID'), record.idconc ? text(record.idconc) : null],
            [tr('Administrative label'), record.admin_label ? text(record.admin_label) : null],
            [tr('Spatial match'), text(matchLabel)],
            [tr('Overlap area'), record.intersection_area_sqm != null
              ? `${Core.formatNumber(record.intersection_area_sqm, 1)} m²` : null],
            [tr('Snapshot'), record.snapshot_id ? text(record.snapshot_id) : null],
            [tr('Source release'), record.concession_source_release ? text(record.concession_source_release) : null],
          ])}</article>`;
        }).join('');
        return {
          badge: data.total,
          meta,
          body: `${items}${data.truncated ? note(tr('Showing {shown} of {total} concession links.', { shown: matches.length, total: data.total })) : ''}`
            + note(tr('Matches are calculated from the published geometries. A boundary point can match more than one parcel; this is a spatial link, not a cadastral or legal determination.')),
        };
      },
    },

    {
      id: 'subsidence', tab: 'territory', icon: 'arrows-down-to-line', title: 'Ground movement (EGMS)', lazy: true,
      load: async (ctx) => {
        if (!ctx.reference) return { unresolved: true };
        const query = new URLSearchParams();
        if (ctx.featureId != null) query.set('id', ctx.featureId);
        const queryString = query.toString();
        const suffix = queryString ? `?${queryString}` : '';
        const result = await ctx.fetchJson(`${API}/parcel/subsidence/${encodeURIComponent(ctx.reference)}${suffix}`);
        if (!result.ok) throw Object.assign(new Error('EGMS subsidence unavailable'), { status: result.status });
        return result.data;
      },
      render: (data, ctx) => {
        const { tr } = ctx;
        if (data && data.unresolved) return empty(tr('The parcel geometry is not available.'));
        const meta = data ? {
          source: data.source,
          dataset_version: data.dataset_version,
          license: data.license,
          spatial_resolution: data.spatial_resolution,
          match_method: data.match_method,
        } : null;
        if (!data || !data.available) return { ...empty(tr('EGMS ground-motion data is not available on this server.')), meta };
        if (!data.matched) return { ...empty(tr('No EGMS grid cell intersects this parcel.')), meta };
        const velocity = Core.toNumber(data.mean_velocity_mm_per_year);
        const acceleration = Core.toNumber(data.mean_acceleration_mm_per_year_squared);
        const classes = Array.isArray(data.class_distribution) ? data.class_distribution : [];
        const classSummary = classes.length
          ? classes.map((item) => `${text(item.class)} (${Core.formatNumber(item.area_pct, 2)}%)`).join(', ')
          : data.dominant_class || null;
        return {
          meta,
          body: `${rows([
            [tr('Movement class by covered area'), classSummary],
            [tr('Intersected EGMS cells'), data.cell_count],
            [tr('Parcel area covered by EGMS'), `${Core.formatNumber(data.covered_area_sqm, 1)} m² (${Core.formatNumber(data.covered_area_pct, 2)}%)`],
            [tr('Mean vertical velocity'), velocity !== null ? `${Core.formatNumber(velocity, 2)} mm/year` : null],
            [tr('Minimum vertical velocity'), data.min_velocity_mm_per_year != null ? `${Core.formatNumber(data.min_velocity_mm_per_year, 2)} mm/year` : null],
            [tr('Maximum vertical velocity'), data.max_velocity_mm_per_year != null ? `${Core.formatNumber(data.max_velocity_mm_per_year, 2)} mm/year` : null],
            [tr('Mean acceleration'), acceleration !== null ? `${Core.formatNumber(acceleration, 3)} mm/year²` : null],
          ])}${note(tr('Velocity and acceleration are weighted by the parcel area intersected by each EGMS grid cell. Coverage is the area overlapped by returned cells and does not certify complete source coverage. Negative vertical velocity indicates lowering.'))}`
            + (data.query_complete ? '' : note(tr('The EGMS query is partial because its candidate limit was reached or some grid-cell geometries could not be measured.'))),
        };
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
        const averages = data.income_reference_averages || {};
        const averageValue = (scope, fallback) => {
          const value = averages[scope] && averages[scope].mean_taxable_income_eur;
          const resolved = value == null ? fallback : value;
          return resolved == null ? null : `€ ${Core.formatNumber(resolved)}`;
        };
        const averageLabel = (key, scope) => {
          const name = averages[scope] && averages[scope].name;
          return name ? `${tr(key)} (${name})` : tr(key);
        };
        const averageSeries = [
          { key: 'municipality', label: averageLabel('Municipality average', 'municipality'), fallback: data.mean_taxable_income_eur },
          { key: 'province', label: averageLabel('Province average', 'province') },
          { key: 'region', label: averageLabel('Region average', 'region') },
          { key: 'nation', label: averageLabel('National average', 'nation') },
        ].map((item) => ({
          ...item,
          value: Core.toNumber(
            averages[item.key] && averages[item.key].mean_taxable_income_eur != null
              ? averages[item.key].mean_taxable_income_eur : item.fallback
          ),
        })).filter((item) => item.value != null);
        const averageMaximum = Math.max(0, ...averageSeries.map((item) => item.value));
        const averageBars = averageSeries.length
          ? `<h4 class="parcel-chart-heading">${esc(tr('Average taxable income'))}</h4><div class="parcel-bars parcel-average-bars" role="list" aria-label="${esc(tr('Average taxable income by area'))}">${averageSeries.map((item) => {
            const width = averageMaximum > 0 ? Math.max(2, item.value / averageMaximum * 100) : 0;
            return `<div class="parcel-bar-row" role="listitem"><span>${esc(item.label)}</span><div class="parcel-bar-track"><div class="parcel-bar" style="width:${width.toFixed(1)}%"></div></div><span>€ ${esc(Core.formatNumber(item.value))}</span></div>`;
          }).join('')}</div>`
          : '';
        const brackets = Core.incomeBracketRows(data.income_distribution);
        const inequality = data.derived_metrics || {};
        const bars = brackets.length
          ? `<div class="parcel-bars" role="list" aria-label="${esc(tr('Taxpayers by income bracket'))}">${brackets.map((row) => `<div class="parcel-bar-row" role="listitem"><span>${esc(row.label)}</span><div class="parcel-bar-track"><div class="parcel-bar" style="width:${row.pct ?? 0}%"></div></div><span>${row.pct === null ? '—' : `${esc(Core.formatNumber(row.pct, 1))}%`}</span></div>`).join('')}</div>`
          : '';
        return {
          meta: { source: data.source, dataset_version: data.dataset_version, spatial_resolution: tr('municipality') },
          body: `${rows([
            [tr('Taxpayers'), data.taxpayers != null ? Core.formatNumber(data.taxpayers) : null],
            [averageLabel('Municipality average', 'municipality'),
              averageValue('municipality', data.mean_taxable_income_eur)],
            [averageLabel('Province average', 'province'), averageValue('province', null)],
            [averageLabel('Region average', 'region'), averageValue('region', null)],
            [averageLabel('National average', 'nation'), averageValue('nation', null)],
            [tr('Estimated income inequality (Gini)'), inequality.grouped_gini_estimate != null
              ? `${Core.formatNumber(inequality.grouped_gini_estimate, 3)} <small>${esc(inequality.grouped_gini_model_version || '')}</small>`
              : null],
          ])}${averageBars}${bars}${inequality.grouped_gini_estimate != null
            ? note(tr('Grouped Gini estimate on a 0–1 scale from eight income brackets. The lowest bracket is represented by €0 and the open-ended top bracket by €150,000; within-bracket inequality is not observed.'))
            : ''}`,
        };
      },
    },

    {
      id: 'census', tab: 'context', icon: 'people-group', title: 'Census 2021', lazy: true,
      load: async (ctx) => {
        if (!ctx.centroid) return { unresolved: true };
        const query = new URLSearchParams({ lat: ctx.centroid.lat, lng: ctx.centroid.lng });
        if (ctx.cadastralCode) query.set('comune', ctx.cadastralCode);
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
          ])}${modelled ? note(tr('Modelled values: they are estimates for the census section, not observations of this parcel.')) : ''}${censusAgePyramid(data, tr)}`,
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
      load: (ctx) => loadQualityOfLifeIndicators(ctx),
      render: (data, ctx) => renderQualityOfLifeIndicators(data, ctx),
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
    const codes = catalog.data.indicators.slice(0, 4);
    const series = await Promise.all(codes.map(async (code) => {
      const result = await ctx.fetchJson(`${base}/${encodeURIComponent(code)}`);
      const values = result.ok && result.data ? result.data.series || [] : [];
      return { code, latest: values.length ? values[values.length - 1] : null };
    }));
    return { source: catalog.data.source, nuts3: catalog.data.nuts3, total: catalog.data.indicators.length, series };
  }

  async function loadQualityOfLifeIndicators(ctx) {
    if (!ctx.cadastralCode) return null;
    const base = `${API}/quality-of-life/${encodeURIComponent(ctx.cadastralCode)}`;
    const catalog = await ctx.fetchJson(base);
    if (!catalog.ok) throw Object.assign(new Error('Quality-of-life indicators unavailable'), { status: catalog.status });
    if (!catalog.data) return null;
    if (Array.isArray(catalog.data.clusters) && Array.isArray(catalog.data.years)) return catalog.data;

    // Compatibility fallback for older Stats snapshots without the clustered
    // source view: fetch every indicator and pivot its series into year columns.
    const indicators = Array.isArray(catalog.data.indicators) ? catalog.data.indicators : [];
    const records = [];
    for (let offset = 0; offset < indicators.length; offset += 16) {
      records.push(...await Promise.all(indicators.slice(offset, offset + 16).map(async (name) => {
        const result = await ctx.fetchJson(`${base}/${encodeURIComponent(name)}`);
        const values = result.ok && result.data ? result.data.series || [] : [];
        return { name, values };
      })));
    }
    const yearSet = new Set();
    const rowsByIndicator = records.map(({ name, values }) => {
      const row = { name, unit: null, unit_varies: false, values: {} };
      const units = new Set();
      values.forEach((item) => {
        const year = String(item.edition_label || (item.is_covid_supplement ? `${item.year} COVID` : item.year));
        yearSet.add(year);
        row.values[year] = {
          value: item.value,
          unit: item.unit || null,
          temporal_reference: item.temporal_reference || null,
        };
        if (item.unit) units.add(item.unit);
      });
      row.unit_varies = units.size > 1;
      row.unit = units.size === 1 ? [...units][0] : null;
      return row;
    });
    const years = [...yearSet].sort((a, b) => {
      const ay = Number.parseInt(a, 10) || 0;
      const by = Number.parseInt(b, 10) || 0;
      return ay - by || a.localeCompare(b);
    });
    return {
      ...catalog.data,
      years,
      clusters: rowsByIndicator.length ? [{
        key: 'quality_of_life',
        label: 'Quality-of-life indicators',
        indicators: rowsByIndicator,
      }] : [],
    };
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

  function renderQualityOfLifeIndicators(data, ctx) {
    const { tr } = ctx;
    const clusters = data && Array.isArray(data.clusters) ? data.clusters : [];
    const years = data && Array.isArray(data.years) ? data.years : [];
    if (!clusters.some((cluster) => (cluster.indicators || []).length)) {
      return empty(tr('Quality-of-life indicators are not available.'));
    }
    const cell = (indicator, year) => {
      const item = indicator.values && indicator.values[year];
      if (item == null || item.value == null) return '<td>—</td>';
      const unit = item.unit || '';
      const title = [unit, item.temporal_reference].filter(Boolean).join(' · ');
      return `<td${title ? ` title="${esc(title)}"` : ''}>${esc(Core.formatNumber(item.value, 2))}`
        + `${indicator.unit_varies && unit ? `<small class="parcel-indicator-cell-unit">${esc(unit)}</small>` : ''}</td>`;
    };
    const body = clusters.map((cluster) => {
      const indicators = cluster.indicators || [];
      const tableRows = indicators.map((indicator) => `<tr>
        <th scope="row">${esc(indicator.name)}${indicator.unit && !indicator.unit_varies
          ? `<small class="parcel-indicator-unit">${esc(indicator.unit)}</small>` : ''}</th>
        ${years.map((year) => cell(indicator, year)).join('')}
      </tr>`).join('');
      return `<section class="parcel-indicator-cluster"><h4>${esc(tr(cluster.label))} <small>${indicators.length}</small></h4>
        <div class="parcel-indicator-scroll" role="region" tabindex="0" aria-label="${esc(tr(cluster.label))}">
          <table class="parcel-indicator-table"><thead><tr><th scope="col">${esc(tr('Indicator'))}</th>
            ${years.map((year) => `<th scope="col">${esc(year)}</th>`).join('')}
          </tr></thead><tbody>${tableRows}</tbody></table>
        </div></section>`;
    }).join('');
    return {
      badge: clusters.reduce((sum, cluster) => sum + (cluster.indicators || []).length, 0),
      meta: { source: data.source, spatial_resolution: tr('province') },
      body: `${body}${note(tr('Province-level indicators ({nuts3}).', { nuts3: data.nuts3 || 'NUTS3' }))}`,
    };
  }

  function censusAgePyramid(data, tr) {
    const stats = Core.censusDemographics(data);
    if (!stats) return '';
    const maximum = Math.max(...stats.ageGroups.flatMap((group) => [group.male, group.female]));
    if (!(maximum > 0)) return '';
    const maleShare = stats.total ? Core.formatRatio(stats.male / stats.total, 1) : '—';
    const femaleShare = stats.total ? Core.formatRatio(stats.female / stats.total, 1) : '—';
    const ageRows = stats.ageGroups.map((group) => {
      const maleWidth = Math.min(100, (group.male / maximum) * 100);
      const femaleWidth = Math.min(100, (group.female / maximum) * 100);
      return `<tr><td class="parcel-age-male"><span>${Core.formatNumber(group.male)}</span><span class="parcel-age-track"><i style="width:${maleWidth.toFixed(1)}%"></i></span></td>`
        + `<th scope="row">${esc(tr(group.label))}</th>`
        + `<td class="parcel-age-female"><span class="parcel-age-track"><i style="width:${femaleWidth.toFixed(1)}%"></i></span><span>${Core.formatNumber(group.female)}</span></td></tr>`;
    }).join('');
    return `<details class="parcel-census-demographics"><summary>${esc(tr('Age and sex distribution (Census 2021)'))}</summary>`
      + `${rows([[tr('Male residents'), `${Core.formatNumber(stats.male)} (${maleShare})`], [tr('Female residents'), `${Core.formatNumber(stats.female)} (${femaleShare})`]])}`
      + `<div class="parcel-age-legend"><span><i class="parcel-age-swatch parcel-age-swatch-male"></i>${esc(tr('Male'))}</span><span><i class="parcel-age-swatch parcel-age-swatch-female"></i>${esc(tr('Female'))}</span></div>`
      + `<table class="parcel-age-pyramid" aria-label="${esc(tr('Age and sex distribution (Census 2021)'))}"><thead><tr><th>${esc(tr('Male'))}</th><th>${esc(tr('Age'))}</th><th>${esc(tr('Female'))}</th></tr></thead><tbody>${ageRows}</tbody></table>`
      + `${note(tr('Counts describe residents of the 2021 census section containing the parcel, not residents of this parcel.'))}`
      + `${note(tr('Bar length is scaled to the largest age-group count.'))}</details>`;
  }

  function omiQuoteTable(quotes, tr) {
    const labels = ['Zone', 'Type', 'Condition', 'Sale range (€/m²)', 'Rent range (€/m²/month)', 'Gross yield', 'Sale percentile', 'Period'];
    const body = quotes.map((quote) => {
      const saleMin = Core.toNumber(quote.prezzo_min);
      const saleMax = Core.toNumber(quote.prezzo_max);
      const rentMin = Core.toNumber(quote.locazione_min);
      const rentMax = Core.toNumber(quote.locazione_max);
      const metrics = quote.derived_metrics || {};
      const sale = saleMin !== null && saleMax !== null ? `${Core.formatNumber(saleMin)}–${Core.formatNumber(saleMax)}` : '—';
      const rent = rentMin !== null && rentMax !== null ? `${Core.formatNumber(rentMin, 2)}–${Core.formatNumber(rentMax, 2)}` : '—';
      const yieldPct = Core.toNumber(metrics.gross_rental_yield_pct);
      const percentile = Core.toNumber(metrics.zone_percentile_pct);
      const period = quote.anno && quote.semestre ? `${quote.anno} S${quote.semestre}` : '—';
      return `<tr><td>${esc(quote.zona || '—')}</td><td>${esc(quote.tipologia || quote.cod_tipologia || '—')}</td>`
        + `<td>${esc(quote.stato_conservazione || '—')}</td><td>${sale}</td><td>${rent}</td>`
        + `<td>${yieldPct === null ? '—' : `${Core.formatNumber(yieldPct, 2)}%`}</td>`
        + `<td>${percentile === null ? '—' : `${Core.formatNumber(percentile, 1)}%`}</td><td>${esc(period)}</td></tr>`;
    }).join('');
    const title = tr('All OMI quotes for this municipality ({n})', { n: quotes.length });
    return `<details class="parcel-omi-quotes"><summary>${esc(title)}</summary>`
      + `<div class="parcel-omi-table-wrap"><table class="parcel-omi-table" aria-label="${esc(title)}">`
      + `<thead><tr>${labels.map((label) => `<th scope="col">${esc(tr(label))}</th>`).join('')}</tr></thead>`
      + `<tbody>${body}</tbody></table></div></details>`;
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
      const metrics = quote.derived_metrics || {};
      const yieldPct = Core.toNumber(metrics.gross_rental_yield_pct);
      const percentile = Core.toNumber(metrics.zone_percentile_pct);
      const comparableZones = Core.toNumber(metrics.zone_percentile_comparable_zones);
      const quoteBody = rows([
        [tr('Sale'), `${Core.formatNumber(quote.prezzo_min)}–${Core.formatNumber(quote.prezzo_max)} €/m²${benchmarkInline(benchmark, (value) => `${Core.formatNumber(value)} €/m²`, tr)}`],
        [tr('Rent'), Core.toNumber(quote.locazione_min) !== null ? `${Core.formatNumber(quote.locazione_min, 2)}–${Core.formatNumber(quote.locazione_max, 2)} €/m²/${esc(tr('month'))}` : null],
        [tr('Gross rental yield'), yieldPct !== null ? `${Core.formatNumber(yieldPct, 2)}% <small>${esc(metrics.gross_rental_yield_model_version || '')}</small>` : null],
        [tr('Sale-price percentile in comune'), percentile !== null
          ? `${Core.formatNumber(percentile, 1)}% <small>${esc(metrics.zone_percentile_model_version || '')} · ${esc(tr('{n} comparable zones', { n: comparableZones }))}</small>`
          : comparableZones !== null && comparableZones < (metrics.zone_percentile_minimum_comparable_zones || 5)
            ? tr('Not enough comparable zones ({n} found; at least {minimum} needed)', { n: comparableZones, minimum: metrics.zone_percentile_minimum_comparable_zones || 5 })
            : null],
      ]);
      quoteRows.innerHTML = quoteBody + (yieldPct !== null || percentile !== null || comparableZones !== null
        ? note(tr('Gross yield = rent midpoint × 12 ÷ sale midpoint × 100%, before costs and taxes. Percentile uses midranks of sale midpoints for the same OMI type and condition; ties share their middle rank.'))
        : '');
      ctx.setStat('price', `${Core.formatNumber(quote.prezzo_min)}–${Core.formatNumber(quote.prezzo_max)} €/m²`);
    }

    function showEstimate() {
      const quote = current();
      const range = Core.estimateOmiRange(quote, areaInput.value);
      estimate.innerHTML = range
        ? `<span>${esc(tr('Indicative value'))}</span><strong>${esc(Core.formatCurrency(range.min))} – ${esc(Core.formatCurrency(range.max))}</strong><small>${esc(tr('Local preview'))}</small>`
        : `<span>${esc(tr('Enter a valid surface to compute the estimate.'))}</span>`;
      return scheduleServerEstimate(quote, Number(areaInput.value));
    }

    function scheduleServerEstimate(quote, area) {
      const token = ++estimateToken;
      clearTimeout(estimateTimer);
      if (estimateWaiter) estimateWaiter();
      estimateWaiter = null;
      if (!Number.isFinite(area) || area <= 0) return Promise.resolve();
      return new Promise((resolve) => {
        estimateWaiter = resolve;
        estimateTimer = setTimeout(async () => {
          try {
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
            const derived = result.data.derived_metrics || {};
            const priceIncome = Core.toNumber(derived.price_to_mean_taxable_income_years);
            estimate.innerHTML = `<span>${esc(tr('Indicative value'))}</span><strong>${esc(Core.formatCurrency(server.min))} – ${esc(Core.formatCurrency(server.max))}</strong>`
              + `<small>${esc(tr('Calculated and verified by the server'))}${versions ? ` · ${esc(versions)}` : ''}</small>`
              + (priceIncome !== null
                ? rows([[tr('Price to mean taxable income'), `${Core.formatNumber(priceIncome, 1)} ${esc(tr('years'))} <small>${esc(derived.price_to_income_model_version || '')}${derived.income_year ? ` · ${esc(tr('MEF {year}', { year: derived.income_year }))}` : ''}</small>`]])
                  + note(tr('Compares this indicative property value with one mean municipal taxpayer’s annual taxable income; it is not a household affordability measure.'))
                : '');
          } finally {
            if (estimateWaiter === resolve) estimateWaiter = null;
            resolve();
          }
        }, 450);
      });
    }

    async function showHistory() {
      const quote = current();
      const token = ++historyToken;
      history.innerHTML = `<p class="parcel-note">${esc(tr('Loading history…'))}</p>`;
      const query = new URLSearchParams({ comune: ctx.cadastralCode, zona: quote.zona || '' });
      if (quote.cod_tipologia != null) query.set('cod_tipologia', quote.cod_tipologia);
      if (quote.stato_conservazione) query.set('stato_conservazione', quote.stato_conservazione);
      const result = await ctx.fetchJson(`${API}/omi/history?${query}`);
      if (token !== historyToken || !alive()) return;
      const series = Core.omiHistorySeries(result.ok && result.data ? result.data.history : [], quote);
      if (!series.points.length) {
        history.innerHTML = note(tr('OMI history is not available for this selection.'));
        return;
      }
      const change = series.change === null ? '' : ` · ${series.change >= 0 ? '+' : ''}${Core.formatNumber(series.change, 1)}%`;
      const trendDeltas = (result.data && result.data.trend_deltas) || [];
      const horizonLabels = { 1: '6 months', 2: '1 year', 6: '3 years', 10: '5 years', 20: '10 years' };
      const trendRows = trendDeltas.map((item) => [
        tr(horizonLabels[item.horizon_semesters] || '{n} semesters', { n: item.horizon_semesters }),
        item.change_pct == null ? null : `${item.change_pct >= 0 ? '+' : ''}${Core.formatNumber(item.change_pct, 1)}% <small>${esc(item.from)} → ${esc(item.to)}</small>`,
      ]);
      history.innerHTML = `<h4>${esc(tr('Sale price history'))} <small>${esc(tr('{n} semesters', { n: series.points.length }))}${esc(change)}${trendDeltas.length ? ` · ${esc(result.data.trend_model_version || '')}` : ''}</small></h4>`
        + Core.historyChartSvg(series, tr('Trend of the mean OMI value'))
        + `<div class="parcel-chart-axis"><span>${esc(series.first.label)}</span><span>${esc(series.last.label)}</span></div>`
        + rows([[tr('Latest interval'), `${Core.formatNumber(series.last.min)}–${Core.formatNumber(series.last.max)} €/m²`], ...trendRows])
        + (trendRows.length ? note(tr('Trend change = (latest sale-band midpoint ÷ comparison-period sale-band midpoint − 1) × 100%. The compared periods are shown.')) : '');
    }

    let estimateWaiter = null;
    select.addEventListener('change', () => { showQuote(); showEstimate(); showHistory(); });
    areaInput.addEventListener('input', showEstimate);
    showQuote();
    const initialEstimate = showEstimate();
    const initialHistory = showHistory();
    return Promise.all([initialEstimate, initialHistory]);
  }

  // ---- Panel (DOM) -----------------------------------------------------------------------

  const view = {
    mounted: false, token: 0, controller: null, observers: [], ctx: null,
    readModel: undefined, readModelWaiters: [], sections: new Map(), data: new Map(),
    pending: new Map(), metadata: new Map(),
  };

  function stripCell(key, label) {
    return `<div class="parcel-stat" data-stat="${key}"><span class="parcel-stat-label">${esc(label)}</span><strong class="parcel-stat-value">—</strong></div>`;
  }

  function sectionShell(section, tr) {
    return `<details class="parcel-section" id="parcel-section-${section.id}" data-section="${section.id}" data-tab="${section.tab}" data-state="idle" aria-labelledby="parcel-section-${section.id}-title" aria-busy="false" open>
      <summary class="parcel-section-header"><i class="fas fa-${section.icon}" aria-hidden="true"></i><span class="parcel-section-title" id="parcel-section-${section.id}-title">${esc(tr(section.title))}</span><span class="parcel-section-badge" hidden></span></summary>
      <div class="parcel-section-body"></div>
      <div class="parcel-section-footer" hidden></div>
    </details>`;
  }

  function makeContext(feature, options, tr) {
    const controller = new AbortController();
    const token = ++view.token;
    const props = (feature && feature.properties) || {};
    const reference = options.reference || null;
    const featureId = options.featureId ?? (feature && feature.id) ?? props.id ?? null;
    const memo = new Map();
    const ctx = {
      tr, token, feature, props, reference, featureId,
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
    ctx.sisterParcelRecords = () => ctx.memo('sister-parcel-records', () => {
      if (!reference) return Promise.resolve({ ok: false, status: 0, data: null });
      return ctx.fetchJson(`${API}/parcel/buildings/${encodeURIComponent(reference)}`);
    });
    ctx.municipality = () => ctx.memo('municipality', async () => {
      if (!ctx.cadastralCode) return null;
      const result = await ctx.fetchJson(`${API}/municipality/${encodeURIComponent(ctx.cadastralCode)}`);
      return result.ok ? result.data : null;
    });
    ctx.zoneMatch = () => ctx.memo('zone', async () => {
      const municipality = await ctx.municipality();
      if (!municipality || !municipality.province || !ctx.centroid) return null;
      const query = new URLSearchParams({ province: municipality.province, lat: ctx.centroid.lat, lng: ctx.centroid.lng });
      if (ctx.cadastralCode) query.set('comune', ctx.cadastralCode);
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
    view.metadata.set(section.id, result.meta || {});
    const metaHtml = result.meta ? Core.blockMetadataHtml(result.meta, ctx.tr) : '';
    footer.innerHTML = metaHtml;
    footer.hidden = !metaHtml;
    count.textContent = result.badge != null ? String(result.badge) : '';
    count.hidden = result.badge == null;
    setState(el, result.empty ? 'empty' : 'ready');
  }

  async function loadSection(section, el, ctx) {
    const pending = view.pending.get(section.id);
    if (pending) return pending;
    if (['ready', 'empty'].includes(el.dataset.state)) return;
    const task = (async () => {
      setState(el, 'loading');
      el.querySelector('.parcel-section-body').innerHTML = `<div class="parcel-skeleton" aria-hidden="true"><span></span><span></span><span></span></div><span class="visually-hidden">${esc(ctx.tr('Loading…'))}</span>`;
      try {
        const data = await section.load(ctx);
        if (!ctx.isCurrent()) return;
        view.data.set(section.id, data);
        paint(el, section, section.render(data, ctx), ctx);
        if (section.bind) await section.bind(el, data, ctx);
      } catch (error) {
        if (!ctx.isCurrent()) return;
        if (error && error.status === 404) {
          // The source is not provisioned on this server: retrying cannot help.
          setState(el, 'empty');
          el.querySelector('.parcel-section-body').innerHTML = note(failureMessage(404, ctx.tr));
          return;
        }
        setState(el, 'error');
        const retryLabel = ctx.tr('Retry');
        el.querySelector('.parcel-section-body').innerHTML = `<p class="parcel-note" role="status">${esc(failureMessage(error && error.status, ctx.tr))}</p>`
          + `<button type="button" class="parcel-retry secondary-action">${esc(retryLabel)}</button>`;
        el.querySelector('.parcel-retry').addEventListener('click', () => {
          setState(el, 'idle');
          void loadSection(section, el, ctx);
        });
      }
    })();
    view.pending.set(section.id, task);
    try { await task; }
    finally { if (view.pending.get(section.id) === task) view.pending.delete(section.id); }
  }

  function reportSectionText(body) {
    const clone = body.cloneNode(true);
    clone.querySelectorAll('details').forEach((details) => { details.open = true; });
    clone.querySelectorAll('svg[aria-label]').forEach((svg) => {
      svg.replaceWith(root.document.createTextNode(svg.getAttribute('aria-label')));
    });
    clone.querySelectorAll('select').forEach((select) => {
      const selected = [...select.options].filter((option) => option.selected).map((option) => option.textContent.trim()).join(', ');
      select.replaceWith(root.document.createTextNode(selected));
    });
    clone.querySelectorAll('input, textarea').forEach((input) => {
      input.replaceWith(root.document.createTextNode(input.value || ''));
    });
    // innerText preserves the visual row breaks from lists and tables. Render
    // off-screen so it remains independent from whether the source card is
    // currently collapsed, without exposing the clone to assistive tech.
    const host = root.document.createElement('div');
    host.setAttribute('aria-hidden', 'true');
    host.style.cssText = 'position:fixed;left:-20000px;top:0;width:800px;pointer-events:none;';
    host.append(clone);
    root.document.body.append(host);
    let renderedText = '';
    try { renderedText = clone.innerText || clone.textContent || ''; }
    finally { host.remove(); }
    return Array.from(renderedText.split(/\n+/).map((line) => line.replace(/[\t\r ]+/g, ' ').trim())
      .filter(Boolean).join('\n')).slice(0, 18000).join('');
  }

  async function createReportSnapshot(onProgress) {
    const ctx = view.ctx;
    if (!ctx || !ctx.reference) throw new Error('No parcel is selected.');
    const token = ctx.token;
    const entries = [...view.sections.values()];
    for (let offset = 0; offset < entries.length; offset += 4) {
      if (!ctx.isCurrent()) throw new Error('Parcel selection changed.');
      const batch = entries.slice(offset, offset + 4);
      await Promise.all(batch.map(({ section, el }) => loadSection(section, el, ctx)));
      if (typeof onProgress === 'function') onProgress(Math.min(offset + batch.length, entries.length), entries.length);
    }
    if (!ctx.isCurrent() || token !== view.token) throw new Error('Parcel selection changed.');
    const snapshot = {
      sections: entries.map(({ section, el }) => ({
        id: section.id,
        title: section.title,
        state: el.dataset.state || 'empty',
        body: reportSectionText(el.querySelector('.parcel-section-body')),
        metadata: Object.fromEntries(Object.entries(view.metadata.get(section.id) || {})
          .filter(([key, value]) => REPORT_METADATA_KEYS.has(key) && ['string', 'number', 'boolean'].includes(typeof value))
          .slice(0, 10)
          .map(([key, value]) => [key, String(value).slice(0, 160)])),
      })),
    };
    const jsonSize = () => new root.Blob([JSON.stringify(snapshot)]).size;
    const bodySize = () => snapshot.sections.reduce((total, section) => total + section.body.length, 0);
    const notice = ctx.tr('Section text was shortened to fit the PDF request limit.');
    const marker = `\n${notice}`;
    const markerLength = Array.from(marker).length;
    while (bodySize() > REPORT_TEXT_LIMIT || jsonSize() > REPORT_JSON_LIMIT) {
      const candidates = snapshot.sections.map((section) => ({
        section,
        content: section.body.endsWith(marker) ? section.body.slice(0, -marker.length) : section.body,
      })).filter((item) => item.content.length)
        .sort((a, b) => new root.Blob([b.content]).size - new root.Blob([a.content]).size);
      const candidate = candidates[0];
      if (!candidate) throw new Error('Parcel report snapshot exceeds the size limit.');
      const { section, content } = candidate;
      const points = Array.from(content);
      const averageBytes = new root.Blob([content]).size / Math.max(points.length, 1);
      const byteExcess = Math.max(0, jsonSize() - REPORT_JSON_LIMIT);
      const textExcess = Math.max(0, bodySize() - REPORT_TEXT_LIMIT);
      const sectionCount = Math.max(candidates.length, 1);
      const removeCount = Math.min(points.length, Math.max(64,
        Math.ceil(byteExcess / sectionCount / Math.max(averageBytes, 1))
          + Math.ceil(textExcess / sectionCount) + 32));
      const keepCount = Math.min(18000 - markerLength, Math.max(0, points.length - removeCount));
      section.body = `${points.slice(0, keepCount).join('').trimEnd()}${marker}`;
    }
    return snapshot;
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
      if (visible) {
        const tabId = visible.target.dataset.tab;
        setActiveTab(tabId);
        updateTabHash(tabId);
      }
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

  function tabFromHash() {
    const raw = root.location && root.location.hash ? root.location.hash.slice(1) : '';
    let key = raw;
    try { key = decodeURIComponent(raw); } catch (_) { /* keep the raw fragment */ }
    return HASH_TABS[String(key).toLowerCase()] || null;
  }

  function updateTabHash(tabId, { push = false } = {}) {
    if (!root.location || !root.history || !root.history.replaceState) return;
    const hash = TAB_HASHES[tabId];
    if (!hash || root.location.hash === `#${hash}`) return;
    const url = `${root.location.pathname}${root.location.search}#${hash}`;
    if (push && root.history.pushState) root.history.pushState(null, '', url);
    else root.history.replaceState(null, '', url);
  }

  function navigateToTab(tabId, { updateHash = false, pushHash = false, behavior = 'auto' } = {}) {
    const first = view.content && view.content.querySelector(`.parcel-section[data-tab="${tabId}"]`);
    if (!first) return false;
    if (typeof first.scrollIntoView === 'function') first.scrollIntoView({ block: 'start', behavior });
    else if (view.content) view.content.scrollTop = first.offsetTop || 0;
    setActiveTab(tabId);
    if (updateHash) updateTabHash(tabId, { push: pushHash });
    return true;
  }

  function restoreTabFromHash() {
    const tabId = tabFromHash();
    if (tabId) navigateToTab(tabId);
  }

  function renderNav(tr) {
    view.nav.innerHTML = TABS.map((tab) => `<button type="button" data-tab="${tab.id}">${esc(tr(tab.title))}</button>`).join('');
    view.nav.querySelectorAll('button').forEach((button) => button.addEventListener('click', () => {
      const reduce = root.matchMedia && root.matchMedia('(prefers-reduced-motion: reduce)').matches;
      navigateToTab(button.dataset.tab, { updateHash: true, pushHash: true, behavior: reduce ? 'auto' : 'smooth' });
    }));
  }

  function mount({ content, nav, strip, tr }) {
    view.content = content;
    view.nav = nav;
    view.statStrip = strip;
    view.tr = tr || ((key, values) => Core.translate((k) => k, key, values));
    view.mounted = true;
    if (!view.hashListenerBound && typeof root.addEventListener === 'function') {
      root.addEventListener('hashchange', restoreTabFromHash);
      root.addEventListener('popstate', restoreTabFromHash);
      view.hashListenerBound = true;
    }
  }

  function show(feature, options = {}) {
    if (!view.mounted) return;
    const tr = view.tr;
    if (view.controller) view.controller.abort();
    disconnectObservers();
    view.readModel = undefined;
    view.readModelWaiters = [];
    view.data = new Map();
    view.pending = new Map();
    view.metadata = new Map();
    view.sections = new Map();
    const made = makeContext(feature, options, tr);
    view.controller = made.controller;
    view.ctx = made.ctx;
    const area = Core.parcelAreaSqm(made.ctx.props);
    view.statStrip.innerHTML = stripCell('area', area && area.computed ? tr('Area (computed)') : tr('Area'))
      + stripCell('sheet', tr('Sheet')) + stripCell('buildings', tr('SISTER records'))
      + stripCell('price', tr('Sale price (OMI)')) + stripCell('seismic', tr('Seismic zone'));
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
    restoreTabFromHash();
  }

  /** Hand over the parcel read model (or {unavailable: true}); refreshes dependent sections. */
  function setReadModel(model) {
    if (!view.mounted || !view.ctx) return;
    view.readModel = model || { unavailable: true };
    const waiters = view.readModelWaiters || [];
    view.readModelWaiters = [];
    waiters.forEach((resolve) => resolve(view.readModel));
    // Sections already painted from block-dependent data pick up provenance.
    ['identity', 'census', 'solar'].forEach((id) => {
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

  return { mount, show, setReadModel, clear, createReportSnapshot, sections: SECTIONS, tabs: TABS, coverageBlocks: COVERAGE_BLOCKS };
}));
