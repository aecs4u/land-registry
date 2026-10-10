"""Focused coverage for the SISTER cadastral parcel map overlay."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from land_registry.map_layers import PostgresMapLayerSource


class _SisterConnection:
    def __init__(self):
        self.queries = []
        self.candidate_rows = [{
            "id": 42,
            "canonical_reference": "C773A002800.29",
            "national_cadastral_reference": "C773A002800.29",
            "parcel": "29",
            "sheet": "002800",
            "source_release": "test",
            "municipality_name": "Example",
            "province": "Test Province",
            "geometry": '{"type":"Polygon","coordinates":[]}',
        }]
        self.sister_rows = [{
            "province": "test province",
            "municipality": "example",
            "sheet": "28",
            "parcel": "29",
            "section": "A",
        }]

    async def fetchval(self, sql, *params):
        if "to_regclass($1)" in sql:
            return "spatial.cadastral_parcel"
        if "information_schema.columns" in sql:
            return True
        if "to_regclass('sister.v_sister_property_by_cadastral_parcel')" in sql:
            return True
        raise AssertionError(f"Unexpected fetchval query: {sql}")

    async def fetch(self, sql, *params):
        self.queries.append((sql, params))
        if "SELECT t.id, t.canonical_reference" in sql:
            return self.candidate_rows
        if "FROM sister.v_sister_property_by_cadastral_parcel" in sql:
            return self.sister_rows
        raise AssertionError(f"Unexpected fetch query: {sql}")


class _ConnectionSource:
    def __init__(self, connection):
        self.connection_value = connection

    @asynccontextmanager
    async def connection(self):
        yield self.connection_value


def test_sister_overlay_matches_fixed_width_sheet_and_section():
    connection = _SisterConnection()
    source = PostgresMapLayerSource(_ConnectionSource(connection))

    result = asyncio.run(source.read_sister_parcels((12, 41, 12.1, 41.1)))

    assert result["available"] is True
    assert result["count"] == 1
    feature = result["features"][0]
    assert feature["id"] == 42
    assert feature["properties"]["sister_listed"] is True
    assert feature["geometry"]["type"] == "Polygon"
    sister_sql, sister_params = next(
        (sql, params) for sql, params in connection.queries
        if "FROM sister.v_sister_property_by_cadastral_parcel" in sql
    )
    assert "upper(trim(coalesce(section, ''))) = ANY($7)" in sister_sql
    assert "28" in sister_params[2]
    assert "002800" in sister_params[2]


def test_sister_overlay_endpoint_returns_bounded_feature_collection(monkeypatch):
    from land_registry.main import app
    from land_registry.routers import api as api_module

    payload = {
        "available": True,
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "id": 42, "properties": {"sister_listed": True}, "geometry": None}],
        "count": 1,
    }
    source = MagicMock(available=True)
    source.read_sister_parcels = AsyncMock(return_value=payload)
    monkeypatch.setattr(api_module, "get_map_layer_source", lambda: source)

    response = TestClient(app).get(
        "/api/v1/map/sister-parcels",
        params={"west": 12, "south": 41, "east": 13, "north": 42, "limit": 250},
    )

    assert response.status_code == 200
    assert response.json() == payload
    source.read_sister_parcels.assert_awaited_once_with((12.0, 41.0, 13.0, 42.0), limit=250)


def test_sister_overlay_template_and_frontend_register_the_switch():
    root = Path(__file__).parents[1] / "land_registry"
    script = (root / "static" / "map-v2.js").read_text(encoding="utf-8")
    assert "function ensureSisterParcelsOverlayLayer" in script
    assert "/api/v1/map/sister-parcels" in script
    for template in (root / "templates" / "map_v2.html", root / "templates" / "theme_overrides" / "map_v2.html"):
        assert 'id="toggleSisterParcels"' in template.read_text(encoding="utf-8")


class _VisuraConnection:
    """Document view as stored today: province is the code ('PA'), unlike the property view."""

    def __init__(self, document_rows):
        self.document_rows = document_rows
        self.queries = []

    async def fetchval(self, sql, *params):
        if "to_regclass('sister.v_sister_document_by_cadastral_parcel')" in sql:
            return True
        raise AssertionError(f"Unexpected fetchval query: {sql}")

    async def fetch(self, sql, *params):
        self.queries.append((sql, params))
        if "FROM sister.v_sister_document_by_cadastral_parcel" in sql:
            return self.document_rows
        raise AssertionError(f"Unexpected fetch query: {sql}")


def _parcel_feature(feature_id, reference, sheet, parcel):
    return {
        "type": "Feature",
        "id": feature_id,
        "properties": {
            "id": feature_id,
            "canonical_reference": reference,
            "national_cadastral_reference": reference,
            "sheet": sheet,
            "parcel": parcel,
            "province": "PA",
            "municipality_code": "G273",
        },
        "geometry": None,
    }


def test_visura_flags_match_province_code_or_name_and_only_property_visure():
    connection = _VisuraConnection([
        {"province": "pa", "municipality": "palermo", "section": "", "sheet": "9", "parcel": "107"},
    ])
    source = PostgresMapLayerSource(_ConnectionSource(connection))
    features = [
        _parcel_feature(1, "G273_000900.107", "9", "107"),
        _parcel_feature(2, "G273_000900.108", "9", "108"),
    ]
    names = {"G273": ("Palermo", ("Palermo", "PA"))}

    assert source.visura_candidate_codes(features) == ["G273"]
    flags = asyncio.run(source.read_visura_flags(features, names))

    assert flags == {1: True, 2: False}
    sql, params = connection.queries[0]
    assert "document_type = ANY($1)" in sql
    assert params[0] == ["visura_fabbricati", "visura_terreni", "visura"]
    assert params[1] == ["pa", "palermo"]  # both province spellings are accepted


def test_visura_flags_are_unknown_when_names_or_source_are_missing():
    connection = _VisuraConnection([])
    source = PostgresMapLayerSource(_ConnectionSource(connection))
    features = [_parcel_feature(1, "G273_000900.107", "9", "107")]

    assert asyncio.run(source.read_visura_flags(features, {})) is None
    assert connection.queries == []  # no query without a resolved municipality
    source.connection_source = None  # no SISTER database configured
    assert asyncio.run(source.read_visura_flags(features, {"G273": ("Palermo", ("PA",))})) is None


def test_cadastral_parcel_features_endpoint_adds_has_visura(monkeypatch):
    from land_registry.main import app
    from land_registry.routers import api as api_module

    features = [_parcel_feature(1, "G273_000900.107", "9", "107"), _parcel_feature(2, "G273_000900.108", "9", "108")]
    source = MagicMock(available=True)
    source.read_geojson = AsyncMock(return_value={"type": "FeatureCollection", "features": features})
    source.visura_candidate_codes = MagicMock(return_value=["G273"])
    source.read_visura_flags = AsyncMock(return_value={1: True, 2: False})
    monkeypatch.setattr(api_module, "get_map_layer_source", lambda: source)
    monkeypatch.setattr(
        api_module.stats_service,
        "aget_municipality_by_cadastral_code",
        AsyncMock(return_value={"name": "Palermo", "province": "Palermo", "province_sigla": "PA"}),
    )

    response = TestClient(app).get(
        "/api/v1/map/layers/cadastral-parcels/features",
        params={"west": 13.30, "south": 38.19, "east": 13.32, "north": 38.20},
    )

    assert response.status_code == 200
    flags = {f["id"]: f["properties"]["has_visura"] for f in response.json()["features"]}
    assert flags == {1: True, 2: False}
    source.read_visura_flags.assert_awaited_once()
    assert source.read_visura_flags.await_args.args[1] == {"G273": ("Palermo", ("Palermo", "PA"))}


def test_cadastral_parcel_features_endpoint_leaves_has_visura_null_when_sister_fails(monkeypatch):
    from land_registry.main import app
    from land_registry.routers import api as api_module

    source = MagicMock(available=True)
    source.read_geojson = AsyncMock(return_value={
        "type": "FeatureCollection", "features": [_parcel_feature(1, "G273_000900.107", "9", "107")]})
    source.visura_candidate_codes = MagicMock(return_value=["G273"])
    source.read_visura_flags = AsyncMock(side_effect=RuntimeError("SISTER down"))
    monkeypatch.setattr(api_module, "get_map_layer_source", lambda: source)
    monkeypatch.setattr(
        api_module.stats_service, "aget_municipality_by_cadastral_code", AsyncMock(return_value=None))

    response = TestClient(app).get(
        "/api/v1/map/layers/cadastral-parcels/features",
        params={"west": 13.30, "south": 38.19, "east": 13.32, "north": 38.20},
    )

    assert response.status_code == 200
    assert response.json()["features"][0]["properties"]["has_visura"] is None


def test_attribute_table_renders_extra_boolean_columns():
    script = (Path(__file__).parents[1] / "land_registry" / "static" / "map-v2.js").read_text(encoding="utf-8")
    assert "const cellText = (value) =>" in script
    assert "tr('Yes')" in script and "tr('No')" in script
