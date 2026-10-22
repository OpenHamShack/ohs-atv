# OpenHamShack ATV

An analogue amateur-television transceiver for the Pluto family of SDRs
(ADALM-Pluto, Pluto+, Pluto nano, LibreSDR), optionally transmitting through
a HackRF One: 625-line PAL with sound, generated, transmitted, received and
decoded in one native desktop program.

- **AM-VSB** (UK System I-style: 5.5 MHz vision, 1.25 MHz vestige, FM sound
  at +6.0 MHz) and **FM-ATV** (IARU R1 V.2: 3.5 MHz peak deviation, CCIR 405
  pre-emphasis, 6.0 MHz sound subcarrier).
- **23cm** and **6cm (5.8 GHz)** transmit and receive; **70cm** too, but
  outside the bandplan (analogue ATV left the RSGB 70cm plan in December
  2020): allowed inside 430–440 MHz with a prominent warning, and logged.
- Each radio is steered to its best mode: full-width FM-ATV through a HackRF
  transmitter or an Ethernet LibreSDR / Pluto+, reduced deviation on a USB
  Pluto.
- Its own PAL generator and decoder: a test card with the station's ident,
  colour bars, a webcam or a video file out; sync recovery, PAL colour, sound,
  a waveform monitor and a vectorscope in. No hacktv.
- **NICAM-728 stereo** (EN 300 163, System I) both ways, where the sample rate
  has room, beside the analogue sound and falling back to it.
- **Teletext** (WST Level 1) sent with the picture and decoded on receive,
  with subtitles on 888, and the **Teletext editor** that makes the pages
  (formerly OpenHamShack Teletext, now `ohstxt/` in this repository): the
  transmit panel's editor button opens it on the folder being sent.
- A **simulated radio** is the default: stations on the bandplan channels and
  our own transmission looped back, so everything runs, and is tested, with
  no hardware and without transmitting anything.

```
python3 -m ohsatv                        # the window
python3 -m ohsatv --headless --seconds 10  # the engine alone (never transmits)
python3 -m ohsatv --play FILE.sigmf-meta # decode an IQ recording (Record IQ, in the window) instead of a radio
python3 -m ohsatv --teletext [FOLDER]    # the Teletext editor alone (also python3 -m ohstxt)
python3 -m ohsatv --server --port 8080   # the store form: the engine behind its API, no window (docs/SERVER-API.md)
python3 -m ohsatv.bench                  # what each stage costs at each sample rate
python3 -m unittest discover -s tests   # the tests (about 40 s)
tools/build-deb.sh                      # openhamshack-atv_<version>_all.deb in the current directory
```

Needs `python3-numpy`, `python3-pyqt6`, `python3-pyaudio`, `python3-pil`
(the editor's image import and PNG export); for a Pluto
`python3-libiio`; for a HackRF `libhackrf0`; for webcam and file sources
`ffmpeg`.

## Honest limits

A Pluto on **USB 2 sustains about 6.8 MS/s** (ADI's measurement): enough for
a mono AM picture without sound. Colour AM-VSB with sound needs 10 MS/s and
standard FM-ATV 20 MS/s — a Pluto+ or LibreSDR on Ethernet, or a **HackRF
One as the transmitter** (8-bit IQ, 20 MS/s over USB 2) with receive staying
on the Pluto, LibreSDR or simulator. Underruns on the HackRF are shown, not
hidden. A stock Pluto
(AD9363) tunes 325–3800 MHz; **6cm needs the AD9364 setting** or an AD9361
board, and the radio's output there is about −3 dBm: an external PA and LNA
are needed in practice. The window says all of this where it applies.

**Nothing has run on real hardware yet** (no Pluto, no HackRF), and nothing transmits on a real
radio until "Allow transmit on real hardware" is set in Settings.

## Documents

- [DESIGN.md](DESIGN.md) — the contract
- [docs/ENGINE-API.md](docs/ENGINE-API.md) — the engine/window interface
- [docs/SERVER-API.md](docs/SERVER-API.md) — the store form's HTTP/WebSocket API, for OpenHamShack Remote
- [docs/atv-research.md](docs/atv-research.md) — sourced facts: bandplans, licence, PAL, FM-ATV, Pluto limits
- [docs/PERF.md](docs/PERF.md) — measured CPU costs
- [HANDOVER.md](HANDOVER.md) — state, what is verified, next steps
