import sqlite3

from land_registry.stats_service import _SisterBuildingSource, _SisterDocumentSource, _SisterPostgresSource


def test_sister_postgres_match_accepts_section_prefixed_sheet():
    assert _SisterPostgresSource._match_location(
        {
            "location_province": "Ravenna",
            "location_municipality": "RAVENNA",
            "location_sheet": "RA/103",
            "location_parcel": "1714",
        },
        {"103"},
        {"1714"},
        {"ra", "ravenna"},
        {"ravenna"},
    ) is True


def test_sister_query_returns_building_category_for_legacy_schema(tmp_path):
    database = tmp_path / "sister.sqlite"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE building_identifiers (
            id INTEGER PRIMARY KEY,
            building_unit_id INTEGER,
            current_state_id INTEGER,
            history_document_id INTEGER,
            province TEXT,
            municipality_code TEXT,
            municipality TEXT,
            sheet TEXT,
            parcel TEXT,
            subunit TEXT
        );
        CREATE TABLE building_classifications (
            id INTEGER PRIMARY KEY,
            building_unit_id INTEGER,
            current_state_id INTEGER,
            history_document_id INTEGER,
            census_zone TEXT,
            category TEXT,
            cadastral_class TEXT,
            cadastral_income NUMERIC,
            consistency_value NUMERIC,
            consistency_unit TEXT
        );
        INSERT INTO building_identifiers
            (id, building_unit_id, province, municipality, sheet, parcel, subunit)
        VALUES (1, 10, 'Padova', 'Cittadella', '0018', '1036', '1');
        INSERT INTO building_classifications
            (id, building_unit_id, category, cadastral_class, cadastral_income,
             consistency_value, consistency_unit)
        VALUES (1, 10, 'A/2', '3', 456.78, 6, 'vani');
        """
    )
    connection.commit()
    connection.close()

    result = _SisterBuildingSource(database).buildings_for_parcel(
        "C743_001800.1036",
        "C743",
        {"province": "Padova", "name": "Cittadella"},
    )

    assert result["available"] is True
    assert result["count"] == 1
    assert result["buildings"][0]["building_type"] == "A/2"
    assert result["buildings"][0]["cadastral_income"] == 456.78


def test_sister_query_returns_empty_when_no_cached_visura(tmp_path):
    database = tmp_path / "sister.sqlite"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE building_identifiers (id INTEGER PRIMARY KEY)")
    connection.execute("CREATE TABLE building_classifications (id INTEGER PRIMARY KEY, category TEXT)")
    connection.commit()
    connection.close()

    result = _SisterBuildingSource(database).buildings_for_parcel(
        "C743_001800.1036",
        "C743",
        {"province": "Padova", "name": "Cittadella"},
    )

    assert result["available"] is False
    assert result["buildings"] == []


def _create_document_schema(connection):
    connection.executescript(
        """
        CREATE TABLE visura_documents (
            id INTEGER PRIMARY KEY,
            response_id TEXT,
            document_type TEXT NOT NULL,
            file_format TEXT NOT NULL,
            filename TEXT NOT NULL,
            file_path TEXT,
            file_size INTEGER,
            created_at TEXT NOT NULL,
            subject TEXT,
            requested_at TEXT
        );
        CREATE TABLE document_metadata (
            id INTEGER PRIMARY KEY,
            location_id INTEGER,
            municipality_code TEXT,
            view_subtype TEXT,
            protocol TEXT,
            year TEXT,
            title TEXT,
            reference_date TEXT,
            registry_view_type TEXT,
            service_type TEXT,
            generation_date TEXT,
            content TEXT
        );
        CREATE TABLE cadastral_locations (
            id INTEGER PRIMARY KEY,
            cadastre_type TEXT NOT NULL,
            province TEXT NOT NULL,
            municipality TEXT NOT NULL,
            sheet TEXT NOT NULL,
            parcel TEXT NOT NULL,
            subunit TEXT NOT NULL,
            section TEXT NOT NULL
        );
        """
    )


def test_sister_query_returns_indexed_visura_documents(tmp_path):
    database = tmp_path / "sister.sqlite"
    connection = sqlite3.connect(database)
    _create_document_schema(connection)
    connection.execute(
        "INSERT INTO cadastral_locations VALUES (1, 'F', 'Padova', 'Cittadella', '18', '1036', '1', '')"
    )
    connection.execute(
        "INSERT INTO visura_documents VALUES (1, NULL, 'visura_fabbricati', 'pdf', 'visura.pdf', '', 12, '2026-01-01', NULL, NULL)"
    )
    connection.execute(
        "INSERT INTO document_metadata VALUES (1, 1, 'C743', 'attuale', 'P1', '2026', NULL, NULL, NULL, NULL, NULL, '<Visura><DatiRichiesta Provincia=\"PD\" CodiceComune=\"C743\" Comune=\"CITTADELLA\"/><IdentificativoDefinitivo Provincia=\"PD\" CodiceComune=\"C743\" Comune=\"CITTADELLA\" Foglio=\"18\" ParticellaNum=\"1036\" Subalterno=\"1\"/><DatiClassamentoF Categoria=\"A/2\" RenditaEuro=\"456,78\"/></Visura>')"
    )
    connection.commit()
    connection.close()

    result = _SisterDocumentSource(database).documents_for_parcel(
        "C743_001800.1036/1",
        "C743",
        {"province": "Padova", "province_sigla": "PD", "name": "Cittadella"},
    )

    assert result["available"] is True
    assert result["count"] == 1
    record = result["records"][0]
    assert record["endpoint"] == "visura_fabbricati"
    assert record["result"]["filename"] == "visura.pdf"
    assert record["result"]["data"]["identificativi"][0]["Foglio"] == "18"


def test_sister_query_matches_unlinked_xml_and_extracts_owner(tmp_path):
    database = tmp_path / "sister.sqlite"
    connection = sqlite3.connect(database)
    _create_document_schema(connection)
    connection.execute(
        "INSERT INTO visura_documents VALUES (1, NULL, 'visura_soggetto', 'pdf', 'owner.pdf', '', 12, '2026-01-01', NULL, NULL)"
    )
    content = (
        '<Visura><DatiRichiesta Provincia="PD" CodiceComune="C743" Comune="CITTADELLA"/>'
        '<IdentificativoDefinitivo Provincia="PD" CodiceComune="C743" Comune="CITTADELLA" Foglio="18" ParticellaNum="1036"/>'
        '<Intestato><Nominativo>Mario Rossi</Nominativo><CF>RSSMRA00A00A000A</CF>'
        '<DirittiReali Quota="1/1" Descrizione="Proprieta"/></Intestato></Visura>'
    )
    connection.execute(
        "INSERT INTO document_metadata VALUES (1, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, ?)",
        (content,),
    )
    connection.commit()
    connection.close()

    result = _SisterDocumentSource(database).documents_for_parcel(
        "C743_001800.1036",
        "C743",
        {"province": "Padova", "province_sigla": "PD", "name": "Cittadella"},
    )

    assert result["count"] == 1
    record = result["records"][0]
    assert record["result"]["match_method"] == "document_xml"
    assert record["result"]["data"]["intestatari"][0]["codice_fiscale"] == "RSSMRA00A00A000A"
