"""One-click install of the XTTS add-on into its own portable Python.

Layout: %LOCALAPPDATA%/BookReader/runtime/xtts/
    python/              official Windows embeddable Python + pip, torch (CUDA) and coqui-tts
    models/              XTTS model files (downloaded by coqui on first load)
    installed.json       marker written when everything succeeded

Works the same whether BookReader runs from source or as a packaged exe, and never touches
the user's own Python installations.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import zipfile
from typing import Callable

from ..downloads import Cancelled, download
from ..paths import APP_DIR

RUNTIME_DIR = os.path.join(APP_DIR, "runtime", "xtts")
PYTHON_DIR = os.path.join(RUNTIME_DIR, "python")
MODELS_DIR = os.path.join(RUNTIME_DIR, "models")
MARKER = os.path.join(RUNTIME_DIR, "installed.json")
PYTHON_VERSION = "3.12.10"
PYTHON_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"
TORCH_INDEX_CUDA = "https://download.pytorch.org/whl/cu128"     # RTX 50xx (Blackwell) needs CUDA 12.8+
TORCH_INDEX_CPU = "https://download.pytorch.org/whl/cpu"
TRANSFORMERS_PIN = "transformers>=4.43,<5"
# torch >= 2.9 makes coqui-tts require torchcodec, which on Windows needs separate FFmpeg DLLs.
# 2.8 has CUDA 12.8 builds (RTX 50xx) and works with XTTS out of the box.
TORCH_PINS = ["torch==2.8.*", "torchaudio==2.8.*"]
APPROX_SIZE_GB = 5.5          # python + torch CUDA + coqui-tts + model
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

Log = Callable[[str], None]
Progress = Callable[[float | None], None]     # 0..1, or None = indeterminate


def python_exe() -> str:
    return os.path.join(PYTHON_DIR, "python.exe")


def worker_script() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "xtts_worker.py")


def is_installed() -> bool:
    return os.path.exists(MARKER) and os.path.exists(python_exe())


def nvidia_gpu() -> str:
    """Name of the NVIDIA GPU via nvidia-smi (no torch needed), or ''."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=10, creationflags=NO_WINDOW)
        return out.stdout.strip().splitlines()[0] if out.returncode == 0 and out.stdout.strip() else ""
    except (OSError, subprocess.SubprocessError, IndexError):
        return ""


def worker_env(license_accepted: bool) -> dict:
    from .ollama_setup import clean_env
    env = clean_env()                                 # never load the packaged app's DLLs into torch
    env["TTS_HOME"] = MODELS_DIR                      # model download location
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONNOUSERSITE"] = "1"
    if license_accepted:
        env["COQUI_TOS_AGREED"] = "1"                 # the user accepted the CPML in the app
    return env


def enable_site_packages(python_dir: str) -> None:
    """The embeddable build ignores site-packages (so pip installs are invisible) until
    `import site` is enabled in its ._pth file."""
    pth = next(f for f in os.listdir(python_dir) if f.endswith("._pth"))
    path = os.path.join(python_dir, pth)
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    lines = [("import site" if l.strip() == "#import site" else l) for l in lines]
    if "Lib\\site-packages" not in lines:
        lines.insert(0, "Lib\\site-packages")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _run(cmd: list[str], log: Log, cancel: threading.Event) -> None:
    """Run a command streaming its output into the log; raise on failure or cancel."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                         errors="replace", creationflags=NO_WINDOW, env=worker_env(False))
    for line in p.stdout:
        if cancel.is_set():
            p.kill()
            raise Cancelled()
        line = line.rstrip()
        if line:
            log(line)
    if p.wait() != 0:
        raise RuntimeError(f"Polecenie nie powiodło się (kod {p.returncode}): {' '.join(cmd[:4])}…")


def install(log: Log, progress: Progress, cancel: threading.Event) -> None:
    os.makedirs(RUNTIME_DIR, exist_ok=True)
    gpu = nvidia_gpu()
    log(f"Karta graficzna: {gpu or 'nie wykryto NVIDIA — XTTS będzie działał na procesorze (wolno)'}")

    # 1. portable Python
    if not os.path.exists(python_exe()):
        log(f"Pobieranie Pythona {PYTHON_VERSION} (wersja przenośna, ~11 MB)…")
        zip_path = os.path.join(RUNTIME_DIR, "python.zip")
        download(PYTHON_URL, zip_path, lambda d, t: progress(d / t * 0.05 if t else None), cancel)
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(PYTHON_DIR)
        os.remove(zip_path)
        enable_site_packages(PYTHON_DIR)

    # 2. pip
    if not os.path.exists(os.path.join(PYTHON_DIR, "Scripts", "pip.exe")):
        log("Instalowanie pip…")
        progress(0.06)
        gp = os.path.join(RUNTIME_DIR, "get-pip.py")
        download(GET_PIP_URL, gp, None, cancel)
        _run([python_exe(), gp, "--no-warn-script-location"], log, cancel)
        os.remove(gp)

    pip = [python_exe(), "-m", "pip", "install", "--no-warn-script-location", "--progress-bar", "off"]
    # 3. PyTorch
    log("Instalowanie PyTorch 2.8 " + ("z CUDA (~3 GB, to potrwa kilka–kilkanaście minut)…" if gpu else "(CPU)…"))
    progress(None)
    _run(pip + TORCH_PINS + ["--index-url", TORCH_INDEX_CUDA if gpu else TORCH_INDEX_CPU], log, cancel)
    progress(0.75)

    # 4. coqui-tts (XTTS)
    log("Instalowanie coqui-tts (XTTS)…")
    progress(None)
    # transformers 5.x removed APIs that XTTS imports (isin_mps_friendly); coqui-tts does not cap it.
    _run(pip + ["coqui-tts", TRANSFORMERS_PIN] + TORCH_PINS + ["--extra-index-url",
         TORCH_INDEX_CUDA if gpu else TORCH_INDEX_CPU], log, cancel)
    # leftovers from an earlier install with torch 2.9+ would make TTS demand FFmpeg DLLs
    _run([python_exe(), "-m", "pip", "uninstall", "-y", "torchcodec"], log, cancel)
    progress(0.95)

    # 5. quick check
    log("Sprawdzanie instalacji…")
    check = ("import os, tempfile, numpy as np, soundfile as sf, torch, torchaudio, TTS; "
             "p = os.path.join(tempfile.gettempdir(), 'bookreader_xtts_check.wav'); "
             "sf.write(p, np.zeros(2400, 'float32'), 24000); torchaudio.load(p); os.remove(p); "
             "print('torch', torch.__version__, 'CUDA', torch.cuda.is_available(), '| coqui-tts', TTS.__version__)")
    _run([python_exe(), "-c", check], log, cancel)
    with open(MARKER, "w", encoding="utf-8") as f:
        json.dump({"python": PYTHON_VERSION, "gpu": gpu}, f)
    progress(1.0)
    log("XTTS zainstalowany. Model głosu (~1,9 GB) pobierze się przy pierwszym użyciu.")


def uninstall() -> None:
    shutil.rmtree(RUNTIME_DIR, ignore_errors=True)


def disk_free_gb(path: str = APP_DIR) -> float:
    return shutil.disk_usage(path).free / 1e9
