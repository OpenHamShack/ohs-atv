"""The decoder against known cases from ETS 300 706 section 12.2: each test
builds a row with the codes in question and checks what the cells show,
cell by cell. The renderer is checked for what flash and reveal do to
pixels, since that is what the preview animates."""
import unittest

import numpy as np

from helpers import page_rows

from ohstxt import charset, decoder, render
from ohstxt.decoder import BLACK, BLUE, CYAN, DH_BOTTOM, DH_NONE, DH_TOP, GREEN, RED, WHITE, YELLOW
from ohstxt.samples import (AB, AC, AG, AR, AW, AY, BLACKBG, BLOCK, CONCEAL, CONTIG, DOUBLE, END_BOX, FLASH, GB,
                           GG, GR, GW, GY, HOLD, NEWBG, NORMAL, RELEASE, SEPAR, START_BOX, STEADY, test_page)


def dec(*rows, **kw):
    return decoder.decode(page_rows(*rows), **kw)


class Colours(unittest.TestCase):
    def test_row_starts_alpha_white_on_black(self):
        d = dec((1, b"A"))
        self.assertEqual((d.fg[1, 0], d.bg[1, 0]), (WHITE, BLACK))
        self.assertFalse(d.graphics[1, 0])
        self.assertEqual(d.gset[1, 0], charset.SET_ALPHA)

    def test_colour_is_set_after(self):
        d = dec((1, bytes([AR]) + b"X"))
        self.assertEqual(d.fg[1, 0], WHITE)        # the code's own cell keeps the old colour
        self.assertTrue(d.control[1, 0])
        self.assertEqual(d.code[1, 0], 0x20)
        self.assertEqual(d.fg[1, 1], RED)

    def test_state_resets_every_row(self):
        d = dec((1, bytes([AR, NEWBG]) + b"X"), (2, b"Y"))
        self.assertEqual((d.fg[2, 0], d.bg[2, 0]), (WHITE, BLACK))

    def test_new_background_is_set_at_and_takes_the_foreground(self):
        d = dec((1, bytes([AB, NEWBG, AY]) + b"X"))
        self.assertEqual(d.bg[1, 0], BLACK)
        self.assertEqual(d.bg[1, 1], BLUE)          # set-at: the NEWBG cell itself is blue
        self.assertEqual((d.fg[1, 3], d.bg[1, 3]), (YELLOW, BLUE))

    def test_black_background_is_set_at(self):
        d = dec((1, bytes([AB, NEWBG, BLACKBG]) + b"X"))
        self.assertEqual(d.bg[1, 2], BLACK)
        self.assertEqual(d.bg[1, 3], BLACK)

    def test_black_foreground_is_ignored_at_level_1_and_honoured_on_request(self):
        row = (1, bytes([AR, 0x00]) + b"X")
        self.assertEqual(dec(row).fg[1, 2], RED)
        self.assertEqual(dec(row, black_fg=True).fg[1, 2], BLACK)


class Graphics(unittest.TestCase):
    def test_graphics_colour_draws_mosaics(self):
        d = dec((1, bytes([GG, BLOCK, 0x21])))
        self.assertTrue(d.graphics[1, 1])
        self.assertEqual(d.gset[1, 1], charset.SET_CONTIG)
        self.assertEqual(d.fg[1, 1], GREEN)

    def test_blast_through_capitals_are_alpha(self):
        d = dec((1, bytes([GG]) + b"A"))
        self.assertEqual(d.gset[1, 1], charset.SET_ALPHA)
        self.assertEqual(d.code[1, 1], ord("A"))

    def test_separated_is_set_at_and_contiguous_returns(self):
        d = dec((1, bytes([GG, BLOCK, SEPAR, BLOCK, CONTIG, BLOCK])))
        self.assertEqual(d.gset[1, 1], charset.SET_CONTIG)
        self.assertEqual(d.gset[1, 3], charset.SET_SEP)
        self.assertEqual(d.gset[1, 5], charset.SET_CONTIG)

    def test_separated_glyph_has_gaps(self):
        self.assertLess(charset.SEPARATED[0x7F].sum(), charset.CONTIG[0x7F].sum())
        self.assertEqual(charset.CONTIG[0x7F].sum(), 12 * 20)


class Hold(unittest.TestCase):
    def test_without_hold_a_colour_change_leaves_a_gap(self):
        d = dec((1, bytes([GR, BLOCK, GY, BLOCK])))
        self.assertEqual(d.code[1, 2], 0x20)

    def test_hold_repeats_the_last_mosaic_in_the_old_colour(self):
        d = dec((1, bytes([GR, HOLD, BLOCK, GY, BLOCK])))
        self.assertEqual(d.code[1, 3], BLOCK)       # the GY cell shows the held block...
        self.assertEqual(d.fg[1, 3], RED)           # ...in red: the colour is set-after
        self.assertEqual(d.fg[1, 4], YELLOW)

    def test_hold_is_set_at(self):
        d = dec((1, bytes([GR, BLOCK, HOLD, BLOCK])))
        self.assertEqual(d.code[1, 2], BLOCK)

    def test_release_is_set_after(self):
        d = dec((1, bytes([GR, HOLD, BLOCK, RELEASE, GY, BLOCK])))
        self.assertEqual(d.code[1, 3], BLOCK)       # release cell still shows the held mosaic
        self.assertEqual(d.code[1, 4], 0x20)        # then holding is off

    def test_held_mosaic_keeps_its_separated_style(self):
        d = dec((1, bytes([GR, HOLD, SEPAR, BLOCK, CONTIG, GY])))
        self.assertEqual(d.code[1, 4], BLOCK)
        self.assertEqual(d.gset[1, 5], charset.SET_SEP)

    def test_mode_change_resets_the_held_mosaic(self):
        # alpha then back to graphics: the held mosaic is gone
        d = dec((1, bytes([GR, HOLD, BLOCK, AY, GG])))
        self.assertEqual(d.code[1, 4], 0x20)

    def test_repeated_graphics_colour_does_not_reset(self):
        d = dec((1, bytes([GR, HOLD, BLOCK, GY, GG])))
        self.assertEqual(d.code[1, 4], BLOCK)

    def test_capitals_do_not_become_the_held_mosaic(self):
        d = dec((1, bytes([GR, HOLD, BLOCK]) + b"A" + bytes([GY])))
        self.assertEqual(d.code[1, 4], BLOCK)

    def test_held_mosaic_shows_only_in_graphics_mode(self):
        d = dec((1, bytes([HOLD, AR])))
        self.assertEqual(d.code[1, 1], 0x20)


class Height(unittest.TestCase):
    def test_double_height_takes_the_row_below(self):
        d = dec((5, bytes([DOUBLE]) + b"AB"), (6, b"hidden"))
        self.assertEqual(d.dh[5, 1], DH_TOP)
        self.assertEqual(d.dh[6, 1], DH_BOTTOM)
        self.assertEqual(d.code[6, 1], ord("A"))
        self.assertTrue(d.hidden_row[6])
        self.assertEqual(d.dh[5, 0], DH_NONE)        # set-after: the code's own cell is normal

    def test_normal_height_cells_leave_spaces_below(self):
        d = dec((5, b"X" + bytes([DOUBLE]) + b"Y"), (6, b"Q"))
        self.assertEqual(d.code[6, 0], 0x20)
        self.assertEqual(d.code[6, 2], ord("Y"))

    def test_lower_row_takes_the_upper_rows_background(self):
        d = dec((5, bytes([AB, NEWBG, DOUBLE]) + b"Y"), (6, b"Q"))
        self.assertEqual(d.bg[6, 1], BLUE)
        self.assertEqual(d.bg[6, 3], BLUE)

    def test_normal_height_is_set_at(self):
        d = dec((5, bytes([DOUBLE]) + b"A" + bytes([NORMAL]) + b"B"))
        self.assertEqual(d.dh[5, 2], DH_NONE)
        self.assertEqual(d.dh[5, 3], DH_NONE)

    def test_no_double_height_in_rows_0_23_24(self):
        for r in (0, 23, 24):
            d = dec((r, bytes([DOUBLE]) + b"A"))
            self.assertEqual(d.dh[r, 1], DH_NONE, r)
        self.assertFalse(dec((23, bytes([DOUBLE]) + b"A")).hidden_row[24])

    def test_rendered_double_height_is_the_glyph_stretched(self):
        d = dec((5, bytes([DOUBLE]) + b"T"))
        idx = render.render_indices(d)
        top = idx[100:120, 12:24] == WHITE
        bot = idx[120:140, 12:24] == WHITE
        g = charset.ALPHA[ord("T")]
        self.assertTrue(np.array_equal(top, np.repeat(g[:10], 2, axis=0)))
        self.assertTrue(np.array_equal(bot, np.repeat(g[10:], 2, axis=0)))


class FlashConcealBox(unittest.TestCase):
    def test_flash_is_set_after_and_steady_set_at(self):
        d = dec((1, bytes([FLASH]) + b"AB" + bytes([STEADY]) + b"C"))
        self.assertFalse(d.flash[1, 0])
        self.assertTrue(d.flash[1, 1])
        self.assertFalse(d.flash[1, 3])

    def test_flash_off_phase_hides_the_foreground_only(self):
        d = dec((1, bytes([AB, NEWBG, FLASH, AY]) + b"X"))
        on = render.render_indices(d, flash_phase_on=True)[20:40, 48:60]
        off = render.render_indices(d, flash_phase_on=False)[20:40, 48:60]
        self.assertTrue((on == YELLOW).any())
        self.assertFalse((off == YELLOW).any())
        self.assertTrue((off == BLUE).all())

    def test_conceal_hides_until_revealed(self):
        d = dec((1, bytes([CONCEAL]) + b"S"))
        self.assertFalse((render.render_indices(d)[20:40, 12:24] == WHITE).any())
        self.assertTrue((render.render_indices(d, reveal=True)[20:40, 12:24] == WHITE).any())

    def test_a_colour_code_ends_conceal(self):
        d = dec((1, bytes([CONCEAL]) + b"S" + bytes([AG]) + b"V"))
        self.assertTrue(d.conceal[1, 2])           # set-after: the colour cell is still concealed
        self.assertFalse(d.conceal[1, 3])

    def test_box_and_the_newsflash_view(self):
        d = dec((3, b"out" + bytes([START_BOX, START_BOX]) + b"in" + bytes([END_BOX, END_BOX]) + b"out"))
        self.assertFalse(d.box[3, 0])
        self.assertTrue(d.box[3, 5])
        self.assertFalse(d.box[3, 9])
        mixed = render.render_indices(d, mixed=True)
        self.assertTrue((mixed[60:80, 0:36] == 8).all())          # outside: the picture
        self.assertTrue((mixed[60:80, 60:84] == WHITE).any())     # inside: the text


class SamplePage(unittest.TestCase):
    """The engineering test page is the known case for the whole set: its
    rows are built to show one attribute each."""

    def setUp(self):
        self.d = decoder.decode(test_page().subpages[0].rows)

    def test_colour_bars_are_seven_solid_colours(self):
        idx = render.render_indices(self.d)
        y = 3 * 20 + 10
        seen = [int(idx[y, 12 * (5 * i + 2) + 6]) for i in range(7)]
        self.assertEqual(seen, [WHITE, YELLOW, CYAN, GREEN, 5, RED, BLUE])

    def test_hold_row_has_no_gaps(self):
        r = 12
        blocks = [c for c in range(40) if self.d.code[r, c] == BLOCK]
        self.assertEqual(blocks, list(range(blocks[0], blocks[0] + len(blocks))))

    def test_row_below_double_height_is_hidden(self):
        self.assertTrue(self.d.hidden_row[10])


if __name__ == "__main__":
    unittest.main()
