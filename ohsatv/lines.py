"""Line standards: what the decoder needs to know about a scanning system.

The decoder (ohsatv/decoder.py) was written for 625 lines and is the same
machine for 405: sync tips and back porch measured on every line, a 64-line
fold to acquire, a matched edge detector at every line's 0H and a straight
line through a batch of them, field sync from the first half-line that is
mostly sync. What differs between the two is numbers, not method, so the
numbers live here and the decoder takes a `Standard` rather than being
forked (Brian's brief: "generalise the decoder over the line standard").

Two standards:

- ``PAL625``: 625 lines, 15 625 Hz, built from ohsatv.pal's constants, so
  the 625-line decoder is exactly what it was.
- ``SYSTEM_A``: the British 405-line standard (CCIR Report 624-2, 1982,
  Tables I, 1-1 and 1-2; DESIGN.md "405 lines"). 10 125 Hz lines, a 9 us
  line sync, eight 40 us broad pulses at half-line intervals and no
  equalising pulses.

The one difference that is more than a number: a 405-line line is 98.765 us,
which is not a whole number of samples at any of the program's rates
(5 MS/s gives 493.83). The decoder has always fitted a fractional line
period; what changes is that its fold and its line windows can no longer
assume one (`period` against `window`).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import cached_property

import numpy as np

from . import pal


@dataclass(frozen=True)
class Standard:
    name: str
    lines: int
    fh: float                       # line frequency, Hz
    hsync_us: float                 # line-sync pulse width
    active_start_us: float          # 0H to the start of the picture
    active_us: float                # picture width
    porch_us: tuple                 # where the back porch is measured (us from 0H), clear of sync and burst
    field2: int                     # the line whose second half starts field 2's broad pulses
    picture: tuple                  # ((first, last, row0), ...) per field: frame lines -> rows row0, row0+2...
    quiet: tuple                    # 0-based lines with nothing in the picture area (noise measurement)
    colour: bool                    # carries PAL colour
    half_top: int = 0               # the picture line that shows only its second half
    half_bottom: int = 0            # ... only its first half
    notes: tuple = field(default=())

    def period(self, fs: float) -> float:
        """Samples per line: a whole number for 625 lines at every rate the
        program offers, never for 405."""
        return fs / self.fh

    def window(self, fs: float) -> int:
        """Samples taken for each line: the period, rounded up."""
        return int(math.ceil(self.period(fs) - 1e-9))

    def whole(self, fs: float) -> bool:
        p = self.period(fs)
        return abs(p - round(p)) < 1e-6

    def active_samples(self, fs: float) -> tuple[int, int]:
        first = int(math.ceil(self.active_start_us * 1e-6 * fs))
        count = int(math.floor(self.active_us * 1e-6 * fs))
        return first, count

    @cached_property
    def rows(self) -> int:
        return sum(last - first + 1 for first, last, _ in self.picture)

    def picture_row(self, line: int) -> int | None:
        for first, last, row0 in self.picture:
            if first <= line <= last:
                return row0 + 2 * (line - first)
        return None

    @cached_property
    def row_map(self) -> tuple[np.ndarray, np.ndarray]:
        """(frame line indices 0-based, picture rows) for every picture line."""
        lines, rows = [], []
        for line in range(1, self.lines + 1):
            r = self.picture_row(line)
            if r is not None:
                lines.append(line - 1)
                rows.append(r)
        return np.array(lines), np.array(rows)

    @cached_property
    def order(self) -> np.ndarray:
        """The frame line (0-based) of each picture row, top to bottom."""
        lines, rows = self.row_map
        return lines[np.argsort(rows)]


PAL625 = Standard(
    name="625", lines=pal.LINES, fh=pal.FH, hsync_us=pal.HSYNC_US, active_start_us=pal.ACTIVE_START_US,
    active_us=pal.ACTIVE_US, porch_us=(8.2, 10.2), field2=313,
    picture=((pal.FIELD1_FIRST, pal.FIELD1_LAST, 0), (pal.FIELD2_FIRST, pal.FIELD2_LAST, 1)),
    quiet=tuple(np.r_[7:15, 320:328]), colour=True, half_top=23, half_bottom=623)


# --- 405 lines: CCIR System A ---------------------------------------------------
# Every figure is CCIR Report 624-2 (Recommendations and Reports of the CCIR,
# 1982, Volume XI-1), the last edition that described System A (ITU-R
# BT.470-6, note 1, points back to it). Where the Report gives a range the
# middle is used, and said so. DESIGN.md "405 lines" has the table.
A_LINES = 405
A_FH = 10_125.0                         # Table I item 3: 10 125 Hz
A_LINE_US = 1e6 / A_FH                  # 98.765 us (Table 1-1 H: "98.8")
A_BLANK_US = 18.0                       # Table 1-1 a: line blanking 17.5 to 19 us
A_FRONT_US = 1.75                       # c: front porch 1.5 to 2.0 us
A_HSYNC_US = 9.0                        # d: line sync 8 to 10 us
A_ACTIVE_START_US = A_BLANK_US - A_FRONT_US          # b: 0H to end of blanking, 16 to 17 us -> 16.25
A_ACTIVE_US = A_LINE_US - A_BLANK_US                 # 80.765 us of picture
A_HALF_US = A_LINE_US / 2
A_BROAD_US = 40.0                       # Table 1-2 q: field-sync (broad) pulse 38.0 to 42.0 us
A_BROAD_GAP_US = A_HALF_US - A_BROAD_US              # r: between them, 7.4 to 11.4 us -> 9.38
A_BROAD_PULSES = 8                      # m: field-sync sequence 4 H, pulses at half-line intervals
A_SYNC_EDGE_US = 0.2                    # f, s: sync edges under 0.25 us (10-90 %)
A_PIC_EDGE_US = 0.35                    # e: blanking edges 0.25 to 0.5 us


def a_slots() -> list[tuple[str, str]]:
    """System A's field-sync pattern as pal.py writes 625's: each line two
    half-line slots, "h" line sync, "b" broad pulse, "" nothing. No
    equalising pulses (Report 624-2, Table 1-2 note 4). Field 1's eight
    broad pulses start at 0H of line 1 and fill lines 1-4; field 2's start
    half a line later, in the middle of line 203, and end with the first
    half of line 207 (202.5 lines a field)."""
    t = [("h", "")] * (A_LINES + 1)          # index 0 unused
    for ln in (1, 2, 3, 4):
        t[ln] = ("b", "b")
    t[203] = ("h", "b")
    for ln in (204, 205, 206):
        t[ln] = ("b", "b")
    t[207] = ("b", "")
    return t


# Picture lines. Field blanking is (13 to 15.5) H + a (Table 1-2 j), and
# commences 0.015 H to 0.515 H before the field-sync sequence (note 4).
# Taken as 14 H + a: blanking from the end of line 405's picture (the front
# porch, 0.018 H, before line 1) to the start of line 15's - lines 1-14 of
# each field blanked, 377 picture lines in all, the figure always quoted for
# 405. Field 2 is half a line later, so its blanking runs from the middle of
# line 203 to the middle of line 217: line 203 shows its first half, 217 its
# second, as 625's lines 623 and 23 do. Field 2's lines sit half a line
# above field 1's (its field sync is half a line later), so 217 is the top
# row and field 2 has the even rows.
SYSTEM_A = Standard(
    name="405", lines=A_LINES, fh=A_FH, hsync_us=A_HSYNC_US, active_start_us=A_ACTIVE_START_US,
    active_us=A_ACTIVE_US, porch_us=(10.5, 15.5), field2=203,
    picture=((15, 203, 1), (217, 405, 0)),
    quiet=tuple(np.r_[5:13, 208:216]), colour=False, half_top=217, half_bottom=203)

STANDARDS = {"625": PAL625, "405": SYSTEM_A}
