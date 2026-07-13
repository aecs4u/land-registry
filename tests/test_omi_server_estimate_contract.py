"""Contracts for the report-ready server-side OMI estimate."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from land_registry import stats_service
from land_registry.routers import enrichment as enrichment_module


ROOT = Path(__file__).resolve().parents[1]
PANEL_SOURCE = (ROOT / "land_registry" / "static" / "parcel-enrichment.js").read_text(
    encoding="utf-8"
)


@pytest.fixture
def omi_rows() -> list[dict]:
    return [
        {
            "anno": 2025,
            "semestre": 2,
            "zona": "B1",
            "cod_tipologia": "20",
            "tipologia": "Abitazioni civili",
            "stato_conservazione": "Normale",
            "prezzo_min": 1400,
            "prezzo_max": 1900,
        },
        {
            "anno": 2025,
            "semestre": 2,
            "zona": "B1",
            "cod_tipologia": "20",
            "tipologia": "Abitazioni civili",
            "stato_conservazione": "Ottimo",
            "prezzo_min": 1700,
            "prezzo_max": 2300,
        },
    ]


def test_estimate_selects_one_exact_quote_and_returns_versioned_range(monkeypatch, omi_rows) -> None:
    monkeypatch.setattr(stats_service, "quotes_for_comune", lambda comune, zona=None: omi_rows)

    result = stats_service.estimate_omi_value(
        comune="C773",
        zona="b1",
        cod_tipologia="20",
        stato_conservazione="normale",
        area_sqm=100,
    )

    assert result is not None
    assert result["methodology"] == "omi-area-range-v1"
    assert result["value_range_eur"] == {"min": 140000.0, "max": 190000.0}
    assert result["quote"]["anno"] == 2025
    assert "Non è una perizia" in result["disclaimer"]


def test_estimate_rejects_ambiguous_or_invalid_inputs(monkeypatch, omi_rows) -> None:
    monkeypatch.setattr(stats_service, "quotes_for_comune", lambda comune, zona=None: omi_rows)

    assert stats_service.estimate_omi_value("C773", "B1", "20", 100) is None
    assert stats_service.estimate_omi_value("C773", "B1", "20", -1, "Normale") is None


@pytest.mark.asyncio
async def test_estimate_endpoint_dispatches_validated_request(monkeypatch) -> None:
    expected = {"methodology": "omi-area-range-v1", "value_range_eur": {"min": 1, "max": 2}}
    monkeypatch.setattr(enrichment_module.stats_service, "omi_db_available_public", lambda: True)
    monkeypatch.setattr(enrichment_module.stats_service, "estimate_omi_value", lambda **kwargs: expected)
    request = enrichment_module.OmiEstimateRequest(
        comune="C773",
        zona="B1",
        cod_tipologia="20",
        stato_conservazione="Normale",
        area_sqm=100,
    )

    assert await enrichment_module.estimate_omi_value(request) == expected

    with pytest.raises(ValidationError):
        enrichment_module.OmiEstimateRequest(
            comune="C773", zona="B1", cod_tipologia="20", area_sqm=0
        )


def test_panel_uses_server_contract_with_local_fallback() -> None:
    assert "/api/v1/enrichment/omi/estimate" in PANEL_SOURCE
    assert "function _postJson" in PANEL_SOURCE
    assert "scheduleServerEstimate" in PANEL_SOURCE
    assert "Calcolo verificato dal server" in PANEL_SOURCE
    assert "Anteprima locale" in PANEL_SOURCE
