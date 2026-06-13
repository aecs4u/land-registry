/**
 * index-init.js — page-level initialisation for the /map view.
 *
 * Depends on (loaded before this file in base.html):
 *   map.js, table-manager.js, i18n.js, theme.js
 * Loaded alongside: folium-interface.js
 *
 * Data injected by the server before DOMContentLoaded:
 *   window.geoJsonData  — parsed GeoJSON or null
 *   window.hasData      — boolean
 */

// ── Auth UI ──────────────────────────────────────────────────────────────

function updateSidebarAuth() {
    const navUserMenu = document.getElementById('navUserMenu');
    const navSignInBtn = document.getElementById('navSignInBtn');
    if (window.Clerk && window.Clerk.user) {
        const user = window.Clerk.user;
        document.getElementById('navUserName').textContent =
            user.firstName || user.primaryEmailAddress?.emailAddress?.split('@')[0] || 'User';
        document.getElementById('navUserEmail').textContent =
            user.primaryEmailAddress?.emailAddress || '';
        if (navUserMenu) navUserMenu.style.display = 'flex';
        if (navSignInBtn) navSignInBtn.style.display = 'none';
    } else if (window.Clerk) {
        if (navUserMenu) navUserMenu.style.display = 'none';
        if (navSignInBtn) navSignInBtn.style.display = 'inline-block';
    }
}

async function handleSidebarLogout() {
    if (window.Clerk) {
        await window.Clerk.signOut();
        await fetch('/auth/clerk/logout', { method: 'POST' });
        window.location.reload();
    }
}

// Wait for Clerk SDK then wire up auth state
window.addEventListener('load', async function () {
    let attempts = 0;
    while (!window.Clerk && attempts < 50) {
        await new Promise(r => setTimeout(r, 100));
        attempts++;
    }
    if (window.Clerk) {
        await window.Clerk.load();
        updateSidebarAuth();
        window.Clerk.addListener(updateSidebarAuth);
    } else {
        const sidebarAuth = document.getElementById('sidebar-auth-status');
        if (sidebarAuth) {
            sidebarAuth.innerHTML = '<div class="loading-text">Auth unavailable</div>';
        }
    }
});

// ── Toolbar height CSS variable ───────────────────────────────────────────

function _updateToolbarHeight() {
    const tb = document.getElementById('mapToolbar');
    if (tb) {
        document.querySelector('.map-container')
            ?.style.setProperty('--toolbar-h', tb.offsetHeight + 'px');
    }
}
document.addEventListener('DOMContentLoaded', _updateToolbarHeight);
window.addEventListener('resize', _updateToolbarHeight);

// ── Map toolbar panels ────────────────────────────────────────────────────

function toggleToolPanel(panelId, btnId) {
    const panel = document.getElementById(panelId);
    const btn = document.getElementById(btnId);
    const isOpen = panel.style.display === 'block';
    document.querySelectorAll('.tool-panel').forEach(p => p.style.display = 'none');
    document.querySelectorAll('.map-tool-btn').forEach(b => b.classList.remove('active'));
    if (!isOpen) {
        panel.style.display = 'block';
        btn.classList.add('active');
        if (panelId === 'panelExplore' && window.refreshSearchComuneList) {
            window.refreshSearchComuneList();
        }
    }
}

function closeToolPanel(panelId) {
    const panel = document.getElementById(panelId);
    if (panel) panel.style.display = 'none';
    document.querySelectorAll('.map-tool-btn').forEach(b => b.classList.remove('active'));
}

// ── Language selector ─────────────────────────────────────────────────────

function changeLang(lang) {
    document.cookie = 'lang=' + lang + '; path=/; max-age=31536000; SameSite=Lax';
    window.location.reload();
}

// ── User menu dropdown ────────────────────────────────────────────────────

function toggleUserDropdown() {
    const dd = document.getElementById('navUserDropdown');
    if (dd) dd.style.display = dd.style.display === 'block' ? 'none' : 'block';
}
document.addEventListener('click', function (e) {
    if (!e.target.closest('#navUserBtn')) {
        const dd = document.getElementById('navUserDropdown');
        if (dd) dd.style.display = 'none';
    }
});

// ── View switching ────────────────────────────────────────────────────────

function handleTableViewClick() {
    document.querySelectorAll('.view-content').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.view-toggle button').forEach(el => el.classList.remove('active'));
    document.getElementById('tableView').classList.add('active');
    document.getElementById('tableViewBtn').classList.add('active');
    const mapInfo = document.getElementById('tableInfo');
    const tableViewInfo = document.getElementById('tableViewInfo');
    if (mapInfo && tableViewInfo) {
        tableViewInfo.textContent = mapInfo.textContent;
    }
    if (typeof loadTableData === 'function') {
        loadTableData('table', 1);
    }
}

// ── Page init (auto-zoom, zone manager) ──────────────────────────────────

document.addEventListener('DOMContentLoaded', function () {
    setTimeout(function () {
        if (typeof updatePolygonManagementState === 'function') {
            updatePolygonManagementState();
        }
    }, 500);

    const hasNewLayerBounds = sessionStorage.getItem('newLayerBounds');
    const hasNewlyLoadedLayers = sessionStorage.getItem('newlyLoadedLayers');
    if (hasNewLayerBounds || hasNewlyLoadedLayers) {
        // Newly loaded layers — auto-zoom handled by folium-interface.js
    } else if (window.geoJsonData?.features?.length > 0) {
        setTimeout(function () {
            if (typeof autoZoomToAllPolygons === 'function') autoZoomToAllPolygons();
        }, 1000);
    } else {
        setTimeout(function () {
            if (typeof autoZoomToAllPolygons === 'function') autoZoomToAllPolygons();
        }, 1500);
    }

    // Auto-load saved zones for authenticated users
    setTimeout(function () {
        if (typeof initZoneManager === 'function') initZoneManager();
        setTimeout(function () {
            if (window.Clerk?.user && typeof loadAllZones === 'function') loadAllZones();
        }, 1500);
    }, 3000);
});
