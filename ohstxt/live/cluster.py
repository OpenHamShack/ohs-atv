"""DX cluster spots over telnet.

Each fetch is one short session - connect, log in with the callsign, ask
for the last spots, read until the cluster goes quiet, say bye - rather
than a connection held open for the life of the runner. A held connection
would see spots as they come, but it has to notice a half-dead TCP link,
reconnect with backoff and survive a cluster restarting; a session every
couple of minutes has none of that to get wrong, and a teletext page
cannot show spots faster than a carousel turns anyway.

The command is DXSpider's `sh/dx N` (AR-Cluster and CC Cluster accept it
too). Bands are filtered here, from each spot's frequency, rather than
with the cluster's own filter syntax, which differs between cluster
programs. Two spot formats are read: the `sh/dx` listing

    14025.0  JA1ABC      5-Oct-2026 1802Z  CQ CQ               <G4XYZ>

and the live announcement a cluster may send at any time

    DX de G4XYZ:     14025.0  JA1ABC       CQ CQ                 1802Z

Telnet option negotiation (IAC sequences) is stripped and never answered;
every cluster seen accepts a client that ignores it.
"""
from __future__ import annotations

import re
import socket
import time

from .sources import Source, SourceError, register

# Amateur bands by frequency in kHz (IARU Region 1 edges, a little wide).
BANDS = (("160m", 1800, 2000), ("80m", 3500, 4000), ("60m", 5250, 5450), ("40m", 7000, 7300),
         ("30m", 10100, 10150), ("20m", 14000, 14350), ("17m", 18068, 18168), ("15m", 21000, 21450),
         ("12m", 24890, 24990), ("10m", 28000, 29700), ("6m", 50000, 54000), ("4m", 70000, 70500),
         ("2m", 144000, 148000), ("70cm", 430000, 440000), ("23cm", 1240000, 1300000),
         ("13cm", 2300000, 2450000), ("3cm", 10000000, 10500000))

LISTING = re.compile(r"^\s*(\d+(?:\.\d+)?)\s+([A-Z0-9/]+)\s+(\d{1,2}-[A-Za-z]{3}-\d{4})\s+(\d{4})Z\s*(.*?)\s*"
                     r"<([A-Za-z0-9/-]+)>\s*$")
ANNOUNCE = re.compile(r"^DX de\s+([A-Za-z0-9/-]+):?\s+(\d+(?:\.\d+)?)\s+([A-Za-z0-9/]+)\s+(.*?)\s*(\d{4})Z")


def band_of(khz: float) -> str:
    for name, lo, hi in BANDS:
        if lo <= khz <= hi:
            return name
    return ""


def strip_telnet(data: bytes) -> bytes:
    out = bytearray()
    i = 0
    while i < len(data):
        b = data[i]
        if b == 0xFF and i + 1 < len(data):
            cmd = data[i + 1]
            if cmd == 0xFF:
                out.append(0xFF)
                i += 2
            elif cmd == 0xFA:                       # subnegotiation: to IAC SE
                end = data.find(b"\xff\xf0", i + 2)
                i = len(data) if end < 0 else end + 2
            elif cmd in (0xFB, 0xFC, 0xFD, 0xFE):
                i += 3
            else:
                i += 2
        else:
            out.append(b)
            i += 1
    return bytes(out)


def parse_spots(text: str) -> list[dict[str, str]]:
    spots = []
    for line in text.replace("\r", "\n").split("\n"):
        m = LISTING.match(line)
        if m:
            freq, call, _date, hhmm, comment, spotter = m.groups()
        else:
            m = ANNOUNCE.match(line)
            if not m:
                continue
            spotter, freq, call, comment, hhmm = m.groups()
        try:
            khz = float(freq)
        except ValueError:
            continue
        spots.append({"freq": f"{khz:.1f}" if khz < 100000 else f"{khz:.0f}", "khz": khz, "call": call.upper(),
                      "time": hhmm, "spotter": spotter.upper(), "comment": " ".join(comment.split()),
                      "band": band_of(khz)})
    return spots


@register("dxcluster")
class ClusterSource(Source):
    every = 120.0
    min_every = 30.0
    COLUMNS = ("freq", "call", "time", "spotter", "comment", "band")
    LINE = "{:>8} {:<10}{:<5}{}"             # a template's column headings use the same format

    def count(self) -> int:
        try:
            return max(1, min(40, int(self.config.get("count", 10))))
        except (TypeError, ValueError):
            return 10

    def bands(self) -> set[str]:
        b = self.config.get("bands") or []
        if isinstance(b, str):
            b = b.split(",")
        return {x.strip().lower() for x in b if x.strip()}

    def fields(self):
        out = [("count", "spots shown"), ("bands", "the bands filtered for")]
        for i in range(1, self.count() + 1):
            out.append((str(i), f"spot {i}: frequency, call, time, spotter"))
            out.extend((f"{i}.{c}", f"spot {i}: {c}") for c in self.COLUMNS)
        return out

    def values(self, spots) -> dict[str, str]:
        want = self.bands()
        if want:
            spots = [s for s in spots if s["band"].lower() in want]
        # newest first, one line per call and band (a busy station is
        # spotted over and over; the page has ten lines)
        seen, keep = set(), []
        for s in spots:
            key = (s["call"], s["band"])
            if key in seen:
                continue
            seen.add(key)
            keep.append(s)
        keep = keep[:self.count()]
        v = {"count": str(len(keep)), "bands": " ".join(sorted(want)) if want else "all"}
        for i, s in enumerate(keep, 1):
            v[str(i)] = self.LINE.format(s["freq"], s["call"], s["time"], s["spotter"])
            for c in self.COLUMNS:
                v[f"{i}.{c}"] = s[c]
        for i in range(len(keep) + 1, self.count() + 1):
            v[str(i)] = ""                          # fewer spots than lines: the rest are blank
            for c in self.COLUMNS:
                v[f"{i}.{c}"] = ""
        return v

    def fetch(self):
        host = str(self.config.get("host", "")).strip()
        if not host:
            raise SourceError("no DX cluster host set")
        port = int(self.config.get("port", 7300))
        login = str(self.config.get("login") or self.station.callsign or "").strip()
        if not login:
            raise SourceError("no callsign to log in to the cluster with")
        timeout = float(self.config.get("timeout", 15))
        n = min(100, self.count() * (5 if self.bands() else 2))
        text = session(host, port, login, f"sh/dx {n}", timeout, float(self.config.get("quiet", 1.2)))
        if not text.strip():
            raise SourceError(f"{host}:{port} sent nothing after the login")
        # An answer with no spots in it is an empty list, not a failure: a
        # quiet band filter on a quiet evening is true data.
        return self.values(parse_spots(text))

    def sample(self):
        return self.values(parse_spots(SAMPLE))


def session(host: str, port: int, login: str, command: str, timeout: float = 15.0, quiet: float = 1.2) -> str:
    """Log in, send one command, return everything the cluster said after
    it. 'Quiet' is the cluster saying nothing for `quiet` seconds - the
    only end-of-reply marker every cluster program shares."""
    deadline = time.monotonic() + timeout
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            def read_until_quiet(prompt_words=()) -> bytes:
                buf = b""
                last = time.monotonic()
                while time.monotonic() < deadline:
                    s.settimeout(max(0.05, min(quiet, deadline - time.monotonic())))
                    try:
                        chunk = s.recv(4096)
                    except socket.timeout:
                        if buf and time.monotonic() - last >= quiet:
                            return buf
                        if not buf and time.monotonic() - last >= quiet * 4:
                            return buf
                        continue
                    if not chunk:
                        return buf
                    buf += strip_telnet(chunk)
                    last = time.monotonic()
                    low = buf.lower()
                    if prompt_words and any(w in low[-80:] for w in prompt_words):
                        return buf
                raise SourceError(f"{host}:{port} did not finish answering in {timeout:.0f} s")

            read_until_quiet((b"login:", b"call:", b"callsign:", b"enter your call"))
            s.sendall(login.encode("ascii", "replace") + b"\r\n")
            read_until_quiet()
            s.sendall(command.encode() + b"\r\n")
            reply = read_until_quiet()
            try:
                s.sendall(b"bye\r\n")
            except OSError:
                pass
            return reply.decode("latin-1")
    except SourceError:
        raise
    except OSError as e:
        raise SourceError(f"{host}:{port}: {e.strerror or e}") from e


SAMPLE = """\
 14025.0  JA1ZZA      5-Oct-2026 2012Z  CQ DX                         <G4ZZB>
 50313.0  CT1ZZC      5-Oct-2026 2009Z  FT8 -12dB                     <F5ZZD>
  7074.0  VK2ZZE      5-Oct-2026 2007Z  FT8 long path                 <ON4ZZF>
144300.0  PA3ZZG      5-Oct-2026 2005Z  tropo JO22 > IO91             <G8ZZH>
 21074.0  ZS6ZZJ      5-Oct-2026 2003Z  FT8                           <EA4ZZK>
1296200.0  F6ZZL       5-Oct-2026 2001Z  EME                           <DL1ZZM>
 28480.0  PY2ZZN      5-Oct-2026 1958Z  SSB 59                        <I2ZZP>
432200.0  ON7ZZQ      5-Oct-2026 1955Z  tropo 55                      <G3ZZR>
 18100.0  9Y4ZZS      5-Oct-2026 1952Z  RTTY                          <OH2ZZT>
 10136.0  UA9ZZU      5-Oct-2026 1950Z  FT8                           <SM5ZZV>
"""
