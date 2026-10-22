"""python3 -m ohstxt.live SERVICE_DIR [--once] [--interval S] [--plugin MODULE]...

Keeps the live pages in a service folder filled (DESIGN.md "Live pages").
Runs until stopped (SIGTERM or Ctrl+C); --once fills every page one time,
waiting for its sources, and exits - for cron, and to see a page filled
before leaving the runner to it. Logs to stdout, which systemd's journal
takes (packaging/openhamshack-teletext-live.service).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m ohstxt.live", description="Fill live teletext pages")
    ap.add_argument("folder", help="the service folder")
    ap.add_argument("--once", action="store_true", help="fill every live page once and exit")
    ap.add_argument("--interval", type=float, default=2.0, help="seconds between passes (2)")
    ap.add_argument("--plugin", action="append", default=[], help="a module that registers more source types")
    a = ap.parse_args(argv)
    folder = Path(a.folder).expanduser()
    if not folder.is_dir():
        print(f"{folder}: not a folder", file=sys.stderr)
        return 2
    from .runner import Runner

    def log(msg):
        print(msg, flush=True)

    r = Runner(folder, plugins=a.plugin, log=log)
    pages = r.live_pages()
    log(f"ohstxt.live: {folder}, {len(pages)} live page{'s' if len(pages) != 1 else ''}")
    r.run(interval=max(0.2, a.interval), once=a.once)
    if a.once:
        for p in r.written:
            log(f"wrote {p.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
