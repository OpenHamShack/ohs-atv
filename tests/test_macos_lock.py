"""The radio lock's folder on a Mac (ohsatv/host.py lock_dir): /private/tmp,
the one folder every account and every OpenHamShack program there shares
and that keeps Linux's root-and-sticky rule, rather than /run/lock, which a
Mac does not have (the "does not exist" warning that refused every shared
radio on macOS), or a per-user folder, which would hide one account's
markers from the next.

Run on Linux: the platform is changed for the call - sys.platform itself
with the module re-read, to prove the detection, and host.MACOS for the
rest, as test_windows_lock.py does with host.WINDOWS."""
import importlib
import os
import stat
import sys
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401
from helpers import tmp_dir
from ohsatv import host, lockacl


def macos():
    return mock.patch.multiple(host, MACOS=True, WINDOWS=False)


class MacFolder(unittest.TestCase):
    def test_a_mac_is_recognised_from_sys_platform(self):
        try:
            with mock.patch.object(sys, "platform", "darwin"):
                importlib.reload(host)
                self.assertTrue(host.macos())
                self.assertFalse(host.windows())
                self.assertEqual(host.lock_dir(), Path("/private/tmp"))
        finally:
            importlib.reload(host)          # back to this machine's answers
        self.assertEqual(host.macos(), sys.platform == "darwin")

    def test_each_system_keeps_its_own_folder(self):
        with macos():
            self.assertEqual(host.lock_dir(), Path("/private/tmp"))
        with mock.patch.multiple(host, MACOS=False, WINDOWS=False):
            self.assertEqual(host.lock_dir(), Path("/run/lock"))
        with mock.patch.multiple(host, MACOS=False, WINDOWS=True), \
                mock.patch.dict(os.environ, {"ProgramData": r"D:\PD"}):
            self.assertEqual(host.lock_dir(), Path(r"D:\PD") / "openhamshack-radio")

    def test_the_macs_folder_as_it_ships_passes_the_root_and_sticky_rule(self):
        # macOS ships /private/tmp as root:wheel drwxrwxrwt, the same rule
        # /run/lock keeps on Debian, so the judge needs no Mac exception.
        self.assertEqual(lockacl.judge_posix(0, stat.S_IFDIR | 0o1777), [])
        self.assertTrue(lockacl.judge_posix(501, stat.S_IFDIR | 0o755))     # a user's own folder is refused

    def test_tmp_itself_would_be_refused_as_a_link(self):
        # /tmp on a Mac is a symlink to /private/tmp; naming it would fail
        # folder_problems' link check on every Mac.
        root = tmp_dir(self, "ohsatv-maclock-")
        real = root / "private-tmp"
        real.mkdir()
        (root / "tmp").symlink_to(real)
        self.assertEqual(lockacl.folder_problems(root / "tmp"),
                         ["it is a link to another folder, not a folder"])

    def test_a_broken_mac_folder_is_explained_in_macs_terms(self):
        with macos():
            why = host.lock_dir_problem(problems=lambda p: ["it is owned by uid 501, not root"])
        self.assertIn("/private/tmp cannot be trusted: it is owned by uid 501, not root", why)
        self.assertIn("sudo chown root:wheel /private/tmp && sudo chmod 1777 /private/tmp", why)
        self.assertNotIn("Debian", why)
        self.assertNotIn("/run/lock", why)


if __name__ == "__main__":
    unittest.main()
