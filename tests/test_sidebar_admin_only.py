"""The theme's left sidebar is shown to admin users only."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from land_registry.main import _sidebar_enabled, app
from land_registry.routers.auth import get_current_user, get_current_user_optional

CUSTOMER = SimpleNamespace(id="customer-1", email="customer@example.com", roles=["user"])
ADMIN = SimpleNamespace(id="admin-1", email="admin@example.com", roles=["admin"])
SUPERUSER = SimpleNamespace(id="root-1", email="root@example.com", is_superuser=True)


def _client_for(user):
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_user_optional] = lambda: user
    client = TestClient(app)
    client.cookies.set("lang", "en")
    return client


@pytest.fixture(autouse=True)
def _reset_overrides():
    yield
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_current_user_optional, None)


def test_sidebar_flag_by_user():
    assert _sidebar_enabled(None) is False
    assert _sidebar_enabled(CUSTOMER) is False
    assert _sidebar_enabled(ADMIN) is True
    assert _sidebar_enabled(SUPERUSER) is True


@pytest.mark.parametrize("user, expected", [(None, False), (CUSTOMER, False), (ADMIN, True)])
def test_map_page_renders_sidebar_for_admins_only(user, expected):
    html = _client_for(user).get("/map").text
    assert ('id="sidebar"' in html) is expected
    assert ('class="sidebar-visible"' in html or 'class="sidebar-visible ' in html) is expected
    assert ("sidebar-hidden" in html) is not expected


@pytest.mark.parametrize("user, expected", [(CUSTOMER, False), (ADMIN, True)])
def test_account_page_renders_sidebar_for_admins_only(user, expected):
    html = _client_for(user).get("/settings").text
    assert ('id="sidebar"' in html) is expected
