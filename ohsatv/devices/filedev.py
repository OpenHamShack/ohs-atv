"""A recording as the receiving radio (ohsatv/iqrec.py): the samples go
through exactly the receive chain a live radio's do, so a fault seen on air
can be replayed and worked on offline.

It delivers at the recording's own rate in real time, as a radio would - the
decoder, the CPU figures and the decodeSkipped count then mean what they mean
live. At the end it starts again from the beginning (playLoop), or delivers
silence. It transmits nothing and cannot be tuned: the tuning the engine
asks for is noted against the recording's own, and status() says when they
differ.
"""
from __future__ import annotations

import time

import numpy as np

from .. import iqrec
from .base import Device, DeviceError


def recorded_response(g: dict, fs: float):
    """The receiving radio's fixed response, from a recording's metadata:
    an AD936x's (devices/ad936x.py) when the recording says it came from
    one and gives its rf_bandwidth, else None. A recording holds what the
    radio delivered, before any correction, so playing it back has to undo
    the same filters the live receiver did - the 5 October captures replay
    with their colour restored this way (docs/atv-research.md 17)."""
    from . import ad936x
    from .pluto import _looks_like_ad936x
    bw = g.get("openhamshack:rf_bandwidth")
    if not bw or not (g.get("openhamshack:device") == "pluto" or _looks_like_ad936x(str(g.get("core:hw", "")))):
        return None
    text = g.get("openhamshack:rx_path_rates")
    stages = ad936x.parse_path_rates(text)
    if text and stages is None:
        return None
    return ad936x.Response("rx", fs, float(bw), stages)


class FileDevice(Device):
    kind = "file"
    can_tx = False
    real = False
    link = "file"

    def __init__(self, cfg: dict, fs: float):
        self.cfg = cfg
        self.fs = float(fs)
        self.path = cfg.get("playFile", "")
        self.loop = bool(cfg.get("playLoop", True))
        self.realtime = True
        self.rec = None
        self.pos = 0
        self.loops = 0
        self.ended = False
        self.rx_lo = None
        self._t0 = None
        self._delivered = 0

    def open(self) -> None:
        if not self.path:
            raise DeviceError("Choose a recording to play (Settings › Radio, or --play FILE.sigmf-meta)")
        try:
            self.rec = iqrec.Recording(self.path)
        except iqrec.RecordError as e:
            raise DeviceError(str(e)) from e
        if abs(self.rec.fs - self.fs) > 1:
            raise DeviceError(f"The recording is at {self.rec.fs / 1e6:g} MS/s but the engine is running at "
                              f"{self.fs / 1e6:g} MS/s")
        if self.rec.n == 0:
            raise DeviceError(f"{self.rec.data_path.name} holds no samples")
        self.rx_response = recorded_response(self.rec.globals, self.rec.fs)

    def close(self) -> None:
        self.rec = None

    def set_rx_lo(self, hz: float) -> None:
        self.rx_lo = float(hz)

    def read(self, n: int) -> np.ndarray:
        if self.rec is None:
            raise DeviceError("The recording is not open")
        out = np.zeros(n, np.complex64)
        got = 0
        while got < n and not self.ended:
            part = self.rec.samples(self.pos, n - got)
            out[got: got + len(part)] = part
            got += len(part)
            self.pos += len(part)
            if self.pos >= self.rec.n:
                if self.loop:
                    self.pos = 0
                    self.loops += 1
                else:
                    self.ended = True
        if self.realtime:
            now = time.monotonic()
            if self._t0 is None or now - (self._t0 + self._delivered / self.fs) > 0.5:
                # First read, or the reader paused: pace from here.
                self._t0, self._delivered = now, 0
            self._delivered += n
            due = self._t0 + self._delivered / self.fs
            if due > now:
                time.sleep(due - now)
        return out

    def status(self) -> dict:
        r = self.rec
        st = {"kind": "file", "file": str(self.path), "fs": self.fs, "rxLo": self.rx_lo}
        if r is not None:
            st.update(datatype=r.datatype, recordedLo=r.freq, seconds=round(r.seconds, 2),
                      position=round(self.pos / r.fs, 2), loops=self.loops, ended=self.ended,
                      description=r.globals.get("core:description", ""),
                      rxCorrection=self.rx_response.describe() if self.rx_response else None)
            here = r.freq_at(self.pos)
            st["recordedLo"] = here
            notes = []
            if len(r.segments) > 1:
                notes.append("The radio retuned during this recording: " + ", ".join(
                    f"{f / 1e6:.4f} MHz from {s / r.fs:.1f} s" for s, f in r.segments))
            if self.rx_lo is not None and abs(self.rx_lo - here) > 1:
                notes.append(f"Tuned to {self.rx_lo / 1e6:.4f} MHz but the recording's centre here is "
                             f"{here / 1e6:.4f} MHz: a recording cannot be retuned")
            if notes:
                st["notes"] = notes
        return st
