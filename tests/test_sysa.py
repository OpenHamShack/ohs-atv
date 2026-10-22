"""405-line System A, measured: the waveform's timing from the encoder's
samples, the transmitted levels and spectrum, and the whole chain - encoder
-> modulator -> noise and offset -> demodulator -> decoder - at the rates a
USB Pluto and an Ethernet radio run (DESIGN.md "405 lines").

The standard's figures are written out here from CCIR Report 624-2 (1982),
not read back from ohsatv.lines or ohsatv.sysa. CNR is over the whole
sample band, as tests/test_chain.py specifies it."""
import math
import unittest

import numpy as np

from helpers import ad936x_link, ad936x_tx_spectrum, channel, crop, first_claim, luma, psnr, resample_width, tmp_dir
from ohsatv import narrow, sysa, systems, testcard
from ohsatv.devices import ad936x

LINE_US = 1e6 / 10_125                  # Table I: 10 125 Hz -> 98.765 us
HALF_US = LINE_US / 2
SYNC_US = 9.0                           # Table 1-1 d: 8 to 10 us
BROAD_US = 40.0                         # Table 1-2 q: 38 to 42 us
PIC_START_US = 16.25                    # b: 16 to 17 us
PIC_END_US = LINE_US - 1.75             # c: front porch 1.5 to 2.0 us
CARD = testcard.test_card("GM4BRI", "IO86 TEST", lines405=True)


def crossings(x, level, falling):
    a, b = x[:-1], x[1:]
    idx = np.flatnonzero((a >= level) & (b < level)) if falling else np.flatnonzero((a < level) & (b >= level))
    return idx + (a[idx] - level) / (a[idx] - b[idx])


def pulses(signal, fs):
    """(start_us, width_us) of every sync pulse, at the 50 % (-0.15 V) points."""
    f = crossings(signal, -0.15, True)
    r = crossings(signal, -0.15, False)
    out = []
    for s in f:
        e = r[r > s]
        if len(e):
            out.append((s / fs * 1e6, (e[0] - s) / fs * 1e6))
    return out


def kinds(ps):
    return "".join("h" if abs(w - SYNC_US) < 0.05 else "b" if abs(w - BROAD_US) < 0.05 else f"?{w:.2f}"
                   for _, w in ps)


def sinad(audio, f0=1000.0, rate=48_000):
    """SINAD (dB) of a tone: a least-squares sinusoid, its frequency found by
    a grid and then a ternary search, so the fit's own frequency error does
    not read as distortion (test_chain's 0.05 Hz grid limits it to ~30 dB
    over a quarter of a second)."""
    x = np.asarray(audio[len(audio) // 4:], np.float64)
    x = x - x.mean()
    t = np.arange(len(x)) / rate

    def resid(fr):
        b = np.vstack([np.sin(2 * np.pi * fr * t), np.cos(2 * np.pi * fr * t)]).T
        c, *_ = np.linalg.lstsq(b, x, rcond=None)
        fit = b @ c
        return float(np.sum((x - fit) ** 2)), float(np.sum(fit ** 2))

    best = min(np.arange(f0 - 2, f0 + 2, 0.01), key=lambda fr: resid(fr)[0])
    lo, hi = best - 0.01, best + 0.01
    for _ in range(30):
        a, b = lo + (hi - lo) / 3, hi - (hi - lo) / 3
        if resid(a)[0] < resid(b)[0]:
            hi = b
        else:
            lo = a
    e, p = resid((lo + hi) / 2)
    return 10 * math.log10(p / max(e, 1e-30))


TONE = 0.5 * np.sin(2 * np.pi * 1000 * np.arange(48_000 * 2) / 48_000)


def chain(fs, cnr, frames=10, link=False, seed=1, offset=21e3, img=CARD):
    """(last frame or None, its stats, audio) through the whole chain; `link`:
    through two AD936x radios (helpers.ad936x_link), each modem undoing its
    own radio's response."""
    tx = ad936x.Response("tx", fs, fs) if link else None
    rx = ad936x.Response("rx", fs, fs) if link else None
    p = {"videoFormat": "405"}
    enc = systems.make_encoder(fs, "am", p)
    mod, dem = systems.make_mod(fs, "am", p, tx_shape=tx), systems.make_demod(fs, "am", p, rx_shape=rx)
    dec = systems.make_decoder(fs, "am", p)
    rng = np.random.default_rng(seed)
    out, snd, pos = [], [], 0
    comp = enc.encode(img, 0)
    for k in range(frames):
        iq = mod(comp, TONE[k * 1920:(k + 1) * 1920])
        if link:
            iq = ad936x_link(iq, fs)
        iq = channel(iq, cnr, offset, fs, pos, rng)
        pos += len(iq)
        if k % 2 == 0:
            dem.track(iq)
        out += dec.feed(dem(iq))
        snd.append(dem.audio(iq))
    last = out[-1] if out else None
    return last, (last or {}).get("stats", dec.stats), np.concatenate(snd)


def luma_psnr(frame, src=CARD):
    got = frame["rgb"].astype(np.float32) / 255
    ref = resample_width(sysa.rows_to(src, 378), got.shape[1])
    return psnr(crop(luma(got)), crop(luma(ref)))


def locked(st):
    return bool(st.get("vLock")) and st.get("hLock", 0) >= 0.9


class LineTiming(unittest.TestCase):
    """At the encoder's own line-locked rate, where a line is whole samples."""

    def setUp(self):
        self.enc = sysa.Encoder(5e6)
        self.fs = self.enc.fs_i
        self.n = self.enc.n

    def test_a_picture_line(self):
        fr = self.enc.encode(np.full((576, 720, 3), 0.5, np.float32))
        self.assertEqual(fr.shape, (405, self.n))
        sig = fr.reshape(-1)
        line = 100
        seg = sig[(line - 1) * self.n - 50:(line + 1) * self.n]
        ps = pulses(seg, self.fs)
        self.assertAlmostEqual(ps[0][0], 50 / self.fs * 1e6, delta=0.02)        # 0H on the line's first sample
        self.assertAlmostEqual(ps[0][1], SYNC_US, delta=0.05)
        self.assertAlmostEqual(ps[1][0] - ps[0][0], LINE_US, delta=0.01)
        body = sig[(line - 1) * self.n:line * self.n]
        up = crossings(body, 0.175, False) / self.fs * 1e6
        dn = crossings(body, 0.175, True) / self.fs * 1e6
        self.assertAlmostEqual(up[0], PIC_START_US, delta=0.08)
        self.assertAlmostEqual(dn[-1], PIC_END_US, delta=0.08)
        # Black is blanking (Table I: no set-up): the back porch and a black
        # picture are the same level.
        black = self.enc.encode(np.zeros((576, 720, 3), np.float32))[line - 1]
        self.assertLess(float(np.abs(black[int(10e-6 * self.fs):]).max()), 1e-3)

    def test_sync_edges(self):
        x = self.enc.encode(np.zeros((576, 720, 3))).reshape(-1)[99 * self.n - 40:99 * self.n + 40]
        rise = (crossings(x, -0.27, True)[0] - crossings(x, -0.03, True)[0]) / self.fs * 1e6
        self.assertLess(rise, 0.25)                        # Table 1-1 f: under 0.25 us
        self.assertGreater(rise, 0.1)

    def field_pulses(self, sig, first_line, last_line):
        a = (first_line - 1) * self.n - 10
        b = last_line * self.n
        return [(s - 10 / self.fs * 1e6, w) for s, w in pulses(sig[a:b], self.fs)]

    def test_eight_broad_pulses_and_no_equalising_pulses(self):
        fr = self.enc.encode(np.zeros((576, 720, 3)))
        sig = np.concatenate([fr.reshape(-1)] * 2)
        # Field 1: lines 404 and 405, the broad pulses from 0H of line 1
        # (four lines), then lines 5 and 6.
        ps = self.field_pulses(sig, 404, 405 + 6)
        self.assertEqual(kinds(ps), "hh" + "b" * 8 + "hh")
        starts = [s for s, _ in ps]
        self.assertAlmostEqual(starts[2], 2 * LINE_US, delta=0.01)
        self.assertTrue(np.allclose(np.diff(starts[2:10]), HALF_US, atol=0.01))   # Table 1-2 m: 4 H
        # Field 2 starts half a line later: in the middle of line 203.
        ps = self.field_pulses(sig, 202, 209)
        self.assertEqual(kinds(ps), "hh" + "b" * 8 + "hh")
        self.assertAlmostEqual(ps[2][0], 1.5 * LINE_US, delta=0.01)
        # 202.5 lines a field, 40 ms a frame.
        self.assertAlmostEqual(fr.size / self.fs, 0.04, delta=1e-9)

    def test_377_picture_lines(self):
        fr = self.enc.encode(np.ones((576, 720, 3), np.float32))
        first, second = fr[:, :fr.shape[1] // 2], fr[:, fr.shape[1] // 2:]
        lit1 = np.flatnonzero(first.max(axis=1) > 0.35) + 1
        lit2 = np.flatnonzero(second.max(axis=1) > 0.35) + 1
        full = sorted(set(lit1) & set(lit2))
        self.assertEqual(full, list(range(15, 203)) + list(range(218, 406)))
        self.assertEqual(sorted(set(lit1) - set(lit2)), [203])       # field 1 ends on half a line
        self.assertEqual(sorted(set(lit2) - set(lit1)), [217])       # field 2 starts on one


class Transmitted(unittest.TestCase):
    def detect(self, fs, iq, carrier, bw=0.5e6):
        """Synchronous detection close to the vision carrier, where the
        signal is double-sideband and so exactly AM."""
        n = len(iq)
        k = np.arange(n)
        x = iq * np.exp(-2j * np.pi * ((k * carrier / fs) % 1.0))
        f = np.fft.fftfreq(n, 1 / fs)
        X = np.fft.fft(x)
        X[np.abs(f) > bw] = 0
        y = np.fft.ifft(X)
        return (y * np.exp(-1j * np.angle(y.mean()))).real

    def test_positive_modulation_levels(self):
        # Table III items 7-8: sync under 3 %, blanking 30 %, white 100 %.
        fs = 10e6
        mod = sysa.Mod(fs)
        white = np.ones((576, 720, 3), np.float32)
        iq = mod(sysa.Encoder(fs).encode(white))
        env = self.detect(fs, iq, mod.plan["carrier"]) / mod.scale
        us = fs * 1e-6                                     # 405 lines are not whole samples here:
        start = int(round(99 * LINE_US * us))              # line 100 from its own start
        line = env[start:start + int(LINE_US * us)]
        self.assertLess(abs(np.median(line[int(3 * us):int(6 * us)])), 0.03)
        self.assertAlmostEqual(np.median(line[int(11 * us):int(15 * us)]), 0.30, delta=0.02)
        self.assertAlmostEqual(np.median(line[int(30 * us):int(80 * us)]), 1.00, delta=0.02)

    def test_sound_and_spectrum(self):
        for fs in (5e6, 6.25e6, 10e6):
            with self.subTest(fs=fs):
                mod = sysa.Mod(fs)
                white = np.ones((576, 720, 3), np.float32)
                iq = np.concatenate([mod(sysa.Encoder(fs).encode(white)) for _ in range(2)])
                n = len(iq)
                P = np.abs(np.fft.fft(iq)) ** 2
                f = np.fft.fftfreq(n, 1 / fs)
                car = mod.plan["carrier"]
                # The sound carrier 3.5 MHz below vision (Table III item 2),
                # a quarter of the vision carrier's peak power (item 12: 4/1);
                # at 5 MS/s none at all (sysa.SOUND_MIN_FS).
                near = np.abs(f - (car - 3.5e6)) < 2e3
                sound_amp = math.sqrt(P[near].max()) / n / mod.scale
                if fs < 6e6:
                    self.assertLess(20 * math.log10(sound_amp + 1e-30), -80)
                else:
                    self.assertAlmostEqual(20 * math.log10(sound_amp), -6.0, delta=0.2)
                # Everything inside the 5 MHz channel: 4.3 MHz from the sound's
                # sidebands to the vestige's top (items 1-5), and the whole of
                # it inside 0.45 fs, clear of the AD936x's band edge.
                g = f - car
                outside = P[(g < -3.6e6) | (g > 0.85e6)].sum() / P.sum()
                self.assertLess(10 * math.log10(outside + 1e-30), -40)
                edge = P[np.abs(f) > 0.45 * fs].sum() / P.sum()
                self.assertLess(10 * math.log10(edge + 1e-30), -40)

    def test_a_pluto_s_images(self):
        # On 5 October 2026 the nano's 405-line bursts at 5 MS/s put an
        # image of the sound carrier at 1256.48 MHz, outside the channel,
        # 14.2 dB under the sound carrier: the half-band that interpolates
        # to the DAC passes it. A tone where that sound carrier was comes out
        # so here, and as the plan says when it leaves the sound off. Then
        # the whole signal, the test card's picture and the carriers, as each
        # rate now sends it: nothing outside the channel within 40 dB of the
        # vision carrier, and the strongest carrier image where the model
        # (sysa.tx_image) puts it.
        snd, _ = sysa._centred_sound(sysa.VSB, sysa.SOUND_OFFSET)
        k = np.arange(400_000)
        f, P = ad936x_tx_spectrum(np.exp(2j * np.pi * snd / 5e6 * k).astype(np.complex64), 5e6)

        def at(hz):
            return P[np.argmin(np.abs(f - hz))]
        db = 10 * math.log10(at(snd + 5e6) / at(snd))
        self.assertAlmostEqual(db, -14.2, delta=2.0)
        self.assertIn(f"{-db:.0f} dB under it", sysa.plan(5e6)["soundWhy"])

        for fs, picture in ((5e6, -60.0), (6.25e6, -45.0), (8e6, -50.0), (10e6, -60.0)):
            with self.subTest(fs=fs):
                pl = sysa.plan(fs)
                mod = sysa.Mod(fs, tx_shape=ad936x.Response("tx", fs, fs))
                enc = sysa.Encoder(fs)
                iq = np.concatenate([mod(enc.encode(CARD), TONE[k * 1920:(k + 1) * 1920]) for k in range(2)])
                f, P = ad936x_tx_spectrum(iq, fs)
                car = pl["carrier"]
                vision = P[np.argmin(np.abs(f - car))]
                g = f - car
                out = ((g < -3.6e6) | (g > 0.85e6)) & (np.abs(f) < fs)
                rel = 10 * np.log10(P[out] / vision + 1e-30)
                self.assertLess(rel.max(), sysa.IMAGE_LIMIT_DB)
                im = pl["txImage"]
                got = 10 * math.log10(P[np.argmin(np.abs(f - (car + im["offset"])))] / vision)
                self.assertAlmostEqual(got, im["db"], delta=1.5)
                # The picture's own images, away from the carriers' lines.
                lines = (np.abs(g - im["offset"]) < 30e3) | (np.abs(g + fs) < 30e3) | (np.abs(g - fs) < 30e3)
                if pl["sound"] is not None:
                    lines |= np.abs(f - (pl["sound"] + fs)) < 30e3
                    lines |= np.abs(f - (pl["sound"] - fs)) < 30e3
                pic = out & ~lines
                self.assertLess(10 * math.log10(P[pic].max() / vision), picture)
                if fs > 6e6:
                    self.assertIsNone(sysa.tx_image_warning(pl, "pluto", fs, 1255.0e6))

    def test_mono_at_5_msps_and_sound_from_6_25(self):
        pl = sysa.plan(5e6)
        self.assertIsNone(pl["sound"])
        self.assertIn("no sound at 5 MS/s", pl["why"][0])
        self.assertEqual(pl["video_bw"], 3e6)                   # the whole picture still
        warning = sysa.tx_image_warning(pl, "pluto", 5e6, 1255.0e6)
        self.assertIn("405 lines go without sound at 5 MS/s", warning)
        self.assertIn("6.25 MS/s or more carries the sound", warning)
        pl = sysa.plan(6.25e6)
        self.assertIsNotNone(pl["sound"])
        self.assertEqual(pl["soundWhy"], "")
        # Placed so the carriers' images are equally deep: either side of the LO.
        self.assertAlmostEqual(pl["carrier"], -pl["sound"], delta=25)
        self.assertLess(pl["txImage"]["db"], sysa.IMAGE_LIMIT_DB)
        # 8 MS/s and up keep the centred plan that went on air.
        for fs in (8e6, 10e6, 20e6):
            self.assertEqual(sysa.plan(fs)["carrier"], 1.4e6)

    def test_the_transmit_panel_names_the_image(self):
        from test_radio import FAST
        from ohsatv.audio import NullAudio
        from ohsatv.radio import Radio
        from unittest import mock
        d = tmp_dir(self, "ohsatv-sysa-")
        for fs, kind, expect in ((5_000_000, "pluto", "go without sound at 5 MS/s"), (6_250_000, "pluto", None),
                                 (10_000_000, "pluto", None), (8_000_000, "hackrf", "baseband filter"),
                                 (20_000_000, "hackrf", None), (5_000_000, "sim", "go without sound")):
            with self.subTest(fs=fs, kind=kind):
                r = Radio(dict(FAST, system="am", amRate=fs, videoFormat="405", formatAuto=False, rxFreq=1255.0e6,
                               txFreq=1255.0e6), conf_path=f"{d}/c.json", audio=NullAudio(realtime=False),
                          data_dir=f"{d}/data")
                r.start()
                try:
                    with mock.patch.object(r.dev, "kind", kind):
                        warn = " ".join(r._tx_refusals()[1])
                    card = " ".join(r.state()["plan"]["why"])
                finally:
                    r.stop()
                if expect:
                    self.assertIn(expect, warn)
                else:
                    self.assertNotIn("image", warn)
                    self.assertNotIn("without sound", warn)
                # The plan card says it too.
                self.assertEqual("no sound at" in card, fs < 6e6)

    def test_625_lines_on_a_hackrf_are_warned_below_20_msps(self):
        # A HackRF has no interpolation filter: 625 lines at 10 MS/s image
        # as 405 did at 8. With the carriers centred the sound carrier's
        # image was the worst, 4 MHz below vision at about -3 dB; placed for
        # an AD936x's images (modem._place, +-3 MHz from the LO) the vision
        # carrier's, 10 MHz above it, is as bad - a HackRF images both
        # wherever they go. 20 MS/s (profiles.HACKRF) puts every carrier's
        # image, NICAM's included, more than 40 dB under.
        from test_radio import FAST
        from ohsatv.audio import NullAudio
        from ohsatv.radio import Radio
        from unittest import mock
        d = tmp_dir(self, "ohsatv-sysa-")
        for fs, nicam, kind, expect in ((10_000_000, False, "hackrf", "1265.00"), (10_000_000, True, "hackrf", None),
                                        (16_000_000, True, "hackrf", ""), (20_000_000, True, "hackrf", ""),
                                        (20_000_000, False, "hackrf", ""), (10_000_000, False, "sim", "")):
            with self.subTest(fs=fs, nicam=nicam, kind=kind):
                r = Radio(dict(FAST, system="am", amRate=fs, videoFormat="pal", nicam=nicam, rxFreq=1255.0e6,
                               txFreq=1255.0e6), conf_path=f"{d}/c.json", audio=NullAudio(realtime=False),
                          data_dir=f"{d}/data")
                r.start()
                try:
                    with mock.patch.object(r.dev, "kind", kind):
                        warn = " ".join(r._tx_refusals()[1])
                finally:
                    r.stop()
                if expect == "":
                    self.assertNotIn("image", warn)
                else:
                    self.assertIn(f"At {fs / 1e6:g} MS/s", warn)
                    self.assertIn("a HackRF's images", warn)
                    if expect:
                        self.assertIn(f"strongest at {expect}", warn)

    def test_the_plan_fits_every_rate_whole(self):
        for fs in (5e6, 6.25e6, 8e6, 10e6, 12.5e6, 16e6, 20e6):
            pl = sysa.plan(fs)
            self.assertTrue(pl["fits"])
            self.assertEqual(pl["video_bw"], 3e6)
            self.assertEqual(pl["sound"] is None, fs < 6e6)
            self.assertAlmostEqual(pl["width"], 3.75e6 if fs < 6e6 else 4.3e6, delta=1)
            self.assertFalse(pl["colour"])
            lo, hi = pl["carrier"] + pl["occupied"][0], pl["carrier"] + pl["occupied"][1]
            self.assertLessEqual(max(-lo, hi), 0.45 * fs)


class Chain(unittest.TestCase):
    """Lock, picture and sound against CNR at the three rates. Thresholds as
    measured (DESIGN.md "405 lines"), each asserted at its own CNR: lock
    (90 % of lines and field sync) 14, 14 and 10 dB; luma PSNR 15 dB at 16,
    16 and 14; sound 20 dB SINAD at 10 and 8 (measured 8 and 6) - at 6.25
    and 10 MS/s, since 5 MS/s carries none (sysa.SOUND_MIN_FS)."""

    POINTS = {5e6: (14, 16, None), 6.25e6: (14, 16, 10), 10e6: (10, 14, 8)}

    def test_thresholds(self):
        for fs, (lock, pic, snd) in self.POINTS.items():
            with self.subTest(fs=fs):
                fr, st, _ = chain(fs, lock)
                self.assertTrue(locked(st), (fs, lock, st))
                fr, st, _ = chain(fs, pic)
                self.assertGreaterEqual(luma_psnr(fr), 15.0)
                if snd is None:
                    continue
                _, _, a = chain(fs, snd, frames=8)
                self.assertGreaterEqual(sinad(a), 20.0)

    def test_clean_picture_and_sound(self):
        for fs in (5e6, 6.25e6):
            with self.subTest(fs=fs):
                fr, st, a = chain(fs, 30)
                self.assertTrue(locked(st))
                self.assertEqual(fr["rgb"].shape[0], 378)
                self.assertGreater(luma_psnr(fr), 25.0)
                if fs < 6e6:
                    self.assertEqual(len(a), 0)              # no sound channel at 5 MS/s
                else:
                    self.assertGreater(sinad(a), 38.0)
                self.assertFalse(st["colour"])

    def test_an_offset_the_filter_is_not_redesigned_for(self):
        # Tracking redesigns the receive filter only for more than 20 kHz;
        # below that the detector takes the rest out itself. A version that
        # summed each line first and corrected the sums afterwards held 47 %
        # of the simulator's 405 station's lines, 14.5 kHz off.
        for offset in (5e3, 14.5e3, -19e3):
            with self.subTest(offset=offset):
                fr, st, _ = chain(5e6, 30, offset=offset)
                self.assertTrue(locked(st) and st["hLock"] > 0.98, st)
                self.assertGreater(luma_psnr(fr), 25.0)

    def test_a_usb_pluto_at_5_msps_through_two_ad936x_radios(self):
        # The nano transmitting at 5 MS/s, received at 5: the radios' own
        # filters on both ends, each modem undoing its own radio's.
        fr, st, a = chain(5e6, 30, link=True)
        self.assertTrue(locked(st))
        self.assertGreater(luma_psnr(fr), 25.0)
        fr, st, _ = chain(5e6, 14, link=True)
        self.assertTrue(locked(st))
        # At 6.25 MS/s with the sound, its carrier placed for the images
        # (sysa._placement): through both radios' filters it still comes out.
        fr, st, a = chain(6.25e6, 30, link=True)
        self.assertTrue(locked(st))
        self.assertGreater(luma_psnr(fr), 25.0)
        self.assertGreater(sinad(a), 30.0)

    def test_vertical_edges_are_straight(self):
        # 405 lines are never a whole number of samples, so each line is
        # moved by its own fraction of a sample (decoder.py): without it a
        # vertical edge zig-zags by up to half a sample from line to line.
        img = np.zeros((576, 720, 3), np.float32)
        img[:, 360:] = 1.0
        fr, _, _ = chain(5e6, 60, img=img, offset=0)
        y = luma(fr["rgb"].astype(np.float32) / 255)[20:-20]
        x = np.array([crossings(row, 0.5, False)[0] for row in y])
        us = x / (fr["rgb"].shape[1] / (LINE_US - 18.0))
        self.assertLess(float(np.std(us)), 0.01, f"edge wanders {np.std(us) * 1e3:.0f} ns")


class NoSignal(unittest.TestCase):
    def test_noise_never_claims_a_405_field(self):
        for fs in (5e6, 6.25e6, 10e6):
            with self.subTest(fs=fs):
                p = {"videoFormat": "405"}
                dem, dec = systems.make_demod(fs, "am", p), systems.make_decoder(fs, "am", p)
                self.assertIsNone(first_claim(dem, dec, fs, 120, seed=int(fs) % 97))


class Standards(unittest.TestCase):
    """405 is never taken for 625 lines, nor 625 for 405: by the receiver's
    choice (sysa.detect) or by either decoder locking to the other."""

    PICS = {"card": testcard.test_card("SIM", "X", "Y"), "bars": testcard.colour_bars("SIM"),
            "black": np.zeros((576, 720, 3), np.float32), "grey": np.full((576, 720, 3), 0.5, np.float32)}

    def test_detect_names_the_line_standard(self):
        for fs in (5e6, 10e6, 16e6):
            for fmt in ("405", "pal"):
                for name, img in self.PICS.items():
                    loop = systems.build_loop(fs, "am", {"videoFormat": fmt}, img)
                    car = systems.plan(fs, "am", {"videoFormat": fmt})["carrier"]
                    for cnr in (30, 6):
                        with self.subTest(fs=fs, fmt=fmt, pic=name, cnr=cnr):
                            iq = channel(loop[:int(0.04 * fs)], cnr, 15e3, fs, 0, np.random.default_rng(3))
                            got, feats = sysa.detect(iq, fs, car)
                            if fmt == "pal" and name == "black":
                                # 625's sync is only 24 % above black: named,
                                # or not named, never called 405.
                                self.assertIn(got, ("pal", None), feats)
                            else:
                                self.assertEqual(got, fmt, feats)

    def test_noise_is_neither(self):
        for fs in (5e6, 10e6):
            rng = np.random.default_rng(int(fs) % 89)
            n = int(0.04 * fs)
            for _ in range(5):
                iq = (rng.standard_normal(n) + 1j * rng.standard_normal(n)).astype(np.complex64)
                self.assertIsNone(sysa.detect(iq, fs, 1e6)[0])

    def test_neither_decoder_locks_to_the_other_standard(self):
        for fs in (5e6, 10e6):
            for sent, rx in (("pal", "405"), ("405", "pal")):
                with self.subTest(fs=fs, sent=sent):
                    loop = systems.build_loop(fs, "am", {"videoFormat": sent}, self.PICS["card"])
                    dem = systems.make_demod(fs, "am", {"videoFormat": rx})
                    dec = systems.make_decoder(fs, "am", {"videoFormat": rx})
                    rng = np.random.default_rng(2)
                    n = int(0.04 * fs)
                    for k in range(30):
                        iq = channel(loop[(k % 4) * n:(k % 4 + 1) * n], 30, 0, fs, k * n, rng)
                        if k % 2 == 0:
                            dem.track(iq)
                        self.assertEqual(dec.feed(dem(iq)), [])
                        self.assertFalse(dec.stats["vLock"])

    def test_fm_detection_is_untouched(self):
        # 405 lines are AM only: an FM receiver is never offered them.
        self.assertEqual(narrow.SYSTEMS["405"], ("am",))
        self.assertEqual(narrow.fmt_for("fm", {"videoFormat": "405"}, 5e6), "pal")
        self.assertFalse(narrow.is_narrow("405"))


if __name__ == "__main__":
    unittest.main()
