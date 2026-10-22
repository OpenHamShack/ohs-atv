"""edit.tf and zxnet hash-URLs: a whole page in a link.

The format is the "teletext page hashstring" (github.com/rawles/edit-tf,
wiki): after the '#', colon-separated parts. The first is one hex digit -
the low three bits an SAA505x character set (0 is the SAA5050, English),
bit 3 "render black foreground". The second is the 25 x 40 seven-bit codes
packed most significant bit first, row by row, into 7000 bits and written
as 1167 base64url digits (RFC 4648 section 5; two bits of padding unused).
Older links carry 24 rows in 1120 digits; they are read with a blank row
24. Then optional key=value parts in any order:

  PN  page number, three hex digits       SC  subcode, hex
  PS  page status, the MRG PS word's bits  X270  Fastext: six MPPSSSS
                                                 groups and the link control nibble

Keys this program does not use (X25, X26, X280, X284, zx...) are kept and
written back unchanged, as the format asks, so a link that went
edit.tf -> here -> zxnet keeps what zxnet put in it.

Both https://edit.tf/#... and https://zxnet.co.uk/teletext/editor/#... are
read; anything with a hashstring after a '#' (or a bare hashstring) is.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .page import COLS, NO_LINK, ROWS, Page, Subpage

B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
_B64_INDEX = {c: i for i, c in enumerate(B64)}
EDIT_TF = "https://edit.tf/"
ZXNET = "https://zxnet.co.uk/teletext/editor/"


class HashError(ValueError):
    pass


@dataclass
class HashPage:
    rows: list[bytearray]
    charset: int = 0
    black_fg: bool = False
    fields: dict[str, str] = field(default_factory=dict)    # every key=value part, in order


def encode_rows(rows) -> str:
    bits = []
    for r in range(ROWS):
        row = rows[r]
        for c in range(COLS):
            v = row[c] & 0x7F
            bits.extend((v >> (6 - k)) & 1 for k in range(7))
    while len(bits) % 6:
        bits.append(0)
    out = []
    for i in range(0, len(bits), 6):
        v = 0
        for b in bits[i:i + 6]:
            v = (v << 1) | b
        out.append(B64[v])
    return "".join(out)


def decode_rows(data: str) -> list[bytearray]:
    bits = []
    for ch in data.strip():
        if ch not in _B64_INDEX:
            raise HashError(f"not base64url: {ch!r}")
        v = _B64_INDEX[ch]
        bits.extend((v >> (5 - k)) & 1 for k in range(6))
    rows = [bytearray(b" " * COLS) for _ in range(ROWS)]
    n = min(ROWS * COLS, len(bits) // 7)
    for i in range(n):
        v = 0
        for b in bits[7 * i:7 * i + 7]:
            v = (v << 1) | b
        rows[i // COLS][i % COLS] = v
    return rows


def parse(url: str) -> HashPage:
    s = url.strip()
    if "#" in s:
        s = s.split("#", 1)[1]
    parts = s.split(":")
    if len(parts) < 2:
        raise HashError("no page data: expected #<charset>:<data>")
    head = parts[0].strip() or "0"
    try:
        meta = int(head, 16)
    except ValueError as e:
        raise HashError(f"bad character-set digit {head!r}") from e
    if len(parts[1]) < 1120:
        raise HashError(f"page data is {len(parts[1])} digits; a page is 1167 (or 1120 for 24 rows)")
    hp = HashPage(rows=decode_rows(parts[1]), charset=meta & 7, black_fg=bool(meta & 8))
    for p in parts[2:]:
        k, sep, v = p.partition("=")
        if sep:
            hp.fields[k] = v
    return hp


def _hexval(s: str | None, default: int | None = None) -> int | None:
    if not s:
        return default
    try:
        return int(s, 16)
    except ValueError:
        return default


def to_page(hp: HashPage, into: Page | None = None) -> tuple[Page, Subpage]:
    """A HashPage as a single-subpage Page (or as a new subpage values for
    `into`, which keeps its own number). Returns (page, subpage)."""
    sub = Subpage(rows=hp.rows)
    status = _hexval(hp.fields.get("PS"))
    if status is not None:
        sub.status = (status & 0x7FFF) | 0x8000        # bit 15 is ignored in a hash; it is "transmit" in a .tti
    sc = _hexval(hp.fields.get("SC"))
    if sc is not None:
        sub.subcode = sc & 0x3F7F
    x270 = hp.fields.get("X270")
    if x270 and len(x270) >= 42:
        links = []
        for i in range(6):
            g = x270[7 * i:7 * i + 7]
            n = _hexval(g[:3], NO_LINK)
            links.append(n if 0x100 <= n <= 0x8FE and (n & 0xFF) != 0xFF else NO_LINK)
        sub.links = links
        sub.has_links = any(lk != NO_LINK for lk in links)
    if into is not None:
        return into, sub
    pg = Page(subpages=[sub])
    pn = _hexval(hp.fields.get("PN"))
    if pn is not None and 0x100 <= pn <= 0x8FF:
        pg.number = pn
    return pg, sub


def from_page(page: Page, sub: Subpage, black_fg: bool = False, keep: dict[str, str] | None = None,
              base: str = EDIT_TF) -> str:
    fields = dict(keep or {})
    fields["PN"] = f"{page.number:03X}"
    fields["SC"] = f"{sub.subcode:X}"
    fields["PS"] = f"{sub.status & 0x7FFF:X}"
    if sub.has_links:
        fields["X270"] = "".join(f"{lk:03X}3F7F" for lk in sub.links) + "F"
    else:
        fields.pop("X270", None)
    meta = 0 | (8 if black_fg else 0)
    tail = "".join(f":{k}={v}" for k, v in fields.items())
    return f"{base}#{meta:X}:{encode_rows(sub.rows)}{tail}"
