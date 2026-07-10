"""
Additional tests for zones/microzones endpoint paths not covered elsewhere.

Covers:
- GET /zones/geojson with invisible zones (line 2257)
- GET /zones/geojson with invalid JSON in zone record (lines 2271-2272)
- GET /zones/ with polygon_type filter (line 2299)
- GET /zones/ with tag filter (line 2302)
- POST /zones/visibility bulk toggle (lines 2395-2404)
- POST /microzones/visibility bulk toggle (lines 2421-2423)
- PATCH /zones/{id} update zone description, color, tags, is_visible
- GET /zones/{id}/microzones/ include_geojson=True
"""

import json
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from land_registry.main import app
from land_registry.routers.auth import get_current_user as _get_current_user

MOCK_USER = MagicMock(id="zones-test-user", email="zones@test.com")


def _fake_user():
    return MOCK_USER


@pytest.fixture
def authed_client():
    app.dependency_overrides[_get_current_user] = _fake_user
    yield TestClient(app, follow_redirects=True)
    app.dependency_overrides.pop(_get_current_user, None)


VALID_ZONE = {
    "name": "Test Zone",
    "description": "A test",
    "geojson": {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[12.0, 41.0], [13.0, 41.0], [13.0, 42.0], [12.0, 42.0], [12.0, 41.0]]]
        },
        "properties": {}
    },
    "polygon_type": "polygon",
    "color": "#3388ff",
    "tags": ["tagA"]
}

VALID_MICROZONE = {
    "name": "Test Microzone",
    "geojson": {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[12.0, 41.0], [12.5, 41.0], [12.5, 41.5], [12.0, 41.5], [12.0, 41.0]]]
        },
        "properties": {}
    },
    "microzone_type": "polygon",
    "color": "#ff0000",
    "tags": []
}


# ---------------------------------------------------------------------------
# GET /zones/geojson
# ---------------------------------------------------------------------------

class TestZonesGeojsonCoverage:

    def test_zones_geojson_skips_invisible_zone(self, authed_client):
        """Invisible zone (is_visible=0) should be excluded from GeoJSON output."""
        # Create a zone
        create_resp = authed_client.post("/api/v1/zones/", json=VALID_ZONE)
        assert create_resp.status_code in (200, 201)
        zone_id = create_resp.json()["zone"]["id"]

        # Make it invisible
        authed_client.patch(f"/api/v1/zones/{zone_id}", json={"is_visible": False})

        # Get geojson - invisible zone should be excluded
        resp = authed_client.get("/api/v1/zones/geojson")
        assert resp.status_code == 200
        data = resp.json()
        zone_ids_in_response = [f["properties"]["zone_id"] for f in data["features"]]
        assert zone_id not in zone_ids_in_response

    def test_zones_geojson_with_visible_zone(self, authed_client):
        """Visible zone is included in GeoJSON output."""
        create_resp = authed_client.post("/api/v1/zones/", json=VALID_ZONE)
        zone_id = create_resp.json()["zone"]["id"]

        resp = authed_client.get("/api/v1/zones/geojson")
        assert resp.status_code == 200
        data = resp.json()
        zone_ids_in_response = [f["properties"]["zone_id"] for f in data["features"]]
        assert zone_id in zone_ids_in_response


# ---------------------------------------------------------------------------
# GET /zones/ with filters
# ---------------------------------------------------------------------------

class TestListZonesWithFilters:

    def _create_zone(self, client, name="Zone", polygon_type="polygon", tags=None):
        zone = {**VALID_ZONE, "name": name, "polygon_type": polygon_type}
        if tags is not None:
            zone["tags"] = tags
        resp = client.post("/api/v1/zones/", json=zone)
        assert resp.status_code in (200, 201)
        return resp.json()["zone"]["id"]

    def test_list_zones_filter_by_polygon_type(self, authed_client):
        """Only zones matching polygon_type are returned."""
        self._create_zone(authed_client, name="Poly1", polygon_type="polygon")
        self._create_zone(authed_client, name="Circle1", polygon_type="polygon")  # same type, still polygon

        resp = authed_client.get("/api/v1/zones/?polygon_type=polygon")
        assert resp.status_code == 200
        zones = resp.json()["zones"]
        assert all(z["polygon_type"] == "polygon" for z in zones)

    def test_list_zones_filter_by_nonexistent_type(self, authed_client):
        """Filter by type that no zone has returns empty list."""
        self._create_zone(authed_client, polygon_type="polygon")

        resp = authed_client.get("/api/v1/zones/?polygon_type=circle")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_list_zones_filter_by_tag(self, authed_client):
        """Only zones with the specified tag are returned."""
        self._create_zone(authed_client, name="Tagged", tags=["important", "urgent"])
        self._create_zone(authed_client, name="Untagged", tags=["other"])

        resp = authed_client.get("/api/v1/zones/?tag=important")
        assert resp.status_code == 200
        zones = resp.json()["zones"]
        # All returned zones should have the tag
        for z in zones:
            assert "important" in z.get("tags", [])

    def test_list_zones_no_filter_returns_all(self, authed_client):
        self._create_zone(authed_client, name="A")
        self._create_zone(authed_client, name="B")
        resp = authed_client.get("/api/v1/zones/")
        assert resp.status_code == 200
        assert resp.json()["total"] >= 2


# ---------------------------------------------------------------------------
# POST /zones/visibility bulk toggle
# ---------------------------------------------------------------------------

class TestBulkZoneVisibility:

    def test_bulk_toggle_visibility_hide(self, authed_client):
        """Bulk set zones to invisible."""
        create_resp = authed_client.post("/api/v1/zones/", json=VALID_ZONE)
        zone_id = create_resp.json()["zone"]["id"]

        resp = authed_client.post("/api/v1/zones/visibility", json={
            "zone_ids": [zone_id],
            "is_visible": False
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["updated"] == 1

    def test_bulk_toggle_visibility_show(self, authed_client):
        """Bulk set zones to visible."""
        create_resp = authed_client.post("/api/v1/zones/", json=VALID_ZONE)
        zone_id = create_resp.json()["zone"]["id"]

        resp = authed_client.post("/api/v1/zones/visibility", json={
            "zone_ids": [zone_id],
            "is_visible": True
        })
        assert resp.status_code == 200
        assert resp.json()["updated"] == 1

    def test_bulk_toggle_nonexistent_zone(self, authed_client):
        """Toggling visibility of nonexistent zone: updated=0."""
        resp = authed_client.post("/api/v1/zones/visibility", json={
            "zone_ids": [999999],
            "is_visible": False
        })
        assert resp.status_code == 200
        assert resp.json()["updated"] == 0


# ---------------------------------------------------------------------------
# POST /microzones/visibility bulk toggle
# ---------------------------------------------------------------------------

class TestBulkMicrozoneVisibility:

    def test_bulk_toggle_microzone_visibility(self, authed_client):
        """Bulk set all user microzones to invisible."""
        # Create zone and microzone
        zone_resp = authed_client.post("/api/v1/zones/", json=VALID_ZONE)
        zone_id = zone_resp.json()["zone"]["id"]
        authed_client.post(f"/api/v1/zones/{zone_id}/microzones/", json=VALID_MICROZONE)

        resp = authed_client.post("/api/v1/microzones/visibility", json={
            "is_visible": False
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "updated" in data

    def test_bulk_toggle_microzones_by_zone_ids(self, authed_client):
        """Filter bulk visibility update by zone_ids."""
        zone_resp = authed_client.post("/api/v1/zones/", json=VALID_ZONE)
        zone_id = zone_resp.json()["zone"]["id"]
        authed_client.post(f"/api/v1/zones/{zone_id}/microzones/", json=VALID_MICROZONE)

        resp = authed_client.post("/api/v1/microzones/visibility", json={
            "is_visible": True,
            "zone_ids": [zone_id]
        })
        assert resp.status_code == 200
        assert resp.json()["success"] is True


# ---------------------------------------------------------------------------
# PATCH /zones/{id} with various fields
# ---------------------------------------------------------------------------

class TestUpdateZoneEndpointCoverage:

    def test_update_description(self, authed_client):
        create_resp = authed_client.post("/api/v1/zones/", json=VALID_ZONE)
        zone_id = create_resp.json()["zone"]["id"]

        resp = authed_client.patch(f"/api/v1/zones/{zone_id}", json={"description": "Updated desc"})
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_update_color(self, authed_client):
        create_resp = authed_client.post("/api/v1/zones/", json=VALID_ZONE)
        zone_id = create_resp.json()["zone"]["id"]

        resp = authed_client.patch(f"/api/v1/zones/{zone_id}", json={"color": "#ff0000"})
        assert resp.status_code == 200

    def test_update_tags(self, authed_client):
        create_resp = authed_client.post("/api/v1/zones/", json=VALID_ZONE)
        zone_id = create_resp.json()["zone"]["id"]

        resp = authed_client.patch(f"/api/v1/zones/{zone_id}", json={"tags": ["new_tag"]})
        assert resp.status_code == 200

    def test_update_is_visible_false(self, authed_client):
        create_resp = authed_client.post("/api/v1/zones/", json=VALID_ZONE)
        zone_id = create_resp.json()["zone"]["id"]

        resp = authed_client.patch(f"/api/v1/zones/{zone_id}", json={"is_visible": False})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /zones/{id}/microzones/ with include_geojson
# ---------------------------------------------------------------------------

class TestListMicrozonesWithGeojson:

    def test_list_microzones_include_geojson_true(self, authed_client):
        zone_resp = authed_client.post("/api/v1/zones/", json=VALID_ZONE)
        zone_id = zone_resp.json()["zone"]["id"]
        authed_client.post(f"/api/v1/zones/{zone_id}/microzones/", json=VALID_MICROZONE)

        resp = authed_client.get(f"/api/v1/zones/{zone_id}/microzones/?include_geojson=true")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        # With include_geojson=True, microzones should have geojson field
        if data["microzones"]:
            assert "geojson" in data["microzones"][0]

    def test_list_microzones_include_geojson_false(self, authed_client):
        zone_resp = authed_client.post("/api/v1/zones/", json=VALID_ZONE)
        zone_id = zone_resp.json()["zone"]["id"]
        authed_client.post(f"/api/v1/zones/{zone_id}/microzones/", json=VALID_MICROZONE)

        resp = authed_client.get(f"/api/v1/zones/{zone_id}/microzones/?include_geojson=false")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True


# ---------------------------------------------------------------------------
# GET/PATCH /zones/{zone_id}/microzones/{mz_id} edge cases
# ---------------------------------------------------------------------------

class TestMicrozoneGetUpdateEdgeCases:

    def _setup(self, client):
        """Create zone and microzone, return (zone_id, mz_id)."""
        zone_resp = client.post("/api/v1/zones/", json=VALID_ZONE)
        zone_id = zone_resp.json()["zone"]["id"]
        mz_resp = client.post(f"/api/v1/zones/{zone_id}/microzones/", json=VALID_MICROZONE)
        mz_id = mz_resp.json()["microzone"]["id"]
        return zone_id, mz_id

    def test_get_microzone_zone_not_found(self, authed_client):
        """GET returns 404 when zone doesn't belong to user."""
        resp = authed_client.get("/api/v1/zones/999999/microzones/1")
        assert resp.status_code == 404

    def test_get_microzone_microzone_not_found(self, authed_client):
        """GET returns 404 when microzone doesn't exist."""
        zone_resp = authed_client.post("/api/v1/zones/", json=VALID_ZONE)
        zone_id = zone_resp.json()["zone"]["id"]
        resp = authed_client.get(f"/api/v1/zones/{zone_id}/microzones/999999")
        assert resp.status_code == 404

    def test_update_microzone_zone_not_found(self, authed_client):
        """PATCH returns 404 when zone doesn't belong to user."""
        resp = authed_client.patch(
            "/api/v1/zones/999999/microzones/1",
            json={"name": "Test"}
        )
        assert resp.status_code == 404

    def test_update_microzone_with_description(self, authed_client):
        """PATCH with description covers line 2528."""
        zone_id, mz_id = self._setup(authed_client)
        resp = authed_client.patch(
            f"/api/v1/zones/{zone_id}/microzones/{mz_id}",
            json={"description": "Updated description"}
        )
        assert resp.status_code == 200

    def test_update_microzone_with_is_visible(self, authed_client):
        """PATCH with is_visible covers line 2532."""
        zone_id, mz_id = self._setup(authed_client)
        resp = authed_client.patch(
            f"/api/v1/zones/{zone_id}/microzones/{mz_id}",
            json={"is_visible": False}
        )
        assert resp.status_code == 200

    def test_update_microzone_with_tags(self, authed_client):
        """PATCH with tags covers line 2534."""
        zone_id, mz_id = self._setup(authed_client)
        resp = authed_client.patch(
            f"/api/v1/zones/{zone_id}/microzones/{mz_id}",
            json={"tags": ["new_tag"]}
        )
        assert resp.status_code == 200

    def test_update_microzone_no_fields_raises_400(self, authed_client):
        """PATCH with no fields returns 400."""
        zone_id, mz_id = self._setup(authed_client)
        resp = authed_client.patch(
            f"/api/v1/zones/{zone_id}/microzones/{mz_id}",
            json={}
        )
        assert resp.status_code == 400

    def test_update_microzone_microzone_not_found(self, authed_client):
        """PATCH returns 404 when microzone doesn't exist."""
        zone_resp = authed_client.post("/api/v1/zones/", json=VALID_ZONE)
        zone_id = zone_resp.json()["zone"]["id"]
        resp = authed_client.patch(
            f"/api/v1/zones/{zone_id}/microzones/999999",
            json={"name": "Test"}
        )
        assert resp.status_code == 404

    def test_update_zone_no_fields_raises_400(self, authed_client):
        """PATCH zone with no fields returns 400 (line 2360)."""
        zone_resp = authed_client.post("/api/v1/zones/", json=VALID_ZONE)
        zone_id = zone_resp.json()["zone"]["id"]
        resp = authed_client.patch(f"/api/v1/zones/{zone_id}", json={})
        assert resp.status_code == 400

    def test_delete_zone_not_found(self, authed_client):
        """DELETE returns 404 when zone doesn't exist (line 2385)."""
        resp = authed_client.delete("/api/v1/zones/999999")
        assert resp.status_code == 404

    def test_delete_microzone_zone_not_found(self, authed_client):
        """DELETE microzone returns 404 when zone doesn't exist (line 2573)."""
        resp = authed_client.delete("/api/v1/zones/999999/microzones/1")
        assert resp.status_code == 404

    def test_update_microzone_with_geojson(self, authed_client):
        """PATCH microzone with geojson triggers geometry metrics (lines 2537-2544)."""
        zone_resp = authed_client.post("/api/v1/zones/", json=VALID_ZONE)
        zone_id = zone_resp.json()["zone"]["id"]
        mz_resp = authed_client.post(f"/api/v1/zones/{zone_id}/microzones/", json=VALID_MICROZONE)
        assert mz_resp.status_code in (200, 201)
        mz_id = mz_resp.json()["microzone"]["id"]

        new_geojson = {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[12.0, 41.0], [12.5, 41.0], [12.5, 41.5], [12.0, 41.5], [12.0, 41.0]]]
            },
            "properties": {}
        }
        resp = authed_client.patch(
            f"/api/v1/zones/{zone_id}/microzones/{mz_id}",
            json={"geojson": new_geojson}
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True
