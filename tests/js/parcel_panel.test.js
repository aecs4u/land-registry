'use strict';
// Unit tests for the parcel panel's pure logic and DOM-free section renderers.
// Run with: node --test tests/js   (pytest runs this via test_parcel_panel_js.py)
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const STATIC = path.join(__dirname, '..', '..', 'land_registry', 'static');
const Core = require(path.join(STATIC, 'parcel-panel-core.js'));
const Panel = require(path.join(STATIC, 'parcel-panel.js'));

const tr = (key, values) => Core.translate((k) => k, key, values);

function makeCtx(overrides = {}) {
  const props = overrides.props || {
    municipality_code: 'H501', municipality_name: 'ROMA', province: 'RM', region: 'LAZIO',
    sheet_number: '486', parcel_number: 'D', national_cadastral_reference: 'H501A048600.D', area_sqm: 5545.17,
    centroid_lat: 41.8986, centroid_lng: 12.4769,
  };
  return {
    tr, props, feature: { properties: props, geometry: { type: 'Point', coordinates: [12.4769, 41.8986] } },
    reference: 'H501A048600.D', cadastralCode: 'H501', centroid: { lat: 41.8986, lng: 12.4769 },
    block: () => null, isCurrent: () => true, setStat: () => {},
    municipality: async () => ({ name: 'Roma', province: 'Roma', istat_code: '058091' }),
    zoneMatch: async () => null,
    fetchJson: async () => ({ ok: true, status: 200, data: {} }),
    ...overrides,
  };
}
const section = (id) => Panel.sections.find((item) => item.id === id);

// ---- Core ------------------------------------------------------------------

test('formatters use Italian number formatting and tolerate missing values', () => {
  // Italian CLDR does not group four-digit numbers; it groups from five digits.
  assert.equal(Core.formatNumber(1234.56, 1), '1234,6');
  assert.equal(Core.formatNumber(12345.67, 1), '12.345,7');
  assert.equal(Core.formatNumber(null), '—');
  assert.equal(Core.formatNumber('abc'), '—');
  assert.equal(Core.formatRatio(0.1234), '12,3%');
  assert.equal(Core.formatRatio(undefined), '—');
  assert.match(Core.formatCurrency(684000), /684\.000/);
  assert.equal(Core.formatArea(3764.85), '3764,9 m²');
  assert.equal(Core.formatConfidence(0.8), '80%');
  assert.equal(Core.formatConfidence(''), '');
});

test('escapeHtml neutralises markup and quotes', () => {
  assert.equal(Core.escapeHtml('<img src=x onerror="a">\''), '&lt;img src=x onerror=&quot;a&quot;&gt;&#39;');
});

test('cadastral identity helpers', () => {
  assert.equal(Core.cadastralCodeOf({ municipality_code: 'h501' }, null), 'H501');
  assert.equal(Core.cadastralCodeOf({}, 'c773_0020.846'), 'C773');
  assert.equal(Core.cadastralCodeOf({}, ''), null);
  assert.deepEqual(Core.referenceParts({ sheet_number: '486', parcel_number: 'D' }, 'H501A048600.D'),
    { municipality: 'H501', sheet: '486', parcel: 'D' });
  // Tile features name the fields sheet/parcel and carry references without an underscore.
  assert.deepEqual(Core.referenceParts({ sheet: '486', parcel: 'D' }, 'H501A048600.D'), { municipality: 'H501', sheet: '486', parcel: 'D' });
  assert.equal(Core.referenceParts({}, 'H501A048600.D').sheet, '');
  assert.deepEqual(Core.referenceParts({}, 'C773_0020.846/2'), { municipality: 'C773', sheet: '0020', parcel: '846/2' });
  assert.deepEqual(Core.parcelAreaSqm({ computed_area_sqm: 120 }), { value: 120, computed: true });
  assert.deepEqual(Core.parcelAreaSqm({ area_sqm: 50 }), { value: 50, computed: false });
  assert.deepEqual(Core.parcelAreaSqm({ area_ha: 2 }), { value: 20000, computed: false });
  assert.equal(Core.parcelAreaSqm({ area_sqm: 0 }), null);
  assert.deepEqual(Core.centroidOf({ centroid_lat: 1, centroid_lng: 2 }, null), { lat: 1, lng: 2 });
  assert.deepEqual(Core.centroidOf({}, { type: 'Polygon', coordinates: [[[10, 40], [12, 40], [12, 42], [10, 42], [10, 40]]] }), { lat: 41, lng: 11 });
  assert.equal(Core.centroidOf({}, null), null);
});

test('validOmiQuotes drops invalid intervals and puts the detected zone first', () => {
  const quotes = [
    { zona: 'C1', prezzo_min: 3000, prezzo_max: 4000 },
    { zona: 'B31', prezzo_min: 7400, prezzo_max: 9700 },
    { zona: 'B31', prezzo_min: 9000, prezzo_max: 8000 },
    { zona: 'X', prezzo_min: null, prezzo_max: 10 },
  ];
  const result = Core.validOmiQuotes({ quotes }, { matched: true, zone: 'b31' });
  assert.deepEqual(result.map((quote) => quote.zona), ['B31', 'C1']);
  assert.equal(Core.validOmiQuotes({ quotes }, null).length, 2);
  assert.deepEqual(Core.validOmiQuotes(null, null), []);
});

test('estimateOmiRange multiplies surface by the quote bounds and rejects bad input', () => {
  const quote = { prezzo_min: 7400, prezzo_max: 9700 };
  assert.deepEqual(Core.estimateOmiRange(quote, 80), { min: 592000, max: 776000 });
  assert.equal(Core.estimateOmiRange(quote, 0), null);
  assert.equal(Core.estimateOmiRange(quote, ''), null);
  assert.equal(Core.estimateOmiRange({ prezzo_min: 9, prezzo_max: 5 }, 10), null);
  assert.equal(Core.estimateOmiRange(null, 10), null);
});

test('defaultEstimateArea asks for the building surface on large parcels', () => {
  assert.deepEqual(Core.defaultEstimateArea(3764.85), { value: 3765, tooLarge: false });
  assert.deepEqual(Core.defaultEstimateArea(10000), { value: 10000, tooLarge: false });
  assert.deepEqual(Core.defaultEstimateArea(10001), { value: '', tooLarge: true });
  assert.deepEqual(Core.defaultEstimateArea(null), { value: '', tooLarge: false });
});

test('quoteBenchmark is the median of midpoints with the latest year', () => {
  const benchmark = Core.quoteBenchmark([
    { prezzo_min: 1000, prezzo_max: 2000, anno: 2024 },
    { prezzo_min: 3000, prezzo_max: 4000, anno: 2025 },
    { prezzo_min: 5000, prezzo_max: 6000, anno: 2025 },
  ]);
  assert.equal(benchmark.value, 3500);
  assert.equal(benchmark.year, 2025);
  assert.equal(Core.quoteBenchmark([]), null);
});

test('omiHistorySeries filters by state, keeps the last N and reports the change', () => {
  const history = [];
  for (let i = 0; i < 30; i += 1) history.push({ anno: 2015 + Math.floor(i / 2), semestre: (i % 2) + 1, prezzo_min: 1000 + i * 10, prezzo_max: 2000 + i * 10, stato_conservazione: 'NORMALE' });
  history.push({ anno: 2030, semestre: 1, prezzo_min: 1, prezzo_max: 2, stato_conservazione: 'SCADENTE' });
  history.push({ anno: 2031, semestre: 1, prezzo_min: 'x', prezzo_max: 2, stato_conservazione: 'NORMALE' });
  const series = Core.omiHistorySeries(history, { stato_conservazione: 'NORMALE' }, 24);
  assert.equal(series.points.length, 24);
  assert.equal(series.last.label, '2029 S2');
  assert.ok(series.change > 0);
  assert.deepEqual(Core.omiHistorySeries([], null).points, []);
});

test('historyChartSvg draws a band and line, and is empty without points', () => {
  const series = Core.omiHistorySeries([
    { anno: 2024, semestre: 1, prezzo_min: 100, prezzo_max: 200 },
    { anno: 2024, semestre: 2, prezzo_min: 110, prezzo_max: 230 },
  ], null);
  const svg = Core.historyChartSvg(series, 'Trend "x"');
  assert.match(svg, /<polygon class="parcel-chart-band"/);
  assert.match(svg, /<polyline class="parcel-chart-line"/);
  assert.match(svg, /aria-label="Trend &quot;x&quot;"/);
  assert.equal(Core.historyChartSvg({ points: [] }), '');
  // A single point must not divide by zero.
  assert.doesNotMatch(Core.historyChartSvg(Core.omiHistorySeries([{ anno: 2024, semestre: 1, prezzo_min: 5, prezzo_max: 5 }], null)), /NaN/);
});

test('risk, bulletin and indicator helpers', () => {
  assert.equal(Core.riskLevel(5), 'high');
  assert.equal(Core.riskLevel(1), 'medium');
  assert.equal(Core.riskLevel(0.2), 'low');
  assert.equal(Core.riskLevel(null), 'unknown');
  assert.equal(Core.riskLevel('n/a'), 'unknown');
  assert.equal(Core.seismicLabel(1, tr), 'Zone 1 (high seismicity)');
  assert.equal(Core.seismicLabel(3, tr), 'Zone 3');
  assert.equal(Core.bulletinSeverity('Allerta ROSSA'), 'red');
  assert.equal(Core.bulletinSeverity('arancione'), 'orange');
  assert.equal(Core.bulletinSeverity('Gialla'), 'yellow');
  assert.equal(Core.bulletinSeverity('nessuna'), 'none');
  const bulletin = { today_zones: { objects: { a: { geometries: [{ properties: { Comuni: ["Sant'Egidio alla Vibrata", 'Forlì'], 'Nome zona': 'Z1' } }] } } } };
  assert.equal(Core.findBulletinZone(bulletin, 'forli')['Nome zona'], 'Z1');
  assert.equal(Core.findBulletinZone(bulletin, 'Roma'), null);
  assert.equal(Core.findBulletinZone(null, 'Roma'), null);
  assert.equal(Core.indicatorLabel('life_expectancy-at-birth'), 'Life Expectancy At Birth');
});

test('census summary derives density and keeps the supplied ratios', () => {
  const feature = { properties: { sez21_id: 'S1', p1: 200, pf1: 80, a8: 90, e3: 12, area_sqm: 500000, ratios: { vacancy_rate: 0.1 } } };
  const summary = Core.censusSummary(feature, null, { value: 196 });
  assert.equal(summary.density, 400);
  assert.equal(summary.households, 80);
  assert.equal(summary.ratios.vacancy_rate, 0.1);
  assert.equal(summary.benchmark.value, 196);
  assert.equal(Core.censusSummary(null, null), null);
});

test('census demographics require complete ISTAT 2021 sex and age fields', () => {
  const properties = { p1: 101, p2: 49, p3: 52 };
  for (let key = 14; key <= 29; key += 1) properties[`p${key}`] = key - 13;
  for (let key = 30; key <= 45; key += 1) properties[`p${key}`] = key - 29;
  for (let key = 67; key <= 82; key += 1) properties[`p${key}`] = key - 66;
  const result = Core.censusDemographics({ properties });
  assert.equal(result.total, 101);
  assert.equal(result.male, 49);
  assert.equal(result.female, 52);
  assert.deepEqual(result.ageGroups[0], { label: 'Under 5', total: 1, male: 1, female: 1 });
  assert.deepEqual(result.ageGroups.at(-1), { label: '75+', total: 16, male: 16, female: 16 });
  assert.equal(Core.censusDemographics({ properties: { p1: 101, p2: 49, p3: 52 } }), null);
});

test('income bracket rows clamp to 0..100', () => {
  const result = Core.incomeBracketRows([{ bracket: '0-10k', pct: 140 }, { bracket: 'x', pct: null }, { bracket: 'y', pct: -3 }]);
  assert.deepEqual(result.map((row) => row.pct), [100, null, 0]);
});

test('time helpers', () => {
  const now = new Date('2026-10-09T12:00:00Z');
  assert.equal(Core.relativeAge('2026-10-09T11:59:40Z', tr, now), 'less than 1 min ago');
  assert.equal(Core.relativeAge('2026-10-09T11:00:00Z', tr, now), '60 min ago');
  assert.equal(Core.relativeAge('2026-10-09T05:00:00Z', tr, now), '7 hours ago');
  assert.equal(Core.relativeAge('2026-10-01T12:00:00Z', tr, now), '8 days ago');
  assert.equal(Core.relativeAge('garbage', tr, now), '');
  assert.equal(Core.parseFireObservationTime({ acq_date: '2026-10-09', acq_time: '0930' }).toISOString(), '2026-10-09T09:30:00.000Z');
  assert.equal(Core.parseFireObservationTime({ acq_date: '2026-10-09' }).toISOString(), '2026-10-09T00:00:00.000Z');
  assert.equal(Core.parseFireObservationTime({}), null);
  assert.equal(Core.feedRefreshValue({ fetched_at: 'x' }), 'x');
  assert.equal(Core.feedRefreshValue({}), null);
});

test('records: binary payloads are hidden and only http(s) links survive', () => {
  const rows = Core.flattenValues({ a: 1, pdf: 'AAAA', nested: { content_base64: 'zzz', b: 'two' }, list: [{ c: 3 }] });
  const flat = Object.fromEntries(rows);
  assert.equal(flat.a, '1');
  assert.equal(flat['nested · b'], 'two');
  assert.equal(flat['list[1] · c'], '3');
  assert.ok(!Object.keys(flat).some((key) => /pdf|base64/i.test(key)));
  assert.equal(Core.safeHttpUrl('javascript:alert(1)'), null);
  assert.equal(Core.safeHttpUrl('data:text/html,x'), null);
  assert.equal(Core.safeHttpUrl('https://example.org/a b'), 'https://example.org/a%20b');
  assert.equal(Core.safeHttpUrl('not a url'), null);
});

test('blockMetadataHtml shows only what the source supplies and escapes it', () => {
  assert.equal(Core.blockMetadataHtml(null, tr), '');
  assert.equal(Core.blockMetadataHtml({}, tr), '');
  const html = Core.blockMetadataHtml({
    source: 'A <b>', dataset_version: 'v1', model_version: 'm1', match_method: 'centroid',
    confidence: 0.5, spatial_resolution: 'section', spatial_resolution_m: 30,
    updated_at: '2026-09-30',
    benchmarks: { d: { label: 'Italy', year: 2021, value: 196, unit: 'x/km²' }, empty: { value: null } },
  }, tr);
  assert.match(html, /Source: A &lt;b&gt;/);
  assert.match(html, /Dataset: v1/);
  assert.match(html, /Model: m1/);
  assert.match(html, /50%/);
  assert.match(html, /30 m/);
  assert.match(html, /Last updated.*2026-09-30/);
  assert.match(html, /parcel-block-benchmarks/);
  assert.equal((html.match(/Benchmark/g) || []).length, 1);
});

// ---- Sections --------------------------------------------------------------

test('every section renders an empty state for null data without throwing', () => {
  for (const item of Panel.sections) {
    const ctx = makeCtx();
    const data = item.id === 'bulletin' ? { bulletin: null, name: null } : null;
    const result = item.render(data, ctx);
    assert.equal(typeof result.body, 'string', item.id);
    assert.ok(result.body.length > 0, item.id);
  }
});

test('every section declares a known tab, a title and an icon', () => {
  const tabs = new Set(Panel.tabs.map((tab) => tab.id));
  const ids = new Set();
  for (const item of Panel.sections) {
    assert.ok(tabs.has(item.tab), item.id);
    assert.ok(item.title && item.icon, item.id);
    assert.ok(!ids.has(item.id), `duplicate ${item.id}`);
    ids.add(item.id);
  }
  for (const tab of Panel.tabs) assert.ok(Panel.sections.some((item) => item.tab === tab.id), `empty tab ${tab.id}`);
});

test('identity escapes feature properties and reports a computed area', () => {
  const props = { municipality_name: '<script>x</script>', parcel_number: '"><img src=x>', computed_area_sqm: 100, sheet_number: '1' };
  const result = section('identity').render({ properties: props, geometry: null }, makeCtx({ props, reference: 'R<1>' }));
  assert.doesNotMatch(result.body, /<script>|<img/);
  assert.match(result.body, /&lt;script&gt;/);
  assert.match(result.body, /computed from the geometry/);
  assert.match(result.body, /Unavailable for this record/);
});

test('address section escapes bounded SISTER addresses and explains its match scope', () => {
  const result = section('address').render({
    addresses: ['Via <Roma> 1'], address_count: 12, addresses_truncated: true,
    address_source: 'SISTER SQLite visura_properties.address',
  }, makeCtx());

  assert.equal(result.badge, 12);
  assert.match(result.body, /Via &lt;Roma&gt; 1/);
  assert.match(result.body, /not a geocoded address register/);
  assert.match(result.body, /Showing 1 of 12 addresses/);
  assert.equal(result.meta.match_method, 'cadastral_reference');
  assert.equal(section('address').render({ unresolved: true }, makeCtx()).empty, true);
});

test('omi renders zone states, a surface prompt for large parcels and the quote options', () => {
  const quotes = [
    { zona: 'B31', cod_tipologia: '20', tipologia: 'Abitazioni civili', stato_conservazione: 'NORMALE', prezzo_min: 7400, prezzo_max: 9700, anno: 2025, semestre: 2 },
    { zona: 'C1', cod_tipologia: '20', tipologia: 'Abitazioni civili', stato_conservazione: 'NORMALE', prezzo_min: 3000, prezzo_max: 4000, anno: 2025, semestre: 2 },
    { zona: 'A1', cod_tipologia: '20', tipologia: 'Abitazioni civili', stato_conservazione: 'NORMALE', locazione_min: 11, locazione_max: 14, anno: 2025, semestre: 2 },
  ];
  const detected = section('omi').render({ quotes, zone: { matched: true, zone: 'B31' }, source: 'AdE OMI', dataset_version: '2025/2' }, makeCtx());
  assert.match(detected.body, /OMI zone detected automatically/);
  assert.match(detected.body, /<option value="0">Zone B31/);
  assert.match(detected.body, /All OMI quotes for this municipality \(3\)/);
  assert.match(detected.body, /<td>B31<\/td><td>Abitazioni civili<\/td>/);
  assert.ok(detected.body.includes('Sale range (€/m²)'));
  assert.match(detected.body, /<td>A1<\/td><td>Abitazioni civili<\/td><td>NORMALE<\/td><td>—<\/td><td>11–14<\/td>/);
  assert.equal(detected.badge, 3);
  assert.equal(detected.meta.dataset_version, '2025/2');
  assert.match(detected.body, /value="5545"/);

  const noQuotesForZone = section('omi').render({ quotes, zone: { matched: true, zone: 'ZZ' } }, makeCtx());
  assert.match(noQuotesForZone.body, /Zone ZZ was detected but has no quotes available/);

  const undetected = section('omi').render({ quotes, zone: null }, makeCtx());
  assert.match(undetected.body, /was not detected automatically/);

  const large = section('omi').render({ quotes, zone: null }, makeCtx({ props: { area_sqm: 50000, sheet_number: '1' } }));
  assert.match(large.body, /enter the commercial surface/);
  assert.match(large.body, /value=""/);

  assert.equal(section('omi').render({ quotes: [] }, makeCtx()).empty, true);
  assert.equal(section('omi').render({ quotes: [{ zona: 'A', prezzo_min: 5, prezzo_max: 1 }] }, makeCtx()).empty, true);
  assert.equal(section('omi').render(null, makeCtx()).empty, true);
});

test('omi load throws with the HTTP status so the UI can explain it', async () => {
  const ctx = makeCtx({ fetchJson: async () => ({ ok: false, status: 503, data: null }) });
  await assert.rejects(() => section('omi').load(ctx), (error) => error.status === 503);
  const ok = makeCtx({
    fetchJson: async (url) => ({ ok: true, status: 200, data: { quotes: [{ zona: 'A' }], url } }),
    zoneMatch: async () => ({ matched: true, zone: 'A' }),
  });
  const data = await section('omi').load(ok);
  assert.equal(data.zone.zone, 'A');
  assert.match(data.url, /comune=H501/);
  assert.equal(await section('omi').load(makeCtx({ cadastralCode: null })), null);
});

test('pvp only links http(s) listings and keeps nearby counts separate', async () => {
  const ctx = makeCtx();
  const html = section('pvp').render({
    matchData: {
      source: 'PVP',
      records: [
        { source_url: 'javascript:alert(1)', sale_id: 1, minimum_offer: 1000 },
        { source_url: 'https://pvp.example/1', base_auction_price: 5000, street: 'Via X', house_number: '1' },
      ],
    },
    matchAvailable: true,
    nearbyPoints: [],
    nearbySource: null,
    nearbyError: false,
    centroid: null,
  }, ctx);
  assert.equal((html.body.match(/<a href=/g) || []).length, 1);
  assert.doesNotMatch(html.body, /javascript:/);
  assert.equal(html.badge, 0);
  const nearbyOnly = section('pvp').render({
    matchAvailable: false, matchData: null, nearbyPoints: [], nearbySource: null,
    nearbyError: false, centroid: ctx.centroid,
  }, ctx);
  assert.match(nearbyOnly.body, /Distance is measured in a straight line/);
  assert.doesNotMatch(nearbyOnly.body, /Listings matched to this parcel/);
  const calls = [];
  const unresolved = makeCtx({
    municipality: async () => ({ name: 'X' }),
    fetchJson: async (url) => { calls.push(url); return { ok: true, status: 200, data: {} }; },
  });
  const result = await section('pvp').load(unresolved);
  assert.equal(result.matchAvailable, false);
  assert.equal(calls.some((url) => url.includes('/parcel/pvp?')), false);
});

test('risks are labelled as municipality-level and graded', () => {
  const html = section('risks').render({
    seismic: { zone: 3 },
    pga: { available: true, matched: true, pga_g: 0.123, pga_p16_g: 0.101, pga_p84_g: 0.147 },
    hydrogeological: { flood: { area_pct: { P3_high_probability: 6.2 } }, landslide: { area_pct: { P4_very_high: 0.4 } } },
  }, makeCtx());
  assert.match(html.body, /whole municipality/);
  assert.match(html.body, /PGA source: INGV MPS04 under CC BY 4\.0/);
  assert.match(html.body, /data-level="high"/);
  assert.match(html.body, /data-level="low"/);
  assert.match(html.body, /Zone 3/);
  assert.match(html.body, /0,123 g \(0,101–0,147 g\)/);
  assert.equal(html.meta.spatial_resolution, 'municipality plus parcel-centroid nearest grid point');
  assert.equal(html.meta.additional_source, 'INGV MPS04 seismic hazard model');
  assert.equal(html.meta.additional_model_version, 'MPS04');
  assert.equal(section('risks').render({}, makeCtx()).empty, true);
  assert.equal(section('risks').render({ unresolved: true }, makeCtx()).empty, true);
});

test('risks load municipality data and parcel-centroid PGA independently', async () => {
  const calls = [];
  const ctx = makeCtx({
    fetchJson: async (url) => {
      calls.push(url);
      return url.includes('/mps04/pga')
        ? { ok: true, status: 200, data: { available: true, matched: true, pga_g: 0.08 } }
        : { ok: true, status: 200, data: { seismic: { zone: 2 } } };
    },
  });

  const result = await section('risks').load(ctx);

  assert.ok(calls.some((url) => url.includes('/risks/058091')));
  assert.ok(calls.some((url) => url.includes('/mps04/pga?lat=41.8986&lng=12.4769')));
  assert.equal(result.seismic.zone, 2);
  assert.equal(result.pga.pga_g, 0.08);
});

test('PGA distinguishes an unbuilt store from a point outside its match radius', () => {
  const render = (pga) => section('risks').render({ pga }, makeCtx()).body;
  assert.match(render({ available: false, reason: 'mps04_not_built' }), /Not available on this server/);
  assert.match(render({ available: true, matched: false }), /No MPS04 grid point was found within 5 km/);
});

test('municipal solar aggregates carry municipality provenance and are not framed as parcel estimates', () => {
  const data = {
    pv_n_buildings: 125,
    pv_pvout_pessimistic_kwh_year_total: 940000,
    pv_pvout_modern_kwh_year_total: 1210000,
    pv_high_viability_pct: 18.5,
    pv_medium_viability_pct: 32,
    pv_low_viability_pct: 21.5,
    pv_not_eligible_pct: 28,
  };
  const result = section('solar').render(null, makeCtx({
    block: (name) => name === 'solar' ? {
      available: true, data, source: 'aecs4u-stats serving.municipality_profile',
      spatial_resolution: 'municipality', match_method: 'municipality',
    } : null,
  }));

  assert.match(result.body, /Buildings included in the estimate/);
  assert.match(result.body, /940\.000 kWh\/year/);
  assert.match(result.body, /not estimates for this parcel, building, or roof/);
  assert.equal(result.meta.spatial_resolution, 'municipality');
  assert.equal(result.meta.match_method, 'municipality');
  assert.equal(section('solar').render(null, makeCtx()).empty, true);
});

test('risks bind pushes the seismic zone into the stat strip', () => {
  const stats = {};
  section('risks').bind({}, { seismic: { zone: 2 } }, makeCtx({ setStat: (key, value) => { stats[key] = value; } }));
  assert.equal(stats.seismic, 'Zone 2');
  section('risks').bind({}, { unresolved: true }, makeCtx({ setStat: () => assert.fail('must not set a stat') }));
});

test('bulletin handles stale, unmatched and matched zones', () => {
  const ctx = makeCtx();
  assert.match(section('bulletin').render({ bulletin: { stale: true }, name: 'Roma' }, ctx).body, /has expired/);
  const bulletin = { today_zones: { objects: { a: { geometries: [{ properties: { Comuni: ['Roma'], 'Nome zona': 'Lazio A', 'Per rischio idraulico': 'ARANCIONE', 'Per rischio temporali': 'x', 'Per rischio idrogeologico': 'GIALLA' } }] } } } };
  assert.match(section('bulletin').render({ bulletin, name: 'Milano' }, ctx).body, /not in today/);
  const matched = section('bulletin').render({ bulletin, name: 'ROMA' }, ctx);
  assert.match(matched.body, /Lazio A/);
  assert.match(matched.body, /Orange alert/);
  assert.match(matched.body, /Yellow alert/);
  assert.match(matched.body, /No alert/);
  assert.match(matched.body, /not declared by the provider/);
});

test('fires lists the most recent detections first and caps the list', () => {
  const detections = [];
  for (let i = 1; i <= 7; i += 1) detections.push({ acq_date: `2026-10-0${i}`, acq_time: '1200', frp: i });
  const html = section('fires').render({ count: 7, detections, source: 'FIRMS', fetched_at: '2026-10-09T10:00:00Z' }, makeCtx());
  assert.equal(html.badge, 7);
  assert.match(html.body, /\+ 2 more/);
  assert.ok(html.body.indexOf('7 MW') < html.body.indexOf('3 MW'));
  assert.match(section('fires').render({ count: 0, source: 'FIRMS' }, makeCtx()).body, /No active fire detected within 25 km/);
});

test('municipality links are restricted to safe URLs and valid emails', () => {
  const html = section('municipality').render({
    name: 'Roma', province: 'Roma', province_sigla: 'RM', istat_code: '058091',
    website: 'javascript:alert(1)', wikipedia_url: 'https://it.wikipedia.org/wiki/Roma',
    email: 'info@roma.it', pec_email: '"><script>@x', population: { year: 2024, resident_population: 2800000 },
    population_history: [{ year: 2023, resident_population: 1 }, { year: 2024, resident_population: 2 }],
  }, makeCtx());
  assert.doesNotMatch(html.body.replace(/&lt;script&gt;/g, ''), /<script>|javascript:/);
  assert.match(html.body, /mailto:info@roma\.it/);
  assert.match(html.body, /it\.wikipedia\.org/);
  assert.match(html.body, /Population history \(2 years\)/);
});

test('income renders bars and benchmark; census flags modelled values', () => {
  const income = section('income').render({
    taxpayers: 1000, mean_taxable_income_eur: 25000, income_distribution: [{ bracket: '0-10k', pct: 26.5 }], source: 'MEF',
    income_reference_averages: { nation: { mean_taxable_income_eur: 23456, name: 'Italy' } },
  }, makeCtx());
  assert.match(income.body, /parcel-bar/);
  assert.match(income.body, /National average \(Italy\)/);
  assert.equal(income.meta.spatial_resolution, 'municipality');

  const feature = { properties: { sez21_id: 'S1', p1: 100, area_sqm: 1e6, ratios: { employment_rate_working_age: 0.5 } } };
  const plain = section('census').render(feature, makeCtx());
  assert.match(plain.body, /Census section 2021/);
  assert.doesNotMatch(plain.body, /Modelled values/);
  const modelled = section('census').render(feature, makeCtx({ block: () => ({ confidence: 0.7, spatial_resolution: 'section' }) }));
  assert.match(modelled.body, /Modelled values/);
  assert.equal(section('census').render({ unresolved: true }, makeCtx()).empty, true);
});

test('census section shows a scoped sex split and age pyramid when ISTAT fields are present', () => {
  const props = { sez21_id: 'S1', p1: 100, p2: 48, p3: 52, area_sqm: 1e6 };
  for (let key = 14; key <= 29; key += 1) props[`p${key}`] = 2;
  for (let key = 30; key <= 45; key += 1) props[`p${key}`] = 1;
  for (let key = 67; key <= 82; key += 1) props[`p${key}`] = 1;
  const result = section('census').render({ properties: props }, makeCtx({ props }));
  assert.match(result.body, /Age and sex distribution \(Census 2021\)/);
  assert.match(result.body, /Male residents/);
  assert.match(result.body, /Female residents/);
  assert.match(result.body, /Under 5/);
  assert.match(result.body, /75\+/);
  assert.match(result.body, /not residents of this parcel/);
  assert.equal((result.body.match(/<tr><td class="parcel-age-male"/g) || []).length, 16);
});

test('indicator sections preview a few series and say they are province-level', async () => {
  const calls = [];
  const ctx = makeCtx({
    fetchJson: async (url) => {
      calls.push(url);
      if (/\/demographics\/H501$/.test(url)) return { ok: true, status: 200, data: { indicators: ['a', 'b', 'c', 'd', 'e'], source: 'ISTAT', nuts3: 'ITI43' } };
      return { ok: true, status: 200, data: { series: [{ year: 2023, value: 1 }, { year: 2024, value: 2.5 }] } };
    },
  });
  const data = await section('demographics').load(ctx);
  assert.equal(calls.length, 1 + 4);
  const html = section('demographics').render(data, ctx);
  assert.match(html.body, /Preview of 4 of 5/);
  assert.match(html.body, /ITI43/);
  assert.equal(section('demographics').render({ series: [{ code: 'a', latest: null }] }, ctx).empty, true);
});

test('socioeconomic fallbacks translate labels and interpolate their geographic scope', () => {
  const translations = {
    country: 'paese',
    Italy: 'Italia',
    'Resident Population': 'Popolazione residente',
    'Municipality-level indicators for {municipality}.': 'Indicatori a livello comunale per {municipality}.',
    'Country-level relocation indicators for {country}; these values do not describe the province or parcel.': 'Indicatori nazionali per {country}; questi valori non descrivono la provincia né la particella.',
    'Safety index': 'Indice di sicurezza',
    'Country-level safety index; it is not a count of reported crimes in this province or municipality.': 'Indice di sicurezza nazionale; non è un conteggio dei reati locali.',
  };
  const ctx = makeCtx({
    tr: (key, values) => Core.translate((item) => translations[item] || item, key, values),
  });
  const demographics = section('demographics').render({
    series: [{ code: 'resident_population', latest: { year: 2024, value: 2800000, unit: 'residents' } }],
    total: 1,
    municipality: 'Roma',
    spatial_resolution: 'municipality',
    source: 'ISTAT',
  }, ctx);
  assert.match(demographics.body, /Popolazione residente/);
  assert.match(demographics.body, /Indicatori a livello comunale per Roma/);

  const quality = section('quality').render({
    years: ['2024'],
    spatial_resolution: 'country',
    country_name: 'Italy',
    scope_note: 'Country-level relocation indicators for {country}; these values do not describe the province or parcel.',
    clusters: [{
      key: 'safety', label: 'Safety', indicators: [{
        name: 'Safety index', values: { '2024': { value: 71.5, unit: 'index' } },
      }],
    }],
  }, ctx);
  assert.match(quality.body, /Indice di sicurezza/);
  assert.match(quality.body, /Indicatori nazionali per Italy/);
  assert.equal(quality.meta.spatial_resolution, 'paese');

  const safety = section('safety').render({
    country: 'Italy', year: 2024, safety_index: 71.5, spatial_resolution: 'country',
    scope_note: 'Country-level safety index; it is not a count of reported crimes in this province or municipality.',
  }, ctx);
  assert.equal(safety.meta.spatial_resolution, 'paese');
  assert.match(safety.body, /Indice di sicurezza nazionale; non è un conteggio dei reati locali/);
});

test('pois groups by category, most numerous first', () => {
  const html = section('pois').render({ total: 3, source: 'OSM', categories: { Schools: [1], Shops: [1, 2], Parks: [] } }, makeCtx());
  assert.ok(html.body.indexOf('Shops') < html.body.indexOf('Schools'));
  assert.doesNotMatch(html.body, /Parks/);
  assert.equal(html.badge, 3);
  assert.match(section('pois').render({ total: 0 }, makeCtx()).body, /No point of interest found within 1 km/);
});

test('coverage counts available blocks and never presents a missing block as zero', () => {
  const ctx = makeCtx();
  const html = section('coverage').render({ blocks: { basic: { available: true }, valuation: { available: true }, risk: { available: false } } }, ctx);
  assert.equal(html.badge, `2/${Panel.coverageBlocks.length}`);
  assert.match(html.body, /Not available/);
  assert.match(html.body, /parcel read model only/);
  assert.match(html.body, /Separately loaded sections may contain data/);
  assert.match(html.body, /never shown as zero/);
  const unavailable = section('coverage').render({ unavailable: true }, ctx);
  assert.match(unavailable.body, /temporarily unavailable/);
  assert.match(unavailable.body, /role="tablist"/);
  assert.equal(unavailable.empty, undefined);
  const notBuilt = section('coverage').render({ blocks: {} }, ctx);
  assert.match(notBuilt.body, /No data profile has been built/);
  assert.equal(notBuilt.empty, undefined);
});

test('coverage marks partial blocks and escapes source/version metadata', () => {
  const ctx = makeCtx();
  const html = section('coverage').render({ blocks: {
    address: {
      available: true,
      coverage: 'partial',
      coverage_status: 'partial',
      source: 'SISTER <img src=x>',
      dataset_version: 'release-1',
    },
  } }, ctx);
  assert.match(html.body, /Partial coverage/);
  assert.match(html.body, /SISTER &lt;img src=x&gt; · release-1/);
  assert.doesNotMatch(html.body, /<img src=x>/);
});

test('coverage accepts the typed unavailable state', () => {
  const html = section('coverage').render({ blocks: {
    risk: { available: false, coverage: 'unavailable' },
  } }, makeCtx());
  assert.match(html.body, /Not available/);
});

test('coverage lists exactly the blocks the backend declares', () => {
  const fs = require('node:fs');
  const source = fs.readFileSync(path.join(__dirname, '..', '..', 'land_registry', 'stats_service.py'), 'utf8');
  const declared = /_PARCEL_DETAIL_BLOCKS = \(([\s\S]*?)\)/.exec(source)[1].match(/"([a-z_]+)"/g).map((name) => name.replaceAll('"', ''));
  const listed = Panel.coverageBlocks.map(([name]) => name);
  // "addresses" is the plural twin of "address" and land_use is not rendered.
  const ignored = new Set(['addresses', 'land_use']);
  assert.deepEqual(declared.filter((name) => !ignored.has(name)).sort(), listed.slice().sort());
});

test('pvp skips parcel matching without a resolvable sheet and parcel', async () => {
  const calls = [];
  const ctx = makeCtx({ props: { municipality_code: 'H501' }, reference: 'H501', fetchJson: async (url) => { calls.push(url); return { ok: true, status: 200, data: {} }; } });
  const pvp = await section('pvp').load(ctx);
  assert.equal(pvp.matchAvailable, false);
  assert.ok(calls.some((url) => url.includes('/sales/nearby-points?')));
  assert.equal(calls.some((url) => url.includes('/parcel/pvp?')), false);
});
