"""'Instalator dodatków': one window, checkboxes, one button — installs everything the user picked.

Every component downloads only after the user presses "Zainstaluj zaznaczone".
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QDialog, QGridLayout, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit,
                               QProgressBar, QPushButton, QVBoxLayout, QWidget)

from .. import ocr
from ..addons import ollama_setup, xtts_runtime
from ..downloads import Cancelled, download
from ..tts import piper_engine
from ..tts.xtts_engine import LICENSE_URL
from .common import run_async


@dataclass
class Component:
    key: str
    title: str
    desc: str
    size_gb: float
    installed: Callable[[], bool]
    run: Callable                     # run(log, progress, cancel)
    requires: list[str] = field(default_factory=list)
    default: bool = True


def _piper_install(keys):
    def run(log, progress, cancel):
        files = []
        for k in keys:
            if not piper_engine.is_installed(k):
                e = next(c for c in piper_engine.CATALOG if c.key == k)
                onnx, js = e.urls()
                files += [(js, piper_engine.model_path(k) + ".json", e.label), (onnx, piper_engine.model_path(k), e.label)]
        for i, (url, dest, label) in enumerate(files):
            if dest.endswith(".onnx"):
                log(f"Pobieranie głosu {label}…")
            download(url, dest, lambda d, t, i=i: progress((i + (d / t if t else 0)) / len(files)), cancel)
    return run


def _ocr_install(log, progress, cancel):
    langs = [l for l in ocr.LANGS if l not in ocr.installed_langs()]
    for i, code in enumerate(langs):
        log(f"Pobieranie danych OCR: {ocr.LANGS[code][0]}…")
        download(ocr.TESSDATA_URL.format(lang=code), os.path.join(ocr.TESSDATA_DIR, f"{code}.traineddata"),
                 lambda d, t, i=i: progress((i + (d / t if t else 0)) / len(langs)), cancel)


def _model_installed(name: str) -> Callable[[], bool]:
    def check() -> bool:
        if not ollama_setup.is_running():
            return False
        try:
            from ..translate.ollama import OllamaTranslator
            names = OllamaTranslator().models()
            return name in names or f"{name}:latest" in names
        except Exception:
            return False
    return check


PIPER_PL = ["pl_PL-gosia-medium", "pl_PL-darkman-medium"]
GEMMA = "gemma3:12b"
BIELIK = "SpeakLeash/bielik-11b-v2.3-instruct:Q4_K_M"


def components() -> list[Component]:
    gpu = xtts_runtime.nvidia_gpu()
    return [
        Component("piper", "Polskie głosy offline (Piper: Gosia ♀, Darkman ♂)",
                  "Czytanie bez internetu, szybkie, na procesorze.", 0.13,
                  lambda: all(piper_engine.is_installed(k) for k in PIPER_PL), _piper_install(PIPER_PL)),
        Component("ocr", "Rozpoznawanie skanów — OCR (polski + angielski)",
                  "Pozwala czytać PDF-y, które są zeskanowanymi obrazami stron.", 0.03,
                  lambda: all(l in ocr.installed_langs() for l in ocr.LANGS), _ocr_install),
        Component("ollama", "Ollama — lokalny tłumacz (program)",
                  "Potrzebny do tłumaczenia angielskich książek na polski bez internetu i bez limitów.", 1.0,
                  lambda: ollama_setup.find_ollama() is not None, ollama_setup.install),
        Component("gemma", "Model tłumaczący Gemma 3 12B (zalecany)",
                  "Bardzo dobra jakość tłumaczenia literackiego. Wymaga karty z ~10 GB pamięci lub będzie wolniej.",
                  8.1, _model_installed(GEMMA),
                  lambda log, p, c: ollama_setup.pull_model(GEMMA, log, p, c), ["ollama"]),
        Component("bielik", "Model Bielik 11B (polski) — opcjonalnie, do porównania",
                  "Najbardziej naturalna polszczyzna; porównasz go z Gemmą w menu Tłumaczenie → Porównaj tłumaczy.",
                  6.7, _model_installed(BIELIK),
                  lambda log, p, c: ollama_setup.pull_model(BIELIK, log, p, c), ["ollama"], default=False),
        Component("xtts", "XTTS — klonowanie głosu i dodatkowi lektorzy",
                  ("Wykryto kartę: " + gpu if gpu else "Nie wykryto karty NVIDIA — XTTS będzie bardzo wolny.")
                  + " Instaluje osobne środowisko z PyTorch (nie zmienia nic w systemie).",
                  xtts_runtime.APPROX_SIZE_GB, xtts_runtime.is_installed, xtts_runtime.install, default=False),
    ]


class SetupWizard(QDialog):
    changed = Signal()

    def __init__(self, parent, settings: dict, preselect: list[str] | None = None, intro: bool = False,
                 autostart: bool = False, note: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Instalator dodatków")
        self.resize(820, 680)
        self.settings = settings
        self.comps = components()
        self.checks: dict[str, QCheckBox] = {}
        self.status: dict[str, QLabel] = {}
        self.worker = None

        self._autostart = autostart
        lay = QVBoxLayout(self)
        if note:
            info = QLabel(note)
            info.setWordWrap(True)
            lay.addWidget(info)
        if intro:
            hello = QLabel("<b>Witaj w BookReader!</b> Program już działa z głosami online (Edge). "
                           "Zaznacz, co jeszcze chcesz doinstalować — możesz to zrobić teraz albo później "
                           "z menu <i>Narzędzia → Instalator dodatków</i>.")
            hello.setWordWrap(True)
            lay.addWidget(hello)

        grid = QGridLayout()
        for r, c in enumerate(self.comps):
            cb = QCheckBox(c.title)
            cb.setChecked(c.key in preselect if preselect is not None else c.default)
            cb.toggled.connect(self._update_summary)
            desc = QLabel(c.desc)
            desc.setWordWrap(True)
            desc.setStyleSheet("color: gray; margin-left: 22px;")
            size = QLabel(f"{c.size_gb:.1f} GB" if c.size_gb >= 0.1 else f"{c.size_gb * 1000:.0f} MB")
            st = QLabel("sprawdzam…")
            grid.addWidget(cb, r * 2, 0)
            grid.addWidget(size, r * 2, 1, Qt.AlignRight)
            grid.addWidget(st, r * 2, 2)
            grid.addWidget(desc, r * 2 + 1, 0, 1, 3)
            self.checks[c.key], self.status[c.key] = cb, st
        grid.setColumnStretch(0, 1)
        box = QWidget()
        box.setLayout(grid)
        lay.addWidget(box)

        self.license = QCheckBox("Akceptuję licencję modelu XTTS (Coqui Public Model License — tylko użytek niekomercyjny)")
        self.license.setChecked(settings.get("xtts_license", False))
        lic_link = QLabel(f'<a href="{LICENSE_URL}">Przeczytaj licencję</a>')
        lic_link.setOpenExternalLinks(True)
        lrow = QHBoxLayout()
        lrow.addWidget(self.license)
        lrow.addWidget(lic_link)
        lrow.addStretch()
        lay.addLayout(lrow)
        self.checks["xtts"].toggled.connect(self._update_summary)
        self.license.toggled.connect(self._update_summary)

        self.summary = QLabel()
        lay.addWidget(self.summary)
        self.step_label = QLabel("")
        self.step_bar = QProgressBar()
        self.total_bar = QProgressBar()
        self.total_bar.setFormat("Całość: %p%")
        self.log = QPlainTextEdit(readOnly=True)
        self.log.setMaximumBlockCount(2000)
        lay.addWidget(self.step_label)
        lay.addWidget(self.step_bar)
        lay.addWidget(self.total_bar)
        lay.addWidget(self.log, 1)

        self.install_btn = QPushButton("Zainstaluj zaznaczone", clicked=self._install)
        self.install_btn.setDefault(True)
        self.cancel_btn = QPushButton("Anuluj instalację", clicked=self._cancel)
        self.cancel_btn.setEnabled(False)
        self.close_btn = QPushButton("Zamknij", clicked=self.reject)
        brow = QHBoxLayout()
        brow.addStretch()
        for b in (self.install_btn, self.cancel_btn, self.close_btn):
            brow.addWidget(b)
        lay.addLayout(brow)
        self._refresh_status()

    # ------------------------------------------------------------------ status
    def _refresh_status(self):
        def work(report):
            return {c.key: c.installed() for c in self.comps}

        def done(states):
            self.installed = states
            for key, ok in states.items():
                self.status[key].setText("✅ zainstalowane" if ok else "—")
                if ok:
                    self.checks[key].setChecked(False)
            self._update_summary()
            if self._autostart and self.install_btn.isEnabled():
                self._autostart = False
                self._install()
        self.installed = {}
        run_async(work, on_done=done)

    def _selected(self) -> list:
        return [c for c in self.comps if self.checks[c.key].isChecked() and not self.installed.get(c.key)]

    def _update_summary(self):
        # a model needs Ollama: tick it automatically
        for c in self.comps:
            if self.checks[c.key].isChecked():
                for req in c.requires:
                    if not self.installed.get(req):
                        self.checks[req].setChecked(True)
        sel = self._selected()
        need = sum(c.size_gb for c in sel)
        free = xtts_runtime.disk_free_gb()
        text = f"Do pobrania: ~{need:.1f} GB · wolne miejsce na dysku: {free:.0f} GB"
        ok = bool(sel) and need < free - 1
        if need >= free - 1:
            text += " — <span style='color:#d33'>za mało miejsca!</span>"
        if self.checks["xtts"].isChecked() and not self.license.isChecked():
            text += " — <span style='color:#d33'>zaakceptuj licencję XTTS</span>"
            ok = False
        self.summary.setText(text)
        self.install_btn.setEnabled(ok and self.worker is None)

    # ------------------------------------------------------------------ install
    def _install(self):
        sel = self._selected()
        if not sel:
            return
        self.settings["xtts_license"] = self.license.isChecked()
        for cb in self.checks.values():
            cb.setEnabled(False)
        self.install_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.close_btn.setEnabled(False)
        self.total_bar.setMaximum(len(sel) * 100)
        cancel = self._cancel_event = threading.Event()

        def work(report):
            failures = []
            for i, c in enumerate(sel):
                if cancel.is_set():
                    raise Cancelled()
                report(("step", i, c.title))
                try:
                    c.run(lambda m: report(("log", m)),
                          lambda f, i=i: report(("frac", i, f)),
                          cancel)
                    report(("ok", c.key))
                except Cancelled:
                    raise
                except Exception as e:
                    failures.append(f"{c.title}: {e}")
                    report(("fail", c.key, str(e)))
            return failures

        self.worker = run_async(work, on_progress=self._on_progress, on_done=self._on_done, on_failed=self._on_failed)

    def _on_progress(self, ev):
        kind = ev[0]
        if kind == "step":
            _, i, title = ev
            self.step_label.setText(f"<b>Krok {i + 1}: {title}</b>")
            self.total_bar.setValue(i * 100)
            self.log.appendPlainText(f"\n=== {title}")
        elif kind == "log":
            self.log.appendPlainText(ev[1])
        elif kind == "frac":
            _, i, f = ev
            if f is None:
                self.step_bar.setMaximum(0)          # busy indicator
            else:
                self.step_bar.setMaximum(1000)
                self.step_bar.setValue(int(f * 1000))
                self.total_bar.setValue(int(i * 100 + f * 100))
        elif kind == "ok":
            self.status[ev[1]].setText("✅ zainstalowane")
            self.changed.emit()
        elif kind == "fail":
            self.status[ev[1]].setText("❌ błąd")
            self.log.appendPlainText(f"BŁĄD: {ev[2]}")

    def _finish(self):
        self.worker = None
        self.step_bar.setMaximum(1000)
        self.cancel_btn.setEnabled(False)
        self.close_btn.setEnabled(True)
        for cb in self.checks.values():
            cb.setEnabled(True)
        self.changed.emit()
        self._refresh_status()

    def _on_done(self, failures):
        self._finish()
        self.total_bar.setValue(self.total_bar.maximum())
        if failures:
            self.step_label.setText("<b>Część elementów się nie zainstalowała</b> — szczegóły w dzienniku poniżej.")
            QMessageBox.warning(self, "Instalacja", "Nie udało się:\n\n" + "\n".join(failures))
        else:
            self.step_label.setText("<b>Gotowe ✅</b> Wszystko zainstalowane.")

    def _on_failed(self, msg):
        self._finish()
        self.step_label.setText("<b>Instalacja anulowana.</b>" if "Cancelled" in msg else f"<b>Błąd:</b> {msg}")

    def _cancel(self):
        if self.worker:
            self._cancel_event.set()
            self.cancel_btn.setEnabled(False)
            self.step_label.setText("Anulowanie… (bieżący plik zostanie przerwany)")

    def reject(self):
        if self.worker is not None:
            if QMessageBox.question(self, "Instalacja trwa", "Przerwać instalację i zamknąć?") != QMessageBox.Yes:
                return
            self._cancel_event.set()
        super().reject()
