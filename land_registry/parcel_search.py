"""Pure helpers for direct cadastral parcel search."""

from __future__ import annotations

import re


_COMPONENT_SEARCH = re.compile(
    r"([A-Z]\d{3})[\s_/-]+(\d+)[\s./-]+([A-Z0-9]+(?:[_/-][A-Z0-9]+)*)"
)


def parse_cadastral_components(query: str) -> tuple[str, str, str] | None:
    """Parse ``comune foglio particella`` without treating place names as SQL filters.

    Parcel labels may contain letters or internal separators (for example
    ``STRADA001`` or ``42/1``), so the final component is an identifier rather
    than a number-only field.
    """
    normalized = str(query or "").strip().upper()
    match = _COMPONENT_SEARCH.fullmatch(normalized)
    return match.groups() if match else None
