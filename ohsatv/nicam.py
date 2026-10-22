"""NICAM-728 digital stereo sound, System I: coding, modulation, reception.

Everything here is from ETSI EN 300 163 V1.2.1 (1998), "NICAM 728:
transmission of two-channel digital sound with terrestrial television
systems B, G, H, I, K1 and L" (the clause numbers below are its), with BBC
RD 1990/6 (A.J. Bower, "NICAM 728 - digital two-channel sound for
terrestrial television") for the reasoning behind it. hacktv's encoder
(fsphil/hacktv, src/nicam728.c, GPLv3) was used to check this one bit for
bit (tests/test_nicam.py runs the same frames through both when a copy of
it can be built); nothing of it runs here.

The parts, in the order a sample meets them:

- **Sound coding (4.2.5).** 32 kHz, 14-bit two's complement, ITU-T J.17
  pre-emphasis. Each 1 ms block of 32 samples per channel is cut to 10 bits
  at the position its largest sample needs (five coding ranges: drop 4, 3,
  2, 1 or no LSBs), the range sent as a 3-bit scale factor. One even-parity
  bit per sample over its six MSBs; the scale factors ride on those parity
  bits ("signalling in parity", 4.2.5.5): each scale-factor bit inverts the
  parity of nine samples, and the receiver takes it by majority, then
  restores the parity to find which samples were hit.
- **The frame (4.1).** 728 bits a millisecond: frame alignment word
  01001110, control C0-C4 (C0 flips every 8 frames, a 16-frame sequence;
  C1-C3 the application, 000 stereo; C4 "the analogue sound is the same
  programme"), 11 additional data bits, then 64 words of 11 bits - A1 B1
  A2 B2 ... for stereo, each LSB first and parity last - bit-interleaved 16
  apart (4.1.2) and scrambled after the FAW by x^9 + x^4 + 1 from all ones
  (4.1.3).
- **Modulation (5.2, 5.3).** Bit pairs as phase changes (00 0, 01 -90,
  11 -180, 10 -270 degrees) at 364 kbaud, shaped for System I by
  H(f) = cos(pi f / (2 x 364 kHz)) up to 364 kHz at each end, so the two
  ends together are a 100 % raised cosine. **Not 40 %**: that is systems
  B, G, H, K1 and L (5.2.5.1), whose channel is narrower above the sound.
  The carrier is 6.552 MHz above vision, 20 dB below peak vision power
  (100:1, 5.2.4): it occupies 6.188-6.916 MHz, beside the FM sound at 6.0.

Why it is built the way it is, here:

- **Blocks of whole milliseconds.** One NICAM frame is 1 ms - 364 symbols -
  and every sample rate the program uses is a whole number of samples a
  millisecond at some decimation of it (`low_rate`): 2 MS/s from 10, 16 and
  20, 2.5 from 5 and 12.5. So the symbol grid repeats exactly every frame,
  and the shaping is done in the frequency domain on whole frames (the
  spectrum of N symbols lands on the FFT grid of N frames of samples
  exactly), then interpolated up by an integer. A per-sample polyphase
  filter at 20 MS/s cost ~40 ms a block, more than the frame lasts.
- **A looped picture stays seamless.** A static source is sent as a 0.16 s
  loop (systems.build_loop): 160 frames, ten 16-frame sequences, and a
  tone that repeats too - except the differential encoder's phase, which
  would land anywhere after 58 240 symbols and step once a loop (one
  symbol error every 0.16 s). The transmitter closes it: in the last frame
  of every 16-frame sequence two of the additional-data bits (AD1, AD2,
  "reserved for future applications", 4.2.3 - receivers ignore them) are
  chosen so the sequence's phase changes sum to a whole turn.
- **Reception is feed-forward.** Symbol timing from the square law's line
  at the symbol rate (Oerder and Meyr), per millisecond; the frequency
  error from the fourth power's rotation; the phase by Viterbi and
  Viterbi's fourth-power average; decisions coherent, then differential.
  No loop runs per sample in Python; a 40 ms block at 20 MS/s costs a few
  ms after the channel filter.
"""
from __future__ import annotations

import math

import numpy as np

from .dsp.filters import Channel, Ols, design, taper
from .dsp.fir import lowpass
from .dsp.resample import Resampler

# --- the standard -----------------------------------------------------------
FRAME_BITS = 728
FRAME_SYMS = 364
SYMBOL_RATE = 364_000.0
FRAME_S = 1e-3
CARRIER_I = 6.552e6                     # above the vision carrier, System I (5.2.3.2)
HALF_BW = SYMBOL_RATE                   # cos shaping reaches zero at 364 kHz (5.2.5.2)
LEVEL_DB = -20.0                        # against peak vision power (5.2.4)
AUDIO_RATE = 32_000
BLOCK = 32                              # samples per channel per frame
FAW = np.array([0, 1, 0, 0, 1, 1, 1, 0], np.uint8)

# Application control bits C1 C2 C3 (table 1).
MODES = {"stereo": (0, 0, 0), "dual": (0, 1, 0), "mono": (1, 0, 0), "data": (1, 1, 0)}
_MODE_OF = {v: k for k, v in MODES.items()}

# Scale factor R2 R1 R0 (table 3) <-> LSBs dropped: ranges 1-4 drop 4..1,
# range 5 none, with protection ranges 5, 6, 7 as 100, 010, 001 (000 is
# also range 5).
_SHIFT_OF_SCALE = np.array([0, 0, 0, 1, 0, 2, 3, 4], np.int64)


def _prbs(n: int = FRAME_BITS - 8) -> np.ndarray:
    """The energy-dispersal sequence (4.1.3, figure 2): nine stages from all
    ones, the fifth and ninth fed back. The standard gives its start as
    0000 0111 1011 1110 0010 (tested)."""
    s = [1] * 9
    out = np.empty(n, np.uint8)
    for i in range(n):
        o = s[4] ^ s[8]
        out[i] = o
        s = [o] + s[:-1]
    return out


PRBS = _prbs()

# Bit interleaving (4.1.2): transmitted bit 24 + j carries frame bit
# 24 + 44 (j mod 16) + j // 16 - adjacent bits of the frame structure go out
# 16 apart, so a burst of errors hits different samples.
_J = np.arange(704)
TX_ORDER = np.concatenate([np.arange(24), 24 + 44 * (_J % 16) + _J // 16])
RX_ORDER = np.argsort(TX_ORDER)


def _signal_map(mode: str) -> np.ndarray:
    """For each of the 64 words, which scale-factor bit its parity carries:
    0-5 = R2A, R1A, R0A, R2B, R1B, R0B (stereo) or R2n, R1n, R0n, R2n+1,
    R1n+1, R0n+1 (mono), -1 = none (4.2.5.5)."""
    m = np.full(64, -1, np.int64)
    i = np.arange(54)                    # words 1..54, zero-based
    if mode == "stereo":
        # i = 1, 7, 13... R2A; 2, 8... R2B; 3, 9... R1A; 4... R1B; 5... R0A; 6... R0B.
        g = i % 6
        m[i] = np.array([0, 3, 1, 4, 2, 5])[g]
    else:
        m[:27] = np.arange(27) % 3
        m[27:54] = 3 + np.arange(27) % 3
    return m


SIGNAL = {"stereo": _signal_map("stereo"), "mono": _signal_map("mono")}


# --- companding ----------------------------------------------------------------
def scale_factor(block: np.ndarray) -> np.ndarray:
    """3-bit scale factors (as integers R2R1R0) for blocks of 14-bit samples,
    shape (..., 32) -> (...): from the largest magnitude in the block, a
    negative sample counting as its ones' complement (figure 3)."""
    b = np.asarray(block, np.int64)
    v = np.where(b < 0, ~b, b).max(axis=-1)
    return np.select([v >= 4096, v >= 2048, v >= 1024, v >= 512, v >= 256, v >= 128],
                     [7, 6, 5, 3, 4, 2], 1).astype(np.int64)


def compress(block: np.ndarray, scale: np.ndarray) -> np.ndarray:
    """14-bit samples -> 10-bit two's complement codes (0..1023)."""
    sh = _SHIFT_OF_SCALE[np.asarray(scale)][..., None]
    return (np.asarray(block, np.int64) >> sh) & 0x3FF


def expand(code: np.ndarray, scale: np.ndarray) -> np.ndarray:
    """10-bit codes -> 14-bit samples. Half a step is added back: the
    compressor truncates, so the middle of the step is the best guess."""
    c = np.asarray(code, np.int64)
    c = np.where(c >= 512, c - 1024, c)
    sh = _SHIFT_OF_SCALE[np.asarray(scale)][..., None]
    return (c << sh) + np.where(sh > 0, 1 << np.maximum(sh - 1, 0), 0)


def _parity6(code: np.ndarray) -> np.ndarray:
    """Even parity over the six MSBs of a 10-bit code."""
    x = (np.asarray(code, np.int64) >> 4) & 0x3F
    p = np.zeros(x.shape, np.int64)
    for k in range(6):
        p ^= (x >> k) & 1
    return p


# --- frames ----------------------------------------------------------------------
def encode_frames(words: np.ndarray, scales: np.ndarray, frame0: int = 0, mode: str = "stereo",
                  c4: int = 1, ad: np.ndarray | None = None) -> np.ndarray:
    """The 728 transmitted bits of each frame, shape (F, 728), uint8.

    `words`: (F, 64) 10-bit codes in frame order (D1..D64: A1 B1 A2 B2 ...
    for stereo). `scales`: (F, 2) the two scale factors (FE1, FE2: channel A
    and B, or the frame's two mono blocks). `frame0`: the number of the
    first frame counted from the start of a 16-frame sequence (C0 is 1 in
    the first eight). `ad`: (F, 11) additional data, zeros if None."""
    words = np.asarray(words, np.int64)
    scales = np.asarray(scales, np.int64)
    F = len(words)
    sig = SIGNAL["stereo" if mode == "stereo" else "mono"]
    par = _parity6(words)
    # Each signalling word's parity is inverted by its scale-factor bit.
    sbits = np.stack([(scales[:, 0] >> 2) & 1, (scales[:, 0] >> 1) & 1, scales[:, 0] & 1,
                      (scales[:, 1] >> 2) & 1, (scales[:, 1] >> 1) & 1, scales[:, 1] & 1], axis=1)
    on = sig >= 0
    par[:, on] ^= sbits[:, sig[on]]
    # 64 words of 11 bits, each LSB first, parity last.
    wbits = ((words[:, :, None] >> np.arange(10)) & 1)
    data = np.concatenate([wbits, par[:, :, None]], axis=2).reshape(F, 704)
    fr = np.zeros((F, FRAME_BITS), np.uint8)
    fr[:, :8] = FAW
    n = frame0 + np.arange(F)
    fr[:, 8] = ((n % 16) < 8).astype(np.uint8)
    fr[:, 9:12] = MODES[mode]
    fr[:, 12] = c4
    if ad is not None:
        fr[:, 13:24] = np.asarray(ad, np.uint8)
    fr[:, 24:] = data
    tx = fr[:, TX_ORDER]
    tx[:, 8:] ^= PRBS
    return tx


def decode_frames(tx: np.ndarray) -> dict:
    """The inverse of encode_frames, with the receiver's error detection.

    Returns {"words" (F, 64), "scales" (F, 2), "errors" (F, 64) bool - the
    parity check failed once the scale factors were taken out, "mode" (F,)
    names, "c0", "c4", "faw_errors" (F,)}. The scale-factor bits are taken
    by majority over their nine words; the mode decides which nine."""
    tx = np.asarray(tx, np.uint8)
    F = len(tx)
    rx = tx.copy()
    rx[:, 8:] ^= PRBS
    fr = rx[:, RX_ORDER]
    faw_err = (fr[:, :8] != FAW).sum(axis=1)
    c = fr[:, 9:12].astype(np.int64)
    modes = np.array([_MODE_OF.get(tuple(r), "other") for r in c.tolist()])
    data = fr[:, 24:].reshape(F, 64, 11).astype(np.int64)
    words = (data[:, :, :10] << np.arange(10)).sum(axis=2)
    syn = _parity6(words) ^ data[:, :, 10]               # 1 where parity (as sent) is odd
    scales = np.zeros((F, 2), np.int64)
    errors = np.zeros((F, 64), bool)
    for kind in ("stereo", "mono"):
        rows = (modes == "stereo") if kind == "stereo" else (modes != "stereo")
        if not rows.any():
            continue
        sig = SIGNAL[kind]
        s = syn[rows]
        bits = np.zeros((len(s), 6), np.int64)
        for b in range(6):
            bits[:, b] = (s[:, sig == b].sum(axis=1) >= 5).astype(np.int64)
        scales[rows, 0] = bits[:, 0] * 4 + bits[:, 1] * 2 + bits[:, 2]
        scales[rows, 1] = bits[:, 3] * 4 + bits[:, 4] * 2 + bits[:, 5]
        expect = np.where(sig >= 0, bits[:, np.maximum(sig, 0)], 0)
        errors[rows] = (s ^ expect).astype(bool)
    return {"words": words, "scales": scales, "errors": errors, "mode": modes,
            "c0": fr[:, 8].copy(), "c4": fr[:, 12].copy(), "faw_errors": faw_err, "ad": fr[:, 13:24].copy()}


# --- DQPSK -------------------------------------------------------------------------
def bits_to_steps(bits: np.ndarray) -> np.ndarray:
    """Bit pairs (A, B) -> phase steps in quarter turns, counter-clockwise
    (table 4: 00 0, 10 -270 = +90, 11 180, 01 -90 = +270)."""
    b = np.asarray(bits, np.int64).reshape(-1, 2)
    return (b[:, 0] ^ b[:, 1]) + 2 * b[:, 1]


def steps_to_bits(steps: np.ndarray) -> np.ndarray:
    s = np.asarray(steps, np.int64) & 3
    b = s >> 1
    a = (s & 1) ^ b
    return np.stack([a, b], axis=1).reshape(-1).astype(np.uint8)


_QPSK = np.exp(1j * (np.pi / 4 + np.pi / 2 * np.arange(4))).astype(np.complex64)


# --- J.17 pre-emphasis ---------------------------------------------------------------
# H(s) = (s + a) / (s + b), a = 3000 rad/s, b = 3000 sqrt(75): 0 dB at high
# frequencies, -18.75 dB at low ones (table 2's H(f) is its power, 10 log of
# (1 + (w/3000)^2) / (75 + (w/3000)^2)). As a first-order digital filter at
# 32 kHz: the zero by the bilinear transform pre-warped (at 477 Hz warping
# moves nothing), the low-frequency gain exactly J.17's, and the pole where
# the largest error from 20 Hz to 15 kHz is least - 0.05 dB. The bilinear
# transform for the pole too was 0.37 dB out near 15 kHz unwarped and, pre-
# warped, 0.49 dB low at low frequencies, which a NICAM receiver elsewhere
# would hear as the bass that much quieter (tested against J.17's formula).
J17_POLE = 0.4391
J17_ZERO = (1 - math.tan(3000.0 / (2 * AUDIO_RATE))) / (1 + math.tan(3000.0 / (2 * AUDIO_RATE)))
J17_GAIN = (1 / math.sqrt(75.0)) * (1 - J17_POLE) / (1 - J17_ZERO)


def j17_response(f) -> np.ndarray:
    """|H| of the digital pre-emphasis at f Hz (32 kHz sampling)."""
    z = np.exp(-2j * np.pi * np.asarray(f, np.float64) / AUDIO_RATE)
    return np.abs(J17_GAIN * (1 - J17_ZERO * z) / (1 - J17_POLE * z))


# The app's audio is +-1 full scale = +-50 kHz of FM sound deviation (the
# modulators' sound_dev). EN 300 163 4.2.5.2 ties the two together through
# the alignment level, 0 dBu0s: +-17 kHz of System I FM sound deviation at
# 400 Hz, and 24.3 dB below the NICAM coding range's maximum at 400 Hz. So
# a tone at 17/50 of full scale here is 24.3 dB below 14-bit full scale
# after pre-emphasis: the same sound is as loud from NICAM as from the FM
# carrier, and the receiver's fallback from one to the other does not jump.
ALIGN = 17.0 / 50.0
GAIN = 10 ** (-24.3 / 20) / (ALIGN * float(j17_response(400.0)))


def _one_pole(x: np.ndarray, p: float, state: list) -> np.ndarray:
    """y[n] = p y[n-1] + x[n], vectorised from its closed form in chunks
    short enough that p^-k stays inside float64 (the way
    modem.audio_deemph does it)."""
    x = np.asarray(x, np.float64)
    out = np.empty(len(x))
    y = state[0]
    if len(x) == 0:
        return out
    chunk = max(1, int(300.0 / max(-math.log(p), 1e-9)))
    for s in range(0, len(x), chunk):
        seg = x[s:s + chunk]
        k = np.arange(len(seg), dtype=np.float64)
        pk = p ** k
        out[s:s + len(seg)] = pk * (p * y + np.cumsum(seg / pk))
        y = out[s + len(seg) - 1]
    state[0] = y
    return out


class Emphasis:
    """J.17 pre-emphasis (`inverse` False) or de-emphasis at 32 kHz, one
    channel, streaming. Pre-emphasis: a zero at 0.91, then a pole at 0.40;
    de-emphasis the other way round."""

    def __init__(self, inverse: bool = False):
        self.inverse = inverse
        self.prev = 0.0
        self.pole = [0.0]

    def __call__(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, np.float64)
        if not len(x):
            return x
        z, p, g = (J17_POLE, J17_ZERO, 1 / J17_GAIN) if self.inverse else (J17_ZERO, J17_POLE, J17_GAIN)
        prev = np.concatenate([[self.prev], x[:-1]])
        self.prev = float(x[-1])
        return _one_pole(g * (x - z * prev), p, self.pole)


# --- rates -----------------------------------------------------------------------------
def low_rate(fs: float, at_least: float = 1.2e6, near: float = 2.0e6) -> tuple[int, float]:
    """(D, fs / D): the decimation that gives a rate near 2 MS/s with a
    whole number of samples per 1 ms frame - the grid the shaping and the
    symbol timing work on."""
    best = None
    for d in range(1, int(fs // at_least) + 1):
        r = fs / d
        if abs(r / 1000 - round(r / 1000)) < 1e-9 and r >= at_least:
            if best is None or abs(r - near) < abs(best[1] - near):
                best = (d, r)
    if best is None:
        raise ValueError(f"no NICAM rate at {fs / 1e6:g} MS/s")
    return best


def shaping(f: np.ndarray) -> np.ndarray:
    """System I's spectrum shaping, one end (5.2.5.2)."""
    f = np.abs(np.asarray(f, np.float64))
    return np.where(f <= SYMBOL_RATE, np.cos(np.pi * f / (2 * SYMBOL_RATE)), 0.0)


# --- the transmitter ------------------------------------------------------------------
class AudioCoder:
    """48 kHz audio -> NICAM frames (bits), stereo, streaming. Mono audio
    is sent as A = B. `close_loop`: choose AD1/AD2 so every 16-frame
    sequence's phase steps sum to a whole turn (module docstring)."""

    def __init__(self, c4: int = 1, close_loop: bool = True):
        self.rs = [Resampler(2, 3), Resampler(2, 3)]
        self.emph = [Emphasis(), Emphasis()]
        self.fifo = np.zeros((0, 2), np.int64)
        self.frame = 0
        self.c4 = c4
        self.close_loop = close_loop
        self.turns = 0                       # quarter turns since this 16-frame sequence began

    def push(self, audio: np.ndarray | None) -> None:
        if audio is None:
            return
        a = np.asarray(audio, np.float32)
        if a.ndim == 1:
            a = np.stack([a, a], axis=1)
        ch = []
        for k in range(2):
            y = self.rs[k](a[:, k])
            ch.append(self.emph[k](y))
        n = min(len(ch[0]), len(ch[1]))
        x = np.stack([ch[0][:n], ch[1][:n]], axis=1) * (GAIN * 8192.0)
        q = np.clip(np.round(x), -8192, 8191).astype(np.int64)
        self.fifo = np.concatenate([self.fifo, q])

    def frames_ready(self) -> int:
        return len(self.fifo) // BLOCK

    def take(self, count: int) -> np.ndarray:
        """`count` frames of bits, from the queued audio and then silence."""
        have = min(count, self.frames_ready())
        pcm = np.zeros((count * BLOCK, 2), np.int64)
        pcm[:have * BLOCK] = self.fifo[:have * BLOCK]
        self.fifo = self.fifo[have * BLOCK:]
        a = pcm[:, 0].reshape(count, BLOCK)
        b = pcm[:, 1].reshape(count, BLOCK)
        sa, sb = scale_factor(a), scale_factor(b)
        words = np.empty((count, 64), np.int64)
        words[:, 0::2] = compress(a, sa)
        words[:, 1::2] = compress(b, sb)
        bits = encode_frames(words, np.stack([sa, sb], axis=1), self.frame, "stereo", self.c4)
        if self.close_loop:
            self._close(bits)
        self.frame += count
        return bits

    def drop(self, frames: int) -> None:
        """Forget queued audio (the transmitter has fallen behind it)."""
        self.fifo = self.fifo[frames * BLOCK:]

    def _close(self, bits: np.ndarray) -> None:
        for i in range(len(bits)):
            n = (self.frame + i) % 16
            steps = bits_to_steps(bits[i])
            if n == 15:
                # AD1, AD2 are frame bits 14 and 15, before interleaving's
                # reach and scrambled by PRBS[6], PRBS[7]: one symbol.
                rest = (int(steps.sum()) - int(steps[7]) + self.turns) % 4
                want = (-rest) % 4
                b = want >> 1
                a = (want & 1) ^ b
                bits[i, 14], bits[i, 15] = a, b
                self.turns = 0
            else:
                self.turns = (self.turns + int(steps.sum())) % 4


class Transmitter:
    """NICAM-728 as complex baseband at fs, unit power, centred on 0 Hz:
    the modulator moves it to its carrier (6.552 MHz above vision, or onto
    the FM baseband) and sets its level. `eq(f)`: a gain to apply at f Hz
    from the NICAM carrier (the radio's own roll-off, devices/ad936x.py).

    Called with each block's 48 kHz audio and the number of samples
    wanted; the coder runs a frame or two ahead of the shaping, which needs
    the symbols after a frame to finish it (the latency is ~2 ms)."""

    CHUNK = 8                                # frames shaped at a time

    def __init__(self, fs: float, eq=None, c4: int = 1, close_loop: bool = True):
        self.fs = float(fs)
        self.D, self.r = low_rate(self.fs)
        self.nr = int(round(self.r / 1000))
        self.sps = self.r / SYMBOL_RATE
        self.coder = AudioCoder(c4=c4, close_loop=close_loop)
        self.phase = 0                       # differential state, quarter turns
        self.syms = np.zeros(FRAME_SYMS, np.complex64)     # frame -1: nothing sent yet
        self.sym_frame0 = -1                 # frame number of syms[0]
        self.next_frame = 0                  # first frame not yet shaped
        self.out = np.zeros(0, np.complex64)
        F = self.CHUNK + 2
        n = F * self.nr
        f = np.fft.fftfreq(n, 1 / self.r)
        h = shaping(f) * (np.asarray(eq(f), np.float64) if eq is not None else 1.0)
        # The symbol train's spectrum repeats every SYMBOL_RATE; on this grid
        # bin m of the waveform is bin m mod L of the symbols' FFT.
        self.idx = np.round(f / (SYMBOL_RATE / (F * FRAME_SYMS))).astype(np.int64) % (F * FRAME_SYMS)
        self.H = (h * self.sps).astype(np.complex64)
        # Interpolation to fs: polyphase, D phases of `tpp` taps; passes
        # +-0.4 MHz flat, the first image (centred on r) is 70 dB down.
        tpp = 8
        proto = lowpass(0.5 * self.r, self.fs, self.D * tpp, 70.0) * self.D if self.D > 1 else np.ones(1)
        self.tpp = tpp if self.D > 1 else 1
        self.poly = proto.reshape(self.tpp, self.D)[::-1].astype(np.float32) if self.D > 1 else None
        self.hist = np.zeros(self.tpp - 1, np.complex64)
        self.sent: list | None = None        # a list: every frame's bits are kept there (for measuring)

    def _symbols(self, count: int) -> None:
        bits = self.coder.take(count)
        if self.sent is not None:
            self.sent.append(bits.copy())
        steps = bits_to_steps(bits.reshape(-1))
        ph = (self.phase + np.cumsum(steps)) % 4
        self.phase = int(ph[-1])
        self.syms = np.concatenate([self.syms, _QPSK[ph]])

    def _shape(self) -> np.ndarray:
        """The next CHUNK frames of waveform at the low rate."""
        F = self.CHUNK
        need_to = self.next_frame + F + 1                # one frame of symbols after
        have_to = self.sym_frame0 + len(self.syms) // FRAME_SYMS
        if have_to < need_to:
            self._symbols(need_to - have_to)
        a = (self.next_frame - 1 - self.sym_frame0) * FRAME_SYMS
        s = self.syms[a:a + (F + 2) * FRAME_SYMS]
        y = np.fft.ifft(np.fft.fft(s)[self.idx] * self.H)
        self.next_frame += F
        # Keep from the frame before the next chunk on.
        drop = (self.next_frame - 1 - self.sym_frame0) * FRAME_SYMS
        self.syms = self.syms[drop:]
        self.sym_frame0 = self.next_frame - 1
        return y[self.nr:(F + 1) * self.nr].astype(np.complex64)

    def _up(self, x: np.ndarray) -> np.ndarray:
        if self.poly is None:
            return x
        buf = np.concatenate([self.hist, x])
        self.hist = buf[len(buf) - (self.tpp - 1):]
        # Real and imaginary parts as one float32 matrix product (BLAS), the
        # taps' window built as a contiguous (2n x tpp) array: a complex
        # product against real taps went through complex128, and one on a
        # strided window view missed BLAS altogether - 3-5x slower.
        n = len(x)
        X = np.empty((2 * n, self.tpp), np.float32)
        for j in range(self.tpp):
            X[:n, j] = buf.real[j:j + n]
            X[n:, j] = buf.imag[j:j + n]
        y = X @ self.poly
        out = np.empty((n, self.D), np.complex64)
        out.real = y[:n]
        out.imag = y[n:]
        return out.reshape(-1)

    def __call__(self, audio: np.ndarray | None, n: int) -> np.ndarray:
        self.coder.push(audio)
        short = n - len(self.out)
        # Never more than a few frames of sound waiting beyond what this
        # call sends: audio that arrives faster than the samples asked for
        # would otherwise make the sound later and later.
        want = -(-max(short, 0) // (self.nr * self.D)) + self.CHUNK + 1
        if self.coder.frames_ready() > want + 8:
            self.coder.drop(self.coder.frames_ready() - want - 4)
        chunks = []
        while short > 0:
            chunks.append(self._shape())
            short -= len(chunks[-1]) * self.D
        # One interpolation for the whole block: a product per chunk cost
        # its overhead five times over at 20 MS/s.
        buf = np.concatenate([self.out, self._up(np.concatenate(chunks))]) if chunks else self.out
        self.out = buf[n:]
        return buf[:n]


# --- the receiver ----------------------------------------------------------------------
# Lock: four frame alignment words in a row, exactly, at 728-bit spacing
# (a random stream does that at one offset in 2^32 / 364); lost after
# eight frames in a row whose FAW has more than one bit wrong.
LOCK_FRAMES = 4
LOSE_FRAMES = 8
# Sound from NICAM while the estimated bit-error rate is below USE_BER; back
# to the analogue sound above DROP_BER (hysteresis). Measured through the
# AM-VSB chain at 10 MS/s (tests/test_nicam.py, AmChain): at 1.1e-3 the
# tones were 57 and 39 dB SINAD against the FM sound's 45, at 2e-3 56 and
# 30, at 3.7e-3 13 - concealment holds and then gives way all at once. So
# NICAM is heard while it is at least as good as the FM sound.
USE_BER = 5e-4
DROP_BER = 1e-3


class Receiver:
    """NICAM-728 from IQ (AM-VSB: `real` False, the carrier at `centre` Hz
    from the LO) or from an FM discriminator's output (`real` True, the
    subcarrier at `centre`). Returns 48 kHz stereo audio, shape (n, 2),
    while locked; the status is in `stats`.

    `rx_shape`: the receiving radio's own response (devices/ad936x.py),
    undone in the matched filter as the video filters undo it."""

    def __init__(self, fs: float, centre: float = CARRIER_I, real: bool = False, rx_shape=None):
        self.fs = float(fs)
        self.real = real
        self.rx_shape = rx_shape
        self.D, self.r = low_rate(self.fs)
        self.nr = int(round(self.r / 1000))
        self.sps = self.r / SYMBOL_RATE
        m = np.arange(self.nr)
        self.w = np.exp(-2j * np.pi * FRAME_SYMS * m / self.nr).astype(np.complex64)
        self.chan = None
        self._tune(centre)
        self.reset()

    def _tune(self, centre: float) -> None:
        from .devices import ad936x
        self.centre = float(centre)
        self.chan = Channel(self.fs, centre, 0.9e6, self.D, real=self.real)
        c = self.chan.centre
        shape = self.rx_shape

        def h(f):
            g = shaping(f)
            if shape is not None:
                g = g * (ad936x.correction(shape, np.abs(c + f)) if self.real else
                         ad936x.correction(shape, c + f))
            return g
        self.mf = Ols(design(h, self.r, 63).astype(np.complex64))

    def reset(self) -> None:
        self.buf = np.zeros(0, np.complex64)
        self.buf0 = 0                    # stream index (low rate) of buf[0]
        self.seg = 0                     # next 1 ms segment to time
        self.next_pos = None
        self.xs = []                     # recent timing vectors
        self.freq = 0.0                  # rad / symbol
        self.rot = 0.0
        self.v_tail = np.zeros(0, np.complex64)
        self.ph_last = None
        self.q_last = None
        self.bits = np.zeros(0, np.uint8)
        self.sync = None                 # bit offset of the next frame in self.bits, when locked
        self.bad = 0
        self.frame_no = None             # number in the 16-frame sequence of the next frame
        self.prev_c0 = None
        self.ber = None
        self.frames = 0
        self.last = np.zeros(2)          # last good sample per channel, for concealment
        self.deemph = [Emphasis(inverse=True), Emphasis(inverse=True)]
        self.up = [Resampler(3, 2), Resampler(3, 2)]
        self.stats = {"lock": False, "ber": None, "mode": "", "c4": None, "frames": 0}
        self.got: list | None = None         # a list: every decided bit is kept there (for measuring)

    # -- samples to symbols -------------------------------------------------------
    def retune(self, centre: float) -> None:
        """Follow a moved carrier (the AM receiver's tracked vision carrier)."""
        if abs(centre - self.centre) > 10e3:
            self._tune(centre)
            self.buf = np.zeros(0, np.complex64)
            self.buf0 = 0
            self.seg = 0
            self.next_pos = None
            self.xs = []

    def _symbols(self, y: np.ndarray) -> np.ndarray:
        self.buf = np.concatenate([self.buf, y]) if len(self.buf) else y
        nr, sps = self.nr, self.sps
        if self.seg * nr < self.buf0:
            self.seg = -(-self.buf0 // nr)
        s0 = self.seg * nr - self.buf0
        # Every whole millisecond held, with room after it for the last
        # symbol's interpolation.
        nseg = max(0, (len(self.buf) - s0 - int(2 * sps) - 4) // nr)
        if not nseg:
            return np.zeros(0, np.complex64)
        segs = self.buf[s0:s0 + nseg * nr].reshape(nseg, nr)
        # Oerder and Meyr: |y|^2 has a line at the symbol rate whose phase
        # is the symbol instants'. Each millisecond's, averaged with the
        # three before it.
        x = ((segs.real ** 2 + segs.imag ** 2) @ self.w).astype(np.complex128)
        xx = np.concatenate([np.asarray(self.xs, np.complex128), x])
        c = np.concatenate([[0], np.cumsum(xx)])
        k = len(self.xs) + np.arange(nseg)
        avg = c[k + 1] - c[np.maximum(k - 3, 0)]
        self.xs = list(xx[-3:])
        phis = (-np.angle(avg) / (2 * np.pi)) % 1.0 * sps
        pos_all = []
        for i, phi in enumerate(phis.tolist()):
            seg = self.seg + i
            base = seg * nr + phi
            # Each millisecond's symbols, from where the last one's ended:
            # when the phase wraps between two, none is sampled twice or
            # missed.
            lo = base - sps / 2 if self.next_pos is None else max(base - sps / 2, self.next_pos - sps / 2)
            hi = (seg + 1) * nr + phi - sps / 2
            j0 = math.ceil((lo - base) / sps)
            j1 = math.ceil((hi - base) / sps)
            if j1 > j0:
                pos_all.append(base + sps * np.arange(j0, j1))
                self.next_pos = float(pos_all[-1][-1] + sps)
        self.seg += nseg
        out = self._interp(np.concatenate(pos_all) - self.buf0) if pos_all else np.zeros(0, np.complex64)
        keep = max(0, self.seg * nr - self.buf0 - nr)
        if keep:
            self.buf = self.buf[keep:]
            self.buf0 += keep
        return out

    def _interp(self, p: np.ndarray) -> np.ndarray:
        """Cubic (4-point Lagrange) interpolation of buf at positions p."""
        i = np.floor(p).astype(np.int64)
        t = (p - i).astype(np.float32)
        i = np.clip(i, 1, len(self.buf) - 3)
        ym1, y0, y1, y2 = self.buf[i - 1], self.buf[i], self.buf[i + 1], self.buf[i + 2]
        c0 = -t * (t - 1) * (t - 2) / 6
        c1 = (t + 1) * (t - 1) * (t - 2) / 2
        c2 = -(t + 1) * t * (t - 2) / 2
        c3 = (t + 1) * t * (t - 1) / 6
        return (c0 * ym1 + c1 * y0 + c2 * y1 + c3 * y2).astype(np.complex64)

    def _decide(self, z: np.ndarray) -> np.ndarray:
        """Symbols -> phase steps: frequency, phase, quadrant, difference."""
        if not len(z):
            return np.zeros(0, np.int64)
        # Unit RMS first: from an FM discriminator the symbols are in Hz,
        # ~1e5, and their fourth powers' products overflow float32.
        z = z * np.float32(1.0 / max(float(np.sqrt(np.mean(np.abs(z) ** 2))), 1e-30))
        v = z ** 4
        if len(v) > 1:
            f = np.angle(np.sum(v[1:] * np.conj(v[:-1]))) / 4
            # A frequency error moves z^4 by 4 x its phase a symbol; past
            # 45 degrees a symbol it would alias, which is +-45 kHz.
            self.freq = f if self.frames == 0 and self.ph_last is None else 0.8 * self.freq + 0.2 * f
        k = np.arange(len(z), dtype=np.float64)
        rot = self.rot + self.freq * (k + 1)
        self.rot = float(rot[-1] % (2 * np.pi))
        z = z * np.exp(-1j * rot).astype(np.complex64)
        # Viterbi and Viterbi: the fourth power strips the modulation; its
        # average over the 32 symbols around each one, a quarter of its
        # angle, is the phase (the newest symbols of a block see only the
        # half before them).
        W = 32
        lead = len(self.v_tail)
        v = np.concatenate([self.v_tail, z ** 4])
        c = np.concatenate([[0], np.cumsum(v.astype(np.complex128))])
        i = lead + np.arange(len(z))
        a = c[np.minimum(i + W // 2, len(v))] - c[np.maximum(i - W // 2, 0)]
        self.v_tail = v[-(W // 2):]
        ph4 = np.angle(-a)
        if self.ph_last is not None:
            ph4 = np.unwrap(np.concatenate([[self.ph_last], ph4]))[1:]
        else:
            ph4 = np.unwrap(ph4)
        self.ph_last = float(ph4[-1])
        ang = np.angle(z * np.exp(-1j * ph4 / 4))
        q = (np.floor(ang / (np.pi / 2)).astype(np.int64)) % 4
        prev = np.concatenate([[q[0] if self.q_last is None else self.q_last], q[:-1]])
        self.q_last = int(q[-1])
        return (q - prev) % 4

    # -- bits to frames -------------------------------------------------------------
    def _frames(self) -> list[np.ndarray]:
        b = self.bits
        out = []
        if self.sync is None:
            need = (LOCK_FRAMES + 1) * FRAME_BITS
            if len(b) < need:
                return out
            # Every even offset in one frame's length, through every frame
            # held: a run of LOCK_FRAMES exact words anywhere will do. (The
            # first version looked at the first four frames of each block
            # only; a picture that hit the same frame's FAW with one error
            # every block - a looped test card does that - kept it unlocked
            # at 60 dB.)
            o = np.arange(0, FRAME_BITS, 2)
            nfr = (len(b) - FRAME_BITS) // FRAME_BITS
            idx = o[:, None, None] + FRAME_BITS * np.arange(nfr)[None, :, None] + np.arange(8)
            ok = (b[idx] == FAW).all(axis=2).astype(np.int64)            # (offsets, frames)
            c = np.concatenate([np.zeros((len(o), 1), np.int64), np.cumsum(ok, axis=1)], axis=1)
            runs = c[:, LOCK_FRAMES:] - c[:, :-LOCK_FRAMES] == LOCK_FRAMES      # (offsets, starts)
            hit = runs.any(axis=1)
            first = np.argmax(runs, axis=1)
            # Silence makes every frame the same apart from C0, so any byte
            # of the frame that happens to read 01001110 repeats at 728 bits
            # too (seen: five such offsets in a silent stream). Only the true
            # offset descrambles and deinterleaves into words whose parity
            # checks pass; anywhere else half of them fail.
            cand = []
            for c in np.flatnonzero(hit):
                s = int(o[c]) + FRAME_BITS * int(first[c])
                d = decode_frames(b[s + FRAME_BITS * np.arange(LOCK_FRAMES)[:, None] + np.arange(FRAME_BITS)])
                if d["errors"].mean() < 0.1 and all(m in MODES for m in d["mode"]):
                    cand.append(s)
            if len(cand) == 1:
                # From the run's first frame: the frames before it go unheard.
                self.sync = cand[0]
                self.bad = 0
                self.frame_no = None
            else:
                # Keep the last frame's worth, which may hold the start of a FAW.
                self.bits = b[len(b) - FRAME_BITS:]
                return out
        while self.sync + FRAME_BITS <= len(self.bits):
            fr = self.bits[self.sync:self.sync + FRAME_BITS]
            errs = int((fr[:8] != FAW).sum())
            if errs > 1:
                self.bad += 1
                if self.bad >= LOSE_FRAMES:
                    self.sync = None
                    self.bits = self.bits[len(self.bits) - FRAME_BITS:]
                    self.stats.update(lock=False, ber=None, mode="")
                    self.ber = None
                    return out
            else:
                self.bad = 0
            out.append(fr.copy())
            self.sync += FRAME_BITS
        cut = self.sync
        self.bits = self.bits[cut:]
        self.sync = 0
        return out

    def _audio(self, frames: list[np.ndarray]) -> np.ndarray:
        if not frames:
            return np.zeros((0, 2), np.float32)
        d = decode_frames(np.stack(frames))
        F = len(frames)
        e = d["errors"]
        # Bit errors from the parity checks: a sample's check covers seven
        # bits, so at low error rates a failure is one bit in seven.
        rate = float(e.sum()) / (F * 64 * 7)
        self.ber = rate if self.ber is None else 0.9 * self.ber + 0.1 * rate
        self.frames += F
        modes = d["mode"]
        mode = str(modes[-1])
        # Frame numbering from C0: frame 1 is the first with C0 = 1.
        nums = np.zeros(F, np.int64)
        for i, c0 in enumerate(d["c0"].tolist()):
            if self.prev_c0 == 0 and c0 == 1:
                self.frame_no = 1
            elif self.frame_no is not None:
                self.frame_no = self.frame_no % 16 + 1
            nums[i] = self.frame_no or 0
            self.prev_c0 = c0
        self.stats.update(lock=True, ber=self.ber, mode=mode, c4=int(d["c4"][-1]), frames=self.frames)
        pcm = []
        if (modes == "stereo").all():
            # The usual case, all frames at once; concealment frame by frame
            # only where a parity check failed.
            w, sc = d["words"], d["scales"]
            ab = np.stack([expand(w[:, 0::2], sc[:, 0]), expand(w[:, 1::2], sc[:, 1])], axis=2).astype(np.float64)
            bad = np.stack([e[:, 0::2], e[:, 1::2]], axis=2)
            for i in range(F):
                for ch in range(2):
                    if bad[i, :, ch].any():
                        ab[i, :, ch] = self._conceal(ab[i, :, ch], bad[i, :, ch], ch)
                    self.last[ch] = ab[i, -1, ch]
            pcm.append(ab.reshape(F * BLOCK, 2))
            modes = ()
        for i in range(len(modes)):
            w, sc, er = d["words"][i], d["scales"][i], e[i]
            if modes[i] == "stereo":
                a = expand(w[0::2], sc[0])
                b = expand(w[1::2], sc[1])
                pcm.append(np.stack([self._conceal(a, er[0::2], 0), self._conceal(b, er[1::2], 1)], axis=1))
            elif modes[i] in ("mono", "dual") and nums[i] % 2 == 1:
                # M1 in the odd frames: 64 consecutive samples, two blocks.
                m = np.concatenate([expand(w[:32], sc[0]), expand(w[32:], sc[1])])
                m = self._conceal(m, er, 0)
                pcm.append(np.stack([m, m], axis=1))
            elif modes[i] in ("mono", "dual"):
                continue
        if not pcm:
            return np.zeros((0, 2), np.float32)
        x = np.concatenate(pcm).astype(np.float64) / (8192.0 * GAIN)
        ch = [self.up[k](self.deemph[k](x[:, k]).astype(np.float32)) for k in range(2)]
        n = min(len(ch[0]), len(ch[1]))
        return np.stack([ch[0][:n], ch[1][:n]], axis=1).astype(np.float32)

    def _conceal(self, x: np.ndarray, bad: np.ndarray, ch: int) -> np.ndarray:
        """A sample whose parity failed is replaced by interpolation between
        its good neighbours (the last good sample of the previous frame
        before the first): a wrong MSB is a loud click, an interpolated
        sample is inaudible."""
        x = x.astype(np.float64)
        if bad.any():
            good = ~bad
            if good.sum() < len(x) // 4:
                x = np.full(len(x), self.last[ch] * 0.5)
            else:
                i = np.arange(len(x))
                xp = np.concatenate([[-1], i[good]])
                fp = np.concatenate([[self.last[ch]], x[good]])
                x[bad] = np.interp(i[bad], xp, fp)
        self.last[ch] = x[-1]
        return x

    # -- the whole -------------------------------------------------------------------------
    # While nothing is locked, one block in this many is looked at: a
    # receiver listens for NICAM on every station whose band holds it, and
    # most send none - a fifth of the receive thread's time at 20 MS/s for
    # nothing. A 40 ms block holds forty frames, so one look finds a NICAM
    # signal (four FAWs in a row); it costs at most 120 ms more to lock.
    SEARCH_EVERY = 4

    def __call__(self, x: np.ndarray) -> np.ndarray:
        if self.sync is None:
            self._skip = (getattr(self, "_skip", self.SEARCH_EVERY - 1) + 1) % self.SEARCH_EVERY
            if self._skip:
                self._gap = True
                return np.zeros((0, 2), np.float32)
            if getattr(self, "_gap", False):
                # The blocks between were not heard: start the symbol
                # stream afresh rather than splice across the gap.
                self._gap = False
                self.buf = np.zeros(0, np.complex64)
                self.buf0 = self.seg = 0
                self.next_pos = None
                self.xs, self.bits = [], np.zeros(0, np.uint8)
                self.v_tail = np.zeros(0, np.complex64)
                self.ph_last = self.q_last = None
                self.rot = 0.0
                self.chan = Channel(self.fs, self.centre, 0.9e6, self.D, real=self.real)
                self.mf.hist[:] = 0
                if self.got is not None:
                    self.got.clear()
        y = self.chan(x)
        if not len(y):
            return np.zeros((0, 2), np.float32)
        y = self.mf(y)
        z = self._symbols(y)
        steps = self._decide(z)
        if len(steps):
            got = steps_to_bits(steps)
            if self.got is not None:
                self.got.append(got)
            self.bits = np.concatenate([self.bits, got])
        out = self._audio(self._frames())
        if self.sync is None:
            self.stats.update(lock=False, ber=None, mode="")
        return out

    @property
    def active(self) -> bool:
        """Whether NICAM's sound should be the one heard (with hysteresis on
        the error rate, so the sound does not flip between the two)."""
        st = self.stats
        if not st["lock"] or st["ber"] is None or st["mode"] not in ("stereo", "mono", "dual"):
            self._active = False
            return False
        was = getattr(self, "_active", False)
        self._active = st["ber"] < (DROP_BER if was else USE_BER)
        return self._active


# --- what fits ---------------------------------------------------------------------------
TOP = CARRIER_I + HALF_BW                 # 6.916 MHz above vision
GUARD = 0.05e6
# The transmitted waveform's peak over its RMS (unit-power symbols through
# the System I shaping): 1.48 measured over 10 s of random data. The
# modulators leave this much headroom for it.
PEAK = 1.5
# A receiver listens for NICAM only where its band ends inside this
# fraction of the sample rate from the LO: past it the AD936x's last
# half-band (6 dB down at fs/2) takes more than the matched filter's 6 dB
# of correction can put back.
RX_EDGE = 0.45
# FM-ATV has no NICAM standard. Its subcarrier deviates the carrier by this
# much (RMS) - 1 dB under the analogue sound subcarrier at IARU V.2's index
# (0.07 x 6 MHz peak, 297 kHz RMS), and as much as IARU V.2's 7 MHz p-p at
# 20 MS/s leaves room for under Carson's rule (fm_plan: 18.9 of 19.0 MHz).
# System I's spacing - NICAM 7 dB under the FM sound, 133 kHz here - was
# tried first: an FM discriminator's noise rises with frequency, and at
# 6.55 MHz it buried NICAM by 20 dB CNR (bit errors 9 %) where the picture
# was perfect. At 265 kHz it is error-free to ~25 dB CNR and fails (1e-3)
# near 20, where the analogue sound still gives ~39 dB SINAD and the
# picture locks down to ~5 - so on FM the analogue sound is the fallback
# that matters, and the receiver uses it (tests/test_nicam.py, FmChain).
FM_RMS_DEV = 265e3


def am_top() -> float:
    """The highest frequency above the vision carrier that NICAM occupies,
    with a little room for the filters' skirts."""
    return TOP + GUARD
