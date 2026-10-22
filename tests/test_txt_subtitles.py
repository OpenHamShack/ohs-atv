"""Subtitles: SRT read leniently and written strictly (the subset OpenHamShack ATV
reads), round trips, the warnings, and a cue laid out as a real subtitle
page - checked through the decoder: double height, boxed, the speaker's
colour, centred, and nothing outside the boxes shown on a C6 page."""
import unittest

from helpers import page_rows  # noqa: F401  (path setup)

from ohstxt import decoder, render, subtitles as st
from ohstxt.page import PS_SUBTITLE

MESSY = ("﻿1\r\n00:00:01,000 --> 00:00:03,500\r\n<font color=\"#FFFF00\">Yellow speaker</font>\r\n"
         "<i>white, in italics</i>\r\n\r\n"
         "7\r\n00:00:04.2 --> 00:00:06,000 X1:10 X2:20\r\n{\\an8}<font color=cyan>Fish &amp; chips</font>\r\n\r\n"
         "00:00:07,000 --> 00:00:08,000\r\n<b>no number</b>\r\n<font color='#f00'>red</font>\r\nthird line\r\n\r\n"
         "garbage block\r\n")


class Srt(unittest.TestCase):
    def test_lenient_read(self):
        track, problems = st.parse_srt(MESSY)
        self.assertEqual(len(track.cues), 3)
        a, b, c = track.cues
        self.assertEqual((a.start, a.end), (1.0, 3.5))
        self.assertEqual([(ln.text, ln.colour) for ln in a.lines], [("Yellow speaker", 3), ("white, in italics", 7)])
        self.assertEqual((b.start, b.lines[0].text, b.lines[0].colour), (4.2, "Fish & chips", 6))
        self.assertEqual([(ln.text, ln.colour) for ln in c.lines], [("no number", 7), ("red", 1), ("third line", 7)])
        self.assertEqual(problems, ["no timing in: garbage block"])

    def test_colours_go_to_the_nearest_teletext_colour(self):
        self.assertEqual(st.nearest_colour("#ffa500"), 3)       # orange: yellow
        self.assertEqual(st.nearest_colour("#00ff7f"), 2)       # spring green: green
        self.assertEqual(st.nearest_colour("#000000"), 7)       # black cannot be a subtitle: white
        self.assertEqual(st.nearest_colour("Magenta"), 5)
        self.assertEqual(st.nearest_colour("not a colour"), 7)

    def test_strict_write(self):
        track, _ = st.parse_srt(MESSY)
        out = st.dumps_srt(track)
        self.assertEqual(out.split("\n")[:9], [
            "1", "00:00:01,000 --> 00:00:03,500", '<font color="#ffff00">Yellow speaker</font>',
            "white, in italics", "",
            "2", "00:00:04,200 --> 00:00:06,000", '<font color="#00ffff">Fish &amp; chips</font>', ""])
        self.assertNotIn("third line", out)                       # two lines a cue, at most
        self.assertNotIn("\r", out)

    def test_round_trip(self):
        track, _ = st.parse_srt(MESSY)
        for c in track.cues:
            del c.lines[2:]
        back, problems = st.parse_srt(st.dumps_srt(track))
        self.assertEqual(problems, [])
        self.assertEqual([(c.start, c.end, [(ln.text, ln.colour) for ln in c.lines]) for c in back.cues],
                         [(c.start, c.end, [(ln.text, ln.colour) for ln in c.lines]) for c in track.cues])

    def test_written_text_is_what_the_set_shows(self):
        t = st.Track([st.Cue(0, 1, [st.Line("[MUSIC] 1/2 #1 <loud>")])])
        self.assertIn("←MUSIC→ 1/2 #1 &lt;loud&gt;", st.dumps_srt(t))


class Warnings(unittest.TestCase):
    def test_long_lines_too_many_lines_timing_and_odd_characters(self):
        t = st.Track([st.Cue(0, 2, [st.Line("x" * 38)]),
                      st.Cue(1.5, 1.0, [st.Line("a"), st.Line("b"), st.Line("[c]")])])
        self.assertEqual(st.warnings(t, 0), ["line 1 is 38 characters; a subtitle line holds 37"])
        w = st.warnings(t, 1)
        self.assertIn("3 lines; a subtitle has at most 2", w)
        self.assertIn("ends before it starts", w)
        self.assertIn("overlaps the cue before", w)
        self.assertIn("on a set '[' is '←', ']' is '→'", w)

    def test_thirty_seven_is_fine(self):
        self.assertEqual(st.warnings(st.Track([st.Cue(0, 1, [st.Line("y" * 37, 3)])]), 0), [])


class AsAPage(unittest.TestCase):
    def decoded(self, cue):
        page = st.cue_page(cue)
        sub = page.subpages[0]
        self.assertTrue(sub.status & PS_SUBTITLE)
        return sub, decoder.decode(sub.rows)

    def test_one_line_is_boxed_double_height_centred_on_row_22(self):
        sub, d = self.decoded(st.Cue(0, 1, [st.Line("Hello there", 3)]))
        text = sub.text(22)
        start = text.index("Hello")
        self.assertAlmostEqual(start + len("Hello there") / 2, 20, delta=1)
        cols = range(start, start + 11)
        self.assertTrue(all(d.box[22, c] for c in cols))
        self.assertTrue(all(int(d.fg[22, c]) == 3 for c in cols))
        self.assertTrue(all(int(d.dh[22, c]) == decoder.DH_TOP for c in cols))
        self.assertTrue(d.hidden_row[23])
        self.assertTrue(d.box[22, start - 1] and d.box[22, start + 11])     # a black margin each side
        self.assertFalse(d.box[22, start - 3] or d.box[22, start + 13])     # and no further

    def test_two_lines_on_rows_20_and_22(self):
        sub, d = self.decoded(st.Cue(0, 1, [st.Line("first", 2), st.Line("second")]))
        self.assertIn("first", sub.text(20))
        self.assertIn("second", sub.text(22))
        self.assertTrue(d.hidden_row[21] and d.hidden_row[23])

    def test_thirty_seven_characters_fit_in_colour(self):
        sub, d = self.decoded(st.Cue(0, 1, [st.Line("Z" * 37, 5)]))
        row = bytes(sub.rows[22])
        self.assertEqual(row[:3], bytes([0x0D, 0x0B, 0x05]))
        self.assertEqual(row[3:], b"Z" * 37)
        self.assertTrue(all(int(d.fg[22, c]) == 5 and d.box[22, c] for c in range(3, 40)))

    def test_only_the_boxes_show_on_the_set(self):
        sub, d = self.decoded(st.Cue(0, 1, [st.Line("Boxed")]))
        idx = render.render_indices(d, mixed=True)
        self.assertTrue((idx[:20 * 20] == 8).all())               # rows 0-19: all picture
        self.assertTrue((idx[22 * 20:24 * 20] == 7).any())        # white text in the box

    def test_no_cue_is_an_empty_page(self):
        self.assertTrue(all(bytes(r) == b" " * 40 for r in st.cue_rows(None)))


if __name__ == "__main__":
    unittest.main()
