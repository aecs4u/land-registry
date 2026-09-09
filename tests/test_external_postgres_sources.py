from contextlib import contextmanager

from land_registry import stats_service


class _Description:
    def __init__(self, name):
        self.name = name


class _Cursor:
    description = [_Description("id"), _Description("result")]

    def __init__(self, rows):
        self.rows = rows
        self.sql = ""
        self.params = None

    def execute(self, sql, params):
        self.sql = sql
        self.params = params

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self, cursor):
        self.cursor_value = cursor

    def cursor(self):
        return self

    def __enter__(self):
        return self.cursor_value

    def __exit__(self, *args):
        return False


def test_reference_parts_include_insPIRE_sheet_variant():
    assert stats_service._parcel_reference_parts("C743_001800.1036") == (
        "C743", ["001800", "1800", "18"], ["1036"]
    )


def test_opendata_source_decodes_json_and_queries_only_selected_location():
    cursor = _Cursor([("query-1", '{"immobili": [{"categoria": "A/2"}]}')])
    source = stats_service._OpenDataPostgresSource("postgresql://localhost/opendata")

    @contextmanager
    def connection():
        yield _Connection(cursor)

    source._connection = connection
    result = source.parcel_data(
        "C743_001800.1036",
        {"province": "Padova", "name": "Cittadella"},
    )

    assert result["available"] is True
    assert result["records"][0]["result"]["immobili"][0]["categoria"] == "A/2"
    assert "cadastral_locations" in cursor.sql
    assert "Padova" in cursor.params
    assert "Cittadella" in cursor.params


def test_pvp_source_retains_one_to_many_records():
    cursor = _Cursor([(1, None), (2, None)])
    source = stats_service._PvpPostgresSource("postgresql://localhost/pvp")

    @contextmanager
    def connection():
        yield _Connection(cursor)

    source._connection = connection
    result = source.parcel_data(
        "C743_001800.1036",
        {"province": "Padova", "name": "Cittadella", "istat_code": "28032"},
    )

    assert result["count"] == 2
    assert result["records"][0]["id"] == 1
    assert "modelview_registries" in cursor.sql
    assert result["match_method"] == "municipality+code+sheet+parcel"


class _RelationCursor:
    def __init__(self):
        self.rows = []

    def execute(self, sql, params=None):
        if "FROM pg_index" in sql:
            self.rows = [
                ("public", "modelview_registries", ["id"]),
                ("public", "modelview_assets", ["id"]),
                ("public", "modelview_sales", ["id"]),
                ("public", "modelview_events", ["id"]),
            ]
            return
        if "FROM pg_constraint" in sql:
            self.rows = [
                ("public", "modelview_registries", "public", "modelview_assets", ["asset_id"], ["id"], "registry_asset"),
                ("public", "modelview_assets", "public", "modelview_sales", ["sale_id"], ["id"], "asset_sale"),
                ("public", "modelview_events", "public", "modelview_sales", ["sale_id"], ["id"], "event_sale"),
            ]
            return
        if '"modelview_registries"' in sql:
            self.rows = [({"id": 1, "asset_id": 10},)]
        elif '"modelview_assets"' in sql:
            self.rows = [({"id": 10, "sale_id": 20, "description": "House"},)]
        elif '"modelview_sales"' in sql:
            self.rows = [({"id": 20, "source": "pvp"},)]
        elif '"modelview_events"' in sql:
            self.rows = [({"id": 7, "sale_id": 20, "event_type": "auction"},)]
        else:
            self.rows = []

    def fetchall(self):
        return self.rows


def test_pvp_relations_are_merged_recursively_and_cycles_are_safe():
    source = stats_service._PvpPostgresSource("postgresql://localhost/pvp")
    cursor = _RelationCursor()

    result = source._resolve_pvp_record_relations(
        cursor,
        {"registry_id": 1, "asset_pk": 10},
    )

    assert result["registry"]["_relations"]["asset"]["description"] == "House"
    sale = result["registry"]["_relations"]["asset"]["_relations"]["sale"]
    assert sale["source"] == "pvp"
    assert sale["_relations"]["events"][0]["event_type"] == "auction"
    assert sale["_relations"]["events"][0]["_relations"]["sale"]["_ref"].endswith("modelview_sales:20")
