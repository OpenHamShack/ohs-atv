"""What the Windows bundle carries and leaves out: no Qt PDF, every Python
package's licence text beside the C libraries', matching THIRD-PARTY.txt,
and the program's own upstream sets each in the folder ohsatv/host.py looks
in (ffmpeg\\, libiio\\, hackrf\\) with the icon where the window finds it.

PyInstaller is not installed here, so the spec is executed as PyInstaller
executes it - as Python with SPECPATH and Analysis/PYZ/EXE/COLLECT in its
globals - with stand-ins for those four and for PyInstaller's
collect_submodules. The stand-in Analysis hands back the kind of TOC the
QtGui hook produces on Windows, Qt PDF included; the test then looks at what
the spec actually passes to COLLECT, which is what lands in the installer.

Ported from OpenHamShack NB's tests/test_windows_bundle.py (as of 08/10/2026),
with the packaging helpers it tests; one program here, so no per-app
entries, and Paramiko is in this program's bundle (the radio lock)."""
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401

REPO_ROOT = Path(__file__).resolve().parent.parent
WINDOWS = REPO_ROOT / "packaging" / "windows"

sys.path.insert(0, str(WINDOWS))
import bundle_licences  # noqa: E402
import bundle_prune  # noqa: E402

sys.path.insert(0, str(REPO_ROOT / "packaging"))
import shack_vendor  # noqa: E402

THIRD_PARTY = WINDOWS / "THIRD-PARTY.txt"

QT_PDF = [
    r"PyQt6\Qt6\bin\Qt6Pdf.dll",
    r"PyQt6\Qt6\bin\Qt6PdfQuick.dll",
    r"PyQt6\Qt6\bin\Qt6PdfWidgets.dll",
    r"PyQt6\Qt6\plugins\imageformats\qpdf.dll",
    r"PyQt6\QtPdf.pyd",
]
NEEDED = [
    r"PyQt6\Qt6\bin\Qt6Core.dll",
    r"PyQt6\Qt6\bin\Qt6Gui.dll",
    r"PyQt6\Qt6\bin\Qt6Widgets.dll",
    r"PyQt6\Qt6\bin\Qt6Svg.dll",
    r"PyQt6\Qt6\plugins\imageformats\qsvg.dll",
    r"PyQt6\Qt6\plugins\platforms\qwindows.dll",
    r"PyQt6\QtGui.pyd",
]
# What build-windows.ps1 puts in the vendor folder, by subfolder.
VENDOR_FILES = {
    "libiio": ["libiio.dll", "libxml2.dll", "libusb-1.0.dll", "libserialport-0.dll"],
    "ffmpeg": ["ffmpeg.exe", "ffprobe.exe"],
    "hackrf": ["hackrf-0.dll", "libusb-1.0.dll", "libwinpthread-1.dll"],
}


def toc(names, typecode="BINARY"):
    return [(n, "C:\\src\\" + n, typecode) for n in names]


class StandIns:
    """What the spec reads, made up in a scratch folder: the vendor folder,
    installed distributions as pip lays them out, and a Python home."""

    DISTS = [("numpy", "2.5.3", ["numpy"], ["licenses/LICENSE.txt", "licenses/numpy/ma/LICENSE"]),
             ("PyQt6", "6.11.0", ["PyQt6"], ["licenses/LICENSE"]),
             ("PyQt6-Qt6", "6.11.2", ["PyQt6/Qt6"], ["LICENSE"]),
             ("PyQt6_sip", "13.13.0", ["PyQt6/sip"], ["licenses/LICENSE"]),
             ("PyAudio", "0.2.14", ["pyaudio"], ["LICENSE.txt"]),
             ("pillow", "12.3.0", ["PIL"], ["licenses/LICENSE"]),
             ("pyinstaller", "6.22.3", ["PyInstaller"], ["licenses/COPYING.txt"]),
             ("cryptography", "50.0.2", ["cryptography"],
              ["licenses/LICENSE", "licenses/LICENSE.APACHE", "licenses/LICENSE.BSD"]),
             ("cffi", "2.1.1", ["_cffi_backend.pyd"], ["licenses/LICENSE"]),
             ("paramiko", "3.5.1", ["paramiko"], ["LICENSE"]),
             ("bcrypt", "5.0.0", ["bcrypt"], ["LICENSE"]),
             ("PyNaCl", "1.6.2", ["nacl"], ["licenses/LICENSE", "licenses/licenses/LICENSE.libsodium.txt"])]
    NOT_BUNDLED = [("altgraph", "0.17.5", ["altgraph"], ["LICENSE"]),
                   ("pycparser", "3.0", ["pycparser"], ["licenses/LICENSE"])]

    def __init__(self, root: Path):
        self.root = root
        self.vendor = root / "vendor"
        for sub in ("licences", "py"):
            (self.vendor / sub).mkdir(parents=True)
        # The real wheels, checked and unpacked as build-windows.ps1 does.
        shack_vendor.unpack(self.vendor / "shack")
        for sub, names in VENDOR_FILES.items():
            (self.vendor / sub).mkdir(parents=True)
            for n in names:
                (self.vendor / sub / n).write_bytes(b"MZ")
        self.ico = self.vendor / "openhamshack-atv.ico"
        self.ico.write_bytes(b"\0")
        self.python = root / "python"
        self.python.mkdir()
        (self.python / "LICENSE.txt").write_text("PSF licence\n")
        self.site = root / "site"
        self.site.mkdir()
        self.modules = []
        for d in self.DISTS + self.NOT_BUNDLED:
            self.add_dist(*d, bundled=d not in self.NOT_BUNDLED)
        provided = {"python-LICENSE.txt"} | set(self.dist_licences())
        required, _ = bundle_licences.references(THIRD_PARTY.read_text())
        for ref in required:
            if not ref.endswith("*") and ref not in provided:
                (self.vendor / "licences" / ref).write_text(f"{ref}\n")

    def add_dist(self, name, version, tops, licences, bundled=True) -> None:
        info = f"{name.replace('-', '_')}-{version}.dist-info"
        files = []
        for top in tops:
            rel = top if top.endswith(".pyd") else f"{top}/__init__.py"
            files.append(rel)
            if bundled:
                self.modules.append(top.split("/")[0].removesuffix(".pyd"))
        files += [f"{info}/{lic}" for lic in licences] + [f"{info}/METADATA", f"{info}/RECORD"]
        for rel in files:
            (self.site / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.site / rel).write_text(f"{name} {rel}\n")
        (self.site / info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
        (self.site / info / "RECORD").write_text("".join(f"{rel},,\n" for rel in files))

    def dist_licences(self):
        for name, _v, _tops, licences in self.DISTS:
            for lic in licences:
                parts = lic.split("/")
                if parts[0] == "licenses" and len(parts) > 1:
                    parts = parts[1:]
                yield f"{name}-{'-'.join(parts)}"


class Spec(unittest.TestCase):
    def run_spec(self, prepare=None) -> dict:
        captured = {}
        stand_ins = None

        class Analysis:
            def __init__(self, scripts, binaries=(), datas=(), hiddenimports=(), **_kw):
                captured["hidden"] = list(hiddenimports)
                self.scripts = scripts
                self.pure = [(m, f"C:\\site\\{m}\\__init__.py", "PYMODULE") for m in stand_ins.modules] + [
                    ("json", "C:\\python\\Lib\\json\\__init__.py", "PYMODULE")]
                self.binaries = list(binaries) + toc(NEEDED + QT_PDF)
                self.datas = [(f"{dest}\\{Path(src).name}" if dest != "." else Path(src).name, src, "DATA")
                              for src, dest in datas] + toc([r"PyQt6\Qt6\plugins\imageformats\qpdf.dll"], "DATA")

        def COLLECT(*args, **kw):
            captured["toc"] = [e for a in args if isinstance(a, list) for e in a]
            captured["name"] = kw.get("name")

        hooks = types.ModuleType("PyInstaller.utils.hooks")
        hooks.collect_submodules = lambda pkg: [f"{pkg}.radio", f"{pkg}.devices.hackrf"]
        fake = {"PyInstaller": types.ModuleType("PyInstaller"),
                "PyInstaller.utils": types.ModuleType("PyInstaller.utils"), "PyInstaller.utils.hooks": hooks}
        with tempfile.TemporaryDirectory() as tmp:
            stand_ins = StandIns(Path(tmp))
            if prepare:
                prepare(stand_ins)
            spec = WINDOWS / "openhamshack-atv.spec"
            env = {"OHSATV_VENDOR": str(stand_ins.vendor), "OHSATV_ICO": str(stand_ins.ico)}
            g = {"__file__": str(spec), "__name__": "__main__", "SPECPATH": str(WINDOWS),
                 "Analysis": Analysis, "PYZ": lambda *a, **k: "pyz", "EXE": lambda *a, **k: "exe",
                 "COLLECT": COLLECT}
            saved_path = list(sys.path)
            try:
                sys.path[:] = [str(stand_ins.site)] + [p for p in saved_path
                                                        if not p.endswith(("site-packages", "dist-packages"))]
                with mock.patch.dict(os.environ, env), mock.patch.object(sys, "base_prefix", str(stand_ins.python)), \
                        mock.patch.dict(sys.modules, fake):
                    exec(compile(spec.read_text(), str(spec), "exec"), g)
            finally:
                sys.path[:] = saved_path
        return captured

    def names(self, got):
        return [e[0].replace("/", "\\") for e in got["toc"]]

    def test_no_qt_pdf_and_everything_used_kept(self):
        got = self.run_spec()
        self.assertEqual(got["name"], "openhamshack-atv")
        names = self.names(got)
        for n in QT_PDF:
            self.assertNotIn(n, names)
        for n in NEEDED:
            self.assertIn(n, names)

    def test_each_upstream_set_lands_where_host_looks_for_it(self):
        from ohsatv import host
        names = self.names(self.run_spec())
        for sub, files in VENDOR_FILES.items():
            for f in files:
                self.assertIn(f"{sub}\\{f}", names)
        self.assertEqual({host.FFMPEG_SUBDIR, host.LIBIIO_SUBDIR, host.HACKRF_SUBDIR}, set(VENDOR_FILES))
        self.assertIn(host.HACKRF_DLLS[0], VENDOR_FILES["hackrf"])
        # The window's icon where ohsatv.ui.appicon.svg_path() looks beside
        # the package, and every non-Python file of the package (VERSION).
        self.assertIn("packaging\\openhamshack-atv.svg", names)
        self.assertIn("ohsatv\\VERSION", names)
        for n in ("LICENSE", "THIRD-PARTY.txt", "openhamshack-atv.ico"):
            self.assertIn(n, names)

    def test_the_lazily_imported_modules_are_named(self):
        hidden = self.run_spec()["hidden"]
        for m in ("iio", "pyaudio", "paramiko", "ohsatv.devices.hackrf", "ohstxt.radio", "PIL.Image",
                  "shack_bandplan.radio", "shack_pad.radio"):
            self.assertIn(m, hidden)

    def test_the_shared_libraries_are_carried_but_not_the_string_check(self):
        got = self.run_spec()
        names = self.names(got)
        # shack-bandplan reads its dataset beside its own __file__.
        self.assertIn("shack_bandplan\\data\\bandplan\\manifest.json", names)
        self.assertFalse(any(n.startswith("shack_i18n") for n in names + got["hidden"]))
        # Our own code, under LICENSE: no licence entry of their own.
        self.assertFalse(any(n.startswith("licences\\shack") for n in names))

    def test_a_wheel_that_does_not_match_its_pin_stops_the_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            vendor = Path(tmp) / "vendor"
            vendor.mkdir()
            for w in shack_vendor.VENDOR.iterdir():
                (vendor / w.name).write_bytes(w.read_bytes())
            wheel = next(vendor.glob("shack_pad-*.whl"))
            wheel.write_bytes(wheel.read_bytes() + b"\0")
            with self.assertRaises(SystemExit) as e:
                shack_vendor.unpack(Path(tmp) / "out", vendor)
            self.assertIn("does not match its pin", str(e.exception))

    def test_every_python_packages_licence_is_carried(self):
        got = {n.split("\\", 1)[1] for n in self.names(self.run_spec()) if n.startswith("licences\\")}
        for name in ["python-LICENSE.txt", "pyinstaller-COPYING.txt", "numpy-LICENSE.txt", "PyQt6-LICENSE",
                     "PyQt6-Qt6-LICENSE", "PyQt6_sip-LICENSE", "PyAudio-LICENSE.txt", "pillow-LICENSE", "cryptography-LICENSE",
                     "cffi-LICENSE", "paramiko-LICENSE", "bcrypt-LICENSE", "PyNaCl-LICENSE",
                     "hackrf-COPYING", "libusb-1.0.29-COPYING", "winpthreads-COPYING", "ffmpeg-LICENSE.txt"]:
            self.assertIn(name, got)
        self.assertNotIn("altgraph-LICENSE", got)
        self.assertNotIn("pycparser-LICENSE", got)

    def test_a_licence_named_in_third_party_but_missing_stops_the_build(self):
        def drop(s): (s.vendor / "licences" / "hackrf-COPYING").unlink()
        with self.assertRaises(bundle_licences.LicenceError) as e:
            self.run_spec(drop)
        self.assertIn("licences/hackrf-COPYING, which is not in the bundle", str(e.exception))

    def test_a_package_without_an_entry_stops_the_build(self):
        def newdep(s): s.add_dist("shinything", "1.0", ["shinything"], ["licenses/LICENSE"])
        with self.assertRaises(bundle_licences.LicenceError) as e:
            self.run_spec(newdep)
        self.assertIn("licences/shinything-LICENSE is in the bundle but THIRD-PARTY.txt does not name it",
                      str(e.exception))

    def test_the_filter_takes_qt_pdf_and_only_it(self):
        kept = bundle_prune.without_qt_pdf(toc(QT_PDF + NEEDED))
        self.assertEqual([e[0] for e in kept], NEEDED)


if __name__ == "__main__":
    unittest.main()
