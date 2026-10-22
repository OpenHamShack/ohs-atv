"""NICAM-728 (ohsatv/nicam.py): the frame coding against EN 300 163 and
against hacktv's encoder, the modulation, and the sound through the AM-VSB
and FM chains with the AD936x's filters, noise and a carrier offset.

The chain figures (bit-error rate against CNR over the whole sample band,
SINAD, separation) are the ones DESIGN.md quotes; the assertions hold them
with a little room, never the other way round."""
import math
import os
import unittest

import numpy as np

from helpers import ad936x_link, channel
from ohsatv import modem, nicam, pal, testcard
from ohsatv.decoder import VideoDecoder
from ohsatv.devices import ad936x

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CARD = testcard.test_card("GM4BRI", "IO86 TEST")


def tone(x, f, rate=48000):
    """(amplitude of the first, SINAD dB) of tones at known frequencies
    (`f` one or a list), by least squares."""
    x = np.asarray(x, np.float64)
    x = x - x.mean()
    t = np.arange(len(x)) / rate
    fl = f if isinstance(f, (list, tuple)) else [f]
    a = np.concatenate([np.stack([np.sin(2 * np.pi * q * t), np.cos(2 * np.pi * q * t)], 1) for q in fl], 1)
    c, *_ = np.linalg.lstsq(a, x, rcond=None)
    fit = a @ c
    return float(np.hypot(c[0], c[1])), 10 * math.log10(np.sum(fit ** 2) / max(np.sum((x - fit) ** 2), 1e-30))


def stereo_test(frames: int) -> np.ndarray:
    """The test signal the transmitter offers: 1 kHz on the left, 2 kHz on
    the right, half of full scale, as (n, 2) at 48 kHz."""
    t = np.arange(1920 * frames) / 48000
    return np.stack([0.5 * np.sin(2 * np.pi * 1000 * t), 0.5 * np.sin(2 * np.pi * 2000 * t)], 1).astype(np.float32)


def bit_errors(sent: list, got: list) -> tuple[float, int]:
    """Bit-error rate of the receiver's decided bits against the
    transmitter's, aligned on the best offset (the chain's delay)."""
    s = np.concatenate([x.reshape(-1) for x in sent])
    g = np.concatenate(got)
    n = min(20000, len(g) - 4000)
    best = None
    for d in range(-4000, 4000, 2):
        a = 2000 + d
        if a < 0 or a + n > len(s):
            continue
        e = np.count_nonzero(s[a:a + n] != g[2000:2000 + n])
        if best is None or e < best[0]:
            best = (e, a)
    a = best[1]
    m = min(len(s) - a, len(g) - 2000)
    return float(np.mean(s[a:a + m] != g[2000:2000 + m])), m


class Standard(unittest.TestCase):
    """EN 300 163, clause by clause."""

    def test_the_scrambling_sequence_starts_as_the_standard_gives_it(self):
        # 4.1.3 d): "the sequence shall start 0000 0111 1011 1110 0010".
        self.assertEqual("".join(map(str, nicam.PRBS[:20])), "00000111101111100010")
        self.assertEqual(len(nicam.PRBS), 720)

    def test_interleaving_puts_neighbours_sixteen_bits_apart(self):
        # 4.1.2: transmitted bits 25, 26, 27... carry frame bits 25, 69, 113...
        self.assertEqual(list(nicam.TX_ORDER[24:28] + 1), [25, 69, 113, 157])
        self.assertEqual(int(nicam.TX_ORDER[24 + 15]) + 1, 685)
        self.assertEqual(int(nicam.TX_ORDER[-1]) + 1, 728)
        where = np.argsort(nicam.TX_ORDER)
        gaps = np.diff(where[24:])
        self.assertGreaterEqual(int(np.abs(gaps).min()), 16)

    def test_scale_factors_follow_the_coding_ranges_of_figure_3(self):
        cases = [(8191, 7), (4096, 7), (4095, 6), (2048, 6), (2047, 5), (1024, 5), (1023, 3), (512, 3),
                 (511, 4), (256, 4), (255, 2), (128, 2), (127, 1), (0, 1), (-8192, 7), (-4097, 7), (-4096, 6),
                 (-129, 2), (-128, 1)]
        for x, want in cases:
            blk = np.zeros(32, np.int64)
            blk[5] = x
            self.assertEqual(int(nicam.scale_factor(blk)), want, f"sample {x}")

    def test_companding_keeps_ten_bits_of_every_range(self):
        rng = np.random.default_rng(3)
        for top in (8191, 4000, 2000, 1000, 500, 200, 100):
            blk = rng.integers(-top - 1, top + 1, (40, 32))
            sc = nicam.scale_factor(blk)
            y = nicam.expand(nicam.compress(blk, sc), sc)
            step = 1 << nicam._SHIFT_OF_SCALE[sc][:, None]
            self.assertTrue((np.abs(y - blk) <= step // 2 + (step == 1)).all(), f"range up to {top}")

    def test_frames_round_trip_bit_for_bit_in_stereo_and_mono(self):
        rng = np.random.default_rng(1)
        for mode in ("stereo", "mono", "dual"):
            words = rng.integers(0, 1024, (64, 64))
            scales = rng.integers(0, 8, (64, 2))
            bits = nicam.encode_frames(words, scales, frame0=5, mode=mode, c4=1)
            self.assertEqual(bits.shape, (64, 728))
            self.assertTrue((bits[:, :8] == nicam.FAW).all())
            d = nicam.decode_frames(bits)
            np.testing.assert_array_equal(d["words"], words)
            np.testing.assert_array_equal(d["scales"], scales)
            self.assertFalse(d["errors"].any())
            self.assertTrue((d["mode"] == mode).all())
            # C0: 1 for the first eight frames of each 16-frame sequence.
            np.testing.assert_array_equal(d["c0"], ((5 + np.arange(64)) % 16 < 8).astype(np.uint8))

    def test_scale_factors_survive_four_errors_in_nine_and_parity_finds_the_hit_samples(self):
        words = np.full((1, 64), 0x155)
        scales = np.array([[6, 3]])
        bits = nicam.encode_frames(words, scales)
        # Corrupt the MSB of four of R2A's nine words (A1, A7, A13, A19 -
        # words 0, 6, 12, 18), wherever interleaving put those bits.
        frame_pos = np.argsort(nicam.TX_ORDER)
        hit = [0, 6, 12, 18]
        for w in hit:
            bits[0, frame_pos[24 + 11 * w + 9]] ^= 1
        d = nicam.decode_frames(bits)
        np.testing.assert_array_equal(d["scales"], scales)
        self.assertEqual(sorted(np.flatnonzero(d["errors"][0]).tolist()), hit)

    def test_phase_changes_are_table_4s(self):
        # 00 0, 01 -90, 11 -180, 10 -270 degrees: quarter turns
        # counter-clockwise 0, 3, 2, 1.
        steps = nicam.bits_to_steps([0, 0, 0, 1, 1, 1, 1, 0])
        self.assertEqual(list(steps), [0, 3, 2, 1])
        np.testing.assert_array_equal(nicam.steps_to_bits(steps), [0, 0, 0, 1, 1, 1, 1, 0])

    def test_pre_emphasis_is_j17_within_a_twentieth_of_a_db(self):
        f = np.geomspace(20, 15000, 60)
        w = 2 * np.pi * f / 3000
        want = 10 * np.log10((1 + w ** 2) / (75 + w ** 2))
        got = 20 * np.log10(nicam.j17_response(f))
        self.assertLess(float(np.abs(got - want).max()), 0.06)
        # And de-emphasis undoes it exactly.
        x = np.random.default_rng(2).standard_normal(4000)
        y = nicam.Emphasis(inverse=True)(nicam.Emphasis()(x))
        np.testing.assert_allclose(y, x, atol=1e-9)

    def test_the_alignment_level_is_where_4_2_5_2_puts_it(self):
        # A 400 Hz tone at the level that deviates System I's FM sound by
        # +-17 kHz comes out 24.3 dB under the coding range's maximum.
        coder = nicam.AudioCoder(close_loop=False)
        t = np.arange(48000) / 48000
        coder.push((nicam.ALIGN * np.sin(2 * np.pi * 400 * t)).astype(np.float32))
        frames = coder.frames_ready()
        bits = coder.take(frames)
        d = nicam.decode_frames(bits)
        a = nicam.expand(d["words"][:, 0::2], d["scales"][:, 0])[200:].reshape(-1)
        peak_db = 20 * math.log10(np.abs(a).max() / 8192)
        self.assertAlmostEqual(peak_db, -24.3, delta=0.15)


class Hacktv(unittest.TestCase):
    """hacktv's encoder (fsphil/hacktv src/nicam728.c), run on 48 frames of
    stereo audio covering every coding and protection range: its frames,
    and the samples after its J.17 filter, are in tests/data. Ours, given
    the same samples, must make the same frames."""

    def test_our_frames_are_hacktvs_bit_for_bit(self):
        x14 = np.load(os.path.join(DATA, "hacktv_nicam_in.npy")).astype(np.int64)
        with open(os.path.join(DATA, "hacktv_nicam_frames.bin"), "rb") as f:
            ref = np.frombuffer(f.read(), np.uint8).reshape(-1, 91)
        F = len(ref)
        a, b = x14[:, 0].reshape(F, 32), x14[:, 1].reshape(F, 32)
        sa, sb = nicam.scale_factor(a), nicam.scale_factor(b)
        self.assertEqual(set(sa.tolist()) | set(sb.tolist()), {1, 2, 3, 4, 5, 6, 7})
        w = np.empty((F, 64), np.int64)
        w[:, 0::2] = nicam.compress(a, sa)
        w[:, 1::2] = nicam.compress(b, sb)
        ours = np.packbits(nicam.encode_frames(w, np.stack([sa, sb], 1), 0, "stereo", 1), axis=1)
        np.testing.assert_array_equal(ours, ref)


class Modulation(unittest.TestCase):
    def test_baseband_occupies_364_khz_each_side_at_unit_power(self):
        fs = 10e6
        tx = nicam.Transmitter(fs)
        y = np.concatenate([tx(stereo_test(1), 400000) for _ in range(5)])[400000:]
        self.assertAlmostEqual(float(np.mean(np.abs(y) ** 2)), 1.0, delta=0.05)
        self.assertLess(float(np.abs(y).max()), nicam.PEAK)
        n = 1 << 16
        p = (np.abs(np.fft.fft(y[:16 * n].reshape(16, n) * np.hanning(n), axis=1)) ** 2).mean(0)
        f = np.fft.fftfreq(n, 1 / fs)
        inside = p[np.abs(f) <= 364e3].sum()
        outside = p[np.abs(f) > 420e3].sum()
        self.assertLess(10 * math.log10(outside / inside), -45.0)

    def test_baseband_round_trip_is_bit_exact_with_clean_stereo(self):
        fs = 10e6
        n = 400000
        tx = nicam.Transmitter(fs)
        rx = nicam.Receiver(fs, centre=1.5e6)
        tx.sent, rx.got = [], []
        aud = stereo_test(20)
        ph = np.exp(2j * np.pi * (1.5e6 + 2500) * np.arange(20 * n) / fs).astype(np.complex64)
        out = []
        for k in range(20):
            out.append(rx(tx(aud[k * 1920:(k + 1) * 1920], n) * ph[k * n:(k + 1) * n]))
        e, m = bit_errors(tx.sent, rx.got)
        self.assertEqual(e, 0.0)
        self.assertGreater(m, 200000)
        self.assertTrue(rx.stats["lock"] and rx.stats["mode"] == "stereo" and rx.stats["c4"] == 1)
        lr = np.concatenate(out)[-9600:]
        aL, sL = tone(lr[:, 0], 1000)
        aR, sR = tone(lr[:, 1], 2000)
        self.assertAlmostEqual(aL, 0.5, delta=0.01)
        self.assertAlmostEqual(aR, 0.5, delta=0.01)
        self.assertGreater(sL, 60.0)
        self.assertGreater(sR, 60.0)
        # Separation: the right channel's tone does not appear on the left.
        leak, _ = tone(lr[:, 0], 2000)
        self.assertLess(20 * math.log10(max(leak, 1e-12) / aR), -60.0)

    def test_a_loop_of_160_frames_closes_its_phase(self):
        # The 0.16 s transmit loop of a static picture is 160 frames: the
        # transmitter's phase must be back where it started, or the loop
        # steps once a turn (module docstring).
        def sums(close):
            coder = nicam.AudioCoder(close_loop=close)
            # Not the 1 and 2 kHz test tones: they repeat every millisecond,
            # so every frame is the same and the sums close by themselves.
            t = np.arange(48000) / 48000
            coder.push(np.stack([0.3 * np.sin(2 * np.pi * 700 * t), 0.3 * np.sin(2 * np.pi * 1300 * t)], 1))
            bits = coder.take(160)
            return [int(nicam.bits_to_steps(g.reshape(-1)).sum()) % 4 for g in bits.reshape(10, 16, 728)]
        # Every 16-frame sequence ends where it began, so any loop of whole
        # sequences does.
        self.assertEqual(sums(True), [0] * 10)
        # Without the AD bits chosen, sequences carrying sound would not.
        self.assertNotEqual(sums(False), [0] * 10)


def chain(system, fs, cnr, frames=10, offset=19e3, seed=1, dev=7e6, nicam_on=True, link=True, card=CARD):
    """Picture and NICAM through the whole chain, the radios' filters at
    both ends: (decoder stats, receiver stats, BER, the receiver's (n, 2)
    sound, the analogue sound)."""
    rng = np.random.default_rng(seed)
    tx_shape = ad936x.Response("tx", fs, fs) if link else None
    rx_shape = ad936x.Response("rx", fs, fs) if link else None
    if system == "am":
        mod = modem.AmVsbMod(fs, nicam_on=nicam_on, tx_shape=tx_shape)
        dem = modem.AmVsbDemod(fs, nicam_on=nicam_on, rx_shape=rx_shape)
    else:
        mod = modem.FmMod(fs, deviation=dev, nicam_on=nicam_on, tx_shape=tx_shape)
        dem = modem.FmDemod(fs, deviation=dev, nicam_on=nicam_on, rx_shape=rx_shape)
    if mod.nicam is not None:
        mod.nicam.sent = []
    dem.nicam.got = []
    enc = pal.Encoder(fs, colour=mod.plan["colour"])
    dec = VideoDecoder(fs)
    aud = stereo_test(frames)
    comps = [enc.encode(card, k) for k in range(4)]
    pos = 0
    nic, snd = [], []
    for k in range(frames):
        iq = mod(comps[k % 4], aud[k * 1920:(k + 1) * 1920])
        if link:
            iq = ad936x_link(iq, fs)
        iq = channel(iq, cnr, offset, fs, pos, rng)
        pos += len(iq)
        if system == "am" and k % 2 == 0:
            dem.track(iq)
        dec.feed(dem(iq))
        snd.append(dem.audio(iq))
        nic.append(dem.nicam_audio(iq))
    ber = bit_errors(mod.nicam.sent, dem.nicam.got)[0] if mod.nicam is not None else None
    return dec.stats, dict(dem.nicam.stats), ber, np.concatenate(nic), np.concatenate(snd)


class AmChain(unittest.TestCase):
    """AM-VSB at 10 MS/s - the lowest rate that carries NICAM, with the
    vision carrier moved 0.43 MHz further down to make room - through two
    AD936x, with a 19 kHz carrier offset. Measured (bit errors, against
    the receiver's own estimate from the parity checks): 0 at 20 dB CNR,
    1.9e-4 at 15, 1.1e-3 at 14, 9e-3 at 12, where the picture still locks
    on 94 % of its lines - NICAM at -20 dB fails where System I meant it
    to, with the picture (EN 300 163 clause 1 a)."""

    def test_error_free_at_20_db_with_clean_stereo(self):
        vid, st, ber, lr, snd = chain("am", 10e6, 20.0)
        self.assertTrue(vid["vLock"])
        self.assertEqual(ber, 0.0)
        self.assertTrue(st["lock"])
        self.assertEqual(st["mode"], "stereo")
        aL, sL = tone(lr[-9600:, 0], 1000)
        aR, sR = tone(lr[-9600:, 1], 2000)
        self.assertAlmostEqual(aL, 0.5, delta=0.02)
        self.assertGreater(min(sL, sR), 60.0)
        leak, _ = tone(lr[-9600:, 0], 2000)
        self.assertLess(20 * math.log10(max(leak, 1e-12) / aR), -60.0)

    def test_errors_rise_with_noise_and_the_estimate_follows_them(self):
        res = {}
        for cnr in (15.0, 12.0):
            vid, st, ber, _, _ = chain("am", 10e6, cnr, seed=4)
            res[cnr] = (ber, st["ber"], vid["hLock"])
        self.assertLess(res[15.0][0], 1e-3)
        self.assertGreater(res[12.0][0], 2e-3)
        for cnr, (ber, est, _) in res.items():
            self.assertAlmostEqual(math.log10(max(est, 1e-6)), math.log10(max(ber, 1e-6)), delta=0.5,
                                   msg=f"{cnr} dB: estimate {est:.2g} against {ber:.2g}")
        self.assertGreater(res[12.0][2], 0.9)          # the picture is still there

    def test_the_vision_carrier_moves_for_nicam_and_the_signal_stays_centred(self):
        # Where centred keeps an AD936x's images under the limit (12.5 MS/s
        # and up), NICAM moves the vision carrier down so the whole signal
        # stays centred.
        for fs in (12.5e6, 16e6):
            with_n = modem.am_plan(fs, nicam_on=True)
            without = modem.am_plan(fs)
            self.assertLess(with_n["carrier"], without["carrier"])
            lo, hi = with_n["occupied"]
            self.assertAlmostEqual(with_n["carrier"] + lo, -(with_n["carrier"] + hi), delta=20e3)
            self.assertAlmostEqual(with_n["nicam"] - with_n["carrier"], nicam.CARRIER_I, delta=10)
            self.assertEqual(with_n["video_bw"], without["video_bw"])

    def test_at_10_msps_the_carriers_go_either_side_of_the_lo_with_nicam_too(self):
        # At 10 MS/s centred would leave the sound carrier's image at -25 dB
        # (-36 with NICAM's shift), so vision and sound go equally far either
        # side of the LO, NICAM or not; NICAM's own image, 20 dB under peak
        # vision to start with, stays under the limit, and a receiver at this
        # rate still has NICAM's whole band in its flat region.
        with_n = modem.am_plan(10e6, nicam_on=True)
        without = modem.am_plan(10e6)
        for pl in (with_n, without):
            self.assertAlmostEqual(pl["carrier"], -pl["sound"], delta=25)
            self.assertLessEqual(pl["txImage"]["db"], modem.IMAGE_LIMIT_DB)
        self.assertAlmostEqual(with_n["nicam"] - with_n["carrier"], nicam.CARRIER_I, delta=10)
        self.assertTrue(with_n["nicamRx"])
        self.assertEqual(with_n["video_bw"], without["video_bw"])
        self.assertGreater(modem.am_plan(10e6, nicam_on=True, placement="centred")["txImage"]["db"], -37)


class FmChain(unittest.TestCase):
    """FM-ATV to IARU V.2 at 20 MS/s through two AD936x. NICAM's subcarrier
    at 265 kHz RMS deviation: error-free to 25 dB CNR, ~2e-3 at 20, lost
    by 15, while the picture locks to ~5 dB and the analogue sound gives
    ~39 dB SINAD at 20 - on FM the analogue sound is the fallback that
    matters. A floor of ~6e-5 at high CNR comes from the radios' filters
    cutting NICAM's outer sidebands while the carrier swings to peak
    white; concealment hides it (SINAD > 60 dB)."""

    def test_clean_at_30_db(self):
        vid, st, ber, lr, _ = chain("fm", 20e6, 30.0, frames=8)
        self.assertTrue(vid["vLock"])
        self.assertTrue(st["lock"])
        self.assertLess(ber, 3e-4)
        aL, sL = tone(lr[-9600:, 0], 1000)
        self.assertAlmostEqual(aL, 0.5, delta=0.02)
        self.assertGreater(sL, 50.0)

    def test_fails_before_the_picture_and_the_analogue_sound(self):
        vid, st, ber, _, snd = chain("fm", 20e6, 15.0, frames=8, seed=2)
        self.assertGreater(ber, 1e-2)
        self.assertFalse(st["lock"] and st["ber"] is not None and st["ber"] < nicam.USE_BER)
        self.assertTrue(vid["vLock"])
        # The analogue subcarrier carries the compatible mono, (L + R) / 2.
        self.assertGreater(tone(snd[-9600:], [1000, 2000])[1], 30.0)


class FalseLock(unittest.TestCase):
    def test_noise_never_locks(self):
        rx = nicam.Receiver(10e6, centre=3.7e6)
        rng = np.random.default_rng(9)
        for _ in range(50):                                  # 2 s
            x = (rng.standard_normal(400000) + 1j * rng.standard_normal(400000)).astype(np.complex64)
            rx(x)
            self.assertFalse(rx.stats["lock"])
            self.assertFalse(rx.active)

    def test_plain_analogue_sound_never_locks(self):
        # A station with its FM sound and no NICAM, received where NICAM
        # would be: the FM sound carrier 552 kHz below, the picture's
        # upper sideband, nothing at 6.552 MHz.
        fs = 10e6
        vid, st, ber, lr, snd = chain("am", fs, 35.0, frames=25, nicam_on=False, link=False)
        self.assertIsNone(ber)
        self.assertFalse(st["lock"])
        self.assertEqual(len(lr), 0)
        vid, st, ber, lr, snd = chain("fm", 20e6, 35.0, frames=25, nicam_on=False, link=False)
        self.assertFalse(st["lock"])


class WithTeletext(unittest.TestCase):
    """NICAM beside teletext (ohsatv/vbi.py) on the 625 modes that carry
    both - AM-VSB from 10 MS/s, FM-ATV at 20 - through two AD936x. They
    share nothing: NICAM is a carrier 6.552 MHz above vision, teletext is
    in the field blanking of the picture. Each must arrive as it does
    alone."""

    def run_both(self, system, fs, cnr, frames=10):
        from ohsatv import narrow, ttx, vbi
        p = {"nicam": True, "fmDeviation": 7e6}
        rng = np.random.default_rng(3)
        shape = (ad936x.Response("tx", fs, fs), ad936x.Response("rx", fs, fs))
        enc = narrow.make_encoder(fs, system, p)
        mod = narrow.make_mod(fs, system, p, tx_shape=shape[0])
        dem = narrow.make_demod(fs, system, p, rx_shape=shape[1])
        dec = narrow.make_decoder(fs, system, p)
        pl = narrow.plan(fs, system, p)
        self.assertIsNotNone(pl["nicam"])
        self.assertTrue(pl["teletext"])
        enc.colour = enc.colour and pl["colour"]
        mod.nicam.sent, dem.nicam.got = [], []
        ins = vbi.Inserter(fs)
        aud = stereo_test(frames)
        sent, got, st, pos = [], [], None, 0
        for k in range(frames):
            comp = enc.encode(CARD, k)
            pk = [ttx.row(1 + i % 8, 1 + i % 24, bytes(rng.integers(32, 127, 40).astype(np.uint8)))
                  for i in range(32)]
            ins.insert(comp, pk[:16], pk[16:])
            sent += pk
            iq = channel(ad936x_link(mod(comp, aud[k * 1920:(k + 1) * 1920]), fs), cnr, 19e3, fs, pos, rng)
            pos += len(iq)
            if system == "am":
                dem.track(iq)
            for f in dec.feed(dem(iq)):
                got += f["ttx"]
                st = f["stats"]
            dem.nicam_audio(iq)
        return bit_errors(mod.nicam.sent, dem.nicam.got)[0], sent, got, st

    def test_both_arrive_on_am_vsb_and_fm(self):
        for system, fs, cnr in (("am", 10e6, 30.0), ("fm", 20e6, 30.0)):
            ber, sent, got, st = self.run_both(system, fs, cnr)
            self.assertLess(ber, 3e-4, system)
            self.assertGreater(len(got), 0.7 * len(sent), system)
            self.assertTrue(set(got) <= set(sent), system)
            self.assertTrue(st["colour"], system)


class Plans(unittest.TestCase):
    def test_what_carries_nicam(self):
        for fs in (5e6, 6.25e6, 8e6):
            p = modem.am_plan(fs, nicam_on=True)
            self.assertIsNone(p["nicam"])
            self.assertIn("no NICAM", " ".join(p["why"]))
        for fs in (10e6, 12.5e6, 16e6, 20e6):
            self.assertIsNotNone(modem.am_plan(fs, nicam_on=True)["nicam"], fs)
        self.assertIsNotNone(modem.fm_plan(20e6, 7e6, nicam_on=True)["nicam"])
        self.assertIsNotNone(modem.fm_plan(16e6, 3.0e6, nicam_on=True)["nicam"])
        p = modem.fm_plan(16e6, 4.0e6, nicam_on=True)
        self.assertIsNone(p["nicam"])
        self.assertIn("p-p at most", p["nicamWhy"])
        p = modem.fm_plan(12.5e6, 1.2e6, sub=5.5e6, nicam_on=True)
        self.assertIsNone(p["nicam"])
        self.assertIn("16 MS/s", p["nicamWhy"])
        p = modem.fm_plan(20e6, 7e6, sub=6.5e6, nicam_on=True)
        self.assertIsNone(p["nicam"])
        self.assertIn("6.5 MHz", p["nicamWhy"])
        p = modem.am_plan(10e6, sound_offset=6.5e6, nicam_on=True)
        self.assertIsNone(p["nicam"])

    def test_nothing_changes_when_nicam_is_off(self):
        for fs in (5e6, 8e6, 10e6, 20e6):
            a = modem.am_plan(fs)
            self.assertIsNone(a["nicam"])
            self.assertEqual(a["why"], [w for w in a["why"] if "NICAM" not in w])
        f = modem.fm_plan(20e6, 7e6)
        self.assertAlmostEqual(f["carson"], 17.84e6, delta=0.05e6)


class Fallback(unittest.TestCase):
    def test_nicam_is_heard_only_while_it_is_better_than_the_fm_sound(self):
        rx = nicam.Receiver(10e6, centre=3.7e6)
        rx.stats.update(lock=True, mode="stereo", ber=1e-4)
        self.assertTrue(rx.active)
        rx.stats["ber"] = 8e-4                  # between the two: stays
        self.assertTrue(rx.active)
        rx.stats["ber"] = 2e-3
        self.assertFalse(rx.active)
        rx.stats["ber"] = 8e-4                  # and does not come back until under USE_BER
        self.assertFalse(rx.active)
        rx.stats["ber"] = 1e-4
        self.assertTrue(rx.active)
        rx.stats.update(mode="data")
        self.assertFalse(rx.active)


if __name__ == "__main__":
    unittest.main()
