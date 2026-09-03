"""Deterministic parcel identity and dataset-version helpers.

The current application still reads several legacy and upstream stores.  These
helpers provide a stable key for new durable records without changing the
meaning of existing ``feature_id`` values or requiring a database migration.
"""

from __future__ import annotations

import unicodedata
from uuid import UUID, uuid5


# Deliberately fixed: changing this namespace would change every generated ID.
PARCEL_ID_NAMESPACE = UUID("6f9386b0-7c9f-4f11-b2f6-08dce4b2e7d7")


def _canonical_component(value: str) -> str:
    """Canonicalize identifiers while retaining significant zeroes."""

    normalized = unicodedata.normalize("NFKC", value).strip().upper()
    return " ".join(normalized.split())


def build_source_key(
    source: str,
    national_reference: str | None = None,
    *,
    municipality_code: str | None = None,
    section: str | None = None,
    sheet: str | None = None,
    parcel: str | None = None,
) -> str:
    """Build a source-qualified key without using a snapshot-local row ID.

    ``national_reference`` is preferred when supplied.  The component fallback
    supports legacy records that only have cadastral municipality/section/
    sheet/parcel fields.  The original source reference should still be stored
    separately for display and audit purposes.
    """

    source_value = _canonical_component(source)
    if not source_value:
        raise ValueError("source must not be empty")

    if national_reference is not None and _canonical_component(national_reference):
        identifier = f"REF={_canonical_component(national_reference)}"
    else:
        components = {
            "COMUNE": municipality_code,
            "SECTION": section,
            "SHEET": sheet,
            "PARCEL": parcel,
        }
        present = [f"{name}={_canonical_component(value)}" for name, value in components.items() if value]
        if not present:
            raise ValueError("a national_reference or cadastral components are required")
        identifier = "|".join(present)

    return f"{source_value}|{identifier}"


def canonical_source_key(source: str, source_key: str) -> str:
    """Normalize a client-supplied source key and enforce its source prefix.

    Unqualified keys are interpreted as source-native references. Qualified
    keys must use the same source name supplied alongside them; silently
    changing the prefix could associate a parcel with the wrong authority.
    """

    source_value = _canonical_component(source)
    key_value = _canonical_component(source_key)
    if not source_value or not key_value:
        raise ValueError("source and source_key must not be empty")

    if "|" not in key_value:
        return build_source_key(source_value, key_value)

    key_source, key_suffix = key_value.split("|", 1)
    if key_source != source_value:
        raise ValueError("source_key has a different source prefix")
    if not key_suffix:
        raise ValueError("source_key must contain an identifier")
    return f"{source_value}|{key_suffix}"


def parcel_identity_id(source_key: str) -> UUID:
    """Return the stable identity UUID for a canonical source key."""

    key = _canonical_component(source_key)
    if not key:
        raise ValueError("source_key must not be empty")
    return uuid5(PARCEL_ID_NAMESPACE, key)


def parcel_version_id(identity_id: UUID, dataset_version: str) -> UUID:
    """Return a stable observation ID for one identity in one dataset version."""

    version = _canonical_component(dataset_version)
    if not version:
        raise ValueError("dataset_version must not be empty")
    return uuid5(identity_id, version)
