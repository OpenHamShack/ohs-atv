"""The radio lock (OpenHamShack DATV's docs/radio-lock.md, ohsatv/radiolock.py): one
taker wins, a fresh marker refuses with the spec's sentence, a stale one is
taken over by each of the spec's rules, heartbeats keep it and notice when it
is lost, and release removes only our own. Every case runs against both
places: the host directory, and the over-ssh shell scripts run for real
under `sh -c` on a temporary path (the radio's side of the protocol, not an
imitation).

Copied from openhamshack-datv 7daa15e (tests/test_radiolock.py, unchanged there
since 734c7e8), as the module is. Left out: DATV's MiniTiouner cases, which
test its longmynd receiver, not the lock. OpenHamShack ATV's own use of the lock
- the Pluto, the HackRF, the engine and the window - is tested in
test_shared_radio.py."""
import json
import os
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path

import helpers  # noqa: F401
from helpers import tmp_dir
from ohsatv import radiolock as rl


def host_place(d):
    return rl.HostPlace("ad936x-test", d)


def shell_place(d):
    return rl.ShellPlace(rl.local_runner, "the test radio's shell", str(Path(d) / "openhamshack-radio.lock"))


def other_marker(**kw):
    m = {"v": 1, "radio": "ad936x-test", "use": "tx", "app": "OpenHamShack ATV", "version": "1", "host": "openhamshack-laptop",
         "bootId": "b", "pid": 1, "user": "x", "since": "2026-10-05T12:36:00Z", "token": "aaaaaaaaaaaaaaaa",
         "note": "1251.000 MHz FM PAL"}
    m.update(kw)
    return m


class Places(unittest.TestCase):
    def setUp(self):
        self.d = str(tmp_dir(self, "ohsatv-lock-"))

    def each(self):
        # The radio's side is a POSIX sh script; Windows has no sh to run it
        # locally, and there it is only ever run on the radio (over paramiko,
        # test_windows_lock.WindowsSsh), so the host side alone is tested.
        for make in (host_place,) if sys.platform == "win32" else (host_place, shell_place):
            with self.subTest(place=make.__name__):
                d = tempfile.mkdtemp(dir=self.d)
                yield make(d), d

    def plant(self, place, marker, age=0.0):
        """Another program's marker, `age` seconds old."""
        path = Path(place.path)
        path.write_text(json.dumps(marker) + "\n")
        t = time.time() - age
        os.utime(path, (t, t))

    def test_one_taker_wins_and_the_second_is_refused_with_who_has_it(self):
        for place, _ in self.each():
            a = rl.RadioLock("ad936x-test", "the nano", [place], app="OpenHamShack DATV", beat=60)
            b = rl.RadioLock("ad936x-test", "the nano", [place], app="OpenHamShack DATV", beat=60)
            a.take("tx", "437.000 MHz DVB-S2")
            with self.assertRaises(rl.RadioBusy) as cm:
                b.take("rx", "")
            msg = str(cm.exception)
            self.assertIn("the nano is in use by OpenHamShack DATV on " + socket.gethostname(), msg)
            self.assertIn("transmitting", msg)
            self.assertIn('"437.000 MHz DVB-S2"', msg)
            self.assertIn("has not touched the radio", msg)
            a.release()
            b.take("rx", "")                                   # free once given back
            b.release()
            self.assertFalse(Path(place.path).exists())

    def test_a_fresh_marker_from_another_computer_is_busy(self):
        for place, _ in self.each():
            self.plant(place, other_marker(), age=5)
            lock = rl.RadioLock("ad936x-test", "libre-2 (198.51.100.74)", [place], beat=60)
            with self.assertRaises(rl.RadioBusy) as cm:
                lock.take("tx")
            self.assertIn("libre-2 (198.51.100.74) is in use by OpenHamShack ATV on openhamshack-laptop (transmitting, since "
                          "12:36 UTC, last heard 5 s ago", str(cm.exception))
            self.assertEqual(json.loads(Path(place.path).read_text())["token"], "aaaaaaaaaaaaaaaa")

    def test_a_marker_older_than_the_ttl_is_taken_over_and_said(self):
        for place, _ in self.each():
            self.plant(place, other_marker(), age=rl.TTL_S + 30)
            lock = rl.RadioLock("ad936x-test", "libre-2", [place], beat=60)
            notes = lock.take("tx", "ours")
            self.assertTrue(any("Took over a stale lock on libre-2 left by OpenHamShack ATV on openhamshack-laptop" in n
                                for n in notes), notes)
            self.assertEqual(json.loads(Path(place.path).read_text())["note"], "ours")
            lock.release()

    def test_a_dead_holder_on_this_computer_is_stale_at_once(self):
        for place, _ in self.each():
            dead = 2 ** 22 + 12345                  # above pid_max's default: no such process
            self.plant(place, other_marker(host=socket.gethostname(), bootId=rl.boot_id(), pid=dead), age=1)
            lock = rl.RadioLock("ad936x-test", "the nano", [place], beat=60)
            notes = lock.take("rx")
            self.assertTrue(any("is no longer running" in n for n in notes), notes)
            lock.release()

    def test_a_live_holder_on_this_computer_is_busy(self):
        for place, _ in self.each():
            self.plant(place, other_marker(host=socket.gethostname(), bootId=rl.boot_id(), pid=os.getppid()), age=1)
            with self.assertRaises(rl.RadioBusy):
                rl.RadioLock("ad936x-test", "the nano", [place], beat=60).take("rx")

    def test_heartbeat_touches_and_notices_a_lost_lock(self):
        for place, _ in self.each():
            lock = rl.RadioLock("ad936x-test", "the nano", [place], beat=60)
            lock.take("tx")
            t = time.time() - 40
            os.utime(place.path, (t, t))
            self.assertEqual(lock.beat(), "")
            self.assertLess(time.time() - Path(place.path).stat().st_mtime, 5)
            self.plant(place, other_marker(), age=0)          # somebody removed ours and took it
            why = lock.beat()
            self.assertIn("no longer this program's", why)
            self.assertIn("OpenHamShack ATV", why)
            lock.release()                                    # must not remove theirs
            self.assertEqual(json.loads(Path(place.path).read_text())["app"], "OpenHamShack ATV")

    def test_heartbeat_thread_reports_loss(self):
        place = host_place(self.d)
        lost = []
        lock = rl.RadioLock("ad936x-test", "the nano", [place], beat=0.05, on_lost=lost.append)
        lock.take("tx")
        Path(place.path).unlink()
        self.assertTrue(helpers.wait_for(lambda: lost, 3))
        self.assertIn("it was removed", lost[0])
        lock.release()

    def test_set_use_rewrites_keeping_the_token(self):
        for place, _ in self.each():
            lock = rl.RadioLock("ad936x-test", "the nano", [place], beat=60)
            lock.take("rx", "receiving 437")
            token = json.loads(Path(place.path).read_text())["token"]
            lock.set_use("tx", "keyed 437")
            m = json.loads(Path(place.path).read_text())
            self.assertEqual((m["use"], m["note"], m["token"]), ("tx", "keyed 437", token))
            lock.release()

    @unittest.skipIf(sys.platform == "win32", "the radio's side is a POSIX sh script, run only on the radio there")
    def test_a_refusal_at_one_place_gives_back_the_other(self):
        d1, d2 = tempfile.mkdtemp(dir=self.d), tempfile.mkdtemp(dir=self.d)
        radio, host = shell_place(d1), host_place(d2)
        self.plant(host, other_marker(), age=1)
        lock = rl.RadioLock("ad936x-test", "the nano", [radio, host], beat=60)
        with self.assertRaises(rl.RadioBusy):
            lock.take("tx")
        self.assertFalse(Path(radio.path).exists(), "the radio's marker was left behind after a refusal")

    def test_an_unreachable_radio_shell_is_said_not_ignored(self):
        def refused(script, data=""):
            return 255, "", "ssh: connect to host 198.51.100.74 port 22: Connection refused"
        radio = rl.ShellPlace(refused, "the radio's shell (ssh root@198.51.100.74)")
        lock = rl.RadioLock("net-198.51.100.74", "libre-2", [radio, host_place(self.d)], beat=60)
        notes = lock.take("tx")
        self.assertTrue(any("covers this computer only" in n and "Connection refused" in n for n in notes), notes)
        self.assertEqual(lock.describe()["where"], ["this computer"])
        lock.release()

    def test_an_unreadable_young_marker_is_busy_and_an_old_one_stale(self):
        for place, _ in self.each():
            Path(place.path).write_text("{half writ")
            with self.assertRaises(rl.RadioBusy):
                rl.RadioLock("ad936x-test", "the nano", [place], beat=60).take("tx")
            t = time.time() - rl.TTL_S - 5
            os.utime(place.path, (t, t))
            lock = rl.RadioLock("ad936x-test", "the nano", [place], beat=60)
            notes = lock.take("tx")
            self.assertTrue(notes and "unreadable" in notes[0], notes)
            lock.release()


class Ids(unittest.TestCase):
    def test_serial_first_then_address(self):
        self.assertEqual(rl.radio_id("ad936x", "1044730a19970/0D"), "ad936x-1044730a19970-0d")
        self.assertEqual(rl.radio_id("ad936x", None, "ip:192.168.2.1"), "net-192.168.2.1")
        self.assertEqual(rl.radio_id("dl1jm", None, "198.51.100.74"), "net-198.51.100.74")

    def test_places_follow_the_settings(self):
        os.environ["OHSATV_RADIO_SSH"] = "1"
        try:
            ps = rl.places_for("net-198.51.100.74", "198.51.100.74", {"radioLockSsh": True})
            self.assertEqual([type(p).__name__ for p in ps], ["ShellPlace", "HostPlace"])
            ps = rl.places_for("net-198.51.100.74", "198.51.100.74", {"radioLockSsh": False})
            self.assertEqual([type(p).__name__ for p in ps], ["HostPlace"])
            ps = rl.places_for("ftdi-x", None, {"radioLockSsh": True})
            self.assertEqual([type(p).__name__ for p in ps], ["HostPlace"])
        finally:
            os.environ["OHSATV_RADIO_SSH"] = "0"


if __name__ == "__main__":
    unittest.main()
