"""Regression tests for optional PostgreSQL failures in parcel enrichment."""

from contextlib import contextmanager
import time

from land_registry import stats_service


def test_failed_postgres_context_lookup_opens_circuit_breaker(monkeypatch) -> None:
    source = stats_service._PostgresStatsSource("postgresql://localhost/stats")

    @contextmanager
    def unavailable_connection():
        raise RuntimeError("database unavailable")
        yield  # pragma: no cover - keeps this a context manager

    monkeypatch.setattr(source, "_connection", unavailable_connection)
    started = time.monotonic()

    result = source.context_for_parcel(
        "A050_001600.143",
        "A050",
        {"lat": 41.8556, "lng": 14.7703},
    )

    assert result is None
    assert source._retry_at > started
    assert source.available() is False
