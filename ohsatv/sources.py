"""What is transmitted: the built-in cards, a webcam, a video file.

Webcam and file go through the `ffmpeg` program in a subprocess, writing raw
RGB frames to a pipe (and, for a file, 48 kHz float audio to a second one).
Debian 13 packages python3-av, but the ffmpeg program is already on any
machine that plays video, a pipe needs no binding to match the library's
ABI, and a decoder crash takes down a child process rather than the radio.
ffmpeg does the scaling to the 720 x 576 picture (letterboxing anything
that is not 4:3, through square pixels so circles stay round) and `-re`
paces a file at its own frame rate.

Every source exposes the latest frame; the transmitter takes whatever is
newest when it needs a frame, so a source that stalls freezes the picture
rather than the transmitter. `static` sources never change, which lets the
transmitter generate them once as a 0.16 s loop.

The ident: the test card and the bars draw the callsign themselves; a live
picture gets testcard.Ident laid over every frame. It cannot be turned off
(DESIGN.md safety rule 6).

`list_webcams()` finds V4L2 capture nodes for Settings › Sources: it reads
the kernel's own name for each /dev/videoN from sysfs rather than opening
every one, and leaves out a camera's metadata-only node where VIDIOC_QUERYCAP
says so plainly.

Windows (ohsatv/host.py has the rest of what differs there) has neither
V4L2 nor sysfs. A camera is a DirectShow device, named by its friendly name
("Integrated Camera"): ffmpeg lists them itself (`-list_devices true -f
dshow`), which is what list_webcams() asks there, and reads one with `-f
dshow -i video=<name>` - so the `webcam` setting holds a name, not a path,
and its default is "" for "the first camera found". ffmpeg is the copy the
installer carries (host.find_tool), found before any on PATH.

The file's sound reaches us on a second pipe, ffmpeg's `pipe:<fd>`, on
Linux. Windows cannot pass a pipe to a child that way (subprocess has no
pass_fds there, and a child's C runtime numbers its descriptors afresh),
so there ffmpeg sends the sound to a TCP socket this process listens on,
on 127.0.0.1 only and for the one connection ffmpeg makes (FileAudioTcp).
Rejected: a second ffmpeg for the sound alone (two -re clocks would drift
apart over a long clip) and a named pipe (_winapi is CPython's private
module, and nothing else here needs it).
"""
from __future__ import annotations

import logging
import os
import re
import socket
import subprocess
import threading
import time

import numpy as np

from . import host, state, testcard
from .audio import Ring

log = logging.getLogger("ohsatv.sources")
W, H = 720, 576
FRAME_BYTES = W * H * 3
# Letterbox through square pixels: fit 768 x 576 (the 4:3 display), pad,
# then squeeze to the 720-sample picture line.
VF = ("scale=768:576:force_original_aspect_ratio=decrease,pad=768:576:(ow-iw)/2:(oh-ih)/2,setsar=1,"
      "scale=720:576,fps=25")


def ffmpeg_path() -> str | None:
    return host.find_tool("ffmpeg")


def ffprobe_path() -> str | None:
    return host.find_tool("ffprobe")


SYSFS_V4L2 = "/sys/class/video4linux"
VIDIOC_QUERYCAP = 0x80685600
V4L2_CAP_VIDEO_CAPTURE = 0x00000001
V4L2_CAP_META_CAPTURE = 0x00800000
V4L2_CAP_DEVICE_CAPS = 0x80000000


def _is_capture_node(path: str) -> bool:
    """False only when VIDIOC_QUERYCAP plainly says this node is
    metadata-only - the second /dev/videoN a UVC camera has exposed
    alongside its capture node since Linux 4.16 (ffmpeg's own v4l2 input
    skips it the same way). Anything that cannot be opened or queried -
    permission denied, no such ioctl, the node gone - is kept: a
    convenience filter must never be the reason a real camera vanishes
    from the picker."""
    import struct
    try:
        import fcntl
    except ImportError:              # not Linux: nothing to ask, so nothing is left out
        return True
    try:
        fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
    except OSError:
        return True
    try:
        buf = bytearray(104)
        fcntl.ioctl(fd, VIDIOC_QUERYCAP, buf, True)
    except OSError:
        return True
    finally:
        os.close(fd)
    capabilities, device_caps = struct.unpack_from("<II", buf, 16 + 32 + 32 + 4)
    caps = device_caps if capabilities & V4L2_CAP_DEVICE_CAPS else capabilities
    if caps & V4L2_CAP_VIDEO_CAPTURE:
        return True
    if caps & V4L2_CAP_META_CAPTURE:
        return False
    return True


def list_v4l2(sysfs_root: str = SYSFS_V4L2, probe=_is_capture_node) -> list[dict]:
    """Capture-capable V4L2 nodes as [{"path": "/dev/videoN", "name": ...}],
    in node order. The name comes from sysfs rather than from opening the
    device, so an unplugged or busy camera still shows its last-known name
    (Settings keeps the current path even when nothing answers it, below).
    `probe` is a seam for tests: real capability checking needs an open()
    and an ioctl a test must not depend on, so it is passed in rather than
    reached for directly."""
    try:
        names = sorted((e for e in os.listdir(sysfs_root) if e.startswith("video")),
                       key=lambda e: int(e[5:]) if e[5:].isdigit() else e)
    except OSError:
        return []
    out = []
    for entry in names:
        dev = f"/dev/{entry}"
        if not probe(dev):
            continue
        try:
            with open(os.path.join(sysfs_root, entry, "name")) as f:
                name = f.read().strip()
        except OSError:
            name = ""
        out.append({"path": dev, "name": name or entry})
    return out


def list_webcams(sysfs_root: str = SYSFS_V4L2, probe=_is_capture_node, run=None) -> list[dict]:
    """The cameras Settings › Sources offers: V4L2 nodes on Linux
    (list_v4l2), DirectShow devices as ffmpeg lists them on Windows
    (list_dshow; `run` is its seam for tests)."""
    if host.windows():
        return list_dshow(run)
    return list_v4l2(sysfs_root, probe)


# One device in ffmpeg's -list_devices output, in either of the forms
# ffmpeg has printed: since 4.4 `"Name" (video)` (or (audio), or (none) for
# a device with neither), with a "DirectShow video devices" heading before
# them in older versions instead and no kind after the name. The log prefix
# is not relied on: it is "[dshow @ ...]" for the devices, but ffmpeg 9 on
# the build VM said "none found" as "[in#0 @ ...]", and OpenHamShack DATV's parser
# (ohsdatv/encoder.py) matches on what follows the "]" for the same reason. The
# alternative name (`Alternative name "@device_pnp_\\?\usb#..."`) follows
# its device on a line of its own.
_DSHOW_DEVICE = re.compile(r'^\[[^\]]*\]\s+"(?P<name>[^"]+)"(?:\s+\((?P<kind>[^)]*)\))?\s*$')
_DSHOW_ALT = re.compile(r'^\[[^\]]*\]\s+Alternative name\s+"(?P<alt>[^"]+)"')
_DSHOW_HEADING = re.compile(r"^\[[^\]]*\]\s+DirectShow (?P<kind>video|audio) devices")


def parse_dshow(text: str) -> list[dict]:
    """Video devices from `ffmpeg -list_devices true -f dshow -i dummy`'s
    output, as [{"path": what to put after video=, "name": the friendly
    name}], in ffmpeg's order.

    The path is the friendly name - what an operator recognises, and what
    still matches after the camera moves to another USB port - except when
    two cameras share a name (two of one model) or the name holds a ":"
    (dshow splits "video=A:audio=B" there): then the device's alternative
    name, which is unique and has no colon, is the only unambiguous one."""
    out: list[dict] = []
    heading = None
    for line in text.splitlines():
        line = line.rstrip()
        m = _DSHOW_HEADING.match(line)
        if m:
            heading = m.group("kind")
            continue
        m = _DSHOW_ALT.match(line)
        if m:
            if out and out[-1].get("_open"):
                out[-1]["alt"] = m.group("alt")
            continue
        m = _DSHOW_DEVICE.match(line)
        if not m:
            continue
        if out:
            out[-1].pop("_open", None)
        kind = m.group("kind")
        video = ("video" in kind) if kind is not None else heading == "video"
        out.append({"name": m.group("name"), "video": video, "_open": True})
    cams = [d for d in out if d["video"]]
    names = [d["name"] for d in cams]
    result = []
    for d in cams:
        path = d["name"]
        if (names.count(path) > 1 or ":" in path) and d.get("alt"):
            path = d["alt"]
        result.append({"path": path, "name": d["name"]})
    return result


def list_dshow(run=None) -> list[dict]:
    """The DirectShow cameras ffmpeg can open, or [] (no ffmpeg, no
    camera, or ffmpeg would not say). ffmpeg prints the list on stderr and
    then fails on the "dummy" input it was given, so its exit code means
    nothing here; only what it printed does."""
    exe = ffmpeg_path()
    if not exe:
        return []
    cmd = [exe, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"]
    try:
        if run is not None:
            text = run(cmd)
        else:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10, encoding="utf-8",
                               errors="replace", stdin=subprocess.DEVNULL, **host.no_console())
            text = (r.stderr or "") + (r.stdout or "")
    except (OSError, subprocess.TimeoutExpired):
        return []
    return parse_dshow(text)


def probe_duration(path: str) -> float | None:
    """The file's length in seconds (ffprobe), or None if unknown."""
    probe = ffprobe_path()
    if not probe:
        return None
    try:
        r = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                           capture_output=True, text=True, timeout=10, **host.no_console())
        d = float(r.stdout.strip())
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    return d if d > 0 else None


def has_audio(path: str) -> bool:
    """Whether the file has a sound stream. ffmpeg's optional mapping
    ("-map 0:a:0?") does not help here: an output file left with no streams
    is an error ("Error opening output files: Invalid argument"), which
    stopped every silent clip, so the sound output is only asked for when
    ffprobe finds a stream to put in it."""
    probe = ffprobe_path()
    if not probe:
        return False
    try:
        r = subprocess.run([probe, "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                            "-of", "csv=p=0", path], capture_output=True, text=True, timeout=10,
                           **host.no_console())
    except (OSError, subprocess.TimeoutExpired):
        return False
    return bool(r.stdout.strip())


class Source:
    kind = "none"
    static = True

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def frame(self) -> np.ndarray:
        """Latest picture, 576 x 720 x 3 float32 0..1, ident included."""
        raise NotImplementedError

    def audio(self, n: int) -> np.ndarray | None:
        """n samples of the source's own sound at 48 kHz, or None if it has none."""
        return None

    def status(self) -> str:
        return ""

    def error(self) -> str:
        """Why this source is not delivering pictures, or "" while it is
        (or is still starting)."""
        return ""

    def ident_rect(self) -> list[float]:
        """Where the ident is in this source's pictures, as fractions of the
        4:3 picture: [x0, y0, x1, y1]."""
        x0, y0, x1, y1 = self._ident_box()
        return [x0 / testcard.DW, y0 / testcard.DH, x1 / testcard.DW, y1 / testcard.DH]

    def _ident_box(self):
        raise NotImplementedError


class CardSource(Source):
    """The built-in test card or the colour bars, with the station's ident."""

    def __init__(self, kind: str, callsign: str, line2: str = "", lines405: bool = False):
        self.kind = kind
        self.static = True
        call = callsign or "NO CALLSIGN"
        if kind == "bars":
            self.img = testcard.colour_bars(call)
        else:
            # 405 lines have their own gratings (testcard.GRATINGS_405_MHZ).
            self.img = testcard.test_card(call, line2, "OPENHAMSHACK ATV", lines405=lines405)
        self.img = self.img.astype(np.float32)
        self.call = call

    def _ident_box(self):
        return testcard.ident_box(self.kind, self.call)

    def frame(self) -> np.ndarray:
        return self.img


class FfmpegSource(Source):
    """A webcam (`kind="webcam"`, `path` a V4L2 device) or a file (looped)."""

    def __init__(self, kind: str, path: str, ident: testcard.Ident):
        self.kind = kind
        self.static = False
        self.path = path
        self.ident = ident
        self.proc = None
        self._ident_box = ident.rect
        # Black until the first frame - and for ever if the camera or file
        # is not there - but never anonymous: keyed meanwhile, this is what
        # is sent. It used to be plain black, so a missing webcam put a
        # picture with no callsign on the air (DESIGN.md safety rule 6).
        self.latest = ident.apply(np.zeros((H, W, 3), np.float32))
        self.frames = 0
        self.err = ""
        # The file's sound as two channels, interleaved: NICAM sends them as
        # they are (audio_stereo); everything else takes their mix (audio).
        self.ring = Ring(48_000 * 4) if kind == "file" else None
        self.has_audio = False
        self.stop_ev = threading.Event()
        self.threads: list[threading.Thread] = []
        self.lock = threading.Lock()
        # Playback position (a file): where this run of ffmpeg started and
        # how many frames it has given since. Pause and seek restart ffmpeg
        # at the position (-ss): ffmpeg's -re paces by the wall clock since
        # it started, so simply not reading during a pause would make it
        # race to catch up afterwards. Subtitles follow this position.
        self.start_at = 0.0
        self.run_frames = 0
        self.paused = False
        self.pause_pos = 0.0
        self.duration = probe_duration(path) if kind == "file" and path and os.path.isfile(path) else None

    def position(self) -> float | None:
        """Seconds into the file being shown now (looping), or None for a
        webcam."""
        if self.kind != "file":
            return None
        if self.paused:
            return self.pause_pos
        with self.lock:
            pos = self.start_at + self.run_frames / 25.0
        return pos % self.duration if self.duration else pos

    def pause(self, on: bool) -> None:
        """Hold the picture (and the subtitles) where they are; resume from
        there. The held frame keeps being sent - a transmitter never stops."""
        if self.kind != "file" or on == self.paused:
            return
        if on:
            self.pause_pos = self.position() or 0.0
            self._halt()
            self.paused = True
        else:
            self.paused = False
            self.start_at = self.pause_pos
            self.start()

    def seek(self, seconds: float) -> None:
        if self.kind != "file":
            return
        t = max(0.0, float(seconds))
        if self.duration:
            t = min(t, max(0.0, self.duration - 0.04))
        if self.paused:
            self.pause_pos = t
            return
        self._halt()
        self.start_at = t
        self.start()

    def _cmd(self, audio_out: str | None) -> list[str]:
        """ffmpeg's arguments. `audio_out` is where the file's sound goes -
        "pipe:<fd>" on Linux, "tcp://127.0.0.1:<port>" on Windows - or None
        for none."""
        exe = ffmpeg_path()
        base = [exe, "-hide_banner", "-loglevel", "error", "-nostdin"]
        if self.kind == "webcam":
            # DirectShow takes the camera by name; -i video= with a name
            # holding spaces is one argument, so no quoting is needed. A
            # real-time buffer bigger than dshow's 3 MB default (DATV's
            # setting): at 720p that is under ten frames, and a camera's
            # frames are dropped with a warning whenever ffmpeg is late.
            cam = (["-f", "dshow", "-rtbufsize", "64M", "-i", f"video={self.path}"] if host.windows()
                   else ["-f", "v4l2", "-i", self.path])
            return base + cam + ["-vf", VF, "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"]
        seek = ["-ss", f"{self.start_at:.3f}"] if self.start_at > 0 else []
        cmd = base + ["-re", "-stream_loop", "-1"] + seek + ["-i", self.path, "-map", "0:v:0", "-vf", VF,
                      "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"]
        if audio_out is not None:
            cmd += ["-map", "0:a:0", "-ac", "2", "-ar", "48000", "-f", "f32le", audio_out]
        return cmd

    def _find_camera(self) -> str:
        """'' when self.path names a camera that is there, else why not.
        On Windows an empty setting (the default) takes the first camera
        ffmpeg lists, so an operator with one webcam never has to choose."""
        if not host.windows():
            return "" if os.path.exists(self.path) else f"No webcam at {self.path}"
        cams = list_dshow()
        if self.path.startswith("/dev/"):
            # A Linux config brought over: no camera chosen here yet.
            self.path = ""
        if not self.path:
            if not cams:
                return host.hint("webcam-none")
            self.path = cams[0]["path"]
            return ""
        if any(self.path in (c["path"], c["name"]) for c in cams):
            return ""
        return f"No webcam called {self.path!r} (Settings > Sources lists the cameras found)"

    def start(self) -> None:
        if not ffmpeg_path():
            self.err = host.hint("ffmpeg")
            return
        if self.kind == "webcam":
            why = self._find_camera()
            if why:
                self.err = why
                return
        if self.kind == "file" and not (self.path and os.path.isfile(self.path)):
            self.err = f"No video file at {self.path!r}" if self.path else "Choose a video file"
            return
        r_fd = w_fd = None
        listener = None
        audio_out = None
        if self.kind == "file" and has_audio(self.path):
            if host.windows():
                listener = FileAudioTcp()
                audio_out = listener.url
                self._listener = listener
            else:
                r_fd, w_fd = os.pipe()
                audio_out = f"pipe:{w_fd}"
        try:
            self.proc = subprocess.Popen(self._cmd(audio_out), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                         pass_fds=(w_fd,) if w_fd is not None else (), **host.no_console())
        except OSError as e:
            self.err = f"ffmpeg would not start: {e}"
            if listener is not None:
                listener.close()
            return
        finally:
            if w_fd is not None:
                os.close(w_fd)
        self.err = ""
        # A stop event per run of ffmpeg: after a pause or seek the old
        # run's reader threads see their own event set and go quietly, and
        # cannot report the old ffmpeg's end as the new one's.
        ev = self.stop_ev = threading.Event()
        proc = self.proc
        with self.lock:
            self.run_frames = 0
        if self.ring is not None:
            self.ring.discard(len(self.ring))      # sound from before a seek is not this position's
        t = threading.Thread(target=self._read_video, args=(proc, ev), name=f"src-{self.kind}", daemon=True)
        t.start()
        self.threads.append(t)
        if r_fd is not None or listener is not None:
            src = os.fdopen(r_fd, "rb", buffering=0) if r_fd is not None else listener
            t = threading.Thread(target=self._read_audio, args=(src, ev), name="src-audio", daemon=True)
            t.start()
            self.threads.append(t)

    def _read_video(self, proc, ev):
        out = proc.stdout
        while not ev.is_set():
            buf = out.read(FRAME_BYTES)
            if not buf or len(buf) < FRAME_BYTES:
                err = b""
                try:
                    err = proc.stderr.read() or b""
                except (OSError, ValueError, AttributeError):
                    pass
                if not ev.is_set():
                    self.err = ("ffmpeg stopped: " + err.decode("utf-8", "replace").strip().splitlines()[-1]
                                if err.strip() else "ffmpeg stopped")
                return
            img = np.frombuffer(buf, np.uint8).reshape(H, W, 3).astype(np.float32) * np.float32(1 / 255)
            self.ident.apply(img)
            if ev.is_set():
                return
            with self.lock:
                self.latest = img
                self.frames += 1
                self.run_frames += 1

    def _read_audio(self, f, ev):
        """The file's sound from `f` (the pipe, or FileAudioTcp: anything
        with read(n) and close()) into the ring, until ffmpeg closes it or
        this run is stopped."""
        # A read can end part-way through a sample pair - a socket splits
        # its stream anywhere, and a pipe may - so the odd bytes wait for
        # the next read rather than being dropped: dropped, every later
        # float would be read from the wrong byte (found on the socket
        # path, which Windows uses, as bursts of 1e35 in the sound).
        rest = b""
        with f:
            while not ev.is_set():
                b = f.read(8 * 4800)
                if not b:
                    return
                self.has_audio = True
                b = rest + b
                whole = len(b) // 8 * 8
                rest = b[whole:]
                if not whole:
                    continue
                a = np.frombuffer(b[:whole], np.float32)
                # Do not let ffmpeg run ahead of the transmitter by more than
                # the ring: -re paces it, the ring absorbs jitter.
                while len(self.ring) > 2 * 48_000 and not ev.is_set():
                    time.sleep(0.01)
                self.ring.write(a)

    def stop(self) -> None:
        self._halt()

    def _halt(self) -> None:
        self.stop_ev.set()
        # Windows' sound socket: closed so a reader still waiting for
        # ffmpeg's connection gives up now rather than at its timeout.
        listener, self._listener = getattr(self, "_listener", None), None
        if listener is not None:
            listener.close()
        if self.proc is not None:
            try:
                self.proc.kill()
                self.proc.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                pass
            for f in (self.proc.stdout, self.proc.stderr):
                try:
                    f.close()
                except (OSError, AttributeError):
                    pass
            self.proc = None

    def frame(self) -> np.ndarray:
        with self.lock:
            return self.latest

    def audio(self, n: int) -> np.ndarray | None:
        lr = self.audio_stereo(n)
        return None if lr is None else lr.mean(axis=1).astype(np.float32)

    def audio_stereo(self, n: int) -> np.ndarray | None:
        """n samples of the file's sound as (n, 2), left and right (a mono
        file's one channel twice, as ffmpeg upmixes it)."""
        if self.ring is None or not self.has_audio:
            return None
        a = self.ring.read(2 * n)
        if len(a) < 2 * n:
            a = np.concatenate([a, np.zeros(2 * n - len(a), np.float32)])
        return a[: 2 * n - (len(a) % 2)].reshape(-1, 2)[:n]

    def error(self) -> str:
        return self.err

    def status(self) -> str:
        if self.err:
            return self.err
        return f"{self.kind}: {self.frames} frames" + (" with sound" if self.has_audio else "")


class FileAudioTcp:
    """Where ffmpeg sends a file's sound on Windows (the module docstring
    says why not a pipe): a socket listening on 127.0.0.1 at a port the
    system picks, for exactly one connection - ffmpeg's, made as it opens
    its outputs. read(n) waits for that connection first, so the reader
    thread can be started before ffmpeg is.

    Loopback only, so nothing off this computer can reach it; another
    program on this computer that connected first would be read as the
    file's sound, which is the whole of the exposure - accepted, as the
    port lives only while ffmpeg starts and its number is not published."""

    ACCEPT_S = 15.0

    def __init__(self):
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.srv.settimeout(self.ACCEPT_S)
        self.url = f"tcp://127.0.0.1:{self.srv.getsockname()[1]}"
        self.conn = None

    def read(self, n: int) -> bytes:
        if self.conn is None:
            try:
                self.conn, _ = self.srv.accept()
            except OSError:
                return b""                      # ffmpeg never connected (or we were closed): no sound
            finally:
                self._close_server()
            self.conn.settimeout(None)
        try:
            return self.conn.recv(n)
        except OSError:
            return b""

    def _close_server(self) -> None:
        try:
            self.srv.close()
        except OSError:
            pass

    def close(self) -> None:
        self._close_server()
        if self.conn is not None:
            try:
                self.conn.close()
            except OSError:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def make_source(cfg: dict) -> Source:
    call = (cfg.get("callsign") or "").upper()
    line2 = " ".join(x for x in ((cfg.get("locator") or "").upper(), (cfg.get("identText") or "").upper()) if x)
    kind = cfg.get("source", "testcard")
    if kind in ("testcard", "bars"):
        lines405 = cfg.get("system") == "am" and cfg.get("videoFormat") == "405"
        return CardSource(kind, call, line2, lines405=lines405)
    ident = testcard.Ident(call or "NO CALLSIGN", line2, cfg.get("identPosition", "bottom-left"))
    if kind == "webcam":
        return FfmpegSource("webcam", cfg.get("webcam", state.WEBCAM_DEFAULT), ident)
    return FfmpegSource("file", cfg.get("videoFile", ""), ident)
