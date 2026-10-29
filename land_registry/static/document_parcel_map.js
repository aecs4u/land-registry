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
            if (record.actionAdded) return;
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
            if (target && (target.layers.length || target.point)) {
                record.parcelCell.appendChild(button);
                record.actionAdded = true;
            }
        });
    }

    async function fetchMapData(url) {
        var response = await fetch(url, {
            credentials: 'same-origin',
            headers: { Accept: 'application/json' }
        });
        if (!response.ok) throw new Error('Map data request failed with status ' + response.status);
        return response.json();
    }

    async function initializeMap(container) {
        if (container.dataset.mapLoading === 'true' || container.dataset.mapInitialized === 'true') return;
        container.dataset.mapLoading = 'true';
        setStatus(container, localized(container, 'loading', 'Caricamento mappa catastale…'), false);
        var urlHadView = new URLSearchParams(window.location.search).has('map');
        var userInteracted = false;
        ['pointerdown', 'wheel', 'keydown', 'touchstart'].forEach(function (name) {
            container.addEventListener(name, function () { userInteracted = true; }, { passive: true });
        });
        try {
            if (!window.L || !window.LandRegistryMap) {
                throw new Error('Leaflet or LandRegistryMap did not load');
            }
            var dataUrl = container.dataset.mapDataUrl;
            if (!dataUrl) throw new Error('Map data URL is missing');
            // The first answer holds what the server has placed so far; `done: false` + `next` mean more follows
            // (`?after=<next>`), drawn as it arrives. Plain answers (a document's map) carry neither and are complete.
            var mapData = await fetchMapData(dataUrl);

            var latitude = Number(container.dataset.centerLat);
            var longitude = Number(container.dataset.centerLng);
            var zoom = Number(container.dataset.zoom);
            if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) {
                throw new Error('Invalid map center coordinates');
            }

            var labels = {};
            try { labels = JSON.parse(container.dataset.labels || '{}'); } catch (_error) { labels = {}; }
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
            // Parcels without geometry have no true position; they stay visible at every zoom.
            var approximateParcelMarkers = window.L.layerGroup();
            var updateOverviewMarkers = null;
            var overviewLayoutFrame = null;
            var markerBounds = [];
            var listedSeen = { sheets: new Set(), parcels: new Set() };
            var overviewSheetKeys = new Set();
            var foundMarkers = 0;
            var selectedFallbackAdded = false;
            var fitAllButton = null;

            // Clustering: the markers of a group are not added to it one by one. At every zoom the ones whose points fall in
            // the same grid cell (of `clusterCellPixels`, in world pixels, so panning never regroups them) are shown as one
            // count bubble; a click zooms to its members, or lists them when they all sit on the same point (the
            // approximate position of a comune is shared by all its parcels without an outline).
            var clusterCellPixels = 56;
            var clusterEntries = new Map();
            [parcelOverviewMarkers, sheetOverviewMarkers, approximateParcelMarkers].forEach(function (group) {
                clusterEntries.set(group, []);
            });

            function placeOf(item) {
                var name = String(item.municipality || item.Comune || '').trim();
                var province = String(item.province || item.Provincia || '').trim();
                return name ? (province ? name + ' (' + province + ')' : name) : '';
            }

            function clusterMarker(group, members, kind) {
                var count = members.length;
                var latSum = 0;
                var lngSum = 0;
                var places = new Set();
                members.forEach(function (entry) {
                    latSum += entry.latlng.lat;
                    lngSum += entry.latlng.lng;
                    if (entry.place) places.add(entry.place);
                });
                var place = places.size === 1 ? Array.from(places)[0] : '';
                var size = count < 10 ? 34 : (count < 100 ? 40 : 48);
                var noun = kind === 'sheet'
                    ? localized(container, 'cluster_sheets', 'fogli') : localized(container, 'cluster_parcels', 'particelle');
                var title = (place ? place + ' · ' : '') + count + ' ' + noun;
                var icon = window.L.divIcon({
                    className: 'document-map-cluster-icon',
                    html: '<span class="document-map-cluster document-map-cluster--' + kind + '" style="width:' + size + 'px;height:' + size + 'px">' +
                        '<span class="document-map-cluster-count">' + count + '</span></span>' +
                        (place ? '<span class="document-map-cluster-place">' + escapeHtml(place) + '</span>' : ''),
                    iconSize: [size, size],
                    iconAnchor: [size / 2, size / 2]
                });
                var marker = window.L.marker(window.L.latLng(latSum / count, lngSum / count), {
                    icon: icon,
                    title: title,
                    alt: title,
                    keyboard: true,
                    riseOnHover: true,
                    zIndexOffset: 1500 + count
                });
                marker.on('click', function () {
                    var bounds = window.L.latLngBounds(members.map(function (entry) { return entry.latlng; }));
                    var span = instance.map.project(bounds.getNorthEast(), 20).distanceTo(instance.map.project(bounds.getSouthWest(), 20));
                    if (span >= 12) {
                        instance.map.fitBounds(bounds, { padding: [40, 40], maxZoom: 18 });
                        return;
                    }
                    var list = document.createElement('div');
                    list.className = 'document-map-cluster-list';
                    var heading = document.createElement('strong');
                    heading.textContent = title;
                    list.appendChild(heading);
                    members.slice(0, 15).forEach(function (entry) {
                        var line = document.createElement('div');
                        line.textContent = entry.label;
                        list.appendChild(line);
                    });
                    if (count > 15) {
                        var more = document.createElement('div');
                        more.textContent = '+' + (count - 15);
                        list.appendChild(more);
                    }
                    marker.bindPopup(list).openPopup();
                });
                return marker;
            }

            function renderClusters(group) {
                var entries = clusterEntries.get(group) || [];
                group.clearLayers();
                var zoom = instance.map.getZoom();
                var cells = new Map();
                entries.forEach(function (entry) {
                    var point = instance.map.project(entry.latlng, zoom);
                    var key = Math.floor(point.x / clusterCellPixels) + ':' + Math.floor(point.y / clusterCellPixels);
                    var cell = cells.get(key);
                    if (!cell) cells.set(key, cell = []);
                    cell.push(entry);
                });
                cells.forEach(function (members) {
                    if (members.length < 2) group.addLayer(members[0].marker);
                    else group.addLayer(clusterMarker(group, members, members[0].kind));
                });
            }

            function overlaps(first, second, gap) {
                return first.left < second.right + gap && first.right + gap > second.left
                    && first.top < second.bottom + gap && first.bottom + gap > second.top;
            }

            function layoutOverviewLabels() {
                overviewLayoutFrame = null;
                var mapBounds = instance.map.getContainer().getBoundingClientRect();
                var active = overviewMarkers.map(function (entry) {
                    var element = entry.marker.getElement();
                    if (!element) return null;
                    var label = element.querySelector('.document-map-overview-marker-label');
                    var dot = element.querySelector('.document-map-overview-dot');
                    if (!label || !dot) return null;
                    var chip = label.parentElement;
                    // A marker outside the visible map shows no label: the chip would be placed in free space of the
                    // map, with a leader line to a point nobody can see.
                    var point = dot.getBoundingClientRect();
                    var pointX = point.left + point.width / 2;
                    var pointY = point.top + point.height / 2;
                    if (pointX < mapBounds.left || pointX > mapBounds.right || pointY < mapBounds.top || pointY > mapBounds.bottom) {
                        chip.hidden = true;
                        return null;
                    }
                    chip.hidden = false;
                    return { entry: entry, chip: chip, label: label, dot: dot };
                }).filter(Boolean);
                if (!active.length) return;

                active.sort(function (first, second) {
                    return second.entry.priority - first.entry.priority
                        || first.entry.label.localeCompare(second.entry.label, 'it');
                });
                // Labels the user has moved keep their spot; the others are placed around them.
                active.sort(function (first, second) {
                    return (second.entry.manual ? 1 : 0) - (first.entry.manual ? 1 : 0);
                });

                var occupied = Array.prototype.map.call(
                    instance.map.getContainer().querySelectorAll('.leaflet-control'),
                    function (control) { return control.getBoundingClientRect(); }
                );
                var fitButton = container.parentElement.querySelector('[data-document-map-fit-all]');
                if (fitButton && !fitButton.disabled) occupied.push(fitButton.getBoundingClientRect());
                Array.prototype.forEach.call(
                    instance.map.getContainer().querySelectorAll('.document-map-cluster, .document-map-cluster-place'),
                    function (cluster) { occupied.push(cluster.getBoundingClientRect()); }
                );
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
                    if (item.entry.manual) {
                        var manualLeft = anchor.left + item.entry.manual.dx;
                        var manualTop = anchor.top + item.entry.manual.dy;
                        chosen = { left: manualLeft, top: manualTop, bounds: boundsAt(manualLeft, manualTop, width, height) };
                    }
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

            // Let the user drag a label chip to resolve overlaps; the offset is kept relative
            // to its marker, so it follows pans and zooms. Double-click restores automatic placement.
            function enableLabelDrag(entry) {
                var element = entry.marker.getElement();
                var chip = element && element.querySelector('.document-map-overview-marker');
                if (!chip || chip.dataset.dragBound === 'true') return;
                chip.dataset.dragBound = 'true';
                chip.classList.add('document-map-overview-draggable');
                chip.title = localized(container, 'drag_label',
                    'Trascina per spostare l’etichetta (doppio clic per ripristinare)');
                window.L.DomEvent.on(chip, 'mousedown touchstart dblclick', window.L.DomEvent.stopPropagation);
                var drag = null;
                chip.addEventListener('pointerdown', function (event) {
                    if (event.button) return;
                    var rect = chip.getBoundingClientRect();
                    drag = { id: event.pointerId, x: event.clientX, y: event.clientY, left: rect.left, top: rect.top, moved: false };
                    chip.setPointerCapture(event.pointerId);
                    event.stopPropagation();
                });
                chip.addEventListener('pointermove', function (event) {
                    if (!drag || event.pointerId !== drag.id) return;
                    var dx = event.clientX - drag.x;
                    var dy = event.clientY - drag.y;
                    if (!drag.moved && Math.hypot(dx, dy) < 4) return;
                    drag.moved = true;
                    chip.classList.add('document-map-overview-dragging');
                    var anchor = element.getBoundingClientRect();
                    var mapBounds = instance.map.getContainer().getBoundingClientRect();
                    var rect = chip.getBoundingClientRect();
                    var left = Math.max(mapBounds.left + 2, Math.min(drag.left + dx, mapBounds.right - rect.width - 2));
                    var top = Math.max(mapBounds.top + 2, Math.min(drag.top + dy, mapBounds.bottom - rect.height - 2));
                    entry.manual = { dx: left - anchor.left, dy: top - anchor.top };
                    scheduleOverviewLabelLayout();
                });
                function endDrag(event) {
                    if (!drag || event.pointerId !== drag.id) return;
                    var moved = drag.moved;
                    drag = null;
                    chip.classList.remove('document-map-overview-dragging');
                    if (!moved) return;
                    // The click that follows a drag must not open the marker popup.
                    var swallow = function (clickEvent) { clickEvent.stopPropagation(); clickEvent.preventDefault(); };
                    chip.addEventListener('click', swallow, true);
                    window.setTimeout(function () { chip.removeEventListener('click', swallow, true); }, 0);
                }
                chip.addEventListener('pointerup', endDrag);
                chip.addEventListener('pointercancel', endDrag);
                chip.addEventListener('dblclick', function () {
                    entry.manual = null;
                    scheduleOverviewLabelLayout();
                });
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
                var entry = {
                    marker: marker,
                    label: label,
                    manual: null,
                    latlng: point,
                    kind: markerKind,
                    place: placeOf(item),
                    priority: approximate ? 3 : (kind === 'parcel' ? 2 : 1)
                };
                overviewMarkers.push(entry);
                clusterEntries.get(group).push(entry);
                // The icon element is recreated whenever the marker's layer is re-added.
                marker.on('add', function () { enableLabelDrag(entry); });
                if (kind === 'parcel') {
                    var row = resolveRow(records, item);
                    var target = rowTarget(targets, row);
                    if (target && !target.point) target.point = [point.lat, point.lng];
                    marker.on('click', function () { if (row) highlightRow(row.row); });
                }
                // shown (alone or inside a cluster) by renderClusters
            }

            function featureCenter(feature) {
                var featureLayer = window.L.geoJSON(feature);
                var bounds = featureLayer.getBounds();
                return bounds.isValid() ? bounds.getCenter() : null;
            }

            function addListedGeometry(key, collection, style) {
                // Additive: a later increment adds its features to the same layer (the layer toggles keep working).
                var features = (collection && Array.isArray(collection.features) ? collection.features : []).filter(function (feature) {
                    var properties = feature.properties || {};
                    var id = key === 'parcels' ? parcelMapKey(properties) : (properties.reference || parcelSheetKey(properties));
                    if (listedSeen[key].has(id)) return false;
                    listedSeen[key].add(id);
                    return true;
                });
                var layer = listedLayers[key];
                if (!layer) {
                    layer = listedLayers[key] = window.L.geoJSON(null, {
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
                }
                if (!features.length) return;
                var collection = { type: 'FeatureCollection', features: features };
                layer.addData(collection);
                // bounds of what was just added (asking the live layer breaks on its permanent tooltips)
                var addedBounds = window.L.geoJSON(collection).getBounds();
                if (addedBounds.isValid()) {
                    dataBounds.extend(addedBounds);
                    if (key === 'parcels') parcelBounds.extend(addedBounds);
                }
            }

            // Draws one increment of the map data; later increments add to what is there (nothing is drawn twice).
            function applyData(data) {
            var parcelMarkers = Array.isArray(data.parcel_markers) ? data.parcel_markers : [];
            var parcelFeatures = data.parcel_features || { type: 'FeatureCollection', features: [] };
            var sheetFeatures = data.sheet_features || { type: 'FeatureCollection', features: [] };
            var unmappedParcels = Array.isArray(data.unmapped_parcels) ? data.unmapped_parcels : [];
            foundMarkers += parcelMarkers.length;

            if (listedOnly) {
                addListedGeometry('sheets', sheetFeatures, {
                    color: '#1976a8', weight: 2, fillColor: '#1976a8', fillOpacity: 0.05
                });
                addListedGeometry('parcels', parcelFeatures, {
                    color: '#d63384', weight: 2, fillColor: '#d63384', fillOpacity: 0.18
                });
            }

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
                    var sheetKey = parcelSheetKey(properties);
                    if (overviewSheetKeys.has(sheetKey)) return;
                    overviewSheetKeys.add(sheetKey);
                    sheetCenters.set(sheetKey, center);
                    addOverviewMarker(sheetOverviewMarkers, 'sheet', properties, center, false);
                });

                unmappedParcels.forEach(function (item) {
                    var key = parcelMapKey(item);
                    if (overviewParcelKeys.has(key)) return;
                    // Without a sheet outline either, use the approximate position the server sends (the centre of what is
                    // known of the comune). Never the map centre: that is another parcel, often in another city.
                    var center = sheetCenters.get(parcelSheetKey(item));
                    if (!center) {
                        var approximateLat = item.latitude === null || item.latitude === undefined || item.latitude === ''
                            ? NaN : Number(item.latitude);
                        var approximateLng = item.longitude === null || item.longitude === undefined || item.longitude === ''
                            ? NaN : Number(item.longitude);
                        if (!Number.isFinite(approximateLat) || !Number.isFinite(approximateLng)) return;
                        center = window.L.latLng(approximateLat, approximateLng);
                    }
                    addOverviewMarker(approximateParcelMarkers, 'parcel', item, center, true);
                    overviewParcelKeys.add(key);
                    parcelBounds.extend(center);
                });

                var overviewMaxZoom = 14;
                if (!updateOverviewMarkers) {
                updateOverviewMarkers = function () {
                    clusterEntries.forEach(function (_entries, group) { renderClusters(group); });
                    var visible = instance.map.getZoom() <= overviewMaxZoom;
                    container.classList.toggle('document-map-polygons-labeled', !visible);
                    if (listedLayers.parcels && !listedLayers.parcels.hasLayer(approximateParcelMarkers)) {
                        listedLayers.parcels.addLayer(approximateParcelMarkers);
                    }
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
                }
                updateOverviewMarkers();
            }

            if (!selectedFallbackAdded && !markerBounds.length && (!listedOnly || !(parcelFeatures.features || []).length)
                && container.dataset.centerKind === 'parcel') {
                selectedFallbackAdded = true;
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


            refreshFoundCounts();
            if (fitAllButton) fitAllButton.disabled = !parcelBounds.isValid();
            }

            // A page rendered while the server was still looking the parcels up says "Trovate 0 di N": put what the
            // data holds so far in that line.
            function refreshFoundCounts() {
                var root = container.closest('.document-land-map-aside, .document-parcel-map-card, aside, section') || document.body;
                var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
                var node;
                while ((node = walker.nextNode())) {
                    var text = node.nodeValue;
                    if (!/Trovate\s+\d+\s+di\s+\d+\s+particelle/.test(text)) continue;
                    text = text.replace(/Trovate\s+\d+(\s+di\s+\d+\s+particelle)/, 'Trovate ' + foundMarkers + '$1');
                    text = text.replace(/(\s+e\s+)\d+(\s+di\s+\d+\s+fogli)/, '$1' + listedSeen.sheets.size + '$2');
                    node.nodeValue = text;
                }
            }

            applyData(mapData);

            fitAllButton = container.parentElement.querySelector('[data-document-map-fit-all]');
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

            // The view and the layers of the page's first map live in the URL (`?map=lat,lng,zoom&layers=l-sheets,c-parcels`:
            // `l-` listed, `c-` cadastral layers), so a link reproduces what is on screen. The other query parameters stay.
            if (!window.documentParcelMapUrlOwner) window.documentParcelMapUrlOwner = container.id;
            if (window.documentParcelMapUrlOwner === container.id) {
                var urlToggles = [];
                document.querySelectorAll('[data-document-map-layer], [data-land-registry-layer-map]').forEach(function (toggle) {
                    var listed = toggle.dataset.documentMapLayer !== undefined;
                    if ((listed ? toggle.dataset.documentMapLayer : toggle.dataset.landRegistryLayerMap) !== container.id) return;
                    urlToggles.push({ toggle: toggle, name: (listed ? 'l-' : 'c-') + toggle.dataset.layerKey });
                });
                var urlParams = new URLSearchParams(window.location.search);
                var layersParam = urlParams.get('layers');
                if (layersParam !== null) {
                    var layersOn = new Set(layersParam.split(',').filter(Boolean));
                    urlToggles.forEach(function (entry) {
                        var wanted = layersOn.has(entry.name);
                        if (entry.toggle.checked === wanted) return;
                        entry.toggle.checked = wanted;
                        entry.toggle.dispatchEvent(new Event('change'));
                    });
                }
                var view = (urlParams.get('map') || '').split(',').map(Number);
                if (view.length === 3 && view.every(Number.isFinite) && Math.abs(view[0]) <= 90 && Math.abs(view[1]) <= 180) {
                    instance.map.setView([view[0], view[1]], Math.min(Math.max(Math.round(view[2]), 1), 20), { animate: false });
                }
                var writeMapUrl = function () {
                    var next = new URLSearchParams(window.location.search);
                    var center = instance.map.getCenter();
                    next.set('map', center.lat.toFixed(5) + ',' + center.lng.toFixed(5) + ',' + instance.map.getZoom());
                    next.set('layers', urlToggles.filter(function (entry) { return entry.toggle.checked; })
                        .map(function (entry) { return entry.name; }).join(','));
                    try {
                        window.history.replaceState(window.history.state, '',
                            window.location.pathname + '?' + next.toString().replace(/%2C/g, ',') + window.location.hash);
                    } catch (_error) { /* a sandboxed frame may not rewrite its URL */ }
                };
                instance.map.on('moveend', writeMapUrl);
                urlToggles.forEach(function (entry) { entry.toggle.addEventListener('change', writeMapUrl); });
                writeMapUrl();
            }

            container.dataset.mapInitialized = 'true';
            clearStatus(container);

            // More to come: fetch and draw the next increments while the map is already usable.
            var increment = mapData;
            while (increment.done === false && increment.next !== undefined && increment.next !== null) {
                setStatus(container, localized(container, 'loading_more', 'Posizionamento delle particelle… {percent}%', {
                    percent: Math.round((Number(increment.progress) || 0) * 100)
                }), false);
                var nextUrl = new URL(dataUrl, window.location.href);
                nextUrl.searchParams.set('after', increment.next);
                increment = await fetchMapData(nextUrl.toString());
                // let the browser paint and answer clicks between two increments
                await new Promise(function (resolve) { window.requestAnimationFrame(function () { window.setTimeout(resolve, 0); }); });
                applyData(increment);
                if (updateOverviewMarkers) updateOverviewMarkers();
                addMapActions(records, targets, instance.map, container);
            }
            // nobody moved the map and no view came in the URL: show everything that was found, not just the first chunk
            if (mapData.done === false && !userInteracted && !urlHadView && dataBounds.isValid()) {
                instance.map.fitBounds(dataBounds, { padding: [28, 28], maxZoom: 17 });
            }
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
