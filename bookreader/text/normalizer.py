"""Turn display text into text a speech engine pronounces well.

Neural engines (Edge) inflect numbers correctly by themselves, so number
expansion is optional and only used for engines that need it (Piper, XTTS).
"""
from __future__ import annotations

import re

from num2words import num2words

_ABBREV_PL = {
    "np.": "na przykład", "tzw.": "tak zwany", "itd.": "i tak dalej", "itp.": "i tym podobne",
    "m.in.": "między innymi", "ok.": "około", "tj.": "to jest", "tzn.": "to znaczy",
    "zob.": "zobacz", "wg": "według", "godz.": "godzina", "ul.": "ulica", "al.": "aleja",
    "nr": "numer", "str.": "strona", "prof.": "profesor", "dr": "doktor", "mgr": "magister",
    "inż.": "inżynier", "św.": "święty", "ks.": "ksiądz", "gen.": "generał", "płk": "pułkownik",
    "tys.": "tysięcy", "mln": "milionów", "mld": "miliardów", "zł": "złotych",
    "p.n.e.": "przed naszą erą", "n.e.": "naszej ery", "jw.": "jak wyżej", "ww.": "wyżej wymieniony",
    "cdn.": "ciąg dalszy nastąpi",
}
_ABBREV_EN = {
    "e.g.": "for example", "i.e.": "that is", "etc.": "et cetera", "vs.": "versus",
    "Mr.": "Mister", "Mrs.": "Missus", "Dr.": "Doctor", "St.": "Saint", "approx.": "approximately",
}
_SYMBOLS_PL = {"%": " procent", "§": "paragraf ", "&": " i ", "°C": " stopni Celsjusza", "°": " stopni", "+": " plus ", "=": " równa się "}
_SYMBOLS_EN = {"%": " percent", "§": "section ", "&": " and ", "°C": " degrees Celsius", "°": " degrees", "+": " plus ", "=": " equals "}

# "1985 r." / "XIX w." are only expanded right after a number, never on their own.
_YEAR_CENTURY_PL = re.compile(r"\b(\d{1,4}|[MDCLXVI]+)\s?(r|w)\.(?=\s|$|[,;:)])")
_ROMAN_VALUES = {"M": 1000, "D": 500, "C": 100, "L": 50, "X": 10, "V": 5, "I": 1}
_CHAPTER_WORDS = re.compile(r"\b(Rozdział|Część|Tom|Księga|Chapter|Part|Book|Volume)\s+([MDCLXVI]+|\d+)\b")
_NUMBER = re.compile(r"(?<![\w.,])(\d{1,3}(?:[  ]\d{3})+|\d+)(?:[,.](\d+))?(?![\w])")
_SPACES = re.compile(r"\s{2,}")


def _roman_to_int(s: str) -> int:
    total = 0
    for i, ch in enumerate(s):
        v = _ROMAN_VALUES[ch]
        total += -v if i + 1 < len(s) and _ROMAN_VALUES[s[i + 1]] > v else v
    return total


def _abbrev_pattern(table: dict[str, str]) -> re.Pattern:
    keys = sorted(table, key=len, reverse=True)
    return re.compile(r"(?<![\w.])(" + "|".join(re.escape(k) for k in keys) + r")(?![\w])", re.I)


def _expand(table: dict[str, str], m: re.Match) -> str:
    """Look up case-insensitively and keep the capital letter: "Dr" -> "Doktor"."""
    raw = m.group(1)
    word = table.get(raw) or next((v for k, v in table.items() if k.lower() == raw.lower()), raw)
    return word[:1].upper() + word[1:] if raw[:1].isupper() else word


_ABBREV_RE = {"pl": (_abbrev_pattern(_ABBREV_PL), _ABBREV_PL), "en": (_abbrev_pattern(_ABBREV_EN), _ABBREV_EN)}


def normalize(text: str, lang: str = "pl", expand_numbers: bool = False) -> str:
    lang = "en" if lang.startswith("en") else "pl"
    w2n_lang = lang

    def chapter_number(m: re.Match) -> str:
        raw = m.group(2)
        n = int(raw) if raw.isdigit() else _roman_to_int(raw.upper())
        return f"{m.group(1)} {num2words(n, lang=w2n_lang, to='ordinal' if lang == 'pl' else 'cardinal')}"

    text = _CHAPTER_WORDS.sub(chapter_number, text)

    if lang == "pl":
        text = _YEAR_CENTURY_PL.sub(lambda m: f"{m.group(1)} {'roku' if m.group(2) == 'r' else 'wieku'}", text)

    pattern, table = _ABBREV_RE[lang]
    text = pattern.sub(lambda m: _expand(table, m), text)

    for sym, word in (_SYMBOLS_PL if lang == "pl" else _SYMBOLS_EN).items():
        text = text.replace(sym, word)

    if expand_numbers:
        def number(m: re.Match) -> str:
            whole = int(re.sub(r"\D", "", m.group(1)))
            spoken = num2words(whole, lang=w2n_lang)
            if m.group(2):
                comma = " przecinek " if lang == "pl" else " point "
                spoken += comma + " ".join(num2words(int(d), lang=w2n_lang) for d in m.group(2))
            return spoken
        text = _NUMBER.sub(number, text)

    return _SPACES.sub(" ", text).strip()
