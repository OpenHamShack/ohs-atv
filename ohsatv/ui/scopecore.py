"""The parts of the scopes that are arithmetic rather than painting: the
pixel <-> frequency map and the waterfall's image ring buffer.

Kept free of widgets so they can be tested directly, and so the hot path is
plainly visible: one numpy resample and one LUT index per spectrum line, and
nothing at all per pixel in Python.
"""
from __future__ import annotations

import numpy as np
from PyQt6.QtCore import QRectF
from PyQt6.QtGui import QImage, QPainter


class FreqMap:
    """Linear map between a frequency span [lo, hi] Hz and x in [x0, x0 + width]."""

    def __init__(self, lo: float, hi: float, width: float, x0: float = 0.0):
        self.set(lo, hi, width, x0)

    def set(self, lo: float, hi: float, width: float, x0: float = 0.0):
        if hi <= lo:
            hi = lo + 1.0
        self.lo, self.hi, self.width, self.x0 = float(lo), float(hi), max(1.0, float(width)), float(x0)

    @property
    def span(self) -> float:
        return self.hi - self.lo

    @property
    def hz_per_px(self) -> float:
        return self.span / self.width

    def x(self, f: float) -> float:
        return self.x0 + (f - self.lo) / self.span * self.width

    def f(self, x: float) -> float:
        return self.lo + (x - self.x0) / self.width * self.span

    def edges(self, n: int) -> np.ndarray:
        """Frequencies of the n+1 boundaries of n equal columns."""
        return self.lo + np.arange(n + 1, dtype=np.float64) * (self.span / n)


def resample(start: float, bin_hz: float, db: np.ndarray, lo: float, hi: float, n: int, fill: float) -> np.ndarray:
    """A spectrum line (bin i centred on start + i*bin_hz) as n columns over [lo, hi].

    Where a column covers several bins it takes their *maximum*, not the
    mean or a sample: on the wide view one pixel is ~280 Hz, and a CW signal
    or an FT8 tone is one bin - sampling would make it flicker in and out as
    the line's start drifts with rxCorr; averaging would bury it. Where a
    column is narrower than half a bin (zoomed in) it interpolates. Columns outside
    the line get `fill`.
    """
    db = np.asarray(db, np.float32)
    nb = len(db)
    span = hi - lo
    col_w = span / n
    out = np.full(n, fill, np.float32)
    if nb == 0:
        return out
    # Pool whenever a column is at least half a bin: at the default view a
    # column is ~1.07 bins, and interpolating there lands between bins and
    # shaves 10 dB off a one-bin carrier.
    if col_w >= 0.5 * bin_hz:
        fe = lo + np.arange(n + 1) * col_w
        idx = np.floor((fe - start) / bin_hz + 0.5).astype(np.int64)
        valid = (idx[1:] > 0) & (idx[:-1] < nb)
        i0 = np.clip(idx[:-1], 0, nb - 1)
        i1 = np.clip(idx[1:], 1, nb)
        i1 = np.maximum(i1, i0 + 1)
        # reduceat over ascending starts; each segment ends where the next begins,
        # which is exactly the column edge, so one call does every column.
        r = np.maximum.reduceat(db, i0)
        # reduceat's last segment runs to the end of db: clip it to the column.
        last = n - 1
        r[last] = db[i0[last]:i1[last]].max()
        out[valid] = r[valid]
        return out
    fc = lo + (np.arange(n) + 0.5) * col_w
    x = (fc - start) / bin_hz
    inside = (x >= -0.5) & (x <= nb - 0.5)
    out[inside] = np.interp(x[inside], np.arange(nb), db).astype(np.float32)
    return out


class Waterfall:
    """A QImage ring buffer, newest row on top.

    Each spectrum line becomes one row written into a numpy array that the
    QImage wraps (the image never owns or copies the memory). Painting is two
    blits - the rows from `head` to the bottom, then the rows above it - so
    history is never re-rendered, only moved when the view itself changes.
    """

    def __init__(self, width: int, height: int):
        self.lut = np.zeros(256, np.uint32)
        self.rows_total = 0
        self._alloc(max(1, width), max(1, height))

    def _alloc(self, w: int, h: int):
        self.w, self.h = w, h
        self.buf = np.zeros((h, w), np.uint32)
        self.buf[:] = self.blank()
        self.head = 0
        self.img = QImage(self.buf.data, w, h, w * 4, QImage.Format.Format_RGB32)

    def blank(self) -> int:
        """Rows with no history yet: the palette's floor colour, so a new
        or enlarged waterfall reads as "quiet", not as a black hole."""
        return int(self.lut[0]) if self.lut.any() else 0xFF06080C

    def set_lut(self, lut: np.ndarray):
        old_blank = self.blank()
        self.lut = lut
        self.buf[self.buf == old_blank] = self.blank()

    def resize(self, w: int, h: int):
        """New size; the old history is kept, stretched to the new width (a
        one-off cost when the operator resizes, never per frame)."""
        w, h = max(1, w), max(1, h)
        if (w, h) == (self.w, self.h):
            return
        old = self.ordered()
        self._alloc(w, h)
        cols = np.minimum((np.arange(w) * old.shape[1] / w).astype(np.int64), old.shape[1] - 1)
        rows = min(h, old.shape[0])
        self.buf[:rows] = old[:rows][:, cols]

    def ordered(self) -> np.ndarray:
        """The buffer with the newest row first (a copy)."""
        return np.concatenate([self.buf[self.head:], self.buf[:self.head]], axis=0)

    def remap(self, old_lo: float, old_hi: float, new_lo: float, new_hi: float):
        """The view moved or zoomed: shift/stretch history so each column still
        shows the frequency it now stands for. Columns with no history go dark."""
        w = self.w
        fc = new_lo + (np.arange(w) + 0.5) * (new_hi - new_lo) / w
        src = np.floor((fc - old_lo) / (old_hi - old_lo) * w).astype(np.int64)
        ok = (src >= 0) & (src < w)
        new = np.empty_like(self.buf)
        new[:, ok] = self.buf[:, src[ok]]
        new[:, ~ok] = self.lut[0]
        self.buf[:] = new

    def push_row(self, row_u32: np.ndarray):
        self.head = (self.head - 1) % self.h
        self.buf[self.head] = row_u32
        self.rows_total += 1

    def push_db(self, cols_db: np.ndarray, floor: float, rng: float):
        idx = np.clip((cols_db - floor) * (255.0 / max(1.0, rng)), 0, 255).astype(np.uint8)
        self.push_row(self.lut[idx])

    def draw(self, p: QPainter, target: QRectF):
        """Two blits; `target` is in logical pixels, the image in device pixels."""
        h = self.h
        top = h - self.head
        sy = target.height() / h
        if top > 0:
            p.drawImage(QRectF(target.x(), target.y(), target.width(), top * sy), self.img,
                        QRectF(0, self.head, self.w, top))
        if self.head > 0:
            p.drawImage(QRectF(target.x(), target.y() + top * sy, target.width(), self.head * sy), self.img,
                        QRectF(0, 0, self.w, self.head))
