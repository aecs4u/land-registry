"""Compatibility tests for optional aecs4u-stats features."""

import builtins
import runpy
from pathlib import Path

from land_registry import stats_service


def test_missing_optional_stats_subpackages_degrade_gracefully() -> None:
    """The base package remains usable without newer enrichment datasets."""
    assert isinstance(stats_service.cadastral_store_available(), bool)
    assert isinstance(stats_service.get_fogli("H501")["fogli"], list)
    assert isinstance(stats_service.zone_boundaries_available(), bool)


def test_missing_census_subpackage_does_not_break_stats_service(monkeypatch) -> None:
    """Older aecs4u-stats releases must not prevent application startup."""
    real_import = builtins.__import__

    def import_without_census(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "aecs4u_stats.census":
            error = ModuleNotFoundError("No module named 'aecs4u_stats.census'")
            error.name = name
            raise error
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", import_without_census)
    namespace = runpy.run_path(Path(stats_service.__file__))

    assert namespace["census_db_available"]() is False
    assert namespace["get_census_sections"]("H501") is None
    assert namespace["get_census_section_at_point"](41.9, 12.5) is None
