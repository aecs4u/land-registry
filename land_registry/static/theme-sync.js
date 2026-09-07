// Minimal dark-mode sync for pages that don't load map.js (map.js already
// has its own toggleDarkMode()/enableDarkMode()/disableDarkMode() — this
// exists only so /landing and /cadastral-data can read and change the same
// persisted preference without pulling in map.js's much larger surface
// area). Same localStorage key and body/attribute markers as map.js, so a
// choice made on one page is honored on the others instead of always
// resetting to light mode.
(function () {
    var DARK_MODE_KEY = 'cadastre_dark_mode';

    function apply(isDark) {
        document.body.classList.toggle('dark-mode', isDark);
        document.documentElement.setAttribute('data-bs-theme', isDark ? 'dark' : 'light');
        var icon = document.getElementById('themeIcon');
        if (icon) {
            icon.classList.toggle('fa-moon', !isDark);
            icon.classList.toggle('fa-sun', isDark);
        }
    }

    apply(localStorage.getItem(DARK_MODE_KEY) === 'true');

    window.toggleDarkMode = function () {
        var isDark = !document.body.classList.contains('dark-mode');
        localStorage.setItem(DARK_MODE_KEY, isDark ? 'true' : 'false');
        apply(isDark);
    };
})();
