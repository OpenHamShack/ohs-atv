"""Raw .t42: the Hamming 8/4 code corrects and detects what it should, every
character byte has odd parity, and headers, rows and X/27/0 read back to
what was written."""
import unittest

from helpers import page_rows  # noqa: F401  (path setup)

from ohstxt import samples, t42
from ohstxt.page import NO_LINK, PS_ERASE, PS_NEWSFLASH, PS_SUBTITLE, PS_TRANSMIT, Page, Subpage


class Hamming(unittest.TestCase):
    def test_codewords_are_at_least_four_bits_apart(self):
        for a in range(16):
            for b in range(a + 1, 16):
                self.assertGreaterEqual(bin(t42.HAM84[a] ^ t42.HAM84[b]).count("1"), 4)

    def test_every_nibble_decodes(self):
        for n in range(16):
            self.assertEqual(t42.unham84(t42.ham84(n)), n)

    def test_every_single_bit_error_is_corrected(self):
        for n in range(16):
            for bit in range(8):
                self.assertEqual(t42.unham84(t42.ham84(n) ^ (1 << bit)), n)

    def test_every_double_bit_error_is_detected(self):
        for n in range(16):
            for b1 in range(8):
                for b2 in range(b1 + 1, 8):
                    self.assertEqual(t42.unham84(t42.ham84(n) ^ (1 << b1) ^ (1 << b2)), -1)

    def test_the_standard_table(self):
        # ETS 300 706 table 3 (and vbit2's Hamming8EncodeTable)
        self.assertEqual(t42.ham84(0), 0x15)
        self.assertEqual(t42.ham84(0xF), 0xEA)


class Parity(unittest.TestCase):
    def test_every_character_has_odd_parity_and_keeps_its_value(self):
        for c in range(128):
            b = t42.odd_parity(c)
            self.assertEqual(bin(b).count("1") % 2, 1)
            self.assertEqual(b & 0x7F, c)

    def test_text_packets_carry_parity_on_every_byte(self):
        data = t42.dumps(samples.test_page(), now=0)
        for pk in t42.read_packets(data):
            if pk["row"] <= 24:
                self.assertTrue(pk["parity_ok"], pk["row"])


class Packets(unittest.TestCase):
    def test_single_page_is_header_24_rows_and_x27(self):
        pg = samples.news_index()
        data = t42.dumps(pg, now=0)
        self.assertEqual(len(data) % 42, 0)
        rows = [p["row"] for p in t42.read_packets(data)]
        self.assertEqual(rows, list(range(0, 25)) + [27])

    def test_rows_read_back_as_written(self):
        pg = samples.test_page()
        pks = t42.read_packets(t42.dumps(pg, now=0))
        for pk in pks:
            if 1 <= pk["row"] <= 24:
                self.assertEqual(pk["text"], bytes(pg.subpages[0].rows[pk["row"]]))

    def test_header_fields(self):
        sub = Subpage(subcode=0x2345 & 0x3F7F, status=PS_TRANSMIT | PS_ERASE | PS_NEWSFLASH | PS_SUBTITLE | 0x0028
                      | 0x0100)
        pg = Page(number=0x8A7, subpages=[sub])
        h = t42.read_packets(t42.dumps(pg, now=0))[0]
        self.assertEqual(h["magazine"], 8)
        self.assertEqual(h["page"], 0x8A7)
        self.assertEqual(h["subcode"], 0x2345 & 0x3F7F)
        self.assertEqual(h["status"], sub.status & 0x7FBF)     # C11 always parallel; bit 15 is not transmitted

    def test_header_text_is_columns_8_to_39_with_substitutions(self):
        pg = Page(number=0x123)
        pg.subpages[0].rows[0][8:16] = b"ABC %%# "
        h = t42.read_packets(t42.dumps(pg, now=0))[0]
        self.assertEqual(h["text"][:8], b"ABC 123 ")

    def test_fastext_links_with_relative_magazines(self):
        sub = Subpage()
        sub.links = [0x101, 0x250, 0x8FE, 0x1FF, NO_LINK, 0x100]
        sub.has_links = True
        pg = Page(number=0x150, subpages=[sub])
        x27 = [p for p in t42.read_packets(t42.dumps(pg, now=0)) if p["row"] == 27][0]
        self.assertEqual(x27["dc"], 0)
        self.assertEqual(x27["links"], sub.links)
        self.assertEqual(x27["control"], 0xF)

    def test_no_x27_without_links(self):
        pg = Page(number=0x100)
        self.assertNotIn(27, [p["row"] for p in t42.read_packets(t42.dumps(pg, now=0))])

    def test_carousel_sends_every_subpage_with_erase(self):
        pg = samples.weather_carousel()
        heads = [p for p in t42.read_packets(t42.dumps(pg, now=0)) if p["row"] == 0]
        self.assertEqual([h["subcode"] for h in heads], [1, 2, 3])
        self.assertTrue(all(h["status"] & PS_ERASE for h in heads))

    def test_whole_service_in_one_file(self):
        data = t42.dumps(samples.service(), now=0)
        pages = [p["page"] for p in t42.read_packets(data) if p["row"] == 0]
        self.assertEqual(pages, [0x100, 0x150, 0x199, 0x300, 0x300, 0x300])


class Crc(unittest.TestCase):
    def test_crc_depends_on_the_page_and_is_repeatable(self):
        rows = [b" " * 40] * 25
        a = t42.page_crc(b" " * 24, rows)
        self.assertEqual(a, t42.page_crc(b" " * 24, rows))
        b = t42.page_crc(b" " * 24, [b"X" + b" " * 39] + rows[1:])
        self.assertNotEqual(a, b)

    def test_crc_is_the_x27_crc(self):
        pg = samples.news_index()
        pks = t42.read_packets(t42.dumps(pg, now=0))
        head = pks[0]["text"]
        rows = [pk["text"] for pk in pks if 1 <= pk["row"] <= 24] + [b" " * 40]
        x27 = [p for p in pks if p["row"] == 27][0]
        self.assertEqual(x27["crc"], t42.page_crc(head, rows))


class AgainstVbit2(unittest.TestCase):
    """tests/fixtures/vbit2-sample.t42 is what vbit2 (built from
    github.com/peterkvt80/vbit2, October 2026) transmitted when given the
    sample service as .tti files written by this program: P100, and the
    second subpage of the P300 carousel, each from its header to the next
    header in its magazine. It checks the whole contract at once - vbit2
    read our .tti rows, subcodes and links as we meant them - and that our
    page CRC is the one vbit2 sends."""

    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        data = (Path(__file__).parent / "fixtures" / "vbit2-sample.t42").read_bytes()
        cls.pages = []
        for pk in t42.read_packets(data):
            if pk["row"] == 0:
                cls.pages.append({"head": pk, "rows": {}, "x27": None})
            elif pk["row"] == 27:
                cls.pages[-1]["x27"] = pk
            else:
                cls.pages[-1]["rows"][pk["row"]] = pk["text"]
        cls.want = {pg.number: pg for pg in samples.service()}

    def sub_for(self, head):
        pg = self.want[head["page"]]
        return pg.subpages[0] if len(pg.subpages) == 1 else pg.subpages[head["subcode"] - 1]

    def test_vbit2_sent_our_rows(self):
        self.assertEqual([(p["head"]["page"], p["head"]["subcode"]) for p in self.pages], [(0x100, 0), (0x300, 2)])
        for p in self.pages:
            sub = self.sub_for(p["head"])
            for r in range(1, 25):
                # vbit2 leaves blank rows out; those must be blank in ours
                self.assertEqual(p["rows"].get(r, b" " * 40), bytes(sub.rows[r]), (hex(p["head"]["page"]), r))

    def test_vbit2_sent_our_links(self):
        for p in self.pages:
            self.assertEqual(p["x27"]["links"], self.sub_for(p["head"]).links)

    def test_our_crc_is_vbit2s(self):
        for p in self.pages:
            sub = self.sub_for(p["head"])
            rows = [bytes(sub.rows[r]) for r in range(1, 25)] + [b" " * 40]
            self.assertEqual(t42.page_crc(p["head"]["text"], rows), p["x27"]["crc"])


if __name__ == "__main__":
    unittest.main()
