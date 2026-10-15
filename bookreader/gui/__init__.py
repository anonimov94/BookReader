"""Window application.  python -m bookreader.gui [KSIĄŻKA]"""
from __future__ import annotations

import os
import sys


def main() -> int:
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from .main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("BookReader")
    base = getattr(sys, "_MEIPASS", os.path.join(os.path.dirname(__file__), "..", "..", "packaging"))
    icon = os.path.join(base, "BookReader.png")
    if os.path.exists(icon):
        app.setWindowIcon(QIcon(icon))
    win = MainWindow()
    if len(sys.argv) > 1:
        win._pending_book = os.path.abspath(sys.argv[1])
    win.show()
    return app.exec()