"""Resumable-safe file download used by the in-app model manager (user-initiated only)."""
from __future__ import annotations

import os
import threading
from typing import Callable

import requests


class Cancelled(Exception):
    pass


def download(url: str, dest: str, progress: Callable[[int, int], None] | None = None,
             cancel: threading.Event | None = None, timeout: float = 30) -> str:
    """Download `url` to `dest` atomically. progress(done_bytes, total_bytes or 0)."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    with requests.get(url, stream=True, timeout=timeout, headers={"User-Agent": "BookReader/1.0"}) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 16):
                if cancel is not None and cancel.is_set():
                    f.close()
                    os.remove(tmp)
                    raise Cancelled()
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    os.replace(tmp, dest)
    return dest
