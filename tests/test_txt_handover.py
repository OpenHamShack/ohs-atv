"""The hand-over between ATV's window and the Teletext editor folded into
it: the transmit panel's editor button opens the editor (ohstxt) as a window
of this program on the folder the panel sends, and a page typed and saved
there is what the engine's teletext insertion then reads - through the
engine's own reader (ohsatv/tti.py, via Radio._ttx_load, the call the
carousel is filled from), not the editor's.

The ATV window is built with the radio never started (build(...,
start=False)): what is under test is the folder passing from one window to
the other and the file passing from the editor to the engine, and nothing
here keys or opens a radio. Keying with the folder watched is
test_editor_e2e.py and test_teletext.py's LiveEdits."""
import atexit
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from helpers import tmp_dir

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Both windows' preferences, and the editor's own data folder (where its
# default service is made when ATV has no folder yet), somewhere of the
# tests' own, removed at exit: the variables outlive any one test.
_TMP = tempfile.mkdtemp(prefix="ohsatv-handover-")
os.environ["OHSATV_UI_CONF"] = str(Path(_TMP) / "atv-ui.json")
os.environ["OHSTXT_UI_CONF"] = str(Path(_TMP) / "txt-ui.json")
os.environ["OHSTXT_DATA"] = str(Path(_TMP) / "txt-data")
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)

try:
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    HAVE_QT = True
except ImportError:          # the engine's tests must still run without PyQt6
    HAVE_QT = False

from ohsatv import state, ttxsample
from ohsatv.audio import NullAudio
from ohsatv.radio import Radio


@unittest.skipUnless(HAVE_QT, "PyQt6 is not installed")
class EditorHandOver(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ohsatv.ui import make_app
        cls.app = make_app(["test"])

    def make(self, **over):
        from ohsatv.ui import build
        from ohsatv.ui.prefs import Prefs
        tmp = tmp_dir(self, "ohsatv-handover-")
        cfg = state.defaults(("setting", "control"))
        cfg.update(device="sim", audioBackend="null", band="23cm", system="am", amRate=10_000_000,
                   callsign="GM4BRI", licenceAck=True)
        cfg.update(over)
        self.radio = Radio(cfg, conf_path=tmp / "c.json", data_dir=tmp / "data", audio=NullAudio(realtime=False))
        self.win, self.bridge = build(self.radio, Prefs(tmp / "ui.json"), start=False)
        self.win.show()
        self.addCleanup(self._close)
        self.pump()
        return self.win

    def _close(self):
        from PyQt6 import sip
        ed = self.win.ttx_editor
        if ed is not None and not sip.isdeleted(ed):
            ed.ask_before_discard = False
        if not sip.isdeleted(self.win):
            self.win.close()
            self.bridge.stop()
            self.pump(0.1)
            sip.delete(self.win)
        self.radio.stop()

    def pump(self, sec=0.05):
        end = time.monotonic() + sec
        while time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.005)

    def open_editor(self):
        QTest.mouseClick(self.win.tx_panel.ttx_edit, Qt.MouseButton.LeftButton)
        self.pump(0.2)
        ed = self.win.ttx_editor
        self.assertIsNotNone(ed, "the editor button opened nothing")
        ed.ask_before_discard = False
        return ed

    def engine_page(self, mag, page):
        """What the insertion would send: the service as the engine loads it."""
        self.radio._ttx_load(force=True)
        svc = self.radio._ttx_svc
        self.assertIsNotNone(svc, self.radio._ttx_err)
        return svc.pages.get((mag, page))

    def test_the_editor_opens_on_the_folder_sent_and_its_saved_page_is_what_goes_out(self):
        svc = tmp_dir(self, "ohsatv-handover-svc-")
        ttxsample.write(svc)
        self.make(ttxOn=True, ttxDir=str(svc))
        ed = self.open_editor()
        self.assertTrue(ed.isVisible())
        self.assertEqual(Path(ed.service.folder).resolve(), svc.resolve())
        self.assertIn(0x100, [p.number for p in ed.service.pages])

        # Typed on the editor's canvas with real key presses, then saved.
        for i, pg in enumerate(ed.service.pages):
            if pg.number == 0x100:
                ed.select_page(i)
        self.pump()
        ed.doc.goto(18, 1)
        ed.canvas.setFocus()
        QTest.keyClicks(ed.canvas, "EDITED IN ATV")
        self.pump()
        ed.save_page()

        sent = self.engine_page(1, 0x00)
        self.assertIsNotNone(sent)
        row = bytes(sent.subpages[0].rows[18])
        self.assertEqual(row[1:14], b"EDITED IN ATV", row)

        # A second press raises the same window rather than opening another.
        self.assertIs(self.open_editor(), ed)

    def test_with_no_folder_yet_the_editors_own_service_becomes_the_one_sent(self):
        self.make(ttxDir="")
        ed = self.open_editor()
        folder = Path(self.radio.st["ttxDir"])
        self.assertEqual(folder, Path(os.environ["OHSTXT_DATA"]) / "service")
        self.assertEqual(Path(ed.service.folder).resolve(), folder.resolve())
        # The editor made its sample pages there, and the engine reads them.
        sent = self.engine_page(1, 0x00)
        self.assertIsNotNone(sent)
        epage = next(p for p in ed.service.pages if p.number == 0x100)
        self.assertEqual(bytes(sent.subpages[0].rows[1]), bytes(c & 0x7F for c in epage.subpages[0].rows[1]))

    def test_the_editor_keeps_its_look_to_itself_and_closes_with_atv(self):
        from PyQt6 import sip
        self.make()
        sheet = self.app.styleSheet()
        ed = self.open_editor()
        self.assertEqual(self.app.styleSheet(), sheet, "the editor restyled the whole application")
        self.assertTrue(ed.styleSheet())
        ed.toggle_theme()
        self.assertEqual(self.app.styleSheet(), sheet, "the editor's theme switch restyled ATV")
        self.win.close()
        self.pump(0.1)
        self.assertTrue(sip.isdeleted(ed) or not ed.isVisible())


if __name__ == "__main__":
    unittest.main()
