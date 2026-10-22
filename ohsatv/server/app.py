"""The store form's HTTP and WebSocket API over one ATV engine
(docs/SERVER-API.md).

One process, one Radio, run headless in a container on an OpenHamShack
controller. Everything a person does arrives through the platform's proxy,
which has already signed them in and checked the route's minimum level
(CONTROLLER-CONTRACT 5.2); this server believes the identity headers only
after this app's proxy secret matches (5.3), checks the level again
(defence in depth, and for what a path glob cannot say - which settings
change what goes on the air), and never trusts a source address. No secret
configured is a locked app: every request is 401 proxy_required, which the
manifest's health check counts as healthy.

Transmitting, in this order (CONTROLLER-CONTRACT 6.2): the Transmit level;
the engine's own guard, exactly the window's (Radio.ptt: licence
acknowledgement, callsign, real-hardware switch, the band plan - 70 cm
outside 430-440 MHz a warning, 23 cm and 6 cm a refusal, the cable feed's
own check instead of the amateur one - the rate the link can carry, the
gain ceiling, a radio changed behind its back); a broker claim in tx mode
on the transmitting radio and the broker's on-air answer; only then the
key-up, held under the client's lease (REMOTE-API 7: heartbeat every 1 s,
unkeyed after 5 s of silence). Unkeying runs the other way: RF off, then
the broker told. A claim the broker takes back unkeys at once and stops
the radio, then is re-claimed for receive while the radio is still wanted
(a broker restart loses every claim); only a person's stop or a hand-over
ends wanting it.

Shaped on QO-100 NB's ohscore/server/app.py (openhamshack-nb 561732c), so a fix
to the lease or the broker handling carries across; what differs is ATV's:
two radios (a Pluto receiving and a HackRF transmitting beside it), the
pictures, teletext, the transmit log and the cable feed.

Threads: http.server's one per request, one for the lease, one for the
broker's heartbeat, one per picture kind while watched. The engine is
thread-safe (ENGINE-API.md).
"""
from __future__ import annotations

import base64
import dataclasses
import html
import json
import logging
import os
import queue
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import numpy as np

from .. import bands
from ..i18n.text import _
from ..radio import FEEDS, TxRefused
from . import pictures, shack, ws
from .audio import FORMAT

log = logging.getLogger("ohsatv.server")

APP_ID, KIND, NAME = "shack-atv", "atv", "ATV"
LEASE_S = 5.0                  # REMOTE-API 7 TX_REMOTE_LEASE
# Re-claiming a claim the broker lost: first try at once, then doubling to
# a ceiling, unless the broker says how long (retryAfter). The ceiling keeps
# a box whose controller was down for an hour back within 30 s of it.
RECLAIM_FIRST_S = 1.0
RECLAIM_MAX_S = 30.0
# Receive audio with the engine silent (stopped, re-claiming): this much
# real-time silence per wait, so a listener's socket is written and a gone
# client found (get_audio_rx).
IDLE_AUDIO_S = 0.5
IDLE_SILENCE = bytes(int(FORMAT["rate"] * IDLE_AUDIO_S) * 2)
REMOTE_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
# The controller's path prefix for this app (X-Forwarded-Prefix), used as
# given only when it is a plain path; anything else is ignored rather than
# echoed into the page.
PREFIX_RE = re.compile(r"^(/[A-Za-z0-9._~-]+)*/?$")
FEED_KINDS_DEFAULT = ("state", "toast", "meter", "teletext")
SPECTRUM = ("spectrum", "txspectrum")
# Pictures go over their own MJPEG routes, never as WebSocket messages: a
# 576-line RGB frame 25 times a second is 30 MB/s raw.
NOT_OVER_EVENTS = ("video", "txvideo")

# The broker's names for the radios the engine's settings choose (the
# rendezvous names every OpenHamShack app that uses one declares). The
# simulator and a recording are nobody's hardware.
RX_DEVICES = {"pluto": "pluto-1"}
TX_DEVICES = {"hackrf": "hackrf-1"}

# Settings the station profile owns on the platform (SERVICES.md 2): they
# change on the controller and reach the engine from there (run.py). The
# licence acknowledgement is the profile's licence answer (derived.canTransmit);
# the country, licence and conditions are the profile's too (station.py).
PROFILE_KEYS = frozenset({"callsign", "locator", "licenceAck", "stationCountry", "licence", "licenceClass",
                          "stationConditions"})
# Settings the store form cannot use or the controller does instead: the
# radio's address comes from the broker's claim, there is no sound card or
# camera, and recordings go to the data volume.
PLATFORM_KEYS = frozenset({"plutoUri", "hackrfSerial", "radioLockSsh", "radioSshUser", "radioSshPassword",
                           "audioBackend", "audioOut", "audioIn", "webcam", "recDir"})
# Never sent to a client: Radio.state() is every key, and a View-level
# person has no business with the Pluto's shell password.
SECRET_KEYS = frozenset({"radioSshPassword"})
# What may change at Control (CONTROLLER-CONTRACT 5.1: receive-side
# changes). Everything else - the transmit frequency, power, picture
# source, ident, teletext, NICAM, the modulation - changes what goes on the
# air and needs Transmit, as do the engine's `auth` keys.
RECEIVE_KEYS = frozenset({"volume", "mute", "view", "scopeLine", "colour", "formatAuto", "rxGainMode", "rxGain",
                          "recSeconds", "recMaxMB", "playLoop", "simCnrDb", "simStations", "simSeed", "simNicam",
                          "simTxErrHz"})
# Tuning: receive-side while nobody transmits (the key-up checks wherever
# it lands). On the air they would move or restart the transmitter - the
# transmit frequency follows the receive one without split, and a rate is
# a restart - so then they need Transmit.
TUNING_KEYS = frozenset({"band", "system", "rxFreq", "videoFormat", "amRate", "a405Rate", "fmRate", "plutoRx8",
                         "plutoTx8", "autoProfile", "profileKey", "playFile"})


def _json_default(o):
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (set, tuple, frozenset)):
        return list(o)
    if isinstance(o, (bytes, bytearray)):
        return list(o)
    if dataclasses.is_dataclass(o):
        return dataclasses.asdict(o)
    if hasattr(o, "__fspath__"):
        return os.fspath(o)
    return str(o)


def dumps(obj) -> bytes:
    return json.dumps(obj, default=_json_default, separators=(",", ":")).encode()


def _b64_i16(a, scale: float) -> str:
    v = np.clip(np.round(np.asarray(a, np.float32) * scale), -32768, 32767).astype("<i2")
    return base64.b64encode(v.tobytes()).decode()


def spectrum_item(kind: str, item) -> dict:
    """(t, start_hz, bin_hz, db) as JSON: the bins as centi-dB in int16,
    base64, about a tenth of the bytes of a JSON list (NB's format)."""
    t, start, bin_hz, db = item
    n = int(np.asarray(db).size)
    return {"kind": kind, "t": t, "startHz": float(start), "binHz": float(bin_hz), "n": n,
            "cdb": _b64_i16(db, 100.0)}


def scope_item(item: dict) -> dict:
    """The waveform monitor's line and the vectorscope as JSON: volts as
    int16 millivolts, base64 (a line at 20 MS/s is 1280 samples)."""
    out = {"kind": "scope", "t": item.get("t"), "line": item.get("line"), "fs": item.get("fs"),
           "mv": _b64_i16(item.get("wave", ()), 1000.0)}
    uv = item.get("uv")
    if uv is not None:
        out["uMv"], out["vMv"] = _b64_i16(uv[0], 1000.0), _b64_i16(uv[1], 1000.0)
    return out


def feed_message(kind: str, item) -> dict:
    if kind in SPECTRUM:
        return spectrum_item(kind, item)
    if kind == "scope":
        return scope_item(item)
    return {"kind": kind, **(item if isinstance(item, dict) else {"item": item})}


def public_state(st: dict) -> dict:
    return {k: v for k, v in st.items() if k not in SECRET_KEYS}


def onair_body(radio) -> dict:
    """What the broker's own licence check is told (CONTROLLER-CONTRACT
    7.4): the whole occupied signal - sound carrier to vestige, from the
    plan - as a centre and a width, not just the vision carrier, and the
    dataset's ATV mode. `cableFeed` says the output is cabled to a
    television, which the broker does not read yet (docs/SERVER-API.md,
    "What it is not")."""
    st = radio.state()
    lo, hi = st["plan"]["occupied"]
    f = float(st["txFreq"])
    return {"freqHz": int(round(f + (lo + hi) / 2)), "bandwidthHz": int(round(hi - lo)),
            "mode": "pal-fm" if st["system"] == "fm" else "pal-am", "cableFeed": bool(st["cableFeed"])}


class Server:
    def __init__(self, radio, audio, *, version: str = "", commit: str = "", secret: str | None = None,
                 broker=None, clock=time.monotonic):
        self.radio = radio
        self.audio = audio
        self.version, self.commit = version, commit
        self.secret = os.environ.get(shack.PROXY_SECRET_ENV, "") if secret is None else secret
        self.broker = broker
        self.clock = clock
        self.lock = threading.RLock()
        self.lease: dict | None = None      # {"id", "last", "device"}
        self.tx_error = ""
        self.radio_error = ""
        self.stop_ev = threading.Event()
        self.wanted = True                  # whether the radio should be running (run.py retries)
        # A claim the broker lost while the radio was wanted, being taken
        # back: {"device", "at" (next try, self.clock), "delay"}. While set,
        # the broker loop re-claims and run.py's plain retry stands aside.
        self.reclaim: dict | None = None
        # The engine says why it unkeyed itself (time-out, watchdog, a radio
        # that moved, a lost lock) only as a toast; the lease reads it from
        # here so txError tells the remote, as REMOTE-API 7 promises.
        self._toasts = radio.subscribe("toast", 32)
        self.pictures = {"rx": pictures.PictureHub(radio, "video"), "tx": pictures.PictureHub(radio, "txvideo")}
        self.httpd: ThreadingHTTPServer | None = None
        self.threads: list[threading.Thread] = []

    # ---------------------------------------------------------- devices
    def rx_device(self) -> str:
        return RX_DEVICES.get(self.radio.state().get("device", "sim"), "")

    def tx_device(self) -> str:
        """The broker's name for the radio that transmits: a HackRF beside
        the receiver when txDevice says so, else the receiver itself."""
        st = self.radio.state()
        if st.get("txDevice", "same") != "same":
            return TX_DEVICES.get(st["txDevice"], "")
        return self.rx_device()

    def devices(self) -> list[str]:
        return [d for d in dict.fromkeys((self.rx_device(), self.tx_device())) if d]

    # ---------------------------------------------------------- lifecycle
    def start_radio(self, requester: str = "auto", acting: str = "-", reclaim: bool = False) -> str:
        """Claim the radios for receive (when the broker lends them) and
        start the engine. '' or why not; never transmits. The HackRF is
        claimed at start too: the engine opens it with the receiver.
        `reclaim` re-takes claims the broker lost when it restarted
        (CONTROLLER-CONTRACT 7.2)."""
        taken = []
        if self.broker is not None:
            for dev in self.devices():
                why = self.broker.claim(dev, "rx", requester=requester, acting=acting,
                                        reason=_("Receive with {app}").format(app=NAME), reclaim=reclaim)
                if why:
                    for d in taken:
                        self.broker.release(d)
                    self.radio_error = why
                    return why
                taken.append(dev)
                self._apply_access(dev)
        try:
            self.radio.start()
        except Exception as e:  # noqa: BLE001  a radio that will not open is a status, not a crash
            self.radio_error = str(e)
            for d in taken:
                self.broker.release(d)
            return self.radio_error
        self.radio_error = ""
        self.reclaim = None
        return ""

    def _apply_access(self, dev: str) -> None:
        """The broker says where the radio is: a Pluto's network address, a
        HackRF's USB serial."""
        with self.broker.lock:
            access = dict((self.broker.claims.get(dev) or {}).get("access") or {})
        cls = dev.rsplit("-", 1)[0]
        change = {}
        if cls == "pluto" and access.get("reach") == "network" and access.get("address"):
            change["plutoUri"] = f"ip:{access['address']}"
        elif cls == "hackrf" and access.get("serial"):
            change["hackrfSerial"] = str(access["serial"])
        if change:
            try:
                self.radio.set(authorise=True, **change)
            except ValueError as e:
                log.warning("broker address not taken: %s", e)

    def stop_radio(self, why: str = "") -> None:
        self.end_lease(why)
        devs = self.devices()
        self.radio.stop()
        if self.broker is not None:
            for dev in devs:
                self.broker.release(dev)

    def serve(self, host: str = "0.0.0.0", port: int = 8080) -> ThreadingHTTPServer:
        server = self

        class Handler(_Handler):
            srv = server

        self.httpd = ThreadingHTTPServer((host, port), Handler)
        self.httpd.daemon_threads = True
        for name, fn in (("lease", self._lease_loop), ("broker", self._broker_loop),
                         ("http", self.httpd.serve_forever)):
            t = threading.Thread(target=fn, name=f"server-{name}", daemon=True)
            t.start()
            self.threads.append(t)
        return self.httpd

    def shutdown(self) -> None:
        self.stop_ev.set()
        for hub in self.pictures.values():
            hub.close()
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
        self.stop_radio(_("the app is stopping"))

    # ---------------------------------------------------------- status
    def status(self) -> dict:
        st = self.radio.state()
        with self.lock:
            lease = self.lease
            remote = None if lease is None else {
                "id": lease["id"], "leaseSeconds": LEASE_S,
                "secondsLeft": max(0, round(LEASE_S - (self.clock() - lease["last"])))}
        return {"app": APP_ID, "kind": KIND, "name": NAME, "version": self.version, "commit": self.commit,
                "running": bool(st.get("running")), "onAir": bool(st.get("keyed")),
                "txError": self.tx_error, "txRefused": st.get("txRefused", []),
                "txWarnings": st.get("txWarnings", []),
                # tx.remote's presence says this server has the lease (GUIDE 2:
                # clients refuse to transmit through one without it).
                "tx": {"remote": remote, "leaseSeconds": LEASE_S},
                "radioError": self.radio_error or st.get("error", ""),
                # True while a claim the broker lost is being taken back:
                # radioError is then a passing state, not a fault.
                "reconnecting": self.reclaim is not None,
                "device": st.get("device"), "txDevice": st.get("txDevice"), "band": st.get("band"),
                "system": st.get("system"), "videoFormat": st.get("videoFormat"),
                "rxFreq": st.get("rxFreq"), "txFreq": st.get("txFreq"), "rxLock": st.get("rxLock"),
                "snr": st.get("snr"), "cableFeed": bool(st.get("cableFeed")),
                "simulator": st.get("device") == "sim",
                "broker": self.broker.view() if self.broker is not None else None,
                "audio": {**FORMAT, "listeners": self.audio.listeners()},
                "pictures": {k: {"watchers": h.count()} for k, h in self.pictures.items()},
                "station": {"callsign": st.get("callsign", ""), "locator": st.get("locator", "")}}

    # ---------------------------------------------------------- transmit
    def key(self, remote: str, acting: str) -> None:
        """Key under `remote`'s lease. Sets tx_error and leaves the radio
        unkeyed on any refusal."""
        with self.lock:
            self.tx_error = ""
            if self.lease is not None and self.lease["id"] != remote and self.radio.state().get("keyed"):
                self.tx_error = _("Another remote is transmitting")
                return
            if self.radio.state().get("keyed"):
                # Keyed already (by this remote): a repeated key-up renews
                # its lease rather than refusing it.
                self.lease = {"id": remote, "last": self.clock(), "device": self.tx_device()}
                return
            # The engine's guard first, so a refusal the engine would give
            # never troubles the broker; it runs again inside ptt().
            if not self.radio.state().get("running"):
                self.tx_error = _("The radio is not running")
                return
            refused = self.radio.state().get("txRefused") or []
            if refused:
                self.tx_error = refused[0]
                return
            dev = self.tx_device()
            if self.broker is not None and dev:
                why = self.broker.claim(dev, "tx", requester="person", acting=acting,
                                        reason=_("Transmit with {app}").format(app=NAME))
                if not why:
                    why = self.broker.on_air(dev, onair_body(self.radio), acting)
                if why:
                    self.tx_error = why
                    self._back_to_rx(dev)
                    return
            self._toasts.drain()
            try:
                self.radio.ptt(True)
            except (TxRefused, ValueError) as e:
                self.tx_error = str(e)
                if self.broker is not None and dev:
                    self.broker.off_air(dev)
                    self._back_to_rx(dev)
                return
            self.lease = {"id": remote, "last": self.clock(), "device": dev}

    def _back_to_rx(self, dev: str) -> None:
        if self.broker is not None and dev and self.broker.held(dev, "tx"):
            self.broker.set_mode(dev, "rx")

    def _rf_off(self, why: str) -> None:
        # Radio._unkey is the engine's one unkey path; ptt(False) is it with
        # the reason "released". Called directly with the reason so the
        # transmit log (ENGINE-API tx_log) records why the server stopped
        # the over - a lost remote, a halt, the controller - which is the
        # first thing anyone reading it after an incident wants. The engine
        # also toasts "Transmit stopped: <why>", so `why` is the reason
        # alone, never a whole "Transmit stopped" sentence.
        if why:
            self.radio._unkey(why)
        else:
            self.radio.ptt(False)

    def end_lease(self, why: str = "") -> None:
        """RF off first, then the broker told; the lease ends. `why`, when
        given, is the reason (a fragment: "stopped by brian"), which becomes
        txError as "Transmit stopped: <why>" only if something was keyed."""
        with self.lock:
            lease, self.lease = self.lease, None
            keyed = bool(self.radio.state().get("keyed"))
            if keyed:
                self._rf_off(why)
                if why:
                    self.tx_error = _("Transmit stopped: {why}").format(why=why)
            dev = lease["device"] if lease else self.tx_device()
        if self.broker is not None and dev and (keyed or lease):
            # Radio._unkey returns with the transmitter stopped (it joins the
            # tx thread), so nothing is on the air when the broker hears.
            self.broker.off_air(dev)
            self._back_to_rx(dev)

    def heartbeat(self, remote: str) -> tuple[int, dict]:
        with self.lock:
            keyed = bool(self.radio.state().get("keyed"))
            if self.lease is None or not keyed:
                return 409, {"error": _("Not transmitting under a remote lease"), "code": "not_lease_holder",
                             "onAir": keyed, "txError": self.tx_error}
            if self.lease["id"] != remote:
                return 409, {"error": _("Transmit is held by another remote"), "code": "not_lease_holder",
                             "onAir": True, "txError": ""}
            self.lease["last"] = self.clock()
            self.radio.heartbeat()
            return 200, {"ok": True, "remote": {"id": remote, "secondsLeft": LEASE_S, "leaseSeconds": LEASE_S}}

    def _lease_loop(self) -> None:
        while not self.stop_ev.wait(0.25):
            with self.lock:
                lease = self.lease
                keyed = bool(self.radio.state().get("keyed"))
            if lease is None:
                continue
            if not keyed:
                # Unkeyed elsewhere (time-out, watchdog, the radio moved, a
                # lost lock): the lease ends, the broker is told, and the
                # engine's reason becomes txError.
                why = next((t["text"] for t in reversed(self._toasts.drain())
                            if t.get("level") in ("warn", "error")), "")
                self.end_lease("")
                if why:
                    self.tx_error = why
                continue
            if self.clock() - lease["last"] > LEASE_S:
                self.end_lease(_("remote controller lost (no word from {id} for {s:.0f} s)").format(
                    id=lease["id"], s=LEASE_S))
            else:
                # The engine's watchdog (watchdogSec) wants a sign of life
                # while keyed; the client's heartbeats are that sign, passed
                # on here between them.
                self.radio.heartbeat()

    def _broker_loop(self) -> None:
        while not self.stop_ev.wait(0.5):
            if self.broker is None:
                continue
            try:
                lost, asked = self.broker.beat()
            except Exception:  # noqa: BLE001  the loop must outlive one bad pass
                log.exception("broker heartbeat")
                continue
            mine = self.devices()
            for dev, why in lost:
                if dev in mine:
                    self._lost_claim(dev, why, mine)
            self._reclaim_step()
            for dev, _req in asked:
                if dev in mine and not self.radio.state().get("keyed"):
                    self.wanted = False
                    self.stop_radio("")
                    self.radio_error = _("The radio was handed to another app")

    def _lost_claim(self, dev: str, why: str, mine: list[str]) -> None:
        """The broker no longer has our claim on `dev` (a heartbeat answered
        404/410, or the controller was silent for longer than the lease).
        RF off and the engine stopped at once, whatever happens next: the
        radio is not ours to use, and the engine runs both radios together,
        so the other one's claim goes back too. But a lost claim is not a
        hand-over. The usual cause is the broker restarting (a platform
        update restarts the controller, and its claims live in memory), and
        until 10/10 this set wanted = False, so a box sat stopped for hours
        after an update while WB and Flex, which re-claim, came back (seen
        on QO-100 NB, whose code this is; openhamshack-nb 9747285). A person's
        stop and a hand-over (release_request, or releaseRequested on a
        heartbeat) are what end wanting the radio; this only re-claims it.
        A broker restart loses both of ATV's claims in one pass, so this
        can run twice; the second finds nothing left to stop."""
        with self.lock:
            self.lease = None
            if self.radio.state().get("keyed"):
                self._rf_off(why)
                self.tx_error = _("Transmit stopped: {why}").format(why=why)
        self.radio.stop()
        for other in mine:
            if other != dev:
                self.broker.release(other)
        log.warning("%s", why)
        if not self.wanted:
            self.radio_error = why
            return
        if self.reclaim is None:
            self.reclaim = {"device": dev, "at": self.clock(), "delay": RECLAIM_FIRST_S}
        self.radio_error = _("Reconnecting to the station…")

    def _reclaim_step(self) -> None:
        """One try at re-taking lost claims, when one is due. Backs off from
        RECLAIM_FIRST_S to RECLAIM_MAX_S, or waits the broker's own
        retryAfter (503 broker_restarting during its 15 s grace). Always in
        receive: a transmission the loss ended is never resumed by itself."""
        r = self.reclaim
        if r is None:
            return
        if not self.wanted or r["device"] not in self.devices() or self.radio.state().get("running"):
            # A person stopped it, it was handed over, they picked another
            # radio, or a person's start got there first.
            self.reclaim = None
            return
        if self.clock() < r["at"]:
            return
        why = self.start_radio(reclaim=True)
        if not why:
            log.info("re-claimed %s", ", ".join(self.devices()))
            return
        refused = self.broker.refused
        if refused is None or refused["code"] in ("device_held", "device_on_air", "queued"):
            # The claims were granted and the radio itself would not open,
            # or another app holds a radio now: no longer a reconnection.
            # Still wanted, so run.py's retry carries on as it does at
            # start-up, and radioError says what is wrong.
            self.reclaim = None
            return
        wait = refused["retryAfter"] if refused["retryAfter"] else r["delay"]
        r["delay"] = min(r["delay"] * 2, RECLAIM_MAX_S)
        r["at"] = self.clock() + max(wait, 0.5)
        log.info("re-claim of %s: %s; next try in %.0f s", r["device"], why, wait)
        self.radio_error = _("Reconnecting to the station…")

    def release_request(self, body: dict) -> tuple[int, dict]:
        """The broker asks for a radio back (CONTROLLER-CONTRACT 7.5)."""
        if self.radio.state().get("keyed"):
            if body.get("stop"):
                self.end_lease(_("stopped by the controller"))
            else:
                return 409, {"onAir": True}
        self.wanted = False
        self.stop_radio("")
        self.radio_error = _("The radio was handed to another app")
        return 202, {"released": True}

    # ---------------------------------------------------------- settings
    def needs_for(self, changes: dict) -> str:
        """The level a set() of `changes` needs: Control for receive-side
        keys, and for tuning while nobody transmits; Transmit otherwise."""
        keyed = bool(self.radio.state().get("keyed"))
        control = RECEIVE_KEYS | (frozenset() if keyed else TUNING_KEYS)
        return "control" if set(changes) <= control else "transmit"


# ---------------------------------------------------------------------- HTTP
class _Handler(BaseHTTPRequestHandler):
    srv: Server = None             # set per server
    protocol_version = "HTTP/1.1"
    server_version = "OpenHamShack"

    def log_message(self, fmt, *args):
        log.debug("%s %s", self.address_string(), fmt % args)

    # ---------------------------------------------------------- plumbing
    def _send(self, status: int, body, ctype: str = "application/json", extra: dict | None = None) -> None:
        raw = body if isinstance(body, bytes) else dumps(body)
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def _error(self, status: int, text: str, code: str, **more) -> None:
        self._send(status, {"error": text, "code": code, **more})

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        if n > 1 << 20:
            raise ValueError(_("The request is too large"))
        try:
            data = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            raise ValueError(_("The request is not JSON")) from None
        if not isinstance(data, dict):
            raise ValueError(_("The request must be a JSON object"))
        return data

    def _who(self) -> dict | None:
        return shack.shack_identity(self.headers.get, self.srv.secret)

    def _need(self, who: dict, level: str) -> bool:
        if shack.level_at_least(who["level"], level):
            return True
        names = {"view": _("View"), "control": _("Control"), "transmit": _("Transmit")}
        self._error(403, _("This needs the {needs} level; your level for {app} is {level}.").format(
            needs=names.get(level, level), app=NAME, level=names.get(who["level"], who["level"])),
            "level_too_low", level=who["level"], needs=level)
        return False

    # ---------------------------------------------------------- routing
    def do_GET(self):
        self._route("GET")

    def do_HEAD(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def _route(self, method: str) -> None:
        u = urlsplit(self.path)
        path, query = u.path.rstrip("/") or "/", parse_qs(u.query)
        who = self._who()
        if who is None:
            self._error(401, _("This app answers only through the OpenHamShack controller."), "proxy_required")
            return
        if who["level"] == "none":
            # The broker's release request and the busy gate are the
            # platform itself, and reach only their own two routes.
            if path == "/api/shack/release" and method == "POST":
                status, ans = self.srv.release_request(self._safe_body() or {})
                self._send(status, ans)
            elif path == "/api/shack/busy":
                self._send(200, self._busy())
            else:
                self._error(403, _("This needs the View level."), "level_too_low", level="none", needs="view")
            return
        try:
            handler = ROUTES.get((method, path))
            if handler is None:
                self._error(404, _("There is nothing at {path}").format(path=path), "not_found")
                return
            need, fn = handler
            if not self._need(who, need):
                return
            fn(self, who, query)
        except ValueError as e:
            self._error(400, str(e), "bad_request")
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _safe_body(self):
        try:
            return self._body()
        except ValueError:
            return None

    def _busy(self) -> dict:
        keyed = bool(self.srv.radio.state().get("keyed"))
        return {"busy": keyed, "why": _("{app} is transmitting").format(app=NAME) if keyed else ""}

    # ---------------------------------------------------------- pages
    def page(self, who, q):
        """A minimal status page at the base path: the app is a server for
        OpenHamShack Remote (D8), and a person who opens it in a browser
        sees what it is and how it is, with no script and nothing fetched
        from anywhere. The picture is the app's own MJPEG, by a relative
        URL (never an absolute one: the controller's prefix is not ours)."""
        s = self.srv.status()
        rows = [(_("Radio"), s["device"] or ""), (_("Running"), _("yes") if s["running"] else _("no")),
                (_("On the air"), _("yes") if s["onAir"] else _("no")),
                (_("Receiving"), f"{(s['rxFreq'] or 0) / 1e6:.3f} MHz {str(s['system'] or '').upper()}"),
                (_("Picture"), str(s["rxLock"] or "")),
                (_("Problem"), s["radioError"] or s["txError"] or "-")]
        body = "".join(f"<tr><th>{html.escape(k)}</th><td>{html.escape(str(v))}</td></tr>" for k, v in rows)
        title = html.escape(_("OpenHamShack ATV"))
        prefix = self.headers.get("X-Forwarded-Prefix", "")
        base = html.escape(prefix.rstrip("/") if PREFIX_RE.match(prefix) else "")
        doc = (f"<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width'>"
               f"<title>{title}</title><style>body{{font:15px system-ui,sans-serif;margin:16px;color:#111;"
               f"background:#fff}}th{{text-align:left;padding:4px 16px 4px 0}}img{{max-width:100%;"
               f"aspect-ratio:4/3;background:#000}}@media(prefers-color-scheme:dark)"
               f"{{body{{background:#111;color:#eee}}}}</style><h1>{title}</h1><p>"
               + html.escape(_("Use OpenHamShack Remote to operate this app. This page shows how it is."))
               + f"</p><img alt='' src='{base}/api/video/rx.mjpeg'><table>{body}</table>"
               f"<p>{html.escape(s['version'])}</p></html>")
        self._send(200, doc.encode(), "text/html; charset=utf-8")

    def get_status(self, who, q):
        self._send(200, self.srv.status())

    def get_state(self, who, q):
        self._send(200, public_state(self.srv.radio.state()))

    def get_spec(self, who, q):
        from .. import state
        self._send(200, {k: {f: v for f, v in s.items() if f in ("type", "kind", "default", "min", "max", "choices",
                                                                  "unit", "help", "auth", "restart")}
                         for k, s in state.SPEC.items() if k not in SECRET_KEYS})

    def get_bands(self, who, q):
        """The bandplan the engine guards with (ohsatv/bands.py), for a
        client's band strip and channel list; Band I and UHF are the cable
        feed's."""
        self._send(200, {"bands": [bands.BY_NAME[n] for n in bands.BY_NAME], "cable": list(bands.CABLE_NAMES)})

    def post_set(self, who, q):
        body = self._body()
        changes = body.get("changes") if isinstance(body.get("changes"), dict) else body
        owned = sorted(PROFILE_KEYS & set(changes))
        if owned:
            raise ValueError(_("{keys}: set in the station profile on the controller, not here").format(
                keys=", ".join(owned)))
        plat = sorted(PLATFORM_KEYS & set(changes))
        if plat:
            raise ValueError(_("{keys}: the controller looks after these").format(keys=", ".join(plat)))
        from .. import state
        auth = any(state.SPEC.get(k, {}).get("auth") for k in changes)
        if not self._need(who, "transmit" if auth else self.srv.needs_for(changes)):
            return
        changed = self.srv.radio.set(authorise=auth, **changes)
        self._send(200, {"changed": public_state(changed)})

    def post_start(self, who, q):
        self.srv.wanted = True
        why = (self.srv.start_radio("person", who["device"], reclaim=self.srv.reclaim is not None)
               if not self.srv.radio.state().get("running") else "")
        self._send(200, {**self.srv.status(), "startError": why})

    def post_stop(self, who, q):
        if self.srv.radio.state().get("keyed") and not self._need(who, "transmit"):
            return            # stopping the radio would end somebody's over: halt is the way for Control
        self.srv.wanted = False
        self.srv.stop_radio(_("stopped by {user}").format(user=who["user"]))
        self._send(200, self.srv.status())

    def post_ptt(self, who, q):
        body = self._body()
        if body.get("on"):
            if not self._need(who, "transmit"):
                return
            remote = str(body.get("remote") or "")
            if not REMOTE_RE.match(remote):
                raise ValueError(_("remote must be 1 to 64 letters, digits, '.', '_', ':' or '-'"))
            self.srv.key(remote, who["device"])
        else:
            self.srv.end_lease("")
            self.srv.tx_error = ""
        self._send(200, self.srv.status())

    def post_heartbeat(self, who, q):
        body = self._body()
        status, ans = self.srv.heartbeat(str(body.get("remote") or ""))
        self._send(status, ans)

    def post_halt(self, who, q):
        # Stopping a transmission needs no transmit level: anyone who may
        # change anything may stop it.
        self.srv.end_lease(_("stopped by {user}").format(user=who["user"]))
        self._send(200, self.srv.status())

    def post_accept_radio(self, who, q):
        self.srv.radio.accept_radio()
        self._send(200, self.srv.status())

    def get_txlog(self, who, q):
        self._send(200, {"entries": self.srv.radio.tx_log()})

    def post_record(self, who, q):
        body = self._body()
        secs = body.get("seconds")
        path = self.srv.radio.record_iq(bool(body.get("on")), float(secs) if secs else None)
        self._send(200, {"file": os.path.basename(path), "recording": bool(self.srv.radio.state().get("recording"))})

    def post_caption(self, who, q):
        self.srv.radio.caption(str(self._body().get("text", "")))
        self._send(200, {"ok": True})

    def get_ttx_pages(self, who, q):
        self._send(200, {"pages": self.srv.radio.ttx_pages(), "header": self.srv.radio.ttx_header()})

    def get_ttx_page(self, who, q):
        label = (q.get("label") or [""])[0]
        sub = (q.get("sub") or [""])[0]
        page = self.srv.radio.ttx_page(label, int(sub, 16) if sub else None)
        if page is None:
            self._error(404, _("Page {label} has not been received").format(label=label), "not_found")
            return
        self._send(200, page)

    # ---------------------------------------------------------- streams
    def ws_events(self, who, q):
        """The engine's feeds as JSON messages, one per item:
        {"kind": "state" | "toast" | ..., ...item}. ?kinds=a,b picks them;
        spectrum and scope are offered but not sent unless asked, pictures
        never (their MJPEG routes)."""
        radio = self.srv.radio
        kinds = [k for k in (q.get("kinds", [",".join(FEED_KINDS_DEFAULT)])[0].split(","))
                 if k in FEEDS and k not in NOT_OVER_EVENTS]
        if not ws.handshake(self):
            return
        sock = ws.Socket(self.rfile, self.wfile)
        feeds = {k: radio.subscribe(k, 256 if k not in SPECTRUM + ("scope",) else 4) for k in kinds}
        gone = threading.Event()

        def reader():
            try:
                while sock.recv() is not None:
                    pass
            except (OSError, ConnectionError):
                pass
            gone.set()
        threading.Thread(target=reader, daemon=True).start()
        try:
            sock.send_text(json.dumps({"kind": "hello", "status": self.srv.status()}, default=_json_default))
            while not gone.wait(0.05):
                for k, f in feeds.items():
                    for item in f.drain():
                        msg = feed_message(k, item)
                        if k == "state":
                            msg["changes"] = public_state(msg.get("changes") or {})
                        sock.send_text(json.dumps(msg, default=_json_default))
        except (OSError, ConnectionError):
            pass
        finally:
            for f in feeds.values():
                radio.unsubscribe(f)
            sock.close()

    def _mjpeg(self, hub: pictures.PictureHub) -> None:
        """A picture as multipart/x-mixed-replace JPEG frames over a
        long-lived response (pictures.py says why not a video stream)."""
        if not pictures.have_pillow():
            self._error(503, _("Pictures need Pillow (Debian: python3-pil), which is not installed"),
                        "service_unavailable")
            return
        lq = hub.listen()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            while not self.srv.stop_ev.is_set():
                try:
                    jpeg = lq.get(timeout=1.0)
                except queue.Empty:
                    continue
                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                 + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n")
                self.wfile.flush()
        except (OSError, ConnectionError):
            pass
        finally:
            hub.unlisten(lq)

    def get_rx_mjpeg(self, who, q):
        self._mjpeg(self.srv.pictures["rx"])

    def get_tx_mjpeg(self, who, q):
        self._mjpeg(self.srv.pictures["tx"])

    def get_rx_jpeg(self, who, q):
        """One received picture: the newest, waiting up to 2 s for one. 404
        while nothing is decoded (the receiver hands out no picture while it
        searches: rxLock in the status says so)."""
        if not pictures.have_pillow():
            self._error(503, _("Pictures need Pillow (Debian: python3-pil), which is not installed"),
                        "service_unavailable")
            return
        hub = self.srv.pictures["rx"]
        lq = hub.listen()
        try:
            jpeg = lq.get(timeout=2.0)
        except queue.Empty:
            jpeg = b""
        finally:
            hub.unlisten(lq)
        if not jpeg:
            self._error(404, _("No picture is being received"), "not_found")
            return
        self._send(200, jpeg, "image/jpeg")

    def get_audio_rx(self, who, q):
        """Receive sound as raw PCM over a long-lived response, its format
        in the headers (GUIDE 2: announced up front)."""
        a = self.srv.audio
        lq = a.listen()
        try:
            self.send_response(200)
            self.send_header("Content-Type", f"audio/L16; rate={FORMAT['rate']}; channels=1")
            self.send_header("X-Audio-Format", json.dumps(FORMAT))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            while not self.srv.stop_ev.is_set():
                try:
                    pcm = lq.get(timeout=IDLE_AUDIO_S)
                except queue.Empty:
                    # The engine is not playing: send that much silence. A
                    # write is the only way this thread learns the client
                    # has gone; sending nothing while the radio was stopped
                    # kept every dead listener (and its thread) for good -
                    # QO-100 NB on ohs-box showed 20 from one TV reconnecting
                    # every 7 s (openhamshack-nb a750f32). Silence also keeps a
                    # client's player fed, like the video stream's null
                    # packets.
                    pcm = IDLE_SILENCE
                self.wfile.write(pcm)
        except (OSError, ConnectionError):
            pass
        finally:
            a.unlisten(lq)

    def ws_audio_tx(self, who, q):
        """A remote microphone: binary frames of PCM in FORMAT, the sound of
        a transmission whose audioSource is `mic` while this socket is
        open. Transmit level, like keying."""
        if not ws.handshake(self):
            return
        sock = ws.Socket(self.rfile, self.wfile)
        a = self.srv.audio
        a.mic_writer = who["device"]
        try:
            sock.send_text(json.dumps({"kind": "hello", "format": FORMAT}))
            while True:
                fr = sock.recv()
                if fr is None:
                    break
                op, data = fr
                if op == 0x2:
                    a.feed_mic(data)
        except (OSError, ConnectionError):
            pass
        finally:
            if a.mic_writer == who["device"]:
                a.mic_writer = ""
            sock.close()

    def get_shack_busy(self, who, q):
        self._send(200, self._busy())

    def post_shack_release(self, who, q):
        status, ans = self.srv.release_request(self._body())
        self._send(status, ans)


ROUTES = {
    ("GET", "/"): ("view", _Handler.page),
    ("GET", "/api/status"): ("view", _Handler.get_status),
    ("GET", "/api/state"): ("view", _Handler.get_state),
    ("GET", "/api/spec"): ("view", _Handler.get_spec),
    ("GET", "/api/bands"): ("view", _Handler.get_bands),
    ("POST", "/api/set"): ("control", _Handler.post_set),
    ("POST", "/api/start"): ("control", _Handler.post_start),
    ("POST", "/api/stop"): ("control", _Handler.post_stop),
    ("POST", "/api/ptt"): ("control", _Handler.post_ptt),
    ("POST", "/api/ptt/heartbeat"): ("transmit", _Handler.post_heartbeat),
    ("POST", "/api/halt"): ("control", _Handler.post_halt),
    ("POST", "/api/accept-radio"): ("transmit", _Handler.post_accept_radio),
    ("GET", "/api/txlog"): ("view", _Handler.get_txlog),
    ("POST", "/api/record"): ("control", _Handler.post_record),
    ("POST", "/api/caption"): ("transmit", _Handler.post_caption),
    ("GET", "/api/teletext/pages"): ("view", _Handler.get_ttx_pages),
    ("GET", "/api/teletext/page"): ("view", _Handler.get_ttx_page),
    ("GET", "/api/events"): ("view", _Handler.ws_events),
    ("GET", "/api/video/rx.mjpeg"): ("view", _Handler.get_rx_mjpeg),
    ("GET", "/api/video/rx.jpg"): ("view", _Handler.get_rx_jpeg),
    ("GET", "/api/video/tx.mjpeg"): ("view", _Handler.get_tx_mjpeg),
    ("GET", "/api/audio/rx"): ("view", _Handler.get_audio_rx),
    ("GET", "/api/audio/tx"): ("transmit", _Handler.ws_audio_tx),
    ("GET", "/api/shack/busy"): ("view", _Handler.get_shack_busy),
    ("POST", "/api/shack/release"): ("view", _Handler.post_shack_release),
}
