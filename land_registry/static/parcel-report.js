// ========================================
// PRINTABLE PARCEL DOSSIER
// ========================================
// Builds a print-safe snapshot of the current parcel panel. The browser print
// dialog provides PDF export without requiring a server-side rendering binary.

(function () {
    let returnFocus = null;

    function _escapeHtml(value) {
        return String(value ?? '').replace(/[&<>'"]/g, (char) => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
        })[char]);
    }

    function _parcelReference(feature) {
        const props = (feature && feature.properties) || {};
        return props.NATIONALCADASTRALREFERENCE
            || props.nationalcadastralreference
            || props.national_cadastral_reference
            || props.national_reference
            || [props.sheet_number || props.foglio, props.parcel_number || props.particella]
                .filter(Boolean).join('/')
            || 'Particella selezionata';
    }

    function _parcelCenter(feature, layer) {
        const props = (feature && feature.properties) || {};
        const lat = Number(props.centroid_lat), lng = Number(props.centroid_lng);
        if (Number.isFinite(lat) && Number.isFinite(lng)) return { lat, lng };
        try {
            const bounds = layer && layer.getBounds && layer.getBounds();
            if (bounds && bounds.isValid()) {
                const center = bounds.getCenter();
                return { lat: center.lat, lng: center.lng };
            }
        } catch (error) { /* report still works without a centroid */ }
        return null;
    }

    function _geometryRings(feature) {
        const geometry = feature && feature.geometry;
        if (!geometry || !geometry.coordinates) return [];
        if (geometry.type === 'Polygon') return geometry.coordinates;
        if (geometry.type === 'MultiPolygon') {
            return geometry.coordinates.reduce((rings, polygon) => rings.concat(polygon), []);
        }
        return [];
    }

    function _geometrySvg(feature) {
        const rings = _geometryRings(feature).filter(ring => Array.isArray(ring) && ring.length >= 3);
        const points = rings.flat().filter(point => Array.isArray(point) && point.length >= 2)
            .map(point => [Number(point[0]), Number(point[1])])
            .filter(point => point.every(Number.isFinite));
        if (!points.length) return '';

        const width = 560, height = 210, padding = 18;
        const xs = points.map(point => point[0]), ys = points.map(point => point[1]);
        const minX = Math.min(...xs), maxX = Math.max(...xs);
        const minY = Math.min(...ys), maxY = Math.max(...ys);
        const spanX = Math.max(maxX - minX, 1e-9), spanY = Math.max(maxY - minY, 1e-9);
        const scale = Math.min((width - padding * 2) / spanX, (height - padding * 2) / spanY);
        const offsetX = (width - spanX * scale) / 2;
        const offsetY = (height - spanY * scale) / 2;
        const project = point => [
            offsetX + (point[0] - minX) * scale,
            height - offsetY - (point[1] - minY) * scale,
        ];
        const path = rings.map(ring => ring
            .map((point, index) => {
                const projected = project([Number(point[0]), Number(point[1])]);
                return `${index ? 'L' : 'M'}${projected[0].toFixed(2)} ${projected[1].toFixed(2)}`;
            }).join(' ') + ' Z').join(' ');
        return `
            <figure class="parcel-report-geometry">
                <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Sagoma della particella">
                    <path d="${path}" fill-rule="evenodd"></path>
                </svg>
                <figcaption>Sagoma catastale · geometria non in scala di stampa</figcaption>
            </figure>`;
    }

    /** Web Mercator lat/lng -> world pixel at a given zoom (Leaflet/Google tile scheme). */
    function _project(lat, lng, zoom, tileSize) {
        const sinLat = Math.sin(lat * Math.PI / 180);
        const x = (lng + 180) / 360;
        const y = 0.5 - Math.log((1 + sinLat) / (1 - sinLat)) / (4 * Math.PI);
        const mapSize = tileSize * Math.pow(2, zoom);
        return { x: x * mapSize, y: y * mapSize };
    }

    /** The base tile layer currently visible on the live Folium/Leaflet map, if any. */
    function _activeBaseTileLayer() {
        try {
            const els = document.querySelectorAll('.leaflet-container');
            for (const el of els) {
                const map = window[el.id];
                if (!map || typeof map.eachLayer !== 'function') continue;
                let found = null;
                map.eachLayer(layer => {
                    if (!found && layer instanceof L.TileLayer && map.hasLayer(layer) && layer._url) found = layer;
                });
                if (found) return found;
            }
        } catch (error) { /* fall through to no map figure */ }
        return null;
    }

    /**
     * A static basemap "screenshot" of where the parcel actually is, built from
     * the same tile provider the live map uses — no canvas/html2canvas, just an
     * absolutely-positioned mosaic of <img> tiles (prints fine) plus an SVG
     * outline on top. _geometrySvg() alone only draws an unscaled abstract
     * shape with no location context, which is what "no map in the report" meant.
     */
    function _staticMapHtml(feature) {
        const tileLayer = _activeBaseTileLayer();
        if (!tileLayer) return '';

        const points = _geometryRings(feature)
            .filter(ring => Array.isArray(ring) && ring.length >= 3)
            .flat()
            .map(point => [Number(point[1]), Number(point[0])]) // [lng,lat] -> [lat,lng]
            .filter(point => point.every(Number.isFinite));
        if (!points.length) return '';

        const lats = points.map(p => p[0]), lngs = points.map(p => p[1]);
        const minLat = Math.min(...lats), maxLat = Math.max(...lats);
        const minLng = Math.min(...lngs), maxLng = Math.max(...lngs);
        const centerLat = (minLat + maxLat) / 2, centerLng = (minLng + maxLng) / 2;

        const tileSize = 256;
        const width = 560, height = 320;
        const maxZoom = Math.min(
            tileLayer.options && tileLayer.options.maxNativeZoom || 19,
            tileLayer.options && tileLayer.options.maxZoom || 19,
            19
        );

        // Pick the deepest zoom where the parcel still fits comfortably inside
        // the frame (with surrounding context), so a tiny urban lot and a large
        // rural field both render at a sensible scale.
        let zoom = maxZoom;
        for (let candidate = maxZoom; candidate >= 10; candidate--) {
            const p1 = _project(minLat, minLng, candidate, tileSize);
            const p2 = _project(maxLat, maxLng, candidate, tileSize);
            const spanX = Math.abs(p2.x - p1.x), spanY = Math.abs(p1.y - p2.y);
            zoom = candidate;
            if (spanX <= width * 0.5 && spanY <= height * 0.5) break;
        }

        const center = _project(centerLat, centerLng, zoom, tileSize);
        const windowMinX = center.x - width / 2;
        const windowMinY = center.y - height / 2;
        const tileMinX = Math.floor(windowMinX / tileSize);
        const tileMaxX = Math.floor((windowMinX + width) / tileSize);
        const tileMinY = Math.floor(windowMinY / tileSize);
        const tileMaxY = Math.floor((windowMinY + height) / tileSize);
        const tileCount = 2 ** zoom;

        const subdomains = (tileLayer.options && tileLayer.options.subdomains) || 'abc';
        const subdomain = Array.isArray(subdomains) ? subdomains[0] : subdomains[0];

        const tiles = [];
        for (let tx = tileMinX; tx <= tileMaxX; tx++) {
            for (let ty = tileMinY; ty <= tileMaxY; ty++) {
                const wrappedX = ((tx % tileCount) + tileCount) % tileCount;
                if (ty < 0 || ty >= tileCount) continue;
                const url = tileLayer._url
                    .replace('{s}', subdomain)
                    .replace('{z}', zoom)
                    .replace('{x}', wrappedX)
                    .replace('{y}', ty);
                const left = (tx * tileSize - windowMinX).toFixed(1);
                const top = (ty * tileSize - windowMinY).toFixed(1);
                tiles.push(
                    `<img src="${_escapeHtml(url)}" style="position:absolute;left:${left}px;top:${top}px;width:${tileSize}px;height:${tileSize}px;" loading="eager" crossorigin="anonymous">`
                );
            }
        }

        const outlinePoints = _geometryRings(feature)
            .filter(ring => Array.isArray(ring) && ring.length >= 3)
            .map(ring => ring
                .map(point => {
                    const projected = _project(Number(point[1]), Number(point[0]), zoom, tileSize);
                    return `${(projected.x - windowMinX).toFixed(1)},${(projected.y - windowMinY).toFixed(1)}`;
                }).join(' '));
        const outlinePath = outlinePoints.map(pointsAttr => `<polygon points="${pointsAttr}"></polygon>`).join('');

        return `
            <figure class="parcel-report-map">
                <div class="parcel-report-map-frame" style="width:${width}px;height:${height}px;">
                    ${tiles.join('')}
                    <svg class="parcel-report-map-overlay" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">
                        ${outlinePath}
                    </svg>
                </div>
                <figcaption>${_escapeHtml(tileLayer.options && tileLayer.options.attribution || '')} · zoom ${zoom}</figcaption>
            </figure>`;
    }

    function _snapshotPanel(source) {
        const clone = source.cloneNode(true);
        clone.querySelectorAll('.parcel-actions, button, script').forEach(node => node.remove());
        clone.querySelectorAll('details').forEach(details => { details.open = true; });
        clone.querySelectorAll('select').forEach(select => {
            const value = document.createElement('div');
            value.className = 'parcel-report-control-value';
            value.textContent = select.options[select.selectedIndex]
                ? select.options[select.selectedIndex].textContent
                : select.value;
            select.replaceWith(value);
        });
        clone.querySelectorAll('input').forEach(input => {
            const value = document.createElement('div');
            value.className = 'parcel-report-control-value';
            value.textContent = input.value || '—';
            input.replaceWith(value);
        });
        const extraProperties = clone.querySelector('.parcel-extra-info');
        if (extraProperties) clone.appendChild(extraProperties);
        clone.querySelectorAll('[id]').forEach(node => node.removeAttribute('id'));
        return clone.innerHTML;
    }

    window.openParcelReport = function () {
        const overlay = document.getElementById('parcelReportOverlay');
        const report = document.getElementById('parcelReportContent');
        const panel = document.getElementById('parcelInfoContent');
        const feature = window.currentParcelFeature;
        if (!overlay || !report || !panel || !feature) return;

        returnFocus = document.activeElement;
        const reference = _parcelReference(feature);
        const center = _parcelCenter(feature, window.currentParcelLayer);
        const geometry = feature.geometry && feature.geometry.type;
        const loading = panel.querySelectorAll('.enrichment-loading').length;
        const link = window.location.href;
        // .omi-controls only renders when an OMI quote was actually found for
        // this comune (see parcel-enrichment.js) — the disclaimer about OMI
        // estimates was showing even on reports with no OMI section at all.
        const hasOmiData = !!panel.querySelector('.omi-controls');
        const t = window.t || (key => key);
        report.innerHTML = `
            <header class="parcel-report-heading">
                <div>
                    <div class="parcel-report-brand">${_escapeHtml(t('Land Registry').toUpperCase())}</div>
                    <h1>Dossier particella</h1>
                    <p class="parcel-report-reference">${_escapeHtml(reference)}</p>
                </div>
                <div class="parcel-report-meta">
                    <span>Generato il ${_escapeHtml(new Date().toLocaleString('it-IT'))}</span>
                    ${geometry ? `<span>Geometria: ${_escapeHtml(geometry)}</span>` : ''}
                    ${center ? `<span>Centroide: ${center.lat.toFixed(6)}, ${center.lng.toFixed(6)}</span>` : ''}
                </div>
            </header>
            ${loading ? `<div class="parcel-report-notice"><i class="fa-solid fa-clock"></i> Alcune fonti sono ancora in caricamento; il dossier fotografa i dati attualmente disponibili.</div>` : ''}
            ${_staticMapHtml(feature)}
            ${_geometrySvg(feature)}
            <section class="parcel-report-data">${_snapshotPanel(panel)}</section>
            <footer class="parcel-report-footer">
                <strong>Avvertenza</strong>
                <p>I dati hanno finalità informative.${hasOmiData ? ' Le stime OMI non costituiscono una perizia o una valutazione immobiliare;' : ''} verificare sempre gli atti e le fonti ufficiali.</p>
                <p>Collegamento alla vista originale: <a href="${_escapeHtml(link)}">${_escapeHtml(link)}</a></p>
            </footer>`;
        overlay.hidden = false;
        document.body.classList.add('parcel-report-open');
        const closeButton = overlay.querySelector('.parcel-report-toolbar-btn.secondary');
        if (closeButton) closeButton.focus();
    };

    window.closeParcelReport = function () {
        const overlay = document.getElementById('parcelReportOverlay');
        if (overlay) overlay.hidden = true;
        document.body.classList.remove('parcel-report-open', 'parcel-report-printing');
        if (returnFocus && typeof returnFocus.focus === 'function') returnFocus.focus();
        returnFocus = null;
    };

    window.printParcelReport = function () {
        document.body.classList.add('parcel-report-printing');
        window.print();
    };

    window.addEventListener('afterprint', () => {
        document.body.classList.remove('parcel-report-printing');
    });
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape') window.closeParcelReport();
    });

    function _openDeepLinkedReport() {
        if (new URLSearchParams(window.location.search).get('report') !== '1') return;
        let attempts = 0;
        const timer = setInterval(() => {
            attempts += 1;
            const panel = document.getElementById('parcelInfoContent');
            const loading = panel && panel.querySelector('.enrichment-loading');
            if (window.currentParcelFeature && (!loading || attempts >= 60)) {
                clearInterval(timer);
                window.openParcelReport();
            } else if (attempts >= 60) {
                clearInterval(timer);
            }
        }, 250);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', _openDeepLinkedReport, { once: true });
    } else {
        _openDeepLinkedReport();
    }
})();
