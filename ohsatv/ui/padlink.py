"""The AKP03E (or a USB keypad) on the desktop: shack_pad's DesktopPad, fed
by ohsatv/pad-actions.json, pad-layout.json and ohsatv/padactions.py. Shape
copied from openhamshack-nb ohscore/ui/padlink.py (no code is shared at run time).

CONTROLLER-PAD.md section 6.2: no service and no socket on the desktop.
The pad belongs to whichever OpenHamShack app the operator is using,
through DesktopPad's lock file beside the station's store: this window
gaining focus claims it, and the app that had it lets go within a second
unless it is on the air from the pad. Remaps go into the station
profile's controllerMaps, so they sync with a controller when paired.

Transmit: `ptt` keys through shack_pad's LocalLease, whose key-up is
Radio.ptt - exactly the call the window's PTT button makes, with every
refusal it gets (licence, band plan, real-hardware permission, cable feed);
a refused key-up is shown on the key, not keyed. While keyed the lease
expects the pad's heartbeat: a pad that goes quiet or is unplugged is
unkeyed (shack_pad unkeys first on an unplug, the lease after 5 s of
silence). The PTT key shows ON AIR whenever the radio is on the air,
keyed from anywhere. The pad never reaches the radio any other way.

Optional: without shack_pad, without a HID transport (hidapi on Windows
and macOS), or with no pad plugged in, nothing happens.
"""
from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import QEvent, QObject

from .. import padactions, shacklibs
from ..i18n import fmt

log = logging.getLogger("ohsatv.ui.padlink")
KIND = "atv"


class PadLink(QObject):
    def __init__(self, window, radio, *, open_device=None, package_dir: Path | None = None, token: str | None = None):
        super().__init__(window)
        if shacklibs.shack_lib("shack_pad") is None:
            raise OSError("shack-pad is missing from this installation")
        from shack_pad.focus import DesktopPad
        from shack_pad.router import InProcessApp, InputHost
        from shack_pad.tx import LocalLease
        self.radio = radio
        decl, layout = padactions.declaration(package_dir or padactions.PACKAGE)
        self.actions = padactions.PadActions(radio)
        self.words = self._words()
        self.lease = LocalLease(on_key=self._key, on_unkey=self._unkey)
        self.lease.start()
        app = InProcessApp(KIND, self.tr("ATV"), decl, layout, perform=self._perform, faces=self.faces,
                           lease=self.lease, text=lambda k: self.words.get(k, k))
        try:
            station = radio.station
            prof = station.profile.get()
            store_dir = Path(station.store.path).parent
        except Exception:  # noqa: BLE001  no shared store: the pad still works, remaps are not kept
            log.info("pad without the station store", exc_info=True)
            station, prof, store_dir = None, {}, Path(radio.data_dir)
        self.station = station
        self.host = InputHost([app], maps=prof.get("controllerMaps") or {}, on_maps=self._save_maps)
        self.pad = DesktopPad(self.host, app="shack-atv", store_dir=store_dir, open_device=open_device,
                              token=token)        # tells two windows in one process apart (tests)
        window.installEventFilter(self)
        self.pad.start()
        self.pad.focus_gained()

    def _words(self) -> dict:
        """The declaration's catalog keys and short words, in the window's
        language (the pad-actions.json labels are keys, never English)."""
        return {
            "pad.ptt": self.tr("Push to talk"), "pad.halt": self.tr("Stop transmitting"),
            "pad.tune": self.tr("Tune"), "pad.step": self.tr("Tuning step"),
            "pad.channel": self.tr("Channel"), "pad.band": self.tr("Band"), "pad.band_next": self.tr("Next band"),
            "pad.mode_next": self.tr("AM-VSB / FM-ATV"), "pad.source": self.tr("Picture source"),
            "pad.view": self.tr("Received picture / transmit preview"),
            "pad.scope_line": self.tr("Waveform monitor line"), "pad.mute": self.tr("Mute"),
            "pad.volume": self.tr("Volume"), "pad.record": self.tr("Record IQ"),
            "HALT": self.tr("HALT"), "TX": self.tr("TX"), "TUNE": self.tr("TUNE"), "MHz": self.tr("MHz"),
            "STEP": self.tr("STEP"), "CHAN": self.tr("CHAN"), "BAND": self.tr("BAND"), "MODE": self.tr("MODE"),
            "SOURCE": self.tr("SOURCE"), "VIEW": self.tr("VIEW"), "LINE": self.tr("LINE"), "VOL": self.tr("VOL"),
        }

    # ---------------------------------------------------------- the lease
    def _key(self) -> str | None:
        try:
            self.radio.ptt(True)
        except ValueError as e:          # TxRefused is a ValueError: the button's own sentence
            return str(e)
        return None

    def _unkey(self) -> None:
        self.radio.ptt(False)

    # ---------------------------------------------------------- actions
    def _perform(self, action) -> None:
        try:
            self.actions.perform(action.action, action.kind, action.step)
        except ValueError as e:
            self.radio.toast("warn", str(e))
        except Exception:  # noqa: BLE001  a pad press must never take the window down
            log.exception("pad action %s", action.action)

    def faces(self) -> dict:
        """Live faces for the keys whose value is worth seeing, and whether
        the radio is on the air (shack_pad puts ON AIR on the PTT key)."""
        v = self.actions.values()
        systems = {"am": self.tr("AM-VSB"), "fm": self.tr("FM-ATV")}
        sources = {"testcard": self.tr("CARD"), "bars": self.tr("BARS"), "webcam": self.tr("CAMERA"),
                   "file": self.tr("FILE")}
        return {"onAir": v["onAir"], "faces": {
            # The number alone on the first line (the unit is the second's).
            "tune": {"l1": fmt.frequency(int(v["rxFreq"] or 0), "MHz", 3).rsplit(" ", 1)[0], "l2": self.tr("MHz")},
            "step": {"l1": self.tr("STEP"), "l2": fmt.frequency(int(v["stepHz"]), "kHz", 0)},
            "band:next": {"l1": self.tr("BAND"), "l2": str(v["band"] or "")},
            "mode:next": {"l1": self.tr("MODE"), "l2": systems.get(v["system"], "")},
            "source": {"l1": self.tr("SOURCE"), "l2": sources.get(v["source"], "")},
            "view": {"l1": self.tr("VIEW"), "l2": self.tr("TX") if v["view"] == "tx" else self.tr("RX")},
            "mute": {"l1": self.tr("MUTE"), "l2": self.tr("ON") if v["mute"] else "",
                     "tone": "warn" if v["mute"] else ""},
            "record": {"l1": self.tr("REC"), "l2": self.tr("ON") if v["recording"] else "",
                       "tone": "warn" if v["recording"] else ""},
        }}

    def _save_maps(self, maps: dict) -> None:
        if self.station is None:
            return
        try:
            self.station.profile.update({"controllerMaps": maps})
        except Exception:  # noqa: BLE001
            log.exception("pad remap not stored")

    # ---------------------------------------------------------- focus
    def eventFilter(self, obj, ev) -> bool:
        if ev.type() == QEvent.Type.WindowActivate:
            self.pad.focus_gained()
        return False

    def close(self) -> None:
        self.lease.unkey()
        self.lease.stop()
        self.pad.close()


def start(window, radio):
    """A PadLink, or None when the pad library cannot run here (it is
    optional; the window works the same without it)."""
    try:
        return PadLink(window, radio)
    except Exception:  # noqa: BLE001
        log.info("no pad support here", exc_info=True)
        return None
