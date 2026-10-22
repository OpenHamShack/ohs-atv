"""The generated PAL waveform, measured: line and field timing, burst,
subcarrier, the 8-field sequence. Every number is measured from the samples
the encoder produces, against the standard's figures written out here (not
read back from ohsatv.pal's own constants)."""
import unittest

import numpy as np

from helpers import resample_width  # noqa: F401  (path setup)
from ohsatv import pal, testcard

RATES = (8e6, 10e6, 12.5e6, 16e6, 20e6)
FSC = 4_433_618.75


def crossings(x: np.ndarray, level: float, falling: bool) -> np.ndarray:
    """Sub-sample positions where x crosses `level` (linear interpolation)."""
    a, b = x[:-1], x[1:]
    if falling:
        idx = np.flatnonzero((a >= level) & (b < level))
    else:
        idx = np.flatnonzero((a < level) & (b >= level))
    return idx + (a[idx] - level) / (a[idx] - b[idx])


def pulses(signal: np.ndarray, fs: float) -> list[tuple[float, float]]:
    """(start_us, width_us) of every sync pulse, at the 50 % (-0.15 V) points."""
    f = crossings(signal, -0.15, True)
    r = crossings(signal, -0.15, False)
    out = []
    for s in f:
        e = r[r > s]
        if len(e):
            out.append((s / fs * 1e6, (e[0] - s) / fs * 1e6))
    return out


class LineTiming(unittest.TestCase):
    def test_normal_line_at_every_rate(self):
        grey = np.full((576, 720, 3), 0.5, np.float32)
        for fs in RATES:
            with self.subTest(fs=fs):
                enc = pal.Encoder(fs, colour=False)
                fr = enc.encode(grey, 0)
                n = fs / 15625
                self.assertEqual(fr.shape, (625, round(n)))
                sig = fr.reshape(-1)
                line = 100                             # a picture line of field 1
                seg = sig[(line - 1) * fr.shape[1] - 50:(line + 1) * fr.shape[1]]
                ps = [p for p in pulses(seg, fs)]
                self.assertTrue(ps)
                start, width = ps[0]
                # 0H falls exactly on the line's first sample.
                self.assertAlmostEqual(start, 50 / fs * 1e6, delta=0.02)
                self.assertAlmostEqual(width, 4.7, delta=0.05)        # BT.470: 4.7 +- 0.2 us
                # Line period: the next line's sync is 64 us later.
                self.assertAlmostEqual(ps[1][0] - ps[0][0], 64.0, delta=0.01)
                # Picture: 50 % of grey (0.175 V) up at 10.5 us, down at 62.5 us.
                body = sig[(line - 1) * fr.shape[1]:line * fr.shape[1]]
                up = crossings(body, 0.175, False) / fs * 1e6
                dn = crossings(body, 0.175, True) / fs * 1e6
                self.assertAlmostEqual(up[0], 10.5, delta=0.06)
                # front porch 1.5 us (to within half a sample of the picture's edge)
                self.assertAlmostEqual(dn[-1], 62.5, delta=max(0.06, 0.6 / fs * 1e6))

    def test_edges_are_not_instant(self):
        fs = 20e6
        fr = pal.Encoder(fs, colour=False).encode(np.zeros((576, 720, 3)), 0)
        x = fr.reshape(-1)[100 * 1280 - 40:100 * 1280 + 40]
        t10 = crossings(x, -0.03, True)[0]
        t90 = crossings(x, -0.27, True)[0]
        rise = (t90 - t10) / fs * 1e6
        self.assertGreater(rise, 0.15)
        self.assertLess(rise, 0.35)                       # BT.470: 0.25 +- 0.05 us


class FieldStructure(unittest.TestCase):
    def setUp(self):
        self.fs = 10e6
        self.n = 640
        self.fr = pal.Encoder(self.fs, colour=False).encode(np.zeros((576, 720, 3)), 0)
        self.sig = np.concatenate([self.fr.reshape(-1)] * 2)

    def field_pulses(self, first_line: int, last_line: int):
        a = (first_line - 1) * self.n - 10
        b = last_line * self.n
        return [(round(s - 10 / self.fs * 1e6, 2), round(w, 2))
                for s, w in pulses(self.sig[a:b], self.fs)]

    def kinds(self, ps):
        out = []
        for _, w in ps:
            out.append("e" if abs(w - 2.35) < 0.1 else "h" if abs(w - 4.7) < 0.1 else
                       "b" if abs(w - 27.3) < 0.1 else f"?{w}")
        return "".join(out)

    def test_field_one_vertical_interval(self):
        # Lines 622..7: h, then 5 equalising, 5 broad, 5 equalising, then line syncs.
        ps = self.field_pulses(622, 625 + 7)
        self.assertEqual(self.kinds(ps), "hh" + "eeeee" + "bbbbb" + "eeeee" + "hh")
        starts = [s for s, _ in ps]
        # Pulses of the field-sync group are half a line (32 us) apart.
        self.assertTrue(np.allclose(np.diff(starts[1:17]), 32.0, atol=0.01))
        # The first broad pulse starts at 0H of line 1, four lines after line 622 starts.
        self.assertAlmostEqual(starts[7], 4 * 64.0, delta=0.01)

    def test_field_two_vertical_interval(self):
        ps = self.field_pulses(310, 320)
        # 310 line sync; 311-318 field sync; 319 and 320 line sync again.
        self.assertEqual(self.kinds(ps), "h" + "eeeee" + "bbbbb" + "eeeee" + "hh")
        # The first broad pulse is in the middle of line 313 (field 2 is offset half a line).
        self.assertAlmostEqual(ps[6][0], 3 * 64.0 + 32.0, delta=0.01)

    def test_frame_and_field_rates(self):
        # 625 lines of 64 us: 40 ms a frame, two fields of 312.5 lines.
        self.assertEqual(self.fr.size / self.fs, 0.04)
        # First broad pulse of field 2 (in line 313) to that of the next
        # field 1 (line 1 of the following frame): 312.5 lines.
        f2 = self.field_pulses(313, 314)
        b2 = [s for s, w in f2 if abs(w - 27.3) < 0.1][0]
        f1 = self.field_pulses(626, 627)
        b1 = [s for s, w in f1 if abs(w - 27.3) < 0.1][0] + (626 - 313) * 64.0
        self.assertAlmostEqual(b1 - b2, 312.5 * 64.0, delta=0.01)

    def test_picture_lines(self):
        white = np.ones((576, 720, 3), np.float32)
        fr = pal.Encoder(self.fs, colour=False).encode(white, 0)
        lit = [i + 1 for i in range(625) if fr[i].max() > 0.6]
        self.assertEqual(lit[0], 23)
        self.assertEqual(lit[-1], 623)
        self.assertEqual(len(lit), 576)
        # Line 23 is lit only in its second half, 623 only in its first.
        self.assertLess(fr[22, :300].max(), 0.01)
        self.assertGreater(fr[22, 400:].max(), 0.69)
        self.assertGreater(fr[622, 150:300].max(), 0.69)
        self.assertLess(fr[622, 340:].max(), 0.01)


class Colour(unittest.TestCase):
    def test_subcarrier_frequency_and_eight_field_sequence(self):
        fs = 16e6
        enc = pal.Encoder(fs)
        img = np.zeros((576, 720, 3), np.float32)
        # A colour with no V component (R' = Y'): PAL's V-switch moves V
        # half a line frequency off the subcarrier, U stays on it.
        img[:] = (0.48134, 0.4, 0.9)
        frames = [enc.encode(img, k) for k in range(5)]
        np.testing.assert_array_equal(frames[4], frames[0])      # 4 frames = 8 fields
        self.assertFalse(np.array_equal(frames[1], frames[0]))
        x = np.concatenate([f.reshape(-1) for f in frames[:4]])
        spec = np.abs(np.fft.rfft(x))
        f = np.fft.rfftfreq(len(x), 1 / fs)                       # 6.25 Hz bins
        band = (f > 4.3e6) & (f < 4.6e6)
        peak = f[band][np.argmax(spec[band])]
        self.assertLess(abs(peak - FSC), 30.0)

    def test_burst(self):
        fs = 20e6
        fr = pal.Encoder(fs).encode(np.zeros((576, 720, 3), np.float32), 0)
        us = fs / 1e6
        t = np.arange(fr.shape[1])
        for line in (100, 101):
            x = fr[line - 1]
            seg = x[int(5.0 * us):int(8.5 * us)]
            env_on = np.flatnonzero(np.abs(seg) > 0.05)
            start = (env_on[0] / us) + 5.0
            self.assertAlmostEqual(start, 5.6, delta=0.15)
            mid = x[int(6.0 * us):int(7.6 * us)]
            self.assertAlmostEqual(np.abs(mid).max(), 0.15, delta=0.01)    # 300 mV p-p
            # 10 cycles: count the positive peaks above half amplitude (the
            # gate ramps over about a cycle at each end).
            gate = x[int(5.4 * us):int(8.4 * us)]
            pk = np.flatnonzero((gate[1:-1] > gate[:-2]) & (gate[1:-1] >= gate[2:]) & (gate[1:-1] > 0.075))
            self.assertIn(len(pk), (9, 10, 11))
        # The burst swings +-45 degrees about -U from line to line (PAL's V-switch):
        # demodulate two consecutive lines against the same absolute subcarrier.
        ph = []
        for line in (100, 101):
            k = (line - 1) * fr.shape[1] + t
            ref = np.exp(-2j * np.pi * FSC * k / fs)
            seg = slice(int(6.0 * us), int(7.6 * us))
            ph.append(np.angle(np.sum(fr[line - 1][seg] * ref[seg]), deg=True))
        d = (ph[1] - ph[0] + 180) % 360 - 180
        self.assertAlmostEqual(abs(d), 90.0, delta=3.0)

    def test_no_colour_when_the_rate_cannot_carry_it(self):
        enc = pal.Encoder(8e6)
        self.assertFalse(enc.colour)
        fr = enc.encode(np.full((576, 720, 3), (0.8, 0.1, 0.1), np.float32), 0)
        # A mono line of a flat colour is flat across the picture.
        self.assertLess(np.ptp(fr[100, 100:400]), 0.01)

    def test_yuv_round_trip(self):
        rgb = np.random.default_rng(1).random((10, 3)).astype(np.float32)
        back = pal.yuv_to_rgb(*pal.rgb_to_yuv(rgb))
        np.testing.assert_allclose(back, rgb, atol=1e-5)


class TestCard(unittest.TestCase):
    def test_card_shows_the_callsign_and_has_the_gratings(self):
        a = testcard.test_card("GM4BRI", "IO86")
        b = testcard.test_card("M0XYZ", "IO86")
        self.assertEqual(a.shape, (576, 720, 3))
        self.assertGreaterEqual(a.min(), 0.0)
        self.assertLessEqual(a.max(), 1.0)
        diff = np.abs(a - b).max(axis=2) > 0.2
        rows = np.flatnonzero(diff.any(axis=1))
        # Only the ident box changes with the callsign.
        self.assertGreater(len(rows), 30)
        self.assertLess(rows.max() - rows.min(), 120)

    def test_gratings_have_the_frequency_they_are_labelled(self):
        fs = 20e6
        card = testcard.test_card("GM4BRI")
        fr = pal.Encoder(fs, colour=False).encode(card, 0)
        # Find a picture line through the gratings: the row with the most
        # high-frequency energy.
        rows = [pal.picture_row(i + 1) for i in range(625)]
        best = None
        for i, r in enumerate(rows):
            if r is None:
                continue
            x = fr[i]
            e = np.sum(np.abs(np.diff(x)))
            if best is None or e > best[0]:
                best = (e, i)
        x = fr[best[1]]
        spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
        f = np.fft.rfftfreq(len(x), 1 / fs)
        for mhz in testcard.GRATINGS_MHZ:
            band = (f > (mhz - 0.4) * 1e6) & (f < (mhz + 0.4) * 1e6)
            peak = f[band][np.argmax(spec[band])]
            self.assertAlmostEqual(peak / 1e6, mhz, delta=0.1)


if __name__ == "__main__":
    unittest.main()
