"""Picture and sound through the whole chain: test card -> PAL encoder ->
modulator -> channel (noise, carrier offset) -> demodulator -> decoder.

What is measured: the decoder's sync lock (every line's 0H found, field
sync, colour), its own SNR estimate against the channel's, and the
decoded picture against the source (PSNR on luminance and on colour, after
cropping the blanking edges). Sound: a 1 kHz tone's frequency and SINAD.
"""
import math
import unittest

import numpy as np

from helpers import ad936x_link, ad936x_tx_spectrum, channel, crop, first_claim, luma, psnr, resample_width
from ohsatv import modem, narrow, pal, testcard
from ohsatv.devices import ad936x
from ohsatv.decoder import VideoDecoder


def run_chain(mod, dem, fs, img, frames, cnr_db, offset, seed=1, audio=None, track=False):
    enc = pal.Encoder(fs, colour=mod.plan["colour"])
    dec = VideoDecoder(fs)
    rng = np.random.default_rng(seed)
    out, sound, pos = [], [], 0
    for k in range(frames):
        a = None
        if audio is not None:
            n_a = int(0.04 * modem.AUDIO_RATE)
            a = audio[k * n_a:(k + 1) * n_a]
        iq = mod(enc.encode(img, k), a)
        iq = channel(iq, cnr_db, offset, fs, pos, rng)
        pos += len(iq)
        if track and k % 2 == 0:
            dem.track(iq)
        out += dec.feed(dem(iq))
        sound.append(dem.audio(iq))
    return out, dec, np.concatenate(sound)


def picture_scores(frame, src):
    got = frame["rgb"].astype(np.float32) / 255
    ref = resample_width(src, got.shape[1])
    return psnr(crop(luma(got)), crop(luma(ref))), psnr(crop(got), crop(ref))


def tone_quality(audio, rate=48000):
    """(frequency, SINAD dB) of a tone: least-squares fit of a sinusoid,
    its frequency refined on a fine grid around the FFT peak (one FFT bin
    of error over a quarter of a second would leave a beat in the residual
    and read as distortion)."""
    x = np.asarray(audio[len(audio) // 4:], np.float64)          # skip filter start-up
    x = x - x.mean()
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    f = np.fft.rfftfreq(len(x), 1 / rate)
    k = np.argmax(spec[5:]) + 5
    t = np.arange(len(x)) / rate
    best = None
    for fr in np.arange(f[k] - 6, f[k] + 6, 0.05):
        basis = np.vstack([np.sin(2 * np.pi * fr * t), np.cos(2 * np.pi * fr * t)]).T
        coef, *_ = np.linalg.lstsq(basis, x, rcond=None)
        fit = basis @ coef
        e = np.sum((x - fit) ** 2)
        if best is None or e < best[0]:
            best = (e, fr, np.sum(fit ** 2))
    e, fr, p = best
    return fr, 10 * math.log10(p / max(e, 1e-20))


CARD = testcard.test_card("GM4BRI", "IO86 TEST")


def narrow_test_tone(n: int, stereo: bool) -> np.ndarray:
    """The transmitter's test sound: 1 kHz, or the stereo test with NICAM."""
    from ohsatv import systems
    return systems.test_tone(n, 1000.0, 0.5, stereo)


class AmVsb(unittest.TestCase):
    def test_test_card_over_am_vsb_at_10_msps(self):
        fs = 10e6
        mod, dem = modem.AmVsbMod(fs), modem.AmVsbDemod(fs)
        self.assertTrue(mod.plan["colour"])
        frames, dec, _ = run_chain(mod, dem, fs, CARD, 6, cnr_db=35, offset=31e3, track=True)
        self.assertGreaterEqual(len(frames), 4)
        st = frames[-1]["stats"]
        self.assertGreater(st["hLock"], 0.98)
        self.assertTrue(st["vLock"])
        self.assertTrue(st["colour"])
        self.assertLess(st["jitterNs"], 20)
        y, c = picture_scores(frames[-1], CARD)
        self.assertGreater(y, 26.0, f"luma PSNR {y:.1f} dB")
        self.assertGreater(c, 22.0, f"colour PSNR {c:.1f} dB")
        # The vision carrier was found where the channel put it.
        self.assertAlmostEqual(dem.carrier, mod.plan["carrier"] + 31e3, delta=500)

    def test_snr_estimate_follows_the_channel(self):
        fs = 10e6
        res = []
        for cnr in (20.0, 30.0):
            mod, dem = modem.AmVsbMod(fs), modem.AmVsbDemod(fs)
            frames, _, _ = run_chain(mod, dem, fs, CARD, 4, cnr_db=cnr, offset=0, seed=3)
            res.append(frames[-1]["stats"]["snr"])
        # 10 dB more carrier-to-noise is ~10 dB more video SNR.
        self.assertAlmostEqual(res[1] - res[0], 10.0, delta=2.0)

    def test_still_locks_in_heavy_noise(self):
        fs = 10e6
        mod, dem = modem.AmVsbMod(fs), modem.AmVsbDemod(fs)
        frames, _, _ = run_chain(mod, dem, fs, CARD, 6, cnr_db=12, offset=-20e3, seed=4, track=True)
        st = frames[-1]["stats"]
        self.assertTrue(st["vLock"])
        self.assertGreater(st["hLock"], 0.9)
        y, _ = picture_scores(frames[-1], CARD)
        self.assertGreater(y, 12.0)         # noise-limited: the picture is there, grainy

    def test_mono_at_8_msps(self):
        fs = 8e6
        mod, dem = modem.AmVsbMod(fs), modem.AmVsbDemod(fs)
        self.assertFalse(mod.plan["colour"])
        self.assertTrue(any("mono" in w for w in mod.plan["why"]))
        frames, _, _ = run_chain(mod, dem, fs, CARD, 5, cnr_db=35, offset=0)
        st = frames[-1]["stats"]
        self.assertTrue(st["vLock"])
        self.assertFalse(st["colour"])
        y, _ = picture_scores(frames[-1], CARD)
        self.assertGreater(y, 20.0)

    def test_the_usb_pluto_rate_gives_a_mono_picture_without_sound(self):
        # 6.25 MS/s is under the ~6.8 MS/s a Pluto sustains on USB 2 (ADI):
        # the only AM-VSB a stock Pluto can carry. The plan says what it
        # gives up, and the picture still locks.
        fs = 6.25e6
        mod, dem = modem.AmVsbMod(fs), modem.AmVsbDemod(fs)
        self.assertIsNone(mod.plan["sound"])
        self.assertFalse(mod.plan["colour"])
        self.assertTrue(mod.plan["fits"])
        self.assertEqual(len(mod.plan["why"]), 3)
        frames, _, _ = run_chain(mod, dem, fs, CARD, 6, cnr_db=30, offset=10e3, track=True)
        st = frames[-1]["stats"]
        self.assertTrue(st["vLock"])
        y, _ = picture_scores(frames[-1], CARD)
        self.assertGreater(y, 24.0)

    def test_sound_carrier(self):
        fs = 10e6
        mod, dem = modem.AmVsbMod(fs), modem.AmVsbDemod(fs)
        t = np.arange(int(8 * 0.04 * 48000)) / 48000
        tone = (0.5 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
        _, _, audio = run_chain(mod, dem, fs, CARD, 8, cnr_db=35, offset=0, audio=tone)
        self.assertGreater(len(audio), 0.2 * 48000)
        f, sinad = tone_quality(audio)
        self.assertAlmostEqual(f, 1000.0, delta=15)
        self.assertGreater(sinad, 25.0)


class Fm(unittest.TestCase):
    def test_iaru_fm_atv_at_20_msps(self):
        # IARU R1 V.2: 3.5 MHz peak deviation (7 MHz p-p here), CCIR 405,
        # 5 MHz video; with the UK's 6.0 MHz sound subcarrier.
        fs = 20e6
        mod = modem.FmMod(fs, deviation=7e6)
        dem = modem.FmDemod(fs, deviation=7e6)
        self.assertTrue(mod.plan["fits"])
        self.assertTrue(mod.plan["colour"])
        self.assertEqual(mod.plan["sub"], 6.0e6)
        frames, dec, _ = run_chain(mod, dem, fs, CARD, 6, cnr_db=22, offset=150e3)
        st = frames[-1]["stats"]
        self.assertGreater(st["hLock"], 0.98)
        self.assertTrue(st["vLock"])
        self.assertTrue(st["colour"])
        y, c = picture_scores(frames[-1], CARD)
        self.assertGreater(y, 26.0, f"luma PSNR {y:.1f} dB")
        self.assertGreater(c, 22.0, f"colour PSNR {c:.1f} dB")

    def test_fm_subcarrier_sound_at_16_msps_reduced_deviation(self):
        fs = 16e6
        mod = modem.FmMod(fs, deviation=4e6)
        dem = modem.FmDemod(fs, deviation=4e6)
        self.assertTrue(mod.plan["fits"])
        self.assertEqual(mod.plan["sub"], 6.0e6)
        t = np.arange(int(8 * 0.04 * 48000)) / 48000
        tone = (0.5 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
        _, _, audio = run_chain(mod, dem, fs, CARD, 8, cnr_db=25, offset=0, audio=tone)
        f, sinad = tone_quality(audio)
        self.assertAlmostEqual(f, 1000.0, delta=15)
        self.assertGreater(sinad, 25.0)

    def test_iaru_deviation_does_not_fit_below_20_msps(self):
        plan = modem.fm_plan(16e6, 7e6)
        self.assertFalse(plan["fits"])
        self.assertTrue(any("Carson" in w for w in plan["why"]))

    def test_narrow_fm_at_12_5_msps_has_no_sound_subcarrier(self):
        fs = 12.5e6
        mod = modem.FmMod(fs, deviation=1.8e6)
        dem = modem.FmDemod(fs, deviation=1.8e6)
        self.assertTrue(mod.plan["fits"])
        self.assertIsNone(mod.plan["sub"])
        frames, _, _ = run_chain(mod, dem, fs, CARD, 5, cnr_db=25, offset=0)
        st = frames[-1]["stats"]
        self.assertTrue(st["vLock"])
        y, _ = picture_scores(frames[-1], CARD)
        self.assertGreater(y, 22.0)

    def test_inverted_video_polarity_is_a_setting_both_ends_must_share(self):
        fs = 16e6
        mod = modem.FmMod(fs, deviation=4e6, invert=True)
        dem = modem.FmDemod(fs, deviation=4e6, invert=True)
        frames, _, _ = run_chain(mod, dem, fs, CARD, 5, cnr_db=30, offset=0)
        self.assertTrue(frames[-1]["stats"]["vLock"])
        y, _ = picture_scores(frames[-1], CARD)
        self.assertGreater(y, 26.0)


class Recovery(unittest.TestCase):
    def test_a_jump_in_the_stream_is_recovered_within_a_few_frames(self):
        # A receiver overrun drops samples: the flywheel is suddenly part of
        # a line and part of a field out. The decoder must not settle on a
        # minority of lines and call it a lock.
        fs = 20e6
        enc = pal.Encoder(fs)
        mod = modem.FmMod(fs, 7e6)
        iq = np.concatenate([mod(enc.encode(CARD, k)) for k in range(22)])
        n = len(iq) // 22
        for skip in (12_345, 400_777, 5_000_001):
            with self.subTest(skip=skip):
                x = np.concatenate([iq[:4 * n], iq[4 * n + skip:]])
                dem, dec = modem.FmDemod(fs, 7e6), VideoDecoder(fs)
                frames = []
                for i in range(0, len(x) - n + 1, n):
                    frames.append(dec.feed(dem(x[i:i + n])))
                after = [f for blk in frames[7:] for f in blk]
                self.assertGreaterEqual(len(after), 3)
                for f in after:
                    y, _ = picture_scores(f, CARD)
                    self.assertGreater(y, 26.0)


def bar_saturation(rgb) -> float:
    """Mean saturation of the six coloured 75 % bars, decoded over expected:
    the median of each bar's middle (the picture is clipped to 0..255, so a
    mean would read clipped noise as lost saturation)."""
    got = np.asarray(rgb, np.float32) / 255
    h, w = got.shape[:2]
    out = []
    for i in range(1, 7):
        m = np.median(got[int(0.15 * h):int(0.7 * h), int((i + 0.3) * w / 8):int((i + 0.7) * w / 8)].reshape(-1, 3),
                      axis=0)
        e = testcard.BARS[i]
        uv = lambda c: np.array([c[2] - (0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]),        # noqa: E731
                                 c[0] - (0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2])])
        out.append(np.hypot(*uv(m)) / np.hypot(*uv(e)))
    return float(np.mean(out))


def frac_delay(x: np.ndarray, d: float) -> np.ndarray:
    """Delay a whole recording by d samples (band-limited, by FFT)."""
    f = np.fft.fftfreq(len(x))
    return np.fft.ifft(np.fft.fft(x) * np.exp(-2j * np.pi * f * d)).astype(np.complex64)


class SubSampleTiming(unittest.TestCase):
    def test_colour_does_not_depend_on_where_a_line_starts_within_a_sample(self):
        # Two real radios' clocks differ by ~15 ppm, so where each line
        # starts drifts through every fraction of a sample. The decoder
        # once took lines by linear interpolation - a low-pass whose depth
        # depends on that fraction: half a sample out passed only 0.65 of
        # the 4.43 MHz subcarrier at 16 MS/s, and colour would have come and
        # gone in bands down a real picture.
        fs = 16e6
        bars = testcard.colour_bars("GM4BRI")
        enc, mod = pal.Encoder(fs), modem.FmMod(fs, deviation=4e6)
        iq = np.concatenate([mod(enc.encode(bars, k)) for k in range(5)])
        sat = {}
        for d in (0.0, 0.5):
            dem, dec = modem.FmDemod(fs, deviation=4e6), VideoDecoder(fs)
            x = frac_delay(iq, d)
            n = len(x) // 5
            frames = [f for k in range(5) for f in dec.feed(dem(x[k * n:(k + 1) * n]))]
            self.assertTrue(frames[-1]["stats"]["colour"])
            sat[d] = bar_saturation(frames[-1]["rgb"])
        self.assertGreater(sat[0.0], 0.9)
        self.assertAlmostEqual(sat[0.5], sat[0.0], delta=0.05)


class Breaks(unittest.TestCase):
    def run_break(self, gap: int = 0, slip: int = 0):
        fs = 10e6
        enc, mod = pal.Encoder(fs), modem.AmVsbMod(fs)
        cards = [np.roll(CARD, 24 * k, axis=1) for k in range(12)]
        iq = [mod(enc.encode(cards[k], k)) for k in range(12)]
        at = int(0.37 * len(iq[6]))
        iq[6] = np.concatenate([iq[6][:at], np.zeros(gap, np.complex64), iq[6][at + slip:]])
        dem, dec = modem.AmVsbDemod(fs), VideoDecoder(fs)
        rng = np.random.default_rng(5)
        got = []
        pos = 0
        for k in range(12):
            x = channel(iq[k], 30, 0, fs, pos, rng)
            pos += len(x)
            if k % 2 == 0:
                dem.track(x)
            got += dec.feed(dem(x))
        # Every source frame from the one after the break on is decoded whole.
        for k in (7, 8, 9):
            best = max(picture_scores(f, cards[k])[0] for f in got)
            self.assertGreater(best, 24.0, f"frame {k}")

    def test_a_gap_in_the_transmission_costs_no_more_than_a_field(self):
        # The nano's USB transmit falls behind (257 late buffers a minute on
        # air): the carrier stops, then the picture carries on late. The
        # decoder used to keep predicting lines into the old timing for a
        # batch or two, then search from the newest samples; now it
        # re-acquires from the first line after the break, and only the
        # lines before the next field sync are missing.
        self.run_break(gap=20_000)

    def test_lost_receive_samples_cost_no_more_than_a_field(self):
        # A receiver overrun: 777 samples gone mid-picture. Measured before
        # this change: two frames lost; after it, the lines up to the next
        # field sync.
        self.run_break(slip=777)


class NoSignal(unittest.TestCase):
    def test_noise_alone_never_claims_a_picture(self):
        fs = 10e6
        self.assertIsNone(first_claim(modem.AmVsbDemod(fs), VideoDecoder(fs), fs, 8, seed=9))

    def test_an_empty_fm_band_at_16_msps_never_claims_a_field(self):
        # The LibreSDR listening at 16 MS/s with the nano's narrow
        # parameters, nothing on the air: its FIELD pill read LOCKED in 3 of
        # 22 seconds (5 October 2026). FM noise through a 1.6 MHz video
        # filter at 16 MS/s passes the field sync test now and then with a
        # fifth to a third of the lines "found". These two streams claimed
        # a field at blocks 8 and 10 before the decoder asked for line lock
        # first (ohsatv/decoder.py, CLAIM_H).
        fs = 16e6
        p = {"fmDeviation": 1.2e6, "fmVideoBw": 1.6e6, "fmPreemph": True, "fmSub": 0.0}
        for seed in (2, 4):
            with self.subTest(seed=seed):
                dem, dec = narrow.make_demod(fs, "fm", p), narrow.make_decoder(fs, "fm", p)
                self.assertIsNone(first_claim(dem, dec, fs, 20, seed))


if __name__ == "__main__":
    unittest.main()


class CrossRate(unittest.TestCase):
    def test_a_5_msps_usb_pluto_is_received_at_16_msps(self):
        # The real 23cm pairing: a USB Pluto can only transmit 5 MS/s (narrow
        # FM: 1.2 MHz p-p, 1.6 MHz video, CCIR 405, no subcarrier); an
        # Ethernet receiver listens at 16 MS/s with the same parameters, for
        # the wider view. The air resamples; here an FFT does.
        tx_fs, rx_fs = 5e6, 16e6
        enc = pal.Encoder(tx_fs, colour=False)
        mod = modem.FmMod(tx_fs, deviation=1.2e6, video_bw=1.6e6, sub=0.0)
        self.assertTrue(mod.plan["fits"])
        iq = np.concatenate([mod(enc.encode(CARD, k)) for k in range(7)])
        spec = np.fft.fftshift(np.fft.fft(iq))
        pad = int(round(len(iq) * (rx_fs / tx_fs))) - len(iq)
        up = np.fft.ifft(np.fft.ifftshift(np.pad(spec, (pad // 2, pad - pad // 2)))) * (rx_fs / tx_fs)
        rng = np.random.default_rng(5)
        up = channel(up.astype(np.complex64), cnr_db=25, offset_hz=20e3, fs=rx_fs, start=0, rng=rng,
                     bw_hz=4.4e6)
        dem, dec = modem.FmDemod(rx_fs, deviation=1.2e6, video_bw=1.6e6, sub=0.0), VideoDecoder(rx_fs)
        frames = []
        n = int(rx_fs * 0.04)
        for i in range(0, len(up) - n + 1, n):
            frames += dec.feed(dem(up[i:i + n]))
        st = frames[-1]["stats"]
        self.assertTrue(st["vLock"])
        self.assertGreater(st["hLock"], 0.98)
        y, _ = picture_scores(frames[-1], CARD)
        self.assertGreater(y, 18.0, f"luma PSNR {y:.1f} dB")


class Ad936x(unittest.TestCase):
    """A Pluto's own fixed filters between two Lin boxes (docs/atv-research.md
    17), simulated sample by sample (helpers.ad936x_link), and what the
    modems do about them. On 5 October 2026 FM-ATV at 12.5 MS/s, LibreSDR to
    Pluto nano, came in with its colour burst at 0.73 of what was sent and
    its 5.5 MHz sound subcarrier 7-10 dB down."""

    FS = 12.5e6
    # The USB-Pluto-with-8-bit-receive profile (ohsatv/profiles.py), as on air.
    FM = dict(deviation=1.2e6, video_bw=4.9e6, sub=5.5e6)

    def test_the_response_model_is_what_the_filters_do(self):
        # Tones through the simulated chip against the model the modems use:
        # the same to 0.1 dB up to 0.47 fs, at the rates the profiles use.
        for fs in (5e6, 12.5e6, 16e6):
            rx, tx = ad936x.Response("rx", fs, fs), ad936x.Response("tx", fs, fs)
            n, seg = 1 << 15, 1 << 14
            k = np.arange(n)
            for frac in (0.2, 0.354, 0.44, -0.44, 0.47):
                frac = round(frac * seg) / seg          # whole cycles in the segment measured
                x = (0.5 * np.exp(2j * np.pi * frac * k)).astype(np.complex64)
                y = ad936x_link(x, fs, image_hz=50e3)
                # One FFT bin, away from the ends: the transmitter's image
                # folds back 50 kHz off the tone (two clocks), out of it.
                b = int(round(frac * seg)) % seg
                got = 20 * math.log10(abs(np.fft.fft(y[8000:8000 + seg])[b]) / abs(np.fft.fft(x[8000:8000 + seg])[b]))
                want = 20 * math.log10(float(rx(frac * fs) * tx(frac * fs)))
                self.assertAlmostEqual(got, want, delta=0.1, msg=f"{fs / 1e6:g} MS/s, {frac:+.3f} fs")

    def _through(self, tx_shape, rx_shape, link=True, frames=5, audio=None):
        fs = self.FS
        mod = modem.FmMod(fs, tx_shape=tx_shape, **self.FM)
        dem = modem.FmDemod(fs, rx_shape=rx_shape, **self.FM)
        enc = pal.Encoder(fs, colour=mod.plan["colour"])
        n_a = int(0.04 * modem.AUDIO_RATE)
        iq = np.concatenate([mod(enc.encode(CARD, k), None if audio is None else audio[k * n_a:(k + 1) * n_a])
                             for k in range(frames)])
        if link:
            iq = ad936x_link(iq, fs)
        iq = channel(iq, cnr_db=30, offset_hz=20e3, fs=fs, start=0, rng=np.random.default_rng(3))
        dec = VideoDecoder(fs)
        out, snd = [], []
        n = int(fs * 0.04)
        for i in range(0, len(iq) - n + 1, n):
            blk = iq[i:i + n]
            out += dec.feed(dem(blk))
            snd.append(dem.audio(blk))
        return out, np.concatenate(snd)

    def _burst(self, frames):
        b = [f["stats"]["burst"] for f in frames[1:] if f["stats"]["burst"] is not None]
        self.assertTrue(b, "no colour burst measured")
        return float(np.median(b))

    def test_fm_colour_through_two_plutos_at_12_5_msps(self):
        flat = self._burst(self._through(None, None, link=False)[0])
        raw = self._burst(self._through(None, None)[0])
        rx = ad936x.Response("rx", self.FS, self.FS)
        tx = ad936x.Response("tx", self.FS, self.FS)
        fixed = self._burst(self._through(tx, rx)[0])
        # Uncorrected, the colour is desaturated by the amount seen on air
        # (0.73 there, with the carrier's swing and the radios' tolerances).
        self.assertLess(raw / flat, 0.85)
        # Each end undoing its own filters puts it back.
        self.assertAlmostEqual(fixed / flat, 1.0, delta=0.05, msg=f"{raw / flat:.3f} -> {fixed / flat:.3f}")

    def test_each_end_corrects_its_own_share(self):
        # A Pluto receiving a transmitter that corrects nothing (a HackRF, or
        # an older OpenHamShack) recovers its own half and no more.
        flat = self._burst(self._through(None, None, link=False)[0])
        raw = self._burst(self._through(None, None)[0])
        rx_only = self._burst(self._through(None, ad936x.Response("rx", self.FS, self.FS))[0])
        tx_only = self._burst(self._through(ad936x.Response("tx", self.FS, self.FS), None)[0])
        for got in (rx_only, tx_only):
            self.assertGreater(got, raw + 0.3 * (flat - raw))
            self.assertLess(got, flat - 0.2 * (flat - raw))

    def test_sound_still_clean_with_the_correction(self):
        t = np.arange(int(5 * 0.04 * 48000)) / 48000
        tone = (0.5 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
        rx = ad936x.Response("rx", self.FS, self.FS)
        tx = ad936x.Response("tx", self.FS, self.FS)
        frames, audio = self._through(tx, rx, audio=tone)
        f, sinad = tone_quality(audio)
        self.assertAlmostEqual(f, 1000.0, delta=15)
        self.assertGreater(sinad, 25.0)
        self.assertTrue(frames[-1]["stats"]["colour"])

    def test_a_pluto_s_images_of_625_line_am(self):
        # The colour AM mode on air on 4 October 2026 - 10 MS/s, carriers
        # centred - put the sound carrier at +3.6 MHz from the LO, 0.36 fs,
        # where the half-band that interpolates to the DAC barely touches
        # its image: about 25 dB under the sound carrier, outside the
        # channel, with no warning. The plans now put the carriers where
        # every image is 40 dB under (modem._place). Built whole - the test
        # card with teletext and the tone (the stereo test with NICAM),
        # through the chip's own interpolation - each carrier's image is
        # where and as strong as the plan says, nothing outside the channel
        # is within 40 dB of the vision carrier, and the picture's own images
        # are lower still. Where no placement reaches the limit (8 MS/s with
        # sound: -11 dB, from -8) the plan card and the transmit panel say so.
        from ohsatv import sysa, ttx, vbi
        rng = np.random.default_rng(2)
        tone = modem.AUDIO_RATE // 25
        for fs, nic in ((10e6, False), (10e6, True), (12.5e6, True), (8e6, False)):
            with self.subTest(fs=fs, nicam=nic):
                p = {"nicam": nic}
                pl = narrow.plan(fs, "am", p)
                old = narrow.plan(fs, "am", dict(p, amPlacement="centred"))
                mod = narrow.make_mod(fs, "am", p, tx_shape=ad936x.Response("tx", fs, fs))
                enc = narrow.make_encoder(fs, "am", p)
                enc.colour = enc.colour and pl["colour"]
                ins = vbi.Inserter(fs) if pl["teletext"] else None
                stereo = mod.nicam is not None
                aud = narrow_test_tone(2 * tone, stereo)
                out = []
                for k in range(2):
                    comp = enc.encode(CARD, k)
                    if ins is not None:
                        pk = [ttx.row(1 + i % 8, 1 + i % 24, bytes(rng.integers(32, 127, 40).astype(np.uint8)))
                              for i in range(32)]
                        ins.insert(comp, pk[:16], pk[16:])
                    out.append(mod(comp, aud[k * tone:(k + 1) * tone]))
                f, P = ad936x_tx_spectrum(np.concatenate(out), fs)
                car = pl["carrier"]

                def line(hz):
                    return P[np.argmin(np.abs(f - hz))]
                vision = line(car)
                for c in (car, pl["sound"]):
                    if c is None:
                        continue
                    want = ad936x.Response("tx", fs, fs).image(c)[1]
                    got = 10 * math.log10(line(c - fs if c > 0 else c + fs) / line(c))
                    self.assertAlmostEqual(got, want, delta=1.5)
                g = f - car
                lo, hi = pl["occupied"]
                outside = ((g < lo - 0.05e6) | (g > hi + 0.05e6)) & (np.abs(f) < fs)
                worst = 10 * math.log10(P[outside].max() / vision)
                warning = sysa.tx_image_warning(pl, "pluto", fs, 1251.0e6)
                if pl["txImage"]["db"] <= modem.IMAGE_LIMIT_DB:
                    self.assertLess(worst, modem.IMAGE_LIMIT_DB)
                    self.assertIsNone(warning)
                    # The picture's own images, away from the carriers' lines.
                    lines = np.zeros(len(f), bool)
                    for c in (car, pl["sound"], pl["nicam"]):
                        if c is not None:
                            lines |= np.abs(f - (c - fs if c > 0 else c + fs)) < 0.4e6
                    self.assertLess(10 * math.log10(P[outside & ~lines].max() / vision), -55)
                else:
                    self.assertEqual(fs, 8e6)
                    self.assertLess(pl["txImage"]["db"], old["txImage"]["db"] - 2)       # -8 -> -11
                    self.assertIn("image of the sound carrier", warning)
                    self.assertIn("no placement at 8 MS/s", pl["why"][-1])
                if fs == 10e6:
                    # Centred, as on air on 4 October: the sound's image far
                    # over the limit, and the panel would have said so.
                    self.assertGreater(old["txImage"]["db"], -37)
                    self.assertIn("image of the sound carrier", sysa.tx_image_warning(old, "pluto", fs, 1251.0e6))
                    self.assertAlmostEqual(pl["carrier"], -pl["sound"], delta=25)

    def test_a_pluto_s_images_of_fm_subcarriers(self):
        # FM cannot be placed, so a subcarrier's sidebands near fs/2 image
        # where they are: Low-PAL's 2.2 MHz sound at 5 MS/s and the 5.5 MHz
        # sound at 12.5 MS/s, both at 0.44 fs, came out 1-2.6 dB over the
        # limit in docs/atv-research.md 24, and Brian chose a warning over a
        # change. Built whole - test card, tone (the stereo test with NICAM),
        # the transmit correction - and interpolated as the chip does, the
        # strongest image in 100 kHz against the whole signal's power is
        # what the plan's txImage says, to a dB; NICAM's model errs on the
        # side of warning. Where the plan is over the limit the plan card and
        # the transmit panel say so, and where it is under, the panel says
        # nothing and nothing in the image band reaches the limit.
        from ohsatv import sysa
        tone = modem.AUDIO_RATE // 25
        base = {"fmPreemph": True, "fmSubIndex": 0.07, "fmInvert": False}
        cases = (
            # (fs, params, whether it warns)
            (5e6, {"videoFormat": "lowpal", "fmDeviation": 1.2e6, "fmVideoBw": 1.6e6}, True),
            (12.5e6, {"fmDeviation": 1.2e6, "fmVideoBw": 4.9e6, "fmSub": 5.5e6}, True),
            (16e6, {"fmDeviation": 4.0e6, "fmVideoBw": 5.0e6, "fmSub": 6.0e6}, False),
            (16e6, {"fmDeviation": 3.2e6, "fmVideoBw": 5.0e6, "fmSub": 6.0e6, "nicam": True}, False),
            (5e6, {"fmDeviation": 1.2e6, "fmVideoBw": 1.6e6, "fmSub": 6.0e6}, False),        # no room: none
        )
        for fs, p, warns in cases:
            p = dict(base, **p)
            with self.subTest(fs=fs, p=p):
                pl = narrow.plan(fs, "fm", p)
                mod = narrow.make_mod(fs, "fm", p, tx_shape=ad936x.Response("tx", fs, fs))
                enc = narrow.make_encoder(fs, "fm", p)
                aud = narrow_test_tone(2 * tone, mod.nicam is not None)
                iq = np.concatenate([mod(enc.encode(CARD, k, aud[k * tone:(k + 1) * tone]),
                                         aud[k * tone:(k + 1) * tone]) for k in range(2)])
                f, P = ad936x_tx_spectrum(iq, fs)
                total = P[np.abs(f) < fs / 2].sum()
                b = np.rint(f / 100e3).astype(np.int64)
                u, inv = np.unique(b, return_inverse=True)
                P100 = np.bincount(inv, weights=P)
                f100 = u * 100e3
                band = (np.abs(f100) > fs / 2 + 50e3) & (np.abs(f100) < fs)
                k = np.flatnonzero(band)[np.argmax(P100[band])]
                got = 10 * math.log10(P100[k] / total)
                im = pl["txImage"]
                panel = sysa.tx_image_warning(pl, "pluto", fs, 1255.0e6)
                card = [w for w in pl["why"] if "a Pluto (AD936x)" in w]
                if im is None:
                    self.assertIsNone(pl["sub"])
                    self.assertLess(got, modem.IMAGE_LIMIT_DB - 20)
                elif im["of"] == "NICAM":
                    self.assertGreaterEqual(im["db"], got - 0.5)
                    self.assertLess(im["db"], got + 3.0)
                else:
                    self.assertAlmostEqual(im["db"], got, delta=1.0)
                    # Both sidebands image alike: either side of the carrier.
                    self.assertAlmostEqual(abs(f100[k]), abs(im["offset"]), delta=0.25e6)
                if warns:
                    self.assertGreater(got, modem.IMAGE_LIMIT_DB)
                    self.assertIn("image of the sound subcarrier's sidebands", panel)
                    self.assertIn(f"{(1255.0e6 + im['offset']) / 1e6:.2f} MHz, outside the channel", panel)
                    self.assertEqual(len(card), 1)
                else:
                    self.assertLess(got, modem.IMAGE_LIMIT_DB)
                    self.assertIsNone(panel)
                    self.assertEqual(card, [])

    def test_am_vsb_at_10_msps_through_two_plutos(self):
        # AM-VSB is linear, so the correction is exact. With the carriers
        # centred, as 10 MS/s placed them until late on 5 October 2026, the colour
        # sits low in the band (the vision carrier 2.4 MHz below the LO),
        # where the loss is small, but the sound carrier at +3.6 MHz loses
        # 2.4 dB, which the transmitter puts back. Placed for the images
        # (modem._place) the two carriers lose the same and the ratio below
        # cannot show it, so this is the centred plan; the correction is the
        # same filter wherever the carriers are.
        fs = 10e6
        rx, tx = ad936x.Response("rx", fs, fs), ad936x.Response("tx", fs, fs)
        mod = modem.AmVsbMod(fs, tx_shape=tx, placement="centred")
        plain = modem.AmVsbMod(fs, placement="centred")
        enc = pal.Encoder(fs, colour=mod.plan["colour"])
        frames = [enc.encode(CARD, k) for k in range(4)]

        def line_power(iq, at):
            spec = np.abs(np.fft.fft(iq)) ** 2
            k = int(round(at / fs * len(iq))) % len(iq)
            return spec[k - 3:k + 4].sum()
        sent_fixed = ad936x_link(np.concatenate([mod(f) for f in frames]), fs)
        sent_plain = ad936x_link(np.concatenate([plain(f) for f in frames]), fs)
        ideal = np.concatenate([modem.AmVsbMod(fs, placement="centred")(f) for f in frames])
        snd, vis = mod.plan["sound"], mod.plan["carrier"]

        def ratio_db(iq):
            return 10 * math.log10(line_power(iq, snd) / line_power(iq, vis))
        self.assertLess(ratio_db(sent_plain) - ratio_db(ideal), -1.5)
        # What is left after the link is the receiver's share, which its
        # demodulator undoes.
        self.assertAlmostEqual(ratio_db(sent_fixed) - ratio_db(ideal) - 20 * math.log10(float(rx(snd) / rx(vis))),
                               0.0, delta=0.3)
        dem = modem.AmVsbDemod(fs, rx_shape=rx, placement="centred")
        dec = VideoDecoder(fs)
        out = []
        n = int(fs * 0.04)
        for i in range(0, len(sent_fixed) - n + 1, n):
            out += dec.feed(dem(sent_fixed[i:i + n]))
        st = out[-1]["stats"]
        self.assertTrue(st["vLock"])
        self.assertTrue(st["colour"])
