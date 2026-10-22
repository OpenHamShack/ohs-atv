"""The teletext carousel: which packet goes on which VBI line of which field.

A teletext service is sent as a loop - every page in turn, again and
again - and a receiver shows a page when it next comes round. This builds
that loop from a Service (ohsatv/tti.py), one field at a time:

- **Magazines interleave** (parallel mode, EN 300 706 clause 7.3): each
  magazine is its own sequence of pages in numerical order, and a field's
  lines are dealt out to the magazines in turn. A magazine with few pages
  then comes round often, and one big magazine cannot hold up the others.
- **Each page** goes out as its header (X/0), its rows X/1 - X/24 (all of
  them, blank ones as spaces, so an edit that empties a row clears it on
  the receiver too; X/25 if the page has one) and its links (X/27/0, if
  it has any) - packet for packet what vbit2 sends (tests/test_teletext.py,
  AgainstVbit2).
- **After a header, that magazine waits for the next field.** A header with
  C4 set tells the receiver to erase the page; real decoders need time to
  do it, and broadcasters left a field between a header and the rows
  after it (the "page erasure interval"). It costs nothing here: the other
  magazines fill the lines.
- **Carousels** (pages with several subpages) move on when the subpage's
  cycle time is up - in seconds of *transmission* time, counted from the
  frame number, not the wall clock, so a frame encoded late does not skip
  a subpage and the tests are exact; or after n complete cycles of its
  magazine (CT,n,C).
- **The header row** carries the station's name (the callsign: teletext is
  a part of the picture and the ident rule applies to the picture), the
  page number being sent - the "rolling header" a receiver shows while it
  waits - the date, and the clock in its last 8 characters, from the wall
  clock (`now`, injectable for tests).
- A line with nothing to send is left empty (no run-in, no framing code: a
  receiver sees no packet).

Once a second the broadcast service data packet 8/30 goes out first: the
initial page (`initial`, chosen on the transmit panel - a receiver opens
on it), the date and time, and a status line with the callsign.

A .t42 stream (Service.packets) is sent as it is, 1 packet a line, in a
loop: it is already a carousel, made by whatever wrote it.
"""
from __future__ import annotations

import time

from . import ttx

FIELD_S = 0.02


SUB_MAG, SUB_PAGE = 8, 0x88                      # subtitles: page 888, as UK broadcasters used
SUB_REPEAT_S = 2.0                               # a subtitle on screen is sent again this often


class _Mag:
    def __init__(self, mag: int, pages: list):
        self.mag = mag
        self.pages = sorted(pages, key=lambda p: p.page)
        self.idx = 0
        self.queue: list = []
        self.priority: list = []             # packets to send before the next page (subtitles)
        self.cycles = 0                      # complete passes through this magazine
        self.wait_field = -1                 # no more packets of this magazine in this field

    def busy(self) -> bool:
        return bool(self.queue or self.priority or self.pages)


class Carousel:
    def __init__(self, service, name: str = "OPENHAMSHACK", now=time.time, initial: str = "100", status: str = ""):
        # The header's service name has eight characters, so the default
        # stays OPENHAMSHACK after the OpenHamShack rename (OPENHAMSHACK is twelve).
        # It goes out only with no callsign set; the radio passes the callsign.
        self.name = (name or "OPENHAMSHACK").upper()[:8]
        self.now = now
        self.initial = ttx.parse_label(initial or "")
        # The status display is 20 characters: the callsign and the brand
        # fit for a callsign of up to seven; past that the brand gives way
        # to "ATV" rather than going out cut short.
        dflt = f"{self.name} OPENHAMSHACK"
        self.status = (status or (dflt if len(dflt) <= 20 else f"{self.name} ATV")).encode("ascii", "replace")[:20]
        self.mags: list[_Mag] = []
        self.turn = 0
        # Per page: (index of the subpage on the air, stream time it went
        # up, the magazine's cycle count when it went up).
        self.sub_state: dict[tuple[int, int], tuple[int, float, int]] = {}
        self.t42_pos = 0
        self.sent: dict[tuple[int, int], int] = {}      # (mag, page) -> headers sent (tests, status)
        self._static: dict = {}                         # (mag, page, sub index) -> packets after the header
        self._new = None                                # a service waiting to replace this one
        self.subtitle = None                            # rows of page 888 now, or None (not in use)
        self._sub_changed = False
        self._sub_sent = -1e9
        self._apply(service)

    # ------------------------------------------------------------- the service
    def update(self, service) -> None:
        """Replace the service while on air (ohsatv/tti.FolderWatch): taken
        at the next field. Nothing is cut short - a page already going out
        finishes as it was - and each changed page goes out new the next
        time its magazine comes round to it, so within one cycle."""
        self._new = service

    def _apply(self, service) -> None:
        self.service = service
        by_mag: dict[int, list] = {}
        for pg in service.pages.values():
            if (pg.mag, pg.page) == (SUB_MAG, SUB_PAGE) and self.subtitle is not None:
                continue                                  # live subtitles own 888
            if any(s.transmit for s in pg.subpages):
                by_mag.setdefault(pg.mag, []).append(pg)
        old = {m.mag: m for m in self.mags}
        mags = []
        for m in sorted(set(by_mag) | {m for m, mg in old.items() if mg.queue or mg.priority}
                        | ({SUB_MAG} if self.subtitle is not None else set())):
            mg = old.get(m) or _Mag(m, [])
            nxt = mg.pages[mg.idx].page if mg.pages and mg.idx < len(mg.pages) else -1
            mg.pages = sorted(by_mag.get(m, []), key=lambda p: p.page)
            # Carry on from where the magazine was in its sequence.
            mg.idx = next((i for i, p in enumerate(mg.pages) if p.page >= nxt), 0) if mg.pages else 0
            mags.append(mg)
        self.mags = mags
        keys = {(pg.mag, pg.page) for pg in service.pages.values()}
        for k in [k for k in self.sub_state if k not in keys]:
            del self.sub_state[k]
        for k, (i, since, cyc) in list(self.sub_state.items()):
            if i >= len(service.pages[k].subpages):
                self.sub_state[k] = (0, since, cyc)
        self._static = {}

    # ------------------------------------------------------------- subtitles
    def set_subtitle(self, rows) -> None:
        """Page 888's rows (25, None for blank) for what is said now, or
        an empty list for nothing on screen; None stops subtitles. A change
        goes out ahead of the magazine's next page, with C4 (erase) so the
        old words go, and C6 (subtitle: shown boxed over the picture) -
        the status OpenHamShack Teletext gives page 888, nothing more. The same subtitle is
        repeated every SUB_REPEAT_S without C4, for a receiver that has just
        tuned in, and without the flicker an erase each time would cause."""
        if rows is None:
            if self.subtitle is not None:
                self.subtitle = None
                self._apply(self.service)
            return
        rows = list(rows)
        first = self.subtitle is None
        if rows != self.subtitle:
            self.subtitle = rows
            self._sub_changed = True
        if first:
            self._apply(self.service)

    def _sub_packets(self, erase: bool) -> list:
        ctl = ttx.C6 | (ttx.C4 if erase else 0)
        hdr = ttx.header(SUB_MAG, SUB_PAGE, 0, ctl, self.header_text(SUB_MAG, SUB_PAGE))
        rows = self.subtitle or []
        return [hdr] + [ttx.row(SUB_MAG, y, rows[y]) for y in range(1, min(len(rows), 25))
                        if rows[y] is not None]

    def _sub_due(self, t: float) -> None:
        if self.subtitle is None:
            return
        mg = next((m for m in self.mags if m.mag == SUB_MAG), None)
        if mg is None:
            return
        if self._sub_changed:
            mg.priority = self._sub_packets(erase=True)
            self._sub_changed = False
            self._sub_sent = t
        elif t - self._sub_sent >= SUB_REPEAT_S and not mg.priority:
            mg.priority = self._sub_packets(erase=False)
            self._sub_sent = t

    # ------------------------------------------------------------- the clock
    def header_text(self, mag: int, page: int) -> bytes:
        t = time.localtime(self.now())
        s = f"{self.name:<8} {ttx.page_label(mag, page)} {time.strftime('%a %d %b', t)} "
        s = s[:24].ljust(24) + time.strftime("%H:%M/%S", t)
        return s.encode("ascii", "replace")

    # ------------------------------------------------------------- choosing subpages
    def _subpage(self, mg: _Mag, pg, t: float) -> int:
        subs = [i for i, s in enumerate(pg.subpages) if s.transmit]
        key = (pg.mag, pg.page)
        st = self.sub_state.get(key)
        if st is None:
            self.sub_state[key] = (subs[0], t, mg.cycles)
            return subs[0]
        i, since, cyc = st
        if len(subs) == 1:
            return subs[0]
        s = pg.subpages[i]
        due = (t - since >= s.cycle - 1e-9) if s.cycle_mode == "T" else (mg.cycles - cyc >= s.cycle)
        if due:
            nxt = subs[(subs.index(i) + 1) % len(subs)] if i in subs else subs[0]
            self.sub_state[key] = (nxt, t, mg.cycles)
            return nxt
        return i

    def _fill(self, mg: _Mag, t: float) -> bool:
        """Queue the magazine's next packets: subtitles waiting, else its
        next page. False if it has nothing to send."""
        if mg.priority:
            mg.queue, mg.priority = mg.priority, []
            return True
        if not mg.pages:
            return False
        pg = mg.pages[mg.idx]
        mg.idx += 1
        if mg.idx >= len(mg.pages):
            mg.idx = 0
            mg.cycles += 1
        i = self._subpage(mg, pg, t)
        s = pg.subpages[i]
        ctl = s.ctl & ~ttx.SERIAL
        hdr = ttx.header(pg.mag, pg.page, s.sub, ctl, self.header_text(pg.mag, pg.page))
        key = (pg.mag, pg.page, i)
        body = self._static.get(key)
        if body is None:
            # Rows 1-24 always, a blank one as spaces (X/25 only if the page
            # has one): a row left out would leave a receiver showing what
            # an earlier version of the page had there. vbit2 does the same.
            body = [ttx.row(pg.mag, y, s.rows[y] or b"") for y in range(1, 25)]
            if s.rows[25] is not None:
                body.append(ttx.row(pg.mag, 25, s.rows[25]))
            if any(ln is not None for ln in s.links):
                crc = ttx.page_crc(hdr[10:], s.rows)
                body.append(ttx.links_packet(pg.mag, s.links, crc))
            self._static[key] = body
        mg.queue = [hdr] + list(body)
        self.sent[(pg.mag, pg.page)] = self.sent.get((pg.mag, pg.page), 0) + 1
        return True

    # ------------------------------------------------------------- one field
    def field(self, field_no: int, slots: int) -> list:
        """The packets for one field's `slots` lines (None: leave the line
        empty). `field_no` counts fields from the start of the
        transmission; its time is field_no x 20 ms."""
        t = field_no * FIELD_S
        if self._new is not None:
            svc, self._new = self._new, None
            self._apply(svc)
        self._sub_due(t)
        if self.service.packets and not self.service.pages and self.subtitle is None:
            out = []
            pk = self.service.packets
            for _ in range(slots):
                out.append(pk[self.t42_pos % len(pk)])
                self.t42_pos += 1
            return out
        out: list = [None] * slots
        if not self.mags:
            return out
        first = 0
        if field_no % 50 == 0 and slots:
            # Once a second, first line of the field: the broadcast service
            # data packet (8/30) - the initial page, date and time (Annex
            # B.5: "immediately following the boundary between clock-
            # seconds"; here the boundary of transmission seconds, with the
            # wall clock's time in it).
            ini = None if self.initial is None else (self.initial[0], self.initial[1], ttx.NO_SUB)
            now = self.now()
            dst = time.localtime(now).tm_isdst > 0
            out[0] = ttx.service_packet(ini, self.status, now, -(time.altzone if dst else time.timezone))
            first = 1
        n = len(self.mags)
        for slot in range(first, slots):
            for _ in range(n):
                mg = self.mags[self.turn % n]
                self.turn += 1
                if mg.wait_field == field_no:
                    continue
                if not mg.queue and not self._fill(mg, t):
                    continue
                pkt = mg.queue.pop(0)
                out[slot] = pkt
                if pkt[1] == ttx.HAM84[0] and ttx.unham(pkt[0]) >> 3 == 0:
                    mg.wait_field = field_no        # a header: rows from the next field on
                break
        return out
