"""Checks that a packaged (frozen) build contains every native dependency.

    BookReader.exe --selftest raport.txt
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback


def _checks():
    def edge():
        from .tts.edge import EdgeTTSEngine
        return f"{len(EdgeTTSEngine().voices())} głosów (sieć)"

    def audio_stack():
        import miniaudio  # noqa: F401
        import numpy as np
        import soundfile as sf
        import soxr
        import sounddevice as sd
        x = np.zeros(2400, dtype=np.float32)
        path = os.path.join(tempfile.mkdtemp(), "t.flac")
        sf.write(path, x, 24000)
        soxr.resample(x, 24000, 22050)
        return f"urządzenia audio: {len(sd.query_devices())}"

    def dsp():
        import numpy as np
        from .audio.dsp import DSPChain, VoiceSettings
        d = DSPChain(VoiceSettings(speed=1.5, bass_db=3))
        return f"{len(d.process_block(d.prosody(np.zeros(24000, dtype=np.float32))))} próbek"

    def books():
        import pymupdf
        from ebooklib import epub  # noqa: F401
        from bs4 import BeautifulSoup
        BeautifulSoup("<p>x</p>", "lxml")
        return f"PyMuPDF {pymupdf.VersionBind}"

    def text():
        from .text.normalizer import normalize
        return normalize("Rozdział IV, 25 kg", "pl", expand_numbers=True)

    def piper():
        import piper
        data = os.path.join(os.path.dirname(piper.__file__), "espeak-ng-data")
        if not os.path.isdir(data):
            raise RuntimeError(f"brak {data}")
        import onnxruntime
        return f"onnxruntime {onnxruntime.__version__}, espeak-ng-data OK"

    def ffmpeg():
        import subprocess
        from .export import ffmpeg_exe
        exe = ffmpeg_exe()
        out = subprocess.run([exe, "-version"], capture_output=True, text=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        return out.splitlines()[0][:60]

    def qt():
        from PySide6.QtCore import qVersion
        from .gui import main_window  # noqa: F401
        return f"Qt {qVersion()}"

    def translate():
        from .translate import deepl, ollama, service  # noqa: F401
        return "OK"

    return [("Edge TTS", edge), ("Audio (sounddevice/soundfile/soxr/miniaudio)", audio_stack),
            ("DSP (pedalboard)", dsp), ("EPUB/PDF", books), ("Normalizacja tekstu", text),
            ("Piper", piper), ("ffmpeg (eksport)", ffmpeg), ("GUI", qt), ("Tłumaczenie", translate)]


def main(report_path: str) -> int:
    lines, failed = [], 0
    for name, fn in _checks():
        try:
            lines.append(f"OK    {name}: {fn()}")
        except Exception as e:
            failed += 1
            lines.append(f"BŁĄD  {name}: {e}\n{traceback.format_exc()}")
    lines.append(f"\n{'WSZYSTKO OK' if not failed else f'BŁĘDÓW: {failed}'} · frozen={getattr(sys, 'frozen', False)}")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return 1 if failed else 0
