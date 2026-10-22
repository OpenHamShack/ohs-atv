"""The teletext character set, generated in code: no font file, nothing
fetched (DESIGN.md: no external assets).

A teletext set drew its characters as 5 x 9 dots in a 6 x 10 cell (the
Mullard SAA5050 and its kind) and doubled every dot for the screen,
smoothing diagonals by "character rounding" as it did - half-dots where a
stroke steps diagonally, so an 'O' or a '/' is not a staircase. This does
the same: each glyph is defined on the 5 x 9 grid (the capitals and digits
are font.py's 5 x 7 ident glyphs, which are the same style), placed in a
6 x 10 cell with a blank top row and right column, doubled to 12 x 20, and
rounded.

Mosaics (the G1 block graphics) are the 2 x 3 sixels of the cell, 6 / 8 / 6
of its 20 rows (the SAA5050's 3 / 4 / 3 lines doubled); separated mosaics
leave a gap of two pixels at the left and bottom of each sixel.

The G0 set is Latin with the English national option (EN 300 706 clause
15.6.2: 23 is '£', 5B-5E arrows and a half, 5F '#', 60 a long dash, 7B-7E
quarter, double bar, three quarters, divide; 7F a full block). Other
national options (C12-C14) are drawn as English: which of their letters
differ is in the clause's table, and nobody has asked for them.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np

from .font import _GLYPHS as _IDENT

CELL_W, CELL_H = 12, 20               # pixels of a cell on the screen

# 5 x 9: rows 0-6 are capital height, 7-8 the descenders. Characters not
# here and in font.py's set take that (7 rows, padded); anything else is a
# box (so a missing glyph shows as one rather than silently as a space).
_G0 = {
    0x20: "", 0x22: ".#.#.|.#.#.|.#.#.", 0x23: "..##.|.#..#|.#...|###..|.#...|.#...|#####",
    0x24: ".###.|#.#.#|#.#..|.###.|..#.#|#.#.#|.###.", 0x25: "##...|##..#|...#.|..#..|.#...|#..##|...##",
    0x27: "..#..|..#..|.#...", 0x2D: ".....|.....|.....|.###.", 0x2A: "..#..|#.#.#|.###.|..#..|.###.|#.#.#|..#..",
    0x2C: ".....|.....|.....|.....|.....|..#..|..#..|.#...", 0x2E: ".....|.....|.....|.....|.....|.....|..#..",
    0x2F: ".....|....#|...#.|..#..|.#...|#....|.....", 0x3A: ".....|.....|..#..|.....|.....|.....|..#..",
    0x3B: ".....|.....|..#..|.....|.....|..#..|..#..|.#...", 0x3C: "...#.|..#..|.#...|#....|.#...|..#..|...#.",
    0x3E: ".#...|..#..|...#.|....#|...#.|..#..|.#...",
    0x5B: ".....|..#..|.#...|#####|.#...|..#..|.....", 0x5C: "#....|#....|#..##|#...#|...#.|..#..|..###",
    0x5D: ".....|..#..|...#.|#####|...#.|..#..|.....", 0x5E: ".....|..#..|.###.|#.#.#|..#..|..#..|.....",
    0x5F: ".#.#.|.#.#.|#####|.#.#.|#####|.#.#.|.#.#.", 0x60: ".....|.....|.....|#####",
    0x61: ".....|.....|.###.|....#|.####|#...#|.####", 0x62: "#....|#....|####.|#...#|#...#|#...#|####.",
    0x63: ".....|.....|.####|#....|#....|#....|.####", 0x64: "....#|....#|.####|#...#|#...#|#...#|.####",
    0x65: ".....|.....|.###.|#...#|#####|#....|.###.", 0x66: "..##.|.#...|####.|.#...|.#...|.#...|.#...",
    0x67: ".....|.....|.####|#...#|#...#|#...#|.####|....#|.###.",
    0x68: "#....|#....|####.|#...#|#...#|#...#|#...#", 0x69: "..#..|.....|.##..|..#..|..#..|..#..|.###.",
    0x6A: "...#.|.....|..##.|...#.|...#.|...#.|...#.|#..#.|.##..",
    0x6B: ".#...|.#...|.#..#|.#.#.|.##..|.#.#.|.#..#", 0x6C: ".##..|..#..|..#..|..#..|..#..|..#..|.###.",
    0x6D: ".....|.....|##.#.|#.#.#|#.#.#|#.#.#|#.#.#", 0x6E: ".....|.....|####.|#...#|#...#|#...#|#...#",
    0x6F: ".....|.....|.###.|#...#|#...#|#...#|.###.",
    0x70: ".....|.....|####.|#...#|#...#|#...#|####.|#....|#....",
    0x71: ".....|.....|.####|#...#|#...#|#...#|.####|....#|....#",
    0x72: ".....|.....|.#.##|.##..|.#...|.#...|.#...", 0x73: ".....|.....|.####|#....|.###.|....#|####.",
    0x74: ".#...|.#...|####.|.#...|.#...|.#...|..##.", 0x75: ".....|.....|#...#|#...#|#...#|#...#|.####",
    0x76: ".....|.....|#...#|#...#|.#.#.|.#.#.|..#..", 0x77: ".....|.....|#...#|#...#|#.#.#|#.#.#|.#.#.",
    0x78: ".....|.....|#...#|.#.#.|..#..|.#.#.|#...#",
    0x79: ".....|.....|#...#|#...#|#...#|#...#|.####|....#|.###.",
    0x7A: ".....|.....|#####|...#.|..#..|.#...|#####",
    0x7B: "#....|#....|#....|#..#.|..##.|.####|...#.", 0x7C: ".#.#.|.#.#.|.#.#.|.#.#.|.#.#.|.#.#.|.#.#.",
    0x7D: "##...|..#..|##...|..##.|##.#.|.####|...#.", 0x7E: ".....|..#..|.....|#####|.....|..#..|.....",
}


def _rows(code: int) -> list[str]:
    if code in _G0:
        r = [x for x in _G0[code].split("|") if x] if _G0[code] else []
    else:
        ch = chr(code)
        if ch in _IDENT:
            r = _IDENT[ch].split("|")
        else:
            r = ["#####", "#...#", "#...#", "#...#", "#...#", "#...#", "#####"]
    r = r + ["....."] * (9 - len(r))
    return r[:9]


def _round(dots: np.ndarray) -> np.ndarray:
    """6 x 10 dots -> 12 x 20 pixels with character rounding: where two
    dots touch only diagonally, the two half-dots in the corner between
    them are filled."""
    big = np.kron(dots, np.ones((2, 2), bool))
    h, w = dots.shape
    for y in range(h - 1):
        for x in range(w - 1):
            a, b, c, d = dots[y, x], dots[y, x + 1], dots[y + 1, x], dots[y + 1, x + 1]
            if a and d and not b and not c:          # "\"
                big[2 * y + 1, 2 * x + 2] = True
                big[2 * y + 2, 2 * x + 1] = True
            if b and c and not a and not d:          # "/"
                big[2 * y + 1, 2 * x + 1] = True
                big[2 * y + 2, 2 * x + 2] = True
    return big


@lru_cache(maxsize=None)
def alpha(code: int) -> np.ndarray:
    """The G0 character `code` (20-7F) as a 20 x 12 bool array."""
    code &= 0x7F
    if code == 0x7F:
        return np.ones((CELL_H, CELL_W), bool)
    if code < 0x20:
        return np.zeros((CELL_H, CELL_W), bool)
    dots = np.zeros((10, 6), bool)
    for y, row in enumerate(_rows(code)):
        dots[y + 1, :5] = [c == "#" for c in row]
    out = _round(dots)
    out.setflags(write=False)
    return out


# Sixel rows: 6, 8 and 6 pixels of the 20.
_SIXEL_Y = ((0, 6), (6, 14), (14, 20))


@lru_cache(maxsize=None)
def mosaic(code: int, separated: bool = False) -> np.ndarray:
    """The G1 mosaic `code` (20-3F, 60-7F): bit 0 top left, 1 top right,
    2 middle left, 3 middle right, 4 bottom left, 6 bottom right."""
    out = np.zeros((CELL_H, CELL_W), bool)
    bits = (0x01, 0x02, 0x04, 0x08, 0x10, 0x40)
    for i, bit in enumerate(bits):
        if not code & bit:
            continue
        y0, y1 = _SIXEL_Y[i // 2]
        x0, x1 = (0, 6) if i % 2 == 0 else (6, 12)
        if separated:
            x0 += 2
            y1 -= 2
        out[y0:y1, x0:x1] = True
    out.setflags(write=False)
    return out


def is_mosaic_code(code: int) -> bool:
    """Codes that are block graphics in mosaic mode; 40-5F stay letters
    ("blast-through" capitals)."""
    return 0x20 <= code <= 0x3F or 0x60 <= code <= 0x7F
