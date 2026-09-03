"""Typed provenance, freshness, and ingestion contract tests."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from land_registry.models import FreshnessMetadata, IngestionManifest


def test_ingestion_manifest_validates_checksum_counts_and_utc():
    manifest = IngestionManifest(
        source="agenzia-entrate",
        source_version="2025-01",
        acquired_at=datetime(2025, 1, 2, 12, 0, tzinfo=timezone.utc),
        checksum_sha256="a" * 64,
        content_type="application/geo+json",
        size_bytes=1024,
        feature_count=25,
        adapter_version="1.2.0",
        status="validated",
        dataset_version="catasto-2025-01",
    )

    assert manifest.acquired_at.tzinfo == timezone.utc
    assert manifest.feature_count == 25


def test_ingestion_manifest_rejects_naive_timestamp_and_invalid_checksum():
    with pytest.raises(ValidationError):
        IngestionManifest(
            source="test",
            acquired_at=datetime(2025, 1, 2),
            checksum_sha256="not-a-checksum",
            content_type="application/octet-stream",
            size_bytes=0,
            adapter_version="test",
            status="acquired",
        )


def test_freshness_metadata_normalizes_offsets_and_rejects_naive_values():
    freshness = FreshnessMetadata(
        loaded_at=datetime(2025, 1, 2, 13, 0, tzinfo=timezone.utc),
        published_at=datetime(2025, 1, 2, 14, 0, tzinfo=timezone.utc),
        age_seconds=10,
        freshness_sla_seconds=3600,
        stale=False,
    )
    assert freshness.loaded_at.tzinfo == timezone.utc

    with pytest.raises(ValidationError):
        FreshnessMetadata(loaded_at=datetime(2025, 1, 2, 13, 0))
