"""The .tti contract: what this program writes, read back, is what it wrote;
and what other tools write - MRG's high-bit form, vbit2's habits, files
with odd line endings and lines this program does not understand - is
read the way vbit2 reads it and survives a save."""
import tempfile
import unittest
from pathlib import Path

from helpers import page_rows  # noqa: F401  (path setup)

from ohstxt import samples, tti
from ohstxt.page import NO_LINK, Page, Subpage, annex_a_subcode
from ohstxt.service import Service


def same(a: Page, b: Page):
    assert a.number == b.number, (a.number, b.number)
    assert a.description == b.description
    assert len(a.subpages) == len(b.subpages)
    for x, y in zip(a.subpages, b.subpages):
        assert [bytes(r) for r in x.rows] == [bytes(r) for r in y.rows]
        assert (x.subcode, x.status, x.cycle, x.timed, x.region) == (y.subcode, y.status, y.cycle, y.timed, y.region)
        assert x.has_links == y.has_links
        if x.has_links:
            assert x.links == y.links
        assert x.extra_rows == y.extra_rows
        assert x.extra_lines == y.extra_lines


class RoundTrip(unittest.TestCase):
    def test_every_sample_round_trips(self):
        for pg in samples.service():
            with self.subTest(page=pg.label()):
                same(pg, tti.loads(tti.dumps(pg)))

    def test_every_code_in_every_cell_round_trips(self):
        s = Subpage()
        for r in range(25):
            for c in range(40):
                s.rows[r][c] = (r * 40 + c) % 128
        pg = Page(number=0x8FE, description="all codes", subpages=[s])
        same(pg, tti.loads(tti.dumps(pg)))

    def test_high_bit_form_round_trips_too(self):
        pg = samples.test_page()
        same(pg, tti.loads(tti.dumps(pg, escape="high")))

    def test_written_file_is_seven_bit_ascii_with_crlf_and_no_blank_lines(self):
        data = tti.dumps(samples.test_page())
        self.assertTrue(all(b < 0x80 for b in data))
        lines = data.split(b"\r\n")
        self.assertEqual(lines[-1], b"")
        self.assertNotIn(b"", lines[:-1])
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""))

    def test_carousel_writes_one_block_per_subpage_with_annex_a_subcodes(self):
        pg = samples.weather_carousel()
        text = tti.dumps(pg).decode()
        self.assertEqual(text.count("PN,"), 3)
        self.assertIn("PN,30001", text)
        self.assertIn("PN,30003", text)
        back = tti.loads(text)
        self.assertEqual([s.subcode for s in back.subpages], [1, 2, 3])
        self.assertEqual([s.cycle for s in back.subpages], [10, 10, 15])

    def test_annex_a_numbering(self):
        self.assertEqual([annex_a_subcode(i) for i in (1, 9, 10, 79, 80)], [0x0001, 0x0009, 0x0010, 0x0079, 0x0100])


class ReadsWhatOthersWrite(unittest.TestCase):
    def test_mrg_high_bit_controls(self):
        pg = tti.loads(b"PN,15000\r\nOL,1,\x81red\x8dDOUBLE\r\n")
        self.assertEqual(bytes(pg.subpages[0].rows[1][:11]), b"\x01red\x0dDOUBLE")
        self.assertEqual(pg.number, 0x150)

    def test_viewdata_escapes(self):
        pg = tti.loads("PN,10000\nOL,2,\x1bAred\x1b]new bg\n")
        self.assertEqual(bytes(pg.subpages[0].rows[2][:11]), b"\x01red\x1dnew bg")

    def test_any_byte_is_taken_modulo_128(self):
        pg = tti.loads(b"PN,10000\nOL,1,\xc1\xe2\n")
        self.assertEqual(bytes(pg.subpages[0].rows[1][:2]), b"Ab")

    def test_a_raw_control_byte_ends_the_row_as_vbit2_does(self):
        pg = tti.loads(b"PN,10000\nOL,1,abc\x05def\n")
        self.assertEqual(bytes(pg.subpages[0].rows[1][:6]), b"abc   ")

    def test_short_rows_are_padded_and_long_rows_cut(self):
        pg = tti.loads("PN,10000\nOL,1,hi\nOL,2," + "x" * 50 + "\n")
        self.assertEqual(bytes(pg.subpages[0].rows[1]), b"hi" + b" " * 38)
        self.assertEqual(bytes(pg.subpages[0].rows[2]), b"x" * 40)

    def test_commas_in_text_are_text(self):
        pg = tti.loads("PN,10000\nOL,3,one, two, three\nDE,a, b\n")
        self.assertEqual(pg.subpages[0].text(3).rstrip(), "one, two, three")
        self.assertEqual(pg.description, "a, b")

    def test_line_endings_lf_cr_and_crlf_and_a_bom(self):
        for nl in ("\n", "\r", "\r\n"):
            text = nl.join(["DE,x", "PN,20100", "OL,1,hello", "FL,100,200,300,400,8ff,100"]) + nl
            for data in (text.encode(), b"\xef\xbb\xbf" + text.encode()):
                pg = tti.loads(data)
                self.assertEqual(pg.number, 0x201)
                self.assertEqual(pg.subpages[0].text(1).rstrip(), "hello")
                self.assertEqual(pg.subpages[0].links[:4], [0x100, 0x200, 0x300, 0x400])

    def test_ct_and_ps_before_pn_are_defaults_and_carry_forward(self):
        text = "\n".join(["CT,20,C", "PS,C000", "PN,10001", "SC,0001", "PN,10002", "SC,0002", "CT,5,T", "PS,8001",
                          "PN,10003", "SC,0003"]) + "\n"
        pg = tti.loads(text)
        cs = [(s.cycle, s.timed, s.status) for s in pg.subpages]
        self.assertEqual(cs, [(20, False, 0xC000), (5, True, 0x8001), (5, True, 0x8001)])

    def test_page_number_is_the_first_pn(self):
        pg = tti.loads("PN,1A001\nPN,2B002\n")
        self.assertEqual(pg.number, 0x1A0)
        self.assertEqual(len(pg.subpages), 2)

    def test_zero_and_8ff_links_mean_none(self):
        pg = tti.loads("PN,10000\nFL,0,8ff,1a0,100\n")
        self.assertEqual(pg.subpages[0].links, [NO_LINK, NO_LINK, 0x1A0, 0x100, NO_LINK, NO_LINK])

    def test_unknown_lines_and_enhancement_rows_survive_a_save(self):
        text = ("DS,inserter\r\nSP,c:\\pages\\p100.tti\r\nDE,keep me\r\nPN,10000\r\nSC,0000\r\nPS,8000\r\n"
                "MS,somemask\r\nOL,1,text\r\nOL,26,@@@ABCDEF\r\nOL,28,@AAAAAAA\r\nZZ,future\r\n")
        pg = tti.loads(text)
        out = tti.dumps(pg).decode("latin-1")
        for keep in ("DS,inserter", "SP,c:\\pages\\p100.tti", "MS,somemask", "OL,26,@@@ABCDEF", "OL,28,@AAAAAAA",
                     "ZZ,future"):
            self.assertIn(keep, out)
        same(pg, tti.loads(out))

    def test_rows_before_any_pn_are_kept(self):
        pg = tti.loads("OL,1,orphan\n")
        self.assertEqual(pg.subpages[0].text(1).rstrip(), "orphan")

    def test_garbage_is_refused(self):
        with self.assertRaises(tti.TTIError):
            tti.loads(b"this is not a page at all")


class Folder(unittest.TestCase):
    def test_service_folder_saves_and_reopens_every_page(self):
        with tempfile.TemporaryDirectory() as d:
            svc = Service(Path(d))
            for pg in samples.service():
                svc.pages.append(pg)
                svc.save(pg)
            self.assertEqual(sorted(p.name for p in Path(d).iterdir()),
                             ["P100.tti", "P150.tti", "P199.tti", "P300.tti"])
            back = Service.open(d)
            self.assertEqual([p.number for p in back.pages], [0x100, 0x150, 0x199, 0x300])
            for a, b in zip(sorted(samples.service(), key=lambda p: p.number), back.pages):
                same(a, b)

    def test_save_is_atomic_and_leaves_no_temporary_file(self):
        with tempfile.TemporaryDirectory() as d:
            tti.save(samples.news_index(), Path(d) / "x.tti")
            self.assertEqual([p.name for p in Path(d).iterdir()], ["x.tti"])

    def test_unreadable_file_is_reported_not_fatal(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "bad.tti").write_bytes(b"nonsense")
            tti.save(samples.news_index(), Path(d) / "good.tti")
            svc = Service.open(d)
            self.assertEqual(len(svc.pages), 1)
            self.assertEqual(len(svc.problems), 1)

    def test_duplicate_page_numbers_are_found(self):
        with tempfile.TemporaryDirectory() as d:
            tti.save(samples.news_index(), Path(d) / "a.tti")
            tti.save(samples.news_index(), Path(d) / "b.tti")
            self.assertEqual(Service.open(d).duplicates(), {0x100: ["a.tti", "b.tti"]})


if __name__ == "__main__":
    unittest.main()
