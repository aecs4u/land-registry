"""OMI quotes are read from the normalised omi tables by ISTAT municipality key."""

from __future__ import annotations

import asyncio

import pytest

from land_registry import stats_service as svc

LEGACY = ("omi_ref_zone", "omi_fact_quotazioni", "cod_comune_istat", "cod_comune_amm", "cod_comune_catastale")


@pytest.mark.parametrize(
    "sql",
    [svc._OMI_ZONE_HISTORY_SQL, svc._OMI_LATEST_PERIOD_SQL, svc._OMI_LATEST_QUOTES_SQL],
)
def test_queries_use_the_normalised_tables_and_the_istat_key(sql):
    assert "zm.istat_code = %s" in sql
    assert "omi.zone_municipality" in sql
    assert "omi.zone_version" in sql
    for legacy in LEGACY:
        assert legacy not in sql


def test_quote_queries_return_the_float_forms_the_api_has_always_returned():
    for sql in (svc._OMI_ZONE_HISTORY_SQL, svc._OMI_LATEST_QUOTES_SQL):
        assert "f.purchase_min::float8 AS prezzo_min" in sql
        assert "f.rental_max::float8 AS locazione_max" in sql


class _Source(svc._AsyncPostgresSource):
    def __init__(self, rows):
        super().__init__("postgresql://u@localhost/aecs4u-stats")
        self.rows = rows
        self.calls = []

    async def _fetchrow_in(self, database, sql, params=()):
        self.calls.append((database, params))
        return self.rows


def _resolve(source, value):
    return asyncio.run(source._omi_istat_code(value))


def test_numeric_input_is_already_an_istat_key():
    source = _Source(None)
    assert _resolve(source, "14047") == "014047"
    assert _resolve(source, "058091") == "058091"
    assert _resolve(source, "1234567") is None
    assert source.calls == []


def test_belfiore_code_is_resolved_through_the_istat_database():
    source = _Source({"code": "058091"})
    assert _resolve(source, "h501") == "058091"
    assert source.calls == [("istat", ("H501",))]


def test_unknown_or_blank_codes_resolve_to_nothing():
    assert _resolve(_Source(None), "Z999") is None
    assert _resolve(_Source({"code": "058091"}), "  ") is None
