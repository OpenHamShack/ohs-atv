"""A small teletext service to try the transmitter with: four pages written
as .tti files into a folder of your choosing.

    python3 -m ohsatv.ttxsample ~/teletext/sample

The screenshots and tests use it too, so it is real .tti (the 8-bit
translation, controls as 80-9F), read back by ohsatv/tti.py like any
editor's. Pages: 100 the index (fastext to the others), 101 about the
station, 102 a two-subpage carousel, 103 the Level 1 test card (colours,
mosaics, double height, flash, conceal).
"""
from __future__ import annotations

import sys
from pathlib import Path

# Level 1 control codes, by name (EN 300 706 clause 12.2).
RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN, WHITE = range(1, 8)
FLASH, STEADY, NORMAL, DOUBLE = 0x08, 0x09, 0x0C, 0x0D
CONCEAL, CONTIG, SEPARATED, BLACKBG, NEWBG, HOLD, RELEASE = 0x18, 0x19, 0x1A, 0x1C, 0x1D, 0x1E, 0x1F


def a(colour: int) -> bytes:
    return bytes((colour,))


def g(colour: int) -> bytes:
    """Mosaic colour code."""
    return bytes((0x10 + colour,))


def banner(title: str, bg: int = BLUE, fg: int = YELLOW) -> bytes:
    """A double-height title on a coloured band (it takes the row below
    too, whose own text is then not shown)."""
    return a(bg) + bytes((NEWBG, fg, DOUBLE)) + title.encode()


def page(num: str, rows: dict, links=None, subpages=None, cycle: int = 6, description: str = "") -> bytes:
    """`rows`: {row: bytes}; `subpages`: a list of such dicts instead."""
    out = [f"DE,{description or 'OpenHamShack ATV sample'}", "PS,8000", f"CT,{cycle},T"]
    for i, rs in enumerate(subpages or [rows]):
        out.append(f"PN,{num}{i + (1 if subpages else 0):02d}")
        out.append(f"SC,{i + (1 if subpages else 0):04d}")
        for y in sorted(rs):
            text = bytes(c | 0x80 if c < 0x20 else c for c in rs[y][:40])
            out.append(b"OL,%d," % y + text)
        if links:
            out.append("FL," + ",".join(links))
    return b"\r\n".join(x if isinstance(x, bytes) else x.encode() for x in out) + b"\r\n"


def mosaic_band(colours=(RED, YELLOW, GREEN, CYAN, BLUE, MAGENTA)) -> bytes:
    row = b""
    for c in colours:
        row += g(c) + b"\x7f" * 5
    return row[:40]


FASTEXT = a(RED) + b"About   " + a(GREEN) + b"News    " + a(YELLOW) + b"Testcard" + a(CYAN) + b"Index"


def pages(callsign: str = "GM4BRI") -> dict[str, bytes]:
    idx = {1: banner(f" {callsign} TELETEXT"),
           4: mosaic_band(),
           6: a(WHITE) + b"OpenHamShack ATV sends these pages on",
           7: a(WHITE) + b"the blanked lines of its PAL picture.",
           9: a(CYAN) + b"About this station        " + a(YELLOW) + b"101",
           10: a(CYAN) + b"Station news (2 pages)    " + a(YELLOW) + b"102",
           11: a(CYAN) + b"Level 1 test page         " + a(YELLOW) + b"103",
           14: a(GREEN) + b"Keys: 0-9 page, F1-F4 the coloured",
           15: a(GREEN) + b"links, PgUp/PgDn subpages, R reveal.",
           20: mosaic_band((BLUE, CYAN, GREEN, YELLOW, RED, MAGENTA)),
           22: a(WHITE) + b"Amateur television: callsign " + callsign.encode(),
           24: FASTEXT}
    about = {1: banner(" ABOUT THIS STATION", bg=RED, fg=WHITE),
             4: a(YELLOW) + b"Station " + callsign.encode(),
             6: a(WHITE) + b"Analogue PAL on 23cm, AM-VSB and FM-ATV,",
             7: a(WHITE) + b"from a Pluto-family SDR. Teletext is",
             8: a(WHITE) + b"World System Teletext, 625-line",
             9: a(WHITE) + b"System B (EN 300 706): 6.9375 Mbit/s",
             10: a(WHITE) + b"on lines 7-22 and 320-335.",
             13: a(CYAN) + b"Pages are .tti files: edit them in",
             14: a(CYAN) + b"OpenHamShack Teletext; they go out on",
             15: a(CYAN) + b"the next transmission.",
             24: FASTEXT}
    news = [{1: banner(" STATION NEWS  1/2", bg=GREEN, fg=BLUE),
             4: a(WHITE) + b"Teletext added to OpenHamShack ATV.",
             6: a(YELLOW) + b"Pages come round in a carousel; this",
             7: a(YELLOW) + b"one has two subpages, six seconds each.",
             24: FASTEXT},
            {1: banner(" STATION NEWS  2/2", bg=GREEN, fg=BLUE),
             4: a(WHITE) + b"Measured, not assumed: teletext needs",
             5: a(WHITE) + b"about 3.75 MHz of video. A USB Pluto's",
             6: a(WHITE) + b"5 MS/s modes do not carry it.",
             24: FASTEXT}]
    test = {1: banner(" LEVEL 1 TEST PAGE", bg=MAGENTA, fg=WHITE),
            3: b"".join(a(c) + name.encode() for c, name in ((RED, "Red"), (GREEN, "Green"), (YELLOW, "Yell"),
                                                              (BLUE, "Blue"), (MAGENTA, "Mag"), (CYAN, "Cyan"),
                                                              (WHITE, "White"))),
            5: b"".join(a(c) + bytes((NEWBG,)) + b"   " for c in (RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN, WHITE)),
            7: g(WHITE) + bytes(range(0x20, 0x40)),
            8: g(CYAN) + bytes((SEPARATED,)) + bytes(range(0x60, 0x80))[:36],
            10: a(YELLOW) + bytes((FLASH,)) + b"Flashing" + bytes((STEADY,)) + a(WHITE) + b"steady " +
            bytes((CONCEAL,)) + b"concealed: reveal",
            12: a(GREEN) + b"Mosaics held across controls:",
            13: g(RED) + bytes((HOLD,)) + b"\x7f" + bytes((0x13, 0x16, 0x14)) + b"\x7f" + bytes((RELEASE,)),
            15: a(WHITE) + b"abcdefghijklmnopqrstuvwxyz 0123456789",
            16: a(WHITE) + b"ABCDEFGHIJKLMNOPQRSTUVWXYZ #[\\]^_`{|}~",
            24: FASTEXT}
    return {
        "P100.tti": page("100", idx, links=("101", "102", "103", "100", "8FF", "100"), description="Index"),
        "P101.tti": page("101", about, links=("101", "102", "103", "100", "8FF", "100"), description="About"),
        "P102.tti": page("102", {}, subpages=news, links=("101", "102", "103", "100", "8FF", "100"),
                         description="News carousel"),
        "P103.tti": page("103", test, links=("101", "102", "103", "100", "8FF", "100"), description="Test page"),
    }


def write(folder, callsign: str = "GM4BRI") -> Path:
    p = Path(folder).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    for name, data in pages(callsign).items():
        (p / name).write_bytes(data)
    return p


if __name__ == "__main__":
    dest = write(sys.argv[1] if len(sys.argv) > 1 else "teletext-sample")
    print(f"wrote 4 pages to {dest}")
