from land_registry.routers.api_v2 import (
    _ENRICHMENT_SECTIONS,
    _SECTION_SOURCES,
    _unavailable_enrichment_section,
)


def test_every_catalog_enrichment_has_a_valid_unavailable_fallback():
    assert _ENRICHMENT_SECTIONS <= _SECTION_SOURCES.keys()

    for section in _ENRICHMENT_SECTIONS:
        block = _unavailable_enrichment_section(section, "test_unavailable")
        assert block["available"] is False
        assert block["data"] is None
        assert block["source"]
        assert block["reason"] == "test_unavailable"
