"""The application icon: the same SVG the package installs.

One file, packaging/openhamshack-teletext.svg, is both the desktop icon and the
window icon (as in OpenHamShack ATV and NB). Installed, it is under
share/icons/hicolor next to the lib directory the launcher puts on
PYTHONPATH; from a source tree it is in packaging/. If none is found, or
this Qt has no SVG image plugin (qt6-svg-plugins), a painted copy of the
same drawing stands in rather than a blank square.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPixmap

_HERE = Path(__file__).resolve()

# (x, y, w, h, colour) on the SVG's 64-unit grid: the page on the screen.
_PAGE = [(10, 15, 44, 8, "#0000ff"), (13, 16.5, 3, 5, "#ffff00"), (17, 16.5, 3, 2.5, "#ffff00"),
         (21, 16.5, 3, 5, "#ffff00"), (27, 17, 22, 4, "#ffffff"), (13, 27, 26, 2.6, "#ffffff"),
         (42, 27, 8, 2.6, "#00ffff"), (13, 32, 22, 2.6, "#ffffff"), (42, 32, 8, 2.6, "#00ffff"),
         (13, 37, 24, 2.6, "#ffffff"), (42, 37, 8, 2.6, "#00ffff"), (12, 44, 9, 4, "#ff0000"),
         (22.5, 44, 9, 4, "#00ff00"), (33, 44, 9, 4, "#ffff00"), (43.5, 44, 9, 4, "#00ffff")]


def svg_path() -> Path | None:
    pkg = _HERE.parents[1]                 # .../ohstxt
    cands = [pkg.parent / "packaging" / "openhamshack-teletext.svg",
             pkg.parents[2] / "share" / "icons" / "hicolor" / "scalable" / "apps" / "openhamshack-teletext.svg",
             Path("/usr/share/icons/hicolor/scalable/apps/openhamshack-teletext.svg")]
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
    b = QLinearGradient(0, 9, 0, 55)
    b.setColorAt(0, QColor("#3a4558"))
    b.setColorAt(1, QColor("#1b212c"))
    p.setBrush(b)
    p.drawRoundedRect(QRectF(6, 9, 52, 46), 6, 6)
    p.setBrush(QColor("#000000"))
    p.drawRoundedRect(QRectF(10, 13, 44, 38), 3, 3)
    for x, y, w, h, col in _PAGE:
        p.setBrush(QColor(col))
        p.drawRect(QRectF(x, y, w, h))
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
