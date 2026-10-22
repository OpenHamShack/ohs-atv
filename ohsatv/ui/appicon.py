"""The application icon: the same SVG the package installs.

One file, packaging/openhamshack-atv.svg, is both the desktop icon and the window
icon (as OpenHamShack NB). Installed, it is under share/icons/hicolor next to the
lib directory the launcher puts on PYTHONPATH; from a source tree it is in
packaging/. If none is found, or this Qt has no SVG image plugin
(qt6-svg-plugins), a painted copy of the same drawing - a monitor showing
75 % bars under a Yagi - stands in rather than a blank square.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPen, QPixmap

_HERE = Path(__file__).resolve()


def svg_path() -> Path | None:
    pkg = _HERE.parents[1]                 # .../ohsatv
    cands = [pkg.parent / "packaging" / "openhamshack-atv.svg",
             pkg.parents[2] / "share" / "icons" / "hicolor" / "scalable" / "apps" / "openhamshack-atv.svg",
             Path("/usr/share/icons/hicolor/scalable/apps/openhamshack-atv.svg")]
    return next((c for c in cands if c.is_file()), None)


def paint_mark(p: QPainter, r: QRectF):
    """The icon drawn into `r` (square), on a 64-unit grid as the SVG is."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.translate(r.x(), r.y())
    p.scale(r.width() / 64, r.height() / 64)
    g = QLinearGradient(0, 0, 0, 64)
    g.setColorAt(0, QColor("#16213a"))
    g.setColorAt(1, QColor("#070a12"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(g)
    p.drawRoundedRect(QRectF(2, 2, 60, 60), 13, 13)
    pen = QPen(QColor("#4fd1e8"), 2.2)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    for x1, y1, x2, y2 in ((14, 13, 50, 13), (18, 8, 18, 18), (26, 9, 26, 17), (34, 9.5, 34, 16.5),
                           (42, 10, 42, 16), (32, 13, 32, 21)):
        p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
    p.setPen(Qt.PenStyle.NoPen)
    b = QLinearGradient(0, 21, 0, 57)
    b.setColorAt(0, QColor("#3a4558"))
    b.setColorAt(1, QColor("#1b212c"))
    p.setBrush(b)
    p.drawRoundedRect(QRectF(8, 21, 48, 36), 6, 6)
    p.setBrush(QColor("#05070b"))
    p.drawRoundedRect(QRectF(12, 25, 40, 28), 3, 3)
    for i, col in enumerate(("#c0c0c0", "#c0c000", "#00c0c0", "#00c000", "#c000c0", "#c00000", "#0000c0",
                             "#000000")):
        p.setBrush(QColor(col))
        p.drawRect(QRectF(12 + 5 * i, 25, 5, 20))
    p.setBrush(QColor("#0b0f15"))
    p.drawRect(QRectF(12, 45, 40, 8))
    p.setBrush(QColor("#e8edf4"))
    p.drawRoundedRect(QRectF(16, 47.5, 18, 3), 1, 1)
    p.setBrush(QColor("#ff4d5e"))
    p.drawEllipse(QPointF(44, 49), 2, 2)
    p.restore()


def _painted(size: int) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    paint_mark(p, QRectF(0, 0, size, size))
    p.end()
    return pm


def app_icon() -> QIcon:
    sp = svg_path()
    if sp is not None:
        ic = QIcon(str(sp))
        if not ic.isNull() and not ic.pixmap(32, 32).isNull():
            return ic
    ic = QIcon()
    for s in (16, 32, 48, 64, 128, 256):
        ic.addPixmap(_painted(s))
    return ic
