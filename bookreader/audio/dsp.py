"""Voice shaping: speed/pitch per sentence, EQ and volume live per audio block."""
from __future__ import annotations

import threading
from dataclasses import asdict, dataclass

import numpy as np
from pedalboard import Gain, HighShelfFilter, LowShelfFilter, PeakFilter, Pedalboard, time_stretch

from ..tts.base import SAMPLE_RATE


@dataclass
class VoiceSettings:
    speed: float = 1.0          # 0.5 – 3.0
    pitch: float = 0.0          # semitones, -8 – +8
    volume_db: float = 0.0      # -30 – +12
    bass_db: float = 0.0        # low shelf @ 180 Hz, ±15
    mid_db: float = 0.0         # peak @ 1.2 kHz, ±15
    treble_db: float = 0.0      # high shelf @ 5 kHz, ±15
    sentence_pause_ms: int = 250
    paragraph_pause_ms: int = 650
    heading_pause_ms: int = 1100

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "VoiceSettings":
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in d.items() if k in known})


def _soft_clip(x: np.ndarray, knee: float = 0.85) -> np.ndarray:
    """Transparent below `knee`, smoothly saturates above it (protects against EQ/volume boosts).
    (pedalboard's Limiter adds make-up gain, which would override the volume slider.)"""
    over = np.abs(x) > knee
    if over.any():
        x = x.copy()
        head = 1.0 - knee
        x[over] = np.sign(x[over]) * (knee + head * np.tanh((np.abs(x[over]) - knee) / head))
    return x


def apply_prosody(audio: np.ndarray, speed: float, pitch: float) -> np.ndarray:
    """Speed and pitch for one sentence (Rubber Band). Thread-safe and stateless, so the
    player runs it in the synthesis workers ahead of time instead of between sentences."""
    if audio.size == 0 or (abs(speed - 1.0) < 1e-3 and abs(pitch) < 1e-3):
        return audio
    out = time_stretch(audio[np.newaxis, :], SAMPLE_RATE, stretch_factor=float(speed),
                       pitch_shift_in_semitones=float(pitch), high_quality=True)
    return out[0].astype(np.float32, copy=False)


class DSPChain:
    """Thread-safe: the GUI changes settings while the player thread processes audio."""

    def __init__(self, settings: VoiceSettings | None = None):
        self.settings = settings or VoiceSettings()
        self._lock = threading.Lock()
        self._bass = LowShelfFilter(cutoff_frequency_hz=180, gain_db=0, q=0.7)
        self._mid = PeakFilter(cutoff_frequency_hz=1200, gain_db=0, q=0.8)
        self._treble = HighShelfFilter(cutoff_frequency_hz=5000, gain_db=0, q=0.7)
        self._gain = Gain(0)
        self._board = Pedalboard([self._bass, self._mid, self._treble, self._gain])
        self.apply()

    def apply(self) -> None:
        """Push current settings into the live filters (call after changing `settings`)."""
        s = self.settings
        with self._lock:
            self._bass.gain_db = s.bass_db
            self._mid.gain_db = s.mid_db
            self._treble.gain_db = s.treble_db
            self._gain.gain_db = s.volume_db

    def update(self, **changes) -> None:
        for k, v in changes.items():
            setattr(self.settings, k, v)
        self.apply()

    def prosody(self, audio: np.ndarray) -> np.ndarray:
        """Speed and pitch for one whole sentence with the current settings."""
        return apply_prosody(audio, self.settings.speed, self.settings.pitch)

    def process_block(self, block: np.ndarray) -> np.ndarray:
        with self._lock:
            out = self._board.process(block[np.newaxis, :], SAMPLE_RATE, reset=False)[0]
        return _soft_clip(out)

    def reset(self) -> None:
        with self._lock:
            self._board.reset()
