"""Received material: .t42 captures back into pages.

    python3 -m ohstxt.receive CAPTURE.t42 [--out FOLDER]

A capture - OpenHamShack ATV saving what it received, an off-air recording, an
archive recovered from a videotape - is packets in the order they arrived,
magazines interleaved, pages repeated as often as the carousel came round,
and errors wherever the signal was weak. This turns it back into pages:

- **Addresses** (MRAG and the header's page, subcode and control bits) are
  Hamming 8/4: a single bit error is corrected, a double error makes the
  packet (or the header's field) unusable. A packet whose address cannot
  be read is dropped; a header whose page number cannot be read ends the
  page in progress for its magazine, so the rows after it are not filed
  under the wrong page.
- **Rows** follow the last header of their magazine (parallel
  transmission, as vbit2 sends). A header with C11 set (serial) ends every
  magazine's page. Page mFF headers are time filling, never a page.
- **Characters** carry odd parity. A byte with bad parity is wrong in at
  least one bit and nothing says which, so every reception of a page is
  kept and each cell is decided by vote among the copies that arrived
  with good parity. A cell with no good copy is **damaged**: it keeps the
  commonest value it arrived as, but a damaged control code becomes a
  space - a wrong colour or double-height code would repaint the rest of
  its row, which is worse than a missing letter.
- **Subpages** are gathered by subcode, in subcode order (a capture that
  starts mid-carousel still lists the carousel from its first subpage).
- Rows never received are blank and not damaged: broadcasters (unlike
  ohstxt.t42) do not send blank rows.
- Fastext links come from X/27/0, link by link, by vote.

Rows 25-31 other than X/27/0 (enhancement data, broadcast service data)
are counted and left out: this program edits Level 1 pages.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .page import COLS, NO_LINK, PS_ERASE, PS_TRANSMIT, ROWS, Page, Subpage
from .t42 import PACKET, parity_ok, unham84


@dataclass
class Reception:
    """One time a subpage came past: what arrived, cell by cell."""
    number: int
    subcode: int
    status: int
    rows: list[bytearray] = field(default_factory=lambda: [bytearray(b" " * COLS) for _ in range(ROWS)])
    good: list[list[bool]] = field(default_factory=lambda: [[False] * COLS for _ in range(ROWS)])
    present: list[bool] = field(default_factory=lambda: [False] * ROWS)
    links: list[int | None] | None = None


@dataclass
class RxSubpage:
    subcode: int
    receptions: int
    sub: Subpage
    damaged: set[tuple[int, int]]


@dataclass
class RxPage:
    number: int
    subpages: list[RxSubpage]

    @property
    def damaged(self) -> int:
        return sum(len(s.damaged) for s in self.subpages)

    @property
    def receptions(self) -> int:
        return sum(s.receptions for s in self.subpages)

    def to_page(self) -> Page:
        subs = []
        for s in self.subpages:
            sub = s.sub.copy()
            sub.damaged = set(s.damaged)
            subs.append(sub)
        return Page(number=self.number, description=f"Received P{self.number:03X}", subpages=subs)


@dataclass
class Capture:
    pages: list[RxPage] = field(default_factory=list)
    packets: int = 0
    bad_address: int = 0          # MRAG uncorrectable: packet dropped
    bad_headers: int = 0          # page number uncorrectable: page in progress ended
    orphan_rows: int = 0          # rows with no page in progress for their magazine
    corrected: int = 0            # Hamming bytes with a single error, corrected
    skipped_rows: int = 0         # enhancement and service packets, not used here

    def find(self, number: int) -> RxPage | None:
        return next((p for p in self.pages if p.number == number), None)

    def summary(self) -> str:
        dmg = sum(p.damaged for p in self.pages)
        return (f"{len(self.pages)} pages from {self.packets} packets; {dmg} damaged cells; "
                f"{self.corrected} address bytes corrected, {self.bad_address} packets and "
                f"{self.bad_headers} headers unreadable, {self.orphan_rows} rows with no page")


def _ham(cap: Capture, b: int) -> int:
    from .t42 import HAM84_DECODE
    n, err = HAM84_DECODE[b & 0xFF]
    if err == 1:
        cap.corrected += 1
    return n


def decode(data: bytes) -> Capture:
    cap = Capture()
    current: dict[int, Reception | None] = {}
    done: list[Reception] = []

    def close(mag: int):
        r = current.pop(mag, None)
        if r is not None:
            done.append(r)

    for i in range(0, len(data) - PACKET + 1, PACKET):
        p = data[i:i + PACKET]
        cap.packets += 1
        a, b = _ham(cap, p[0]), _ham(cap, p[1])
        if a < 0 or b < 0:
            cap.bad_address += 1
            continue
        mag = (a & 7) or 8
        row = (a >> 3) | (b << 1)
        body = p[2:]
        if row == 0:
            n = [_ham(cap, x) for x in body[:8]]
            close(mag)
            if n[0] < 0 or n[1] < 0:
                cap.bad_headers += 1
                continue
            page_byte = (n[1] << 4) | n[0]
            nz = [max(0, x) for x in n]
            c11 = bool(nz[7] & 1)
            if c11:
                for m in list(current):
                    close(m)
            if page_byte == 0xFF:
                continue                       # time filling: not a page
            sub = nz[2] | ((nz[3] & 7) << 4) | (nz[4] << 8) | ((nz[5] & 3) << 12)
            st = PS_TRANSMIT
            st |= PS_ERASE if nz[3] & 8 else 0
            st |= 0x0001 if nz[5] & 4 else 0
            st |= 0x0002 if nz[5] & 8 else 0
            st |= (nz[6] & 0xF) << 2
            st |= (nz[7] & 0xE) << 6
            rec = Reception(number=(mag << 8) | page_byte, subcode=sub, status=st)
            for c, x in enumerate(body[8:40]):
                rec.rows[0][8 + c] = x & 0x7F
                rec.good[0][8 + c] = parity_ok(x)
            rec.present[0] = True
            current[mag] = rec
        elif row <= 24:
            rec = current.get(mag)
            if rec is None:
                cap.orphan_rows += 1
                continue
            rec.rows[row] = bytearray(x & 0x7F for x in body)
            rec.good[row] = [parity_ok(x) for x in body]
            rec.present[row] = True
        elif row == 27:
            rec = current.get(mag)
            if rec is None or _ham(cap, body[0]) != 0:
                cap.skipped_rows += 1
                continue
            links: list[int | None] = []
            for k in range(6):
                g = [_ham(cap, x) for x in body[1 + 6 * k:7 + 6 * k]]
                if min(g[0], g[1], g[3], g[5]) < 0:
                    links.append(None)          # this link unreadable; the others may be fine
                    continue
                m = (((g[3] >> 3) & 1) | ((g[5] >> 1) & 6)) ^ (mag & 7)
                lk = ((m or 8) << 8) | (g[1] << 4) | g[0]
                links.append(NO_LINK if (lk & 0xFF) == 0xFF else lk)
            rec.links = links
        else:
            cap.skipped_rows += 1
    for m in list(current):
        close(m)
    cap.pages = _gather(done)
    return cap


def _vote(values_good: list[tuple[int, bool]]) -> tuple[int, bool]:
    """(value, damaged) for one cell from every copy of it."""
    good = [v for v, ok in values_good if ok]
    if good:
        return Counter(good).most_common(1)[0][0], False
    v = Counter(v for v, _ in values_good).most_common(1)[0][0]
    return (0x20 if v < 0x20 else v), True


def _merge(recs: list[Reception]) -> RxSubpage:
    sub = Subpage(subcode=recs[0].subcode)
    damaged: set[tuple[int, int]] = set()
    for r in range(ROWS):
        copies = [rec for rec in recs if rec.present[r]]
        if not copies:
            continue
        for c in range(COLS):
            if r == 0 and c < 8:
                continue
            v, bad = _vote([(rec.rows[r][c], rec.good[r][c]) for rec in copies])
            sub.rows[r][c] = v
            if bad:
                damaged.add((r, c))
    sub.status = Counter(rec.status for rec in recs).most_common(1)[0][0]
    with_links = [rec.links for rec in recs if rec.links is not None]
    if with_links:
        links = []
        for k in range(6):
            got = [lk[k] for lk in with_links if lk[k] is not None]
            links.append(Counter(got).most_common(1)[0][0] if got else NO_LINK)
        sub.links = links
        sub.has_links = True
    return RxSubpage(subcode=recs[0].subcode, receptions=len(recs), sub=sub, damaged=damaged)


def _gather(done: list[Reception]) -> list[RxPage]:
    groups: dict[int, dict[int, list[Reception]]] = {}
    for rec in done:
        groups.setdefault(rec.number, {}).setdefault(rec.subcode, []).append(rec)
    pages = []
    for number in sorted(groups):
        subs = [_merge(groups[number][sc]) for sc in sorted(groups[number])]
        if len(subs) > 1:
            # C4 is how a carousel is sent (every subpage erases the last),
            # not something its subpages each mean
            for s in subs:
                s.sub.status &= ~PS_ERASE
        pages.append(RxPage(number=number, subpages=subs))
    return pages


def load(path) -> Capture:
    return decode(Path(path).read_bytes())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m ohstxt.receive", description="Pages from a .t42 capture")
    ap.add_argument("capture")
    ap.add_argument("--out", help="write every page found to this folder as .tti")
    a = ap.parse_args(argv)
    try:
        cap = load(a.capture)
    except OSError as e:
        print(f"{a.capture}: {e.strerror or e}", file=sys.stderr)
        return 2
    for p in cap.pages:
        subs = ", ".join(f"{s.subcode:04X}×{s.receptions}" + (f" ({len(s.damaged)} damaged)" if s.damaged else "")
                         for s in p.subpages)
        print(f"P{p.number:03X}  {subs}")
    print(cap.summary())
    if a.out:
        from . import tti
        out = Path(a.out)
        for p in cap.pages:
            tti.save(p.to_page(), out / f"P{p.number:03X}.tti")
        print(f"wrote {len(cap.pages)} pages to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
