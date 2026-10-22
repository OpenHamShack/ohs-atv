"""The frequency readout: receive in large figures, transmit beneath.

Copied from OpenHamShack NB's readout and re-ruled for ATV: the figure is the
vision carrier (AM-VSB) or the carrier (FM) in MHz, as ATV operators quote
it, with the kHz group in full size and the Hz group small and quiet -
nobody tunes a 6 MHz-wide picture to the hertz, but a repeater list does
quote 1311.5. Each digit is drawn centred in a fixed-width cell so the number
never shifts sideways; the wheel over a digit tunes by that digit's place
value; a click opens an editor (parse_freq).
"""
from __future__ import annotations

import re

from PyQt6.QtCore import QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QFontMetricsF, QPainter, QPen
from PyQt6.QtWidgets import QLineEdit, QSizePolicy, QWidget

from .. import bands
from ..i18n.fmt import number
from . import theme

STEPS = [10_000, 25_000, 100_000, 250_000, 500_000, 1_000_000]


def step_label(step: float) -> str:
    return f"{step / 1e6:g} MHz" if step >= 1e6 else f"{step / 1e3:g} kHz"


def parse_freq(text: str) -> float:
    """A typed frequency -> Hz. "1255", "1255.25", "1255.250 000" and
    "1255 MHz" are MHz; "1255250000" or "1.25525 GHz" also work. Raises
    ValueError with a sentence."""
    s = text.strip().lower().replace(" ", "").replace(",", ".")
    unit = re.search(r"(ghz|mhz|khz|hz)$", s)
    s = re.sub(r"(ghz|mhz|khz|hz)$", "", s)
    if s.count(".") > 1:
        head, _, tail = s.partition(".")
        s = head + "." + tail.replace(".", "")
    try:
        x = float(s)
    except ValueError:
        raise ValueError(f"{text!r} is not a frequency") from None
    if unit:
        x *= {"ghz": 1e9, "mhz": 1e6, "khz": 1e3, "hz": 1.0}[unit.group(1)]
    elif x < 10.0:
        x *= 1e9
    elif x < 100_000.0:
        x *= 1e6
    b = bands.band_at(x)
    if b is None:
        raise ValueError(f"{x / 1e6:.3f} MHz is not in 70cm, 23cm or 6cm")
    return float(round(x))


class Readout(QWidget):
    tune = pyqtSignal(float)            # receive Hz
    tune_tx = pyqtSignal(float)         # transmit Hz (split)
    step_clicked = pyqtSignal()
    split_clicked = pyqtSignal()

    def __init__(self, bridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.step = 100_000
        self.hover_place: int | None = None
        self.cells: list[tuple[QRectF, int | None]] = []
        self.chips: dict[str, QRectF] = {}
        self.tx_rect = QRectF()
        self.editing = "rx"
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(92)
        self.kbd_focus = False
        self.edit = QLineEdit(self)
        self.edit.hide()
        # The example in the locale's own decimals: parse_freq takes a comma too.
        self.edit.setPlaceholderText(self.tr("MHz, e.g. {example}").format(example=number(1255.25)))
        self.edit.returnPressed.connect(self._commit)
        self.edit.installEventFilter(self)
        bridge.changed.connect(self._changed)

    def sizeHint(self):
        return QSize(380, 92)

    def minimumSizeHint(self):
        return QSize(300, 92)

    def _changed(self, ch):
        if any(k in ch for k in ("rxFreq", "txFreq", "split", "keyed", "band")):
            self.update()

    def set_step(self, step: int):
        self.step = step
        self.update()

    # ----------------------------------------------------------- editing
    def open_editor(self, which: str = "rx"):
        self.editing = which
        big = self._big_px()
        self.edit.setFont(theme.font(big * 0.55, theme.QFont.Weight.DemiBold, mono=True))
        self.edit.setGeometry(0, 16, min(self.width(), 300), int(big + 8))
        key = "txFreq" if which == "tx" else "rxFreq"
        self.edit.setText(f"{float(self.bridge.st.get(key, 0)) / 1e6:.3f}")
        self.edit.selectAll()
        self.edit.show()
        self.edit.setFocus()

    def _commit(self):
        try:
            f = parse_freq(self.edit.text())
        except ValueError as e:
            self.bridge.say(str(e))
            self.edit.selectAll()
            return
        self.edit.hide()
        (self.tune_tx if self.editing == "tx" else self.tune).emit(f)
        self.setFocus()

    def eventFilter(self, obj, ev):
        if obj is self.edit and ev.type() == ev.Type.KeyPress and ev.key() == Qt.Key.Key_Escape:
            self.edit.hide()
            self.setFocus()
            return True
        if obj is self.edit and ev.type() == ev.Type.FocusOut:
            self.edit.hide()
        return False

    # ----------------------------------------------------------- mouse
    def _place_at(self, pos) -> int | None:
        for r, place in self.cells:
            if place is not None and r.adjusted(-1, 0, 1, 0).contains(pos):
                return place
        return None

    def _chip_at(self, pos) -> str | None:
        for k, r in self.chips.items():
            if r.contains(pos):
                return k
        return None

    def mouseMoveEvent(self, e):
        pl = self._place_at(e.position())
        if pl != self.hover_place:
            self.hover_place = pl
            self.update()
        self.setCursor(Qt.CursorShape.SizeVerCursor if pl is not None else
                       Qt.CursorShape.PointingHandCursor if (self._chip_at(e.position()) or
                                                             self.tx_rect.contains(e.position()))
                       else Qt.CursorShape.ArrowCursor)

    def leaveEvent(self, e):
        self.hover_place = None
        self.update()

    def wheelEvent(self, e):
        d = e.angleDelta().y()
        if not d:
            return
        if self._chip_at(e.position()) == "step":
            i = STEPS.index(self.step) if self.step in STEPS else 2
            self.set_step(STEPS[max(0, min(len(STEPS) - 1, i + (1 if d > 0 else -1)))])
            e.accept()
            return
        pl = self._place_at(e.position())
        step = 10 ** pl if pl is not None else self.step
        f = float(self.bridge.st.get("rxFreq", 0))
        self.tune.emit(f + (step if d > 0 else -step))
        e.accept()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        chip = self._chip_at(e.position())
        if chip == "step":
            self.step_clicked.emit()
        elif chip == "split":
            self.split_clicked.emit()
        elif self.tx_rect.contains(e.position()) and self.bridge.st.get("split"):
            self.open_editor("tx")
        elif e.position().y() < 16 + self._big_px() + 6:
            self.open_editor("rx")

    def focusInEvent(self, e):
        self.kbd_focus = e.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason)
        super().focusInEvent(e)

    def focusOutEvent(self, e):
        self.kbd_focus = False
        super().focusOutEvent(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.open_editor()
        else:
            super().keyPressEvent(e)

    # ----------------------------------------------------------- paint
    def _big_px(self) -> float:
        return max(30.0, min(50.0, self.height() * 0.52))

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        st = self.bridge.st
        f = float(st.get("rxFreq") or 0.0)
        keyed = bool(st.get("keyed"))
        split = bool(st.get("split"))
        big = self._big_px()
        p.setFont(theme.caps(10.5))
        p.setPen(theme.c("text3"))
        cap = self.tr("Receive · MHz")
        p.drawText(QRectF(2, 0, 300, 14), Qt.AlignmentFlag.AlignVCenter, cap)
        if keyed:
            fm = QFontMetricsF(p.font())
            p.setPen(theme.c("tx"))
            p.drawText(QRectF(2 + fm.horizontalAdvance(cap) + 12, 0, 200, 14), Qt.AlignmentFlag.AlignVCenter,
                       self.tr("● on air"))
        hz = int(round(f))
        mhz, rest = divmod(hz, 1_000_000)
        khz, hz3 = divmod(rest, 1000)
        f_big = theme.font(big, theme.QFont.Weight.Medium)
        f_small = theme.font(big * 0.56, theme.QFont.Weight.Medium)
        fm_b, fm_s = QFontMetricsF(f_big), QFontMetricsF(f_small)
        cw_b = max(fm_b.horizontalAdvance(d) for d in "0123456789") * 1.02
        cw_s = max(fm_s.horizontalAdvance(d) for d in "0123456789") * 1.02
        x = 0.0
        y_top = 16.0
        base = y_top + fm_b.ascent() * 0.98
        self.cells = []
        ms = str(mhz)
        groups = [(ms, 6 + len(ms) - 1, "bright"), (".", None, "dot"), (f"{khz:03d}", 5, "bright"),
                  (" ", None, "gap"), (f"{hz3:03d}", 2, "small")]
        for text, top_place, kind in groups:
            small = kind == "small"
            if kind == "gap":
                x += cw_b * 0.2
                continue
            if kind == "dot":
                p.setFont(f_big)
                p.setPen(theme.c("text2"))
                w = cw_b * 0.42
                p.drawText(QRectF(x, base - fm_b.ascent(), w, fm_b.height()), Qt.AlignmentFlag.AlignHCenter, ".")
                x += w
                continue
            p.setFont(f_small if small else f_big)
            fmx = fm_s if small else fm_b
            cw = cw_s if small else cw_b
            for i, ch in enumerate(text):
                place = top_place - i
                if place < 2:
                    place = None     # finer than 100 Hz means nothing for a TV carrier
                r = QRectF(x, y_top, cw, big)
                if place is not None and place == self.hover_place:
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(theme.c("accent", 0.14))
                    p.drawRoundedRect(QRectF(r.x() - 1, y_top - 2, r.width() + 2, big + 6), 4, 4)
                    p.setBrush(theme.c("accent"))
                    p.drawRoundedRect(QRectF(r.x() + 3, y_top + big + 2, r.width() - 6, 2.5), 1, 1)
                col = theme.c("text2") if small else theme.c("text")
                if keyed and not split and not small:
                    col = theme.c("tx")
                p.setPen(col)
                p.drawText(QRectF(r.x(), base - fmx.ascent(), cw, fmx.height()), Qt.AlignmentFlag.AlignHCenter, ch)
                self.cells.append((r, place))
                x += cw
        # Second row: transmit, the chips, the bandplan segment.
        y2 = y_top + big + 8
        p.setFont(theme.caps(10))
        p.setPen(theme.c("tx") if keyed else theme.c("text3"))
        p.drawText(QRectF(2, y2, 26, 20), Qt.AlignmentFlag.AlignVCenter, self.tr("TX"))
        p.setFont(theme.font(14, theme.QFont.Weight.Medium, mono=True))
        tf = float(st.get("txFreq") or f)
        tm, tr = divmod(int(round(tf)), 1_000_000)
        ttext = f"{tm}.{tr // 1000:03d} {tr % 1000:03d}" if split else "= RX"
        fmu = QFontMetricsF(p.font())
        p.setPen(theme.c("tx") if keyed else theme.c("text2") if split else theme.c("text3"))
        tw = fmu.horizontalAdvance(ttext)
        self.tx_rect = QRectF(26, y2, tw + 4, 20)
        p.drawText(self.tx_rect, Qt.AlignmentFlag.AlignVCenter, ttext)
        cx = 26 + tw + 14
        self.chips = {}
        p.setFont(theme.font(10.5, theme.QFont.Weight.Bold, spacing=0.6))
        fmc = QFontMetricsF(p.font())
        for key, text, on, col in (("split", "SPLIT", split, "warn"), ("step", "STEP " + step_label(self.step).upper(),
                                                                        True, "text2")):
            w = fmc.horizontalAdvance(text) + 14
            r = QRectF(cx, y2 + 1, w, 19)
            self.chips[key] = r
            if key == "step":
                p.setPen(QPen(theme.c("line2"), 1))
                p.setBrush(theme.c("bg2"))
                p.drawRoundedRect(r, 4, 4)
                p.setPen(theme.c("text2"))
            elif on:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(theme.c(col, 0.18))
                p.drawRoundedRect(r, 4, 4)
                p.setPen(theme.c(col))
            else:
                p.setPen(QPen(theme.c("line"), 1))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(r, 4, 4)
                p.setPen(theme.c("text3"))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
            cx += w + 6
        if self.hasFocus() and self.kbd_focus and not self.edit.isVisible():
            p.setPen(QPen(theme.c("accent", 0.5), 1, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 6, 6)
