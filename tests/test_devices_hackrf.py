"""HackRF transmit device against a fake libhackrf: the sample conversion,
the quiet-until-keyed order, gain mapping, range, the ring buffer the USB
callback drains, and underruns being counted. There is no HackRF on the
development host, and nothing here could key one."""
import ctypes
import threading
import time
import unittest
from unittest import mock

import numpy as np

from helpers import crop, tmp_dir  # noqa: F401  (crop: path setup)
from ohsatv import host
from ohsatv.devices import hackrf
from ohsatv.devices.base import DeviceError


class FakeLib:
    """Records calls in order; start_tx keeps the callback so the test can
    play libhackrf's USB thread."""

    def __init__(self, present=True, fail=None):
        self.present, self.fail = present, fail or {}
        self.calls = []
        self.cb = None
        self.vga = None
        self.amp = None
        self.streaming = False

    def _rec(self, name, *a):
        self.calls.append((name,) + a)
        return self.fail.get(name, 0)

    def hackrf_init(self):
        return self._rec("init")

    def hackrf_exit(self):
        return self._rec("exit")

    def hackrf_open(self, pdev):
        if not self.present:
            return self._rec("open") or -1000
        return self._rec("open")

    def hackrf_open_by_serial(self, serial, pdev):
        return self._rec("open_by_serial", serial) if self.present else -1000

    def hackrf_close(self, dev):
        return self._rec("close")

    def hackrf_set_sample_rate(self, dev, fs):
        return self._rec("rate", fs)

    def hackrf_compute_baseband_filter_bw(self, bw):
        # libhackrf's max2837_ft table, rounded down as it does.
        table = [1750000, 2500000, 3500000, 5000000, 5500000, 6000000, 7000000, 8000000, 9000000, 10000000,
                 12000000, 14000000, 15000000, 20000000, 24000000, 28000000]
        ok = [b for b in table if b <= bw]
        return ok[-1] if ok else table[0]

    def hackrf_set_baseband_filter_bandwidth(self, dev, bw):
        return self._rec("bw", bw)

    def hackrf_set_txvga_gain(self, dev, g):
        self.vga = g
        return self._rec("vga", g)

    def hackrf_set_amp_enable(self, dev, on):
        self.amp = on
        return self._rec("amp", on)

    def hackrf_set_freq(self, dev, f):
        return self._rec("freq", f)

    def hackrf_start_tx(self, dev, cb, ctx):
        self.cb = cb
        self.streaming = True
        ret = self._rec("start_tx")
        self.prefill()
        return ret

    def prefill(self):
        """libhackrf fills its four transfers from the callback inside
        start_tx, before anything has been written."""
        for _ in range(TRANSFERS):
            self.pull(TRANSFER)

    def hackrf_stop_tx(self, dev):
        self.streaming = False
        return self._rec("stop_tx")

    def hackrf_error_name(self, ret):
        return b"HACKRF_ERROR_FAKE"

    def pull(self, nbytes):
        """Play libhackrf's USB thread: ask for nbytes, return them as int8."""
        buf = (ctypes.c_uint8 * nbytes)()
        t = hackrf.HackrfTransfer(None, ctypes.cast(buf, ctypes.POINTER(ctypes.c_uint8)), nbytes, 0, None, None)
        self.cb(ctypes.pointer(t))
        return np.frombuffer(bytes(buf), np.int8), t.valid_length


class EventThreadLib(FakeLib):
    """libusb's threading, which FakeLib leaves out. libhackrf runs one
    event thread; it reaps every completion in turn - bulk transfers, which
    call our callback, and the synchronous control transfers behind gain,
    amplifier, start and stop. A control call therefore returns only once
    the event thread has got past any callback that came due before it.
    Each control call here makes a bulk transfer come due just ahead of it,
    the unlucky interleaving, and gives up after `limit` seconds, recording
    the call as stuck: on the real library it would never return."""

    def __init__(self, limit=1.0, **kw):
        super().__init__(**kw)
        self.limit = limit
        self.stuck = []
        self.events = []
        self.cond = threading.Condition()
        self.thread = threading.Thread(target=self._event_thread, daemon=True)
        self.thread.start()

    def _event_thread(self):
        while True:
            with self.cond:
                while not self.events:
                    self.cond.wait()
                kind, done = self.events.pop(0)
            if kind == "bulk" and self.cb is not None:
                self.pull(4096)
            done.set()

    def _control(self, name, fn, *a):
        due, reaped = threading.Event(), threading.Event()
        with self.cond:
            if self.streaming:
                self.events.append(("bulk", due))
            self.events.append(("control", reaped))
            self.cond.notify()
        if not reaped.wait(self.limit):
            self.stuck.append(name)
        return fn(*a)

    def hackrf_set_txvga_gain(self, dev, g):
        return self._control("vga", super().hackrf_set_txvga_gain, dev, g)

    def hackrf_set_amp_enable(self, dev, on):
        return self._control("amp", super().hackrf_set_amp_enable, dev, on)

    def hackrf_stop_tx(self, dev):
        return self._control("stop_tx", super().hackrf_stop_tx, dev)


TRANSFER = 262144        # libhackrf 2024.02: TRANSFER_BUFFER_SIZE, bytes
TRANSFERS = 4            # TRANSFER_COUNT: transfers in flight
DEVBUF = 32768           # the firmware's bulk buffer, bytes (USB_BULK_BUFFER_SIZE)
CTRL_S = 0.0003          # one control transfer on USB 2, round trip
LEAK = 10 ** (-30 / 10)  # LO leakage and DAC offset against a full-scale signal (assumed)


class AirModel(FakeLib):
    """A HackRF whose output can be measured, on a simulated clock.

    What it models, from libhackrf 2024.02 and its firmware:
    - the stream: start_tx fills four 256 KiB transfers through the
      callback; the device plays them back to back at fs; a transfer
      completes when the device has taken its last byte into its 32 KiB
      buffer, and the callback is then asked to refill it, unless it has
      returned non-zero once, after which nothing is refilled;
    - control transfers (gain, amplifier, transceiver mode) take CTRL_S and
      act on the radio when they complete, ahead of any queued samples;
    - hackrf_stop_tx: the queue is cancelled (the device keeps what is in its
      buffer), then transceiver mode off turns the RF path off;
    - with the RF path on, the output is gain x (|sample|^2 + LEAK): zeros -
      the callback's, or the firmware's when the stream runs dry - leave the
      carrier from LO leakage. With the path off there is nothing.
    """

    def __init__(self, fs, **kw):
        super().__init__(**kw)
        self.fs = fs
        self.now = 0.0
        self.vga_t, self.amp_t, self.path_t = [(0.0, 0)], [(0.0, 0)], [(0.0, 0)]
        self.segments = []           # (time the first sample plays, int8 I/Q)
        self.play_end = 0.0
        self.inflight = []           # completion times of the transfers in flight
        self.submitted = []          # when each transfer was handed to the device
        self.completed = []
        self.refilling = False       # libhackrf's device->streaming
        self.cut = None

    def clock(self):
        return self.now

    def sleep(self, dt):
        self.advance(self.now + dt)

    def advance(self, t):
        while self.inflight and min(self.inflight) <= t:
            tc = min(self.inflight)
            self.inflight.remove(tc)
            self.now = max(self.now, tc)
            self.completed.append(self.now)
            if self.refilling:
                self._fill()
        self.now = max(self.now, t)

    def _fill(self):
        buf = (ctypes.c_uint8 * TRANSFER)()
        t = hackrf.HackrfTransfer(None, ctypes.cast(buf, ctypes.POINTER(ctypes.c_uint8)), TRANSFER, TRANSFER,
                                  None, None)
        ret = self.cb(ctypes.pointer(t))
        if ret != 0 or t.valid_length <= 0:
            self.refilling = False
            return False
        begin = max(self.play_end, self.now)
        self.segments.append((begin, np.frombuffer(bytes(buf), np.int8)[:t.valid_length].copy()))
        self.play_end = begin + t.valid_length / 2 / self.fs
        self.inflight.append(self.play_end - DEVBUF / 2 / self.fs)
        self.submitted.append(self.now)
        return True

    def _control(self, timeline, value):
        self.advance(self.now + CTRL_S)
        timeline.append((self.now, value))

    def hackrf_set_txvga_gain(self, dev, g):
        self._control(self.vga_t, g)
        return super().hackrf_set_txvga_gain(dev, g)

    def hackrf_set_amp_enable(self, dev, on):
        self._control(self.amp_t, on)
        return super().hackrf_set_amp_enable(dev, on)

    def prefill(self):
        self._control(self.path_t, 1)
        self.refilling = True
        self.play_end = self.now
        for _ in range(TRANSFERS):
            if not self._fill():
                break

    def hackrf_stop_tx(self, dev):
        self.inflight.clear()
        self.refilling = False
        self.cut = min(self.play_end, self.now + DEVBUF / 2 / self.fs)
        self._control(self.path_t, 0)
        return super().hackrf_stop_tx(dev)

    def per_sample(self, t0, t1):
        """Over [t0, t1) at fs: times, the samples' own power (0 where none
        play), and the VGA, amplifier and RF path in force."""
        n = int(round((t1 - t0) * self.fs))
        tt = t0 + np.arange(n) / self.fs
        p = np.zeros(n)
        for begin, b in self.segments:
            x = b.astype(np.float64)
            pw = (x[0::2] ** 2 + x[1::2] ** 2) / 127.0 ** 2
            i0 = int(round((begin - t0) * self.fs))
            lo, hi = max(0, i0), min(n, i0 + len(pw))
            if lo < hi:
                p[lo:hi] = pw[lo - i0:hi - i0]
        if self.cut is not None:
            p[tt >= self.cut] = 0.0

        def at(timeline):
            ts = np.array([e[0] for e in timeline])
            vs = np.array([e[1] for e in timeline], np.float64)
            return vs[np.searchsorted(ts, tt, side="right") - 1]
        return tt, p, at(self.vga_t), at(self.amp_t), at(self.path_t)

    def radiated(self, t0, t1, bin_s=0.00025):
        """Mean output power per bin over [t0, t1), relative to a full-scale
        signal at full gain with the amplifier off; (bin start times, power)."""
        tt, p, vga, amp, path = self.per_sample(t0, t1)
        n = len(tt)
        gain = 10 ** ((vga - hackrf.VGA_MAX + 14 * amp) / 10)
        out = path * gain * (p + LEAK)
        k = int(round(bin_s * self.fs))
        m = n // k
        return t0 + np.arange(m) * bin_s, out[:m * k].reshape(m, k).mean(axis=1)


def tone(n):
    """A carrier at fs/4, 0.95 of full scale: every sample is (+-121, 0) or
    (0, +-121) after conversion, so the power is exactly constant and the
    output's power follows the gain and nothing else. (An FM signal's int8
    rounding wobbles its power by hundredths of a dB.)"""
    return (0.95 * 1j ** np.arange(n)).astype(np.complex64)


def transmit(d, m, seconds):
    """Play the engine's transmit thread on the model's clock: a 40 ms frame
    whenever the ring has room for one, else let the radio run for 1 ms."""
    frame = 4 * int(0.01 * m.fs)
    end = m.now + seconds
    while m.now < end:
        if len(d.ring) // 2 - d.n // 2 >= frame:
            d.write(tone(frame))
        else:
            m.sleep(0.001)


def keyed_up(fs, gain_db, amp=False):
    """A HackRF on the air model from tx_start until its gain is up: the
    four zero transfers start_tx filled, the first transfer of picture, the
    ramp, and a little more."""
    m = AirModel(fs)
    d = hackrf.HackRFDevice(fs, {"hackrfAmp": amp}, lib=m, clock=m.clock, sleep=m.sleep)
    d.open()
    d.set_tx_lo(1251e6)
    d.set_tx_gain(gain_db)
    t0 = m.now
    d.tx_start()
    transmit(d, m, (TRANSFERS + 2) * TRANSFER / 2 / fs + 0.03)
    return d, m, t0


def keyed(fs, gain_db, amp=False):
    d, m, _ = keyed_up(fs, gain_db, amp)
    return d, m


def primed(d, lib, nbytes=TRANSFER):
    """Play libhackrf's thread on a FakeLib until the first transfer of
    picture has gone, then write once more so the gain comes up."""
    for _ in range(4 * TRANSFERS):
        if d.radiating:
            break
        if d.n < nbytes:
            d.write(np.full(nbytes, 0.5 + 0j, np.complex64))
        lib.pull(nbytes)
    d.write(np.full(1000, 0.5 + 0j, np.complex64))


def device(fs=10e6, **cfg):
    lib = FakeLib(**cfg.pop("lib", {}))
    d = hackrf.HackRFDevice(fs, cfg, lib=lib)
    return d, lib


class Samples(unittest.TestCase):
    def test_int8_scaling_rounding_and_symmetric_clipping(self):
        iq = np.array([0.5 + 0.25j, -1.0 - 1.0j, 1.5 - 2.0j, 0.004 + 0j], np.complex64)
        b = hackrf.to_int8(iq)
        self.assertEqual(b.dtype, np.int8)
        np.testing.assert_array_equal(b, [64, 32, -127, -127, 127, -127, 1, 0])

    def test_a_modulated_signal_survives_quantisation(self):
        # FM at the modulators' 0.95 peak: int8 keeps ~45 dB+ SNR.
        ph = np.cumsum(np.random.default_rng(1).standard_normal(100_000) * 0.3)
        iq = (0.95 * np.exp(1j * ph)).astype(np.complex64)
        b = hackrf.to_int8(iq).astype(np.float64)
        back = (b[0::2] + 1j * b[1::2]) / 127
        snr = 10 * np.log10(np.mean(np.abs(iq) ** 2) / np.mean(np.abs(back - iq) ** 2))
        self.assertGreater(snr, 40)
        self.assertLessEqual(np.abs(b).max(), 127)

    def test_gain_mapping(self):
        self.assertEqual(hackrf.vga_for(0.0), 47)
        self.assertEqual(hackrf.vga_for(-10.0), 37)
        self.assertEqual(hackrf.vga_for(-47.0), 0)
        self.assertEqual(hackrf.vga_for(-89.75), 0)


class Model(unittest.TestCase):
    def test_open_is_quiet(self):
        d, lib = device()
        d.open()
        self.assertEqual(lib.vga, 0)
        self.assertEqual(lib.amp, 0)
        self.assertFalse(lib.streaming)
        self.assertIn(("rate", 10e6), lib.calls)

    @mock.patch.object(host, "WINDOWS", False)         # Linux's sentence; Windows' in test_windows_host
    def test_missing_radio_and_missing_library_are_sentences(self):
        d, _ = device(lib={"present": False})
        with self.assertRaisesRegex(DeviceError, "No HackRF"):
            d.open()
        d = hackrf.HackRFDevice(10e6, {}, lib=None)
        d._lib = None
        orig = hackrf.load_lib
        hackrf.load_lib = lambda: None
        try:
            with self.assertRaisesRegex(DeviceError, "libhackrf0"):
                d.open()
        finally:
            hackrf.load_lib = orig

    def test_rates_above_20_msps_are_refused(self):
        d, _ = device(fs=25e6)
        with self.assertRaisesRegex(DeviceError, "2-20 MS/s"):
            d.open()

    def test_transmit_only_and_its_range(self):
        d, _ = device()
        d.open()
        self.assertFalse(d.can_rx)
        self.assertTrue(d.duplex())
        self.assertEqual(d.freq_range(), (1e6, 6e9))
        with self.assertRaises(DeviceError):
            d.read(10)
        with self.assertRaisesRegex(DeviceError, "1 MHz-6 GHz"):
            d.set_tx_lo(6.1e9)
        d.set_tx_lo(5665e6)

    def test_gain_reaches_the_radio_only_once_the_picture_has_gone_out(self):
        # Samples in our ring are not enough: libhackrf's four transfers of
        # zeros from start_tx are ahead of them, and then the first transfer
        # of picture has to reach the device.
        d, lib = device(hackrfAmp=False)
        d.open()
        d.set_tx_gain(-10.0)
        d.tx_start()
        self.assertTrue(lib.streaming)
        self.assertEqual(lib.vga, 0)
        d.write(np.full(4 * TRANSFER, 0.5 + 0j, np.complex64))
        self.assertEqual(lib.vga, 0)                 # queued, not yet on the air
        for _ in range(TRANSFERS):                   # the zeros go; picture is refilled
            lib.pull(TRANSFER)
            d.write(np.full(10, 0.5 + 0j, np.complex64))
            self.assertEqual(lib.vga, 0)
        lib.pull(TRANSFER)                           # the first picture transfer has gone
        d.write(np.full(10, 0.5 + 0j, np.complex64))
        self.assertEqual(lib.vga, 37)
        self.assertEqual(lib.amp, 0)

    def test_amplifier_only_when_set_and_only_with_the_picture(self):
        d, lib = device(hackrfAmp=True)
        d.open()
        d.tx_start()
        self.assertEqual(lib.amp, 0)
        primed(d, lib)
        self.assertEqual(lib.amp, 1)
        d.tx_stop()
        self.assertEqual(lib.amp, 0)
        d, lib = device(hackrfAmp=False)
        d.open()
        d.tx_start()
        primed(d, lib)
        self.assertEqual(lib.amp, 0)

    def test_stop_drops_gain_before_the_stream(self):
        d, lib = device()
        d.open()
        d.set_tx_gain(-20.0)
        d.tx_start()
        primed(d, lib)
        self.assertEqual(lib.vga, 27)
        lib.calls.clear()
        d.tx_stop()
        names = [c[0] for c in lib.calls]
        self.assertIn("vga", names)
        self.assertLess(max(i for i, n in enumerate(names) if n == "vga"), names.index("stop_tx"))
        self.assertEqual(lib.vga, 0)

    def test_callback_drains_what_was_written_in_order(self):
        d, lib = device()
        d.open()
        d.tx_start()
        iq = (np.arange(3000) % 100 / 100.0).astype(np.complex64)
        d.write(iq)
        got, valid = lib.pull(4000)
        self.assertEqual(valid, 4000)
        np.testing.assert_array_equal(got, hackrf.to_int8(iq[:2000]))
        got, _ = lib.pull(2000)
        np.testing.assert_array_equal(got, hackrf.to_int8(iq[2000:]))
        self.assertEqual(d.status()["txUnderruns"], 0)

    def test_underruns_are_counted_and_filled_with_zeros(self):
        d, lib = device()
        d.open()
        d.tx_start()
        primed(d, lib, 1000)
        while d.n:                                   # the host falls behind
            lib.pull(1000)
        d.write(np.full(100, 0.5 + 0j, np.complex64))
        got, valid = lib.pull(1000)
        self.assertEqual(valid, 1000)
        self.assertTrue(np.all(got[200:] == 0))
        st = d.status()
        self.assertEqual(st["txUnderruns"], 1)
        self.assertGreater(st["txUnderrunMs"], 0)

    def test_write_blocks_when_the_ring_is_full_and_resumes_when_drained(self):
        d, lib = device(fs=2e6)                        # ring: 0.25 s = 500k samples
        d.open()
        d.tx_start()
        done = threading.Event()

        def writer():
            d.write(np.zeros(700_000, np.complex64))
            done.set()
        t = threading.Thread(target=writer, daemon=True)
        t.start()
        time.sleep(0.3)
        self.assertFalse(done.is_set())                 # paced by the radio
        lib.pull(2 * 300_000)
        t.join(2)
        self.assertTrue(done.is_set())

    def test_no_control_call_waits_on_the_callback_it_is_blocking(self):
        # Key up, raise the gain by writing, change it while keyed, unkey:
        # every libhackrf control call made while a bulk transfer is due must
        # still come back, which it cannot if the caller holds what the
        # callback needs.
        lib = EventThreadLib(limit=0.5)
        d = hackrf.HackRFDevice(10e6, {}, lib=lib)
        d.open()
        d.set_tx_gain(-20.0)
        d.tx_start()
        primed(d, lib, 4096)
        self.assertEqual(lib.vga, 27)
        d.set_tx_gain(-30.0)
        self.assertEqual(lib.vga, 17)
        t0 = time.monotonic()
        d.tx_stop()
        self.assertLess(time.monotonic() - t0, 0.4)
        self.assertEqual(lib.stuck, [])
        self.assertEqual((lib.vga, lib.amp, lib.streaming), (0, 0, False))

    def test_a_second_stop_waits_for_the_first(self):
        # The engine stops from its own thread and the transmit thread stops
        # again on its way out; the second must not return while the first
        # is still taking the radio down.
        d, lib = device()
        d.open()
        d.tx_start()
        d.write(np.full(100, 0.5 + 0j, np.complex64))
        inside, release = threading.Event(), threading.Event()
        stop = lib.hackrf_stop_tx

        def slow_stop(dev):
            inside.set()
            release.wait(2)
            return stop(dev)
        lib.hackrf_stop_tx = slow_stop
        first = threading.Thread(target=d.tx_stop, daemon=True)
        first.start()
        self.assertTrue(inside.wait(2))
        second_done = threading.Event()
        threading.Thread(target=lambda: (d.tx_stop(), second_done.set()), daemon=True).start()
        self.assertFalse(second_done.wait(0.2))
        release.set()
        self.assertTrue(second_done.wait(2))
        first.join(2)
        self.assertFalse(lib.streaming)
        self.assertEqual([c[0] for c in lib.calls].count("stop_tx"), 1)

    @mock.patch.object(host, "WINDOWS", False)         # Linux's sentence; Windows' in test_windows_host
    def test_discover_lists_serials_or_says_why_not(self):
        notes = []
        orig = hackrf.load_lib
        hackrf.load_lib = lambda: None
        try:
            self.assertEqual(hackrf.discover(notes=notes), [])
        finally:
            hackrf.load_lib = orig
        self.assertIn("libhackrf0", notes[0])


class KeyUp(unittest.TestCase):
    """What goes out from tx_start() until the gain is up, on the air model.
    start_tx's four transfers are zeros - the carrier from LO leakage - and
    they play before any picture; the gain and the amplifier must stay at
    VGA 0 and off for all of them."""

    def test_no_carrier_above_vga_0_before_the_picture(self):
        for fs in (2e6, 10e6, 20e6):
            for gain, amp in ((-17.0, False), (-7.0, False), (0.0, True)):
                with self.subTest(fs=fs, gain=gain, amp=amp):
                    d, m, t0 = keyed_up(fs, gain, amp)
                    tt, p, vga, amp_on, path = m.per_sample(t0, m.now)
                    picture = np.flatnonzero(p > 0)
                    self.assertGreater(len(picture), 0)
                    first = picture[0]
                    # The zeros ahead of the picture, and any sample without
                    # picture while the path is on: VGA 0, amplifier off.
                    bare = (path > 0) & (p == 0)
                    self.assertTrue(bare[:first].any())       # the zeros were there to guard against
                    self.assertTrue(np.all(vga[bare] == 0), "LO carrier above VGA 0")
                    self.assertTrue(np.all(amp_on[bare] == 0), "LO carrier with the amplifier on")
                    # and the gain did come up, to the setting, with the picture
                    self.assertEqual(m.vga, hackrf.vga_for(gain))
                    self.assertEqual(m.amp, 1 if amp else 0)
                    self.assertEqual(d.status()["txUnderruns"], 0)
                    # status() says when the gain arrived, on the driver's clock
                    t_full = [e[0] for e in m.vga_t if e[1] == hackrf.vga_for(gain)][-1]
                    self.assertAlmostEqual(d.status()["txGainUpMs"], 1000 * (t_full - t0), delta=0.05)

    def test_the_rise_is_a_ramp_and_comes_soon_after_the_picture(self):
        for fs in (2e6, 20e6):
            for gain in (-17.0, 0.0):
                with self.subTest(fs=fs, gain=gain):
                    d, m, t0 = keyed_up(fs, gain)
                    tt, p, *_ = m.per_sample(t0, m.now)
                    t_pic = tt[np.flatnonzero(p > 0)[0]]
                    t, w = m.radiated(t0, m.now)
                    final = float(np.median(w[-20:]))
                    db = 10 * np.log10(np.maximum(w, 1e-30) / final)
                    # within 20 dB of the final level, no quarter-ms rises
                    # more than 6 dB (the raised cosine's steepest is ~4.5)
                    for a, b in zip(db[:-1], db[1:]):
                        if b > -20.0 + 1e-6:
                            self.assertLess(b, a + 6.0, f"a {b - a:.1f} dB step up to {b:.1f} dB")
                    t_up = t[np.flatnonzero(db > -1.0)[0]]
                    t_10 = t[np.flatnonzero(db > -10.0)[0]]
                    self.assertGreater(t_up - t_10, 0.25 * hackrf.RAMP_S)
                    # Full power no later than the first picture transfer
                    # plus the ramp and a few control transfers.
                    self.assertLess(t_up - t_pic, TRANSFER / 2 / fs + hackrf.RAMP_S + 0.002)

    def test_a_stop_during_the_rise_takes_down_what_it_reached(self):
        m = AirModel(10e6)
        d = hackrf.HackRFDevice(10e6, {}, lib=m, clock=m.clock, sleep=m.sleep)
        d.open()
        d.set_tx_gain(0.0)
        d.tx_start()
        sleep = m.sleep
        state = {"n": 0}

        def stop_mid_ramp(dt):
            sleep(dt)
            if d.primed and 0 < m.vga < 47 and state["n"] == 0:
                # The engine's thread calls tx_stop() now; it clears tx_on
                # at once and then waits for the ramp to give way.
                state["n"] += 1
                state["t"] = m.now
                state["thread"] = threading.Thread(target=d.tx_stop, daemon=True)
                state["thread"].start()
                end = time.monotonic() + 2
                while d.tx_on and time.monotonic() < end:
                    time.sleep(0.001)
        d.sleep = stop_mid_ramp
        frame = 4 * int(0.01 * m.fs)
        while d.tx_on and m.now < 0.2:
            if len(d.ring) // 2 - d.n // 2 >= frame:
                d.write(tone(frame))
            else:
                m.sleep(0.001)
        state["thread"].join(2)
        self.assertEqual(state["n"], 1)
        self.assertEqual((m.vga, m.amp, m.streaming), (0, 0, False))
        t, w = m.radiated(state["t"], state["t"] + 0.02)
        self.assertTrue(np.all(np.diff(w) <= 1e-12 * w[0]))
        self.assertTrue(np.all(w[t >= state["t"] + 0.01] == 0))


class Unkey(unittest.TestCase):
    """What goes out after tx_stop(), on the air model. The bound: from the
    stop call to the RF path off within OFF_S. The ramp itself is RAMP_S;
    the rest is control transfers, two after the ramp and one when a step
    straddles its end."""

    OFF_S = 0.010

    def unkey(self, d, m):
        tu = m.now
        d.tx_stop()
        m.sleep(0.05)
        t, p = m.radiated(tu - 0.005, tu + 0.03)
        keyed_p = float(np.median(p[t < tu]))
        return tu, t, p, keyed_p

    def assert_falls_to_nothing(self, tu, t, p, keyed_p):
        after = p[t >= tu]
        self.assertTrue(np.all(np.diff(after) <= 1e-12 * keyed_p),
                        "the output rose after the stop call")
        self.assertLessEqual(after.max(), keyed_p * (1 + 1e-9))
        self.assertTrue(np.all(p[t >= tu + self.OFF_S] == 0.0), f"still radiating {self.OFF_S * 1e3:g} ms on")

    def test_after_unkey_the_output_only_falls_and_is_off_within_10_ms(self):
        for fs in (2e6, 10e6, 20e6):
            for gain, amp in ((-47.0, False), (-27.0, False), (0.0, False), (-27.0, True)):
                with self.subTest(fs=fs, gain=gain, amp=amp):
                    d, m = keyed(fs, gain, amp)
                    tu, t, p, keyed_p = self.unkey(d, m)
                    self.assertGreater(keyed_p, 0.5 * 10 ** ((hackrf.vga_for(gain) - 47 + 14 * amp) / 10))
                    self.assert_falls_to_nothing(tu, t, p, keyed_p)
                    off = [e[0] for e in m.path_t if e[1] == 0][-1] - tu
                    self.assertLessEqual(off, hackrf.RAMP_S + 3 * CTRL_S + 1e-9)
                    self.assertAlmostEqual(d.status()["txStopMs"], 1000 * (off + CTRL_S), delta=0.05)
                    self.assertEqual((m.vga, m.amp, m.streaming), (0, 0, False))

    def test_the_fall_is_a_ramp_not_a_step(self):
        # Within 20 dB of the keyed level no quarter-millisecond falls more
        # than 6 dB: the raised cosine's steepest quarter there is ~4.5 dB.
        # Dropping the gain in one write - the driver before this - fell
        # the whole way in one.
        for gain in (-27.0, 0.0):
            with self.subTest(gain=gain):
                d, m = keyed(20e6, gain)
                tu, t, p, keyed_p = self.unkey(d, m)
                db = 10 * np.log10(np.maximum(p[t >= tu - 0.0005], 1e-30) / keyed_p)
                for a, b in zip(db[:-1], db[1:]):
                    if a > -20.0 + 1e-6:
                        self.assertGreater(b, a - 6.0, f"a {a - b:.1f} dB step from {a:.1f} dB")
                # and it takes the ramp's time to get 10 dB down
                down = t[(t >= tu) & (10 * np.log10(np.maximum(p, 1e-30) / keyed_p) < -10)][0] - tu
                self.assertGreater(down, 0.4 * hackrf.RAMP_S)

    def test_a_transfer_due_during_the_ramp_is_not_refilled(self):
        # At 20 MS/s a transfer completes every 6.5 ms, so one often comes
        # due mid-ramp. The callback ends the stream there instead of queuing
        # more picture, and the transfers already in flight carry the ramp.
        d, m = keyed(20e6, -27.0)
        m.sleep(min(m.inflight) - m.now - 0.001)
        tu = m.now
        d.tx_stop()
        m.sleep(0.05)
        self.assertTrue(any(tu < c < tu + hackrf.RAMP_S for c in m.completed))
        self.assertFalse(any(s > tu for s in m.submitted))
        t, p = m.radiated(tu, tu + hackrf.RAMP_S)
        g = 10 ** ((np.interp(t, [e[0] for e in m.vga_t], [e[1] for e in m.vga_t]) - 47) / 10)
        self.assertTrue(np.all(p[t < tu + 0.75 * hackrf.RAMP_S] > 0.5 * g[t < tu + 0.75 * hackrf.RAMP_S] * 0.9))

    def test_a_stream_that_ran_dry_still_only_falls(self):
        # Underrun before the unkey: the firmware sends zeros and the
        # carrier from LO leakage goes out at the keyed gain. Unkeying must
        # take that down the same way.
        d, m = keyed(10e6, -20.0)
        m.sleep(0.3)                    # nothing written: the 0.25 s ring runs out
        self.assertGreater(d.status()["txUnderruns"], 0)
        tu, t, p, keyed_p = self.unkey(d, m)
        self.assertAlmostEqual(keyed_p, LEAK * 10 ** ((27 - 47) / 10), delta=keyed_p * 1e-6)
        self.assert_falls_to_nothing(tu, t, p, keyed_p)

    def test_a_failing_gain_write_still_stops_the_stream(self):
        d, m = keyed(10e6, -27.0)
        m.fail["vga"] = -1000
        d.tx_stop()
        self.assertFalse(m.streaming)
        self.assertEqual(m.path_t[-1][1], 0)
        self.assertEqual(m.amp, 0)

    def test_the_lo_stays_where_it_was_and_the_antenna_port_is_left_alone(self):
        # Retuning after the stop would re-enable the mixer the stop turned
        # off; the antenna "enable" is the port's DC bias. Neither is used.
        d, m = keyed(10e6, -27.0)
        m.calls.clear()
        d.tx_stop()
        names = [c[0] for c in m.calls]
        self.assertNotIn("freq", names)
        self.assertEqual(names[-2:], ["stop_tx", "amp"])
        self.assertTrue(all(n == "vga" for n in names[:-2]))


if __name__ == "__main__":
    unittest.main()


class Filter(unittest.TestCase):
    """The MAX2837 baseband filter as devices/hackrf.py models it, against
    the datasheet's own figures (Maxim 19-5235 Rev 3, written out here, not
    read back from the module) and against the 5 October 2026 burst."""

    def test_the_datasheet_skirt(self):
        att = hackrf.attenuation_db
        # Rejection, receive table (the hold taken off: fs huge).
        for bw, f, db in ((5e6, 3.3e6, 7), (10e6, 6.7e6, 7), (10e6, 15e6, 57), (10e6, 20e6, 75)):
            with self.subTest(bw=bw, f=f):
                self.assertAlmostEqual(float(att(f, bw, 1e12)), db, delta=1.0)
        # Transmit: flat to 0.2 dB over 0-2.3 MHz at 5 MHz and 0-4.6 at 10;
        # 80 dB far out, and never more.
        self.assertLess(float(att(2.3e6, 5e6, 1e12)), 0.2)
        self.assertLess(float(att(4.6e6, 10e6, 1e12)), 0.2)
        self.assertAlmostEqual(float(att(30e6, 5e6, 1e12)), 80.0, places=6)

    def test_the_settings_libhackrf_takes(self):
        self.assertEqual(hackrf.default_filter(8e6), 6e6)            # what the 5 October burst ran
        self.assertEqual(hackrf.default_filter(20e6), 15e6)
        # 405 lines reach 2.15 MHz either side of the LO: 5 MHz passes that
        # flat, 3.5 does not.
        self.assertEqual(hackrf.filter_for(20e6, 2.15e6), 5e6)
        self.assertEqual(hackrf.filter_for(20e6, 40e6), 28e6)

    def test_the_8_msps_burst_and_20_msps(self):
        # 5 October 2026, 8 MS/s, 6 MHz filter: the sound carrier (2.1 MHz
        # below the LO) imaged 8 MHz above it at -18.6 dBc, the vision
        # carrier (1.4 MHz above) 8 MHz below at -29.0. The sound line was
        # 0.1 dB over the vision line.
        at, db = hackrf.image(8e6, -2.1e6, 6e6)
        self.assertEqual(at, 5.9e6)
        self.assertAlmostEqual(db + 0.1, -18.6, delta=2.0)
        at, db = hackrf.image(8e6, 1.4e6, 6e6)
        self.assertEqual(at, -6.6e6)
        self.assertAlmostEqual(db, -29.0, delta=2.5)
        # At 20 MS/s through 5 MHz: far under -40 dBc, even with the
        # shortfall the 8 MS/s burst showed against the datasheet.
        for f in (-2.1e6, 1.4e6, -2.15e6, 2.15e6):
            self.assertLess(hackrf.image(20e6, f, 5e6)[1], -70)

    def test_set_tx_bandwidth_narrows_the_filter(self):
        d, lib = device(fs=20e6)
        d.open()
        self.assertEqual(d.status()["bbFilter"], 15e6)
        d.set_tx_bandwidth(2.15e6)
        self.assertEqual(lib.calls[-1], ("bw", 5_000_000))
        self.assertEqual(d.status()["bbFilter"], 5e6)
        # None is libhackrf's own choice for the rate again, as open() set it.
        d.set_tx_bandwidth(None)
        self.assertEqual(lib.calls[-1], ("bw", 15_000_000))
        self.assertEqual(d.status()["bbFilter"], 15e6)


class EngineFilter(unittest.TestCase):
    """The engine sets the HackRF's filter for the plan before the gain
    rises at key-up: 405 lines at 20 MS/s get 5 MHz, 625 lines 8, FM
    libhackrf's default for the rate."""

    def test_the_filter_is_set_before_the_gain_rises(self):
        r, lib = self.over(dict(amRate=20_000_000, videoFormat="405", autoProfile=False))
        self.check(r, lib, 5e6)

    def test_625_lines_are_steered_to_20_msps_and_filtered(self):
        # At 10 MS/s, the AM-VSB default, a HackRF's image of the sound
        # carrier comes out 4 MHz below vision about 3 dB under it
        # (profiles.py): the HackRF's profile moves AM-VSB to 20 MS/s, and the
        # over narrows the filter to 625 lines' plan.
        r, lib = self.over(dict(videoFormat="pal", autoProfile=True))
        self.assertEqual((r.state()["amRate"], r.fs), (20_000_000, 20e6))
        self.assertIn("AM-VSB (625 and 405 lines) at 20 MS/s", r.state()["profile"])
        self.assertFalse([w for w in r.state()["txWarnings"] if "image" in w])
        self.check(r, lib, 8e6)

    def test_an_fm_over_after_an_am_over_sets_the_fm_filter_itself(self):
        # Changing the system restarts the radio, and reopening the HackRF
        # puts libhackrf's default back - so today an FM over after an AM
        # one does get FM's filter, but only because of that reopen. An FM
        # over used to leave the filter alone, and anything that kept the
        # device open across the change (a system switch without a restart,
        # say) would have sent FM through the AM over's 5 MHz, narrower than
        # its deviation. So the FM over must set the filter itself: check()
        # counts only what was written from key-up, after the reopen.
        from helpers import wait_for
        r, lib = self.over(dict(amRate=20_000_000, fmRate=20_000_000, videoFormat="405", autoProfile=False))
        self.check(r, lib, 5e6)
        since = len(lib.calls)
        r.set(system="fm")
        self.assertTrue(wait_for(lambda: ("open",) in lib.calls[since:], 10))
        with r.life:                                         # the restart has finished
            pass
        self.assertEqual((r.state()["system"], r.fs), ("fm", 20e6))
        self.check(r, lib, hackrf.default_filter(20e6))

    def over(self, cfg):
        from ohsatv.audio import NullAudio
        from ohsatv.radio import Radio
        from test_radio import FAST
        tmp = tmp_dir(self, "ohsatv-hkfilter-")
        r = Radio(dict(FAST, formatAuto=False, rxFreq=1255.0e6, txFreq=1255.0e6, txDevice="hackrf",
                       allowHardwareTx=True, callsign="GM4BRI", licenceAck=True, licenceClass="full", simStations=False,
                       audioSource="tone", **cfg),
                  conf_path=f"{tmp}/c.json", audio=NullAudio(realtime=False), data_dir=f"{tmp}/data")
        self.addCleanup(r.stop)
        lib = FakeLib()
        r._tx_device_override = hackrf.HackRFDevice(20e6, {}, lib=lib)
        r.start()
        self.assertEqual(r.txdev.bb_filter, 15e6)            # libhackrf's default until an over
        stop = threading.Event()

        def usb():                                           # libhackrf's thread
            while not stop.is_set():
                if lib.streaming and lib.cb is not None:
                    lib.pull(TRANSFER)
                time.sleep(0.002)
        t = threading.Thread(target=usb, daemon=True)
        t.start()
        self.addCleanup(stop.set)
        return r, lib

    def check(self, r, lib, want):
        """Key one over: the filter it ends on is `want`, and the last
        setting before its gain first rose was `want` - set by this over,
        not left by an earlier one."""
        from helpers import wait_for
        since = len(lib.calls)
        r.ptt(True)
        self.assertTrue(wait_for(lambda: any(c[0] == "vga" and c[1] > 0 for c in lib.calls[since:]), 15,
                                 beat=r.heartbeat))
        r.ptt(False)
        calls = lib.calls[since:]
        first_gain = next(i for i, c in enumerate(calls) if c[0] == "vga" and c[1] > 0)
        filters = [c[1] for c in calls[:first_gain] if c[0] == "bw"]
        self.assertEqual(filters[-1:], [int(want)])
        self.assertNotIn("bw", [c[0] for c in calls[first_gain:]])
        self.assertEqual(r.txdev.status()["bbFilter"], want)
