"""Video sources: the cards carry the callsign, and a file plays through
ffmpeg with its sound, the ident laid over it."""
import os
import shutil
import subprocess
import time
import unittest

import numpy as np

from helpers import crop, tmp_dir  # noqa: F401  (crop: path setup)
from ohsatv import sources


class Cards(unittest.TestCase):
    def test_cards_are_static_and_carry_the_callsign(self):
        a = sources.make_source({"source": "testcard", "callsign": "gm4bri"})
        b = sources.make_source({"source": "testcard", "callsign": "m0abc"})
        self.assertTrue(a.static)
        self.assertEqual(a.frame().shape, (576, 720, 3))
        self.assertGreater(np.abs(a.frame() - b.frame()).max(), 0.5)
        bars = sources.make_source({"source": "bars", "callsign": "GM4BRI"})
        self.assertTrue(bars.static)

    def test_missing_file_is_a_sentence_not_a_crash(self):
        s = sources.make_source({"source": "file", "videoFile": "/nonexistent.mp4", "callsign": "GM4BRI"})
        s.start()
        self.assertIn("No video file", s.status())
        s.stop()

    def test_a_live_source_with_no_picture_yet_still_carries_the_ident(self):
        # Before its first frame, or for ever if the camera or file is not
        # there, a live source is black; keyed, that black is sent, and it
        # must still say who is sending (DESIGN.md safety rule 6).
        for cfg in ({"source": "webcam", "webcam": "/nonexistent/video9"},
                    {"source": "file", "videoFile": "/nonexistent.mp4"}):
            s = sources.make_source(dict(cfg, callsign="GM4BRI"))
            s.start()
            f = s.frame()
            s.stop()
            self.assertTrue(s.error())
            box = f[576 - 100:, :300]                                  # bottom left
            self.assertGreater(box.max(), 0.9, cfg)
            self.assertEqual(f[:400].max(), 0.0, cfg)                  # and black elsewhere


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
class IdentRect(unittest.TestCase):
    """Each source says where its ident is (the window keeps its labels off
    it): the box must hold the ident wherever it is drawn."""

    def lit_outside(self, src, x0, y0, x1, y1, region):
        """Lit pixels of `region` ((ys, xs) slices of the 576 x 720
        picture) that lie outside [x0, y0, x1, y1] (fractions)."""
        f = src.frame()
        ys, xs = np.nonzero(f[region].min(axis=2) > 0.8)
        ys = ys + (region[0].start or 0)
        xs = xs + (region[1].start or 0)
        fx, fy = (xs + 0.5) / f.shape[1], (ys + 0.5) / f.shape[0]
        return len(xs), int(np.sum((fx < x0) | (fx > x1) | (fy < y0) | (fy > y1)))

    def test_a_live_sources_box_holds_its_ident_in_every_corner(self):
        for corner in ("bottom-left", "bottom-right", "top-left", "top-right"):
            s = sources.make_source({"source": "webcam", "webcam": "/nonexistent/video9", "callsign": "GM4BRI",
                                     "locator": "IO86", "identText": "23CM TEST STATION", "identPosition": corner})
            r = s.ident_rect()
            # Black but for the ident: every lit pixel is the ident.
            n, out = self.lit_outside(s, *r, (slice(None), slice(None)))
            self.assertGreater(n, 100, corner)
            self.assertEqual(out, 0, corner)
            self.assertEqual(("left" in corner, "top" in corner), (r[2] < 0.5, r[3] < 0.5), corner)

    def test_the_bars_box_holds_the_callsign(self):
        s = sources.make_source({"source": "bars", "callsign": "GM4BRI"})
        # The callsign strip at the foot; the bars above have no white.
        n, out = self.lit_outside(s, *s.ident_rect(), (slice(576 - 96, None), slice(None)))
        self.assertGreater(n, 100)
        self.assertEqual(out, 0)

    def test_the_cards_box_is_its_ident_box(self):
        s = sources.make_source({"source": "testcard", "callsign": "GM4BRI", "locator": "IO86"})
        x0, y0, x1, y1 = s.ident_rect()
        f = s.frame()
        h, w = f.shape[:2]
        # The black box with the callsign on it, and around it, inside the
        # circle, nothing lit that the box leaves out.
        box = f[int(y0 * h) + 4:int(y1 * h) - 4, int(x0 * w) + 4:int(x1 * w) - 4]
        self.assertLess(np.median(box), 0.1)
        n, out = self.lit_outside(s, x0, y0, x1, y1, (slice(int(y0 * h) - 10, int(y1 * h) + 10),
                                                      slice(int(x0 * w) - 18, int(x1 * w) + 18)))
        self.assertGreater(n, 100)
        self.assertEqual(out, 0)


class File(unittest.TestCase):
    def test_a_file_plays_with_sound_and_ident(self):
        d = tmp_dir(self, "ohsatv-src-")
        path = os.path.join(d, "clip.mkv")
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "testsrc2=size=640x360:rate=25:duration=2", "-f", "lavfi", "-i",
                        "sine=frequency=1000:duration=2:sample_rate=48000", "-c:v", "ffv1", "-c:a", "flac",
                        "-shortest", path], check=True)
        s = sources.make_source({"source": "file", "videoFile": path, "callsign": "GM4BRI"})
        s.start()
        try:
            end = time.monotonic() + 5
            while time.monotonic() < end and (s.frames < 12 or not s.has_audio):
                time.sleep(0.05)
            self.assertGreaterEqual(s.frames, 12)
            f1 = s.frame().copy()
            time.sleep(0.3)
            f2 = s.frame()
            self.assertGreater(np.abs(f1 - f2).mean(), 0.001)            # it moves
            # 16:9 is letterboxed: the top rows are black.
            self.assertLess(f2[5, 100:600].max(), 0.05)
            # The ident box (bottom-left) is darkened behind white text.
            box = f2[576 - 60:576 - 30, 30:150]
            self.assertGreater(box.max(), 0.9)
            a = s.audio(4800)
            self.assertEqual(len(a), 4800)
            spec = np.abs(np.fft.rfft(a * np.hanning(len(a))))
            self.assertAlmostEqual(np.argmax(spec) * 48000 / len(a), 1000.0, delta=20)
        finally:
            s.stop()


class Webcams(unittest.TestCase):
    """`sources.list_v4l2` (what list_webcams asks on Linux) against a fake
    /sys/class/video4linux tree - no real device is opened. `probe` is the seam that stands in for
    VIDIOC_QUERYCAP, which only a real device node could answer."""

    def _tree(self, nodes: dict[str, str | None]) -> str:
        """nodes: {"video0": "name text" or None (no name file)}."""
        root = str(tmp_dir(self, "ohsatv-sysfs-"))
        for node, name in nodes.items():
            d = os.path.join(root, node)
            os.makedirs(d)
            if name is not None:
                with open(os.path.join(d, "name"), "w") as f:
                    f.write(name)
        return root

    def test_names_come_from_sysfs_not_the_device(self):
        root = self._tree({"video0": "Integrated Camera: Integrated C\n"})
        found = sources.list_v4l2(root, probe=lambda path: True)
        self.assertEqual(found, [{"path": "/dev/video0", "name": "Integrated Camera: Integrated C"}])

    def test_a_node_the_probe_calls_metadata_only_is_left_out(self):
        root = self._tree({"video0": "USB Camera", "video1": "USB Camera"})
        found = sources.list_v4l2(root, probe=lambda path: path == "/dev/video0")
        self.assertEqual([d["path"] for d in found], ["/dev/video0"])

    def test_nodes_are_kept_when_the_probe_cannot_tell(self):
        # A real capability check answers True/False/"can't tell"; this
        # module's own probe treats "can't tell" as True (DESIGN.md:
        # a convenience filter must never hide a camera it is unsure of).
        root = self._tree({"video0": "Cam A", "video1": "Cam B"})
        found = sources.list_v4l2(root, probe=lambda path: True)
        self.assertEqual([d["path"] for d in found], ["/dev/video0", "/dev/video1"])

    def test_nodes_sort_numerically_not_lexically(self):
        root = self._tree({"video10": "Ten", "video2": "Two", "video1": "One"})
        found = sources.list_v4l2(root, probe=lambda path: True)
        self.assertEqual([d["path"] for d in found], ["/dev/video1", "/dev/video2", "/dev/video10"])

    def test_a_node_with_no_name_file_falls_back_to_the_node_name(self):
        root = self._tree({"video0": None})
        found = sources.list_v4l2(root, probe=lambda path: True)
        self.assertEqual(found, [{"path": "/dev/video0", "name": "video0"}])

    def test_entries_that_are_not_video_nodes_are_ignored(self):
        root = self._tree({"video0": "Cam"})
        os.makedirs(os.path.join(root, "media0"))
        found = sources.list_v4l2(root, probe=lambda path: True)
        self.assertEqual([d["path"] for d in found], ["/dev/video0"])

    def test_no_sysfs_tree_is_an_empty_list_not_an_error(self):
        self.assertEqual(sources.list_v4l2("/nonexistent/path/for/ohsatv-tests", probe=lambda path: True), [])

    def test_real_probe_keeps_a_node_it_cannot_open(self):
        # No fd, no ioctl, nothing to classify - the real probe used in
        # production must not turn that into "skip this camera".
        self.assertTrue(sources._is_capture_node("/nonexistent/ohsatv-test-video-node"))


if __name__ == "__main__":
    unittest.main()
