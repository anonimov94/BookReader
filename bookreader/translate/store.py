"""SQLite store of translated paragraphs; re-reading or exporting never re-translates."""
from __future__ import annotations

import hashlib
import sqlite3
import threading

from ..paths import TRANSLATIONS_DB


def _h(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()


class TranslationStore:
    def __init__(self, path: str = TRANSLATIONS_DB):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")       # fast commits, readers never blocked
            self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.execute("""CREATE TABLE IF NOT EXISTS tr (
                book TEXT, chapter INT, paragraph INT, src_hash TEXT, translator TEXT,
                text TEXT, manual INT DEFAULT 0, PRIMARY KEY (book, chapter, paragraph))""")
            self._db.commit()

    def get_book(self, book: str, translator: str) -> dict[tuple[int, int], tuple[str, str]]:
        """(chapter, paragraph) -> (src_hash, text). Manual edits win over any translator."""
        with self._lock:
            rows = self._db.execute("SELECT chapter, paragraph, src_hash, text, translator, manual FROM tr WHERE book=?",
                                    (book,)).fetchall()
        return {(c, p): (h, t) for c, p, h, t, tr, manual in rows if manual or tr == translator}

    def put(self, book: str, chapter: int, paragraph: int, source: str, translator: str, text: str,
            manual: bool = False) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO tr VALUES (?,?,?,?,?,?,?)",
                             (book, chapter, paragraph, _h(source), translator, text, int(manual)))
            self._db.commit()

    def put_many(self, book: str, translator: str, rows: list[tuple[int, int, str, str]]) -> None:
        """rows: (chapter, paragraph, source, text) — one transaction for a whole batch."""
        with self._lock:
            self._db.executemany("INSERT OR REPLACE INTO tr VALUES (?,?,?,?,?,?,0)",
                                 [(book, c, p, _h(s), translator, t) for c, p, s, t in rows])
            self._db.commit()

    def delete_chapter(self, book: str, chapter: int) -> None:
        with self._lock:
            self._db.execute("DELETE FROM tr WHERE book=? AND chapter=? AND manual=0", (book, chapter))
            self._db.commit()

    def delete(self, book: str, chapter: int, paragraph: int) -> None:
        with self._lock:
            self._db.execute("DELETE FROM tr WHERE book=? AND chapter=? AND paragraph=?", (book, chapter, paragraph))
            self._db.commit()

    @staticmethod
    def matches(entry: tuple[str, str] | None, source: str) -> bool:
        return entry is not None and entry[0] == _h(source)
