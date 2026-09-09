/* Location search for the Folium-rendered Leaflet map.
 * Uses Nominatim's public geocoder and keeps all map interaction client-side.
 */
(function () {
    'use strict';

    const controls = document.querySelectorAll('[data-location-search]');
    if (!controls.length) return;

    let searchMarker = null;
    let requestId = 0;

    function getMap() {
        try {
            if (typeof getLeafletMapInstance === 'function') {
                const candidate = getLeafletMapInstance();
                if (candidate && typeof candidate.setView === 'function') return candidate;
            }
        } catch (error) {
            // The map helper can run before Leaflet/Folium has finished loading.
        }

        const mapElements = document.querySelectorAll('.leaflet-container');
        for (const element of mapElements) {
            const candidate = window[element.id];
            if (candidate && typeof candidate.setView === 'function') return candidate;
        }
        return null;
    }

    function setStatus(control, message, isError) {
        const status = control.querySelector('.location-search-status');
        status.textContent = message || '';
        status.classList.toggle('error', Boolean(isError));
    }

    function setLoading(control, loading) {
        control.classList.toggle('is-loading', loading);
        const submit = control.querySelector('.location-search-submit');
        submit.disabled = loading;
        submit.setAttribute('aria-busy', loading ? 'true' : 'false');
    }

    function clearResults(control) {
        const results = control.querySelector('.location-search-results');
        results.replaceChildren();
        results.hidden = true;
    }

    function resultBounds(result) {
        if (!Array.isArray(result.boundingbox) || result.boundingbox.length !== 4) return null;
        const [south, north, west, east] = result.boundingbox.map(Number);
        if ([south, north, west, east].some(Number.isNaN)) return null;
        return [[south, west], [north, east]];
    }

    function centerOnResult(control, result, keepResults) {
        const map = getMap();
        if (!map) {
            setStatus(control, 'The map is still loading. Please try again.', true);
            return;
        }

        const lat = Number(result.lat);
        const lon = Number(result.lon);
        if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;

        const bounds = resultBounds(result);
        if (bounds) {
            map.fitBounds(bounds, { padding: [50, 50], maxZoom: 17 });
        } else {
            map.setView([lat, lon], Math.max(map.getZoom(), 15));
        }

        if (searchMarker) map.removeLayer(searchMarker);
        searchMarker = L.marker([lat, lon], { title: result.display_name }).addTo(map);
        const popupContent = document.createElement('span');
        popupContent.textContent = result.display_name;
        searchMarker.bindPopup(popupContent).openPopup();
        if (!keepResults) clearResults(control);
        setStatus(control, '', false);
    }

    function showResults(control, results) {
        const resultList = control.querySelector('.location-search-results');
        resultList.replaceChildren();

        results.forEach((result) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'location-search-result';
            button.setAttribute('role', 'option');
            button.textContent = result.display_name;
            button.addEventListener('click', () => centerOnResult(control, result));
            resultList.appendChild(button);
        });

        resultList.hidden = results.length === 0;
    }

    async function search(control) {
        const input = control.querySelector('.location-search-input');
        const query = input.value.trim();
        if (!query) {
            clearResults(control);
            setStatus(control, 'Enter a place, address, or postcode.', true);
            input.focus();
            return;
        }

        const currentRequest = ++requestId;
        setLoading(control, true);
        clearResults(control);
        setStatus(control, 'Searching…', false);

        try {
            const params = new URLSearchParams({
                q: query,
                format: 'jsonv2',
                addressdetails: '1',
                limit: '5'
            });
            const response = await fetch(
                `https://nominatim.openstreetmap.org/search?${params.toString()}`,
                {
                    headers: {
                        'Accept': 'application/json',
                        'Accept-Language': document.documentElement.lang || 'it'
                    }
                }
            );
            if (!response.ok) throw new Error(`Geocoder returned HTTP ${response.status}`);
            const results = await response.json();
            if (currentRequest !== requestId) return;

            if (!Array.isArray(results) || results.length === 0) {
                setStatus(control, 'No locations found.', true);
                return;
            }

            centerOnResult(control, results[0], true);
            showResults(control, results);
        } catch (error) {
            if (currentRequest !== requestId) return;
            console.warn('[LocationSearch] Geocoding failed:', error);
            setStatus(control, 'Unable to search right now. Please try again.', true);
        } finally {
            if (currentRequest === requestId) setLoading(control, false);
        }
    }

    controls.forEach((control) => {
        const form = control.querySelector('.location-search-form');
        const input = control.querySelector('.location-search-input');
        form.addEventListener('submit', (event) => {
            event.preventDefault();
            search(control);
        });
        input.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') clearResults(control);
        });
    });

    document.addEventListener('click', (event) => {
        controls.forEach((control) => {
            if (!control.contains(event.target)) clearResults(control);
        });
    });
})();
