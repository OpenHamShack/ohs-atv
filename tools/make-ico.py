#!/usr/bin/env python3
"""Write OpenHamShack ATV's .ico from the same icon its window uses.

    python tools/make-ico.py out.ico

Windows wants an .ico for the .exe, the Start-menu shortcut and the
installer. The program's one icon is packaging/openhamshack-atv.svg, which
ohsatv.ui.appicon.app_icon() loads (or paints, without Qt's SVG plugin), so
rather than commit a binary copy that would drift from it, the Windows build
renders that icon at the sizes Explorer asks for and packs them as
PNG-compressed ICO entries - valid for every Windows since Vista, and no
image library beyond Qt needed.

Copied from OpenHamShack DATV's tools/make-ico.py (itself NB's, GPLv3).
"""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def png_bytes(pixmap) -> bytes:
    from PyQt6.QtCore import QBuffer, QIODevice
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    pixmap.save(buf, "PNG")
    return bytes(buf.data())


def build_ico(images: list[tuple[int, bytes]]) -> bytes:
    """ICONDIR, one ICONDIRENTRY per image, then the PNGs. A 256 px entry is
    written as 0 in the one-byte width/height fields, as the format says."""
    head = struct.pack("<HHH", 0, 1, len(images))
    offset = len(head) + 16 * len(images)
    entries, blobs = b"", b""
    for size, data in images:
        b = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", b, b, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    return head + entries + blobs


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    if len(args) != 1:
        print("usage: make-ico.py out.ico", file=sys.stderr)
        return 2
    from PyQt6.QtGui import QGuiApplication
    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841 - pixmaps need one
    from ohsatv.ui.appicon import app_icon
    icon = app_icon()
    images = []
    for s in SIZES:
        pm = icon.pixmap(s, s)
        if pm.width() != s:
            pm = pm.scaled(s, s)
        images.append((s, png_bytes(pm)))
    Path(args[0]).write_bytes(build_ico(images))
    print(f"wrote {args[0]} ({', '.join(str(s) for s in SIZES)} px)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
