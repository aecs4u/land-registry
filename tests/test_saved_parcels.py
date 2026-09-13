"""Persistence and validation tests for version-aware saved parcels."""

import json
import sqlite3

import pytest
from pydantic import ValidationError

from land_registry.models import SavedParcelCreateRequest, SavedParcelUpdateRequest
from land_registry.parcel_identity import build_source_key, parcel_identity_id, parcel_version_id
from land_registry.routers import api as api_router
from land_registry.sqlite_db import SQLiteDatabase


@pytest.fixture
def db(tmp_path):
    return SQLiteDatabase(db_path=str(tmp_path / "saved-parcels.sqlite"))


def test_saved_parcel_schema_and_crud_are_user_scoped(db):
    identity = parcel_identity_id(build_source_key("catasto", "RM-123"))
    version = parcel_version_id(identity, "2025-01")
    parcel = {
        "source": "catasto",
        "source_key": "CATASTO|REF=RM-123",
        "national_reference": "RM-123",
        "parcel_identity_id": str(identity),
        "parcel_version_id": str(version),
        "dataset_version": "2025-01",
        "label": "Test parcel",
        "notes": None,
        "geometry": {"type": "Point", "coordinates": [12.5, 41.9]},
    }

    saved_id = db.save_parcel("user-a", parcel)
    saved = db.get_saved_parcel(saved_id, "user-a")

    assert saved["parcel_identity_id"] == str(identity)
    assert saved["parcel_version_id"] == str(version)
    assert saved["geometry"] == '{"type": "Point", "coordinates": [12.5, 41.9]}'
    assert db.get_saved_parcel(saved_id, "user-b") is None
    assert [item["id"] for item in db.get_saved_parcels("user-a")] == [saved_id]
    assert db.delete_saved_parcel(saved_id, "user-b") is False
    assert db.update_saved_parcel(saved_id, "user-b", label="No access") is False
    assert db.update_saved_parcel(saved_id, "user-a", label="Updated parcel") is True
    assert db.get_saved_parcel(saved_id, "user-a")["label"] == "Updated parcel"
    assert db.delete_saved_parcel(saved_id, "user-a") is True


def test_saved_parcel_shortlist_metadata_round_trips(db):
    parcel = {
        "source": "catasto",
        "source_key": "CATASTO|REF=RM-SHORTLIST",
        "national_reference": "RM-SHORTLIST",
        "parcel_identity_id": "8e52ec0f-3a0f-5b20-af70-4ce7fb5c94b5",
        "parcel_version_id": None,
        "dataset_version": None,
        "label": "Shortlist parcel",
        "notes": "Call owner",
        "status": "researching",
        "priority": 5,
        "tags": ["urgent", "coastal"],
        "geometry": {"type": "Point", "coordinates": [12.5, 41.9]},
    }

    saved_id = db.save_parcel("user-a", parcel)
    saved = db.get_saved_parcel(saved_id, "user-a")

    assert saved["status"] == "researching"
    assert saved["priority"] == 5
    assert saved["tags"] == '["urgent", "coastal"]'

    assert db.update_saved_parcel(
        saved_id,
        "user-a",
        status="contacted",
        priority=3,
        notes="Left voicemail",
        tags=["owner", "follow-up"],
    )
    updated = db.get_saved_parcel(saved_id, "user-a")
    assert updated["status"] == "contacted"
    assert updated["priority"] == 3
    assert updated["notes"] == "Left voicemail"
    assert updated["tags"] == '["owner", "follow-up"]'


def test_saved_parcel_request_requires_non_snapshot_identity():
    with pytest.raises(ValidationError):
        SavedParcelCreateRequest(source="catasto")


def test_saved_parcel_identity_is_unique_when_dataset_version_is_unknown(db):
    parcel = {
        "source": "catasto",
        "source_key": "CATASTO|REF=RM-UNKNOWN-VERSION",
        "national_reference": "RM-UNKNOWN-VERSION",
        "parcel_identity_id": "e6492ecf-9eca-5bbd-88f1-677fdabb3578",
        "parcel_version_id": None,
        "dataset_version": None,
        "label": None,
        "notes": None,
        "geometry": None,
    }

    db.save_parcel("user-a", parcel)
    with pytest.raises(sqlite3.IntegrityError):
        db.save_parcel("user-a", parcel)

    # A different user may save the same parcel independently.
    db.save_parcel("user-b", parcel)


def test_saved_parcel_request_preserves_legacy_reference():
    request = SavedParcelCreateRequest(
        source="catasto",
        national_reference="RM-0007",
        dataset_version="2025-01",
    )

    assert request.national_reference == "RM-0007"
    assert request.dataset_version == "2025-01"


def test_saved_parcel_update_requires_a_mutable_field():
    with pytest.raises(ValidationError):
        SavedParcelUpdateRequest()


def test_saved_parcel_update_accepts_shortlist_only_metadata():
    request = SavedParcelUpdateRequest(status="researching", priority=4, tags="solar, follow-up")

    assert request.status == "researching"
    assert request.priority == 4
    assert request.tags == ["solar", "follow-up"]


def test_saved_parcel_collection_contract_uses_configured_statuses_and_summary(monkeypatch):
    monkeypatch.setenv(
        "SAVED_PARCEL_STATUS_VOCABULARY_JSON",
        json.dumps(
            [
                {"value": "triage", "label": "Triage", "is_initial": True, "is_active": True},
                {"value": "discarded", "label": "Discarded", "is_active": False},
            ]
        ),
    )
    row = {
        "id": 42,
        "source": "catasto",
        "source_key": "CATASTO|REF=RM-LEGACY",
        "national_reference": None,
        "parcel_identity_id": "8e52ec0f-3a0f-5b20-af70-4ce7fb5c94b5",
        "parcel_version_id": None,
        "dataset_version": None,
        "label": "Legacy saved parcel",
        "notes": "Needs review",
        "status": None,
        "priority": 2,
        "tags": '["legacy", "review"]',
        "geometry": '{"type": "Point", "coordinates": [12.5, 41.9]}',
        "created_at": "2026-09-13 10:00:00",
        "updated_at": "2026-09-13T11:00:00Z",
    }

    item = api_router._saved_parcel_row_to_response(row)
    summary = api_router._saved_parcel_summary([item, {"status": "discarded"}])

    assert item["status"] == "triage"
    assert item["tags"] == ["legacy", "review"]
    assert item["geometry"] == {"type": "Point", "coordinates": [12.5, 41.9]}
    assert item["created_at"] == "2026-09-13T10:00:00Z"
    assert item["updated_at"] == "2026-09-13T11:00:00Z"
    assert summary == {
        "total": 2,
        "by_status": {"triage": 1, "discarded": 1},
        "active_total": 1,
    }


def test_dpc_topojson_decoder_handles_transform_and_reversed_arcs():
    topology = {
        "type": "Topology",
        "transform": {"scale": [0.1, 0.1], "translate": [12.0, 41.0]},
        "objects": {
            "zones": {
                "type": "GeometryCollection",
                "geometries": [
                    {
                        "type": "Polygon",
                        "arcs": [[0]],
                        "properties": {"Nome zona": "Quantized zone"},
                    }
                ],
            }
        },
        "arcs": [
            [[0, 0], [10, 0], [0, 10], [-10, 0], [0, -10]],
        ],
    }

    features = api_router._topojson_features(topology)
    reversed_arc = api_router._topojson_arc_points(topology, -1)

    assert features == [
        {
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [[12.0, 41.0], [13.0, 41.0], [13.0, 42.0], [12.0, 42.0], [12.0, 41.0]]
                ],
            },
            "properties": {"Nome zona": "Quantized zone"},
        }
    ]
    assert reversed_arc == [[12.0, 41.0], [12.0, 42.0], [13.0, 42.0], [13.0, 41.0], [12.0, 41.0]]


def test_dpc_bulletin_hazard_intersects_saved_parcel_geometry(monkeypatch):
    bulletin = {
        "source": "DPC",
        "stamp": "Bollettino 2026-09-13 12:00",
        "today_zones": {
            "type": "Topology",
            "objects": {
                "zones": {
                    "type": "GeometryCollection",
                    "geometries": [
                        {
                            "type": "Polygon",
                            "arcs": [[0]],
                            "properties": {
                                "Nome zona": "Zona test",
                                "Rappresentata nella mappa": "ALLERTA GIALLA",
                            },
                        }
                    ],
                }
            },
            "arcs": [
                [[12.0, 41.0], [13.0, 41.0], [13.0, 42.0], [12.0, 42.0], [12.0, 41.0]]
            ],
        },
    }
    monkeypatch.setattr(api_router.stats_service, "get_criticality_bulletin", lambda: bulletin)

    hazard = api_router._active_bulletin_hazard({"type": "Point", "coordinates": [12.5, 41.5]})

    assert hazard["type"] == "dpc_criticality"
    assert hazard["zone"] == "Zona test"
    assert hazard["issue_time"] == "Bollettino 2026-09-13 12:00"
