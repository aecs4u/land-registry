"""Readiness, search outage signalling, info pages, sign-out, caching and rate limits."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from land_registry.main import app
from land_registry.rate_limit import RateLimitMiddleware
from land_registry.tile_cache import TileCache


@pytest.fixture
def client():
    return TestClient(app)


def _source(available=True, layers=None, health_error=None):
    source = MagicMock()
    source.available = available
    if health_error:
        source.health = AsyncMock(side_effect=health_error)
    else:
        source.health = AsyncMock(return_value=layers or [])
    return source


# --- /ready -----------------------------------------------------------------


def test_health_stays_liveness_only(client):
    with patch("land_registry.main.get_map_layer_source", return_value=_source(available=False)):
        assert client.get("/health").status_code == 200


def test_ready_503_when_source_not_configured(client):
    with patch("land_registry.main.get_map_layer_source", return_value=_source(available=False)):
        response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"
    assert response.headers["cache-control"] == "no-store"


def test_ready_503_when_source_unreachable(client):
    with patch("land_registry.main.get_map_layer_source", return_value=_source(health_error=ConnectionError("db down"))):
        assert client.get("/ready").status_code == 503


def test_ready_503_when_no_layer_available(client):
    layers = [{"id": "a", "available": False}, {"id": "b", "available": False}]
    with patch("land_registry.main.get_map_layer_source", return_value=_source(layers=layers)):
        assert client.get("/ready").status_code == 503


def test_ready_200_and_lists_missing_layers_when_partly_available(client):
    layers = [{"id": "a", "available": True}, {"id": "b", "available": False}]
    with patch("land_registry.main.get_map_layer_source", return_value=_source(layers=layers)):
        response = client.get("/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["layers_available"] == 1
    assert body["layers_unavailable"] == ["b"]


# --- search -----------------------------------------------------------------


def test_search_503_instead_of_empty_200_when_source_down(client):
    with patch("land_registry.routers.api.get_map_layer_source", return_value=_source(available=False)):
        response = client.get("/api/v1/map/search?query=Cesena")
    assert response.status_code == 503
    assert response.headers["retry-after"]


# --- info pages ---------------------------------------------------------------


@pytest.mark.parametrize("path", ["/privacy", "/terms", "/help", "/contact", "/notifications"])
def test_footer_pages_exist(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert "<h1>" in response.text


def test_contact_email_comes_from_environment(client, monkeypatch):
    monkeypatch.setenv("LEGAL_CONTACT_EMAIL", "privacy@example.test")
    assert "privacy@example.test" in client.get("/contact").text


# --- sign-out -----------------------------------------------------------------


def test_get_logout_does_not_end_the_session(client):
    response = client.get("/auth/logout", follow_redirects=False)
    assert response.status_code == 200
    assert 'action="/auth/signout"' in response.text


def test_post_signout_redirects_to_login(client):
    response = client.post("/auth/signout", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login"


# --- static caching -------------------------------------------------------------


def test_versioned_static_asset_is_immutable_and_unversioned_revalidates(client):
    versioned = client.get("/static/map-v2.js?v=abc")
    assert versioned.status_code == 200
    assert "immutable" in versioned.headers["cache-control"]
    assert client.get("/static/map-v2.js").headers["cache-control"] == "no-cache"


# --- tile cache -----------------------------------------------------------------


def test_tile_cache_serves_repeats_and_shares_inflight_fetches():
    async def scenario():
        cache = TileCache(max_bytes=1024, ttl_seconds=60)
        calls = 0

        async def fetch():
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.01)
            return b"tile"

        first = await asyncio.gather(*(cache.get_or_fetch("k", fetch) for _ in range(5)))
        again = await cache.get_or_fetch("k", fetch)
        return calls, first, again

    calls, first, again = asyncio.run(scenario())
    assert calls == 1
    assert set(first) == {b"tile"} and again == b"tile"


def test_tile_cache_does_not_cache_failures_and_evicts_by_size():
    async def scenario():
        cache = TileCache(max_bytes=100, ttl_seconds=60)

        async def boom():
            raise RuntimeError("db")

        with pytest.raises(RuntimeError):
            await cache.get_or_fetch("bad", boom)
        assert await cache.get_or_fetch("bad", AsyncMock(return_value=b"ok")) == b"ok"
        for i in range(10):
            await cache.get_or_fetch(i, AsyncMock(return_value=b"x" * 20))
        return cache.size_bytes

    assert asyncio.run(scenario()) <= 100


def test_tile_cache_expires_entries(monkeypatch):
    async def scenario():
        cache = TileCache(ttl_seconds=0)
        fetch = AsyncMock(side_effect=[b"one", b"two"])
        first = await cache.get_or_fetch("k", fetch)
        await asyncio.sleep(0.01)
        return first, await cache.get_or_fetch("k", fetch)

    assert asyncio.run(scenario()) == (b"one", b"two")


# --- rate limiting --------------------------------------------------------------


def _limited_app(monkeypatch):
    sent = []

    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = RateLimitMiddleware(inner)

    async def call(path, ip="1.1.1.1"):
        messages = []

        async def send(message):
            messages.append(message)

        scope = {"type": "http", "method": "GET", "path": path, "headers": [(b"x-forwarded-for", ip.encode())]}
        await middleware(scope, None, send)
        return messages[0]["status"]

    return call


def test_search_is_rate_limited_per_client_and_tiles_are_not(monkeypatch):
    call = _limited_app(monkeypatch)

    async def scenario():
        statuses = [await call("/api/v1/map/search") for _ in range(45)]
        other_client = await call("/api/v1/map/search", ip="2.2.2.2")
        tiles = [await call("/api/v1/tiles/map-layers/x/1/1/1.pbf") for _ in range(500)]
        return statuses, other_client, set(tiles)

    statuses, other_client, tiles = asyncio.run(scenario())
    assert statuses[:40] == [200] * 40
    assert 429 in statuses[40:]
    assert other_client == 200
    assert tiles == {200}


def test_rate_limit_can_be_disabled(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "0")
    call = _limited_app(monkeypatch)
    assert {asyncio.run(call("/api/v1/map/search")) for _ in range(60)} == {200}


# --- SQL migration runner ---------------------------------------------------------


def test_map_sql_runner_covers_every_listed_script_and_splits_statements():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "apply_map_sql.py"
    spec = importlib.util.spec_from_file_location("apply_map_sql", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    plan = module.load_plan()
    assert [name for name, _ in plan] == list(module.ORDER)
    for name, statements in plan:
        assert statements, name
        assert all(s.rstrip().endswith(";") for s in statements), name
    # CONCURRENTLY statements must be sent alone, never batched with another.
    assert any("CONCURRENTLY" in s for _, statements in plan for s in statements)
    assert module.split_statements("-- c\nSELECT 1; -- x\nSELECT 2;") == ["SELECT 1;", "SELECT 2;"]


# --- per-session map state ---------------------------------------------------------


def test_map_state_is_isolated_per_session_and_defaults_outside_requests():
    from land_registry.dependencies import SessionScopedMapState, _request_session

    state = SessionScopedMapState()
    state.set_layers({"shared": 1})  # outside a request: shared default state
    alice, bob = {}, {}

    token = _request_session.set(alice)
    state.set_layers({"alice": 1})
    _request_session.reset(token)

    token = _request_session.set(bob)
    assert state.get_layers() == {"shared": 1}  # no data of its own yet: not Alice's
    state.set_layers({"bob": 2})
    _request_session.reset(token)

    for session, expected in ((alice, {"alice": 1}), (bob, {"bob": 2})):
        token = _request_session.set(session)
        assert state.get_layers() == expected
        _request_session.reset(token)
    assert state.get_layers() == {"shared": 1}


def test_map_state_evicts_oldest_session_and_evicted_session_reads_empty():
    from land_registry.dependencies import SessionScopedMapState, _request_session

    state = SessionScopedMapState()
    state.MAX_SESSIONS = 2
    sessions = [{} for _ in range(3)]
    for index, session in enumerate(sessions):
        token = _request_session.set(session)
        state.set_layers({"n": index})
        _request_session.reset(token)
    token = _request_session.set(sessions[0])
    assert state.get_layers() == {}  # evicted: reads the (empty) default, never another session's data
    _request_session.reset(token)
    token = _request_session.set(sessions[2])
    assert state.get_layers() == {"n": 2}
    _request_session.reset(token)


def test_two_browsers_do_not_share_loaded_layers(client):
    from land_registry.dependencies import _map_state

    other = TestClient(app)
    # Each client gets its own session cookie from the first data-loading API call.
    client.get("/api/v1/get-cadastral-structure/")
    other.get("/api/v1/get-cadastral-structure/")
    cookies = {c.name: c.value for c in client.cookies.jar}
    assert cookies, "an API call that can load data must establish a session cookie"
    assert client.cookies.get("aecs4u_session") != other.cookies.get("aecs4u_session")


def test_tile_responses_never_set_a_cookie(client):
    response = client.get("/api/v1/tiles/map-layers/cadastral-parcels/1/0/0.pbf")
    assert "set-cookie" not in response.headers
