"""Common interface for speech engines."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np

SAMPLE_RATE = 24000   # every engine returns mono float32 at this rate


@dataclass(frozen=True)
class Voice:
    id: str             # engine-specific identifier
    name: str           # display name
    gender: str         # "K" / "M"
    languages: tuple[str, ...]   # ISO 639-1 codes the voice can speak
    engine: str


class TTSEngine(ABC):
    id: str = ""
    name: str = ""
    expand_numbers: bool = False     # True if the engine reads digits poorly

    @abstractmethod
    def voices(self) -> list[Voice]: ...

    @abstractmethod
    def synthesize(self, text: str, voice: Voice, lang: str = "pl") -> np.ndarray:
        """Return mono float32 audio at SAMPLE_RATE. `lang` matters only for multilingual engines."""

    def voices_for(self, lang: str) -> list[Voice]:
        return [v for v in self.voices() if lang in v.languages]


def resample(audio: np.ndarray, src_rate: int) -> np.ndarray:
    if src_rate == SAMPLE_RATE:
        return audio.astype(np.float32, copy=False)
    import soxr
    return soxr.resample(audio, src_rate, SAMPLE_RATE, quality="HQ").astype(np.float32)
