"""The header row: what row 0 of a page holds, and what a set shows there.

Row 0 is not really the page's: the inserter transmits its own header
(vbit2 takes it from vbit.conf, or from row 0 of page mFF of each
magazine), and the receiver writes the page number it is looking for over
columns 0-7. So an OL,0 line in an ordinary .tti is kept and round-tripped
but rarely matters; what the preview shows in row 0 is what a viewer would
see - the page number in columns 0-7, then the header template with vbit2's
substitutions done, ticking.

The substitutions are vbit2's (Packet::Header), with its quirks kept so the
preview matches the air: each token is replaced at its first occurrence
only, in this order - %%# page number, %%a day name, %%b month name, %d
day of month, %e day without leading zero, %m month, %y year, %H, %M, %S.
"""
from __future__ import annotations

import time

from .page import COLS

# A default template for new pages: 32 characters, the clock in yellow at
# the right as the broadcasters' services had it. The service name has a
# nine-character slot before the page, day, date and month (templates.py
# cuts a callsign to the same width); OPENHAMSHACK (twelve) overflowed it
# after the rename, so Brian chose the short form OHS for this slot on
# 21/10/2026.
DEFAULT_TEMPLATE = (b"OHS %%# %%a %e %%b").ljust(23, b" ") + b"\x03%H:%M/%S"
assert len(DEFAULT_TEMPLATE) == 32


def default_row0() -> bytearray:
    return bytearray(b" " * 8 + DEFAULT_TEMPLATE)


def expand(row: bytes, page_number: int, now: float) -> bytes:
    """Row 0 with the header substitutions done, still 40 bytes. Tokens are
    replaced in place, character for character, as vbit2 overwrites them;
    a 3-character token with a 3-character value keeps its width."""
    buf = bytearray(bytes(row[:COLS]).ljust(COLS, b" "))
    tm = time.localtime(now)

    def put(token: bytes, value: bytes):
        i = buf.find(token)
        if i >= 0:
            for k, ch in enumerate(value):
                if i + k < COLS:
                    buf[i + k] = ch

    mag = (page_number >> 8) & 7 or 8
    put(b"%%#", f"{mag}{page_number & 0xFF:02X}".encode())
    put(b"%%a", time.strftime("%a", tm)[:3].ljust(3).encode("latin-1", "replace"))
    put(b"%%b", time.strftime("%b", tm)[:3].ljust(3).encode("latin-1", "replace"))
    put(b"%d", time.strftime("%d", tm).encode())
    d = time.strftime("%d", tm)
    put(b"%e", (" " + d[1] if d[0] == "0" else d).encode())
    put(b"%m", time.strftime("%m", tm).encode())
    put(b"%y", time.strftime("%y", tm).encode())
    put(b"%H", time.strftime("%H", tm).encode())
    put(b"%M", time.strftime("%M", tm).encode())
    put(b"%S", time.strftime("%S", tm).encode())
    return bytes(buf)


def display_row0(row: bytes, page_number: int, now: float | None = None) -> bytes:
    """Row 0 as a set shows it: 'P' and the page number in columns 1-4 in
    white, then columns 8-39 of the expanded template."""
    now = time.time() if now is None else now
    head = expand(row, page_number, now)
    mag = (page_number >> 8) & 7 or 8
    lead = f" P{mag}{page_number & 0xFF:02X}   ".encode()
    return lead[:8] + head[8:40]
