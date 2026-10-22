"""Subtitles: SRT in and out, and cues as teletext subtitle pages.

OpenHamShack ATV puts subtitles on page 888 from an SRT file, so SRT is the
contract (DESIGN.md "Subtitles: the SRT subset"); this module reads any
reasonable SRT, writes only the subset OpenHamShack ATV reads, and lays a cue
out the way a subtitle page is built so the editor can show it through the
one decoder:

- one or two lines at the foot of the page, each **double height** (row 22
  for one line, rows 20 and 22 for two), centred;
- each line **boxed** - a subtitle page (C6) shows only boxed cells, over
  the picture - with the codes before the text: double height, start box
  twice (sets that miss one still see the other), then the speaker's
  colour inside the box, where its cell is a black margin; after the text,
  end box twice, the first of which is a black margin on the right;
- at 37 characters there is room for only one start box; over 37 the line
  is cut, which is why the editor warns past 37 (and past two lines: a
  third would cover the picture's middle).

The page's status is C6 subtitle and C4 erase (each cue replaces the last
on the set, nothing lingers).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import charset
from .page import COLS, PS_ERASE, PS_SUBTITLE, PS_TRANSMIT, Page, Subpage

PAGE = 0x888
MAX_CHARS = 37
MAX_LINES = 2
DH, SB, EB = 0x0D, 0x0B, 0x0A

# The seven teletext colours a speaker can have, as SRT writes them.
HEX = {1: "#ff0000", 2: "#00ff00", 3: "#ffff00", 4: "#0000ff", 5: "#ff00ff", 6: "#00ffff", 7: "#ffffff"}
NAMES = {"red": 1, "lime": 2, "green": 2, "yellow": 3, "blue": 4, "magenta": 5, "fuchsia": 5, "cyan": 6,
         "aqua": 6, "white": 7, "black": 7}
RGB = {k: (int(v[1:3], 16), int(v[3:5], 16), int(v[5:7], 16)) for k, v in HEX.items()}

TIMING = re.compile(r"(\d+):(\d{1,2}):(\d{1,2})[,.](\d{1,3})\s*-->\s*(\d+):(\d{1,2}):(\d{1,2})[,.](\d{1,3})")
FONT = re.compile(r"<font\b[^>]*\bcolou?r\s*=\s*[\"']?([#\w]+)[\"']?[^>]*>", re.I)
TAG = re.compile(r"<[^>]*>|\{\\[^}]*\}")


@dataclass
class Line:
    text: str
    colour: int = 7


@dataclass
class Cue:
    start: float
    end: float
    lines: list[Line] = field(default_factory=list)


@dataclass
class Track:
    cues: list[Cue] = field(default_factory=list)

    def copy(self) -> "Track":
        return Track([Cue(c.start, c.end, [Line(ln.text, ln.colour) for ln in c.lines]) for c in self.cues])

    def at(self, t: float) -> Cue | None:
        for c in self.cues:
            if c.start <= t < c.end:
                return c
        return None


# ---------------------------------------------------------------- colours
def nearest_colour(spec: str) -> int:
    """A font colour as a teletext colour (1-7). Names and #rgb/#rrggbb;
    black (which a subtitle cannot be on a set) and anything unreadable
    are white."""
    s = spec.strip().lower()
    if s in NAMES:
        return NAMES[s]
    s = s.lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    try:
        r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    except (ValueError, IndexError):
        return 7
    if max(r, g, b) < 64:
        return 7
    return min(RGB, key=lambda k: sum((a - c) ** 2 for a, c in zip(RGB[k], (r, g, b))))


# ---------------------------------------------------------------- SRT
def _secs(h, m, s, ms) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def _unescape(t: str) -> str:
    return t.replace("&lt;", "<").replace("&gt;", ">").replace("&nbsp;", " ").replace("&amp;", "&")


def parse_srt(text: str) -> tuple[Track, list[str]]:
    """Any reasonable SRT: CRLF or LF, a BOM, missing or wrong cue numbers,
    '.' for ',' in times, styling tags (stripped), font colours (the first
    in a line gives the line its colour). Returns the track and what was
    not understood, one line each."""
    if text.startswith("﻿"):
        text = text[1:]
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    track, problems = Track(), []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.split("\n")
        i = next((k for k, ln in enumerate(lines[:3]) if TIMING.search(ln)), None)
        if i is None:
            if block.strip():
                problems.append(f"no timing in: {lines[0][:40]}")
            continue
        m = TIMING.search(lines[i])
        g = m.groups()
        cue = Cue(_secs(*g[:4]), _secs(*g[4:]))
        for raw in lines[i + 1:]:
            if not raw.strip():
                continue
            fm = FONT.search(raw)
            colour = nearest_colour(fm.group(1)) if fm else 7
            cue.lines.append(Line(_unescape(TAG.sub("", raw)).strip(), colour))
        track.cues.append(cue)
    track.cues.sort(key=lambda c: c.start)
    return track, problems


def _stamp(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def as_teletext_text(text: str) -> str:
    """Text as a set will show it, one English-set character per cell: what
    is written to SRT, so a reader maps every character to exactly one code
    (DESIGN.md has the table)."""
    return "".join(charset.UNICODE[charset.FROM_UNICODE.get(ch, 0x3F)] for ch in text)


def _escape(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def dumps_srt(track: Track) -> str:
    """The subset OpenHamShack ATV reads (DESIGN.md): cues numbered from 1 in
    time order, HH:MM:SS,mmm times, LF line ends, one or two text lines a
    cue, each plain (white) or wholly inside one <font color="#rrggbb">
    with one of the six other teletext colours; &amp; &lt; &gt; escaped;
    no other tags."""
    out = []
    for n, c in enumerate(sorted(track.cues, key=lambda c: c.start), 1):
        out.append(str(n))
        out.append(f"{_stamp(c.start)} --> {_stamp(c.end)}")
        for ln in c.lines[:MAX_LINES] or [Line("")]:
            t = _escape(as_teletext_text(ln.text))
            out.append(t if ln.colour == 7 else f'<font color="{HEX[ln.colour]}">{t}</font>')
        out.append("")
    return "\n".join(out) + ("\n" if out else "")


# ---------------------------------------------------------------- checks
def warnings(track: Track, i: int) -> list[str]:
    c = track.cues[i]
    w = []
    for k, ln in enumerate(c.lines, 1):
        if len(ln.text) > MAX_CHARS:
            w.append(f"line {k} is {len(ln.text)} characters; a subtitle line holds {MAX_CHARS}")
        shown = as_teletext_text(ln.text)
        odd = sorted({a for a, b in zip(ln.text, shown) if a != b})
        if odd:
            w.append("on a set " + ", ".join(f"'{a}' is '{as_teletext_text(a)}'" for a in odd))
    if len(c.lines) > MAX_LINES:
        w.append(f"{len(c.lines)} lines; a subtitle has at most {MAX_LINES}")
    if c.end <= c.start:
        w.append("ends before it starts")
    if i > 0 and track.cues[i - 1].end > c.start:
        w.append("overlaps the cue before")
    return w


# ---------------------------------------------------------------- as a teletext page
def line_cells(line: Line) -> tuple[bytes, int]:
    """One subtitle line as cells and the column the cells start in."""
    text = bytes(charset.FROM_UNICODE.get(ch, 0x3F) for ch in line.text)[:MAX_CHARS]
    colour = line.colour & 7 or 7
    if len(text) >= MAX_CHARS:
        pre = bytes([DH, SB, colour])
    else:
        pre = bytes([DH, SB, SB, colour])
    room = COLS - len(pre) - len(text)
    post = bytes([EB, EB])[:max(0, room)]
    cells = pre + text + post
    text_col = max(len(pre), (COLS - len(text)) // 2)
    start = min(text_col - len(pre), COLS - len(cells))
    return cells, max(0, start)


def cue_rows(cue: Cue | None) -> list[bytearray]:
    rows = [bytearray(b" " * COLS) for _ in range(25)]
    if cue is None:
        return rows
    lines = cue.lines[:MAX_LINES]
    slots = {1: [22], 2: [20, 22]}.get(len(lines), [])
    for r, ln in zip(slots, lines):
        cells, col = line_cells(ln)
        rows[r][col:col + len(cells)] = cells
    return rows


def cue_page(cue: Cue | None, number: int = PAGE) -> Page:
    s = Subpage(rows=cue_rows(cue), status=PS_TRANSMIT | PS_SUBTITLE | PS_ERASE)
    return Page(number=number, description="Subtitles", subpages=[s])
