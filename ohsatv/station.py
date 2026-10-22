"""The station profile, kept on this computer, and the controller link.

BETA-PLAN architecture change 2: every app keeps its own station profile,
offline first, and when an OpenHamShack controller is present it pairs with
it and syncs. The store is ohs-libs' shack_client (SYNC.md, SERVICES.md):
one SQLite store per user per machine, shared by every OpenHamShack app on
it, so ATV, QO-100 NB and Radio on one laptop keep one profile and pair
once (SYNC.md section 2.1).

ATV keeps no logbook - it has never logged contacts, only its own transmit
log (Radio.tx_log), which is a safety record, not QSOs - so it uses only
the profile half of the store. The store's logbook is still there, written
by the other apps, which is why pairing can still ask what to do with two
logbooks (shack_client NeedsDecision).

What this module adds to shack_client is ATV's side, copied in shape from
openhamshack-nb ohscore/station.py (no code is shared at run time):

- Where the store is: the shared per-user path, except that an engine
  built with its own data folder (every test, `--data`) keeps its store
  there, and SHACK_STATION_DB overrides both (the store container).
- The profile kept equal to ATV's settings (callsign, locator, station
  country, licence, class, station conditions): settings the operator
  changes go into the profile, and a profile another app or the controller
  changed comes back into the settings. Only differing fields are written,
  so the two never echo each other, and a value ATV cannot hold (a licence
  ATV's dataset does not know, an operator's own licence) is left out
  rather than forced in.
- Pairing run off the caller's thread, and the status every app shows the
  same way (SYNC.md section 7).

A missing controller, or a missing or damaged store, never stops the radio:
the engine catches every failure here, and the profile is a copy.
"""
from __future__ import annotations

import logging
import os
import socket
import threading
from pathlib import Path

from . import shacklibs

log = logging.getLogger("ohsatv.station")

APP_ID = "shack-atv"
# settings key -> profile field, for the scalar ones. The licence is one
# profile field built from two settings (below).
SCALAR = {"callsign": "callsign", "locator": "locator", "stationCountry": "dxccPrefix"}
LICENCE_KEYS = ("licence", "licenceClass")
PROFILE_SETTINGS = tuple(SCALAR) + LICENCE_KEYS + ("stationConditions",)


def client():
    """shack_client, or None when it cannot be loaded."""
    return shacklibs.shack_lib("shack_client")


def store_path(data_dir: Path | None) -> Path:
    env = os.environ.get("SHACK_STATION_DB", "").strip()
    if env:
        return Path(env)
    if data_dir is not None:
        return Path(data_dir) / "station.db"
    client()
    from shack_client.store import default_path
    return default_path()


def open_station(version: str, data_dir: Path | None = None, background: bool = True):
    sc = client()
    if sc is None:
        raise OSError("shack-client is missing from this installation")
    path = store_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    return sc.Station.open(app=APP_ID, app_version=version, path=path, background=background)


# ------------------------------------------------------------------ profile <-> settings
def settings_to_profile(st: dict) -> dict:
    """The profile fields ATV's settings say."""
    out = {}
    for k, f in SCALAR.items():
        if k in st:
            v = st[k]
            out[f] = v.strip().upper() if isinstance(v, str) and f in ("callsign", "dxccPrefix") else v
    if "licence" in st or "licenceClass" in st:
        out["licence"] = {"regime": st.get("licence") or "auto", "class": st.get("licenceClass", "") or ""}
    if "stationConditions" in st:
        out["conditions"] = {k: bool(v) for k, v in (st.get("stationConditions") or {}).items()}
    return out


def profile_to_settings(p: dict) -> dict:
    """ATV's settings a profile says (only fields it has)."""
    out = {}
    for k, f in SCALAR.items():
        if f in p and p[f] is not None:
            out[k] = p[f]
    lic = p.get("licence")
    if isinstance(lic, dict):
        out["licence"] = lic.get("regime") or "auto"
        out["licenceClass"] = lic.get("class", "") or ""
    if isinstance(p.get("conditions"), dict):
        out["stationConditions"] = {k: bool(v) for k, v in p["conditions"].items()}
    return out


def _norm(v):
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, dict):
        return {k: _norm(x) for k, x in v.items()}
    return v


class StationLink:
    """One engine's view of the shared store. `engine` needs `st` and
    `set(authorise=..., **kw)`; it is called back on the store's watch
    thread."""

    def __init__(self, station, engine):
        from . import state
        self.station = station
        self.engine = engine
        self.state = state
        self._applying = threading.Lock()

    # ---------------------------------------------------------- profile
    def adopt(self) -> None:
        """At start: the profile wins where it says something, the settings
        fill what it does not (a first run after the upgrade puts the
        operator's callsign into the shared profile)."""
        prof = self.station.profile.get()
        mine = settings_to_profile(self.engine.st)
        missing = {f: v for f, v in mine.items() if f not in prof and v not in ("", None, [], {})}
        if missing.get("licence", {}).get("class") == "":
            missing.pop("licence")          # an empty class says nothing worth sharing
        if missing:
            self.station.profile.update(missing)
        self.pull()
        self.station.on_change(self._changed)

    def push(self, changed: dict) -> None:
        """Settings the operator changed -> the profile, field by field,
        only where the profile differs."""
        if self._applying.locked() or not any(k in changed for k in PROFILE_SETTINGS):
            return
        want = settings_to_profile({k: self.engine.st.get(k) for k in PROFILE_SETTINGS if k in self.engine.st})
        have = self.station.profile.get()
        if isinstance(have.get("licence"), dict) and "licence" in want:
            # The operator's own licence segments belong to the apps that
            # have them (NB, Radio); ATV has none and must not wipe them.
            if have["licence"].get("custom"):
                want["licence"]["custom"] = have["licence"]["custom"]
        diff = {f: v for f, v in want.items() if _norm(have.get(f)) != _norm(v)}
        if diff:
            try:
                self.station.profile.update(diff)
            except ValueError as e:              # a locator shack_client will not store
                log.warning("station profile not updated: %s", e)

    def pull(self) -> dict:
        """The profile -> the settings, where they differ. Returns what
        changed."""
        want = profile_to_settings(self.station.profile.get())
        st = self.engine.st
        clean = {}
        for k, v in want.items():
            if k not in self.state.SPEC or _norm(st.get(k)) == _norm(v):
                continue
            try:
                clean[k] = self.state.coerce(k, v)
            except ValueError as e:
                log.info("profile field for %s not taken: %s", k, e)
        if not clean:
            return {}
        with self._applying:
            try:
                return self.engine.set(authorise=True, **clean)
            except ValueError as e:
                log.info("profile not applied: %s", e)
                return {}

    def _changed(self, collection, _ids) -> None:
        if collection in (None, "profile"):
            try:
                self.pull()
            except Exception:  # noqa: BLE001  the watch thread must outlive a bad record
                log.warning("station profile not pulled", exc_info=True)

    # ---------------------------------------------------------- controller
    def status(self) -> dict:
        return self.station.sync.status()

    def pairing(self) -> dict | None:
        p = self.station.pairing_info()
        if p is None:
            return None
        return {k: p.get(k, "") for k in ("controllerId", "name", "baseUrl", "deviceId", "user", "maxLevel",
                                           "pairedAt")}

    def discover(self, timeout: float = 3.0) -> list:
        """Controllers on the LAN (_openhamshack._tcp); raises
        shack_client.DiscoveryUnavailable without python-zeroconf."""
        return client().discover(timeout=timeout)

    def pair(self, base_url: str, username: str, password: str, totp: str | None = None, device_name: str = ""):
        """Pairing or NeedsDecision; raises PairingError or Offline. Run it
        off the UI thread."""
        if not base_url.endswith("/"):
            base_url += "/"
        res = self.station.pair(base_url, username, password, totp=totp or None,
                                device_name=device_name or f"{socket.gethostname()} ({APP_ID})",
                                device_kind="desktop")
        self.pull()                 # the controller's profile, if the first exchange brought one
        return res

    def resolve(self, decision, choice: str):
        res = self.station.resolve(decision, choice)
        self.pull()
        return res

    def unpair(self) -> None:
        """Forget the controller. The profile stays here as it is."""
        self.station.unpair()

    def sync_now(self) -> None:
        self.station.sync.now()

    def close(self) -> None:
        """Close the store and wait for its watch thread, which would
        otherwise outlive it and read a closed (or deleted) file."""
        self.station.close()
        w = getattr(self.station, "_watch", None)
        if w is not None:
            w.join(timeout=3)
