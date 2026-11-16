"""The /map hierarchy finder reads the regional tables of the cadastral database."""

import asyncio

from fastapi.testclient import TestClient

from land_registry.main import app
from land_registry.map_layers import PostgresMapLayerSource
from land_registry.routers import api


class _Connection:
    def __init__(self, calls):
        self.calls = calls

    async def fetch(self, sql, *params):
        self.calls.append((" ".join(sql.split()), params))
        if "information_schema.tables" in sql:
            return [{"table_name": "lazio__fogli"}, {"table_name": "bad-name__fogli"}]
        if "sheet_number" in sql and "parcel_number" not in sql:
            return [{"sheet": sheet} for sheet in ("10", "2", "122A", "122", "1_X")]
        if "parcel_number" in sql:
            return [{"id": 7, "reference": "A062_000100.10", "municipality_name": "AFFILE", "sheet": "1"}]
        return []

    async def fetchval(self, sql, *params):
        self.calls.append((sql, params))
        return "LAZIO"


class _Source:
    def __init__(self):
        self.calls = []

    def connection(self):
        calls = self.calls

        class _Context:
            async def __aenter__(self):
                return _Connection(calls)

            async def __aexit__(self, *exc):
                return False

        return _Context()


def _map_source():
    cadastral = _Source()
    return PostgresMapLayerSource(connection_source=_Source(), cadastral_connection_source=cadastral), cadastral


def test_regions_come_from_the_regional_tables_and_unknown_regions_are_refused():
    source, cadastral = _map_source()
    assert asyncio.run(source.cadastral_regions()) == [{"id": "lazio", "name": "LAZIO"}]
    # A region id is only ever turned into a table name through that allow-list.
    assert asyncio.run(source.cadastral_provinces('lazio"; drop table x; --')) is None
    assert asyncio.run(source.cadastral_provinces("veneto")) is None
    assert not any("veneto" in sql or "drop" in sql for sql, _ in cadastral.calls)


def test_sheets_are_listed_in_natural_order():
    source, _ = _map_source()
    assert asyncio.run(source.cadastral_sheets("lazio", "h501")) == ["1_X", "2", "10", "122", "122A"]


def test_parcel_lookup_uses_the_regional_lookup_index_expression():
    source, cadastral = _map_source()
    rows = asyncio.run(source.find_cadastral_parcels("lazio", "a062", "1", "10"))
    assert rows == [{"id": 7, "reference": "A062_000100.10", "municipality_name": "AFFILE", "sheet": "1"}]
    sql, params = cadastral.calls[-1]
    assert 'public."lazio__particelle"' in sql and 'public."lazio__fogli"' in sql
    assert "lower(btrim(p.municipality_name)) = m.name" in sql and "upper(btrim(p.parcel_number)) = $3" in sql
    assert params == ("A062", "1", "10", 20)


def test_hierarchy_endpoints(monkeypatch):
    source, _ = _map_source()
    monkeypatch.setattr(api, "get_map_search_source", lambda: source)
    client = TestClient(app)
    assert client.get("/api/v1/map/cadastral/regions").json() == {"regions": [{"id": "lazio", "name": "LAZIO"}]}
    assert client.get("/api/v1/map/cadastral/provinces", params={"region": "veneto"}).status_code == 404
    assert client.get("/api/v1/map/cadastral/provinces", params={"region": 'x";--'}).status_code == 422
    found = client.get(
        "/api/v1/map/cadastral/parcels",
        params={"region": "lazio", "municipality": "A062", "sheet": "1", "parcel": "10"},
    )
    assert found.status_code == 200
    assert found.json()["results"] == [{
        "kind": "parcel", "id": 7, "label": "A062_000100.10", "reference": "A062_000100.10",
        "municipality_name": "AFFILE", "sheet": "1",
    }]
    source.cadastral_connection_source = None
    assert client.get("/api/v1/map/cadastral/regions").status_code == 503
