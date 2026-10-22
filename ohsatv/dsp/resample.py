"""Rational polyphase resampler (up/down), vectorised with a gather: every
output sample is one row of a (outputs x taps) product, so there is no
Python loop per sample. Used between the engine's 48 kHz and whatever the
sound card runs at (44.1 kHz is 147/160; copied from OpenHamShack NB)."""
from __future__ import annotations

from math import gcd

import numpy as np

from .fir import lowpass


class Resampler:
    def __init__(self, up: int, down: int, taps_per_phase: int = 24, atten_db: float = 70.0):
        g = gcd(up, down)
        self.up, self.down = up // g, down // g
        self.tpp = taps_per_phase
        n = self.up * self.tpp
        # Cutoff at 90 % of the narrower Nyquist, in units of the upsampled rate.
        fc = 0.5 / max(self.up, self.down) * 0.9
        proto = lowpass(fc, 1.0, n, atten_db) * self.up
        self.table = proto.reshape(self.tpp, self.up).T[:, ::-1].astype(np.float32)  # [phase, tap] newest-last
        self.hist = np.zeros(self.tpp - 1, np.float32)
        self.pos = (self.tpp - 1) * self.up      # next output position, in 1/up input samples from hist[0]
        self.m = np.arange(self.tpp)

    def __call__(self, x: np.ndarray) -> np.ndarray:
        buf = np.concatenate([self.hist, np.asarray(x, np.float32)])
        last = (len(buf) - 1) * self.up          # largest usable position
        if self.pos > last:
            k = 0
        else:
            k = (last - self.pos) // self.down + 1
        pos = self.pos + self.down * np.arange(k, dtype=np.int64)
        i = pos // self.up
        p = pos % self.up
        idx = i[:, None] - (self.tpp - 1) + self.m[None, :]
        y = np.einsum("ij,ij->i", self.table[p], buf[idx]) if k else np.zeros(0, np.float32)
        nxt = self.pos + self.down * k
        drop = max(0, nxt // self.up - (self.tpp - 1))
        self.hist = buf[drop:]
        self.pos = nxt - drop * self.up
        return y.astype(np.float32)
