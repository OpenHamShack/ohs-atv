# The store form's API (ATV)

`python3 -m ohsatv --server` runs the engine headless in a container on an
OpenHamShack controller, as the store app `shack-atv` (kind `atv`). This is
its wire contract, for OpenHamShack Remote and anyone else who builds a
client (BETA-PLAN D8: "API first for Qt apps, the remote app as the UI").
It is the engine's own API (docs/ENGINE-API.md) over HTTP and WebSocket;
where this file is silent, ENGINE-API.md says what a call or a key does.

The code is `ohsatv/server/`, tested by `tests/test_server.py`. The desktop
window never imports any of it, and never opens a listener (also tested):
no servers inside the desktop apps. The shape is QO-100 NB's store form
(`~/openhamshack-nb` docs/SERVER-API.md), so one client can learn both.

## Reaching it

- Through the controller only: `<controller>/shack-atv/…`. Paths below are
  relative to that base; build no absolute URLs (`X-Forwarded-Prefix` is
  the controller's; the status page uses it only when it is a plain path).
- The controller's proxy signs the person in and sends the identity
  headers (CONTROLLER-CONTRACT 5.3). The app believes them only when
  `X-OpenHamShack-Proxy-Secret` equals its own `DOCK360_SHACK_PROXY_SECRET`
  (constant-time, before any other header is read). Anything else is `401
  {"code": "proxy_required"}` - also when no secret is configured at all,
  so the manifest's health check accepts 401 as healthy.
- Levels (CONTROLLER-CONTRACT 5.1): `view` reads and watches; `control`
  makes receive-side changes; `transmit` keys and changes what goes on the
  air. The proxy enforces the manifest's minimums; the app checks again,
  including what a path glob cannot say (below). A refusal is `403
  {"code": "level_too_low", "level", "needs"}`.
- Errors are `{"error": "<one sentence>", "code": "<snake_case>"}`.

## Endpoints

| Method, path | Level | Does |
|---|---|---|
| `GET /` | view | a status page: the received picture and a few facts, no script, nothing fetched from elsewhere |
| `GET api/status` | view | the app and radio at a glance: `app`, `kind`, `name`, `version`, `commit`, `running`, `onAir`, `txError`, `txRefused` (the guard's sentences now; empty = PTT allowed), `txWarnings` (one starting `OUTSIDE THE BANDPLAN` or `OUTSIDE THE ALLOCATION` must be shown prominently), `tx.remote` (the lease; its presence says this server has it), `radioError`, `reconnecting` (true while claims the broker lost are being taken back; `radioError` then says "Reconnecting to the station…"), `device`, `txDevice`, `band`, `system`, `videoFormat`, `rxFreq`, `txFreq`, `rxLock`, `snr`, `cableFeed`, `simulator`, `broker`, `audio`, `pictures`, `station` |
| `GET api/state` | view | `Radio.state()`: every key ENGINE-API lists, except the Pluto's shell password |
| `GET api/spec` | view | the settings registry (type, kind, default, limits, choices, unit, help, auth, restart) |
| `GET api/bands` | view | the bandplan the guard uses (`ohsatv/bands.py`): each band's `lo`, `hi`, `blocks`, `segments`, `channels`, `default`, `modes`, `analogue_tx`, `status`, `power`, `rf_note`, `notes`, `warn`; `cable` names the cable feed's bands (Band I, UHF) |
| `POST api/set` | control or transmit (below) | `{key: value, …}` or `{"changes": {…}}` through `Radio.set()`, all or nothing; answers `{"changed": {…}}`. Refused (400): the station profile's keys (`callsign`, `locator`, `licenceAck`, `stationCountry`, `licence`, `licenceClass`, `stationConditions`: set on the controller) and the platform's (`plutoUri`, `hackrfSerial`, the radio lock's ssh keys, audio and camera devices, `recDir`) |
| `POST api/start` | control | start the radio (claims it from the broker first) |
| `POST api/stop` | control (transmit while keyed) | stop the radio and give the broker its claims back |
| `POST api/ptt` | control to unkey, transmit to key | `{"on": true, "remote": "<client id>"}` keys under that client's lease; `{"on": false}` unkeys. Always `200` with the status: a refused key-up has `onAir` false and `txError` saying why (the guard's first sentence, or the broker's). A second key-up from the lease holder renews the lease |
| `POST api/ptt/heartbeat` | transmit | `{"remote": …}` every 1 s once keyed; `409 not_lease_holder` when that client no longer holds it |
| `POST api/halt` | control | stop transmitting now, whoever keyed |
| `POST api/accept-radio` | transmit | accept the transmitting radio as it is after `radioChanged` refused a key-up (ENGINE-API `accept_radio`) |
| `GET api/txlog` | view | `{"entries": [...]}`, the transmit log (ENGINE-API `tx_log`); a server-ended over's `why` names the reason (a lost remote, a halt by whom, the controller) |
| `POST api/record` | control | `{"on": true, "seconds": 30}` / `{"on": false}`: record the receiver's IQ as SigMF on the data volume; answers the file name |
| `POST api/caption` | transmit | `{"text": "…"}`: a typed subtitle on page 888 now (`|` breaks a line; empty clears) |
| `GET api/teletext/pages` | view | `{"pages": ["100", …], "header": {"label", "text"}}`: received pages and the rolling header (`text` as 7-bit codes) |
| `GET api/teletext/page?label=100&sub=` | view | one received page (ENGINE-API `ttx_page`): `rows` 25 lists of 40 seven-bit codes, `links`, `subs`; `sub` in hex, empty for the newest; 404 if not received |
| `GET api/events?kinds=state,toast,meter,teletext` | view | WebSocket: one JSON message per feed item, `{"kind": …, …item}`; first `{"kind": "hello", "status": …}`. Also offered, only when asked: `spectrum` and `txspectrum` (`{"kind", "t", "startHz", "binHz", "n", "cdb"}`, `cdb` the bins as little-endian int16 centi-dB in base64), `scope` (`{"kind", "t", "line", "fs", "mv", "uMv", "vMv"}`, the line and the vectorscope as int16 millivolts in base64). Never `video` or `txvideo`: pictures have their own routes |
| `GET api/video/rx.mjpeg` | view | the received picture: `multipart/x-mixed-replace; boundary=frame`, JPEG frames at 768 × 576 (4:3, as the engine says to show it), up to 12.5 a second; nothing while the receiver searches (`rxLock`) |
| `GET api/video/rx.jpg` | view | one received picture, the newest (waits up to 2 s); 404 while none is decoded |
| `GET api/video/tx.mjpeg` | view | the transmit preview, the same way: what goes (or would go) on air, ident included; 2 a second, 10 while keyed |
| `GET api/audio/rx` | view | receive sound, raw PCM over a long-lived response: `Content-Type: audio/L16; rate=48000; channels=1` and `X-Audio-Format: {"encoding": "s16le", "rate": 48000, "channels": 1}`; NICAM stereo arrives as its mono mix; silence (zeros) while the radio is stopped or nothing is received |
| `GET api/audio/tx` | transmit | WebSocket: binary frames of PCM in the same format are the microphone while it is open - the sound of a transmission whose `audioSource` is `mic` (keying is still `api/ptt`) |
| `GET api/shack/busy` | the platform | `{"busy": <on air>, "why"}` for the update gate |
| `POST api/shack/release` | the platform | the broker asks for a radio back: `409 {"onAir": true}` while keyed (unless `"stop": true`), else `202` and the radio stops |

### Which settings need Transmit

`api/set` needs **Control** only when every key is receive-side: `volume`,
`mute`, `view`, `scopeLine`, `colour`, `formatAuto`, `rxGainMode`,
`rxGain`, `recSeconds`, `recMaxMB`, `playLoop` and the simulator's keys;
and, while nothing is keyed, tuning: `band`, `system`, `rxFreq`,
`videoFormat`, the sample rates, the Pluto's 8-bit modes, the radio
profile and `playFile` (the key-up checks wherever they land). On the air
those would move or restart the transmitter (without `split`, `txFreq`
follows `rxFreq`), so then they need **Transmit**. Everything else - the
transmit frequency and `split`, `txGain`, the picture source, the ident,
teletext and subtitles, NICAM, the modulation, and every `auth` key
(`device`, `txDevice`, `allowHardwareTx`, `txGainCeiling`, `txTimeoutMin`,
`watchdogSec`, `cableFeedAck`, `cableFeed`, `hackrfAmp`) - needs
**Transmit**.

## Transmitting

In this order, and nothing keys otherwise (CONTROLLER-CONTRACT 6.2):

1. the Transmit level;
2. the engine's key-up check, the very one the window's PTT meets
   (`Radio.ptt`): the licence answer and a callsign, the real-hardware
   switch, the band plan - **70 cm outside 430-440 MHz is a warning, not a
   refusal** (most 70 cm ATV goes down a cable through an attenuator:
   Brian, 8 Oct 2026), **23 cm and 6 cm outside their allocation or ATV
   segments are refused** - with the cable feed on, its own check
   (`bands.cable_check`: 405 lines on B1-B5, PAL on UHF E21-E48 except E38,
   inside the radio's range) **instead of** the amateur one; the rate the
   link can carry, the gain ceiling, and a radio changed behind the
   engine's back. On the simulator a hardware refusal is a warning saying
   "on a real radio this would be refused";
3. a broker claim in `tx` mode on the transmitting radio and the broker's
   `onair` yes, told the whole occupied signal (sound carrier to vestige,
   from the plan) as `freqHz` and `bandwidthHz`, `mode` `pal-am` or
   `pal-fm` (the dataset's ATV class), and `chain` that radio alone - the
   Pluto, or the HackRF when `txDevice` is `hackrf`;
4. the key-up, held under the client's lease: unkeyed 5 s after its last
   heartbeat (`txError` "Transmit stopped: remote controller lost …"). The
   heartbeats also feed the engine's own watchdog (`watchdogSec`).

Unkeying runs the other way: RF off (the engine's unkey returns with the
transmitter stopped), then the broker told and the claim back to `rx`. A
claim the broker takes back unkeys at once and stops the radio (both
radios, and the other one's claim goes back). While the radio is still
wanted the server then re-claims them for receive with `{"reclaim": true}`
(CONTROLLER-CONTRACT 7.2: a broker restart loses every claim), backing off
from 1 s to 30 s or waiting the broker's `retryAfter`; transmitting never
resumes by itself. Only `api/stop` or a hand-over (`api/shack/release`, or
`releaseRequested` on a heartbeat) ends wanting the radio. The
engine's own unkeys (time-out, watchdog, a radio that moved, a lost radio
lock) end the lease and put the engine's reason in `txError`.

On a controller, the station profile supplies `callsign`, `locator`,
`licenceAck` (from `derived.canTransmit`) and the country, licence regime,
class and station conditions the band check reads (`dxccPrefix`,
`licence`, `conditions`), read every 30 s.

Tested only into the simulator (dressed as real hardware for the guard):
nothing in this server has keyed a radio.

## Radios

The broker lends `pluto-1` (receiving, and transmitting unless a HackRF
does) and `hackrf-1` (transmitting beside it). Both are claimed for
receive when the radio starts, since the engine opens both then. The
claim's `access` sets `plutoUri` (`ip:<address>`) and `hackrfSerial`. The
simulator, the default in a fresh container, and a recording (`device`
`file`) claim nothing.

## What it is not (yet)

- **No web console**: Remote is the UI. The status page only says how it is.
- **The cable feed cannot go on air through a controller yet.** The
  broker's licence check knows the amateur allocations only and refuses
  Band I and UHF; the on-air body carries `"cableFeed": true` for the day
  the broker learns it (a platform question, not answered here).
- **70 cm beyond 430-440 MHz is refused by the broker today**, though the
  engine only warns: shack-bandplan 0.1.0 (dataset 2026.10.3) answers
  `edge_above` "the signal would leave the allocation" for an AM-VSB signal
  at 437 MHz and up, and `plan_reserved` (the beacons at 432.4 MHz) lower
  down. Brian's warning-not-refusal rule (most 70 cm ATV is cabled) has
  not reached the shared dataset; until it does, the broker's refusal is
  what a client sees, as `txError`.
- **No webcam or video-file source in the store image**: those need
  ffmpeg and a camera, which the image leaves out; the test card, bars and
  tone work. No pad actions yet (CONTROLLER-PAD.md): ATV declares none.
- **Logbook**: ATV keeps none today, so the store form has no `api/log`.
