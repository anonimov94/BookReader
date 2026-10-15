"""All speech engines behind one voice list."""
from __future__ import annotations

from .base import TTSEngine, Voice
from .edge import EdgeTTSEngine
from .piper_engine import PiperEngine
from .xtts_engine import XTTSEngine


class EngineRegistry:
    def __init__(self, xtts_license_accepted: bool = False):
        self.edge = EdgeTTSEngine()
        self.piper = PiperEngine()
        self.xtts = XTTSEngine(license_accepted=xtts_license_accepted)
        self.engines: dict[str, TTSEngine] = {e.id: e for e in (self.edge, self.piper, self.xtts)}
        self.errors: dict[str, str] = {}

    def engine(self, voice: Voice) -> TTSEngine:
        return self.engines[voice.engine]

    def voices(self) -> list[Voice]:
        """Collects voices from every engine; an engine that fails (e.g. offline) is skipped."""
        out: list[Voice] = []
        self.errors.clear()
        for e in self.engines.values():
            try:
                out += e.voices()
            except Exception as ex:
                self.errors[e.id] = str(ex)
        return out
