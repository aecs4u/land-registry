from pathlib import Path

from land_registry.routers.api import _resolve_local_cadastral_path


def test_local_loader_accepts_s3_rooted_path_when_root_is_italia(tmp_path: Path) -> None:
    root = tmp_path / "catasto" / "ITALIA"
    expected = root / "SICILIA" / "PA" / "G273_PALERMO" / "G273_PALERMO_ple.fgb"

    assert _resolve_local_cadastral_path(
        "ITALIA/SICILIA/PA/G273_PALERMO/G273_PALERMO_ple.fgb", str(root)
    ) == expected


def test_local_loader_accepts_path_without_s3_prefix(tmp_path: Path) -> None:
    root = tmp_path / "catasto" / "ITALIA"

    assert _resolve_local_cadastral_path(
        "SICILIA/PA/G273_PALERMO/G273_PALERMO_ple.fgb", str(root)
    ) == root / "SICILIA" / "PA" / "G273_PALERMO" / "G273_PALERMO_ple.fgb"
