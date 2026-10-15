"""Split chapters into speakable segments (sentences) with paragraph offsets."""
from __future__ import annotations

import re
from typing import Callable

from ..models import Book, Segment, SegmentKind

# Abbreviations after which a period does not end a sentence (lowercase, no dot).
ABBREVIATIONS = {
    # Polish
    "np", "tzw", "itd", "itp", "m.in", "ok", "dr", "prof", "mgr", "inż", "hab", "ul", "al",
    "nr", "str", "s", "godz", "tj", "zob", "wg", "św", "ks", "płk", "gen", "por", "kpt",
    "tys", "mln", "mld", "zł", "gr", "r", "w", "pt", "ww", "j", "tzn", "jw", "red", "wyd",
    "cdn", "ang", "łac", "niem", "fr", "ros", "pol", "min", "max", "sek", "cd", "pn", "pd", "wsch", "zach",
    # English
    "mr", "mrs", "ms", "dr", "st", "jr", "sr", "vs", "etc", "e.g", "i.e", "no", "vol", "ch",
    "fig", "approx", "dept", "inc", "ltd", "co", "mt", "ft", "lt", "col", "sgt", "capt",
}

# Candidate sentence end: terminal punctuation, optional closing quotes/brackets, whitespace.
_BOUNDARY = re.compile(r"[.!?…]+[\"'»”’)\]]*\s+")
_NEXT_STARTS_SENTENCE = re.compile(r"[\"'„«“(\[—–-]*\s*[A-ZĄĆĘŁŃÓŚŹŻÀ-Ý0-9]")
_WORD_BEFORE = re.compile(r"([\w.]+)\.$")

MAX_SEGMENT_CHARS = 350
_SOFT_SPLIT = re.compile(r"[;:,—–]\s+")


def split_sentences(text: str) -> list[tuple[int, int]]:
    """Return (start, end) spans of sentences in `text`."""
    spans: list[tuple[int, int]] = []
    start = 0
    for m in _BOUNDARY.finditer(text):
        end_punct = m.group().rstrip()
        cut = m.start() + len(end_punct)
        rest = text[m.end():]
        if not rest or not _NEXT_STARTS_SENTENCE.match(rest):
            continue
        if end_punct.startswith(".") and len(end_punct.rstrip("\"'»”’)]")) == 1:
            wm = _WORD_BEFORE.search(text[start:m.start() + 1])
            if wm:
                word = wm.group(1).lower()
                # abbreviation or a single initial ("J. R. R. Tolkien")
                if word in ABBREVIATIONS or (len(word) == 1 and word.isalpha()):
                    continue
        spans.append((start, cut))
        start = m.end()
    if start < len(text) and text[start:].strip():
        spans.append((start, len(text.rstrip())))
    return [s for s in (_split_long(text, a, b) for a, b in spans) for s in s]


def _split_long(text: str, a: int, b: int) -> list[tuple[int, int]]:
    """Break very long sentences at clause punctuation so TTS latency stays low."""
    if b - a <= MAX_SEGMENT_CHARS:
        return [(a, b)]
    out: list[tuple[int, int]] = []
    pos = a
    while b - pos > MAX_SEGMENT_CHARS:
        window = text[pos:pos + MAX_SEGMENT_CHARS]
        cuts = [m.end() for m in _SOFT_SPLIT.finditer(window) if m.end() > MAX_SEGMENT_CHARS // 3]
        if not cuts:
            space = window.rfind(" ", MAX_SEGMENT_CHARS // 2)
            cuts = [space + 1] if space > 0 else [MAX_SEGMENT_CHARS]
        cut = pos + cuts[-1]
        out.append((pos, len(text[:cut].rstrip())))
        pos = cut
    out.append((pos, b))
    return out


def segment_paragraph(chapter: int, paragraph: int, text: str | None, source: str = "") -> list[Segment]:
    """Segments of one paragraph. paragraph == -1 is the chapter title.
    text None means "translation pending": one PENDING placeholder carrying the source text."""
    if text is None:
        return [Segment(chapter, paragraph, 0, len(source), source, SegmentKind.PENDING, last_in_paragraph=True)]
    if paragraph == -1:
        return [Segment(chapter, -1, 0, len(text), text, SegmentKind.HEADING, last_in_paragraph=True)]
    spans = split_sentences(text) or [(0, len(text))]
    return [Segment(chapter, paragraph, a, b, text[a:b], last_in_paragraph=(k == len(spans) - 1))
            for k, (a, b) in enumerate(spans)]


def segment_book(book: Book, text_for: Callable[[int, int, str], str | None] | None = None) -> list[Segment]:
    """`text_for(chapter, paragraph, source)` lets a translation replace each paragraph
    (return None while it is still pending). Without it the original text is used."""
    text_for = text_for or (lambda c, p, s: s)
    segments: list[Segment] = []
    for ci, chapter in enumerate(book.chapters):
        if chapter.title:
            segments += segment_paragraph(ci, -1, text_for(ci, -1, chapter.title), chapter.title)
        for pi, para in enumerate(chapter.paragraphs):
            segments += segment_paragraph(ci, pi, text_for(ci, pi, para), para)
    return segments
