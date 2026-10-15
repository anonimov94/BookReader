"""Main window: book text with the sentence being read highlighted, transport, voice panel,
translation, bookmarks, presets, sleep timer, export and tools."""
from __future__ import annotations

import os
import threading
import time

import sounddevice as sd
from PySide6.QtCore import QObject, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QFont, QKeySequence, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel,
                               QListWidget, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QSlider, QSplitter,
                               QStatusBar, QTextEdit, QVBoxLayout, QWidget)

from ..audio.dsp import DSPChain, VoiceSettings
from ..audio.player import Player, State
from ..export import Exporter, ExportJob, ExportCancelled
from ..library import Library
from ..loaders import load_book
from ..models import Book, Segment, SegmentKind
from ..paths import CACHE_DIR
from ..text.pronunciation import PronunciationDict, load_global_rules, save_global_rules
from ..text.segmenter import segment_book, segment_paragraph
from ..translate.deepl import DeepLTranslator
from ..translate.glossary import Glossary
from ..translate.ollama import OllamaTranslator
from ..translate.service import TranslationService
from ..tts.base import SAMPLE_RATE
from ..tts.cache import AudioCache
from ..tts.registry import EngineRegistry
from .common import run_async
from .dialogs import (BookmarksDialog, CompareDialog, EditTranslationDialog, ExportDialog, GlossaryDialog,
                      ProgressDialog, PronunciationDialog, TranslationSettingsDialog)
from .manager import ManagerDialog
from .setup_wizard import SetupWizard
from ..addons import ollama_setup

# (field, label, min, max, scale, unit)  – QSlider is integer, value = slider / scale
SLIDERS = [
    ("speed", "Prędkość", 50, 250, 100, "×"),
    ("pitch", "Wysokość", -80, 80, 10, " półt."),
    ("volume_db", "Głośność", -30, 12, 1, " dB"),
    ("bass_db", "Niskie tony", -15, 15, 1, " dB"),
    ("mid_db", "Średnie tony", -15, 15, 1, " dB"),
    ("treble_db", "Wysokie tony", -15, 15, 1, " dB"),
    ("sentence_pause_ms", "Pauza po zdaniu", 0, 1500, 1, " ms"),
    ("paragraph_pause_ms", "Pauza po akapicie", 0, 3000, 1, " ms"),
]
HIGHLIGHT = QColor(255, 214, 102)
PENDING_COLOR = QColor(140, 140, 140)
SAMPLE_TEXT = {"pl": "Dzień dobry. Tak będzie brzmiał lektor czytający Twoją książkę.",
               "en": "Hello. This is how the narrator will sound while reading your book."}
SLEEP_CHOICES = [(0, "Wyłączony"), (5, "5 min"), (10, "10 min"), (15, "15 min"), (30, "30 min"),
                 (45, "45 min"), (60, "60 min"), (90, "90 min"), (-1, "Do końca rozdziału")]
DEFAULT_VOICE = {"pl": "pl-PL-ZofiaNeural", "en": "en-US-AvaMultilingualNeural"}
ENGINE_FILTER = [("", "Wszystkie silniki"), ("edge", "Edge (online)"), ("piper", "Piper (offline)"), ("xtts", "XTTS (GPU)")]


class Bridge(QObject):
    """Carries callbacks from worker threads into the Qt thread."""
    segment = Signal(int)
    state = Signal(str)
    error = Signal(str)
    finished = Signal()
    timer_paused = Signal()
    voices_ready = Signal(list)
    book_loaded = Signal(object, str)
    book_failed = Signal(str)
    load_progress = Signal(str)
    translated = Signal(list)
    tr_status = Signal(str)
    tr_error = Signal(str)
    glossary = Signal(object)


class BookText(QPlainTextEdit):
    """Read-only book text; double-click starts reading from the clicked sentence."""
    double_clicked = Signal(int)   # character position

    def mouseDoubleClickEvent(self, event) -> None:
        self.double_clicked.emit(self.cursorForPosition(event.position().toPoint()).position())


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("BookReader")
        self.resize(1280, 800)
        self.library = Library()
        self.registry = EngineRegistry(xtts_license_accepted=self.library.settings.get("xtts_license", False))
        self.cache = AudioCache(CACHE_DIR)
        self.dsp = DSPChain(VoiceSettings.from_dict(self.library.settings.get("voice", {})))
        self.player: Player | None = None
        self.book: Book | None = None
        self.service: TranslationService | None = None
        self.glossary = Glossary()
        self.shown_chapter = -1
        self.para_offsets: dict[int, int] = {}     # paragraph index -> start of its spoken text in the view
        self.voices = []
        self._pending_book: str | None = None
        self._sleep_deadline: float | None = None
        self.global_rules = load_global_rules()

        b = self.bridge = Bridge()
        b.segment.connect(self._on_segment)
        b.state.connect(self._on_state)
        b.error.connect(lambda m: QMessageBox.warning(self, "Błąd", m))
        b.finished.connect(lambda: self.statusBar().showMessage("Koniec książki", 10000))
        b.timer_paused.connect(self._timer_fired)
        b.voices_ready.connect(self._on_voices)
        b.book_loaded.connect(self._on_book_loaded)
        b.book_failed.connect(lambda m: (QMessageBox.warning(self, "Nie udało się otworzyć", m),
                                         self.statusBar().showMessage("")))
        b.load_progress.connect(lambda m: self.statusBar().showMessage(m))
        b.translated.connect(self._on_translated)
        b.tr_status.connect(lambda m: self.tr_label.setText(m))
        b.tr_error.connect(self._on_tr_error)
        b.glossary.connect(self._on_glossary_built)

        self._save_timer = QTimer(self, interval=3000, singleShot=True, timeout=self._save)
        self._rerender_timer = QTimer(self, interval=250, singleShot=True, timeout=self._rerender)
        self._sleep_timer = QTimer(self, interval=1000, timeout=self._sleep_tick)
        self._last_tr_error = 0.0
        self._build_ui()
        self._restore_geometry()
        self.registry.xtts.on_log = self.bridge.load_progress.emit
        self.reload_voices()
        if not self.library.settings.get("setup_offered"):
            self.library.settings["setup_offered"] = True
            QTimer.singleShot(1200, lambda: self.open_setup(intro=True))

    # ================================================================== UI
    def _build_ui(self) -> None:
        mb = self.menuBar()
        # --- Plik
        m = mb.addMenu("&Plik")
        m.addAction(QAction("Otwórz…", self, shortcut=QKeySequence.Open, triggered=self.open_dialog))
        self.recent_menu = m.addMenu("Ostatnio otwierane")
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        m.addSeparator()
        m.addAction(QAction("Eksport audiobooka (MP3/M4B)…", self, shortcut="Ctrl+E", triggered=self.export))
        m.addSeparator()
        m.addAction(QAction("Zamknij", self, shortcut=QKeySequence.Quit, triggered=self.close))
        # --- Czytanie
        m = mb.addMenu("&Czytanie")
        for text, key, slot in [("Czytaj / pauza", "Space", self._toggle),
                                ("Poprzednie zdanie", "Left", lambda: self._call("previous")),
                                ("Następne zdanie", "Right", lambda: self._call("next")),
                                ("Poprzedni akapit", "Ctrl+Left", lambda: self._call("next_paragraph", -1)),
                                ("Następny akapit", "Ctrl+Right", lambda: self._call("next_paragraph", 1)),
                                ("Stop", "", lambda: self._call("stop"))]:
            act = QAction(text, self, triggered=slot)
            if key:
                act.setShortcut(QKeySequence(key))
                act.setShortcutContext(Qt.ApplicationShortcut)
            m.addAction(act)
        m.addSeparator()
        sleep = m.addMenu("Wyłącznik czasowy")
        group = QActionGroup(self)
        for minutes, label in SLEEP_CHOICES:
            act = QAction(label, self, checkable=True, checked=minutes == 0)
            act.triggered.connect(lambda _=False, mins=minutes: self._set_sleep(mins))
            group.addAction(act)
            sleep.addAction(act)
        self.sleep_group = group
        # --- Zakładki
        self.bm_menu = mb.addMenu("&Zakładki")
        self.bm_menu.aboutToShow.connect(self._fill_bookmarks_menu)
        self.addAction(QAction(self, shortcut="Ctrl+B", triggered=self.add_bookmark))
        # --- Tłumaczenie
        m = mb.addMenu("&Tłumaczenie")
        self.act_translate = QAction("Tłumacz na polski w locie", self, checkable=True, shortcut="Ctrl+T",
                                     triggered=self._translate_toggled)
        self.act_bilingual = QAction("Widok dwujęzyczny (oryginał pod tłumaczeniem)", self, checkable=True,
                                     checked=self.library.settings.get("bilingual", False),
                                     triggered=self._bilingual_toggled)
        m.addAction(self.act_translate)
        m.addAction(self.act_bilingual)
        m.addSeparator()
        m.addAction(QAction("Słownik postaci i stylu…", self, triggered=self.edit_glossary))
        m.addAction(QAction("Popraw tłumaczenie bieżącego akapitu…", self, shortcut="Ctrl+Shift+E",
                            triggered=self.edit_translation))
        m.addAction(QAction("Przetłumacz bieżący akapit ponownie", self, triggered=lambda: self.retranslate(False)))
        m.addAction(QAction("Przetłumacz bieżący rozdział ponownie", self, triggered=lambda: self.retranslate(True)))
        m.addSeparator()
        m.addAction(QAction("Porównaj tłumaczy na bieżącym fragmencie…", self, triggered=self.compare))
        m.addAction(QAction("Ustawienia tłumaczenia…", self, triggered=self.translation_settings))
        # --- Narzędzia
        m = mb.addMenu("&Narzędzia")
        m.addAction(QAction("Słownik wymowy…", self, shortcut="Ctrl+P", triggered=self.edit_pronunciation))
        m.addAction(QAction("Głosy i modele…", self, shortcut="Ctrl+M", triggered=self.open_manager))
        m.addAction(QAction("Instalator dodatków…", self, triggered=lambda: self.open_setup()))

        # --- left: chapters
        self.chapters = QListWidget()
        self.chapters.itemClicked.connect(lambda item: self._jump_chapter(self.chapters.row(item)))

        # --- center: text + transport
        self.text = BookText(readOnly=True)
        self.text.setStyleSheet("QPlainTextEdit { font-size: 15px; padding: 12px; }")
        self.text.double_clicked.connect(self._text_double_click)
        self.text.setPlaceholderText("Plik → Otwórz… (Ctrl+O) i wybierz książkę EPUB lub PDF.\n\n"
                                     "Spacja – czytaj/pauza · ←/→ – zdanie · Ctrl+←/→ – akapit\n"
                                     "Podwójne kliknięcie w tekst – czytaj od tego miejsca · Ctrl+B – zakładka\n"
                                     "Ctrl+T – tłumaczenie na polski w locie")
        self.btn_play = QPushButton("▶ Czytaj", clicked=self._toggle)
        self.btn_play.setMinimumWidth(130)
        transport = QHBoxLayout()
        for w in (QPushButton("⏮ akapit", clicked=lambda: self._call("next_paragraph", -1)),
                  QPushButton("◀ zdanie", clicked=lambda: self._call("previous")),
                  self.btn_play,
                  QPushButton("■ Stop", clicked=lambda: self._call("stop")),
                  QPushButton("zdanie ▶", clicked=lambda: self._call("next")),
                  QPushButton("akapit ⏭", clicked=lambda: self._call("next_paragraph", 1)),
                  QPushButton("🔖 Zakładka", clicked=self.add_bookmark)):
            transport.addWidget(w)
        transport.addStretch()
        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(self.text)
        cl.addLayout(transport)

        # --- right: voice panel
        self.engine_filter = QComboBox()
        for eid, label in ENGINE_FILTER:
            self.engine_filter.addItem(label, eid)
        self.engine_filter.currentIndexChanged.connect(self._fill_voices)
        self.gender = QComboBox()
        self.gender.addItems(["Wszystkie", "Kobiece", "Męskie"])
        self.gender.currentIndexChanged.connect(self._fill_voices)
        self.voice_box = QComboBox()
        self.voice_box.setMinimumWidth(260)
        self.voice_box.currentIndexChanged.connect(self._voice_changed)
        self.voice_box.addItem("Wczytywanie głosów…")
        listen = QPushButton("🔊 Odsłuchaj próbkę", clicked=lambda: self.speak_sample())

        self.preset_box = QComboBox()
        self.preset_box.setMinimumWidth(160)
        self.preset_box.activated.connect(self._apply_preset)
        save_p = QPushButton("Zapisz", clicked=self._save_preset)
        del_p = QPushButton("Usuń", clicked=self._delete_preset)
        prow = QHBoxLayout()
        prow.addWidget(self.preset_box, 1)
        prow.addWidget(save_p)
        prow.addWidget(del_p)

        form = QFormLayout()
        form.addRow("Silnik", self.engine_filter)
        form.addRow("Płeć", self.gender)
        form.addRow("Lektor", self.voice_box)
        form.addRow("", listen)
        form.addRow("Ustawienia", prow)
        self.sliders: dict[str, QSlider] = {}
        for field, label, lo, hi, scale, unit in SLIDERS:
            s = QSlider(Qt.Horizontal, minimum=lo, maximum=hi)
            s.setMinimumWidth(170)
            s.setValue(round(getattr(self.dsp.settings, field) * scale))
            value = QLabel()
            value.setMinimumWidth(70)
            s.valueChanged.connect(lambda v, f=field, sc=scale, u=unit, lb=value: self._slider(f, v, sc, u, lb))
            self._slider(field, s.value(), scale, unit, value, save=False)
            row = QHBoxLayout()
            row.addWidget(s)
            row.addWidget(value)
            form.addRow(label, row)
            self.sliders[field] = s
        form.addRow(QPushButton("Przywróć domyślne", clicked=self._reset_sliders))
        box = QGroupBox("Lektor")
        box.setLayout(form)
        side = QWidget()
        sl = QVBoxLayout(side)
        sl.addWidget(box)
        sl.addStretch()
        side.setMinimumWidth(430)
        self._fill_presets()

        self.split = QSplitter()
        self.split.addWidget(self.chapters)
        self.split.addWidget(center)
        self.split.addWidget(side)
        self.split.setSizes([230, 620, 430])
        self.setCentralWidget(self.split)

        sb = QStatusBar()
        self.tr_label = QLabel()
        self.sleep_label = QLabel()
        sb.addPermanentWidget(self.tr_label)
        sb.addPermanentWidget(self.sleep_label)
        self.setStatusBar(sb)

    def _restore_geometry(self):
        qs = QSettings("BookReader", "BookReader")
        if qs.value("geometry"):
            self.restoreGeometry(qs.value("geometry"))
        if qs.value("split"):
            self.split.restoreState(qs.value("split"))

    # ================================================================== voices
    def reload_voices(self) -> None:
        def work(report):
            return self.registry.voices()

        def failed(m):
            QMessageBox.warning(self, "Głosy", f"Nie można pobrać listy głosów: {m}")
        run_async(work, on_done=self.bridge.voices_ready.emit, on_failed=failed)

    def _on_voices(self, voices: list) -> None:
        self.voices = voices
        if "edge" in self.registry.errors:
            self.statusBar().showMessage("Głosy Edge niedostępne (brak internetu?) — użyj Piper lub XTTS.", 10000)
        self._fill_voices()
        path = self._pending_book or (self.library.last_book if self.book is None else None)
        self._pending_book = None
        if path and os.path.exists(path):
            self.open_book(path)

    @property
    def speech_lang(self) -> str:
        if self.translating:
            return "pl"
        return (self.book.language if self.book and self.book.language else "pl")

    def _fill_voices(self) -> None:
        if not self.voices:
            return
        lang = self.speech_lang
        want = {1: "K", 2: "M"}.get(self.gender.currentIndex())
        engine = self.engine_filter.currentData()
        # The narrator is remembered per language (Polish book vs English original).
        current = self.library.settings.get(f"voice_id_{lang}", DEFAULT_VOICE.get(lang, "pl-PL-ZofiaNeural"))
        self.voice_box.blockSignals(True)
        self.voice_box.clear()
        icon = {"K": "♀", "M": "♂"}
        for v in self.voices:
            if lang in v.languages and (want is None or v.gender == want) and (not engine or v.engine == engine):
                self.voice_box.addItem(f"{icon.get(v.gender, '◦')} {v.name}", v.id)
        i = self.voice_box.findData(current)
        self.voice_box.setCurrentIndex(max(i, 0))
        self.voice_box.blockSignals(False)
        self._voice_changed()

    def current_voice(self):
        vid = self.voice_box.currentData()
        return next((v for v in self.voices if v.id == vid), None)

    def _voice_changed(self) -> None:
        voice = self.current_voice()
        if voice is None:
            return
        self.library.settings[f"voice_id_{self.speech_lang}"] = voice.id
        self._save_timer.start()
        if self.player and self.player.voice != voice:
            self.player.set_voice(voice)

    def speak_sample(self, text: str | None = None) -> None:
        voice = self.current_voice()
        if voice is None:
            return
        text = text or SAMPLE_TEXT.get(self.speech_lang, SAMPLE_TEXT["pl"])
        engine = self.registry.engine(voice)
        settings = VoiceSettings.from_dict(self.dsp.settings.to_dict())
        transform = self._pronunciation().apply
        lang = self.speech_lang

        def work(report):
            from ..text.normalizer import normalize
            audio = self.cache.get_or_synthesize(engine, voice, normalize(transform(text), lang, engine.expand_numbers), lang)
            d = DSPChain(settings)
            return d.process_block(d.prosody(audio))
        run_async(work, on_done=lambda a: sd.play(a, SAMPLE_RATE),
                  on_failed=lambda m: QMessageBox.warning(self, "Próbka", m))

    # ================================================================== sliders & presets
    def _slider(self, field, raw, scale, unit, label, save=True) -> None:
        value = raw / scale
        if isinstance(getattr(VoiceSettings(), field), int):
            value = int(value)
        self.dsp.update(**{field: value})
        label.setText(f"{value:.2f}{unit}" if scale == 100 else f"{value:+.1f}{unit}" if scale == 10
                      else f"{value:+.0f}{unit}" if "dB" in unit else f"{value}{unit}")
        if save:
            self.library.settings["voice"] = self.dsp.settings.to_dict()
            self._save_timer.start()

    def _set_sliders(self, settings: VoiceSettings) -> None:
        for field, _, _, _, scale, _ in SLIDERS:
            self.sliders[field].setValue(round(getattr(settings, field) * scale))
        self.dsp.update(heading_pause_ms=settings.heading_pause_ms)

    def _reset_sliders(self) -> None:
        self._set_sliders(VoiceSettings())

    def _fill_presets(self) -> None:
        self.preset_box.clear()
        self.preset_box.addItem("— wybierz zapisane —", None)
        for name in sorted(self.library.presets):
            self.preset_box.addItem(name, name)

    def _save_preset(self) -> None:
        name, ok = QInputDialog.getText(self, "Zapisz ustawienia lektora", "Nazwa (np. „Wieczorem”, „Kryminał”):")
        if ok and name.strip():
            self.library.presets[name.strip()] = {"voice_id": self.voice_box.currentData(),
                                                  "settings": self.dsp.settings.to_dict()}
            self._fill_presets()
            self.preset_box.setCurrentIndex(self.preset_box.findData(name.strip()))
            self._save()

    def _apply_preset(self, _index) -> None:
        name = self.preset_box.currentData()
        if not name:
            return
        p = self.library.presets[name]
        self._set_sliders(VoiceSettings.from_dict(p["settings"]))
        i = self.voice_box.findData(p.get("voice_id"))
        if i < 0 and p.get("voice_id"):
            self.engine_filter.setCurrentIndex(0)
            self.gender.setCurrentIndex(0)
            i = self.voice_box.findData(p.get("voice_id"))
        if i >= 0:
            self.voice_box.setCurrentIndex(i)

    def _delete_preset(self) -> None:
        name = self.preset_box.currentData()
        if name and QMessageBox.question(self, "Usuń", f"Usunąć ustawienia „{name}”?") == QMessageBox.Yes:
            self.library.presets.pop(name, None)
            self._fill_presets()
            self._save()

    # ================================================================== opening books
    def open_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Otwórz książkę", os.path.dirname(self.library.last_book or ""),
                                              "Książki (*.epub *.pdf)")
        if path:
            self.open_book(path)

    def _fill_recent(self) -> None:
        self.recent_menu.clear()
        for path, title in self.library.recent():
            self.recent_menu.addAction(QAction(title, self, triggered=lambda _=False, p=path: self.open_book(p)))
        if self.recent_menu.isEmpty():
            self.recent_menu.addAction(QAction("(brak)", self, enabled=False))

    def open_book(self, path: str) -> None:
        if not self.voices:            # opened before the voice list arrived: finish later
            self._pending_book = path
            self.statusBar().showMessage("Czekam na listę głosów…")
            return
        self.statusBar().showMessage(f"Wczytywanie {os.path.basename(path)}…")

        def work():
            try:
                self.bridge.book_loaded.emit(load_book(path, self.bridge.load_progress.emit), path)
            except Exception as e:
                self.bridge.book_failed.emit(str(e))
        threading.Thread(target=work, daemon=True).start()

    def _on_book_loaded(self, book: Book, path: str) -> None:
        if not any(c.paragraphs for c in book.chapters):
            msg = "Nie znaleziono tekstu w tej książce."
            if book.needs_ocr_pages:
                msg += ("\n\nTo skan. Pobierz dane OCR (Narzędzia → Głosy i modele → OCR) i otwórz książkę ponownie.")
            QMessageBox.information(self, "Brak tekstu", msg)
            return
        self._close_book()
        self.book = book
        self.library.last_book = path
        rec = self.library.book(path)
        self.glossary = Glossary.from_dict(rec.get("glossary"))
        self.setWindowTitle(f"{book.title} — BookReader")
        self.chapters.clear()
        self.chapters.addItems([c.title for c in book.chapters])

        translate = rec.get("translate")
        if translate is None and book.language == "en":
            answer = QMessageBox.question(self, "Książka po angielsku",
                                          "Ta książka jest po angielsku. Czy tłumaczyć ją na polski w locie?\n\n"
                                          "(Możesz to zmienić w menu Tłumaczenie → Tłumacz na polski, Ctrl+T)")
            translate = answer == QMessageBox.Yes
            rec["translate"] = translate
        self.act_translate.setChecked(bool(translate) and book.language != "pl")
        if self.act_translate.isChecked() and not self._start_translation():
            self.act_translate.setChecked(False)

        self._fill_voices()
        voice = self.current_voice()
        if self.player is None:
            self.player = Player(self.registry, voice, self.dsp, self.cache)
            p = self.player
            p.on_segment = self.bridge.segment.emit
            p.on_state = self.bridge.state.emit
            p.on_error = lambda e: self.bridge.error.emit(str(e))
            p.on_finished = self.bridge.finished.emit
            p.on_paused_by_timer = self.bridge.timer_paused.emit
        self.player.text_transform = self._pronunciation().apply
        self._reload_segments(self.library.position(path))
        extra = f" · {len(book.needs_ocr_pages)} stron bez tekstu (pobierz dane OCR)" if book.needs_ocr_pages else ""
        lang = {"pl": "polski", "en": "angielski"}.get(book.language, book.language or "?")
        self.statusBar().showMessage(f"{book.title} · {book.author} · {len(book.chapters)} rozdz. · język: {lang}{extra}")

    def _close_book(self) -> None:
        if self.player:
            self.player.stop()
        if self.service:
            self.service.stop()
            self.service = None
        self.tr_label.setText("")

    def _reload_segments(self, position) -> None:
        """Rebuild segments (original or translated) and keep the reading position."""
        text_for = self.service.text_for if self.service else None
        segments = segment_book(self.book, text_for)
        self.player.load(segments, lang=self.speech_lang)
        self.player.index = self.player.index_of(position)
        self.shown_chapter = -1
        self._on_segment(self.player.index)

    @property
    def segments(self) -> list[Segment]:
        return self.player.segments if self.player else []

    # ================================================================== translation
    @property
    def translating(self) -> bool:
        return self.service is not None

    def _make_translator(self):
        s = self.library.settings
        if s.get("translator") == "deepl":
            return DeepLTranslator(s.get("deepl_key", ""), s.get("deepl_formality", "prefer_less"))
        return OllamaTranslator(s.get("ollama_model", "gemma3:12b"))

    def _start_translation(self) -> bool:
        tr = self._make_translator()
        if isinstance(tr, OllamaTranslator) and not ollama_setup.is_running():
            if ollama_setup.find_ollama():
                self.statusBar().showMessage("Uruchamianie Ollamy…")
                QApplication.setOverrideCursor(Qt.WaitCursor)
                try:
                    ollama_setup.ensure_running()
                finally:
                    QApplication.restoreOverrideCursor()
            else:
                ans = QMessageBox.question(self, "Brak tłumacza",
                                           "Do tłumaczenia offline potrzebna jest Ollama z modelem językowym (~9 GB).\n\n"
                                           "Otworzyć instalator dodatków? (Albo wpisz klucz DeepL w ustawieniach tłumaczenia.)")
                if ans == QMessageBox.Yes:
                    self.open_setup(["ollama", "gemma"])
                    if not ollama_setup.find_ollama():
                        return False
                else:
                    return False
        ok, why = tr.available()
        if not ok:
            ans = QMessageBox.question(self, "Tłumacz niedostępny",
                                       f"{why}\n\nOtworzyć ustawienia tłumaczenia?",
                                       QMessageBox.Yes | QMessageBox.No)
            if ans == QMessageBox.Yes:
                self.translation_settings()
            return False
        key = os.path.abspath(self.book.path)
        svc = TranslationService(self.book, key, tr, self.glossary, source_lang=self.book.language or "en")
        if self.library.book(self.book.path).get("glossary_built"):
            svc.need_glossary = False
        svc.on_paragraphs = self.bridge.translated.emit
        svc.on_status = self.bridge.tr_status.emit
        svc.on_error = self.bridge.tr_error.emit
        svc.on_glossary = self.bridge.glossary.emit
        self.service = svc
        pos = self.library.position(self.book.path)
        svc.focus(pos[0], pos[1])
        svc.start()
        self.tr_label.setText(f"Tłumacz: {tr.name}")
        return True

    def _translate_toggled(self, on: bool) -> None:
        if not self.book:
            self.act_translate.setChecked(False)
            return
        pos = self.player.position_of(self.player.index) if self.player else (0, -1, 0)
        was_playing = self.player and self.player.state in (State.PLAYING, State.LOADING)
        if on:
            if not self._start_translation():
                self.act_translate.setChecked(False)
                return
        else:
            self._close_book()
        self.library.book(self.book.path)["translate"] = on
        self._fill_voices()
        self._reload_segments(pos)
        if was_playing:
            self.player.play()

    def _restart_translation(self) -> None:
        if self.translating:
            pos = self.player.position_of(self.player.index)
            self.service.stop()
            self.service = None
            if self._start_translation():
                self._reload_segments(pos)
            else:
                self.act_translate.setChecked(False)
                self._reload_segments(pos)

    def _on_translated(self, items: list) -> None:
        if not self.player or not self.service:
            return
        for ci, pi, text in items:
            self.player.replace_paragraph(ci, pi, segment_paragraph(ci, pi, text))
            if ci == self.shown_chapter:
                self._rerender_timer.start()

    def _on_tr_error(self, msg: str) -> None:
        self.tr_label.setText("⚠ " + msg[:120])
        if time.time() - self._last_tr_error > 60:        # don't flood with dialogs
            self._last_tr_error = time.time()
            self.statusBar().showMessage(msg, 15000)

    def _on_glossary_built(self, g: Glossary) -> None:
        self.glossary = g
        rec = self.library.book(self.book.path)
        rec["glossary"] = g.to_dict()
        rec["glossary_built"] = True
        self._save()
        self.statusBar().showMessage(f"Słownik postaci gotowy: {len(g.entries)} pozycji "
                                     "(Tłumaczenie → Słownik postaci i stylu)", 10000)

    def _bilingual_toggled(self, on: bool) -> None:
        self.library.settings["bilingual"] = on
        self._rerender()

    def translation_settings(self) -> None:
        dlg = TranslationSettingsDialog(self, self.library.settings)
        if dlg.exec():
            self._save()
            self._restart_translation()

    def edit_glossary(self) -> None:
        if not self.book:
            return
        tr = self._make_translator()
        rebuild = None
        if isinstance(tr, OllamaTranslator):
            paras = [p for c in self.book.chapters for p in c.paragraphs][:400]
            rebuild = lambda g: tr.build_glossary(paras, g)
        dlg = GlossaryDialog(self, self.glossary, rebuild)
        if dlg.exec():
            self.glossary = dlg.glossary
            rec = self.library.book(self.book.path)
            rec["glossary"] = self.glossary.to_dict()
            rec["glossary_built"] = True
            if self.service:
                self.service.set_glossary(self.glossary)
            self._save()

    def _current_paragraph(self) -> tuple[int, int] | None:
        if not self.player or not self.segments:
            return None
        s = self.segments[self.player.index]
        return s.chapter, s.paragraph

    def edit_translation(self) -> None:
        cur = self._current_paragraph()
        if not cur or not self.service:
            QMessageBox.information(self, "Tłumaczenie", "Włącz tłumaczenie (Ctrl+T), aby poprawiać akapity.")
            return
        src = self.service.source(*cur)
        dlg = EditTranslationDialog(self, src, self.service.text_for(*cur) or "")
        if dlg.exec() and dlg.text:
            self.service.set_manual(cur[0], cur[1], dlg.text)

    def retranslate(self, whole_chapter: bool) -> None:
        cur = self._current_paragraph()
        if not cur or not self.service:
            return
        keys = self.service.retranslate(cur[0], None if whole_chapter else cur[1])
        for ci, pi in keys:
            self.player.replace_paragraph(ci, pi, segment_paragraph(ci, pi, None, self.service.source(ci, pi)))
        self._rerender()
        if self.player.state in (State.PLAYING, State.LOADING):
            self.player.seek(self.player.index)

    def compare(self) -> None:
        if not self.book:
            return
        cur = self._current_paragraph() or (0, 0)
        ch = self.book.chapters[cur[0]]
        start = max(0, cur[1])
        text = "\n".join(ch.paragraphs[start:start + 2])
        dlg = CompareDialog(self, text, self.glossary, self.library.settings, self.book.title)
        if dlg.exec() and dlg.chosen_model:
            self._save()
            self.statusBar().showMessage(f"Wybrano tłumacza: {dlg.chosen_model}", 8000)
            self._restart_translation()

    # ================================================================== pronunciation & tools
    def _pronunciation(self) -> PronunciationDict:
        book_rules = []
        if self.book:
            book_rules = PronunciationDict.rules_from(self.library.book(self.book.path)["pronunciation"])
        return PronunciationDict(book_rules + self.global_rules)

    def edit_pronunciation(self) -> None:
        book_rules = PronunciationDict.rules_from(self.library.book(self.book.path)["pronunciation"]) if self.book else None
        dlg = PronunciationDialog(self, self.global_rules, book_rules, speak=self.speak_sample)
        if dlg.exec():
            self.global_rules = dlg.global_tab.rules()
            save_global_rules(self.global_rules)
            if self.book:
                self.library.book(self.book.path)["pronunciation"] = PronunciationDict.rules_to(dlg.book_tab.rules())
                self._save()
            if self.player:
                self.player.text_transform = self._pronunciation().apply
                self.player.invalidate()

    def open_manager(self) -> None:
        while True:
            dlg = ManagerDialog(self, self.registry, self.library.settings)
            dlg.changed.connect(self.reload_voices)
            dlg.exec()
            self._save()
            if not dlg.reopen:
                break

    def open_setup(self, preselect: list[str] | None = None, intro: bool = False) -> None:
        wiz = SetupWizard(self, self.library.settings, preselect, intro)
        wiz.changed.connect(self.reload_voices)
        wiz.exec()
        self.registry.xtts.license_accepted = self.library.settings.get("xtts_license", False)
        self._save()

    # ================================================================== bookmarks
    def add_bookmark(self) -> None:
        if not self.book or not self.segments:
            return
        pos = self.player.position_of(self.player.index)
        seg = self.segments[self.player.index]
        default = f"{self.book.chapters[pos[0]].title} — {time.strftime('%d.%m %H:%M')}"
        label, ok = QInputDialog.getText(self, "Nowa zakładka", "Nazwa:", text=default)
        if ok:
            self.library.book(self.book.path)["bookmarks"].append(
                {"label": label.strip() or default, "position": list(pos), "preview": seg.text[:120]})
            self._save()
            self.statusBar().showMessage("Dodano zakładkę", 4000)

    def _fill_bookmarks_menu(self) -> None:
        m = self.bm_menu
        m.clear()
        m.addAction(QAction("Dodaj zakładkę", self, shortcut="Ctrl+B", triggered=self.add_bookmark))
        m.addAction(QAction("Zarządzaj zakładkami…", self, triggered=self.manage_bookmarks))
        if self.book:
            marks = self.library.book(self.book.path)["bookmarks"]
            if marks:
                m.addSeparator()
            for b in marks[-15:]:
                m.addAction(QAction(b["label"], self, triggered=lambda _=False, b=b: self._goto_position(b["position"])))

    def manage_bookmarks(self) -> None:
        if not self.book:
            return
        marks = self.library.book(self.book.path)["bookmarks"]
        dlg = BookmarksDialog(self, marks, [c.title for c in self.book.chapters])
        dlg.exec()
        self._save()
        if dlg.chosen:
            self._goto_position(dlg.chosen["position"])

    def _goto_position(self, pos) -> None:
        if self.player:
            self.player.seek(self.player.index_of(tuple(pos)))

    # ================================================================== sleep timer
    def _set_sleep(self, minutes: int) -> None:
        self._sleep_deadline = None
        if self.player:
            self.player.pause_before_chapter = None
        if minutes > 0:
            self._sleep_deadline = time.time() + minutes * 60
            self._sleep_timer.start()
        elif minutes == -1 and self.player and self.segments:
            self.player.pause_before_chapter = self.segments[self.player.index].chapter
            self.sleep_label.setText("⏾ do końca rozdziału")
            return
        else:
            self._sleep_timer.stop()
        self._sleep_tick()

    def _sleep_tick(self) -> None:
        if self._sleep_deadline is None:
            self.sleep_label.setText("")
            self._sleep_timer.stop()
            return
        left = self._sleep_deadline - time.time()
        if left <= 0:
            self._sleep_deadline = None
            self._sleep_timer.stop()
            self._call("pause")
            self._timer_fired()
            return
        self.sleep_label.setText(f"⏾ {int(left // 60)}:{int(left % 60):02d}")

    def _timer_fired(self) -> None:
        self.sleep_label.setText("")
        self.sleep_group.actions()[0].setChecked(True)
        self.statusBar().showMessage("Wyłącznik czasowy zatrzymał czytanie. Dobranoc!", 15000)

    # ================================================================== export
    def export(self) -> None:
        if not self.book:
            return
        voice = self.current_voice()
        default_dir = os.path.join(os.path.expanduser("~"), "Music", "BookReader",
                                   "".join(c for c in self.book.title if c not in '<>:"/\\|?*')[:60])
        cur = self.segments[self.player.index].chapter if self.segments else 0
        dlg = ExportDialog(self, [c.title for c in self.book.chapters], default_dir, cur)
        if not dlg.exec() or not dlg.selected:
            return
        chapters = dlg.selected
        service, book = self.service, self.book
        settings = VoiceSettings.from_dict(self.dsp.settings.to_dict())
        transform = self._pronunciation().apply
        lang = self.speech_lang
        fmt, folder, bitrate = dlg.fmt, dlg.folder.text(), f"{dlg.quality.value()}k"
        prog = ProgressDialog(self, "Eksport audiobooka")

        def work(report):
            if service:      # make sure the chosen chapters are translated first
                service.translate_all = True
                needed = [k for k in service.order if k[0] in chapters]
                if needed:
                    service.focus(*needed[0])
                while not all(service.chapter_done(c) for c in chapters):
                    if prog.cancel.is_set():
                        raise ExportCancelled()
                    done = sum(k in service.done for k in needed)
                    report((done, len(needed), f"Tłumaczę wybrane rozdziały: {done}/{len(needed)} akapitów"))
                    nxt = next((k for k in needed if k not in service.done), None)
                    if nxt:
                        service.focus(*nxt)
                    time.sleep(1)
                service.translate_all = False
            segs = segment_book(book, service.text_for if service else None)
            job = ExportJob(folder, fmt, book.title, book.author,
                            [(service.text_for(c, -1) if service and service.text_for(c, -1) else book.chapters[c].title,
                              [s for s in segs if s.chapter == c and s.kind != SegmentKind.PENDING]) for c in chapters],
                            voice, settings, lang, book.cover, transform, m4b_bitrate=bitrate)
            return Exporter(self.registry, self.cache).run(job, lambda d, t, m: report((d, t, m)), prog.cancel)

        def failed(m):
            prog.finish("Eksport anulowany." if not m or "ExportCancelled" in m else f"Błąd: {m}")

        run_async(work, on_progress=prog.update_progress,
                  on_done=lambda paths: prog.finish(f"Gotowe: {len(paths)} plik(i) w {folder}", paths),
                  on_failed=failed)
        prog.exec()

    # ================================================================== text view
    def _formats(self):
        normal = QTextCharFormat()
        title = QTextCharFormat()
        title.setFontWeight(QFont.Bold)
        title.setFontPointSize(15)
        pending = QTextCharFormat()
        pending.setForeground(PENDING_COLOR)
        pending.setFontItalic(True)
        original = QTextCharFormat()
        original.setForeground(QColor(120, 120, 140))
        original.setFontPointSize(9.5)
        return normal, title, pending, original

    def _show_chapter(self, ci: int) -> None:
        ch = self.book.chapters[ci]
        normal, title_fmt, pending_fmt, original_fmt = self._formats()
        bilingual = self.act_bilingual.isChecked() and self.translating
        doc = self.text.document()
        doc.clear()
        cur = QTextCursor(doc)
        offsets: dict[int, int] = {}
        first = True
        for pi, source in [(-1, ch.title)] + list(enumerate(ch.paragraphs)):
            if pi == -1 and not source:
                continue
            if not first:
                cur.insertText("\n\n", normal)
            first = False
            spoken = self.service.text_for(ci, pi) if self.translating else source
            if spoken is None:                       # translation pending
                cur.insertText("⏳ ", pending_fmt)
                offsets[pi] = cur.position()
                cur.insertText(source, pending_fmt)
                continue
            offsets[pi] = cur.position()
            cur.insertText(spoken, title_fmt if pi == -1 else normal)
            if bilingual:
                cur.insertText("\n" + source, original_fmt)
        self.para_offsets = offsets
        self.shown_chapter = ci
        self.chapters.setCurrentRow(ci)

    def _rerender(self) -> None:
        if self.book and self.player and self.segments:
            self.shown_chapter = -1
            self._on_segment(self.player.index, scroll=False)

    def _on_segment(self, index: int, scroll: bool = True) -> None:
        if not self.segments or not self.book:
            return
        index = max(0, min(index, len(self.segments) - 1))
        seg = self.segments[index]
        if seg.chapter != self.shown_chapter:
            self._show_chapter(seg.chapter)
            scroll = True
        if seg.paragraph not in self.para_offsets:
            return
        base = self.para_offsets[seg.paragraph]
        cur = QTextCursor(self.text.document())
        end = min(base + seg.end, self.text.document().characterCount() - 1)
        cur.setPosition(min(base + seg.start, end))
        cur.setPosition(end, QTextCursor.KeepAnchor)
        sel = QTextEdit.ExtraSelection()
        sel.cursor = cur
        fmt = QTextCharFormat()
        fmt.setBackground(HIGHLIGHT)
        fmt.setForeground(QColor(20, 20, 20))
        sel.format = fmt
        self.text.setExtraSelections([sel])
        if scroll:
            view = QTextCursor(cur)
            view.clearSelection()
            self.text.setTextCursor(view)
            self.text.centerCursor()
        self.library.set_position(self.book.path, self.player.position_of(index), self.book.title)
        if self.service:
            self.service.focus(seg.chapter, seg.paragraph)
        self._save_timer.start()

    def _text_double_click(self, pos: int) -> None:
        if not self.segments or not self.para_offsets:
            return
        before = [p for p, off in self.para_offsets.items() if off <= pos]
        if not before:
            return
        para = max(before, key=lambda p: self.para_offsets[p])
        rel = pos - self.para_offsets[para]
        candidates = [i for i, s in enumerate(self.segments) if s.chapter == self.shown_chapter and s.paragraph == para]
        if candidates:
            target = next((i for i in candidates if self.segments[i].end >= rel), candidates[-1])
            self.player.seek(target)
            if self.player.state == State.STOPPED:
                self.player.play()

    def _jump_chapter(self, row: int) -> None:
        if self.player:
            self.player.seek(self.player.first_segment_of_chapter(row))

    # ================================================================== transport
    def _call(self, method: str, *args) -> None:
        if self.player:
            getattr(self.player, method)(*args)

    def _toggle(self) -> None:
        if self.player:
            self.player.toggle()
        else:
            self.open_dialog()

    def _on_state(self, state: str) -> None:
        loading = "⏳ Tłumaczę…" if (self.segments and self.segments[self.player.index].kind == SegmentKind.PENDING) \
            else "⏳ Generuję…"
        self.btn_play.setText({State.PLAYING: "⏸ Pauza", State.LOADING: loading}.get(state, "▶ Czytaj"))

    def _save(self) -> None:
        try:
            self.library.save()
        except OSError:
            pass

    def closeEvent(self, event) -> None:
        if self.player:
            self.player.shutdown()
        if self.service:
            self.service.stop()
        qs = QSettings("BookReader", "BookReader")
        qs.setValue("geometry", self.saveGeometry())
        qs.setValue("split", self.split.saveState())
        self._save()
        super().closeEvent(event)
