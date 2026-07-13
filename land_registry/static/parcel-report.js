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
        report.innerHTML = `
            <header class="parcel-report-heading">
                <div>
                    <div class="parcel-report-brand">LAND REGISTRY</div>
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
            ${_geometrySvg(feature)}
            <section class="parcel-report-data">${_snapshotPanel(panel)}</section>
            <footer class="parcel-report-footer">
                <strong>Avvertenza</strong>
                <p>I dati hanno finalità informative. Le stime OMI non costituiscono una perizia o una valutazione immobiliare; verificare sempre gli atti e le fonti ufficiali.</p>
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
