"""The built-in test card and the ident overlay, drawn with numpy.

Everything is drawn in *display* coordinates - 768 x 576, square pixels, the
picture as a 4:3 screen shows it - and then sampled to the 720-pixel picture
line, so the circle is round on a receiver and not on the array. Shapes have
anti-aliased edges (a distance field through a 1-pixel ramp), because a hard
pixel edge rings after the 5 MHz video filter and a test card that rings by
itself cannot show whether the channel does.

What is on it, and what each part is for on air:

- **75 % colour bars** across the top of the circle: saturation, hue, the
  PAL decoder, the vectorscope's six targets.
- **Greyscale** in six steps: gamma, black level, the AM transmitter's
  linearity near peak white.
- **Ident box**: the callsign large, and a second line (locator, band, mode)
  - the licence's identification, and the reason the card exists.
- **Frequency gratings** at 0.8-4.8 MHz: bandwidth at a glance - the last
  grating that still shows lines is the channel's video bandwidth.
- **Grid, circle and border castellations**: geometry and the line-timing
  stability the decoder's sync fit is meant to give.
"""
from __future__ import annotations

import numpy as np

from . import font, pal

DW, DH = 768, 576          # display coordinates
PIC_W = 720                # samples across the 52 us picture in the source image
GREY = 0.42


def _coords():
    y, x = np.mgrid[0:DH, 0:DW].astype(np.float32)
    return x + 0.5, y + 0.5


def _cov(d: np.ndarray) -> np.ndarray:
    """Coverage from a signed distance (negative inside), 1-pixel ramp."""
    return np.clip(0.5 - d, 0.0, 1.0)


def _paint(img, mask, colour):
    m = mask[..., None]
    img *= 1 - m
    img += m * np.asarray(colour, np.float32)


def _rect(x, y, x0, y0, x1, y1):
    d = np.maximum(np.maximum(x0 - x, x - x1), np.maximum(y0 - y, y - y1))
    return _cov(d)


def to_picture(disp: np.ndarray) -> np.ndarray:
    """768-wide display image -> 720-wide picture samples (576 x 720 x 3)."""
    return pal._resample_rows(disp, PIC_W)


def grating(x: np.ndarray, mhz: float, active_us: float = pal.ACTIVE_US) -> np.ndarray:
    """Vertical lines at `mhz` once on air: across the 52 us picture, a
    frequency f is f x 52 cycles over the display width (80.8 us on 405
    lines)."""
    cycles = mhz * active_us
    return 0.5 + 0.5 * np.sin(2 * np.pi * cycles * x / DW)


GRATINGS_MHZ = (0.8, 1.8, 2.8, 3.8, 4.8)
# 405 lines: System A's 3 MHz in five steps. A 625-line card shown on 405
# lines would put its 4.8 MHz grating at 3.1 MHz, under the wrong label.
GRATINGS_405_MHZ = (0.6, 1.2, 1.8, 2.4, 3.0)
ACTIVE_405_US = 1e6 / 10_125 - 18.0           # ohsatv/lines.py: A_ACTIVE_US
BARS = np.array([[1, 1, 1], [1, 1, 0], [0, 1, 1], [0, 1, 0], [1, 0, 1], [1, 0, 0], [0, 0, 1], [0, 0, 0]],
                np.float32) * 0.75


# The card's circle, and the ident box inside it under the bars and the
# greyscale - module constants because ident_box() reports the same box to
# the window, which keeps its own labels off it.
CARD_R = 252.0
_BARS_Y1 = DH / 2 - CARD_R + 112          # bottom of the 75 % bars in the circle
CARD_IDENT = (DW / 2 - 210, _BARS_Y1 + 52, DW / 2 + 210, _BARS_Y1 + 168)
BARS_STRIP = 96                           # the colour bars' callsign strip, at the foot
BARS_CALL_H = 63


def ident_box(kind: str, callsign: str = "", ident: "Ident | None" = None) -> tuple[float, float, float, float]:
    """Where the ident is in a picture of this source, in display
    coordinates (x0, y0, x1, y1 of 768 x 576): the card's box, the bars'
    callsign, or the box laid over a live picture."""
    if kind == "testcard":
        return CARD_IDENT
    if kind == "bars":
        w = font.width((callsign or "NO CALLSIGN").upper()[:16], BARS_CALL_H)
        return (DW / 2 - w / 2, DH - BARS_STRIP + 16, DW / 2 + w / 2, DH - BARS_STRIP + 16 + BARS_CALL_H)
    if ident is None:
        raise ValueError("a live source's ident box comes from its Ident")
    return ident.rect()


def test_card(callsign: str = "GM4BRI", line2: str = "OPENHAMSHACK ATV", footer: str = "",
              lines405: bool = False) -> np.ndarray:
    """The card as 576 x 720 x 3 float32 R'G'B' (0..1), ready for pal.Encoder
    - or, `lines405`, for sysa.Encoder: the same card with its gratings at
    System A's frequencies (0.6-3 MHz across 80.8 us of picture). Its
    ident is drawn at the same size: 405 lines keep the callsign 46 lines
    tall."""
    gratings = GRATINGS_405_MHZ if lines405 else GRATINGS_MHZ
    active_us = ACTIVE_405_US if lines405 else pal.ACTIVE_US
    x, y = _coords()
    img = np.full((DH, DW, 3), GREY, np.float32)
    cx, cy = DW / 2, DH / 2

    # Grid: 16 x 12 squares of 48 px, lines 2 px.
    gx = np.abs(((x + 24) % 48) - 24)
    gy = np.abs(((y + 24) % 48) - 24)
    _paint(img, _cov(np.minimum(gx, gy) - 1.0), (0.92, 0.92, 0.92))

    # Border castellations: alternating black/white blocks in the outer band.
    edge = 18
    band = (x < edge) | (x > DW - edge) | (y < edge) | (y > DH - edge)
    alt = ((np.floor(x / 48) + np.floor(y / 48)) % 2).astype(np.float32)
    castel = np.where(band, 1.0, 0.0).astype(np.float32)
    _paint(img, castel * alt, (0.95, 0.95, 0.95))
    _paint(img, castel * (1 - alt), (0.02, 0.02, 0.02))

    # Corner colour patches, outside the circle.
    for (px, py), col in zip(((48, 48), (DW - 120, 48), (48, DH - 120), (DW - 120, DH - 120)),
                             ((0.75, 0.1, 0.1), (0.1, 0.65, 0.15), (0.1, 0.2, 0.8), (0.8, 0.7, 0.1))):
        _paint(img, _rect(x, y, px, py, px + 72, py + 72), col)

    # The circle and what is inside it, band by band.
    r = CARD_R
    dist = np.hypot(x - cx, y - cy)
    inside = _cov(dist - r)
    top = cy - r
    inner = np.zeros_like(img)
    inner[:] = 0.06
    # 75 % bars
    bw = 2 * r / 8
    bi = np.clip(((x - (cx - r)) // bw).astype(int), 0, 7)
    yb0, yb1 = top + 34, _BARS_Y1
    m = ((y >= yb0) & (y < yb1)).astype(np.float32)
    inner = inner * (1 - m[..., None]) + BARS[bi] * m[..., None]
    # greyscale, 6 steps
    gw = 2 * r / 6
    gi = np.clip(((x - (cx - r)) // gw), 0, 5)
    m = ((y >= yb1) & (y < yb1 + 40)).astype(np.float32)
    lv = (gi / 5.0)[..., None] * np.ones(3, np.float32)
    inner = inner * (1 - m[..., None]) + lv * m[..., None]
    # ident box
    ix0, ib0, ix1, ib1 = CARD_IDENT
    _paint(inner, _rect(x, y, ix0, ib0, ix1, ib1), (0.0, 0.0, 0.0))
    # gratings
    gr0, gr1 = ib1 + 18, ib1 + 86
    pw = 2 * r * 0.84 / len(gratings)
    gx0 = cx - pw * len(gratings) / 2
    for i, f in enumerate(gratings):
        a = gx0 + i * pw + 4
        msk = _rect(x, y, a, gr0, a + pw - 8, gr1)
        g = 0.05 + 0.85 * grating(x, f, active_us)
        inner = inner * (1 - msk[..., None]) + (g[..., None] * np.ones(3, np.float32)) * msk[..., None]
    # saturated primaries strip under the gratings: red, green, blue, white, black
    st0, st1 = gr1 + 30, gr1 + 62
    sw_ = 2 * r * 0.7 / 5
    sx0 = cx - sw_ * 2.5
    for i, col in enumerate(((0.75, 0, 0), (0, 0.75, 0), (0, 0, 0.75), (1, 1, 1), (0, 0, 0))):
        _paint(inner, _rect(x, y, sx0 + i * sw_, st0, sx0 + (i + 1) * sw_, st1), col)
    # white base band for the footer text
    fb0 = st1 + 12
    _paint(inner, _rect(x, y, cx - r, fb0, cx + r, DH), (0.92, 0.92, 0.92))
    img = img * (1 - inside[..., None]) + inner * inside[..., None]
    # circle outline and centre cross
    _paint(img, _cov(np.abs(dist - r) - 1.6), (0.95, 0.95, 0.95))
    cross = np.minimum(np.abs(x - cx), np.abs(y - cy))
    near = ((np.abs(x - cx) < 30) & (np.abs(y - cy) < 30)).astype(np.float32)
    _paint(img, _cov(cross - 0.8) * near * (1 - _rect(x, y, ix0, ib0, ix1, ib1)), (1, 1, 1))

    # Text.
    call = (callsign or "").upper()[:12]
    big = 70 if len(call) <= 7 else 49
    font.draw(img, call, int(cx), int(ib0 + 14), big, (1, 1, 1), align="centre")
    font.draw(img, (line2 or "").upper()[:28], int(cx), int(ib1 - 30), 14, (0.95, 0.85, 0.3), align="centre")
    for i, f in enumerate(gratings):
        font.draw(img, f"{f:g}", int(gx0 + (i + 0.5) * pw), int(gr1 + 6), 14, (0.95, 0.95, 0.95), align="centre")
    font.draw(img, (footer or "OPENHAMSHACK ATV").upper()[:28], int(cx), int(fb0 + 12), 21, (0.05, 0.05, 0.05),
              align="centre")
    return to_picture(np.clip(img, 0, 1))


def colour_bars(callsign: str = "") -> np.ndarray:
    """Full-field EBU 75 % bars, with the callsign in a strip at the foot."""
    x, y = _coords()
    img = BARS[np.clip((x // (DW / 8)).astype(int), 0, 7)].copy()
    if callsign:
        _paint(img, _rect(x, y, 0, DH - BARS_STRIP, DW, DH), (0.0, 0.0, 0.0))
        font.draw(img, callsign.upper()[:16], DW // 2, DH - BARS_STRIP + 16, BARS_CALL_H, (1, 1, 1),
                  align="centre")
    return to_picture(img)


class Ident:
    """The callsign box laid over live pictures (webcam, file). Kept as a
    precomputed mask and colour so overlaying a frame is two array
    operations."""

    def __init__(self, callsign: str, line2: str = "", position: str = "bottom-left"):
        h = 36
        m1 = font.render(callsign.upper()[:12], h)
        rows = [m1]
        if line2:
            rows.append(np.zeros((6, 1), np.float32))
            rows.append(font.render(line2.upper()[:24], 14))
        w = max(r.shape[1] for r in rows)
        text = np.concatenate([np.pad(r, ((0, 0), (0, w - r.shape[1]))) for r in rows], axis=0)
        pad = 12
        self.box = np.pad(np.ones_like(text), pad, constant_values=1.0)
        self.text = np.pad(text, pad)
        self.position = position

    def _place(self, W: int, H: int, bw: int, bh: int, mx: int) -> tuple[int, int]:
        """Top left of a bw x bh box in a W x H picture, `mx` in from the
        side and 28 lines from the top or foot, in the chosen corner."""
        x0 = mx if "left" in self.position else W - bw - mx
        y0 = 28 if "top" in self.position else H - bh - 28
        return x0, y0

    def rect(self) -> tuple[float, float, float, float]:
        """The box in display coordinates, (x0, y0, x1, y1) of 768 x 576."""
        bh, bw = self.box.shape
        x0, y0 = self._place(DW, DH, bw, bh, 28)
        return (float(x0), float(y0), float(x0 + bw), float(y0 + bh))

    def apply(self, rgb: np.ndarray) -> np.ndarray:
        """Overlay onto a 576 x W x 3 float image (in place) and return it."""
        H, W = rgb.shape[:2]
        # The box is drawn in display pixels; squeeze it to the picture's width.
        sx = W / DW
        bw = max(1, int(round(self.box.shape[1] * sx)))
        bh = self.box.shape[0]
        cols = np.clip(((np.arange(bw) + 0.5) / sx).astype(int), 0, self.box.shape[1] - 1)
        box, text = self.box[:, cols], self.text[:, cols]
        x0, y0 = self._place(W, H, bw, bh, int(28 * sx))
        sub = rgb[y0:y0 + bh, x0:x0 + bw]
        sub *= (1 - 0.72 * box)[..., None]
        sub += text[..., None] * (1 - sub)
        return rgb
