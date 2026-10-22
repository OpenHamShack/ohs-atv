"""The page model: a page is one or more subpages; a service is a folder of
pages.

A subpage is what a decoder holds at once: 25 rows of 40 seven-bit codes
(row 0 the header, rows 1-23 the page, row 24 the Fastext labels), its
subcode, its page-status word, its cycle time and its six Fastext links.
Everything here is stored exactly as it will be written - bytes, not
styled text - because the codes *are* the page: a colour is a byte in a
cell, and an editor that kept anything richer would have to reinvent the
decoder to save. `ohstxt.decoder` is the only thing that turns these bytes
into what a television shows.

Rows 25-28 (X/25 and the X/26-X/28 enhancement packets) are kept as the
opaque .tti text they arrived as and written back unchanged: this program
edits Level 1 pages, but a page that came from a Level 2.5 editor must not
lose its enhancements by being opened and saved here.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

ROWS, COLS = 25, 40
NO_LINK = 0x8FF          # "no page" in an FL line and in X/27/0, as vbit2 writes it
LINK_NAMES = ("red", "green", "yellow", "cyan", "link 5", "index")

# Page-status bits (the MRG PS word; DESIGN.md has the table).
PS_TRANSMIT = 0x8000
PS_ERASE = 0x4000        # C4
PS_NEWSFLASH = 0x0001    # C5
PS_SUBTITLE = 0x0002     # C6
PS_SUPPRESS_HEADER = 0x0004  # C7
PS_UPDATE = 0x0008       # C8
PS_INTERRUPTED = 0x0010  # C9
PS_INHIBIT = 0x0020      # C10
PS_SERIAL = 0x0040       # C11
PS_NOS_MASK = 0x0380     # C12-C14, national option (000 English)


def blank_rows() -> list[bytearray]:
    return [bytearray(b" " * COLS) for _ in range(ROWS)]


def annex_a_subcode(index: int) -> int:
    """The subcode for the index-th subpage (1-based) of a carousel, as
    ETS 300 706 Annex A.1 numbers them and vbit2's RenumberSubpages does:
    0001-0009, 0010-0019 ... 0079 (decimal digits in the nibbles, tens no
    higher than 7), then on into the hundreds."""
    code = [0, 0, 0, 0]
    for _ in range(index):
        code[3] += 1
        if code[3] > 9:
            code[3] = 0
            code[2] += 1
            if code[2] > 7:
                code[2] = 0
                code[1] += 1
                if code[1] > 9:
                    code[1] = 0
                    code[0] += 1
                    if code[0] > 3:
                        code = [0, 0, 0, 0]
    return (code[0] << 12) | (code[1] << 8) | (code[2] << 4) | code[3]


@dataclass
class Subpage:
    rows: list[bytearray] = field(default_factory=blank_rows)
    subcode: int = 0
    status: int = PS_TRANSMIT
    cycle: int = 8                     # seconds (timed) or magazine cycles
    timed: bool = True                 # CT ...,T (seconds) or ...,C (cycles)
    region: int = 0                    # RE, the G0/G2 region (0 = western Europe)
    links: list[int] = field(default_factory=lambda: [NO_LINK] * 6)
    has_links: bool = False            # an FL line was read, or links were set
    extra_rows: dict[int, str] = field(default_factory=dict)   # rows 25-28, verbatim
    extra_lines: list[str] = field(default_factory=list)       # unknown commands inside this subpage
    hash_fields: dict[str, str] = field(default_factory=dict)  # edit.tf keys from an import, given back on export
    damaged: set = field(default_factory=set)  # (row, col) received with errors (ohstxt.receive); never written

    def copy(self) -> "Subpage":
        return copy.deepcopy(self)

    def get(self, row: int, col: int) -> int:
        return self.rows[row][col]

    def put(self, row: int, col: int, code: int):
        self.rows[row][col] = code & 0x7F

    def text(self, row: int) -> str:
        from .charset import UNICODE
        return "".join(UNICODE.get(c, " ") for c in self.rows[row])

    def is_blank(self) -> bool:
        return all(all(c == 0x20 for c in r) for r in self.rows[1:])

    def set_link(self, i: int, page: int):
        self.links[i] = page if valid_page(page) else NO_LINK
        self.has_links = any(lk != NO_LINK for lk in self.links) or self.has_links


@dataclass
class Page:
    number: int = 0x100                # magazine and page, 0x100-0x8FF
    description: str = ""
    subpages: list[Subpage] = field(default_factory=lambda: [Subpage()])
    head_lines: list[str] = field(default_factory=list)   # DS, SP, MS... before the first PN, kept verbatim
    filename: str = ""                 # the file it came from in its service folder, if any
    live: object = None                # a ohstxt.live.Sidecar when the page is live (never written to the .tti)

    def copy(self) -> "Page":
        return copy.deepcopy(self)

    @property
    def magazine(self) -> int:
        return (self.number >> 8) & 7 or 8

    def label(self) -> str:
        return f"{self.number:03X}"

    def renumber(self):
        """Give the subpages the subcodes vbit2 will give them anyway: 0000
        for a single page, Annex A.1 numbering for a carousel."""
        if len(self.subpages) == 1:
            return
        for i, s in enumerate(self.subpages, 1):
            s.subcode = annex_a_subcode(i)


def valid_page(n: int) -> bool:
    """A displayable page number: magazine 1-8, page 00-FF (FF excluded:
    mFF is the time-filling page, never a link target)."""
    return 0x100 <= n <= 0x8FE and (n & 0xFF) != 0xFF


def parse_page_number(s: str) -> int | None:
    s = s.strip().upper().removeprefix("P")
    if len(s) != 3:
        return None
    try:
        n = int(s, 16)
    except ValueError:
        return None
    return n if 0x100 <= n <= 0x8FF else None
