"""The headless runner: keeps every live page in a service folder filled.

Each step (two seconds apart by default) it

1. looks for sidecars in the folder - a live page added, changed or
   removed by the editor is noticed without a restart;
2. starts a fetch for every feed that a live page uses and that is due,
   each on a worker thread, so a slow cluster cannot hold up the clock;
3. takes in the fetches that have finished;
4. renders every live page and writes the ones whose bytes changed,
   atomically, keeping the runner's state file up to date so a restart
   (or the editor's "current data" preview) starts from the last good data.

A page is read from disk on every step, not kept: the editor may have
saved it, and the runner must only ever replace the rows the template
owns. Nothing a source does - an exception, a hang (sockets carry
timeouts), garbage - escapes `Feed.fetch`; nothing a page does (a bad
sidecar, a missing .tti) stops the others.
"""
from __future__ import annotations

import signal
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from .. import station as _station
from .. import tti
from . import SIDECAR_SUFFIX, Board, Feed, load_sidecar, tokens_in_row


class Runner:
    def __init__(self, folder, plugins=(), log=print, workers: int = 4):
        self.folder = Path(folder)
        self.plugins = list(plugins)
        self.log = log
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ohstxt-live")
        self.pending: dict[Feed, Future] = {}
        self.stop = False
        self._station_mtime = None
        self.board: Board | None = None
        self._reported: dict[str, str] = {}
        self.written: list[Path] = []
        self._load_board()

    def _load_board(self):
        st = _station.load(self.folder)
        old = self.board
        self.board = Board(self.folder, st, self.plugins)
        self.board.load_state()
        if old is not None:
            # keep feeds whose configuration did not change (and their data)
            for k, f in old.feeds.items():
                if k in self.board.feeds or f is None:
                    continue
                self.board.feeds[k] = f
        for e in self.board.errors:
            self.log(e)
        try:
            self._station_mtime = (self.folder / _station.FILENAME).stat().st_mtime
        except OSError:
            self._station_mtime = None

    def live_pages(self) -> list[tuple[Path, Path]]:
        """(the .tti, its sidecar) for every live page in the folder."""
        out = []
        for sc in sorted(self.folder.glob("*" + SIDECAR_SUFFIX)):
            if sc.name.startswith("."):
                continue
            page = sc.with_name(sc.name[:-len(SIDECAR_SUFFIX)] + ".tti")
            if page.is_file():
                out.append((page, sc))
        return out

    def step(self, now: float | None = None, wait: bool = False) -> list[Path]:
        """One pass. `wait` finishes this pass's fetches before rendering
        (the tests' way; the loop does not wait)."""
        now = time.time() if now is None else now
        try:
            mt = (self.folder / _station.FILENAME).stat().st_mtime
        except OSError:
            mt = None
        if mt != self._station_mtime:
            self._load_board()
        pages = []
        for tti_path, sc_path in self.live_pages():
            try:
                pages.append((tti_path, tti.load(tti_path), load_sidecar(sc_path)))
            except (OSError, ValueError) as e:
                self._say(sc_path.name, f"{sc_path.name}: {e}")
        # which feeds are in use, and start the due ones
        used: list[Feed] = []
        for _, _, sc in pages:
            scope = self.board.scope(sc)
            for rows in sc.subpages:
                for text in rows.values():
                    for tok in tokens_in_row(tti.decode_ol(text)):
                        if tok.name == "status":
                            continue
                        _, f = self.board.lookup(tok.name, scope)
                        if f is not None and f not in used:
                            used.append(f)
        for f in used:
            if f not in self.pending and now >= f.next_due:
                f.next_due = now + f.source.every          # not again until this one is done and due
                self.pending[f] = self.pool.submit(f.fetch)
        changed = self._collect(now, wait)
        if changed:
            self.board.save_state()
        # render and write what changed
        written = []
        for path, page, sc in pages:
            try:
                filled = self.board.render(page, sc)
                data = tti.dumps(filled)
                if path.read_bytes() != data:
                    tti.save(filled, path)
                    written.append(path)
            except Exception as e:                    # noqa: BLE001 - one bad page must not stop the rest
                self._say(path.name, f"{path.name}: not written: {e}")
        self.written = written
        return written

    def _collect(self, now: float, wait: bool) -> bool:
        changed = False
        for f, fut in list(self.pending.items()):
            if wait:
                fut.result()
            if not fut.done():
                continue
            del self.pending[f]
            values, error = fut.result()
            before = (f.values, f.error)
            f.apply(now, values, error)
            if (f.values, f.error) != before or f.attempted_at == now:
                changed = True
            name = f.source.name
            if error:
                self._say(name, f"source {name}: {error}; keeping data from "
                          f"{time.strftime('%H:%M', time.localtime(f.good_at)) if f.good_at else 'nowhere'}")
            else:
                self._say(name, f"source {name}: ok" if self._reported.get(name) else "")
        return changed

    def _say(self, key: str, msg: str):
        """Log a change of state once, not every pass."""
        if self._reported.get(key, "") != msg:
            self._reported[key] = msg
            if msg:
                self.log(msg)

    def run(self, interval: float = 2.0, once: bool = False):
        def _stop(*_):
            self.stop = True
        try:
            signal.signal(signal.SIGTERM, _stop)
            signal.signal(signal.SIGINT, _stop)
        except ValueError:
            pass                                      # not the main thread (tests)
        try:
            if once:
                self.step(wait=True)
                return
            while not self.stop:
                for p in self.step():
                    self.log(f"wrote {p.name}")
                end = time.monotonic() + interval
                while not self.stop and time.monotonic() < end:
                    time.sleep(0.1)
        finally:
            self.board.save_state()
            self.pool.shutdown(wait=False, cancel_futures=True)
