"""Background translation that runs ahead of the reader.

The worker translates paragraphs in book order starting at the reader's position
(`focus`), but only as far ahead as `lookahead_chars` of source text (about the next
couple of minutes of reading) — not the whole book. The first batch after a jump is a
single paragraph so reading can start quickly; later batches are larger for better
context. Results are stored in SQLite.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

from ..models import Book
from .base import TranslationContext, Translator
from .glossary import Glossary
from .store import TranslationStore

# How far ahead of the reader to translate, in characters of source text
# (a narrator reads ~15 characters per second, so 2000 ≈ 2 minutes).
LOOKAHEAD_CHOICES = [
    (0, "tylko bieżący akapit"),
    (1000, "~1 minuta czytania"),
    (2000, "~2 minuty czytania (zalecane)"),
    (5000, "~5 minut czytania"),
    (15000, "~15 minut czytania"),
    (-1, "cały bieżący rozdział"),
]
DEFAULT_LOOKAHEAD = 2000
BATCH_CHARS = 1800
BATCH_MAX = 6


class TranslationService:
    def __init__(self, book: Book, book_key: str, translator: Translator, glossary: Glossary,
                 store: TranslationStore | None = None, source_lang: str = "en",
                 lookahead_chars: int = DEFAULT_LOOKAHEAD):
        self.book, self.book_key, self.translator, self.glossary = book, book_key, translator, glossary
        self.store = store or TranslationStore()
        self.source_lang = source_lang
        self.lookahead_chars = lookahead_chars
        self.translate_all = False        # export: translate everything that is still missing
        self.need_glossary = not glossary.entries

        self.on_paragraphs: Callable[[list[tuple[int, int, str]]], None] = lambda items: None
        self.on_status: Callable[[str], None] = lambda s: None
        self.on_error: Callable[[str], None] = lambda s: None
        self.on_glossary: Callable[[Glossary], None] = lambda g: None

        self.order: list[tuple[int, int]] = []
        for ci, ch in enumerate(book.chapters):
            if ch.title:
                self.order.append((ci, -1))
            self.order += [(ci, pi) for pi in range(len(ch.paragraphs))]
        self._pos = {k: i for i, k in enumerate(self.order)}
        stored = self.store.get_book(book_key, translator.key)
        self.done: dict[tuple[int, int], str] = {
            k: stored[k][1] for k in self.order if TranslationStore.matches(stored.get(k), self.source(*k))}

        self._cursor = 0
        self._quick = True
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------ queries
    def source(self, ci: int, pi: int) -> str:
        ch = self.book.chapters[ci]
        return ch.title if pi == -1 else ch.paragraphs[pi]

    def text_for(self, ci: int, pi: int, source: str = "") -> str | None:
        return self.done.get((ci, pi))

    def progress(self) -> tuple[int, int]:
        return len(self.done), len(self.order)

    def chapter_done(self, ci: int) -> bool:
        return all(k in self.done for k in self.order if k[0] == ci)

    # ------------------------------------------------------------ control
    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True, name="translator")
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def focus(self, ci: int, pi: int) -> None:
        """The reader is here: translate from this point on."""
        i = self._pos.get((ci, pi))
        if i is None:
            i = next((k for k, (c, p) in enumerate(self.order) if c >= ci), 0)
        if abs(i - self._cursor) > 2 or (ci, pi) not in self.done:
            self._quick = (ci, pi) not in self.done
        self._cursor = i
        self._wake.set()

    def retranslate(self, ci: int, pi: int | None = None) -> list[tuple[int, int]]:
        keys = [k for k in self.order if k[0] == ci and (pi is None or k[1] == pi)]
        if pi is None:
            self.store.delete_chapter(self.book_key, ci)
        for k in keys:
            if pi is not None:
                self.store.delete(self.book_key, *k)
            self.done.pop(k, None)
        self.focus(*keys[0]) if keys else None
        return keys

    def set_manual(self, ci: int, pi: int, text: str) -> None:
        self.store.put(self.book_key, ci, pi, self.source(ci, pi), self.translator.key, text, manual=True)
        self.done[(ci, pi)] = text
        self.on_paragraphs([(ci, pi, text)])

    def set_glossary(self, g: Glossary) -> None:
        self.glossary = g
        self.need_glossary = False

    # ------------------------------------------------------------ worker
    def _window(self) -> tuple[int, int]:
        """[begin, end) of paragraphs that should be translated now. Paragraphs from the
        reader's position onwards come first; [begin, cursor) is filled afterwards."""
        n = len(self.order)
        cur = min(self._cursor, max(0, n - 1))
        if self.translate_all:
            return 0, n
        if self.lookahead_chars < 0:                       # whole current chapter
            ci = self.order[cur][0]
            begin = end = cur
            while begin > 0 and self.order[begin - 1][0] == ci:
                begin -= 1
            while end < n and self.order[end][0] == ci:
                end += 1
            return begin, end
        end, chars = cur + 1, 0                             # the current paragraph always counts
        while end < n and chars < self.lookahead_chars:
            chars += len(self.source(*self.order[end]))
            end += 1
        return cur, min(end, n)

    def _next_batch(self) -> list[tuple[int, int]]:
        begin, end = self._window()
        cur = max(begin, min(self._cursor, end))
        start = next((i for i in range(cur, end) if self.order[i] not in self.done), None)
        if start is None:
            start = next((i for i in range(begin, cur) if self.order[i] not in self.done), None)
        if start is None:
            return []
        batch, chars, ci = [], 0, self.order[start][0]
        max_n = 1 if self._quick else BATCH_MAX
        for i in range(start, end):
            k = self.order[i]
            if k in self.done or k[0] != ci or len(batch) >= max_n:
                break
            n = len(self.source(*k))
            if batch and chars + n > BATCH_CHARS:
                break
            batch.append(k)
            chars += n
        self._quick = False
        return batch

    def _context(self, first: tuple[int, int]) -> TranslationContext:
        i = self._pos[first]
        prev = [(self.source(*k), self.done[k]) for k in self.order[max(0, i - 2):i] if k in self.done]
        return TranslationContext(self.glossary, prev, self.book.title, self.source_lang, "pl")

    def _run(self) -> None:
        failures = 0
        while not self._stop.is_set():
            if self.need_glossary and hasattr(self.translator, "build_glossary"):
                self.need_glossary = False
                try:
                    self.on_status("Analizuję postaci i nazwy w książce…")
                    paras = [self.source(*k) for k in self.order[:400]]
                    self.glossary = self.translator.build_glossary(paras, self.glossary)
                    self.on_glossary(self.glossary)
                except Exception as e:
                    self.on_error(f"Nie udało się zbudować słownika postaci: {e}")

            batch = self._next_batch()
            if not batch:
                done, total = self.progress()
                self.on_status(f"Tłumaczenie: {done}/{total} akapitów" + (" — gotowe" if done == total else ""))
                self._wake.wait(1.0)
                self._wake.clear()
                continue
            sources = [self.source(*k) for k in batch]
            try:
                t0 = time.time()
                result = self.translator.translate(sources, self._context(batch[0]))
                failures = 0
            except Exception as e:
                failures += 1
                self.on_error(f"Błąd tłumaczenia ({self.translator.name}): {e}")
                if failures >= 3:
                    self.on_status("Tłumaczenie wstrzymane po błędach — zmień ustawienia lub przeskocz dalej.")
                    self._wake.clear()
                    self._wake.wait()
                    failures = 0
                else:
                    time.sleep(3 * failures)
                continue
            items = []
            for k, text in zip(batch, result):
                self.done[k] = text
                items.append((k[0], k[1], text))
            self.store.put_many(self.book_key, self.translator.key,
                                [(ci, pi, src, text) for (ci, pi, text), src in zip(items, sources)])
            self.on_paragraphs(items)
            done, total = self.progress()
            self.on_status(f"Tłumaczenie: {done}/{total} akapitów ({time.time() - t0:.1f} s / {len(batch)} akap.)")
