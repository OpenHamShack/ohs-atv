"""The service checker: what is wrong with a folder of pages before it goes
to air.

    python3 -m ohstxt.check SERVICE_DIR [--callsign CALL] [--lines N]
                           [--max-carousel SECONDS] [--budget PACKETS] [--json] [--strict]

Exit status 0 when nothing is an error (1 with --strict if anything is a
warning), 1 when something is, 2 for a bad argument - so a script can run
it before handing a folder to the inserter. The window runs the same checks
(Tools > Check service, and before Save all) and jumps to the cell a
finding names.

Every finding has a severity:

  error    the service is broken for a viewer: a Fastext key leads nowhere,
           there is no P100 for a set to start on, two files claim one
           page (the inserter sends one and hides the other), a row
           holds more than 40 cells (the rest never reaches a set), the
           station does not identify itself.
  warning  it works but probably not as meant: a page number written on a
           page that is not in the service, a row hidden under double
           height above it, a carousel whose turn is too long to wait for,
           a page over the size budget, no callsign to check against.
  info     worth knowing: double height on a row where it does nothing.

The time estimates assume the inserter sends `lines` teletext packets per
field, 50 fields a second, one subpage of each page per pass of its
magazine (vbit2's way), every row of a subpage sent (as ohstxt.t42 sends
them). They are estimates - an inserter that skips blank rows is faster -
and they are only used to turn "C" cycle counts into seconds and to say
how long a service takes to go round.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import decoder, tti
from .page import COLS, NO_LINK, ROWS, Page, valid_page

ERROR, WARNING, INFO = "error", "warning", "info"
SEVERITY_ORDER = {ERROR: 0, WARNING: 1, INFO: 2}

# A page number written on a page: three decimal digits, magazine 1-8,
# alone - not part of a longer number, a word or a decimal ("144.750"),
# and not followed by a single space and a word ("437 MHz", "333 kS/s",
# "100 W" are quantities) - or anything after a 'P', which is always a page.
# An index puts its page numbers at the ends of lines or before a gap, so
# this finds them; "see 201 for more" is missed, and is what P201 is for.
# Hex pages (1A5) are possible but no index writes them, and matching them
# would flag every hex-looking word.
PAGE_REF = re.compile(r"(?<![0-9A-Za-z.,])(?:P([1-8][0-9A-F]{2})|([1-8][0-9][0-9]))"
                      r"(?![0-9A-Za-z])(?![.,][0-9])(?(2)(?! [A-Za-z]))")


@dataclass
class Finding:
    severity: str
    kind: str
    message: str
    page: int | None = None
    filename: str = ""
    sub: int = 0
    row: int | None = None
    col: int | None = None

    def where(self) -> str:
        if self.page is None:
            return self.filename or "service"
        s = f"P{self.page:03X}"
        if self.sub:
            s += f"/{self.sub + 1}"
        if self.row is not None:
            s += f" r{self.row}"
            if self.col is not None:
                s += f" c{self.col}"
        return s


@dataclass
class Options:
    callsign: str = ""
    lines: int = 16                  # teletext packets per field
    max_carousel: float = 120.0      # seconds for a whole carousel to turn
    budget: int = 400                # packets a page may take, all subpages


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    pages: int = 0
    packets_per_second: float = 0.0
    cycle_seconds: float = 0.0       # one pass of the whole service

    def count(self, severity: str) -> int:
        return sum(1 for f in self.findings if f.severity == severity)

    def summary(self) -> str:
        e, w, i = self.count(ERROR), self.count(WARNING), self.count(INFO)
        if not self.findings:
            verdict = "no problems found"
        else:
            verdict = ", ".join(f"{n} {name}{'s' if n != 1 else ''}"
                                for n, name in ((e, "error"), (w, "warning"), (i, "note")) if n)
        return (f"{self.pages} pages, one pass about {self.cycle_seconds:.1f} s at "
                f"{self.packets_per_second:.0f} packets/s: {verdict}")


def subpage_packets(sub) -> int:
    """Packets one subpage costs on air: header, rows 1-24, X/27/0 if it has
    links, and any enhancement rows kept from elsewhere."""
    return 25 + (1 if sub.has_links and any(lk != NO_LINK for lk in sub.links) else 0) + len(sub.extra_rows)


def page_packets(page: Page) -> int:
    return sum(subpage_packets(s) for s in page.subpages)


def _displayed_text(rows, d, r: int) -> str:
    """The characters a viewer sees in row r: alpha cells as text, mosaics
    and control cells as spaces, a row hidden by double height as blank."""
    from .charset import SET_ALPHA, UNICODE
    if d.hidden_row[r]:
        return " " * COLS
    out = []
    for c in range(COLS):
        code = int(rows[r][c])
        out.append(UNICODE.get(code, " ") if code >= 0x20 and int(d.gset[r, c]) == SET_ALPHA else " ")
    return "".join(out)


def _has_station_token(text: str) -> bool:
    try:
        from .live.template import names_in
    except ImportError:
        return False
    return any(n in ("call", "callsign", "station.call", "station.callsign") for n in names_in(text))


def check_pages(pages: list[Page], raw: dict[str, bytes] | None = None, opts: Options | None = None) -> Report:
    """Check pages as the window holds them. `raw` maps file names to the
    bytes on disk, for what only the file shows (rows longer than 40 cells:
    reading a page cuts them)."""
    opts = opts or Options()
    rep = Report(pages=len(pages))
    F = rep.findings
    numbers = {pg.number for pg in pages}
    rep.packets_per_second = 50.0 * max(1, opts.lines)
    # One pass: every page sends one subpage. Carousels average their subpages.
    one_pass = sum(page_packets(pg) / len(pg.subpages) for pg in pages) if pages else 0
    rep.cycle_seconds = one_pass / rep.packets_per_second

    if 0x100 not in numbers:
        F.append(Finding(ERROR, "no-p100", "No P100: a set shows P100 when it is switched to teletext, and "
                         "this service has none"))

    by_number: dict[int, list[Page]] = {}
    for pg in pages:
        by_number.setdefault(pg.number, []).append(pg)
    for n, pgs in sorted(by_number.items()):
        if len(pgs) > 1:
            files = ", ".join(p.filename or "(unsaved)" for p in pgs)
            for p in pgs:
                F.append(Finding(ERROR, "duplicate", f"P{n:03X} is claimed by {len(pgs)} files ({files}); "
                                 "the inserter will send one and hide the others", page=n, filename=p.filename))

    callsign = opts.callsign.strip().upper()
    ident_found = False

    for pg in pages:
        # --- links (a carousel usually repeats its links on every subpage:
        # one finding, at the first, says how many)
        broken: dict[tuple[int, int], tuple[Finding, int]] = {}
        for si, sub in enumerate(pg.subpages):
            if sub.has_links:
                for i, lk in enumerate(sub.links):
                    if lk == NO_LINK or not valid_page(lk) or lk in numbers:
                        continue
                    if (i, lk) in broken:
                        f0, k = broken[(i, lk)]
                        broken[(i, lk)] = (f0, k + 1)
                        continue
                    name = ("red", "green", "yellow", "cyan", "fifth", "index")[i]
                    col = (1, 11, 21, 31)[i] if i < 4 else None
                    f0 = Finding(ERROR, "link", f"The {name} {'link' if i < 5 else 'link (index key)'} goes "
                                 f"to P{lk:03X}, which is not in the service",
                                 page=pg.number, filename=pg.filename, sub=si, row=24 if i < 4 else None, col=col)
                    broken[(i, lk)] = (f0, 1)
                    F.append(f0)
        for f0, k in broken.values():
            if k > 1:
                f0.message += f" (on {k} subpages)"
        for si, sub in enumerate(pg.subpages):
            d = decoder.decode(sub.rows)
            seen_refs = set()
            for r in range(1, 24):
                text = _displayed_text(sub.rows, d, r)
                for m in PAGE_REF.finditer(text):
                    grp = 1 if m.group(1) else 2
                    n = int(m.group(grp), 16)
                    if n in numbers or (n & 0xFF) == 0xFF or n in seen_refs:
                        continue
                    seen_refs.add(n)
                    F.append(Finding(WARNING, "index-link", f"\"{m.group(0)}\" on the page: P{n:03X} is not in "
                                     "the service", page=pg.number, filename=pg.filename, sub=si, row=r,
                                     col=m.start(grp)))
            # --- double height
            for r in range(ROWS):
                has_dh = any(c == 0x0D for c in sub.rows[r])
                if not has_dh or d.hidden_row[r]:
                    continue
                if r in (0, 23, 24):
                    F.append(Finding(INFO, "double-height", f"Double height on row {r} does nothing "
                                     "(the header and rows 23 and 24 have no row below for the lower half)",
                                     page=pg.number, filename=pg.filename, sub=si, row=r,
                                     col=bytes(sub.rows[r]).index(0x0D)))
                    continue
                if r + 1 < ROWS and d.hidden_row[r + 1]:
                    below = sub.rows[r + 1]
                    used = [c for c in range(COLS) if below[c] != 0x20]
                    if used:
                        F.append(Finding(WARNING, "double-height", f"Row {r + 1} has {len(used)} cells of its "
                                         f"own under double height on row {r}: a Level 1 set never shows "
                                         "them, and other decoders differ", page=pg.number,
                                         filename=pg.filename, sub=si, row=r + 1, col=used[0]))
            # --- identification
            if callsign and not ident_found:
                head = sub.text(0)[8:]
                body = [sub.text(r) for r in range(1, ROWS)] if pg.number == 0x100 else []
                for t in [head] + body:
                    if callsign in t.upper() or _has_station_token(t):
                        ident_found = True
                        break

        # --- carousel turn
        if len(pg.subpages) > 1:
            turn = sum(s.cycle if s.timed else s.cycle * max(rep.cycle_seconds, 0.001) for s in pg.subpages)
            if turn > opts.max_carousel:
                F.append(Finding(WARNING, "carousel", f"A full turn of the carousel takes about {turn:.0f} s "
                                 f"({len(pg.subpages)} subpages); a viewer may wait that long for one "
                                 f"(limit {opts.max_carousel:.0f} s)", page=pg.number, filename=pg.filename))
        # --- size
        n = page_packets(pg)
        if n > opts.budget:
            F.append(Finding(WARNING, "size", f"{n} packets in {len(pg.subpages)} subpages, over the budget of "
                             f"{opts.budget}: {n / rep.packets_per_second:.1f} s of the inserter's time",
                             page=pg.number, filename=pg.filename))

    # --- rows longer than a set shows: only the file knows
    for pg in pages:
        data = (raw or {}).get(pg.filename) if pg.filename else None
        if data is None:
            continue
        for si, r, cells in tti.row_lengths(data):
            if cells > COLS:
                F.append(Finding(ERROR, "long-row", f"Row {r} is {cells} cells in {pg.filename}; a set shows "
                                 f"40 and the last {cells - COLS} never reach the air", page=pg.number,
                                 filename=pg.filename, sub=si, row=r, col=COLS - 1))

    if not callsign:
        F.append(Finding(WARNING, "station-id", "No callsign in the station settings, so identification "
                         "cannot be checked"))
    elif not ident_found:
        F.append(Finding(ERROR, "station-id", f"{callsign} is not in any page's header row or on P100: the "
                         "service does not identify the station", page=0x100 if 0x100 in numbers else None))

    F.sort(key=lambda f: (SEVERITY_ORDER[f.severity], f.page if f.page is not None else -1, f.sub,
                          f.row if f.row is not None else -1))
    return rep


def check_folder(folder, opts: Options | None = None) -> Report:
    """Check a service folder as it is on disk. The callsign and line count
    come from station.json unless the options give them."""
    from . import station
    from .service import Service
    folder = Path(folder)
    opts = opts or Options()
    st = station.load(folder)
    if not opts.callsign:
        opts.callsign = st.callsign
    svc = Service.open(folder)
    raw = {}
    for pg in svc.pages:
        try:
            raw[pg.filename] = (folder / pg.filename).read_bytes()
        except OSError:
            pass
    rep = check_pages(svc.pages, raw, opts)
    for prob in svc.problems:
        name = prob.split(":", 1)[0]
        rep.findings.insert(0, Finding(ERROR, "unreadable", f"Not a page: {prob}", filename=name))
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m ohstxt.check", description="Check a teletext service folder")
    ap.add_argument("folder")
    ap.add_argument("--callsign", default="", help="the station's callsign (default: station.json's)")
    ap.add_argument("--lines", type=int, default=None, help="teletext lines per field the inserter uses (16)")
    ap.add_argument("--max-carousel", type=float, default=120.0, help="seconds a carousel may take to turn")
    ap.add_argument("--budget", type=int, default=400, help="packets a page may take, all subpages")
    ap.add_argument("--json", action="store_true", help="print the findings as JSON")
    ap.add_argument("--strict", action="store_true", help="exit 1 on warnings too")
    a = ap.parse_args(argv)
    folder = Path(a.folder)
    if not folder.is_dir():
        print(f"{folder}: not a folder", file=sys.stderr)
        return 2
    from . import station
    lines = a.lines if a.lines is not None else station.load(folder).vbi_lines
    rep = check_folder(folder, Options(callsign=a.callsign, lines=lines, max_carousel=a.max_carousel,
                                       budget=a.budget))
    if a.json:
        print(json.dumps({"summary": rep.summary(), "cycle_seconds": round(rep.cycle_seconds, 3),
                          "findings": [asdict(f) for f in rep.findings]}, indent=1))
    else:
        for f in rep.findings:
            print(f"{f.severity:<8}{f.where():<16}{f.message}")
        print(rep.summary())
    if rep.count(ERROR) or (a.strict and rep.count(WARNING)):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
