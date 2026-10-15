"""Piper: fast offline neural voices (ONNX). Voices are downloaded on demand from the manager."""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass

import numpy as np

from ..paths import PIPER_DIR
from .base import TTSEngine, Voice, resample

HF_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"


@dataclass(frozen=True)
class PiperCatalogEntry:
    key: str            # e.g. pl_PL-gosia-medium
    label: str
    gender: str
    size_mb: int

    @property
    def lang(self) -> str:
        return self.key[:2]

    def urls(self) -> tuple[str, str]:
        lang_region, name, quality = self.key.split("-")
        base = f"{HF_BASE}/{lang_region[:2]}/{lang_region}/{name}/{quality}/{self.key}.onnx"
        return base, base + ".json"


CATALOG = [
    PiperCatalogEntry("pl_PL-gosia-medium", "Gosia (PL)", "K", 63),
    PiperCatalogEntry("pl_PL-darkman-medium", "Darkman (PL)", "M", 63),
    PiperCatalogEntry("pl_PL-mc_speech-medium", "MC Speech (PL)", "M", 63),
    PiperCatalogEntry("en_US-lessac-high", "Lessac (EN-US)", "K", 114),
    PiperCatalogEntry("en_US-ryan-high", "Ryan (EN-US)", "M", 121),
    PiperCatalogEntry("en_US-amy-medium", "Amy (EN-US)", "K", 63),
    PiperCatalogEntry("en_GB-alba-medium", "Alba (EN-GB)", "K", 63),
    PiperCatalogEntry("en_GB-northern_english_male-medium", "Northern English (EN-GB)", "M", 63),
]


def model_path(key: str) -> str:
    return os.path.join(PIPER_DIR, key + ".onnx")


def is_installed(key: str) -> bool:
    return os.path.exists(model_path(key)) and os.path.exists(model_path(key) + ".json")


class PiperEngine(TTSEngine):
    id = "piper"
    name = "Piper (offline)"
    expand_numbers = True        # espeak reads digits only in the nominative case

    def __init__(self):
        self._loaded: dict[str, object] = {}
        self._lock = threading.Lock()

    @staticmethod
    def available() -> bool:
        try:
            import piper  # noqa: F401
            return True
        except ImportError:
            return False

    def voices(self) -> list[Voice]:
        if not self.available():
            return []
        out = []
        known = {e.key: e for e in CATALOG}
        for fn in sorted(os.listdir(PIPER_DIR)):
            if not fn.endswith(".onnx") or not os.path.exists(os.path.join(PIPER_DIR, fn + ".json")):
                continue
            key = fn[:-5]
            entry = known.get(key)
            if entry:
                label, gender = entry.label, entry.gender
            else:
                label, gender = key, "?"
            out.append(Voice(id=key, name=f"{label} · Piper", gender=gender, languages=(key[:2],), engine=self.id))
        return out

    def _voice(self, key: str):
        with self._lock:
            if key not in self._loaded:
                from piper import PiperVoice
                self._loaded[key] = PiperVoice.load(model_path(key))
            return self._loaded[key]

    def synthesize(self, text: str, voice: Voice, lang: str = "pl") -> np.ndarray:
        pv = self._voice(voice.id)
        chunks, rate = [], 22050
        for chunk in pv.synthesize(text):
            chunks.append(chunk.audio_float_array)
            rate = chunk.sample_rate
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return resample(np.concatenate(chunks).astype(np.float32), rate)
