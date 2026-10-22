"""Translation scaffolding (docs/I18N.md): the string check passes with its
baseline and fails on a new literal, the committed catalogs are what their
.ts compile to, the window and the Settings dialog speak whatever catalog is
installed (shown with the pseudo-locale), English plurals come out right, an
unknown locale falls back to English, and frequencies format as the shared
vectors say.

The windows here are built with the radio never started (build(...,
start=False)): what is under test is the words, and a simulator receiving
in real time would only slow the run. Nothing here opens a radio."""
import atexit
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import tmp_dir  # noqa: F401  (path setup)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# The window's preferences go somewhere of the tests' own, removed at exit
# as test_ui.py's are (the variable outlives any one test).
_TMP = tempfile.mkdtemp(prefix="ohsatv-i18ntest-")
os.environ["OHSATV_UI_CONF"] = str(Path(_TMP) / "ui.json")
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)

try:
    from PyQt6.QtCore import QCoreApplication
    HAVE_QT = True
except ImportError:          # the engine's tests must still run without PyQt6
    HAVE_QT = False

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("i18n_tool", REPO / "tools" / "i18n.py")
tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool)


def run_tool(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(root / "tools" / "i18n.py"), *args],
                          capture_output=True, text=True, timeout=300)


class StringCheck(unittest.TestCase):
    def test_this_repo_passes_with_its_baseline(self):
        r = run_tool(REPO, "check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # The count M3 drives to zero, in every suite run's output.
        summary = r.stdout.strip().splitlines()[-1]
        self.assertIn("baseline", summary)
        sys.stderr.write(f"\n{summary}\n")

    def test_a_new_literal_fails_and_a_converted_one_must_leave_the_baseline(self):
        root = tmp_dir(self, "ohsatv-i18ncheck-")
        shutil.copytree(REPO / "tools", root / "tools", ignore=shutil.ignore_patterns("__pycache__"))
        (root / ".git").mkdir()     # the check finds the repo root by it
        (root / "app").mkdir()
        ui = root / "app" / "ui.py"
        ui.write_text('class W:\n    def f(self):\n        self.b.setText("Old label")\n')

        r = run_tool(root, "check")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('"Old label"', r.stdout)

        self.assertEqual(run_tool(root, "baseline", "--add-new").returncode, 0)
        r = run_tool(root, "check")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("baseline 1 literal(s)", r.stdout)

        # A new literal beside the baselined one fails, and the baseline
        # command will not quietly take it in.
        ui.write_text(ui.read_text() + '        self.b.setToolTip("New tip")\n')
        r = run_tool(root, "check")
        self.assertEqual(r.returncode, 1)
        self.assertIn('"New tip"', r.stdout)
        self.assertNotIn('"Old label"', r.stdout)
        self.assertEqual(run_tool(root, "baseline").returncode, 1)
        self.assertNotIn("New tip", (root / "i18n-pending.txt").read_text())

        # Converting the old literal leaves a stale line, which fails
        # until the baseline command removes it.
        ui.write_text('class W:\n    def f(self):\n        self.b.setText(self.tr("Old label"))\n')
        r = run_tool(root, "check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("matched nothing", r.stdout)
        run_tool(root, "baseline")
        self.assertNotIn("Old label", (root / "i18n-pending.txt").read_text())


class Catalogs(unittest.TestCase):
    def test_each_committed_qm_is_its_ts_compiled(self):
        for c in tool.CATALOGS:
            ts = REPO / c["ts"]
            self.assertEqual(ts.with_suffix(".qm").read_bytes(), tool.qm_for(ts),
                             f"{ts.name} changed without tools/i18n.py compile")

    def test_the_gettext_template_is_current(self):
        r = run_tool(REPO, "pot", "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_the_template_holds_each_gettext_call_and_goes_stale_when_a_sentence_changes(self):
        root = tmp_dir(self, "ohsatv-i18npot-")
        shutil.copytree(REPO / "tools", root / "tools", ignore=shutil.ignore_patterns("__pycache__"))
        (root / "ohsatv").mkdir()
        src = root / "ohsatv" / "radio.py"
        src.write_text("from ohsatv.i18n.text import _, ngettext, pgettext\n"
                       "def f(n, x):\n"
                       "    a = _(\"The picture is {state}\").format(state=x)\n"
                       "    b = ngettext(\"{n} page\", \"{n} pages\", n)\n"
                       "    c = pgettext(\"lock\", \"Locked\")\n"
                       "    d = _(x)\n")
        self.assertEqual(run_tool(root, "pot").returncode, 0)
        pot = (root / "ohsatv" / "i18n" / "ohsatv.pot").read_text()
        self.assertIn('#, python-brace-format\nmsgid "The picture is {state}"\nmsgstr ""', pot)
        self.assertIn('msgid "{n} page"\nmsgid_plural "{n} pages"\nmsgstr[0] ""\nmsgstr[1] ""', pot)
        self.assertIn('msgctxt "lock"\nmsgid "Locked"', pot)
        self.assertIn("#: ohsatv/radio.py:3", pot)
        self.assertEqual(run_tool(root, "pot", "--check").returncode, 0)
        src.write_text(src.read_text().replace("The picture is", "The picture was"))
        r = run_tool(root, "pot", "--check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("ohsatv/i18n/ohsatv.pot is out of date", r.stdout)

    def test_gettext_sentences_need_no_catalog_in_english_and_no_qt(self):
        code = ("import sys; from ohsatv.i18n.text import _, ngettext; "
                "print(_('Transmit stopped'), ngettext('{n} page', '{n} pages', 2).format(n=2)); "
                "print(any(m.startswith('PyQt6') for m in sys.modules))")
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, cwd=REPO,
                             env={**os.environ, "SHACK_LOCALE": "de"})
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.split("\n")[:2], ["Transmit stopped 2 pages", "False"])

    def test_english_plural_forms_follow_the_convention(self):
        self.assertEqual(tool.plural_forms("Teletext · %n page(s) received"),
                         ["Teletext · %n page received", "Teletext · %n pages received"])
        self.assertIsNone(tool.plural_forms("%n entry or entries"))

    def test_pseudo_text_keeps_placeholders_and_grows(self):
        p = tool.pseudo_text("Recording {seconds} s, %n page(s)")
        self.assertIn("%n", p)
        self.assertIn("{seconds}", p)
        self.assertTrue(p.startswith("[") and p.endswith("]"))
        self.assertGreater(len(p), len("Recording {seconds} s, %n page(s)") * 1.3)


class Locale(unittest.TestCase):
    def test_candidates_go_from_specific_to_language(self):
        from ohsatv import i18n
        self.assertEqual(i18n.candidates("pt-BR.UTF-8"), ["pt_BR", "pt"])
        self.assertEqual(i18n.candidates("C"), [])

    def test_a_language_without_a_catalog_falls_back_to_english(self):
        from ohsatv import i18n
        d = tmp_dir(self, "ohsatv-i18nqm-")
        (d / "ohsatv_de.qm").write_bytes(b"")
        self.assertEqual(i18n.choose(["fr_FR", "de_AT"], d), "de")
        self.assertEqual(i18n.choose(["fr_FR", "xx"], d), "en")


class Frequencies(unittest.TestCase):
    """The shared vectors (ohs-libs spec/vectors/i18n/format.json)."""

    @unittest.skipUnless(HAVE_QT, "PyQt6 is not installed")
    def test_frequency_vectors(self):
        from ohsatv.i18n import fmt
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
    def test_other_numbers_follow_the_same_rules(self):
        # fmt.number: sample rates, axis spans, percentages - the locale's
        # separator, never grouped, :g's shortest form unless told.
        from ohsatv.i18n import fmt
        self.assertEqual(fmt.number(12.5, locale="de"), "12,5")
        self.assertEqual(fmt.number(10.0, locale="en"), "10")
        self.assertEqual(fmt.number(2400.0, 0, locale="de"), "2400")
        self.assertEqual(fmt.number(4.43, 2, locale="fr"), "4,43")


@unittest.skipUnless(HAVE_QT, "PyQt6 is not installed")
class Window(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ohsatv.ui import make_app
        cls.app = make_app(["test"])

    def tearDown(self):
        from ohsatv import i18n
        i18n.install(locale="en")

    def window(self):
        from ohsatv import state
        from ohsatv.audio import NullAudio
        from ohsatv.radio import Radio
        from ohsatv.ui import build
        from ohsatv.ui.prefs import Prefs
        tmp = tmp_dir(self, "ohsatv-i18nwin-")
        cfg = state.defaults(("setting", "control"))
        cfg.update(device="sim", audioBackend="null", band="23cm", system="am", rxFreq=1275.0e6,
                   txFreq=1275.0e6, licenceAck=True)
        radio = Radio(cfg, conf_path=tmp / "c.json", data_dir=tmp / "data", audio=NullAudio(realtime=True))
        win, bridge = build(radio, Prefs(tmp / "ui.json"), start=False)
        self.addCleanup(self._destroy, win, bridge)
        return win, bridge

    def _destroy(self, win, bridge):
        # On this thread, now, as test_ui.py does and for its reason: left to
        # the garbage collector a widget tree can be freed on another thread.
        from PyQt6 import sip
        win.close()
        bridge.stop()
        for o in (win, bridge):
            if not sip.isdeleted(o):
                sip.delete(o)
        self.app.processEvents()

    def test_english_plurals_come_from_the_english_catalog(self):
        from ohsatv import i18n
        self.assertEqual(i18n.install(locale="en_GB"), "en")
        tr = QCoreApplication.translate
        self.assertEqual(tr("MainWindow", "Teletext · %n page(s) received", "", 1), "Teletext · 1 page received")
        self.assertEqual(tr("MainWindow", "Teletext · %n page(s) received", "", 3), "Teletext · 3 pages received")

    def test_the_window_speaks_the_installed_catalog(self):
        from ohsatv import i18n
        from ohsatv.ui.settings import SettingsDialog
        d = tmp_dir(self, "ohsatv-i18nxa-")
        tool.write_pseudo(d)
        self.assertEqual(i18n.install(locale="en-XA", catalog_dir=d), "en_XA")
        win, bridge = self.window()
        self.assertEqual(win.windowTitle(), tool.pseudo_text("OpenHamShack ATV"))
        # No menu bar here: the top bar's tools are the window's commands.
        self.assertEqual(win.btn_gear.toolTip(), tool.pseudo_text("Settings (Ctrl+,)"))
        self.assertEqual(win.picture_card.title, tool.pseudo_text("Picture · received"))
        dlg = SettingsDialog(bridge, win.prefs, win)
        self.addCleanup(dlg.deleteLater)
        self.assertEqual(dlg.windowTitle(), tool.pseudo_text("Settings"))
        self.assertIn((dlg.PAGES[0][0], tool.pseudo_text("Station")), dlg.nav.items)
        # And the strings M3 converted, in the panels and the teletext viewer.
        self.assertEqual(win.tx_panel.srt_clear.toolTip(), tool.pseudo_text("No SRT file (typed captions only)"))
        self.assertEqual(win.ttx_view.hold_btn.toolTip(), tool.pseudo_text("Keep this subpage on the screen (H)"))

    def test_the_transmit_frequency_takes_the_locales_decimal_separator(self):
        from ohsatv import i18n
        # No German catalog yet: English words, but German numbers, because
        # the numbers follow the locale asked for, not the catalog found.
        self.assertEqual(i18n.install(locale="de"), "en")
        win, _bridge = self.window()
        self.assertEqual(win.tx_panel.freq.text(), "TX 1275,000\u00a0MHz")

    def test_an_unknown_locale_gets_the_english_window(self):
        from ohsatv import i18n
        self.assertEqual(i18n.install(locale="xx_YY"), "en")
        win, _bridge = self.window()
        self.assertEqual(win.windowTitle(), "OpenHamShack ATV")
        self.assertEqual(win.btn_gear.toolTip(), "Settings (Ctrl+,)")
        self.assertEqual(win.picture_card.title, "Picture · received")


if __name__ == "__main__":
    unittest.main()
