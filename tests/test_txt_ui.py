"""The window, driven offscreen with real key presses and mouse clicks on a
throw-away service folder: it opens on the sample service, typing and the
Esc codes change the page, the pen draws sixels, undo undoes, the panels
change what they say, the preview animates flash, and save, PNG and image
import do what they should to files on disk."""
import atexit
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

from helpers import page_rows  # noqa: F401  (path setup)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TMP = tempfile.mkdtemp(prefix="ohstxt-uitest-")
os.environ["OHSTXT_UI_CONF"] = str(Path(_TMP) / "ui.json")
os.environ["OHSTXT_DATA"] = str(Path(_TMP) / "data")
# Removed at exit, as test_ui.py's are: the variables outlive any one test.
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)

try:
    from PyQt6.QtCore import QPoint, Qt
    from PyQt6.QtTest import QTest
    HAVE_QT = True
except ImportError:
    HAVE_QT = False

from ohstxt import render, tti


@unittest.skipUnless(HAVE_QT, "PyQt6 is not installed")
class UiBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ohstxt.ui import make_app
        cls.app = make_app(["test"])

    def make(self):
        from ohstxt.ui import build
        from ohstxt.ui.prefs import Prefs
        tmp = Path(tempfile.mkdtemp(prefix="svc-", dir=_TMP))
        self.folder = tmp / "service"
        self.prefs = Prefs(tmp / "ui.json")
        self.win = build(self.prefs, self.folder)
        self.win.ask_before_discard = False
        self.win.resize(1600, 1000)
        self.win.show()
        self.addCleanup(self.win.close)
        self.pump(0.2)
        return self.win

    def pump(self, sec=0.05):
        end = time.monotonic() + sec
        while time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.005)

    def goto(self, number):
        for i, pg in enumerate(self.win.service.pages):
            if pg.number == number:
                self.win.select_page(i)
                self.pump()
                return pg
        raise KeyError(number)

    def click_cell(self, r, c, button=Qt.MouseButton.LeftButton, sixel=None):
        cv = self.win.canvas
        if sixel is None:
            pt = cv.cell_rect(r, c).center()
        else:
            pt = cv.sixel_rect(*sixel).center()
        QTest.mouseClick(cv, button, Qt.KeyboardModifier.NoModifier, QPoint(int(pt.x()), int(pt.y())))
        self.pump()


class Opens(UiBase):
    def test_first_run_creates_the_sample_service(self):
        win = self.make()
        self.assertEqual([p.number for p in win.service.pages], [0x100, 0x150, 0x199, 0x300])
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()),
                         ["P100.tti", "P150.tti", "P199.tti", "P300.tti"])
        self.assertEqual(win.services.list.count(), 4)

    def test_picking_a_page_in_the_list_opens_it(self):
        win = self.make()
        win.services.list.setCurrentRow(2)
        self.pump()
        self.assertEqual(win.doc.page.number, 0x199)
        self.assertIn("P199", win.mark.sub)


class Keyboard(UiBase):
    def test_typing_and_undo(self):
        win = self.make()
        self.goto(0x199)
        win.doc.goto(15, 0)
        win.canvas.setFocus()
        QTest.keyClicks(win.canvas, "Hi!")
        self.pump()
        self.assertEqual(bytes(win.doc.sub.rows[15][:3]), b"Hi!")
        self.assertTrue(win.doc.dirty)
        QTest.keyClick(win.canvas, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.pump()
        self.assertEqual(bytes(win.doc.sub.rows[15][:3]), b"Hi ")

    def test_esc_then_a_key_puts_a_control_code(self):
        win = self.make()
        win.doc.goto(10, 0)
        win.canvas.setFocus()
        QTest.keyClick(win.canvas, Qt.Key.Key_Escape)
        QTest.keyClicks(win.canvas, "R")
        QTest.keyClick(win.canvas, Qt.Key.Key_Escape)
        QTest.keyClicks(win.canvas, "n")
        self.pump()
        self.assertEqual(bytes(win.doc.sub.rows[10][:2]), b"\x11\x1d")
        d = win.canvas.decoded()
        self.assertEqual(int(d.bg[10, 1]), 1)          # the new background is red, set-at

    def test_page_down_changes_subpage(self):
        win = self.make()
        self.goto(0x300)
        win.canvas.setFocus()
        QTest.keyClick(win.canvas, Qt.Key.Key_PageDown)
        self.pump()
        self.assertEqual(win.doc.sub_index, 1)
        self.assertEqual(win.subpanel.list.currentRow(), 1)


class Mouse(UiBase):
    def test_pen_draws_and_right_button_erases(self):
        win = self.make()
        self.goto(0x199)
        win.set_tool("pen")
        # row 11 "No hold:" has GR at column 9 then a block; column 12 is GY...
        # use a fresh row instead: put a graphics colour code in row 15
        win.doc.goto(15, 0)
        win.insert_code(0x16)
        self.click_cell(15, 3, sixel=(6, 45))
        self.assertTrue(win.doc.sixel_on(6, 45))
        self.click_cell(15, 3, button=Qt.MouseButton.RightButton, sixel=(6, 45))
        self.assertFalse(win.doc.sixel_on(6, 45))

    def test_drawing_on_a_control_cell_warns(self):
        win = self.make()
        self.goto(0x199)
        win.set_tool("pen")
        self.click_cell(3, 0, sixel=(0, 9))            # row 3 col 0 is a graphics colour code
        self.assertIn("control code", win.status.message[0])

    def test_rect_tool_drag(self):
        win = self.make()
        win.doc.goto(20, 0)                            # a blank row of P100
        win.insert_code(0x13)
        win.set_tool("rect")
        cv = win.canvas
        a = cv.sixel_rect(4, 60).center()
        b = cv.sixel_rect(12, 62).center()
        QTest.mousePress(cv, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(int(a.x()), int(a.y())))
        QTest.mouseMove(cv, QPoint(int(b.x()), int(b.y())))
        QTest.mouseRelease(cv, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                           QPoint(int(b.x()), int(b.y())))
        self.pump()
        self.assertTrue(win.doc.sixel_on(4, 60) and win.doc.sixel_on(12, 62) and win.doc.sixel_on(8, 60))
        self.assertFalse(win.doc.sixel_on(8, 61))       # an outline, not filled


class Clipboard(UiBase):
    def drag(self, a, b, mods=None):
        cv = self.win.canvas
        m = mods or Qt.KeyboardModifier.NoModifier
        pa, pb = cv.cell_rect(*a).center(), cv.cell_rect(*b).center()
        QTest.mousePress(cv, Qt.MouseButton.LeftButton, m, QPoint(int(pa.x()), int(pa.y())))
        QTest.mouseMove(cv, QPoint(int(pb.x()), int(pb.y())))
        QTest.mouseRelease(cv, Qt.MouseButton.LeftButton, m, QPoint(int(pb.x()), int(pb.y())))
        self.pump()

    def keys(self, key, mods=Qt.KeyboardModifier.ControlModifier):
        QTest.keyClick(self.win.canvas, key, mods)
        self.pump()

    def test_drag_select_copy_and_paste_with_the_keyboard(self):
        win = self.make()
        self.goto(0x100)
        self.drag((6, 1), (7, 6))
        self.assertEqual(win.doc.selection(), (6, 1, 7, 6))
        self.keys(Qt.Key.Key_C)
        win.doc.clear_selection()
        win.doc.goto(15, 30)
        self.keys(Qt.Key.Key_V)
        self.assertEqual(bytes(win.doc.sub.rows[15][30:36]), bytes(win.doc.sub.rows[6][1:7]))
        self.assertEqual(bytes(win.doc.sub.rows[16][30:36]), bytes(win.doc.sub.rows[7][1:7]))
        self.keys(Qt.Key.Key_Z)
        self.assertEqual(bytes(win.doc.sub.rows[15][30:36]), b" " * 6)

    def test_shift_arrows_then_cut_and_delete(self):
        win = self.make()
        self.goto(0x199)
        win.doc.goto(5, 0)
        win.canvas.setFocus()
        for _ in range(4):
            self.keys(Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(win.doc.selection(), (5, 0, 5, 4))
        self.keys(Qt.Key.Key_X)
        self.assertEqual(bytes(win.doc.sub.rows[5][:5]), b"     ")
        from PyQt6.QtWidgets import QApplication
        self.assertEqual(QApplication.clipboard().text(), "white")
        self.keys(Qt.Key.Key_Z)
        win.doc.select(8, 0, 8, 3)
        self.keys(Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier)
        self.assertEqual(bytes(win.doc.sub.rows[8][:4]), b"    ")

    def test_pasting_plain_text_from_elsewhere(self):
        from PyQt6.QtWidgets import QApplication
        win = self.make()
        self.goto(0x150)
        QApplication.clipboard().setText("Line one\nLine two")
        win.doc.goto(3, 2)
        win.paste()
        self.assertEqual(win.doc.sub.text(3)[2:10], "Line one")
        self.assertEqual(win.doc.sub.text(4)[2:10], "Line two")

    def test_paste_that_repaints_the_row_warns(self):
        from PyQt6.QtWidgets import QApplication
        win = self.make()
        self.goto(0x199)
        win.doc.select(5, 5, 5, 8)                     # AR "red": a colour code and text
        win.copy()
        win.doc.clear_selection()
        win.doc.goto(16, 5)                            # into the character set row: the rest turns red
        win.paste()
        self.assertIn("Paste changes how", win.status.message[0])
        self.assertEqual(win.status.message[1], "warn")
        self.assertIn("Paste changes", win.cellinfo.warn)
        self.assertTrue(QApplication.clipboard().mimeData().hasText())

    def test_copy_as_link_and_picture(self):
        from PyQt6.QtWidgets import QApplication
        from ohstxt import edittf
        win = self.make()
        self.goto(0x199)
        win.doc.select(3, 0, 4, 9)
        win.copy_link()
        hp = edittf.parse(QApplication.clipboard().text())
        self.assertEqual(bytes(hp.rows[3][:10]), bytes(win.doc.sub.rows[3][:10]))
        self.assertEqual(bytes(hp.rows[5]), b" " * 40)
        win.copy_picture()
        img = QApplication.clipboard().image()
        self.assertEqual((img.width(), img.height()), (round(10 * 12 * 1.2 * 2), 2 * 20 * 2))


class ServiceCheck(UiBase):
    def test_findings_are_listed_and_a_click_goes_to_the_cell(self):
        win = self.make()
        dlg = win.check_service()
        self.pump()
        self.assertTrue(dlg.isVisible())
        kinds = [dlg.list.item(i).data(Qt.ItemDataRole.UserRole).kind for i in range(dlg.list.count())]
        self.assertIn("link", kinds)
        self.assertIn("double-height", kinds)       # the test page hides row 10 on purpose
        i = kinds.index("double-height")
        it = dlg.list.item(i)
        rect = dlg.list.visualItemRect(it)
        dlg.list.scrollToItem(it)
        self.pump()
        rect = dlg.list.visualItemRect(it)
        QTest.mouseClick(dlg.list.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         rect.center())
        self.pump()
        self.assertEqual(win.doc.page.number, 0x199)
        self.assertEqual((win.doc.row, win.doc.col), (10, 0))

    def test_a_carousel_finding_goes_to_its_subpage(self):
        from ohstxt import check
        win = self.make()
        win.jump_to(check.Finding("error", "link", "x", page=0x300, sub=2, row=24, col=21))
        self.assertEqual((win.doc.page.number, win.doc.sub_index, win.doc.row, win.doc.col), (0x300, 2, 24, 21))
        self.assertEqual(win.subpanel.list.currentRow(), 2)

    def test_station_settings_reach_the_checker(self):
        from ohstxt import station
        win = self.make()
        st = station.load(self.folder)
        st.callsign = "M0TXT"
        station.save(st, self.folder)
        win.station = station.load(self.folder)
        rep = win.run_check()
        self.assertIn("M0TXT is not in", " ".join(f.message for f in rep.findings))
        self.goto(0x100)
        win.doc.goto(0, 9)
        win.doc.type_text("M0TXT")
        rep = win.run_check()
        self.assertNotIn("station-id", [f.kind for f in rep.findings])

    def test_save_all_runs_the_checker_first(self):
        win = self.make()
        self.goto(0x100)
        win.doc.type_text("x")
        win.save_all()
        self.assertIsNotNone(win.check_dlg)
        self.assertTrue(win.check_dlg.isVisible())
        self.assertGreater(win.check_dlg.report.count("error"), 0)
        self.assertFalse(win.doc.dirty)                # tests say "save anyway"

    def test_the_station_dialog_writes_station_json(self):
        from ohstxt import station
        from ohstxt.ui.servicedlg import StationDialog
        win = self.make()
        dlg = StationDialog(station.load(self.folder), win)
        dlg.callsign.setText("g4abc")
        dlg.lat.setValue(51.5)
        dlg._ok()
        station.save(dlg.st, self.folder)
        st = station.load(self.folder)
        self.assertEqual((st.callsign, st.lat, st.lon), ("G4ABC", 51.5, None))


class LivePages(UiBase):
    def test_mark_live_insert_a_field_preview_save_and_reopen(self):
        from ohstxt import live
        win = self.make()
        self.goto(0x150)
        win.page_panel.live.setChecked(True)
        self.pump()
        self.assertIsNotNone(win.doc.page.live)
        win.doc.goto(5, 2)
        win.doc.type_text("SFI ")
        win.insert_live_field("sfi", 5)
        self.assertEqual(bytes(win.doc.sub.rows[5][2:11]), b"SFI {sfi}")
        win.doc.goto(5, 7)
        win._cell()
        self.assertIn("live {sfi} 5 cells, solar: '162'", win.cellinfo.text)
        # the preview fills it (sample data: the runner has never run here)
        rows = win.board.render_rows(win.doc.page, 0, [bytearray(r) for r in win.doc.sub.rows])
        self.assertEqual(bytes(rows[5][2:11]), b"SFI 162  ")
        win.save_page()
        sc = live.load_sidecar(self.folder / "P150.live.json")
        self.assertEqual(sc.subpages[0][5].rstrip(), "  SFI {sfi}")
        on_disk = tti.load(self.folder / "P150.tti").subpages[0].text(5)
        self.assertEqual(on_disk[2:11], "SFI --   ")             # never sample data on the air
        win.open_service(self.folder)
        self.goto(0x150)
        self.assertIsNotNone(win.doc.page.live)
        self.assertEqual(bytes(win.doc.sub.rows[5][2:11]), b"SFI {sfi}")
        win.page_panel.live.setChecked(False)
        win.save_page()
        self.assertFalse((self.folder / "P150.live.json").exists())

    def test_marking_live_is_undone_like_any_edit(self):
        win = self.make()
        win.set_live(True)
        self.assertIsNotNone(win.doc.page.live)
        win.undo()
        self.assertIsNone(win.doc.page.live)
        self.assertFalse(win.page_panel.live.isChecked())

    def test_the_insert_menu_lists_sources_and_fields(self):
        win = self.make()
        from PyQt6.QtWidgets import QMenu
        m = QMenu()
        win.live_field_menu(m)
        titles = [a.text() for a in m.actions()]
        self.assertTrue(any(t.startswith("solar") for t in titles))
        solar = next(a.menu() for a in m.actions() if a.text().startswith("solar"))
        self.assertTrue(any(a.text().startswith("{sfi}") for a in solar.actions()))


class Templates(UiBase):
    def test_new_page_from_a_template_uses_the_station(self):
        from ohstxt import station
        from ohstxt.ui.servicedlg import TemplateDialog
        win = self.make()
        win.station = station.Station(callsign="G4TPL", qth="Testville")
        dlg = TemplateDialog(win.station, {p.number for p in win.service.pages}, win)
        self.assertEqual(dlg.list.count(), 8)
        dlg.list.setCurrentRow(4)                        # test card: P199 is taken; P19A is no page to offer
        self.assertEqual(dlg.number.text(), "198")
        dlg.list.setCurrentRow(0)                        # station index: P100 is taken, so P101
        self.assertEqual(dlg.number.text(), "101")
        dlg._ok()
        win.add_page(dlg.page)
        self.assertEqual(win.doc.page.number, 0x101)
        self.assertIn("G4TPL", win.doc.sub.text(0))
        self.assertTrue(win.doc.dirty)

    def test_a_live_template_arrives_live(self):
        from ohstxt.ui.servicedlg import TemplateDialog
        win = self.make()
        dlg = TemplateDialog(win.station, set(), win)
        keys = [dlg.list.item(i).data(Qt.ItemDataRole.UserRole).key for i in range(dlg.list.count())]
        dlg.list.setCurrentRow(keys.index("propagation"))
        dlg._ok()
        win.add_page(dlg.page)
        self.assertIsNotNone(win.doc.page.live)
        self.assertTrue(win.page_panel.live.isChecked())
        win.save_page()
        self.assertTrue((self.folder / "P500.live.json").is_file())


class Received(UiBase):
    def test_a_capture_with_damage_is_shown_marked_and_imported(self):
        from ohstxt import samples, t42
        win = self.make()
        pg = samples.test_page()
        pg.number = 0x777
        data = bytearray(t42.dumps([samples.news_index(), pg]))
        n_p100 = len(t42.page_packets(samples.news_index()))
        data[(n_p100 + 5) * 42 + 2 + 7] ^= 0x02           # P777 row 5 col 7: one bit, only copy
        cap = Path(_TMP) / "rx.t42"
        cap.write_bytes(bytes(data))
        from ohstxt.ui.receivedlg import ReceiveDialog
        dlg = ReceiveDialog(str(cap), {p.number for p in win.service.pages}, win)
        states = {dlg.list.item(i).data(Qt.ItemDataRole.UserRole).number: dlg.list.item(i).checkState()
                  for i in range(dlg.list.count())}
        self.assertEqual(states[0x100], Qt.CheckState.Unchecked)       # P100 is taken
        self.assertEqual(states[0x777], Qt.CheckState.Unchecked)       # damaged
        for i in range(dlg.list.count()):
            dlg.list.item(i).setCheckState(Qt.CheckState.Checked)
        pages, moved = dlg.chosen()
        self.assertEqual(moved, [(0x100, 0x101)])
        win.import_received(dlg)
        self.assertEqual(win.doc.page.number, 0x101)
        self.assertIn("P100 became P101", win.status.message[0])
        self.goto(0x777)
        self.assertEqual(win.doc.sub.damaged, {(5, 7)})
        self.assertTrue(win.doc.dirty)


class Subtitles(UiBase):
    SRT = ("1\n00:00:01,000 --> 00:00:03,000\n<font color=\"yellow\">Who is there?</font>\n\n"
           "2\n00:00:03,500 --> 00:00:05,000\nOnly the wind.\n<font color=\"#00ffff\">Or is it?</font>\n")

    def test_import_edit_preview_export_and_undo(self):
        from ohstxt import decoder, subtitles
        win = self.make()
        win.set_subtitle_mode(True)
        self.assertEqual(win.stack.currentIndex(), 1)
        src = Path(_TMP) / "in.srt"
        src.write_text(self.SRT)
        sv = win.subs
        sv.import_srt(str(src))
        self.assertEqual(sv.table.rowCount(), 2)
        self.assertIn("2 cues", win.status.message[0])
        # the preview is page 888 through the decoder: boxed, double height, yellow
        rows = sv.pdoc.page.subpages[0].rows
        d = decoder.decode(rows)
        col = bytes(rows[22]).index(b"Who")
        self.assertTrue(d.box[22, col] and int(d.fg[22, col]) == 3 and int(d.dh[22, col]) == decoder.DH_TOP)
        # type a long second line: a warning, the preview follows
        sv.lines[1].text.setText("x" * 39)
        sv.lines[1].set_colour(6)
        sv._line(1)
        self.assertIn("39 characters", sv.warn.text())
        self.assertEqual(sv.table.item(0, 3).text(), "⚠")
        self.assertIn(b"xxx", bytes(sv.pdoc.page.subpages[0].rows[22]))
        win.undo()
        self.assertEqual(len(sv.doc.track.cues[0].lines), 1)
        out = Path(_TMP) / "out.srt"
        sv.export_srt(str(out))
        back, problems = subtitles.parse_srt(out.read_text())
        self.assertEqual(problems, [])
        self.assertEqual([[(ln.text, ln.colour) for ln in c.lines] for c in back.cues],
                         [[("Who is there?", 3)], [("Only the wind.", 7), ("Or is it?", 6)]])

    def test_cues_added_timed_and_played(self):
        win = self.make()
        win.set_subtitle_mode(True)
        sv = win.subs
        sv.b_add.clicked.emit()
        sv.lines[0].text.setText("Hello")
        sv._line(0)
        sv.start.setText("10")
        sv.end.setText("00:00:12,5")
        sv._times()
        self.assertEqual((sv.doc.cue.start, sv.doc.cue.end), (10.0, 12.5))
        sv.b_add.clicked.emit()
        sv.lines[0].text.setText("Second")
        sv._line(0)
        self.assertEqual(sv.doc.cue.start, 12.7)
        self.assertIsNone(sv.show_time(5.0))
        self.assertTrue(all(bytes(r) == b" " * 40 for r in sv.pdoc.page.subpages[0].rows))
        self.assertEqual(sv.show_time(13.0).lines[0].text, "Second")
        self.assertEqual(sv.table.currentRow(), 1)

    def test_switching_back_keeps_the_page_editor(self):
        win = self.make()
        self.goto(0x199)
        win.set_subtitle_mode(True)
        win.set_subtitle_mode(False)
        self.assertEqual(win.doc.page.number, 0x199)
        self.assertFalse(win.b_subs.checked)


class Panels(UiBase):
    def test_fastext_labels_write_row_24(self):
        win = self.make()
        self.goto(0x150)
        for e, t in zip(win.fastext.labels, ("Back", "Next", "More", "Home")):
            e.setText(t)
        win.fastext._labels()
        self.pump()
        self.assertEqual(bytes(win.doc.sub.rows[24][1:5]), b"Back")
        self.assertEqual(win.doc.sub.rows[24][30], 6)

    def test_link_field(self):
        win = self.make()
        win.fastext.links[1].setText("2a0")
        win.fastext._link(1)
        self.assertEqual(win.doc.sub.links[1], 0x2A0)

    def test_page_number_and_flags(self):
        win = self.make()
        self.goto(0x199)
        win.page_panel.number.setText("1a5")
        win.page_panel._number()
        win.page_panel.checks[0x0001].setChecked(True)
        self.pump()
        self.assertEqual(win.doc.page.number, 0x1A5)
        self.assertTrue(win.doc.sub.status & 0x0001)

    def test_subpages_add_and_cycle(self):
        win = self.make()
        self.goto(0x100)
        win.subpanel.add.clicked.emit()
        self.pump()
        self.assertEqual(len(win.doc.page.subpages), 2)
        self.assertEqual(win.subpanel.list.count(), 2)
        win.subpanel.cycle.setValue(20)
        self.pump()
        self.assertEqual(win.doc.sub.cycle, 20)


class Preview(UiBase):
    def test_flash_animates_in_the_preview_only(self):
        win = self.make()
        self.goto(0x199)
        pv = win.preview
        pv.now = 1000.1
        on = pv.image()
        pv.now = 1000.9
        off = pv.image()
        self.assertNotEqual(on, off)                    # FLASHING on row 7 is gone in the off phase
        # the editor shows it steady
        img1 = win.canvas.decoded()
        self.assertTrue(img1.flash[7].any())

    def test_suppressed_header_is_not_shown(self):
        from ohstxt.ui.preview import display_rows
        win = self.make()
        sub = win.doc.sub
        self.assertNotEqual(bytes(display_rows(win.doc.page, sub, 0)[0]), b" " * 40)
        sub.status |= 0x0004
        self.assertEqual(bytes(display_rows(win.doc.page, sub, 0)[0]), b" " * 40)

    def test_carousel_plays(self):
        win = self.make()
        self.goto(0x300)
        pv = win.preview
        for s in win.doc.page.subpages:
            s.cycle = 1
        pv.set_playing(True)
        self.pump(1.3)
        self.assertEqual(pv.sub_index, 1)


class Files(UiBase):
    def test_save_writes_a_file_that_reads_back(self):
        win = self.make()
        self.goto(0x100)
        win.doc.goto(9, 0)
        win.doc.type_text("Saved line")
        win.save_page()
        back = tti.load(self.folder / "P100.tti")
        self.assertEqual(back.subpages[0].text(9)[:10], "Saved line")
        self.assertFalse(win.doc.dirty)

    def test_new_page_and_save_all(self):
        win = self.make()
        win.add_page(win._blank_page(0x555))
        win.doc.type_text("new")
        win.save_all()
        self.assertTrue((self.folder / "P555.tti").is_file())

    def test_png_export_is_tv_shaped_and_coloured(self):
        from PIL import Image
        win = self.make()
        self.goto(0x199)
        out = Path(_TMP) / "p199.png"
        win.page_png(str(out), scale=1, now=0)
        im = np.asarray(Image.open(out).convert("RGB"))
        self.assertEqual(im.shape[:2], (500, round(480 * render.TV_ASPECT)))
        self.assertTrue((im == (255, 255, 0)).all(-1).any())     # the yellow bar

    def test_image_import_commits_mosaics(self):
        from PIL import Image
        win = self.make()
        self.goto(0x150)
        img = Path(_TMP) / "pic.png"
        a = np.zeros((90, 120, 3), np.uint8)
        a[:, :60] = (255, 0, 0)
        a[:, 60:] = (0, 0, 255)
        Image.fromarray(a).save(img)
        dlg = win.run_image_dialog(str(img), accept=True)
        self.assertIsNotNone(dlg)
        d = win.canvas.decoded()
        self.assertTrue(d.graphics[5].any())
        self.assertIn(0x11, bytes(win.doc.sub.rows[5][:3]))
        win.undo()
        self.assertFalse(win.canvas.decoded().graphics[5].any())

    def test_edit_tf_link_imports_into_the_subpage(self):
        from ohstxt import edittf, samples
        win = self.make()
        self.goto(0x150)
        pg = samples.test_page()
        win.import_link(edittf.from_page(pg, pg.subpages[0]))
        self.assertEqual(bytes(win.doc.sub.rows[1]), bytes(pg.subpages[0].rows[1]))
        self.assertEqual(win.doc.page.number, 0x150)     # the page keeps its own number


if __name__ == "__main__":
    unittest.main()
