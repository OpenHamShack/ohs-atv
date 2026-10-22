# Teletext: the first real transmissions — a bench checklist

For GM4BRI's first teletext on a real radio. Everything so far is
simulator-only (HANDOVER.md "Teletext"); this is the order to try it in,
what should be seen at each step, and what to send back when it fails.
Tick as you go. Numbers: docs/teletext-eval.md. Design: DESIGN.md
"Teletext" and "Cable feed".

## A. Before you start

- [ ] **Radios.** Station A transmits, station B receives, each with its
  own OpenHamShack ATV - ideally on two hosts (a shared laptop dropped 0.88 s
  of receive samples during the HackRF tests). A *USB* Pluto cannot send
  teletext (5 MS/s: refused); it can *receive* it at 10 MS/s AM with 8-bit
  receive. Good pairs:
  - LibreSDR (Ethernet) transmits, nano (8-bit RX) receives: **AM-VSB 10
    MS/s** at both ends.
  - HackRF transmits **FM 20 MS/s, 7 MHz p-p (IARU V.2)**; the receiver
    must run 20 MS/s too, so an Ethernet radio that can (not the
    LibreSDR at 16, not a USB Pluto).
- [ ] **Profile and mode.** Let the auto profile choose, then check the
  plan card. Teletext needs >= 3.75 MHz of video:

  | mode (both ends) | carries | PER <= 1 % from (CNR) |
  | --- | --- | --- |
  | FM 20 MS/s, 7 MHz p-p | yes | 10 dB |
  | FM 16 MS/s, 4 MHz p-p (LibreSDR profile) | yes | 16 dB |
  | AM 10 / 12.5 / 16 MS/s | yes | 24 / 23 / 23 dB |
  | AM 8 MS/s | yes, **with the note** | 25-26 dB |
  | FM 12.5 MS/s, 1.2 MHz p-p (8-bit RX) | yes | 26 dB |
  | AM or FM at 5-6.25 MS/s, FM 10 MS/s 3 MHz p-p, Low-PAL, Mini-MAC | **no** (amber sentence, nothing inserted) | - |

  Teletext wants a decoder SNR of 22-25 dB in every mode - far more than
  a watchable picture.
- [ ] **Band and licence** (DESIGN.md "Bands", docs/atv-research.md 1-2):
  23cm is the band (1255 FM simplex; AM on 1249 or 1275). 70cm has no
  analogue ATV in the bandplan: allowed, warned, logged - avoid it for a
  first test. Full licence, 400 W PEP; the callsign is in vision and in
  the teletext header, which is the ident rule for both.
- [ ] **Power.** Settings › Transmit › *Let a real radio transmit* on, the
  ceiling where you want it; start at the least gain and work up.
- [ ] **A second receiver or a TV** for part D and E: a 625-line set with
  teletext (UHF, for the cable feed) or the second OpenHamShack.
- [ ] **The service.** In OpenHamShack Teletext: `python3 -m ohstxt` (first run
  writes the sample service to `~/.local/share/openhamshack-teletext/service/`),
  then `python3 -m ohstxt.check ~/.local/share/openhamshack-teletext/service
  --callsign GM4BRI` - no errors. (`python3 -m ohsatv.ttxsample DIR` is our
  own 4-page sample if the editor is not to hand.)

## B. On the bench, simulator first (nothing radiated)

- [ ] Station A, Radio *Simulator*, AM-VSB 10 MS/s on 1249. Transmit panel
  › Text on, folder = the service, initial page 100. Under the switch:
  "Sending N pages on 32 lines (800 packets/s), opening on 100".
- [ ] Key. Waveform monitor on **line 12**: the run-in burst then data
  at ~0.46 V from about 10 us. A flat blanked line means nothing is
  inserted - read the amber sentence.
- [ ] Picture card › Text (or T): P100 with GM4BRI and the clock in the
  header, Fastext keys working, 102 cycling subpages, 103 the test page
  (Reveal). Packets/s near 800.
- [ ] Repeat with the profile you will use on air (rate and system).

## C. Over a cable, two radios (nothing radiated)

- [ ] A's output -> 30-40 dB attenuator -> B's input (dummy load if A has
  a PA). Same frequency, system and rate on both.
- [ ] Key A at its least gain; raise until B is locked. Note B's SNR
  badge: aim for 30 dB; teletext should be clean from the table's CNR.
- [ ] On B: Text, P100 arrives within a few seconds; packets/s ~800.
  Step A's gain down 2 dB at a time and note where pages stop filling
  (expect a decoder SNR of 22-25 dB).
- [ ] **Live pages**: `python3 -m ohstxt.live <service>` on A while keyed.
  A rewritten page should reach B within a carousel turn (1.3 s on the
  simulator); no restart, no gap in the others.
- [ ] **Subtitles on 888**: type a caption on A's transmit panel (Enter),
  or choose an SRT with a video file source; on B press 888 (or S) - the
  boxed lines over the picture.
- [ ] **Save received pages** on B: the viewer's save button - this page,
  or everything as P<mpp>.tti plus a .t42 of the last ~50 s. Open them
  with `python3 -m ohstxt <folder>` or `python3 -m ohstxt.receive
  <capture>.t42`.

## D. Over the air

- [ ] Antennas placed, the frequency on the band card, the guard silent
  (no red sentence under PTT). Same checks as C, from B's SNR.
- [ ] **AM 8 MS/s**: the video is cut at 3.84 MHz (the standard's
  teletext runs to 5). Our slicer takes it; a television's decoder may
  not. If a TV is the receiver, use 10 MS/s or more.
- [ ] A real TV, if you can receive 23cm into one (an ATV converter or
  receiver into its aerial socket): press TEXT, then 100 and 888.

## E. Cable feed on UHF into a 625-line set (nothing radiated)

DESIGN.md "Cable feed". By cable only, through attenuation, never an
antenna.

- [ ] Settings › Transmit › Cable feed: tick the sentence, then *Cable
  feed on*, Save. Choose **UHF** in the band list: E36, 591.25 MHz, AM
  625-line PAL. Rate **10 MS/s or more** (5 MS/s: teletext refused; 8
  MS/s puts the sound at 0.45 fs, where research 21 found images on air).
  Text on as in B. The red CABLE FEED banner shows; there is no power
  control.
- [ ] **Measure the least output first.** A's output -> attenuator -> B
  (B also turns its cable feed on, to tune UHF, and never keys). Note B's
  SNR at E36. The estimate is that a Pluto's least gives a TV only 10-16
  dB CNR - a snowy picture, no teletext; a HackRF at VGA 0 tens of dB
  more.
- [ ] Attenuator: a Pluto little or none (start at 6 dB); a HackRF 30 dB.
- [ ] Tune a spare button on the set to E36 (often the VCR button). Key:
  picture, 1 kHz tone, then TEXT -> P100 with GM4BRI. Then 888 with a
  typed caption.
- [ ] Try E21 and E48 too (any of E21-E48 except E38 can be typed).
- [ ] **If the picture is snowy and text does not come**, stop: it is the
  power rule, not the set. Send B's SNR reading and the set's picture;
  raising the cable feed's level is Brian's decision.
- [ ] 405 lines on Band I into a 405-line set: the same feed, Band I, B2
  51.75 MHz on a Tezuka Pluto or any of B1-B5 on a HackRF. No teletext
  there (refused).

## F. When it fails: what to capture and send back

- [ ] **On B** (the receiver), while it is failing:
  - Record IQ (receive band header) for 10-20 s - the
    `.sigmf-meta` + `.sigmf-data` pair from
    `~/.local/share/openhamshack-atv/recordings/` (10 MS/s 16-bit is 40 MB/s).
  - The viewer's *Save all received*: the `.tti` folder and the `.t42`.
  - A still (Ctrl+S) with the waveform monitor on line 12.
- [ ] **On A** (the transmitter): `~/.local/share/openhamshack-atv/txlog.jsonl`
  (its last lines), `~/.config/openhamshack-atv/config.json`, and the service
  folder as sent (or `ohstxt.check` output on it).
- [ ] Write down: radios and firmware, rate, system, deviation, frequency,
  attenuation or path, A's gain, B's SNR badge, packets/s, what the TV
  did. Send the files and notes back to the session that is debugging it.
