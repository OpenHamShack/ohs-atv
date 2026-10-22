"""Mini-MAC: time-multiplexed analogue components with digital sound, for
an FM channel of ~5 MS/s.

D/D2-MAC (1980s satellite TV) sent each line as a burst of data, then a
time-compressed colour-difference signal, then time-compressed luminance,
one after the other - never added together, so there is no cross-colour,
no subcarrier and no burst to lose in noise, and sound is digital in the
line blanking. hacktv's mac.c was read for the idea and the D2-MAC numbers
(GPL-3.0; nothing copied - this is a scaled-down layout of our own).
D2-MAC needs ~8.4 MHz of baseband at 20.25 MS/s; this layout is built for
the ~1.4 MHz a USB Pluto's 5 MS/s FM channel leaves flat.

**The line (64 us, times from its start).** Levels in MAC units: -0.5..+0.5,
0 is the clamp level; luminance black -0.5, white +0.5; U and V scaled so a
saturated primary reaches +-0.5. On air a MAC unit is a volt shifted by 0.2,
so FmMod's -0.3..0.7 V range is the full deviation.

| us | what |
| --- | --- |
| 0.5 - 11.5 | 21 bits at 2 Mbit/s, +-0.5, raised-cosine pulses two bits wide (a run is flat, centres are free of their neighbours): **line sync** Barker-7 (+++--+-), a 13-bit SECDED **sound sample**, a **U/V flag** (1 on odd lines: U) |
| 11.5 - 12.5 | **clamp** at 0: the receiver's DC reference (in FM the DC is the carrier offset) |
| 12.75 - 25.25 | **chroma**, 52 us of U (odd lines) or V (even lines) compressed 4.16:1 |
| 25.75 - 63.5 | **luminance**, 52 us compressed 1.377:1 |

Segments fade to 0 over 0.25 us outside their edges, so the joins carry no
steps. The compression ratios trade luma against chroma as D2-MAC's 3:2 and
3:1 do, but with more of the line to luminance - 1.0 MHz of luma and 0.34 MHz
of chroma (on alternate lines, as in MAC and SECAM) at 5 MS/s, 1.45 and 0.48
MHz at 6.25. The data rate, 2 Mbit/s, is what ~1.4 MHz of flat baseband
carries with pulses this smooth.

**Frame sync.** Line 1's analogue area carries a 63-bit m-sequence instead
of picture, and line 313's the same sequence inverted: correlating each
line against it finds them even at a bit error rate that would ruin the
sound, and after a break in the signal the numbering is back within a
field, as PAL's field sync gives it.

**Sound.** One sample a line - 15 625 Hz, ~7 kHz of audio - in 8-bit mu-law
with SECDED (ohsatv/pcm.py).

The picture lines are PAL's (23-310, 336-623 interleaved into 576 rows), so
every source and the window treat a MAC frame as they do a PAL one.
"""
from __future__ import annotations

import math

import numpy as np

from . import pal, pcm
from .decoder import break_at

BIT_US = 0.5
FIRST_BIT_US = 1.0
NBITS = 21
BARKER = np.array([1, 1, 1, -1, -1, 1, -1], np.float32)
SND0 = 7                       # sound codeword bits 7..19
UV_BIT = 20
CLAMP_US = (11.6, 12.4)
CHROMA_US = (12.75, 25.25)
LUMA_US = (25.75, 63.5)
FADE_US = 0.25
PN_FIRST_US = 13.0
LEVEL = 0.5                    # data and full-scale amplitude, MAC units
VOLT_OFFSET = 0.2              # MAC unit 0 is 0.2 V on air: -0.5..0.5 -> -0.3..0.7 V
U_SCALE = 0.5 / 0.886          # B'-Y' range +-0.886 -> +-0.5
V_SCALE = 0.5 / 0.701          # R'-Y' range +-0.701 -> +-0.5
LUMA_RATIO = 52.0 / (LUMA_US[1] - LUMA_US[0])
CHROMA_RATIO = 52.0 / (CHROMA_US[1] - CHROMA_US[0])


def _mseq63() -> np.ndarray:
    """The 63-bit m-sequence of x^6 + x^5 + 1, as +-1."""
    reg = [1] * 6
    out = []
    for _ in range(63):
        out.append(reg[-1])
        fb = reg[5] ^ reg[4]
        reg = [fb] + reg[:-1]
    return np.where(np.array(out) > 0, 1.0, -1.0).astype(np.float32)


PN = _mseq63()


def _scrambler() -> np.ndarray:
    """625 x 14 bits (0/1) XORed onto each line's sound and U/V bits:
    x^15 + x^14 + 1 run from all ones at line 1, 14 bits a line.

    Energy dispersal, as MAC's own data was scrambled: a steady tone or
    silence would otherwise send the same bits line after line, and a
    pattern that repeats down the frame survives the acquisition fold and
    can pass for (or hide) the line sync. And it numbers the lines: only the
    right line number descrambles a run of lines into valid SECDED words
    with the right U/V flags, so after a break the receiver knows where it
    is within ~30 lines instead of waiting up to a field for the m-sequence
    line (Decoder._number_by_data). The period (32 767 bits) is longer than
    a frame's 8 750, so no two line numbers share a stretch of it; a 9-bit
    register's 511 would repeat every 73 lines."""
    reg = [1] * 15
    out = np.empty(pal.LINES * 14, np.uint8)
    for i in range(len(out)):
        out[i] = reg[-1]
        fb = reg[14] ^ reg[13]
        reg = [fb] + reg[:-1]
    return out.reshape(pal.LINES, 14)


SCRAMBLE = _scrambler()


def hann_pulses(fs: float, centres_us: np.ndarray, width_us: float) -> tuple[np.ndarray, int, int]:
    """(len(centres) x span) raised-cosine pulses `width_us` wide at full
    width, over the samples [a, b) of a line that they touch."""
    a = max(0, int(np.floor((centres_us.min() - width_us / 2) * 1e-6 * fs)))
    b = int(np.ceil((centres_us.max() + width_us / 2) * 1e-6 * fs)) + 1
    t = np.arange(a, b) / fs * 1e6
    x = (t[None, :] - centres_us[:, None]) / (width_us / 2)
    p = np.where(np.abs(x) < 1.0, 0.5 + 0.5 * np.cos(np.pi * x), 0.0)
    return p.astype(np.float32), a, b


def bl_resample(x: np.ndarray, m: int, cutoff: float, edge: float = 0.15,
                shift: np.ndarray | None = None) -> np.ndarray:
    """Band-limited resampling of each row of `x` (rows x w) to `m`
    samples spanning the same interval, keeping frequencies below `cutoff`
    (a fraction of the *output* row's Nyquist when compressing, or of the
    input's when expanding - whichever is lower is what survives). The row is
    mirrored first so the FFT sees no step from its last sample to its
    first. `shift` (per row, input samples) moves each row's content that
    far earlier - the fraction of a sample the decoder's whole-sample line
    extraction left over."""
    x = np.asarray(x, np.float32)
    w, m = x.shape[1], int(m)
    ext = np.concatenate([x, x[:, ::-1]], axis=1)
    # Scaled (norm="forward", undone in the last line): numpy 2.2's unscaled
    # transforms are on a slow path - 2.3 ms against 0.5 for 576 rows of a
    # 520-point row (docs/PERF.md).
    X = np.fft.rfft(ext, axis=1, norm="forward")    # complex64 from float32 (numpy 2)
    nyq = min(w, m)                        # bins: cycles per 2w-long period
    kc = cutoff * nyq
    nb = m + 1
    # Only the bins the low-pass passes and the output can hold are
    # multiplied, and in single precision throughout: the decoder expands
    # three components of 576 rows a frame, and complex128 phase ramps and
    # gains doubled the cost of everything after the forward FFT (DESIGN.md,
    # "Mini-MAC's sound").
    K = min(X.shape[1], nb, int(math.ceil(kc + edge * nyq / 2)) + 1)
    k = np.arange(K, dtype=np.float32)
    g = np.clip((np.float32(kc + edge * nyq / 2) - k) / np.float32(edge * nyq + 1e-9), 0.0, 1.0)
    g = (0.5 - 0.5 * np.cos(np.float32(np.pi) * g)).astype(np.float32)
    # Half-sample alignment: input sample i sits at (i + 0.5) / w of the
    # interval, output j at (j + 0.5) / m. irfft to 2m puts j at j * w / m
    # input samples, so shift by delta = 0.5 w / m - 0.5.
    delta = 0.5 * w / m - 0.5
    if shift is None:
        ang = k * np.float32(np.pi * delta / w)
    else:
        d = (delta + np.asarray(shift, np.float64)).astype(np.float32)
        ang = d[:, None] * (k * np.float32(np.pi / w))[None, :]
    Y = np.zeros((X.shape[0], nb), np.complex64)
    Y[:, :K] = X[:, :K] * (g * (np.cos(ang) + np.complex64(1j) * np.sin(ang))).astype(np.complex64)
    y = np.fft.irfft(Y, 2 * m, axis=1)
    # x 2w undoes the forward transform's scaling; x m / w is the change of
    # interval length: 2m in all.
    return (y[:, :m] * np.float32(2 * m)).astype(np.float32, copy=False)


def _seg(fs: float, span_us: tuple[float, float]) -> tuple[int, int]:
    a = int(math.ceil(span_us[0] * 1e-6 * fs))
    b = int(math.floor(span_us[1] * 1e-6 * fs))
    return a, b - a


def picture_lines() -> tuple[np.ndarray, np.ndarray]:
    return pal._row_map()


class Layout:
    """The line layout at one sample rate: pulse tables and segment
    positions, shared by the encoder and the decoder."""

    def __init__(self, fs: float):
        self.fs = float(fs)
        self.n = pal.samples_per_line(fs)
        self.centres = FIRST_BIT_US + BIT_US * np.arange(NBITS)
        self.pulses, self.da, self.db = hann_pulses(fs, self.centres, 2 * BIT_US)
        pnc = PN_FIRST_US + BIT_US * np.arange(len(PN))
        self.pn_pulses, self.pa, self.pb = hann_pulses(fs, pnc, 2 * BIT_US)
        self.pn_wave = (LEVEL * PN) @ self.pn_pulses
        self.pn_pos = pnc * 1e-6 * fs
        self.c0, self.cn = _seg(fs, CHROMA_US)
        self.l0, self.ln = _seg(fs, LUMA_US)
        self.k0, self.kn = _seg(fs, CLAMP_US)
        self.first, self.count = pal.active_samples(fs)
        # Least-squares bit estimator: amplitudes = lines[:, da:db] @ est.T
        P = self.pulses.astype(np.float64)
        self.est = (np.linalg.solve(P @ P.T, P)).astype(np.float32)
        # The Barker as a waveform, zero-mean, for timing.
        bw, ba, bb = hann_pulses(fs, self.centres[:7], 2 * BIT_US)
        tmpl = (LEVEL * BARKER) @ bw
        self.tmpl = (tmpl - tmpl.mean()).astype(np.float32)
        self.ta = ba

    def fades(self, a: int, m: int) -> tuple[np.ndarray, np.ndarray]:
        """Ramp-in / ramp-out sample indices and weights for a segment."""
        f = max(1, int(round(FADE_US * 1e-6 * self.fs)))
        w = (0.5 - 0.5 * np.cos(np.pi * (np.arange(1, f + 1) / (f + 1)))).astype(np.float32)
        return np.arange(a - f, a), w


class Encoder:
    """RGB + a frame of 48 kHz audio -> a Mini-MAC frame (625 x N, volts)."""

    audio_in_video = True

    def __init__(self, fs: float, colour: bool = True, chan_bw: float | None = None):
        """`chan_bw`: the flat baseband the FM channel carries (Hz); the
        components are band-limited to it divided by their compression."""
        self.fs = float(fs)
        self.L = Layout(fs)
        self.colour = colour
        self.chan_bw = chan_bw or (0.28 * fs)
        self.lines, self.rows = picture_lines()
        self.pack = pcm.FramePacker(pal.LINES)
        self.parity = ((np.arange(pal.LINES) + 1) % 2).astype(np.float32)      # line 1 odd -> 1: U
        self.fixed = self._fixed()

    def _fixed(self) -> np.ndarray:
        L = self.L
        fr = np.zeros((pal.LINES, L.n), np.float32)
        fr[0, L.pa:L.pb] += L.pn_wave
        fr[312, L.pa:L.pb] -= L.pn_wave
        return fr

    def _component(self, rows_x: np.ndarray, m: int, ratio: float) -> np.ndarray:
        # Content bandwidth after compression must fit the channel: before
        # compression it is chan_bw / ratio. As a fraction of the output
        # segment's Nyquist (fs / 2): chan_bw / (fs / 2).
        cutoff = min(0.95, self.chan_bw / (self.fs / 2))
        return bl_resample(rows_x, m, cutoff)

    def _place(self, fr: np.ndarray, lines: np.ndarray, seg: np.ndarray, a: int):
        m = seg.shape[1]
        fr[lines, a:a + m] = seg
        idx, w = self.L.fades(a, m)
        fr[lines[:, None], idx[None, :]] = seg[:, :1] * w[None, :]
        f = len(w)
        fr[lines[:, None], (a + m + np.arange(f))[None, :]] = seg[:, -1:] * w[::-1][None, :]

    def bits(self, audio) -> np.ndarray:
        x = self.pack(audio)
        cw = pcm.secded_encode(pcm.mulaw_encode(x)).astype(np.float32)          # 625 x 13
        b = np.empty((pal.LINES, NBITS), np.float32)
        b[:, :7] = BARKER
        d = np.concatenate([cw, self.parity[:, None]], axis=1).astype(np.uint8) ^ SCRAMBLE
        b[:, SND0:] = 2.0 * d - 1
        return b

    def encode(self, rgb: np.ndarray, frame_no: int = 0, audio: np.ndarray | None = None) -> np.ndarray:
        L = self.L
        rgb = np.asarray(rgb)
        if rgb.dtype == np.uint8:
            rgb = rgb.astype(np.float32) / 255.0
        rgb = np.clip(rgb.astype(np.float32), 0.0, 1.0)
        if rgb.shape[0] != pal.ACTIVE_ROWS:
            ri = np.clip(((np.arange(pal.ACTIVE_ROWS) + 0.5) * rgb.shape[0] / pal.ACTIVE_ROWS).astype(int), 0,
                         rgb.shape[0] - 1)
            rgb = rgb[ri]
        r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
        y = pal.KR * r + pal.KG * g + pal.KB * b
        fr = self.fixed.copy()
        lines, rows = self.lines, self.rows
        ys = self._component(y[rows] - 0.5, L.ln, LUMA_RATIO)
        self._place(fr, lines, ys, L.l0)
        if self.colour:
            # Odd frame lines carry U, even ones V (line numbers 1-based).
            odd = (lines + 1) % 2 == 1
            c = np.where(odd[:, None], U_SCALE * (b[rows] - y[rows]), V_SCALE * (r[rows] - y[rows]))
            cs = self._component(c, L.cn, CHROMA_RATIO)
            self._place(fr, lines, cs, L.c0)
        fr[:, L.da:L.db] += (LEVEL * self.bits(audio)) @ L.pulses
        return (fr + np.float32(VOLT_OFFSET)).astype(np.float32)


class Decoder:
    """Raw FM-demodulated baseband -> pictures and sound.

    Acquisition folds 64 lines and finds the Barker word in the fold (the
    data around it averages away; a check on its correlation sidelobes keeps
    a picture's own texture from passing for it). Tracking correlates every
    line against the Barker waveform, fits a straight line through a batch of
    those times (the transmitter's line rate is crystal-locked, as in
    ohsatv.decoder) and resamples each line from its fitted start. Gain comes
    from the Barker's known amplitude, DC from the clamp. Line 1 is found by
    its m-sequence; the U/V flag says which colour difference a line holds.
    """

    def __init__(self, fs: float, colour: str = "auto"):
        self.fs = float(fs)
        self.L = Layout(fs)
        self.n = self.L.n
        self.us = self.fs * 1e-6
        self.colour_mode = colour
        self.search = int(round(1.5 * self.us))
        self.margin = self.search + len(self.L.tmpl) + 4
        self.buf = np.zeros(0, np.float32)
        self.buf0 = 0
        self.first, self.count = self.L.first, self.L.count
        self.frame = np.zeros((pal.LINES, self.n), np.float32)
        self.bitsf = np.zeros((pal.LINES, NBITS), np.float32)
        self.frac = np.zeros(pal.LINES, np.float32)
        self.filled = np.zeros(pal.LINES, bool)
        self.lines, self.rows = picture_lines()
        self.unpack = pcm.FrameUnpacker(pal.LINES)
        self.frames_out = 0
        self.last_uv = None
        # False: finish frames without their picture (rgb None) - the sound,
        # the stats and the line count still come out. The engine sets it
        # while the decoder is behind (Radio._decode_loop), see `_finish`.
        self.picture = True
        self.sound = {"good": 0.0, "corrected": 0.0}
        self.stats = {"hLock": 0.0, "vLock": False, "colour": False, "snr": None, "jitterNs": None,
                      "lines": 0, "level": None, "state": "searching"}
        self.reset()

    def reset(self):
        self.locked = False
        self.line_next = None
        self.period = float(self.n)
        self.line_no = None
        self.gain = None
        self.dc = None
        self.misses = 0
        self.poor = 0
        self.vmiss = 0
        self.acq_from = None
        self.acq_since = None
        self.fresh = False

    # -------------------------------------------------------------- input
    def feed(self, raw: np.ndarray) -> list[dict]:
        raw = np.asarray(raw, np.float32)
        self.buf = np.concatenate([self.buf, raw]) if len(self.buf) else raw.copy()
        out: list = []
        while True:
            if not self.locked:
                got = self._acquire()
                if got is None:          # failed inside a break, moved on: try again
                    continue
                if not got:
                    break
            if not self._track(out):
                break
        if self.line_next is not None:
            keep_from = int(self.line_next) - self.margin - self.buf0
        elif self.acq_from is not None:
            keep_from = int(self.acq_from) - self.buf0
        else:
            keep_from = len(self.buf) - 70 * self.n
        keep_from = max(0, min(keep_from, len(self.buf)))
        if keep_from:
            self.buf = self.buf[keep_from:]
            self.buf0 += keep_from
        return out

    def _corr(self, x: np.ndarray) -> np.ndarray:
        """Circular correlation of a line-long profile with the Barker."""
        t = self.L.tmpl
        ext = np.concatenate([x, x[:len(t)]])
        return np.lib.stride_tricks.sliding_window_view(ext, len(t))[:self.n] @ t

    def _acquire(self) -> bool | None:
        k = 64
        need = (k + 2) * self.n
        s0 = len(self.buf) - need if self.acq_from is None else int(self.acq_from) - self.buf0
        if s0 < 0 or s0 + need > len(self.buf):
            return False
        x = self.buf[s0:s0 + need]
        prof = x[:k * self.n].reshape(k, self.n).mean(axis=0)
        prof = prof - prof.mean()
        c = self._corr(prof)
        ph = int(np.argmax(c))
        peak = float(c[ph])
        e = float(self.L.tmpl @ self.L.tmpl)
        g = peak / e                                  # the fold's gain, Hz per MAC unit
        step = int(round(2 * BIT_US * self.us))
        # A texture that matches the Barker by chance (a grating) matches
        # it again a bit or two along; the Barker itself does not (its
        # sidelobes are 1/7).
        side = max(c[(ph + s) % self.n] for s in (-3 * step, -2 * step, -step, step, 2 * step, 3 * step))
        noise = float(np.median(np.abs(c)))
        # The fold's level in the clamp should sit between the Barker's
        # +-0.5 (at 0) - a picture's texture that happens to match the
        # Barker has no reason to.
        base = (ph - self.L.ta) % self.n
        clamp = prof[(base + self.L.k0 + np.arange(self.L.kn)) % self.n].mean()
        ones = prof[(base + np.round(self.L.centres[:7] * self.us).astype(int)) % self.n]
        mid = 0.5 * (ones[BARKER > 0].mean() + ones[BARKER < 0].mean())
        if not (peak > 0 and peak > 6 * noise and side < 0.6 * peak and abs(clamp - mid) < 0.25 * g):
            self.stats.update(state="searching", hLock=0.0, vLock=False, jitterNs=None, snr=None)
            if self.acq_from is not None:
                # Still in the break: try again 16 lines on, at once (feed()
                # loops) - one try per call fell further behind the signal
                # every frame and never caught up. Once the break has gone on
                # for more than a few folds, search the newest samples as at
                # the start. "Gone on" is measured from where the break
                # began (acq_since), not as what is left in the buffer: that
                # was the test until 5 October, and with a whole 40 ms block
                # queued behind the break it held at the first failed fold,
                # so a 2 ms transmit gap jumped the search to the newest
                # samples and cost a whole picture (40 ms; 5-6 before
                # fbd177f, tools/narrow_eval.py).
                self.acq_from += 16 * self.n
                if self.acq_since is None:
                    self.acq_since = self.acq_from - 16 * self.n
                if self.acq_from - self.acq_since > 4 * need:
                    self.acq_from = self.acq_since = None
                return None
            drop = len(self.buf) - need
            self.buf = self.buf[drop:]
            self.buf0 += drop
            return False
        start = s0 + base
        self.acq_from = None
        self.fresh = True
        if start < self.margin:
            start += self.n
        self.line_next = float(self.buf0 + start)
        self.period = float(self.n)
        self.gain = g
        self.locked = True
        self.line_no = None
        self.misses = 0
        return True

    def _track(self, out: list) -> bool:
        n, L = self.n, self.L
        avail = self.buf0 + len(self.buf) - self.margin - n
        count = int((avail - self.line_next) // self.period)
        if count < 8:
            return False
        count = min(count, 320)
        k = np.arange(count)
        pred = self.line_next + k * self.period
        base = np.round(pred).astype(np.int64) - self.buf0
        S, t = self.search, L.tmpl
        offs = np.arange(-S, S + len(t))
        win = self.buf[(base + L.ta)[:, None] + offs[None, :]]
        c = np.lib.stride_tricks.sliding_window_view(win, len(t), axis=1) @ t          # count x (2S+1)
        j = np.argmax(c, axis=1)
        cmax = c[k, j]
        jj = np.clip(j, 1, 2 * S - 1)
        a, b, cc = c[k, jj - 1], c[k, jj], c[k, jj + 1]
        den = a - 2 * b + cc
        frac = np.clip(np.where(np.abs(den) > 1e-12, 0.5 * (a - cc) / np.where(den == 0, 1, den), 0.0), -0.5, 0.5)
        r = (jj - S) + frac + (base - (pred - self.buf0))
        e = float(t @ t)
        # Found: the Barker at about its own strength. The upper bound is for
        # breaks in the signal, where the FM discriminator's noise is several
        # times full scale and correlates strongly with anything. Straight
        # after acquisition the gain is the fold's, which is low if the fold
        # straddled the start of a transmission: then only the lower bound,
        # and the gain is taken from this batch.
        if self.fresh:
            ok = cmax > 0.5 * self.gain * e
            if ok.sum() >= 6:
                self.gain = float(np.median(cmax[ok])) / e
                ok &= cmax < 1.8 * self.gain * e
        else:
            ok = (cmax > 0.5 * self.gain * e) & (cmax < 1.8 * self.gain * e)
        if ok.sum() >= max(6, count // 5):
            med = np.median(r[ok])
            ok &= np.abs(r - med) < 0.4 * self.us
        cut = break_at(ok)          # a gap or a jump: see ohsatv.decoder
        resync = cut is not None
        if cut == 0:
            self._lost_at(self.line_next + (16 * self.n if self.fresh else 0))     # see ohsatv.decoder
            return True
        self.fresh = False
        if resync:
            count = cut
            k, pred, r, ok, cmax = k[:cut], pred[:cut], r[:cut], ok[:cut], cmax[:cut]
        if ok.sum() >= max(6, count // 5):
            A = np.vstack([np.ones(ok.sum()), k[ok]]).T
            coef, *_ = np.linalg.lstsq(A, r[ok], rcond=None)
            fit = coef[0] + coef[1] * k
            resid = r[ok] - (coef[0] + coef[1] * k[ok])
            self.stats["jitterNs"] = float(np.std(resid) / self.fs * 1e9)
            starts = pred + fit
            self.misses = 0
            self.acq_since = None
            slope = coef[1]
            self.gain = 0.7 * self.gain + 0.3 * float(np.median(cmax[ok])) / e
        else:
            starts = pred
            fit = np.zeros(count)
            self.misses += 1
            slope = 0.0
            self.stats["jitterNs"] = None
        hfrac = float(ok.mean())
        self.stats["hLock"] = 0.8 * self.stats["hLock"] + 0.2 * hfrac if self.stats["lines"] else hfrac
        self.stats["lines"] += count
        self.poor = self.poor + 1 if hfrac < 0.5 else 0
        if self.poor >= 2:
            self.misses = 7
            self.poor = 0
        if self.misses > 6:
            self.locked = False
            self.stats.update(state="searching", vLock=False, jitterNs=None, snr=None)
            self.line_next = None
            self.line_no = None
            self.misses = 0
            return True

        # Whole-sample extraction, the fraction kept and taken out exactly in
        # the band-limited expansion (bl_resample's shift): linear
        # interpolation would low-pass the compressed components by an
        # amount that depends on the fraction (see ohsatv.decoder).
        si = np.round(starts)
        frac = (starts - si).astype(np.float32)
        # Rows out of a strided view (a copy per line), not a 2-D fancy index.
        lines = np.lib.stride_tricks.sliding_window_view(self.buf, n)[(si - self.buf0).astype(np.int64)]
        clamp = lines[:, L.k0:L.k0 + L.kn].mean(axis=1)
        dc = float(np.median(clamp[ok])) if ok.any() else float(np.median(clamp))
        self.dc = dc
        norm = (lines - np.float32(dc)) * np.float32(1.0 / max(self.gain, 1e-9))
        self.stats["level"] = float(self.gain)
        bits = norm[:, L.da:L.db] @ L.est.T                                        # count x 21

        # Line 1: its m-sequence. Sampled at the bit centres and correlated.
        pp = L.pn_pos
        p0 = np.floor(pp).astype(int)
        pf = (pp - p0).astype(np.float32)
        pnv = norm[:, p0] * (1 - pf) + norm[:, p0 + 1] * pf
        score = (pnv @ PN) / (LEVEL * len(PN))
        anchor = None
        # Only on lines whose Barker was found: inside a break the FM
        # discriminator's noise is several times full scale, and its
        # correlation with anything crosses 0.5 often.
        # And only where the line's own Barker decodes as one: its seven bits
        # within 0.3 rms of +-0.5. A noise line can pass the timing tests by
        # chance; it does not pass this, and one false anchor numbers a
        # field wrong for two field syncs.
        sound = ok & (np.sqrt(np.mean((bits[:, :7] - LEVEL * BARKER) ** 2, axis=1)) < 0.3)
        hits = np.flatnonzero((np.abs(score) > 0.6) & sound)
        if len(hits):
            i = int(hits[0])
            anchor = (i, 1 if score[i] > 0 else 313)
        numbers = None
        if self.line_no is not None:
            numbers = (self.line_no - 1 + k) % pal.LINES + 1
        else:
            first = self._number_by_data(bits, sound)
            if first is not None:
                numbers = (first - 1 + k) % pal.LINES + 1
        if anchor is not None:
            i, ln = anchor
            if numbers is None or numbers[i] != ln:
                self.vmiss += 1
                if numbers is None or self.vmiss >= 2:
                    numbers = (ln - 1 + (k - i)) % pal.LINES + 1
                    numbers[:i] = 0
                    self.vmiss = 0
            else:
                self.vmiss = 0
        if numbers is not None:
            self.stats["vLock"] = True
            self.stats["state"] = "locked"
            # Stored by line number a run at a time (as ohsatv.decoder): a
            # run ends with line 625, where the frame is finished.
            cuts = list(np.flatnonzero(numbers == pal.LINES) + 1)
            s0 = 0
            for s1 in cuts + ([count] if not cuts or cuts[-1] != count else []):
                idx = s0 + np.flatnonzero(numbers[s0:s1])
                li = numbers[idx] - 1
                self.frame[li] = norm[idx]
                self.frac[li] = frac[idx]
                self.bitsf[li] = bits[idx]
                self.filled[li] = True
                if s1 in cuts:
                    out.append(self._finish())
                s0 = s1
            last = int(numbers[-1]) if numbers[-1] else None
            self.line_no = (last % pal.LINES + 1) if last else None
        else:
            self.stats["state"] = "h-lock"
            self.stats["vLock"] = False
        self.line_next = float(pred[-1] + fit[-1]) + self.period
        self.period = float(np.clip(self.period + 0.5 * slope, n * 0.999, n * 1.001))
        if resync:
            self._lost_at(self.line_next)
        return True

    def _lost_at(self, pos: float) -> None:
        self.acq_from = pos
        self.line_next = None
        self.line_no = None
        self.locked = False

    def _number_by_data(self, bits: np.ndarray, ok: np.ndarray, use: int = 32) -> int | None:
        """The frame line number of the batch's first line, from the
        scrambled data alone (see _scrambler), or None. Every one of the 625
        candidates is tried on up to `use` lines: the right one turns nearly
        every line into a valid codeword with the right U/V flag; a wrong
        one about one line in five."""
        idx = np.flatnonzero(ok)[:use]
        if len(idx) < 24:
            return None
        hard = (bits[idx, SND0:] > 0).astype(np.uint8)                       # m x 14
        cand = np.arange(pal.LINES)
        nums = (cand[:, None] + idx[None, :]) % pal.LINES                    # 625 x m, 0-based lines
        d = hard[None, :, :] ^ SCRAMBLE[nums]
        _, good, _ = pcm.secded_decode(d[..., :pcm.CODE_BITS].reshape(-1, pcm.CODE_BITS))
        par = d[..., -1] == ((nums + 1) % 2)
        score = (good.reshape(nums.shape) & par).mean(axis=1)
        o = np.argsort(score)
        if score[o[-1]] < 0.9 or score[o[-2]] > 0.7:
            return None
        return int(o[-1]) + 1

    # ------------------------------------------------------------- frames
    def _finish(self) -> dict:
        L = self.L
        fr = self.frame
        # Noise: the luminance segment of lines 2-22 carries only the clamp
        # level, so what is there is noise.
        q = fr[1:22, L.l0 + 2:L.l0 + L.ln - 2]
        sigma = float(np.std(q))
        self.stats["snr"] = 20 * math.log10(1.0 / max(sigma, 1e-6))
        bits = self.bitsf
        hard = (bits[:, SND0:] > 0).astype(np.uint8) ^ SCRAMBLE
        codes, good, fixed = pcm.secded_decode(hard[:, :pcm.CODE_BITS])
        good &= self.filled
        self.sound = {"good": float(good.mean()), "corrected": float(fixed.mean())}
        audio = self.unpack(pcm.mulaw_decode(codes), good)
        self.frames_out += 1
        self.filled[:] = False
        if not self.picture:
            # The sound is in every line and costs ~7 ms a frame at 16 MS/s;
            # the picture (three band-limited expansions of 576 rows) ~37.
            # A receiver that falls behind used to drop whole blocks of
            # signal before the decoder, and every one took 40 ms of sound
            # with it: on air at 16 MS/s the window decoded 12-16 frames a
            # second and the 1 kHz tone measured ~17 dB SINAD, where the same
            # recording decoded offline gives 37-39. Skipping the picture
            # instead keeps the sound whole.
            return {"rgb": None, "stats": dict(self.stats), "composite": None, "audio": audio,
                    "sound": dict(self.sound)}
        # U/V flags by majority over the frame: odd lines carry U.
        flag = 2.0 * hard[:, -1] - 1
        par = np.where((np.arange(pal.LINES) + 1) % 2 == 1, 1.0, -1.0)
        swap = float(np.mean(flag * par)) < 0
        rgb, colour = self._picture(fr, swap)
        self.stats["colour"] = colour
        comp = fr + np.float32(VOLT_OFFSET)
        return {"rgb": rgb, "stats": dict(self.stats), "composite": comp, "audio": audio,
                "sound": dict(self.sound)}

    def _picture(self, fr: np.ndarray, swap: bool) -> tuple[np.ndarray, bool]:
        L = self.L
        lines, rows = self.lines, self.rows
        order = lines[np.argsort(rows)]
        sh = self.frac[order]
        y = bl_resample(fr[order, L.l0:L.l0 + L.ln], self.count, 0.95, shift=sh) + np.float32(0.5)
        out = np.empty((pal.ACTIVE_ROWS, self.count, 3), np.uint8)
        if self.colour_mode == "mono":
            self.last_uv = None
            out[...] = np.clip(y * 255 + 0.5, 0, 255).astype(np.uint8)[..., None]
            return out, False
        # Each line has one colour difference; the other is the mean of the
        # lines above and below it in the same field (frame lines l +- 1).
        # Every line is expanded once, at its own timing, and the mean taken
        # after: half the work of expanding U and V for every row (the
        # neighbours' mean was formed first and expanded at the row's
        # timing, which also put the neighbours a fraction of a sample out).
        c = bl_resample(fr[:, L.c0:L.c0 + L.cn], self.count, 0.95, shift=self.frac)
        nb = 0.5 * (np.roll(c, 1, axis=0) + np.roll(c, -1, axis=0))
        odd = ((np.arange(pal.LINES) + 1) % 2 == 1) ^ swap
        u = np.where(odd[:, None], c, nb)[order] / np.float32(U_SCALE)        # B'-Y'
        v = np.where(odd[:, None], nb, c)[order] / np.float32(V_SCALE)        # R'-Y'
        self.last_uv = ((np.float32(pal.WHITE * pal.U_W) * u[::6, ::3]).ravel(),
                        (np.float32(pal.WHITE * pal.V_W) * v[::6, ::3]).ravel())
        # R'G'B' straight to 8 bits in float32, in place (as ohsatv.decoder).
        y *= np.float32(255)
        b = y + u * np.float32(255)
        r = y + v * np.float32(255)
        g = (y - np.float32(pal.KR) * r - np.float32(pal.KB) * b) * np.float32(1 / pal.KG)
        for i, ch in enumerate((r, g, b)):
            np.clip(ch + np.float32(0.5), 0, 255, out=ch)
            out[..., i] = ch
        return out, True
