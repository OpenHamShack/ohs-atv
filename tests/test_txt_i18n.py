"""The Teletext editor's translation scaffolding (ohstxt/i18n): its window
speaks whatever catalog is installed (shown with the pseudo-locale),
English plurals come out right, an unknown locale falls back to English,
and its frequency helper formats as the shared vectors say.

The string check and the catalogs being their .ts compiled are one test for
the whole repo, in test_i18n.py: tools/i18n.py's CATALOGS holds ohstxt's
catalog beside ohsatv's."""
import atexit
import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from helpers import page_rows  # noqa: F401  (path setup)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TMP = tempfile.mkdtemp(prefix="ohstxt-i18ntest-")
os.environ["OHSTXT_UI_CONF"] = str(Path(_TMP) / "ui.json")
os.environ["OHSTXT_DATA"] = str(Path(_TMP) / "data")
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)

try:
    from PyQt6.QtCore import QCoreApplication
    HAVE_QT = True
except ImportError:
    HAVE_QT = False

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("i18n_tool", REPO / "tools" / "i18n.py")
tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool)


class Locale(unittest.TestCase):
    def test_candidates_go_from_specific_to_language(self):
        from ohstxt import i18n
        self.assertEqual(i18n.candidates("pt-BR.UTF-8"), ["pt_BR", "pt"])
        self.assertEqual(i18n.candidates("C"), [])

    def test_a_language_without_a_catalog_falls_back_to_english(self):
        from ohstxt import i18n
        with tempfile.TemporaryDirectory(dir=_TMP) as d:
            (Path(d) / "ohstxt_de.qm").write_bytes(b"")
            self.assertEqual(i18n.choose(["fr_FR", "de_AT"], Path(d)), "de")
            self.assertEqual(i18n.choose(["fr_FR", "xx"], Path(d)), "en")


class Frequencies(unittest.TestCase):
    """The shared vectors (ohs-libs spec/vectors/i18n/format.json)."""

    @unittest.skipUnless(HAVE_QT, "PyQt6 is not installed")
    def test_frequency_vectors(self):
        from ohstxt.i18n import fmt
        cases = json.loads((REPO / "tests" / "data" / "i18n-format.json").read_text())["frequency"]
        for c in cases:
            with self.subTest(c):
                if "error" in c:
                    with self.assertRaises(fmt.FormatError) as cm:
                        fmt.frequency(c["hz"], c["unit"], c["decimals"], c["locale"])
                    self.assertEqual(cm.exception.code, c["error"])
                else:
                    self.assertEqual(fmt.frequency(c["hz"], c["unit"], c["decimals"], c["locale"]), c["text"])


@unittest.skipUnless(HAVE_QT, "PyQt6 is not installed")
class Window(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ohstxt.ui import make_app
        cls.app = make_app(["test"])

    def tearDown(self):
        from ohstxt import i18n
        i18n.install(locale="en")

    def window(self):
        from ohstxt.ui import build
        from ohstxt.ui.prefs import Prefs
        tmp = Path(tempfile.mkdtemp(prefix="svc-", dir=_TMP))
        win = build(Prefs(tmp / "ui.json"), tmp / "service")
        win.ask_before_discard = False
        self.addCleanup(win.close)
        return win

    def test_english_plurals_come_from_the_english_catalog(self):
        from ohstxt import i18n
        self.assertEqual(i18n.install(locale="en_GB"), "en")
        tr = QCoreApplication.translate
        self.assertEqual(tr("MainWindow", "Saved %n page(s)", "", 1), "Saved 1 page")
        self.assertEqual(tr("MainWindow", "Saved %n page(s)", "", 3), "Saved 3 pages")

    def test_the_window_speaks_the_installed_catalog(self):
        from ohstxt import i18n
        with tempfile.TemporaryDirectory(dir=_TMP) as d:
            tool.write_pseudo(Path(d))
            self.assertEqual(i18n.install(locale="en-XA", catalog_dir=Path(d)), "en_XA")
            win = self.window()
        self.assertEqual(win.windowTitle(), tool.pseudo_text("OpenHamShack Teletext"))
        menus = [a.text() for a in win.menuBar().actions()]
        self.assertIn(tool.pseudo_text("&File"), menus)
        self.assertEqual(win.b_undo.toolTip(), tool.pseudo_text("Undo (Ctrl+Z)"))

    def test_an_unknown_locale_gets_the_english_window(self):
        from ohstxt import i18n
        self.assertEqual(i18n.install(locale="xx_YY"), "en")
        win = self.window()
        self.assertEqual(win.windowTitle(), "OpenHamShack Teletext")
        self.assertIn("&File", [a.text() for a in win.menuBar().actions()])


if __name__ == "__main__":
    unittest.main()
