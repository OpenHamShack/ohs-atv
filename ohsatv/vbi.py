"""Teletext on the picture's blanked lines: the waveform, and the slicer.

The physical layer of WST System B for 625 lines (ETSI EN 300 706 V1.2.1,
clauses 4-6; facts quoted in docs/atv-research.md section 20):

- NRZ at 444 fH = **6.9375 Mbit/s**, 45 bytes a line: the clock run-in
  1010101010101010, the framing code 11100100 (both in transmission order),
  then the 42-byte packet (ohsatv/ttx.py), every byte LSB first.
- **Levels**: 0 at black level (0 V - PAL has no set-up), 1 at 66 % of
  black to peak white, 0.462 V.
- **Timing**: the middle of the penultimate '1' of the run-in at **12.0
  us** after 0H (+0.4 / -1.0 us). The run-in's first bit is then centred
  at 10.27 us and the last data bit at 62.0 us - inside the line, clear of
  the next sync.
- **Spectrum**: "skew symmetrical about 0.5 x bit rate, substantially zero
  by 5 MHz". That is a Nyquist pulse; here a raised-cosine spectrum centred
  on 3.47 MHz with roll-off 0.44, so it reaches zero at exactly 5.0 MHz.
  A raised cosine is zero at every other bit's centre, so the receiver
  samples each bit with no interference from its neighbours.
- **Lines**: 6-22 and 318-335 may carry data (clause 4.1). The default here
  is 7-22 and 320-335 - 32 a frame, 800 packets a second: line 6 is next
  to the post-equalising pulses and lines 318-319 hold the field-2
  equalising pulse and its neighbour in pal.py's line map.

**The waveform is built as a matrix product**: every line starts on a
whole sample (pal.py), so bit k of every line has the same position
relative to the line's first sample, and a (360 x N) matrix of the pulse at
each bit's position, built once, turns a (lines x 360) array of bits into
the lines' waveforms in one call. 32 lines at 20 MS/s is 32 x 360 x 1280
multiply-adds, ~0.3 ms.

**The slicer** works on the decoder's lines (decoder.py: volts, black at
0, sync at -0.3, each line cut at the whole sample nearest its fitted
start - so its true start is up to half a sample away, and differs line to
line). For each line:

1. the run-in is a 3.47 MHz tone for 2.3 us: its phase, from one complex
   correlation, gives where the bits are to a small fraction of a bit,
   whatever the line's half-sample offset or the transmitter's timing;
2. its mean is the slicing level (half way between 0 and 1, whatever the
   receiver's gain and offset), and its size against the noise says
   whether there is teletext on the line at all;
3. (the run-in is looked for at the nominal timing and 4 and 7 bits early,
   so a transmitter anywhere in the standard's +0.4 / -1.0 us is found);
   the framing code at that phase and +-1 and +-2 bit pairs either side,
   accepting one bit wrong, as decoders do (EN 300 706 F.4:
   a "framing code acceptance window considerably wider than the
   transmission limits");
4. the 360 bits are read at their centres by band-limited interpolation
   (a windowed sinc - the signal has nothing above 5 MHz, so at 10 MS/s or
   more its samples determine it between them), and cut at the slicing
   level.

Below 10 MS/s the samples do not determine a 5 MHz signal; slicing is
still attempted (the engine says the mode cannot carry teletext, ohsatv/
narrow.py), and fails honestly - the framing code is not found.
"""
from __future__ import annotations

import math

import numpy as np

from . import pal

BIT_RATE = 444 * pal.FH                  # 6.9375 Mbit/s
BIT_US = 1e6 / BIT_RATE                  # 0.14414 us
LEVEL_1 = 0.66 * pal.WHITE               # 0.462 V
REF_US = 12.0                            # 0H to the middle of the run-in's penultimate '1'
FIRST_BIT_US = REF_US - 12 * BIT_US      # the run-in's first bit: bits 0..15, ones at 0, 2 .. 14
ROLLOFF = 5.0e6 / (BIT_RATE / 2) - 1.0   # 0.4414: a raised cosine that is zero by 5 MHz
PREFIX = bytes((0x55, 0x55, 0x27))       # run-in, framing code 11100100 sent LSB first
FRAMING = 0x27
BITS = 360
# The lowest sample rate a receiver slices at: the run-in's 3.47 MHz tone
# must be below the Nyquist frequency (8 MS/s is the program's first rate
# above 6.94).
MIN_FS = 2 * BIT_RATE / 2 + 1.0

# Lines a frame (1-based frame line numbers) - clause 4.1 allows 6-22 and
# 318-335; see the module docstring for the default.
ALLOWED = tuple(range(6, 23)) + tuple(range(319, 336))
DEFAULT_LINES = tuple(range(7, 23)) + tuple(range(320, 336))


def parse_lines(spec: str) -> tuple[int, ...]:
    """'7-22,320-335' -> the line numbers, sorted. Raises ValueError with a
    sentence for anything outside what the standard allows."""
    out: set[int] = set()
    for part in str(spec).replace(" ", "").split(","):
        if not part:
            continue
        a, sep, b = part.partition("-")
        try:
            lo, hi = int(a), int(b) if sep else int(a)
        except ValueError:
            raise ValueError(f"Teletext lines: {part!r} is not a line or a range like 7-22") from None
        if lo > hi:
            lo, hi = hi, lo
        for ln in range(lo, hi + 1):
            if ln not in ALLOWED:
                raise ValueError(f"Teletext lines: line {ln} cannot carry teletext (6-22 and 319-335 can)")
            out.add(ln)
    if not out:
        raise ValueError("Teletext lines: name at least one line, e.g. 7-22,320-335")
    return tuple(sorted(out))


def field_lines(lines) -> tuple[tuple[int, ...], tuple[int, ...]]:
    lines = tuple(sorted(lines))
    return tuple(x for x in lines if x <= 313), tuple(x for x in lines if x > 313)


def rc_pulse(t: np.ndarray, alpha: float = ROLLOFF) -> np.ndarray:
    """Raised-cosine (Nyquist) pulse, t in bit periods: 1 at 0, 0 at every
    other whole bit."""
    t = np.asarray(t, np.float64)
    s = np.sinc(t)
    d = 1.0 - (2.0 * alpha * t) ** 2
    near = np.abs(d) < 1e-8
    c = np.where(near, np.pi / 4, np.cos(np.pi * alpha * t) / np.where(near, 1.0, d))
    return s * c


def packet_bits(pkt: bytes) -> np.ndarray:
    """A 42-byte packet -> its 360 bits in transmission order (with the
    run-in and framing code), uint8."""
    return np.unpackbits(np.frombuffer(PREFIX + bytes(pkt), np.uint8), bitorder="little")


def bits_to_bytes(bits: np.ndarray) -> np.ndarray:
    """(..., 8 k) bits in transmission order -> (..., k) bytes."""
    return np.packbits(np.asarray(bits, np.uint8), axis=-1, bitorder="little")


class Inserter:
    """Packets -> the waveforms of their lines, added to a 625 x N frame."""

    def __init__(self, fs: float, lines=DEFAULT_LINES, span_bits: int = 10, ref_us: float = REF_US):
        """`ref_us`: the timing reference (0H to the penultimate run-in
        one); the standard's 12.0, which is what we send - other values are
        for testing the slicer against other transmitters' timing."""
        self.fs = float(fs)
        self.n = pal.samples_per_line(fs)
        self.lines = tuple(sorted(lines))
        self.f1, self.f2 = field_lines(self.lines)
        tb = self.fs / BIT_RATE                                     # samples a bit
        c = (ref_us - 12 * BIT_US) * 1e-6 * self.fs + np.arange(BITS) * tb    # bit centres, samples from 0H
        s = np.arange(self.n)
        x = (s[None, :] - c[:, None]) / tb
        # Truncated at +-span_bits: the raised cosine with this roll-off is
        # under 0.1 % of its peak 10 bits out.
        m = np.where(np.abs(x) <= span_bits, rc_pulse(x), 0.0)
        self.matrix = (LEVEL_1 * m).astype(np.float32)

    def waveforms(self, packets) -> np.ndarray:
        """(k, N) float32 volts for k packets (None -> a line of zeros)."""
        bits = np.zeros((len(packets), BITS), np.float32)
        for i, p in enumerate(packets):
            if p is not None:
                bits[i] = packet_bits(p)
        return bits @ self.matrix

    def insert(self, comp: np.ndarray, field1, field2) -> np.ndarray:
        """Add the two fields' packets to `comp` (625 x N, volts) in place.
        `field1` / `field2`: packets for this frame's lines of each field,
        in line order (None = leave the line as it is). The lines are
        blanked in pal.py's picture (they are before line 23 / 336), so
        adding is placing."""
        pk = list(field1)[:len(self.f1)] + list(field2)[:len(self.f2)]
        ln = list(self.f1[:len(field1)]) + list(self.f2[:len(field2)])
        rows = [i for i, p in enumerate(pk) if p is not None]
        if not rows:
            return comp
        w = self.waveforms([pk[i] for i in rows])
        comp[[ln[i] - 1 for i in rows]] += w
        return comp


class Slicer:
    """Decoded VBI lines -> packets."""

    # A line may carry teletext when its run-in tone stands this far above
    # the rest of the same samples (the tone's amplitude against the
    # residual's standard deviation). Measured through the simulator's
    # channel (AM 10 MS/s, FM 20 MS/s, 8-30 dB CNR, ~200 lines each): noise
    # lines have a median of 0.5 and reach 1.85; teletext 8-44 at 30 dB,
    # 1.6-9 at 15 dB. Above MIN_RUNIN the framing code is looked for; a
    # code with one bit wrong is taken only above SURE_RUNIN, where noise
    # never reached - so a noise line becomes a false packet only by
    # matching all 8 framing bits at one of five offsets (5/256 of the ~1 %
    # of noise lines past the gate) and then both address bytes' Hamming.
    MIN_RUNIN = 1.5
    SURE_RUNIN = 2.5
    # And in volts: the decoder's lines are in volts (clamped on sync and
    # back porch), so a run-in is a tone of ~0.22 V about a mean of 0.23 V
    # (half the 0.462 V of a one). Gates at about a third of each, which a
    # 9 dB loss at 3.5 MHz would still pass: a quiet line's noise has a mean
    # near 0 V, and without this one in ~8 500 such lines passed both the
    # tone test and the framing code (a picture with no teletext, 30 dB).
    MIN_TONE_V = 0.08
    MEAN_V = (0.08, 0.40)
    SHIFTS = (0, -4, -7)            # run-in windows, bits from the nominal timing

    def __init__(self, fs: float, lines=ALLOWED, taps: int = 12):
        self.fs = float(fs)
        self.n = pal.samples_per_line(fs)
        self.lines = tuple(sorted(lines))
        self.tb = self.fs / BIT_RATE
        self.c0 = FIRST_BIT_US * 1e-6 * self.fs
        self.taps = taps
        # The run-in's tone over bits 1..14 (the first and last are skirted
        # by the edges of the packet), as a complex reference - at the
        # nominal timing and at 4 and 7 bits early. EN 300 706 allows a
        # transmitter's data from 0.4 us late to 1.0 us (7 bits) early
        # (6.3); a window 7 bits off the run-in would straddle the framing
        # code and miss the line, and the framing search below reaches only
        # 4 bits either way. Each line takes the window its run-in fits best.
        self.windows = []
        for shift in self.SHIFTS:
            a = int(math.ceil(self.c0 + (1.0 + shift) * self.tb))
            b = int(math.floor(self.c0 + (14.0 + shift) * self.tb))
            k = np.arange(a, b + 1)
            self.windows.append((shift, slice(a, b + 1), k, np.exp(-1j * np.pi * k / self.tb).astype(np.complex64)))
        self.stats = {"lines": 0, "found": 0, "framing1": 0}

    # The interpolator's weights for fractions of a sample in steps of
    # 1/STEPS: a table, because computing sinc and cos for every bit of
    # every line was two thirds of the slicer's 3-4 ms a frame. 1/128 of a
    # sample is 0.006 of a bit at 20 MS/s - nothing at the bit centre.
    STEPS = 128

    def _weights(self) -> np.ndarray:
        w = getattr(self, "_wt", None)
        if w is None:
            h = self.taps // 2
            k = np.arange(-h + 1, h + 1)
            d = (np.arange(self.STEPS + 1) / self.STEPS)[:, None] - k
            w = np.sinc(d) * (0.5 + 0.5 * np.cos(np.pi * d / h))
            w = (w / w.sum(axis=1, keepdims=True)).astype(np.float32)
            self._wt = w
        return w

    def _sample(self, x: np.ndarray, pos: np.ndarray) -> np.ndarray:
        """Band-limited interpolation of each row of x at fractional sample
        positions pos (rows x m): a Hann-windowed sinc, `taps` wide."""
        h = self.taps // 2
        i0 = np.floor(pos).astype(np.int64)
        q = np.rint((pos - i0) * self.STEPS).astype(np.int64)
        k = np.arange(-h + 1, h + 1)
        idx = np.clip(i0[..., None] + k, 0, x.shape[1] - 1)
        rows = np.arange(x.shape[0])[:, None, None]
        return np.einsum("rmk,rmk->rm", x[rows, idx], self._weights()[q])

    def slice(self, lines: np.ndarray) -> tuple[list, list[bool]]:
        """lines: (k, N) volts, one per entry of `self.lines`. Returns
        (packets found, 42 bytes each, in line order; for each line whether
        it carried teletext)."""
        x = np.asarray(lines, np.float32)
        k = x.shape[0]
        self.stats["lines"] += k
        has = [False] * k
        best = None
        for shift, sl, k_idx, ref in self.windows:
            seg = x[:, sl]
            mean = seg.mean(axis=1)
            z = (seg - mean[:, None]) @ ref
            amp = 2 * np.abs(z) / seg.shape[1]                    # the run-in tone's amplitude
            # The residual: what is left of the window after the tone.
            tone = amp[:, None] * np.cos(np.pi * k_idx[None, :] / self.tb + np.angle(z)[:, None])
            resid = np.std(seg - mean[:, None] - tone, axis=1) + 1e-9
            st = amp / resid
            if best is None:
                best = [st, amp, mean, z, np.zeros(k)]
            else:
                better = st > best[0]
                for i, v in enumerate((st, amp, mean, z, np.full(k, float(shift)))):
                    best[i] = np.where(better, v, best[i])
        strength, amp, mean, z, shift = best
        cand = np.flatnonzero((strength > self.MIN_RUNIN) & (amp > self.MIN_TONE_V) & (mean > self.MEAN_V[0])
                              & (mean < self.MEAN_V[1]))
        if not len(cand):
            return [], has
        # Phase: the run-in peaks (the ones) where pi (s - c) / tb = 2 pi j,
        # so the first one's centre is c = -angle(z) tb / pi, modulo 2 tb -
        # taken nearest where that window expects it.
        ph = -np.angle(z[cand]) * self.tb / np.pi
        two = 2 * self.tb
        c = ph + np.round((self.c0 + shift[cand] * self.tb - ph) / two) * two
        offs = np.array([0, -1, 1, -2, 2]) * two
        pos = c[:, None, None] + offs[None, :, None] + (16 + np.arange(8))[None, None, :] * self.tb
        sub = x[cand]
        thr = mean[cand]
        fc = self._sample(np.repeat(sub, len(offs), axis=0), pos.reshape(-1, 8)).reshape(len(cand), len(offs), 8)
        fbits = (fc > thr[:, None, None]).astype(np.uint8)
        val = bits_to_bytes(fbits)[..., 0]
        dist = np.array([[bin(int(v) ^ FRAMING).count("1") for v in row] for row in val])
        out = []
        keep, starts = [], []
        for i in range(len(cand)):
            j = int(np.argmin(dist[i]))          # offsets are ordered nearest first: ties go to the nearest
            if dist[i, j] == 0 or (dist[i, j] == 1 and strength[cand[i]] > self.SURE_RUNIN):
                keep.append(i)
                starts.append(c[i] + offs[j])
                if dist[i, j] == 1:
                    self.stats["framing1"] += 1
        if not keep:
            return [], has
        keep = np.array(keep)
        pos = np.array(starts)[:, None] + (24 + np.arange(BITS - 24))[None, :] * self.tb
        v = self._sample(x[cand[keep]], pos)
        bits = (v > thr[keep][:, None]).astype(np.uint8)
        data = bits_to_bytes(bits)
        for r, i in enumerate(keep):
            out.append(bytes(data[r]))
            has[int(cand[i])] = True
        self.stats["found"] += len(out)
        return out, has
