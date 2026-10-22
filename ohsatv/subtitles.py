"""Subtitles: SRT files and typed captions, as teletext page 888.

**SRT is the contract** with whatever makes the subtitles (OpenHamShack
Teletext writes it): numbered cues, `HH:MM:SS,mmm --> HH:MM:SS,mmm`, one or
more lines of text, a blank line. Read as UTF-8 (a BOM skipped; Latin-1 if
it is not UTF-8); `.` accepted for `,` in times, as players do. Tags:
`<font color="...">` - a name or #RRGGBB - picks the nearest teletext
colour; `<i> <b> <u>` and anything else in angle brackets are dropped
(teletext has no italic); `{\\an8}`-style positioning codes are dropped too.

**The page** is what UK broadcast subtitles looked like, built as OpenHamShack
Teletext builds it (its DESIGN.md "Subtitles", so its 888 preview is what
goes out): each line double height, in its own black box over the picture,
centred, on rows 20 and 22 (one line: 22). A row is

    [spaces] double height, start box, start box, colour, text, end box, end box

- two start boxes against a corrupted one, the colour inside the box (its
cell a black margin). A colour change inside a line takes a cell. At most
two lines, each cut at 37 cells (the editor's SRT subset; a typed caption
is wrapped at words first).

The editor writes a subset (DESIGN.md "Subtitles: the SRT subset"): UTF-8,
LF, one or two lines a cue, each white or wholly in one `<font
color="#rrggbb">` of the six colours, `&amp; &lt; &gt;` the only entities,
and the English set's characters as Unicode (`←` for 0x5B, `½` 0x5C, `£`
0x23 ...), one a cell - all read here, along with the looser SRT other
tools write. Other characters map to the nearest (curly quotes, dashes;
accents dropped).
"""
from __future__ import annotations

import bisect
import re
import unicodedata
from dataclasses import dataclass

# Level 1 codes.
COLOURS = {1: (255, 0, 0), 2: (0, 255, 0), 3: (255, 255, 0), 4: (0, 0, 255), 5: (255, 0, 255),
           6: (0, 255, 255), 7: (255, 255, 255)}
NAMES = {"red": 1, "green": 2, "lime": 2, "yellow": 3, "blue": 4, "magenta": 5, "fuchsia": 5, "cyan": 6,
         "aqua": 6, "white": 7}
DOUBLE, START_BOX, END_BOX = 0x0D, 0x0B, 0x0A
WIDTH = 37                      # cells of text a line can hold (one start box, no end box)
ROWS_FOR = {1: (22,), 2: (20, 22)}


def colour_code(spec: str) -> int:
    """A font colour (name, #rgb or #rrggbb) -> the nearest teletext
    colour (1-7). Black has no Level 1 alphanumeric code: it maps to white."""
    s = spec.strip().strip("\"'").lower()
    if s in NAMES:
        return NAMES[s]
    m = re.fullmatch(r"#?([0-9a-f]{3}|[0-9a-f]{6})", s)
    if not m:
        return 7
    h = m.group(1)
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    rgb = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    if max(rgb) < 64:
        return 7
    return min(COLOURS, key=lambda c: sum((a - b) ** 2 for a, b in zip(COLOURS[c], rgb)))


_SUBST = {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "…": "...", " ": " "}


# The English G0 set's characters that are not ASCII, as OpenHamShack Teletext
# writes them in SRT (its DESIGN.md, "Subtitles: the SRT subset"): one
# character, one cell.
ENGLISH = {"£": 0x23, "←": 0x5B, "½": 0x5C, "→": 0x5D, "↑": 0x5E, "#": 0x5F, "\u2015": 0x60, "¼": 0x7B,
           "\u2016": 0x7C, "¾": 0x7D, "÷": 0x7E, "\u25a0": 0x7F}


def to_teletext(text: str) -> bytes:
    """Unicode -> G0 codes (English national set)."""
    out = bytearray()
    for ch in text:
        ch = _SUBST.get(ch, ch)
        for c in ch:
            if c in ENGLISH:
                out.append(ENGLISH[c])
            elif 0x20 <= ord(c) < 0x7F:
                out.append(ord(c))
            else:
                base = unicodedata.normalize("NFKD", c).encode("ascii", "ignore")
                out += base if base else b"?"
    return bytes(out)


@dataclass
class Cue:
    start: float
    end: float
    lines: list          # [[(colour, text), ...], ...] - runs of one colour per line


_TIME = re.compile(r"(\d+):(\d{1,2}):(\d{1,2})[,.](\d{1,3})")


def _secs(m) -> float:
    h, mi, s, ms = m.groups()
    return int(h) * 3600 + int(mi) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0


def parse_markup(line: str, colour: int = 7) -> list:
    """One line of SRT text -> [(colour, text), ...]."""
    line = re.sub(r"\{\\[^}]*\}", "", line)
    runs, stack = [], [colour]
    pos = 0
    for m in re.finditer(r"<\s*(/?)\s*([a-zA-Z]+)([^>]*)>", line):
        if m.start() > pos:
            runs.append((stack[-1], line[pos:m.start()]))
        pos = m.end()
        closing, tag, attrs = m.group(1), m.group(2).lower(), m.group(3)
        if tag == "font":
            if closing:
                if len(stack) > 1:
                    stack.pop()
            else:
                cm = re.search(r"colou?r\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", attrs, re.I)
                stack.append(colour_code(cm.group(1)) if cm else stack[-1])
    if pos < len(line):
        runs.append((stack[-1], line[pos:]))
    # The only entities the editor writes (and the usual ones), after the
    # tags are gone, so "&lt;loud&gt;" is text, not a tag.
    runs = [(c, t.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&amp;", "&"))
            for c, t in runs]
    # Merge neighbours of one colour; drop empty runs.
    out: list = []
    for c, t in runs:
        if not t:
            continue
        if out and out[-1][0] == c:
            out[-1] = (c, out[-1][1] + t)
        else:
            out.append((c, t))
    return out


def parse_srt(data: bytes | str) -> list[Cue]:
    """SRT -> cues sorted by start. Malformed blocks are skipped."""
    if isinstance(data, bytes):
        if data.startswith(b"\xef\xbb\xbf"):
            data = data[3:]
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
    else:
        text = data.lstrip("﻿")
    cues = []
    for block in re.split(r"\r?\n\s*\r?\n", text.replace("\r\n", "\n").replace("\r", "\n")):
        lines = [x for x in block.split("\n") if x.strip() != ""]
        ti = next((i for i, x in enumerate(lines) if "-->" in x), None)
        if ti is None:
            continue
        a, _, b = lines[ti].partition("-->")
        ma, mb = _TIME.search(a), _TIME.search(b)
        if not ma or not mb:
            continue
        body = [parse_markup(x) for x in lines[ti + 1:]]
        body = [r for r in body if r]
        if body:
            cues.append(Cue(_secs(ma), _secs(mb), body))
    cues.sort(key=lambda c: c.start)
    return cues


class Track:
    """Cues by time: `at(t)` is the cue showing at t seconds, or None."""

    def __init__(self, cues: list[Cue]):
        self.cues = cues
        self.starts = [c.start for c in cues]

    def at(self, t: float) -> Cue | None:
        i = bisect.bisect_right(self.starts, t) - 1
        # Cues may overlap; take the latest started that is still on.
        while i >= 0:
            c = self.cues[i]
            if c.start <= t < c.end:
                return c
            if self.starts[i] < t - 3600:
                break
            i -= 1
        return None


def _wrap(runs: list) -> list[list]:
    """A line of colour runs -> lines of at most WIDTH cells, a colour
    change costing one."""
    words = []                               # (colour, word) with spaces kept as separators
    for c, t in runs:
        for w in re.split(r"(\s+)", t):
            if w:
                words.append((c, " " if w.isspace() else w))
    lines, cur, used, last_c = [], [], 0, None
    for c, w in words:
        cost = len(w) + (1 if c != last_c and cur else 0)
        if w == " " and not cur:
            continue
        if used + cost > WIDTH and cur:
            while cur and cur[-1][1] == " ":
                cur.pop()
            lines.append(cur)
            cur, used, last_c = [], 0, None
            if w == " ":
                continue
            cost = len(w)
        cur.append((c, w[:WIDTH]))
        used += cost
        last_c = c
    while cur and cur[-1][1] == " ":
        cur.pop()
    if cur:
        lines.append(cur)
    return lines


def row_bytes(runs: list) -> bytes:
    """One display line -> a 40-byte boxed double-height row, centred, as
    OpenHamShack Teletext builds them (so its preview of 888 is what goes out):
    double height, start box twice, the colour inside the box (its cell a
    black margin), the text, end box twice (the first a margin). A long
    line gives up the second end box, then the first, then the second
    start box: at 37 cells only one start box fits; past 37 it is cut."""
    body = bytearray()
    last = None
    for i, (c, t) in enumerate(runs):
        if i and c != last:
            body.append(c)                   # a colour change: one cell inside the box
        body += to_teletext(t)
        last = c
    text = bytes(body[:WIDTH])
    colour = (runs[0][0] if runs else 7) & 7 or 7
    # Placed exactly as the editor's line_cells places it: the text
    # centred in the row (not the boxed whole), the codes before it.
    pre = bytes((DOUBLE, START_BOX, colour)) if len(text) >= WIDTH else bytes((DOUBLE, START_BOX, START_BOX, colour))
    post = bytes((END_BOX, END_BOX))[:max(0, 40 - len(pre) - len(text))]
    cells = pre + text + post
    text_col = max(len(pre), (40 - len(text)) // 2)
    start = max(0, min(text_col - len(pre), 40 - len(cells)))
    return (b" " * start + cells).ljust(40, b" ")[:40]


def page_rows(lines: list, wrap: bool = False) -> list:
    """Cue lines (lists of colour runs) -> page 888's 26 rows (None where
    nothing is sent): the first two lines, each cut at 37 cells, as the
    editor's SRT subset asks of a reader. `wrap` (typed captions, which
    can run on): break at words first. An empty list -> no rows: nothing
    on screen."""
    disp = []
    for runs in lines:
        disp += _wrap(runs) if wrap else [runs]
    disp = disp[:2]
    rows = [None] * 26
    if disp:
        for y, runs in zip(ROWS_FOR[len(disp)], disp):
            rows[y] = row_bytes(runs)
    return rows


def caption_lines(text: str) -> list:
    """A typed caption: plain text (font tags allowed), '|' or a newline
    for a line break."""
    return [parse_markup(x.strip()) for x in re.split(r"\||\n", text) if x.strip()]


def caption_seconds(text: str) -> float:
    """How long a typed caption stays up: reading time, ~13 characters a
    second after a second and a half to notice it, between 3 and 10 s."""
    return max(3.0, min(10.0, 1.5 + len(text.strip()) / 13.0))
