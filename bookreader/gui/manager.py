"""'Voices and models' manager: Piper voices, XTTS, Ollama models, OCR data.

Every download here starts only when the user clicks a button.
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QListWidget, QMessageBox, QProgressBar, QPushButton, QTableWidget,
                               QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget, QHeaderView)

from .. import ocr
from ..downloads import download
from ..tts import piper_engine
from ..tts.registry import EngineRegistry
from ..addons import ollama_setup, xtts_runtime
from ..tts.xtts_engine import LICENSE_URL, XTTSEngine
from ..translate.ollama import SUGGESTED_MODELS, OllamaTranslator
from .common import human_mb, run_async



class ManagerDialog(QDialog):
    changed = Signal()          # voices / models changed -> main window refreshes

    def __init__(self, parent, registry: EngineRegistry, settings: dict):
        super().__init__(parent)
        self.setWindowTitle("Głosy i modele")
        self.resize(860, 600)
        self.registry, self.settings = registry, settings
        self.reopen = False
        tabs = QTabWidget()
        tabs.addTab(self._piper_tab(), "Piper (offline)")
        tabs.addTab(self._xtts_tab(), "XTTS (klonowanie głosu)")
        tabs.addTab(self._ollama_tab(), "Ollama (tłumacz)")
        tabs.addTab(self._ocr_tab(), "OCR")
        lay = QVBoxLayout(self)
        lay.addWidget(tabs)
        lay.addWidget(QDialogButtonBox(QDialogButtonBox.Close, rejected=self.reject))

    # ------------------------------------------------------------------ helpers
    def _download_files(self, files: list[tuple[str, str]], bar: QProgressBar, button: QPushButton, on_done):
        """files: [(url, dest)]"""
        button.setEnabled(False)
        bar.show()

        def work(report):
            for i, (url, dest) in enumerate(files):
                download(url, dest, lambda d, t, i=i: report((i, len(files), d, t)))

        def prog(v):
            i, n, d, t = v
            bar.setMaximum(max(1, t))
            bar.setValue(d)
            bar.setFormat(f"plik {i + 1}/{n}: {human_mb(d)} / {human_mb(t) if t else '?'}")

        def done(_):
            bar.hide()
            button.setEnabled(True)
            on_done()
            self.changed.emit()

        def failed(m):
            bar.hide()
            button.setEnabled(True)
            QMessageBox.warning(self, "Pobieranie nieudane", m)
        run_async(work, on_done=done, on_failed=failed, on_progress=prog)

    @staticmethod
    def _bar() -> QProgressBar:
        b = QProgressBar()
        b.setTextVisible(True)
        b.hide()
        return b

    # ------------------------------------------------------------------ Piper
    def _piper_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        if not piper_engine.PiperEngine.available():
            lay.addWidget(QLabel("Pakiet piper-tts nie jest zainstalowany: .venv\\Scripts\\pip install piper-tts"))
            return w
        lay.addWidget(QLabel("Głosy Piper działają bez internetu, szybko, na procesorze. "
                             "Źródło: huggingface.co/rhasspy/piper-voices"))
        self.piper_table = QTableWidget(len(piper_engine.CATALOG), 4)
        self.piper_table.setHorizontalHeaderLabels(["Głos", "Płeć", "Rozmiar", ""])
        self.piper_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.piper_table.verticalHeader().hide()
        self.piper_bar = self._bar()
        self._fill_piper()
        lay.addWidget(self.piper_table)
        lay.addWidget(self.piper_bar)
        return w

    def _fill_piper(self):
        for r, e in enumerate(piper_engine.CATALOG):
            self.piper_table.setItem(r, 0, QTableWidgetItem(e.label))
            self.piper_table.setItem(r, 1, QTableWidgetItem("♀" if e.gender == "K" else "♂"))
            self.piper_table.setItem(r, 2, QTableWidgetItem(f"{e.size_mb} MB"))
            if piper_engine.is_installed(e.key):
                btn = QPushButton("Usuń")
                btn.clicked.connect(lambda _=False, k=e.key: self._piper_remove(k))
            else:
                btn = QPushButton("Pobierz")
                onnx, js = e.urls()
                dest = piper_engine.model_path(e.key)
                btn.clicked.connect(lambda _=False, f=[(js, dest + ".json"), (onnx, dest)], b=btn:
                                    self._download_files(f, self.piper_bar, b, self._fill_piper))
            self.piper_table.setCellWidget(r, 3, btn)

    def _piper_remove(self, key: str):
        for p in (piper_engine.model_path(key), piper_engine.model_path(key) + ".json"):
            if os.path.exists(p):
                os.remove(p)
        self._fill_piper()
        self.changed.emit()

    # ------------------------------------------------------------------ XTTS
    def _open_installer(self, keys: list[str]):
        from .setup_wizard import SetupWizard
        wiz = SetupWizard(self, self.settings, preselect=keys)
        wiz.changed.connect(self.changed.emit)
        wiz.exec()
        self.registry.xtts.license_accepted = self.settings.get("xtts_license", False)
        self.reopen = True    # the main window reopens the manager to show the new state
        self.done(0)

    def _xtts_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        xtts = self.registry.xtts
        if not xtts.available():
            info = QLabel("XTTS pozwala czytać Twoim własnym głosem (klonowanie z krótkiego nagrania) "
                          "i daje dodatkowych, bardzo naturalnych lektorów.<br><br>"
                          f"Potrzebne jest osobne środowisko z PyTorch (~{xtts_runtime.APPROX_SIZE_GB:.1f} GB). "
                          "Instalator zrobi wszystko sam — nic nie trzeba wpisywać.")
            info.setWordWrap(True)
            lay.addWidget(info)
            gpu = xtts_runtime.nvidia_gpu()
            lay.addWidget(QLabel(f"Karta graficzna: {gpu or 'brak NVIDIA — XTTS będzie bardzo wolny'}"))
            btn = QPushButton("Zainstaluj XTTS…", clicked=lambda: self._open_installer(["xtts"]))
            btn.setMinimumHeight(36)
            lay.addWidget(btn)
            lay.addStretch()
            return w
        gpu = xtts_runtime.nvidia_gpu()
        lay.addWidget(QLabel(f"Karta graficzna: {gpu or 'brak CUDA — XTTS będzie bardzo wolny na procesorze'}"))

        lic = QLabel(f'Model XTTS v2 jest na licencji <a href="{LICENSE_URL}">Coqui Public Model License</a> '
                     "— tylko do użytku niekomercyjnego. Pierwsze załadowanie pobierze ~1,9 GB.")
        lic.setOpenExternalLinks(True)
        lic.setWordWrap(True)
        self.accept_lic = QCheckBox("Przeczytałem/am i akceptuję licencję CPML")
        self.accept_lic.setChecked(self.settings.get("xtts_license", False))
        self.accept_lic.toggled.connect(self._license_toggled)
        self.load_btn = QPushButton("Załaduj model XTTS", clicked=self._xtts_load)
        self.load_btn.setEnabled(self.accept_lic.isChecked() and not xtts.loaded)
        if xtts.loaded:
            self.load_btn.setText("Model załadowany ✓")
        self.xtts_status = QLabel()
        lay.addWidget(lic)
        lay.addWidget(self.accept_lic)
        row = QHBoxLayout()
        row.addWidget(self.load_btn)
        row.addWidget(self.xtts_status, 1)
        lay.addLayout(row)

        lay.addWidget(QLabel("<b>Twoje głosy (klonowane z próbki)</b> — nagranie 6–30 s, czysta mowa jednej osoby, "
                             "bez muzyki i pogłosu, WAV. Kilka próbek poprawia podobieństwo."))
        self.clones = QListWidget()
        self.clones.addItems(XTTSEngine.cloned_voices())
        add = QPushButton("Dodaj głos z próbek WAV…", clicked=self._clone_add)
        rem = QPushButton("Usuń", clicked=self._clone_remove)
        r2 = QHBoxLayout()
        r2.addWidget(add)
        r2.addWidget(rem)
        r2.addStretch()
        r2.addWidget(QPushButton("Odinstaluj XTTS", clicked=self._xtts_uninstall))
        lay.addWidget(self.clones)
        lay.addLayout(r2)
        return w

    def _license_toggled(self, on: bool):
        self.settings["xtts_license"] = on
        self.registry.xtts.license_accepted = on
        self.load_btn.setEnabled(on and not self.registry.xtts.loaded)

    def _xtts_load(self):
        self.load_btn.setEnabled(False)
        self.xtts_status.setText("Ładowanie (pierwszy raz: pobieranie ~1,9 GB, kilka minut)…")

        def done(_):
            self.load_btn.setText("Model załadowany ✓")
            self.xtts_status.setText(f"Gotowe. Wbudowanych głosów: {len(self.registry.xtts.voices()) - self.clones.count()}")
            self.changed.emit()

        def failed(m):
            self.load_btn.setEnabled(True)
            self.xtts_status.setText("Błąd: " + m)
        run_async(lambda r: self.registry.xtts.load(), on_done=done, on_failed=failed)

    def _xtts_uninstall(self):
        if QMessageBox.question(self, "Odinstaluj XTTS",
                                f"Usunąć dodatek XTTS (~{xtts_runtime.APPROX_SIZE_GB:.1f} GB)? Twoje próbki głosów zostaną.") == QMessageBox.Yes:
            self.registry.xtts.close()
            xtts_runtime.uninstall()
            self.changed.emit()
            self.done(0)

    def _clone_add(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Próbki głosu", "", "Nagrania WAV (*.wav)")
        if not files:
            return
        name, ok = QInputDialog.getText(self, "Nazwa głosu", "Jak nazwać ten głos?")
        if ok and name.strip():
            XTTSEngine.add_cloned_voice(name.strip(), files)
            self.clones.clear()
            self.clones.addItems(XTTSEngine.cloned_voices())
            self.changed.emit()

    def _clone_remove(self):
        it = self.clones.currentItem()
        if it and QMessageBox.question(self, "Usuń głos", f"Usunąć głos „{it.text()}”?") == QMessageBox.Yes:
            XTTSEngine.remove_cloned_voice(it.text())
            self.clones.takeItem(self.clones.row(it))
            self.changed.emit()

    # ------------------------------------------------------------------ Ollama
    def _ollama_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.ollama_status = QLabel("Sprawdzam Ollamę…")
        self.ollama_install = QPushButton("Zainstaluj Ollamę + model…", clicked=lambda: self._open_installer(["ollama", "gemma"]))
        self.ollama_start = QPushButton("Uruchom Ollamę", clicked=self._ollama_start)
        recheck = QPushButton("Sprawdź ponownie", clicked=self._ollama_refresh)
        row = QHBoxLayout()
        row.addWidget(self.ollama_status, 1)
        row.addWidget(self.ollama_install)
        row.addWidget(self.ollama_start)
        row.addWidget(recheck)
        lay.addLayout(row)
        lay.addWidget(QLabel("<b>Pobrane modele</b>"))
        self.ollama_models = QListWidget()
        self.ollama_models.setMaximumHeight(130)
        lay.addWidget(self.ollama_models)
        lay.addWidget(QLabel("<b>Polecane do tłumaczenia</b> (na RTX z 16 GB działa każdy z nich)"))
        self.suggest = QTableWidget(len(SUGGESTED_MODELS), 2)
        self.suggest.horizontalHeader().hide()
        self.suggest.verticalHeader().hide()
        self.suggest.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for r, (name, desc) in enumerate(SUGGESTED_MODELS):
            self.suggest.setItem(r, 0, QTableWidgetItem(f"{name} — {desc}"))
            b = QPushButton("Pobierz")
            b.clicked.connect(lambda _=False, n=name, b=b: self._ollama_pull(n, b))
            self.suggest.setCellWidget(r, 1, b)
        lay.addWidget(self.suggest)
        self.custom = QLineEdit()
        self.custom.setPlaceholderText("inna nazwa modelu z ollama.com/library, np. llama3.1:8b")
        cbtn = QPushButton("Pobierz")
        cbtn.clicked.connect(lambda: self.custom.text().strip() and self._ollama_pull(self.custom.text().strip(), cbtn))
        r3 = QHBoxLayout()
        r3.addWidget(self.custom)
        r3.addWidget(cbtn)
        lay.addLayout(r3)
        self.ollama_bar = self._bar()
        lay.addWidget(self.ollama_bar)
        self._ollama_refresh()
        return w

    def _ollama_refresh(self):
        tr = OllamaTranslator()

        def work(r):
            if not tr.running():
                return None
            return tr.models()

        def done(models):
            self.ollama_models.clear()
            installed = ollama_setup.find_ollama() is not None
            self.ollama_install.setVisible(not installed)
            self.ollama_start.setVisible(installed and models is None)
            if models is None:
                self.ollama_status.setText("❌ Ollama jest zainstalowana, ale nie działa." if installed
                                           else "❌ Ollama nie jest zainstalowana.")
                return
            self.ollama_status.setText(f"✅ Ollama działa · modeli: {len(models)}")
            self.ollama_models.addItems(models)
        run_async(work, on_done=done, on_failed=lambda m: self.ollama_status.setText("Błąd: " + m))

    def _ollama_start(self):
        self.ollama_status.setText("Uruchamianie Ollamy…")
        run_async(lambda r: ollama_setup.ensure_running(), on_done=lambda ok: self._ollama_refresh())

    def _ollama_pull(self, name: str, button: QPushButton):
        button.setEnabled(False)
        self.ollama_bar.show()

        def prog(v):
            status, done, total = v
            self.ollama_bar.setMaximum(max(1, total))
            self.ollama_bar.setValue(done)
            self.ollama_bar.setFormat(f"{name}: {status} {human_mb(done)}/{human_mb(total)}" if total else f"{name}: {status}")

        def finished(_):
            self.ollama_bar.hide()
            button.setEnabled(True)
            self._ollama_refresh()
            self.changed.emit()

        def failed(m):
            self.ollama_bar.hide()
            button.setEnabled(True)
            QMessageBox.warning(self, "Pobieranie modelu", m)
        run_async(lambda r: OllamaTranslator().pull(name, lambda s, d, t: r((s, d, t))),
                  on_done=finished, on_failed=failed, on_progress=prog)

    # ------------------------------------------------------------------ OCR
    def _ocr_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("OCR odczytuje skanowane PDF-y (strony bez warstwy tekstowej). Używa silnika Tesseract "
                             "wbudowanego w PyMuPDF — potrzebne są tylko dane językowe (github.com/tesseract-ocr/tessdata_best)."))
        self.ocr_rows = QVBoxLayout()
        lay.addLayout(self.ocr_rows)
        self.ocr_bar = self._bar()
        lay.addWidget(self.ocr_bar)
        lay.addWidget(QLabel("Wynik OCR jest zapamiętywany — każda książka jest rozpoznawana tylko raz. "
                             "Po pobraniu danych otwórz skanowaną książkę ponownie."))
        lay.addStretch()
        self._fill_ocr()
        return w

    def _fill_ocr(self):
        while self.ocr_rows.count():
            item = self.ocr_rows.takeAt(0)
            if item.layout():
                while item.layout().count():
                    wid = item.layout().takeAt(0).widget()
                    if wid:
                        wid.deleteLater()
        have = ocr.installed_langs()
        for code, (label, mb) in ocr.LANGS.items():
            row = QHBoxLayout()
            row.addWidget(QLabel(f"{label} ({code}) — ~{mb} MB"))
            row.addStretch()
            if code in have:
                row.addWidget(QLabel("zainstalowany ✓"))
            else:
                b = QPushButton("Pobierz")
                dest = os.path.join(ocr.TESSDATA_DIR, f"{code}.traineddata")
                b.clicked.connect(lambda _=False, c=code, d=dest, b=b: self._download_files(
                    [(ocr.TESSDATA_URL.format(lang=c), d)], self.ocr_bar, b, self._fill_ocr))
                row.addWidget(b)
            self.ocr_rows.addLayout(row)
