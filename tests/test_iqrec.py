"""IQ recording and playback (ohsatv/iqrec.py, ohsatv/devices/filedev.py):
what is written is what was received, the limits and the disk checks hold,
a recording plays back through the receive chain to a locked picture, and
the Pluto driver records its native 8-bit samples untouched."""
from __future__ import annotations

import io
import json
import os
import sys
import threading
import time
import unittest
from collections import namedtuple
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import numpy as np

from helpers import crop, tmp_dir  # noqa: F401  (crop: path setup)
from ohsatv import iqrec, modem, pal, systems, testcard
from ohsatv.audio import NullAudio
from ohsatv.devices import ad936x
from ohsatv.devices.base import DeviceError
from ohsatv.devices.filedev import FileDevice
from ohsatv.radio import Radio
from test_radio import FAST, wait_for

Usage = namedtuple("Usage", "total used free")
PLENTY = lambda _p: Usage(1 << 40, 0, 1 << 40)          # noqa: E731


def tmpdir(tc) -> Path:
    return tmp_dir(tc, "ohsatv-iqrec-")


def wait_done(rec, timeout=5.0):
    end = time.monotonic() + timeout
    while not rec.done.is_set() and time.monotonic() < end:
        time.sleep(0.01)
    return rec.done.is_set()


class Recorder(unittest.TestCase):
    def test_cf32_round_trip_with_metadata(self):
        d = tmpdir(self)
        x = (np.random.default_rng(1).normal(0, 0.1, 50_000) * (1 + 1j)).astype(np.complex64)
        rec = iqrec.Recorder(d / "a", 1e6, 1255e6, "cf32_le", {"core:description": "test", "openhamshack:system": "fm"},
                             max_seconds=10, disk_usage=PLENTY)
        for k in range(0, len(x), 10_000):
            rec.feed(x[k:k + 10_000])
        rec.stop()
        r = iqrec.Recording(d / "a.sigmf-meta")
        np.testing.assert_array_equal(r.samples(0, r.n), x)
        g = r.globals
        self.assertEqual((g["core:datatype"], g["core:sample_rate"], g["openhamshack:samples"]), ("cf32_le", 1e6, 50_000))
        self.assertEqual((g["core:description"], g["openhamshack:system"], g["openhamshack:stop_reason"]), ("test", "fm", "stopped"))
        self.assertEqual(r.freq, 1255e6)
        self.assertIn("core:datetime", r.meta["captures"][0])

    def test_a_retune_starts_a_new_capture_segment(self):
        # 5 October 2026: format recognition moved the LibreSDR's LO from
        # 1257.4248 to 1253.6 MHz 3.6 s into a 20 s recording, whose
        # metadata then gave 1257.4248 for all of it.
        d = tmpdir(self)
        x = np.zeros(30_000, np.complex64)
        rec = iqrec.Recorder(d / "a", 1e6, 1257.4248e6, "cf32_le", {"openhamshack:rx_format": "pal"},
                             max_seconds=10, disk_usage=PLENTY)
        rec.feed(x[:10_000])
        rec.retune(1257.4248e6, "pal")                  # nothing moved: no new segment
        rec.feed(x[10_000:20_000])
        rec.retune(1253.6e6, "405")
        rec.feed(x[20_000:])
        rec.stop()
        r = iqrec.Recording(d / "a.sigmf-meta")
        caps = r.meta["captures"]
        self.assertEqual([(c["core:sample_start"], c["core:frequency"]) for c in caps],
                         [(0, 1257.4248e6), (20_000, 1253.6e6)])
        self.assertEqual(caps[1]["openhamshack:rx_format"], "405")
        self.assertEqual((r.freq_at(0), r.freq_at(19_999), r.freq_at(20_000), r.freq_at(29_999)),
                         (1257.4248e6, 1257.4248e6, 1253.6e6, 1253.6e6))
        dev = FileDevice({"playFile": str(d / "a.sigmf-meta")}, 1e6)
        dev.open()
        dev.set_rx_lo(1253.6e6)
        notes = " ".join(dev.status()["notes"])
        self.assertIn("retuned during this recording", notes)
        self.assertIn("1257.4248 MHz", notes)
        dev.close()

    def test_native_integers_are_written_untouched_and_read_back_to_full_scale(self):
        d = tmpdir(self)
        for dt, etype, fsc in (("ci8", np.int8, 128), ("ci16_le", np.int16, 2048)):
            with self.subTest(dt=dt):
                raw = np.random.default_rng(2).integers(-fsc, fsc, 20_000).astype(etype)
                rec = iqrec.Recorder(d / dt, 2e6, 433e6, dt, max_seconds=10, disk_usage=PLENTY)
                rec.feed(raw.tobytes())
                rec.stop()
                self.assertEqual((d / f"{dt}.sigmf-data").read_bytes(), raw.tobytes())
                r = iqrec.Recording(d / f"{dt}.sigmf-data")
                want = (raw[0::2] + 1j * raw[1::2]) / fsc
                np.testing.assert_allclose(r.samples(0, r.n), want.astype(np.complex64), atol=1e-7)

    def test_the_time_limit_stops_it_exactly(self):
        d = tmpdir(self)
        rec = iqrec.Recorder(d / "t", 100_000, 0, "cf32_le", max_seconds=0.25, disk_usage=PLENTY)
        for _ in range(10):
            rec.feed(np.ones(10_000, np.complex64))
        self.assertTrue(wait_done(rec))
        self.assertEqual(rec.status()["bytes"], 25_000 * 8)
        self.assertEqual(rec.reason, "limit reached")
        self.assertEqual(json.loads((d / "t.sigmf-meta").read_text())["global"]["openhamshack:samples"], 25_000)

    def test_done_means_the_metadata_is_final(self):
        # `done` was set before the final metadata was written, so a reader
        # it woke could find the metadata from the start - no sample count,
        # no reason. The time limit test above failed that way in 9 of 15
        # runs under load (6 October 2026). Here the final write is made
        # slow, so the old order fails every time rather than by chance.
        d = tmpdir(self)
        real = iqrec.Recorder._write_meta

        def slow(rec, final=False):
            if final:
                time.sleep(0.3)
            real(rec, final)
        with mock.patch.object(iqrec.Recorder, "_write_meta", slow):
            rec = iqrec.Recorder(d / "f", 100_000, 0, "cf32_le", max_seconds=0.1, disk_usage=PLENTY)
            rec.feed(np.ones(20_000, np.complex64))
            self.assertTrue(wait_done(rec))
            g = json.loads((d / "f.sigmf-meta").read_text())["global"]
        self.assertEqual((g["openhamshack:samples"], g["openhamshack:stop_reason"]), (10_000, "limit reached"))

    def test_the_size_limit_stops_it_first_when_smaller(self):
        d = tmpdir(self)
        rec = iqrec.Recorder(d / "s", 100_000, 0, "ci8", max_seconds=60, max_bytes=30_001, disk_usage=PLENTY)
        for _ in range(5):
            rec.feed(bytes(20_000))
        self.assertTrue(wait_done(rec))
        self.assertEqual(rec.status()["bytes"], 30_000)          # whole samples only

    def test_refuses_to_start_without_room_for_the_whole_recording(self):
        d = tmpdir(self)
        small = lambda _p: Usage(1 << 30, 0, 300 * 1024 * 1024)          # noqa: E731
        with self.assertRaises(iqrec.RecordError) as cm:
            iqrec.Recorder(d / "x", 10e6, 0, "ci8", max_seconds=60, disk_usage=small)    # 1.2 GB wanted
        self.assertIn("Not enough disk space", str(cm.exception))
        self.assertFalse((d / "x.sigmf-data").exists())

    def test_stops_with_a_reason_when_the_disk_fills_while_writing(self):
        d = tmpdir(self)
        free = [1 << 40]
        rec = iqrec.Recorder(d / "f", 100_000, 0, "ci8", max_seconds=60,
                             disk_usage=lambda _p: Usage(1 << 40, 0, free[0]))
        rec.feed(bytes(1000))
        free[0] = 10 * 1024 * 1024
        end = time.monotonic() + 4
        while not rec.done.is_set() and time.monotonic() < end:
            rec.feed(bytes(1000))
            time.sleep(0.05)
        self.assertTrue(rec.done.is_set())
        self.assertIn("disk is nearly full", rec.reason)

    def test_a_writer_that_falls_behind_marks_the_gap_instead_of_splicing(self):
        d = tmpdir(self)

        class Held(iqrec.Recorder):
            gate = threading.Event()

            def _writer(self):
                self.gate.wait()
                super()._writer()

        with mock.patch.object(iqrec, "QUEUE_BUFFERS", 2):
            rec = Held(d / "g", 100_000, 0, "ci8", max_seconds=60, disk_usage=PLENTY)
        for k in range(5):
            rec.feed(np.full(2000, k, np.int8).tobytes())      # 1000 samples each; only 2 fit the queue
        Held.gate.set()
        rec.stop()
        self.assertEqual(rec.lost, 3000)
        ann = json.loads((d / "g.sigmf-meta").read_text())["annotations"]
        self.assertEqual([(a["core:sample_start"], a["core:comment"].split()[0]) for a in ann], [(2000, "1000")] * 3)
        self.assertEqual(iqrec.Recording(d / "g.sigmf-meta").n, 2000)

    def test_an_unreadable_recording_is_a_sentence(self):
        d = tmpdir(self)
        (d / "bad.sigmf-meta").write_text(json.dumps({"global": {"core:datatype": "cu8", "core:sample_rate": 1e6}}))
        with self.assertRaises(iqrec.RecordError) as cm:
            iqrec.Recording(d / "bad.sigmf-meta")
        self.assertIn("cu8", str(cm.exception))


class Playback(unittest.TestCase):
    def recording(self, n=30_000, fs=1e6):
        d = tmpdir(self)
        x = (np.arange(n) % 1000 / 1000).astype(np.complex64)
        rec = iqrec.Recorder(d / "p", fs, 1255e6, "cf32_le", max_seconds=60, disk_usage=PLENTY)
        rec.feed(x)
        rec.stop()
        return d / "p.sigmf-meta", x

    def test_loops_and_stops(self):
        path, x = self.recording()
        dev = FileDevice({"playFile": str(path), "playLoop": True}, 1e6)
        dev.realtime = False
        dev.open()
        y = np.concatenate([dev.read(20_000) for _ in range(3)])
        np.testing.assert_array_equal(y, np.concatenate([x, x])[:60_000])
        self.assertEqual(dev.status()["loops"], 2)
        dev = FileDevice({"playFile": str(path), "playLoop": False}, 1e6)
        dev.realtime = False
        dev.open()
        y = np.concatenate([dev.read(20_000) for _ in range(2)])
        np.testing.assert_array_equal(y[:30_000], x)
        self.assertFalse(y[30_000:].any())
        self.assertTrue(dev.status()["ended"])

    def test_paced_in_real_time(self):
        path, _ = self.recording(n=200_000)
        dev = FileDevice({"playFile": str(path)}, 1e6)
        dev.open()
        t0 = time.monotonic()
        for _ in range(5):
            dev.read(40_000)
        self.assertGreater(time.monotonic() - t0, 0.15)          # 0.2 s of samples, not instant

    def test_a_pluto_recording_is_played_back_through_the_pluto_s_response(self):
        # What the radio's filters did is in the recording, uncorrected (it
        # holds what the radio delivered), so playback undoes them as the
        # live receiver did. The 5 October captures say only what the
        # recording script wrote: the board and the rf_bandwidth.
        path, _ = self.recording(fs=12.5e6)
        meta = json.loads(path.read_text())
        meta["global"].update({"core:hw": "Nano PlutoSDR (Z7010-AD9361) usb:3.19.5 fw tezuka-v0.3.21",
                               "openhamshack:rf_bandwidth": 12500000.0})
        path.write_text(json.dumps(meta))
        dev = FileDevice({"playFile": str(path)}, 12.5e6)
        dev.open()
        self.assertAlmostEqual(20 * np.log10(float(dev.rx_response(5.5e6))), -4.2, delta=0.1)
        self.assertIn("12.5 MS/s", dev.status()["rxCorrection"])
        # Path rates showing a FIR at work: its taps are not known, so no correction.
        meta["global"]["openhamshack:rx_path_rates"] = ("BBPLL:1022976007 ADC:31968000 R2:10656000 R1:5328000 "
                                                   "RF:2664000 RXSAMP:666000")
        path.write_text(json.dumps(meta))
        dev = FileDevice({"playFile": str(path)}, 12.5e6)
        dev.open()
        self.assertIsNone(dev.rx_response)

    def test_a_recording_from_anything_else_is_played_flat(self):
        path, _ = self.recording()
        dev = FileDevice({"playFile": str(path)}, 1e6)
        dev.open()
        self.assertIsNone(dev.rx_response)
        self.assertIsNone(dev.status()["rxCorrection"])

    def test_a_rate_mismatch_or_missing_file_is_a_sentence(self):
        path, _ = self.recording()
        with self.assertRaises(DeviceError):
            FileDevice({"playFile": str(path)}, 2e6).open()
        with self.assertRaises(DeviceError):
            FileDevice({"playFile": str(path) + "x"}, 1e6).open()
        with self.assertRaises(DeviceError):
            FileDevice({}, 1e6).open()


class EngineRecordAndPlay(unittest.TestCase):
    def make(self, **over):
        d = tmpdir(self)
        cfg = dict(FAST, rxFreq=1275.0e6, txFreq=1275.0e6, recDir=str(d / "rec"))   # the sim's AM-VSB station
        cfg.update(over)
        r = Radio(cfg, conf_path=d / "c.json", audio=NullAudio(realtime=False), data_dir=d / "data")
        self.addCleanup(r.stop)
        return r, d

    def test_record_then_play_back_to_a_locked_picture(self):
        r, d = self.make()
        with mock.patch.object(iqrec.shutil, "disk_usage", PLENTY):
            with self.assertRaises(ValueError):
                r.record_iq(True)                                # not running yet
            r.start()
            path = r.record_iq(True, seconds=1.5)
            self.assertTrue(r.state()["recording"])
            self.assertTrue(wait_for(lambda: not r.state()["recording"], timeout=10, radio=r))
        r.stop()
        meta = json.loads(Path(path).read_text())
        g = meta["global"]
        self.assertEqual((g["core:datatype"], g["core:sample_rate"], g["openhamshack:system"]), ("cf32_le", 10e6, "am"))
        self.assertEqual(g["openhamshack:rx_freq"], 1275.0e6)
        self.assertEqual(g["openhamshack:samples"], 15_000_000)
        self.assertEqual(r.state()["recWhy"], "limit reached")
        self.assertEqual(g["openhamshack:placement"], "images")      # where the receiver looked for the carriers

        # Played back with the operator's radio set elsewhere: the recording
        # brings its own system, rate and tuning.
        p, _ = self.make(device="file", playFile=path, system="fm", rxFreq=1255.0e6, txFreq=1255.0e6)
        video = p.subscribe("video", 16)
        p.start()
        st = p.state()
        self.assertEqual((st["system"], st["amRate"], st["rxFreq"], st["fs"]), ("am", 10_000_000, 1275.0e6, 10e6))
        got = wait_for(lambda: video.drain(), timeout=15, radio=p)
        self.assertTrue(got)
        self.assertTrue(got[-1][2]["vLock"])
        self.assertIn("This radio cannot transmit", p._tx_refusals()[0])

    def test_a_recording_from_before_the_carriers_moved_plays_where_they_were(self):
        # Late on 5 October 2026 625-line AM-VSB at 10 MS/s moved its carriers off
        # the AD936x's images: the vision carrier from 2.42 to 3.00 MHz below
        # the LO (modem.am_plan). The 4 October colour captures say only
        # their LO and system - no openhamshack:rx_freq, params or placement -
        # as this one does, made with the old placement. Played back, the
        # receiver must look where their carriers are, not 0.575 MHz off:
        # with the new placement the tuning shown was 575 kHz out and the
        # carrier offset read +594 kHz (docs/atv-research.md 24).
        fs, lo = 10e6, 1253.4248e6
        d = tmpdir(self)
        old = modem.am_plan(fs, placement="centred")
        self.assertNotAlmostEqual(old["carrier"], modem.am_plan(fs)["carrier"], delta=0.5e6)
        card = testcard.test_card("GM4BRI", "IO86 TEST")
        enc, mod = pal.Encoder(fs), modem.AmVsbMod(fs, placement="centred")
        rec = iqrec.Recorder(d / "old", fs, lo, "cf32_le", {"openhamshack:system": "am"}, max_seconds=10,
                             disk_usage=PLENTY)
        for k in range(8):
            rec.feed(mod(enc.encode(card, k)))
        rec.stop()
        p, _ = self.make(device="file", playFile=str(d / "old.sigmf-meta"), playLoop=True)
        video = p.subscribe("video", 16)
        p.start()
        st = p.state()
        self.assertEqual(st["rxPlan"]["carrier"], old["carrier"])
        self.assertAlmostEqual(st["rxFreq"], lo + old["carrier"], delta=1)
        got = wait_for(lambda: video.drain(), timeout=15, radio=p)
        self.assertTrue(got and got[-1][2]["vLock"])
        self.assertTrue(wait_for(lambda: p.state()["carrierHz"] is not None, timeout=10, radio=p))
        self.assertLess(abs(p.state()["carrierHz"]), 5e3)
        # What would be sent is placed for now, whatever is being played.
        self.assertEqual(st["plan"]["carrier"], modem.am_plan(fs)["carrier"])

    def test_the_engine_s_recordings_ask_the_disk_when_they_start(self):
        # The engine's recordings check the disk as shutil.disk_usage says at
        # the time, so these tests' PLENTY reaches them. Until 6 October 2026
        # the check was bound at import and they read the real disk instead:
        # with /tmp full every engine recording test failed together.
        r, d = self.make()
        r.start()
        full = lambda _p: Usage(1 << 40, 1 << 40, 1 << 20)          # noqa: E731
        with mock.patch.object(iqrec.shutil, "disk_usage", full):
            with self.assertRaises(ValueError) as cm:
                r.record_iq(True, seconds=0.5)
        self.assertIn("Not enough disk space", str(cm.exception))
        self.assertFalse(r.state()["recording"])

    def test_the_format_being_decoded_is_what_plays_back(self):
        # Auto-detect can be decoding Mini-MAC while videoFormat (what we
        # transmit) is still PAL; the recording must say Mini-MAC.
        r, d = self.make(system="fm", rxFreq=1255.0e6, txFreq=1255.0e6, fmRate=16_000_000)
        with mock.patch.object(iqrec.shutil, "disk_usage", PLENTY):
            r.start()
            r._status(rxFormat="mac")
            path = r.record_iq(True, seconds=0.5)
            wait_for(lambda: not r.state()["recording"], timeout=10, radio=r)
        r.stop()
        g = json.loads(Path(path).read_text())["global"]
        self.assertEqual((g["openhamshack:rx_format"], g["openhamshack:params"]["videoFormat"]), ("mac", "mac"))
        p, _ = self.make(device="file", playFile=path)
        p._prepare_playback()
        self.assertEqual((p.st["videoFormat"], p.st["system"], p.st["fmRate"]), ("mac", "fm", 16_000_000))

    def test_a_format_switch_while_recording_is_in_the_metadata(self):
        # The receiver switching from 625 to 405 lines moves its LO
        # (radio._switch_rx); the recording must say where, and to what.
        #
        # The recording is ended once samples at the new LO are in it, not by
        # a 1 s limit. Under load, rebuilding the receiver for 405 lines took
        # long enough that a 1 s recording could fill before the switch
        # reached it, and its last segment kept the old LO (6 October 2026).
        # Blocks the receive thread feeds while the switch is under way are
        # taken at the old LO but decoded as 405, so they may form a segment
        # of their own; what must hold is that nothing at the new LO is
        # marked PAL.
        #
        # Format recognition is off. The switch is the test's own, and with
        # recognition on, the decoder's backlog of PAL samples could be
        # recognised as PAL and switch the receiver back before one block was
        # taken at the 405 LO. That was seen under load, and it is a race
        # between two switches, not the recording of one.
        r, d = self.make(rxFreq=1255.0e6, txFreq=1255.0e6, formatAuto=False)
        lo_405 = 1255.0e6 - systems.plan(10e6, "am", dict(r._params(), videoFormat="405"))["carrier"]
        with mock.patch.object(iqrec.shutil, "disk_usage", PLENTY):
            r.start()
            lo_pal = r.dev.rx_lo
            self.assertNotAlmostEqual(lo_pal, lo_405, delta=1e6)
            path = r.record_iq(True, seconds=60.0)
            rec = r.rec
            self.assertTrue(wait_for(lambda: rec.queued > 0, timeout=10, radio=r))
            r._switch_rx("405")
            self.assertTrue(wait_for(lambda: abs(rec.freq - lo_405) < 1, timeout=30, radio=r),
                            (rec.captures, r.rx_fmt))
            r.record_iq(False)
        r.stop()
        caps = json.loads(Path(path).read_text())["captures"]
        self.assertEqual((caps[0]["core:sample_start"], caps[0]["core:frequency"]), (0, lo_pal))
        self.assertEqual(caps[0].get("openhamshack:rx_format", "pal"), "pal")
        last = caps[-1]
        self.assertAlmostEqual(last["core:frequency"], lo_405, delta=1)
        self.assertEqual(last["openhamshack:rx_format"], "405")
        self.assertGreater(last["core:sample_start"], 0)
        self.assertIn(len(caps), (2, 3))
        for c in caps[1:-1]:
            # The switch under way: the old LO, the new format.
            self.assertEqual((c["core:frequency"], c["openhamshack:rx_format"]), (lo_pal, "405"))
        self.assertEqual([c["core:sample_start"] for c in caps], sorted({c["core:sample_start"] for c in caps}))

    def test_play_from_the_command_line(self):
        r, d = self.make()
        with mock.patch.object(iqrec.shutil, "disk_usage", PLENTY):
            r.start()
            path = r.record_iq(True, seconds=1.0)
            wait_for(lambda: not r.state()["recording"], timeout=10, radio=r)
        r.stop()
        from ohsatv.__main__ import main
        out = io.StringIO()
        env = {"OHSATV_CONF": str(d / "none.json"), "OHSATV_DATA": str(d / "data2")}
        with mock.patch.dict(os.environ, env), redirect_stdout(out):
            rc = main(["--headless", "--no-audio", "--play", path, "--seconds", "2.5"])
        self.assertEqual(rc, 0)
        lines = out.getvalue().splitlines()
        self.assertTrue(any("AM" in ln and "1275.000 MHz" in ln for ln in lines), lines)


def _video_gain(demod, f: float, fs: float) -> float:
    """An FM demodulator's video response at f, from an impulse."""
    imp = np.zeros(4096, np.float32)
    imp[0] = 1
    return float(abs(np.fft.rfft(demod.filt(imp))[int(round(f / fs * 4096))]))


class EngineUndoesTheRadiosFilters(unittest.TestCase):
    """The engine hands the receiving radio's response to the demodulator,
    writes it into a recording and gets it back on playback
    (ohsatv/devices/ad936x.py; docs/atv-research.md 17)."""

    def test_record_and_play_back_keep_the_correction(self):
        from test_radio import FakeHardware

        class Pluto(FakeHardware):
            kind = "pluto"

            def open(self):
                super().open()
                self.rx_response = ad936x.Response("rx", self.fs, self.fs)

            def status(self):
                r = self.fs
                return dict(super().status(), model="Nano PlutoSDR (Z7010-AD9361)", rfBandwidth=r,
                            rxPathRates=f"BBPLL:{64 * r:.0f} ADC:{8 * r:.0f} R2:{4 * r:.0f} R1:{2 * r:.0f} "
                                        f"RF:{r:.0f} RXSAMP:{r:.0f}")

        d = tmpdir(self)
        cfg = dict(FAST, system="fm", rxFreq=1255.0e6, txFreq=1255.0e6, fmRate=12_500_000, fmDeviation=1.2e6,
                   fmVideoBw=4.9e6, fmSub=5_500_000.0, autoProfile=False, recDir=str(d / "rec"))
        r = Radio(cfg, conf_path=d / "c.json", audio=NullAudio(realtime=False), data_dir=d / "data",
                  device=Pluto(cfg, 12.5e6))
        self.addCleanup(r.stop)
        flat = systems.make_demod(12.5e6, "fm", r._params())
        with mock.patch.object(iqrec.shutil, "disk_usage", PLENTY):
            r.start()
            # The receiver's 1.3 dB at the colour subcarrier, put back.
            gain = _video_gain(r.demod, 4.43e6, 12.5e6) / _video_gain(flat, 4.43e6, 12.5e6)
            self.assertAlmostEqual(20 * np.log10(gain), 1.33, delta=0.1)
            path = r.record_iq(True, seconds=0.3)
            wait_for(lambda: not r.state()["recording"], timeout=10, radio=r)
        r.stop()
        g = json.loads(Path(path).read_text())["global"]
        self.assertEqual(g["openhamshack:rf_bandwidth"], 12.5e6)
        self.assertIn("RXSAMP:12500000", g["openhamshack:rx_path_rates"])
        dev = FileDevice({"playFile": path}, 12.5e6)
        dev.open()
        self.assertEqual((dev.rx_response.fs, dev.rx_response.rf_bw, dev.rx_response.stages),
                         (12.5e6, 12.5e6, (2, 2, 2)))


class PlutoRecordsNative(unittest.TestCase):
    def test_8_bit_receive_is_recorded_as_ci8_bytes_untouched(self):
        from test_devices_pluto import EightBitReceive
        t = EightBitReceive()
        d, radio, _ = t.nano(fs=10e6)
        self.assertEqual(d.native_format, ("ci8", 128.0))
        rec = iqrec.Recorder(tmpdir(self) / "n", d.fs, 1255e6, "ci8", max_seconds=10, disk_usage=PLENTY)
        d.raw_tap = rec.feed
        y = d.read(d.rx_buf_n)
        rec.stop()
        self.assertEqual(rec.data_path.read_bytes(), radio.rxbufs[-1].raw)
        np.testing.assert_allclose(iqrec.Recording(rec.meta_path).samples(0, len(y)), y, atol=1e-7)

    def test_dropped_buffers_are_in_the_status(self):
        from test_devices_pluto import opened
        d, _ = opened()
        self.assertEqual(d.status()["rxDropped"], 0)


if __name__ == "__main__":
    unittest.main()
