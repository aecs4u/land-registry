"""Regression tests for the asyncpg request-path adapters."""

import pytest

from land_registry import stats_service


def test_asyncpg_placeholder_translation():
    assert stats_service._asyncpg_sql("SELECT %s, %s::jsonb") == "SELECT $1, $2::jsonb"


@pytest.mark.asyncio
async def test_async_municipality_lookup_does_not_use_legacy_adapter(monkeypatch):
    class FakeSource:
        _retry_at = 0.0

        async def municipality_by_cadastral_code(self, code):
            return {"cadastral_code": code, "name": "Async municipality"}

    async def source_factory(*, poi=False):
        assert poi is False
        return FakeSource()

    monkeypatch.setattr(stats_service, "_get_async_postgres_source", source_factory)
    monkeypatch.setattr(
        stats_service,
        "get_municipality_by_cadastral_code",
        lambda *args, **kwargs: pytest.fail("legacy synchronous lookup was used"),
    )

    result = await stats_service.aget_municipality_by_cadastral_code("H501")

    assert result == {"cadastral_code": "H501", "name": "Async municipality"}
