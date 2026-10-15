from __future__ import annotations

import os
from typing import Callable

from ..models import Book
from ..text.langdetect import detect_language


def load_book(path: str, progress: Callable[[str], None] | None = None) -> Book:
    """Load an EPUB/PDF; the language is detected from the text when metadata lacks it."""
    book = _load(path, progress)
    sample = " ".join(p for c in book.chapters[:3] for p in c.paragraphs[:40])
    detected = detect_language(sample)
    if detected:
        book.language = detected        # text beats metadata (EPUBs often say "en" by default)
    return book


def _load(path: str, progress) -> Book:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".epub":
        from .epub import load_epub
        return load_epub(path)
    if ext == ".pdf":
        from .pdf import load_pdf
        return load_pdf(path, progress)
    raise ValueError(f"Nieobsługiwany format: {ext} (obsługiwane: .epub, .pdf)")
