(function () {
    'use strict';

    function setStatus(container, message, hidden) {
        var status = container.parentElement && container.parentElement.querySelector('[data-map-status]');
        if (!status) return;
        if (message) status.textContent = message;
        status.hidden = Boolean(hidden);
    }

    function initializeMaps() {
        document.querySelectorAll('[data-land-registry-map]').forEach(function (container) {
            if (container.dataset.mapInitialized === 'true') return;
            if (!window.LandRegistryMap || !window.L) {
                setStatus(container, 'The cadastral map could not be loaded. Reload the page to try again.', false);
                return;
            }

            try {
                var labels = {};
                try {
                    labels = JSON.parse(container.dataset.labels || '{}');
                } catch (_error) {
                    labels = {};
                }
                var center = [Number(container.dataset.centerLat), Number(container.dataset.centerLng)];
                var zoom = Number(container.dataset.zoom);
                var instance = window.LandRegistryMap.create({
                    container: container,
                    apiBase: container.dataset.apiBase,
                    center: center,
                    zoom: Number.isFinite(zoom) ? zoom : 6,
                    labels: labels
                });
                container.dataset.mapInitialized = 'true';

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
                setStatus(container, '', true);
            } catch (error) {
                setStatus(container, 'The cadastral map could not be initialized.', false);
                if (window.console) console.error('[LandRegistryMap] initialization failed', error);
            }
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initializeMaps, { once: true });
    } else {
        initializeMaps();
    }
})();
