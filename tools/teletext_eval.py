#!/usr/bin/env python3
"""Teletext through each mode the program offers: packet error rate against
carrier-to-noise ratio, in the simulator.

    python3 tools/teletext_eval.py [--quick] [--jobs N] [--bw]
    python3 tools/teletext_eval.py --cable [--jobs N]

Teletext is 6.9375 Mbit/s with its spectrum running to 5 MHz. Whether a
mode carries it is a question of its video bandwidth, its sample rate and,
on FM, its deviation - so it is measured, not assumed. Each run is the
real chain (ohsatv.narrow's encoder, modulator, demodulator and decoder,
ohsatv.vbi's inserter and slicer) through tools/narrow_eval.py's channel:
the AD936x's filtering at both ends (flat to 0.44 fs, gone by 0.5 fs),
white noise for a CNR over the whole sample band (as tests/test_chain.py
and docs/narrow-eval.md specify it), and a 19 kHz carrier offset.

The picture is 75 % colour bars (full-amplitude chroma next to the burst
and the data), and every one of the 32 lines a frame carries a packet of
random characters - a harder load than a real page, which is mostly
spaces. A packet counts as received only if all 42 bytes are exactly
what was sent; the packet error rate (PER) is the fraction of packets sent
in the measured frames (all but the first two, which are spent locking,
and the last) that did not arrive exactly.

What a PER means for a page: rows that fail are filled in from the next
cycle's copy (a character that fails parity keeps the last good one), so
a page of 24 rows at 1 % PER is complete after one or two cycles; at 10 %
it is readable after a few with the odd wrong character; at 50 % it never
settles. Thresholds below: the lowest CNR of an unbroken run (from the top)
at PER <= 1 % ("good") and <= 10 % ("usable").

`--bw` adds a sweep of video bandwidth at high CNR (AM at 12.5 MS/s, FM at
20 MS/s), which is where narrow.py's rule for "carries teletext" comes from.

`--cable` measures the cable feed's UHF mode (DESIGN.md "Cable feed") on
its own: 625-line PAL, System I as the feed sends it - AM-VSB with its FM
sound carrier at +6 MHz, the settings' defaults - at each rate that
carries teletext, in 1 dB steps and over 40 frames (1 184 packets a
point, so 1 % is 12 packets), and prints a section of its own. The chain
is the AM rows' own - the feed sends exactly what the air gets - so what
is new is the finer grid; on a cable the CNR is set by the level the
radio puts into the set, not by a path (DESIGN.md "Cable feed" works it
out for the least output).
"""
from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ohsatv import narrow, testcard, ttx, vbi  # noqa: E402
from ohsatv.dsp.filters import Ols  # noqa: E402
from narrow_eval import ad936x  # noqa: E402

BARS = testcard.colour_bars("GM4BRI")

# Every mode the program steers a radio to or offers (ohsatv/profiles.py),
# by name: (system, fs, parameters).
MODES = [
    ("AM 5 MS/s (USB Pluto)", "am", 5e6, {}),
    ("AM 6.25 MS/s", "am", 6.25e6, {}),
    ("AM 8 MS/s", "am", 8e6, {}),
    ("AM 10 MS/s (standard)", "am", 10e6, {}),
    ("AM 12.5 MS/s", "am", 12.5e6, {}),
    ("AM 16 MS/s (full System I)", "am", 16e6, {}),
    ("FM 20 MS/s, 7 MHz p-p (IARU V.2)", "fm", 20e6, {"fmDeviation": 7e6, "fmVideoBw": 5e6, "fmSub": 6e6}),
    ("FM 16 MS/s, 4 MHz p-p (Ethernet)", "fm", 16e6, {"fmDeviation": 4e6, "fmVideoBw": 5e6, "fmSub": 6e6}),
    ("FM 12.5 MS/s, 1.2 MHz p-p (8-bit RX)", "fm", 12.5e6,
     {"fmDeviation": 1.2e6, "fmVideoBw": 4.9e6, "fmSub": 5.5e6}),
    ("FM 10 MS/s, 3 MHz p-p (8-bit TX)", "fm", 10e6, {"fmDeviation": 3e6, "fmVideoBw": 3e6, "fmSub": 0.0}),
    ("FM 6.25 MS/s, 1.5 MHz p-p", "fm", 6.25e6, {"fmDeviation": 1.5e6, "fmVideoBw": 2e6, "fmSub": 0.0}),
    ("FM 5 MS/s, 1.2 MHz p-p (USB Pluto)", "fm", 5e6, {"fmDeviation": 1.2e6, "fmVideoBw": 1.6e6, "fmSub": 0.0}),
]


def per(system: str, fs: float, p: dict, cnr: float, frames: int = 14, seed: int = 1) -> dict:
    enc = narrow.make_encoder(fs, system, p)
    mod = narrow.make_mod(fs, system, p)
    dem = narrow.make_demod(fs, system, p)
    dec = narrow.make_decoder(fs, system, p)
    pl = narrow.plan(fs, system, p)
    enc.colour = enc.colour and pl["colour"]
    ins = vbi.Inserter(fs)
    txf, rxf = Ols(ad936x(fs)), Ols(ad936x(fs))
    rng = np.random.default_rng(seed)
    want, got = set(), []
    sig_p, pos, st = None, 0, None
    for k in range(frames):
        comp = enc.encode(BARS, k)
        pk = [ttx.row(1 + i % 8, 1 + i % 24, bytes(rng.integers(32, 127, 40).astype(np.uint8))) for i in range(32)]
        ins.insert(comp, pk[:16], pk[16:])
        if 2 <= k < frames - 1:
            want.update(pk)
        iq = txf(mod(comp))
        n = len(iq)
        if sig_p is None:
            sig_p = float(np.mean(np.abs(iq) ** 2))
        s = np.sqrt(sig_p / 10 ** (cnr / 10) / 2)
        kk = np.arange(pos, pos + n, dtype=np.float64)
        rot = np.exp(2j * np.pi * ((kk * 19e3 / fs) % 1.0)).astype(np.complex64)
        iq = (iq * rot + s * (rng.standard_normal(n) + 1j * rng.standard_normal(n))).astype(np.complex64)
        pos += n
        iq = rxf(iq)
        if system == "am" and k % 2 == 0:
            dem.track(iq)
        for f in dec.feed(dem(iq)):
            got += f["ttx"]
            st = f["stats"]
    ok = len(want & set(got))
    return {"per": 1.0 - ok / len(want), "snr": None if st is None else st["snr"],
            "locked": bool(st and st["vLock"]), "video_bw": pl["video_bw"], "teletext": pl.get("teletext")}


# The cable feed's UHF mode: System I with its sound, at the rates offered
# for AM that carry teletext (narrow.teletext_fit).
CABLE_MODES = [
    ("UHF cable, AM 8 MS/s", "am", 8e6, {}),
    ("UHF cable, AM 10 MS/s", "am", 10e6, {}),
    ("UHF cable, AM 12.5 MS/s", "am", 12.5e6, {}),
    ("UHF cable, AM 16 MS/s", "am", 16e6, {}),
]
CABLE_FRAMES = 40


def job(a):
    return a, per(*a)


def threshold(rows: list, limit: float):
    """Lowest CNR of the unbroken run of passes from the highest CNR."""
    best = None
    for cnr, r in sorted(rows, key=lambda x: -x[0]):
        if r["per"] <= limit:
            best = cnr
        else:
            break
    return best


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument("--bw", action="store_true", help="also sweep video bandwidth")
    ap.add_argument("--cable", action="store_true", help="the cable feed's UHF mode only, finer grid")
    a = ap.parse_args(argv)
    if a.cable:
        return cable(a.jobs)
    cnrs = list(range(40, 3, -4 if a.quick else -2))
    jobs = [(system, fs, p, float(c)) for _, system, fs, p in MODES for c in cnrs]
    bw_jobs = []
    if a.bw:
        for vbw in (3.0e6, 3.5e6, 4.0e6, 4.25e6, 4.5e6, 4.75e6, 5.0e6, 5.5e6):
            bw_jobs.append(("am", 12.5e6, {"amVideoBw": vbw, "amSoundOffset": 0.0}, 40.0))
            bw_jobs.append(("fm", 20e6, {"fmDeviation": 7e6, "fmVideoBw": vbw, "fmSub": 0.0}, 30.0))
    res = {}
    with ProcessPoolExecutor(a.jobs) as ex:
        for key, r in ex.map(job, jobs + bw_jobs):
            res[(key[0], key[1], tuple(sorted(key[2].items())), key[3])] = r

    def get(system, fs, p, c):
        return res[(system, fs, tuple(sorted(p.items())), c)]

    print("# Teletext through each mode: the evaluation\n")
    print("Printed by `python3 tools/teletext_eval.py` (its docstring says how the channel is built). Simulator "
          "only: nothing here has been on air. CNR is over the whole sample band, as in docs/narrow-eval.md. "
          "PER: the fraction of packets (32 a frame, random characters, on 75 % colour bars) not received "
          "exactly. SNR: the decoder's own video SNR estimate at that CNR. 'Plan': whether ohsatv.narrow's "
          "plan says the mode carries teletext (the engine inserts it only then). Here it is inserted in every "
          "mode, refused ones too, to show that it would not survive - so in those the SNR reads low: the "
          "mangled data fills the blanked lines the decoder measures noise on, and no slicer finds it there "
          "to leave it out. In the program a refused mode sends nothing on those lines.\n")
    print("## Thresholds\n")
    print("| mode | video bw | plan | PER <= 1 % from | PER <= 10 % from | PER at 40 dB |")
    print("| --- | --- | --- | --- | --- | --- |")
    for name, system, fs, p in MODES:
        rows = [(c, get(system, fs, p, float(c))) for c in cnrs]
        g, u = threshold(rows, 0.01), threshold(rows, 0.10)
        top = rows[0][1]
        print(f"| {name} | {top['video_bw'] / 1e6:.2f} MHz | {'yes' if top['teletext'] else 'no'} | "
              f"{'-' if g is None else f'{g} dB'} | {'-' if u is None else f'{u} dB'} | {top['per']:.1%} |")
    print("\n## Packet error rate against CNR\n")
    head = "| CNR | " + " | ".join(n for n, *_ in MODES) + " |"
    print(head)
    print("|" + " --- |" * (len(MODES) + 1))
    for c in cnrs:
        cells = []
        for _, system, fs, p in MODES:
            r = get(system, fs, p, float(c))
            snr = "" if r["snr"] is None else f" ({r['snr']:.0f})"
            cells.append(f"{r['per']:.1%}{snr}" if r["locked"] else "no lock")
        print(f"| {c} dB | " + " | ".join(cells) + " |")
    print("\n(In brackets: the decoder's SNR estimate, dB.)")
    if bw_jobs:
        print("\n## Video bandwidth\n")
        print("PER at high CNR (AM 12.5 MS/s at 40 dB, no sound carrier; FM 20 MS/s 7 MHz p-p at 30 dB, no "
              "subcarrier) with the modem's video filter set to each bandwidth.\n")
        print("| video bw | AM | FM |")
        print("| --- | --- | --- |")
        for vbw in (3.0e6, 3.5e6, 4.0e6, 4.25e6, 4.5e6, 4.75e6, 5.0e6, 5.5e6):
            am = get("am", 12.5e6, {"amVideoBw": vbw, "amSoundOffset": 0.0}, 40.0)
            fm = get("fm", 20e6, {"fmDeviation": 7e6, "fmVideoBw": vbw, "fmSub": 0.0}, 30.0)
            print(f"| {vbw / 1e6:g} MHz | {am['per']:.1%} | {fm['per']:.1%} |")
    return 0


def cable(jobs_n: int) -> int:
    cnrs = list(range(40, 13, -1))
    jobs = [(system, fs, p, float(c), CABLE_FRAMES) for _, system, fs, p in CABLE_MODES for c in cnrs]
    res = {}
    with ProcessPoolExecutor(jobs_n) as ex:
        for key, r in ex.map(job, jobs):
            res[(key[1], key[3])] = r
    print("## The cable feed on UHF: 625-line PAL, System I\n")
    print("Printed by `python3 tools/teletext_eval.py --cable`. The chain and channel are the ones above; each "
          f"point is {CABLE_FRAMES} frames ({(CABLE_FRAMES - 3) * 32} packets measured), CNR in 1 dB steps. "
          "System I as the feed sends it: AM-VSB, 5.5 MHz video where the rate allows, the 1.25 MHz vestige "
          "and the FM sound carrier at +5.9996 MHz, 13 dB down.\n")
    print("| mode | video bw | plan | PER <= 1 % from | PER <= 10 % from | PER = 0 from |")
    print("| --- | --- | --- | --- | --- | --- |")
    for name, system, fs, p in CABLE_MODES:
        rows = [(c, res[(fs, float(c))]) for c in cnrs]
        g, u = threshold(rows, 0.01), threshold(rows, 0.10)
        z = threshold(rows, 0.0)
        top = rows[0][1]
        print(f"| {name} | {top['video_bw'] / 1e6:.2f} MHz | {'yes' if top['teletext'] else 'no'} | "
              f"{'-' if g is None else f'{g} dB'} | {'-' if u is None else f'{u} dB'} | "
              f"{'-' if z is None else f'{z} dB'} |")
    print("\n| CNR | " + " | ".join(n for n, *_ in CABLE_MODES) + " |")
    print("|" + " --- |" * (len(CABLE_MODES) + 1))
    for c in cnrs:
        if c > 30 and c % 2:
            continue
        cells = []
        for _, system, fs, p in CABLE_MODES:
            r = res[(fs, float(c))]
            snr = "" if r["snr"] is None else f" ({r['snr']:.0f})"
            cells.append(f"{r['per']:.2%}{snr}" if r["locked"] else "no lock")
        print(f"| {c} dB | " + " | ".join(cells) + " |")
    print("\n(In brackets: the decoder's SNR estimate, dB. Above 30 dB every other step is left out: all 0.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
