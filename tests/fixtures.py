"""Builds small EPUB/PDF books with original sample text for tests."""
from __future__ import annotations

import os

import pymupdf
from ebooklib import epub

FONT = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "arial.ttf")   # has Polish glyphs

CHAPTERS = [
    ("Rozdział I", [
        "Poranek w Dolinie Mgieł był chłodny. Anna otworzyła okno i spojrzała na las, "
        "np. na stare dęby przy drodze, tzw. Strażników.",
        "Dr Nowak przyjechał ok. godz. 8 rano, tj. wcześniej niż zwykle. W 1985 r. zbudował tu "
        "pierwszą stację pomiarową<sup>1</sup>.",
    ]),
    ("Rozdział II", [
        "Wieczorem rozpalili ognisko. „Czy to już koniec?” — zapytała Anna. Nikt nie odpowiedział.",
    ]),
]


def make_epub(path: str) -> str:
    book = epub.EpubBook()
    book.set_identifier("test-book")
    book.set_title("Dolina Mgieł")
    book.set_language("pl")
    book.add_author("Autor Testowy")
    items = []
    for i, (title, paras) in enumerate(CHAPTERS, 1):
        ch = epub.EpubHtml(title=title, file_name=f"ch{i}.xhtml", lang="pl")
        body = "".join(f"<p>{p}</p>" for p in paras)
        ch.content = f"<html><body><h2>{title}</h2>{body}</body></html>"
        book.add_item(ch)
        items.append(ch)
    book.toc = items
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", *items]
    epub.write_epub(path, book)
    return path


def make_pdf(path: str) -> str:
    """Two chapters over 4 pages, running header, page numbers, a hyphenated line break
    and a paragraph that continues across a page break."""
    doc = pymupdf.open()
    pages_text = [
        ["Rozdział I", "Poranek w Dolinie Mgieł był chłodny. Anna otworzyła okno i spoj-\nrzała na las."],
        ["Dr Nowak przyjechał wcześniej niż zwykle i od razu poszedł do stacji, gdzie"],
        ["czekały na niego nowe pomiary z całej nocy."],
        ["Rozdział II", "Wieczorem rozpalili ognisko. Nikt nie odpowiedział."],
    ]
    for n, blocks in enumerate(pages_text, 1):
        page = doc.new_page(width=420, height=595)
        page.insert_font(fontname="F0", fontfile=FONT)
        page.insert_text((40, 30), "Dolina Mgieł — powieść", fontname="F0", fontsize=8)
        y = 90
        for b in blocks:
            rect = pymupdf.Rect(40, y, 380, y + 120)
            page.insert_textbox(rect, b, fontname="F0", fontsize=11)
            y += 130
        page.insert_text((200, 575), str(n), fontname="F0", fontsize=8)
    doc.set_toc([[1, "Rozdział I", 1], [1, "Rozdział II", 4]])
    doc.save(path)
    doc.close()
    return path


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(__file__), "samples")
    os.makedirs(out, exist_ok=True)
    print(make_epub(os.path.join(out, "dolina.epub")), make_pdf(os.path.join(out, "dolina.pdf")))
