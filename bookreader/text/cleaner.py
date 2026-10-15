"""Cleanup of raw text extracted from books, before segmentation."""
from __future__ import annotations

import re
import unicodedata

_LOWER = "a-ząćęłńóśźżäöüéèàçñ"

# "przy-\nkład" -> "przykład" ; keep real hyphenated compounds like "biało-\nczerwony"
# when the next part starts with an uppercase letter.
_HYPHEN_BREAK = re.compile(rf"([{_LOWER}])[-­]\s*\n\s*([{_LOWER}])")
_SOFT_HYPHEN = re.compile("­")
_WS = re.compile(r"[ \t  -​  　]+")
_LINEBREAKS = re.compile(r"\s*\n\s*")
_ZERO_WIDTH = re.compile("[​-‍﻿]")


def dehyphenate(text: str) -> str:
    return _HYPHEN_BREAK.sub(r"\1\2", text)


def clean_paragraph(text: str) -> str:
    """Normalize a paragraph to a single line of clean text."""
    text = unicodedata.normalize("NFC", text)
    text = _ZERO_WIDTH.sub("", text)
    text = dehyphenate(text)
    text = _SOFT_HYPHEN.sub("", text)
    text = _LINEBREAKS.sub(" ", text)
    text = _WS.sub(" ", text)
    text = text.replace("ﬁ", "fi").replace("ﬂ", "fl")
    return text.strip()


def is_speakable(text: str) -> bool:
    """False for paragraphs that contain no letters or digits (ornaments, '* * *')."""
    return any(ch.isalnum() for ch in text)
