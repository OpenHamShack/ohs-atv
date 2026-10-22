"""The teletext viewer: the received service's pages, as a television shows
them, in the picture card (the Text button, or T).

Everything a set's remote had: three digits for a page, the four coloured
fastext keys (the page's X/27/0 links; clicking the coloured prompts on row
24 does the same), reveal, hold, and stepping through a carousel's
subpages - plus mix, the page over the picture with its black background
clear. The page is drawn by ohsatv/ttxrender.py (Level 1 attributes, the
built-in character set); this widget only places it at 4:3 and keeps it
current: when the engine says a page has finished arriving (the
`teletext` feed) and it is the one shown, it is fetched again - unless
held - and the header row rolls with every header heard, as a set's does
while it looks for the page asked for.

Keys while the viewer is up (MainWindow routes them): 0-9 page number,
F1-F4 red / green / yellow / cyan, PgUp / PgDn subpage, R reveal, H hold,
M mix, T back to the picture.
"""
from __future__ import annotations

import time

import numpy as np
from PyQt6.QtCore import QDir, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetricsF, QImage, QPainter, QPen
from PyQt6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from .. import ttxrender
from . import theme
from .controls import Button, FocusVisible

FASTEXT = [("red", "#e0453a"), ("green", "#2fb14a"), ("yellow", "#e8c522"), ("cyan", "#2ab7c9")]
FLASH_ON_S, FLASH_OFF_S = 0.75, 0.25           # a set's flash: about once a second, mostly on


class FastextKey(FocusVisible, QWidget):
    """A coloured key with the page it goes to."""
    clicked = pyqtSignal()

    def __init__(self, colour: str, parent=None):
        super().__init__(parent)
        self.colour = QColor(colour)
        self.target = ""
        self.setFixedSize(40, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def set_target(self, label: str):
        self.target = label
        self.setEnabled(bool(label))
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.target:
            self.clicked.emit()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.target:
            self.clicked.emit()
        else:
            super().keyPressEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        c = QColor(self.colour)
        if not self.target:
            c.setAlphaF(0.25)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawRoundedRect(r, theme.RADIUS_SM, theme.RADIUS_SM)
        p.setFont(theme.font(12, theme.QFont.Weight.Bold, mono=True))
        p.setPen(QColor("#101010") if self.target else theme.c("text3"))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.target or "—")
        if self.focus_visible():
            p.setPen(QPen(theme.c("accent"), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), theme.RADIUS_SM, theme.RADIUS_SM)


class PageCanvas(QWidget):
    """The page at 4:3; clicks on row 24's prompts or on a page number in
    the text go there."""
    goto = pyqtSignal(str)
    fastext = pyqtSignal(int)

    def __init__(self, view, parent=None):
        super().__init__(parent)
        self.view = view
        self.setMinimumSize(320, 240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def screen_rect(self) -> QRectF:
        m = 8.0
        w, h = self.width() - 2 * m, self.height() - 2 * m
        if w / max(1.0, h) > 4 / 3:
            sw, sh = h * 4 / 3, h
        else:
            sw, sh = w, w * 3 / 4
        return QRectF((self.width() - sw) / 2, (self.height() - sh) / 2, sw, sh)

    def cell_at(self, pos) -> tuple[int, int] | None:
        sr = self.screen_rect()
        if not sr.contains(pos):
            return None
        x = int((pos.x() - sr.x()) / sr.width() * ttxrender.COLS)
        y = int((pos.y() - sr.y()) / sr.height() * ttxrender.ROWS)
        return min(x, ttxrender.COLS - 1), min(y, ttxrender.ROWS - 1)

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        cell = self.cell_at(e.position())
        if cell is None:
            return
        x, y = cell
        if y == 24:
            # The FLOF prompt row is laid out as four coloured quarters.
            self.fastext.emit(min(3, x // 10))
            return
        row = self.view.rows[y] if y < len(self.view.rows) else b""
        label = page_number_at(row, x)
        if label:
            self.goto.emit(label)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#05070a"))
        sr = self.screen_rect()
        img = self.view.image()
        if self.view.mix and self.view.picture is not None:
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawImage(sr, self.view.picture)
        if img is not None:
            p.drawImage(sr, img)
        msg = self.view.message()
        if msg:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setFont(theme.font(13, theme.QFont.Weight.DemiBold))
            fm = QFontMetricsF(p.font())
            w = fm.horizontalAdvance(msg) + 28
            r = QRectF(sr.center().x() - w / 2, sr.center().y() - 18, w, 36)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 190))
            p.drawRoundedRect(r, 10, 10)
            p.setPen(QColor("#e8eef5"))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, msg)


def page_number_at(row: bytes, x: int) -> str:
    """The three-digit page number (100-899) in `row` that column x is on,
    or ''. Pages say 'News 101' in their text; clicking it goes there."""
    s = bytes(c & 0x7F for c in row)
    for i in range(max(0, x - 2), min(len(s) - 2, x + 1)):
        w = s[i:i + 3]
        if (len(w) == 3 and all(0x30 <= c <= 0x39 for c in w) and 0x31 <= w[0] <= 0x38
                and (i == 0 or not 0x30 <= s[i - 1] <= 0x39) and (i + 3 >= len(s) or not 0x30 <= s[i + 3] <= 0x39)):
            return w.decode()
    return ""


class TeletextView(QWidget):
    """The viewer. `label` is the page asked for; `rows` what is shown."""

    def __init__(self, bridge, prefs=None, parent=None):
        super().__init__(parent)
        self.bridge, self.prefs = bridge, prefs
        self.label = (prefs.get("ttxPage") if prefs is not None else None) or ""
        self.entry = ""                  # digits typed so far
        self.sub: int | None = None      # a chosen sub-code, or None for the newest
        self.reveal = self.hold = self.mix = False
        self.page: dict | None = None
        self.rows = [b" " * 40] * 25
        self.picture: QImage | None = None
        self._img: QImage | None = None
        self._img_key = None
        self._flash_t = time.monotonic()
        self._header_t = 0.0
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.canvas = PageCanvas(self)
        self.canvas.goto.connect(self.go)
        self.canvas.fastext.connect(self.follow)
        lay.addWidget(self.canvas, 1)
        bar = QHBoxLayout()
        bar.setContentsMargins(8, 0, 8, 4)
        # Fixed widths: at 1366 x 768 the card is ~440 px wide and every
        # control must still read (the first layout elided Reveal and Hold).
        bar.setSpacing(4)
        self.prev = Button("", icon="chevron-left", small=True)
        self.prev.setToolTip(self.tr("Previous subpage (PgUp)"))
        self.prev.clicked.connect(lambda: self.step_sub(-1))
        self.next = Button("", icon="chevron-right", small=True)
        self.next.setToolTip(self.tr("Next subpage (PgDn)"))
        self.next.clicked.connect(lambda: self.step_sub(1))
        self.keys = []
        # Whole sentences, one per key: a colour's word can change with the
        # noun in other languages, so "{colour} link" would not translate.
        tips = (self.tr("Red link (F1)"), self.tr("Green link (F2)"), self.tr("Yellow link (F3)"),
                self.tr("Cyan link (F4)"))
        for i, (_, colour) in enumerate(FASTEXT):
            k = FastextKey(colour)
            k.setToolTip(tips[i])
            k.clicked.connect(lambda i=i: self.follow(i))
            self.keys.append(k)
        self.reveal_btn = Button(self.tr("Reveal"), small=True, checkable=True)
        self.reveal_btn.setToolTip(self.tr("Show concealed text (R)"))
        self.reveal_btn.clicked.connect(lambda: self.set_reveal(self.reveal_btn.checked))
        self.hold_btn = Button(self.tr("Hold"), small=True, checkable=True)
        self.hold_btn.setToolTip(self.tr("Keep this subpage on the screen (H)"))
        self.hold_btn.clicked.connect(lambda: self.set_hold(self.hold_btn.checked))
        self.mix_btn = Button(self.tr("Mix"), small=True, checkable=True)
        self.mix_btn.setToolTip(self.tr("The page over the received picture (M)"))
        self.mix_btn.clicked.connect(lambda: self.set_mix(self.mix_btn.checked))
        # Save: this page (all its subpages) as .tti, or everything received
        # as a folder of .tti and a .t42 capture - files OpenHamShack Teletext
        # opens (vbit2's dialect, tti.dumps).
        self.save_btn = Button("", icon="export", small=True)
        self.save_btn.setToolTip(self.tr("Save this page, or everything received, as .tti files (and a .t42 capture)"))
        self.save_btn.clicked.connect(self.save_menu)
        for b, w in ((self.prev, 30), (self.next, 30), (self.reveal_btn, 54), (self.hold_btn, 44),
                     (self.mix_btn, 38), (self.save_btn, 30)):
            b.setFixedWidth(w)
        bar.addWidget(self.prev)
        bar.addWidget(self.next)
        bar.addSpacing(4)
        for k in self.keys:
            bar.addWidget(k)
        bar.addStretch(1)
        for b in (self.reveal_btn, self.hold_btn, self.mix_btn, self.save_btn):
            bar.addWidget(b)
        lay.addLayout(bar)
        bridge.teletext.connect(self.on_pages)
        bridge.video.connect(self.on_video)
        bridge.tick.connect(self.on_tick)
        self.refresh()

    def sizeHint(self):
        return QSize(800, 640)

    # ------------------------------------------------------------ navigation
    def current(self) -> str:
        if self.label:
            return self.label
        ini = (self.bridge.st.get("ttxRx") or {}).get("initial") or ""
        return ini or "100"

    def go(self, label: str):
        self.label, self.entry, self.sub = label, "", None
        self.set_hold(False)
        if self.prefs is not None:
            self.prefs["ttxPage"] = label
        self.refresh()

    def digit(self, d: str):
        if not self.entry and d in "09":
            return                      # magazines are 1-8
        self.entry += d
        if len(self.entry) == 3:
            self.go(self.entry)
        else:
            self.update_rows()

    def follow(self, i: int):
        links = (self.page or {}).get("links") or []
        if i < len(links) and links[i]:
            self.go(links[i][0])

    def step_sub(self, d: int):
        subs = (self.page or {}).get("subs") or []
        if not subs:
            return
        cur = self.page["sub"] if self.page else subs[0]
        i = subs.index(cur) if cur in subs else 0
        self.sub = subs[(i + d) % len(subs)]
        self.set_hold(True)            # stepping by hand: stay on it
        self.refresh(force=True)

    def set_reveal(self, on: bool):
        self.reveal = on
        self.reveal_btn.set_checked(on)
        self.canvas.update()

    def set_hold(self, on: bool):
        self.hold = on
        self.hold_btn.set_checked(on)
        if not on:
            self.sub = None
            self.refresh()

    def set_mix(self, on: bool):
        self.mix = on
        self.mix_btn.set_checked(on)
        self._img_key = None
        self.canvas.update()

    # ------------------------------------------------------------ data
    def on_pages(self, items: list):
        cur = self.current()
        if any(it.get("page") == cur for it in items) and not self.hold:
            self.refresh()

    def on_video(self, item):
        if self.mix and self.isVisible():
            from .picture import rgb_qimage
            self.picture, _ = rgb_qimage(item[1])

    def on_tick(self, _dt):
        if not self.isVisible():
            return
        now = time.monotonic()
        if now - self._header_t > 0.5:
            self._header_t = now
            # Not chosen a page yet: follow the sender's initial page (8/30)
            # once it is heard.
            if not self.label and not self.entry and (self.page or {}).get("label") != self.current():
                self.refresh()
            self.update_rows()
        # Repaint for the flash phase only when it turns (or with mix, for
        # the picture under the page): not at the pump's 30 Hz for nothing.
        fl = self.flash_on()
        if fl != getattr(self, "_flash_last", None) or self.mix:
            self._flash_last = fl
            self.canvas.update()

    def refresh(self, force: bool = False):
        if self.hold and not force:
            return
        try:
            self.page = self.bridge.radio.ttx_page(self.current(), self.sub)
        except Exception:  # noqa: BLE001 - a viewer must not take the window down
            self.page = None
        links = (self.page or {}).get("links") or []
        for i, k in enumerate(self.keys):
            ln = links[i] if i < len(links) else None
            k.set_target(ln[0] if ln else "")
        subs = (self.page or {}).get("subs") or []
        for b in (self.prev, self.next):
            b.setEnabled(len(subs) > 1)
        self.update_rows()

    def update_rows(self):
        """Row 0 is the set's own: the page asked for (or the digits being
        typed) in its first 8 columns, then the newest header heard from
        any page - the rolling header - while the page is not here, its own
        header once it is."""
        rows = list(self.page["rows"]) if self.page else [b" " * 40] * 25
        typed = self.entry.ljust(3, "-") if self.entry else self.current()
        lead = f"P{typed}".ljust(7).encode()
        # The header row green while the page is searched for, as sets did
        # (the colour code takes the last cell of the set's own 8 columns).
        lead += b"\x02" if self.page is None else b" "
        head = None
        if self.page is None or not self.hold:
            h = self.bridge.radio.ttx_header()
            if h is not None:
                head = h["text"]
        if self.page is not None and head is not None:
            # The clock keeps rolling on a page that is up: take the newest
            # header's last 8 columns (the time), keep the page's own text.
            head = self.page["rows"][0][8:32] + head[24:32]
        if head is None:
            head = self.page["rows"][0][8:] if self.page else b" " * 32
        rows[0] = lead + head
        self.rows = rows
        self._img_key = None
        self.canvas.update()

    def message(self) -> str:
        if self.page is not None:
            return ""
        st = self.bridge.st
        rx = st.get("ttxRx") or {}
        if st.get("rxFormat") == "405":
            return "405-line pictures carry no teletext"
        if not rx.get("packets"):
            return "No teletext on this signal"
        return f"Waiting for page {self.current()} · {rx.get('pages', 0)} pages heard"

    def flash_on(self) -> bool:
        ph = (time.monotonic() - self._flash_t) % (FLASH_ON_S + FLASH_OFF_S)
        return ph < FLASH_ON_S

    def image(self) -> QImage | None:
        key = (tuple(self.rows), self.reveal, self.flash_on(), self.mix)
        if key != self._img_key:
            rgb = ttxrender.render(self.rows, reveal=self.reveal, flash_on=key[2])
            if self.mix:
                # Black background clear: what is black in the page shows
                # the picture.
                a = np.where(rgb.any(axis=2), 255, 0).astype(np.uint8)
                rgba = np.ascontiguousarray(np.dstack([rgb, a]))
                img = QImage(rgba.data, rgba.shape[1], rgba.shape[0], rgba.shape[1] * 4,
                             QImage.Format.Format_RGBA8888).copy()
            else:
                rgb = np.ascontiguousarray(rgb)
                img = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.shape[1] * 3,
                             QImage.Format.Format_RGB888).copy()
            self._img, self._img_key = img, key
        return self._img

    # ------------------------------------------------------------ saving
    def save_menu(self):
        from PyQt6.QtWidgets import QMenu
        m = QMenu(self)
        a1 = m.addAction(self.tr("Save page {page}…").format(page=self.current()))
        a1.setEnabled(self.page is not None)
        a2 = m.addAction(self.tr("Save all received…"))
        a2.setEnabled(bool(self.bridge.radio.ttx_pages()))
        act = m.exec(self.save_btn.mapToGlobal(self.save_btn.rect().bottomLeft()))
        if act is a1:
            self.save_page()
        elif act is a2:
            self.save_all()

    def _start_dir(self) -> str:
        import os
        d = self.prefs.get("ttxSaveDir", "") if self.prefs is not None else ""
        return d or os.path.expanduser("~")

    def save_page(self, path: str | None = None) -> str:
        import os
        from PyQt6.QtWidgets import QFileDialog
        label = self.current()
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, self.tr("Save page {page}").format(page=label),
                                                  os.path.join(self._start_dir(), f"P{label}.tti"),
                                                  self.tr("Teletext page (*.tti)"))
            if not path:
                return ""
            path = QDir.toNativeSeparators(path)        # C:/ from Qt's dialog on Windows
        try:
            out = self.bridge.radio.ttx_save_page(label, path)
        except ValueError as e:
            self.bridge.say(str(e))
            return ""
        if self.prefs is not None:
            self.prefs["ttxSaveDir"] = os.path.dirname(out)
        self.bridge.say(self.tr("Saved page {page} to {path}").format(page=label, path=out), "info")
        return out

    def save_all(self, folder: str | None = None) -> str:
        from PyQt6.QtWidgets import QFileDialog
        if folder is None:
            folder = QFileDialog.getExistingDirectory(self, self.tr("Save every received page (.tti) and a .t42 capture"),
                                                      self._start_dir())
            if not folder:
                return ""
            folder = QDir.toNativeSeparators(folder)
        try:
            n, t42 = self.bridge.radio.ttx_save_all(folder)
        except ValueError as e:
            self.bridge.say(str(e))
            return ""
        if self.prefs is not None:
            self.prefs["ttxSaveDir"] = folder
        said = (self.tr("Saved %n page(s) and the .t42 capture to {folder}", "", n) if t42 else
                self.tr("Saved %n page(s) to {folder}", "", n))
        self.bridge.say(said.format(folder=folder), "info")
        return folder

    # ------------------------------------------------------------ keys
    def key(self, k: int, text: str) -> bool:
        """A key from the main window's filter; True if it was the viewer's."""
        if text and text in "0123456789":
            self.digit(text)
            return True
        fk = {Qt.Key.Key_F1: 0, Qt.Key.Key_F2: 1, Qt.Key.Key_F3: 2, Qt.Key.Key_F4: 3}
        if k in fk:
            self.follow(fk[k])
            return True
        if k == Qt.Key.Key_PageUp:
            self.step_sub(-1)
            return True
        if k == Qt.Key.Key_PageDown:
            self.step_sub(1)
            return True
        if k == Qt.Key.Key_R:
            self.set_reveal(not self.reveal)
            return True
        if k == Qt.Key.Key_H:
            self.set_hold(not self.hold)
            return True
        if k == Qt.Key.Key_M:
            self.set_mix(not self.mix)
            return True
        return False

