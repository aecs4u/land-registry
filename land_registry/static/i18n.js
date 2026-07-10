/**
 * Frontend i18n helper.
 * window._i18n is injected by the server as a JSON object: { key: translated_string, ... }
 */
window.t = function(key) {
  if (window._i18n && window._i18n[key] !== undefined) {
    return window._i18n[key];
  }
  return key;
};
