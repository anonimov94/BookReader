"""Coqui XTTS v2: built-in speakers and voices cloned from the user's WAV samples.

The model runs in a separate worker process inside the XTTS add-on runtime (its own Python with
torch + coqui-tts, installed with one click — see addons/xtts_runtime.py). This keeps the main app
small and works the same from source and from the packaged exe.
The model licence (Coqui Public Model License, non-commercial) must be accepted in the app first.
"""
from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from typing import Callable

import numpy as np

from ..addons import xtts_runtime
from ..paths import XTTS_VOICES_DIR
from .base import SAMPLE_RATE, TTSEngine, Voice, resample

LICENSE_URL = "https://coqui.ai/cpml"
# XTTS degrades on long inputs; the Polish limit in the model config is ~224 characters.
MAX_CHARS = 200
_CLAUSE = re.compile(r"(?<=[,;:—–])\s+")


def split_for_xtts(text: str) -> list[str]:
    if len(text) <= MAX_CHARS:
        return [text]
    parts, cur = [], ""
    for piece in _CLAUSE.split(text):
        while len(piece) > MAX_CHARS:                 # no punctuation: cut at a space
            cut = piece.rfind(" ", 0, MAX_CHARS)
            cut = cut if cut > 0 else MAX_CHARS
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(piece[:cut])
            piece = piece[cut:].lstrip()
        if len(cur) + len(piece) + 1 > MAX_CHARS and cur:
            parts.append(cur)
            cur = piece
        else:
            cur = f"{cur} {piece}".strip()
    if cur:
        parts.append(cur)
    return parts


class WorkerProcess:
    """JSON-lines client for addons/xtts_worker.py."""

    def __init__(self, python: str, script: str, env: dict, on_log: Callable[[str], None]):
        self.on_log = on_log
        self._p = subprocess.Popen([python, "-u", script], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, text=True, encoding="utf-8", env=env,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self._id = 0
        self._lock = threading.Lock()
        self._read_until(lambda m: m.get("event") == "ready")

    def _read_until(self, done) -> dict:
        while True:
            line = self._p.stdout.readline()
            if not line:
                raise RuntimeError("Proces XTTS zakończył się nieoczekiwanie.")
            msg = json.loads(line)
            if msg.get("event") == "log":
                self.on_log(msg["msg"])
            elif done(msg):
                return msg

    def call(self, cmd: str, **kw) -> dict:
        with self._lock:
            self._id += 1
            rid = self._id
            self._p.stdin.write(json.dumps({"id": rid, "cmd": cmd, **kw}, ensure_ascii=False) + "\n")
            self._p.stdin.flush()
            reply = self._read_until(lambda m: m.get("id") == rid)
        if not reply.get("ok"):
            raise RuntimeError(reply.get("error", "błąd XTTS"))
        return reply

    @property
    def alive(self) -> bool:
        return self._p.poll() is None

    def close(self) -> None:
        if self.alive:
            try:
                self.call("quit")
            except Exception:
                pass
            try:
                self._p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._p.kill()


class XTTSEngine(TTSEngine):
    id = "xtts"
    name = "XTTS v2 (GPU, klonowanie)"
    expand_numbers = True

    def __init__(self, license_accepted: bool = False, python: str | None = None, dummy: bool = False):
        self.license_accepted = license_accepted
        self._python = python            # override for tests
        self._dummy = dummy
        self._worker: WorkerProcess | None = None
        self._speakers: list[str] = []
        self.device = ""
        self._lock = threading.Lock()
        self._tmp = tempfile.mkdtemp(prefix="bookreader_xtts_")
        self.on_log: Callable[[str], None] = lambda m: None
        atexit.register(self.close)

    def available(self) -> bool:
        return self._python is not None or xtts_runtime.is_installed()

    @property
    def loaded(self) -> bool:
        return self._worker is not None and self._worker.alive

    def load(self) -> None:
        """Starts the worker and loads the model (first time downloads ~1.9 GB). Call from a worker thread."""
        if not self.license_accepted:
            raise RuntimeError("Najpierw zaakceptuj licencję modelu XTTS (Coqui Public Model License).")
        if not self.available():
            raise RuntimeError("Dodatek XTTS nie jest zainstalowany (Narzędzia → Instalator dodatków).")
        with self._lock:
            if self.loaded:
                return
            env = xtts_runtime.worker_env(True)
            if self._dummy:
                env["XTTS_WORKER_DUMMY"] = "1"
            self._worker = WorkerProcess(self._python or xtts_runtime.python_exe(), xtts_runtime.worker_script(),
                                         env, lambda m: self.on_log(m))
            info = self._worker.call("load")
            self._speakers = info.get("speakers", [])
            self.device = info.get("device", "")

    def close(self) -> None:
        if self._worker:
            self._worker.close()
            self._worker = None
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ------------------------------------------------------------ voices
    @staticmethod
    def cloned_voices() -> list[str]:
        return sorted(d for d in os.listdir(XTTS_VOICES_DIR)
                      if os.path.isdir(os.path.join(XTTS_VOICES_DIR, d)) and
                      any(f.lower().endswith(".wav") for f in os.listdir(os.path.join(XTTS_VOICES_DIR, d))))

    @staticmethod
    def add_cloned_voice(name: str, wav_files: list[str]) -> str:
        safe = re.sub(r"[^\w\- ]", "_", name).strip() or "glos"
        target = os.path.join(XTTS_VOICES_DIR, safe)
        os.makedirs(target, exist_ok=True)
        for i, src in enumerate(wav_files):
            shutil.copy(src, os.path.join(target, f"sample{i + 1}.wav"))
        return safe

    @staticmethod
    def remove_cloned_voice(name: str) -> None:
        shutil.rmtree(os.path.join(XTTS_VOICES_DIR, name), ignore_errors=True)

    def voices(self) -> list[Voice]:
        if not self.available():
            return []
        langs = ("en", "pl")
        out = [Voice(id=f"clone:{n}", name=f"{n} · XTTS (klon)", gender="?", languages=langs, engine=self.id)
               for n in self.cloned_voices()]
        out += [Voice(id=f"builtin:{s}", name=f"{s} · XTTS", gender="?", languages=langs, engine=self.id)
                for s in self._speakers]
        return out

    # ------------------------------------------------------------ synthesis
    def _voice_spec(self, voice: Voice) -> dict:
        if voice.id.startswith("clone:"):
            folder = os.path.join(XTTS_VOICES_DIR, voice.id[6:])
            return {"type": "clone", "wavs": [os.path.join(folder, f) for f in sorted(os.listdir(folder))
                                               if f.lower().endswith(".wav")]}
        return {"type": "builtin", "name": voice.id[8:]}

    def synthesize(self, text: str, voice: Voice, lang: str = "pl") -> np.ndarray:
        if not self.loaded:
            self.load()
        spec = self._voice_spec(voice)
        pieces = []
        for k, part in enumerate(split_for_xtts(text)):
            out = os.path.join(self._tmp, f"{threading.get_ident()}_{k}.npy")
            self._worker.call("synth", text=part, lang=lang, voice=spec, out=out)
            pieces.append(np.load(out).astype(np.float32))
            os.remove(out)
            pieces.append(np.zeros(int(0.08 * SAMPLE_RATE), dtype=np.float32))
        return resample(np.concatenate(pieces), 24000) if pieces else np.zeros(0, dtype=np.float32)
