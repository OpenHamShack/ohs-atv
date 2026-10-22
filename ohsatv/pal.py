"""625-line PAL: the timing, the line map, and the composite encoder.

Everything is in volts of composite video, as on a 75-ohm cable: sync tip
-0.3 V, blanking (and black - PAL has no set-up) 0 V, peak white +0.7 V.
The modulators (modem.py) map that to carrier amplitude or deviation; the
decoder (decoder.py) measures it back.

Timing follows ITU-R BT.470 / BT.1700 for 625/50 (sources in
docs/atv-research.md). Times are measured from **0H**, the half-amplitude
point of the line-sync leading edge, which is also where a "line" starts in
every array here. A frame is 625 lines of N samples, N = fs / 15625, so a
sample rate is only accepted when it gives a whole number of samples per
line - then a line, a field and the 8-field PAL sequence all fall on sample
boundaries and a static picture can be generated once and looped.

Why the encoder works on whole frames as 2-D arrays (625 x N): every step
is then one numpy expression over 300 000-1 300 000 samples, never a Python
loop per line or per sample, which is what makes real time possible at
10-20 MS/s on a laptop.
"""
from __future__ import annotations

import math
from functools import lru_cache

import numpy as np

# --- the standard ----------------------------------------------------------
LINES = 625
FH = 15_625.0                 # line frequency, Hz
LINE_S = 1.0 / FH             # 64 us
FIELD_HZ = 50.0
FSC = 4_433_618.75            # colour subcarrier = 283.75 FH + 25 Hz
SEQ_FRAMES = 4                # subcarrier phase repeats every 4 frames (8 fields): 0.16 s

SYNC_TIP = -0.3
BLANK = 0.0
WHITE = 0.7

HSYNC_US = 4.7                # line sync pulse
EQ_US = 2.35                  # equalising pulse
BROAD_GAP_US = 4.7            # the serration between broad pulses
HALF_US = 32.0
BROAD_US = HALF_US - BROAD_GAP_US   # 27.3 us
ACTIVE_START_US = 10.5        # 0H to start of picture
ACTIVE_US = 52.0              # picture width; front porch = 64 - 10.5 - 52 = 1.5 us
EDGE_US = 0.25                # sync edge rise time (10-90 %), BT.470: 0.25 +- 0.05 us
PIC_EDGE_US = 0.3             # picture blanking edges
BURST_START_US = 5.6          # 0H to burst start
BURST_CYCLES = 10
BURST_AMP = 0.15              # +-150 mV: 300 mV p-p, i.e. 3/7 of the 0.7 V luminance range

# Y, U, V from gamma-corrected R'G'B' (0..1), then scaled to volts by WHITE.
KR, KG, KB = 0.299, 0.587, 0.114
U_W, V_W = 0.493, 0.877

ACTIVE_ROWS = 576
# Picture lines of each field (frame line numbers, 1-based). Field 1 carries
# rows 0, 2, 4...; field 2 rows 1, 3, 5... Line 23's first half and line
# 623's second half are blanked, as in the standard.
FIELD1_FIRST, FIELD1_LAST = 23, 310
FIELD2_FIRST, FIELD2_LAST = 336, 623


def samples_per_line(fs: float) -> int:
    n = fs / FH
    if abs(n - round(n)) > 1e-6:
        raise ValueError(f"{fs / 1e6:g} MS/s is not a whole number of samples per 64 us line")
    return int(round(n))


# --- the field-sync pattern ----------------------------------------------
# Each line is two half-line slots; each slot starts with one pulse type.
# "h" line sync (only ever in the first slot), "e" equalising, "b" broad,
# "" nothing (the second half of a normal line). Frame lines are 1-based.
def _slot_table() -> list[tuple[str, str]]:
    t = [("h", "")] * (LINES + 1)          # index 0 unused
    # Field 1: pre-equalising 623.5..625.5, broad 1.0..3.0, post-equalising 3.5..5.5
    t[623] = ("h", "e")
    t[624] = ("e", "e")
    t[625] = ("e", "e")
    t[1] = ("b", "b")
    t[2] = ("b", "b")
    t[3] = ("b", "e")
    t[4] = ("e", "e")
    t[5] = ("e", "e")
    # Field 2: pre-equalising 311.0..313.0, broad 313.5..315.5, post 316.0..318.0
    t[311] = ("e", "e")
    t[312] = ("e", "e")
    t[313] = ("e", "b")
    t[314] = ("b", "b")
    t[315] = ("b", "b")
    t[316] = ("e", "e")
    t[317] = ("e", "e")
    t[318] = ("e", "")
    return t


SLOTS = _slot_table()


def picture_row(line: int) -> int | None:
    """Frame line (1..625) -> picture row (0..575), or None in blanking."""
    if FIELD1_FIRST <= line <= FIELD1_LAST:
        return 2 * (line - FIELD1_FIRST)
    if FIELD2_FIRST <= line <= FIELD2_LAST:
        return 2 * (line - FIELD2_FIRST) + 1
    return None


def burst_line(line: int) -> bool:
    """Lines carrying colour burst. Burst is suppressed through the field
    blanking around the sync pulses (lines 623-6 and 310-318). The standard's
    Bruch blanking sequence, which shifts this by a line per field so every
    field starts on the same burst phase, is not reproduced: decoders take
    the V-switch from the burst itself, ours included."""
    return 7 <= line <= 309 or 320 <= line <= 621


def _smooth_step(t: np.ndarray, rise: float) -> np.ndarray:
    """0 -> 1 over `rise` (10-90 %), a raised-cosine edge centred on t=0, so
    the 50 % point is exactly where the standard measures from."""
    w = rise / 0.8                       # raised cosine 10-90 % is ~80 % of its full width
    x = np.clip(t / w + 0.5, 0.0, 1.0)
    return 0.5 - 0.5 * np.cos(np.pi * x)


def _pulse(t: np.ndarray, start: float, width: float, rise: float) -> np.ndarray:
    return _smooth_step(t - start, rise) - _smooth_step(t - start - width, rise)


@lru_cache(maxsize=8)
def sync_frame(fs: float) -> np.ndarray:
    """625 x N array: blanking with every sync pulse in place, 0 V elsewhere."""
    n = samples_per_line(fs)
    t = np.arange(n) / fs * 1e6                      # us from 0H
    width = {"h": HSYNC_US, "e": EQ_US, "b": BROAD_US}
    cache: dict[tuple, np.ndarray] = {}
    out = np.zeros((LINES, n), np.float32)
    for line in range(1, LINES + 1):
        nxt = SLOTS[line % LINES + 1][0]
        key = (SLOTS[line], nxt)
        if key not in cache:
            row = np.zeros(n)
            for slot, kind in enumerate(key[0]):
                if kind:
                    row += _pulse(t, slot * HALF_US, width[kind], EDGE_US)
            # The next line's first pulse starts at 64 us: the first half of
            # its falling edge belongs to the end of this line.
            row += _pulse(t, 2 * HALF_US, width[nxt], EDGE_US)
            cache[key] = (SYNC_TIP * row).astype(np.float32)
        out[line - 1] = cache[key]
    return out


@lru_cache(maxsize=8)
def picture_window(fs: float) -> np.ndarray:
    """1-D window over a line: 1 across the picture, smooth edges, 0 in
    blanking. Picture content is multiplied by it, so blanking is exact and
    the edges do not ring."""
    n = samples_per_line(fs)
    t = np.arange(n) / fs * 1e6
    return _pulse(t, ACTIVE_START_US, ACTIVE_US, PIC_EDGE_US).astype(np.float32)


def active_samples(fs: float) -> tuple[int, int]:
    """(first, count) sample indices of the picture within a line."""
    first = int(math.ceil(ACTIVE_START_US * 1e-6 * fs))
    count = int(math.floor(ACTIVE_US * 1e-6 * fs))
    return first, count


@lru_cache(maxsize=4)
def subcarrier(fs: float, fsc: float = FSC) -> tuple[np.ndarray, np.ndarray]:
    """(sin, cos) of the colour subcarrier for one whole 4-frame sequence,
    shape (4, 625, N), phase measured from the first sample of frame 0.
    fsc x 0.16 s is a whole number of cycles (709 379), so this repeats
    seamlessly; a frame k of any longer transmission uses [k % 4]. (The
    lowered subcarrier of the narrow format, 102.75 fH + 25 Hz, is a whole
    number of cycles in 0.16 s too: 256 879.)"""
    n = samples_per_line(fs)
    total = SEQ_FRAMES * LINES * n
    k = np.arange(total, dtype=np.float64)
    ph = 2 * np.pi * ((k * (fsc / fs)) % 1.0)
    s = np.sin(ph).astype(np.float32).reshape(SEQ_FRAMES, LINES, n)
    c = np.cos(ph).astype(np.float32).reshape(SEQ_FRAMES, LINES, n)
    return s, c


def vswitch(frame: int) -> np.ndarray:
    """+1/-1 per line of `frame`: the PAL V-switch, alternating every line
    and continuing across frames (625 is odd, so it flips phase each frame)."""
    lines = (frame % 2) * LINES + np.arange(LINES)
    return np.where(lines % 2 == 0, 1.0, -1.0).astype(np.float32)


@lru_cache(maxsize=8)
def _burst_env(fs: float, fsc: float = FSC, cycles: int = BURST_CYCLES, start_us: float = BURST_START_US,
               rise_us: float | None = None) -> np.ndarray:
    n = samples_per_line(fs)
    t = np.arange(n) / fs * 1e6
    width = cycles / fsc * 1e6
    # Burst envelope rises over about a cycle, as a real burst gate does.
    return _pulse(t, start_us, width, rise_us or 1.0 / fsc * 1e6).astype(np.float32)


@lru_cache(maxsize=1)
def _burst_mask() -> np.ndarray:
    return np.array([burst_line(i + 1) for i in range(LINES)], np.float32)


@lru_cache(maxsize=1)
def _row_map() -> tuple[np.ndarray, np.ndarray]:
    """(frame line indices 0-based, picture rows) for every picture line."""
    lines, rows = [], []
    for line in range(1, LINES + 1):
        r = picture_row(line)
        if r is not None:
            lines.append(line - 1)
            rows.append(r)
    return np.array(lines), np.array(rows)


@lru_cache(maxsize=8)
def _half_line_masks(fs: float) -> tuple[np.ndarray, np.ndarray]:
    """Line 23 shows only its second half, line 623 only its first."""
    n = samples_per_line(fs)
    t = np.arange(n) / fs * 1e6
    second = _smooth_step(t - HALF_US, PIC_EDGE_US).astype(np.float32)
    return second, (1.0 - second).astype(np.float32)


def rgb_to_yuv(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """R'G'B' 0..1 (float, ... x 3) -> Y, U, V in volts."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    y = KR * r + KG * g + KB * b
    return WHITE * y, WHITE * U_W * (b - y), WHITE * V_W * (r - y)


def yuv_to_rgb(y, u, v) -> np.ndarray:
    """Volts back to R'G'B' 0..1 (unclipped)."""
    y = y / WHITE
    b = y + u / (WHITE * U_W)
    r = y + v / (WHITE * V_W)
    g = (y - KR * r - KB * b) / KG
    return np.stack([r, g, b], axis=-1)


def _resample_rows(img: np.ndarray, count: int) -> np.ndarray:
    """Resample each row of `img` (rows x w [x c]) to `count` samples by
    linear interpolation, all rows at once (a 2-tap matrix product would
    cost the same; this keeps memory flat)."""
    w = img.shape[1]
    x = (np.arange(count) + 0.5) * (w / count) - 0.5
    x = np.clip(x, 0, w - 1)
    i0 = np.floor(x).astype(np.int64)
    i1 = np.minimum(i0 + 1, w - 1)
    f = (x - i0).astype(np.float32)
    if img.ndim == 3:
        f = f[:, None]
    return img[:, i0] * (1 - f) + img[:, i1] * f


def _box_smooth(a: np.ndarray, width: int) -> np.ndarray:
    """Moving average along axis 1 (cheap chroma band-limiting)."""
    if width <= 1:
        return a
    k = np.ones(width, np.float32) / width
    pad = width // 2
    ap = np.pad(a, ((0, 0), (pad, width - 1 - pad)), mode="edge")
    c = np.cumsum(ap, axis=1, dtype=np.float32)
    c = np.concatenate([np.zeros((a.shape[0], 1), np.float32), c], axis=1)
    del k
    return (c[:, width:] - c[:, :-width]) / width


class Encoder:
    """RGB pictures -> composite PAL frames at sample rate fs.

    `encode(rgb, frame_no)` returns one frame (625 x N float32 volts).
    `colour=False` sends luminance only, with no burst (a mono picture; also
    what is sent when the sample rate cannot carry the subcarrier).
    `luma_bw` band-limits luminance (Hz); chroma is limited to +-1.3 MHz as
    in the standard.
    """

    def __init__(self, fs: float, colour: bool = True, luma_bw: float | None = None):
        self.fs = float(fs)
        self.n = samples_per_line(fs)
        self.colour = colour and self.fs / 2 > FSC + 0.5e6
        self.luma_bw = luma_bw
        self.first, self.count = active_samples(fs)
        self.sync = sync_frame(self.fs)
        self.win = picture_window(self.fs)
        self.lines, self.rows = _row_map()
        self.h23, self.h623 = _half_line_masks(self.fs)
        if self.colour:
            self.sin, self.cos = subcarrier(self.fs)
            self.benv = _burst_env(self.fs)
            self.bmask = _burst_mask()
        # Luma lowpass as a frequency mask applied along each line (rfft).
        self._lmask = None
        if luma_bw:
            f = np.fft.rfftfreq(self.n, 1 / self.fs)
            self._lmask = (0.5 + 0.5 * np.cos(np.pi * np.clip((f - luma_bw) / 0.5e6 + 0.5, 0, 1))).astype(
                np.float32)

    def _place(self, pic: np.ndarray) -> np.ndarray:
        """576 x count picture values -> 625 x N, zero in blanking."""
        full = np.zeros((LINES, self.n), np.float32)
        full[self.lines, self.first:self.first + self.count] = pic[self.rows]
        full *= self.win
        full[22] *= self.h23          # line 23
        full[622] *= self.h623        # line 623
        return full

    def encode(self, rgb: np.ndarray, frame_no: int = 0) -> np.ndarray:
        """rgb: H x W x 3, float 0..1 or uint8, any size (rows are resampled
        to 576 and columns to the active line)."""
        rgb = np.asarray(rgb)
        if rgb.dtype == np.uint8:
            rgb = rgb.astype(np.float32) / 255.0
        rgb = np.clip(rgb.astype(np.float32), 0.0, 1.0)
        if rgb.shape[0] != ACTIVE_ROWS:
            ri = np.clip(((np.arange(ACTIVE_ROWS) + 0.5) * rgb.shape[0] / ACTIVE_ROWS).astype(int), 0,
                         rgb.shape[0] - 1)
            rgb = rgb[ri]
        rgb = _resample_rows(rgb, self.count)
        y, u, v = rgb_to_yuv(rgb)
        yl = self._place(y)
        if self._lmask is not None:
            yl = np.fft.irfft(np.fft.rfft(yl, axis=1) * self._lmask, self.n, axis=1).astype(np.float32)
        comp = self.sync + yl
        if self.colour:
            # Chroma bandwidth +-1.3 MHz: a box average ~0.77 us wide puts its
            # first null at 1.3 MHz.
            w = max(1, int(round(self.fs / 1.3e6)))
            uu = self._place(_box_smooth(u, w))
            vv = self._place(_box_smooth(v, w))
            k = frame_no % SEQ_FRAMES
            s, c = self.sin[k], self.cos[k]
            sw = vswitch(frame_no)[:, None]
            comp += uu * s + sw * vv * c
            # Burst: -U +- V at 45 degrees, i.e. 180 +- 45 degrees from U.
            b = (BURST_AMP / math.sqrt(2.0)) * self.benv[None, :] * self.bmask[:, None]
            comp += b * (-s + sw * c)
        return comp.astype(np.float32)
