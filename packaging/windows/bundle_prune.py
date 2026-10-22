"""What both PyInstaller specs take back out of the bundle after Analysis.

Qt PDF, from 0.1.4 on. PyInstaller's QtGui hook collects every Qt
image-format plugin, and one of them, qpdf.dll, renders PDF pages as images;
it pulls in Qt6Pdf.dll behind it. Neither program opens a PDF - their images
are the .ico and .svg they ship, which the qico and qsvg plugins read - so
the pair was dead weight. They were also expensive weight: Qt PDF is built
from the Qt WebEngine source module, so carrying it obliged us (LGPL-3) to
host the 580 MB qtwebengine tarball beside every installer that had it, for
two DLLs nothing calls. Nothing else in the bundle links against Qt6Pdf:
Qt6Svg, the other image-format plugins and PyQt6's QtCore/QtGui/QtWidgets
need only Core, Gui and Widgets.

Done by name after Analysis rather than through `excludes` because excludes
names Python modules: it can keep PyQt6.QtPdf out, but the plugin and its
DLL arrive through the QtGui hook's plugin collection, which no module
exclude reaches. tools/build-windows.ps1 fails a build whose dist folder
still has either DLL, so a PyInstaller or PyQt6 upgrade that brings them
back by another route stops there instead of quietly re-creating the source
obligation.

A plain module beside the specs, not code pasted into each, so the tests
can run the same filter the build runs.
"""
from __future__ import annotations

import fnmatch


def is_qt_pdf(dest: str) -> bool:
    """True for a bundle path that belongs to Qt PDF. `dest` is a TOC
    entry's destination name, with either separator (PyInstaller uses the
    host's, so backslashes on Windows)."""
    path = dest.replace("\\", "/").lower()
    name = path.rsplit("/", 1)[-1]
    if fnmatch.fnmatch(name, "qt6pdf*.dll"):                  # Qt6Pdf, Qt6PdfQuick, Qt6PdfWidgets
        return True
    if path.endswith("imageformats/qpdf.dll"):
        return True
    # The Mac build (packaging/macos/) has the same pair under Mac names:
    # QtPdf.framework (anything inside it, or a link to it) and the
    # libqpdf.dylib image-format plugin.
    if "qtpdf.framework" in path or path.endswith("imageformats/libqpdf.dylib"):
        return True
    if "pyqt6/" in path and fnmatch.fnmatch(name, "qtpdf*.so"):
        return True
    # PyQt6's bindings for it, should anything ever import them.
    return "pyqt6/" in path and fnmatch.fnmatch(name, "qtpdf*.pyd")


def without_qt_pdf(toc):
    """The TOC (a.binaries or a.datas: (dest, source, typecode) entries)
    without Qt PDF, as a new list of the same entries."""
    # The source is looked at too: on macOS PyInstaller adds a SYMLINK entry
    # named plain "QtPdf" at the bundle's root whose source is the link's
    # target inside QtPdf.framework, and left in, it dangles.
    return [entry for entry in toc if not (is_qt_pdf(entry[0]) or is_qt_pdf(str(entry[1])))]
