"""What differs on Windows, exercised on any system (ohsatv/host.py and the
modules that ask it): where config and data live, which ffmpeg and which
libhackrf are used, how cameras are listed and opened, how a file's sound
reaches us without a pipe, what the Pluto and HackRF drivers tell the
operator to do, and that Linux keeps every answer it had.

The Windows branches are reached by turning host.WINDOWS on for the call
(every function reads it when called) and by handing the seams a fake
bundle folder or a fake ffmpeg's output - so each branch runs here, not just
its source text, and on the Windows build machine the same tests run for
real."""
import os
import socket
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

import helpers  # noqa: F401
from helpers import tmp_dir
from ohsatv import config, host, sources, testcard
from ohsatv.devices import hackrf, pluto


def windows(on=True):
    return mock.patch.object(host, "WINDOWS", on)


class Places(unittest.TestCase):
    def test_config_and_data_under_appdata_on_windows(self):
        env = {k: v for k, v in os.environ.items() if k not in ("OHSATV_CONF", "OHSATV_DATA")}
        env["APPDATA"] = r"C:\Users\op\AppData\Roaming"
        with windows(), mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(config.conf_path(), Path(r"C:\Users\op\AppData\Roaming") / "openhamshack-atv" / "config.json")
            self.assertEqual(config.data_dir(), Path(r"C:\Users\op\AppData\Roaming") / "openhamshack-atv" / "data")

    def test_linux_keeps_its_xdg_places(self):
        env = {k: v for k, v in os.environ.items() if k not in ("OHSATV_CONF", "OHSATV_DATA")}
        with windows(False), mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(config.conf_path(), Path.home() / ".config" / "openhamshack-atv" / "config.json")
            self.assertEqual(config.data_dir(), Path.home() / ".local" / "share" / "openhamshack-atv")

    def test_the_environment_still_wins(self):
        with windows(), mock.patch.dict(os.environ, {"OHSATV_CONF": "/x/c.json", "OHSATV_DATA": "/x/d"}):
            self.assertEqual(config.conf_path(), Path("/x/c.json"))
            self.assertEqual(config.data_dir(), Path("/x/d"))


class Bundle(unittest.TestCase):
    """A fake _internal folder stands in for the frozen build's."""

    def setUp(self):
        self.root = tmp_dir(self, "ohsatv-bundle-")

    def put(self, sub, name):
        d = self.root / sub
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_bytes(b"MZ")
        return d / name

    def test_the_bundled_ffmpeg_comes_before_path_on_windows(self):
        exe = self.put("ffmpeg", "ffmpeg.exe")
        with windows(), mock.patch.object(host.shutil, "which", return_value=r"C:\other\ffmpeg.exe"):
            self.assertEqual(host.find_tool("ffmpeg", dirs=[self.root]), str(exe))
            self.assertEqual(host.find_tool("ffprobe", dirs=[self.root]), r"C:\other\ffmpeg.exe")  # not bundled: PATH

    def test_linux_finds_ffmpeg_on_path_only(self):
        self.put("ffmpeg", "ffmpeg.exe")
        with windows(False), mock.patch.object(host.shutil, "which", return_value="/usr/bin/ffmpeg"):
            self.assertEqual(host.find_tool("ffmpeg", dirs=[self.root]), "/usr/bin/ffmpeg")

    def test_libhackrf_by_full_path_from_the_bundle_then_path(self):
        dll = self.put("hackrf", "hackrf-0.dll")
        found = {"hackrf": r"C:\radioconda\Library\bin\hackrf.dll"}
        with windows():
            got = host.hackrf_library(dirs=[self.root], find=lambda n: found.get(n))
        self.assertEqual(got, [str(dll), r"C:\radioconda\Library\bin\hackrf.dll"])

    def test_linux_asks_for_libhackrf_as_it_always_did(self):
        with windows(False):
            self.assertEqual(host.hackrf_library(find=lambda n: None), ["libhackrf.so.0"])
            self.assertEqual(host.hackrf_library(find=lambda n: "libhackrf.so.0.9"), ["libhackrf.so.0.9"])

    def test_the_driver_tries_each_candidate_until_one_loads(self):
        tried = []

        class Lib:
            def __getattr__(self, name):
                f = mock.Mock()
                return f

        def cdll(name):
            tried.append(name)
            if name != "second":
                raise OSError("not here")
            return Lib()
        with mock.patch.object(hackrf.ctypes, "CDLL", cdll):
            self.assertIsNotNone(hackrf.load_lib(["first", "second", "third"]))
            self.assertIsNone(hackrf.load_lib(["nothing"]))
        self.assertEqual(tried, ["first", "second", "nothing"])

    def test_libiio_is_imported_with_the_bundles_folder_first_and_path_restored(self):
        d = self.put("libiio", "libiio.dll").parent
        before = os.environ.get("PATH")
        with windows():
            with host.bundled_libiio(dirs=[self.root]) as got:
                self.assertEqual(got, d)
                self.assertTrue(os.environ["PATH"].startswith(str(d) + os.pathsep))
        self.assertEqual(os.environ.get("PATH"), before)
        with windows(False), host.bundled_libiio(dirs=[self.root]) as got:
            self.assertIsNone(got)

    def test_console_children_get_no_window_on_windows_only(self):
        with windows():
            self.assertEqual(host.no_console(), {"creationflags": 0x08000000})
        with windows(False):
            self.assertEqual(host.no_console(), {})


# What ffmpeg 9 prints for `-list_devices true -f dshow -i dummy` (stderr),
# in the shape the VM's ffmpeg gives it; and ffmpeg 4's older form.
DSHOW_NEW = r'''[dshow @ 000001f2a3b4c5d0] "Integrated Camera" (video)
[dshow @ 000001f2a3b4c5d0]   Alternative name "@device_pnp_\\?\usb#vid_04f2&pid_b6be&mi_00#6&1"
[dshow @ 000001f2a3b4c5d0] "USB Camera" (video)
[dshow @ 000001f2a3b4c5d0]   Alternative name "@device_pnp_\\?\usb#vid_0c45&pid_6366&mi_00#7&a"
[dshow @ 000001f2a3b4c5d0] "USB Camera" (video)
[dshow @ 000001f2a3b4c5d0]   Alternative name "@device_pnp_\\?\usb#vid_0c45&pid_6366&mi_00#7&b"
[dshow @ 000001f2a3b4c5d0] "Cam: front" (video)
[dshow @ 000001f2a3b4c5d0]   Alternative name "@device_pnp_\\?\usb#vid_1111&pid_2222&mi_00#8&c"
[dshow @ 000001f2a3b4c5d0] "Microphone (Realtek(R) Audio)" (audio)
[dshow @ 000001f2a3b4c5d0]   Alternative name "@device_cm_{33D9A762-90C8-11D0-BD43-00A0C911CE86}\wave_{1}"
[in#0 @ 000001f2a3b4c6e0] Error opening input: Immediate exit requested
Error opening input file dummy.
'''
DSHOW_OLD = r'''[dshow @ 0000000000a1b2c3] DirectShow video devices (some may be both video and audio devices)
[dshow @ 0000000000a1b2c3]  "HD Webcam"
[dshow @ 0000000000a1b2c3]     Alternative name "@device_pnp_\\?\usb#vid_1bcf&pid_2284"
[dshow @ 0000000000a1b2c3] DirectShow audio devices
[dshow @ 0000000000a1b2c3]  "Microphone (HD Webcam)"
[dshow @ 0000000000a1b2c3]     Alternative name "@device_cm_{33D9A762}\wave_{2}"
dummy: Immediate exit requested
'''


class Cameras(unittest.TestCase):
    def test_dshow_video_devices_by_name_audio_left_out(self):
        cams = sources.parse_dshow(DSHOW_NEW)
        self.assertEqual([c["name"] for c in cams], ["Integrated Camera", "USB Camera", "USB Camera", "Cam: front"])
        self.assertEqual(cams[0]["path"], "Integrated Camera")

    def test_two_of_one_model_and_a_colon_take_the_alternative_name(self):
        cams = sources.parse_dshow(DSHOW_NEW)
        self.assertEqual(cams[1]["path"], r"@device_pnp_\\?\usb#vid_0c45&pid_6366&mi_00#7&a")
        self.assertEqual(cams[2]["path"], r"@device_pnp_\\?\usb#vid_0c45&pid_6366&mi_00#7&b")
        self.assertEqual(cams[3]["path"], r"@device_pnp_\\?\usb#vid_1111&pid_2222&mi_00#8&c")

    def test_older_ffmpegs_headed_list(self):
        self.assertEqual(sources.parse_dshow(DSHOW_OLD), [{"path": "HD Webcam", "name": "HD Webcam"}])

    def test_no_camera_and_no_ffmpeg_are_empty_lists(self):
        # What ffmpeg 9.0.2 printed on the build VM, which has no camera.
        self.assertEqual(sources.parse_dshow(
            "[in#0 @ 000001f076dd4b00] Could not enumerate video devices (or none found).\n"
            "[in#0 @ 000001f076dd4b00] Could not enumerate audio only devices (or none found).\n"
            "Error opening input file dummy.\n"), [])
        with windows(), mock.patch.object(sources, "ffmpeg_path", return_value=None):
            self.assertEqual(sources.list_webcams(), [])

    def test_windows_asks_ffmpeg_and_linux_reads_sysfs(self):
        asked = []

        def run(cmd):
            asked.append(cmd)
            return DSHOW_OLD
        with windows(), mock.patch.object(sources, "ffmpeg_path", return_value=r"C:\x\ffmpeg.exe"):
            self.assertEqual(sources.list_webcams(run=run)[0]["name"], "HD Webcam")
        self.assertEqual(asked, [[r"C:\x\ffmpeg.exe", "-hide_banner", "-list_devices", "true", "-f", "dshow",
                                  "-i", "dummy"]])
        with windows(False):
            self.assertEqual(sources.list_webcams("/nonexistent/ohsatv", run=run), [])
        self.assertEqual(len(asked), 1)

    def ident(self):
        return testcard.Ident("GM4BRI", "", "bottom-left")

    def test_the_webcam_is_opened_through_dshow_by_name(self):
        s = sources.FfmpegSource("webcam", "Integrated Camera", self.ident())
        with windows(), mock.patch.object(sources, "ffmpeg_path", return_value="ffmpeg"):
            cmd = s._cmd(None)
        i = cmd.index("-f")
        self.assertEqual(cmd[i:i + 2], ["-f", "dshow"])
        self.assertEqual(cmd[cmd.index("-i") + 1], "video=Integrated Camera")
        with windows(False), mock.patch.object(sources, "ffmpeg_path", return_value="ffmpeg"):
            cmd = sources.FfmpegSource("webcam", "/dev/video0", self.ident())._cmd(None)
        i = cmd.index("-f")
        self.assertEqual(cmd[i:i + 4], ["-f", "v4l2", "-i", "/dev/video0"])

    def test_blank_means_the_first_camera_and_a_missing_one_is_a_sentence(self):
        cams = sources.parse_dshow(DSHOW_NEW)
        with windows(), mock.patch.object(sources, "list_dshow", return_value=cams):
            s = sources.FfmpegSource("webcam", "", self.ident())
            self.assertEqual(s._find_camera(), "")
            self.assertEqual(s.path, "Integrated Camera")
            s = sources.FfmpegSource("webcam", "/dev/video0", self.ident())   # a config from Linux
            self.assertEqual(s._find_camera(), "")
            self.assertEqual(s.path, "Integrated Camera")
            s = sources.FfmpegSource("webcam", "Lost Cam", self.ident())
            self.assertIn("No webcam called 'Lost Cam'", s._find_camera())
            s = sources.FfmpegSource("webcam", "USB Camera", self.ident())        # by name, though its path is the alt
            self.assertEqual(s._find_camera(), "")
        with windows(), mock.patch.object(sources, "list_dshow", return_value=[]):
            self.assertIn("No camera found", sources.FfmpegSource("webcam", "", self.ident())._find_camera())

    def test_without_ffmpeg_the_sentence_fits_the_system(self):
        for on, want, unwanted in ((True, "reinstall OpenHamShack ATV", "apt"), (False, "sudo apt install ffmpeg", "reinstall")):
            s = sources.FfmpegSource("webcam", "", self.ident())
            with windows(on), mock.patch.object(sources, "ffmpeg_path", return_value=None):
                s.start()
            self.assertIn(want, s.error())
            self.assertNotIn(unwanted, s.error())

    def test_the_default_setting_per_system(self):
        import importlib
        from ohsatv import state
        try:
            with windows():
                importlib.reload(state)
                self.assertEqual(state.WEBCAM_DEFAULT, "")
                self.assertEqual(state.SPEC["webcam"]["default"], "")
        finally:
            importlib.reload(state)
        self.assertEqual(state.WEBCAM_DEFAULT, "" if sys.platform == "win32" else "/dev/video0")


class FileSound(unittest.TestCase):
    """The file's sound on Windows comes over a loopback socket. Here a
    client stands in for ffmpeg and writes what ffmpeg would."""

    def test_the_socket_carries_the_sound_into_the_ring_and_ends_with_it(self):
        s = sources.FfmpegSource("file", "", testcard.Ident("GM4BRI", "", "bottom-left"))
        listener = sources.FileAudioTcp()
        self.assertTrue(listener.url.startswith("tcp://127.0.0.1:"))
        port = int(listener.url.rsplit(":", 1)[1])
        tone = np.sin(2 * np.pi * 1000 * np.arange(9600) / 48000).astype(np.float32)
        lr = np.repeat(tone, 2)                        # interleaved stereo, as -ac 2 f32le gives

        def ffmpeg():
            # In pieces that end part-way through a sample, as a socket may
            # deliver them: each must join the next, not be cut off.
            data = lr.tobytes()
            with socket.create_connection(("127.0.0.1", port)) as c:
                c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                for i in range(0, len(data), 1003):
                    c.sendall(data[i:i + 1003])
                    time.sleep(0.001)
        t = threading.Thread(target=ffmpeg)
        t.start()
        ev = threading.Event()
        s._read_audio(listener, ev)                    # returns when the "ffmpeg" closes its end
        t.join()
        self.assertTrue(s.has_audio)
        got = s.audio_stereo(4800)
        self.assertEqual(got.shape, (4800, 2))
        np.testing.assert_allclose(got[:, 0], tone[:4800], atol=1e-6)

    def test_only_loopback_is_listened_on(self):
        listener = sources.FileAudioTcp()
        try:
            self.assertEqual(listener.srv.getsockname()[0], "127.0.0.1")
        finally:
            listener.close()

    def test_a_closed_listener_ends_the_reader_at_once(self):
        listener = sources.FileAudioTcp()
        listener.close()
        self.assertEqual(listener.read(100), b"")

    def test_windows_asks_ffmpeg_for_tcp_and_linux_for_a_pipe(self):
        s = sources.FfmpegSource("file", "clip.mkv", testcard.Ident("GM4BRI", "", "bottom-left"))
        with mock.patch.object(sources, "ffmpeg_path", return_value="ffmpeg"):
            cmd = s._cmd("tcp://127.0.0.1:5000")
        self.assertEqual(cmd[-2:], ["f32le", "tcp://127.0.0.1:5000"])
        started = {}

        class Proc:
            stdout = stderr = None

            def __init__(self, cmd, **kw):
                started["cmd"], started["kw"] = cmd, kw
                raise OSError("stop here")
        path = tmp_dir(self) / "clip.mkv"
        path.write_bytes(b"")
        s = sources.FfmpegSource("file", str(path), testcard.Ident("GM4BRI", "", "bottom-left"))
        with windows(), mock.patch.object(sources, "ffmpeg_path", return_value="ffmpeg"), \
                mock.patch.object(sources, "has_audio", return_value=True), \
                mock.patch.object(sources.subprocess, "Popen", Proc):
            s.start()
        self.assertTrue(started["cmd"][-1].startswith("tcp://127.0.0.1:"), started["cmd"])
        self.assertEqual(started["kw"]["pass_fds"], ())
        self.assertEqual(started["kw"]["creationflags"], 0x08000000)
        self.assertIn("ffmpeg would not start", s.error())
        with windows(False), mock.patch.object(sources, "ffmpeg_path", return_value="ffmpeg"), \
                mock.patch.object(sources, "has_audio", return_value=True), \
                mock.patch.object(sources.subprocess, "Popen", Proc):
            s.start()
        self.assertTrue(started["cmd"][-1].startswith("pipe:"), started["cmd"])
        self.assertEqual(len(started["kw"]["pass_fds"]), 1)
        self.assertNotIn("creationflags", started["kw"])


class Sentences(unittest.TestCase):
    def test_pluto_install_and_open_sentences_fit_the_system(self):
        with windows(), mock.patch.dict(sys.modules, {"iio": None}):
            with self.assertRaises(pluto.DeviceError) as cm:
                pluto.import_iio()
        self.assertIn("reinstall OpenHamShack ATV", str(cm.exception))
        self.assertNotIn("apt", str(cm.exception))
        with windows(False), mock.patch.dict(sys.modules, {"iio": None}):
            with self.assertRaises(pluto.DeviceError) as cm:
                pluto.import_iio()
        self.assertIn("sudo apt install python3-libiio", str(cm.exception))

    def test_a_pluto_that_will_not_open_points_at_find_and_the_drivers_on_windows(self):
        class Iio:
            class Context:
                def __init__(self, uri):
                    raise OSError(0, "No error")
        dev = pluto.PlutoDevice("usb:", fs=10e6)
        for on in (True, False):
            with windows(on), mock.patch.object(pluto, "import_iio", return_value=Iio):
                with self.assertRaises(pluto.DeviceError) as cm:
                    dev.open()
            msg = str(cm.exception)
            self.assertTrue(msg.startswith("No Pluto at usb:. "), msg)      # errno 0 is not repeated
            if on:
                self.assertIn("Find in Settings > Radio", msg)
                self.assertIn("PlutoSDR drivers", msg)
            else:
                self.assertIn("iio_info -s", msg)
                self.assertNotIn("drivers", msg)

    def test_a_failed_scan_never_tells_windows_to_install_avahi(self):
        e = OSError(26, "Text file busy")
        with windows(), mock.patch.object(pluto, "_avahi_running", return_value=False):
            self.assertNotIn("avahi", pluto._scan_failed_note(e))
        with windows(False), mock.patch.object(pluto, "_avahi_running", return_value=False):
            self.assertIn("sudo apt install avahi-daemon", pluto._scan_failed_note(e))
        with windows(False), mock.patch.object(pluto, "_avahi_running", return_value=True):
            self.assertEqual(pluto._scan_failed_note(e),
                             "Full device scan failed ([Errno 26] Text file busy); fell back to a USB/IP-only search.")

    def test_windows_falls_back_to_libiio_and_known_addresses_without_sysfs(self):
        # No sysfs, no fcntl: each Linux-only probe says "not known", and
        # discovery is left with libiio's own scan and the direct addresses.
        self.assertIsNone(pluto.usb_net_state("usb:1.5.5", root="/nonexistent/ohsatv"))
        self.assertIsNone(pluto.resolve_usb("usb:", "abc", object(), root="/nonexistent/ohsatv"))
        self.assertIn("ip:192.168.2.1", pluto._direct_candidates())
        with mock.patch.dict(sys.modules, {"fcntl": None}):
            self.assertEqual(pluto._usb_network_addresses(), [])
            self.assertIsNone(pluto._iface_ipv4("eth0"))

    def test_hackrf_sentences_fit_the_system(self):
        for on, want, unwanted in ((True, "hackrf-0.dll", "apt"), (False, "sudo apt install libhackrf0", "reinstall")):
            notes = []
            with windows(on), mock.patch.object(hackrf, "load_lib", return_value=None):
                hackrf.discover(notes=notes)
                d = hackrf.HackRFDevice(10e6, {}, lib=None)
                with self.assertRaises(hackrf.DeviceError) as cm:
                    d.open()
            for said in (notes[0], str(cm.exception)):
                self.assertIn(want, said)
                self.assertNotIn(unwanted, said)


class Console(unittest.TestCase):
    def test_a_console_that_cannot_print_a_character_prints_a_question_mark(self):
        import io

        from ohsatv.__main__ import _printable_console
        raw = io.BytesIO()
        out = io.TextIOWrapper(raw, encoding="cp437")
        with mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", out):
            _printable_console()
            print("Settings › Radio")
            out.flush()
        self.assertEqual(raw.getvalue().decode("cp437").strip(), "Settings ? Radio")


if __name__ == "__main__":
    unittest.main()
