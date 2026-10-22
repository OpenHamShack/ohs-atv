"""Configuration: load, validate, save atomically.

The file holds the persisted state keys (ohsatv/state.py). Copied from
OpenHamShack NB. A key that fails validation on load falls back to its default
with a note rather than stopping the program: a hand-edited typo should cost
one setting, not the radio.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from . import bands, host, state


def conf_path() -> Path:
    """$OHSATV_CONF, else ~/.config/openhamshack-atv/config.json (Windows:
    %APPDATA%\\openhamshack-atv\\config.json - ohsatv/host.py says why there)."""
    env = os.environ.get("OHSATV_CONF")
    if env:
        return Path(env)
    return host.conf_dir() / "config.json"


def data_dir() -> Path:
    env = os.environ.get("OHSATV_DATA")
    return Path(env) if env else host.data_dir()


def defaults() -> dict:
    return {k: state.SPEC[k]["default"] for k in state.persisted()}


def clean(raw: dict, notes: list[str] | None = None) -> dict:
    """Validated config from an arbitrary dict: unknown keys dropped, bad
    values replaced by defaults (and described in `notes`)."""
    cfg = defaults()
    notes = notes if notes is not None else []
    for k, v in (raw or {}).items():
        if k not in state.SPEC or not state.SPEC[k]["persist"]:
            continue
        try:
            cfg[k] = state.coerce(k, v)
        except ValueError as e:
            notes.append(f"{e}; using {state.SPEC[k]['default']!r}")
    # Cross-key rules: the gain ceiling wins over a stored gain, never the
    # other way round; a frequency outside its band goes back to the band's
    # default channel.
    if cfg["txGain"] > cfg["txGainCeiling"]:
        cfg["txGain"] = cfg["txGainCeiling"]
    # Band I and UHF are the cable feed's, and the cable feed is off at every
    # start (ohsatv/state.py): a config saved while on one comes back on 23cm.
    if cfg["band"] in bands.CABLE_NAMES:
        cfg["band"] = state.SPEC["band"]["default"]
    b = bands.BY_NAME[cfg["band"]]
    for key in ("rxFreq", "txFreq"):
        if not (b.lo <= cfg[key] <= b.hi):
            cfg[key] = b.default
    # Split off means transmit follows receive - from the first moment, not
    # only after the next set(): a config (or a test) giving only rxFreq
    # once left the transmitter on the old frequency, 6 MHz from where the
    # receiver was listening.
    if not cfg["split"]:
        cfg["txFreq"] = cfg["rxFreq"]
    return cfg


def load(path: Path | None = None, notes: list[str] | None = None) -> dict:
    path = path or conf_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raw = {}
    except (OSError, ValueError) as e:
        if notes is not None:
            notes.append(f"Could not read {path} ({e}); starting from defaults")
        raw = {}
    return clean(raw, notes)


def save(cfg: dict, path: Path | None = None) -> None:
    """Write via a temporary file in the same directory, fsync, rename: a
    crash or power cut mid-save leaves the old file or the new one, never a
    truncated one - a laptop on a field-day table is often switched off at the wall."""
    path = path or conf_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    out = {k: cfg[k] for k in state.persisted() if k in cfg}
    text = json.dumps(out, indent=2, sort_keys=True) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
