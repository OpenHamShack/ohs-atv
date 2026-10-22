"""Spectrum and waterfall of the receive band, with the bandplan above it.

Copied from OpenHamShack NB's panadapter (scope.py) and cut to what an ATV
receiver needs. Top to bottom: the bandplan strip (segments coloured by what
the transmit guard does there, channel ticks you can click), the spectrum,
a thin MHz axis, the waterfall.

The span is whatever the engine sends: the whole sample band (10-20 MHz)
around the LO, in true RF Hz (docs/ENGINE-API.md "Feeds"). Over it go the
plan's own numbers - the occupied bandwidth as a translucent band around the
carrier (plan["occupied"] are offsets from the carrier), the carrier, the
sound carrier (AM: plan["sound"] is relative to the LO, which sits
-plan["carrier"] from the vision carrier), the colour subcarrier (AM) or the
sound subcarrier's sidebands (FM) - so the picture of the signal and the
statement of what fits are the same picture.

Everything except the waterfall history and the trace is painted once into
a cached pixmap and blitted (NB's `_static`): grid, axis and strip change
only when the view or the levels do.

Keyed on a half-duplex radio, receive pauses. The view then shows what is
being sent - the engine's txspectrum feed, the spectrum of the samples it
hands the radio - in the transmit red, on its own dB scale, under a TX
label; the waterfall keeps scrolling with those lines in grey between red
edges, so the history still says afterwards which rows were us. It used to
freeze on the last received trace, which looks like a quiet live band. The
receive trace, levels and scale are left as they were and carry on at unkey.
"""
from __future__ import annotations

import math
import time

import numpy as np
from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetricsF, QImage, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PyQt6.QtWidgets import QToolTip, QWidget

from .. import bands, pal
from ..i18n.fmt import number
from . import palettes, theme
from .scopecore import FreqMap, Waterfall, resample

NICE_STEPS = [0.1e6, 0.2e6, 0.25e6, 0.5e6, 1e6, 2e6, 2.5e6, 5e6, 10e6, 20e6]
SNAP_PX = 12           # a click this close to a channel tick tunes to the channel
TX_RANGE_DB = 70.0     # the transmitted spectrum's scale, down from its peak
TX_EDGE_PX = 4         # the red edges marking a waterfall row as transmitted, device pixels


def tx_lut() -> np.ndarray:
    """Grey ramp for transmitted rows: no palette colour, so they are never
    read as received signal, however the palette is set."""
    v = np.linspace(12, 235, 256).astype(np.uint32)
    return np.uint32(0xFF000000) | (v << 16) | (v << 8) | v


def _poly(n: int) -> tuple[QPolygonF, np.ndarray]:
    """A QPolygonF of n points and a numpy (n, 2) view writing straight into
    it, so a 1000-point trace costs one array assignment, not 1000 QPointFs."""
    poly = QPolygonF([QPointF(0, 0)] * n)
    ptr = poly.data()
    ptr.setsize(n * 16)
    return poly, np.frombuffer(ptr, np.float64).reshape(n, 2)


def mhz_label(f: float, step: float) -> str:
    """Axis labels in MHz with as many decimals as the step needs."""
    places = 0 if step % 1e6 == 0 else 1 if step % 1e5 == 0 else 2 if step % 1e4 == 0 else 3
    return f"{f / 1e6:.{places}f}"


class SpectrumView(QWidget):
    tune = pyqtSignal(float)                 # Hz: a channel, or a click rounded to the readout's step
    wheel_step = pyqtSignal(int)

    def __init__(self, bridge, prefs, parent=None):
        super().__init__(parent)
        self.bridge, self.prefs = bridge, prefs
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setMinimumHeight(180)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.fmap = FreqMap(1245e6, 1265e6, 100)
        self.have_view = False
        self.wf = Waterfall(100, 100)
        self.wf.set_lut(palettes.lut(prefs["palette"]))
        self.avg: np.ndarray | None = None
        self.peak: np.ndarray | None = None
        self.floor = None
        self.range = 40.0
        self.db_lo, self.db_hi = -110.0, -20.0
        self.n_lines = 0
        self.poly_n = 0
        self.hover: QPointF | None = None
        self.cost_ingest = 0.0
        self.cost_paint = 0.0
        self.snap_step = 250_000.0
        self.tx_avg: np.ndarray | None = None  # the transmitted spectrum, while it is shown
        self.tx_db = (-TX_RANGE_DB, 0.0)
        self.rx_db = (self.db_lo, self.db_hi)
        self.tx_label = QRectF()             # where the TX / RECEIVE PAUSED label was drawn (tests read it)
        self._tx_lut = tx_lut()
        self._tx_red = np.uint32(theme.sc("tx").rgba())
        self._layout()
        bridge.spectrum.connect(self.on_lines)
        bridge.txspectrum.connect(self.on_tx_lines)
        bridge.changed.connect(self._on_state)

    def set_palette(self, name: str):
        self.wf.set_lut(palettes.lut(name))
        self.update()

    def _st(self, k, d=None):
        return self.bridge.st.get(k, d)

    def _on_state(self, ch: dict):
        if ("keyed" in ch or "duplex" in ch) and not self.rx_paused():
            self._end_tx()
        if any(k in ch for k in ("rxFreq", "txFreq", "plan", "rxPlan", "keyed", "band", "system", "running", "error")):
            self.update()

    def rx_paused(self) -> bool:
        st = self.bridge.st
        return bool(st.get("keyed")) and not st.get("duplex")

    def showing_tx(self) -> bool:
        return self.tx_avg is not None and self.rx_paused()

    def _end_tx(self):
        """Back to receive: the receive scale as it was, the TX trace gone."""
        if self.tx_avg is None:
            return
        self.tx_avg = None
        self.db_lo, self.db_hi = self.rx_db
        self.update()

    # ----------------------------------------------------------- geometry
    def _layout(self):
        w, h = self.width(), self.height()
        strip, axis = 40, 20
        spec_h = max(60, int((h - strip - axis) * 0.52))
        self.r_strip = QRectF(0, 0, w, strip)
        self.r_spec = QRectF(0, strip, w, spec_h)
        self.r_axis = QRectF(0, strip + spec_h, w, axis)
        self.r_wf = QRectF(0, strip + spec_h + axis, w, max(1, h - strip - spec_h - axis))
        dpr = self.devicePixelRatioF()
        self.wf.resize(max(1, int(w * dpr)), max(1, int(self.r_wf.height() * dpr)))
        self.fmap.set(self.fmap.lo, self.fmap.hi, w, 0)
        n = max(2, int(w))
        if self.avg is not None and len(self.avg) != n:
            x_old = np.linspace(0, 1, len(self.avg))
            x_new = np.linspace(0, 1, n)
            self.avg = np.interp(x_new, x_old, self.avg).astype(np.float32)
            self.peak = np.interp(x_new, x_old, self.peak).astype(np.float32)
        if n != self.poly_n:
            self.poly_n = n
            self.trace_poly, self.trace_xy = _poly(n)
            self.peak_poly, self.peak_xy = _poly(n)

    def resizeEvent(self, e):
        self._layout()
        super().resizeEvent(e)

    # ----------------------------------------------------------- data
    def on_lines(self, lines: list):
        t0 = time.perf_counter()
        for (_t, start, bin_hz, db) in lines:
            lo, hi = float(start), float(start + bin_hz * len(db))
            if not self.have_view or abs(lo - self.fmap.lo) > 1 or abs(hi - self.fmap.hi) > 1:
                # Retuned (or a new rate): keep the history where it still
                # applies rather than smearing old frequencies across new ones.
                if self.have_view:
                    self.wf.remap(self.fmap.lo, self.fmap.hi, lo, hi)
                    self._remap_trace(self.fmap.lo, self.fmap.hi, lo, hi)
                self.fmap.set(lo, hi, self.width(), 0)
                self.have_view = True
            fill = float(np.min(db)) if len(db) else -150.0
            n_wf, n_tr = self.wf.w, self.poly_n
            cols = resample(start, bin_hz, db, lo, hi, n_wf, fill)
            tr = cols if n_wf == n_tr else resample(start, bin_hz, db, lo, hi, n_tr, fill)
            self._levels(cols)
            if self.avg is None or len(self.avg) != n_tr:
                self.avg = tr.copy()
                self.peak = tr.copy()
            else:
                self.avg += (tr - self.avg) * 0.35
                self.peak = np.maximum(self.avg, self.peak - 0.4)
            if self.n_lines <= 3:
                continue
            fl, rg = self._wf_levels()
            self.wf.push_db(cols, fl, rg)
        self.update()
        ms = (time.perf_counter() - t0) * 1000.0 / max(1, len(lines))
        self.cost_ingest += (ms - self.cost_ingest) * 0.1

    def on_tx_lines(self, lines: list):
        """The spectrum of what is being sent. Only while receive is paused:
        a line that arrives after unkey (the feed is drained a tick late)
        must not put TX back on a receiving display."""
        if not self.rx_paused() or not self.have_view:
            return
        lo, hi = self.fmap.lo, self.fmap.hi
        for (_t, start, bin_hz, db) in lines:
            # Onto the receive view's span: split, the carrier sent can sit
            # elsewhere in the band (or off this view entirely), and it is
            # drawn where it is.
            fill = float(np.min(db)) if len(db) else -150.0
            n_wf, n_tr = self.wf.w, self.poly_n
            cols = resample(start, bin_hz, db, lo, hi, n_wf, fill)
            tr = cols if n_wf == n_tr else resample(start, bin_hz, db, lo, hi, n_tr, fill)
            top = float(np.max(tr)) + 6.0
            if self.tx_avg is None or len(self.tx_avg) != n_tr:
                self.tx_avg = tr.copy()
                self.tx_db = (round(top) - TX_RANGE_DB, round(top))
            else:
                self.tx_avg += (tr - self.tx_avg) * 0.35
            self.db_lo, self.db_hi = self.tx_db
            fl, rg = self.tx_db[0] + 10.0, TX_RANGE_DB - 16.0
            idx = np.clip((cols - fl) * (255.0 / rg), 0, 255).astype(np.uint8)
            row = self._tx_lut[idx]
            e = min(TX_EDGE_PX, len(row) // 4)
            row[:e] = self._tx_red
            row[len(row) - e:] = self._tx_red
            self.wf.push_row(row)
        self.update()

    def _remap_trace(self, old_lo, old_hi, lo, hi):
        for name in ("avg", "peak"):
            a = getattr(self, name)
            if a is None:
                continue
            n = len(a)
            fc = lo + (np.arange(n) + 0.5) * (hi - lo) / n
            src = np.floor((fc - old_lo) / (old_hi - old_lo) * n).astype(np.int64)
            ok = (src >= 0) & (src < n)
            b = np.full(n, self.floor if self.floor is not None else -150.0, np.float32)
            b[ok] = a[src[ok]]
            setattr(self, name, b)

    def _levels(self, cols: np.ndarray):
        lo_est = float(np.percentile(cols, 20))
        hi_est = float(np.percentile(cols, 99.5))
        self.n_lines += 1
        if self.floor is None:
            self.floor = lo_est
            self.range = max(25.0, min(70.0, hi_est - lo_est + 6))
            self._dbl, self._dbh = self.floor - 10, self.floor + self.range + 10
        else:
            self.floor += (lo_est - self.floor) * max(0.03, 1.0 / self.n_lines)
            self.range += (max(25.0, min(70.0, hi_est - self.floor + 6)) - self.range) * 0.02
            want_lo, want_hi = self.floor - 10, self.floor + max(self.range, 30) + 10
            self._dbl += (want_lo - self._dbl) * 0.03
            self._dbh += (want_hi - self._dbh) * 0.03
        # Whole dB, so the cached grid layer stays valid for seconds at a time.
        self.rx_db = (float(round(self._dbl)), float(round(self._dbh)))
        if not self.showing_tx():
            self.db_lo, self.db_hi = self.rx_db

    def _wf_levels(self) -> tuple[float, float]:
        if self.prefs.get("wfAuto", True) or self.floor is None:
            return (self.floor if self.floor is not None else -100.0) + 3.0, self.range
        return float(self.prefs["wfFloor"]), float(self.prefs["wfRange"])

    def adopt(self, other: "SpectrumView"):
        """Carry another view's history over (the window rebuilds its content
        on a theme change): the waterfall, the levels and the trace, so the
        new view is full at once instead of filling for ten seconds."""
        self.wf.lut = other.wf.lut
        old = other.wf.ordered()
        self.wf.buf = old.copy()
        self.wf.w, self.wf.h, self.wf.head = old.shape[1], old.shape[0], 0
        self.wf.img = QImage(self.wf.buf.data, self.wf.w, self.wf.h, self.wf.w * 4, QImage.Format.Format_RGB32)
        self.wf.rows_total = other.wf.rows_total
        self.fmap.set(other.fmap.lo, other.fmap.hi, self.width(), 0)
        self.have_view = other.have_view
        self.floor, self.range, self.n_lines = other.floor, other.range, other.n_lines
        self.db_lo, self.db_hi = other.db_lo, other.db_hi
        self.rx_db, self.tx_db = other.rx_db, other.tx_db
        self.tx_avg = None if other.tx_avg is None else other.tx_avg.copy()
        self._dbl, self._dbh = getattr(other, "_dbl", other.db_lo), getattr(other, "_dbh", other.db_hi)
        self.avg = None if other.avg is None else other.avg.copy()
        self.peak = None if other.peak is None else other.peak.copy()
        self._layout()

    # ----------------------------------------------------------- the signal, from the plan
    def marks(self) -> dict:
        """Where the plan says things are, in RF Hz, for the carrier we receive
        (and, keyed, the one we send)."""
        st = self.bridge.st
        # Receive marks from the format being decoded (formatAuto may have
        # found another than ours on the air), the transmit band from ours.
        pl = st.get("rxPlan") or st.get("plan") or {}
        f = float(st.get("rxFreq") or 0.0)
        occ = pl.get("occupied") or [0.0, 0.0]
        out = {"carrier": f, "occ": (f + occ[0], f + occ[1]), "sound": [], "chroma": None}
        if st.get("system") == "am":
            if pl.get("sound") is not None:
                out["sound"] = [f - float(pl.get("carrier", 0.0)) + float(pl["sound"])]
            if pl.get("colour"):
                out["chroma"] = f + pal.FSC
        elif pl.get("sub"):
            out["sound"] = [f - float(pl["sub"]), f + float(pl["sub"])]
        # NICAM where it is heard, or sent (6.552 MHz above vision; on FM a
        # subcarrier, so either side of the carrier).
        out["nicam"] = []
        if st.get("nicamLock") or (pl.get("nicam") is not None):
            out["nicam"] = [f + 6.552e6] if st.get("system") == "am" else [f - 6.552e6, f + 6.552e6]
        tf = float(st.get("txFreq") or f)
        tocc = (st.get("plan") or {}).get("occupied") or occ
        out["tx"] = (tf, (tf + tocc[0], tf + tocc[1]))
        return out

    # ----------------------------------------------------------- mouse
    def _channels(self):
        b = bands.BY_NAME.get(self._st("band", "23cm"))
        return b.channels if b else ()

    def channel_at(self, x: float):
        best, bd = None, SNAP_PX + 1
        for ch in self._channels():
            d = abs(self.fmap.x(ch.freq) - x)
            if d < bd:
                best, bd = ch, d
        return best

    def mouseMoveEvent(self, e):
        self.hover = e.position()
        ch = self.channel_at(self.hover.x())
        if self.r_strip.contains(self.hover):
            f = self.fmap.f(self.hover.x())
            seg = bands.segment_at(f)
            tip = ""
            if ch is not None:
                tip = (f"<b>{ch.freq / 1e6:g} MHz</b> — {ch.label}<br>"
                       f"{'A home station may transmit here' if ch.tx else 'Receive only'} · click to tune")
            elif seg is not None:
                tip = f"<b>{seg.label}</b><br>{seg.lo / 1e6:g}–{seg.hi / 1e6:g} MHz"
            if tip:
                QToolTip.showText(e.globalPosition().toPoint(), tip, self)
            else:
                QToolTip.hideText()
        else:
            QToolTip.hideText()
        self.setCursor(Qt.CursorShape.PointingHandCursor if ch is not None else Qt.CursorShape.CrossCursor)
        self.update()

    def leaveEvent(self, e):
        self.hover = None
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or not self.have_view:
            return
        x = e.position().x()
        ch = self.channel_at(x)
        if ch is not None:
            self.tune.emit(float(ch.freq))
            return
        step = self.snap_step
        self.tune.emit(float(round(self.fmap.f(x) / step) * step))

    def wheelEvent(self, e):
        d = e.angleDelta().y()
        if d:
            self.wheel_step.emit(1 if d > 0 else -1)
        e.accept()

    # ----------------------------------------------------------- painting
    def y_db(self, db):
        r = self.r_spec
        return r.bottom() - (db - self.db_lo) / max(1.0, self.db_hi - self.db_lo) * (r.height() - 6)

    def paintEvent(self, _e):
        t0 = time.perf_counter()
        p = QPainter(self)
        p.drawPixmap(0, 0, self._static())
        m = self.marks()
        self._paint_occupied(p, m)
        self._paint_trace(p)
        self.wf.draw(p, self.r_wf)
        self._paint_marks(p, m)
        self._paint_hover(p)
        self._paint_tx_label(p)
        p.setPen(QPen(theme.sc("line"), 1))
        p.drawLine(QPointF(0, self.r_wf.y()), QPointF(self.width(), self.r_wf.y()))
        p.end()
        ms = (time.perf_counter() - t0) * 1000.0
        self.cost_paint += (ms - self.cost_paint) * 0.1

    def _static(self) -> QPixmap:
        dpr = self.devicePixelRatioF()
        key = (self.width(), self.height(), dpr, round(self.fmap.lo), round(self.fmap.hi), self.db_lo, self.db_hi,
               theme.t()["name"], self._st("band"), round(float(self._st("rxFreq") or 0)),
               str((self._st("bandLicence") or {}).get("spans")))
        if getattr(self, "_static_key", None) != key:
            pm = QPixmap(max(1, int(self.width() * dpr)), max(1, int(self.height() * dpr)))
            pm.setDevicePixelRatio(dpr)
            pm.fill(theme.c("scope"))
            q = QPainter(pm)
            q.setRenderHint(QPainter.RenderHint.Antialiasing)
            self._paint_grid(q)
            self._paint_axis(q)
            self._paint_strip(q)
            q.end()
            self._static_pm, self._static_key = pm, key
        return self._static_pm

    def _ticks(self, min_px: float = 80.0):
        span = self.fmap.span
        step = next((s for s in NICE_STEPS if s / span * self.width() >= min_px), NICE_STEPS[-1])
        f = math.ceil(self.fmap.lo / step) * step
        out = []
        while f <= self.fmap.hi:
            out.append(f)
            f += step
        return step, out

    def _paint_grid(self, p: QPainter):
        r = self.r_spec
        p.setPen(QPen(QColor("#141a23"), 1))
        _, ticks = self._ticks()
        for f in ticks:
            x = round(self.fmap.x(f)) + 0.5
            p.drawLine(QPointF(x, r.y()), QPointF(x, r.bottom()))
        dstep = 10 if r.height() / max(1.0, (self.db_hi - self.db_lo) / 10) >= 18 else 20
        db = math.floor(self.db_hi / dstep) * dstep
        p.setFont(theme.font(10.5, mono=True))
        while db > self.db_lo:
            y = round(self.y_db(db)) + 0.5
            p.setPen(QPen(QColor("#141a23"), 1))
            p.drawLine(QPointF(0, y), QPointF(r.right(), y))
            p.setPen(QColor(255, 255, 255, 70))
            if r.y() + 14 < y < r.bottom() - 4:
                p.drawText(QRectF(r.right() - 46, y - 13, 42, 12), Qt.AlignmentFlag.AlignRight, f"{db:.0f}")
            db -= dstep

    def _paint_axis(self, p: QPainter):
        r = self.r_axis
        p.fillRect(r, QColor(10, 13, 19))
        p.setPen(QPen(QColor(255, 255, 255, 18), 1))
        p.drawLine(QPointF(0, r.y() + 0.5), QPointF(self.width(), r.y() + 0.5))
        step, ticks = self._ticks()
        p.setFont(theme.font(10.5, mono=True))
        fm = QFontMetricsF(p.font())
        for f in ticks:
            x = self.fmap.x(f)
            p.setPen(QPen(QColor(255, 255, 255, 60), 1))
            p.drawLine(QPointF(round(x) + 0.5, r.y()), QPointF(round(x) + 0.5, r.y() + 4))
            lab = mhz_label(f, step)
            w = fm.horizontalAdvance(lab)
            if x - w / 2 < 44 or x + w / 2 > self.width() - 2:
                continue
            p.setPen(QColor(200, 210, 225, 170))
            p.drawText(QRectF(x - w / 2 - 2, r.y() + 3, w + 4, r.height() - 3), Qt.AlignmentFlag.AlignCenter, lab)
        p.setFont(theme.font(9.5, theme.QFont.Weight.DemiBold, spacing=0.6))
        p.setPen(QColor(200, 210, 225, 110))
        p.drawText(QRectF(6, r.y(), 40, r.height()), Qt.AlignmentFlag.AlignVCenter, "MHz")

    def _paint_strip(self, p: QPainter):
        """Segments (a bar coloured by kind, labelled where it fits), the gaps
        outside the station's licence hatched, and channel ticks beneath."""
        r = self.r_strip
        p.fillRect(r, QColor(12, 15, 21))
        b = bands.BY_NAME.get(self._st("band", "23cm"))
        if b is None or not self.have_view:
            return
        bar = QRectF(0, r.y() + 3, self.width(), 17)
        # Outside what the station's licence class may use (the engine's
        # bandLicence, from the shared dataset): hatched - not ours.
        hatch = QColor(255, 255, 255, 14)
        p.save()
        p.setClipRect(bar)
        spans = (self._st("bandLicence") or {}).get("spans") or [] if b in bands.BANDS else []
        if spans:
            edges = [(-1e12, spans[0][0])] + [(spans[i][1], spans[i + 1][0])
                                              for i in range(len(spans) - 1)] + [(spans[-1][1], 1e12)]
        else:
            edges = [(-1e12, 1e12)]         # the cable feed, or nothing in the licence: none of it is ours to radiate in
        for lo, hi in edges:
            x0, x1 = max(-5.0, self.fmap.x(lo)), min(self.width() + 5.0, self.fmap.x(hi))
            if x1 <= x0:
                continue
            p.fillRect(QRectF(x0, bar.y(), x1 - x0, bar.height()), QColor(8, 10, 14))
            p.setPen(QPen(hatch, 1))
            k = x0 - bar.height()
            while k < x1:
                p.drawLine(QPointF(k, bar.bottom()), QPointF(k + bar.height(), bar.y()))
                k += 6
        p.restore()
        p.setFont(theme.font(10, theme.QFont.Weight.Bold, spacing=0.6))
        fm = QFontMetricsF(p.font())
        for s in b.segments:
            x0, x1 = self.fmap.x(s.lo), self.fmap.x(s.hi)
            if x1 < 0 or x0 > self.width():
                continue
            col = theme.sc(theme.segment_colour_key(s.kind))
            box = QRectF(max(0.0, x0) + 0.5, bar.y(), max(1.0, min(self.width(), x1) - max(0.0, x0) - 1), bar.height())
            fillc = QColor(col)
            fillc.setAlphaF(0.26)
            p.fillRect(box, fillc)
            p.fillRect(QRectF(box.x(), box.bottom() - 2, box.width(), 2), col)
            text = s.label.upper()
            if fm.horizontalAdvance(text) + 12 > box.width():
                text = fm.elidedText(text, Qt.TextElideMode.ElideRight, box.width() - 12)
            if len(text) > 3 and fm.horizontalAdvance(text) + 6 < box.width():
                p.setPen(QColor(col).lighter(150))
                p.drawText(box.adjusted(0, -1, 0, -1), Qt.AlignmentFlag.AlignCenter, text)
        # Channels: a tick and the frequency, the one we are on highlighted.
        # Ticks first, then labels where they clear every tick and label -
        # 1248 and 1249 are 1 MHz apart, which is 30 px at 1366 wide.
        f_rx = float(self._st("rxFreq") or 0)
        p.setFont(theme.font(10, theme.QFont.Weight.DemiBold, mono=True))
        fm = QFontMetricsF(p.font())
        y = bar.bottom() + 2
        shown = [ch for ch in b.channels if -2 <= self.fmap.x(ch.freq) <= self.width() + 2]
        placed: list[QRectF] = [QRectF(self.fmap.x(ch.freq) - 5, y, 10, 6) for ch in shown]
        for k, ch in enumerate(shown):
            x = self.fmap.x(ch.freq)
            on = abs(ch.freq - f_rx) < 1.0
            col = theme.sc("rx") if on else (theme.sc("text2") if ch.tx else theme.sc("text3"))
            tri = QPainterPath()
            tri.moveTo(x - 4.5, y)
            tri.lineTo(x + 4.5, y)
            tri.lineTo(x, y + 5)
            tri.closeSubpath()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawPath(tri)
            lab = f"{ch.freq / 1e6:g}"
            w = fm.horizontalAdvance(lab) + 4
            own = placed[k]
            for box in (QRectF(x + 6, y - 1, w, 14), QRectF(x - 6 - w, y - 1, w, 14)):
                if box.x() < 0 or box.right() > self.width() - 2:
                    continue
                if any(box.intersects(q) for q in placed if q is not own):
                    continue
                placed.append(box)
                p.setPen(col)
                p.drawText(box, Qt.AlignmentFlag.AlignVCenter, lab)
                break

    def _paint_occupied(self, p: QPainter, m: dict):
        """The plan's occupied bandwidth around our receive carrier (and, when
        keyed on another frequency, around the transmit carrier)."""
        top, bot = self.r_spec.y(), self.r_spec.bottom()
        lo, hi = m["occ"]
        xl, xh = self.fmap.x(lo), self.fmap.x(hi)
        p.fillRect(QRectF(xl, top, xh - xl, bot - top), theme.sc("rx", 0.07))
        p.fillRect(QRectF(xl, self.r_wf.y(), xh - xl, self.r_wf.height()), theme.sc("rx", 0.03))
        if self._st("keyed"):
            _tf, (tlo, thi) = m["tx"]
            a, b = self.fmap.x(tlo), self.fmap.x(thi)
            p.fillRect(QRectF(a, top, b - a, bot - top), theme.sc("tx", 0.10))

    def _paint_tx_label(self, p: QPainter):
        """Whenever receive is paused, say so over the spectrum - in red, and
        in words, since a red trace alone is a colour to be told apart."""
        self.tx_label = QRectF()
        if not self.rx_paused():
            return
        text = ("TX · THE SIGNAL BEING SENT · RECEIVE PAUSED" if self.showing_tx()
                else "RECEIVE PAUSED · TRANSMITTING")
        f = theme.font(10.5, theme.QFont.Weight.Bold, spacing=0.8)
        fm = QFontMetricsF(f)
        text = fm.elidedText(text, Qt.TextElideMode.ElideRight, self.width() - 80)
        r = QRectF(10, self.r_spec.y() + 6, fm.horizontalAdvance(text) + 20, fm.height() + 8)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.sc("tx", 0.9))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setFont(f)
        p.setPen(QColor("#ffffff"))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        self.tx_label = r

    def _paint_trace(self, p: QPainter):
        if self.showing_tx():
            self._paint_tx_trace(p)
            return
        if self.avg is None or self.poly_n < 2 or len(self.avg) != self.poly_n:
            p.setFont(theme.font(12))
            p.setPen(QColor(255, 255, 255, 90))
            msg = "Waiting for the radio…" if not self._st("running") else "Waiting for spectrum…"
            if self._st("error"):
                msg = "Receiver stopped: " + self._st("error")
            p.drawText(self.r_spec.adjusted(20, 0, -20, 0), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                       msg)
            return
        n = self.poly_n
        xs = np.arange(n, dtype=np.float64) * (self.width() / max(1, n - 1))
        a = self.avg.astype(np.float64)
        a[1:-1] = 0.25 * a[:-2] + 0.5 * a[1:-1] + 0.25 * a[2:]
        ys = np.clip(self.y_db(a), self.r_spec.y() + 1, self.r_spec.bottom())
        p.drawImage(QRectF(0, self.r_spec.y(), self.width(), self.r_spec.height()), self._fill_image(ys))
        self.trace_xy[:, 0] = xs
        self.trace_xy[:, 1] = ys
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.peak is not None and len(self.peak) == n:
            self.peak_xy[:, 0] = xs
            self.peak_xy[:, 1] = np.clip(self.y_db(self.peak.astype(np.float64)), self.r_spec.y() + 1,
                                         self.r_spec.bottom())
            p.setPen(QPen(QColor(150, 170, 200, 60), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPolyline(self.peak_poly)
        # Width exactly 1: Qt's fast antialiased-line path (NB measured 1.25 at ~50 ms).
        p.setPen(QPen(QColor(214, 238, 255, 235), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolyline(self.trace_poly)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)

    def _paint_tx_trace(self, p: QPainter):
        """The transmitted spectrum: one red line, no fill and no peak hold -
        nothing shared with the receive trace's look."""
        n = self.poly_n
        if self.tx_avg is None or len(self.tx_avg) != n or n < 2:
            return
        xs = np.arange(n, dtype=np.float64) * (self.width() / max(1, n - 1))
        self.trace_xy[:, 0] = xs
        self.trace_xy[:, 1] = np.clip(self.y_db(self.tx_avg.astype(np.float64)), self.r_spec.y() + 1,
                                      self.r_spec.bottom())
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(theme.sc("tx"), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolyline(self.trace_poly)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)

    def _fill_image(self, ys: np.ndarray) -> QImage:
        """The gradient under the trace, built in numpy (NB: QPainter's polygon
        fill of a noisy trace cost 12-15 ms a frame; this costs under one)."""
        w, h = self.poly_n, max(1, int(self.r_spec.height()))
        if getattr(self, "_fill_wh", None) != (w, h):
            self._fill_wh = (w, h)
            self._fill_buf = np.zeros((h, w), np.uint32)
            self._fill_img = QImage(self._fill_buf.data, w, h, w * 4, QImage.Format.Format_ARGB32_Premultiplied)
            k = np.linspace(0.0, 1.0, h)
            a = 0.50 * (1 - k) ** 1.4 + 0.04
            r = (79 * (1 - k) + 20 * k) * a
            g = (209 * (1 - k) + 50 * k) * a
            b_ = (232 * (1 - k) + 110 * k) * a
            self._grad = ((np.round(a * 255).astype(np.uint32) << 24) | (np.round(r).astype(np.uint32) << 16) |
                          (np.round(g).astype(np.uint32) << 8) | np.round(b_).astype(np.uint32))
            self._rows = np.arange(h, dtype=np.float64)[:, None]
        mask = self._rows >= (ys[None, :w] - self.r_spec.y())
        np.copyto(self._fill_buf, np.where(mask, self._grad[:, None], np.uint32(0)))
        return self._fill_img

    def _flag(self, p: QPainter, x: float, y: float, text: str, col: QColor, placed: list) -> None:
        fm = QFontMetricsF(p.font())
        w = fm.horizontalAdvance(text) + 10
        r = QRectF(x + 3, y, w, 15)
        if r.right() > self.width() - 50:
            r.moveRight(x - 3)
        for _ in range(4):
            if any(r.intersects(q) for q in placed):
                r.translate(0, 17)
        placed.append(r)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(r, 3, 3)
        p.setPen(QColor(6, 8, 12))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)

    def _paint_marks(self, p: QPainter, m: dict):
        if not self.have_view:
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        top, bot = self.r_spec.y(), self.r_spec.bottom()
        st = self.bridge.st
        am = st.get("system") == "am"
        rx = theme.sc("rx")
        placed: list[QRectF] = []
        # Occupied-band edges, and its width, so "17.8 MHz" is something seen.
        lo, hi = m["occ"]
        xl, xh = self.fmap.x(lo), self.fmap.x(hi)
        p.setPen(QPen(theme.sc("rx", 0.5), 1, Qt.PenStyle.DashLine))
        for x in (xl, xh):
            p.drawLine(QPointF(x, top), QPointF(x, bot))
        p.setFont(theme.font(10.5, theme.QFont.Weight.Bold))
        width = (hi - lo) / 1e6
        lab = f"{width:.1f} MHz OCCUPIED"
        fm = QFontMetricsF(p.font())
        lw = fm.horizontalAdvance(lab)
        if xh - xl > lw + 20:
            p.setPen(theme.sc("rx", 0.85))
            p.drawText(QRectF(xl, top + 4, xh - xl, 14), Qt.AlignmentFlag.AlignCenter, lab)
        # Carrier: solid, with a notch on top, continuing faintly down the waterfall.
        xc = self.fmap.x(m["carrier"])
        p.setPen(QPen(rx, 1.4))
        p.drawLine(QPointF(xc, top + 20), QPointF(xc, bot))
        p.setPen(QPen(theme.sc("rx", 0.25), 1, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(xc, self.r_wf.y()), QPointF(xc, self.r_wf.bottom()))
        tri = QPainterPath()
        tri.moveTo(xc - 6, top + 20)
        tri.lineTo(xc + 6, top + 20)
        tri.lineTo(xc, top + 27)
        tri.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(rx)
        p.drawPath(tri)
        p.setFont(theme.font(10, theme.QFont.Weight.Bold))
        self._flag(p, xc, top + 22, self.tr("VISION") if am else self.tr("CARRIER"), rx, placed)
        snd = theme.sc("sound")
        for i, fs in enumerate(m["sound"]):
            x = self.fmap.x(fs)
            if 0 <= x <= self.width():
                p.setPen(QPen(snd, 1.2, Qt.PenStyle.DashLine))
                p.drawLine(QPointF(x, top + 40), QPointF(x, bot))
                if i == len(m["sound"]) - 1:
                    off = abs(fs - m["carrier"]) / 1e6
                    # AM's carrier is above vision, except System A's (405
                    # lines), 3.5 MHz below it.
                    sign = ("+" if fs > m["carrier"] else "−") if am else "±"
                    self._flag(p, x, top + 40, self.tr("SOUND {offset}").format(offset=sign + number(off, 2)), snd, placed)
        for i, fn in enumerate(m.get("nicam") or []):
            x = self.fmap.x(fn)
            if 0 <= x <= self.width():
                p.setPen(QPen(snd, 1.0, Qt.PenStyle.DotLine))
                p.drawLine(QPointF(x, top + 76), QPointF(x, bot))
                if i == len(m["nicam"]) - 1:
                    self._flag(p, x, top + 76, self.tr("NICAM {offset}").format(offset=("+" if am else "±") + number(6.55, 2)),
                               snd, placed)
        if m["chroma"] is not None:
            x = self.fmap.x(m["chroma"])
            ch = theme.sc("chroma")
            p.setPen(QPen(ch, 1.2, Qt.PenStyle.DotLine))
            p.drawLine(QPointF(x, top + 58), QPointF(x, bot))
            self._flag(p, x, top + 58, self.tr("CHROMA {offset}").format(offset="+" + number(4.43, 2)), ch, placed)
        if st.get("keyed") or st.get("split"):
            tf, _occ = m["tx"]
            x = self.fmap.x(tf)
            red = theme.sc("tx")
            if 0 <= x <= self.width():
                p.setPen(QPen(red, 1.4 if st.get("keyed") else 1.0, Qt.PenStyle.SolidLine if st.get("keyed")
                              else Qt.PenStyle.DashLine))
                p.drawLine(QPointF(x, top + 76), QPointF(x, bot))
                self._flag(p, x, top + 76, self.tr("TX"), red, placed)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)

    def _paint_hover(self, p: QPainter):
        if self.hover is None or not self.have_view or self.r_strip.contains(self.hover):
            return
        x = self.hover.x()
        f = self.fmap.f(x)
        p.setPen(QPen(QColor(255, 255, 255, 60), 1))
        p.drawLine(QPointF(round(x) + 0.5, self.r_spec.y()), QPointF(round(x) + 0.5, self.height()))
        p.setFont(theme.font(11, theme.QFont.Weight.DemiBold, mono=True))
        ch = self.channel_at(x)
        text = f"{f / 1e6:.3f} MHz" if ch is None else f"{ch.freq / 1e6:g} MHz · click to tune"
        fm = QFontMetricsF(p.font())
        w = fm.horizontalAdvance(text) + 14
        box = QRectF(x + 8, self.r_spec.bottom() - 26, w, 20)
        if box.right() > self.width() - 50:
            box.moveRight(x - 8)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        p.setBrush(QColor(14, 18, 26, 230))
        p.drawRoundedRect(box, 4, 4)
        p.setPen(QColor(235, 242, 250))
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
