# What it costs

Measured on shack (AMD Ryzen 9 6900HX, 16 threads, Debian 13, numpy 2.2,
Python 3.13) on 4 October 2026. Re-measure with `python3 -m ohsatv.bench`.

## Per stage, per 40 ms frame, one core

`python3 -m ohsatv.bench` (ms; a frame lasts 40):

| system | MS/s | encode | modulate | demodulate | decode | loop (once) |
| --- | --- | --- | --- | --- | --- | --- |
| AM-VSB | 6.25 | 1.8 | 2.5 | 7.2 | 4.3 | 29 |
| AM-VSB | 8 | 3.1 | 9.1 | 12.3 | 5.4 | 69 |
| AM-VSB | 10 | 8.3 | 11.6 | 16.0 | 21.6 | 113 |
| AM-VSB | 12.5 | 15.8 | 14.9 | 18.7 | 15.6 | 112 |
| AM-VSB | 16 | 11.5 | 17.4 | 22.4 | 18.8 | 137 |
| AM-VSB | 20 | 15.0 | 22.1 | 28.8 | 24.2 | 182 |
| FM (2 MHz p-p) | 6.25 | 1.8 | 2.5 | 4.9 | 3.9 | 43 |
| FM (2 MHz p-p) | 10 | 7.2 | 4.0 | 7.9 | 11.8 | 69 |
| FM (4 MHz p-p) | 16 | 12.8 | 16.4 | 17.2 | 19.2 | 190 |
| FM (7 MHz p-p, IARU) | 20 | 14.2 | 20.3 | 20.9 | 24.3 | 243 |

(Single runs; expect ±20 % between runs - the 10 and 12.5 MS/s rows show the
noise.) "Demodulate" includes the sound; "loop" is the one-off cost of
generating a static picture's 0.16 s IQ loop at the first key-up.

## The narrow formats (5 and 6.25 MS/s)

`python3 -m ohsatv.bench` also prints the picture formats with the narrow FM
profile (1.2 MHz p-p / 1.6 MHz video at 5 MS/s, 1.5 / 2.0 at 6.25), the
1 kHz tone included; `detect` is the receiver's look at what is on the air
(DESIGN.md "Narrow formats"), run every half second only while nothing is
decoded. Shack, 4 October 2026, ms per 40 ms frame:

| format | MS/s | encode | modulate | demodulate | decode | detect | loop (once) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| PAL (mono) | 5 | 1.4 | 1.9 | 3.3 | 2.3 | 2.8 | 35 |
| narrow colour PAL | 5 | 4.2 | 4.3 | 5.2 | 5.7 | 2.8 | 60 |
| Mini-MAC | 5 | 13.4 | 2.0 | 3.4 | 10.6 | 2.9 | 94 |
| PAL (mono) | 6.25 | 1.7 | 2.5 | 4.6 | 2.7 | 3.3 | 43 |
| narrow colour PAL | 6.25 | 5.3 | 5.3 | 6.5 | 7.0 | 3.3 | 73 |
| Mini-MAC | 6.25 | 13.5 | 2.5 | 4.5 | 12.8 | 3.3 | 99 |

Mini-MAC's encoder is the dearest column (band-limited compression of luma
and chroma by FFT, 576 rows a frame); it runs only for a live source, on its
own thread. In the engine, keyed on the simulator at 5 MS/s with the window
closed, everything at 25 fps with nothing skipped or repeated:

| format | test card (looped) | live video file (encode + modulate per frame) |
| --- | --- | --- |
| PAL (mono) | 0.23 cores | 0.48 cores |
| narrow colour PAL | 0.36 | 0.74 |
| Mini-MAC | 0.44 | 0.93 |

**On a laptop i5** (estimated, not measured — the test laptop was in use
for hardware tests). The one figure from it: headless receive of mono PAL
at 6.25 MS/s from the nano took 16-50 % of a core (HANDOVER), where shack's
stages add to ~18 % (demodulate 4.6 + decode 2.7 ms a frame) — so the
i5-1135G7 is up to ~2.5x slower here, USB reading included. At 2.5x,
Mini-MAC's decoder is ~32 ms and narrow colour PAL's ~18 ms of the 40 a
frame lasts, each on its own thread: **both decode at 25 fps**. The
tightest case is a live Mini-MAC source, whose encoder would be ~34 ms on
its own thread; `txRepeats` says if it falls behind (a test card is a
precomputed loop and costs nothing per frame).

## The receive path made cheaper (5 October 2026)

Why: on the test laptop (i5-1135G7) receiving AM-VSB at 10 MS/s with the
decoder running cost about 30 ms of every 40 ms block; the engine fell
behind, the reader dropped buffers, and ae92ed7 had to make it skip whole
frames. The tables above are the 4 October figures, before this work.

How it was measured: shack is shared with other sessions (load average
3-5 during these runs, 6-12 earlier in the day), so each figure is an A/B:
main as it was before this work (0f610fc, which already had its own
cheaper Mini-MAC picture building) and the new tree, run alternately,
three rounds, the minimum kept. The per-stage figures are the median of 24
frames' thread CPU time (`time.thread_time`), which shrugs off time stolen
by other processes; `python3 -m ohsatv.bench` (wall time, its own mean)
gives the same ratios. **Read the ratios first**; the absolute figures are
within ~10 % of a quiet shack's.

Per stage, ms per 40 ms frame, one core, old → new:

| case | filter | detector | sound | demodulate (rx thread) | decode (decode thread) | of which the picture |
| --- | --- | --- | --- | --- | --- | --- |
| AM-VSB 10 MS/s | 3.2 → 3.7 | 8.7 → 2.8 | 2.8 → 2.2 | 15.3 → 9.0 | 10.8 → 7.1 | 7.6 → 4.8 |
| AM-VSB 20 MS/s | 6.4 → 7.4 | 16.8 → 5.5 | 5.6 → 3.7 | 30.3 → 17.0 | 22.4 → 13.0 | 16.9 → 9.0 |
| FM 10 MS/s, 2 MHz p-p | 8.1 → 5.8 (discriminator + filter) | | - | 8.1 → 5.8 | 9.1 → 6.0 | 5.9 → 3.5 |
| FM 16 MS/s, 4 MHz p-p | 13.4 → 9.5 | | 4.5 → 1.9 | 18.0 → 11.3 | 17.3 → 11.1 | 12.8 → 7.6 |
| FM 20 MS/s, 7 MHz p-p | 16.4 → 11.3 | | 5.9 → 2.1 | 22.2 → 13.9 | 21.6 → 12.5 | 16.5 → 8.9 |
| PAL (mono) 5 MS/s narrow | 3.8 → 2.7 | | - | 3.7 → 2.8 | 2.6 → 2.1 | 0.7 → 0.8 |
| narrow colour PAL 5 MS/s | 3.9 → 2.8 | | 1.7 → 0.9 | 5.8 → 3.7 | 6.2 → 4.1 | 3.9 → 2.5 |
| Mini-MAC 5 MS/s | 3.8 → 2.7 | | - | 3.8 → 2.7 | 7.2 → 6.1 | |

(AM "filter" is the receive filter, now also the carrier mixer, hence a
little dearer; "detector" is the synchronous detector; "sound" includes
the de-emphasis. "Demodulate" is the bench's rx column: filter, detector,
sound and the carrier search amortised.)

`python3 -m ohsatv.bench`, demodulate + decode, min of three, old → new:

| system | MS/s | demodulate | decode | sum | new / old |
| --- | --- | --- | --- | --- | --- |
| AM-VSB | 6.25 | 7.2 → 4.2 | 2.8 → 2.8 | 10.0 → 7.0 | 0.70 |
| AM-VSB | 8 | 11.9 → 8.1 | 3.6 → 3.1 | 15.5 → 11.2 | 0.72 |
| AM-VSB | 10 | 15.9 → 9.6 | 12.1 → 7.9 | 28.0 → 17.5 | **0.62** |
| AM-VSB | 12.5 | 18.4 → 11.4 | 13.6 → 9.5 | 32.0 → 20.9 | 0.65 |
| AM-VSB | 16 | 23.2 → 14.6 | 17.0 → 10.6 | 40.2 → 25.2 | 0.63 |
| AM-VSB | 20 | 29.0 → 17.6 | 22.2 → 15.5 | 51.2 → 33.1 | 0.65 |
| FM | 8 | 7.2 → 4.7 | 4.5 → 3.4 | 11.7 → 8.1 | 0.69 |
| FM | 10 | 8.8 → 6.1 | 13.2 → 9.7 | 22.0 → 15.8 | 0.72 |
| FM | 12.5 | 12.2 → 7.3 | 18.1 → 8.5 | 30.3 → 15.8 | 0.52 |
| FM | 16 | 20.7 → 11.0 | 19.9 → 10.0 | 40.6 → 21.0 | 0.52 |
| FM | 20 | 22.4 → 15.1 | 21.4 → 15.8 | 43.8 → 30.9 | 0.71 |
| PAL (mono), narrow | 5 | 3.9 → 2.8 | 2.8 → 2.2 | 6.7 → 5.0 | 0.75 |
| narrow colour PAL | 5 | 5.9 → 3.9 | 6.3 → 4.1 | 12.2 → 8.0 | 0.66 |
| Mini-MAC | 5 | 4.2 → 2.7 | 8.5 → 6.4 | 12.7 → 9.1 | 0.72 |
| narrow colour PAL | 6.25 | 8.2 → 4.9 | 9.4 → 5.4 | 17.6 → 10.3 | 0.59 |
| Mini-MAC | 6.25 | 5.5 → 3.5 | 9.4 → 7.6 | 14.9 → 11.1 | 0.74 |

(Mono pictures gain least: their decoders do no colour, which is where the
decoder's time went. Against 2ced13e, before main's own Mini-MAC work, the
Mini-MAC decoder went from ~14 to ~8 ms.)

**The laptop.** (Measured since: "The test laptop" below. It is ~1.3x
shack, not 2.5x, so the estimate here was pessimistic.) At 10 MS/s
AM the receive path now costs ~0.62 of what it did, the rx thread ~0.6
and the decode thread ~0.65. Scaling the laptop's ~30 ms a block by 0.62
gives **~19 ms of 40: just under half of real time**, split ~11 + ~8
across its two threads. If that 30 ms was one thread rather than both (the report
does not say), the other is now well under 20 too. The HANDOVER's one
hardware figure (mono PAL at 6.25 MS/s: 16-50 % of a core headless,
against 18 % for shack's stages) makes the i5 up to ~2.5x slower with USB
reading included; at 2.5x the new 10 MS/s AM figures are ~23 ms (rx) and
~18 ms (decode) on separate threads - each inside the 40, with headroom
the old ~38 and ~27 did not have.

What made the difference, in order of what it saved:

- **AM synchronous detector** (10.5 → 3.2 ms at 10 MS/s): the carrier's
  phase was computed per sample in float64 and reduced with `np.mod`,
  which runs at ~14 ns a sample here - more than a float32 cosine and sine
  together. Now the receive filter mixes the carrier to within half a bin
  of DC (`Ols(shift=)`: on the bin grid a mixer is one table per
  overlap-save frame and one phase per frame), and the detector applies
  only the residual, in steps of 8 samples. `_cis` reduces with `rint`,
  not `np.mod`.
- **Unscaled transforms, again**: the decoder's colour forward FFT
  (625 lines, `np.fft.fft` default norm on real input) and Mini-MAC's
  resampler (rfft/irfft unscaled and complex128 - 2.3 ms for one 520-point
  pass over 576 rows, against 0.5 scaled) were on numpy 2.2's slow path.
  Every transform on the receive path is now scaled and complex64.
- **Real signals as real**: the FM video filter (real taps, real input)
  is `OlsReal`; the FM sound subcarrier is taken with `rfft` (`Channel(real=True)`).
- **`Channel` folds only the band**: the ~4 % of bins where the sound
  filter is above -80 dB, instead of multiplying, rolling and summing the
  whole spectrum.
- **The decoder works on the picture's samples only** after the chroma
  transform, and the PAL delay line is slices rather than two rolls of
  the frame; line tracking takes rows from a strided view and stores a
  batch by line number instead of a Python loop per line (also Mini-MAC).
- Smaller: sound de-emphasis in 200-time-constant chunks (40 Python
  iterations a frame → 4), overlap-save buffers assembled with one copy,
  the Pluto driver scaling each buffer in place (1 M samples: 3.5 → 1.3 ms),
  the AM carrier search in complex64.

Tried and not kept: a polynomial arctangent from numpy ufuncs for the FM
discriminator (twice as slow as `np.angle` - the temporaries cost more
than the transcendental; numpy has no vectorised `arctan2`, so the
discriminator is now FM's largest single cost); taking the luma's rows
after the subtraction rather than before (no gain).

Quality: every chain measurement (tests/test_chain.py's cases with their
values printed, not thresholded: luma and colour PSNR, jitter, hLock, the
SNR estimate, sound SINAD, and the narrow formats at 12 and 20 dB) is the
same to 0.03 dB. Decoded on identical samples, old and new decoders give
identical composites and pictures within 1/255 on 1e-5 of samples; Mini-MAC's
decoded sound is identical. tests/test_dsp.py pins the reworked filters
against the plain computation.

Not done, and what they would give: sharing one forward FFT between the AM
video filter and the sound channel (~1.2 ms at 10 MS/s; their overlap-save
framings differ, so it is a restructure); reusing the Pluto driver's
`left` buffer instead of concatenating (~0.4 ms a frame on the rx thread).

## How the engine stays in real time

Each column must stay under 40 ms **on its own thread**:

- receive: read + demodulate (rx thread) | decode (decode thread);
- transmit, static source: nothing per frame (the loop is written out);
- transmit, live source: encode (encode thread) | modulate + write (tx thread).

numpy releases the GIL in the heavy calls, so the threads overlap on
separate cores. What it adds up to, measured with the engine running:

| case | result | CPU |
| --- | --- | --- |
| receive FM-ATV at 20 MS/s on the simulator | 25 fps, locked | ~1.7 cores |
| test card looped back through the simulator, FM at 20 MS/s | 25 fps, 45 dB | ~1.8 cores |
| live video file looped back, FM at 16 MS/s | 25 fps, no repeats | ~3.4 cores |
| live video file looped back, FM at 20 MS/s | transmitter keeps time (no repeats); monitor decodes ~17 fps | ~3.7 cores |

The 20 MS/s live case is five busy threads (ffmpeg's reader, encode,
modulate, the simulator's receive, decode) and the decoder sheds a third
of its frames on this machine; it says so (`decodeSkipped`). On a real
Pluto at that rate the transmitter is half duplex, so receive and decode are
idle while keyed and the transmit side alone (encode 14 + modulate 20 ms)
fits two cores.

What made the difference, in case it needs doing again:

- numpy 2.2's unscaled complex64 FFTs are ~4x slower than scaled ones:
  both transforms scale and the filter undoes it (`dsp/filters.py`).
- Phasors are tables one 0.16 s period long, frequencies snapped to 6.25 Hz
  so the tables are exactly periodic (`modem._phasor`, `_angle_table`).
- `exp(j x)` as float32 cos/sin is ten times cheaper than complex128 `exp`.
- FM phase accumulated per line in float32 with a float64 offset per line,
  not a float64 running sum (39 → 21 ms a frame at 20 MS/s).
- The decoder emits its own sampling of the line; the window scales to 4:3
  in C (saved ~7 ms a frame).

## The test laptop (5 October 2026)

The i5-1135G7 (4 cores, 8 threads, Debian 13, Plasma on Wayland) that
drives the radios, on mains power. Old is main before the receive work
(0f610fc), new is c25e943, both run from copies in /tmp; nothing was
installed. Raw logs, per-second JSON and screenshots are in
`~/openhamshack-atv-captures/2026-10-05/laptop-bench/` on shack.

**`python3 -m ohsatv.bench`**, old → new, minimum of two alternating rounds,
ms per 40 ms frame:

| system | MS/s | demodulate | decode | sum | new / old |
| --- | --- | --- | --- | --- | --- |
| AM-VSB | 6.25 | 9.1 → 5.3 | 3.3 → 2.8 | 12.4 → 8.1 | 0.65 |
| AM-VSB | 8 | 14.6 → 9.0 | 4.2 → 3.5 | 18.8 → 12.5 | 0.66 |
| AM-VSB | 10 | 18.6 → 11.8 | 17.0 → 9.8 | 35.6 → 21.6 | **0.61** |
| AM-VSB | 12.5 | 24.1 → 14.4 | 21.6 → 12.1 | 45.7 → 26.5 | 0.58 |
| AM-VSB | 16 | 30.8 → 18.2 | 28.3 → 15.3 | 59.1 → 33.5 | 0.57 |
| AM-VSB | 20 | 38.8 → 22.8 | 35.7 → 19.1 | 74.5 → 41.9 | 0.56 |
| FM (2 MHz p-p) | 10 | 7.6 → 4.5 | 17.0 → 9.8 | 24.6 → 14.3 | 0.58 |
| FM (2 MHz p-p) | 12.5 | 9.4 → 5.5 | 21.2 → 12.1 | 30.6 → 17.6 | 0.58 |
| FM (4 MHz p-p) | 16 | 19.3 → 10.3 | 28.3 → 15.6 | 47.6 → 25.9 | 0.54 |
| FM (7 MHz p-p) | 20 | 24.6 → 12.8 | 35.4 → 18.9 | 60.0 → 31.7 | 0.53 |
| PAL (mono), narrow | 5 | 3.4 → 2.1 | 2.8 → 2.3 | 6.2 → 4.4 | 0.71 |
| narrow colour PAL | 5 | 6.1 → 3.4 | 9.1 → 5.7 | 15.2 → 9.1 | 0.60 |
| Mini-MAC | 5 | 3.6 → 2.1 | 9.3 → 8.0 | 12.9 → 10.1 | 0.78 |
| narrow colour PAL | 6.25 | 8.1 → 4.1 | 11.5 → 7.1 | 19.6 → 11.2 | 0.57 |
| Mini-MAC | 6.25 | 4.6 → 2.7 | 11.5 → 10.1 | 16.1 → 12.8 | 0.80 |

The laptop is **~1.3x shack** on these stages (AM 10 MS/s: 35.6 against
28.0 old, 21.6 against 17.5 new), not the 2.5x the one headless figure
suggested; that figure included USB reading and the carrier search with
nothing to find. Before this work AM at 16 MS/s and up, and FM at 20, did
not fit 40 ms on one thread here; now every row's slower thread does.

**In the engine, window open on the desktop**, 60 s each, old and new
alternately (two runs each for the first three rows, one for the last;
runs agreed within 0.02 core). CPU is each thread's share of a core,
read from /proc once a second; "process" includes everything:

| case | rx thread | decode thread | window | process | pictures | skipped |
| --- | --- | --- | --- | --- | --- | --- |
| nano, AM-VSB 10 MS/s 8-bit, empty band | 0.49 → 0.40 | 0.02 | 0.05 | 0.57 → 0.48 | (none) | rxSkips 0, dropped 0 |
| playback: the 4 Oct off-air AM colour capture (10 MS/s) | 0.66 → 0.52 | 0.57 → **0.30** | 0.12 | 1.33 → 0.92 | 24 fps both | none |
| playback: the 4 Oct off-air Mini-MAC capture (16 MS/s) | 0.80 → 0.51 | 0.97 → 0.98 | 0.12 | 1.90 → 1.63 | **18.5 → 25 fps** | decodeSkipped ~6.4/s → ~0.1/s |
| LibreSDR, FM 16 MS/s 4 MHz p-p, empty band | 0.63 → **0.35** | 0.02 | 0.05 | 0.76 → 0.45 | (none) | rxSkips 0, dropped 0 |

- **The 10 MS/s AM case** that started this work: with a real colour
  picture to decode, the rx thread is ~21 ms and the decode thread ~12 ms
  of every 40 (was ~26 and ~23). Both now have headroom.
- **Mini-MAC received at 16 MS/s** is the tightest case on the laptop: the
  decode thread is saturated with either tree, but the new one builds every
  picture but about one in ten seconds, where the old one dropped a quarter.
  The sound is unaffected either way since b060d81 (sound is decoded even for
  a skipped picture).
- Playback's rx thread includes reading and converting the file, not USB
  or the network; live, the driver's reader thread (`pluto-rx`) takes
  another 0.02 (nano) to 0.05 (LibreSDR) of a core.
- Live in the Mini-MAC burst (atv-research.md 16), with the transmit
  preview and IQ recording running too, the LibreSDR window took 1.52 cores:
  rx 0.38, decode 0.87, window 0.17, at 23.5 fps with no receive skips.

- **The guard's read-back** (5 October, nano over usb:, receive only;
  atv-research 17c): one `watch()` (rate + RX LO reads) costs 1.3–14 ms,
  median 2.3 ms at 10 MS/s and 6.5 ms at 12.5 MS/s, once a second on its
  own thread; a call that finds a move and sets it back 53–98 ms. Receive
  CPU was unchanged by it and nothing was skipped or dropped.

## Not measured

A Raspberry Pi or the N150 box. Expect a slower machine to manage
AM-VSB at 10 MS/s comfortably and FM-ATV at 20 MS/s only for receive or for a
static card; `decodeSkipped` and `txRepeats` will say when it cannot keep up.
