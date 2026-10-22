"""The simulated radio: the default device, so the whole program runs, and
is tested, with no hardware.

What the receiver hears, at whatever the LO is tuned to:

- **Other stations**: a test card on each of a few bandplan channels (an
  AM-VSB one on 433 MHz on 70cm (where an AM signal fits 430-440), FM on 1255 MHz simplex and a
  1310 MHz repeater output, AM-VSB on the 1275 MHz simplex channel, a
  405-line System A one on 1287.5 MHz, FM on 5665 MHz), each a 0.16 s IQ
  loop generated once - the PAL colour sequence
  repeats exactly then, so the loop has no seam - carrying a 1 kHz tone, at
  a small carrier offset, with an ident that says SIM so nobody mistakes it
  for a real callsign. Where its mode has room (`simNicam`, on by
  default) it also sends NICAM-728 - the stereo test, 1 kHz left and 2 kHz
  right (ohsatv/nicam.py).
- **Our own transmission**: whatever is written while keyed comes back,
  shifted by the TX-RX LO difference plus a small error, so the monitor shows
  our own picture through the whole chain (the sim is full duplex; a Pluto
  on USB is not - DESIGN.md).
- **Noise** for a chosen carrier-to-noise ratio (in the whole sample band),
  from a precomputed table read at random offsets: generating 20 million
  Gaussian samples a second would cost more than the receiver.

Paced to real time both ways. If the reader falls more than half a second
behind, the sim skips ahead and counts it, as a real radio would overrun.
"""
from __future__ import annotations

import threading
import time
from collections import deque

import numpy as np

from .. import modem, testcard
from ..systems import build_loop, plan as system_plan
from .base import Device

MHZ = 1e6

# (frequency, system, ident line[, picture format]). Channels from
# ohsatv/bands.py, chosen so no two overlap: an FM-ATV signal on 1255 MHz
# spans 1246-1264 MHz, which would bury an AM station on the 1248/1249
# repeater inputs - true on air too, but a simulator that shows only
# interference proves nothing. The 405-line station (1283.95-1288.25 MHz)
# sits above the 1275 AM-VSB one's sound carrier and outside the band a
# receiver tuned to 1275 at 10 MS/s sees.
STATIONS = (
    (433.0 * MHZ, "am", "SIM 70CM 433"),
    (1275.0 * MHZ, "am", "SIM AM-VSB 1275"),
    (1255.0 * MHZ, "fm", "SIM FM 1255"),
    (1310.0 * MHZ, "fm", "SIM REPEATER 1310"),
    (5665.0 * MHZ, "fm", "SIM FM 5665"),
    (1287.5 * MHZ, "am", "SIM 405 LINES", "405"),
)


def station_format(idx: int) -> str:
    st = STATIONS[idx]
    return st[3] if len(st) > 3 else "pal"
STATION_ERR_HZ = 14_500.0          # how far off its channel each simulated station is


class SimDevice(Device):
    kind = "sim"
    can_tx = True
    real = False
    link = "sim"
    link_msps = None

    def __init__(self, cfg: dict, fs: float):
        self.cfg = dict(cfg)
        self.fs = float(fs)
        self.rng = np.random.default_rng(int(cfg.get("simSeed", 7)))
        self.rx_lo = None
        self.tx_lo = None
        self.tx_on = False
        self.loops: dict = {}
        self.lock = threading.Lock()
        self.loop_q: deque = deque()
        self.loop_n = 0
        self.loop_started = False
        self.rx_pos = 0
        self.t0 = None
        self.tx_t0 = None
        self.tx_pos = 0
        self.skipped = 0
        self.noise = None
        self.gen_lock = threading.Lock()

    # ----------------------------------------------------------- lifecycle
    def open(self) -> None:
        n = 1 << 21
        s = np.sqrt(0.5)
        self.noise = (s * (self.rng.standard_normal(n) + 1j * self.rng.standard_normal(n))).astype(np.complex64)
        self.t0 = None

    def close(self) -> None:
        self.tx_stop()

    def freq_range(self):
        # Down to Band I, so the cable feed (405 lines on B1-B5) can be
        # shown and tested with nothing connected.
        return (40 * MHZ, 6000 * MHZ)

    def duplex(self) -> bool:
        return True

    def set_rx_lo(self, hz: float) -> None:
        self.rx_lo = float(hz)

    def set_tx_lo(self, hz: float) -> None:
        self.tx_lo = float(hz)

    def set_tx_gain(self, db: float) -> None:
        self.tx_gain = float(db)

    def configure(self, **cfg):
        """Settings that change what the stations look like (mode
        parameters) or how noisy the channel is take effect without a
        restart; a changed mode parameter rebuilds the loops lazily."""
        changed = {k for k, v in cfg.items() if self.cfg.get(k) != v}
        self.cfg.update(cfg)
        if changed & {"amVideoBw", "amVsbLower", "amSoundOffset", "amSoundDb", "fmDeviation", "fmVideoBw",
                      "fmPreemph", "fmSub", "fmSubIndex", "fmInvert", "simNicam"}:
            with self.gen_lock:
                self.loops.clear()

    # ------------------------------------------------------------- stations
    def _station_loop(self, idx: int) -> np.ndarray:
        with self.gen_lock:
            loop = self.loops.get(idx)
            if loop is None:
                freq, system, line2 = STATIONS[idx][:3]
                fmt = station_format(idx)
                card = testcard.test_card("SIM", line2, "SIMULATED STATION", lines405=fmt == "405")
                # Other stations send their own format whatever we have
                # chosen to send (videoFormat): it is our choice, not theirs.
                loop = build_loop(self.fs, system, self._station_params(idx), card)
                self.loops[idx] = loop
            return loop

    def _stations(self, n: int) -> np.ndarray:
        out = np.zeros(n, np.complex64)
        if self.rx_lo is None or not self.cfg.get("simStations", True) or self.tx_on:
            # While keyed, our own transmitter a few metres away swamps
            # every other station in the receiver (they would be 40 dB down
            # behind it), so they are not synthesised at all - which also
            # frees the time a full-duplex 20 MS/s loopback needs.
            return out
        half = 0.5 * self.fs
        for i, (freq, system, _) in enumerate(s[:3] for s in STATIONS):
            # The station's carrier offset from our LO, and where its signal
            # spans - it is heard if any of it is inside our band.
            off = freq + STATION_ERR_HZ - self.rx_lo
            if system == "am":
                pl = system_plan(self.fs, "am", self._station_params(i))
                lo_e, hi_e = off + pl["occupied"][0], off + pl["occupied"][1]
            else:
                w = 0.5 * modem.fm_plan(self.fs, self.cfg.get("fmDeviation", 7e6))["carson"]
                lo_e, hi_e = off - w, off + w
            if hi_e < -half or lo_e > half:
                continue
            if abs(off) > half:
                # Its carrier is outside our band: shifting its loop there
                # would wrap it round the band's edge and put the whole
                # station - carrier and all - on top of whatever we are
                # tuned to. Found when the 405-line station on 1287.5 MHz
                # never locked at 10 MS/s: the AM station on 1275, whose
                # last 10 kHz just reach that band, landed on its carrier.
                continue
            loop = self._station_loop(i)
            p = len(loop)
            s = self.rx_pos % p
            seg = loop[s:s + n] if s + n <= p else np.tile(loop, -(-(s + n) // p) + 1)[s:s + n]
            # AM loops are generated with the vision carrier at the plan's place
            # (off the LO); move it to where this station actually is.
            base = pl["carrier"] if system == "am" else 0.0
            out += seg * modem._phasor(self.fs, off - base, n, self.rx_pos)
        return out

    def _station_params(self, idx: int) -> dict:
        """A station's own format, with NICAM where it is PAL (simNicam):
        the 405-line station has none, as System A had none."""
        fmt = station_format(idx)
        return dict(self.cfg, videoFormat=fmt, nicam=fmt == "pal" and bool(self.cfg.get("simNicam", True)))

    # -------------------------------------------------------------- receive
    def _pace(self, pos_attr: str, t0_attr: str, n: int) -> None:
        now = time.monotonic()
        t0 = getattr(self, t0_attr)
        pos = getattr(self, pos_attr)
        if t0 is None:
            setattr(self, t0_attr, now - pos / self.fs)
            return
        due = t0 + (pos + n) / self.fs
        if due > now:
            time.sleep(due - now)
        elif now - due > 0.5 and pos_attr == "rx_pos":
            # The reader is half a second behind real time: a radio would
            # have overrun. Skip ahead and say so.
            skip = int((now - due) * self.fs)
            self.rx_pos += skip
            self.skipped += skip

    def read(self, n: int) -> np.ndarray:
        self._pace("rx_pos", "t0", n)
        sig = self._stations(n)
        # Our own transmission, looped back.
        own = self._take_loopback(n)
        if own is not None and self.tx_lo is not None and self.rx_lo is not None:
            off = self.tx_lo - self.rx_lo + float(self.cfg.get("simTxErrHz", -3200.0))
            if abs(off) < self.fs:
                g = 10 ** (float(self.cfg.get("simLoopDb", 0.0)) / 20)
                sig = sig + own * (np.float32(g) * modem._phasor(self.fs, off, n, self.rx_pos))
        cnr = float(self.cfg.get("simCnrDb", 32.0))
        sigma = 10 ** (-cnr / 20)
        k = int(self.rng.integers(0, len(self.noise) - n)) if n < len(self.noise) else 0
        noise = self.noise[k:k + n] if n < len(self.noise) else np.resize(self.noise, n)
        self.rx_pos += n
        return (sig + np.float32(sigma) * noise).astype(np.complex64)

    def _take_loopback(self, n: int) -> np.ndarray | None:
        with self.lock:
            if not self.loop_q and not self.tx_on:
                self.loop_started = False
                return None
            # Wait for two blocks of backlog before playing out, then keep
            # playing - TX and RX threads are paced separately, and a little
            # latency is what keeps the two from starving each other.
            if not self.loop_started:
                if self.loop_n < 2 * n:
                    return None
                self.loop_started = True
            parts, got = [], 0
            while self.loop_q and got < n:
                a = self.loop_q[0]
                take = min(len(a), n - got)
                parts.append(a[:take])
                got += take
                if take == len(a):
                    self.loop_q.popleft()
                else:
                    self.loop_q[0] = a[take:]
            self.loop_n -= got
        out = np.concatenate(parts) if parts else np.zeros(0, np.complex64)
        if len(out) < n:
            out = np.concatenate([out, np.zeros(n - len(out), np.complex64)])
        return out

    # ------------------------------------------------------------- transmit
    def tx_start(self) -> None:
        with self.lock:
            self.tx_on = True
            self.loop_q.clear()
            self.loop_n = 0
            self.loop_started = False
        self.tx_t0 = None
        self.tx_pos = 0

    def write(self, iq: np.ndarray) -> None:
        if not self.tx_on:
            return
        n = len(iq)
        self._pace("tx_pos", "tx_t0", n)
        self.tx_pos += n
        with self.lock:
            self.loop_q.append(np.asarray(iq, np.complex64))
            self.loop_n += n
            # Never hold more than a second: if RX is not reading, drop.
            while self.loop_n > self.fs:
                a = self.loop_q.popleft()
                self.loop_n -= len(a)

    def tx_stop(self) -> None:
        with self.lock:
            self.tx_on = False
            self.loop_q.clear()
            self.loop_n = 0

    def status(self) -> dict:
        return {"kind": "sim", "fs": self.fs, "rxLo": self.rx_lo, "txLo": self.tx_lo, "tx": self.tx_on,
                "range": self.freq_range(), "link": "sim", "skipped": self.skipped,
                "cnr": float(self.cfg.get("simCnrDb", 32.0))}
