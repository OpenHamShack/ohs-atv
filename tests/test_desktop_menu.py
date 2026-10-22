"""The .deb puts OpenHamShack ATV in an "Amateur Radio" submenu rather than
Internet (packaging/openhamshack-atv.menu says why). OpenHamShack NB and HAM ship
the same submenu; this is the same test as theirs.

The menu is resolved here, from the built packages' own files, by the
subset of the freedesktop menu spec they use: merged menus of the same
name combine, a submenu takes what its <Include> matches, and a parent's
<Exclude> removes an entry. That is enough to catch a wrong category, a
<Directory> that names a file no package ships, or an Internet exclusion
that misses our own entry. Where KDE's kbuildsycoca6 is installed, the
real resolver is asked as well - that is what was checked by hand on the
Plasma laptop on 08/10/2026 (Amateur Radio/ openhamshack-atv.desktop, and no
longer under Internet/).
"""
import configparser
import os
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

if shutil.which("dpkg-deb") is None:
    raise unittest.SkipTest("dpkg-deb not installed: the .deb is built and checked on Debian")

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILD_SCRIPT = REPO_ROOT / "tools" / "build-deb.sh"
PACKAGES = {"atv": "openhamshack-atv"}


def desktop(path: Path) -> dict:
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    cp.optionxform = str
    cp.read(path, encoding="utf-8")
    return dict(cp["Desktop Entry"]) if cp.has_section("Desktop Entry") else {}


def matches(rule: ET.Element, filename: str, categories: set) -> bool:
    if rule.tag == "Category":
        return rule.text in categories
    if rule.tag == "Filename":
        return rule.text == filename
    if rule.tag == "And":
        return all(matches(r, filename, categories) for r in rule)
    if rule.tag == "Not":
        return not any(matches(r, filename, categories) for r in rule)
    return any(matches(r, filename, categories) for r in rule)      # Or, Include, Exclude


class AmateurRadioSubmenu(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name) / "root"
        cls.conffiles = {}
        for app, name in PACKAGES.items():
            subprocess.run([str(BUILD_SCRIPT)], cwd=cls.tmp.name, check=True, capture_output=True, text=True)
            deb = next(Path(cls.tmp.name).glob(f"{name}_*_all.deb"))
            subprocess.run(["dpkg-deb", "-x", str(deb), str(cls.root)], check=True)
            cls.conffiles[name] = subprocess.run(["dpkg-deb", "-I", str(deb), "conffiles"], check=True,
                                                 capture_output=True, text=True).stdout.split()
        cls.merged = cls.root / "etc" / "xdg" / "menus" / "applications-merged"
        cls.dirs = cls.root / "usr" / "share" / "desktop-directories"
        cls.apps = cls.root / "usr" / "share" / "applications"

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def menus(self, name: str) -> list:
        """Every same-named submenu of Applications across all merged files."""
        found = []
        for f in sorted(self.merged.glob("*.menu")):
            top = ET.parse(f).getroot()
            self.assertEqual(top.findtext("Name"), "Applications", f"{f.name}: merges only under Applications")
            found += [m for m in top.findall("Menu") if m.findtext("Name") == name]
        return found

    def placed(self, filename: str) -> tuple:
        entry = desktop(self.apps / filename)
        cats = {c for c in entry.get("Categories", "").split(";") if c}
        ham = any(matches(inc, filename, cats) for m in self.menus("HamRadio") for inc in m.findall("Include"))
        out_of_internet = any(matches(ex, filename, cats) for m in self.menus("Internet") for ex in m.findall("Exclude"))
        return ham, out_of_internet

    def test_each_app_lands_in_amateur_radio_and_leaves_internet(self):
        for name in PACKAGES.values():
            with self.subTest(name):
                self.assertEqual(self.placed(f"{name}.desktop"), (True, True))

    def test_the_submenu_is_titled_amateur_radio_by_a_shipped_directory_file(self):
        for m in self.menus("HamRadio"):
            path = self.dirs / m.findtext("Directory")
            self.assertTrue(path.is_file(), f"{path.name} is named but no package ships it")
            entry = desktop(path)
            self.assertEqual((entry.get("Type"), entry.get("Name")), ("Directory", "Amateur Radio"))

    def test_another_packages_ham_app_joins_without_being_moved(self):
        # WSJT-X says AudioVideo;Audio;HamRadio; - it should appear in
        # Amateur Radio too, but its own place is its packager's business.
        (self.apps / "wsjtx-like.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=X\nCategories=Network;HamRadio;\n")
        try:
            self.assertEqual(self.placed("wsjtx-like.desktop"), (True, False))
        finally:
            (self.apps / "wsjtx-like.desktop").unlink()

    def test_the_menu_file_is_a_conffile_with_a_name_of_its_own(self):
        for name, files in self.conffiles.items():
            with self.subTest(name):
                self.assertEqual(files, [f"/etc/xdg/menus/applications-merged/{name}.menu"])

    @unittest.skipIf(shutil.which("kbuildsycoca6") is None, "KDE's menu resolver is not installed here")
    def test_kde_puts_them_under_amateur_radio(self):
        env = dict(os.environ, XDG_MENU_PREFIX="plasma-",
                   XDG_CONFIG_DIRS=f"{self.root / 'etc' / 'xdg'}:/etc/xdg",
                   XDG_DATA_DIRS=f"{self.root / 'usr' / 'share'}:/usr/share")
        out = subprocess.run(["kbuildsycoca6", "--menutest"], env=env, capture_output=True, text=True).stdout
        for name in PACKAGES.values():
            self.assertIn(f"Amateur Radio/\t{name}.desktop", out)
            self.assertNotIn(f"Internet/\t{name}.desktop", out)


if __name__ == "__main__":
    unittest.main()
