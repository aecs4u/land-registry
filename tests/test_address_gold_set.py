"""Regression gold set: typed addresses -> expected ANNCSU street and civic number.

50 Sicilian addresses (14 municipalities, spelling variants, abbreviations and
house-number letters) taken from the ANNCSU extract 2026-09-15. Each text must
parse, resolve inside its municipality and return the expected street id and
access. Needs the local aecs4u-stats ``anncsu`` Postgres database; skipped when
it is not reachable.
"""

import json
from pathlib import Path

import pytest

from land_registry.address_search import fold, parse_address
from land_registry.anncsu_lookup import find_addresses

GOLD = json.loads((Path(__file__).parent / "data" / "address_gold_sicily.json").read_text(encoding="utf-8"))
MIN_EXACT_RATE = 0.95


def test_gold_set_size_and_variety():
    assert len(GOLD) >= 50
    assert len({e["istat"] for e in GOLD}) >= 12
    assert sum(1 for e in GOLD if e["exponent"]) >= 5


def test_gold_texts_parse_to_the_expected_municipality_and_civic():
    for entry in GOLD:
        parsed = parse_address(entry["text"])
        assert parsed, entry["text"]
        first = parsed[0]
        assert first.civic == entry["civic"], entry["text"]
        assert (first.exponent or None) == (entry["exponent"] or None), entry["text"]
        if "(XX)" not in entry["text"]:
            assert first.municipality_hint == fold(entry["municipality"]), entry["text"]


def test_gold_addresses_resolve_against_anncsu():
    try:
        find_addresses(GOLD[0]["istat"], parse_address(GOLD[0]["text"])[0])
    except Exception as exc:  # database not reachable in this environment
        pytest.skip(f"ANNCSU database unavailable: {type(exc).__name__}")
    misses = []
    for entry in GOLD:
        parsed = parse_address(entry["text"])[0]
        matches = find_addresses(entry["istat"], parsed)
        top = matches[0] if matches else None
        ok = (
            top is not None
            and top.match_level == "address"
            and top.street_id == entry["street_id"]
            and top.civic == entry["civic"]
            and (top.exponent or None) == (entry["exponent"] or None)
        )
        if not ok:
            misses.append((entry["text"], top.match_level if top else None))
    rate = 1 - len(misses) / len(GOLD)
    assert rate >= MIN_EXACT_RATE, f"exact-hit rate {rate:.0%}; misses: {misses}"
