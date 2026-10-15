"""Disk cache of synthesized sentences, so re-reading and exporting is instant."""
from __future__ import annotations

import hashlib
import os

import numpy as np
import soundfile as sf

from .base import SAMPLE_RATE, TTSEngine, Voice


class AudioCache:
    def __init__(self, directory: str):
        self.directory = directory
        os.makedirs(directory, exist_ok=True)

    def _path(self, engine: TTSEngine, voice: Voice, text: str, lang: str) -> str:
        key = hashlib.sha1(f"{engine.id}|{voice.id}|{lang}|{text}".encode()).hexdigest()
        return os.path.join(self.directory, key[:2], key + ".flac")

    def get_or_synthesize(self, engine: TTSEngine, voice: Voice, text: str, lang: str = "pl") -> np.ndarray:
        path = self._path(engine, voice, text, lang)
        if os.path.exists(path):
            try:
                audio, _ = sf.read(path, dtype="float32")
                return audio
            except Exception:
                os.remove(path)
        audio = engine.synthesize(text, voice, lang)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        sf.write(tmp, audio, SAMPLE_RATE, format="FLAC")
        os.replace(tmp, path)
        return audio
