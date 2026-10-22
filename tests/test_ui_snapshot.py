"""The install proof's photograph (ohsatv/ui/snapshot.py): with
$SHACK_DESKTOP_SNAPSHOT set the program saves a PNG of its front window
once and carries on; without it nothing is scheduled or written."""
import os
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401  (path setup)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PyQt6.QtGui import QImage
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QDialog, QLabel, QWidget
    HAVE_QT = True
except ImportError:
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "PyQt6 is not installed")
class Snapshot(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ohsatv.ui import make_app
        cls.app = make_app(["test"])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ohsatv-snap-")
        self.addCleanup(self.tmp.cleanup)
        self.png = str(Path(self.tmp.name) / "shot.png")

    def show(self, w, width, height):
        w.resize(width, height)
        w.show()
        self.addCleanup(w.close)
        QTest.qWait(50)
        return w

    def test_nothing_is_scheduled_without_the_variable(self):
        from ohsatv.ui import snapshot
        saved = os.environ.pop(snapshot.ENV, None)
        self.addCleanup(lambda: saved is None or os.environ.__setitem__(snapshot.ENV, saved))
        self.assertIsNone(snapshot.arm(self.app))

    def test_the_timer_saves_the_window_once_as_a_png(self):
        from ohsatv.ui import snapshot
        self.show(QLabel("OpenHamShack ATV"), 320, 200)
        timer = snapshot.arm(self.app, self.png, after_ms=10)
        self.assertIsNotNone(timer)
        for _ in range(100):
            QTest.qWait(20)
            if os.path.exists(self.png):
                break
        img = QImage(self.png)
        self.assertFalse(img.isNull(), "no PNG was written")
        self.assertEqual((img.width(), img.height()), (320, 200))
        self.assertFalse(os.path.exists(self.png + ".part"))
        self.assertFalse(timer.isActive(), "it photographs once, not on a repeat")

    def test_the_largest_shown_window_is_taken_when_none_is_active(self):
        from ohsatv.ui import snapshot
        self.show(QWidget(), 120, 80)
        self.show(QWidget(), 400, 300)
        self.assertTrue(snapshot.save(self.png))
        self.assertEqual(QImage(self.png).width(), 400)

    def test_a_modal_dialog_in_front_is_what_is_photographed(self):
        from ohsatv.ui import snapshot
        self.show(QWidget(), 400, 300)
        dlg = QDialog()
        dlg.setModal(True)
        self.show(dlg, 200, 150)
        self.assertTrue(snapshot.save(self.png))
        self.assertEqual(QImage(self.png).width(), 200)

    def test_no_window_means_no_file(self):
        from ohsatv.ui import snapshot
        for w in self.app.topLevelWidgets():
            w.close()
        QTest.qWait(20)
        self.assertFalse(snapshot.save(self.png))
        self.assertFalse(os.path.exists(self.png))


if __name__ == "__main__":
    unittest.main()
