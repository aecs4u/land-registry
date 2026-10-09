/* Private orders: aecs4u-billing checkout, followed by provider fulfillment. */
(function () {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const tr = (value) => window._i18n?.[value] || value;
  const API = '/api/v1/cadastral-purchases';
  const EMPTY = { type: 'FeatureCollection', features: [] };
  let selection = null;
  let quote = null;
  let quoteKey = null;
  let quotedTarget = null;
  let items = [];
  let geo = EMPTY;
  let leafletLayer = null;
  let boundMap = null;
  let refreshing = false;
  let loaded = false;
  let busy = false;

  function notice(value, error = false) {
    $('purchaseNotice').textContent = tr(value);
    $('purchaseNotice').dataset.error = String(error);
  }

  async function api(path, options = {}) {
    const response = await fetch(API + path, {
      credentials: 'same-origin', cache: 'no-store', ...options,
      headers: { 'Content-Type': 'application/json', 'X-Purchase-Request': '1', ...options.headers },
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      if (response.status === 401 || response.status === 403) clearPrivateData();
      throw new Error(typeof data.detail === 'string' ? data.detail : tr('Unable to load purchases.'));
    }
    return data;
  }

  function clearPrivateData() {
    items = [];
    geo = EMPTY;
    quote = null;
    $('purchaseList').replaceChildren();
    $('purchaseRecord').textContent = '';
    $('purchaseResult').hidden = true;
    $('purchaseDocument').removeAttribute('href');
    renderMap();
  }

  function button(label, action) {
    const element = document.createElement('button');
    element.type = 'button';
    element.className = 'secondary-action';
    element.textContent = tr(label);
    element.addEventListener('click', action);
    return element;
  }

  function renderList(admin) {
    $('purchaseListTitle').textContent = tr(admin ? 'Customer purchases' : 'My purchases');
    $('purchaseList').replaceChildren();
    if (!items.length) {
      $('purchaseList').textContent = tr('No purchases yet. Select a parcel to request a query.');
      return;
    }
    const statuses = {
      quoting: 'Getting quote', quoted: 'Quote ready', submitting: 'Opening checkout',
      awaiting_payment: 'Awaiting payment', paid: 'Paid · waiting for provider',
      processing: 'Query in progress', available: 'Available', failed: 'Provider failed · contact support', cancelled: 'Cancelled',
    };
    items.forEach((item) => {
      const row = document.createElement('div');
      row.className = 'purchase-item';
      const title = document.createElement('strong');
      title.textContent = `${item.target.national_reference} · ${item.provider.toUpperCase()}`;
      const status = document.createElement('p');
      status.textContent = tr(statuses[item.status] || item.status);
      if (admin && item.customer_id) status.textContent += ` · ${item.customer_id}`;
      row.append(title, status);
      if (item.status === 'available') {
        row.append(button('View purchased record', () => openResult(item.id)));
        row.append(button('Show on map', () => showOnMap(item.id)));
      }
      // An admin can inspect all customers' records; checkout belongs to the customer.
      if (item.can_checkout && ['quoted', 'submitting', 'awaiting_payment'].includes(item.status)) {
        row.append(button('Continue purchase', () => continuePurchase(item)));
      }
      $('purchaseList').append(row);
    });
  }

  async function refresh() {
    if (!window.landRegistrySignedIn || refreshing || document.hidden) return;
    refreshing = true;
    try {
      const [data, collection] = await Promise.all([api(''), api('/map')]);
      const previous = new Map(items.map((item) => [item.id, item.status]));
      items = data.items;
      geo = collection;
      renderList(data.admin);
      renderMap();
      if (loaded && items.some((item) => item.status === 'available' && previous.get(item.id) !== 'available')) {
        notice('Your cadastral query is ready and available on the map.');
        const pill = $('mapStatus');
        if (pill) pill.textContent = tr('A cadastral purchase is ready. Open My purchases to view it.');
      }
      loaded = true;
    } catch (error) { notice(error.message, true); }
    finally { refreshing = false; }
  }

  function renderMap() {
    const maps = window.landRegistryPurchaseMap?.maps() || {};
    const map = maps.map;
    if (map && map.isStyleLoaded()) {
      if (!map.getSource('private-cadastral-purchases')) {
        map.addSource('private-cadastral-purchases', { type: 'geojson', data: geo });
        map.addLayer({ id: 'private-purchases-fill', type: 'fill', source: 'private-cadastral-purchases',
          filter: ['==', ['geometry-type'], 'Polygon'], paint: { 'fill-color': '#8b5cf6', 'fill-opacity': 0.28 } });
        map.addLayer({ id: 'private-purchases-line', type: 'line', source: 'private-cadastral-purchases',
          filter: ['==', ['geometry-type'], 'Polygon'], paint: { 'line-color': '#7c3aed', 'line-width': 3 } });
        map.addLayer({ id: 'private-purchases-point', type: 'circle', source: 'private-cadastral-purchases',
          filter: ['==', ['geometry-type'], 'Point'], paint: { 'circle-color': '#7c3aed', 'circle-radius': 7 } });
      } else map.getSource('private-cadastral-purchases').setData(geo);
    }
    if (maps.fallbackMap && window.L) {
      if (leafletLayer) maps.fallbackMap.removeLayer(leafletLayer);
      leafletLayer = L.geoJSON(geo, {
        style: { color: '#7c3aed', fillOpacity: 0.28 },
        onEachFeature: (feature, layer) => layer.on('click', () => openResult(feature.properties.purchase_id)),
      }).addTo(maps.fallbackMap);
    }
  }

  function bindMap() {
    const map = window.landRegistryPurchaseMap?.maps().map;
    if (map && map !== boundMap) {
      boundMap = map;
      map.on('style.load', renderMap);
      map.on('click', (event) => {
        const layers = ['private-purchases-fill', 'private-purchases-point'].filter((id) => map.getLayer(id));
        if (!layers.length) return;
        const feature = map.queryRenderedFeatures(event.point, { layers })[0];
        if (feature) void openResult(feature.properties.purchase_id);
      });
    }
    renderMap();
  }

  async function openResult(id) {
    $('cadastralPurchaseDialog').showModal();
    try {
      const data = await api(`/${encodeURIComponent(id)}`);
      $('purchaseRecord').textContent = data.record ? JSON.stringify(data.record, null, 2) : tr('Document available for download.');
      $('purchaseDocument').hidden = !data.document_url;
      if (data.document_url) $('purchaseDocument').href = data.document_url;
      else $('purchaseDocument').removeAttribute('href');
      $('purchaseResult').hidden = false;
    } catch (error) { notice(error.message, true); }
  }

  function showOnMap(id) {
    const feature = geo.features.find((entry) => entry.properties.purchase_id === id);
    if (!feature) return;
    const maps = window.landRegistryPurchaseMap.maps();
    const points = [];
    const visit = (coordinates) => {
      if (typeof coordinates[0] === 'number') points.push(coordinates);
      else coordinates.forEach(visit);
    };
    visit(feature.geometry.coordinates);
    const west = Math.min(...points.map((p) => p[0]));
    const east = Math.max(...points.map((p) => p[0]));
    const south = Math.min(...points.map((p) => p[1]));
    const north = Math.max(...points.map((p) => p[1]));
    if (maps.map) maps.map.fitBounds([[west, south], [east, north]], { padding: 80, maxZoom: 18 });
    else maps.fallbackMap?.fitBounds([[south, west], [north, east]], { maxZoom: 18 });
    $('cadastralPurchaseDialog').close();
  }

  function showQuote(item) {
    quote = item;
    const amount = new Intl.NumberFormat(document.documentElement.lang || 'en', { style: 'currency', currency: item.quote.currency }).format(item.quote.amount_cents / 100);
    const expiry = new Date(item.quote.expires_at).toLocaleString();
    $('purchaseQuoteSummary').textContent = `${item.target.national_reference} · ${item.provider.toUpperCase()} · ${amount} · ${tr('Quote expires')}: ${expiry}`;
    $('purchaseQuote').hidden = false;
    $('purchasePayButton').disabled = false;
  }

  function continuePurchase(item) {
    showQuote(item);
    notice('Review this quote, then continue to secure checkout.');
  }

  async function openPurchases(withSelection = false) {
    $('cadastralPurchaseDialog').showModal();
    if (!window.landRegistrySignedIn) {
      $('cadastralPurchaseForm').hidden = true;
      notice('Sign in to purchase cadastral queries.');
      const link = document.createElement('a');
      link.href = '/auth/login?next=%2Fmap';
      link.textContent = tr('Sign in');
      $('purchaseList').replaceChildren(link);
      return;
    }
    quote = null;
    quoteKey = null;
    quotedTarget = null;
    $('purchaseQuote').hidden = true;
    $('purchaseResult').hidden = true;
    selection = withSelection ? window.landRegistryPurchaseMap.selection() : null;
    $('cadastralPurchaseForm').hidden = !selection?.feature?.geometry || !selection?.reference;
    if (selection?.feature) {
      const props = selection.feature.properties || {};
      $('purchaseParcelReference').textContent = selection.reference;
      const code = props.municipality_code || props.cadastral_code || props.codice_catastale || props.municipality_id || '';
      $('purchaseMunicipality').value = /^[A-Za-z][0-9]{3}$/.test(String(code)) ? code : '';
      $('purchaseSheet').value = props.sheet || props.sheet_number || props.foglio || '';
      $('purchaseParcel').value = props.parcel || props.particella || props.LABEL || '';
      $('purchaseSubaltern').value = '';
    }
    notice(selection ? 'Check the cadastral identifiers before requesting a quote.' : 'Select a parcel to purchase a cadastral query.');
    try {
      const data = await api('/providers');
      $('purchaseProvider').replaceChildren();
      data.providers.forEach((provider) => {
        const option = document.createElement('option');
        option.value = provider.id;
        option.textContent = provider.name + (provider.enabled ? '' : ` · ${tr('Unavailable')}`);
        option.disabled = !provider.enabled;
        $('purchaseProvider').append(option);
      });
      const enabled = data.providers.find((provider) => provider.enabled);
      $('purchaseQuoteButton').disabled = !enabled;
      if (enabled) $('purchaseProvider').value = enabled.id;
      else notice('Purchases are not configured yet. Your existing orders remain accessible.');
      await refresh();
    } catch (error) { notice(error.message, true); }
  }

  async function getQuote(event) {
    event.preventDefault();
    if (busy || !selection?.reference) return;
    busy = true;
    $('purchaseQuoteButton').disabled = true;
    const target = {
      provider: $('purchaseProvider').value, registry: $('purchaseRegistry').value,
      municipality_code: $('purchaseMunicipality').value.toUpperCase(), sheet: $('purchaseSheet').value,
      parcel: $('purchaseParcel').value, subaltern: $('purchaseSubaltern').value || null,
      national_reference: selection.reference, geometry: selection.feature.geometry,
    };
    const encoded = JSON.stringify(target);
    if (quotedTarget !== encoded) { quoteKey = crypto.randomUUID(); quotedTarget = encoded; }
    try {
      notice('Getting quote…');
      const data = await api('/quote', { method: 'POST', body: encoded, headers: { 'Idempotency-Key': quoteKey } });
      if (data.status === 'quoted') showQuote(data);
      else if (data.quote) continuePurchase(data);
      quoteKey = null;
      quotedTarget = null;
      notice('Review this quote, then continue to secure checkout.');
      await refresh();
    } catch (error) { notice(error.message, true); }
    finally { busy = false; $('purchaseQuoteButton').disabled = false; }
  }

  async function pay() {
    if (!quote || busy) return;
    busy = true;
    $('purchasePayButton').disabled = true;
    try {
      notice('Opening secure checkout…');
      const data = await api(`/${encodeURIComponent(quote.id)}/checkout`, { method: 'POST' });
      if (data.checkout_url) window.location.assign(data.checkout_url);
      else { notice('Payment is being processed. Your result will appear after delivery.'); await refresh(); }
    } catch (error) { notice(error.message, true); }
    finally { busy = false; $('purchasePayButton').disabled = false; }
  }

  document.addEventListener('DOMContentLoaded', () => {
    if (!$('cadastralPurchaseDialog')) return;
    $('parcelPurchaseButton').addEventListener('click', () => openPurchases(true));
    $('purchasesOpenButton').addEventListener('click', () => openPurchases());
    $('purchaseCloseButton').addEventListener('click', () => $('cadastralPurchaseDialog').close());
    $('purchaseRefreshButton').addEventListener('click', refresh);
    $('cadastralPurchaseForm').addEventListener('submit', getQuote);
    $('cadastralPurchaseForm').addEventListener('input', () => { quote = null; $('purchaseQuote').hidden = true; });
    $('purchasePayButton').addEventListener('click', pay);
    window.addEventListener('cadastral-map-ready', () => { bindMap(); void refresh(); });
    document.addEventListener('visibilitychange', () => { if (!document.hidden) void refresh(); });
    window.setInterval(refresh, 15000);
    if (window.landRegistrySignedIn && new URLSearchParams(location.search).has('purchase')) void openPurchases();
    void refresh();
  });
})();
