"""Raw composite -> pictures: sync recovery, line tracking, PAL decoding.

Input is what a demodulator returns (modem.py): composite video at the
radio's sample rate with the right shape and sync pulses going negative, at
an unknown gain and offset. Output is one RGB frame (576 rows, shown at 4:3)
per 625 lines, plus the measurements the console shows.

How it locks, and why this way:

- **Levels** come from the signal: sync tip and blanking are measured on
  every line (inside the sync pulse; on the back porch, which the colour
  burst averages out of over its whole cycles), and the slicing threshold is
  half way between, the 50 % point the standard times everything from. So
  an AM demodulator's arbitrary gain, an FM one's carrier offset (a DC shift)
  and a fading signal are all the same problem, solved once.
- **Acquisition** folds 64 lines of "below threshold" on top of each other:
  line sync is the one thing at a fixed place in every line, so it stands out
  of the fold even when a single line is lost in noise.
- **Tracking**: every 625-line PAL line, including the equalising and broad
  pulse lines, has a falling edge exactly at 0H. Each line's edge is found by
  a matched edge detector (the mean of 2 us before minus the mean of 2 us
  after, which peaks on a step and averages noise over 40-80 samples), and
  the line starts are then a straight-line fit through a whole batch of
  edges with outliers rejected. A transmitter's line rate is crystal-locked,
  so the only real motion is a slow drift from the two sample clocks'
  difference - which a line fit follows exactly while averaging away the
  per-line noise that would otherwise make vertical edges ragged.
- **Field sync**: a line whose first half is mostly sync is broad-pulse
  territory; the first broad half-line after normal lines is either the
  start of line 1 (field 1) or the middle of line 313 (field 2). That one
  observation numbers every line, and the count is then kept by the
  flywheel and checked at each field. It is claimed as field lock only
  once the lines around it are line-locked (CLAIM_H): noise passes the
  field sync test now and then, never that.
- **PAL colour**: the burst is measured on every line against an absolute
  subcarrier phase, so the reference is constant down the frame and can be
  averaged over neighbouring lines (both burst phases, which cancels the
  +-45 degree swing). The V-switch comes from each line's burst. Chroma is
  demodulated, low-passed and averaged with the previous line (the PAL delay
  line: hue errors become small saturation errors); luminance is the
  composite less the re-modulated chroma. With no burst, or one too weak,
  the picture is decoded in mono.
"""
from __future__ import annotations

import math

import numpy as np

from . import lines as lines_mod
from . import pal, vbi

# Frames come out at the decoder's own sampling of the 52 us picture (520
# samples at 10 MS/s, 832 at 16): 576 rows of `count` columns, to be shown
# at 4:3. Scaling to square pixels is left to the display, which does it in
# C (QImage.scaled) instead of here in numpy at ~7 ms a frame.
ASPECT = 4 / 3

# Field lock is claimed - the lines numbered from nothing, FIELD shown
# LOCKED, frames given out - only while the newest CLAIM_LINES or more
# lines tracked are line-locked: CLAIM_H of their syncs found. Until then a
# field sync numbers the lines provisionally (they are stored, nothing is
# claimed); if the lines then fail the test the numbering is forgotten.
#
# Why: on an empty FM band at 10-20 MS/s with a narrow video filter the
# field sync test passed on noise in up to 1 block in 25, and the FIELD
# pill flashed LOCKED (atv-research.md 16). Noise keeps the tracker going
# because a fair share of its "edges" land inside the line fit's +-0.6 us
# outlier window, and band-limited noise sits on either side of the slice
# for half a line at a time, as broad pulses do.
#
# Why pooled, and why these numbers (measured 5 October 2026, white noise
# at 5-20 MS/s, AM and FM, PAL and narrow colour PAL): a floor on one batch
# cannot do it. The first batch after acquisition is 64 lines; on noise
# its found fraction averages 0.25 and reached 0.61 in 24 000 batches, and
# 3 of 689 noise field syncs came in batches with half or more found -
# where a weak real signal's first batch reads 0.5-0.6. Over 256 lines the
# noise fraction averaged 0.16-0.22 (sd 0.04) and never passed 0.34 in
# 14 000 checks, so 0.5 is seven standard deviations clear. 0.5 is also
# the hold limit (`poor` in _track: two batches under half found start
# acquisition again), so any signal that can hold a field lock can claim
# one: no lock threshold moved. A real lock is confirmed in the batch after
# its field sync, before the frame it begins is finished, so no picture is
# lost; the FIELD pill can light one 40 ms block later. What does change
# is below the hold limit, a signal losing lock every few batches: it
# claims a field in fewer blocks and gives fewer of the broken pictures
# it used to (frames with most of their lines' syncs missing).
CLAIM_H = 0.5
CLAIM_LINES = 256


class VideoDecoder:
    def __init__(self, fs: float, colour: str = "auto", *, fsc: float = pal.FSC,
                 burst_us: tuple[float, float] | None = None, chroma_half: float = 1.3e6,
                 chroma_edge: float = 0.6e6, luma_bw: float | None = None, porch_us=None,
                 std: lines_mod.Standard = lines_mod.PAL625):
        """The defaults are 625-line PAL. The narrow formats (ohsatv/narrow.py)
        change them: the lowered-subcarrier PAL moves `fsc`, its burst and
        the chroma band and low-passes luminance at `luma_bw`. `std` is the
        line standard (ohsatv/lines.py): 405-line System A is the same
        decoder with other numbers, mono."""
        self.fs = float(fs)
        self.std = std
        # 625 lines are a whole number of samples at every rate offered;
        # 405 never are, so the line period (`P`) and the samples taken for
        # each line (`n`, the period rounded up) are kept apart.
        self.P = std.period(self.fs)
        self.n = pal.samples_per_line(fs) if std is lines_mod.PAL625 else std.window(self.fs)
        self.colour_mode = colour            # "auto" | "mono"
        us = self.fs * 1e-6
        self.us = us
        self.fsc = float(fsc)
        self.B = max(4, int(round(2.0 * us)))        # edge detector half-width
        self.porch_us = porch_us or std.porch_us
        self.search = int(round(1.5 * us))           # tracking search either side of the prediction
        self.margin = self.B + self.search + 4
        self.buf = np.zeros(0, np.float32)
        self.buf0 = 0                                # absolute index of buf[0]
        self.reset()
        self.first, self.count = std.active_samples(fs)
        self.frames_out = 0
        self.frame = np.zeros((std.lines, self.n), np.float32)
        self.starts = np.zeros(std.lines, np.float64)
        self.valid = np.zeros(std.lines, bool)
        self.frac = np.zeros(std.lines, np.float64)       # each line's start less the whole sample it was cut at
        t = np.arange(self.n)
        self.sc = np.exp(-2j * np.pi * self.fsc / self.fs * t).astype(np.complex64)
        if burst_us is None:
            burst_us = (pal.BURST_START_US + 0.3, pal.BURST_START_US + pal.BURST_CYCLES / pal.FSC * 1e6 - 0.3)
        self.burst = slice(int(round(burst_us[0] * us)), int(round(burst_us[1] * us)))
        # The chroma band-pass, positive frequencies only (x2: the analytic
        # signal), kept as the slice of rfft bins where it is not zero. Both
        # transforms are scaled - forward by norm="forward", inverse by
        # default - because numpy 2.2's unscaled ones are ~4x slower on
        # these sizes (dsp/filters.py, Ols); the masks put n back.
        f = np.fft.rfftfreq(self.n, 1 / self.fs)
        x = np.clip((np.abs(f - self.fsc) - chroma_half) / chroma_edge + 0.5, 0, 1)
        bp = np.where((f > 0) & (f < self.fs / 2), 1.0 + np.cos(np.pi * x), 0.0)
        nz = np.flatnonzero(bp)
        self.chroma_bins = slice(int(nz[0]), int(nz[-1]) + 1) if len(nz) else slice(0, 0)
        self.chroma_bp = (bp[self.chroma_bins] * self.n).astype(np.float32)
        self.luma_lp = None
        if luma_bw:
            self.luma_lp = (self.n * (0.5 + 0.5 * np.cos(np.pi * np.clip((f - luma_bw) / 0.2e6 + 0.5, 0, 1)))
                            ).astype(np.float32)
        self.order = std.order                          # frame line (0-based) of each picture row
        self._spec = self._chroma = None
        self.last_rgb = np.zeros((std.rows, self.count, 3), np.uint8)
        self.last_uv = None
        # False: finish frames without their picture (rgb None), keeping the
        # timing and line count - set by the engine while it is behind
        # (Radio._decode_loop; ohsatv/mac.py says why).
        self.picture = True
        # Teletext on the blanked lines (ohsatv/vbi.py). Not below
        # vbi.MIN_FS: there the run-in's 3.47 MHz is past the Nyquist
        # frequency, nothing can be sliced, and what the gate then let
        # through was a false packet now and then (seen at 6.25 MS/s).
        # 625 lines only: World System Teletext is defined on 625-line
        # blanking (EN 300 706 4.1); 405-line System A never carried it,
        # and the slicer's line numbers and timing would read a 405 frame's
        # lines as nonsense (tests/test_teletext.py, Lines405).
        self.slicer = (vbi.Slicer(self.fs) if self.fs >= vbi.MIN_FS and std is lines_mod.PAL625
                       else None)
        self.stats = {"hLock": 0.0, "vLock": False, "colour": False, "snr": None, "jitterNs": None,
                      "lines": 0, "level": None, "state": "searching", "burst": None}

    def reset(self):
        self.locked = False
        self.line_next = None            # absolute 0H of the next line to process
        self.period = float(self.P)
        self.line_no = None              # frame line number (1..625) of line_next
        self.tip = None
        self.blank = None
        self.misses = 0
        self.poor = 0
        self.vmiss = 0
        self.prev_broad = False
        self.held = False                # field lock claimed for the current numbering (CLAIM_H)
        self.recent: list[tuple[int, int]] = []     # (lines, found) of the batches since acquisition
        self.acq_from = None             # absolute index to re-acquire from after a break
        self.acq_since = None            # where the search after that break began
        self.fresh = False               # levels are still the acquisition fold's
        # The next frame out follows a break: the engine's page store must
        # not join its teletext to what came before (ohsatv/ttx.PageStore.gap).
        self.gap = True

    # ------------------------------------------------------------------ input
    def feed(self, raw: np.ndarray) -> list[dict]:
        """Feed raw composite; returns frames completed: {"rgb", "stats"}."""
        raw = np.asarray(raw, np.float32)
        self.buf = np.concatenate([self.buf, raw]) if len(self.buf) else raw.copy()
        out = []
        while True:
            if not self.locked:
                got = self._acquire()
                if got is None:          # failed inside a break, moved on: try again
                    continue
                if not got:
                    break
            got = self._track(out)
            if not got:
                break
        # Keep only what the next call can still need.
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

    # ------------------------------------------------------------ acquisition
    def _acquire(self) -> bool | None:
        """Average 64 lines on top of each other at the nominal line length
        and look for the 4.7 us dip: line sync is the one thing at the same
        place in every line, and averaging takes the noise down 18 dB first,
        with no threshold to set from a level not yet known. Picture content
        can be dark but never below blanking, so the deepest 4.7 us of the
        fold is sync. (A first version thresholded at a percentile-derived
        level; in heavy noise the percentiles moved past the sync tip and it
        never locked at 12 dB CNR.)"""
        k = 64
        need = (k + 2) * self.n
        # After a break in the signal (break_at below) fold the lines that
        # follow it, not the newest ones, so nothing between is skipped.
        s0 = len(self.buf) - need if self.acq_from is None else int(self.acq_from) - self.buf0
        if s0 < 0 or s0 + need > len(self.buf):
            return False
        x = self.buf[s0:s0 + need]
        if self.std.whole(self.fs):
            prof = x[: k * self.n].reshape(k, self.n).mean(axis=0).astype(np.float64)
        else:
            # A fractional period: each line from the sample nearest its
            # start, so 64 lines fold on top of each other rather than
            # sliding 0.17 of a sample apart per line (11 samples over the
            # fold at 5 MS/s, 2 us of smear on the sync's edges).
            st = np.round(np.arange(k) * self.P).astype(np.int64)
            prof = x[st[:, None] + np.arange(self.n)].mean(axis=0).astype(np.float64)
        w = int(round(self.std.hsync_us * self.us))
        c = np.concatenate([prof, prof[:w]])
        cs = np.cumsum(np.concatenate([[0.0], c]))
        box = (cs[w:w + self.n] - cs[:self.n]) / w          # mean over [i, i + w)
        ph = int(np.argmin(box))
        tip = float(box[ph])
        # Back porch: after the burst, before the picture.
        p0, p1 = self.std.porch_us
        bp = prof[(ph + int(p0 * self.us) + np.arange(int(round((p1 - p0) * self.us)))) % self.n].mean()
        rest = np.median(box)
        blank = float(bp)
        # Sync must stand out of the fold: deeper than the back porch by
        # clearly more than the averaged noise, and the porch must not be
        # below the typical level of the line (it is blanking).
        # Noise from the fold's sample-to-sample differences, robustly: the
        # fold of a picture with vertical edges (a test card) has a few big
        # steps that a standard deviation counts as noise, and a narrow AM
        # picture with a shallow sync in its fold then failed this test
        # (found with the rejected sound-in-syncs format). For pure noise the
        # median-based figure is the same as the standard deviation.
        noise = 1.4826 * float(np.median(np.abs(np.diff(prof)))) / np.sqrt(2)
        if not (blank - tip > 6 * noise and blank - tip > 0.25 * (rest - tip + 1e-12) and rest >= tip):
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
        start = s0 + ph
        self.acq_from = None
        self.fresh = True
        if start < self.margin:
            start += int(round(self.P))
        self.line_next = float(self.buf0 + start)
        self.period = float(self.P)
        self.tip, self.blank = tip, blank
        self.locked = True
        self.line_no = None
        self.misses = 0
        self.recent = []
        return True

    # --------------------------------------------------------------- tracking
    def _track(self, out: list) -> bool:
        n = self.n
        avail = self.buf0 + len(self.buf) - self.margin - n - self.B
        count = int((avail - self.line_next) // self.period)
        if count < 8:
            return False
        count = min(count, 320)
        k = np.arange(count)
        pred = self.line_next + k * self.period                 # absolute
        base = np.round(pred).astype(np.int64) - self.buf0
        B, S = self.B, self.search
        # Edge detector over the search window, all lines at once.
        offs = np.arange(-S - B, S + B + 1)
        # Clipped at blanking, so picture content - a bright line ending
        # 1.5 us before sync makes a bigger step than sync itself - cannot
        # look like an edge; only excursions into the sync region count.
        win = np.minimum(self.buf[base[:, None] + offs[None, :]], self.blank).astype(np.float64)
        cs = np.concatenate([np.zeros((count, 1)), np.cumsum(win, axis=1)], axis=1)
        taus = np.arange(B, B + 2 * S + 1)                       # positions in win with B either side
        pre = (cs[:, taus] - cs[:, taus - B]) / B
        post = (cs[:, taus + B] - cs[:, taus]) / B
        e = pre - post
        j = np.argmax(e, axis=1)
        emax = e[k, j]
        jj = np.clip(j, 1, 2 * S - 1)
        a, b, c = e[k, jj - 1], e[k, jj], e[k, jj + 1]
        den = a - 2 * b + c
        frac = np.where(np.abs(den) > 1e-12, 0.5 * (a - c) / np.where(den == 0, 1, den), 0.0)
        frac = np.clip(frac, -0.5, 0.5)
        # e peaks where the step is centred between the two windows: that is
        # the 50 % point of the falling edge.
        edge = base + (jj - S) + frac - 0.5 + self.buf0         # absolute, fractional
        height = self.blank - self.tip
        ok = emax > 0.5 * height
        r = edge - pred
        if ok.sum() >= max(6, count // 5):
            med = np.median(r[ok])
            ok &= np.abs(r - med) < 0.6 * self.us
        # A break - the transmitter's buffer ran dry and it carried on late,
        # or the receiver lost samples - leaves good lines and then none
        # where they are expected. Take the batch only up to the break and
        # re-acquire from there, rather than predicting lines into a timing
        # that no longer exists for a batch or two first.
        cut = break_at(ok)
        resync = cut is not None
        if cut == 0:
            # The batch is all after the break: acquire from its start - or,
            # straight after an acquisition that found nothing to track, 16
            # lines on, so that a fold which passes and a batch which does
            # not can never loop on the same samples (seen: the engine's
            # decode thread stuck at the moment a transmission started).
            self._lost_at(self.line_next + (16 * self.n if self.fresh else 0))
            return True
        if resync:
            count = cut
            k, pred, base, r, ok, edge = k[:cut], pred[:cut], base[:cut], r[:cut], ok[:cut], edge[:cut]
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
        else:
            starts = pred
            self.misses += 1
            slope = 0.0
            self.stats["jitterNs"] = None
        hfrac = float(ok.mean())
        self.stats["hLock"] = 0.8 * self.stats["hLock"] + 0.2 * hfrac if self.stats["lines"] else hfrac
        self.stats["lines"] += count
        # The evidence for claiming a field (CLAIM_H): the newest batches
        # covering at least CLAIM_LINES lines.
        self.recent.append((count, int(ok.sum())))
        while len(self.recent) > 1 and sum(c for c, _ in self.recent[1:]) >= CLAIM_LINES:
            self.recent.pop(0)
        # A lock that only a minority of lines agree with is not a lock: a
        # jump in the stream (a receiver overrun drops samples) leaves the
        # flywheel half a picture out, and the outlier rejection then fits
        # whichever lines happen to agree. Seen on a busy machine at 20 MS/s:
        # "locked" at ~60 % of lines, a doubled picture, for good. Two
        # batches in a row with under half the lines found start again from
        # acquisition, which takes one fold of 64 lines.
        self.poor = self.poor + 1 if hfrac < 0.5 else 0
        if self.poor >= 2:
            self.misses = 7
            self.poor = 0
        if self.misses > 6:
            self.locked = False
            # Nothing measured while searching is current: a jitter or SNR
            # left from the last lock would be shown as if it were (seen on
            # the real nano with no signal: 'searching ... jitter 326 ns').
            self.stats.update(state="searching", vLock=False, jitterNs=None, snr=None)
            self.line_next = None
            self.line_no = None
            self.misses = 0
            # Drop what we searched so acquisition looks at fresh signal.
            return True

        # Each line from the sample nearest its fitted start - whole
        # samples, no interpolation. Linear interpolation was here first:
        # it is a low-pass whose depth depends on the fraction of a sample
        # the start falls at - at a fraction of 0.5 it passes cos(pi f / fs)
        # of frequency f, 0.17 of a 4.43 MHz subcarrier at 10 MS/s, 0.53
        # of the narrow format's 1.6 MHz at 5 MS/s. Two real radios' clocks
        # differ by ~15 ppm, so the fraction drifts down the picture and the
        # colour would come and go in bands; in the simulator the fraction
        # was steady and it only showed as saturation falling with noise.
        # Whole samples cost a sub-sample horizontal step where the rounding
        # changes (0.1 us at 10 MS/s); the subcarrier phase is taken at the
        # same whole-sample start, so colour is exact.
        frac = starts - np.round(starts)
        starts = np.round(starts)
        i0 = (starts - self.buf0).astype(np.int64)
        # Whole rows out of a strided view: one copy per line, where a
        # two-dimensional fancy index built and read a 400 000-entry index.
        lines = np.lib.stride_tricks.sliding_window_view(self.buf, n)[i0]

        # Levels from lines with a valid edge: tip inside the pulse (normal,
        # equalising and broad pulses all hold sync tip 0.5-2 us after 0H),
        # blanking on the back porch after the burst.
        us = self.us
        tip_l = lines[:, int(0.6 * us):int(2.0 * us)].mean(axis=1)
        bp_l = lines[:, int(self.porch_us[0] * us):int(self.porch_us[1] * us)].mean(axis=1)
        if ok.any():
            tip = float(np.median(tip_l[ok]))
            # Back porch only on normal lines: broad pulse lines are at sync
            # there. Normal lines are the majority, so the median holds.
            blank = float(np.median(bp_l[ok]))
            if blank > tip:
                # Straight after acquisition the levels are the fold's, and
                # a fold taken across a break has the break's noise in it:
                # take this batch's own (seen wrong, they made noise lines
                # look like broad pulses and numbered a field wrong).
                g = 1.0 if self.fresh else 0.5
                self.fresh = False
                self.tip = (1 - g) * self.tip + g * tip
                self.blank = (1 - g) * self.blank + g * blank
        height = max(self.blank - self.tip, 1e-9)
        norm = (lines - self.blank) * (-pal.SYNC_TIP / height)
        self.stats["level"] = float(height)

        # Field sync: fraction of each half-line below the slice.
        half = int(self.P // 2)
        low = norm < pal.SYNC_TIP / 2
        q1 = low[:, int(5 * us):half].mean(axis=1) > 0.5
        q2 = low[:, half + int(5 * us):int(self.P)].mean(axis=1) > 0.5
        prev_any = np.concatenate([[self.prev_broad], (q1 | q2)[:-1]])
        f1 = q1 & ~prev_any
        f2 = q2 & ~q1 & ~prev_any
        self.prev_broad = bool(q1[-1] | q2[-1])
        anchor = None
        # Only where the line's own sync edge was found, and the line
        # before's: noise - the inside of a break in the signal - is
        # "mostly below the slice" too, and once numbered a field wrong it
        # takes two field syncs to put right.
        okp = np.concatenate([[True], ok[:-1]])
        # And the line after must be broad pulses throughout (lines 2 and
        # 314 are): a dark or noisy half-line can pass for a broad pulse,
        # two and a half in the right pattern cannot.
        nxt = np.concatenate([(q1 & q2)[1:], [True]])
        for i in np.flatnonzero((f1 | f2) & ok & okp & nxt):
            anchor = (int(i), 1 if f1[i] else self.std.field2)
            break
        numbers = None
        L = self.std.lines
        if self.line_no is not None:
            numbers = (self.line_no - 1 + k) % L + 1
        else:
            self.held = False
        if anchor is not None:
            i, ln = anchor
            if numbers is None or numbers[i] != ln:
                self.vmiss += 1
                if numbers is None or self.vmiss >= 2:
                    numbers = (ln - 1 + (k - i)) % L + 1
                    numbers[:i] = 0          # before the anchor: unknown, discard
                    self.vmiss = 0
                    # The count was wrong: what the frame holds so far is
                    # some other frame's lines.
                    self._break_vbi()
            else:
                self.vmiss = 0
        if numbers is not None and not self.held:
            seen = sum(c for c, _ in self.recent)
            if seen >= CLAIM_LINES:
                if sum(f for _, f in self.recent) >= CLAIM_H * seen:
                    self.held = True
                else:
                    numbers = self.line_no = None       # that was no field sync: forget it
        if numbers is not None:
            self.stats["vLock"] = self.held
            self.stats["state"] = "locked" if self.held else "h-lock"
            # Into the frame by line number, a run at a time: a run ends
            # with line 625, where the frame is finished and the next begins.
            cuts = list(np.flatnonzero(numbers == L) + 1)
            s0 = 0
            for s1 in cuts + ([count] if not cuts or cuts[-1] != count else []):
                idx = s0 + np.flatnonzero(numbers[s0:s1])
                li = numbers[idx] - 1
                self.frame[li] = norm[idx]
                self.starts[li] = starts[idx]
                self.frac[li] = frac[idx]
                self.valid[li] = ok[idx]
                if s1 in cuts and self.held:
                    out.append(self._finish())
                s0 = s1
            last = int(numbers[-1]) if numbers[-1] else None
            self.line_no = (last % L + 1) if last else None
        else:
            self.stats["state"] = "h-lock"
            self.stats["vLock"] = False

        self.line_next = float(pred[-1] + (fit[-1] if self.misses == 0 else 0.0)) + self.period
        # Real line rates are within a few ppm of 15 625 Hz (System I:
        # +-0.02 ppm): a period that wanders further is the fit chasing
        # noise, so it is held within 0.1 %.
        self.period = float(np.clip(self.period + 0.5 * slope, self.P * 0.999, self.P * 1.001))
        if resync:
            self._lost_at(self.line_next)
        return True

    def _lost_at(self, pos: float) -> None:
        """Acquire again from `pos`, the first line after a break. The line
        numbers go with it - how many lines went by in the break is not
        knowable to the line - and the next field sync restores them."""
        self.acq_from = pos
        self.line_next = None
        self.line_no = None
        self.locked = False
        self._break_vbi()

    def _break_vbi(self) -> None:
        """A break in the signal: no line already in the frame may be
        sliced with the lines after it, and the frame out says so. Without
        this a frame finished after a break carried the packets of the
        field before it and of the one after, as if they were one - the
        rows of a page whose header fell in the break went onto the page
        before (tests/test_teletext.py, MisAssembly)."""
        self.valid[:] = False
        self.gap = True

    # ------------------------------------------------------------- the frame
    def _finish(self) -> dict:
        fr = self.frame
        # Teletext first: it is cheap, and it says which blanked lines carry
        # data and so cannot be used to measure the noise.
        packets, has = self._teletext(fr)
        # Every line must be written again to be sliced again: one the next
        # frame does not reach (a break, a renumbering) is not that frame's.
        self.valid[:] = False
        gap, self.gap = self.gap, False
        self.stats["snr"] = self._snr(fr, has)
        if not self.picture:
            self.frames_out += 1
            return {"rgb": None, "stats": dict(self.stats), "composite": None, "ttx": packets, "gap": gap}
        rgb, colour = self._decode(fr)
        self.stats["colour"] = colour
        self.frames_out += 1
        self.last_rgb = rgb
        return {"rgb": rgb, "stats": dict(self.stats), "composite": fr.copy(), "ttx": packets, "gap": gap}

    def _teletext(self, fr: np.ndarray) -> tuple[list, dict]:
        """The packets on the frame's blanked lines (ohsatv/vbi.py), and
        {frame line (1-based): True} for every line that carried one."""
        if self.slicer is None:
            return [], {}
        # Only lines whose own sync was found this frame: a line left over
        # from an earlier frame, or never written since acquisition, is not
        # this frame's (a false packet came from one now and then: ~1 in 10 s
        # of a picture without teletext, at any CNR).
        lines = [ln for ln in self.slicer.lines if self.valid[ln - 1]]
        if not lines:
            return [], {}
        packets, found = self.slicer.slice(fr[[ln - 1 for ln in lines]])
        return packets, {ln: True for ln, f in zip(lines, found) if f}

    def _snr(self, fr: np.ndarray, has: dict) -> float:
        """Unweighted video SNR. Noise: the picture area of blanked lines
        8-15 and 321-328, which carry nothing in a plain PAL signal, so what
        is there is noise after the receive filter.

        Teletext uses those lines (vbi.DEFAULT_LINES fills 7-22 and
        320-335), and its data would read as noise - an SNR of ~4 dB on a
        perfect signal. Lines the slicer found teletext on are left out;
        if fewer than four are left, the noise is measured instead on the
        sync tips of the blanked lines (0.8-4.0 us after 0H, clear of both
        edges), which nothing but sync ever occupies. The receive noise is
        the same at the tip as on the porch after it - the AM detector is
        synchronous, the FM one's noise does not depend on the frequency
        it is at - which tests/test_teletext.py checks: with and without
        teletext the figure agrees within 1 dB from 15 to 35 dB CNR."""
        us = self.us
        a, c = self.first, self.count
        # The standard's quiet lines (ohsatv/lines.py: 8-15 and 321-328 for
        # 625; 405's own), less any the slicer found data on.
        quiet = [i for i in np.asarray(self.std.quiet).tolist() if not has.get(i + 1)]
        if len(quiet) >= 4 or not has:
            q = fr[quiet, a + int(2 * us):a + c - int(2 * us)]
        else:
            vb = [ln - 1 for ln in vbi.ALLOWED]
            q = fr[vb, int(round(0.8 * us)):int(round(4.0 * us))]
        sigma = float(np.std(q - q.mean(axis=1, keepdims=True)))
        return 20 * math.log10(pal.WHITE / max(sigma, 1e-6))

    def _decode(self, fr: np.ndarray) -> tuple[np.ndarray, bool]:
        a, c = self.first, self.count
        act = slice(a, a + c)
        order = self.order
        colour = False
        x = fr
        self.stats["burst"] = None
        if not self.std.whole(self.fs):
            # A 405-line line is never a whole number of samples, so the
            # sample nearest each line's start is up to half a sample
            # early or late, a different amount on every line: at 5 MS/s
            # +-0.1 us, a saw-tooth down every vertical edge. 625 lines take
            # that (a colour subcarrier's phase must be measured at the same
            # whole sample); a mono picture need not, so each line is moved
            # by its own fraction, exactly, in the frequency domain. The
            # shift wraps round the line's ends, which are sync and blanking.
            f = np.fft.rfftfreq(self.n, 1 / self.fs)
            ph = np.exp(2j * np.pi * np.outer(self.frac[order] / self.fs, f)).astype(np.complex64)
            x = np.empty_like(fr)
            x[order] = np.fft.irfft(np.fft.rfft(fr[order], axis=1) * ph, self.n, axis=1)
        if (self.std.colour and self.colour_mode != "mono" and self.fs / 2 > self.fsc + 0.3e6
                and self.chroma_bins.stop):
            # Absolute subcarrier phase at each line's 0H (float64: line
            # starts are sample counts since the stream began).
            th = np.exp(-2j * np.pi * ((self.starts * (self.fsc / self.fs)) % 1.0)).astype(np.complex64)
            # Chroma band first, positive frequencies only (the analytic
            # signal): demodulating the real composite directly would also
            # bring the subcarrier's negative-frequency image down to -2 fsc,
            # which at 10 MS/s aliases to +1.13 MHz - inside the chroma band.
            # The real line's rfft has every positive bin; the band is placed
            # in an otherwise empty spectrum for the inverse. Both 625-line
            # arrays are kept from frame to frame rather than allocated (and
            # the spectrum zeroed) afresh for each.
            if self._spec is None:
                self._spec = np.zeros((pal.LINES, self.n), np.complex64)
                self._chroma = np.empty((pal.LINES, self.n), np.complex64)
            S = self._spec
            S[:, self.chroma_bins] = np.fft.rfft(x, axis=1, norm="forward")[:, self.chroma_bins] * self.chroma_bp
            A = np.fft.ifft(S, axis=1, out=self._chroma)
            # The burst, demodulated: (sw V - jU) e^{j psi}. The line's
            # subcarrier phase is a constant across the line, so it multiplies
            # the burst's mean rather than every sample.
            z = (A[:, self.burst] * self.sc[self.burst]).mean(axis=1) * th
            burst_lines = _BURST_LINES
            z = np.where(burst_lines, z, 0)
            amp = np.abs(z[burst_lines])
            # The burst's amplitude (V; 0.15 sent) is the picture's
            # saturation, since chroma is not normalised to it: the channel's
            # loss at the subcarrier shows here first (docs/atv-research.md 17).
            self.stats["burst"] = float(np.median(amp))
            # Reference: sum of neighbouring bursts (both V-switch phases)
            # points along +90 degrees in this convention (pal.Encoder): a burst
            # demodulates to (sw + j) A/sqrt2.
            kern = np.ones(17)
            ref = np.convolve(z, kern, mode="same")
            refn = ref / np.maximum(np.abs(ref), 1e-12)
            rot = (np.conj(refn) * 1j).astype(np.complex64)          # exp(-j psi)
            zr = z * rot
            meas = np.where(zr.real >= 0, 1.0, -1.0)
            # The V-switch alternates every line in time, so it is one sign
            # for the whole frame times (-1)^line: take that sign by
            # majority, which also gives the lines without burst theirs.
            par = np.where(np.arange(pal.LINES) % 2 == 0, 1.0, -1.0)
            agree = float(np.mean(meas[burst_lines] * par[burst_lines]))
            sw = (1.0 if agree >= 0 else -1.0) * par
            alt = abs(agree)
            if np.median(amp) > 0.025 and alt > 0.6:
                colour = True
                # Only the active part of each line goes further. The PAL
                # delay line averages each line with the previous one of the
                # field - the previous one in time - and the V-switch
                # alternates line to line, so for line l: U = -(Im C[l] +
                # Im C[l-1]) / 2 and V = sw[l] (Re C[l] - Re C[l-1]) / 2.
                # Computed for every line from slices of one array and only
                # then gathered to picture order as float32 rows: gathering
                # rows of a complex64 array measured several times dearer.
                C = A[:, act] * self.sc[act]
                C *= (th * rot)[:, None]
                Ci, Cr = C.imag, C.real
                U = Ci[1:] + Ci[:-1]
                U *= np.float32(-0.5)
                V = Cr[1:] - Cr[:-1]
                V *= (np.float32(0.5) * sw[1:, None]).astype(np.float32)
                U, V = U[order - 1], V[order - 1]
                # Remove the chroma we found from the composite to get Y
                # (or, where luminance was band-limited below the chroma at
                # the sender, low-pass it there: that also drops the noise
                # between the two).
                if self.luma_lp is None:
                    Yp = x[order, act] - A.real[order, act]
                else:
                    Yp = self._luma(x[order])[:, act]
        if not colour:
            Yp = x[order, act] if self.luma_lp is None else self._luma(x[order])[:, act]
        out = np.empty((self.std.rows, c, 3), np.uint8)
        # R'G'B' straight to 8 bits, in float32 throughout (a float64 detour
        # here cost as much as the whole colour decoder).
        ky = np.float32(255.0 / pal.WHITE)
        if colour:
            Up, Vp = U, V
            # The rounding offset goes in once, on Y: R and B inherit it, and
            # so does G, (0.5 - KR 0.5 - KB 0.5) / KG being 0.5 exactly.
            y = Yp * ky
            y += np.float32(0.5)
            kb = np.float32(255.0 / (pal.WHITE * pal.U_W))
            kr = np.float32(255.0 / (pal.WHITE * pal.V_W))
            b = Up * kb
            b += y
            r = Vp * kr
            r += y
            g = (y - np.float32(pal.KR) * r - np.float32(pal.KB) * b) * np.float32(1 / pal.KG)
            # A thinned copy of the picture's U/V for the vectorscope.
            self.last_uv = (Up[::6, ::3].ravel().copy(), Vp[::6, ::3].ravel().copy())
            for i, ch in enumerate((r, g, b)):
                np.clip(ch, 0, 255, out=ch)
                out[..., i] = ch
        else:
            self.last_uv = None
            y = np.clip(Yp * ky + np.float32(0.5), 0, 255).astype(np.uint8)
            out[...] = y[..., None]
        return out, colour

    def _luma(self, x: np.ndarray) -> np.ndarray:
        return np.fft.irfft(np.fft.rfft(x, axis=1, norm="forward") * self.luma_lp, self.n, axis=1).astype(
            np.float32, copy=False)


def break_at(ok: np.ndarray, run: int = 6) -> int | None:
    """Where a batch of lines breaks: the first run of `run` missing lines
    after which most of the rest are missing too, with the lines before it
    (if any) mostly found. Random misses in noise do not look like that -
    they are scattered - so a weak signal is not cut up; a gap in the
    signal, or a jump in its timing, does. 0 means the whole batch is after
    the break."""
    bad = ~np.asarray(ok, bool)
    if len(bad) < run:
        return None
    w = np.lib.stride_tricks.sliding_window_view(bad, run).all(axis=1)
    idx = np.flatnonzero(w)
    if not len(idx):
        return None
    i = int(idx[0])
    if (i and bad[:i].mean() > 0.2) or bad[i:].mean() < 0.8:
        return None
    return i


# Lines that carry burst (pal.burst_line), as a mask over frame lines.
_BURST_LINES = np.array([pal.burst_line(i + 1) for i in range(pal.LINES)])
