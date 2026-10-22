"""The received picture and the transmit preview as JPEG, for the store
form's MJPEG streams (docs/SERVER-API.md, "Pictures").

Why JPEG frames and not a video stream: the engine hands out decoded
pictures (ENGINE-API "Feeds": `video`, `txvideo`), not an encoded stream,
and analogue TV is what it is - noise, tearing and colour loss are the
signal report. A JPEG per frame keeps every frame independent, so a weak
picture costs nothing extra and a client that falls behind just skips
frames; `multipart/x-mixed-replace` shows in a browser, Android and iOS
with no player. An encoder (H.264 through ffmpeg) would save bandwidth at
the price of a subprocess, a GOP of latency and a smeared picture under
noise, which is the wrong trade for a receiver whose job is to show noise.

One encoder thread per picture kind, only while somebody watches: it
subscribes to the engine's feed when the first listener arrives and
unsubscribes when the last leaves, takes the newest frame at most `fps`
times a second, scales it to 4:3 (768 x 576, as ENGINE-API says to show
it) and encodes it once for every listener. A listener's queue holds two
frames and drops the older, so one slow phone never slows another.

Pillow does the scaling and encoding (Debian's python3-pil in the image);
without it the streams answer 503 and say so, and nothing else changes.
"""
from __future__ import annotations

import io
import queue
import threading
import time

import numpy as np

WIDTH, HEIGHT = 768, 576
DEFAULT_FPS = 12.5
MAX_FPS = 25.0
QUALITY = 80
LISTENER_FRAMES = 2


def have_pillow() -> bool:
    try:
        import PIL.Image  # noqa: F401
        return True
    except ImportError:
        return False


def to_jpeg(rgb, quality: int = QUALITY) -> bytes:
    """One engine picture as a 4:3 JPEG. `rgb`: uint8 576 x W x 3 (the
    receiver's) or float32 0..1 (the transmit preview's)."""
    from PIL import Image
    a = np.asarray(rgb)
    if a.dtype != np.uint8:
        a = (np.clip(a, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
    img = Image.fromarray(np.ascontiguousarray(a), "RGB")
    if img.size != (WIDTH, HEIGHT):
        img = img.resize((WIDTH, HEIGHT), Image.BILINEAR)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=quality)
    return out.getvalue()


class PictureHub:
    """JPEG frames of one engine feed (`video` or `txvideo`) for any number
    of listeners."""

    def __init__(self, radio, kind: str, fps: float = DEFAULT_FPS):
        self.radio, self.kind = radio, kind
        self.period = 1.0 / max(1.0, min(MAX_FPS, fps))
        self.lock = threading.Lock()
        self.listeners: list[queue.Queue] = []
        self.latest: bytes = b""
        self.latest_t = 0.0
        self.thread: threading.Thread | None = None
        self.stop_ev = threading.Event()

    def listen(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=LISTENER_FRAMES)
        with self.lock:
            self.listeners.append(q)
            if self.thread is None or not self.thread.is_alive():
                feed = self.radio.subscribe(self.kind, 2)
                self.thread = threading.Thread(target=self._run, args=(feed,), name=f"server-{self.kind}-jpeg",
                                               daemon=True)
                self.thread.start()
        return q

    def unlisten(self, q: queue.Queue) -> None:
        with self.lock:
            if q in self.listeners:
                self.listeners.remove(q)

    def count(self) -> int:
        with self.lock:
            return len(self.listeners)

    def close(self) -> None:
        self.stop_ev.set()

    def _run(self, feed) -> None:
        try:
            while not self.stop_ev.is_set():
                t0 = time.monotonic()
                with self.lock:
                    qs = list(self.listeners)
                    if not qs:
                        # The last listener left: give the feed back so the
                        # engine stops queueing pictures nobody encodes.
                        self.thread = None
                        return
                items = feed.drain()
                if items:
                    jpeg = to_jpeg(items[-1][1])
                    self.latest, self.latest_t = jpeg, time.monotonic()
                    for q in qs:
                        try:
                            q.put_nowait(jpeg)
                        except queue.Full:
                            try:
                                q.get_nowait()
                                q.put_nowait(jpeg)
                            except (queue.Empty, queue.Full):
                                pass
                self.stop_ev.wait(max(0.0, self.period - (time.monotonic() - t0)))
        finally:
            self.radio.unsubscribe(feed)
