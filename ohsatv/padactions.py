"""What a pad's keys and knobs do in ATV (CONTROLLER-PAD.md section 3;
BETA-PLAN architecture change 6): the actions ohsatv/pad-actions.json
declares, performed on the engine. No Qt, so the desktop window (through
shack_pad's DesktopPad, ohsatv/ui/padlink.py) and a store form's server can
share one implementation. Shape copied from openhamshack-nb ohscore/padactions.py
(no code is shared at run time).

`ptt` is not here and never will be: a pad keys only through the app's
transmit lease, like any other client (CONTROLLER-PAD.md section 7.1), so
it reaches Radio.ptt with every check the window's PTT button gets - the
licence, the band plan, real-hardware permission, the cable feed. `halt`
stops a transmission and needs no lease. Nothing here sets power: the
transmit gain and its ceiling stay with the window and Settings.

Every change goes through Radio.set, which validates it as it would a
click: a frequency outside the band, a band the cable feed does not allow,
come back as the engine's own ValueError sentence, which the pad link shows
as a toast.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import bands, state

PACKAGE = Path(__file__).resolve().parent
# The tuning steps a press of `step` walks through: the frequency readout's
# own (ohsatv/ui/readout.py STEPS).
STEPS_HZ = (10_000, 25_000, 100_000, 250_000, 500_000, 1_000_000)


def declaration(package_dir: Path = PACKAGE) -> tuple[dict, dict]:
    """(pad-actions.json, pad-layout.json) the package carries."""
    return (json.loads((package_dir / "pad-actions.json").read_text(encoding="utf-8")),
            json.loads((package_dir / "pad-layout.json").read_text(encoding="utf-8")))


class PadActions:
    def __init__(self, radio):
        self.radio = radio
        self.step_i = STEPS_HZ.index(100_000)

    @property
    def step_hz(self) -> int:
        return STEPS_HZ[self.step_i % len(STEPS_HZ)]

    def _bands(self) -> list[str]:
        """The bands a band knob walks: the amateur ones, or with the cable
        feed on its own two (state.check_combined refuses mixing them)."""
        group = bands.CABLE_BANDS if self.radio.state()["cableFeed"] else bands.BANDS
        return [b.name for b in group]

    def perform(self, action: str, kind: str = "press", step: int = 1) -> None:
        """Do one action. ValueError (a sentence) when it cannot be done
        here and now; an unknown action is ignored, as a layout naming an
        action this version lacks must not stop the pad."""
        r, s = self.radio, self.radio.state()
        n = int(step or 1)
        if action == "halt":
            r.ptt(False)
        elif action == "tune":
            r.set(rxFreq=s["rxFreq"] + n * self.step_hz)
        elif action == "step":
            self.step_i = (self.step_i + 1) % len(STEPS_HZ)
        elif action == "channel":
            chans = sorted(c.freq for c in bands.BY_NAME[s["band"]].channels)
            if not chans:
                raise ValueError(f"{s['band']} has no channels to step through")
            here = s["rxFreq"]
            if n > 0:
                ahead = [f for f in chans if f > here + 1.0] or chans
                r.set(rxFreq=ahead[min(n, len(ahead)) - 1])
            else:
                behind = [f for f in chans if f < here - 1.0] or chans
                r.set(rxFreq=behind[max(n, -len(behind))])
        elif action in ("band", "band:next"):
            names = self._bands()
            i = names.index(s["band"]) if s["band"] in names else -1
            r.set(band=names[(i + (n if action == "band" else 1)) % len(names)])
        elif action == "mode:next":
            modes = bands.BY_NAME[s["band"]].modes
            if len(modes) < 2:
                raise ValueError(f"{s['band']} has {'AM-VSB' if modes[0] == 'am' else 'FM-ATV'} only")
            i = modes.index(s["system"]) if s["system"] in modes else -1
            r.set(system=modes[(i + 1) % len(modes)])
        elif action == "source":
            choices = state.SPEC["source"]["choices"]
            r.set(source=choices[(choices.index(s["source"]) + 1) % len(choices)])
        elif action == "view":
            r.set(view="tx" if s["view"] == "rx" else "rx")
        elif action == "scope-line":
            spec = state.SPEC["scopeLine"]
            r.set(scopeLine=min(spec["max"], max(spec["min"], s["scopeLine"] + n)))
        elif action == "mute":
            r.set(mute=not s["mute"])
        elif action == "volume":
            r.set(volume=min(1.0, max(0.0, round(s["volume"] + 0.05 * n, 2))))
        elif action == "record":
            if s["recording"]:
                r.record_iq(False, why="stopped from the pad")
            else:
                r.record_iq(True)            # ValueError with its sentence when it cannot start

    def values(self) -> dict:
        """What the keys that show a value need (the pad link words them),
        and whether the radio is on the air: ON AIR on the PTT key."""
        s = self.radio.state()
        return {"onAir": bool(s.get("keyed")), "stepHz": self.step_hz, "rxFreq": s.get("rxFreq"),
                "band": s.get("band"), "system": s.get("system"), "source": s.get("source"),
                "view": s.get("view"), "mute": bool(s.get("mute")), "recording": bool(s.get("recording")),
                "volume": s.get("volume"), "scopeLine": s.get("scopeLine")}
