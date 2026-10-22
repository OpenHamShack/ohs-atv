"""Raw IQ recording and reading, as SigMF (sigmf.org: a .sigmf-data file of
samples beside a small .sigmf-meta JSON file that says what they are).

Why: on 4 October 2026 Brian asked whether the on-air tests kept the IQ for
offline analysis. They did not. The two 10 s captures taken in the next test
(docs/atv-research.md 13a) were what showed the receive path dropping
whole buffers - nothing on the screen had said so. A recording lets a fault
seen on air be replayed through the same receive chain (devices/filedev.py)
as many times as it takes.

Decisions:

- **Native format.** The samples are written as the radio delivered them:
  a Pluto's 12-bit samples as `ci16_le`, Tezuka's packed 8-bit receive as
  `ci8`, anything else (the simulator) as `cf32_le`. Nothing is converted on
  the way in, so the file is exactly what the decoder saw, and 8-bit is half
  the size. `openhamshack:full_scale` records what 1.0 is (2048, 128 or 1).
- **The writing happens on its own thread.** feed() is called from the
  receive path and only queues the buffer; a slow disk must not become the
  very fault being recorded. If the queue fills, the buffer is counted as
  lost and an annotation marks where, so the file never silently splices.
- **Limits are checked before starting, not discovered at the end.** A
  recording has a time limit and a size limit, whichever comes first, and
  refuses to start unless the disk has room for all of it plus MIN_FREE. It
  checks again every second while writing (another program may be filling
  the disk) and stops with a reason rather than fill it.
- **The metadata is written at the start** (so a crash leaves a readable
  pair) and rewritten at the end with the sample count, the stop reason and
  any gaps.
"""
from __future__ import annotations

import json
import queue
import shutil
import threading
import time
from pathlib import Path

import numpy as np

# SigMF datatype -> (numpy element type, bytes per complex sample, default full scale)
DTYPES = {
    "ci8": (np.int8, 2, 128.0),
    "ci16_le": (np.int16, 4, 2048.0),
    "cf32_le": (np.float32, 8, 1.0),
}
MIN_FREE = 200 * 1024 * 1024        # left free on the disk, whatever the limits say
QUEUE_BUFFERS = 64                  # buffers waiting for the writer before one is dropped

# feed()'s `lo` when the caller does not say which LO the samples were taken at.
_SAME = object()


class RecordError(RuntimeError):
    """A recording that cannot start or continue, with the sentence why."""


def paths(base) -> tuple[Path, Path]:
    """(meta, data) for a base path, a .sigmf-meta path or a .sigmf-data path."""
    p = Path(base)
    name = p.name
    for ext in (".sigmf-meta", ".sigmf-data", ".sigmf"):
        if name.endswith(ext):
            name = name[: -len(ext)]
            break
    return p.with_name(name + ".sigmf-meta"), p.with_name(name + ".sigmf-data")


class Recorder:
    """Writes one recording. `meta` adds keys to SigMF's global object
    (core:hw, core:description, openhamshack:*). Raises RecordError if it cannot
    start."""

    def __init__(self, base, fs: float, freq: float, datatype: str, meta: dict | None = None,
                 max_seconds: float = 60.0, max_bytes: int = 4_000_000_000, full_scale: float | None = None,
                 min_free: int = MIN_FREE, disk_usage=None):
        if datatype not in DTYPES:
            raise RecordError(f"Cannot record {datatype!r}: {', '.join(DTYPES)} only")
        self.meta_path, self.data_path = paths(base)
        self.fs, self.freq, self.datatype = float(fs), float(freq), datatype
        self.bps = DTYPES[datatype][1]
        self.full_scale = DTYPES[datatype][2] if full_scale is None else float(full_scale)
        self.limit = (min(int(max_bytes), int(max_seconds * self.fs) * self.bps) // self.bps) * self.bps
        if self.limit <= 0:
            raise RecordError("The recording limits allow no samples")
        self.min_free = int(min_free)
        # Looked up when the recording starts, not bound as the default: a
        # default of shutil.disk_usage is fixed at import, and the engine
        # tests' patch of shutil.disk_usage never reached the recordings
        # Radio.record_iq starts, which then checked the real disk. When
        # /tmp filled (6 October 2026: 252 MB free, 210 MB of it reserved)
        # every one of them refused to start.
        self._disk_usage = disk_usage or shutil.disk_usage
        self.meta_path.parent.mkdir(parents=True, exist_ok=True)
        free = self._free()
        if free is not None and free - self.limit < self.min_free:
            raise RecordError(
                f"Not enough disk space to record: {self.limit / 1e6:.0f} MB wanted, {free / 1e6:.0f} MB free in "
                f"{self.meta_path.parent}, and {self.min_free / 1e6:.0f} MB must stay free. Shorten the recording "
                f"or free some space.")
        self.extra = dict(meta or {})
        self.started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        # One SigMF capture segment per tuning. The receiver moves its LO on
        # its own when format recognition switches between 625 and 405 lines
        # (radio._switch_rx); on 5 October 2026 a recording made across that
        # switch said 1257.4248 MHz for all 20 s while 16 of them were at
        # 1253.6, so every frequency read from it after 3.6 s was 3.8 MHz out.
        self.captures = [{"core:sample_start": 0, "core:frequency": self.freq, "core:datetime": self.started}]
        self.written = 0                 # bytes on disk
        self.queued = 0                  # samples accepted (written or waiting), for gap positions
        self.lost = 0                    # samples dropped because the writer fell behind
        self.gaps: list[tuple[int, int]] = []
        # [start, count] stretches whose LO the radio could not say (feed's
        # lo=None): the moments after a retune when its buffers still held
        # samples from before it.
        self.unsure: list[list[int]] = []
        self.reason = ""
        # `done` is the promise that the recording is finished: the data file
        # closed and the metadata rewritten with the count and the reason. It
        # is set last. Until 6 October 2026 it was also how the writer was
        # told to stop, so it was set before the final metadata, and a reader
        # woken by it (tests/test_iqrec.py's time limit, under load) could
        # find the metadata from the start, with no sample count. `_halt`
        # now tells the writer to stop.
        self.done = threading.Event()
        self._halt = threading.Event()
        self._q: queue.Queue = queue.Queue(maxsize=QUEUE_BUFFERS)
        self._lock = threading.Lock()
        self._write_meta()
        self._f = open(self.data_path, "wb")
        self._t = threading.Thread(target=self._writer, name="ohsatv-iqrec", daemon=True)
        self._t.start()

    # -- the receive side --------------------------------------------------
    def feed(self, raw, lo=_SAME) -> None:
        """Queue one buffer of native samples (bytes, or a numpy array in
        the recording's own format). Never blocks. `lo`: the LO the radio
        took them at (Device.read_lo), which opens a capture segment where
        it changes, or None when the radio cannot say, which annotates them;
        left out, they are taken to be at the current segment's."""
        if self.done.is_set() or self._halt.is_set():
            return
        b = raw.tobytes() if isinstance(raw, np.ndarray) else bytes(raw)
        n = len(b) // self.bps
        with self._lock:
            if self.queued * self.bps >= self.limit:
                return
            try:
                self._q.put_nowait(b)
            except queue.Full:
                self.lost += n
                self.gaps.append((self.queued, n))
                return
            if lo is None:
                u = self.unsure
                if u and u[-1][0] + u[-1][1] == self.queued:
                    u[-1][1] += n
                else:
                    u.append([self.queued, n])
            elif lo is not _SAME:
                self._segment(float(lo), None)
            self.queued += n

    def retune(self, freq: float | None, rx_format: str | None = None) -> None:
        """The receiver moved to `freq` (None: the LO stays) and to decoding
        `rx_format`: a new capture segment at the next sample queued. The
        engine gives only the format here, because the samples carry their
        own LO (feed's `lo`): a radio delivers samples from before a retune
        for a while after it - up to 1.2 s at 5 MS/s on a Pluto
        (devices/pluto.py RX_LO_PIPELINE_BUFS) - and a segment opened at the
        moment of the retune (3c4d570) labelled those with the new LO."""
        if self.done.is_set():
            return
        with self._lock:
            self._segment(self.freq if freq is None else float(freq), rx_format)

    def _segment(self, freq: float, rx_format: str | None) -> None:
        """Under _lock: a new segment at `queued` if the LO or the format
        changes there."""
        seg = {"core:sample_start": self.queued, "core:frequency": freq,
               "core:datetime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        last = self.captures[-1]
        last_fmt = last.get("openhamshack:rx_format", self.extra.get("openhamshack:rx_format"))
        if rx_format:
            seg["openhamshack:rx_format"] = rx_format
        elif "openhamshack:rx_format" in last:
            seg["openhamshack:rx_format"] = last["openhamshack:rx_format"]   # the LO moving alone keeps the format
        moved = abs(last["core:frequency"] - freq) > 1 or (rx_format and rx_format != last_fmt)
        if not moved:
            return
        if last["core:sample_start"] == seg["core:sample_start"]:
            self.captures[-1] = dict(last, **seg)
        else:
            self.captures.append(seg)
        self.freq = freq

    # -- the writer ----------------------------------------------------------
    def _free(self) -> int | None:
        try:
            return int(self._disk_usage(self.meta_path.parent).free)
        except OSError:
            return None

    def _writer(self):
        last_check = time.monotonic()
        try:
            while not self._halt.is_set():
                try:
                    b = self._q.get(timeout=0.2)
                except queue.Empty:
                    continue
                if b is None:
                    break
                b = b[: self.limit - self.written]
                self._f.write(b)
                self.written += len(b)
                if self.written >= self.limit:
                    self.reason = self.reason or "limit reached"
                    break
                now = time.monotonic()
                if now - last_check >= 1.0:
                    last_check = now
                    free = self._free()
                    if free is not None and free < self.min_free:
                        self.reason = f"stopped: the disk is nearly full ({free / 1e6:.0f} MB left)"
                        break
        except OSError as e:
            self.reason = f"stopped: could not write ({e})"
        finally:
            try:
                self._f.close()
            except OSError:
                pass
            self._halt.set()
            try:
                self._write_meta(final=True)
            finally:
                self.done.set()

    def stop(self, reason: str = "stopped") -> None:
        if not self.done.is_set():
            self.reason = self.reason or reason
            try:
                self._q.put(None, timeout=1.0)
            except queue.Full:
                self._halt.set()
        self._t.join(timeout=5)

    # -- metadata ------------------------------------------------------------
    def _write_meta(self, final: bool = False) -> None:
        g = {"core:datatype": self.datatype, "core:sample_rate": self.fs, "core:version": "1.0.0",
             "core:recorder": "OpenHamShack ATV", "openhamshack:full_scale": self.full_scale}
        g.update(self.extra)
        if final:
            g["openhamshack:samples"] = self.written // self.bps
            g["openhamshack:stop_reason"] = self.reason
            g["openhamshack:lost_samples"] = self.lost
        meta = {"global": g,
                "captures": [dict(c) for c in self.captures],
                "annotations": [{"core:sample_start": s, "core:sample_count": 0,
                                 "core:comment": f"{n} samples lost here: the recorder fell behind"}
                                for s, n in self.gaps]
                + [{"core:sample_start": s, "core:sample_count": n, "openhamshack:lo_unsure": True,
                    "core:comment": "the receiver had just retuned: these samples may be from the LO before it or "
                                    "after it; the next capture segment starts where the radio was sure"}
                   for s, n in self.unsure]}
        meta["annotations"].sort(key=lambda a: a["core:sample_start"])
        tmp = self.meta_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(meta, indent=1))
        tmp.replace(self.meta_path)

    def status(self) -> dict:
        return {"file": str(self.meta_path), "bytes": self.written, "seconds": round(self.written / self.bps / self.fs, 2),
                "lost": self.lost, "done": self.done.is_set(), "reason": self.reason}


class Recording:
    """A SigMF recording opened for reading: samples as complex64 with the
    radio's full scale at 1.0."""

    def __init__(self, path):
        self.meta_path, self.data_path = paths(path)
        try:
            self.meta = json.loads(self.meta_path.read_text())
        except (OSError, ValueError) as e:
            raise RecordError(f"Cannot read the recording's metadata {self.meta_path}: {e}") from e
        g = self.meta.get("global", {})
        self.datatype = g.get("core:datatype", "")
        if self.datatype not in DTYPES:
            raise RecordError(f"{self.meta_path.name}: datatype {self.datatype!r} is not one OpenHamShack ATV reads "
                              f"({', '.join(DTYPES)})")
        self.fs = float(g.get("core:sample_rate") or 0)
        if self.fs <= 0:
            raise RecordError(f"{self.meta_path.name} gives no sample rate")
        caps = self.meta.get("captures") or [{}]
        self.freq = float(caps[0].get("core:frequency") or 0.0)
        # (sample_start, frequency) per segment: a recording made across a
        # retune has more than one (Recorder.retune).
        self.segments = [(int(c.get("core:sample_start") or 0), float(c.get("core:frequency") or self.freq))
                         for c in caps]
        etype, self.bps, scale = DTYPES[self.datatype]
        self.full_scale = float(g.get("openhamshack:full_scale") or scale)
        try:
            self.raw = np.memmap(self.data_path, dtype=etype, mode="r")
        except (OSError, ValueError) as e:
            raise RecordError(f"Cannot read the recording's samples {self.data_path}: {e}") from e
        self.n = len(self.raw) // 2
        self.globals = g

    def samples(self, start: int, n: int) -> np.ndarray:
        """complex64 samples [start, start + n), clipped to the recording."""
        start = max(0, min(start, self.n))
        n = max(0, min(n, self.n - start))
        x = np.asarray(self.raw[2 * start: 2 * (start + n)], np.float32)
        return (x.view(np.complex64) / np.float32(self.full_scale)).astype(np.complex64)

    def freq_at(self, sample: int) -> float:
        """The LO the radio was at when it took `sample`."""
        f = self.freq
        for start, freq in self.segments:
            if start <= sample:
                f = freq
        return f

    @property
    def seconds(self) -> float:
        return self.n / self.fs
