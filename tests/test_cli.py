import json

from land_registry import cli


class _FakeCadastralData:
    def __init__(self):
        self.source = "json"
        self.stats = {
            "total_regions": 1,
            "total_provinces": 2,
            "total_municipalities": 3,
            "total_files": 4,
        }

    def cache_metadata(self):
        return {"age_seconds": 0.0, "source": "json", "is_expired": False}

    def get_file_availability_stats(self):
        return {"municipalities_with_files": 3, "coverage_percentage": 100.0}


def test_serve_invokes_uvicorn(monkeypatch):
    captured = {}

    def _fake_run(import_string, **kwargs):
        captured["import_string"] = import_string
        captured["kwargs"] = kwargs

    monkeypatch.setattr(cli.uvicorn, "run", _fake_run)

    exit_code = cli.main(
        [
            "serve",
            "--target",
            "main",
            "--host",
            "127.0.0.1",
            "--port",
            "9001",
            "--no-reload",
            "--log-level",
            "debug",
        ]
    )

    assert exit_code == 0
    assert captured["import_string"] == "land_registry.main:app"
    assert captured["kwargs"]["host"] == "127.0.0.1"
    assert captured["kwargs"]["port"] == 9001
    assert captured["kwargs"]["reload"] is False
    assert captured["kwargs"]["log_level"] == "debug"


def test_info_json_output(capsys):
    exit_code = cli.main(["info", "--json"])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert "app" in payload
    assert "server" in payload
    assert "panel" in payload


def test_cadastral_stats_json_output(monkeypatch, capsys):
    calls = {"use_cache": None}

    def _fake_load(use_cache=True):
        calls["use_cache"] = use_cache
        return _FakeCadastralData()

    monkeypatch.setattr(cli, "load_cadastral_structure", _fake_load)

    exit_code = cli.main(["cadastral-stats", "--fresh", "--json"])

    assert exit_code == 0
    assert calls["use_cache"] is False
    payload = json.loads(capsys.readouterr().out)
    assert payload["source"] == "json"
    assert payload["stats"]["total_regions"] == 1


def test_cache_clear_defaults_to_all(monkeypatch, capsys):
    cleared = {"cad": 0, "db": 0}

    def _fake_clear_cache():
        cleared["cad"] += 1

    def _fake_db_clear_cache():
        cleared["db"] += 1

    monkeypatch.setattr(cli, "clear_cache", _fake_clear_cache)
    monkeypatch.setattr(cli.file_availability_db, "clear_cache", _fake_db_clear_cache)

    exit_code = cli.main(["cache", "clear"])

    assert exit_code == 0
    assert cleared["cad"] == 1
    assert cleared["db"] == 1
    assert "Cleared:" in capsys.readouterr().out


def test_routes_command(monkeypatch, capsys):
    class _FakeRoute:
        methods = {"GET"}
        path = "/health"
        name = "health"

    class _FakeApp:
        routes = [_FakeRoute()]

    monkeypatch.setattr(cli, "_load_fastapi_app", lambda _module: _FakeApp())

    exit_code = cli.main(["routes", "--target", "main"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "/health" in output
    assert "GET" in output


def test_routes_command_supports_mount_routes(monkeypatch, capsys):
    class _MountLikeRoute:
        path = "/static"
        name = "static"

    class _FakeApp:
        routes = [_MountLikeRoute()]

    monkeypatch.setattr(cli, "_load_fastapi_app", lambda _module: _FakeApp())

    exit_code = cli.main(["routes", "--target", "main"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "/static" in output
