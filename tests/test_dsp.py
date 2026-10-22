"""The streaming filters against the plain computation they stand for.

The receive chain's tests (test_chain.py) judge pictures and sound; these
pin the filters' own numerics, which were reworked for speed (docs/PERF.md)
in ways a picture might hide - a phase step at an FFT frame boundary, or a
mixer that is continuous within a call and not across calls.
"""
import unittest

import numpy as np

import helpers  # noqa: F401 - puts the repo on sys.path
from ohsatv.dsp.filters import Channel, Ols, design, taper
from ohsatv.modem import audio_deemph, audio_preemph


def pieces(x, sizes):
    out, i = [], 0
    for n in sizes:
        out.append(x[i:i + n])
        i += n
    return out


class ShiftingFilter(unittest.TestCase):
    def test_filter_and_shift_equal_a_convolution_then_a_mixer(self):
        fs = 10e6
        rng = np.random.default_rng(5)
        x = (rng.standard_normal(70_000) + 1j * rng.standard_normal(70_000)).astype(np.complex64)
        taps = design(lambda f: taper(np.abs(f + 2.4e6), 2.0e6, 0.4e6), fs, 127)
        ref = np.convolve(x.astype(np.complex128), taps)[:len(x)]
        f = Ols(taps, shift=-2.4e6, fs=fs)
        self.assertAlmostEqual(f.shift, -2.4e6, delta=fs / f.nfft / 2)
        # Odd block lengths, one shorter than an FFT frame and one spanning
        # several: the mixer's phase must carry across both kinds of seam.
        got = np.concatenate([f(p) for p in pieces(x, [1000, 8191, 30_017, 30_792])])
        j = np.arange(len(x))
        want = ref * np.exp(-2j * np.pi * f.shift * j / fs)
        err = np.abs(got - want).max() / np.abs(want).max()
        self.assertLess(err, 1e-4)

    def test_no_shift_is_the_plain_filter(self):
        rng = np.random.default_rng(6)
        x = (rng.standard_normal(20_000) + 1j * rng.standard_normal(20_000)).astype(np.complex64)
        taps = design(lambda f: taper(np.abs(f), 1.0e6, 0.4e6), 5e6, 63)
        got = Ols(taps)(x)
        want = np.convolve(x.astype(np.complex128), taps)[:len(x)]
        self.assertLess(np.abs(got - want).max() / np.abs(want).max(), 1e-4)


class NarrowChannel(unittest.TestCase):
    """Channel (the sound carrier's extractor) is a filter, a mixer and a
    decimator done inside one FFT, folding only the bins where the filter
    is above -80 dB."""

    def reference(self, ch, x):
        h = design(lambda f: taper(np.abs(f - ch.centre), ch.bw / 2, ch.bw * 0.3), ch.fs, ch.p)
        z = np.convolve(np.asarray(x, np.complex128), h)[:len(x)]
        j = np.arange(len(x))
        z = z * np.exp(-2j * np.pi * ch.centre * j / ch.fs)
        # The first output is stream sample skip*dec - (p-1) of the filter's.
        e = -(-(ch.p - 1) // ch.dec) * ch.dec - (ch.p - 1)
        return z[e::ch.dec]

    def check(self, ch, x):
        got = np.concatenate([ch(p) for p in pieces(x, [5000, 40_001, 70_000, 84_999])])
        want = self.reference(ch, x)[:len(got)]
        self.assertGreater(len(got), 0.9 * len(x) / ch.dec)
        err = np.abs(got - want).max() / np.abs(want).max()
        self.assertLess(err, 1e-4)

    def test_complex_input_matches_filter_mix_decimate(self):
        fs = 10e6
        rng = np.random.default_rng(7)
        t = np.arange(200_000)
        x = (np.exp(2j * np.pi * 3.62e6 * t / fs) + 0.3 * (rng.standard_normal(len(t))
                                                             + 1j * rng.standard_normal(len(t))))
        self.check(Channel(fs, 3.6e6, 220e3, 40), x.astype(np.complex64))

    def test_real_input_uses_half_the_spectrum_and_gives_the_same(self):
        # The FM sound subcarrier, in the discriminator's real output.
        fs = 16e6
        rng = np.random.default_rng(8)
        t = np.arange(200_000)
        x = (np.cos(2 * np.pi * 6.01e6 * t / fs) + 0.3 * rng.standard_normal(len(t))).astype(np.float32)
        ch = Channel(fs, 6.0e6, 220e3, 64, real=True)
        self.assertTrue(ch.real)
        self.check(ch, x)


class Emphasis(unittest.TestCase):
    def test_de_emphasis_is_the_one_pole_recursion_across_blocks(self):
        # Computed in closed form, in long chunks; checked here against the
        # recursion itself, sample by sample, over blocks of awkward lengths.
        rate, tau = 250e3, 50e-6
        rng = np.random.default_rng(9)
        x = rng.standard_normal(30_000)
        st = [0.25]
        got = np.concatenate([audio_deemph(b, rate, tau, st) for b in pieces(x, [1, 999, 4000, 25_000])])
        p = np.exp(-1 / (tau * rate))
        want, y = np.empty(len(x)), 0.25
        for i, v in enumerate(x):
            y = p * y + (1 - p) * v
            want[i] = y
        self.assertLess(np.abs(got - want).max(), 1e-5)

    def test_de_emphasis_undoes_pre_emphasis(self):
        rate, tau = 48e3, 50e-6
        x = np.sin(2 * np.pi * 1000 * np.arange(9600) / rate)
        pre, de = [0.0], [0.0]
        y = np.concatenate([audio_deemph(audio_preemph(b, rate, tau, pre), rate, tau, de)
                            for b in pieces(x, [1920] * 5)])
        self.assertLess(np.abs(y - x).max(), 1e-5)


if __name__ == "__main__":
    unittest.main()
