"""The radio lock's Windows answers (ohsatv/radiolock.py, ohsatv/host.py),
exercised on any system: the folder is judged before a marker is made and
an untrusted one refuses every radio, a stale marker another account left is
reported with both ways out and never taken, the administrator's clear
removes only a stale one, the installer's check exits 0 or 1, the radio's
shell goes over paramiko where there is no ssh/sshpass, the boot id and
process liveness are asked the Windows way, and the folder is the one every
OpenHamShack program on the PC shares.

Cases ported from OpenHamShack DATV's tests/test_radiolock.py (d3c62d6) with the
module; the ones about this program's engine and command line are this
program's own. The Windows branches are reached by turning host.WINDOWS on
for the call (each function reads it when called), so they run here on
Linux as well as on the Windows build machine."""
import contextlib
import io
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401
from helpers import tmp_dir
from ohsatv import host
from ohsatv import radiolock as rl
from ohsatv.audio import NullAudio
from ohsatv.radio import Radio


def other_marker(**kw):
    m = {"v": 1, "radio": "ad936x-test", "use": "tx", "app": "OpenHamShack DATV", "version": "1", "host": "openhamshack-laptop",
         "bootId": "b", "pid": 1, "user": "x", "since": "2026-10-05T12:36:00Z", "token": "aaaaaaaaaaaaaaaa",
         "note": "437.000 MHz DVB-S2"}
    m.update(kw)
    return m


class FolderTrust(unittest.TestCase):
    def setUp(self):
        self.d = str(tmp_dir(self, "ohsatv-lock-"))

    def test_an_untrusted_folder_refuses_every_radio_and_writes_nothing(self):
        said = "The radio lock's folder X cannot be trusted: it is owned by bob"
        hp = rl.HostPlace("ad936x-test", self.d, check=lambda d: said)
        lock = rl.RadioLock("ad936x-test", "the nano", [hp], beat=60)
        with self.assertRaises(rl.FolderUntrusted) as cm:
            lock.take("tx")
        self.assertEqual(str(cm.exception), said)
        self.assertFalse(lock.taken)
        self.assertEqual(list(Path(self.d).glob("openhamshack-radio.*")), [])

    def test_a_stale_marker_in_an_untrusted_folder_is_not_taken_over_either(self):
        hp = rl.HostPlace("ad936x-test", self.d, check=lambda d: "untrusted")
        Path(hp.path).write_text(json.dumps(other_marker()) + "\n")
        t = time.time() - 600
        os.utime(hp.path, (t, t))
        with self.assertRaises(rl.FolderUntrusted):
            rl.RadioLock("ad936x-test", "the nano", [hp], beat=60).take("tx")
        self.assertEqual(json.loads(Path(hp.path).read_text())["token"], "aaaaaaaaaaaaaaaa")

    def test_the_systems_folder_is_judged_and_a_chosen_one_is_not(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("OHSRADIO_LOCK_DIR", None)
            hp = rl.HostPlace("ad936x-test")
            self.assertEqual(hp.dir, Path(rl.HOST_DIR))
            self.assertIs(hp.check, rl.hostinfo.lock_dir_problem)
            with mock.patch.object(rl.hostinfo, "lock_dir_problem", lambda p: f"bad {p}"):
                self.assertEqual(rl.folder_problem(), f"bad {Path(rl.HOST_DIR)}")
        self.assertIsNone(rl.HostPlace("ad936x-test").check)          # $OHSRADIO_LOCK_DIR: the tests' own
        self.assertEqual(rl.folder_problem(), "")

    def test_the_engine_says_it_once_at_start(self):
        tmp = tmp_dir(self)
        r = Radio(dict(device="sim", audioBackend="null"), conf_path=tmp / "c.json", audio=NullAudio(realtime=False),
                  data_dir=tmp / "data")
        self.addCleanup(r.stop)
        toasts = r.subscribe("toast", 100)
        with mock.patch.object(rl, "folder_problem", lambda: "The radio lock's folder Q cannot be trusted"):
            r.start()
        said = [(t["level"], t["text"]) for t in toasts.drain()]
        self.assertEqual(said.count(("error", "The radio lock's folder Q cannot be trusted")), 1, said)

    def test_the_installers_check_exits_0_or_1(self):
        from ohsatv.__main__ import main
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(["--radio-lock-folder"]), 0)
            with mock.patch.object(rl, "folder_problem", lambda: "cannot be trusted: reasons"):
                self.assertEqual(main(["--radio-lock-folder"]), 1)
        self.assertIn("can be trusted", out.getvalue().splitlines()[0])
        self.assertEqual(out.getvalue().splitlines()[1], "cannot be trusted: reasons")


class StaleOtherAccount(unittest.TestCase):
    """A stale marker another account on this computer left. The sticky
    bit (/run/lock) and the folder's ACL (Windows) let only that account or
    an administrator remove it: the rename that takes it over fails with a
    permission error, stood in for here (one account cannot make another's
    file in a test; DATV's two-account run on VM 130 showed the real one)."""

    def setUp(self):
        self.d = str(tmp_dir(self, "ohsatv-lock-"))
        self.place = rl.HostPlace("ad936x-test", self.d)
        Path(self.place.path).write_text(json.dumps(other_marker(user="bob")) + "\n")
        t = time.time() - 300
        os.utime(self.place.path, (t, t))

    def refused(self, windows: bool):
        with mock.patch.object(rl.os, "rename", side_effect=PermissionError(1, "Operation not permitted")), \
                mock.patch.object(host, "WINDOWS", windows):
            with self.assertRaises(rl.StaleNotOurs) as cm:
                rl.RadioLock("ad936x-test", "libre-2 (198.51.100.74)", [self.place], beat=60).take("tx")
        return str(cm.exception)

    def test_says_whose_since_when_and_both_ways_out_and_leaves_it(self):
        msg = self.refused(windows=False)
        self.assertTrue(msg.startswith("libre-2 (198.51.100.74): a stale lock left by OpenHamShack DATV on openhamshack-laptop "
                                       "(account bob"), msg)
        self.assertIn("belongs to another account on this computer, and only that account or an administrator may "
                      "remove it", msg)
        self.assertIn("sudo ", msg)
        self.assertIn("--clear-stale-lock ad936x-test", msg)
        self.assertTrue(msg.endswith("OpenHamShack ATV has not touched the radio"), msg)
        self.assertEqual(json.loads(Path(self.place.path).read_text())["token"], "aaaaaaaaaaaaaaaa")

    def test_on_windows_the_way_out_is_an_administrator_command_prompt(self):
        msg = self.refused(windows=True)
        self.assertIn("from an administrator command prompt: ", msg)
        self.assertNotIn("sudo", msg)

    def test_the_administrators_clear_removes_it_only_while_stale(self):
        cleared, said = rl.clear_stale("ad936x-test", self.d)
        self.assertTrue(cleared, said)
        self.assertIn("cleared the stale lock on ad936x-test left by OpenHamShack DATV on openhamshack-laptop", said)
        self.assertFalse(Path(self.place.path).exists())
        self.assertFalse(rl.clear_stale("ad936x-test", self.d)[0])        # nothing left to clear

    def test_the_clear_leaves_a_live_lock_alone(self):
        os.utime(self.place.path)
        cleared, said = rl.clear_stale("ad936x-test", self.d)
        self.assertFalse(cleared)
        self.assertIn("is live, not stale", said)
        self.assertTrue(Path(self.place.path).exists())

    def test_the_command_line_clear(self):
        from ohsatv.__main__ import main
        os.replace(self.place.path, Path(os.environ["OHSRADIO_LOCK_DIR"]) / "openhamshack-radio.ad936x-cli.lock")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(["--clear-stale-lock", "ad936x-cli"]), 0)
            self.assertEqual(main(["--clear-stale-lock", "ad936x-cli"]), 1)
        self.assertIn("cleared the stale lock on ad936x-cli", out.getvalue())
        self.assertIn("clear-stale-lock: there is no lock on ad936x-cli", out.getvalue())


class FakeSsh:
    """paramiko.SSHClient as paramiko_runner uses it."""

    def __init__(self, log, rc=0, fail=None):
        self.log, self.rc, self.fail = log, rc, fail

    def __call__(self):
        return self

    def set_missing_host_key_policy(self, p):
        self.log.append(("policy", p))

    def connect(self, host, **kw):
        self.log.append(("connect", host, kw))
        if self.fail:
            raise self.fail

    def exec_command(self, script, timeout=None):
        log, rc = self.log, self.rc

        class Chan:
            def shutdown_write(self):
                log.append(("eof",))

            def recv_exit_status(self):
                return rc

        class In:
            channel = Chan()

            def write(self, data):
                log.append(("stdin", data))

        class Out:
            channel = Chan()

            def __init__(self, text):
                self.text = text

            def read(self):
                return self.text.encode()
        log.append(("exec", script))
        return In(), Out("out"), Out("")

    def close(self):
        self.log.append(("close",))


class WindowsSsh(unittest.TestCase):
    def test_password_goes_to_paramiko_and_keys_are_not_tried(self):
        log = []
        run = rl.paramiko_runner("192.168.2.1", "root", "analog", client_factory=FakeSsh(log, rc=3))
        self.assertEqual(run("echo hi", "marker\n"), (3, "out", ""))
        self.assertEqual([e[0] for e in log], ["connect", "exec", "stdin", "eof", "close"])
        connect = log[0][2]
        self.assertEqual((connect["username"], connect["password"], connect["look_for_keys"]), ("root", "analog", False))

    def test_no_answer_is_rc_255_with_the_reason(self):
        run = rl.paramiko_runner("192.168.2.1", client_factory=FakeSsh([], fail=TimeoutError("timed out")))
        rc, _, err = run("true")
        self.assertEqual(rc, 255)
        self.assertIn("timed out", err)

    def test_windows_uses_paramiko_for_the_radio_side(self):
        with mock.patch.object(host, "WINDOWS", True), \
                mock.patch.object(rl, "paramiko_runner", return_value="runner") as pr:
            self.assertEqual(rl.ssh_runner("192.168.2.1", "root", "analog"), "runner")
        pr.assert_called_once_with("192.168.2.1", "root", "analog", 22, rl.SSH_TIMEOUT_S)


class ThisComputer(unittest.TestCase):
    def test_the_folder_is_the_one_every_openhamshack_program_shares(self):
        with mock.patch.object(host, "WINDOWS", True), \
                mock.patch.dict(os.environ, {"ProgramData": r"D:\PD"}):
            self.assertEqual(host.lock_dir(), Path(r"D:\PD") / "openhamshack-radio")
        with mock.patch.object(host, "WINDOWS", False), mock.patch.object(host, "MACOS", False):
            self.assertEqual(host.lock_dir(), Path("/run/lock"))

    def test_windows_never_signals_a_process_to_ask_whether_it_lives(self):
        # os.kill(pid, 0) is TerminateProcess on Windows: asking would kill
        # the lock's holder. The Windows branch must go to OpenProcess.
        with mock.patch.object(host, "WINDOWS", True), \
                mock.patch.object(host, "_win_pid_alive", return_value=False) as asked, \
                mock.patch.object(host.os, "kill", side_effect=AssertionError("os.kill on Windows")):
            self.assertFalse(host.pid_alive(4321))
        asked.assert_called_once_with(4321)
        self.assertTrue(host.pid_alive("not a pid"))         # unknown counts as alive

    def test_linux_still_asks_the_kernel(self):
        with mock.patch.object(host, "WINDOWS", False):
            self.assertTrue(host.pid_alive(os.getpid()))

    def test_the_boot_id_comes_from_the_registry_on_windows(self):
        class Key:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        fake = type(sys)("winreg")
        fake.HKEY_LOCAL_MACHINE = object()
        fake.OpenKey = lambda root, path: Key()
        fake.QueryValueEx = lambda k, name: (17, 4) if name == "BootId" else (None, 0)
        with mock.patch.object(host, "WINDOWS", True), mock.patch.dict(sys.modules, {"winreg": fake}):
            self.assertEqual(host.boot_id(), "win-17")
        broken = type(sys)("winreg")
        broken.HKEY_LOCAL_MACHINE = object()

        def refuse(*a):
            raise OSError("no such key")
        broken.OpenKey = refuse
        with mock.patch.object(host, "WINDOWS", True), mock.patch.dict(sys.modules, {"winreg": broken}):
            self.assertEqual(host.boot_id(), "")             # unknown: the lock judges by age alone

    def test_the_marker_names_the_windows_user(self):
        env = {k: v for k, v in os.environ.items() if k not in ("USER", "LOGNAME")}
        env["USERNAME"] = "brian"
        with mock.patch.dict(os.environ, env, clear=True):
            m = rl.RadioLock("ad936x-test", "x", [])._marker("rx", "")
        self.assertEqual(m["user"], "brian")
        self.assertEqual(m["app"], "OpenHamShack ATV")


if __name__ == "__main__":
    unittest.main()
