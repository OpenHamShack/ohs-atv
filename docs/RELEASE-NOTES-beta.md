# OpenHamShack beta: what changes for ATV

The beta's notes for OpenHamShack ATV, kept as the work lands (M3) so the
release can quote them. Compared with ATV 0.2.3 (8 October 2026).

## Transmit permission comes from the station profile and the shared dataset

ATV now asks the shared OpenHamShack band-plan and licence dataset (ohs-libs
`shack_bandplan`, dataset **2026.10.3**, pinned in
`tools/vendor/requirements.lock`) what the station's licence allows, with
the station profile built from ATV's settings: callsign, station country,
licence, licence class and the licence's yes/no conditions
(`ohsatv/txguard.py`). ATV's own tables of UK allocation blocks and the
band card's UK power sentences are gone.

What stays ATV's own (`ohsatv/bands.py`): the bands it works (70cm, 23cm,
6cm), their channels, and the RSGB plan's ATV reading - ATV segments, the
UK's TV repeater outputs (receive only), and the overlaps the UK's own
simplex channels make on purpose (warnings). The dataset's RSGB 2026
transcription carries no ATV rows on 23cm and keeps everyone off 5650-5668
MHz, which would refuse 5665 MHz, the UK's ATV frequency; so for a station
whose plan is the RSGB's, the dataset judges the licence and ATV judges the
plan, as before. The cable feed (Band I, UHF) is not amateur and is judged
as before (`cable_check`), never by the dataset.

For a UK Full licensee the answers are 0.2.3's (`tests/test_bands.py`
proves it, and that the dataset's UK allocation is 0.2.3's 430-440,
1240-1325, 5650-5680, 5755-5765 and 5820-5850 MHz). These answers change:

| Where | 0.2.3 | Now | Why |
|---|---|---|---|
| Any station without a licence class | transmitted (licence acknowledgement and callsign were enough) | a real radio is refused: "No licence class chosen: transmit stays locked until you choose yours in the station profile" (the simulator shows it as "on a real radio this would be refused") | SERVICES.md 2.1: an empty class locks transmit; Radio's rule since 05/10/2026 |
| A station outside the UK (from the callsign, or Settings › Station › Country) | the UK's allocations and the RSGB plan | its own country's licence from the dataset, and its region's plan from the dataset (IARU R2 in the US, IARU R1 elsewhere in Europe), whose reserved segments refuse and whose other rows warn | BETA-PLAN architecture change 5 |
| A country the dataset has no licence for (Japan, for example) | the UK's rules | refused: "No licence for Japan is written into OpenHamShack yet: …" | partial data refuses, never guessed |
| US station, 6cm 5705 MHz | refused (outside the UK blocks) | allowed (FCC 5650-5925 MHz), with the "not yet second-checked" note | the FCC's allocation |
| US station, 70cm 433 MHz AM-VSB | allowed with the RSGB / IARU R1 warning | refused: the signal reaches the IARU R2 plan's beacons (432.3-432.4 MHz); no "OUTSIDE THE BANDPLAN" (that is Region 1's) | the dataset's R2 plan |
| German station, 23cm FM-ATV | allowed (UK rules) | refused: the dataset's BNetzA emission table lists no ATV on 1240-1300 MHz | the dataset as written: a data question for ohs-libs, not an ATV rule |
| UK Foundation, 23cm | allowed | refused: "Foundation licence: 1240.0-1260.0 MHz is not available" | Ofcom: 23cm is not a Foundation band |
| UK Foundation, 6cm 5665 MHz | allowed | allowed; the band card says 2 W PEP | |
| UK station within 100 km of Charing Cross (Settings › Station, ticked) | 431-432 MHz was a warning line | refused wherever the signal touches 431-432 MHz (70cm AM-VSB at 433 MHz does), even on 70cm | a licence condition; Brian's 70cm leniency is for the allocation's edge only |
| Signal past the allocation's edge on 23cm or 6cm | "… outside the UK allocation 1240-1325 MHz …" | "… outside the allocation 1240-1325 MHz (UK (Ofcom), Full) …", same carrier-fit hint | the licence named is the station's |
| Carrier outside every block (6cm 5705 MHz in the UK) | "… outside the UK 6cm allocation (…)" | "5705 MHz is outside the 6cm allocation (5650-5680, 5755-5765, 5820-5850 MHz; UK (Ofcom), Full)" | as above |
| Other licence refusals | none | the dataset's sentences, which point at "the station profile" | shared wording across the family |
| The band card | UK blocks in the title; Power: the UK's Full / Intermediate / Foundation sentence | the station's own allocation in the title ("not in your licence" when none); Power: the class's highest limit there from the dataset | |
| The spectrum strip | hatched outside the UK blocks | hatched outside the station's licence | |
| shack-bandplan missing from an installation | n/a | every amateur transmission refused, saying so | fail closed |

New warnings that never refuse: a regime other than the UK's carries the
dataset's note that its data has had one research pass, not a line-by-line
check; another region's plan says when ATV is not what a segment is for.

Unchanged on purpose:

- **70cm: a signal reaching past the allocation (430-440 MHz in the UK) is
  an "OUTSIDE THE ALLOCATION" warning, not a refusal** (Brian, 8 October
  2026: most 70cm ATV goes down a cable into an attenuator), and the
  "OUTSIDE THE BANDPLAN" warning stays first and is logged with every
  70cm transmission. 435.0 MHz stays a 70cm channel. 23cm and 6cm still
  refuse past the edge.
- The simulator still turns every refusal into a warning ("on a real radio
  this would be refused: …") and transmits into itself.
- The cable feed's guard, channels and minimum power.

New settings (Settings › Station › Licence, all `auth`): `stationCountry`
(a DXCC prefix; empty follows the callsign), `licence` (automatic or one
of the dataset's licences), `licenceClass` (no default), and
`stationConditions` (the licence's yes/no questions). New status keys:
`licenceInForce`, `bandLicence` (docs/ENGINE-API.md).

Transmit paths were tested only into the simulator and its "real radio"
stand-in: nothing in this work keyed a radio.

## 0.2.4-beta.1 (store) and 0.2.4 (desktop), 10 October 2026

The desktop forms move to 0.2.4 because the installers published as 0.2.3
were made before this beta's work; the store package moves with them
(after 0.2.3-beta.1). What changed since that store package:

- **The microphone is opened only while it is the sound source.** ATV
  used to open an input stream at every start; on macOS that is what asks
  for microphone permission, and the prompt held the first window back.
- **The radio lock works on macOS.** It lives in `/private/tmp`, the Mac's
  shared sticky folder, instead of `/run/lock`, which a Mac does not have.
- **A macOS build:** OpenHamShack ATV.app in a signed, notarised disk image.
- **ohs-libs 326d251:** the shared station store gives back a finished
  thread's database connection (ATV never touches it from request threads,
  so it was not leaking here).

Transmit paths were tested only into the simulator: nothing keyed a radio.

## 0.2.5-beta.1 (store) and 0.2.5 (desktop), 10 October 2026

What changed since 0.2.4-beta.1, all in the store form:

- **A platform update no longer stops the radios for good.** The broker
  keeps claims in memory, so a controller restart lost ATV's claims on the
  Pluto and the HackRF, and the store form treated that as the controller
  taking them back. Lost claims are now re-claimed for receive with
  backoff; only a real release request stops the radio (8454fec).
- **Gone listeners are dropped.** Receive audio sends silence while idle, so
  a client that has gone is noticed and its listener and thread freed
  (3614ada).

The desktop forms carry no change of their own; they move to 0.2.5 with the
store label because the nightly rebuilds them from main under whatever
VERSION says, and 0.2.4's name is already built.

Transmit paths were tested only into the simulator: nothing keyed a radio.
