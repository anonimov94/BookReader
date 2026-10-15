"""Audiobook export: one MP3 per chapter, or a single M4B with chapter markers and cover."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import soundfile as sf

from .audio.dsp import DSPChain, VoiceSettings
from .models import Segment, SegmentKind
from .text.normalizer import normalize
from .tts.base import SAMPLE_RATE, Voice
from .tts.cache import AudioCache
from .tts.registry import EngineRegistry


class ExportCancelled(Exception):
    pass


@dataclass
class ExportJob:
    out_dir: str
    fmt: str                             # "mp3" or "m4b"
    title: str
    author: str
    chapters: list[tuple[str, list[Segment]]]     # (chapter title, its segments)
    voice: Voice
    settings: VoiceSettings
    lang: str = "pl"
    cover: bytes | None = None
    text_transform: Callable[[str], str] = field(default=lambda t: t)
    mp3_quality: int = 4                 # LAME VBR: 0 best … 9 smallest
    m4b_bitrate: str = "64k"


def ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def _run_ffmpeg(args: list[str]) -> None:
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    p = subprocess.run([ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", *args],
                       capture_output=True, text=True, creationflags=flags)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg: {p.stderr.strip()[-800:]}")


def safe_name(s: str, limit: int = 80) -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", s).strip(" .")
    return s[:limit] or "bez_tytulu"


def _meta_escape(s: str) -> str:
    return re.sub(r"([=;#\\\n])", r"\\\1", s)


class Exporter:
    def __init__(self, registry: EngineRegistry, cache: AudioCache):
        self.registry, self.cache = registry, cache

    def run(self, job: ExportJob, progress: Callable[[int, int, str], None],
            cancel: threading.Event) -> list[str]:
        engine = self.registry.engine(job.voice)
        total = sum(len(segs) for _, segs in job.chapters)
        done = 0
        outputs: list[str] = []
        os.makedirs(job.out_dir, exist_ok=True)
        tmp = tempfile.mkdtemp(prefix="bookreader_")
        cover_path = None
        if job.cover:
            ext = ".png" if job.cover[:4] == b"\x89PNG" else ".jpg"
            cover_path = os.path.join(tmp, "cover" + ext)
            with open(cover_path, "wb") as f:
                f.write(job.cover)

        def speech(seg: Segment) -> str:
            return normalize(job.text_transform(seg.text), job.lang, expand_numbers=engine.expand_numbers)

        def pause_ms(seg: Segment) -> int:
            s = job.settings
            if seg.kind == SegmentKind.HEADING:
                return s.heading_pause_ms
            return s.paragraph_pause_ms if seg.last_in_paragraph else s.sentence_pause_ms

        book_wav = os.path.join(tmp, "book.wav")
        book_file = sf.SoundFile(book_wav, "w", SAMPLE_RATE, 1, "PCM_16") if job.fmt == "m4b" else None
        marks: list[tuple[str, int, int]] = []      # (title, start_ms, end_ms)
        position = 0
        try:
            with ThreadPoolExecutor(max_workers=3) as pool:
                for n, (ch_title, segs) in enumerate(job.chapters, 1):
                    dsp = DSPChain(VoiceSettings.from_dict(job.settings.to_dict()))
                    futures = [pool.submit(self.cache.get_or_synthesize, engine, job.voice, speech(s), job.lang)
                               for s in segs]
                    parts = []
                    for seg, fut in zip(segs, futures):
                        if cancel.is_set():
                            for f in futures:
                                f.cancel()
                            raise ExportCancelled()
                        audio = dsp.prosody(fut.result())
                        parts.append(audio)
                        parts.append(np.zeros(int(SAMPLE_RATE * pause_ms(seg) / 1000), dtype=np.float32))
                        done += 1
                        progress(done, total, f"Rozdział {n}/{len(job.chapters)}: {ch_title}")
                    audio = dsp.process_block(np.concatenate(parts)) if parts else np.zeros(0, np.float32)

                    if job.fmt == "mp3":
                        wav = os.path.join(tmp, f"{n}.wav")
                        sf.write(wav, audio, SAMPLE_RATE, subtype="PCM_16")
                        out = os.path.join(job.out_dir, f"{n:03d} - {safe_name(ch_title)}.mp3")
                        args = ["-i", wav]
                        if cover_path:
                            args += ["-i", cover_path, "-map", "0:a", "-map", "1:v", "-c:v", "copy",
                                     "-id3v2_version", "3", "-metadata:s:v", "title=Album cover",
                                     "-metadata:s:v", "comment=Cover (front)"]
                        args += ["-c:a", "libmp3lame", "-q:a", str(job.mp3_quality),
                                 "-metadata", f"title={ch_title}", "-metadata", f"album={job.title}",
                                 "-metadata", f"artist={job.author}", "-metadata", f"track={n}/{len(job.chapters)}",
                                 "-metadata", "genre=Audiobook", out]
                        _run_ffmpeg(args)
                        os.remove(wav)
                        outputs.append(out)
                    else:
                        book_file.write(audio)
                        length_ms = int(len(audio) * 1000 / SAMPLE_RATE)
                        marks.append((ch_title, position, position + length_ms))
                        position += length_ms

            if job.fmt == "m4b":
                book_file.close()
                book_file = None
                progress(total, total, "Kodowanie M4B…")
                meta = os.path.join(tmp, "chapters.txt")
                with open(meta, "w", encoding="utf-8") as f:
                    f.write(";FFMETADATA1\n")
                    f.write(f"title={_meta_escape(job.title)}\nartist={_meta_escape(job.author)}\n"
                            f"album={_meta_escape(job.title)}\ngenre=Audiobook\n")
                    for title, a, b in marks:
                        f.write(f"\n[CHAPTER]\nTIMEBASE=1/1000\nSTART={a}\nEND={b}\ntitle={_meta_escape(title)}\n")
                out = os.path.join(job.out_dir, f"{safe_name(job.title)}.m4b")
                args = ["-i", book_wav, "-i", meta]
                if cover_path:
                    args += ["-i", cover_path, "-map", "2:v", "-c:v", "copy", "-disposition:v", "attached_pic"]
                args += ["-map", "0:a", "-map_metadata", "1", "-map_chapters", "1",
                         "-c:a", "aac", "-b:a", job.m4b_bitrate, "-ac", "1", "-movflags", "+faststart", out]
                _run_ffmpeg(args)
                outputs.append(out)
        finally:
            if book_file is not None:
                book_file.close()
            for fn in os.listdir(tmp):
                try:
                    os.remove(os.path.join(tmp, fn))
                except OSError:
                    pass
            try:
                os.rmdir(tmp)
            except OSError:
                pass
        return outputs
