"""Inventory of parcel detail blocks that carry data.

The parcel read model declares a long list of blocks (``_PARCEL_DETAIL_BLOCKS``)
but only some are filled by ``_build_parcel_enrichment``. This test pins that
split with every external source stubbed, so a change in coverage is a visible,
deliberate edit instead of a silent one. See
``docs/PARCEL_PANEL_GAP_ANALYSIS_2026-10.md`` (Phase 0 and Phase 3).
"""

from land_registry import stats_service

REFERENCE = "H501_048600.E"

# Blocks the builder fills when its sources return data.
POPULATED_BLOCKS = {
    "basic",
    "cadastral",
    "population",
    "demographics",
    "economics",
    "buildings",
    "opendata",
    "pvp",
    "valuation",
}

ENVELOPE_KEYS = {
    "available",
    "data",
    "source",
    "dataset_version",
    "model_version",
    "coverage_status",
    "confidence",
    "spatial_resolution",
    "benchmarks",
    "match_method",
}


def _stub_sources(monkeypatch):
    parcel = {
        "type": "Feature",
        "properties": {
            "municipality_code": "H501",
            "sheet_number": "486",
            "urban_section": "A",
            "area_sqm": 3764.85,
        },
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[12.47, 41.89], [12.48, 41.89], [12.48, 41.90], [12.47, 41.90], [12.47, 41.89]]],
        },
    }
    monkeypatch.setattr(stats_service, "get_parcel_by_reference", lambda ref: parcel)
    monkeypatch.setattr(stats_service, "_get_postgres_stats_source", lambda: None)
    monkeypatch.setattr(
        stats_service,
        "get_buildings_for_parcel",
        lambda *args, **kwargs: {
            "available": True,
            "source": "SISTER SQLite",
            "buildings": [{"category": "A/2"}],
        },
    )
    monkeypatch.setattr(
        stats_service,
        "get_opendata_for_parcel",
        lambda *args, **kwargs: {"available": True, "source": "OpenData", "records": [{"id": 1}]},
    )
    monkeypatch.setattr(
        stats_service,
        "get_pvp_for_parcel",
        lambda *args, **kwargs: {"available": True, "source": "PVP", "records": [{"id": 2}]},
    )
    monkeypatch.setattr(
        stats_service,
        "get_omi_zone_at_point",
        lambda *args, **kwargs: {"matched": True, "zone": "B31"},
    )
    monkeypatch.setattr(
        stats_service,
        "_census_section_for_parcel",
        lambda *args, **kwargs: {"type": "Feature", "properties": {"p1": 120, "pf1": 50}, "geometry": None},
    )


def _build(monkeypatch):
    _stub_sources(monkeypatch)
    return stats_service._build_parcel_enrichment(
        REFERENCE,
        municipality_override={"name": "Roma", "province": "Roma", "province_sigla": "RM"},
        omi_override={"quotes": [{"zona": "B31", "prezzo_min": 7400, "prezzo_max": 9700}]},
        income_override={"year": 2022, "taxpayers": 1000, "mean_taxable_income_eur": 25000, "income_distribution": []},
    )


def test_every_declared_block_has_the_common_envelope(monkeypatch):
    result = _build(monkeypatch)

    assert set(result["blocks"]) == set(stats_service._PARCEL_DETAIL_BLOCKS)
    for name, block in result["blocks"].items():
        assert ENVELOPE_KEYS <= set(block), name


def test_populated_block_inventory_is_pinned(monkeypatch):
    result = _build(monkeypatch)

    populated = {name for name, block in result["blocks"].items() if block["available"]}

    assert populated == POPULATED_BLOCKS


def test_declared_but_empty_blocks_are_explicitly_unavailable(monkeypatch):
    result = _build(monkeypatch)

    empty = set(stats_service._PARCEL_DETAIL_BLOCKS) - POPULATED_BLOCKS
    assert empty, "every declared block is populated; update this inventory"
    for name in empty:
        block = result["blocks"][name]
        assert block["available"] is False, name
        assert block["data"] is None, name
        assert block["coverage_status"] == "not_available", name


def test_valuation_is_unavailable_when_nothing_matched(monkeypatch):
    """An empty OMI lookup must not report a populated valuation block."""
    _stub_sources(monkeypatch)
    monkeypatch.setattr(
        stats_service,
        "get_omi_zone_at_point",
        lambda *args, **kwargs: {"matched": False, "zone": None},
    )

    result = stats_service._build_parcel_enrichment(
        REFERENCE,
        municipality_override={"name": "Roma", "province": "Roma", "province_sigla": "RM"},
        omi_override={"quotes": []},
        income_override=None,
    )

    block = result["blocks"]["valuation"]
    assert block["available"] is False
    assert block["data"] is None
    assert block["coverage_status"] == "not_available"


def test_valuation_is_available_with_a_matched_zone_but_no_quotes(monkeypatch):
    _stub_sources(monkeypatch)

    result = stats_service._build_parcel_enrichment(
        REFERENCE,
        municipality_override={"name": "Roma", "province": "Roma", "province_sigla": "RM"},
        omi_override={"quotes": []},
        income_override=None,
    )

    assert result["blocks"]["valuation"]["available"] is True
