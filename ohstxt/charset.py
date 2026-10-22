"""The character set, drawn here: G0 Latin with the English national option
subset, and the G1 block mosaics. No font file, nothing fetched.

Why drawn by hand rather than taken from a system font: a teletext page is
a 40 x 25 grid of 12 x 20 cells, and what makes it look like a television
rather than a terminal is the character generator - the Mullard SAA5050's
5 x 9 dot matrix and its *character rounding*, which fills the half-dot
notch wherever two dots touch only at a corner. No installed font has
either, and an outline font scaled into 12 x 20 smears. So each glyph is a
5 x 9 dot pattern written out below, placed at column 1, row 0 of a 6 x 10
cell (the blank column on the left and row at the bottom are the
inter-character gaps), doubled to 12 x 20 and then rounded exactly as the
SAA5050 does it. The patterns are drawn to the SAA5050's proportions -
capitals seven dots high, lower case five with true descenders - and are
this program's own drawing, not a copy of the ROM.

The mosaics are generated, not drawn: a cell is 2 x 3 sixels, the columns
6 + 6 pixels and the rows 6 + 8 + 6 (the SAA5050's 3 + 4 + 3 lines,
doubled), and separated graphics take a 2-pixel gap off the left and
bottom of every sixel. Bit k of the code lights sixel k, except that the
bottom-right sixel is bit 6 (bit 5 is the 0x20 that makes the code a
mosaic at all).

English national option (ETS 300 706 table 36, the UK set): 0x23 is £,
0x24 $, 0x40 @, 0x5B ←, 0x5C ½, 0x5D →, 0x5E ↑, 0x5F #, 0x60 ―, 0x7B ¼,
0x7C ‖, 0x7D ¾, 0x7E ÷, and 0x7F is a solid block.
"""
from __future__ import annotations

import numpy as np

CELL_W, CELL_H = 12, 20

# Five dots wide, up to nine rows; rows not given are blank. '#' is a dot.
_GLYPHS: dict[int, str] = {
    0x20: "",
    0x21: "..#.. ..#.. ..#.. ..#.. ..#.. ..... ..#..",
    0x22: ".#.#. .#.#. .#.#.",
    0x23: "..##. .#..# .#... ####. .#... .#... #####",          # £
    0x24: ".###. #.#.# #.#.. .###. ..#.# #.#.# .###.",
    0x25: "##... ##..# ...#. ..#.. .#... #..## ...##",
    0x26: ".#... #.#.. #.#.. .#... #.#.# #..#. .##.#",
    0x27: "..#.. ..#.. ..#..",
    0x28: "...#. ..#.. .#... .#... .#... ..#.. ...#.",
    0x29: ".#... ..#.. ...#. ...#. ...#. ..#.. .#...",
    0x2A: "..#.. #.#.# .###. ..#.. .###. #.#.# ..#..",
    0x2B: "..... ..#.. ..#.. ##### ..#.. ..#.. .....",
    0x2C: "..... ..... ..... ..... ..... ..#.. ..#.. .#...",
    0x2D: "..... ..... ..... .###. ..... ..... .....",
    0x2E: "..... ..... ..... ..... ..... ..... ..#..",
    0x2F: "..... ....# ...#. ..#.. .#... #.... .....",
    0x30: "..#.. .#.#. #...# #...# #...# .#.#. ..#..",
    0x31: "..#.. .##.. ..#.. ..#.. ..#.. ..#.. .###.",
    0x32: ".###. #...# ....# ..##. .#... #.... #####",
    0x33: "##### ....# ...#. ..##. ....# #...# .###.",
    0x34: "...#. ..##. .#.#. #..#. ##### ...#. ...#.",
    0x35: "##### #.... ####. ....# ....# #...# .###.",
    0x36: "..##. .#... #.... ####. #...# #...# .###.",
    0x37: "##### ....# ...#. ..#.. .#... .#... .#...",
    0x38: ".###. #...# #...# .###. #...# #...# .###.",
    0x39: ".###. #...# #...# .#### ....# ...#. .##..",
    0x3A: "..... ..... ..#.. ..... ..... ..... ..#..",
    0x3B: "..... ..... ..#.. ..... ..... ..#.. ..#.. .#...",
    0x3C: "...#. ..#.. .#... #.... .#... ..#.. ...#.",
    0x3D: "..... ..... ##### ..... ##### ..... .....",
    0x3E: ".#... ..#.. ...#. ....# ...#. ..#.. .#...",
    0x3F: ".###. #...# ...#. ..#.. ..#.. ..... ..#..",
    0x40: ".###. #...# #.### #.#.# #.### #.... .###.",
    0x41: "..#.. .#.#. #...# #...# ##### #...# #...#",
    0x42: "####. #...# #...# ####. #...# #...# ####.",
    0x43: ".###. #...# #.... #.... #.... #...# .###.",
    0x44: "####. #...# #...# #...# #...# #...# ####.",
    0x45: "##### #.... #.... ####. #.... #.... #####",
    0x46: "##### #.... #.... ####. #.... #.... #....",
    0x47: ".###. #...# #.... #.... #..## #...# .####",
    0x48: "#...# #...# #...# ##### #...# #...# #...#",
    0x49: ".###. ..#.. ..#.. ..#.. ..#.. ..#.. .###.",
    0x4A: "....# ....# ....# ....# ....# #...# .###.",
    0x4B: "#...# #..#. #.#.. ##... #.#.. #..#. #...#",
    0x4C: "#.... #.... #.... #.... #.... #.... #####",
    0x4D: "#...# ##.## #.#.# #.#.# #...# #...# #...#",
    0x4E: "#...# #...# ##..# #.#.# #..## #...# #...#",
    0x4F: ".###. #...# #...# #...# #...# #...# .###.",
    0x50: "####. #...# #...# ####. #.... #.... #....",
    0x51: ".###. #...# #...# #...# #.#.# #..#. .##.#",
    0x52: "####. #...# #...# ####. #.#.. #..#. #...#",
    0x53: ".###. #...# #.... .###. ....# #...# .###.",
    0x54: "##### ..#.. ..#.. ..#.. ..#.. ..#.. ..#..",
    0x55: "#...# #...# #...# #...# #...# #...# .###.",
    0x56: "#...# #...# #...# .#.#. .#.#. ..#.. ..#..",
    0x57: "#...# #...# #...# #.#.# #.#.# #.#.# .#.#.",
    0x58: "#...# #...# .#.#. ..#.. .#.#. #...# #...#",
    0x59: "#...# #...# .#.#. ..#.. ..#.. ..#.. ..#..",
    0x5A: "##### ....# ...#. ..#.. .#... #.... #####",
    0x5B: "..... ..#.. .#... ##### .#... ..#.. .....",            # ←
    0x5C: ".#... ##... .#... .#.## ...#. ..#.. .###.",            # ½
    0x5D: "..... ..#.. ...#. ##### ...#. ..#.. .....",            # →
    0x5E: "..... ..#.. .###. #.#.# ..#.. ..#.. .....",            # ↑
    0x5F: ".#.#. .#.#. ##### .#.#. ##### .#.#. .#.#.",            # #
    0x60: "..... ..... ..... ##### ..... ..... .....",            # ―
    0x61: "..... ..... .###. ....# .#### #...# .####",
    0x62: "#.... #.... ####. #...# #...# #...# ####.",
    0x63: "..... ..... .#### #.... #.... #.... .####",
    0x64: "....# ....# .#### #...# #...# #...# .####",
    0x65: "..... ..... .###. #...# ##### #.... .###.",
    0x66: "..##. .#... .#... ###.. .#... .#... .#...",
    0x67: "..... ..... .#### #...# #...# #...# .#### ....# .###.",
    0x68: "#.... #.... ####. #...# #...# #...# #...#",
    0x69: "..#.. ..... .##.. ..#.. ..#.. ..#.. .###.",
    0x6A: "..#.. ..... ..#.. ..#.. ..#.. ..#.. ..#.. ..#.. .#...",
    0x6B: ".#... .#... .#..# .#.#. .##.. .#.#. .#..#",
    0x6C: ".##.. ..#.. ..#.. ..#.. ..#.. ..#.. .###.",
    0x6D: "..... ..... ##.#. #.#.# #.#.# #.#.# #.#.#",
    0x6E: "..... ..... ####. #...# #...# #...# #...#",
    0x6F: "..... ..... .###. #...# #...# #...# .###.",
    0x70: "..... ..... ####. #...# #...# #...# ####. #.... #....",
    0x71: "..... ..... .#### #...# #...# #...# .#### ....# ....#",
    0x72: "..... ..... .#.## .##.. .#... .#... .#...",
    0x73: "..... ..... .#### #.... .###. ....# ####.",
    0x74: ".#... .#... ###.. .#... .#... .#... ..##.",
    0x75: "..... ..... #...# #...# #...# #...# .####",
    0x76: "..... ..... #...# #...# .#.#. .#.#. ..#..",
    0x77: "..... ..... #...# #...# #.#.# #.#.# .#.#.",
    0x78: "..... ..... #...# .#.#. ..#.. .#.#. #...#",
    0x79: "..... ..... #...# #...# #...# #...# .#### ....# .###.",
    0x7A: "..... ..... ##### ...#. ..#.. .#... #####",
    0x7B: "#.... #.... #.... #..#. ..##. .#.#. ....#",            # ¼
    0x7C: ".#.#. .#.#. .#.#. .#.#. .#.#. .#.#. .#.#.",            # ‖
    0x7D: "##... .#... ##... .#.#. ##.## ..#.# ....#",            # ¾
    0x7E: "..... ..#.. ..... ##### ..... ..#.. .....",            # ÷
}

# The English set's printable characters as Unicode, for text import and
# export (edit fields, clipboard, the screen reader name of a cell).
UNICODE = {c: chr(c) for c in range(0x20, 0x7F)}
UNICODE.update({0x23: "£", 0x5B: "←", 0x5C: "½", 0x5D: "→", 0x5E: "↑", 0x5F: "#", 0x60: "―", 0x7B: "¼",
                0x7C: "‖", 0x7D: "¾", 0x7E: "÷", 0x7F: "■"})
# Typed characters to codes. '#' has no home of its own in ASCII order (0x23
# is £), so typing '#' gives 0x5F, where the English set keeps it; '_' has
# no glyph at all and falls back to the same; '[' ']' '{' '}' '|' '~' and
# '^' land on whatever the English set put there, as on a real keyboard
# attached to a teletext editor.
FROM_UNICODE = {u: c for c, u in UNICODE.items()}
FROM_UNICODE.update({"#": 0x5F, "_": 0x5F, "`": 0x60, "—": 0x60, "-": 0x2D, "$": 0x24, "@": 0x40})
# The first version promised the line above and never did it: the inverted
# map only knows the English glyphs, so a typed '{' (or [ ] } | ~ ^) found
# nothing and was dropped. They take their ASCII positions, where the set
# draws ¼ ← ¾ ‖ ÷ ↑ - and live-page tokens are written with braces.
FROM_UNICODE.update({ch: ord(ch) for ch in "[]{}|~^\\"})


def _dots(code: int) -> np.ndarray:
    """6 x 10 dot matrix for a G0 code (columns 1-5, rows 0-8 drawn)."""
    m = np.zeros((10, 6), bool)
    if code == 0x7F:
        m[:, :] = True
        return m
    rows = _GLYPHS.get(code, "").split()
    for y, row in enumerate(rows[:9]):
        for x, ch in enumerate(row[:5]):
            if ch == "#":
                m[y, x + 1] = True
    return m


def round_chars(m: np.ndarray) -> np.ndarray:
    """Double a dot matrix and apply SAA5050 character rounding: where two
    dots meet only diagonally, the half-dots either side of the corner are
    filled, so a diagonal stroke reads as a line rather than a staircase."""
    h, w = m.shape
    out = np.zeros((2 * h, 2 * w), bool)
    out[0::2, 0::2] = m
    out[0::2, 1::2] = m
    out[1::2, 0::2] = m
    out[1::2, 1::2] = m
    def at(y, x):
        return 0 <= y < h and 0 <= x < w and bool(m[y, x])
    for y in range(h - 1):
        for x in range(w):
            if not m[y, x]:
                continue
            # down-right: the two dots touch only at a corner - fill the two
            # half-dots that turn the step into a diagonal
            if at(y + 1, x + 1) and not at(y, x + 1) and not at(y + 1, x):
                out[2 * y + 1, 2 * x + 2] = True
                out[2 * y + 2, 2 * x + 1] = True
            # down-left, the mirror image
            if at(y + 1, x - 1) and not at(y, x - 1) and not at(y + 1, x):
                out[2 * y + 1, 2 * x - 1] = True
                out[2 * y + 2, 2 * x] = True
    return out


def _mosaic(code: int, separated: bool) -> np.ndarray:
    m = np.zeros((CELL_H, CELL_W), bool)
    cols = ((0, 6), (6, 12))
    rows = ((0, 6), (6, 14), (14, 20))
    bits = (0x01, 0x02, 0x04, 0x08, 0x10, 0x40)
    for i, bit in enumerate(bits):
        if code & bit:
            x0, x1 = cols[i % 2]
            y0, y1 = rows[i // 2]
            if separated:
                x0 += 2
                y1 -= 2
            m[y0:y1, x0:x1] = True
    return m


def _build():
    alpha = np.zeros((128, CELL_H, CELL_W), bool)
    for c in range(0x20, 0x80):
        alpha[c] = round_chars(_dots(c))
    contig = alpha.copy()
    sep = alpha.copy()
    for c in list(range(0x20, 0x40)) + list(range(0x60, 0x80)):
        contig[c] = _mosaic(c, False)
        sep[c] = _mosaic(c, True)
    return alpha, contig, sep


# Glyph masks indexed by 7-bit code: [code, y, x]. Control codes (0x00-0x1F)
# are blank in all three; mosaic codes 0x40-0x5F are the capitals in all
# three (they "blast through" in graphics mode).
ALPHA, CONTIG, SEPARATED = _build()

SET_ALPHA, SET_CONTIG, SET_SEP = 0, 1, 2
GLYPHS = np.stack([ALPHA, CONTIG, SEPARATED])   # [set, code, y, x]


def is_mosaic_code(code: int) -> bool:
    """A code that draws sixels in graphics mode (bit 5 set: 0x20-0x3F and
    0x60-0x7F). 0x40-0x5F in graphics mode are capitals, not mosaics."""
    return 0x20 <= code < 0x80 and bool(code & 0x20)


SIXEL_BITS = (0x01, 0x02, 0x04, 0x08, 0x10, 0x40)


def sixel_bit(sx: int, sy: int) -> int:
    """The code bit for the sixel at column sx (0-1), row sy (0-2) of a cell."""
    return SIXEL_BITS[sy * 2 + sx]
