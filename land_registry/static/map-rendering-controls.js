/* Settings shared by the legacy Folium map's cadastral renderers. */
(function (global) {
    'use strict';

    const STORAGE_KEY = 'land-registry-map-rendering-v1';
    const DEFAULTS = Object.freeze({ colorScheme: 'default', rendererMode: 'auto', lodEnabled: false });
    const SCHEMES = new Set(['default', 'foglio', 'density', 'propertyType', 'temporal']);
    const RENDERERS = new Set(['auto', 'webgl', 'svg']);
    let settings = { ...DEFAULTS };

    try {
        const stored = JSON.parse(global.localStorage.getItem(STORAGE_KEY) || '{}');
        if (SCHEMES.has(stored.colorScheme)) {
            settings.colorScheme = stored.colorScheme;
        }
        if (RENDERERS.has(stored.rendererMode)) settings.rendererMode = stored.rendererMode;
        if (typeof stored.lodEnabled === 'boolean') settings.lodEnabled = stored.lodEnabled;
    } catch (_) {
        // Keep defaults when storage is unavailable or contains invalid JSON.
    }

    const api = {
        getSettings() { return { ...settings }; },
        setSettings(next) {
            if (SCHEMES.has(next?.colorScheme)) settings.colorScheme = next.colorScheme;
            if (RENDERERS.has(next?.rendererMode)) settings.rendererMode = next.rendererMode;
            if (typeof next?.lodEnabled === 'boolean') settings.lodEnabled = next.lodEnabled;
            try { global.localStorage.setItem(STORAGE_KEY, JSON.stringify(settings)); } catch (_) {}
            global.dispatchEvent(new CustomEvent('map-rendering-settings-change', { detail: api.getSettings() }));
        },
    };
    global.MapRenderingControls = api;

    function init() {
        const schemeSelect = document.getElementById('mapColorScheme');
        const rendererSelect = document.getElementById('mapRendererMode');
        const lodCheckbox = document.getElementById('mapLODEnabled');
        const webglNotice = document.getElementById('mapWebGLUnavailable');
        if (schemeSelect) {
            schemeSelect.value = settings.colorScheme;
            schemeSelect.addEventListener('change', () => api.setSettings({ colorScheme: schemeSelect.value }));
        }
        if (rendererSelect) {
            rendererSelect.value = settings.rendererMode;
            rendererSelect.addEventListener('change', () => api.setSettings({ rendererMode: rendererSelect.value }));
        }
        if (lodCheckbox) {
            lodCheckbox.checked = settings.lodEnabled;
            lodCheckbox.addEventListener('change', () => api.setSettings({ lodEnabled: lodCheckbox.checked }));
        }
        const updateWebGLNotice = () => {
            const available = !!global.WebGLRenderer?.isWebGLSupported;
            if (webglNotice) webglNotice.hidden = available;
        };
        updateWebGLNotice();
        global.addEventListener('load', updateWebGLNotice, { once: true });
        global.addEventListener('map-rendering-settings-change', () => {
            updateWebGLNotice();
        });
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
    else init();
})(window);
