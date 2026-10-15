from __future__ import annotations

import hashlib
import os
import pickle
from typing import Callable

from ..models import Book
from ..paths import APP_DIR
from ..text.langdetect import detect_language


# Bump when parsing changes so cached books are re-parsed.
PARSER_VERSION = 3
BOOK_CACHE_DIR = os.path.join(APP_DIR, "cache", "books")


def _cache_path(path: str) -> str:
    st = os.stat(path)
    key = f"{PARSER_VERSION}|{os.path.abspath(path)}|{st.st_size}|{st.st_mtime_ns}"
    return os.path.join(BOOK_CACHE_DIR, hashlib.sha1(key.encode()).hexdigest() + ".pickle")


def load_book(path: str, progress: Callable[[str], None] | None = None) -> Book:
    """Load an EPUB/PDF; the language is detected from the text when metadata lacks it.
    Parsed books are cached on disk, so reopening a large PDF is instant."""
    cache = _cache_path(path)
    try:
        with open(cache, "rb") as f:
            book = pickle.load(f)
        book.path = path
        return book
    except (OSError, pickle.PickleError, EOFError, AttributeError, ImportError):
        pass
    book = _load(path, progress)
    sample = " ".join(p for c in book.chapters[:3] for p in c.paragraphs[:40])
    detected = detect_language(sample)
    if detected:
        book.language = detected        # text beats metadata (EPUBs often say "en" by default)
    if not book.needs_ocr_pages:        # scans may be re-read after OCR data is installed
        try:
            os.makedirs(BOOK_CACHE_DIR, exist_ok=True)
            with open(cache + ".tmp", "wb") as f:
                pickle.dump(book, f, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(cache + ".tmp", cache)
        except OSError:
            pass
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
