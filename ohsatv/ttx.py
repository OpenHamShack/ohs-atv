"""World System Teletext, the packet layer: codes, packets, pages, and the
receiver's page store. ETSI EN 300 706 (Enhanced Teletext specification,
V1.2.1); clause numbers below are that document's.

A **packet** here is the 42 bytes after the clock run-in and framing code
- two address bytes and 40 data bytes - which is also what a .t42 file
holds, so a packet read from a file, built by the carousel or sliced off
the air is the same `bytes` object everywhere. The physical layer (the
run-in, the framing code, the waveform) is ohsatv/vbi.py's.

What is built and what is not:

- Level 1 pages: the header (X/0: page number, sub-code, control bits C4-
  C14, 32 displayable bytes with the clock in the last 8), display rows
  X/1-X/24 (7 data bits + odd parity), X/25 (stored, not shown at Level 1)
  and the editorial links X/27/0 (FLOF / "fastext", clause 9.6.1).
- The broadcast service data packet 8/30 Format 1 (clause 9.8.1): the
  initial page, the date and time, a status line.
- Not built: Level 1.5-3.5 enhancements (X/26, X/28, M/29), magazine
  inventory, TOP. A receiver here ignores those packets; it does not fail
  on them.

Why the control bits are kept as one int with bit n = Cn (C4 is 1 << 4):
the .tti PS word, the header's four Hamming nibbles and the page store all
convert to and from it in one place, and `ctl & C8` reads as the standard.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

# --- control bits (clause 9.3.1.3), bit n = Cn ---------------------------------
C4, C5, C6, C7, C8, C9, C10, C11, C12, C13, C14 = (1 << n for n in range(4, 15))
ERASE, NEWSFLASH, SUBTITLE, SUPPRESS_HEADER, UPDATE, INTERRUPTED, INHIBIT, SERIAL = C4, C5, C6, C7, C8, C9, C10, C11
NATIONAL = C12 | C13 | C14

NO_PAGE = 0xFF            # "no page" in a link; also the time-filling header's page
NO_SUB = 0x3F7F           # "no particular sub-code" (clause 9.6.1)
ROWS = 25                 # rows 0 (header) to 24 are displayable at Level 1


# --- Hamming 8/4 (clause 8.2) -------------------------------------------------
def _h84(d: int) -> int:
    """Data nibble (D1 = bit 0) -> the transmitted byte, bit 1 of the byte
    (the first sent) its LSB: P1 D1 P2 D2 P3 D3 P4 D4."""
    d1, d2, d3, d4 = (d >> 0) & 1, (d >> 1) & 1, (d >> 2) & 1, (d >> 3) & 1
    p1 = 1 ^ d1 ^ d3 ^ d4
    p2 = 1 ^ d1 ^ d2 ^ d4
    p3 = 1 ^ d1 ^ d2 ^ d3
    p4 = 1 ^ p1 ^ d1 ^ p2 ^ d2 ^ p3 ^ d3 ^ d4
    return p1 | d1 << 1 | p2 << 2 | d2 << 3 | p3 << 4 | d3 << 5 | p4 << 6 | d4 << 7


HAM84 = bytes(_h84(d) for d in range(16))


def _unham_table() -> tuple[int, ...]:
    """Every byte -> its nibble, or -1. The code is an extended Hamming code
    (minimum distance 4): a byte one bit from a codeword is that codeword
    (corrected), one two bits from several is a detected double error. A
    table built by distance, not by the clause's parity tests, so it cannot
    disagree with the encoder above; tests/test_teletext.py checks it
    against the tests all the same."""
    out = []
    for b in range(256):
        best = -1
        for d in range(16):
            dist = bin(b ^ HAM84[d]).count("1")
            if dist == 0:
                best = d
                break
            if dist == 1:
                best = d
        out.append(best)
    return tuple(out)


UNHAM84 = _unham_table()


def ham(d: int) -> int:
    return HAM84[d & 15]


def unham(b: int) -> int:
    """The nibble, or -1 for an uncorrectable byte."""
    return UNHAM84[b & 0xFF]


# --- odd parity (clause 8.1) ----------------------------------------------------
PARITY = bytes((c | (0 if bin(c).count("1") % 2 else 0x80)) for c in range(128))
PARITY_OK = tuple(bin(b).count("1") % 2 == 1 for b in range(256))


def par(c: int) -> int:
    return PARITY[c & 0x7F]


# --- addresses ----------------------------------------------------------------------
def address(mag: int, row: int) -> bytes:
    """Bytes 4-5 (clause 7.1.2). Magazine 8 is sent as 0."""
    m = mag & 7
    return bytes((ham(m | (row & 1) << 3), ham(row >> 1)))


def parse_address(pkt: bytes) -> tuple[int, int] | None:
    """(magazine 1-8, row 0-31) or None if either byte is uncorrectable."""
    a, b = unham(pkt[0]), unham(pkt[1])
    if a < 0 or b < 0:
        return None
    mag = a & 7
    return (mag or 8), (a >> 3) | (b << 1)


def _page_nibbles(page: int, sub: int, extra: tuple[int, int, int]) -> bytes:
    """The six bytes a page address takes in a header (bytes 6-11) and in
    each X/27 link: units, tens, S1, S2 + one bit, S3, S4 + two bits.
    `extra` = (bit 3 of the S2 nibble, bits 2 and 3 of the S4 nibble): C4,
    C5, C6 in a header, the magazine complement M1, M2, M3 in a link."""
    s1, s2, s3, s4 = sub & 0xF, (sub >> 4) & 0x7, (sub >> 8) & 0xF, (sub >> 12) & 0x3
    return bytes((ham(page & 0xF), ham(page >> 4), ham(s1), ham(s2 | extra[0] << 3), ham(s3),
                  ham(s4 | extra[1] << 2 | extra[2] << 3)))


def _parse_page_nibbles(b: bytes) -> tuple[int, int, tuple[int, int, int]] | None:
    n = [unham(x) for x in b[:6]]
    if min(n) < 0:
        return None
    page = n[0] | n[1] << 4
    sub = n[2] | (n[3] & 7) << 4 | n[4] << 8 | (n[5] & 3) << 12
    return page, sub, ((n[3] >> 3) & 1, (n[5] >> 2) & 1, (n[5] >> 3) & 1)


# --- building packets ------------------------------------------------------------------
def text_bytes(chars: bytes, n: int) -> bytes:
    """`n` displayable bytes, odd parity, padded with spaces."""
    chars = bytes(chars[:n]).ljust(n, b" ")
    return bytes(PARITY[c & 0x7F] for c in chars)


def header(mag: int, page: int, sub: int, ctl: int, text: bytes) -> bytes:
    """X/0. `text` is the 32 displayable bytes (columns 8-39); the clock is
    by convention its last 8 (bytes 38-45)."""
    c = ctl
    b = bytearray(address(mag, 0))
    b += _page_nibbles(page, sub, ((c >> 4) & 1, (c >> 5) & 1, (c >> 6) & 1))
    b.append(ham((c >> 7) & 0xF))          # C7 C8 C9 C10
    b.append(ham((c >> 11) & 0xF))         # C11 C12 C13 C14
    b += text_bytes(text, 32)
    return bytes(b)


def row(mag: int, y: int, chars: bytes) -> bytes:
    """X/1 - X/25: 40 bytes, 7 bits + odd parity."""
    return address(mag, y) + text_bytes(chars, 40)


def links_packet(mag: int, links, crc: int = 0, control: int = 0xF) -> bytes:
    """X/27/0 (clause 9.6.1): six links (red, green, yellow, cyan, the fifth
    'next', index), each (magazine 1-8, page, sub-code) or None, the link
    control byte (0xF: no local code of practice, and display row 24 - the
    FLOF prompt row), and the page's CRC."""
    b = bytearray(address(mag, 27))
    b.append(ham(0))                        # designation code 0
    for i in range(6):
        ln = links[i] if i < len(links) else None
        if ln is None:
            # "No page" as vbit2 sends it, 8FF:3F7F - magazine 8, relative
            # to ours - so our packets match its byte for byte (a receiver
            # ignores page FF whatever the magazine).
            lm, page, sub = 8, NO_PAGE, NO_SUB
        else:
            lm, page, sub = ln
        # Relative magazine: each M bit set complements that bit of this
        # packet's own magazine.
        x = (lm & 7) ^ (mag & 7)
        b += _page_nibbles(page, sub, (x & 1, (x >> 1) & 1, (x >> 2) & 1))
    b.append(ham(control))
    b.append((crc >> 8) & 0xFF)
    b.append(crc & 0xFF)
    return bytes(b)


def page_crc(header_text: bytes, rows) -> int:
    """The X/27/0 check word (clause 9.6.1): a 16-stage shift register fed
    with the exclusive-or of the input and stages 7, 9, 12 and 16, over
    header bytes 14-37 (its first 24 displayable characters) and rows 1-25,
    40 characters each (absent rows as spaces), each byte's bits b8 first.
    The bytes are taken as transmitted, *with* their parity bits: the
    clause's "page memory" reads either way, and vbit2 sums them with
    parity - this first summed the 7-bit codes and disagreed with both
    X/27/0 packets in a capture vbit2 made (tests/fixtures/vbit2-sample.t42,
    from OpenHamShack Teletext, which found the same). Nothing here checks it
    on receive; it is sent because the clause says a transmitter does."""
    reg = 0                                 # bit k = stage k + 1
    data = bytes(header_text[:24]).ljust(24, b" ")
    for y in range(1, 26):
        r = rows[y] if y < len(rows) and rows[y] is not None else b""
        data += bytes(r[:40]).ljust(40, b" ")
    for c in data:
        c = PARITY[c & 0x7F]
        for i in range(7, -1, -1):
            fb = ((c >> i) & 1) ^ ((reg >> 6) & 1) ^ ((reg >> 8) & 1) ^ ((reg >> 11) & 1) ^ ((reg >> 15) & 1)
            reg = ((reg << 1) | fb) & 0xFFFF
    return reg


def service_packet(initial: tuple[int, int, int] | None, status: bytes, utc: float, offset_s: float = 0.0,
                   ni: int = 0) -> bytes:
    """Packet 8/30 Format 1, the broadcast service data packet (clause
    9.8.1): the initial page a receiver should show without being asked
    (absolute magazine), the network identification (0: an amateur
    station has none allocated), local time offset, date (Modified Julian)
    and UTC - each digit sent plus one - and 20 characters of status
    display. `utc` is the second the packet relates to: "the next
    following second" (Annex B.5: sent just after the second's boundary)."""
    b = bytearray(address(8, 30))
    b.append(ham(0))                          # format 1, multiplexed with a picture
    if initial is None:
        mag, page, sub = 8, NO_PAGE, NO_SUB
    else:
        mag, page, sub = initial
    m = mag & 7
    b += _page_nibbles(page, sub, (m & 1, (m >> 1) & 1, (m >> 2) & 1))
    b += bytes(((ni >> 8) & 0xFF, ni & 0xFF))
    half_hours = int(round(abs(offset_s) / 1800.0)) & 0x1F
    b.append((half_hours << 1) | (0x40 if offset_s < 0 else 0))
    days = int(utc // 86400)
    mjd = 40587 + days                        # 1970-01-01 is MJD 40587
    d = [int(x) + 1 for x in f"{mjd:05d}"]
    b += bytes((d[0], (d[1] << 4) | d[2], (d[3] << 4) | d[4]))
    sec = int(utc) % 86400
    hh, mm, ss = sec // 3600, (sec // 60) % 60, sec % 60
    b += bytes(((((hh // 10) + 1) << 4) | ((hh % 10) + 1), (((mm // 10) + 1) << 4) | ((mm % 10) + 1),
                (((ss // 10) + 1) << 4) | ((ss % 10) + 1)))
    b += bytes(4)                             # reserved
    b += text_bytes(status, 20)
    return bytes(b)


def parse_service_packet(pkt: bytes) -> dict | None:
    """8/30 Format 1 -> {"initial": (mag, page, sub) or None, "status": bytes}."""
    if parse_address(pkt) != (8, 30) or unham(pkt[2]) not in (0, 1):
        return None
    p = _parse_page_nibbles(pkt[3:9])
    if p is None:
        return None
    page, sub, (a, b2, c) = p
    mag = (a | b2 << 1 | c << 2) or 8
    status = bytes((x & 0x7F) if PARITY_OK[x] else 0x20 for x in pkt[22:42])
    return {"initial": None if page == NO_PAGE else (mag, page, sub), "status": status}


# --- pages --------------------------------------------------------------------------------
def page_label(mag: int, page: int) -> str:
    """'100'-style: magazine 1-8, page tens and units in hex (A-F are legal
    on the air; a viewer is not normally offered them)."""
    return f"{mag}{page:02X}"


def parse_label(s: str) -> tuple[int, int] | None:
    s = s.strip()
    if len(s) != 3 or s[0] not in "12345678":
        return None
    try:
        return int(s[0]), int(s[1:], 16)
    except ValueError:
        return None


@dataclass
class Subpage:
    """One version of a page: rows 1-25 of 40 7-bit characters (None =
    not sent), its sub-code, control bits, links, and how long it stays
    up before the next subpage of a carousel (seconds, or magazine cycles
    when `cycle_mode` is 'C')."""
    rows: list = field(default_factory=lambda: [None] * 26)
    sub: int = 0
    ctl: int = 0
    links: list = field(default_factory=list)
    cycle: float = 8.0
    cycle_mode: str = "T"
    transmit: bool = True


@dataclass
class Page:
    mag: int
    page: int
    subpages: list = field(default_factory=list)
    description: str = ""

    @property
    def label(self) -> str:
        return page_label(self.mag, self.page)


# --- the receiver's page store ---------------------------------------------------------------
@dataclass
class RxPage:
    mag: int
    page: int
    sub: int
    ctl: int = 0
    header: bytes = b" " * 32
    rows: list = field(default_factory=lambda: [None] * 26)
    links: list = field(default_factory=list)
    t: float = 0.0
    errors: int = 0


class PageStore:
    """Packets in, pages out - what a television's teletext decoder keeps.

    Each magazine has a page being received: the one its last header
    named (parallel mode, C11 clear - the magazines interleave, as ours
    and most broadcasters' do); in serial mode a header ends every
    magazine's page. Rows go to their magazine's page. Pages are kept by
    page number and sub-code, so a carousel's subpages each have their own
    copy and the viewer can step through them.

    A character that fails its parity check keeps what was there from an
    earlier transmission (a space if nothing was): that is what made
    teletext on a weak signal fill in over a few cycles instead of showing
    garbage.

    **Never a row on the wrong page.** The standard ends a page only at
    the next header in its magazine, so a header lost to noise would put
    the next page's rows on this one. Whenever that might have happened,
    the page being received is closed instead - its rows so far stay, and
    what comes before the next header is dropped (it comes round again):

    - a header whose page address or control bits cannot be corrected
      closes its magazine; a packet whose address cannot be read might
      have been a header - of any magazine if the magazine is unreadable
      (all are closed), of its own if only the row number is and it could
      be 0;
    - a display row (1-25) not after the last one received, or a second
      X/27/0, means another transmission has begun: every inserter we
      know of - vbit2, hacktv, ours - sends rows in ascending order, and
      a page's links once;
    - a break in the signal (`gap()`, from the decoder's frame) closes
      every magazine: what follows is not known to follow what went before.

    What this cannot catch, and nothing in the packets can: an inserter
    that leaves blank rows out (hacktv does) loses a header and the next
    page's first row sent is below the last one received - say rows 1-3
    of one page, then rows 10-12 of the next. Every row here is sent, so
    our own service never has that gap; tests/test_teletext.py,
    MisAssembly, drops and damages packets and headers at random and finds
    no row on a page it was not sent with.

    Thread-safe: the engine adds from its decode thread, the window reads.
    `on_page` is called (mag, page, sub) when a page's transmission ends -
    the next header in its magazine.
    """

    MAX_SUBS = 80

    def __init__(self, on_page=None):
        self.lock = threading.Lock()
        self.pages: dict[tuple[int, int], dict[int, RxPage]] = {}
        self.current: dict[int, RxPage | None] = {}
        # Per magazine, of the transmission being received: the last display
        # row taken, and whether its X/27/0 has come.
        self.last_row: dict[int, int] = {}
        self.had_links: dict[int, bool] = {}
        self.on_page = on_page
        self.last_header: tuple[int, int, bytes] | None = None
        self.service: dict | None = None             # the last 8/30: initial page, status display
        self.stats = {"packets": 0, "rejected": 0, "parityErrors": 0, "headers": 0, "pages": 0}

    def clear(self) -> None:
        with self.lock:
            self.pages.clear()
            self.current.clear()
            self.last_row.clear()
            self.had_links.clear()
            self.last_header = None
            self.service = None
            for k in self.stats:
                self.stats[k] = 0

    def add(self, pkt: bytes, now: float | None = None) -> None:
        if len(pkt) != 42:
            return
        now = time.time() if now is None else now
        done = []
        with self.lock:
            self.stats["packets"] += 1
            ad = parse_address(pkt)
            if ad is None:
                self.stats["rejected"] += 1
                a = unham(pkt[0])
                # It might have been a header: of any magazine, or - the
                # magazine read, the row number not, and its low bit 0 - of
                # this one.
                if a < 0:
                    done = self._close(list(self.current))
                elif not a & 8:
                    done = self._close([(a & 7) or 8])
            else:
                mag, y = ad
                if y == 0:
                    done = self._header(mag, pkt, now)
                elif 1 <= y <= 25:
                    pg = self.current.get(mag)
                    if pg is not None:
                        if y <= self.last_row.get(mag, 0):
                            self.stats["rejected"] += 1
                            done = self._close([mag])
                        else:
                            self.last_row[mag] = y
                            self._row(pg, y, pkt[2:])
                elif y == 30 and mag == 8:
                    sp = parse_service_packet(pkt)
                    if sp is not None:
                        self.service = sp
                elif y == 27:
                    pg = self.current.get(mag)
                    if pg is not None and unham(pkt[2]) == 0:
                        if self.had_links.get(mag):
                            self.stats["rejected"] += 1
                            done = self._close([mag])
                        else:
                            self.had_links[mag] = True
                            self._links(pg, mag, pkt)
        if self.on_page is not None:
            for d in done:
                self.on_page(*d)

    def gap(self) -> None:
        """The signal broke (the decoder's frame says so): close every
        magazine's page, so nothing after the break is added to a page
        whose header came before it."""
        with self.lock:
            done = self._close(list(self.current))
        if self.on_page is not None:
            for d in done:
                self.on_page(*d)

    def _close(self, mags) -> list:
        closed = []
        for m in mags:
            pg = self.current.pop(m, None)
            self.last_row.pop(m, None)
            self.had_links.pop(m, None)
            if pg is not None:
                closed.append((pg.mag, pg.page, pg.sub))
        return closed

    def _header(self, mag: int, pkt: bytes, now: float) -> list:
        parsed = _parse_page_nibbles(pkt[2:8])
        c7 = unham(pkt[8])
        c11 = unham(pkt[9])
        if parsed is None or c7 < 0 or c11 < 0:
            self.stats["rejected"] += 1
            return self._close([mag])
        page, sub, (c4, c5, c6) = parsed
        ctl = c4 << 4 | c5 << 5 | c6 << 6 | c7 << 7 | c11 << 11
        self.stats["headers"] += 1
        text = self._chars(pkt[10:42], None)
        # The rolling header: every header's clock and text, whatever page it
        # belongs to, as a set shows while it waits for the one asked for.
        self.last_header = (mag, page, text)
        closed = self._close(list(self.current) if ctl & SERIAL else [mag])
        if page == NO_PAGE:
            return closed                   # time filling: closes the page, starts none
        subs = self.pages.setdefault((mag, page), {})
        pg = subs.get(sub)
        if pg is None:
            if len(subs) >= self.MAX_SUBS:
                subs.pop(min(subs, key=lambda s: subs[s].t))
            pg = subs[sub] = RxPage(mag, page, sub)
            self.stats["pages"] = sum(len(s) for s in self.pages.values())
        if ctl & ERASE:
            pg.rows = [None] * 26
            pg.links = []
        pg.ctl = ctl
        pg.header = text
        pg.t = now
        self.current[mag] = pg
        return closed

    def _chars(self, data: bytes, old) -> bytes:
        out = bytearray(len(data))
        bad = 0
        for i, b in enumerate(data):
            if PARITY_OK[b]:
                out[i] = b & 0x7F
            else:
                bad += 1
                out[i] = old[i] if old is not None and i < len(old) else 0x20
        self.stats["parityErrors"] += bad
        return bytes(out)

    def _row(self, pg: RxPage, y: int, data: bytes) -> None:
        pg.rows[y] = self._chars(data, pg.rows[y])

    def _links(self, pg: RxPage, mag: int, pkt: bytes) -> None:
        links = []
        for i in range(6):
            p = _parse_page_nibbles(pkt[3 + 6 * i:9 + 6 * i])
            if p is None:
                links.append(None)
                continue
            page, sub, (m1, m2, m3) = p
            lm = (mag & 7) ^ (m1 | m2 << 1 | m3 << 2)
            links.append(None if page == NO_PAGE else ((lm or 8), page, sub))
        pg.links = links

    # ------------------------------------------------------------- reading
    def get(self, mag: int, page: int, sub: int | None = None) -> RxPage | None:
        """A copy of the page: the sub-code asked for, else the newest."""
        with self.lock:
            subs = self.pages.get((mag, page))
            if not subs:
                return None
            pg = subs.get(sub) if sub is not None else max(subs.values(), key=lambda p: p.t)
            if pg is None:
                return None
            return RxPage(pg.mag, pg.page, pg.sub, pg.ctl, pg.header, list(pg.rows), list(pg.links), pg.t,
                          pg.errors)

    def subcodes(self, mag: int, page: int) -> list[int]:
        with self.lock:
            return sorted(self.pages.get((mag, page), {}))

    def labels(self) -> list[str]:
        with self.lock:
            return sorted(page_label(m, p) for (m, p) in self.pages)

    def header(self) -> tuple[int, int, bytes] | None:
        with self.lock:
            return self.last_header
