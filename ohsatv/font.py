"""A 5 x 7 bitmap font for the ident and the test card.

The engine draws the callsign into every transmitted picture, and it may not
import Qt (DESIGN.md), and the machine may have no fonts worth relying on and
no network to fetch one - so the font is here, as data. 5 x 7 is the classic
character-generator cell: legible after PAL's 5 MHz and a noisy channel at
the sizes an ident uses, which a thin vector font is not.

`render(text, height)` returns a float mask (0..1) with soft edges: glyphs
are drawn at an integer scale, then box-filtered, so the strokes do not ring
when the picture is band-limited to 5 MHz.
"""
from __future__ import annotations

import numpy as np

_GLYPHS = {
    "A": ".###.|#...#|#...#|#####|#...#|#...#|#...#",
    "B": "####.|#...#|#...#|####.|#...#|#...#|####.",
    "C": ".###.|#...#|#....|#....|#....|#...#|.###.",
    "D": "####.|#...#|#...#|#...#|#...#|#...#|####.",
    "E": "#####|#....|#....|####.|#....|#....|#####",
    "F": "#####|#....|#....|####.|#....|#....|#....",
    "G": ".###.|#...#|#....|#.###|#...#|#...#|.####",
    "H": "#...#|#...#|#...#|#####|#...#|#...#|#...#",
    "I": ".###.|..#..|..#..|..#..|..#..|..#..|.###.",
    "J": "..###|...#.|...#.|...#.|...#.|#..#.|.##..",
    "K": "#...#|#..#.|#.#..|##...|#.#..|#..#.|#...#",
    "L": "#....|#....|#....|#....|#....|#....|#####",
    "M": "#...#|##.##|#.#.#|#.#.#|#...#|#...#|#...#",
    "N": "#...#|#...#|##..#|#.#.#|#..##|#...#|#...#",
    "O": ".###.|#...#|#...#|#...#|#...#|#...#|.###.",
    "P": "####.|#...#|#...#|####.|#....|#....|#....",
    "Q": ".###.|#...#|#...#|#...#|#.#.#|#..#.|.##.#",
    "R": "####.|#...#|#...#|####.|#.#..|#..#.|#...#",
    "S": ".####|#....|#....|.###.|....#|....#|####.",
    "T": "#####|..#..|..#..|..#..|..#..|..#..|..#..",
    "U": "#...#|#...#|#...#|#...#|#...#|#...#|.###.",
    "V": "#...#|#...#|#...#|#...#|#...#|.#.#.|..#..",
    "W": "#...#|#...#|#...#|#.#.#|#.#.#|#.#.#|.#.#.",
    "X": "#...#|#...#|.#.#.|..#..|.#.#.|#...#|#...#",
    "Y": "#...#|#...#|.#.#.|..#..|..#..|..#..|..#..",
    "Z": "#####|....#|...#.|..#..|.#...|#....|#####",
    "0": ".###.|#...#|#..##|#.#.#|##..#|#...#|.###.",
    "1": "..#..|.##..|..#..|..#..|..#..|..#..|.###.",
    "2": ".###.|#...#|....#|...#.|..#..|.#...|#####",
    "3": "#####|...#.|..#..|...#.|....#|#...#|.###.",
    "4": "...#.|..##.|.#.#.|#..#.|#####|...#.|...#.",
    "5": "#####|#....|####.|....#|....#|#...#|.###.",
    "6": "..##.|.#...|#....|####.|#...#|#...#|.###.",
    "7": "#####|....#|...#.|..#..|.#...|.#...|.#...",
    "8": ".###.|#...#|#...#|.###.|#...#|#...#|.###.",
    "9": ".###.|#...#|#...#|.####|....#|...#.|.##..",
    "-": ".....|.....|.....|#####|.....|.....|.....",
    "/": "....#|....#|...#.|..#..|.#...|#....|#....",
    ".": ".....|.....|.....|.....|.....|.##..|.##..",
    ":": ".....|.##..|.##..|.....|.##..|.##..|.....",
    "#": ".#.#.|.#.#.|#####|.#.#.|#####|.#.#.|.#.#.",
    "?": ".###.|#...#|....#|...#.|..#..|.....|..#..",
    "!": "..#..|..#..|..#..|..#..|..#..|.....|..#..",
    "+": ".....|..#..|..#..|#####|..#..|..#..|.....",
    "=": ".....|.....|#####|.....|#####|.....|.....",
    "(": "...#.|..#..|.#...|.#...|.#...|..#..|...#.",
    ")": ".#...|..#..|...#.|...#.|...#.|..#..|.#...",
    "'": "..#..|..#..|.#...|.....|.....|.....|.....",
    ",": ".....|.....|.....|.....|.##..|..#..|.#...",
    "&": ".##..|#..#.|#.#..|.#...|#.#.#|#..#.|.##.#",
    "@": ".###.|#...#|#.###|#.#.#|#.###|#....|.###.",
    " ": ".....|.....|.....|.....|.....|.....|.....",
}

GLYPH_W, GLYPH_H = 5, 7


def _bitmap(ch: str) -> np.ndarray:
    rows = _GLYPHS.get(ch.upper(), _GLYPHS["?"]).split("|")
    return np.array([[c == "#" for c in r] for r in rows], np.float32)


def supported(ch: str) -> bool:
    return ch.upper() in _GLYPHS


def render(text: str, height: int, spacing: float = 1.0) -> np.ndarray:
    """Text as a float mask `height` rows tall (7 font rows plus no margin),
    with `spacing` font pixels between characters. Lower case is drawn as
    upper case - idents are callsigns."""
    scale = max(1, int(round(height / GLYPH_H)))
    gap = max(0, int(round(spacing * scale)))
    cells = []
    for i, ch in enumerate(text):
        g = np.kron(_bitmap(ch), np.ones((scale, scale), np.float32))
        cells.append(g)
        if i != len(text) - 1 and gap:
            cells.append(np.zeros((g.shape[0], gap), np.float32))
    if not cells:
        return np.zeros((GLYPH_H * scale, 1), np.float32)
    m = np.concatenate(cells, axis=1)
    # Soften by a 3-tap box both ways: kills the stair-step harmonics that
    # would ring after a 5 MHz video filter, keeps strokes solid.
    p = np.pad(m, 1)
    m = (p[:-2, 1:-1] + p[1:-1, 1:-1] + p[2:, 1:-1]) / 3
    p = np.pad(m, ((0, 0), (1, 1)))
    m = (p[:, :-2] + p[:, 1:-1] + p[:, 2:]) / 3
    return m


def width(text: str, height: int, spacing: float = 1.0) -> int:
    return render(text, height, spacing).shape[1]


def draw(img: np.ndarray, text: str, x: int, y: int, height: int, colour, spacing: float = 1.0,
         align: str = "left") -> tuple[int, int]:
    """Composite `text` into `img` (H x W x 3 float) at (x, y) top-left (or
    centred on x with align="centre"); clipped to the image. Returns the
    drawn (width, height)."""
    m = render(text, height, spacing)
    h, w = m.shape
    if align == "centre":
        x = int(round(x - w / 2))
    elif align == "right":
        x = int(round(x - w))
    y0, x0 = max(0, y), max(0, x)
    y1, x1 = min(img.shape[0], y + h), min(img.shape[1], x + w)
    if y1 <= y0 or x1 <= x0:
        return w, h
    sub = m[y0 - y:y1 - y, x0 - x:x1 - x][..., None]
    col = np.asarray(colour, np.float32)
    img[y0:y1, x0:x1] = img[y0:y1, x0:x1] * (1 - sub) + col * sub
    return w, h
