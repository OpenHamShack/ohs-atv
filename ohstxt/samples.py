"""Sample pages, built here in code: a news index, an engineering test page
that exercises every Level 1 attribute, a three-subpage weather carousel and
a newsflash. They are the program's first-run service, the screenshots'
subject and the tests' known cases. All of it is invented - the "OHS-ATV"
service is ours; no broadcaster's page, name or logo is copied. It reads
"OHS-ATV"/"OHS ATV", not "OpenHamShack", on rows 2 and 22: a row is too
narrow for the full name, and tests/fixtures/vbit2-sample.t42 holds what
vbit2 really transmitted from this exact text, byte for byte, with
test_our_crc_is_vbit2s and test_vbit2_sent_our_rows (test_txt_t42.py)
checking this module's own output against it directly - so unlike every
other sample row, a reworded row 2 or 22 needs a new capture before the
wording can change, which is why they keep the fixtures' seven-character
word rather than header.py's shorter "OHS".

Rows are written as lists of parts: bytes are text in the English set (so
b"#" is £ and b"_" is #), ints are codes. `row()` pads to 40 and refuses to
overrun, so a sample that grows past its row fails loudly here rather than
being cut off on screen.
"""
from __future__ import annotations

from .header import default_row0
from .page import NO_LINK, PS_NEWSFLASH, PS_TRANSMIT, Page, Subpage

# control codes by name
AK, AR, AG, AY, AB, AM, AC, AW = range(0x00, 0x08)
FLASH, STEADY, END_BOX, START_BOX, NORMAL, DOUBLE = 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D
GK, GR, GG, GY, GB, GM, GC, GW = range(0x10, 0x18)
CONCEAL, CONTIG, SEPAR, ESC, BLACKBG, NEWBG, HOLD, RELEASE = range(0x18, 0x20)
BLOCK = 0x7F


def row(*parts) -> bytearray:
    out = bytearray()
    for p in parts:
        if isinstance(p, int):
            out.append(p)
        elif isinstance(p, str):
            out.extend(p.encode("ascii"))
        else:
            out.extend(p)
    if len(out) > 40:
        raise ValueError(f"sample row is {len(out)} cells: {bytes(out)!r}")
    return out + bytearray(b" " * (40 - len(out)))


def sixels(*art: str) -> bytes:
    """Sixel art, three strings per character row ('#' lit), as mosaic codes."""
    if len(art) != 3 or len({len(a) for a in art}) != 1 or len(art[0]) % 2:
        raise ValueError("sixel art is three rows of equal, even length")
    out = bytearray()
    for x in range(0, len(art[0]), 2):
        code = 0x20
        for sy in range(3):
            for sx in range(2):
                if art[sy][x + sx] == "#":
                    code |= (0x01, 0x02, 0x04, 0x08, 0x10, 0x40)[sy * 2 + sx]
        out.append(code)
    return bytes(out)


def fastext(labels=((AR, "Headlines"), (AG, "Sport"), (AY, "Weather"), (AC, "Index"))) -> bytearray:
    """Row 24: four coloured labels, one per quarter (10 cells each)."""
    out = bytearray()
    for code, text in labels:
        cell = bytearray([code]) + text.encode("ascii")
        out += cell[:10].ljust(10, b" ")
    return row(out)


def _sub(rows: dict[int, bytearray], links=None, **kw) -> Subpage:
    s = Subpage(**kw)
    s.rows[0] = default_row0()
    for r, data in rows.items():
        s.rows[r] = bytearray(data)
    if links:
        s.links = list(links) + [NO_LINK] * (6 - len(links))
        s.has_links = True
    return s


def news_index() -> Page:
    """P100: the service index, in the house style of a 1990s magazine
    front page - a logo in block graphics, a double-height title, two
    columns of page numbers, Fastext labels."""
    # A television under an aerial, in block graphics: the aerial in row 1,
    # the set in row 2 at double height (so it is twice as tall as its art),
    # with the title beside it in the same double-height row.
    aerial = sixels("..#.........#.....",
                    "...#.......#......",
                    "....##...##.......")
    tv = sixels("##..............##",
                "##..............##",
                "##################")
    rows = {
        1: row(GB, NEWBG, GY, aerial),
        2: row(GB, NEWBG, GY, DOUBLE, tv, AW, b" OHS-ATV ", AY, b"NEWS"),
        4: row(GB, NEWBG, AC, b"           Amateur TV teletext"),
        5: row(GB, b"\x2c" * 39),
        6: row(AW, b"Club rally draws record crowd  ", AC, b"101"),
        7: row(AW, b"Repeater back on air after storm", AC, b"102"),
        8: row(AW, b"New 23cm beacon heard in Europe", AC, b"103"),
        9: row(AW, b"Contest results: who came first", AC, b"104"),
        11: row(AY, b"SPORT", b"            ", AY, b"WEATHER"),
        12: row(AW, b"Football    ", AC, b"201", b"  ", AW, b"Today        ", AC, b"301"),
        13: row(AW, b"Cricket     ", AC, b"210", b"  ", AW, b"Outlook      ", AC, b"302"),
        14: row(AW, b"Motor sport ", AC, b"220", b"  ", AW, b"Propagation  ", AC, b"303"),
        16: row(AY, b"SERVICE", b"          ", AY, b"STATION"),
        17: row(AW, b"Test page   ", AC, b"199", b"  ", AW, b"Schedules    ", AC, b"400"),
        18: row(AW, b"Newsflash   ", AC, b"150", b"  ", AW, b"Callsigns    ", AC, b"410"),
        19: row(AW, b"Index       ", AC, b"100", b"  ", AW, b"Contact      ", AC, b"420"),
        21: row(GR, b"\x70" * 39),
        22: row(AR, NEWBG, AY, b"Pages from ", AW, b"OHS ATV Teletext", AY, b"  P100"),
        23: row(GR, b"\x23" * 39),
        24: fastext(),
    }
    sub = _sub(rows, links=[0x101, 0x200, 0x300, 0x100, NO_LINK, 0x100])
    return Page(number=0x100, description="News index", subpages=[sub])


def test_page() -> Page:
    """P199: one row per attribute, each labelled, so a decoder (or this
    one) can be checked against it by eye - and the tests check the same
    rows cell by cell."""
    bars = row(*[x for c in (GW, GY, GC, GG, GM, GR, GB) for x in (c, BLOCK, BLOCK, BLOCK, BLOCK)], GK, b"    ")
    rows = {
        1: row(AY, DOUBLE, b"ENGINEERING TEST PAGE", AW, b"199"),
        3: bars,
        4: bars,
        5: row(b"white", AR, b"red", AG, b"green", AY, b"yellow", AB, b"blue", AM, b"magenta", AC, b"cyan"),
        6: row(AW, b"Contiguous", GG, b"\x7f\x6a\x35\x2c\x23\x73\x7f", AW, b"Separated", SEPAR, GG,
               b"\x7f\x6a\x35\x2c\x23\x73\x7f"),
        7: row(AW, b"Flash", FLASH, AY, b"FLASHING", STEADY, AW, b"steady", AG, b"Reveal", CONCEAL,
               b"SECRET"),
        8: row(AW, b"Bg:", AB, NEWBG, AY, b"yellow on blue", BLACKBG, AW, b"black again"),
        9: row(AW, b"Double:", DOUBLE, AC, b"HEIGHT", NORMAL, AW, b"normal, same row"),
        10: row(b"(this row's own text is hidden)"),
        11: row(AW, b"No hold:", GR, BLOCK, GY, BLOCK, GG, BLOCK, GC, BLOCK, AW, b"gaps between"),
        12: row(AW, b"Hold:   ", GR, HOLD, BLOCK, GY, BLOCK, GG, BLOCK, GC, BLOCK, RELEASE, AW, b"no gaps"),
        13: row(AW, b"Box:", START_BOX, START_BOX, b"in a box", END_BOX, END_BOX, b"outside the box"),
        14: row(AW, b"Blast-through:", GM, b"\x2cABC\x2c", AW, b"caps in gfx"),
        16: row(AC, b"G0:", AW, bytes(range(0x20, 0x40))),
        17: row(AC, b"   ", AW, bytes(range(0x40, 0x60))),
        18: row(AC, b"   ", AW, bytes(range(0x60, 0x80))),
        20: row(AC, b"Con", GW, bytes(range(0x20, 0x40))),
        21: row(AC, b"Con", GW, bytes(range(0x60, 0x80))),
        22: row(AC, b"Sep", SEPAR, GW, bytes(range(0x20, 0x40))),
        23: row(AC, b"Sep", SEPAR, GW, bytes(range(0x60, 0x80))),
        24: fastext(((AR, "Index"), (AG, "News"), (AY, "Flash"), (AC, "Index"))),
    }
    sub = _sub(rows, links=[0x100, 0x101, 0x150, 0x100, NO_LINK, 0x100])
    return Page(number=0x199, description="Engineering test page", subpages=[sub])


# A sun in block graphics: twelve sixels across, nine down - three rows of
# characters. (Not at double height: a double-height row would take the
# row below it, and the sun needs all three.)
SUN = (sixels(".....##.....", "..#..##..#..", "....####...."),
       sixels("#..######..#", "...######...", "#..######..#"),
       sixels("....####....", "..#..##..#..", ".....##....."))


def weather_carousel() -> Page:
    """P300: three subpages that rotate every 10, 10 and 15 seconds."""
    subs = []
    regions = [("NORTH", AC, b"Cloudy, rain later", b"9C", b"wind W 20mph"),
               ("MIDLANDS", AY, b"Sunny spells", b"13C", b"wind SW 10mph"),
               ("SOUTH", AG, b"Dry and bright", b"15C", b"wind S 5mph")]
    for i, (name, col, sky, temp, wind) in enumerate(regions, 1):
        rows = {
            1: row(GB, NEWBG, AW, DOUBLE, b"WEATHER  ", AY, name.encode()),
            3: row(GB, NEWBG),
            5: row(col, b"Today: ", AW, sky),
            7: row(b"  ", GY, SUN[0], AW, b"  Temperature ", AY, temp),
            8: row(b"  ", GY, SUN[1], AW, b"  ", wind),
            9: row(b"  ", GY, SUN[2]),
            12: row(AW, b"Outlook: settled, mild, light winds."),
            14: row(AC, b"Propagation: tropo lift likely on 23cm"),
            15: row(AC, b"along the east coast after dark."),
            22: row(AW, b"Region ", AY, f"{i}/3".encode(), AW, b" - turns by itself"),
            24: fastext(((AR, "Index"), (AG, "Today"), (AY, "Outlook"), (AC, "Index"))),
        }
        subs.append(_sub(rows, links=[0x100, 0x300, 0x302, 0x100, NO_LINK, 0x100], cycle=10 if i < 3 else 15))
    pg = Page(number=0x300, description="Weather carousel", subpages=subs)
    pg.renumber()
    return pg


def newsflash() -> Page:
    """P150: a newsflash - only the boxed text shows, over the picture."""
    rows = {
        19: row(AW, START_BOX, START_BOX, AY, DOUBLE, b"NEWSFLASH", END_BOX, END_BOX),
        21: row(AW, START_BOX, START_BOX, b"Club net tonight 2000 on 1255MHz", END_BOX, END_BOX),
        22: row(AW, START_BOX, START_BOX, b"all stations welcome", END_BOX, END_BOX),
    }
    sub = _sub(rows, status=PS_TRANSMIT | PS_NEWSFLASH)
    return Page(number=0x150, description="Newsflash", subpages=[sub])


def service() -> list[Page]:
    return [news_index(), newsflash(), test_page(), weather_carousel()]
