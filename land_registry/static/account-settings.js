/* Persist account settings through /api/v1/user/preferences.
 *
 * Every settings pane saves the whole preference document, so saving the map
 * pane never resets the language and vice versa.
 */
(function () {
  'use strict';

  const forms = document.querySelectorAll('.js-settings-form');
  if (!forms.length) return;

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
})();
