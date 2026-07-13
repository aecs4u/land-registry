from pathlib import Path

from fastapi import FastAPI

from land_registry.consumer import install_land_registry_consumer

ROOT = Path(__file__).resolve().parents[1]


def test_consumer_map_exposes_cadastral_map_contract():
    source = (ROOT / "land_registry/static/consumer-map.js").read_text(encoding="utf-8")

    assert "global.LandRegistryMap = { create: create" in source
    assert "/tiles/cadastral-boundaries/{z}/{x}/{y}.png?layer=map" in source
    assert "/tiles/cadastral-boundaries/{z}/{x}/{y}.png?layer=ple" in source
    assert "/cadastral-identify" in source
    assert "cadastral: { layer: cadastral, sheets: sheets, parcels: parcels }" in source


def test_consumer_package_installer_mounts_asset_and_api():
    app = FastAPI()

    install_land_registry_consumer(app)

    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/land-registry/static" in paths
    assert "/land-registry/api/tiles/cadastral-boundaries/{z}/{x}/{y}.png" in paths
    assert "/land-registry/api/cadastral-identify" in paths
