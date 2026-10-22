"""Raw .t42: teletext packets as they leave the inserter, 42 bytes each.

A .t42 file is a sequence of packets with the clock run-in and framing
code stripped: two bytes of magazine and row address (MRAG, Hamming 8/4),
then 40 bytes. Rows 1-24 carry 40 characters with odd parity. The header
(row 0) carries eight Hamming 8/4 bytes - page units, page tens, the four
subcode nibbles with C4-C6 folded into the spare bits, C7-C10, C11-C14 -
then the 32 header characters (columns 8-39) with odd parity. Fastext
links go in X/27/0. Byte order is transmission order with each byte's
first-transmitted bit as its least significant bit, which is how vbit2,
the vbit tools and every .t42 reader expect them.

The control bits and X/27/0 are laid out exactly as vbit2's
Packet::Header and Packet::Fastext lay them out, including its choices:
C11 always 0 (parallel transmission), link control nibble 0xF (row 24
displayed), relative magazine bits in each link, and the page CRC over
header columns 8-31 and rows 1-25 with the generator x^16 + x^12 + x^9 +
x^7 + 1 (ETS 300 706 9.6.1) - computed, not left zero, so a decoder that
checks it is satisfied.

A carousel is written subpage after subpage, each a complete page
(header, all 24 rows, then X/27/0), so a reader that loops the file loops
the carousel. Every row is sent even when blank, so a decoder never keeps
a row from the previous subpage; for the same reason C4 (erase page) is set
on every subpage of a carousel.
"""
from __future__ import annotations

import time

import numpy as np

from .page import COLS, NO_LINK, PS_ERASE, Page, Subpage

PACKET = 42

HAM84 = (0x15, 0x02, 0x49, 0x5E, 0x64, 0x73, 0x38, 0x2F, 0xD0, 0xC7, 0x8C, 0x9B, 0xA1, 0xB6, 0xFD, 0xEA)


def _parity_table() -> bytes:
    t = bytearray(128)
    for c in range(128):
        t[c] = c | (0x80 if bin(c).count("1") % 2 == 0 else 0)
    return bytes(t)


PARITY = _parity_table()


def _ham_decode_table() -> list[tuple[int, int]]:
    """For every byte: (data nibble, errors) with errors 0, 1 (corrected)
    or 2 (uncorrectable; data -1). Built from the encode table by distance,
    which is what Hamming 8/4 decoding is: nearest codeword, and a byte two
    bits from every codeword is a detected double error."""
    out = []
    for b in range(256):
        best = min(range(16), key=lambda n: bin(b ^ HAM84[n]).count("1"))
        d = bin(b ^ HAM84[best]).count("1")
        if d == 0:
            out.append((best, 0))
        elif d == 1:
            out.append((best, 1))
        else:
            out.append((-1, 2))
    return out


HAM84_DECODE = _ham_decode_table()


def ham84(n: int) -> int:
    return HAM84[n & 0xF]


def unham84(b: int) -> int:
    """The nibble, or -1 for an uncorrectable byte."""
    return HAM84_DECODE[b & 0xFF][0]


def odd_parity(c: int) -> int:
    return PARITY[c & 0x7F]


def parity_ok(b: int) -> bool:
    return bin(b & 0xFF).count("1") % 2 == 1


def mrag(magazine: int, row: int) -> bytes:
    m = magazine & 7
    return bytes((ham84(m | ((row & 1) << 3)), ham84((row >> 1) & 0xF)))


def page_crc(header_text: bytes, rows: list[bytes]) -> int:
    """vbit2's Packet::PageCRC over header columns 8-31 and rows 1-25, taken
    over the bytes as transmitted - with their parity bits - which is what
    vbit2 does (its rows have had parity applied before it sums them). The
    first version summed the 7-bit codes and disagreed with every X/27/0
    vbit2 sent; tests/test_t42.py now checks against packets vbit2 wrote."""
    crc = 0
    data = bytes(odd_parity(c) for c in bytes(header_text[:24]) + b"".join(bytes(r) for r in rows))
    for byte in data:
        for i in range(8):
            b = ((byte >> (7 - i)) & 1) ^ ((crc >> 6) & 1) ^ ((crc >> 8) & 1) ^ ((crc >> 11) & 1) ^ ((crc >> 15) & 1)
            crc = b | ((crc & 0x7FFF) << 1)
    return crc


def header_packet(magazine: int, page: int, subcode: int, status: int, text: bytes) -> bytes:
    """Row 0. `text` is the 32 characters for columns 8-39."""
    sc = subcode
    p = bytearray(mrag(magazine, 0))
    p.append(ham84(page & 0xF))
    p.append(ham84((page >> 4) & 0xF))
    p.append(ham84(sc & 0xF))
    p.append(ham84(((sc >> 4) & 0x7) | (0x8 if status & 0x4000 else 0)))
    p.append(ham84((sc >> 8) & 0xF))
    p.append(ham84(((sc >> 12) & 0x3) | (0x4 if status & 0x0001 else 0) | (0x8 if status & 0x0002 else 0)))
    c7_10 = ((1 if status & 0x0004 else 0) | (2 if status & 0x0008 else 0) | (4 if status & 0x0010 else 0)
             | (8 if status & 0x0020 else 0))
    p.append(ham84(c7_10))
    p.append(ham84((status & 0x0380) >> 6))
    t = bytes(text[:32]).ljust(32, b" ")
    p.extend(odd_parity(c) for c in t)
    return bytes(p)


def row_packet(magazine: int, row: int, data) -> bytes:
    p = bytearray(mrag(magazine, row))
    p.extend(odd_parity(c) for c in bytes(data[:COLS]).ljust(COLS, b" "))
    return bytes(p)


def x27_packet(magazine: int, links: list[int], crc: int) -> bytes:
    p = bytearray(mrag(magazine, 27))
    p.append(ham84(0))                     # designation code 0
    mag = magazine & 7
    for lp in links[:6]:
        if not lp:
            lp = NO_LINK
        ls = 0x3F7F                        # "any subpage", as vbit2 writes it
        m = ((lp >> 8) ^ mag) & 7          # relative magazine
        p.append(ham84(lp & 0xF))
        p.append(ham84((lp >> 4) & 0xF))
        p.append(ham84(ls & 0xF))
        p.append(ham84(((m & 1) << 3) | ((ls >> 4) & 0x7)))
        p.append(ham84((ls >> 8) & 0xF))
        p.append(ham84(((m & 6) << 1) | ((ls >> 12) & 0x3)))
    p.append(ham84(0xF))                   # link control: row 24 displayed
    p.append((crc >> 8) & 0xFF)
    p.append(crc & 0xFF)
    return bytes(p)


def header_text(page: Page, sub: Subpage, now: float | None = None) -> bytes:
    """Columns 8-39 of the header as transmitted: the page's own row 0 if it
    has one, with vbit2's header substitutions applied (%%# page number,
    %%a day, %%b month, %d date, %H:%M/%S clock)."""
    from .header import expand
    row = bytes(sub.rows[0])
    return expand(row, page.number, now if now is not None else time.time())[8:40]


def subpage_packets(page: Page, sub: Subpage, erase: bool = False, now: float | None = None) -> list[bytes]:
    mag = page.magazine & 7
    status = sub.status | (PS_ERASE if erase else 0)
    head = header_text(page, sub, now)
    pkts = [header_packet(mag, page.number & 0xFF, sub.subcode, status, head)]
    rows = [bytes(sub.rows[r]) for r in range(1, 25)]
    for r, data in enumerate(rows, 1):
        pkts.append(row_packet(mag, r, data))
    if sub.has_links and any(lk != NO_LINK for lk in sub.links):
        crc = page_crc(bytes(b & 0x7F for b in head), rows + [b" " * COLS])
        pkts.append(x27_packet(mag, sub.links, crc))
    return pkts


def page_packets(page: Page, now: float | None = None) -> list[bytes]:
    erase = len(page.subpages) > 1
    out = []
    for s in page.subpages:
        out.extend(subpage_packets(page, s, erase=erase, now=now))
    return out


def dumps(pages, now: float | None = None) -> bytes:
    """One page or a list of pages (a whole service) as .t42 bytes."""
    if isinstance(pages, Page):
        pages = [pages]
    return b"".join(b"".join(page_packets(p, now)) for p in pages)


def save(pages, path, now: float | None = None):
    with open(path, "wb") as f:
        f.write(dumps(pages, now))


# ------------------------------------------------------------------ reading back
def read_packets(data: bytes) -> list[dict]:
    """Parse .t42 bytes into packets: magazine, row, and the payload decoded
    (header fields, 7-bit text with parity checked, or X/27/0 links). For
    tests and for checking a file before it goes to air."""
    out = []
    for i in range(0, len(data) - PACKET + 1, PACKET):
        p = data[i:i + PACKET]
        a, b = unham84(p[0]), unham84(p[1])
        if a < 0 or b < 0:
            out.append({"error": "MRAG"})
            continue
        mag = (a & 7) or 8
        row = (a >> 3) | (b << 1)
        pk: dict = {"magazine": mag, "row": row}
        body = p[2:]
        if row == 0:
            n = [unham84(x) for x in body[:8]]
            pk["page"] = (mag << 8) | (n[1] << 4) | n[0]
            pk["subcode"] = n[2] | ((n[3] & 7) << 4) | (n[4] << 8) | ((n[5] & 3) << 12)
            st = 0
            st |= 0x4000 if n[3] & 8 else 0
            st |= 0x0001 if n[5] & 4 else 0
            st |= 0x0002 if n[5] & 8 else 0
            st |= (n[6] & 1) * 0x0004 | (n[6] & 2) * 0x0004 | (n[6] & 4) * 0x0004 | (n[6] & 8) * 0x0004
            st |= (n[7] & 0xE) << 6
            pk["status"] = st
            pk["text"] = bytes(c & 0x7F for c in body[8:])
            pk["parity_ok"] = all(parity_ok(c) for c in body[8:])
        elif row <= 25:
            pk["text"] = bytes(c & 0x7F for c in body)
            pk["parity_ok"] = all(parity_ok(c) for c in body)
        elif row == 27:
            pk["dc"] = unham84(body[0])
            links = []
            for k in range(6):
                g = [unham84(x) for x in body[1 + 6 * k:7 + 6 * k]]
                m = (((g[3] >> 3) & 1) | ((g[5] >> 1) & 6)) ^ (mag & 7)
                links.append(((m or 8) << 8) | (g[1] << 4) | g[0])
            pk["links"] = links
            pk["control"] = unham84(body[37])
            pk["crc"] = (body[38] << 8) | body[39]
        out.append(pk)
    return out


def as_array(data: bytes) -> np.ndarray:
    return np.frombuffer(data, np.uint8).reshape(-1, PACKET)
