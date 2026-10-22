"""FIR design and streaming FFT convolution.

Kaiser-windowed sinc throughout: one parameter (stopband attenuation) sets
the window, and the length for a wanted transition width follows from
Kaiser's formula, so filters are specified the way they are judged - "80 dB
down, 150 Hz transition" - instead of by tap count.
"""
from __future__ import annotations

import math

import numpy as np


def kaiser_beta(atten_db: float) -> float:
    if atten_db > 50:
        return 0.1102 * (atten_db - 8.7)
    if atten_db >= 21:
        return 0.5842 * (atten_db - 21) ** 0.4 + 0.07886 * (atten_db - 21)
    return 0.0


def kaiser_taps(transition_hz: float, fs: float, atten_db: float = 80.0) -> int:
    n = (atten_db - 7.95) / (14.36 * transition_hz / fs) + 1
    return int(math.ceil(n)) | 1


def lowpass(cutoff_hz: float, fs: float, ntaps: int, atten_db: float = 80.0) -> np.ndarray:
    """Real linear-phase lowpass, unity gain at DC. `cutoff_hz` is the -6 dB
    point (the centre of the transition)."""
    n = np.arange(ntaps) - (ntaps - 1) / 2.0
    h = 2 * cutoff_hz / fs * np.sinc(2 * cutoff_hz / fs * n)
    h *= np.kaiser(ntaps, kaiser_beta(atten_db))
    return h / h.sum()


def bandpass_complex(lo_hz: float, hi_hz: float, fs: float, ntaps: int, atten_db: float = 80.0) -> np.ndarray:
    """Complex one-sided bandpass passing [lo, hi] Hz and rejecting the mirror
    image: a lowpass of half the width shifted up to the band centre. Applied
    to real audio it yields the analytic USB signal (a Hilbert transformer and
    the band-limiting in one filter); applied to complex IQ it selects one
    sideband."""
    centre = (lo_hz + hi_hz) / 2.0
    h = lowpass((hi_hz - lo_hz) / 2.0, fs, ntaps, atten_db)
    n = np.arange(ntaps) - (ntaps - 1) / 2.0
    return (h * np.exp(2j * np.pi * centre / fs * n)).astype(np.complex128)


def _good_size(n: int) -> int:
    """Smallest 2^a 3^b 5^c >= n (pocketfft is fast on these)."""
    best = 1 << (n - 1).bit_length()
    p5 = 1
    while p5 < best:
        p35 = p5
        while p35 < best:
            p = p35
            while p < n:
                p *= 2
            best = min(best, p)
            p35 *= 3
        p5 *= 5
    return best


class OlsFilter:
    """Streaming FIR by FFT overlap-save. Accepts any block length; keeps
    its own history so a filter can be swapped (new taps) without a gap."""

    def __init__(self, taps: np.ndarray, block: int = 0, complex_out: bool = True):
        self.complex_out = complex_out
        self._set(np.asarray(taps), block)
        self.hist = np.zeros(self.p - 1, np.complex64)

    def _set(self, taps: np.ndarray, block: int):
        self.p = len(taps)
        self.nfft = _good_size(max(4 * self.p, block + self.p - 1 if block else 4 * self.p))
        self.step = self.nfft - (self.p - 1)
        self.H = np.fft.fft(taps, self.nfft).astype(np.complex64)

    def set_taps(self, taps: np.ndarray):
        old = self.hist
        self._set(np.asarray(taps), 0)
        h = np.zeros(self.p - 1, np.complex64)
        k = min(len(old), len(h))
        if k:
            h[-k:] = old[-k:]
        self.hist = h

    def __call__(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, np.complex64)
        n = len(x)
        if n == 0:
            return np.zeros(0, np.complex64)
        buf = np.concatenate([self.hist, x])
        out = np.empty(n, np.complex64)
        nblk = -(-n // self.step)
        need = (nblk - 1) * self.step + self.nfft
        if len(buf) < need:
            buf = np.concatenate([buf, np.zeros(need - len(buf), np.complex64)])
        frames = np.lib.stride_tricks.sliding_window_view(buf, self.nfft)[::self.step][:nblk]
        y = np.fft.ifft(np.fft.fft(frames, axis=1) * self.H, axis=1)[:, self.p - 1:]
        out[:] = y.reshape(-1)[:n]
        if self.p > 1:
            self.hist = np.concatenate([self.hist, x])[-(self.p - 1):]
        return out
