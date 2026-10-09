/* Parcel panel: pure logic shared by the views and unit tests.
 *
 * Nothing here touches the DOM, the network or the map. Functions take plain
 * data and an optional ``tr`` lookup and return strings or plain objects, so
 * they can be tested under Node (see tests/test_parcel_panel_core_js.py).
 * Behaviour is ported from the legacy parcel-enrichment.js cards; the legacy
 * page keeps its own copy until /map-legacy stops extending its parcel panel.
 */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.ParcelPanelCore = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const LOCALE = 'it-IT';
  const identity = (text) => text;
  const interpolate = (text, values) => (values
    ? String(text).replace(/\{(\w+)\}/g, (_, name) => values[name] ?? '')
    : String(text));
  // ``tr`` may be the page's gettext-style lookup or absent (tests).
  const translate = (tr, key, values) => interpolate((tr || identity)(key), values);

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>'"]/g, (char) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
    })[char]);
  }

  function isPresent(value) {
    return value !== null && value !== undefined && value !== '';
  }

  function toNumber(value) {
    if (!isPresent(value) || typeof value === 'boolean') return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  // ---- Formatting ----------------------------------------------------------

  function formatNumber(value, maximumFractionDigits = 0) {
    const number = toNumber(value);
    return number === null ? '—' : number.toLocaleString(LOCALE, { maximumFractionDigits });
  }

  /** Format a 0..1 ratio as a percentage. */
  function formatRatio(value, maximumFractionDigits = 1) {
    const number = toNumber(value);
    return number === null
      ? '—'
      : `${(number * 100).toLocaleString(LOCALE, { maximumFractionDigits })}%`;
  }

  function formatCurrency(value) {
    const number = toNumber(value);
    return number === null
      ? '—'
      : number.toLocaleString(LOCALE, { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 });
  }

  function formatArea(value) {
    const number = toNumber(value);
    return number === null ? '—' : `${number.toLocaleString(LOCALE, { maximumFractionDigits: 1 })} m²`;
  }

  function formatConfidence(value) {
    if (!isPresent(value)) return '';
    const number = Number(value);
    if (!Number.isFinite(number)) return String(value);
    const percent = number <= 1 ? number * 100 : number;
    return `${percent.toLocaleString(LOCALE, { maximumFractionDigits: 1 })}%`;
  }

  function parseDateTime(value) {
    if (!value) return null;
    const parsed = value instanceof Date ? value : new Date(value);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  }

  function formatDateTime(value) {
    const date = parseDateTime(value);
    return date
      ? date.toLocaleString(LOCALE, { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
      : '';
  }

  function relativeAge(value, tr, now = new Date()) {
    const date = parseDateTime(value);
    if (!date) return '';
    const minutes = Math.floor(Math.max(0, now.getTime() - date.getTime()) / 60000);
    if (minutes < 1) return translate(tr, 'less than 1 min ago');
    if (minutes < 90) return translate(tr, '{n} min ago', { n: minutes });
    const hours = Math.floor(minutes / 60);
    if (hours < 48) return translate(tr, '{n} hours ago', { n: hours });
    return translate(tr, '{n} days ago', { n: Math.floor(hours / 24) });
  }

  function median(values) {
    const numbers = values.map(toNumber).filter((value) => value !== null).sort((a, b) => a - b);
    if (!numbers.length) return null;
    const middle = Math.floor(numbers.length / 2);
    return numbers.length % 2 ? numbers[middle] : (numbers[middle - 1] + numbers[middle]) / 2;
  }

  // ---- Parcel identity -----------------------------------------------------

  /** Catasto comune code (e.g. "H501") from properties or a national reference. */
  function cadastralCodeOf(properties, reference) {
    const props = properties || {};
    const direct = props.municipality_code || props.ADMINISTRATIVEUNIT || props.administrativeunit;
    if (direct) return String(direct).trim().toUpperCase();
    const match = /^([A-Za-z]\d{3})/.exec(String(reference || '').trim());
    return match ? match[1].toUpperCase() : null;
  }

  /** Parcel area in m², from the first usable of several property names. */
  function parcelAreaSqm(properties) {
    const props = properties || {};
    for (const key of ['area_sqm', 'area_m2', 'area', 'area_display', 'computed_area_sqm']) {
      const value = toNumber(props[key]);
      if (value !== null && value > 0) return { value, computed: key === 'computed_area_sqm' };
    }
    const hectares = toNumber(props.area_ha);
    return hectares !== null && hectares > 0 ? { value: hectares * 10000, computed: false } : null;
  }

  /** Centroid {lat, lng} from properties, else from the geometry's bounding box. */
  function centroidOf(properties, geometry) {
    const props = properties || {};
    const lat = toNumber(props.centroid_lat);
    const lng = toNumber(props.centroid_lng);
    if (lat !== null && lng !== null) return { lat, lng };
    let minLng = Infinity; let minLat = Infinity; let maxLng = -Infinity; let maxLat = -Infinity;
    const visit = (value) => {
      if (!Array.isArray(value)) return;
      if (value.length >= 2 && typeof value[0] === 'number' && typeof value[1] === 'number') {
        minLng = Math.min(minLng, value[0]); maxLng = Math.max(maxLng, value[0]);
        minLat = Math.min(minLat, value[1]); maxLat = Math.max(maxLat, value[1]);
        return;
      }
      value.forEach(visit);
    };
    visit(geometry && geometry.coordinates);
    return Number.isFinite(minLng) ? { lat: (minLat + maxLat) / 2, lng: (minLng + maxLng) / 2 } : null;
  }

  /**
   * Municipality, sheet and parcel for the by-parcel lookups. Feature
   * properties win; the national reference ("H501_000100.1") is the fallback.
   */
  function referenceParts(properties, reference) {
    const props = properties || {};
    const text = String(reference || '');
    let sheet = props.sheet_number ?? props.sheet ?? props.foglio ?? '';
    let parcel = props.parcel_number ?? props.parcel ?? props.particella ?? '';
    if ((!isPresent(sheet) || !isPresent(parcel)) && text.includes('_')) {
      const location = text.split('_')[1].split('.');
      if (!isPresent(sheet)) sheet = location[0];
      if (!isPresent(parcel)) parcel = location.slice(1).join('.');
    }
    return { municipality: cadastralCodeOf(props, text) || '', sheet: String(sheet ?? ''), parcel: String(parcel ?? '') };
  }

  // ---- OMI valuation -------------------------------------------------------

  /**
   * Quotes with a valid sale interval, those of the detected zone first.
   * ``zoneMatch`` is the /omi/at-point result ({matched, zone}).
   */
  function validOmiQuotes(data, zoneMatch) {
    const quotes = ((data && data.quotes) || []).filter((quote) => {
      const min = toNumber(quote.prezzo_min);
      const max = toNumber(quote.prezzo_max);
      return min !== null && max !== null && min >= 0 && max >= min;
    });
    const preferred = zoneMatch && zoneMatch.matched && String(zoneMatch.zone || '').toUpperCase();
    if (!preferred) return quotes;
    return quotes
      .map((quote, index) => ({ quote, index }))
      .sort((a, b) => {
        const aMatch = String(a.quote.zona || '').toUpperCase() === preferred ? 0 : 1;
        const bMatch = String(b.quote.zona || '').toUpperCase() === preferred ? 0 : 1;
        return aMatch - bMatch || a.index - b.index;
      })
      .map((item) => item.quote);
  }

  function quoteMidpoint(quote) {
    const min = toNumber(quote && quote.prezzo_min);
    const max = toNumber(quote && quote.prezzo_max);
    return min === null || max === null ? null : (min + max) / 2;
  }

  /** Indicative range {min, max} in EUR from one quote and an area; null if invalid. */
  function estimateOmiRange(quote, areaSqm) {
    const area = toNumber(areaSqm);
    const minRate = toNumber(quote && quote.prezzo_min);
    const maxRate = toNumber(quote && quote.prezzo_max);
    if (area === null || minRate === null || maxRate === null) return null;
    if (area <= 0 || minRate < 0 || maxRate < minRate) return null;
    return { min: area * minRate, max: area * maxRate };
  }

  /**
   * Default estimator surface. A parcel above 1 ha is almost never a single
   * dwelling, so the user must enter the building's commercial surface.
   */
  function defaultEstimateArea(areaSqm) {
    const area = toNumber(areaSqm);
    if (area === null || area <= 0) return { value: '', tooLarge: false };
    return area <= 10000 ? { value: Math.round(area), tooLarge: false } : { value: '', tooLarge: true };
  }

  /** Comparable benchmark across the loaded quotes (median of band midpoints). */
  function quoteBenchmark(quotes) {
    const midpoint = median(quotes.map(quoteMidpoint));
    if (midpoint === null) return null;
    const years = quotes.map((quote) => toNumber(quote.anno)).filter((year) => year !== null);
    return { label: 'median of the comune quotes', value: midpoint, unit: '€/m²', year: years.length ? Math.max(...years) : null };
  }

  /**
   * Sale-price history for one quote's conservation state: last ``limit``
   * semesters as points {label, min, max, mid}, plus the change over the span.
   */
  function omiHistorySeries(history, selectedQuote, limit = 24) {
    const state = selectedQuote && selectedQuote.stato_conservazione;
    const rows = (history || []).filter((row) => {
      const min = toNumber(row.prezzo_min);
      const max = toNumber(row.prezzo_max);
      const sameState = !state || !row.stato_conservazione || row.stato_conservazione === state;
      return sameState && min !== null && max !== null;
    }).slice(-limit);
    const points = rows.map((row) => {
      const min = Number(row.prezzo_min);
      const max = Number(row.prezzo_max);
      return { label: `${row.anno} S${row.semestre}`, min, max, mid: (min + max) / 2 };
    });
    const first = points[0];
    const last = points[points.length - 1];
    const change = first && last && first.mid ? ((last.mid - first.mid) / first.mid) * 100 : null;
    return { points, change, first, last };
  }

  /** Inline SVG: min..max band with the midpoint line. ``series`` from omiHistorySeries. */
  function historyChartSvg(series, ariaLabel) {
    const points = series && series.points;
    if (!points || !points.length) return '';
    const width = 320; const height = 96; const padX = 8; const padY = 10;
    const low = Math.min(...points.map((point) => point.min));
    const high = Math.max(...points.map((point) => point.max));
    const span = high - low;
    const x = (index) => padX + (points.length === 1 ? (width - padX * 2) / 2 : index * (width - padX * 2) / (points.length - 1));
    const y = (value) => height - padY - (span === 0 ? 0.5 : (value - low) / span) * (height - padY * 2);
    const upper = points.map((point, index) => `${x(index).toFixed(1)},${y(point.max).toFixed(1)}`);
    const lower = points.map((point, index) => `${x(index).toFixed(1)},${y(point.min).toFixed(1)}`).reverse();
    const line = points.map((point, index) => `${x(index).toFixed(1)},${y(point.mid).toFixed(1)}`).join(' ');
    const lastIndex = points.length - 1;
    return `<svg class="parcel-chart parcel-history-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(ariaLabel || '')}">`
      + `<polygon class="parcel-chart-band" points="${upper.concat(lower).join(' ')}"></polygon>`
      + `<polyline class="parcel-chart-line" points="${line}" fill="none" vector-effect="non-scaling-stroke"></polyline>`
      + `<circle class="parcel-chart-dot" cx="${x(lastIndex).toFixed(1)}" cy="${y(points[lastIndex].mid).toFixed(1)}" r="3.5"></circle>`
      + '</svg>';
  }

  // ---- Census, income, risk, bulletin --------------------------------------

  /** Section summary: counts and ratios from an ISTAT census-section feature. */
  function censusSummary(feature, metadata, fallbackBenchmark) {
    const props = feature && feature.properties;
    if (!props) return null;
    const population = toNumber(props.p1 ?? props.pop21);
    const area = toNumber(props.area_sqm ?? props.area_m2 ?? props.shape_area);
    const density = area !== null && area > 0 && population !== null ? population / (area / 1e6) : null;
    const benchmark = (metadata && metadata.benchmarks && metadata.benchmarks.population_density_per_km2) || fallbackBenchmark || null;
    return {
      section: props.sez21_id || null,
      population,
      density,
      households: toNumber(props.pf1 ?? props.fam21),
      dwellings: toNumber(props.a8 ?? props.abi21),
      buildings: toNumber(props.e3 ?? props.edi21),
      ratios: props.ratios || {},
      benchmark,
    };
  }

  /** Income-bracket rows {label, pct (0..100 or null)}. */
  function incomeBracketRows(distribution) {
    return (distribution || []).map((row) => {
      const pct = toNumber(row.pct);
      return { label: String(row.bracket ?? ''), pct: pct === null ? null : Math.max(0, Math.min(100, pct)) };
    });
  }

  /** 'high' (>= 5 %), 'medium' (>= 1 %), 'low', or 'unknown' for a hazard-area share. */
  function riskLevel(pct) {
    const value = toNumber(pct);
    if (value === null) return 'unknown';
    if (value >= 5) return 'high';
    if (value >= 1) return 'medium';
    return 'low';
  }

  function seismicLabel(zone, tr) {
    const key = { 1: 'Zone 1 (high seismicity)', 4: 'Zone 4 (low seismicity)' }[zone];
    return key ? translate(tr, key) : translate(tr, 'Zone {n}', { n: zone });
  }

  /** Alert level from a Protezione Civile description: red, orange, yellow or none. */
  function bulletinSeverity(description) {
    const text = String(description || '').toUpperCase();
    if (text.includes('ROSSA')) return 'red';
    if (text.includes('ARANCIONE')) return 'orange';
    if (text.includes('GIALLA')) return 'yellow';
    return 'none';
  }

  function foldName(name) {
    return String(name || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
  }

  /** Bulletin zone properties whose comuni list contains ``muniName``, or null. */
  function findBulletinZone(bulletin, muniName) {
    const objects = bulletin && bulletin.today_zones && bulletin.today_zones.objects;
    if (!objects || !muniName) return null;
    const target = foldName(muniName);
    for (const object of Object.values(objects)) {
      for (const geometry of (object.geometries || [])) {
        const comuni = (geometry.properties && geometry.properties.Comuni) || [];
        if (comuni.some((name) => foldName(name) === target)) return geometry.properties;
      }
    }
    return null;
  }

  function feedRefreshValue(data) {
    if (!data) return null;
    for (const key of ['feed_refreshed_at', 'last_refreshed_at', 'refreshed_at', 'source_refreshed_at', 'feed_updated_at',
      'source_updated_at', 'updated_at', 'fetched_at', 'generated_at', 'issued_at', 'published_at']) {
      if (data[key]) return data[key];
    }
    return null;
  }

  function parseFireObservationTime(detection) {
    const explicit = detection && (detection.observed_at || detection.observation_time || detection.detected_at || detection.timestamp);
    const explicitDate = parseDateTime(explicit);
    if (explicitDate) return explicitDate;
    const day = detection && detection.acq_date;
    if (!day) return null;
    const time = String((detection && detection.acq_time) || '').replace(/\D/g, '').padStart(4, '0').slice(0, 4);
    return /^\d{4}$/.test(time)
      ? parseDateTime(`${day}T${time.slice(0, 2)}:${time.slice(2)}:00Z`)
      : parseDateTime(`${day}T00:00:00Z`);
  }

  function indicatorLabel(code) {
    return String(code || '').replace(/[_-]+/g, ' ').replace(/\s+/g, ' ').trim().toLowerCase()
      .replace(/(^|\s)\p{L}/gu, (letter) => letter.toUpperCase());
  }

  // ---- Records ---------------------------------------------------------------

  const HIDDEN_RECORD_KEYS = new Set(['documento', 'pdf', 'base64', 'content_base64']);

  /** Flatten a nested record to [label, value] rows, skipping binary payloads. */
  function flattenValues(value, prefix = '', rows = [], limit = 14) {
    if (rows.length >= limit || !isPresent(value)) return rows;
    if (Array.isArray(value)) {
      value.slice(0, 6).forEach((item, index) => flattenValues(item, `${prefix}[${index + 1}]`, rows, limit));
      return rows;
    }
    if (typeof value === 'object') {
      Object.entries(value).forEach(([key, item]) => {
        if (!HIDDEN_RECORD_KEYS.has(key.toLowerCase())) flattenValues(item, prefix ? `${prefix} · ${key}` : key, rows, limit);
      });
      return rows;
    }
    rows.push([prefix || 'Value', String(value)]);
    return rows;
  }

  /** Only http(s) URLs are ever rendered as links. */
  function safeHttpUrl(value) {
    try {
      const url = new URL(String(value));
      return url.protocol === 'http:' || url.protocol === 'https:' ? url.href : null;
    } catch (_) {
      return null;
    }
  }

  // ---- Provenance ------------------------------------------------------------

  /**
   * Provenance footer for one envelope block (or any object carrying the same
   * fields). Modelled values are flagged and missing metadata is left out
   * rather than invented.
   */
  function blockMetadataHtml(block, tr) {
    if (!block) return '';
    const chips = [];
    const confidence = formatConfidence(block.confidence);
    if (confidence) chips.push([translate(tr, 'Confidence'), confidence]);
    if (block.spatial_resolution) chips.push([translate(tr, 'Resolution'), block.spatial_resolution]);
    if (toNumber(block.spatial_resolution_m) !== null) chips.push([translate(tr, 'Resolution'), `${formatNumber(block.spatial_resolution_m)} m`]);
    const provenance = [
      block.dataset_version ? `${translate(tr, 'Dataset')}: ${block.dataset_version}` : '',
      block.model_version ? `${translate(tr, 'Model')}: ${block.model_version}` : '',
      block.match_method ? `${translate(tr, 'Match')}: ${block.match_method}` : '',
    ].filter(Boolean).join(' · ');
    const benchmarks = Object.values(block.benchmarks || {}).filter((benchmark) => benchmark && isPresent(benchmark.value));
    if (!chips.length && !benchmarks.length && !block.source && !provenance) return '';
    const chipHtml = chips.length
      ? `<div class="parcel-block-chips">${chips.map(([label, value]) => `<span><strong>${escapeHtml(label)}</strong> ${escapeHtml(value)}</span>`).join('')}</div>`
      : '';
    const benchmarkHtml = benchmarks.length
      ? `<div class="parcel-block-benchmarks">${benchmarks.map((benchmark) => `<span>${escapeHtml(translate(tr, 'Benchmark'))} ${escapeHtml([benchmark.label || '', benchmark.year || ''].filter(Boolean).join(' '))}: <strong>${escapeHtml(benchmark.value)} ${escapeHtml(benchmark.unit || '')}</strong></span>`).join('')}</div>`
      : '';
    const sourceHtml = `${block.source ? `${escapeHtml(translate(tr, 'Source'))}: ${escapeHtml(block.source)}` : ''}${block.source && provenance ? '<br>' : ''}${escapeHtml(provenance)}`;
    return `${chipHtml}${benchmarkHtml}${sourceHtml ? `<p class="parcel-block-provenance">${sourceHtml}</p>` : ''}`;
  }

  return {
    escapeHtml, isPresent, toNumber, translate,
    formatNumber, formatRatio, formatCurrency, formatArea, formatConfidence,
    parseDateTime, formatDateTime, relativeAge, median,
    cadastralCodeOf, parcelAreaSqm, centroidOf, referenceParts,
    validOmiQuotes, quoteMidpoint, estimateOmiRange, defaultEstimateArea, quoteBenchmark,
    omiHistorySeries, historyChartSvg,
    censusSummary, incomeBracketRows, riskLevel, seismicLabel, bulletinSeverity,
    foldName, findBulletinZone, feedRefreshValue, parseFireObservationTime, indicatorLabel,
    flattenValues, safeHttpUrl, blockMetadataHtml,
  };
}));
