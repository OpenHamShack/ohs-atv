"""The waveform monitor and the vectorscope - the two instruments a TV
engineer reads a picture with.

Both are phosphor displays: each decoded line (or each frame's U/V points)
is splatted into a float buffer that decays a little every frame, then
coloured through a lookup table into one premultiplied ARGB image and
blitted over a cached graticule. That is how the real instruments look - a
trace that is bright where it lingers and faint on fast edges - and it is
cheap: a few numpy operations per frame, nothing per pixel in Python.

Graticules are from the standard, not decoration: the waveform monitor in
volts (-0.3 V sync tip, 0 V blanking, 0.7 V peak white, 10 % lines between)
over the 64 us line; the vectorscope's target boxes are the 75 % EBU bars
computed by ohsatv.pal.rgb_to_yuv - the same arithmetic the encoder uses, so
a correct picture lands in the boxes by construction - and the PAL burst
axes at +-135 degrees.
"""
from __future__ import annotations

import math
import time

import numpy as np
from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetricsF, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QSizePolicy, QWidget

from .. import pal
from . import theme
from .controls import Anim, FocusVisible

V_LO, V_HI = -0.4, 0.8           # waveform monitor vertical range, volts
LINE_US = 64.0
VEC_FS = 0.45                    # vectorscope full scale (circle), volts: just above 100 % red

BARS_75 = (("YL", (0.75, 0.75, 0.0)), ("CY", (0.0, 0.75, 0.75)), ("G", (0.0, 0.75, 0.0)),
           ("MG", (0.75, 0.0, 0.75)), ("R", (0.75, 0.0, 0.0)), ("B", (0.0, 0.0, 0.75)))


def bar_targets() -> list[tuple[str, float, float]]:
    """(name, U, V) volts for the 75 % bars, from the encoder's own matrix."""
    out = []
    for name, rgb in BARS_75:
        _y, u, v = pal.rgb_to_yuv(np.array(rgb, np.float64))
        out.append((name, float(u), float(v)))
    return out


def phosphor_lut(hex_colour: str) -> np.ndarray:
    """256 premultiplied ARGB32 entries: transparent at 0, the phosphor colour
    in the middle, running to near-white where the trace is densest."""
    c = QColor(hex_colour)
    base = np.array([c.red(), c.green(), c.blue()], np.float64) / 255.0
    k = np.linspace(0.0, 1.0, 256)
    a = np.clip(k * 1.6, 0, 1) ** 0.8
    white = np.clip((k - 0.55) / 0.45, 0, 1) ** 1.5
    rgb = base[None, :] * (1 - white[:, None]) + np.array([0.92, 1.0, 0.95])[None, :] * white[:, None]
    rgb = rgb * a[:, None]
    q = np.clip(np.round(np.concatenate([a[:, None], rgb], axis=1) * 255), 0, 255).astype(np.uint32)
    return (q[:, 0] << 24) | (q[:, 1] << 16) | (q[:, 2] << 8) | q[:, 3]


class Phosphor:
    """A decaying float buffer and the ARGB image it is shown through."""

    def __init__(self, colour: str):
        self.lut = phosphor_lut(colour)
        self.w = self.h = 0
        self.buf = np.zeros((1, 1), np.float32)
        self.out = np.zeros((1, 1), np.uint32)
        self.img = None

    def resize(self, w: int, h: int):
        w, h = max(1, int(w)), max(1, int(h))
        if (w, h) != (self.w, self.h):
            self.w, self.h = w, h
            self.buf = np.zeros((h, w), np.float32)
            self.out = np.zeros((h, w), np.uint32)
            self.img = QImage(self.out.data, w, h, w * 4, QImage.Format.Format_ARGB32_Premultiplied)

    def decay(self, k: float):
        self.buf *= np.float32(k)

    def render(self, gain: float, log: bool = False) -> QImage:
        # Log for the vectorscope: a colour-bar patch puts thousands of points
        # on one pixel and a transition a handful, and both must be visible.
        x = np.log1p(self.buf) if log else self.buf
        idx = np.clip(x * np.float32(gain), 0, 255).astype(np.uint8)
        np.take(self.lut, idx, out=self.out)
        return self.img


class LineSelector(FocusVisible, QWidget):
    """'LINE 100' between two arrows: click an arrow or wheel to step (Shift:
    ten lines). Which field the line is in is printed beside it, because
    lines 1-312 and 313-625 are different fields of the same frame."""

    picked = pyqtSignal(int)

    def __init__(self, value: int = 100, parent=None):
        super().__init__(parent)
        self.v = int(value)
        self.hover = Anim(self)
        self.setFixedSize(150, 24)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(self.tr("Waveform monitor line: wheel or arrows (Shift: 10 lines)"))

    def sizeHint(self):
        return QSize(150, 24)

    def set_value(self, v: int):
        self.v = max(1, min(625, int(v)))
        self.update()

    def _step(self, d: int):
        self.set_value(self.v + d)
        self.picked.emit(self.v)

    def wheelEvent(self, e):
        d = e.angleDelta().y()
        if d:
            k = 10 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
            self._step(k if d > 0 else -k)
        e.accept()

    def mouseReleaseEvent(self, e):
        k = 10 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
        if e.position().x() < 26:
            self._step(-k)
        elif e.position().x() > self.width() - 26:
            self._step(k)

    def keyPressEvent(self, e):
        k = 10 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
        if e.key() in (Qt.Key.Key_Left, Qt.Key.Key_Down):
            self._step(-k)
        elif e.key() in (Qt.Key.Key_Right, Qt.Key.Key_Up):
            self._step(k)
        else:
            super().keyPressEvent(e)

    def enterEvent(self, e):
        self.hover.to(1)

    def leaveEvent(self, e):
        self.hover.to(0)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(theme.c("line2") if not self.focus_visible() else theme.c("accent"), 1))
        p.setBrush(theme.c("bg2"))
        p.drawRoundedRect(r, 6, 6)
        p.setPen(QPen(theme.c("text2"), 1.6, cap=Qt.PenCapStyle.RoundCap))
        cy = r.center().y()
        for x0, d in ((13, -1), (r.right() - 13, 1)):
            p.drawLine(QPointF(x0 - 2.5 * d, cy - 4.5), QPointF(x0 + 2.5 * d, cy))
            p.drawLine(QPointF(x0 + 2.5 * d, cy), QPointF(x0 - 2.5 * d, cy + 4.5))
        p.setFont(theme.font(11.5, theme.QFont.Weight.DemiBold, mono=True))
        p.setPen(theme.c("text"))
        fld = 1 if self.v <= 312 else 2
        p.drawText(QRectF(24, 0, self.width() - 48, self.height()), Qt.AlignmentFlag.AlignCenter,
                   self.tr("LINE {line} · F{field}").format(line=f"{self.v:3d}", field=fld))


class WaveformMonitor(QWidget):
    """One line of composite in volts against its line: 64 us, or 98.8 us
    for 405 lines (the graticule follows the line it is given)."""

    def __init__(self, bridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.ph = Phosphor(theme.DARK["phosphor"])
        self.line_us = LINE_US
        self.last_t = 0.0
        self.cost_paint = 0.0
        self.setMinimumSize(320, 160)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        bridge.scope.connect(self.on_items)
        bridge.tick.connect(self._tick)

    def plot_rect(self) -> QRectF:
        return QRectF(46, 10, max(10, self.width() - 46 - 44), max(10, self.height() - 10 - 24))

    def y_of(self, v):
        r = self.plot_rect()
        return r.bottom() - (v - V_LO) / (V_HI - V_LO) * r.height()

    def resizeEvent(self, e):
        r = self.plot_rect()
        dpr = self.devicePixelRatioF()
        self.ph.resize(r.width() * dpr, r.height() * dpr)
        super().resizeEvent(e)

    def on_items(self, items: list):
        ph = self.ph
        if ph.w < 2 or ph.h < 2:
            return
        for it in items:
            wave = np.asarray(it.get("wave"), np.float32)
            if wave.size < 8:
                continue
            fs = float(it.get("fs") or 0)
            line_us = wave.size / fs * 1e6 if fs else LINE_US
            if abs(line_us - self.line_us) > 1.0:
                self.line_us = line_us
                self._skey = None                   # the graticule is for the other standard
            ph.decay(0.55)
            w, h = ph.w, ph.h
            # Two points per column, then each column filled between the
            # trace's lowest and highest point in it - so an edge is a
            # continuous vertical stroke, dimmer the longer it is.
            n = 2 * w
            xs = np.linspace(0, wave.size - 1, n)
            v = np.interp(xs, np.arange(wave.size), wave)
            y = (V_HI - v) / (V_HI - V_LO) * (h - 1)
            y = np.clip(y, 0, h - 1).reshape(w, 2)
            ymin = y.min(axis=1)
            ymax = y.max(axis=1)
            # include the neighbour's end so steep edges join up
            ymin[1:] = np.minimum(ymin[1:], y[:-1, 1])
            ymax[1:] = np.maximum(ymax[1:], y[:-1, 1])
            lo = np.floor(ymin).astype(np.int32)
            hi = np.ceil(ymax).astype(np.int32)
            span = (hi - lo + 1).astype(np.float32)
            rows = np.arange(h, dtype=np.int32)[:, None]
            mask = (rows >= lo[None, :]) & (rows <= hi[None, :])
            ph.buf += mask * (1.0 / np.sqrt(span))[None, :]
            self.last_t = time.monotonic()
        self.update()

    def _tick(self, _dt):
        # No lines arriving: let the trace fade rather than freeze.
        if self.last_t and time.monotonic() - self.last_t > 0.3:
            self.ph.decay(0.7)
            if float(self.ph.buf.max()) < 0.02:
                self.last_t = 0.0
                self.ph.buf[:] = 0
            self.update()

    def _static(self) -> QPixmap:
        dpr = self.devicePixelRatioF()
        key = (self.width(), self.height(), dpr, theme.t()["name"])
        if getattr(self, "_skey", None) == key:
            return self._spm
        pm = QPixmap(max(1, int(self.width() * dpr)), max(1, int(self.height() * dpr)))
        pm.setDevicePixelRatio(dpr)
        pm.fill(theme.c("scope"))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.plot_rect()
        line_us = self.line_us
        xus = lambda us: r.x() + us / line_us * r.width()  # noqa: E731
        # Regions of the line: sync and burst (625), sync alone (405).
        lines405 = line_us > 80
        p.fillRect(QRectF(xus(0), r.y(), xus(9.0 if lines405 else 4.7) - xus(0), r.height()), QColor(255, 255, 255, 6))
        if not lines405:
            p.fillRect(QRectF(xus(5.6), r.y(), xus(7.85) - xus(5.6), r.height()), QColor(255, 143, 163, 10))
        grat = QColor(120, 200, 150, 34)
        strong = QColor(140, 220, 170, 85)
        p.setFont(theme.font(10, mono=True))
        lab = QColor(200, 215, 205, 150)
        for i in range(11):
            v = 0.07 * i
            y = self.y_of(v)
            major = i in (0, 10)
            p.setPen(QPen(strong if major else grat, 1, Qt.PenStyle.SolidLine if major or i == 5
                          else Qt.PenStyle.DotLine))
            p.drawLine(QPointF(r.x(), y), QPointF(r.right(), y))
        y = self.y_of(-0.3)
        p.setPen(QPen(strong, 1))
        p.drawLine(QPointF(r.x(), y), QPointF(r.right(), y))
        for v, left, right in ((0.7, "0.7", "100"), (0.35, ".35", "50"), (0.0, "0", "0"), (-0.3, "-0.3", "-43")):
            y = self.y_of(v)
            p.setPen(lab)
            p.drawText(QRectF(2, y - 8, r.x() - 8, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       left)
            p.drawText(QRectF(r.right() + 6, y - 8, 40, 16), Qt.AlignmentFlag.AlignLeft |
                       Qt.AlignmentFlag.AlignVCenter, right)
        p.setFont(theme.font(9, theme.QFont.Weight.DemiBold, spacing=0.5))
        p.setPen(QColor(200, 215, 205, 90))
        p.drawText(QRectF(2, r.y() - 8, r.x() - 8, 14), Qt.AlignmentFlag.AlignRight, "V")
        p.drawText(QRectF(r.right() + 6, r.y() - 8, 40, 14), Qt.AlignmentFlag.AlignLeft, "%")
        # Time: a full-height line every 8 us, ticks every microsecond on the
        # blanking level - the centre scale of a real graticule.
        y0 = self.y_of(0.0)
        for us in range(0, int(line_us) + 1):
            x = xus(us)
            if us % 8 == 0:
                p.setPen(QPen(grat, 1, Qt.PenStyle.DotLine))
                p.drawLine(QPointF(x, r.y()), QPointF(x, r.bottom()))
            h = 6 if us % 4 == 0 else 3
            p.setPen(QPen(strong, 1))
            p.drawLine(QPointF(x, y0 - h), QPointF(x, y0 + h))
        p.setPen(QPen(strong, 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(r)
        p.setFont(theme.font(10, mono=True))
        p.setPen(lab)
        for us in range(0, int(line_us) + 1, 8):
            x = xus(us)
            p.drawText(QRectF(x - 20, r.bottom() + 4, 40, 14), Qt.AlignmentFlag.AlignCenter, f"{us}")
        p.setFont(theme.font(9, theme.QFont.Weight.DemiBold, spacing=0.5))
        p.setPen(QColor(200, 215, 205, 90))
        p.drawText(QRectF(r.right() + 16, r.bottom() + 4, 30, 14), Qt.AlignmentFlag.AlignLeft, "µs")
        # Where the picture is (10.5-62.5 us; 405 lines 16.25-97.0), bracketed
        # under the top edge.
        p.setPen(QPen(QColor(140, 220, 170, 60), 1))
        a, b = (xus(16.25), xus(line_us - 1.75)) if lines405 else (xus(10.5), xus(62.5))
        p.drawLine(QPointF(a, r.y() + 3), QPointF(b, r.y() + 3))
        p.drawLine(QPointF(a, r.y()), QPointF(a, r.y() + 6))
        p.drawLine(QPointF(b, r.y()), QPointF(b, r.y() + 6))
        p.end()
        self._skey, self._spm = key, pm
        return pm

    def paintEvent(self, _e):
        t0 = time.perf_counter()
        p = QPainter(self)
        p.drawPixmap(0, 0, self._static())
        r = self.plot_rect()
        if self.last_t:
            p.drawImage(r, self.ph.render(150.0))
        else:
            p.setFont(theme.font(12))
            p.setPen(QColor(255, 255, 255, 90))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.tr("No line to show: the receiver is not locked"))
        p.end()
        self.cost_paint += ((time.perf_counter() - t0) * 1000.0 - self.cost_paint) * 0.1


class Vectorscope(QWidget):
    """V against U, with the 75 % bar targets and the PAL burst axes."""

    def __init__(self, bridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.ph = Phosphor("#7fe6f5")
        self.targets = bar_targets()
        self.mono = True
        self.last_t = 0.0
        self.cost_paint = 0.0
        self.setMinimumSize(180, 180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        bridge.scope.connect(self.on_items)
        bridge.tick.connect(self._tick)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return w

    def circle(self) -> tuple[QPointF, float]:
        side = min(self.width(), self.height()) - 44     # room for the target labels outside the circle
        return QPointF(self.width() / 2, self.height() / 2), max(10.0, side / 2)

    def resizeEvent(self, e):
        _c, rad = self.circle()
        dpr = self.devicePixelRatioF()
        self.ph.resize(2 * rad * dpr, 2 * rad * dpr)
        super().resizeEvent(e)

    def on_items(self, items: list):
        ph = self.ph
        if ph.w < 2:
            return
        for it in items:
            uv = it.get("uv")
            ph.decay(0.72)
            if uv is None:
                self.mono = True
                continue
            self.mono = False
            u, v = np.asarray(uv[0], np.float32), np.asarray(uv[1], np.float32)
            n = ph.w
            x = ((u / VEC_FS) * 0.5 + 0.5) * (n - 1)
            y = (0.5 - (v / VEC_FS) * 0.5) * (n - 1)
            ok = (x >= 0) & (x < n) & (y >= 0) & (y < n)
            idx = y[ok].astype(np.int32) * n + x[ok].astype(np.int32)
            ph.buf += np.bincount(idx, minlength=n * n).reshape(n, n).astype(np.float32)
            self.last_t = time.monotonic()
        self.update()

    def _tick(self, _dt):
        if self.last_t and time.monotonic() - self.last_t > 0.3:
            self.ph.decay(0.7)
            self.mono = True
            if float(self.ph.buf.max()) < 0.05:
                self.last_t = 0.0
                self.ph.buf[:] = 0
            self.update()

    def _pt(self, u, v) -> QPointF:
        c, rad = self.circle()
        return QPointF(c.x() + u / VEC_FS * rad, c.y() - v / VEC_FS * rad)

    def _static(self) -> QPixmap:
        dpr = self.devicePixelRatioF()
        key = (self.width(), self.height(), dpr, theme.t()["name"])
        if getattr(self, "_skey", None) == key:
            return self._spm
        pm = QPixmap(max(1, int(self.width() * dpr)), max(1, int(self.height() * dpr)))
        pm.setDevicePixelRatio(dpr)
        pm.fill(theme.c("scope"))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c, rad = self.circle()
        grat = QColor(127, 230, 245, 40)
        strong = QColor(127, 230, 245, 90)
        p.setPen(QPen(strong, 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(c, rad, rad)
        p.setPen(QPen(grat, 1, Qt.PenStyle.DotLine))
        p.drawEllipse(c, rad * 0.5, rad * 0.5)
        # Degree ticks round the circle every 10 degrees, longer every 30.
        p.setPen(QPen(strong, 1))
        for d in range(0, 360, 10):
            a = math.radians(d)
            k = 0.94 if d % 30 else 0.9
            p.drawLine(QPointF(c.x() + rad * math.cos(a), c.y() - rad * math.sin(a)),
                       QPointF(c.x() + rad * k * math.cos(a), c.y() - rad * k * math.sin(a)))
        # U and V axes with 0.05 V ticks.
        p.setPen(QPen(grat, 1))
        p.drawLine(QPointF(c.x() - rad, c.y()), QPointF(c.x() + rad, c.y()))
        p.drawLine(QPointF(c.x(), c.y() - rad), QPointF(c.x(), c.y() + rad))
        step = 0.05 / VEC_FS * rad
        for i in range(-8, 9):
            if i == 0:
                continue
            p.drawLine(QPointF(c.x() + i * step, c.y() - 3), QPointF(c.x() + i * step, c.y() + 3))
            p.drawLine(QPointF(c.x() - 3, c.y() + i * step), QPointF(c.x() + 3, c.y() + i * step))
        # PAL burst: 0.3 V p-p at +135 and -135 (225) degrees.
        burst = 0.15
        p.setPen(QPen(QColor(255, 143, 163, 150), 1.4))
        for d in (135, 225):
            a = math.radians(d)
            end = QPointF(c.x() + burst / VEC_FS * rad * math.cos(a), c.y() - burst / VEC_FS * rad * math.sin(a))
            p.drawLine(c, end)
            p.drawEllipse(end, 2.5, 2.5)
        # 75 % targets: a box +-5 % of the target's amplitude, labelled.
        p.setFont(theme.font(max(9.0, rad / 14), theme.QFont.Weight.Bold))
        fm = QFontMetricsF(p.font())
        for name, u, v in self.targets:
            pt = self._pt(u, v)
            box = max(7.0, rad * 0.07)
            p.setPen(QPen(QColor(230, 240, 250, 160), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(QRectF(pt.x() - box, pt.y() - box, 2 * box, 2 * box))
            p.setPen(QPen(QColor(230, 240, 250, 90), 1))
            p.drawLine(QPointF(pt.x() - box / 2, pt.y()), QPointF(pt.x() + box / 2, pt.y()))
            p.drawLine(QPointF(pt.x(), pt.y() - box / 2), QPointF(pt.x(), pt.y() + box / 2))
            mag = math.hypot(u, v)
            lx = c.x() + (u / mag) * (mag / VEC_FS * rad + box + fm.horizontalAdvance(name) * 0.7 + 4)
            ly = c.y() - (v / mag) * (mag / VEC_FS * rad + box + 10)
            p.setPen(QColor(230, 240, 250, 170))
            p.drawText(QRectF(lx - 15, ly - 8, 30, 16), Qt.AlignmentFlag.AlignCenter, name)
        p.setFont(theme.font(9.5, theme.QFont.Weight.DemiBold, spacing=0.6))
        p.setPen(QColor(200, 215, 225, 100))
        p.drawText(QRectF(c.x() + rad - 30, c.y() + 2, 30, 14), Qt.AlignmentFlag.AlignRight, "U")
        p.drawText(QRectF(c.x() + 4, c.y() - rad, 30, 14), Qt.AlignmentFlag.AlignLeft, "V")
        p.end()
        self._skey, self._spm = key, pm
        return pm

    def paintEvent(self, _e):
        t0 = time.perf_counter()
        p = QPainter(self)
        p.drawPixmap(0, 0, self._static())
        c, rad = self.circle()
        if self.last_t:
            p.drawImage(QRectF(c.x() - rad, c.y() - rad, 2 * rad, 2 * rad), self.ph.render(48.0, log=True))
        if self.mono:
            p.setFont(theme.font(max(14.0, rad / 6), theme.QFont.Weight.Bold, spacing=3))
            p.setPen(QColor(230, 240, 250, 120))
            p.drawText(QRectF(c.x() - rad, c.y() + rad * 0.32, 2 * rad, rad * 0.3), Qt.AlignmentFlag.AlignCenter,
                       self.tr("MONO"))
        p.end()
        self.cost_paint += ((time.perf_counter() - t0) * 1000.0 - self.cost_paint) * 0.1
