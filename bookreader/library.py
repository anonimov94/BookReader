"""Per-book state (position, bookmarks, translation, glossary, pronunciation) and app settings."""
from __future__ import annotations

import json
import os
import time

from .paths import LIBRARY_FILE


class Library:
    def __init__(self, path: str = LIBRARY_FILE):
        self.path = path
        try:
            with open(path, encoding="utf-8") as f:
                self.data = json.load(f)
        except (OSError, ValueError):
            self.data = {}
        self.data.setdefault("books", {})
        self.data.setdefault("settings", {})

    def save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    # ------------------------------------------------------------ books
    def book(self, book_path: str) -> dict:
        """Mutable per-book record; created on first access."""
        rec = self.data["books"].setdefault(os.path.abspath(book_path), {})
        rec.setdefault("bookmarks", [])
        rec.setdefault("pronunciation", [])
        return rec

    def position(self, book_path: str) -> tuple[int, int, int]:
        """(chapter, paragraph, sentence-in-paragraph). Stable across translation on/off."""
        pos = self.book(book_path).get("position")
        return tuple(pos) if isinstance(pos, list) and len(pos) == 3 else (0, -1, 0)

    def set_position(self, book_path: str, position: tuple[int, int, int], title: str = "") -> None:
        rec = self.book(book_path)
        rec["position"] = list(position)
        rec["title"] = title or rec.get("title", "")
        rec["opened"] = time.time()

    def recent(self, limit: int = 10) -> list[tuple[str, str]]:
        books = [(p, r) for p, r in self.data["books"].items() if r.get("opened") and os.path.exists(p)]
        books.sort(key=lambda pr: pr[1]["opened"], reverse=True)
        return [(p, r.get("title") or os.path.basename(p)) for p, r in books[:limit]]

    @property
    def last_book(self) -> str | None:
        return self.data.get("last_book")

    @last_book.setter
    def last_book(self, path: str) -> None:
        self.data["last_book"] = os.path.abspath(path)

    # ------------------------------------------------------------ settings
    @property
    def settings(self) -> dict:
        return self.data["settings"]

    @property
    def presets(self) -> dict:
        """name -> {"voice_id": ..., "settings": {...}}"""
        return self.settings.setdefault("presets", {})
