"""The video formats: standard PAL and the two non-standard narrow ones.

A USB Pluto transmits ~5 MS/s (docs/atv-research.md 12): about +-2.4 MHz of
complex baseband. Standard PAL's 4.43 MHz colour and FM-ATV's 6 MHz sound
cannot be in it, so the narrow FM profile was a mono picture with no
sound. Brian (GM4BRI) asked for colour and sound in that channel: "try them
all, and build the ones that work into the app". Three were tried; these
two worked and are here, each in its own module (the third, sound in the
sync pulses, was rejected - DESIGN.md):

- ``"lowpal"`` - **lowered-subcarrier PAL** (ohsatv/lowpal.py): PAL colour on
  a 1.6 MHz subcarrier, luma to 1.1 MHz, FM sound subcarrier at 2.2 MHz.
- ``"mac"`` - **Mini-MAC** (ohsatv/mac.py): data, chroma and luma one after
  the other on each line, sound digital in the line's first 11 us.

None of them is a standard: only another OpenHamShack ATV decodes them
(``NOT_STANDARD``). ``"pal"`` is the default everywhere.

One more format lives here because the engine treats every format alike,
though it is the opposite of narrow: ``"405"``, the British 405-line
System A of 1936-1985 (ohsatv/sysa.py), on AM. It *is* a standard - a vintage
set receives it - so NOT_STANDARD never goes with it.

What was measured and which were kept: DESIGN.md "Narrow formats" and
docs/narrow-eval.md (tools/narrow_eval.py prints the tables).

Each format gives the engine the same five things - a plan, an encoder,
a modulator, a demodulator and a decoder - so radio.py, the simulator and
the loop builder treat every format alike. Encoders take the frame's
48 kHz audio (`audio_in_video`: the digital formats carry it in the
picture; the others ignore it and the modulator adds a subcarrier);
decoders return frames that may carry "audio" (48 kHz) for the digital
ones.
"""
from __future__ import annotations

import numpy as np

from . import lines, lowpal, mac, modem, pal, sysa
from .decoder import VideoDecoder

FORMATS = ["pal", "lowpal", "mac", "405"]
# What the program offers: the ones that worked (DESIGN.md "Narrow
# formats"), and 405 lines. Sound in syncs was evaluated and rejected; its
# code is gone, its figures are in docs/narrow-eval.md.
OFFERED = ["pal", "lowpal", "mac", "405"]
LABELS = {
    "pal": "PAL",
    "lowpal": "Narrow colour PAL (1.6 MHz colour, 2.2 MHz sound)",
    "mac": "Mini-MAC (colour + digital sound)",
    "405": "405 lines (System A, mono, AM sound)",
}
SHORT = {"pal": "PAL", "lowpal": "Low-PAL", "mac": "Mini-MAC", "405": "405"}
NOT_STANDARD = "OpenHamShack ATV only: not compatible with standard ATV receivers"
SYSTEMS = {"pal": ("am", "fm"), "lowpal": ("fm",), "mac": ("fm",), "405": ("am",)}
NARROW = ("lowpal", "mac")
# Offered (suggested in the plan card and the USB profile's sentence) when
# the rate is this or lower: above it standard PAL has room for colour and
# sound. They still work above it - each is defined in time, not samples,
# so an Ethernet radio at 16 MS/s receives a USB Pluto's 5 MS/s Mini-MAC -
# and they apply wherever chosen.
NARROW_MAX_FS = 6_250_000

# Mini-MAC fills the deviation range for most of every line (data, chroma
# and luma all swing +-0.5), where a PAL picture spends much of its time
# near black with only sync reaching the bottom; at the same peak-to-peak
# deviation its spectrum is wider. This scales the narrow profile's
# deviation so Mini-MAC's measured 99 % occupied bandwidth matches the mono
# picture's (docs/narrow-eval.md).
MAC_DEV_SCALE = 1.0
# Lowered-subcarrier PAL carries video to 1.98 MHz and a subcarrier at
# 2.2 MHz in a channel whose mono picture stops at 1.6: its deviation is
# reduced so the occupied bandwidth matches the mono picture's.
LOWPAL_DEV_SCALE = 1.0
LOWPAL_SUB_INDEX = 0.08


def is_narrow(fmt: str) -> bool:
    """The non-standard formats, which only another OpenHamShack decodes."""
    return fmt in NARROW


def fm_params(fs: float, fmt: str, p: dict) -> dict:
    """The FmMod / FmDemod keywords for a format at this rate."""
    dev = float(p.get("fmDeviation", 7e6))
    vbw = float(p.get("fmVideoBw", 5.0e6))
    kw = {"deviation": dev, "video_bw": vbw, "preemph": bool(p.get("fmPreemph", True)),
          "sub": float(p.get("fmSub", 6.0e6)), "sub_index": float(p.get("fmSubIndex", 0.07)),
          "invert": bool(p.get("fmInvert", False))}
    if fmt == "mac":
        # Flat: CCIR 405 lifts high frequencies 14 dB over low ones, and
        # Mini-MAC's data and compressed chroma are full-amplitude high
        # frequencies - with emphasis they would overdeviate, and measured
        # without it the picture and the sound were better at every CNR.
        kw.update(deviation=dev * MAC_DEV_SCALE, preemph=False, sub=0.0)
    elif fmt == "lowpal":
        kw.update(deviation=dev * LOWPAL_DEV_SCALE, video_bw=lowpal.TOP + 0.04e6, sub=lowpal.SUB,
                  sub_index=LOWPAL_SUB_INDEX, sub_dev=lowpal.SUB_DEV, sub_bw=100e3, edge=0.12e6, ntaps=255)
    return kw


def fm_plan(fs: float, fmt: str, p: dict) -> dict:
    kw = fm_params(fs, fmt, p)
    if fmt == "pal":
        return modem.fm_plan(fs, kw["deviation"], kw["video_bw"], kw["sub"], kw["sub_index"],
                             nicam_on=bool(p.get("nicam")))
    base = modem.fm_plan(fs, kw["deviation"], kw["video_bw"], 0.0, 0.0)
    why = [w for w in base["why"] if "colour" not in w]
    plan = dict(base, format=fmt, why=why, nicamRx=False)
    if fmt == "mac":
        plan.update(sound="digital", colour=True)
        why.append("Mini-MAC: colour and digital sound (15.6 kHz samples) in the same channel")
    elif fmt == "lowpal":
        # Carson's rule on the video is the wrong measure here - video and
        # the subcarrier reach ~2.2 MHz, nearly the whole half-band, while
        # the deviation is small; the occupied band is the subcarrier's
        # first sidebands, or Carson's on the luminance, whichever is wider.
        half = max(kw["deviation"] / 2 + lowpal.LUMA_BW + lowpal.LUMA_EDGE / 2,
                   lowpal.SUB + lowpal.SUB_DEV + 15e3)
        fits = 2 * half <= modem.USABLE * fs and fs / 2 > lowpal.SUB + 0.15e6
        why = [] if fits else [f"needs {2 * half / 1e6:.1f} MHz but {fs / 1e6:g} MS/s carries "
                               f"{modem.USABLE * fs / 1e6:.1f} MHz"]
        why.append("colour on a 1.6 MHz subcarrier, luma to 1.1 MHz, FM sound subcarrier at 2.2 MHz")
        # The base plan had no subcarrier; this one does, at 0.44 fs on a
        # USB Pluto's 5 MS/s, where its image is 37 dB down (modem.fm_tx_image).
        im = modem.fm_tx_image(fs, lowpal.SUB, kw["sub_index"], False)
        iwhy = modem.fm_image_why(im, (-half, half))
        if iwhy:
            why.append(iwhy)
        plan.update(video_bw=lowpal.LUMA_BW, sub=lowpal.SUB, colour=True, carson=2 * half, fits=fits,
                    occupied=(-half, half), width=2 * half, sound="subcarrier", why=why,
                    subIndex=kw["sub_index"], txImage=im)
    plan["why"] = plan["why"] + [NOT_STANDARD]
    return plan


def am_plan(fs: float, fmt: str, p: dict) -> dict:
    if fmt == "405":
        # System A's own numbers: the AM-VSB settings are System I's. No
        # NICAM: it rides on 625-line PAL (System I), as System A never did.
        pl = dict(sysa.plan(fs), nicam=None, nicamRx=False, nicamWhy="")
        if p.get("nicam"):
            pl["nicamWhy"] = "no NICAM with 405 lines: NICAM-728 rides on 625-line PAL (System I)"
            pl["why"] = pl["why"] + [pl["nicamWhy"]]
        return pl
    vbw, vsb, snd = p.get("amVideoBw", 5.5e6), p.get("amVsbLower", 1.25e6), p.get("amSoundOffset", 5.9996e6)
    # AM-VSB has no narrow formats (DESIGN.md "Narrow formats").
    return modem.am_plan(fs, vbw, vsb, snd, nicam_on=bool(p.get("nicam")),
                         placement=p.get("amPlacement", "images"))


def plan(fs: float, system: str, p: dict) -> dict:
    fmt = fmt_for(system, p, fs)
    pl = am_plan(fs, fmt, p) if system == "am" else fm_plan(fs, fmt, p)
    ok, why = teletext_fit(pl, fmt)
    return dict(pl, teletext=ok, teletextWhy=why)


# Teletext (ohsatv/vbi.py) is 6.9375 Mbit/s with a spectrum running to 5
# MHz. What a mode needs to carry it was measured, not assumed
# (tools/teletext_eval.py, docs/teletext-eval.md): through the simulator's
# channel, with the modem's video filter set to each bandwidth, every
# packet arrives down to 3.5 MHz and none at 3.25 - the data's Nyquist
# frequency is 3.47 MHz, and below it the eye closes; at 3.5 the packet
# error rate rises ~2 dB of CNR earlier than at 3.75-5. So 3.75 MHz is the
# line, with that margin. Between it and 4.75 MHz the spectrum is cut
# well inside the standard's 5 MHz: our own slicer is measured to take it,
# a television's decoder has not been, and the plan says so.
TTX_MIN_BW = 3.75e6
TTX_FULL_BW = 4.75e6


def teletext_fit(pl: dict, fmt: str) -> tuple[bool, str]:
    """(whether this plan carries teletext, the sentence to show)."""
    if fmt == "405":
        # World System Teletext is defined on 625-line blanking only (EN 300
        # 706 4.1: lines 6-22 and 318-335); System A never carried it.
        return False, ("Teletext and subtitles are not sent with 405 lines: teletext is defined only for "
                       "625-line pictures (System A never carried it)")
    if fmt != "pal":
        return False, (f"Teletext and subtitles are not sent in {SHORT[fmt]}: they ride on PAL's blanked "
                       f"lines, which {SHORT[fmt]} does not have")
    vbw = float(pl["video_bw"])
    if vbw < TTX_MIN_BW:
        return False, (f"Teletext and subtitles are not sent: they need about {TTX_MIN_BW / 1e6:.2f} MHz of "
                       f"video (6.9 Mbit/s data) and this mode carries {vbw / 1e6:.2f} MHz")
    if vbw < TTX_FULL_BW:
        return True, (f"Teletext's spectrum is cut at {vbw / 1e6:.2f} MHz here (the standard's runs to 5 MHz): "
                      f"another OpenHamShack ATV decodes it, a television's decoder may not")
    return True, ""


def fmt_for(system: str, p: dict, fs: float) -> str:
    """The format actually used: the chosen one where it applies to the
    system (the narrow ones are FM, 405 lines AM), else standard PAL."""
    fmt = p.get("videoFormat", "pal")
    if fmt not in SYSTEMS or system not in SYSTEMS[fmt]:
        return "pal"
    return fmt


# --- encoders ----------------------------------------------------------------
class PalEncoder:
    """pal.Encoder with the uniform encode(rgb, k, audio) signature; the
    audio, if any, goes on a subcarrier or carrier added by the modulator."""
    audio_in_video = False

    def __init__(self, fs: float, colour: bool = True):
        self.enc = pal.Encoder(fs, colour=colour)

    @property
    def colour(self):
        return self.enc.colour

    @colour.setter
    def colour(self, v):
        self.enc.colour = v

    def encode(self, rgb, frame_no: int = 0, audio=None) -> np.ndarray:
        return self.enc.encode(rgb, frame_no)


class LowPalEncoder(PalEncoder):
    def __init__(self, fs: float, colour: bool = True):
        self.enc = lowpal.Encoder(fs, colour=colour)


class MacEncoder:
    audio_in_video = True

    def __init__(self, fs: float, colour: bool = True, chan_bw: float | None = None):
        self.enc = mac.Encoder(fs, colour=colour, chan_bw=chan_bw)
        self.colour = colour

    def encode(self, rgb, frame_no: int = 0, audio=None) -> np.ndarray:
        self.enc.colour = self.colour
        return self.enc.encode(rgb, frame_no, audio)


def make_encoder(fs: float, system: str, p: dict, colour: bool = True, fmt: str | None = None):
    fmt = fmt or fmt_for(system, p, fs)
    if fmt == "405":
        return sysa.Encoder(fs)
    if fmt == "lowpal":
        return LowPalEncoder(fs, colour)
    if fmt == "mac":
        kw = fm_params(fs, fmt, p)
        return MacEncoder(fs, colour, chan_bw=min(kw["video_bw"], 0.45 * fs) - 0.2e6)
    return PalEncoder(fs, colour)


# --- modulators ----------------------------------------------------------------
# `tx_shape` / `rx_shape`: the radio's own fixed response, which the modem
# undoes (ohsatv/devices/ad936x.py); None for a radio taken as flat.
def make_mod(fs: float, system: str, p: dict, fmt: str | None = None, tx_shape=None):
    fmt = fmt or fmt_for(system, p, fs)
    if system == "am" and fmt == "405":
        return sysa.Mod(fs, tx_shape=tx_shape)
    if system == "am":
        snd = p.get("amSoundOffset", 5.9996e6) if fmt == "pal" else 0.0
        return modem.AmVsbMod(fs, video_bw=p.get("amVideoBw", 5.5e6), vsb_lower=p.get("amVsbLower", 1.25e6),
                              sound_offset=snd, sound_db=p.get("amSoundDb", -13.0), tx_shape=tx_shape,
                              nicam_on=bool(p.get("nicam")), placement=p.get("amPlacement", "images"))
    return modem.FmMod(fs, plan=_modem_plan(fs, fmt, p), tx_shape=tx_shape, nicam_on=_nicam(fmt, p),
                       **fm_params(fs, fmt, p))


def make_demod(fs: float, system: str, p: dict, fmt: str | None = None, rx_shape=None):
    fmt = fmt or fmt_for(system, p, fs)
    if system == "am" and fmt == "405":
        return sysa.Demod(fs, rx_shape=rx_shape)
    if system == "am":
        snd = p.get("amSoundOffset", 5.9996e6) if fmt == "pal" else 0.0
        return modem.AmVsbDemod(fs, video_bw=p.get("amVideoBw", 5.5e6), vsb_lower=p.get("amVsbLower", 1.25e6),
                                sound_offset=snd, rx_shape=rx_shape, nicam_on=bool(p.get("nicam")),
                                placement=p.get("amPlacement", "images"))
    return modem.FmDemod(fs, plan=_modem_plan(fs, fmt, p), rx_shape=rx_shape, nicam_on=_nicam(fmt, p),
                         **fm_params(fs, fmt, p))


def _nicam(fmt: str, p: dict) -> bool:
    """NICAM-728 rides on standard PAL only (ohsatv/nicam.py): the narrow
    formats have no room for a 6.55 MHz subcarrier."""
    return fmt == "pal" and bool(p.get("nicam"))


def _modem_plan(fs: float, fmt: str, p: dict) -> dict | None:
    if fmt == "pal":
        return None
    pl = fm_plan(fs, fmt, p)
    # The plan's video_bw is what the picture has (luminance's, for the
    # lowered-subcarrier PAL); the modem's video filter passes the whole
    # baseband up to chroma's top and stops before the sound subcarrier.
    return dict(pl, video_bw=fm_params(fs, fmt, p)["video_bw"])


# --- decoders ------------------------------------------------------------------
def make_decoder(fs: float, system: str, p: dict, colour: str = "auto", fmt: str | None = None):
    fmt = fmt or fmt_for(system, p, fs)
    if fmt == "405":
        # At the demodulator's output rate, which below 7.5 MS/s is twice
        # the radio's (sysa.video_rate).
        return VideoDecoder(sysa.video_rate(fs), colour, std=lines.SYSTEM_A)
    if fmt == "lowpal":
        return VideoDecoder(fs, colour, **lowpal.decoder_kwargs())
    if fmt == "mac":
        return mac.Decoder(fs, colour)
    return VideoDecoder(fs, colour)


# --- what is being received --------------------------------------------------
def _local_corr(fold: np.ndarray, t: np.ndarray) -> float:
    """Best normalised correlation of template `t` with any stretch of a
    line-long profile (circularly): 1.0 is the template's own shape,
    whatever the level and gain."""
    n, m = len(fold), len(t)
    t = t - t.mean()
    w = np.lib.stride_tricks.sliding_window_view(np.concatenate([fold, fold[:m]]), m)[:n]
    wz = w - w.mean(axis=1, keepdims=True)
    c = (wz @ t) / (np.linalg.norm(wz, axis=1) * np.linalg.norm(t) + 1e-12)
    return float(c.max())


# detect()'s thresholds. The first of each pair is for a receiver that has
# nothing (searching); the second, `sure`, for one that is already decoding
# a picture, where a wrong switch costs a picture that was working and a
# slow one costs nothing yet. Measured (simulator, 5 to 20 MS/s, test card,
# bars, black and grey, 8-30 dB CNR): Mini-MAC's Barker 0.96-1.0 even at 0
# dB, anything else under 0.89; Low-PAL's subcarrier 4.5 dB over its
# neighbours at 8 dB CNR, 25 at 30, every other format under 1.4 (a PAL
# test card at 16 MS/s, no noise).
MAC_MIN, MAC_SURE = 0.9, 0.95
SUB_MIN_DB, SUB_SURE_DB = 2.5, 4.0
# Sure that there is NO subcarrier: what a receiver decoding Low-PAL needs
# before it takes the signal for plain PAL.
SUB_NONE_DB = 1.5
PAL_MIN = 0.78


def detect(iq: np.ndarray, fs: float, preemph: bool = True, sure: bool = False) -> tuple[str | None, dict]:
    """Which format an FM signal carries, from ~40 ms of IQ: "mac",
    "lowpal", "pal", or None (nothing recognisable). For the receiver's
    automatic choice (Radio, `formatAuto`). `sure`: answer only with a
    margin (the thresholds above), None otherwise - for checking a format
    that is already decoding.

    Done on the raw discriminator output, before any format's video
    filter or de-emphasis, so one look answers for all of them:

    - a 64-line fold, low-passed to 1.5 MHz, correlated (normalised, so
      gain and offset do not matter) with Mini-MAC's Barker word and with a
      PAL line sync as the transmitter's pre-emphasis shapes it. The Barker
      matches Mini-MAC at 1.0 and anything else at under 0.8 (noise alone
      ~0.73); the sync matches PAL-type pictures at 0.82-0.9, Mini-MAC
      at under 0.7 and noise at 0.5-0.6 (measured down to 6 dB CNR);
    - the lowered-subcarrier PAL's 2.2 MHz sound subcarrier, as the mean
      power in +-40 kHz of it over the mean 120-250 kHz either side: 3.6 dB
      at 6 dB CNR, 25 dB at 30 dB. Means, not the median of the
      neighbours: a standard PAL picture at 16 or 20 MS/s carries its line
      harmonics through 2.2 MHz, every 15.6 kHz, and a median at 2 kHz
      resolution lands between them - PAL read 3-5 dB at 30 dB CNR and was
      called Low-PAL (a grey picture with no colour, 22 dB). Against the
      mean the harmonics are on both sides and cancel: under 1.4 dB."""
    from .dsp.filters import taper
    n = pal.samples_per_line(fs)
    x = np.asarray(iq, np.complex64)
    d = np.angle(x[1:] * np.conj(x[:-1])).astype(np.float32) * np.float32(fs / (2 * np.pi))
    if len(d) < 70 * n:
        return None, {}
    m = 8192
    segs = d[:min(len(d) // m, 12) * m].reshape(-1, m)
    p = (np.abs(np.fft.rfft(segs * np.hanning(m).astype(np.float32), axis=1)) ** 2).mean(axis=0)
    f = np.fft.rfftfreq(m, 1 / fs)
    sub_db = 0.0
    if fs / 2 > lowpal.SUB + 0.2e6:
        on = np.abs(f - lowpal.SUB) < 40e3
        off = (np.abs(f - lowpal.SUB) > 120e3) & (np.abs(f - lowpal.SUB) < 250e3)
        sub_db = float(10 * np.log10(max(float(p[on].mean()), 1e-30) / max(float(p[off].mean()), 1e-30)))
    # Fold, then low-pass the fold around its own circle: the same as
    # low-passing first (the lines follow on from each other), at the cost
    # of one line's FFT instead of 40 ms of samples'.
    k = min(64, len(d) // n)
    fold = d[:k * n].reshape(k, n).mean(axis=0)
    fold = np.fft.irfft(np.fft.rfft(fold) * taper(np.fft.rfftfreq(n, 1 / fs), 1.5e6, 0.3e6), n)
    fold = fold - fold.mean()
    s_mac = _local_corr(fold, mac.Layout(fs).tmpl)
    tt = np.arange(int(-2e-6 * fs), int(8e-6 * fs)) / fs * 1e6
    sync = np.where((tt >= 0) & (tt < pal.HSYNC_US), pal.SYNC_TIP, 0.0)
    ff = np.fft.rfftfreq(4096, 1 / fs)
    h = taper(ff, 1.5e6, 0.3e6) * (modem.ccir405(ff) if preemph else 1.0)
    s_pal = _local_corr(fold, np.fft.irfft(np.fft.rfft(sync, 4096) * h, 4096)[:len(sync)])
    feats = {"mac": s_mac, "pal": s_pal, "subDb": sub_db}
    if s_mac > (MAC_SURE if sure else MAC_MIN) and s_mac > s_pal + 0.1:
        return "mac", feats
    if s_pal > PAL_MIN:
        if sub_db > (SUB_SURE_DB if sure else SUB_MIN_DB):
            return "lowpal", feats
        if not sure or sub_db < SUB_NONE_DB:
            return "pal", feats
    return None, feats
