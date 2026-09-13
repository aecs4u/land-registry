"""Account profile/settings pages and per-user preferences."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from land_registry.main import app
from land_registry.models import UserPreferences
from land_registry.routers import api as api_router
from land_registry.routers.auth import get_current_user, get_current_user_optional
from land_registry.sqlite_db import SQLiteDatabase

ROOT = Path(__file__).resolve().parents[1]
USER = SimpleNamespace(
    id="user-account-1",
    email="owner@example.com",
    first_name="Ada",
    last_name="Rossi",
    full_name="Ada Rossi",
    image_url=None,
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = SQLiteDatabase(db_path=str(tmp_path / "account.sqlite"))
    monkeypatch.setattr(api_router, "get_sqlite_db", lambda: db)
    monkeypatch.setattr(api_router, "_saved_parcels_use_postgres", lambda: False)
    app.dependency_overrides[get_current_user] = lambda: USER
    app.dependency_overrides[get_current_user_optional] = lambda: USER
    test_client = TestClient(app)
    test_client.cookies.set("lang", "en")
    try:
        yield test_client, db
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_current_user_optional, None)


def test_preferences_model_defaults_and_validation():
    defaults = UserPreferences()
    assert defaults.default_basemap == "light"
    assert defaults.start_view == "italy"
    assert defaults.default_layers == ["cadastral-parcels"]
    assert defaults.parcel_labels is True

    assert UserPreferences(default_layers=["a", " a ", "", "b"]).default_layers == ["a", "b"]
    with pytest.raises(ValidationError):
        UserPreferences(default_basemap="terrain")
    with pytest.raises(ValidationError):
        UserPreferences(start_view="anywhere")


def test_preferences_round_trip_keeps_unrelated_stored_keys(client):
    test_client, db = client
    db.save_user_preferences(USER.id, {"owned_by_another_feature": 1})

    response = test_client.put(
        "/api/v1/user/preferences",
        json={
            "language": "en",
            "default_basemap": "satellite",
            "start_view": "last",
            "default_layers": ["cadastral-parcels", "hazard-areas"],
            "parcel_labels": False,
        },
    )
    assert response.status_code == 200

    loaded = test_client.get("/api/v1/user/preferences").json()["preferences"]
    assert loaded["default_basemap"] == "satellite"
    assert loaded["start_view"] == "last"
    assert loaded["default_layers"] == ["cadastral-parcels", "hazard-areas"]
    assert loaded["parcel_labels"] is False
    assert db.get_user_preferences(USER.id)["owned_by_another_feature"] == 1


def test_preferences_reject_layers_outside_the_catalog(client):
    test_client, _ = client
    response = test_client.put("/api/v1/user/preferences", json={"default_layers": ["not-a-layer"]})
    assert response.status_code == 422


def test_invalid_stored_preferences_fall_back_to_defaults(client):
    test_client, db = client
    db.save_user_preferences(USER.id, {"default_basemap": "retro"})
    loaded = test_client.get("/api/v1/user/preferences").json()["preferences"]
    assert loaded["default_basemap"] == "light"


def test_export_downloads_saved_parcels_and_preferences(client):
    test_client, _ = client
    response = test_client.get("/api/v1/user/export")
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment;")
    payload = response.json()
    assert payload["user"]["id"] == USER.id
    assert payload["saved_parcels"] == []
    assert payload["preferences"]["default_basemap"] == "light"


@pytest.mark.parametrize("path", ["/profile", "/settings"])
def test_account_pages_send_signed_out_visitors_to_login(client, path):
    test_client, _ = client
    app.dependency_overrides[get_current_user_optional] = lambda: None
    response = test_client.get(path, follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == f"/auth/login?next={path}"


def test_settings_page_shows_only_backed_controls(client):
    pytest.importorskip("aecs4u_theme")
    test_client, _ = client
    html = test_client.get("/settings").text

    assert 'data-pref="default_basemap"' in html
    assert 'data-pref="start_view"' in html
    assert 'id="prefLayer-cadastral-parcels"' in html
    assert 'id="prefLayer-geo-boundaries"' not in html
    assert 'href="/api/v1/user/export"' in html
    assert "/static/account-settings.js" in html
    # Demo-only theme content with no backing in this app must not render.
    assert "iPhone 15 Pro" not in html
    assert "/dashboard/api-keys" not in html


def test_profile_page_summarises_the_shortlist(client, monkeypatch):
    pytest.importorskip("aecs4u_theme")
    test_client, _ = client

    async def rows(_user_id):
        return [
            {
                "id": 7,
                "source": "cadastral",
                "national_reference": "G273_0010A0.2319",
                "label": "Mondello plot",
                "status": None,
                "priority": 2,
                "tags": "[]",
                "created_at": "2026-09-12 10:00:00",
                "updated_at": "2026-09-13T09:30:00Z",
            }
        ]

    monkeypatch.setattr(api_router, "_saved_parcel_rows", rows)
    html = test_client.get("/profile").text

    assert "Mondello plot" in html
    assert "/map?parcel=G273_0010A0.2319" in html
    assert "2026-09-13" in html
    assert "Enter VAT or fiscal code" not in html


def test_direct_map_applies_saved_preferences():
    script = (ROOT / "land_registry/static/map-v2.js").read_text(encoding="utf-8")
    template = (ROOT / "land_registry/templates/map_v2.html").read_text(encoding="utf-8")

    assert "fetch('/api/v1/user/preferences'" in script
    assert "LAST_VIEW_KEY" in script
    assert "getCurrentPosition" in script
    assert "state.defaultLayers.has(layer.id)" in script
    assert "parcel_labels === false" in script
    assert 'href="/settings"' in template
