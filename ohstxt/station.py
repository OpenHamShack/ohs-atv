"""The station a service belongs to: callsign, name, locator, QTH, and the
live data sources its pages draw on - kept in `station.json` in the service
folder.

In the folder rather than in the window's preferences because three things
need it and only one of them is the window: the service checker (is the
callsign on the air?), the headless live-page runner that a systemd unit
starts with nothing but the folder's path, and the page templates. A .tti
reader (OpenHamShack ATV, vbit2) ignores anything that is not a .tti, so the
file sits beside the pages harmlessly, and copying the folder to another
machine takes the station with it.

Read forgivingly, as the preferences are: a field of the wrong type keeps
its default, an unreadable file is an empty station, never an error that
stops a service from opening or a runner from starting.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

FILENAME = "station.json"
FORMAT = "ohstxt-station/1"

# Lines per field the inserter fills with teletext. ETS 300 706 allows lines
# 7-22 and 320-335, sixteen a field; an inserter that uses fewer (or more,
# on a full-field data channel) makes every cycle time longer (or shorter)
# in proportion. Used only for estimates: magazine cycles and page budgets.
DEFAULT_VBI_LINES = 16


@dataclass
class Station:
    callsign: str = ""
    name: str = ""
    locator: str = ""
    qth: str = ""
    lat: float | None = None
    lon: float | None = None
    vbi_lines: int = DEFAULT_VBI_LINES
    sources: dict = field(default_factory=dict)   # name -> {"type": ..., options}; see ohstxt.live
    plugins: list = field(default_factory=list)   # module names that register more source types

    @property
    def grid(self) -> str:
        """The locator as set, or worked out from latitude and longitude."""
        if self.locator:
            return self.locator
        if self.lat is not None and self.lon is not None:
            return maidenhead(self.lat, self.lon)
        return ""


def load(folder) -> Station:
    st = Station()
    try:
        raw = json.loads((Path(folder) / FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return st
    if not isinstance(raw, dict):
        return st
    for k, default in asdict(Station()).items():
        v = raw.get(k)
        if v is None:
            continue
        if k in ("lat", "lon"):
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                setattr(st, k, float(v))
        elif type(v) is type(default):
            setattr(st, k, v)
    st.callsign = st.callsign.strip().upper()
    return st


def save(st: Station, folder):
    p = Path(folder) / FILENAME
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {"format": FORMAT, **asdict(st)}
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".station-", suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
            f.write("\n")
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def maidenhead(lat: float, lon: float, chars: int = 6) -> str:
    """The Maidenhead locator of a position, to 4, 6 or 8 characters:
    field (A-R), square (0-9), subsquare (a-x), extended square (0-9)."""
    lon = min(max(lon + 180.0, 0.0), 359.999999)
    lat = min(max(lat + 90.0, 0.0), 179.999999)
    out = chr(ord("A") + int(lon // 20)) + chr(ord("A") + int(lat // 10))
    lon, lat = lon % 20, lat % 10
    out += str(int(lon // 2)) + str(int(lat // 1))
    lon, lat = lon % 2, lat % 1
    if chars >= 6:
        out += chr(ord("a") + int(lon * 12)) + chr(ord("a") + int(lat * 24))
        lon, lat = (lon * 12) % 1, (lat * 24) % 1
    if chars >= 8:
        out += str(int(lon * 10)) + str(int(lat * 10))
    return out
