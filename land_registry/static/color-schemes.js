/*
 * Feature colors shared by the Leaflet SVG and WebGL cadastral renderers.
 * Palettes are derived from the properties in the current GeoJSON response;
 * missing fields stay neutral instead of implying a value that is not present.
 */
(function (global) {
    'use strict';

    const MISSING = '#9ca3af';
    const CATEGORICAL = [
        '#0072B2', '#E69F00', '#009E73', '#CC79A7', '#D55E00',
        '#56B4E9', '#F0E442', '#332288', '#88CCEE', '#44AA99',
        '#AA4499', '#999933', '#882255', '#661100', '#6699CC',
    ];
    const SEQUENTIAL = ['#f7fbff', '#c6dbef', '#6baed6', '#2171b5', '#08306b'];
    const FIELDS = {
        foglio: ['foglio', 'sheet', 'sheet_number', 'numero_foglio', 'n_foglio'],
        propertyType: ['property_type', 'propertytype', 'tipo_immobile', 'tipoimmobile', 'tipo_edificio', 'tipo', 'destinazione', 'destinazione_uso', 'uso', 'use', 'use_type', 'land_use', 'classamento', 'categoria', 'category'],
        temporal: ['updated_at', 'updated', 'updatedate', 'update_date', 'last_update', 'data_aggiornamento', 'dataaggiornamento', 'modified', 'modified_at', 'modified_on', 'created_at', 'data', 'date'],
    };

    function lookup(properties) {
        const result = Object.create(null);
        if (!properties || typeof properties !== 'object') return result;
        Object.keys(properties).forEach(key => { result[key.toLowerCase()] = properties[key]; });
        return result;
    }

    function fieldValue(feature, keys) {
        const properties = lookup(feature && feature.properties);
        for (const key of keys) {
            const value = properties[key];
            if (value !== undefined && value !== null && String(value).trim() !== '') return value;
        }
        return null;
    }

    function hash(value) {
        let result = 2166136261;
        for (const char of String(value)) {
            result ^= char.charCodeAt(0);
            result = Math.imul(result, 16777619);
        }
        return result >>> 0;
    }

    function categoryColor(value) {
        const key = String(value ?? '').trim().toLocaleLowerCase();
        return key ? CATEGORICAL[hash(key) % CATEGORICAL.length] : MISSING;
    }

    function interpolateColor(stops, value) {
        const t = Math.max(0, Math.min(1, Number(value) || 0));
        const scaled = t * (stops.length - 1);
        const index = Math.min(stops.length - 2, Math.floor(scaled));
        const local = scaled - index;
        const from = stops[index].match(/[\da-f]{2}/gi).map(v => parseInt(v, 16));
        const to = stops[index + 1].match(/[\da-f]{2}/gi).map(v => parseInt(v, 16));
        return '#' + from.map((channel, i) => Math.round(channel + (to[i] - channel) * local)
            .toString(16).padStart(2, '0')).join('');
    }

    function bboxCenter(feature) {
        const coordinates = feature && feature.geometry && feature.geometry.coordinates;
        if (!coordinates) return null;
        let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
        const visit = value => {
            if (!Array.isArray(value)) return;
            if (value.length >= 2 && Number.isFinite(value[0]) && Number.isFinite(value[1])) {
                minX = Math.min(minX, value[0]); maxX = Math.max(maxX, value[0]);
                minY = Math.min(minY, value[1]); maxY = Math.max(maxY, value[1]);
                return;
            }
            value.forEach(visit);
        };
        visit(coordinates);
        return Number.isFinite(minX) ? [(minX + maxX) / 2, (minY + maxY) / 2] : null;
    }

    function normalizedValues(values, logarithmic = false) {
        const valid = values.filter(Number.isFinite);
        if (!valid.length) return values.map(() => null);
        const transformed = values.map(value => Number.isFinite(value)
            ? (logarithmic ? Math.log1p(value) : value) : null);
        const finite = transformed.filter(Number.isFinite);
        let min = Infinity, max = -Infinity;
        finite.forEach(value => { min = Math.min(min, value); max = Math.max(max, value); });
        return transformed.map(value => value === null ? null : (max === min ? 0.5 : (value - min) / (max - min)));
    }

    function create(geojson, scheme = 'default', options = {}) {
        const features = Array.isArray(geojson && geojson.features) ? geojson.features : [];
        const colors = new WeakMap();
        const indexed = [];
        const normalizedScheme = String(scheme || 'default');
        const remember = (feature, color) => {
            if (feature && typeof feature === 'object') colors.set(feature, color);
            indexed.push(color);
        };

        if (normalizedScheme === 'foglio' || normalizedScheme === 'propertyType') {
            const keys = FIELDS[normalizedScheme];
            features.forEach(feature => remember(feature, categoryColor(fieldValue(feature, keys))));
        } else if (normalizedScheme === 'density') {
            const resolution = Math.max(100, Number(options.cellSizeMeters) || 500);
            const centers = features.map(bboxCenter);
            const cells = centers.map(center => {
                if (!center) return null;
                const latitude = center[1] * Math.PI / 180;
                const x = center[0] * 111320 * Math.cos(latitude);
                const y = center[1] * 110540;
                return `${Math.floor(x / resolution)}:${Math.floor(y / resolution)}`;
            });
            const counts = new Map();
            cells.forEach(cell => { if (cell) counts.set(cell, (counts.get(cell) || 0) + 1); });
            const densities = cells.map(cell => cell ? counts.get(cell) : null);
            const normalized = normalizedValues(densities, true);
            features.forEach((feature, index) => remember(feature,
                normalized[index] === null ? MISSING : interpolateColor(SEQUENTIAL, normalized[index])));
        } else if (normalizedScheme === 'temporal') {
            const timestamps = features.map(feature => {
                const value = fieldValue(feature, FIELDS.temporal);
                const time = value === null ? NaN : Date.parse(value);
                return Number.isFinite(time) ? time : null;
            });
            const normalized = normalizedValues(timestamps, false);
            features.forEach((feature, index) => remember(feature,
                normalized[index] === null ? MISSING : interpolateColor(SEQUENTIAL, normalized[index])));
        } else {
            features.forEach(feature => remember(feature, options.color || '#3388ff'));
        }

        return {
            name: normalizedScheme,
            colorForFeature(feature, index) {
                if (feature && colors.has(feature)) return colors.get(feature);
                return indexed[index] || options.color || '#3388ff';
            },
        };
    }

    global.MapColorSchemes = Object.freeze({
        names: Object.freeze(['default', 'foglio', 'density', 'propertyType', 'temporal']),
        create,
        categoryColor,
    });
})(window);
