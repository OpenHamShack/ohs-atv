"""Window furniture: the status strip, the header's icon buttons and the
shortcuts overlay (the last two copied from OpenHamShack ATV, which copied them
from OpenHamShack NB)."""
from __future__ import annotations

import time

from PyQt6.QtCore import QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetricsF, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from . import icons, theme
from .controls import Anim, FocusVisible, mix

SHORTCUTS = [
    ("Esc, then a key: a control code", [
        ("r g y b m c w", "Alpha red ... white"), ("R G Y B M C W", "Graphics red ... white"),
        ("k / K", "Alpha / graphics black"), ("f / F", "Flash / steady"), ("d / D", "Double / normal height"),
        ("n / N", "New / black background"), ("h / H", "Hold / release graphics"),
        ("s / S", "Separated / contiguous"), ("o", "Conceal"), ("[ / ]", "Start / end box"),
        ("Esc Esc", "Cancel")]),
    ("Editing", [("Type", "Type-over at the cursor"), ("Arrows", "Move"), ("Backspace", "Clear to the left"),
                 ("Ins / Del", "Insert / delete a cell"), ("Ctrl+Ins/Del", "Insert / delete a row"),
                 ("PgUp / PgDn", "Previous / next subpage"), ("Ctrl+Z", "Undo"), ("Ctrl+Y", "Redo"),
                 ("q w a s z x", "Drawing tools: toggle a sixel"), ("Right drag", "Erase sixels"),
                 ("Shift+arrows", "Select a block (or drag)"), ("Ctrl+C X V", "Copy, cut, paste")]),
    ("Tools", [("Ctrl+1 ... 5", "Text, Pen, Line, Rect, Fill"), ("Ctrl+E", "Show control codes"),
               ("Ctrl+G", "Cell grid"), ("Ctrl+R", "Reveal concealed text"), ("Ctrl+K", "Check service"),
               ("Ctrl+U", "Subtitle editor")]),
    ("Files and window", [("Ctrl+S", "Save page"), ("Ctrl+Shift+S", "Save all"), ("Ctrl+O", "Open folder"),
                          ("Ctrl+N", "New page"), ("Ctrl+Shift+N", "From a template"),
                          ("Ctrl+Shift+O", "Open received .t42"), ("Ctrl+I", "Import image"),
                          ("Ctrl+L", "edit.tf link"),
                          ("Ctrl+T", "Light / dark theme"), ("F11", "Full screen"), ("F1", "This list")]),
]


class StatusStrip(QWidget):
    """One line at the bottom: the folder, the pages, what is unsaved, and
    the last thing the editor had to say."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(28)
        self.items: list[tuple[str, str]] = []
        self.message = ("", "text3")

    def set_items(self, items):
        self.items = items
        self.update()

    def set_message(self, text: str, level: str = "info"):
        self.message = (text, "warn" if level == "warn" else "text2")
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), theme.c("bg0"))
        p.setPen(QPen(theme.c("line"), 1))
        p.drawLine(0, 0, self.width(), 0)
        x = 14.0
        p.setFont(theme.font(11.5))
        fm = QFontMetricsF(p.font())
        items = list(self.items) + ([self.message] if self.message[0] else [])
        for text, col in items:
            p.setPen(theme.c(col))
            w = fm.horizontalAdvance(text)
            if x + w > self.width() - 150:
                break
            p.drawText(QRectF(x, 0, w + 4, self.height()), Qt.AlignmentFlag.AlignVCenter, text)
            x += w + 22
        p.setFont(theme.font(12, theme.QFont.Weight.DemiBold, mono=True))
        p.setPen(theme.c("text2"))
        p.drawText(QRectF(self.width() - 150, 0, 136, self.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, time.strftime("%H:%M:%S"))


class IconButton(FocusVisible, QWidget):
    clicked = pyqtSignal()

    def __init__(self, icon: str, tip: str, parent=None, checkable: bool = False):
        super().__init__(parent)
        self.icon = icon
        self.checkable = checkable
        self.checked = False
        self.hover = Anim(self)
        self.setToolTip(tip)
        self.setFixedSize(38, 38)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def set_checked(self, on):
        self.checked = bool(on)
        self.update()

    def enterEvent(self, e):
        self.hover.to(1)

    def leaveEvent(self, e):
        self.hover.to(0)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return):
            self.clicked.emit()
        else:
            super().keyPressEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        bg = mix(theme.c("bg1", 0.0), theme.c("bg3"), self.hover.value)
        if self.checked:
            bg = theme.c("accent", 0.18)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(r, 8, 8)
        col = theme.c("accent") if self.checked else mix(theme.c("text2"), theme.c("text"), self.hover.value)
        icons.paint(p, self.icon, QRectF(r.center().x() - 10, r.center().y() - 10, 20, 20), col, 1.7)
        if self.focus_visible():
            p.setPen(QPen(theme.c("accent"), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, 8, 8)


class ShortcutOverlay(QWidget):
    """'?' - every shortcut, over a dimmed window. Any key or click closes it."""

    def __init__(self, parent):
        super().__init__(parent)
        self.k = Anim(self)
        self.hide()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def open(self):
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        self.setFocus()
        self.k.value = 0.0
        self.k.to(1.0)

    def close_(self):
        self.hide()
        if self.parentWidget():
            self.parentWidget().setFocus()

    def keyPressEvent(self, e):
        self.close_()

    def mousePressEvent(self, e):
        self.close_()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        k = self.k.value
        p.fillRect(self.rect(), QColor(0, 0, 0, int(150 * k)))
        w = min(1100, self.width() - 80)
        h = min(700, self.height() - 40)
        card = QRectF((self.width() - w) / 2, (self.height() - h) / 2 + (1 - k) * 12, w, h)
        p.setOpacity(k)
        p.setPen(QPen(theme.c("line2"), 1))
        p.setBrush(theme.c("bg1"))
        p.drawRoundedRect(card, 14, 14)
        p.setFont(theme.font(20, theme.QFont.Weight.Bold))
        p.setPen(theme.c("text"))
        p.drawText(QRectF(card.x() + 32, card.y() + 22, w - 64, 30), Qt.AlignmentFlag.AlignVCenter,
                   self.tr("Keyboard shortcuts"))
        p.setFont(theme.font(12))
        p.setPen(theme.c("text3"))
        p.drawText(QRectF(card.x() + 32, card.y() + 22, w - 64, 30),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, self.tr("any key closes"))
        cols = 3
        colw = (w - 64 - 2 * 32) / cols
        y0 = card.y() + 72
        ys = [y0] * cols
        for i, (title, rows) in enumerate(SHORTCUTS):
            c = {0: 0, 1: 1, 2: 2, 3: 2}[i]
            x = card.x() + 32 + c * (colw + 32)
            y = ys[c]
            p.setFont(theme.caps(10.5))
            p.setPen(theme.c("accent"))
            p.drawText(QRectF(x, y, colw, 18), Qt.AlignmentFlag.AlignVCenter, title)
            y += 24
            for key, what in rows:
                p.setFont(theme.font(12, theme.QFont.Weight.DemiBold, mono=True))
                fm = QFontMetricsF(p.font())
                kw = fm.horizontalAdvance(key) + 14
                kr = QRectF(x, y, kw, 22)
                p.setPen(QPen(theme.c("line2"), 1))
                p.setBrush(theme.c("bg2"))
                p.drawRoundedRect(kr, 5, 5)
                p.setPen(theme.c("text"))
                p.drawText(kr, Qt.AlignmentFlag.AlignCenter, key)
                p.setFont(theme.font(13))
                p.setPen(theme.c("text2"))
                p.drawText(QRectF(x + 112, y, colw - 112, 22), Qt.AlignmentFlag.AlignVCenter, what)
                y += 25
            ys[c] = y + 14

    def sizeHint(self):
        return QSize(800, 600)
