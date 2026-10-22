"""The narrow formats' digital sound: mu-law, SECDED and the per-frame
packing between 48 kHz and a line-locked rate (ohsatv/pcm.py)."""
import unittest

import numpy as np

from test_chain import tone_quality
from ohsatv import pcm


class Secded(unittest.TestCase):
    def test_every_single_error_is_corrected_and_every_double_detected(self):
        codes = np.arange(256, dtype=np.uint8)
        cw = pcm.secded_encode(codes)
        self.assertEqual(cw.shape, (256, pcm.CODE_BITS))
        got, good, fixed = pcm.secded_decode(cw)
        self.assertTrue((got == codes).all() and good.all() and not fixed.any())
        for i in range(pcm.CODE_BITS):
            bad = cw.copy()
            bad[:, i] ^= 1
            got, good, fixed = pcm.secded_decode(bad)
            self.assertTrue((got == codes).all() and good.all(), f"bit {i}")
            # (an error in the overall parity bit leaves the data intact:
            # nothing to correct)
            self.assertEqual(bool(fixed.all()), i < pcm.CODE_BITS - 1)
        for i in range(pcm.CODE_BITS):
            for j in range(i + 1, pcm.CODE_BITS):
                bad = cw.copy()
                bad[:, i] ^= 1
                bad[:, j] ^= 1
                _, good, _ = pcm.secded_decode(bad)
                self.assertFalse(good.any(), f"bits {i}, {j}")


class MuLaw(unittest.TestCase):
    def test_a_tone_survives_with_better_than_35_db(self):
        t = np.arange(4800) / 48000
        for amp in (0.5, 0.05):
            x = amp * np.sin(2 * np.pi * 1000 * t)
            y = pcm.mulaw_decode(pcm.mulaw_encode(x))
            snr = 10 * np.log10(np.sum(x ** 2) / np.sum((y - x) ** 2))
            self.assertGreater(snr, 35.0, f"amplitude {amp}")

    def test_sign_and_full_scale(self):
        y = pcm.mulaw_decode(pcm.mulaw_encode(np.array([-1.0, 0.0, 1.0])))
        np.testing.assert_allclose(y, [-1.0, 0.0, 1.0], atol=1e-6)


class Packing(unittest.TestCase):
    def tone_through(self, per_frame: int, good=None) -> np.ndarray:
        pack, unpack = pcm.FramePacker(per_frame), pcm.FrameUnpacker(per_frame)
        t = np.arange(48000) / 48000
        tone = (0.5 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
        out = []
        for k in range(25):
            x = pack(tone[k * 1920:(k + 1) * 1920])
            self.assertEqual(len(x), per_frame)
            out.append(unpack(pcm.mulaw_decode(pcm.mulaw_encode(x)), good))
        return np.concatenate(out)

    def test_a_frame_of_48_khz_sound_becomes_exactly_one_frame_of_samples_and_back(self):
        for per_frame, floor in ((625, 35.0), (305, 30.0)):
            y = self.tone_through(per_frame)
            self.assertAlmostEqual(len(y), 48000, delta=20)
            f, sinad = tone_quality(y)
            self.assertAlmostEqual(f, 1000.0, delta=2)
            self.assertGreater(sinad, floor, f"{per_frame} a frame")

    def test_erased_samples_are_concealed_not_played(self):
        good = np.ones(625, bool)
        good[::50] = False                 # 2 % of samples flagged as double errors
        y = self.tone_through(625, good)
        self.assertTrue(np.isfinite(y).all())
        self.assertGreater(tone_quality(y)[1], 20.0)


if __name__ == "__main__":
    unittest.main()
