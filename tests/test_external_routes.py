import pytest

from land_registry.routers import enrichment as enrichment_module


@pytest.mark.asyncio
async def test_pvp_route_returns_json_when_lookup_fails(monkeypatch):
    def failed_lookup(reference, municipality=None):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(enrichment_module.stats_service, "get_pvp_for_parcel", failed_lookup)

    result = await enrichment_module.get_parcel_pvp(
        municipality_code="28032",
        sheet="001800",
        parcel="1036",
    )

    assert result["available"] is False
    assert result["source"] == "PVP modelview PostgreSQL"
    assert result["error"] == "lookup_failed"


@pytest.mark.asyncio
async def test_external_route_forwards_explicit_municipality(monkeypatch):
    captured = {}

    def lookup(reference, municipality=None):
        captured["reference"] = reference
        captured["municipality"] = municipality
        return {"records": [], "count": 0, "available": False}

    monkeypatch.setattr(enrichment_module.stats_service, "get_pvp_for_parcel", lookup)

    await enrichment_module.get_parcel_pvp(
        municipality_code="28032",
        sheet="6",
        parcel="543",
    )

    assert captured == {
        "reference": "28032_6.543",
        "municipality": {"pvp_municipality_code": "28032"},
    }
