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
    fetchrow = AsyncMock(side_effect=[municipality_row, solar_row, None, None, None])
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
async def test_country_relocation_indicators_keep_country_scope_and_units(monkeypatch):
    source = stats_service._AsyncPostgresSource("postgresql://localhost/stats")
    fetch = AsyncMock(side_effect=[
        [{"relation_name": "serving.relocation_safety_index"}],
        [
            {"table_name": "relocation_safety_index", "column_name": "country_code"},
            {"table_name": "relocation_safety_index", "column_name": "year"},
            {"table_name": "relocation_safety_index", "column_name": "score"},
            {"table_name": "relocation_safety_index", "column_name": "unit"},
        ],
        [{"payload": {"country_code": "IT", "year": 2024, "score": 71.5, "unit": "index"}}],
    ])
    monkeypatch.setattr(source, "_fetch", fetch)

    result = await source.relocation_quality_indicators()

    assert result["spatial_resolution"] == "country"
    assert result["country_code"] == "IT"
    assert result["years"] == ["2024"]
    safety = result["clusters"][0]["indicators"][0]
    assert safety["name"] == "Safety Index"
    assert safety["values"]["2024"] == {
        "value": 71.5,
        "unit": "index",
        "temporal_reference": None,
    }
    assert "not describe the province or parcel" in result["scope_note"]


@pytest.mark.asyncio
async def test_municipality_demographic_series_reports_its_scope(monkeypatch):
    source = stats_service._AsyncPostgresSource("postgresql://localhost/stats")
    monkeypatch.setattr(source, "_relations_available", AsyncMock(return_value=True))
    monkeypatch.setattr(
        source,
        "_serving_municipality_by_cadastral_code",
        AsyncMock(return_value={"istat_code": "058091", "official_name": "Roma"}),
    )
    columns = [
        {"column_name": "reference_year"},
        {"column_name": "population"},
        {"column_name": "comune_code"},
        {"column_name": "population_lt12"},
        {"column_name": "population_ge12"},
    ]
    rows = [
        {"year": 2023, "population": 2750000, "population_lt12": 290000, "population_ge12": 2460000},
    ]
    monkeypatch.setattr(source, "_fetch", AsyncMock(side_effect=[columns, rows, columns, rows]))

    catalog = await source.municipality_demographic_indicators("H501")
    series = await source.municipality_demographic_series("H501", "resident_population")

    assert catalog["spatial_resolution"] == "municipality"
    assert catalog["municipality"] == "Roma"
    assert catalog["series_by_indicator"]["population_under_12"][0]["value"] == 290000
    assert series["series"] == [{"year": 2023, "value": 2750000, "unit": "residents"}]


@pytest.mark.asyncio
async def test_municipality_demographics_work_without_optional_age_columns(monkeypatch):
    source = stats_service._AsyncPostgresSource("postgresql://localhost/stats")
    monkeypatch.setattr(source, "_relations_available", AsyncMock(return_value=True))
    monkeypatch.setattr(
        source,
        "_serving_municipality_by_cadastral_code",
        AsyncMock(return_value={"istat_code": "058091", "official_name": "Roma"}),
    )
    fetch = AsyncMock(side_effect=[
        [{"column_name": "reference_year"}, {"column_name": "population"}, {"column_name": "comune_code"}],
        [{"year": 2023, "population": 2750000, "population_lt12": None, "population_ge12": None}],
    ])
    monkeypatch.setattr(source, "_fetch", fetch)

    result = await source.municipality_demographic_indicators("H501")

    assert result["indicators"] == ["resident_population"]
    assert result["series_by_indicator"] == {
        "resident_population": [{"year": 2023, "value": 2750000, "unit": "residents"}],
    }
    query, params = fetch.await_args_list[1].args
    assert "NULL AS population_lt12" in query
    assert "NULL AS population_ge12" in query
    assert params == ("058091",)


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

    full = await stats_service.aget_parcel_enrichment(reference, view="full")
    first = await stats_service.aget_parcel_enrichment(reference, view="panel")
    second = await stats_service.aget_parcel_enrichment(reference, view="panel")

    assert full["read_model"]["cached"] is False
    assert full["read_model"]["database"] is None
    assert first["read_model"]["cached"] is False
    assert first["read_model"]["database"] == "land-registry application SQLite panel cache"
    assert first["blocks"]["valuation"]["data"]["quote_count"] == 20
    assert len(first["blocks"]["valuation"]["data"]["quote_preview"]) == 8
    assert second["read_model"]["cached"] is True
    assert second["read_model"]["database"] == "land-registry application SQLite panel cache"
    assert calls["build"] == 2
    assert list(cache) == [f"{reference}{stats_service._LOCAL_PANEL_CACHE_SUFFIX}"]


@pytest.mark.asyncio
async def test_feature_specific_read_models_use_distinct_cache_keys(monkeypatch):
    reference = "H501_048600.D"
    parcels = {
        101: {
            "type": "Feature",
            "id": 101,
            "properties": {"municipality_code": "H501", "sheet_number": "486", "parcel_number": "D"},
            "geometry": {"type": "Polygon", "coordinates": [[[12.47, 41.89], [12.48, 41.89], [12.48, 41.90], [12.47, 41.90], [12.47, 41.89]]]},
        },
        202: {
            "type": "Feature",
            "id": 202,
            "properties": {"municipality_code": "H501", "sheet_number": "486", "parcel_number": "D"},
            "geometry": {"type": "Polygon", "coordinates": [[[12.50, 41.89], [12.51, 41.89], [12.51, 41.90], [12.50, 41.90], [12.50, 41.89]]]},
        },
        303: {
            "type": "Feature",
            "id": 303,
            "properties": {"municipality_code": "H501", "sheet_number": "486", "parcel_number": "D"},
            "geometry": {"type": "Polygon", "coordinates": [[[12.53, 41.89], [12.54, 41.89], [12.54, 41.90], [12.53, 41.90], [12.53, 41.89]]]},
        },
    }

    class FakeSource:
        _retry_at = 0.0

        def __init__(self):
            self.rows = {}
            self.read_keys = []
            self.write_keys = []

        async def get_read_model(self, key):
            self.read_keys.append(key)
            payload = self.rows.get(key)
            if payload is None:
                return None
            result = deepcopy(payload)
            result["read_model"].update({
                "key": key,
                "source_fingerprint": "feature-cache-fingerprint",
                "cached": True,
                "database": "aecs4u-stats PostgreSQL via asyncpg",
            })
            return result

        async def upsert_read_model(self, key, payload, source_fingerprint):
            self.write_keys.append(key)
            self.rows[key] = deepcopy(payload)
            return True

        async def context_for_parcel(self, *args, **kwargs):
            return None

    class FakeMapSource:
        available = True

        async def read_feature_by_reference(self, layer_id, national_reference, *, feature_id):
            assert layer_id == "cadastral-parcels"
            assert national_reference == reference
            return deepcopy(parcels[feature_id])

    source = FakeSource()
    source.rows[f"{reference}::feature:303"] = {
        "national_reference": reference,
        "parcel": deepcopy(parcels[101]),
        "blocks": {},
        "source": "stale fixture",
        "read_model": {"feature_id": 101},
    }
    local_reads = []
    builds = []

    async def no_data(*_args, **_kwargs):
        return None

    def read_local(key):
        local_reads.append(key)
        return None

    def build(_reference, **kwargs):
        parcel = kwargs["parcel_override"]
        builds.append(parcel["id"])
        return {
            "national_reference": reference,
            "parcel": parcel,
            "blocks": {},
            "source": "fixture",
        }

    monkeypatch.setattr(stats_service, "_get_async_postgres_source", AsyncMock(return_value=source))
    monkeypatch.setattr(stats_service, "_parcel_enrichment_fingerprint", lambda: "feature-cache-fingerprint")
    monkeypatch.setattr(stats_service, "aget_municipality_by_cadastral_code", no_data)
    monkeypatch.setattr(stats_service, "aget_omi_quotes", no_data)
    monkeypatch.setattr(stats_service, "aget_income_profile", no_data)
    monkeypatch.setattr(stats_service, "_parcel_centroid", lambda _parcel: None)
    monkeypatch.setattr(stats_service, "_build_parcel_enrichment", build)
    monkeypatch.setattr(stats_service, "_read_local_panel_read_model", read_local)
    monkeypatch.setattr(stats_service, "_write_local_panel_read_model", lambda *_args: None)
    monkeypatch.setattr("land_registry.map_layers.get_map_layer_source", lambda: FakeMapSource())

    first = await stats_service.aget_parcel_enrichment(reference, view="panel", feature_id=101)
    other = await stats_service.aget_parcel_enrichment(reference, view="panel", feature_id=202)
    stale = await stats_service.aget_parcel_enrichment(reference, view="panel", feature_id=303)
    repeat = await stats_service.aget_parcel_enrichment(reference, view="panel", feature_id=101)

    assert builds == [101, 202, 303]
    assert source.read_keys == [
        f"{reference}::feature:101",
        f"{reference}::feature:202",
        f"{reference}::feature:303",
        f"{reference}::feature:101",
    ]
    assert source.write_keys == [
        f"{reference}::feature:101",
        f"{reference}::feature:202",
        f"{reference}::feature:303",
    ]
    assert local_reads == [
        f"{reference}::feature:101{stats_service._LOCAL_PANEL_CACHE_SUFFIX}",
        f"{reference}::feature:202{stats_service._LOCAL_PANEL_CACHE_SUFFIX}",
        f"{reference}::feature:303{stats_service._LOCAL_PANEL_CACHE_SUFFIX}",
    ]
    assert first["read_model"]["feature_id"] == 101
    assert other["read_model"]["feature_id"] == 202
    assert stale["read_model"]["feature_id"] == 303
    assert repeat["read_model"]["feature_id"] == 101
    assert repeat["read_model"]["cached"] is True
    assert repeat["read_model"]["key"] == reference


@pytest.mark.asyncio
async def test_feature_id_survives_local_panel_cache_fallback(monkeypatch):
    reference = "H501_048600.D"
    feature_id = 707
    parcel = {
        "type": "Feature",
        "id": feature_id,
        "properties": {"municipality_code": "H501", "sheet_number": "486", "parcel_number": "D"},
        "geometry": {"type": "Polygon", "coordinates": [[[12.47, 41.89], [12.48, 41.89], [12.48, 41.90], [12.47, 41.90], [12.47, 41.89]]]},
    }
    local_cache = {}
    expected_key = f"{reference}::feature:{feature_id}{stats_service._LOCAL_PANEL_CACHE_SUFFIX}"
    local_cache[expected_key] = {
        "national_reference": reference,
        "blocks": {},
        "source": "stale fixture",
        "read_model": {
            "feature_id": 606,
            "source_fingerprint": "local-feature-fingerprint",
        },
    }
    local_reads = []
    builds = []

    class FakeMapSource:
        available = True

        async def read_feature_by_reference(self, *_args, **_kwargs):
            return deepcopy(parcel)

    async def no_data(*_args, **_kwargs):
        return None

    def read_local(key):
        local_reads.append(key)
        cached = local_cache.get(key)
        return deepcopy(cached) if cached is not None else None

    def write_local(key, payload, fingerprint):
        local_cache[key] = deepcopy(payload)
        local_cache[key]["read_model"]["source_fingerprint"] = fingerprint

    def build(_reference, **kwargs):
        builds.append(kwargs["parcel_override"]["id"])
        return {
            "national_reference": reference,
            "parcel": kwargs["parcel_override"],
            "blocks": {},
            "source": "fixture",
        }

    monkeypatch.setattr(stats_service, "_get_async_postgres_source", AsyncMock(return_value=None))
    monkeypatch.setattr(stats_service, "_parcel_enrichment_fingerprint", lambda: "local-feature-fingerprint")
    monkeypatch.setattr(stats_service, "aget_municipality_by_cadastral_code", no_data)
    monkeypatch.setattr(stats_service, "aget_omi_quotes", no_data)
    monkeypatch.setattr(stats_service, "aget_income_profile", no_data)
    monkeypatch.setattr(stats_service, "_parcel_centroid", lambda _parcel: None)
    monkeypatch.setattr(stats_service, "_build_parcel_enrichment", build)
    monkeypatch.setattr(stats_service, "_read_local_panel_read_model", read_local)
    monkeypatch.setattr(stats_service, "_write_local_panel_read_model", write_local)
    monkeypatch.setattr("land_registry.map_layers.get_map_layer_source", lambda: FakeMapSource())

    first = await stats_service.aget_parcel_enrichment(
        reference, view="panel", feature_id=feature_id
    )
    second = await stats_service.aget_parcel_enrichment(
        reference, view="panel", feature_id=feature_id
    )

    assert local_reads == [expected_key, expected_key]
    assert list(local_cache) == [expected_key]
    assert builds == [feature_id]
    assert first["read_model"]["feature_id"] == feature_id
    assert second["read_model"]["feature_id"] == feature_id
    assert second["read_model"]["cached"] is True
    assert second["read_model"]["database"] == "land-registry application SQLite panel cache"




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
