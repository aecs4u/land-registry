"""Phase 0 contract and parcel identity regression tests."""

from datetime import date, datetime, timezone
from unittest.mock import patch
from uuid import UUID

import pytest
from pydantic import ValidationError

from land_registry.main import app, health_check
from land_registry.models import (
    ComuniSearchResponse,
    DataBlock,
    EnrichmentDatasetStatus,
    GeoJSONFeatureCollection,
    HealthResponse,
    LineageMetadata,
    ParcelVersion,
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


def test_openapi_exposes_the_authoritative_health_schema():
    document = app.openapi()

    assert "ErrorResponse" in document["components"]["schemas"]

    assert document["paths"]["/health"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"] == (
        "#/components/schemas/HealthResponse"
    )
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
    with patch(
        "land_registry.stats_service.enrichment_status",
        return_value={
            "cadastral_parcels": {"available": True, "note": "regional stores"},
            "istat_municipalities": {"available": False, "path": "/data/istat.sqlite"},
        },
    ):
        payload = await get_enrichment_status()

    assert payload
    assert "cadastral_parcels" in payload
    assert all(EnrichmentDatasetStatus.model_validate(value).available in (True, False) for value in payload.values())


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


def test_parcel_version_rejects_inverted_validity_range():
    with pytest.raises(ValidationError):
        ParcelVersion(
            parcel_version_id=UUID("b6a6e302-2f93-4a89-9cb0-75c49c9d0dd1"),
            parcel_identity_id=UUID("a6a6e302-2f93-4a89-9cb0-75c49c9d0dd1"),
            dataset_version="2025-01",
            valid_from=date(2025, 2, 1),
            valid_to=date(2025, 1, 1),
            lineage=LineageMetadata(source="test"),
        )
