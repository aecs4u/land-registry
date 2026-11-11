import pytest

from land_registry import metric_views


def test_dsn_points_at_istat_from_stats_server_settings(monkeypatch):
    monkeypatch.delenv("ISTAT_POSTGRES_DSN", raising=False)
    monkeypatch.delenv("AECS4U_SOURCE_ISTAT_DATABASE_URL", raising=False)
    monkeypatch.setenv("STATS_POSTGRES_DSN", "postgresql+asyncpg://u:p@db.example:5432/aecs4u-stats?sslmode=require")
    dsn = metric_views.istat_dsn()
    assert dsn == "postgresql://u:p@db.example:5432/istat?sslmode=require"


def test_explicit_istat_dsn_wins_and_must_name_the_istat_database(monkeypatch):
    monkeypatch.setenv("ISTAT_POSTGRES_DSN", "postgresql://u@h/istat")
    assert metric_views.istat_dsn() == "postgresql://u@h/istat"
    monkeypatch.setenv("ISTAT_POSTGRES_DSN", "postgresql://u@h/hazards")
    with pytest.raises(ValueError):
        metric_views.istat_dsn()


def test_missing_configuration_is_reported(monkeypatch):
    for name in ("ISTAT_POSTGRES_DSN", "AECS4U_SOURCE_ISTAT_DATABASE_URL", "STATS_POSTGRES_DSN", "AECS4U_STATS_POSTGRES_DSN"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError):
        metric_views.istat_dsn()


def test_main_returns_nonzero_when_refresh_fails(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(metric_views, "refresh", boom)
    assert metric_views.main() == 1
    monkeypatch.setattr(metric_views, "refresh", lambda *a, **k: {"mv_solar_potential_comuni": 1})
    assert metric_views.main() == 0
