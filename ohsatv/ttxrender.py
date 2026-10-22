"""A teletext page as a picture: the Level 1 display (EN 300 706 clause
12.2, "spacing attributes"), drawn with the built-in character set
(ohsatv/ttxfont.py) into an RGB array. No Qt here: the window shows the
array, and tests read it.

The rules that make Level 1 what it is, all here:

- A control code (00-1F) takes a character cell. It shows as a space, in
  the background colour in force - unless mosaics are held (below).
- Some attributes act from the cell they are in ("set-at": steady, normal
  size, conceal, contiguous and separated mosaics, black background, new
  background, hold mosaics), the rest from the next cell ("set-after":
  the alphanumeric and mosaic colours, flash, double height, release
  mosaics, the box codes, ESC). Every row starts as white alphanumerics on
  black, steady, normal size, contiguous, not concealed, not held.
- New background (1D) takes the current foreground colour as background.
- In mosaic mode 20-3F and 60-7F are block graphics; 40-5F stay capitals
  ("blast-through").
- Hold mosaics (1E): a control code's cell shows the last mosaic
  character drawn, in its own contiguous/separated form, instead of a
  space. The held character is forgotten at a change of size or between
  alphanumerics and mosaics, and at the start of each row.
- Double height (0D): the characters after it are drawn twice as tall,
  their lower halves in the row below; that row's own characters are then
  not shown - it takes the row above's backgrounds. Not in row 0 or 23-24.
- Flash: shown in the "on" phase only (the window alternates ~1 s);
  conceal: shown only when revealed.
- Boxing (0A end, 0B start; both set-after) matters only to newsflash and
  subtitle pages (C5 / C6) shown over a picture: `render(boxed_only=True)`
  gives RGBA with everything outside the boxes clear - the subtitle
  overlay. Shown as a page, a box draws like any other text.
"""
from __future__ import annotations

import numpy as np

from . import ttxfont

CW, CH = ttxfont.CELL_W, ttxfont.CELL_H
COLS, ROWS = 40, 25
WIDTH, HEIGHT = COLS * CW, ROWS * CH          # 480 x 500

# The eight colours: black, red, green, yellow, blue, magenta, cyan, white.
PALETTE = np.array([[0, 0, 0], [255, 0, 0], [0, 255, 0], [255, 255, 0], [0, 0, 255], [255, 0, 255],
                    [0, 255, 255], [255, 255, 255]], np.uint8)


def layout(rows) -> list[list[dict]]:
    """The attribute decoder: 25 rows of 40 codes -> for every cell what to
    draw: {"g": glyph code, "kind": "alpha" | "mosaic" | "space", "sep",
    "fg", "bg", "flash", "conceal", "dh": None | "top" | "bottom"}.
    Separated from drawing so tests can read what Level 1 decided."""
    out: list[list[dict]] = []
    lower = None                    # the row above's cells, when it had double height
    for y in range(ROWS):
        r = bytes(rows[y] if y < len(rows) and rows[y] is not None else b"")[:COLS].ljust(COLS, b" ")
        if lower is not None:
            row = []
            for c in lower:
                cell = dict(c, dh="bottom") if c["dh"] == "top" else dict(c, kind="space", dh=None)
                row.append(cell)
            out.append(row)
            lower = None
            continue
        fg, bg = 7, 0
        mosaic = sep = flash = conceal = dh = hold = box = False
        held, held_sep = 0x20, False
        row = []
        any_dh = False
        for x in range(COLS):
            b = r[x] & 0x7F
            # set-at
            if b == 0x09:
                flash = False
            elif b == 0x0C:
                if dh:
                    held, held_sep = 0x20, False
                dh = False
            elif b == 0x18:
                conceal = True
            elif b == 0x19:
                sep = False
            elif b == 0x1A:
                sep = True
            elif b == 0x1C:
                bg = 0
            elif b == 0x1D:
                bg = fg
            elif b == 0x1E:
                hold = True
            # what this cell shows
            if b < 0x20:
                if hold and mosaic:
                    cell = {"g": held, "kind": "mosaic", "sep": held_sep}
                else:
                    cell = {"g": 0x20, "kind": "space", "sep": False}
            elif mosaic and ttxfont.is_mosaic_code(b):
                cell = {"g": b, "kind": "mosaic", "sep": sep}
                held, held_sep = b, sep
            else:
                cell = {"g": b, "kind": "alpha", "sep": False}
            cell.update(fg=fg, bg=bg, flash=flash, conceal=conceal, dh="top" if dh else None, box=box)
            row.append(cell)
            any_dh |= dh
            # set-after
            if 0x01 <= b <= 0x07:
                fg = b
                if mosaic:
                    held, held_sep = 0x20, False
                mosaic, conceal = False, False
            elif 0x11 <= b <= 0x17:
                fg = b - 0x10
                if not mosaic:
                    held, held_sep = 0x20, False
                mosaic, conceal = True, False
            elif b == 0x08:
                flash = True
            elif b == 0x0D:
                if 1 <= y <= 22:
                    if not dh:
                        held, held_sep = 0x20, False
                    dh = True
            elif b == 0x1F:
                hold = False
            elif b == 0x0B:
                box = True
            elif b == 0x0A:
                box = False
        out.append(row)
        if any_dh:
            lower = row
    return out


def render(rows, reveal: bool = False, flash_on: bool = True, boxed_only: bool = False) -> np.ndarray:
    """25 rows of 40 codes (bytes; None or short = spaces) -> uint8 RGB,
    500 x 480 (25 rows of 20 pixels, 40 columns of 12). Show it at 4:3.
    `boxed_only`: RGBA, only the boxed cells drawn (subtitles, newsflash)."""
    img = np.zeros((HEIGHT, WIDTH, 4 if boxed_only else 3), np.uint8)
    for y, row in enumerate(layout(rows)):
        y0 = y * CH
        for x, c in enumerate(row):
            if boxed_only and not c["box"]:
                continue
            x0 = x * CW
            block = img[y0:y0 + CH, x0:x0 + CW]
            block[..., :3] = PALETTE[c["bg"]]
            if boxed_only:
                block[..., 3] = 255
            if c["kind"] == "space" or (c["flash"] and not flash_on) or (c["conceal"] and not reveal):
                continue
            g = ttxfont.mosaic(c["g"], c["sep"]) if c["kind"] == "mosaic" else ttxfont.alpha(c["g"])
            if c["dh"] == "top":
                g = np.repeat(g[:CH // 2], 2, axis=0)
            elif c["dh"] == "bottom":
                g = np.repeat(g[CH // 2:], 2, axis=0)
            block[g, :3] = PALETTE[c["fg"]]
    return img
