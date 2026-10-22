"""Modulators and demodulators: composite video (volts, pal.py) <-> IQ.

Two transmission systems, both at complex baseband at the radio's sample
rate fs:

**AM-VSB** (70cm), UK System I style, negative modulation: carrier
amplitude 100 % at sync tip, 76 % at blanking, 20 % at peak white. The
vestigial lower sideband is cut at `vsb_lower` (1.25 MHz) after the
envelope is formed, the sound is a separate FM carrier `sound_offset` (6.0
MHz) above vision, `sound_db` (-10 dB) below peak vision power.

The vision carrier does not sit at DC. The signal spans roughly
-1.25 .. +6.1 MHz about it, so the carrier is placed `carrier_offset` below
the middle of that span (-2.4 MHz) and everything fits the complex band
symmetrically - unless that leaves an AD936x's image of a carrier within
40 dB of it, when the carriers move (am_plan, _place: at 10 MS/s vision
and sound go to -3.0 and +3.0 MHz). The device's LO is then *above* the vision carrier, inside
the picture's upper sideband; the AD9361's own LO-leakage and DC calibration
keep that spur well down, and a picture hides a small one far better than
the carrier would.

Order matters: the envelope is real, so its spectrum is mirrored about the
carrier and fills -fs/2..fs/2. It is VSB-filtered *at* carrier baseband and
only then shifted - shifting first would wrap its lower sideband round the
band edge and on to the picture.

**FM** (23cm, 6cm): the composite (pre-emphasised) deviates the carrier,
`deviation` Hz peak-to-peak from sync tip to peak white; a sound
subcarrier (6.0 or 6.5 MHz, itself FM) is added to the baseband before
the carrier is modulated, as amateur FM-ATV transmitters do.

Demodulators return a *raw composite*: correct shape, sync pulses
negative-going, arbitrary gain and offset. The decoder clamps it on the
sync tips and back porch, so neither demodulator has to know the received
level or the carrier offset precisely.
"""
from __future__ import annotations

import math
from functools import lru_cache

import numpy as np

from . import nicam, pal
from .devices import ad936x
from .dsp.filters import Channel, Ols, OlsReal, design, taper

# AM-VSB levels (fraction of peak carrier amplitude), System I.
AM_SYNC, AM_BLANK, AM_WHITE = 1.0, 0.76, 0.20
AM_SLOPE = (AM_SYNC - AM_BLANK) / (pal.BLANK - pal.SYNC_TIP)    # per volt: 0.8

SEQ_S = pal.SEQ_FRAMES / 25.0          # 0.16 s: every loop and phasor table repeats on this


def snap(freq: float) -> float:
    """Round a frequency to 6.25 Hz, the resolution at which a phasor is
    periodic over the 0.16 s PAL sequence (6.25 x 0.16 = 1 cycle), so a looped
    picture has no phase step at its seam."""
    return round(freq / 6.25) * 6.25


def tx_span(plan: dict) -> float:
    """How far from the LO an AM plan's signal reaches on either side (Hz):
    what a transmitter's own filter must pass flat. `occupied` is from the
    vision carrier, `carrier` from the LO."""
    c = float(plan.get("carrier", 0.0))
    lo, hi = plan["occupied"]
    return max(abs(c + lo), abs(c + hi))


_TABLES: dict = {}


def _table(fs: float, freq: float) -> np.ndarray:
    """exp(j 2 pi freq k / fs) over one 0.16 s period (freq is snapped to
    6.25 Hz, so the table is exactly periodic). Cached: computing a complex
    exponential costs ~25 ns a sample, slicing a table ~1 ns, and at
    10-20 MS/s that is the difference between a modulator that keeps up and
    one that does not. 13-26 MB per table."""
    key = (float(fs), float(freq))
    t = _TABLES.get(key)
    if t is None:
        period = int(round(SEQ_S * fs))
        k = np.arange(period, dtype=np.float64)
        cyc = (k * (freq / fs)) % 1.0
        ang = (2 * np.pi * cyc).astype(np.float32)
        t = np.empty(period, np.complex64)
        t.real = np.cos(ang)
        t.imag = np.sin(ang)
        if len(_TABLES) > 6:
            _TABLES.clear()
        _TABLES[key] = t
    return t


def _phasor(fs: float, freq: float, n: int, start: int = 0) -> np.ndarray:
    """exp(j 2 pi freq k / fs) for k = start..start+n-1 (freq snapped to
    6.25 Hz), from the periodic table."""
    t = _table(fs, snap(freq))
    period = len(t)
    s = start % period
    if s + n <= period:
        return t[s:s + n]
    reps = -(-(s + n) // period)
    return np.tile(t, reps)[s:s + n]


_ANGLES: dict = {}


def _angle_table(fs: float, freq: float, n: int, start: int = 0) -> np.ndarray:
    """2 pi freq k / fs reduced to 0..2 pi, float32, for k = start..start+n-1,
    from a periodic table (freq snapped to 6.25 Hz), as _phasor."""
    key = (float(fs), snap(freq))
    t = _ANGLES.get(key)
    if t is None:
        period = int(round(SEQ_S * fs))
        k = np.arange(period, dtype=np.float64)
        t = (2 * np.pi * ((k * (snap(freq) / fs)) % 1.0)).astype(np.float32)
        if len(_ANGLES) > 4:
            _ANGLES.clear()
        _ANGLES[key] = t
    period = len(t)
    s = start % period
    if s + n <= period:
        return t[s:s + n]
    return np.tile(t, -(-(s + n) // period))[s:s + n]


_HANN: dict = {}


def _hann32(n: int) -> np.ndarray:
    w = _HANN.get(n)
    if w is None:
        w = _HANN[n] = np.hanning(n).astype(np.float32)
    return w


def _cis(ph: np.ndarray) -> np.ndarray:
    """exp(j ph) in complex64 via float32 cos/sin of the phase reduced to
    +-pi first: ten times cheaper than numpy's complex128 exp. The reduction
    is ph - 2 pi rint(ph / 2 pi), not np.mod: float64 np.mod costs ~14 ns a
    sample here, more than the cosine and sine together."""
    ph = np.asarray(ph, np.float64)
    a = (ph - (2 * np.pi) * np.rint(ph * (1 / (2 * np.pi)))).astype(np.float32)
    out = np.empty(len(a), np.complex64)
    out.real = np.cos(a)
    out.imag = np.sin(a)
    return out


# --- emphasis ----------------------------------------------------------------
def ccir405(f: np.ndarray) -> np.ndarray:
    """ITU-R (CCIR) Rec. F.405-1 pre-emphasis for 625-line FM video, the
    curve IARU R1 Technical Recommendation V.2 specifies for amateur FM-ATV:

        relative deviation (dB) = 10 log[(1 + 10.21 f^2) / (1 + 0.4083 f^2)] - 11.0

    with f in MHz - a minimum-phase shelf, H(s) = 10^(-11/20) (1 + s t1) /
    (1 + s t2), t1 = 508.5 ns (zero at 313.0 kHz), t2 = 101.7 ns (pole at
    1.565 MHz): -11 dB at low frequencies, 0 dB at the 1.512 MHz cross-over,
    +2.98 dB asymptotically, 14 dB of lift in all (docs/atv-research.md 4.2).
    Deviation is specified at the cross-over, so a picture's peak deviation
    is about the same with and without it while its low frequencies - sync,
    large flat areas - deviate 11 dB less."""
    f_mhz = np.asarray(f, np.float64) / 1e6
    return 10 ** (-11.0 / 20) * (1 + 1j * f_mhz * np.sqrt(10.21)) / (1 + 1j * f_mhz * np.sqrt(0.4083))


def audio_preemph(a: np.ndarray, rate: float, tau: float, state: list) -> np.ndarray:
    """First-order audio pre-emphasis (50 us for UK sound) by its difference
    equation, vectorised: y[n] = (x[n] - p x[n-1]) / (1 - p), a high-shelf
    zero at 1/(2 pi tau). Audio blocks are short (a frame is 1920 samples
    at 48 kHz), so this is cheap."""
    if tau <= 0:
        return a
    p = math.exp(-1.0 / (tau * rate))
    prev = np.concatenate([[state[0]], a[:-1]])
    state[0] = float(a[-1]) if len(a) else state[0]
    return (a - p * prev) / (1 - p)


def audio_deemph(a: np.ndarray, rate: float, tau: float, state: list) -> np.ndarray:
    """Inverse of audio_preemph: the one-pole lowpass y[n] = p y[n-1] +
    (1-p) x[n]. A recursion has no vectorised numpy form, so it is computed
    from its closed form, y[k] = p^(k+1) y[-1] + (1-p) p^k sum_i<=k x[i] p^-i,
    in chunks short enough (200 time constants) that p^-i stays well inside
    float64's range: e^200 is ~1e87 of its 1e308. Each output is dominated by
    its last few time constants' terms, so the large range costs no
    precision. (The chunks were 20 time constants: forty a frame at the
    sound's 250 kHz, and their Python overhead was half a millisecond.)"""
    if tau <= 0 or len(a) == 0:
        return np.asarray(a, np.float32)
    p = math.exp(-1.0 / (tau * rate))
    out = np.empty(len(a))
    y = state[0]
    chunk = max(1, int(200 * tau * rate))
    for s in range(0, len(a), chunk):
        x = np.asarray(a[s:s + chunk], np.float64)
        k = np.arange(len(x), dtype=np.float64)
        pk = p ** k
        out[s:s + len(x)] = p * pk * y + (1 - p) * pk * np.cumsum(x / pk)
        y = out[s + len(x) - 1]
    state[0] = y
    return out.astype(np.float32)


# --- sound -------------------------------------------------------------------
AUDIO_RATE = 48_000


def split_audio(audio) -> tuple[np.ndarray | None, np.ndarray | None]:
    """(mono, stereo) from a block of 48 kHz audio that may be either: the
    analogue sound carries the mono, (L + R) / 2 - EN 300 163 4.2.5.2's
    compatible mono, 6 dB down on the sum - and NICAM the pair."""
    if audio is None:
        return None, None
    a = np.asarray(audio, np.float32)
    if a.ndim == 2:
        return a.mean(axis=1), a
    return a, a


class SoundFm:
    """The FM sound signal's phase: audio (48 kHz, +-1 full scale) ->
    pre-emphasis -> phase in radians at 48 kHz -> interpolated to fs on demand.
    Phase is smooth (it is the integral of the audio), so linear
    interpolation is exact enough at a 200-400x rate ratio."""

    def __init__(self, fs: float, dev: float, tau: float = 50e-6):
        self.fs = fs
        self.dev = dev
        self.tau = tau
        self.pre = [0.0]
        self.phase = 0.0
        self.last = 0.0          # phase at the end of what has been used

    def phase_at_fs(self, audio: np.ndarray, n: int) -> np.ndarray:
        """Phase (radians, float64) for `n` samples at fs, spanning exactly
        the duration of `audio` (len(audio) / 48 kHz == n / fs)."""
        a = audio_preemph(np.asarray(audio, np.float64), AUDIO_RATE, self.tau, self.pre)
        a = np.clip(a, -1.5, 1.5)
        ph = self.last + np.cumsum(a) * (2 * np.pi * self.dev / AUDIO_RATE)
        ph = np.concatenate([[self.last], ph])
        self.last = float(ph[-1]) % (2 * np.pi)
        x = np.arange(n) * (len(audio) / n)
        return np.interp(x, np.arange(len(ph)), ph)


# --- AM-VSB --------------------------------------------------------------------
# How much of the complex band is usable: the AD936x's analogue filter is
# set to the sample rate, and the last few per cent of either edge are on its
# skirt and in the aliasing region of the half-band decimators.
USABLE = 0.95
VIDEO_NYQ = 0.48        # a real video signal sampled at fs keeps content to 0.48 fs

# The level an AD936x's image of a carrier must stay under: -40 dBc, what
# the 405-line tests on 5 October 2026 were held to (docs/atv-research.md
# 21) and what any other spurious output is.
IMAGE_LIMIT_DB = -40.0

# Where AM-VSB's carriers go about the LO. "images" (from 5 October 2026, late):
# centred unless an AD936x would then image a carrier above IMAGE_LIMIT_DB,
# and then wherever the worst image is lowest (_place). "centred": the
# signal centred in the band whatever it images - how every 625-line
# AM-VSB plan was placed before, the colour mode on air on 4 October 2026
# among them. Only playback asks for it: a recording holds the samples
# where the radio put them, so one made before has its vision carrier
# where "centred" says, 0.575 MHz from where "images" now puts it at
# 10 MS/s (docs/atv-research.md 24).
PLACEMENTS = ("images", "centred")


def am_plan(fs: float, video_bw: float = 5.5e6, vsb_lower: float = 1.25e6,
            sound_offset: float = 5.9996e6, nicam_on: bool = False, placement: str = "images") -> dict:
    """Where AM-VSB sits at this sample rate, and what fits. The vision
    carrier is placed so the signal is centred in the complex band, unless
    an AD936x would then put an image of a carrier above IMAGE_LIMIT_DB
    (_place; `placement` "centred" keeps the centred plan regardless, for
    recordings made before); the sound carrier is dropped (and said so)
    when the band cannot hold it, and the video bandwidth shrinks before
    anything else does.

    Returns {"carrier": Hz from the LO, "video_bw", "sound": Hz from the LO
    or None, "colour", "fits", "why": [sentences], "occupied": (lo, hi) Hz
    from the vision carrier, "nicam": Hz from the LO or None, "nicamWhy",
    "txImage": the strongest image an AD936x makes of a carrier (tx_image),
    "placement"}.

    `nicam_on`: send NICAM-728 too (ohsatv/nicam.py), 6.552 MHz above vision
    to 6.92 MHz. It moves the vision carrier further below the middle of the
    band so the whole signal stays centred; where the band cannot hold it,
    or the 6.5 MHz sound carrier would sit on it, the plan says so and
    leaves it out - the picture and the FM sound are never given up for it.
    `nicamRx` says whether a receiver at this rate and placement can hear
    NICAM at all (its band inside the radio's flat region), whatever we
    send."""
    half = USABLE * fs / 2
    vnyq = VIDEO_NYQ * fs
    why = []
    sound = None
    top = sound_offset + 0.1e6
    nwhy = ""
    with_nicam = False
    if nicam_on:
        if sound_offset and abs(sound_offset - nicam.CARRIER_I) < nicam.HALF_BW + 0.1e6:
            nwhy = (f"no NICAM: the {sound_offset / 1e6:g} MHz sound carrier sits on its 6.19-6.92 MHz "
                    f"(choose 6.0 MHz sound)")
        elif nicam.am_top() + vsb_lower > 2 * half:
            nwhy = (f"no NICAM at {fs / 1e6:g} MS/s: with it the signal spans "
                    f"{(nicam.am_top() + vsb_lower) / 1e6:.1f} MHz and the band carries {2 * half / 1e6:.1f} "
                    f"(10 MS/s or more)")
        else:
            with_nicam = True
            top = max(top if sound_offset else 0.0, nicam.am_top())
    if placement not in PLACEMENTS:
        raise ValueError(f"placement {placement!r}")
    parts = [("vision", 0.0, 0.0)]
    if (sound_offset or with_nicam) and top + vsb_lower <= 2 * half:
        occ = (-vsb_lower, top)
        carrier = snap(-(top - vsb_lower) / 2.0)
        if sound_offset:
            parts.append(("sound", sound_offset, 0.0))
        if with_nicam:
            parts.append(("NICAM", nicam.CARRIER_I, nicam.LEVEL_DB))
        if placement == "images":
            carrier = _place(fs, carrier, occ, parts, half)
        vbw = min(video_bw, vnyq, (sound_offset or nicam.CARRIER_I - nicam.HALF_BW) - 0.3e6, half - carrier)
        sound = snap(carrier + sound_offset) if sound_offset else None
    else:
        if sound_offset:
            why.append(f"no room for the {sound_offset / 1e6:.1f} MHz sound carrier at {fs / 1e6:g} MS/s")
        vbw = min(video_bw, vnyq, 2 * half - vsb_lower)
        carrier = snap(-(vbw - vsb_lower) / 2.0)
        occ = (-vsb_lower, vbw)
    if vbw < video_bw - 1:
        why.append(f"video limited to {vbw / 1e6:.2f} MHz at {fs / 1e6:g} MS/s")
    colour = vbw >= pal.FSC + 0.3e6
    if not colour:
        why.append("too narrow for the 4.43 MHz colour subcarrier: mono")
    fits = vbw >= 2.0e6
    if not fits:
        why.append("under 2 MHz of video: not a usable picture")
    if nwhy:
        why.append(nwhy)
    nic = snap(carrier + nicam.CARRIER_I)
    nicam_rx = abs(nic) + nicam.HALF_BW <= nicam.RX_EDGE * fs and not (
        sound_offset and abs(sound_offset - nicam.CARRIER_I) < nicam.HALF_BW + 0.1e6)
    im = tx_image(fs, carrier, parts)
    if im["db"] > IMAGE_LIMIT_DB:
        where = "on the picture" if occ[0] <= im["offset"] <= occ[1] else "outside the channel"
        under = "the vision carrier" if im["of"] == "NICAM" else "it"
        why.append(f"a Pluto (AD936x) sending this puts an image of the {im['of']} carrier "
                   f"{im['offset'] / 1e6:+.2f} MHz from vision, {where}, about {-im['db']:.0f} dB under {under}; "
                   f"no placement at {fs / 1e6:g} MS/s gets it {-IMAGE_LIMIT_DB:.0f} dB under")
    return {"carrier": carrier, "video_bw": vbw, "sound": sound, "colour": colour, "fits": fits,
            "why": why, "occupied": occ, "width": occ[1] - occ[0], "nicam": nic if with_nicam else None,
            "nicamWhy": nwhy, "nicamRx": bool(nicam_rx), "txImage": im, "placement": placement}


def tx_image(fs: float, carrier: float, parts) -> dict:
    """The strongest image an AD936x transmitter makes of an AM plan's
    carriers, in sysa.tx_image's shape: {"offset": Hz from the vision
    carrier, "db": how far under, "of": which}. `parts` are (name, Hz from
    the vision carrier, dB under peak vision). A carrier is judged against
    itself: the vision carrier's line falls as the picture whitens, to
    within a few dB of the sound's on a white field, so the sound's image
    is taken as if the sound were as strong. NICAM has no line - it is
    noise 0.73 MHz wide, 20 dB under peak vision - so its image is judged
    against peak vision, its own 20 dB counted."""
    r = ad936x.Response("tx", fs, fs)
    best = None
    for of, off, level in parts:
        img, db = r.image(carrier + off)
        db += level
        if best is None or db > best["db"]:
            best = {"offset": img - carrier, "db": db, "of": of}
    return best


def _place(fs: float, centred: float, occ: tuple, parts, half: float) -> float:
    """Where the vision carrier goes. Centred (`centred`) if every image
    tx_image looks at is then under IMAGE_LIMIT_DB - it is at 12.5 MS/s and
    above, and centred is what went on air there. Otherwise the place,
    among those that keep `occ` inside the band, where the worst of them is
    lowest. For two carriers that is equally far either side of the LO, as
    405 lines at 6.25 MS/s (sysa._placement), so both images are equally
    deep in HB1's stop band. 625 lines' vision and sound are 6 MHz apart,
    so at 10 MS/s that is +-3.0 MHz, 0.30 fs and -41 dB, where centred left
    the sound at 0.36 fs and its image at -25 (docs/atv-research.md 24).
    NICAM, 6.55 MHz above vision, cannot be inside 0.30 fs as well; judged
    against peak vision (tx_image) it does not need to be. The picture's
    own sidebands are not weighed: at the band's edges they are far weaker
    than the carriers, and tests/test_chain.py builds the whole signal
    through the chip to show it."""
    return _place_cached(float(fs), float(centred), tuple(occ), tuple(tuple(x) for x in parts), float(half))


# The engine asks for its plan several times a block (status, guard, LO),
# and a scan through the filters' response costs a few milliseconds even
# vectorised; the answer depends only on these numbers.
@lru_cache(maxsize=64)
def _place_cached(fs: float, centred: float, occ: tuple, parts: tuple, half: float) -> float:
    if tx_image(fs, centred, parts)["db"] <= IMAGE_LIMIT_DB:
        return centred
    lo, hi = -half - occ[0], half - occ[1]
    if hi < lo:
        return centred
    # A grid, and each pair of carriers exactly either side of the LO.
    sym = [-(a[1] + b[1]) / 2 for i, a in enumerate(parts) for b in parts[i + 1:]]
    cand = np.round(np.concatenate([np.linspace(lo, hi, 401), sym]) / 6.25) * 6.25
    cand = cand[(cand >= lo) & (cand <= hi)]
    r = ad936x.Response("tx", fs, fs)
    worst = np.full(len(cand), -np.inf)
    for _, off, level in parts:
        f = cand + off
        img = np.where(f < 0, f + fs, f - fs)
        db = 20 * np.log10(np.maximum(r(img), 1e-9) / np.maximum(r(f), 1e-9)) + level
        worst = np.maximum(worst, db)
    # Ties (within 0.01 dB) go to the place nearest centred.
    ok = np.flatnonzero(worst <= worst.min() + 0.01)
    return float(cand[ok[np.argmin(np.abs(cand[ok] - centred))]])


def _vsb_response(video_bw: float, vsb_lower: float):
    def h(f):
        upper = taper(f, video_bw, 0.4e6)
        lower = taper(-f, vsb_lower - 0.25e6, 0.5e6)       # flat to -0.75, gone by -1.25 MHz
        return np.where(f >= 0, upper, lower)
    return h


class AmVsbMod:
    """Composite (volts) -> AM-VSB IQ, System I levels. Sound -13 dB: the UK's
    20:1 vision-to-sound power ratio (BT.470 Table 3 note 25)."""

    def __init__(self, fs: float, video_bw: float = 5.5e6, vsb_lower: float = 1.25e6,
                 sound_offset: float = 5.9996e6, sound_db: float = -13.0, sound_dev: float = 50e3,
                 tx_shape=None, nicam_on: bool = False, placement: str = "images"):
        """`tx_shape`: the transmitting radio's own fixed response
        (devices/ad936x.py). AM-VSB is linear, so undoing it is exact: the
        VSB filter, which runs about the vision carrier, takes the inverse
        at each frequency's place about the LO, and the sound carrier's
        level is raised by what the radio will take off it. `nicam_on`:
        NICAM-728 as well, where the plan carries it, 20 dB below peak
        vision (EN 300 163 5.2.4), with the radio's roll-off undone across
        its 0.73 MHz the same way."""
        self.fs = float(fs)
        self.plan = am_plan(fs, video_bw, vsb_lower, sound_offset, nicam_on, placement)
        vsb = _vsb_response(self.plan["video_bw"], vsb_lower)
        c = self.plan["carrier"]
        self.vsb = Ols(design(lambda f: vsb(f) * ad936x.correction(tx_shape, f + c), fs, 127))
        self.sound = SoundFm(fs, sound_dev) if self.plan["sound"] is not None else None
        self.sound_amp = 10 ** (sound_db / 20.0)
        if self.sound is not None:
            self.sound_amp *= float(ad936x.correction(tx_shape, self.plan["sound"]))
        self.pos = 0
        self.nicam = None
        nic_peak = 0.0
        if self.plan["nicam"] is not None:
            centre = self.plan["nicam"]
            self.nicam = nicam.Transmitter(fs, eq=lambda f: ad936x.correction(tx_shape, centre + f),
                                           c4=1 if self.sound is not None else 0)
            self.nicam_amp = 10 ** (nicam.LEVEL_DB / 20)
            nic_peak = self.nicam_amp * nicam.PEAK
        # Peak envelope: sync tip (1.0) plus the sound carrier (and NICAM's
        # peaks); scale so the sum never exceeds the DAC's full scale.
        self.scale = 0.95 / (1.0 + (self.sound_amp if self.sound else 0.0) + nic_peak)

    def __call__(self, comp: np.ndarray, audio: np.ndarray | None = None) -> np.ndarray:
        comp = np.asarray(comp, np.float32).reshape(-1)
        n = len(comp)
        mono, stereo = split_audio(audio)
        env = np.clip(AM_BLANK - AM_SLOPE * comp, 0.0, 1.05)
        y = self.vsb(env) * _phasor(self.fs, self.plan["carrier"], n, self.pos)
        if self.sound is not None:
            if mono is None:
                mono = np.zeros(max(1, int(round(n * AUDIO_RATE / self.fs))), np.float32)
            ph = self.sound.phase_at_fs(mono, n)
            y += np.float32(self.sound_amp) * _phasor(self.fs, self.plan["sound"], n, self.pos) * _cis(ph)
        if self.nicam is not None:
            y += np.float32(self.nicam_amp) * self.nicam(stereo, n) * _phasor(self.fs, self.plan["nicam"], n,
                                                                                self.pos)
        self.pos += n
        return y * np.complex64(self.scale)


class AmVsbDemod:
    """Receive filter + envelope detector. The receive filter has the
    Nyquist slope across the carrier (-0.75..+0.75 MHz, half amplitude at
    the carrier), so the double-sideband part near the carrier and the
    single-sideband part above it come out of the envelope at the same
    level - flat video. Envelope detection does not care where the carrier
    is to within a bin or what its phase does, so the only frequency the
    demodulator needs is a coarse one: `track()` finds the vision carrier
    as the strongest spectral line near where it should be, and the filter
    is redesigned when it has moved by more than 20 kHz.
    """

    def __init__(self, fs: float, video_bw: float = 5.5e6, vsb_lower: float = 1.25e6,
                 sound_offset: float = 5.9996e6, sound_dev: float = 50e3, rx_shape=None, nicam_on: bool = False,
                 placement: str = "images"):
        """`rx_shape`: the receiving radio's own fixed response
        (devices/ad936x.py), undone in the receive filter (AM is linear:
        exactly). `nicam_on`: place the band as a transmitter sending
        NICAM would (the plan moves the vision carrier); NICAM is listened
        for wherever the band holds it (plan "nicamRx"), sent or not.
        `placement`: am_plan's - "centred" for a recording made before the
        carriers moved."""
        self.fs = float(fs)
        self.plan = am_plan(fs, video_bw, vsb_lower, sound_offset, nicam_on, placement)
        self.nicam = (nicam.Receiver(fs, self.plan["carrier"] + nicam.CARRIER_I, real=False, rx_shape=rx_shape)
                      if self.plan["nicamRx"] else None)
        self.rx_shape = rx_shape
        self.vbw = min(self.plan["video_bw"], video_bw)
        self.sound_offset = sound_offset
        self.sound_dev = sound_dev
        self.carrier = self.plan["carrier"]       # where the vision carrier is found
        self.filt = None
        self.snd = None
        self.coherent = True
        self.pos = 0                 # stream index of the next filter output
        self.last_tip = None         # (stream index, unwrapped phase) of the last sync tip seen
        self._design(self.carrier)

    def _design(self, carrier: float):
        vbw = self.vbw
        c = carrier

        def h(f):
            g = f - c
            nyq = np.clip(0.5 + g / 1.5e6, 0.0, 1.0)            # Nyquist slope, 0.5 at the carrier
            top = taper(g, vbw, 0.4e6)
            return np.where(g < 0.75e6, nyq, top) * ad936x.correction(self.rx_shape, f)

        old = self.filt
        # The filter also brings the carrier down to (within half a bin of)
        # DC, which the synchronous detector would otherwise do per sample.
        self.filt = Ols(design(h, self.fs, 127), shift=c, fs=self.fs)
        if old is not None:
            self.filt.hist = old.hist
            self.filt.pos = old.pos
        self.carrier_designed = carrier
        if self.plan["sound"] is not None:
            dec = max(1, int(self.fs // 250e3))
            self.snd = SoundDemod(self.fs, c + self.sound_offset, dec, self.sound_dev)

    def track(self, iq: np.ndarray, search: float = 1.0e6) -> float | None:
        """Re-measure the vision carrier from a block; returns it (Hz from DC)."""
        n = 1 << 14
        if len(iq) < n:
            return None
        segs = np.asarray(iq[: (len(iq) // n) * n], np.complex64).reshape(-1, n)[:8]
        # complex64 and a scaled transform (see dsp/filters.py, Ols): half the
        # cost of the float64 window's complex128. Only ratios are used.
        p = (np.abs(np.fft.fft(segs * _hann32(n), axis=1, norm="forward")) ** 2).mean(axis=0)
        f = np.fft.fftfreq(n, 1 / self.fs)
        sel = np.abs(f - self.plan["carrier"]) <= search
        idx = np.flatnonzero(sel)
        k = idx[np.argmax(p[idx])]
        # A carrier is a line, well above the sideband energy around it.
        neigh = np.median(p[idx])
        if p[k] < 30 * neigh:
            return None
        a, b, cc = p[(k - 1) % n], p[k], p[(k + 1) % n]
        d = 0.5 * (a - cc) / (a - 2 * b + cc) if (a - 2 * b + cc) else 0.0
        found = f[k] + d * self.fs / n
        self.carrier = found
        if abs(found - self.carrier_designed) > 20e3:
            self._design(found)
        return found

    def __call__(self, iq: np.ndarray) -> np.ndarray:
        y = self.filt(iq)          # filtered, and shifted down by filt.shift
        n = len(y)
        pos = self.pos
        self.pos += n
        env = np.abs(y)
        if not self.coherent or n == 0:
            return -env
        out = self._coherent(y, env, pos)
        return -env if out is None else out

    # The residual carrier phase is applied in steps of this many samples
    # (each step at its centre value): 0.8 us at 10 MS/s. The residual is
    # under ~1 kHz once tracked, so a step is off by at most ~0.003 rad -
    # far below the phase noise of the sync tips it comes from - where a
    # phasor per sample cost two float32 transcendentals and a float64
    # modulo for every sample (8 of the 15 ms this demodulator took at
    # 10 MS/s; docs/PERF.md).
    STEP = 8

    def _coherent(self, y: np.ndarray, env: np.ndarray, pos: int) -> np.ndarray | None:
        """Synchronous detection with the carrier phase taken from the sync
        tips. An envelope detector on a vestigial-sideband signal suffers
        quadrature distortion: measured on a flat colour patch it lost a
        quarter of the saturation and shifted hue. A synchronous detector
        needs the carrier's phase, and negative modulation hands it over 15 625
        times a second: during every sync pulse the transmitter sends pure,
        unmodulated carrier at full amplitude. So: find the sync tips (the
        strongest few per cent of the envelope), average the carrier phasor
        over each, unwrap from tip to tip, interpolate across the line, and
        take the in-phase component. The receive filter has already moved
        the carrier to within half an FFT bin of DC and the rest of the
        tracked frequency is taken out here, so the phase only has to follow
        what tracking missed (Hz to a few kHz) - well inside the
        half-cycle-per-line limit of unwrapping (7.8 kHz)."""
        n = len(y)
        top, mid = (float(v) for v in np.percentile(env[::7], [99.5, 50.0]))
        thr = top - 0.35 * (top - max(mid, 0.0)) if top > 0 else 0.0
        thr = max(thr, 0.85 * top)
        idx = np.flatnonzero(env > thr)
        if len(idx) < 50:
            return None
        brk = np.flatnonzero(np.diff(idx) > int(0.5e-6 * self.fs)) + 1
        starts = np.concatenate([[0], brk])
        counts = np.diff(np.concatenate([starts, [len(idx)]]))
        keep = counts >= int(0.8e-6 * self.fs)
        if keep.sum() < 4:
            return None
        k = idx.astype(np.float64)
        # The residual's phase at the block start, reduced once as a scalar:
        # within the block w k stays under a few hundred radians, where
        # float32 still resolves 1e-4 rad.
        w = 2 * np.pi * ((self.carrier - self.filt.shift) / self.fs)
        w0 = (w * pos) % (2 * np.pi)
        u = y[idx] * _cis(-(w0 + w * k))
        u = u / np.maximum(np.abs(u), 1e-12)
        ph_sum = np.add.reduceat(u, starts)[keep]
        centre = (np.add.reduceat(k, starts) / counts)[keep]
        ph = np.angle(ph_sum)
        # Continue from the last tip of the previous block. It is kept as the
        # tip's total carrier phase (mod 2 pi), and re-expressed against this
        # block's residual, so a change of the tracked frequency or of the
        # filter's shift between blocks does not step the phase.
        if self.last_tip is not None:
            i_prev, tot_prev = self.last_tip
            prev = (tot_prev - self.filt.shift * 2 * np.pi / self.fs * i_prev - w * i_prev) % (2 * np.pi)
            centre = np.concatenate([[i_prev - pos], centre])
            ph = np.concatenate([[prev], ph])
        ph = np.unwrap(ph)
        i_last = float(centre[-1] + pos)
        self.last_tip = (i_last, float((w0 + w * centre[-1] + ph[-1] + 2 * np.pi * self.filt.shift / self.fs
                                        * i_last) % (2 * np.pi)))
        D = self.STEP
        m = -(-n // D)
        g = np.arange(m, dtype=np.float64) * D + (D - 1) / 2
        rot = _cis(-(w0 + w * g + np.interp(g, centre, ph)))
        out = np.empty(m * D, np.float32)
        full = (n // D) * D
        z = y[:full].reshape(-1, D) * rot[:full // D, None]
        out[:full] = z.real.reshape(-1)
        if full < n:
            out[full:n] = (y[full:] * rot[-1]).real
        return -out[:n]

    def audio(self, iq: np.ndarray) -> np.ndarray:
        return self.snd(iq) if self.snd is not None else np.zeros(0, np.float32)

    def nicam_audio(self, iq: np.ndarray) -> np.ndarray | None:
        """NICAM's stereo sound from this block, (n, 2) at 48 kHz, or None
        where this band cannot hold it. Its carrier is 6.552 MHz above the
        vision carrier as tracked, so it follows the transmitter's error."""
        if getattr(self, "nicam", None) is None:
            return None
        self.nicam.retune(self.carrier + nicam.CARRIER_I)
        return self.nicam(iq)


# --- FM -----------------------------------------------------------------------
def fm_plan(fs: float, deviation: float = 7e6, video_bw: float = 5.0e6, sub: float = 6.0e6,
            sub_index: float = 0.07, nicam_on: bool = False) -> dict:
    """What fits for FM-ATV at this sample rate. `deviation` is peak-to-peak
    for a full sync-to-white signal at the pre-emphasis cross-over (CCIR
    F.405 convention; IARU R1 V.2's "3.5 MHz peak" is 7 MHz here); the sound
    subcarrier deviates the carrier by `sub_index` x its own frequency
    (V.2: 0.07). Occupied bandwidth by Carson's rule on the video, 2 x (peak
    deviation + video bandwidth): the subcarrier's small index adds to the
    peak deviation but its sidebands are ~30 dB down, so it does not set the
    width (IARU V.2's 12 MHz at -40 dB / 18 MHz at -50 dB are the measured
    counterpart). It fits when Carson's width is inside the sample rate.

    `nicam_on`: a NICAM-728 subcarrier at 6.552 MHz too (ohsatv/nicam.py),
    beside the analogue sound as in System I - the analogue sound stays,
    so a receiver without NICAM, or one that loses it, still has sound. It
    needs the baseband to 6.92 MHz inside the video Nyquist limit (16 MS/s
    and up), the 6.5 MHz subcarrier out of its way, and its deviation
    added to the peak; "nicamWhy" says which of these stops it."""
    half = USABLE * fs / 2
    why = []
    vbw = min(video_bw, 0.45 * fs)
    sub_ok = bool(sub) and sub + 0.15e6 <= VIDEO_NYQ * fs
    if sub and not sub_ok:
        why.append(f"no room for the {sub / 1e6:g} MHz sound subcarrier at {fs / 1e6:g} MS/s")
    if sub_ok:
        vbw = min(vbw, sub - 0.6e6)
    if vbw < video_bw - 1:
        why.append(f"video limited to {vbw / 1e6:.2f} MHz")
    sub_peak = sub_index * sub if sub_ok else 0.0
    nic_peak = nicam.FM_RMS_DEV * math.sqrt(2) * nicam.PEAK
    clash = sub_ok and abs(sub - nicam.CARRIER_I) < nicam.HALF_BW + 0.1e6
    room = nicam.TOP + 0.15e6 <= VIDEO_NYQ * fs
    nwhy = ""
    with_nicam = False
    if nicam_on:
        if clash:
            nwhy = f"no NICAM: the {sub / 1e6:g} MHz sound subcarrier sits on its 6.19-6.92 MHz (choose 6.0 MHz)"
        elif not room:
            nwhy = (f"no NICAM at {fs / 1e6:g} MS/s: its subcarrier reaches 6.92 MHz, past the "
                    f"{VIDEO_NYQ * fs / 1e6:.1f} MHz this rate carries (16 MS/s or more)")
        elif 2 * (deviation / 2 + sub_peak + nic_peak + vbw) > 2 * half:
            most = 2 * max(0.0, half - vbw - nic_peak - sub_peak)
            nwhy = (f"no NICAM at {deviation / 1e6:g} MHz p-p: with it the signal needs "
                    f"{2 * (deviation / 2 + sub_peak + nic_peak + vbw) / 1e6:.1f} MHz (Carson) and "
                    f"{fs / 1e6:g} MS/s carries {2 * half / 1e6:.1f}: {most / 1e6:.1f} MHz p-p at most")
        else:
            with_nicam = True
    if nwhy:
        why.append(nwhy)
    peak = deviation / 2 + sub_peak + (nic_peak if with_nicam else 0.0)
    carson = 2 * (peak + vbw)
    colour = vbw >= pal.FSC + 0.3e6
    if not colour:
        why.append("too narrow for colour: mono")
    fits = carson <= 2 * half
    if not fits:
        why.append(f"needs {carson / 1e6:.1f} MHz (Carson) but {fs / 1e6:g} MS/s carries "
                   f"{2 * half / 1e6:.1f} MHz: lower the deviation or raise the sample rate")
    im = fm_tx_image(fs, sub if sub_ok else None, sub_index, with_nicam)
    iwhy = fm_image_why(im, (-carson / 2, carson / 2))
    if iwhy:
        why.append(iwhy)
    return {"video_bw": vbw, "sub": sub if sub_ok else None, "colour": colour, "carson": carson,
            "fits": fits, "why": why, "occupied": (-carson / 2, carson / 2), "width": carson,
            "peak": peak, "nicam": nicam.CARRIER_I if with_nicam else None, "nicamWhy": nwhy,
            "nicamRx": bool(room and not clash), "subIndex": sub_index if sub_ok else 0.0, "txImage": im}


# FM-ATV's images (docs/atv-research.md 24; Brian's call on 6 October
# 2026: warn as AM does, change nothing). An FM carrier sits on the LO and
# cannot be placed, so what an AD936x images is whatever lies near fs/2. In
# every plan the whole-signal model was run on, the strongest such thing
# was a subcarrier's first sidebands: the sound's at 0.44 fs in Low-PAL at
# 5 MS/s and at 12.5 MS/s with the 5.5 MHz subcarrier (-37.4 and -38.8 dBc),
# NICAM's upper edge at 16 MS/s. The picture's own Carson edge never came
# within 13 dB of the limit (-53 dBc at worst) and is not weighed here.
#
# A subcarrier's first sideband is the carrier's whole spectrum moved by the
# subcarrier's frequency and scaled by J1 of its index, so the picture
# spreads it just as it spreads the carrier. FM_SPREAD_DB is how far its
# strongest 100 kHz falls under J1's total: -3.4 to -3.9 dB in the
# whole-signal model (test card, 1 kHz tone) at every rate and deviation it
# was run at, 1.2 to 4 MHz peak-to-peak. tests/test_chain.py builds the
# signal through the chip and holds the two together. Without it the figures
# read 3.5 dB worse than the signal they describe: Low-PAL's -37.4 would be
# called -34.
FM_SPREAD_DB = -3.5


def fm_tx_image(fs: float, sub: float | None, sub_index: float, with_nicam: bool,
                radio: str = "pluto") -> dict | None:
    """The strongest image a transmitter makes of an FM plan's subcarriers,
    in tx_image's shape: {"offset": Hz from the carrier (the LO), "db": dBc
    in 100 kHz against the whole signal's power (the measure of an FM
    carrier's spurious emissions; AM's txImage is dB under the carrier
    imaged instead), "of": "sound subcarrier" or "NICAM"}. None for a plan
    with neither: nothing else near fs/2 is modelled.

    `radio` "pluto": an AD936x's half-bands (ad936x.Response) with the
    modulator's transmit correction, which lifts a subcarrier's sidebands,
    and so their image, by what the half-bands take off them (3.6 dB at
    0.44 fs, docs/atv-research.md 17). "hackrf": its converter's hold and
    the baseband filter libhackrf picks for the rate, which an FM over goes
    out through, with the model's shortfall (devices/hackrf.py); no
    correction, since FmMod is given none for it."""
    if not sub and not with_nicam:
        return None
    return _fm_image_cached(float(fs), float(sub or 0.0), float(sub_index), bool(with_nicam), radio)


# The engine asks for its plan several times a block. Uncached this costs
# ~0.4 ms with NICAM (0.15 without); cached, a dictionary lookup - as
# _place_cached, for the same reason.
@lru_cache(maxsize=64)
def _fm_image_cached(fs: float, sub: float, sub_index: float, with_nicam: bool, radio: str) -> dict:
    if radio == "pluto":
        r = ad936x.Response("tx", fs, fs)

        def boost(f):
            return r.correction(f)

        def image(f):
            img = np.where(f < 0, f + fs, f - fs)
            return img, 20 * np.log10(np.maximum(r(img), 1e-9) / np.maximum(r(f), 1e-9))
    elif radio == "hackrf":
        from .devices import hackrf
        bw = hackrf.default_filter(fs)

        def boost(f):
            return np.ones(np.shape(f))

        def image(f):
            img = np.where(f > 0, f - fs, f + fs)
            db = hackrf.attenuation_db(f, bw, fs) - hackrf.attenuation_db(img, bw, fs)
            return img, np.minimum(db + hackrf.FILTER_SHORTFALL_DB, 0.0)
    else:
        raise ValueError(f"radio {radio!r}")
    best = None
    if sub:
        # Both sidebands image alike (the responses are even); the upper
        # one's image is below the carrier, where the whole-signal model
        # found it.
        b = sub_index * float(boost(sub))
        j1 = b / 2 * (1 - b * b / 8)            # J1(b) to 1e-5 for the indices used (< 0.2)
        img, db = image(np.array([sub]))
        best = {"offset": float(img[0]), "db": float(20 * np.log10(j1) + FM_SPREAD_DB + db[0]),
                "of": "sound subcarrier"}
    if with_nicam:
        # NICAM is noise 0.73 MHz wide, so its sidebands are its spectrum
        # (the shaping, squared), (rms deviation / f)^2 / 2 of the power a
        # side, each 10 kHz lifted by the correction and taken through the
        # image response there. No spread: NICAM is broad already, and
        # without one this reads 2 dB over the whole-signal model at 16 MS/s
        # - on the side of warning.
        f = nicam.CARRIER_I + np.arange(-nicam.HALF_BW, nicam.HALF_BW + 1, 10e3)
        psd = nicam.shaping(f - nicam.CARRIER_I) ** 2
        side = (nicam.FM_RMS_DEV * boost(f) / f) ** 2 / 2 * psd / psd.sum()
        img, db = image(f)
        per100 = np.convolve(side * 10 ** (db / 10), np.ones(10), "same")
        k = int(np.argmax(per100))
        cand = {"offset": float(img[k]), "db": float(10 * np.log10(max(per100[k], 1e-30))), "of": "NICAM"}
        if best is None or cand["db"] > best["db"]:
            best = cand
    return best


def fm_image_why(im: dict | None, occupied: tuple) -> str | None:
    """The plan card's sentence for an FM plan whose image on a Pluto
    (fm_tx_image) is over IMAGE_LIMIT_DB, or None."""
    if not im or im["db"] <= IMAGE_LIMIT_DB:
        return None
    where = "on the picture" if occupied[0] <= im["offset"] <= occupied[1] else "outside the channel"
    return (f"a Pluto (AD936x) sending this puts an image of {fm_image_of(im)} sidebands "
            f"{im['offset'] / 1e6:+.2f} MHz from the carrier, {where}, about {-im['db']:.0f} dBc in 100 kHz; "
            f"FM cannot move its carrier away from it")


def fm_image_of(im: dict) -> str:
    """Whose sidebands an fm_tx_image is the image of, for a sentence."""
    return "NICAM's" if im["of"] == "NICAM" else f"the {im['of']}'s"


class FmMod:
    """Composite (volts) -> FM IQ. Sync tip is the low-frequency extreme,
    peak white the high one, `deviation` Hz apart at the pre-emphasis
    cross-over (fm_plan); `invert` swaps them. The sound subcarrier deviates
    the carrier by `sub_index` x its frequency, and is itself FM, `sub_dev`
    Hz with 50 us pre-emphasis."""

    def __init__(self, fs: float, deviation: float = 7e6, video_bw: float = 5.0e6, preemph: bool = True,
                 sub: float = 6.0e6, sub_dev: float = 50e3, sub_index: float = 0.07, invert: bool = False,
                 edge: float = 0.4e6, ntaps: int = 127, plan: dict | None = None, sub_bw: float = 220e3,
                 tx_shape=None, nicam_on: bool = False):
        """`edge`, `ntaps` and `plan` are for the narrow formats
        (ohsatv/narrow.py): a sharper video roll-off, and a plan of their own
        in place of fm_plan's. `tx_shape` is the transmitting radio's own
        fixed response (devices/ad936x.py), undone here: see below.
        `nicam_on`: a NICAM-728 subcarrier as well, where the plan carries
        it (fm_plan)."""
        self.fs = float(fs)
        self.plan = plan if plan is not None else fm_plan(fs, deviation, video_bw, sub, sub_index, nicam_on)
        self.dev = deviation
        vbw = self.plan["video_bw"]

        # The radio's own filters cut the FM sidebands at +-f from the
        # carrier. Colour and the sound subcarrier deviate the carrier only
        # a little (an index under ~0.1), and for such a component the
        # sidebands at +-f come out of a discriminator as the component at f
        # scaled by the response there - so boosting the video at f by what
        # the radio loses at +-f puts the radiated sidebands where the plan
        # says, inside the filter this modulator runs anyway, at no cost per
        # sample. A filter on the IQ would do the same for every sample at
        # the full rate (as much again as the whole demodulator, measured).
        # The carrier's swing with the picture moves the sidebands about
        # +-f by up to half the deviation; the response is smooth there, so
        # the error is a second-order one (tests/test_chain.py, Ad936x).
        def h(f):
            g = taper(np.abs(f), vbw, edge) * ad936x.correction(tx_shape, f)
            return g * ccir405(np.abs(f)) if preemph else g
        # Emphasis applied to a real signal must be conjugate-symmetric:
        # use the response for |f| and its conjugate for negative f.

        def hh(f):
            r = h(f)
            return np.where(f >= 0, r, np.conj(r))
        self.filt = OlsReal(design(hh, fs, ntaps))
        self.sign = -1.0 if invert else 1.0
        self.sound = SoundFm(fs, sub_dev) if self.plan["sub"] else None
        self.sub = self.plan["sub"]
        self.sub_amp = sub_index * (self.sub or 0.0)
        if self.sub:
            self.sub_amp *= float(ad936x.correction(tx_shape, self.sub))
        self.pos = 0
        self.phase = 0.0
        self.block = pal.samples_per_line(fs) if abs(fs / pal.FH - round(fs / pal.FH)) < 1e-6 else 1024
        self.nicam = None
        if self.plan.get("nicam"):
            # A subcarrier deviates the carrier by its own amplitude; its
            # sidebands at +-f lose what the radio loses there, so the
            # radio's roll-off is undone across NICAM's 0.73 MHz as the
            # sound subcarrier's level is (above).
            self.nicam = nicam.Transmitter(fs, eq=lambda f: ad936x.correction(tx_shape, nicam.CARRIER_I + f),
                                           c4=1 if self.sound is not None else 0)
            self.nicam_k = np.float32(math.sqrt(2) * nicam.FM_RMS_DEV * 2 * np.pi / self.fs)

    def __call__(self, comp: np.ndarray, audio: np.ndarray | None = None) -> np.ndarray:
        comp = np.asarray(comp, np.float32).reshape(-1)
        n = len(comp)
        audio, stereo = split_audio(audio)
        # Map -0.3..0.7 V to -dev/2..+dev/2 about the carrier.
        v = (comp - (pal.SYNC_TIP + pal.WHITE) / 2) * (self.dev / (pal.WHITE - pal.SYNC_TIP))
        # Instantaneous frequency in radians per sample, float32.
        f = self.filt(v) * np.float32(self.sign * 2 * np.pi / self.fs)
        if self.sound is not None:
            if audio is None:
                audio = np.zeros(max(1, int(round(n * AUDIO_RATE / self.fs))), np.float32)
            # The subcarrier's angle is its periodic table's (float32, 0..2 pi)
            # plus the audio's FM phase reduced to 0..2 pi: one float32 cosine
            # of a small angle. (A float64 cosine of the full phase was a
            # third of this modulator's time at 20 MS/s.)
            ph = np.mod(self.sound.phase_at_fs(audio, n), 2 * np.pi).astype(np.float32)
            ph += _angle_table(self.fs, self.sub, n, self.pos)
            f += np.float32(self.sub_amp * 2 * np.pi / self.fs) * np.cos(ph)
        if self.nicam is not None:
            f += self.nicam_k * (self.nicam(stereo, n) * _phasor(self.fs, nicam.CARRIER_I, n, self.pos)).real
        # Phase: accumulate within each block of LINE samples in float32 (a
        # line's phase stays under ~2000 rad, where float32 still resolves
        # 1e-4 rad), and carry each block's start as a float64 offset reduced
        # modulo 2 pi - the precision of a float64 running sum without
        # paying for float64 arithmetic on every sample.
        blk = self.block
        m = -(-n // blk) * blk
        if m != n:
            f = np.concatenate([f, np.zeros(m - n, np.float32)])
        f2 = f.reshape(-1, blk)
        cs = np.cumsum(f2, axis=1, dtype=np.float32)
        ends = np.cumsum(cs[:, -1].astype(np.float64))
        starts = np.concatenate([[0.0], ends[:-1]]) + self.phase
        ph = cs + (starts % (2 * np.pi)).astype(np.float32)[:, None]
        self.phase = float(starts[-1] + cs[-1, n - 1 - (len(cs) - 1) * blk]) % (2 * np.pi)
        ph = ph.reshape(-1)[:n]
        out = np.empty(n, np.complex64)
        out.real = np.cos(ph)
        out.imag = np.sin(ph)
        out *= np.float32(0.95)
        self.pos += n
        return out


class FmDemod:
    """Discriminator -> video lowpass + de-emphasis (one filter). Output is
    instantaneous frequency in Hz, filtered: a raw composite (sync low)."""

    def __init__(self, fs: float, deviation: float = 7e6, video_bw: float = 5.0e6, preemph: bool = True,
                 sub: float = 6.0e6, sub_dev: float = 50e3, sub_index: float = 0.07, invert: bool = False,
                 edge: float = 0.4e6, ntaps: int = 127, plan: dict | None = None, sub_bw: float = 220e3,
                 rx_shape=None, nicam_on: bool = False):
        """`rx_shape`: the receiving radio's own fixed response
        (devices/ad936x.py), undone in the video filter for the reason
        FmMod gives. The sound subcarrier is left as it comes: it is FM
        itself, so its level changes its noise, not its audio. NICAM is
        listened for wherever the baseband holds it (plan "nicamRx"),
        whether or not `nicam_on` says we send it."""
        self.fs = float(fs)
        self.plan = plan if plan is not None else fm_plan(fs, deviation, video_bw, sub, sub_index, nicam_on)
        self.nicam = (nicam.Receiver(fs, nicam.CARRIER_I, real=True, rx_shape=rx_shape)
                      if self.plan.get("nicamRx") else None)
        vbw = self.plan["video_bw"]

        def h(f):
            g = taper(np.abs(f), vbw, edge) * ad936x.correction(rx_shape, f)
            r = g / ccir405(np.abs(f)) if preemph else g + 0j
            return np.where(f >= 0, r, np.conj(r))
        # The response is conjugate-symmetric, so the taps are real and the
        # discriminator's output is real: rfft/irfft do half the work of the
        # complex filter this used to be.
        self.filt = OlsReal(np.real(design(h, fs, ntaps)) * (-1.0 if invert else 1.0))
        self.prev = np.complex64(1.0)
        self.snd = None
        if self.plan["sub"]:
            dec = max(1, int(self.fs // 250e3))
            self.snd = SoundDemod(self.fs, self.plan["sub"], dec, sub_dev, bw=sub_bw, real=True)
        self.last_disc = np.zeros(0, np.float32)

    def __call__(self, iq: np.ndarray) -> np.ndarray:
        iq = np.asarray(iq, np.complex64)
        n = len(iq)
        if n == 0:
            return np.zeros(0, np.float32)
        q = np.empty(n, np.complex64)
        q[0] = iq[0] * np.conj(self.prev)
        np.multiply(iq[1:], np.conj(iq[:-1]), out=q[1:])
        self.prev = iq[-1]
        # np.angle of complex64 is float32 already; scaling in place saves
        # two copies of the block. The arctangent itself is the dearest
        # single operation here (~9 ns a sample: numpy has no vectorised
        # arctan2) and has no cheaper exact substitute in numpy - a
        # polynomial one costs more in temporaries than it saves.
        d = np.angle(q)
        d *= np.float32(self.fs / (2 * np.pi))
        self.last_disc = d
        return self.filt(d)

    def audio(self, iq: np.ndarray) -> np.ndarray:
        """Call after __call__ on the same block: the subcarrier is taken
        from the discriminator output computed there."""
        if self.snd is None:
            return np.zeros(0, np.float32)
        return self.snd(self.last_disc)

    def nicam_audio(self, iq: np.ndarray) -> np.ndarray | None:
        """NICAM's stereo sound, (n, 2) at 48 kHz, from the discriminator
        output of the last __call__; None where the baseband cannot hold it."""
        if getattr(self, "nicam", None) is None:
            return None
        return self.nicam(self.last_disc)


class SoundDemod:
    """FM sound: channel out ~200 kHz around `centre`, decimate, discriminate,
    de-emphasise (50 us), resample to 48 kHz. Output is audio scaled so
    `dev` Hz of deviation is full scale."""

    def __init__(self, fs: float, centre: float, dec: int, dev: float, tau: float = 50e-6, bw: float = 220e3,
                 real: bool = False):
        """`real`: the input is real (a subcarrier in the FM discriminator's
        output), not IQ."""
        self.fs = fs
        self.ch = Channel(fs, centre, bw, dec, real=real)
        self.rate = fs / dec
        self.dev = dev
        self.tau = tau
        self.prev = np.complex64(1.0)
        self.de = [0.0]
        self.frac = 0.0
        self.tail = np.zeros(0, np.float64)

    def __call__(self, x: np.ndarray) -> np.ndarray:
        y = self.ch(x)
        if len(y) == 0:
            return np.zeros(0, np.float32)
        d = self._detect(y)
        # Lowpass ~15 kHz (a box whose first null is at ~30 kHz), then
        # resample to 48 kHz by interpolation from a running buffer, so
        # the output grid is continuous across blocks.
        w = max(1, int(self.rate / 30e3))
        buf = np.concatenate([self.tail, d])
        if w > 1:
            c = np.cumsum(np.concatenate([[0.0], buf]))
            sm = (c[w:] - c[:-w]) / w
        else:
            sm = buf
        step = self.rate / AUDIO_RATE
        x = np.arange(self.frac, len(sm) - 1, step)
        out = np.interp(x, np.arange(len(sm)), sm)
        # Keep enough history for the next block's box and interpolation.
        used = int(x[-1] + step) if len(x) else 0
        keep_from = max(0, min(used, len(sm) - 1))
        self.frac = (x[-1] + step - keep_from) if len(x) else self.frac - keep_from
        self.tail = buf[keep_from:]
        return out.astype(np.float32)

    def _detect(self, y: np.ndarray) -> np.ndarray:
        """The channel's complex baseband -> audio at self.rate: FM here;
        405-line System A's AM sound overrides it (ohsatv/sysa.py)."""
        prev = np.concatenate([[self.prev], y[:-1]])
        self.prev = y[-1]
        d = np.angle(y * np.conj(prev)) * (self.rate / (2 * np.pi * self.dev))
        return audio_deemph(d, self.rate, self.tau, self.de)
