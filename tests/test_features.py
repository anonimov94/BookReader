import os
import threading
import time

import numpy as np
import soundfile as sf

from bookreader.audio.dsp import DSPChain, VoiceSettings
from bookreader.audio.player import Player
from bookreader.export import ExportJob, Exporter, ffmpeg_exe
from bookreader.models import Book, Chapter, SegmentKind
from bookreader.text.langdetect import detect_language
from bookreader.text.pronunciation import PronunciationDict, Rule
from bookreader.text.segmenter import segment_book, segment_paragraph
from bookreader.translate.base import TranslationContext, Translator
from bookreader.translate.glossary import Entry, Glossary, candidate_names
from bookreader.translate.ollama import _parse_numbered
from bookreader.translate.service import TranslationService
from bookreader.translate.store import TranslationStore
from bookreader.tts.base import SAMPLE_RATE, TTSEngine, Voice
from bookreader.tts.cache import AudioCache
from bookreader.tts.xtts_engine import MAX_CHARS, split_for_xtts

EN_BOOK = Book(path="x.epub", title="Test", language="en", chapters=[
    Chapter("Chapter One", ["Mara opened the door. She was tired.", "Tom said nothing to Mara.",
                            "The rain over Greywater did not stop."]),
    Chapter("Chapter Two", ["Mara and Tom walked to Greywater.", "It was late."]),
])


class FakeEngine(TTSEngine):
    id = "fake"
    calls = 0

    def voices(self):
        return [Voice("v", "Fake", "K", ("pl", "en"), "fake")]

    def synthesize(self, text, voice, lang="pl"):
        FakeEngine.calls += 1
        return np.full(int(SAMPLE_RATE * 0.05), 0.1, dtype=np.float32)


class FakeRegistry:
    def __init__(self):
        self.e = FakeEngine()

    def engine(self, voice):
        return self.e


class FakeTranslator(Translator):
    id = "fake"
    name = "Fake"
    seen_contexts: list = []

    def translate(self, paragraphs, ctx: TranslationContext):
        self.seen_contexts.append(ctx)
        return [f"PL[{p}]" for p in paragraphs]


def test_pending_segments_and_replace():
    segs = segment_book(EN_BOOK, lambda c, p, s: None if (c, p) == (0, 1) else s)
    pending = [s for s in segs if s.kind == SegmentKind.PENDING]
    assert len(pending) == 1 and pending[0].text == "Tom said nothing to Mara."
    p = Player(FakeRegistry(), FakeEngine().voices()[0], DSPChain(), None)
    p.segments = segs
    after = segs.index(pending[0]) + 2
    p.index = after
    old_seg = segs[after]
    p.replace_paragraph(0, 1, segment_paragraph(0, 1, "Tom nic nie powiedział. Ani słowa."))
    assert p.segments[p.index] is old_seg            # reader stays on the same sentence
    assert [s.text for s in p.segments if (s.chapter, s.paragraph) == (0, 1)] == ["Tom nic nie powiedział.", "Ani słowa."]


def test_position_roundtrip():
    segs = segment_book(EN_BOOK)
    p = Player(FakeRegistry(), FakeEngine().voices()[0], DSPChain(), None)
    p.segments = segs
    for i in range(len(segs)):
        assert p.index_of(p.position_of(i)) == i


def test_pronunciation_rules():
    d = PronunciationDict([Rule("Greywater", "Grejłoter"), Rule("Dr.", "Doktor", whole_word=False),
                           Rule(r"(\d+)km", r"\1 kilometrów", regex=True, whole_word=False)])
    assert d.apply("W Greywater dr. Nowak jechał 5km") == "W Grejłoter Doktor Nowak jechał 5 kilometrów"
    assert d.apply("Greywaterowi") == "Greywaterowi"          # whole word only


def test_langdetect():
    assert detect_language(" ".join(p for c in EN_BOOK.chapters for p in c.paragraphs) * 3) == "en"
    assert detect_language("To jest polski tekst, w którym się nie dzieje nic, ale już było późno i ciemno. " * 3) == "pl"


def test_glossary_candidates_and_prompt():
    cands = {n for n, _, _ in candidate_names([p for c in EN_BOOK.chapters for p in c.paragraphs])}
    assert {"Mara", "Greywater", "Tom"} & cands
    g = Glossary(narrator_person="1", narrator_gender="K", entries=[Entry("Greywater", "Szara Woda", "miejsce")])
    text = g.describe("They reached Greywater.")
    assert "zrobiłam" in text and "Greywater → Szara Woda" in text


def test_parse_numbered():
    assert _parse_numbered("[[1]] Ala\n\n[[2]] Ola", 2) == ["Ala", "Ola"]
    assert _parse_numbered("[[1]] Ala i Ola", 2) is None


def test_xtts_split():
    long = ", ".join(["słowo " * 8] * 12)
    parts = split_for_xtts(long)
    assert all(len(p) <= MAX_CHARS for p in parts) and "".join(parts).replace(" ", "") == long.replace(" ", "")


def test_translation_service_runs_ahead(tmp_path):
    store = TranslationStore(str(tmp_path / "t.sqlite"))
    svc = TranslationService(EN_BOOK, "book", FakeTranslator(), Glossary(entries=[Entry("Mara")]), store)
    got = []
    svc.on_paragraphs = lambda items: got.extend(items)
    svc.focus(0, 1)
    svc.start()
    deadline = time.time() + 5
    while time.time() < deadline and svc.progress()[0] < 4:
        time.sleep(0.05)
    svc.stop()
    assert svc.text_for(0, 1) == "PL[Tom said nothing to Mara.]"
    assert got[0][:2] == (0, 1)                         # starts where the reader is
    # stored: a new service sees the translations without calling the translator
    svc2 = TranslationService(EN_BOOK, "book", FakeTranslator(), Glossary(), store)
    assert svc2.text_for(0, 1) == "PL[Tom said nothing to Mara.]"


def test_export_mp3_and_m4b(tmp_path):
    assert os.path.exists(ffmpeg_exe())
    reg = FakeRegistry()
    voice = FakeEngine().voices()[0]
    segs = segment_book(EN_BOOK)
    chapters = [(c.title, [s for s in segs if s.chapter == i]) for i, c in enumerate(EN_BOOK.chapters)]
    exp = Exporter(reg, AudioCache(str(tmp_path / "cache")))
    for fmt in ("mp3", "m4b"):
        job = ExportJob(str(tmp_path / fmt), fmt, "Test", "Autor", chapters, voice, VoiceSettings(), "en")
        outs = exp.run(job, lambda d, t, m: None, threading.Event())
        assert len(outs) == (2 if fmt == "mp3" else 1)
        assert all(os.path.getsize(o) > 1000 for o in outs)


def test_xtts_worker_protocol_with_dummy_model(tmp_path, monkeypatch):
    """Main app <-> worker process over JSON lines, using the current Python and a dummy model."""
    import sys
    from bookreader.tts import xtts_engine
    monkeypatch.setattr(xtts_engine, "XTTS_VOICES_DIR", str(tmp_path))
    eng = xtts_engine.XTTSEngine(license_accepted=True, python=sys.executable, dummy=True)
    try:
        eng.load()
        assert eng.loaded and eng.device == "cpu"
        voices = eng.voices()
        assert [v.id for v in voices] == ["builtin:Dummy Speaker"]
        audio = eng.synthesize("Krótkie zdanie. " * 20, voices[0], "pl")     # long text -> several worker calls
        assert audio.dtype == np.float32 and len(audio) > SAMPLE_RATE * 0.6
    finally:
        eng.close()
    assert not eng.loaded


def test_xtts_requires_license():
    import sys
    import pytest
    from bookreader.tts.xtts_engine import XTTSEngine
    with pytest.raises(RuntimeError, match="licencj"):
        XTTSEngine(license_accepted=False, python=sys.executable, dummy=True).load()


def test_enable_site_packages(tmp_path):
    from bookreader.addons.xtts_runtime import enable_site_packages
    (tmp_path / "python312._pth").write_text("python312.zip\n.\n\n# Uncomment to run site.main() automatically\n#import site\n")
    enable_site_packages(str(tmp_path))
    lines = (tmp_path / "python312._pth").read_text().splitlines()
    assert "import site" in lines and "#import site" not in lines and lines[0] == "Lib\\site-packages"


def _long_book(n_chapters=3, n_paras=60):
    return Book(path="long.epub", title="Long", language="en", chapters=[
        Chapter(f"Chapter {c + 1}", [f"Paragraph {c}-{p}: " + "word " * 30 for p in range(n_paras)])
        for c in range(n_chapters)])


def test_translation_stays_near_the_reader(tmp_path):
    book = _long_book()
    svc = TranslationService(book, "long", FakeTranslator(), Glossary(entries=[Entry("x")]),
                             TranslationStore(str(tmp_path / "t.sqlite")), lookahead_chars=1000)
    svc.focus(1, 10)
    svc.start()
    time.sleep(1.5)
    svc.stop()
    done = sorted(svc.done)
    assert done and done[0] == (1, 10)                     # starts at the reader
    assert all(k[0] == 1 and 10 <= k[1] <= 20 for k in done), done     # ~1000 chars ahead, not the book
    assert len(done) < 15


def test_translation_whole_chapter_mode(tmp_path):
    book = _long_book(n_paras=8)
    svc = TranslationService(book, "ch", FakeTranslator(), Glossary(entries=[Entry("x")]),
                             TranslationStore(str(tmp_path / "t.sqlite")), lookahead_chars=-1)
    svc.focus(1, 0)
    svc.start()
    time.sleep(1.5)
    svc.stop()
    assert {k[0] for k in svc.done} == {1} and svc.chapter_done(1)


def test_position_index_survives_replacement():
    book = _long_book(n_chapters=2, n_paras=5)
    segs = segment_book(book, lambda c, p, s: None)        # everything pending
    p = Player(FakeRegistry(), FakeEngine().voices()[0], DSPChain(), None)
    p.segments = segs
    target = p.index_of((1, 3, 0))
    assert (segs[target].chapter, segs[target].paragraph) == (1, 3)
    p.replace_paragraph(0, 2, segment_paragraph(0, 2, "Raz. Dwa. Trzy."))
    i = p.index_of((1, 3, 0))
    assert (p.segments[i].chapter, p.segments[i].paragraph) == (1, 3)
    assert p.index_of((0, 2, 2)) == p.index_of((0, 2, 0)) + 2
    assert p.first_segment_of_chapter(1) == p.index_of((1, -1, 0))


def test_book_cache_roundtrip(tmp_path, monkeypatch):
    from bookreader import loaders
    from .fixtures import make_epub
    monkeypatch.setattr(loaders, "BOOK_CACHE_DIR", str(tmp_path / "cache"))
    path = make_epub(str(tmp_path / "b.epub"))
    first = loaders.load_book(path)
    calls = []
    monkeypatch.setattr(loaders, "_load", lambda *a: calls.append(a))
    second = loaders.load_book(path)
    assert not calls and [c.paragraphs for c in second.chapters] == [c.paragraphs for c in first.chapters]
