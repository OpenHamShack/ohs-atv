"""Shared test helpers: a channel (noise + carrier offset) and picture
comparisons. Kept here rather than in the engine so tests measure the
engine's output with code that is not the engine's own."""
from __future__ import annotations

import atexit
import math
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The radio lock (ohsatv/radiolock.py) puts markers in /run/lock and, for a
# radio with a shell, on the radio over ssh. The tests' radios are fakes:
# their markers go to a directory of the tests' own and no ssh is attempted
# (the shell side is tested by running its scripts locally, in
# tests/test_radiolock.py). Set here, at import, because every test module
# imports this before any test runs: a Pluto test that forgot would
# otherwise write into the real /run/lock beside a real radio's marker.
#
# Removed at exit rather than by a module's tearDown: the variable is read by
# every module that makes a radio, for as long as the process runs, so the
# directory has to outlive them all. (Left behind, one of these per run was
# part of the 21,000 directories that filled shack's /tmp on 6 October 2026.)
os.environ["OHSRADIO_LOCK_DIR"] = tempfile.mkdtemp(prefix="ohsatv-test-locks-")
atexit.register(shutil.rmtree, os.environ["OHSRADIO_LOCK_DIR"], ignore_errors=True)
os.environ["OHSATV_RADIO_SSH"] = "0"


def tmp_dir(tc, prefix: str = "ohsatv-test-") -> Path:
    """A fresh directory for test `tc`, removed when the test ends however
    it ends. Every test that writes to disk takes its directory from here.

    Bare mkdtemp() calls are what filled shack's /tmp (31 GB, 6 October
    2026): one full run left about 350 directories, and a Radio's data
    directory holds recordings and captures, so some were hundreds of MB.
    addCleanup rather than tearDown because it runs when setUp fails too;
    and because cleanups run last-registered first, a directory taken
    before the radio that writes into it is removed after that radio has
    been stopped - take it first."""
    d = Path(tempfile.mkdtemp(prefix=prefix))
    tc.addCleanup(shutil.rmtree, d, ignore_errors=True)
    return d


def page_rows(*rows: tuple[int, bytes]) -> list[bytearray]:
    """25 blank teletext rows with the given (row number, contents) filled
    in (the editor's tests, test_txt_*.py, came from OpenHamShack Teletext
    with this helper)."""
    out = [bytearray(b" " * 40) for _ in range(25)]
    for r, data in rows:
        b = bytearray(data)
        out[r][:len(b)] = b[:40]
    return out


def wait_for(fn, timeout: float = 8.0, step: float = 0.05, beat=None):
    """fn() until it is truthy (returned) or the time runs out (None).
    `beat`: called each step (a radio's heartbeat, a feed drain)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if beat is not None:
            beat()
        r = fn()
        if r:
            return r
        time.sleep(step)
    return None


def channel(iq: np.ndarray, cnr_db: float, offset_hz: float, fs: float, start: int, rng,
            bw_hz: float | None = None) -> np.ndarray:
    """Add white noise for a carrier-to-noise ratio measured in `bw_hz`
    (default: the whole sample rate) and shift by `offset_hz`."""
    n = len(iq)
    p = float(np.mean(np.abs(iq) ** 2))
    noise_p = p / 10 ** (cnr_db / 10) * (fs / bw_hz if bw_hz else 1.0)
    s = math.sqrt(noise_p / 2)
    k = np.arange(start, start + n, dtype=np.float64)
    rot = np.exp(2j * np.pi * ((k * offset_hz / fs) % 1.0)).astype(np.complex64)
    return (iq * rot + s * (rng.standard_normal(n) + 1j * rng.standard_normal(n))).astype(np.complex64)


def luma(rgb: np.ndarray) -> np.ndarray:
    rgb = np.asarray(rgb, np.float32)
    if rgb.max() > 1.5:
        rgb = rgb / 255.0
    return 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]


def resample_width(img: np.ndarray, w: int) -> np.ndarray:
    src = np.asarray(img, np.float32)
    x = (np.arange(w) + 0.5) * (src.shape[1] / w) - 0.5
    x = np.clip(x, 0, src.shape[1] - 1)
    i0 = np.floor(x).astype(int)
    i1 = np.minimum(i0 + 1, src.shape[1] - 1)
    f = (x - i0)
    if src.ndim == 3:
        f = f[:, None]
    return src[:, i0] * (1 - f) + src[:, i1] * f


def psnr(a: np.ndarray, b: np.ndarray) -> float:
    """PSNR (dB) of two images 0..1 of the same shape."""
    mse = float(np.mean((np.asarray(a, np.float64) - np.asarray(b, np.float64)) ** 2))
    return 99.0 if mse <= 0 else 10 * math.log10(1.0 / mse)


def crop(img: np.ndarray, margin_frac: float = 0.04) -> np.ndarray:
    """Drop the picture edges, where blanking edges are allowed to differ."""
    h, w = img.shape[:2]
    my, mx = int(h * margin_frac), int(w * margin_frac)
    return img[my:h - my, mx:w - mx]


def narrow_station(fmt: str):
    """A simulator whose other stations send `fmt` (the real one's are
    always standard PAL): a OpenHamShack in Mini-MAC or narrow colour PAL on the
    air, for the receiver's automatic format choice to find."""
    from ohsatv.devices.sim import STATIONS, SimDevice, station_format
    from ohsatv import testcard
    from ohsatv.systems import build_loop

    class NarrowSim(SimDevice):
        def _station_loop(self, idx: int) -> np.ndarray:
            with self.gen_lock:
                loop = self.loops.get(idx)
                if loop is None:
                    _, system, line2 = STATIONS[idx][:3]
                    card = testcard.test_card("SIM", line2, "SIMULATED STATION")
                    own = fmt if system == "fm" else station_format(idx)
                    loop = build_loop(self.fs, system, dict(self.cfg, videoFormat=own), card)
                    self.loops[idx] = loop
                return loop

    return NarrowSim


def first_claim(dem, dec, fs: float, blocks: int, seed: int) -> int | None:
    """Receiver noise alone, in 40 ms blocks as the radio feeds the decoder:
    the first block after which the decoder claimed field lock or gave out
    a picture, or None. Looked at after every block, not at the end - the
    console shows the lock after every block, so a claim withdrawn a block
    later has still flashed FIELD LOCKED on an empty band."""
    rng = np.random.default_rng(seed)
    n = int(round(0.04 * fs))
    for b in range(blocks):
        iq = (0.01 * (rng.standard_normal(n) + 1j * rng.standard_normal(n))).astype(np.complex64)
        if dec.feed(dem(iq)) or dec.stats["vLock"]:
            return b
    return None


# -- an AD936x link, sample by sample -------------------------------------------
# What a Pluto does to a signal between the samples one radio is given and the
# samples another delivers. The last half-band each way (HB1, x2) is run as the
# chip runs it - interpolating in the transmitter, decimating in the receiver -
# because that is where the trouble is: it is 6 dB down at fs/2, and its
# images and aliases near the band edge come back as they do on air. The
# stages beyond it (HB2 and HB3 at 4 and 8 fs, whose images fall far outside
# and are filtered twice) and the analogue baseband filters (third-order
# Butterworth and one pole, at the driver's corners for `rf_bw`) are applied
# at 2 fs as the responses they are. The coefficients are ADI's
# (libad9361-iio's filter designer), written out here apart from
# ohsatv/devices/ad936x.py so the tests do not measure that model with itself.
_RX_HB1 = [-8, 0, 42, 0, -147, 0, 619, 1013, 619, 0, -147, 0, 42, 0, -8]
_TX_HB1 = [-53, 0, 313, 0, -1155, 0, 4989, 8192, 4989, 0, -1155, 0, 313, 0, -53]
_HB2 = [-9, 0, 73, 128, 73, 0, -9]
_RX_HB3 = [1, 4, 6, 4, 1]
_TX_HB3 = [1, 2, 1]


def _taps_response(taps, f: np.ndarray, rate: float) -> np.ndarray:
    h = np.asarray(taps, np.float64)
    h = h / h.sum()
    k = np.arange(len(h)) - (len(h) - 1) / 2
    return np.exp(-2j * np.pi * np.outer(f / rate, k)) @ h


def _butter3_pole(f: np.ndarray, corner: float, pole: float) -> np.ndarray:
    s = 1j * f / corner
    return 1 / ((1 + s) * (1 + s + s ** 2) * (1 + 1j * f / pole))


def ad936x_tx_spectrum(iq: np.ndarray, fs: float) -> tuple[np.ndarray, np.ndarray]:
    """(f, power) of `iq` as an AD936x transmitter makes it: interpolated
    as the chip does (zeros between samples, then TX HB1 at 2 fs), with the
    later stages (HB2 at 4 fs, HB3 at 8 fs, the DAC's hold, the analogue
    Butterworth and pole) applied as the responses they are, from ADI's
    coefficients written out here, apart from the model in devices/ad936x.py.
    Summed into 1 kHz bins, as the 5 October analysis took them (977 Hz), so
    a carrier is one bin and the picture's line harmonics near it are in it."""
    up = np.zeros(2 * len(iq), np.complex64)
    up[::2] = iq
    h = np.asarray(_TX_HB1, np.float64)
    y = np.convolve(up, 2 * h / h.sum())
    n = len(y)
    P = np.abs(np.fft.fft(y * np.hanning(n))) ** 2
    f = np.fft.fftfreq(n, 1 / (2 * fs))
    P = P * np.abs(_taps_response(_HB2, f, 4 * fs) * _taps_response(_TX_HB3, f, 8 * fs)
                   * np.sinc(f / (8 * fs)) * _butter3_pole(f, 1.6 * fs / 2, 5.0 * fs / 2)) ** 2
    b = np.rint(f / 1e3).astype(np.int64)
    u, inv = np.unique(b, return_inverse=True)
    return u * 1e3, np.bincount(inv, weights=P)


def ad936x_link(iq: np.ndarray, fs: float, rf_bw: float | None = None, tx: bool = True,
                rx: bool = True, image_hz: float = 1000.0) -> np.ndarray:
    """`iq` at fs through an AD936x transmitter and/or receiver, both at fs
    with rf_bandwidth `rf_bw` (default fs): the same length back, the
    half-bands' delay removed."""
    rf_bw = rf_bw or fs
    x = np.asarray(iq, np.complex64)
    n = len(x)
    if tx:
        h = np.asarray(_TX_HB1, np.float64)
        up = np.zeros(2 * n, np.complex64)
        up[::2] = x
        y = np.convolve(up, (2 * h / h.sum()).astype(np.float32))
        y = y[(len(h) - 1) // 2:][:2 * n]
    else:
        # The receiver alone: the signal at 2 fs with nothing beyond fs/2.
        X = np.fft.fft(x)
        Y = np.zeros(2 * n, np.complex128)
        Y[:n // 2] = X[:n // 2]
        Y[-(n - n // 2):] = X[n // 2:]
        y = (np.fft.ifft(Y) * 2).astype(np.complex64)
    rate = 2 * fs
    Y = np.fft.fft(y)
    f = np.fft.fftfreq(len(y), 1 / rate)
    g = np.ones(len(f), np.complex128)
    if tx:
        g *= _taps_response(_HB2, f, 4 * fs) * _taps_response(_TX_HB3, f, 8 * fs)
        g *= _butter3_pole(f, 1.6 * rf_bw / 2, 5.0 * rf_bw / 2)
    if rx:
        g *= _taps_response(_HB2, f, 4 * fs) * _taps_response(_RX_HB3, f, 8 * fs)
        g *= _butter3_pole(f, 1.4 * rf_bw / 2, 2.5 * rf_bw / 2)
    Y *= g
    if tx and rx and image_hz:
        # Two radios have two sample clocks, so the transmitter's images
        # fold back on to the wanted signal a little off it and beat slowly,
        # rather than adding in one fixed phase as they would here with one
        # clock. Moving everything beyond fs/2 by `image_hz` does that.
        out = np.abs(f) > fs / 2
        m = int(round(image_hz / (rate / len(y))))
        Y = np.where(out, 0, Y) + np.roll(np.where(out, Y, 0), m)
    y = np.fft.ifft(Y).astype(np.complex64)
    if not rx:
        return y[::2][:n].copy()
    h = np.asarray(_RX_HB1, np.float64)
    z = np.convolve(y, (h / h.sum()).astype(np.float32))[(len(h) - 1) // 2:]
    return z[::2][:n].astype(np.complex64)
