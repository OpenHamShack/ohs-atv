"""Live pages: pages whose cells are filled from data - the station's clock,
solar and propagation figures, DX cluster spots, a beacon status board - and
rewritten on each source's schedule by a headless runner,

    python3 -m ohstxt.live SERVICE_DIR

which OpenHamShack ATV (watching the folder) then transmits within a carousel
turn. DESIGN.md "Live pages" is the contract; in short:

- A live page is an ordinary .tti, always complete and always valid -
  filled with the last good data - so a plain .tti reader never needs to
  know a page is live. Beside it, `<name>.live.json` (P301.tti ->
  P301.live.json) holds the **template**: the rows that hold tokens, as
  .tti OL text, per subpage, and any page-level data sources.
- Tokens are written in the cells they fill (`{sfi}`, `{>dx.1.freq~~}`;
  ohstxt.live.template has the grammar). Everything else about the page -
  its other rows, links, subpages, flags - lives only in the .tti, so the
  editor and the runner never disagree about it.
- The runner writes a page only when its bytes change, atomically (a
  temporary file, then a rename), so a watcher never reads half a page and
  is not woken for nothing.
- A source that fails keeps its last good values; the page says "No data
  since HH:MM" where the template put `{status}` (or `{<source>.status}`),
  or at the right of row 23 if it put neither. Nothing that happens to a
  network source can blank a page or stop the runner.

This module holds what the editor and the runner share: the sidecar, the
data board that resolves token names, and rendering a page.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import tti
from ..page import COLS, ROWS, Page
from . import sources as _sources
from .template import fill_row, names_in, tokens_in, tokens_in_row  # noqa: F401  (re-exported)

SIDECAR_SUFFIX = ".live.json"
FORMAT = "ohstxt-live/1"
STATE_FILE = ".ohstxt-live-state.json"

# The sources every service has unless station.json says otherwise. Only
# the ones a live page actually uses are ever fetched.
DEFAULT_SOURCES = {
    "station": {"type": "station"},
    "solar": {"type": "solar"},
    "dx": {"type": "dxcluster"},
    "beacons": {"type": "beacons", "file": "beacons.csv"},
}
NOTICE_COLOUR = 0x03            # alpha yellow, for the row-23 fallback notice


# ---------------------------------------------------------------- the sidecar
@dataclass
class Sidecar:
    subpages: list[dict[int, str]] = field(default_factory=list)   # per subpage: row -> OL text
    sources: dict = field(default_factory=dict)                     # page-level sources, by name
    extra: dict = field(default_factory=dict)                       # keys this version does not use, kept


def sidecar_path(tti_path) -> Path:
    p = Path(tti_path)
    return p.with_name(p.stem + SIDECAR_SUFFIX)


def load_sidecar(path) -> Sidecar:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("format", FORMAT) != FORMAT:
        raise ValueError(f"{Path(path).name}: not a {FORMAT} sidecar")
    subs = []
    for s in raw.get("subpages", []):
        rows = {}
        for k, v in (s.get("rows", {}) if isinstance(s, dict) else {}).items():
            try:
                r = int(k)
            except ValueError:
                continue
            if 0 <= r < ROWS and isinstance(v, str):
                rows[r] = v
        subs.append(rows)
    srcs = raw.get("sources", {})
    extra = {k: v for k, v in raw.items() if k not in ("format", "page", "subpages", "sources")}
    return Sidecar(subpages=subs, sources=srcs if isinstance(srcs, dict) else {}, extra=extra)


def dumps_sidecar(sc: Sidecar, page_number: int | None = None) -> str:
    data = {"format": FORMAT}
    if page_number is not None:
        data["page"] = f"{page_number:03X}"
    if sc.sources:
        data["sources"] = sc.sources
    data["subpages"] = [{"rows": {str(r): text for r, text in sorted(rows.items())}} for rows in sc.subpages]
    data.update(sc.extra)
    return json.dumps(data, indent=1, ensure_ascii=True) + "\n"


def _atomic_write(path: Path, data: bytes, prefix: str):
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=prefix, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def template_of(page: Page, sc: Sidecar | None = None) -> Sidecar:
    """The sidecar for a page being edited: the rows that hold tokens, as
    OL text, per subpage (page-level sources carried over from `sc`)."""
    subs = []
    for s in page.subpages:
        subs.append({r: tti.encode_ol(s.rows[r]) for r in range(ROWS) if tokens_in_row(s.rows[r])})
    return Sidecar(subpages=subs, sources=dict(sc.sources) if sc else {}, extra=dict(sc.extra) if sc else {})


def attach(page: Page, sc: Sidecar):
    """Open a live page for editing: its template rows replace the filled
    rows, so the editor shows (and edits) the tokens."""
    for i, rows in enumerate(sc.subpages):
        if i < len(page.subpages):
            for r, text in rows.items():
                page.subpages[i].rows[r] = tti.decode_ol(text)
    page.live = sc


def attach_service(service) -> list[str]:
    """Attach every sidecar in a service's folder to its page. Returns the
    sidecars that could not be read."""
    problems = []
    for pg in service.pages:
        if not pg.filename:
            continue
        p = sidecar_path(service.folder / pg.filename)
        if p.is_file():
            try:
                attach(pg, load_sidecar(p))
            except (OSError, ValueError) as e:
                problems.append(f"{p.name}: {e}")
    return problems


# ---------------------------------------------------------------- data
class Feed:
    """One source and what it last said: the last good values and when,
    and the error from the latest attempt if it failed."""

    def __init__(self, source):
        self.source = source
        self.values: dict[str, str] = {}
        self.good_at: float | None = None
        self.error: str | None = None
        self.attempted_at: float | None = None
        self.next_due = 0.0

    @property
    def stale(self) -> bool:
        return self.error is not None or self.good_at is None

    def notice(self) -> str:
        if not self.stale:
            return ""
        if self.good_at is None:
            return "No data yet"
        return "No data since " + time.strftime("%H:%M", time.localtime(self.good_at))

    def poll(self, now: float | None = None) -> bool:
        """Fetch now. Returns True if what the feed says changed."""
        now = time.time() if now is None else now
        before = (self.values, self.error)
        self.apply(now, *self.fetch())
        return (self.values, self.error) != before

    def fetch(self):
        try:
            return dict(self.source.fetch()), None
        except Exception as e:                    # noqa: BLE001 - a source failing must never stop the runner
            return None, (str(e) or e.__class__.__name__)

    def apply(self, now: float, values, error):
        self.attempted_at = now
        self.next_due = now + self.source.every
        if error is None:
            self.values = {k: str(v) for k, v in values.items()}
            self.good_at = now
            self.error = None
        else:
            self.error = error

    def state(self) -> dict:
        return {"type": self.source.type_name, "values": self.values, "good_at": self.good_at,
                "error": self.error, "attempted_at": self.attempted_at}

    def restore(self, st: dict):
        if isinstance(st.get("values"), dict):
            self.values = {str(k): str(v) for k, v in st["values"].items()}
        self.good_at = st.get("good_at")
        self.error = st.get("error")
        self.attempted_at = st.get("attempted_at")


def feed_key(name: str, config: dict) -> str:
    h = hashlib.sha1(json.dumps(config, sort_keys=True).encode()).hexdigest()[:10]
    return f"{name}|{h}"


class Board:
    """Every feed a service's live pages can draw on, and the token names
    they answer to. The window builds one to preview (with sample data, or
    the runner's last values from the state file); the runner builds one
    and keeps it fetching."""

    def __init__(self, folder, station, plugins=()):
        self.folder = Path(folder)
        self.station = station
        self.errors = _sources.load_plugins(list(station.plugins) + list(plugins))
        self.service_sources = dict(DEFAULT_SOURCES)
        if isinstance(station.sources, dict):
            for k, v in station.sources.items():
                if isinstance(v, dict):
                    self.service_sources[k] = v
        self.feeds: dict[str, Feed | None] = {}
        self._saved: dict[str, dict] = {}          # the state file's feeds, restored as feeds are made
        self.sample = False      # every value from the sources' sample data (the editor's "sample" view)
        self.preview = False     # a feed with no data yet shows sample data (the editor's "current" view)

    def scope(self, sc: Sidecar | None) -> list[tuple[str, Feed]]:
        """(name, feed) in the order bare names are looked up: the page's
        own sources first, then the service's."""
        out = []
        merged = dict(self.service_sources)
        page_srcs = sc.sources if sc and isinstance(sc.sources, dict) else {}
        merged.update({k: v for k, v in page_srcs.items() if isinstance(v, dict)})
        order = [k for k in page_srcs if k in merged] + [k for k in merged if k not in page_srcs]
        for name in order:
            f = self.feed(name, merged[name])
            if f is not None:
                out.append((name, f))
        return out

    def feed(self, name: str, config: dict) -> Feed | None:
        key = feed_key(name, config)
        f = self.feeds.get(key)
        if f is None:
            try:
                src = _sources.make(name, config, self.station, self.folder)
            except _sources.SourceError as e:
                self.errors.append(f"source {name}: {e}")
                self.feeds[key] = None
                return None
            f = Feed(src)
            if isinstance(self._saved.get(key), dict):
                f.restore(self._saved[key])
            self.feeds[key] = f
        return f

    def lookup(self, name: str, scope) -> tuple[str | None, Feed | None]:
        """A token name's value and the feed it came from (None, None if
        no source has it). 'status' is answered by the caller."""
        head, _, rest = name.partition(".")
        for fname, f in scope:
            if fname == head and rest:
                return self._value(f, rest), f
        for fname, f in scope:
            if name in f.source.field_names() or name in f.values:
                return self._value(f, name), f
        return None, None

    def _value(self, f: Feed, field_name: str) -> str | None:
        if field_name == "status":
            return f.notice()
        if self.sample:
            return f.source.sample().get(field_name)
        if f.good_at is None:
            # never fetched here: the editor's preview falls back to sample
            # data so a template can be laid out before the runner has run
            return f.source.sample().get(field_name) if self.preview else None
        return f.values.get(field_name, "")

    # ------------------------------------------------------------ rendering
    def render(self, page: Page, sc: Sidecar) -> Page:
        """The page with its template rows filled. The page passed in is
        not changed."""
        out = page.copy()
        out.live = None
        scope = self.scope(sc)
        for i, rows in enumerate(sc.subpages):
            if i >= len(out.subpages):
                break
            sub = out.subpages[i]
            codes = {r: tti.decode_ol(text) for r, text in rows.items()}
            names = [t.name for r in codes for t in tokens_in_row(codes[r])]
            used = []
            for n in names:
                if n == "status" or n.endswith(".status"):
                    continue
                _, f = self.lookup(n, scope)
                if f is not None and f not in used:
                    used.append(f)
            if self.sample:
                stale = []
            elif self.preview:
                stale = [f for f in used if f.error is not None]
            else:
                stale = [f for f in used if f.stale]
            notice = ""
            if stale:
                never = [f for f in stale if f.good_at is None]
                if never:
                    notice = "No data yet"
                else:
                    oldest = min(stale, key=lambda f: f.good_at)
                    notice = oldest.notice()

            def value(n):
                if n == "status":
                    return notice
                v, _ = self.lookup(n, scope)
                return v

            for r, row in codes.items():
                sub.rows[r], _ = fill_row(row, value)
            if notice and not any(n == "status" or n.endswith(".status") for n in names):
                text = bytes([NOTICE_COLOUR]) + notice.encode("ascii")
                sub.rows[23][COLS - len(text):] = text
        return out

    def render_rows(self, page: Page, sub_index: int, rows) -> list[bytearray]:
        """The editor's rows with their tokens filled, for the preview."""
        sc = template_of(page, page.live)
        probe = Page(number=page.number, subpages=[page.subpages[sub_index].copy()])
        probe.subpages[0].rows = [bytearray(r) for r in rows]
        one = Sidecar(subpages=[{r: tti.encode_ol(rows[r]) for r in range(ROWS) if tokens_in_row(rows[r])}],
                      sources=sc.sources)
        return self.render(probe, one).subpages[0].rows

    # ------------------------------------------------------------ the runner's state
    def load_state(self) -> bool:
        """Read the runner's last values (for the editor's "current data",
        and so a restarted runner keeps the last good data)."""
        try:
            raw = json.loads((self.folder / STATE_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        feeds = raw.get("feeds", {}) if isinstance(raw, dict) else {}
        if not isinstance(feeds, dict):
            return False
        self._saved = feeds
        for key, f in self.feeds.items():
            if f is not None and isinstance(feeds.get(key), dict):
                f.restore(feeds[key])
        return True

    def save_state(self):
        data = {"format": "ohstxt-live-state/1", "written": time.time(),
                "feeds": {k: f.state() for k, f in self.feeds.items() if f is not None and f.attempted_at}}
        try:
            _atomic_write(self.folder / STATE_FILE, json.dumps(data, indent=1).encode(), ".ohstxt-live-")
        except OSError:
            pass


# ---------------------------------------------------------------- saving from the editor
def save_page(folder, page: Page, board: Board | None = None) -> Path:
    """Save a live page from the editor: its template to the sidecar, and
    the .tti filled with the runner's last values (never sample data: that
    would put made-up figures on the air until the runner next writes)."""
    from .. import station as _station
    folder = Path(folder)
    path = folder / (page.filename or f"P{page.number:03X}.tti")
    sc = template_of(page, page.live)
    if board is None:
        board = Board(folder, _station.load(folder))
        board.load_state()
    # Local sources (the station, the beacon file) are real data whenever
    # they are read, so they are read now rather than saved as dashes.
    scope = board.scope(sc)
    for rows in sc.subpages:
        for text in rows.values():
            for tok in tokens_in_row(tti.decode_ol(text)):
                _, f = board.lookup(tok.name, scope)
                if f is not None and f.source.local and f.good_at is None:
                    f.poll()
    keep = board.sample, board.preview
    board.sample = board.preview = False
    try:
        filled = board.render(page, sc)
    finally:
        board.sample, board.preview = keep
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(sidecar_path(path), dumps_sidecar(sc, page.number).encode(), ".")
    tti.save(filled, path)
    page.filename = path.name
    page.live = sc
    return path


def remove_sidecar(folder, page: Page):
    if page.filename:
        try:
            sidecar_path(Path(folder) / page.filename).unlink()
        except FileNotFoundError:
            pass
