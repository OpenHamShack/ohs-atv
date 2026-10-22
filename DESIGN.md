# OpenHamShack ATV — design

An **analogue amateur-television transceiver**: one native desktop
application that generates, transmits, receives and decodes 625-line PAL
pictures with sound, using a Pluto-family SDR for both directions. It is a
sibling of OpenHamShack NB (`~/openhamshack-nb`) and keeps that program's habits —
one process, an engine that never imports Qt, a hand-painted PyQt6 window, a
simulator as the default device, safety rules that are not optional — but
shares no code with it at run time: what it needed was copied.

Two transmission systems, three bands:

| | bands | system | baseband |
| --- | --- | --- | --- |
| **AM-VSB** | 23cm; 70cm (outside the bandplan, warned and logged); and UHF E21-E48 by cable only (the cable feed) | PAL, negative-modulation AM with a vestigial lower sideband, UK System I-style: 5.5 MHz vision, 1.25 MHz vestige, FM sound carrier +5.9996 MHz, 13 dB down | `ohsatv.modem.AmVsbMod` / `AmVsbDemod` |
| **AM-VSB, 405 lines** | as AM-VSB; and Band I B1-B5 by cable only (the cable feed) | the British System A of 1936-1985: positive modulation, 3 MHz mono picture, 0.75 MHz vestige above vision, AM sound 3.5 MHz below | `ohsatv.sysa.Mod` / `Demod` |
| **FM-ATV** | 23cm, 6cm (5.8 GHz) | PAL composite with CCIR F.405 pre-emphasis frequency-modulating the carrier (IARU R1 V.2: 3.5 MHz peak deviation, 5 MHz video), FM sound subcarrier 6.0 MHz (5.5 / 6.5 selectable) | `FmMod` / `FmDemod` |

With standard PAL, either system can carry **NICAM-728 digital stereo**
(6.552 MHz above vision, beside the FM sound) where the rate has room: see
"NICAM-728 stereo" below.

**70cm analogue transmit is allowed, outside the bandplan.** The research
found that the RSGB removed analogue ATV from the 70cm plan on 4 December
2020 and IARU Region 1 has not permitted it in 430–440 MHz since 1 January
2021 (docs/atv-research.md 1.1); the licence does not forbid it. Brian
decided (4 October 2026): allow it, with a prominent warning. On 8 October
he lifted the allocation refusal on 70cm as well, since most of this is sent
down a cable into an attenuator: a signal reaching past 430–440 MHz is an
OUTSIDE THE ALLOCATION warning there (an AM-VSB signal with sound fits with
its carrier at 431.25–433.9 MHz; 433.0 and 435.0 are offered as channels,
and 625 with sound on 435 passes 440), and other bands still refuse it. The window shows a red
OUTSIDE THE BANDPLAN banner by the PTT button, and every transmission is
written to the transmit log with that warning.

**Out of scope:** 2m talkback (144.750 MHz in the RSGB plan). Brian's
decision: it stays on a separate rig, not in this program.

The picture is generated in the program (`ohsatv.pal`): there is **no hacktv**
and no other external generator. hacktv is a reference for numbers only.

Where it runs: a Linux laptop or mini PC (Debian 13 first). Everything above
the device layer is numpy; nothing is Linux-only except packaging.

## Shape

```
 Pluto / Pluto+ / Pluto nano / LibreSDR ─IQ─┐   ┌─────────────── one process ───────────────┐
 simulated radio (loopback + other station) ┴──►│ engine (ohsatv.radio)       ◄──►  Qt UI    │──► main window
                                                 │  RX: demod → sync → PAL decode  (ohsatv.ui)│
 test card / webcam (ffmpeg) / video file ──────►│  TX: source → ident → PAL encode → mod    │
 sound card ◄────────────────────────────────────│  audio 48 kHz (PortAudio)                 │
                                                 └───────────────────────────────────────────┘
```

- `python3 -m ohsatv` opens the window; `python3 -m ohsatv --headless` runs the
  engine alone and prints a status line a second (never transmits).
- **IQ recording and playback** (`ohsatv/iqrec.py`, `devices/filedev.py`):
  Record IQ (the receive band's header, or `Radio.record_iq`) writes the
  receiving radio's raw samples in their native format as SigMF, with time
  and size limits and free-space checks, on a writer thread so a slow disk
  never stalls receive. Device `file` (or `--play FILE`) feeds a recording
  back through the same receive chain in real time, taking its rate, system,
  parameters and tuning from the metadata. Captures taken this way found the
  receive path's dropped buffers (docs/atv-research.md 13a).
- **Falling behind.** If the engine falls behind the radio, the Pluto
  driver skips whole 40 ms frames once 0.5 s is waiting, so the picture
  loses frames but keeps its line and field timing; `rxSkips` says so.
- Engine needs `numpy` only. UI: `python3-pyqt6`. Audio: `python3-pyaudio`
  (a null backend otherwise). Pluto: `python3-libiio`. Webcam and video file:
  the `ffmpeg` program (read through a pipe — no Python bindings needed, and
  `python3-av` is not required). Anything missing disables one feature with a
  sentence saying which package to install; it never stops the program.
- Config `$OHSATV_CONF` else `~/.config/openhamshack-atv/config.json`; data
  (snapshots, recordings) `$OHSATV_DATA` else `~/.local/share/openhamshack-atv/`.
- The engine never imports Qt; the UI never touches numpy DSP or the device.

## Vocabulary

All frequencies are Hz. `rxFreq` / `txFreq` are the **vision carrier** in
AM-VSB and the **carrier** in FM — what an ATV operator quotes. They are
separate because ATV repeaters receive and transmit on different frequencies;
`split` off ties them together.

Composite video is in **volts** (`ohsatv.pal`): sync tip −0.3, blanking and
black 0, peak white +0.7. Times are from 0H, the 50 % point of the line-sync
leading edge.

## The PAL generator — `ohsatv/pal.py`

625 lines, 25 frames/s, interlaced; ITU-R BT.470 / BT.1700 timing
(docs/atv-research.md): 64 µs lines, 4.7 µs line sync, 2.35 µs equalising
and 27.3 µs broad pulses (5 + 5 + 5 per field), 52 µs picture from 10.5 µs,
0.25 µs edges, burst of 10 cycles from 5.6 µs at 135°/225°, 4.433 618 75 MHz
subcarrier, U/V weights 0.493/0.877, picture on lines 23½–310 and 336–623½.

**Sample rates are whole samples per line** (fs = N × 15 625): 8, 10, 12.5,
16 and 20 MS/s give 512, 640, 800, 1024 and 1280. Then a line, a field and
the 8-field PAL colour sequence (0.16 s) fall on sample boundaries, and:

- a **static picture is generated once and looped** — the test card with
  its ident costs nothing per frame after the first 0.16 s;
- every phasor (carrier offset, sound carrier, subcarrier) is a table one
  0.16 s period long, its frequency snapped to 6.25 Hz so the table is
  exactly periodic.

Frames are built as 625 × N arrays, one numpy expression per step, never a
loop per line or sample. Measured on shack (one core): encode 10 ms/frame at
10 MS/s, 16 ms at 16 MS/s, against the 40 ms a frame lasts.

## Transmit and receive — `ohsatv/modem.py`

**AM-VSB.** Carrier amplitude 100 % at sync tip, 76 % blanking, 20 % peak
white. The vision carrier sits *below* the middle of the complex band (−2.45
MHz from the LO) so vestige, picture and sound carrier fit symmetrically; the
envelope is VSB-filtered at carrier baseband and only then shifted, so its
lower sideband cannot wrap onto the picture. Sound: FM ±50 kHz, 50 µs
pre-emphasis, −10 dB, +6.0 MHz. Receive: one linear-phase filter with the
Nyquist slope across the carrier, then **synchronous detection with the
carrier phase measured on the sync tips** (negative modulation sends pure
carrier 15 625 times a second). An envelope detector was the first version;
on a flat colour patch it lost a quarter of the saturation to quadrature
distortion, the synchronous one is within 2 %. The receive filter also
brings the carrier to within half an FFT bin of DC (a mixer on the bin grid
costs two multiplies per overlap-save frame), and the detector applies the
small residual phase in steps of 8 samples; a per-sample float64 phase was
half the demodulator's time (docs/PERF.md).

**FM.** `deviation` Hz peak-to-peak from sync tip to peak white (before
pre-emphasis), CCIR 405 pre-emphasis, sound subcarrier added to the baseband
before modulation. Receive: discriminator, then one filter doing video
lowpass and de-emphasis. The sound is taken from the discriminator output
with an FFT channeliser (shift and decimate inside the FFT).

Both demodulators return a *raw composite* (right shape, sync negative,
arbitrary gain and offset); the decoder clamps it.

## What fits where — sample rate, USB, bandwidth

The Pluto's USB 2 link, not the AD936x, is the limit (docs/atv-research.md
5.1): ADI measured **26.1 MiB/s ≈ 6.8 MS/s** receiving 16-bit IQ over libusb
(5.3 MS/s through the USB network link) and promise "up to 4 MSPS with no
dropped samples". Nobody has published a transmit or full-duplex figure.
Over Gigabit Ethernet a LibreSDR is said to stream 20 MS/s (27.5
overclocked) — **Brian's measured 16.2 MS/s** (stock iiod 0.24), and full
duplex held only at 6.25 MS/s each way — and a Pluto+ ~28 MS/s at most. Tezuka's 8-bit mode is **receive
only**. Measured on the nano, it delivers 10 and 12.5 MS/s in full and
15 MS/s at most; transmit stays 16-bit at ~5.2 (docs/atv-research.md 13). A **HackRF One** as the transmitter carries 20 MS/s
of 8-bit IQ over USB 2 — the USB way to send standard FM-ATV (see Devices).

Every mode has a **plan** (`am_plan()`, `fm_plan()`) computed from the
sample rate: video bandwidth, whether colour and the sound carrier fit, the
occupied bandwidth, and sentences saying what was given up. The UI shows the
plan; transmit is refused when the plan does not fit. This table is printed
from those functions:

| fs | samples/line | AM-VSB | FM-ATV, IARU (7 MHz p-p) | FM-ATV, 4 MHz p-p |
| --- | --- | --- | --- | --- |
| 6.25 MS/s | 400 | video 3.0 MHz, mono, no sound | no (Carson 12.6 MHz) | no (9.6 MHz) |
| 8 MS/s | 512 | video 3.8 MHz, mono, sound | no (14.2 MHz) | no (11.2 MHz) |
| 10 MS/s | 640 | video 4.8 MHz, **colour, sound** | no (16.0 MHz) | no (13.0 MHz) |
| 12.5 MS/s | 800 | video 5.5 MHz, colour, sound | no (17.0 MHz) | no (14.0 MHz) |
| 16 MS/s | 1024 | full System I | no (17.8 MHz) | **fits** (14.8 MHz), colour, 6 MHz sound |
| 20 MS/s | 1280 | full | **fits** (17.8 MHz), colour, 6 MHz sound | fits |

NICAM-728 needs AM-VSB at 10 MS/s or FM-ATV at 16 (20 for IARU V.2's
deviation) - the table is under "NICAM-728 stereo".

So: a **stock Pluto on USB** carries only the 6.25 MS/s row reliably — a
mono AM picture without sound — unless a HackRF does the transmitting, or
the far end is another OpenHamShack ATV: then Mini-MAC or narrow colour PAL
carry colour and sound at 5 MS/s FM (see "Narrow formats"). Colour AM-VSB with sound wants 10 MS/s
(Tezuka 8-bit, or Ethernet); standard FM-ATV wants 20 MS/s (a Pluto+ on
Ethernet, or a HackRF transmitting) — Brian's LibreSDR carries 16 MS/s,
which takes FM-ATV at 4 MHz p-p with colour and sound. The sample rate is a per-system setting (`amRate`,
`fmRate`, defaults 10 and 20 MS/s), and the console marks a rate above what
the device's link is reported to sustain.

**Half duplex on real hardware.** RX and TX share one sample rate and, on
USB, one bus; at video rates there is no room for both. While keyed the
receive stream is paused unless the device says it can carry both
(`Device.duplex`); the main picture then shows the **TX preview** (the frame
being sent, before modulation). The simulator is full duplex and loops our
own signal back, so the monitor shows our picture through the whole chain.

**CPU** (docs/PERF.md, shack, one core per 40 ms frame): at 20 MS/s encode
14 ms, FM modulate 20, demodulate 21, decode 24; at 10 MS/s AM-VSB 8 / 12 /
16 / 22. The receive path was reworked on 5 October 2026 to ~0.6 of
that (AM-VSB at 10 MS/s: demodulate and decode together ~0.62 of before,
FM 0.5-0.7) with the same decoded pictures and sound. Receive is split across threads (read+demodulate, decode) and a
live transmit source across two (encode, modulate+write), so each stays
inside a frame; numpy releases the GIL in the heavy calls. A static source
is a precomputed loop and costs almost nothing to transmit. FM-ATV receive at
20 MS/s on the simulator: 25 fps at about 1.7 cores.

## The decoder — `ohsatv/decoder.py`

Levels from the signal (sync tip and back porch per line; slice at 50 %).
Acquisition folds 64 lines. Tracking finds every line's 0H edge with a
matched edge detector on the composite *clipped at blanking* (so a bright
picture edge before the front porch cannot pose as sync) and fits a straight
line through a batch of edges — a transmitter's line rate is crystal-locked,
so the fit follows clock drift and averages noise. Field sync: the first
broad half-line is the start of line 1 or the middle of line 313; that
numbers every line, and a flywheel keeps and checks the count. PAL colour:
analytic chroma band, burst measured against absolute subcarrier phase and
averaged over 17 lines (both burst phases, which cancels the ±45° swing),
V-switch by majority from the bursts, delay-line averaging, Y = composite −
chroma. No burst or a weak one → mono. Measurements: H-lock fraction, V-lock,
colour, line jitter (ns), unweighted video SNR from the empty picture area of
blanked lines 8–15 / 321–328.

Frames come out at the decoder's own sampling (576 × 520 at 10 MS/s) and are
shown at 4:3 by the UI, which scales in C.

**Lines are taken at whole samples.** Each line is cut from the sample
nearest its fitted start and the subcarrier phase is taken at that same
start, so colour is exact. Linear interpolation was used first: it is a
low-pass whose depth depends on where the start falls within a sample (at
half a sample it passes 0.65 of the 4.43 MHz subcarrier at 16 MS/s, 0.17
at 10), and two real radios' clocks differ by ~15 ppm, so off air colour
would have come and gone in bands down the picture. Found while building
the narrow formats; tested with a half-sample delay.

**Breaks in the signal.** A USB Pluto's transmit falls behind (its carrier
stops, then the picture carries on late) and receivers overrun. A batch of
lines that is found and then missing from a point on (`break_at`) is taken
only up to the break; acquisition then folds the lines from the break on,
not the newest ones, and keeps trying 16 lines on until it is past it. The
line count comes back at the next field sync, which now needs the line's
own sync edge, the line before's and broad pulses across the line after,
so noise inside a break cannot number a field wrong. A break costs the
lines up to the next field sync (5–6 ms measured; it was 25–80 ms).

**Field lock needs line lock.** A field sync numbers the lines
provisionally; the decoder claims field lock (V-lock, frames out) only
while the newest 256 or more lines tracked have at least half their syncs
found — the same 0.5 at which it already gives a lock up. Band-limited FM
noise passes the field sync test now and then (at 10–20 MS/s with a narrow
video filter, up to 1 block in 25 claimed a field, and the FIELD pill
flashed LOCKED on an empty band), with ~0.2–0.35 of lines "found". Pooled
over 256 lines noise never passed 0.34; a single 64-line batch reached
0.61, which is why one batch is not the test. Measured: no claims in
135 000 blocks of noise; every lock threshold and the off-air captures
unchanged (atv-research.md 16).

## Devices — `ohsatv/devices/`

```python
class Device:
    kind: str; can_tx: bool; fs: float
    def open(self) -> None
    def close(self) -> None
    def freq_range(self) -> tuple[float, float]   # what this unit can tune, measured at open()
    def duplex(self) -> bool                      # can RX and TX stream together at fs
    def set_rx_lo(self, hz) / set_tx_lo(self, hz)
    def read(self, n) -> np.ndarray               # complex64, blocks
    def tx_start() / tx_stop() / set_tx_gain(db) / write(iq)
    def status(self) -> dict
```

- **sim** (default, 40–6000 MHz: down to Band I for the cable feed): a full-duplex loopback. Receive is our own transmission
  (when keyed and tuned to it) plus a simulated other station — a test card
  with the ident `SIM` on the receive frequency — at a chosen CNR and carrier
  offset, with the right noise floor. Paced to real time.
- **hackrf** (transmit only): a HackRF One beside the receiving radio,
  `txDevice = "hackrf"` (Settings). It carries 8-bit IQ at up to 20 MS/s over
  USB 2 (40 MB/s), so full-width 23cm and 6cm FM-ATV from a USB device, where
  a USB Pluto (16-bit, ~6.8 MS/s) cannot. Receive stays on the Pluto,
  LibreSDR or simulator and continues while keyed — two radios, so split
  operation is full duplex; the HackRF is never a receiver here (it is half
  duplex and would leave the operator blind while transmitting). libhackrf
  through ctypes (Debian's `libhackrf0`; pyhackrf is not packaged and
  SoapyHackRF adds a layer). complex64 → int8 ×127, rounded, clipped ±127
  (the modulators peak at 0.95: headroom). A quarter-second ring buffer
  feeds libhackrf's USB callback; a short ring is sent as zeros and counted
  as an **underrun**, shown in the window. 1 MHz–6 GHz, 2–20 MS/s; TX VGA =
  47 + txGain dB, the 14 dB amplifier a separate Settings switch, off by
  default; output is low at 5.8 GHz (an external PA is needed). Quiet until
  keyed, gain raised only after samples are queued, and unkey cuts the gain
  from the unkeying thread even if the USB stream has stalled.
- **pluto**: libiio (`python3-libiio`), copied from OpenHamShack NB's discovery
  (direct IP probes, per-backend scan when avahi is missing). ATV rates are
  all above 2.083 MS/s, so the AD9361's ×4 FIR — the thing the Pluto nano's
  Tezuka firmware refuses — is never needed. Tuning range read at open (see
  Bands). The device states its link (USB, USB network, Ethernet) and the
  rate that link is reported to sustain.

### Shared radios — `ohsatv/radiolock.py`, `ohsatv/devices/shared.py`

The radios are shared: libre-2 is on the LAN for anyone, and shack and the
laptop both drive the nano and the LibreSDR with OpenHamShack ATV, OpenHamShack DATV
and others. On 5 October 2026 OpenHamShack DATV keyed libre-2 minutes after
another program had moved its TX LO to 1251 MHz and its filter to 12.5 MHz,
and nothing on either side knew. OpenHamShack DATV then wrote the shared rule,
`docs/radio-lock.md` (version 1) in its repository, for every OpenHamShack
program; this is that rule adopted, both halves.

**The lock.** `ohsatv/radiolock.py` is OpenHamShack DATV's implementation,
copied (not imported: the programs ship separately), with its tests. A
marker file in `/run/lock` on this computer and, where the radio has a
shell, in `/tmp` on the radio over ssh - which is what lets shack see the
laptop's lock. The device takes it in `open()` between the last read and
the first write (only it knows where that line is, and a Pluto cannot be
named before its context answers) and gives it back at the end of
`close()`, after the FIR is put back; the engine decides that it is taken
at all and what its note says. **Receiving holds it too**: an AD936x's
receive and transmit share one sample clock, so a receiver setting its
rate moves somebody's transmitter. Busy refuses the start with the spec's
sentence and nothing written; there is no waiting, by the spec - the
operator decides. A radio whose shell does not answer (the nano on USB
with its network down) is locked on this computer only, and the window
says so for as long as it lasts. A lost lock unkeys, stops the engine and
puts nothing back - the radio is somebody else's by then.

**Changed since we set it.** The lock stops only programs that know about
it (SDR++, GNU Radio and a firmware's web page do not). So before every
key-up the Pluto's whole state is compared with what this program last
set or saw, and anything else's change refuses the key-up until the
operator accepts the radio as it is. It extends the read-back guard
(`_hold`, `watch()`), which until now set back the rate and RX LO when they
moved and carried on: a move it sets back still refuses the next key-up,
because whatever moved it may still be running. Nothing is set back for
this check - who has the radio is the operator's call, not ours.

## Bands — `ohsatv/bands.py`

From the RSGB 2026 plan, IARU R1 VHF Handbook 10.03, the Ofcom licence and
BATC (docs/atv-research.md; nothing from memory):

| band | UK allocation | ATV where | default | radio |
| --- | --- | --- | --- | --- |
| 70cm | 430–440 MHz | none for analogue since Dec 2020 (transmit allowed, warned, logged); DATV ≤ 2 Msym/s at 437 | 437.0 MHz (433.0 for AM-VSB transmit) | stock Pluto ≈ +3.5 dBm |
| 23cm | 1240–1325 MHz | repeater inputs 1242–1249 (1248, 1249); IARU ATV 1243.25–1260 and 1272–1290.994; simplex 1255 and 1275 (BATC); 1300–1325 repeater outputs (receive only) | 1255.0 MHz | ≈ +2.5 dBm: PA needed, LNA advised |
| 6cm | **5650–5680, 5755–5765, 5820–5850 MHz** (three blocks, secondary, ISM in the upper two) | all-mode segments; **5665 MHz** is the UK ATV frequency (BATC, GB3KM input) and the only common channel where a 25–30 MHz FM signal fits a block | 5665.0 MHz | needs a 6 GHz radio; ≈ −3 dBm and NF ≈ 10 dB: external PA and LNA |

**Band I** (`bands.CABLE`, B1-B5) and **UHF** (`bands.CABLE_UHF`, E21-E48
except E38) are in the band list only for the cable feed; neither is an
amateur allocation, they have their own guard (`cable_check`) and are
described under "Cable feed".

The transmit guard (`tx_check`): every part of the occupied bandwidth inside
what the station's licence class may use (the licence — refused otherwise;
from the station profile and the shared OpenHamShack dataset, ohs-libs
`shack_bandplan`, through `ohsatv/txguard.py`: country, licence, class and
the licence's yes/no conditions, for any country the dataset carries, and
no class means no transmit on a real radio); for a station whose plan is the
RSGB's, the carrier in an
ATV or all-mode segment (the bandplan — refused otherwise; repeater-output
segments are receive-only); sidebands reaching into narrowband, satellite or
Galileo E6 (1258.29–1299.21 MHz) are **warnings**, not refusals, because the
UK's own simplex channels (1255, 1275, 5665 MHz) do exactly that. Any
other station's band plan is the dataset's, judged as the dataset says
(its reserved segments refuse); the ATV segments above are the RSGB plan's
ATV reading, which the dataset does not carry.

**6cm and the tuning range.** A stock Pluto is an AD9363: 325–3800 MHz. Above
that it needs the AD9364 setting (`fw_setenv attr_name compatible`,
`fw_setenv attr_val ad9364`, plus `fw_setenv compatible ad9364` on firmware
0.32+) or an AD9361 board (LibreSDR), 70–6000 MHz, with performance outside
the AD9363's range not guaranteed. The Pluto device reads the LO channels'
`frequency_available` at open (then the `ad9361-phy,model` context
attribute, then a set-and-read-back); a band outside the range is shown
greyed with `bands.reachable()`'s sentence and tuning there is refused with
it — never a silent failure. Output and sensitivity fall off near 6 GHz (AD9361
datasheet 6.5 dBm at 5.5 GHz; a real Pluto ≈ −3 dBm at 5760 MHz): the band
card says an external PA and LNA are normally needed.

## Sources — `ohsatv/sources.py`

- **Test card** (built in, drawn with numpy): colour bars, greyscale,
  frequency gratings at 1–5 MHz, a circle and a grid, and the **ident box**
  (callsign, locator, a line of text) drawn with an embedded bitmap font.
- **Webcam** (V4L2) and **video file**: `ffmpeg` in a subprocess writing raw
  RGB frames (and for a file, 48 kHz audio) into pipes; a reader thread keeps
  the latest frame. The ident is overlaid on every source.
- **Sound**: a 1 kHz tone, the microphone, the file's audio, or silence.

## The best mode for each radio — `ohsatv/profiles.py`

All four routes to FM-ATV are supported (Brian, 4 October 2026): a HackRF
transmitting, a LibreSDR on Ethernet, a Pluto+ on Ethernet, and a USB Pluto
at reduced deviation. When the engine first meets a radio combination
(receiving radio, its link, the transmitter) it applies that combination's
best settings once and says what and why (`profile`, a toast, the plan
card): USB Pluto → 6.25 MS/s, AM-VSB a 3 MHz mono picture, FM 1.5 MHz p-p
with 2 MHz video; Ethernet at ≥ 20 MS/s or a HackRF transmitting → FM-ATV at
20 MS/s to IARU V.2, AM-VSB at 10 MS/s. The operator's later changes are
kept (`profileKey`); `autoProfile` off stops it.

A USB Pluto on Tezuka firmware is probed at open for its packed 8-bit
receive. If the probe answers, the combination is steered as `usb-rx8`, a
receive profile:
- AM-VSB at 10 MS/s, in colour with sound;
- FM-ATV at 12.5 MS/s, in colour with 5.5 MHz sound and 1.2 MHz p-p.

The AD936x runs one rate both ways, so the engine refuses to key any radio
above its link's transmit rate (`tx_link_msps`, 5.2 MS/s on USB). To
transmit from that nano, choose 5 MS/s. `plutoRx8` off keeps receive at
16 bits.

The same radio is also checked for 8-bit transmit at open: a tone sent
packed through the AD9361's digital loopback, TX LO powered down
(docs/atv-research.md 18). If it comes back as I/Q, the combination is
`usb-tx8`: 10 MS/s both ways, AM-VSB in colour with sound, FM-ATV mono at
3 MHz p-p, and `tx_link_msps` the 8-bit figure. No Tezuka bitstream unpacks
8-bit transmit yet, so today the check says so and the nano stays
`usb-rx8`. `plutoTx8` off skips the check.

## Narrow formats — `ohsatv/narrow.py`

A USB Pluto transmits only ~5 MS/s (docs/atv-research.md 12): ±2.4 MHz of
complex baseband. Standard PAL's 4.43 MHz colour and FM-ATV's 6 MHz sound
cannot be in it, so the narrow FM profile (5 MS/s, 1.2 MHz p-p, 1.6 MHz
video, CCIR 405) is a mono picture with no sound — the one that gave the
first off-air picture (12d) and locked at 18–21 dB SNR at about 17 dB CNR
on air. Brian asked for colour and sound in that channel: "try them all,
and build the ones that work into the app". Three were built and measured
in the simulator; two are in the program. **None is a standard: only
another OpenHamShack ATV decodes them**, and the window says so wherever one is
chosen.

| format | how | verdict |
| --- | --- | --- |
| **Mini-MAC** (`ohsatv/mac.py`) | D2-MAC's time multiplex scaled to ~1.4 MHz: each line carries 21 bits at 2 Mbit/s (Barker-7 line sync, a 13-bit SECDED mu-law sound sample, a U/V flag), a clamp, U or V compressed 4.16:1, luma compressed 1.377:1 | **offered** — best at every measure |
| **Narrow colour PAL** (`ohsatv/lowpal.py`) | PAL's coding on a 102.75 fH + 25 Hz = 1.605 MHz subcarrier, ±0.32 MHz chroma, luma to 1.1 MHz, 4-cycle burst, FM sound subcarrier at 2.2 MHz | **offered** — equal to the mono picture's threshold |
| **Sound in syncs** (code removed) | the mono picture unchanged, 7 bits at 2 Mbit/s inside each line-sync pulse, two lines to a 13-bit sample (7.6 kHz) | **rejected** — sound fails 8 dB before the picture; Brian dropped it and its code was removed (4 October 2026), the figures stay here and in docs/narrow-eval.md |

### Design decisions

- **Defined in time, not samples.** Bit rate, segment times, subcarrier
  and burst are in µs and Hz, so a 5 MS/s nano and a receiver at 6.25 or
  16 MS/s talk (tested). Formats apply wherever chosen (`fmt_for`); they
  are *suggested* at 6.25 MS/s and below (`NARROW_MAX_FS`).
- **Sound** (`ohsatv/pcm.py`): a sample rate that is a whole number of
  samples a frame, from the lines (15 625 Hz for Mini-MAC, 7 625 for SiS),
  so the line lock is the sound's clock and a static picture still loops
  seam-free; 8-bit mu-law (~38 dB over 40 dB of level, plenty for speech);
  SECDED Hamming (13, 8), because FM noise near threshold comes as short
  clicks, one or two bits: one is corrected, two are detected and the
  sample concealed (last good, decaying). ADPCM would halve the bits but one
  error upsets its step size for many samples.
- **Mini-MAC.** More of the line to luma than D2-MAC's 3:2 / 3:1 (1.0 MHz
  luma and 0.34 MHz chroma on alternate lines at 5 MS/s; 1.45 / 0.48 at
  6.25). Bits are raised-cosine pulses two bits wide (runs flat, centres
  free of neighbours, no steps at the ends); the receiver estimates all 21
  by least squares, gain from the Barker, DC from the clamp. **Flat, no
  pre-emphasis**: its data and compressed chroma are full-amplitude high
  frequencies that CCIR 405's 14 dB of lift would overdeviate, and measured
  flat was better at every CNR. The sound and flag bits are **scrambled**
  by x^15 + x^14 + 1 across the frame: energy dispersal (a steady tone sent
  the same bits on every line and passed for a line sync on colour bars),
  and line numbering — only the right line number descrambles a run of
  lines into valid codewords, so after a break the picture is back within
  ~30 lines, not at the next field sync. Lines 1 and 313 carry a 63-bit
  m-sequence (inverted on 313) as field sync. Expansion is band-limited by
  FFT, with the whole-sample extraction's leftover fraction taken out
  there exactly.
- **Narrow colour PAL.** The subcarrier keeps PAL's quarter-line offset and
  25 Hz (U's and V's lines between luma's harmonics, a crawling rather than
  standing dot pattern) and is a whole number of cycles in 0.16 s; 1.6 MHz
  rather than 1.5 because finer dots show less and it leaves luma 1.1 MHz
  and the sound 2.2. Luma *and sync* are low-passed together so sync edges
  put nothing into the chroma band. The sound subcarrier's index (0.08) and
  the receiver's sound channel (100 kHz) were chosen by measurement: 0.05
  and 220 kHz gave 1.6 dB SINAD at 16 dB CNR; 0.08 and 100 kHz give 30 dB,
  with the power outside ±0.475 fs at −40 dBc (0.12 would be −37).
- **Sound in syncs** (as built and measured, since removed) kept the
  first 0.75 µs of the sync clean (the decoder timed lines and measured the
  tip there), and a one lifts the
  level only 80 % of the way to blanking so a standard receiver still sees
  one sync pulse and locks (tested). Its data has only the sync's 0.3 V to
  swing in — 12 dB less than Mini-MAC's full-deviation bits — so its sound
  needs ~8 dB more CNR than its picture; no code fixes that without taking
  the bandwidth or depth the format exists not to take. On AM it is worse:
  the synchronous detector takes its carrier phase from clean sync tips.

### How they were measured — `tools/narrow_eval.py`

Real encoder, modulator, demodulator and decoder; a channel with **the
AD936x's filtering at both ends** (flat to 0.44 fs, half at 0.47 fs, gone
by 0.5 fs — without it an FM signal wider than the band wraps round inside
a simulation and comes back perfect), noise over the whole sample band (as
test_chain.py specifies CNR), a 19 kHz offset, and the Pluto's real
impairments: a 2 ms transmit gap (late buffers: the carrier stops, then the
picture carries on late) and a 777-sample receive slip, scored as picture
lost with a moving card so a stale row counts. Pass marks: lock = ≥ 90 % of
lines and frame sync; picture = luma PSNR of the decoded test card ≥ 15 dB;
colour = hue within 15° and saturation 0.6–1.4 on 75 % bars; sound = SINAD
of a 1 kHz tone ≥ 20 dB. Full tables: docs/narrow-eval.md. Thresholds (CNR
over the whole sample band; lowest of an unbroken run of passes):

| FM, 5 MS/s (6.25) | lock | picture | colour | sound | 99 % occupied | outside ±0.475 fs | break lost (gap / slip) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| mono PAL (today's profile) | 12 (10) | 12 (12) | — | — | 0.65 MHz | −65 dBc | 5 / 5 ms |
| **Mini-MAC** | **4 (4)** | **8 (8)** | **4 (4)** | **6 (4)** | 1.92 MHz | −50 dBc | **2 / 0 ms** |
| **narrow colour PAL** | 12 (8) | 12 (8) | 12 (8) | 12 (12) | 0.96 MHz | −40 dBc | 6 / 6 ms |
| sound in syncs | 12 (10) | 14 (14) | — | 20 (22) | 0.66 MHz | −65 dBc | 6 / 7 ms |
| AM mono / SiS on AM | 14 / 14 | 16 / 16 | — | — / 22 | (plan) 3.65 MHz | | |

Mini-MAC at 10 dB: 18.5 dB SNR, luma PSNR 17, hue within 1°, saturation
0.99, 39 dB SINAD — against a mono picture that has nothing but its
picture. Its 99 % bandwidth is wider than the mono picture's (it fills the
deviation range on most of every line, where a PAL picture sits near black)
but inside the same Carson plan and 50 dB down outside the band. Narrow
colour PAL matches the mono picture's thresholds while adding colour and
sound; its sound subcarrier sits at 0.44 fs, so how the real AD936x filters
treat 2.2 MHz decides it — the first thing to check on air.

### Mini-MAC's sound on air (4–5 October 2026)

On air (nano at 5 MS/s, −14 dB; LibreSDR at 16 MS/s in the window) the
1 kHz tone measured a median of ~17 dB SINAD against Low-PAL's ~22, while
the picture had 24 dB SNR — where the simulator gives 39 dB. **The signal
was not the cause**: the burst's own recording decoded offline gives 37–39
dB in every second after acquisition (mu-law's ceiling); 99.98 % of
codewords decode, 0.13 % of them after a correction. Level, allocation, filtering and the (absent)
de-emphasis are all as designed, and **the transmitted waveform is
unchanged**. The cause was the receiver keeping up: Mini-MAC's sound comes
out of the decoder with each frame, and the decoder at 16 MS/s cost more
than a frame (≈45 ms a block on shack, idle; the window ran 12–16 pictures
a second on air), so the engine dropped whole blocks of signal and their
sound with them. Low-PAL's sound is demodulated before the decoder and
never lost that way. Played back through the engine in real time on shack
(headless, the same recording, 12 s, interleaved runs):

| | sound delivered | SINAD (median of 1 s) | pictures |
| --- | --- | --- | --- |
| before (queue of 3, blocks dropped) | 5.4–5.8 s of ~10 | 26–28 dB | 136–144 |
| after (pictures skipped, queue of 8) | 9.3–10 s of ~10 | 38.1 dB | 195–242 |

The decoder's picture is also cheaper: single-precision band-limited
expansion (complex128 phase ramps had doubled everything after the FFT),
each chroma line expanded once at its own timing rather than U and V for
every row, and R'G'B' in place — 36–40 ms a block at 16 MS/s against
60–75 for the old code in the same (loaded) minutes, 8.4 against 13–18 at
5 MS/s; sound alone costs 7 and 3. What remains is the forward FFT of the
mirrored luma segment, whose length (1208 at 16 MS/s, 2 × 4 × 151) has a
large prime factor that the band-limited math pins down.

### In the program

- `videoFormat` (pal | lowpal | mac; a control, default pal) is what we
  send and what the receiver expects; `formatAuto` (default on) lets the
  receiver follow what is on the air: when nothing has been decoded for a
  second, every half second `narrow.detect()` looks at 40 ms of the raw
  discriminator output — a 64-line fold correlated with Mini-MAC's Barker
  (1.0 for Mini-MAC, < 0.8 for anything else) and with a pre-emphasised
  PAL sync, and the 2.2 MHz subcarrier's line — and rebuilds the receive
  chain for that format (`rxFormat`), with a toast. What we send never
  changes by itself. Correct for every format at 8 dB CNR; 3 ms.
- **Looking again while locked.** PAL's decoder locks to narrow colour
  PAL's syncs (they are the same pulses) and shows a mono picture; on 4
  October a Low-PAL recording played back as PAL stayed mono because
  detection only ran while nothing was decoded. Now a locked receiver
  that is *not decoding colour* looks once a second, with `detect(...,
  sure=True)`: Mini-MAC's Barker above 0.95 (it measures 0.96–1.0 down to
  0 dB CNR; nothing else above 0.89), Low-PAL's subcarrier above 4 dB over
  its neighbours (4.5 at 8 dB CNR; standard PAL under 1.4), PAL only with
  the subcarrier under 1.5 dB, otherwise no answer. Three looks in a row
  must name the same other format, and any other answer starts the count
  again — so it switches in about 3 s and a genuine PAL picture never
  moves (tested both ways through the simulator, at 5 and 16 MS/s, mono so
  every look is made). Colour lock exempts a picture because a burst at
  the format's own subcarrier, alternating as PAL's does, or Mini-MAC's
  data, is something no other format imitates.
- **Found on the way:** `detect()` compared the subcarrier with the
  *median* of its neighbours. At 16 and 20 MS/s a standard PAL picture's
  line harmonics run through 2.2 MHz every 15.6 kHz and a median at 2 kHz
  resolution falls between them, so PAL read 3–5 dB (22 for a grey mono
  picture) and was called Low-PAL. Against the mean the harmonics are on
  both sides: under 1.4 dB. It could already bite a searching receiver on
  a weak wide PAL signal, which would then have locked Low-PAL's decoder
  to PAL's syncs and stayed there.
- Steering (profiles.py): a USB Pluto's sentence offers the formats, never
  chooses them; the wide profiles set standard PAL back.
- Window: a Format row on the transmit panel (FM only) with an amber
  "OpenHamShack-to-OpenHamShack only: not compatible with standard ATV receivers"
  under it whenever a narrow format is chosen; the plan card's tiles, its
  amber note and "Receiving … — found on the air"; while not keyed the
  card and the spectrum's receive markers describe the format being
  decoded (`rxPlan`), not the one we would send (on air it said "mono"
  over a Mini-MAC picture in colour); the lock badge and the
  picture caption name the format; Settings › FM-ATV explains them and
  holds `formatAuto`.
- AM-VSB has no narrow formats: Mini-MAC has no sync tips for the AM
  receiver's carrier recovery, and the narrow AM picture's only candidate,
  SiS, was rejected.

## NICAM-728 stereo — `ohsatv/nicam.py`

Brian asked for NICAM-728 digital stereo, both ways (5 October 2026). It is
built from the standard, clause by clause, and checked bit for bit against
an independent encoder. (Teletext came from another session's branch:
"Teletext" below is its, and NICAM was fitted round it.)

**Sources** (docs/atv-research.md 22). ETSI EN 300 163 V1.2.1 (1998-03), *NICAM 728: transmission of
two-channel digital sound with terrestrial television systems B, G, H, I,
K1 and L* (every clause number below is its); BBC RD 1990/6, A.J. Bower,
*NICAM 728 - digital two-channel sound for terrestrial television*, for
how and why it was designed; ITU-T J.17 for the pre-emphasis (through
EN 300 163 table 2). hacktv (fsphil/hacktv, `src/nicam728.c`, GPLv3) was
built in a scratch area and run on 48 frames of stereo audio covering every
scale factor; its frames and the samples it coded are in tests/data, and
ours are the same bits (tests/test_nicam.py, Hacktv). Nothing of hacktv
runs here. (BBC RD 1986/2, which the brief also named, is C.K.P. Clarke's
report on line-locked PAL and NTSC colour coding, not NICAM.)

| | System I, as built |
| --- | --- |
| carrier | 6.552 MHz above vision (5.2.3.2), snapped to the 6.25 Hz phasor grid exactly |
| level | 20 dB below peak vision power, 100:1 (5.2.4) |
| bit rate, symbols | 728 kbit/s, 364 kbaud DQPSK (5.2.2, 5.3) |
| shaping | H(f) = cos(pi f / 728 kHz) to 364 kHz at each end: a 100 % raised cosine overall (5.2.5.2). **Not 40 %** - that is B, G, H, K1 and L (5.2.5.1). Occupies 6.188-6.916 MHz |
| phase steps | 00 0, 01 -90, 11 -180, 10 -270 degrees (table 4) |
| frame | 1 ms, 728 bits: FAW 01001110, C0 (8 frames 1, 8 frames 0), C1-C3 application (000 stereo), C4 "the FM sound is the same programme", AD0-AD10, 704 bits of sound (4.1, 4.2) |
| interleave | transmitted bit 24 + j carries frame bit 24 + 44 (j mod 16) + j div 16 (4.1.2) |
| scrambling | x^9 + x^4 + 1 from all ones, every bit after the FAW (4.1.3) |
| sound | 32 kHz, 14 bits, J.17 pre-emphasis; per 32-sample block cut to 10 bits at one of five coding ranges, the 3-bit scale factor carried in the parity bits of nine samples each, recovered by majority (4.2.5) |
| level of the sound | the app's audio full scale is +-50 kHz of FM sound; EN 300 163 4.2.5.2 puts the alignment level (+-17 kHz of System I FM sound at 400 Hz) 24.3 dB under NICAM's maximum: NICAM is as loud as the FM sound |

### Design decisions

- **Whole milliseconds.** A frame is 1 ms and 364 symbols; every rate
  used here is a whole number of samples a millisecond at a decimation
  near 2 MS/s (2 from 10, 16 and 20; 2.5 from 5 and 12.5). The transmitter
  shapes 8 frames at a time in the frequency domain at that rate - the
  spectrum of N symbols lands exactly on the FFT grid of N frames of
  samples - and interpolates up by an integer with a polyphase matrix
  product. A per-sample filter at 20 MS/s took longer than the frame.
- **The 0.16 s loop stays seamless.** A static picture with the tone goes
  out as a loop of four frames: 160 NICAM frames, ten 16-frame sequences.
  The tone repeats, the C0 sequence repeats; the DQPSK phase does not - it
  would step once a loop. In the last frame of each 16-frame sequence AD1
  and AD2 ("reserved for future applications", 4.2.3; receivers ignore
  them) are chosen so the sequence's phase steps add to whole turns.
- **J.17 as a digital filter** fitted to the analogue curve: the zero by
  the pre-warped bilinear transform, the low-frequency gain exact, the
  pole placed for the least worst error - 0.05 dB from 20 Hz to 15 kHz.
  The plain pre-warped bilinear transform was 0.5 dB low in the bass,
  which another NICAM receiver would hear.
- **NICAM sits beside the analogue sound, never instead of it.** As in
  System I, the FM sound (carrier on AM-VSB, subcarrier on FM-ATV) carries
  the compatible mono, (L + R) / 2 (4.2.5.2), and C4 says so. A receiver
  without NICAM, or one that loses it, still has sound. The 6.5 MHz sound
  choice (carrier or subcarrier) sits on NICAM's 6.19-6.92 MHz: the plan
  refuses NICAM with it and says to choose 6.0.
- **The test signal**: with the tone as the source, the tone on the left
  and twice its frequency on the right (1 and 2 kHz), so the channels and
  their separation can be told apart. The microphone is one channel, sent
  to both; a video file's sound goes as its own two channels.
- **Reception is feed-forward, by the millisecond.** The NICAM band is
  taken out with the same channeliser as the FM sound (an FFT that shifts
  and decimates), the matched filter undoes the radio's own roll-off
  (devices/ad936x.py) as the video filters do; symbol timing from the
  squared signal's line at the symbol rate (Oerder and Meyr), averaged
  over 4 ms; frequency error from the fourth power's rotation, phase by
  Viterbi and Viterbi over 32 symbols; quadrant decisions, then the
  phase difference. Frame lock: four exact alignment words in a row,
  anywhere in what is held, whose frames' parity also checks - silence
  makes every frame the same but for C0, and a byte that reads 01001110
  in it repeats at 728 bits too (seen: five false offsets). Lost after
  eight bad words in a row. Not locked, it looks at one block in four.
- **Which sound is heard.** NICAM while its error rate (estimated from the
  parity checks; within a factor of ~3 of the true rate) is under 5e-4,
  the FM sound above 1e-3. Measured on AM-VSB: at 1.1e-3 NICAM's tones were
  57 and 39 dB SINAD against the FM sound's 45, at 3.7e-3 13 dB -
  concealment holds and then gives way all at once.
- **FM-ATV's level.** No standard covers NICAM on FM-ATV. System I's
  spacing (7 dB under the FM sound, 133 kHz RMS deviation) was tried
  first: an FM discriminator's noise rises with frequency, and at 6.55 MHz
  NICAM had 9 % bit errors at 20 dB CNR where the picture was perfect. It
  deviates the carrier by 265 kHz RMS - 1 dB under the analogue sound
  subcarrier, as much as IARU V.2's 7 MHz p-p at 20 MS/s leaves under
  Carson (18.9 of 19.0 MHz).

### Where NICAM fits

| | AM-VSB | FM-ATV |
| --- | --- | --- |
| 5, 6.25, 8 MS/s | no: the signal would span 8.2 MHz | no: 6.92 MHz is past the video Nyquist limit |
| 10 MS/s | **yes**: the vision carrier at -3.00 MHz and the sound at +3.00, either side of the LO for the AD936x's images as without NICAM (docs/atv-research.md 24; centred, -2.86 MHz, left the sound's image at -36 dB); NICAM at +3.55 MHz, edges at -4.25 and +3.92 MHz (0.43 fs, inside the AD936x's flat 0.44); video stays 4.8 MHz | no (as above) |
| 12.5 MS/s | yes | no: the baseband reaches 6.0 MHz |
| 16 MS/s | yes | only at 3.2 MHz p-p or less (Carson); the LibreSDR profile's 4 MHz p-p: no, and the plan says 3.2 |
| 20 MS/s | yes | **yes**, at IARU V.2's 7 MHz p-p |

A receiver listens for NICAM wherever its band ends inside 0.45 fs, sent
or not (`plan["nicamRx"]`): AM-VSB from 10 MS/s, FM from 16. Never with
405 lines (System A had no NICAM) nor the narrow formats.

**With teletext.** The two share nothing - NICAM is a carrier above the
sound, teletext lines in the field blanking - and every mode that carries
NICAM carries teletext too (AM-VSB from 10 MS/s; FM-ATV at 20, or 16 at
3.2 MHz p-p or less). Measured together on AM-VSB 10 MS/s and FM 20 MS/s
at 30 dB CNR through two AD936x: each arrives as it does alone
(tests/test_nicam.py, WithTeletext). The decoder's handling of teletext
lines (which it leaves out of its SNR measurement) does not touch NICAM,
which never passes through the decoder.

### Measured — tests/test_nicam.py

Through two AD936x (helpers.ad936x_link), noise over the whole sample band,
19 kHz offset, the stereo test; bit errors counted against the bits sent:

| | 40 / 30 dB | 20 dB | 15 dB | 14 dB | 13 dB | 12 dB |
| --- | --- | --- | --- | --- | --- | --- |
| AM-VSB 10 MS/s | 0 | 0 | 1.9e-4 | 1.1e-3 | 3.7e-3 | 9.0e-3 (picture 94 % of lines) |
| FM-ATV 20 MS/s, 7 MHz p-p | 6e-5 floor | 2.1e-3 | 8e-2 | | | |

Error-free, the tones come back at 67 and 73 dB SINAD with 0.5 amplitude
(sent 0.5) and more than 60 dB separation (the measurement's floor). The
AM figures match System I's design aim (EN 300 163 clause 1 a): the sound
lasts as long as the picture. On FM it does not - the discriminator's
noise at 6.55 MHz - and the analogue subcarrier, 39 dB SINAD at 20 dB CNR,
is the fallback that matters. The FM floor of ~6e-5 is the radios' filters
cutting NICAM's outer sidebands while the carrier swings to peak white;
concealment hides it. No lock on 2 s of noise, nor on a station sending
only its FM sound (AM and FM). CPU on shack (unloaded): ~10 ms a 40 ms
block at 20 MS/s to transmit, ~10 ms to receive; under the load of the day's
other sessions both doubled.

### In the program

- `nicam` is a switch on the transmit panel; where the plan has no room it
  stays on, nothing extra goes out and the panel says why in amber. The
  plan card says what is sent and where, and the spectrum marks NICAM
  beside the sound.
- A sixth lock badge says what sound is heard: STEREO or MONO from NICAM,
  FM when NICAM is there but the analogue sound has taken over, with the
  estimated error rate in its caption.
- Receive sound may be stereo: the audio backend opens two output channels
  where the card has them (`play_stereo`); otherwise it plays the mix.
- The simulator's PAL stations send NICAM where their mode has room
  (`simNicam`).
## Teletext — `ohsatv/vbi.py`, `ttx.py`, `tti.py`, `carousel.py`, `ttxrender.py`

World System Teletext, 625-line System B, Level 1 (ETSI EN 300 706 V1.2.1;
sourced facts in docs/atv-research.md section 20). The pages are made in
the Teletext editor, `ohstxt/` - until October 2026 the separate program
OpenHamShack Teletext (`~/openhamshack-teletext`), now folded into this
repository as a package of its own beside `ohsatv/`. The contract between
editor and engine is still the file format - a folder of MRG `.tti` files
as vbit2 reads them, or a `.t42` stream - and the engine imports nothing of
ohstxt: `ohsatv/tti.py` and `ohstxt/tti.py` stay two implementations on
purpose (a transmit-side reader that forgives what vbit2 forgives, and the
editor's page model and writer), so each keeps checking the other in
tests/test_editor_e2e.py. Only the window imports ohstxt, to open the editor
on the transmit panel's folder (`MainWindow.open_teletext_editor`,
tests/test_txt_handover.py); `python3 -m ohstxt` and `openhamshack-atv --teletext`
run it alone. The editor's own contract is docs/teletext-editor.md (its DESIGN.md,
copied at Teletext 1cda694), and its tests are tests/test_txt_*.py.

```
 .tti folder ─► tti.load_service ─► Carousel (a field at a time) ─► vbi.Inserter ─► + PAL frame ─► modulator
 decoder frame ─► vbi.Slicer ─► 42-byte packets ─► ttx.PageStore ─► Radio.ttx_page() ─► ttxrender ─► viewer
```

- **On the line** (`vbi.py`): 6.9375 Mbit/s NRZ, run-in + framing code,
  ones at 0.462 V, the penultimate run-in one at 12.0 µs, a raised-cosine
  spectrum (roll-off 0.44) that is zero by 5.0 MHz. Lines 7-22 and
  320-335 by default (`ttxLines`; the standard's 6-22, 319-335 allowed):
  32 a frame, 800 packets/s. Built as one matrix product a frame (lines
  start on whole samples, so each bit's pulse is at the same place in every
  line): insert and carousel 2-4 ms a frame at 10-20 MS/s.
- **The slicer** takes each blanked line from the decoder (volts, cut at
  whole samples, so up to half a sample off): the run-in's 3.47 MHz tone
  gives the bit phase and the slicing level; the framing code is sought at
  that phase and ±1, ±2 bit pairs; bits are read at their centres by a
  windowed-sinc interpolator (weights from a 1/128-sample table: 0.65 ms a
  frame). Gates measured against false packets: the tone against its
  residual, the tone and its mean in volts (a quiet line's noise sits at
  0 V), an exact framing code unless the run-in is strong, and no slicing
  below 6.94 MS/s (the run-in past Nyquist gave false packets at 6.25).
  None in 4 000 frames of pictures without teletext, 10-30 dB CNR.
- **The decoder's SNR** used lines 8-15/321-328 as empty; teletext fills
  them. Lines the slicer found data on are left out, and with fewer than
  four left the noise is measured on the blanked lines' sync tips. With
  and without teletext the figure agrees within 1 dB (tested).
- **Packets and pages** (`ttx.py`): Hamming 8/4 (table built from the
  encoder by distance), odd parity, X/0 with C4-C14 and the clock, X/1-X/25,
  X/27/0 links with the page CRC, 8/30 Format 1 (initial page, MJD/UTC,
  status line with the callsign) once a second. **Byte for byte what vbit2
  sends** - header, all 24 rows, links and CRC - checked against a capture
  vbit2 made from OpenHamShack Teletext's files (tests/fixtures) - and **hacktv's
  too**, a second inserter written independently: its own teletext.c run
  on its demo page and one of ours (both control-code forms, links to
  three magazines), header, rows, X/27/0 and its CRC equal byte for byte
  (tests/fixtures/hacktv, whose README says where it came from). Two things
  that capture corrected: the CRC is summed over the bytes with their
  parity bits, and blank rows are sent as spaces (so an edit that empties
  a row clears it on the receiver).
- **The carousel**: magazines dealt lines in turn (parallel mode), each
  page its header, rows 1-24 and links; nothing more of a magazine in the
  field of its header (the 20 ms "page erasure interval", Annex B.6);
  carousels advanced on transmission time (CT seconds, from the frame
  count) or magazine cycles (CT,n,C). The header shows the callsign: the
  ident rule covers the picture, and teletext is part of it.
- **.tti** (`tti.py`), as vbit2's reader and OpenHamShack Teletext's DESIGN.md
  set it down: ESC + code + 40 (what the editor writes) and MRG's 80-9F
  both read, and a file re-saved as UTF-8 (C2 80+n, a '£' as C2 A3, or
  via Windows' code page 1252) is read as the code points it holds - any
  non-ASCII in a file that decodes as UTF-8 says so; PS and CT carry forward to later subpages; a carousel's
  subpages renumbered 0001...; FL outside 100-8FF is no link. Two
  departures, on purpose: a file **without** PS is sent (vbit2 would send
  nothing, silently), and C11 (serial) is cleared, because we interleave
  magazines. Read again at every key-up: a page edited between overs goes
  out on the next one.
- **Transmit**: teletext changes every field, so with it on even a static
  picture goes out as a live source (encoded once per frame of its colour
  sequence, the packets added to a copy).
- **Receive**: every frame's packets go into the page store, picture
  built or not. A character that fails parity keeps the last cycle's (a
  weak signal's page fills in instead of showing garbage). **No row on
  the wrong page**: the page being received closes - rows kept, the rest
  dropped until the next header - wherever its end might have gone by
  unseen: an uncorrectable header (its magazine), a packet whose address
  cannot be read (its magazine, or all if that is unreadable), a row not
  above the last one or a second X/27/0 (a missed header: inserters send
  rows in order), and a break in the signal, which the decoder reports on
  the next frame (and no frame carries lines from both sides of one). At
  3 % packets lost, 5 % damaged and breaks of up to 40 frames, no row or
  link lands on another page and over 90 % of rows are exact (tested,
  MisAssembly); before, one did in over half of all frames. The one case
  left: an inserter that leaves blank rows out (hacktv) can lose a header
  with the next page's rows still ascending - invisible in the packets;
  ours sends every row. Retuning clears the store.
- **The viewer** (`ui/teletext.py`, `ttxrender.py`, `ttxfont.py`): a Level
  1 attribute decoder (set-at / set-after, hold mosaics, double height,
  flash, conceal, new background, blast-through) and a character set
  generated in code - 5 × 9 dots doubled with SAA5050-style character
  rounding, the English national set, contiguous and separated mosaics.
  In the picture card (Text / T): digits, F1-F4 and the coloured keys,
  clicking page numbers, subpage stepping, reveal, hold, mix.

### Edited on air, subtitles, saving

- **Pages change while on air** (`tti.FolderWatch`, `Carousel.update`):
  the folder is polled twice a second from the encoder thread (a stat a
  file; nothing read unless something changed). A file is read once it has
  held still for 0.4 s; if it then does not parse, the page it was stays
  and a toast says so; dot-files (the editor's temporaries) are never
  pages. The carousel takes the new service at the next field: a page
  going out finishes as it was, each changed page goes out new the next
  time its magazine reaches it - within a cycle, with no restart and no
  empty field (tested, and through the engine with one transmission in the
  log). Written for the editor's live-data generator, which rewrites pages
  every minute or so.
- **Subtitles on 888** (`ohsatv/subtitles.py`): C6 (and C7), each line
  double height in its own box, laid out exactly as OpenHamShack Teletext lays it (double height, start box ×2, colour inside the box, text,
  end box ×2), centred on rows 20 and 22, at most two lines of 37 cells (its SRT subset). A change goes out
  ahead of magazine 8's next page with C4, a repeat every 2 s without it.
  Sources: an **SRT** (the contract with the editor; `<font color>` to the
  nearest teletext colour) timed to the **video file's playback position**
  - the file source now pauses and seeks, by restarting ffmpeg at the
  position (`-ss`), because `-re` races to catch up after a pause - or
  from key-up for other sources; and **captions typed** on the transmit
  panel, over the SRT for their reading time. With no page folder,
  teletext is subtitles alone. **Subtitles are teletext: they fail in the
  narrow modes exactly as teletext does** (the same rule and sentence:
  "Teletext and subtitles are not sent: they need about 3.75 MHz of
  video..."), and need the same CNR as the table below. On receive the 888
  button (or S) draws page 888's boxed rows over the picture.
- **Saving received pages**: the viewer writes a page, or every page and a
  .t42 of the last ~50 s of packets, in vbit2's dialect as the editor
  writes it (ESC + 0x40, CT after PN, CRLF, 7-bit). The editor's reader
  opens them (checked by hand); ours reads them back row for row (tested).

### With the real editor — `tests/test_editor_e2e.py`

An automated test that imports the editor only to make and read files
(it skipped where `~/openhamshack-teletext` was missing until the editor moved
into this repository). Against OpenHamShack Teletext main 9b236a0: its templates, a live page
with its `.live.json` sidecar and its sample carousel pass `ohstxt.check`;
sent over the simulator, every page and subpage comes back equal cell by
cell (codes and Level 1 layout) with its links; its live runner rewriting
a page on air (temp file, rename) reaches the receiver in 1.3 s - the
folder poll and settle (0.9 s) plus under one 0.29 s turn of the service -
with the sidecars, state, station.json and beacons.csv never read; an SRT
from its exporter shows on 888 exactly as its `cue_rows()` lays each cue
out (so ours now centres the text, not the boxed whole, and sets C6 + C4
as it does, not C5 or C7); saved pages open in its `.tti` reader as sent,
and its `.t42` reception reads the capture - where a capture spans a live
rewrite, its per-cell vote gives each cell from one version or the other.
Inserter: 16 lines a field (station.json's `vbi_lines` default matches).

### 405 lines and the cable feed

Teletext and subtitles are **625 lines only**: World System Teletext is
defined on 625-line blanking (EN 300 706 4.1) and System A never carried
it. With `videoFormat` 405 the plan refuses them ("Teletext and subtitles
are not sent with 405 lines ..."), in amber like any refusal, and nothing
is inserted. The decoder builds no slicer for a 405-line standard, so a
405 picture - chosen, or found on the air by `formatAuto` - is never
sliced; the viewer says "405-line pictures carry no teletext". Detection
changes only the receiver: our own teletext goes on being sent if what we
send is still 625 PAL.

**On the cable feed it depends on the band.** On Band I the feed puts
405 lines on the BBC's old channels into a vintage set, which has no
teletext decoder: refused, with its own sentence, which says to choose
UHF. On UHF the feed is 625-line PAL (System I) into a teletext-era set
(added 5 October 2026, at Brian's request; DESIGN.md "Cable feed"), and
teletext and subtitles go with it under the same rule as on air - the
plan's bandwidth decides, so 5 MS/s is refused and 8 MS/s carries it with
the note. Measured through the simulator (`tools/teletext_eval.py
--cable`, 1 dB steps, 1 184 packets a point): PER <= 1 % from 25 dB CNR
at 8 MS/s, 24 at 10, 23 at 12.5 and 16 MS/s; nothing lost from 27, 25,
25, 24. The catch is the level, not the mode: a Pluto's least output is
estimated to give a set 10-16 dB ("Cable feed"). Tested:
`tests/test_teletext.py` Lines405 and CableUhf (decoded off the
loopback, pages and 888), `tests/test_radio.py` CableFeed,
`tests/test_bands.py` CableUhf.

### Which modes carry it — measured (`tools/teletext_eval.py`, docs/teletext-eval.md)

Through the real chain and narrow_eval's AD936x channel (filtering at both
ends, noise over the whole sample band, 19 kHz offset), 32 packets of
random text a frame on 75 % bars; PER = packets not received exactly.

| mode | video | carries | PER ≤ 1 % from | ≤ 10 % from |
| --- | --- | --- | --- | --- |
| AM 5 / 6.25 MS/s | 2.4 / 3.0 MHz | **no** | never (100 % at 40 dB) | |
| AM 8 MS/s | 3.84 MHz | yes, with the note | 26 dB | 24 dB |
| AM 10 / 12.5 / 16 MS/s | 4.8-5.5 MHz | yes | 24 dB | 22 dB |
| FM 20 MS/s, 7 MHz p-p (IARU V.2) | 5.0 MHz | yes | **10 dB** | 10 dB |
| FM 16 MS/s, 4 MHz p-p | 5.0 MHz | yes | 16 dB | 14 dB |
| FM 12.5 MS/s, 1.2 MHz p-p (8-bit RX profile) | 4.9 MHz | yes | 26 dB | 24 dB |
| FM 10 MS/s, 3 MHz p-p (8-bit TX profile) | 3.0 MHz | **no** | never | |
| FM 5 / 6.25 MS/s (USB Pluto) | 1.6 / 2.0 MHz | **no** | never | |
| Low-PAL, Mini-MAC | — | **no** (no PAL blanked lines) | | |

The rule (`narrow.teletext_fit`) is the video bandwidth: **3.75 MHz or
more**. With the modem's video filter swept at high CNR every packet gets
through down to 3.5 MHz and none at 3.25 (the data's Nyquist frequency is
3.47 MHz); a CNR sweep at each width put 3.5 MHz about 2 dB behind 3.75-5
MHz near threshold (AM at 22 dB: 15 % against ~1 %), so 3.75 is the line,
with that margin. Below 4.75 MHz the plan adds that the spectrum is cut
inside the standard's 5 MHz: our slicer is measured to take it, a
television's decoder has not been. A refused mode inserts **nothing**, and
the transmit panel says why in amber (`ttxRefused`).

What the numbers say: teletext wants a video SNR of 22-25 dB (the
decoder's own estimate at the 1 % point, every mode alike) - well above
what a watchable picture needs (AM locks at ~9 dB CNR, teletext needs 24).
So on FM, deviation buys it: IARU V.2's 7 MHz p-p carries it to 10 dB CNR,
the 8-bit receive profile's 1.2 MHz p-p needs 26. A USB Pluto transmitting
(5 MS/s) cannot send it at all; a USB nano *receiving* at 12.5 MS/s 8-bit
can receive it from an Ethernet radio or a HackRF. Nothing here has been
on air.

## 405 lines — `ohsatv/sysa.py`, `ohsatv/lines.py`

Brian asked (5 October 2026) for the British 405-line standard, CCIR
System A, transmitted and received on AM in the amateur bands, and fed by
cable into vintage sets (next section). It is a picture format on AM-VSB
(`videoFormat` "405"), beside 625-line PAL.

### The standard, and where each number comes from

Sources, read for this (nothing from memory):

- **CCIR Report 624-2**, "Characteristics of television systems",
  *Recommendations and Reports of the CCIR, 1982*, Volume XI-1 (ITU
  history collection, 4.281.43.en.1013): Table I (lines, rates, levels,
  bandwidth), Table 1-1 (line sync), Table 1-2 (field sync) and Table III
  (radiated signal). The last edition that described System A: ITU-R
  BT.470-6 (1998), note 1, says "Pre-1986 editions ... in particular that
  of 1982, contain a complete description of ... system A used in the
  United Kingdom until 1985". (The brief named CCIR Report 308; in the
  1982 volume the characteristics are Report 624-2, which is what was
  read.)
- **BVWS**: Steve Pendlebury, "Frequencies and Handy Numbers" (Old
  Tellys, archive.bvws.org.uk/oldtellys/otfrequenc.html), for the Band I
  channels and 10.125 kHz; Andy Emmerson, "Modulators for 405 Lines"
  (405 Alive, archive.bvws.org.uk/405alive/tech/modulator.html), for
  "channel 1 (45MHz vision, 41.5MHz sound)" and zero carrier at the sync
  tips.
- **hacktv** (fsphil/hacktv, src/video.c: `vid_config_405_a` and the
  `VID_RASTER_405` line sequences), to cross-check, at Brian's pointer.
  It is GPLv3 like this program; no code was taken from it.

| | used | Report 624-2 | hacktv mode "a" |
| --- | --- | --- | --- |
| lines, fields, line rate | 405, 50, 10 125 Hz (98.765 us) | Table I 1-3; 1-1 H "98.8" | 405, 25 fps interlaced |
| line blanking | 18.0 us | 1-1 a: 17.5-19 | 18.47 (from its picture's 16.8 + 80.3) |
| front porch | 1.75 us | c: 1.5-2.0 | 1.67 |
| line sync | 9.0 us | d: 8-10 | 9.00 |
| 0H to the picture | 16.25 us (picture 80.77 us) | b: 16-17 | 16.8 (80.3) |
| sync / blanking edges (10-90 %) | 0.2 / 0.35 us | f, s: < 0.25; e: 0.25-0.5 | 0.25 |
| field sync | 8 broad pulses of 40.0 us at half-line spacing (gaps 9.38 us), from 0H of line 1 and from the middle of line 203; **no equalising pulses** | 1-2 m: 4 H; q: 38-42; r: 7.4-11.4; note 4 | 40.0 us; lines 1-4 and 204-206 "V__V", 203 "h0aV", 207 "V___": the same |
| field blanking | 14 H + a: lines 1-14 and 203½-217½ blanked, 377 picture lines (15-203½, 217½-405) | j: (13 to 15.5) H + a, starting 0.015-0.515 H before field sync | 15 H + a: lines 16-203½, 218½-405 |
| levels (carrier) | sync 0, blanking = black 30 %, white 100 % | III 7-8: sync < 3, blanking 30, black-blanking 0, white 100; positive | sync 0, blanking 0.30, black 0.30, white 1.00 |
| levels (video) | sync -0.3 V, white +0.7 V as pal.py | I 4: sync -43 % of white | — |
| picture bandwidth | 3 MHz, *below* vision | I 6, III 4 | vsb_lower_bw 3 MHz |
| vestige | 0.75 MHz *above* vision (whole to 0.5, gone by 0.75) | III 5; attenuation "not specified" (6) | vsb_upper_bw 0.75 MHz |
| channel | 5 MHz, top edge vision + 1.25 MHz | III 3 (+1.25: A is inverted) | — |
| sound | AM 3.5 MHz below vision, no pre-emphasis, audio to 10 kHz | III 2, 9, 11 (blank for A) | am_mono_carrier -3.5 MHz, 10 kHz |
| vision : sound | 4 : 1 e.r.p., sound -6 dB | III 12 | video 0.8, audio 0.2 (amplitudes, -12 dB) |
| colour | none | System A was monochrome | — |

Where they differ:

- **Sound level.** hacktv's 0.8 / 0.2 is a 12 dB ratio; the Report's 4/1
  is in effective radiated power, 6 dB. The Report is followed.
- **Which picture lines, and which field is on top.** The Report allows
  13-15.5 lines of field blanking; hacktv blanks 15, we take 14 - the 377
  lines always quoted for 405. hacktv puts line 16 (field 1) on the top
  row and line 218 below it. By timing, though, field 2's lines are half
  a line *above* field 1's: field 2's broad pulses start 202.5 lines after
  field 1's, so line 217 begins 13.5 lines after its field sync and line
  15 14 lines after its own. The row map here follows the timing, as
  pal.py's does for 625 (where field 2's lines come out *below*); hacktv
  would show a progressive picture with alternate lines a row out of
  place. A vintage set does not care which; a picture's diagonals do.
- **Black.** BVWS's page gives black at 35 %; the Report says no set-up
  (black = blanking). The Report is followed.
- **Edges.** hacktv uses 0.25 us; the Report's "under 0.25" is taken as
  0.2. The channel's own 3 MHz shapes them on air anyway.

The Report assumes a display gamma of 2.8 (Table I 5). Sources here are
already gamma-corrected R'G'B' for a ~2.2 display and are sent as they
are, as for 625 lines (System I also assumes 2.8).

### How it is built

**System A is inverted** against every later system: the sound carrier is
3.5 MHz *below* vision, the 3 MHz picture sideband lies between them and
the 0.75 MHz vestige is *above* the carrier (Report Table III item 3 gives
the channel's nearest edge as +1.25 MHz for A, -1.25 for every other
system). So the whole signal, sound included, is **4.3 MHz** - which fits
the ~5 MS/s a USB Pluto transmits with its full 3 MHz picture and its
sound, centred so that everything stays inside 0.43 fs, clear of the
AD936x's last half-band. The plan is the same at every rate offered (5 to
20 MS/s): 4.3 MHz occupied, 3 MHz of mono picture, sound; the vision
carrier 1.4 MHz above the LO.

**Lines are never whole samples.** 98.765 us is 493.83 samples at 5 MS/s;
a 40 ms frame is always whole. And 3 MHz of picture is not a real signal
at 5 MS/s (2.5 MHz Nyquist), although its single sideband fits the complex
band. So:

- the encoder builds each frame as pal.py does - 405 x N arrays, sync from
  a half-line slot table, the picture placed by a row map - at its own
  line-locked rate, 750 samples a line (7.59 MS/s, Nyquist 3.8 MHz),
  wherever the radio runs;
- the modulator transforms the frame's envelope (sync 0, blanking 0.3,
  white 1.0) and builds the vestigial-sideband spectrum directly at the
  radio's rate: each output bin takes the envelope's component at its
  distance from the vision carrier times the sideband shape there, and
  one inverse transform makes the frame's IQ. The carrier sits on the
  frame's 25 Hz bin grid, so it is exact; frames join seamlessly because
  each ends on line 405's front porch and begins with line 1's broad pulse.
  The vestigial filter, the band limit and the radio's own response
  (`tx_shape`, devices/ad936x.py) are one multiply;
- below 7.5 MS/s the receiver interpolates by two inside its receive
  filter (zeros between samples, the images cut by the same taps), so the
  picture comes out at twice the radio's rate (`sysa.video_rate`) with
  room for 3 MHz;
- the decoder takes a line standard (ohsatv/lines.py) instead of having 625
  written in: the same method, other numbers. Its fold takes each line from
  its nearest sample, and for a mono standard each line is moved by its own
  leftover fraction of a sample (exactly, in the frequency domain) before
  the picture is built - without that every vertical edge zig-zags by up
  to half a sample, measured at 0.5-1.8 dB of luma PSNR; with it a
  vertical edge stays within 10 ns (tests/test_sysa.py). 625 lines keep
  whole samples, for colour's sake.

**Positive modulation** sends no carrier at the sync tips, which is where
the 625-line synchronous detector takes its carrier phase from. The 405
detector takes it from the plain sum of each line-long stretch instead:
a line holds every part of itself once, so its sum is the carrier times
the line's mean level, never below blanking's 30 % for long, and the
vestigial sideband's quadrature part - the picture's detail - sums to
nothing. The residual carrier offset - up to the 20 kHz at which tracking
redesigns the receive filter - comes off in steps of 8 samples before each
line is summed: a phasor per step, not per sample (per sample was a third
of the demodulator's time). Summing first and correcting the sums was tried
and is wrong above about an FFT bin: at 14.5 kHz a line turns 9 rad and its
sum cancels, and the simulator's 405 station held 47 % of its lines.

**AM sound**: the 48 kHz audio low-passed to 10 kHz, the carrier at 0.5 of
peak vision amplitude (-6 dB), modulated 80 % deep at full scale (the
1 kHz test tone, at half scale, 40 %). Received as the envelope of a
40 kHz channel over its own smoothed mean, so the audio level does not
follow the signal's.

**Images outside the channel, and no sound at 5 MS/s.** On 5 October 2026
the nano at 5 MS/s put the sound carrier's image 0.7 MHz above the channel
at -13.4 dBc, and the HackRF at 8 MS/s its sound and vision images 8 MHz
out at -18.6 and -29 (research 21); the limit is -40 dBc, and a band-pass
filter cannot reach them - they are inside the band, beside the signal.
Brian decided (5 October): on a Pluto 405 lines at 5 MS/s go **without
sound** - the sound carrier is not generated, and the plan card and the
transmit panel say why (`sysa.SOUND_MIN_FS`) - and with sound from 6.25
MS/s; on a HackRF, 20 MS/s (`a405Rate`, steered by the HackRF profile).
Without sound the channel's edges are the picture's weakest parts, and the
AD936x model puts every image at 5 MS/s under -68 dBc. At 6.25 MS/s with
the signal centred the sound's image is still -30 dBc, so the plan places
the two carriers equally far either side of the LO (vision +1.75, sound
-1.75 MHz) whenever centred would exceed the limit and that placement
fits: both images then come out at -48 dBc, the picture's under -59
(`sysa._placement`; tests/test_sysa.py builds the whole signal through the
chip's interpolation to check). 8 MS/s and up keep the centred placement
that went on air. A HackRF has no interpolating filter, so its images face
only the converter's hold and the MAX2837's baseband filter, which the
driver now narrows to the plan before every over (5 MHz for 405 lines; the
model, from the datasheet with a measured shortfall, is in
devices/hackrf.py): at 20 MS/s the images are 20 MHz out and about -78
dBc. Whatever a transmitter's model still puts above -40 dBc is said on
the transmit panel (`sysa.tx_image_warning`) - a warning, not a refusal.

**On air or by cable, which?** `formatAuto` now works on AM: when nothing
is decoded for a second, `sysa.detect()` folds the envelope round the
vision carrier at both line rates and measures the sync's depth against
the mean level - a 9 us dip for System A (positive), a 4.7 us peak for 625
(negative). On the right standard the depth is 0.5-1.0 (625 on a black
picture 0.25-0.28, whose sync is only 24 % above black); folded at the
wrong rate, or on noise, under 0.07. It names one when the depth is over
0.15 and three times the other's. A first version correlated the fold
with a pulse template, as narrow.detect() does for FM, and called a
405-line test card neither (0.88 against 0.82): a card has vertical
structure that a template matches at either rate. While locked it does
not look again: neither decoder locks to the other's signal (tested both
ways: no line ever found), so a locked AM picture is the right one. FM's
detection is untouched. The receiver's LO moves with the format, since
the two put the vision carrier in different places.

### Measured — tests/test_sysa.py

Encoder -> modulator -> noise and a 21 kHz offset -> demodulator ->
decoder, the 405 test card (gratings 0.6-3 MHz) and a 1 kHz tone, CNR over
the whole sample band (as test_chain.py). Thresholds: lock = 90 % of lines
and field sync; picture = 15 dB luma PSNR; sound = 20 dB SINAD.

| rate | lock | picture | sound | at 30 dB: PSNR / SINAD | noiseless |
| --- | --- | --- | --- | --- | --- |
| 5 MS/s | 14 dB | 16 dB | none sent (see Images) | 26.9 / - | 30.1 / - |
| 6.25 MS/s | 14 dB | 16 dB | 8 dB | 28.2 / 42.4 dB | 32.2 / 70 dB |
| 10 MS/s | 10 dB | 14 dB | 6 dB | 28.2 / 44.1 dB | 30.1 / 58 dB |
| 5 MS/s through two AD936x (helpers.ad936x_link, each modem undoing its radio) | 12 dB | 14 dB | 10 dB | 27.8 / 33.0 dB | |

A 625-line mono AM picture at 5-6.25 MS/s locks at 14 dB and has its
picture at 16 (docs/narrow-eval.md), with no sound and 2.4-3 MHz of video:
405 lines match it while carrying their full 3 MHz, and from 6.25 MS/s
their sound. (The 5 MS/s sound figures, 10 dB and 41.5 dB SINAD at 30, were
measured before the sound was left off there; the 6.25 MS/s row is from
the centred placement, and the placed one keeps sound above 30 dB SINAD at
30 dB through two radios.) PSNR is against the undegraded card, so its ceiling is the
3 MHz channel's. Noise alone never claimed a 405-line field in 9 000
blocks (5, 6.25 and 10 MS/s, 3 000 each). Timing, levels, the sound
carrier's place and level, and the occupied band (under -40 dBc outside
-3.6..+0.85 MHz, and outside 0.45 fs at 5 MS/s) are measured from the
samples.

## Cable feed — feeding a television by cable

Two kinds of set, two bands. A 405-line set tunes only the old Band I
channels, B1-B5 (41-68 MHz). A UK 625-line set tunes only UHF, so a 625-line
picture - and teletext, which is 625 lines only - goes on a UHF channel
(added 5 October 2026: Brian wanted teletext into a UHF set by cable).
Neither is an amateur allocation, so this is for a cable from the radio,
through attenuation, into the set's aerial socket, and it must never
radiate. The design is the conservative one throughout:

- **`cableFeed`**, Settings › Transmit, is off by default and **never
  persisted**: every start of the program begins with it off, and a
  config saved on Band I or UHF comes back on 23cm. Chosen over asking
  again at each start: the risk is a radio left cabled to a television one
  evening and on an antenna the next, and a setting that cannot outlive
  the session cannot carry over. It is an `auth` key, so only the Settings
  dialog changes it.
- **Turning it on needs `cableFeedAck`** ticked: "The output goes by
  cable, through attenuation, into a receiver's aerial socket - never to
  an antenna." Also never persisted, and unticking it turns the feed off.
  On, the radio goes to Band I, AM-VSB, 405 lines, as it always has; UHF
  is then in the band list, and choosing it goes to E36 and 625-line PAL
  (choosing Band I again goes back to 405). Off, it leaves either band
  (which cannot be chosen without it).
- **While on, `bands.cable_check()` replaces the amateur guard**, it does
  not add to it. Each band takes its one format and no other - a 405-line
  set cannot show 625 lines and a UK 625-line set cannot tune Band I, so
  the wrong one on either is a picture nobody on the cable can use:
  - **Band I: 405-line System A only**, the vision carrier on one of B1
    45.00, B2 51.75, B3 56.75, B4 61.75, B5 66.75 MHz (sound 3.5 MHz below
    each: 41.50, 48.25, 53.25, 58.25, 63.25 - Old Tellys, 405 Alive, and
    the Report's spacing). A Pluto on Tezuka firmware (from 46.875 MHz)
    sends B2-B5 and is told so on B1, whose sound is at 41.5; a stock
    Pluto (70 MHz) none; a HackRF (1 MHz) all five.
  - **UHF: 625-line PAL, System I, only**, the vision carrier on one of
    **E21-E48 except E38**: vision 8N + 303.25 MHz, sound 6 MHz above
    (BVWS Old Tellys; E21 471.25, E36 591.25, E48 687.25), and the whole
    signal inside the channel's 8 MHz, 1.25 MHz below vision to 6.75 above
    (ITU-R BT.470-6 Table 3) - the set may be tuned to the next channel.
    Why those: a vintage set tunes 21-68, but since the 700 MHz clearance
    (Ofcom, 2020) 49-60 is 5G and 61-68 4G, and a cable that leaks there
    leaks into other people's phone networks; 21-48 is the band UK
    television still occupies, the band a TV tuner is built for. E38 is out
    because 608-614 MHz is radio astronomy, which Ofcom keeps even PMSE out
    of. **E36 (591.25 MHz) is the default**: the UK's VCR and RF-modulator
    channel, left clear of broadcasting until 1997, so the likeliest to be
    on a vintage set's spare preset already. The band card shows E21, E24,
    E28, E32, E36, E40, E44, E48 as chips; the others are typed. Every
    radio here tunes UHF: a stock Pluto from 325 MHz (AD9363), a HackRF
    from 1 MHz. Sources: docs/atv-research.md 23.
  - Either way, the whole signal, sound carrier to vestige, inside what the
    transmitting radio tunes. Nothing else at all may be sent while it is
    on - not the amateur bands either: the output is cabled to a
    television.
- **The modulator needs nothing for UHF.** AM-VSB was already built to
  System I (`ohsatv/modem.py`, checked against BT.470-6 Table 3,
  docs/atv-research.md 3.4 and 22): negative modulation at 100 / 76 / 20 %
  of peak carrier for sync / blanking / peak white, 5.5 MHz main sideband,
  1.25 MHz vestige, and the sound an **FM carrier at +5.9996 MHz**, ±50
  kHz, 50 us pre-emphasis, 13 dB below peak vision (the UK's 20:1). That
  is System I's sound, unlike 405 lines' AM sound below vision (`sysa.py`
  has its own). What a rate keeps is the plan's, as on air: 8 MS/s keeps
  the sound and 3.84 MHz of video (colour, teletext with the note), 10
  MS/s 4.8 MHz, 12.5 MS/s and up all 5.5 MHz; 5-6.25 MS/s drop the sound
  and colour and cannot carry teletext. No NICAM (none is built). **Use
  10 MS/s or more on the cable**: at 8 MS/s the sound carrier is at 0.45
  fs, in the AD936x half-band's transition, where the 405-line tests on
  air found the sound's image 13 dB down (docs/atv-research.md 21); here
  it would land 1.15 MHz below vision, inside the channel's vestige. At
  10 MS/s the sound is at 0.36 fs. Not measured for 625 lines.
- **Teletext and subtitles on UHF are allowed**, under the same rule as on
  air (`narrow.teletext_fit`: 3.75 MHz of video or more). **On Band I they
  are refused** with their own sentence, which points at UHF. Measured
  through the simulator (`tools/teletext_eval.py --cable`, in
  docs/teletext-eval.md): see "Teletext" › "405 lines and the cable feed".
- **Power is the radio's least**, whatever the power control says:
  -89.75 dB on a Pluto; on a HackRF that is TX VGA 0, and its amplifier is
  held off whatever Settings › Transmit says. The window has no power
  control while it is on. Neither has been measured on a cable. Estimated
  from the Pluto's full output (about +3.5 dBm at 432 MHz, K0SM; the
  AD9361's 8 dBm at 800 MHz), its least is about -86 dBm at 50 MHz and
  -86 to -82 dBm at UHF, 14-22 uV into 75 ohms. **That is probably too weak
  for teletext on a television.** A set's tuner sees -106.6 dBm of thermal
  noise in 5.5 MHz plus its noise figure (6-10 dB assumed, not looked up
  for any set): a CNR of roughly 10-16 dB, a snowy picture, where our own
  slicer needs about 24 dB (measured, below) and a set's decoder no less.
  A HackRF at VGA 0 is tens of dB more (5-15 dBm full scale below 2.17
  GHz, 47 dB of VGA: about -42 to -32 dBm), enough with a 30 dB
  attenuator. The power rule is kept as it was - raising it is Brian's
  decision, not this feature's - and the on-air test plan
  (docs/teletext-on-air-test.md) says how to find out on the bench. The
  attenuator is still the rule: it is what protects the set, and the air,
  when the radio does not do as it was told.
- **Seen and logged.** A red CABLE FEED — NOT FOR ANTENNAS banner at the
  top of the transmit panel for as long as it is on, keyed or not; Band I
  and UHF only in the band selector while it is on, their strips hatched
  end to end. Every key-up's log line carries `cableFeed`, true or false,
  the band and the gain actually used. Switching the feed either way while
  keyed unkeys.
- **The amateur guard is unchanged with it off**: a test sweeps 40 MHz to
  6 GHz and finds nothing outside the UK allocations passes, Band I and
  every UHF cable channel included, and the engine refuses both bands
  without the feed.

The simulator tunes down to 40 MHz so the feed can be shown and tested
with nothing connected; its loopback ignores the transmit gain, so the
picture there is as clean as the simulator's CNR, not the least output's.

## Engine — `ohsatv/radio.py`

As OpenHamShack NB: `Radio(cfg)`, `start()`, `set(**changes)` validated against
`ohsatv/state.py` (every key: type, range, choices, help; the single source of
truth), `state()`, `ptt(on)`, `subscribe(kind) -> Feed`, `heartbeat()`,
`stop()`. Feeds are bounded drop-oldest queues: `video` (decoded RX frames),
`txvideo` (the preview), `spectrum`, `scope` (waveform monitor line +
vectorscope), `meter`, `state`, `toast`. Contract in docs/ENGINE-API.md.

Threads: **rx** reads, demodulates, decodes; **tx** exists only while keyed
(source → ident → encode → modulate → write; a static source is a precomputed
loop); **supervisor** at 10 Hz runs the safety timers and persists config.
Decoding is a thread of its own behind a queue of up to eight 40 ms blocks.
**When it falls behind it skips pictures, not signal**: a block taken while
more are waiting is decoded for its timing, line count and Mini-MAC's sound
but its picture is not built (`decodeSkipped` counts both kinds). Only if
even that cannot keep up is a block dropped, which breaks the line timing
and loses the sound carried in it (it used to be the only way, with a queue
of three: see "Mini-MAC's sound").

**Transmit log.** Every key-up and unkey is a JSON line in
`$OHSATV_DATA/txlog.jsonl` (time, frequency, band, system, picture format,
device, callsign, source, the gain actually used, whether the cable feed was
on, the warnings that applied; duration and reason at unkey), written at key-up as well, so a crash mid-over still leaves the
record that it began.

## Safety rules (non-negotiable)

1. Never transmits at start, after a restart, a reconnect or a settings save.
   Only `ptt(True)` keys.
2. **Real hardware (Pluto or HackRF) does not transmit until `allowHardwareTx` is set in
   Settings** (default off; `auth` key). Brian decides when that changes — it
   is his licence.
3. Transmit only as `bands.tx_check()` allows (whole signal inside the
   station licence's allocation, from the shared dataset and the station
   profile, a 70cm edge warned rather than refused; carrier in an ATV
   segment where the bandplan has them;
   on 70cm the bandplan's absence of analogue ATV is a warning, logged),
   within the device's tuning range, and when the mode's plan fits the sample
   rate. The one exception is the cable feed (never persisted, confirmed
   by a tick, Band I B1-B5 with 405 lines or UHF E21-E48 with 625-line PAL,
   least power, nothing else while on),
   whose own guard `bands.cable_check()` then replaces this one. On the simulator the guard's refusals are shown but do not stop the
   loopback — nothing leaves the machine — so every mode can be demonstrated.
4. Transmit time-out (default 10 minutes) and a UI watchdog (2 s) unkey.
5. TX gain ceiling in settings; the console's power control cannot exceed it.
6. The callsign ident is in vision on every source for the whole
   transmission and cannot be turned off (licence condition 6(21): "clearly
   identifiable at all times").

## User interface — `ohsatv/ui/`

PyQt6, dark instrument look by default (OpenHamShack NB's tokens and spacing), a
light theme, everything that matters painted by hand: the **picture**
(received, or the TX preview), a **spectrum and waterfall** of the receive
band with the plan's occupied bandwidth and the bandplan strip, a **waveform
monitor** (one line, or the whole field overlaid) and a **vectorscope**, a
frequency readout, lock and quality badges (H, V, colour, SNR, jitter), the
mode plan (what fits at this rate, in sentences), source picker with preview,
PTT with an on-air frame. Settings: station (callsign, locator, licence
acknowledgement), device (find, URI, sample rate, range), transmit (gain
ceiling, timeout, hardware TX), modes (deviation, subcarriers, VSB). No fonts,
images or scripts fetched from anywhere.

## Tests

`python3 -m unittest discover -s tests`. tests/test_narrow.py and
tests/test_pcm.py run the narrow formats through tools/narrow_eval.py's
channel; tests/test_sysa.py measures 405 lines, and test_radio.py's
SystemA and CableFeed drive them through the engine; tests/test_nicam.py
runs NICAM through the AM-VSB and FM chains with two AD936x
(helpers.ad936x_link) and against hacktv's frames (tests/data), and
test_radio_extras.py / test_ui_extras.py through the engine and the
window. Tests execute behaviour, never read
source text: the generated waveform's pulse widths and field structure are
measured; AM-VSB and FM pictures are modulated, passed through the
simulator's channel (noise, offset) and decoded, and the SNR, sync lock and
picture error measured against the source; the engine API is driven
headless.
