# PyInstaller spec for ATV's AppImage, through the shared kit (ohs-libs
# packaging/appimage, `[pyinstaller] spec`): the kit's own spec is for
# web-UI apps and leaves Qt out. Shaped on QO-100 NB's
# (~/openhamshack-nb packaging/appimage/qt-app.spec), whose build proved it.
#
# Everything comes from the kit's stage ($SHACK_STAGE): src/ holds ohsatv,
# ohstxt (the Teletext editor), the pinned shared libraries and
# packaging/openhamshack-atv.svg (tools/build-appimage.sh put them there),
# app.json says which app, launcher.py is the entry. As the Windows spec
# does: every data file the packages carry (VERSION, the translation
# catalogs, the Teletext font and sample pages, the band-plan dataset...),
# every module named (the devices, the dialogs and the whole Teletext
# editor are imported lazily, when the operator asks for them), no Qt PDF
# (packaging/windows/bundle_prune.py says why) and nothing from tkinter.
#
# Pillow is in, unlike the first Windows spec: the Teletext editor's image
# import, PNG export and copy-as-picture use it, and an exception in a Qt
# slot ends the whole program - the transceiver with it.
import json
import os
import sys
from pathlib import Path

stage = Path(os.environ["SHACK_STAGE"]).resolve()
src = stage / "src"
sys.path.insert(0, str(src))
app = json.loads((stage / "app.json").read_text())

datas, hidden = [], []
for pkg in sorted(p.name for p in src.iterdir() if p.is_dir()):
    for p in sorted((src / pkg).rglob("*")):
        if p.is_file() and p.suffix not in (".py", ".pyc") and "__pycache__" not in p.parts \
                and not p.name.endswith(("_en_XA.ts", "_en_XA.qm")):
            datas.append((str(p), str(p.parent.relative_to(src))))
    # Named from the files, not by importing them as collect_submodules
    # does: in the build container a window module cannot import (no
    # libGL), and a module that fails to import is silently left out
    # (NB lost a module that way in its first build).
    for p in sorted((src / pkg).rglob("*.py")):
        parts = list(p.relative_to(src).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts and "tests" not in parts:
            hidden.append(".".join(parts))
# libiio's bindings (pylibiio; libiio itself is the host's, as the .deb
# recommends it) and PyAudio are imported only when a Pluto or the sound
# card is opened.
hidden += ["iio", "pyaudio", "PIL.Image", "PIL.ImageDraw"]

a = Analysis(  # noqa: F821
    [str(stage / "launcher.py")],
    pathex=[str(src)],
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "SoapySDR", "scipy", "matplotlib", "IPython", "webview", "gi"],
    noarchive=False,
)
# No Qt PDF: 15 MB of a module nothing imports (bundle_prune.py).
a.binaries = [b for b in a.binaries if "Qt6Pdf" not in b[0] and "qpdf" not in b[0].lower()]
a.datas = [d for d in a.datas if "Qt6Pdf" not in d[0] and "qpdf" not in d[0].lower()]
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=app["executable"], console=False,  # noqa: F821
          upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name=app["executable"], upx=False)  # noqa: F821
