# -*- mode: python -*-
# PyInstaller spec for the macOS build: "OpenHamShack ATV.app". Run by
# tools/build-macos.sh on the Mac mini, which sets OHSATV_SHACK (the shared
# libraries, unpacked by packaging/shack_vendor.py), OHSATV_ICNS (the icon,
# made from the brand kit's PNGs with iconutil) and OHSATV_VERSION.
#
# Shaped on the Windows spec beside it and on QO-100 NB's macOS specs
# (~/openhamshack-nb packaging/macos), whose build proved the BUNDLE settings.
# What a Mac build of ATV carries, and what it does not yet:
#  - ohsatv, ohstxt (the Teletext editor) with Pillow, the shared libraries,
#    NumPy, PyQt6, and PyAudio with PortAudio linked in (built from source
#    by build-macos.sh: PyPI has no macOS wheel of PyAudio).
#  - Not libiio: ohsatv.host finds a bundled libiio on Windows only, so a
#    Pluto on a Mac needs host support first (NB's ohscore/host.py has the
#    pattern). The simulator, the Teletext editor and file playback work.
#  - Not ffmpeg: the video-file source uses one on PATH, as on Linux.
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parents[1]          # noqa: F821 - SPECPATH is PyInstaller's
SHACK = Path(os.environ["OHSATV_SHACK"])
ICNS = os.environ["OHSATV_ICNS"]
VERSION = os.environ["OHSATV_VERSION"]
sys.path.insert(0, str(SHACK))
sys.path.insert(0, str(ROOT / "packaging" / "windows"))
from bundle_licences import licence_datas           # noqa: E402
LICENCES = Path(os.environ["OHSATV_LICENCES"])       # PortAudio's, fetched by build-macos.sh

# Every non-Python file of the packages, found rather than listed (as on
# Windows): a data file added later and forgotten here would fail only on
# a Mac, when the feature that reads it is used.
datas = []
for base, pkgs in ((ROOT, ("ohsatv", "ohstxt")), (SHACK, sorted(p.name for p in SHACK.iterdir() if p.is_dir()))):
    for pkg in pkgs:
        for p in sorted((base / pkg).rglob("*")):
            if p.is_file() and p.suffix not in (".py", ".pyc") and "__pycache__" not in p.parts:
                datas.append((str(p), str(p.parent.relative_to(base))))
datas += [
    # Where ohsatv.ui.appicon.svg_path() looks beside the package.
    (str(ROOT / "packaging" / "openhamshack-atv.svg"), "packaging"),
    (str(ROOT / "LICENSE"), "."),
]
datas += [(str(p), "licences") for p in sorted(LICENCES.glob("*")) if p.is_file()]
shack_names = sorted(p.name for p in SHACK.iterdir() if p.is_dir())

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "windows" / "launch.py")],
    pathex=[str(ROOT), str(SHACK)],
    datas=datas,
    # The whole of ohsatv and ohstxt, and the shared libraries, because much
    # of each is imported inside functions only when the operator asks for
    # it; then what those import lazily (PyAudio, Pillow).
    hiddenimports=(collect_submodules("ohsatv") + collect_submodules("ohstxt")
                   + [m for n in shack_names for m in collect_submodules(n)]
                   + ["pyaudio", "PIL.Image", "PIL.ImageDraw"]),
    excludes=["tkinter", "SoapySDR", "scipy", "matplotlib", "IPython", "paramiko"],
    noarchive=False,
)
# No Qt PDF (packaging/windows/bundle_prune.py says why).
a.binaries = [b for b in a.binaries if "Qt6Pdf" not in b[0] and "QtPdf" not in b[0]]
a.datas = [d for d in a.datas if "Qt6Pdf" not in d[0] and "QtPdf" not in d[0]]
# The licence text of every Python package actually collected, and
# Python's, into licences/ (the Windows helper's collection; there is no
# macOS THIRD-PARTY.txt to check it against yet, so only the collection).
a.datas += licence_datas(a.pure, a.binaries, a.datas, Path(sys.base_prefix) / "lib" / "python3.13" / "LICENSE.txt")
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="shack-atv", console=False,  # noqa: F821
          upx=False, target_arch="arm64")
coll = COLLECT(exe, a.binaries, a.datas, name="shack-atv", upx=False)  # noqa: F821
app = BUNDLE(  # noqa: F821
    coll,
    name="OpenHamShack ATV.app",
    icon=ICNS,
    # The family's id scheme (REPO-LAYOUT 5), as the shared kit gives it.
    bundle_identifier="com.openhamshack.atv",
    version=VERSION,
    info_plist={
        "CFBundleDisplayName": "OpenHamShack ATV",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        # Without it macOS gives the program silence for a microphone, with
        # no prompt and no error.
        "NSMicrophoneUsageDescription": "OpenHamShack ATV can send your microphone as the sound of the "
                                        "transmitted picture.",
        "NSHighResolutionCapable": True,
        # NumPy 2.5's arm64 wheels need macOS 14 (as NB found).
        "LSMinimumSystemVersion": "14.0",
        "LSApplicationCategoryType": "public.app-category.utilities",
        "NSHumanReadableCopyright": "GPL-3.0-or-later.",
    },
)
