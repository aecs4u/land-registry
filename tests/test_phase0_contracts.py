"""Phase 0 contract and parcel identity regression tests."""

from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from pydantic import ValidationError

from land_registry import stats_service
from land_registry.main import app, health_check
from land_registry.models import (
    ComuniSearchResponse,
    DataBlock,
    EnrichmentDatasetStatus,
    GeoJSONFeatureCollection,
    HealthResponse,
    LineageMetadata,
)
from land_registry.routers.enrichment import get_enrichment_status
from land_registry.routers.api import _attach_parcel_identity
from land_registry.parcel_identity import (
    build_source_key,
    canonical_source_key,
    parcel_identity_id,
    parcel_version_id,
)


@pytest.mark.asyncio
async def test_health_contract_is_typed_and_stable():
    assert HealthResponse.model_validate(await health_check()).model_dump() == {
        "status": "healthy",
        "service": "land-registry",
    }


def test_canonical_cadastral_sqlmodel_is_owned_by_domain_package():
    from aecs4u_domain.real_estate.cadastral_parcel import CadastralParcel

    assert CadastralParcel.__module__.startswith("aecs4u_domain.")
    assert CadastralParcel.__tablename__ == "cadastral_parcels"


def test_shared_parcel_persistence_models_are_owned_by_domain_package():
    from aecs4u_domain.real_estate.models import ParcelIdentity, ParcelVersion, SavedParcel

    assert ParcelIdentity.__module__ == "aecs4u_domain.real_estate.models.parcel_reference"
    assert ParcelVersion.__module__ == "aecs4u_domain.real_estate.models.parcel_reference"
    assert SavedParcel.__module__ == "aecs4u_domain.real_estate.models.parcel_reference"
    assert ParcelIdentity.__tablename__ == "parcel_identities"
    assert ParcelVersion.__tablename__ == "parcel_versions"
    assert SavedParcel.__tablename__ == "saved_parcels"


def test_openapi_exposes_the_authoritative_health_schema():
    document = app.openapi()

    assert "ErrorResponse" in document["components"]["schemas"]

    health_ref = document["paths"]["/health"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    assert health_ref.startswith("#/components/schemas/")
    health_schema_name = health_ref.removeprefix("#/components/schemas/")
    assert document["components"]["schemas"][health_schema_name] == HealthResponse.model_json_schema()
    assert "/api/v1/enrichment/status" in document["paths"]
    status_schema = document["paths"]["/api/v1/enrichment/status"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert status_schema["additionalProperties"]["$ref"] == "#/components/schemas/EnrichmentDatasetStatus"

    for path, method in (
        ("/api/v1/search/parcels", "get"),
        ("/api/v1/cadastral/query", "post"),
        ("/api/v1/cadastral/search/{reference}", "get"),
        ("/api/v1/ghsl/ucdb", "get"),
    ):
        schema = document["paths"][path][method]["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema["$ref"] == "#/components/schemas/GeoJSONFeatureCollection"

    assert document["paths"]["/api/v1/saved-parcels"]["post"]["responses"]["201"]["content"][
        "application/json"
    ]["schema"]["$ref"] == "#/components/schemas/SavedParcelResponse"


@pytest.mark.asyncio
async def test_enrichment_status_preserves_existing_dataset_keys_and_is_typed():
    with (
        patch(
            "land_registry.stats_service.enrichment_status",
            return_value={
                "cadastral_parcels": {"available": True, "note": "regional stores"},
                "istat_municipalities": {"available": False, "path": "/data/istat.sqlite"},
            },
        ),
        patch(
            "land_registry.stats_service._get_async_postgres_source",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        payload = await get_enrichment_status()

    assert payload
    assert "cadastral_parcels" in payload
    assert all(EnrichmentDatasetStatus.model_validate(value).available in (True, False) for value in payload.values())
    assert payload["parcel_enrichment_read_model"]["reason"] == "not_configured"


def test_optional_store_status_explains_missing_support_build_and_probe_errors():
    assert "reason" in EnrichmentDatasetStatus.model_json_schema()["properties"]
    assert stats_service._optional_store_status(None) == {
        "available": False,
        "reason": "not_supported",
    }
    assert stats_service._optional_store_status(lambda: True, None) == {
        "available": False,
        "reason": "not_supported",
    }
    assert stats_service._optional_store_status(lambda: False, lambda: True) == {
        "available": False,
        "reason": "not_built",
    }

    def adapter_requires_arguments(_lat, _lng):
        raise AssertionError("readiness must not invoke the query adapter")

    assert stats_service._optional_store_status(lambda: True, adapter_requires_arguments) == {
        "available": True,
        "reason": None,
    }

    def failed_probe():
        raise RuntimeError("store unavailable")

    assert stats_service._optional_store_status(failed_probe) == {
        "available": False,
        "reason": "probe_failed",
    }


@pytest.mark.parametrize(
    ("availability_name", "checker_name"),
    (
        ("safety_db_available", "safety_available"),
        ("bes_db_available", "bes_available"),
        ("demographic_db_available", "demographic_available"),
    ),
)
def test_unbuilt_istat_source_is_unavailable_without_traceback(
    monkeypatch, caplog, availability_name, checker_name
):
    def missing_source():
        raise FileNotFoundError("SQLite source not found")

    monkeypatch.setattr(
        stats_service,
        "_istat_query_engine",
        lambda: SimpleNamespace(**{checker_name: missing_source}),
    )
    monkeypatch.setattr(stats_service, "_istat_sqlite_table_available", lambda _table: False)
    monkeypatch.setattr(stats_service, "_istat_bes_rows", lambda: ())

    with caplog.at_level("DEBUG", logger="land_registry.stats_service"):
        assert getattr(stats_service, availability_name)() is False

    assert "store is not built" in caplog.text
    assert "Traceback" not in caplog.text


def test_missing_bes_sqlite_snapshot_does_not_log_open_error(monkeypatch, tmp_path, caplog):
    missing_snapshot = tmp_path / "eurostat.IT.sqlite"
    monkeypatch.setenv("ISTAT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(stats_service, "_istat_sqlite_path", lambda: missing_snapshot)
    stats_service._istat_bes_rows.cache_clear()
    try:
        with caplog.at_level("DEBUG", logger="land_registry.stats_service"):
            assert stats_service._istat_bes_rows() == ()
    finally:
        stats_service._istat_bes_rows.cache_clear()

    assert "SQLite BES snapshot could not be read" not in caplog.text
    assert "Traceback" not in caplog.text


def test_enrichment_status_exposes_parcel_spatial_store_readiness(monkeypatch):
    false_probes = (
        "istat_db_available", "postgres_stats_available", "cadastral_store_available",
        "census_db_available", "sister_buildings_available", "sister_documents_available",
        "safety_db_available", "bes_db_available", "demographic_db_available", "poi_db_available",
        "omi_db_available_public", "zone_boundaries_available", "mef_db_available_public",
        "seismic_db_available", "_mps04_db_available", "_ispra_mosaics_db_available",
        "_egms_db_available",
    )
    for name in false_probes:
        monkeypatch.setattr(stats_service, name, lambda *args, **kwargs: False)
    for name in (
        "_get_sister_postgres_source", "_get_opendata_postgres_source", "_get_pvp_postgres_source",
    ):
        monkeypatch.setattr(stats_service, name, lambda: None)
    monkeypatch.setattr(stats_service, "get_criticality_bulletin", lambda: None)
    monkeypatch.setattr(stats_service, "_mps04_pga_at_point", lambda *args, **kwargs: None)
    monkeypatch.setattr(stats_service, "_landslide_hazard_in_bbox", lambda *args, **kwargs: {})
    monkeypatch.setattr(stats_service, "_flood_hazard_in_bbox", lambda *args, **kwargs: {})
    monkeypatch.setattr(stats_service, "_subsidence_in_bbox", lambda *args, **kwargs: {})

    status = stats_service.enrichment_status()

    assert status["hazards_mps04"]["available"] is False
    assert status["hazards_mps04"]["reason"] == "not_built"
    assert status["hazards_mps04"]["source_version"] == "MPS04"
    assert status["hazards_ispra_mosaics"]["available"] is False
    assert status["hazards_ispra_mosaics"]["reason"] == "not_built"
    assert status["egms_subsidence"]["available"] is False
    assert status["egms_subsidence"]["reason"] == "not_built"
    typed_status = EnrichmentDatasetStatus.model_validate(status["hazards_mps04"])
    assert typed_status.model_dump()["reason"] == "not_built"


@pytest.mark.asyncio
async def test_parcel_read_model_status_distinguishes_unprovisioned_and_probe_failure(monkeypatch):
    class Source:
        def __init__(self, available=False, error=None):
            self.available = available
            self.error = error

        async def _read_model_available(self):
            if self.error:
                raise self.error
            return self.available

    async def source_factory(source):
        return source

    monkeypatch.setattr(stats_service, "enrichment_status", lambda: {})
    monkeypatch.setattr(
        stats_service,
        "_get_async_postgres_source",
        lambda: source_factory(None),
    )
    assert (await stats_service.aenrichment_status())["parcel_enrichment_read_model"]["reason"] == "not_configured"

    monkeypatch.setattr(
        stats_service,
        "_get_async_postgres_source",
        lambda: source_factory(Source()),
    )
    assert (await stats_service.aenrichment_status())["parcel_enrichment_read_model"]["reason"] == "not_built"

    monkeypatch.setattr(
        stats_service,
        "_get_async_postgres_source",
        lambda: source_factory(Source(error=RuntimeError("database probe failed"))),
    )
    assert (await stats_service.aenrichment_status())["parcel_enrichment_read_model"]["reason"] == "probe_failed"

    monkeypatch.setattr(
        stats_service,
        "_get_async_postgres_source",
        lambda: source_factory(Source(available=True)),
    )
    assert (await stats_service.aenrichment_status())["parcel_enrichment_read_model"]["available"] is True


def test_lineage_contract_preserves_crs_units_and_nullability():
    lineage = LineageMetadata(
        source="aecs4u-stats",
        dataset="omi_quotes",
        source_version="2025-Q4",
        source_reference_date=date(2025, 12, 31),
        output_crs="EPSG:4326",
        units={"price_eur_m2": "EUR/m2"},
    )
    block = DataBlock[dict](available=True, data={"price_eur_m2": None}, lineage=lineage)

    assert block.data["price_eur_m2"] is None
    assert block.lineage.output_crs == "EPSG:4326"
    assert block.lineage.units["price_eur_m2"] == "EUR/m2"

    normalized = LineageMetadata(
        source="test",
        processed_at=datetime(2025, 1, 2, 13, 0, tzinfo=timezone.utc),
    )
    assert normalized.processed_at.tzinfo == timezone.utc

    with pytest.raises(ValidationError):
        DataBlock[dict](available=False, data={"value": 1}, coverage="unavailable", lineage=lineage)

    with pytest.raises(ValidationError):
        LineageMetadata(source="test", processed_at=datetime(2025, 1, 2, 13, 0))


def test_geojson_and_search_contracts_validate_stable_shapes():
    collection = GeoJSONFeatureCollection.model_validate(
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": 7,
                    "geometry": {"type": "Point", "coordinates": [12.0, 41.0]},
                    "properties": {"area_m2": None},
                }
            ],
            "count": 1,
            "metadata": {
                "source": "land-registry.loaded-dataset",
                "dataset": "loaded-cadastral",
                "output_crs": "EPSG:4326",
                "units": {},
            },
        }
    )
    assert collection.features[0].geometry["coordinates"] == [12.0, 41.0]
    assert collection.features[0].properties["area_m2"] is None
    assert collection.metadata.output_crs == "EPSG:4326"
    assert ComuniSearchResponse.model_validate({"comuni": ["ROMA"]}).comuni == ["ROMA"]


def test_parcel_identity_is_independent_of_feature_order_and_dataset_version():
    key_a = build_source_key("catasto", " rm-123 ")
    key_b = build_source_key("CATASTO", "RM-123")
    identity_a = parcel_identity_id(key_a)
    identity_b = parcel_identity_id(key_b)

    assert key_a == key_b == "CATASTO|REF=RM-123"
    assert identity_a == identity_b
    assert isinstance(identity_a, UUID)
    assert parcel_version_id(identity_a, "2025-01") != parcel_version_id(identity_a, "2025-02")


def test_parcel_identity_fallback_retains_significant_zeroes():
    key = build_source_key(
        "legacy-cadastral",
        municipality_code="H501",
        section="A",
        sheet="001",
        parcel="0007",
    )

    assert key == "LEGACY-CADASTRAL|COMUNE=H501|SECTION=A|SHEET=001|PARCEL=0007"


def test_source_key_is_canonical_and_source_qualified():
    assert canonical_source_key("catasto", " rm-001 ") == "CATASTO|REF=RM-001"
    assert canonical_source_key("catasto", "CATASTO|REF=RM-001") == "CATASTO|REF=RM-001"
    with pytest.raises(ValueError):
        canonical_source_key("catasto", "OTHER|REF=RM-001")


def test_geojson_features_expose_derived_identity_without_replacing_feature_id():
    feature = {
        "type": "Feature",
        "id": 19,
        "properties": {
            "national_reference": "RM-001",
            "dataset_version": "2025-01",
        },
    }

    enriched = _attach_parcel_identity(feature)

    assert enriched["id"] == 19
    assert enriched["properties"]["parcel_identity_id"]
    assert enriched["properties"]["parcel_version_id"]


def test_parcel_version_defines_validity_range_database_constraint():
    from aecs4u_domain.real_estate.models import ParcelVersion

    constraint_names = {constraint.name for constraint in ParcelVersion.__table__.constraints}

    assert "ck_parcel_versions_valid_range" in constraint_names


@pytest.mark.asyncio
async def test_enrichment_status_runs_off_the_event_loop_and_is_cached():
    import threading

    from land_registry.routers import enrichment as enrichment_router_module

    calls = []
    loop_thread = threading.get_ident()

    def probe():
        calls.append(threading.get_ident())
        return {"cadastral_parcels": {"available": True}}

    enrichment_router_module._status_cache = None
    with (
        patch("land_registry.stats_service.enrichment_status", probe),
        patch(
            "land_registry.stats_service._get_async_postgres_source",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        first = await get_enrichment_status()
        second = await get_enrichment_status()

    assert first == second
    assert len(calls) == 1, "the second call within the TTL must reuse the cached report"
    assert calls[0] != loop_thread, "the probe must not run on the event loop thread"
    enrichment_router_module._status_cache = None


@pytest.mark.asyncio
async def test_enrichment_status_recomputes_after_the_cache_expires(monkeypatch):
    from land_registry.routers import enrichment as enrichment_router_module

    calls = []

    def probe():
        calls.append(1)
        return {"cadastral_parcels": {"available": True}}

    enrichment_router_module._status_cache = None
    monkeypatch.setattr(enrichment_router_module, "_STATUS_CACHE_SECONDS", 0.0)
    with (
        patch("land_registry.stats_service.enrichment_status", probe),
        patch(
            "land_registry.stats_service._get_async_postgres_source",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        await get_enrichment_status()
        await get_enrichment_status()

    assert len(calls) == 2
    enrichment_router_module._status_cache = None
