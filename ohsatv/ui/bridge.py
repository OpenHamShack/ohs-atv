"""The one place the window meets the engine (copied from OpenHamShack NB).

A QTimer at ~30 Hz calls `radio.heartbeat()` and drains every feed, then
hands what it found to the widgets as Qt signals. Pictures are handed over
as the *newest* frame per tick only: the decoder makes 25 a second and the
timer runs at 30, so normally there is one, and when the window falls behind
the frames it skipped are the ones nobody would have seen anyway. Spectrum
lines and scope items are handed over as a batch, because the waterfall and
the phosphor displays want every one of them.

Heartbeat and draining live on the same timer on purpose (docs/ENGINE-API.md
"Watchdog"): if the event loop hangs, both stop together and the engine
unkeys. Nothing in the window may block this thread for long.

Operator actions go through `set()` and `ptt()` here, which turn the
engine's ValueError / TxRefused sentences into toasts instead of exceptions.
Unlike OpenHamShack NB the licence acknowledgement is an engine key, so the
engine's own guard holds it and this class adds no gate of its own - it
only makes sure the reason is seen.
"""
from __future__ import annotations

import logging
import time

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .. import state as state_mod

log = logging.getLogger("ohsatv.ui")


class Bridge(QObject):
    video = pyqtSignal(object)       # (t, rgb uint8 576xWx3, stats) - newest only
    txvideo = pyqtSignal(object)     # (t, rgb float32 576x720x3) - newest only
    spectrum = pyqtSignal(list)      # [(t, start_hz, bin_hz, db)]
    txspectrum = pyqtSignal(list)    # the same, of what is being sent (half duplex, keyed)
    scope = pyqtSignal(list)         # [{"t", "line", "wave", "fs", "uv"}]
    meter = pyqtSignal(dict)
    changed = pyqtSignal(dict)       # {key: value} of state keys that changed
    toast = pyqtSignal(dict)         # {level, text}
    tick = pyqtSignal(float)         # dt seconds, once per timer tick
    teletext = pyqtSignal(list)      # [{"t", "page": "100", "sub"}]: pages that finished arriving

    KINDS = ("video", "txvideo", "spectrum", "txspectrum", "scope", "meter", "state", "toast", "teletext")

    def __init__(self, radio, parent=None, hz: float = 30.0):
        super().__init__(parent)
        self.radio = radio
        self.feeds = {k: radio.subscribe(k, {"video": 4, "txvideo": 4, "toast": 64, "teletext": 256}.get(k, 32))
                      for k in self.KINDS}
        self.st = radio.state()
        self.timer = QTimer(self)
        self.timer.setInterval(int(1000 / hz))
        self.timer.timeout.connect(self.pump)
        self.last = time.monotonic()
        self.last_video = 0.0        # monotonic time the last decoded picture arrived
        self.frames = 0
        self.frame_ms = 0.0          # how long the last pump took, signal handlers included

    # ------------------------------------------------------------- lifecycle
    def start(self) -> str:
        """Start the engine; returns '' or the sentence saying why not."""
        self.timer.start()
        try:
            self.radio.start()
        except Exception as e:  # noqa: BLE001 - DeviceError or anything else: show it, keep the window
            self.st = self.radio.state()
            self.changed.emit(dict(self.st))
            return str(e)
        self.st = self.radio.state()
        self.changed.emit(dict(self.st))
        return ""

    def stop(self):
        self.timer.stop()
        try:
            self.radio.stop()
        finally:
            for f in self.feeds.values():
                self.radio.unsubscribe(f)

    # ------------------------------------------------------------- the pump
    def pump(self):
        t0 = time.perf_counter()
        now = time.monotonic()
        dt, self.last = now - self.last, now
        self.radio.heartbeat()
        f = self.feeds
        changes = {}
        for it in f["state"].drain():
            changes.update(it.get("changes", {}))
        if changes:
            self.st.update(changes)
            self.changed.emit(changes)
        frames = f["video"].drain()
        if frames:
            self.last_video = now
            self.frames += len(frames)
            self.video.emit(frames[-1])
        tx = f["txvideo"].drain()
        if tx:
            self.txvideo.emit(tx[-1])
        lines = f["spectrum"].drain()
        if lines:
            self.spectrum.emit(lines)
        lines = f["txspectrum"].drain()
        if lines:
            self.txspectrum.emit(lines)
        items = f["scope"].drain()
        if items:
            self.scope.emit(items)
        for it in f["meter"].drain():
            self.meter.emit(it)
        for it in f["toast"].drain():
            self.toast.emit(it)
        pages = f["teletext"].drain()
        if pages:
            self.teletext.emit(pages)
        self.tick.emit(dt)
        self.frame_ms = (time.perf_counter() - t0) * 1000.0

    def picture_age(self) -> float:
        """Seconds since the last decoded picture (inf if none yet)."""
        return time.monotonic() - self.last_video if self.last_video else float("inf")

    # ------------------------------------------------------------- actions
    def say(self, text: str, level: str = "warn"):
        self.toast.emit({"t": time.time(), "level": level, "text": text})

    def set(self, authorise: bool = False, quiet: bool = False, **changes) -> bool:
        try:
            done = self.radio.set(authorise=authorise, **changes)
        except ValueError as e:
            if not quiet:
                self.say(str(e))
            return False
        # Operator changes also arrive on the state feed; applying them here
        # too makes the control that caused them settle this frame, not next.
        if done:
            self.st.update(self.radio.state())
            self.changed.emit(dict(done))
        return True

    def refusal(self) -> str:
        """'' if a key-up would be attempted, else the sentence why not."""
        if not self.st.get("running"):
            return self.st.get("error") or "The radio is not running"
        r = self.st.get("txRefused") or []
        return r[0] if r else ""

    def keyed(self) -> bool:
        """Asked of the engine, not of `st`: a state batch from just before
        an unkey can still be in the feed, and a toggle or the space bar that
        read it would do the opposite of what the operator meant."""
        return bool(self.radio.state()["keyed"])

    def ptt(self, on: bool) -> bool:
        try:
            self.radio.ptt(on)
        except ValueError as e:      # TxRefused is a ValueError
            self.say(str(e))
            return False
        self.st["keyed"] = bool(on) and self.radio.state()["keyed"]
        self.changed.emit({"keyed": self.st["keyed"]})
        return True

    def accept_radio(self) -> None:
        """The operator's word that the transmitting radio, changed by
        something else, may be keyed as it is now (Radio.accept_radio)."""
        self.radio.accept_radio()
        self.st.update(self.radio.state())
        self.changed.emit({"radioChanged": self.st.get("radioChanged"), "txRefused": self.st.get("txRefused")})

    def record_iq(self, on: bool) -> bool:
        try:
            self.radio.record_iq(on)
        except ValueError as e:
            self.say(str(e))
            return False
        self.st.update(self.radio.state())
        self.changed.emit({"recording": self.st.get("recording"), "recWritten": self.st.get("recWritten")})
        return True

    def spec(self, key: str) -> dict:
        return state_mod.SPEC[key]
