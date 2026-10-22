"""Teletext services from files: MRG .tti pages (vbit2's format) and .t42
packet streams.

A **service** is a folder: every `*.tti` in it is one page (or one carousel
of subpages); if it holds no .tti but a `*.t42`, that stream is sent as it
is. OpenHamShack Teletext (a separate program) edits and saves both; nothing of
it is imported here - the format is the contract, as it is between OpenHamShack
NB and ATV.

The format, as MRG Systems' "TTI Page file format" v1.1 (2005) defines it
and vbit2 reads it (github.com/peterkvt80/vbit2 wiki, "Page files"):

    DE,description            for the editor's list; kept, not sent
    PN,mppss                  page: magazine 1-8, page hex 00-FF, subpage
                              decimal. Each PN starts a subpage; several
                              in one file make a carousel
    SC,ssss                   sub-code, hex (single pages only: a carousel's
                              subpages are numbered 0001, 0002... as EN 300
                              706 Annex A.1 has it)
    PS,ssss                   page status, hex: bit 15 transmit, 14 C4
                              erase, 0 C5 newsflash, 1 C6 subtitle, 2 C7
                              suppress header, 3 C8 update, 4 C9
                              interrupted sequence, 5 C10 inhibit display,
                              6 C11 serial, 7-9 C12-C14 national option
    CT,n,T|C                  how long this subpage stays up: n seconds
                              (T), or n magazine cycles (C)
    OL,rr,<40 characters>     row rr (1-25); control codes 00-1F either
                              with bit 7 set (80-9F) or as ESC (1B)
                              followed by the code + 40 (OpenHamShack
                              Teletext writes the ESC form)
    FL,mpp x 6                links: red, green, yellow, cyan, next, index
                              (0 or 8FF: none)

Decisions, where the format leaves room:

- **A file with no PS line is sent.** vbit2 defaults PS to 0000 and so
  never sends such a page; an editor that leaves PS out almost certainly
  meant the page to go out, and a silent nothing is the worst answer. A PS
  without bit 15 is honoured: that page is not sent.
- **Serial mode (C11) is cleared.** The carousel interleaves magazines
  (ohsatv/carousel.py), which is parallel mode; a header claiming serial
  would make every receiver close every other magazine's page on it.
- **8-bit bytes.** A .tti is bytes, not text. An OL row is read byte by
  byte as vbit2 does: ESC then a character is that character modulo 40,
  any other byte under 20 ends the row, the rest modulo 80 (so the MRG
  form, controls as 80-9F, reads too). PS and CT after a PN carry forward
  to later subpages; FL values outside 100-8FF are "no link"; a UTF-8 BOM
  is skipped (OpenHamShack Teletext's DESIGN.md, "What is read", agrees).
- **A file re-saved as UTF-8 is read as text.** An MRG file's high-bit
  bytes, opened in a text editor and saved again, come back as UTF-8: a
  control 0x80+n as the two bytes C2 80+n, a Latin-1 '£' (A3) as C2 A3. A
  whole file that decodes as UTF-8 and has any byte above 7F is therefore
  read as code points, each modulo 128, as the bytes would have been - so
  U+0081 is alpha red and U+00A3 is 23, '£' in the English set. (This
  first looked only for a control's C2 80-9F, so a file whose one non-
  ASCII character was a '£' was read byte by byte and the row gained a 'B'
  before it and lost its last cell.) Code points above FF come from a
  Windows editor that took the bytes as code page 1252 first (0x82 is
  U+201A there): they are mapped back through 1252, and anything else is
  '?'. A raw high-bit file cannot be taken for UTF-8: its controls (80-9F)
  are UTF-8 continuation bytes, which never follow the ',' or the ASCII
  before them. OpenHamShack Teletext itself writes 7-bit ESC form only, which
  reads the same either way, so this costs its files nothing.
- Rows 26-29 (vbit2's enhancement extensions) and OL,0 (its header
  template) are skipped: Level 1 only, said in `Service.notes`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from . import ttx

ESC = 0x1B


@dataclass
class Service:
    pages: dict = field(default_factory=dict)        # (mag, page) -> ttx.Page
    packets: list = field(default_factory=list)      # a .t42 stream, sent as it is
    notes: list = field(default_factory=list)        # what was skipped or wrong, sentences
    path: str = ""

    def __len__(self):
        return len(self.pages) or (1 if self.packets else 0)

    def labels(self) -> list[str]:
        return sorted(p.label for p in self.pages.values())


def _code(ch: str) -> int:
    """A character of a UTF-8 file -> the byte it was before re-encoding."""
    c = ord(ch)
    if c <= 0xFF:
        return c
    try:
        return ch.encode("cp1252")[0]
    except UnicodeEncodeError:
        return 0x3F                              # '?': nothing a teletext set has


def decode_row(raw: bytes, utf8: bool = False) -> bytes:
    """An OL row's bytes -> 7-bit teletext codes, padded or cut to 40, as
    vbit2's TTXLine reads them: ESC then the next character modulo 0x40 is
    a control code; any other byte below 0x20 ends the row (so a stray CR
    is harmless); everything else is taken modulo 0x80 (the MRG form,
    controls as 80-9F, among them)."""
    codes = [_code(c) for c in raw.decode("utf-8")] if utf8 else list(raw)
    out = bytearray()
    i = 0
    while i < len(codes):
        c = codes[i]
        if c == ESC:
            if i + 1 < len(codes):
                out.append(codes[i + 1] & 0x3F)
            i += 2
            continue
        if c < 0x20:
            break
        out.append(c & 0x7F)
        i += 1
    return bytes(out[:40]).ljust(40, b" ")


def _utf8_file(data: bytes) -> bool:
    """True if the file is UTF-8 text with something beyond ASCII in it (a
    pure 7-bit file reads the same either way; see the module docstring)."""
    if data.isascii():
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _mpp(s: str) -> tuple[int, int] | None:
    s = s.strip()
    if s in ("", "0"):
        return None
    try:
        v = int(s, 16)
    except ValueError:
        return None
    # As vbit2 and OpenHamShack Teletext read it: below 100 or above 8FF is no link.
    if not 0x100 <= v <= 0x8FF or (v & 0xFF) == ttx.NO_PAGE:
        return None
    return v >> 8, v & 0xFF


def ps_to_ctl(ps: int) -> tuple[int, bool]:
    """The PS word -> (control bits, transmit)."""
    ctl = 0
    if ps & 0x4000:
        ctl |= ttx.C4
    for bit, c in ((0, ttx.C5), (1, ttx.C6), (2, ttx.C7), (3, ttx.C8), (4, ttx.C9), (5, ttx.C10)):
        if ps & (1 << bit):
            ctl |= c
    ctl |= ((ps >> 7) & 7) << 12               # national option C12-C14
    # C11 (serial) deliberately not taken: see the module docstring.
    return ctl, bool(ps & 0x8000)


def parse_tti(data: bytes, name: str = "") -> tuple[ttx.Page | None, list[str]]:
    """One .tti file -> (page, notes). None if it has no usable page."""
    notes: list[str] = []
    utf8 = _utf8_file(data)
    mag = page = None
    desc = ""
    cur: ttx.Subpage | None = None
    subs: list[ttx.Subpage] = []
    defaults = {"ps": None, "cycle": 8.0, "mode": "T"}
    skipped = set()
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]                          # a UTF-8 byte-order mark
    for raw in data.replace(b"\r\n", b"\n").replace(b"\r", b"\n").split(b"\n"):
        if len(raw) < 2:
            continue
        cmd = raw[:2].decode("latin-1").upper()
        rest = raw[3:] if len(raw) > 2 and raw[2:3] == b"," else raw[2:]
        if cmd == "OL":
            # The row text may itself contain commas: split once.
            num, _, text = rest.partition(b",")
            try:
                y = int(num.decode("latin-1").strip())
            except ValueError:
                notes.append(f"{name}: an OL line with no row number")
                continue
            if cur is None:
                cur = ttx.Subpage(cycle=defaults["cycle"], cycle_mode=defaults["mode"])
                subs.append(cur)
            if 1 <= y <= 25:
                cur.rows[y] = decode_row(text, utf8)
            elif y != 0:                          # row 0: an editor's header template; the header is ours
                skipped.add(y)
            continue
        args = rest.decode("utf-8" if utf8 else "latin-1").strip()
        if cmd == "DE":
            desc = args
        elif cmd == "PN":
            try:
                v = args.split(",")[0].strip()
                m = int(v[0])
                pg = int(v[1:3], 16)
                if not 1 <= m <= 8:
                    raise ValueError
            except (ValueError, IndexError):
                notes.append(f"{name}: page number {args!r} is not mppss")
                continue
            if mag is None:
                mag, page = m, pg
            elif (m, pg) != (mag, page):
                notes.append(f"{name}: PN {args} differs from the file's first page {ttx.page_label(mag, page)}; "
                             "taken as another subpage of it")
            cur = ttx.Subpage(cycle=defaults["cycle"], cycle_mode=defaults["mode"])
            if defaults["ps"] is not None:
                cur.ctl, cur.transmit = ps_to_ctl(defaults["ps"])
            subs.append(cur)
        elif cmd == "SC":
            try:
                v = int(args.split(",")[0], 16)
            except ValueError:
                notes.append(f"{name}: sub-code {args!r} is not hex")
                continue
            if cur is not None:
                cur.sub = v & 0x3F7F
        elif cmd == "PS":
            try:
                v = int(args.split(",")[0], 16)
            except ValueError:
                notes.append(f"{name}: page status {args!r} is not hex")
                continue
            # Before the first PN a default; after one, this subpage's - and
            # it carries forward to the subpages after it, as vbit2 keeps it.
            defaults["ps"] = v
            if cur is not None:
                cur.ctl, cur.transmit = ps_to_ctl(v)
        elif cmd == "CT":
            parts = [x.strip() for x in args.split(",")]
            try:
                n = float(parts[0])
            except (ValueError, IndexError):
                notes.append(f"{name}: cycle time {args!r} is not a number")
                continue
            mode = parts[1].upper()[:1] if len(parts) > 1 and parts[1] else "T"
            mode = mode if mode in ("T", "C") else "T"
            n = max(1.0, n)
            defaults["cycle"], defaults["mode"] = n, mode
            if cur is not None:
                cur.cycle, cur.cycle_mode = n, mode
        elif cmd == "FL":
            links = []
            for s in args.split(",")[:6]:
                mp = _mpp(s)
                links.append(None if mp is None else (mp[0], mp[1], ttx.NO_SUB))
            if cur is None:
                cur = ttx.Subpage(cycle=defaults["cycle"], cycle_mode=defaults["mode"])
                subs.append(cur)
            cur.links = links
        # Anything else (SP, RE, PF, MS, ...) is an editor's or vbit2's own:
        # nothing a Level 1 page needs.
    if skipped:
        notes.append(f"{name}: rows {', '.join(map(str, sorted(skipped)))} skipped (Level 1 sends rows 1-25)")
    if mag is None:
        if subs:
            notes.append(f"{name}: no PN line; sent as page 1FF is not useful, so skipped")
        return None, notes
    if len(subs) > 1:
        # A carousel: subpages numbered 0001, 0002... (Annex A.1), in BCD as
        # the page number is.
        for i, s in enumerate(subs):
            n = i + 1
            s.sub = (n // 10) << 4 | (n % 10)
    return ttx.Page(mag, page, subs, desc), notes


def read_t42(data: bytes) -> list[bytes]:
    n = len(data) // 42
    return [bytes(data[i * 42:(i + 1) * 42]) for i in range(n)]


def _ttis(files) -> list:
    # A name starting with "." is an editor's temporary (OpenHamShack Teletext
    # writes ".P100.tti.tmp" and renames it over the page): never a page.
    return [f for f in files if f.suffix.lower() in (".tti", ".ttix") and not f.name.startswith(".")]


def _assemble(per_file: dict, svc: "Service") -> None:
    """Pages from {file name: Page}, in file-name order, into svc.pages."""
    svc.pages = {}
    for name in sorted(per_file):
        pg = per_file[name]
        key = (pg.mag, pg.page)
        if key in svc.pages:
            # Two files, one page number: their subpages make one carousel.
            old = svc.pages[key]
            merged = ttx.Page(old.mag, old.page, old.subpages + pg.subpages, old.description)
            for i, sp in enumerate(merged.subpages):
                sp.sub = ((i + 1) // 10) << 4 | ((i + 1) % 10)
            svc.pages[key] = merged
            svc.notes.append(f"{name}: page {pg.label} is also in another file; its subpages were added")
        else:
            svc.pages[key] = pg


def load_service(path: str | os.PathLike) -> Service:
    """A folder of .tti (or one .tti / .t42 file) -> Service. Never raises
    for content: what could not be read is in `notes`. Raises OSError for a
    path that is not there."""
    p = Path(path).expanduser()
    svc = Service(path=str(p))
    if p.is_dir():
        files = sorted(x for x in p.iterdir() if x.is_file())
    elif p.is_file():
        files = [p]
    else:
        raise OSError(f"{p} is not there")
    ttis = _ttis(files)
    per_file = {}
    for f in ttis:
        try:
            data = f.read_bytes()
        except OSError as e:
            svc.notes.append(f"{f.name}: {e.strerror or e}")
            continue
        pg, notes = parse_tti(data, f.name)
        svc.notes += notes
        if pg is not None:
            per_file[f.name] = pg
    _assemble(per_file, svc)
    if not ttis:
        t42s = [f for f in files if f.suffix.lower() == ".t42"]
        for f in t42s[:1]:
            try:
                svc.packets = read_t42(f.read_bytes())
            except OSError as e:
                svc.notes.append(f"{f.name}: {e.strerror or e}")
        if len(t42s) > 1:
            svc.notes.append(f"{len(t42s)} .t42 files: only {t42s[0].name} is sent")
    return svc


class FolderWatch:
    """The service folder while on air: pages added, changed or removed
    reach the carousel without a restart or a break in transmission.

    Polled (`poll()`, every half second from the transmitter): one stat a
    file, nothing read unless something changed. A Qt file watcher would
    put the engine in Qt's hands; polling a folder of a few hundred files
    twice a second costs well under a millisecond.

    **Half-written files.** A file is read only once its size and time
    have held for one poll (`settle` seconds) - an editor writing in place
    is finished by then - and if it does not parse to a page, the page it
    was keeps going out and a note says so. OpenHamShack Teletext writes to a
    temporary name and renames it over the page, which this sees as one
    clean change; a live-data generator rewriting pages every minute or so
    is the case this exists for. A file gone for one settle period is a
    page removed. Only `.tti` files are pages: the editor's live runner
    keeps its sidecars and state beside them (`*.live.json`,
    `station.json`, `beacons.csv`, `.ohstxt-live-state.json`) and writes each
    page whole to `.<name>.tti.tmp` before renaming it over the page, so
    the `.tti` files are always complete and nothing else is read.

    `poll(now)` returns a new Service when the pages changed, else None.
    `notes` collects what was kept and why (the newest last)."""

    def __init__(self, path: str | os.PathLike, settle: float = 0.4):
        self.path = Path(path).expanduser()
        self.settle = settle
        self.seen: dict[str, tuple] = {}         # name -> signature that is loaded (or failed)
        self.pending: dict[str, tuple] = {}      # name -> (signature, first seen at)
        self.per_file: dict[str, ttx.Page] = {}
        self.notes: list[str] = []
        self.t42_sig = None
        self.svc = Service(path=str(self.path))
        self._scan(initial=True)
        _assemble(self.per_file, self.svc)

    def _sigs(self) -> dict:
        out = {}
        try:
            for f in _ttis(x for x in self.path.iterdir() if x.is_file()):
                try:
                    st = f.stat()
                except OSError:
                    continue
                out[f.name] = (st.st_mtime_ns, st.st_size)
        except OSError:
            pass
        return out

    def _read(self, name: str):
        try:
            data = (self.path / name).read_bytes()
        except OSError as e:
            return None, [f"{name}: {e.strerror or e}"]
        return parse_tti(data, name)

    def _scan(self, initial: bool = False, now: float = 0.0) -> bool:
        sigs = self._sigs()
        changed = False
        for name, sig in sigs.items():
            if self.seen.get(name) == sig:
                self.pending.pop(name, None)
                continue
            if not initial:
                p = self.pending.get(name)
                if p is None or p[0] != sig:
                    self.pending[name] = (sig, now)           # changing: wait for it to hold still
                    continue
                if now - p[1] < self.settle:
                    continue
            self.pending.pop(name, None)
            self.seen[name] = sig
            pg, notes = self._read(name)
            if pg is None:
                if name in self.per_file:
                    self.notes.append(f"{name} did not read as a page; page "
                                      f"{self.per_file[name].label} stays as it was")
                else:
                    self.notes += notes or [f"{name} did not read as a page"]
                continue
            self.per_file[name] = pg
            changed = True
        for name in list(self.seen):
            if name in sigs:
                continue
            p = self.pending.get(name)
            if not initial and (p is None or p[0] is not None):
                self.pending[name] = (None, now)
                continue
            if not initial and now - p[1] < self.settle:
                continue
            self.pending.pop(name, None)
            del self.seen[name]
            if self.per_file.pop(name, None) is not None:
                changed = True
        return changed

    def poll(self, now: float) -> Service | None:
        if not self._scan(now=now):
            return None
        svc = Service(path=str(self.path))
        _assemble(self.per_file, svc)
        self.svc = svc
        return svc


# --- writing -------------------------------------------------------------------
def ctl_to_ps(ctl: int, transmit: bool = True) -> int:
    """Control bits -> the PS word (ps_to_ctl's inverse; C11 never set:
    pages are sent in parallel mode)."""
    ps = 0x8000 if transmit else 0
    if ctl & ttx.C4:
        ps |= 0x4000
    for bit, c in ((0, ttx.C5), (1, ttx.C6), (2, ttx.C7), (3, ttx.C8), (4, ttx.C9), (5, ttx.C10)):
        if ctl & c:
            ps |= 1 << bit
    ps |= ((ctl >> 12) & 7) << 7
    return ps


def encode_row(row: bytes) -> bytes:
    """40 codes -> an OL line's text in vbit2's 7-bit dialect: a code under
    0x20 as ESC then the code + 0x40 (ESC itself as ESC '['), the rest as
    it is."""
    out = bytearray()
    for c in bytes(row[:40]).ljust(40, b" "):
        c &= 0x7F
        if c < 0x20:
            out += bytes((ESC, c + 0x40))
        else:
            out.append(c)
    return bytes(out)


def dumps(page: ttx.Page) -> bytes:
    """A page as a .tti file the way OpenHamShack Teletext writes one (its
    DESIGN.md, "What is written") and vbit2 reads it: CRLF, 7-bit, one
    subpage block after another - PN (ss 00 for a single page, else 01,
    02 ...), SC, PS, CT after its PN, the rows that are not blank (1-24;
    25 if present), FL (8FF for no link) only if there are links. The
    header row is not written: it is the transmitter's (its clock and
    name), not the page's."""
    lines = [f"DE,{page.description or 'Page ' + page.label}"]
    many = len(page.subpages) > 1
    for i, s in enumerate(page.subpages):
        n = (i + 1) % 100 if many else 0
        lines.append(f"PN,{page.label}{n:02d}")
        lines.append(f"SC,{(s.sub if not many else ((i + 1) // 10) << 4 | ((i + 1) % 10)):04X}")
        lines.append(f"PS,{ctl_to_ps(s.ctl, s.transmit):04X}")
        cyc = max(1, min(255, int(round(s.cycle))))
        lines.append(f"CT,{cyc},{s.cycle_mode if s.cycle_mode in ('T', 'C') else 'T'}")
        for y in range(1, 26):
            r = s.rows[y] if y < len(s.rows) else None
            if r is None or not bytes(r).strip(b" "):
                continue
            lines.append(b"OL,%d," % y + encode_row(r))
        if any(ln is not None for ln in s.links):
            labels = []
            for ln in list(s.links)[:6] + [None] * (6 - len(s.links[:6])):
                labels.append("8FF" if ln is None else ttx.page_label(ln[0], ln[1]))
            lines.append("FL," + ",".join(labels))
    return b"\r\n".join(x if isinstance(x, bytes) else x.encode("ascii") for x in lines) + b"\r\n"


def write_atomic(path: str | os.PathLike, data: bytes) -> None:
    """Write as the editor does - a dot-file beside it, renamed over - so a
    folder being watched (FolderWatch, vbit2) never reads half a page."""
    p = Path(path)
    tmp = p.with_name("." + p.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, p)
