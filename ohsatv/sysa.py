"""405-line System A: the British standard of 1936-1985, transmit and receive.

Brian (GM4BRI) asked for it on air in the amateur bands (AM, as it was
broadcast) and by cable into vintage receivers (DESIGN.md "405 lines" and
"Cable feed"). The figures are CCIR Report 624-2 (1982), the last edition
that described the system; ohsatv/lines.py has the timing with each source.
hacktv (fsphil/hacktv, src/video.c, mode "a"; GPLv3 as this program is)
was used to cross-check them; where the two differ DESIGN.md says which
was followed and why. No hacktv code is used.

System A is **inverted** against every later system: the sound carrier is
3.5 MHz *below* vision, the picture's full 3 MHz sideband lies between
them, and the 0.75 MHz vestige is *above* the vision carrier, with the
channel's edge 1.25 MHz above it (Report 624-2 Table III items 2-5; hacktv:
vsb_upper_bw 0.75 MHz, vsb_lower_bw 3 MHz). So the whole signal, sound
included, is 4.3 MHz wide. That would fit the ~5 MS/s a USB Pluto can
transmit, but on air the sound carrier's image came out outside the
channel at -13 dBc there (docs/atv-research.md 21), so below 6.25 MS/s
405 lines go without sound (SOUND_MIN_FS) - a 3.75 MHz channel with the
full picture.

What that takes, and why this is not pal.py with other numbers:

- **Video wider than the sample rate's real Nyquist.** 3 MHz of picture
  cannot be a real signal at 5 MS/s (2.5 MHz), although its single
  sideband fits the complex band easily. So the encoder hands the modulator
  each frame at its own line-locked rate - 750 samples a line, 7.59 MS/s,
  wherever the radio runs - and the modulator builds the vestigial-sideband
  signal's spectrum for the whole 40 ms frame directly at the radio's rate.
  A 405-line line is 98.765 us, never a whole number of samples at the
  radio's rates (5 MS/s: 493.83), while a frame always is, so a frame is
  the unit; the resampling is exact and band-limited, and the vestigial
  filter is the same multiply. The receiver does the reverse below
  7.5 MS/s: it interpolates by two before detecting, so the picture comes
  out at twice the radio's rate (`video_rate`).
- **Positive modulation.** Sync at zero carrier, blanking (and black) 30 %,
  peak white 100 % (Table III items 7-8: sync "< 3", blanking 30,
  black-to-blanking 0, white 100; hacktv the same). The 625-line receiver's
  synchronous detector takes its carrier phase from the sync tips, where
  negative modulation sends pure carrier; here the sync tips send nothing.
  The phase is taken from the mean of the signal over each line instead:
  the vestigial sideband's quadrature part is the picture's detail, which
  averages to nothing, while the carrier is at least 30 % for most of it.
- **AM sound** (A3E, item 9), a quarter of vision's power (item 12: 4/1),
  no pre-emphasis (item 11 is blank for A), audio to 10 kHz (hacktv's
  am_mono_bandwidth).
- Mono: System A never carried colour.
"""
from __future__ import annotations

import math
from functools import lru_cache

import numpy as np

from . import lines, modem, pal
from .devices import ad936x
from .dsp.filters import Ols, design, taper

STD = lines.SYSTEM_A
FH = lines.A_FH

# --- levels (fraction of peak carrier amplitude), Report 624-2 Table III -----
A_SYNC, A_BLANK, A_WHITE = 0.0, 0.30, 1.0
A_SLOPE = (A_WHITE - A_BLANK) / pal.WHITE       # per volt: 1.0 (sync -0.3 V -> no carrier)

VIDEO_BW = 3.0e6            # Table I item 6, Table III item 4: the main (lower) sideband
VSB = 0.75e6                # Table III item 5: the vestige, above the vision carrier
SOUND_OFFSET = -3.5e6       # Table III item 2
SOUND_DB = -6.0             # Table III item 12: vision / sound e.r.p. 4/1
SOUND_DEPTH = 0.8           # AM depth at full-scale audio (the 1 kHz test tone at 0.5 is 40 %)
AUDIO_BW = 10e3             # the audio the sound carrier carries (hacktv: 10 kHz)
SOUND_EDGE = 0.05e6         # the sound's sidebands and a margin, below its carrier
VIDEO_EDGE = 0.3e6          # the picture's roll-off at the bottom of the main sideband
MIN_VIDEO = 1.0e6           # under this the plan calls it no picture
LINE_N = 750                # the encoder's samples a line: 7.59 MS/s, Nyquist 3.8 MHz over 3.15 of video
FRAME_S = 0.04
BIN_HZ = 1 / FRAME_S        # the frame spectrum's resolution: carriers are placed on it


def snap25(freq: float) -> float:
    """A frequency on the 25 Hz grid of a 40 ms frame's spectrum, where the
    modulator places the vision carrier (and a multiple of the 6.25 Hz the
    phasor tables want)."""
    return round(freq / BIN_HZ) * BIN_HZ


def video_rate(fs: float) -> float:
    """The rate the receiver's picture comes out at: the radio's own, or
    twice it where the radio's real Nyquist cannot hold 3 MHz of picture."""
    return 2 * fs if fs < 7.5e6 else fs


# ============================================================ the plan
# Below this rate 405 lines go without sound (Brian, 5 October 2026). The
# nano's bursts at 5 MS/s put the sound carrier's image at -13.4 dBc, 0.7
# MHz above the channel (docs/atv-research.md 21): System A's sound sits at
# 0.42 fs there, in the transition band of the half-band that interpolates
# it to the DAC, and no placement of the LO moves it far enough in from
# fs/2 - with sound the signal is 4.3 MHz wide, and 5 MS/s carries 4.75.
# Without sound the channel is 3.75 MHz wide, its edges are the picture's
# weakest parts (the top of the vestige, the finest detail), and the AD936x
# model puts every image under -68 dBc (21a). At 6.25 MS/s the sound fits
# where its image is under the limit (_placement). Receive follows the same
# plan, since both ends run one rate: a 405 receiver at 5 MS/s has no sound
# channel either.
SOUND_MIN_FS = 6.25e6

# The level an out-of-channel image must stay under (modem.IMAGE_LIMIT_DB:
# 625 lines are held to the same).
IMAGE_LIMIT_DB = modem.IMAGE_LIMIT_DB


def plan(fs: float, video_bw: float = VIDEO_BW, vsb: float = VSB, sound_offset: float = SOUND_OFFSET) -> dict:
    """What fits at this sample rate, in am_plan's shape: {"carrier": Hz
    from the LO, "video_bw", "sound": Hz from the LO or None, "colour":
    False, "fits", "why", "occupied": (lo, hi) Hz from the vision carrier,
    "width", "format": "405", "videoRate", "edgeDb", "txImage",
    "soundWhy": why there is no sound, or ""}.

    The occupied band - sound carrier to the top of the vestige, 4.3 MHz -
    is centred in the complex band unless that leaves an AD936x's image of
    a carrier above IMAGE_LIMIT_DB (_placement). `edgeDb` is what the
    radios' fixed filters take off the sound through a transmitter and a
    receiver, before the modems put it back. Below SOUND_MIN_FS there is no
    sound carrier, and the plan says why."""
    half = modem.USABLE * fs / 2
    why = []
    sound = None
    vbw = video_bw
    sound_why = ""
    if sound_offset and fs < SOUND_MIN_FS - 1:
        img = tx_image(fs, *_centred_sound(vsb, sound_offset))
        sound_why = (f"no sound at {fs / 1e6:g} MS/s: a Pluto would put an image of the sound carrier "
                     f"{-img['db']:.0f} dB under it, outside the channel (14 dB measured on 5 October 2026); "
                     f"{SOUND_MIN_FS / 1e6:g} MS/s or more carries the sound")
        why.append(sound_why)
        sound_offset = 0.0
    if sound_offset and vsb - (sound_offset - SOUND_EDGE) <= 2 * half:
        occ = (sound_offset - SOUND_EDGE, vsb)
        vbw = min(video_bw, -sound_offset - 0.3e6)
    else:
        if sound_offset:
            why.append(f"no room for the sound carrier {abs(sound_offset) / 1e6:g} MHz below vision at "
                       f"{fs / 1e6:g} MS/s")
        vbw = min(video_bw, 2 * half - vsb)
        occ = (-vbw, vsb)
    carrier = snap25(-(occ[0] + occ[1]) / 2.0)
    if sound_offset and occ[0] < -vbw:
        carrier = _placement(fs, carrier, occ, sound_offset, half)
        sound = modem.snap(carrier + sound_offset)
    if vbw < video_bw - 1:
        why.append(f"video limited to {vbw / 1e6:.2f} MHz at {fs / 1e6:g} MS/s (System A: {video_bw / 1e6:g} MHz)")
    why.append("405 lines are monochrome: System A never carried colour")
    fits = vbw >= MIN_VIDEO
    if not fits:
        why.append(f"under {MIN_VIDEO / 1e6:g} MHz of video: not a usable picture")
    edge_db = 0.0
    if sound is not None:
        tx, rx = ad936x.Response("tx", fs, fs), ad936x.Response("rx", fs, fs)
        edge_db = float(-20 * np.log10(tx(sound) * rx(sound)))
    return {"carrier": carrier, "video_bw": vbw, "sound": sound, "colour": False, "fits": fits, "why": why,
            "occupied": occ, "width": occ[1] - occ[0], "format": "405", "videoRate": video_rate(fs),
            "edgeDb": edge_db, "txImage": tx_image(fs, sound, carrier), "soundWhy": sound_why}


def _centred_sound(vsb: float, sound_offset: float) -> tuple[float, float]:
    """(sound, vision) Hz from the LO with the 4.3 MHz signal centred, as it
    would be if the rate carried sound: for saying what leaving it off
    avoids."""
    carrier = snap25(-((sound_offset - SOUND_EDGE) + vsb) / 2.0)
    return modem.snap(carrier + sound_offset), carrier


def _placement(fs: float, carrier: float, occ: tuple, sound_offset: float, half: float) -> float:
    """Where the vision carrier goes, with sound. Centred (`carrier`) unless
    an AD936x's image of a carrier is then above IMAGE_LIMIT_DB; then the
    two carriers go equally far either side of the LO, if the band still
    fits there: their images are then equally deep in the half-bands' stop
    band, which is as deep as both can be at once. At 6.25 MS/s centred
    leaves the sound at -2.1 MHz (0.34 fs) and its image at -30 dBc; with
    vision at +1.75 and sound at -1.75 both images come out at -45 dBc, and
    the vestige's top, at 0.4 fs, is weak enough that its image is lower
    still (docs/atv-research.md 21a). At 8 MS/s and above centred is already
    under the limit and is kept: it is what was measured on air."""
    if tx_image(fs, modem.snap(carrier + sound_offset), carrier)["db"] <= IMAGE_LIMIT_DB:
        return carrier
    sym = snap25(-sound_offset / 2.0)
    if sym + occ[1] <= half and sym + occ[0] >= -half:
        return sym
    return carrier


def tx_image(fs: float, sound: float | None, carrier: float) -> dict:
    """The strongest image an AD936x transmitter makes of the vision or the
    sound carrier: {"offset": where, in Hz from the vision carrier, "db": how
    far under the carrier it is an image of (negative), "of": "sound" or
    "vision"}. The filters that interpolate to the DAC are half-bands, so a
    carrier near fs/2 has its image, at fs minus it, in their transition
    band, barely filtered. On 5 October 2026 the nano's 405-line bursts at
    5 MS/s put the sound carrier's image at 1256.48 MHz, 0.7 MHz above the
    channel, 14.2 dB under the sound carrier (13.4 under the vision
    carrier); this model says 13.0. The modems' edge correction lifts a
    carrier and its image together, so the ratio is the filters' alone. The
    two carriers' lines came out within 0.8 dB of each other on air, so dB
    under either is near enough dB under the vision carrier. A band-pass
    filter after the radio cannot take an image out: it is inside the band,
    beside the signal. The picture's own images are weaker than the
    carriers' wherever the carriers are placed here: tests/test_sysa.py
    builds them whole, through the chip's interpolation."""
    r = ad936x.Response("tx", fs, fs)
    best = None
    for of, f in (("vision", carrier), ("sound", sound)):
        if f is None:
            continue
        img, db = r.image(f)
        if best is None or db > best["db"]:
            best = {"offset": img - carrier, "db": db, "of": of}
    return best


def tx_image_warning(plan: dict, kind: str, fs: float, freq: float) -> str | None:
    """A sentence for the transmit panel about an AM transmission's images
    outside the channel, or None: when the transmitter in use puts one
    above IMAGE_LIMIT_DB, or when 405 lines' sound has been left off to keep
    them under it (the panel says why, as the plan card does). An AD936x (a
    Pluto) is modelled by the plan's txImage, for any AM plan: tx_image for
    405 lines, measured on air and decided (docs/atv-research.md 21, 21a);
    modem.tx_image for 625, whose sound image at 10 MS/s was about -25 dB
    until the carriers moved and is still -11 at 8 MS/s, where no placement
    helps (24). A HackRF by its converter's
    hold and its baseband filter (devices/hackrf.py), at the filter its
    driver sets for this plan, for any AM plan: it has no interpolation
    filter, so 625 lines at 10 MS/s image as 405 did at 8 (the sound
    carrier's about 3 dB under it, 4 MHz below vision), and NICAM's carrier
    with them. FM plans place no carriers, but a subcarrier's sidebands near
    fs/2 image all the same (modem.fm_tx_image): _fm_image_warning."""
    if "carrier" not in plan:
        return _fm_image_warning(plan, kind, fs, freq)
    a405 = plan.get("format") == "405"
    if a405 and plan.get("soundWhy"):
        return "405 lines go without sound " + plan["soundWhy"].removeprefix("no sound ")
    if kind == "pluto":
        im = plan.get("txImage")
        if not im or im["db"] <= IMAGE_LIMIT_DB:
            return None
        lo, hi = plan["occupied"]
        where = "on the picture" if lo <= im["offset"] <= hi else "outside the channel"
        under = "the vision carrier" if im["of"] == "NICAM" else "it"
        return (f"At {fs / 1e6:g} MS/s a Pluto's image of the {im['of']} carrier comes out at "
                f"{(freq + im['offset']) / 1e6:.2f} MHz, {where}, about {-im['db']:.0f} dB under {under}; "
                f"a band-pass filter cannot remove it")
    if kind == "hackrf":
        from .devices import hackrf
        bw = hackrf.filter_for(fs, modem.tx_span(plan))
        worst = None
        for f in (plan["carrier"], plan.get("sound"), plan.get("nicam")):
            if f is None:
                continue
            at, db = hackrf.image(fs, f, bw)
            if worst is None or db > worst[1]:
                worst = (at - plan["carrier"], db)
        if worst is None or worst[1] <= IMAGE_LIMIT_DB:
            return None
        return (f"At {fs / 1e6:g} MS/s, through its {bw / 1e6:g} MHz baseband filter, a HackRF's images of the "
                f"carriers come out {fs / 1e6:g} MHz from them, the strongest at {(freq + worst[0]) / 1e6:.2f} "
                f"MHz about {-worst[1]:.0f} dB under its carrier. 20 MS/s puts them more than "
                f"{-IMAGE_LIMIT_DB:.0f} dB under; a band-pass filter cannot remove them")
    return None


def _fm_image_warning(plan: dict, kind: str, fs: float, freq: float) -> str | None:
    """tx_image_warning for an FM plan. A Pluto by the plan's txImage, which
    the plan card shows too; a HackRF by modem.fm_tx_image on its own
    model, which only the panel can say, since only the engine knows which
    radio transmits. The figure is dBc in 100 kHz, not dB under a carrier:
    an FM signal's power is spread, and its spurious emissions are
    measured that way (docs/atv-research.md 24)."""
    if kind == "pluto":
        im, who = plan.get("txImage"), "a Pluto's"
    elif kind == "hackrf":
        im = modem.fm_tx_image(fs, plan.get("sub"), plan.get("subIndex", 0.0), plan.get("nicam") is not None,
                               radio="hackrf")
        who = "a HackRF's"
    else:
        return None
    if not im or im["db"] <= IMAGE_LIMIT_DB:
        return None
    lo, hi = plan["occupied"]
    where = "on the picture" if lo <= im["offset"] <= hi else "outside the channel"
    return (f"At {fs / 1e6:g} MS/s {who} image of {modem.fm_image_of(im)} sidebands comes out at "
            f"{(freq + im['offset']) / 1e6:.2f} MHz, {where}, about {-im['db']:.0f} dBc in 100 kHz; "
            f"a band-pass filter cannot remove it")


# ============================================================ the encoder
@lru_cache(maxsize=4)
def sync_frame(n: int) -> np.ndarray:
    """405 x n: blanking with every sync pulse in place, at n samples a line
    (pal.sync_frame's method, System A's slot table)."""
    fs_i = n * FH
    t = np.arange(n) / fs_i * 1e6
    slots = lines.a_slots()
    width = {"h": lines.A_HSYNC_US, "b": lines.A_BROAD_US}
    cache: dict = {}
    out = np.zeros((STD.lines, n), np.float32)
    for line in range(1, STD.lines + 1):
        nxt = slots[line % STD.lines + 1][0]
        key = (slots[line], nxt)
        if key not in cache:
            row = np.zeros(n)
            for slot, kind in enumerate(key[0]):
                if kind:
                    row += pal._pulse(t, slot * lines.A_HALF_US, width[kind], lines.A_SYNC_EDGE_US)
            row += pal._pulse(t, lines.A_LINE_US, width[nxt], lines.A_SYNC_EDGE_US)
            cache[key] = (pal.SYNC_TIP * row).astype(np.float32)
        out[line - 1] = cache[key]
    return out


def rows_to(img: np.ndarray, rows: int) -> np.ndarray:
    """Resample an image's rows to `rows` (576 -> 378 for a 625-line
    picture): a [1 2 1] smoothing, then linear interpolation. Taking the
    nearest rows aliased the card's castellations and small text into
    stripes."""
    h = img.shape[0]
    if h == rows:
        return img
    if rows < h:
        p = np.concatenate([img[:1], img, img[-1:]], axis=0)
        img = 0.25 * p[:-2] + 0.5 * p[1:-1] + 0.25 * p[2:]
    y = np.clip((np.arange(rows) + 0.5) * (h / rows) - 0.5, 0, h - 1)
    i0 = np.floor(y).astype(np.int64)
    i1 = np.minimum(i0 + 1, h - 1)
    f = (y - i0).astype(np.float32).reshape((-1,) + (1,) * (img.ndim - 1))
    return img[i0] * (1 - f) + img[i1] * f


class Encoder:
    """RGB pictures -> 405-line System A composite.

    `encode(rgb, frame_no, audio)` returns one frame as 405 x LINE_N float32
    volts at the line-locked rate `fs_i` (7.59 MS/s), as pal.py's are (sync
    -0.3, blanking and black 0, white 0.7; Report 624-2 Table I: sync -43 %
    of white). Not band-limited: the modulator's vestigial filter does that
    in the same multiply that brings the frame to the radio's rate. `fs` is
    the radio's rate, kept for the uniform interface. Sound is added by the
    modulator."""
    audio_in_video = False

    def __init__(self, fs: float, colour: bool = False):
        self.fs = float(fs)
        self.colour = False                      # System A is monochrome
        self.n = LINE_N
        self.fs_i = self.n * FH
        self.first, self.count = STD.active_samples(self.fs_i)
        self.sync = sync_frame(self.n)
        t = np.arange(self.n) / self.fs_i * 1e6
        self.win = pal._pulse(t, lines.A_ACTIVE_START_US, lines.A_ACTIVE_US, lines.A_PIC_EDGE_US).astype(np.float32)
        second = pal._smooth_step(t - lines.A_HALF_US, lines.A_PIC_EDGE_US).astype(np.float32)
        self.h_top, self.h_bottom = second, (1.0 - second).astype(np.float32)
        self.lines_idx, self.rows = STD.row_map

    def encode(self, rgb: np.ndarray, frame_no: int = 0, audio=None) -> np.ndarray:
        rgb = np.asarray(rgb)
        if rgb.dtype == np.uint8:
            rgb = rgb.astype(np.float32) / 255.0
        rgb = np.clip(rgb.astype(np.float32), 0.0, 1.0)
        rgb = rows_to(rgb, STD.rows)
        rgb = pal._resample_rows(rgb, self.count)
        y = pal.WHITE * (pal.KR * rgb[..., 0] + pal.KG * rgb[..., 1] + pal.KB * rgb[..., 2])
        full = np.zeros((STD.lines, self.n), np.float32)
        full[self.lines_idx, self.first:self.first + self.count] = y[self.rows]
        full *= self.win
        full[STD.half_top - 1] *= self.h_top
        full[STD.half_bottom - 1] *= self.h_bottom
        return self.sync + full


# ============================================================ transmit
def _vsb_response(video_bw: float, vsb: float):
    """The transmitted sidebands about the vision carrier (Hz): the main one
    *below* it to `video_bw`, the vestige above it whole to 0.5 MHz and gone
    by 0.75 (Report 624-2 Table III item 5; item 6, its attenuation, is "not
    specified" for A)."""
    def h(g):
        main = taper(-g, video_bw, VIDEO_EDGE)
        vest = taper(g, vsb - 0.125e6, 0.25e6)
        return np.where(g <= 0, main, vest)
    return h


class AudioLowpass:
    """Audio band-limited to AUDIO_BW at 48 kHz, streaming: a 10 kHz AM
    sound carrier occupies +-10 kHz, which is what the plan allows."""

    def __init__(self, ntaps: int = 63):
        k = np.arange(ntaps) - (ntaps - 1) / 2
        h = np.sinc(2 * AUDIO_BW / modem.AUDIO_RATE * k) * np.kaiser(ntaps, 6.0)
        self.h = (h / h.sum()).astype(np.float64)
        self.hist = np.zeros(ntaps - 1)

    def __call__(self, a: np.ndarray) -> np.ndarray:
        x = np.concatenate([self.hist, np.asarray(a, np.float64)])
        self.hist = x[len(x) - len(self.hist):]
        return np.convolve(x, self.h, mode="valid")


class Mod:
    """A frame from Encoder -> System A IQ at fs: positive AM-VSB vision,
    AM sound 3.5 MHz below it.

    Vision is built in the frequency domain, a frame at a time: the
    envelope (sync 0, blanking 0.3, white 1.0) at the encoder's line-locked
    rate is transformed, each output bin takes the envelope's component at
    its distance from the vision carrier times the sideband shape there
    (the spectrum of a real envelope about a carrier: its conjugate for
    the lower side), and one inverse transform at the radio's rate gives
    the frame. The carrier sits on the frame's 25 Hz bin grid, so it is
    exact; frames join seamlessly because each ends on line 405's front
    porch and begins with line 1's broad pulse, the same in every frame.
    `tx_shape`: the transmitting radio's own response (devices/ad936x.py),
    undone exactly in the same multiply - AM is linear - and in the sound
    carrier's level."""

    def __init__(self, fs: float, video_bw: float = VIDEO_BW, vsb: float = VSB, sound_offset: float = SOUND_OFFSET,
                 sound_db: float = SOUND_DB, depth: float = SOUND_DEPTH, tx_shape=None):
        self.fs = float(fs)
        m = self.fs * FRAME_S
        if abs(m - round(m)) > 1e-6:
            raise ValueError(f"{fs / 1e6:g} MS/s is not a whole number of samples in a 40 ms frame")
        self.m = int(round(m))
        self.plan = plan(fs, video_bw, vsb, sound_offset)
        c = self.plan["carrier"]
        self.L = STD.lines * LINE_N
        fo = np.fft.fftfreq(self.m, 1 / self.fs)
        g = fo - c
        h = _vsb_response(self.plan["video_bw"], vsb)(g) * ad936x.correction(tx_shape, fo)
        keep = np.flatnonzero(h > 1e-6)
        self.bins = keep                                        # output bins that carry vision
        k = np.rint(g[keep] / BIN_HZ).astype(np.int64)          # the envelope's bin for each
        if np.any(np.abs(k) > self.L // 2):
            raise ValueError("the picture is wider than the encoder's own rate carries")
        self.src = np.abs(k)
        self.conj = k < 0
        self.w = (h[keep] * (self.m / self.L)).astype(np.complex64)
        self.sound_amp = 0.0
        if self.plan["sound"] is not None:
            self.sound_amp = 10 ** (sound_db / 20.0) * float(ad936x.correction(tx_shape, self.plan["sound"]))
        self.depth = depth
        self.lp = AudioLowpass()
        self.last = 0.0
        self.pos = 0
        # Peak envelope: white (1.0) and the sound carrier at the crest of its
        # modulation, at the same instant.
        self.scale = 0.95 / (1.0 + self.sound_amp * (1 + depth))

    def __call__(self, comp: np.ndarray, audio: np.ndarray | None = None) -> np.ndarray:
        comp = np.asarray(comp, np.float32).reshape(-1)
        if len(comp) != self.L:
            raise ValueError(f"System A frames are {STD.lines} x {LINE_N} samples, not {len(comp)}")
        n = self.m
        env = np.clip(A_BLANK + A_SLOPE * comp, 0.0, 1.05)
        E = np.fft.rfft(env)[self.src]
        E[self.conj] = np.conj(E[self.conj])
        Y = np.zeros(n, np.complex64)
        Y[self.bins] = E * self.w
        y = np.fft.ifft(Y).astype(np.complex64)
        if self.sound_amp:
            if audio is None:
                audio = np.zeros(max(1, int(round(n * modem.AUDIO_RATE / self.fs))), np.float32)
            a = np.clip(self.lp(audio), -1.0, 1.0)
            # From 48 kHz to fs by straight lines, continuing from the last
            # block's final sample: the images of a 10 kHz band round 48 kHz
            # come out over 60 dB down.
            pts = np.concatenate([[self.last], a])
            self.last = float(pts[-1])
            x = np.arange(1, n + 1) * (len(a) / n)
            a_fs = np.interp(x, np.arange(len(pts)), pts).astype(np.float32)
            env_s = np.float32(self.sound_amp) * (1.0 + np.float32(self.depth) * a_fs)
            y += env_s * modem._phasor(self.fs, self.plan["sound"], n, self.pos)
        self.pos += n
        return (y * np.complex64(self.scale)).astype(np.complex64)


# ============================================================ receive
class SoundAm(modem.SoundDemod):
    """AM sound: the channel's envelope over the carrier's own mean level,
    so the audio's level does not follow the received signal's."""

    def __init__(self, fs: float, centre: float, dec: int, depth: float = SOUND_DEPTH):
        super().__init__(fs, centre, dec, 1.0, tau=0.0, bw=4 * AUDIO_BW)
        self.depth = depth
        self.level = None

    def _detect(self, y: np.ndarray) -> np.ndarray:
        env = np.abs(y).astype(np.float64)
        m = float(env.mean())
        # The carrier's level, smoothed over blocks so a block's mean does
        # not step the audio 25 times a second.
        self.level = m if self.level is None else 0.8 * self.level + 0.2 * m
        return (env / max(self.level, 1e-12) - 1.0) / self.depth


class Demod(modem.AmVsbDemod):
    """System A receive: a filter with the Nyquist slope across the vision
    carrier, synchronous detection with the carrier's phase from the mean
    of each line (positive modulation has no carrier at the sync tips to
    take it from), the picture the right way up - sync low, as every
    demodulator here returns it - and the AM sound.

    Below 7.5 MS/s the picture comes out at twice the radio's rate
    (`video_rate`): the IQ is interpolated by two inside the receive filter
    (zeros between samples, the images beyond +-fs/2 cut by the same taps),
    so the 3 MHz picture is a real signal with room under its Nyquist."""

    NYQ = 0.5e6                 # half-width of the Nyquist slope: where the vestige is whole

    def __init__(self, fs: float, video_bw: float = VIDEO_BW, vsb: float = VSB, sound_offset: float = SOUND_OFFSET,
                 depth: float = SOUND_DEPTH, rx_shape=None):
        self.fs = float(fs)
        self.up = int(round(video_rate(self.fs) / self.fs))
        self.out_fs = self.fs * self.up
        self.plan = plan(fs, video_bw, vsb, sound_offset)
        self.rx_shape = rx_shape
        self.vbw = self.plan["video_bw"]
        self.sound_offset = sound_offset
        self.depth = depth
        self.carrier = self.plan["carrier"]
        self.filt = None
        self.snd = None
        self.coherent = True
        self.pos = 0
        self.last_tip = None            # (output index, total carrier phase) of the last line's mean
        self.chunk = max(64, int(round(self.out_fs / FH)))
        self._design(self.carrier)

    def _design(self, carrier: float):
        vbw, c, fs = self.vbw, carrier, self.fs

        def h(f):
            g = f - c
            nyq = np.clip(0.5 - g / (2 * self.NYQ), 0.0, 1.0)      # 1 below the carrier, 0.5 at it
            main = taper(-g, vbw, VIDEO_EDGE)
            r = np.where(g > -self.NYQ, nyq, main) * ad936x.correction(self.rx_shape, f)
            # Interpolating: only the radio's own band, the images cut.
            return np.where(np.abs(f) < 0.5 * fs, r * self.up, 0.0)

        old = self.filt
        self.filt = Ols(design(h, self.out_fs, 255), shift=c, fs=self.out_fs)
        if old is not None and len(old.hist) == len(self.filt.hist):
            self.filt.hist = old.hist
            self.filt.pos = old.pos
        elif old is not None:
            self.filt.pos = old.pos
        self.carrier_designed = carrier
        # A carrier found far from the plan's place (a 625-line station
        # tracked while the receiver still expects 405) can put the sound's
        # channel outside the band: keep the last one then.
        if self.plan["sound"] is not None and abs(c + self.sound_offset) < 0.49 * fs - 2 * AUDIO_BW:
            dec = max(1, int(fs // 250e3))
            snd = SoundAm(fs, c + self.sound_offset, dec, self.depth)
            if self.snd is not None:
                snd.level = self.snd.level
            self.snd = snd

    def __call__(self, iq: np.ndarray) -> np.ndarray:
        iq = np.asarray(iq, np.complex64)
        if self.up > 1:
            x = np.zeros(self.up * len(iq), np.complex64)
            x[::self.up] = iq
            iq = x
        y = self.filt(iq)
        n = len(y)
        pos = self.pos
        self.pos += n
        if not self.coherent or n == 0:
            return np.abs(y)
        out = self._coherent_mean(y, pos)
        return np.abs(y) if out is None else out

    def _coherent_mean(self, y: np.ndarray, pos: int) -> np.ndarray | None:
        """The carrier phase from each line-long chunk's mean, unwrapped from
        chunk to chunk (and from the last block's), interpolated across, and
        the in-phase part taken - as the 625-line detector does with its
        sync tips. A chunk a line long holds every part of a line once, so
        its mean is the carrier times the line's mean level, which is at
        least blanking's 30 % for most of it."""
        n = len(y)
        C = self.chunk
        m = n // C
        if m < 2:
            return None
        rate = self.out_fs
        w = 2 * np.pi * ((self.carrier - self.filt.shift) / rate)
        w0 = (w * pos) % (2 * np.pi)
        # The residual - what tracking found less the filter's shift, up to
        # the 20 kHz at which the filter is redesigned - is taken out in
        # steps of STEP samples (at 20 kHz, 0.1 rad within a step) before
        # each chunk is summed: a phasor per step, not per sample, which
        # was a third of this demodulator's time. (Summing first and
        # correcting the sums was tried: right while the residual is under
        # an FFT bin, but at 14 kHz a line turns 9 rad and its sum cancels -
        # the simulator's 405 station, 14.5 kHz off, held 47 % of its lines.)
        D = self.STEP
        L = m * C
        q = -(-L // D)
        g = np.arange(q, dtype=np.float64) * D + (D - 1) / 2
        z = np.zeros(q * D, np.complex64)
        z[:L] = y[:L]
        z = (z.reshape(q, D) * modem._cis(-(w0 + w * g))[:, None]).reshape(-1)[:L]
        sums = z.reshape(m, C).sum(axis=1)
        ph = np.angle(sums)
        centre = (np.arange(m) + 0.5) * C - 0.5
        if self.last_tip is not None:
            i_prev, tot_prev = self.last_tip
            prev = (tot_prev - self.filt.shift * 2 * np.pi / rate * i_prev - w * i_prev) % (2 * np.pi)
            centre = np.concatenate([[i_prev - pos], centre])
            ph = np.concatenate([[prev], ph])
        ph = np.unwrap(ph)
        i_last = float(centre[-1] + pos)
        self.last_tip = (i_last, float((w0 + w * centre[-1] + ph[-1] + 2 * np.pi * self.filt.shift / rate
                                        * i_last) % (2 * np.pi)))
        D = self.STEP
        mm = -(-n // D)
        g = np.arange(mm, dtype=np.float64) * D + (D - 1) / 2
        rot = modem._cis(-(w0 + w * g + np.interp(g, centre, ph)))
        out = np.empty(mm * D, np.float32)
        full = (n // D) * D
        z = y[:full].reshape(-1, D) * rot[:full // D, None]
        out[:full] = z.real.reshape(-1)
        if full < n:
            out[full:n] = (y[full:] * rot[-1]).real
        return out[:n]

    def audio(self, iq: np.ndarray) -> np.ndarray:
        return self.snd(iq) if self.snd is not None else np.zeros(0, np.float32)


# ============================================================ what is on the air
def _fold(env: np.ndarray, period: float, k: int) -> np.ndarray:
    n = int(math.ceil(period))
    st = np.round(np.arange(k) * period).astype(np.int64)
    return env[st[:, None] + np.arange(n)].mean(axis=0)[: int(round(period))]


def _local_corr(fold: np.ndarray, t: np.ndarray) -> float:
    """narrow._local_corr: the best normalised correlation of template `t`
    with any stretch of a line-long profile, circularly."""
    n, m = len(fold), len(t)
    t = t - t.mean()
    w = np.lib.stride_tricks.sliding_window_view(np.concatenate([fold, fold[:m]]), m)[:n]
    wz = w - w.mean(axis=1, keepdims=True)
    c = (wz @ t) / (np.linalg.norm(wz, axis=1) * np.linalg.norm(t) + 1e-12)
    return float(c.max())


# detect()'s thresholds, from measurement (tests/test_sysa.py, Detect).
LINE_MIN = 0.15
LINE_RATIO = 3.0


def detect(iq: np.ndarray, fs: float, carrier: float) -> tuple[str | None, dict]:
    """Which line standard an AM signal carries, from ~40 ms of IQ with its
    vision carrier at `carrier` Hz from the LO: "405", "pal" (625 lines) or
    None. By line rate *and* polarity together: the envelope round the
    vision carrier, folded at 98.77 us and at 64 us, is correlated with a
    9 us dip (System A: positive modulation, sync at no carrier) and a
    4.7 us peak (625: negative, sync at full carrier). Folded at the wrong
    line rate the other standard's syncs smear across the whole line and
    leave nothing to match, and the polarity is a second, independent
    difference. FM is narrow.detect()'s and untouched by this."""
    x = np.asarray(iq, np.complex64)
    n = len(x)
    if n < int(0.03 * fs):
        return None, {}
    k = np.arange(n, dtype=np.float64)
    x = x * np.exp(-2j * np.pi * ((k * carrier / fs) % 1.0)).astype(np.complex64)
    X = np.fft.fft(x)
    f = np.fft.fftfreq(n, 1 / fs)
    env = np.abs(np.fft.ifft(X * taper(np.abs(f), 0.6e6, 0.2e6))).astype(np.float64)
    feats = {}
    level = float(env.mean()) + 1e-12
    for name, std, sign, width in (("405", lines.SYSTEM_A, -1.0, lines.A_HSYNC_US),
                                   ("pal", lines.PAL625, 1.0, pal.HSYNC_US)):
        period = fs / std.fh
        kk = min(64, int((n - period) // period))
        fold = _fold(env, period, kk)
        w = max(1, int(round(width * 1e-6 * fs)))
        c = np.cumsum(np.concatenate([[0.0], fold, fold[:w]]))
        box = (c[w:w + len(fold)] - c[:len(fold)]) / w
        ext = box.min() if sign < 0 else box.max()
        feats[name] = float(sign * (ext - np.median(fold)) / level)
    a, b = feats["405"], feats["pal"]
    if a > LINE_MIN and a > LINE_RATIO * b:
        return "405", feats
    if b > LINE_MIN and b > LINE_RATIO * a:
        return "pal", feats
    return None, feats
