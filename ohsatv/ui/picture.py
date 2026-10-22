"""The picture monitor: the hero of the window.

What it shows, in order of precedence:

- **TX preview** when the operator asked for it (`view == "tx"`) or while
  keyed on a half-duplex radio (`duplex` False), where receive is paused and
  the received picture would otherwise freeze. Labelled TX PREVIEW, so the
  operator never mistakes what they send for what they receive.
- **The received picture**, while the decoder is producing frames.
- **NO SIGNAL**: drifting grain with a rolling bar - what an unsynced set
  shows - and a sentence saying what is being searched for. Shown whenever
  the decoder reports `searching` *or* frames have stopped arriving: a
  frozen last frame looks like a live picture, and that is the one thing a
  monitor must never do.

Frames arrive at the decoder's own width (520-1040 columns) and are shown at
4:3. They are converted to RGB32 once (0.2 ms) and drawn with bilinear
smoothing straight into the screen rectangle (1 ms at 1920 x 1080). The
first version scaled each frame with QImage.scaled(SmoothTransformation):
2-6 ms a frame *holding the GIL* (PyQt does not release it there), which at
25 frames a second starved the receive threads at 20 MS/s until the decoder
lost lines. The grain is six small noise frames generated once and shown
unsmoothed, so it costs a blit.
"""
from __future__ import annotations

import math
import time
from pathlib import Path

import numpy as np
from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (QColor, QFontMetricsF, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
                         QRadialGradient)
from PyQt6.QtWidgets import QSizePolicy, QWidget

from ..i18n.fmt import frequency
from . import icons, theme

STALE_S = 0.6          # no frame for this long = no signal, whatever the lock says

SYSTEM_NAMES = {"am": "AM-VSB", "fm": "FM-ATV"}


def rgb_qimage(rgb: np.ndarray) -> tuple[QImage, np.ndarray]:
    """uint8 HxWx3 -> an RGB32 QImage of its own (the format QPainter scales
    fastest). Returns the source buffer too, for callers that keep it."""
    buf = np.ascontiguousarray(rgb, dtype=np.uint8)
    h, w = buf.shape[:2]
    img = QImage(buf.data, w, h, 3 * w, QImage.Format.Format_RGB888).convertToFormat(QImage.Format.Format_RGB32)
    return img, buf


def float_to_u8(rgb: np.ndarray) -> np.ndarray:
    return (np.clip(rgb, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)


def fmt_mhz(f: float) -> str:
    return f"{f / 1e6:.3f}"


def fmt_clock(sec: float) -> str:
    sec = max(0, int(sec))
    return f"{sec // 60}:{sec % 60:02d}"


class Monitor(QWidget):
    snapshot_saved = pyqtSignal(str)

    def __init__(self, bridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.rx_img: QImage | None = None      # the decoder's own size, RGB32
        self.tx_img: QImage | None = None
        self._grain = self._make_grain()
        self._grain_i = 0
        self._vignette: QPixmap | None = None
        self._vig_size = QSize()
        self.cost_paint = 0.0
        self.snap_rect = QRectF()
        self.pills: dict[str, QRectF] = {}     # where the last paint put each label (tests read it)
        self.hover_snap = False
        self.setMouseTracking(True)
        self.setMinimumSize(320, 240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        # Subtitles (teletext page 888) over the received picture, as a
        # set showed them: only the boxed text, the picture round it.
        self.subs_on = True
        self.sub_img: QImage | None = None
        self.sub_key = None
        self._sub_t = 0.0
        bridge.video.connect(self.on_video)
        bridge.txvideo.connect(self.on_txvideo)
        bridge.tick.connect(self._tick)
        bridge.changed.connect(self._changed)

    # ------------------------------------------------------------ data
    @staticmethod
    def _make_grain() -> list[QImage]:
        rng = np.random.default_rng(1)
        out = []
        for _ in range(6):
            g = rng.integers(0, 256, (150, 200), dtype=np.uint8)
            # A little vertical smear: analogue noise on a set is wider than tall.
            g = ((g.astype(np.uint16) + np.roll(g, 1, axis=1)) // 2).astype(np.uint8)
            img = QImage(g.data, 200, 150, 200, QImage.Format.Format_Grayscale8).copy()
            out.append(img)
        return out

    def on_video(self, item):
        _t, rgb, _stats = item
        self.rx_img, _ = rgb_qimage(rgb)
        if self.mode() == "rx":
            self.update()

    def on_txvideo(self, item):
        _t, rgb = item
        self.tx_img, _ = rgb_qimage(float_to_u8(rgb))
        if self.mode() == "tx":
            self.update()

    def set_subtitles(self, on: bool):
        self.subs_on = bool(on)
        self.sub_img = None
        self.sub_key = None
        self.update()

    def _subtitles(self):
        """Page 888, five times a second: a subtitle (C6) or newsflash (C5)
        page's boxed rows, rendered with ohsatv/ttxrender.py. An erased page
        (C4, no rows) renders empty, so the words go when they should."""
        from .. import ttx, ttxrender
        try:
            pg = self.bridge.radio.ttx_page("888")
        except Exception:  # noqa: BLE001 - the overlay must never take the picture down
            pg = None
        rows = None
        if pg is not None and pg["ctl"] & (ttx.C5 | ttx.C6) and pg["age"] < 30.0:
            rows = tuple(pg["rows"])
        if rows == self.sub_key:
            return
        self.sub_key = rows
        if rows is None:
            self.sub_img = None
        else:
            rgba = np.ascontiguousarray(ttxrender.render(list(rows), boxed_only=True))
            self.sub_img = None if not rgba[..., 3].any() else QImage(
                rgba.data, rgba.shape[1], rgba.shape[0], rgba.shape[1] * 4, QImage.Format.Format_RGBA8888).copy()
        self.update()

    def _tick(self, _dt):
        if self.subs_on and self.isVisible():
            now = time.monotonic()
            if now - self._sub_t > 0.2:
                self._sub_t = now
                self._subtitles()
        # Grain and the on-air pulse are animations; a live picture repaints
        # itself when a frame arrives.
        if self.mode() == "tx" or not self.has_signal() or self.bridge.st.get("keyed"):
            self._grain_i = (self._grain_i + 1) % len(self._grain)
            self.update()

    def _changed(self, ch):
        if any(k in ch for k in ("view", "keyed", "duplex", "rxLock", "rxFreq", "system", "error", "snr",
                                 "rxFormat")):
            self.update()

    # ------------------------------------------------------------ state
    def mode(self) -> str:
        st = self.bridge.st
        if st.get("view") == "tx" or (st.get("keyed") and not st.get("duplex")):
            return "tx"
        return "rx"

    def has_signal(self) -> bool:
        st = self.bridge.st
        return (st.get("rxLock", "searching") != "searching" and self.rx_img is not None
                and self.bridge.picture_age() < STALE_S)

    # ------------------------------------------------------------ geometry
    def screen_rect(self) -> QRectF:
        """The 4:3 picture area, centred, inside the bezel."""
        m = 16.0
        w, h = self.width() - 2 * m, self.height() - 2 * m
        if w / max(1.0, h) > 4 / 3:
            sw, sh = h * 4 / 3, h
        else:
            sw, sh = w, w * 3 / 4
        return QRectF((self.width() - sw) / 2, (self.height() - sh) / 2, sw, sh)

    def sizeHint(self):
        return QSize(800, 600)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return int((w - 32) * 3 / 4 + 32)

    # ------------------------------------------------------------ actions
    def shown_image(self) -> QImage | None:
        return self.tx_img if self.mode() == "tx" else (self.rx_img if self.has_signal() else None)

    def snapshot(self) -> str:
        """Save what the monitor shows, at 768 x 576 (4:3), as PNG in the data
        directory. Returns the path, or '' with a toast saying why not."""
        img = self.shown_image()
        if img is None:
            self.bridge.say(self.tr("Nothing to save: there is no picture"), "info")
            return ""
        d = Path(getattr(self.bridge.radio, "data_dir", ".") or ".")
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.bridge.say(self.tr("Could not create {folder}: {error}").format(folder=d, error=e), "error")
            return ""
        kind = "tx" if self.mode() == "tx" else "rx"
        stamp = time.strftime("%Y%m%d-%H%M%S")
        f = float(self.bridge.st.get("txFreq" if kind == "tx" else "rxFreq") or 0)
        path = d / f"openhamshack-atv-{stamp}-{kind}-{f / 1e6:.3f}MHz.png"
        out = img.scaled(768, 576, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        if not out.save(str(path), "PNG"):
            self.bridge.say(self.tr("Could not write {path}").format(path=path), "error")
            return ""
        self.bridge.say(self.tr("Saved {name} in {folder}").format(name=path.name, folder=d), "info")
        self.snapshot_saved.emit(str(path))
        return str(path)

    def mouseMoveEvent(self, e):
        h = self.snap_rect.contains(e.position())
        if h != self.hover_snap:
            self.hover_snap = h
            self.setCursor(Qt.CursorShape.PointingHandCursor if h else Qt.CursorShape.ArrowCursor)
            self.setToolTip(self.tr("Save this picture as PNG") if h else "")
            self.update()

    def leaveEvent(self, e):
        self.hover_snap = False
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.snap_rect.contains(e.position()):
            self.snapshot()

    # ------------------------------------------------------------ paint
    def _vignette_for(self, sr: QRectF) -> QPixmap:
        size = QSize(int(sr.width()), int(sr.height()))
        if self._vignette is None or size != self._vig_size:
            pm = QPixmap(size)
            pm.fill(Qt.GlobalColor.transparent)
            q = QPainter(pm)
            g = QRadialGradient(QPointF(size.width() / 2, size.height() / 2), 0.75 * math.hypot(size.width(),
                                                                                                size.height()) / 2)
            g.setColorAt(0.0, QColor(0, 0, 0, 0))
            g.setColorAt(0.7, QColor(0, 0, 0, 0))
            g.setColorAt(1.0, QColor(0, 0, 0, 70))
            q.fillRect(pm.rect(), g)
            # Glass: a faint sheen across the top left, as the icon has.
            sheen = QLinearGradient(0, 0, size.width() * 0.6, size.height() * 0.6)
            sheen.setColorAt(0.0, QColor(255, 255, 255, 16))
            sheen.setColorAt(0.5, QColor(255, 255, 255, 0))
            q.fillRect(pm.rect(), sheen)
            q.end()
            self._vignette, self._vig_size = pm, size
        return self._vignette

    def paintEvent(self, _e):
        t0 = time.perf_counter()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        st = self.bridge.st
        p.fillRect(self.rect(), theme.c("bg1"))
        sr = self.screen_rect()
        keyed = bool(st.get("keyed"))
        now = time.monotonic()
        # On air: a red glow round the bezel, breathing slowly.
        bez = sr.adjusted(-12, -12, 12, 12)
        if keyed:
            k = 0.75 + 0.25 * math.sin(now * 2 * math.pi / 1.6)
            p.setBrush(Qt.BrushStyle.NoBrush)
            for i in range(7, 0, -1):
                p.setPen(QPen(theme.c("txGlow", 0.07 * k * (8 - i) / 7 + 0.02), 2 * i))
                p.drawRoundedRect(bez.adjusted(-i, -i, i, i), 16 + i, 16 + i)
        # The bezel: always dark, in both themes - a monitor is a dark object.
        g = QLinearGradient(0, bez.top(), 0, bez.bottom())
        g.setColorAt(0, QColor("#252c38"))
        g.setColorAt(1, QColor("#10141a"))
        p.setPen(QPen(theme.c("txGlow") if keyed else QColor("#2f3846"), 2 if keyed else 1))
        p.setBrush(g)
        p.drawRoundedRect(bez, 16, 16)
        p.setPen(QPen(QColor(255, 255, 255, 18), 1))
        p.drawLine(QPointF(bez.left() + 16, bez.top() + 1.5), QPointF(bez.right() - 16, bez.top() + 1.5))
        clip = QPainterPath()
        clip.addRoundedRect(sr, 6, 6)
        p.save()
        p.setClipPath(clip)
        p.fillRect(sr, QColor("#030406"))
        mode = self.mode()
        if mode == "tx":
            img = self.tx_img
            if img is not None:
                p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                p.drawImage(sr, img)
            else:
                self._paint_no_signal(p, sr, self.tr("WAITING FOR THE SOURCE"),
                                      self.tr("The transmit preview appears in a moment"))
        elif self.has_signal():
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawImage(sr, self.rx_img)
        else:
            self._paint_no_signal(p, sr, *self._no_signal_text())
        p.drawPixmap(sr.topLeft(), self._vignette_for(sr))
        self._paint_overlays(p, sr, mode)
        # Subtitles last, over the monitor's own labels: on a set they are
        # the picture's, and the info pill is only ours.
        if mode == "rx" and self.has_signal() and self.subs_on and self.sub_img is not None:
            p.drawImage(sr, self.sub_img)
        p.restore()
        p.setPen(QPen(QColor(0, 0, 0, 160), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(sr.adjusted(-0.5, -0.5, 0.5, 0.5), 6.5, 6.5)
        p.end()
        ms = (time.perf_counter() - t0) * 1000.0
        self.cost_paint += (ms - self.cost_paint) * 0.1

    def _no_signal_text(self) -> tuple[str, str]:
        st = self.bridge.st
        f = float(st.get("rxFreq") or 0)
        sysname = SYSTEM_NAMES.get(st.get("system"), "")
        if not st.get("running"):
            return "NO RECEIVER", st.get("error") or "The radio is not running"
        if st.get("error"):
            return "NO RECEIVER", st["error"]
        if st.get("rxLock") == "h-lock":
            return "LINE SYNC ONLY", f"Waiting for field sync · {fmt_mhz(f)} MHz {sysname}"
        if self.rx_img is not None and st.get("rxLock") != "searching":
            return "PICTURE LOST", f"No frames for {self.bridge.picture_age():.0f} s · {fmt_mhz(f)} MHz {sysname}"
        return "NO SIGNAL", f"Searching for sync · {fmt_mhz(f)} MHz {sysname}"

    def _paint_no_signal(self, p: QPainter, sr: QRectF, title: str, line: str):
        p.save()
        p.setOpacity(0.30)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        p.drawImage(sr, self._grain[self._grain_i])
        p.setOpacity(1.0)
        # A rolling bar drifting down, as on a set with no field sync.
        ph = (time.monotonic() / 3.2) % 1.0
        y = sr.top() + (ph * 1.4 - 0.2) * sr.height()
        bar = QLinearGradient(0, y - sr.height() * 0.12, 0, y + sr.height() * 0.12)
        bar.setColorAt(0, QColor(255, 255, 255, 0))
        bar.setColorAt(0.5, QColor(255, 255, 255, 22))
        bar.setColorAt(1, QColor(255, 255, 255, 0))
        p.fillRect(sr, bar)
        # The label sits on a soft dark plate so it reads over the grain.
        big = max(18.0, min(40.0, sr.width() / 22))
        tf = theme.font(big, theme.QFont.Weight.Bold, spacing=big * 0.22)
        fm = QFontMetricsF(tf)
        lf = theme.font(max(11.0, big * 0.36))
        fml = QFontMetricsF(lf)
        w = max(fm.horizontalAdvance(title), min(sr.width() - 60, fml.horizontalAdvance(line))) + 64
        h = fm.height() + fml.height() * (2 if fml.horizontalAdvance(line) > sr.width() - 60 else 1) + 36
        plate = QRectF(sr.center().x() - w / 2, sr.center().y() - h / 2, w, h)
        p.setPen(QPen(QColor(255, 255, 255, 26), 1))
        p.setBrush(QColor(4, 6, 10, 200))
        p.drawRoundedRect(plate, 12, 12)
        p.setFont(tf)
        p.setPen(QColor("#e8edf4"))
        p.drawText(QRectF(plate.x(), plate.y() + 14, plate.width() + big * 0.22, fm.height()),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, title)
        p.setFont(lf)
        p.setPen(QColor("#a3adbb"))
        p.drawText(QRectF(plate.x() + 24, plate.y() + 18 + fm.height(), plate.width() - 48, plate.height() - fm.height()
                          - 26), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
                   line)
        p.restore()

    def _pill(self, p: QPainter, x: float, y: float, text: str, fg: QColor, bg: QColor, font, right=False,
              dot: QColor | None = None) -> QRectF:
        fm = QFontMetricsF(font)
        w = fm.horizontalAdvance(text) + 20 + (14 if dot else 0)
        h = fm.height() + 10
        r = QRectF(x - w if right else x, y, w, h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(r, h / 2, h / 2)
        tx = r.x() + 10
        if dot is not None:
            p.setBrush(dot)
            p.drawEllipse(QPointF(tx + 4, r.center().y()), 4, 4)
            tx += 14
        p.setFont(font)
        p.setPen(fg)
        p.drawText(QRectF(tx, r.y(), w, h), Qt.AlignmentFlag.AlignVCenter, text)
        return r

    def ident_rect(self, sr: QRectF) -> QRectF | None:
        """Where the ident burnt into the transmitted picture is on screen,
        from the engine's identRect (fractions of the 4:3 picture, which
        fills `sr`)."""
        r = self.bridge.st.get("identRect") or []
        if len(r) != 4:
            return None
        x0, y0, x1, y1 = (float(v) for v in r)
        return QRectF(sr.x() + x0 * sr.width(), sr.y() + y0 * sr.height(), (x1 - x0) * sr.width(),
                      (y1 - y0) * sr.height())

    def _clear_spot(self, sr: QRectF, w: float, h: float, spots, keep_off) -> tuple[float, float] | None:
        """The first of `spots` (top-left corners) where a w x h label fits
        inside the picture and touches none of `keep_off`."""
        for x, y in spots:
            r = QRectF(x, y, w, h)
            if sr.contains(r) and not any(r.intersects(k) for k in keep_off):
                return x, y
        return None

    def _top_place(self, sr: QRectF, pad: float, w: float, h: float, ident: QRectF | None,
                   after: QRectF | None = None) -> tuple[float, float]:
        """Where a label of the top row goes: the TX PREVIEW (or RX) label
        when `after` is None, otherwise ON AIR, which follows it.

        Top left for the label and centred for ON AIR, as before - but an
        ident put top left (identPosition) is burnt into the picture
        exactly there, and over the TX preview the label hid the callsign
        the operator is checking. So, clear of the ident, the snapshot
        button and what is already placed: along the top row past the
        ident, and failing that a line under it. The ident is padded as for
        the info pill so a label never sits flush on its box."""
        left, top = sr.x() + pad, sr.y() + pad
        keep_off = [r.adjusted(-4, -4, 4, 4) for r in (self.snap_rect, *self.pills.values())]
        if ident is not None:
            keep_off.append(ident.adjusted(-6, -6, 6, 6))
        below = [self.snap_rect.bottom(), *(r.bottom() for r in self.pills.values())]
        if ident is not None and ident.top() < sr.center().y():
            below.append(ident.bottom() + 6)
        under_all = (left, max(below) + 6)
        if after is None:
            spots = [(left, top)]
            if ident is not None:
                spots += [(ident.right() + 8, top), (left, ident.bottom() + 12)]
        else:
            # Centred, unless that runs into the label on its left: at
            # 1366 x 768 the half-duplex "TX PREVIEW · RECEIVE PAUSED" is
            # wider than half the screen and the two pills were drawn over
            # each other, cutting both sentences. Then just after the label,
            # then a second line under it.
            spots = [(sr.center().x() - w / 2, top), (after.right() + 8, after.y()), (after.x(), after.bottom() + 6)]
        return self._clear_spot(sr, w, h, spots + [under_all], keep_off) or under_all

    def _info_place(self, sr: QRectF, pad: float, text: str, font, mode: str) -> tuple[float, float]:
        """Where the frequency-and-source pill goes. Bottom left, as on any
        monitor - except over the TX preview, where it sat on the ident and
        hid its second line (locator and text), the one part of the picture
        the operator is checking. There it moves to the first place clear of
        the ident and of the other labels: the other bottom corner, then
        just above the ident, then under the labels at the top."""
        fm = QFontMetricsF(font)
        w, h = fm.horizontalAdvance(text) + 20, fm.height() + 10
        left, bottom = sr.x() + pad, sr.bottom() - pad - h
        ident = self.ident_rect(sr) if mode == "tx" else None
        if ident is None:
            return left, bottom
        keep_off = [ident.adjusted(-6, -6, 6, 6), self.snap_rect, *self.pills.values()]
        right = sr.right() - pad - w
        ident_right = ident.center().x() > sr.center().x()
        top_y = max([r.bottom() for r in self.pills.values()] + [self.snap_rect.bottom()]) + 6
        spots = ((left, bottom), (right, bottom), (right if ident_right else left, ident.top() - 6 - h), (left, top_y))
        return self._clear_spot(sr, w, h, spots, keep_off) or (left, bottom)

    def _paint_overlays(self, p: QPainter, sr: QRectF, mode: str):
        st = self.bridge.st
        s = max(0.75, min(1.15, sr.width() / 800))
        pad = 14 * s
        f_tag = theme.font(11.5 * s, theme.QFont.Weight.Bold, spacing=1.0)
        f_info = theme.font(12 * s, theme.QFont.Weight.DemiBold, mono=True)
        dark = QColor(4, 6, 10, 170)
        keyed = bool(st.get("keyed"))
        bs = 34 * s
        self.snap_rect = QRectF(sr.right() - pad - bs, sr.y() + pad, bs, bs)
        self.pills = {}
        ident = self.ident_rect(sr) if mode == "tx" else None
        if mode == "tx":
            label = "TX PREVIEW"
            if keyed and not st.get("duplex") and st.get("view") != "tx":
                label = "TX PREVIEW · RECEIVE PAUSED"
            fg, bg, dot = QColor("#ffffff"), theme.c("tx", 0.85), None
        else:
            label, fg, bg = "RX", QColor("#e8edf4"), dark
            dot = theme.sc("ok") if self.has_signal() else theme.sc("text3")
        fm = QFontMetricsF(f_tag)
        w, h = fm.horizontalAdvance(label) + 20 + (14 if dot else 0), fm.height() + 10
        x, y = self._top_place(sr, pad, w, h, ident)
        lab = self.pills["mode"] = self._pill(p, x, y, label, fg, bg, f_tag, dot=dot)
        if keyed:
            txt = f"ON AIR  {fmt_clock(float(st.get('txSeconds') or 0))}"
            w = fm.horizontalAdvance(txt) + 34
            x, y = self._top_place(sr, pad, w, h, ident, after=lab)
            self.pills["onair"] = self._pill(p, x, y, txt, QColor("#ffffff"), theme.c("txGlow", 0.92), f_tag,
                                             dot=QColor("#ffffff"))
        # Bottom left: what this picture is.
        f = float(st.get("txFreq" if mode == "tx" else "rxFreq") or 0)
        # The window's own caption over the picture, not the transmitted
        # ident, so it takes the locale's decimal separator (I18N.md 4.1);
        # three decimals, as fmt_mhz always gave it, so the pill keeps its width.
        bits = [frequency(int(round(f)), "MHz", 3), SYSTEM_NAMES.get(st.get("system"), "")]
        if mode == "rx":
            lock = st.get("rxLock", "searching")
            bits.append({"locked": "LOCKED", "h-lock": "H-LOCK"}.get(lock, "SEARCHING"))
            if st.get("snr") is not None and lock != "searching":
                bits.append(f"SNR {float(st['snr']):.1f} dB")
            fmt = st.get("rxFormat") or "pal"
            if fmt == "mac":
                bits.append("MINI-MAC")
            elif fmt == "405":
                bits.append("405 LINES")
            elif st.get("colourLock"):
                bits.append("LOW-PAL" if fmt == "lowpal" else "PAL")
        else:
            bits.append({"testcard": "TEST CARD", "bars": "COLOUR BARS", "webcam": "WEBCAM",
                         "file": "VIDEO FILE"}.get(st.get("source"), ""))
        text = "  ·  ".join(b for b in bits if b)
        x, y = self._info_place(sr, pad, text, f_info, mode)
        self.pills["info"] = self._pill(p, x, y, text, QColor("#e8edf4"), dark, f_info)
        # Top right: snapshot (placed above, so the pills can keep clear of it).
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(4, 6, 10, 200 if self.hover_snap else 140))
        p.drawRoundedRect(self.snap_rect, 8, 8)
        icons.paint(p, "camera", self.snap_rect.adjusted(8 * s, 8 * s, -8 * s, -8 * s),
                    QColor("#ffffff") if self.hover_snap else QColor("#c9d2de"), 1.6)
