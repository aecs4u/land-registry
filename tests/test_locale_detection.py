"""The catalogue served to the map follows the same signals as the shared theme middleware."""

import pytest
from starlette.requests import Request

from land_registry.i18n import detect_locale


def _request(query: str = "", cookie: str = "", accept: str = "") -> Request:
    headers = []
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    if accept:
        headers.append((b"accept-language", accept.encode()))
    return Request({"type": "http", "method": "GET", "path": "/map", "query_string": query.encode(), "headers": headers})


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"accept": "en-GB,en;q=0.9"}, "en"),
        ({}, "it"),
        # The header picker sets the theme's ``language`` cookie.
        ({"cookie": "language=it", "accept": "en-GB,en;q=0.9"}, "it"),
        ({"cookie": "language=en", "accept": "it-IT"}, "en"),
        # Both cookies present: the theme's picker, visible on every page, leads.
        ({"cookie": "lang=en; language=it"}, "it"),
        ({"cookie": "lang=it"}, "it"),
        # An explicit ?lang= wins over everything.
        ({"query": "lang=it", "cookie": "lang=en", "accept": "en"}, "it"),
        # Unsupported values fall through.
        ({"query": "lang=de", "cookie": "language=fr", "accept": "en-US"}, "en"),
    ],
)
def test_detect_locale_priority(kwargs, expected):
    assert detect_locale(_request(**kwargs)) == expected
