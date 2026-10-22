"""Received material: .t42 captures back into pages - the round trip from our
own export, vbit2's own packets, and captures with the things real ones
have: repeats, interleaved magazines, time filling, serial headers, a
capture that starts mid-carousel, bit errors that Hamming corrects, errors
that only a vote across repeats can fix, and errors nothing can, which are
marked."""
import subprocess
import sys
import unittest
from pathlib import Path

from helpers import page_rows, tmp_dir

from ohstxt import header, receive, samples, t42, tti
from ohstxt.editor import Document
from ohstxt.page import PS_ERASE, Page, Subpage

FIXTURE = Path(__file__).parent / "fixtures" / "vbit2-sample.t42"
REPO = Path(__file__).resolve().parent.parent
NOW = 1_790_000_000.0


def packets(pages) -> list[bytes]:
    data = t42.dumps(pages, now=NOW)
    return [data[i:i + 42] for i in range(0, len(data), 42)]


def flip(pkt: bytes, byte: int, *bits: int) -> bytes:
    b = bytearray(pkt)
    for bit in bits:
        b[byte] ^= 1 << bit
    return bytes(b)


def row_of(pkts, mag, row, start=0):
    """Index of the first packet at or after `start` for that magazine and row."""
    for i in range(start, len(pkts)):
        a = t42.unham84(pkts[i][0])
        b = t42.unham84(pkts[i][1])
        if (a & 7) == (mag & 7) and ((a >> 3) | (b << 1)) == row:
            return i
    raise IndexError(row)


class RoundTrip(unittest.TestCase):
    def test_export_then_decode_gives_the_same_pages(self):
        service = samples.service()
        cap = receive.decode(b"".join(packets(service)))
        self.assertEqual([p.number for p in cap.pages], [p.number for p in service])
        for want, got in zip(service, cap.pages):
            back = got.to_page()
            self.assertEqual(len(back.subpages), len(want.subpages), want.label())
            for ws, gs in zip(want.subpages, back.subpages):
                self.assertEqual([bytes(r) for r in gs.rows[1:]], [bytes(r) for r in ws.rows[1:]], want.label())
                expanded = header.expand(ws.rows[0], want.number, NOW)
                self.assertEqual(bytes(gs.rows[0][8:]), expanded[8:])
                self.assertEqual(gs.subcode, ws.subcode)
                self.assertEqual(gs.status, ws.status)
                self.assertEqual(gs.links, ws.links)
                self.assertEqual(gs.damaged, set())
        self.assertEqual((cap.bad_address, cap.bad_headers, cap.orphan_rows), (0, 0, 0))

    def test_vbit2s_own_packets(self):
        cap = receive.load(FIXTURE)
        p100 = cap.find(0x100).to_page().subpages[0]
        want = samples.news_index().subpages[0]
        self.assertEqual([bytes(r) for r in p100.rows[1:]], [bytes(r) for r in want.rows[1:]])
        self.assertEqual(p100.links, want.links)
        p300 = cap.find(0x300)
        self.assertEqual([s.subcode for s in p300.subpages], [0x0002])
        self.assertEqual([bytes(r) for r in p300.subpages[0].sub.rows[1:]],
                         [bytes(r) for r in samples.weather_carousel().subpages[1].rows[1:]])

    def test_the_whole_service_through_a_tti_folder(self):
        cap = receive.decode(b"".join(packets(samples.service())))
        d = tmp_dir(self, "ohstxt-test-")
        r = subprocess.run([sys.executable, "-m", "ohstxt.receive", "-", "--out", str(d)], cwd=REPO,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)                  # '-' is not a file
        cfile = d / "c.t42"
        cfile.write_bytes(b"".join(packets(samples.service())))
        r = subprocess.run([sys.executable, "-m", "ohstxt.receive", str(cfile), "--out", str(d / "pages")],
                           cwd=REPO, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("P300  0001×1, 0002×1, 0003×1", r.stdout)
        back = tti.load(d / "pages" / "P199.tti")
        self.assertEqual([bytes(x) for x in back.subpages[0].rows[1:]],
                         [bytes(x) for x in cap.find(0x199).subpages[0].sub.rows[1:]])


class Errors(unittest.TestCase):
    def setUp(self):
        self.page = samples.test_page()
        self.one = packets([self.page])

    def test_a_single_bit_error_in_an_address_is_corrected(self):
        pk = list(self.one)
        pk[3] = flip(pk[3], 0, 2)                 # MRAG of row 3
        pk[0] = flip(pk[0], 3, 5)                 # page tens in the header
        cap = receive.decode(b"".join(pk))
        self.assertEqual(cap.corrected, 2)
        self.assertEqual(bytes(cap.find(0x199).subpages[0].sub.rows[3]), bytes(self.page.subpages[0].rows[3]))

    def test_an_unreadable_address_drops_the_packet(self):
        pk = list(self.one)
        pk[5] = flip(pk[5], 1, 0, 1)              # two errors: row number lost
        cap = receive.decode(b"".join(pk))
        self.assertEqual(cap.bad_address, 1)
        self.assertEqual(bytes(cap.find(0x199).subpages[0].sub.rows[5]), b" " * 40)

    def test_an_unreadable_header_does_not_file_rows_under_the_wrong_page(self):
        other = packets([samples.news_index()])
        pk = other + list(self.one)
        pk[len(other)] = flip(pk[len(other)], 2, 0, 1)   # P199's page units: double error
        cap = receive.decode(b"".join(pk))
        self.assertEqual(cap.bad_headers, 1)
        self.assertIsNone(cap.find(0x199))
        self.assertEqual(cap.orphan_rows, 24)
        p100 = cap.find(0x100).subpages[0].sub
        self.assertEqual(bytes(p100.rows[3]), bytes(samples.news_index().subpages[0].rows[3]))

    def test_repeats_vote_out_parity_errors(self):
        loops = [list(self.one) for _ in range(3)]
        loops[0][3] = flip(loops[0][3], 10, 0)
        loops[1][3] = flip(loops[1][3], 11, 6)
        loops[2][7] = flip(loops[2][7], 20, 3)
        cap = receive.decode(b"".join(b"".join(lp) for lp in loops))
        rx = cap.find(0x199).subpages[0]
        self.assertEqual(rx.receptions, 3)
        self.assertEqual(rx.damaged, set())
        self.assertEqual([bytes(r) for r in rx.sub.rows[1:]], [bytes(r) for r in self.page.subpages[0].rows[1:]])

    def test_a_cell_with_no_good_copy_is_marked_and_a_damaged_code_is_a_space(self):
        pk = list(self.one)
        i5 = row_of(pk, 1, 5)
        pk[i5] = flip(pk[i5], 2 + 7, 1)           # row 5 col 7: 'r' of red, wrong in its only copy
        i1 = row_of(pk, 1, 1)
        pk[i1] = flip(pk[i1], 2 + 0, 0)           # row 1 col 0: alpha yellow, wrong
        rx = receive.decode(b"".join(pk)).find(0x199).subpages[0]
        self.assertEqual(rx.damaged, {(5, 7), (1, 0)})
        self.assertEqual(rx.sub.rows[1][0], 0x20)
        self.assertEqual(rx.sub.rows[5][7], self.page.subpages[0].rows[5][7] ^ 0x02)
        page = receive.decode(b"".join(pk)).find(0x199).to_page()
        self.assertEqual(page.subpages[0].damaged, {(5, 7), (1, 0)})


class Shapes(unittest.TestCase):
    def test_interleaved_magazines(self):
        a = packets([samples.news_index()])
        b = packets([Page(number=0x250, subpages=[Subpage(rows=page_rows((3, b"magazine two")))])])
        mixed = [x for pair in zip(a, b) for x in pair] + a[len(b):]
        cap = receive.decode(b"".join(mixed))
        self.assertEqual([p.number for p in cap.pages], [0x100, 0x250])
        self.assertEqual(cap.find(0x250).subpages[0].sub.text(3)[:12], "magazine two")
        self.assertEqual(bytes(cap.find(0x100).subpages[0].sub.rows[22]),
                         bytes(samples.news_index().subpages[0].rows[22]))

    def test_time_filling_headers_end_a_page_and_are_not_pages(self):
        pk = packets([samples.test_page()])
        filler = t42.header_packet(1, 0xFF, 0x3F7F, 0x8000, b" " * 32)
        stray = t42.row_packet(1, 20, b"after the filler")
        cap = receive.decode(b"".join(pk + [filler, stray]))
        self.assertEqual([p.number for p in cap.pages], [0x199])
        self.assertEqual(cap.orphan_rows, 1)

    def test_a_serial_header_ends_every_magazine(self):
        a = packets([samples.news_index()])
        serial = t42.header_packet(2, 0x00, 0, 0x8000, b" " * 32)
        b = bytearray(serial)
        b[9] = t42.ham84(1)                       # C11 set, national option 0
        stray = t42.row_packet(1, 23, b"late row for magazine 1")
        cap = receive.decode(b"".join(a[:5] + [bytes(b), stray]))
        self.assertEqual(cap.orphan_rows, 1)
        self.assertEqual([p.number for p in cap.pages], [0x100, 0x200])

    def test_a_capture_that_starts_mid_carousel_lists_it_in_order(self):
        pk = packets([samples.weather_carousel()])
        per = len(pk) // 3
        rotated = pk[per:] + pk[:per]
        rx = receive.decode(b"".join(rotated)).find(0x300)
        self.assertEqual([s.subcode for s in rx.subpages], [1, 2, 3])
        self.assertTrue(all(not (s.sub.status & PS_ERASE) for s in rx.subpages))


class IntoTheEditor(unittest.TestCase):
    def test_typing_over_a_damaged_cell_clears_its_mark(self):
        pg = Page(subpages=[Subpage(rows=page_rows((4, b"d4mage")))])
        pg.subpages[0].damaged = {(4, 1), (4, 3)}
        d = Document(pg)
        d.goto(4, 1)
        d.type_text("a")
        self.assertEqual(d.sub.damaged, {(4, 3)})
        d.undo()
        self.assertEqual(d.sub.damaged, {(4, 1), (4, 3)})

    def test_damage_is_never_written_to_a_tti(self):
        pg = Page(subpages=[Subpage()])
        pg.subpages[0].damaged = {(1, 1)}
        self.assertEqual(tti.loads(tti.dumps(pg)).subpages[0].damaged, set())


if __name__ == "__main__":
    unittest.main()
