"""Locations of user data (cache, library, voice presets)."""
from __future__ import annotations

import os

import sys


def _app_dir() -> str:
    """Portable mode: a `data` folder next to BookReader.exe (or the project root when run from
    source) holds everything. Otherwise %LOCALAPPDATA%/BookReader."""
    base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else \
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    portable = os.path.join(base, "data")
    if os.path.isdir(portable):
        return portable
    return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/share"), "BookReader")


APP_DIR = _app_dir()
CACHE_DIR = os.path.join(APP_DIR, "cache", "audio")
LIBRARY_FILE = os.path.join(APP_DIR, "library.json")
PRONUNCIATION_FILE = os.path.join(APP_DIR, "pronunciation.json")
TRANSLATIONS_DB = os.path.join(APP_DIR, "translations.sqlite")
VOICES_DIR = os.path.join(APP_DIR, "voices")
PIPER_DIR = os.path.join(VOICES_DIR, "piper")
XTTS_VOICES_DIR = os.path.join(VOICES_DIR, "xtts")
TESSDATA_DIR = os.path.join(APP_DIR, "tessdata")
OCR_CACHE_DIR = os.path.join(APP_DIR, "cache", "ocr")

for _d in (APP_DIR, PIPER_DIR, XTTS_VOICES_DIR, TESSDATA_DIR, OCR_CACHE_DIR):
    os.makedirs(_d, exist_ok=True)
