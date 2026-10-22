"""Data sources for live pages, and the registry that makes them pluggable.

A source is a class with a type name, the fields it provides, how often it
wants to be asked, `fetch()` - which returns {field: text} or raises - and
`sample()`, made-up values for the editor's preview and the screenshots.
Fetching is the only thing that may fail, and failing is ordinary: the
runner keeps the last good values and the page says "no data since".

    from ohstxt.live.sources import Source, register

    @register("weather")
    class Weather(Source):
        every = 600
        def fields(self): return [("temp", "temperature")]
        def fetch(self): return {"temp": read_my_sensor()}
        def sample(self): return {"temp": "14C"}

A module like that is named in station.json's "plugins" list (or passed
with --plugin) and imported before the pages are read; its type can then
be used in "sources". The four built in are here and in solar.py and
cluster.py: station (local), solar (hamqsl.com's XML), dxcluster (telnet)
and beacons (a local file).
"""
from __future__ import annotations

import csv
import importlib
import time
from pathlib import Path

REGISTRY: dict[str, type] = {}


class SourceError(Exception):
    """A fetch that did not work, said in words for the log and the page."""


def register(type_name: str):
    def deco(cls):
        cls.type_name = type_name
        REGISTRY[type_name] = cls
        return cls
    return deco


def load_plugins(names) -> list[str]:
    """Import plugin modules; returns the errors, one line each (a broken
    plugin must not stop the pages that do not use it)."""
    errors = []
    for n in names or ():
        try:
            importlib.import_module(n)
        except Exception as e:                     # noqa: BLE001 - anything a plugin raises
            errors.append(f"plugin {n}: {e}")
    return errors


class Source:
    type_name = ""
    local = False           # True: reads nothing from a network, so fetching it while saving is safe
    every = 300.0           # seconds between fetches
    min_every = 1.0         # never more often than this, whatever the config says

    def __init__(self, name: str, config: dict, station, folder: Path | None = None):
        self.name = name
        self.config = dict(config or {})
        self.station = station
        self.folder = Path(folder) if folder else Path(".")
        try:
            self.every = max(self.min_every, float(self.config.get("every", self.every)))
        except (TypeError, ValueError):
            pass

    def fields(self) -> list[tuple[str, str]]:
        """(name, what it is) for the editor's list of fields."""
        return []

    def field_names(self) -> set[str]:
        return {n for n, _ in self.fields()}

    def fetch(self) -> dict[str, str]:
        raise NotImplementedError

    def sample(self) -> dict[str, str]:
        return {}


# ---------------------------------------------------------------- station
@register("station")
class StationSource(Source):
    """Who and where, and the date and time - nothing from outside, so it
    never fails. The clock is the machine's."""
    every = 20.0
    local = True

    def fields(self):
        return [("call", "callsign"), ("name", "operator's name"), ("locator", "Maidenhead locator"),
                ("qth", "QTH"), ("time", "local time HH:MM"), ("utc", "UTC HH:MM"),
                ("date", "date, e.g. Mon 05 Oct 2026"), ("day", "day name"), ("isodate", "2026-10-05")]

    def values_at(self, now: float) -> dict[str, str]:
        st = self.station
        lt, ut = time.localtime(now), time.gmtime(now)
        return {"call": st.callsign, "callsign": st.callsign, "name": st.name, "locator": st.grid,
                "qth": st.qth, "time": time.strftime("%H:%M", lt), "utc": time.strftime("%H:%MZ", ut),
                "date": time.strftime("%a %d %b %Y", lt), "day": time.strftime("%A", lt),
                "isodate": time.strftime("%Y-%m-%d", lt)}

    def fetch(self):
        return self.values_at(time.time())

    def sample(self):
        v = self.values_at(time.mktime((2026, 10, 5, 20, 15, 0, 0, 0, -1)))
        for k, d in (("call", "M0TXT"), ("callsign", "M0TXT"), ("name", "Alex"), ("locator", "IO91wm"),
                     ("qth", "Reading")):
            v[k] = v[k] or d
        return v


# ---------------------------------------------------------------- beacons and repeaters
@register("beacons")
class BeaconSource(Source):
    """A status board from a local file the operator keeps: one beacon or
    repeater a line, comma separated - callsign, frequency (MHz), mode,
    locator, status, note - '#' starts a comment. Re-read on every fetch,
    so editing the file is how the board is updated."""
    every = 60.0
    local = True
    COLUMNS = ("call", "freq", "mode", "loc", "status", "note")
    LINE = "{:<8}{:>9} {:<6}{:<5}{}"         # a template's column headings use the same format
    MAX_LINES = 40

    def path(self) -> Path:
        p = Path(self.config.get("file", "beacons.csv")).expanduser()
        return p if p.is_absolute() else self.folder / p

    def fields(self):
        n = int(self.config.get("count", 10))
        out = [("count", "how many are listed"), ("on", "how many are on air"), ("off", "how many are off")]
        for i in range(1, n + 1):
            out.append((str(i), f"line {i}: call, frequency, mode, status, note"))
            out.extend((f"{i}.{c}", f"line {i}: {c}") for c in self.COLUMNS)
        return out

    def field_names(self):
        return {n for n, _ in self.fields()}

    @staticmethod
    def parse(text: str) -> list[dict[str, str]]:
        out = []
        lines = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
        for row in csv.reader(lines, skipinitialspace=True):
            cells = [c.strip() for c in row] + [""] * 6
            if not cells[0]:
                continue
            out.append(dict(zip(BeaconSource.COLUMNS, cells[:6])))
        return out

    @staticmethod
    def values(entries: list[dict[str, str]]) -> dict[str, str]:
        on = sum(1 for e in entries if e["status"].lower() in ("on", "ok", "up", "qrv", "active"))
        v = {"count": str(len(entries)), "on": str(on), "off": str(len(entries) - on)}
        for i, e in enumerate(entries, 1):
            status = e["status"].upper()
            v[str(i)] = BeaconSource.LINE.format(e["call"], e["freq"], e["mode"], status, e["note"])
            for c in BeaconSource.COLUMNS:
                v[f"{i}.{c}"] = status if c == "status" else e[c]
        for i in range(len(entries) + 1, BeaconSource.MAX_LINES + 1):
            v[str(i)] = ""                          # a short list leaves the rest of the board blank
            for c in BeaconSource.COLUMNS:
                v[f"{i}.{c}"] = ""
        return v

    def fetch(self):
        try:
            text = self.path().read_text(encoding="utf-8")
        except OSError as e:
            raise SourceError(f"cannot read {self.path().name}: {e.strerror or e}") from e
        return self.values(self.parse(text))

    def sample(self):
        return self.values(self.parse(SAMPLE_BEACONS))


SAMPLE_BEACONS = """\
# call, MHz, mode, locator, status, note
GB3ZZA, 144.430, CW, IO91, on, 2m bcn
GB3ZZB, 432.910, CW, IO91, on, 70cm bcn
GB3ZZC, 1296.890, CW, IO92, off, mast work
GB3ZZD, 1249.000, FM-TV, IO91, on, ATV rptr
GB3ZZE, 145.775, FM, IO91, test, 2m rptr
"""


def make(name: str, config: dict, station, folder=None) -> Source:
    kind = (config or {}).get("type", name)
    cls = REGISTRY.get(kind)
    if cls is None:
        raise SourceError(f"no source type {kind!r} (is its plugin listed?)")
    return cls(name, config, station, folder)


def _builtin():
    # solar and cluster register themselves on import
    from . import cluster, solar  # noqa: F401


_builtin()
