"""Filters designed from a frequency response, applied by FFT.

ATV filters are judged by their shape - a vestigial sideband's Nyquist
slope, a pre-emphasis curve - not by a cutoff and an attenuation, so they
are designed by sampling the wanted response on a dense grid, taking it to
the time domain and windowing (the frequency-sampling method). The result
is linear phase, which video needs: a filter that delays 1 MHz differently
from 100 kHz smears every vertical edge into a ghost.

`Ols` is a streaming overlap-save convolver for any block length. At
10-20 MS/s it is the dominant cost of the receiver and transmitter, so the
FFTs are complex64 and both scaled (numpy 2.2's unscaled complex64
transforms are several times slower - see Ols).
"""
from __future__ import annotations

from typing import Callable

import numpy as np

from .fir import _good_size, kaiser_beta


def design(response: Callable[[np.ndarray], np.ndarray], fs: float, ntaps: int,
           atten_db: float = 60.0) -> np.ndarray:
    """Linear-phase FIR (complex if the response is not symmetric about 0 Hz)
    approximating `response(f)` for f in Hz, -fs/2..fs/2. `ntaps` odd."""
    ntaps = int(ntaps) | 1
    m = _good_size(max(16 * ntaps, 4096))
    f = np.fft.fftfreq(m, 1.0 / fs)
    h = np.fft.ifft(np.asarray(response(f), np.complex128))
    h = np.fft.fftshift(h)
    c = m // 2
    h = h[c - ntaps // 2:c + ntaps // 2 + 1] * np.kaiser(ntaps, kaiser_beta(atten_db))
    if np.allclose(h.imag, 0, atol=1e-12 * np.abs(h).max()):
        return h.real.copy()
    return h


def taper(f: np.ndarray, edge: float, width: float) -> np.ndarray:
    """1 below `edge - width/2`, 0 above `edge + width/2`, raised-cosine
    between: half amplitude at `edge`."""
    x = np.clip((f - edge) / width + 0.5, 0.0, 1.0)
    return 0.5 + 0.5 * np.cos(np.pi * x)


class Ols:
    """Streaming FIR by FFT overlap-save, complex64 in and out. Accepts any
    block length; the output is the same as one long convolution (delayed by
    (ntaps - 1) / 2, the filter's group delay).

    `shift` (Hz, with `fs`) also moves the filtered signal down by that
    much, rounded to the FFT's bin grid (`self.shift` is what was used):
    output j is y[j] exp(-j 2 pi shift j / fs), j counting outputs from
    `self.pos`. On the bin grid the mixer is the same table for every FFT
    frame times one phase per frame: two cheap multiplies instead of a
    phasor computed per sample. The AM demodulator's synchronous detector
    spent more on its float64 carrier phase than on the filter
    (docs/PERF.md)."""

    def __init__(self, taps: np.ndarray, nfft: int = 0, shift: float = 0.0, fs: float = 0.0):
        taps = np.asarray(taps)
        self.p = len(taps)
        self.nfft = nfft or _good_size(max(8 * self.p, 8192))
        self.step = self.nfft - (self.p - 1)
        # Both transforms scale (forward by norm="forward", inverse by
        # default), because numpy 2.2's *unscaled* complex64 transforms are
        # ~4x slower (measured: 4.9 ms against 1.3 ms for 50 x 8192 points).
        # That divides by nfft twice; H puts one back.
        self.H = (np.fft.fft(taps, self.nfft) * self.nfft).astype(np.complex64)
        self.hist = np.zeros(self.p - 1, np.complex64)
        self.delay = (self.p - 1) / 2
        self.k0 = int(round(shift / fs * self.nfft)) if shift else 0
        self.shift = self.k0 * fs / self.nfft if shift else 0.0
        m = np.arange(self.p - 1, self.nfft)
        self.rot = np.exp(-2j * np.pi * ((self.k0 * m) % self.nfft) / self.nfft).astype(np.complex64)
        self.pos = 0

    def __call__(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, np.complex64)
        n = len(x)
        if n == 0:
            return np.zeros(0, np.complex64)
        # History, block and the zeros that pad the last frame, written once
        # into one buffer (two concatenations copied the block twice).
        nblk = -(-n // self.step)
        need = (nblk - 1) * self.step + self.nfft
        h = self.p - 1
        buf = np.empty(max(need, h + n), np.complex64)
        buf[:h] = self.hist
        buf[h:h + n] = x
        buf[h + n:] = 0
        frames = np.lib.stride_tricks.sliding_window_view(buf, self.nfft)[::self.step][:nblk]
        Y = np.fft.fft(frames, axis=1, norm="forward")
        Y *= self.H
        y = np.fft.ifft(Y, axis=1)[:, self.p - 1:]
        if self.k0:
            # Rotating the bins down by k0 would multiply each frame by
            # exp(-j 2 pi k0 m / nfft) from the frame's own sample 0: done
            # here in place, as a multiply (cheaper than np.roll's copy of
            # the spectrum), with the per-frame phase that puts back where
            # that sample is in the stream, so the shift is continuous across
            # frames and calls.
            y *= self.rot
            starts = self.pos + np.arange(nblk, dtype=np.int64) * self.step - (self.p - 1)
            y *= np.exp(-2j * np.pi * ((self.k0 * starts) % self.nfft) / self.nfft).astype(np.complex64)[:, None]
        out = y.reshape(-1)[:n]
        self.pos += n
        if self.p > 1:
            self.hist = buf[n:n + self.p - 1].copy()
        return out.astype(np.complex64, copy=False)


class OlsReal:
    """Ols for a real filter on a real signal: rfft/irfft halve the work.
    float32 in and out."""

    def __init__(self, taps: np.ndarray, nfft: int = 0):
        taps = np.asarray(taps, np.float64)
        if np.iscomplexobj(taps):
            raise ValueError("OlsReal needs real taps")
        self.p = len(taps)
        self.nfft = nfft or _good_size(max(8 * self.p, 8192))
        self.nfft += self.nfft % 2
        self.step = self.nfft - (self.p - 1)
        # rfft with norm="forward" scales by 1/nfft; irfft by default scales
        # by 1/nfft too (see Ols on why both scale).
        self.H = (np.fft.rfft(taps, self.nfft) * self.nfft).astype(np.complex64)
        self.hist = np.zeros(self.p - 1, np.float32)
        self.delay = (self.p - 1) / 2

    def __call__(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, np.float32)
        n = len(x)
        if n == 0:
            return np.zeros(0, np.float32)
        # History, block and the zeros that pad the last frame, written once
        # into one buffer (two concatenations copied the block twice).
        nblk = -(-n // self.step)
        need = (nblk - 1) * self.step + self.nfft
        h = self.p - 1
        buf = np.empty(max(need, h + n), np.float32)
        buf[:h] = self.hist
        buf[h:h + n] = x
        buf[h + n:] = 0
        frames = np.lib.stride_tricks.sliding_window_view(buf, self.nfft)[::self.step][:nblk]
        y = np.fft.irfft(np.fft.rfft(frames, axis=1, norm="forward") * self.H, self.nfft, axis=1)
        out = y[:, self.p - 1:].reshape(-1)[:n]
        if self.p > 1:
            self.hist = buf[n:n + self.p - 1].copy()
        return out.astype(np.float32, copy=False)


class Channel:
    """Extract one narrow band from a wide stream and decimate it:
    overlap-save with the frequency shift and the rate change done inside
    the FFT (shift = rotate the bins, decimate = fold the spectrum), so the
    full-rate output is never formed. Used for the sound carrier, which is
    ~200 kHz wide inside 10-20 MHz of IQ.

    The bin rotation shifts by a whole number of bins, so the centre is
    rounded to the bin grid (fs / nfft: ~1-2 kHz) - an FM sound demodulator
    does not notice a 1 kHz offset. Each FFT frame's rotation restarts at
    the frame's first sample; the phase correction per frame (`_ph`) keeps
    the shifted signal continuous across frames, without which the FM
    demodulator would click at every frame boundary.

    Only the bins where the filter is not negligible (above -80 dB) are
    rotated and folded; the rest of the spectrum would only add zeros, and
    multiplying, rolling and summing all of it cost a third as much as the
    transform. `real=True` takes a real (float32) input - the FM
    discriminator's output, where the sound subcarrier lives - and uses
    rfft, half the work of transforming it as complex, when the band lies
    inside 0..fs/2 (the filter passes positive frequencies only, so the
    negative half carried nothing it used).
    """

    def __init__(self, fs: float, centre: float, bw: float, dec: int, ntaps: int = 0, real: bool = False):
        self.fs = float(fs)
        self.dec = int(dec)
        self.bw = bw
        ntaps = ntaps or (int(8 * fs / bw) | 1)
        nfft = _good_size(max(8 * ntaps, 16384))
        nfft += (-nfft) % self.dec
        while (nfft // self.dec) != _good_size(nfft // self.dec):
            nfft += self.dec
        self.nfft = nfft
        self.k0 = int(round(centre / fs * nfft))
        self.centre = self.k0 * fs / nfft
        h = design(lambda f: taper(np.abs(f - self.centre), bw / 2, bw * 0.3), fs, ntaps)
        self.p = len(h)
        H = (np.fft.fft(h, nfft) * (nfft // self.dec)).astype(np.complex64)   # scaling: see Ols
        k = nfft // self.dec
        # The band, as offsets from k0, and where each lands in the folded
        # (decimated) spectrum: offset mod k, as rotating by -k0 and summing
        # the dec slices of the spectrum would put it.
        a = np.abs(H)
        rel = (np.flatnonzero(a > 1e-4 * a.max()) - self.k0 + nfft // 2) % nfft - nfft // 2
        rel = np.arange(rel.min(), rel.max() + 1)
        self.bins = (self.k0 + rel) % nfft
        self.Hb = H[self.bins]
        self.dest = rel % k
        # Within a run of k consecutive offsets each destination is used once,
        # so each run can be added with one fancy-indexed +=.
        self.runs = [slice(i, min(i + k, len(rel))) for i in range(0, len(rel), k)]
        self.real = bool(real) and self.bins.min() > 0 and self.bins.max() < nfft // 2 and \
            np.all(np.diff(self.bins) == 1)
        self.step = nfft - (self.p - 1)
        self.step -= self.step % self.dec
        # Samples not yet consumed by a frame, starting with p-1 zeros of
        # history; `pos` is the stream index of the first output of the next
        # frame (buf[p-1]).
        self.buf = np.zeros(self.p - 1, np.float32 if self.real else np.complex64)
        self.pos = 0

    def __call__(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, np.float32 if self.real else np.complex64)
        buf = np.concatenate([self.buf, x])
        nblk = (len(buf) - self.nfft) // self.step + 1 if len(buf) >= self.nfft else 0
        if nblk <= 0:
            self.buf = buf
            return np.zeros(0, np.complex64)
        frames = np.lib.stride_tricks.sliding_window_view(buf, self.nfft)[::self.step][:nblk]
        if self.real:
            X = np.fft.rfft(frames, axis=1, norm="forward")
            Y = X[:, self.bins[0]:self.bins[-1] + 1] * self.Hb
        else:
            X = np.fft.fft(frames, axis=1, norm="forward")
            Y = X[:, self.bins] * self.Hb
        k = self.nfft // self.dec
        fold = np.zeros((nblk, k), np.complex64)
        for r in self.runs:
            fold[:, self.dest[r]] += Y[:, r]
        y = np.fft.ifft(fold, axis=1)
        # Frame b's sample 0 is stream sample (pos + b*step - (p-1)); the bin
        # rotation's time-domain factor restarted there, so put back the
        # phase it would have had.
        starts = self.pos + np.arange(nblk) * self.step - (self.p - 1)
        ph = np.exp(-2j * np.pi * ((self.k0 * starts) % self.nfft) / self.nfft).astype(np.complex64)
        skip = -(-(self.p - 1) // self.dec)
        y = (y * ph[:, None])[:, skip:skip + self.step // self.dec]
        self.pos += nblk * self.step
        self.buf = buf[nblk * self.step:]
        return y.reshape(-1).astype(np.complex64, copy=False)
