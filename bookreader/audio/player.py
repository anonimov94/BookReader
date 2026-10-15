"""Sentence-by-sentence reader with background prefetch and live DSP.

Threads:
  * synthesis pool  - generates upcoming sentences ahead of the reader
  * playback thread - takes the current sentence, applies speed/pitch, then
                      streams small blocks through the EQ/volume chain, so
                      slider changes are heard within ~50 ms.

Segments of kind PENDING stand for paragraphs whose translation has not
arrived yet; the reader waits on them and `replace_paragraph` swaps them for
real sentences while reading continues.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait
from typing import Callable

import numpy as np
import sounddevice as sd

from ..models import Segment, SegmentKind
from ..text.normalizer import normalize
from ..tts.base import SAMPLE_RATE, Voice
from ..tts.cache import AudioCache
from ..tts.registry import EngineRegistry
from .dsp import DSPChain, apply_prosody

BLOCK = 1024
PREFETCH_MIN, PREFETCH_MAX = 4, 10     # sentences prepared ahead; grows when synthesis is slow
WORKERS = 4

Position = tuple[int, int, int]      # (chapter, paragraph, sentence number within paragraph)


class State:
    STOPPED = "stopped"
    PLAYING = "playing"
    PAUSED = "paused"
    LOADING = "loading"


def _key(seg: Segment) -> tuple:
    return seg.chapter, seg.paragraph, seg.start, seg.text


class Player:
    def __init__(self, registry: EngineRegistry, voice: Voice, dsp: DSPChain, cache: AudioCache,
                 lang: str = "pl", device: int | str | None = None):
        self.registry, self.voice, self.dsp, self.cache, self.lang = registry, voice, dsp, cache, lang
        self.device = device
        self.segments: list[Segment] = []
        self.index = 0
        self.state = State.STOPPED
        self.text_transform: Callable[[str], str] = lambda t: t     # pronunciation dictionary
        self.pause_before_chapter: int | None = None                # sleep timer "end of chapter"

        # Callbacks are invoked from worker threads; the GUI must marshal them to its own thread.
        self.on_segment: Callable[[int], None] = lambda i: None
        self.on_state: Callable[[str], None] = lambda s: None
        self.on_error: Callable[[Exception], None] = lambda e: None
        self.on_finished: Callable[[], None] = lambda: None
        self.on_paused_by_timer: Callable[[], None] = lambda: None

        self._pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="tts")
        self._prefetch_n = PREFETCH_MIN
        self._para_start: dict[tuple[int, int], int] | None = None   # (chapter, paragraph) -> first index
        self._futures: dict[tuple, Future] = {}
        self._futures_lock = threading.Lock()
        self._seg_lock = threading.RLock()
        self._generation = 0          # bumped on seek/stop/voice change to abort the current sentence
        self._resume = threading.Event()
        self._resume.set()
        self._thread: threading.Thread | None = None

    @property
    def engine(self):
        return self.registry.engine(self.voice)

    # ---------------------------------------------------------------- content
    def load(self, segments: list[Segment], start: int = 0, lang: str | None = None) -> None:
        self.stop()
        with self._seg_lock:
            self.segments = segments
            self._para_start = None
            self.index = max(0, min(start, len(segments) - 1)) if segments else 0
        if lang:
            self.lang = lang
        self._clear_prefetch()

    def replace_paragraph(self, chapter: int, paragraph: int, new: list[Segment]) -> None:
        """Swap all segments of one paragraph (e.g. a translation arrived). Keeps the reader in place."""
        with self._seg_lock:
            span = self._para_span(chapter, paragraph)
            if span is None:
                return
            a, b = span
            self.segments[a:b] = new
            self._para_start = None
            delta = len(new) - (b - a)
            if self.index >= b:
                self.index += delta
            elif self.index >= a:
                self.index = a
            self.index = max(0, min(self.index, len(self.segments) - 1))

    def set_voice(self, voice: Voice) -> None:
        """Takes effect from the current sentence (it is restarted)."""
        self.voice = voice
        self.invalidate()

    def invalidate(self) -> None:
        """Speech text or voice changed: drop prefetched audio and restart the current sentence."""
        self._clear_prefetch()
        if self.state in (State.PLAYING, State.LOADING):
            self.seek(self.index)

    # ---------------------------------------------------------------- positions
    def _index(self) -> dict[tuple[int, int], int]:
        """Lazily rebuilt map of paragraph -> first segment index (O(1) lookups while reading)."""
        if self._para_start is None:
            idx: dict[tuple[int, int], int] = {}
            for i, s in enumerate(self.segments):
                idx.setdefault((s.chapter, s.paragraph), i)
            self._para_start = idx
        return self._para_start

    def _para_span(self, chapter: int, paragraph: int) -> tuple[int, int] | None:
        a = self._index().get((chapter, paragraph))
        if a is None:
            return None
        b = a
        while b < len(self.segments) and (self.segments[b].chapter, self.segments[b].paragraph) == (chapter, paragraph):
            b += 1
        return a, b

    def position_of(self, index: int) -> Position:
        with self._seg_lock:
            if not self.segments:
                return 0, -1, 0
            index = max(0, min(index, len(self.segments) - 1))
            s = self.segments[index]
            return s.chapter, s.paragraph, index - self._index()[(s.chapter, s.paragraph)]

    def index_of(self, pos: Position) -> int:
        ch, para, k = pos
        with self._seg_lock:
            if not self.segments:
                return 0
            span = self._para_span(ch, para)
            if span is None:            # paragraph no longer exists: nearest following one
                first = next((i for i, s in enumerate(self.segments)
                              if (s.chapter, s.paragraph) >= (ch, para)), len(self.segments) - 1)
                return max(0, first)
            a, b = span
            return min(a + max(0, k), b - 1)

    # ---------------------------------------------------------------- control
    def play(self) -> None:
        if not self.segments:
            return
        if self.state == State.PAUSED and self._thread and self._thread.is_alive():
            self._resume.set()
            self._set_state(State.PLAYING)
            return
        if self._thread and self._thread.is_alive():
            return
        self._generation += 1
        self._resume.set()
        self._thread = threading.Thread(target=self._run, args=(self._generation,), daemon=True, name="player")
        self._thread.start()

    def pause(self) -> None:
        if self.state in (State.PLAYING, State.LOADING):
            self._resume.clear()
            self._set_state(State.PAUSED)

    def toggle(self) -> None:
        self.pause() if self.state in (State.PLAYING, State.LOADING) else self.play()

    def stop(self) -> None:
        self._interrupt()
        self._set_state(State.STOPPED)

    def _interrupt(self) -> None:
        """End the playback thread without announcing a state change."""
        self._generation += 1
        self._resume.set()
        if self._thread and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
        self._thread = None

    def seek(self, index: int) -> None:
        if not self.segments:
            return
        index = max(0, min(index, len(self.segments) - 1))
        if self._thread and self._thread.is_alive():
            was_paused = self.state == State.PAUSED
            self._interrupt()          # cuts the sentence being played
            self.index = index
            if was_paused:
                self._set_state(State.STOPPED)
                self.on_segment(index)
            else:
                self.state = State.STOPPED   # silent: play() switches straight to loading/playing
                self.play()
        else:
            self.index = index
            self.on_segment(index)

    def next(self) -> None:
        self.seek(self.index + 1)

    def previous(self) -> None:
        self.seek(self.index - 1)

    def next_paragraph(self, direction: int = 1) -> None:
        if not self.segments:
            return
        segs = self.segments
        para = lambda i: (segs[i].chapter, segs[i].paragraph)
        i = self.index
        cur = para(i)
        if direction > 0:
            while i < len(segs) - 1 and para(i) == cur:
                i += 1
        else:
            # first go to the start of this paragraph, then the previous one
            while i > 0 and para(i - 1) == cur:
                i -= 1
            if i == self.index and i > 0:
                i -= 1
                prev = para(i)
                while i > 0 and para(i - 1) == prev:
                    i -= 1
        self.seek(i)

    def first_segment_of_chapter(self, chapter: int) -> int:
        idx = self._index()
        for p in (-1, 0):
            if (chapter, p) in idx:
                return idx[(chapter, p)]
        return next((i for i, s in enumerate(self.segments) if s.chapter >= chapter), len(self.segments) - 1)

    def shutdown(self) -> None:
        self.stop()
        self._pool.shutdown(wait=False, cancel_futures=True)

    # ---------------------------------------------------------------- internals
    def _set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self.on_state(state)

    def _clear_prefetch(self) -> None:
        with self._futures_lock:
            for f in self._futures.values():
                f.cancel()
            self._futures.clear()

    def speech_text(self, text: str) -> str:
        return normalize(self.text_transform(text), self.lang, expand_numbers=self.engine.expand_numbers)

    def _future(self, seg: Segment) -> Future:
        """Audio for a sentence, already sped up / pitch-shifted, prepared in a worker thread."""
        speed, pitch = round(self.dsp.settings.speed, 2), round(self.dsp.settings.pitch, 1)
        key = _key(seg) + (speed, pitch)
        with self._futures_lock:
            f = self._futures.get(key)
            if f is None:
                engine, voice, text, lang = self.engine, self.voice, self.speech_text(seg.text), self.lang
                f = self._pool.submit(self._prepare, engine, voice, text, lang, speed, pitch)
                self._futures[key] = f
            return f

    def _prepare(self, engine, voice, text, lang, speed, pitch) -> np.ndarray:
        t0 = time.perf_counter()
        audio = self.cache.get_or_synthesize(engine, voice, text, lang)
        took = time.perf_counter() - t0
        dur = len(audio) / SAMPLE_RATE
        # slow engine (e.g. multilingual Edge voices, XTTS on CPU): prepare more sentences ahead
        if dur > 0 and took > 0.5 * dur:
            self._prefetch_n = min(PREFETCH_MAX, self._prefetch_n + 1)
        return apply_prosody(audio, speed, pitch)

    def _prefetch(self, index: int) -> None:
        with self._seg_lock:
            ahead = [s for s in self.segments[index:index + self._prefetch_n + 1] if s.kind != SegmentKind.PENDING]
        for s in ahead:
            self._future(s)
        wanted = {_key(s) for s in ahead}
        with self._futures_lock:     # forget sentences the reader no longer needs
            if len(self._futures) > PREFETCH_MAX * 4:
                for k in [k for k, f in self._futures.items() if k[:4] not in wanted and f.done()]:
                    self._futures.pop(k)

    def _pause_after(self, seg: Segment) -> int:
        s = self.dsp.settings
        if seg.kind == SegmentKind.HEADING:
            return s.heading_pause_ms
        return s.paragraph_pause_ms if seg.last_in_paragraph else s.sentence_pause_ms

    def _wait_for_resume(self, alive) -> bool:
        while alive() and not self._resume.wait(0.1):
            pass
        return alive()

    def _run(self, generation: int) -> None:
        alive = lambda: generation == self._generation
        try:
            with sd.OutputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                 blocksize=BLOCK, latency="low", device=self.device) as stream:
                self.dsp.reset()
                while alive() and self.index < len(self.segments):
                    with self._seg_lock:
                        idx = self.index
                        seg = self.segments[idx]
                    if seg.kind == SegmentKind.PENDING:          # translation not ready yet
                        self.on_segment(idx)
                        if self._resume.is_set():
                            self._set_state(State.LOADING)
                        while alive() and self.segments[min(self.index, len(self.segments) - 1)].kind == SegmentKind.PENDING:
                            threading.Event().wait(0.1)
                        continue

                    if self.pause_before_chapter is not None and seg.chapter > self.pause_before_chapter:
                        self.pause_before_chapter = None
                        self.pause()
                        self.on_paused_by_timer()

                    self._prefetch(idx)
                    fut = self._future(seg)
                    if not fut.done() and self._resume.is_set():
                        self._set_state(State.LOADING)
                    while alive() and not fut.done():
                        wait([fut], timeout=0.05)
                    if not alive():
                        return
                    if self.index != idx:          # a translation shifted indices meanwhile
                        continue
                    audio = fut.result()
                    self.on_segment(idx)
                    if self._resume.is_set():
                        self._set_state(State.PLAYING)

                    silence = np.zeros(int(SAMPLE_RATE * self._pause_after(seg) / 1000), dtype=np.float32)
                    audio = np.concatenate([audio, silence])

                    for pos in range(0, len(audio), BLOCK):
                        if not self._resume.is_set():
                            stream.stop()
                            if not self._wait_for_resume(alive):
                                return
                            stream.start()
                        if not alive():
                            return
                        block = audio[pos:pos + BLOCK]
                        if len(block) < BLOCK:
                            block = np.pad(block, (0, BLOCK - len(block)))
                        stream.write(self.dsp.process_block(block))
                    if alive():
                        with self._seg_lock:
                            # find our segment again: replace_paragraph may have shifted indices
                            if self.index < len(self.segments) and self.segments[self.index] is seg:
                                self.index += 1
                            else:
                                span = self._para_span(seg.chapter, seg.paragraph)
                                cur = next((i for i in range(*span) if self.segments[i] is seg), None) if span else None
                                if cur is not None:
                                    self.index = cur + 1
                if alive() and self.index >= len(self.segments):
                    self.index = len(self.segments) - 1
                    self._set_state(State.STOPPED)
                    self.on_finished()
        except Exception as e:      # surface audio-device / synthesis errors to the UI
            if alive():
                self._set_state(State.STOPPED)
                self.on_error(e)
