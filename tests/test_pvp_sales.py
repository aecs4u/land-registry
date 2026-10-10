"""Contracts for the PVP public-auction sales layer (aecs4u-stats pvp.v_map_sales)."""

import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime

import pytest

from land_registry.pvp_sales import (
    APPROXIMATE_LOCATION_SHARE,
    CATEGORY_KEYS,
    POINT_FIELDS,
    PvpSalesStore,
    sale_category,
)


@pytest.mark.parametrize(
    ("property_type", "category"),
    [
        ("Abitazione Di Tipo Civile", "residential"),
        ("Appartamento", "residential"),
        ("Abitazione Di Tipo Rurale", "residential"),
        ("Villa", "residential"),
        ("Terreno", "land"),
        ("Lotto Edificabile", "land"),
        ("Posto Auto", "parking_storage"),
        ("Stalle, Scuderie, Rimesse, Autorimesse", "parking_storage"),
        ("Magazzini Sotterranei, Deposito Di Derrate", "parking_storage"),
        ("Fabbricati Costruiti Per Esigenze Commerciali", "commercial"),
        ("Negozi, Botteghe", "commercial"),
        ("Casa Di Cura O Ospedale", "commercial"),
        ("Fabbricati Costruiti Per Esigenze Industriali", "industrial"),
        ("Fabbricato Rurale", "industrial"),
        ("Opificio Industriale", "industrial"),
        ("Automezzi Commerciali", "movable"),
        ("Cessione D'Azienda", "movable"),
        ("Porzione Di Fabbricato In Corso Di Costruzione", "building"),
        ("Altro", "other"),
        ("", "unspecified"),
        (None, "unspecified"),
    ],
)
def test_property_types_group_into_map_categories(property_type, category):
    assert sale_category(property_type) == category
    assert category in CATEGORY_KEYS


def test_untyped_sales_are_classified_from_their_notice_text():
    assert sale_category("", "Piena proprietà di box auto dotato di serranda") == "parking_storage"
    assert sale_category(None, "Appartamento al piano secondo con cantina") == "residential"
    assert sale_category("", "Lotto unico") == "unspecified"
    # An explicit type wins over the text.
    assert sale_category("Terreno", "appartamento") == "land"


def test_loader_ships_a_short_description_hint_for_untyped_sales():
    connection = _Connection({"sale_id", "latitude", "longitude", "property_type", "description"}, rows=[
        {"sale_id": 1, "latitude": 44.1, "longitude": 12.2, "property_type": "", "description_hint": "box auto"},
    ])
    snapshot = asyncio.run(PvpSalesStore(_Source(connection))._load())
    load_sql = next(sql for sql in connection.sql if "FROM pvp.v_map_sales" in sql)
    assert "THEN left(description, 240) END AS description_hint" in load_sql
    assert snapshot["points"][0][5] == "parking_storage"


def _row(sale_id, lat, lng, *, price=100000.0, when=datetime(2026, 10, 15, 10), kind="Appartamento"):
    return {"sale_id": sale_id, "latitude": lat, "longitude": lng, "price": price,
            "sale_datetime": when, "property_type": kind}


def test_snapshot_drops_placeholder_coordinates_and_flags_shared_geocodes():
    rows = [
        _row(1, 44.1391, 12.2431),
        _row(1, 44.1391, 12.2431),  # duplicate sale
        _row(2, 1.0, 1.0),  # placeholder geocode
        _row(3, 683.0, 684.0),
        _row(4, 48.85, 2.35),  # abroad
        _row(5, None, 12.0),
        _row(6, 45.0, 9.0, price=0.0, when=None, kind=""),
    ] + [_row(100 + index, 41.93448, 12.61037) for index in range(APPROXIMATE_LOCATION_SHARE)]

    snapshot = PvpSalesStore._build_snapshot(rows)
    by_id = {point[0]: dict(zip(POINT_FIELDS, point)) for point in snapshot["points"]}

    assert set(by_id) == {1, 6, *range(100, 100 + APPROXIMATE_LOCATION_SHARE)}
    assert by_id[1] == {"id": 1, "lng": 12.2431, "lat": 44.1391, "price": 100000, "date": "2026-10-15",
                        "category": "residential", "approximate": 0}
    assert by_id[6]["price"] is None and by_id[6]["date"] is None and by_id[6]["category"] == "unspecified"
    assert all(by_id[sale]["approximate"] == 1 for sale in range(100, 100 + APPROXIMATE_LOCATION_SHARE))


def _store_with(rows):
    store = PvpSalesStore(connection_source=object())
    store._snapshot = PvpSalesStore._build_snapshot(rows)
    store._loaded_at = float("inf")
    return store


def test_map_points_filter_by_period_category_and_price():
    store = _store_with([
        _row(1, 44.0, 12.0, when=datetime(2026, 11, 1)),
        _row(2, 44.0, 12.1, when=datetime(2026, 3, 1), kind="Terreno", price=5000.0),
        _row(3, 44.0, 12.2, when=datetime(2019, 1, 1)),
        _row(4, 44.0, 12.3, when=None),
    ])
    today = date(2026, 9, 28)

    def ids(**filters):
        payload = asyncio.run(store.map_points(today=today, **filters))
        return [point[0] for point in payload["points"]], payload

    assert ids(period="upcoming")[0] == [1]
    assert ids(period="12m")[0] == [1, 2]
    assert ids(period="all")[0] == [1, 2, 3, 4]
    assert ids(period="all", categories={"land"})[0] == [2]
    assert ids(period="all", max_price=10000)[0] == [2]
    selected, payload = ids(period="12m", categories={"land"})
    assert selected == [2]
    # Category counts ignore the category filter so the type list stays usable.
    counts = {item["key"]: item["count"] for item in payload["categories"]}
    assert counts["residential"] == 1 and counts["land"] == 1
    assert payload["fields"] == list(POINT_FIELDS)
    assert payload["source"]["relation"] == "pvp.v_map_sales"


def test_first_load_does_not_block_and_failures_keep_the_last_copy():
    store = PvpSalesStore(connection_source=object(), ttl=0)
    calls = []

    async def failing_load():
        calls.append(1)
        raise RuntimeError('relation "pvp.v_map_sales" does not exist')

    async def scenario():
        store._load = failing_load
        waiting = await store.map_points(period="all")
        await asyncio.sleep(0)
        store._snapshot = PvpSalesStore._build_snapshot([_row(1, 44.0, 12.0)])
        stale = await store.map_points(period="all")  # refresh fails, copy kept
        await asyncio.sleep(0)
        return waiting, stale

    waiting, stale = asyncio.run(scenario())
    assert waiting is None
    assert stale["count"] == 1 and store._snapshot is not None
    assert "does not exist" in store.last_error


class _Connection:
    def __init__(self, columns, rows=(), detail=None, *, point_columns=(), points_error=None):
        self.columns = columns
        self.point_columns = point_columns
        self.points_error = points_error
        self.rows = list(rows)
        self.detail = detail
        self.sql = []

    @asynccontextmanager
    async def transaction(self):
        yield

    async def execute(self, sql, *params):
        self.sql.append(sql)

    async def fetch(self, sql, *params, timeout=None):
        self.sql.append(sql)
        if "pg_attribute" in sql:
            columns = self.point_columns if params[0].endswith('.v_map_sale_points') else self.columns
            return [{"name": name} for name in columns]
        if "FROM pvp.v_map_sale_points" in sql and self.points_error is not None:
            raise self.points_error
        return self.rows

    async def fetchrow(self, sql, *params, timeout=None):
        self.sql.append(sql)
        return self.detail


class _Source:
    def __init__(self, connection):
        self._connection = connection

    @asynccontextmanager
    async def connection(self):
        yield self._connection


def test_loader_reads_only_published_columns_inside_italy():
    connection = _Connection({"sale_id", "geom", "price", "property_type"}, rows=[
        {"sale_id": 1, "latitude": 44.1, "longitude": 12.2, "price": 9.0, "property_type": "Terreno"},
    ])
    snapshot = asyncio.run(PvpSalesStore(_Source(connection))._load())

    load_sql = next(sql for sql in connection.sql if "FROM pvp.v_map_sales" in sql)
    # No latitude/longitude columns: read the point geometry instead.
    assert "ST_Y(geom) AS latitude" in load_sql and "ST_X(geom) BETWEEN 6.4 AND 18.8" in load_sql
    assert "sale_datetime" not in load_sql
    assert any("SET LOCAL statement_timeout" in sql for sql in connection.sql)
    assert snapshot["points"][0][:6] == [1, 12.2, 44.1, 9, None, "land"]


def test_bulk_points_view_preserves_classification_and_uses_detail_view_for_popups():
    point_columns = {
        "sale_id", "latitude", "longitude", "price", "sale_datetime", "property_type",
        "coordinate_is_approximate", "description_hint",
    }
    detail_columns = point_columns - {"description_hint"} | {"description", "city"}
    connection = _Connection(detail_columns, rows=[
        {**_row(1, 37.03, 15.21, kind=""), "description_hint": "box auto", "coordinate_is_approximate": True},
    ], detail={**dict.fromkeys(detail_columns), "sale_id": 1, "description": "Full notice", "city": "Siracusa"},
       point_columns=point_columns)
    store = PvpSalesStore(_Source(connection))
    snapshot = asyncio.run(store._load())
    detail = asyncio.run(store.sale_detail(1))

    assert snapshot["relation"] == "pvp.v_map_sale_points"
    assert snapshot["points"][0][5:] == ["parking_storage", 1]
    assert detail["description"] == "Full notice" and detail["city"] == "Siracusa"
    assert any("FROM pvp.v_map_sales WHERE sale_id" in sql for sql in connection.sql)
    assert any("SET LOCAL work_mem" in sql for sql in connection.sql)


def test_failed_bulk_points_query_falls_back_to_live_sales_view():
    columns = {"sale_id", "latitude", "longitude"}
    connection = _Connection(columns, rows=[_row(1, 37.03, 15.21)], point_columns=columns,
                             points_error=RuntimeError("points view unavailable"))
    snapshot = asyncio.run(PvpSalesStore(_Source(connection))._load())
    assert snapshot["relation"] == "pvp.v_map_sales"
    assert snapshot["points"][0][:3] == [1, 15.21, 37.03]


def test_concurrent_force_refresh_requests_share_one_database_load():
    async def scenario():
        store = PvpSalesStore(connection_source=object())
        started, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def load():
            calls.append(1)
            started.set()
            await release.wait()
            return store._build_snapshot([_row(1, 37.03, 15.21)])

        store._load = load
        first = asyncio.create_task(store.refresh())
        await started.wait()
        second = asyncio.create_task(store.refresh())
        await asyncio.sleep(0)
        release.set()
        snapshots = await asyncio.gather(first, second)
        assert snapshots[0] is snapshots[1]
        assert len(calls) == 1

    asyncio.run(scenario())


def test_sale_detail_offers_only_absolute_links():
    columns = {"sale_id", "property_type", "description", "price", "sale_datetime", "url", "city"}
    row = {"sale_id": 7, "property_type": "Negozio", "description": "  Locale  ", "price": 1.0,
           "sale_datetime": datetime(2026, 10, 1, 9, 30), "url": "/aste/7-negozio", "city": ""}
    detail = asyncio.run(PvpSalesStore(_Source(_Connection(columns, detail=row))).sale_detail(7))

    assert detail["url"] is None
    assert detail["description"] == "Locale" and detail["city"] is None
    assert detail["sale_datetime"] == "2026-10-01T09:30:00"
    assert (detail["category"], detail["category_label"]) == ("commercial", "Shops, offices & hospitality")


@pytest.mark.asyncio
async def test_map_points_route_validates_filters_and_reports_loading(monkeypatch):
    from land_registry.routers import api as api_module
    import land_registry.pvp_sales as pvp_sales

    store = PvpSalesStore(connection_source=object())

    async def loading(**_filters):
        return None

    store.map_points = loading
    monkeypatch.setattr(pvp_sales, "_store", store)

    with pytest.raises(api_module.HTTPException) as error:
        await api_module.get_sales_map_points(period="yesterday", category=None, min_price=None, max_price=None)
    assert error.value.status_code == 400
    with pytest.raises(api_module.HTTPException) as error:
        await api_module.get_sales_map_points(period="all", category="castles", min_price=None, max_price=None)
    assert error.value.status_code == 400
    with pytest.raises(api_module.HTTPException) as error:
        await api_module.get_sales_map_points(period="upcoming", category="land", min_price=None, max_price=None)
    assert error.value.status_code == 503 and "Retry-After" in error.value.headers


def _grid_store():
    # 0.5 degree grid of sales: lat 40..44, lng 8..12 (81 points).
    rows = [
        _row(1000 + i * 9 + j, 40.0 + i * 0.5, 8.0 + j * 0.5, when=datetime(2999, 1, 1))
        for i in range(9) for j in range(9)
    ]
    return _store_with(rows)


def test_map_points_with_bbox_returns_only_points_in_view():
    store = _grid_store()
    everything = asyncio.run(store.map_points(period="all"))
    assert everything["count"] == 81 and "bbox" not in everything  # legacy contract unchanged

    view = asyncio.run(store.map_points(period="all", bbox=(8.9, 40.9, 10.1, 42.1)))
    assert view["bbox"] == [8.9, 40.9, 10.1, 42.1]
    assert view["truncated"] is False
    assert view["count"] == view["matched"] == 9  # lng 9.0/9.5/10.0 x lat 41.0/41.5/42.0
    assert all(8.9 <= p[1] <= 10.1 and 40.9 <= p[2] <= 42.1 for p in view["points"])
    # A box with nothing in it is an empty list, not an error or the national feed.
    empty = asyncio.run(store.map_points(period="all", bbox=(20.0, 50.0, 21.0, 51.0)))
    assert empty["count"] == 0 and empty["points"] == []


def test_map_points_bbox_limit_keeps_the_points_nearest_the_centre_and_flags_truncation():
    store = _grid_store()
    payload = asyncio.run(store.map_points(period="all", bbox=(7.9, 39.9, 12.1, 44.1), limit=5))
    assert payload["matched"] == 81 and payload["count"] == 5 and payload["truncated"] is True
    # The centre of the box is (10.0, 42.0); the nearest point is exactly there.
    assert (payload["points"][0][1], payload["points"][0][2]) == (10.0, 42.0)
    lngs = {p[1] for p in payload["points"]}
    assert lngs <= {9.5, 10.0, 10.5}


def test_map_points_bbox_category_counts_describe_the_viewport():
    rows = [
        _row(1, 41.0, 9.0, when=datetime(2999, 1, 1)),
        _row(2, 41.1, 9.1, when=datetime(2999, 1, 1), kind="Terreno"),
        _row(3, 44.0, 12.0, when=datetime(2999, 1, 1), kind="Terreno"),
    ]
    store = _store_with(rows)
    payload = asyncio.run(store.map_points(period="all", bbox=(8.5, 40.5, 9.5, 41.5)))
    counts = {c["key"]: c["count"] for c in payload["categories"]}
    assert counts["land"] == 1 and counts["residential"] == 1
    assert payload["count"] == 2


def test_sales_map_points_endpoint_accepts_bbox_and_rejects_malformed_boxes(monkeypatch):
    from fastapi.testclient import TestClient

    import land_registry.pvp_sales as pvp_sales
    from land_registry.main import app

    store = _grid_store()
    store._loaded_at = __import__("time").monotonic()
    monkeypatch.setattr(pvp_sales, "_store", store)
    client = TestClient(app)

    ok = client.get("/api/v1/sales/map-points?period=all&bbox=8.9,40.9,10.1,42.1")
    assert ok.status_code == 200 and ok.json()["count"] == 9 and ok.json()["truncated"] is False
    capped = client.get("/api/v1/sales/map-points?period=all&bbox=7.9,39.9,12.1,44.1&limit=4").json()
    assert capped["count"] == 4 and capped["matched"] == 81 and capped["truncated"] is True
    for bad in ("1,2,3", "a,b,c,d", "10,40,8,42", "8,45,9,40", "8,40,9,95", "nan,40,9,42"):
        assert client.get(f"/api/v1/sales/map-points?period=all&bbox={bad}").status_code == 400, bad


def test_sales_map_points_endpoint_serves_pvp_when_configured_and_rejects_bad_filters(monkeypatch):
    from fastapi.testclient import TestClient

    import land_registry.pvp_sales as pvp_sales
    from land_registry.main import app

    store = PvpSalesStore(connection_source=object())
    store._snapshot = PvpSalesStore._build_snapshot([_row(1, 44.0, 12.0, when=datetime(2999, 1, 1))])
    store._loaded_at = __import__("time").monotonic()
    monkeypatch.setattr(pvp_sales, "_store", store)
    client = TestClient(app)

    ok = client.get("/api/v1/sales/map-points?period=all&limit=60000&order_by=saleability_desc")
    assert ok.status_code == 200
    assert ok.json()["fields"] == list(POINT_FIELDS) and ok.json()["count"] == 1
    assert client.get("/api/v1/sales/map-points?period=yesterday").status_code == 400
    assert client.get("/api/v1/sales/map-points?category=castles").status_code == 400
    assert client.get("/api/v1/sales/map-points?min_price=10&max_price=5").status_code == 400


def test_sales_map_points_falls_back_to_proxy_without_stats_database(monkeypatch):
    from fastapi.testclient import TestClient

    import land_registry.pvp_sales as pvp_sales
    from land_registry.main import app

    store = PvpSalesStore(connection_source=None)
    monkeypatch.setattr(store, "_sources", list)
    monkeypatch.setattr(pvp_sales, "_store", store)
    monkeypatch.setenv("LAND_REGISTRY_SALES_BASE_URL", "http://127.0.0.1:1")  # nothing listens
    response = TestClient(app).get("/api/v1/sales/map-points")
    assert response.status_code == 503
    assert response.json()["error"] == "sales map feed unavailable"


@pytest.mark.asyncio
async def test_nearby_sales_endpoint_does_not_wait_for_cold_snapshot(monkeypatch):
    from land_registry.routers import api as api_module
    import land_registry.pvp_sales as pvp_sales

    store = PvpSalesStore(connection_source=object())
    calls = []

    async def loading(**kwargs):
        calls.append(kwargs)
        return None

    store.nearby_points = loading
    monkeypatch.setattr(pvp_sales, "_store", store)

    with pytest.raises(api_module.HTTPException) as error:
        await api_module.sales_nearby_points(lat=41.9, lng=12.5, radius_km=10, limit=8)

    assert error.value.status_code == 503
    assert error.value.headers["Retry-After"] == "10"
    assert calls == [{"lat": 41.9, "lng": 12.5, "radius_km": 10, "limit": 8, "wait": False}]
