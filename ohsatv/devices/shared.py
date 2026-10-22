"""A radio other programs may be using: taking it with the radio lock
(ohsatv/radiolock.py, OpenHamShack DATV's docs/radio-lock.md) before the first
write, and giving it back after the last.

Why the device takes the lock and not the engine: the lock must be held
before the first write to the radio, and only the device knows when that
is - a Pluto writes its transmit attenuation a few lines into open(), and
it cannot be named until its libiio context has said what its serial is.
The engine decides whether a device locks at all (`lock_radio`), what the
marker's note says (`lock_note`), and what happens when the lock is lost
(`on_lock_lost`); see Radio._prepare in ohsatv/radio.py. A device opened
outside the engine (the device tests, a bench script) takes no lock unless
asked: a test that left one open would otherwise hold its fake radio's
marker against every later test in the same process.

What the lock holds: the whole radio, from open() until close() has put
back what it changes (the Pluto's FIR) - receiving included, because a
receiver setting an AD936x's sample rate moves the transmitter's too.
"""
from __future__ import annotations

from pathlib import Path

from .. import radiolock
from .base import DeviceError

APP = "OpenHamShack ATV"
# The spec's 15 s at most; the tests shorten it to see a lost lock quickly.
BEAT_S = radiolock.BEAT_S


def version() -> str:
    try:
        return (Path(__file__).resolve().parent.parent / "VERSION").read_text().strip()
    except OSError:
        return ""


class RadioBusy(DeviceError):
    """The radio is another program's: the message is the spec's sentence
    (who, doing what, since when, how fresh, and that nothing was touched)
    and `holder` the marker it was read from, for the window."""

    def __init__(self, text: str, holder: dict | None = None):
        super().__init__(text)
        self.holder = holder or {}


def holder_of(lock: radiolock.RadioLock) -> dict:
    """The fresh marker that refused us, read again from whichever place
    has it: the refusal carries only the sentence, and the window shows the
    holder as fields. Reading is all this does - a marker is never written
    or removed here."""
    for place in lock.places:
        try:
            marker, age, exists = place.read()
        except OSError:
            continue
        if exists and marker:
            keep = ("radio", "app", "version", "host", "user", "use", "since", "note")
            out = {k: marker.get(k) for k in keep if marker.get(k) not in (None, "")}
            out["where"] = place.where
            if age is not None:
                out["ageS"] = round(age, 1)
            return out
    return {}


def take(dev, rid: str, label: str, ssh_host: str | None, use: str = "rx") -> list[str]:
    """Take the radio for `dev` (a Device with cfg, lock_note, notes and
    on_lock_lost): every place the spec says applies, or none. Raises
    RadioBusy with the spec's sentence if another program holds it. Returns
    the lock's notes (a stale lock taken over; a radio whose shell did not
    answer, so the lock covers this computer only), which are also added
    to the device's notes."""
    lock = radiolock.RadioLock(rid, label, radiolock.places_for(rid, ssh_host, dev.cfg), app=APP,
                               version=version(), beat=BEAT_S, on_lost=lambda why: _lost(dev, why))
    try:
        notes = lock.take(use, dev.lock_note or "")
    except radiolock.RadioBusy as e:
        raise RadioBusy(str(e), holder_of(lock)) from None
    dev.radio_lock = lock
    dev.lock_lost = ""
    dev.notes.extend(notes)
    return notes


def _lost(dev, why: str) -> None:
    """From the lock's heartbeat thread: the marker is gone or not ours.
    Whatever the device was doing to the radio is now done to somebody
    else's, so it must not put anything back at close (leave_as_is), and
    the engine is told so it can unkey first and stop."""
    first = not dev.lock_lost
    dev.lock_lost = why
    dev.leave_as_is = True
    cb = getattr(dev, "on_lock_lost", None)
    # The heartbeat keeps finding it lost until the lock is released; the
    # engine needs telling once.
    if cb is not None and first:
        cb(dev, why)


def release(dev) -> None:
    """After close and restore: remove our markers (only ours: the token is
    checked, so a lock lost to another program is left as they hold it)."""
    lock = getattr(dev, "radio_lock", None)
    if lock is not None:
        lock.release()
