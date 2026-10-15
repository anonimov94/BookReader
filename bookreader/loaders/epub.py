"""EPUB loader: spine order, TOC titles, footnote markers removed."""
from __future__ import annotations

import posixpath
import warnings

import ebooklib
from bs4 import BeautifulSoup, Tag, XMLParsedAsHTMLWarning
from ebooklib import epub

from ..models import Book, Chapter
from ..text.cleaner import clean_paragraph, is_speakable

_BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "pre", "dd", "dt", "td", "figcaption"}
_HEADINGS = ("h1", "h2", "h3")
_MIN_CHAPTER_CHARS = 40   # skip near-empty documents (cover pages, separators)

# XHTML is parsed with the lenient HTML parser on purpose: real-world EPUBs are often malformed XML.
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)


def _toc_titles(book: epub.EpubBook) -> dict[str, str]:
    titles: dict[str, str] = {}

    def walk(items):
        for item in items:
            if isinstance(item, tuple):          # (Section, [children])
                section, children = item
                if getattr(section, "href", None):
                    titles.setdefault(section.href.split("#")[0], section.title)
                walk(children)
            elif isinstance(item, epub.Link):
                titles.setdefault(item.href.split("#")[0], item.title)

    walk(book.toc)
    return titles


def _strip_noise(soup: BeautifulSoup) -> None:
    for tag in soup.find_all(["script", "style", "sup", "rt", "rp"]):
        tag.decompose()
    for tag in soup.find_all(attrs={"epub:type": True}):
        if any(t in tag["epub:type"] for t in ("noteref", "footnote", "endnote", "pagebreak", "rearnote")):
            tag.decompose()
    for tag in soup.find_all(attrs={"role": ["doc-noteref", "doc-footnote", "doc-endnote", "doc-pagebreak"]}):
        tag.decompose()


def _paragraphs(soup: BeautifulSoup) -> list[str]:
    body = soup.body or soup
    out: list[str] = []
    for el in body.find_all(True):
        if el.name not in _BLOCK_TAGS and el.name != "div":
            continue
        # Only leaf-level blocks: skip containers that hold other block elements.
        if any(isinstance(c, Tag) and (c.name in _BLOCK_TAGS or c.name == "div") for c in el.descendants):
            continue
        for br in el.find_all("br"):
            br.replace_with("\n")
        text = clean_paragraph(el.get_text())
        if text and is_speakable(text):
            out.append(text)
    return out


def load_epub(path: str) -> Book:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        eb = epub.read_epub(path, {"ignore_ncx": False})

    def meta(name: str) -> str:
        values = eb.get_metadata("DC", name)
        return values[0][0] if values else ""

    toc = _toc_titles(eb)
    book = Book(path=path, title=meta("title") or posixpath.basename(path),
                author=meta("creator"), language=(meta("language") or "")[:2].lower())

    cover_id = None
    for _, attrs in eb.get_metadata("OPF", "cover"):
        cover_id = attrs.get("content")
    for item in eb.get_items_of_type(ebooklib.ITEM_IMAGE):
        if item.get_id() == cover_id or "cover" in item.get_name().lower():
            book.cover = item.get_content()
            break

    for idref, _linear in eb.spine:
        item = eb.get_item_with_id(idref)
        if item is None or item.get_type() != ebooklib.ITEM_DOCUMENT:
            continue
        soup = BeautifulSoup(item.get_content(), "lxml")
        _strip_noise(soup)
        heading = soup.find(_HEADINGS)
        paras = _paragraphs(soup)
        if sum(map(len, paras)) < _MIN_CHAPTER_CHARS:
            continue
        title = toc.get(item.get_name()) or (clean_paragraph(heading.get_text()) if heading else "")
        if title and paras and paras[0].strip().lower() == title.strip().lower():
            paras = paras[1:]       # the heading is spoken separately
        book.chapters.append(Chapter(title=title or f"Rozdział {len(book.chapters) + 1}", paragraphs=paras))
    return book
