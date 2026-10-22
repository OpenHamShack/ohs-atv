"""The three ATV bands: bandplan segments, channels, and the transmit guard.

Whose licence and what it allows is not here: the station profile and the
shared dataset answer that (ohsatv/txguard.py, ohs-libs shack_bandplan;
BETA-PLAN architecture change 5), for any country the dataset carries.
What stays is ATV's own: the RSGB plan's ATV segments and channels, which
the dataset does not carry, and the cable feed, which is not amateur.

Every figure here is from docs/atv-research.md (RSGB 2026 band plan, IARU
R1 VHF Handbook 10.03, Ofcom licence OFW611, BATC wiki); nothing is from
memory. Where the bandplan is silent the guard does not invent a rule.

How the guard reads a bandplan:

- **Allocation** (the licence, from the dataset for the station's country
  and class): every part of the occupied bandwidth must be inside what
  the class may use. Outside it is not ours to use - refused. A station
  whose plan is not the RSGB's is judged by the dataset alone, its plan
  included (txguard.py says why).
- **ATV segment** (the bandplan): the carrier must sit in a segment where
  ATV, or all modes, are planned. Segments for TV repeater *outputs* are
  receive-only: a home station transmitting there acts as a repeater.
- **Protected neighbours** (narrowband, satellite, Galileo E6): an ATV
  signal's sidebands reaching into them is shown as a warning, not a
  refusal - the UK's own simplex channels (1255, 1275, 5665 MHz) do exactly
  that, and refusing them would refuse the bandplan's own practice. The
  operator sees the overlap and decides.
- **70cm** has had no analogue ATV in the RSGB plan since 4 December 2020
  (IARU R1: not permitted from 1 January 2021). The licence itself does not
  forbid it, and Brian decided (4 Oct 2026) that the program should allow
  it with a prominent warning rather than refuse it. On 8 Oct 2026 he
  lifted the allocation refusal there too (most 70cm work is down a cable
  into an attenuator): a signal reaching past the allocation (430-440 MHz
  in the UK) is a warning on 70cm, still a refusal on the other bands. The
  bandplan's position becomes the first warning, which the engine also
  writes to the transmit log, and the satellite and 40 W ERP sub-bands the
  signal touches are warned about as everywhere else.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import txguard

MHZ = 1e6


@dataclass(frozen=True)
class Segment:
    lo: float
    hi: float
    label: str
    kind: str          # "atv" carrier ok | "all" all modes, carrier ok | "rx" repeater outputs, receive only
    #                   | "narrow" / "sat" / "beacon" protected | "info" (shown, no rule)


@dataclass(frozen=True)
class Channel:
    freq: float
    label: str
    tx: bool           # a sensible place for a home station to transmit


@dataclass(frozen=True)
class Band:
    name: str
    lo: float
    hi: float
    segments: tuple
    channels: tuple
    default: float
    modes: tuple                       # systems in use on this band, preferred first
    analogue_tx: bool                  # analogue ATV is in this band's bandplan
    status: str                        # the allocation's status (secondary...), as the ITU tables give it
    rf_note: str                       # what the radio can do here, honestly
    notes: tuple = field(default=())
    warn: tuple = field(default=())    # extra regions to warn about (lo, hi, sentence)
    # The cable feed's power sentence. An amateur band has none here: the
    # station's own licence gives it (txguard.Guard.limit, on the band card).
    power: str = ""


BANDS = (
    Band(
        name="70cm", lo=430 * MHZ, hi=440 * MHZ,
        segments=(
            Segment(430 * MHZ, 432 * MHZ, "40 W ERP maximum; 431-432 not within 100 km of Charing Cross", "info"),
            Segment(435 * MHZ, 436 * MHZ, "Satellites only", "sat"),
            Segment(436 * MHZ, 438 * MHZ, "Satellites and experimental DATV/data (QPSK <= 2 Msym/s)", "sat"),
        ),
        # 433.0 MHz is not a bandplan channel (there are none for analogue
        # on 70cm): it is where an AM-VSB signal with sound (-1.25 .. +6.1
        # MHz) sits wholly inside 430-440 MHz, derived from the allocation.
        # 435 MHz was asked for by Brian (8 Oct 2026). It is also not a
        # bandplan channel, and it is inside the "Satellites only" segment,
        # so it is offered with that said: 405 lines and 625 without sound
        # at 10 MS/s or less stay inside 430-440, while 625 with sound
        # reaches 441.1 MHz and is warned about (433.9 is the highest
        # carrier that fits), not refused.
        channels=(Channel(433.0 * MHZ, "AM-VSB fits 430-440 here (derived; outside the bandplan)", True),
                  Channel(435.0 * MHZ, "Satellites-only segment; 625 with sound passes 440 "
                                       "(cable and attenuator)", True),
                  Channel(437.0 * MHZ, "DATV centre of activity (receive monitoring)", False)),
        default=437.0 * MHZ,
        modes=("am",),
        analogue_tx=False,
        status="Secondary",
        rf_note="A stock Pluto puts out about +3.5 dBm at 432 MHz (K0SM, measured).",
        notes=("Analogue ATV was removed from the RSGB 70cm plan on 4 Dec 2020; IARU R1: 'no longer "
               "permitted in the 430-440 MHz band' from 1 Jan 2021. Only narrow DATV around 437 MHz "
               "remains. Transmit is allowed (the licence does not forbid it) with this warning, and "
               "every 70cm transmission is logged with it.",),
    ),
    Band(
        name="23cm", lo=1240 * MHZ, hi=1325 * MHZ,
        segments=(
            Segment(1242 * MHZ, 1249 * MHZ, "TV repeater inputs (original ATV inputs 1248, 1249)", "atv"),
            Segment(1249 * MHZ, 1260 * MHZ, "ATV / DATV (IARU R1 1243.25-1260)", "atv"),
            Segment(1260 * MHZ, 1270 * MHZ, "Amateur satellite, Earth-to-space uplinks only", "sat"),
            Segment(1272 * MHZ, 1290.994 * MHZ, "ATV / DATV (IARU R1)", "atv"),
            Segment(1291.494 * MHZ, 1296 * MHZ, "All modes", "all"),
            Segment(1296 * MHZ, 1300 * MHZ, "Narrowband, beacons, FM/DV (not ATV)", "narrow"),
            Segment(1300 * MHZ, 1325 * MHZ, "TV repeater outputs (UK only): receive only", "rx"),
        ),
        channels=(
            Channel(1248.0 * MHZ, "Repeater input (original)", True),
            Channel(1249.0 * MHZ, "Repeater input (original)", True),
            Channel(1255.0 * MHZ, "Simplex (BATC)", True),
            Channel(1275.0 * MHZ, "Simplex / alternative input, as the CAA allows (BATC)", True),
            Channel(1308.0 * MHZ, "Repeater output", False),
            Channel(1310.0 * MHZ, "Repeater output", False),
            Channel(1311.5 * MHZ, "Repeater output", False),
            Channel(1312.0 * MHZ, "Repeater output", False),
            Channel(1316.0 * MHZ, "Repeater output", False),
            Channel(1318.5 * MHZ, "Repeater output", False),
        ),
        default=1255.0 * MHZ,
        modes=("fm", "am"),
        analogue_tx=True,
        status="Secondary",
        rf_note="A stock Pluto puts out about +2.5 dBm at 1296 MHz (K0SM): a PA is needed, and an LNA "
                "and band-pass filter are recommended.",
        notes=("RSGB: 'caution must be exercised prior to using 1250-1290 MHz' (primary users).",
               "No TV repeaters are in Scotland (ukrepeater.net); the nearest are GB3KM and GB3EY."),
        warn=((1258.29 * MHZ, 1299.21 * MHZ,
               "overlaps Galileo E6 (1258.29-1299.21 MHz; ECC Decision (25)01 sets -17 dBW/MHz EIRP for "
               "ATV, which the UK reportedly does not apply)"),
              (1250 * MHZ, 1290 * MHZ, "is in 1250-1290 MHz, where the RSGB asks for caution (primary users)")),
    ),
    Band(
        name="6cm", lo=5650 * MHZ, hi=5850 * MHZ,
        segments=(
            Segment(5650 * MHZ, 5668 * MHZ, "All modes (satellite Earth-to-space)", "all"),
            Segment(5668 * MHZ, 5670 * MHZ, "Narrowband (5668.2 alternative centre)", "narrow"),
            Segment(5670 * MHZ, 5680 * MHZ, "All modes", "all"),
            Segment(5755 * MHZ, 5760 * MHZ, "All modes (accept ISM interference)", "all"),
            Segment(5760 * MHZ, 5762 * MHZ, "Narrowband CW/SSB/EME, beacons 5760.75-5760.995", "narrow"),
            Segment(5762 * MHZ, 5765 * MHZ, "All modes (accept ISM interference)", "all"),
            Segment(5820 * MHZ, 5830 * MHZ, "All modes (accept ISM interference)", "all"),
            Segment(5830 * MHZ, 5850 * MHZ, "All modes, satellite space-to-Earth (accept ISM interference)", "all"),
        ),
        channels=(
            Channel(5665.0 * MHZ, "UK ATV frequency (BATC; GB3KM input)", True),
            Channel(5825.0 * MHZ, "Wideband FM audio users; too close to the band edge for ATV (BATC)", False),
            Channel(5840.0 * MHZ, "Wideband FM audio users; marginal for ATV (BATC)", False),
        ),
        default=5665.0 * MHZ,
        modes=("fm",),
        analogue_tx=True,
        status="Secondary; 5755-5765 and 5820-5850 MHz: users must accept interference from ISM",
        rf_note="Above 3.8 GHz a Pluto needs the AD9364 setting or a 6 GHz-capable board, and output "
                "falls to about -3 dBm at 5760 MHz with a noise figure near 10 dB (K0SM): an external "
                "PA and LNA are needed in practice.",
        notes=("The UK allocation is three blocks, not 5650-5850 MHz. Only 5665 MHz keeps a 25-30 MHz "
               "FPV-style signal inside one (BATC). Horizontal polarisation is the BATC standard.",
               "Ofcom: drone FPV is not compatible with the amateur licence; no airborne use in these bands."),
    ),
)

# --- the cable feed: Band I, for a vintage 405-line set, never an antenna ------
# Not an amateur allocation (DESIGN.md "Cable feed"): the program transmits
# here only with `cableFeed` on, only System A, only on these channels and
# only at the radio's least output (cable_check, Radio._tx_refusals). The
# channels are the BBC's 405-line Band I channels, vision and sound carriers
# in Hz: Old Tellys "Frequencies and Handy Numbers" (BVWS archive), and 405
# Alive's "channel 1 (45MHz vision, 41.5MHz sound)"; each is System A's
# -3.5 MHz spacing (Report 624-2 Table III) and its channel's top edge is
# vision + 1.25 MHz, so B1 occupies 41.25-46.25 MHz.
CABLE_CHANNELS = (
    ("B1", 45.00 * MHZ, 41.50 * MHZ),
    ("B2", 51.75 * MHZ, 48.25 * MHZ),
    ("B3", 56.75 * MHZ, 53.25 * MHZ),
    ("B4", 61.75 * MHZ, 58.25 * MHZ),
    ("B5", 66.75 * MHZ, 63.25 * MHZ),
)
CABLE = Band(
    name="Band I", lo=40 * MHZ, hi=68 * MHZ,
    # Each channel's 5 MHz, 1.25 MHz above vision to 3.75 below (Report
    # 624-2 Table III items 1 and 3): shown on the band strip, no rule.
    segments=tuple(Segment(v - 3.75 * MHZ, v + 1.25 * MHZ, f"{n} - cable only", "info")
                   for n, v, _ in CABLE_CHANNELS),
    channels=tuple(Channel(v, f"{n}: vision {v / MHZ:.2f}, sound {a / MHZ:.2f} MHz (System A, cable only)", True)
                   for n, v, a in CABLE_CHANNELS),
    default=45.00 * MHZ,
    modes=("am",),
    analogue_tx=False,
    status="Not an amateur allocation: cable feed to a vintage 405-line receiver only",
    power="The radio's least output, by cable through attenuation into the receiver's aerial socket",
    rf_note="Never to an antenna. A Pluto tunes down to 46.875 MHz on Tezuka firmware (B2-B5), 70 MHz stock "
            "(none); a HackRF tunes all five.",
    notes=("Cable feed only: Settings › Transmit › Cable feed. 405 lines, minimum power, B1-B5.",),
)

# --- the cable feed on UHF: 625-line PAL, System I, for a teletext-era set ----
# UK 625-line sets tune UHF only (Bands IV and V), so a 625-line feed - and
# teletext, which is 625 lines only - has to be on a UHF channel. The UK plan
# (BVWS Old Tellys, "Frequencies and Handy Numbers"): vision 8N + 303.25 MHz
# for channel N, sound 6 MHz above; System I's 8 MHz channel runs from 1.25
# MHz below vision to 6.75 above (ITU-R BT.470-6 Table 3; Wikipedia "CCIR
# System I" gives E21 470-478, vision 471.25, sound 477.25, and E36 590-598,
# 591.25, 597.25 - the same rule).
#
# Which channels, of 21-68 (a vintage set tunes them all): only 21-48, the
# band UK television still occupies since the 700 MHz clearance (Ofcom:
# DTT within 470-694 MHz from 2020). 49-60 is now 5G and 61-68 4G; a cable
# that leaks there leaks into other people's phone networks, while in 21-48
# it leaks into the band a television's own tuner is built for. And not 38
# (606-614 MHz): 608-614 is the radio astronomy band, which PMSE is kept
# out of (Ofcom, "PMSE ... channel 38"). Every channel is a whole one: the
# feed does not tune between them.
UHF_CHANNELS = tuple((f"E{n}", (8 * n + 303.25) * MHZ, (8 * n + 309.25) * MHZ)
                     for n in range(21, 49) if n != 38)
# The chips on the band card: a few spread across the range, so one clear of
# whatever a set's case picks up locally is a click away; any of the others
# is typed. E36 is the default: the UK's VCR and RF-modulator channel (left clear of
# broadcasting until Channel 5 in 1997), which a vintage set's spare preset
# is the likeliest to be tuned to already.
UHF_SHOWN = ("E21", "E24", "E28", "E32", "E36", "E40", "E44", "E48")
UHF_EDGE_BELOW, UHF_EDGE_ABOVE = 1.25 * MHZ, 6.75 * MHZ
CABLE_UHF = Band(
    name="UHF", lo=470 * MHZ, hi=694 * MHZ,
    segments=tuple(Segment(v - UHF_EDGE_BELOW, v + UHF_EDGE_ABOVE, f"{n} - cable only", "info")
                   for n, v, _ in UHF_CHANNELS),
    channels=tuple(Channel(v, f"{n}: vision {v / MHZ:.2f}, sound {a / MHZ:.2f} MHz (System I, cable only)", True)
                   for n, v, a in UHF_CHANNELS if n in UHF_SHOWN),
    default=(8 * 36 + 303.25) * MHZ,
    modes=("am",),
    analogue_tx=False,
    status="Not an amateur allocation: cable feed to a 625-line UHF receiver only",
    power="The radio's least output, by cable through attenuation into the receiver's aerial socket",
    rf_note="Never to an antenna. Every radio here tunes it: a stock Pluto from 325 MHz, a HackRF from 1 MHz.",
    notes=("Cable feed only: Settings › Transmit › Cable feed. 625-line PAL (System I), minimum power, "
           "E21-E48 except E38. Teletext and subtitles go with it.",),
)
CABLE_BANDS = (CABLE, CABLE_UHF)
CABLE_NAMES = tuple(b.name for b in CABLE_BANDS)
CABLE_TOL_HZ = 1e3

BY_NAME = {b.name: b for b in BANDS + CABLE_BANDS}


def band_at(freq: float) -> Band | None:
    for b in BANDS:
        if b.lo <= freq <= b.hi:
            return b
    return None


def segment_at(freq: float) -> Segment | None:
    b = band_at(freq)
    if b is None:
        return None
    for s in b.segments:
        if s.lo <= freq < s.hi:
            return s
    return None


def _mhz(f: float) -> str:
    return f"{f / MHZ:.3f}".rstrip("0").rstrip(".")


def tx_check(freq: float, lo_off: float, hi_off: float, station: dict,
             system: str | None = None) -> tuple[list[str], list[str]]:
    """(refusals, warnings) for transmitting a signal occupying
    freq + lo_off .. freq + hi_off with its carrier at `freq`, from the
    station profile `station` (txguard.profile). `system` ("am" / "fm")
    lets a licence's emission table judge the mode; None leaves it out."""
    refuse: list[str] = []
    warn: list[str] = []
    b = band_at(freq)
    if b is None:
        return [f"{_mhz(freq)} MHz is not in 70cm, 23cm or 6cm"], []
    lo, hi = freq + lo_off, freq + hi_off
    g = txguard.GUARD
    mode = txguard.MODES.get(system or "")
    # The RSGB plan's ATV reading is ours (the segments above); any other
    # station's plan is the dataset's, refusals and all.
    ours = g.bp is not None and g.plan_id(station) == txguard.PLAN_ID
    if not b.analogue_tx and (ours or g.region(station) == 1):
        warn.append(f"OUTSIDE THE BANDPLAN: analogue ATV is not in the RSGB / IARU R1 {b.name} bandplan "
                    f"(removed December 2020)")
    v = g.check(station, lo, hi, mode, licence_only=ours)
    if not v.ok:
        edge = v.code in txguard.EDGE_CODES and _edge(g, station, b, freq, lo_off, hi_off, mode)
        if edge and edge[0]:
            # 70cm analogue is mostly sent down a cable through an
            # attenuator, not radiated: Brian (8 Oct 2026) asked for the
            # allocation edge there to be a warning, not a refusal. Over
            # the air it is still outside the licence, and the warning
            # says so. The other bands refuse.
            if not b.analogue_tx:
                warn.append(f"OUTSIDE THE ALLOCATION: {edge[1]}. Only into a cable and attenuator - "
                            f"radiated, this is outside the licence")
            else:
                refuse.append(edge[1])
        elif edge:
            refuse.append(edge[1])
        else:
            refuse.append(v.refused)
    warn += v.warnings
    if not ours:
        return refuse, warn
    seg = segment_at(freq)
    if b.analogue_tx and (seg is None or seg.kind not in ("atv", "all")):
        if seg is not None and seg.kind == "rx":
            refuse.append(f"{_mhz(seg.lo)}-{_mhz(seg.hi)} MHz is {seg.label}")
        elif seg is not None:
            refuse.append(f"the carrier at {_mhz(freq)} MHz is in '{seg.label}', not an ATV segment")
        elif not refuse:
            refuse.append(f"the carrier at {_mhz(freq)} MHz is not in an ATV segment of the {b.name} bandplan")
    for s in b.segments:
        if s.kind == "info" and lo < s.hi and hi > s.lo:
            warn.append(f"the signal is in {_mhz(s.lo)}-{_mhz(s.hi)} MHz: {s.label}")
        elif s.kind in ("narrow", "sat", "beacon", "rx") and lo < s.hi and hi > s.lo and (s is not seg or not b.analogue_tx):
            warn.append(f"the sidebands reach into {_mhz(s.lo)}-{_mhz(s.hi)} MHz ({s.label})")
    for wlo, whi, sentence in b.warn:
        if lo < whi and hi > wlo:
            warn.append(f"the signal {sentence}")
    return refuse, warn


def _edge(g, station: dict, b: Band, freq: float, lo_off: float, hi_off: float,
          mode: str | None) -> tuple[bool, str] | None:
    """The guard's own sentence for a signal that leaves the licence's
    allocation, from the spans the dataset gives this class in the band:
    (True, sentence) when the carrier itself is inside one - the sentence
    saying how far the signal spills and where the carrier would fit -
    (False, sentence) when the carrier is outside them all, and None when
    the class has nothing in this band (the dataset's sentence says why)."""
    spans = g.spans(station, b.lo - 50 * MHZ, b.hi + 50 * MHZ, mode)
    ctx = g.context(station)
    where = (ctx.regime or {}).get("short", "")
    lo, hi = freq + lo_off, freq + hi_off
    inside = next(((a, z) for a, z in spans if a <= freq <= z), None)
    if inside is not None and g.check(station, freq, freq, mode, licence_only=True).ok:
        blo, bhi = inside
        over = max(blo - lo, hi - bhi)
        fit = ""
        if bhi - blo >= hi - lo:
            fit = (f"; the carrier must be between {_mhz(blo - lo_off)} and {_mhz(bhi - hi_off)} MHz "
                   f"for this signal")
        return True, (f"the signal ({_mhz(lo)}-{_mhz(hi)} MHz) would extend {over / MHZ:.1f} MHz outside "
                      f"the allocation {_mhz(blo)}-{_mhz(bhi)} MHz ({where}, {ctx.class_name}){fit}")
    mine = [(a, z) for a, z in spans if a < b.hi and z > b.lo]
    if not mine:
        return None
    blocks = ", ".join(f"{_mhz(a)}-{_mhz(z)}" for a, z in mine)
    return False, (f"{_mhz(freq)} MHz is outside the {b.name} allocation ({blocks} MHz; {where}, "
                   f"{ctx.class_name})")


def cable_channel(freq: float) -> tuple[str, float, float] | None:
    """The B1-B5 or UHF cable channel whose vision carrier is `freq`, or None."""
    return next((c for c in CABLE_CHANNELS + UHF_CHANNELS if abs(c[1] - freq) <= CABLE_TOL_HZ), None)


def cable_check(freq: float, lo_off: float, hi_off: float, fmt: str, system: str,
                rng: tuple[float, float] | None) -> list[str]:
    """Refusals for the cable feed (DESIGN.md "Cable feed"): 405-line System
    A on one of B1-B5, or 625-line PAL (System I) on one of the UHF channels,
    the whole signal - freq + lo_off .. freq + hi_off, sound carrier to
    vestige - inside that channel and inside what the transmitting radio
    tunes, `rng` (None: not known yet, which is no refusal by itself; the
    radio's own tuning refuses when it opens). The amateur guard (tx_check)
    does not apply: none of this is an amateur allocation, and nothing else
    may be sent while the cable feed is on.

    Each band takes its own format and no other: a 405-line set cannot show
    625 lines and a UK 625-line set cannot tune Band I, so the wrong one on
    either is a signal nobody on the cable can use."""
    refuse: list[str] = []
    band_i = next((c for c in CABLE_CHANNELS if abs(c[1] - freq) <= CABLE_TOL_HZ), None)
    uhf = next((c for c in UHF_CHANNELS if abs(c[1] - freq) <= CABLE_TOL_HZ), None)
    if system != "am" or fmt not in ("405", "pal"):
        refuse.append("Cable feed sends 405-line System A only on Band I, or 625-line PAL (System I) only on UHF: "
                      "choose AM-VSB")
    elif band_i is not None and fmt != "405":
        refuse.append("Cable feed sends 405-line System A only on Band I: choose AM-VSB and Format 405")
    elif uhf is not None and fmt != "pal":
        refuse.append("Cable feed sends 625-line PAL (System I) only on UHF: choose AM-VSB and Format PAL")
    ch = band_i or uhf
    if ch is None:
        refuse.append(f"Cable feed transmits only on the System A Band I channels "
                      f"({', '.join(f'{n} {v / MHZ:.2f}' for n, v, _ in CABLE_CHANNELS)} MHz vision) and the UHF "
                      f"channels E21-E48 except E38 (vision 8N + 303.25 MHz: E21 471.25 ... E48 687.25); "
                      f"{_mhz(freq)} MHz is not one")
        return refuse
    lo, hi = freq + lo_off, freq + hi_off
    if uhf is not None and not (freq - UHF_EDGE_BELOW <= lo and hi <= freq + UHF_EDGE_ABOVE):
        # Inside the 8 MHz channel, as System I's own signal is: the next
        # channel up may be the one the set is tuned to.
        refuse.append(f"The signal ({_mhz(lo)}-{_mhz(hi)} MHz) is not inside {uhf[0]}'s 8 MHz channel "
                      f"({_mhz(freq - UHF_EDGE_BELOW)}-{_mhz(freq + UHF_EDGE_ABOVE)} MHz)")
    if rng is not None and not (rng[0] <= lo and hi <= rng[1]):
        if uhf is not None:
            can = "A stock Pluto (from 325 MHz) and a HackRF tune all of UHF"
        else:
            ok = [n for n, v, _ in CABLE_CHANNELS if rng[0] <= v + lo_off and v + hi_off <= rng[1]]
            can = (f"Channels it can send whole: {', '.join(ok)}" if ok else
                   "It can send none of B1-B5: a HackRF can, or a Pluto on Tezuka firmware for B2-B5")
        refuse.append(f"This radio tunes {_mhz(rng[0])}-{_mhz(rng[1])} MHz; {ch[0]}'s signal "
                      f"({_mhz(lo)}-{_mhz(hi)} MHz, sound to vestige) is not all inside it. {can}")
    return refuse


def reachable(band: Band, rng: tuple[float, float] | None) -> tuple[bool, str]:
    """Whether a device whose LO tunes `rng` (Hz, from the device) can work
    this band, and the sentence to show when it cannot."""
    if rng is None:
        return True, ""
    lo, hi = rng
    if band.lo >= lo and band.hi <= hi:
        return True, ""
    if band is CABLE:
        # Band I is five channels, not a band to be tuned across: a radio
        # that reaches some of them can work it, and cable_check says which
        # channels (a Tezuka Pluto from 46.875 MHz: B2-B5).
        if any(lo <= v + 0.75 * MHZ and a - 0.05 * MHZ >= lo and v <= hi for _, v, a in CABLE_CHANNELS):
            return True, ""
        return False, (f"This radio tunes {_mhz(lo)}-{_mhz(hi)} MHz: none of the Band I channels B1-B5 "
                       f"(41.45-67.5 MHz) fits inside it. A HackRF tunes all five; a Pluto on Tezuka firmware "
                       f"tunes from 46.875 MHz (B2-B5).")
    span = f"{lo / MHZ:.0f}-{hi / MHZ:.0f} MHz"
    if hi < band.lo and hi <= 3.9e9:
        return False, (f"This radio tunes {span} (an AD9363, as a stock Pluto is). {band.name} needs the "
                       f"AD9364 setting - on the Pluto: fw_setenv attr_name compatible; fw_setenv attr_val "
                       f"ad9364 (and fw_setenv compatible ad9364 on firmware 0.32+), then reboot - or an AD9361 "
                       f"board such as the LibreSDR. Performance out there is not guaranteed by ADI.")
    return False, f"This radio tunes {span}; {band.name} ({band.lo / MHZ:.0f}-{band.hi / MHZ:.0f} MHz) is outside it."
