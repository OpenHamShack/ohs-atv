"""OpenHamShack ATV on a radio other programs share (ohsatv/devices/shared.py, the
radio lock of OpenHamShack DATV's docs/radio-lock.md; devices/pluto.py
changed()): the lock taken before the first write and given back after the
last, a busy radio refused with the spec's sentence and left untouched, the
marker saying rx or tx, a lost lock unkeying first, and a radio changed by
something else refusing the next key-up.

Every radio is a fake - libiio's (test_devices_pluto.py) or libhackrf's
(test_devices_hackrf.py) - and every marker goes to the tests' own lock
directory (helpers.py). The radio's side of the lock runs for real, under
`sh -c` on a temporary path, wherever a test reaches the radio's shell."""
import json
import os
import socket
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

import helpers
from helpers import tmp_dir, wait_for
from ohsatv import radiolock
from ohsatv.devices import DeviceError, hackrf, pluto, shared
from ohsatv.radio import TxRefused
from test_devices_hackrf import FakeLib
from test_devices_pluto import FakeIIO, FakeRadio, FakeRxBuf
from test_radio import Base

MHZ = 1e6
LOCKS = Path(os.environ["OHSRADIO_LOCK_DIR"])
NANO = "ad936x-uqrur7hpfywamkgy"          # FakeRadio's hw_serial, as the spec names it
LIBRE = "ip:198.51.100.74"
HACKRF_SERIAL = "0000000000000000088869dc2439111b"
DATV_OVER = dict(v=1, use="tx", app="OpenHamShack DATV", version="0.3.0", host="openhamshack-laptop", bootId="b", pid=1,
                 user="user", since="2026-10-05T12:36:00Z", token="aaaaaaaaaaaaaaaa",
                 note="437.000 MHz DVB-S2 QPSK 2/3 333 kS")
BUSY = ('{label} is in use by OpenHamShack DATV on openhamshack-laptop (transmitting, since 12:36 UTC, last heard 5 s ago: '
        '"437.000 MHz DVB-S2 QPSK 2/3 333 kS"). Close it there first; OpenHamShack ATV has not touched the radio')


def marker_path(rid: str) -> Path:
    return LOCKS / f"openhamshack-radio.{rid}.lock"


def marker(rid: str) -> dict:
    return json.loads(marker_path(rid).read_text())


def plant(path: Path, age: float = 5.0, **kw) -> dict:
    """Another program's marker, `age` seconds old."""
    m = dict(DATV_OVER, **kw)
    path.write_text(json.dumps(m) + "\n")
    t = time.time() - age
    os.utime(path, (t, t))
    return m


class Paced(FakeIIO):
    """Receive buffers that take as long as the radio would, so an engine
    on them runs at its pace on air instead of spinning; transmitted blocks
    are not kept (test_radio.RadioMoves)."""

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


class FakeLibList(FakeLib):
    """libhackrf with its device list, which FakeLib leaves out."""

    def __init__(self, serials=(HACKRF_SERIAL,), **kw):
        super().__init__(**kw)
        self.serials = list(serials)

    def hackrf_device_list(self):
        self._rec("device_list")
        d = type("List", (), {})()
        d.serial_numbers = [s.encode() for s in self.serials]
        d.devicecount = len(self.serials)
        return type("Ptr", (), {"contents": d})()

    def hackrf_device_list_free(self, lst):
        self._rec("device_list_free")


class Fixture(unittest.TestCase):
    def setUp(self):
        self.addCleanup(self._sweep)

    @staticmethod
    def _sweep():
        # Markers left by a failed test must not refuse the next one.
        for f in LOCKS.glob("openhamshack-radio.*"):
            f.unlink()

    def fake_iio(self, radio, uri=LIBRE, paced=False):
        fake = (Paced if paced else FakeIIO)(radios={uri: radio})
        for p in (mock.patch.dict(sys.modules, {"iio": fake}),
                  mock.patch.object(pluto, "_usb_network_addresses", return_value=[]),
                  mock.patch.object(pluto, "SYS_USB_DEVICES", "/nonexistent")):
            p.start()
            self.addCleanup(p.stop)

    def pluto(self, radio=None, uri=LIBRE, cfg=None, lock=True, note="1255.000 MHz FM-ATV PAL"):
        """A PlutoDevice on a fake radio, set up as the engine sets it up,
        not yet opened."""
        radio = radio or FakeRadio("ad9364")
        self.fake_iio(radio, uri)
        d = pluto.PlutoDevice(uri, 10 * MHZ, cfg or {})
        d.lock_radio, d.lock_note = lock, note
        d.threaded_rx = False
        self.addCleanup(d.close)
        return d, radio

    def shell(self, refuse: str = ""):
        """The radio's shell, run here: ssh_runner hands back a runner that
        runs the very script it was given under `sh -c`, with the radio's
        /tmp marker moved to a temporary directory. `refuse`: answer as an
        unreachable radio does instead."""
        d = tmp_dir(self, "ohsatv-radio-tmp-")
        self.radio_marker = d / "openhamshack-radio.lock"
        self.ssh_calls = []

        def ssh_runner(host, user="root", password="", port=22, timeout=radiolock.SSH_TIMEOUT_S):
            self.ssh_calls.append((host, user, password))

            def run(script, data=""):
                if refuse:
                    return 255, "", refuse
                script = script.replace(radiolock._q(radiolock.RADIO_PATH), radiolock._q(str(self.radio_marker)))
                return radiolock.local_runner(script, data)
            return run
        for p in (mock.patch.object(radiolock, "ssh_runner", ssh_runner),
                  mock.patch.dict(os.environ, {"OHSATV_RADIO_SSH": "1"})):
            p.start()
            self.addCleanup(p.stop)


# ================================================================ the Pluto
class PlutoLock(Fixture):
    def test_taken_before_the_first_write_and_given_back_after_the_fir_goes_back(self):
        radio = FakeRadio("ad9364", fir="1", rate=666_000)
        held_at_write = []
        check = radio.check

        def watched(node, name, v):
            held_at_write.append((name, v, marker_path(NANO).exists()))
            return check(node, name, v)
        radio.check = watched
        d, _ = self.pluto(radio)
        d.open()
        self.assertTrue(held_at_write)
        self.assertTrue(all(h for _, _, h in held_at_write), held_at_write[:3])
        m = marker(NANO)
        self.assertEqual((m["radio"], m["use"], m["app"], m["note"], m["host"], m["pid"]),
                         (NANO, "rx", "OpenHamShack ATV", "1255.000 MHz FM-ATV PAL", socket.gethostname(), os.getpid()))
        self.assertEqual(d.status()["radioLock"]["where"], ["this computer"])
        held_at_write.clear()
        d.close()
        # The other program's FIR went back while the radio was still ours;
        # the marker went after.
        self.assertIn(("voltage_filter_fir_en", "1", True), held_at_write)
        self.assertFalse(marker_path(NANO).exists())

    def test_a_busy_radio_is_refused_with_the_specs_sentence_and_not_touched(self):
        theirs = plant(marker_path(NANO), radio=NANO)
        d, radio = self.pluto()
        with self.assertRaises(shared.RadioBusy) as cm:
            d.open()
        self.assertEqual(str(cm.exception), BUSY.format(label="the Pluto at 198.51.100.74"))
        self.assertEqual(radio.log, [])                              # not one write
        self.assertEqual(marker(NANO)["token"], theirs["token"])     # their marker as it was
        h = cm.exception.holder
        self.assertEqual((h["app"], h["host"], h["use"], h["note"], h["where"]),
                         ("OpenHamShack DATV", "openhamshack-laptop", "tx", DATV_OVER["note"], "this computer"))
        self.assertIsNone(d.ctx)                                     # the context let go of

    def test_a_radio_without_a_serial_is_named_by_its_address(self):
        radio = FakeRadio("ad9364")
        del radio.attrs["hw_serial"]
        d, _ = self.pluto(radio)
        d.open()
        self.assertEqual(marker("net-198.51.100.74")["radio"], "net-198.51.100.74")

    def test_not_asked_to_lock_it_takes_nothing(self):
        # A device opened outside the engine (the device tests, a bench script).
        d, _ = self.pluto(lock=False)
        d.open()
        self.assertEqual(list(LOCKS.glob("openhamshack-radio.*")), [])

    def test_a_failed_open_gives_the_radio_back(self):
        radio = FakeRadio("ad9364")
        radio.stick("rx", "sampling_frequency", 2_330_997)            # a rate it will not hold
        d, _ = self.pluto(radio)
        with self.assertRaises(DeviceError):
            d.open()
        self.assertFalse(marker_path(NANO).exists())


@unittest.skipIf(sys.platform == "win32", "the radio's side is a POSIX sh script, run here under the local sh; "
                 "Windows has none, and there it only runs on the radio (paramiko: test_windows_lock)")
class PlutoShell(Fixture):
    """The marker on the radio itself, through its shell - the scripts run
    locally under `sh -c`, as the radio would run them."""

    def test_the_radios_own_shell_carries_the_marker_too(self):
        self.shell()
        d, _ = self.pluto(cfg={"radioSshUser": "root", "radioSshPassword": "analog"})
        d.open()
        self.assertEqual(self.ssh_calls, [("198.51.100.74", "root", "analog")])
        on_radio = json.loads(self.radio_marker.read_text())
        self.assertEqual(on_radio["token"], marker(NANO)["token"])
        self.assertEqual(on_radio["use"], "rx")
        self.assertEqual(d.status()["radioLock"]["where"],
                         ["the radio's shell (ssh root@198.51.100.74)", "this computer"])
        d.close()
        self.assertFalse(self.radio_marker.exists())
        self.assertFalse(marker_path(NANO).exists())

    def test_a_marker_on_the_radio_from_another_computer_refuses_and_leaves_none_here(self):
        self.shell()
        d, radio = self.pluto()
        plant(self.radio_marker, radio=NANO)
        with self.assertRaises(shared.RadioBusy) as cm:
            d.open()
        self.assertEqual(str(cm.exception), BUSY.format(label="the Pluto at 198.51.100.74"))
        self.assertEqual(cm.exception.holder["where"], "the radio's shell (ssh root@198.51.100.74)")
        self.assertEqual(radio.log, [])
        self.assertFalse(marker_path(NANO).exists())

    def test_an_unreachable_shell_covers_this_computer_only_and_says_so(self):
        self.shell(refuse="ssh: connect to host 198.51.100.74 port 22: Connection refused")
        d, _ = self.pluto()
        d.open()
        notes = d.status()["notes"]
        self.assertTrue(any(n.startswith("The lock on the Pluto at 198.51.100.74 covers this computer only: the "
                                         "radio's shell (ssh root@198.51.100.74) did not answer (ssh: connect to "
                                         "host 198.51.100.74 port 22: Connection refused)") for n in notes), notes)
        self.assertEqual(d.status()["radioLock"]["where"], ["this computer"])
        self.assertTrue(marker_path(NANO).exists())

    def test_turned_off_in_settings_it_is_not_asked(self):
        self.shell()
        d, _ = self.pluto(cfg={"radioLockSsh": False})
        d.open()
        self.assertEqual(self.ssh_calls, [])
        self.assertTrue(marker_path(NANO).exists())

    def test_on_usb_with_its_network_down_there_is_no_shell_to_ask(self):
        self.shell()
        d, _ = self.pluto(uri="usb:3.21.5")
        d.open()
        self.assertEqual(self.ssh_calls, [])
        lock = d.status()["radioLock"]
        self.assertEqual(lock["where"], ["this computer"])
        self.assertEqual(lock["label"], "the Pluto on usb:3.21.5")
        self.assertTrue(any(n.startswith("The lock on the Pluto on usb:3.21.5 covers this computer only: it is "
                                         "reached over USB with its own network down here") for n in lock["notes"]),
                        lock["notes"])


class PlutoLost(Fixture):
    def test_a_lost_lock_is_noticed_and_nothing_is_put_back(self):
        lost = []
        with mock.patch.object(shared, "BEAT_S", 0.05):
            d, radio = self.pluto(FakeRadio("ad9364", fir="1", rate=666_000))
            d.on_lock_lost = lambda dev, why: lost.append(why)
            d.open()
        self.assertFalse(radio.fir_on())
        plant(marker_path(NANO), age=0, radio=NANO)                 # somebody removed ours and took it
        self.assertTrue(wait_for(lambda: lost, 3))
        self.assertIn("no longer this program's", lost[0])
        self.assertTrue(d.leave_as_is)
        d.close()
        self.assertFalse(radio.fir_on())                             # their radio: their filter
        self.assertEqual(marker(NANO)["app"], "OpenHamShack DATV")        # and their marker, untouched


# ============================================================ changed since
class PlutoChanged(Fixture):
    """The other half of the spec: the radio's whole state against what
    this program last set or saw."""

    def opened(self):
        radio = FakeRadio("ad9364", fir="0")
        d, _ = self.pluto(radio, lock=False)
        d.open()
        d.set_rx_lo(1255 * MHZ)
        return d, radio

    def test_each_value_changed_by_another_program_is_found(self):
        cases = [
            (lambda r: r.move("tx_lo", "frequency", 1_251_000_000), "transmit LO "),
            (lambda r: r.move("rx_lo", "frequency", 1_254_666_998), "receive LO 1255 MHz -> 1254.666998 MHz"),
            (lambda r: r.move("rx", "sampling_frequency", 7_670_000), "sample rate 10 MS/s -> 7.67 MS/s"),
            (lambda r: r.move("tx", "rf_bandwidth", 12_500_000), "transmit RF bandwidth 10 MHz -> 12.5 MHz"),
            (lambda r: r.move("rx", "rf_bandwidth", 12_500_000), "receive RF bandwidth 10 MHz -> 12.5 MHz"),
            (lambda r: r.move("tx", "hardwaregain", -10), "transmit attenuation -89.75 dB -> -10 dB"),
            (lambda r: r.move("rx", "gain_control_mode", "manual"), "receive gain mode slow_attack -> manual"),
            (lambda r: setattr(r.fir_ch.attrs["voltage_filter_fir_en"], "_v", "1"), "FIR filter enable 0 -> 1"),
        ]
        for change, phrase in cases:
            with self.subTest(phrase=phrase):
                d, radio = self.opened()
                self.assertEqual(d.changed(), "")
                writes = len(radio.log)
                change(radio)
                why = d.changed()
                self.assertIn(phrase, why)
                self.assertTrue(why.startswith("Something other than OpenHamShack ATV has changed this radio"), why)
                self.assertEqual(len(radio.log), writes)             # found, not set back
                d.accept()
                self.assertEqual(d.changed(), "")

    def test_found_stays_found_until_accepted_even_if_put_back(self):
        d, radio = self.opened()
        radio.move("tx", "rf_bandwidth", 12_500_000)
        self.assertTrue(d.changed())
        radio.move("tx", "rf_bandwidth", 10_000_000)
        self.assertIn("transmit RF bandwidth", d.changed())

    def test_this_programs_own_writes_are_never_taken_for_another_programs(self):
        d, radio = self.opened()
        d.set_rx_lo(1249 * MHZ)
        d.set_tx_lo(1249 * MHZ)
        d.set_tx_gain(-20)
        d.tx_start()
        d.write(np.zeros(d.tx_buf_n, np.complex64))
        d.tx_stop()
        self.assertEqual(d.changed(), "")

    def test_a_move_the_watch_set_back_is_still_a_change(self):
        d, radio = self.opened()
        radio.move("rx", "sampling_frequency", 2_330_997)
        self.assertEqual(d.watch()[0]["held"], True)
        self.assertIn("sample rate 10 MS/s -> 2.330997 MS/s", d.changed())


# ================================================================ the HackRF
class HackRFLock(Fixture):
    def device(self, lib=None, serial="", lock=True):
        self.lib = lib or FakeLibList()
        d = hackrf.HackRFDevice(10e6, {"hackrfSerial": serial}, lib=self.lib)
        d.lock_radio, d.lock_note = lock, "1255.000 MHz FM-ATV PAL, open, not keyed"
        self.addCleanup(d.close)
        return d

    def test_taken_before_the_radio_is_opened_named_by_its_serial_and_given_back(self):
        rid = f"hackrf-{HACKRF_SERIAL}"
        d = self.device()
        opened = self.lib.hackrf_open

        def watched(pdev):
            self.held_at_open = marker_path(rid).exists()
            return opened(pdev)
        self.lib.hackrf_open = watched
        d.open()
        self.assertTrue(self.held_at_open)
        m = marker(rid)
        self.assertEqual((m["use"], m["note"]), ("tx", "1255.000 MHz FM-ATV PAL, open, not keyed"))
        self.assertEqual(d.status()["radioLock"]["label"], "the HackRF 88869dc2439111b")
        d.close()
        self.assertFalse(marker_path(rid).exists())

    def test_a_configured_tail_of_the_serial_names_the_whole_radio(self):
        d = self.device(serial="2439111b")
        d.open()
        self.assertTrue(marker_path(f"hackrf-{HACKRF_SERIAL}").exists())

    def test_a_busy_hackrf_is_refused_before_it_is_opened(self):
        plant(marker_path(f"hackrf-{HACKRF_SERIAL}"))
        d = self.device()
        with self.assertRaises(shared.RadioBusy) as cm:
            d.open()
        self.assertEqual(str(cm.exception), BUSY.format(label="the HackRF 88869dc2439111b"))
        names = [c[0] for c in self.lib.calls]
        self.assertNotIn("open", names)
        self.assertNotIn("rate", names)
        self.assertEqual(names[-1], "exit")

    def test_a_hackrf_that_will_not_open_is_not_held(self):
        d = self.device(lib=FakeLibList(present=False))
        with self.assertRaises(DeviceError):
            d.open()
        self.assertEqual(list(LOCKS.glob("openhamshack-radio.*")), [])


# ================================================================ the engine
class EngineFixture(Fixture, Base):
    def engine(self, radio=None, **over):
        """The engine on a real PlutoDevice over the fake libiio, as
        test_radio.RadioMoves runs it: AM-VSB at 1249 MHz, 10 MS/s."""
        self.fake = radio or FakeRadio("ad9364")
        self.fake_iio(self.fake, paced=True)
        self.dev = pluto.PlutoDevice(LIBRE, 10 * MHZ, {})
        cfg = dict(allowHardwareTx=True, callsign="GM4BRI", licenceAck=True, licenceClass="full", autoProfile=False)
        cfg.update(over)
        return self.make(device=self.dev, **cfg)

    def key(self, r):
        r.ptt(True)
        self.assertTrue(wait_for(lambda: self.dev.tx_on, 8, beat=r.heartbeat))


class EngineLock(EngineFixture):
    def test_a_busy_radio_refuses_the_start_and_is_not_touched(self):
        plant(marker_path(NANO), radio=NANO)
        r = self.engine()
        with self.assertRaises(DeviceError) as cm:
            r.start()
        sentence = BUSY.format(label="the Pluto at 198.51.100.74")
        self.assertEqual(str(cm.exception), sentence)
        st = r.state()
        self.assertFalse(st["running"])
        self.assertEqual(st["error"], sentence)
        self.assertEqual(st["radioLock"]["refused"], sentence)
        self.assertEqual((st["radioLock"]["holder"]["app"], st["radioLock"]["holder"]["host"]),
                         ("OpenHamShack DATV", "openhamshack-laptop"))
        self.assertEqual(self.fake.log, [])

    def test_receiving_is_rx_keyed_is_tx_with_the_same_token_and_stopping_gives_it_back(self):
        r = self.engine()
        r.start()
        m = marker(NANO)
        self.assertEqual((m["use"], m["note"]), ("rx", "1249.000 MHz AM-VSB PAL"))
        held = r.state()["radioLock"]["held"]
        self.assertEqual([(h["radio"], h["use"]) for h in held], [(NANO, "rx")])
        self.key(r)
        self.assertEqual(marker(NANO)["use"], "tx")
        self.assertEqual(marker(NANO)["token"], m["token"])
        r.ptt(False)
        self.assertTrue(wait_for(lambda: marker(NANO)["use"] == "rx", 3))
        self.assertEqual(marker(NANO)["token"], m["token"])
        r.set(rxFreq=1251.0e6, txFreq=1251.0e6)
        self.assertTrue(wait_for(lambda: marker(NANO)["note"] == "1251.000 MHz AM-VSB PAL", 4))
        r.stop()
        self.assertFalse(marker_path(NANO).exists())
        self.assertEqual(r.state()["radioLock"]["held"], [])

    def test_a_lost_lock_unkeys_first_then_stops_and_says_so(self):
        with mock.patch.object(shared, "BEAT_S", 0.05):
            r = self.engine(FakeRadio("ad9364", fir="1", rate=666_000))
            r.start()
        self.key(r)
        theirs = plant(marker_path(NANO), age=0, radio=NANO)         # gone from under us, and taken
        self.assertTrue(wait_for(lambda: not r.state()["running"], 8, beat=r.heartbeat))
        unkey = [e for e in r.tx_log() if e["event"] == "unkey"][-1]
        self.assertTrue(unkey["why"].startswith("Lost the radio: the lock on the Pluto at 198.51.100.74"), unkey)
        self.assertIn("OpenHamShack DATV on openhamshack-laptop has it now", unkey["why"])
        st = r.state()
        self.assertFalse(st["keyed"])
        self.assertEqual(st["error"], unkey["why"])
        self.assertEqual(st["radioLock"]["lost"], unkey["why"])
        # Unkeyed (attenuated, LO down), and nothing of ours put back on
        # their radio: the FIR it was found with stays as they left it.
        self.assertEqual(self.fake.tx_ch.attrs["hardwaregain"].value, "-89.75")
        self.assertEqual(self.fake.tx_lo.attrs["powerdown"].value, "1")
        self.assertFalse(self.fake.fir_on())
        self.assertEqual(marker(NANO)["token"], theirs["token"])


class EngineChanged(EngineFixture):
    def test_a_radio_changed_by_another_program_refuses_to_key_until_accepted(self):
        r = self.engine()
        r.start()
        writes = len(self.fake.log)
        self.fake.move("tx", "rf_bandwidth", 12_500_000)
        with self.assertRaises(TxRefused) as cm:
            r.ptt(True)
        self.assertIn("transmit RF bandwidth 10 MHz -> 12.5 MHz", str(cm.exception))
        st = r.state()
        self.assertFalse(st["keyed"])
        self.assertEqual(st["radioChanged"], str(cm.exception))
        self.assertIn(str(cm.exception), st["txRefused"])
        self.assertEqual(len(self.fake.log), writes)                # nothing written for it
        r.accept_radio()
        self.assertEqual(r.state()["radioChanged"], "")
        self.key(r)
        r.ptt(False)

    def test_a_move_the_guard_set_back_refuses_the_next_key_up(self):
        r = self.engine()
        r.start()
        self.fake.move("rx_lo", "frequency", 1_254_666_998)
        self.assertTrue(wait_for(lambda: r.state()["radioChanged"], 5, beat=r.heartbeat))
        with self.assertRaisesRegex(TxRefused, "receive LO"):
            r.ptt(True)

    def test_its_own_retunes_and_overs_are_not_changes(self):
        r = self.engine()
        r.start()
        self.key(r)
        r.ptt(False)
        r.set(rxFreq=1251.0e6, txFreq=1251.0e6)
        self.key(r)
        r.ptt(False)
        self.assertEqual(r.state()["radioChanged"], "")


class EngineHackRF(EngineFixture):
    def test_a_busy_hackrf_refuses_transmit_and_receive_carries_on(self):
        plant(marker_path(f"hackrf-{HACKRF_SERIAL}"))
        r = self.make(txDevice="hackrf", allowHardwareTx=True, callsign="GM4BRI", licenceAck=True, licenceClass="full")
        r._tx_device_override = hackrf.HackRFDevice(10e6, {}, lib=FakeLibList())
        r.start()
        sentence = BUSY.format(label="the HackRF 88869dc2439111b")
        self.assertTrue(wait_for(lambda: sentence in r.state()["txRefused"], 3, beat=r.heartbeat))
        st = r.state()
        self.assertTrue(st["running"])
        self.assertEqual(st["radioLock"]["refused"], sentence)
        with self.assertRaises(TxRefused):
            r.ptt(True)

    def test_the_hackrf_says_tx_open_and_not_keyed_then_keyed(self):
        rid = f"hackrf-{HACKRF_SERIAL}"
        r = self.make(txDevice="hackrf", allowHardwareTx=True, callsign="GM4BRI", licenceAck=True, licenceClass="full")
        lib = FakeLibList()
        r._tx_device_override = hackrf.HackRFDevice(10e6, {}, lib=lib)
        r.start()
        self.assertEqual((marker(rid)["use"], marker(rid)["note"]), ("tx", "1249.000 MHz AM-VSB PAL, open, not keyed"))
        r.ptt(True)
        self.assertTrue(wait_for(lambda: marker(rid)["note"] == "1249.000 MHz AM-VSB PAL", 4, beat=r.heartbeat))
        r.ptt(False)
        r.stop()
        self.assertFalse(marker_path(rid).exists())


# ================================================================ the window
from test_ui import HAVE_QT, UiBase  # noqa: E402  (sets up Qt offscreen, as the window tests do)

if HAVE_QT:
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest


class Window(Fixture, UiBase):
    def window(self, **over):
        self.fake = FakeRadio("ad9364")
        self.fake_iio(self.fake, paced=True)
        cfg = dict(allowHardwareTx=True, autoProfile=False)
        cfg.update(over)
        return self.make(device=lambda c, fs: pluto.PlutoDevice(LIBRE, fs, {}), **cfg)

    def click(self, w):
        QTest.mouseClick(w, Qt.MouseButton.LeftButton)

    def test_a_busy_radio_is_named_whole_in_the_refusal_line_and_opened_again_once_free(self):
        plant(marker_path(NANO), radio=NANO)
        win, bridge, radio = self.window()
        panel = win.tx_panel
        sentence = BUSY.format(label="the Pluto at 198.51.100.74")
        self.pump(0.5, lambda: panel.radio_btn.isVisible())
        self.assertTrue(panel.refusal.isVisible())
        self.assertEqual(panel.refusal.text(), "T" + sentence[1:])       # whole, not cut at its first full stop
        self.assertEqual(panel.ptt.blocked, sentence)
        self.assertEqual(panel.radio_btn.text, "Open the radio again")
        self.assertEqual(self.fake.log, [])
        marker_path(NANO).unlink()                                      # closed there
        self.click(panel.radio_btn)
        self.assertTrue(self.pump(3.0, lambda: bridge.st.get("running")))
        self.pump(0.3)
        self.assertFalse(panel.radio_btn.isVisible())
        self.assertEqual(marker(NANO)["app"], "OpenHamShack ATV")

    def test_a_radio_changed_underneath_is_refused_and_accepted_with_one_click(self):
        win, bridge, radio = self.window()
        panel = win.tx_panel
        self.assertTrue(self.pump(3.0, lambda: bridge.st.get("running")))
        self.fake.move("tx", "rf_bandwidth", 12_500_000)
        self.assertFalse(bridge.ptt(True))
        self.assertTrue(self.pump(2.0, lambda: panel.radio_btn.isVisible()))
        self.assertEqual(panel.radio_btn.text, "Accept the radio as it is now")
        self.assertIn("Something other than OpenHamShack ATV has changed this radio", panel.refusal.text())
        self.assertTrue(any("transmit RF bandwidth 10 MHz -> 12.5 MHz" in t for t in win.toasts.texts()))
        self.click(panel.radio_btn)
        self.assertTrue(self.pump(2.0, lambda: not panel.radio_btn.isVisible()))
        self.assertEqual(radio.state()["radioChanged"], "")

    def test_a_lock_on_this_computer_only_is_said_on_the_panel(self):
        self.shell(refuse="ssh: connect to host 198.51.100.74 port 22: No route to host")
        win, bridge, radio = self.window()
        panel = win.tx_panel
        self.assertTrue(self.pump(3.0, lambda: panel.lock_note.isVisible()))
        self.assertTrue(panel.lock_note.text().startswith("▲ The lock on the Pluto at 198.51.100.74 covers this "
                                                          "computer only"), panel.lock_note.text())


if __name__ == "__main__":
    unittest.main()
