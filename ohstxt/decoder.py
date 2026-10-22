"""The decoder: 25 rows of codes in, what a television shows out.

There is one decoder and everything goes through it - the editor's grid,
the preview, the PNG export and the tests - so what the editor shows is
what the set shows. It works as a Level 1 decoder does (ETS 300 706
section 12.2), one row at a time from a reset state at the left, with the
spacing attributes split the way the specification splits them:

  Set-At (takes effect in the cell holding the code)
      0x09 steady, 0x0C normal height, 0x18 conceal, 0x19 contiguous,
      0x1A separated, 0x1C black background, 0x1D new background,
      0x1E hold graphics
  Set-After (takes effect from the next cell)
      0x00-0x07 alpha colours, 0x08 flash, 0x0A end box, 0x0B start box,
      0x0D double height, 0x10-0x17 graphics colours, 0x1B ESC (ignored
      here: Level 1 has one G0 set), 0x1F release graphics

and the details that make real pages look right:

- A control cell shows a space in the current background - or, under hold
  graphics in graphics mode, the *held* mosaic: the last mosaic character
  displayed in this row, in the contiguous/separated style it was drawn
  in. The held character is reset to a space at the start of the row, on
  a change between alpha and graphics mode and on a change of size, and
  not otherwise (not by a repeated colour, not by hold/release).
- 0x40-0x5F in graphics mode are capitals ("blast-through"); they do not
  become the held mosaic.
- An alpha or graphics colour code ends conceal.
- Double height takes the row below: that row's own codes are not shown;
  it shows the lower halves of the double-height characters above, and a
  space (in the background of the cell above) under normal-height ones.
  Double height has no effect in rows 0, 23 and 24 (the header, and the
  row whose lower half would land on the Fastext row).
- 0x00 alpha black and 0x10 graphics black are not colours on a Level 1
  decoder - an SAA5050 ignores them, and so does this one unless asked
  (`black_fg`, edit.tf's "render foreground black" flag): then they act as
  Level 2.5 decoders treat them.
- Boxes matter only on newsflash and subtitle pages (C5, C6): there the
  set shows the boxed cells over the television picture and nothing else,
  and suppresses the header. `mixed` asks for that view.

`decode()` returns a `Decoded` of 25 x 40 arrays; `ohstxt.render` turns it
into pixels for a given flash phase and reveal state.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .charset import SET_ALPHA, SET_CONTIG, SET_SEP, is_mosaic_code
from .page import COLS, ROWS

BLACK, RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN, WHITE = range(8)
COLOUR_NAMES = ("black", "red", "green", "yellow", "blue", "magenta", "cyan", "white")

# Names of the 32 control codes, as the code view labels them.
CONTROL_NAMES = {
    0x00: "alpha black", 0x01: "alpha red", 0x02: "alpha green", 0x03: "alpha yellow", 0x04: "alpha blue",
    0x05: "alpha magenta", 0x06: "alpha cyan", 0x07: "alpha white", 0x08: "flash", 0x09: "steady",
    0x0A: "end box", 0x0B: "start box", 0x0C: "normal height", 0x0D: "double height",
    0x0E: "double width (Level 2.5; SO at Level 1)", 0x0F: "double size (Level 2.5; SI at Level 1)",
    0x10: "graphics black", 0x11: "graphics red", 0x12: "graphics green", 0x13: "graphics yellow",
    0x14: "graphics blue", 0x15: "graphics magenta", 0x16: "graphics cyan", 0x17: "graphics white",
    0x18: "conceal", 0x19: "contiguous graphics", 0x1A: "separated graphics", 0x1B: "ESC",
    0x1C: "black background", 0x1D: "new background", 0x1E: "hold graphics", 0x1F: "release graphics",
}
# Two-character mnemonics for the code view (what is drawn in the cell).
CONTROL_SHORT = {
    **{i: "A" + "KRGYBMCW"[i] for i in range(8)},
    **{0x10 + i: "G" + "KRGYBMCW"[i] for i in range(8)},
    0x08: "Fl", 0x09: "St", 0x0A: "Eb", 0x0B: "Sb", 0x0C: "Nh", 0x0D: "Dh", 0x0E: "Dw", 0x0F: "Ds",
    0x18: "Co", 0x19: "Cg", 0x1A: "Sg", 0x1B: "Es", 0x1C: "Kb", 0x1D: "Nb", 0x1E: "Hg", 0x1F: "Rg",
}

DH_NONE, DH_TOP, DH_BOTTOM = 0, 1, 2


@dataclass
class Decoded:
    code: np.ndarray       # [25,40] the character shown (0x20 for a control cell unless a held mosaic)
    gset: np.ndarray       # [25,40] SET_ALPHA / SET_CONTIG / SET_SEP
    fg: np.ndarray         # [25,40] 0-7
    bg: np.ndarray         # [25,40] 0-7
    flash: np.ndarray      # [25,40] bool
    conceal: np.ndarray    # [25,40] bool
    dh: np.ndarray         # [25,40] DH_NONE / DH_TOP / DH_BOTTOM
    box: np.ndarray        # [25,40] bool, inside a box
    control: np.ndarray    # [25,40] bool, the cell holds a control code
    graphics: np.ndarray   # [25,40] bool, graphics mode in force for this cell's character
    hidden_row: np.ndarray  # [25] bool, the row's own codes are not shown (below a double-height row)

    def attrs_at(self, r: int, c: int) -> dict:
        return dict(fg=int(self.fg[r, c]), bg=int(self.bg[r, c]), graphics=bool(self.graphics[r, c]),
                    separated=int(self.gset[r, c]) == SET_SEP, flash=bool(self.flash[r, c]),
                    conceal=bool(self.conceal[r, c]), dh=int(self.dh[r, c]), box=bool(self.box[r, c]),
                    control=bool(self.control[r, c]))


def _empty() -> Decoded:
    z = lambda dt=np.uint8: np.zeros((ROWS, COLS), dt)  # noqa: E731
    return Decoded(code=np.full((ROWS, COLS), 0x20, np.uint8), gset=z(), fg=np.full((ROWS, COLS), WHITE, np.uint8),
                   bg=z(), flash=z(bool), conceal=z(bool), dh=z(), box=z(bool), control=z(bool),
                   graphics=z(bool), hidden_row=np.zeros(ROWS, bool))


def decode_row(row, out: Decoded, r: int, black_fg: bool = False, allow_dh: bool = True) -> bool:
    """Decode one row into out[r]. Returns True if a double-height code took
    effect in it (so the next row is its lower half)."""
    fg, bg = WHITE, BLACK
    graphics = separated = hold = flash = conceal = dh = box = False
    held, held_sep = 0x20, False
    any_dh = False
    for col in range(COLS):
        c = row[col] & 0x7F
        if c < 0x20:
            # --- Set-At: these change this very cell
            if c == 0x09:
                flash = False
            elif c == 0x0C:
                if dh:
                    held, held_sep = 0x20, False
                dh = False
            elif c == 0x18:
                conceal = True
            elif c == 0x19:
                separated = False
            elif c == 0x1A:
                separated = True
            elif c == 0x1C:
                bg = BLACK
            elif c == 0x1D:
                bg = fg
            elif c == 0x1E:
                hold = True
            # --- the cell: a space, or the held mosaic
            if hold and graphics:
                out.code[r, col] = held
                out.gset[r, col] = SET_SEP if held_sep else SET_CONTIG
            else:
                out.code[r, col] = 0x20
                out.gset[r, col] = SET_ALPHA
            out.control[r, col] = True
        else:
            if graphics and is_mosaic_code(c):
                out.gset[r, col] = SET_SEP if separated else SET_CONTIG
                held, held_sep = c, separated
            else:
                out.gset[r, col] = SET_ALPHA
            out.code[r, col] = c
        out.fg[r, col], out.bg[r, col] = fg, bg
        out.flash[r, col], out.conceal[r, col] = flash, conceal
        out.dh[r, col] = DH_TOP if dh else DH_NONE
        out.box[r, col] = box
        out.graphics[r, col] = graphics
        if c >= 0x20:
            continue
        # --- Set-After: these change the cells that follow
        if c <= 0x07 or 0x10 <= c <= 0x17:
            if (c & 7) == BLACK and not black_fg:
                continue          # Level 1: no black foreground; the code does nothing
            want_graphics = c >= 0x10
            if want_graphics != graphics:
                held, held_sep = 0x20, False
            graphics = want_graphics
            fg = c & 7
            conceal = False
        elif c == 0x08:
            flash = True
        elif c == 0x0A:
            box = False
        elif c == 0x0B:
            box = True
        elif c == 0x0D:
            if allow_dh:
                if not dh:
                    held, held_sep = 0x20, False
                dh = True
                any_dh = True
        elif c == 0x1F:
            hold = False
    return any_dh


def decode(rows, black_fg: bool = False) -> Decoded:
    """Decode 25 rows (each 40 codes) as a Level 1 decoder shows them."""
    out = _empty()
    r = 0
    while r < ROWS:
        dh = decode_row(rows[r], out, r, black_fg=black_fg, allow_dh=1 <= r <= 22)
        if dh and r + 1 < ROWS:
            # The row below shows the lower halves; its own codes are ignored.
            n = r + 1
            for name in ("code", "gset", "fg", "bg", "flash", "conceal", "box", "control", "graphics"):
                getattr(out, name)[n] = getattr(out, name)[r]
            top = out.dh[r] == DH_TOP
            out.dh[n] = np.where(top, DH_BOTTOM, DH_NONE)
            out.code[n] = np.where(top, out.code[r], 0x20)
            out.gset[n] = np.where(top, out.gset[r], SET_ALPHA)
            out.hidden_row[n] = True
            r += 2
        else:
            r += 1
    return out


def mode_at(rows, r: int, c: int, black_fg: bool = False) -> dict:
    """The attributes in force at a cell - what a character typed there
    would be drawn with. For the sixel tools: is this cell a mosaic?"""
    d = decode(rows, black_fg=black_fg)
    return d.attrs_at(r, c)
