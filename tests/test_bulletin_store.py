"""Database-only request path and atomic, complete DPC snapshots."""

from datetime import UTC, date, datetime, timezone
from unittest.mock import MagicMock

import httpx
import psycopg2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from land_registry import bulletin_store as store
from land_registry.routers.enrichment import enrichment_router


def topology(label):
    return {"type": "Topology", "arcs": [], "objects": {
        "zones": {"type": "GeometryCollection", "geometries": [
            {"type": "Polygon", "arcs": [], "properties": {"label": label}},
        ]},
    }}


def snapshot():
    return {"stamp": "20261008_1521", "name": "Bollettino",
            "today": {}, "tomorrow": {"topo_json": "tomorrow-url"},
            "today_zones": topology("today"), "tomorrow_zones": topology("tomorrow")}


@pytest.fixture
def connection(monkeypatch):
    for name in ("HAZARDS_POSTGRES_DSN", "AECS4U_STATS_HAZARDS_DATABASE_URL", "AECS4U_STATS_POSTGRES_DSN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("STATS_POSTGRES_DSN", "postgresql://localhost/aecs4u-stats")
    conn = MagicMock()
    monkeypatch.setattr(store, "_connect", lambda dsn: conn)
    return conn


def test_hazards_dsn_uses_source_database_and_normalizes_driver(monkeypatch, connection):
    monkeypatch.setenv("STATS_POSTGRES_DSN", "postgresql+asyncpg://reader@db/aecs4u-stats?sslmode=require")
    assert store.hazards_dsn() == "postgresql://reader@db/hazards?sslmode=require"
    monkeypatch.setenv("HAZARDS_POSTGRES_DSN", "postgresql://writer@db/application")
    with pytest.raises(ValueError, match="hazards"):
        store.hazards_dsn()


def test_no_application_database_fallback(monkeypatch, connection):
    monkeypatch.delenv("STATS_POSTGRES_DSN")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/app")
    with pytest.raises(ValueError, match="Set HAZARDS"):
        store.hazards_dsn()


def test_reads_snapshot_without_any_http(monkeypatch, connection):
    def forbidden(*args, **kwargs):
        raise AssertionError("HTTP on the read path")

    monkeypatch.setattr(httpx, "get", forbidden)
    monkeypatch.setattr(httpx, "Client", forbidden)
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (snapshot(), date(2026, 10, 8), datetime.now(UTC))
    result = store.get_bulletin(target_day=date(2026, 10, 8))
    assert result["today_zones"] == snapshot()["today_zones"]
    assert result["storage"] == "hazards PostgreSQL"
    assert "tomorrow_zones" not in result
    connection.set_session.assert_called_once_with(readonly=True, autocommit=True)
    connection.close.assert_called_once()
    assert "SELECT payload" in cursor.execute.call_args.args[0]


def test_missing_and_failed_store_do_not_download(monkeypatch, connection):
    monkeypatch.setattr(store, "download_bulletin", lambda: pytest.fail("read attempted download"))
    connection.cursor.return_value.__enter__.return_value.fetchone.return_value = None
    assert store.get_bulletin() is None
    connection.cursor.side_effect = psycopg2.OperationalError("offline")
    assert store.get_bulletin() is None


def test_rollover_and_expiration():
    payload = snapshot()
    fetched = datetime(2026, 10, 8, 15, tzinfo=UTC)
    tomorrow = store._for_day(payload, date(2026, 10, 8), fetched, date(2026, 10, 9))
    assert tomorrow["today_zones"] == payload["tomorrow_zones"]
    assert tomorrow["stale"] is False
    assert payload["today_zones"] == topology("today")
    expired = store._for_day(payload, date(2026, 10, 8), fetched, date(2026, 10, 10))
    assert expired["today_zones"] is None
    assert expired["stale"] is True


def test_incomplete_snapshot_cannot_replace_stored_data(connection):
    payload = snapshot()
    del payload["tomorrow_zones"]
    with pytest.raises(ValueError, match="TopoJSON"):
        store.save_bulletin(payload, initialize=True)
    connection.cursor.assert_not_called()


def test_complete_snapshot_uses_one_transaction_and_idempotent_upsert(connection):
    payload = snapshot()
    store.save_bulletin(payload, initialize=True)
    connection.__enter__.assert_called_once()
    cursor = connection.cursor.return_value.__enter__.return_value
    assert cursor.execute.call_args_list[0].args[0] == store.SCHEMA_SQL
    sql, params = cursor.execute.call_args_list[1].args
    assert "ON CONFLICT (repo, stamp)" in sql
    assert params[2] == date(2026, 10, 8)
    assert params[-1].adapted == payload
    connection.close.assert_called_once()


def test_endpoint_uses_local_adapter_and_returns_503_when_empty(monkeypatch):
    from land_registry import stats_service

    app = FastAPI()
    app.include_router(enrichment_router, prefix="/enrichment")
    with TestClient(app) as client:
        monkeypatch.setattr(stats_service, "get_criticality_bulletin", lambda: snapshot())
        assert client.get("/enrichment/bulletin").json()["stamp"] == "20261008_1521"
        monkeypatch.setattr(stats_service, "get_criticality_bulletin", lambda: None)
        assert client.get("/enrichment/bulletin").status_code == 503


@pytest.mark.parametrize("changed_file", [
    "files/20261008_1521.json", "files/topojson/20261008_1521_today.json",
])
def test_download_fetches_both_days_and_reuses_connection(monkeypatch, changed_file):
    base = f"https://raw.githubusercontent.com/{store.REPO}/master/files/"
    metadata = {"today": {"topo_json": f"{base}topojson/20261008_1521_today.json"},
                "tomorrow": {"topo_json": f"{base}topojson/20261008_1521_tomorrow.json"}}
    replies = [[{"sha": "abc"}], {"files": [{"filename": changed_file}]},
               metadata, topology("today"), topology("tomorrow")]
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json=replies.pop(0))

    client_type = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client_type(transport=httpx.MockTransport(handler)))
    result = store.download_bulletin()
    assert result["today_zones"] == topology("today")
    assert result["tomorrow_zones"] == topology("tomorrow")
    assert len(calls) == 5


def test_failed_tomorrow_download_preserves_previous_snapshot(monkeypatch):
    monkeypatch.setattr(store, "hazards_dsn", lambda: "postgresql://localhost/hazards")
    monkeypatch.setattr("sys.argv", ["bulletin_store"])

    def failed_download():
        raise httpx.ConnectError("tomorrow unavailable")

    monkeypatch.setattr(store, "download_bulletin", failed_download)
    monkeypatch.setattr(store, "save_bulletin", lambda *a, **kw: pytest.fail("saved incomplete download"))
    assert store.main() == 1
