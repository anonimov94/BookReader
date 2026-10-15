"""One-click Ollama: download the official installer, install silently, start the server on demand."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import requests

from ..downloads import download
from ..translate.ollama import HOST

INSTALLER_URL = "https://ollama.com/download/OllamaSetup.exe"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0)


def find_ollama() -> str | None:
    candidates = [os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe"),
                  os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Ollama", "ollama.exe")]
    for c in candidates:
        if os.path.exists(c):
            return c
    return shutil.which("ollama")


def clean_env() -> dict:
    """Environment for external programs started from the packaged app: without the bundle's
    folder on PATH, so they never load (and lock) BookReader's own DLLs such as msvcp140.dll."""
    env = dict(os.environ)
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        bundle = os.path.normcase(os.path.abspath(bundle))
        parts = [p for p in env.get("PATH", "").split(os.pathsep)
                 if p and not os.path.normcase(os.path.abspath(p)).startswith(bundle)]
        env["PATH"] = os.pathsep.join(parts)
    return env


def is_running() -> bool:
    try:
        return requests.get(f"{HOST}/api/version", timeout=1.5).ok
    except requests.RequestException:
        return False


def ensure_running(timeout: float = 25) -> bool:
    """Start `ollama serve` in the background if Ollama is installed but not running."""
    if is_running():
        return True
    exe = find_ollama()
    if not exe:
        return False
    subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=os.path.dirname(exe),
                     env=clean_env(), creationflags=NO_WINDOW | DETACHED, close_fds=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_running():
            return True
        time.sleep(0.5)
    return False


def install(log, progress, cancel: threading.Event) -> None:
    if find_ollama():
        log("Ollama jest już zainstalowana.")
    else:
        path = os.path.join(tempfile.gettempdir(), "OllamaSetup.exe")
        log("Pobieranie instalatora Ollama z ollama.com…")
        download(INSTALLER_URL, path, lambda d, t: progress(d / t * 0.8 if t else None), cancel)
        log("Instalowanie Ollama (bez okien, dla bieżącego użytkownika)…")
        progress(None)
        code = subprocess.run([path, "/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES"],
                              env=clean_env(), creationflags=NO_WINDOW).returncode
        try:
            os.remove(path)
        except OSError:
            pass
        if code != 0 or not find_ollama():
            raise RuntimeError(f"Instalator Ollama zakończył się kodem {code}.")
    log("Uruchamianie Ollama…")
    if not ensure_running():
        raise RuntimeError("Ollama zainstalowana, ale nie odpowiada. Uruchom ją z menu Start.")
    progress(1.0)
    log("Ollama działa.")


def pull_model(name: str, log, progress, cancel: threading.Event) -> None:
    from ..translate.ollama import OllamaTranslator
    if not ensure_running():
        raise RuntimeError("Ollama nie działa — zainstaluj ją najpierw.")
    log(f"Pobieranie modelu {name}…")
    last = [""]

    def report(status, done, total):
        progress(done / total if total else None)
        if status != last[0]:
            last[0] = status
            log(f"  {status}")
    OllamaTranslator().pull(name, report, cancel)
    if cancel.is_set():
        from ..downloads import Cancelled
        raise Cancelled()
    log(f"Model {name} gotowy.")
