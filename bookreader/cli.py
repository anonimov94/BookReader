"""Command-line reader (stage 1). The GUI arrives in stage 2.

    python -m bookreader.cli KSIĄŻKA.epub [--chapter N] [--voice ID] [--speed 1.2] ...
    python -m bookreader.cli KSIĄŻKA.pdf --dump          # show parsed chapters/sentences
    python -m bookreader.cli --voices                    # list available voices
"""
from __future__ import annotations

import argparse
import sys
import threading

from .audio.dsp import DSPChain, VoiceSettings
from .audio.player import Player
from .loaders import load_book
from .paths import CACHE_DIR
from .text.segmenter import segment_book
from .tts.cache import AudioCache
from .tts.registry import EngineRegistry


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="bookreader", description="Czytnik książek EPUB/PDF na głos")
    ap.add_argument("book", nargs="?")
    ap.add_argument("--voices", action="store_true", help="wypisz dostępne głosy")
    ap.add_argument("--dump", action="store_true", help="wypisz rozdziały i zdania zamiast czytać")
    ap.add_argument("--lang", default=None, help="język tekstu (pl/en), domyślnie z metadanych")
    ap.add_argument("--chapter", type=int, default=1)
    ap.add_argument("--voice", default="pl-PL-ZofiaNeural")
    for name, default in [("speed", 1.0), ("pitch", 0.0), ("volume", 0.0), ("bass", 0.0), ("mid", 0.0), ("treble", 0.0)]:
        ap.add_argument(f"--{name}", type=float, default=default)
    args = ap.parse_args(argv)

    registry = EngineRegistry()
    if args.voices:
        for v in registry.voices():
            print(f"{v.id:40} {v.gender}  {','.join(v.languages):6} {v.name}")
        return 0
    if not args.book:
        ap.error("podaj plik książki")

    book = load_book(args.book)
    lang = args.lang or book.language or "pl"
    segments = segment_book(book)
    print(f"{book.title} — {book.author}  [{lang}]  rozdziałów: {len(book.chapters)}, zdań: {len(segments)}")
    if book.needs_ocr_pages:
        print(f"Uwaga: {len(book.needs_ocr_pages)} stron to skany bez tekstu (OCR w etapie 7).")

    if args.dump:
        for ci, ch in enumerate(book.chapters, 1):
            print(f"\n=== {ci}. {ch.title}  ({len(ch.paragraphs)} akapitów)")
        print("\n--- pierwsze zdania ---")
        for s in segments[:40]:
            print(f"[{s.chapter + 1}:{s.paragraph}] {s.kind.value[:1].upper()} {s.text}")
        return 0

    voice = next((v for v in registry.voices() if v.id == args.voice), None)
    if voice is None:
        ap.error(f"nieznany głos {args.voice} (zobacz --voices)")
    settings = VoiceSettings(speed=args.speed, pitch=args.pitch, volume_db=args.volume,
                             bass_db=args.bass, mid_db=args.mid, treble_db=args.treble)
    player = Player(registry, voice, DSPChain(settings), AudioCache(CACHE_DIR), lang=lang)
    done = threading.Event()
    player.on_segment = lambda i: print(f"▶ {segments[i].text}", flush=True)
    player.on_error = lambda e: (print(f"Błąd: {e}", file=sys.stderr), done.set())
    player.on_finished = done.set
    player.load(segments)
    player.index = player.first_segment_of_chapter(args.chapter - 1)
    player.play()
    try:
        while not done.wait(0.2):
            pass
    except KeyboardInterrupt:
        print("\nZatrzymano.")
    finally:
        player.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
