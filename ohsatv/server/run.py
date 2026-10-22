"""`--server`: run ATV's engine headless behind the API.

What differs from the desktop: no window, no sound card (StreamAudio), the
settings and transmit log on the data volume (OHSATV_CONF, OHSATV_DATA), and
- on an OpenHamShack controller, where the edition puts DOCK360_SHACK_URL
and DOCK360_SHACK_TOKEN in the environment - the broker lends the radios
and the station profile supplies the callsign, locator and licence answer.

Without those variables it still runs, for a developer: no broker, the
callsign and licence acknowledgement from the config file. Without
DOCK360_SHACK_PROXY_SECRET every request is refused (401 proxy_required):
nobody can reach a transmitter through a server that cannot tell who is
asking. A developer sets the secret and sends it in the header, as the
proxy would.
"""
from __future__ import annotations

import logging
import os
import signal
import threading
import time
from pathlib import Path

from ..i18n.text import _
from .. import station
from . import shack
from .app import Server
from .audio import StreamAudio

log = logging.getLogger("ohsatv.server")
PROFILE_EVERY_S = 30.0
RETRY_S = 10.0


def platform_request():
    url, token = os.environ.get(shack.SHACK_URL_ENV, ""), os.environ.get(shack.SHACK_TOKEN_ENV, "")
    if not url or not token:
        return None
    return shack.shack_http(url, token)


def apply_profile(radio, request) -> str:
    """Take the station's callsign, locator and licence answer from the
    controller's profile (SERVICES.md 2.2) into the engine; '' or why not.

    The callsign is drawn into every transmitted picture, so it must be the
    station's. `licenceAck` - the window's "I have read the licence
    conditions" - becomes the profile's derived.canTransmit: on a
    controller the licence class is the profile's, and the broker checks the
    same profile again before every key-up (CONTROLLER-CONTRACT 7.4).
    """
    try:
        code, prof = request("GET", "/profile")
    except OSError as e:
        return _("The controller's station profile did not answer ({why})").format(why=e)
    if code != 200:
        return _("The controller's station profile answered HTTP {code}").format(code=code)
    derived = prof.get("derived") or {}
    # The country, licence regime and class and the station conditions are
    # what the engine's own band check (txguard) reads; without them a
    # store app with a real radio stays locked ("No licence class chosen")
    # however the controller's profile is set. They are the same mapping
    # the desktop form's StationLink uses, and they are auth settings, so
    # this applies them as Settings would: on a controller the profile is
    # the only place they change (app.PROFILE_KEYS).
    change = station.profile_to_settings(prof)
    change.update({"callsign": str(prof.get("callsign") or "").strip().upper(),
                   "locator": str(prof.get("locator") or "").strip(),
                   "licenceAck": bool(derived.get("canTransmit"))})
    st = radio.state()
    change = {k: v for k, v in change.items() if st.get(k) != v}
    if change:
        try:
            radio.set(authorise=True, **change)
        except ValueError as e:
            return str(e)
    return ""


def main(radio_factory, version: str, commit: str = "", host: str = "0.0.0.0", port: int | None = None) -> int:
    port = port if port is not None else int(os.environ.get("SHACK_PORT", "8080"))
    # The radio lock's folder on this computer: inside a container that is
    # the container's own, so it guards only this process; between apps on
    # the controller the broker decides, and the Pluto-side marker (over
    # ssh) still guards against desktop apps on other computers.
    lock_dir = os.environ.get("OHSRADIO_LOCK_DIR")
    if lock_dir:
        Path(lock_dir).mkdir(parents=True, exist_ok=True)
    audio = StreamAudio()
    radio = radio_factory(audio)
    request = platform_request()
    srv = Server(radio, audio, version=version, commit=commit,
                 broker=shack.BrokerClient(request) if request else None)
    if not srv.secret:
        log.warning("%s is not set: every request will be refused", shack.PROXY_SECRET_ENV)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_a: stop.set())
    signal.signal(signal.SIGINT, lambda *_a: stop.set())
    srv.serve(host, port)
    print(f"OpenHamShack ATV {version} serving on {host}:{port}", flush=True)
    if request:
        err = apply_profile(radio, request)
        if err:
            log.info("%s", err)
    why = srv.start_radio()
    if why:
        print(f"radio not started: {why}", flush=True)
    next_profile = time.monotonic() + PROFILE_EVERY_S
    retry_at = time.monotonic() + RETRY_S
    try:
        while not stop.wait(1.0):
            now = time.monotonic()
            if request and now >= next_profile:
                next_profile = now + PROFILE_EVERY_S
                err = apply_profile(radio, request)
                if err:
                    log.info("%s", err)
            # A radio that failed or never opened is tried again every
            # 10 s, unless a person stopped it or it was handed to another
            # app (Server.wanted). While the broker loop is re-claiming a
            # claim the broker lost (Server.reclaim) it owns the retries:
            # a plain claim here would only meet 503 broker_restarting.
            if srv.wanted and srv.reclaim is None and not radio.state().get("running") and now >= retry_at:
                retry_at = now + RETRY_S
                srv.start_radio()
    finally:
        srv.shutdown()
    return 0
