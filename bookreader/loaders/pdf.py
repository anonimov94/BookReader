"""PDF loader: paragraph reconstruction, header/footer removal, TOC chapters."""
from __future__ import annotations

import os
import re
from collections import Counter

from typing import Callable

import pymupdf

from ..models import Book, Chapter
from ..ocr import ocr_language, ocr_pages
from ..text.cleaner import clean_paragraph, is_speakable

_MARGIN = 0.09          # top/bottom fraction of the page treated as header/footer zone
_REPEAT_RATIO = 0.3     # a margin line repeating on this share of pages is a running header
_PAGES_PER_CHUNK = 12   # chapter size when the PDF has no outline
_PAGE_NUMBER = re.compile(r"^\s*(?:[-–—]?\s*\d{1,4}\s*[-–—]?|[ivxlcdm]{1,6}|(?:str\.|strona|page|s\.)\s*\d+(?:\s*(?:z|of|/)\s*\d+)?)\s*$", re.I)
_DIGITS = re.compile(r"\d+")
_SENTENCE_END = re.compile(r"[.!?…:\"»”)]$")


def _signature(text: str) -> str:
    return _DIGITS.sub("#", text.strip().lower())


def _page_blocks(page: pymupdf.Page, raw: list[tuple] | None = None) -> list[tuple[float, float, str]]:
    """(y0, y1, text) for every text block, in content-stream reading order."""
    blocks = []
    for b in (raw if raw is not None else page.get_text("blocks")):
        x0, y0, x1, y1, text, _no, btype = b[:7]
        if btype == 0 and text.strip():
            blocks.append((y0, y1, text))
    return blocks


def load_pdf(path: str, progress: Callable[[str], None] | None = None) -> Book:
    doc = pymupdf.open(path)
    meta = doc.metadata or {}
    book = Book(path=path, title=(meta.get("title") or "").strip() or os.path.splitext(os.path.basename(path))[0],
                author=(meta.get("author") or "").strip())

    pages = [_page_blocks(p) for p in doc]
    heights = [p.rect.height for p in doc]

    # Scanned pages (images, no text layer) go through OCR when language data is installed.
    scanned = [i for i, (blocks, p) in enumerate(zip(pages, doc)) if not blocks and p.get_images()]
    if scanned and ocr_language():
        report = (lambda n, t: progress(f"OCR: strona {n}/{t}")) if progress else None
        for pno, raw in ocr_pages(path, doc, scanned, report).items():
            pages[pno] = _page_blocks(doc[pno], raw)
    book.needs_ocr_pages = [i for i in scanned if not pages[i]]

    # Running headers/footers: margin lines whose digit-insensitive text repeats.
    margin_sigs: Counter[str] = Counter()
    for blocks, h in zip(pages, heights):
        seen = {_signature(t) for y0, y1, t in blocks if y1 < h * _MARGIN or y0 > h * (1 - _MARGIN)}
        margin_sigs.update(seen)
    min_repeats = max(3, int(len(pages) * _REPEAT_RATIO))
    running = {s for s, n in margin_sigs.items() if n >= min_repeats}

    page_paras: list[list[str]] = []
    for i, (blocks, h) in enumerate(zip(pages, heights)):
        paras = []
        for y0, y1, text in blocks:
            in_margin = y1 < h * _MARGIN or y0 > h * (1 - _MARGIN)
            if in_margin and (_signature(text) in running or _PAGE_NUMBER.match(text)):
                continue
            cleaned = clean_paragraph(text)
            if cleaned and is_speakable(cleaned):
                paras.append(cleaned)
        page_paras.append(paras)

    # Merge paragraphs that continue across a page break (no sentence-final punctuation).
    for i in range(1, len(page_paras)):
        prev = next((p for p in reversed(page_paras[:i]) if p), None)
        cur = page_paras[i]
        if prev and cur and not _SENTENCE_END.search(prev[-1]) and cur[0][:1].islower():
            joined = prev[-1][:-1] + cur[0] if prev[-1].endswith("-") else prev[-1] + " " + cur[0]
            prev[-1] = joined
            cur.pop(0)

    toc = [(lvl, title, page) for lvl, title, page in doc.get_toc() if page >= 1]
    top_level = min((lvl for lvl, _, _ in toc), default=1)
    starts = [(t.strip(), p - 1) for lvl, t, p in toc if lvl == top_level]
    if len(starts) >= 2:
        if starts[0][1] > 0:
            starts.insert(0, ("Początek", 0))
        for k, (title, first) in enumerate(starts):
            last = starts[k + 1][1] if k + 1 < len(starts) else len(page_paras)
            paras = [p for pg in page_paras[first:last] for p in pg]
            if paras and paras[0].lower() == title.lower():
                paras = paras[1:]
            if paras:
                book.chapters.append(Chapter(title=title, paragraphs=paras))
    else:
        for first in range(0, len(page_paras), _PAGES_PER_CHUNK):
            last = min(first + _PAGES_PER_CHUNK, len(page_paras))
            paras = [p for pg in page_paras[first:last] for p in pg]
            if paras:
                book.chapters.append(Chapter(title=f"Strony {first + 1}–{last}", paragraphs=paras))
    doc.close()
    return book
