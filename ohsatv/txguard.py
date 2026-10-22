"""Whose licence, and what it allows: the station profile and the shared
band-plan and licence dataset (ohs-libs shack_bandplan), which is where
ATV's amateur transmit permission now comes from (BETA-PLAN architecture
change 5: band limits and TX permission are data, driven by the profile's
country, region and class, never tables in an app).

What stays ATV's own (ohsatv/bands.py): which bands the program works, the
channels, and the RSGB plan's ATV reading - ATV segments, the UK's TV
repeater outputs, the overlaps the UK's own simplex channels make on
purpose - which the dataset does not carry (its rsgb-2026 transcription has
no ATV rows on 23cm, and keeps everyone off 5650-5668 MHz, which would
refuse 5665 MHz, the UK's ATV frequency). So for a station whose plan is
that one (PLAN_ID) the dataset answers the licence question alone and
bands.py judges the plan; for any other station the dataset answers both,
its plan included, as it says - a partial region's data refuses and warns
as written, never guessed at by this program.

The cable feed (Band I, UHF) is not an amateur allocation and never comes
here: bands.cable_check judges it.

Copied in shape from openhamshack-nb ohscore/txguard.py (no code is shared at run
time between the family's repos): profile() is the one place the app's
settings become the shared station profile (SERVICES.md section 2.1), and
contexts are cached per profile and day because the engine asks on every
dial step.
"""
from __future__ import annotations

import dataclasses
import json
import threading
from datetime import date

from . import shacklibs

# The dataset plan whose ATV reading ohsatv/bands.py's segments are.
PLAN_ID = "rsgb-2026"
# ATV's systems as the dataset's modes: both are the "atv" mode class, which
# a regime's emission table and a plan's segments judge.
MODES = {"am": "pal-am", "fm": "pal-fm"}
# Refusals that mean the signal leaves the licence's allocation; the guard
# words these itself, with where the carrier would fit, and on 70cm warns
# rather than refuses (bands.tx_check).
EDGE_CODES = frozenset({"edge_above", "edge_below", "outside_allocations"})
MISSING = ("The band-plan and licence data (shack-bandplan) is missing from this installation: transmit stays "
           "locked until it is reinstalled")


def profile(st: dict) -> dict:
    """The station profile (SERVICES.md section 2.1) the app's state
    describes: what the check reads, in the shared schema."""
    out = {"callsign": (st.get("callsign") or "").strip().upper(),
           "licence": {"regime": st.get("licence") or "auto", "class": st.get("licenceClass") or ""},
           "conditions": {k: bool(v) for k, v in (st.get("stationConditions") or {}).items()}}
    if st.get("stationCountry"):
        # A DXCC entity's prefix (GM, K, DL), the profile's dxccPrefix, not
        # its ISO country: GM and G are one country with two sets of
        # conditions.
        out["dxccPrefix"] = str(st["stationCountry"]).strip().upper()
    if st.get("locator"):
        out["locator"] = str(st["locator"]).strip()
    return out


@dataclasses.dataclass
class Verdict:
    refused: str = ""
    code: str = ""
    warnings: list = dataclasses.field(default_factory=list)
    limit: dict | None = None

    @property
    def ok(self) -> bool:
        return not self.refused


class Guard:
    def __init__(self):
        self._lock = threading.Lock()
        self._cache: dict = {}
        self._bp = False

    @property
    def bp(self):
        """The shack_bandplan module, or None when it cannot be loaded."""
        if self._bp is False:
            self._bp = shacklibs.shack_lib("shack_bandplan")
        return self._bp

    def context(self, prof: dict, licence_only: bool = False):
        """The resolved profile; `licence_only` leaves its band plan out, so
        a check judges the licence alone."""
        key = (json.dumps(prof, sort_keys=True), licence_only, date.today().isoformat())
        with self._lock:
            ctx = self._cache.get(key)
            if ctx is None:
                if len(self._cache) > 32:
                    self._cache.clear()
                ctx = self.bp.context(prof, self.bp.bundled(), today=key[2])
                if licence_only:
                    ctx = dataclasses.replace(ctx, plan=None)
                self._cache[key] = ctx
            return ctx

    def plan_id(self, prof: dict) -> str:
        return ((self.context(prof).plan or {}).get("id", "")) if self.bp else ""

    def region(self, prof: dict) -> int | None:
        return self.context(prof).region if self.bp else None

    def check(self, prof: dict, lo: float, hi: float, mode: str | None = None, *,
              licence_only: bool = False) -> Verdict:
        """The dataset's verdict on a signal occupying [lo, hi] Hz."""
        if self.bp is None:
            return Verdict(refused=MISSING, code="missing")
        ctx = self.context(prof, licence_only)
        v = self.bp.check(ctx, self.bp.Signal.edges(lo, hi, mode=mode), "warn")
        out = Verdict(warnings=[w["text"] for w in v["warnings"]], limit=v["limit"])
        if v["refusal"]:
            out.refused, out.code = v["refusal"]["text"], v["refusal"]["code"]
        return out

    def spans(self, prof: dict, lo: float, hi: float, mode: str | None = None) -> list[tuple[float, float]]:
        """Where in [lo, hi] this station's class may transmit, adjacent
        allocation rows merged (430-431, 431-432... is one 430-440)."""
        if self.bp is None:
            return []
        out: list[list[float]] = []
        for s in self.bp.limits(self.context(prof, True), lo, hi, mode)["allowed"]:
            if out and s["loHz"] <= out[-1][1]:
                out[-1][1] = max(out[-1][1], s["hiHz"])
            else:
                out.append([s["loHz"], s["hiHz"]])
        return [(a, b) for a, b in out]

    def limit(self, prof: dict, lo: float, hi: float, mode: str | None = None) -> dict | None:
        """The highest power limit this class has anywhere in [lo, hi]
        (the band card's Power line): {watts, unit}, or None."""
        if self.bp is None:
            return None
        top = None
        for s in self.bp.limits(self.context(prof, True), lo, hi, mode)["allowed"]:
            if s["limit"] and (top is None or s["limit"]["watts"] > top["watts"]):
                top = s["limit"]
        return top

    def in_force(self, prof: dict) -> dict:
        """SERVICES.md section 2.2 `derived`: the licence in force, whether
        this profile can transmit at all and the sentence when not."""
        if self.bp is None:
            return {"regimeId": "", "regimeShort": "", "className": "", "canTransmit": False, "why": MISSING,
                    "entity": "", "country": "", "region": None, "plan": "", "bandplanDataVersion": ""}
        return self.bp.in_force(prof, self.bp.bundled())

    def regimes(self) -> list[tuple[str, str]]:
        """(id, short name) of every licence the dataset carries."""
        if self.bp is None:
            return []
        return sorted(((k, r["short"]) for k, r in self.bp.bundled().regimes.items()), key=lambda x: x[1])

    def classes(self, prof: dict) -> list[tuple[str, str]]:
        """(id, name) of the classes of the licence in force for `prof`."""
        if self.bp is None:
            return []
        reg = self.context(prof).regime
        return [(c["id"], c["name"]) for c in (reg or {}).get("classes", [])]

    def conditions(self, prof: dict) -> list[dict]:
        """The licence in force's yes/no questions about where the station
        is: {key, label, value} with the answer the check will use."""
        if self.bp is None:
            return []
        ctx = self.context(prof)
        seen, out = set(), []
        for c in (ctx.regime or {}).get("conditions", []):
            if c["key"] in seen:
                continue
            seen.add(c["key"])
            out.append({"key": c["key"], "label": c["label"], "value": bool(ctx.conditions.get(c["key"]))})
        return out

    def all_class_ids(self) -> list[str]:
        if self.bp is None:
            return []
        ids: list[str] = []
        for r in self.bp.bundled().regimes.values():
            ids += [c["id"] for c in r["classes"] if c["id"] not in ids]
        return ids

    def data_version(self) -> str:
        return self.bp.bundled().data_version if self.bp else ""


GUARD = Guard()
