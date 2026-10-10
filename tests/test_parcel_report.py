"""Contracts for the server-rendered parcel dossier export."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from land_registry import i18n, parcel_report, stats_service
from land_registry.routers import enrichment as enrichment_module


def _request(accept_language="en", method="GET", body=b""):
    scope = {
        "type": "http",
        "method": method,
        "path": "/api/v1/enrichment/parcel/report/H501_1.2",
        "headers": [(b"accept-language", accept_language.encode())],
    }
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


def _feature(feature_id=7):
    return {
        "type": "Feature",
        "id": feature_id,
        "properties": {
            "national_cadastral_reference": "H501_1.2",
            "municipality_code": "H501",
            "municipality_name": "Roma",
            "sheet_number": "1",
            "parcel_number": "2",
            "area_sqm": 1250,
        },
        "geometry": {"type": "Polygon", "coordinates": [[[12, 41], [12.01, 41], [12.01, 41.01], [12, 41.01], [12, 41]]]},
    }


def test_profile_is_attached_only_to_the_matching_canonical_polygon():
    feature = _feature()
    matching = {"parcel": _feature(), "blocks": {}}
    wrong_id = {"parcel": _feature(8), "blocks": {}}

    assert parcel_report.profile_matches_parcel(feature, matching)
    assert not parcel_report.profile_matches_parcel(feature, wrong_id)
    assert not parcel_report.profile_matches_parcel(feature, None)


def test_pdf_renderer_includes_panel_section_text_and_provenance():
    pdf = parcel_report.render_parcel_report_pdf(
        "H501_1.2",
        _feature(),
        {"parcel": _feature(), "blocks": {}},
        "PR-TEST123456",
        sections=[{
            "id": "omi", "title": "OMI valuation", "state": "ready",
            "body": "Current quote: 7,400–9,700 €/m² & indicative only.",
            "metadata": {"source": "OMI", "dataset_version": "2025/2"},
        }],
    )

    assert pdf.startswith(b"%PDF-")
    assert pdf.rstrip().endswith(b"%%EOF")


def test_source_register_separates_zornade_egms_summary_from_copernicus_terms():
    derivative_rows = dict(parcel_report._source_register_rows(
        "Zornade Rischio Subsidenza Italia (Copernicus EGMS L3 Ortho)",
        {"license": "ODbL"},
        lambda message: message,
    ))
    copernicus_rows = dict(parcel_report._source_register_rows(
        "Copernicus EGMS",
        {},
        lambda message: message,
    ))

    assert derivative_rows["Catalog provider"] == "Zornade Rischio Subsidenza Italia (EGMS 100 m summary)"
    assert derivative_rows["Catalog licence"] == "Open Database License (ODbL) 1.0"
    assert "Zornade lists this summary dataset under ODbL 1.0" in derivative_rows["Provider use terms"]
    assert "not endorsed by the European Union" in derivative_rows["Provider attribution"]
    assert copernicus_rows["Licence"] == "Not reported in source metadata"
    assert "no formal licence name is asserted" in copernicus_rows["Provider use terms"]


def test_source_register_uses_official_mef_open_data_citation():
    rows = dict(parcel_report._source_register_rows(
        "MEF/IRPEF via aecs4u-stats PostgreSQL",
        {},
        lambda message: message,
    ))

    assert rows["Licence"] == "Creative Commons Attribution 3.0 Unported (CC BY 3.0)"
    assert rows["Provider citation"] == "MEF – Dipartimento delle Finanze"
    assert rows["Licence review status"] == "Official provider methodology reviewed"


def test_source_register_cites_ingv_mps04_grid_estimate():
    rows = dict(parcel_report._source_register_rows(
        "INGV MPS04 seismic hazard model",
        {"model_version": "MPS04"},
        lambda message: message,
    ))

    assert rows["Licence"] == "Creative Commons Attribution 4.0 International (CC BY 4.0)"
    assert rows["Provider URL"] == "https://mps04-ws.pi.ingv.it/"
    assert "Meletti et al. (2006)" in rows["Provider citation"]
    assert "10% exceedance probability in 50 years" in rows["Provider use terms"]


def test_source_register_preserves_mps04_as_a_separate_risk_section_source():
    rows = parcel_report._source_register_rows(
        "ISPRA IdroGEO / DPC via aecs4u-stats",
        {
            "additional_source": "INGV MPS04 seismic hazard model",
            "additional_model_version": "MPS04",
            "additional_license": "CC BY 4.0",
        },
        lambda message: message,
    )
    sources = [value for key, value in rows if key == "Source"]
    providers = [value for key, value in rows if key == "Catalog provider"]

    assert sources == [
        "ISPRA IdroGEO / DPC via aecs4u-stats",
        "INGV MPS04 seismic hazard model",
    ]
    assert providers == ["No catalog match", "INGV MPS04 seismic hazard model"]
    assert "https://data.ingv.it/docs/note-legali.html" in [value for key, value in rows if key == "Use terms URL"]


def test_source_register_does_not_infer_an_omi_reuse_licence():
    rows = dict(parcel_report._source_register_rows(
        "Agenzia Entrate - OMI",
        {},
        lambda message: message,
    ))

    assert rows["Licence"] == "Not reported in source metadata"
    assert rows["Provider citation"] == "Agenzia Entrate - OMI"
    assert rows["Licence review status"] == "Provider citation verified; licence review pending"
    assert "does not state a named reuse licence" in rows["Provider use terms"]


def test_source_register_keeps_nasa_firms_product_licence_unassigned():
    rows = dict(parcel_report._source_register_rows(
        "NASA FIRMS VIIRS NOAA-21 NRT via aecs4u-stats",
        {},
        lambda message: message,
    ))

    assert rows["Licence"] == "Not reported in source metadata"
    assert "VIIRS_NOAA21_NRT" in rows["Provider use terms"]
    assert "No product-specific reuse terms" in rows["Provider use terms"]
    assert rows["Licence review status"] == "Official policy scope reviewed; product-specific terms pending"
    assert rows["Provider URL"].endswith("FIRMS_VIIRS_Firehotspots.html")
    assert "VIIRS NOAA-21 Near Real-Time" in rows["Provider citation"]


def test_source_register_reports_ispra_coastal_dataset_terms_and_period():
    rows = dict(parcel_report._source_register_rows(
        "ISPRA coastal dynamics 2006-2020",
        {},
        lambda message: message,
    ))

    assert rows["Catalog provider"] == "ISPRA — Linea di Costa 2020 v2.0"
    assert rows["Licence"] == "Creative Commons Attribution 4.0 International (CC BY 4.0)"
    assert rows["Licence review status"] == "Official provider terms reviewed"
    assert "5 m" in rows["Provider use terms"]
    assert "Google Maps imagery" in rows["Provider use terms"]
    assert "2006–2020" in rows["Provider citation"]
    assert rows["Provider attribution"].startswith("ISPRA, Linea di Costa 2020 v2.0")


def test_ispra_coastal_source_register_is_translated_for_italian_reports():
    rows = parcel_report._source_register_rows(
        "ISPRA coastal dynamics 2006-2020",
        {},
        i18n._load_translation("it").gettext,
    )
    values = [value for _, value in rows]

    assert any(value.startswith("I metadati RNDT") for value in values)
    assert any(value.endswith("Metadati fonte: RNDT.") for value in values)


def test_active_fires_uses_noaa21_nrt_and_reports_product_provenance(monkeypatch):
    seen = {}

    def fake_active_fires(**kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr(stats_service, "_active_fires", fake_active_fires)

    result = stats_service.get_active_fires()

    assert seen["source"] == "VIIRS_NOAA21_NRT"
    assert result["source_product"] == "VIIRS_NOAA21_NRT"
    assert "NOAA-21 NRT" in result["source"]


@pytest.mark.asyncio
async def test_fires_route_uses_the_selected_product_adapter(monkeypatch):
    seen = {}

    def fake_get_active_fires(radius_km, lat, lng):
        seen.update(radius_km=radius_km, lat=lat, lng=lng)
        return {"count": 0, "source_product": "VIIRS_NOAA21_NRT"}

    monkeypatch.setattr(stats_service, "get_active_fires", fake_get_active_fires)

    result = await enrichment_module._get_fires(lat=41.9, lng=12.5, radius_km=25)

    assert seen == {"radius_km": 25, "lat": 41.9, "lng": 12.5}
    assert result["source_product"] == "VIIRS_NOAA21_NRT"


@pytest.mark.asyncio
async def test_quality_of_life_route_falls_back_with_country_scope(monkeypatch):
    monkeypatch.setattr(stats_service, "aget_quality_of_life_indicators", AsyncMock(return_value=None))
    fallback = {
        "spatial_resolution": "country",
        "country_name": "Italy",
        "scope_note": "Country-level relocation indicators for {country}; these values do not describe the province or parcel.",
    }
    country_lookup = AsyncMock(return_value=fallback)
    monkeypatch.setattr(stats_service, "aget_country_quality_of_life_indicators", country_lookup)

    result = await enrichment_module._get_quality_of_life("H501")

    assert result == fallback
    country_lookup.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_crime_route_uses_country_safety_when_local_result_is_empty(monkeypatch):
    monkeypatch.setattr(
        enrichment_module._aecs4u_stats_enrichment,
        "get_crime",
        AsyncMock(return_value={"total_crimes": None, "crime_types": None, "year": None}),
    )
    fallback = {"safety_index": 71.5, "spatial_resolution": "country", "country": "Italy"}
    country_lookup = AsyncMock(return_value=fallback)
    monkeypatch.setattr(stats_service, "aget_country_safety_profile", country_lookup)

    result = await enrichment_module._get_crime("H501")

    assert result == fallback
    country_lookup.assert_awaited_once_with()


@pytest.mark.parametrize(
    ("source", "provider", "status", "terms_url", "terms_fragment"),
    [
        (
            "Agenzia del Demanio concessions",
            "Agenzia del Demanio",
            "Official general data policy reviewed; product licence pending",
            "https://dati.agenziademanio.it/",
            "freely reusable",
        ),
        (
            "Agenzia delle Entrate INSPIRE",
            "Agenzia delle Entrate – Catasto",
            "Official service access reviewed; data reuse terms pending",
            "https://www1.agenziaentrate.gov.it/web_app_entrate/accesso_ai_dati.html",
            "does not establish downstream reuse rights",
        ),
        (
            "SISTER SQLite",
            "SISTER",
            "Official service access reviewed; data reuse terms pending",
            "https://www1.agenziaentrate.gov.it/web_app_entrate/accesso_ai_dati.html",
            "does not state downstream reuse terms",
        ),
        (
            "PVP PostgreSQL modelview",
            "Portale delle Vendite Pubbliche",
            "Official public-access guide reviewed; reuse licence not stated",
            "https://pvp.giustizia.it/pvp/it/guida.page",
            "without credentials",
        ),
    ],
)
def test_source_register_reports_reviewed_access_without_inventing_licences(
    source, provider, status, terms_url, terms_fragment
):
    rows = dict(parcel_report._source_register_rows(source, {}, lambda message: message))

    assert rows["Catalog provider"] == provider
    assert rows["Licence"] == "Not reported in source metadata"
    assert rows["Licence review status"] == status
    assert rows["Use terms URL"] == terms_url
    assert terms_fragment in rows["Provider use terms"]


def test_source_register_uses_ispra_dataset_specific_terms():
    rows = dict(parcel_report._source_register_rows(
        "ISPRA PAI/PGRA polygon mosaics",
        {"license": "CC BY-SA 4.0"},
        lambda message: message,
    ))

    assert rows["Catalog licence"] == "Creative Commons Attribution-ShareAlike 4.0 International (CC BY-SA 4.0)"
    assert rows["Provider citation"] == "ISPRA (2020) Pericolosità e indicatori di rischio per frane e alluvioni"
    assert rows["Licence review status"] == "Official provider terms reviewed"
    assert "same licence for redistributed derived data" in rows["Provider use terms"]

    combined_rows = dict(parcel_report._source_register_rows(
        "ISPRA IdroGEO / DPC via aecs4u-stats",
        {},
        lambda message: message,
    ))
    assert combined_rows["Catalog provider"] == "No catalog match"


@pytest.mark.asyncio
async def test_report_route_returns_pdf_with_report_id_and_selected_feature(monkeypatch):
    selected = _feature()
    profile = {
        "national_reference": "H501_1.2",
        "parcel": _feature(),
        "municipality": {"name": "Roma", "province_sigla": "RM", "region": "Lazio"},
        "centroid": {"lat": 41.005, "lng": 12.005},
        "blocks": {
            "economics": {
                "available": True,
                "data": {"tax_year": 2024, "average_income": 28000},
                "source": "MEF/IRPEF",
                "dataset_version": "MEF_IRPEF_2024",
                "model_version": "mef-grouped-gini-v1",
                "license": "CC BY 4.0",
                "updated_at": "2025-10-01",
            },
        },
    }
    seen = {}
    async def resolve(reference, id=None):
        seen["selected"] = (reference, id)
        return selected

    monkeypatch.setattr(enrichment_module, "get_parcel_by_reference", resolve)

    async def get_profile(reference):
        seen["reference"] = reference
        return profile

    monkeypatch.setattr(enrichment_module.stats_service, "aget_parcel_enrichment", get_profile)

    response = await enrichment_module.get_parcel_report("H501_1.2", request=_request(), id=7)

    assert seen["reference"] == "H501_1.2"
    assert seen["selected"] == ("H501_1.2", 7)
    assert response.media_type == "application/pdf"
    assert response.body.startswith(b"%PDF-")
    assert response.body.rstrip().endswith(b"%%EOF")
    assert response.headers["content-disposition"] == 'attachment; filename="parcel-report.pdf"'
    assert response.headers["x-report-id"].startswith("PR-")


@pytest.mark.asyncio
async def test_report_route_falls_back_to_identity_when_read_model_matches_another_polygon(monkeypatch):
    selected = _feature(7)
    profile = {"parcel": _feature(8), "blocks": {"economics": {"available": True, "data": {"average_income": 1}}}}
    async def resolve(_reference, id=None):
        assert id == 7
        return selected

    monkeypatch.setattr(enrichment_module, "get_parcel_by_reference", resolve)

    async def get_profile(_reference):
        return profile

    monkeypatch.setattr(enrichment_module.stats_service, "aget_parcel_enrichment", get_profile)

    response = await enrichment_module.get_parcel_report("H501_1.2", request=_request(), id=7)

    assert response.body.startswith(b"%PDF-")
    # The route drops the reference-keyed model when its feature ID differs;
    # the identity-only report still remains downloadable.
    assert response.headers["x-report-id"].startswith("PR-")


@pytest.mark.asyncio
async def test_post_report_includes_validated_browser_sections(monkeypatch):
    selected = _feature()
    async def resolve(_reference, id=None):
        assert id == 7
        return selected

    monkeypatch.setattr(enrichment_module, "get_parcel_by_reference", resolve)

    async def get_profile(_reference):
        return {"parcel": selected, "blocks": {}}

    monkeypatch.setattr(enrichment_module.stats_service, "aget_parcel_enrichment", get_profile)
    seen = {}

    def render(reference, parcel, profile, report_id, translate, sections):
        seen["snapshot"] = sections
        assert reference == "H501_1.2"
        assert parcel is selected
        assert sections[0]["body"] == "OMI & valuation details"
        assert sections[0]["metadata"]["source"] == "OMI"
        assert sections[1]["id"] == "address"
        assert sections[2]["metadata"]["additional_source"] == "INGV MPS04 seismic hazard model"
        return b"%PDF-browser-sections"

    monkeypatch.setattr(parcel_report, "render_parcel_report_pdf", render)
    body = json.dumps({"sections": [
        {
            "id": "omi", "title": "OMI valuation", "state": "ready",
            "body": "OMI & valuation details", "metadata": {"source": "OMI", "dataset_version": "2025/2"},
        },
        {
            "id": "address", "title": "Main address", "state": "ready",
            "body": "Via Roma 1", "metadata": {"source": "SISTER SQLite visura_properties.address"},
        },
        {
            "id": "risks", "title": "Environmental risks", "state": "ready",
            "body": "PGA (10 percent in 50 years): 0.123 g",
            "metadata": {
                "source": "ISPRA IdroGEO / DPC via aecs4u-stats",
                "additional_source": "INGV MPS04 seismic hazard model",
                "additional_model_version": "MPS04",
                "additional_license": "CC BY 4.0",
            },
        },
    ]}).encode()
    response = await enrichment_module.post_parcel_report(
        "H501_1.2", request=_request(method="POST", body=body), id=7,
    )

    assert response.body == b"%PDF-browser-sections"
    assert response.headers["x-report-id"].startswith("PR-")
    assert seen["snapshot"][0]["id"] == "omi"


@pytest.mark.asyncio
async def test_post_report_rejects_unknown_and_oversized_snapshots():
    unknown = json.dumps({"sections": [{
        "id": "arbitrary", "title": "Injected", "state": "ready", "body": "text",
    }]}).encode()
    with pytest.raises(HTTPException) as error:
        await enrichment_module._parse_parcel_report_snapshot(_request(method="POST", body=unknown))
    assert error.value.status_code == 422

    oversized = json.dumps({"sections": [{
        "id": "omi", "title": "OMI", "state": "ready", "body": "x" * 18_001,
    }]}).encode()
    with pytest.raises(HTTPException) as error:
        await enrichment_module._parse_parcel_report_snapshot(_request(method="POST", body=oversized))
    assert error.value.status_code == 422


@pytest.mark.asyncio
async def test_parcel_details_panel_view_is_compact_and_full_view_stays_compatible(monkeypatch):
    profile = {
        "national_reference": "H501_1.2",
        "omi": {"quotes": [{"zone": "B31"}]},
        "postgres_context": {"tax_facts": [{"year": 2025}]},
        "blocks": {
            "valuation": {
                "available": True,
                "data": {"quotes": [{"zone": f"B{index}"} for index in range(10)]},
            },
            "solar": {"available": True, "data": {"pv_n_buildings": 10}},
        },
    }

    requested_views = []

    async def get_profile(_reference, _refresh=False, view="full", feature_id=None):
        requested_views.append((view, feature_id))
        return enrichment_module.stats_service.parcel_panel_read_model(profile) if view == "panel" else profile

    monkeypatch.setattr(enrichment_module.stats_service, "aget_parcel_enrichment", get_profile)

    full = await enrichment_module.get_parcel_details(
        "H501_1.2", refresh=False, view="full"
    )
    full_feature = await enrichment_module.get_parcel_details(
        "H501_1.2", refresh=False, view="full", id=44
    )
    panel = await enrichment_module.get_parcel_details(
        "H501_1.2", refresh=False, view="panel", id=55
    )

    assert full is profile
    assert full_feature is profile
    assert requested_views == [("full", None), ("full", 44), ("panel", 55)]
    assert set(panel) == {"national_reference", "blocks", "read_model", "source"}
    assert panel["blocks"]["valuation"]["data"]["quote_count"] == 10
    assert len(panel["blocks"]["valuation"]["data"]["quote_preview"]) == 8


@pytest.mark.asyncio
async def test_parcel_details_returns_503_when_canonical_feature_lookup_fails(monkeypatch):
    async def lookup(*_args, **_kwargs):
        raise stats_service.CanonicalParcelLookupError("canonical source unavailable")

    monkeypatch.setattr(enrichment_module.stats_service, "aget_parcel_enrichment", lookup)

    with pytest.raises(HTTPException) as error:
        await enrichment_module.get_parcel_details(
            "H501_1.2", refresh=False, view="panel", id=55
        )

    assert error.value.status_code == 503
