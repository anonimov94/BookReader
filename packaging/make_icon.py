"""Draws the app icon (an open book with a sound wave) into packaging/BookReader.ico."""
import os
import sys

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPen

app = QGuiApplication(sys.argv)
size = 256
img = QImage(size, size, QImage.Format_ARGB32)
img.fill(Qt.transparent)
p = QPainter(img)
p.setRenderHint(QPainter.Antialiasing)
p.setBrush(QColor("#2d6cdf"))
p.setPen(Qt.NoPen)
p.drawRoundedRect(QRectF(8, 8, 240, 240), 48, 48)

# open book
p.setBrush(QColor("#ffffff"))
for sign in (-1, 1):
    path = QPainterPath(QPointF(128, 92))
    path.cubicTo(128 + sign * 30, 76, 128 + sign * 70, 76, 128 + sign * 92, 86)
    path.lineTo(128 + sign * 92, 196)
    path.cubicTo(128 + sign * 70, 186, 128 + sign * 30, 186, 128, 202)
    path.closeSubpath()
    p.drawPath(path)

# sound wave above the book
pen = QPen(QColor("#ffd666"), 10, Qt.SolidLine, Qt.RoundCap)
p.setPen(pen)
for i, h in enumerate((14, 30, 46, 30, 14)):
    x = 88 + i * 20
    p.drawLine(QPointF(x, 52 - h / 2), QPointF(x, 52 + h / 2))
p.end()

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "BookReader.ico")
ok = img.save(out, "ICO")
img.save(out.replace(".ico", ".png"), "PNG")
print("ikona:", out, ok)
