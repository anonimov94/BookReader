"""Microsoft Edge neural voices (free, online) via the edge-tts package."""
from __future__ import annotations

import asyncio
import threading

import edge_tts
import miniaudio
import numpy as np

from .base import SAMPLE_RATE, TTSEngine, Voice

# Multilingual neural voices read Polish well, which widens the choice beyond Zofia/Marek.


class EdgeTTSEngine(TTSEngine):
    id = "edge"
    name = "Microsoft Edge (online)"
    expand_numbers = False

    def __init__(self, retries: int = 3):
        self._retries = retries
        self._voices: list[Voice] | None = None
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True, name="edge-tts-loop").start()

    def _run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def voices(self) -> list[Voice]:
        if self._voices is None:
            raw = self._run(edge_tts.list_voices())
            out = []
            for v in raw:
                locale, short = v["Locale"], v["ShortName"]
                multilingual = "Multilingual" in short
                langs = {locale[:2]}
                if multilingual:
                    langs |= {"pl", "en"}
                if not langs & {"pl", "en"}:
                    continue
                person = short.split("-")[-1].replace("MultilingualNeural", "").replace("Neural", "")
                label = f"{person} ({locale}{', wielojęzyczny' if multilingual else ''})"
                out.append(Voice(id=short, name=label, gender="K" if v["Gender"] == "Female" else "M",
                                 languages=tuple(sorted(langs)), engine=self.id))
            # Native Polish voices first, then multilingual, then the rest.
            out.sort(key=lambda v: (not v.id.startswith("pl-"), "Multilingual" not in v.id, v.id))
            self._voices = out
        return self._voices

    async def _synth(self, text: str, voice_id: str) -> bytes:
        chunks = []
        async for msg in edge_tts.Communicate(text, voice_id).stream():
            if msg["type"] == "audio":
                chunks.append(msg["data"])
        return b"".join(chunks)

    def synthesize(self, text: str, voice: Voice, lang: str = "pl") -> np.ndarray:
        last_error: Exception | None = None
        for _ in range(self._retries):
            try:
                mp3 = self._run(self._synth(text, voice.id))
                if not mp3:
                    return np.zeros(0, dtype=np.float32)
                decoded = miniaudio.decode(mp3, output_format=miniaudio.SampleFormat.FLOAT32,
                                           nchannels=1, sample_rate=SAMPLE_RATE)
                return np.frombuffer(decoded.samples, dtype=np.float32).copy()
            except edge_tts.exceptions.NoAudioReceived:
                return np.zeros(0, dtype=np.float32)   # e.g. text made only of punctuation
            except Exception as e:  # network hiccups: retry
                last_error = e
        raise RuntimeError(f"Edge TTS nie odpowiada: {last_error}") from last_error
