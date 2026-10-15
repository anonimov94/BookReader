"""Dialogs: bookmarks, glossary, pronunciation, translation settings, model comparison, export."""
from __future__ import annotations

import os
import threading
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
                               QRadioButton, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget, QTextEdit,
                               QVBoxLayout, QWidget)

from ..text.pronunciation import PronunciationDict, Rule
from ..translate.base import TranslationContext
from ..translate.deepl import DeepLTranslator
from ..translate.glossary import GENDERS, Entry, Glossary
from ..translate.ollama import OllamaTranslator
from ..translate.service import DEFAULT_LOOKAHEAD, LOOKAHEAD_CHOICES
from .common import run_async


# ====================================================================== bookmarks
class BookmarksDialog(QDialog):
    """List of bookmarks; returns the chosen one via `chosen`."""

    def __init__(self, parent, bookmarks: list[dict], chapter_titles: list[str]):
        super().__init__(parent)
        self.setWindowTitle("Zakładki")
        self.resize(560, 420)
        self.bookmarks = bookmarks
        self.chosen: dict | None = None
        self.list = QListWidget()
        for b in bookmarks:
            ch = b["position"][0]
            title = chapter_titles[ch] if ch < len(chapter_titles) else ""
            item = QListWidgetItem(f"{b['label']}\n    {title} · {b.get('preview', '')[:70]}")
            self.list.addItem(item)
        self.list.itemDoubleClicked.connect(self._go)
        go = QPushButton("Przejdź", clicked=self._go)
        rename = QPushButton("Zmień nazwę", clicked=self._rename)
        delete = QPushButton("Usuń", clicked=self._delete)
        buttons = QHBoxLayout()
        for b in (go, rename, delete):
            buttons.addWidget(b)
        buttons.addStretch()
        buttons.addWidget(QPushButton("Zamknij", clicked=self.reject))
        lay = QVBoxLayout(self)
        lay.addWidget(self.list)
        lay.addLayout(buttons)

    def _go(self, *_):
        row = self.list.currentRow()
        if row >= 0:
            self.chosen = self.bookmarks[row]
            self.accept()

    def _rename(self):
        row = self.list.currentRow()
        if row < 0:
            return
        from PySide6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(self, "Nazwa zakładki", "Nazwa:", text=self.bookmarks[row]["label"])
        if ok and text.strip():
            self.bookmarks[row]["label"] = text.strip()
            self.list.item(row).setText(text.strip() + "\n" + self.list.item(row).text().split("\n", 1)[1])

    def _delete(self):
        row = self.list.currentRow()
        if row >= 0:
            self.bookmarks.pop(row)
            self.list.takeItem(row)


# ====================================================================== glossary
class GlossaryDialog(QDialog):
    KINDS = ["postać", "miejsce", "termin", "inne"]

    def __init__(self, parent, glossary: Glossary, rebuild=None):
        """`rebuild()` -> Glossary (runs in a thread) re-analyses the book with the LLM."""
        super().__init__(parent)
        self.setWindowTitle("Słownik postaci, nazw i stylu tłumaczenia")
        self.resize(820, 600)
        self.glossary = glossary
        self._rebuild = rebuild

        self.person = QComboBox()
        self.person.addItem("trzecioosobowa", "3")
        self.person.addItem("pierwszoosobowa („ja”)", "1")
        self.gender = QComboBox()
        for code, label in GENDERS.items():
            self.gender.addItem(label, code)
        self.narrator_name = QLineEdit()
        self.genre = QLineEdit()
        self.genre.setPlaceholderText("np. fantasy, kryminał, literatura faktu, romans…")
        self.address = QComboBox()
        self.address.addItem("automatycznie", "auto")
        self.address.addItem("na „ty”", "ty")
        self.address.addItem("formalnie („pan/pani”)", "pan")
        self.notes = QLineEdit()
        self.notes.setPlaceholderText("Dodatkowe wskazówki dla tłumacza, np. „zachowaj archaiczny styl”")

        form = QFormLayout()
        form.addRow("Narracja", self.person)
        form.addRow("Płeć narratora", self.gender)
        form.addRow("Imię narratora", self.narrator_name)
        form.addRow("Gatunek", self.genre)
        form.addRow("Zwracanie się", self.address)
        form.addRow("Uwagi o stylu", self.notes)
        top = QGroupBox("Narrator i styl")
        top.setLayout(form)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Oryginał", "Po polsku (puste = bez zmian)", "Rodzaj", "Płeć", "Uwagi"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        add = QPushButton("Dodaj", clicked=lambda: self._add_row(Entry("")))
        remove = QPushButton("Usuń zaznaczone", clicked=self._remove_rows)
        self.analyse = QPushButton("Analizuj książkę ponownie (LLM)", clicked=self._run_rebuild)
        self.analyse.setEnabled(rebuild is not None)
        row = QHBoxLayout()
        for b in (add, remove, self.analyse):
            row.addWidget(b)
        row.addStretch()
        self.info = QLabel("Zmiany obowiązują dla akapitów tłumaczonych od teraz. "
                           "Aby zastosować je wstecz: Tłumaczenie → Przetłumacz rozdział ponownie.")
        self.info.setWordWrap(True)

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(top)
        lay.addWidget(QLabel("Postacie, miejsca i terminy:"))
        lay.addWidget(self.table)
        lay.addLayout(row)
        lay.addWidget(self.info)
        lay.addWidget(bb)
        self._load(glossary)

    def _load(self, g: Glossary):
        self.person.setCurrentIndex(max(0, self.person.findData(g.narrator_person)))
        self.gender.setCurrentIndex(max(0, self.gender.findData(g.narrator_gender)))
        self.narrator_name.setText(g.narrator_name)
        self.genre.setText(g.genre)
        self.address.setCurrentIndex(max(0, self.address.findData(g.address)))
        self.notes.setText(g.style_notes)
        self.table.setRowCount(0)
        for e in g.entries:
            self._add_row(e)

    def _add_row(self, e: Entry):
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(e.source))
        self.table.setItem(r, 1, QTableWidgetItem(e.target))
        kind = QComboBox()
        kind.addItems(self.KINDS)
        kind.setCurrentText(e.kind if e.kind in self.KINDS else "inne")
        self.table.setCellWidget(r, 2, kind)
        gender = QComboBox()
        for code, label in GENDERS.items():
            gender.addItem(label, code)
        gender.setCurrentIndex(max(0, gender.findData(e.gender)))
        self.table.setCellWidget(r, 3, gender)
        self.table.setItem(r, 4, QTableWidgetItem(e.note))

    def _remove_rows(self):
        for r in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(r)

    def _collect(self) -> Glossary:
        g = Glossary(narrator_person=self.person.currentData(), narrator_gender=self.gender.currentData(),
                     narrator_name=self.narrator_name.text().strip(), genre=self.genre.text().strip(),
                     address=self.address.currentData(), style_notes=self.notes.text().strip())
        for r in range(self.table.rowCount()):
            src = (self.table.item(r, 0).text() if self.table.item(r, 0) else "").strip()
            if not src:
                continue
            g.entries.append(Entry(src, (self.table.item(r, 1).text() if self.table.item(r, 1) else "").strip(),
                                   self.table.cellWidget(r, 2).currentText(), self.table.cellWidget(r, 3).currentData(),
                                   (self.table.item(r, 4).text() if self.table.item(r, 4) else "").strip()))
        return g

    def _run_rebuild(self):
        self.analyse.setEnabled(False)
        self.analyse.setText("Analizuję… (może potrwać minutę)")

        def done(g):
            self._load(g)
            self.analyse.setText("Analizuj książkę ponownie (LLM)")
            self.analyse.setEnabled(True)

        def failed(msg):
            QMessageBox.warning(self, "Analiza nieudana", msg)
            done(self._collect())
        run_async(lambda report: self._rebuild(self._collect()), on_done=done, on_failed=failed)

    def _save(self):
        self.glossary = self._collect()
        self.accept()


# ====================================================================== pronunciation
class _RulesTable(QWidget):
    def __init__(self, rules: list[Rule]):
        super().__init__()
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Pisownia", "Wymowa", "Całe słowo", "Wielkość liter", "Wyrażenie regularne"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        add = QPushButton("Dodaj", clicked=lambda: self.add(Rule("", "")))
        rem = QPushButton("Usuń zaznaczone", clicked=self.remove)
        row = QHBoxLayout()
        row.addWidget(add)
        row.addWidget(rem)
        row.addStretch()
        lay = QVBoxLayout(self)
        lay.addWidget(self.table)
        lay.addLayout(row)
        for r in rules:
            self.add(r)

    def add(self, rule: Rule):
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(rule.find))
        self.table.setItem(r, 1, QTableWidgetItem(rule.say))
        for col, val in ((2, rule.whole_word), (3, rule.case_sensitive), (4, rule.regex)):
            it = QTableWidgetItem()
            it.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            it.setCheckState(Qt.Checked if val else Qt.Unchecked)
            self.table.setItem(r, col, it)

    def remove(self):
        for r in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(r)

    def rules(self) -> list[Rule]:
        out = []
        for r in range(self.table.rowCount()):
            find = (self.table.item(r, 0).text() if self.table.item(r, 0) else "").strip()
            if find:
                chk = lambda c: self.table.item(r, c).checkState() == Qt.Checked
                out.append(Rule(find, self.table.item(r, 1).text() if self.table.item(r, 1) else "",
                                chk(2), chk(3), chk(4)))
        return out


class PronunciationDialog(QDialog):
    def __init__(self, parent, global_rules: list[Rule], book_rules: list[Rule] | None, speak=None):
        """`speak(text)` plays a sample with the current voice."""
        super().__init__(parent)
        self.setWindowTitle("Słownik wymowy")
        self.resize(780, 560)
        self.global_tab = _RulesTable(global_rules)
        self.book_tab = _RulesTable(book_rules or [])
        tabs = QTabWidget()
        tabs.addTab(self.global_tab, "Wszystkie książki")
        tabs.addTab(self.book_tab, "Ta książka")
        tabs.setTabEnabled(1, book_rules is not None)

        self.sample = QLineEdit()
        self.sample.setPlaceholderText("Wpisz zdanie, aby sprawdzić reguły…")
        self.result = QLabel()
        self.result.setWordWrap(True)
        self.sample.textChanged.connect(self._preview)
        listen = QPushButton("Odsłuchaj", clicked=lambda: speak and speak(self._apply(self.sample.text())))
        listen.setEnabled(speak is not None)
        test = QHBoxLayout()
        test.addWidget(self.sample)
        test.addWidget(listen)

        hint = QLabel("Przykłady: „Hermione” → „Hermajoni”, „SQL” → „es kju el”, „Nguyen” → „Nguen”. "
                      "Reguły książki działają przed globalnymi. W trybie wyrażeń regularnych można użyć \\1.")
        hint.setWordWrap(True)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(tabs)
        lay.addWidget(hint)
        lay.addLayout(test)
        lay.addWidget(self.result)
        lay.addWidget(bb)

    def _apply(self, text: str) -> str:
        return PronunciationDict(self.book_tab.rules() + self.global_tab.rules()).apply(text)

    def _preview(self, text: str):
        self.result.setText(f"Lektor przeczyta: <b>{self._apply(text)}</b>" if text else "")


# ====================================================================== translation settings
class TranslationSettingsDialog(QDialog):
    def __init__(self, parent, settings: dict):
        super().__init__(parent)
        self.setWindowTitle("Ustawienia tłumaczenia")
        self.resize(560, 360)
        self.settings = settings
        self.use_ollama = QRadioButton("Lokalnie przez Ollama (offline, bez limitów)")
        self.use_deepl = QRadioButton("DeepL API (online, limit znaków)")
        (self.use_deepl if settings.get("translator") == "deepl" else self.use_ollama).setChecked(True)

        self.model = QComboBox(editable=True)
        self.model.setMinimumWidth(320)
        self.model.addItem(settings.get("ollama_model", "gemma3:12b"))
        refresh = QPushButton("Odśwież listę", clicked=self._load_models)
        mrow = QHBoxLayout()
        mrow.addWidget(self.model)
        mrow.addWidget(refresh)

        self.key = QLineEdit(settings.get("deepl_key", ""))
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText("klucz z deepl.com/your-account/keys (Free kończy się na :fx)")
        self.formality = QComboBox()
        self.formality.addItem("swobodnie (ty)", "prefer_less")
        self.formality.addItem("formalnie (pan/pani)", "prefer_more")
        self.formality.setCurrentIndex(max(0, self.formality.findData(settings.get("deepl_formality", "prefer_less"))))
        self.usage = QLabel()
        check = QPushButton("Sprawdź klucz i limit", clicked=self._check_deepl)
        krow = QHBoxLayout()
        krow.addWidget(self.key)
        krow.addWidget(check)

        self.lookahead = QComboBox()
        for chars, label in LOOKAHEAD_CHOICES:
            self.lookahead.addItem(label, chars)
        self.lookahead.setCurrentIndex(max(0, self.lookahead.findData(settings.get("lookahead_chars", DEFAULT_LOOKAHEAD))))
        self.lookahead.setToolTip("Jak daleko przed lektorem tłumaczyć. Mniej = mniej pracy karty graficznej "
                                  "i szybsza reakcja po przeskoku; więcej = zapas przy wolnym modelu.")

        form = QFormLayout()
        form.addRow("Tłumacz z wyprzedzeniem", self.lookahead)
        form.addRow(self.use_ollama)
        form.addRow("Model Ollama", mrow)
        form.addRow(self.use_deepl)
        form.addRow("Klucz DeepL", krow)
        form.addRow("Forma", self.formality)
        form.addRow("", self.usage)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addStretch()
        lay.addWidget(bb)
        self._load_models()

    def _load_models(self):
        current = self.model.currentText()

        def fill(names):
            self.model.clear()
            self.model.addItems(names or [current])
            self.model.setCurrentText(current if current in names or not names else names[0])
        run_async(lambda r: OllamaTranslator().models(), on_done=fill,
                  on_failed=lambda m: self.model.setToolTip("Ollama nie działa: " + m))

    def _check_deepl(self):
        tr = DeepLTranslator(self.key.text())
        self.usage.setText("Sprawdzam…")
        run_async(lambda r: tr.usage(),
                  on_done=lambda u: self.usage.setText(f"OK · wykorzystano {u[0]:,} z {u[1]:,} znaków w tym miesiącu".replace(",", " ")),
                  on_failed=lambda m: self.usage.setText("Błąd: " + m))

    def _save(self):
        self.settings["translator"] = "deepl" if self.use_deepl.isChecked() else "ollama"
        self.settings["ollama_model"] = self.model.currentText().strip()
        self.settings["deepl_key"] = self.key.text().strip()
        self.settings["deepl_formality"] = self.formality.currentData()
        self.settings["lookahead_chars"] = self.lookahead.currentData()
        self.accept()


# ====================================================================== compare
class CompareDialog(QDialog):
    """Translate the same passage with several models side by side."""

    def __init__(self, parent, text: str, glossary: Glossary, settings: dict, title: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Porównaj tłumaczy")
        self.resize(1200, 700)
        self.glossary, self.settings, self.title = glossary, settings, title
        self.chosen_model: str | None = None
        self.source = QPlainTextEdit(text)
        self.source.setMaximumHeight(150)
        self.models = QListWidget()
        self.models.setMaximumHeight(120)
        self.run_btn = QPushButton("Porównaj", clicked=self._run)
        self.results = QGridLayout()
        top = QHBoxLayout()
        top.addWidget(self.models, 1)
        top.addWidget(self.run_btn)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Fragment do przetłumaczenia (możesz go zmienić):"))
        lay.addWidget(self.source)
        lay.addWidget(QLabel("Tłumacze (zaznacz 2–4):"))
        lay.addLayout(top)
        holder = QWidget()
        holder.setLayout(self.results)
        lay.addWidget(holder, 1)
        lay.addWidget(QDialogButtonBox(QDialogButtonBox.Close, rejected=self.reject))
        run_async(lambda r: OllamaTranslator().models(), on_done=self._fill, on_failed=lambda m: self._fill([]))

    def _fill(self, names):
        for n in names:
            it = QListWidgetItem(f"Ollama · {n}")
            it.setData(Qt.UserRole, ("ollama", n))
            it.setCheckState(Qt.Checked if len([i for i in range(self.models.count())
                                                if self.models.item(i).checkState() == Qt.Checked]) < 2 else Qt.Unchecked)
            self.models.addItem(it)
        if self.settings.get("deepl_key"):
            it = QListWidgetItem("DeepL")
            it.setData(Qt.UserRole, ("deepl", ""))
            it.setCheckState(Qt.Unchecked)
            self.models.addItem(it)
        if not self.models.count():
            self.models.addItem("Brak tłumaczy — uruchom Ollamę i pobierz model albo dodaj klucz DeepL.")

    def _run(self):
        while self.results.count():
            w = self.results.takeAt(0).widget()
            if w:
                w.deleteLater()
        paras = [p.strip() for p in self.source.toPlainText().split("\n") if p.strip()]
        picked = [self.models.item(i).data(Qt.UserRole) for i in range(self.models.count())
                  if self.models.item(i).data(Qt.UserRole) and self.models.item(i).checkState() == Qt.Checked]
        for col, (kind, model) in enumerate(picked):
            head = QLabel(f"<b>{model if kind == 'ollama' else 'DeepL'}</b> — tłumaczę…")
            body = QTextEdit(readOnly=True)
            use = QPushButton("Używaj tego tłumacza")
            use.clicked.connect(lambda _=False, k=kind, m=model: self._choose(k, m))
            self.results.addWidget(head, 0, col)
            self.results.addWidget(body, 1, col)
            self.results.addWidget(use, 2, col)
            tr = OllamaTranslator(model) if kind == "ollama" else DeepLTranslator(self.settings.get("deepl_key", ""))

            def work(report, tr=tr):
                t0 = time.time()
                out = tr.translate(paras, TranslationContext(self.glossary, [], self.title))
                return out, time.time() - t0

            run_async(work, on_done=lambda res, h=head, b=body, n=tr.name: (
                b.setPlainText("\n\n".join(res[0])), h.setText(f"<b>{n}</b> — {res[1]:.1f} s")),
                on_failed=lambda m, h=head: h.setText(f"Błąd: {m}"))

    def _choose(self, kind: str, model: str):
        self.settings["translator"] = kind
        if kind == "ollama":
            self.settings["ollama_model"] = model
        self.chosen_model = model or kind
        self.accept()


# ====================================================================== edit translation
class EditTranslationDialog(QDialog):
    def __init__(self, parent, source: str, translation: str):
        super().__init__(parent)
        self.setWindowTitle("Popraw tłumaczenie akapitu")
        self.resize(760, 480)
        src = QPlainTextEdit(source, readOnly=True)
        self.edit = QPlainTextEdit(translation)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Oryginał:"))
        lay.addWidget(src)
        lay.addWidget(QLabel("Tłumaczenie (Twoja wersja zostanie zapamiętana na stałe):"))
        lay.addWidget(self.edit)
        lay.addWidget(bb)

    @property
    def text(self) -> str:
        return " ".join(self.edit.toPlainText().split())


# ====================================================================== export
class ExportDialog(QDialog):
    def __init__(self, parent, chapter_titles: list[str], default_dir: str, current_chapter: int = 0):
        super().__init__(parent)
        self.setWindowTitle("Eksport audiobooka")
        self.resize(560, 560)
        self.mp3 = QRadioButton("MP3 — osobny plik na każdy rozdział")
        self.m4b = QRadioButton("M4B — jeden plik audiobooka z rozdziałami i okładką")
        self.m4b.setChecked(True)
        self.quality = QSpinBox(minimum=32, maximum=192, singleStep=16, value=64, suffix=" kbit/s")
        self.folder = QLineEdit(default_dir)
        browse = QPushButton("…", clicked=self._browse)
        frow = QHBoxLayout()
        frow.addWidget(self.folder)
        frow.addWidget(browse)
        self.chapters = QListWidget()
        for t in chapter_titles:
            it = QListWidgetItem(t)
            it.setCheckState(Qt.Checked)
            self.chapters.addItem(it)
        sel_all = QPushButton("Wszystkie", clicked=lambda: self._check(lambda i: True))
        sel_none = QPushButton("Żaden", clicked=lambda: self._check(lambda i: False))
        sel_cur = QPushButton("Tylko bieżący", clicked=lambda: self._check(lambda i: i == current_chapter))
        crow = QHBoxLayout()
        for b in (sel_all, sel_none, sel_cur):
            crow.addWidget(b)
        crow.addStretch()

        form = QFormLayout()
        form.addRow(self.m4b)
        form.addRow(self.mp3)
        form.addRow("Jakość (M4B)", self.quality)
        form.addRow("Folder", frow)
        note = QLabel("Używane są bieżący lektor, suwaki i słownik wymowy. Zdania już wygenerowane "
                      "podczas czytania biorą się z pamięci podręcznej. Przy tłumaczeniu program najpierw "
                      "przetłumaczy wybrane rozdziały.")
        note.setWordWrap(True)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Eksportuj")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(QLabel("Rozdziały:"))
        lay.addWidget(self.chapters)
        lay.addLayout(crow)
        lay.addWidget(note)
        lay.addWidget(bb)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Folder eksportu", self.folder.text())
        if d:
            self.folder.setText(d)

    def _check(self, pred):
        for i in range(self.chapters.count()):
            self.chapters.item(i).setCheckState(Qt.Checked if pred(i) else Qt.Unchecked)

    @property
    def selected(self) -> list[int]:
        return [i for i in range(self.chapters.count()) if self.chapters.item(i).checkState() == Qt.Checked]

    @property
    def fmt(self) -> str:
        return "mp3" if self.mp3.isChecked() else "m4b"


class ProgressDialog(QDialog):
    def __init__(self, parent, title: str):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(480)
        self.cancel = threading.Event()
        self.label = QLabel("Przygotowuję…")
        self.bar = QProgressBar()
        self.btn = QPushButton("Anuluj", clicked=self._cancel)
        lay = QVBoxLayout(self)
        lay.addWidget(self.label)
        lay.addWidget(self.bar)
        lay.addWidget(self.btn, alignment=Qt.AlignRight)

    def update_progress(self, value):
        done, total, msg = value
        self.bar.setMaximum(max(1, total))
        self.bar.setValue(done)
        self.label.setText(msg)

    def _cancel(self):
        self.cancel.set()
        self.btn.setEnabled(False)
        self.label.setText("Anulowanie…")

    def finish(self, text: str, paths: list[str] | None = None):
        self.label.setText(text)
        self.bar.setValue(self.bar.maximum())
        self.btn.setText("Zamknij")
        self.btn.setEnabled(True)
        self.btn.clicked.disconnect()
        self.btn.clicked.connect(self.accept)
        if paths:
            folder = os.path.dirname(paths[0])
            open_btn = QPushButton("Otwórz folder", clicked=lambda: os.startfile(folder))
            self.layout().addWidget(open_btn, alignment=Qt.AlignRight)
