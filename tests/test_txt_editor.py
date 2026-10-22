"""The editing model: type-over and the cursor, undo and redo, subpages,
Fastext labels, and the sixel rules - what is drawn, what colour codes go
in, and what is refused rather than repainting the row."""
import unittest

from helpers import page_rows

from ohstxt import decoder, editor
from ohstxt.editor import PEN_KEEP, Document
from ohstxt.page import Page, Subpage
from ohstxt.samples import AW, GR, GY


def doc_with(*rows):
    return Document(Page(subpages=[Subpage(rows=page_rows(*rows))]))


def lit(doc, sx, sy):
    return doc.sixel_on(sx, sy)


class Typing(unittest.TestCase):
    def test_type_over_and_advance(self):
        d = doc_with((3, b"xxxxx"))
        d.goto(3, 1)
        d.type_text("AB")
        self.assertEqual(bytes(d.sub.rows[3][:5]), b"xABxx")
        self.assertEqual((d.row, d.col), (3, 3))

    def test_pound_and_hash_map_to_the_english_set(self):
        d = doc_with()
        d.goto(1, 0)
        d.type_text("£#")
        self.assertEqual(bytes(d.sub.rows[1][:2]), b"\x23\x5f")

    def test_ascii_brackets_land_where_the_set_keeps_them(self):
        d = doc_with()
        d.goto(1, 0)
        d.type_text("{[|]}~^")
        self.assertEqual(bytes(d.sub.rows[1][:7]), b"{[|]}~^")

    def test_wraps_to_the_next_row(self):
        d = doc_with()
        d.goto(1, 39)
        d.type_text("ab")
        self.assertEqual(d.sub.rows[1][39], ord("a"))
        self.assertEqual(d.sub.rows[2][0], ord("b"))

    def test_header_columns_0_to_7_are_not_editable(self):
        d = doc_with()
        d.goto(0, 0)
        self.assertEqual(d.col, 8)
        d.type_text("X")
        self.assertEqual(d.sub.rows[0][8], ord("X"))
        self.assertEqual(bytes(d.sub.rows[0][:8]), b" " * 8)

    def test_insert_and_delete_cells_and_rows(self):
        d = doc_with((2, b"abc"), (3, b"def"))
        d.goto(2, 0)
        d.insert_cell()
        self.assertEqual(bytes(d.sub.rows[2][:4]), b" abc")
        d.delete_cell()
        self.assertEqual(bytes(d.sub.rows[2][:3]), b"abc")
        d.insert_row()
        self.assertEqual(bytes(d.sub.rows[3][:3]), b"abc")
        d.delete_row()
        self.assertEqual(bytes(d.sub.rows[2][:3]), b"abc")
        self.assertEqual(bytes(d.sub.rows[3][:3]), b"def")


class Undo(unittest.TestCase):
    def test_undo_and_redo_every_step(self):
        d = doc_with()
        d.goto(1, 0)
        d.type_text("abc")
        self.assertTrue(d.undo())
        self.assertEqual(bytes(d.sub.rows[1][:3]), b"ab ")
        self.assertEqual((d.row, d.col), (1, 2))
        d.undo()
        d.undo()
        self.assertFalse(d.undo())
        self.assertEqual(bytes(d.sub.rows[1][:3]), b"   ")
        d.redo()
        d.redo()
        self.assertEqual(bytes(d.sub.rows[1][:3]), b"ab ")

    def test_a_new_change_clears_redo(self):
        d = doc_with()
        d.type_text("a")
        d.undo()
        d.type_text("b")
        self.assertFalse(d.redo())

    def test_a_stroke_is_one_undo_step(self):
        d = doc_with((5, bytes([GR])))
        d.line(2, 15, 20, 15)
        d.undo()
        self.assertEqual(bytes(d.sub.rows[5][:12]), bytes([GR]) + b" " * 11)

    def test_subpage_operations_undo(self):
        d = doc_with((1, b"one"))
        d.add_subpage(copy_current=True)
        self.assertEqual(len(d.page.subpages), 2)
        self.assertEqual([s.subcode for s in d.page.subpages], [1, 2])
        d.undo()
        self.assertEqual(len(d.page.subpages), 1)
        self.assertEqual(d.page.subpages[0].subcode, 0)


class Sixels(unittest.TestCase):
    def test_draw_in_a_graphics_run(self):
        d = doc_with((5, bytes([GR])))
        st = editor.Stroke()
        self.assertTrue(d.set_sixel(2, 15, True, PEN_KEEP, st))
        self.assertTrue(lit(d, 2, 15))
        self.assertEqual(d.sub.rows[5][1], 0x21)
        d.set_sixel(3, 17, True)
        self.assertEqual(d.sub.rows[5][1], 0x21 | 0x40)
        d.set_sixel(2, 15, False)
        self.assertEqual(d.sub.rows[5][1], 0x60)

    def test_control_cell_is_refused_with_a_reason(self):
        d = doc_with((5, bytes([GR])))
        st = editor.Stroke()
        self.assertFalse(d.set_sixel(0, 15, True, PEN_KEEP, st))
        self.assertEqual(st.refused, 1)
        self.assertIn("control code", st.message())

    def test_alpha_cell_gets_a_graphics_code_in_the_blank_to_its_left(self):
        d = doc_with()
        d.set_sixel(10, 15, True, 3)
        row = d.sub.rows[5]
        self.assertEqual(row[4], 0x13)               # graphics yellow, one cell left of column 5
        self.assertTrue(lit(d, 10, 15))
        dec = decoder.decode(d.sub.rows)
        self.assertEqual(dec.fg[5, 5], 3)

    def test_first_column_cannot_be_given_a_code(self):
        d = doc_with()
        st = editor.Stroke()
        self.assertFalse(d.set_sixel(0, 15, True, 3, st))
        self.assertIn("first cell", st.message())

    def test_a_code_that_would_turn_following_text_into_mosaics_is_refused(self):
        # "ab" right after the cell: putting graphics there would make them mosaics
        d = doc_with((5, b"    ab"))
        st = editor.Stroke()
        self.assertFalse(d.set_sixel(6, 15, True, 1, st))
        self.assertIn("rest of the row", st.message())
        self.assertEqual(bytes(d.sub.rows[5][:6]), b"    ab")

    def test_the_old_attributes_are_restored_after_when_there_is_room(self):
        # text two cells on: a restoring alpha code goes in the blank between
        d = doc_with((5, b"       hello"))
        d.set_sixel(8, 15, True, 1)
        dec = decoder.decode(d.sub.rows)
        self.assertEqual(d.sub.rows[5][3], 0x11)
        self.assertEqual(d.sub.rows[5][5], 0x07)       # alpha white puts the text back as it was
        self.assertFalse(dec.graphics[5, 7])
        self.assertEqual(bytes(d.sub.rows[5][7:12]), b"hello")

    def test_a_different_pen_colour_recolours_one_cell_not_the_run(self):
        d = doc_with((5, bytes([GR, 0x7F, 0x20, 0x20, 0x20, 0x7F])))
        d.set_sixel(6, 15, True, 3)                     # cell 3 in yellow; cell 2 takes the code
        dec = decoder.decode(d.sub.rows)
        self.assertEqual(d.sub.rows[5][2], 0x13)
        self.assertEqual(d.sub.rows[5][4], 0x11)        # graphics red again in the blank after
        self.assertEqual(dec.fg[5, 3], 3)
        self.assertEqual(dec.fg[5, 1], 1)
        self.assertEqual(dec.fg[5, 5], 1)

    def test_no_room_to_restore_the_run_is_refused(self):
        d = doc_with((5, bytes([GR, 0x7F, 0x20, 0x20, 0x7F])))
        before = bytes(d.sub.rows[5])
        st = editor.Stroke()
        self.assertFalse(d.set_sixel(6, 15, True, 3, st))
        self.assertEqual(bytes(d.sub.rows[5]), before)

    def test_rect_and_line(self):
        d = doc_with(*[(r, bytes([GY])) for r in range(2, 8)])
        d.rect(2, 6, 9, 14)
        self.assertTrue(lit(d, 2, 6) and lit(d, 9, 14) and lit(d, 5, 6))
        self.assertFalse(lit(d, 5, 10))
        d.rect(2, 6, 9, 14, filled=True)
        self.assertTrue(lit(d, 5, 10))
        d.line(2, 20, 9, 20, on=True)
        self.assertTrue(all(lit(d, x, 20) for x in range(2, 10)))

    def test_fill_stops_at_control_cells_and_lit_sixels(self):
        d = doc_with((5, bytes([GY]) + b" " * 4 + bytes([GR]) + b" " * 4))
        st = d.fill(2, 15)
        self.assertTrue(all(lit(d, x, y) for x in range(2, 10) for y in range(15, 18)))
        self.assertFalse(lit(d, 12, 15))                # beyond the GR code cell
        self.assertGreater(st.changed, 0)

    def test_fill_in_alpha_says_why_not(self):
        d = doc_with()
        st = d.fill(10, 15)
        self.assertEqual(st.changed, 0)
        self.assertIn("no mosaic", st.message())


class Fastext(unittest.TestCase):
    def test_labels_round_trip_through_row_24(self):
        d = doc_with()
        d.set_fastext_labels([(1, "News"), (2, "Sport"), (3, "Weather"), (6, "Index")])
        row = d.sub.rows[24]
        self.assertEqual(row[0], 1)
        self.assertEqual(row[10], 2)
        self.assertEqual(bytes(row[1:5]), b"News")
        self.assertEqual(editor.fastext_labels(row), [(1, "News"), (2, "Sport"), (3, "Weather"), (6, "Index")])

    def test_a_row_24_of_its_own_is_not_mistaken_for_labels(self):
        self.assertIsNone(editor.fastext_labels(bytearray(bytes([AW]) + b"x" * 39)))

    def test_links(self):
        d = doc_with()
        d.set_link(0, 0x200)
        self.assertTrue(d.sub.has_links)
        d.set_link(0, None)
        self.assertFalse(d.sub.has_links)


if __name__ == "__main__":
    unittest.main()
