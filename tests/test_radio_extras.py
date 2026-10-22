"""NICAM through the engine, headless, on the simulator: the plan and the
guard, the simulated station's NICAM stereo, our own stereo test round the
loop, the sound heard switching between NICAM and the analogue sound, and
the status keys the window uses. Nothing here opens a real radio."""
import time
import unittest

import numpy as np

from helpers import crop, tmp_dir  # noqa: F401  (crop: path setup)
from ohsatv import nicam
from ohsatv.audio import NullAudio
from ohsatv.radio import Radio

AM = dict(device="sim", audioBackend="null", band="23cm", system="am", amRate=10_000_000, rxFreq=1275.0e6,
          txFreq=1275.0e6, simStations=True, callsign="GM4BRI", locator="IO86", licenceAck=True)


class Grab(NullAudio):
    """What the engine plays: mono and stereo blocks, kept apart."""

    def __init__(self):
        super().__init__(realtime=False)
        self.mono, self.stereo = [], []

    def play(self, a):
        self.mono.append(np.asarray(a, np.float32).copy())

    def play_stereo(self, lr):
        self.stereo.append(np.asarray(lr, np.float32).copy())


def wait_for(fn, timeout=10.0, radio=None):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if radio is not None:
            radio.heartbeat()
        r = fn()
        if r:
            return r
        time.sleep(0.05)
    return None


class Base(unittest.TestCase):
    def make(self, **over):
        tmp = tmp_dir(self, "ohsatv-nicam-")
        cfg = dict(AM)
        cfg.update(over)
        self.audio = Grab()
        r = Radio(cfg, conf_path=tmp / "c.json", audio=self.audio, data_dir=tmp / "data")
        self.addCleanup(r.stop)
        return r


class Station(Base):
    def test_the_simulated_station_sends_nicam_stereo(self):
        r = self.make(simNicam=True)
        r.start()
        self.assertTrue(wait_for(lambda: r.state()["nicamActive"], timeout=15, radio=r), r.state())
        st = r.state()
        self.assertTrue(st["nicamLock"])
        self.assertEqual(st["nicamMode"], "stereo")
        self.assertLess(st["nicamBer"], nicam.USE_BER)
        # What is played is NICAM's stereo test: 1 kHz left, 2 kHz right.
        wait_for(lambda: sum(len(x) for x in self.audio.stereo) > 24000, timeout=5, radio=r)
        lr = np.concatenate(self.audio.stereo)[-9600:]
        for ch, f in ((0, 1000.0), (1, 2000.0)):
            spec = np.abs(np.fft.rfft(lr[:, ch] * np.hanning(len(lr))))
            self.assertAlmostEqual(np.argmax(spec) * 48000 / len(lr), f, delta=10)

    def test_without_it_the_analogue_sound_is_heard(self):
        r = self.make(simNicam=False)
        r.start()
        wait_for(lambda: r.state()["rxLock"] == "locked", timeout=10, radio=r)
        wait_for(lambda: False, timeout=2.0, radio=r)
        self.assertFalse(r.state()["nicamLock"])
        self.assertTrue(self.audio.mono)
        self.assertFalse(self.audio.stereo)


class Transmit(Base):
    def test_plan_and_guard(self):
        r = self.make(nicam=True)
        pl = r.plan()
        self.assertIsNotNone(pl["nicam"])
        self.assertGreater(pl["occupied"][1], nicam.TOP)
        # Where the rate has no room: the plan says why, the picture is
        # still allowed (nothing is given up for NICAM).
        r.set(amRate=6_250_000)
        pl = r.plan()
        self.assertIsNone(pl["nicam"])
        self.assertIn("no NICAM", " ".join(pl["why"]))
        self.assertTrue(pl["fits"])

    def test_405_lines_carry_none_and_say_so(self):
        # System A never had NICAM: asked for, the plan refuses it and
        # still allows the picture.
        r = self.make(nicam=True, videoFormat="405", amRate=5_000_000)
        pl = r.plan()
        self.assertEqual(pl.get("format"), "405")
        self.assertIsNone(pl["nicam"])
        self.assertFalse(pl["nicamRx"])
        self.assertIn("no NICAM with 405 lines", " ".join(pl["why"]))
        self.assertTrue(pl["fits"])

    def test_our_stereo_test_round_the_loop(self):
        # FM-ATV at 20 MS/s, keyed on the simulator with no other station:
        # what comes back is our own NICAM.
        r = self.make(system="fm", fmRate=20_000_000, rxFreq=1249.0e6, txFreq=1249.0e6, simStations=False,
                      nicam=True)
        r.start()
        time.sleep(0.5)
        r.ptt(True)
        self.assertTrue(r.state()["keyed"])
        ok = wait_for(lambda: r.state()["nicamActive"], timeout=20, radio=r)
        st = r.state()
        r.ptt(False)
        self.assertTrue(ok, st)
        self.assertEqual(st["nicamMode"], "stereo")


class Fallback(Base):
    def test_the_analogue_sound_takes_over_when_nicam_is_poor(self):
        # The simulated station at 13 dB CNR: NICAM may still lock, but
        # with more errors than USE_BER - the FM sound is played.
        r = self.make(simNicam=True, simCnrDb=13.0)
        r.start()
        wait_for(lambda: r.state()["nicamLock"], timeout=12, radio=r)
        wait_for(lambda: False, timeout=2.0, radio=r)
        st = r.state()
        if st["nicamLock"]:
            self.assertGreater(st["nicamBer"], nicam.USE_BER)
        self.assertFalse(st["nicamActive"])
        n = len(self.audio.stereo)
        wait_for(lambda: False, timeout=1.0, radio=r)
        self.assertEqual(len(self.audio.stereo), n)
        self.assertTrue(self.audio.mono)


if __name__ == "__main__":
    unittest.main()
