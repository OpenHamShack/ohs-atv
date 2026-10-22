"""Editing, without Qt: the cursor, type-over, control codes, sixel drawing,
subpages, Fastext labels, undo and redo.

Everything the window does to a page goes through a `Document`, so the
tests can drive the same operations the keyboard and mouse do, and undo
covers all of them. Undo keeps whole-page snapshots rather than inverse
operations: a page is a few kilobytes, a snapshot cannot be wrong about
what an operation touched (a sixel stroke may insert colour codes, a
subpage delete moves every index), and two hundred of them is nothing.

Sixel drawing works on the 80 x 75 grid of sixels and has to respect the
fact that a sixel can only be lit in a cell that is a mosaic: a control
cell has none, and an alpha-mode cell draws letters. `set_sixel` therefore
does what the codes allow and says why when it cannot:

1. a control cell, or a capital in graphics mode, is refused (the stroke
   carries on and the refusals are counted for the status line);
2. a cell already in graphics mode in the pen's colour (or with the pen on
   "keep colour") just has its bit changed;
3. otherwise the cell needs a graphics-colour code to its left. If the cell
   to the left is blank it becomes that code - and if that would change how
   anything further along the row is drawn (text after it turning into
   mosaics, another colour run changing), the cell to the right is used
   for a code that puts back what was there, if it is blank; if not, the
   sixel is refused rather than repaint the row behind the operator's back.

The check in 3 is done by decoding the row before and after and comparing
every other cell, so it holds for any combination of attributes, not only
the cases thought of here.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np

from . import charset, decoder
from .page import COLS, NO_LINK, ROWS, Page, Subpage, annex_a_subcode

SX, SY = COLS * 2, ROWS * 3          # the sixel grid: 80 x 75
UNDO_DEPTH = 200
PEN_KEEP = -1                        # pen colour: whatever the cell already has


@dataclass
class Stroke:
    """What one drawing operation did: sixels changed and sixels refused."""
    changed: int = 0
    refused: int = 0
    reasons: dict[str, int] = field(default_factory=dict)

    def refuse(self, why: str):
        self.refused += 1
        self.reasons[why] = self.reasons.get(why, 0) + 1

    def message(self) -> str:
        if not self.refused:
            return ""
        why = max(self.reasons, key=self.reasons.get)
        n = self.refused
        return f"{n} sixel{'s' if n != 1 else ''} not drawn: {why}"


@dataclass
class Block:
    """A rectangle of cells, as copied: rows of codes (they may be ragged -
    a block made from plain text has lines of its own lengths) and where it
    came from."""
    rows: list[bytes]
    top: int = 1
    left: int = 0

    @property
    def height(self) -> int:
        return len(self.rows)

    @property
    def width(self) -> int:
        return max((len(r) for r in self.rows), default=0)


@dataclass
class PasteReport:
    written: int = 0
    clipped: int = 0
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    changed_outside: dict[int, int] = field(default_factory=dict)   # row -> cells outside the block drawn differently
    brings_codes: bool = False

    def message(self) -> str:
        """The warning for the status line, or '' when the paste changed
        nothing outside itself."""
        if not self.changed_outside:
            return ""
        n = sum(self.changed_outside.values())
        rows = sorted(self.changed_outside)
        where = (f"row {rows[0]}" if len(rows) == 1 else
                 "rows " + ", ".join(str(r) for r in rows[:5]) + (" ..." if len(rows) > 5 else ""))
        cause = ("its control codes carry on past its edge" if self.brings_codes else
                 "it covered control codes the rest of the row relied on")
        return f"Paste changes how {n} cell{'s' if n != 1 else ''} outside it look ({where}): {cause}"


def _row_render(row) -> list[tuple]:
    """A row's appearance cell by cell, for comparing before and after."""
    d = decoder._empty()
    decoder.decode_row(row, d, 1)
    return [(int(d.code[1, c]), int(d.gset[1, c]), int(d.fg[1, c]), int(d.bg[1, c]), bool(d.flash[1, c]),
             bool(d.conceal[1, c])) for c in range(COLS)]


class Document:
    """One page being edited (with its subpages), the cursor, and undo."""

    def __init__(self, page: Page | None = None, black_fg: bool = False):
        self.page = page or Page()
        self.sub_index = 0
        self.row, self.col = 1, 0
        self.black_fg = black_fg
        self.undo_stack: list[tuple[Page, int, int, int]] = []
        self.redo_stack: list[tuple[Page, int, int, int]] = []
        self.dirty = False
        self._group_open = False
        self.anchor: tuple[int, int] | None = None    # the fixed corner of a block selection; the cursor is the other

    # ------------------------------------------------------------ basics
    @property
    def sub(self) -> Subpage:
        self.sub_index = max(0, min(self.sub_index, len(self.page.subpages) - 1))
        return self.page.subpages[self.sub_index]

    def rows(self):
        return self.sub.rows

    def decoded(self):
        return decoder.decode(self.sub.rows, black_fg=self.black_fg)

    # ------------------------------------------------------------ undo
    def checkpoint(self):
        """Call before a change. Consecutive calls inside begin()/end() make
        one undo step (a drag stroke, a paste)."""
        if self._group_open == "taken":
            return
        self.undo_stack.append((self.page.copy(), self.sub_index, self.row, self.col))
        del self.undo_stack[:-UNDO_DEPTH]
        self.redo_stack.clear()
        self.dirty = True
        if self._group_open:
            self._group_open = "taken"

    def begin(self):
        self._group_open = True

    def end(self):
        self._group_open = False

    def _restore(self, snap):
        page, si, r, c = snap
        filename = self.page.filename
        self.page.__dict__.update(page.copy().__dict__)
        self.page.filename = self.page.filename or filename
        self.sub_index, self.row, self.col = si, r, c
        self.dirty = True

    def undo(self) -> bool:
        if not self.undo_stack:
            return False
        self.redo_stack.append((self.page.copy(), self.sub_index, self.row, self.col))
        self._restore(self.undo_stack.pop())
        return True

    def redo(self) -> bool:
        if not self.redo_stack:
            return False
        self.undo_stack.append((self.page.copy(), self.sub_index, self.row, self.col))
        self._restore(self.redo_stack.pop())
        return True

    # ------------------------------------------------------------ cursor and text
    def first_col(self, row: int) -> int:
        """Row 0's columns 0-7 belong to the receiver (it writes the page
        number there), so the header is edited from column 8."""
        return 8 if row == 0 else 0

    def move(self, dr: int = 0, dc: int = 0):
        self.row = max(0, min(ROWS - 1, self.row + dr))
        self.col = max(self.first_col(self.row), min(COLS - 1, self.col + dc))

    def goto(self, row: int, col: int):
        self.row = max(0, min(ROWS - 1, row))
        self.col = max(self.first_col(self.row), min(COLS - 1, col))

    def _advance(self):
        if self.col < COLS - 1:
            self.col += 1
        elif self.row < ROWS - 1:
            self.row += 1
            self.col = self.first_col(self.row)

    def put(self, code: int, advance: bool = True):
        """Type-over: `code` into the cursor cell."""
        if self.col < self.first_col(self.row):
            self.col = self.first_col(self.row)
        self.checkpoint()
        self.sub.put(self.row, self.col, code)
        self.sub.damaged.discard((self.row, self.col))     # retyped: no longer as received
        if advance:
            self._advance()

    def type_text(self, text: str):
        for ch in text:
            code = charset.FROM_UNICODE.get(ch)
            if code is None:
                if ch == "\n":
                    self.row = min(ROWS - 1, self.row + 1)
                    self.col = self.first_col(self.row)
                continue
            self.put(code)

    def backspace(self):
        if self.col > self.first_col(self.row):
            self.col -= 1
            self.checkpoint()
            self.sub.put(self.row, self.col, 0x20)

    def delete_cell(self):
        """Delete at the cursor; the rest of the row moves left."""
        self.checkpoint()
        row = self.sub.rows[self.row]
        del row[self.col]
        row.append(0x20)

    def insert_cell(self):
        """Insert a space at the cursor; the rest of the row moves right
        (the last cell falls off)."""
        self.checkpoint()
        row = self.sub.rows[self.row]
        row.insert(self.col, 0x20)
        del row[COLS:]

    def insert_row(self):
        """A blank row at the cursor; rows below move down within 1-23."""
        if not 1 <= self.row <= 23:
            return
        self.checkpoint()
        rows = self.sub.rows
        rows[self.row:24] = [bytearray(b" " * COLS)] + rows[self.row:23]

    def delete_row(self):
        if not 1 <= self.row <= 23:
            return
        self.checkpoint()
        rows = self.sub.rows
        rows[self.row:24] = rows[self.row + 1:24] + [bytearray(b" " * COLS)]

    def clear_row(self):
        self.checkpoint()
        a = self.first_col(self.row)
        self.sub.rows[self.row][a:] = b" " * (COLS - a)

    def clear_page(self):
        self.checkpoint()
        for r in range(1, ROWS):
            self.sub.rows[r][:] = b" " * COLS

    # ------------------------------------------------------------ blocks
    def select(self, r0: int, c0: int, r1: int, c1: int):
        """Select the rectangle between two corners; the cursor goes to the second."""
        self.goto(r0, c0)
        self.anchor = (self.row, self.col)
        self.goto(r1, c1)

    def extend_selection(self, dr: int = 0, dc: int = 0):
        """Shift+arrow: start a selection at the cursor if there is none, then move."""
        if self.anchor is None:
            self.anchor = (self.row, self.col)
        self.move(dr, dc)

    def clear_selection(self):
        self.anchor = None

    def selection(self) -> tuple[int, int, int, int] | None:
        """(top, left, bottom, right), inclusive, or None. Row 0's first eight
        cells are the receiver's, so a selection reaching into the header
        starts at column 8 there - the clipping is done when cells are
        written, not here, so a block can still span rows 0 and 1."""
        if self.anchor is None:
            return None
        (r0, c0), (r1, c1) = self.anchor, (self.row, self.col)
        return min(r0, r1), min(c0, c1), max(r0, r1), max(c0, c1)

    def copy_block(self, rect: tuple[int, int, int, int] | None = None) -> Block:
        rect = rect or self.selection() or (self.row, self.col, self.row, self.col)
        t, l, b, r = rect
        return Block([bytes(self.sub.rows[y][l:r + 1]) for y in range(t, b + 1)], top=t, left=l)

    def cut_block(self) -> Block:
        blk = self.copy_block()
        t, l, b, r = self.selection() or (self.row, self.col, self.row, self.col)
        self.checkpoint()
        for y in range(t, b + 1):
            for x in range(max(l, self.first_col(y)), r + 1):
                self.sub.rows[y][x] = 0x20
        return blk

    def clear_block(self):
        """Delete with a selection: its cells become spaces, one undo step."""
        self.cut_block()

    def paste_block(self, block: Block, top: int | None = None, left: int | None = None) -> PasteReport:
        """Write a block with its top-left at (top, left), the cursor by
        default. Cells off the page, and row 0's receiver-owned columns,
        are left alone. The pasted area becomes the selection.

        Teletext attributes are state carried along the row, so a block is
        never just its cells: a colour code in it repaints everything after
        it to the end of the row, a double-height code takes the row below,
        and pasting spaces over a code that was there undoes what that code
        did further along. The report says so - by decoding the subpage
        before and after and comparing every cell outside the block - so
        the warning holds for any combination of codes, not only the ones
        thought of here."""
        top = self.row if top is None else top
        left = self.col if left is None else left
        before = decoder.decode(self.sub.rows, black_fg=self.black_fg)
        self.checkpoint()
        written = clipped = 0
        for i, data in enumerate(block.rows):
            y = top + i
            for j, code in enumerate(data):
                x = left + j
                if not (0 <= y < ROWS and self.first_col(y) <= x < COLS):
                    clipped += 1
                    continue
                self.sub.rows[y][x] = code & 0x7F
                self.sub.damaged.discard((y, x))
                written += 1
        after = decoder.decode(self.sub.rows, black_fg=self.black_fg)
        rect = (top, left, top + block.height - 1, left + block.width - 1)
        rep = PasteReport(written=written, clipped=clipped, rect=rect)
        inside = np.zeros((ROWS, COLS), bool)
        inside[max(0, top):max(0, top + block.height), max(0, left):max(0, left + block.width)] = True
        diff = np.zeros((ROWS, COLS), bool)
        for name in ("code", "gset", "bg", "dh", "box"):
            diff |= getattr(before, name) != getattr(after, name)
        # Foreground colour, flash and conceal only change how a cell looks
        # if something is drawn in it: a blank turned from white to cyan
        # looks the same, and warning about it taught people to ignore the
        # warning.
        inked = (before.code != 0x20) | (after.code != 0x20)
        for name in ("fg", "flash", "conceal"):
            diff |= inked & (getattr(before, name) != getattr(after, name))
        diff &= ~inside
        rep.changed_outside = {int(r): int(diff[r].sum()) for r in np.nonzero(diff.any(1))[0]}
        rep.brings_codes = any(c < 0x20 for row in block.rows for c in row)
        self.goto(max(0, min(ROWS - 1, rect[0])), max(0, min(COLS - 1, rect[1])))
        self.anchor = (self.row, self.col)
        self.goto(min(ROWS - 1, rect[2]), min(COLS - 1, rect[3]))
        return rep

    # ------------------------------------------------------------ sixels
    def sixel_on(self, sx: int, sy: int, d=None) -> bool:
        d = d if d is not None else self.decoded()
        r, c = sy // 3, sx // 2
        if not (0 <= r < ROWS and 0 <= c < COLS):
            return False
        if d.gset[r, c] == charset.SET_ALPHA or d.control[r, c]:
            return False
        return bool(int(d.code[r, c]) & charset.sixel_bit(sx % 2, sy % 3))

    def sixel_refusal(self, r: int, c: int, d=None) -> str:
        """Why the cell cannot take a sixel as it stands ('' if it can,
        'alpha' if it could once a graphics code is put before it)."""
        d = d if d is not None else self.decoded()
        if r == 0:
            return "the header row holds no graphics"
        if d.control[r, c]:
            return "a control code is in that cell"
        if d.hidden_row[r]:
            return "that row is the lower half of double height above"
        code = int(self.sub.rows[r][c])
        if d.graphics[r, c] and 0x40 <= code < 0x60:
            return "a capital letter is in that cell"
        if not d.graphics[r, c]:
            return "alpha"
        return ""

    def set_sixel(self, sx: int, sy: int, on: bool, pen: int = PEN_KEEP, stroke: Stroke | None = None) -> bool:
        stroke = stroke if stroke is not None else Stroke()
        r, c = sy // 3, sx // 2
        if not (0 <= r < ROWS and 0 <= c < COLS):
            return False
        rows = self.sub.rows
        d = decoder.decode(rows, black_fg=self.black_fg)
        why = self.sixel_refusal(r, c, d)
        bit = charset.sixel_bit(sx % 2, sy % 3)
        if why and why != "alpha":
            stroke.refuse(why)
            return False
        if not why and (pen == PEN_KEEP or int(d.fg[r, c]) == pen or not on):
            code = int(rows[r][c])
            code = 0x20 | (code & 0x5F) if charset.is_mosaic_code(code) else 0x20
            new = (code | bit) if on else (code & ~bit)
            if new != rows[r][c]:
                self.checkpoint()
                rows[r][c] = new
                stroke.changed += 1
            return True
        if not on:
            return True                           # erasing in alpha: nothing lit there
        # The cell needs a graphics-colour code before it.
        colour = pen if pen != PEN_KEEP else (int(d.fg[r, c]) or 7)
        ok, why = self._make_room(r, c, colour)
        if not ok:
            stroke.refuse(why)
            return False
        cur = int(rows[r][c])
        base = 0x20 | (cur & 0x5F) if (charset.is_mosaic_code(cur) and d.graphics[r, c]) else 0x20
        rows[r][c] = base | bit
        stroke.changed += 1
        return True

    def _make_room(self, r: int, c: int, colour: int) -> tuple[bool, str]:
        """Put graphics colour `colour` in force at (r, c) by turning the
        blank cell to its left into the code, restoring the old attributes
        on its right if needed. Changes nothing unless it succeeds."""
        rows = self.sub.rows
        if c == 0:
            return False, "the first cell of a row has no room for a colour code before it"
        left = int(rows[r][c - 1])
        d = decoder.decode(rows, black_fg=self.black_fg)
        left_is_ours = 0x10 < left <= 0x17
        if not (left == 0x20 and not d.control[r, c - 1]) and not left_is_ours:
            return False, "no blank cell to its left for a graphics colour code"
        before = _row_render(rows[r])
        trial = bytearray(rows[r])
        trial[c - 1] = 0x10 | colour
        if trial[c] < 0x20 or not charset.is_mosaic_code(trial[c]):
            trial[c] = 0x20
        after = _row_render(trial)
        changed = [k for k in range(COLS) if k not in (c - 1, c) and before[k] != after[k]]
        if changed and c + 1 < COLS:
            # Restore what was in force at c+1 before, in that cell, if it is blank.
            if int(trial[c + 1]) == 0x20 and not d.control[r, c + 1]:
                was_graphics = bool(d.graphics[r, c + 1])
                restore = (0x10 if was_graphics else 0x00) | int(d.fg[r, c + 1])
                t2 = bytearray(trial)
                t2[c + 1] = restore
                after2 = _row_render(t2)
                changed2 = [k for k in range(COLS) if k not in (c - 1, c, c + 1) and before[k] != after2[k]]
                if not changed2:
                    trial = t2
                    changed = []
        if changed:
            return False, "a colour code there would change the rest of the row"
        self.checkpoint()
        rows[r][:] = trial
        return True, ""

    def line(self, x0: int, y0: int, x1: int, y1: int, on: bool = True, pen: int = PEN_KEEP) -> Stroke:
        st = Stroke()
        self.begin()
        try:
            for x, y in line_points(x0, y0, x1, y1):
                self.set_sixel(x, y, on, pen, st)
        finally:
            self.end()
        return st

    def rect(self, x0: int, y0: int, x1: int, y1: int, on: bool = True, filled: bool = False,
             pen: int = PEN_KEEP) -> Stroke:
        st = Stroke()
        xa, xb = sorted((x0, x1))
        ya, yb = sorted((y0, y1))
        self.begin()
        try:
            for y in range(ya, yb + 1):
                for x in range(xa, xb + 1):
                    if filled or x in (xa, xb) or y in (ya, yb):
                        self.set_sixel(x, y, on, pen, st)
        finally:
            self.end()
        return st

    def fill(self, sx: int, sy: int, on: bool = True, pen: int = PEN_KEEP) -> Stroke:
        """Flood fill: the 4-connected run of sixels in the same state as
        the one clicked, through mosaic cells only - a control cell, an
        alpha cell or a different-coloured run is a wall."""
        st = Stroke()
        d = self.decoded()
        r0, c0 = sy // 3, sx // 2
        why = self.sixel_refusal(r0, c0, d)
        if why:
            st.refuse("fill starts in a cell with no mosaic" if why == "alpha" else why)
            return st
        target = self.sixel_on(sx, sy, d)
        if target == on:
            return st
        colour = int(d.fg[r0, c0])
        seen = set()
        todo = deque([(sx, sy)])
        pts = []
        while todo:
            x, y = todo.popleft()
            if (x, y) in seen or not (0 <= x < SX and 0 <= y < SY):
                continue
            seen.add((x, y))
            r, c = y // 3, x // 2
            if self.sixel_refusal(r, c, d) or int(d.fg[r, c]) != colour or int(d.bg[r, c]) != int(d.bg[r0, c0]):
                continue
            if self.sixel_on(x, y, d) != target:
                continue
            pts.append((x, y))
            todo.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))
        self.begin()
        try:
            for x, y in pts:
                self.set_sixel(x, y, on, PEN_KEEP, st)
        finally:
            self.end()
        return st

    # ------------------------------------------------------------ subpages
    def add_subpage(self, copy_current: bool = False):
        self.checkpoint()
        new = self.sub.copy() if copy_current else Subpage(cycle=self.sub.cycle, timed=self.sub.timed,
                                                           status=self.sub.status)
        if not copy_current:
            new.rows[0] = bytearray(self.sub.rows[0])
            new.links = list(self.sub.links)
            new.has_links = self.sub.has_links
        self.page.subpages.insert(self.sub_index + 1, new)
        self.sub_index += 1
        self._renumber()

    def delete_subpage(self):
        if len(self.page.subpages) < 2:
            return
        self.checkpoint()
        del self.page.subpages[self.sub_index]
        self.sub_index = min(self.sub_index, len(self.page.subpages) - 1)
        self._renumber()

    def move_subpage(self, delta: int):
        j = self.sub_index + delta
        if not 0 <= j < len(self.page.subpages):
            return
        self.checkpoint()
        s = self.page.subpages
        s[self.sub_index], s[j] = s[j], s[self.sub_index]
        self.sub_index = j
        self._renumber()

    def _renumber(self):
        subs = self.page.subpages
        if len(subs) == 1:
            subs[0].subcode = 0
        else:
            for i, s in enumerate(subs, 1):
                s.subcode = annex_a_subcode(i)

    def set_cycle(self, seconds: int, timed: bool = True):
        self.checkpoint()
        self.sub.cycle = max(1, min(255, int(seconds)))
        self.sub.timed = timed

    # ------------------------------------------------------------ links and labels
    def set_link(self, i: int, number: int | None):
        self.checkpoint()
        self.sub.links[i] = number if number is not None else NO_LINK
        self.sub.has_links = any(lk != NO_LINK for lk in self.sub.links)

    def set_fastext_labels(self, labels: list[tuple[int, str]]):
        """Row 24 as four labels, each in its own colour, a quarter of the
        row each - the layout sets put the coloured keys under."""
        self.checkpoint()
        self.sub.rows[24][:] = fastext_row(labels)

    # ------------------------------------------------------------ whole page
    def replace_rows(self, rows, top: int = 1, left: int = 0):
        """Paste a block of rows (an image conversion, an import)."""
        self.checkpoint()
        for i, data in enumerate(rows):
            r = top + i
            if not 0 < r < ROWS:
                continue
            for j, code in enumerate(data):
                if 0 <= left + j < COLS:
                    self.sub.rows[r][left + j] = code & 0x7F


DEFAULT_LABEL_COLOURS = (1, 2, 3, 6)       # red, green, yellow, cyan: the keys


def fastext_row(labels: list[tuple[int, str]]) -> bytearray:
    out = bytearray()
    for i in range(4):
        colour, text = labels[i] if i < len(labels) else (DEFAULT_LABEL_COLOURS[i], "")
        cell = bytearray([colour & 7]) + bytearray(charset.FROM_UNICODE.get(ch, 0x20) for ch in text[:9])
        out += cell.ljust(10, b" ")
    return out


def fastext_labels(row) -> list[tuple[int, str]] | None:
    """Read row 24 back as four labels if it has that layout (a colour code
    at columns 0, 10, 20 and 30 and nothing else that is a control code),
    or None if it is something else."""
    labels = []
    for q in range(4):
        seg = bytes(row[10 * q:10 * q + 10])
        if not seg or seg[0] > 0x07 or any(b < 0x20 for b in seg[1:]):
            return None
        labels.append((seg[0], "".join(charset.UNICODE.get(b, " ") for b in seg[1:]).rstrip()))
    return labels


def line_points(x0: int, y0: int, x1: int, y1: int):
    """Bresenham, both ends included."""
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
    err = dx + dy
    while True:
        yield x0, y0
        if x0 == x1 and y0 == y1:
            return
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy
