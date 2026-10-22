"""Every state key the engine has: the single source of truth.

The UI reads types, limits, choices and help text from here rather than
hard-coding them; `Radio.set()` validates against it; `config.py` persists
exactly the keys marked `persist`. Three kinds, as in OpenHamShack NB:

- **setting**: changed in the Settings dialog, persisted.
- **control**: an operating control on the main window (frequency, system,
  source...), persisted so the radio comes back where it was left - except
  that nothing here can make it come back *transmitting*.
- **status**: measured or derived by the engine; read-only to the UI.

`auth` keys decide what or how hard we transmit (the gain ceiling, the
timeout, which device, whether real hardware may transmit at all);
`Radio.set()` refuses them unless called with `authorise=True`, which the UI
passes only from its Settings dialog. `restart` keys take effect when the
device is reopened (the UI does stop() + start() after a settings save).
"""
from __future__ import annotations

import math

from . import bands, host, txguard

SYSTEMS = ["am", "fm"]
RATES = [5_000_000, 6_250_000, 8_000_000, 10_000_000, 12_500_000, 16_000_000, 20_000_000]

SPEC: dict[str, dict] = {}


def _k(name, type_, default, kind, *, lo=None, hi=None, choices=None, unit="", persist=None, auth=False,
       restart=False, help=""):
    SPEC[name] = {
        "type": type_, "default": default, "kind": kind, "min": lo, "max": hi, "choices": choices,
        "unit": unit, "persist": (kind != "status") if persist is None else persist, "auth": auth,
        "restart": restart, "help": help,
    }


# --- device -----------------------------------------------------------------
_k("device", "enum", "sim", "setting", choices=["sim", "pluto", "file"], auth=True, restart=True,
   help="Which radio. The simulator needs no hardware, transmits nothing and is the default. 'file' plays an "
        "IQ recording (SigMF) through the receive chain instead of a radio.")
_k("playFile", "str", "", "setting", restart=True,
   help="The recording to play when the radio is 'file': its .sigmf-meta file. Its sample rate, system and "
        "tuning are taken from the recording.")
_k("playLoop", "bool", True, "setting", restart=True, help="Start the recording again when it ends.")
_k("recSeconds", "float", 60.0, "setting", lo=1.0, hi=3600.0, unit="s",
   help="Record IQ stops after this long.")
_k("recMaxMB", "int", 4000, "setting", lo=10, hi=200_000, unit="MB",
   help="Record IQ stops at this size, whichever limit comes first. 10 MS/s of 8-bit IQ is 20 MB a second, "
        "16-bit 40 MB.")
_k("recDir", "str", "", "setting",
   help="Where recordings go (empty: the 'recordings' folder in the program's data folder).")
_k("plutoUri", "str", "usb:", "setting", auth=True, restart=True,
   help="libiio URI: usb: (the Pluto on USB - the fastest route, 6.25 MS/s in full), usb:<bus>.<dev>.<n>, "
        "ip:<address> (Pluto+ or LibreSDR on Ethernet), ip:192.168.2.1 (a Pluto's USB network link: "
        "about 5 MS/s).")
_k("plutoRx8", "bool", True, "setting", restart=True,
   help="Use Tezuka firmware's 8-bit receive on a USB Pluto when the rate needs it (above 6.25 MS/s): "
        "twice the samples over the same USB link, up to about 15 MS/s, with 48 dB of range instead of "
        "66. Checked when the radio opens; other firmware stays 16-bit.")
_k("plutoTx8", "bool", True, "setting", restart=True,
   help="Use 8-bit transmit on a USB Pluto on Tezuka firmware if its bitstream unpacks it, checked when the "
        "radio opens - through the radio's own digital loopback, transmitter powered down - and used above "
        "5.2 MS/s. No Tezuka release does yet (v0.3.22): the check says so and transmit stays 16-bit, "
        "5 MS/s over USB.")
_k("amRate", "enum", 10_000_000, "setting", choices=RATES, unit="S/s", restart=True,
   help="Sample rate for AM-VSB. 10 MS/s carries colour and the sound carrier; a Pluto on USB 2 receives "
        "about 6.8 MS/s (15 with Tezuka's 8-bit receive) but transmits only about 5.2, so it sends at 5 MS/s - a mono "
        "picture without sound. A HackRF sends at 20 MS/s: below that its images of the carriers come out beside the "
        "channel.")
# 405 lines get a rate of their own because the transmitter's images decide
# it, not the picture: a HackRF has no interpolating filter, and at the 10
# MS/s AM-VSB runs at its images land 10 MHz out, where its baseband filter
# barely acts; at 20 they are far down its skirt (devices/hackrf.py). 0
# leaves 405 lines on amRate, which suits a Pluto, whose half-bands do the
# filtering (sysa.SOUND_MIN_FS).
_k("a405Rate", "enum", 0, "setting", choices=[0] + RATES, unit="S/s", restart=True,
   help="Sample rate for 405 lines, if not the AM-VSB rate. A HackRF sends them at 20 MS/s, where its images of "
        "the carriers are far outside its filter; the receiving radio runs at the same rate.")
_k("fmRate", "enum", 20_000_000, "setting", choices=RATES, unit="S/s", restart=True,
   help="Sample rate for FM-ATV. IARU V.2 deviation needs 20 MS/s (Ethernet: LibreSDR or Pluto+); "
        "16 MS/s carries 4 MHz p-p.")
_k("txDevice", "enum", "same", "setting", choices=["same", "hackrf"], auth=True, restart=True,
   help="What transmits: the same radio that receives, or a HackRF One beside it (8-bit IQ at up to 20 MS/s "
        "over USB 2, so full-width FM-ATV from a USB device). With a HackRF, receive stays on the Pluto, LibreSDR "
        "or simulator and carries on while you transmit.")
_k("hackrfSerial", "str", "", "setting", auth=True, restart=True,
   help="HackRF serial number (empty: the first one found).")
_k("hackrfAmp", "bool", False, "setting", auth=True, restart=True,
   help="Switch on the HackRF's 14 dB RF amplifier while transmitting. Off by default.")
# The radio lock (ohsatv/radiolock.py; OpenHamShack DATV's docs/radio-lock.md):
# on a radio with a shell its marker lives on the radio itself, which is what
# makes it visible to programs on other computers. The spec's defaults are
# the firmwares' own (root / analog on Analog's, Tezuka's, F5OEO's and
# DL1JM's); the password is no secret, and an empty one means ssh keys.
_k("radioLockSsh", "bool", True, "setting", restart=True,
   help="Mark a Pluto or LibreSDR as in use on the radio itself (over ssh), so OpenHamShack programs on other "
        "computers see it too. Off, or if the radio's shell does not answer, the mark is on this computer only, "
        "and the window says so.")
_k("radioSshUser", "str", "root", "setting", restart=True, help="The radio's login for that (root on every firmware).")
_k("radioSshPassword", "str", "analog", "setting", restart=True,
   help="Its password (analog unless it was changed; empty: use this computer's ssh keys).")
_k("autoProfile", "bool", True, "setting",
   help="When a different radio, link or transmitter is first used, choose its best sample rates and FM "
        "deviation automatically (and say what was chosen). Changes you make afterwards are kept.")
_k("profileKey", "str", "", "setting", help="The radio combination the settings were last chosen for.")
_k("rxGainMode", "enum", "slow_attack", "setting", choices=["manual", "slow_attack", "fast_attack"], restart=True,
   help="Receiver gain control in the SDR.")
_k("rxGain", "float", 50.0, "setting", lo=0.0, hi=73.0, unit="dB", restart=True, help="Receiver gain when manual.")

# --- operating ---------------------------------------------------------------
_k("band", "enum", "23cm", "control", choices=[b.name for b in bands.BANDS] + list(bands.CABLE_NAMES),
   help="70cm (analogue outside the bandplan), 23cm or 6cm (5.8 GHz); Band I (B1-B5, 405 lines) and UHF "
        "(E21-E48, 625-line PAL) only with the cable feed on.")
_k("system", "enum", "fm", "control", choices=SYSTEMS, restart=True,
   help="AM-VSB (PAL, System I-style) or FM-ATV.")
_k("rxFreq", "float", 1255.0e6, "control", lo=40e6, hi=6000e6, unit="Hz",
   help="Receive: the vision carrier (AM-VSB) or the carrier (FM).")
_k("txFreq", "float", 1255.0e6, "control", lo=40e6, hi=6000e6, unit="Hz",
   help="Transmit frequency, same convention. Follows rxFreq unless split is on.")
_k("split", "bool", False, "control", help="Transmit on a different frequency from receive (repeaters).")
_k("colour", "enum", "auto", "control", choices=["auto", "mono"], help="Decode colour when there is burst.")
# The narrow formats (ohsatv/narrow.py; DESIGN.md "Narrow formats"): what we
# transmit, and what the receiver expects until formatAuto finds otherwise.
_k("videoFormat", "enum", "pal", "control", choices=["pal", "lowpal", "mac", "405"],
   help="Picture format. PAL is the standard. FM-ATV: Mini-MAC (colour and digital sound) and narrow colour PAL "
        "(1.6 MHz colour, 2.2 MHz sound) fit colour and sound into the ~5 MS/s a USB Pluto can transmit, but are "
        "OpenHamShack ATV only: not compatible with standard ATV receivers. AM-VSB: 405 lines is the British "
        "System A of 1936-1985 (mono, AM sound 3.5 MHz below vision), which fits 5 MS/s whole and which a "
        "vintage 405-line set receives.")
_k("formatAuto", "bool", True, "setting",
   help="When nothing is decoded for a second, look at what is being received and switch the receiver to "
        "match: PAL, narrow colour PAL or Mini-MAC on FM-ATV; 625 or 405 lines on AM-VSB. What you transmit "
        "does not change.")
# Teletext (ohsatv/vbi.py, carousel.py; DESIGN.md "Teletext"). What we send:
# a folder of .tti pages - a "service" - on the picture's blanked lines.
# Receive needs no setting: whatever is on the air is sliced and kept.
_k("ttxOn", "bool", False, "control",
   help="Send teletext with the picture: the pages in the teletext folder, on the blanked lines between "
        "pictures. Only where the mode has the video bandwidth for it (about 3.75 MHz; the plan says).")
_k("ttxDir", "str", "", "control",
   help="The teletext service to send: a folder of .tti pages (as OpenHamShack Teletext, vbit2 or MRG editors save "
        "them), or one with a .t42 stream in it. Read again at each key-up, so an edited page goes out on the "
        "next transmission.")
_k("ttxInitial", "str", "100", "control",
   help="The page a receiver opens on: sent once a second in packet 8/30.")
_k("ttxSrt", "str", "", "control",
   help="Subtitles to send on page 888: an SRT file, timed to the video file's playback position (through pauses "
        "and seeks; with any other picture source, from key-up). Captions typed on the transmit panel go out "
        "over it.")
_k("ttxLines", "str", "7-22,320-335", "setting",
   help="Which blanked lines carry teletext, e.g. 7-22,320-335 (the standard allows 6-22 and 319-335). Fewer "
        "lines leave room for others' test signals; each line is 25 packets a second.")
_k("volume", "float", 0.6, "control", lo=0.0, hi=1.0, help="Receive sound volume.")
_k("mute", "bool", False, "control", help="Mute receive sound.")
_k("view", "enum", "rx", "control", choices=["rx", "tx"],
   help="What the main picture shows: the received picture, or the transmit preview.")
_k("scopeLine", "int", 100, "control", lo=1, hi=625, help="The line the waveform monitor shows.")

# --- source -------------------------------------------------------------------
_k("source", "enum", "testcard", "control", choices=["testcard", "bars", "webcam", "file"],
   help="What is transmitted.")
# A V4L2 node on Linux; on Windows a DirectShow camera's name, where ""
# means the first camera ffmpeg lists (ohsatv/sources.py): no name is the
# same on every PC, and /dev/video0 would name nothing at all there.
WEBCAM_DEFAULT = "" if host.windows() else "/dev/video0"
_k("webcam", "str", WEBCAM_DEFAULT, "setting",
   help=("Camera for the webcam source (blank: the first one found), chosen in Settings › Sources."
         if host.windows() else "V4L2 device for the webcam source, chosen in Settings › Sources."))
_k("videoFile", "str", "", "control", help="Video file for the file source (anything ffmpeg plays).")
_k("audioSource", "enum", "tone", "control", choices=["tone", "mic", "file", "none"],
   help="Sound: a 1 kHz tone, the microphone, the video file's sound, or silence.")
_k("toneHz", "float", 1000.0, "setting", lo=100.0, hi=10_000.0, unit="Hz", help="Test tone frequency.")
# NICAM-728 (ohsatv/nicam.py): what we add to standard PAL where the plan
# has room. Receiving it is automatic.
_k("nicam", "bool", False, "control",
   help="Send NICAM-728 digital stereo sound beside the analogue sound (6.552 MHz above vision, System I), where "
        "the rate has room: AM-VSB from 10 MS/s, FM-ATV from 16 MS/s. With the tone as the sound source it sends "
        "the stereo test: the tone on the left, twice its frequency on the right. Received NICAM is played "
        "whenever it is cleaner than the analogue sound.")

# --- station ---------------------------------------------------------------------
_k("callsign", "str", "", "setting", help="Your callsign. It is drawn into every transmitted picture.")
_k("locator", "str", "", "setting", help="Maidenhead locator, shown under the callsign.")
_k("identText", "str", "", "setting", help="A second ident line (e.g. your name or the band).")
_k("identPosition", "enum", "bottom-left", "setting",
   choices=["bottom-left", "bottom-right", "top-left", "top-right"],
   help="Where the callsign box sits on webcam and file pictures (the test card has it built in).")
# Where the station is and whose licence it transmits under: the station
# profile's location and licence (SERVICES.md section 2.1), which the
# transmit guard reads through ohsatv/txguard.py. The country follows the
# callsign until the operator picks one; the licence follows the country.
# All four are `auth`: they decide where the transmitter may go, so only
# the Settings dialog (and a paired controller's profile, through it)
# changes them.
_k("stationCountry", "str", "", "setting", auth=True,
   help="The station's country, as a DXCC prefix (G, GM, K, DL...). Empty: worked out from your callsign - the "
        "prefix's country when you sign EA8/GM4BRI.")
_k("licence", "enum", "auto", "setting", choices=["auto"] + [k for k, _ in txguard.GUARD.regimes()], auth=True,
   help="The licence whose conditions the transmit guard applies. Automatic: the station country's.")
# No default class (Brian, 05/10/2026, for Radio, and SERVICES.md: an empty
# class locks transmit): a default wrong for most people is worse than a
# one-time choice.
_k("licenceClass", "enum", "", "setting", choices=[""] + txguard.GUARD.all_class_ids(), auth=True,
   help="Your licence class. There is no default: until you choose one, a real radio will not transmit "
        "(receiving works).")
_k("stationConditions", "dict", {}, "setting", auth=True,
   help="Your answers to the licence's questions about where the station is (near London for 431-432 MHz in "
        "the UK...). An unanswered one takes the licence's default, the restrictive side.")
_k("licenceAck", "bool", False, "setting",
   help="You hold an amateur licence, will identify, and accept that you are responsible for what is "
        "transmitted. Transmit is locked until this is set.")
_k("allowHardwareTx", "bool", False, "setting", auth=True,
   help="Let a real radio transmit. Off by default: the simulator transmits nothing, a Pluto does.")

# --- AM-VSB --------------------------------------------------------------------------
_k("amVideoBw", "float", 5.5e6, "setting", lo=2.0e6, hi=5.5e6, unit="Hz", help="Video bandwidth (System I: 5.5 MHz).")
_k("amVsbLower", "float", 1.25e6, "setting", lo=0.75e6, hi=1.25e6, unit="Hz",
   help="Vestigial lower sideband (System I 1.25 MHz, System G 0.75 MHz).")
_k("amSoundOffset", "enum", 5_999_600.0, "setting", choices=[0.0, 5_500_000.0, 5_999_600.0, 6_500_000.0], unit="Hz",
   help="Sound carrier above vision: 5.9996 MHz (UK System I), 5.5 MHz (System G), 6.5 MHz, or none.")
_k("amSoundDb", "float", -13.0, "setting", lo=-30.0, hi=-6.0, unit="dB",
   help="Sound carrier level below peak vision (UK: 20:1 power, -13 dB).")

# --- FM ---------------------------------------------------------------------------------
_k("fmDeviation", "float", 7.0e6, "setting", lo=0.5e6, hi=12.0e6, unit="Hz",
   help="Peak-to-peak deviation, sync tip to peak white, at the pre-emphasis cross-over. IARU R1 V.2: 3.5 MHz "
        "peak = 7 MHz.")
_k("fmVideoBw", "float", 5.0e6, "setting", lo=1.0e6, hi=5.5e6, unit="Hz",
   help="Video bandwidth (IARU V.2: 5 MHz). The narrow profiles a USB Pluto needs go down to 1.6 MHz.")
_k("fmPreemph", "bool", True, "setting", help="CCIR F.405 625-line pre-emphasis (IARU V.2). Both ends must agree.")
_k("fmSub", "enum", 6_000_000.0, "setting", choices=[0.0, 5_500_000.0, 6_000_000.0, 6_500_000.0], unit="Hz",
   help="Sound subcarrier: 6.0 MHz is the UK amateur norm, 6.5 MHz FPV's second channel, 5.5 MHz IARU V.2.")
_k("fmSubIndex", "float", 0.07, "setting", lo=0.01, hi=0.3,
   help="How hard the sound subcarrier deviates the carrier (IARU V.2: 0.07).")
_k("fmInvert", "bool", False, "setting", help="Invert video polarity (sync high). Both ends must agree.")

# --- transmit safety ---------------------------------------------------------------------
_k("txGain", "float", -30.0, "control", lo=-89.75, hi=0.0, unit="dB",
   help="Transmit attenuation in the SDR (0 dB is full output: a few mW).")
_k("txGainCeiling", "float", -10.0, "setting", lo=-89.75, hi=0.0, unit="dB", auth=True,
   help="The most the power control may reach.")
_k("txTimeoutMin", "float", 10.0, "setting", lo=0.5, hi=60.0, unit="min", auth=True,
   help="Transmit stops after this long.")
# The cable feed (DESIGN.md "Cable feed"): 405 lines on Band I, or 625-line
# PAL on UHF, into a set's aerial socket by cable; neither is an amateur
# allocation. Never
# persisted (persist=False): every start of the program begins with it off,
# so a radio left cabled to a television one evening cannot carry the setting
# into the next session, when an antenna may be on the socket. Turning it on
# needs cableFeedAck, the sentence about the cable, ticked in the same
# Settings session (state.check_combined); both are `auth` keys, so only the
# Settings dialog changes them.
_k("cableFeedAck", "bool", False, "setting", auth=True, persist=False,
   help="The output goes by cable, through attenuation, into a receiver's aerial socket - never to an antenna.")
_k("cableFeed", "bool", False, "setting", auth=True, persist=False,
   help="Feed a television by cable: 405 lines (System A) on the Band I channels B1-B5 for a vintage 405-line "
        "set, or 625-line PAL (System I, teletext allowed) on the UHF channels E21-E48 for a 625-line set, at "
        "the radio's least output, and nothing else may be transmitted while it is on. Neither band is an "
        "amateur allocation. Off at every start.")
_k("watchdogSec", "float", 2.0, "setting", lo=0.5, hi=10.0, unit="s", auth=True,
   help="Transmit stops if the window stops servicing the engine for this long.")

# --- simulator ------------------------------------------------------------------------------
_k("simCnrDb", "float", 32.0, "setting", lo=0.0, hi=60.0, unit="dB",
   help="Simulated carrier-to-noise ratio over the whole sample band.")
_k("simStations", "bool", True, "setting", help="Simulated stations on the bandplan channels.")
_k("simTxErrHz", "float", -3200.0, "setting", lo=-200_000.0, hi=200_000.0, unit="Hz",
   help="How far off frequency our own looped-back transmission arrives.")
_k("simSeed", "int", 7, "setting", lo=0, hi=1 << 30, help="Noise seed.")
_k("simNicam", "bool", True, "setting",
   help="Simulated PAL stations also send NICAM-728 stereo (1 kHz left, 2 kHz right) where their mode has room.")

# --- audio -------------------------------------------------------------------------------------
_k("audioBackend", "enum", "portaudio", "setting", choices=["portaudio", "null"], restart=True,
   help="Sound card through PortAudio, or none.")
_k("audioOut", "str", "", "setting", restart=True, help="Output device name (empty: system default).")
_k("audioIn", "str", "", "setting", restart=True, help="Microphone device name (empty: system default).")

# --- status ---------------------------------------------------------------------------------------
for name, type_, default, help_ in [
    ("running", "bool", False, "The engine is running."),
    ("keyed", "bool", False, "Transmitting."),
    ("txSeconds", "float", 0.0, "How long this transmission has lasted."),
    ("fs", "float", 0.0, "The sample rate the device is running at."),
    ("rxLock", "str", "searching", "searching | h-lock | locked"),
    ("hLock", "float", 0.0, "Fraction of lines whose sync was found."),
    ("vLock", "bool", False, "Field sync found and the line count held."),
    ("colourLock", "bool", False, "Decoding colour (burst present)."),
    ("rxFormat", "str", "pal", "The picture format the receiver is decoding (videoFormat, or what formatAuto found)."),
    ("snr", "float", None, "Unweighted video SNR, dB, from blanked lines."),
    ("jitterNs", "float", None, "Line-timing jitter, ns."),
    ("carrierHz", "float", None, "Measured carrier offset from where it should be (AM), Hz."),
    ("audioDb", "float", -120.0, "Receive sound level, dBFS."),
    ("nicamLock", "bool", False, "NICAM-728 frames are being received (frame alignment held)."),
    ("nicamBer", "float", None, "NICAM bit-error rate, estimated from the samples' parity checks."),
    ("nicamMode", "str", "", "What the NICAM transmission carries: stereo, mono, dual, data; empty when none."),
    ("nicamActive", "bool", False, "The sound being played is NICAM's (else the analogue sound)."),
    ("fps", "float", 0.0, "Decoded frames per second."),
    ("decodeSkipped", "int", 0, "Pictures not shown to keep up (skipped while behind, or their signal dropped)."),
    ("txRepeats", "int", 0, "Live transmit frames sent twice because the encoder was late."),
    ("cpu", "float", 0.0, "Engine CPU, fraction of one core."),
    ("plan", "dict", {}, "What fits at this sample rate: ohsatv.modem am_plan / fm_plan."),
    ("rxPlan", "dict", {}, "The plan of the format being received (rxFormat): plan, unless formatAuto found another."),
    ("txRefused", "list", [], "Why transmit is refused here, sentences (empty: allowed)."),
    ("txWarnings", "list", [], "Bandplan overlaps to know about, sentences."),
    ("licenceInForce", "dict", {}, "The licence the station profile resolves to (SERVICES.md 2.2 derived): regimeShort, "
                                   "className, canTransmit, why, entity, country, region, bandplanDataVersion."),
    ("bandLicence", "dict", {}, "What the station's licence gives it on this band, from the dataset: spans [[lo, hi] "
                                "Hz...] where its class may transmit, and limit {watts, unit} (the highest there) "
                                "or None. Empty on the cable-feed bands."),
    ("bandOk", "bool", True, "The receiving device can tune this band."),
    ("txBandOk", "bool", True, "The transmitting device can tune this band."),
    ("txBandWhy", "str", "", "Why it cannot."),
    ("profile", "str", "", "What was chosen for this radio and why (ohsatv.profiles), or empty."),
    ("txKind", "str", "", "The transmitting device's kind (sim, pluto, hackrf)."),
    ("txLink", "str", "", "How the host reaches the transmitting device."),
    ("txDeviceStatus", "dict", {}, "The separate transmit device's status, shown verbatim."),
    ("txUnderruns", "int", 0, "Times the separate transmit device ran out of samples (the host fell behind)."),
    ("bandWhy", "str", "", "Why it cannot."),
    ("range", "list", [], "What the device tunes, [lo, hi] Hz."),
    ("link", "str", "", "How the host reaches the radio."),
    ("linkMsps", "float", None, "What that link is reported to sustain, MS/s."),
    ("duplex", "bool", False, "Receive continues while transmitting."),
    ("deviceStatus", "dict", {}, "Device status, shown verbatim."),
    ("radioMoved", "list", [], "What the radio's rate or LOs last moved to by themselves: "
                               "[{what, from, to, held, text, t}]."),
    ("radioLock", "dict", {}, "The radio lock: {held: [{radio, label, use, since, where, notes}], refused: the "
                              "sentence if a radio was busy, holder: {app, host, use, since, note, ...} of whoever "
                              "has it, lost: the sentence if a lock was lost}; empty before a start."),
    ("radioChanged", "str", "", "Why the next key-up is refused because something else changed the transmitting "
                                "radio since this program set it (empty: nothing found); accept_radio() clears it."),
    ("audio", "dict", {}, "Audio status."),
    ("sourceStatus", "str", "", "The video source's state or the sentence saying why it is not running."),
    ("sourceError", "str", "", "Why the video source is not delivering pictures; empty while it is."),
    ("identRect", "list", [], "Where the ident is in the transmitted picture: [x0, y0, x1, y1], fractions of it."),
    ("ttxStatus", "str", "", "What teletext is being sent, or would be at key-up."),
    ("ttxRefused", "str", "", "Why teletext, switched on, is not being sent (empty: it is, or it is off)."),
    ("ttxSub", "str", "", "The subtitle on air now (page 888), as text; empty when none."),
    ("filePos", "float", None, "The video file source's playback position, seconds."),
    ("fileLen", "float", None, "The video file's length, seconds (None if unknown)."),
    ("filePaused", "bool", False, "The video file source is paused (its picture held)."),
    ("ttxRx", "dict", {}, "Teletext received: {packets, pages, rate (packets/s), initial, status}."),
    ("recording", "bool", False, "Recording IQ."),
    ("recFile", "str", "", "The recording being (or last) written, its .sigmf-meta path."),
    ("recWritten", "float", 0.0, "How much of it is written, seconds."),
    ("recLost", "int", 0, "Samples the recorder lost because the disk fell behind (marked in the file)."),
    ("recWhy", "str", "", "Why the last recording stopped."),
    ("error", "str", "", "Why the engine is not running, if it is not."),
]:
    _k(name, type_, default, "status", help=help_)


def defaults(kinds=("setting", "control", "status")) -> dict:
    return {k: (type(v["default"])(v["default"]) if isinstance(v["default"], (dict, list)) else v["default"])
            for k, v in SPEC.items() if v["kind"] in kinds}


def persisted() -> list[str]:
    return [k for k, v in SPEC.items() if v["persist"]]


def coerce(key: str, value):
    """Validate one value against SPEC; return it in canonical type or raise
    ValueError with a sentence an operator can read."""
    spec = SPEC.get(key)
    if spec is None:
        raise ValueError(f"There is no setting called {key!r}")
    t = spec["type"]
    try:
        if t == "bool":
            if isinstance(value, str):
                if value.lower() in ("1", "true", "on", "yes"):
                    value = True
                elif value.lower() in ("0", "false", "off", "no"):
                    value = False
                else:
                    raise ValueError
            value = bool(value)
        elif t == "int":
            if isinstance(value, bool) or float(value) != int(float(value)):
                raise ValueError
            value = int(float(value))
        elif t == "float":
            if value is None and spec["default"] is None:
                return None
            if isinstance(value, bool):
                raise ValueError
            value = float(value)
            if not math.isfinite(value):
                raise ValueError
        elif t == "str":
            if not isinstance(value, str) or "\n" in value or len(value) > 200:
                raise ValueError
        elif t == "enum":
            if value not in spec["choices"]:
                matches = [c for c in spec["choices"] if str(c) == str(value) or
                           (isinstance(c, (int, float)) and _num(value) == c)]
                if not matches:
                    raise ValueError(f"{key} must be one of {', '.join(map(str, spec['choices']))}")
                value = matches[0]
        elif t == "dict":
            if not isinstance(value, dict):
                raise ValueError
        elif t == "list":
            if not isinstance(value, (list, tuple)):
                raise ValueError
            value = list(value)
    except ValueError as e:
        if str(e):
            raise
        raise ValueError(f"{key} must be a {t}, not {value!r}") from None
    if t in ("int", "float") and value is not None:
        lo, hi = spec["min"], spec["max"]
        unit = (" " + spec["unit"]) if spec["unit"] else ""
        if lo is not None and value < lo:
            raise ValueError(f"{key} cannot be below {lo:g}{unit}")
        if hi is not None and value > hi:
            raise ValueError(f"{key} cannot be above {hi:g}{unit}")
    return value


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def check_value(key: str, value) -> None:
    """Rules for one key beyond its type, checked when it is set (not at
    load: a stored value that no longer passes is replaced where it is
    used, so it cannot lock every other change out). Raises ValueError."""
    from . import ttx, vbi
    if key == "stationConditions" and not all(isinstance(k, str) and isinstance(v, bool) for k, v in value.items()):
        raise ValueError("stationConditions: each answer is a yes or no under the licence's own key")
    if key == "ttxLines":
        vbi.parse_lines(value)                      # raises with its own sentence
    elif key == "ttxInitial" and ttx.parse_label(value) is None:
        raise ValueError(f"Initial page {value!r}: a page number like 100 (magazine 1-8, then two hex digits)")


def check_combined(st: dict) -> None:
    """Rules that span several keys. Raises ValueError."""
    if st["txGain"] > st["txGainCeiling"]:
        raise ValueError(f"Transmit gain cannot exceed the ceiling of {st['txGainCeiling']:g} dB set in Settings")
    if st.get("cableFeed") and not st.get("cableFeedAck"):
        raise ValueError("Tick the confirmation first: the output goes by cable, through attenuation, into a "
                         "receiver's aerial socket - never to an antenna")
    if st["band"] in bands.CABLE_NAMES and not st.get("cableFeed"):
        raise ValueError(f"{st['band']} is for the cable feed only: turn it on in Settings › Transmit")
    b = bands.BY_NAME[st["band"]]
    for key in ("rxFreq", "txFreq"):
        if not (b.lo <= st[key] <= b.hi):
            raise ValueError(f"{st[key] / 1e6:.3f} MHz is outside {b.name} ({b.lo / 1e6:g}-{b.hi / 1e6:g} MHz)")
