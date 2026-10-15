import numpy as np

from bookreader.audio.dsp import DSPChain, VoiceSettings
from bookreader.loaders import load_book
from bookreader.text.cleaner import clean_paragraph
from bookreader.text.normalizer import normalize
from bookreader.text.segmenter import segment_book, split_sentences
from bookreader.tts.base import SAMPLE_RATE

from .fixtures import make_epub, make_pdf


def sentences(text):
    return [text[a:b] for a, b in split_sentences(text)]


def test_split_respects_abbreviations_and_initials():
    text = "Spotkał np. dr. Kowalskiego i J. R. Nowaka. Potem poszli do domu! Czy to koniec? Tak."
    assert sentences(text) == [
        "Spotkał np. dr. Kowalskiego i J. R. Nowaka.", "Potem poszli do domu!", "Czy to koniec?", "Tak."]


def test_split_dialogue_quotes():
    assert sentences("„Czy to już koniec?” — zapytała. Nikt nie odpowiedział.") == [
        "„Czy to już koniec?” — zapytała.", "Nikt nie odpowiedział."]


def test_long_sentence_is_broken_up():
    long = ", ".join(["bardzo długi fragment zdania bez kropki"] * 20) + "."
    spans = split_sentences(long)
    assert len(spans) > 1 and all(b - a <= 350 for a, b in spans)
    assert spans[0][0] == 0 and spans[-1][1] == len(long)


def test_clean_paragraph_dehyphenates():
    assert clean_paragraph("spoj-\nrzała na  las\n i dalej") == "spojrzała na las i dalej"
    assert clean_paragraph("Biało-\nCzerwoni") == "Biało- Czerwoni"   # uppercase: real compound, keep hyphen


def test_normalize_polish():
    out = normalize("Dr Nowak przyjechał ok. godz. 8, np. w 1985 r. Rozdział IV", "pl")
    assert out == "Doktor Nowak przyjechał około godzina 8, na przykład w 1985 roku Rozdział czwarty"
    assert normalize("Ma 25 lat i 3,5 kg", "pl", expand_numbers=True) == \
        "Ma dwadzieścia pięć lat i trzy przecinek pięć kg"


def test_normalize_english():
    assert normalize("Mr. Smith, e.g. 50% done. Chapter 3", "en") == \
        "Mister Smith, for example 50 percent done. Chapter three"


def test_epub_loader(tmp_path):
    book = load_book(make_epub(str(tmp_path / "b.epub")))
    assert book.title == "Dolina Mgieł" and book.language == "pl"
    assert [c.title for c in book.chapters] == ["Rozdział I", "Rozdział II"]
    assert book.chapters[0].paragraphs[1].endswith("pierwszą stację pomiarową.")   # footnote <sup> removed
    segs = segment_book(book)
    assert segs[0].text == "Rozdział I" and segs[0].kind.value == "heading"


def test_pdf_loader(tmp_path):
    book = load_book(make_pdf(str(tmp_path / "b.pdf")))
    assert [c.title for c in book.chapters] == ["Rozdział I", "Rozdział II"]
    text = " ".join(book.chapters[0].paragraphs)
    assert "spojrzała na las" in text                       # dehyphenated
    assert "Dolina Mgieł — powieść" not in text             # running header removed
    assert "gdzie czekały na niego" in text                 # joined across page break
    assert not any(p.strip().isdigit() for p in book.chapters[0].paragraphs)   # page numbers removed


def test_dsp_speed_and_eq():
    dsp = DSPChain(VoiceSettings(speed=2.0))
    tone = np.sin(np.linspace(0, 440 * 2 * np.pi, SAMPLE_RATE)).astype(np.float32) * 0.3
    assert abs(len(dsp.prosody(tone)) - SAMPLE_RATE // 2) < 200
    dsp.update(volume_db=-12)
    out = dsp.process_block(tone[:1024])
    assert np.abs(out).max() < np.abs(tone[:1024]).max() * 0.4
