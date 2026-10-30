"""Sign-in and registration pages must render, whatever theme release is installed."""

import pytest
from fastapi.testclient import TestClient

from land_registry.main import _HtmlScriptPolicyMiddleware, app


@pytest.mark.parametrize("path", ["/auth/login", "/auth/register"])
def test_auth_pages_render(path):
    response = TestClient(app).get(path)
    assert response.status_code == 200
    # The override links the app's own stylesheet that defines the button classes.
    assert "/static/css/auth.css" in response.text
    assert "Traceback" not in response.text


def test_missing_theme_template_is_not_a_debug_traceback(monkeypatch):
    from land_registry.routers import auth_pages

    monkeypatch.setitem(auth_pages._templates.env.globals, "theme_auth_login_parent", "aecs4u_theme/auth/nope.html")
    response = TestClient(app, raise_server_exceptions=False).get("/auth/login")
    assert response.status_code == 503
    assert "Traceback" not in response.text
    assert "/mnt/" not in response.text


@pytest.mark.parametrize("path", [
    "/map",
    "/map-legacy",
    "/map/embed",
    "/omi/valuation",
    "/profile",
    "/settings",
    "/privacy",
    "/terms",
    "/help",
    "/contact",
    "/notifications",
    "/data-sources",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/map_table",
    "/adjacency_table",
    "/mapping_table",
    "/cadastral-data",
    "/api/v1/map/layers/market-zones/1/sheet",
])
def test_private_html_routes_redirect_signed_out_visitors(path):
    response = TestClient(app).get(path, follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].startswith("/auth/login?next=")


def test_private_api_views_reject_signed_out_visitors():
    client = TestClient(app)
    poi_map = client.get("/api/v1/enrichment/poi-map?lat=41.9&lng=12.5")
    uploaded_map = client.post(
        "/api/v1/generate-map/",
        files={"file": ("parcel.gpkg", b"not processed without a session", "application/octet-stream")},
    )
    assert poi_map.status_code == 401
    assert uploaded_map.status_code == 401


def test_landing_and_auth_flows_remain_public():
    client = TestClient(app)
    assert client.get("/landing").status_code == 200
    assert client.get("/auth/login").status_code == 200
    assert client.get("/auth/register").status_code == 200


def test_html_api_views_for_poi_and_uploaded_maps_require_auth():
    client = TestClient(app)
    assert client.get("/api/v1/enrichment/poi-map?lat=41.9&lng=12.5").status_code == 401
    response = client.post(
        "/api/v1/generate-map/",
        files={"file": ("sample.qpkg", b"not a real archive", "application/octet-stream")},
    )
    assert response.status_code == 401


def test_map_table_renders_readable_fallback_when_panel_is_unavailable(monkeypatch):
    from types import SimpleNamespace

    from land_registry import main
    from land_registry.routers.auth import get_current_user_optional

    async def unavailable():
        return False

    monkeypatch.setitem(
        main.app.dependency_overrides,
        get_current_user_optional,
        lambda: SimpleNamespace(id="panel-fallback-test"),
    )
    monkeypatch.setattr(main, "_panel_embed_available", unavailable)
    client = TestClient(main.app)
    client.cookies.set("lang", "en")
    response = client.get("/map_table")
    assert response.status_code == 200
    assert "The table service did not connect" in response.text
    assert 'href="/map?panel=table&amp;notice=table"' in response.text
    assert "Open the map attribute table" in response.text
    assert '[class*="bk-panel-models-layout-"]' in response.text
    script_src = next(
        part.strip() for part in response.headers["content-security-policy"].split(";")
        if part.strip().startswith("script-src ")
    )
    assert "'unsafe-eval'" not in script_src


@pytest.mark.parametrize("path", ["/map_table", "/adjacency_table", "/mapping_table"])
def test_panel_eval_is_allowed_only_for_live_authenticated_table_embed(monkeypatch, path):
    from types import SimpleNamespace

    from land_registry import main
    from land_registry.routers.auth import get_current_user_optional

    async def available():
        return True

    monkeypatch.setitem(
        main.app.dependency_overrides,
        get_current_user_optional,
        lambda: SimpleNamespace(id="panel-csp-test"),
    )
    monkeypatch.setattr(main, "_panel_embed_available", available)
    monkeypatch.setattr(
        main,
        "server_document",
        lambda _url: (
            '<script>const panelUrl = '
            '"http://127.0.0.1:5006/dashboard/autoload.js?bokeh-autoload-element=1";'
            '</script>'
        ),
    )

    client = TestClient(main.app)
    client.cookies.set("lang", "en")
    response = client.get(path)
    assert response.status_code == 200
    assert "/autoload.js" in response.text
    script_src = next(
        part.strip() for part in response.headers["content-security-policy"].split(";")
        if part.strip().startswith("script-src ")
    )
    assert "'unsafe-eval'" in script_src
    assert "'unsafe-inline'" not in script_src

    landing = client.get("/landing")
    landing_script_src = next(
        part.strip() for part in landing.headers["content-security-policy"].split(";")
        if part.strip().startswith("script-src ")
    )
    assert "'unsafe-eval'" not in landing_script_src


@pytest.mark.parametrize(("path", "description"), [
    ("/map_table", "Explore cadastral parcels in an interactive attribute table."),
    ("/adjacency_table", "Review parcels adjacent to the selected cadastral parcel."),
    ("/mapping_table", "Use drawing and zoning tools with cadastral map data."),
])
def test_panel_table_pages_have_descriptions(monkeypatch, path, description):
    from types import SimpleNamespace

    from land_registry import main
    from land_registry.routers.auth import get_current_user_optional

    async def unavailable():
        return False

    monkeypatch.setitem(
        main.app.dependency_overrides,
        get_current_user_optional,
        lambda: SimpleNamespace(id="panel-description-test"),
    )
    monkeypatch.setattr(main, "_panel_embed_available", unavailable)
    client = TestClient(main.app)
    client.cookies.set("lang", "en")
    response = client.get(path)
    assert response.status_code == 200
    assert f'<meta name="description" content="{description}"' in response.text


def test_html_script_policy_uses_nonces_and_exact_event_handler_hashes():
    import asyncio
    import base64
    import gzip
    import hashlib

    async def exercise():
        original_policy = b"default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval' https://example.test; style-src 'self' 'unsafe-inline'"
        body = b'<button onclick="toggleDarkMode()">Theme</button><script>window.ready = true;</script><script src="/static/app.js"></script>'
        encoded = gzip.compress(body)
        captured = []

        async def inner(scope, receive, send):
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"text/html; charset=utf-8"),
                    (b"content-encoding", b"gzip"),
                    (b"content-length", str(len(encoded)).encode()),
                    (b"content-security-policy", original_policy),
                ],
            })
            await send({"type": "http.response.body", "body": encoded, "more_body": False})

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            captured.append(message)

        scope = {"type": "http", "path": "/map-legacy", "state": {}}
        await _HtmlScriptPolicyMiddleware(inner)(scope, receive, send)
        headers = dict(captured[0]["headers"])
        policy = headers[b"content-security-policy"].decode("latin-1")
        script_src = next(part for part in policy.split(";") if part.strip().startswith("script-src"))
        script_attr = next(part for part in policy.split(";") if part.strip().startswith("script-src-attr"))
        style_src = next(part for part in policy.split(";") if part.strip().startswith("style-src"))
        rewritten = gzip.decompress(captured[1]["body"])
        handler_hash = "'sha256-" + base64.b64encode(hashlib.sha256(b"toggleDarkMode()").digest()).decode() + "'"
        assert scope["state"]["csp_nonce"] in script_src
        assert "'unsafe-inline'" not in script_src
        assert "'unsafe-eval'" not in script_src
        assert "'unsafe-hashes'" in script_attr
        assert handler_hash in script_attr
        assert "'unsafe-inline'" in style_src
        assert b'<script nonce="' + scope["state"]["csp_nonce"].encode() + b'">' in rewritten
        assert b'<script src="/static/app.js"></script>' in rewritten
        assert b"unsafe-inline" not in script_src.encode()
        assert int(headers[b"content-length"]) == len(captured[1]["body"])

        captured.clear()
        other_scope = {"type": "http", "path": "/landing", "state": {}}
        await _HtmlScriptPolicyMiddleware(inner)(other_scope, receive, send)
        other_headers = dict(captured[0]["headers"])
        other_policy = other_headers[b"content-security-policy"].decode("latin-1")
        assert "'unsafe-inline'" not in next(part for part in other_policy.split(";") if part.strip().startswith("script-src"))
        assert other_scope["state"]["csp_nonce"] in other_policy

    asyncio.run(exercise())


def test_production_script_policy_removes_loopback_panel_origins(monkeypatch):
    from land_registry import main

    monkeypatch.setattr(main.app_settings, "debug", False)
    policy = (
        b"default-src 'self'; script-src 'self' http://127.0.0.1:5006; "
        b"connect-src 'self' http://127.0.0.1:5006 ws://localhost:5006"
    )
    rewritten = main._HtmlScriptPolicyMiddleware._strict_script_policy(policy, "fresh-nonce", set())
    assert "127.0.0.1:5006" not in rewritten
    assert "localhost:5006" not in rewritten
    assert "'unsafe-inline'" not in rewritten
    assert "'unsafe-eval'" not in rewritten
    table_policy = main._HtmlScriptPolicyMiddleware._strict_script_policy(
        policy, "fresh-nonce", set(), allow_panel_eval=True
    )
    assert "'unsafe-eval'" in next(
        part.strip() for part in table_policy.split(";") if part.strip().startswith("script-src ")
    )
