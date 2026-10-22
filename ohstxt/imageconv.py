"""A picture as block mosaics.

The constraint that makes this more than a resize: a teletext row has no
colour per pixel. Colour is a *state* that runs along the row, changed only
by a control code, and every control code takes a whole cell - two sixels
wide, three high - which shows as background (Level 1 has no way to put a
change of colour between two cells). So a picture is not quantised pixel by
pixel; each character row is chosen as a whole, trading the cells spent on
colour changes against the sixels they would make right.

That choice is made exactly, by dynamic programming over the cells of the
row (Viterbi): the state is (foreground, background), and at each cell the
row either draws a mosaic in the current colours (each sixel taking
whichever of the two is nearer), or spends the cell on a graphics-colour
code, a new-background or a black-background code. The cost is the colour
error of every sixel, including the sixels a control cell blanks. Each row
starts as a decoder starts it - alpha white on black - so its first cell is
always a graphics-colour code.

Dithering happens before the row search and changes what it aims at: with
none, each sixel's target is its own colour; with Floyd-Steinberg or a
4 x 4 ordered (Bayer) pattern the picture is first reduced to the allowed
colours with the error spread, and the search then tries to reproduce that
pattern. The eight teletext colours are the corners of the RGB cube, so
with all of them allowed both dithers are per-channel thresholds.

Sizing: a sixel is 6 x 6.67 dots of a 480 x 500 page that a television
stretches 1.2 across, so on screen it is 1.08 times as wide as it is high;
the picture is fitted (or filled, or stretched) with that in mind so a
circle stays round on the set.

Pillow loads and resizes the picture; nothing here needs Qt.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .render import PALETTE

SIXEL_ASPECT = (6 * 1.2) / (20 / 3)        # on-screen width / height of one sixel
DITHERS = ("none", "floyd", "bayer")
FITS = ("fit", "fill", "stretch")
_BAYER4 = (np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]], np.float32) + 0.5) / 16


@dataclass
class Options:
    width: int = 40                 # cells across, including the colour-code cells
    height: int = 23                # character rows
    dither: str = "none"
    fit: str = "fit"
    colours: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 6, 7)   # allowed (0 black is the background)
    backgrounds: bool = True        # allow new/black background codes
    separated: bool = False
    brightness: float = 0.0         # -1..1 added before quantising
    contrast: float = 1.0


@dataclass
class Result:
    rows: list[bytearray]           # height rows of width codes
    target: np.ndarray              # [3h, 2w, 3] the sixel image aimed at (after dither)
    error: float                    # mean squared colour error per sixel, 0..1
    codes_spent: int = 0            # control cells used
    notes: list[str] = field(default_factory=list)


def sixel_image(img, opts: Options) -> np.ndarray:
    """The picture as a [3*height, 2*width, 3] float array 0-255 at sixel
    resolution, sized as `opts.fit` asks."""
    from PIL import Image
    if not isinstance(img, Image.Image):
        img = Image.fromarray(np.asarray(img, np.uint8))
    img = img.convert("RGB")
    W, H = 2 * opts.width, 3 * opts.height
    iw, ih = img.size
    if opts.fit == "stretch":
        out = img.resize((W, H), Image.Resampling.LANCZOS)
        return np.asarray(out, np.float32)
    # Work in "screen" units: the target box is W*SIXEL_ASPECT wide.
    bw, bh = W * SIXEL_ASPECT, H
    s = min(bw / iw, bh / ih) if opts.fit == "fit" else max(bw / iw, bh / ih)
    nw = max(1, int(round(iw * s / SIXEL_ASPECT)))
    nh = max(1, int(round(ih * s)))
    scaled = img.resize((nw, nh), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (W, H), (0, 0, 0))
    canvas.paste(scaled, ((W - nw) // 2, (H - nh) // 2))
    return np.asarray(canvas, np.float32)


def _nearest(px: np.ndarray, allowed: np.ndarray) -> np.ndarray:
    d = ((px[..., None, :] - PALETTE[allowed].astype(np.float32)) ** 2).sum(-1)
    return allowed[np.argmin(d, axis=-1)]


def dither(rgb: np.ndarray, mode: str, allowed: tuple[int, ...]) -> np.ndarray:
    """[h, w] palette indices."""
    allowed_a = np.array(sorted(set(allowed) | {0}), np.int64)
    if mode == "bayer":
        h, w = rgb.shape[:2]
        t = np.tile(_BAYER4, (h // 4 + 1, w // 4 + 1))[:h, :w] * 255.0
        if len(allowed_a) == 8:
            bits = rgb > t[..., None]
            return (bits[..., 0] * 1 + bits[..., 1] * 2 + bits[..., 2] * 4).astype(np.int64)
        # A subset: push each pixel by the pattern, then take the nearest.
        return _nearest(np.clip(rgb + (t[..., None] - 127.5) * 0.6, 0, 255), allowed_a)
    if mode == "floyd":
        a = rgb.astype(np.float32).copy()
        h, w = a.shape[:2]
        out = np.zeros((h, w), np.int64)
        pal = PALETTE[allowed_a].astype(np.float32)
        for y in range(h):
            for x in range(w):
                old = a[y, x]
                k = int(np.argmin(((pal - old) ** 2).sum(1)))
                out[y, x] = allowed_a[k]
                err = old - pal[k]
                if x + 1 < w:
                    a[y, x + 1] += err * (7 / 16)
                if y + 1 < h:
                    if x > 0:
                        a[y + 1, x - 1] += err * (3 / 16)
                    a[y + 1, x] += err * (5 / 16)
                    if x + 1 < w:
                        a[y + 1, x + 1] += err * (1 / 16)
        return out
    return _nearest(rgb, allowed_a)


def _row_codes(C: np.ndarray, fgs: list[int], bgs: list[int], backgrounds: bool, first: int):
    """Viterbi over one character row. C: [cells, 6 sixels, 8 colours] cost.
    Returns (codes for the cells from `first`, total cost)."""
    n = C.shape[0]
    INF = np.float64(1e18)
    # mosaic cost for every (fg, bg), and the bits it would light
    pair = np.minimum(C[:, :, :, None], C[:, :, None, :])          # [n, 6, f, b]
    M = pair.sum(1)                                                 # [n, f, b]
    K = C.sum(1)                                                    # [n, colour]: a cell all background
    fg_ok = np.zeros(8, bool)
    fg_ok[fgs] = True
    bg_ok = np.zeros(8, bool)
    bg_ok[bgs] = True
    valid = fg_ok[:, None] & bg_ok[None, :]
    cost = np.full((8, 8), INF)
    back: list[np.ndarray] = []         # per cell: [f, b] -> (kind, prev f, prev b, code)
    # cell `first`: the row is in alpha mode, so it must be a graphics colour code
    j = first
    step = np.empty((8, 8, 4), np.int64)
    step[:] = -1
    for f in fgs:
        cost[f, 0] = K[j, 0]
        step[f, 0] = (1, 0, 0, 0x10 | f)
    back.append(step)
    for j in range(first + 1, n):
        new = np.full((8, 8), INF)
        step = np.full((8, 8, 4), -1, np.int64)
        # 1) mosaic in the current colours
        c1 = cost + M[j]
        better = c1 < new
        new = np.where(better, c1, new)
        ff, bb = np.meshgrid(np.arange(8), np.arange(8), indexing="ij")
        step[better] = np.stack([np.zeros_like(ff), ff, bb, np.full_like(ff, -1)], -1)[better]
        # 2) graphics colour code: any f -> f', background unchanged; the cell shows background
        best_f = np.argmin(cost, axis=0)                            # per b
        best_c = cost[best_f, np.arange(8)] + K[j]                   # [b]
        for f2 in fgs:
            c2 = best_c                                             # [b]
            upd = c2 < new[f2]
            new[f2] = np.where(upd, c2, new[f2])
            for b in np.nonzero(upd)[0]:
                step[f2, b] = (1, best_f[b], b, 0x10 | f2)
        if backgrounds:
            # 3) new background: set-at, the background becomes the foreground in this cell
            for f in fgs:
                if not bg_ok[f]:
                    continue
                b0 = int(np.argmin(cost[f]))
                c3 = cost[f, b0] + K[j, f]
                if c3 < new[f, f]:
                    new[f, f] = c3
                    step[f, f] = (2, f, b0, 0x1D)
            # 4) black background: set-at
            for f in fgs:
                b0 = int(np.argmin(cost[f]))
                c4 = cost[f, b0] + K[j, 0]
                if c4 < new[f, 0]:
                    new[f, 0] = c4
                    step[f, 0] = (3, f, b0, 0x1C)
        new[~valid] = INF
        cost = new
        back.append(step)
    f, b = np.unravel_index(int(np.argmin(cost)), cost.shape)
    total = float(cost[f, b])
    codes = []
    for j in range(n - 1, first - 1, -1):
        kind, pf, pb, code = back[j - first][f, b]
        if kind == 0:
            bits = 0x20
            for s, bit in enumerate((0x01, 0x02, 0x04, 0x08, 0x10, 0x40)):
                if C[j, s, f] <= C[j, s, b] and f != b:
                    bits |= bit
            codes.append(bits)
        else:
            codes.append(int(code))
        f, b = int(pf), int(pb)
    codes.reverse()
    return codes, total


def convert(img, opts: Options | None = None) -> Result:
    opts = opts or Options()
    rgb = sixel_image(img, opts)
    if opts.contrast != 1.0 or opts.brightness:
        rgb = np.clip((rgb - 127.5) * opts.contrast + 127.5 + 255 * opts.brightness, 0, 255)
    allowed = tuple(sorted(set(opts.colours) | {0}))
    fgs = [c for c in allowed if c != 0] or [7]
    bgs = list(allowed) if opts.backgrounds else [0]
    pal = PALETTE.astype(np.float32)
    if opts.dither in ("floyd", "bayer"):
        q = dither(rgb, opts.dither, allowed)
        target = pal[q]
    else:
        target = rgb
    H, W = target.shape[:2]
    # cost of showing colour k for each sixel: [H, W, 8], scaled 0..1
    cost = ((target[..., None, :] - pal[None, None]) ** 2).sum(-1) / (3 * 255.0 ** 2)
    first = 1 if opts.separated else 0
    rows = []
    total = 0.0
    spent = 0
    for r in range(opts.height):
        blk = cost[3 * r:3 * r + 3]                                 # [3, W, 8]
        C = blk.reshape(3, opts.width, 2, 8).transpose(1, 0, 2, 3).reshape(opts.width, 6, 8)
        codes, t = _row_codes(C, fgs, bgs, opts.backgrounds, first)
        row = bytearray([0x1A] if opts.separated else []) + bytearray(codes)
        spent += sum(1 for c in row if c < 0x20)
        rows.append(row)
        total += t + (float(C[0, :, 0].sum()) if opts.separated else 0.0)   # the 0x1A cell shows black
    err = total / (H * W)
    return Result(rows=rows, target=target.astype(np.uint8), error=float(err), codes_spent=spent)


def load_image(path):
    from PIL import Image
    im = Image.open(path)
    im.load()
    return im
