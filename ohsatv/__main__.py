"""python3 -m ohsatv [--headless | --server [--port N]] [--device sim|pluto|file] [--play FILE.sigmf-meta] [--seconds N]
python3 -m ohsatv --teletext [FOLDER | FILE.tti] [ohstxt's options]

Without --headless this opens the window (ohsatv/ui). --headless runs the
engine alone and prints one status line a second; it heartbeats the
watchdog itself and never transmits: nothing here calls ptt(). --server is
the store form: the engine headless behind its HTTP/WebSocket API
(ohsatv/server, docs/SERVER-API.md), for a container on an OpenHamShack
controller. It is a process mode of its own and the window never opens a
listener (no servers inside the desktop apps).
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import time

from . import config
from .radio import Radio


def status_line(s: dict, frames: int) -> str:
    snr = "  -  " if s["snr"] is None else f"{s['snr']:5.1f}"
    jit = "  - " if s["jitterNs"] is None else f"{s['jitterNs']:4.0f}"
    return (f"{time.strftime('%H:%M:%S')} {s['band']:4s} {s['system'].upper()} {s['rxFreq'] / 1e6:9.3f} MHz "
            f"@{s['fs'] / 1e6:g} MS/s  {s['rxLock']:9s} H {100 * s['hLock']:5.1f}% V {'Y' if s['vLock'] else '-'} "
            f"col {'Y' if s['colourLock'] else '-'}  SNR {snr} dB  jitter {jit} ns  "
            f"frames {frames:2d}  cpu {100 * s['cpu']:5.1f}%"
            + (f"  skipped {s['decodeSkipped']}" if s["decodeSkipped"] else "")
            + ("  ON AIR" if s["keyed"] else "")
            + (f"  ! {s['error']}" if s.get("error") else ""))


def _printable_console() -> None:
    """Never fail a print for want of a character. The Windows console
    build's stdout is the console's code page (437 or 1252), which has no
    "›" or "−" - characters the engine's sentences use - and a print that
    raised UnicodeEncodeError would stop --headless in the middle of a run.
    Unencodable characters become "?" instead. Linux's UTF-8 stdout never
    meets one, so nothing changes there."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    if args[:1] == ["--teletext"]:
        # The Teletext editor alone, as `python3 -m ohstxt` runs it: the
        # .deb's launcher and the Windows .exe only know this package, and
        # this is how their menu entries open the editor without the
        # transceiver (and without a radio).
        from ohstxt.__main__ import main as teletext_main
        return teletext_main(args[1:])
    ap = argparse.ArgumentParser(prog="python3 -m ohsatv", description="OpenHamShack ATV, an analogue amateur-TV transceiver")
    ap.add_argument("--headless", action="store_true", help="run the engine without a window")
    ap.add_argument("--server", action="store_true",
                    help="the store form: run the engine headless behind its HTTP/WebSocket API "
                         "(docs/SERVER-API.md); for a container on an OpenHamShack controller")
    ap.add_argument("--port", type=int, default=None, help="--server's port (default $SHACK_PORT or 8080)")
    ap.add_argument("--device", choices=["sim", "pluto", "file"], help="override the configured device for this run")
    ap.add_argument("--play", metavar="FILE", help="decode an IQ recording (its .sigmf-meta) instead of a radio")
    ap.add_argument("--band", choices=["70cm", "23cm", "6cm"], help="override the band for this run")
    ap.add_argument("--system", choices=["am", "fm"], help="override the system for this run")
    ap.add_argument("--seconds", type=float, default=0.0, help="stop after this long (0 = until Ctrl-C)")
    ap.add_argument("--no-audio", action="store_true", help="run with the null audio backend")
    ap.add_argument("--radio-lock-folder", action="store_true",
                    help="check this computer's radio-lock folder can be trusted (exit 0) or say why not (exit 1)")
    ap.add_argument("--clear-stale-lock", metavar="RADIO_ID",
                    help="as an administrator: remove another account's radio lock on this computer, only if stale")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--teletext", action="store_true",
                    help="open the Teletext editor alone (must come first; the rest goes to the editor)")
    a = ap.parse_args(args)
    if a.teletext:          # listed only for --help; anywhere but first it would be silently ignored
        ap.error("--teletext must come first")
    _printable_console()
    logging.basicConfig(level=logging.INFO if a.verbose else logging.WARNING,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    # Both before the config: the Windows installer runs the first (hidden,
    # unelevated) to decide whether to ask for administrator rights to set
    # the folder's ACL (packaging/windows/radio-lock-folder.iss, shared by
    # every OpenHamShack installer), and an administrator runs the second when a
    # refusal names it. The same two flags, worded the same, as OpenHamShack
    # DATV's: the installers and the refusals rely on them.
    if a.radio_lock_folder:
        from . import radiolock
        why = radiolock.folder_problem()
        print(why or f"radio-lock folder: {radiolock.HOST_DIR} can be trusted")
        return 1 if why else 0
    if a.clear_stale_lock:
        from . import radiolock
        cleared, said = radiolock.clear_stale(a.clear_stale_lock)
        print(("" if cleared else "clear-stale-lock: ") + said)
        return 0 if cleared else 1
    notes: list[str] = []
    cfg = config.load(notes=notes)
    for n in notes:
        print("config:", n)
    override = bool(a.device or a.band or a.system or a.no_audio or a.play)
    if a.device:
        cfg["device"] = a.device
    if a.play:
        # A recording carries its own rate, system and tuning; the radio the
        # operator configured is left as it was (nothing is saved: override).
        cfg["device"], cfg["playFile"] = "file", a.play
    if a.no_audio:
        cfg["audioBackend"] = "null"
    if a.server:
        return _serve(a, cfg, persist=not override)
    if not a.headless:
        try:
            from .ui import run
        except ImportError as e:
            print(f"The window needs PyQt6 (Debian: python3-pyqt6): {e}\n"
                  f"Run with --headless to start the engine on its own.", file=sys.stderr)
            return 2
        return run(cfg, persist=not override, band=a.band, system=a.system)
    radio = Radio(cfg, persist=not override)
    if a.band:
        radio.set(band=a.band)
    if a.system:
        radio.set(system=a.system)
    feeds = {k: radio.subscribe(k, 64) for k in ("video", "toast", "state")}
    stop = {"now": False}
    signal.signal(signal.SIGINT, lambda *_: stop.__setitem__("now", True))
    signal.signal(signal.SIGTERM, lambda *_: stop.__setitem__("now", True))
    try:
        radio.start()
    except Exception as e:  # noqa: BLE001
        print(f"Could not start: {e}", file=sys.stderr)
        return 1
    t0 = time.monotonic()
    next_line = t0 + 1.0
    frames = 0
    try:
        while not stop["now"] and (a.seconds <= 0 or time.monotonic() - t0 < a.seconds):
            time.sleep(0.05)
            radio.heartbeat()
            frames += len(feeds["video"].drain())
            feeds["state"].drain()
            for it in feeds["toast"].drain():
                print(f"[{it['level']}] {it['text']}")
            if time.monotonic() >= next_line:
                next_line += 1.0
                print(status_line(radio.state(), frames), flush=True)
                frames = 0
    finally:
        radio.stop()
    print("stopped")
    return 0


def _serve(a, cfg: dict, persist: bool) -> int:
    """The store form (ohsatv/server). Imported only here: the window never
    loads the server (no servers inside the desktop apps)."""
    from pathlib import Path

    from .server import run as server_run
    here = Path(__file__).resolve().parent
    version = (here / "VERSION").read_text().strip()
    commit = (here / "COMMIT").read_text().strip() if (here / "COMMIT").is_file() else ""

    def make(audio):
        radio = Radio(cfg, audio=audio, persist=persist)
        if a.band:
            radio.set(band=a.band)
        if a.system:
            radio.set(system=a.system)
        return radio
    return server_run.main(make, version, commit, port=a.port)


if __name__ == "__main__":
    sys.exit(main())
