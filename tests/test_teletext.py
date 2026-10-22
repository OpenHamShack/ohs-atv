"""Teletext: the codes, packets and page store (ohsatv/ttx.py), the .tti and
.t42 readers (ohsatv/tti.py), the carousel (ohsatv/carousel.py), the line
waveform and slicer (ohsatv/vbi.py) and the whole chain through the
simulator's channel. Every test runs the code; none reads it."""
from __future__ import annotations

import os
import random
import tempfile
import unittest

import numpy as np

from helpers import channel, tmp_dir  # noqa: F401  (channel: sys.path)
from ohsatv import carousel, narrow, pal, testcard, ttx, tti, vbi


def popcount(x: int) -> int:
    return bin(x).count("1")


class Hamming(unittest.TestCase):
    def test_table_matches_the_clause_8_2_parity_tests(self):
        # Every codeword passes the four odd-parity tests A-D of EN 300 706
        # clause 8.2 (bits in transmission order P1 D1 P2 D2 P3 D3 P4 D4).
        for d in range(16):
            b = ttx.ham(d)
            bit = [(b >> i) & 1 for i in range(8)]        # bit[0] = P1
            p1, d1, p2, d2, p3, d3, p4, d4 = bit
            self.assertEqual((p1 ^ d1 ^ d3 ^ d4) & 1, 1)                     # A
            self.assertEqual((d1 ^ p2 ^ d2 ^ d4) & 1, 1)                     # B
            self.assertEqual((d1 ^ d2 ^ p3 ^ d3) & 1, 1)                     # C
            self.assertEqual(sum(bit) % 2, 1)                                # D
            self.assertEqual(d1 | d2 << 1 | d3 << 2 | d4 << 3, d)

    def test_known_codewords(self):
        # The widely published table: 0 -> 15, F -> EA.
        self.assertEqual(ttx.ham(0), 0x15)
        self.assertEqual(ttx.ham(0xF), 0xEA)
        self.assertEqual(len(set(ttx.HAM84)), 16)

    def test_single_errors_corrected_double_detected(self):
        for d in range(16):
            b = ttx.ham(d)
            self.assertEqual(ttx.unham(b), d)
            for i in range(8):
                self.assertEqual(ttx.unham(b ^ (1 << i)), d)
            for i in range(8):
                for j in range(i + 1, 8):
                    self.assertEqual(ttx.unham(b ^ (1 << i) ^ (1 << j)), -1)

    def test_odd_parity(self):
        for c in range(128):
            b = ttx.par(c)
            self.assertEqual(popcount(b) % 2, 1)
            self.assertEqual(b & 0x7F, c)
            self.assertTrue(ttx.PARITY_OK[b])
            for i in range(8):
                self.assertFalse(ttx.PARITY_OK[b ^ (1 << i)])


class Packets(unittest.TestCase):
    def test_header_round_trip(self):
        store = ttx.PageStore()
        for mag, page, sub, ctl in ((1, 0x00, 0x0000, 0), (8, 0x99, 0x3F7F, ttx.C4 | ttx.C8 | ttx.C12),
                                    (3, 0x2A, 0x1234 & 0x3F7F, ttx.C5 | ttx.C6 | ttx.C7 | ttx.C9 | ttx.C10 | ttx.C14)):
            pkt = ttx.header(mag, page, sub, ctl, b"HELLO HEADER")
            self.assertEqual(len(pkt), 42)
            self.assertEqual(ttx.parse_address(pkt), (mag, 0))
            store.add(pkt)
            got = store.get(mag, page, sub)
            self.assertIsNotNone(got)
            self.assertEqual(got.ctl, ctl)
            self.assertEqual(got.header, b"HELLO HEADER".ljust(32))

    def test_rows_and_links_round_trip(self):
        store = ttx.PageStore()
        mag = 2
        store.add(ttx.header(mag, 0x01, 0, 0, b""))
        text = bytes(range(32, 72))
        store.add(ttx.row(mag, 5, text))
        store.add(ttx.row(mag, 24, b"\x01Red\x02Green"))
        links = [(1, 0x00, ttx.NO_SUB), (8, 0x88, 0x0001), None, (3, 0x33, ttx.NO_SUB), None, (1, 0x99, 0x0000)]
        store.add(ttx.links_packet(mag, links, crc=0x1234))
        pg = store.get(mag, 0x01)
        self.assertEqual(pg.rows[5], text)
        self.assertEqual(pg.rows[24], b"\x01Red\x02Green".ljust(40))
        self.assertEqual(pg.links, links)

    def test_address_every_magazine_and_row(self):
        for mag in range(1, 9):
            for y in range(32):
                self.assertEqual(ttx.parse_address(ttx.address(mag, y)), (mag, y))

    def test_parity_error_keeps_the_old_character(self):
        store = ttx.PageStore()
        store.add(ttx.header(1, 0x10, 0, 0, b""))
        store.add(ttx.row(1, 3, b"ABCDEFGH"))
        store.add(ttx.header(1, 0x10, 0, 0, b""))
        bad = bytearray(ttx.row(1, 3, b"XBCDEFGH"))
        bad[2] ^= 0x01                         # 'X' with a bit flipped: parity fails
        store.add(bytes(bad))
        self.assertEqual(store.get(1, 0x10).rows[3][:8], b"ABCDEFGH")
        self.assertEqual(store.stats["parityErrors"], 1)

    def test_erase_bit_clears_rows(self):
        store = ttx.PageStore()
        store.add(ttx.header(1, 0x10, 0, 0, b""))
        store.add(ttx.row(1, 3, b"OLD"))
        store.add(ttx.header(1, 0x10, 0, ttx.C4, b""))
        self.assertIsNone(store.get(1, 0x10).rows[3])

    def test_uncorrectable_header_closes_the_magazine(self):
        store = ttx.PageStore()
        store.add(ttx.header(1, 0x10, 0, 0, b""))
        bad = bytearray(ttx.header(1, 0x20, 0, 0, b""))
        bad[2] ^= 0x03                          # two bits wrong in the page units
        store.add(bytes(bad))
        store.add(ttx.row(1, 4, b"STRAY"))      # must not land on page 110
        self.assertIsNone(store.get(1, 0x10).rows[4])
        self.assertIsNone(store.get(1, 0x20))

    def test_magazines_interleave(self):
        store = ttx.PageStore()
        store.add(ttx.header(1, 0x00, 0, 0, b""))
        store.add(ttx.header(2, 0x00, 0, 0, b""))
        store.add(ttx.row(1, 1, b"ONE"))
        store.add(ttx.row(2, 1, b"TWO"))
        self.assertEqual(store.get(1, 0).rows[1][:3], b"ONE")
        self.assertEqual(store.get(2, 0).rows[1][:3], b"TWO")

    def test_subpages_are_kept_apart(self):
        store = ttx.PageStore()
        for sub, word in ((1, b"FIRST"), (2, b"SECOND")):
            store.add(ttx.header(1, 0x50, sub, 0, b""))
            store.add(ttx.row(1, 2, word))
        self.assertEqual(store.subcodes(1, 0x50), [1, 2])
        self.assertEqual(store.get(1, 0x50, 1).rows[2][:5], b"FIRST")
        self.assertEqual(store.get(1, 0x50, 2).rows[2][:6], b"SECOND")

    def test_crc_depends_on_the_page(self):
        rows = [None] * 26
        rows[1] = b"A" * 40
        a = ttx.page_crc(b"HEADER", rows)
        rows[1] = b"B" + b"A" * 39
        self.assertNotEqual(a, ttx.page_crc(b"HEADER", rows))
        self.assertEqual(ttx.page_crc(b"", [None] * 26), ttx.page_crc(b" " * 24, [None] + [b" " * 40] * 25))


SAMPLE = (b"DE,A test page\r\n"
          b"PS,8000\r\n"
          b"CT,4,T\r\n"
          b"PN,10001\r\n"
          b"SC,0001\r\n"
          b"OL,1,\x81RED\x1bBESC GREEN, with a comma\r\n"
          b"OL,2,\x8dDouble\r\n"
          b"OL,24,\x81Index\x82Next\r\n"
          b"FL,100,101,1FF,0,8FF,1A0\r\n"
          b"PN,10002\r\n"
          b"SC,0002\r\n"
          b"CT,6,T\r\n"
          b"OL,1,Second subpage\r\n"
          b"OL,26,@@@@\r\n")


class Tti(unittest.TestCase):
    def test_parse_sample(self):
        pg, notes = tti.parse_tti(SAMPLE, "t.tti")
        self.assertEqual((pg.mag, pg.page), (1, 0x00))
        self.assertEqual(pg.description, "A test page")
        self.assertEqual(len(pg.subpages), 2)
        a, b = pg.subpages
        # 8-bit translation (0x81) and the 7-bit ESC form (ESC B = 0x02).
        self.assertEqual(a.rows[1][:22], b"\x01RED\x02ESC GREEN, with a")
        self.assertEqual(len(a.rows[1]), 40)
        self.assertEqual(a.rows[2][0], 0x0D)
        self.assertEqual((a.cycle, a.cycle_mode), (4.0, "T"))
        self.assertEqual((b.cycle, b.cycle_mode), (6.0, "T"))
        self.assertEqual([s.sub for s in pg.subpages], [0x0001, 0x0002])
        self.assertEqual(a.links, [(1, 0x00, ttx.NO_SUB), (1, 0x01, ttx.NO_SUB), None, None, None,
                                   (1, 0xA0, ttx.NO_SUB)])
        self.assertTrue(a.transmit and b.transmit)
        self.assertEqual(b.rows[1][:14], b"Second subpage")
        self.assertTrue(any("26" in n for n in notes))

    def test_utf8_file(self):
        data = "PN,20000\r\nOL,1,\u0081Red £\r\n".encode("utf-8")
        pg, _ = tti.parse_tti(data)
        self.assertEqual(pg.subpages[0].rows[1][:6], b"\x01Red #")      # U+00A3 -> 0x23, '£' in English

    def test_every_form_a_control_code_arrives_in(self):
        """One row - every control code but 0x00 (vbit2's reader ends a row
        at a raw byte below 0x20, so only ESC can carry it), the '£' cell,
        DEL - in each form a .tti reaches us: the editor's ESC + 0x40, MRG's
        0x80 + n, that file re-saved as UTF-8 (C2 80+n), re-saved as UTF-8
        by a Windows editor that took it as code page 1252 first, and a
        UTF-8 file whose only non-ASCII character is '£'. Each must read
        as the same 40 codes, and the ESC form must also come back from
        our writer unchanged."""
        row = bytes(range(1, 0x20)) + b" #Mix 5\x7f"
        row = row.ljust(40)

        def esc(r):
            return b"".join(bytes((0x1B, c + 0x40)) if c < 0x20 else bytes((c,)) for c in r)

        def high(r):
            return bytes(c + 0x80 if c < 0x20 else c for c in r)

        def windows(b):
            # As Windows reads "ANSI": 1252, its five undefined bytes as
            # the code points of the same number.
            return "".join(bytes((c,)).decode("cp1252", errors="ignore") or chr(c) for c in b)

        forms = {
            "ESC + 0x40": esc(row),
            "0x80 + n": high(row),
            "0x80 + n, '£' as Latin-1 A3": high(row).replace(b"#", b"\xa3"),
            "0x80 + n re-saved as UTF-8": high(row).replace(b"#", b"\xa3").decode("latin-1").encode("utf-8"),
            "via code page 1252 to UTF-8": windows(high(row)).encode("utf-8"),
            "ESC form, '£' as UTF-8": esc(row).replace(b"#", "£".encode("utf-8")),
            "ESC form with a BOM": b"\xef\xbb\xbf" + esc(row),
        }
        for name, text in forms.items():
            bom = text.startswith(b"\xef\xbb\xbf")
            data = (b"\xef\xbb\xbf" if bom else b"") + b"DE,x\r\nPN,10000\r\nOL,1," + text[3 * bom:] + b"\r\n"
            pg, _ = tti.parse_tti(data, name)
            self.assertEqual(pg.subpages[0].rows[1], row, name)
        self.assertEqual(tti.encode_row(row), esc(row))
        back, _ = tti.parse_tti(tti.dumps(pg))
        self.assertEqual(back.subpages[0].rows[1], row)

    def test_round_trip_in_the_editors_dialect_keeps_every_code(self):
        # All 128 codes over four rows, written as OpenHamShack Teletext writes
        # (7-bit, ESC + 0x40, CRLF) and read back: nothing gained or lost.
        s = ttx.Subpage()
        codes = bytes(range(128))
        for y in range(1, 5):
            s.rows[y] = codes[(y - 1) * 32:y * 32].ljust(40, b"\x7f")
        data = tti.dumps(ttx.Page(1, 0x23, [s]))
        self.assertTrue(data.isascii())
        back, _ = tti.parse_tti(data)
        self.assertEqual(back.subpages[0].rows[1:5], s.rows[1:5])

    def test_page_status(self):
        pg, _ = tti.parse_tti(b"PN,30000\r\nPS,4048\r\nOL,1,x\r\n")
        s = pg.subpages[0]
        self.assertFalse(s.transmit)
        self.assertEqual(s.ctl, ttx.C4 | ttx.C8)       # C11 (serial, 0x0040) deliberately dropped
        pg, _ = tti.parse_tti(b"PN,30000\r\nOL,1,x\r\n")
        self.assertTrue(pg.subpages[0].transmit)      # no PS: sent (module docstring)

    def test_single_page_keeps_its_subcode(self):
        pg, _ = tti.parse_tti(b"PN,10500\r\nSC,3F7F\r\nOL,1,x\r\n")
        self.assertEqual(pg.subpages[0].sub, 0x3F7F)

    def test_service_folder_and_t42(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "P100.tti"), "wb") as f:
                f.write(SAMPLE)
            with open(os.path.join(d, "P201.tti"), "wb") as f:
                f.write(b"PN,20100\r\nOL,1,two\r\n")
            with open(os.path.join(d, "junk.tti"), "wb") as f:
                f.write(b"nothing useful\r\n")
            svc = tti.load_service(d)
            self.assertEqual(svc.labels(), ["100", "201"])
        with tempfile.TemporaryDirectory() as d:
            pk = [ttx.header(1, 0, 0, 0, b"T42"), ttx.row(1, 1, b"from a t42")]
            with open(os.path.join(d, "s.t42"), "wb") as f:
                f.write(b"".join(pk))
            svc = tti.load_service(d)
            self.assertEqual(svc.packets, pk)
        with self.assertRaises(OSError):
            tti.load_service("/nonexistent/teletext")


FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


class AgainstVbit2(unittest.TestCase):
    """Cross-checks with the outside world, from fixtures copied in (no
    other program is run or imported):

    - fixtures/ohstxt-service/: the sample service as OpenHamShack Teletext (the
      editor) wrote it - ESC-form rows, CT per subpage, an OL,0 header
      template, a three-subpage carousel;
    - fixtures/vbit2-sample.t42: what vbit2 transmitted from those same
      files (OpenHamShack Teletext's tests/fixtures): page 100, and subpage 2 of
      the 300 carousel, each from its header to the next.

    Our reader must read the files as vbit2 did, and our packet builders
    must make vbit2's packets byte for byte - the header, every row, and
    the X/27/0 links with their CRC."""

    @classmethod
    def setUpClass(cls):
        cls.svc = tti.load_service(os.path.join(FIXTURES, "ohstxt-service"))
        with open(os.path.join(FIXTURES, "vbit2-sample.t42"), "rb") as f:
            cls.packets = tti.read_t42(f.read())
        cls.sent = []
        for p in cls.packets:
            mag, y = ttx.parse_address(p)
            if y == 0:
                cls.sent.append({"mag": mag, "hdr": p, "rows": {}, "x27": None})
            elif y == 27:
                cls.sent[-1]["x27"] = p
            else:
                cls.sent[-1]["rows"][y] = p

    def subpage(self, sent):
        page = ttx.unham(sent["hdr"][2]) | ttx.unham(sent["hdr"][3]) << 4
        sub = ttx._parse_page_nibbles(sent["hdr"][2:8])[1]
        pg = self.svc.pages[(sent["mag"], page)]
        return pg, (pg.subpages[sub - 1] if len(pg.subpages) > 1 else pg.subpages[0]), sub

    def test_the_service_reads_as_vbit2_read_it(self):
        self.assertEqual(self.svc.labels(), ["100", "150", "199", "300"])
        p300 = self.svc.pages[(3, 0x00)]
        self.assertEqual([(s.sub, s.cycle, s.cycle_mode) for s in p300.subpages],
                         [(1, 10.0, "T"), (2, 10.0, "T"), (3, 15.0, "T")])
        self.assertFalse(any("rows 0" in n for n in self.svc.notes))

    def test_our_packets_are_vbit2s(self):
        self.assertEqual(len(self.sent), 2)
        for sent in self.sent:
            pg, s, sub = self.subpage(sent)
            mag = sent["mag"]
            # The header: our builder, vbit2's page, sub-code and text.
            text = bytes(b & 0x7F for b in sent["hdr"][10:])
            self.assertEqual(ttx.header(mag, pg.page, sub, s.ctl, text), sent["hdr"], pg.label)
            # Rows 1-24, every one, from our reading of the file - blank
            # ones as spaces, as vbit2 sends them - and what our carousel
            # sends for this subpage, the same packets in the same order.
            ours = {y: ttx.row(mag, y, s.rows[y] or b"") for y in range(1, 25)}
            self.assertEqual(ours, sent["rows"], pg.label)
            car = carousel.Carousel(tti.Service(pages={(mag, pg.page): ttx.Page(mag, pg.page, [s])}),
                                    now=lambda: 0.0)
            got = [p for f in range(1, 4) for p in car.field(f, 16) if p is not None]
            nxt = next(i for i in range(1, len(got)) if ttx.parse_address(got[i])[1] == 0)
            body = got[1:nxt]
            self.assertEqual(body, [sent["rows"][y] for y in range(1, 25)] + [ttx.links_packet(
                mag, s.links, ttx.page_crc(got[0][10:], s.rows))], pg.label)
            # The links and the page CRC over vbit2's own header.
            crc = ttx.page_crc(text, s.rows)
            self.assertEqual(ttx.links_packet(mag, s.links, crc), sent["x27"], pg.label)


class AgainstHacktv(unittest.TestCase):
    """A second, independent inserter: hacktv's teletext.c, run on its own
    demo page and on one of ours (fixtures/hacktv/, README.md there says
    exactly where it came from and how it was made). Ours, reading the
    same .tti files, must make hacktv's packets byte for byte - every
    header's address and control bytes (with hacktv's text), every row it
    sent, the X/27/0 links and the page CRC hacktv computed - and our page
    store, given hacktv's stream, must come out with the pages as the files
    have them.

    hacktv leaves blank rows out (vbit2 and we send them as spaces), sends
    X/27 straight after the header (we send it after the rows, as vbit2
    does) and always sends one (all links 8FF:3F7F when the page has
    none); those are its choices of what to send, not differences in how a
    packet is coded, and the test allows them."""

    DIR = os.path.join(FIXTURES, "hacktv")

    @classmethod
    def setUpClass(cls):
        cls.pages = {}
        for name in ("demo.tti", "P2A0.tti"):
            with open(os.path.join(cls.DIR, name), "rb") as f:
                pg, _ = tti.parse_tti(f.read(), name)
            cls.pages[(pg.mag, pg.page)] = pg
        with open(os.path.join(cls.DIR, "hacktv.t42"), "rb") as f:
            cls.packets = tti.read_t42(f.read())
        # Each transmission: a header and what follows it in its magazine.
        cls.sent, cur = [], {}
        for p in cls.packets:
            mag, y = ttx.parse_address(p)
            if y == 0:
                if mag in cur:
                    cur[mag]["ended"] = True          # the capture may stop part-way through the last
                cur[mag] = {"mag": mag, "hdr": p, "rows": {}, "x27": None, "ended": False}
                cls.sent.append(cur[mag])
            elif y == 27:
                cur[mag]["x27"] = p
            elif 1 <= y <= 25:
                cur[mag]["rows"][y] = p

    def test_the_files_read_as_hacktv_read_them(self):
        self.assertEqual(sorted(self.pages), [(1, 0x00), (2, 0xA0)])
        demo, ours = self.pages[(1, 0x00)].subpages[0], self.pages[(2, 0xA0)].subpages[0]
        self.assertEqual(demo.sub, 0x3F7F)
        self.assertEqual((ours.sub, ours.ctl), (0x0005, ttx.C5 | ttx.C8))
        # ESC + 0x40 and 0x80 + n are the same codes; A3 is '£' (0x23).
        self.assertEqual(ours.rows[1][:4], b"\x03\x0dOH")
        self.assertEqual(ours.rows[3][:2], b"\x06\x1d")
        self.assertEqual(ours.rows[7][:8], b"\x01Price #")
        self.assertEqual(ours.links, [(2, 0xA1, ttx.NO_SUB), (1, 0x00, ttx.NO_SUB), (3, 0xB2, ttx.NO_SUB), None,
                                      (2, 0xA0, ttx.NO_SUB), (7, 0xC4, ttx.NO_SUB)])

    def test_our_packets_are_hacktvs(self):
        checked = {"hdr": 0, "rows": 0, "x27": 0}
        for sent in self.sent:
            mag, hdr = sent["mag"], sent["hdr"]
            page, sub, (c4, _, _) = ttx._parse_page_nibbles(hdr[2:8])
            if page == ttx.NO_PAGE:
                continue                          # hacktv's time filler between transmissions
            pg = self.pages[(mag, page)]
            s = pg.subpages[0]
            text = bytes(b & 0x7F for b in hdr[10:])
            # C4 is hacktv's own: set on a page's first transmission only.
            self.assertEqual(ttx.header(mag, page, sub, s.ctl | (ttx.C4 if c4 else 0), text), hdr, pg.label)
            self.assertEqual(sub, s.sub)
            checked["hdr"] += 1
            for y in range(1, 25):
                if y in sent["rows"]:
                    self.assertEqual(ttx.row(mag, y, s.rows[y]), sent["rows"][y], f"{pg.label} row {y}")
                    checked["rows"] += 1
                elif sent["ended"]:
                    self.assertFalse((s.rows[y] or b"").strip(b" "), f"{pg.label} row {y} is not blank")
            if sent["x27"] is not None:
                self.assertEqual(ttx.links_packet(mag, s.links, ttx.page_crc(text, s.rows)), sent["x27"], pg.label)
                checked["x27"] += 1
        self.assertGreaterEqual(checked["hdr"], 3)
        self.assertGreaterEqual(checked["x27"], 3)
        self.assertGreaterEqual(checked["rows"], 40)

    def test_our_receiver_reads_hacktvs_stream(self):
        store = ttx.PageStore()
        for i, p in enumerate(self.packets):
            store.add(p, now=i * 0.04)
        for (mag, page), pg in self.pages.items():
            s = pg.subpages[0]
            got = store.get(mag, page)
            self.assertIsNotNone(got, pg.label)
            for y in range(1, 25):
                self.assertEqual(got.rows[y] or b" " * 40, s.rows[y] or b" " * 40, f"{pg.label} row {y}")
            self.assertEqual(got.links, s.links + [None] * (6 - len(s.links)) if s.links else [None] * 6)


def service(pages: dict) -> tti.Service:
    svc = tti.Service()
    for label, subs in pages.items():
        mag, page = ttx.parse_label(label)
        sp = []
        for text, cycle in subs:
            s = ttx.Subpage(cycle=cycle)
            s.rows[1] = text.ljust(40).encode()
            sp.append(s)
        if len(sp) > 1:
            for i, s in enumerate(sp):
                s.sub = i + 1
        svc.pages[(mag, page)] = ttx.Page(mag, page, sp)
    return svc


class MisAssembly(unittest.TestCase):
    """No page ever shows a row it was not sent with, under the losses a
    weak or breaking signal gives - and the pages still come in.

    A service of 11 pages and 16 subpages in magazines 1, 2 and 8, two of
    them carousels, every row and link different from every other (random
    text, seeded), goes through our carousel at 16 lines a field into the
    page store, with what the air does to it:

    - 3 % of packets lost (the slicer missed the line) and 5 % damaged -
      one to three bytes each with one or two bits wrong, so a Hamming byte
      is either corrected or known to be bad and a character either fails
      its parity or is wrong. That is reception near the threshold the
      DESIGN.md table measures (PER 1-10 %). Headers suffer like any
      packet: lost, or unreadable in their page, sub-code or address;
    - about one break a ten seconds, 1 to 40 frames long, starting and
      ending part-way through a frame, the store told of each (as the
      engine tells it when the decoder's frame says `gap`).

    After every frame, every row the store holds must be its own page's
    (30 of its 40 characters, where another page's would match ~0.4) and
    every link must be its own page's. Three bytes with three wrong bits
    in one Hamming byte can be corrected to the wrong value; no Level 1
    decoder can tell, and the damage here stays inside what the code
    corrects or detects."""

    FRAMES = 2500
    DROP, DAMAGE, BREAK = 0.03, 0.05, 0.004

    def service(self, rng):
        layout = {(1, 0x00): 1, (1, 0x01): 1, (1, 0x02): 1, (1, 0x10): 1, (1, 0x99): 1,
                  (2, 0x00): 1, (2, 0x50): 3, (2, 0x51): 1,
                  (8, 0x00): 1, (8, 0x88): 1, (8, 0xA0): 5}
        svc, own = tti.Service(), {}
        for (mag, page), n in layout.items():
            subs = []
            for i in range(n):
                s = ttx.Subpage(cycle=1.0 if n > 1 else 8.0)
                s.sub = (i + 1) if n > 1 else 0
                for y in range(1, 25):
                    s.rows[y] = bytes(rng.randrange(0x20, 0x7F) for _ in range(40))
                s.links = [(rng.randint(1, 8), rng.randrange(0xFF), ttx.NO_SUB) for _ in range(6)]
                subs.append(s)
                own[(mag, page, s.sub)] = s
            svc.pages[(mag, page)] = ttx.Page(mag, page, subs)
        return svc, own

    @staticmethod
    def damage(pkt: bytes, rng) -> bytes:
        b = bytearray(pkt)
        for i in rng.sample(range(42), rng.randint(1, 3)):
            for bit in rng.sample(range(8), rng.randint(1, 2)):
                b[i] ^= 1 << bit
        return bytes(b)

    def check(self, store, own, frame):
        for label in store.labels():
            mag, page = ttx.parse_label(label)
            for sub in store.subcodes(mag, page):
                got = store.get(mag, page, sub)
                s = own.get((mag, page, sub))
                self.assertIsNotNone(s, f"frame {frame}: page {label}/{sub} was never sent")
                for y in range(1, 25):
                    r = got.rows[y]
                    if r is None or r == s.rows[y]:
                        continue
                    same = sum(a == b for a, b in zip(r, s.rows[y]))
                    self.assertGreaterEqual(same, 30, f"frame {frame}: page {label}/{sub} row {y} is another "
                                                      f"page's: {r!r}")
                for i, ln in enumerate(got.links):
                    if ln is not None:
                        self.assertEqual(ln, s.links[i], f"frame {frame}: page {label}/{sub} link {i}")

    def run_air(self, seed):
        rng = random.Random(seed)
        svc, own = self.service(rng)
        car = carousel.Carousel(svc, now=lambda: 1759680000.0)
        store = ttx.PageStore()
        broken, stats = 0, {"lost": 0, "damaged": 0, "breaks": 0, "headers lost": 0}
        for k in range(self.FRAMES):
            pk = [p for f in (2 * k, 2 * k + 1) for p in car.field(f, 16) if p is not None]
            if broken:
                broken -= 1
                if broken:
                    continue
                pk = pk[rng.randrange(len(pk)):]
                store.gap()
            elif rng.random() < self.BREAK:
                pk = pk[:rng.randrange(len(pk))]
                broken = rng.randint(1, 40) + 1
                stats["breaks"] += 1
            for p in pk:
                header = ttx.parse_address(p) is not None and ttx.parse_address(p)[1] == 0
                if rng.random() < self.DROP:
                    stats["lost"] += 1
                    stats["headers lost"] += header
                    continue
                if rng.random() < self.DAMAGE:
                    p = self.damage(p, rng)
                    stats["damaged"] += 1
                store.add(p, now=k * 0.04)
            self.check(store, own, k)
        return store, own, stats

    def test_no_row_on_another_page_and_the_pages_still_come_in(self):
        for seed in (1, 2, 3):
            with self.subTest(seed=seed):
                store, own, stats = self.run_air(seed)
                self.assertGreater(stats["headers lost"], 50)
                self.assertGreater(stats["breaks"], 3)
                exact = total = 0
                for (mag, page, sub), s in own.items():
                    got = store.get(mag, page, sub)
                    self.assertIsNotNone(got, ttx.page_label(mag, page))
                    exact += sum(got.rows[y] == s.rows[y] for y in range(1, 25))
                    total += 24
                self.assertGreater(exact / total, 0.9)

    def test_each_rule_on_its_own(self):
        rows = {n: bytes([0x40 + n]) * 40 for n in range(1, 25)}

        def fresh(mag=1, page=0x10):
            st = ttx.PageStore()
            st.add(ttx.header(mag, page, 0, 0, b""))
            st.add(ttx.row(mag, 1, rows[1]))
            st.add(ttx.row(mag, 5, rows[5]))
            return st

        # A header lost, the next page's rows from row 1 again: not on 110.
        st = fresh()
        st.add(ttx.row(1, 2, b"STRAY"))
        self.assertIsNone(st.get(1, 0x10).rows[2])
        # A row repeated is a new transmission too.
        st = fresh()
        st.add(ttx.row(1, 5, b"STRAY"))
        self.assertEqual(st.get(1, 0x10).rows[5], rows[5])
        # A second X/27/0 in one transmission: the first stays.
        st = fresh()
        mine = [(1, 0x20, ttx.NO_SUB)] + [None] * 5
        st.add(ttx.links_packet(1, mine))
        st.add(ttx.links_packet(1, [(2, 0x30, ttx.NO_SUB)] + [None] * 5))
        st.add(ttx.row(1, 9, b"STRAY"))
        self.assertEqual(st.get(1, 0x10).links, mine)
        self.assertIsNone(st.get(1, 0x10).rows[9])
        # A packet whose magazine is unreadable: every magazine closes.
        st = fresh()
        st.add(ttx.header(2, 0x20, 0, 0, b""))
        bad = bytearray(ttx.header(3, 0x30, 0, 0, b""))
        bad[0] ^= 0x03
        st.add(bytes(bad))
        st.add(ttx.row(1, 9, b"STRAY"))
        st.add(ttx.row(2, 9, b"STRAY"))
        self.assertIsNone(st.get(1, 0x10).rows[9])
        self.assertIsNone(st.get(2, 0x20).rows[9])
        # Its magazine readable but not its row, which could be 0: that
        # magazine closes, the others go on.
        st = fresh()
        st.add(ttx.header(2, 0x20, 0, 0, b""))
        bad = bytearray(ttx.header(1, 0x30, 0, 0, b""))
        bad[1] ^= 0x03
        st.add(bytes(bad))
        st.add(ttx.row(1, 9, b"STRAY"))
        st.add(ttx.row(2, 9, rows[9]))
        self.assertIsNone(st.get(1, 0x10).rows[9])
        self.assertEqual(st.get(2, 0x20).rows[9], rows[9])
        # A break: nothing after it joins a page from before it.
        done = []
        st = ttx.PageStore(on_page=lambda *a: done.append(a))
        st.add(ttx.header(1, 0x10, 0, 0, b""))
        st.add(ttx.row(1, 1, rows[1]))
        st.gap()
        st.add(ttx.row(1, 12, b"STRAY"))
        self.assertIsNone(st.get(1, 0x10).rows[12])
        self.assertEqual(done, [(1, 0x10, 0)])
        # And in order, all of it arrives.
        st = fresh()
        for y in (6, 7, 24):
            st.add(ttx.row(1, y, rows[y]))
        st.add(ttx.links_packet(1, mine))
        self.assertEqual([st.get(1, 0x10).rows[y] for y in (1, 5, 6, 7, 24)], [rows[y] for y in (1, 5, 6, 7, 24)])

    def test_the_decoder_never_joins_the_two_sides_of_a_break(self):
        """Through the real decoder: a frame finished after a break carried
        the packets of the field before the break and of the field after it
        as one frame's, with nothing to say so - so a page whose header fell
        in the break got the rows after it. Each break here (samples lost,
        noise, both; at different places in the frame) must give frames
        whose packets all come from one side of it, and the first frame
        after it must say `gap`."""
        fs = 10e6
        enc, dec = narrow.make_encoder(fs, "am", {}), narrow.make_decoder(fs, "am", {})
        ins, card = vbi.Inserter(fs), testcard.colour_bars("GM4BRI")
        sent_at, sig = {}, []
        for k in range(16):
            comp = enc.encode(card, k)
            pk = [ttx.row(1, 1 + i % 24, f"FRAME {k:03d} LINE {i:02d}".encode()) for i in range(32)]
            ins.insert(comp, pk[:16], pk[16:])
            n = comp.size
            # Where each packet is in the signal: the first field's lines
            # start the frame, the second's half-way through it.
            sent_at.update((p, k * n + (i >= 16) * n // 2) for i, p in enumerate(pk))
            sig.append(comp.ravel())
        stream = np.concatenate(sig)
        rng = np.random.default_rng(5)
        for at, noise, lost in ((0.47, 600_000, 0), (0.02, 0, 640_000), (0.31, 2_000, 100_000), (0.23, 0, 1_000)):
            with self.subTest(at=at, noise=noise, lost=lost):
                dec.reset()
                a = int(6 * n + at * n)
                s = np.concatenate([stream[:a], rng.normal(0, 0.05, noise).astype(np.float32),
                                    stream[a + noise + lost:]])
                frames = []
                for i in range(0, len(s), 200_000):
                    frames += dec.feed(s[i:i + 200_000])
                sides = [{sent_at[p] >= a for p in f["ttx"]} for f in frames]
                self.assertTrue(all(len(x) <= 1 for x in sides), "a frame out with packets from both sides")
                first = next(i for i, x in enumerate(sides) if x == {True})
                self.assertTrue(frames[first]["gap"])
                self.assertFalse(any(f["gap"] for f in frames[first + 1:]))
                self.assertTrue(all(x != {True} for x in sides[:first]))


class Scheduler(unittest.TestCase):
    def run_fields(self, car, fields, slots=16):
        out = []
        for f in range(fields):
            out.append(car.field(f, slots))
        return out

    def test_every_page_comes_round(self):
        car = carousel.Carousel(service({"100": [("a", 8)], "101": [("b", 8)], "200": [("c", 8)],
                                         "300": [("d", 8)]}), now=lambda: 0.0)
        store = ttx.PageStore()
        for f in self.run_fields(car, 10):
            for p in f:
                if p is not None:
                    store.add(p)
        self.assertEqual(store.labels(), ["100", "101", "200", "300"])

    def test_no_rows_in_the_field_of_their_header(self):
        car = carousel.Carousel(service({"100": [("a", 8)], "200": [("b", 8)]}), now=lambda: 0.0)
        for f in self.run_fields(car, 20):
            hdr_mags = set()
            for p in f:
                if p is None:
                    continue
                mag, y = ttx.parse_address(p)
                if y == 0:
                    self.assertNotIn(mag, hdr_mags)
                    hdr_mags.add(mag)
                else:
                    self.assertNotIn(mag, hdr_mags, "a row followed its header in the same field")

    def test_magazines_take_turns(self):
        # Magazine 1 has many pages, magazine 2 one: 2's page still comes
        # round about as often as the field allows, not once per pass of 1.
        pages = {f"1{i:02X}": [(f"p{i}", 8)] for i in range(40)}
        pages["200"] = [("two", 8)]
        car = carousel.Carousel(service(pages), now=lambda: 0.0)
        self.run_fields(car, 50)
        most_of_1 = max(n for (m, _), n in car.sent.items() if m == 1)
        self.assertGreater(car.sent[(2, 0x00)], 8 * most_of_1)

    def test_subpages_cycle_on_time(self):
        # Two subpages, 2 s and 3 s: the subpage on the air at each moment.
        car = carousel.Carousel(service({"100": [("first", 2), ("second", 3)]}), now=lambda: 0.0)
        seen = []
        for f in range(int(11 / carousel.FIELD_S)):
            for p in car.field(f, 16):
                if p is not None and ttx.parse_address(p)[1] == 0:
                    sub = ttx._parse_page_nibbles(p[2:8])[1]
                    seen.append((round(f * carousel.FIELD_S, 2), sub))
        changes = [seen[0]] + [b for a, b in zip(seen, seen[1:]) if a[1] != b[1]]
        times = [t for t, _ in changes]
        self.assertEqual([s for _, s in changes][:5], [1, 2, 1, 2, 1])
        np.testing.assert_allclose(times[:5], [0.0, 2.0, 5.0, 7.0, 10.0], atol=0.05)

    def test_header_carries_the_clock(self):
        import time as _t
        t0 = _t.mktime((2026, 10, 5, 12, 34, 56, 0, 0, -1))
        car = carousel.Carousel(service({"100": [("a", 8)]}), name="GM4BRI", now=lambda: t0)
        store = ttx.PageStore()
        for p in car.field(0, 16):
            if p is not None:
                store.add(p)
        self.assertEqual(store.get(1, 0).header, b"GM4BRI   100 Mon 05 Oct 12:34/56")

    def test_service_packet_once_a_second(self):
        car = carousel.Carousel(service({"100": [("a", 8)], "2A0": [("b", 8)]}), name="GM4BRI",
                                now=lambda: 1_791_200_000.0, initial="2A0")
        firsts = []
        for f in range(150):
            pk = car.field(f, 16)
            n830 = [p for p in pk if p is not None and ttx.parse_address(p) == (8, 30)]
            if n830:
                firsts.append(f)
                self.assertIs(pk[0], n830[0])
        self.assertEqual(firsts, [0, 50, 100])
        store = ttx.PageStore()
        store.add(car.field(150, 16)[0])
        self.assertEqual(store.service["initial"], (2, 0xA0, ttx.NO_SUB))
        self.assertEqual(store.service["status"], b"GM4BRI OPENHAMSHACK ")
        # The date and time, each digit plus one: 1 791 200 000 s is
        # 2026-10-05 11:33:20 UTC, MJD 61318 (the clause's reference, 31
        # January 1982, is MJD 45000: 16 318 days earlier).
        import datetime as _d
        self.assertEqual((_d.date(2026, 10, 5) - _d.date(1982, 1, 31)).days + 45000, 61318)
        pkt = ttx.service_packet(None, b"", 1_791_200_000.0)
        self.assertEqual(pkt[12:18], bytes((0x07, 0x24, 0x29, 0x22, 0x44, 0x31)))

    def test_t42_stream_is_sent_as_it_is(self):
        pk = [ttx.row(1, i % 24 + 1, bytes([65 + i % 26]) * 40) for i in range(50)]
        car = carousel.Carousel(tti.Service(packets=pk), now=lambda: 0.0)
        got = [p for f in self.run_fields(car, 4) for p in f]
        self.assertEqual(got, (pk * 2)[:64])


class LiveEdits(unittest.TestCase):
    """The folder changing under a transmission (tti.FolderWatch,
    Carousel.update): changed, added and removed pages go out within a
    cycle, half-written files are waited for, unreadable ones leave the
    page as it was."""

    def setUp(self):
        self.d = str(tmp_dir(self, "ohsatv-watch-"))
        self.write("P100.tti", b"PN,10000\r\nOL,1,first version\r\n")
        self.write("P101.tti", b"PN,10100\r\nOL,1,other page\r\n")

    def write(self, name, data):
        with open(os.path.join(self.d, name), "wb") as f:
            f.write(data)

    def bump(self, name, data):
        # A new modification time even on a coarse clock.
        self.write(name, data)
        st = os.stat(os.path.join(self.d, name))
        os.utime(os.path.join(self.d, name), ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))

    def test_changes_added_removed_and_settling(self):
        w = tti.FolderWatch(self.d, settle=0.4)
        self.assertEqual(w.svc.labels(), ["100", "101"])
        self.assertIsNone(w.poll(0.0))                     # nothing changed, nothing new
        self.bump("P100.tti", b"PN,10000\r\nOL,1,second version\r\n")
        self.assertIsNone(w.poll(1.0))                     # first seen changing: wait
        self.assertIsNone(w.poll(1.2))                     # not yet held still for 0.4 s
        svc = w.poll(1.5)
        self.assertEqual(svc.pages[(1, 0)].subpages[0].rows[1][:14], b"second version")
        # Added; removed.
        self.write("P200.tti", b"PN,20000\r\nOL,1,new\r\n")
        w.poll(2.0)
        self.assertEqual(w.poll(2.5).labels(), ["100", "101", "200"])
        os.remove(os.path.join(self.d, "P101.tti"))
        self.assertIsNone(w.poll(3.0))
        self.assertEqual(w.poll(3.5).labels(), ["100", "200"])
        # A half-written file - no PN yet - keeps the page that was.
        self.bump("P100.tti", b"OL,1,half writ")
        w.poll(4.0)
        self.assertIsNone(w.poll(4.5))
        self.assertTrue(any("stays as it was" in n for n in w.notes))
        self.assertEqual(w.svc.pages[(1, 0)].subpages[0].rows[1][:14], b"second version")
        # An editor's temporary file is never a page; nor are the live
        # runner's sidecars and state.
        self.write(".P300.tti.tmp", b"PN,30000\r\n")
        self.write(".P300.tti", b"PN,30000\r\n")
        for name in ("P100.live.json", "station.json", "beacons.csv", ".ohstxt-live-state.json"):
            self.write(name, b'{"vbi_lines": 16}')
        w.poll(5.0)
        self.assertIsNone(w.poll(5.5))

    def test_carousel_takes_the_new_service_without_a_gap(self):
        svc = tti.load_service(self.d)
        car = carousel.Carousel(svc, now=lambda: 0.0)
        store = ttx.PageStore()
        f = 0

        def run(fields):
            nonlocal f
            for _ in range(fields):
                pk = car.field(f, 16)
                self.assertTrue(any(p is not None for p in pk))       # never an empty field
                for p in pk:
                    if p is not None:
                        store.add(p)
                f += 1
        run(10)
        self.assertEqual(store.get(1, 0).rows[1][:13], b"first version")
        self.bump("P100.tti", b"PN,10000\r\nOL,1,second version\r\n")
        self.write("P150.tti", b"PN,15000\r\nOL,1,added\r\n")
        w = tti.FolderWatch(self.d)
        car.update(w.svc)
        # One cycle of magazine 1 (three pages of 25 packets) is a few fields.
        run(12)
        self.assertEqual(store.get(1, 0).rows[1][:14], b"second version")
        self.assertEqual(store.get(1, 0x50).rows[1][:5], b"added")


SRT = ("﻿1\r\n00:00:00,500 --> 00:00:02,000\r\nFirst <i>line</i>\r\n\r\n"
       "2\n00:00:03.000 --> 00:00:05,250\n<font color=\"#ff0000\">Red</font> then white\n"
       "<font color=yellow>second line</font>\n\n"
       "not a cue at all\n\n"
       "3\n00:00:06,000 --> 00:00:07,000\n{\\an8}Costs £5 #1\n").encode("utf-8")


class Subtitles(unittest.TestCase):
    def test_srt(self):
        from ohsatv import subtitles
        cues = subtitles.parse_srt(SRT)
        self.assertEqual([(c.start, c.end) for c in cues], [(0.5, 2.0), (3.0, 5.25), (6.0, 7.0)])
        self.assertEqual(cues[0].lines, [[(7, "First line")]])
        self.assertEqual(cues[1].lines, [[(1, "Red"), (7, " then white")], [(3, "second line")]])
        self.assertEqual(cues[2].lines, [[(7, "Costs £5 #1")]])
        tr = subtitles.Track(cues)
        self.assertIsNone(tr.at(0.4))
        self.assertIs(tr.at(0.5), cues[0])
        self.assertIsNone(tr.at(2.0))
        self.assertIs(tr.at(5.0), cues[1])
        self.assertEqual(subtitles.colour_code("#00ffff"), 6)
        self.assertEqual(subtitles.colour_code("#e01010"), 1)
        self.assertEqual(subtitles.colour_code("black"), 7)

    def test_page_rows(self):
        from ohsatv import subtitles
        rows = subtitles.page_rows(subtitles.parse_srt(SRT)[1].lines)
        self.assertEqual([y for y in range(26) if rows[y] is not None], [20, 22])
        r = rows[20].rstrip(b" ")
        core = r.lstrip(b" ")
        # OpenHamShack Teletext's layout: double height, box, box, the colour
        # inside the box, the text, end box twice.
        self.assertEqual(core[:4], bytes((0x0D, 0x0B, 0x0B, 1)))
        self.assertEqual(core[4:7], b"Red")
        self.assertEqual(core[7], 7)                                      # white: a cell inside the box
        self.assertTrue(core.endswith(b" then white\x0a\x0a"))
        # The text (15 cells, the colour change included) centred in the row,
        # as the editor places it: column (40 - 15) // 2.
        self.assertEqual(rows[20].index(b"Red"), (40 - 15) // 2)
        one = subtitles.page_rows([[(7, "x")]])
        self.assertEqual([y for y in range(26) if one[y] is not None], [22])
        # £ and # as the English national set has them.
        self.assertIn(b"Costs #5 _1", subtitles.page_rows(subtitles.parse_srt(SRT)[2].lines)[22])
        # 37 cells: one start box, no end box; past 37, cut; two lines at most.
        r37 = subtitles.row_bytes([(7, "x" * 37)])
        self.assertEqual(r37, bytes((0x0D, 0x0B, 7)) + b"x" * 37)
        self.assertEqual(subtitles.row_bytes([(7, "y" * 50)]), bytes((0x0D, 0x0B, 7)) + b"y" * 37)
        three = subtitles.page_rows([[(7, "a")], [(7, "b")], [(7, "c")]])
        self.assertEqual([y for y in range(26) if three[y] is not None], [20, 22])
        # A typed caption wraps at words instead.
        long = subtitles.page_rows([[(7, "word " * 12)]], wrap=True)
        self.assertEqual([y for y in range(26) if long[y] is not None], [20, 22])
        self.assertNotIn(b"wor\x0a", long[20])

    def test_the_editors_srt_subset(self):
        # Exactly as OpenHamShack Teletext's DESIGN.md "Subtitles: the SRT subset"
        # writes it: LF, one or two lines, a line white or wholly in one of
        # the six colours, three entities, the English set as Unicode.
        from ohsatv import subtitles
        srt = ("1\n00:00:01,000 --> 00:00:03,200\n<font color=\"#ffff00\">Is the camera running?</font>\n"
               "Plain lines are white.\n\n2\n00:00:03,600 --> 00:00:06,000\nFish &amp; chips &lt;loud&gt;\n\n"
               "3\n00:00:07,000 --> 00:00:08,000\n<font color=\"#00ffff\">\u2190MUSIC\u2192 \u00bd \u00a3 # "
               "\u2015\u00bc\u2016\u00be\u00f7\u25a0</font>\n\n").encode("utf-8")
        cues = subtitles.parse_srt(srt)
        self.assertEqual(cues[0].lines, [[(3, "Is the camera running?")], [(7, "Plain lines are white.")]])
        self.assertEqual(cues[1].lines, [[(7, "Fish & chips <loud>")]])
        for colour, code in (("#ff0000", 1), ("#00ff00", 2), ("#ffff00", 3), ("#0000ff", 4), ("#ff00ff", 5),
                             ("#00ffff", 6)):
            self.assertEqual(subtitles.colour_code(colour), code)
        row = subtitles.page_rows(cues[2].lines)[22]
        self.assertIn(bytes((6,)) + bytes([0x5B]) + b"MUSIC" + bytes([0x5D, 0x20, 0x5C, 0x20, 0x23, 0x20, 0x5F, 0x20,
                                                                     0x60, 0x7B, 0x7C, 0x7D, 0x7E, 0x7F]), row)
        self.assertEqual(subtitles.parse_srt(srt.replace(b"\n", b"\r\n"))[1].lines, cues[1].lines)

    def test_boxed_overlay(self):
        from ohsatv import subtitles, ttxrender
        rows = subtitles.page_rows([[(3, "Hello")]])
        rgba = ttxrender.render(rows, boxed_only=True)
        self.assertEqual(rgba.shape, (500, 480, 4))
        lit_rows = np.flatnonzero(rgba[..., 3].any(axis=1))
        self.assertEqual((lit_rows.min(), lit_rows.max()), (22 * 20, 24 * 20 - 1))   # the double row only
        self.assertTrue((rgba[22 * 20:24 * 20][..., :3] == (255, 255, 0)).all(axis=-1).any())   # yellow text

    def test_carousel_sends_subtitles_ahead_with_erase_and_repeats(self):
        from ohsatv import subtitles
        car = carousel.Carousel(service({"100": [("a", 8)]}), now=lambda: 0.0)
        store = ttx.PageStore()
        seen = []

        def run(f0, f1):
            for f in range(f0, f1):
                for p in car.field(f, 16):
                    if p is None:
                        continue
                    store.add(p)
                    mag, y = ttx.parse_address(p)
                    if (mag, y) == (8, 0):
                        n = [ttx.unham(b) for b in p[2:10]]
                        seen.append((f, n[0] | n[1] << 4, bool(n[3] & 8)))
        run(0, 5)
        car.set_subtitle(subtitles.page_rows([[(7, "Hello")]]))
        run(5, 7)
        self.assertEqual(seen[0], (5, 0x88, True))                      # at once, with C4
        pg = store.get(8, 0x88)
        self.assertEqual(pg.ctl & (ttx.C5 | ttx.C6 | ttx.C7), ttx.C6)          # as the editor sets 888
        self.assertIn(b"Hello", pg.rows[22])
        run(7, 5 + int(carousel.SUB_REPEAT_S / carousel.FIELD_S) + 2)
        self.assertEqual(seen[1][2], False)                             # the repeat: no erase
        car.set_subtitle([None] * 26)                                   # nothing on screen
        f = seen[-1][0] + 200
        run(f, f + 3)
        self.assertTrue(seen[-1][2])
        self.assertIsNone(store.get(8, 0x88).rows[22])                  # erased


class Waveform(unittest.TestCase):
    def test_levels_timing_and_spectrum(self):
        fs = 20e6
        ins = vbi.Inserter(fs, lines=(7,))
        pkt = ttx.row(1, 1, bytes(range(32, 72)))
        w = ins.waveforms([pkt])[0]
        bits = vbi.packet_bits(pkt)
        tb = fs / vbi.BIT_RATE
        c = vbi.FIRST_BIT_US * 1e-6 * fs + np.arange(360) * tb
        # Read at the bit centres from an 8x band-limited upsampling of the line.
        up = np.fft.irfft(np.fft.rfft(w), 8 * len(w)) * 8
        v = np.interp(8 * c, np.arange(len(up)), up)
        # Ones at 66 % of white, zeros at black: the raised cosine has no
        # intersymbol interference at the centres (what is left is the
        # pulse's truncation at +-10 bits).
        self.assertLess(np.max(np.abs(v[bits == 1] - 0.462)), 0.01)
        self.assertLess(np.max(np.abs(v[bits == 0])), 0.01)
        # The penultimate run-in '1' (bit 12) centred at 12.0 us after 0H.
        self.assertAlmostEqual(vbi.FIRST_BIT_US + 12 * vbi.BIT_US, 12.0, places=9)
        self.assertAlmostEqual(vbi.BIT_RATE, 6.9375e6)
        # Nothing before 9 us (the burst and porch are left alone) or in
        # the last microsecond of the line.
        us = fs / 1e6
        self.assertLess(np.max(np.abs(w[:int(9 * us)])), 1e-3)
        self.assertLess(np.max(np.abs(w[int(63 * us):])), 1e-3)
        # "Substantially zero by 5 MHz": a long random line's spectrum.
        rng = np.random.default_rng(3)
        lines = ins.waveforms([bytes(rng.integers(0, 256, 42).astype(np.uint8)) for _ in range(64)])
        spec = (np.abs(np.fft.rfft(lines, axis=1)) ** 2).mean(axis=0)
        f = np.fft.rfftfreq(lines.shape[1], 1 / fs)
        inband = spec[(f > 0.5e6) & (f < 3e6)].mean()
        self.assertLess(10 * np.log10(spec[f > 5.2e6].mean() / inband), -40)

    def test_parse_lines(self):
        self.assertEqual(vbi.parse_lines("7-22,320-335"), vbi.DEFAULT_LINES)
        self.assertEqual(vbi.parse_lines("10"), (10,))
        for bad in ("5", "23", "318", "a-b", ""):
            with self.assertRaises(ValueError):
                vbi.parse_lines(bad)

    def test_slice_round_trip_at_every_rate(self):
        rng = np.random.default_rng(1)
        for fs in (10e6, 12.5e6, 16e6, 20e6):
            ins = vbi.Inserter(fs)
            sl = vbi.Slicer(fs, ins.lines)
            pk = [ttx.row(1 + i % 8, 1 + i % 24, bytes(rng.integers(32, 127, 40).astype(np.uint8)))
                  for i in range(32)]
            fr = np.zeros((pal.LINES, ins.n), np.float32)
            ins.insert(fr, pk[:16], pk[16:])
            rows = fr[[ln - 1 for ln in ins.lines]]
            # A receiver's gain and offset, and half a sample of timing.
            rows = 0.8 * np.roll(rows, 1, axis=1) + 0.05
            got, has = sl.slice(rows)
            self.assertEqual(got, pk, fs)
            self.assertTrue(all(has))

    def test_other_transmitters_timing(self):
        # EN 300 706 6.3 allows the data from 0.4 us late to 1.0 us early.
        rng = np.random.default_rng(4)
        for fs in (10e6, 20e6):
            sl = vbi.Slicer(fs, vbi.DEFAULT_LINES)
            for ref in (11.0, 11.3, 11.6, 12.0, 12.2, 12.4):
                ins = vbi.Inserter(fs, ref_us=ref)
                pk = [ttx.row(1, 1 + i % 24, bytes(rng.integers(32, 127, 40).astype(np.uint8))) for i in range(32)]
                fr = np.zeros((pal.LINES, ins.n), np.float32)
                ins.insert(fr, pk[:16], pk[16:])
                got, _ = sl.slice(fr[[ln - 1 for ln in ins.lines]])
                self.assertEqual(got, pk, (fs, ref))

    def test_noise_alone_gives_nothing(self):
        rng = np.random.default_rng(5)
        sl = vbi.Slicer(10e6)
        n = 0
        for _ in range(100):
            rows = (0.05 * rng.standard_normal((len(sl.lines), sl.n))).astype(np.float32)
            n += len(sl.slice(rows)[0])
        self.assertLessEqual(n, 1)


def loopback(system, fs, p, cnr, frames=8, ttx_on=True, seed=2):
    """Encoder + teletext -> modulator -> noise and a 19 kHz offset ->
    demodulator -> decoder. Returns (packets sent, packets received, last
    stats)."""
    from helpers import channel as chan
    enc = narrow.make_encoder(fs, system, p)
    mod = narrow.make_mod(fs, system, p)
    dem = narrow.make_demod(fs, system, p)
    dec = narrow.make_decoder(fs, system, p)
    pl = narrow.plan(fs, system, p)
    enc.colour = enc.colour and pl["colour"]
    ins = vbi.Inserter(fs)
    rng = np.random.default_rng(seed)
    card = testcard.colour_bars("GM4BRI")
    sent, got, st, pos = [], [], None, 0
    for k in range(frames):
        comp = enc.encode(card, k)
        pk = [ttx.row(1 + i % 8, 1 + i % 24, bytes(rng.integers(32, 127, 40).astype(np.uint8))) for i in range(32)]
        if ttx_on:
            ins.insert(comp, pk[:16], pk[16:])
            sent += pk
        iq = chan(mod(comp), cnr, 19e3, fs, pos, rng)
        pos += len(iq)
        if system == "am":
            dem.track(iq)
        for f in dec.feed(dem(iq)):
            got += f["ttx"]
            st = f["stats"]
    return sent, got, st


class Loopback(unittest.TestCase):
    def test_am_and_fm_carry_it_and_keep_colour(self):
        for system, fs, p, cnr in (("am", 10e6, {}, 30.0), ("fm", 20e6, {}, 20.0)):
            sent, got, st = loopback(system, fs, p, cnr)
            ok = set(sent)
            self.assertGreater(len(got), 0.7 * len(sent), system)   # the first frame is spent locking
            self.assertTrue(all(g in ok for g in got), system)
            self.assertTrue(st["colour"], system)

    def test_a_picture_without_teletext_gives_no_packets(self):
        for system, fs, cnr in (("am", 10e6, 30.0), ("fm", 20e6, 12.0)):
            _, got, st = loopback(system, fs, {}, cnr, frames=40, ttx_on=False)
            self.assertTrue(st["vLock"])
            self.assertEqual(got, [], system)

    def test_snr_unchanged_by_teletext(self):
        for system, fs, cnr in (("am", 10e6, 20.0), ("am", 10e6, 32.0), ("fm", 20e6, 15.0), ("fm", 20e6, 25.0)):
            _, _, a = loopback(system, fs, {}, cnr, frames=5, ttx_on=False)
            _, _, b = loopback(system, fs, {}, cnr, frames=5, ttx_on=True)
            self.assertLess(abs(a["snr"] - b["snr"]), 1.0, (system, cnr, a["snr"], b["snr"]))


class Level1(unittest.TestCase):
    """The attribute decoder and the character set (ohsatv/ttxrender.py,
    ttxfont.py)."""

    def cells(self, row: bytes, y: int = 1, more=None):
        from ohsatv import ttxrender
        rows = [b""] * 25
        rows[y] = row
        if more:
            for k, v in more.items():
                rows[k] = v
        return ttxrender.layout(rows)

    def test_colours_are_set_after_and_new_background_set_at(self):
        r = self.cells(b"\x01AB\x1dC\x1cD")[1]
        self.assertEqual((r[0]["kind"], r[0]["fg"]), ("space", 7))      # the code's own cell: still white
        self.assertEqual((r[1]["g"], r[1]["fg"], r[1]["bg"]), (ord("A"), 1, 0))
        self.assertEqual(r[3]["bg"], 1)                                   # new background: at its own cell
        self.assertEqual((r[4]["g"], r[4]["bg"]), (ord("C"), 1))
        self.assertEqual(r[5]["bg"], 0)                                   # black background, set-at

    def test_mosaics_blast_through_and_separation(self):
        r = self.cells(b"\x12\x7fA\x1a\x7f\x19\x23")[1]
        self.assertEqual(r[1]["kind"], "mosaic")
        self.assertEqual(r[2]["kind"], "alpha")                           # 40-5F stay capitals
        self.assertTrue(r[4]["sep"])
        self.assertFalse(r[6]["sep"])

    def test_hold_mosaics(self):
        r = self.cells(b"\x11\x1e\x35\x02\x03X")[1]
        self.assertEqual((r[3]["kind"], r[3]["g"]), ("mosaic", 0x35))   # control cell shows the held one
        self.assertEqual(r[4]["kind"], "space")                           # alphanumerics now: not held
        r = self.cells(b"\x11\x35\x02")[1]
        self.assertEqual(r[2]["kind"], "space")                           # no hold, no held character

    def test_double_height_takes_the_row_below(self):
        rows = self.cells(b"\x04\x1d\x07\x0dBIG", more={2: b"never shown"})
        top, bottom = rows[1], rows[2]
        self.assertEqual((top[4]["dh"], bottom[4]["dh"], bottom[4]["g"]), ("top", "bottom", ord("B")))
        self.assertEqual(bottom[0]["kind"], "space")
        self.assertEqual(bottom[2]["bg"], 4)                              # the row above's background
        self.assertFalse(any(c["kind"] == "alpha" and c["g"] == ord("n") for c in bottom))

    def test_flash_and_conceal_in_pixels(self):
        from ohsatv import ttxrender
        rows = [b""] * 25
        rows[3] = b"\x08FLASH\x09\x18HIDDEN"
        on = ttxrender.render(rows, reveal=False, flash_on=True)
        off = ttxrender.render(rows, reveal=False, flash_on=False)
        shown = ttxrender.render(rows, reveal=True, flash_on=True)
        y0, y1 = 3 * 20, 4 * 20
        flash_px = (slice(y0, y1), slice(12, 6 * 12))
        hidden_px = (slice(y0, y1), slice(8 * 12, 14 * 12))
        self.assertGreater(on[flash_px].max(), 0)
        self.assertEqual(off[flash_px].max(), 0)
        self.assertEqual(on[hidden_px].max(), 0)
        self.assertGreater(shown[hidden_px].max(), 0)

    def test_character_set(self):
        from ohsatv import ttxfont
        glyphs = {c: ttxfont.alpha(c).tobytes() for c in range(0x21, 0x80)}
        self.assertEqual(len(set(glyphs.values())), len(glyphs))           # every character its own
        self.assertFalse(ttxfont.alpha(0x20).any())
        self.assertTrue(ttxfont.alpha(0x7F).all())
        # Descenders below the capitals' baseline; capitals do not reach it.
        self.assertTrue(ttxfont.alpha(ord("g"))[16:].any())
        self.assertFalse(ttxfont.alpha(ord("E"))[16:].any())
        # Character rounding: '/' has the half-dots between its steps.
        s = ttxfont.alpha(ord("/"))
        dots = np.kron(s.reshape(10, 2, 6, 2).all(axis=(1, 3)), np.ones((2, 2), bool))
        self.assertTrue((s >= dots).all())
        self.assertEqual(int(s.sum() - dots.sum()), 2 * 4)       # 4 diagonal steps, 2 half-dots each
        # Mosaics: all six sixels of 7F fill the cell; separated ones leave gaps.
        self.assertTrue(ttxfont.mosaic(0x7F).all())
        self.assertLess(ttxfont.mosaic(0x7F, True).sum(), ttxfont.mosaic(0x7F).sum())
        self.assertEqual(ttxfont.mosaic(0x21)[:6, :6].all(), True)         # bit 0: top left
        self.assertFalse(ttxfont.mosaic(0x21)[6:].any())


class Lines405(unittest.TestCase):
    """Teletext and subtitles are 625 lines only: refused with 405 lines
    chosen and on the cable feed's Band I (which carries 405 only); and the receive
    slicer never runs on a 405-line picture, chosen or found on the air."""

    def radio(self, **over):
        import pathlib
        from ohsatv.audio import NullAudio
        from ohsatv.radio import Radio
        tmp = tmp_dir(self, "ohsatv-405-")
        svc = str(tmp_dir(self, "ohsatv-ttx-"))
        with open(os.path.join(svc, "p100.tti"), "wb") as f:
            f.write(PAGE_100)
        cfg = dict(device="sim", audioBackend="null", system="am", band="23cm", rxFreq=1249.0e6, txFreq=1249.0e6,
                   amRate=5_000_000, videoFormat="405", callsign="GM4BRI", licenceAck=True, ttxOn=True, ttxDir=svc)
        cfg.update(over)
        r = Radio(cfg, conf_path=tmp / "c.json", audio=NullAudio(realtime=False), data_dir=tmp / "data")
        self.addCleanup(r.stop)
        return r

    def test_the_plan_refuses_405(self):
        for fs in (5e6, 10e6):
            pl = narrow.plan(fs, "am", {"videoFormat": "405"})
            self.assertFalse(pl["teletext"])
            self.assertIn("405 lines", pl["teletextWhy"])
            self.assertIn("625", pl["teletextWhy"])

    def test_405_chosen_refuses_and_inserts_nothing(self):
        r = self.radio()
        r.start()
        self.assertIn("405 lines", r.state()["ttxRefused"])
        with self.assertRaisesRegex(ValueError, "405 lines"):
            r.caption("hello")
        r.ptt(True)
        self.assertTrue(Engine.wait(self, r, lambda: r.state()["rxFormat"] == "405" and r.state()["vLock"]))
        Engine.wait(self, r, lambda: False, timeout=1.0)
        r.ptt(False)
        self.assertEqual(r.tx_log()[-2]["teletext"], "")
        self.assertEqual(r.state()["ttxRx"].get("packets", 0), 0)
        # Back to 625 lines, the same service is sent.
        r.set(videoFormat="pal", amRate=10_000_000)
        Engine.wait(self, r, lambda: r.state()["running"] and r.state()["fs"] == 10e6)
        self.assertEqual(r.state()["ttxRefused"], "")

    def test_the_slicer_never_runs_on_405_lines(self):
        from unittest import mock
        from ohsatv import systems
        for fs in (5e6, 10e6):
            p = {"videoFormat": "405"}
            dec = systems.make_decoder(fs, "am", p)
            self.assertIsNone(dec.slicer)
            loop = systems.build_loop(fs, "am", p, testcard.test_card("GM4BRI", "IO86", lines405=True))
            dem = systems.make_demod(fs, "am", p)
            n = int(0.04 * fs)
            frames = []
            with mock.patch.object(vbi.Slicer, "slice", side_effect=AssertionError("sliced a 405 frame")):
                for k in range(12):
                    iq = loop[(k % 4) * n:(k % 4 + 1) * n]
                    if k % 2 == 0:
                        dem.track(iq)
                    frames += dec.feed(dem(iq))
            self.assertTrue(frames, fs)
            self.assertTrue(all(f["ttx"] == [] and f["stats"]["snr"] is not None for f in frames))

    def test_405_found_on_the_air_stops_slicing_but_not_our_teletext(self):
        # formatAuto switches the receiver to 405 (the simulator's 405-line
        # station on 1287.5 MHz): nothing is sliced. What we send is our own
        # videoFormat, PAL here, so our teletext stays allowed - detection
        # never changes what is transmitted.
        r = self.radio(videoFormat="pal", amRate=10_000_000, rxFreq=1287.5e6, txFreq=1287.5e6)
        r.start()
        self.assertTrue(Engine.wait(self, r, lambda: r.state()["rxFormat"] == "405"
                                    and r.state()["rxLock"] == "locked", timeout=10))
        self.assertIsNone(r.decoder.slicer)
        self.assertEqual(r.state()["ttxRefused"], "")

    def test_the_cable_feed_refuses_teletext(self):
        r = self.radio()
        r.start()
        r.set(authorise=True, cableFeedAck=True, cableFeed=True)
        self.assertTrue(r.state()["cableFeed"])
        self.assertEqual(r.state()["band"], "Band I")
        self.assertIn("cable feed's Band I", r.state()["ttxRefused"])
        self.assertIn("choose UHF", r.state()["ttxRefused"])
        with self.assertRaisesRegex(ValueError, "cable feed"):
            r.caption("hello")
        r.set(authorise=True, cableFeed=False)
        self.assertNotIn("cable feed", r.state()["ttxRefused"])


class CableUhf(unittest.TestCase):
    """The cable feed on UHF is 625-line PAL into a teletext-era set, and
    teletext and subtitles go with it under the same rule as on the air
    (DESIGN.md "Cable feed"): sent where the plan carries them, refused
    where it does not, and decoded off the simulator's loopback."""

    def radio(self, **over):
        r = Engine.make(self, **over)
        r.start()
        r.set(authorise=True, cableFeedAck=True, cableFeed=True)
        r.set(band="UHF")
        return r

    def test_allowed_on_uhf_and_refused_on_band_i(self):
        r = self.radio()
        st = r.state()
        self.assertEqual((st["band"], st["videoFormat"], st["txFreq"]), ("UHF", "pal", 591.25e6))
        self.assertEqual(st["ttxRefused"], "")
        self.assertIn("Sending 2 pages", st["ttxStatus"])
        r.set(band="Band I")
        self.assertIn("cable feed's Band I", r.state()["ttxRefused"])
        r.set(band="UHF")
        self.assertEqual(r.state()["ttxRefused"], "")

    def test_the_plan_still_decides_on_uhf(self):
        # A USB Pluto's 5 MS/s carries 2.4 MHz of video: refused with the
        # bandwidth sentence, cable feed or not.
        r = self.radio(amRate=5_000_000)
        self.assertIn("3.75 MHz", r.state()["ttxRefused"])
        with self.assertRaisesRegex(ValueError, "3.75 MHz"):
            r.caption("hello")

    def test_pages_and_subtitles_decode_off_the_loopback(self):
        r = self.radio()
        r.ptt(True)
        self.assertTrue(Engine.wait(self, r, lambda: r.state()["vLock"] and r.state()["rxFormat"] == "pal"))
        pg = Engine.wait(self, r, lambda: (lambda p: p if p and p["links"] else None)(r.ttx_page("100")))
        self.assertIsNotNone(pg)
        self.assertEqual(pg["rows"][1][:24], b"\x03Hello from the carousel")
        self.assertTrue(pg["rows"][0].startswith(b"P100    GM4BRI   100 "))
        r.caption("On the cable")
        sub = Engine.wait(self, r, lambda: any(b"On the cable" in row for row in (r.ttx_page("888") or {"rows": []})["rows"]))
        self.assertTrue(sub)
        r.ptt(False)
        key = [e for e in r.tx_log() if e["event"] == "key"][-1]
        self.assertEqual((key["band"], key["cableFeed"], key["gainDb"]), ("UHF", True, -89.75))
        self.assertIn("Sending 2 pages", key["teletext"])


class Plan(unittest.TestCase):
    """Which modes carry teletext: narrow.teletext_fit, the rule
    docs/teletext-eval.md measured."""

    def test_modes(self):
        cases = [
            ("am", 10e6, {}, True), ("am", 8e6, {}, True), ("am", 6.25e6, {}, False), ("am", 5e6, {}, False),
            ("fm", 20e6, {}, True), ("fm", 16e6, {"fmDeviation": 4e6}, True),
            ("fm", 12.5e6, {"fmDeviation": 1.2e6, "fmVideoBw": 4.9e6, "fmSub": 5.5e6}, True),
            ("fm", 10e6, {"fmDeviation": 3e6, "fmVideoBw": 3e6, "fmSub": 0.0}, False),
            ("fm", 5e6, {"fmDeviation": 1.2e6, "fmVideoBw": 1.6e6, "fmSub": 0.0}, False),
            ("fm", 5e6, {"fmDeviation": 1.2e6, "fmVideoBw": 1.6e6, "fmSub": 0.0, "videoFormat": "mac"}, False),
            ("fm", 20e6, {"videoFormat": "lowpal"}, False),
        ]
        for system, fs, p, want in cases:
            pl = narrow.plan(fs, system, p)
            self.assertEqual(pl["teletext"], want, (system, fs, p))
            if not want:
                self.assertTrue(pl["teletextWhy"].startswith("Teletext and subtitles are not sent"),
                                pl["teletextWhy"])
        # Carried, but with the spectrum cut inside the standard's 5 MHz: said.
        self.assertIn("television", narrow.plan(8e6, "am", {})["teletextWhy"])
        self.assertEqual(narrow.plan(20e6, "fm", {})["teletextWhy"], "")

    def test_a_refused_mode_really_does_not_decode(self):
        # The rule's other side, through the chain: with 3 MHz of video
        # (refused) nothing would survive even if it were sent.
        self.assertFalse(narrow.plan(8e6, "am", {"amVideoBw": 3.0e6})["teletext"])
        sent, got, _ = loopback("am", 8e6, {"amVideoBw": 3.0e6}, 40.0, frames=6)
        self.assertEqual(len(set(got) & set(sent)), 0)


PAGE_100 = b"PN,10000\r\nOL,1,\x83Hello from the carousel\r\nFL,101,102,103,104,8FF,100\r\n"
PAGE_101 = b"PN,10100\r\nCT,2,T\r\nOL,2,first sub\r\nPN,10102\r\nOL,2,second sub\r\n"


class Engine(unittest.TestCase):
    """The engine with the simulator: our service keyed, looped back,
    sliced and stored - and refused where the mode cannot carry it."""

    def make(self, **over):
        import pathlib
        from ohsatv.audio import NullAudio
        from ohsatv.radio import Radio
        self.svc = str(tmp_dir(self, "ohsatv-ttx-"))
        for name, data in (("p100.tti", PAGE_100), ("p101.tti", PAGE_101)):
            with open(os.path.join(self.svc, name), "wb") as f:
                f.write(data)
        tmp = tmp_dir(self)
        cfg = dict(device="sim", audioBackend="null", system="am", band="23cm", rxFreq=1249.0e6, txFreq=1249.0e6,
                   amRate=10_000_000, simStations=True, callsign="GM4BRI", licenceAck=True, ttxOn=True,
                   ttxDir=self.svc, ttxInitial="101")
        cfg.update(over)
        r = Radio(cfg, conf_path=tmp / "c.json", audio=NullAudio(realtime=False), data_dir=tmp / "data")
        self.addCleanup(r.stop)
        return r

    def wait(self, r, fn, timeout=8.0):
        import time
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            r.heartbeat()
            v = fn()
            if v:
                return v
            time.sleep(0.05)
        return None

    def test_keyed_service_comes_back_as_pages(self):
        r = self.make()
        video = r.subscribe("video", 64)
        r.start()
        self.assertIn("Sending 2 pages", r.state()["ttxStatus"])
        self.assertEqual(r.state()["ttxRefused"], "")
        r.ptt(True)
        # The page with its links (X/27/0 comes after its 24 rows).
        pg = self.wait(r, lambda: (lambda p: p if p and p["links"] else None)(r.ttx_page("100")))
        self.assertIsNotNone(pg)
        self.assertEqual(pg["rows"][1][:24], b"\x03Hello from the carousel")
        self.assertTrue(pg["rows"][0].startswith(b"P100    GM4BRI   100 "))
        self.assertEqual([ln and ln[0] for ln in pg["links"]], ["101", "102", "103", "104", None, "100"])
        # The carousel's second subpage (2 s cycle), header and its row.
        second = self.wait(r, lambda: (r.ttx_page("101", 2) or {"rows": [b""] * 3})["rows"][2][:10] == b"second sub",
                           timeout=6)
        self.assertTrue(second)
        self.assertEqual(r.ttx_page("101")["subs"], [1, 2])
        self.wait(r, lambda: r.state()["ttxRx"].get("initial"), timeout=3)
        self.assertEqual(r.state()["ttxRx"]["initial"], "101")
        # The picture is unharmed: still locked, still in colour.
        frames = video.drain()
        self.assertTrue(frames and frames[-1][2]["vLock"] and frames[-1][2]["colour"])
        r.ptt(False)
        self.assertIn("Sending 2 pages", r.tx_log()[-2]["teletext"])

    def test_an_edited_page_goes_out_on_the_next_over(self):
        r = self.make()
        r.start()
        r.ptt(True)
        self.assertTrue(self.wait(r, lambda: r.ttx_page("100")))
        r.ptt(False)
        with open(os.path.join(self.svc, "p100.tti"), "wb") as f:
            f.write(b"PN,10000\r\nOL,1,Edited\r\n")
        r.ptt(True)
        self.assertTrue(self.wait(r, lambda: (r.ttx_page("100") or {"rows": [b""] * 2})["rows"][1][:6] == b"Edited"))
        r.ptt(False)

    def test_a_page_edited_on_air_goes_out_without_a_break(self):
        r = self.make()
        r.start()
        r.ptt(True)
        self.assertTrue(self.wait(r, lambda: r.ttx_page("100")))
        tmp = os.path.join(self.svc, ".P100.tti.tmp")              # as the editor saves: write, rename
        with open(tmp, "wb") as f:
            f.write(b"PN,10000\r\nOL,1,Edited on air\r\n")
        os.replace(tmp, os.path.join(self.svc, "p100.tti"))
        with open(os.path.join(self.svc, "p1A0.tti"), "wb") as f:
            f.write(b"PN,1A000\r\nOL,1,Added on air\r\n")
        got = self.wait(r, lambda: (r.ttx_page("100") or {"rows": [b""] * 2})["rows"][1][:13] == b"Edited on air"
                        and r.ttx_page("1A0"), timeout=8.0)
        self.assertTrue(got)
        self.assertTrue(r.state()["keyed"])
        self.assertEqual([e["event"] for e in r.tx_log()], ["key"])      # one transmission throughout
        r.ptt(False)

    def test_subtitles_from_an_srt_and_typed_captions(self):
        import time as _t
        srt = os.path.join(tmp_dir(self, "ohsatv-srt-"), "subs.srt")
        with open(srt, "wb") as f:
            f.write(b"1\n00:00:00,000 --> 00:01:00,000\nFrom the file\n")
        r = self.make(ttxDir="", ttxSrt=srt)                    # subtitles only: no page folder
        r.start()
        self.assertIn("subtitles on 888 only", r.state()["ttxStatus"])
        r.ptt(True)
        got = self.wait(r, lambda: b"From the file" in ((r.ttx_page("888") or {"rows": [b""] * 25})["rows"][22]))
        self.assertTrue(got)
        self.assertEqual(r.state()["ttxSub"], "From the file")
        pg = r.ttx_page("888")
        self.assertEqual(pg["ctl"] & ttx.C6, ttx.C6)
        # A typed caption goes over the file's cue at once: the transmitter
        # puts it on 888 within a frame or two (0.05 s, idle or under load)...
        t0 = _t.monotonic()
        r.caption("Typed live|two lines")
        self.assertTrue(self.wait(r, lambda: r.state()["ttxSub"] == "Typed live|two lines"))
        self.assertLess(_t.monotonic() - t0, 1.0)
        # ...and it comes back off the air. Not timed: the loopback's receive
        # side is the simulator decoding on this host - 0.6-0.8 s idle, 1.5 s
        # to 4.5 s with the CPUs oversubscribed, which failed a 2 s bound on
        # the whole round trip in the nightly. That the carousel sends a
        # changed subtitle ahead of everything else, in the very next field,
        # is test_carousel_sends_subtitles_ahead_with_erase_and_repeats's,
        # counted in fields rather than seconds.
        self.assertTrue(self.wait(r, lambda: b"two lines" in (r.ttx_page("888")["rows"][22] or b"")))
        self.assertIn(b"Typed live", r.ttx_page("888")["rows"][20])
        # ...and an empty one gives the file's back.
        r.caption("")
        self.assertTrue(self.wait(r, lambda: b"From the file" in (r.ttx_page("888")["rows"][22] or b"")))
        r.ptt(False)

    @unittest.skipUnless(__import__("shutil").which("ffmpeg"), "ffmpeg not installed")
    def test_srt_follows_the_video_file_through_seek_and_pause(self):
        import subprocess
        import time as _t
        d = str(tmp_dir(self, "ohsatv-srt-"))
        clip = os.path.join(d, "clip.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25", "-t", "12",
                        "-pix_fmt", "yuv420p", clip], check=True, timeout=60)
        srt = os.path.join(d, "clip.srt")
        with open(srt, "wb") as f:
            f.write(b"1\n00:00:00,000 --> 00:00:04,000\nEarly words\n\n2\n00:00:07,000 --> 00:00:11,000\nLate words\n")
        r = self.make(ttxDir="", ttxSrt=srt, source="file", videoFile=clip)
        r.start()
        self.assertTrue(self.wait(r, lambda: (r.state()["filePos"] or 0) > 0.2))
        r.ptt(True)
        self.assertTrue(self.wait(r, lambda: r.state()["ttxSub"] == "Early words"))
        r.file_seek(7.5)
        self.assertTrue(self.wait(r, lambda: r.state()["ttxSub"] == "Late words", timeout=5))
        self.assertTrue(self.wait(r, lambda: b"Late words" in ((r.ttx_page("888") or {"rows": [b""] * 25})
                                                               ["rows"][22] or b"")))
        r.file_pause(True)
        self.wait(r, lambda: r.state()["filePaused"])
        p1 = r.state()["filePos"]
        _t.sleep(1.2)
        self.wait(r, lambda: False, timeout=0.3)
        self.assertEqual(r.state()["filePos"], p1)                      # held
        self.assertEqual(r.state()["ttxSub"], "Late words")
        self.assertTrue(r.state()["keyed"])                              # the transmitter carried on
        r.file_pause(False)
        self.assertTrue(self.wait(r, lambda: (r.state()["filePos"] or 0) > p1 + 0.3, timeout=5))
        r.ptt(False)

    def test_received_pages_save_as_tti_and_t42(self):
        from ohsatv import ttxsample
        src = str(ttxsample.write(str(tmp_dir(self, "ohsatv-sample-"))))
        r = self.make(ttxDir=src)
        r.start()
        r.ptt(True)
        self.assertTrue(self.wait(r, lambda: all(r.ttx_page(x) for x in ("100", "101", "103"))
                                  and len((r.ttx_page("102") or {}).get("subs", [])) == 2, timeout=15))
        # Wait until every row and link of the carousel's pages is in.
        self.wait(r, lambda: False, timeout=1.5)
        r.ptt(False)
        out = str(tmp_dir(self, "ohsatv-saved-"))
        n, t42 = r.ttx_save_all(out)
        self.assertEqual(n, 4)
        names = sorted(os.listdir(out))
        self.assertEqual([x for x in names if x.endswith(".tti")], ["P100.tti", "P101.tti", "P102.tti", "P103.tti"])
        # Read back with our reader: the same page as was sent, row for row.
        sent, saved = tti.load_service(src), tti.load_service(out)
        for key, pg in sent.pages.items():
            got = saved.pages[key]
            self.assertEqual(len(got.subpages), len(pg.subpages), pg.label)
            for a, b in zip(pg.subpages, got.subpages):
                for y in range(1, 25):
                    self.assertEqual((a.rows[y] or b" " * 40), (b.rows[y] or b" " * 40), (pg.label, y))
                self.assertEqual([ln and ln[:2] for ln in b.links], [ln and ln[:2] for ln in a.links])
        # The dialect: CRLF, 7-bit, controls as ESC + code + 40, CT after its PN.
        with open(os.path.join(out, "P102.tti"), "rb") as f:
            data = f.read()
        self.assertTrue(all(c < 0x80 for c in data))
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""))
        self.assertIn(b"\x1b", data)
        lines = data.split(b"\r\n")
        heads = [ln[:2] for ln in lines if ln[:2] in (b"PN", b"SC", b"PS", b"CT")]
        self.assertEqual(heads, [b"PN", b"SC", b"PS", b"CT"] * 2)
        self.assertIn(b"PN,10201", lines)
        self.assertIn(b"SC,0002", lines)
        self.assertIn(b"FL,101,102,103,100,8FF,100", lines)
        # The capture: whole packets, our pages' headers among them.
        with open(t42, "rb") as f:
            cap = tti.read_t42(f.read())
        self.assertGreater(len(cap), 100)
        self.assertIn((1, 0), {ttx.parse_address(p) for p in cap})
        # One page; and one never received.
        one = os.path.join(out, "one.tti")
        r.ttx_save_page("103", one)
        self.assertEqual(tti.load_service(one).labels(), ["103"])
        with self.assertRaisesRegex(ValueError, "not been received"):
            r.ttx_save_page("777", one)

    def test_captions_refused_where_teletext_is(self):
        r = self.make(amRate=6_250_000)
        r.start()
        with self.assertRaisesRegex(ValueError, "subtitles are not sent"):
            r.caption("hello")

    def test_refused_where_the_mode_cannot_carry_it(self):
        r = self.make(amRate=6_250_000)
        r.start()
        why = r.state()["ttxRefused"]
        self.assertTrue(why.startswith("Teletext and subtitles are not sent"), why)
        self.assertEqual(r.state()["ttxStatus"], "")
        r.ptt(True)
        self.wait(r, lambda: False, timeout=2.0)
        self.assertEqual(r.state()["ttxRx"].get("packets", 0), 0)       # nothing was inserted
        self.assertEqual(r.tx_log()[-1]["teletext"], "")
        r.ptt(False)

    def test_folder_problems_are_sentences(self):
        r = self.make(ttxDir="")
        # No folder is not a refusal: teletext is then page 888's subtitles.
        self.assertEqual(r.state()["ttxRefused"], "")
        self.assertIn("subtitles on 888 only", r.state()["ttxStatus"])
        r.set(ttxDir="/nonexistent/ttx")
        self.assertIn("/nonexistent/ttx", r.state()["ttxRefused"])
        r.set(ttxOn=False)
        self.assertEqual(r.state()["ttxRefused"], "")

    def test_settings_are_checked(self):
        r = self.make()
        with self.assertRaisesRegex(ValueError, "cannot carry teletext"):
            r.set(ttxLines="7-30")
        with self.assertRaisesRegex(ValueError, "Initial page"):
            r.set(ttxInitial="900")
        self.assertEqual(r.set(ttxLines="10-12,323-325"), {"ttxLines": "10-12,323-325"})
        self.assertIn("6 lines", r.state()["ttxStatus"])


if __name__ == "__main__":
    unittest.main()
