"""The tests leave nothing in the temporary directory.

On 6 October 2026 shack's /tmp (31 GB, tmpfs) was found full: one run of
this suite left about 350 directories behind, 21,000 had piled up, and a
radio's data directory can hold recordings. Each test now takes its
directories from helpers.tmp_dir, which removes them however the test ends.
This runs a sample of the tests that write to disk - a radio and its data
directory, a recorder, lock markers on this computer and on a radio's
shell, a teletext folder being edited, a sysfs tree, the window - plus one
that fails on purpose, in a process of their own with TMPDIR pointed at an
empty directory, and looks in it afterwards. A sample rather than the whole
suite, which takes many minutes; a new test that writes to disk without
tmp_dir is found by the next look at /tmp, and this keeps the habit honest.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

import helpers  # noqa: F401 - puts the repo on sys.path

HERE = os.path.dirname(os.path.abspath(__file__))

SAMPLE = [
    "test_radio.Lifecycle.test_constructing_opens_and_keys_nothing",
    "test_iqrec.Recorder.test_cf32_round_trip_with_metadata",
    # Not all of Places: some of its tests assert ages in whole seconds
    # ("last heard 5 s ago"), and one failed in here once during a full run
    # (not reproduced alone, so most likely the clock under load). This
    # test is about what is left in TMPDIR, not about that.
    "test_radiolock.Places.test_one_taker_wins_and_the_second_is_refused_with_who_has_it",
    "test_radiolock.Places.test_a_refusal_at_one_place_gives_back_the_other",
    "test_shared_radio.PlutoShell.test_the_radios_own_shell_carries_the_marker_too",
    "test_teletext.LiveEdits.test_changes_added_removed_and_settling",
    "test_sources.Webcams",
    "test_devices_hackrf.EngineFilter.test_the_filter_is_set_before_the_gain_rises",
    "test_ui.Window.test_a_closed_window_is_destroyed_on_the_gui_thread",
]

# Run in the child: the sample, and a test that fails with a file in its
# directory - cleanup has to happen on the way out of a failure too, which
# is when a directory used to be left for someone to look at and never was.
DRIVER = r"""
import json, sys, unittest
from helpers import tmp_dir

class Fails(unittest.TestCase):
    def test_fails_with_a_file_in_its_directory(self):
        (tmp_dir(self) / "left.bin").write_bytes(bytes(1024))
        self.fail("on purpose")

suite = unittest.defaultTestLoader.loadTestsFromNames(sys.argv[1:])
suite.addTest(Fails("test_fails_with_a_file_in_its_directory"))
res = unittest.TextTestRunner(stream=sys.stderr, verbosity=0).run(suite)
print(json.dumps({"run": res.testsRun,
                  "failed": [t.id() for t, _ in res.failures],
                  "why": [e for t, e in res.failures if not t.id().startswith("__main__.")],
                  "errors": [t.id() + ": " + e for t, e in res.errors]}))
"""


class NothingLeftBehind(unittest.TestCase):
    def test_a_sample_of_the_suite_leaves_its_tmpdir_empty(self):
        with tempfile.TemporaryDirectory(prefix="ohsatv-hygiene-") as tmp:
            env = dict(os.environ, TMPDIR=tmp)
            p = subprocess.run([sys.executable, "-c", DRIVER, *SAMPLE], cwd=HERE, env=env,
                               capture_output=True, text=True, timeout=300)
            self.assertEqual(p.returncode, 0, p.stderr[-3000:])
            got = json.loads(p.stdout.strip().splitlines()[-1])
            # The sample ran, and only the deliberate failure failed: a
            # sample that errored early would leave nothing for a dull reason.
            self.assertEqual(got["failed"], ["__main__.Fails.test_fails_with_a_file_in_its_directory"],
                             "\n".join(got["why"]))
            self.assertEqual(got["errors"], [])
            self.assertGreater(got["run"], len(SAMPLE))
            left = sorted(os.listdir(tmp))
            self.assertEqual(left, [], f"left in TMPDIR by the sample: {left}")


if __name__ == "__main__":
    unittest.main()
