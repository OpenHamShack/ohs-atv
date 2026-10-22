"""What each stage costs per 40 ms frame, at each sample rate.

    python3 -m ohsatv.bench [--frames N]

Runs the real encoder, modulators, demodulators and decoder on the test card
on one core (the stages are single-threaded numpy) and prints milliseconds
per frame. Real time is 40 ms a frame per thread: the engine runs receive
(read + demodulate) and decode on separate threads, and a live transmit
source (encode + modulate) on a third, so each column has to stay under 40
on its own, and the machine needs a spare core per busy thread. A static
card is sent as a precomputed loop, so its transmit cost is the one-off
"loop" column, not a per-frame one.
"""
from __future__ import annotations

import argparse
import time

import numpy as np

from . import modem, narrow, pal, systems, testcard
from .decoder import VideoDecoder

P = {"fmDeviation": 7e6}


def _time(fn, n):
    fn(0)
    t = time.perf_counter()
    for k in range(1, n + 1):
        fn(k)
    return (time.perf_counter() - t) / n * 1e3


def bench(fs: float, system: str, frames: int = 8) -> dict:
    card = testcard.test_card("GM4BRI", "BENCH")
    params = dict(P)
    if system == "fm" and not modem.fm_plan(fs, 7e6)["fits"]:
        params["fmDeviation"] = 4e6 if modem.fm_plan(fs, 4e6)["fits"] else 2e6
    enc = pal.Encoder(fs)
    comps = [enc.encode(card, k) for k in range(4)]
    mod = systems.make_mod(fs, system, params)
    iqs = [mod(comps[k % 4]) for k in range(4)]
    dem = systems.make_demod(fs, system, params)
    raws = [dem(iqs[k % 4]) for k in range(4)]
    dec = VideoDecoder(fs)
    for k in range(4):
        dec.feed(raws[k])
    out = {"fs": fs, "system": system, "deviation": params["fmDeviation"] if system == "fm" else None}
    out["encode"] = _time(lambda k: enc.encode(card, k), frames)
    out["modulate"] = _time(lambda k: mod(comps[k % 4]), frames)

    def rx(k):
        iq = iqs[k % 4]
        if system == "am" and k % 12 == 0:
            dem.track(iq)
        dem(iq)
        dem.audio(iq)
    out["demodulate"] = _time(rx, frames)
    out["decode"] = _time(lambda k: dec.feed(raws[k % 4]), frames)
    t = time.perf_counter()
    systems.build_loop(fs, system, params, card)
    out["loop"] = (time.perf_counter() - t) * 1e3
    return out


# The narrow profile a USB Pluto runs (ohsatv/profiles.py), per rate.
NARROW = {5e6: {"fmDeviation": 1.2e6, "fmVideoBw": 1.6e6, "fmSub": 0.0},
          6.25e6: {"fmDeviation": 1.5e6, "fmVideoBw": 2.0e6, "fmSub": 0.0}}


def bench_format(fs: float, fmt: str, frames: int = 8) -> dict:
    """The same columns for a picture format (ohsatv/narrow.py) at a narrow
    rate, with the 1 kHz tone it carries; plus `detect`, the receiver's
    look at what is on the air (run every half second, only while nothing
    is decoded)."""
    card = testcard.test_card("GM4BRI", "BENCH")
    p = dict(NARROW[fs], videoFormat=fmt)
    enc = narrow.make_encoder(fs, "fm", p)
    mod = narrow.make_mod(fs, "fm", p)
    enc.colour = enc.colour and mod.plan["colour"]
    t = np.arange(int(0.04 * modem.AUDIO_RATE)) / modem.AUDIO_RATE
    a = (0.5 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
    ma = None if enc.audio_in_video else a
    comps = [enc.encode(card, k, a) for k in range(4)]
    iqs = [mod(comps[k % 4], ma) for k in range(4)]
    dem = narrow.make_demod(fs, "fm", p)
    raws = [dem(iqs[k % 4]) for k in range(4)]
    dec = narrow.make_decoder(fs, "fm", p)
    for k in range(6):
        dec.feed(raws[k % 4])
    out = {"fs": fs, "format": fmt}
    out["encode"] = _time(lambda k: enc.encode(card, k, a), frames)
    out["modulate"] = _time(lambda k: mod(comps[k % 4], ma), frames)

    def rx(k):
        dem(iqs[k % 4])
        dem.audio(iqs[k % 4])
    out["demodulate"] = _time(rx, frames)
    out["decode"] = _time(lambda k: dec.feed(raws[k % 4]), frames)
    out["detect"] = _time(lambda k: narrow.detect(iqs[k % 4], fs), max(2, frames // 4))
    t0 = time.perf_counter()
    systems.build_loop(fs, "fm", p, card)
    out["loop"] = (time.perf_counter() - t0) * 1e3
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m ohsatv.bench")
    ap.add_argument("--frames", type=int, default=8)
    a = ap.parse_args(argv)
    print(f"{'system':6s} {'MS/s':>5s} {'encode':>7s} {'modulate':>9s} {'demod':>7s} {'decode':>7s}"
          f" {'loop (once)':>12s}   ms per 40 ms frame, one core")
    for system in ("am", "fm"):
        for fs in (6.25e6, 8e6, 10e6, 12.5e6, 16e6, 20e6):
            r = bench(fs, system, a.frames)
            dev = f"  ({r['deviation'] / 1e6:g} MHz p-p)" if r["deviation"] else ""
            print(f"{system:6s} {fs / 1e6:5.2f} {r['encode']:7.1f} {r['modulate']:9.1f} {r['demodulate']:7.1f} "
                  f"{r['decode']:7.1f} {r['loop']:12.0f}{dev}", flush=True)
    print(f"\n{'format':8s} {'MS/s':>5s} {'encode':>7s} {'modulate':>9s} {'demod':>7s} {'decode':>7s} "
          f"{'detect':>7s} {'loop (once)':>12s}   narrow FM profile")
    for fs in (5e6, 6.25e6):
        for fmt in ("pal", "lowpal", "mac"):
            r = bench_format(fs, fmt, a.frames)
            print(f"{fmt:8s} {fs / 1e6:5.2f} {r['encode']:7.1f} {r['modulate']:9.1f} {r['demodulate']:7.1f} "
                  f"{r['decode']:7.1f} {r['detect']:7.1f} {r['loop']:12.0f}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
