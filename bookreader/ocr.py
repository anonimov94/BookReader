"""OCR of scanned PDF pages with the Tesseract engine built into PyMuPDF/MuPDF.

Only the language data files are needed (no Tesseract installer): eng/pol `.traineddata`
are downloaded on demand into TESSDATA_DIR. Results are cached per file.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Callable

import pymupdf

from .paths import OCR_CACHE_DIR, TESSDATA_DIR

TESSDATA_URL = "https://github.com/tesseract-ocr/tessdata_best/raw/main/{lang}.traineddata"
LANGS = {"pol": ("polski", 13), "eng": ("angielski", 15)}     # code -> (label, ~MB)


def installed_langs() -> list[str]:
    return sorted(f[:-12] for f in os.listdir(TESSDATA_DIR) if f.endswith(".traineddata"))


def ocr_language() -> str:
    """'pol+eng' when both are present; whatever is installed otherwise; '' if none."""
    have = [l for l in ("pol", "eng") if l in installed_langs()]
    return "+".join(have)


def _cache_file(pdf_path: str) -> str:
    st = os.stat(pdf_path)
    key = hashlib.sha1(f"{os.path.abspath(pdf_path)}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()
    return os.path.join(OCR_CACHE_DIR, key + ".json")


def ocr_pages(pdf_path: str, doc: pymupdf.Document, pages: list[int],
              progress: Callable[[int, int], None] | None = None, dpi: int = 300) -> dict[int, list[tuple]]:
    """page -> list of (x0, y0, x1, y1, text, block_no, block_type) like page.get_text('blocks')."""
    lang = ocr_language()
    if not lang or not pages:
        return {}
    cache_path = _cache_file(pdf_path)
    try:
        with open(cache_path, encoding="utf-8") as f:
            cache = {int(k): [tuple(b) for b in v] for k, v in json.load(f).items()}
    except (OSError, ValueError):
        cache = {}
    todo = [p for p in pages if p not in cache]
    for n, pno in enumerate(todo, 1):
        page = doc[pno]
        tp = page.get_textpage_ocr(language=lang, dpi=dpi, full=True, tessdata=TESSDATA_DIR)
        cache[pno] = [tuple(b) for b in page.get_text("blocks", textpage=tp)]
        if progress:
            progress(n, len(todo))
        if n % 10 == 0 or n == len(todo):          # save progress: a long OCR can be resumed
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(cache, f)
    return {p: cache[p] for p in pages if p in cache}
