/* Shared, renderer-independent helpers for the legacy Leaflet and MapLibre maps. */
(function () {
  'use strict';

  function featureKey(feature) {
    const properties = feature?.properties || {};
    const id = feature?.id ?? properties.feature_id ?? properties.id ?? properties.OGC_FID;
    if (id !== null && id !== undefined && String(id)) return `id:${id}`;
    const reference = properties.national_cadastral_reference
      || properties.canonical_reference
      || properties.national_reference;
    return reference ? `ref:${String(reference)}` : null;
  }

  function matchesValue(raw, wanted, operation = 'contains') {
    const value = String(raw ?? '').trim().toLocaleLowerCase();
    const needle = String(wanted ?? '').trim().toLocaleLowerCase();
    if (operation === 'empty') return value === '';
    if (operation === 'not_empty') return value !== '';
    if (operation === 'equals') return value === needle;
    if (operation === 'starts') return value.startsWith(needle);
    if (operation === 'ends') return value.endsWith(needle);
    if (operation === 'gt' || operation === 'gte' || operation === 'lt' || operation === 'lte') {
      const left = Number(raw);
      const right = Number(wanted);
      if (!Number.isFinite(left) || !Number.isFinite(right)) return false;
      if (operation === 'gt') return left > right;
      if (operation === 'gte') return left >= right;
      if (operation === 'lt') return left < right;
      return left <= right;
    }
    return value.includes(needle);
  }

  function compareValues(left, right, direction = 'asc') {
    const leftEmpty = left === null || left === undefined || left === '';
    const rightEmpty = right === null || right === undefined || right === '';
    if (leftEmpty || rightEmpty) return leftEmpty === rightEmpty ? 0 : (leftEmpty ? 1 : -1);
    const leftNumber = Number(left);
    const rightNumber = Number(right);
    const numeric = Number.isFinite(leftNumber) && Number.isFinite(rightNumber)
      && String(left).trim() !== '' && String(right).trim() !== '';
    const comparison = numeric
      ? leftNumber - rightNumber
      : String(left).localeCompare(String(right), undefined, { numeric: true, sensitivity: 'base' });
    return (direction === 'desc' ? -1 : 1) * comparison;
  }

  function filterRows(features, { query = '', filters = {} } = {}) {
    const text = String(query).trim().toLocaleLowerCase();
    return (features || []).filter((feature) => {
      const properties = feature?.properties || {};
      if (text && !Object.values(properties).some((value) => String(value ?? '').toLocaleLowerCase().includes(text))) return false;
      return Object.entries(filters).every(([field, filter]) => {
        if (!field || !filter || String(filter.value ?? '') === '') return true;
        return matchesValue(properties[field], filter.value, filter.operation || 'contains');
      });
    });
  }

  function sortRows(features, field, direction = 'asc') {
    if (!field) return [...(features || [])];
    return [...(features || [])].sort((a, b) => compareValues(
      a?.properties?.[field], b?.properties?.[field], direction,
    ));
  }

  function toFeatureCollection(value) {
    if (value?.type === 'FeatureCollection' && Array.isArray(value.features)) {
      return { type: 'FeatureCollection', features: value.features.filter((feature) => feature?.type === 'Feature' && feature.geometry) };
    }
    if (value?.type === 'Feature' && value.geometry) return { type: 'FeatureCollection', features: [value] };
    throw new Error('Choose a GeoJSON Feature or FeatureCollection with geometries.');
  }

  function downloadGeoJSON(value, filename = 'map-drawings.geojson') {
    const collection = toFeatureCollection(value);
    const blob = new Blob([JSON.stringify(collection, null, 2)], { type: 'application/geo+json;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  async function readGeoJSONFile(file) {
    const text = await file.text();
    return toFeatureCollection(JSON.parse(text));
  }

  function basemapEngines(cartoEnabled = false, cartoApiKey = '') {
    const esri = 'https://server.arcgisonline.com/ArcGIS/rest/services';
    const engines = [
      {
        id: 'light', label: 'Light', legacyLabel: 'Esri Light',
        base: `${esri}/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}`,
        labels: `${esri}/Canvas/World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}`,
        maxzoom: 16, displayMaxzoom: 19, background: '#f2f2ef',
        attribution: '© Esri, HERE, Garmin, © OpenStreetMap contributors',
      },
      {
        id: 'dark', label: 'Dark', legacyLabel: 'Esri Dark',
        base: `${esri}/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}`,
        labels: `${esri}/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}`,
        maxzoom: 16, displayMaxzoom: 19, background: '#1a1a1a',
        attribution: '© Esri, HERE, Garmin, © OpenStreetMap contributors',
      },
      {
        id: 'satellite', label: 'ESRI World Imagery', legacyLabel: 'ESRI World Imagery',
        base: `${esri}/World_Imagery/MapServer/tile/{z}/{y}/{x}`,
        maxzoom: 19, background: '#3d3d3d', attribution: '© Esri, Maxar, Earthstar Geographics',
      },
      {
        id: 'osm', label: 'OpenStreetMap', legacyLabel: 'OpenStreetMap',
        base: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
        maxzoom: 19, displayMaxzoom: 22, background: '#e9e4d6', attribution: '© OpenStreetMap contributors',
      },
      {
        id: 'google-road', label: 'Google Maps', legacyLabel: 'Google Maps',
        base: 'https://mt1.google.com/vt/lyrs=m&x={x}&y={y}&z={z}',
        maxzoom: 20, displayMaxzoom: 22, background: '#e9e4d6', attribution: '© Google',
      },
      {
        id: 'google-satellite', label: 'Google Satellite', legacyLabel: 'Google Satellite',
        base: 'https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}',
        maxzoom: 20, displayMaxzoom: 22, background: '#3d3d3d', attribution: '© Google',
      },
      {
        id: 'google-terrain', label: 'Google Terrain', legacyLabel: 'Google Terrain',
        base: 'https://mt1.google.com/vt/lyrs=p&x={x}&y={y}&z={z}',
        maxzoom: 20, displayMaxzoom: 22, background: '#e9e4d6', attribution: '© Google',
      },
      {
        id: 'google-hybrid', label: 'Google Hybrid', legacyLabel: 'Google Hybrid',
        base: 'https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}',
        maxzoom: 20, displayMaxzoom: 22, background: '#3d3d3d', attribution: '© Google',
      },
      {
        id: 'google-transit', label: 'Google Maps with Transit', legacyLabel: 'Google Maps with Transit',
        base: 'https://mt1.google.com/vt/lyrs=m,transit&x={x}&y={y}&z={z}',
        maxzoom: 20, displayMaxzoom: 22, background: '#e9e4d6', attribution: '© Google',
      },
      {
        id: 'google-traffic', label: 'Google Maps with Traffic', legacyLabel: 'Google Maps with Traffic',
        base: 'https://mt1.google.com/vt/lyrs=m,traffic&x={x}&y={y}&z={z}',
        maxzoom: 20, displayMaxzoom: 22, background: '#e9e4d6', attribution: '© Google',
      },
      {
        id: 'esri-terrain', label: 'ESRI World Terrain', legacyLabel: 'ESRI World Terrain',
        base: 'https://services.arcgisonline.com/ArcGIS/rest/services/World_Terrain_Base/MapServer/tile/{z}/{y}/{x}',
        maxzoom: 13, displayMaxzoom: 22, background: '#e9e4d6', attribution: '© ESRI',
      },
    ];
    if (cartoEnabled && cartoApiKey) {
      const key = encodeURIComponent(cartoApiKey);
      engines.push(
        {
          id: 'carto-light', label: 'CartoDB Positron (Light)', legacyLabel: 'CartoDB Positron (Light)',
          base: `https://cartodb-basemaps-{s}.global.ssl.fastly.net/light_all/{z}/{x}/{y}.png?api_key=${key}`,
          maxzoom: 20, displayMaxzoom: 22, background: '#f2f2ef', attribution: '© OpenStreetMap contributors © CARTO',
        },
        {
          id: 'carto-dark', label: 'CartoDB Dark Matter', legacyLabel: 'CartoDB Dark Matter',
          base: `https://cartodb-basemaps-{s}.global.ssl.fastly.net/dark_all/{z}/{x}/{y}.png?api_key=${key}`,
          maxzoom: 20, displayMaxzoom: 22, background: '#1a1a1a', attribution: '© OpenStreetMap contributors © CARTO',
        },
      );
    }
    return engines;
  }

  const urbanLabels = {
    30: 'urban centre', 23: 'dense urban cluster', 22: 'semi-dense urban cluster',
    21: 'suburban or peri-urban', 13: 'rural cluster', 12: 'low density rural',
    11: 'very low density rural', 10: 'water',
  };
  const urbanCodeLabels = {
    'urban centre': 30, 'urban center': 30, 'dense urban cluster': 23,
    'semi dense urban cluster': 22, 'semi-dense urban cluster': 22,
    'suburban or peri urban': 21, 'suburban or peri-urban': 21,
    'rural cluster': 13, 'low density rural': 12, 'very low density rural': 11, water: 10,
  };
  function classifyUrbanStatus(feature, mode = 'broad') {
    const properties = feature?.properties;
    if (!properties || typeof properties !== 'object') return null;
    const lookup = Object.fromEntries(Object.entries(properties).map(([key, value]) => [key.toLowerCase(), value]));
    const normalize = (value) => String(value ?? '').trim().toLowerCase().replace(/[_-]+/g, ' ').replace(/\s+/g, ' ');
    const statusValue = ['urban_status', 'urban_classification', 'classification_status', 'ghsl_status', 'degurba_status']
      .map((key) => lookup[key]).find((value) => value !== undefined && value !== null && value !== '');
    const explicit = normalize(statusValue).replace(/\s/g, '');
    if (explicit) {
      if (['urban', 'urbano'].includes(explicit)) return { status: 'urban', classCode: null, classLabel: null, hasSignal: true };
      if (['noturban', 'nonurban', 'rural'].includes(explicit)) return { status: 'not_urban', classCode: null, classLabel: null, hasSignal: true };
      if (['unknown', 'nodata', 'na'].includes(explicit)) return { status: 'unknown', classCode: null, classLabel: null, hasSignal: true };
    }
    const rawCode = ['class_code', 'ghsl_class_code', 'ghsl_smod', 'ghsl_smod_code', 'smod', 'smod_code', 'degurba_code']
      .map((key) => lookup[key]).find((value) => value !== undefined && value !== null && value !== '');
    let code = Number.parseInt(String(rawCode ?? '').trim(), 10);
    if (!Number.isFinite(code)) {
      const label = ['class_label', 'ghsl_class_label', 'ghsl_label', 'smod_label', 'degurba_label']
        .map((key) => lookup[key]).find((value) => value !== undefined && value !== null && value !== '');
      if (label === undefined) return null;
      code = urbanCodeLabels[normalize(label)];
    }
    if (!Number.isFinite(code)) return { status: 'unknown', classCode: null, classLabel: null, hasSignal: true };
    const urban = mode === 'strict' ? [30, 23, 22] : [30, 23, 22, 21];
    const notUrban = mode === 'strict' ? [21, 13, 12, 11, 10] : [13, 12, 11, 10];
    const status = urban.includes(code) ? 'urban' : notUrban.includes(code) ? 'not_urban' : 'unknown';
    return { status, classCode: code, classLabel: urbanLabels[code] || null, hasSignal: true };
  }

  window.MapWorkbench = Object.freeze({
    featureKey,
    matchesValue,
    compareValues,
    filterRows,
    sortRows,
    toFeatureCollection,
    downloadGeoJSON,
    readGeoJSONFile,
    basemapEngines,
    classifyUrbanStatus,
  });
})();
