"""Small Qt helpers shared by the window and dialogs."""
from __future__ import annotations

import threading
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal


class Worker(QObject):
    """Runs `fn(report, *args)` in a thread; signals arrive in the Qt thread.

    `report(value)` emits `progress`. Keep a reference to the worker until it finishes
    (the parent list in `start` does that).
    """
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(object)

    _alive: list["Worker"] = []

    def __init__(self, fn: Callable, *args):
        super().__init__()
        self.fn, self.args = fn, args
        self.cancel = threading.Event()

    def start(self) -> "Worker":
        Worker._alive.append(self)
        # Released in the Qt thread *after* the result was delivered: dropping the last
        # reference earlier (in the worker thread) discards the queued signal.
        self.done.connect(self._release)
        self.failed.connect(self._release)
        threading.Thread(target=self._run, daemon=True).start()
        return self

    def _release(self, *_) -> None:
        if self in Worker._alive:
            Worker._alive.remove(self)

    def _run(self) -> None:
        try:
            result = self.fn(self.progress.emit, *self.args)
        except Exception as e:
            print(f"[worker] {type(e).__name__}: {e}"[:300])
            self.failed.emit(str(e) or e.__class__.__name__)
            return
        self.done.emit(result)


def run_async(fn: Callable, *args, on_done: Callable[[Any], None] | None = None,
              on_failed: Callable[[str], None] | None = None,
              on_progress: Callable[[Any], None] | None = None) -> Worker:
    w = Worker(fn, *args)
    if on_done:
        w.done.connect(on_done)
    if on_failed:
        w.failed.connect(on_failed)
    if on_progress:
        w.progress.connect(on_progress)
    return w.start()


def human_mb(n: int) -> str:
    return f"{n / 1e6:.0f} MB" if n < 1e9 else f"{n / 1e9:.2f} GB"
