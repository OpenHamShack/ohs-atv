"""MRG .tti page files - the contract with OpenHamShack ATV and with vbit2.

The dialect (DESIGN.md "The .tti contract" has it in full) is the MRG
Systems TTI format v1.1 as vbit2's loader (filemonitor.cpp, LoadTTI) reads
it, and the reader here follows that loader rule for rule wherever the
MRG document is silent, because vbit2 is what will actually transmit
these files:

- One command per line, `XX,` then parameters. Lines end CRLF (MRG says
  so; LF and CR alone are read too).
- `PN,mppss` starts a subpage. The page number is the first three hex
  digits of the first PN; `ss` is not used for anything by vbit2.
- `CT,n,T|C`, `PS,hhhh` and `RE,h` before the first PN are defaults; after
  a PN they belong to that subpage, and CT and PS are inherited by the
  subpages that follow (vbit2 carries them forward; RE it does not).
- `SC,hhhh` is the current subpage's subcode.
- `OL,n,text`: row n. In the text a code below 0x20 is written either as
  ESC followed by the code + 0x40 (the "Viewdata" escape) or as the code +
  0x80 (MRG's own form); any byte is taken modulo 0x80; and a raw byte
  below 0x20 ends the row, as vbit2 does - so a stray CR is harmless.
  Rows shorter than 40 are padded with spaces.
- `FL,r,g,y,c,l,i`: the six Fastext links, hex page numbers; 0 or 8FF is
  "no link".
- Everything else (DS, SP, MS, RD, PF, anything unknown) is kept verbatim
  in the position it came from and written back, as are rows 25-29: this
  program edits Level 1 pages, but must not strip what other tools put in.

The writer writes ESC escapes (7-bit clean: the file survives editors, git
and e-mail, and vbit2, wxTED, edit.tf and zxnet all read it), one subpage
block at a time - PN, SC, PS, CT, RE if set, OL rows that are not blank,
FL if the subpage has links - with no blank lines (vbit2 tokenises on
commas across newlines, and an empty line hides the command after it).
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .page import COLS, NO_LINK, ROWS, Page, Subpage

ESC = "\x1b"


class TTIError(ValueError):
    pass


def decode_ol(text: str) -> bytearray:
    """An OL line's text as 40 seven-bit codes, as vbit2's TTXLine does it."""
    out = bytearray()
    i = 0
    while i < len(text) and len(out) < COLS:
        ch = ord(text[i])
        if ch == 0x1B:
            i += 1
            if i >= len(text):
                break
            out.append(ord(text[i]) & 0x3F)
        elif ch < 0x20:
            break
        else:
            out.append(ch & 0x7F)
        i += 1
    out.extend(b" " * (COLS - len(out)))
    return out


def ol_cells(text: str) -> int:
    """How many cells an OL line's text holds before decode_ol cuts it at
    40: what a writer meant the row to be, for the service checker."""
    n = i = 0
    while i < len(text):
        ch = ord(text[i])
        if ch == 0x1B:
            i += 1
            if i >= len(text):
                break
        elif ch < 0x20:
            break
        n += 1
        i += 1
    return n


def row_lengths(data: bytes | str) -> list[tuple[int, int, int]]:
    """(subpage index, row, cells) for every OL line of rows 0-24 in a .tti
    file, counted before the 40-cell cut, subpages numbered as `loads`
    numbers them."""
    text = data.decode("latin-1") if isinstance(data, (bytes, bytearray)) else data
    out = []
    sub = -1
    for line in _lines(text.lstrip("\ufeff").removeprefix("\xef\xbb\xbf")):
        cmd, sep, rest = line.partition(",")
        cmd = cmd.strip().upper()
        if not sep:
            continue
        if cmd == "PN" or (cmd in ("OL", "SC", "FL") and sub < 0):
            sub += 1
        if cmd == "OL":
            n, _, body = rest.partition(",")
            r = _int(n, -1)
            if 0 <= r < ROWS:
                out.append((sub, r, ol_cells(body)))
    return out


def encode_ol(row, escape: str = "esc") -> str:
    """40 codes as OL text: control codes as ESC + (code + 0x40), or with
    escape="high" as code + 0x80 (MRG's form; not 7-bit clean)."""
    parts = []
    for c in row:
        c &= 0x7F
        if c < 0x20:
            parts.append(ESC + chr(c + 0x40) if escape == "esc" else chr(c + 0x80))
        else:
            parts.append(chr(c))
    return "".join(parts)


def _hex(s: str, default: int = 0) -> int:
    s = s.strip()
    n = ""
    for ch in s:
        if ch in "0123456789abcdefABCDEF":
            n += ch
        else:
            break
    return int(n, 16) if n else default


def _int(s: str, default: int = 0) -> int:
    s = s.strip()
    n = ""
    for ch in s:
        if ch.isdigit() or (ch == "-" and not n):
            n += ch
        else:
            break
    try:
        return int(n)
    except ValueError:
        return default


def _lines(text: str) -> list[str]:
    if "\n" in text:
        return [ln[:-1] if ln.endswith("\r") else ln for ln in text.split("\n")]
    return text.split("\r")


def loads(data: bytes | str, filename: str = "") -> Page:
    """Parse a .tti file's contents."""
    text = data.decode("latin-1") if isinstance(data, (bytes, bytearray)) else data
    if text.startswith("\xef\xbb\xbf"):
        text = text[3:]
    elif text.startswith("﻿"):
        text = text[1:]
    page = Page(subpages=[], filename=filename)
    number = None
    cycle, timed, status, region = 8, True, 0x8000, 0
    cur: Subpage | None = None
    saw_any = False

    def new_sub() -> Subpage:
        s = Subpage(cycle=cycle, timed=timed, status=status, region=region)
        page.subpages.append(s)
        return s

    for line in _lines(text):
        if not line.strip():
            continue
        cmd, sep, rest = line.partition(",")
        cmd = cmd.strip().upper()
        if not sep:
            (cur.extra_lines if cur else page.head_lines).append(line)
            continue
        saw_any = True
        if cmd == "DE":
            page.description = rest
        elif cmd == "PN":
            if number is None:
                digits = rest.strip()
                if len(digits) >= 3 and digits[0] in "12345678":
                    try:
                        number = int(digits[:3], 16)
                    except ValueError:
                        pass
            cur = new_sub()
        elif cmd == "CT":
            n, _, kind = rest.partition(",")
            cycle = max(1, min(255, _int(n, 1)))
            timed = kind.strip()[:1].upper() != "C"
            if cur is not None:
                cur.cycle, cur.timed = cycle, timed
        elif cmd == "PS":
            status = _hex(rest, 0x8000) & 0xFFFF
            if cur is not None:
                cur.status = status
        elif cmd == "SC":
            if cur is None:
                cur = new_sub()
            cur.subcode = _hex(rest) & 0x3F7F
        elif cmd == "RE":
            r = _hex(rest) & 0xF
            if cur is not None:
                cur.region = r
            else:
                region = r
        elif cmd == "OL":
            n, _, body = rest.partition(",")
            row = _int(n, -1)
            if cur is None:
                cur = new_sub()     # vbit2 drops rows before any PN; a lone page without PN is kept here
            if 0 <= row < ROWS:
                cur.rows[row] = decode_ol(body)
            elif ROWS <= row <= 29:
                cur.extra_rows[row] = body
        elif cmd == "FL":
            if cur is None:
                cur = new_sub()
            vals = rest.split(",")
            links = []
            for i in range(6):
                v = _hex(vals[i], 0) if i < len(vals) else 0
                links.append(NO_LINK if v == 0 or v > 0x8FF or v < 0x100 else v)
            cur.links = links
            cur.has_links = True
        else:
            (cur.extra_lines if cur else page.head_lines).append(line)
    if not page.subpages:
        if not saw_any and text.strip():
            raise TTIError("not a .tti page: no commands found")
        page.subpages.append(Subpage(cycle=cycle, timed=timed, status=status, region=region))
    page.number = number if number is not None else 0x100
    return page


def dumps(page: Page, escape: str = "esc", newline: str = "\r\n") -> bytes:
    """A page as .tti bytes (latin-1; pure ASCII with escape="esc")."""
    out: list[str] = []
    desc = page.description.replace("\r", " ").replace("\n", " ")
    out.append(f"DE,{desc}")
    out.extend(page.head_lines)
    multi = len(page.subpages) > 1
    for i, s in enumerate(page.subpages, 1):
        ss = i % 100 if multi else 0
        out.append(f"PN,{page.number:03X}{ss:02d}")
        out.append(f"SC,{s.subcode & 0x3F7F:04X}")
        out.append(f"PS,{s.status & 0xFFFF:04X}")
        out.append(f"CT,{max(1, min(255, int(s.cycle)))},{'T' if s.timed else 'C'}")
        if s.region:
            out.append(f"RE,{s.region & 0xF:X}")
        out.extend(s.extra_lines)
        for r in range(ROWS):
            row = s.rows[r]
            if any(c != 0x20 for c in row):
                out.append(f"OL,{r},{encode_ol(row, escape)}")
        for r in sorted(s.extra_rows):
            out.append(f"OL,{r},{s.extra_rows[r]}")
        if s.has_links:
            out.append("FL," + ",".join(f"{lk:03X}" for lk in s.links))
    return (newline.join(out) + newline).encode("latin-1")


def load(path: str | os.PathLike) -> Page:
    p = Path(path)
    return loads(p.read_bytes(), filename=p.name)


def save(page: Page, path: str | os.PathLike, escape: str = "esc"):
    """Write atomically: a page half-written when the inserter re-reads the
    folder would go to air half-written (vbit2 watches the folder)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".", suffix=".tti.tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(dumps(page, escape=escape))
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    page.filename = p.name
