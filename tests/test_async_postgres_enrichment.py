"""Regression tests for the asyncpg request-path adapters."""

import sqlite3
from contextlib import contextmanager
from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock, Mock

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


@pytest.mark.asyncio
@pytest.mark.parametrize("relation", ["facts.poi", "source_osm_pois.osm_pois"])
async def test_async_poi_lookup_uses_available_relation(monkeypatch, relation):
    source = stats_service._AsyncPostgresSource("postgresql://localhost/stats")
    probe = AsyncMock(return_value={"relation": relation})
    fetch = AsyncMock(return_value=[
        {"code": "schools", "name": "School", "st_y": 41.9, "st_x": 12.5, "distance_km": 0.1234},
    ])
    monkeypatch.setattr(source, "_fetchrow", probe)
    monkeypatch.setattr(source, "_fetch", fetch)
    monkeypatch.setattr(stats_service, "_get_async_postgres_source", AsyncMock(return_value=source))
    local = Mock(side_effect=AssertionError("local fallback was used"))
    monkeypatch.setattr(stats_service, "pois_within_radius", local)

    result = await stats_service.aget_pois_near(41.9, 12.5, radius_km=2, categories=["schools"])
    await stats_service.aget_pois_near(41.9, 12.5, radius_km=2, categories=["schools"])

    assert result["categories"] == {
        "schools": [{"lat": 41.9, "lng": 12.5, "name": "School", "distance_km": 0.123}],
    }
    assert result["total"] == 1
    assert result["source"] == "OpenStreetMap via aecs4u-stats (PostGIS asyncpg)"
    probe.assert_awaited_once()
    sql, params = fetch.call_args.args
    assert f"FROM {relation} p" in sql
    assert "ORDER BY distance_km" in sql
    assert "ST_DWithin" in sql
    assert "schools" in params
    assert 2000 in params
    if relation == "facts.poi":
        assert "JOIN facts.poi_category" in sql
        assert "c.code IN (%s)" in sql
    else:
        assert "facts.poi_category" not in sql
        assert "p.category IN (%s)" in sql
        assert "p.latitude BETWEEN %s AND %s" in sql
        assert "p.longitude BETWEEN %s AND %s" in sql
        assert all(isinstance(value, (int, float)) for value in params[:-1])
    local.assert_not_called()


@pytest.mark.asyncio
async def test_missing_postgres_poi_relations_use_local_store_and_recover(monkeypatch, tmp_path, caplog):
    """A missing optional schema must not trigger queries or prevent later recovery."""
    db_path = tmp_path / "osm_pois.IT.sqlite"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE osm_pois "
            "(category TEXT, name TEXT, latitude REAL, longitude REAL, tags_json TEXT)"
        )
        connection.executemany(
            "INSERT INTO osm_pois VALUES (?, ?, ?, ?, ?)",
            [
                ("schools", "Local school", 41.9, 12.5, "{}"),
                ("schools", "Distant school", 42.9, 12.5, "{}"),
                ("parks", "Local park", 41.9, 12.5, "{}"),
            ],
        )
    monkeypatch.setenv("OSM_POI_DB", str(db_path))
    source = stats_service._AsyncPostgresSource("postgresql://localhost/stats")
    probe = AsyncMock(side_effect=[{"relation": None}, {"relation": "source_osm_pois.osm_pois"}])
    fetch = AsyncMock(return_value=[])
    monkeypatch.setattr(source, "_fetchrow", probe)
    monkeypatch.setattr(source, "_fetch", fetch)
    monkeypatch.setattr(stats_service, "_get_async_postgres_source", AsyncMock(return_value=source))

    for _ in range(2):
        result = await stats_service.aget_pois_near(41.9, 12.5, radius_km=1, categories=["schools"])
        assert result["total"] == 1
        assert result["categories"]["schools"][0]["name"] == "Local school"
        assert result["categories"]["schools"][0]["distance_km"] == 0
        assert result["source"] == "OpenStreetMap via aecs4u-stats"
    probe.assert_awaited_once()
    fetch.assert_not_awaited()
    assert not [record for record in caplog.records if record.exc_info]

    source._poi_relation_cache = (None, 0.0)
    result = await stats_service.aget_pois_near(41.9, 12.5, categories=["schools"])
    assert result["total"] == 0
    assert result["source"] == "OpenStreetMap via aecs4u-stats (PostGIS asyncpg)"
    assert probe.await_count == 2
    fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_postgres_and_local_poi_stores_return_empty(monkeypatch):
    source = stats_service._AsyncPostgresSource("postgresql://localhost/stats")
    monkeypatch.setattr(source, "_fetchrow", AsyncMock(return_value={"relation": None}))
    fetch = AsyncMock(side_effect=AssertionError("missing table was queried"))
    monkeypatch.setattr(source, "_fetch", fetch)
    monkeypatch.setattr(stats_service, "_get_async_postgres_source", AsyncMock(return_value=source))
    monkeypatch.setattr("aecs4u_stats.osm.pois.resolve_poi_db", lambda *args, **kwargs: None)

    result = await stats_service.aget_pois_near(41.9, 12.5, categories=["schools"])

    assert result["total"] == 0
    assert result["categories"] == {"schools": []}
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_async_parcel_context_includes_optional_municipal_solar_aggregates(monkeypatch):
    source = stats_service._AsyncPostgresSource("postgresql://localhost/stats")
    municipality_row = {
        "id": 17,
        "canonical_name": "Roma",
        "unit_type": "comune",
        "istat_code": "058091",
        "cadastral_code": "H501",
        "observation_count": 4,
        "tax_fact_count": 2,
        "total_imponibile": 1200,
        "market_zone_count": 3,
        "pv_n_buildings": None,
        "pv_pvout_pessimistic_kwh_year_total": None,
        "pv_pvout_modern_kwh_year_total": None,
        "pv_pvout_per_capita_kwh": None,
        "pv_kwp_max_total": None,
        "pv_high_viability_pct": None,
        "pv_medium_viability_pct": None,
        "pv_low_viability_pct": None,
        "pv_not_eligible_pct": None,
        "pv_observation_count": None,
    }
    solar_row = {
        "pv_n_buildings": 120,
        "pv_pvout_pessimistic_kwh_year_total": 540000,
        "pv_pvout_modern_kwh_year_total": 690000,
        "pv_kwp_max_total": 420,
        "pv_high_viability_pct": 12.5,
        "pv_medium_viability_pct": 44.0,
        "pv_low_viability_pct": 31.0,
        "pv_not_eligible_pct": 12.5,
        "solar_data_version": "solar-comuni-2026-09",
        "solar_updated_at": "2026-09-30",
    }
    fetchrow = AsyncMock(side_effect=[municipality_row, solar_row, None, None])
    fetch = AsyncMock(return_value=[])
    monkeypatch.setattr(source, "_relations_available", AsyncMock(return_value=True))
    monkeypatch.setattr(source, "_fetchrow", fetchrow)
    monkeypatch.setattr(source, "_fetch", fetch)

    context = await source.context_for_parcel(
        "IT.AGC.STAT.058091.001",
        "H501",
        {"lat": 41.9, "lng": 12.5},
    )

    assert context is not None
    profile = context["municipality_profile"]
    assert profile["pv_n_buildings"] == 120
    assert profile["pv_pvout_modern_kwh_year_total"] == 690000
    assert profile["solar_data_version"] == "solar-comuni-2026-09"
    assert profile["solar_updated_at"] == "2026-09-30"
    assert profile["solar_source"] == "aecs4u-stats solar.solar_potential_comuni"
    assert "FROM solar.solar_potential_comuni" in fetchrow.await_args_list[1].args[0]
    assert "ltrim(pro_com_t::text, '0')" in fetchrow.await_args_list[1].args[0]
    fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_panel_read_model_falls_back_to_local_cache_and_hits_on_repeat(monkeypatch):
    reference = "H501_048600.D"
    parcel = {
        "type": "Feature",
        "properties": {"municipality_code": "H501", "sheet_number": "486", "parcel_number": "D"},
        "geometry": {"type": "Polygon", "coordinates": [[[12.47, 41.89], [12.48, 41.89], [12.48, 41.90], [12.47, 41.90], [12.47, 41.89]]]},
    }
    quotes = [{"zona": "B31", "prezzo_min": index, "prezzo_max": index + 1} for index in range(20)]
    full_payload = {
        "national_reference": reference,
        "omi": {"quotes": quotes},
        "blocks": {"valuation": {"available": True, "data": {"quotes": quotes}}},
        "source": "fixture",
    }
    cache = {}
    calls = {"build": 0}

    def read_local(cache_key):
        entry = cache.get(cache_key)
        if entry is None:
            return None
        payload, fingerprint = entry
        cached = deepcopy(payload)
        cached["read_model"] = {
            "key": cache_key,
            "source_fingerprint": fingerprint,
            "cached": True,
        }
        return cached

    def write_local(cache_key, payload, fingerprint):
        cache[cache_key] = (deepcopy(payload), fingerprint)

    def build(_reference, **_kwargs):
        calls["build"] += 1
        return deepcopy(full_payload)

    monkeypatch.setattr(stats_service, "_get_async_postgres_source", AsyncMock(return_value=None))
    monkeypatch.setattr(stats_service, "_parcel_enrichment_fingerprint", lambda: "fixture-fingerprint")
    monkeypatch.setattr(stats_service, "get_parcel_by_reference", lambda _reference: parcel)
    monkeypatch.setattr(stats_service, "aget_municipality_by_cadastral_code", AsyncMock(return_value=None))
    monkeypatch.setattr(stats_service, "aget_omi_quotes", AsyncMock(return_value={"quotes": []}))
    monkeypatch.setattr(stats_service, "aget_income_profile", AsyncMock(return_value=None))
    monkeypatch.setattr(stats_service, "_build_parcel_enrichment", build)
    monkeypatch.setattr(stats_service, "_read_local_panel_read_model", read_local)
    monkeypatch.setattr(stats_service, "_write_local_panel_read_model", write_local)

    first = await stats_service.aget_parcel_enrichment(reference, view="panel")
    second = await stats_service.aget_parcel_enrichment(reference, view="panel")

    assert first["read_model"]["cached"] is False
    assert first["blocks"]["valuation"]["data"]["quote_count"] == 20
    assert len(first["blocks"]["valuation"]["data"]["quote_preview"]) == 8
    assert second["read_model"]["cached"] is True
    assert second["read_model"]["database"] == "land-registry application SQLite panel cache"
    assert calls["build"] == 1
    assert list(cache) == [f"{reference}{stats_service._LOCAL_PANEL_CACHE_SUFFIX}"]




@pytest.mark.parametrize("relation", ["facts.poi", "source_osm_pois.osm_pois", None])
def test_sync_poi_lookup_uses_available_relation_or_local_fallback(monkeypatch, relation):
    source = stats_service._PostgresPoiSource("postgresql://localhost/stats")
    cursor = MagicMock()
    cursor.fetchone.return_value = (relation,)
    cursor.fetchall.return_value = [("schools", "School", 41.9, 12.5, 0.1234)]
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value = cursor

    @contextmanager
    def get_connection():
        yield connection

    monkeypatch.setattr(source, "_connection", get_connection)
    monkeypatch.setattr(stats_service, "_get_postgres_poi_source", lambda: source)
    local = Mock(return_value={"schools": []})
    monkeypatch.setattr(stats_service, "pois_within_radius", local)

    result = stats_service.get_pois_near(41.9, 12.5, categories=["schools"], use_postgres=True)

    if relation is None:
        assert result["total"] == 0
        local.assert_called_once_with(41.9, 12.5, radius_km=1.0, categories=["schools"])
        cursor.fetchall.assert_not_called()
    else:
        assert result["categories"]["schools"][0]["distance_km"] == 0.123
        assert result["source"] == "OpenStreetMap via aecs4u-stats (PostGIS)"
        assert f"FROM {relation} p" in cursor.execute.call_args.args[0]
        local.assert_not_called()
