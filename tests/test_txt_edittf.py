"""edit.tf / zxnet hash-URLs: the bit packing against a hand-worked case, a
round trip of every code in every cell, the old 24-row links, and the
extended keys - including the ones this program does not use, which must
come back unchanged."""
import random
import unittest

from helpers import page_rows

from ohstxt import edittf, samples
from ohstxt.page import NO_LINK


class Packing(unittest.TestCase):
    def test_hand_worked_prefix(self):
        # Spaces are 0100000. Seven of them, MSB first, give the bits
        # 010000 001000 000100 000010 000001 000000 100000 ... so the
        # first seven digits are Q I E C B A g.
        url = edittf.from_page(*_single(page_rows()))
        data = url.split("#", 1)[1].split(":")[1]
        self.assertEqual(data[:7], "QIECBAg")
        self.assertEqual(len(data), 1167)

    def test_every_code_in_every_cell_round_trips(self):
        rng = random.Random(7)
        rows = [bytearray(rng.randrange(128) for _ in range(40)) for _ in range(25)]
        back = edittf.decode_rows(edittf.encode_rows(rows))
        self.assertEqual([bytes(r) for r in back], [bytes(r) for r in rows])

    def test_old_24_row_links_read_with_a_blank_row_24(self):
        rows = page_rows((23, b"last row"), (24, b"gone"))
        data = edittf.encode_rows(rows)[:1120]
        hp = edittf.parse("https://edit.tf/#0:" + data)
        self.assertEqual(bytes(hp.rows[23][:8]), b"last row")
        self.assertEqual(bytes(hp.rows[24]), b" " * 40)

    def test_bad_input_is_refused(self):
        for bad in ("https://edit.tf/", "#0:short", "#Z:" + "A" * 1167, "#0:" + "!" * 1167):
            with self.assertRaises(edittf.HashError, msg=bad):
                edittf.parse(bad)


def _single(rows):
    from ohstxt.page import Page, Subpage
    s = Subpage(rows=rows)
    return Page(subpages=[s]), s


class Extended(unittest.TestCase):
    def test_sample_page_round_trips_with_number_status_and_links(self):
        pg = samples.news_index()
        url = edittf.from_page(pg, pg.subpages[0])
        back, sub = edittf.to_page(edittf.parse(url))
        self.assertEqual(back.number, 0x100)
        self.assertEqual([bytes(r) for r in sub.rows], [bytes(r) for r in pg.subpages[0].rows])
        self.assertEqual(sub.links, pg.subpages[0].links)
        self.assertEqual(sub.status, pg.subpages[0].status)

    def test_zxnet_url_and_black_flag(self):
        pg, s = _single(page_rows((1, b"\x00black")))
        url = edittf.from_page(pg, s, black_fg=True, base=edittf.ZXNET)
        self.assertTrue(url.startswith("https://zxnet.co.uk/teletext/editor/#8:"))
        hp = edittf.parse(url)
        self.assertTrue(hp.black_fg)
        self.assertEqual(hp.charset, 0)

    def test_unknown_keys_are_kept_and_given_back(self):
        pg, s = _single(page_rows((1, b"x")))
        url = edittf.from_page(pg, s) + ":X26=Hgp:zx=abc:X280=" + "0" * 64
        hp = edittf.parse(url)
        again = edittf.from_page(pg, s, keep=hp.fields)
        hp2 = edittf.parse(again)
        for k in ("X26", "zx", "X280"):
            self.assertEqual(hp2.fields[k], hp.fields[k])

    def test_x270_link_groups(self):
        pg, s = _single(page_rows())
        s.links = [0x101, 0x202, 0x303, 0x404, NO_LINK, 0x100]
        s.has_links = True
        url = edittf.from_page(pg, s)
        x270 = edittf.parse(url).fields["X270"]
        self.assertEqual(x270, "1013F7F2023F7F3033F7F4043F7F8FF3F7F1003F7FF")
        _, back = edittf.to_page(edittf.parse(url))
        self.assertEqual(back.links, s.links)


if __name__ == "__main__":
    unittest.main()
