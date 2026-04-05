"""
Internationalisation support for Land Registry.

Locales supported: it (Italian, default), en (English).
Language detection order: cookie → Accept-Language header → default.

Usage in templates:  {{ _('string') }}
Usage in Python:     from land_registry.i18n import gettext as _
"""
from __future__ import annotations

import contextvars
import gettext as _gettext
import logging
from pathlib import Path
from typing import Callable

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

SUPPORTED_LOCALES: list[str] = ["it", "en"]
DEFAULT_LOCALE = "it"
LOCALE_COOKIE = "lang"
TRANSLATIONS_DIR = Path(__file__).parent / "translations"
DOMAIN = "land_registry"

# ContextVar so the current locale is available anywhere in the request scope
# (including Jinja2 globals that can't receive the request object directly).
_current_locale: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_current_locale", default=DEFAULT_LOCALE
)


def _load_translation(locale: str) -> _gettext.GNUTranslations | _gettext.NullTranslations:
    """Load compiled .mo translation for *locale*, falling back to NullTranslations."""
    try:
        return _gettext.translation(
            DOMAIN,
            localedir=str(TRANSLATIONS_DIR),
            languages=[locale],
        )
    except FileNotFoundError:
        return _gettext.NullTranslations()


# Pre-load all supported translations at startup
_TRANSLATIONS: dict[str, _gettext.NullTranslations] = {
    locale: _load_translation(locale) for locale in SUPPORTED_LOCALES
}


def get_translation(locale: str) -> _gettext.NullTranslations:
    return _TRANSLATIONS.get(locale, _TRANSLATIONS.get(DEFAULT_LOCALE, _gettext.NullTranslations()))


def detect_locale(request: Request) -> str:
    """Return the best matching locale for this request."""
    # 1. Cookie
    cookie = request.cookies.get(LOCALE_COOKIE, "")
    if cookie in SUPPORTED_LOCALES:
        return cookie

    # 2. Accept-Language header
    accept = request.headers.get("accept-language", "")
    for part in accept.split(","):
        lang = part.strip().split(";")[0].split("-")[0].lower()
        if lang in SUPPORTED_LOCALES:
            return lang

    return DEFAULT_LOCALE


def make_gettext(locale: str) -> Callable[[str], str]:
    """Return a gettext callable bound to *locale*."""
    return get_translation(locale).gettext


def contextvar_gettext(message: str) -> str:
    """
    Request-scoped gettext that reads the locale from the ContextVar set by
    LocaleMiddleware.  Safe to use as a Jinja2 environment global because it
    resolves the correct locale at render time without needing the request object.
    """
    locale = _current_locale.get()
    return get_translation(locale).gettext(message)


class LocaleMiddleware(BaseHTTPMiddleware):
    """
    Detect locale from cookie / Accept-Language and:
    - attach it to request.state.locale / request.state.gettext
    - set the _current_locale ContextVar so Jinja2 globals work automatically
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        locale = detect_locale(request)
        token = _current_locale.set(locale)
        try:
            request.state.locale = locale
            request.state.gettext = make_gettext(locale)
            response = await call_next(request)
        finally:
            _current_locale.reset(token)
        return response


def gettext(message: str) -> str:
    """Module-level gettext for Python code (uses DEFAULT_LOCALE; prefer request-scoped version)."""
    return get_translation(DEFAULT_LOCALE).gettext(message)
