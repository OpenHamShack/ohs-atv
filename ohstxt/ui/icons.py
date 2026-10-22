"""Icons, drawn as paths on a 24-unit grid (OpenHamShack ATV's set, plus the
drawing tools and editing actions at the end).

No image files: the program may be installed on a machine with no network,
from a source tree that someone has trimmed, and a missing PNG is a blank
button. A path also scales to any DPI and takes the theme colour for free.
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap


def _mic(p: QPainterPath):
    p.addRoundedRect(QRectF(9, 3, 6, 11), 3, 3)
    p.moveTo(6, 11)
    p.cubicTo(6, 18, 18, 18, 18, 11)
    p.moveTo(12, 17)
    p.lineTo(12, 21)
    p.moveTo(9, 21)
    p.lineTo(15, 21)


def _gear(p: QPainterPath):
    p.addEllipse(QPointF(12, 12), 3, 3)
    n = 8
    pts = []
    for i in range(n * 2):
        a = math.pi * i / n
        r = 9 if i % 2 == 0 else 7
        pts.append(QPointF(12 + r * math.cos(a), 12 + r * math.sin(a)))
    p.moveTo(pts[0])
    for q in pts[1:]:
        p.lineTo(q)
    p.closeSubpath()


def _lock(p: QPainterPath):
    p.addRoundedRect(QRectF(5, 10, 14, 10), 2, 2)
    p.moveTo(8, 10)
    p.lineTo(8, 7)
    p.cubicTo(8, 2, 16, 2, 16, 7)
    p.lineTo(16, 10)


def _book(p: QPainterPath):
    p.addRoundedRect(QRectF(5, 3, 14, 18), 2, 2)
    p.moveTo(9, 3)
    p.lineTo(9, 21)
    p.moveTo(12, 8)
    p.lineTo(16, 8)
    p.moveTo(12, 11)
    p.lineTo(16, 11)


def _star(p: QPainterPath):
    pts = []
    for i in range(10):
        a = -math.pi / 2 + math.pi * i / 5
        r = 9 if i % 2 == 0 else 4
        pts.append(QPointF(12 + r * math.cos(a), 12.5 + r * math.sin(a)))
    p.moveTo(pts[0])
    for q in pts[1:]:
        p.lineTo(q)
    p.closeSubpath()


def _screen(p: QPainterPath):
    p.addRoundedRect(QRectF(3, 4, 18, 12), 2, 2)
    p.moveTo(12, 16)
    p.lineTo(12, 20)
    p.moveTo(8, 20)
    p.lineTo(16, 20)


def _keyboard(p: QPainterPath):
    p.addRoundedRect(QRectF(2, 6, 20, 12), 2, 2)
    for x in (6, 10, 14, 18):
        p.moveTo(x, 10)
        p.lineTo(x + 0.5, 10)
    p.moveTo(7, 14)
    p.lineTo(17, 14)


def _send(p: QPainterPath):
    p.moveTo(3, 11)
    p.lineTo(21, 3)
    p.lineTo(14, 21)
    p.lineTo(11, 13)
    p.closeSubpath()


def _folder(p: QPainterPath):
    p.moveTo(3, 6)
    p.lineTo(9, 6)
    p.lineTo(11, 8)
    p.lineTo(21, 8)
    p.lineTo(21, 19)
    p.lineTo(3, 19)
    p.closeSubpath()


def _plus(p: QPainterPath):
    p.moveTo(12, 5)
    p.lineTo(12, 19)
    p.moveTo(5, 12)
    p.lineTo(19, 12)


def _close(p: QPainterPath):
    p.moveTo(6, 6)
    p.lineTo(18, 18)
    p.moveTo(18, 6)
    p.lineTo(6, 18)


def _export(p: QPainterPath):
    p.moveTo(12, 3)
    p.lineTo(12, 15)
    p.moveTo(7, 8)
    p.lineTo(12, 3)
    p.lineTo(17, 8)
    p.moveTo(4, 14)
    p.lineTo(4, 20)
    p.lineTo(20, 20)
    p.lineTo(20, 14)


def _trash(p: QPainterPath):
    p.moveTo(4, 7)
    p.lineTo(20, 7)
    p.moveTo(9, 7)
    p.lineTo(9, 4)
    p.lineTo(15, 4)
    p.lineTo(15, 7)
    p.addRoundedRect(QRectF(6, 7, 12, 14), 2, 2)


def _info(p: QPainterPath):
    p.addEllipse(QPointF(12, 12), 9, 9)
    p.moveTo(12, 11)
    p.lineTo(12, 17)
    p.moveTo(12, 7.5)
    p.lineTo(12, 8)


def _warn(p: QPainterPath):
    p.moveTo(12, 3)
    p.lineTo(22, 20)
    p.lineTo(2, 20)
    p.closeSubpath()
    p.moveTo(12, 9)
    p.lineTo(12, 14)
    p.moveTo(12, 17)
    p.lineTo(12, 17.5)


def _satellite(p: QPainterPath):
    p.addRect(QRectF(10, 10, 4, 4))
    p.addRect(QRectF(2, 10.5, 6, 3))
    p.addRect(QRectF(16, 10.5, 6, 3))
    p.moveTo(8, 12)
    p.lineTo(10, 12)
    p.moveTo(14, 12)
    p.lineTo(16, 12)
    p.moveTo(12, 14)
    p.lineTo(12, 18)
    p.moveTo(9, 20)
    p.cubicTo(10, 18.5, 14, 18.5, 15, 20)


def _antenna(p: QPainterPath):
    p.moveTo(12, 10)
    p.lineTo(12, 21)
    p.moveTo(8, 21)
    p.lineTo(16, 21)
    p.addEllipse(QPointF(12, 9), 1.5, 1.5)
    p.moveTo(7.5, 13.5)
    p.cubicTo(5, 11, 5, 7, 7.5, 4.5)
    p.moveTo(16.5, 13.5)
    p.cubicTo(19, 11, 19, 7, 16.5, 4.5)


def _waves(p: QPainterPath):
    p.moveTo(2, 12)
    for i in range(5):
        x = 2 + i * 4
        p.cubicTo(x + 1, 5, x + 3, 5, x + 4, 12) if i % 2 == 0 else p.cubicTo(x + 1, 19, x + 3, 19, x + 4, 12)


def _image(p: QPainterPath):
    p.addRoundedRect(QRectF(3, 5, 18, 14), 2, 2)
    p.moveTo(3, 16)
    p.lineTo(9, 11)
    p.lineTo(14, 16)
    p.lineTo(17, 13)
    p.lineTo(21, 17)
    p.addEllipse(QPointF(16, 9), 1.5, 1.5)


def _help(p: QPainterPath):
    p.addEllipse(QPointF(12, 12), 9, 9)
    p.moveTo(9.5, 9.5)
    p.cubicTo(9.5, 6, 14.5, 6, 14.5, 9.5)
    p.cubicTo(14.5, 11.5, 12, 11.5, 12, 14)
    p.moveTo(12, 17)
    p.lineTo(12, 17.5)


def _check(p: QPainterPath):
    p.moveTo(5, 12.5)
    p.lineTo(10, 17.5)
    p.lineTo(19, 7)


def _camera(p: QPainterPath):
    p.addRoundedRect(QRectF(3, 7, 18, 13), 2.5, 2.5)
    p.moveTo(8, 7)
    p.lineTo(9.5, 4)
    p.lineTo(14.5, 4)
    p.lineTo(16, 7)
    p.addEllipse(QPointF(12, 13.5), 3.5, 3.5)


def _webcam(p: QPainterPath):
    p.addEllipse(QPointF(12, 10), 7, 7)
    p.addEllipse(QPointF(12, 10), 2.5, 2.5)
    p.moveTo(8, 21)
    p.lineTo(16, 21)
    p.moveTo(12, 17)
    p.lineTo(12, 21)


def _bars(p: QPainterPath):
    p.addRoundedRect(QRectF(3, 5, 18, 14), 2, 2)
    for x in (7.5, 12, 16.5):
        p.moveTo(x, 5)
        p.lineTo(x, 19)


def _card(p: QPainterPath):
    p.addRoundedRect(QRectF(3, 5, 18, 14), 2, 2)
    p.addEllipse(QPointF(12, 12), 5, 5)
    p.moveTo(3, 12)
    p.lineTo(7, 12)
    p.moveTo(17, 12)
    p.lineTo(21, 12)


def _film(p: QPainterPath):
    p.addRoundedRect(QRectF(4, 3, 16, 18), 2, 2)
    p.moveTo(8, 3)
    p.lineTo(8, 21)
    p.moveTo(16, 3)
    p.lineTo(16, 21)
    for y in (7.5, 12, 16.5):
        p.moveTo(4, y)
        p.lineTo(8, y)
        p.moveTo(16, y)
        p.lineTo(20, y)


def _tone(p: QPainterPath):
    p.moveTo(2, 12)
    p.cubicTo(4.5, 2, 7.5, 2, 9.5, 12)
    p.cubicTo(11.5, 22, 14.5, 22, 16.5, 12)
    p.cubicTo(18, 6, 20.5, 5, 22, 7)


def _none(p: QPainterPath):
    p.addEllipse(QPointF(12, 12), 8, 8)
    p.moveTo(6.5, 17.5)
    p.lineTo(17.5, 6.5)


def _tv(p: QPainterPath):
    p.addRoundedRect(QRectF(3, 6, 18, 13), 2.5, 2.5)
    p.moveTo(8, 2.5)
    p.lineTo(12, 6)
    p.lineTo(16, 2.5)
    p.moveTo(8, 21.5)
    p.lineTo(16, 21.5)


def _expand(p: QPainterPath):
    for (x0, y0, dx, dy) in ((4, 4, 1, 1), (20, 4, -1, 1), (4, 20, 1, -1), (20, 20, -1, -1)):
        p.moveTo(x0 + 5 * dx, y0)
        p.lineTo(x0, y0)
        p.lineTo(x0, y0 + 5 * dy)


def _chip(p: QPainterPath):
    p.addRoundedRect(QRectF(6, 6, 12, 12), 2, 2)
    for k in (9, 12, 15):
        p.moveTo(k, 2.5)
        p.lineTo(k, 6)
        p.moveTo(k, 18)
        p.lineTo(k, 21.5)
        p.moveTo(2.5, k)
        p.lineTo(6, k)
        p.moveTo(18, k)
        p.lineTo(21.5, k)


def _scope(p: QPainterPath):
    p.addRoundedRect(QRectF(3, 4, 18, 16), 2, 2)
    p.moveTo(5, 15)
    p.lineTo(8, 15)
    p.lineTo(9, 9)
    p.lineTo(11, 9)
    p.lineTo(12, 15)
    p.lineTo(14, 15)
    p.lineTo(15, 11)
    p.lineTo(17, 11)
    p.lineTo(18, 15)
    p.lineTo(19, 15)


def _sliders(p: QPainterPath):
    for y, x in ((6, 15), (12, 8), (18, 13)):
        p.moveTo(3, y)
        p.lineTo(21, y)
        p.addEllipse(QPointF(x, y), 2, 2)


def _speaker(p: QPainterPath):
    p.moveTo(4, 9)
    p.lineTo(8, 9)
    p.lineTo(13, 4.5)
    p.lineTo(13, 19.5)
    p.lineTo(8, 15)
    p.lineTo(4, 15)
    p.closeSubpath()
    p.moveTo(16.5, 8.5)
    p.cubicTo(18.5, 10.5, 18.5, 13.5, 16.5, 15.5)
    p.moveTo(19, 6)
    p.cubicTo(22.5, 9.5, 22.5, 14.5, 19, 18)


def _theme(p: QPainterPath):
    p.addEllipse(QPointF(12, 12), 8.5, 8.5)
    p.moveTo(12, 3.5)
    p.lineTo(12, 20.5)
    p.cubicTo(16.7, 20.5, 20.5, 16.7, 20.5, 12)
    p.cubicTo(20.5, 7.3, 16.7, 3.5, 12, 3.5)


# --- OpenHamShack Teletext's own: the drawing tools and the editor's actions.
def _text(p: QPainterPath):
    p.moveTo(5, 6)
    p.lineTo(19, 6)
    p.moveTo(12, 6)
    p.lineTo(12, 19)
    p.moveTo(9, 19)
    p.lineTo(15, 19)


def _pen(p: QPainterPath):
    p.moveTo(5, 19)
    p.lineTo(6, 15)
    p.lineTo(16, 5)
    p.lineTo(19, 8)
    p.lineTo(9, 18)
    p.closeSubpath()
    p.moveTo(14, 7)
    p.lineTo(17, 10)


def _line(p: QPainterPath):
    p.moveTo(5, 19)
    p.lineTo(19, 5)
    p.addEllipse(QPointF(5, 19), 1.6, 1.6)
    p.addEllipse(QPointF(19, 5), 1.6, 1.6)


def _rect(p: QPainterPath):
    p.addRect(QRectF(5, 6, 14, 12))


def _fill(p: QPainterPath):
    p.moveTo(6, 11)
    p.lineTo(12, 5)
    p.lineTo(18, 11)
    p.lineTo(12, 17)
    p.closeSubpath()
    p.moveTo(18, 14)
    p.cubicTo(20, 17, 20, 19, 18.5, 19.5)
    p.cubicTo(17, 19.5, 16.5, 17, 18, 14)


def _undo(p: QPainterPath):
    p.moveTo(9, 6)
    p.lineTo(5, 10)
    p.lineTo(9, 14)
    p.moveTo(5, 10)
    p.lineTo(14, 10)
    p.cubicTo(18, 10, 19, 13, 19, 14.5)
    p.cubicTo(19, 17, 17, 19, 14, 19)
    p.lineTo(10, 19)


def _redo(p: QPainterPath):
    p.moveTo(15, 6)
    p.lineTo(19, 10)
    p.lineTo(15, 14)
    p.moveTo(19, 10)
    p.lineTo(10, 10)
    p.cubicTo(6, 10, 5, 13, 5, 14.5)
    p.cubicTo(5, 17, 7, 19, 10, 19)
    p.lineTo(14, 19)


def _eye(p: QPainterPath):
    p.moveTo(3, 12)
    p.cubicTo(6, 6.5, 18, 6.5, 21, 12)
    p.cubicTo(18, 17.5, 6, 17.5, 3, 12)
    p.addEllipse(QPointF(12, 12), 2.8, 2.8)


def _grid(p: QPainterPath):
    p.addRect(QRectF(4, 4, 16, 16))
    for v in (9.33, 14.67):
        p.moveTo(v, 4)
        p.lineTo(v, 20)
        p.moveTo(4, v)
        p.lineTo(20, v)


def _codes(p: QPainterPath):
    p.moveTo(9, 6)
    p.lineTo(4, 12)
    p.lineTo(9, 18)
    p.moveTo(15, 6)
    p.lineTo(20, 12)
    p.lineTo(15, 18)
    p.moveTo(13.5, 5)
    p.lineTo(10.5, 19)


def _save(p: QPainterPath):
    p.moveTo(5, 4)
    p.lineTo(16, 4)
    p.lineTo(20, 8)
    p.lineTo(20, 20)
    p.lineTo(4, 20)
    p.lineTo(4, 4)
    p.closeSubpath()
    p.addRect(QRectF(8, 4, 7, 5))
    p.addRect(QRectF(7.5, 13, 9, 7))


def _link(p: QPainterPath):
    p.addRoundedRect(QRectF(3, 9, 10, 6), 3, 3)
    p.addRoundedRect(QRectF(11, 9, 10, 6), 3, 3)


def _play(p: QPainterPath):
    p.moveTo(8, 5)
    p.lineTo(19, 12)
    p.lineTo(8, 19)
    p.closeSubpath()


def _page(p: QPainterPath):
    p.addRoundedRect(QRectF(4, 3, 16, 18), 2, 2)
    for y in (8, 11.5, 15):
        p.moveTo(7.5, y)
        p.lineTo(16.5, y)


DRAW = {"mic": _mic, "gear": _gear, "lock": _lock, "book": _book, "star": _star, "screen": _screen,
        "keyboard": _keyboard, "send": _send, "folder": _folder, "plus": _plus, "close": _close,
        "export": _export, "trash": _trash, "info": _info, "warn": _warn, "satellite": _satellite,
        "antenna": _antenna, "waves": _waves, "image": _image, "help": _help, "check": _check,
        "camera": _camera, "webcam": _webcam, "bars": _bars, "card": _card, "film": _film, "tone": _tone,
        "none": _none, "tv": _tv, "expand": _expand, "chip": _chip, "scope": _scope, "sliders": _sliders,
        "speaker": _speaker, "theme": _theme, "text": _text, "pen": _pen, "line": _line, "rect": _rect,
        "fill": _fill, "undo": _undo, "redo": _redo, "eye": _eye, "grid": _grid, "codes": _codes, "save": _save,
        "link": _link, "play": _play, "page": _page}
FILLED = {"star", "play"}



def path(name: str) -> QPainterPath:
    p = QPainterPath()
    DRAW[name](p)
    return p


def paint(qp: QPainter, name: str, rect: QRectF, colour: QColor, width: float = 1.8):
    qp.save()
    qp.setRenderHint(QPainter.RenderHint.Antialiasing)
    qp.translate(rect.x(), rect.y())
    s = min(rect.width(), rect.height()) / 24.0
    qp.scale(s, s)
    # Cosmetic: the stroke stays `width` device-independent pixels whatever the icon size.
    pen = QPen(colour, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    pen.setCosmetic(True)
    qp.setPen(pen)
    qp.setBrush(colour if name in FILLED else Qt.BrushStyle.NoBrush)
    qp.drawPath(path(name))
    qp.restore()


def icon(name: str, colour: QColor, size: int = 24, dpr: float = 2.0) -> QIcon:
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    qp = QPainter(pm)
    paint(qp, name, QRectF(0, 0, size, size), colour)
    qp.end()
    return QIcon(pm)
