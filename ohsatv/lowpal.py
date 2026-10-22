"""Lowered-subcarrier PAL: colour and FM sound in a 5 MS/s FM channel.

Standard PAL puts chroma on 4.43 MHz and FM-ATV's sound on 6 MHz; a USB
Pluto's ~5 MS/s carries about 2.4 MHz of baseband, so the narrow FM profile
had to drop both. This format keeps PAL's colour coding - V-switch, burst,
delay-line decoding - and moves everything down:

- **Luminance** (with the sync pulses) low-passed to 1.1 MHz, gone by 1.2
  MHz. Sync is filtered with the picture so its edges put nothing into the
  chroma band; the decoder's edge detector does not mind slower edges.
- **Chroma subcarrier 102.75 fH + 25 Hz = 1 605 493.75 Hz.** The same
  relation to the line rate as PAL's 283.75 fH + 25 Hz: the quarter-line
  offset puts U's and V's spectral lines (V is half a line rate off U,
  because of the V-switch) between luminance's line harmonics, and the
  25 Hz makes the dot pattern crawl instead of standing, so what chroma
  leaks into the picture is a fine moving texture rather than a pattern.
  It is a whole number of cycles in 0.16 s (256 879), so the 8-field
  sequence and a looped picture stay seam-free. 1.6 MHz rather than 1.5:
  the higher the subcarrier the finer its dots, and it still leaves luma
  1.1 MHz below and the sound subcarrier room above.
- **Chroma +-0.32 MHz** (gone by +-0.37): 1.24-1.98 MHz. About VHS's
  colour resolution; plenty for a test card's bars and a face.
- **Burst**: 4 cycles (2.5 us) from 5.5 us - 10 cycles of 1.6 MHz would not
  fit the back porch.
- **Sound**: an FM subcarrier at 2.2 MHz (+-30 kHz, 50 us), 200 kHz clear
  of chroma's top, added by the FM modulator as the standard 6 MHz one is.

The decoder is ohsatv.decoder.VideoDecoder with these numbers; the FM
modulator and demodulator are modem.FmMod/FmDemod with a sharper video
filter (the 0.4 MHz roll-off of the wide modes would cut into chroma or let
the sound subcarrier into the picture).
"""
from __future__ import annotations

import math

import numpy as np

from . import pal
from .dsp.filters import taper

FSC = 102.75 * pal.FH + 25.0          # 1 605 493.75 Hz
LUMA_BW = 1.1e6                       # half amplitude; transition 0.2 MHz
LUMA_EDGE = 0.2e6
CHROMA_BW = 0.32e6                    # each side of the subcarrier, half amplitude
CHROMA_EDGE = 0.1e6
BURST_CYCLES = 4
BURST_START_US = 5.5
SUB = 2.2e6                           # FM sound subcarrier
SUB_DEV = 30e3
TOP = FSC + CHROMA_BW + CHROMA_EDGE / 2      # highest video frequency: ~1.98 MHz


class Encoder(pal.Encoder):
    """RGB -> lowered-subcarrier PAL composite (625 x N volts)."""

    def __init__(self, fs: float, colour: bool = True):
        super().__init__(fs, colour=False)
        self.colour = colour and self.fs / 2 > TOP + 0.2e6
        f = np.fft.rfftfreq(self.n, 1 / self.fs)
        self._ymask = taper(f, LUMA_BW, LUMA_EDGE).astype(np.float32)
        self._cmask = taper(f, CHROMA_BW, CHROMA_EDGE).astype(np.float32)
        if self.colour:
            self.sin, self.cos = pal.subcarrier(self.fs, FSC)
            self.benv = pal._burst_env(self.fs, FSC, BURST_CYCLES, BURST_START_US, 0.5)
            self.bmask = pal._burst_mask()
        # Sync alone, low-passed once: it is the same every frame.
        self._sync = self._lp(self.sync, self._ymask)

    def _lp(self, x: np.ndarray, mask: np.ndarray) -> np.ndarray:
        # Along each line, circularly: every line ends on blanking and the
        # next line's sync edge, so the wrap joins what really follows.
        return np.fft.irfft(np.fft.rfft(x, axis=1) * mask, self.n, axis=1).astype(np.float32)

    def encode(self, rgb: np.ndarray, frame_no: int = 0) -> np.ndarray:
        rgb = np.asarray(rgb)
        if rgb.dtype == np.uint8:
            rgb = rgb.astype(np.float32) / 255.0
        rgb = np.clip(rgb.astype(np.float32), 0.0, 1.0)
        if rgb.shape[0] != pal.ACTIVE_ROWS:
            ri = np.clip(((np.arange(pal.ACTIVE_ROWS) + 0.5) * rgb.shape[0] / pal.ACTIVE_ROWS).astype(int), 0,
                         rgb.shape[0] - 1)
            rgb = rgb[ri]
        rgb = pal._resample_rows(rgb, self.count)
        y, u, v = pal.rgb_to_yuv(rgb)
        comp = self._sync + self._lp(self._place(y), self._ymask)
        if self.colour:
            uu = self._place(u)
            vv = self._place(v)
            # Burst on -U +- V (as PAL), added before the chroma filter so it
            # is band-limited with everything else.
            b = (pal.BURST_AMP / math.sqrt(2.0)) * self.benv[None, :] * self.bmask[:, None]
            sw = pal.vswitch(frame_no)[:, None]
            uu = self._lp(uu - b, self._cmask)
            vv = self._lp(vv + b, self._cmask)
            k = frame_no % pal.SEQ_FRAMES
            comp += uu * self.sin[k] + sw * vv * self.cos[k]
        return comp.astype(np.float32)


def decoder_kwargs() -> dict:
    """VideoDecoder settings for this format."""
    return {"fsc": FSC, "burst_us": (BURST_START_US + 0.5, BURST_START_US + BURST_CYCLES / FSC * 1e6 - 0.5),
            "chroma_half": CHROMA_BW + 0.05e6, "chroma_edge": 0.15e6, "luma_bw": LUMA_BW,
            "porch_us": (8.9, 10.3)}
