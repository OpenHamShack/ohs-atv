"""The preview: the page as a set shows it, live.

Same decoder and renderer as the editor; what differs is everything the
editor holds still so it can be edited. Here flash flashes (3/4 on, 1/4
off, a one-second cycle), conceal stays hidden until Reveal, the header
shows the page number and the template with vbit2's substitutions done
and the clock ticking, a newsflash or subtitle page shows only its boxes
over a stand-in for the television picture, and Play steps through the
subpages at their own cycle times as the inserter will.

It redraws only when what it shows changes: the flash phase, the second
in the header, the subpage. A page with no flash costs one render a second.
"""
from __future__ import annotations

import time

from PyQt6.QtCore import QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QLinearGradient, QPainter
from PyQt6.QtWidgets import QSizePolicy, QWidget

from .. import decoder, header, render
from ..page import PS_NEWSFLASH, PS_SUBTITLE, PS_SUPPRESS_HEADER
from . import theme
from .canvas import to_qimage


def display_rows(page, sub, now: float) -> list:
    """The rows a set displays: row 0 as the receiver fills it, or blank
    when the page asks for its header to be suppressed (C7)."""
    rows = [bytearray(r) for r in sub.rows]
    if sub.status & PS_SUPPRESS_HEADER:
        rows[0] = bytearray(b" " * 40)
    else:
        rows[0] = bytearray(header.display_row0(rows[0], page.number, now))
    return rows


class Preview(QWidget):
    subpageShown = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.doc = None
        self.reveal = False
        self.black_fg = False
        self.playing = False
        self.sub_index = 0
        self._key = None
        self._img = None
        self._next_turn = 0.0
        self.now = None                    # fixed time for screenshots and tests
        self.board = None                  # live data for a live page's tokens (ohstxt.live.Board)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumHeight(200)
        self._t = QTimer(self)
        self._t.timeout.connect(self._tick)
        self._t.start(60)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return int(w * 3 / 4)

    def sizeHint(self):
        return QSize(380, 285)

    def set_document(self, doc):
        self.doc = doc
        self.sub_index = doc.sub_index if doc else 0
        self._key = None
        self.update()

    def follow(self):
        """The editor moved to another subpage or changed the page."""
        if self.doc is not None and not self.playing:
            self.sub_index = self.doc.sub_index
        self._key = None
        self.update()

    def set_playing(self, on: bool):
        self.playing = on
        self._next_turn = time.monotonic() + self._cycle()
        self.update()

    def _cycle(self) -> float:
        if self.doc is None:
            return 8.0
        subs = self.doc.page.subpages
        s = subs[min(self.sub_index, len(subs) - 1)]
        # 'C' cycles count magazine cycles, which depend on the whole
        # service's size; a second each is a fair stand-in for a preview.
        return float(max(1, s.cycle))

    def _tick(self):
        if self.doc is None:
            return
        if self.playing and len(self.doc.page.subpages) > 1 and time.monotonic() >= self._next_turn:
            self.sub_index = (self.sub_index + 1) % len(self.doc.page.subpages)
            self._next_turn = time.monotonic() + self._cycle()
            self.subpageShown.emit(self.sub_index)
        now = self.now if self.now is not None else time.time()
        key = (int(now), render.flash_on(now), self.sub_index, self.reveal, self.black_fg)
        if key != self._key:
            self.update()

    def current(self):
        subs = self.doc.page.subpages
        return subs[min(self.sub_index, len(subs) - 1)]

    def image(self):
        now = self.now if self.now is not None else time.time()
        sub = self.current()
        rows = display_rows(self.doc.page, sub, now)
        if self.doc.page.live is not None and self.board is not None:
            rows = self.board.render_rows(self.doc.page, min(self.sub_index, len(self.doc.page.subpages) - 1),
                                          rows)
        d = decoder.decode(rows, black_fg=self.black_fg)
        mixed = bool(sub.status & (PS_NEWSFLASH | PS_SUBTITLE))
        rgb = render.render(d, flash_phase_on=render.flash_on(now), reveal=self.reveal, mixed=mixed)
        self._key = (int(now), render.flash_on(now), self.sub_index, self.reveal, self.black_fg)
        return to_qimage(rgb)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        # a 4:3 set, its bezel and its glass
        sh = min(h - 4, (w - 4) * 3 / 4)
        sw = sh * 4 / 3
        outer = QRectF((w - sw) / 2, (h - sh) / 2, sw, sh)
        g = QLinearGradient(0, outer.top(), 0, outer.bottom())
        g.setColorAt(0, QColor("#3a4558"))
        g.setColorAt(1, QColor("#1b212c"))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawRoundedRect(outer, 12, 12)
        pad = max(8.0, sw * 0.035)
        screen = outer.adjusted(pad, pad, -pad, -pad)
        p.setBrush(QColor(0, 0, 0))
        p.drawRoundedRect(screen, 8, 8)
        if self.doc is None:
            return
        # the page fills the safe area of the screen, as a set's teletext does
        img = self.image()
        inner = screen.adjusted(screen.width() * 0.05, screen.height() * 0.05, -screen.width() * 0.05,
                                -screen.height() * 0.05)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.drawImage(inner, img)
        glass = QLinearGradient(screen.topLeft(), screen.bottomRight())
        glass.setColorAt(0, QColor(255, 255, 255, 30))
        glass.setColorAt(0.45, QColor(255, 255, 255, 0))
        p.setBrush(glass)
        p.drawRoundedRect(screen, 8, 8)
        # corner badges: subpage and play state
        n = len(self.doc.page.subpages)
        if n > 1:
            p.setFont(theme.font(11, theme.QFont.Weight.DemiBold, mono=True))
            text = f"{self.sub_index + 1}/{n}" + ("  ▶" if self.playing else "")
            p.setPen(QColor(220, 225, 235))
            p.drawText(QRectF(outer.x(), outer.bottom() - pad, outer.width() - 14, pad),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, text)
