"""The best mode for each radio, chosen when the engine first meets it.

Brian's decision (4 Oct 2026): all four routes to FM-ATV stay supported -
a HackRF transmitting, a LibreSDR on Ethernet, a Pluto+ on Ethernet, and a
USB Pluto at reduced deviation - and the program should steer each to what
it does best rather than leave the operator to work out sample rates.

When the receiving radio, its link, or the transmitter changes, the engine
applies that combination's settings once and says what it chose and why. It
does not keep re-applying them: a rate or deviation the operator then
changes stays changed until a different radio appears (`profileKey` remembers
which combination was steered for). `autoProfile` off in Settings stops it.

The numbers come from docs/atv-research.md and the plan functions (see
DESIGN.md "What fits where"):

- **USB Pluto over libusb** (`usb:`): receives 6.25 MS/s in full on the
  laptop's nano but transmits only ~5.2, so 5 MS/s. AM-VSB: a 2.4 MHz mono
  picture, no sound carrier. FM: 1.2 MHz p-p with 1.6 MHz of video, mono, no
  subcarrier - narrow FM-ATV, which a standard wide receiver will see as a
  weak, low-contrast picture.
- **USB Pluto on Tezuka firmware with 8-bit receive** (`usb:`, probed at
  open; docs/atv-research.md 13): it receives up to ~15 MS/s but still
  transmits only ~5.2, and the AD936x runs one rate both ways. Steered for
  receive: AM-VSB at 10 MS/s in colour with sound; FM-ATV at 12.5 MS/s in
  colour with the 5.5 MHz sound subcarrier, which leaves room for only
  1.2 MHz p-p. Keying above 5.2 MS/s is refused by the engine; to transmit
  from it, choose 5 MS/s.
- **USB Pluto on Tezuka with 8-bit transmit too** (`usb:`, checked in
  digital loopback at open; docs/atv-research.md 18): one rate both ways
  that it can send - 10 MS/s, where 8-bit transmit keeps up over USB
  (~10.8 MS/s ceiling measured on the nano, 2 bytes a sample, nothing
  radiated). AM-VSB in colour with sound; FM-ATV mono at 3 MHz p-p with
  3 MHz of video and no subcarrier, because colour in FM needs 12.5 MS/s,
  which 8-bit transmit does not reach. No Tezuka bitstream unpacks 8-bit
  transmit yet (v0.3.22), so no radio is steered here today.
- **USB Pluto through its USB network link** (`ip:192.168.2.1`; ~5.2
  MS/s delivered on the nano): 5 MS/s, narrower still, and a pointer to the
  usb: address.
- **Ethernet at ~16 MS/s** (Brian's LibreSDR: 16.2 measured, though its
  README says 20) or of unknown speed: FM at 16 MS/s, 4 MHz p-p, colour and
  sound; AM-VSB at 10 MS/s.
- **Ethernet at 20 MS/s or more** (a Pluto+, ~28 reported ceiling):
  FM-ATV to IARU R1 V.2 at 20 MS/s (7 MHz p-p, 5 MHz video, 6 MHz sound);
  AM-VSB at 10 MS/s with colour and sound.
- **HackRF transmitting**: the same full-width settings (it carries 20 MS/s
  of 8-bit IQ over USB), and AM-VSB at 20 MS/s too, not 10: the HackRF has
  no interpolation filter, so its images sit at +-fs from each carrier with
  only the MAX2837's baseband filter in front of them. At 10 MS/s, with
  625 lines' carriers centred, the sound carrier's image came out 4 MHz
  below the vision carrier, modelled about 3 dB under the sound carrier - in
  the channel below - and the vision carrier's 10 MHz above it at -18; with
  them placed for a Pluto's images (modem._place, from 5 October 2026) both
  are modelled at the carriers' own level. At 20 MS/s, where the plan is
  centred, through the filter the engine narrows to the
  plan before every AM over (an 8 MHz setting), -70 and -77 dBc
  (devices/hackrf.py, with its 20 dB shortfall). The same fault as 405
  lines' at 8 MS/s, which were measured on air (docs/atv-research.md 21,
  21a). The receiving radio runs at the same rate; if that is a USB Pluto it
  will drop samples receiving at 20 MS/s, and the window says so - full-width
  receive needs an Ethernet radio.

**405 lines** have their own rate, `a405Rate` (0: as AM-VSB), because what
decides it is the transmitter's images (docs/atv-research.md 21, 21a): a
HackRF sends them at 20 MS/s, where its images are 20 MHz out and its
baseband filter takes them down; a USB Pluto at 5 MS/s without sound, or
at 6.25 with sound when its transmit link is known to carry that - its own
USB network down on this computer (18a; devices/pluto.py
USB_TX_NET_DOWN_MSPS). Every other profile sets it back to 0.
"""
from __future__ import annotations

# The wide profiles also go back to standard PAL: at these rates it has room
# for colour and sound, and a narrow format (videoFormat) would only cut off
# every standard receiver. The narrow profiles leave the format alone - the
# narrow formats are offered there, never chosen for the operator.
FULL = {"fmRate": 20_000_000, "fmDeviation": 7.0e6, "fmVideoBw": 5.0e6, "fmSub": 6.0e6, "amRate": 10_000_000,
        "videoFormat": "pal"}
# An Ethernet radio that carries ~16 MS/s - Brian's LibreSDR measured 16.2
# (stock iiod 0.24, gigabit): FM at 16 MS/s, 4 MHz p-p with 5 MHz video, colour
# and the 6 MHz sound subcarrier (Carson 14.8 MHz).
ETH16 = {"fmRate": 16_000_000, "fmDeviation": 4.0e6, "fmVideoBw": 5.0e6, "fmSub": 6.0e6, "amRate": 10_000_000,
         "videoFormat": "pal"}
# A Pluto on USB receives 6.25 MS/s in full but transmits only 5.1-5.3 (the
# laptop's nano, measured after its first real transmission could not be
# received: at 6.25 the DAC starved ~15 % of the time). One rate serves both
# directions, so a USB Pluto runs at 5 MS/s: a 2.4 MHz mono AM picture, FM
# at 1.2 MHz p-p with 1.6 MHz video.
USB = {"fmRate": 5_000_000, "fmDeviation": 1.2e6, "fmVideoBw": 1.6e6, "amRate": 5_000_000}
# A USB Pluto with Tezuka's 8-bit receive (15.0 MS/s measured on the nano).
# The rates are for receiving: 10 MS/s AM-VSB carries colour and sound;
# 16 MS/s FM would be past the 15 measured, so 12.5. At 12.5 the 6 MHz sound
# subcarrier is above the video Nyquist limit (0.48 fs), 5.5 fits with 4.9
# MHz of video (colour needs 4.73), and Carson's 11.9 MHz then leaves
# 1.2 MHz p-p (fm_plan: 11.8 MHz). fmSub is set because it changes here;
# FULL and ETH16 set it back.
USB_RX8 = {"fmRate": 12_500_000, "fmDeviation": 1.2e6, "fmVideoBw": 4.9e6, "fmSub": 5.5e6,
           "amRate": 10_000_000}
# A USB Pluto that transmits 8-bit too. 10 MS/s is the highest rate in the
# table that its USB transmit carries in full (10.8 MS/s at 2 bytes a sample,
# measured on the nano 5 October 2026 with the TX LO powered down) - 20 MB/s,
# the same as the USB profile's 5 MS/s at 16 bits, so no more demanding of
# the link than what the nano already transmits. AM-VSB
# there has 4.8 MHz of video - colour - and the 6 MHz sound carrier. FM at
# 10 MS/s carries 9.5 MHz: colour (4.73 MHz of video) would leave no
# deviation and the 5.5 MHz subcarrier is past the video Nyquist limit
# (4.8 MHz), so FM is a mono picture at 3 MHz p-p with 3 MHz of video
# (Carson 9.0 MHz) - three times the 5 MS/s profile's deviation, and still
# what a standard receiver expects at the subcarrier: nothing. fmSub is set
# because it changes here; FULL and ETH16 set it back.
USB_TX8 = {"fmRate": 10_000_000, "fmDeviation": 3.0e6, "fmVideoBw": 3.0e6, "fmSub": 0.0, "amRate": 10_000_000}
# The Pluto's USB network link (ip:192.168.2.1): ~5.2 MS/s delivered,
# measured on the test laptop's nano. 5 MS/s: 2.4 MHz mono AM, FM 1.2 MHz
# p-p with 1.6 MHz video (Carson 4.4 MHz).
USB_NET = {"fmRate": 5_000_000, "fmDeviation": 1.2e6, "fmVideoBw": 1.6e6, "amRate": 5_000_000}
for _p in (FULL, ETH16, USB, USB_RX8, USB_TX8, USB_NET):
    _p["a405Rate"] = 0
# A HackRF: AM-VSB at 20 MS/s, 625 lines and 405 (Brian for 405, 5 October
# 2026; 625 the same, for the same images - see the module notes). a405Rate
# is set as well rather than left to follow amRate: an operator who brings
# amRate down for a slow receiver should not take 405 lines with it.
HACKRF = dict(FULL, amRate=20_000_000, a405Rate=20_000_000)
# The rate 405 lines need for their sound (sysa.SOUND_MIN_FS).
A405_SOUND_RATE = 6_250_000


# What a USB Pluto is offered instead of the mono picture (ohsatv/narrow.py).
NARROW_OFFER = (" Colour and sound in this channel: Mini-MAC or narrow colour PAL under Transmit › Format - "
                "OpenHamShack ATV only, not compatible with standard ATV receivers.")


def _usb_405(tx_msps) -> tuple[dict, str]:
    """405 lines on a USB Pluto, 16-bit transmit: 6.25 MS/s with sound if
    its transmit link is known to carry that, else 5 MS/s without. 405
    needs no more than 6.25 to receive either, so a nano steered to receive
    colour at 8-bit rates can still send them."""
    if tx_msps and tx_msps * 1e6 >= A405_SOUND_RATE:
        return {"a405Rate": A405_SOUND_RATE}, (
            f" 405 lines at 6.25 MS/s with their sound: the radio's USB network is down here, so its transmit "
            f"carries ~{tx_msps:g} MS/s.")
    return {"a405Rate": 5_000_000}, (
        " 405 lines at 5 MS/s without sound (there the sound carrier's image would leave the channel); with "
        "the radio's USB network down on this computer it sends them at 6.25 MS/s with sound.")


def key(rx_kind: str, link: str, link_msps, tx_kind: str, tx_msps=None) -> str:
    """The combination a profile was chosen for. `tx_msps`, what the radio's
    transmit link is known to carry, is part of it only where it is known:
    a USB Pluto, once opened."""
    base = f"{rx_kind}/{link}/{link_msps}/{tx_kind}"
    return base + (f"/tx{tx_msps:g}" if tx_msps else "")


def best(rx_kind: str, link: str, link_msps, tx_kind: str, tx_msps=None) -> tuple[dict, str]:
    """(settings, sentence) for this combination; ({}, "") when there is
    nothing to steer (the simulator, or a link of unknown speed)."""
    if tx_kind == "hackrf":
        s = ("HackRF transmitting: FM-ATV at 20 MS/s to IARU V.2, AM-VSB (625 and 405 lines) at 20 MS/s with "
             "colour and sound, where the HackRF's images fall outside its filter.")
        if rx_kind == "pluto" and link in ("usb", "usb-net", "usb-rx8", "usb-tx8"):
            s += (" The Pluto receiving on USB cannot carry 20 MS/s - expect dropped samples on receive; "
                  "full-width receive needs a LibreSDR or Pluto+ on Ethernet.")
        return dict(HACKRF), s
    if rx_kind != "pluto":
        return {}, ""
    if link == "usb-tx8":
        return dict(USB_TX8), ("Pluto on USB with Tezuka's 8-bit receive and transmit: 10 MS/s both ways, the "
                               "most its USB transmit carries. AM-VSB in colour with sound; FM-ATV mono at 3 MHz "
                               "p-p without a sound subcarrier (FM colour needs 12.5 MS/s, beyond 8-bit transmit "
                               "over USB).")
    if link == "usb-rx8":
        settings, a405 = _usb_405(tx_msps)
        return dict(USB_RX8, **settings), ("Pluto on USB with Tezuka's 8-bit receive (~15 MS/s measured): set up to "
                               "receive colour - AM-VSB at 10 MS/s with sound, FM-ATV at 12.5 MS/s with "
                               "5.5 MHz sound and 1.2 MHz p-p. It transmits only ~5.2 MS/s over USB, so "
                               "keying is refused at these rates: choose 5 MS/s to transmit from it." + a405)
    if link == "usb":
        settings, a405 = _usb_405(tx_msps)
        return dict(USB, **settings), ("Pluto on USB: 5 MS/s - it receives 6.25 MS/s but transmits only ~5.2 over USB "
                          "(measured). AM-VSB as a 2.4 MHz mono picture without sound, FM-ATV at reduced "
                          "deviation (1.2 MHz p-p, mono, no sound subcarrier). For full width use a HackRF to "
                          "transmit or an Ethernet radio." + a405 + NARROW_OFFER)
    if link == "usb-net":
        return dict(USB_NET), ("Pluto through its USB network link (~5.2 MS/s measured): 5 MS/s - a 2.4 MHz "
                               "mono AM picture, FM at 1.2 MHz p-p. Its usb: address carries 6.25 MS/s: "
                               "Settings › Radio › Find lists it first." + NARROW_OFFER)
    if link == "ethernet" and link_msps and link_msps >= 20:
        return dict(FULL), (f"Ethernet radio (~{link_msps:g} MS/s): FM-ATV at 20 MS/s to IARU V.2, AM-VSB at "
                            f"10 MS/s with colour and sound.")
    if link == "ethernet":
        speed = (f"~{link_msps:g} MS/s measured" if link_msps else
                 "speed unknown - its receive rate on the status line will say if this is too much")
        return dict(ETH16), (f"Ethernet radio ({speed}): FM-ATV at 16 MS/s, 4 MHz p-p with colour and 6 MHz "
                             f"sound (IARU V.2's 7 MHz needs 20 MS/s, which this link does not carry); AM-VSB "
                             f"at 10 MS/s with colour and sound.")
    return {}, ""
