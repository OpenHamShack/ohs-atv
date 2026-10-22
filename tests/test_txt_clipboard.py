"""Blocks of cells: selecting, copying, cutting and pasting them, the warning
when a paste repaints cells outside itself, and every clipboard form -
our own, plain text, edit.tf link and picture - going out and coming back."""
import io
import unittest

import numpy as np

from helpers import page_rows

from ohstxt import clipboard, edittf
from ohstxt.editor import Block, Document
from ohstxt.page import Page, Subpage
from ohstxt.samples import AR, AW, AY, DOUBLE, GR, NEWBG


def doc_with(*rows):
    return Document(Page(subpages=[Subpage(rows=page_rows(*rows))]))


class Selecting(unittest.TestCase):
    def test_selection_is_normalised_whichever_way_it_was_dragged(self):
        d = doc_with()
        d.select(8, 20, 3, 5)
        self.assertEqual(d.selection(), (3, 5, 8, 20))
        self.assertEqual((d.row, d.col), (3, 5))

    def test_shift_arrows_grow_from_the_cursor(self):
        d = doc_with()
        d.goto(4, 4)
        d.extend_selection(0, 1)
        d.extend_selection(1, 0)
        self.assertEqual(d.selection(), (4, 4, 5, 5))
        d.clear_selection()
        self.assertIsNone(d.selection())

    def test_copy_takes_exactly_the_rectangle(self):
        d = doc_with((3, b"abcdef"), (4, b"ghijkl"))
        d.select(3, 1, 4, 3)
        blk = d.copy_block()
        self.assertEqual(blk.rows, [b"bcd", b"hij"])
        self.assertEqual((blk.top, blk.left, blk.width, blk.height), (3, 1, 3, 2))


class CutAndPaste(unittest.TestCase):
    def test_cut_blanks_and_paste_puts_back_one_undo_step_each(self):
        d = doc_with((3, b"abcdef"), (4, b"ghijkl"))
        d.select(3, 1, 4, 3)
        blk = d.cut_block()
        self.assertEqual(bytes(d.sub.rows[3][:6]), b"a   ef")
        d.paste_block(blk, 10, 30)
        self.assertEqual(bytes(d.sub.rows[10][30:33]), b"bcd")
        self.assertEqual(bytes(d.sub.rows[11][30:33]), b"hij")
        self.assertEqual(d.selection(), (10, 30, 11, 32))       # the pasted area is selected
        d.undo()
        self.assertEqual(bytes(d.sub.rows[10][30:33]), b"   ")
        d.undo()
        self.assertEqual(bytes(d.sub.rows[3][:6]), b"abcdef")

    def test_paste_off_the_edge_is_clipped_not_wrapped(self):
        d = doc_with()
        rep = d.paste_block(Block([b"0123456789"], 1, 0), 5, 35)
        self.assertEqual(bytes(d.sub.rows[5][35:]), b"01234")
        self.assertEqual(bytes(d.sub.rows[6][:5]), b"     ")
        self.assertEqual(rep.clipped, 5)

    def test_paste_never_writes_the_receivers_header_cells(self):
        d = doc_with()
        d.paste_block(Block([b"XXXXXXXXXXXX", b"YYYYYYYYYYYY"]), 0, 0)
        self.assertEqual(bytes(d.sub.rows[0][:8]), b" " * 8)
        self.assertEqual(bytes(d.sub.rows[0][8:12]), b"XXXX")
        self.assertEqual(bytes(d.sub.rows[1][:12]), b"Y" * 12)

    def test_ragged_text_lines_leave_what_is_beside_them(self):
        d = doc_with((2, b"..........."), (3, b"..........."))
        d.paste_block(clipboard.text_to_block("ab\nabcdef"), 2, 0)
        self.assertEqual(bytes(d.sub.rows[2][:8]), b"ab......")
        self.assertEqual(bytes(d.sub.rows[3][:8]), b"abcdef..")


class AttributeWarning(unittest.TestCase):
    def test_plain_text_into_plain_text_says_nothing(self):
        d = doc_with((5, b"hello world"))
        rep = d.paste_block(Block([b"HELLO"]), 5, 0)
        self.assertEqual(rep.message(), "")

    def test_a_colour_code_that_carries_past_the_block_is_reported(self):
        d = doc_with((5, b"one two three"))
        rep = d.paste_block(Block([bytes([AR]) + b"x"]), 5, 0)
        self.assertEqual(sorted(rep.changed_outside), [5])
        self.assertEqual(rep.changed_outside[5], 9)              # "e two three" turned red; spaces look the same
        self.assertIn("carry on past its edge", rep.message())

    def test_a_colour_change_over_blank_cells_is_not_reported(self):
        d = doc_with((5, b"word"))
        rep = d.paste_block(Block([bytes([AR])]), 5, 10)
        self.assertEqual(rep.message(), "")

    def test_a_colour_code_restored_inside_the_block_is_not_reported(self):
        d = doc_with((5, b"one two three"))
        rep = d.paste_block(Block([bytes([AR]) + b"x" + bytes([AW])]), 5, 0)
        self.assertEqual(rep.message(), "")

    def test_covering_a_code_the_row_relied_on_is_reported(self):
        d = doc_with((5, bytes([AY]) + b"yellow text"))
        rep = d.paste_block(Block([b" "]), 5, 0)
        self.assertIn("covered control codes", rep.message())

    def test_double_height_reaching_the_row_below_is_reported(self):
        d = doc_with((6, b"row below has words"))
        rep = d.paste_block(Block([bytes([DOUBLE]) + b"BIG"]), 5, 0)
        self.assertIn(6, rep.changed_outside)

    def test_a_background_block_inside_a_coloured_row(self):
        # new background is Set-At: a block of [GR NB] repaints what follows
        d = doc_with((7, b"          text"))
        rep = d.paste_block(Block([bytes([GR, NEWBG])]), 7, 2)
        self.assertGreater(rep.changed_outside.get(7, 0), 0)


class Forms(unittest.TestCase):
    def setUp(self):
        self.blk = Block([bytes([AR]) + b"Red #3", b"\x7b\x7d half"], top=4, left=6)

    def test_our_own_form_is_lossless(self):
        back = clipboard.block_from_json(clipboard.block_to_json(self.blk))
        self.assertEqual((back.rows, back.top, back.left), (self.blk.rows, 4, 6))

    def test_our_own_form_refuses_garbage(self):
        self.assertIsNone(clipboard.block_from_json(b"not json"))
        self.assertIsNone(clipboard.block_from_json(b'{"rows": ["zz"]}'))

    def test_text_shows_the_english_set_and_spaces_for_codes(self):
        self.assertEqual(clipboard.block_to_text(self.blk), " Red £3\n¼¾ half")     # 0x23 is £ on the English set

    def test_text_back_to_codes(self):
        blk = clipboard.text_to_block("£5 ½\r\nnext\tline ☃\n")
        self.assertEqual(blk.rows, [b"#5 \x5c", b"next line ?"])

    def test_edittf_link_holds_the_block_where_it_was(self):
        url = clipboard.block_to_edittf(self.blk, 0x123)
        hp = edittf.parse(url)
        self.assertEqual(bytes(hp.rows[4][6:13]), self.blk.rows[0])
        self.assertEqual(hp.fields["PN"], "123")
        back = clipboard.edittf_to_block(url)
        self.assertEqual((back.top, back.left), (4, 6))
        self.assertEqual(back.rows[0], self.blk.rows[0])
        self.assertEqual(back.rows[1][:7], self.blk.rows[1])

    def test_plain_text_with_a_hash_is_not_a_link(self):
        self.assertIsNone(clipboard.edittf_to_block("Page #1: the news"))

    def test_picture_is_the_cells_as_decoded_in_their_row(self):
        from PIL import Image
        # a mosaic block in a row whose graphics colour is to its left: alone
        # it would be letters, in its row it is red blocks
        rows = page_rows((5, bytes([GR]) + b"\x7f\x7f\x7f"))
        rgb = clipboard.block_image(rows, (5, 1, 5, 3), scale=1)
        self.assertEqual(rgb.shape[0], 20)
        self.assertEqual(rgb.shape[1], round(36 * 1.2))
        self.assertTrue((rgb == (255, 0, 0)).all(-1).mean() > 0.9)
        im = Image.open(io.BytesIO(clipboard.png_bytes(rgb)))
        self.assertEqual(im.size, (rgb.shape[1], rgb.shape[0]))
        self.assertTrue(np.array_equal(np.asarray(im.convert("RGB")), rgb))


if __name__ == "__main__":
    unittest.main()
