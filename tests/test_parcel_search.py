from land_registry.parcel_search import parse_cadastral_components


def test_parse_numeric_cadastral_components():
    assert parse_cadastral_components("C773 28 42") == ("C773", "28", "42")
    assert parse_cadastral_components("C773_000028.0042") == ("C773", "000028", "0042")


def test_parse_alphanumeric_and_subdivided_parcel_labels():
    assert parse_cadastral_components("C773 28 STRADA001") == ("C773", "28", "STRADA001")
    assert parse_cadastral_components("C773 28 42/1") == ("C773", "28", "42/1")


def test_reject_free_form_queries_as_cadastral_components():
    assert parse_cadastral_components("Civitavecchia 28 42") is None
    assert parse_cadastral_components("C773 sheet 42") is None
    assert parse_cadastral_components("C773 28") is None
