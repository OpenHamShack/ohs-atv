# OpenHamShack ATV engine API

For whoever builds the Qt UI. The UI talks to the engine through one object,
`ohsatv.radio.Radio`, and reads limits and help text from `ohsatv.state.SPEC`;
it never touches numpy DSP or the device. The engine never imports Qt.

Everything here is exercised by `tests/test_radio.py`; if this document and
the tests disagree, the tests are right and this document has a bug.

## Frequencies and the picture

All frequencies are Hz (Python floats). `rxFreq` / `txFreq` are the **vision
carrier** for AM-VSB and the **carrier** for FM — what ATV operators quote.
With `split` off, `txFreq` follows `rxFreq`.

The device's LO is not necessarily at that frequency: for AM-VSB it sits
`-plan["carrier"]` (≈ 2.45 MHz) above the vision carrier so vestige, picture
and sound carrier fit the complex band. The spectrum feed is in true RF Hz,
so the UI never needs to know this — except to draw markers, for which it
uses `plan` (below).

Pictures are `uint8` arrays, 576 rows × W columns × 3 (RGB), where W is the
decoder's own sampling of the 52 µs line (520 at 10 MS/s, 832 at 16, 1040 at
20). **Show them at 4:3** (scale to 768 × 576 or any 4:3 size; QImage does
it in C). Transmit-preview pictures are float32 0..1, 576 × 720 × 3, also 4:3.

## Lifecycle and threading

```python
from ohsatv.radio import Radio, TxRefused
radio = Radio()                    # loads $OHSATV_CONF / ~/.config/openhamshack-atv/config.json; opens nothing
feeds = {k: radio.subscribe(k, 8) for k in ("video", "txvideo", "spectrum", "txspectrum", "scope", "meter", "state",
                                            "toast")}
radio.start()                      # opens device, audio, source; starts receive. Never transmits.
...                                # a QTimer at ~30 Hz: radio.heartbeat(); drain every feed
radio.stop()                       # unkeys, stops threads, closes, saves
```

- Every method may be called from the Qt main thread; all are thread-safe
  and return quickly — except `ptt(True)` for a static source the first time
  after a change, which builds the 0.16 s transmit loop (≈0.5–1 s; the
  keyed state is set before it returns, the loop is built on the tx thread).
- **Watchdog**: if neither `heartbeat()` nor any feed's `drain()`/`get()` is
  called for `watchdogSec` (default 2 s) while transmitting, the engine
  unkeys and posts a toast. Call `heartbeat()` from the timer that drains the
  feeds.
- A `restart` key changed through `set()` (sample rates, `system`, device,
  audio devices) makes the engine restart itself on a background thread
  (stop + start); this never keys. A toast says so.
- `start()` raises `ohsatv.devices.DeviceError` with an operator-readable
  sentence if the device cannot be opened; `state()["error"]` keeps it. A
  radio another program holds raises its subclass
  `ohsatv.devices.shared.RadioBusy` (below, "Shared radios").

## Radio methods

| call | does | raises |
| --- | --- | --- |
| `Radio(cfg=None, *, conf_path=None, data_dir=None, audio=None, device=None, tx_device=None, persist=True)` | `cfg` a dict of state keys (else the config file); `audio`/`device`/`tx_device` inject a backend, the receiving device, the separate transmit device (tests, screenshots) | |
| `start()` / `stop()` | as above | `DeviceError` |
| `set(authorise=False, **changes) -> dict` | validate every change against `state.SPEC` plus cross-key rules, apply all or none, return what changed. `auth` keys need `authorise=True` (Settings dialog only). Changing `band` moves both frequencies to the band's default channel and `system` to the band's first, unless given in the same call. Lowering `txGainCeiling` lowers `txGain` with it. | `ValueError(sentence)` |
| `state() -> dict` | snapshot of every key (settings, controls, status) | |
| `plan() -> dict` | the current system's plan at the current rate (also in `state()["plan"]`) | |
| `ptt(on: bool)` | key / unkey. Keying checks the guard first. | `TxRefused(sentence)` (a `ValueError`) |
| `subscribe(kind, maxlen=32) -> Feed` / `unsubscribe(feed)` | feeds, below | `ValueError` |
| `heartbeat()` | the watchdog's "UI is alive" | |
| `record_iq(on, seconds=None) -> str` | start / stop recording the receiving radio's raw IQ as SigMF (`ohsatv/iqrec.py`); returns the `.sigmf-meta` path. Native format: `ci8` (Tezuka 8-bit), `ci16_le` (Pluto), `cf32_le` (simulator). Stops by itself at `recSeconds` / `recMaxMB`, or if the disk nearly fills; status `recording`, `recFile`, `recWritten` (s), `recLost`, `recWhy`, and a toast when saved | `ValueError(sentence)`: not running, or no room on the disk |
| `accept_radio()` | the operator accepts the transmitting radio as it is now, after a key-up was refused because something else had changed it (`radioChanged`); clears it | |
| `tx_log() -> list[dict]` | the transmit log (`$OHSATV_DATA/txlog.jsonl`), oldest first: `{"t", "event": "key", freq, band, system, fs, format, device, callsign, source, gainDb, cableFeed, warnings}` and `{"t", "event": "unkey", why, seconds}` (`format`: the picture format sent, `pal` / `lowpal` / `mac` / `405`; `gainDb`: the gain actually used, -89.75 with the cable feed on whatever `txGain` says; `cableFeed`: true or false) | |

Playing a recording: setting `device` = `"file"` and `playFile` = its `.sigmf-meta` (or `python3 -m ohsatv --play FILE`). At `start()` the engine takes the recording's rate, system, mode parameters and tuning from its metadata; the samples then go through the same receive chain in real time (`playLoop` repeats them). It never transmits (`txRefused` says "This radio cannot transmit").

Helpers the UI needs:

- `ohsatv.state.SPEC[key]` — type, default, min, max, choices, unit, help, kind, persist, auth, restart.
- `ohsatv.bands.BANDS` / `BY_NAME` — per band: `lo`, `hi`, `segments` (`lo, hi, label, kind`: kind `atv`/`all` = a carrier may go there, `rx` = repeater outputs, receive only, `narrow`/`sat` = protected, `info`), `channels` (`freq, label, tx`), `default`, `modes`, `analogue_tx`, `status`, `rf_note`, `notes` (`power` and `blocks` only on the cable-feed bands). Draw the bandplan strip and the channel list from these; what the station's licence allows there is the `bandLicence` status key.
- `ohsatv.bands.reachable(band, range)` and `tx_check(freq, lo_off, hi_off, station, system)` (`station`: `ohsatv.txguard.profile(state)`) — the engine already publishes their results as status keys.
- `ohsatv.txguard.GUARD` — the shared dataset for Settings: `regimes()`, `classes(profile)` (the licence in force's), `conditions(profile)`, `in_force(profile)`.
- `ohsatv.devices.pluto.discover(timeout_s=1.0, notes=[]) -> [(uri, description)]` — Settings' "Find" button (slow: run it on a worker thread).
- `ohsatv.audio.list_devices()` — sound devices for Settings.
- `ohsatv.modem.am_plan(fs, ...)` / `fm_plan(fs, ...)` — to show "what would fit" for each rate in Settings before choosing it.
- `ohsatv.narrow` — the picture formats: `OFFERED` (`["pal", "lowpal", "mac", "405"]`, the `videoFormat` choices), `SYSTEMS` (which system each belongs to: the narrow ones FM, `405` AM), `LABELS` / `SHORT` (names), `is_narrow(fmt)` and `NOT_STANDARD` (the sentence to show with a narrow format - never with `405`, which is a standard), `NARROW_MAX_FS` (the rate at and below which to suggest the narrow ones), `plan(fs, system, params)` (the plan with the format applied, as `state()["plan"]`).
- `ohsatv.bands.CABLE` — Band I for the cable feed (`name` "Band I", `channels` B1-B5, `segments` each channel's 5 MHz as `info`, no `blocks`: none of it is an amateur allocation); `CABLE_CHANNELS` `(name, vision Hz, sound Hz)`; `CABLE_UHF` the same for UHF (`name` "UHF", 470-694 MHz, `channels` the eight shown as chips, `segments` all 27 channels' 8 MHz), `UHF_CHANNELS` E21-E48 except E38; `CABLE_BANDS`, `CABLE_NAMES`; `cable_channel(freq)` either list; `cable_check(...)` the cable feed's guard. Not in `BANDS`, which stays the amateur bands; in `BY_NAME`.

## State keys the UI shows (status, read-only)

| key | meaning |
| --- | --- |
| `running`, `error` | engine running; why not / what is wrong (e.g. the band is out of the radio's range) |
| `keyed`, `txSeconds` | transmitting, for how long |
| `fs` | the device's sample rate now |
| `rxLock` | `searching` / `h-lock` / `locked` |
| `hLock` | fraction of lines with sync found (0..1) |
| `vLock`, `colourLock` | field sync held; colour being decoded |
| `rxFormat` | the picture format the receiver is decoding: `videoFormat`, or what `formatAuto` found on the air (then say so, e.g. "Receiving Mini-MAC — found on the air") |
| `snr` | unweighted video SNR in dB (None until measured) |
| `jitterNs` | line-timing jitter, ns |
| `carrierHz` | AM: measured vision-carrier offset from where it should be |
| `audioDb` | receive sound level dBFS |
| `fps`, `decodeSkipped`, `cpu` | decoded pictures/s; pictures not shown to keep up (the decoder skips building a picture while more signal is waiting - timing and Mini-MAC's sound are still decoded - and drops a block of signal only if even that cannot keep up); engine CPU as a fraction of one core |
| `plan` | `{carrier (AM), video_bw, sound (AM) / sub (FM), colour, fits, why: [sentences], occupied: [lo, hi] Hz from the carrier, width}` — **show `why`**: it is the honest statement of what this rate gives up. With a narrow format also `format` (`lowpal` / `mac`) and `sound` (`"subcarrier"` / `"digital"`); its `why` ends with `ohsatv.narrow.NOT_STANDARD` — show that prominently, once. With 405 lines `format` is `405`, `sound` is Hz from the LO and *below* the carrier (AM sound 3.5 MHz under vision), `occupied` is about [-3.55, +0.75] MHz, `colour` is always false (System A was monochrome: say so, not as something given up), plus `videoRate` (the decoder's rate: twice `fs` below 7.5 MS/s) and `edgeDb` (the radios' nominal loss at the sound carrier) |
| `rxPlan` | the plan of what is being received: the same as `plan`, except while `formatAuto` has found another format on the air (`rxFormat` ≠ `videoFormat`) — then that format's plan. **Describe the received signal with it** (the plan card while not keyed, the spectrum's receive markers); whether PTT is allowed stays `plan`'s `fits`. Back to `plan` when stopped |
| `txRefused` | sentences: why PTT would be refused now (empty = allowed). Show the first next to the PTT button, all on hover |
| `profile` | what was chosen automatically for this radio combination and why (ohsatv/profiles.py), or empty — show it with the plan |
| `txWarnings` | sentences: bandplan overlaps. One starting `OUTSIDE THE BANDPLAN` (70cm) must be shown prominently, not as one more line; the engine logs it with each transmission. Others: (satellite, Galileo, narrowband); on the simulator also "on a real radio this would be refused: …" |
| `licenceInForce` | the licence the station profile resolves to (SERVICES.md 2.2 `derived`): `regimeShort`, `className`, `canTransmit`, `why` (the dataset's whole sentence when not), `entity`, `country`, `region`, `bandplanDataVersion`. Settings › Station shows it |
| `bandLicence` | what the station's licence gives it on this band: `spans` `[[lo, hi] Hz…]` its class may use, `limit` `{watts, unit}` (the highest there) or null; `{}` on the cable-feed bands. The band card's title and Power line and the strip's hatching come from it |
| `bandOk`, `bandWhy` | whether the radio can tune this band, and the sentence if not (grey the band out and show it) |
| `range` | `[lo, hi]` Hz the device tunes (empty if unknown) |
| `link`, `linkMsps`, `duplex` | `usb` / `usb-net` / `ethernet` / `sim`; the rate that link is reported to sustain; whether RX continues while keyed |
| `txKind`, `txLink` | the transmitting device: `sim`, `pluto`, or `hackrf` when a HackRF transmits beside the receiver (`txDevice = "hackrf"`) — say so, e.g. "TX HackRF" |
| `txBandOk`, `txBandWhy` | whether the transmitting device can tune this band (a HackRF: 1 MHz–6 GHz) |
| `txUnderruns`, `txDeviceStatus` | the separate transmitter's underrun count (the host fell behind: zeros were sent, the far end sees a break) and its status dict (`txUnderrunMs`, `txRate`, `vga`, `amp`, `serial`). **Show underruns in red while transmitting; never hide them** |
| `deviceStatus` | dict from the device (Pluto: model, phyModel, fw, rxRate, rxShort, rxBits, rx8, txLate, txLinkMsps, notes…) — show verbatim in a details panel; `rxShort` True means samples are being dropped; `rxDropped` / `rxSkips`: samples the engine never got because it fell behind the radio, and how many times whole frames were skipped to catch up - shown on the plan card for 10 s after each; `moved` / `moves`: what the radio last moved by itself (as `radioMoved`, below) and how many moves since open; `rxBits` 8 means Tezuka's packed receive is in use (`rx8`: the radio has it; `rx8Checks`: how many checks it took at open, 2 when the first read plain 16-bit - the notes say which way it went), and `linkMsps` is then the 8-bit receive figure (15) while `txLinkMsps` is what it can transmit (5.2 on USB) — keying above that is refused |
| `radioMoved` | what the radio changed by itself, as last found: a list, one dict per value, `{"what": "sampleRate" / "rxLo" / "txLo", "from": Hz it was set to, "to": Hz it was found at, "held": True / False / None, "text": the sentence, "t": when}`; empty after `start()` until something moves. A Pluto is asked once a second what it holds (the rate, the RX LO, and the TX LO while keyed). A moved rate or RX LO is set back once: `held` True if that took (receive carries on), False if not (the radio is not doing what the plan says; while keyed the engine unkeys). A moved TX LO while keyed means transmitting off frequency: the engine unkeys at once without setting it back (`held` None). Every such unkey is in the transmit log with `text` as its `why`, and a toast is posted when the moves differ from the last ones. **Show `text` in red** - for a while after `t`, and for as long as any `held` is False. Seen on air: the Pluto nano (Tezuka) at 2.330997 MS/s and 1254.666998 MHz while the program had set 10 MS/s and 1247 MHz (docs/atv-research.md 17a) |
| `radioLock` | the radio lock (below): `{"held": [{"radio", "label", "use", "since", "where", "notes"}], "refused": sentence, "holder": {"app", "version", "host", "user", "use", "since", "note", "where", "ageS"}, "lost": sentence}`; `{}` before a start. `held`: one entry per radio this program holds (the receiver, and a separate HackRF); `use` `rx` / `tx`; `where` the places its marker is (`"this computer"`, and the radio's shell when it answered); `notes` include "The lock on … covers this computer only: …" when the radio's shell did not answer - **show that for as long as it is so**. `refused`: the spec's sentence when a radio was busy (also `error`, the engine not running; or, for a busy HackRF, in `txRefused` with receive running) - **show it whole: it names who has the radio**; `holder` the busy radio's marker, as fields. `lost`: the sentence when a held lock stopped being ours (the engine has unkeyed and stopped; also `error`) |
| `radioChanged` | why the next key-up is refused because something other than this program changed the transmitting radio since it last set it (`""`: nothing found); also in `txRefused` while not keyed. Offer `accept_radio()` beside it |
| `sourceStatus` | the source's state or why it is not running ("No webcam at /dev/video0", "ffmpeg stopped: …") |
| `sourceError` | why the source is not delivering pictures, or empty while it is: warn on this, not on the wording of `sourceStatus` |
| `nicamLock`, `nicamMode` | NICAM-728 frames received (four alignment words in a row found, kept until eight in a row are bad); what they carry: `stereo`, `mono`, `dual`, `data`, or empty |
| `nicamBer` | NICAM bit-error rate estimated from its samples' parity checks (None while not locked) — within a factor of ~3 of the true rate (tests/test_nicam.py) |
| `nicamActive` | the sound being played is NICAM's: locked, carrying sound, and its error rate under 5e-4 (back to the analogue sound above 1e-3). Say which sound is heard: STEREO / MONO from NICAM, FM when the analogue sound has taken over |
| `identRect` | where the ident is in the transmitted picture, `[x0, y0, x1, y1]` as fractions of the 4:3 picture (the card's ident box, the bars' callsign, or the box laid over a webcam or file in its `identPosition` corner); empty until a source has started. **Keep labels drawn over the TX preview off it** |

## Shared radios

OpenHamShack DATV's `docs/radio-lock.md` (version 1), adopted: one program on a
radio at a time, across computers.

- **The lock.** Every real radio is opened holding it (`ohsatv/radiolock.py`,
  copied from OpenHamShack DATV; `ohsatv/devices/shared.py`): taken after the
  radio has said who it is and before the first write, given back after
  `stop()` has closed it and put back what it changed (the Pluto's FIR). A
  Pluto or LibreSDR is `ad936x-<hw_serial>`, else `net-<address>`; a
  HackRF `hackrf-<serial>`. The marker is on this computer
  (`/run/lock/openhamshack-radio.<id>.lock`) and, for a Pluto reached by
  address (or over its own USB network), on the radio too
  (`/tmp/openhamshack-radio.lock` over ssh: settings `radioLockSsh`,
  `radioSshUser`, `radioSshPassword`, default on / `root` / `analog`). Its
  `use` is `rx` while receiving and `tx` while keyed (a HackRF: `tx`
  throughout, its note saying whether it is keyed); its note is frequency,
  mode and picture format, kept up to date once a second.
- **Busy**: `start()` raises `RadioBusy` with the spec's sentence ("libre-2
  … is in use by OpenHamShack DATV on shack (transmitting, since 12:36 UTC, last
  heard 5 s ago: "…"). Close it there first; OpenHamShack ATV has not touched the
  radio") and writes nothing to the radio; `radioLock.refused` and
  `.holder` say so. There is no waiting and no retry: the operator closes
  the other program and starts again. A busy HackRF refuses transmit only.
- **Lost** (the marker gone or another program's at a heartbeat, every
  15 s): the engine unkeys first, stops, puts nothing back on the radio,
  and says so in `error`, `radioLock.lost`, a toast and the transmit log's
  unkey.
- **Changed**: before every key-up the transmitting Pluto's whole state
  (both LOs, rate, both RF bandwidths, TX attenuation and LO power, RX gain
  mode and manual gain, FIR enable) is read and compared with what this
  program last set or saw; anything different, and anything the guard
  found moved (`radioMoved`) even if it was set back, refuses the key-up
  with `TxRefused` and `radioChanged` until `accept_radio()`. A HackRF
  cannot be read back: the lock is all it has.

## Picture formats

Two keys, besides the plan fields above (DESIGN.md "Narrow formats", "405 lines"):

- `videoFormat` (control): `pal` (default, the standard), `lowpal` (narrow
  colour PAL: 1.6 MHz colour, 2.2 MHz FM sound) or `mac` (Mini-MAC: colour
  and digital sound) on FM-ATV; `pal` (625 lines) or `405` (405-line
  System A: mono, positive modulation, AM sound 3.5 MHz below vision) on
  AM-VSB. A format of the other system means `pal`. It is what we
  transmit and what the receiver expects. 405 lines are a standard: no
  NOT_STANDARD with them. The narrow ones fit colour and
  sound into the ~5 MS/s a USB Pluto can transmit but are **OpenHamShack-to-
  OpenHamShack only**: whenever one is chosen the window must say
  `ohsatv.narrow.NOT_STANDARD` where the choice is made. Changing it
  rebuilds the receiver at once (no restart); a transmission already keyed
  keeps the format it started with.
- `formatAuto` (setting, default on): when nothing has been decoded for a
  second the engine looks at what is on the air every half second and, if
  it is another of the formats, switches the receiver (`rxFormat`) and
  posts a toast. On AM that is 625 or 405 lines (by line rate and
  polarity; the receiver's LO moves with it, since the two put the vision
  carrier in different places), and it does not look again once locked. While a picture is decoded without colour it keeps looking,
  once a second and with wider margins, and switches when three looks in a
  row agree (PAL's decoder locks to narrow colour PAL's syncs and shows it
  in mono). It never changes what is sent.

## Teletext

DESIGN.md "Teletext". Transmit keys (all persisted):

- `ttxOn` (control, default off): send teletext with the picture.
- `ttxDir` (control): the service, a folder of `.tti` pages (or one with a
  `.t42` stream). Read when chosen and again at every key-up.
- `ttxInitial` (control, `"100"`): the page receivers open on (packet 8/30).
  `set()` refuses anything but a page number 100-8FF.
- `ttxLines` (setting, `"7-22,320-335"`): which blanked lines carry it.
  `set()` refuses lines the standard does not allow (6-22, 319-335).

Status: `ttxStatus` - what is sent ("Sending 4 pages on 32 lines (800
packets/s), opening on 100", plus the plan's caveat if any); `ttxRefused` -
when `ttxOn` is set but nothing will be sent, the sentence why (no folder,
no pages, or the mode: `plan["teletextWhy"]`). **Show `ttxRefused` under
the switch, in amber**: the engine inserts nothing then. `plan` carries
`teletext` (bool: this mode can carry it) and `teletextWhy`.

Receive needs no setting: every frame's packets are kept, per station
(retuning clears them).

| call | returns |
| --- | --- |
| `ttx_page(label, sub=None)` | `{"label", "sub", "subs", "rows", "links", "ctl", "age"}` or None. `rows`: 25 x 40 bytes of 7-bit codes, row 0 being the header as a set shows it (8 columns `P100    `, then the 32 sent); draw with `ohsatv.ttxrender.render(rows, reveal, flash_on)`. `links`: six `(label, sub)` or None (red, green, yellow, cyan, next, index). `sub` None = the newest subpage |
| `ttx_header()` | `{"label", "text"}`: the newest header heard from any page (the rolling header and clock) |
| `ttx_pages()` | the page labels held |

Pages edited, added or removed in `ttxDir` while keyed go out within a
cycle (no restart); `ttxStatus` then says "(updated on air)", and a page
whose new file does not parse stays as it was, with a toast.

Subtitles (page 888): `ttxSrt` (control) an SRT file, timed to the video
file source's position (else to time since key-up); `caption(text)` sends a
typed caption now ('|' breaks a line; empty clears; raises `ValueError`
with `ttxRefused`'s sentence when teletext is not being sent); status
`ttxSub` is the subtitle on air as text. With `ttxDir` empty, teletext is
subtitles alone. The video file source: `file_pause(on)`,
`file_seek(seconds)` (`ValueError` for other sources); status `filePos`,
`fileLen`, `filePaused`.

Saving: `ttx_save_page(label, path) -> path` (all subpages, .tti in
vbit2's dialect), `ttx_save_all(folder) -> (pages, t42_path)` (P<mpp>.tti
each, and the newest ~50 s of packets as `capture-<time>.t42`). Both raise
`ValueError(sentence)`.

`ttxRx` status (once a second): `{packets, pages, rate (packets/s), initial
(the sender's 8/30 page), status (its status line)}`. Feed `teletext`: one
`{"t", "page", "sub"}` each time a page finishes arriving.

Mini-MAC's sound is carried in the picture: the engine plays it from the
decoded frames, so `audioDb` and the speaker behave as for any format.

## NICAM-728

- `nicam` (control, default off): send NICAM-728 stereo beside the
  analogue sound where the plan has room - AM-VSB from 10 MS/s (the
  vision carrier moves 0.43 MHz further below the LO to make room: `plan`
  says where), FM-ATV from 16 MS/s. Its `plan` fields: `nicam` (Hz from the
  LO for AM, the subcarrier for FM, or None), `nicamWhy` (why not, when
  asked for: also one of `why`), `nicamRx` (whether a receiver at this rate
  can hear NICAM at all - it listens wherever this is true, whatever we
  send). With the tone as the sound source the stereo test goes out: the
  tone on the left, twice its frequency on the right. Changing it rebuilds
  the receiver and moves the AM LO, without a restart. Standard PAL only:
  never with 405 lines or the narrow formats.
- `simNicam` (setting, default on): the simulator's PAL stations send
  NICAM where their mode has room.

Receive sound may be stereo: the engine calls the audio backend's
`play_stereo((n, 2))` for NICAM (a mono backend plays the mix).
## The cable feed

DESIGN.md "Cable feed". Two `auth` settings, **never persisted** (off at
every `start()` of a new `Radio`; a saved Band I or UHF comes back as 23cm):

- `cableFeedAck` — the operator's confirmation: the output goes by cable,
  through attenuation, into a receiver's aerial socket, never to an
  antenna. Show its help as the checkbox's text. Setting it false turns
  `cableFeed` off.
- `cableFeed` — needs `cableFeedAck` (else `ValueError`, with the
  sentence). Turning it on moves to `band` "Band I", `system` "am",
  `videoFormat` "405", B1 (45.00 MHz) unless the same call says otherwise;
  turning it off leaves Band I or UHF for the default band. `band` "Band I"
  and `band` "UHF" are refused without it. Switching it either way while
  keyed unkeys.
- `band` "UHF" (with the feed on) moves to `videoFormat` "pal", `system`
  "am", E36 (591.25 MHz) unless the same call says otherwise; `band` "Band
  I" back to "405".

While it is on: `txRefused` comes from `bands.cable_check` instead of the
amateur guard (on AM only: 405 lines on B1-B5, 625-line PAL on UHF
E21-E48 except E38, inside its 8 MHz channel; the whole signal inside the
transmitting radio's range - its sentences name the channels a radio can
send); teletext and subtitles are refused on Band I (`ttxRefused` says to
choose UHF) and decided by the plan on UHF; every transmission uses the radio's least output, whatever
`txGain` says, and a HackRF's amplifier stays off. The UI must show a
prominent **CABLE FEED — NOT FOR ANTENNAS** banner for as long as
`cableFeed` is true, offer Band I and UHF in the band list only then, and replace
the power control with a sentence (there is none).

## The station profile and a controller

`Radio(..., link_station=True)` (the window's engine; `ohsatv/ui/__init__.py`)
keeps the station profile settings - `callsign`, `locator`, `stationCountry`,
`licence`, `licenceClass`, `stationConditions` - equal to the shared
OpenHamShack station profile (ohs-libs `shack_client`, SERVICES.md 2.1):
one store per user per machine, shared with QO-100 NB and Radio, or
`<data_dir>/station.db` for an engine given its own data folder, or
`SHACK_STATION_DB`. The profile wins at start where it says something;
afterwards each side's changes reach the other, and a value ATV cannot hold
(an operator's own licence) is left out. ATV keeps no logbook.
`radio.station_link` (`ohsatv.station.StationLink`) pairs with a controller
(`discover()`, `pair(url, user, password, totp)` -> `Pairing` or
`NeedsDecision`, `resolve()`, `unpair()`, `status()`, `pairing()`):
Settings › Controller drives it on a worker thread. Unpairing keeps the
profile. `link_profile()` returns False and logs when the store cannot be
opened; nothing else changes. `close_station()` at exit.

## The pad (AKP03E, USB keypads)

`ohsatv/pad-actions.json` and `pad-layout.json` declare what a pad does
here (CONTROLLER-PAD.md 3-4: PTT on K1 and KPENTER); `ohsatv.padactions.
PadActions(radio).perform(action, kind, step)` does it through `set()`, so
every change is validated as a click is (no Qt: a store form's server can
use it too). The window's `run()` starts `ohsatv/ui/padlink.py`: shack_pad's
DesktopPad (the window in front claims the pad through a lock file beside
the station store; the others let go), remaps kept in the station profile's
`controllerMaps`. **PTT only through shack_pad's LocalLease, whose key-up
is `ptt(True)`** - the button's own call, refused with the same sentence;
a pad unplugged or silent while keyed is unkeyed. Nothing on the pad sets
power.

## Feeds

`Feed` is a bounded drop-oldest queue: the engine `put()`s and never blocks;
the UI calls `drain()` (list, oldest first) or `get()`. A slow UI loses
frames; it can never stall receive.

| kind | rate | item |
| --- | --- | --- |
| `video` | each decoded frame (25/s when locked; none while searching) | `(t, rgb uint8 576×W×3, stats)`; `stats` = decoder stats: `hLock, vLock, colour, snr, jitterNs, state, level` |
| `txvideo` | 2/s, 10/s while keyed or when `view == "tx"` | `(t, rgb float32 576×720×3)` — the picture being (or that would be) sent, ident included |
| `spectrum` | every second frame (12.5/s) | `(t, start_hz, bin_hz, db float32[1024])` — power across the whole sample band in dBFS, true RF Hz |
| `txspectrum` | 12.5/s, **only while keyed on a half-duplex radio** (receive paused, `spectrum` silent) | the same tuple, of the samples being sent, around the transmit LO (`txFreq`). dBFS of what is handed to the radio, not a measurement off the air. Computed on the idle receive thread from the transmitter's own buffer: 0.17 ms a line at 5–20 MS/s, 0.2 % of one core. **Label it as transmit wherever it is shown** — it is on its own feed so that it can never be taken for a received spectrum |
| `scope` | each decoded frame | `{"t", "line": n (1..625; 1..405 for 405 lines, `scopeLine` clamped), "wave": float32[N] volts of that line (0H at index 0, N = fs/15625, or fs/10125 rounded up for 405 lines), "fs" (the decoder's rate: for 405 lines below 7.5 MS/s twice the radio's), "uv": (u, v) float32 arrays in volts or None}` — waveform monitor and vectorscope. Waveform: −0.3 V sync tip, 0 V blanking, 0.7 V peak white. Vectorscope: plot V against U; 75 % bars land on the standard targets (e.g. 75 % red at U −0.077, V +0.323 V) |
| `meter` | 1/s | `{t, snr, audioDb, hLock, keyed}` |
| `state` | on change (batched, ≤10/s) | `{"t", "changes": {key: value}}` |
| `toast` | as needed | `{"t", "level": "info"/"warn"/"error", "text"}` |
| `teletext` | each time a received page finishes arriving (often: a small service comes round many times a second) | `{"t", "page": "100", "sub": 0}` - fetch it with `ttx_page()` if it is the one shown |

## Safety the UI adds

The engine refuses transmit without `licenceAck` and a callsign, on real
hardware without a licence class the dataset knows for the station's country
(`stationCountry`, `licence`, `licenceClass`, `stationConditions`: `auth`
keys, Settings › Station), on real
hardware (a Pluto, or a HackRF transmitter beside any receiver) without
`allowHardwareTx`, when the HackRF is missing ("No HackRF found…" in
`txRefused`), and per the bandplan. With a HackRF, `duplex` is true:
receive carries on while keyed. The UI must not
add ways around this; it should make the reasons visible (`txRefused`),
make `allowHardwareTx` a deliberate Settings change with its help text, and
never key on start-up, on a settings save or from a stray shortcut (PTT is
a held or toggled button / space bar only).
