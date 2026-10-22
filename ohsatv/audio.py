"""Audio in and out: PortAudio through pyaudio, or a null backend.

Copied from OpenHamShack NB and adapted: the engine's audio here is 48 kHz (the
rate the TV sound carrier is modulated and demodulated at), so a card at
48 kHz - nearly all of them - needs no resampling at all.

The engine's audio is 48 kHz float32 everywhere, mono but for NICAM's
stereo (play_stereo; two output channels where the card has them). This module is the only
place that knows the sound card's rate: it resamples on the engine side of
the ring buffers (in the RX and TX threads), so the PortAudio callbacks do
nothing but copy - a callback that computes is a callback that glitches when
the Pi is busy.

Receive playback keeps a small jitter buffer (~120 ms). On underrun the last
few milliseconds are faded out instead of stopping dead, and playback waits
for the buffer to refill to half its target before resuming, so a hiccup is a
soft gap rather than a click and a stutter. The SDR's clock and the sound
card's are not the same crystal; if the buffer creeps past 240 ms the oldest
audio is dropped back to the target.
"""
from __future__ import annotations

import contextlib
import logging
import os
import threading
import time

import numpy as np

import sys
from .dsp.resample import Resampler

log = logging.getLogger("ohsatv.audio")
RATE = 48_000


def _rms_db(x: np.ndarray) -> float:
    """RMS level of `x` in dBFS, floored well below the meter's range rather
    than at -inf for silence."""
    if not len(x):
        return -120.0
    return float(20 * np.log10(np.sqrt(np.mean(np.asarray(x, np.float64) ** 2)) + 1e-9))


class Ring:
    """Lock-protected circular float32 buffer; writes drop the oldest when full."""

    def __init__(self, size: int):
        self.buf = np.zeros(size, np.float32)
        self.size = size
        self.r = 0
        self.n = 0
        self.lock = threading.Lock()
        self.dropped = 0

    def write(self, x: np.ndarray):
        x = np.asarray(x, np.float32)
        with self.lock:
            if len(x) >= self.size:
                self.dropped += self.n + len(x) - self.size
                x = x[-self.size:]
                self.r, self.n = 0, 0
            over = self.n + len(x) - self.size
            if over > 0:
                self.r = (self.r + over) % self.size
                self.n -= over
                self.dropped += over
            w = (self.r + self.n) % self.size
            k = min(len(x), self.size - w)
            self.buf[w:w + k] = x[:k]
            self.buf[:len(x) - k] = x[k:]
            self.n += len(x)

    def read(self, n: int) -> np.ndarray:
        with self.lock:
            k = min(n, self.n)
            idx = (self.r + np.arange(k)) % self.size
            out = self.buf[idx]
            self.r = (self.r + k) % self.size
            self.n -= k
            return out

    def discard(self, n: int):
        with self.lock:
            k = min(n, self.n)
            self.r = (self.r + k) % self.size
            self.n -= k

    def __len__(self):
        return self.n


class Backend:
    """Interface the engine uses. All audio arguments/results are 48 kHz."""

    name = "none"
    rate = RATE

    def start(self):
        pass

    def stop(self):
        pass

    def play(self, audio: np.ndarray):
        pass

    def play_stereo(self, lr: np.ndarray):
        """(n, 2) left and right. A backend with one channel plays their
        mix - NICAM's compatible mono, (L + R) / 2."""
        lr = np.asarray(lr, np.float32)
        self.play(lr.mean(axis=1) if lr.ndim == 2 else lr)

    def monitor(self, audio: np.ndarray):
        pass

    def read_mic(self, n: int) -> np.ndarray:
        return np.zeros(n, np.float32)

    def mic_level(self) -> float:
        return -120.0

    def use_mic(self, on: bool) -> str:
        """Open (True) or close (False) the microphone; a sentence when it
        would not open. No microphone here, so nothing to do."""
        return ""

    def status(self) -> dict:
        return {"backend": self.name}


class NullAudio(Backend):
    """No sound card: playback is swallowed, the microphone is silence (or
    `source(n)` - tests feed test tones through it). With realtime=True
    read_mic() takes as long as the audio it returns would have."""

    name = "null"

    def __init__(self, realtime: bool = True, source=None):
        self.realtime = realtime
        self.source = source
        self.played = 0
        self.t_next = None
        self.level = -120.0

    def play(self, audio):
        self.played += len(audio)

    def read_mic(self, n: int) -> np.ndarray:
        if self.realtime:
            now = time.monotonic()
            if self.t_next is None or self.t_next < now - 0.5:
                self.t_next = now
            self.t_next += n / RATE
            wait = self.t_next - now - 0.05
            if wait > 0:
                time.sleep(wait)
        a = np.asarray(self.source(n), np.float32) if self.source else np.zeros(n, np.float32)
        self.level = 20 * np.log10(np.sqrt(np.mean(a ** 2)) + 1e-9) if len(a) else -120.0
        return a

    def mic_level(self) -> float:
        return float(self.level)

    def status(self):
        return {"backend": "null", "played": self.played}


@contextlib.contextmanager
def quiet_stderr():
    """Silence file descriptor 2 for the duration. PortAudio enumerating
    ALSA prints a screenful of "ALSA lib pcm.c: Unknown PCM cards.pcm.rear"
    and JACK "cannot connect to server" lines from C, straight to fd 2, every
    time a PyAudio is created - harmless, and alarming in the terminal of
    someone who just opened the Settings audio page. Python's sys.stderr is
    not involved, so it has to be the descriptor. Messages from our own code
    are unaffected outside the block."""
    try:
        saved = os.dup(2)
    except OSError:
        yield
        return
    try:
        with open(os.devnull, "w") as null:
            os.dup2(null.fileno(), 2)
            yield
    finally:
        os.dup2(saved, 2)
        os.close(saved)


def _usable_api(pa, d: dict) -> bool:
    """Whether to offer a device. On Windows PortAudio lists every card once
    per host API (MME, DirectSound, WASAPI, WDM-KS), so a laptop's one
    headset shows up four times under near-identical names, and choosing by
    name - as Settings does, so a USB headset is found again on another
    port - would land on whichever came first anyway. Only the default host
    API is offered there: MME, which accepts any rate and mono float32 from
    every card, where WASAPI shared mode refuses anything but the mixer's own
    format. Linux has one host API in practice (ALSA) and keeps every
    device."""
    if sys.platform != "win32":
        return True
    try:
        return d["hostApi"] == pa.get_default_host_api_info()["index"]
    except (OSError, KeyError):
        return True


def list_devices() -> list[dict]:
    """Sound devices PortAudio can see: [{index, name, inputs, outputs, rate}]."""
    try:
        import pyaudio
    except ImportError:
        return []
    with quiet_stderr():
        pa = pyaudio.PyAudio()
    try:
        out = []
        for i in range(pa.get_device_count()):
            d = pa.get_device_info_by_index(i)
            if not _usable_api(pa, d):
                continue
            out.append({"index": i, "name": d["name"], "inputs": int(d["maxInputChannels"]),
                        "outputs": int(d["maxOutputChannels"]), "rate": float(d["defaultSampleRate"])})
        return out
    finally:
        pa.terminate()


class PortAudio(Backend):
    name = "portaudio"
    TARGET = 0.12     # jitter buffer, seconds
    MAX = 0.24        # beyond this the SDR clock has outrun the card's: drop back to TARGET

    def __init__(self, out_name: str = "", in_name: str = "", out2_name: str = ""):
        import pyaudio  # noqa: F401 - fail here, with ImportError, if missing
        self.out_name, self.in_name, self.out2_name = out_name, in_name, out2_name
        # The microphone is opened only while the sound source is `mic`
        # (use_mic). It used to be opened at start() with the outputs,
        # whatever the source: on macOS that open is what asks for
        # microphone permission, and the prompt held the app's window back
        # at first launch - the signed .dmg "launched" with no window. And
        # an app sending a tone has no business listening anyway.
        self.want_mic = False
        self.mic_stream = None
        self.pa = None
        self.streams = []
        self.rate = None
        self.notes: list[str] = []
        self.priming = True
        self.underruns = 0
        self.level = -120.0
        self.last_sample = 0.0

    # -- device selection -------------------------------------------------------
    def _find(self, want: str, inputs: bool) -> int | None:
        if not want:
            return None
        key = "maxInputChannels" if inputs else "maxOutputChannels"
        w = want.lower()
        for i in range(self.pa.get_device_count()):
            d = self.pa.get_device_info_by_index(i)
            if d[key] > 0 and d["name"].lower() == w and _usable_api(self.pa, d):
                return i
        for i in range(self.pa.get_device_count()):
            d = self.pa.get_device_info_by_index(i)
            if d[key] > 0 and w in d["name"].lower() and _usable_api(self.pa, d):
                return i
        self.notes.append(f"Sound device {want!r} not found; using the system default")
        return None

    def _pick_rate(self, out_idx, in_idx) -> int:
        import pyaudio
        for r in (48_000, 44_100, 24_000, 96_000):
            try:
                ok = self.pa.is_format_supported(r, output_device=out_idx if out_idx is not None else
                                                 self.pa.get_default_output_device_info()["index"],
                                                 output_channels=1, output_format=pyaudio.paFloat32)
                if ok:
                    return r
            except (ValueError, OSError):
                continue
        try:
            return int(self.pa.get_default_output_device_info()["defaultSampleRate"])
        except (OSError, IOError):
            return 48_000

    def start(self):
        import pyaudio
        with quiet_stderr():
            self.pa = pyaudio.PyAudio()
        out_idx = self._find(self.out_name, inputs=False)
        in_idx = self._find(self.in_name, inputs=True)
        self.rate = self._pick_rate(out_idx, in_idx)
        r = self.rate
        # Playback is two channels where the card takes them (NICAM's
        # stereo); the ring holds them interleaved, and mono is played as
        # the same sample on both. Each channel has its own resampler.
        ch = self.out_channels = self._pick_channels(out_idx, r)
        self.ups = [Resampler(r, RATE) for _ in range(ch)]      # 48 kHz -> card, RX thread
        self.mon_up = Resampler(r, RATE)      # 48 kHz -> card, TX thread (monitor)
        self.down = Resampler(RATE, r)        # card -> 48 kHz, TX thread
        self.play_ring = Ring(int(r * 1.0) * ch)
        self.mon_ring = Ring(int(r * 0.5))
        self.mic_ring = Ring(int(r * 1.0))
        self.fade = np.linspace(1, 0, max(1, int(r * 0.004)), dtype=np.float32)
        self.last_frame = np.zeros(ch, np.float32)

        def out_cb(_in, frames, _ti, _st):
            # Copy only: everything heavy happened before the ring.
            need = frames * ch
            if self.priming:
                if len(self.play_ring) >= int(self.TARGET * r / 2) * ch:
                    self.priming = False
                out = np.zeros(need, np.float32)
            else:
                out = self.play_ring.read(need)
                if len(out) < need:
                    self.underruns += 1
                    self.priming = True
                    tail = np.zeros((need - len(out)) // ch * ch, np.float32).reshape(-1, ch)
                    k = min(len(self.fade), len(tail))
                    tail[:k] = self.last_frame[None, :] * self.fade[:k, None]
                    out = np.concatenate([out[: len(out) // ch * ch], tail.reshape(-1)])
                    out = np.concatenate([out, np.zeros(need - len(out), np.float32)])
            if len(self.mon_ring):
                m = self.mon_ring.read(frames)
                o = out.reshape(-1, ch)
                o[:len(m)] += m[:, None]
            if len(out) >= ch:
                self.last_frame = out[-ch:].astype(np.float32)
            return out.astype(np.float32).tobytes(), pyaudio.paContinue

        kw = dict(format=pyaudio.paFloat32, channels=1, rate=r, frames_per_buffer=int(r * 0.02))
        self._in_idx, self._kw = in_idx, kw
        self.streams.append(self.pa.open(output=True, output_device_index=out_idx, stream_callback=out_cb,
                                         **dict(kw, channels=ch)))
        if self.out2_name:
            idx2 = self._find(self.out2_name, inputs=False)
            if idx2 is not None:
                self.out2_ring = Ring(int(r * 1.0) * ch)

                def out2_cb(_in, frames, _ti, _st):
                    o = self.out2_ring.read(frames * ch)
                    if len(o) < frames * ch:
                        o = np.concatenate([o, np.zeros(frames * ch - len(o), np.float32)])
                    return o.tobytes(), pyaudio.paContinue

                self.streams.append(self.pa.open(output=True, output_device_index=idx2, stream_callback=out2_cb,
                                                 **dict(kw, channels=ch)))
        for s in self.streams:
            s.start_stream()
        if self.want_mic:              # chosen before start(), or open before a stop()
            note = self.use_mic(True)
            if note:
                self.notes.append(note)

    def use_mic(self, on: bool) -> str:
        """Open or close the input stream. The device is the one start()
        found by name and the rate the one the outputs were opened at, so
        the resampler (self.down) and the ring stand as they are.
        Called from the UI thread while the TX thread may be in read_mic():
        that only reads the ring, which pads with silence once the stream
        has gone."""
        self.want_mic = on
        if self.pa is None:
            return ""          # not started: start() opens it if still wanted
        if not on:
            s, self.mic_stream = self.mic_stream, None
            if s is not None:
                try:
                    s.stop_stream()
                    s.close()
                except OSError:
                    pass
            return ""
        if self.mic_stream is not None:
            return ""
        import pyaudio

        def in_cb(data, frames, _ti, _st):
            self._on_mic(np.frombuffer(data, np.float32))
            return None, pyaudio.paContinue

        try:
            s = self.pa.open(input=True, input_device_index=self._in_idx, stream_callback=in_cb, **self._kw)
            s.start_stream()
        except (OSError, ValueError) as e:
            return f"No microphone ({e}); a microphone sound source will send silence"
        self.mic_stream = s
        return ""

    def stop(self):
        want = self.want_mic          # a start() after this opens it again if it was wanted
        self.use_mic(False)
        self.want_mic = want
        for s in self.streams:
            try:
                s.stop_stream()
                s.close()
            except OSError:
                pass
        self.streams = []
        if self.pa:
            self.pa.terminate()
            self.pa = None

    def _on_mic(self, chunk: np.ndarray):
        """Every raw chunk from the card, at the card's rate. Pulled out of
        the PortAudio callback so the "level keeps updating even when
        nothing is reading the ring" behaviour (read_mic() is only called by
        the TX thread, while keyed) can be driven directly in a test rather
        than needing a real stream open."""
        self.mic_ring.write(chunk)
        self.level = _rms_db(chunk)

    def play(self, audio):
        a = np.asarray(audio, np.float32)
        self._play([a] * self.out_channels)

    def play_stereo(self, lr):
        lr = np.asarray(lr, np.float32)
        if self.out_channels == 1:
            self._play([lr.mean(axis=1)])
        else:
            self._play([lr[:, 0], lr[:, 1]])

    def _play(self, chans):
        ys = [up(c) for up, c in zip(self.ups, chans)]
        n = min(len(y) for y in ys)
        y = np.stack([v[:n] for v in ys], axis=1).reshape(-1)
        ch = self.out_channels
        self.play_ring.write(y)
        if len(self.play_ring) > self.MAX * self.rate * ch:
            self.play_ring.discard(len(self.play_ring) - int(self.TARGET * self.rate) * ch)
        if hasattr(self, "out2_ring"):
            self.out2_ring.write(y)

    def _pick_channels(self, out_idx, rate) -> int:
        """2 where the output takes stereo at this rate, else 1."""
        import pyaudio
        try:
            dev = out_idx if out_idx is not None else self.pa.get_default_output_device_info()["index"]
            if self.pa.is_format_supported(rate, output_device=dev, output_channels=2,
                                           output_format=pyaudio.paFloat32):
                return 2
        except (ValueError, OSError):
            pass
        return 1

    def monitor(self, audio):
        self.mon_ring.write(self.mon_up(audio))

    def read_mic(self, n: int) -> np.ndarray:
        """n samples at 48 kHz. Waits (at most the audio's own duration plus
        a little) for the card; whatever is missing is padded with silence so
        the transmitter is never starved."""
        need = int(np.ceil(n * self.rate / RATE)) + 4
        deadline = time.monotonic() + n / RATE + 0.1
        while len(self.mic_ring) < need and time.monotonic() < deadline:
            time.sleep(0.005)
        # Clock drift: never let the mic backlog grow past 200 ms.
        excess = len(self.mic_ring) - need - int(0.2 * self.rate)
        if excess > 0:
            self.mic_ring.discard(excess)
        raw = self.mic_ring.read(need)
        y = self.down(raw)
        if len(y) < n:
            y = np.concatenate([y, np.zeros(n - len(y), np.float32)])
        elif len(y) > n:
            y = y[:n]
        self.level = _rms_db(y)
        return y

    def mic_level(self) -> float:
        return float(self.level)

    def status(self):
        return {"backend": "portaudio", "rate": self.rate, "notes": list(self.notes), "underruns": self.underruns,
                "channels": getattr(self, "out_channels", 1),
                "bufferMs": round(1000 * len(self.play_ring) / self.rate / getattr(self, "out_channels", 1), 1)
                if self.rate else 0}


def make_backend(cfg: dict, realtime: bool = True) -> tuple[Backend, str]:
    """The configured backend, falling back to null (with the reason) when
    PortAudio or the card is unavailable: no sound card must never stop the
    radio."""
    if cfg.get("audioBackend", "portaudio") == "null":
        return NullAudio(realtime=realtime), ""
    try:
        # Outputs only: the microphone waits to be chosen as the sound
        # source (PortAudio.use_mic; the engine's _sync_mic, which opens it
        # straight away when a saved setting already chose it).
        b = PortAudio(cfg.get("audioOut", ""), cfg.get("audioIn", ""), cfg.get("audioOut2", ""))
        b.start()
        return b, "; ".join(b.notes)
    except ImportError:
        return NullAudio(realtime=realtime), "No audio: install python3-pyaudio"
    except Exception as e:  # noqa: BLE001 - any PortAudio failure degrades to silence
        log.warning("audio unavailable: %s", e)
        return NullAudio(realtime=realtime), f"No audio ({e}); running without a sound card"
