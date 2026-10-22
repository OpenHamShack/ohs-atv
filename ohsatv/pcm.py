"""Digital sound for the narrow formats: 8-bit mu-law samples protected by
a Hamming SECDED code, and the per-frame packing between the engine's
48 kHz audio and a sample rate tied to the line rate.

Why this codec (narrow formats, DESIGN.md):

- **The sample rate is a whole number of samples per frame**, taken from
  the line structure: one sample per line (15 625 Hz) for Mini-MAC (the
  rejected sound-in-syncs format used one per two sync pulses, 7 625 Hz,
  and is why the packer takes any count a frame). The sound then needs
  no clock of its own - the receiver's line lock is its clock - and a
  frame's sound travels with its picture, so the 0.16 s transmit loop of a
  static picture stays seam-free.
- **8-bit mu-law** (mu = 255, G.711's law in its smooth form) gives ~38 dB
  SNR over a 40 dB range of levels, more than an FM-ATV sound channel
  delivers near threshold and plenty for speech. A 10-bit linear sample is
  better only at full scale and costs two more bits per sample in a line
  that has none to spare.
- **SECDED Hamming (13, 8)** - four Hamming parity bits and an overall
  parity bit - corrects any single bit error in a sample and detects
  any double one. Near threshold FM noise arrives as clicks a fraction of
  a microsecond long, so errors come one or two at a time: one is
  corrected, two are detected and the sample is concealed (the last good
  sample, decaying), which is far less audible than a wrong one.
"""
from __future__ import annotations

import numpy as np

from .dsp.resample import Resampler

MU = 255.0
AUDIO_RATE = 48_000
FRAME_RATE = 25


def mulaw_encode(x: np.ndarray) -> np.ndarray:
    """-1..1 floats -> 0..255 codes (sign in the top bit)."""
    x = np.clip(np.asarray(x, np.float64), -1.0, 1.0)
    y = np.log1p(MU * np.abs(x)) / np.log1p(MU)
    mag = np.minimum(np.round(y * 127.0), 127).astype(np.int64)
    return np.where(x < 0, 128 + mag, mag).astype(np.uint8)


def mulaw_decode(c: np.ndarray) -> np.ndarray:
    c = np.asarray(c, np.int64)
    mag = (c & 127) / 127.0
    x = (np.expm1(mag * np.log1p(MU))) / MU
    return np.where(c >= 128, -x, x).astype(np.float32)


# --- SECDED (13, 8) --------------------------------------------------------
# Codeword bit positions 1..12 are a Hamming (12, 8) code (parity at 1, 2,
# 4, 8; data at the rest), position 13 the overall parity of all twelve.
_DATA_POS = np.array([3, 5, 6, 7, 9, 10, 11, 12])
_PAR_POS = np.array([1, 2, 4, 8])


def _encode_table() -> np.ndarray:
    t = np.zeros((256, 13), np.uint8)
    for v in range(256):
        cw = np.zeros(13, np.uint8)
        for i, p in enumerate(_DATA_POS):
            cw[p - 1] = (v >> (7 - i)) & 1
        for p in _PAR_POS:
            cw[p - 1] = sum(cw[q - 1] for q in range(1, 13) if q & p) & 1
        cw[12] = cw[:12].sum() & 1
        t[v] = cw
    return t


_ENC = _encode_table()
CODE_BITS = 13


def secded_encode(codes: np.ndarray) -> np.ndarray:
    """uint8 codes (n,) -> bits (n, 13)."""
    return _ENC[np.asarray(codes, np.uint8)]


def secded_decode(bits: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """bits (n, 13) of 0/1 -> (codes uint8, good bool, corrected bool).
    `good` is False where a double error was detected."""
    b = np.asarray(bits, np.int64) & 1
    pos = np.arange(1, 13)
    syn = np.zeros(len(b), np.int64)
    for p in _PAR_POS:
        syn |= ((b[:, :12][:, (pos & p) > 0].sum(axis=1) & 1) * p)
    overall = b.sum(axis=1) & 1
    single = overall == 1
    fix = single & (syn >= 1) & (syn <= 12)
    rows = np.flatnonzero(fix)
    b = b.copy()
    b[rows, syn[rows] - 1] ^= 1
    double = (~single) & (syn != 0)
    # A syndrome pointing past the codeword with odd parity is also more
    # than one error.
    bad = double | (single & (syn > 12))
    data = b[:, _DATA_POS - 1]
    codes = (data * (1 << np.arange(7, -1, -1))).sum(axis=1).astype(np.uint8)
    return codes, ~bad, fix


# --- per-frame packing -----------------------------------------------------
class FramePacker:
    """48 kHz audio blocks -> exactly `per_frame` samples a frame at
    per_frame x 25 Hz. A polyphase resampler (its own anti-alias filter at
    90 % of the lower Nyquist) feeds a FIFO that is primed half a frame
    deep, so every frame finds its samples whatever the resampler's
    rounding does to a block's length."""

    def __init__(self, per_frame: int):
        self.per_frame = int(per_frame)
        rate = self.per_frame * FRAME_RATE
        self.rs = Resampler(rate, AUDIO_RATE)
        self.fifo = np.zeros(self.per_frame // 2, np.float32)

    def __call__(self, audio: np.ndarray | None) -> np.ndarray:
        if audio is None or len(audio) == 0:
            audio = np.zeros(AUDIO_RATE // FRAME_RATE, np.float32)
        self.fifo = np.concatenate([self.fifo, self.rs(np.asarray(audio, np.float32))])
        if len(self.fifo) < self.per_frame:
            self.fifo = np.concatenate([np.zeros(self.per_frame - len(self.fifo), np.float32), self.fifo])
        out, self.fifo = self.fifo[:self.per_frame], self.fifo[self.per_frame:]
        return out


class FrameUnpacker:
    """`per_frame` received samples a frame -> 48 kHz audio, with erasures
    concealed: a sample whose codeword had a detected double error is
    replaced by the last good one, decaying towards silence so a long run
    of errors fades rather than holding a tone."""

    def __init__(self, per_frame: int):
        self.per_frame = int(per_frame)
        self.rs = Resampler(AUDIO_RATE, self.per_frame * FRAME_RATE)
        self.last = 0.0

    def __call__(self, samples: np.ndarray, good: np.ndarray | None = None) -> np.ndarray:
        x = np.asarray(samples, np.float32).copy()
        if good is not None and not good.all():
            last = self.last
            for i in np.flatnonzero(~good) if (~good).sum() < len(x) else range(len(x)):
                # Hold the previous sample (the one before i, already fixed).
                prev = x[i - 1] if i > 0 else last
                x[i] = 0.9 * prev
        if len(x):
            self.last = float(x[-1])
        return self.rs(x)

    def silence(self) -> np.ndarray:
        return self.rs(np.zeros(self.per_frame, np.float32))
