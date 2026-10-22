#!/usr/bin/env python3
"""Evaluate the narrow video formats in the simulator: picture, colour,
sound, lock, threshold and occupied bandwidth, at 5 and 6.25 MS/s.

    python3 tools/narrow_eval.py [--quick] [--jobs N]

Everything goes through the real encoder, modulator, demodulator and
decoder (ohsatv.narrow). The channel between them is the honest part:

- **the AD936x's filtering at both ends**, modelled as flat to 0.4 fs,
  half amplitude at 0.45 fs and gone by 0.5 fs, applied on transmit and on
  receive. Without it an FM signal wider than the sample band would wrap
  round inside the simulation and come back perfectly - which a real
  transmitter's reconstruction filter and a real receiver's anti-alias
  filter do not allow;
- white noise for a carrier-to-noise ratio over the whole sample band (as
  tests/test_chain.py specifies it), so a figure here and one there are
  the same thing; C/N0 = CNR + 10 log10(fs) compares the two rates;
- a 19 kHz carrier offset (the two real radios' references are ~15 ppm
  apart at 1255 MHz);
- optionally the Pluto's transmit underruns (a gap of nothing, after which
  the signal resumes late) and receive overruns (samples lost).

Measured: luma PSNR of the decoded test card against the source; on 75 %
colour bars the mean hue error (degrees) and saturation ratio over the six
coloured bars; a 1 kHz tone's SINAD; lock (lines with sync found, field or
frame sync); and for each the lowest CNR at which it still holds.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ohsatv import narrow, testcard  # noqa: E402
from ohsatv.dsp.filters import Ols, design, taper  # noqa: E402

CARD = testcard.test_card("GM4BRI", "IO86 TEST")
BARS = testcard.colour_bars("GM4BRI")
AUDIO = 48_000
N_A = AUDIO // 25

# The narrow profile Brian keyed on air (0.1.5): 5 MS/s, 1.2 MHz p-p,
# 1.6 MHz video, CCIR 405; at 6.25 MS/s the 12d test's 1.5 MHz / 2 MHz.
PROFILE = {5e6: {"fmDeviation": 1.2e6, "fmVideoBw": 1.6e6, "fmPreemph": True, "fmSub": 0.0},
           6.25e6: {"fmDeviation": 1.5e6, "fmVideoBw": 2.0e6, "fmPreemph": True, "fmSub": 0.0}}
AM = {5e6: {"amRate": 5e6}, 6.25e6: {"amRate": 6.25e6}}

# Pass marks for "works" (DESIGN.md, Narrow formats).
LOCK_H = 0.9                 # fraction of lines with sync found
PIC_PSNR = 15.0              # luma PSNR of the card: the card is recognisable
HUE_DEG = 15.0               # mean hue error on the bars
SAT = (0.6, 1.4)             # saturation ratio on the bars
SINAD_OK = 20.0              # a 1 kHz tone: intelligible speech
SINAD_GOOD = 30.0


def ad936x(fs: float) -> np.ndarray:
    return design(lambda f: taper(np.abs(f), 0.47 * fs, 0.06 * fs) + 0j, fs, 127)


def tone(frames: int) -> np.ndarray:
    t = np.arange(frames * N_A) / AUDIO
    return (0.5 * np.sin(2 * np.pi * 1000.0 * t)).astype(np.float32)


def sinad(audio: np.ndarray) -> float | None:
    x = np.asarray(audio[len(audio) // 4:], np.float64)
    if len(x) < 4000:
        return None
    x = x - x.mean()
    t = np.arange(len(x)) / AUDIO
    best = None
    for fr in np.arange(990.0, 1010.0, 0.05):
        basis = np.vstack([np.sin(2 * np.pi * fr * t), np.cos(2 * np.pi * fr * t)]).T
        coef, *_ = np.linalg.lstsq(basis, x, rcond=None)
        e = float(np.sum((x - basis @ coef) ** 2))
        if best is None or e < best[0]:
            best = (e, float(np.sum((basis @ coef) ** 2)))
    e, p = best
    return 10 * math.log10(max(p, 1e-20) / max(e, 1e-20))


def _resample_w(img, w):
    src = np.asarray(img, np.float32)
    x = np.clip((np.arange(w) + 0.5) * (src.shape[1] / w) - 0.5, 0, src.shape[1] - 1)
    i0 = np.floor(x).astype(int)
    i1 = np.minimum(i0 + 1, src.shape[1] - 1)
    f = (x - i0)[:, None]
    return src[:, i0] * (1 - f) + src[:, i1] * f


def luma_psnr(rgb: np.ndarray, src: np.ndarray) -> float:
    got = rgb.astype(np.float32) / 255
    ref = _resample_w(src, got.shape[1])
    y = lambda a: 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]   # noqa: E731
    h, w = got.shape[:2]
    a, b = y(got)[23:h - 23, w // 25:w - w // 25], y(ref)[23:h - 23, w // 25:w - w // 25]
    mse = float(np.mean((a - b) ** 2))
    return 10 * math.log10(1 / max(mse, 1e-10))


def bar_colour(rgb: np.ndarray) -> tuple[float, float]:
    """(mean hue error in degrees, mean saturation ratio) over the six
    coloured bars, from the middle of each bar above the ident strip."""
    got = rgb.astype(np.float32) / 255
    h, w = got.shape[:2]
    errs, sats = [], []
    for i in range(1, 7):
        x0, x1 = int((i + 0.3) * w / 8), int((i + 0.7) * w / 8)
        # The median, not the mean: the picture is clipped to 0..255, so on a
        # bar with a channel at 0 the noise's lower half is cut off and a
        # mean reads as desaturation that is not there.
        m = np.median(got[int(0.15 * h):int(0.7 * h), x0:x1].reshape(-1, 3), axis=0)
        e = testcard.BARS[i]
        def uv(c):
            yy = 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]
            return np.array([0.493 * (c[2] - yy), 0.877 * (c[0] - yy)])
        a, b = uv(m), uv(e)
        d = math.degrees(math.atan2(a[1], a[0]) - math.atan2(b[1], b[0]))
        errs.append(abs((d + 180) % 360 - 180))
        sats.append(float(np.hypot(*a) / np.hypot(*b)))
    return float(np.mean(errs)), float(np.mean(sats))


def chain(fmt: str, system: str, fs: float, cnr: float, img, frames: int = 10, seed: int = 1,
          gaps: tuple = (), slips: tuple = ()) -> dict:
    """One run. `gaps`: (frame, samples) - the transmitter stops for that
    long before that frame and then carries on (late). `slips`: (frame,
    samples) - the receiver loses that many samples before that frame."""
    p = dict(PROFILE[fs] if system == "fm" else AM[fs], videoFormat=fmt)
    enc = narrow.make_encoder(fs, system, p, colour=True)
    mod = narrow.make_mod(fs, system, p)
    dem = narrow.make_demod(fs, system, p)
    dec = narrow.make_decoder(fs, system, p)
    pl = narrow.plan(fs, system, p)
    enc.colour = enc.colour and pl["colour"]
    txf, rxf = Ols(ad936x(fs)), Ols(ad936x(fs))
    rng = np.random.default_rng(seed)
    au = tone(frames)
    out, sound = [], []
    pos = 0
    sig_p = None
    for k in range(frames):
        a = au[k * N_A:(k + 1) * N_A]
        comp = enc.encode(img(k) if callable(img) else img, k, a)
        iq = mod(comp, None if enc.audio_in_video else a)
        iq = txf(iq)
        # Breaks land 37 % of the way into the frame - in the picture, not
        # just before a field sync, which would hide what they cost.
        at = int(0.37 * len(iq))
        for f, g in gaps:
            if f == k:
                iq = np.concatenate([iq[:at], np.zeros(g, np.complex64), iq[at:]])
        for f, g in slips:
            if f == k:
                iq = np.concatenate([iq[:at], iq[at + g:]])
        n = len(iq)
        if sig_p is None:
            sig_p = float(np.mean(np.abs(iq) ** 2))
        s = math.sqrt(sig_p / 10 ** (cnr / 10) / 2)
        kk = np.arange(pos, pos + n, dtype=np.float64)
        rot = np.exp(2j * np.pi * ((kk * 19e3 / fs) % 1.0)).astype(np.complex64)
        iq = (iq * rot + s * (rng.standard_normal(n) + 1j * rng.standard_normal(n))).astype(np.complex64)
        pos += n
        iq = rxf(iq)
        if system == "am" and k % 2 == 0:
            dem.track(iq)
        fr = dec.feed(dem(iq))
        snd = dem.audio(iq)
        for f in fr:
            f["k"] = k
            if "audio" in f:
                sound.append(f["audio"])
        if len(snd):
            sound.append(snd)
        out += fr
    return {"frames": out, "audio": np.concatenate(sound) if sound else np.zeros(0, np.float32), "dec": dec}


def measure(fmt: str, system: str, fs: float, cnr: float, frames: int = 10) -> dict:
    r = {"fmt": fmt, "system": system, "fs": fs, "cnr": cnr}
    card = chain(fmt, system, fs, cnr, CARD, frames)
    fr = card["frames"]
    r["delivered"] = len(fr)
    if fr:
        st = fr[-1]["stats"]
        r.update(hLock=float(st["hLock"]), vLock=bool(st["vLock"]), snr=st["snr"], jitter=st["jitterNs"],
                 psnr=float(np.median([luma_psnr(f["rgb"], CARD) for f in fr[-3:]])))
    s = sinad(card["audio"])
    r["sinad"] = s
    bars = chain(fmt, system, fs, cnr, BARS, 6, seed=2)
    bf = bars["frames"]
    if bf and bf[-1]["stats"]["colour"]:
        r["hue"], r["sat"] = bar_colour(bf[-1]["rgb"])
    else:
        r["hue"] = r["sat"] = None
    return r


def passes(r: dict) -> dict:
    lock = r.get("hLock", 0) >= LOCK_H and r.get("vLock", False) and r["delivered"] >= 5
    pic = lock and r.get("psnr", 0) >= PIC_PSNR
    col = lock and r["hue"] is not None and r["hue"] <= HUE_DEG and SAT[0] <= r["sat"] <= SAT[1]
    snd = lock and r["sinad"] is not None and r["sinad"] >= SINAD_OK
    return {"lock": lock, "picture": pic, "colour": col, "sound": snd}


def occupied(fmt: str, system: str, fs: float) -> dict:
    """99 % occupied bandwidth and the power outside +-0.475 fs (dBc), from
    the same signal generated at four times the rate - where nothing
    wraps - over four frames of the test card with a tone."""
    hi = 4 * fs
    p = dict(PROFILE[fs] if system == "fm" else AM[fs], videoFormat=fmt)
    # Formats are only offered at 6.25 MS/s and below, so name it.
    enc = narrow.make_encoder(hi, system, p, fmt=fmt)
    if fmt == "mac":
        enc = narrow.MacEncoder(hi, True, chan_bw=narrow.make_encoder(fs, system, p).enc.chan_bw)
    mod = narrow.make_mod(hi, system, p, fmt=fmt)
    pl = narrow.plan(fs, system, p)
    enc.colour = enc.colour and pl["colour"]
    au = tone(4)
    iq = np.concatenate([mod(enc.encode(CARD, k, au[k * N_A:(k + 1) * N_A]),
                             None if enc.audio_in_video else au[k * N_A:(k + 1) * N_A]) for k in range(4)])
    m = 1 << 14
    segs = iq[:len(iq) // m * m].reshape(-1, m)
    psd = (np.abs(np.fft.fft(segs * np.hanning(m), axis=1)) ** 2).mean(axis=0)
    f = np.fft.fftfreq(m, 1 / hi)
    o = np.argsort(f)
    f, psd = f[o], psd[o]
    if system == "am":
        f = f - pl["carrier"]
    c = np.cumsum(psd) / psd.sum()
    lo, up = f[np.searchsorted(c, 0.005)], f[np.searchsorted(c, 0.995)]
    centre = 0.0 if system == "fm" else pl["carrier"]
    out = (np.abs(f + centre) > 0.475 * fs)
    return {"obw99": float(up - lo), "outside_dbc": float(10 * math.log10(max(psd[out].sum(), 1e-30) / psd.sum()))}


def _bad_rows(rgb: np.ndarray, src: np.ndarray) -> int:
    got = rgb.astype(np.float32) / 255
    ref = _resample_w(src, got.shape[1])
    y = lambda a: 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]   # noqa: E731
    w = got.shape[1]
    e = np.mean((y(got) - y(ref))[:, w // 25:w - w // 25] ** 2, axis=1)
    return int(np.sum(e > 0.03))           # a row more than ~15 dB off


def resync(fmt: str, system: str, fs: float, cnr: float = 20.0) -> dict:
    """Picture lost (ms) to one break in the signal: a transmit gap of 2 ms
    (the nano's late USB buffers: the transmitter stops, then carries on
    late) and a receive slip of 777 samples (an overrun). The picture moves
    (the card slides sideways 24 pixels a frame), so a row left over from
    an earlier frame counts as lost; each source frame from the break on is
    matched to the decoded frame that shows it best, and the rows decoded
    wrong (or the whole frame, if it never appears) are counted against the
    same run without the break, as time (576 rows = 40 ms)."""
    frames, first, last = 16, 6, 12
    imgs = [np.roll(CARD, 24 * j, axis=1) for j in range(frames)]

    def damage(r):
        best: dict = {}
        for f in r["frames"]:
            for j in range(max(0, f["k"] - 4), f["k"] + 1):
                best[j] = min(_bad_rows(f["rgb"], imgs[j]), best.get(j, 576))
        return sum(best.get(j, 576) for j in range(first, last))

    base = damage(chain(fmt, system, fs, cnr, imgs.__getitem__, frames, seed=3))
    res = {}
    for name, kw in (("gap", {"gaps": ((first, int(2e-3 * fs)),)}), ("slip", {"slips": ((first, 777),)})):
        rows = damage(chain(fmt, system, fs, cnr, imgs.__getitem__, frames, seed=3, **kw)) - base
        res[name] = max(0.0, rows / 576 * 40.0)
    return res


def job(args):
    kind, a = args
    if kind == "m":
        return kind, a, measure(*a)
    if kind == "o":
        return kind, a, occupied(*a)
    return kind, a, resync(*a)


# Sound in syncs ("sis") was a fourth case: rejected, and its code removed
# (Brian, 4 October 2026); its figures stay in docs/narrow-eval.md.
CASES = [("pal", "fm"), ("lowpal", "fm"), ("mac", "fm"), ("pal", "am")]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="2 dB steps, fewer CNRs")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    a = ap.parse_args(argv)
    cnrs = list(range(24, 3, -4 if a.quick else -2))
    jobs = []
    for fmt, system in CASES:
        for fs in (5e6, 6.25e6):
            if system == "fm":
                jobs.append(("o", (fmt, system, fs)))
            jobs.append(("r", (fmt, system, fs)))
            for c in cnrs:
                jobs.append(("m", (fmt, system, fs, float(c))))
    print("# Narrow formats: the evaluation\n")
    print("Printed by `python3 tools/narrow_eval.py` (the module docstring says how the channel is built). "
          "Simulator only: nothing here has been on air. CNR is over the whole sample band; C/N0 compares the "
          "two rates. Columns: fraction of lines with sync found and field/frame sync (V); the decoder's own SNR "
          "estimate; luma PSNR of the decoded test card against the source; on 75 % bars the mean hue error "
          "(degrees) and saturation ratio of the six coloured bars; SINAD of a 1 kHz tone.\n")
    print(f"Pass marks (the 'threshold' lines, the lowest CNR of an unbroken run of passes from the top): lock = "
          f"at least {LOCK_H:.0%} of lines and frame sync; picture = locked and luma PSNR >= {PIC_PSNR:g} dB; "
          f"colour = locked, hue within {HUE_DEG:g} degrees, saturation {SAT[0]:g}-{SAT[1]:g}; sound = locked "
          f"and SINAD >= {SINAD_OK:g} dB. Narrow profile: 5 MS/s 1.2 MHz p-p / 1.6 MHz video, 6.25 MS/s "
          f"1.5 / 2.0, CCIR 405 (Mini-MAC flat); AM-VSB at the plan's 2.4 / 3.0 MHz.")
    res = {}
    with ProcessPoolExecutor(a.jobs) as ex:
        for kind, key, r in ex.map(job, jobs):
            res[(kind, key)] = r
    for fmt, system in CASES:
        print(f"\n## {narrow.LABELS[fmt]} - {system.upper()}\n")
        for fs in (5e6, 6.25e6):
            rs = res[("r", (fmt, system, fs))]
            if system == "fm":
                o = res[("o", (fmt, system, fs))]
                occ = (f"99 % occupied {o['obw99'] / 1e6:.2f} MHz, outside +-0.475 fs "
                       f"{o['outside_dbc']:.1f} dBc")
            else:
                pl = narrow.plan(fs, system, dict(AM[fs], videoFormat=fmt))
                occ = f"occupied (plan) {pl['width'] / 1e6:.2f} MHz"
            print(f"{fs / 1e6:g} MS/s: {occ}; picture lost to a 2 ms TX gap {rs['gap']:.0f} ms, to a "
                  f"777-sample RX slip {rs['slip']:.0f} ms (at 20 dB CNR)")
            print("| CNR dB | C/N0 dBHz | lock (H, V) | SNR | luma PSNR | hue err | sat | SINAD |")
            print("| --- | --- | --- | --- | --- | --- | --- | --- |")
            thr = {}
            for c in cnrs:
                r = res[("m", (fmt, system, fs, float(c)))]
                ps = passes(r)
                for k, v in ps.items():
                    if v:
                        thr[k] = c
                f = lambda v, fmt_="{:.1f}": "-" if v is None else fmt_.format(v)   # noqa: E731
                print(f"| {c} | {c + 10 * math.log10(fs):.0f} | {f(r.get('hLock'), '{:.2f}')} "
                      f"{'V' if r.get('vLock') else '-'} | {f(r.get('snr'))} | {f(r.get('psnr'))} | "
                      f"{f(r['hue'])} | {f(r['sat'], '{:.2f}')} | {f(r['sinad'])} |")
            # The threshold: the lowest CNR in a run of passes from the top.
            line = []
            for k in ("lock", "picture", "colour", "sound"):
                t = None
                for c in cnrs:
                    if passes(res[("m", (fmt, system, fs, float(c)))])[k]:
                        t = c
                    else:
                        break
                line.append(f"{k} {'-' if t is None else f'{t} dB'}")
            print("\nthreshold: " + ", ".join(line) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
