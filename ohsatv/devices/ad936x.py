"""The AD936x's own fixed filters, as a frequency response, so the modems
can undo them (docs/atv-research.md 17).

Without a programmable FIR - and OpenHamShack ATV never loads one, and bypasses
anybody else's (pluto.py) - the AD936x still filters every sample it sends
or receives: a third-order Butterworth and a single pole in the analogue
baseband, set from `rf_bandwidth`, and a chain of fixed half-band filters
between the converter and the sample rate. ADI's own designs hide the
half-bands' roll-off with a compensating FIR; with none loaded it is all
there. The last half-band (HB1) is, by being a half-band, 6 dB down at the
output's Nyquist frequency, and FM-ATV puts its colour and sound sidebands
close to it: at 12.5 MS/s the 5.5 MHz sound subcarrier is at 0.44 fs, where
transmitter and receiver together lose 7.8 dB, and PAL chroma at 0.35 fs
loses 2.3. On 5 October 2026 that is what the received spectrum showed
(chroma 1.7-4 dB down, subcarrier 7-10 dB) and why the off-air colour burst
came in at 0.73 of the transmitted one (docs/atv-research.md 15a, 17).

Widening `rf_bandwidth` would not help: the analogue filters are ~1 dB of
that 7.8 and the half-bands are fixed. What does help is that the response
is known exactly - the half-band coefficients are ADI's (libad9361-iio's
filter designer, internal_design_filter_cg.c, which also gives the
analogue models used here), the analogue corners are the Linux driver's
(ad9361.c: RX 1.4 x and TX 1.6 x the baseband bandwidth, which is half
rf_bandwidth) - so each modem folds its inverse into a filter it already
runs, at no cost per sample. The model was checked against the receivers'
own noise floors (the nano at 10 MS/s, the LibreSDR at 16), which it
matches to within 0.3 and 1 dB.

Which half-bands run depends on the rate; the driver reports it as
`rx_path_rates` / `tx_path_rates` ("BBPLL:800000000 DAC:100000000
T2:50000000 T1:25000000 TF:12500000 TXSAMP:12500000" on the LibreSDR at
12.5 MS/s). Each ratio between neighbours names a stage. When the radio does
not say, the driver's nominal rate governor (x8: HB3, HB2, HB1 at 2 each)
is assumed - what both radios ran at every rate measured here.
"""
from __future__ import annotations

import re

import numpy as np

# Coefficients from ADI's filter designer (libad9361-iio
# filterdesigner/internal_design_filter_cg.c), as integers over their scale.
RX_HB1 = np.array([-8, 0, 42, 0, -147, 0, 619, 1013, 619, 0, -147, 0, 42, 0, -8]) / 2048
TX_HB1 = np.array([-53, 0, 313, 0, -1155, 0, 4989, 8192, 4989, 0, -1155, 0, 313, 0, -53]) / 16384
HB2 = np.array([-9, 0, 73, 128, 73, 0, -9]) / 256
RX_HB3 = np.array([1, 4, 6, 4, 1]) / 16
TX_HB3 = np.array([1, 2, 1]) / 4
RX_DEC3 = np.array([55, 83, 0, -393, -580, 0, 1914, 4041, 5120, 4041, 1914, 0, -580, -393, 0, 83, 55]) / 16384
TX_INT3 = np.array([36, -19, 0, -156, -12, 0, 479, 223, 0, -1215, -993, 0, 3569, 6277, 8192, 6277, 3569, 0,
                    -993, -1215, 0, 223, 479, 0, -12, -156, 0, -19, 36]) / 24576

# The most a modem boosts to undo the roll-off. The plans keep content
# below 0.48 fs (modem.VIDEO_NYQ), where the receiver is 6.7 dB down; past
# that, boosting would mostly lift noise and the edge of the next image.
EQ_MAX_DB = 6.0

# The nominal chain: stage factors from the converter down, (HB3, HB2, HB1).
NOMINAL = (2, 2, 2)

_RATES = re.compile(r"([A-Z0-9]+):(\d+)")


def parse_path_rates(text: str | None) -> tuple[int, int, int] | None:
    """(HB3/DEC3, HB2, HB1) factors from the driver's rx_path_rates or
    tx_path_rates, or None if the text is not that. A stage that does not run
    is 1; HB3's 3 is DEC3/INT3. None too when a FIR is changing the rate:
    its taps are somebody else's, so the response is not known here."""
    if not text:
        return None
    r = [int(v) for _, v in _RATES.findall(text)]
    # BBPLL, converter, then three stage outputs and the FIR's.
    if len(r) != 6 or min(r[1:]) <= 0 or r[4] != r[5]:
        return None
    f = [r[1] / r[2], r[2] / r[3], r[3] / r[4]]
    if any(abs(x - round(x)) > 1e-6 or round(x) not in (1, 2, 3) for x in f):
        return None
    if round(f[1]) == 3 or round(f[2]) == 3:
        return None
    return tuple(int(round(x)) for x in f)


def _fir_mag(h: np.ndarray, f: np.ndarray, fs: float) -> np.ndarray:
    n = np.arange(len(h)) - (len(h) - 1) / 2       # symmetric taps: centred, the response is real
    g = np.cos(2 * np.pi * np.outer(np.ravel(f) / fs, n)) @ (h / h.sum())
    return np.abs(g).reshape(np.shape(f))


class Response:
    """|H(f)| of one direction of an AD936x at sample rate `fs`, with
    `rf_bw` its rf_bandwidth: the analogue baseband filters and the
    half-band chain `stages` (from parse_path_rates; NOMINAL if None).
    Called with frequencies in Hz from the LO; the filters are real, so the
    response is symmetric about 0."""

    def __init__(self, path: str, fs: float, rf_bw: float, stages: tuple[int, int, int] | None = None):
        if path not in ("rx", "tx"):
            raise ValueError(path)
        self.path = path
        self.fs = float(fs)
        self.rf_bw = float(rf_bw)
        self.stages = tuple(stages) if stages else NOMINAL

    def analogue(self, f: np.ndarray) -> np.ndarray:
        f = np.abs(np.asarray(f, np.float64))
        if self.path == "rx":
            bb = min(max(self.rf_bw / 2, 0.2e6), 28e6)          # ad9361_rx_bb_analog_filter_calib's clamp
            corner, pole = 1.4 * bb, 2.5 * bb                     # BB LPF, and the TIA
        else:
            bb = min(max(self.rf_bw / 2, 0.625e6), 20e6)        # ad9361_tx_bb_analog_filter_calib's clamp
            corner, pole = 1.6 * bb, 5.0 * bb                     # BB LPF, and the secondary filter
        return 1 / np.sqrt((1 + (f / corner) ** 6) * (1 + (f / pole) ** 2))

    def digital(self, f: np.ndarray) -> np.ndarray:
        f = np.abs(np.asarray(f, np.float64))
        hb3, hb2, hb1 = self.stages
        rx = self.path == "rx"
        g = np.ones_like(f)
        rate = self.fs
        for factor, taps in ((hb1, RX_HB1 if rx else TX_HB1), (hb2, HB2),
                             (hb3, (RX_HB3 if rx else TX_HB3) if hb3 == 2 else (RX_DEC3 if rx else TX_INT3))):
            if factor == 1:
                continue
            rate *= factor
            g = g * _fir_mag(taps, f, rate)
        if not rx:
            g = g * np.abs(np.sinc(f / rate))                     # the DAC's hold
        return g

    def __call__(self, f) -> np.ndarray:
        return self.analogue(f) * self.digital(f)

    def correction(self, f, max_db: float = EQ_MAX_DB) -> np.ndarray:
        """1 / |H(f)|, limited to `max_db` of boost."""
        return np.minimum(1.0 / np.maximum(self(f), 1e-6), 10 ** (max_db / 20))

    def image(self, f: float) -> tuple[float, float]:
        """(where, dB): the image a transmitter makes of a component `f` Hz
        from the LO, and how far under that component it comes out. The
        half-bands interpolate by two first, so the image is at f - fs (f +
        fs below the LO) and HB1 is the filter that has to stop it. HB1's
        transition band is symmetric about fs/2, so what matters is how far
        the component sits inside fs/2: about -41 dB at 0.30 fs, -25 at
        0.36, -13 at 0.42, the same at every rate since rf_bandwidth follows
        it (docs/atv-research.md 21a, 24). The later stages and the
        analogue filters take a little more."""
        img = f + self.fs if f < 0 else f - self.fs
        return img, float(20 * np.log10(max(float(self(img)), 1e-9) / max(float(self(f)), 1e-9)))

    def describe(self) -> str:
        return f"AD936x {self.path.upper()} at {self.fs / 1e6:g} MS/s, rf_bandwidth {self.rf_bw / 1e6:g} MHz"


def correction(shape, f, max_db: float = EQ_MAX_DB) -> np.ndarray:
    """The boost that undoes `shape` (a Response, or None for a radio taken
    as flat) at frequencies `f` from the LO: ones when there is nothing to undo."""
    if shape is None:
        return np.ones(np.shape(f))
    return shape.correction(f, max_db)
