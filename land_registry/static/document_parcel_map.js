(function () {
    'use strict';

    function elementsFor(container) {
        var card = container.closest('.document-parcel-map-card');
        var status = card && card.querySelector('[data-document-map-status]');
        return {
            status: status,
            message: status && status.querySelector('[data-map-status-text]'),
            retry: status && status.querySelector('[data-document-map-retry]')
        };
    }

    function setStatus(container, message, showRetry) {
        var parts = elementsFor(container);
        if (parts.message) parts.message.textContent = message;
        if (parts.status) parts.status.hidden = false;
        if (parts.retry) parts.retry.hidden = !showRetry;
    }

    function localized(container, key, fallback, values) {
        var card = container.closest('.document-parcel-map-card');
        var labels = {};
        try {
            labels = JSON.parse(card && card.dataset.documentMapLabels || '{}');
        } catch (error) {}
        var message = labels[key] || fallback;
        Object.keys(values || {}).forEach(function (name) {
            message = message.replace('{' + name + '}', String(values[name]));
        });
        return message;
    }

    function clearStatus(container) {
        var parts = elementsFor(container);
        if (parts.status) parts.status.hidden = true;
        if (parts.retry) parts.retry.hidden = true;
    }

    function cleanText(value) {
        return String(value == null ? '' : value).trim().replace(/\s+/g, '');
    }

    function normalizeName(value) {
        return String(value == null ? '' : value)
            .replace(/\s*\([^)]*\)\s*$/, '')
            .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
            .toLocaleLowerCase('it').replace(/[^a-z0-9]/g, '');
    }

    function normalizeCadastralPart(value) {
        var text = cleanText(value).toLocaleUpperCase('it');
        if (/^\d+$/.test(text)) return String(Number(text));
        return text;
    }

    function escapeHtml(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function cadastralCategory(value) {
        var match = String(value == null ? '' : value).toLocaleUpperCase('it')
            .match(/^\s*([A-F])\s*\/?\s*(\d{1,2}[A-Z]?)/);
        if (!match) return null;
        var groupHues = { A: 218, B: 274, C: 148, D: 20, E: 328, F: 190 };
        var categoryNumber = parseInt(match[2], 10);
        var hue = (groupHues[match[1]] + ((categoryNumber - 1) % 10) * 4) % 360;
        return {
            label: match[1] + '/' + match[2],
            color: 'hsl(' + hue + ' 72% 34%)'
        };
    }

    function overviewLabel(item, kind, container) {
        var parts = [];
        var municipality = String(item.municipality || '').trim();
        var province = String(item.province || '').trim();
        if (municipality) parts.push(municipality + (province ? ' (' + province + ')' : ''));
        else if (item.municipality_code) parts.push(localized(container, 'municipality_prefix', 'Comune {value}', {value: item.municipality_code}));
        if (item.section) parts.push(localized(container, 'section_prefix', 'Sezione {value}', {value: item.section}));
        if (item.sheet) parts.push(localized(container, 'sheet_prefix', 'Foglio {value}', {value: item.sheet}));
        if (kind === 'parcel' && item.parcel) parts.push(localized(container, 'parcel_prefix', 'Particella {value}', {value: item.parcel}));
        var category = kind === 'parcel' ? cadastralCategory(item.category || item.Categoria) : null;
        if (category) parts.push(localized(container, 'category_prefix', 'Cat. {value}', {value: category.label}));
        return parts.join(' · ') || String(item.reference || localized(container, 'feature_fallback', 'Elemento catastale'));
    }

    function parcelMapKey(item) {
        var municipality = String(item.municipality_code || '').trim().toUpperCase()
            || normalizeName(item.municipality);
        return [municipality, normalizeCadastralPart(item.sheet), normalizeCadastralPart(item.parcel)].join('|');
    }

    function parcelSheetKey(item) {
        var municipality = String(item.municipality_code || '').trim().toUpperCase()
            || normalizeName(item.municipality);
        return [
            municipality,
            normalizeCadastralPart(item.section),
            normalizeCadastralPart(item.sheet)
        ].join('|');
    }

    function cellIndex(headers, matcher) {
        for (var i = 0; i < headers.length; i += 1) {
            if (matcher.test(headers[i])) return i;
        }
        return -1;
    }

    function findPropertyRows() {
        var records = [];
        document.querySelectorAll('.cat-compact-table').forEach(function (table) {
            var headers = Array.prototype.map.call(table.querySelectorAll('thead th'), function (cell) {
                return cell.textContent.trim().toLocaleLowerCase('it').replace(/\s+/g, ' ');
            });
            var municipalityIndex = cellIndex(headers, /^(?:municipality|comune)$/);
            var sheetIndex = cellIndex(headers, /^(?:fog\.?|foglio|sheet)$/);
            var parcelIndex = cellIndex(headers, /^(?:part\.?|particella|parcel)$/);
            if (sheetIndex < 0 || parcelIndex < 0) return;

            table.querySelectorAll('tbody tr').forEach(function (row) {
                var cells = row.cells;
                if (!cells[sheetIndex] || !cells[parcelIndex]) return;
                var sheet = normalizeCadastralPart(cells[sheetIndex].textContent);
                var parcel = normalizeCadastralPart(cells[parcelIndex].textContent);
                if (!sheet || !parcel || sheet === '—' || parcel === '—' || sheet === '-') return;

                var municipalityText = municipalityIndex >= 0 && cells[municipalityIndex]
                    ? cells[municipalityIndex].textContent.trim() : '';
                var codeMatch = municipalityText.match(/\(([A-Z0-9]{3,8})\)\s*$/i);
                var record = {
                    row: row,
                    parcelCell: cells[parcelIndex],
                    municipality: normalizeName(municipalityText),
                    municipalityCode: codeMatch ? codeMatch[1].toUpperCase() : '',
                    sheet: sheet,
                    parcel: parcel
                };
                records.push(record);
            });
        });
        return records;
    }

    function resolveRow(records, item) {
        var sheet = normalizeCadastralPart(item.sheet);
        var parcel = normalizeCadastralPart(item.parcel);
        if (!sheet || !parcel) return null;
        var candidates = records.filter(function (record) {
            return record.sheet === sheet && record.parcel === parcel;
        });
        var code = String(item.municipality_code || '').toUpperCase();
        if (code) {
            var byCode = candidates.find(function (record) { return record.municipalityCode === code; });
            if (byCode) return byCode;
        }
        var municipality = normalizeName(item.municipality);
        if (municipality) {
            var byMunicipality = candidates.find(function (record) { return record.municipality === municipality; });
            if (byMunicipality) return byMunicipality;
        }
        return candidates.length === 1 && !municipality && !code ? candidates[0] : null;
    }

    function rowTarget(targets, row) {
        if (!row) return null;
        if (!targets.has(row.row)) targets.set(row.row, { row: row.row, layers: [], point: null });
        return targets.get(row.row);
    }

    function highlightRow(row) {
        if (!row) return;
        row.scrollIntoView({ behavior: 'smooth', block: 'center' });
        row.classList.add('document-map-row-highlight');
        window.setTimeout(function () { row.classList.remove('document-map-row-highlight'); }, 1800);
    }

    function addMapActions(records, targets, map, container) {
        records.forEach(function (record) {
            var button = document.createElement('button');
            button.type = 'button';
            button.className = 'btn btn-sm btn-link document-map-row-action';
            button.textContent = localized(container, 'show_map', 'Mappa');
            button.setAttribute('aria-label', localized(container, 'show_map_aria', 'Mostra sulla mappa foglio {sheet}, particella {parcel}', {
                sheet: record.sheet,
                parcel: record.parcel
            }));
            button.addEventListener('click', function () {
                var target = targets.get(record.row);
                if (!target || (!target.layers.length && !target.point)) return;
                if (target.layers.length) {
                    var bounds = window.L.latLngBounds([]);
                    target.layers.forEach(function (layer) {
                        if (layer.getBounds && layer.getBounds().isValid()) bounds.extend(layer.getBounds());
                    });
                    if (bounds.isValid()) map.flyToBounds(bounds, { padding: [32, 32], maxZoom: 18 });
                    else if (target.layers[0].getLatLng) map.flyTo(target.layers[0].getLatLng(), Math.max(map.getZoom(), 17));
                    target.layers[0].openPopup && target.layers[0].openPopup();
                } else if (target.point) {
                    map.flyTo(target.point, Math.max(map.getZoom(), 17));
                }
                highlightRow(record.row);
            });
            var target = targets.get(record.row);
            if (target && (target.layers.length || target.point)) record.parcelCell.appendChild(button);
        });
    }

    async function initializeMap(container) {
        if (container.dataset.mapLoading === 'true' || container.dataset.mapInitialized === 'true') return;
        container.dataset.mapLoading = 'true';
        setStatus(container, localized(container, 'loading', 'Caricamento mappa catastale…'), false);
        try {
            if (!window.L || !window.LandRegistryMap) {
                throw new Error('Leaflet or LandRegistryMap did not load');
            }
            var dataUrl = container.dataset.mapDataUrl;
            if (!dataUrl) throw new Error('Map data URL is missing');
            var response = await fetch(dataUrl, {
                credentials: 'same-origin',
                headers: { Accept: 'application/json' }
            });
            if (!response.ok) throw new Error('Map data request failed with status ' + response.status);
            var mapData = await response.json();

            var latitude = Number(container.dataset.centerLat);
            var longitude = Number(container.dataset.centerLng);
            var zoom = Number(container.dataset.zoom);
            if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) {
                throw new Error('Invalid map center coordinates');
            }

            var labels = {};
            try { labels = JSON.parse(container.dataset.labels || '{}'); } catch (_error) { labels = {}; }
            var parcelMarkers = Array.isArray(mapData.parcel_markers) ? mapData.parcel_markers : [];
            var parcelFeatures = mapData.parcel_features || { type: 'FeatureCollection', features: [] };
            var sheetFeatures = mapData.sheet_features || { type: 'FeatureCollection', features: [] };
            var unmappedParcels = Array.isArray(mapData.unmapped_parcels) ? mapData.unmapped_parcels : [];
            var listedOnly = container.dataset.listedOnly === 'true';
            var instance = window.LandRegistryMap.create({
                container: container,
                apiBase: container.dataset.apiBase,
                center: [latitude, longitude],
                zoom: Number.isFinite(zoom) ? zoom : 17,
                labels: labels
            });
            container._documentMapInstance = instance;
            if (listedOnly) {
                instance.map.removeLayer(instance.cadastral.sheets);
                instance.map.removeLayer(instance.cadastral.parcels);
            }

            var records = findPropertyRows();
            var targets = new Map();
            var dataBounds = window.L.latLngBounds([]);
            var parcelBounds = window.L.latLngBounds([]);
            var listedLayers = {};
            var featureKeys = new Set();
            var parcelSheetKeys = new Set();
            var overviewParcelKeys = new Set();
            var sheetCenters = new Map();
            var overviewMarkers = [];
            var parcelOverviewMarkers = window.L.layerGroup();
            var sheetOverviewMarkers = window.L.layerGroup();
            var updateOverviewMarkers = null;
            var overviewLayoutFrame = null;

            function overlaps(first, second, gap) {
                return first.left < second.right + gap && first.right + gap > second.left
                    && first.top < second.bottom + gap && first.bottom + gap > second.top;
            }

            function layoutOverviewLabels() {
                overviewLayoutFrame = null;
                var active = overviewMarkers.map(function (entry) {
                    var element = entry.marker.getElement();
                    if (!element) return null;
                    var label = element.querySelector('.document-map-overview-marker-label');
                    var dot = element.querySelector('.document-map-overview-dot');
                    if (!label || !dot) return null;
                    var chip = label.parentElement;
                    chip.hidden = false;
                    return { entry: entry, chip: chip, label: label, dot: dot };
                }).filter(Boolean);
                if (!active.length) return;

                active.sort(function (first, second) {
                    return second.entry.priority - first.entry.priority
                        || first.entry.label.localeCompare(second.entry.label, 'it');
                });

                var mapBounds = instance.map.getContainer().getBoundingClientRect();
                var occupied = Array.prototype.map.call(
                    instance.map.getContainer().querySelectorAll('.leaflet-control'),
                    function (control) { return control.getBoundingClientRect(); }
                );
                var fitButton = container.parentElement.querySelector('[data-document-map-fit-all]');
                if (fitButton && !fitButton.disabled) occupied.push(fitButton.getBoundingClientRect());
                active.forEach(function (item) { occupied.push(item.dot.getBoundingClientRect()); });
                var placed = [];
                var mapWidth = mapBounds.width;
                var mapHeight = mapBounds.height;
                var maxRadius = Math.hypot(mapWidth, mapHeight);
                var radiusStep = 24;
                var angleCount = 24;

                function insideMap(bounds) {
                    return bounds.left >= mapBounds.left + 2 && bounds.top >= mapBounds.top + 2
                        && bounds.right <= mapBounds.right - 2 && bounds.bottom <= mapBounds.bottom - 2;
                }

                function isAvailable(bounds) {
                    if (!insideMap(bounds)) return false;
                    if (placed.some(function (prior) { return overlaps(bounds, prior, 4); })) return false;
                    return !occupied.some(function (control) { return overlaps(bounds, control, 3); });
                }

                function boundsAt(left, top, width, height) {
                    return { left: left, top: top, right: left + width, bottom: top + height };
                }

                active.forEach(function (item) {
                    var element = item.entry.marker.getElement();
                    var anchor = element.getBoundingClientRect();
                    item.chip.classList.remove('document-map-overview-position-placed');
                    item.chip.style.removeProperty('--document-map-overview-x');
                    item.chip.style.removeProperty('--document-map-overview-y');
                    item.chip.style.removeProperty('--document-map-overview-leader-x');
                    item.chip.style.removeProperty('--document-map-overview-leader-y');
                    item.chip.style.removeProperty('--document-map-overview-leader-length');
                    item.chip.style.removeProperty('--document-map-overview-leader-angle');
                    var chipSize = item.chip.getBoundingClientRect();
                    var width = chipSize.width;
                    var height = chipSize.height;
                    var chosen = null;
                    var step = Math.max(18, Math.min(32, height + 6));

                    // Try positions in expanding rings around the parcel first, then use
                    // the remaining map area so nearby parcels can still get separate labels.
                    for (var radius = 14; radius <= maxRadius && !chosen; radius += step) {
                        for (var angle = 0; angle < angleCount; angle += 1) {
                            var radians = angle * Math.PI * 2 / angleCount;
                            var dx = Math.cos(radians) * radius;
                            var dy = Math.sin(radians) * radius;
                            var left = anchor.left + dx - (dx < 0 ? width : 0);
                            var top = anchor.top + dy - height / 2;
                            var bounds = boundsAt(left, top, width, height);
                            if (isAvailable(bounds)) {
                                chosen = { left: left, top: top, bounds: bounds };
                                break;
                            }
                        }
                    }

                    // Dense clusters can exhaust the nearby rings. Scan the map on a
                    // coarse grid and take the closest free slot for this label.
                    if (!chosen) {
                        var bestDistance = Infinity;
                        for (var y = mapBounds.top + 2; y + height <= mapBounds.bottom - 2; y += 18) {
                            for (var x = mapBounds.left + 2; x + width <= mapBounds.right - 2; x += 18) {
                                var gridBounds = boundsAt(x, y, width, height);
                                if (!isAvailable(gridBounds)) continue;
                                var distance = Math.hypot(x + width / 2 - anchor.left, y + height / 2 - anchor.top);
                                if (distance < bestDistance) {
                                    bestDistance = distance;
                                    chosen = { left: x, top: y, bounds: gridBounds };
                                }
                            }
                        }
                    }

                    if (chosen) {
                        item.chip.classList.add('document-map-overview-position-placed');
                        item.chip.style.setProperty('--document-map-overview-x', (chosen.left - anchor.left) + 'px');
                        item.chip.style.setProperty('--document-map-overview-y', (chosen.top - anchor.top) + 'px');

                        var edgeX = Math.max(chosen.bounds.left, Math.min(anchor.left, chosen.bounds.right));
                        var edgeY = Math.max(chosen.bounds.top, Math.min(anchor.top, chosen.bounds.bottom));
                        var leaderX = anchor.left - edgeX;
                        var leaderY = anchor.top - edgeY;
                        item.chip.style.setProperty('--document-map-overview-leader-x', (edgeX - chosen.left) + 'px');
                        item.chip.style.setProperty('--document-map-overview-leader-y', (edgeY - chosen.top) + 'px');
                        item.chip.style.setProperty('--document-map-overview-leader-length', Math.hypot(leaderX, leaderY) + 'px');
                        item.chip.style.setProperty('--document-map-overview-leader-angle', Math.atan2(leaderY, leaderX) + 'rad');
                        placed.push(chosen.bounds);
                    } else {
                        item.chip.hidden = true;
                    }
                });
            }

            function scheduleOverviewLabelLayout() {
                if (!overviewMarkers.length || overviewLayoutFrame !== null) return;
                overviewLayoutFrame = window.requestAnimationFrame(layoutOverviewLabels);
            }

            function addOverviewMarker(group, kind, item, position, approximate) {
                var category = kind === 'parcel' ? cadastralCategory(item.category || item.Categoria) : null;
                var label = overviewLabel(item, kind, container);
                var point = Array.isArray(position)
                    ? window.L.latLng(position[0], position[1]) : position;
                var markerKind = kind === 'sheet' ? 'sheet' : (approximate ? 'parcel-fallback' : 'parcel');
                var categoryStyle = category
                    ? ' style="--document-map-overview-category-background:' + category.color + '"' : '';
                var icon = window.L.divIcon({
                    className: 'document-map-overview-icon',
                    html: '<span class="document-map-overview-marker-point document-map-overview-marker-point--' + markerKind + '">' +
                        '<span class="document-map-overview-dot" aria-hidden="true"></span></span>' +
                        '<span class="document-map-overview-marker document-map-overview-marker--' + markerKind + '"' + categoryStyle + '>' +
                        '<span class="document-map-overview-leader" aria-hidden="true"></span>' +
                        '<span class="document-map-overview-marker-label">' + escapeHtml(label) + '</span></span>',
                    iconSize: [1, 1],
                    iconAnchor: [0, 0]
                });
                var marker = window.L.marker(point, {
                    icon: icon,
                    title: label,
                    alt: label,
                    keyboard: true,
                    riseOnHover: true,
                    zIndexOffset: 1000
                });
                var popup = document.createElement('span');
                popup.textContent = approximate
                    ? label + ' · ' + localized(container, 'approximate_location', 'Posizione indicativa: centro del foglio catastale')
                    : label;
                marker.bindPopup(popup);
                overviewMarkers.push({
                    marker: marker,
                    label: label,
                    priority: approximate ? 3 : (kind === 'parcel' ? 2 : 1)
                });
                if (kind === 'parcel') {
                    var row = resolveRow(records, item);
                    var target = rowTarget(targets, row);
                    if (target && !target.point) target.point = [point.lat, point.lng];
                    marker.on('click', function () { if (row) highlightRow(row.row); });
                }
                group.addLayer(marker);
            }

            function featureCenter(feature) {
                var featureLayer = window.L.geoJSON(feature);
                var bounds = featureLayer.getBounds();
                return bounds.isValid() ? bounds.getCenter() : null;
            }

            function addListedGeometry(key, collection, style) {
                var features = collection && Array.isArray(collection.features) ? collection.features : [];
                var layer = window.L.geoJSON({ type: 'FeatureCollection', features: features }, {
                    style: style,
                    onEachFeature: function (feature, featureLayer) {
                        var properties = feature.properties || {};
                        var popup = document.createElement('span');
                        popup.textContent = properties.reference || localized(container, 'feature_fallback', 'Elemento catastale');
                        featureLayer.bindPopup(popup);
                        if (key === 'parcels') {
                            var polygonLabel = document.createElement('span');
                            polygonLabel.textContent = overviewLabel(properties, 'parcel', container);
                            featureLayer.bindTooltip(polygonLabel, {
                                permanent: true,
                                direction: 'center',
                                className: 'document-map-parcel-polygon-label',
                                opacity: 1,
                                interactive: false
                            });
                            featureKeys.add(parcelMapKey(properties));
                            parcelSheetKeys.add(parcelSheetKey(properties));
                            var row = resolveRow(records, properties);
                            var target = rowTarget(targets, row);
                            if (target) target.layers.push(featureLayer);
                            featureLayer.on('click', function () { if (row) highlightRow(row.row); });
                        }
                    }
                }).addTo(instance.map);
                if (layer.getBounds().isValid()) {
                    dataBounds.extend(layer.getBounds());
                    if (key === 'parcels') parcelBounds.extend(layer.getBounds());
                }
                listedLayers[key] = layer;
            }

            if (listedOnly) {
                addListedGeometry('sheets', sheetFeatures, {
                    color: '#1976a8', weight: 2, fillColor: '#1976a8', fillOpacity: 0.05
                });
                addListedGeometry('parcels', parcelFeatures, {
                    color: '#d63384', weight: 2, fillColor: '#d63384', fillOpacity: 0.18
                });
            }

            var markerBounds = [];
            parcelMarkers.forEach(function (item) {
                var markerLat = Number(item.latitude);
                var markerLng = Number(item.longitude);
                if (!Number.isFinite(markerLat) || !Number.isFinite(markerLng)) return;
                var markerKey = parcelMapKey(item);
                if (listedOnly && !overviewParcelKeys.has(markerKey)) {
                    addOverviewMarker(parcelOverviewMarkers, 'parcel', item, [markerLat, markerLng], false);
                    overviewParcelKeys.add(markerKey);
                }
                if (listedOnly && featureKeys.has(markerKey)) return;
                parcelSheetKeys.add(parcelSheetKey(item));
                var selected = Boolean(item.selected);
                var marker = window.L.circleMarker([markerLat, markerLng], {
                    radius: selected ? 8 : 6,
                    color: '#ffffff',
                    weight: 2,
                    fillColor: selected ? '#d63384' : '#0d6efd',
                    fillOpacity: 1
                }).addTo(instance.map);
                var popup = document.createElement('span');
                popup.textContent = item.reference || localized(container, 'parcel_fallback', 'Particella');
                marker.bindPopup(popup);
                var row = resolveRow(records, item);
                var target = rowTarget(targets, row);
                if (target) {
                    target.layers.push(marker);
                    target.point = [markerLat, markerLng];
                }
                marker.on('click', function () { if (row) highlightRow(row.row); });
                if (selected) marker.openPopup();
                markerBounds.push([markerLat, markerLng]);
                dataBounds.extend([markerLat, markerLng]);
                parcelBounds.extend([markerLat, markerLng]);
            });

            if (listedOnly) {
                (parcelFeatures.features || []).forEach(function (feature) {
                    var properties = feature.properties || {};
                    var key = parcelMapKey(properties);
                    if (overviewParcelKeys.has(key)) return;
                    var center = featureCenter(feature);
                    if (!center) return;
                    addOverviewMarker(parcelOverviewMarkers, 'parcel', properties, center, false);
                    overviewParcelKeys.add(key);
                });

                (sheetFeatures.features || []).forEach(function (feature) {
                    var properties = feature.properties || {};
                    var center = featureCenter(feature);
                    if (!center) return;
                    sheetCenters.set(parcelSheetKey(properties), center);
                    addOverviewMarker(sheetOverviewMarkers, 'sheet', properties, center, false);
                });

                unmappedParcels.forEach(function (item) {
                    var key = parcelMapKey(item);
                    if (overviewParcelKeys.has(key)) return;
                    var center = sheetCenters.get(parcelSheetKey(item));
                    if (!center) return;
                    addOverviewMarker(parcelOverviewMarkers, 'parcel', item, center, true);
                    overviewParcelKeys.add(key);
                });

                var overviewMaxZoom = 14;
                updateOverviewMarkers = function () {
                    var visible = instance.map.getZoom() <= overviewMaxZoom;
                    container.classList.toggle('document-map-polygons-labeled', !visible);
                    [
                        [listedLayers.parcels, parcelOverviewMarkers],
                        [listedLayers.sheets, sheetOverviewMarkers]
                    ].forEach(function (entry) {
                        if (!entry[0]) return;
                        if (visible && !entry[0].hasLayer(entry[1])) entry[0].addLayer(entry[1]);
                        else if (!visible && entry[0].hasLayer(entry[1])) entry[0].removeLayer(entry[1]);
                    });
                    scheduleOverviewLabelLayout();
                };
                instance.map.on('zoomend', updateOverviewMarkers);
                instance.map.on('zoomend moveend resize', scheduleOverviewLabelLayout);
                updateOverviewMarkers();
            }

            if (!markerBounds.length && (!listedOnly || !(parcelFeatures.features || []).length)
                && container.dataset.centerKind === 'parcel') {
                var selectedMarker = window.L.circleMarker([latitude, longitude], {
                    radius: 8, color: '#ffffff', weight: 2, fillColor: '#d63384', fillOpacity: 1
                }).addTo(instance.map);
                var selectedPopup = document.createElement('span');
                selectedPopup.textContent = container.dataset.reference || localized(container, 'selected_parcel', 'Particella selezionata');
                selectedMarker.bindPopup(selectedPopup).openPopup();
                markerBounds.push([latitude, longitude]);
                dataBounds.extend([latitude, longitude]);
                parcelBounds.extend([latitude, longitude]);
            }

            if (listedOnly) {
                var fallbackSheets = (sheetFeatures.features || []).filter(function (feature) {
                    return !parcelSheetKeys.has(parcelSheetKey(feature.properties || {}));
                });
                if (fallbackSheets.length) {
                    var fallbackBounds = window.L.geoJSON({
                        type: 'FeatureCollection',
                        features: fallbackSheets
                    }).getBounds();
                    if (fallbackBounds.isValid()) parcelBounds.extend(fallbackBounds);
                }
            }

            var fitAllButton = container.parentElement.querySelector('[data-document-map-fit-all]');
            if (fitAllButton) {
                fitAllButton.disabled = !parcelBounds.isValid();
                fitAllButton.addEventListener('click', function () {
                    if (parcelBounds.isValid()) {
                        instance.map.fitBounds(parcelBounds, { padding: [32, 32], maxZoom: 18 });
                    }
                });
            }

            if (listedOnly && dataBounds.isValid()) {
                instance.map.fitBounds(dataBounds, { padding: [28, 28], maxZoom: 17 });
            } else if (markerBounds.length > 1) {
                instance.map.fitBounds(markerBounds, { padding: [28, 28], maxZoom: 17 });
            }
            if (updateOverviewMarkers) updateOverviewMarkers();

            addMapActions(records, targets, instance.map, container);
            document.querySelectorAll('[data-document-map-layer]').forEach(function (toggle) {
                if (toggle.dataset.documentMapLayer !== container.id) return;
                var layer = listedLayers[toggle.dataset.layerKey];
                if (!layer) return;
                function applyListedLayerState() {
                    if (toggle.checked) layer.addTo(instance.map);
                    else instance.map.removeLayer(layer);
                }
                applyListedLayerState();
                toggle.addEventListener('change', function () {
                    applyListedLayerState();
                    scheduleOverviewLabelLayout();
                });
            });

            document.querySelectorAll('[data-land-registry-layer-map]').forEach(function (toggle) {
                if (toggle.dataset.landRegistryLayerMap !== container.id) return;
                var layer = instance.cadastral[toggle.dataset.layerKey];
                if (!layer) return;
                function applyLayerState() {
                    if (toggle.checked) layer.addTo(instance.map);
                    else instance.map.removeLayer(layer);
                }
                applyLayerState();
                toggle.addEventListener('change', applyLayerState);
            });

            container.dataset.mapInitialized = 'true';
            clearStatus(container);
            window.requestAnimationFrame(function () {
                instance.map.invalidateSize();
                scheduleOverviewLabelLayout();
            });
            window.addEventListener('resize', function () {
                instance.map.invalidateSize();
                scheduleOverviewLabelLayout();
            }, { passive: true });
        } catch (error) {
            if (container._documentMapInstance) {
                container._documentMapInstance.map.remove();
                delete container._documentMapInstance;
            }
            var message = error && error.message && error.message.indexOf('Map data request failed') === 0
                ? localized(container, 'data_error', 'I dati delle particelle non sono disponibili. Controlla la connessione e riprova.')
                : localized(container, 'load_error', 'La mappa catastale non è stata caricata. Riprova tra poco.');
            setStatus(container, message, true);
            if (window.console) console.error('[DocumentParcelMap] initialization failed', error);
        } finally {
            delete container.dataset.mapLoading;
        }
    }

    function initializeMaps() {
        document.querySelectorAll('[data-document-parcel-map]').forEach(function (container) {
            var parts = elementsFor(container);
            if (parts.retry) {
                parts.retry.addEventListener('click', function () {
                    if (parts.retry.dataset.documentMapScriptRetry === 'true') {
                        window.location.reload();
                        return;
                    }
                    initializeMap(container);
                });
            }
            initializeMap(container);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initializeMaps, { once: true });
    } else {
        initializeMaps();
    }
})();
