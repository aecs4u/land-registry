from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_docker_runtime_matches_supported_python_version():
    for filename in ("Dockerfile", "Dockerfile.template"):
        dockerfile = (ROOT / filename).read_text(encoding="utf-8")
        assert "FROM python:3.12-slim" in dockerfile
        assert "FROM python:3.11-slim" not in dockerfile
