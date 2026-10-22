"""The engine API, driven headless: lifecycle, validation, the transmit
guard and safety timers, and the whole loop - our own test card transmitted
into the simulator, received, decoded, and compared with what was sent.

"Hardware" here is the simulator with `real = True` and the range and
duplex of the radio being imitated: the guard must treat it exactly as it
would a Pluto. Nothing in these tests opens a real radio."""
import json
import os
import sys
import time
import unittest

import numpy as np

from helpers import crop, luma, narrow_station, psnr, resample_width, tmp_dir
from ohsatv import testcard
from ohsatv.audio import NullAudio
from ohsatv.devices.sim import SimDevice
from ohsatv import state
from ohsatv.radio import Radio, TxRefused

FAST = dict(device="sim", audioBackend="null", system="am", band="23cm", rxFreq=1249.0e6, txFreq=1249.0e6,
            amRate=10_000_000, simStations=True)


class FakeHardware(SimDevice):
    """The simulator dressed as a real radio."""
    kind = "fakehw"
    real = True

    def __init__(self, cfg, fs, rng=(325e6, 3800e6), duplex=False):
        super().__init__(cfg, fs)
        self._rng, self._duplex = rng, duplex

    def freq_range(self):
        return self._rng

    def duplex(self):
        return self._duplex


def wait_for(fn, timeout=8.0, radio=None, feeds=()):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if radio is not None:
            radio.heartbeat()
        for f in feeds:
            f.drain()
        r = fn()
        if r:
            return r
        time.sleep(0.05)
    return None


class Base(unittest.TestCase):
    def make(self, device=None, **over):
        self.tmp = str(tmp_dir(self))
        cfg = dict(FAST)
        cfg.update(over)
        r = Radio(cfg, conf_path=__import__("pathlib").Path(self.tmp) / "c.json", audio=NullAudio(realtime=False),
                  device=device, data_dir=__import__("pathlib").Path(self.tmp) / "data")
        self.addCleanup(r.stop)
        return r


class Lifecycle(Base):
    def test_constructing_opens_and_keys_nothing(self):
        r = self.make()
        self.assertFalse(r.running)
        self.assertFalse(r.state()["keyed"])
        self.assertIsNone(r.dev)

    def test_start_receives_and_decodes_the_simulated_station(self):
        r = self.make(rxFreq=1275.0e6, txFreq=1275.0e6)       # the sim's AM-VSB station
        video = r.subscribe("video", 8)
        r.start()
        self.assertFalse(r.state()["keyed"])
        got = wait_for(lambda: video.drain(), radio=r)
        self.assertTrue(got)
        t, rgb, stats = got[-1]
        self.assertEqual(rgb.shape[0], 576)
        self.assertTrue(stats["vLock"])
        st = r.state()
        self.assertEqual(st["rxLock"], "locked")
        self.assertGreater(st["snr"], 20)

    def test_the_meter_ticks_once_a_second_whatever_the_source(self):
        # It once went out only with the video-file source, its line
        # indented under that source's status by an unrelated change.
        r = self.make(source="testcard")
        meter = r.subscribe("meter", 8)
        r.start()
        got = wait_for(lambda: meter.drain(), timeout=4.0, radio=r)
        self.assertTrue(got)
        self.assertEqual(set(got[-1]), {"t", "snr", "audioDb", "hLock", "keyed"})

    def test_spectrum_feed_is_in_rf_hz(self):
        r = self.make()
        spec = r.subscribe("spectrum", 8)
        r.start()
        items = wait_for(lambda: spec.drain(), radio=r)
        t, start, bin_hz, db = items[-1]
        centre = start + bin_hz * len(db) / 2
        # AM-VSB: the LO sits above the vision carrier, by the plan's offset.
        self.assertAlmostEqual(centre, 1249.0e6 - r.plan()["carrier"], delta=bin_hz)
        self.assertEqual(len(db), 1024)


class LaggingLo(FakeHardware):
    """A radio whose samples reach the host late, as a Pluto's do: for
    `old` reads after a retune they are still from the old LO, then for
    `unsure` reads it cannot say (read_lo None), then the new LO."""

    def __init__(self, cfg, fs, old=6, unsure=4):
        super().__init__(cfg, fs, duplex=True)
        self.lag = (old, unsure)
        self.prev = None
        self.since = 10 ** 9
        self.last = None

    def set_rx_lo(self, hz):
        self.prev, self.since = self.rx_lo, 0
        super().set_rx_lo(hz)

    def read(self, n):
        iq = super().read(n)
        old, unsure = self.lag
        k, self.since = self.since, self.since + 1
        self.last = self.prev if k < old else None if k < old + unsure else self.rx_lo
        return iq

    @property
    def read_lo(self):
        return self.last


class SpectrumLabels(Base):
    def test_frames_carry_the_lo_their_samples_were_taken_at(self):
        # 5 October 2026: after format recognition moved libre-2's LO, frames
        # still holding the old LO's samples went out labelled with the new
        # one, and the guard took the vision carrier, seen through the old
        # LO, for a new signal (docs/atv-research.md 21a).
        dev = LaggingLo(dict(FAST), 10e6)
        r = self.make(device=dev)
        spec = r.subscribe("spectrum", 256)
        r.start()
        self.assertTrue(wait_for(lambda: dev.since > 12, radio=r))
        lo_a = dev.rx_lo
        spec.drain()
        r.set(rxFreq=1255.0e6)
        lo_b = dev.rx_lo
        self.assertNotAlmostEqual(lo_a, lo_b, delta=1e6)
        seen = []

        def enough():
            seen.extend(spec.drain())
            return dev.since > 20
        self.assertTrue(wait_for(enough, radio=r))
        centres = [round(start + bin_hz * len(db) / 2) for _t, start, bin_hz, db in seen]
        self.assertTrue(set(centres) <= {round(lo_a), round(lo_b)}, centres)
        # The old LO's frames come first, labelled as such; none after the new.
        first_b = centres.index(round(lo_b))
        self.assertGreater(first_b, 0)
        self.assertNotIn(round(lo_a), centres[first_b:])
        # The frames the radio could not place (four reads, so two spectra:
        # one every other block) are missing, not guessed: at 25 blocks a
        # second, the gap between the last old frame and the first new one.
        ts = [t for t, *_ in seen]
        self.assertGreater(ts[first_b] - ts[first_b - 1], 3 * 0.04)


class Validation(Base):
    def test_set_validates_and_explains(self):
        r = self.make()
        with self.assertRaises(ValueError):
            r.set(nonsense=1)
        with self.assertRaises(ValueError):
            r.set(keyed=True)                         # status
        with self.assertRaisesRegex(ValueError, "Settings"):
            r.set(txGainCeiling=0.0)                  # auth key from the main window
        with self.assertRaisesRegex(ValueError, "outside 23cm"):
            r.set(rxFreq=1400e6)
        with self.assertRaisesRegex(ValueError, "ceiling"):
            r.set(txGain=-1.0)                        # default ceiling -10 dB
        self.assertEqual(r.set(txGain=-20.0), {"txGain": -20.0})

    def test_band_change_moves_to_the_bands_default_channel(self):
        r = self.make()
        r.set(band="6cm")
        st = r.state()
        self.assertEqual(st["rxFreq"], 5665.0e6)
        self.assertEqual(st["system"], "fm")

    def test_lowering_the_ceiling_brings_the_gain_down(self):
        r = self.make()
        r.set(txGain=-12.0)
        r.set(authorise=True, txGainCeiling=-40.0)
        self.assertEqual(r.state()["txGain"], -40.0)

    def test_split_off_ties_transmit_to_receive(self):
        r = self.make(rxFreq=1275.0e6, txFreq=1310.0e6)        # a config that disagrees with itself
        self.assertEqual(r.state()["txFreq"], 1275.0e6)
        r.set(rxFreq=1255.0e6)
        self.assertEqual(r.state()["txFreq"], 1255.0e6)
        r.set(split=True, txFreq=1248.0e6)
        r.set(rxFreq=1310.0e6)
        self.assertEqual(r.state()["txFreq"], 1248.0e6)

    def test_settings_persist_and_status_does_not(self):
        r = self.make()
        r.set(callsign="GM4BRI", rxFreq=1255.0e6)
        r.start()
        r.stop()
        with open(os.path.join(self.tmp, "c.json")) as f:
            saved = json.load(f)
        self.assertEqual(saved["callsign"], "GM4BRI")
        self.assertEqual(saved["rxFreq"], 1255.0e6)
        self.assertNotIn("keyed", saved)
        self.assertNotIn("snr", saved)


class Guard(Base):
    def ready(self, r):
        r.set(authorise=True, callsign="GM4BRI", licenceAck=True, licenceClass="full")

    def test_nothing_keys_without_licence_and_callsign(self):
        r = self.make()
        r.start()
        with self.assertRaisesRegex(TxRefused, "licence"):
            r.ptt(True)
        r.set(licenceAck=True)
        with self.assertRaisesRegex(TxRefused, "callsign"):
            r.ptt(True)
        self.assertFalse(r.state()["keyed"])

    def test_real_hardware_needs_allow_hardware_tx(self):
        r = self.make(device=FakeHardware({}, 10e6))
        self.ready(r)
        r.start()
        with self.assertRaisesRegex(TxRefused, "real hardware"):
            r.ptt(True)

    def test_70cm_transmits_with_the_bandplan_warning_logged(self):
        # Brian's decision: 70cm analogue TX is allowed, outside the bandplan,
        # with a prominent warning; past 430-440 MHz is a warning too (8 Oct:
        # most 70cm work is down a cable into an attenuator).
        r = self.make(device=FakeHardware({}, 10e6, rng=(325e6, 3800e6), duplex=True), allowHardwareTx=True,
                      band="70cm", rxFreq=437e6, txFreq=437e6)
        self.ready(r)
        r.start()
        # 437 MHz: AM-VSB's sound carrier would land above 440 MHz - warned.
        st = r.state()
        self.assertEqual(st["txRefused"], [])
        self.assertTrue(any("OUTSIDE THE ALLOCATION" in w and "between 431.25 and 433.9 MHz" in w
                            for w in st["txWarnings"]))
        r.set(rxFreq=433.0e6)
        st = r.state()
        self.assertEqual(st["txRefused"], [])
        self.assertTrue(st["txWarnings"][0].startswith("OUTSIDE THE BANDPLAN"))
        r.ptt(True)
        self.assertTrue(r.state()["keyed"])
        r.heartbeat()
        r.ptt(False)
        log = r.tx_log()
        self.assertEqual([e["event"] for e in log], ["key", "unkey"])
        self.assertEqual(log[0]["freq"], 433.0e6)
        self.assertTrue(any("OUTSIDE THE BANDPLAN" in w for w in log[0]["warnings"]))

    def test_band_out_of_the_radios_range_is_said_not_silently_failed(self):
        r = self.make(device=FakeHardware({}, 20e6), allowHardwareTx=True)
        self.ready(r)
        r.set(band="6cm")
        r.start()
        st = r.state()
        self.assertFalse(st["bandOk"])
        self.assertIn("ad9364", st["bandWhy"])
        self.assertEqual(st["error"], st["bandWhy"])
        with self.assertRaisesRegex(TxRefused, "ad9364"):
            r.ptt(True)

    def test_a_plan_that_does_not_fit_refuses(self):
        r = self.make(device=FakeHardware({}, 16e6), allowHardwareTx=True, system="fm", fmRate=16_000_000,
                      rxFreq=1255e6, txFreq=1255e6)
        self.ready(r)
        r.start()
        with self.assertRaisesRegex(TxRefused, "does not fit"):
            r.ptt(True)


class Transmit(Base):
    def test_the_transmitted_loop_is_built_for_the_radio_s_own_filters(self):
        # A radio that reports its fixed response (a Pluto: devices/ad936x.py)
        # gets a loop with that response undone; the simulator, flat, gets
        # the plain one. Same picture, same settings: only the shape differs.
        from unittest import mock
        from ohsatv import systems
        from ohsatv.devices import ad936x

        class Pluto(FakeHardware):
            def open(self):
                super().open()
                self.tx_response = ad936x.Response("tx", self.fs, self.fs)

        seen = []
        real = systems.build_loop

        def spy(*a, **kw):
            seen.append(kw.get("tx_shape"))
            return real(*a, **kw)
        for dev in (Pluto({}, 10e6, duplex=True), None):
            r = self.make(device=dev, callsign="GM4BRI", locator="IO86", licenceAck=True, licenceClass="full", allowHardwareTx=True)
            r.start()
            with mock.patch.object(systems, "build_loop", spy):
                r.ptt(True)
                self.assertTrue(wait_for(lambda: len(seen) >= (1 if dev else 2), timeout=5, radio=r))
                r.ptt(False)
            r.stop()
        self.assertIsInstance(seen[0], ad936x.Response)
        self.assertEqual((seen[0].path, seen[0].fs), ("tx", 10e6))
        self.assertIsNone(seen[1])

    def test_our_test_card_comes_back_through_the_whole_chain(self):
        r = self.make(callsign="GM4BRI", locator="IO86", licenceAck=True, licenceClass="full")
        video = r.subscribe("video", 64)
        r.start()
        r.ptt(True)
        self.assertTrue(r.state()["keyed"])
        frames = []
        end = time.monotonic() + 10
        while time.monotonic() < end and len(frames) < 30:
            r.heartbeat()
            frames += video.drain()
            time.sleep(0.05)
        r.ptt(False)
        self.assertFalse(r.state()["keyed"])
        self.assertGreater(len(frames), 10)
        t, rgb, stats = frames[-1]
        self.assertTrue(stats["vLock"])
        self.assertTrue(stats["colour"])
        sent = testcard.test_card("GM4BRI", "IO86", "OPENHAMSHACK ATV")
        got = rgb.astype(np.float32) / 255
        ref = resample_width(sent, got.shape[1])
        self.assertGreater(psnr(crop(luma(got)), crop(luma(ref))), 24.0)
        # ...and it is ours, not the simulated station's: the callsign box differs.
        other = resample_width(testcard.test_card("SIM", "SIM AM-VSB 1275", "SIMULATED STATION"), got.shape[1])
        self.assertGreater(psnr(crop(luma(got)), crop(luma(ref))), psnr(crop(luma(got)), crop(luma(other))) + 3)

    @unittest.skipUnless(__import__("shutil").which("ffmpeg"), "ffmpeg not installed")
    def test_a_live_source_goes_out_frame_by_frame(self):
        import subprocess
        d = tmp_dir(self, "ohsatv-live-")
        clip = os.path.join(d, "clip.mkv")
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "testsrc2=size=768x576:rate=25:duration=3", "-c:v", "ffv1", clip], check=True)
        r = self.make(callsign="GM4BRI", licenceAck=True, licenceClass="full", source="file", videoFile=clip, audioSource="none")
        video = r.subscribe("video", 64)
        r.start()
        self.assertTrue(wait_for(lambda: r.source is not None and r.source.frames > 5, radio=r), r.state()["sourceStatus"])
        r.ptt(True)
        frames = []
        end = time.monotonic() + 8
        while time.monotonic() < end and len(frames) < 40:
            r.heartbeat()
            frames += video.drain()
            time.sleep(0.05)
        r.ptt(False)
        self.assertGreater(len(frames), 20)
        a, b = frames[-12][1].astype(np.float32), frames[-1][1].astype(np.float32)
        self.assertTrue(frames[-1][2]["vLock"])
        self.assertGreater(np.abs(a - b).mean(), 2.0)            # the picture moves
        # The ident box is burnt in, bottom-left: bright text on a dark box.
        box = b[576 - 70:576 - 28, : b.shape[1] // 5]
        self.assertGreater(box.max(), 200)
        self.assertLess(r.state()["txRepeats"], 10)

    def test_watchdog_unkeys_when_the_window_stops(self):
        r = self.make(callsign="GM4BRI", licenceAck=True, licenceClass="full")
        r.set(authorise=True, watchdogSec=0.5)
        feed = r.subscribe("video", 4)
        r.start()
        r.ptt(True)
        r.heartbeat()
        feed.drain()
        time.sleep(1.5)                        # no heartbeat, no drain
        self.assertFalse(r.state()["keyed"])

    def test_timeout_unkeys(self):
        r = self.make(callsign="GM4BRI", licenceAck=True, licenceClass="full")
        r.start()
        # The shortest settable time-out is 30 s; shorten it inside the
        # engine for the test rather than wait.
        r.st["txTimeoutMin"] = 0.01
        r.ptt(True)
        ok = wait_for(lambda: not r.state()["keyed"], timeout=5, radio=r)
        self.assertTrue(ok)

    def test_half_duplex_pauses_receive_and_keeps_the_preview(self):
        r = self.make(device=FakeHardware({}, 10e6, duplex=False), allowHardwareTx=True, callsign="GM4BRI",
                      licenceAck=True, licenceClass="full")
        video = r.subscribe("video", 64)
        prev = r.subscribe("txvideo", 64)
        r.start()
        wait_for(lambda: video.drain(), radio=r)
        r.ptt(True)
        time.sleep(0.4)
        video.drain()
        prev.drain()
        t0 = time.monotonic()
        while time.monotonic() - t0 < 1.0:
            r.heartbeat()
            time.sleep(0.05)
        self.assertTrue(r.rx_paused)
        self.assertEqual(len(video.drain()), 0)
        self.assertGreater(len(prev.drain()), 3)
        r.ptt(False)

    def test_half_duplex_publishes_the_transmitted_spectrum_on_its_own_feed(self):
        # Split, so the transmitted carrier (1255) is not where receive was
        # tuned (1249): the spectrum must be the one we send.
        r = self.make(device=FakeHardware({}, 10e6, duplex=False), allowHardwareTx=True, callsign="GM4BRI",
                      licenceAck=True, licenceClass="full", split=True, txFreq=1255.0e6)
        rx = r.subscribe("spectrum", 64)
        tx = r.subscribe("txspectrum", 64)
        r.start()
        self.assertTrue(wait_for(lambda: rx.drain(), radio=r))
        self.assertEqual(tx.drain(), [])                 # nothing sent, nothing on it
        r.ptt(True)
        time.sleep(0.3)
        rx.drain()
        tx.drain()
        t0 = time.monotonic()
        while time.monotonic() - t0 < 1.0:
            r.heartbeat()
            time.sleep(0.05)
        lines = tx.drain()
        self.assertEqual(rx.drain(), [])                 # receive is paused, and says nothing
        self.assertGreaterEqual(len(lines), 8)           # 12.5 a second, as receive's
        _t, start, bin_hz, db = lines[-1]
        self.assertAlmostEqual(start + bin_hz * len(db) / 2, 1255.0e6 - r.plan()["carrier"], delta=bin_hz)
        # AM-VSB: the vision carrier is the strongest thing in it.
        self.assertAlmostEqual(start + bin_hz * (int(np.argmax(db)) + 0.5), 1255.0e6, delta=2 * bin_hz)
        r.ptt(False)
        self.assertTrue(wait_for(lambda: rx.drain(), radio=r))
        tx.drain()
        time.sleep(0.3)
        self.assertEqual(tx.drain(), [])

    def test_full_duplex_sends_no_transmitted_spectrum(self):
        # Receive carries on: the received spectrum already shows our signal.
        r = self.make(device=FakeHardware({}, 10e6, duplex=True), allowHardwareTx=True, callsign="GM4BRI",
                      licenceAck=True, licenceClass="full")
        tx = r.subscribe("txspectrum", 64)
        r.start()
        r.ptt(True)
        t0 = time.monotonic()
        while time.monotonic() - t0 < 0.8:
            r.heartbeat()
            time.sleep(0.05)
        self.assertEqual(tx.drain(), [])
        r.ptt(False)

    def test_changing_to_a_refused_frequency_while_keyed_unkeys(self):
        r = self.make(device=FakeHardware({}, 10e6, duplex=True), allowHardwareTx=True, callsign="GM4BRI",
                      licenceAck=True, licenceClass="full")
        r.start()
        r.ptt(True)
        self.assertTrue(r.state()["keyed"])
        r.set(rxFreq=1310.0e6)                 # repeater outputs: receive only
        ok = wait_for(lambda: not r.state()["keyed"], timeout=3, radio=r)
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()


NARROW = dict(system="fm", fmRate=5_000_000, fmDeviation=1.2e6, fmVideoBw=1.6e6, fmSub=0.0, rxFreq=1249.0e6,
              txFreq=1249.0e6, callsign="GM4BRI", locator="IO86", licenceAck=True, licenceClass="full")


class NarrowFormats(Base):
    def loopback(self, r, video, seconds=10, want=20):
        frames = []
        end = time.monotonic() + seconds
        while time.monotonic() < end and len(frames) < want:
            r.heartbeat()
            frames += video.drain()
            time.sleep(0.05)
        return frames

    def test_mini_mac_goes_out_and_comes_back_in_colour_with_its_sound(self):
        r = self.make(videoFormat="mac", **NARROW)
        video = r.subscribe("video", 64)
        r.start()
        pl = r.plan()
        self.assertTrue(pl["fits"] and pl["colour"])
        self.assertIn("not compatible with standard ATV receivers", " ".join(pl["why"]))
        r.ptt(True)
        frames = self.loopback(r, video)
        st = r.state()
        r.ptt(False)
        self.assertEqual(st["rxFormat"], "mac")
        self.assertGreater(len(frames), 10)
        _, rgb, stats = frames[-1]
        self.assertTrue(stats["vLock"] and stats["colour"])
        got = rgb.astype(np.float32) / 255
        ref = resample_width(testcard.test_card("GM4BRI", "IO86", "OPENHAMSHACK ATV"), got.shape[1])
        self.assertGreater(psnr(crop(luma(got)), crop(luma(ref))), 18.0)
        # The tone came back in the picture's data: -9 dBFS sent.
        self.assertGreater(st["audioDb"], -15.0)
        self.assertEqual([e.get("format") for e in r.tx_log() if e["event"] == "key"], ["mac"])

    def test_every_over_locks_again(self):
        # Key, unkey, key: each over starts with a fold half noise, half
        # signal. Seen before this test: the decode thread looping on one
        # acquisition (Mini-MAC), and then a search that fell further behind
        # the signal every frame - the third over never decoded.
        for fmt in ("mac", "pal"):
            with self.subTest(fmt=fmt):
                r = self.make(videoFormat=fmt, **NARROW)
                video = r.subscribe("video", 64)
                r.start()
                shown = 0
                for _ in range(3):
                    r.ptt(True)
                    video.drain()
                    got = self.loopback(r, video, seconds=4, want=5)
                    r.ptt(False)
                    self.assertGreaterEqual(len(got), 5)
                    shown += len(got)
                    time.sleep(0.6)
                # Kept up: a search falling further behind every frame skips
                # every picture from then on. Not "none skipped": skipping a
                # picture to catch up is what the receiver is meant to do when
                # the host is slow, and with the CPUs oversubscribed (the
                # nightly, beside an ISO build) the start of an over skipped
                # 1-4 of its first pictures; idle, none.
                self.assertLess(r.state()["decodeSkipped"], shown)
                r.stop()

    def test_the_receiver_finds_the_format_on_the_air_by_itself(self):
        r = self.make(videoFormat="mac", **NARROW)
        video = r.subscribe("video", 64)
        toasts = r.subscribe("toast", 64)
        r.start()
        r.ptt(True)                           # the loop goes out as Mini-MAC...
        self.assertTrue(self.loopback(r, video, want=3))
        r.set(videoFormat="pal")              # ...and the receiver is told to expect PAL
        self.assertEqual(r.state()["rxFormat"], "pal")
        ok = wait_for(lambda: r.state()["rxFormat"] == "mac" and r.state()["rxLock"] == "locked", timeout=8,
                      radio=r)
        r.ptt(False)
        self.assertTrue(ok, r.state()["rxFormat"])
        self.assertTrue(any("Mini-MAC" in t["text"] for t in toasts.drain()))

    def station(self, fmt, seconds, then=None, **over):
        """Receive another station's `fmt` (None: the simulator's own
        standard PAL) with ours set to PAL; return the rxFormat and rxLock
        seen every 0.1 s, and the toasts. `then`: settings to make once the
        receiver has locked, the watch starting from there."""
        cfg = dict(NARROW, rxFreq=1255.0e6, txFreq=1255.0e6)
        cfg.update(over)
        fs = float(cfg["fmRate"])
        dev = (SimDevice if fmt is None else narrow_station(fmt))(dict(FAST, **cfg), fs)
        r = self.make(device=dev, **cfg)
        toasts = r.subscribe("toast", 64)
        r.start()
        if then:
            self.assertTrue(wait_for(lambda: r.state()["rxLock"] == "locked", timeout=8, radio=r))
            r.set(**then)
        seen = []
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            r.heartbeat()
            st = r.state()
            seen.append((st["rxFormat"], st["rxLock"]))
            time.sleep(0.1)
        r.stop()
        return seen, [t["text"] for t in toasts.drain()]

    def test_pal_locked_to_narrow_colour_pal_looks_again_and_switches(self):
        # PAL's decoder locks to Low-PAL's syncs and calls it mono; formatAuto
        # used to look only while nothing was decoded, so it never switched.
        # Auto-detect is turned on only once PAL has locked, so the look
        # that finds it has to be the one made while locked.
        seen, toasts = self.station("lowpal", 8.0, formatAuto=False, then=dict(formatAuto=True))
        self.assertEqual(seen[0], ("pal", "locked"))
        self.assertIn("lowpal", [f for f, _ in seen])
        self.assertEqual(seen[-1], ("lowpal", "locked"))
        self.assertTrue(any("Narrow colour PAL" in t for t in toasts), toasts)

    def test_a_genuine_pal_picture_is_never_second_guessed(self):
        # The other way: a standard PAL signal at the USB rate and at the
        # LibreSDR's 16 MS/s - where detect() used to read PAL's line
        # harmonics as Low-PAL's subcarrier. Decoded as mono, so the colour
        # guard does not apply and every look is made (as for a mono
        # transmission). Locked throughout, it never leaves PAL.
        for over in (dict(colour="mono"),
                     dict(colour="mono", fmRate=16_000_000, fmDeviation=4e6, fmVideoBw=5e6, fmSub=6e6)):
            with self.subTest(**over):
                seen, toasts = self.station(None, 7.0, **over)
                self.assertEqual({f for f, _ in seen}, {"pal"})
                self.assertGreater(sum(lk == "locked" for _, lk in seen), 0.7 * len(seen))
                self.assertFalse(any("Receiving" in t for t in toasts), toasts)

    def test_mini_mac_sound_stays_whole_when_the_picture_cannot_keep_up(self):
        # On air at 16 MS/s the window decoded 12-16 pictures a second and
        # Mini-MAC's tone measured ~17 dB SINAD: the receiver dropped whole
        # blocks of signal to catch up, and the sound in them. The same
        # recording decoded offline gives 37-39 dB. Here every picture takes
        # 60 ms more than a frame's 40 - a computer that cannot keep up - and
        # the sound must still come through whole.
        from unittest import mock
        from test_chain import tone_quality
        from ohsatv import mac

        class Grab(NullAudio):
            def __init__(self):
                super().__init__(realtime=False)
                self.chunks = []

            def play(self, a):
                self.chunks.append(np.asarray(a, np.float32).copy())

        slow = mac.Decoder._picture

        def picture(dec, *a):
            time.sleep(0.06)
            return slow(dec, *a)

        cfg = dict(FAST, **dict(NARROW, rxFreq=1255.0e6, txFreq=1255.0e6, videoFormat="mac"))
        self.tmp = str(tmp_dir(self))
        au = Grab()
        r = Radio(cfg, conf_path=__import__("pathlib").Path(self.tmp) / "c.json", audio=au,
                  device=narrow_station("mac")(cfg, 5e6), data_dir=__import__("pathlib").Path(self.tmp) / "d")
        self.addCleanup(r.stop)
        video = r.subscribe("video", 256)
        with mock.patch.object(mac.Decoder, "_picture", picture):
            r.start()
            self.assertTrue(wait_for(lambda: r.state()["rxLock"] == "locked", timeout=8, radio=r))
            # The simulator builds its station's loop on the first read and
            # then catches up on the second it lost in one burst - a start-up
            # overrun no radio has. Measure from after it.
            wait_for(lambda: False, timeout=1.5, radio=r)
            au.chunks.clear()
            video.drain()
            t0 = time.monotonic()
            wait_for(lambda: False, timeout=4.0, radio=r)
            elapsed = time.monotonic() - t0
            st = r.state()
        r.stop()
        sound = np.concatenate(au.chunks)
        # All of it, in order: as many seconds of sound as of signal...
        self.assertGreater(len(sound) / 48000, 0.9 * elapsed)
        # ...with no gaps, which a fit of one steady tone would see as noise
        # in the second it fell in. (Second by second: tone_quality's 0.05 Hz
        # grid is too coarse for one fit over several seconds.)
        secs = [sound[i:i + 48000] for i in range(0, len(sound) - 47999, 48000)]
        worst = min(tone_quality(np.concatenate([s[:16000], s]))[1] for s in secs)
        self.assertGreater(worst, 30.0)
        # The pictures are what gave way, and some still came.
        self.assertGreater(st["decodeSkipped"], 10)
        self.assertGreater(len(video.drain()), 5)

    def test_the_plan_describes_the_format_being_received(self):
        # Another OpenHamShack sending Mini-MAC on 1255 MHz while ours is set to
        # PAL: once formatAuto has found it, rxPlan is Mini-MAC's (colour,
        # digital sound) while plan - what PTT would send - stays PAL's.
        cfg = dict(FAST, **dict(NARROW, rxFreq=1255.0e6, txFreq=1255.0e6))
        r = self.make(device=narrow_station("mac")(cfg, 5e6), **dict(NARROW, rxFreq=1255.0e6, txFreq=1255.0e6))
        r.start()
        self.assertEqual(r.state()["rxPlan"].get("format", "pal"), "pal")
        ok = wait_for(lambda: r.state()["rxFormat"] == "mac", timeout=8, radio=r)
        self.assertTrue(ok, r.state()["rxFormat"])
        st = r.state()
        self.assertEqual(st["rxPlan"]["format"], "mac")
        self.assertTrue(st["rxPlan"]["colour"])
        self.assertEqual(st["rxPlan"]["sound"], "digital")
        self.assertNotIn("format", st["plan"])
        self.assertFalse(st["plan"]["colour"])
        r.stop()
        # Stopped, nothing is being received: the plan is ours again.
        self.assertNotIn("format", r.state()["rxPlan"])

    def test_off_means_the_receiver_keeps_what_it_was_told(self):
        r = self.make(videoFormat="mac", formatAuto=False, **NARROW)
        video = r.subscribe("video", 64)
        r.start()
        r.ptt(True)
        self.assertTrue(self.loopback(r, video, want=3))
        r.set(videoFormat="pal")
        time.sleep(2.5)
        r.heartbeat()
        st = r.state()
        r.ptt(False)
        self.assertEqual(st["rxFormat"], "pal")
        self.assertNotEqual(st["rxLock"], "locked")

    def test_a_usb_pluto_is_offered_them_never_given_them(self):
        from ohsatv import profiles
        settings, sentence = profiles.best("pluto", "usb", 6.8, "")
        self.assertNotIn("videoFormat", settings)
        self.assertIn("Mini-MAC", sentence)
        self.assertIn("not compatible with standard ATV receivers", sentence)
        # A wide radio goes back to standard PAL: it has room for colour and sound.
        self.assertEqual(profiles.best("pluto", "ethernet", 16.2, "")[0]["videoFormat"], "pal")

    def test_am_has_no_narrow_formats(self):
        r = self.make(videoFormat="mac", system="am", amRate=5_000_000)
        self.assertNotIn("format", r.plan())
        self.assertFalse(r.plan()["colour"])


A405 = dict(system="am", amRate=5_000_000, videoFormat="405", rxFreq=1249.0e6, txFreq=1249.0e6, callsign="GM4BRI",
            licenceAck=True, licenceClass="full")


class SystemA(Base):
    """405 lines through the engine: sent, looped back by the simulator,
    decoded; and found on the air by formatAuto (the simulator has a
    405-line station on 1287.5 MHz and a 625-line one on 1275)."""

    def test_405_lines_go_out_and_come_back_with_their_sound(self):
        # With sound from 6.25 MS/s; at a USB Pluto's 5 MS/s without it, and
        # the plan says why (sysa.SOUND_MIN_FS).
        for rate in (6_250_000, 5_000_000):
            with self.subTest(rate=rate):
                r = self.make(**dict(A405, amRate=rate))
                video = r.subscribe("video", 64)
                r.start()
                pl = r.plan()
                self.assertTrue(pl["fits"])
                self.assertEqual((pl["format"], pl["video_bw"], pl["colour"]), ("405", 3e6, False))
                self.assertNotIn("not compatible", " ".join(pl["why"]))
                self.assertEqual("no sound at 5 MS/s" in " ".join(pl["why"]), rate < 6e6)
                r.ptt(True)
                frames = []
                self.assertTrue(wait_for(lambda: frames.extend(video.drain()) or len(frames) >= 10, timeout=10,
                                         radio=r))
                st = r.state()
                r.ptt(False)
                self.assertEqual(st["rxFormat"], "405")
                _, rgb, stats = frames[-1]
                self.assertEqual(rgb.shape[0], 378)
                self.assertTrue(stats["vLock"])
                got = rgb.astype(np.float32) / 255
                from ohsatv import sysa
                ref = resample_width(sysa.rows_to(testcard.test_card("GM4BRI", "IO86", "OPENHAMSHACK ATV", lines405=True),
                                                  378), got.shape[1])
                self.assertGreater(psnr(crop(luma(got)), crop(luma(ref))), 18.0)
                if rate < 6e6:
                    self.assertLess(st["audioDb"], -60.0)            # no sound carrier, no sound
                else:
                    self.assertGreater(st["audioDb"], -15.0)         # the tone, -9 dBFS sent
                self.assertEqual([e.get("format") for e in r.tx_log() if e["event"] == "key"], ["405"])
                r.stop()

    def test_choosing_405_lines_moves_to_their_own_rate_and_back(self):
        r = self.make(**dict(A405, videoFormat="pal", amRate=10_000_000, a405Rate=6_250_000, autoProfile=False))
        r.start()
        self.assertEqual(r.dev.fs, 10e6)
        r.set(videoFormat="405")
        self.assertTrue(wait_for(lambda: r.running and r.dev is not None and r.dev.fs == 6.25e6, timeout=8,
                                 radio=r))
        self.assertIsNotNone(r.plan()["sound"])
        r.set(videoFormat="pal")
        self.assertTrue(wait_for(lambda: r.running and r.dev is not None and r.dev.fs == 10e6, timeout=8, radio=r))
        # 0: 405 lines at the AM-VSB rate, no reopen.
        before = r.dev
        r.set(a405Rate=0)                                    # a rate setting: reopens, as amRate does
        self.assertTrue(wait_for(lambda: r.running and r.dev is not None and r.dev is not before, timeout=8,
                                 radio=r))
        dev = r.dev
        r.set(videoFormat="405")
        time.sleep(0.5)
        self.assertIs(r.dev, dev)
        self.assertEqual(r.state()["fs"], 10e6)

    def test_the_scope_shows_a_405_line(self):
        r = self.make(scopeLine=500, **A405)                 # past line 405: the last line is shown
        scope = r.subscribe("scope", 8)
        r.start()
        r.ptt(True)
        item = wait_for(lambda: scope.get(), timeout=8, radio=r)
        r.ptt(False)
        self.assertEqual(item["line"], 405)
        self.assertEqual(item["fs"], 10e6)                   # the picture comes out at twice 5 MS/s
        self.assertEqual(len(item["wave"]), 988)             # 98.77 us at 10 MS/s, rounded up

    def watch(self, seconds=8.0, **cfg):
        r = self.make(**cfg)
        toasts = r.subscribe("toast", 64)
        r.start()
        ok = wait_for(lambda: r.state()["rxLock"] == "locked", timeout=seconds, radio=r)
        st = r.state()
        r.stop()
        return ok, st, [t["text"] for t in toasts.drain()]

    def test_formatauto_finds_405_lines_on_am(self):
        for rate in (5_000_000, 10_000_000):
            with self.subTest(rate=rate):
                ok, st, toasts = self.watch(**dict(A405, videoFormat="pal", amRate=rate, rxFreq=1287.5e6,
                                                   txFreq=1287.5e6))
                self.assertTrue(ok)
                self.assertEqual(st["rxFormat"], "405")
                self.assertEqual(st["rxPlan"]["format"], "405")
                self.assertEqual(st["plan"].get("format", "pal"), "pal")     # what we send is unchanged
                self.assertTrue(any("405 lines" in t for t in toasts), toasts)
                self.assertFalse(any("not compatible" in t for t in toasts))

    def test_formatauto_finds_625_lines_when_expecting_405(self):
        ok, st, toasts = self.watch(**dict(A405, amRate=10_000_000, rxFreq=1275.0e6, txFreq=1275.0e6))
        self.assertTrue(ok)
        self.assertEqual(st["rxFormat"], "pal")
        self.assertEqual(st["videoFormat"], "405")

    def test_a_625_line_station_is_never_taken_for_405(self):
        # Locked to the simulator's 625-line AM station with formatAuto on:
        # it stays PAL for as long as it is watched.
        r = self.make(**dict(A405, videoFormat="pal", amRate=10_000_000, rxFreq=1275.0e6, txFreq=1275.0e6))
        r.start()
        self.assertTrue(wait_for(lambda: r.state()["rxLock"] == "locked", timeout=8, radio=r))
        end = time.monotonic() + 4
        while time.monotonic() < end:
            r.heartbeat()
            self.assertEqual(r.state()["rxFormat"], "pal")
            time.sleep(0.1)


TEZUKA = (46.875001e6, 6000e6)


class CableFeed(Base):
    """Settings › Transmit › Cable feed (DESIGN.md "Cable feed"): 405 lines
    into a vintage set's aerial socket by cable, on Band I, which is not an
    amateur allocation."""

    def radio(self, rng=TEZUKA, **over):
        cfg = dict(A405, allowHardwareTx=True, txGain=-20.0)
        cfg.update(over)
        r = self.make(device=FakeHardware({}, 5e6, rng=rng, duplex=True), **cfg)
        r.start()
        return r

    def on(self, r, **more):
        r.set(authorise=True, cableFeedAck=True, cableFeed=True, **more)

    def test_off_by_default_and_the_confirmation_comes_first(self):
        r = self.radio()
        self.assertFalse(r.state()["cableFeed"])
        with self.assertRaisesRegex(ValueError, "never to an antenna"):
            r.set(authorise=True, cableFeed=True)
        with self.assertRaisesRegex(ValueError, "Settings"):
            r.set(cableFeedAck=True, cableFeed=True)          # auth keys: the Settings dialog only
        with self.assertRaisesRegex(ValueError, "cable feed only"):
            r.set(band="Band I")
        self.on(r)
        st = r.state()
        self.assertEqual((st["band"], st["system"], st["videoFormat"], st["txFreq"]), ("Band I", "am", "405", 45.0e6))

    def test_never_comes_back_on_after_a_restart(self):
        r = self.radio()
        self.on(r, rxFreq=51.75e6)
        r.stop()
        again = Radio(None, conf_path=__import__("pathlib").Path(self.tmp) / "c.json", audio=NullAudio(realtime=False),
                      data_dir=__import__("pathlib").Path(self.tmp) / "data")
        self.addCleanup(again.stop)
        st = again.state()
        self.assertFalse(st["cableFeed"])
        self.assertFalse(st["cableFeedAck"])
        self.assertEqual(st["band"], "23cm")
        saved = json.loads((__import__("pathlib").Path(self.tmp) / "c.json").read_text())
        self.assertNotIn("cableFeed", saved)
        self.assertNotIn("cableFeedAck", saved)

    def test_b2_keys_at_the_least_output_and_is_logged(self):
        r = self.radio()
        self.on(r, rxFreq=51.75e6)
        self.assertEqual(r.state()["txRefused"], [])
        r.ptt(True)
        self.assertTrue(r.state()["keyed"])
        self.assertTrue(wait_for(lambda: getattr(r.txdev, "tx_gain", None) is not None, radio=r))
        self.assertEqual(r.txdev.tx_gain, -89.75)            # the control said -20 dB
        r.ptt(False)
        key = [e for e in r.tx_log() if e["event"] == "key"][-1]
        self.assertEqual((key["cableFeed"], key["gainDb"], key["freq"], key["format"], key["band"]),
                         (True, -89.75, 51.75e6, "405", "Band I"))

    def test_b1_is_refused_on_a_tezuka_pluto_with_the_channels_it_can_send(self):
        r = self.radio()
        self.on(r)                                            # B1, 45.00 MHz
        with self.assertRaisesRegex(TxRefused, "B2, B3, B4, B5"):
            r.ptt(True)

    def test_a_stock_pluto_sends_no_channel(self):
        r = self.radio(rng=(70e6, 6000e6))
        self.on(r)
        for f in (45.0e6, 51.75e6, 56.75e6, 61.75e6, 66.75e6):
            r.set(rxFreq=f)
            with self.assertRaisesRegex(TxRefused, "none of (the Band I channels )?B1-B5"):
                r.ptt(True)

    def test_nothing_but_405_on_b1_to_b5(self):
        r = self.radio(rng=(1e6, 6000e6))
        self.on(r, rxFreq=56.75e6)
        self.assertEqual(r.state()["txRefused"], [])
        for change, why in ((dict(videoFormat="pal"), "405-line System A only"),
                            (dict(rxFreq=50.0e6), "not one"),
                            (dict(band="23cm", system="am"), "not one"),
                            (dict(band="70cm", rxFreq=433.0e6), "not one")):
            with self.subTest(**change):
                r.set(**change)
                with self.assertRaisesRegex(TxRefused, why):
                    r.ptt(True)
                r.set(band="Band I", rxFreq=56.75e6, system="am", videoFormat="405")
        # FM on the cable feed: never.
        r.set(band="23cm", system="fm")
        self.assertTrue(wait_for(lambda: r.running and r.state()["system"] == "fm", radio=r))
        with self.assertRaisesRegex(TxRefused, "405-line System A only"):
            r.ptt(True)

    def test_turning_it_on_from_625_lines_retunes_the_receiver_for_405(self):
        # One call changes band, frequency and format (PAL on 23cm -> 405 on
        # B1). The receiver's LO must follow the new format's carrier place;
        # it used to be set before the receive chain was rebuilt, so it sat
        # where 625 lines want it and our own looped-back 405 never locked.
        r = self.make(**dict(A405, videoFormat="pal", amRate=10_000_000))
        r.start()
        self.on(r)
        self.assertEqual(r.state()["videoFormat"], "405")
        r.ptt(True)
        self.assertTrue(wait_for(lambda: r.state()["rxFormat"] == "405" and r.state()["vLock"], timeout=10, radio=r))
        self.assertAlmostEqual(r.dev.rx_lo, 45.0e6 - r.plan()["carrier"])
        r.ptt(False)

    def test_uhf_is_the_cable_feeds_alone_and_625_lines_on_it(self):
        r = self.radio(rng=(325e6, 3800e6))                  # a stock Pluto: UHF yes, Band I no
        with self.assertRaisesRegex(ValueError, "UHF is for the cable feed only"):
            r.set(band="UHF")
        self.on(r)
        r.set(band="UHF")
        st = r.state()
        self.assertEqual((st["band"], st["system"], st["videoFormat"], st["txFreq"]), ("UHF", "am", "pal", 591.25e6))
        self.assertTrue(st["bandOk"])
        self.assertTrue(wait_for(lambda: r.running and r.state()["txRefused"] == [], radio=r))
        r.ptt(True)
        self.assertTrue(wait_for(lambda: getattr(r.txdev, "tx_gain", None) is not None, radio=r))
        self.assertEqual(r.txdev.tx_gain, -89.75)            # the control said -20 dB
        r.ptt(False)
        key = [e for e in r.tx_log() if e["event"] == "key"][-1]
        self.assertEqual((key["cableFeed"], key["gainDb"], key["freq"], key["format"], key["band"]),
                         (True, -89.75, 591.25e6, "pal", "UHF"))
        # Back to Band I: 405 lines again (and refused on this radio, which
        # cannot tune it); back to UHF: PAL.
        r.set(band="Band I")
        self.assertEqual(r.state()["videoFormat"], "405")
        with self.assertRaisesRegex(TxRefused, "none of (the Band I channels )?B1-B5"):
            r.ptt(True)
        r.set(band="UHF", rxFreq=471.25e6)
        self.assertEqual(r.state()["videoFormat"], "pal")

    def test_nothing_but_625_pal_on_uhf_channels(self):
        r = self.radio(rng=(1e6, 6000e6), amRate=10_000_000)
        self.on(r, band="UHF")
        self.assertTrue(wait_for(lambda: r.running and r.state()["txRefused"] == [], radio=r))
        for change, why in ((dict(videoFormat="405"), "625-line PAL \\(System I\\) only on UHF"),
                            (dict(rxFreq=607.25e6), "except E38.*not one"),           # radio astronomy
                            (dict(rxFreq=592.0e6), "not one"),                         # between channels
                            (dict(band="23cm", system="am"), "not one"),               # the air: never
                            (dict(band="70cm", rxFreq=433.0e6), "not one")):
            with self.subTest(**change):
                r.set(**change)
                with self.assertRaisesRegex(TxRefused, why):
                    r.ptt(True)
                r.set(band="UHF", rxFreq=591.25e6, system="am", videoFormat="pal")
        # The channel's 8 MHz holds System I with 6.5 MHz sound, not a wider signal.
        r.set(authorise=True, amSoundOffset=6_500_000.0)
        self.assertEqual(r.state()["txRefused"], [])

    def test_uhf_never_comes_back_after_a_restart(self):
        r = self.radio(rng=(325e6, 3800e6))
        self.on(r, band="UHF", rxFreq=687.25e6)
        r.stop()
        again = Radio(None, conf_path=__import__("pathlib").Path(self.tmp) / "c.json", audio=NullAudio(realtime=False),
                      data_dir=__import__("pathlib").Path(self.tmp) / "data")
        self.addCleanup(again.stop)
        st = again.state()
        self.assertFalse(st["cableFeed"])
        self.assertEqual((st["band"], st["rxFreq"]), ("23cm", 1255.0e6))

    def test_switching_it_off_on_uhf_leaves_uhf(self):
        r = self.radio(rng=(325e6, 3800e6))
        self.on(r, band="UHF")
        r.ptt(True)
        r.set(authorise=True, cableFeed=False)
        st = r.state()
        self.assertFalse(st["keyed"])
        self.assertEqual(st["band"], "23cm")

    def test_switching_it_off_while_keyed_unkeys_and_leaves_band_i(self):
        r = self.radio()
        self.on(r, rxFreq=61.75e6)
        r.ptt(True)
        self.assertTrue(r.state()["keyed"])
        r.set(authorise=True, cableFeedAck=False)             # unticking the confirmation turns it off
        st = r.state()
        self.assertFalse(st["keyed"])
        self.assertFalse(st["cableFeed"])
        self.assertEqual(st["band"], "23cm")
        self.assertIn("cable feed", r.tx_log()[-1]["why"])

    def test_the_amateur_guard_is_unchanged_with_it_off(self):
        # Off, the same radio keys 405 lines on 23cm as any AM-VSB picture,
        # at the power control's gain, and the log says cableFeed false.
        r = self.radio(rng=(1e6, 6000e6))
        self.assertEqual(r.state()["txRefused"], [])
        r.ptt(True)
        self.assertTrue(wait_for(lambda: getattr(r.txdev, "tx_gain", None) is not None, radio=r))
        self.assertEqual(r.txdev.tx_gain, -20.0)
        r.ptt(False)
        self.assertIs(r.tx_log()[0]["cableFeed"], False)

    def test_the_hackrf_amplifier_stays_off(self):
        class AmpHardware(FakeHardware):
            amp = True
        r = self.make(device=AmpHardware({}, 5e6, rng=(1e6, 6000e6), duplex=True),
                      **dict(A405, allowHardwareTx=True, hackrfAmp=True))
        r.start()
        self.on(r)
        r.ptt(True)
        self.assertTrue(wait_for(lambda: getattr(r.txdev, "tx_gain", None) is not None, radio=r))
        self.assertFalse(r.txdev.amp)
        r.ptt(False)


class SplitHackRF(Base):
    """Receive on the simulator, transmit on a HackRF (fake libhackrf)."""

    def hackrf(self, **lib):
        from ohsatv.devices import hackrf
        from test_devices_hackrf import FakeLib
        self.lib = FakeLib(**lib)
        return hackrf.HackRFDevice(10e6, {}, lib=self.lib)

    def test_a_missing_hackrf_refuses_transmit_but_receive_runs(self):
        r = self.make(txDevice="hackrf", allowHardwareTx=True, callsign="GM4BRI", licenceAck=True, licenceClass="full",
                      rxFreq=1275.0e6, txFreq=1275.0e6)
        r._tx_device_override = self.hackrf(present=False)
        video = r.subscribe("video", 8)
        r.start()
        self.assertTrue(wait_for(lambda: video.drain(), radio=r))
        self.assertTrue(any("No HackRF" in x for x in r.state()["txRefused"]))
        with self.assertRaisesRegex(TxRefused, "No HackRF"):
            r.ptt(True)

    def test_hackrf_needs_the_hardware_switch(self):
        r = self.make(txDevice="hackrf", callsign="GM4BRI", licenceAck=True, licenceClass="full")
        r._tx_device_override = self.hackrf()
        r.start()
        with self.assertRaisesRegex(TxRefused, "real hardware"):
            r.ptt(True)

    def test_keyed_hackrf_gets_samples_and_receive_carries_on(self):
        r = self.make(txDevice="hackrf", allowHardwareTx=True, callsign="GM4BRI", licenceAck=True, licenceClass="full",
                      rxFreq=1275.0e6, txFreq=1275.0e6)
        r._tx_device_override = self.hackrf()
        video = r.subscribe("video", 64)
        r.start()
        self.assertTrue(r.state()["duplex"])
        self.assertEqual(r.state()["txKind"], "hackrf")
        r.ptt(True)
        self.assertTrue(wait_for(lambda: self.lib.streaming, radio=r))
        # Play libhackrf's USB thread for a while: it pulls what the engine writes.
        got = []
        end = time.monotonic() + 3
        while time.monotonic() < end:
            r.heartbeat()
            b, _ = self.lib.pull(262144)
            got.append(b)
            time.sleep(0.004)
        frames = video.drain()
        self.assertFalse(r.rx_paused)
        self.assertGreater(len(frames), 10)                 # receive kept decoding while keyed
        data = np.concatenate(got[-20:]).astype(np.float32)
        self.assertGreater(np.abs(data).max(), 100)          # a real signal, scaled near full int8
        tuned = [c[1] for c in self.lib.calls if c[0] == "freq"]
        self.assertEqual(tuned[-1], round(1275.0e6 - r.plan()["carrier"]))
        r.ptt(False)
        self.assertEqual(self.lib.vga, 0)

    def test_underruns_reach_the_window(self):
        r = self.make(txDevice="hackrf", allowHardwareTx=True, callsign="GM4BRI", licenceAck=True, licenceClass="full",
                      rxFreq=1275.0e6, txFreq=1275.0e6)
        r._tx_device_override = self.hackrf()
        r.start()
        r.ptt(True)
        wait_for(lambda: self.lib.streaming, radio=r)
        time.sleep(0.5)
        # Pull far faster than the engine writes: the host "falls behind".
        for _ in range(200):
            self.lib.pull(262144)
        ok = wait_for(lambda: r.state()["txUnderruns"] > 0, timeout=4, radio=r)
        r.ptt(False)
        self.assertTrue(ok)


class Steering(Base):
    """Each radio comes up in its best mode (ohsatv/profiles.py)."""

    def usb_pluto(self):
        d = FakeHardware({}, 10e6)
        d.kind, d.link, d.link_msps = "pluto", "usb", 6.8
        return d

    def test_a_usb_pluto_is_steered_to_its_rate_once(self):
        r = self.make(device=self.usb_pluto(), system="fm")
        r.start()
        st = r.state()
        # 5 MS/s: the USB Pluto receives 6.25 but transmits only ~5.2 (measured).
        self.assertEqual(st["fmRate"], 5_000_000)
        self.assertEqual(st["fmDeviation"], 1.2e6)
        self.assertEqual(st["amRate"], 5_000_000)
        self.assertIn("reduced deviation", st["profile"])
        self.assertTrue(r.plan()["fits"])
        # The operator's own change afterwards is kept across a restart.
        r.set(fmDeviation=1.0e6)
        r.stop()
        r.start()
        self.assertEqual(r.state()["fmDeviation"], 1.0e6)

    def test_a_hackrf_transmitter_gets_full_width(self):
        from ohsatv.devices import hackrf
        from test_devices_hackrf import FakeLib
        r = self.make(device=self.usb_pluto(), txDevice="hackrf", system="fm")
        r._tx_device_override = hackrf.HackRFDevice(10e6, {}, lib=FakeLib())
        r.start()
        st = r.state()
        self.assertEqual(st["fmRate"], 20_000_000)
        self.assertEqual(st["fmDeviation"], 7.0e6)
        self.assertEqual((st["amRate"], st["a405Rate"]), (20_000_000, 20_000_000))   # its images: profiles.py
        self.assertIn("dropped samples", st["profile"])

    def test_off_means_off(self):
        r = self.make(device=self.usb_pluto(), autoProfile=False)
        r.start()
        self.assertEqual(r.state()["fmRate"], 20_000_000)
        self.assertEqual(r.state()["profile"], "")

    def test_the_simulator_is_left_alone(self):
        r = self.make()
        r.start()
        self.assertEqual(r.state()["amRate"], 10_000_000)


class SteeringTezuka(Base):
    """A USB nano that receives 8-bit (docs/atv-research.md 13) is set up to
    receive colour, and refuses to transmit at rates only its receive can
    carry."""

    def nano(self):
        d = FakeHardware({}, 10e6)
        d.kind, d.link, d.link_msps, d.rx8_capable, d.tx_link_msps = "pluto", "usb", 15.0, True, 5.2
        return d

    def test_it_is_steered_to_receive_colour_and_stays_so(self):
        r = self.make(device=self.nano(), system="am")
        r.start()
        st = r.state()
        self.assertEqual(st["amRate"], 10_000_000)
        self.assertEqual((st["fmRate"], st["fmDeviation"], st["fmSub"]), (12_500_000, 1.2e6, 5.5e6))
        self.assertIn("8-bit receive", st["profile"])
        am = r.plan()
        self.assertTrue(am["fits"] and am["colour"], am["why"])
        self.assertIsNotNone(am["sound"])
        r.set(system="fm")
        fm = r.plan()
        self.assertTrue(fm["fits"] and fm["colour"], fm["why"])
        self.assertEqual(fm["sub"], 5.5e6)
        # The operator's change survives a restart: not re-steered.
        r.set(fmDeviation=1.0e6)
        r.stop()
        r.start()
        self.assertEqual(r.state()["fmDeviation"], 1.0e6)

    def test_keying_above_its_usb_transmit_rate_is_refused(self):
        r = self.make(device=self.nano(), system="am", allowHardwareTx=True, callsign="GM4BRI", licenceAck=True, licenceClass="full",
                      rxFreq=1255.0e6, txFreq=1255.0e6)
        r.start()
        refuse, _ = r._tx_refusals()
        self.assertTrue(any("transmits only ~5.2 MS/s" in x for x in refuse), refuse)

    def test_at_5_msps_the_rate_is_no_reason_to_refuse(self):
        r = self.make(device=self.nano(), system="am", autoProfile=False, amRate=5_000_000,
                      allowHardwareTx=True, callsign="GM4BRI", licenceAck=True, licenceClass="full")
        r.start()
        refuse, _ = r._tx_refusals()
        self.assertFalse(any("transmits only" in x for x in refuse), refuse)

    def test_early_steering_does_not_undo_it_on_the_next_start(self):
        from ohsatv import profiles
        from ohsatv.devices import pluto
        # Before opening, a usb: address is all the engine knows: it must not
        # steer the nano back to plain-USB 5 MS/s each time it starts.
        r = self.make(plutoUri="usb:")
        r.st["device"] = "pluto"
        r.st["profileKey"] = profiles.key("pluto", "usb-rx8", pluto.USB_RX8_MSPS, "")
        r.st["amRate"] = 10_000_000
        r._steer_early()
        self.assertEqual(r.st["amRate"], 10_000_000)


class SteeringTezukaTx8(Base):
    """A USB nano whose 8-bit transmit passed the loopback check
    (docs/atv-research.md 18) runs one rate it can both receive and send:
    10 MS/s, AM-VSB in colour with sound. No Tezuka bitstream passes it
    today; this is the steering waiting for one that does."""

    def nano(self):
        from ohsatv.devices import pluto
        d = FakeHardware({}, 10e6)
        d.kind, d.link, d.link_msps = "pluto", "usb", pluto.USB_RX8_MSPS
        d.rx8_capable, d.tx8_capable, d.tx_link_msps = True, True, pluto.USB_TX8_MSPS
        return d

    def test_it_is_steered_to_transmit_colour_at_10_msps(self):
        r = self.make(device=self.nano(), system="am", allowHardwareTx=True, callsign="GM4BRI", licenceAck=True, licenceClass="full",
                      rxFreq=1255.0e6, txFreq=1255.0e6)
        r.start()
        st = r.state()
        self.assertEqual((st["amRate"], st["fmRate"]), (10_000_000, 10_000_000))
        self.assertIn("8-bit receive and transmit", st["profile"])
        am = r.plan()
        self.assertTrue(am["fits"] and am["colour"], am["why"])
        self.assertIsNotNone(am["sound"])
        refuse, _ = r._tx_refusals()
        self.assertFalse(any("transmits only" in x for x in refuse), refuse)
        r.set(system="fm")
        fm = r.plan()
        self.assertTrue(fm["fits"], fm["why"])
        self.assertEqual((st["fmDeviation"], fm["sub"]), (3.0e6, None))

    def test_without_8_bit_transmit_it_stays_a_receive_profile(self):
        d = self.nano()
        d.tx8_capable, d.tx_link_msps = False, 5.2
        r = self.make(device=d, system="am")
        r.start()
        self.assertEqual(r.state()["fmRate"], 12_500_000)
        self.assertIn("8-bit receive (", r.state()["profile"])

    def test_early_steering_does_not_undo_it_on_the_next_start(self):
        from ohsatv import profiles
        from ohsatv.devices import pluto
        r = self.make(plutoUri="usb:")
        r.st["device"] = "pluto"
        r.st["profileKey"] = profiles.key("pluto", "usb-tx8", pluto.USB_RX8_MSPS, "")
        r.st["fmRate"] = 10_000_000
        r._steer_early()
        self.assertEqual(r.st["fmRate"], 10_000_000)


class SteeringEthernet(Base):
    def test_a_16_msps_ethernet_radio_gets_16_msps_fm(self):
        d = FakeHardware({}, 10e6)
        d.kind, d.link, d.link_msps = "pluto", "ethernet", 16.2
        r = self.make(device=d, system="fm")
        r.start()
        st = r.state()
        self.assertEqual((st["fmRate"], st["fmDeviation"]), (16_000_000, 4.0e6))
        self.assertTrue(r.plan()["fits"])
        self.assertEqual(r.plan()["sub"], 6.0e6)

    def test_unknown_ethernet_speed_is_said(self):
        d = FakeHardware({}, 10e6)
        d.kind, d.link, d.link_msps = "pluto", "ethernet", None
        r = self.make(device=d, system="fm")
        r.start()
        self.assertIn("speed unknown", r.state()["profile"])


class RadioMoves(Base):
    """The guard: a real PlutoDevice on the fake libiio (tests/
    test_devices_pluto.py), its attributes changed underneath it while the
    engine runs - what the nano did on 5 October 2026 (docs/atv-research.md
    17a). Nothing real is opened or keyed."""

    def pluto(self, **over):
        from unittest import mock
        from ohsatv.devices import pluto
        from test_devices_pluto import FakeIIO, FakeRadio, FakeRxBuf

        class Paced(FakeIIO):
            """Buffers that take as long as the radio would, so the engine's
            threads run at the pace they would on air instead of spinning;
            transmitted blocks are not kept."""

            def Buffer(self, dev, n, cyclic):
                buf = super().Buffer(dev, n, cyclic)
                fs = float(dev.radio.rate or 10e6)
                if isinstance(buf, FakeRxBuf):
                    refill = buf.refill

                    def paced():
                        time.sleep(n / fs)
                        refill()
                    buf.refill = paced
                else:
                    buf.write = lambda b: None
                    buf.push = lambda: time.sleep(n / fs)
                return buf

        uri = "ip:198.51.100.50"
        self.fake = FakeRadio("ad9361")
        for p in (mock.patch.dict(sys.modules, {"iio": Paced(radios={uri: self.fake})}),
                  mock.patch.object(pluto, "_usb_network_addresses", return_value=[])):
            p.start()
            self.addCleanup(p.stop)
        dev = pluto.PlutoDevice(uri, 10e6, {})
        cfg = dict(allowHardwareTx=True, callsign="GM4BRI", licenceAck=True, licenceClass="full", autoProfile=False)
        cfg.update(over)
        r = self.make(device=dev, **cfg)
        r.start()
        return r, dev, self.fake

    def key(self, r, dev):
        r.ptt(True)
        self.assertTrue(wait_for(lambda: dev.tx_on, timeout=8, radio=r))

    def test_a_moved_rate_and_lo_are_set_back_and_shown_while_receiving(self):
        r, dev, fake = self.pluto()
        toasts = r.subscribe("toast", 16)
        lo = dev.rx_lo
        fake.move("rx", "sampling_frequency", 2_330_997)
        fake.move("rx_lo", "frequency", 1_254_666_998)
        moved = wait_for(lambda: r.state()["radioMoved"], timeout=5, radio=r)
        self.assertEqual([(m["what"], m["to"], m["held"]) for m in moved],
                         [("sampleRate", 2_330_997, True), ("rxLo", 1_254_666_998, True)])
        self.assertEqual(moved[1]["from"], lo)
        self.assertEqual(fake.rx_ch.attrs["sampling_frequency"].value, "10000000")
        self.assertEqual(float(fake.rx_lo.attrs["frequency"].value), lo)
        said = [t["text"] for t in toasts.drain()]
        self.assertTrue(any("moved from 10 MS/s to 2.330997 MS/s by itself; set back, it held" in t for t in said),
                        said)
        self.assertEqual(dev.status()["moves"], 2)

    def test_a_rate_that_moves_and_holds_when_set_back_does_not_unkey(self):
        r, dev, fake = self.pluto()
        self.key(r, dev)
        fake.move("rx", "sampling_frequency", 2_330_997)
        self.assertTrue(wait_for(lambda: r.state()["radioMoved"], timeout=5, radio=r))
        self.assertTrue(r.state()["keyed"])
        r.ptt(False)
        self.assertEqual(r.tx_log()[-1]["why"], "released")

    def test_a_rate_that_will_not_go_back_while_keyed_unkeys_with_the_reason_logged(self):
        r, dev, fake = self.pluto()
        self.key(r, dev)
        fake.stick("rx", "sampling_frequency", 2_330_997)
        self.assertTrue(wait_for(lambda: not r.state()["keyed"], timeout=5, radio=r))
        last = r.tx_log()[-1]
        self.assertEqual(last["event"], "unkey")
        self.assertIn("sample rate moved from 10 MS/s to 2.330997 MS/s by itself and will not go back", last["why"])
        self.assertIs(r.state()["radioMoved"][0]["held"], False)
        self.assertFalse(dev.tx_on)
        self.assertEqual(fake.tx_ch.attrs["hardwaregain"].value, "-89.75")

    def test_a_moved_tx_lo_unkeys_at_once_without_setting_it_back(self):
        r, dev, fake = self.pluto()
        self.key(r, dev)
        writes = len(fake.written("tx_lo", "frequency"))
        fake.move("tx_lo", "frequency", 1_254_666_998)
        self.assertTrue(wait_for(lambda: not r.state()["keyed"], timeout=5, radio=r))
        self.assertIn("transmit LO moved from", r.tx_log()[-1]["why"])
        self.assertIn("off frequency", r.tx_log()[-1]["why"])
        self.assertEqual(len(fake.written("tx_lo", "frequency")), writes)        # not set back first
        (m,) = r.state()["radioMoved"]
        self.assertEqual((m["what"], m["to"], m["held"]), ("txLo", 1_254_666_998, None))
        self.assertEqual(fake.tx_lo.attrs["powerdown"].value, "1")


class Profiles(unittest.TestCase):
    """Every profile is a valid set of settings that fits its own rate."""

    def test_405_lines_go_at_the_rate_their_transmitter_needs(self):
        # Research 21: a HackRF at 20 MS/s; a USB nano at 6.25 with sound
        # when its transmit is known to carry it (its USB network down: 6.36
        # MS/s), else 5 without; anything else at the AM-VSB rate.
        from ohsatv import profiles, sysa
        cases = ((("pluto", "usb", 6.8, "hackrf"), 20e6, True), (("pluto", "usb", 6.8, "", 6.36), 6.25e6, True),
                 (("pluto", "usb", 6.8, "", 5.2), 5e6, False), (("pluto", "usb", 6.8, ""), 5e6, False),
                 (("pluto", "usb-rx8", 15.0, "", 6.36), 6.25e6, True), (("pluto", "ethernet", 16.2, ""), 10e6, True))
        for args, rate, sound in cases:
            with self.subTest(args=args):
                settings, sentence = profiles.best(*args)
                st = state.defaults(("setting", "control"))
                st.update(settings)
                got = st["a405Rate"] or st["amRate"]
                self.assertEqual(got, rate)
                self.assertEqual(sysa.plan(got)["sound"] is not None, sound)
                if args[1].startswith("usb") and args[3] == "":
                    self.assertIn("405 lines at", sentence)
        # A nano whose transmit was measured is a combination of its own.
        self.assertNotEqual(profiles.key("pluto", "usb", 6.8, "", 6.36), profiles.key("pluto", "usb", 6.8, "", 5.2))

    def test_every_profile_is_valid_and_fits(self):
        from ohsatv import modem, profiles
        for args in (("pluto", "usb", 6.8, ""), ("pluto", "usb-rx8", 15.0, ""), ("pluto", "usb-tx8", 15.0, ""),
                     ("pluto", "usb-net", 5.3, ""), ("pluto", "ethernet", 16.2, ""),
                     ("pluto", "ethernet", 28.0, ""), ("pluto", "ethernet", None, ""), ("pluto", "usb", 6.8, "hackrf")):
            settings, _ = profiles.best(*args)
            with self.subTest(args=args):
                for k, v in settings.items():
                    self.assertEqual(state.coerce(k, v), v, k)          # within the table's limits
                st = state.defaults(("setting", "control"))
                st.update(settings)
                fm = modem.fm_plan(st["fmRate"], st["fmDeviation"], st["fmVideoBw"], st["fmSub"], st["fmSubIndex"])
                am = modem.am_plan(st["amRate"], st["amVideoBw"], st["amVsbLower"], st["amSoundOffset"])
                self.assertTrue(fm["fits"], fm["why"])
                self.assertTrue(am["fits"], am["why"])
