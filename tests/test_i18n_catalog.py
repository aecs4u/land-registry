"""Translation catalogs load from the .po source, so deploys need no compile step."""

import ast
import re
import shutil
from pathlib import Path

from babel.messages.pofile import read_po

from land_registry import i18n

ROOT = Path(__file__).resolve().parents[1]
_TRANSLATION_CALL = re.compile(
    r"\btr\(\s*(?:'((?:\\.|[^'\\])*)'|\"((?:\\.|[^\"\\])*)\")",
    re.DOTALL,
)


def _literal_translation_keys(path: Path) -> set[str]:
    keys = set()
    for single_quoted, double_quoted in _TRANSLATION_CALL.findall(path.read_text(encoding="utf-8")):
        literal = single_quoted if single_quoted else double_quoted
        quote = "'" if single_quoted else '"'
        keys.add(ast.literal_eval(f"{quote}{literal}{quote}"))
    return keys


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


def test_italian_catalog_covers_literal_primary_map_strings():
    source = i18n.TRANSLATIONS_DIR / "it" / "LC_MESSAGES" / f"{i18n.DOMAIN}.po"
    with source.open("rb") as handle:
        catalog = read_po(handle, locale="it")
    translated = {str(message.id) for message in catalog if message.id and message.string}

    ui_files = (
        ROOT / "land_registry" / "static" / "parcel-panel.js",
        ROOT / "land_registry" / "static" / "map-v2.js",
    )
    missing = sorted(
        key
        for path in ui_files
        for key in _literal_translation_keys(path)
        if key not in translated
    )

    assert not missing, "Add Italian catalog entries for:\n" + "\n".join(missing)
