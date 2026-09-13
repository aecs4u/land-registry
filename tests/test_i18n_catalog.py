"""Translation catalogs load from the .po source, so deploys need no compile step."""

import shutil

from land_registry import i18n


def test_catalog_loads_from_po_without_a_compiled_mo(tmp_path, monkeypatch):
    source = i18n.TRANSLATIONS_DIR / "it" / "LC_MESSAGES" / f"{i18n.DOMAIN}.po"
    target = tmp_path / "it" / "LC_MESSAGES"
    target.mkdir(parents=True)
    shutil.copy(source, target / source.name)
    monkeypatch.setattr(i18n, "TRANSLATIONS_DIR", tmp_path)

    assert not (target / f"{i18n.DOMAIN}.mo").exists()
    assert i18n._load_translation("it").gettext("Save map settings") == "Salva le impostazioni della mappa"


def test_missing_catalog_falls_back_to_source_strings(tmp_path, monkeypatch):
    monkeypatch.setattr(i18n, "TRANSLATIONS_DIR", tmp_path)
    assert i18n._load_translation("it").gettext("Save map settings") == "Save map settings"


def test_shipped_italian_catalog_covers_account_and_map_strings():
    translation = i18n._load_translation("it")
    for msgid in ("Settings", "Profile", "Light", "Dark", "Analysis map", "Researching", "Download your data"):
        assert translation.gettext(msgid) != msgid, msgid
