"""Pluto family (ADALM-Pluto, Pluto+, LibreSDR, Pluto nano) through the
libiio 0.2x Python bindings directly - no pyadi-iio, which Debian does not
package. All of them are an AD936x behind the same IIO driver, so one class
serves; the variant is identified at runtime from what the radio reports,
not from a product name. Discovery and the attribute handling are copied
from OpenHamShack NB's driver (ohsnb/devices/pluto.py), not imported: the two
programs ship separately.

NOT YET TESTED ON HARDWARE. The attribute names follow the ADI driver
(drivers/iio/adc/ad9361.c) and docs/atv-research.md section 5; every step
that could plausibly differ between firmware versions is wrapped so a
failure names the attribute.

Decisions, and where they differ from OpenHamShack NB:

- **Sample rate.** ATV runs at 5-20 MS/s, all above the 2.083 MS/s below
  which the AD9361 needs its x4 FIR. So none of NB's FIR loading, nor its
  software-decimation fallback for the Pluto nano's Tezuka firmware (which
  refuses every FIR load), is here: `sampling_frequency` is set and that is
  all. A rate the radio refuses is a DeviceError with a sentence, never a
  silent substitute - the mode plans (modem.py) are computed from fs, so a
  different rate would be a different, unchecked plan.
- **Somebody else's FIR is bypassed.** Not loading a FIR is not the same as
  not having one: the AD936x keeps whatever FIR the last program loaded, and
  keeps applying it at any rate. On 5 October 2026 Brian's LibreSDR still
  held pluto_dvb's 128-tap x4 root-raised-cosine from a DATV run that
  morning; at 12.5 MS/s it cut everything beyond about 3 MHz from the
  carrier, so FM-ATV went out with its luminance intact and no chroma and no
  sound subcarrier - a locked, clean, mono picture, with nothing to say why
  (docs/atv-research.md 15). So open() sets the rate first (a radio left at
  a DATV rate below 2.083 MS/s cannot drop its FIR until it is above it),
  then turns sysfs `in_out_voltage_filter_fir_en` off if it was on, says so
  in the notes, and close() turns it back on: the coefficients stay loaded,
  so the other program finds its filter as it left it. libiio does not see
  that file as a device attribute: it parses it as input channel "out",
  attribute "voltage_filter_fir_en" (pyadi-iio's ad936x addresses it the
  same way). The first version of this looked for a device attribute, found
  none on the real radio and bypassed nothing; the test fake had been built
  the same wrong way, so the tests passed (docs/atv-research.md 15).
- **RF bandwidth** for RX and TX is set to the sample rate, clamped to what
  the chip allows: 20 MHz for an AD9363, 56 MHz for an AD9364/AD9361 (the
  driver clamps there itself; doing it here keeps status() truthful). The
  analogue filter then passes the whole complex band the plan uses: the
  driver puts its corners at 0.7 fs (receive) and 0.8 fs (transmit), so at
  0.44 fs they cost 0.8 and 0.3 dB. Widening it was considered after the
  5 October colour loss and rejected: the loss was the fixed half-band
  filters after the converters, which rf_bandwidth does not touch, and a
  wider analogue filter only lets more of the neighbouring band into the
  ADC and more of the half-bands' images out of the transmitter
  (docs/atv-research.md 17).
- **The chip's own response** is reported, not corrected here:
  `rx_response` and `tx_response` (devices/ad936x.py) describe what the
  analogue filters and the half-band chain do at this rate - the chain
  read from the driver's rx_path_rates / tx_path_rates - and the modems
  fold its inverse into filters they already run. Correcting the samples
  here would cost a full-rate filter per sample, and an IQ recording would
  no longer hold what the radio delivered. None when a FIR is still
  changing the rate (somebody else's taps: the response is not known).
- **Tuning range** is measured at open(), in the order docs/atv-research.md
  5.2 derives: the LO channels' `frequency_available` ("[min 1 max]",
  intersected over RX and TX, since one band must be tunable by both), else
  the `ad9361-phy,model` context attribute (the driver personality:
  "ad9363a" 325-3800 MHz, "ad9364"/"ad9361" 70-6000 MHz), else None. The
  research's third method, setting a frequency and reading it back, is not
  used: it retunes the radio as a side effect of asking. `hw_model` is never
  used for the range - it names the physical chip, not what the driver will
  tune: OpenHamShack NB saw a Pluto nano report "Nano PlutoSDR (Z7010-AD9361)",
  and a "Z7010-AD9363" board can run as an AD9364. Tuning outside the range
  is refused here with the AD9364 remedy, before the driver's bare EINVAL.
- **Link.** The URI says how the host reaches the radio, and that - not the
  AD936x - limits the rate (docs/atv-research.md 5.1). "usb:" is libusb,
  6.8 MS/s (ADI measured 26.1 MiB/s). An ip: address that is the Pluto's
  own 192.168.2.1, or on the subnet of an enx*/usb* interface (the Pluto's
  RNDIS link), is the USB network, 5.3 MS/s. Any other ip: is Ethernet:
  20 MS/s for a LibreSDR (its README, stock clocks), 28 MS/s for a Pluto+
  (pysdr's reported ceiling, not a sustained figure), and None - unknown,
  not guessed - for anything else. duplex() is True only on Ethernet with
  room for both directions (2 x fs); nobody has published a USB full-duplex
  figure, and at video rates there is no room on the bus anyway.
- **Receive** uses one block per buffer, one field (20 ms: 200k samples at
  10 MS/s), with four kernel buffers. libiio says nothing when the kernel
  drops samples because the host fell behind, so the driver measures what
  actually arrives: samples returned over wall time across a few seconds.
  status()["rxShort"] is True below 97 % of fs - the link (or the host)
  cannot keep up and pictures will tear. A pause in reading (the engine
  stops receiving while keyed) restarts the measurement rather than
  counting the burst of stale buffers that follows as a fast link.
- **8-bit receive (Tezuka).** A Pluto on usb: whose fw_version starts
  "tezuka" is probed at open for Tezuka's packed int8 I/Q (one channel
  enabled, two bytes compared: _probe_rx8), and probed once more on a
  fresh buffer if the first says no (status()["rx8Checks"]). If it answers, the link is
  taken as 15 MS/s for receive and rates above 6.25 MS/s are received
  8-bit; below that, 16-bit as before. Setting plutoRx8 off skips it.
- **8-bit transmit (Tezuka), checked, not assumed.** The mechanism a host
  would use is the receive one mirrored - only voltage0 of
  cf-ad9361-dds-core-lpc enabled, int8 I in byte 0 and int8 Q in byte 1 of
  each word - and it is what F5OEO's SoapyPlutoPAPR sends for CS8. Whether
  the bitstream unpacks it is another matter: Tezuka's 8-bit packer
  (maia-hdl cs12_8mux, F5OEO/maia-sdr "refactor") sits on the ADC side
  only, and every board's DAC path, the nano's included, is ADI's stock
  util_upack2, which takes the word as one 16-bit I sample and feeds Q
  zeros (docs/atv-research.md 13, 18). No attribute names the mode either
  way. So a Tezuka radio on usb: is checked at open (_probe_tx8): a complex
  tone at +fs/8 sent packed, through the AD9361's digital loopback, with
  the TX LO powered down and full attenuation read back first, and taken
  as 8-bit only if it comes back as that tone - not as a real signal, not
  mirrored. Until a bitstream unpacks it, every check says no, the radio
  transmits 16-bit, tx_link_msps stays the 16-bit figure, and the notes say
  why. plutoTx8 off skips the check.
- **Transmit** is quiet until keyed, as in NB: unkeyed the TX LO is
  powered down and the attenuation is at its maximum (-89.75 dB). tx_start()
  powers the LO but keeps full attenuation; the gain last given to
  set_tx_gain() is applied only after the first block is pushed, so the
  carrier never sits at power with nothing modulating it. set_tx_gain()
  while unkeyed only stores the value. The buffer is non-cyclic, one block,
  and write() pushes whole blocks and keeps the remainder. tx_stop()
  attenuates *first*, then pushes one block of zeros and powers the LO
  down - attenuating first means a push that blocks or fails cannot leave
  the transmitter at power. Less than a block left over at tx_stop() (under
  one field) is dropped.
- **TX underruns** are counted, because nobody has documented what the
  DAC does when its buffers run dry. The driver keeps track of when the
  data it has pushed runs out (each block adds its duration); a push that
  arrives after that moment found the DAC already empty, and status()
  ["txLate"] counts those.
- **What is set is read back** (_hold): the rate, both RF bandwidths, the
  transmit attenuation and the receive gain mode (and gain, if manual) at
  open, each LO as it is tuned. On 5 October 2026 the nano twice held a rate
  and an RX LO the program had not set, while status() went on reporting
  what it had (docs/atv-research.md 17a). A value that differs is set once
  more; if it still differs the radio is refused with both figures in the
  sentence. Tolerances are the hardware's own resolution (LO_TOL_HZ,
  RATE_TOL_HZ), not a guess at what is close enough.
- **And watched while running** (watch(), called once a second by the
  engine's guard thread): the rate, the RX LO, and the TX LO while keyed.
  One of the nano's moves came 17 s into a receive run, so reading back at
  open alone would not have caught it. A moved rate or RX LO is set back
  once; a moved TX LO is reported for the engine to unkey on, not set back.
  Each look is two or three attribute reads, iiod round trips of about a
  millisecond each, estimated (not measured: the radios were in use on air
  when this was written).
- **Shared radios.** Other programs drive these radios too - OpenHamShack DATV
  keyed libre-2 on 5 October 2026 minutes after another program had moved
  its TX LO and filter. So open() takes the radio lock (devices/shared.py,
  OpenHamShack DATV's docs/radio-lock.md) once the context has said which radio
  it is and before the first write, and close() gives it back after
  putting the FIR back. The radio is `ad936x-<hw_serial>` (the nano is
  usb: to one program and ip: to another; only the serial makes them one
  radio), else `net-<address>` (the LibreSDR reports no serial). Its own
  shell carries the marker when the radio is reached by address - an ip:
  URI, or the radio's USB network if that is up on this computer - else
  the lock is this computer's only, and the notes say so. Busy is a
  shared.RadioBusy with the spec's sentence, and nothing has been written.
- **Changed since we last set it** (changed(), before every key-up): the
  lock only stops programs that know about it, so the radio's whole state -
  both LOs, the rate, both RF bandwidths, the transmit attenuation and LO
  power, the receive gain mode (and gain, if manual) and the FIR's enable -
  is read and compared with what this program last set or saw. Anything
  different, and anything watch() found moved and set back, refuses the
  key-up until the operator accepts the radio as it is (accept()). Nothing
  is set back for it: something else is driving the radio, and who has it
  is the operator's decision, not this program's.
- **Discovery** (discover(), below) is NB's, unchanged in substance: it
  never trusts `iio.scan_contexts()` alone. Without avahi-daemon the
  combined scan raises `OSError [Errno 26] Text file busy` and takes USB
  down with it (observed in NB), so a failed scan falls back to one backend
  at a time, and direct probes of likely addresses run regardless, each
  bounded to `timeout_s`.
"""
from __future__ import annotations

import collections
import math
import os
import re
import socket
import struct
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .. import host, radiolock
from . import ad936x, shared
from .base import Device, DeviceError
# The HackRF's conversion, not a copy of it: the two 8-bit transmitters must
# not drift apart in scaling, rounding or clipping.
from .hackrf import to_int8

MHZ = 1e6
TX_ATTEN_MAX = -89.75
NO_FIR_MIN = 2_083_334      # lowest AD9361 baseband rate without the x4 FIR in the chain
BLOCK_S = 0.02              # one field: the unit the decoder and the modulator work in
# Receive buffer: as long as possible up to a quarter of a second. Measured
# on the test laptop against a real Pluto nano over libusb (iio_readdev):
# 6.61 MS/s with 64 k-sample buffers, 7.08 with 1 M - per-refill overhead is
# what a USB 2 link at its limit cannot spare.
RX_BUF_MAX = 1 << 20
# When the engine falls behind the radio. read() catches up once more than
# RX_BEHIND_S is waiting, by skipping whole frames (BLOCK_S x 2, 40 ms) down to
# RX_CATCHUP_S: a skipped frame leaves line and field timing exactly where
# they were, so the decoder keeps its lock and loses one picture. Until 0.1.6
# the reader dropped its oldest raw buffer instead, 1 048 576 samples - not a
# whole number of lines - and every drop was a timing jump the AM decoder took
# seconds to recover from (docs/atv-research.md 13a: 13 jumps in 10 s on the
# laptop with the window open). RX_QUEUE_S is only the backstop, if read()
# stops being called at all.
RX_BEHIND_S = 0.5
RX_CATCHUP_S = 0.1
RX_QUEUE_S = 3.0            # raw buffers held for the reader of read(), at most
RX_WINDOW_S = 3.0           # how far back the delivered-rate measurement looks
RX_MIN_SPAN_S = 0.5         # shorter than this is too little to judge a link by
RX_GAP_S = 0.5              # no refill for this long is a pause in reading, not a slow link
# How many receive buffers' worth of samples taken before a retune can still
# arrive after it, beyond what the host already holds: the radio's kernel
# buffers (4, set at open), the one a refill is filling, and one more for
# what a network link's iiod and socket hold. Counted whole because the
# host cannot see how full they are; when the reader keeps up most are
# empty, so this is the worst case - a reader paused while keyed comes back
# to four full ones. On 5 October 2026 libre-2 (Ethernet, 16 MS/s, 1 M-
# sample buffers) labelled frames of the old LO with the new one for long
# enough that a guard max-holding six of them stopped an over
# (docs/atv-research.md 21a). The cost is no spectrum for this long after a
# retune: 0.39 s at 16 MS/s, 1.2 s at 5.
RX_LO_PIPELINE_BUFS = 4 + 2
RX_SHORT = 0.97             # delivered / fs below this: samples are being lost

# Driver personality (the `ad9361-phy,model` context attribute) -> LO range
# and RF bandwidth limit, from drivers/iio/adc/ad9361_regs.h and
# ad9361_validate_rf_bw() (docs/atv-research.md 5.2).
CHIPS = {
    "ad9363a": (325e6, 3800e6, 20e6),
    "ad9364": (70e6, 6000e6, 56e6),
    "ad9361": (70e6, 6000e6, 56e6),
}

# What each link is reported to sustain for 16-bit IQ receive, MS/s
# (docs/atv-research.md 5.1).
USB_MSPS = 6.8              # libusb: ADI measured 26.1 MiB/s; the laptop's nano 6.6-7.1 raw, 7.4 here
# Transmit over USB is slower: the nano took 5.1-5.3 MS/s whatever the
# buffer (4 Oct 2026, zero samples, TX LO powered down) - at 6.25 MS/s the
# DAC starved ~15 % of the time and the first real transmission's picture
# could not lock. 5 MS/s keeps up.
USB_TX_MSPS = 5.2
# Tezuka's "complex 8-bit" receive (docs/atv-research.md 13): with only
# voltage0 of cf-ad9361-lpc enabled, Tezuka's bitstream packs each sample as
# int8 I, int8 Q in one 16-bit word - the top 8 of the 12 ADC bits. Half the
# bytes, so twice the rate: the laptop's nano (tezuka-v0.3.21) delivered
# 10.00 and 12.50 MS/s in full over libusb and topped out at 15.0. Receive
# only - the same trick on the transmit side is the stock unpacker, which
# takes the word as one 16-bit I sample and feeds Q zeros (tested in AD9361
# digital loopback, nothing radiated).
USB_RX8_MSPS = 15.0
# Up to this rate 16-bit receive arrives in full over USB (6.25 MS/s held on
# the nano), so the four extra bits are kept; above it, 8-bit.
RX16_USB_FULL = 6_250_000
# The probe's verdict: Q's spread must be at least this share of I's. Packed
# int8 I and Q are matched (quadrature tracking is on); a stock bitstream's
# single 16-bit channel puts the sign extension of 12-bit I in byte 1, a
# spread of a few counts against byte 0's tens.
RX8_Q_SHARE = 0.3
# Transmitting two bytes a sample over the nano's USB (voltage0 alone, TX LO
# powered down, -89.75 dB), measured 5 October 2026 (docs/atv-research.md
# 18): 10.8 MS/s at most, 10 MS/s in full. That is the transport's figure -
# the nano's bitstream has no 8-bit unpacker, so its DAC was not given the
# picture - and the same 21.5 MB/s that carries 16-bit at 5.2 MS/s
# (USB_TX_MSPS): one figure in bytes, so 8-bit 10 MS/s stands or falls with
# today's 5 MS/s 16-bit transmit. On most of that afternoon the nano's USB
# transmit carried only ~17.4 MB/s (8.6 MS/s here, 4.36 at 16-bit), cause not
# found; status()["txLate"] is what shows it.
USB_TX8_MSPS = 10.8
# The loopback check's tone: +fs/8, 100 of 127 (the unpacker's place for
# int8 is the top 8 of the DAC's 12 bits, as receive's is, so it comes back
# at about 1600 of 2048). It must come back carrying at least TX8_SHARE of
# what arrives, and TX8_IMAGE_DB above its mirror at -fs/8: the stock
# unpacker turns the word into a real signal, whose mirror is as strong as
# the tone itself; a byte-swapped one puts the tone in the mirror.
TX8_TONE = 100
TX8_SHARE = 0.5
TX8_IMAGE_DB = 20.0
# What the check found, as the note finishes the sentence "8-bit transmit ...".
TX8_FOUND = {
    "packed": "came back through the digital loopback as packed I/Q",
    "real": ("came back through the radio's digital loopback as a real signal: its bitstream takes each word as "
             "one 16-bit sample, not int8 I and Q"),
    "mirrored": "came back through the digital loopback mirrored (I and Q bytes the other way round)",
    "silent": "did not come back through the radio's digital loopback as the tone that was sent",
    "no-loopback": "could not be checked: the radio offers no digital loopback",
    "not-quiet": ("was not checked: the transmitter could not be confirmed powered down at full attenuation "
                  "first"),
    "failed": "could not be checked: the radio refused a step of the check",
}
USB_NET_MSPS = 5.3          # RNDIS: ADI measured 20.4 MiB/s; the laptop's nano delivered 5.0 raw, 5.2-5.4 here
# hz12opensource's README says "continuous 20MSPS" over GbE at stock clocks;
# measured 4 Oct 2026 on Brian's LibreSDR (iiod 0.24, gigabit to the laptop):
# iio_readdev 16.1-16.4 MS/s at 20-30.72 MS/s, whatever the buffer size. The
# measurement wins.
LIBRESDR_MSPS = 16.2
PLUTOPLUS_MSPS = 28.0       # pysdr: "roughly 28 MHz" ceiling over GbE - reported, not sustained
PLUTO_USB_NET_IP = "192.168.2.1"

# How far a read-back may be from what was set and still be what was set.
# The LO: the RF synthesiser is fractional-N with a modulus of 8 388 593 and
# a 10-80 MHz reference, divided by at least 2 down to the LO, so its finest
# step is under 5 Hz and the driver reports the frequency it actually made.
# The nano read back 1254.666998 MHz for 1254.667: 2 Hz. Two steps' room.
LO_TOL_HZ = 10.0
# The rate: the BBPLL's fractional step (40 MHz / 2 088 960 = 19 Hz) divided
# down at least 16 times to the baseband rate is ~1.2 Hz; every rate this
# program uses is an exact multiple of the reference anyway. 10 Hz is 1 ppm
# at 10 MS/s - nothing a plan could notice - and the move seen on air was
# 7.67 MS/s.
RATE_TOL_HZ = 10.0
# rf_bandwidth sets an analogue corner the driver calibrates to a few per
# cent; the read-back is the value it stored, so 1 % only allows for rounding.
BW_TOL = 0.01
# Gains: the transmit attenuator moves in 0.25 dB steps; receive gain comes
# from a table and can read back 1 dB from what was asked for.
TX_GAIN_TOL_DB = 0.25
RX_GAIN_TOL_DB = 1.0

# What changed() compares (see the module docstring): key -> (what the
# operator calls it, the device attribute holding the channel, the IIO
# attribute, tolerance, how a value is shown). A tolerance of None compares
# the text; "bw" is BW_TOL of the value. These are read-backs compared with
# read-backs, so the tolerances only absorb a driver's rounding.
STATE = {
    "rxLo": ("receive LO", "rx_lo_ch", "frequency", LO_TOL_HZ, "mhz"),
    "txLo": ("transmit LO", "tx_lo_ch", "frequency", LO_TOL_HZ, "mhz"),
    "txPowerdown": ("transmit LO power-down", "tx_lo_ch", "powerdown", None, ""),
    "sampleRate": ("sample rate", "rx_ch", "sampling_frequency", RATE_TOL_HZ, "msps"),
    "rxBandwidth": ("receive RF bandwidth", "rx_ch", "rf_bandwidth", "bw", "mhz"),
    "txBandwidth": ("transmit RF bandwidth", "tx_ch", "rf_bandwidth", "bw", "mhz"),
    "txGain": ("transmit attenuation", "tx_ch", "hardwaregain", TX_GAIN_TOL_DB, "db"),
    "rxGainMode": ("receive gain mode", "rx_ch", "gain_control_mode", None, ""),
    "rxGain": ("receive gain", "rx_ch", "hardwaregain", RX_GAIN_TOL_DB, "db"),
    "firEnable": ("FIR filter enable", None, "voltage_filter_fir_en", None, ""),
}

def _install() -> str:
    """What to install for libiio, in the words of the system it is read on
    (host.hint): an apt line on Linux, a reinstall on Windows, where the
    installer carries libiio 0.26."""
    return host.hint("libiio")


# -- discovery (from OpenHamShack NB) ----------------------------------------------

# Description text libiio's scan returns for a real Pluto/LibreSDR, e.g.
# "192.168.2.1 (Nano PlutoSDR (Z7010-AD9361)), serial=UQRUR7HPFYWAMKGY" --
# every AD936x variant we support names itself with one of these. "local:"
# (a laptop's own hwmon/battery/NVMe sensors) and anything else unrecognised
# is dropped rather than offered as a radio.
_AD936X_MARKERS = ("pluto", "ad9361", "ad9363", "ad9364", "libresdr")


def _looks_like_ad936x(description: str) -> bool:
    d = description.lower()
    return any(m in d for m in _AD936X_MARKERS)


def _serial_from_description(description: str) -> str | None:
    m = re.search(r"serial=(\S+)", description)
    return m.group(1) if m else None


@dataclass
class _Candidate:
    uri: str
    description: str
    serial: str | None


def _is_ipv4(host: str) -> bool:
    parts = host.split(".")
    return len(parts) == 4 and all(p.isdigit() for p in parts)


def _preference(uri: str) -> int:
    """Lower sorts first, among the addresses of one radio (same serial).

    OpenHamShack NB put the numeric ip: route first (no USB permissions, no
    mDNS). For video rates the order is by throughput instead, measured on
    the test laptop's Pluto nano (Tezuka, 4 Oct 2026): the USB *network*
    route (the Pluto's RNDIS link, ip:192.168.2.1) delivers ~5.0 MS/s raw and
    ~5.2-5.4 to this program; libusb (usb:) 7.1 raw and the full 6.25 MS/s.
    So: a real Ethernet address first (a Pluto+ or LibreSDR), then usb:,
    then the USB network address, then a name needing mDNS. libiio0's udev
    rule gives the desktop user the usb: device (it did on the laptop)."""
    if uri.startswith("ip:"):
        host = uri[len("ip:"):]
        if not _is_ipv4(host):
            return 3
        return 2 if classify_link(uri, usb_net=_usb_network_addresses())[0] == "usb-net" else 0
    if uri.startswith("usb:"):
        return 1
    return 4


def _dedupe(cands: list[_Candidate]) -> list[_Candidate]:
    """The same radio shows up more than once in practice: ip: and usb:
    for one Pluto share a serial (observed), and a direct probe can repeat
    something the scan already found. Collapse by serial, keeping the
    most-preferred uri (see _preference); a candidate with no serial has
    nothing to merge on, so it is kept, deduplicated only by uri."""
    best: dict[str, _Candidate] = {}
    order: list[str] = []
    bare: list[_Candidate] = []
    seen_bare: set[str] = set()
    for c in cands:
        if not c.serial:
            if c.uri not in seen_bare:
                seen_bare.add(c.uri)
                bare.append(c)
            continue
        if c.serial not in best:
            order.append(c.serial)
            best[c.serial] = c
        elif _preference(c.uri) < _preference(best[c.serial].uri):
            best[c.serial] = c
    return [best[s] for s in order] + bare


def _backend_scan(iio_mod, backend: bytes) -> dict[str, str]:
    """Scan one libiio backend only (b"usb" or b"ip"), bypassing whatever
    the combined scan does to merge them - which is how it drags USB down
    with Avahi (see the module docstring). There is no public per-backend
    scan in the Python binding, only the private ctypes calls
    `iio.scan_contexts()` itself uses, so every symbol is feature-detected:
    a binding that lacks one yields no candidates from this path instead of
    an AttributeError."""
    names = ("_create_scan_context", "_get_context_info_list", "_context_info_get_uri",
             "_context_info_get_description", "_context_info_list_free", "_destroy_scan_context",
             "_POINTER", "_byref", "_ContextInfoPtr")
    if not all(hasattr(iio_mod, n) for n in names):
        return {}
    ptr = iio_mod._POINTER(iio_mod._ContextInfoPtr)()
    ctx = iio_mod._create_scan_context(backend, 0)
    try:
        n = iio_mod._get_context_info_list(ctx, iio_mod._byref(ptr))
        out = {}
        for i in range(n):
            uri = iio_mod._context_info_get_uri(ptr[i]).decode("ascii")
            out[uri] = iio_mod._context_info_get_description(ptr[i]).decode("ascii")
        iio_mod._context_info_list_free(ptr)
        return out
    finally:
        iio_mod._destroy_scan_context(ctx)


# Where Linux lists USB devices; a test points it elsewhere.
SYS_USB_DEVICES = "/sys/bus/usb/devices"
# Transmit over the nano's USB with its own network function up on the host
# and down, measured 5 October 2026 (docs/atv-research.md 18a; zero samples,
# TX LO powered down, -89.75 dB): 20.0 against 25.8 MB/s, every time, on
# alternate runs - 5.0 MS/s 16-bit with it up, exactly the 5 MS/s profile's
# need and so starving it, 6.4 with it down. Idle (no packets) it costs the
# same: the host keeps the network's receive transfers queued, and the link
# spends its time polling them. A USB reset changed nothing.
USB_TX_NET_UP_MBPS, USB_TX_NET_DOWN_MBPS = 20.0, 25.8
# What that carries at 16 bits, as the driver's own write loop measured it
# with the network down: 6.36 MS/s asked for 6.25, nothing late in 15 s,
# and 5.07 at 5 (18a). It is what lets a USB nano send 405 lines with sound,
# which need 6.25 MS/s (sysa.SOUND_MIN_FS). Only when the network is known
# to be down: up, the 5.2 of USB_TX_MSPS is already more than it carries.
USB_TX_NET_DOWN_MSPS = 6.36


def usb_net_up(uri: str, root: str | None = None) -> list[str]:
    """The host network interfaces that are up on the USB device `uri`
    (usb:BUS.DEVICE.INTERFACE) - a Pluto's own RNDIS/NCM function. Linux
    only; anything unreadable is "none", since this only ever adds a note."""
    return usb_net_state(uri, root) or []


def usb_net_state(uri: str, root: str | None = None) -> list[str] | None:
    """usb_net_up's answer, or None when it is not known: the address does
    not name a device (bare usb:), or the device is not in sysfs. A
    transmit ceiling is raised only on a known [] - nothing up."""
    root = root or SYS_USB_DEVICES
    try:
        bus, dev = (int(x) for x in uri[len("usb:"):].split(".")[:2])
    except ValueError:
        return None
    up = []
    found = False
    try:
        for name in sorted(os.listdir(root)):
            base = os.path.join(root, name)
            try:
                with open(os.path.join(base, "busnum")) as f:
                    b = int(f.read())
                with open(os.path.join(base, "devnum")) as f:
                    d = int(f.read())
            except (OSError, ValueError):
                continue
            if (b, d) != (bus, dev):
                continue
            found = True
            for intf in sorted(os.listdir(root)):
                if not intf.startswith(f"{name}:"):
                    continue
                net = os.path.join(root, intf, "net")
                for ifname in sorted(os.listdir(net)) if os.path.isdir(net) else ():
                    try:
                        with open(os.path.join(net, ifname, "flags")) as f:
                            if int(f.read(), 16) & 1:          # IFF_UP
                                up.append(ifname)
                    except (OSError, ValueError):
                        pass
    except OSError:
        return None
    return up if found else None


def resolve_usb(uri: str, serial: str | None, iio_mod, root: str | None = None) -> str | None:
    """The usb:BUS.DEVICE.INTERFACE that a bare `usb:` - the default address
    - opened, or None if that cannot be told. libiio opens the first Pluto it
    finds on a bare usb: and says nothing of which, so without this the
    radio's USB network could not be looked up (usb_net_state), and a nano
    with that network down was never trusted with the 6.36 MS/s its transmit
    then carries - 405 lines kept to 5 MS/s without their sound.

    First libiio's own USB scan, which names each device usb:B.D.I with its
    serial in the description: the one with this radio's serial (hw_serial),
    or the only one. Failing that (a binding without the private scan
    calls), sysfs: the USB device whose `serial` is this radio's - a Pluto's
    USB serial is its hw_serial. Any other address is returned as it is."""
    if not uri.startswith("usb:"):
        return None
    if uri[len("usb:"):]:
        return uri
    serial = (serial or "").strip().lower()
    try:
        found = {u: d for u, d in _backend_scan(iio_mod, b"usb").items() if u.startswith("usb:") and u[4:]}
    except Exception:  # noqa: BLE001 - libiio's private calls, feature-detected; no answer is "not known"
        found = {}
    mine = [u for u, d in found.items() if serial and serial in d.lower()]
    if len(mine) == 1:
        return mine[0]
    if not mine and len(found) == 1:
        return next(iter(found))
    if not serial:
        return None
    root = root or SYS_USB_DEVICES
    hits = []
    try:
        for name in sorted(os.listdir(root)):
            try:
                with open(os.path.join(root, name, "serial")) as f:
                    if f.read().strip().lower() != serial:
                        continue
                with open(os.path.join(root, name, "busnum")) as f:
                    b = int(f.read())
                with open(os.path.join(root, name, "devnum")) as f:
                    d = int(f.read())
            except (OSError, ValueError):
                continue
            hits.append(f"usb:{b}.{d}")
    except OSError:
        return None
    return hits[0] if len(hits) == 1 else None


def _usb_network_addresses() -> list[str]:
    """IPv4 addresses ending .10 on a /24, bound to an enx*/usb* interface
    - the host side of the Pluto's own RNDIS-over-USB link (observed in
    NB: enx00e022121743, 192.168.2.10/24, Pluto at .1). Linux-only
    (SIOCGIFADDR via fcntl); any failure just means no candidates come from
    this source, not an error."""
    try:
        import fcntl
        names = [n for _, n in socket.if_nameindex() if n.startswith(("enx", "usb"))]
    except (ImportError, AttributeError, OSError):
        return []
    SIOCGIFADDR, SIOCGIFNETMASK = 0x8915, 0x891B

    def _ioctl_ip(sock, name: str, request: int) -> str | None:
        try:
            packed = struct.pack("256s", name.encode("ascii")[:15])
            res = fcntl.ioctl(sock.fileno(), request, packed)
        except OSError:
            return None
        return socket.inet_ntoa(res[20:24])

    out = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        for name in names:
            addr = _ioctl_ip(s, name, SIOCGIFADDR)
            mask = _ioctl_ip(s, name, SIOCGIFNETMASK)
            if addr and mask == "255.255.255.0" and addr.endswith(".10"):
                out.append(addr)
    return out


def _iface_ipv4(name: str) -> str | None:
    """The IPv4 address of one network interface, or None (Linux only, as
    _usb_network_addresses)."""
    try:
        import fcntl
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            res = fcntl.ioctl(s.fileno(), 0x8915, struct.pack("256s", name.encode("ascii")[:15]))  # SIOCGIFADDR
        return socket.inet_ntoa(res[20:24])
    except (ImportError, OSError, UnicodeError):
        return None


def _direct_candidates() -> list[str]:
    """URIs worth trying even when the scan found nothing (or degraded):
    the Pluto's factory-default address, its mDNS name, and the .1 address
    on every USB-network interface that looks like a Pluto's own link."""
    uris = [f"ip:{PLUTO_USB_NET_IP}", "ip:pluto.local"]
    for addr in _usb_network_addresses():
        uri = f"ip:{addr[:-len('10')]}1"
        if uri not in uris:
            uris.append(uri)
    return uris


def _open_with_timeout(iio_mod, uri: str, timeout_s: float):
    """Open `uri` as an IIO context, giving up after timeout_s even though
    the binding cannot cancel an in-flight connect: a dead address on a live
    subnet hangs for the kernel's whole SYN-retry budget, so the open runs on
    its own thread and this just stops waiting at the deadline."""
    box: list = []

    def run():
        try:
            ctx = iio_mod.Context(uri)
            try:
                ctx.set_timeout(max(1, int(timeout_s * 1000)))
            except Exception:  # noqa: BLE001 - best-effort; absence doesn't disqualify the context
                pass
            box.append(ctx)
        except Exception:  # noqa: BLE001 - any failure to open just means "not there"
            box.append(None)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout_s)
    return box[0] if box else None


def _probe_ad936x(iio_mod, uri: str, timeout_s: float) -> _Candidate | None:
    ctx = _open_with_timeout(iio_mod, uri, timeout_s)
    if ctx is None:
        return None
    try:
        if ctx.find_device("ad9361-phy") is None:
            return None  # answered, but it is not a Pluto family radio
        attrs = getattr(ctx, "attrs", {})
        model = attrs.get("hw_model") or attrs.get("hw_model_variant") or "AD936x"
        serial = attrs.get("hw_serial")
        host = uri.split(":", 1)[1] if ":" in uri else uri
        desc = f"{host} ({model})" + (f", serial={serial}" if serial else "")
        return _Candidate(uri, desc, serial)
    except Exception:  # noqa: BLE001 - a context that answers but misbehaves is not a candidate
        return None


def _avahi_running() -> bool:
    return Path("/var/run/avahi-daemon/socket").exists()


def _scan_failed_note(e: Exception) -> str:
    """Why the combined scan failed, for Find's notes. On Linux the usual
    cause is a missing avahi-daemon, and the note says to install it. On
    Windows there is no Avahi to install - libiio 0.26 asks Windows' own
    DNS-SD there - so the note says only that the scan by name did not
    answer; the USB and direct-address searches that follow still run."""
    if not host.windows() and not _avahi_running():
        return f"Full device scan failed; {host.hint('avahi')}. Fell back to a USB/IP-only search."
    if host.windows():
        return f"Full device scan failed ({e}); {host.hint('avahi')}. Fell back to a USB/IP-only search."
    return f"Full device scan failed ({e}); fell back to a USB/IP-only search."


def discover(timeout_s: float = 1.0, notes: list[str] | None = None) -> list[tuple[str, str]]:
    """Find Pluto-family radios. Returns (uri, description) pairs, one per
    radio found; never raises - with nothing plugged in, every step timing
    out or finding nothing is the normal case. `notes`, if given, collects
    sentences about anything that degraded along the way, so Settings' Find
    button can explain a "nothing found"."""
    try:
        iio = import_iio()
    except DeviceError as e:
        if notes is not None:
            notes.append(str(e))
        return []

    cands: list[_Candidate] = []

    def _absorb(scan: dict[str, str]):
        for uri, desc in scan.items():
            if _looks_like_ad936x(desc):
                cands.append(_Candidate(uri, desc, _serial_from_description(desc)))

    try:
        _absorb(iio.scan_contexts())
    except Exception as e:  # noqa: BLE001 - plain OSError today, but nothing here may stop discover()
        if notes is not None:
            notes.append(_scan_failed_note(e))
        # The "ip" backend's scan is mDNS too, so it can still fail where
        # "usb" succeeds: each is tried and caught on its own.
        for backend in (b"usb", b"ip"):
            try:
                _absorb(_backend_scan(iio, backend))
            except Exception:  # noqa: BLE001 - this fallback is itself best-effort
                pass

    for uri in _direct_candidates():
        try:
            cand = _probe_ad936x(iio, uri, timeout_s)
        except Exception:  # noqa: BLE001 - a bad probe must not stop the others
            cand = None
        if cand is not None:
            cands.append(cand)

    return [(c.uri, c.description) for c in _dedupe(_add_ipv4(cands, timeout_s))]


def _resolve_ipv4(host: str, timeout_s: float) -> str | None:
    """host's IPv4 address, or None; bounded by timeout_s (mDNS can hang)."""
    box: list = []

    def run():
        try:
            box.append(socket.getaddrinfo(host, None, socket.AF_INET)[0][4][0])
        except (OSError, IndexError):
            box.append(None)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout_s)
    return box[0] if box else None


def _add_ipv4(cands: list[_Candidate], timeout_s: float) -> list[_Candidate]:
    """Offer a numeric IPv4 address for every radio found by name.

    Seen with Brian's LibreSDR: libiio's scan reported it twice under one
    URI, ip:libre-2.local - once by IPv4, once by IPv6 - and the binding's
    scan returns a dict keyed by URI, so the IPv4 entry was gone before we
    saw it, and Find offered only the mDNS name (with the IPv6 address as
    its description). The name works only while Avahi does; the address
    always does. So resolve each name to IPv4 and offer that too; radios
    without a serial number (this LibreSDR has none) are tied to their name
    by the address, so the two collapse to one entry and _preference picks
    the address."""
    out = list(cands)
    for c in cands:
        if not c.uri.startswith("ip:"):
            continue
        host = c.uri[len("ip:"):]
        if _is_ipv4(host):
            if not c.serial:
                c.serial = f"addr:{host}"
            continue
        v4 = _resolve_ipv4(host, timeout_s)
        if v4:
            key = c.serial or f"addr:{v4}"
            c.serial = key
            desc = c.description
            if not desc.startswith(v4):
                desc = f"{v4} ({host}) " + desc.split(" ", 1)[-1] if " " in desc else f"{v4} ({host})"
            out.append(_Candidate(f"ip:{v4}", desc, key))
    for c in out:
        if c.uri.startswith("ip:") and _is_ipv4(c.uri[3:]) and not c.serial:
            c.serial = f"addr:{c.uri[3:]}"
    return out


def import_iio():
    """The libiio Python bindings, or DeviceError with the sentence to act
    on. ImportError is not the only way this fails: bindings whose C library
    is missing raise OSError or TypeError from ctypes, and a libiio 1.0
    library lacks the 0.2x symbols (AttributeError). All of them mean
    "install the right libiio".

    On Windows the import runs inside host.bundled_libiio(), so the
    installer's libiio 0.26 is the one loaded, never a 1.0 that ADI's own
    installer may have left in System32 (NB's finding); elsewhere that
    changes nothing."""
    try:
        with host.bundled_libiio():
            import iio
    except ImportError as e:
        raise DeviceError(_install()) from e
    except AttributeError as e:
        raise DeviceError(f"The installed libiio is not the 0.2x series these bindings speak ({e}); "
                          f"libiio 1.0 changed its interface. {_install()}") from e
    except (OSError, TypeError) as e:
        raise DeviceError(f"libiio could not be loaded ({e}). {_install()}") from e
    return iio


# -- what the radio can do -----------------------------------------------------

def parse_range(text: str) -> tuple[float, float] | None:
    """"[70000000 1 6000000000]" -> (70e6, 6e9); anything else -> None."""
    m = re.fullmatch(r"\s*\[\s*(\d+)\s+\d+\s+(\d+)\s*\]\s*", text or "")
    if not m:
        return None
    lo, hi = float(m.group(1)), float(m.group(2))
    return (lo, hi) if lo < hi else None


def classify_link(uri: str, hw_model: str = "", usb_net: list[str] | None = None,
                  ip_addr: str | None = None) -> tuple[str, float | None]:
    """(link, MS/s the link is reported to sustain) for `uri`. `usb_net` is
    the host's own addresses on enx*/usb* interfaces; `ip_addr` the
    address a named host resolved to, if the context reported it."""
    if uri.startswith("usb:"):
        return "usb", USB_MSPS
    if not uri.startswith("ip:"):
        return "unknown", None
    host = uri[len("ip:"):]
    if not _is_ipv4(host) and ip_addr and _is_ipv4(ip_addr):
        host = ip_addr
    if host == PLUTO_USB_NET_IP or (_is_ipv4(host) and any(
            a.rsplit(".", 1)[0] == host.rsplit(".", 1)[0] for a in (usb_net or ()))):
        return "usb-net", USB_NET_MSPS
    m = (hw_model or "").lower()
    if "libresdr" in m:
        return "ethernet", LIBRESDR_MSPS
    if "pluto+" in m or "plutoplus" in m or "pluto plus" in m:
        return "ethernet", PLUTOPLUS_MSPS
    return "ethernet", None


def range_refusal(hz: float, rng: tuple[float, float]) -> str:
    """The sentence for tuning outside the radio's range, worded as
    bands.reachable() words it for a band."""
    lo, hi = rng
    span = f"{lo / MHZ:.0f}-{hi / MHZ:.0f} MHz"
    want = f"{hz / MHZ:.3f}".rstrip("0").rstrip(".")
    looks_ad9363 = lo >= 300e6 and hi <= 3.9e9
    if looks_ad9363 and 70e6 <= hz <= 6000e6:
        return (f"This radio tunes {span} (an AD9363, as a stock Pluto is). {want} MHz needs the AD9364 "
                f"setting - on the Pluto: fw_setenv attr_name compatible; fw_setenv attr_val ad9364 (and "
                f"fw_setenv compatible ad9364 on firmware 0.32+), then reboot - or an AD9361 board such as "
                f"the LibreSDR. Performance out there is not guaranteed by ADI.")
    return f"This radio tunes {span}; {want} MHz is outside it."


def _msps(hz: float) -> str:
    return f"{hz / MHZ:.6f}".rstrip("0").rstrip(".") + " MS/s"


def _mhz(hz: float) -> str:
    return f"{hz / MHZ:.6f}".rstrip("0").rstrip(".") + " MHz"


def _db(v: float) -> str:
    return f"{v:g} dB"


class PlutoDevice(Device):
    kind = "pluto"
    can_tx = True

    def __init__(self, uri: str = "ip:192.168.2.1", fs: float = 10e6, cfg: dict | None = None):
        self.uri = uri
        self.fs = float(fs)
        self.cfg = cfg or {}
        self.ctx = None
        self.iio = None
        self.rxbuf = None
        self.txbuf = None
        self.block = int(round(self.fs * BLOCK_S))
        self.left = np.zeros(0, np.complex64)
        self.tx_pending = np.zeros(0, np.complex64)
        self.model = ""
        self.phy_model = ""
        self.range: tuple[float, float] | None = None
        self.bw_limit = 20e6
        self.rf_bw: float | None = None
        self.link, self.link_msps = "unknown", None
        self.tx_on = False
        self.tx_gain = TX_ATTEN_MAX       # stored; applied only while keyed
        self._gain_applied = False
        self.rx_lo = None
        self.tx_lo = None
        self.usb_addr: str | None = None
        # Which LO each sample read() returns was taken at (_lo_marks): the
        # stream position read() has reached (samples returned or skipped),
        # and (position, LO or None) marks, None meaning not known.
        self._rx_pos = 0
        self._lo_marks: list[tuple[int, float | None]] = []
        self._read_lo: float | None = None
        self._lo_lock = threading.Lock()
        self.notes: list[str] = []
        # A clock the tests can drive; the rate measurements are its only user.
        self.clock = time.monotonic
        self._rx_hist: collections.deque = collections.deque()
        self._tx_until: float | None = None
        self.tx_late = 0
        self.rx_bits = 16
        self._bps = 4
        self.rx8_capable = False
        self.rx8_checks = 0           # 8-bit checks made at open: 0 none, 1, or 2 (the first failed)
        self.tx_link_msps = None
        self.tx_bits = 16
        self._tx_bps = 4
        self.tx8_capable = False
        self.tx8_check = None         # what the 8-bit transmit check found; None if not made
        self.fw = ""
        self.fir_bypassed = False
        self.rx_response = None
        self.tx_response = None
        self.path_rates: dict[str, str | None] = {}
        # Held while an LO is set and its value recorded, and while watch()
        # compares: a watch landing between the two would take a retune for
        # a move and put the old frequency back.
        self._cfg_lock = threading.RLock()
        self.moved: list[dict] = []     # what watch() last found moved
        self.moves = 0                  # and how many moves it has found since open
        # changed(): the radio as this program last set or saw it, and the
        # changes found since then that were not this program's.
        self.seen: dict[str, object] = {}
        self.foreign: list[str] = []

    # -- helpers -------------------------------------------------------------
    def _attr(self, chan, name: str, value):
        try:
            chan.attrs[name].value = str(value)
        except (KeyError, OSError) as e:
            raise DeviceError(f"Pluto: could not set {name}={value} ({e})") from e

    def _try(self, chan, name: str, value) -> bool:
        try:
            chan.attrs[name].value = str(value)
            return True
        except (AttributeError, KeyError, OSError):
            return False

    def _read(self, chan, name: str) -> str | None:
        try:
            return chan.attrs[name].value
        except (AttributeError, KeyError, OSError):
            return None

    def _number(self, chan, name: str) -> float | None:
        """An attribute read as a number. The driver appends units to some
        ("-89.750000 dB"), so the leading number is taken; None if the
        attribute cannot be read or holds no number."""
        m = re.match(r"\s*(-?\d+(?:\.\d*)?(?:[eE][-+]?\d+)?)", self._read(chan, name) or "")
        return float(m.group(1)) if m else None

    def _hold(self, what: str, chan, name: str, want: float, tol: float, show) -> None:
        """Read back an attribute just set and insist on it.

        Until 5 October 2026 this driver reported what it had set, not what
        the radio held. Twice that afternoon the nano was found at 2.330997
        MS/s and an RX LO of 1254.666998 MHz while OpenHamShack ATV had set 10 MS/s
        and 1247 MHz, and the program went on showing its own figures
        (docs/atv-research.md 17a). So each value is read back: a mismatch is
        set once more - something may have written it in between - and a
        radio that still will not hold it is refused, with the sentence in
        the notes too. An attribute that cannot be read at all is not a
        mismatch: there is nothing to compare, and old firmware lacks some.
        `show` formats a value for the sentence."""
        got = self._number(chan, name)
        if got is None or abs(got - want) <= tol:
            return
        first = got
        value = f"{want:g}" if name == "hardwaregain" else int(round(want))
        self._try(chan, name, value)
        got = self._number(chan, name)
        if got is not None and abs(got - want) <= tol:
            self.notes.append(f"The radio's {what} read {show(first)} after it was set to {show(want)}; "
                              f"set again, it held")
            return
        msg = (f"The radio will not hold its {what}: set to {show(want)}, it reads "
               f"{show(got if got is not None else first)}. Something else is setting this radio (Tezuka's "
               f"own control script can); close it and start again")
        self.notes.append(msg)
        raise DeviceError(msg)

    # -- open / close --------------------------------------------------------
    def open(self) -> None:
        if self.fs < NO_FIR_MIN:
            raise DeviceError(f"{self.fs / MHZ:g} MS/s is below the 2.083 MS/s an AD936x runs at without "
                              f"its FIR; OpenHamShack ATV's rates are 5-20 MS/s")
        iio = import_iio()
        try:
            self.ctx = iio.Context(self.uri)
        except OSError as e:
            # On Windows libiio reports failure through the Win32 last-error,
            # which its bindings do not read, so the OSError arrives as
            # "[Errno 0] No error" - worse than saying nothing (NB's lesson).
            why = f" ({e})" if e.errno else ""
            raise DeviceError(f"No Pluto at {self.uri}{why}. A LibreSDR or Pluto+ on Ethernet has its own "
                              f"address (ip:<address>); {host.hint('iio_info')}.{host.hint('pluto-usb')}") from e
        self.iio = iio
        attrs = getattr(self.ctx, "attrs", {}) or {}
        self.model = attrs.get("hw_model", "") or ""
        if not self.model:
            # Brian's LibreSDR reports no hw_model and no ad9361-phy,model at
            # all - only the kernel, uri and ip. Its iiod description string
            # carries the hostname ("198.51.100.74 Linux libre 6.1.70 ..."),
            # which is the one thing that says LibreSDR; without it the link
            # speed, and so the steering, would be unknown.
            desc = (getattr(self.ctx, "description", "") or "").lower()
            self.model = "LibreSDR (from its hostname)" if " libre " in f" {desc} " else "AD936x (no hw_model)"
        self.phy_model = (attrs.get("ad9361-phy,model", "") or "").strip().lower()
        # Which USB device this is, for its network's state: the default
        # address, bare usb:, does not say (resolve_usb).
        self.usb_addr = resolve_usb(self.uri, attrs.get("hw_serial"), iio)
        self.phy = self.ctx.find_device("ad9361-phy")
        self.rxdev = self.ctx.find_device("cf-ad9361-lpc")
        self.txdev = self.ctx.find_device("cf-ad9361-dds-core-lpc")
        if not (self.phy and self.rxdev and self.txdev):
            raise DeviceError(f"{self.uri} is not an AD936x (no ad9361-phy / cf-ad9361 devices)")
        self.rx_lo_ch = self.phy.find_channel("altvoltage0", True)
        self.tx_lo_ch = self.phy.find_channel("altvoltage1", True)
        self.rx_ch = self.phy.find_channel("voltage0", False)
        self.tx_ch = self.phy.find_channel("voltage0", True)
        self.seen, self.foreign = {}, []
        # Everything above only read the radio; everything in _setup writes
        # to it. The lock goes between the two.
        if self.lock_radio:
            self._take_lock(attrs)
        try:
            self._setup(iio, attrs)
        except BaseException:
            # A radio this program could not set up is not one it is using:
            # give it back now, or the heartbeat would keep it from everyone
            # until this process ended.
            shared.release(self)
            raise
        self.seen = self._snapshot()

    def _take_lock(self, attrs) -> None:
        serial = (attrs.get("hw_serial") or "").strip() or None
        addr = self._address(attrs)
        rid = radiolock.radio_id("ad936x", serial, addr) if serial or addr else \
            radiolock.safe_id(f"ad936x-{self.uri}")
        label, shell = self._label(addr), self._shell_host(addr)
        try:
            shared.take(self, rid, label, shell)
        except shared.RadioBusy:
            self._drop()            # nothing was written: just let go of the context
            raise
        if shell is None and self.cfg.get("radioLockSsh", True):
            # The radio has a shell; there is just no route to it from here.
            # The spec's rule for a shell that does not answer applies: say so.
            note = (f"The lock on {label} covers this computer only: it is reached over USB with its own "
                    f"network down here, so its shell cannot be asked. Programs on other computers will not see it")
            self.radio_lock.notes.append(note)
            self.notes.append(note)

    def _address(self, attrs) -> str | None:
        """The radio's IPv4 address as this computer reaches it, if it is
        reached by address: the ip: URI's, or the address a name resolved to
        (libiio reports it as `ip,ip-addr`)."""
        if not self.uri.startswith("ip:"):
            return None
        host = self.uri[3:].split(":")[0] if self.uri.count(":") <= 1 else self.uri[3:]
        ip = (attrs.get("ip,ip-addr") or "").strip()
        return ip if not _is_ipv4(host) and _is_ipv4(ip) else host

    def _shell_host(self, addr: str | None) -> str | None:
        """Where the radio's shell answers: its address, or on usb: the
        radio's end of its own USB network if that is up here (the nano's
        RNDIS link: this computer .10, the radio .1). On usb: with the
        network down there is no route to the shell at all."""
        if addr:
            return addr
        for ifname in usb_net_up(self.usb_addr or self.uri):
            mine = _iface_ipv4(ifname)
            if mine:
                return mine.rsplit(".", 1)[0] + ".1"
        return None

    def _label(self, addr: str | None) -> str:
        """The radio as the operator knows it, for the lock's sentences."""
        m = self.model.lower()
        name = ("the LibreSDR" if "libre" in m else "the Pluto nano" if "nano" in m else
                "the Pluto+" if "pluto+" in m or "plutoplus" in m else "the Pluto")
        return f"{name} at {addr}" if addr else f"{name} on {self.uri}"

    def _setup(self, iio, attrs) -> None:
        """open() from the first write on."""
        # Transmit quiet first, before anything else can fail half way.
        self._attr(self.tx_ch, "hardwaregain", TX_ATTEN_MAX)
        self._try(self.tx_lo_ch, "powerdown", 1)

        self.range = self._measure_range()
        self.bw_limit = self._bw_limit()
        self.link, self.link_msps = classify_link(self.uri, self.model, _usb_network_addresses(),
                                                  attrs.get("ip,ip-addr"))
        self.tx_link_msps = {"usb": USB_TX_MSPS, "usb-net": USB_NET_MSPS}.get(self.link)
        # The nano's own USB network shares the link the samples use, and up
        # it takes a fifth of what transmit can carry - enough to starve the
        # 5 MS/s profile. Taking it down needs root, so this only says so.
        net = usb_net_state(self.usb_addr or self.uri) if self.link == "usb" else None
        self.usb_net_up = net or []
        if net == []:
            self.tx_link_msps = USB_TX_NET_DOWN_MSPS
        if self.usb_net_up:
            names = ", ".join(self.usb_net_up)
            self.notes.append(f"The radio's USB network ({names}) is up on this computer and shares the USB "
                              f"link with the samples: transmit carries about {USB_TX_NET_UP_MBPS:g} MB/s "
                              f"with it up, {USB_TX_NET_DOWN_MBPS:g} with it down; 5 MS/s needs 20 and starves with it up. "
                              f"'sudo ip link set {self.usb_net_up[0]} down' frees it; 'up' brings it back")
        self.fw = (attrs.get("fw_version", "") or "").strip()
        if self.link_msps == PLUTOPLUS_MSPS:
            self.notes.append(f"{PLUTOPLUS_MSPS:g} MS/s is the reported ceiling for a Pluto+ on Ethernet, "
                              f"not a measured sustained rate")

        try:
            self.rx_ch.attrs["sampling_frequency"].value = str(int(self.fs))
        except (KeyError, OSError) as e:
            raise DeviceError(f"The radio refused {self.fs / MHZ:g} MS/s ({e}). An AD936x takes "
                              f"2.083-61.44 MS/s; choose another rate in Settings.") from e
        self.fir_bypassed = self._bypass_fir()
        self.rf_bw = max(200e3, min(self.fs, self.bw_limit))
        self._attr(self.rx_ch, "rf_bandwidth", int(self.rf_bw))
        tx_bw = self._try(self.tx_ch, "rf_bandwidth", int(self.rf_bw))
        self._try(self.rx_ch, "rf_port_select", "A_BALANCED")
        self._try(self.tx_ch, "rf_port_select", "A")
        for name in ("quadrature_tracking_en", "rf_dc_offset_tracking_en", "bb_dc_offset_tracking_en"):
            self._try(self.rx_ch, name, 1)
        mode = self.cfg.get("rxGainMode", "slow_attack")
        self._attr(self.rx_ch, "gain_control_mode", mode)
        if mode == "manual":
            self._attr(self.rx_ch, "hardwaregain", float(self.cfg.get("rxGain", 40.0)))
        self._read_back(mode, tx_bw)
        # After the read-back: the responses are worked out from the rate and
        # bandwidth the radio holds, not from a value it may have refused.
        self.rx_response = self._response("rx")
        self.tx_response = self._response("tx")

        self.rx8_capable = False
        self.rx8_checks = 0
        if self.link == "usb" and self.fw.lower().startswith("tezuka") and self.cfg.get("plutoRx8", True):
            # Checked twice before giving up. At two opens on 5 October 2026
            # the nano's first check read plain 16-bit words, and the
            # fallback - right as far as it went - left 5 MS/s arriving of
            # 12.5; opening again found the packing every time
            # (docs/atv-research.md 17a). The cause is not known, so the
            # second check is the first again on a fresh buffer: a reopen in
            # miniature, without dropping the context.
            self.rx8_capable = self._probe_rx8()
            self.rx8_checks = 1
            if not self.rx8_capable:
                self.rx8_capable = self._probe_rx8()
                self.rx8_checks = 2
            if self.rx8_capable:
                self.link_msps = USB_RX8_MSPS
                if self.rx8_checks == 2:
                    self.notes.append(f"Tezuka's 8-bit receive read plain 16-bit samples at the first check and "
                                      f"packed I/Q at the second: receiving 8-bit, up to {USB_RX8_MSPS:g} MS/s")
            else:
                self.notes.append(f"Tezuka firmware ({self.fw}), but its 8-bit receive did not answer as "
                                  f"packed I/Q, checked twice: receiving 16-bit, up to {USB_MSPS:g} MS/s. "
                                  f"Restarting the radio has found it before")
        self.rx_bits = 8 if self.rx8_capable and self.fs > RX16_USB_FULL else 16
        self._bps = 2 if self.rx_bits == 8 else 4

        self.tx8_capable = False
        self.tx8_check = None
        if self.link == "usb" and self.fw.lower().startswith("tezuka") and self.cfg.get("plutoTx8", True):
            self.tx8_check = self._probe_tx8()
            self.tx8_capable = self.tx8_check == "packed"
            if self.tx8_capable:
                self.tx_link_msps = USB_TX8_MSPS
            else:
                self.notes.append(f"Tezuka firmware ({self.fw}), but 8-bit transmit {TX8_FOUND[self.tx8_check]}: "
                                  f"transmitting 16-bit, up to {USB_TX_MSPS:g} MS/s over USB")
        # Like receive: the four extra bits are kept wherever 16-bit keeps up.
        self.tx_bits = 8 if self.tx8_capable and self.fs > USB_TX_MSPS * MHZ else 16
        self._tx_bps = 2 if self.tx_bits == 8 else 4
        if self.link_msps is not None and self.fs > self.link_msps * MHZ:
            self.notes.append(f"{self.fs / MHZ:g} MS/s is more than this {self.link} link is reported to "
                              f"carry ({self.link_msps:g} MS/s): expect lost samples and torn pictures")

        self.rxdev.find_channel("voltage0", False).enabled = True
        self.rxdev.find_channel("voltage1", False).enabled = self.rx_bits == 16
        try:
            self.rxdev.set_kernel_buffers_count(4)
        except (AttributeError, OSError):
            pass
        self.rx_buf_n = int(min(RX_BUF_MAX, max(self.block, self.fs * 0.25)))
        try:
            self.rxbuf = iio.Buffer(self.rxdev, self.rx_buf_n, False)
        except OSError as e:
            raise DeviceError(f"Pluto: could not create the receive buffer ({e})") from e

    def _read_back(self, mode: str, tx_bw: bool) -> None:
        """open()'s settings, read back (see _hold). The transmit
        attenuation is among them: a transmitter that did not take -89.75 dB
        is not quiet. The receive gain is checked only in manual mode - under
        the AGC it is the AGC's to move."""
        self._hold("sample rate", self.rx_ch, "sampling_frequency", self.fs, RATE_TOL_HZ, _msps)
        self._hold("receive RF bandwidth", self.rx_ch, "rf_bandwidth", self.rf_bw, BW_TOL * self.rf_bw, _mhz)
        if tx_bw:
            self._hold("transmit RF bandwidth", self.tx_ch, "rf_bandwidth", self.rf_bw, BW_TOL * self.rf_bw, _mhz)
        self._hold("transmit attenuation", self.tx_ch, "hardwaregain", TX_ATTEN_MAX, TX_GAIN_TOL_DB, _db)
        got = (self._read(self.rx_ch, "gain_control_mode") or "").strip()
        if got and got != mode:
            self._try(self.rx_ch, "gain_control_mode", mode)
            again = (self._read(self.rx_ch, "gain_control_mode") or "").strip()
            if again != mode:
                msg = (f"The radio will not hold its receive gain mode: set to {mode}, it reads {again or got}. "
                       f"Something else is setting this radio (Tezuka's own control script can); close it and "
                       f"start again")
                self.notes.append(msg)
                raise DeviceError(msg)
            self.notes.append(f"The radio's receive gain mode read {got} after it was set to {mode}; set again, "
                              f"it held")
        if mode == "manual":
            self._hold("receive gain", self.rx_ch, "hardwaregain", float(self.cfg.get("rxGain", 40.0)),
                       RX_GAIN_TOL_DB, _db)

    def _response(self, path: str):
        """The fixed response of one direction at this rate (ad936x.py):
        the half-band chain from the driver's path rates if it reports them,
        the nominal x8 chain if it does not, None if they show a FIR still
        changing the rate."""
        text = None
        try:
            a = self.phy.attrs.get(f"{path}_path_rates")
            text = None if a is None else a.value
        except (AttributeError, OSError):
            text = None
        self.path_rates[path] = text
        stages = ad936x.parse_path_rates(text)
        if text and stages is None:
            return None
        return ad936x.Response(path, self.fs, self.rf_bw, stages)

    def _fir_attr(self):
        """sysfs in_out_voltage_filter_fir_en, as libiio names it; None if
        the radio has none."""
        try:
            ch = self.phy.find_channel("out", False)
            return None if ch is None else ch.attrs.get("voltage_filter_fir_en")
        except (AttributeError, OSError):
            return None

    def _bypass_fir(self) -> bool:
        """Turn off a FIR another program left enabled (see the module
        docstring). True if one was on and is now off."""
        fir = self._fir_attr()
        if fir is None:
            return False
        try:
            if fir.value.strip() != "1":
                return False
        except (AttributeError, OSError):
            return False
        try:
            fir.value = "0"
        except OSError as e:
            self.notes.append(f"The radio has a FIR filter enabled (another program's) and it could not be "
                              f"turned off ({e}): the signal may be narrower than the plan says")
            return False
        got = self._read(self.rx_ch, "sampling_frequency")
        if got is not None and got.strip() and abs(float(got) - self.fs) > 1:
            raise DeviceError(f"Turning off the radio's FIR moved its rate to {float(got) / MHZ:g} MS/s, "
                              f"not {self.fs / MHZ:g}")
        self.notes.append("The radio had a FIR filter enabled (another program's, such as a DATV "
                          "transmitter's); it is bypassed while OpenHamShack ATV runs and turned back on at close")
        return True

    def _probe_rx8(self) -> bool:
        """True if this radio packs int8 I/Q into one channel (Tezuka's
        8-bit receive). The firmware name alone is not trusted: the packing
        lives in the bitstream, and a Tezuka build without it would look the
        same from here while delivering I alone. So one short buffer is read
        with voltage1 disabled and the two bytes of each word compared (see
        RX8_Q_SHARE). Anything that goes wrong - a refusal, a short read,
        silence - is a no, and receive stays 16-bit."""
        ch0 = self.rxdev.find_channel("voltage0", False)
        ch1 = self.rxdev.find_channel("voltage1", False)
        n = 1 << 15
        buf = None
        try:
            ch0.enabled, ch1.enabled = True, False
            buf = self.iio.Buffer(self.rxdev, n, False)
            buf.refill()               # the first may hold samples from before the AGC settled
            buf.refill()
            raw = bytes(buf.read())
        except (OSError, AttributeError):
            return False
        finally:
            buf = None
            ch1.enabled = True
        if len(raw) != 2 * n:
            return False
        b = np.frombuffer(raw, np.int8).astype(np.float32)
        si, sq = float(b[0::2].std()), float(b[1::2].std())
        return si > 0 and sq >= RX8_Q_SHARE * si

    def _probe_tx8(self) -> str:
        """Does this radio's bitstream unpack int8 I/Q sent on voltage0
        alone? One of TX8_FOUND's keys; only "packed" is a yes.

        The only way to know is to send something and look at what the DAC
        was given, and nothing may be radiated to do it. The AD9361's
        digital loopback (ad9361-phy debug attribute `loopback` 1) hands the
        transmit samples to the receive port inside the chip, before the
        DAC; and the TX LO is powered down and the attenuation at -89.75 dB,
        both read back before anything is sent - if either cannot be
        confirmed, there is no check. The tone goes out in a cyclic buffer:
        the DMA repeats it with no host pacing, so a slow link cannot make
        gaps that look like a failure. It comes back on both receive
        channels, 16-bit, so I and Q are unambiguous. Loopback is put back
        to what it was, and read back: a radio left in loopback hears
        nothing from its antenna, which is worse than any answer here."""
        if (self._number(self.tx_lo_ch, "powerdown") != 1
                or abs((self._number(self.tx_ch, "hardwaregain") or 0.0) - TX_ATTEN_MAX) > TX_GAIN_TOL_DB):
            return "not-quiet"
        try:
            loop = self.phy.debug_attrs["loopback"]
            before = loop.value.strip()
        except (AttributeError, KeyError, OSError):
            return "no-loopback"
        k = np.arange(4096)
        tone = (TX8_TONE / 127) * np.exp(2j * np.pi * k / 8)
        rx0, rx1 = self.rxdev.find_channel("voltage0", False), self.rxdev.find_channel("voltage1", False)
        tx0, tx1 = self.txdev.find_channel("voltage0", True), self.txdev.find_channel("voltage1", True)
        txbuf = rxbuf = None
        try:
            loop.value = "1"
            tx0.enabled, tx1.enabled = True, False
            txbuf = self.iio.Buffer(self.txdev, len(k), True)
            txbuf.write(bytearray(to_int8(tone).tobytes()))
            txbuf.push()
            rx0.enabled, rx1.enabled = True, True
            rxbuf = self.iio.Buffer(self.rxdev, 1 << 14, False)
            rxbuf.refill()                 # the first may hold samples from before the loopback
            rxbuf.refill()
            raw = bytes(rxbuf.read())
        except (OSError, AttributeError):
            return "failed"
        finally:
            rxbuf = txbuf = None
            tx1.enabled = True
            try:
                loop.value = before
                back = loop.value.strip()
            except OSError:
                back = None
            if back != before:
                msg = (f"The radio's digital loopback was set for the 8-bit transmit check and will not go back "
                       f"to {before} (it reads {back}): it would hear nothing from its antenna. Restart the radio")
                self.notes.append(msg)
                raise DeviceError(msg)
        s = np.frombuffer(raw, np.int16).astype(np.float64)
        x = s[0::2] + 1j * s[1::2]
        x = x[: len(x) // 8 * 8]
        if len(x) < 1024:
            return "failed"
        m = np.arange(len(x))
        pos = abs(np.vdot(np.exp(2j * np.pi * m / 8), x)) ** 2 / len(x)
        neg = abs(np.vdot(np.exp(-2j * np.pi * m / 8), x)) ** 2 / len(x)
        total = float(np.sum(np.abs(x) ** 2))
        if total <= 0 or pos + neg < TX8_SHARE * total:
            return "silent"
        # A real signal - the stock unpacker's - splits equally between the
        # tone and its mirror: 0 dB, "real".
        ratio = 10 * np.log10(max(pos, 1e-30) / max(neg, 1e-30))
        if ratio >= TX8_IMAGE_DB:
            return "packed"
        return "mirrored" if ratio <= -TX8_IMAGE_DB else "real"

    def _measure_range(self) -> tuple[float, float] | None:
        rx = parse_range(self._read(self.rx_lo_ch, "frequency_available") or "")
        tx = parse_range(self._read(self.tx_lo_ch, "frequency_available") or "")
        if rx and tx:
            lo, hi = max(rx[0], tx[0]), min(rx[1], tx[1])
            if lo < hi:
                return lo, hi
        elif rx or tx:
            return rx or tx
        chip = CHIPS.get(self.phy_model)
        if chip:
            return chip[0], chip[1]
        return None

    def _bw_limit(self) -> float:
        chip = CHIPS.get(self.phy_model)
        if chip:
            return chip[2]
        # No personality reported: a range beyond the AD9363's says the
        # driver is running as a 56 MHz part; otherwise assume the stock
        # chip, which is also the safe guess (the driver clamps anyway).
        if self.range and (self.range[0] < 325e6 or self.range[1] > 3800e6):
            return 56e6
        return 20e6

    def close(self) -> None:
        try:
            self.tx_stop()
        finally:
            if getattr(self, "fir_bypassed", False) and not self.leave_as_is:
                # After tx_stop: the transmitter is attenuated and its LO
                # down before the other program's filter comes back. Not if
                # the lock was lost: the radio is somebody else's by then,
                # and their filter is theirs to set.
                try:
                    self._fir_attr().value = "1"
                except (AttributeError, OSError):
                    pass
            self.fir_bypassed = False
            ev = getattr(self, "_rx_stop", None)
            if ev is not None:
                ev.set()
                t = self._rx_thread
                if t is not None:
                    t.join(timeout=2)
                self._rx_thread = None
            self._drop()
            # Last: the radio is ours until everything above is done.
            shared.release(self)

    def _drop(self) -> None:
        """Drop every object that holds the context, then collect: the
        libiio binding releases the radio (and on usb: the claimed USB
        interface) only when the last reference goes. Dropping just self.ctx
        left the channels holding it, and reopening the nano on the test
        laptop failed with "Unable to claim interface 3:4:5: Device or
        resource busy"."""
        for name in ("rxbuf", "txbuf", "rx_ch", "tx_ch", "rx_lo_ch", "tx_lo_ch", "phy", "rxdev", "txdev", "ctx"):
            if hasattr(self, name):
                setattr(self, name, None)
        import gc
        gc.collect()

    def freq_range(self) -> tuple[float, float] | None:
        return self.range

    def duplex(self) -> bool:
        # Measured on Brian's LibreSDR (16.2 MS/s one way), streaming RX and
        # zero-valued TX together with the TX LO powered down: 6.25 MS/s each
        # way held (RX 6.27), 8 MS/s did not (RX 7.42), 10 MS/s much worse (RX
        # 6.34). Both directions together carry about 0.8 x the one-way rate,
        # so full duplex is allowed only within that.
        return (self.link == "ethernet" and self.link_msps is not None
                and 2 * self.fs <= 0.8 * self.link_msps * MHZ)

    def _check_range(self, hz: float) -> None:
        if self.range is not None and not (self.range[0] <= hz <= self.range[1]):
            raise DeviceError(range_refusal(hz, self.range))

    # -- receive -------------------------------------------------------------
    def set_rx_lo(self, hz: float) -> None:
        self._check_range(hz)
        with self._cfg_lock:
            self._lo_moving()
            try:
                self._attr(self.rx_lo_ch, "frequency", int(round(hz)))
                self._hold("receive LO", self.rx_lo_ch, "frequency", round(hz), LO_TOL_HZ, _mhz)
            except DeviceError:
                self._lo_moved(None)    # where it went is not known
                raise
            self.rx_lo = float(hz)
            self._lo_moved(self.rx_lo)
            self._settle("rxLo")

    # -- which LO the samples were taken at ------------------------------------
    # read() hands on samples the radio took up to RX_LO_PIPELINE_BUFS
    # buffers ago, so a retune reaches the stream late by an amount the host
    # cannot see. What it can bound: everything it already holds (the reader's
    # queue, read()'s remainder) was taken before the LO moved, and
    # everything after that plus the pipeline was taken after it. Between the
    # two the LO is not known, and read_lo says so; the engine then shows no
    # spectrum for those frames rather than a wrong one, and an IQ recording
    # marks them.
    def _held_ahead(self) -> int:
        """Samples taken but not yet returned by read() that the host holds."""
        return self._rx_pos + len(self.left) + int(getattr(self, "_rx_qn", 0))

    def _lo_moving(self, held_too: bool = False) -> None:
        """Before the LO is written: from what the host does not yet hold on,
        the LO is not known. `held_too`: nor for what it holds - the LO was
        moved by something else, at a moment nobody saw (watch())."""
        with self._lo_lock:
            start = self._rx_pos if held_too else self._held_ahead()
            self._lo_marks = [m for m in self._lo_marks if m[0] < start] + [(start, None)]

    def _lo_moved(self, lo: float | None) -> None:
        """After it is written and read back: samples beyond the pipeline as
        it stands now are at `lo`."""
        with self._lo_lock:
            n = int(getattr(self, "rx_buf_n", 0) or self.block)
            at = self._held_ahead() + RX_LO_PIPELINE_BUFS * n
            self._lo_marks = [m for m in self._lo_marks if m[0] < at] + [(at, lo)]

    def lo_over(self, start: int, n: int) -> float | None:
        """The LO samples [start, start + n) of the stream were taken at, or
        None if that is not known for all of them."""
        with self._lo_lock:
            lo = None
            for at, mark in self._lo_marks:
                if at <= start:
                    lo = mark
                elif at < start + n:
                    return None
                else:
                    break
            return lo

    def _advance(self, n: int) -> None:
        """read() has returned or skipped `n` more samples; marks wholly
        behind it are forgotten (the last one still applies)."""
        with self._lo_lock:
            self._rx_pos += n
            marks = self._lo_marks
            while len(marks) > 1 and marks[1][0] <= self._rx_pos:
                marks.pop(0)

    @property
    def read_lo(self) -> float | None:
        return self._read_lo

    # -- receive: a reader thread ---------------------------------------------
    # Measured on a real Pluto nano over USB: with refill() called from the
    # engine's receive thread, between demodulating frames, 4.85 MS/s arrived
    # of the 6.6-7.1 the link carries - while the thread demodulated, the
    # radio's four kernel buffers filled and samples were lost. A thread that
    # does nothing but refill (libiio's call releases the GIL: it is a ctypes
    # foreign call) keeps the link busy; read() converts what it queued.
    # While transmitting it pauses: on USB the link has no room for both.
    def _reader(self):
        while not self._rx_stop.is_set():
            if self.tx_on and not self.duplex():
                time.sleep(0.01)
                continue
            try:
                self.rxbuf.refill()
                raw = self.rxbuf.read()
            except (OSError, AttributeError) as e:
                if self._rx_stop.is_set():
                    return
                self._rx_err = e
                with self._rx_cv:
                    self._rx_cv.notify_all()
                return
            self._rx_delivered(len(raw) // self._bps)
            with self._rx_cv:
                self._rx_q.append(raw)
                self._rx_qn += len(raw) // self._bps
                while self._rx_qn > self.fs * RX_QUEUE_S and len(self._rx_q) > 1:
                    old = self._rx_q.popleft()
                    self._rx_qn -= len(old) // self._bps
                    self.rx_dropped += len(old) // self._bps       # the reader of read() fell behind
                self._rx_cv.notify_all()

    def _init_queue(self):
        self._rx_stop = threading.Event()
        self._rx_cv = threading.Condition()
        self._rx_q = collections.deque()
        self._rx_qn = 0
        self._rx_err = None
        self.rx_dropped = 0          # samples never given to the engine
        self.rx_skips = 0            # times read() skipped ahead

    def _start_reader(self):
        self._init_queue()
        self._rx_thread = threading.Thread(target=self._reader, name="pluto-rx", daemon=True)
        self._rx_thread.start()

    def _catch_up(self) -> None:
        """Skip whole frames if the engine has fallen behind (RX_BEHIND_S)."""
        frame = int(round(self.fs * 2 * BLOCK_S))
        with self._rx_cv:
            backlog = self._rx_qn + len(self.left)
            if backlog <= self.fs * RX_BEHIND_S:
                return
            skip = int((backlog - self.fs * RX_CATCHUP_S) // frame) * frame
            if skip <= 0:
                return
            self.rx_dropped += skip
            self.rx_skips += 1
            k = min(skip, len(self.left))
            self.left = self.left[k:]
            rest = skip - k
            while rest > 0 and self._rx_q:
                raw = self._rx_q[0]
                m = len(raw) // self._bps
                if m <= rest:
                    self._rx_q.popleft()
                    self._rx_qn -= m
                    rest -= m
                else:
                    self._rx_q[0] = raw[rest * self._bps:]
                    self._rx_qn -= rest
                    rest = 0
            self._advance(skip)

    # The tests drive the delivered-rate measurement with a fake clock, one
    # refill per read; they turn the thread off to keep that deterministic.
    threaded_rx = True

    def read(self, n: int) -> np.ndarray:
        if not self.threaded_rx:
            return self._read_direct(n)
        if getattr(self, "_rx_thread", None) is None:
            self._start_reader()
        self._catch_up()
        while len(self.left) < n:
            with self._rx_cv:
                while not self._rx_q and self._rx_err is None:
                    self._rx_cv.wait(1.0)
                if self._rx_err is not None and not self._rx_q:
                    e, self._rx_err = self._rx_err, None
                    self._rx_thread = None
                    raise DeviceError(f"Pluto receive failed: {e}") from e
                raw = self._rx_q.popleft()
                self._rx_qn -= len(raw) // self._bps
            iq = self._to_iq(raw, self._rx_pos + len(self.left))
            self.left = np.concatenate([self.left, iq]) if len(self.left) else iq
        return self._take(n)

    def _take(self, n: int) -> np.ndarray:
        out, self.left = self.left[:n], self.left[n:]
        self._read_lo = self.lo_over(self._rx_pos, n)
        self._advance(n)
        return out

    def _read_direct(self, n: int) -> np.ndarray:
        while len(self.left) < n:
            try:
                self.rxbuf.refill()
                raw = self.rxbuf.read()
            except OSError as e:
                raise DeviceError(f"Pluto receive failed: {e}") from e
            iq = self._to_iq(raw, self._rx_pos + len(self.left))
            self._rx_delivered(len(iq))
            self.left = np.concatenate([self.left, iq]) if len(self.left) else iq
        return self._take(n)

    # Where an IQ recording taps the stream (ohsatv/iqrec.py): called with
    # each raw buffer as it is converted, so a recording holds exactly what
    # the engine was given - including any buffer the reader had to drop
    # before it, which shows as a jump, as it did on air - and the LO that
    # buffer was taken at (lo_over), so the recording's capture segments
    # follow the samples rather than the moment the engine retuned.
    raw_tap = None

    @property
    def native_format(self) -> tuple[str, float]:
        """(SigMF datatype, full scale) of the raw receive buffers."""
        return ("ci8", 128.0) if self.rx_bits == 8 else ("ci16_le", 2048.0)

    def _to_iq(self, raw, pos: int | None = None) -> np.ndarray:
        """Raw buffer bytes -> complex64 with the ADC's full scale at 1:
        12-bit samples sign-extended in int16 (/2048), or Tezuka's packed
        int8 pairs, the top 8 of those bits (/128)."""
        tap = self.raw_tap
        if tap is not None:
            if pos is None:
                tap(raw)
            else:
                tap(raw, lo=self.lo_over(pos, len(raw) // self._bps))
        # Scaled in place, as float32: dividing the complex64 view made a
        # second copy of every buffer (3.5 ms against 1.3 a 1 M-sample buffer
        # on shack - most of a millisecond a frame of the receive thread's
        # time at 10 MS/s). The scales are powers of two, so the values are
        # the same.
        if self.rx_bits == 8:
            x = np.frombuffer(raw, np.int8).astype(np.float32)
            x *= np.float32(1 / 128)
        else:
            x = np.frombuffer(raw, np.int16).astype(np.float32)
            x *= np.float32(1 / 2048)
        return x.view(np.complex64)

    def _rx_delivered(self, n: int) -> None:
        t = self.clock()
        h = self._rx_hist
        if h and t - h[-1][0] > RX_GAP_S:
            h.clear()
        h.append((t, n))
        while len(h) > 2 and t - h[1][0] >= RX_WINDOW_S:
            h.popleft()

    def rx_rate(self) -> float | None:
        """Samples per second actually delivered over the last few seconds,
        or None until there is enough to judge. The first refill only marks
        the start: its samples were gathered before the window opened."""
        h = self._rx_hist
        if len(h) < 2:
            return None
        span = h[-1][0] - h[0][0]
        if span < RX_MIN_SPAN_S:
            return None
        return sum(n for _, n in list(h)[1:]) / span

    # -- transmit ------------------------------------------------------------
    def set_tx_lo(self, hz: float) -> None:
        self._check_range(hz)
        with self._cfg_lock:
            self._attr(self.tx_lo_ch, "frequency", int(round(hz)))
            self._hold("transmit LO", self.tx_lo_ch, "frequency", round(hz), LO_TOL_HZ, _mhz)
            self.tx_lo = float(hz)
            self._settle("txLo")

    def set_tx_gain(self, db: float) -> None:
        self.tx_gain = max(TX_ATTEN_MAX, min(0.0, round(float(db) * 4) / 4))
        if self.tx_on and self._gain_applied:
            self._attr(self.tx_ch, "hardwaregain", self.tx_gain)

    def tx_start(self) -> None:
        if self.tx_on:
            return
        self._attr(self.tx_ch, "hardwaregain", TX_ATTEN_MAX)
        self._gain_applied = False
        self._try(self.tx_lo_ch, "powerdown", 0)
        # 8-bit: voltage0 alone carries the packed pair (see _probe_tx8).
        self.txdev.find_channel("voltage0", True).enabled = True
        self.txdev.find_channel("voltage1", True).enabled = self.tx_bits == 16
        try:
            self.txdev.set_kernel_buffers_count(4)
        except (AttributeError, OSError):
            pass
        try:
            # Long TX buffers, as for receive. Measured on the nano over USB
            # (zero samples, TX LO powered down): at 5 MS/s, 100 k-sample
            # buffers ran late 16 times in 751, 250 k 4 in 302, 1 M never.
            self.tx_buf_n = int(min(RX_BUF_MAX, max(self.block, self.fs * 0.2)))
            self.txbuf = self.iio.Buffer(self.txdev, self.tx_buf_n, False)
        except OSError as e:
            self._try(self.tx_lo_ch, "powerdown", 1)
            raise DeviceError(f"Pluto: could not create the transmit buffer ({e})") from e
        self.tx_pending = np.zeros(0, np.complex64)
        self._tx_until = None
        self.tx_on = True

    def _push(self, data: bytes, count: bool = True) -> None:
        now = self.clock()
        if count and self._tx_until is not None and now > self._tx_until:
            self.tx_late += 1
        self._tx_until = max(now, self._tx_until or now) + (len(data) // self._tx_bps) / self.fs
        self.txbuf.write(bytearray(data))
        self.txbuf.push()

    def write(self, iq: np.ndarray) -> None:
        if not self.tx_on:
            raise DeviceError("Transmit is not started")
        self.tx_pending = np.concatenate([self.tx_pending, np.asarray(iq, np.complex64)])
        n = getattr(self, "tx_buf_n", self.block)
        while len(self.tx_pending) >= n:
            blk, self.tx_pending = self.tx_pending[:n], self.tx_pending[n:]
            try:
                self._push(self.to_native(blk).tobytes())
            except OSError as e:
                raise DeviceError(f"Pluto transmit failed: {e}") from e
            if not self._gain_applied:
                self._attr(self.tx_ch, "hardwaregain", self.tx_gain)
                self._gain_applied = True

    def to_native(self, iq: np.ndarray) -> np.ndarray:
        """complex64 (|x| <= 1) -> what goes in the transmit buffer, two
        values a sample: interleaved int16 (12 bits, MSB-aligned: full scale
        +-2047 << 4, i.e. x 32767), or for 8-bit transmit interleaved int8
        I, Q at +-127 - rounded and clipped symmetrically, as the HackRF's.
        The modulators peak at 0.95, so 8 bits keep 45 dB and more of
        quantisation SNR for FM and over 40 for AM-VSB at 10 MS/s
        (tests/test_devices_pluto.py measures both)."""
        if self.tx_bits == 8:
            return to_int8(iq)
        iq = np.asarray(iq, np.complex64)
        s = np.empty(2 * len(iq), np.int16)
        s[0::2] = np.clip(np.round(iq.real * 32767), -32767, 32767)
        s[1::2] = np.clip(np.round(iq.imag * 32767), -32767, 32767)
        return s

    def write_loop(self, loop: np.ndarray, stop) -> None:
        """Send `loop` (a seam-free static picture, complex64) over and over
        until `stop` (a threading.Event) is set or transmit stops.

        Why a separate path: through write(), every buffer cost a
        concatenation and an int16 conversion in Python, and with the
        receive window busy on the same laptop the nano fell behind - 257
        late buffers in a keyed minute at 5 MS/s, each a slip the far end
        had to reacquire. Here the loop is converted once, and the few
        buffer-long slices it cycles through (a 0.16 s loop into 0.2 s
        buffers repeats every 4 buffers) are built before the first push,
        so the loop below does nothing but push prepared bytes - libiio's
        push releases the GIL - and keeps the device fed as well as the
        bare driver does."""
        if not self.tx_on:
            raise DeviceError("Transmit is not started")
        native = self.to_native(loop)                    # int16 or int8, 2 per sample
        L = len(loop)
        n = getattr(self, "tx_buf_n", self.block)
        cycle = L // math.gcd(L, n)                      # buffers before the slices repeat
        reps = -(-(L + n) // L) + 1
        tiled = np.tile(native, reps)
        if cycle <= 16:
            slices = [bytearray(tiled[2 * ((i * n) % L): 2 * ((i * n) % L + n)].tobytes()) for i in range(cycle)]
        else:
            slices = None
        i = 0
        while self.tx_on and not stop.is_set():
            if slices is not None:
                data = slices[i % cycle]
            else:
                off = (i * n) % L
                data = bytearray(tiled[2 * off: 2 * (off + n)].tobytes())
            try:
                self._push(data)
            except OSError as e:
                raise DeviceError(f"Pluto transmit failed: {e}") from e
            if not self._gain_applied:
                self._attr(self.tx_ch, "hardwaregain", self.tx_gain)
                self._gain_applied = True
            i += 1

    def tx_stop(self) -> None:
        if not self.tx_on:
            return
        self.tx_on = False
        self._gain_applied = False
        try:
            self._attr(self.tx_ch, "hardwaregain", TX_ATTEN_MAX)
        except DeviceError:
            pass
        try:
            if self.txbuf is not None:
                # Not counted as late: the engine has stopped writing by
                # now, and a gap before the closing zeros is not an underrun
                # anyone hears.
                self._push(bytes(self._tx_bps * getattr(self, "tx_buf_n", self.block)), count=False)
        except OSError:
            pass
        self.txbuf = None
        self.tx_pending = np.zeros(0, np.complex64)
        self._try(self.tx_lo_ch, "powerdown", 1)
        with self._cfg_lock:
            self._settle("txGain", "txPowerdown")

    # -- the watch -------------------------------------------------------------
    def watch(self) -> list[dict]:
        """Read the rate, the RX LO and (while keyed) the TX LO the radio
        holds now, and compare them with what was set. Returns one dict per
        value that has moved - {"what": "sampleRate" | "rxLo" | "txLo",
        "from": set, "to": found, "held", "text"} - and [] when nothing has.

        The engine calls this about once a second from a thread of its own
        (radio.py, _guard): the reads are iiod round trips, and one that
        hangs for the context's timeout must stall neither the samples nor
        the supervisor that unkeys. The streams are not touched: libiio
        gives an open buffer its own USB pipe or TCP connection, so these
        reads queue behind no samples and no samples behind them.

        A moved rate or RX LO is set back once and read again: "held" says
        whether that took. A moved TX LO is not set back - the transmitter
        is already off frequency, and the engine unkeys on it at once - so
        its "held" is None. A rate set back is followed by both RF
        bandwidths: whatever moves the rate (Tezuka's api_controller sets a
        "span") may have narrowed the analogue filter with it, and writing
        them costs no read."""
        with self._cfg_lock:
            if self.ctx is None or getattr(self, "rx_ch", None) is None:
                return []
            checks = [("sampleRate", "sample rate", self.rx_ch, "sampling_frequency", self.fs, RATE_TOL_HZ, _msps)]
            if self.rx_lo is not None:
                checks.append(("rxLo", "receive LO", self.rx_lo_ch, "frequency", self.rx_lo, LO_TOL_HZ, _mhz))
            if self.tx_on and self.tx_lo is not None:
                checks.append(("txLo", "transmit LO", self.tx_lo_ch, "frequency", self.tx_lo, LO_TOL_HZ, _mhz))
            moves = []
            for key, what, chan, name, want, tol, show in checks:
                got = self._number(chan, name)
                if got is None or abs(got - want) <= tol:
                    continue
                move = {"what": key, "from": want, "to": got, "held": None}
                # Not ours, whether or not it goes back: the next key-up is
                # refused until the operator accepts the radio (changed()).
                self._foreign(f"{what} {show(want)} -> {show(got)}")
                if key == "txLo":
                    move["text"] = (f"The radio's transmit LO moved from {show(want)} to {show(got)} by itself while "
                                    f"transmitting: off frequency")
                else:
                    if key == "rxLo":
                        self._lo_moving(held_too=True)
                    self._try(chan, name, int(round(want)))
                    if key == "sampleRate" and self.rf_bw is not None:
                        self._try(self.rx_ch, "rf_bandwidth", int(self.rf_bw))
                        self._try(self.tx_ch, "rf_bandwidth", int(self.rf_bw))
                    after = self._number(chan, name)
                    move["held"] = after is not None and abs(after - want) <= tol
                    if key == "rxLo":
                        self._lo_moved(want if move["held"] else None)
                    self._settle(key, *(("rxBandwidth", "txBandwidth") if key == "sampleRate" else ()))
                    if move["held"]:
                        move["text"] = (f"The radio's {what} moved from {show(want)} to {show(got)} by itself; "
                                        f"set back, it held")
                    else:
                        move["text"] = (f"The radio's {what} moved from {show(want)} to {show(got)} by itself and "
                                        f"will not go back: something else is setting this radio (Tezuka's own "
                                        f"control script can)")
                moves.append(move)
            if moves:
                self.moved = moves
                self.moves += len(moves)
            return moves

    # -- changed since we last set it ---------------------------------------
    def _state_value(self, key: str):
        _, holder, name, tol, _ = STATE[key]
        if holder is None:                       # the FIR's enable: an attribute object, not a channel
            fir = self._fir_attr()
            try:
                return None if fir is None else fir.value.strip()
            except (AttributeError, OSError):
                return None
        chan = getattr(self, holder, None)
        if chan is None:
            return None
        if tol is None:
            v = self._read(chan, name)
            return None if v is None else v.strip()
        return self._number(chan, name)

    def _snapshot(self) -> dict:
        """Every STATE value the radio holds now (None: not readable here).
        The receive gain only under manual control: the AGC moves it."""
        out = {k: self._state_value(k) for k in STATE if k != "rxGain"}
        if out.get("rxGainMode") == "manual":
            out["rxGain"] = self._state_value("rxGain")
        return out

    def _settle(self, *keys: str) -> None:
        """What the radio holds now for `keys` is this program's doing: the
        new baseline for them. Called under _cfg_lock after each write."""
        if self.ctx is None or not self.seen:
            return                   # still opening: open() takes the whole snapshot at its end
        for k in keys:
            self.seen[k] = self._state_value(k)

    @staticmethod
    def _show(key: str, v) -> str:
        if isinstance(v, (int, float)):
            return {"mhz": _mhz, "msps": _msps, "db": _db}.get(STATE[key][4], lambda x: f"{x:g}")(v)
        return str(v)

    def _differences(self, old: dict, new: dict) -> list[str]:
        out = []
        for key, (what, _, _, tol, _) in STATE.items():
            a, b = old.get(key), new.get(key)
            if a is None or b is None:
                continue
            if tol is None:
                same = a == b
            else:
                same = abs(float(a) - float(b)) <= (BW_TOL * abs(float(a)) if tol == "bw" else tol)
            if not same:
                out.append(f"{what} {self._show(key, a)} -> {self._show(key, b)}")
        return out

    def _foreign(self, phrase: str) -> None:
        if phrase not in self.foreign:
            self.foreign.append(phrase)

    def changed(self) -> str:
        """'' if the radio holds what this program last set or saw, else
        the sentence refusing the key-up (see the module docstring). Reads
        the radio; changes nothing on it. What it finds stays found until
        accept(), even if the other program puts the value back."""
        with self._cfg_lock:
            if self.ctx is None or not self.seen:
                return ""
            for d in self._differences(self.seen, self._snapshot()):
                self._foreign(d)
            return self.changed_text()

    def changed_text(self) -> str:
        """The refusal for what has been found so far, without reading."""
        if not self.foreign:
            return ""
        return (f"Something other than OpenHamShack ATV has changed this radio since it last set it "
                f"({'; '.join(self.foreign)}): another program may be using it. Not keyed - close that "
                f"program, then accept the radio as it is to transmit again")

    def accept(self) -> None:
        """The operator's word: the radio as it is now is the baseline."""
        with self._cfg_lock:
            if self.ctx is None:
                return
            self.seen = self._snapshot()
            self.foreign = []

    # -- status --------------------------------------------------------------
    def status(self) -> dict:
        rate = self.rx_rate()
        st = {"kind": "pluto", "uri": self.uri, "model": self.model, "phyModel": self.phy_model or None,
              "range": list(self.range) if self.range else None, "link": self.link,
              "linkMsps": self.link_msps, "fs": self.fs, "rfBandwidth": self.rf_bw,
              "rxLo": self.rx_lo, "txLo": self.tx_lo, "tx": self.tx_on,
              "rxRate": None if rate is None else round(rate),
              "rxShort": None if rate is None else rate < RX_SHORT * self.fs,
              "rxDropped": getattr(self, "rx_dropped", 0), "rxSkips": getattr(self, "rx_skips", 0),
              "txLate": self.tx_late, "rxBits": getattr(self, "rx_bits", 16),
              "rx8": getattr(self, "rx8_capable", False), "rx8Checks": getattr(self, "rx8_checks", 0),
              "txLinkMsps": getattr(self, "tx_link_msps", None),
              "usbNetUp": list(getattr(self, "usb_net_up", [])), "usbAddr": getattr(self, "usb_addr", None),
              "txBits": getattr(self, "tx_bits", 16), "tx8": getattr(self, "tx8_capable", False),
              "tx8Check": getattr(self, "tx8_check", None),
              "fw": getattr(self, "fw", "") or None, "firBypassed": getattr(self, "fir_bypassed", False),
              "rxPathRates": self.path_rates.get("rx"),
              "rxCorrection": self.rx_response.describe() if self.rx_response else None,
              "txCorrection": self.tx_response.describe() if self.tx_response else None,
              "moved": [dict(m) for m in getattr(self, "moved", [])], "moves": getattr(self, "moves", 0),
              "changed": self.changed_text(),
              "radioLock": self.radio_lock.describe() if self.radio_lock is not None else {},
              "notes": list(self.notes)}
        return st
