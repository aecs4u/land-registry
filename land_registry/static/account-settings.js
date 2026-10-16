/* Persist account settings through /api/v1/user/preferences.
 *
 * Every settings pane saves the whole preference document, so saving the map
 * pane never resets the language and vice versa.
 */
(function () {
  'use strict';

  const forms = document.querySelectorAll('.js-settings-form');
  const apiKeySettings = document.getElementById('apiKeySettings');
  if (!forms.length && !apiKeySettings) return;

  const LOCALE_COOKIE = 'lang';
  const messageSource = document.getElementById('settingsMessages');
  const messages = {
    saving: messageSource?.dataset.saving || 'Saving…',
    saved: messageSource?.dataset.saved || 'Saved.',
    reloading: messageSource?.dataset.reloading || 'Saved. Reloading…',
    error: messageSource?.dataset.error || 'Could not save.',
    signedOut: messageSource?.dataset.signedOut || 'Sign in again to save.',
  };
  const currentLocale = document.querySelector('[data-current-locale]')?.dataset.currentLocale || '';

  function collectPreferences() {
    const preferences = { default_layers: [] };
    document.querySelectorAll('[data-pref]').forEach((field) => {
      const key = field.dataset.pref;
      if (key === 'default_layers') {
        if (field.checked) preferences.default_layers.push(field.value);
      } else if (field.type === 'radio') {
        if (field.checked) preferences[key] = field.value;
      } else if (field.type === 'checkbox') {
        preferences[key] = field.checked;
      } else {
        preferences[key] = field.value;
      }
    });
    return preferences;
  }

  function setStatus(form, message, isError) {
    const status = form.querySelector('.settings-status');
    if (!status) return;
    status.textContent = message;
    status.classList.toggle('text-danger', Boolean(isError));
    status.classList.toggle('text-success', !isError && Boolean(message));
  }

  function errorMessage(payload) {
    const detail = payload && payload.detail;
    if (typeof detail === 'string') return detail;
    if (detail && typeof detail.message === 'string') return detail.message;
    return messages.error;
  }

  async function save(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector('button[type="submit"]');
    const preferences = collectPreferences();
    if (button) button.disabled = true;
    setStatus(form, messages.saving, false);
    try {
      const response = await fetch('/api/v1/user/preferences', {
        method: 'PUT',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify(preferences),
      });
      if (response.status === 401) {
        setStatus(form, messages.signedOut, true);
        return;
      }
      if (!response.ok) {
        setStatus(form, errorMessage(await response.json().catch(() => null)), true);
        return;
      }
      if (preferences.language && preferences.language !== currentLocale) {
        document.cookie = `${LOCALE_COOKIE}=${encodeURIComponent(preferences.language)}; path=/; max-age=31536000; SameSite=Lax`;
        // The shared theme and i18n middleware read this one.
        document.cookie = `language=${encodeURIComponent(preferences.language)}; path=/; max-age=31536000; SameSite=Lax`;
        setStatus(form, messages.reloading, false);
        window.location.reload();
        return;
      }
      setStatus(form, messages.saved, false);
    } catch (_) {
      setStatus(form, messages.error, true);
    } finally {
      if (button) button.disabled = false;
    }
  }

  forms.forEach((form) => form.addEventListener('submit', save));

  if (!apiKeySettings) return;

  const keyMessages = {
    loading: apiKeySettings.dataset.loading || 'Loading API keys…',
    loadError: apiKeySettings.dataset.loadError || 'Could not load API keys.',
    unavailable: apiKeySettings.dataset.unavailable || 'API key storage is unavailable.',
    createError: apiKeySettings.dataset.createError || 'Could not create the API key.',
    maxError: apiKeySettings.dataset.maxError || 'Maximum active API keys reached.',
    revokeError: apiKeySettings.dataset.revokeError || 'Could not revoke the API key.',
    revokeSuccess: apiKeySettings.dataset.revokeSuccess || 'API key revoked.',
    signedOut: apiKeySettings.dataset.signedOut || 'Sign in again.',
    created: apiKeySettings.dataset.created || 'Copy and store this key now.',
    copied: apiKeySettings.dataset.copied || 'Copied.',
    copyError: apiKeySettings.dataset.copyError || 'Copy failed.',
    noKeys: apiKeySettings.dataset.noKeys || 'No API keys yet.',
    createdLabel: apiKeySettings.dataset.createdLabel || 'Created',
    lastUsedLabel: apiKeySettings.dataset.lastUsedLabel || 'Last used',
    neverUsed: apiKeySettings.dataset.neverUsed || 'Never',
    activeLabel: apiKeySettings.dataset.activeLabel || 'Active',
    revokedLabel: apiKeySettings.dataset.revokedLabel || 'Revoked',
    revokeLabel: apiKeySettings.dataset.revokeLabel || 'Revoke',
    limitLabel: apiKeySettings.dataset.limitLabel || 'requests per hour',
    quota: Number(apiKeySettings.dataset.limit) || 10000,
  };
  const keyForm = document.getElementById('apiKeyCreateForm');
  const keyName = document.getElementById('apiKeyName');
  const keyCreateButton = document.getElementById('apiKeyCreateButton');
  const keyCreateStatus = document.getElementById('apiKeyCreateStatus');
  const keyListStatus = document.getElementById('apiKeyListStatus');
  const keyList = document.getElementById('apiKeyList');
  const createdSecret = document.getElementById('apiKeyCreatedSecret');
  const createdMessage = document.getElementById('apiKeyCreatedMessage');
  const secretValue = document.getElementById('apiKeySecretValue');
  const copyButton = document.getElementById('apiKeyCopyButton');
  const copyStatus = document.getElementById('apiKeyCopyStatus');

  function setTextStatus(element, message, isError) {
    if (!element) return;
    element.textContent = message || '';
    element.classList.toggle('text-danger', Boolean(isError));
    element.classList.toggle('text-success', !isError && Boolean(message));
  }

  async function readApiJson(response) {
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = payload && payload.detail;
      const error = new Error(typeof detail === 'string' ? detail : 'API key request failed');
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  function formattedDate(value) {
    if (!value) return keyMessages.neverUsed;
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return keyMessages.neverUsed;
    return new Intl.DateTimeFormat(document.documentElement.lang || undefined, {
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(date);
  }

  function renderApiKeys(keys) {
    keyList.replaceChildren();
    if (!Array.isArray(keys) || keys.length === 0) {
      keyListStatus.textContent = keyMessages.noKeys;
      return;
    }
    keyListStatus.textContent = '';
    keys.forEach((key) => {
      const item = document.createElement('div');
      item.className = 'list-group-item d-flex flex-wrap align-items-center justify-content-between gap-3';

      const details = document.createElement('div');
      details.className = 'me-auto';
      const name = document.createElement('strong');
      name.textContent = key.name || key.key_prefix || 'API key';
      const prefix = document.createElement('code');
      prefix.className = 'ms-2';
      prefix.textContent = key.key_prefix || '';
      const metadata = document.createElement('div');
      metadata.className = 'small text-muted mt-1';
      const scopes = Array.isArray(key.scopes) ? key.scopes.join(', ') : '';
      const requestLimit = Number(key.requests_per_hour) || keyMessages.quota;
      metadata.textContent = `${scopes} · ${requestLimit.toLocaleString()} ${keyMessages.limitLabel}`;
      const dates = document.createElement('div');
      dates.className = 'small text-muted';
      dates.textContent = `${keyMessages.createdLabel}: ${formattedDate(key.created_at)} · ${keyMessages.lastUsedLabel}: ${formattedDate(key.last_used_at)}`;
      details.append(name, prefix, metadata, dates);

      const actions = document.createElement('div');
      actions.className = 'd-flex align-items-center gap-2';
      const status = document.createElement('span');
      const active = String(key.status).toLowerCase() === 'active';
      status.className = `badge ${active ? 'text-bg-success' : 'text-bg-secondary'}`;
      status.textContent = active ? keyMessages.activeLabel : keyMessages.revokedLabel;
      actions.append(status);
      if (active) {
        const revoke = document.createElement('button');
        revoke.type = 'button';
        revoke.className = 'btn btn-sm btn-outline-danger';
        revoke.textContent = keyMessages.revokeLabel;
        revoke.dataset.keyId = key.id;
        revoke.addEventListener('click', () => revokeApiKey(key.id, revoke));
        actions.append(revoke);
      }
      item.append(details, actions);
      keyList.append(item);
    });
  }

  async function loadApiKeys() {
    setTextStatus(keyListStatus, keyMessages.loading, false);
    try {
      const response = await fetch('/api/v1/user/api-keys', {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
      });
      const payload = await readApiJson(response);
      renderApiKeys(payload.keys);
    } catch (error) {
      const message = error.status === 401
        ? keyMessages.signedOut
        : error.status === 503
          ? keyMessages.unavailable
          : keyMessages.loadError;
      setTextStatus(keyListStatus, message, true);
    }
  }

  async function revokeApiKey(id, button) {
    button.disabled = true;
    setTextStatus(keyListStatus, '', false);
    try {
      const response = await fetch(`/api/v1/user/api-keys/${encodeURIComponent(id)}`, {
        method: 'DELETE',
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
      });
      await readApiJson(response);
      setTextStatus(keyListStatus, keyMessages.revokeSuccess, false);
      await loadApiKeys();
    } catch (error) {
      button.disabled = false;
      const message = error.status === 401 ? keyMessages.signedOut : keyMessages.revokeError;
      setTextStatus(keyListStatus, message, true);
    }
  }

  keyForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const name = keyName.value.trim();
    if (!name) {
      keyName.focus();
      return;
    }
    keyCreateButton.disabled = true;
    setTextStatus(keyCreateStatus, messages.saving, false);
    try {
      const response = await fetch('/api/v1/user/api-keys', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ name }),
      });
      const payload = await readApiJson(response);
      if (!payload.key) throw new Error('The API did not return the new key secret');
      secretValue.textContent = payload.key;
      createdMessage.textContent = keyMessages.created;
      createdSecret.hidden = false;
      keyForm.reset();
      setTextStatus(keyCreateStatus, '', false);
      await loadApiKeys();
    } catch (error) {
      const message = error.status === 401
        ? keyMessages.signedOut
        : error.status === 503
          ? keyMessages.unavailable
          : error.status === 409
            ? keyMessages.maxError
          : (error.message && error.message !== 'API key request failed' ? error.message : keyMessages.createError);
      setTextStatus(keyCreateStatus, message, true);
    } finally {
      keyCreateButton.disabled = false;
    }
  });

  copyButton.addEventListener('click', async () => {
    const value = secretValue.textContent || '';
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(value);
      } else {
        const range = document.createRange();
        range.selectNodeContents(secretValue);
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        const copied = document.execCommand('copy');
        selection.removeAllRanges();
        if (!copied) throw new Error('clipboard unavailable');
      }
      setTextStatus(copyStatus, keyMessages.copied, false);
    } catch (_) {
      setTextStatus(copyStatus, keyMessages.copyError, true);
    }
  });

  loadApiKeys();
})();
