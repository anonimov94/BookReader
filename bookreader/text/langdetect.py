"""Tiny Polish/English detector: diacritics and function words are enough for books."""
from __future__ import annotations

import re

_PL_WORDS = {"i", "w", "na", "się", "nie", "że", "z", "do", "to", "jest", "jak", "co", "ale", "po", "o",
             "tak", "jego", "już", "był", "była", "było", "przez", "od", "za", "czy", "tylko", "mnie"}
_EN_WORDS = {"the", "and", "of", "to", "a", "in", "is", "that", "it", "was", "he", "she", "for", "on",
             "with", "as", "his", "her", "at", "be", "had", "you", "not", "but", "they", "this"}
_WORD = re.compile(r"[^\W\d_]+")
_PL_CHARS = set("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ")


def detect_language(text: str) -> str:
    """Returns "pl", "en" or "" when unsure."""
    sample = text[:20000]
    words = [w.lower() for w in _WORD.findall(sample)]
    if len(words) < 20:
        return ""
    pl = sum(w in _PL_WORDS for w in words) + 0.5 * sum(ch in _PL_CHARS for ch in sample)
    en = sum(w in _EN_WORDS for w in words)
    if pl > en * 1.3:
        return "pl"
    if en > pl * 1.3:
        return "en"
    return ""
