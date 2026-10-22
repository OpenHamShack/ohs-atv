"""Window furniture: the status strip, the shortcuts overlay and the header's
small icon buttons (copied from OpenHamShack NB)."""
from __future__ import annotations

import time

from PyQt6.QtCore import QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetricsF, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from . import icons, theme
from .controls import FocusVisible, Anim, mix

SHORTCUTS = [
    ("Tuning", [("← / →", "Down / up one step"), ("Shift + ← / →", "Ten steps"), ("Wheel", "One step (over a digit: "
                "that digit)"), ("Click readout", "Type a frequency"), ("Click channel", "Tune to it")]),
    ("Transmit", [("Space (hold)", "PTT - released, it unkeys"), ("Esc", "Unkey now")]),
    ("Picture", [("P", "Received picture / TX preview"), ("Ctrl + S", "Save the picture as PNG"),
                 ("T", "Teletext / picture"), ("0-9", "Teletext page"), ("F1-F4", "Red, green, yellow, cyan"),
                 ("PgUp / PgDn", "Subpage"), ("R / H / M", "Reveal, hold, mix"),
                 ("S", "Subtitles (888) on the picture")]),
    ("Window", [("Ctrl + ,", "Settings"), ("Ctrl + T", "Light / dark theme"), ("F11", "Full screen"),
                ("?", "This list")]),
]


class StatusStrip(QWidget):
    """One line at the bottom: device, rate, load, corrections, UTC."""

    def __init__(self, bridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.setFixedHeight(28)
        self.extra = ""

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), theme.c("bg0"))
        p.setPen(QPen(theme.c("line"), 1))
        p.drawLine(0, 0, self.width(), 0)
        st = self.bridge.st
        running = bool(st.get("running"))
        x = 14.0
        p.setFont(theme.font(11.5))
        fm = QFontMetricsF(p.font())
        dot = theme.c("ok") if running else theme.c("err") if st.get("error") else theme.c("text3")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(dot)
        p.drawEllipse(QRectF(x, 10, 8, 8))
        x += 16
        from .panels import radio_name
        dev = radio_name(st)
        items = [(dev + ("" if running else "  —  " + (st.get("error") or "stopped")), "text")]
        if running:
            fs = float(st.get("fs") or 0)
            link = st.get("link") or ""
            items += [(f"{fs / 1e6:g} MS/s" + (f" over {link}" if link and link != "sim" else ""), "text2"),
                      (f"decoded {float(st.get('fps') or 0):.1f} fps", "text2"),
                      (f"CPU {100 * float(st.get('cpu') or 0):.0f} %", "text2"),
                      (f"skipped {st.get('decodeSkipped', 0)}", "warn" if st.get("decodeSkipped") else "text3")]
            if st.get("txKind") == "hackrf":
                n = int(st.get("txUnderruns") or 0)
                items.append(("TX HackRF" + (f" · underruns {n}" if n else ""), "err" if n else "text2"))
            if st.get("txRepeats"):
                items.append((f"TX repeats {st['txRepeats']}", "warn"))
            if st.get("carrierHz") is not None and st.get("system") == "am":
                items.append((f"carrier {float(st['carrierHz']) / 1e3:+.1f} kHz", "text2"))
            if st.get("sourceStatus"):
                items.append((f"source: {st['sourceStatus']}", "text3"))
        if self.extra:
            items.append((self.extra, "text3"))
        for text, col in items:
            if not text:
                continue
            p.setPen(theme.c(col))
            w = fm.horizontalAdvance(text)
            if x + w > self.width() - 150:
                break
            p.drawText(QRectF(x, 0, w + 4, self.height()), Qt.AlignmentFlag.AlignVCenter, text)
            x += w + 22
        p.setFont(theme.font(12, theme.QFont.Weight.DemiBold, mono=True))
        p.setPen(theme.c("text2"))
        p.drawText(QRectF(self.width() - 150, 0, 136, self.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                   time.strftime("%H:%M:%S UTC", time.gmtime()))


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
        h = min(320, self.height() - 60)
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
            c = {0: 0, 1: 1, 2: 1, 3: 2}[i]
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
                p.drawText(QRectF(x + 138, y, colw - 138, 22), Qt.AlignmentFlag.AlignVCenter, what)
                y += 27
            ys[c] = y + 14

    def sizeHint(self):
        return QSize(800, 600)
