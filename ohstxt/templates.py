"""Page templates: File > New page from template.

Each template builds a whole page for a station - its callsign, name,
locator and QTH from the service's station.json - in the house style of
the sample pages: a coloured title band, double-height headings, page
numbers in cyan, Fastext labels in the keys' colours. All of the content
is invented placeholder text for the operator to replace (frequencies are
typical amateur ones, not anyone's real schedule).

Three are live pages (DESIGN.md "Live pages"): propagation from hamqsl's
solar data, DX cluster spots and the beacon board. They come with their
tokens in place, so saving one and starting the runner is all it takes.

A row is built with ohstxt.samples.row, which refuses to overrun 40 cells,
and every station value is cut to the room the layout gives it, so a long
callsign or QTH shortens a line rather than breaking a page.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .header import DEFAULT_TEMPLATE
from .live.cluster import ClusterSource
from .live.sources import BeaconSource
from .page import NO_LINK, Page, Subpage
from .samples import (AB, AC, AG, AM, AR, AW, AY, BLACKBG, CONTIG, DOUBLE, GB, GC, GG, GM, GR, GW, GY, NEWBG,
                      SEPAR, fastext, row, sixels)
from .station import Station


def _cut(text: str, n: int) -> bytes:
    return text.encode("ascii", "replace")[:n]


def header_row(st: Station) -> bytearray:
    """Row 0 with the callsign where the default has OPENHAMSHACK, so the
    station identifies itself on every page that carries this header."""
    if not st.callsign:
        return bytearray(b" " * 8 + DEFAULT_TEMPLATE)
    left = (_cut(st.callsign, 9) + b" %%# %%a %e %%b").ljust(23, b" ")[:23]
    return bytearray(b" " * 8 + left + b"\x03%H:%M/%S")


def _page(st: Station, number: int, desc: str, rows: dict[int, bytes], links=None, **kw) -> Page:
    s = Subpage(**kw)
    s.rows[0] = header_row(st)
    for r, data in rows.items():
        s.rows[r] = bytearray(data)
    if links:
        s.links = list(links) + [NO_LINK] * (6 - len(links))
        s.has_links = True
    return Page(number=number, description=desc, subpages=[s])


def _title(colour: int, text: str, st: Station, right: str = "") -> bytes:
    """A double-height title band: colour background, white title, the
    callsign (or `right`) in yellow at the right."""
    tag = _cut(right or st.callsign, 10)
    room = 40 - 5 - len(tag) - 1
    return row(colour, NEWBG, DOUBLE, AW, _cut(text, room).ljust(room), AY, tag)


def _call(st: Station) -> str:
    return st.callsign or "N0CALL"            # the conventional placeholder, nobody's call


# ---------------------------------------------------------------- static pages
def station_index(st: Station, number: int = 0x100) -> Page:
    aerial = sixels("....#......#....", ".....#....#.....", "......####......")
    rows = {
        1: row(GB, NEWBG, GY, aerial, AW, _cut(st.name or "Amateur television", 20)),
        2: _title(4, f"{_call(st)}", st, right=st.grid or "TV"),
        4: row(GB, NEWBG, AC, b"         ", _cut(st.qth or "Amateur TV teletext", 28)),
        5: row(AY, b"WELCOME TO THE STATION PAGES"),
        6: row(AW, b"Pictures and pages from ", AY, _cut(_call(st), 10)),
        8: row(AC, b"THE STATION", b"            ", AC, b"ON THE AIR"),
        9: row(AW, b"Contact, QSL", AC, b"200", b"  ", AW, b"Schedule    ", AC, b"300"),
        10: row(AW, b"Equipment   ", AC, b"400", b"  ", AW, b"Propagation ", AC, b"500"),
        11: row(AW, b"Test card   ", AC, b"199", b"  ", AW, b"DX spots    ", AC, b"510"),
        12: row(b" " * 19, AW, b"Beacons     ", AC, b"520"),
        14: row(GG, b"\x2c" * 39),
        16: row(AG, b"Reports welcome: see P200 for how.  "),
        17: row(AW, b"Talkback on ", AY, b"144.750", AW, b"MHz FM while"),
        18: row(AW, b"the station is on the air."),
        21: row(GB, b"\x70" * 39),
        22: row(AB, NEWBG, AY, b"Station ", AW, _cut(_call(st), 10), AY, b" index", AW, b"  P100"),
        24: fastext(((AR, "Contact"), (AG, "Schedule"), (AY, "Equipment"), (AC, "Propagatn"))),
    }
    return _page(st, number, "Station index", rows, links=[0x200, 0x300, 0x400, 0x500, NO_LINK, 0x100])


def contact(st: Station, number: int = 0x200) -> Page:
    call = _call(st)
    rows = {
        1: _title(1, "CONTACT AND QSL", st),
        4: row(AY, b"Callsign  ", AW, _cut(call, 12)),
        5: row(AY, b"Operator  ", AW, _cut(st.name or "(your name)", 28)),
        6: row(AY, b"QTH       ", AW, _cut(st.qth or "(your town)", 28)),
        7: row(AY, b"Locator   ", AW, _cut(st.grid or "(locator)", 10)),
        9: row(AC, b"REPORTS"),
        10: row(AW, b"Picture and teletext reports are very"),
        11: row(AW, b"welcome. Tell us what you saw, on what"),
        12: row(AW, b"receiver and aerial, and how strong."),
        14: row(AC, b"QSL"),
        15: row(AW, b"Cards via the bureau, or direct with a"),
        16: row(AW, b"stamped addressed envelope. Electronic"),
        17: row(AW, b"QSLs are answered too."),
        19: row(AC, b"TALKBACK"),
        20: row(AW, b"144.750 MHz FM, or the ATV repeater's"),
        21: row(AW, b"talkback channel when it is on air."),
        24: fastext(((AR, "Index"), (AG, "Schedule"), (AY, "Equipment"), (AC, "Index"))),
    }
    return _page(st, number, "Contact and QSL", rows, links=[0x100, 0x300, 0x400, 0x100, NO_LINK, 0x100])


def schedule(st: Station, number: int = 0x300) -> Page:
    head = row(AC, b"DAY  ", AC, b"UTC        ", AC, b"MHz      ", AC, b"MODE")
    lines = [("Mon", "1900-2100", "1255.000", "FM-TV"), ("Tue", "2000-2100", "437.000", "DVB-S2"),
             ("Wed", "1900-2100", "1255.000", "FM-TV"), ("Thu", "2000-2200", "2395.000", "DVB-S"),
             ("Sat", "1000-1200", "1255.000", "FM-TV"), ("Sun", "1400-1600", "10491.500", "DVB-S2")]
    rows = {1: _title(2, "TRANSMISSION SCHEDULE", st), 4: head, 5: row(AW, b"\x60" * 39)}
    for i, (day, utc, mhz, mode) in enumerate(lines):
        rows[6 + i] = row(AY, day.encode().ljust(5), AW, utc.encode().ljust(11), AG, mhz.encode().ljust(9),
                          AW, mode.encode())
    rows.update({
        13: row(AW, b"\x60" * 39),
        15: row(AW, b"Times are UTC. Tests and nets may be"),
        16: row(AW, b"added at short notice: this page is"),
        17: row(AW, b"kept up to date with each change."),
        19: row(AY, b"Club net", AW, b" every Wed 2000 on talkback"),
        24: fastext(((AR, "Index"), (AG, "Contact"), (AY, "Equipment"), (AC, "Index"))),
    })
    return _page(st, number, "Transmission schedule", rows, links=[0x100, 0x200, 0x400, 0x100, NO_LINK, 0x100])


def technical(st: Station, number: int = 0x400) -> Page:
    rows = {
        1: _title(5, "TECHNICAL DETAILS", st),
        4: row(AC, b"TRANSMITTER"),
        5: row(AW, b"SDR exciter, ", AY, b"10 W", AW, b" PA on 23cm,"),
        6: row(AW, b"2 W on 70cm, 1 W on 13cm"),
        8: row(AC, b"AERIALS"),
        9: row(AW, b"23cm  ", AY, b"35-element Yagi", AW, b" at 12 m"),
        10: row(AW, b"70cm  ", AY, b"19-element Yagi", AW, b" at 10 m"),
        11: row(AW, b"3cm   ", AY, b"80 cm dish", AW, b" (QO-100)"),
        13: row(AC, b"FREQUENCIES AND MODES"),
        14: row(AW, b"1255.000 MHz  ", AG, b"FM-TV, PAL, 6.0 MHz"),
        15: row(AW, b" 437.000 MHz  ", AG, b"DVB-S2 333 kS/s QPSK"),
        16: row(AW, b"2395.000 MHz  ", AG, b"DVB-S 2 MS/s QPSK"),
        18: row(AC, b"TELETEXT"),
        19: row(AW, b"WST Level 1 in the VBI, from"),
        20: row(AW, b"OpenHamShack ATV; pages made with"),
        21: row(AW, b"OpenHamShack Teletext."),
        24: fastext(((AR, "Index"), (AG, "Contact"), (AY, "Schedule"), (AC, "Test card"))),
    }
    return _page(st, number, "Technical details", rows, links=[0x100, 0x200, 0x300, 0x199, NO_LINK, 0x100])


def test_card(st: Station, number: int = 0x199) -> Page:
    """A test card inside a mosaic border: colour bars, contiguous and
    separated mosaics, the station in a box, the character set and every
    colour. (The station box is normal height: a double-height row cannot
    carry the border down through its lower half, because the border
    cell's graphics code and the double-height code would both have to
    sit in the one cell before it.)"""
    def inner(*parts) -> bytes:
        mid = row(*parts)[:36]
        return row(GW, 0x35, mid.rstrip(b" ").ljust(36, b" "), GW, 0x6A)

    bars = inner(*[x for c in (GW, GY, GC, GG, GM, GR, GB) for x in (c, 0x7F, 0x7F, 0x7F, 0x7F)])
    call = _cut(_call(st), 22).center(24)
    where = _cut((st.grid + "  " + st.qth).strip() or "TEST CARD", 24).center(24)
    mos = b"\x7f\x6a\x35\x2c\x23\x73\x7f"
    rows = {
        1: row(GW, 0x37, b"\x23" * 37, 0x6B),
        2: inner(),
        3: bars, 4: bars, 5: bars,
        6: inner(),
        7: inner(b"   ", AB, NEWBG, b" " * 25, BLACKBG),       # 25: as wide as the rows with a colour code
        8: inner(b"   ", AB, NEWBG, AY, call, BLACKBG),
        9: inner(b"   ", AB, NEWBG, AW, where, BLACKBG),
        10: inner(b"   ", AB, NEWBG, b" " * 25, BLACKBG),
        11: inner(),
        12: inner(GR, mos, GG, mos, GB, mos, GC, mos),
        13: inner(SEPAR, GR, mos, GG, mos, GB, mos, GC, mos[:6], CONTIG),   # the border stays contiguous
        14: inner(),
        15: inner(AW, b"ABCDEFGHIJKLMNOPQRSTUVWXYZ"),
        16: inner(AY, b"abcdefghijklmnopqrstuvwxyz"),
        17: inner(AC, b"0123456789 #$%&@_?! \x5c\x7b\x7d\x7e\x5b\x5d\x5e"),
        18: inner(AR, b"RED", AG, b"GREEN", AY, b"YELLOW", AB, b"BLUE", AM, b"MAGENTA", AC, b"CYAN"),
        19: inner(),
        20: inner(AW, b"Flash: ", 0x08, AY, b"FLASHING", 0x09, AW, b" steady"),
        21: inner(),
        22: row(GW, 0x75, b"\x70" * 37, 0x7A),
        24: fastext(((AR, "Index"), (AG, "Equipment"), (AY, "Schedule"), (AC, "Index"))),
    }
    return _page(st, number, "Test card", rows, links=[0x100, 0x400, 0x300, 0x100, NO_LINK, 0x100])


# ---------------------------------------------------------------- live pages
def _live(page: Page) -> Page:
    from .live import Sidecar
    page.live = Sidecar()
    return page


def propagation(st: Station, number: int = 0x500) -> Page:
    """Solar and band conditions from hamqsl.com, filled by the runner."""
    rows = {
        1: _title(4, "PROPAGATION", st, right="LIVE"),
        3: row(AC, b"Solar flux ", AW, b"{>sfi}", AC, b"  Sunspots ", AW, b"{>ssn}"),
        4: row(AC, b"A index    ", AW, b"{>a~~}", AC, b"  K index  ", AW, b"{>k~~}"),
        5: row(AC, b"X-ray      ", AW, b"{xray}", AC, b"  Noise    ", AW, b"{noise~}"),
        6: row(AC, b"Geomagnetic field ", AY, b"{geomag~~~~~~~~}"),
        8: row(GB, NEWBG, AY, b"HF CONDITIONS    ", AW, b"DAY     ", AW, b"NIGHT  "),
        9: row(AW, b"  80m-40m          ", AG, b"{d80~~}", AG, b" {n80~~} "),
        10: row(AW, b"  30m-20m          ", AG, b"{d30~~}", AG, b" {n30~~} "),
        11: row(AW, b"  17m-15m          ", AG, b"{d17~~}", AG, b" {n17~~} "),
        12: row(AW, b"  12m-10m          ", AG, b"{d12~~}", AG, b" {n12~~} "),
        14: row(GB, NEWBG, AY, b"VHF                                 "),
        15: row(AW, b"  Aurora     ", AG, b"{aurora_vhf~~~~~~~~~~~}"),
        16: row(AW, b"  Es Europe  ", AG, b"{es_eu~~~~~~~~~~~~~~~~}"),
        17: row(AW, b"  Es 6m      ", AG, b"{es_6m~~~~~~~~~~~~~~~~}"),
        18: row(AW, b"  Es 4m      ", AG, b"{es_4m~~~~~~~~~~~~~~~~}"),
        20: row(AC, b"Updated ", AW, b"{updated~~~~~~~~~~~~~~~~}"),
        21: row(AM, b"Data: hamqsl.com (N0NBH)"),
        22: row(AY, b"{status~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~}"),
        24: fastext(((AR, "Index"), (AG, "DX spots"), (AY, "Beacons"), (AC, "Index"))),
    }
    return _live(_page(st, number, "Propagation (live)", rows, links=[0x100, 0x510, 0x520, 0x100, NO_LINK, 0x100]))


def dx_spots(st: Station, number: int = 0x510) -> Page:
    rows = {
        1: _title(1, "DX CLUSTER", st, right="LIVE"),
        3: row(AC, ClusterSource.LINE.format("kHz", "DX", "UTC", "SPOTTER").encode()),
    }
    for i in range(10):
        tok = "{dx.%d" % (i + 1)
        rows[5 + i] = row(AW if i % 2 == 0 else AC, (tok + "~" * (38 - len(tok) - 1) + "}").encode())
    rows.update({
        16: row(AY, b"Bands: ", AW, b"{dx.bands~~~~~~~~~~~~~~~~~~}"),
        18: row(AW, b"Spots from the DX cluster, newest at"),
        19: row(AW, b"the top, one line per call and band."),
        22: row(AY, b"{status~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~}"),
        24: fastext(((AR, "Index"), (AG, "Propagatn"), (AY, "Beacons"), (AC, "Index"))),
    })
    return _live(_page(st, number, "DX cluster spots (live)", rows,
                       links=[0x100, 0x500, 0x520, 0x100, NO_LINK, 0x100]))


def beacons(st: Station, number: int = 0x520) -> Page:
    rows = {
        1: _title(6, "BEACONS AND REPEATERS", st, right="LIVE"),
        3: row(AC, BeaconSource.LINE.format("CALL", "MHz", "MODE", "STAT", "NOTE").encode()),
    }
    for i in range(10):
        tok = "{beacons.%d" % (i + 1)
        rows[5 + i] = row(AW, (tok + "~" * (39 - len(tok) - 1) + "}").encode())
    rows.update({
        16: row(AG, b"On the air  ", AW, b"{beacons.on}"),
        17: row(AR, b"Off the air ", AW, b"{beacons.off}"),
        19: row(AW, b"The board is kept in beacons.csv in"),
        20: row(AW, b"the service folder."),
        22: row(AY, b"{status~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~}"),
        24: fastext(((AR, "Index"), (AG, "Propagatn"), (AY, "DX spots"), (AC, "Index"))),
    })
    return _live(_page(st, number, "Beacons and repeaters (live)", rows,
                       links=[0x100, 0x500, 0x510, 0x100, NO_LINK, 0x100]))


@dataclass(frozen=True)
class Template:
    key: str
    title: str
    about: str
    number: int
    build: Callable[[Station, int], Page]
    live: bool = False


TEMPLATES = [
    Template("index", "Station index", "Front page: the station, its pages, talkback", 0x100, station_index),
    Template("contact", "Contact and QSL", "Callsign, name, QTH, locator; reports and QSL", 0x200, contact),
    Template("schedule", "Transmission schedule", "Days, times, frequencies and modes", 0x300, schedule),
    Template("technical", "Technical details", "Equipment, aerials, frequencies and modes", 0x400, technical),
    Template("testcard", "Test card", "Colour bars, mosaics, the station in a box", 0x199, test_card),
    Template("propagation", "Propagation (live)", "Solar flux, A and K, HF and VHF conditions", 0x500,
             propagation, live=True),
    Template("dxspots", "DX cluster spots (live)", "The last ten spots, filtered by band", 0x510, dx_spots,
             live=True),
    Template("beacons", "Beacons and repeaters (live)", "A status board from beacons.csv", 0x520, beacons,
             live=True),
]


def get(key: str) -> Template:
    return next(t for t in TEMPLATES if t.key == key)
