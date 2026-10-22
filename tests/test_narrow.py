"""The narrow formats through the whole chain, in the simulator.

Encoder -> modulator -> the AD936x's filtering at both ends, noise, a 19 kHz
carrier offset (and, where said, a break in the signal) -> demodulator ->
decoder, with the channel and the measurements of tools/narrow_eval.py
(the evaluation that chose which formats to keep - DESIGN.md "Narrow
formats"). CNR is over the whole sample band, as in test_chain.py; the
narrow mono picture these are compared with locks down to ~12 dB at 5 MS/s.
"""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import narrow_eval as ev  # noqa: E402
from helpers import first_claim  # noqa: E402
from ohsatv import mac, modem, narrow, pal, profiles, sysa  # noqa: E402
from ohsatv.dsp.resample import Resampler  # noqa: E402


def run(fmt, fs, cnr, img=ev.CARD, frames=8, **kw):
    r = ev.chain(fmt, "fm", fs, cnr, img, frames, **kw)
    return r["frames"], r["audio"], r["dec"]


class MiniMac(unittest.TestCase):
    def test_colour_and_sound_at_the_usb_pluto_rates(self):
        for fs in (5e6, 6.25e6):
            with self.subTest(fs=fs):
                frames, audio, _ = run("mac", fs, 10.0)
                st = frames[-1]["stats"]
                self.assertGreater(st["hLock"], 0.98)
                self.assertTrue(st["vLock"])
                self.assertTrue(st["colour"])
                self.assertGreater(ev.luma_psnr(frames[-1]["rgb"], ev.CARD), 16.0)
                self.assertGreater(ev.sinad(audio), 35.0)
                bars, _, _ = run("mac", fs, 10.0, ev.BARS, 5, seed=2)
                hue, sat = ev.bar_colour(bars[-1]["rgb"])
                self.assertLess(hue, 3.0)
                self.assertAlmostEqual(sat, 1.0, delta=0.08)

    def test_holds_lock_and_sound_well_below_the_mono_pictures_threshold(self):
        # At 6 dB the narrow mono picture has lost lock (~12 dB, 5 MS/s).
        frames, audio, _ = run("mac", 5e6, 6.0)
        self.assertGreater(frames[-1]["stats"]["hLock"], 0.95)
        self.assertTrue(frames[-1]["stats"]["vLock"])
        self.assertGreater(ev.sinad(audio), 25.0)

    def test_a_break_costs_little_more_than_itself(self):
        # The scrambled data numbers the lines, so after a break the picture
        # is back in place within ~30 lines - not at the next field sync.
        lost = ev.resync("mac", "fm", 5e6)
        self.assertLess(lost["gap"], 4.0)           # the gap itself is 2 ms
        self.assertLess(lost["slip"], 2.0)

    def test_stays_inside_the_sample_band(self):
        o = ev.occupied("mac", "fm", 5e6)
        self.assertLess(o["outside_dbc"], -45.0)
        self.assertLess(o["obw99"], 0.5 * 5e6)

    def test_scrambler_numbers_lines_uniquely(self):
        # Any run of lines descrambles validly at one line number only: no
        # two line numbers share a stretch of the sequence.
        s = mac.SCRAMBLE.astype(np.int64)
        words = [tuple(s[(i + np.arange(4)) % pal.LINES].ravel()) for i in range(pal.LINES)]
        self.assertEqual(len(set(words)), pal.LINES)


class LowPal(unittest.TestCase):
    def test_colour_and_sound_at_the_mono_pictures_threshold(self):
        for fs in (5e6, 6.25e6):
            with self.subTest(fs=fs):
                frames, audio, _ = run("lowpal", fs, 14.0)
                st = frames[-1]["stats"]
                self.assertGreater(st["hLock"], 0.95)
                self.assertTrue(st["vLock"])
                self.assertTrue(st["colour"])
                self.assertGreater(ev.sinad(audio), 25.0)
                bars, _, _ = run("lowpal", fs, 14.0, ev.BARS, 5, seed=2)
                hue, sat = ev.bar_colour(bars[-1]["rgb"])
                self.assertLess(hue, 6.0)
                self.assertAlmostEqual(sat, 1.0, delta=0.1)

    def test_stays_inside_the_sample_band(self):
        for fs in (5e6, 6.25e6):
            self.assertLess(ev.occupied("lowpal", "fm", fs)["outside_dbc"], -39.0)

    def test_a_transmit_gap_costs_little_more_than_itself(self):
        # A 2 ms gap in the middle of a received block cost a whole picture
        # (~40 ms) from fbd177f until 5 October: the search after the break
        # judged "the break has gone on too long" by what was left in the
        # buffer, and jumped to the newest samples at its first failed fold.
        # PAL at 6.25 MS/s goes through the same decoder and showed it too.
        for fmt, fs in (("lowpal", 5e6), ("pal", 6.25e6)):
            with self.subTest(fmt=fmt, fs=fs):
                self.assertLess(ev.resync(fmt, "fm", fs)["gap"], 10.0)


class CrossRate(unittest.TestCase):
    def test_a_5_msps_usb_pluto_is_received_at_6_25_and_16_msps(self):
        # Each format is defined in time, not samples: the nano sends at 5
        # MS/s, and a radio receiving at 6.25 - or a LibreSDR at 16 with the
        # same narrow parameters, for the wider view - decodes it.
        for fmt in ("mac", "lowpal"):
            for rx_fs, up, down in ((6.25e6, 5, 4), (16e6, 16, 5)):
                with self.subTest(fmt=fmt, rx_fs=rx_fs):
                    p = dict(ev.PROFILE[5e6], videoFormat=fmt)
                    enc, mod = narrow.make_encoder(5e6, "fm", p), narrow.make_mod(5e6, "fm", p)
                    dem, dec = narrow.make_demod(rx_fs, "fm", p), narrow.make_decoder(rx_fs, "fm", p)
                    rr, ri = Resampler(up, down), Resampler(up, down)
                    au = ev.tone(8)
                    frames, sound = [], []
                    for k in range(8):
                        a = au[k * ev.N_A:(k + 1) * ev.N_A]
                        iq = mod(enc.encode(ev.CARD, k, a), None if enc.audio_in_video else a)
                        x = (rr(iq.real) + 1j * ri(iq.imag)).astype(np.complex64)
                        got = dec.feed(dem(x))
                        frames += got
                        sound += [f["audio"] for f in got if "audio" in f] + [dem.audio(x)]
                    st = frames[-1]["stats"]
                    self.assertTrue(st["vLock"] and st["colour"])
                    self.assertGreater(ev.luma_psnr(frames[-1]["rgb"], ev.CARD), 18.0)
                    self.assertGreater(ev.sinad(np.concatenate(sound)), 35.0)


class NoSignal(unittest.TestCase):
    """Receiver noise alone never claims a field or gives a picture: at the
    nano's rates, and at 16 MS/s - the LibreSDR listening with the narrow
    parameters, where narrow colour PAL's noise claimed a field until the
    decoder asked for line lock first (ohsatv/decoder.py, CLAIM_H). Mini-MAC
    numbers its lines from their data and never did; this keeps it so."""

    def check(self, fmt, fs, seed, blocks=20):
        p = dict(ev.PROFILE[5e6], videoFormat=fmt)
        dem, dec = narrow.make_demod(fs, "fm", p), narrow.make_decoder(fs, "fm", p)
        self.assertIsNone(first_claim(dem, dec, fs, blocks, seed))

    def test_narrow_colour_pal(self):
        # At 16 MS/s seeds 4 and 6 claimed a field at blocks 9 and 12 before.
        for fs, seed in ((5e6, 1), (6.25e6, 1), (16e6, 4), (16e6, 6)):
            with self.subTest(fs=fs, seed=seed):
                self.check("lowpal", fs, seed)

    def test_mini_mac(self):
        for fs in (5e6, 6.25e6, 16e6):
            with self.subTest(fs=fs):
                self.check("mac", fs, 1)


class Detect(unittest.TestCase):
    def test_each_format_is_recognised_from_40_ms(self):
        for fs in (5e6, 6.25e6):
            for fmt in ("pal", "lowpal", "mac"):
                with self.subTest(fs=fs, fmt=fmt):
                    p = dict(ev.PROFILE[fs], videoFormat=fmt)
                    enc, mod = narrow.make_encoder(fs, "fm", p), narrow.make_mod(fs, "fm", p)
                    a = ev.tone(1)
                    iq = mod(enc.encode(ev.CARD, 0, a), None if enc.audio_in_video else a)
                    rng = np.random.default_rng(4)
                    s = np.sqrt(np.mean(np.abs(iq) ** 2) / 10 ** 0.8 / 2)          # 8 dB CNR
                    iq = iq + s * (rng.standard_normal(len(iq)) + 1j * rng.standard_normal(len(iq)))
                    self.assertEqual(narrow.detect(iq, fs)[0], fmt)

    def look(self, fmt, fs, p, img, cnr, seed=4):
        p = dict(p, videoFormat=fmt)
        enc, mod = narrow.make_encoder(fs, "fm", p), narrow.make_mod(fs, "fm", p)
        a = ev.tone(1)
        iq = mod(enc.encode(img, 0, a), None if enc.audio_in_video else a)
        rng = np.random.default_rng(seed)
        s = np.sqrt(np.mean(np.abs(iq) ** 2) / 10 ** (cnr / 10) / 2)
        iq = iq + s * (rng.standard_normal(len(iq)) + 1j * rng.standard_normal(len(iq)))
        return iq.astype(np.complex64)

    def test_standard_pal_at_wide_rates_is_never_taken_for_narrow_colour_pal(self):
        # At 16 and 20 MS/s a PAL picture's line harmonics run through 2.2
        # MHz; measured against the median of its neighbours they read as
        # Low-PAL's subcarrier (3-5 dB on the card, 22 on a grey mono
        # picture). A receiver that re-checks a locked picture would then
        # have switched a genuine PAL picture away.
        grey = np.full((576, 720, 3), 0.5, np.float32)
        wide = {16e6: dict(fmDeviation=4e6, fmVideoBw=5e6, fmSub=6e6), 20e6: dict(fmDeviation=7e6, fmVideoBw=5e6,
                                                                                   fmSub=6e6)}
        for fs, p in wide.items():
            for name, img in (("card", ev.CARD), ("bars", ev.BARS), ("grey", grey)):
                for cnr in (40.0, 30.0, 20.0):
                    with self.subTest(fs=fs, img=name, cnr=cnr):
                        iq = self.look("pal", fs, p, img, cnr)
                        self.assertEqual(narrow.detect(iq, fs)[0], "pal")
                        self.assertEqual(narrow.detect(iq, fs, sure=True)[0], "pal")

    def test_sure_names_the_narrow_formats_with_a_margin_or_not_at_all(self):
        for fmt in ("lowpal", "mac"):
            with self.subTest(fmt=fmt):
                iq = self.look(fmt, 5e6, ev.PROFILE[5e6], ev.CARD, 10.0)
                self.assertEqual(narrow.detect(iq, 5e6, sure=True)[0], fmt)
        # Low-PAL near its threshold: the subcarrier is there but not by the
        # margin. Not sure is an answer of None - never "pal".
        iq = self.look("lowpal", 5e6, ev.PROFILE[5e6], ev.CARD, 5.0)
        self.assertIn(narrow.detect(iq, 5e6, sure=True)[0], (None, "lowpal"))

    def test_noise_is_nothing(self):
        rng = np.random.default_rng(5)
        n = 200_000
        self.assertIsNone(narrow.detect(rng.standard_normal(n) + 1j * rng.standard_normal(n), 5e6)[0])


class Plans(unittest.TestCase):
    def test_the_offered_formats_fit_the_usb_profile_and_say_they_are_not_standard(self):
        for fs in (5e6, 6.25e6):
            # The FM formats: 405 lines are AM's, and a standard.
            for fmt in (f for f in narrow.OFFERED if "fm" in narrow.SYSTEMS[f]):
                pl = narrow.plan(fs, "fm", dict(ev.PROFILE[fs], videoFormat=fmt))
                self.assertTrue(pl["fits"], (fs, fmt, pl["why"]))
                self.assertEqual(narrow.NOT_STANDARD in pl["why"], fmt != "pal")
                self.assertEqual(pl["colour"], fmt != "pal")


class FmImages(unittest.TestCase):
    """FM-ATV's images on the transmitters that send it (modem.fm_tx_image;
    docs/atv-research.md 24): which plans the plan card and the transmit
    panel warn about, on the profiles' own settings. tests/test_chain.py
    holds the Pluto's figures to the whole signal."""

    def plan(self, prof, **extra):
        fs = prof["fmRate"]
        return fs, narrow.plan(fs, "fm", dict(prof, **extra))

    def test_a_pluto_warns_only_where_a_subcarrier_sits_at_0_44_fs(self):
        P = profiles
        warned = {}
        for name, prof, extra in (("USB", P.USB, {}), ("USB Low-PAL", P.USB, {"videoFormat": "lowpal"}),
                                  ("USB Mini-MAC", P.USB, {"videoFormat": "mac"}), ("USB_TX8", P.USB_TX8, {}),
                                  ("USB_RX8", P.USB_RX8, {}), ("ETH16", P.ETH16, {}),
                                  ("ETH16 NICAM", P.ETH16, {"nicam": True, "fmDeviation": 3.2e6}),
                                  ("FULL", P.FULL, {}), ("FULL NICAM", P.FULL, {"nicam": True})):
            fs, pl = self.plan(prof, **extra)
            panel = sysa.tx_image_warning(pl, "pluto", fs, 1255e6)
            card = [w for w in pl["why"] if w.startswith("a Pluto (AD936x)")]
            self.assertEqual(panel is not None, bool(card), name)
            warned[name] = None if panel is None else round(pl["txImage"]["db"], 1)
            if pl["txImage"] is None:
                # Nothing near fs/2 to image: no subcarrier, no NICAM.
                self.assertIsNone(pl["sub"], name)
                self.assertIsNone(pl["nicam"], name)
        self.assertEqual({k for k, v in warned.items() if v is not None}, {"USB Low-PAL", "USB_RX8"})
        # docs/atv-research.md 24's whole-signal figures, -37.4 and -38.8.
        self.assertAlmostEqual(warned["USB Low-PAL"], -37.4, delta=0.5)
        self.assertAlmostEqual(warned["USB_RX8"], -38.8, delta=0.5)
        fs, pl = self.plan(P.USB_RX8)
        self.assertIn("1248.00 MHz, outside the channel, about 39 dBc in 100 kHz",
                      sysa.tx_image_warning(pl, "pluto", fs, 1255e6))
        # A radio that is neither, the simulator, is not warned about.
        self.assertIsNone(sysa.tx_image_warning(pl, "sim", fs, 1255e6))

    def test_a_hackrf_on_its_own_model(self):
        # No correction, and no half-band: its images meet only the
        # converter's hold and the 15 MHz filter libhackrf picks at 20 MS/s,
        # which take the 6 MHz subcarrier's 14 MHz out (the model's 20 dB
        # shortfall counted) to -47 dBc. Its profile is not warned about;
        # a HackRF at a Pluto's 5 MS/s, its filter 3.5 MHz, is.
        P = profiles
        for extra in ({}, {"nicam": True}):
            fs, pl = self.plan(P.FULL, **extra)
            self.assertIsNone(sysa.tx_image_warning(pl, "hackrf", fs, 1255e6), extra)
            im = modem.fm_tx_image(fs, pl["sub"], pl["subIndex"], pl["nicam"] is not None, radio="hackrf")
            self.assertLess(im["db"], modem.IMAGE_LIMIT_DB - 5)
        fs, pl = self.plan(P.USB, videoFormat="lowpal")
        self.assertIn("a HackRF's image of the sound subcarrier's sidebands",
                      sysa.tx_image_warning(pl, "hackrf", fs, 1255e6))


if __name__ == "__main__":
    unittest.main()
