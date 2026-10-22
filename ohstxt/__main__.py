"""python3 -m ohstxt [FOLDER | FILE.tti] [--page NNN]
python3 -m ohstxt --t42 OUT.t42 FOLDER       (no window: a service as packets)
python3 -m ohstxt --png OUT.png FILE.tti     (no window: a page as a picture)

Opens the window on a service folder (default: the last one, or
~/.local/share/openhamshack-teletext/service, created with the sample pages on
first run). A .tti file opens its folder at that page. The two headless
conversions are for scripts - and for OpenHamShack ATV's side, to check a folder
turns into packets without starting a window.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m ohstxt", description="OpenHamShack Teletext, a teletext page editor")
    ap.add_argument("path", nargs="?", help="a service folder, or a .tti page in one")
    ap.add_argument("--page", help="open at this page number (e.g. 100)")
    ap.add_argument("--t42", metavar="OUT", help="write the folder (or page) as .t42 packets and exit")
    ap.add_argument("--png", metavar="OUT", help="write the page (first subpage) as a PNG and exit")
    ap.add_argument("--version", action="store_true")
    a = ap.parse_args(argv)
    if a.version:
        from . import __version__
        print(__version__)
        return 0
    if a.t42 or a.png:
        from . import t42, tti
        from .service import Service
        if not a.path:
            print("--t42 and --png need a folder or a .tti file", file=sys.stderr)
            return 2
        p = Path(a.path)
        pages = [tti.load(p)] if p.is_file() else Service.open(p).pages
        if not pages:
            print(f"no .tti pages in {p}", file=sys.stderr)
            return 1
        if a.t42:
            t42.save(pages, a.t42)
            print(f"wrote {a.t42}: {len(pages)} pages, {len(t42.dumps(pages)) // 42} packets")
        if a.png:
            import time

            from PIL import Image

            from . import decoder, header, render
            pg = pages[0]
            sub = pg.subpages[0]
            rows = [bytearray(r) for r in sub.rows]
            rows[0] = bytearray(header.display_row0(rows[0], pg.number, time.time()))
            rgb = render.render(decoder.decode(rows), mixed=bool(sub.status & 3))
            Image.fromarray(render.stretch(rgb, render.TV_ASPECT, 2)).save(a.png)
            print(f"wrote {a.png}")
        return 0
    try:
        from .ui import run
    except ImportError as e:
        print(f"The window needs PyQt6 (Debian: python3-pyqt6): {e}", file=sys.stderr)
        return 2
    return run(a.path, a.page)


if __name__ == "__main__":
    sys.exit(main())
