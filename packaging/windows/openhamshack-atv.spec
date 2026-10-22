# -*- mode: python -*-
# PyInstaller spec for the Windows build. Run by tools/build-windows.ps1,
# which sets OHSATV_VENDOR (the folder it fetched libiio, ffmpeg, libhackrf
# and the licence texts into) and OHSATV_ICO (the icon it rendered with
# tools/make-ico.py). Copied in shape from OpenHamShack DATV's
# packaging/windows/openhamshack-datv.spec, itself NB's (GPLv3), whose ports
# proved it; the licence check after Analysis is NB's (bundle_licences.py).
#
# onedir, not onefile: a onefile .exe unpacks itself into %TEMP% on every
# start - seconds of delay for Qt's size, and a fresh DLL tree for
# antivirus to scan each time - and the installer has to lay files down
# anyway, so there is nothing for onefile to save.
#
# Two executables share one folder: openhamshack-atv.exe is the windowed program
# the Start menu runs (no console behind it); openhamshack-atv-console.exe is the
# same program with a console, for --headless, for the installer's
# --radio-lock-folder check, and for reading what the engine prints when
# something goes wrong. A windowed .exe has no stdout at all.
#
# The layout under _internal (sys._MEIPASS) is ohsatv/host.py's, and that
# module is the authority: ffmpeg\, libiio\ and hackrf\, each a set of
# upstream files kept in a folder of its own.
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

sys.path.insert(0, SPECPATH)                       # noqa: F821 - for the helpers beside this file
from bundle_licences import with_licences           # noqa: E402
from bundle_prune import without_qt_pdf             # noqa: E402

ROOT = Path(SPECPATH).resolve().parents[1]          # noqa: F821 - SPECPATH is PyInstaller's
VENDOR = Path(os.environ["OHSATV_VENDOR"])
ICO = os.environ["OHSATV_ICO"]

# Every file under ohsatv\ and ohstxt\ (the Teletext editor) that is not
# Python (VERSION, the translation catalogs), found rather than listed: a
# data file added later and forgotten here would fail only on Windows, at
# the moment the feature that reads it is used.
datas = []
for p in sorted([*(ROOT / "ohsatv").rglob("*"), *(ROOT / "ohstxt").rglob("*")]):
    if p.is_file() and p.suffix not in (".py", ".pyc") and "__pycache__" not in p.parts:
        datas.append((str(p), str(p.parent.relative_to(ROOT)).replace("\\", "/")))
datas += [
    (ICO, "."),
    # Where ohsatv.ui.appicon.svg_path() looks beside the package, as in the
    # repository: the window's icon is the same drawing as the .ico's.
    (str(ROOT / "packaging" / "openhamshack-atv.svg"), "packaging"),
    (str(ROOT / "LICENSE"), "."),
    (str(ROOT / "packaging" / "windows" / "THIRD-PARTY.txt"), "."),
]
# The shared OpenHamShack libraries' data (shack-bandplan's dataset...),
# and their names, from what build-windows.ps1 unpacked.
sys.path.insert(0, str(VENDOR / "shack"))
SHACK = sorted(p.name for p in (VENDOR / "shack").iterdir() if p.is_dir())
for p in sorted((VENDOR / "shack").rglob("*")):
    if p.is_file() and p.suffix not in (".py", ".pyc") and "__pycache__" not in p.parts:
        datas.append((str(p), str(p.parent.relative_to(VENDOR / "shack")).replace("\\", "/")))
datas += [(str(p), "licences") for p in sorted((VENDOR / "licences").glob("*")) if p.is_file()]


def tree(sub, pattern="*"):
    """vendor\\<sub>\\* as data into _internal\\<sub>.

    Data, not binaries, on purpose (NB's lesson with libiio): PyInstaller's
    binary analysis would go looking for each DLL's imports itself, take
    them from wherever it finds them - System32 on a build machine with
    ADI's installer - and may hoist them to the bundle root, where anything
    searching the program's folder picks them up. As data, each set lands
    exactly as fetched, side by side, and Windows resolves each one's
    imports from its own folder first."""
    return [(str(p), sub) for p in sorted((VENDOR / sub).glob(pattern)) if p.is_file()]


# libiio 0.26 (libiio, libxml2, libusb-1.0, libserialport), unmodified from
# ADI's 0.26 Windows release; ohsatv.host.bundled_libiio().
datas += tree("libiio", "*.dll")
# ffmpeg.exe and ffprobe.exe: static builds, no DLLs beside them;
# ohsatv.host.find_tool() looks here before PATH. (Not ffplay: nothing here
# plays to a window of ffmpeg's own.)
datas += tree("ffmpeg", "*.exe")
# hackrf-0.dll with the libusb-1.0.dll and libwinpthread-1.dll it imports,
# unmodified from conda-forge's packages; ohsatv.host.hackrf_library() loads
# it by full path.
datas += tree("hackrf", "*.dll")

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "windows" / "launch.py")],
    # VENDOR/py holds ADI's pure-Python iio.py (libiio 0.26's bindings); the
    # libiio.dll it loads is the one in _internal\libiio (above).
    # VENDOR/shack holds the pinned shared OpenHamShack libraries, unpacked
    # as plain packages (packaging/shack_vendor.py says why not installed).
    pathex=[str(ROOT), str(VENDOR / "py"), str(VENDOR / "shack")],
    binaries=[],
    datas=datas,
    # The whole of ohsatv, because much of it is imported inside functions
    # only when the operator picks it (the devices, the window's dialogs,
    # the teletext tools): named one by one here, a module added later would
    # be missing from the .exe and only from the .exe. collect_submodules
    # lists modules without importing them, so Linux-only ones (fcntl inside
    # functions) are harmless. Then the optional third-party modules those
    # import lazily: libiio's bindings, PyAudio, and Paramiko (the radio
    # lock's SSH on Windows).
    # ohstxt, the Teletext editor, the same way: the window imports it only
    # when its button is pressed - and with it Pillow, which the editor's
    # image import, PNG export and copy-as-picture use (an exception in a Qt
    # slot ends the whole program, the transceiver with it). The shared
    # OpenHamShack libraries (VENDOR/shack, unpacked by
    # packaging/shack_vendor.py) likewise: ohsatv.shacklibs imports them by
    # name.
    hiddenimports=(collect_submodules("ohsatv") + collect_submodules("ohstxt")
                   + [m for name in SHACK for m in collect_submodules(name)]
                   + ["iio", "pyaudio", "paramiko", "PIL.Image", "PIL.ImageDraw"]),
    excludes=["tkinter", "SoapySDR", "scipy", "matplotlib", "IPython"],
    noarchive=False,
)
# Qt PDF out: neither used nor wanted, and carrying it would oblige us to
# host Qt WebEngine's source beside the installer (bundle_prune.py, NB's).
a.binaries = without_qt_pdf(a.binaries)
a.datas = without_qt_pdf(a.datas)
# The licence texts of the Python packages actually collected, and of
# Python and PyInstaller, into licences/ beside the C libraries' (which
# build-windows.ps1 fetched); then licences/ must match THIRD-PARTY.txt both
# ways, or the build stops (bundle_licences.py says why and how).
a.datas = with_licences(a, "atv", ROOT / "packaging" / "windows" / "THIRD-PARTY.txt",
                        Path(sys.base_prefix) / "LICENSE.txt")
pyz = PYZ(a.pure)  # noqa: F821

common = dict(exclude_binaries=True, debug=False, strip=False, upx=False, icon=ICO)
gui = EXE(pyz, a.scripts, [], name="openhamshack-atv", console=False, **common)  # noqa: F821
cli = EXE(pyz, a.scripts, [], name="openhamshack-atv-console", console=True, **common)  # noqa: F821

coll = COLLECT(gui, cli, a.binaries, a.datas, strip=False, upx=False, name="openhamshack-atv")  # noqa: F821
