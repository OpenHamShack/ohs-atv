"""The engine: one object the UI talks to (DESIGN.md "Engine", docs/ENGINE-API.md).

    radio = Radio(cfg)           # opens nothing, transmits nothing
    radio.start()                # opens device + audio + source, starts receive
    radio.set(**changes)         # validated against ohsatv/state.py
    radio.state() -> dict
    radio.ptt(on)                # the only thing that keys
    radio.subscribe(kind) -> Feed
    radio.heartbeat()            # the UI's timer; the watchdog unkeys without it
    radio.stop()

Threads:
- **rx**: reads one frame (40 ms) of IQ at a time, takes the spectrum,
  demodulates picture and sound, plays the sound, and hands the raw
  composite to the decoder through a queue three frames deep. A full queue
  drops the oldest frame and counts it - the device is never left waiting.
- **decode**: sync, line tracking, PAL decoding; video and scope feeds. On
  its own thread because at 16-20 MS/s demodulating and decoding a frame
  together take longer than the frame lasts (DESIGN.md "CPU"), and numpy
  releases the GIL in the heavy calls, so the two overlap on two cores.
- **tx**: exists only while keyed. A static source with a tone or silence
  is sent as a precomputed 0.16 s loop; anything live is encoded and
  modulated frame by frame.
- **supervisor** (10 Hz): watchdog, transmit time-out, the transmit guard's
  sentences, batched state to the UI, config persistence. Not in the rx
  thread, so a stalled device read cannot stop it unkeying.
- **guard** (1 Hz, devices with watch(): the Pluto; or any radio holding
  the radio lock): reads back the rate and LOs the radio holds; sets back
  what moved, unkeys on a moved TX LO; keeps each lock's marker saying what
  the radio is doing (rx or tx, frequency, mode, format). The lock's
  heartbeat is its own thread (ohsatv/radiolock.py).

Shared radios (DESIGN.md "Shared radios"): every real radio is opened
holding the radio lock (ohsatv/devices/shared.py) - a busy one refuses the
start with the lock's sentence and is not touched - and a lost lock unkeys
and stops the engine. Before every key-up the transmitting radio's whole
state is compared with what this program last set (devices/pluto.py,
changed()); a radio changed by something else refuses the key-up until the
operator accepts it (accept_radio()).

Safety (DESIGN.md, non-negotiable): only ptt(True) keys; start, restart and
set() never do. Every key-up passes _tx_refusals(), and the supervisor
re-checks it while keyed: a change that makes transmitting wrong unkeys.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np

from . import audio as audio_mod
from . import bands, carousel, config, iqrec, modem, narrow, radiolock, sources, state, subtitles, sysa, systems, tti, ttx, txguard, vbi
from .devices import DeviceError, make_device
from .devices.shared import RadioBusy, holder_of

log = logging.getLogger("ohsatv.radio")

FEEDS = ("video", "txvideo", "spectrum", "txspectrum", "scope", "meter", "state", "toast", "teletext")
FRAME_S = 0.04
SPEC_BINS = 1024
# formatAuto while a picture is already being decoded (Radio._recheck):
# look every second, and switch only when that many looks in a row name the
# same other format with narrow.detect's `sure` margins.
RECHECK_BLOCKS = 25
RECHECK_AGREE = 3
# How often the guard asks the radio what it holds (Radio._guard). A second
# is soon enough to unkey a transmitter whose LO moved before it has said
# much off frequency, and each look is two or three attribute reads.
GUARD_S = 1.0
# The cable feed's transmit gain: the least any of our radios puts out (a
# Pluto's attenuation runs to -89.75 dB; a HackRF's TX VGA bottoms at 0,
# which is txGain -47 and below).
CABLE_GAIN_DB = -89.75


class Feed:
    """Bounded, drop-oldest queue. The engine puts and never blocks; the UI
    drains on a timer. `dropped` counts what a slow consumer missed."""

    def __init__(self, kind: str, maxlen: int):
        self.kind = kind
        self.q: deque = deque(maxlen=max(1, int(maxlen)))
        self.lock = threading.Lock()
        self.dropped = 0
        self.last_drain = time.monotonic()

    def put(self, item) -> None:
        with self.lock:
            if len(self.q) == self.q.maxlen:
                self.dropped += 1
            self.q.append(item)

    def drain(self, max_items: int | None = None) -> list:
        with self.lock:
            self.last_drain = time.monotonic()
            if max_items is None or max_items >= len(self.q):
                out = list(self.q)
                self.q.clear()
            else:
                out = [self.q.popleft() for _ in range(max_items)]
            return out

    def get(self):
        with self.lock:
            self.last_drain = time.monotonic()
            return self.q.popleft() if self.q else None

    def __len__(self):
        return len(self.q)


class TxRefused(ValueError):
    """Keying refused; the message is the operator-readable reason."""


class Radio:
    def __init__(self, cfg: dict | None = None, *, conf_path=None, data_dir=None, audio=None, device=None,
                 tx_device=None, persist: bool = True, link_station: bool = False):
        notes: list[str] = []
        from pathlib import Path
        self.conf_path = Path(conf_path) if conf_path is not None else None
        base = config.load(conf_path, notes) if cfg is None else config.clean(cfg, notes)
        self.st = state.defaults(("status",))
        self.st.update(base)
        # Settings that are never persisted (the cable feed) start from their
        # defaults every time: they are not in a config, so not in `base`.
        for k, v in state.defaults(("setting", "control")).items():
            self.st.setdefault(k, v)
        self.persist = persist
        self.data_dir = data_dir or config.data_dir()
        # The station profile kept on this computer and shared with the
        # other OpenHamShack apps and a paired controller (ohsatv/station.py).
        # One store per user, or one inside a data folder the engine was
        # given (tests, --data); opened on first use. Kept in step with the
        # settings only for the window's engine (`link_station`): the
        # suite's hundreds of throw-away engines must not each start a
        # watch thread. A missing or broken store never stops the radio.
        self._station = None
        self._station_dir = Path(data_dir) if data_dir else None
        self._station_link = None
        self.link_station = link_station
        self._audio_override = audio
        self._device_override = device
        self._tx_device_override = tx_device
        # Where the recording being played puts AM-VSB's carriers
        # (modem.PLACEMENTS), from its metadata: _rx_params.
        self._play_placement = "images"
        self.life = threading.RLock()        # start/stop/restart one at a time
        self.lock = threading.RLock()
        self.feeds: dict[str, list[Feed]] = {k: [] for k in FEEDS}
        self.running = False
        self.stop_ev = threading.Event()
        self.threads: list[threading.Thread] = []
        self.dev = None
        self.txdev = None                    # the transmitting device: self.dev, or a second one (HackRF)
        self.tx_open_error = ""
        self.audio = None
        self.source = None
        # Room for one slow picture (~100 ms under load at 16 MS/s) and the
        # blocks that arrive meanwhile: the decoder then catches up on them
        # without their pictures (_decode_loop). Dropping a block is the last
        # resort - it breaks the line timing and takes Mini-MAC's sound.
        self.dec_q: queue.Queue = queue.Queue(maxsize=8)
        self.keyed = False
        self.tx_thread = None
        self.tx_stop_ev = threading.Event()
        self.key_time = 0.0
        self.last_beat = time.monotonic()
        self.dirty = False
        self.changed: dict = {}
        self.notes = notes
        self.rx_paused = False
        # What the transmitter is sending, for the txspectrum feed: the
        # static loop, or the newest modulated block of a live source. A
        # reference handed over, never a copy - the tx thread pays nothing.
        self._tx_tap: np.ndarray | None = None
        self._loop_cache: dict = {}
        self._fps = deque(maxlen=50)
        self._cpu = {"t": time.monotonic(), "c": time.process_time()}
        self.rec: iqrec.Recorder | None = None
        self._rec_cf32 = False               # True: the engine feeds the recorder complex64 (no native tap)
        self._moved_seen = None
        # Teletext. Receive: every packet sliced off the air goes into the
        # page store, whatever is being sent. Transmit: the service (a
        # folder of .tti) loaded when chosen and again at each key-up.
        self.ttx_store = ttx.PageStore(on_page=self._ttx_done)
        self._ttx_svc: tti.Service | None = None
        self._ttx_sig = None                 # (path, mtime) the service was loaded from
        self._ttx_err = ""
        self._ttx_count = deque(maxlen=64)   # (monotonic time, packets) per decoded frame
        self._caption = None                 # (lines, until, text) of the last typed caption
        # The newest packets as received, for "save all" as a .t42 capture:
        # 40 000 is ~50 s of a full 32-line service, 1.7 MB.
        self._ttx_raw: deque = deque(maxlen=40_000)
        self._srt = None                     # ((path, mtime), subtitles.Track)
        self._recompute()
        if link_station:
            self.link_profile()

    # =============================================================== feeds
    def subscribe(self, kind: str, maxlen: int = 32) -> Feed:
        if kind not in self.feeds:
            raise ValueError(f"No feed called {kind!r}; there are {', '.join(FEEDS)}")
        f = Feed(kind, maxlen)
        with self.lock:
            self.feeds[kind].append(f)
        return f

    def unsubscribe(self, feed: Feed) -> None:
        with self.lock:
            for lst in self.feeds.values():
                if feed in lst:
                    lst.remove(feed)

    def _put(self, kind: str, item) -> None:
        for f in self.feeds[kind]:
            f.put(item)

    def toast(self, level: str, text: str) -> None:
        self._put("toast", {"t": time.time(), "level": level, "text": text})

    def heartbeat(self) -> None:
        self.last_beat = time.monotonic()

    # =============================================================== state
    def state(self) -> dict:
        with self.lock:
            return {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v)
                    for k, v in self.st.items()}

    def _status(self, **kv) -> None:
        with self.lock:
            for k, v in kv.items():
                if self.st.get(k) != v:
                    self.st[k] = v
                    self.changed[k] = v

    @property
    def fs(self) -> float:
        return float(self.st[self._rate_key(self.st)])

    @staticmethod
    def _rate_key(st: dict) -> str:
        """The setting that holds the sample rate for this system and the
        format we send (state.py, a405Rate)."""
        if st["system"] != "am":
            return "fmRate"
        if narrow.fmt_for("am", {"videoFormat": st.get("videoFormat", "pal")}, 0.0) == "405" and st.get("a405Rate"):
            return "a405Rate"
        return "amRate"

    def _params(self) -> dict:
        return {k: self.st[k] for k in ("amVideoBw", "amVsbLower", "amSoundOffset", "amSoundDb", "fmDeviation",
                                        "fmVideoBw", "fmPreemph", "fmSub", "fmSubIndex", "fmInvert",
                                        "videoFormat", "nicam")}

    def _rx_params(self) -> dict:
        """_params for the receiver. A recording plays back with AM-VSB's
        carriers where it says they were put (openhamshack:placement), or where
        every recording before that key put them, "centred": late on 5 October
        2026 the plans moved the carriers away from the AD936x's images
        (modem.am_plan), and a recording's samples cannot move with them.
        The 4 October colour captures at 10 MS/s have their vision carrier
        0.575 MHz from where the new plan looks (docs/atv-research.md 24).
        Only the receiver: what we would send is always placed for now."""
        p = self._params()
        if self.st["device"] == "file":
            p["amPlacement"] = self._play_placement
        return p

    def plan(self) -> dict:
        return systems.plan(self.fs, self.st["system"], self._params())

    def rx_plan(self) -> dict:
        """The plan of what the receiver is decoding. It differs from plan()
        - what we would send - when formatAuto has found another format on
        the air: on 4 October the plan card described PAL ("too narrow for
        colour: mono") over a Mini-MAC picture in colour; and when a
        recording made before the carriers moved is played (_rx_params)."""
        fmt = getattr(self, "rx_fmt", None)
        p = self._rx_params()
        if not self.running or fmt is None or self.st["system"] not in narrow.SYSTEMS.get(fmt, ()):
            return systems.plan(self.fs, self.st["system"], p)
        return systems.plan(self.fs, self.st["system"], dict(p, videoFormat=fmt))

    @staticmethod
    def _plain(pl: dict) -> dict:
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in pl.items()}

    def _lo(self, freq: float, rx: bool = False) -> float:
        """Where the device's LO goes for a carrier at `freq`: AM-VSB puts the
        vision carrier off the middle of the band (modem.am_plan, sysa.plan).
        `rx`: for the format being received, which formatAuto may have
        changed - 625 and 405 lines put their carriers in different places."""
        if self.st["system"] == "am":
            return freq - (self.rx_plan() if rx else self.plan())["carrier"]
        return freq

    def _recompute(self) -> None:
        """Derived status: the plan, the guard, the band/device fit."""
        pl = self.plan()
        b = bands.BY_NAME[self.st["band"]]
        rng = self.dev.freq_range() if self.dev is not None else None
        ok, why = bands.reachable(b, rng)
        tx = self.txdev
        trng = tx.freq_range() if tx is not None else None
        tok, twhy = bands.reachable(b, trng) if tx is not None and tx is not self.dev else (ok, why)
        self._status(plan=self._plain(pl), rxPlan=self._plain(self.rx_plan()), bandOk=ok, bandWhy=why, fs=self.fs,
                     range=list(rng) if rng else [], txBandOk=tok, txBandWhy=twhy)
        prof = txguard.profile(self.st)
        lic = {}
        if b in bands.BANDS:
            lim = txguard.GUARD.limit(prof, b.lo, b.hi)
            lic = {"spans": [[a, z] for a, z in txguard.GUARD.spans(prof, b.lo, b.hi)],
                   "limit": dict(lim) if lim else None}
        self._status(licenceInForce=txguard.GUARD.in_force(prof), bandLicence=lic)
        refuse, warn = self._tx_refusals()
        self._status(txRefused=refuse, txWarnings=warn)
        self._ttx_check()

    def set(self, authorise: bool = False, **changes) -> dict:
        """Validate and apply all of `changes` or none; return what changed."""
        clean = {}
        for k, v in changes.items():
            spec = state.SPEC.get(k)
            if spec is None:
                raise ValueError(f"There is no setting called {k!r}")
            if spec["kind"] == "status":
                raise ValueError(f"{k} is measured by the engine and cannot be set")
            if spec["auth"] and not authorise:
                raise ValueError(f"{k} can only be changed in Settings")
            clean[k] = state.coerce(k, v)
            state.check_value(k, clean[k])
        with self.lock:
            trial = dict(self.st)
            # A band change moves both frequencies to the band's default
            # channel, and the system to the band's first, unless the
            # same call says otherwise.
            # The cable feed has two bands, each with its one format: Band I
            # with 405 lines, UHF with 625-line PAL. Turning it on goes to
            # Band I, as it always has (and turning it off leaves, below).
            if clean.get("cableFeed") and not self.st["cableFeed"]:
                clean.setdefault("band", bands.CABLE.name)
            if "band" in clean and clean["band"] != self.st["band"]:
                b = bands.BY_NAME[clean["band"]]
                clean.setdefault("rxFreq", b.default)
                clean.setdefault("txFreq", b.default)
                clean.setdefault("system", b.modes[0])
                if b is bands.CABLE:
                    clean.setdefault("videoFormat", "405")
                elif b is bands.CABLE_UHF:
                    clean.setdefault("videoFormat", "pal")
            trial.update(clean)
            if not trial["split"]:
                trial["txFreq"] = trial["rxFreq"]
                if self.st["txFreq"] != trial["txFreq"]:
                    clean["txFreq"] = trial["txFreq"]
            # Unticking the cable confirmation turns the cable feed off, and
            # turning it off leaves Band I or UHF, which are the cable feed's alone.
            if clean.get("cableFeedAck") is False and trial["cableFeed"]:
                trial["cableFeed"] = clean["cableFeed"] = False
            if clean.get("cableFeed") is False and trial["band"] in bands.CABLE_NAMES:
                b = bands.BY_NAME[state.SPEC["band"]["default"]]
                for kk, vv in (("band", b.name), ("rxFreq", b.default), ("txFreq", b.default),
                               ("system", b.modes[0])):
                    trial[kk] = clean[kk] = vv
            if "txGainCeiling" in clean and trial["txGain"] > trial["txGainCeiling"]:
                trial["txGain"] = trial["txGainCeiling"]
                clean["txGain"] = trial["txGain"]
            state.check_combined(trial)
            done = {k: v for k, v in clean.items() if self.st.get(k) != v}
            old_fs = self.fs
            self.st.update(done)
            # A change of format can change the rate (a405Rate), which needs
            # the radios reopened as a change of the rate itself does.
            rate_moved = self.fs != old_fs
            self.changed.update(done)
            if done:
                self.dirty = True
        if done:
            self._apply(done, rate_moved)
            if self.link_station and self._station_link is not None:
                try:
                    self._station_link.push(done)
                except Exception:  # noqa: BLE001  the profile is a copy; the setting is already applied
                    log.warning("station profile not updated", exc_info=True)
        return done

    @property
    def station(self):
        """The shared store (shack_client Station), opened on first use."""
        if self._station is None:
            from . import station as station_mod
            self._station = station_mod.open_station(self.app_version(), self._station_dir)
        return self._station

    @property
    def station_link(self):
        """ohsatv.station.StationLink over `station`."""
        if self._station_link is None:
            from . import station as station_mod
            self._station_link = station_mod.StationLink(self.station, self)
        return self._station_link

    def link_profile(self) -> bool:
        """Join the shared station profile: the profile and the settings
        agree from now on. False (and a log line) when the store cannot be
        opened - the radio works the same without it."""
        try:
            self.station_link.adopt()
            return True
        except Exception:  # noqa: BLE001  a damaged or missing store must not stop the radio
            log.warning("station profile not adopted", exc_info=True)
            return False

    def close_station(self) -> None:
        """Close the shared store, if it was opened (the window's exit)."""
        if self._station is None:
            return
        try:
            if self._station_link is not None:
                self._station_link.close()
            else:
                self._station.close()
        except Exception:  # noqa: BLE001  closing a copy must not stop an exit
            log.warning("station store not closed", exc_info=True)
        self._station = self._station_link = None

    @staticmethod
    def app_version() -> str:
        try:
            return (Path(__file__).resolve().parent / "VERSION").read_text().strip()
        except OSError:
            return ""

    def _apply(self, done: dict, rate_moved: bool = False) -> None:
        """Make changes take effect on a running engine. `rate_moved`: the
        sample rate changed although no rate setting did."""
        if "cableFeed" in done and self.keyed:
            # Whichever way it went, what is on the air was keyed under the
            # other rules (frequency, power, amplifier): stop it.
            self._unkey("the cable feed was switched " + ("on" if done["cableFeed"] else "off"))
        self._recompute()
        if "audioSource" in done:
            self._sync_mic()
        if not self.running:
            return
        restart_keys = {k for k in done if state.SPEC[k]["restart"]}
        if restart_keys or rate_moved:
            # A new sample rate or system needs the device reopened; the UI
            # does it (stop + start) and that never keys.
            self.toast("info", "Restarting the radio for the new settings")
            threading.Thread(target=self._restart, daemon=True).start()
            return
        # The receive chain first, then the LO: where the LO goes depends on
        # the format being received (625 and 405 lines put the vision carrier
        # in different places), and that is the rebuilt chain's. The other
        # way round, a change of band and format in one call - the cable
        # feed going on, or moving between its Band I and UHF - left the LO
        # where the old format wanted it - 3.8 MHz out at 10 MS/s - and
        # nothing locked.
        if self._mode_keys(done):
            self._rebuild_rx()
            if hasattr(self.dev, "configure"):
                self.dev.configure(**self._params(), simCnrDb=self.st["simCnrDb"])
        if {"rxFreq", "txFreq", "band"} & done.keys() or self._mode_keys(done):
            self._retune()
        if {"simCnrDb", "simStations", "simTxErrHz", "simNicam"} & done.keys() and hasattr(self.dev, "configure"):
            self.dev.configure(simCnrDb=self.st["simCnrDb"], simStations=self.st["simStations"],
                               simTxErrHz=self.st["simTxErrHz"], simNicam=self.st["simNicam"])
        if {"source", "videoFile", "webcam", "callsign", "locator", "identText", "identPosition", "videoFormat",
                "system"} & done.keys():
            self._start_source()
        if "colour" in done and self.decoder is not None:
            self.decoder.colour_mode = done["colour"]
        if "txGain" in done and self.keyed and self.txdev is not None:
            try:
                self.txdev.set_tx_gain(self._tx_gain())
            except DeviceError as e:
                self.toast("error", str(e))

    def _sync_mic(self) -> None:
        """The microphone open while, and only while, it is the sound
        source: no microphone access the operator did not ask for (and on
        macOS, no permission prompt at launch holding the window back)."""
        if self.audio is None:
            return
        note = self.audio.use_mic(self.st["audioSource"] == "mic")
        if note:
            self.toast("warn", note)

    @staticmethod
    def _mode_keys(done) -> bool:
        return bool({"amVideoBw", "amVsbLower", "amSoundOffset", "amSoundDb", "fmDeviation", "fmVideoBw",
                     "fmPreemph", "fmSub", "fmSubIndex", "fmInvert", "videoFormat", "nicam"} & set(done))

    def _restart(self):
        with self.life:
            self.stop(save=False)
            try:
                self.start()
            except DeviceError as e:
                self.toast("error", str(e))

    # =========================================================== lifecycle
    def start(self) -> None:
        with self.life:
            self._start()

    def _start(self) -> None:
        if self.running:
            return
        self.stop_ev.clear()
        # Said once here, before any radio is wanted, rather than first as a
        # refusal at the moment the operator presses transmit (DATV's way).
        # On Windows this is what a folder the installer could not set up
        # looks like; on Linux it never speaks unless /run/lock is broken.
        lock_why = radiolock.folder_problem()
        if lock_why:
            self.toast("error", lock_why)
        if self.st["device"] == "file" and self._device_override is None:
            self._prepare_playback()
        self._steer_early()
        fs = self.fs
        self.dev = self._device_override or make_device(self.st, fs)
        if self._device_override is not None:
            self.dev.fs = fs
        self._status(radioLock={}, radioChanged="")
        self._prepare(self.dev)
        try:
            self.dev.open()
        except DeviceError as e:
            if isinstance(e, RadioBusy):
                self._status(radioLock=self._lock_state(refused=str(e), holder=e.holder))
            self._status(error=str(e), running=False)
            raise
        try:
            self._start_opened()
        except BaseException:
            # Whatever failed, the radios this start opened are closed again:
            # left open, they would hold their locks, and the next start
            # would find them busy - held by this very process.
            self._close_devices()
            raise

    def _start_opened(self) -> None:
        if self.audio is None:
            if self._audio_override is not None:
                self.audio = self._audio_override
                note = ""
            else:
                self.audio, note = audio_mod.make_backend(self.st)
            if note:
                self.toast("warn", note)
        # A restart keeps the backend it had, mic open or not, and may come
        # from a change that also chose another sound source.
        self._sync_mic()
        self._open_tx_device()
        self._steer()
        self._status(error="", running=True, link=getattr(self.dev, "link", ""),
                     linkMsps=getattr(self.dev, "link_msps", None), duplex=self._duplex())
        if hasattr(self.dev, "configure"):
            self.dev.configure(**self._params(), simCnrDb=self.st["simCnrDb"], simStations=self.st["simStations"],
                               simTxErrHz=self.st["simTxErrHz"], simNicam=self.st["simNicam"])
        self._recompute()
        self._rebuild_rx()
        self._retune()
        self._start_source()
        self.running = True
        self.last_beat = time.monotonic()
        self._moved_seen = None
        self._status(radioMoved=[], radioLock=self._lock_state())
        for d in self._locked():
            for n in d.radio_lock.notes:
                # A stale lock taken over, or a radio whose shell did not
                # answer (the lock then covers this computer only).
                self.toast("warn", n)
        loops = [("rx", self._rx_loop), ("decode", self._decode_loop), ("supervisor", self._supervise)]
        if hasattr(self.dev, "watch") or self._locked():
            loops.append(("guard", self._guard))
        for name, fn in loops:
            t = threading.Thread(target=fn, name=f"ohsatv-{name}", daemon=True)
            t.start()
            self.threads.append(t)

    def _open_tx_device(self):
        """The transmitting device. "same": the receiving one. "hackrf": a
        HackRF opened beside it, transmit only, so receive carries on while
        keyed. A HackRF that is missing or will not open is a sentence in
        txRefused, not a failed start - receive works without it."""
        self.tx_open_error = ""
        if self.st["txDevice"] == "same":
            self.txdev = self.dev
        else:
            from .devices.hackrf import HackRFDevice
            dev = self._tx_device_override or HackRFDevice(self.fs, self.st)
            dev.fs = self.fs
            self._prepare(dev)
            try:
                dev.open()
                self.txdev = dev
            except DeviceError as e:
                self.txdev = None
                self.tx_open_error = str(e)
                self.toast("warn", str(e))
                if isinstance(e, RadioBusy):
                    self._status(radioLock=self._lock_state(refused=str(e), holder=e.holder))
        tx = self.txdev
        self._status(txLink=getattr(tx, "link", "") if tx is not None else "",
                     txKind=getattr(tx, "kind", "") if tx is not None else "")

    def _steer_early(self) -> None:
        """Steer before opening where the URI alone says what the link is (a
        Pluto on usb: or its USB network address): the radio is then opened
        once, at the right rate. An Ethernet radio needs its model, so it is
        steered after opening (_steer), with a reopen."""
        if self._device_override is not None or self.st["device"] != "pluto":
            return
        from .devices import pluto
        link, msps = pluto.classify_link(self.st["plutoUri"], usb_net=pluto._usb_network_addresses())
        if link in ("usb", "usb-net"):
            tx_kind = "hackrf" if self.st["txDevice"] == "hackrf" else ""
            # A Tezuka nano is only known to be one after opening (_steer
            # probes it as "usb-rx8", or "usb-tx8" if it transmits 8-bit
            # too): once it has been steered that way, steering it here as
            # plain USB would undo that on every start.
            # Likewise a USB nano whose transmit link was measured at open
            # (the key then carries it: profiles.key's tx_msps).
            from . import profiles
            pk = self.st["profileKey"]
            if link == "usb" and (pk.startswith(profiles.key("pluto", "usb-rx8", pluto.USB_RX8_MSPS, tx_kind)) or
                                  pk.startswith(profiles.key("pluto", "usb-tx8", pluto.USB_RX8_MSPS, tx_kind)) or
                                  pk.startswith(profiles.key("pluto", "usb", msps, tx_kind) + "/tx")):
                return
            self._apply_profile("pluto", link, msps, tx_kind)

    def _apply_profile(self, rx_kind, link, msps, tx_kind, tx_msps=None) -> bool:
        """Apply a radio combination's profile if it is new; True if applied."""
        from . import profiles
        k = profiles.key(rx_kind, link, msps, tx_kind, tx_msps)
        if not self.st["autoProfile"] or k == self.st["profileKey"]:
            return False
        settings, sentence = profiles.best(rx_kind, link, msps, tx_kind, tx_msps)
        # Through the same validation as every other setting: 0.1.4's USB
        # profile set a 1.6 MHz video bandwidth under the table's 2 MHz
        # minimum, straight into the state, and the same values in a config
        # were then silently replaced by defaults - which the guard (rightly)
        # refused on the first 23cm test from the nano.
        settings = {kk: state.coerce(kk, v) for kk, v in settings.items()}
        with self.lock:
            changed = {kk: v for kk, v in settings.items() if self.st.get(kk) != v}
            self.st.update(changed)
            self.st["profileKey"] = k
            self.changed.update(changed)
            self.dirty = True
        if sentence:
            self._status(profile=sentence)
            self.toast("info", sentence)
        return bool(settings)

    def _steer(self) -> None:
        """After opening: apply this radio combination's best settings the
        first time it is seen (ohsatv/profiles.py), reopening the devices if
        the sample rate changed. Never keys; never touches a setting the
        operator has changed since, because it runs only when the
        combination is new."""
        rx_kind = getattr(self.dev, "kind", "")
        tx_kind = getattr(self.txdev, "kind", "") if self.txdev is not None and self.txdev is not self.dev else ""
        link, msps = getattr(self.dev, "link", ""), getattr(self.dev, "link_msps", None)
        if link == "usb" and getattr(self.dev, "rx8_capable", False):
            # 8-bit both ways is a profile of its own: the nano can then
            # transmit what it receives. Its check runs at open
            # (devices/pluto.py _probe_tx8) and no Tezuka bitstream has
            # passed it yet, so today this is always "usb-rx8".
            link = "usb-tx8" if getattr(self.dev, "tx8_capable", False) else "usb-rx8"
        # What a USB nano's transmit carries decides 405 lines' rate
        # (profiles.py): known only now, from its own USB network's state.
        tx_msps = getattr(self.dev, "tx_link_msps", None) if link in ("usb", "usb-rx8") and not tx_kind else None
        old_fs = self.fs
        if not self._apply_profile(rx_kind, link, msps, tx_kind, tx_msps) or self.fs == old_fs:
            return
        if self._device_override is not None:
            self.dev.fs = self.fs
            return
        # The rate the radios were opened at is no longer the plan's.
        for d in {id(self.txdev): self.txdev, id(self.dev): self.dev}.values():
            if d is not None:
                try:
                    d.close()
                except DeviceError:
                    pass
        self.dev = make_device(self.st, self.fs)
        self._prepare(self.dev)
        self.dev.open()
        self._open_tx_device()

    # ======================================================== IQ recording
    def _prepare_playback(self) -> None:
        """Before a recording is opened as the radio: run the engine at its
        sample rate, and where it says (a OpenHamShack ATV recording does) in its
        system, mode parameters and tuning - otherwise the plan and the
        decoder would be for a different signal."""
        try:
            r = iqrec.Recording(self.st["playFile"])
        except iqrec.RecordError as e:
            self._status(error=str(e), running=False)
            raise DeviceError(str(e)) from e
        g = r.globals
        placed = g.get("openhamshack:placement")
        self._play_placement = placed if placed in modem.PLACEMENTS else "centred"
        ch: dict = {}
        system = g.get("openhamshack:system")
        if system in state.SYSTEMS:
            ch["system"] = system
        system = ch.get("system", self.st["system"])
        rate = int(round(r.fs))
        if rate not in state.RATES:
            raise DeviceError(f"The recording is at {r.fs / 1e6:g} MS/s; OpenHamShack ATV decodes at "
                              f"{', '.join(f'{x / 1e6:g}' for x in state.RATES)} MS/s")
        for k, v in (g.get("openhamshack:params") or {}).items():
            if k in state.SPEC and state.SPEC[k]["kind"] in ("setting", "control"):
                ch[k] = state.coerce(k, v)
        # The recording's rate goes where this engine will look for it: a
        # 405-line recording's into a405Rate if that is in use.
        ch[self._rate_key({**self.st, **ch, "system": system})] = rate
        if g.get("openhamshack:rx_freq"):
            ch["rxFreq"] = float(g["openhamshack:rx_freq"])
        elif r.freq:
            # Only the centre is known: put the carrier where the plan expects it.
            pl = systems.plan(rate, system, {**self._params(), **{k: v for k, v in ch.items() if k in self._params()},
                                             "amPlacement": self._play_placement})
            ch["rxFreq"] = r.freq + (pl["carrier"] if system == "am" else 0.0)
        ch["autoProfile"] = self.st["autoProfile"]
        with self.lock:
            changed = {k: v for k, v in ch.items() if self.st.get(k) != v}
            self.st.update(changed)
            if not self.st["split"]:
                self.st["txFreq"] = self.st["rxFreq"]
            self.changed.update(changed)
        self._recompute()

    def record_iq(self, on: bool, seconds: float | None = None, why: str = "stopped") -> str:
        """Start or stop recording the receiving radio's raw IQ as SigMF
        (ohsatv/iqrec.py). Returns the .sigmf-meta path. Starting refuses
        (ValueError, with the sentence) when the radio is not running or the
        disk has no room for the limits (recSeconds, recMaxMB)."""
        if not on:
            rec, self.rec = self.rec, None
            dev = self.dev
            if dev is not None and getattr(dev, "raw_tap", None) is not None:
                dev.raw_tap = None
            self._rec_cf32 = False
            if rec is None:
                return ""
            rec.stop(why)
            self._rec_done(rec)
            return str(rec.meta_path)
        if self.rec is not None:
            return str(self.rec.meta_path)
        dev = self.dev
        if not self.running or dev is None:
            raise ValueError("Start the radio before recording")
        native = getattr(dev, "native_format", None) if hasattr(type(dev), "raw_tap") else None
        datatype, scale = native if native else ("cf32_le", 1.0)
        st = self.st
        folder = Path(st["recDir"]).expanduser() if st["recDir"] else Path(self.data_dir) / "recordings"
        stamp = time.strftime("%Y%m%d-%H%M%SZ", time.gmtime())
        base = folder / f"ohsatv-{stamp}-{st['system']}-{st['rxFreq'] / 1e6:.3f}MHz"
        ds = {}
        try:
            ds = dev.status()
        except Exception:  # noqa: BLE001 - the status is decoration here
            pass
        # The format being decoded, not the one chosen for transmitting: with
        # auto-detect the two differ, and a recording of a narrow signal that
        # says "pal" plays back as a mono picture - PAL's decoder locks to its
        # syncs, so auto-detect never looks again (seen with the first
        # narrow-format captures, 4 October 2026).
        params = self._params()
        if st.get("rxFormat") in narrow.OFFERED and "videoFormat" in params:
            params["videoFormat"] = st["rxFormat"]
        meta = {"core:hw": " ".join(str(x) for x in (ds.get("model") or getattr(dev, "kind", ""), ds.get("uri") or "",
                                                     ("fw " + ds["fw"]) if ds.get("fw") else "") if x),
                "core:description": f"{st['system'].upper()} at {st['rxFreq'] / 1e6:.3f} MHz, {self.fs / 1e6:g} MS/s, "
                                    f"band {st['band']}",
                "openhamshack:system": st["system"], "openhamshack:rx_freq": st["rxFreq"], "openhamshack:params": params,
                "openhamshack:rx_format": st.get("rxFormat", "pal"),
                "openhamshack:rx_bits": ds.get("rxBits"), "openhamshack:gain_mode": st["rxGainMode"],
                "openhamshack:device": getattr(dev, "kind", ""),
                # Where the receiver expected AM-VSB's carriers about its LO,
                # so playback looks there and not wherever the plans have
                # moved them since (_rx_params).
                "openhamshack:placement": self._rx_params().get("amPlacement", "images")}
        # What playback needs to undo the radio's own filters, as the live
        # receiver does (devices/filedev.py, recorded_response).
        if ds.get("rfBandwidth"):
            meta["openhamshack:rf_bandwidth"] = ds["rfBandwidth"]
        if ds.get("rxPathRates"):
            meta["openhamshack:rx_path_rates"] = ds["rxPathRates"]
        rx_lo = getattr(dev, "rx_lo", None)
        try:
            lo = rx_lo if rx_lo is not None else self._lo(st["rxFreq"], rx=True)
            rec = iqrec.Recorder(base, self.fs, lo, datatype, meta, max_seconds=seconds or st["recSeconds"],
                                 max_bytes=st["recMaxMB"] * 1_000_000, full_scale=scale)
        except (iqrec.RecordError, OSError) as e:
            raise ValueError(str(e)) from e
        self.rec = rec
        if native:
            self._rec_cf32 = False
            dev.raw_tap = rec.feed
        else:
            self._rec_cf32 = True
        self._status(recording=True, recFile=str(rec.meta_path), recWritten=0.0, recLost=0, recWhy="")
        return str(rec.meta_path)

    def _rec_tick(self) -> None:
        rec = self.rec
        if rec is None:
            return
        s = rec.status()
        self._status(recWritten=s["seconds"], recLost=s["lost"])
        if s["done"]:
            self.record_iq(False, why=s["reason"])

    def _rec_done(self, rec) -> None:
        s = rec.status()
        self._status(recording=False, recWritten=s["seconds"], recLost=s["lost"], recWhy=s["reason"])
        lost = f"; {s['lost']} samples lost (marked in the file)" if s["lost"] else ""
        self.toast("info", f"IQ recording saved: {rec.meta_path.name}, {s['seconds']:g} s, {s['reason']}{lost}")

    def _duplex(self) -> bool:
        """Receive continues while keyed: always with a separate transmit
        device, else when the one device says it can carry both."""
        if self.dev is None:
            return False
        if self.txdev is not None and self.txdev is not self.dev:
            return True
        return bool(self.dev.duplex())

    def stop(self, save: bool = True) -> None:
        with self.life:
            self._stop(save)

    def _stop(self, save: bool = True) -> None:
        self._unkey("stopped")
        self.record_iq(False, why="the radio stopped")
        self.stop_ev.set()
        for t in self.threads:
            t.join(timeout=3)
        self.threads = []
        if self.source is not None:
            self.source.stop()
            self.source = None
        self._close_devices()
        self.running = False
        # Nothing is being received now: the plan card goes back to ours.
        # The lock's refusal or loss stays for the window to say; nothing is held.
        self._status(running=False, rxPlan=self._plain(self.plan()),
                     radioLock=dict(self.st["radioLock"], held=[]) if self.st["radioLock"] else {})
        if save:
            self._save()
            if self.audio is not None and self._audio_override is None:
                self.audio.stop()
                self.audio = None

    def _close_devices(self) -> None:
        """Close both radios (each puts back what it changed, then gives
        its lock back)."""
        if self.dev is not None:
            try:
                self.dev.close()
            except DeviceError:
                pass
        if self.txdev is not None and self.txdev is not self.dev:
            try:
                self.txdev.close()
            except DeviceError:
                pass
        self.txdev = None
        self.dev = None if self._device_override is None else self.dev

    # =========================================================== shared radios
    def _prepare(self, dev) -> None:
        """Before a device is opened: it takes the radio lock (if it is a
        real radio - the simulator and a recording have none), with a note
        saying what it will be doing, and tells the engine if it loses it."""
        dev.lock_radio = True
        dev.lock_note = self._lock_use(dev)[1]
        dev.on_lock_lost = self._lock_lost

    def _locked(self) -> list:
        out = []
        for d in (self.dev, self.txdev):
            if d is not None and d not in out and getattr(d, "radio_lock", None) is not None and d.radio_lock.taken:
                out.append(d)
        return out

    def _lock_use(self, dev) -> tuple[str, str]:
        """(use, note) for `dev`'s marker: what the spec's message shows the
        next program. tx while it is keyed - or always for a transmit-only
        radio (the HackRF), whose note then says it is not keyed - else rx.
        The note: frequency, mode and picture format."""
        st = self.st
        mode = "AM-VSB" if st["system"] == "am" else "FM-ATV"
        tx_only = getattr(dev, "can_rx", True) is False
        if tx_only or (self.keyed and dev is self.txdev):
            fmt = narrow.fmt_for(st["system"], self._params(), self.fs)
            note = f"{st['txFreq'] / 1e6:.3f} MHz {mode} {narrow.SHORT.get(fmt, fmt)}"
            return "tx", note + ("" if self.keyed and dev is self.txdev else ", open, not keyed")
        fmt = st.get("rxFormat") or "pal"
        return "rx", f"{st['rxFreq'] / 1e6:.3f} MHz {mode} {narrow.SHORT.get(fmt, fmt)}"

    def _sync_locks(self) -> None:
        """Each held lock's marker says what its radio is doing now. A
        rewrite only when that changed (RadioLock.set_use): over ssh it is a
        round trip, so it is never made from the supervisor, which must stay
        free to unkey."""
        for d in self._locked():
            use, note = self._lock_use(d)
            d.radio_lock.set_use(use, note)
        if self.running:
            self._status(radioLock=self._lock_state())

    def _lock_state(self, refused: str = "", holder: dict | None = None, lost: str = "") -> dict:
        prev = self.st.get("radioLock") or {}
        return {"held": [d.radio_lock.describe() for d in self._locked()],
                "refused": refused or prev.get("refused", ""),
                "holder": holder if holder is not None else prev.get("holder", {}),
                "lost": lost or prev.get("lost", "")}

    def _lock_lost(self, dev, why: str) -> None:
        """From a lock's heartbeat thread: its marker is gone or another
        program's. The spec's order: unkey first, then stop using the radio,
        and say so. Stopping is the whole engine, on a thread of its own
        (stop() joins the threads that might be calling here); the device
        that lost its lock puts nothing back at close (leave_as_is)."""
        lock = dev.radio_lock
        try:
            holder = holder_of(lock) if lock is not None else {}
        except Exception:  # noqa: BLE001 - who has it now is decoration; stopping is not
            holder = {}
        who = f" {holder.get('app', 'another program')} on {holder.get('host', 'another computer')} has it now." \
            if holder else ""
        text = f"Lost the radio: {why}.{who} OpenHamShack ATV stopped using it (unkeyed first) and put nothing back"
        if self.keyed:
            self._unkey(text)
        self._status(radioLock=self._lock_state(lost=text, holder=holder), error=text)
        self.toast("error", text)

        def stop():
            self.stop()
            self._status(error=text)
        threading.Thread(target=stop, name="ohsatv-lock-lost", daemon=True).start()

    def accept_radio(self) -> None:
        """The operator accepts the transmitting radio as it is now: what a
        changed() refusal compared with becomes what it holds now, and the
        next key-up is allowed again (if nothing else refuses it)."""
        dev = self.txdev
        if dev is not None and hasattr(dev, "accept"):
            dev.accept()
        self._status(radioChanged="")
        self._recompute()

    def _radio_changed(self) -> str:
        """Before a key-up: '' or why the transmitting radio is not as this
        program last set it. A radio that cannot be read is not keyed on
        trust either."""
        dev = self.txdev
        check = getattr(dev, "changed", None)
        if check is None:
            return ""
        try:
            return check()
        except Exception as e:  # noqa: BLE001
            return f"The radio's state could not be read before keying ({e}); not keyed"

    def _save(self):
        if self.persist and self.dirty:
            try:
                config.save(self.st, self.conf_path)
                self.dirty = False
            except OSError as e:
                self.toast("error", f"Could not save the settings: {e}")

    # =============================================================== receive
    def _rebuild_rx(self, fmt: str | None = None):
        """The receive chain for the chosen format, or for `fmt` - the one
        formatAuto found on the air."""
        fs = self.fs
        system = self.st["system"]
        fmt = fmt or narrow.fmt_for(system, self._params(), fs)
        self.rx_fmt = fmt
        # The receiving radio's own filters are undone in the demodulator's
        # (devices/ad936x.py); a radio without a known response is taken as flat.
        self.demod = systems.make_demod(fs, system, self._rx_params(), fmt=fmt,
                                        rx_shape=getattr(self.dev, "rx_response", None))
        self.decoder = systems.make_decoder(fs, system, self._params(), self.st["colour"], fmt=fmt)
        self.ttx_store.clear()
        self._ttx_raw.clear()
        self._track_n = 0
        self._unlocked_since = time.monotonic()
        self._sniff_n = 0
        self._recheck_n = RECHECK_BLOCKS
        self._votes: tuple[str | None, int] = (None, 0)
        self._status(rxFormat=fmt, rxPlan=self._plain(self.rx_plan()), nicamLock=False, nicamBer=None,
                     nicamMode="", nicamActive=False)

    def _retune(self):
        if self.dev is None:
            return
        if not self.st["bandOk"]:
            self._status(error=self.st["bandWhy"])
            return
        try:
            lo = self._lo(self.st["rxFreq"], rx=True)
            rec = self.rec
            if rec is not None:
                # The format only: the LO's segment follows the samples
                # (read_lo), which reach the recording late. Before the LO
                # moves: the decoder has already switched (_switch_rx), and
                # told after, a block the receive thread fed in between went
                # down at the new LO under the old format - a third segment,
                # seen under load on 6 October 2026, with 405-line samples
                # marked PAL.
                rec.retune(None, getattr(self, "rx_fmt", None))
            self.dev.set_rx_lo(lo)
            # Another frequency is another station: its pages, not these.
            self.ttx_store.clear()
            self._ttx_raw.clear()
            self._status(error="")
        except DeviceError as e:
            self._status(error=str(e))
            self.toast("error", str(e))

    def _rx_loop(self):
        n = int(round(self.fs * FRAME_S))
        k = 0
        dev = self.dev
        next_tx_spec = 0.0
        tx_k = 0
        while not self.stop_ev.is_set():
            if self.keyed and not self._duplex():
                # Half duplex: the link has no room for both directions at
                # video rates. The picture shows the TX preview meanwhile,
                # and the spectrum what is being sent: this thread has
                # nothing else to do, and a spectrum frozen on the last
                # received trace looked like a live band.
                self.rx_paused = True
                now = time.monotonic()
                tap = self._tx_tap
                if tap is not None and now >= next_tx_spec:
                    next_tx_spec = now + 2 * FRAME_S      # the receive spectrum's rate, 12.5 a second
                    blocks = max(1, len(tap) // n)
                    tx_k = (tx_k + 1) % blocks            # a static loop is four frames: walk through them
                    self._spectrum(tap[tx_k * n:(tx_k + 1) * n], self.st["txFreq"], "txspectrum")
                time.sleep(0.02)
                continue
            self.rx_paused = False
            if not self.st["bandOk"]:
                time.sleep(0.1)
                continue
            try:
                iq = dev.read(n)
            except DeviceError as e:
                self._status(error=str(e))
                self.toast("error", str(e))
                time.sleep(1.0)
                continue
            except Exception:  # noqa: BLE001 - keep receiving whatever one block did
                log.exception("rx")
                continue
            k += 1
            lo = dev.read_lo
            rec = self.rec
            if rec is not None and self._rec_cf32:
                rec.feed(np.ascontiguousarray(iq, np.complex64), lo=lo)
            try:
                self._rx_block(iq, k, lo)
            except Exception:  # noqa: BLE001 - one bad block must not end receive
                log.exception("rx block")

    def _rx_block(self, iq: np.ndarray, k: int, lo: float | None):
        demod = self.demod
        if k % 2 == 0 and lo is not None:
            self._spectrum(iq, lo=lo)
        # Ask the demodulator, not the setting: for a moment after the
        # system changes, the setting is AM while the demodulator is
        # still FM's (it had no track() and this thread died).
        if hasattr(demod, "track"):
            self._track_n -= 1
            if self._track_n <= 0:
                c = demod.track(iq)
                self._track_n = 12 if c is not None else 3
                if c is not None:
                    self._status(carrierHz=float(c - demod.plan["carrier"]))
        raw = demod(iq)
        self._maybe_sniff(iq)
        snd = demod.audio(iq)
        # NICAM, where the band holds it: heard instead of the analogue
        # sound while it is the cleaner of the two (nicam.Receiver.active).
        lr = demod.nicam_audio(iq) if hasattr(demod, "nicam_audio") else None
        nic = getattr(demod, "nicam", None)
        if nic is not None:
            st = nic.stats
            ber = st["ber"]
            active = nic.active
            self._status(nicamLock=bool(st["lock"]), nicamMode=st["mode"], nicamActive=active,
                         nicamBer=None if ber is None else float(f"{ber:.2g}"))
            if active and lr is not None and len(lr):
                self._play(lr)
                snd = np.zeros(0, np.float32)
        if len(snd):
            self._play(snd)
        try:
            self.dec_q.put_nowait(raw)
        except queue.Full:
            try:
                self.dec_q.get_nowait()
            except queue.Empty:
                pass
            self.dec_q.put_nowait(raw)
            self._status(decodeSkipped=self.st["decodeSkipped"] + 1)

    def _play(self, snd: np.ndarray) -> None:
        """Play receive sound: mono, or (n, 2) stereo (NICAM)."""
        lvl = float(20 * np.log10(np.sqrt(np.mean(snd.astype(np.float64) ** 2)) + 1e-9))
        self._status(audioDb=round(lvl, 1))
        if not self.st["mute"] and self.audio is not None and not self.keyed:
            out = np.clip(snd * np.float32(self.st["volume"]), -1, 1)
            if out.ndim == 2:
                self.audio.play_stereo(out)
            else:
                self.audio.play(out)

    def _maybe_sniff(self, iq: np.ndarray) -> None:
        """formatAuto: when nothing has been decoded for a second, look at
        what is on the air every half second (narrow.detect, ~40 ms of IQ)
        and switch the receive chain to that format. Only the receiver
        changes - what we transmit is videoFormat, which only the operator
        sets."""
        if not self.st["formatAuto"]:
            return
        am = self.st["system"] == "am"
        now = time.monotonic()
        if self.st["rxLock"] == "locked":
            self._unlocked_since = now
            if not am:
                # On AM no standard's decoder locks to the other's signal:
                # 625 lines want a 4.7 us peak every 64 us, 405 a 9 us dip
                # every 98.8 (sysa.detect) - so a locked AM picture is the
                # right one and is never looked at again.
                self._recheck(iq)
            return
        self._votes = (None, 0)
        if now - self._unlocked_since < 1.0:
            return
        self._sniff_n -= 1
        if self._sniff_n > 0:
            return
        self._sniff_n = 12
        if am:
            # Where the vision carrier is: the demodulator tracks it.
            fmt, _ = sysa.detect(iq, self.fs, float(getattr(self.demod, "carrier", 0.0)))
        else:
            fmt, _ = narrow.detect(iq, self.fs, preemph=self.st["fmPreemph"])
        if fmt is None or fmt == self.rx_fmt or fmt not in narrow.OFFERED:
            return
        self._switch_rx(fmt)

    def _recheck(self, iq: np.ndarray) -> None:
        """formatAuto while locked. PAL's decoder locks to narrow colour
        PAL's syncs (the same pulses) and calls it a mono picture; the
        sniff above only looks when nothing is decoded, so on 4 October a
        recording of Low-PAL played back with "pal" in its metadata stayed
        mono for good. So a locked receiver looks again, every second -
        but only if it is not decoding colour: colour lock means the
        format's own burst (or Mini-MAC's data) was found where that format
        puts it, which no other format imitates. And it switches only on
        RECHECK_AGREE looks in a row naming the same other format, each with
        detect()'s `sure` margins; anything else (this format, or no clear
        answer) starts the count again. A genuine PAL picture then never
        moves: the margins are 2.6 dB (subcarrier) and 0.06 (Barker) beyond
        anything a PAL picture measured, three times over
        (tests/test_radio.py, NarrowFormats)."""
        if self.st["colourLock"]:
            self._votes = (None, 0)
            return
        self._recheck_n -= 1
        if self._recheck_n > 0:
            return
        self._recheck_n = RECHECK_BLOCKS
        fmt, _ = narrow.detect(iq, self.fs, preemph=self.st["fmPreemph"], sure=True)
        if fmt is None or fmt == self.rx_fmt or fmt not in narrow.OFFERED:
            self._votes = (None, 0)
            return
        prev, n = self._votes
        n = n + 1 if prev == fmt else 1
        self._votes = (fmt, n)
        if n >= RECHECK_AGREE:
            self._switch_rx(fmt)

    def _switch_rx(self, fmt: str) -> None:
        self._rebuild_rx(fmt)
        if self.st["system"] == "am":
            # 625 and 405 lines put the vision carrier in different places
            # in the band, so the receiver's LO moves with the format.
            self._retune()
        self.toast("info", f"Receiving {narrow.LABELS[fmt]}" + (f" ({narrow.NOT_STANDARD})" if narrow.is_narrow(fmt)
                                                                 else ""))

    def _spectrum(self, iq: np.ndarray, freq: float | None = None, kind: str = "spectrum", lo: float | None = None):
        """Power across the sample band, around `lo` - for received samples,
        the LO the radio took them at (Device.read_lo) - or the LO for
        `freq`, onto feed `kind`. Received frames whose LO the radio cannot
        say are not sent at all (_rx_block): until 5 October 2026 each was
        labelled with the LO in force when it was drawn, so for a moment
        after every retune the window drew, and a transmit guard read, the
        old LO's samples at the new LO's frequencies (docs/atv-research.md
        21a). The transmitted signal goes on its own feed, never this one: a
        consumer that knows only "spectrum" can then never show what we send
        as something received."""
        m = SPEC_BINS
        segs = len(iq) // m
        if segs < 1:
            return
        x = iq[: segs * m].reshape(segs, m)[:: max(1, segs // 24)]
        p = (np.abs(np.fft.fft(x * np.hanning(m).astype(np.float32), axis=1)) ** 2).mean(axis=0)
        db = (10 * np.log10(np.fft.fftshift(p) / (m * 0.375 * m) + 1e-20)).astype(np.float32)
        if lo is None:
            lo = self._lo(self.st["rxFreq"], rx=True) if freq is None else self._lo(freq)
        self._put(kind, (time.time(), lo - self.fs / 2, self.fs / m, db))

    def _decode_loop(self):
        while not self.stop_ev.is_set():
            try:
                raw = self.dec_q.get(timeout=0.2)
            except queue.Empty:
                continue
            dec = self.decoder
            # Behind: more signal is already waiting. Decode it all - timing,
            # line count and Mini-MAC's sound - but skip the picture of what
            # this block finishes. Dropping the signal itself (below, in
            # _rx_block, only if even this cannot keep up) breaks the line
            # timing and loses the sound carried in the picture.
            dec.picture = self.dec_q.empty()
            try:
                frames = dec.feed(raw)
            except Exception:  # noqa: BLE001
                log.exception("decode")
                continue
            st = dec.stats
            self._status(rxLock=st["state"], hLock=round(float(st["hLock"]), 3), vLock=bool(st["vLock"]),
                         colourLock=bool(st["colour"]) and bool(st["vLock"]),
                         snr=None if st["snr"] is None else round(float(st["snr"]), 1),
                         jitterNs=None if st["jitterNs"] is None else round(float(st["jitterNs"]), 1))
            for fr in frames:
                now = time.time()
                # Teletext from every frame, its picture built or not: the
                # packets cost nothing to keep, and a page half-missed is a
                # page shown a cycle later.
                pk = fr.get("ttx") or ()
                if fr.get("gap"):
                    # The signal broke before this frame: no page goes on
                    # from before the break (ttx.PageStore, "Never a row on
                    # the wrong page").
                    self.ttx_store.gap()
                for p in pk:
                    self.ttx_store.add(p, now)
                self._ttx_raw.extend(pk)
                self._ttx_count.append((time.monotonic(), len(pk)))
                snd = fr.get("audio")
                if snd is not None and len(snd):
                    # Sound carried in the picture (Mini-MAC) comes out with
                    # the frame, not from the demodulator.
                    self._play(snd)
                if fr["rgb"] is None:
                    self._status(decodeSkipped=self.st["decodeSkipped"] + 1)
                    continue
                self._fps.append(time.monotonic())
                self._put("video", (now, fr["rgb"], dict(fr["stats"])))
                comp = fr["composite"]
                # 405 lines have fewer lines to choose from, and their
                # picture comes out at the decoder's own rate.
                line = min(int(self.st["scopeLine"]), len(comp)) - 1
                self._put("scope", {"t": now, "line": line + 1, "wave": comp[line].copy(),
                                    "fs": dec.fs, "uv": dec.last_uv})

    # ============================================================== teletext
    def _ttx_load(self, force: bool = False) -> None:
        """Load the service in `ttxDir` if it changed (path, or the
        folder's modification time - a page added or removed) or `force`
        (each key-up, so a page edited in place goes out on the next
        transmission). Cheap when nothing changed: one stat."""
        path = self.st["ttxDir"].strip()
        if not path:
            # No folder: teletext is page 888's subtitles alone (and 8/30).
            self._ttx_svc, self._ttx_sig, self._ttx_err = tti.Service(), None, ""
            return
        try:
            sig = (path, os.stat(os.path.expanduser(path)).st_mtime_ns)
        except OSError:
            sig = (path, None)
        if sig == self._ttx_sig and not force:
            return
        self._ttx_sig = sig
        try:
            self._ttx_svc = tti.load_service(path)
            self._ttx_err = ""
            for n in self._ttx_svc.notes[:3]:
                log.info("teletext: %s", n)
        except OSError as e:
            self._ttx_svc = None
            self._ttx_err = f"Teletext folder {path}: {e.strerror or e}"

    def _ttx_lines(self) -> tuple[int, ...]:
        try:
            return vbi.parse_lines(self.st["ttxLines"])
        except ValueError:
            return vbi.DEFAULT_LINES

    def _ttx_check(self) -> None:
        """ttxStatus / ttxRefused from the settings, the service and the
        plan. Teletext switched on but not carried is said in a sentence
        (shown amber under the switch), never sent as garbage: a mode
        whose video bandwidth cannot carry it (narrow.teletext_fit, from
        measurement) does not get it inserted at all."""
        st = self.st
        if not st["ttxOn"]:
            self._status(ttxStatus="", ttxRefused="")
            return
        self._ttx_load()
        pl = self.plan()
        svc = self._ttx_svc
        refused = ""
        if st["cableFeed"] and st["band"] != bands.CABLE_UHF.name:
            # Decided, not inherited: on Band I the feed carries 405 lines
            # to a 405-line set, which has no teletext decoder, and its own
            # sentence says so ahead of the 405-line one. On UHF the feed
            # is 625-line PAL into a teletext-era set (Brian, 5 October
            # 2026), and teletext goes with it under the same rules as on
            # the air: the plan below decides (DESIGN.md "Cable feed").
            refused = ("Teletext and subtitles are not sent on the cable feed's Band I: it carries 405 lines "
                       "to a vintage set, and teletext is 625 lines only - choose UHF for 625 lines and teletext")
        elif self._ttx_err:
            refused = self._ttx_err
        elif st["ttxDir"].strip() and (svc is None or not len(svc)):
            refused = f"No teletext pages in {st['ttxDir']}: it needs .tti files (or a .t42 stream)"
        elif not pl.get("teletext"):
            refused = pl.get("teletextWhy") or "This mode cannot carry teletext"
        if refused:
            self._status(ttxStatus="", ttxRefused=refused)
            return
        lines = self._ttx_lines()
        if not len(svc):
            msg = (f"Sending subtitles on 888 only (no page folder chosen), on {len(lines)} lines - typed "
                   f"captions" + (" and the SRT file" if st["ttxSrt"].strip() else ""))
        else:
            what = f"{len(svc.pages)} page{'s' if len(svc.pages) != 1 else ''}" if svc.pages else "a .t42 stream"
            msg = (f"Sending {what} on {len(lines)} lines ({25 * len(lines)} packets/s), opening on "
                   f"{st['ttxInitial']}")
            if st["ttxSrt"].strip():
                msg += ", subtitles on 888"
        if pl.get("teletextWhy"):
            msg += ". " + pl["teletextWhy"]
        self._status(ttxStatus=msg, ttxRefused="")

    def _ttx_active(self) -> bool:
        return bool(self.st["ttxOn"] and not self.st["ttxRefused"] and self._ttx_svc is not None
                    and self.plan().get("teletext"))

    # ------------------------------------------------------------ subtitles
    def caption(self, text: str) -> None:
        """Send `text` now as a subtitle on page 888 (a typed caption: '|'
        breaks a line; <font color> tags work), for as long as it takes to
        read (subtitles.caption_seconds); an empty text clears it. Over an
        SRT file's cue while it lasts. Only while teletext is being sent:
        otherwise a sentence says why."""
        text = (text or "").strip()
        if text and not self._ttx_active():
            raise ValueError(self.st["ttxRefused"] or "Switch teletext on to send captions")
        if len(text) > 200:
            raise ValueError("A caption is at most 200 characters")
        if not text:
            self._caption = (None, 0.0, "")
            return
        self._caption = (subtitles.caption_lines(text), time.monotonic() + subtitles.caption_seconds(text), text)

    def _srt_track(self):
        """The SRT file's cues, read again when the file changes."""
        path = self.st["ttxSrt"].strip()
        if not path:
            return None
        try:
            sig = (path, os.stat(os.path.expanduser(path)).st_mtime_ns)
        except OSError:
            return None
        if self._srt is None or self._srt[0] != sig:
            try:
                with open(os.path.expanduser(path), "rb") as f:
                    self._srt = (sig, subtitles.Track(subtitles.parse_srt(f.read())))
            except OSError:
                return None
        return self._srt[1]

    def _subtitle_now(self, src, t_over: float):
        """(page 888's rows, text) now, or (None, "") when there are no
        subtitles at all this over. A typed caption first, while it lasts;
        then the SRT cue at the video file's position (or the time since
        key-up, for any other source)."""
        cap = self._caption
        track = self._srt_track()
        if cap is not None and cap[0] is not None and time.monotonic() < cap[1]:
            return subtitles.page_rows(cap[0], wrap=True), cap[2]
        if track is None and cap is None:
            return None, ""
        if track is not None:
            pos = src.position() if src is not None and hasattr(src, "position") else None
            cue = track.at(t_over if pos is None else pos)
            if cue is not None:
                return subtitles.page_rows(cue.lines), " / ".join("".join(t for _, t in ln) for ln in cue.lines)
        return subtitles.page_rows([]), ""

    # ------------------------------------------------------------ the file source
    def file_pause(self, on: bool) -> None:
        src = self.source
        if src is None or not hasattr(src, "pause") or getattr(src, "kind", "") != "file":
            raise ValueError("Pause is for the video file source")
        src.pause(bool(on))
        self._file_status()

    def file_seek(self, seconds: float) -> None:
        src = self.source
        if src is None or not hasattr(src, "seek") or getattr(src, "kind", "") != "file":
            raise ValueError("Seeking is for the video file source")
        src.seek(float(seconds))
        self._file_status()

    def _file_status(self) -> None:
        src = self.source
        if src is not None and getattr(src, "kind", "") == "file" and hasattr(src, "position"):
            pos = src.position()
            self._status(filePos=None if pos is None else round(pos, 2), fileLen=src.duration,
                         filePaused=bool(src.paused))
        else:
            self._status(filePos=None, fileLen=None, filePaused=False)

    def _ttx_done(self, mag: int, page: int, sub: int) -> None:
        self._put("teletext", {"t": time.time(), "page": ttx.page_label(mag, page), "sub": sub})

    def _ttx_rx_status(self) -> None:
        now = time.monotonic()
        recent = [(t, n) for t, n in list(self._ttx_count) if now - t < 2.0]
        rate = sum(n for _, n in recent) / 2.0
        s = self.ttx_store
        svc = s.service or {}
        ini = svc.get("initial")
        self._status(ttxRx={"packets": s.stats["packets"], "pages": len(s.labels()), "rate": rate,
                            "initial": ttx.page_label(ini[0], ini[1]) if ini else "",
                            "status": (svc.get("status") or b"").decode("latin-1").strip()})

    def ttx_page(self, label: str, sub: int | None = None) -> dict | None:
        """A received page for the viewer: {"label", "sub", "subs" (the
        sub-codes held), "rows" (25 x 40 bytes, 7-bit codes; row 0 is the
        header as a set shows it - 8 columns of page number then the 32
        sent), "links" [(label, sub) or None] x 6, "ctl", "age" s} - or None
        if it has not been received."""
        m = ttx.parse_label(label)
        if m is None:
            return None
        pg = self.ttx_store.get(m[0], m[1], sub)
        if pg is None:
            return None
        rows = [b" " * 40] * 25
        rows[0] = (b"P" + label.encode() + b"    ")[:8] + pg.header
        for y in range(1, 25):
            if pg.rows[y] is not None:
                rows[y] = pg.rows[y]
        links = [None if ln is None else (ttx.page_label(ln[0], ln[1]), ln[2]) for ln in pg.links]
        return {"label": label, "sub": pg.sub, "subs": self.ttx_store.subcodes(m[0], m[1]), "rows": rows,
                "links": links, "ctl": pg.ctl, "age": time.time() - pg.t}

    def ttx_header(self) -> dict | None:
        """The newest header heard, any page: {"label", "text" (32 bytes)}
        - the rolling header a set shows while it waits."""
        h = self.ttx_store.header()
        if h is None:
            return None
        return {"label": ttx.page_label(h[0], h[1]), "text": h[2]}

    def ttx_pages(self) -> list[str]:
        return self.ttx_store.labels()

    # ------------------------------------------------------------ saving received pages
    def _received_page(self, label: str):
        m = ttx.parse_label(label)
        if m is None:
            return None
        subs = []
        for sc in self.ttx_store.subcodes(*m):
            pg = self.ttx_store.get(m[0], m[1], sc)
            if pg is None:
                continue
            rows = [None] + [pg.rows[y] for y in range(1, 26)]
            links = [None if ln is None else ln for ln in pg.links]
            # C4 (erase) is how it was sent, not what the page is; the rest
            # of the control bits are kept.
            subs.append(ttx.Subpage(rows=rows, sub=pg.sub, ctl=pg.ctl & ~(ttx.C4 | ttx.SERIAL), links=links))
        if not subs:
            return None
        when = time.strftime("%Y-%m-%d %H:%M")
        desc = f"Received {when}, {self.st['rxFreq'] / 1e6:.3f} MHz"
        return ttx.Page(m[0], m[1], subs, desc[:40])

    def ttx_save_page(self, label: str, path: str) -> str:
        """Write a received page (all its subpages, a carousel if more than
        one) as a .tti OpenHamShack Teletext and vbit2 read; returns the path."""
        pg = self._received_page(label)
        if pg is None:
            raise ValueError(f"Page {label} has not been received")
        try:
            tti.write_atomic(path, tti.dumps(pg))
        except OSError as e:
            raise ValueError(f"Could not save page {label}: {e.strerror or e}") from None
        return str(path)

    def ttx_save_all(self, folder: str) -> tuple[int, str]:
        """Every received page into `folder` as P<mpp>.tti, and the packets
        as received (the newest ~50 s) as capture-<time>.t42; returns
        (pages written, the .t42's path or '')."""
        labels = self.ttx_store.labels()
        raw = list(self._ttx_raw)
        if not labels and not raw:
            raise ValueError("No teletext has been received")
        d = Path(os.path.expanduser(folder))
        try:
            d.mkdir(parents=True, exist_ok=True)
            n = 0
            for label in labels:
                pg = self._received_page(label)
                if pg is not None:
                    tti.write_atomic(d / f"P{label}.tti", tti.dumps(pg))
                    n += 1
            t42 = ""
            if raw:
                t42 = str(d / time.strftime("capture-%Y%m%d-%H%M%S.t42"))
                tti.write_atomic(t42, b"".join(raw))
        except OSError as e:
            raise ValueError(f"Could not save to {folder}: {e.strerror or e}") from None
        return n, t42

    # ============================================================== transmit
    def _tx_refusals(self) -> tuple[list[str], list[str]]:
        st = self.st
        refuse: list[str] = []
        warn: list[str] = []
        if not st["licenceAck"]:
            refuse.append("Acknowledge the licence conditions in Settings first")
        if not st["callsign"].strip():
            refuse.append("Set your callsign in Settings: it is drawn into every transmitted picture")
        dev = self.txdev
        separate = st["txDevice"] != "same"
        # Before start() there is no device object yet: go by the settings.
        if dev is None:
            real = separate or st["device"] not in ("sim", "file")
        else:
            real = bool(getattr(dev, "real", True))
        if self.running and separate and dev is None:
            refuse.append(self.tx_open_error or "The transmit device is not open")
        if dev is not None and not dev.can_tx:
            refuse.append("This radio cannot transmit")
        if real and not st["allowHardwareTx"]:
            refuse.append("Transmit on real hardware is off: enable it in Settings › Transmit")
        if not st.get("txBandOk", True):
            refuse.append(st["txBandWhy"])
        elif not separate and not st["bandOk"]:
            refuse.append(st["bandWhy"])
        pl = self.plan()
        if not pl["fits"]:
            refuse.append("The signal does not fit this sample rate: " + "; ".join(pl["why"]))
        cap = getattr(dev, "tx_link_msps", None) if dev is not None else None
        if cap and self.fs > cap * 1e6:
            # The nano starved at 6.25 MS/s on its first real transmission
            # (~15 % of the time), and its 8-bit receive invites rates it can
            # only receive: refused, not warned - the far end gets no picture.
            refuse.append(f"This radio transmits only ~{cap:g} MS/s over its {dev.link} link; at "
                          f"{self.fs / 1e6:g} MS/s the transmitter would starve and the picture break up. "
                          f"Choose 5 MS/s in Settings › Radio to transmit from it")
        if st["txGain"] > st["txGainCeiling"]:
            refuse.append("Transmit gain is above the ceiling")
        if st["radioChanged"] and not self.keyed:
            # Only before keying: a radio changed under a transmission
            # already on the air is the guard's (_moved), which unkeys on
            # what matters there and leaves a rate set back alone.
            refuse.append(st["radioChanged"])
        lo_off, hi_off = pl["occupied"]
        if st["cableFeed"]:
            # The cable feed replaces the amateur guard rather than adding to
            # it: neither Band I nor UHF is an amateur allocation, and while
            # the output is cabled to a television nothing else may be sent -
            # 405 lines on B1-B5 or 625-line PAL on E21-E48, whole inside the
            # channel and what the transmitter tunes, at its least output
            # (_tx_loop forces that; there is no power control).
            fmt = narrow.fmt_for(st["system"], self._params(), self.fs)
            trng = dev.freq_range() if dev is not None else None
            bref = bands.cable_check(st["txFreq"], lo_off, hi_off, fmt, st["system"], trng)
        else:
            # The station profile (country, licence, class) and the shared
            # dataset decide what the licence allows (ohsatv/txguard.py).
            bref, bwarn = bands.tx_check(st["txFreq"], lo_off, hi_off, txguard.profile(st), st["system"])
            warn += bwarn
        kind = getattr(dev, "kind", None) if dev is not None else ("hackrf" if separate else st["device"])
        img = sysa.tx_image_warning(pl, kind, self.fs, st["txFreq"])
        if img:
            warn.append(img)
        if real:
            refuse += bref
        else:
            warn += [f"on a real radio this would be refused: {r}" for r in bref]
        return refuse, warn

    def ptt(self, on: bool) -> None:
        if not on:
            self._unkey("released")
            return
        if self.keyed:
            return
        if not self.running:
            raise TxRefused("The radio is not running")
        refuse, warn = self._tx_refusals()
        if refuse:
            raise TxRefused(refuse[0])
        why = self._radio_changed()
        if why:
            self._status(radioChanged=why)
            self._recompute()
            raise TxRefused(why)
        self._log_tx("key", warnings=warn, teletext=self.st["ttxStatus"] if self._ttx_active() else "")
        self.tx_stop_ev.clear()
        self.keyed = True
        self.key_time = time.monotonic()
        self._status(keyed=True, txSeconds=0.0)
        self.tx_thread = threading.Thread(target=self._tx_loop, name="ohsatv-tx", daemon=True)
        self.tx_thread.start()

    def _unkey(self, why: str) -> None:
        if not self.keyed:
            return
        self.keyed = False
        self.tx_stop_ev.set()
        dev = self.txdev
        if dev is not None and getattr(dev, "stop_from_any_thread", False):
            # Cut the gain now, from this thread. The transmit thread may be
            # blocked in write() waiting for a USB stream that has stalled -
            # waiting for it to notice before unkeying would leave the
            # radio at power for as long as the stall lasts.
            try:
                dev.tx_stop()
            except DeviceError:
                pass
        t = self.tx_thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout=3)
        self.tx_thread = None
        self._tx_tap = None
        self._caption = None
        self._status(keyed=False, ttxSub="")
        self._log_tx("unkey", why=why, seconds=round(time.monotonic() - self.key_time, 1))
        if why not in ("released", "stopped"):
            self.toast("warn", f"Transmit stopped: {why}")

    # ------------------------------------------------------------ the log
    def _tx_log_path(self):
        from pathlib import Path
        return Path(self.data_dir) / "txlog.jsonl"

    def _log_tx(self, event: str, **kw) -> None:
        """Every key-up and unkey, one JSON line each, in $OHSATV_DATA/txlog.jsonl:
        when, where, what, on which radio, and the warnings that applied
        (a 70cm transmission carries 'OUTSIDE THE BANDPLAN'). Written at
        key-up as well as unkey, so a crash mid-over still leaves the record
        that it began. A log that cannot be written is a toast, never a
        reason not to unkey."""
        st = self.st
        rec = {"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": event}
        if event == "key":
            rec.update(freq=st["txFreq"], band=st["band"], system=st["system"], fs=self.fs,
                       format=narrow.fmt_for(st["system"], self._params(), self.fs),
                       device=getattr(self.txdev, "kind", st["device"]), callsign=st["callsign"],
                       source=st["source"], gainDb=self._tx_gain(), cableFeed=bool(st["cableFeed"]))
        rec.update(kw)
        try:
            path = self._tx_log_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
        except OSError as e:
            self.toast("error", f"Could not write the transmit log: {e}")

    def tx_log(self) -> list[dict]:
        """The transmit log, oldest first."""
        try:
            with open(self._tx_log_path(), encoding="utf-8") as f:
                return [json.loads(line) for line in f if line.strip()]
        except (OSError, ValueError):
            return []

    def _tx_gain(self) -> float:
        """The transmit gain to use: the power control's, or with the cable
        feed on the radio's least output whatever the control says - the
        Pluto's -89.75 dB (devices/pluto.py, TX_ATTEN_MAX), which is the
        HackRF's TX VGA 0 (devices/hackrf.py, vga_for)."""
        return CABLE_GAIN_DB if self.st["cableFeed"] else float(self.st["txGain"])

    def _tx_loop(self):
        dev = self.txdev
        fs = self.fs
        try:
            # The marker says "transmitting" before the over's first write.
            self._sync_locks()
            dev.set_tx_lo(self._lo(self.st["txFreq"]))
            if hasattr(dev, "amp"):
                # The HackRF's 14 dB amplifier stays off on the cable feed,
                # whatever Settings › Transmit says for the air.
                dev.amp = bool(self.st["hackrfAmp"]) and not self.st["cableFeed"]
            if hasattr(dev, "set_tx_bandwidth"):
                # A HackRF's images at +-fs meet only its baseband filter
                # (devices/hackrf.py): set for this over's plan before any
                # gain is asked for. AM is narrowed to its signal - its
                # occupied band is exact. FM gets libhackrf's default back
                # (None), which its Carson-rule edges were measured through.
                # Set on every over, FM's included: the device stays open
                # between overs, and an FM over that left the filter alone
                # went out through the previous AM over's 5 MHz.
                am = self.st["system"] == "am"
                dev.set_tx_bandwidth(modem.tx_span(self.plan()) if am else None)
            dev.set_tx_gain(self._tx_gain())
            src = self.source
            system = self.st["system"]
            aud = self.st["audioSource"]
            loop = None
            tele = None
            if self.st["ttxOn"]:
                # Read the service again: a page edited since the last over
                # goes out on this one.
                self._ttx_load(force=True)
                self._ttx_check()
                if self._ttx_active():
                    tele = self._ttx_svc
            # Teletext changes every field (the clock, the carousel), so it
            # cannot ride a 0.16 s loop: with it on, even a static picture
            # goes out as a live one (its frames encoded once and reused).
            if tele is None and src is not None and src.static and aud in ("tone", "none"):
                shape = getattr(dev, "tx_response", None)
                key = (fs, system, tuple(sorted(self._params().items())), id(src), aud, self.st["toneHz"],
                       id(shape))
                loop = self._loop_cache.get(key)
                if loop is None:
                    loop = systems.build_loop(fs, system, self._params(), src.frame(), self.st["toneHz"],
                                              0.5 if aud == "tone" else 0.0, tx_shape=shape)
                    self._loop_cache = {key: loop}
            dev.tx_start()
        except DeviceError as e:
            self.toast("error", str(e))
            self.keyed = False
            self._status(keyed=False)
            self._sync_locks()
            return
        try:
            if loop is not None:
                self._tx_static(dev, loop)
            else:
                self._tx_live(dev, system, fs, tele)
        except DeviceError as e:
            self.toast("error", str(e))
        except Exception:  # noqa: BLE001
            log.exception("tx")
            self.toast("error", "Transmit failed; see the log")
        finally:
            try:
                dev.tx_stop()
            except DeviceError:
                pass
            if self.keyed:
                self.keyed = False
                self._status(keyed=False)
            self._sync_locks()

    def _tx_static(self, dev, loop: np.ndarray):
        self._tx_tap = loop
        if hasattr(dev, "write_loop"):
            # The device sends a prepared loop itself (pluto.write_loop):
            # nothing per buffer in Python, so a busy machine no longer starves it.
            dev.write_loop(loop, self.tx_stop_ev)
            return
        n = int(round(self.fs * FRAME_S))
        k = 0
        while not self.tx_stop_ev.is_set():
            dev.write(loop[k * n:(k + 1) * n])
            k = (k + 1) % 4

    def _tx_live(self, dev, system: str, fs: float, tele: tti.Service | None = None):
        """Live sources: encode on one thread, modulate and write on this
        one. At 20 MS/s encoding (16 ms) and FM modulation (28 ms) together
        overrun a 40 ms frame; apart, each fits, on two cores. If the encoder
        is late the previous frame is sent again rather than leaving a gap
        in the carrier - a repeated frame is invisible, a gap breaks the
        receiver's lock - and counted in txRepeats.

        `tele`: a teletext service to insert (the carousel deals its
        packets to each field's lines). A static source is then encoded
        once per frame of its 4-frame colour sequence and each frame is a
        copy of that with the teletext added - the encode was 10-16 ms a
        frame, the copy and insert ~1."""
        mod = systems.make_mod(fs, system, self._params(), tx_shape=getattr(dev, "tx_response", None))
        enc = systems.make_encoder(fs, system, self._params())
        enc.colour = enc.colour and mod.plan["colour"]
        n_a = int(round(modem.AUDIO_RATE * FRAME_S))
        q: queue.Queue = queue.Queue(maxsize=2)
        tone = {"k": 0}
        # NICAM carries two channels: the stereo test for the tone (the
        # tone left, twice it right), the file's own two; the microphone is
        # one, sent to both.
        stereo = getattr(mod, "nicam", None) is not None

        def next_audio():
            aud = self.st["audioSource"]
            if aud == "tone":
                a = systems.test_tone(n_a, self.st["toneHz"], 0.5, stereo, tone["k"])
                tone["k"] = (tone["k"] + n_a) % (modem.AUDIO_RATE * 8)
                return a
            if aud == "mic" and self.audio is not None:
                return self.audio.read_mic(n_a)
            if aud == "file" and self.source is not None:
                if stereo and hasattr(self.source, "audio_stereo"):
                    return self.source.audio_stereo(n_a)
                return self.source.audio(n_a)
            return None

        # A format that carries its sound in the picture (Mini-MAC) needs
        # each frame's sound when the frame is encoded; the others have it
        # added by the modulator, as before.
        in_video = enc.audio_in_video

        ins = car = watch = None
        if tele is not None:
            ins = vbi.Inserter(fs, self._ttx_lines())
            car = carousel.Carousel(tele, name=self.st["callsign"], initial=self.st["ttxInitial"])
            if tele.path and os.path.isdir(tele.path):
                # Pages edited, added or removed while on air go out within
                # a cycle, with no restart (tti.FolderWatch).
                watch = tti.FolderWatch(tele.path)
        cache: dict = {}

        def poll_folder(k):
            if watch is None or k % 12:
                return
            try:
                new = watch.poll(time.monotonic())
            except Exception:  # noqa: BLE001 - a bad folder must never stop the transmission
                log.exception("teletext folder")
                return
            if new is not None:
                car.update(new)
                self._ttx_svc = new
                n = len(new.pages)
                self._status(ttxStatus=f"Sending {n} page{'s' if n != 1 else ''} (updated on air)")
            while watch.notes:
                self.toast("warn", "Teletext: " + watch.notes.pop(0))

        def encoder():
            k = 0
            while not self.tx_stop_ev.is_set():
                src = self.source
                if ins is not None and src is not None and src.static and not in_video:
                    if cache.get("src") is not src:          # the source changed mid-over
                        cache.clear()
                        cache["src"] = src
                    base = cache.get(k % 4)
                    if base is None:
                        base = cache[k % 4] = enc.encode(src.frame(), k, None)
                    comp = base.copy()
                else:
                    rgb = src.frame() if src is not None else np.zeros((576, 720, 3), np.float32)
                    comp = enc.encode(rgb, k, next_audio() if in_video else None)
                if ins is not None:
                    poll_folder(k)
                    rows, said = self._subtitle_now(self.source, k * FRAME_S)
                    car.set_subtitle(rows)
                    if said != self.st["ttxSub"]:
                        self._status(ttxSub=said)
                    ins.insert(comp, car.field(2 * k, len(ins.f1)), car.field(2 * k + 1, len(ins.f2)))
                while not self.tx_stop_ev.is_set():
                    try:
                        q.put((k, comp), timeout=0.1)
                        break
                    except queue.Full:
                        continue
                k += 1

        et = threading.Thread(target=encoder, name="ohsatv-encode", daemon=True)
        et.start()
        last = None
        repeats = 0
        try:
            while not self.tx_stop_ev.is_set():
                try:
                    _, comp = q.get(timeout=FRAME_S if last is not None else 1.0)
                    last = comp
                except queue.Empty:
                    if last is None:
                        continue
                    comp = last
                    repeats += 1
                    self._status(txRepeats=repeats)
                iq = mod(comp, None if in_video else next_audio())
                self._tx_tap = iq
                dev.write(iq)
        finally:
            self.tx_stop_ev.set()
            et.join(timeout=2)

    # ================================================== the radio's own values
    def _guard(self):
        """Once a second, ask the radio what it holds (the device's watch():
        the rate, the RX LO, the TX LO while keyed). On 5 October 2026 the
        nano moved to 2.330997 MS/s and 1254.666998 MHz 17 s into a receive
        run while the program went on showing what it had set
        (docs/atv-research.md 17a). A thread of its own, not the
        supervisor's: each read is an iiod round trip that can hang for the
        context's timeout, and the supervisor must stay free to unkey."""
        while not self.stop_ev.wait(GUARD_S):
            try:
                self._sync_locks()
            except Exception:  # noqa: BLE001 - as below
                log.exception("radio lock")
            dev = self.dev
            watch = getattr(dev, "watch", None)
            if watch is None:
                continue
            try:
                moves = watch()
            except Exception:  # noqa: BLE001 - a failed look is no reason to stop looking
                log.exception("guard")
                continue
            if moves:
                self._moved(moves, dev)

    def _moved(self, moves: list[dict], dev) -> None:
        """What the guard found. Transmitting from a radio whose TX LO moved
        is transmitting off frequency: unkey at once, nothing set back first.
        A rate or RX LO that moved has been set back by the device; if it
        did not hold while keyed, unkey too - the radio is no longer doing
        what the plan says. Each unkey goes through _unkey, so the reason is
        in the transmit log. The status key keeps the last moves for the
        window; a toast is posted when they differ from the last ones, not
        every second a stuck radio is found stuck again."""
        keyed = self.keyed and self.txdev is dev
        why = next((m["text"] for m in moves if m["what"] == "txLo"), None)
        if why is None and keyed:
            why = next((m["text"] for m in moves if m["held"] is False), None)
        if keyed and why:
            self._unkey(why)
        if self.txdev is dev and hasattr(dev, "changed_text"):
            self._status(radioChanged=dev.changed_text())
        t = time.time()
        self._status(radioMoved=[dict(m, t=t) for m in moves])
        seen = tuple((m["what"], m["to"], m["held"]) for m in moves)
        if seen != self._moved_seen:
            self._moved_seen = seen
            if not (keyed and why):              # _unkey has posted its own toast
                self.toast("warn", "; ".join(m["text"] for m in moves))

    # ============================================================== sources
    def _start_source(self):
        old = self.source
        if old is not None:
            old.stop()
        try:
            src = sources.make_source(self.st)
            src.start()
        except Exception as e:  # noqa: BLE001 - a source failing must not stop the radio
            log.exception("source")
            src = sources.CardSource("testcard", self.st["callsign"])
            self.toast("error", f"Source failed ({e}); sending the test card")
        self.source = src
        self._status(sourceStatus=src.status(), sourceError=src.error(), identRect=src.ident_rect())

    # ============================================================ supervisor
    def _supervise(self):
        last_preview = 0.0
        last_save = time.monotonic()
        while not self.stop_ev.wait(0.1):
            now = time.monotonic()
            if self.keyed:
                elapsed = now - self.key_time
                self._status(txSeconds=round(elapsed, 1))
                if elapsed > 60 * self.st["txTimeoutMin"]:
                    self._unkey(f"the {self.st['txTimeoutMin']:g}-minute time-out")
                beat = max([self.last_beat] + [f.last_drain for lst in self.feeds.values() for f in lst])
                if now - beat > self.st["watchdogSec"]:
                    self._unkey("the window stopped responding (watchdog)")
                refuse, _ = self._tx_refusals()
                if refuse and self.keyed:
                    self._unkey(refuse[0])
            else:
                self._recompute()
            # Transmit preview a few times a second: what would go out.
            if now - last_preview > (0.1 if self.keyed or self.st["view"] == "tx" else 0.5):
                last_preview = now
                src = self.source
                if src is not None:
                    frame = src.frame()
                    # Only if it is still the source: one replaced meanwhile
                    # (the operator chose another, on the window's thread)
                    # would otherwise put its last frame after the new
                    # one's - seen under load in tests/test_ui.py, a test
                    # card in the preview of a webcam source.
                    if src is self.source:
                        self._put("txvideo", (time.time(), frame))
                        self._status(sourceStatus=src.status(), sourceError=src.error())
            # CPU and frame rate.
            dt = now - self._cpu["t"]
            if dt >= 1.0:
                c = time.process_time()
                fps = sum(1 for x in self._fps if now - x < 2.0) / 2.0
                self._status(cpu=round((c - self._cpu["c"]) / dt, 2), fps=fps)
                self._cpu = {"t": now, "c": c}
                self._rec_tick()
                if self.dev is not None:
                    try:
                        self._status(deviceStatus=self.dev.status())
                    except Exception:  # noqa: BLE001
                        pass
                tx = self.txdev
                if tx is not None and tx is not self.dev:
                    try:
                        ts = tx.status()
                        self._status(txDeviceStatus=ts, txUnderruns=int(ts.get("txUnderruns", 0)))
                    except Exception:  # noqa: BLE001
                        pass
                if self.audio is not None:
                    self._status(audio=self.audio.status())
                self._ttx_rx_status()
                self._put("meter", {"t": time.time(), "snr": self.st["snr"], "audioDb": self.st["audioDb"],
                                    "hLock": self.st["hLock"], "keyed": self.keyed})
            if self.source is not None and getattr(self.source, "kind", "") == "file":
                self._file_status()
            with self.lock:
                ch, self.changed = self.changed, {}
            if ch:
                self._put("state", {"t": time.time(), "changes": ch})
            if self.dirty and now - last_save > 2.0:
                last_save = now
                self._save()
