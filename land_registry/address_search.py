"""Parse free-text Italian civic addresses for the map search box.

Pure functions, no I/O.  ``parse_address`` turns text such as
``"via goethe 1, Palermo"`` or ``"V. Goethe 14/A (PA)"`` into candidate
``ParsedAddress`` values (street, civic number, exponent, municipality hint).
``normalize_street`` produces the comparison key used against the ANNCSU
street registry.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# Canonical street-type word for each accepted spelling (dots already removed).
STREET_TYPES: dict[str, str] = {
    "via": "via", "v": "via", "vi": "via",
    "viale": "viale", "vle": "viale", "vl": "viale",
    "piazza": "piazza", "pza": "piazza", "pzza": "piazza", "pz": "piazza",
    "piazzale": "piazzale", "ple": "piazzale", "pzle": "piazzale",
    "corso": "corso", "cso": "corso", "c": "corso",
    "largo": "largo", "lgo": "largo", "lg": "largo",
    "vicolo": "vicolo", "vic": "vicolo", "vco": "vicolo",
    "strada": "strada", "str": "strada",
    "contrada": "contrada", "c.da": "contrada", "cda": "contrada",
    "localita": "localita", "loc": "localita",
    "lungomare": "lungomare", "lungotevere": "lungotevere",
    "salita": "salita", "discesa": "discesa", "traversa": "traversa",
    "borgo": "borgo", "rione": "rione", "frazione": "frazione", "fraz": "frazione",
}
# Spellings only read as a street type in first position ("v. goethe", "c. mazzini").
_FIRST_POSITION_ONLY = {"v", "vi", "c", "pz", "lg", "vl"}

_NOISE_TAIL = {"italia", "italy", "it", "sicilia"}
_CIVIC_WORD = {"n", "no", "civ", "civico", "num", "numero"}
_EXPONENT_WORDS = {"bis": "BIS", "ter": "TER", "quater": "QUATER"}
_APOSTROPHES = re.compile(r"[’‘`´ʼ']")
_NON_WORD = re.compile(r"[^a-z0-9/]+")


@dataclass(frozen=True)
class ParsedAddress:
    """One reading of the user's text."""

    street: str                  # normalised street name, without house number
    civic: str | None = None     # house number digits, e.g. "14"
    exponent: str | None = None  # "A", "BIS"... (upper case) or None
    municipality_hint: str | None = None
    street_type: str | None = None
    raw_street: str = ""         # street as typed, for labels
    municipality_candidates: tuple[str, ...] = field(default=())

    @property
    def civic_label(self) -> str | None:
        if not self.civic:
            return None
        return f"{self.civic}/{self.exponent}" if self.exponent else self.civic


def fold(text: str) -> str:
    """Lower-case, accent-free, apostrophe-free text."""
    decomposed = unicodedata.normalize("NFKD", str(text or ""))
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _APOSTROPHES.sub(" ", stripped).casefold().strip()


def _tokens(text: str) -> list[str]:
    # "p.zza" -> "pzza"; other punctuation separates tokens.
    folded = re.sub(r"(?<=\w)\.(?=\w)", "", fold(text))
    return [t for t in _NON_WORD.sub(" ", folded).split() if t]


def normalize_street(text: str) -> tuple[str | None, str]:
    """Return ``(street_type, name_key)`` for comparison with registry names.

    ``name_key`` is the folded street name without its type word and without
    filler such as ``di``/``del``/``della`` so that "Via G. Mazzini" and
    "VIA GIUSEPPE MAZZINI" share their final tokens for matching.
    """
    tokens = _tokens(text)
    street_type = None
    if tokens and tokens[0] in STREET_TYPES and (
        tokens[0] not in _FIRST_POSITION_ONLY or len(tokens) > 1
    ):
        street_type = STREET_TYPES[tokens[0]]
        tokens = tokens[1:]
    return street_type, " ".join(tokens)


def _split_civic(tokens: list[str]) -> tuple[list[str], str | None, str | None, list[str]]:
    """Split ``tokens`` into (street, civic, exponent, rest).

    The civic number is the last numeric token that still leaves at least one
    street-name word (not just a street type) before it, so "via 4 novembre"
    keeps its 4 while "via goethe 1" yields civic 1.
    """
    for index in range(len(tokens) - 1, -1, -1):
        match = re.fullmatch(r"(\d{1,5})(?:/?([a-z]))?", tokens[index])
        if not match:
            continue
        before = [t for t in tokens[:index] if t not in _CIVIC_WORD]
        name_words = [t for i, t in enumerate(before) if not (i == 0 and t in STREET_TYPES)]
        if not name_words:
            continue
        civic, exponent = match.group(1), match.group(2)
        rest = tokens[index + 1:]
        # "1 / a", "1 bis", "1 a"
        if not exponent and rest:
            nxt = rest[0]
            if nxt in _EXPONENT_WORDS:
                exponent, rest = _EXPONENT_WORDS[nxt], rest[1:]
            elif re.fullmatch(r"[a-z]", nxt):
                # a lone letter straight after the number is an exponent
                exponent, rest = nxt.upper(), rest[1:]
        elif exponent:
            exponent = exponent.upper()
        return before, civic, exponent, rest
    return [t for t in tokens if t not in _CIVIC_WORD], None, None, []


def _strip_province(tokens: list[str]) -> list[str]:
    """Drop a trailing province sigla/country: ``palermo pa`` -> ``palermo``."""
    out = list(tokens)
    while out and out[-1] in _NOISE_TAIL:
        out.pop()
    if len(out) > 1 and re.fullmatch(r"[a-z]{2}", out[-1]):
        out.pop()
    return out


def parse_address(text: str) -> list[ParsedAddress]:
    """Return plausible readings of ``text`` as a civic address, best first.

    Returns an empty list when the text does not look like an address
    (a bare municipality name, a parcel reference, ...).
    """
    raw = str(text or "").strip()
    if len(raw) < 4 or len(raw) > 120:
        return []
    # Cadastral references and component forms are handled elsewhere.
    if "_" in raw or re.match(r"^[A-Za-z]\d{3}\b", raw):
        return []
    # Province in brackets: "(PA)" -> trailing token.
    cleaned = re.sub(r"\(([^)]*)\)", r" , \1 ", raw)
    # Italian postal codes (CAP, five digits) are not part of the street or the
    # municipality: "via antigone 99, 90151 palermo".
    cleaned = re.sub(r"(?<!\d)\d{5}(?!\d)", " ", cleaned)
    parts = [p.strip() for p in re.split(r"[,;]", cleaned) if p.strip()]
    if not parts:
        return []

    street_tokens = _tokens(parts[0])
    if not street_tokens:
        return []
    first_is_type = street_tokens[0] in STREET_TYPES and (
        street_tokens[0] not in _FIRST_POSITION_ONLY or len(street_tokens) > 1
    )
    street_part, civic, exponent, rest = _split_civic(street_tokens)
    has_comma = len(parts) > 1
    if not (first_is_type or civic):
        return []

    muni_tokens = _strip_province(rest)
    # A comma-separated tail may be "Palermo, PA" or "Palermo": use the first
    # non-empty part as the municipality.
    if has_comma:
        for extra in parts[1:]:
            tail = _strip_province(_tokens(extra))
            if len(tail) == 1 and re.fullmatch(r"[a-z]{2}", tail[0]):
                continue  # a bare province sigla such as "(PA)"
            if tail:
                muni_tokens = tail
                break

    results: list[ParsedAddress] = []

    def add(street_words: list[str], muni: list[str]) -> None:
        raw_street = " ".join(street_words)
        stype, key = normalize_street(raw_street)
        if not key:
            return
        results.append(ParsedAddress(
            street=key, civic=civic, exponent=exponent,
            municipality_hint=" ".join(muni) or None, street_type=stype,
            raw_street=raw_street,
        ))

    if has_comma or civic or not first_is_type:
        add(street_part, muni_tokens)
    else:
        # "via goethe palermo": the municipality is the last 1-3 words and the
        # street is whatever precedes them.  Try the shortest tail first.
        words = street_part
        for take in (1, 2, 3):
            if len(words) - take >= 2:  # keep the type plus >= 1 name word
                add(words[: len(words) - take], words[len(words) - take:])
        if not results:
            add(words, [])
    return results
